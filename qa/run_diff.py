"""What actually changed between two runs of the same check - the point of running one on a schedule.

Pure functions over two {banner_id: result record} maps (the shape web/runner.load_results returns),
so the rules can be tested without a database or a live run. A "regression" here means a banner that is
FAIL now and wasn't FAIL last time, *including* a banner that's brand new in the feed - a new banner that
fails is just as much a finding as an old one that broke.
"""
from __future__ import annotations

MODES = ("off", "new_fails", "any_change")   # a schedule's notify_mode
_REASON_MAX = 200


def _short(text: str | None, n: int = _REASON_MAX) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _reference_note(result: dict | None) -> str:
    chk = (result or {}).get("reference_check") or {}
    return chk.get("reason", "") if chk.get("status") == "MISMATCH" else ""


def _entry(banner_id: str, prev: dict | None, cur: dict | None) -> dict:
    src = cur or prev or {}
    return {"banner_id": banner_id, "alt_text": src.get("alt_text") or "", "destination_raw": src.get("destination_raw") or "",
            "image_url": src.get("image_url"), "was": (prev or {}).get("result"), "now": (cur or {}).get("result"),
            "reason": _short((cur or {}).get("reason") or _reference_note(cur))}


def diff_runs(previous: dict[str, dict], current: dict[str, dict]) -> dict:
    """Banner-by-banner comparison. Records with no result (a run that never got to them) are ignored
    on both sides - "never checked" is not a change."""
    prev = {k: v for k, v in previous.items() if v and v.get("result")}
    cur = {k: v for k, v in current.items() if v and v.get("result")}
    newly_failing, newly_inconclusive, recovered, still_failing = [], [], [], 0
    for banner_id, c in cur.items():
        p = prev.get(banner_id)
        now, was = c["result"], (p or {}).get("result")
        if now == "FAIL" and was != "FAIL":
            newly_failing.append(_entry(banner_id, p, c))
        elif now == "FAIL":
            still_failing += 1
        elif now == "INCONCLUSIVE" and was != "INCONCLUSIVE":
            newly_inconclusive.append(_entry(banner_id, p, c))
        elif now == "PASS" and was in ("FAIL", "INCONCLUSIVE"):
            recovered.append(_entry(banner_id, p, c))
    new_banners = [_entry(b, None, cur[b]) for b in cur if b not in prev]
    removed_banners = [_entry(b, prev[b], None) for b in prev if b not in cur]
    unavailable = sum(1 for c in cur.values() if c["result"] == "UNAVAILABLE")
    diff = {"baseline": False, "newly_failing": newly_failing, "newly_inconclusive": newly_inconclusive,
            "recovered": recovered, "new_banners": new_banners, "removed_banners": removed_banners,
            "still_failing": still_failing, "unavailable": unavailable}
    diff["counts"] = counts(diff)
    return diff


def baseline(reason: str, previous_run_id: str | None = None) -> dict:
    """A run with nothing comparable to diff against (the schedule's first run, or its settings changed
    since the last one) - recorded so the UI can say so, never alerted on."""
    return {"baseline": True, "baseline_reason": reason, "previous_run_id": previous_run_id, "newly_failing": [],
            "newly_inconclusive": [], "recovered": [], "new_banners": [], "removed_banners": [], "still_failing": 0,
            "unavailable": 0,
            "counts": {"newly_failing": 0, "newly_inconclusive": 0, "recovered": 0, "new_banners": 0,
                       "removed_banners": 0, "still_failing": 0, "unavailable": 0}}


def counts(diff: dict) -> dict[str, int]:
    return {"newly_failing": len(diff["newly_failing"]), "newly_inconclusive": len(diff["newly_inconclusive"]),
            "recovered": len(diff["recovered"]), "new_banners": len(diff["new_banners"]),
            "removed_banners": len(diff["removed_banners"]), "still_failing": diff["still_failing"],
            "unavailable": diff.get("unavailable", 0)}


def is_notable(diff: dict, mode: str) -> bool:
    """Whether a schedule set to `mode` should be alerted about this diff. new_fails: only a banner that
    is FAIL now and wasn't. any_change: also a new INCONCLUSIVE, a recovery, or a banner appearing/leaving.
    A baseline never alerts."""
    if mode == "off" or diff.get("baseline"):
        return False
    c = diff["counts"]
    if mode == "new_fails":
        return c["newly_failing"] > 0
    return any(c[k] for k in ("newly_failing", "newly_inconclusive", "recovered", "new_banners", "removed_banners"))


def summarize(diff: dict) -> str:
    """One line for a schedule's status: "2 new FAIL, 1 new INCONCLUSIVE, 3 recovered, 1 new banner"."""
    if diff.get("baseline"):
        return f"baseline ({diff.get('baseline_reason', 'nothing to compare')})"
    c = diff["counts"]
    parts = [f"{c['newly_failing']} new FAIL" if c["newly_failing"] else "",
             f"{c['newly_inconclusive']} new INCONCLUSIVE" if c["newly_inconclusive"] else "",
             f"{c['recovered']} recovered" if c["recovered"] else "",
             f"{c['new_banners']} new banner{'s' if c['new_banners'] != 1 else ''}" if c["new_banners"] else "",
             f"{c['removed_banners']} removed" if c["removed_banners"] else "",
             f"{c['unavailable']} couldn't be checked" if c.get("unavailable") else ""]
    return ", ".join(p for p in parts if p) or "no change"


def _line(e: dict) -> str:
    name = e["alt_text"] or e["banner_id"]
    return f"{name[:60]}: {e['reason']}" if e.get("reason") else name[:60]


def alert_text(diff: dict, schedule_name: str, mode: str, max_lines: int = 4) -> tuple[str, str]:
    """(title, body) for an alert - failing banners first (what a person acts on), then the other changes
    if the schedule asked for any change. Capped at max_lines with a "+N more" tail so a toast stays readable."""
    title = f"{schedule_name}: {summarize(diff)}"
    lines = [f"FAIL - {_line(e)}" for e in diff["newly_failing"]]
    if mode == "any_change":
        lines += [f"INCONCLUSIVE - {_line(e)}" for e in diff["newly_inconclusive"]]
        lines += [f"recovered - {_line(e)}" for e in diff["recovered"]]
        lines += [f"new banner - {_line(e)}" for e in diff["new_banners"] if e["now"] not in ("FAIL", "INCONCLUSIVE")]
        lines += [f"removed - {_line(e)}" for e in diff["removed_banners"]]
    extra = len(lines) - max_lines
    lines = lines[:max_lines] + ([f"+{extra} more"] if extra > 0 else [])
    return title, "\n".join(lines)
