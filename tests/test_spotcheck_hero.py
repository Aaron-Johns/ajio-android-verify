"""Hero carousel helpers (qa/spotcheck/hero.py)."""
import pytest

from qa.feed_client import Banner
from qa.spotcheck import hero


def banner(alt, dest, idx=0):
    return Banner(f"id{idx}", f"https://cdn/{idx}.webp", idx, dest, "hybrid-dynamic-banner", alt, 0, idx, alt_text=alt)


def test_hero_candidates_are_dynamic_banner_blocks_with_images():
    feed = [banner("a", "/s/a", 0),
            Banner("flat", "https://cdn/f.webp", 1, "/s/f", "hybrid-banner", "f", 0, None),
            Banner("noimg", None, 2, "/s/n", "hybrid-dynamic-banner", "n", 0, 3)]
    assert [b.banner_id for b in hero.hero_candidates(feed)] == ["id0"]


def test_transient_vision_failures_are_retried_but_unavailability_is_not(monkeypatch):
    from qa.spotcheck import vision
    monkeypatch.setattr(hero.time, "sleep", lambda s: None)
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
