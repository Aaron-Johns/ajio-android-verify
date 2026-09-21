"""Runner logic against a fake device that replays trimmed real screen dumps (no emulator, no network)."""
import json
from pathlib import Path

import pytest

from qa.compare import AliasMap
from qa.feed_client import Banner
from qa.spotcheck import device as dev
from qa.spotcheck import landing as lp
from qa.spotcheck import runner
from qa.spotcheck.vision import VisionUnavailable
from qa.status import SpotStatus as S

FIX = Path(__file__).resolve().parent / "fixtures"
SCREENS = {
    "home": (lp.APP_PACKAGE, ".home.AjioHomeActivity", (FIX / "ui_home.xml").read_text(encoding="utf-8")),
    "plp": (lp.APP_PACKAGE, ".home.AjioHomeActivity", (FIX / "ui_plp.xml").read_text(encoding="utf-8")),
    "web": (lp.APP_PACKAGE, ".web.CustomWebViewActivity", (FIX / "ui_webview.xml").read_text(encoding="utf-8")),
    "chrome": ("com.android.chrome", "org.chromium.Main", (FIX / "ui_webview.xml").read_text(encoding="utf-8")),
}
ALIASES = AliasMap([["LEVI'S", "LEVIS"]])


class FakeDevice:
    """Home screen with real labelled banners; tapping one navigates according to `routes`."""
    width, height = 1080, 2424

    def __init__(self, routes):
        self.routes, self.current, self.taps = routes, "home", 0

    def state(self):
        return SCREENS[self.current]

    def scroll(self, *a, **k): pass
    def scroll_to_top(self, *a, **k): pass
    def return_home(self): self.current = "home"
    def capture_after_tap(self, before): return self.state()

    def tap(self, box):
        self.taps += 1
        labels = {b: d for d, boxes in dev.clickable_descs(SCREENS["home"][2], self.width, self.height).items() for b in boxes}
        self.current = self.routes.get(labels.get(box), "home")

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")
        return str(path)


def banner(alt, dest, bid=None, image="https://cdn/x.png"):
    return Banner(bid or alt, image, 0, dest, "hybrid-banner", alt, 0, None, alt_text=alt)


def check(device, b, tmp_path, analyzer=None, use_vision=True):
    return runner.check_banner(device, b, tmp_path, analyzer or (lambda p: {}), ALIASES, use_vision=use_vision)


RUSH = "https://www.ajio.com/s/4hoursdelivery-160865?isPDBanner=true"
HDFC = "https://www.ajio.com/hdfc-emi-credit"


def test_helpers_find_only_fully_visible_clickable_labels():
    found = dev.clickable_descs(SCREENS["home"][2], 1080, 2424)
    assert found["Ajio Rush"] == [(0, 724, 1080, 840)]
    assert "Home" not in found  # the bottom tab bar is outside the content area / not clickable
    assert dev.looks_like_home(*SCREENS["home"]) and not dev.looks_like_home(*SCREENS["plp"])
    assert dev.parse_bounds("[0,724][1080,840]") == (0, 724, 1080, 840) and dev.parse_bounds("junk") is None


def test_confirmed_end_to_end_listing(tmp_path):
    r = check(FakeDevice({"Ajio Rush": "plp"}), banner("Ajio Rush", RUSH), tmp_path)
    assert r.status is S.CONFIRMED and r.landing_kind == lp.PLP and r.landing_title == "Delivery Starts in 30 Mins"
    assert Path(r.screenshot).exists()


def test_ambiguous_link_confirmed_by_page_title_without_vision(tmp_path):
    r = check(FakeDevice({"HDFC": "web"}), banner("HDFC", HDFC), tmp_path, use_vision=False)
    assert (r.status, r.evidence, r.declared_type) == (S.CONFIRMED, "type_and_text", "UNKNOWN")
    assert r.vision == {}


def test_deviation_flagged_when_linked_brand_missing_from_listing(tmp_path):
    r = check(FakeDevice({"Ajio Rush": "plp"}), banner("Ajio Rush", "https://www.ajio.com/b/nike-1"), tmp_path)
    assert r.status is S.APP_DEVIATES_FROM_FEED and "nike" in r.reason


def test_deviation_when_listing_link_opens_another_app(tmp_path):
    r = check(FakeDevice({"Ajio Rush": "chrome"}), banner("Ajio Rush", RUSH), tmp_path)
    assert r.status is S.APP_DEVIATES_FROM_FEED and r.landing_kind == lp.EXTERNAL


def test_dead_banner_is_retried_once_then_flagged(tmp_path):
    device = FakeDevice({})  # tapping does nothing
    r = check(device, banner("Ajio Rush", RUSH), tmp_path)
    assert device.taps == 2 and r.status is S.APP_DEVIATES_FROM_FEED and r.reason == "tap_did_not_navigate"


def test_first_tap_missing_is_forgiven_if_the_retry_navigates(tmp_path):
    class Flaky(FakeDevice):
        def tap(self, box):
            super().tap(box)
            if self.taps == 1:
                self.current = "home"
    r = check(Flaky({"Ajio Rush": "plp"}), banner("Ajio Rush", RUSH), tmp_path)
    assert r.status is S.CONFIRMED


def test_banner_not_on_screen_is_inconclusive(tmp_path):
    device = FakeDevice({})
    r = check(device, banner("Not a real label", RUSH), tmp_path)
    assert r.status is S.INCONCLUSIVE and r.reason == "banner_not_found_uniquely_on_screen" and device.taps == 0


# ---------------- vision fallback for ambiguous links ----------------

@pytest.fixture
def fake_download(monkeypatch):
    monkeypatch.setattr(runner, "fetch_image", lambda url: ("OK", b"img", "image/webp"))


