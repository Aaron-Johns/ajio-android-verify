"""Emulator-free banner verification: banner image + listing from the server, with everything faked."""
import csv
import dataclasses
import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image as PILImage

from qa import feed_verify as fv
from qa import listing_client as lc
from qa.banner_cache import BannerCache
from qa.compare import AliasMap
from qa.feed_client import Banner, Hotspot
from qa.spotcheck import hero, vision

ALIASES = AliasMap([["LEVI'S", "LEVIS"]])


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(fv.time, "sleep", lambda s: None)
    monkeypatch.setattr(hero.time, "sleep", lambda s: None)


def banner(bid, dest, idx=0, image="https://cdn/x.webp", section="hybrid-dynamic-banner", block=True,
          hotspots=None, image_width=None, image_height=None):
    return Banner(bid, image, idx, dest, section, bid, 0, idx if block else None, hotspots=hotspots or [],
                 image_width=image_width, image_height=image_height, alt_text=bid)


def hotspot(url, x=0, y=0, w=50, h=50, alt=""):
    return Hotspot(url=url, x=x, y=y, width=w, height=h, alt=alt)


def real_image(url, w=200, h=100):
    """A real, PIL-openable image (unlike ok_image's fake bytes) - needed for hotspot tests, which
    actually crop the downloaded image."""
    buf = io.BytesIO()
    PILImage.new("RGB", (w, h), "blue").save(buf, format="PNG")
    return "OK", buf.getvalue(), "image/png"


def listing(title="Min 40 Percent Off", brands=None, genders=None):
    return lc.Listing("slug", title, 1000, 0, brands if brands is not None else {"NEW BALANCE": 5, "Under Armour": 9},
                      genders or {}, {"Brands": 2}, [])


INFO = {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}


def ok_image(url):
    return "OK", b"imagebytes", "image/webp"


class Fetcher:
    def __init__(self, table):
        self.table, self.calls, self.sorted_calls = table, [], []

    def __call__(self, slug, kind="curated", **k):
        (self.sorted_calls if k.get("sort") else self.calls).append((slug, kind))   # calls = the plain listing fetches
        value = self.table[slug]
        if isinstance(value, Exception):
            raise value
        return value


def run(banners, tmp_path, fetcher, analyzer=lambda p: INFO, image_fetcher=ok_image, retry_rounds=0, **kw):
    # cache=None unless a test opts in explicitly - run_feed_verify defaults to the real, on-disk,
    # cross-run cache (qa/.cache/banner_image_cache.json), which would make tests read/write shared
    # state on the developer's machine and could cross-contaminate between test cases
    kw.setdefault("cache", None)
    return fv.run_feed_verify(banners, tmp_path, analyzer=analyzer, aliases=ALIASES, listing_fetcher=fetcher,
                              image_fetcher=image_fetcher, workers=1, retry_rounds=retry_rounds, retry_pause=0, **kw)


def test_pass_fail_and_skipped_are_told_apart(tmp_path):
    feed = [banner("good", "https://ajio.com/s/min40-1", 0),
            banner("bad", "https://ajio.com/s/min40-2", 1),
            banner("store", "https://www.ajio.com/shop/ethnicwear", 2)]
    fetcher = Fetcher({"min40-1": listing(), "min40-2": listing(brands={"NEW BALANCE": 5})})
    results = run(feed, tmp_path, fetcher, analyzer=lambda p: INFO)
    by = {r["banner_id"]: r for r in results}
    assert by["good"]["result"] == "PASS" and by["good"]["banner_check"]["source"] == "server"
    assert by["bad"]["result"] == "FAIL" and by["bad"]["banner_check"]["missing_brands"] == ["under armour"]
    assert by["store"]["result"] == "SKIPPED" and by["store"]["reason"] == "not_a_listing_link"
    assert Path(by["good"]["image_file"]).read_bytes() == b"imagebytes"


def test_a_banner_without_an_image_is_skipped_not_guessed(tmp_path):
    r = fv.verify_banner(banner("noimg", "https://ajio.com/s/a-1", image=None), tmp_path, lambda p: INFO,
                         fv.ListingCache(Fetcher({"a-1": listing()})), ALIASES, ok_image)
    assert r["result"] == "SKIPPED" and r["reason"] == "banner_has_no_image"


def test_a_hidden_banner_is_skipped_before_any_image_download_or_listing_fetch(tmp_path):
    hidden = dataclasses.replace(banner("hid", "https://ajio.com/s/x-1"), hidden=True, hidden_reason="outside_schedule")
    image_calls = []
    def image_fetcher(url):
        image_calls.append(url)
        return ok_image(url)
    fetcher = Fetcher({"x-1": listing()})
    r = run([hidden], tmp_path, fetcher, image_fetcher=image_fetcher)[0]
    assert r["result"] == "SKIPPED" and r["reason"] == "hidden: outside_schedule"
    assert image_calls == [] and fetcher.calls == []


def test_select_banners_still_includes_hidden_ones_so_the_ui_toggle_can_show_them(tmp_path):
    hidden = dataclasses.replace(banner("hid", "https://ajio.com/s/x-1"), hidden=True, hidden_reason="outside_schedule")
    visible = banner("vis", "https://ajio.com/s/x-2", 1)
    assert [b.banner_id for b in fv.select_banners([hidden, visible], "hero")] == ["hid", "vis"]


def test_all_scope_keeps_a_banner_whose_only_checkable_link_is_a_hotspot(tmp_path):
    # own destination is a non-listing /shop/ page (or missing entirely) - not checkable itself -
    # but each hotspot points to a real /s/ listing, so the banner is still fully checkable overall
    # and must not be dropped before it ever reaches verification.
    gallery = banner("gallery", "https://www.ajio.com/shop/wallet-offer", 0,
                     hotspots=[hotspot("https://ajio.com/s/min40-1"), hotspot("https://ajio.com/s/min50-2")])
    no_own_link = banner("noown", None, 1, hotspots=[hotspot("https://ajio.com/s/min60-3")])
    uncheckable = banner("dead", "https://www.ajio.com/shop/dead-end", 2)
    assert [b.banner_id for b in fv.select_banners([gallery, no_own_link, uncheckable], "all")] == ["gallery", "noown"]

    fetcher = Fetcher({"min40-1": listing(), "min50-2": listing()})
    r = run([gallery], tmp_path, fetcher, scope="all", image_fetcher=real_image)[0]
    # own link isn't a listing link (excluded from the overall verdict, not counted as a failure) -
    # but both hotspots were actually verified, so the banner is a real PASS overall, not skipped.
    assert r["result"] == "PASS"
    assert [h["result"] for h in r["hotspot_checks"]] == ["PASS", "PASS"]


def test_deal_that_does_not_match_the_title_fails(tmp_path):
    r = run([banner("a", "https://ajio.com/s/x-1")], tmp_path, Fetcher({"x-1": listing(title="Min 70 Percent Off")}))[0]
    assert r["result"] == "FAIL" and r["banner_check"]["title_matches_deal"] is False


def test_a_banner_targeting_the_wrong_audience_fails(tmp_path):
    info = {**INFO, "target_gender": "women"}
    r = run([banner("a", "https://ajio.com/s/x-1")], tmp_path, Fetcher({"x-1": listing(genders={"Men": 50})}),
           analyzer=lambda p: info)[0]
    assert r["result"] == "FAIL" and r["banner_check"]["gender_matches"] is False


def test_category_links_use_the_category_endpoint_and_one_fetch_per_link(tmp_path):
    feed = [banner("a", "https://www.ajio.com/c/clearance-store-1", 0), banner("b", "https://www.ajio.com/c/clearance-store-1", 1)]
    fetcher = Fetcher({"clearance-store-1": listing(title="Clearance Store")})
    run(feed, tmp_path, fetcher, analyzer=lambda p: {"brands_mentioned": [], "deal_offered": "FLAT 70% OFF"})
    assert fetcher.calls == [("clearance-store-1", "category")]


