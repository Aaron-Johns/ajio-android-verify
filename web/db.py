"""SQLite-backed history for runs, schedules and alerts. Small enough to not need an ORM - a fresh
connection per call, in autocommit-ish mode via `with`, is plenty for a single-user local tool.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "app.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    out_dir      TEXT NOT NULL,
    scope        TEXT NOT NULL,
    banner_limit INTEGER,
    workers      INTEGER NOT NULL,
    l1           TEXT NOT NULL,
    l2           TEXT NOT NULL,
    pincode      TEXT NOT NULL DEFAULT '560029',  -- sent to AJIO's listing API as AJIO_PINCODE
    state        TEXT NOT NULL DEFAULT 'KARNATAKA',  -- sent in the home-feed's x-location-detail header
    status       TEXT NOT NULL,           -- running | done | failed | cancelled
    pid          INTEGER,
    schedule_id  INTEGER,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    error        TEXT,
    hidden       INTEGER NOT NULL DEFAULT 0,  -- UI-only dismissal; the run folder on disk is untouched
    excluded_carousels TEXT,                  -- JSON list of section_index ints unchecked in the carousel picker
    excluded_sections  TEXT,                  -- JSON list of CMS section _id strings (a schedule's saved exclusions)
    diff_json    TEXT,                        -- qa/run_diff result vs the schedule's previous done run of the same combo
    batch_id     TEXT                         -- runs started together (one scheduler fire / one multi-select Run now)
);

CREATE TABLE IF NOT EXISTS schedules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    interval_minutes  INTEGER NOT NULL,
    scope             TEXT NOT NULL,
    banner_limit      INTEGER,
    workers           INTEGER NOT NULL,
    -- l1 / l2 / pincode / state each hold a JSON list of the values the schedule covers (a plain string, from
    -- before multi-select existed, still reads as a one-item list - see as_list). One run per combination.
    l1                TEXT NOT NULL,
    l2                TEXT NOT NULL,
    pincode           TEXT NOT NULL DEFAULT '560029',
    state             TEXT NOT NULL DEFAULT 'KARNATAKA',
    start_at          TEXT,                   -- UTC ISO: the interval is anchored here; NULL = counted from when it was saved
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    last_run_at       TEXT,
    last_run_id       TEXT,
    excluded_sections TEXT,                   -- JSON list of {"id": <section _id>, "label": <display name>}
    notify_mode       TEXT NOT NULL DEFAULT 'new_fails',   -- off | new_fails | any_change (qa/run_diff.MODES)
    notify_toast      INTEGER NOT NULL DEFAULT 1,          -- also pop a Windows notification, not just the in-app alert
    last_status       TEXT,                   -- running | done | failed | cancelled | skipped | error
    last_message      TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    schedule_id  INTEGER,
    run_id       TEXT,
    kind         TEXT NOT NULL,               -- regression | run_failed | test
    title        TEXT NOT NULL,
    message      TEXT NOT NULL,
    payload      TEXT,                        -- JSON: the diff's counts
    is_read      INTEGER NOT NULL DEFAULT 0,
    delivery     TEXT                         -- how the Windows notification went: "ok" / "error: ..." / null if not asked for
);
"""