def test_vision_match_confirms_an_ambiguous_link(tmp_path, fake_download):
    analyses = iter([{"brands_mentioned": ["HDFC Bank"]}, {"brands_mentioned": ["HDFC BANK"]}])
    r = check(FakeDevice({"HDFC": "web"}), banner("HDFC", "https://www.ajio.com/supercash"), tmp_path, lambda p: next(analyses))
    assert (r.status, r.evidence, r.vision["verdict"]) == (S.CONFIRMED, "vision", "MATCH")


def test_vision_difference_flags_deviation_when_title_cannot_explain_it(tmp_path, fake_download):
    analyses = iter([{"brands_mentioned": ["HDFC Bank"]}, {"brands_mentioned": ["Nike"]}])
    r = check(FakeDevice({"HDFC": "web"}), banner("HDFC", "https://www.ajio.com/supercash"), tmp_path, lambda p: next(analyses))
    assert r.status is S.APP_DEVIATES_FROM_FEED and r.vision["verdict"] == "DIFFERENT"


def test_vision_unavailable_falls_back_to_page_title(tmp_path, fake_download):
    def analyzer(p):
        raise VisionUnavailable("GEMINI_API_KEY is not set")
    r = check(FakeDevice({"HDFC": "web"}), banner("HDFC", HDFC), tmp_path, analyzer)
    assert r.status is S.CONFIRMED and "unavailable" in r.vision


def test_vision_error_is_recorded_not_raised(tmp_path, fake_download):
    def analyzer(p):
        raise RuntimeError("model exploded")
    r = check(FakeDevice({"HDFC": "web"}), banner("HDFC", "https://www.ajio.com/supercash"), tmp_path, analyzer)
    assert r.status is S.INCONCLUSIVE and "model exploded" in r.vision["error"]


def test_vision_not_used_for_resolvable_links(tmp_path, fake_download):
    def analyzer(p):
        raise AssertionError("vision must not run for a resolvable link")
    assert check(FakeDevice({"Ajio Rush": "plp"}), banner("Ajio Rush", RUSH), tmp_path, analyzer).status is S.CONFIRMED


# ---------------- sampling and the whole run ----------------

def feed():
    return [banner("Ajio Rush", RUSH), banner("HDFC", HDFC), banner("ICICI", "https://www.ajio.com/bank-icici-offer-tnc"),
            banner("Duplicate", RUSH, "d1"), banner("Duplicate", RUSH, "d2"), banner("", RUSH, "noalt"),
            banner("Ajio Rush ", ""), banner("Beauty", "https://ajio.com/s/beauty-373949")]


def test_candidates_are_unique_labelled_visible_and_have_a_destination():
    found = runner.collect_candidates(FakeDevice({}), feed(), wanted=5)
    ids = {b.banner_id for b in found}
    assert "Ajio Rush" in ids and "HDFC" in ids
    assert not ({"d1", "d2", "noalt"} & ids)          # duplicate / missing labels are unusable
    assert "Beauty" in ids                            # unique on this screen, so tappable


def test_label_appearing_twice_on_screen_is_neither_a_candidate_nor_tappable():
    extra = '<node class="x" resource-id="" text="" content-desc="Ajio Rush" clickable="true" bounds="[0,900][1080,1000]"/>'
    source = SCREENS["home"][2].replace("</hierarchy>", extra + "</hierarchy>")

    class Duplicated(FakeDevice):
        def state(self):
            return (lp.APP_PACKAGE, ".home.AjioHomeActivity", source)

    device = Duplicated({})
    assert "Ajio Rush" not in {b.banner_id for b in runner.collect_candidates(device, feed(), 5)}
    assert runner.locate(device, "Ajio Rush") is None


def test_run_spotcheck_samples_reproducibly_and_flags_deviations(tmp_path):
    routes = {"Ajio Rush": "plp", "HDFC": "chrome"}    # HDFC opens Chrome although the link is an in-app CMS page
    device = FakeDevice(routes)
    results = runner.run_spotcheck(device, feed(), tmp_path, count=2, seed=7, analyzer=lambda p: {}, aliases=ALIASES, use_vision=False)
    again = runner.run_spotcheck(FakeDevice(routes), feed(), tmp_path / "again", count=2, seed=7, aliases=ALIASES, use_vision=False)
    assert len(results) == 2 and [r.banner_id for r in results] == [r.banner_id for r in again]
    assert device.current == "home"


def test_a_crashing_check_becomes_inconclusive_and_the_run_continues(tmp_path):
    class Boom(FakeDevice):
        def tap(self, box):
            raise RuntimeError("appium died")
    results = runner.run_spotcheck(Boom({}), feed(), tmp_path, count=2, seed=1, aliases=ALIASES, use_vision=False)
    assert len(results) == 2 and all(r.status is S.INCONCLUSIVE and "appium died" in r.reason for r in results)


def test_summary_flags_deviations_prominently_and_results_serialise(tmp_path):
    ok = runner.SpotResult("a", "Ajio Rush", RUSH, "PLP", S.CONFIRMED, "opened a product listing", "type_only", lp.PLP, "T")
    bad = runner.SpotResult("b", "HDFC", HDFC, "UNKNOWN", S.APP_DEVIATES_FROM_FEED, "tap_did_not_navigate", "type_only", lp.HOME)
    text = runner.format_summary([ok, bad])
    assert "2 banners tapped: APP_DEVIATES_FROM_FEED=1, CONFIRMED=1" in text
    assert "!!! APP_DEVIATES_FROM_FEED" in text and text.count("!!!") == 1
    assert json.loads(json.dumps([bad.to_dict()]))[0]["status"] == "APP_DEVIATES_FROM_FEED"