def test_failures_are_inconclusive_never_a_pass(tmp_path):
    feed = [banner("img", "https://ajio.com/s/a-1", 0), banner("list", "https://ajio.com/s/b-2", 1),
            banner("gone", "https://ajio.com/s/a-1", 2), banner("boom", "https://ajio.com/s/a-1", 3)]
    fetcher = Fetcher({"a-1": listing(), "b-2": RuntimeError("404")})

    def images(url):
        return ("HTTP 503", None, "") if images.n.pop(0) == "fail" else ok_image(url)
    images.n = ["fail", "ok", "ok", "ok"]

    def analyzer(path):
        if "gone" in path.name:
            raise vision.VisionUnavailable("no key")
        if "boom" in path.name:
            raise ValueError("model exploded")
        return INFO
    results = run(feed, tmp_path, fetcher, analyzer=analyzer, image_fetcher=images)
    by = {r["banner_id"]: r for r in results}
    assert by["img"]["reason"].startswith("image_download_failed") and by["list"]["reason"].startswith("listing_fetch_failed")
    assert by["gone"]["reason"].startswith("vision_unavailable") and by["boom"]["reason"].startswith("vision_failed")
    assert all(r["result"] == "INCONCLUSIVE" for r in results)


def test_scope_and_limit(tmp_path):
    feed = [banner("hero1", "https://ajio.com/s/a-1", 0), banner("hero2", "https://ajio.com/s/a-2", 1),
            banner("flat", "https://ajio.com/s/a-3", 2, section="hybrid-banner", block=False)]
    assert [b.banner_id for b in fv.select_banners(feed, "hero")] == ["hero1", "hero2"]
    assert [b.banner_id for b in fv.select_banners(feed, "all")] == ["hero1", "hero2", "flat"]
    fetcher = Fetcher({"a-1": listing(), "a-2": listing(), "a-3": listing()})
    assert len(run(feed, tmp_path, fetcher, limit=1)) == 1


def test_outputs_and_summary(tmp_path):
    feed = [banner("good", "https://ajio.com/s/a-1", 0), banner("bad", "https://ajio.com/s/a-2", 1)]

    def analyzer(p):  # a partial match for "good", a recognized-but-missing brand for "bad"
        deal = {"deal_offered": "MIN. 40% OFF*", "target_gender": "men_and_women"}
        return {**deal, "brands_mentioned": ["kiana"]} if "good" in str(p) else {**deal, "brands_mentioned": ["Guess"]}

    fetcher = Fetcher({"a-1": listing(brands={"Kiana House Of Fashion": 3}), "a-2": listing(brands={})})
    results = run(feed, tmp_path, fetcher, analyzer=analyzer)
    fv.write_outputs(results, tmp_path)
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))[0]["banner_id"] == "good"
    with open(tmp_path / "results.csv", encoding="utf-8-sig") as f:
        rows = {r["banner_id"]: r for r in csv.DictReader(f)}
    assert rows["good"]["result"] == "PASS" and rows["good"]["partial_matches"] == "kiana->Kiana House Of Fashion"
    assert rows["bad"]["result"] == "FAIL" and rows["bad"]["missing_brands"] == "Guess"
    text = fv.format_summary(results)
    assert "PASS=1" in text and "FAIL=1" in text and "missing brands ['Guess']" in text


# ---------------- failed banners are held back and retried after the rest ----------------

def test_a_banner_that_fails_on_a_temporary_error_is_retried_and_ends_up_verified(tmp_path):
    feed = [banner("steady", "https://ajio.com/s/a-1", 0), banner("flaky", "https://ajio.com/s/a-1", 1),
            banner("last", "https://ajio.com/s/a-1", 2)]
    calls = {"flaky": 0}

    def analyzer(path):
        if "flaky" in path.name:
            calls["flaky"] += 1
            if calls["flaky"] <= 3:          # first pass: all 3 inner attempts fail (a Google outage)
                raise RuntimeError("500 Internal Server Error")
        return INFO
    results = run(feed, tmp_path, Fetcher({"a-1": listing()}), analyzer=analyzer, retry_rounds=2)
    assert [r["banner_id"] for r in results] == ["steady", "flaky", "last"]          # order preserved
    assert [r["result"] for r in results] == ["PASS", "PASS", "PASS"]
    assert [r["attempts"] for r in results] == [1, 2, 1]
    assert results[1]["recovered_in_retry_round"] == 1 and "recovered_in_retry_round" not in results[0]


def _queue_run(tmp_path, banners, analyzer, workers, retry_pause=0.0, retry_rounds=4):
    """run_feed_verify with the (banner_id, attempts) of every result recorded in the order they arrive."""
    seen, lock = [], threading.Lock()

    def on_result(r):
        with lock:
            seen.append((r["banner_id"], r["attempts"]))
    fv.run_feed_verify(banners, tmp_path, analyzer=analyzer, aliases=ALIASES, listing_fetcher=Fetcher({"a-1": listing()}),
                       image_fetcher=ok_image, workers=workers, retry_rounds=retry_rounds, retry_pause=retry_pause,
                       cache=None, on_result=on_result)
    return seen


def _fails_first_pass(name, times=3):
    """An analyzer that fails the first `times` calls for the banner `name` (one whole first try) and passes otherwise."""
    calls = {"n": 0}

    def analyzer(path):
        if name in path.name:
            calls["n"] += 1
            if calls["n"] <= times:
                raise RuntimeError("500 Internal Server Error")
        return INFO
    return analyzer


def _seven(prefix="b"):
    return [banner(f"{prefix}{n}", "https://ajio.com/s/a-1", n) for n in range(1, 8)]


def test_a_failed_banner_goes_back_in_the_queue_at_once_ahead_of_banners_not_yet_tried(tmp_path):
    # one worker: b1 fails, so its retry must come before b2..b7 - not after the whole first pass
    seen = _queue_run(tmp_path, _seven(), _fails_first_pass("b1"), workers=1)
    assert seen == [("b1", 1), ("b1", 2), ("b2", 1), ("b3", 1), ("b4", 1), ("b5", 1), ("b6", 1), ("b7", 1)]


def test_seven_banners_three_workers_the_failed_one_takes_the_next_free_worker(tmp_path):
    """The scenario: 1,2,3 running and 4-7 waiting; 1 fails while 2 and 3 are still busy. The worker that
    freed up retries 1 straight away, then carries on with 4,5,6,7 - it doesn't wait for 2 and 3, and it doesn't
    leave 1 until everything else has had its first try."""
    release = threading.Event()
    base = _fails_first_pass("b1")

    def analyzer(path):
        if "b2" in path.name or "b3" in path.name:
            assert release.wait(20), "b2/b3 were never released"
        if "b7" in path.name:
            release.set()                        # by now b1's worker has got through 1(retry), 4, 5, 6, 7
        return base(path)
    seen = _queue_run(tmp_path, _seven(), analyzer, workers=3)
    assert seen[:6] == [("b1", 1), ("b1", 2), ("b4", 1), ("b5", 1), ("b6", 1), ("b7", 1)]
    assert sorted(seen[6:]) == [("b2", 1), ("b3", 1)]


def test_a_failed_banner_cooling_down_never_holds_a_worker(tmp_path):
    """With a cooldown, the free worker carries on with untried banners until the failed one is due again -
    and takes it as soon as it is, ahead of the banners still untried."""
    base = _fails_first_pass("b1")
    step = threading.Event()

    def analyzer(path):
        if any(n in path.name for n in ("b2", "b3")):
            step.wait(0.3)                       # (not time.sleep: the suite stubs that out)
        return base(path)
    seen = _queue_run(tmp_path, _seven(), analyzer, workers=1, retry_pause=0.5)
    order = [s for s in seen]
    assert order[0] == ("b1", 1)
    assert order.index(("b2", 1)) < order.index(("b1", 2)) and order.index(("b3", 1)) < order.index(("b1", 2))
    assert order.index(("b1", 2)) < order.index(("b4", 1))       # due at 0.5 s: back before the untried b4..b7


