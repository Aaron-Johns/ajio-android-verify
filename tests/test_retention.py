"""Automatic deletion of runs older than 30 days (web/retention.py). Throwaway SQLite and runs/ folder."""
from datetime import datetime, timedelta, timezone

import pytest

from web import db, retention, runner

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    db.init_db()
    return tmp_path / "runs"


def add_run(runs, run_id, age_days, status="done", folder=True, out_dir=None):
    d = out_dir or runs / run_id
    if folder:
        d.mkdir(parents=True, exist_ok=True)
        (d / "results.json").write_text("[]", encoding="utf-8")
    db.insert_run(run_id, str(d), "all", None, 3, "nontransacted", "unisex", None, None)
    db.finish_run(run_id, status)
    with db.connect() as conn:
        conn.execute("UPDATE runs SET started_at=?, status=? WHERE run_id=?",
                     ((NOW - timedelta(days=age_days)).isoformat(), status, run_id))


def test_a_run_older_than_30_days_loses_its_row_and_its_folder(env):
    add_run(env, "old", 31)
    assert retention.purge_old_runs(NOW)["runs"] == ["old"]
    assert db.get_run("old") is None and not (env / "old").exists()


def test_a_run_inside_30_days_is_kept(env):
    add_run(env, "recent", 29)
    add_run(env, "edge", 30)                        # "over 30 days" - exactly 30 is not over
    assert retention.purge_old_runs(NOW)["runs"] == []
    assert db.get_run("recent") and db.get_run("edge") and (env / "recent").exists()


def test_a_run_that_is_still_running_is_never_deleted(env):
    add_run(env, "stuck", 90, status="running")
    assert retention.purge_old_runs(NOW)["runs"] == []
    assert db.get_run("stuck") and (env / "stuck").exists()


def test_hidden_and_failed_runs_are_deleted_too(env):
    add_run(env, "h", 40)
    db.hide_run("h")
    add_run(env, "f", 40, status="failed")
    assert sorted(retention.purge_old_runs(NOW)["runs"]) == ["f", "h"]


def test_an_alert_about_a_deleted_run_stays_without_its_link(env):
    add_run(env, "old", 45)
    alert_id = db.insert_alert("regression", "t", "m", None, "old")
    retention.purge_old_runs(NOW)
    alert = next(a for a in db.list_alerts() if a["id"] == alert_id)
    assert alert["run_id"] is None and alert["title"] == "t"


def test_a_row_whose_folder_is_already_gone_is_still_cleaned_up(env):
    add_run(env, "gone", 45, folder=False)
    assert retention.purge_old_runs(NOW)["runs"] == ["gone"] and db.get_run("gone") is None


def test_a_folder_outside_runs_is_never_deleted_and_its_row_stays(env, tmp_path):
    elsewhere = tmp_path / "precious"
    add_run(env, "odd", 45, out_dir=elsewhere)
    assert retention.purge_old_runs(NOW)["runs"] == []
    assert elsewhere.exists() and db.get_run("odd")


def test_stray_run_folders_go_by_their_timestamp_and_loose_files_stay(env):
    for name in ("20260901T000000Z", "20261110T000000Z_feedverify"):
        (env / name).mkdir()
    (env / "home_check.png").write_bytes(b"x")
    (env / "notes").mkdir()
    assert retention.purge_old_runs(NOW)["folders"] == ["20260901T000000Z"]
    assert not (env / "20260901T000000Z").exists() and (env / "20261110T000000Z_feedverify").exists()
    assert (env / "home_check.png").exists() and (env / "notes").exists()


def test_a_folder_that_cannot_be_removed_keeps_its_row_for_the_next_pass(env, monkeypatch):
    add_run(env, "busy", 45)
    monkeypatch.setattr(retention.shutil, "rmtree", lambda p: (_ for _ in ()).throw(PermissionError("in use")))
    assert retention.purge_old_runs(NOW)["runs"] == [] and db.get_run("busy")


def test_the_server_schedules_the_clean_up_when_it_starts(env):
    from web import api
    api.scheduler.remove_all_jobs()
    api._startup()
    try:
        job = api.scheduler.get_job("purge-old-runs")
        assert job is not None and job.next_run_time - datetime.now(timezone.utc) < timedelta(minutes=3)
    finally:
        api.scheduler.remove_all_jobs()
        api._shutdown()


# ---- deleting one run by hand (the Delete button, DELETE /api/runs/{id}) ----

def test_delete_run_removes_the_row_and_the_folder_and_unlinks_its_alerts(env):
    add_run(env, "mine", 1)
    alert_id = db.insert_alert("regression", "t", "m", None, "mine")
    assert retention.delete_run("mine") == "deleted"
    assert db.get_run("mine") is None and not (env / "mine").exists()
    assert next(a for a in db.list_alerts() if a["id"] == alert_id)["run_id"] is None


def test_delete_run_refuses_a_running_run_and_an_unknown_one(env):
    add_run(env, "live", 1, status="running")
    assert retention.delete_run("live") == "running" and db.get_run("live") and (env / "live").exists()
    assert retention.delete_run("nope") == "unknown"


def test_delete_run_keeps_the_row_when_the_folder_cannot_be_removed(env, monkeypatch):
    add_run(env, "busy", 1)
    monkeypatch.setattr(retention.shutil, "rmtree", lambda p: (_ for _ in ()).throw(PermissionError("in use")))
    assert retention.delete_run("busy") == "stuck" and db.get_run("busy")


def test_the_delete_endpoint_answers_200_404_and_409(env):
    from fastapi.testclient import TestClient
    from web import api
    client = TestClient(api.app)
    add_run(env, "gone", 1)
    add_run(env, "live", 1, status="running")
    assert client.delete("/api/runs/gone").json() == {"deleted": True} and not (env / "gone").exists()
    assert client.delete("/api/runs/gone").status_code == 404
    assert client.delete("/api/runs/live").status_code == 409 and (env / "live").exists()
