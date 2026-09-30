"""Launches qa/feed_verify.py as a subprocess - the same command line you'd type by hand - and
watches it. qa/ is never imported for anything that has a side effect; the only qa import here
(at the bottom, for run_view) is a pure read of files a run already wrote.

Why a subprocess and not an in-process call: qa/feed_client.py reads AJIO_USER_GROUPS from the
environment once, at import time (there's no function argument to override it - see
analysis/FINDINGS.md 6.4.1/6.8 and PROGRESS.md's Phase 5 addendum 4). A fresh process per run,
given a custom `env`, is the only way to vary l1/l2 per request without editing that module. It
also means a run can be cancelled by killing its process, and a stuck run can't wedge the API.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import psutil

from qa import pages as qa_pages
from web import db

log = logging.getLogger("web.runner")

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "runs"

L1_OPTIONS = ["nontransacted", "premium", "nonpremium"]
L2_OPTIONS = ["men", "women", "unisex", "nogender"]
SCOPES = ["hero", "all"]
MAX_WORKERS = 10   # Gemma requests are paced separately (qa/spotcheck/vision.py CALLS_PER_MINUTE), so more workers can't overrun the API limit

# Every Indian state, for the "State" field's autocomplete list and for validating AJIO_LOCATION_DETAIL's
# "state" value (see build_location_detail below) - all caps, matching the default LOCATION constant's
# own "KARNATAKA" in qa/feed_client.py. A multi-word state gets two entries, one space-separated and one
# underscore-separated, since it's not confirmed which AJIO's edge actually expects (user request:
# neither has been seen in a live capture the way "KARNATAKA" has, so both are offered rather than guessed).
_SINGLE_WORD_STATES = ["ASSAM", "BIHAR", "CHHATTISGARH", "GOA", "GUJARAT", "HARYANA", "JHARKHAND",
                       "KARNATAKA", "KERALA", "MAHARASHTRA", "MANIPUR", "MEGHALAYA", "MIZORAM", "NAGALAND",
                       "ODISHA", "PUNJAB", "RAJASTHAN", "SIKKIM", "TELANGANA", "TRIPURA", "UTTARAKHAND"]
_MULTI_WORD_STATES = ["ANDHRA PRADESH", "ARUNACHAL PRADESH", "HIMACHAL PRADESH", "MADHYA PRADESH",
                      "TAMIL NADU", "UTTAR PRADESH", "WEST BENGAL"]
STATE_OPTIONS = sorted(_SINGLE_WORD_STATES +
                       [v for s in _MULTI_WORD_STATES for v in (s, s.replace(" ", "_"))])


def build_location_detail(state: str, pincode: str = "560029") -> str:
    """The AJIO_LOCATION_DETAIL env var's value - the JSON blob sent as the home-feed request's
    x-location-detail header (qa/feed_client.py). Only state and pincode are ever overridden here;
    country/city stay fixed at the tool's original defaults since nothing has asked to vary them yet.
    pincode is threaded through from the same value as AJIO_PINCODE (the listing calls' own pincode)
    rather than left at a separately-hardcoded default, so the two requests describe one consistent
    location instead of silently disagreeing with each other."""
    return json.dumps({"country": "INDIA", "city": "BENGALURU", "pincode": pincode, "state": state})

_FOLDER_WAIT_S = 30      # how long to wait for feed_verify.py's own run folder to appear
_POLL_S = 0.2

_procs: dict[str, subprocess.Popen] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()


def build_user_groups(l1: str, l2: str) -> str:
    """Same flag skeleton as the one captured value - only the gender token is known to vary.
    The other three flags (p_null, false, noasp) are preserved verbatim rather than guessed away,
    since nothing has confirmed what they mean or whether dropping them is even valid."""
    return f"l1:{l1}|l2:p_null,false,{l2},noasp"


def _existing_run_dirs() -> set[str]:
    if not RUNS_DIR.exists():
        return set()
    return {p.name for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.endswith("_feedverify")}


DEFAULT_STATE = "KARNATAKA"
DEFAULT_PINCODE = "560029"
MAX_COMBOS = 24      # [A] most runs one Run now / one scheduler fire may expand into (l1 x l2 x state x pincode)


def new_batch_id() -> str:
    return uuid.uuid4().hex[:12]


# Only the home feed is personalised by l1/l2; every other page returns the same banners for any cohort, so
# those pages run once per state/pincode with this fixed, neutral cohort (the baseline in qa/cohort_client.py).
NEUTRAL_L1, NEUTRAL_L2 = "nontransacted", "unisex"
PAGE_OPTIONS = qa_pages.PAGES
PAGE_IDS = qa_pages.PAGE_IDS


def count_combos(pages, l1s, l2s, states, pincodes) -> int:
    """How many runs expand_combos would make, without building them: home is l1 x l2, any other page 1."""
    uniq = lambda values: len(set(values))
    per_page = sum(uniq(l1s) * uniq(l2s) if p == qa_pages.DEFAULT_PAGE else 1 for p in dict.fromkeys(pages))
    return per_page * uniq(states) * uniq(pincodes)


def expand_combos(l1s, l2s, states, pincodes, pages=(qa_pages.DEFAULT_PAGE,)) -> list[dict]:
    """Every page x l1 x l2 x state x pincode combination, in that order (page, then l1, vary slowest), duplicates
    dropped. Each combination is one run: qa/feed_verify takes exactly one cohort per process. A page other than
    home ignores l1/l2 (see NEUTRAL_L1), so it contributes one combination per state x pincode, not l1 x l2."""
    def uniq(values):
        return list(dict.fromkeys(values))
    out = []
    for page in uniq(pages):
        cohorts = itertools.product(uniq(l1s), uniq(l2s)) if page == qa_pages.DEFAULT_PAGE else [(NEUTRAL_L1, NEUTRAL_L2)]
        out += [{"page": page, "l1": a, "l2": b, "state": c, "pincode": d}
                for (a, b), c, d in itertools.product(cohorts, uniq(states), uniq(pincodes))]
    return out


def _row_pages(row) -> list[str]:
    keys = row.keys() if hasattr(row, "keys") else ()
    return db.as_list(row["page"] if "page" in keys else None) or [qa_pages.DEFAULT_PAGE]


def combos_of(row) -> list[dict]:
    """The combinations a schedule row covers. Reads both storage shapes (see db.as_list); a missing
    state/pincode means the tool's defaults and a missing page means home."""
    return expand_combos(db.as_list(row["l1"]), db.as_list(row["l2"]),
                         db.as_list(row["state"]) or [DEFAULT_STATE], db.as_list(row["pincode"]) or [DEFAULT_PINCODE],
                         _row_pages(row))


def combo_label(combo: dict) -> str:
    page = combo.get("page", qa_pages.DEFAULT_PAGE)
    what = f"{combo['l1']}/{combo['l2']}" if page == qa_pages.DEFAULT_PAGE else qa_pages.label(page)
    return f"{what} · {combo['state']} · {combo['pincode']}"


def start_run(l1: str, l2: str, scope: str, banner_limit: int | None, workers: int,
              schedule_id: int | None = None, excluded_carousels: list[int] | None = None,
              pincode: str = DEFAULT_PINCODE, state: str = DEFAULT_STATE,
              excluded_sections: list[str] | None = None, batch_id: str | None = None,
              page: str = qa_pages.DEFAULT_PAGE) -> str:
    if l1 not in L1_OPTIONS or l2 not in L2_OPTIONS:
        raise ValueError(f"unknown l1/l2: {l1}/{l2}")
    if page not in PAGE_IDS:
        raise ValueError(f"unknown page: {page}")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    workers = max(1, min(MAX_WORKERS, workers))

    before = _existing_run_dirs()
    env = {**os.environ, "AJIO_USER_GROUPS": build_user_groups(l1, l2), "AJIO_PINCODE": pincode,
          "AJIO_LOCATION_DETAIL": build_location_detail(state, pincode)}
    cmd = [sys.executable, "-m", "qa.feed_verify", "--scope", scope, "--workers", str(workers)]
    if banner_limit:
        cmd += ["--limit", str(banner_limit)]
    for section_index in excluded_carousels or []:
        cmd += ["--exclude-carousel", str(section_index)]
    for section in excluded_sections or []:
        cmd += ["--exclude-section", section]
    if page != qa_pages.DEFAULT_PAGE:
        cmd += ["--page", page]
    if l1 == "premium" and page == qa_pages.DEFAULT_PAGE:
        # FINDINGS 6.10: which asset set (standard/premium) comes back for l1:premium varies
        # per-request, even with l1/l2/device-id all held identical - so a premium run keeps
        # re-fetching the feed (cheap, no vision/listing calls) until it actually sees a premium
        # banner before running the expensive per-banner checks, instead of silently checking
        # whatever the first roll happened to return.
        cmd += ["--confirm-asset-set", "PR"]
    proc = subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)

    run_id = None
    for _ in range(int(_FOLDER_WAIT_S / _POLL_S)):
        new = _existing_run_dirs() - before
        if new:
            run_id = new.pop()
            break
        if proc.poll() is not None:
            break
        time.sleep(_POLL_S)
    if run_id is None:
        # the process exited (or is still fetching the feed) before making its folder; keep a
        # unique key so the run is still trackable, findable() below will report it has no output
        run_id = f"pending-{proc.pid}-{int(time.time())}"

    with _lock:
        _procs[run_id] = proc
    db.insert_run(run_id, str(RUNS_DIR / run_id), scope, banner_limit, workers, l1, l2,
                  proc.pid, schedule_id, excluded_carousels, pincode=pincode, state=state,
                  excluded_sections=excluded_sections, batch_id=batch_id, page=page)
    if schedule_id is not None:
        db.set_schedule_status(schedule_id, "running", f"run {run_id} in progress")
    threading.Thread(target=_watch, args=(run_id, proc), daemon=True).start()
    return run_id