def test_persistent_failures_give_up_after_the_last_try_without_hanging_several_workers(tmp_path):
    outcome = {}

    def go():
        outcome["seen"] = _queue_run(tmp_path, _seven()[:4], lambda p: (_ for _ in ()).throw(RuntimeError("500")),
                                     workers=3, retry_rounds=2)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive(), "the queue never finished"
    assert sorted(outcome["seen"]) == sorted((f"b{n}", tries) for n in range(1, 5) for tries in (1, 2, 3))


def test_a_listing_outage_is_retried_too_because_failures_are_not_cached(tmp_path):
    state = {"n": 0}

    def fetcher(slug, kind="curated", **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("503")
        return listing()
    r = run([banner("a", "https://ajio.com/s/a-1")], tmp_path, fetcher, retry_rounds=1)[0]
    assert r["result"] == "PASS" and r["attempts"] == 2


def test_a_persistent_failure_stays_inconclusive_after_all_rounds(tmp_path):
    r = run([banner("a", "https://ajio.com/s/a-1")], tmp_path, Fetcher({"a-1": listing()}),
            analyzer=lambda p: (_ for _ in ()).throw(RuntimeError("500")), retry_rounds=3)[0]
    assert r["result"] == "INCONCLUSIVE" and r["reason"].startswith("vision_failed") and r["attempts"] == 4


def test_a_missing_api_key_is_not_retried(tmp_path):
    calls = []

    def analyzer(path):
        calls.append(1)
        raise vision.VisionUnavailable("no key")
    r = run([banner("a", "https://ajio.com/s/a-1")], tmp_path, Fetcher({"a-1": listing()}), analyzer=analyzer, retry_rounds=3)[0]
    assert r["reason"].startswith("vision_unavailable") and r["attempts"] == 1 and len(calls) == 1


def test_a_real_fail_verdict_is_final_not_retried(tmp_path):
    r = run([banner("a", "https://ajio.com/s/a-1")], tmp_path, Fetcher({"a-1": listing(brands={})}), retry_rounds=3)[0]
    assert r["result"] == "FAIL" and r["attempts"] == 1


def test_images_are_downloaded_once_even_across_retries(tmp_path):
    downloads = []

    def images(url):
        downloads.append(url)
        return ok_image(url)
    calls = {"n": 0}

    def analyzer(path):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("500")
        return INFO
    r = run([banner("a", "https://ajio.com/s/a-1")], tmp_path, Fetcher({"a-1": listing()}), analyzer=analyzer,
            image_fetcher=images, retry_rounds=1)[0]
    assert r["result"] == "PASS" and len(downloads) == 1


# ---------------- results are saved as they arrive; an interrupted run can be resumed ----------------

def test_each_result_is_saved_the_moment_it_is_known_and_a_torn_line_is_ignored(tmp_path):
    seen = []
    feed = [banner("a", "https://ajio.com/s/a-1", 0), banner("b", "https://ajio.com/s/a-1", 1)]
    log = fv.PartialLog(tmp_path / "partial.jsonl")
    run(feed, tmp_path, Fetcher({"a-1": listing()}), on_result=lambda r: (seen.append(r["banner_id"]), log(r)))
    assert sorted(seen) == ["a", "b"]
    with open(tmp_path / "partial.jsonl", "a", encoding="utf-8") as f:
        f.write('{"banner_id": "c", "resu')            # process killed mid-write
    saved = fv.load_partial(tmp_path)
    assert set(saved) == {"a", "b"} and saved["a"]["result"] == "PASS"


def test_resume_redoes_only_missing_and_temporarily_failed_banners(tmp_path):
    feed = [banner("ok", "https://ajio.com/s/a-1", 0), banner("real_fail", "https://ajio.com/s/a-2", 1),
            banner("flaky", "https://ajio.com/s/a-1", 2), banner("never_ran", "https://ajio.com/s/a-1", 3)]
    fetcher = Fetcher({"a-1": listing(), "a-2": listing(brands={})})
    flaky_down = {"on": True}

    def analyzer(path):
        analyzed.append(path.stem)
        if "flaky" in path.stem and flaky_down["on"]:
            raise RuntimeError("500")
        return INFO
    analyzed = []
    log = fv.PartialLog(tmp_path / "partial.jsonl")
    # first run: interrupted after 3 banners (never_ran is missing), flaky failed and no retry rounds were left
    first = run(feed[:3], tmp_path, fetcher, analyzer=analyzer, on_result=log)
    assert [r["result"] for r in first] == ["PASS", "FAIL", "INCONCLUSIVE"]

    analyzed.clear()
    flaky_down["on"] = False
    results = run(feed, tmp_path, fetcher, analyzer=analyzer, previous=fv.load_partial(tmp_path), on_result=log)
    assert [r["banner_id"] for r in results] == ["ok", "real_fail", "flaky", "never_ran"]          # order preserved
    assert [r["result"] for r in results] == ["PASS", "FAIL", "PASS", "PASS"]
    assert sorted(analyzed) == ["flaky", "never_ran"]              # finished banners, including a real FAIL, were not redone
    assert results[2]["attempts"] == 2 and results[3]["attempts"] == 1 and results[1]["attempts"] == 1
    assert fv.load_partial(tmp_path)["flaky"]["result"] == "PASS"  # later saved result replaces the failed one


def test_only_restricts_a_resume_to_one_banner_leaving_the_rest_untouched(tmp_path):
    # the web UI's per-banner "Retry" button: redo exactly one banner from an otherwise-finished
    # run, without disturbing anyone else's already-saved result (even another retryable one).
    feed = [banner("a", "https://ajio.com/s/a-1", 0), banner("flaky_b", "https://ajio.com/s/a-1", 1),
            banner("flaky_c", "https://ajio.com/s/a-1", 2)]
    fetcher = Fetcher({"a-1": RuntimeError("500")})
    previous = {"a": {"banner_id": "a", "result": "PASS", "attempts": 1},
               "flaky_b": {"banner_id": "flaky_b", "result": "INCONCLUSIVE",
                          "reason": "listing_fetch_failed: boom", "attempts": 1},
               "flaky_c": {"banner_id": "flaky_c", "result": "INCONCLUSIVE",
                          "reason": "listing_fetch_failed: boom", "attempts": 1}}
    results = run(feed, tmp_path, fetcher, previous=previous, only={"flaky_b"})
    by = {r["banner_id"]: r for r in results}
    assert fetcher.calls == [("a-1", "curated")]              # only the requested banner's link was ever refetched
    assert by["a"] == previous["a"]                           # untouched, not even a retryable one
    assert by["flaky_c"] == previous["flaky_c"]                # also retryable, but not the one asked for
    assert by["flaky_b"]["result"] == "INCONCLUSIVE" and by["flaky_b"]["attempts"] == 2   # still fails (fetcher always 500s), attempt counted


def test_banner_list_round_trips_so_a_resume_needs_no_live_feed(tmp_path):
    feed = [banner("a", "https://ajio.com/s/a-1", 0), banner("b", "https://www.ajio.com/c/x-2", 1)]
    fv.save_banners(tmp_path, feed)
    assert fv.load_banners(tmp_path) == feed


def test_a_failed_banner_waits_out_the_same_cooldown_before_every_retry(tmp_path):
    # a real (tiny) cooldown rather than a stubbed sleep: the queue waits on a condition, not time.sleep
    stamps = []
    fv.run_feed_verify([banner("a", "https://ajio.com/s/a-1")], tmp_path, analyzer=lambda p: (_ for _ in ()).throw(RuntimeError("500")),
                       aliases=ALIASES, listing_fetcher=Fetcher({"a-1": listing()}), image_fetcher=ok_image, workers=1,
                       retry_rounds=3, retry_pause=0.2, cache=None, on_result=lambda r: stamps.append(time.monotonic()))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 4 and all(g >= 0.19 for g in gaps), gaps        # 1 try + 3 retries, each after its cooldown


# ---- fetch_confirmed_banners: FINDINGS 6.10's premium/standard split is per-request, so the only ----
# ---- lever is re-fetching the feed until the wanted asset_set shows up ------------------------------

def st_pull(*a, **k):
    return [banner("a", "https://ajio.com/s/a-1", image="https://cdn/x-UHP-ST-MB-1.jpeg")]


def pr_pull(*a, **k):
    return [banner("a", "https://ajio.com/s/a-1", image="https://cdn/x-UHP-PR-MB-1.jpeg")]


def test_no_target_asset_set_fetches_exactly_once():
    banners, attempts, confirmed = fv.fetch_confirmed_banners(target_asset_set=None, fetcher=st_pull, sleep=lambda s: None)
    assert attempts == 1 and confirmed is True
    assert banners[0].banner_id == "a"


def test_returns_immediately_once_the_target_set_is_seen():
    banners, attempts, confirmed = fv.fetch_confirmed_banners(target_asset_set="PR", fetcher=pr_pull, sleep=lambda s: None)
    assert attempts == 1 and confirmed is True


def test_keeps_retrying_until_the_target_set_shows_up():
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return pr_pull() if calls["n"] == 3 else st_pull()

    sleeps = []
    banners, attempts, confirmed = fv.fetch_confirmed_banners(target_asset_set="PR", max_attempts=8, fetcher=flaky,
                                                               sleep=lambda s: sleeps.append(s))
    assert attempts == 3 and confirmed is True
    assert len(sleeps) == 2   # one pause between each of the 2 misses and the next attempt, none after the hit


def test_gives_up_after_max_attempts_and_returns_the_last_pull_anyway():
    banners, attempts, confirmed = fv.fetch_confirmed_banners(target_asset_set="PR", max_attempts=4, fetcher=st_pull,
                                                               sleep=lambda s: None)
    assert attempts == 4 and confirmed is False
    assert banners[0].banner_id == "a"   # still got something back, just not confirmed


# ---- hotspots: a banner image can carry several independently-linked sub-regions -------------

# ---------------- live activity: what a banner is doing right now (one or two words) ----------------

def _notes(banner_, tmp_path, cache=None, analyzer=lambda p: INFO, image_fetcher=ok_image, listing_=None):
    steps = []
    fv.verify_banner(banner_, tmp_path, analyzer, fv.ListingCache(Fetcher({"a-1": listing_ or listing(), "b-2": listing()})),
                     ALIASES, image_fetcher, cache=cache, note=steps.append)
    return steps


def test_a_banner_reports_each_step_in_order(tmp_path):
    assert _notes(banner("a", "https://ajio.com/s/a-1"), tmp_path) == ["Downloading", "Listing lookup", "Reading image", "Comparing"]


def test_a_cache_check_is_reported_when_the_cache_is_on(tmp_path):
    steps = _notes(banner("a", "https://ajio.com/s/a-1"), tmp_path, cache=BannerCache(path=tmp_path / "c.json"))
    assert steps[:2] == ["Downloading", "Cache check"]


def test_a_retry_that_already_has_its_image_does_not_report_downloading_again(tmp_path):
    b = banner("a", "https://ajio.com/s/a-1")
    _notes(b, tmp_path)                                   # first try leaves the image in images/
    assert "Downloading" not in _notes(b, tmp_path)


def test_hotspot_crops_report_reading_hotspot(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    steps = _notes(b, tmp_path, image_fetcher=real_image)
    # no own link, so the whole banner is read once first (for its gender), then each hotspot crop
    assert steps == ["Downloading", "Reading image", "Listing lookup", "Reading hotspot", "Comparing"]


def test_a_banner_that_needs_no_work_reports_nothing(tmp_path):
    assert _notes(banner("a", "https://www.ajio.com/my-account/refer-and-earn"), tmp_path) == []      # not a listing link


def test_every_activity_is_at_most_two_words(tmp_path):
    steps = set(_notes(banner("a", "https://ajio.com/s/a-1"), tmp_path, cache=BannerCache(path=tmp_path / "c.json")))
    steps |= set(_notes(banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100),
                        tmp_path, image_fetcher=real_image))
    steps |= {"Cooling down", "Retry queued"}
    assert steps and all(len(s.split()) <= 2 for s in steps), steps


def test_a_failed_banner_reports_cooling_down_with_the_time_it_is_due(tmp_path):
    events = []
    before = time.time()
    fv.run_feed_verify([banner("a", "https://ajio.com/s/a-1")], tmp_path, analyzer=lambda p: (_ for _ in ()).throw(RuntimeError("500")),
                       aliases=ALIASES, listing_fetcher=Fetcher({"a-1": listing()}), image_fetcher=ok_image, workers=1,
                       retry_rounds=1, retry_pause=0, cache=None,
                       on_activity=lambda bid, text, until=None: events.append((bid, text, until)))
    cooling = [e for e in events if e[1] == "Cooling down"]
    assert len(cooling) == 1 and cooling[0][0] == "a" and cooling[0][2] >= before       # only after try 1; try 2 is the last


def test_the_activity_log_keeps_the_latest_line_per_banner_and_ignores_a_cut_off_one(tmp_path):
    log = fv.ActivityLog(tmp_path / "activity.jsonl")
    log("a", "Downloading")
    log("b", "Listing lookup")
    log("a", "Reading image")
    with open(tmp_path / "activity.jsonl", "a", encoding="utf-8") as f:
        f.write('{"banner_id": "b", "activ')             # a write cut off by a crash
    latest = fv.load_activity(tmp_path)
    assert latest["a"]["activity"] == "Reading image" and latest["b"]["activity"] == "Listing lookup"


def test_a_banner_with_no_own_destination_and_one_passing_hotspot_passes(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing()})
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image)
    assert r["result"] == "PASS"
    assert len(r["hotspot_checks"]) == 1 and r["hotspot_checks"][0]["result"] == "PASS"
    assert r["hotspot_checks"][0]["url"] == "https://ajio.com/s/a-1"


