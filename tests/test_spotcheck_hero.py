"""Hero carousel: control discovery on a real dump, image matching, pause handling, and a full fake run."""
import csv
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from qa.compare import AliasMap
from qa.feed_client import Banner
from qa.spotcheck import hero
from qa.spotcheck import landing as lp

FIX = Path(__file__).resolve().parent / "fixtures"
HOME_SRC = (FIX / "ui_home.xml").read_text(encoding="utf-8")
PLP_SRC = (FIX / "ui_plp.xml").read_text(encoding="utf-8")
HOME = (lp.APP_PACKAGE, ".home.AjioHomeActivity", HOME_SRC)
ALIASES = AliasMap([["LEVI'S", "LEVIS"]])


def pattern(kind: int) -> Image.Image:
    """Distinct synthetic 'banners' the size of the hero region."""
    img = Image.new("RGB", (1080, 709), (240, 240, 240))
    d = ImageDraw.Draw(img)
    if kind == 0:
        d.rectangle([0, 0, 540, 709], fill=(20, 20, 20))
    elif kind == 1:
        d.rectangle([0, 0, 1080, 300], fill=(200, 30, 30))
        d.ellipse([300, 350, 800, 650], fill=(30, 30, 200))
    else:
        for x in range(0, 1080, 120):
            d.rectangle([x, 0, x + 60, 709], fill=(30, 160, 30))
    return img


def NO_INFO(path):
    return {"brands_mentioned": [], "deal_offered": ""}


def banner(alt, dest, idx=0):
    return Banner(f"id{idx}", f"https://cdn/{idx}.webp", idx, dest, "hybrid-dynamic-banner", alt, 0, idx, alt_text=alt)


def test_hero_controls_are_found_in_the_real_home_dump():
    c = hero.find_hero_controls(HOME_SRC, 1080)
    assert c.region == c.slide == (0, 840, 1080, 1549)
    assert c.pause == (21, 861, 63, 903) and c.prev == (0, 1152, 42, 1236) and c.next == (1033, 1150, 1080, 1239)


def test_no_carousel_means_no_controls():
    assert hero.find_hero_controls("<hierarchy/>", 1080) is None


def test_slide_matches_its_feed_image_even_after_rescaling_and_noise():
    candidates = [(banner(str(k), f"/s/{k}", k), hero.signature(pattern(k))) for k in range(3)]
    noisy = pattern(1).resize((1024, 700)).resize((1080, 709))
    m = hero.match_slide(noisy, candidates)
    assert m.banner.banner_id == "id1" and m.distance < 0.05 and m.margin > 0.1


def test_unrelated_or_ambiguous_slides_are_not_matched():
    candidates = [(banner("a", "/s/a", 0), hero.signature(pattern(0)))]
    stranger = Image.new("RGB", (1080, 709), (10, 200, 200))
    assert hero.match_slide(stranger, candidates).banner is None
    twins = [(banner("a", "/s/a", 0), hero.signature(pattern(2))), (banner("b", "/s/b", 1), hero.signature(pattern(2)))]
    assert hero.match_slide(pattern(2), twins).banner is None          # identical candidates: refuse to guess
    assert hero.match_slide(pattern(2), []).banner is None


# ---------------- fake device for pause + full run ----------------

def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeDevice:
    """Home screen with a 2-slide carousel; tapping the slide opens a listing, back returns home."""
    width, height = 1080, 2424

    def __init__(self, playing=False):
        self.current, self.slide, self.taps, self.playing, self.tick = "home", 0, [], playing, 0
        self.controls = hero.find_hero_controls(HOME_SRC, 1080)

    def state(self):
        return HOME if self.current == "home" else (lp.APP_PACKAGE, ".home.AjioHomeActivity", PLP_SRC)

    def screenshot_png(self):
        self.tick += 1
        base = Image.new("RGB", (1080, 2424), (255, 255, 255))
        slide = pattern((self.slide + (self.tick if self.playing else 0)) % 2)
        base.paste(slide, (0, 840))
        return png(base)

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")
        return str(path)

    def tap(self, box):
        self.taps.append(box)
        if box == self.controls.pause:
            self.playing = False
        elif box == self.controls.next:
            self.slide = (self.slide + 1) % 2
        elif box == self.controls.slide:
            self.current = "plp"

    def scroll(self, *a, **k): pass
    def scroll_to_top(self): pass
    def capture_after_tap(self, before): return self.state()
    def return_home(self): self.current = "home"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hero.time, "sleep", lambda s: None)


