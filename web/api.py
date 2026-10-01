"""Local API + static UI for qa/feed_verify.py. Binds to 127.0.0.1 only (CLAUDE.md Sec.11).

Run:  python -m uvicorn web.api:app --port 8000
Then open http://127.0.0.1:8000/

This module is the only thing a UI talks to. It never edits qa/ - see web/runner.py for why runs
are subprocesses. Read-only calls to AJIO only ever happen inside qa/feed_verify.py itself; this
layer just starts it, watches its output files, and serves them back over HTTP/SSE.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, StreamingResponse

from qa import run_diff
from web import alerts, db, retention, runner

log = logging.getLogger("web.api")
app = FastAPI(title="AJIO Feed Verify")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

scheduler = BackgroundScheduler()


# ---- request models -------------------------------------------------------------------------

# l1 / l2 / state / pincode are each a list: a run (or a scheduler fire) covers every combination of them,
# one run per combination.


class RunRequest(BaseModel):
    pages: list[str] = Field(default_factory=lambda: [runner.qa_pages.DEFAULT_PAGE])   # only home uses l1s / l2s
    l1s: list[str]
    l2s: list[str]
    states: list[str] = Field(default_factory=lambda: [runner.DEFAULT_STATE])
    pincodes: list[str] = Field(default_factory=lambda: [runner.DEFAULT_PINCODE])
    scope: str = "hero"
    banner_limit: int | None = Field(default=None, ge=1)
    workers: int = Field(default=3, ge=1, le=runner.MAX_WORKERS)
    excluded_carousels: list[int] = Field(default_factory=list)
    excluded_sections: list[str] = Field(default_factory=list)   # CMS section ids - stay right when the feed's order shifts


class SectionRef(BaseModel):
    """A carousel a schedule leaves out: the CMS section's stable _id (what's matched at run time) plus
    a display name (what the scheduler window shows - the id alone means nothing to a person)."""
    id: str
    label: str = ""


class ScheduleRequest(BaseModel):
    name: str
    interval_minutes: int = Field(ge=1)
    pages: list[str] = Field(default_factory=lambda: [runner.qa_pages.DEFAULT_PAGE])
    l1s: list[str]
    l2s: list[str]
    states: list[str] = Field(default_factory=lambda: [runner.DEFAULT_STATE])
    pincodes: list[str] = Field(default_factory=lambda: [runner.DEFAULT_PINCODE])
    scope: str = "hero"
    banner_limit: int | None = Field(default=None, ge=1)
    workers: int = Field(default=3, ge=1, le=runner.MAX_WORKERS)
    enabled: bool = True
    excluded_sections: list[SectionRef] = Field(default_factory=list)
    notify_mode: str = "new_fails"
    notify_toast: bool = True
    start_at: str | None = None      # ISO date-time the interval counts from; null = from when it's saved


class ScheduleUpdate(BaseModel):
    name: str | None = None
    interval_minutes: int | None = Field(default=None, ge=1)
    pages: list[str] | None = None
    l1s: list[str] | None = None
    l2s: list[str] | None = None
    states: list[str] | None = None
    pincodes: list[str] | None = None
    scope: str | None = None
    banner_limit: int | None = Field(default=None, ge=1)     # an explicit null clears it (= verify everything)
    workers: int | None = Field(default=None, ge=1, le=runner.MAX_WORKERS)
    enabled: bool | None = None
    excluded_sections: list[SectionRef] | None = None         # replaces the whole list; [] clears it
    notify_mode: str | None = None
    notify_toast: bool | None = None
    start_at: str | None = None                                # an explicit null clears it


def _validate_scope(scope: str) -> None:
    if scope not in runner.SCOPES:
        raise HTTPException(422, f"scope must be one of {runner.SCOPES}")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _clean_l1s(values: list[str]) -> list[str]:
    values = _unique(values)
    if not values:
        raise HTTPException(422, f"pick at least one l1 from {runner.L1_OPTIONS}")
    bad = [v for v in values if v not in runner.L1_OPTIONS]
    if bad:
        raise HTTPException(422, f"l1 must be one of {runner.L1_OPTIONS} (got {bad})")
    return values


def _clean_l2s(values: list[str]) -> list[str]:
    values = _unique(values)
    if not values:
        raise HTTPException(422, f"pick at least one l2 from {runner.L2_OPTIONS}")
    bad = [v for v in values if v not in runner.L2_OPTIONS]
    if bad:
        raise HTTPException(422, f"l2 must be one of {runner.L2_OPTIONS} (got {bad})")
    return values


def _clean_states(values: list[str]) -> list[str]:
    """Nothing chosen falls back to the tool's original default rather than 422ing - the same treatment a
    blank pincode gets. A value that isn't a known state still 422s: that's a real typo worth surfacing,
    not "nothing happened"."""
    values = _unique(values) or [runner.DEFAULT_STATE]
    bad = [v for v in values if v not in runner.STATE_OPTIONS]
    if bad:
        raise HTTPException(422, f"state must be one of {runner.STATE_OPTIONS} (got {bad})")
    return values


def _clean_pincodes(values: list[str]) -> list[str]:
    values = _unique(values) or [runner.DEFAULT_PINCODE]
    bad = [v for v in values if not v.isdigit()]
    if bad:
        raise HTTPException(422, f"pincode must be a non-empty numeric string (got {bad})")
    return values


def _clean_pages(values: list[str]) -> list[str]:
    values = _unique(values)
    if not values:
        raise HTTPException(422, f"pick at least one page from {runner.qa_pages.PAGE_IDS}")
    bad = [v for v in values if v not in runner.qa_pages.PAGE_IDS]
    if bad:
        raise HTTPException(422, f"page must be one of {runner.qa_pages.PAGE_IDS} (got {bad})")
    return values


def _check_combo_count(l1s, l2s, states, pincodes, pages) -> None:
    n = len(runner.expand_combos(l1s, l2s, states, pincodes, pages))
    if n > runner.MAX_COMBOS:
        raise HTTPException(422, f"{len(pages)} page(s) x {len(l1s)} l1 x {len(l2s)} l2 (home only) x {len(states)} states x "
                                 f"{len(pincodes)} pincodes = {n} runs; at most {runner.MAX_COMBOS} are allowed at once - "
                                 f"narrow the selection")


def _clean_axes(l1s, l2s, states, pincodes, pages=None) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """(l1s, l2s, states, pincodes, pages) checked and de-duplicated; pages left out means just home."""
    axes = (_clean_l1s(l1s), _clean_l2s(l2s), _clean_states(states), _clean_pincodes(pincodes),
            _clean_pages(pages or [runner.qa_pages.DEFAULT_PAGE]))
    _check_combo_count(*axes)
    return axes


def _parse_start_at(text: str | None) -> str | None:
    """A start date-time as UTC ISO, or None for "no start" (blank/null). A value without a timezone is
    taken as this machine's local time; the UI always sends one with its offset."""
    if text is None or not text.strip():
        return None
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError:
        raise HTTPException(422, f"start_at must be an ISO date-time, e.g. 2026-09-30T10:00:00+05:30 (got {text!r})")
    if when.tzinfo is None:
        when = when.astimezone()          # naive -> local time
    return when.astimezone(timezone.utc).isoformat()


