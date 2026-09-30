"""Automatic deletion of old runs: a run (its database row and its folder under runs/) is removed once it started
more than RETENTION_DAYS ago. Runs that are still running are never touched. Alerts about a deleted run stay,
minus their "Open run" link. Folders in runs/ that no database row owns (command-line pulls, older builds) are
removed by the timestamp in their name; loose files in runs/ are left alone."""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from web import db, runner

log = logging.getLogger("web.retention")

RETENTION_DAYS = 30      # [D] user-specified (2026-09-30)
_STAMP = re.compile(r"^(\d{8}T\d{6})Z")


def _remove_run_dir(path: Path) -> bool:
    """Delete a run folder, only ever a direct child of runs/. False if it could not be removed (e.g. a file is open)."""
    root = runner.RUNS_DIR.resolve()
    if path.resolve().parent != root:
        log.warning("not deleting %s: it is not directly inside %s", path, root)
        return False
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("could not delete %s (%s); will try again at the next clean-up", path, exc)
        return False
    return True


def purge_old_runs(now: datetime | None = None, days: int = RETENTION_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    runs, folders = [], []
    for row in db.list_runs_started_before(cutoff.isoformat()):
        if _remove_run_dir(Path(row["out_dir"])):
            db.delete_run(row["run_id"])
            runs.append(row["run_id"])
    if runner.RUNS_DIR.exists():
        for p in runner.RUNS_DIR.iterdir():
            m = _STAMP.match(p.name)
            if not (p.is_dir() and m and db.get_run(p.name) is None):
                continue
            if datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc) < cutoff and _remove_run_dir(p):
                folders.append(p.name)
    if runs or folders:
        log.info("deleted runs older than %d days: %d with a database row, %d stray folders", days, len(runs), len(folders))
    return {"runs": runs, "folders": folders}
