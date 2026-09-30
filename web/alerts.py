"""What happens after a scheduled run ends: diff it against the schedule's previous run, record how the
schedule went, and raise an alert when the change is one the schedule asked to hear about.

Only runs that belong to a schedule (runs.schedule_id) are diffed - "the previous run" only means
something within one schedule, whose settings stay fixed between fires. An interactive run has nothing to
compare against and is left alone. Called from web/runner._finalize for every run, however it ended.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from qa import run_diff
from web import db, notify, runner

log = logging.getLogger("web.alerts")


def _config(row) -> tuple:
    """Everything that changes which banners a run covers or how they're judged. Two runs that differ here
    aren't comparable - a different cohort or pincode will legitimately look different."""
    return (row["page"], row["l1"], row["l2"], row["pincode"], row["state"], row["scope"], row["banner_limit"],
            tuple(sorted(json.loads(row["excluded_carousels"] or "[]"))),
            tuple(sorted(json.loads(row["excluded_sections"] or "[]"))))


def compute_diff(row) -> dict:
    """The change vs the schedule's previous finished run of the same l1/l2/state/pincode, or a baseline
    (never alerted on) when there's nothing comparable: the first run of this combination, settings edited
    since the last one, or missing result files."""
    prev = db.previous_done_run(row["schedule_id"], row["started_at"], row["l1"], row["l2"], row["pincode"], row["state"],
                                 row["page"])
    if prev is None:
        return run_diff.baseline("first completed run of this schedule for this page/l1/l2/state/pincode")
    if _config(prev) != _config(row):
        return run_diff.baseline("settings changed since the previous run", prev["run_id"])
    current = runner.load_shown_results(Path(row["out_dir"]))
    before = runner.load_shown_results(Path(prev["out_dir"]))
    if not current:
        return run_diff.baseline("no results were saved for this run", prev["run_id"])
    if not before:
        return run_diff.baseline("the previous run's results are no longer on disk", prev["run_id"])
    diff = run_diff.diff_runs(before, current)
    diff["previous_run_id"] = prev["run_id"]
    return diff


def get_diff(row) -> dict | None:
    """A scheduled run's stored diff - computed (and stored) now if it finished before diffs existed.
    None for a run that isn't a finished scheduled one."""
    if row["schedule_id"] is None or row["status"] != "done":
        return None
    if row["diff_json"]:
        return json.loads(row["diff_json"])
    diff = compute_diff(row)
    db.set_run_diff(row["run_id"], diff)
    return diff


def _raise(schedule, run_id: str, kind: str, title: str, body: str, payload: dict | None = None) -> int:
    alert_id = db.insert_alert(kind, title, body, schedule["id"], run_id, payload)
    if schedule["notify_toast"]:
        db.set_alert_delivery(alert_id, notify.toast(title, body))
    return alert_id


def after_run(run_id: str) -> None:
    row = db.get_run(run_id)
    if row is None or row["schedule_id"] is None:
        return
    schedule = db.get_schedule(row["schedule_id"])      # None if it was deleted while this run was going
    status = row["status"]
    # a schedule that covers several combinations runs each every fire - say which one an alert is about
    name = schedule["name"] if schedule is not None else ""
    tag = ""
    if schedule is not None and len(runner.combos_of(schedule)) > 1:
        tag = runner.combo_label(dict(row))
        name = f"{name} [{tag}]"
    if status == "done":
        diff = compute_diff(row)
        db.set_run_diff(run_id, diff)
        if schedule is None:
            return
        db.set_schedule_status(schedule["id"], "done", f"[{tag}] {run_diff.summarize(diff)}" if tag else run_diff.summarize(diff))
        if run_diff.is_notable(diff, schedule["notify_mode"]):
            title, body = run_diff.alert_text(diff, name, schedule["notify_mode"])
            _raise(schedule, run_id, "regression", title, body, diff["counts"])
    elif status == "failed" and schedule is not None:
        tail = " ".join((row["error"] or "no error output").split())[-300:]
        db.set_schedule_status(schedule["id"], "failed", f"[{tag}] {tail}" if tag else tail)
        if schedule["notify_mode"] != "off":
            _raise(schedule, run_id, "run_failed", f"{name}: scheduled run failed", tail)
    elif status == "cancelled" and schedule is not None:
        db.set_schedule_status(schedule["id"], "cancelled", "the last run was cancelled")


def send_test() -> dict:
    """The scheduler window's "Send test notification" - proves the alert path (and the Windows toast)
    works without waiting for a real regression."""
    title, body = "AJIO Feed Verify: test notification", "If you can read this, scheduled-run alerts can reach you."
    alert_id = db.insert_alert("test", title, body)
    delivery = notify.toast(title, body)
    db.set_alert_delivery(alert_id, delivery)
    return {"alert_id": alert_id, "delivery": delivery}


def alert_view(row) -> dict:
    d = dict(row)
    d["is_read"] = bool(d["is_read"])
    d["payload"] = json.loads(d["payload"]) if d["payload"] else None
    return d
