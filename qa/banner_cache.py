"""Cross-run cache of banner verification results.

The same banner (same artwork, same link) keeps reappearing across runs - different carousel
positions, different l1/l2 segments, later days - and its vision reading + listing check would come
back identical every time. That's the expensive part of verify_banner() (one Gemma call and one
search-edge fetch per link), so this lets a repeat banner skip straight to the answer.

Identity = (perceptual image hash within a small Hamming-distance tolerance) AND (exact
destination_raw) AND (exact set of hotspot urls). The hash alone would be too loose - two different
banners can look alike - and the destination/hotspot match alone would be too strict - the same
banner is re-served from the CDN at slightly different compression/size across pulls. Together they
mean "this is genuinely the same banner", which is what licenses reusing its verdict rather than just
"this looks similar".

Cached to disk (qa/.cache/, gitignored - see cohort_client.py for the same pattern) so the saving
carries across runs, not just within one.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from PIL import Image

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "banner_image_cache.json"
HASH_SIZE = 8            # [A] 8x8 dhash grid - cheap, and plenty of resolution for "same banner or not"
MAX_DISTANCE = 4         # [A] Hamming-distance tolerance - absorbs recompression/resize noise between
                          # CDN pulls of the same artwork without being loose enough to match a different one
MAX_AGE_S = 24 * 3600     # [A] a cache hit older than this is not reused - the listing behind a link
                          # (price, brand mix, title) can drift, so a saving this old is treated as stale


def dhash(image_path: Path, hash_size: int = HASH_SIZE) -> int:
    """Difference hash: shrink to a (hash_size+1) x hash_size grayscale grid and record, for each
    pixel, whether it's darker than its right neighbour. Robust to resizing and recompression (unlike
    a byte/exact hash) without needing an extra dependency beyond the PIL this project already uses."""
    with Image.open(image_path) as im:
        im = im.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
        pixels = list(im.getdata())
    bits = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | int(pixels[offset + col] < pixels[offset + col + 1])
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _hotspot_key(hotspot_urls: list[str]) -> list[str]:
    return sorted(hotspot_urls)


def _touches_luxe(destination_raw: str | None, hotspot_urls: list[str]) -> bool:
    return any("://luxe." in (u or "").lower() or (u or "").lower().startswith(("luxe.", "//luxe."))
               for u in [destination_raw, *hotspot_urls])


class BannerCache:
    """Loaded once (per run, typically) and flushed to disk after every new entry, so an interrupted
    run keeps whatever savings it already found. Safe to share across threads within one run."""

    def __init__(self, path: Path = CACHE_PATH, max_distance: int = MAX_DISTANCE, max_age_s: float = MAX_AGE_S):
        self.path, self.max_distance, self.max_age_s = path, max_distance, max_age_s
        self._lock = threading.Lock()
        self.entries: list[dict] = []
        if path.exists():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.entries = []

    def find(self, image_hash: int, destination_raw: str | None, hotspot_urls: list[str]) -> dict | None:
        """The newest matching entry (destination/hotspots exact, image within tolerance, not stale),
        or None. Newest-first so a banner whose listing has since been re-verified doesn't keep
        matching an older saving forever once a fresher one exists."""
        want_hotspots = _hotspot_key(hotspot_urls)
        now = time.time()
        with self._lock:
            for e in sorted(self.entries, key=lambda e: e["cached_at"], reverse=True):
                if now - e["cached_at"] > self.max_age_s:
                    continue
                # a verdict for a luxe.ajio.com link saved before the Luxe store was used (no "store_aware" mark) was
                # judged against the wrong catalogue - never reuse it
                if not e.get("store_aware") and _touches_luxe(e.get("destination_raw"), e.get("hotspot_urls", [])):
                    continue
                if e.get("destination_raw") != (destination_raw or None):
                    continue
                if _hotspot_key(e.get("hotspot_urls", [])) != want_hotspots:
                    continue
                if _hamming(e["image_hash"], image_hash) <= self.max_distance:
                    return e
        return None

    def save(self, image_hash: int, destination_raw: str | None, hotspot_urls: list[str],
             own_check: dict | None, hotspot_checks: list[dict], source_banner_id: str, source_run: str) -> None:
        entry = {"image_hash": image_hash, "destination_raw": destination_raw or None,
                "hotspot_urls": _hotspot_key(hotspot_urls), "own_check": own_check,
                "hotspot_checks": [{k: v for k, v in h.items() if k not in ("hotspot_index", "image_file")}
                                   for h in hotspot_checks],
                "source_banner_id": source_banner_id, "source_run": source_run, "cached_at": time.time(),
                "store_aware": True}
        with self._lock:
            self.entries.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.entries, ensure_ascii=False), encoding="utf-8")


def clear_cache(path: Path = CACHE_PATH) -> int:
    """Deletes the on-disk cross-run cache - the web UI's "Clear cache" button, for forcing every
    banner fresh on the next run instead of waiting out MAX_AGE_S. Returns how many entries were
    cleared (0 if there was no cache file). Only a transient-free verdict is ever written here (see
    verify_banner's `not transient` check), so a flaky listing/vision error was never cached in the
    first place - this is for when the cached verdict itself needs redoing (e.g. the resolver or
    matching rules changed), not for un-sticking a one-off failure. Safe to call mid-run: a BannerCache
    already loaded into an active run keeps its in-memory entries regardless - only a run started after
    this call sees the empty cache."""
    if not path.exists():
        return 0
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        count = len(entries) if isinstance(entries, list) else 0
    except (ValueError, OSError):
        count = 0
    path.unlink()
    return count