def start_batch(combos: list[dict], scope: str, banner_limit: int | None, workers: int,
                schedule_id: int | None = None, excluded_carousels: list[int] | None = None,
                excluded_sections: list[str] | None = None, poll_s: float = 2.0, sleep=time.sleep) -> tuple[str, str, int]:
    """Run now for one or several combinations. The first starts immediately (so the caller has a real run
    to show); the rest start one after another, each as soon as the previous one has finished - a batch of
    twelve must not become twelve simultaneous rounds of Gemini + listing calls. Cancelling a run of the
    batch stops the rest of it. Returns (first run id, batch id, how many are still queued).

    The queue lives in memory: a server restart mid-batch keeps the run in progress (see reconcile_orphans)
    but forgets the combinations that hadn't started yet."""
    batch_id = new_batch_id()

    def start_one(combo: dict) -> str:
        return start_run(combo["l1"], combo["l2"], scope, banner_limit, workers, schedule_id=schedule_id,
                        excluded_carousels=excluded_carousels, pincode=combo["pincode"], state=combo["state"],
                        excluded_sections=excluded_sections, batch_id=batch_id, page=combo.get("page", qa_pages.DEFAULT_PAGE))

    first_id = start_one(combos[0])
    rest = combos[1:]
    if rest:
        threading.Thread(target=_run_rest_of_batch, args=(first_id, rest, start_one, poll_s, sleep), daemon=True).start()
    return first_id, batch_id, len(rest)