def test_a_failing_hotspot_fails_the_whole_banner_even_if_the_banners_own_link_passes(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1", hotspots=[hotspot("https://ajio.com/s/b-2")],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(), "b-2": listing(brands={"NEW BALANCE": 5})})   # b-2 is missing Under Armour
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image)
    assert r["result"] == "FAIL"
    assert r["banner_check"]["result"] == "PASS"          # the banner's own link was fine
    assert r["hotspot_checks"][0]["result"] == "FAIL"     # the hotspot is what failed it
    assert "hotspot 0" in r["reason"] and "under armour" in r["reason"].lower()


def test_all_hotspots_passing_alongside_the_banners_own_link_is_an_overall_pass(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1",
              hotspots=[hotspot("https://ajio.com/s/b-2"), hotspot("https://ajio.com/s/c-3", x=50)],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(), "b-2": listing(), "c-3": listing()})
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image)
    assert r["result"] == "PASS" and len(r["hotspot_checks"]) == 2
    assert all(h["result"] == "PASS" for h in r["hotspot_checks"])


def test_a_hotspot_to_a_non_listing_link_is_skipped_but_does_not_drag_down_a_passing_banner(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1", hotspots=[hotspot("https://www.ajio.com/shop/support")],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing()})
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image)
    assert r["result"] == "PASS"
    assert r["hotspot_checks"][0]["result"] == "SKIPPED"