def _validate_notify_mode(mode: str) -> None:
    if mode not in run_diff.MODES:
        raise HTTPException(422, f"notify_mode must be one of {list(run_diff.MODES)}")


def _clean_sections(sections: list[SectionRef]) -> list[dict]:
    """Drop blank ids and duplicates; keep the first label seen for an id."""
    seen: dict[str, str] = {}
    for s in sections:
        sid = s.id.strip()
        if sid and sid not in seen:
            seen[sid] = s.label.strip()[:200]
    return [{"id": sid, "label": label} for sid, label in seen.items()]


# ---- meta -------------------------------------------------------------------------------------

@app.get("/api/meta")
def meta():
    return {
        "l1_options": runner.L1_OPTIONS,
        "l2_options": runner.L2_OPTIONS,
        "page_options": runner.qa_pages.PAGES,
        "scopes": runner.SCOPES,
        "state_options": runner.STATE_OPTIONS,
        "notify_modes": list(run_diff.MODES),
        "max_workers": runner.MAX_WORKERS,
        "max_concurrent_runs": runner.MAX_CONCURRENT_RUNS,
        "max_combos": runner.MAX_COMBOS,
        "default_state": runner.DEFAULT_STATE,
        "default_pincode": runner.DEFAULT_PINCODE,
    }