def _run_rest_of_batch(previous_id: str, rest: list[dict], start_one, poll_s: float, sleep) -> None:
    for i, combo in enumerate(rest):
        while True:
            row = db.get_run(previous_id)
            if row is None or row["status"] != "running":
                break
            sleep(poll_s)
        if row is not None and row["status"] == "cancelled":
            log.info("batch stopped after %s was cancelled; %d combination(s) not started", previous_id, len(rest) - i)
            return
        try:
            previous_id = start_one(combo)
        except Exception:
            log.exception("batch: could not start %s; the remaining combinations are not started", combo_label(combo))
            return


# ---- scheduled runs: don't pile up ----------------------------------------------------------------

MAX_CONCURRENT_RUNS = 1        # [A] a scheduled fire waits until fewer than this many runs (of any kind) are active
SCHEDULED_MAX_WAIT_S = 3600    # [A] ...and gives up on that fire (records "skipped") after this long
_sched_gate = threading.Lock()


def start_scheduled_run(row, combo: dict | None = None, batch_id: str | None = None,
                        wait_s: float = SCHEDULED_MAX_WAIT_S, poll_s: float = 5.0,
                        sleep=time.sleep, clock=time.monotonic) -> str | None:
    """Start one combination of the run a schedule's interval just fired (the schedule's first if none is
    given), but not on top of another run: every run does up to `workers` simultaneous Gemini calls plus
    listing fetches against AJIO, so two schedules firing together (or one firing during a manual run) would
    double that load for no benefit. Waits - serialized behind other scheduled fires by _sched_gate - until
    fewer than MAX_CONCURRENT_RUNS runs are active, then starts it. Returns None if that never happened within
    wait_s (the caller records the skipped run). A manual "Run now" is never held up by this; it only counts
    toward "active"."""
    combo = combo or combos_of(row)[0]
    deadline = clock() + wait_s
    with _sched_gate:
        while db.count_running() >= MAX_CONCURRENT_RUNS:
            if clock() >= deadline:
                return None
            sleep(poll_s)
        return start_run(combo["l1"], combo["l2"], row["scope"], row["banner_limit"], row["workers"],
                         schedule_id=row["id"], pincode=combo["pincode"], state=combo["state"],
                         excluded_sections=[s["id"] for s in json.loads(row["excluded_sections"] or "[]")] or None,
                         batch_id=batch_id, page=combo.get("page", qa_pages.DEFAULT_PAGE))


