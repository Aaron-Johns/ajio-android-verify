"""Scheduled-run bookkeeping: the diff/alert step after a run, orphan reconciliation after a server
restart, and the gate that stops scheduled runs piling up. Uses a throwaway SQLite file and stubs the
Windows toast; the reconciliation tests spawn real (sleeping) processes to exercise the psutil checks."""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from web import alerts, db, notify, runner


@pytest.fixture
def toasts(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    db.init_db()
    shown = []
    monkeypatch.setattr(notify, "toast", lambda title, body: (shown.append((title, body)), "ok")[1])
    return shown


def make_schedule(**kw):
    args = dict(name="Hourly hero", interval_minutes=60, scope="hero", banner_limit=None, workers=3,
                l1="nontransacted", l2="unisex", enabled=True)
    args.update(kw)
    return db.insert_schedule(**args)


def finished_run(tmp_path, run_id, schedule_id, results, status="done", error=None, pincode="560029",
                 l1="nontransacted", l2="unisex", excluded_sections=None):
    out = tmp_path / run_id
    out.mkdir()
    if results is not None:
        (out / "results.json").write_text(json.dumps(
            [{"banner_id": k, "alt_text": k.upper(), "reason": "", **v} for k, v in results.items()]), encoding="utf-8")
    db.insert_run(run_id, str(out), "hero", None, 3, l1, l2, 1, schedule_id, pincode=pincode,
                  excluded_sections=excluded_sections)
    db.finish_run(run_id, status, error)
    alerts.after_run(run_id)


P, F = {"result": "PASS"}, {"result": "FAIL", "reason": "missing brands ['Levis']"}


def test_the_first_run_of_a_schedule_is_a_baseline_and_never_alerts(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": F})
    diff = json.loads(db.get_run("r1")["diff_json"])
    assert diff["baseline"] and db.list_alerts() == [] and toasts == []
    assert db.get_schedule(sid)["last_status"] == "done"


def test_a_new_failure_after_a_baseline_raises_an_alert_and_a_toast(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": P, "b": P})
    finished_run(tmp_path, "r2", sid, {"a": F, "b": P})
    (alert,) = db.list_alerts()
    assert alert["kind"] == "regression" and alert["run_id"] == "r2" and alert["delivery"] == "ok"
    assert alert["title"] == "Hourly hero: 1 new FAIL" and "FAIL - A: missing brands ['Levis']" in alert["message"]
    assert toasts == [(alert["title"], alert["message"])]
    assert json.loads(db.get_run("r2")["diff_json"])["previous_run_id"] == "r1"
    assert db.get_schedule(sid)["last_message"] == "1 new FAIL"


def test_a_failure_that_was_already_failing_does_not_alert_again(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": F})
    finished_run(tmp_path, "r2", sid, {"a": F})
    assert db.list_alerts() == [] and db.get_schedule(sid)["last_message"] == "no change"


def test_notify_mode_off_still_records_the_diff_but_raises_nothing(tmp_path, toasts):
    sid = make_schedule(notify_mode="off")
    finished_run(tmp_path, "r1", sid, {"a": P})
    finished_run(tmp_path, "r2", sid, {"a": F})
    assert db.list_alerts() == [] and toasts == []
    assert json.loads(db.get_run("r2")["diff_json"])["counts"]["newly_failing"] == 1


def test_turning_the_toast_off_keeps_the_in_app_alert(tmp_path, toasts):
    sid = make_schedule(notify_toast=False)
    finished_run(tmp_path, "r1", sid, {"a": P})
    finished_run(tmp_path, "r2", sid, {"a": F})
    (alert,) = db.list_alerts()
    assert alert["delivery"] is None and toasts == []


def test_any_change_mode_alerts_on_a_recovery_that_new_fails_mode_ignores(tmp_path, toasts):
    quiet, loud = make_schedule(name="quiet"), make_schedule(name="loud", notify_mode="any_change")
    for sid, tag in ((quiet, "q"), (loud, "l")):
        finished_run(tmp_path, f"{tag}1", sid, {"a": F})
        finished_run(tmp_path, f"{tag}2", sid, {"a": P})
    assert [a["schedule_id"] for a in db.list_alerts()] == [loud]


def test_editing_the_schedules_settings_makes_the_next_run_a_baseline(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": P})
    finished_run(tmp_path, "r2", sid, {"a": F}, excluded_sections=["SEC-A"])     # a carousel now left out: not comparable
    diff = json.loads(db.get_run("r2")["diff_json"])
    assert diff["baseline"] and "settings changed" in diff["baseline_reason"] and db.list_alerts() == []


def test_a_new_pincode_is_its_own_baseline_not_a_comparison_with_the_old_one(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": P})
    finished_run(tmp_path, "r2", sid, {"a": F}, pincode="400001")          # a different combination: nothing to compare to
    diff = json.loads(db.get_run("r2")["diff_json"])
    assert diff["baseline"] and "for this page/l1/l2/state/pincode" in diff["baseline_reason"] and db.list_alerts() == []


def test_each_combination_of_a_multi_combo_schedule_is_diffed_against_its_own_previous_run(tmp_path, toasts):
    sid = make_schedule(l1=["premium", "nonpremium"], l2=["men"])
    finished_run(tmp_path, "p1", sid, {"a": P}, l1="premium", l2="men")
    finished_run(tmp_path, "n1", sid, {"a": F}, l1="nonpremium", l2="men")     # already failing: its own baseline
    assert db.list_alerts() == []
    finished_run(tmp_path, "p2", sid, {"a": F}, l1="premium", l2="men")        # premium regressed
    finished_run(tmp_path, "n2", sid, {"a": F}, l1="nonpremium", l2="men")     # nonpremium unchanged
    assert json.loads(db.get_run("p2")["diff_json"])["previous_run_id"] == "p1"
    assert json.loads(db.get_run("n2")["diff_json"])["previous_run_id"] == "n1"
    (alert,) = db.list_alerts()
    assert alert["run_id"] == "p2"
    assert alert["title"] == "Hourly hero [premium/men · KARNATAKA · 560029]: 1 new FAIL"
    assert db.get_schedule(sid)["last_message"].startswith("[nonpremium/men · KARNATAKA · 560029]")


def test_a_failed_run_of_a_multi_combo_schedule_names_the_combination(tmp_path, toasts):
    sid = make_schedule(l1=["premium", "nonpremium"], l2=["men"])
    finished_run(tmp_path, "p1", sid, None, status="failed", error="FeedError: HTTP 401", l1="premium", l2="men")
    (alert,) = db.list_alerts()
    assert alert["title"] == "Hourly hero [premium/men · KARNATAKA · 560029]: scheduled run failed"


def test_a_failed_scheduled_run_raises_a_run_failed_alert(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, None, status="failed", error="Traceback...\nFeedError: HTTP 401")
    (alert,) = db.list_alerts()
    assert alert["kind"] == "run_failed" and "FeedError: HTTP 401" in alert["message"]
    assert db.get_schedule(sid)["last_status"] == "failed"


def test_a_cancelled_run_only_updates_the_schedules_status(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {}, status="cancelled")
    assert db.list_alerts() == [] and db.get_schedule(sid)["last_status"] == "cancelled"


def test_manual_runs_and_runs_of_a_deleted_schedule_are_left_alone(tmp_path, toasts):
    finished_run(tmp_path, "manual", None, {"a": F})
    assert db.get_run("manual")["diff_json"] is None and alerts.get_diff(db.get_run("manual")) is None
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": P})
    db.delete_schedule(sid)
    finished_run(tmp_path, "r2", sid, {"a": F})                             # schedule gone: nobody to alert
    assert db.list_alerts() == [] and toasts == []


def test_a_diff_is_computed_on_demand_for_a_run_that_finished_before_diffs_existed(tmp_path, toasts):
    sid = make_schedule()
    finished_run(tmp_path, "r1", sid, {"a": P})
    finished_run(tmp_path, "r2", sid, {"a": F})
    with db.connect() as conn:
        conn.execute("UPDATE runs SET diff_json=NULL WHERE run_id='r2'")
    diff = alerts.get_diff(db.get_run("r2"))
    assert diff["counts"]["newly_failing"] == 1 and db.get_run("r2")["diff_json"] is not None
    assert alerts.get_diff(db.get_run("r1"))["baseline"]


def test_the_test_notification_records_an_alert_and_reports_how_delivery_went(toasts):
    result = alerts.send_test()
    assert result["delivery"] == "ok" and len(toasts) == 1
    (alert,) = db.list_alerts()
    assert alert["kind"] == "test" and alert["delivery"] == "ok" and db.unread_alert_count() == 1
    db.mark_all_alerts_read()
    assert db.unread_alert_count() == 0


def test_the_toast_never_raises_even_when_powershell_cannot_be_started(monkeypatch):
    import web.notify as real
    monkeypatch.setattr(real.sys, "platform", "win32")
    monkeypatch.setattr(real.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no powershell")))
    assert real.toast("t", "b").startswith("error: FileNotFoundError")
    monkeypatch.setattr(real.sys, "platform", "linux")
    assert real.toast("t", "b").startswith("skipped")


# ---- runs that outlive the server ----------------------------------------------------------------

def sleeper(*extra):
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *extra])


def running_row(tmp_path, run_id, pid, results=None):
    out = tmp_path / run_id
    out.mkdir()
    if results is not None:
        (out / "results.json").write_text("[]", encoding="utf-8")
    db.insert_run(run_id, str(out), "hero", None, 3, "nontransacted", "unisex", pid, None)


def wait_for(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_a_running_row_whose_process_is_gone_is_finalized(tmp_path, toasts):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    running_row(tmp_path, "finished", dead.pid, results=[])
    running_row(tmp_path, "crashed", dead.pid)
    summary = runner.reconcile_orphans()
    assert sorted(summary["finalized"]) == ["crashed", "finished"] and summary["adopted"] == []
    assert db.get_run("finished")["status"] == "done"          # had finished writing results.json
    crashed = db.get_run("crashed")
    assert crashed["status"] == "failed" and "server restarted" in crashed["error"]


def test_a_live_run_process_is_adopted_and_can_be_cancelled_again(tmp_path, toasts):
    proc = sleeper("qa.feed_verify")
    try:
        running_row(tmp_path, "orphan", proc.pid)
        summary = runner.reconcile_orphans()
        assert summary == {"adopted": ["orphan"], "finalized": []}
        assert db.get_run("orphan")["status"] == "running" and runner.is_running("orphan")
        assert runner.cancel_run("orphan") is True
        assert wait_for(lambda: db.get_run("orphan")["status"] == "cancelled")
        assert not runner.is_running("orphan")
    finally:
        proc.kill()


def test_a_reused_pid_belonging_to_some_other_program_is_not_adopted(tmp_path, toasts):
    proc = sleeper()                                         # alive, but not a qa.feed_verify process
    try:
        running_row(tmp_path, "reused", proc.pid)
        summary = runner.reconcile_orphans()
        assert summary == {"adopted": [], "finalized": ["reused"]}
        assert db.get_run("reused")["status"] == "failed"
    finally:
        proc.kill()


def test_a_process_created_long_before_or_after_the_run_started_is_not_its_process():
    proc = sleeper("qa.feed_verify")
    try:
        now = datetime.now(timezone.utc)
        assert runner._run_process(proc.pid, now.isoformat()) is not None
        assert runner._run_process(proc.pid, (now - timedelta(days=1)).isoformat()) is None   # process is newer than the run
        assert runner._run_process(proc.pid, (now + timedelta(days=1)).isoformat()) is None   # process is far older
        assert runner._run_process(None, now.isoformat()) is None
        assert runner._run_process(proc.pid, "not a timestamp") is None
    finally:
        proc.kill()


# ---- the run gate ----------------------------------------------------------------------------------

SCHEDULE_ROW = {"id": 7, "l1": "nontransacted", "l2": "unisex", "scope": "hero", "banner_limit": None, "workers": 3,
                "pincode": "400001", "state": "ASSAM",
                "excluded_sections": json.dumps([{"id": "SEC-A", "label": "Bank offers"}])}


def test_a_scheduled_fire_waits_for_the_active_run_then_starts_with_the_schedules_settings(monkeypatch):
    counts = iter([1, 1, 0])
    monkeypatch.setattr(runner.db, "count_running", lambda: next(counts))
    started = []
    monkeypatch.setattr(runner, "start_run", lambda *a, **k: (started.append((a, k)), "run-1")[1])
    slept = []
    run_id = runner.start_scheduled_run(SCHEDULE_ROW, wait_s=100, poll_s=5, sleep=slept.append, clock=lambda: 0)
    assert run_id == "run-1" and slept == [5, 5]
    (args, kwargs), = started
    assert args[:5] == ("nontransacted", "unisex", "hero", None, 3)
    assert kwargs == {"schedule_id": 7, "pincode": "400001", "state": "ASSAM", "excluded_sections": ["SEC-A"],
                      "batch_id": None, "page": "home"}


def test_a_scheduled_fire_that_never_gets_a_free_slot_gives_up_without_starting(monkeypatch):
    monkeypatch.setattr(runner.db, "count_running", lambda: 1)
    monkeypatch.setattr(runner, "start_run", lambda *a, **k: pytest.fail("must not start"))
    ticks = iter([0, 50, 150])
    assert runner.start_scheduled_run(SCHEDULE_ROW, wait_s=100, sleep=lambda s: None, clock=lambda: next(ticks)) is None


def test_an_idle_system_starts_a_scheduled_run_immediately(monkeypatch):
    monkeypatch.setattr(runner.db, "count_running", lambda: 0)
    monkeypatch.setattr(runner, "start_run", lambda *a, **k: "run-2")
    assert runner.start_scheduled_run({**SCHEDULE_ROW, "excluded_sections": None}, sleep=lambda s: pytest.fail("no wait")) == "run-2"


# ---- several combinations per run / per fire ----------------------------------------------------------

def test_the_combinations_are_the_product_of_the_four_selections_with_l1_varying_slowest():
    combos = runner.expand_combos(["premium", "nonpremium"], ["men", "women"], ["ASSAM"], ["560029", "400001"])
    assert len(combos) == 8
    assert combos[0] == {"page": "home", "l1": "premium", "l2": "men", "state": "ASSAM", "pincode": "560029"}
    assert combos[1] == {"page": "home", "l1": "premium", "l2": "men", "state": "ASSAM", "pincode": "400001"}
    assert combos[-1] == {"page": "home", "l1": "nonpremium", "l2": "women", "state": "ASSAM", "pincode": "400001"}


def test_repeated_selections_do_not_repeat_a_run():
    assert len(runner.expand_combos(["premium", "premium"], ["men"], ["ASSAM", "ASSAM"], ["1", "1"])) == 1


def test_a_schedule_row_reads_as_its_combinations_whether_stored_as_a_list_or_a_plain_string():
    legacy = {"l1": "premium", "l2": "men", "state": "ASSAM", "pincode": "560029"}
    assert runner.combos_of(legacy) == [{"page": "home", "l1": "premium", "l2": "men", "state": "ASSAM", "pincode": "560029"}]
    stored = {"l1": json.dumps(["premium", "nonpremium"]), "l2": json.dumps(["men"]),
              "state": json.dumps(["ASSAM", "GOA"]), "pincode": json.dumps(["560029"])}
    assert len(runner.combos_of(stored)) == 4
    blank = runner.combos_of({**legacy, "state": None, "pincode": ""})
    assert blank[0]["state"] == runner.DEFAULT_STATE and blank[0]["pincode"] == runner.DEFAULT_PINCODE


def test_schedule_axes_round_trip_through_the_database_as_lists(toasts):
    sid = make_schedule(l1=["premium", "nonpremium"], l2="men", state=["ASSAM", "GOA"], pincode="400001",
                        start_at="2026-10-01T04:30:00+00:00")
    row = db.get_schedule(sid)
    assert db.as_list(row["l1"]) == ["premium", "nonpremium"] and db.as_list(row["l2"]) == ["men"]
    assert db.as_list(row["state"]) == ["ASSAM", "GOA"] and db.as_list(row["pincode"]) == ["400001"]
    assert row["start_at"] == "2026-10-01T04:30:00+00:00"
    db.update_schedule(sid, l2=["men", "women"], pincode="560029")
    row = db.get_schedule(sid)
    assert db.as_list(row["l2"]) == ["men", "women"] and db.as_list(row["pincode"]) == ["560029"]


def _combo(l1="premium"):
    return {"l1": l1, "l2": "men", "state": "ASSAM", "pincode": "560029"}


def test_a_batch_starts_each_combination_only_after_the_previous_run_finished(tmp_path, toasts):
    db.insert_run("r0", str(tmp_path), "hero", None, 3, "premium", "men", 1, None)          # still running
    started = []

    def start_one(combo):
        started.append(combo)
        run_id = f"r{len(started)}"
        db.insert_run(run_id, str(tmp_path), "hero", None, 3, combo["l1"], combo["l2"], 1, None)
        return run_id

    waits = []

    def sleep(_):                       # each wait ends with the run we were waiting on finishing
        running = db.list_runs_with_status("running")
        waits.append(len(running))
        for r in running:
            db.finish_run(r["run_id"], "done")

    rest = [_combo("nonpremium"), _combo("nontransacted")]
    runner._run_rest_of_batch("r0", rest, start_one, 0, sleep)
    assert started == rest
    assert waits == [1, 1]              # never more than one run active while it waited


def test_cancelling_a_run_of_a_batch_stops_the_rest(tmp_path, toasts):
    db.insert_run("r0", str(tmp_path), "hero", None, 3, "premium", "men", 1, None)
    db.finish_run("r0", "cancelled")
    runner._run_rest_of_batch("r0", [_combo("nonpremium")], lambda c: pytest.fail("must not start"), 0, lambda s: None)


def test_a_failed_run_does_not_stop_the_rest_of_the_batch(tmp_path, toasts):
    db.insert_run("r0", str(tmp_path), "hero", None, 3, "premium", "men", 1, None)
    db.finish_run("r0", "failed", "boom")
    started = []
    runner._run_rest_of_batch("r0", [_combo("nonpremium")], lambda c: (started.append(c), "r1")[1], 0, lambda s: None)
    assert started == [_combo("nonpremium")]


def test_run_now_with_one_combination_starts_it_directly_and_queues_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "start_run", lambda *a, **k: (calls.append((a, k)), "run-1")[1])
    run_id, batch_id, queued = runner.start_batch([_combo()], "hero", 5, 2, excluded_sections=["S1"])
    assert (run_id, queued) == ("run-1", 0) and batch_id
    (args, kwargs), = calls
    assert args == ("premium", "men", "hero", 5, 2)
    assert kwargs["pincode"] == "560029" and kwargs["state"] == "ASSAM" and kwargs["batch_id"] == batch_id
    assert kwargs["excluded_sections"] == ["S1"] and kwargs["schedule_id"] is None


def test_a_scheduled_fire_runs_every_combination_sharing_one_batch_id(monkeypatch):
    row = {**SCHEDULE_ROW, "l1": json.dumps(["premium", "nonpremium"]), "l2": json.dumps(["men"]),
           "state": json.dumps(["ASSAM"]), "pincode": json.dumps(["560029", "400001"])}
    seen, told = [], []

    def fake(_row, combo=None, batch_id=None, **kw):
        seen.append((combo, batch_id))
        return f"run-{len(seen)}"

    monkeypatch.setattr(runner, "start_scheduled_run", fake)
    started, skipped = runner.start_scheduled_batch(row, on_started=told.append)
    assert started == ["run-1", "run-2", "run-3", "run-4"] and skipped == 0 and told == started
    assert len({b for _, b in seen}) == 1 and seen[0][1]
    assert [c["l1"] for c, _ in seen] == ["premium", "premium", "nonpremium", "nonpremium"]


def test_a_combination_that_never_gets_a_slot_is_counted_as_skipped_and_the_rest_still_try(monkeypatch):
    row = {**SCHEDULE_ROW, "l1": json.dumps(["premium", "nonpremium"])}
    answers = iter([None, "run-2"])
    monkeypatch.setattr(runner, "start_scheduled_run", lambda *a, **k: next(answers))
    assert runner.start_scheduled_batch(row) == (["run-2"], 1)


def test_deleting_or_disabling_a_schedule_mid_fire_stops_the_remaining_combinations(monkeypatch):
    row = {**SCHEDULE_ROW, "l1": json.dumps(["premium", "nonpremium", "nontransacted"])}
    monkeypatch.setattr(runner, "start_scheduled_run", lambda *a, **k: "run-x")
    wanted = iter([True, False])
    assert runner.start_scheduled_batch(row, still_wanted=lambda: next(wanted)) == (["run-x"], 0)