_RUN_MIGRATIONS = {
    "hidden": "hidden INTEGER NOT NULL DEFAULT 0",
    "excluded_carousels": "excluded_carousels TEXT",
    "pincode": "pincode TEXT NOT NULL DEFAULT '560029'",
    "state": "state TEXT NOT NULL DEFAULT 'KARNATAKA'",
    "excluded_sections": "excluded_sections TEXT",
    "diff_json": "diff_json TEXT",
    "batch_id": "batch_id TEXT",
}
_SCHEDULE_MIGRATIONS = {
    "start_at": "start_at TEXT",
    "pincode": "pincode TEXT NOT NULL DEFAULT '560029'",
    "state": "state TEXT NOT NULL DEFAULT 'KARNATAKA'",
    "excluded_sections": "excluded_sections TEXT",
    "notify_mode": "notify_mode TEXT NOT NULL DEFAULT 'new_fails'",
    "notify_toast": "notify_toast INTEGER NOT NULL DEFAULT 1",
    "last_status": "last_status TEXT",
    "last_message": "last_message TEXT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_list(value) -> list[str]:
    """A schedule's l1/l2/pincode/state column as a list: a JSON list (how it's stored now) or a plain
    string (a row written before multi-select existed) both read as the values they hold."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    text = str(value)
    if not text.strip():
        return []
    if text.startswith("["):
        try:
            return [str(v) for v in json.loads(text)]
        except ValueError:
            pass
    return [text]


_AXIS_COLUMNS = ("l1", "l2", "pincode", "state")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        # CREATE TABLE IF NOT EXISTS above is a no-op on an existing table, so a DB created before a
        # column existed needs it added explicitly.
        for table, migrations in (("runs", _RUN_MIGRATIONS), ("schedules", _SCHEDULE_MIGRATIONS)):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in migrations.items():
                if name not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        if "confirmed" in {row[1] for row in conn.execute("PRAGMA table_info(runs)")}:
            conn.execute("ALTER TABLE runs DROP COLUMN confirmed")


# ---- runs -------------------------------------------------------------------------------

def insert_run(run_id: str, out_dir: str, scope: str, banner_limit: int | None, workers: int,
                l1: str, l2: str, pid: int | None, schedule_id: int | None,
                excluded_carousels: list[int] | None = None, pincode: str = "560029",
                state: str = "KARNATAKA", excluded_sections: list[str] | None = None,
                batch_id: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, out_dir, scope, banner_limit, workers, l1, l2, pincode, state, "
            "status, pid, schedule_id, started_at, excluded_carousels, excluded_sections, batch_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, out_dir, scope, banner_limit, workers, l1, l2, pincode, state,
             "running", pid, schedule_id, _now(),
             json.dumps(excluded_carousels) if excluded_carousels else None,
             json.dumps(excluded_sections) if excluded_sections else None, batch_id),
        )


def finish_run(run_id: str, status: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, error=? WHERE run_id=?",
            (status, _now(), error, run_id),
        )


def set_run_diff(run_id: str, diff: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE runs SET diff_json=? WHERE run_id=?", (json.dumps(diff, ensure_ascii=False), run_id))


def get_run(run_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()


def list_runs(limit: int = 50, include_hidden: bool = False) -> list[sqlite3.Row]:
    with connect() as conn:
        clause = "" if include_hidden else "WHERE hidden=0 "
        return conn.execute(
            f"SELECT * FROM runs {clause}ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()


def list_runs_with_status(status: str) -> list[sqlite3.Row]:
    """Every run in this status, hidden or not - a hidden run is still a live process."""
    with connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE status=? ORDER BY started_at", (status,)).fetchall()


def count_running() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]


def previous_done_run(schedule_id: int, before_started_at: str, l1: str, l2: str, pincode: str,
                      state: str) -> sqlite3.Row | None:
    """The schedule's most recent finished (status done) run of the same l1/l2/pincode/state that started
    before this one. A schedule with several combinations runs each of them every fire, and premium/men only
    means something next to the last premium/men - never next to whichever combination happened to finish last."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE schedule_id=? AND status='done' AND started_at<? "
            "AND l1=? AND l2=? AND pincode=? AND state=? ORDER BY started_at DESC LIMIT 1",
            (schedule_id, before_started_at, l1, l2, pincode, state)).fetchone()


def list_schedule_runs(schedule_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE schedule_id=? ORDER BY started_at DESC LIMIT ?",
                            (schedule_id, limit)).fetchall()


def schedule_stats() -> dict[int, dict]:
    """{schedule_id: {"runs": how many runs it has made, "unread": its unread alerts}} for the scheduler list."""
    stats: dict[int, dict] = {}
    with connect() as conn:
        for sid, n in conn.execute("SELECT schedule_id, COUNT(*) FROM runs WHERE schedule_id IS NOT NULL GROUP BY schedule_id"):
            stats.setdefault(sid, {"runs": 0, "unread": 0})["runs"] = n
        for sid, n in conn.execute("SELECT schedule_id, COUNT(*) FROM alerts WHERE is_read=0 AND schedule_id IS NOT NULL "
                                   "GROUP BY schedule_id"):
            stats.setdefault(sid, {"runs": 0, "unread": 0})["unread"] = n
    return stats