def test_ensure_paused_only_taps_pause_when_the_carousel_is_running():
    running, idle = FakeDevice(playing=True), FakeDevice(playing=False)
    assert hero.ensure_paused(running, running.controls, wait=0) == "paused" and running.taps == [running.controls.pause]
    assert hero.ensure_paused(idle, idle.controls, wait=0) == "already_paused" and idle.taps == []


def test_run_hero_end_to_end(tmp_path, monkeypatch):
    feed = [banner("Rush", "https://www.ajio.com/s/4hoursdelivery-160865", 0), banner("Nike", "https://www.ajio.com/b/nike-1", 1)]
    monkeypatch.setattr(hero, "load_candidates", lambda banners, data_dir: [(b, hero.signature(pattern(b.block_index))) for b in banners])
    results = hero.run_hero(FakeDevice(), feed, tmp_path, tmp_path, slides=5, aliases=ALIASES, analyzer=NO_INFO, server_mode=False)

    assert [r.get("stopped") for r in results] == [None, None, "carousel_wrapped_to_first_slide"]
    s0, s1 = results[:2]
    assert s0["matched_banner"]["alt_text"] == "Rush" and s0["status"] == "CONFIRMED"
    assert s0["landing"]["title"] == "Delivery Starts in 30 Mins" and s0["plp_ok"] is True
    assert len(s0["plp"]["products"]) == 4 and s0["plp"]["header"]["subtitle"] == "10K+ Products"
    assert s1["matched_banner"]["alt_text"] == "Nike" and s1["status"] == "APP_DEVIATES_FROM_FEED"   # link says Nike, listing shows RIO
    assert "nike" in s1["reason"]

    text = hero.format_summary(results)
    assert "!!! APP_DEVIATES_FROM_FEED" in text and "checks all OK" in text and "stopped (carousel_wrapped" in text

    hero.write_outputs(results, tmp_path)
    assert json.loads((tmp_path / "hero_results.json").read_text(encoding="utf-8"))[0]["slide"] == 0
    with open(tmp_path / "products.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8 and rows[0]["banner_alt_text"] == "Rush" and rows[0]["brand"] == "RIO"


def test_unmatched_slide_still_yields_listing_data_but_is_inconclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(hero, "load_candidates", lambda banners, data_dir: [])
    results = hero.run_hero(FakeDevice(), [], tmp_path, tmp_path, slides=1, aliases=ALIASES, analyzer=NO_INFO, server_mode=False)
    r = results[0]
    assert r["matched_banner"] is None and r["status"] == "INCONCLUSIVE" and r["plp"]["products"]


def test_missing_carousel_is_reported(tmp_path, monkeypatch):
    class NoHero(FakeDevice):
        def state(self):
            return (lp.APP_PACKAGE, ".home.AjioHomeActivity", "<hierarchy/>")
    monkeypatch.setattr(hero, "load_candidates", lambda banners, data_dir: [])
    with pytest.raises(RuntimeError, match="hero carousel"):
        hero.run_hero(NoHero(), [], tmp_path, tmp_path, slides=1, analyzer=NO_INFO, server_mode=False)


def test_hero_candidates_are_dynamic_banner_blocks_with_images():
    feed = [banner("a", "/s/a", 0),
            Banner("flat", "https://cdn/f.webp", 1, "/s/f", "hybrid-banner", "f", 0, None),
            Banner("noimg", None, 2, "/s/n", "hybrid-dynamic-banner", "n", 0, 3)]
    assert [b.banner_id for b in hero.hero_candidates(feed)] == ["id0"]


# ---------------- server mode: listing data comes from the server, the emulator only taps ----------------

from qa import listing_client as lc  # noqa: E402


def _listing(title="Delivery Starts in 30 Mins", brands=None, page=0):
    prod = {"code": "p1", "fnlColorVariantData": {"brandName": "RIO"}, "name": "Tee", "price": {"value": 356.0},
            "wasPriceData": {"value": 699.0}, "discountPercent": "49% off", "offerPrice": {"value": 249.0},
            "averageRating": 3.6, "verticalNameText": "Tees", "segmentNameText": "Women", "url": "/x/p/1"}
    return lc.Listing("slug", title, 1234, page, brands or {"RIO": 10, "NIKE": 5}, {"Brands": 2},
                      [dict(prod, code=f"p{page}a"), dict(prod, code=f"p{page}b")])


def test_server_mode_reads_the_listing_from_the_server_and_only_taps_in_the_app(tmp_path, monkeypatch):
    feed = [banner("Rush", "https://www.ajio.com/s/4hoursdelivery-160865", 0)]
    calls = []
    monkeypatch.setattr(hero.lc, "fetch_listing", lambda slug, page=0, kind="curated", **k: calls.append((slug, page, kind)) or _listing(page=page))
    monkeypatch.setattr(hero, "load_candidates", lambda banners, data_dir: [(b, hero.signature(pattern(0))) for b in banners])
    info = {"brands_mentioned": ["Rio", "Nike"], "deal_offered": "Delivery Starts in 30 Mins"}
    d = FakeDevice()
    results = hero.run_hero(d, feed, tmp_path, tmp_path, slides=1, aliases=ALIASES, analyzer=lambda p: info)
    r = results[0]
    assert calls == [("4hoursdelivery-160865", 0, "curated"), ("4hoursdelivery-160865", 1, "curated")]
    assert r["banner_check"]["source"] == "server" and r["banner_check"]["result"] == "PASS"
    assert r["banner_check"]["app_title_matches_server"] is True
    assert r["server_listing"]["total_results"] == 1234 and len(r["server_listing"]["products"]) == 4
    assert r["server_listing"]["products"][0]["brand"] == "RIO" and r["server_listing"]["products"][0]["discount_percent"] == 49
    assert all(c["ok"] for c in r["server_listing"]["checks"])
    assert len(r["plp"]["screenshots"]) == 1          # one screen read in the app, no scrolling

    hero.write_outputs(results, tmp_path)
    with open(tmp_path / "server_products.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4 and rows[0]["listing_title"] == "Delivery Starts in 30 Mins" and rows[0]["price"] == "356"
    assert "server listing" in hero.format_summary(results)


def test_server_mode_fails_when_a_banner_brand_is_not_in_the_servers_brand_filter(tmp_path, monkeypatch):
    feed = [banner("Rush", "https://www.ajio.com/s/4hoursdelivery-160865", 0)]
    monkeypatch.setattr(hero.lc, "fetch_listing", lambda *a, **k: _listing(brands={"RIO": 10}))
    monkeypatch.setattr(hero, "load_candidates", lambda banners, data_dir: [(b, hero.signature(pattern(0))) for b in banners])
    info = {"brands_mentioned": ["Rio", "Nyrika Acai"], "deal_offered": "Delivery Starts in 30 Mins"}
    r = hero.run_hero(FakeDevice(), feed, tmp_path, tmp_path, slides=1, aliases=ALIASES, analyzer=lambda p: info)[0]
    assert r["banner_check"]["result"] == "FAIL" and r["banner_check"]["missing_brands"] == ["Nyrika Acai"]


def test_server_failure_falls_back_to_reading_the_app(tmp_path, monkeypatch):
    feed = [banner("Rush", "https://www.ajio.com/s/4hoursdelivery-160865", 0)]
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(hero.lc, "fetch_listing", boom)
    monkeypatch.setattr(hero, "load_candidates", lambda banners, data_dir: [(b, hero.signature(pattern(0))) for b in banners])
    r = hero.run_hero(FakeDevice(), feed, tmp_path, tmp_path, slides=1, aliases=ALIASES, analyzer=NO_INFO)[0]
    assert "server_listing" not in r and r["banner_check"]["result"] == "INCONCLUSIVE" and len(r["plp"]["screenshots"]) >= 1


def test_transient_vision_failures_are_retried_but_unavailability_is_not(monkeypatch):
    from qa.spotcheck import vision
    calls = []

    def flaky(path):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("slow")
        return {"brands_mentioned": ["X"], "deal_offered": "MIN. 40% OFF"}
    assert hero._analyze(flaky, "p.png")["brands_mentioned"] == ["X"] and len(calls) == 3

    def gone(path):
        raise vision.VisionUnavailable("no key")
    with pytest.raises(vision.VisionUnavailable):
        hero._analyze(gone, "p.png")
    with pytest.raises(TimeoutError):
        hero._analyze(lambda p: (_ for _ in ()).throw(TimeoutError("x")), "p.png", attempts=2)