def start_scheduled_batch(row, still_wanted=lambda: True, on_started=lambda run_id: None,
                          **wait_kwargs) -> tuple[list[str], int]:
    """One scheduler fire: every combination the schedule covers, each through start_scheduled_run's gate -
    which, with a cap of one active run, is what makes them go one after another. `still_wanted` is asked
    before each combination, so deleting or disabling the schedule mid-fire stops the rest; `on_started` is
    told each run's id as soon as it starts (the batch itself can take hours). Returns (ids of the runs
    started, how many combinations were skipped)."""
    batch_id = new_batch_id()
    started: list[str] = []
    skipped = 0
    for combo in combos_of(row):
        if not still_wanted():
            break
        run_id = start_scheduled_run(row, combo=combo, batch_id=batch_id, **wait_kwargs)
        if run_id is None:
            skipped += 1
        else:
            started.append(run_id)
            on_started(run_id)
    return started, skipped


_LIST_CAROUSELS_TIMEOUT_S = 60  # a plain feed fetch (no vision/listing calls) - should take a few seconds


def list_carousels(l1: str, l2: str, scope: str, page: str = qa_pages.DEFAULT_PAGE) -> list[dict]:
    """Fetches the feed fresh and returns every banner in scope (each with its section_index/label
    carousel grouping), without running any verification - for the "New run" form's carousel preview,
    shown before a real run starts so the user can see each carousel's actual banners and uncheck ones
    they don't want processed at all (see start_run's excluded_carousels and qa/feed_verify.py's
    --list-carousels/--exclude-carousel). A subprocess for the same reason as start_run: AJIO_USER_GROUPS
    is read once at import time, not passable as a plain argument."""
    if l1 not in L1_OPTIONS or l2 not in L2_OPTIONS:
        raise ValueError(f"unknown l1/l2: {l1}/{l2}")
    if page not in PAGE_IDS:
        raise ValueError(f"unknown page: {page}")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    if page != qa_pages.DEFAULT_PAGE:
        l1, l2 = NEUTRAL_L1, NEUTRAL_L2
    env = {**os.environ, "AJIO_USER_GROUPS": build_user_groups(l1, l2)}
    cmd = [sys.executable, "-m", "qa.feed_verify", "--scope", scope, "--list-carousels"]
    if page != qa_pages.DEFAULT_PAGE:
        cmd += ["--page", page]
    if l1 == "premium" and page == qa_pages.DEFAULT_PAGE:
        cmd += ["--confirm-asset-set", "PR"]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=_LIST_CAROUSELS_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"feed fetch failed: {proc.stdout[-2000:]}")
    last_line = proc.stdout.strip().splitlines()[-1]  # earlier lines can be warnings (e.g. confirm-asset-set)
    return json.loads(last_line)


def _finalize(run_id: str, status: str, error: str | None = None) -> None:
    """Record how a run ended, then run the post-run hooks (diff against the schedule's previous run,
    alerts). The hooks are best-effort: they must never stop the run being recorded as finished."""
    db.finish_run(run_id, status, error)
    try:
        from web import alerts   # imported here: alerts reads run results back through this module
        alerts.after_run(run_id)
    except Exception:
        log.exception("post-run hooks failed for %s", run_id)


