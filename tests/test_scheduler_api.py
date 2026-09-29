"""The multi-select surface of the web API: several l1/l2/state/pincode values per run or schedule, the cap on
how many runs that may expand into, the single-value fields older clients still send, and a schedule's start
date-time. Uses a throwaway SQLite file and stubs whatever would start a real run; the FastAPI app is driven
without its startup hooks (no real scheduler thread, no orphan reconciliation)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from web import api, db, runner


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    db.init_db()
    api.scheduler.remove_all_jobs()
    yield TestClient(api.app)
    api.scheduler.remove_all_jobs()


@pytest.fixture
def started(monkeypatch):
    """Replaces runner.start_batch, recording what a Run now asked for."""
    calls = []

    def fake(combos, scope, banner_limit, workers, **kw):
        calls.append({"combos": combos, "scope": scope, "banner_limit": banner_limit, "workers": workers, **kw})
        return "run-1", "batch-1", len(combos) - 1

    monkeypatch.setattr(runner, "start_batch", fake)
    return calls


def schedule_body(**over):
    body = {"name": "s", "interval_minutes": 60, "l1s": ["premium"], "l2s": ["men"]}
    body.update(over)
    return body


# ---- Run now ---------------------------------------------------------------------------------------------

def test_run_now_expands_every_selection_into_combinations(client, started):
    r = client.post("/api/runs", json={"l1s": ["premium", "nonpremium"], "l2s": ["men", "women"],
                                       "states": ["ASSAM", "GOA"], "pincodes": ["560029", "400001"]})
    assert r.status_code == 200
    assert r.json() == {"run_id": "run-1", "batch_id": "batch-1", "combos": 16, "queued": 15}
    assert len(started[0]["combos"]) == 16


def test_run_now_still_accepts_the_single_value_fields_older_clients_send(client, started):
    r = client.post("/api/runs", json={"l1": "premium", "l2": "men", "state": "ASSAM", "pincode": "400001"})
    assert r.status_code == 200 and r.json()["combos"] == 1
    assert started[0]["combos"] == [{"l1": "premium", "l2": "men", "state": "ASSAM", "pincode": "400001"}]


def test_a_blank_state_and_pincode_fall_back_to_the_defaults(client, started):
    r = client.post("/api/runs", json={"l1s": ["premium"], "l2s": ["men"], "states": ["  "], "pincodes": []})
    assert r.status_code == 200
    assert started[0]["combos"] == [{"l1": "premium", "l2": "men", "state": "KARNATAKA", "pincode": "560029"}]


def test_run_now_without_any_state_or_pincode_uses_the_defaults(client, started):
    assert client.post("/api/runs", json={"l1s": ["premium"], "l2s": ["men"]}).status_code == 200
    assert started[0]["combos"][0]["state"] == "KARNATAKA" and started[0]["combos"][0]["pincode"] == "560029"


def test_more_combinations_than_the_cap_are_refused_before_anything_starts(client, started):
    r = client.post("/api/runs", json={"l1s": runner.L1_OPTIONS, "l2s": runner.L2_OPTIONS,
                                       "states": ["ASSAM", "GOA"], "pincodes": ["560029"]})       # 3 x 4 x 2 = 24: fine
    assert r.status_code == 200
    r = client.post("/api/runs", json={"l1s": runner.L1_OPTIONS, "l2s": runner.L2_OPTIONS,
                                       "states": ["ASSAM", "GOA"], "pincodes": ["560029", "400001"]})   # 48
    assert r.status_code == 422 and "48 runs" in r.json()["detail"] and len(started) == 1


@pytest.mark.parametrize("body, needle", [
    ({"l1s": [], "l2s": ["men"]}, "at least one l1"),
    ({"l1s": ["premium"], "l2s": []}, "at least one l2"),
    ({"l1s": ["bogus"], "l2s": ["men"]}, "l1 must be one of"),
    ({"l1s": ["premium"], "l2s": ["men"], "states": ["ATLANTIS"]}, "state must be one of"),
    ({"l1s": ["premium"], "l2s": ["men"], "pincodes": ["56x029"]}, "pincode must be"),
    ({"l1s": ["premium"], "l2s": ["men"], "scope": "everything"}, "scope must be one of"),
])
def test_bad_selections_are_refused_with_a_reason(client, started, body, needle):
    r = client.post("/api/runs", json=body)
    assert r.status_code == 422 and needle in json.dumps(r.json()) and started == []


def test_the_carousel_exclusions_from_the_ui_are_passed_on_by_section_id(client, started):
    client.post("/api/runs", json={"l1s": ["premium"], "l2s": ["men"], "excluded_sections": ["A", " B ", "A", ""]})
    assert started[0]["excluded_sections"] == ["A", "B"]


# ---- schedules -------------------------------------------------------------------------------------------

def test_a_schedule_stores_and_returns_every_selection(client):
    r = client.post("/api/schedules", json=schedule_body(l1s=["premium", "nonpremium"], states=["ASSAM", "TAMIL_NADU"],
                                                         pincodes=["560029", "400001"]))
    assert r.status_code == 200
    s = r.json()
    assert s["l1s"] == ["premium", "nonpremium"] and s["l2s"] == ["men"]
    assert s["states"] == ["ASSAM", "TAMIL_NADU"] and s["pincodes"] == ["560029", "400001"]
    assert s["combo_count"] == 8 and s["run_count"] == 0 and s["unread_alerts"] == 0 and s["latest_run"] is None
    assert "l1" not in s and "state" not in s


def test_a_schedule_saved_before_multi_select_existed_still_reads_and_runs(client):
    with db.connect() as conn:                   # exactly the shape the old code wrote: plain strings
        conn.execute("INSERT INTO schedules (name, interval_minutes, scope, workers, l1, l2, pincode, state, created_at) "
                     "VALUES ('old', 60, 'hero', 3, 'premium', 'women', '400001', 'ASSAM', '2026-09-01T00:00:00+00:00')")
    (s,) = client.get("/api/schedules").json()
    assert (s["l1s"], s["l2s"], s["states"], s["pincodes"]) == (["premium"], ["women"], ["ASSAM"], ["400001"])
    assert s["combo_count"] == 1 and s["start_at"] is None


def test_creating_a_schedule_with_the_single_value_fields_still_works(client):
    r = client.post("/api/schedules", json={"name": "s", "interval_minutes": 30, "l1": "premium", "l2": "men",
                                            "state": "ASSAM", "pincode": "400001"})
    assert r.status_code == 200 and r.json()["states"] == ["ASSAM"] and r.json()["pincodes"] == ["400001"]


def test_a_schedule_over_the_combination_cap_is_refused(client):
    r = client.post("/api/schedules", json=schedule_body(l1s=runner.L1_OPTIONS, l2s=runner.L2_OPTIONS,
                                                         states=["ASSAM", "GOA"], pincodes=["1", "2"]))
    assert r.status_code == 422 and "at most 24" in r.json()["detail"] and client.get("/api/schedules").json() == []


def test_an_edit_that_pushes_a_schedule_over_the_cap_is_refused_and_changes_nothing(client):
    sid = client.post("/api/schedules", json=schedule_body(l1s=runner.L1_OPTIONS, l2s=runner.L2_OPTIONS)).json()["id"]  # 12
    r = client.patch(f"/api/schedules/{sid}", json={"pincodes": ["1", "2", "3"]})                                       # 36
    assert r.status_code == 422
    assert client.get(f"/api/schedules/{sid}").json()["pincodes"] == ["560029"]


def test_editing_one_selection_leaves_the_others_alone(client):
    sid = client.post("/api/schedules", json=schedule_body(states=["ASSAM"], pincodes=["400001"])).json()["id"]
    s = client.patch(f"/api/schedules/{sid}", json={"l1s": ["premium", "nonpremium"]}).json()
    assert s["l1s"] == ["premium", "nonpremium"] and s["states"] == ["ASSAM"] and s["pincodes"] == ["400001"]
    s = client.patch(f"/api/schedules/{sid}", json={"state": "GOA"}).json()             # the single-value name still works
    assert s["states"] == ["GOA"] and s["l1s"] == ["premium", "nonpremium"]


# ---- start date-time -------------------------------------------------------------------------------------

def _job(sid):
    return api.scheduler.get_job(f"schedule-{sid}")


def test_a_start_time_is_stored_as_utc_and_anchors_the_interval(client):
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    ist = start.astimezone(timezone(timedelta(hours=5, minutes=30)))
    s = client.post("/api/schedules", json=schedule_body(start_at=ist.isoformat())).json()
    assert datetime.fromisoformat(s["start_at"]) == start and s["start_at"].endswith("+00:00")
    assert _job(s["id"]).trigger.start_date == start


def test_a_start_time_that_has_passed_keeps_its_place_in_the_interval(client):
    long_ago = (datetime.now(timezone.utc) - timedelta(days=3)).replace(microsecond=0)
    s = client.post("/api/schedules", json=schedule_body(interval_minutes=120, start_at=long_ago.isoformat())).json()
    trigger = _job(s["id"]).trigger
    now = datetime.now(timezone.utc)
    nxt = trigger.get_next_fire_time(None, now)
    assert now <= nxt <= now + timedelta(hours=2)
    assert (nxt - long_ago) % timedelta(hours=2) == timedelta(0)        # lands on start + k x interval


def test_a_slot_missed_while_the_server_was_off_is_never_run_late(client):
    """The server (and the PC) being off across a scheduled time: on the next start every job is rebuilt from
    the database (_startup -> _sync_job), and a rebuilt job's first fire is always in the future - the missed
    slot is dropped, the schedule carries on with its next one."""
    slot = (datetime.now(timezone.utc) - timedelta(hours=7, minutes=13)).replace(microsecond=0)   # 3 slots ago
    sid = client.post("/api/schedules", json=schedule_body(interval_minutes=120, start_at=slot.isoformat())).json()["id"]
    api.scheduler.remove_all_jobs()                                   # "the server stopped"
    api._sync_job(db.get_schedule(sid))                               # "the server started again"
    now = datetime.now(timezone.utc)
    nxt = _job(sid).trigger.get_next_fire_time(None, now)
    assert nxt > now
    assert nxt - now <= timedelta(hours=2)                            # the very next slot, not a backlog
    assert (nxt - slot) % timedelta(hours=2) == timedelta(0)          # still on the schedule's own grid


def test_no_start_time_counts_the_interval_from_when_it_is_saved(client):
    s = client.post("/api/schedules", json=schedule_body()).json()
    assert s["start_at"] is None


def test_a_start_time_can_be_changed_and_cleared_by_an_edit(client):
    sid = client.post("/api/schedules", json=schedule_body(start_at="2030-01-01T10:00:00+00:00")).json()["id"]
    assert client.patch(f"/api/schedules/{sid}", json={"start_at": "2031-02-03T04:05:00+00:00"}).json()["start_at"] \
        == "2031-02-03T04:05:00+00:00"
    assert client.patch(f"/api/schedules/{sid}", json={"name": "renamed"}).json()["start_at"] == "2031-02-03T04:05:00+00:00"
    assert client.patch(f"/api/schedules/{sid}", json={"start_at": None}).json()["start_at"] is None


def test_a_start_time_without_a_timezone_means_this_machines_local_time(client):
    s = client.post("/api/schedules", json=schedule_body(start_at="2030-06-01T09:30:00")).json()
    assert datetime.fromisoformat(s["start_at"]) == datetime(2030, 6, 1, 9, 30).astimezone(timezone.utc)


def test_a_start_time_that_is_not_a_date_is_refused(client):
    r = client.post("/api/schedules", json=schedule_body(start_at="next tuesday"))
    assert r.status_code == 422 and "ISO date-time" in r.json()["detail"]


# ---- a schedule's runs -----------------------------------------------------------------------------------

def test_a_schedules_run_now_starts_all_its_combinations(client, started):
    sid = client.post("/api/schedules", json=schedule_body(l1s=["premium", "nonpremium"])).json()["id"]
    r = client.post(f"/api/schedules/{sid}/run-now")
    assert r.json() == {"run_id": "run-1", "batch_id": "batch-1", "combos": 2, "queued": 1}
    assert started[0]["schedule_id"] == sid and [c["l1"] for c in started[0]["combos"]] == ["premium", "nonpremium"]


def test_runs_can_be_listed_per_schedule_with_their_batch(client, tmp_path):
    a = client.post("/api/schedules", json=schedule_body(name="a")).json()["id"]
    b = client.post("/api/schedules", json=schedule_body(name="b")).json()["id"]
    for run_id, sid, batch in (("r1", a, "fire-1"), ("r2", a, "fire-1"), ("r3", b, "fire-2"), ("r4", None, None)):
        db.insert_run(run_id, str(tmp_path / run_id), "hero", None, 3, "premium", "men", 1, sid, batch_id=batch)
    rows = client.get(f"/api/runs?schedule_id={a}").json()
    assert sorted(r["run_id"] for r in rows) == ["r1", "r2"] and {r["batch_id"] for r in rows} == {"fire-1"}
    assert len(client.get("/api/runs").json()) == 4
    stats = {s["id"]: s for s in client.get("/api/schedules").json()}
    assert stats[a]["run_count"] == 2 and stats[b]["run_count"] == 1
    assert stats[a]["latest_run"]["run_id"] in ("r1", "r2")


def test_a_schedules_unread_alerts_are_counted(client):
    sid = client.post("/api/schedules", json=schedule_body()).json()["id"]
    first = db.insert_alert("regression", "t", "m", schedule_id=sid)
    db.insert_alert("regression", "t2", "m", schedule_id=sid)
    db.mark_alert_read(first)
    assert client.get(f"/api/schedules/{sid}").json()["unread_alerts"] == 1


def test_an_alert_can_be_removed_and_all_alerts_can_be_cleared(client):
    a = client.post("/api/schedules", json=schedule_body(name="a")).json()["id"]
    b = client.post("/api/schedules", json=schedule_body(name="b")).json()["id"]
    first = db.insert_alert("regression", "t1", "m", schedule_id=a)
    db.insert_alert("regression", "t2", "m", schedule_id=a)
    db.insert_alert("regression", "t3", "m", schedule_id=b)
    assert client.delete(f"/api/alerts/{first}").json() == {"unread": 2}
    assert client.delete(f"/api/alerts/{first}").status_code == 200              # already gone: still fine
    assert client.delete(f"/api/alerts?schedule_id={a}").json() == {"removed": 1, "unread": 1}
    assert [x["title"] for x in client.get("/api/alerts").json()["items"]] == ["t3"]     # the other schedule's kept
    assert client.delete("/api/alerts").json() == {"removed": 1, "unread": 0}
    assert client.get("/api/alerts").json()["items"] == []


def test_meta_tells_the_ui_the_cap_and_the_defaults(client):
    m = client.get("/api/meta").json()
    assert m["max_combos"] == runner.MAX_COMBOS and m["default_state"] == "KARNATAKA" and m["default_pincode"] == "560029"
