"""qa/banner_cache.py: the cross-run cache that lets a repeat banner (same image, same link) skip
straight to its earlier verdict instead of redoing the vision+listing check."""
import io
import time

import pytest
from PIL import Image as PILImage

from qa.banner_cache import BannerCache, clear_cache, dhash


def make_image(path, color="blue", w=200, h=100):
    PILImage.new("RGB", (w, h), color).save(path, format="PNG")
    return path


def make_gradient(path, horizontal=True, w=200, h=100):
    """A solid color is dhash's worst case (every neighbour is equal, so every bit is 0) - a gradient
    gives the left-to-right/top-to-bottom comparisons dhash actually relies on something real to read."""
    im = PILImage.new("L", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = int(255 * (x / w if horizontal else y / h))
    im.convert("RGB").save(path, format="PNG")
    return path


def test_dhash_is_stable_for_the_same_image(tmp_path):
    p = make_image(tmp_path / "a.png")
    assert dhash(p) == dhash(p)


def test_dhash_differs_for_different_looking_images(tmp_path):
    a = make_gradient(tmp_path / "a.png", horizontal=True)
    b = make_gradient(tmp_path / "b.png", horizontal=False)
    assert dhash(a) != dhash(b)


def test_find_hits_on_an_exact_match(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src-banner", "src-run")
    hit = cache.find(img, "https://ajio.com/s/x-1", [])
    assert hit is not None and hit["own_check"] == {"result": "PASS"}


def test_find_misses_when_destination_differs(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src-banner", "src-run")
    assert cache.find(img, "https://ajio.com/s/x-2", []) is None


def test_find_misses_when_hotspot_urls_differ(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, None, ["https://ajio.com/s/h-1"], None, [], "src-banner", "src-run")
    assert cache.find(img, None, ["https://ajio.com/s/h-2"]) is None
    assert cache.find(img, None, []) is None


def test_find_ignores_hotspot_order(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, None, ["https://ajio.com/s/h-1", "https://ajio.com/s/h-2"], None, [], "src-banner", "src-run")
    assert cache.find(img, None, ["https://ajio.com/s/h-2", "https://ajio.com/s/h-1"]) is not None


def test_find_tolerates_a_small_hamming_distance(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json", max_distance=4)
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src", "run")
    # a hash 2 bits off the stored one - within tolerance, should still hit
    assert cache.find(img ^ 0b11, "https://ajio.com/s/x-1", []) is not None


def test_find_rejects_a_hash_too_far_off(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json", max_distance=2)
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src", "run")
    far = img ^ 0xFF   # 8 bits flipped, well past a tolerance of 2
    assert cache.find(far, "https://ajio.com/s/x-1", []) is None


def test_find_ignores_a_stale_entry(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json", max_age_s=1)
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src", "run")
    cache.entries[0]["cached_at"] = time.time() - 10   # simulate an old saving without sleeping in the test
    assert cache.find(img, "https://ajio.com/s/x-1", []) is None


def test_a_fresh_cache_instance_reads_what_a_previous_one_saved(tmp_path):
    path = tmp_path / "cache.json"
    img = dhash(make_image(tmp_path / "a.png"))
    BannerCache(path=path).save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src", "run")
    reloaded = BannerCache(path=path)
    assert reloaded.find(img, "https://ajio.com/s/x-1", []) is not None


def test_a_missing_cache_file_starts_empty_not_an_error(tmp_path):
    cache = BannerCache(path=tmp_path / "does_not_exist.json")
    assert cache.entries == []


def test_hotspot_checks_are_saved_without_the_per_run_hotspot_index_or_image_file(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    hc = [{"hotspot_index": 0, "url": "https://ajio.com/s/h-1", "image_file": "runs/x/images/h0.webp", "result": "PASS"}]
    cache.save(img, None, ["https://ajio.com/s/h-1"], None, hc, "src", "run")
    saved = cache.entries[0]["hotspot_checks"][0]
    assert "hotspot_index" not in saved and "image_file" not in saved
    assert saved["url"] == "https://ajio.com/s/h-1" and saved["result"] == "PASS"


def test_clear_cache_deletes_the_file_and_reports_how_many_entries_it_held(tmp_path):
    path = tmp_path / "cache.json"
    img = dhash(make_image(tmp_path / "a.png"))
    cache = BannerCache(path=path)
    cache.save(img, "https://ajio.com/s/x-1", [], {"result": "PASS"}, [], "src", "run")
    cache.save(img, "https://ajio.com/s/x-2", [], {"result": "FAIL"}, [], "src2", "run")
    assert clear_cache(path) == 2
    assert not path.exists()
    assert BannerCache(path=path).entries == []


def test_clear_cache_on_a_missing_file_is_a_harmless_no_op(tmp_path):
    assert clear_cache(tmp_path / "does_not_exist.json") == 0


# ---- verdicts for luxe.ajio.com links saved before the Luxe store was used are not trusted ----------------

def _old_luxe_entry(cache, img, dest="https://luxe.ajio.com/s/bossluxe15-407847"):
    cache.save(img, dest, [], {"result": "FAIL", "reason": "missing brands ['BOSS']"}, [], "old", "old-run")
    cache.entries[-1].pop("store_aware")            # exactly what an entry written by the earlier code looks like


def test_an_old_luxe_verdict_is_never_reused(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    _old_luxe_entry(cache, img)
    assert cache.find(img, "https://luxe.ajio.com/s/bossluxe15-407847", []) is None


def test_an_old_verdict_with_a_luxe_hotspot_is_not_reused_either(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://ajio.com/s/x-1", ["https://luxe.ajio.com/s/y-2"], {"result": "PASS"}, [], "old", "old-run")
    cache.entries[-1].pop("store_aware")
    assert cache.find(img, "https://ajio.com/s/x-1", ["https://luxe.ajio.com/s/y-2"]) is None


def test_a_new_luxe_verdict_is_reused(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://luxe.ajio.com/s/bossluxe15-407847", [], {"result": "PASS"}, [], "new", "new-run")
    assert cache.find(img, "https://luxe.ajio.com/s/bossluxe15-407847", []) is not None


def test_old_verdicts_for_ordinary_links_are_still_reused(tmp_path):
    cache = BannerCache(path=tmp_path / "cache.json")
    img = dhash(make_image(tmp_path / "a.png"))
    cache.save(img, "https://www.ajio.com/s/x-1", [], {"result": "PASS"}, [], "old", "old-run")
    cache.entries[-1].pop("store_aware")
    assert cache.find(img, "https://www.ajio.com/s/x-1", []) is not None