def _watch(run_id: str, proc: subprocess.Popen) -> None:
    output = proc.communicate()[0] or ""
    with _lock:
        was_cancelled = run_id in _cancelled
        _cancelled.discard(run_id)
        _procs.pop(run_id, None)
    if was_cancelled:
        status = "cancelled"
    elif proc.returncode == 0:
        status = "done"
    else:
        status = "failed"
    _finalize(run_id, status, None if status != "failed" else output[-4000:])


# ---- runs that outlive the server ----------------------------------------------------------------
# _procs/_cancelled live in this process's memory, but a run's subprocess doesn't die with the server.
# Restart the server mid-run and that run keeps going and writing its results, while the API forgets it
# (can't cancel it, and its DB row stays "running" forever - _watch's thread died with the old server).
# reconcile_orphans() runs at startup to repair exactly that.

_adopted: dict[str, int] = {}   # run_id -> pid of a still-running run process this server didn't launch


def _run_process(pid: int | None, started_at: str) -> psutil.Process | None:
    """The live process behind a "running" DB row, or None if that run's process is gone. Windows reuses
    pids, so a live pid alone proves nothing: it must also be a qa.feed_verify process that was created
    when the run was (start_run launches the subprocess before it stamps started_at, never after)."""
    if not pid:
        return None
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE or "qa.feed_verify" not in " ".join(proc.cmdline()):
            return None
        started, created = datetime.fromisoformat(started_at).timestamp(), proc.create_time()
    except (psutil.Error, ValueError):
        return None
    return proc if started - (_FOLDER_WAIT_S + 15) <= created <= started + 5 else None


def _outcome_from_disk(row) -> tuple[str, str | None]:
    """How a run whose process we didn't watch actually ended. The exit code is unknown, but
    qa/feed_verify writes results.json only once the whole run finished - so its presence means done."""
    if (Path(row["out_dir"]) / "results.json").exists():
        return "done", None
    return "failed", ("The server restarted while this run was in progress and the run's process is gone. "
                     "Anything checked before that is still in the run folder.")


def _watch_adopted(run_id: str, proc: psutil.Process) -> None:
    try:
        proc.wait()
    except psutil.Error:
        pass
    with _lock:
        was_cancelled = run_id in _cancelled
        _cancelled.discard(run_id)
        _adopted.pop(run_id, None)
    row = db.get_run(run_id)
    if was_cancelled:
        _finalize(run_id, "cancelled")
    else:
        _finalize(run_id, *_outcome_from_disk(row))


def reconcile_orphans() -> dict[str, list[str]]:
    """Startup pass over every DB row still marked "running". A run whose process is still alive is
    adopted - cancellable again, and finalized when it ends. One whose process is gone is finalized now
    (done if it had finished writing results, else failed), so the UI never shows a permanently "running"
    run and a scheduled run's diff/alert still happens. Returns which runs were adopted / finalized."""
    summary: dict[str, list[str]] = {"adopted": [], "finalized": []}
    for row in db.list_runs_with_status("running"):
        run_id = row["run_id"]
        with _lock:
            if run_id in _procs or run_id in _adopted:
                continue
        proc = _run_process(row["pid"], row["started_at"])
        if proc is not None:
            with _lock:
                _adopted[run_id] = proc.pid
            threading.Thread(target=_watch_adopted, args=(run_id, proc), daemon=True).start()
            summary["adopted"].append(run_id)
        else:
            _finalize(run_id, *_outcome_from_disk(row))
            summary["finalized"].append(run_id)
    return summary


def cancel_run(run_id: str) -> bool:
    with _lock:
        proc = _procs.get(run_id)
        adopted_pid = _adopted.get(run_id)
        if proc and proc.poll() is None:
            _cancelled.add(run_id)
        elif adopted_pid:
            _cancelled.add(run_id)
            proc = None
        else:
            return False
    try:
        (proc or psutil.Process(adopted_pid)).terminate()
    except psutil.NoSuchProcess:
        return False
    return True


def is_running(run_id: str) -> bool:
    with _lock:
        proc = _procs.get(run_id)
        adopted_pid = _adopted.get(run_id)
    return bool(proc and proc.poll() is None) or bool(adopted_pid and psutil.pid_exists(adopted_pid))


_retrying: set[tuple[str, str]] = set()