@app.post("/api/cache/clear")
def clear_cache():
    """Clears the cross-run banner verdict cache (qa/.cache/banner_image_cache.json) - the "Clear
    cache" button in the New run panel. Not tied to any one run/banner: this is global, and only
    affects runs started after this call (an already-running one keeps whatever it already loaded)."""
    return {"cleared": runner.clear_banner_cache()}


# ---- runs -------------------------------------------------------------------------------------

def _excluded_carousels(row) -> set[int]:
    raw = row["excluded_carousels"]
    return set(json.loads(raw)) if raw else set()


def _run_excluded_sections(row) -> set[str]:
    raw = row["excluded_sections"]
    return set(json.loads(raw)) if raw else set()


def _run_summary(row) -> dict:
    d = dict(row)
    out_dir = Path(d["out_dir"])
    results = runner.load_shown_results(out_dir, is_live=d["status"] == "running")
    counts: dict[str, int] = {}
    for r in results.values():
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    d["counts"] = counts
    d["warnings"] = runner.load_warnings(out_dir)
    if d["status"] == "running":     # counts only holds banners with a result so far; "x of n done" needs n
        d["total"] = len(runner.chosen_banners(out_dir, d["scope"], d["banner_limit"], _excluded_carousels(row),
                                               _run_excluded_sections(row)))
    d["excluded_carousels"] = sorted(_excluded_carousels(row))
    d["excluded_sections"] = sorted(_run_excluded_sections(row))
    # the full diff is big and has its own endpoint - a run summary only carries the headline
    raw_diff = d.pop("diff_json", None)
    diff = json.loads(raw_diff) if raw_diff else None
    d["diff"] = None if diff is None else {
        "baseline": diff["baseline"], "baseline_reason": diff.get("baseline_reason"),
        "previous_run_id": diff.get("previous_run_id"), "counts": diff["counts"], "summary": run_diff.summarize(diff)}
    return d


@app.get("/api/feed-preview")
def feed_preview(l1: str, l2: str, scope: str = "hero", page: str = runner.qa_pages.DEFAULT_PAGE):
    """Fetches the feed fresh and groups it by carousel, without running any verification - the "New
    run" form's carousel checklist calls this before a run starts (see runner.list_carousels)."""
    _clean_l1s([l1]), _clean_l2s([l2]), _validate_scope(scope)
    try:
        return runner.list_carousels(l1, l2, scope, page)
    except Exception as exc:
        raise HTTPException(502, f"could not fetch the feed: {exc}") from exc


@app.post("/api/runs")
def create_run(req: RunRequest):
    """Starts one run per page x l1 x l2 x state x pincode combination (l1/l2 only vary for the home page). The first starts now and its id is
    `run_id`; the others start one after another as each finishes (`queued` says how many are waiting)."""
    _validate_scope(req.scope)
    l1s, l2s, states, pincodes, pages = _clean_axes(req.l1s, req.l2s, req.states, req.pincodes, req.pages)
    combos = runner.expand_combos(l1s, l2s, states, pincodes, pages)
    run_id, batch_id, queued = runner.start_batch(
        combos, req.scope, req.banner_limit, req.workers, excluded_carousels=req.excluded_carousels or None,
        excluded_sections=_unique(req.excluded_sections) or None)
    return {"run_id": run_id, "batch_id": batch_id, "combos": len(combos), "queued": queued}