def test_a_banner_with_only_non_listing_hotspots_and_no_own_link_is_skipped_overall_not_passed(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://www.ajio.com/shop/support")],
              image_width=200, image_height=100)
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(Fetcher({})), ALIASES, real_image)
    assert r["result"] == "SKIPPED"   # nothing was actually checkable - must not be a vacuous PASS


def test_each_hotspot_gets_its_own_cropped_image_file(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1", x=0, y=0, w=100, h=100),
                                     hotspot("https://ajio.com/s/b-2", x=100, y=0, w=100, h=100)],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(), "b-2": listing()})
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image)
    crop_files = [Path(h["image_file"]) for h in r["hotspot_checks"]]
    assert len(crop_files) == 2 and crop_files[0] != crop_files[1]
    assert all(f.exists() for f in crop_files)
    with PILImage.open(crop_files[0]) as im:
        assert im.size == (100, 100)   # scaled 1:1 since the fake image is already 200x100


def test_hotspot_bounding_box_is_rescaled_to_the_real_downloaded_image_size(tmp_path):
    # declared 200x100 but the "real" fetched image is 400x200 (2x) - the crop must scale with it
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1", x=0, y=0, w=100, h=50)],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing()})
    r = fv.verify_banner(b, tmp_path, lambda p: INFO, fv.ListingCache(fetcher), ALIASES,
                         lambda url: real_image(url, w=400, h=200))
    with PILImage.open(r["hotspot_checks"][0]["image_file"]) as im:
        assert im.size == (200, 100)   # 100x50 declared, scaled 2x to match the real 400x200 image


# ---- hotspot gender comes from the main banner, brand stays specific to its own crop -------------

def test_hotspot_uses_the_main_banners_gender_but_its_own_crops_brands(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1", hotspots=[hotspot("https://ajio.com/s/b-2")],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(genders={"Men": 10}), "b-2": listing(genders={"Men": 10})})

    def analyzer(path):
        if "__hs" in path.name:
            # the crop's own gender reading - would fail if used, but its brands should still count
            return {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "MIN. 40% OFF*",
                    "target_gender": "women"}
        return {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "MIN. 40% OFF*",
                "target_gender": "men"}   # the main banner's own reading

    r = fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image)
    hc = r["hotspot_checks"][0]
    assert hc["banner_check"]["banner_gender"] == "men"                                  # the main banner's gender...
    assert hc["banner_check"]["banner_brands"] == ["new balance", "under armour"]         # ...but its own crop's brands
    assert hc["result"] == "PASS"   # would have FAILed on gender ("women" vs a Men-only listing) without the override


def test_main_gender_reuses_the_already_computed_own_check_without_a_second_vision_call(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1", hotspots=[hotspot("https://ajio.com/s/b-2")],
              image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(), "b-2": listing()})
    calls = []

    def analyzer(path):
        calls.append(path.name)
        return INFO

    fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image)
    assert len(calls) == 2   # the main image (for the own check) and the one hotspot crop - not a 3rd call for gender


def test_main_image_is_still_read_for_gender_when_the_banner_has_no_own_destination(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(genders={"Men": 10})})

    def analyzer(path):
        if "__hs" in path.name:
            return {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "MIN. 40% OFF*",
                    "target_gender": "women"}
        return {"brands_mentioned": [], "deal_offered": None, "target_gender": "men"}

    r = fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image)
    hc = r["hotspot_checks"][0]
    assert hc["banner_check"]["banner_gender"] == "men"   # read from the full banner even though it has no link of its own
    assert hc["result"] == "PASS"


def test_a_failed_main_image_read_leaves_the_hotspot_using_its_own_gender_reading(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(genders={"Men": 10})})

    def analyzer(path):
        if "__hs" in path.name:
            return {"brands_mentioned": ["new balance", "under armour"], "deal_offered": "MIN. 40% OFF*",
                    "target_gender": "men"}
        raise ValueError("main image analysis blew up")

    r = fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image)
    hc = r["hotspot_checks"][0]
    assert hc["banner_check"]["banner_gender"] == "men"   # its own crop's reading - the main image couldn't be read at all
    assert hc["result"] == "PASS"


# ---- cross-run cache: a repeat banner (same image, same link) reuses its earlier verdict --------