def retry_banner(run_id: str, banner_id: str) -> bool:
    """Fire-and-forget: launches a subprocess that redoes just this one banner in an already-existing
    run folder (--resume-from + --only-banner - qa/feed_verify.py), for the web UI's per-banner
    "Retry" button. --resume-from never calls the live feed/cohort endpoints (it loads banners.json
    off disk), and the listing/vision calls load their own credentials via qa.envfile.load_env() same
    as the original run did - the one thing still passed explicitly is AJIO_PINCODE, so a retry checks
    the same delivery pincode the original run used rather than silently falling back to .env's/the
    default. Returns False without starting anything if a retry for this exact (run_id, banner_id) is
    already in flight, so a double-click can't pile up duplicate subprocesses."""
    row = db.get_run(run_id)
    if not row:
        raise ValueError(f"unknown run_id: {run_id}")
    key = (run_id, banner_id)
    with _lock:
        if key in _retrying:
            return False
        _retrying.add(key)
    out_dir = row["out_dir"]
    env = {**os.environ, "AJIO_PINCODE": row["pincode"]}
    # --no-cache: a retry is "look at this banner again", so it never reuses an earlier verdict for the same artwork + link
    cmd = [sys.executable, "-m", "qa.feed_verify", "--resume-from", out_dir, "--only-banner", banner_id, "--workers", "1", "--no-cache"]
    proc = subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    threading.Thread(target=_watch_retry, args=(key, proc), daemon=True).start()
    return True


def _watch_retry(key: tuple[str, str], proc: subprocess.Popen) -> None:
    proc.communicate()
    with _lock:
        _retrying.discard(key)


def is_retrying(run_id: str, banner_id: str) -> bool:
    with _lock:
        return (run_id, banner_id) in _retrying


_skip_lock = threading.Lock()


def _skip_requests_path(run_id: str) -> Path:
    row = db.get_run(run_id)
    if not row:
        raise ValueError(f"unknown run_id: {run_id}")
    return Path(row["out_dir"]) / "skip_requests.json"