@app.get("/api/runs")
def list_runs(limit: int = 50, schedule_id: int | None = None):
    """The recent runs (hidden ones left out) - or, with schedule_id, that schedule's runs, newest first."""
    rows = db.list_schedule_runs(schedule_id, limit) if schedule_id is not None else db.list_runs(limit)
    return [_run_summary(row) for row in rows]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    return _run_summary(row)


@app.get("/api/runs/{run_id}/banners")
def get_run_banners(run_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    return runner.run_view(Path(row["out_dir"]), row["scope"], row["banner_limit"],
                           is_live=row["status"] == "running", excluded_carousels=_excluded_carousels(row),
                           excluded_sections=_run_excluded_sections(row))


@app.get("/api/runs/{run_id}/diff")
def get_run_diff(run_id: str):
    """What changed vs the previous finished run of the same schedule - full detail behind a run
    summary's `diff` headline. Only a finished scheduled run has one."""
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    diff = alerts.get_diff(row)
    if diff is None:
        raise HTTPException(404, "only a finished run that belongs to a schedule has a diff")
    return diff


@app.get("/api/runs/{run_id}/images/{filename}")
def get_run_image(run_id: str, filename: str):
    """Serves a locally-downloaded image (a hotspot crop, or the banner's own saved copy) from this
    run's images/ folder - unlike a banner's live image_url (a public CDN link the browser loads
    directly), a crop only ever exists on this machine's disk. filename must be a bare basename
    (no path separators) - it always is, coming from image_file's own Path(...).name, but this is
    the only user-reachable boundary that turns a run_id + name into a filesystem read, so it's
    still checked here rather than trusted from the caller."""
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    if filename != Path(filename).name:
        raise HTTPException(400, "invalid filename")
    path = Path(row["out_dir"]) / "images" / filename
    if not path.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(path)


@app.get("/api/runs/{run_id}/export.xlsx")
def export_run_xlsx(run_id: str):
    """Only offered once a run is off "running" (see the button's disabled state in the UI) - a still-
    running run's own results.json can be rewritten mid-flight by its own process, which isn't a file
    a second reader should be racing against."""
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    if row["status"] == "running":
        raise HTTPException(409, "run is still in progress")
    try:
        path = runner.export_xlsx(Path(row["out_dir"]))
    except FileNotFoundError:
        raise HTTPException(404, "no results to export for this run")
    timestamp = run_id.split("_", 1)[0]   # run_id is "<timestamp>_feedverify" - see web/runner.start_run
    what = f"{row['l1']}_{row['l2']}" if row["page"] == runner.qa_pages.DEFAULT_PAGE else row["page"]
    return FileResponse(path, filename=f"{what}_{timestamp}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/runs/{run_id}/open-folder")
def open_run_folder(run_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    out_dir = Path(row["out_dir"])
    if not out_dir.exists():
        raise HTTPException(404, "run folder not found")
    runner.open_folder(out_dir)
    return {"opened": True}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    ok = runner.cancel_run(run_id)
    if not ok:
        raise HTTPException(409, "run is not currently running")
    return {"cancelled": True}


@app.post("/api/runs/{run_id}/banners/{banner_id}/retry")
def retry_banner(run_id: str, banner_id: str):
    """Redoes just this one banner in place (see web/runner.retry_banner) - for a banner that gave up
    after exhausting its automatic retries. Fire-and-forget: returns immediately: the UI polls
    /api/runs/{run_id}/banners afterward to see the updated result once the subprocess finishes."""
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    started = runner.retry_banner(run_id, banner_id)
    if not started:
        raise HTTPException(409, "a retry for this banner is already in progress")
    return {"started": True}


@app.post("/api/runs/{run_id}/banners/{banner_id}/skip")
def skip_banner(run_id: str, banner_id: str):
    """Only meaningful for a still-PENDING banner (that's all the UI offers it for) - dropped before
    any work starts. Reversible via unskip below, right up until a worker actually reaches it."""
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    runner.request_skip(run_id, banner_id)
    return {"requested": True}


@app.post("/api/runs/{run_id}/banners/{banner_id}/unskip")
def unskip_banner(run_id: str, banner_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    runner.unrequest_skip(run_id, banner_id)
    return {"requested": True}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    """Deletes a run for good: its database row and its whole folder under runs/ (images, results, everything). Not for a running run."""
    result = retention.delete_run(run_id)
    if result == "unknown":
        raise HTTPException(404, "unknown run_id")
    if result == "running":
        raise HTTPException(409, "this run is still running: stop it first")
    if result == "stuck":
        raise HTTPException(409, "the run's folder could not be removed (a file in it may be open), so nothing was deleted")
    return {"deleted": True}


@app.post("/api/runs/{run_id}/hide")
def hide_run(run_id: str):
    """Dismisses a run from the Recent runs list only - its folder under runs/ is left exactly as is."""
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    db.hide_run(run_id)
    return {"hidden": True}


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, "unknown run_id")
    out_dir = Path(row["out_dir"])
    scope, banner_limit = row["scope"], row["banner_limit"]
    excluded_carousels = _excluded_carousels(row)
    excluded_sections = _run_excluded_sections(row)

    async def gen():
        last: dict[str, dict] = {}
        while True:
            rows = runner.run_view(out_dir, scope, banner_limit, is_live=True, excluded_carousels=excluded_carousels,
                                   excluded_sections=excluded_sections)  # only reached while status == "running"
            changed = [r for r in rows if last.get(r["banner_id"]) != r]
            for r in changed:
                last[r["banner_id"]] = r
                yield f"data: {json.dumps(r)}\n\n"
            cur = db.get_run(run_id)
            if cur["status"] != "running":
                yield f"event: done\ndata: {json.dumps({'status': cur['status']})}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- schedules --------------------------------------------------------------------------------

def _apscheduler_job(schedule_id: int) -> None:
    row = db.get_schedule(schedule_id)
    if not row or not row["enabled"]:
        return

    def still_wanted() -> bool:
        current = db.get_schedule(schedule_id)
        return bool(current and current["enabled"])

    try:
        _, skipped = runner.start_scheduled_batch(
            row, still_wanted, on_started=lambda run_id: db.touch_schedule(schedule_id, run_id))
    except Exception as exc:
        log.exception("schedule %s: could not start its runs", schedule_id)
        db.set_schedule_status(schedule_id, "error", f"could not start the run: {type(exc).__name__}: {exc}")
        return
    if skipped:
        db.set_schedule_status(
            schedule_id, "skipped", f"skipped {skipped} of {len(runner.combos_of(row))} run(s) this fire: another run was "
            f"still in progress after waiting {int(runner.SCHEDULED_MAX_WAIT_S / 60)} min")


def _start_date(row):
    """Where the interval is anchored: the schedule's start date-time, or None (counted from now)."""
    return datetime.fromisoformat(row["start_at"]) if row["start_at"] else None


def _sync_job(row) -> None:
    job_id = f"schedule-{row['id']}"
    if row["enabled"]:
        # coalesce + a grace period: a fire that had to wait for a free thread (or for the run gate) is
        # still run once, not silently dropped as a misfire. With a start date the first fire is that moment
        # (or, if it has already passed, the next interval slot after it: start + k x interval).
        scheduler.add_job(_apscheduler_job, IntervalTrigger(minutes=row["interval_minutes"], start_date=_start_date(row)),
                          args=[row["id"]], id=job_id, replace_existing=True, max_instances=1,
                          coalesce=True, misfire_grace_time=300)
    else:
        scheduler.remove_job(job_id) if scheduler.get_job(job_id) else None


def _section_ids(row) -> list[str]:
    return [s["id"] for s in json.loads(row["excluded_sections"] or "[]")]


def _schedule_view(row, stats: dict | None = None, with_latest_run: bool = False) -> dict:
    d = dict(row)
    # l1 / l2 / state / pincode are each stored as a list; the view names them l1s / l2s / states / pincodes
    d["l1s"], d["l2s"] = db.as_list(d.pop("l1")), db.as_list(d.pop("l2"))
    d["pages"] = db.as_list(d.pop("page")) or [runner.qa_pages.DEFAULT_PAGE]
    d["states"] = db.as_list(d.pop("state")) or [runner.DEFAULT_STATE]
    d["pincodes"] = db.as_list(d.pop("pincode")) or [runner.DEFAULT_PINCODE]
    d["combo_count"] = len(runner.combos_of(row))
    d["excluded_sections"] = json.loads(row["excluded_sections"]) if row["excluded_sections"] else []
    d["notify_toast"] = bool(row["notify_toast"])
    job = scheduler.get_job(f"schedule-{row['id']}")
    next_run = getattr(job, "next_run_time", None) if job else None
    d["next_run_at"] = next_run.isoformat() if next_run else None
    counts = (stats or {}).get(row["id"], {})
    d["run_count"] = counts.get("runs", 0)
    d["unread_alerts"] = counts.get("unread", 0)
    if with_latest_run:
        latest = db.list_schedule_runs(row["id"], 1)
        d["latest_run"] = _run_summary(latest[0]) if latest else None
    return d


@app.get("/api/schedules")
def list_schedules():
    stats = db.schedule_stats()
    return [_schedule_view(r, stats, with_latest_run=True) for r in db.list_schedules()]


@app.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: int):
    """One schedule with its recent alerts - the scheduler window's "View". Its runs come from
    GET /api/runs?schedule_id=..."""
    row = db.get_schedule(schedule_id)
    if not row:
        raise HTTPException(404, "unknown schedule_id")
    d = _schedule_view(row, db.schedule_stats(), with_latest_run=True)
    d["alerts"] = [alerts.alert_view(a) for a in db.list_alerts(10, schedule_id=schedule_id)]
    return d


@app.post("/api/schedules")
def create_schedule(req: ScheduleRequest):
    _validate_scope(req.scope)
    l1s, l2s, states, pincodes, pages = _clean_axes(req.l1s, req.l2s, req.states, req.pincodes, req.pages)
    _validate_notify_mode(req.notify_mode)
    sid = db.insert_schedule(req.name, req.interval_minutes, req.scope, req.banner_limit, req.workers,
                             l1s, l2s, req.enabled, pincode=pincodes, state=states,
                             excluded_sections=_clean_sections(req.excluded_sections) or None,
                             notify_mode=req.notify_mode, notify_toast=req.notify_toast,
                             start_at=_parse_start_at(req.start_at), pages=pages)
    _sync_job(db.get_schedule(sid))
    return _schedule_view(db.get_schedule(sid), db.schedule_stats(), with_latest_run=True)


# a field a PATCH may set to an explicit null (everything else: null just means "leave it")
_NULLABLE_SCHEDULE_FIELDS = {"banner_limit", "start_at"}

# the request's plural names -> the schedules table's column names
_AXIS_FIELDS = {"l1s": "l1", "l2s": "l2", "states": "state", "pincodes": "pincode", "pages": "page"}


@app.patch("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: int, req: ScheduleUpdate):
    row = db.get_schedule(schedule_id)
    if not row:
        raise HTTPException(404, "unknown schedule_id")
    fields = {}
    for key in req.model_fields_set:
        value = getattr(req, key)
        if value is not None or key in _NULLABLE_SCHEDULE_FIELDS:
            fields[key] = value
    if "scope" in fields:
        _validate_scope(fields["scope"])
    # any one axis being edited is checked against the others as they'll be, so the combo cap holds
    axes = {"l1s": db.as_list(row["l1"]), "l2s": db.as_list(row["l2"]),
            "states": db.as_list(row["state"]) or [runner.DEFAULT_STATE],
            "pincodes": db.as_list(row["pincode"]) or [runner.DEFAULT_PINCODE],
            "pages": db.as_list(row["page"]) or [runner.qa_pages.DEFAULT_PAGE]}
    if any(k in fields for k in _AXIS_FIELDS):
        for k in _AXIS_FIELDS:
            if k in fields:
                axes[k] = fields.pop(k)
        cleaned = _clean_axes(axes["l1s"], axes["l2s"], axes["states"], axes["pincodes"], axes["pages"])
        for k, values in zip(("l1s", "l2s", "states", "pincodes", "pages"), cleaned):
            fields[_AXIS_FIELDS[k]] = values
    if "start_at" in fields:
        fields["start_at"] = _parse_start_at(fields["start_at"])
    if "notify_mode" in fields:
        _validate_notify_mode(fields["notify_mode"])
    if "excluded_sections" in fields:
        cleaned = _clean_sections(fields["excluded_sections"])
        fields["excluded_sections"] = json.dumps(cleaned) if cleaned else None
    for key in ("enabled", "notify_toast"):
        if key in fields:
            fields[key] = int(fields[key])
    if fields:
        db.update_schedule(schedule_id, **fields)
    updated = db.get_schedule(schedule_id)
    _sync_job(updated)
    return _schedule_view(updated, db.schedule_stats(), with_latest_run=True)


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    job_id = f"schedule-{schedule_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    db.delete_schedule(schedule_id)
    return {"deleted": True}


@app.post("/api/schedules/{schedule_id}/run-now")
def run_schedule_now(schedule_id: int):
    """Starts the schedule's runs immediately - its first combination now, any others one after another.
    Deliberately not held back by the concurrency gate that scheduled fires wait behind - an explicit click
    means run it now."""
    row = db.get_schedule(schedule_id)
    if not row:
        raise HTTPException(404, "unknown schedule_id")
    combos = runner.combos_of(row)
    run_id, batch_id, queued = runner.start_batch(combos, row["scope"], row["banner_limit"], row["workers"],
                                                  schedule_id=schedule_id, excluded_sections=_section_ids(row) or None)
    db.touch_schedule(schedule_id, run_id)
    return {"run_id": run_id, "batch_id": batch_id, "combos": len(combos), "queued": queued}


# ---- alerts -----------------------------------------------------------------------------------

@app.get("/api/alerts")
def list_alerts(limit: int = 50, unread: bool = False, schedule_id: int | None = None):
    return {"unread": db.unread_alert_count(),
            "items": [alerts.alert_view(r) for r in db.list_alerts(limit, unread, schedule_id)]}


@app.post("/api/alerts/read-all")
def read_all_alerts():
    db.mark_all_alerts_read()
    return {"unread": 0}


@app.post("/api/alerts/{alert_id}/read")
def read_alert(alert_id: int):
    db.mark_alert_read(alert_id)
    return {"unread": db.unread_alert_count()}


@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: int):
    """Removes one alert from the list (idempotent). A Windows notification already shown isn't affected."""
    db.delete_alert(alert_id)
    return {"unread": db.unread_alert_count()}


@app.delete("/api/alerts")
def clear_alerts(schedule_id: int | None = None):
    """Clears every alert, or with schedule_id only that schedule's."""
    removed = db.clear_alerts(schedule_id)
    return {"removed": removed, "unread": db.unread_alert_count()}


@app.post("/api/notify/test")
def notify_test():
    """Raises a test alert and tries the Windows toast, reporting how delivery went ("ok" or why not)."""
    return alerts.send_test()


# ---- lifecycle + static UI ---------------------------------------------------------------------

@app.on_event("startup")
def _startup():
    db.init_db()
    summary = runner.reconcile_orphans()
    if summary["adopted"] or summary["finalized"]:
        log.warning("reconciled runs left over from before this start: adopted %s (still running), finalized %s",
                    summary["adopted"], summary["finalized"])
    scheduler.start()
    for row in db.list_schedules():
        if row["enabled"]:
            _sync_job(row)
    # once shortly after every start (the PC may be off at any fixed hour), then daily
    scheduler.add_job(retention.purge_old_runs, IntervalTrigger(hours=24), id="purge-old-runs", replace_existing=True,
                      max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2))


@app.on_event("shutdown")
def _shutdown():
    scheduler.shutdown(wait=False)


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
