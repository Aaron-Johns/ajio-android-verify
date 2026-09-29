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


# ---- UNAVAILABLE in the run view, the run summary and the alert diff ----

def write_results(tmp_path, *recs):
    (tmp_path / "results.json").write_text(json.dumps(list(recs)), encoding="utf-8")


def res(bid, result, reason="", attempts=1, **extra):
    return {"banner_id": bid, "result": result, "reason": reason, "alt_text": bid, "destination_raw": "x", "image_url": "u",
            "attempts": attempts, **extra}


def test_a_banner_that_used_up_its_retries_on_a_server_error_is_unavailable_and_offers_a_retry(tmp_path):
    run_dir(tmp_path)
    write_results(tmp_path, res("a", "INCONCLUSIVE", "listing_fetch_failed: HTTP 400", attempts=runner.MAX_TRIES),
                  res("b", "INCONCLUSIVE", "empty_bounding_box_after_scaling"), res("c", "PASS"))
    got = {r["banner_id"]: r for r in runner.run_view(tmp_path, "hero", None, is_live=False)}
    assert got["a"]["result"] == "UNAVAILABLE" and got["a"]["retries_exhausted"] is True
    assert "gave up after" in got["a"]["reason"]
    assert got["b"]["result"] == "INCONCLUSIVE" and got["c"]["result"] == "PASS"


def test_a_stopped_run_shows_its_half_retried_banner_as_unavailable_too(tmp_path):
    run_dir(tmp_path)
    write_results(tmp_path, res("a", "INCONCLUSIVE", "image_download_failed: error: ConnectionError", attempts=2))
    got = {r["banner_id"]: r for r in runner.run_view(tmp_path, "hero", None, is_live=False)}
    assert got["a"]["result"] == "UNAVAILABLE" and got["a"]["retries_exhausted"] is True


def test_still_retrying_in_a_live_run_stays_processing(tmp_path):
    run_dir(tmp_path)
    write_results(tmp_path, res("a", "INCONCLUSIVE", "listing_fetch_failed: x", attempts=2))
    got = {r["banner_id"]: r for r in runner.run_view(tmp_path, "hero", None, is_live=True)}
    assert got["a"]["result"] == "PROCESSING"


def test_load_shown_results_relabels_without_touching_the_raw_records(tmp_path):
    write_results(tmp_path, res("a", "INCONCLUSIVE", "vision_failed: 503", attempts=5), res("b", "FAIL"))
    shown = runner.load_shown_results(tmp_path)
    assert (shown["a"]["result"], shown["b"]["result"]) == ("UNAVAILABLE", "FAIL")
    assert runner.load_results(tmp_path)["a"]["result"] == "INCONCLUSIVE"        # the retry logic still reads the raw one
    assert runner.load_shown_results(tmp_path, is_live=True)["a"]["result"] == "UNAVAILABLE"   # 5 tries = spent, even live