def _read_skip_requests(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
    except ValueError:
        return set()


def request_skip(run_id: str, banner_id: str) -> None:
    """Marks a still-PENDING banner to be skipped instead of ever being sent, by the still-running
    subprocess for run_id (see qa/feed_verify.py's skip_requested/verify_one) - for the web UI's
    per-banner Skip button. Writes a small shared file in the run's own folder rather than talking to
    the subprocess directly, since that's the only channel this process and that one share. Safe to
    call more than once. Reversible up until the worker actually reaches this banner - see unrequest_skip."""
    path = _skip_requests_path(run_id)
    with _skip_lock:
        ids = _read_skip_requests(path)
        ids.add(banner_id)
        path.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def unrequest_skip(run_id: str, banner_id: str) -> None:
    """Undoes request_skip - the web UI's "Unskip" button. Only meaningful while the banner is still
    PENDING (nothing has checked skip_requested for it yet); once a worker has already recorded it as
    SKIPPED, removing the id here doesn't undo that - the card would need a real Retry instead."""
    path = _skip_requests_path(run_id)
    with _skip_lock:
        ids = _read_skip_requests(path)
        ids.discard(banner_id)
        path.write_text(json.dumps(sorted(ids)), encoding="utf-8")


# ---- reading a run's output (pure reads; safe to do in-process) ---------------------------

from qa.feed_verify import (RETRY_ROUNDS, UNAVAILABLE, _safe, exclude_banners, is_retryable, load_activity,  # noqa: E402
                            load_banners, load_final_results, select_banners, shown_hotspot_result, shown_result)

# 1 initial attempt + RETRY_ROUNDS retries - matches every web-triggered run exactly, since start_run()
# never passes --retry-rounds (always the qa/feed_verify.py CLI default). A banner still INCONCLUSIVE
# with a transient reason after this many attempts is treated as having genuinely given up, not "still
# going" - see run_view()'s try_number/retries_exhausted below.
MAX_TRIES = RETRY_ROUNDS + 1


def _is_processing(out_dir: Path, banner_id: str) -> bool:
    """True once this banner's image has been downloaded to disk but it has no result yet.
    verify_banner() in qa/feed_verify.py writes the image (images/<safe_id>.<ext>) before doing the
    slow part - the listing fetch and vision call - so this file's mere presence is a reliable,
    code-untouched signal that a worker has actually started on this specific banner, not just that
    the run is going. images/ never has a banner's file removed once a run reuses it, so this is
    monotonic within a run (never flips back to "not processing")."""
    images_dir = out_dir / "images"
    return images_dir.exists() and next(images_dir.glob(f"{_safe(banner_id)}.*"), None) is not None


def current_activity(entry: dict | None, now: float | None = None) -> str:
    """The one-or-two-word "what is it doing" for a banner that is in progress. A failed banner's "Cooling
    down" turns into "Retry queued" once its cooldown is over - it's then just waiting for a free worker."""
    if not entry:
        return "Working"
    now = time.time() if now is None else now
    if entry["activity"] == "Cooling down" and entry.get("until") is not None and now >= entry["until"]:
        return "Retry queued"
    return entry["activity"]


def load_shown_results(out_dir: Path, is_live: bool = False) -> dict[str, dict]:
    """load_results with each record's `result` replaced by the status a person sees (see qa.feed_verify.shown_result):
    a banner that only hit a temporary server/network error and used up its retries is UNAVAILABLE, not INCONCLUSIVE.
    What run summaries, the alert diff and the exports count. The raw records (which the retry logic reads) are untouched."""
    out = {}
    for bid, r in load_results(out_dir).items():
        label = shown_result(r, live=is_live, max_tries=MAX_TRIES)
        out[bid] = r if label == r["result"] else {**r, "result": label}
    return out


def load_results(out_dir: Path) -> dict[str, dict]:
    """banner_id -> latest known result: results.json once finished (plus any later per-banner retry), else partial.jsonl."""
    return load_final_results(out_dir)


def run_view(out_dir: Path, scope: str, banner_limit: int | None, is_live: bool = True,
            excluded_carousels: set[int] | None = None, excluded_sections: set[str] | None = None) -> list[dict]:
    """Every banner this run will (or does) cover, in order, each with its latest known result - or
    a PENDING/PROCESSING placeholder if the run hasn't finished it yet (PROCESSING once its image is
    on disk - see _is_processing - PENDING before that, i.e. still queued behind other workers).
    Lets the UI show the full banner list (with images) immediately, then fill in verdicts as
    partial.jsonl grows.

    A banner still cycling through automatic retries (is_retryable(), attempts short of MAX_TRIES) is
    shown as PROCESSING too, with try_number/max_tries attached, rather than the misleadingly-final-
    looking INCONCLUSIVE its last attempt actually produced - partial.jsonl gets a line per attempt
    (see qa/feed_verify.py's verify_one), so without this a transient failure would flash as "done"
    for up to retry_pause seconds between rounds. Once attempts reaches MAX_TRIES and it's still
    retryable, retries_exhausted=True is set instead so the UI can offer a manual retry.

    `is_live` (pass row["status"] == "running"): a "PROCESSING X/N" banner only means anything while
    its run's own process is still alive to actually run the next round - for a cancelled/failed run,
    nothing will ever advance that banner again, so it's treated as exhausted too (offering the same
    manual retry) regardless of its attempts count, instead of showing a permanently-stuck "still
    processing" with no way forward."""
    banners_file = out_dir / "banners.json"
    if not banners_file.exists():
        return []
    banners = load_banners(out_dir)
    chosen = exclude_banners(select_banners(banners, scope), excluded_carousels, excluded_sections)
    if banner_limit:
        chosen = chosen[:banner_limit]
    results = load_results(out_dir)
    skip_ids = _read_skip_requests(out_dir / "skip_requests.json")
    activity = load_activity(out_dir) if is_live else {}
    rows = []
    for b in chosen:
        r = results.get(b.banner_id)
        if r is None:
            if is_live:
                # a banner a worker has reported on is in progress even before its image is on disk (Downloading)
                started = b.banner_id in activity or _is_processing(out_dir, b.banner_id)
                placeholder = "PROCESSING" if started else "PENDING"
                r = {"banner_id": b.banner_id, "alt_text": b.alt_text, "destination_raw": b.destination_raw,
                     "image_url": b.image_url, "result": placeholder, "reason": "",
                     "try_number": 1 if placeholder == "PROCESSING" else 0, "max_tries": MAX_TRIES,
                     "skip_requested": placeholder == "PENDING" and b.banner_id in skip_ids}
            else:
                r = {"banner_id": b.banner_id, "alt_text": b.alt_text, "destination_raw": b.destination_raw,
                     "image_url": b.image_url, "result": "INCONCLUSIVE",
                     "reason": "run stopped before this banner was checked",
                     "try_number": 0, "max_tries": MAX_TRIES, "retries_exhausted": True}
        else:
            attempts = r.get("attempts", 1)
            if is_live and is_retryable(r) and attempts < MAX_TRIES:
                r = {**r, "result": "PROCESSING", "try_number": attempts + 1, "max_tries": MAX_TRIES}
            else:
                exhausted = is_retryable(r) and (attempts >= MAX_TRIES or not is_live)
                note = f"gave up after {attempts} tries" if attempts >= MAX_TRIES else "run stopped before this banner finished retrying"
                if exhausted and r["result"] == "INCONCLUSIVE":
                    reason = f"{note}: {r['reason']}" if r.get("reason") else note
                elif exhausted:          # a FAIL (a real finding) whose hotspot(s) never got checked: keep the finding, add the caveat
                    n = sum(1 for h in r.get("hotspot_checks", []) if shown_hotspot_result(h) == UNAVAILABLE)
                    caveat = f"{n} hotspot{'s' if n != 1 else ''} couldn't be checked ({note})"
                    reason = f"{r['reason']}; {caveat}" if r.get("reason") else caveat
                else:
                    reason = r.get("reason", "")
                r = {**r, "try_number": attempts, "max_tries": MAX_TRIES, "retries_exhausted": exhausted, "reason": reason,
                     **({"result": UNAVAILABLE} if exhausted and r["result"] == "INCONCLUSIVE" else {})}
                if r.get("hotspot_checks"):      # each hotspot's own status: a temporary error on one link is UNAVAILABLE, not a finding
                    r["hotspot_checks"] = [{**h, "result": shown_hotspot_result(h)} for h in r["hotspot_checks"]]
        if r["result"] == "PROCESSING":
            r = {**r, "activity": current_activity(activity.get(b.banner_id))}
        # Retry is offered on a FAIL / INCONCLUSIVE / UNAVAILABLE banner. While the run is still going only when its automatic
        # tries are spent: a retry writes next to the live run, whose own final write would otherwise overwrite it.
        r = {**r, "can_retry": r["result"] in ("FAIL", "INCONCLUSIVE", UNAVAILABLE) and (bool(r.get("retries_exhausted")) or not is_live)}
        # always overlaid from banners.json (not just when missing) so this shows up even for
        # results saved by an older run, from before feed_verify.py started including it itself
        total_links = (1 if b.destination_raw else 0) + len(b.hotspots)
        r = {**r, "section_index": b.section_index, "block_index": b.block_index, "position": b.position,
             "total_links": total_links, "hidden": b.hidden, "hidden_reason": b.hidden_reason}
        rows.append(r)
    return rows


def export_xlsx(out_dir: Path) -> Path:
    """Rebuilds out_dir/results.xlsx fresh from whatever's on disk now (results.json, or partial.jsonl
    for a cancelled/failed run - see qa/export_xlsx.load_results) and returns its path. Not cached: a
    completed run's results.json can still change afterward (a per-banner Retry rewrites it even once
    the run itself shows "done"), so an export button click always reflects the current data."""
    from qa.export_xlsx import build_workbook, load_results
    results = load_results(out_dir)
    if not results:
        raise FileNotFoundError(f"no results to export in {out_dir}")
    wb = build_workbook(results)
    path = out_dir / "results.xlsx"
    wb.save(path)
    return path


def open_folder(out_dir: Path) -> None:
    """Opens out_dir in Windows Explorer - the web UI's "open folder" button, for a local single-user
    tool (CLAUDE.md Sec.0.1: dev machine is Windows) where the server and the person clicking the
    button are the same machine."""
    os.startfile(str(out_dir))  # noqa: S606 - Windows-only by design, see above


def clear_banner_cache() -> int:
    """The web UI's "Clear cache" button (see qa.banner_cache.clear_cache) - forces every banner
    fresh on the next run instead of reusing earlier cross-run verdicts. Returns how many entries
    were cleared."""
    from qa.banner_cache import clear_cache
    return clear_cache()
