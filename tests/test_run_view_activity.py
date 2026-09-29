"""How the API turns a run folder's activity.jsonl into the live "what is it doing" tag on a banner card
(web/runner.run_view + current_activity). Builds a small run folder on disk; nothing is started."""
import json
import time

from qa import feed_verify as fv
from qa.feed_client import Banner
from web import runner


def make_banner(bid, idx):
    return Banner(bid, "https://cdn/x.webp", idx, f"https://ajio.com/s/{bid}-1", "hybrid-dynamic-banner", bid, 0, idx,
                  hotspots=[], alt_text=bid)


def run_dir(tmp_path, ids=("a", "b", "c")):
    fv.save_banners(tmp_path, [make_banner(b, i) for i, b in enumerate(ids)])
    return tmp_path


def rows(tmp_path):
    return {r["banner_id"]: r for r in runner.run_view(tmp_path, "hero", None, is_live=True)}


def test_a_banner_a_worker_has_reported_on_is_processing_even_before_its_image_is_on_disk(tmp_path):
    run_dir(tmp_path)
    fv.ActivityLog(tmp_path / "activity.jsonl")("a", "Downloading")
    r = rows(tmp_path)
    assert (r["a"]["result"], r["a"]["activity"]) == ("PROCESSING", "Downloading")
    assert r["b"]["result"] == "PENDING" and "activity" not in r["b"]         # untouched banners just wait


def test_the_latest_step_wins(tmp_path):
    run_dir(tmp_path)
    log = fv.ActivityLog(tmp_path / "activity.jsonl")
    for step in ("Downloading", "Listing lookup", "Reading image"):
        log("a", step)
    assert rows(tmp_path)["a"]["activity"] == "Reading image"


def test_a_banner_without_any_activity_line_still_shows_something(tmp_path):
    run_dir(tmp_path)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.webp").write_bytes(b"x")                       # started, but by an older run with no activity file
    assert rows(tmp_path)["a"]["activity"] == "Working"


def test_a_failed_banner_cools_down_then_waits_for_a_worker(tmp_path):
    run_dir(tmp_path)
    fail = {"banner_id": "a", "alt_text": "a", "destination_raw": "", "image_url": "", "result": "INCONCLUSIVE",
            "reason": "vision_failed: RuntimeError: 500", "attempts": 1}
    (tmp_path / "partial.jsonl").write_text(json.dumps(fail) + "\n", encoding="utf-8")
    log = fv.ActivityLog(tmp_path / "activity.jsonl")
    log("a", "Cooling down", until=time.time() + 600)
    r = rows(tmp_path)["a"]
    assert (r["result"], r["try_number"], r["activity"]) == ("PROCESSING", 2, "Cooling down")
    log("a", "Cooling down", until=time.time() - 1)                          # the cooldown is over; nobody has picked it up yet
    assert rows(tmp_path)["a"]["activity"] == "Retry queued"


def test_a_finished_banner_carries_no_activity(tmp_path):
    run_dir(tmp_path)
    done = {"banner_id": "a", "alt_text": "a", "destination_raw": "", "image_url": "", "result": "PASS", "reason": "", "attempts": 1}
    (tmp_path / "partial.jsonl").write_text(json.dumps(done) + "\n", encoding="utf-8")
    fv.ActivityLog(tmp_path / "activity.jsonl")("a", "Comparing")             # the last thing it did before finishing
    assert "activity" not in rows(tmp_path)["a"]


def test_a_run_that_is_no_longer_live_shows_no_activity(tmp_path):
    run_dir(tmp_path)
    fv.ActivityLog(tmp_path / "activity.jsonl")("a", "Reading image")
    stopped = {r["banner_id"]: r for r in runner.run_view(tmp_path, "hero", None, is_live=False)}
    assert stopped["a"]["result"] == "INCONCLUSIVE" and "activity" not in stopped["a"]