def test_a_cache_hit_reuses_the_earlier_verdict_without_recalling_the_analyzer_or_listing(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    fetcher = Fetcher({"a-1": listing()})
    calls = []

    def analyzer(p):
        calls.append(p)
        return INFO

    r1 = fv.verify_banner(banner("first", "https://ajio.com/s/a-1"), tmp_path / "run1", analyzer,
                          fv.ListingCache(fetcher), ALIASES, real_image, cache=cache, run_id="run1")
    assert r1["result"] == "PASS" and len(calls) == 1 and len(fetcher.calls) == 1

    r2 = fv.verify_banner(banner("second", "https://ajio.com/s/a-1"), tmp_path / "run2", analyzer,
                          fv.ListingCache(fetcher), ALIASES, real_image, cache=cache, run_id="run2")
    assert r2["result"] == "PASS"
    assert len(calls) == 1 and len(fetcher.calls) == 1   # neither the vision model nor the listing API was hit again
    assert r2["reused_from"] == {"banner_id": "first", "run_id": "run1", "cached_at": cache.entries[0]["cached_at"]}


def test_a_different_destination_on_the_same_image_does_not_reuse_the_cache(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    fetcher = Fetcher({"a-1": listing(), "a-2": listing()})
    calls = []

    def analyzer(p):
        calls.append(p)
        return INFO

    fv.verify_banner(banner("first", "https://ajio.com/s/a-1"), tmp_path, analyzer, fv.ListingCache(fetcher),
                     ALIASES, real_image, cache=cache, run_id="run1")
    fv.verify_banner(banner("second", "https://ajio.com/s/a-2"), tmp_path, analyzer, fv.ListingCache(fetcher),
                     ALIASES, real_image, cache=cache, run_id="run1")
    assert len(calls) == 2   # a different link means it isn't "the same banner", even with an identical image


def test_a_transient_failure_is_not_cached(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    fetcher = Fetcher({"a-1": RuntimeError("boom")})
    r = fv.verify_banner(banner("first", "https://ajio.com/s/a-1"), tmp_path, lambda p: INFO,
                         fv.ListingCache(fetcher), ALIASES, real_image, cache=cache, run_id="run1")
    assert fv.is_retryable(r)
    assert cache.entries == []   # a transient error must never be saved as if it were the real verdict


def test_cache_hit_still_crops_a_fresh_hotspot_image_for_this_runs_own_out_dir(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    fetcher = Fetcher({"a-1": listing()})
    b1 = banner("first", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    fv.verify_banner(b1, tmp_path / "run1", lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image,
                     cache=cache, run_id="run1")

    b2 = banner("second", None, hotspots=[hotspot("https://ajio.com/s/a-1")], image_width=200, image_height=100)
    r2 = fv.verify_banner(b2, tmp_path / "run2", lambda p: INFO, fv.ListingCache(fetcher), ALIASES, real_image,
                          cache=cache, run_id="run2")
    assert r2["hotspot_checks"][0]["result"] == "PASS"
    assert Path(r2["hotspot_checks"][0]["image_file"]).exists()
    assert "run2" in r2["hotspot_checks"][0]["image_file"]   # cropped into *this* run's own images dir
    assert r2["hotspot_checks"][0]["reused_from"]["banner_id"] == "first"
    assert len(fetcher.calls) == 1   # listing not refetched for the second (cache-hit) banner's hotspot


def test_run_feed_verify_wires_the_cache_through_across_banners_in_one_run(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    fetcher = Fetcher({"a-1": listing()})
    calls = []

    def analyzer(p):
        calls.append(p)
        return INFO

    feed = [banner("first", "https://ajio.com/s/a-1", 0), banner("second", "https://ajio.com/s/a-1", 1)]
    results = run(feed, tmp_path, fetcher, analyzer=analyzer, image_fetcher=real_image, cache=cache)
    by = {r["banner_id"]: r for r in results}
    assert all(r["result"] == "PASS" for r in results)
    assert len(calls) == 1   # the second banner (same image, same link) reused the first's vision reading
    assert by["first"].get("reused_from") is None
    assert by["second"]["reused_from"]["banner_id"] == "first"


def test_is_retryable_catches_a_transient_failure_on_a_non_winning_hotspot():
    # own check passes (result "PASS"), one hotspot fails permanently (FAIL, wins the aggregation),
    # another hotspot fails transiently (INCONCLUSIVE/listing_fetch_failed) - the overall result is
    # FAIL, not INCONCLUSIVE, so the top-level reason never mentions the transient one; is_retryable
    # must still find it by checking every hotspot's own reason, not just the aggregated one.
    result = {"result": "FAIL", "reason": "hotspot 0 (x): missing brands ['Guess']",
             "hotspot_checks": [{"hotspot_index": 0, "result": "FAIL", "reason": ""},
                                {"hotspot_index": 1, "result": "INCONCLUSIVE", "reason": "listing_fetch_failed: boom"}]}
    assert fv.is_retryable({**result, "result": "INCONCLUSIVE"}) is True
    assert fv.is_retryable(result) is True    # a FAIL whose other hotspot never got checked is retried too
    clean = {"result": "FAIL", "reason": "x", "hotspot_checks": [{"hotspot_index": 0, "result": "FAIL", "reason": "missing brands"}]}
    assert fv.is_retryable(clean) is False    # a FAIL with every hotspot properly checked is final


def test_a_banner_skipped_before_it_starts_never_downloads_or_fetches_a_listing(tmp_path):
    (tmp_path / "skip_requests.json").write_text(json.dumps(["a"]), encoding="utf-8")
    calls = []
    fetcher = Fetcher({"x-1": listing()})
    results = run([banner("a", "https://ajio.com/s/x-1")], tmp_path, fetcher,
                 image_fetcher=lambda url: (calls.append(url), ok_image(url))[1])
    assert results[0]["result"] == "SKIPPED" and results[0]["reason"] == "user_skipped"
    assert calls == [] and fetcher.calls == []   # never actually "sent" - no image download, no listing fetch


def test_a_skip_requested_mid_call_discards_the_in_flight_result(tmp_path):
    # simulates a skip landing on a worker thread while it's already blocked inside the vision call -
    # the check right after verify_banner returns must still catch it and throw away the real PASS
    def analyzer_that_triggers_a_skip(image_path):
        (tmp_path / "skip_requests.json").write_text(json.dumps(["a"]), encoding="utf-8")
        return INFO

    r = run([banner("a", "https://ajio.com/s/x-1")], tmp_path, Fetcher({"x-1": listing()}),
           analyzer=analyzer_that_triggers_a_skip)[0]
    assert r["result"] == "SKIPPED" and r["reason"] == "user_skipped"


def test_a_skipped_banner_is_not_retryable(tmp_path):
    (tmp_path / "skip_requests.json").write_text(json.dumps(["a"]), encoding="utf-8")
    r = run([banner("a", "https://ajio.com/s/x-1")], tmp_path, Fetcher({"x-1": RuntimeError("500")}))[0]
    assert r["result"] == "SKIPPED"
    assert fv.is_retryable(r) is False


def test_excluded_carousels_are_dropped_before_anything_runs(tmp_path):
    calls = []
    feed = [banner("a", "https://ajio.com/s/x-1", idx=0), banner("b", "https://ajio.com/s/x-2", idx=1)]
    feed[0].section_index, feed[1].section_index = 5, 9
    fetcher = Fetcher({"x-1": listing(), "x-2": listing()})
    results = run(feed, tmp_path, fetcher, excluded_carousels={9},
                 image_fetcher=lambda url: (calls.append(url), ok_image(url))[1])
    assert [r["banner_id"] for r in results] == ["a"]   # "b" (carousel 9) isn't in the output at all
    assert fetcher.calls == [("x-1", "curated")]
    assert len(calls) == 1


def test_a_carousel_excluded_by_section_id_stays_excluded_when_its_position_shifts(tmp_path):
    # A schedule saves the exclusion as the section's stable _id: the same section can sit at a
    # different section_index on the next pull (the feed gained a section above it), and must still go.
    slide0 = banner("SEC-A:0", "https://ajio.com/s/x-1", idx=0)
    slide1 = banner("SEC-A:1", "https://ajio.com/s/x-2", idx=1)
    other = banner("SEC-B", "https://ajio.com/s/x-3", idx=2)
    slide0.section_index = slide1.section_index = 12      # shifted - was 5 when the exclusion was saved
    other.section_index = 5                               # a different section now sits at the old position
    assert fv.section_id(slide0) == fv.section_id(slide1) == "SEC-A" and fv.section_id(other) == "SEC-B"
    kept = fv.exclude_banners([slide0, slide1, other], excluded_sections={"SEC-A"})
    assert [b.banner_id for b in kept] == ["SEC-B"]
    fetcher = Fetcher({"x-1": listing(), "x-2": listing(), "x-3": listing()})
    results = run([slide0, slide1, other], tmp_path, fetcher, excluded_sections={"SEC-A"})
    assert [r["banner_id"] for r in results] == ["SEC-B"]
    assert fetcher.calls == [("x-3", "curated")]


def test_exclusions_by_position_and_by_section_id_combine():
    a, b, c = (banner(i, "https://ajio.com/s/x-1") for i in ("S1", "S2", "S3"))
    a.section_index, b.section_index, c.section_index = 1, 2, 3
    assert [x.banner_id for x in fv.exclude_banners([a, b, c], {1}, {"S3"})] == ["S2"]
    assert fv.exclude_banners([a, b, c]) == [a, b, c]


# ---------------- luxe.ajio.com links are checked against the Luxe store ----------------

class StoreFetcher:
    """Answers per store, so a test can tell which catalogue a check was made against."""

    def __init__(self, by_store):
        self.by_store, self.calls = by_store, []

    def __call__(self, slug, kind="curated", store=None, **k):
        if not k.get("sort"):
            self.calls.append((slug, kind, store))
        return self.by_store[store]


def test_a_luxe_link_is_checked_against_the_luxe_store_not_the_standard_one(tmp_path):
    # the real case: the standard store's copy of the page has no BOSS; the Luxe store's does
    standard = listing(title="All Stars Early Offers Men", brands={"BROOKS BROTHERS": 583, "EA7 Emporio Armani": 669})
    luxe = listing(title="All Stars Early Offers Men", brands={"BOSS": 1991, "ALL SAINTS": 337, "Tom Ford": 257})
    fetcher = StoreFetcher({None: standard, "luxe": luxe})
    info = {"brands_mentioned": ["BOSS", "ALLSAINTS"], "deal_offered": "", "target_gender": "men"}
    r = fv.verify_banner(banner("b", "https://luxe.ajio.com/s/allstarsearlyoffersmen-403091"), tmp_path,
                         lambda p: info, fv.ListingCache(fetcher, pause=0), ALIASES, ok_image)
    assert fetcher.calls == [("allstarsearlyoffersmen-403091", "curated", "luxe")]
    assert r["listing_store"] == "luxe" and "BOSS" not in (r["banner_check"].get("missing_brands") or [])


def test_an_ordinary_link_is_still_checked_against_the_standard_store(tmp_path):
    fetcher = StoreFetcher({None: listing(), "luxe": listing()})
    r = fv.verify_banner(banner("b", "https://www.ajio.com/s/a-1"), tmp_path, lambda p: INFO,
                         fv.ListingCache(fetcher, pause=0), ALIASES, ok_image)
    assert fetcher.calls == [("a-1", "curated", None)] and "listing_store" not in r


def test_the_same_slug_in_two_stores_is_fetched_and_cached_separately():
    fetcher = StoreFetcher({None: listing(title="standard"), "luxe": listing(title="luxe")})
    cache = fv.ListingCache(fetcher, pause=0)
    assert cache.get("curated", "s-1").title == "standard"
    assert cache.get("curated", "s-1", "luxe").title == "luxe"
    assert cache.get("curated", "s-1", "luxe").title == "luxe"          # second luxe lookup comes from the cache
    assert fetcher.calls == [("s-1", "curated", None), ("s-1", "curated", "luxe")]


def test_a_fetcher_that_takes_no_store_argument_is_still_called_for_ordinary_links():
    calls = []
    cache = fv.ListingCache(lambda slug, kind="curated": calls.append((slug, kind)) or listing(), pause=0)
    cache.get("curated", "s-1")
    assert calls == [("s-1", "curated")]


# ---- UNAVAILABLE: a temporary server/network error that outlived its retries is not a finding about the banner ----

def _inc(reason, **extra):
    return {"banner_id": "a", "result": "INCONCLUSIVE", "reason": reason, "attempts": 5, **extra}


def test_shown_result_turns_a_spent_temporary_error_into_unavailable():
    assert fv.shown_result(_inc("listing_fetch_failed: FeedError: HTTP 400")) == "UNAVAILABLE"
    assert fv.shown_result(_inc("image_download_failed: error: ConnectionError")) == "UNAVAILABLE"
    assert fv.shown_result(_inc("vision_failed: InternalServerError: 503")) == "UNAVAILABLE"


def test_a_transient_hotspot_error_counts_too():
    r = _inc("", hotspot_checks=[{"result": "INCONCLUSIVE", "reason": "listing_fetch_failed: timeout"}])
    assert fv.shown_result(r) == "UNAVAILABLE"


def test_other_inconclusive_reasons_stay_inconclusive_and_other_results_are_untouched():
    assert fv.shown_result(_inc("empty_bounding_box_after_scaling")) == "INCONCLUSIVE"
    assert fv.shown_result(_inc("vision_unavailable: no key")) == "INCONCLUSIVE"           # a missing key won't fix itself
    for status in ("PASS", "FAIL", "SKIPPED"):
        assert fv.shown_result({"banner_id": "a", "result": status, "reason": "listing_fetch_failed"}) == status


def test_in_a_live_run_it_is_still_processing_while_tries_remain():
    assert fv.shown_result(_inc("listing_fetch_failed: x", attempts=2), live=True) == "PROCESSING"
    assert fv.shown_result(_inc("listing_fetch_failed: x", attempts=fv.RETRY_ROUNDS + 1), live=True) == "UNAVAILABLE"
    assert fv.shown_result(_inc("listing_fetch_failed: x", attempts=2), live=False) == "UNAVAILABLE"   # run over: no more tries coming


def test_the_cli_summary_counts_unavailable_separately():
    text = fv.format_summary([{"banner_id": "a", "result": "PASS"}, _inc("listing_fetch_failed: x", alt_text="t")])
    assert "PASS=1" in text and "UNAVAILABLE=1" in text and "INCONCLUSIVE" not in text.split("\n")[0]


# ---- multi-link (hotspot) banners: an unchecked hotspot is UNAVAILABLE and retryable, and a retry only redoes it ----

def test_a_fail_with_an_unchecked_hotspot_stays_a_fail_but_that_hotspot_is_unavailable():
    r = {"banner_id": "a", "result": "FAIL", "reason": "hotspot 0: missing brands", "attempts": fv.RETRY_ROUNDS + 1,
         "hotspot_checks": [{"hotspot_index": 0, "result": "FAIL", "reason": ""},
                            {"hotspot_index": 1, "result": "INCONCLUSIVE", "reason": "vision_failed: InternalServerError: 500"}]}
    assert fv.shown_result(r) == "FAIL"                                     # the finding is never hidden
    assert [fv.shown_hotspot_result(h) for h in r["hotspot_checks"]] == ["FAIL", "UNAVAILABLE"]
    assert fv.shown_result(r, live=True) == "FAIL"                           # tries used up
    assert fv.shown_result({**r, "attempts": 1}, live=True) == "PROCESSING"   # still retrying in a live run


def test_shown_hotspot_result_leaves_other_inconclusive_hotspots_alone():
    assert fv.shown_hotspot_result({"result": "INCONCLUSIVE", "reason": "empty_bounding_box_after_scaling"}) == "INCONCLUSIVE"
    assert fv.shown_hotspot_result({"result": "PASS"}) == "PASS"


def test_a_retry_redoes_only_the_hotspot_that_hit_a_temporary_error(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1",
               hotspots=[hotspot("https://ajio.com/s/b-2"), hotspot("https://ajio.com/s/c-3", x=60), hotspot("https://ajio.com/s/d-4", x=120)],
               image_width=300, image_height=100)
    fetcher = Fetcher({"a-1": listing(), "b-2": listing(), "c-3": listing(), "d-4": listing()})
    calls, broken = [], {"on": True}

    def analyzer(path):
        calls.append(path.name)
        if broken["on"] and "__hs1" in path.name:
            raise RuntimeError("Gemma 500")
        return INFO

    first = fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image)
    assert [h["result"] for h in first["hotspot_checks"]] == ["PASS", "INCONCLUSIVE", "PASS"]
    assert fv.is_retryable(first) and first["result"] == "INCONCLUSIVE"
    calls.clear(); broken["on"] = False
    second = fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image, prior=first)
    assert second["result"] == "PASS" and [h["result"] for h in second["hotspot_checks"]] == ["PASS", "PASS", "PASS"]
    assert sum("__hs" in c for c in calls) == 1 and any("__hs1" in c for c in calls)   # only hotspot 1 was read again
    assert second["hotspot_checks"][0]["image_file"] == first["hotspot_checks"][0]["image_file"]   # the kept ones keep their crop


def test_a_retry_with_nothing_to_redo_for_the_hotspots_does_not_read_the_main_image_for_them(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/b-2")], image_width=200, image_height=100)
    fetcher = Fetcher({"b-2": listing()})
    calls = []

    def analyzer(path):
        calls.append(path.name)
        return INFO

    first = fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image)
    calls.clear()
    fv.verify_banner(b, tmp_path, analyzer, fv.ListingCache(fetcher), ALIASES, real_image, prior=first)
    assert calls == []                                                      # the good hotspot is kept: no vision call at all


def test_the_retry_queue_retries_a_fail_banner_whose_hotspot_was_unchecked_and_keeps_the_fail(tmp_path):
    b = banner("hs", "https://ajio.com/s/a-1", hotspots=[hotspot("https://ajio.com/s/b-2"), hotspot("https://ajio.com/s/c-3", x=60)],
               image_width=200, image_height=100)
    fetcher = Fetcher({"a-1": listing(), "b-2": listing(brands={"NEW BALANCE": 5}), "c-3": listing()})   # b-2 lacks Under Armour
    calls = {"c": 0}

    def analyzer(path):
        if "__hs1" in path.name:
            calls["c"] += 1
            if calls["c"] <= 3:                  # hero._analyze itself tries a vision call 3 times before giving up
                raise RuntimeError("Gemma 500")
        return INFO

    got = run([b], tmp_path, fetcher, analyzer=analyzer, image_fetcher=real_image, retry_rounds=2)[0]
    assert got["result"] == "FAIL" and got["attempts"] == 2                  # retried once, hotspot 1 now fine, the FAIL stands
    assert [h["result"] for h in got["hotspot_checks"]] == ["FAIL", "PASS"]


# ---- a manual (per-banner) retry: works on any result, starts from scratch, never uses the cross-run cache ----

def _fail_listing_fetcher():
    return Fetcher({"a-1": listing(brands={"NEW BALANCE": 5})})     # lacks Under Armour, which INFO names -> FAIL


def test_only_banner_redoes_a_fail_that_the_automatic_retry_would_have_left_alone(tmp_path):
    b = banner("a", "https://ajio.com/s/a-1")
    first = run([b], tmp_path, _fail_listing_fetcher())[0]
    assert first["result"] == "FAIL" and not fv.is_retryable(first)
    fetcher = Fetcher({"a-1": listing()})                             # the listing has since been fixed
    plain = run([b], tmp_path, fetcher, previous={"a": first})[0]      # a normal resume leaves a settled FAIL as it is
    assert plain["result"] == "FAIL" and fetcher.calls == []
    forced = run([b], tmp_path, fetcher, previous={"a": first}, only={"a"})[0]
    assert forced["result"] == "PASS" and forced["attempts"] == 2      # redone, and counted as another try


def test_a_forced_retry_of_a_fail_is_one_try_not_five(tmp_path):
    b = banner("a", "https://ajio.com/s/a-1")
    fetcher = _fail_listing_fetcher()
    first = run([b], tmp_path, fetcher)[0]
    fetcher.calls.clear()
    got = run([b], tmp_path, fetcher, previous={"a": first}, only={"a"}, retry_rounds=4)[0]
    assert got["result"] == "FAIL" and len(fetcher.calls) == 1        # a real FAIL is not a temporary error: no automatic re-tries


def test_a_forced_retry_starts_from_scratch_but_a_follow_up_after_a_temporary_error_may_reuse_good_hotspots(tmp_path):
    b = banner("hs", None, hotspots=[hotspot("https://ajio.com/s/b-2"), hotspot("https://ajio.com/s/c-3", x=60)],
               image_width=200, image_height=100)
    fetcher = Fetcher({"b-2": listing(), "c-3": listing()})
    calls = []

    def analyzer(path):
        calls.append(path.name)
        return INFO

    first = run([b], tmp_path, fetcher, analyzer=analyzer, image_fetcher=real_image)[0]
    calls.clear()
    run([b], tmp_path, fetcher, analyzer=analyzer, image_fetcher=real_image, previous={"hs": first}, only={"hs"})
    assert sum("__hs" in c for c in calls) == 2                       # both hotspots read again, none carried over


def test_the_cross_run_cache_is_bypassed_when_it_is_switched_off(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    b = banner("a", "https://ajio.com/s/a-1")
    fetcher = Fetcher({"a-1": listing()})
    fv.run_feed_verify([b], tmp_path / "r1", analyzer=lambda p: INFO, aliases=ALIASES, listing_fetcher=fetcher, image_fetcher=real_image,
                       workers=1, retry_rounds=0, retry_pause=0, cache=cache)
    n = len(fetcher.calls)
    fv.run_feed_verify([b], tmp_path / "r2", analyzer=lambda p: INFO, aliases=ALIASES, listing_fetcher=fetcher, image_fetcher=real_image,
                       workers=1, retry_rounds=0, retry_pause=0, cache=cache)
    assert len(fetcher.calls) == n                                    # a repeat banner is served from the cache...
    fv.run_feed_verify([b], tmp_path / "r3", analyzer=lambda p: INFO, aliases=ALIASES, listing_fetcher=fetcher, image_fetcher=real_image,
                       workers=1, retry_rounds=0, retry_pause=0, cache=False)
    assert len(fetcher.calls) == n + 1                                # ...and cache=False (--no-cache) checks it for real


def test_load_final_results_lays_a_later_retry_over_a_finished_runs_results_json(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps([
        {"banner_id": "a", "result": "FAIL", "attempts": 1}, {"banner_id": "b", "result": "PASS", "attempts": 1}]), encoding="utf-8")
    (tmp_path / "partial.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"banner_id": "a", "result": "FAIL", "attempts": 1}, {"banner_id": "b", "result": "PASS", "attempts": 1},
        {"banner_id": "a", "result": "PASS", "attempts": 2}]) + "\n", encoding="utf-8")
    got = fv.load_final_results(tmp_path)
    assert list(got) == ["a", "b"] and got["a"]["result"] == "PASS" and got["b"]["result"] == "PASS"


def test_load_final_results_without_results_json_is_just_the_partial_log(tmp_path):
    (tmp_path / "partial.jsonl").write_text(json.dumps({"banner_id": "a", "result": "PASS", "attempts": 1}) + "\n", encoding="utf-8")
    assert fv.load_final_results(tmp_path) == {"a": {"banner_id": "a", "result": "PASS", "attempts": 1}}


# ---- reasons for INCONCLUSIVE ------------------------------------------------------------------------------

BRAND_ONLY = {"brands_mentioned": ["new balance"], "deal_offered": None, "target_gender": "men_and_women"}


def test_a_brand_only_banner_is_inconclusive_and_says_why(tmp_path):
    r = run([banner("b", "https://ajio.com/s/a-1")], tmp_path, Fetcher({"a-1": listing(brands={"NEW BALANCE": 5})}),
            analyzer=lambda p: BRAND_ONLY)[0]
    assert r["result"] == "INCONCLUSIVE"
    assert "no deal text" in r["reason"] and "page title can't be checked" in r["reason"]


def test_every_undecided_case_has_its_own_words():
    assert "AJIO beauty" in fv.undecided_reason({"gender_matches": "AJIO_BEAUTY", "banner_brands": ["X"]})
    assert "isn't a recognized audience" in fv.undecided_reason({"gender_matches": "INCONCLUSIVE", "banner_gender": "odd"})
    assert "nothing to compare" in fv.undecided_reason({"title_matches_deal": None, "banner_brands": []})
    assert "no deal text" in fv.undecided_reason({"title_matches_deal": None, "banner_brands": ["X"]})
    assert "names no brand" in fv.undecided_reason({"title_matches_deal": True, "banner_brands": []})
    assert fv.undecided_reason({}) == "" and fv.undecided_reason({"title_matches_deal": True, "banner_brands": ["X"]}) == ""


def test_an_undecided_hotspot_gets_a_reason_too_and_a_fail_keeps_its_own():
    check = {"title_matches_deal": None, "banner_brands": ["X"]}
    assert "no deal text" in fv._hotspot_reason({"result": "INCONCLUSIVE", "banner_check": check})
    assert fv._hotspot_reason({"result": "FAIL", "banner_check": {"missing_brands": ["Y"], **check}}).startswith("missing brands")
    assert fv._hotspot_reason({"result": "PASS", "banner_check": check}) == ""


def test_the_summary_and_an_old_result_without_a_reason_are_explained(tmp_path):
    old = {"banner_id": "b", "alt_text": "B", "destination_raw": "https://ajio.com/s/a-1", "result": "INCONCLUSIVE", "reason": "",
           "banner_check": {"title_matches_deal": None, "banner_brands": ["X"]}}
    assert "no deal text" in fv.format_summary([old])
    from qa import export_xlsx
    assert "no deal text" in export_xlsx._cell(old, "reason")


def test_an_old_undecided_result_and_its_hotspots_are_explained_when_the_run_is_read_back():
    from web import runner
    check = {"title_matches_deal": None, "banner_brands": ["X"]}
    old = {"result": "INCONCLUSIVE", "reason": "", "banner_check": check}
    assert "no deal text" in runner._explained(old)["reason"]
    assert runner._explained({**old, "reason": "already has one"})["reason"] == "already has one"
    assert runner._explained({"result": "PASS", "reason": "", "banner_check": check})["reason"] == ""