def hide_run(run_id: str) -> None:
    """UI-only dismissal from the Recent runs list - never touches the run's folder on disk."""
    with connect() as conn:
        conn.execute("UPDATE runs SET hidden=1 WHERE run_id=?", (run_id,))


# ---- schedules ----------------------------------------------------------------------------

def insert_schedule(name: str, interval_minutes: int, scope: str, banner_limit: int | None,
                    workers: int, l1: str | list[str], l2: str | list[str], enabled: bool,
                    pincode: str | list[str] = "560029", state: str | list[str] = "KARNATAKA",
                    excluded_sections: list[dict] | None = None, notify_mode: str = "new_fails",
                    notify_toast: bool = True, start_at: str | None = None) -> int:
    """l1 / l2 / pincode / state may each be one value or a list of them (one run per combination)."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO schedules (name, interval_minutes, scope, banner_limit, workers, l1, l2, "
            "pincode, state, enabled, created_at, excluded_sections, notify_mode, notify_toast, start_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, interval_minutes, scope, banner_limit, workers, json.dumps(as_list(l1)), json.dumps(as_list(l2)),
             json.dumps(as_list(pincode)), json.dumps(as_list(state)), int(enabled), _now(),
             json.dumps(excluded_sections) if excluded_sections else None, notify_mode, int(notify_toast), start_at),
        )
        return cur.lastrowid


def update_schedule(schedule_id: int, **fields) -> None:
    if not fields:
        return
    for column in _AXIS_COLUMNS:
        if column in fields:
            fields[column] = json.dumps(as_list(fields[column]))
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE schedules SET {cols} WHERE id=?", (*fields.values(), schedule_id))


def touch_schedule(schedule_id: int, run_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE schedules SET last_run_at=?, last_run_id=? WHERE id=?",
            (_now(), run_id, schedule_id),
        )


def set_schedule_status(schedule_id: int, status: str, message: str = "") -> None:
    with connect() as conn:
        conn.execute("UPDATE schedules SET last_status=?, last_message=? WHERE id=?",
                     (status, message[:500], schedule_id))


def delete_schedule(schedule_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def get_schedule(schedule_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()


def list_schedules() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM schedules ORDER BY created_at DESC").fetchall()


# ---- alerts -------------------------------------------------------------------------------

def insert_alert(kind: str, title: str, message: str, schedule_id: int | None = None, run_id: str | None = None,
                 payload: dict | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO alerts (created_at, schedule_id, run_id, kind, title, message, payload) VALUES (?,?,?,?,?,?,?)",
            (_now(), schedule_id, run_id, kind, title, message, json.dumps(payload) if payload else None))
        return cur.lastrowid


def set_alert_delivery(alert_id: int, delivery: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE alerts SET delivery=? WHERE id=?", (delivery, alert_id))


def list_alerts(limit: int = 50, unread_only: bool = False, schedule_id: int | None = None) -> list[sqlite3.Row]:
    clauses, args = [], []
    if unread_only:
        clauses.append("is_read=0")
    if schedule_id is not None:
        clauses.append("schedule_id=?")
        args.append(schedule_id)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    with connect() as conn:
        return conn.execute(f"SELECT * FROM alerts {where}ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()


def unread_alert_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE is_read=0").fetchone()[0]


def mark_alert_read(alert_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE alerts SET is_read=1 WHERE id=?", (alert_id,))


def delete_alert(alert_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))


def clear_alerts(schedule_id: int | None = None) -> int:
    """Deletes every alert - or only one schedule's. Returns how many were removed."""
    with connect() as conn:
        if schedule_id is None:
            return conn.execute("DELETE FROM alerts").rowcount
        return conn.execute("DELETE FROM alerts WHERE schedule_id=?", (schedule_id,)).rowcount


def mark_all_alerts_read() -> None:
    with connect() as conn:
        conn.execute("UPDATE alerts SET is_read=1 WHERE is_read=0")
