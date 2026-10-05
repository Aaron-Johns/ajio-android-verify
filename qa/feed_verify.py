"""Verify feed banners against the listing pages they link to, with no emulator.

For each banner: download its image from the CDN, read the promoted brands and the deal with the
vision model, fetch the linked listing (title + Brands filter) from the search-edge API, then require
every banner brand to be a Brands-filter option and the listing title to match the deal.
Read-only: one image GET, one listing GET (cached per link) and one model call per banner.

Run:  python -m qa.feed_verify [--scope hero|all] [--limit N] [--workers 3] [--from-run runs/<stamp>]
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from qa import asset_set, deal_sort_check, listing_client as lc, reference_check
from qa.pages import DEFAULT_PAGE, PAGE_IDS
from qa.banner_cache import BannerCache, dhash
from qa.compare import AliasMap
from qa.feed_client import RUNS_DIR, Banner, Hotspot, RunLog, fetch_banners, fetch_image, parse_banners
from qa.spotcheck import filters, hero, vision

log = logging.getLogger("qa.feed_verify")

WORKERS = 12           # [A] banners verified in parallel (mostly waiting on the vision model: ~90 s a call, so 12 keep 7 calls a minute busy)
LISTING_PAUSE = 0.3    # [A] seconds between listing fetches; they are serialized to stay gentle on the API
PREMIUM_WARNING = "Premium cannot be loaded: AJIO kept returning the regular banner set, so this run checked that set instead."
RETRY_ROUNDS = 4       # [A] extra tries per banner that fails on a temporary error (Gemini 5xx, network, listing API)
RETRY_PAUSE = 10.0     # [A] seconds a failed banner cools down before a free worker may retry it (was 60 s per whole retry pass)
CONFIRM_ATTEMPTS = 8   # [A] max feed re-fetches while waiting for a target asset_set (FINDINGS 6.10 - per-request, not
CONFIRM_PAUSE = 2.0    # [A] per-identity, so re-fetching is the only lever); this is a plain feed fetch, not the full check
_EXT = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}




def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _truncated(items: list[str], n: int = 3) -> str:
    """First n items, then '...' if there were more - a listing's extra-brands list can run to dozens
    of entries and a reason line that long is unreadable in the CLI/CSV/UI. Mirrors web/static/index.html's
    truncatedList() so the same list reads the same way everywhere it's shown."""
    if not items:
        return "[]"
    shown = ", ".join(repr(x) for x in items[:n])
    return f"[{shown}, ...]" if len(items) > n else f"[{shown}]"


def select_banners(banners: list[Banner], scope: str = "hero") -> list[Banner]:
    """hero = the hero carousel's image blocks; all = every banner with an image and at least one
    checkable link - either its own destination or one of its hotspots'. A banner whose own tap
    target isn't a listing link (or has none at all) but whose hotspots are real listing links is
    still fully checkable via those hotspots, so it belongs in "all" scope too - only a banner with
    nothing checkable anywhere on it should be dropped."""
    chosen = hero.hero_candidates(banners) if scope == "hero" else [b for b in banners if b.image_url]
    if scope == "all":
        chosen = [b for b in chosen
                 if lc.listing_target(b.destination_raw) or any(lc.listing_target(hs.url) for hs in b.hotspots)]
    return chosen


def section_id(banner: Banner) -> str:
    """The CMS section's own _id - a banner_id is "<_id>" for a flat banner or "<_id>:<block index>" for
    one slide of a carousel (qa/feed_client.py). Unlike section_index (a position in this particular
    pull's section list, which shifts whenever a section is added or removed), the _id is stable across
    pulls - so it's what a *saved* exclusion (a schedule's) has to key on."""
    return banner.banner_id.split(":", 1)[0]


def exclude_banners(banners: list[Banner], excluded_carousels: set[int] | None = None,
                    excluded_sections: set[str] | None = None) -> list[Banner]:
    """Drop every banner in an excluded carousel, by this pull's section_index (an interactive run's
    checklist, made from a fresh preview a moment earlier) and/or by stable section _id (a schedule's
    saved exclusions). Shared by run_feed_verify and web/runner.run_view so both agree on which banners
    a run covers."""
    return [b for b in banners
            if b.section_index not in (excluded_carousels or ()) and section_id(b) not in (excluded_sections or ())]


def fetch_confirmed_banners(slug: str = "home", target_asset_set: str | None = None,
                            max_attempts: int = CONFIRM_ATTEMPTS, pause: float = CONFIRM_PAUSE,
                            run_log: RunLog | None = None, fetcher: Callable = fetch_banners,
                            sleep: Callable = time.sleep) -> tuple[list[Banner], int, bool]:
    """Plain fetcher() if target_asset_set is falsy. Otherwise keeps re-fetching the feed
    (cheap - no vision/listing calls) until at least one image banner's asset_set (qa/asset_set.py)
    matches, up to max_attempts, then returns that pull. FINDINGS 6.10: which asset set comes back
    for e.g. l1:premium was shown to vary per-request even with l1/l2/device-id all held identical -
    so retrying the fetch is the only known lever, not a device-id or user-groups fix.

    Returns (banners, attempts_used, confirmed) - confirmed is False if max_attempts was exhausted
    without a match; the caller gets the last pull back anyway rather than nothing, but should treat
    the run as suspect for whatever this was targeting."""
    banners: list[Banner] = []
    for attempt in range(1, max_attempts + 1):
        banners = fetcher(slug, run_log=run_log)
        if not target_asset_set:
            return banners, attempt, True
        if any(asset_set.detect_asset_set(b.image_url) == target_asset_set for b in banners if b.image_url):
            return banners, attempt, True
        if attempt < max_attempts:
            sleep(pause)
    return banners, max_attempts, False


class ListingCache:
    """One fetch per distinct link, serialized with a short pause between real fetches."""

    def __init__(self, fetcher: Callable = lc.fetch_listing, pause: float = LISTING_PAUSE):
        self.fetcher, self.pause, self._lock, self._cache = fetcher, pause, threading.Lock(), {}

    def get(self, kind: str, slug: str, store: str | None = None, sort: str | None = None, page: int = 0,
            page_size: int | None = None) -> lc.Listing:
        """`store` (lc.listing_store) is part of the key: the same slug is a different catalogue in the Luxe store.
        `sort` / `page` / `page_size` ask for a sorted page instead (qa/deal_sort_check.py); each combination is cached."""
        with self._lock:
            key = (kind, slug, store, sort, page, page_size)
            if key not in self._cache:
                try:
                    extra = {**({"store": store} if store else {}),
                             **({"sort": sort, "page": page, "page_size": page_size} if sort else {})}
                    self._cache[key] = self.fetcher(slug, kind=kind, **extra)   # failures are not cached, so a retry round re-fetches
                finally:
                    time.sleep(self.pause)
            return self._cache[key]


def _result(base: dict, result: str, reason: str = "", **extra) -> dict:
    return {**base, "result": result, "reason": reason, **extra}


def _plain(text) -> str:
    """Words Gemma read off a banner's picture end up in the reason, which the classic UI shows as HTML: keep letters, digits and
    ordinary punctuation only (no < > & quotes), and a sane length."""
    return re.sub(r"[<>&\"'`\\\x00-\x1f]", "", str(text or "")).strip()[:40]


def _spelling_errors(info: dict) -> list[dict]:
    """The misspellings Gemma listed, cleaned: a word with a different correction, nothing else (it sometimes lists a word as its own fix)."""
    found = []
    for e in (info.get("spelling_errors") or [])[:10]:
        word, fix = (_plain(e.get("word")), _plain(e.get("correction"))) if isinstance(e, dict) else ("", "")
        if word and fix and word.lower() != fix.lower():
            found.append({"word": word, "correction": fix})
    return found


def spelling_text(c: dict) -> str:
    """One line for a saved banner_check's misspellings ("" when there are none): shared by the reason, the CLI summary and the UIs' wording."""
    return "; ".join(f'spelling: "{e["word"]}" should be "{e["correction"]}"' for e in c.get("spelling_errors") or [])


def _verify_image(image_path: Path, destination_raw: str | None, analyzer: Callable, listings: ListingCache,
                  aliases: AliasMap, gender_override: str | None = None, note: Callable[[str], None] = lambda text: None,
                  reading: str = "Reading image", check_spelling: bool = True) -> dict:
    """The vision + listing + filters check shared by a banner's own image and each of its hotspot
    crops (see verify_banner). Callers handle "no destination"/"no image" themselves - those mean
    different things depending on whether this is the banner's own check or one hotspot among several.

    gender_override: for a hotspot crop, the audience reading is taken from the *main* banner image
    instead of the crop's own vision reading - a crop can cut off the gender cue (text/imagery) that
    only appears once, on the full banner, while the brand/deal being promoted really is specific to
    that one hotspot - see verify_banner. None (the default, used for the banner's own image) keeps
    whatever the analyzed image's own reading was."""
    target = lc.listing_target(destination_raw)
    if not target:
        return {"result": "SKIPPED", "reason": "not_a_listing_link"}
    kind, slug = target
    store = lc.listing_store(destination_raw)      # a luxe.ajio.com link is looked up in the Luxe store
    note("Listing lookup")
    try:
        listing = listings.get(kind, slug, store)
    except Exception as exc:
        return {"result": "INCONCLUSIVE", "reason": f"listing_fetch_failed: {type(exc).__name__}: {exc}",
                "listing_kind": kind, "slug": slug}
    note(reading)
    try:
        info = hero._analyze(analyzer, str(image_path))
    except vision.VisionUnavailable as exc:
        return {"result": "INCONCLUSIVE", "reason": f"vision_unavailable: {exc}", "listing_kind": kind, "slug": slug,
                "listing_title": listing.title}
    except Exception as exc:
        return {"result": "INCONCLUSIVE", "reason": f"vision_failed: {type(exc).__name__}: {exc}",
                "listing_kind": kind, "slug": slug, "listing_title": listing.title}
    if gender_override:
        info = {**info, "target_gender": gender_override}
    note("Comparing")
    check = filters.verify_from_listing(info, listing.title, listing.brands, aliases, listing.genders, listing.total_results)
    sort_check = deal_sort_check.check(info.get("deal_offered"),
                                       lambda sort, page, size: listings.get(kind, slug, store, sort, page, size),
                                       listing.discount_ranges)
    if sort_check:
        check["sort_check"] = sort_check
        # a real mismatch is a finding; the beauty / unrecognised-audience INCONCLUSIVE is left as it is (a person looks anyway)
        if sort_check["status"] == "MISMATCH" and not isinstance(check.get("gender_matches"), str):
            check["result"] = "FAIL"
    # a misspelt word on the banner is a finding of its own, whatever the listing says. Only the banner's own image is read for
    # it: a hotspot crop can cut a word in half, and that would be flagged as a typo.
    if check_spelling and (misspelt := _spelling_errors(info)):
        check["spelling_errors"] = misspelt
        check["result"] = "FAIL"
    return {"result": check["result"], "reason": "", "listing_kind": kind, "slug": slug, "listing_title": listing.title,
            "total_results": listing.total_results, "brands_in_filter": len(listing.brands), "banner_check": check,
            **({"listing_store": store} if store else {})}


def _apply_reference(result: dict, banner: Banner, row, listings: "ListingCache", aliases: AliasMap) -> dict:
    """Judge the banner against its reference row (qa/reference_check.py) and record the outcome as `reference_check`.
    A MISMATCH is a concrete finding: a banner that would have PASSed (or was INCONCLUSIVE/SKIPPED) becomes FAIL, and one
    that already FAILs keeps its own reasons - the reference finding is shown next to them. Runs after (and independently
    of) the cross-run cache, so an edited expectation applies to the very next check. A banner that was never checked
    (hidden, user-skipped) is left alone."""
    if row is None or (result["result"] == "SKIPPED" and (result.get("reason", "").startswith("hidden") or result.get("reason") == "user_skipped")):
        return result
    target = lc.listing_target(banner.destination_raw)

    def get_listing():
        kind, slug = target
        return listings.get(kind, slug, lc.listing_store(banner.destination_raw))

    try:
        chk = reference_check.check(row, banner.destination_raw, get_listing, aliases)
    except Exception as exc:                        # a broken row must never lose the banner's own result
        log.warning("reference check for %s failed: %s: %s", banner.banner_id, type(exc).__name__, exc)
        return result
    if chk is None:
        return result
    result = {**result, "reference_check": chk}
    if chk["status"] == reference_check.MISMATCH and result["result"] != "FAIL":
        # a transient error on the banner's own check is still retried first: it may not be checkable yet
        if not is_retryable(result):
            result = {**result, "result": "FAIL", "reason": chk["reason"] if not result.get("reason") else f"{result['reason']}; {chk['reason']}"}
    return result


def _crop_hotspot(image_path: Path, banner: Banner, hs, out_dir: Path, index: int) -> Path | None:
    """Crop image_path to hs's bounding box, scaled from the CMS's declared image_width/image_height
    to the actual downloaded image's real pixel size (a CDN transform isn't guaranteed to match what
    the feed declared - see Hotspot's docstring). Returns None if the box is empty after clamping to
    the image's bounds, rather than raising - a bad box shouldn't crash the whole banner's check."""
    from PIL import Image as PILImage
    with PILImage.open(image_path) as im:
        im = im.convert("RGB")
        sx = im.width / banner.image_width if banner.image_width else 1
        sy = im.height / banner.image_height if banner.image_height else 1
        box = (max(0, hs.x * sx), max(0, hs.y * sy),
              min(im.width, (hs.x + hs.width) * sx), min(im.height, (hs.y + hs.height) * sy))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        crop = im.crop(tuple(round(v) for v in box))
        crop_path = out_dir / "images" / f"{_safe(banner.banner_id)}__hs{index}{image_path.suffix}"
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(crop_path)
        return crop_path


def undecided_reason(c: dict) -> str:
    """Why a banner whose checks found nothing wrong is still INCONCLUSIVE (qa/spotcheck/filters._finish). Read off the saved
    banner_check, so it also explains results saved before reasons were written. "" when there is nothing to say."""
    if not c:
        return ""
    if c.get("gender_matches") == "AJIO_BEAUTY":
        return "AJIO beauty banner - gender check skipped, needs a human look"
    if c.get("gender_matches") == "INCONCLUSIVE":
        return f"banner's gender reading {c.get('banner_gender')!r} isn't a recognized audience"
    has_brands = bool(c.get("banner_brands"))
    if c.get("title_matches_deal") is None:
        return ("banner has no deal text, so the page title can't be checked (its brands and audience are fine)" if has_brands
                else "banner names no brand and shows no deal text, so there is nothing to compare with the page")
    if not has_brands:
        return "banner names no brand, so the page's brand list can't be checked (the deal matches the page title)"
    return ""


def _hotspot_reason(h: dict) -> str:
    if h.get("reason"):
        return h["reason"]
    c = h.get("banner_check") or {}
    bits = list(filter(None, [
        f"missing brands {c['missing_brands']}" if c.get("missing_brands") else "",
        "title doesn't match the deal" if c.get("title_matches_deal") is False else "",
        f"banner targets {c.get('banner_gender')!r} but listing genders are {c.get('listing_genders')}"
        if c.get("gender_matches") is False else "",
        f"extra brands in the listing: {c['extra_brands']}" if c.get("extra_brands") else "",
        spelling_text(c),
        (c.get("sort_check") or {}).get("reason") if (c.get("sort_check") or {}).get("status") == "MISMATCH" else "",
        (c.get("sort_check") or {}).get("reason") if (c.get("sort_check") or {}).get("note") else ""]))
    if not bits and h.get("result") == "INCONCLUSIVE":
        return undecided_reason(c)
    return "; ".join(bits)


_TRANSIENT = ("image_download_failed", "listing_fetch_failed", "vision_failed")   # not vision_unavailable: no key won't fix itself


def _is_transient_check(check: dict | None) -> bool:
    return bool(check) and any(t in (check.get("reason") or "") for t in _TRANSIENT)


def _banner_base(banner: Banner) -> dict:
    return {"banner_id": banner.banner_id, "alt_text": banner.alt_text, "destination_raw": banner.destination_raw,
            "image_url": banner.image_url, "asset_set": asset_set.detect_asset_set(banner.image_url),
            "section_index": banner.section_index, "block_index": banner.block_index, "position": banner.position}


def skip_requested(out_dir: Path, banner_id: str) -> bool:
    """True if the web UI's per-banner Skip button (web/runner.request_skip) has asked for this banner
    to be left alone. Re-read from disk on every call (cheap - a handful of ids) rather than cached,
    since a skip can arrive from the API process at any point during this process's run."""
    path = out_dir / "skip_requests.json"
    if not path.exists():
        return False
    try:
        ids = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return banner_id in ids


def verify_banner(banner: Banner, out_dir: Path, analyzer: Callable, listings: ListingCache, aliases: AliasMap,
                  image_fetcher: Callable = fetch_image, cache: BannerCache | None = None, run_id: str = "",
                  note: Callable[[str], None] = lambda text: None, prior: dict | None = None) -> dict:
    """`note(text)` is told, in one or two words, what this banner is doing as it moves through the steps - the
    web UI shows it live on the card (see ActivityLog).

    `prior` (the previous try's result for this banner): on a retry, every hotspot that was checked properly last time
    is kept as it is - only a hotspot that hit a temporary error is redone, so a 6-link banner with one glitched link
    costs one vision call, not six."""
    base = _banner_base(banner)
    if banner.hidden:
        return _result(base, "SKIPPED", f"hidden: {banner.hidden_reason}" if banner.hidden_reason else "hidden")
    own_target = lc.listing_target(banner.destination_raw)
    if not own_target and not banner.hotspots:
        return _result(base, "SKIPPED", "not_a_listing_link")
    if not banner.image_url:
        return _result(base, "SKIPPED", "banner_has_no_image")

    images_dir = out_dir / "images"
    image = next(iter(images_dir.glob(f"{_safe(banner.banner_id)}.*")), None) if images_dir.exists() else None
    if image is None:   # already downloaded on an earlier attempt: reuse it
        note("Downloading")
        status, data, ctype = image_fetcher(banner.image_url)
        if data is None:
            return _result(base, "INCONCLUSIVE", f"image_download_failed: {status}")
        image = images_dir / f"{_safe(banner.banner_id)}{_EXT.get(ctype, '.img')}"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(data)
    base["image_file"] = str(image)

    hotspot_urls = [hs.url for hs in banner.hotspots]
    cache_hit = None
    image_hash = None
    if cache is not None:
        note("Cache check")
        try:
            image_hash = dhash(image)
        except Exception:
            image_hash = None   # an unreadable/corrupt download shouldn't block verification, just skip caching
        if image_hash is not None:
            cache_hit = cache.find(image_hash, banner.destination_raw, hotspot_urls)

    if cache_hit is not None:
        receipt = {"banner_id": cache_hit["source_banner_id"], "run_id": cache_hit["source_run"],
                  "cached_at": cache_hit["cached_at"]}
        own_check = copy.deepcopy(cache_hit["own_check"]) if own_target and cache_hit.get("own_check") else None
        if own_check is not None:
            own_check["reused_from"] = receipt
        cached_by_url = {h["url"]: h for h in cache_hit.get("hotspot_checks", [])}
        hotspot_checks = []
        for i, hs in enumerate(banner.hotspots):
            crop = _crop_hotspot(image, banner, hs, out_dir, i)   # still crop fresh - this run's UI serves its own files
            if crop is None:
                hotspot_checks.append({"hotspot_index": i, "url": hs.url,
                                       "result": "INCONCLUSIVE", "reason": "empty_bounding_box_after_scaling"})
                continue
            hc = copy.deepcopy(cached_by_url.get(hs.url)) or {"result": "INCONCLUSIVE", "reason": "cache_hit_missing_hotspot_data"}
            hc["reused_from"] = receipt
            hotspot_checks.append({"hotspot_index": i, "url": hs.url, "image_file": str(crop), **hc})
    else:
        own_check = _verify_image(image, banner.destination_raw, analyzer, listings, aliases, note=note) if own_target else None

        # A hotspot crop can cut off the gender cue (text/imagery) that only appears once, on the
        # full banner - so every hotspot's audience reading is taken from the main image instead of
        # its own crop; only the brand/deal stay specific to that hotspot's own cropped area. Reuse
        # own_check's already-computed reading when there is one, rather than paying for a second
        # vision call on the same full image.
        kept = {c["hotspot_index"]: c for c in (prior or {}).get("hotspot_checks", [])
                if "hotspot_index" in c and not _is_transient_check(c)}
        redo = [i for i, hs in enumerate(banner.hotspots) if kept.get(i, {}).get("url") != hs.url]
        main_gender = None
        if redo:                     # the main image's audience reading is only needed for a hotspot that gets (re)checked
            if own_check and own_check.get("banner_check"):
                main_gender = own_check["banner_check"].get("banner_gender")
            else:
                note("Reading image")
                try:
                    main_gender = hero._analyze(analyzer, str(image)).get("target_gender")
                except Exception:
                    main_gender = None   # couldn't read the main banner at all - hotspots fall back to their own crop's reading

        hotspot_checks = []
        for i, hs in enumerate(banner.hotspots):
            if i not in redo:
                hotspot_checks.append(copy.deepcopy(kept[i]))
                continue
            crop = _crop_hotspot(image, banner, hs, out_dir, i)
            if crop is None:
                hotspot_checks.append({"hotspot_index": i, "url": hs.url,
                                       "result": "INCONCLUSIVE", "reason": "empty_bounding_box_after_scaling"})
                continue
            hc = _verify_image(crop, hs.url, analyzer, listings, aliases, gender_override=main_gender,
                               note=note, reading="Reading hotspot", check_spelling=False)
            hotspot_checks.append({"hotspot_index": i, "url": hs.url, "image_file": str(crop), **hc})

        # don't cache a transient failure (a Gemini 5xx, a network blip) as if it were the real verdict -
        # that would both serve a wrong answer to future banners and rob this one of its own retry rounds
        transient = _is_transient_check(own_check) or any(_is_transient_check(h) for h in hotspot_checks)
        if cache is not None and image_hash is not None and not transient:
            cache.save(image_hash, banner.destination_raw, hotspot_urls, own_check, hotspot_checks,
                      banner.banner_id, run_id)

    all_results = ([own_check["result"]] if own_check else []) + [h["result"] for h in hotspot_checks]
    if not all_results or all(r == "SKIPPED" for r in all_results):
        # own destination missing/absent AND every hotspot's own link was a non-listing URL too -
        # nothing was actually checkable, so the banner is SKIPPED overall, not a vacuous PASS.
        overall = "SKIPPED"
    elif "FAIL" in all_results:
        overall = "FAIL"
    elif "INCONCLUSIVE" in all_results:
        overall = "INCONCLUSIVE"
    else:
        overall = "PASS"

    reasons = []
    if own_check and own_check["result"] == overall:
        why = own_check.get("reason") or _hotspot_reason(own_check)
        if why:
            reasons.append(why)
    for h in hotspot_checks:
        if h["result"] == overall:
            why = _hotspot_reason(h)
            if why:
                reasons.append(f"hotspot {h['hotspot_index']} ({h['url']}): {why}")
    reason = "; ".join(reasons)

    extra = {k: v for k, v in (own_check or {}).items() if k not in ("result", "reason")}
    return _result(base, overall, reason, hotspot_checks=hotspot_checks, **extra)


def is_retryable(result: dict) -> bool:
    """True if this banner's failure looks temporary (Gemini 5xx, a network blip, the listing API) and
    is worth trying again. Checks the combined reason text and, separately, every hotspot's own reason -
    with multiple hotspots the top-level reason only surfaces the ones matching the overall result, so a
    transient failure on a hotspot that didn't end up "winning" the aggregation could otherwise be missed."""
    hotspots_transient = any(_is_transient_check(h) for h in result.get("hotspot_checks", []))
    if result["result"] == "FAIL":
        # A finding stays a finding, but if one of its hotspots never got checked (Gemma 5xx, network) the banner is
        # not fully verified: worth another try, which redoes only that hotspot (see verify_banner's `prior`).
        return hotspots_transient
    if result["result"] != "INCONCLUSIVE":
        return False
    return any(t in result.get("reason", "") for t in _TRANSIENT) or hotspots_transient


UNAVAILABLE = "UNAVAILABLE"    # shown status: AJIO / Google / the network didn't answer - says nothing about the banner


def shown_result(result: dict, live: bool = False, max_tries: int = RETRY_ROUNDS + 1) -> str:
    """The status a person sees for a saved result. The pipeline keeps recording a temporary server/network failure as
    INCONCLUSIVE + a transient reason (that is what the retry logic keys on), but once its automatic retries are used up
    it isn't a finding about the banner at all - it's UNAVAILABLE, kept out of the "needs a look" pile and offered a
    Retry instead. While a live run still has tries left for it, it's PROCESSING. Everything else is its own result."""
    if not is_retryable(result):
        return result["result"]
    if live and result.get("attempts", 1) < max_tries:
        return "PROCESSING"
    return UNAVAILABLE if result["result"] == "INCONCLUSIVE" else result["result"]     # a FAIL with an unchecked hotspot is still a FAIL


def shown_hotspot_result(check: dict) -> str:
    """The same idea for one hotspot's own check: INCONCLUSIVE because AJIO / Google / the network didn't answer is
    UNAVAILABLE (not a finding about that link)."""
    return UNAVAILABLE if check.get("result") == "INCONCLUSIVE" and _is_transient_check(check) else check.get("result")


class PartialLog:
    """Appends each banner's result to partial.jsonl the moment it is known, so an interrupted run loses nothing."""

    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()

    def __call__(self, result: dict) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()


class ActivityLog:
    """Appends what each banner is doing right now to activity.jsonl (one line per step change), for the web UI's
    live card tag. Every activity is one or two words. `until` (epoch seconds) is only set for "Cooling down": the
    reader turns it into "Retry queued" once that time has passed. Separate from partial.jsonl, which only ever
    holds results."""

    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()

    def __call__(self, banner_id: str, activity: str, until: float | None = None) -> None:
        line = json.dumps({"banner_id": banner_id, "activity": activity, "at": time.time(), "until": until})
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()


def load_activity(out_dir: Path) -> dict[str, dict]:
    """banner_id -> its latest activity line. A line cut off mid-write is ignored."""
    path, latest = out_dir / "activity.jsonl", {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            latest[a["banner_id"]] = a
    return latest


def load_partial(out_dir: Path) -> dict[str, dict]:
    """banner_id -> latest saved result. A line cut off by a crash is ignored."""
    path, latest = out_dir / "partial.jsonl", {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            latest[r["banner_id"]] = r
    return latest


def load_final_results(out_dir: Path) -> dict[str, dict]:
    """banner_id -> the latest known result of a run folder: results.json once the run finished (its order kept), with any
    later try from partial.jsonl laid over it. A per-banner Retry only appends to partial.jsonl - it must never rewrite a
    finished run's results.json (the parent may still be alive) - so without this overlay a retry on a finished run would
    be invisible. A retry starts counting its tries from 1 again, so "newer" is `tried_at` (older results, saved without
    it, fall back to the higher `attempts`)."""
    final: dict[str, dict] = {}
    results_file = out_dir / "results.json"
    if results_file.exists():
        final = {r["banner_id"]: r for r in json.loads(results_file.read_text(encoding="utf-8"))}
    for bid, r in load_partial(out_dir).items():
        if bid not in final:
            if not results_file.exists():
                final[bid] = r
        elif (r.get("tried_at", 0), r.get("attempts", 1)) > (final[bid].get("tried_at", 0), final[bid].get("attempts", 1)):
            final[bid] = r
    return final


def save_banners(out_dir: Path, banners: list[Banner]) -> None:
    (out_dir / "banners.json").write_text(json.dumps([asdict(b) for b in banners], ensure_ascii=False), encoding="utf-8")


def load_banners(out_dir: Path) -> list[Banner]:
    """asdict() (in save_banners) flattens Hotspot dataclasses to plain dicts for JSON - rebuild
    them here so callers get real Banner/Hotspot objects back, not banners with dict soup inside."""
    records = json.loads((out_dir / "banners.json").read_text(encoding="utf-8"))
    for d in records:
        d["hotspots"] = [Hotspot(**h) for h in d.get("hotspots", [])]
    return [Banner(**d) for d in records]


def run_feed_verify(banners: list[Banner], out_dir: Path, analyzer: Callable | None = None, aliases: AliasMap | None = None,
                    workers: int = WORKERS, listing_fetcher: Callable = lc.fetch_listing, image_fetcher: Callable = fetch_image,
                    limit: int | None = None, scope: str = "hero", retry_rounds: int = RETRY_ROUNDS,
                    retry_pause: float = RETRY_PAUSE, previous: dict[str, dict] | None = None,
                    on_result: Callable[[dict], None] | None = None,
                    on_activity: Callable[..., None] | None = None,
                    cache: BannerCache | bool | None = True, only: set[str] | None = None,
                    excluded_carousels: set[int] | None = None,
                    excluded_sections: set[str] | None = None,
                    reference: dict | None = None) -> list[dict]:
    """Verify every chosen banner; ones that fail on a temporary error go straight back in the queue and are
    retried by the next free worker (after `retry_pause` seconds), ahead of banners not yet tried.

    `previous` (banner_id -> earlier result) resumes an interrupted run: finished banners are reused as they are,
    and only the missing or temporarily-failed ones are done again. `on_result` sees each new result immediately.

    `cache`: True (default) uses the real cross-run cache (qa/banner_cache.py, qa/.cache/ on disk) so a
    repeat banner (same image, same link) skips the vision+listing work and reuses its earlier verdict.
    Pass a BannerCache instance to point it elsewhere (tests), or False/None to verify everything fresh.

    `only` (banner_id set): restricts which banners are actually (re)computed to this subset - every other
    banner's `previous` result is carried through to the return value untouched. For the web UI's per-banner
    "Retry" button (see web/runner.py's retry_banner): --resume-from + --only-banner so a single stuck banner
    can be redone without touching the rest of an otherwise-finished run's results.

    `excluded_carousels` (section_index set): banners in these carousels are dropped before anything else -
    they never enter `chosen`, so unlike `only` there's no None placeholder for them in the return value at
    all, no image download, no result of any kind. For the web UI's carousel checklist (see web/runner.py's
    list_carousels/start_run): unlike a banner_limit cutoff, this is a deliberate per-carousel opt-out, not
    "the first N banners in scope".

    `excluded_sections` (CMS section _id set): the same opt-out, keyed on the section's stable _id instead
    of this pull's positional section_index - what a saved schedule uses, since the index shifts whenever
    the feed gains or loses a section (see section_id).

    `reference` (banner_id -> qa.reference.ReferenceRow): the human's expectation of what a banner should lead to. Each
    banner with a row is also judged against it (qa/reference_check.py); a mismatch makes it FAIL.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    aliases = aliases or AliasMap.from_file()
    analyzer = analyzer or (lambda p: vision.analyze_image(p, extra=vision.HERO_EXTRA))
    chosen = exclude_banners(select_banners(banners, scope), excluded_carousels, excluded_sections)
    chosen = chosen[:limit]
    listings = ListingCache(listing_fetcher)
    if cache is True:
        cache = BannerCache()
    elif not cache:
        cache = None
    previous = previous or {}
    attempts = {bid: r.get("attempts", 1) for bid, r in previous.items()}
    for bid in only or ():          # an explicit per-banner Retry is a fresh start: its tries count from 1 again, not on from the old run's
        attempts[bid] = 0

    def verify_one(b: Banner, retries_so_far: int = 0, prior: dict | None = None) -> dict:
        # Checked twice: before, so a banner still queued (no work started) is never sent at all; and
        # after, so a skip that arrives while this banner's own worker is mid-call (image/listing/vision)
        # discards that work instead of recording it - the call itself can't be safely interrupted once
        # it's blocked on a socket, but its result never gets kept once a skip lands for it.
        if skip_requested(out_dir, b.banner_id):
            r = _result(_banner_base(b), "SKIPPED", "user_skipped")
        else:
            note = (lambda text: on_activity(b.banner_id, text)) if on_activity else (lambda text: None)
            with vision.reporting(note) as note:      # so a Gemma request waiting its turn shows "Waiting turn" on the card
                r = verify_banner(b, out_dir, analyzer, listings, aliases, image_fetcher, cache=cache, run_id=out_dir.name, note=note,
                                  prior=prior)
            if skip_requested(out_dir, b.banner_id):
                r = _result(_banner_base(b), "SKIPPED", "user_skipped")
            elif reference:
                note("Reference check")
                r = _apply_reference(r, b, reference.get(b.banner_id), listings, aliases)
        attempts[b.banner_id] = r["attempts"] = attempts.get(b.banner_id, 0) + 1
        r["tried_at"] = time.time()
        if retries_so_far and not is_retryable(r):
            r["recovered_in_retry_round"] = retries_so_far
        if on_result:
            on_result(r)
        return r

    def _wanted(i: int, r: dict | None) -> bool:
        if only is not None:               # an explicit per-banner retry: redo it whatever its result was (a FAIL, an INCONCLUSIVE...)
            return chosen[i].banner_id in only
        return r is None or is_retryable(r)

    results: list[dict | None] = [previous.get(b.banner_id) for b in chosen]
    todo = [i for i, r in enumerate(results) if _wanted(i, r)]

    # One shared queue, not "a pass, then a retry pass". Each free worker takes, in this order: a failed banner
    # whose cooldown (retry_pause) is over - it goes back in the moment it fails, ahead of banners that haven't
    # had their first try - else the next untried banner in feed order. A failed banner still cooling down never
    # holds a worker: they carry on with untried banners. Every banner gets 1 + retry_rounds tries at most.
    fresh = deque(todo)
    cooling: list[tuple[float, int]] = []    # (ready_at, index) of failed banners waiting out retry_pause, oldest first
    tries: dict[int, int] = {}
    active = 0
    cond = threading.Condition()

    def take() -> int | None:
        nonlocal active
        with cond:
            while True:
                now = time.monotonic()
                ready = next((k for k, (at, _) in enumerate(cooling) if at <= now), None)
                if ready is not None:
                    i = cooling.pop(ready)[1]
                elif fresh:
                    i = fresh.popleft()
                elif cooling:                   # only cooling banners left: sleep until the first is due
                    cond.wait(timeout=max(0.0, min(at for at, _ in cooling) - now))
                    continue
                elif active:                    # nothing queued, but a banner in flight may still fail and come back
                    cond.wait()
                    continue
                else:
                    return None
                active += 1
                return i

    def worker() -> None:
        nonlocal active
        while (i := take()) is not None:
            requeue = False
            try:
                tries[i] = tries.get(i, 0) + 1
                # results[i] = the previous try, if any. Its properly-checked hotspots are kept when a run resumes or a temporary
                # error is retried - but an explicit per-banner retry (`only`) starts from scratch on its first try
                prior = None if (only is not None and tries[i] == 1) else results[i]
                r = results[i] = verify_one(chosen[i], tries[i] - 1, prior)
                requeue = is_retryable(r) and tries[i] <= retry_rounds and (only is None or chosen[i].banner_id in only)
                if requeue:
                    log.warning("banner %s failed on a temporary error (try %d of %d); back in the queue",
                                chosen[i].banner_id, tries[i], retry_rounds + 1)
                    if on_activity:
                        on_activity(chosen[i].banner_id, "Cooling down", until=time.time() + retry_pause)
            finally:
                with cond:
                    if requeue:
                        cooling.append((time.monotonic() + retry_pause, i))
                    active -= 1
                    cond.notify_all()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in [pool.submit(worker) for _ in range(max(1, workers))]:
            future.result()
    return results


def _hotspot_summary(h: dict) -> str:
    why = _hotspot_reason(h)
    return f"{h['hotspot_index']}:{h['url']}:{shown_hotspot_result(h)}" + (f" ({why})" if why else "")


def write_outputs(results: list[dict], out_dir: Path) -> None:
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def format_summary(results: list[dict]) -> str:
    counts = Counter(shown_result(r) for r in results)
    lines = [f"{len(results)} banners: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    for r in results:
        if shown_result(r) in ("PASS", "SKIPPED"):
            continue
        c = r.get("banner_check") or {}
        why = r.get("reason") or "; ".join(filter(None, [
            f"missing brands {c['missing_brands']}" if c.get("missing_brands") else "",
            f"title {r.get('listing_title')!r} != deal {c.get('banner_deal')!r}" if c.get("title_matches_deal") is False else "",
            f"banner targets {c.get('banner_gender')!r} but listing genders are {c.get('listing_genders')}"
            if c.get("gender_matches") is False else "",
            f"listing has extra brands not named on the banner: {_truncated(c['extra_brands'])}" if c.get("extra_brands") else "",
            spelling_text(c)])
            ) or (undecided_reason(c) if shown_result(r) == "INCONCLUSIVE" else "")
        ref = reference_check.note(r)             # what the reference CSV says, beside whichever reasons the banner already has
        if ref and ref not in why:
            why = f"{why}; {ref}" if why else ref
        lines.append(f"{shown_result(r):12} {r.get('alt_text', '')[:38]!r:40} {r.get('destination_raw')} | {why}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify feed banners against their listing pages (no emulator)")
    ap.add_argument("--scope", choices=["hero", "all"], default="hero")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--retry-rounds", type=int, default=RETRY_ROUNDS)
    ap.add_argument("--retry-pause", type=float, default=RETRY_PAUSE)
    ap.add_argument("--page", choices=PAGE_IDS, default=DEFAULT_PAGE,
                    help="which app page's feed to check (qa/pages.py); only home depends on the l1/l2 cohort")
    ap.add_argument("--from-run", type=Path, help="use this run's saved response for --page (default home) instead of a live feed call")
    ap.add_argument("--resume-from", type=Path, help="continue an interrupted run folder: redo only banners with no result yet "
                                                     "or a temporary failure, reusing its banner list and images")
    ap.add_argument("--confirm-asset-set", choices=sorted(asset_set.KNOWN_SETS),
                    help="keep re-fetching the feed (cheap - no vision/listing calls) until at least one banner's "
                         "asset_set matches this, before running the full check - see FINDINGS 6.10; useful for "
                         "'premium', where which set comes back varies per-request")
    ap.add_argument("--confirm-attempts", type=int, default=CONFIRM_ATTEMPTS)
    ap.add_argument("--confirm-pause", type=float, default=CONFIRM_PAUSE)
    ap.add_argument("--no-cache", action="store_true",
                    help="verify every banner fresh, ignoring qa/.cache/banner_image_cache.json - a repeat banner "
                         "(same image, same link) normally reuses its earlier vision+listing verdict instead of "
                         "redoing it (see qa/banner_cache.py)")
    ap.add_argument("--reference", type=Path, metavar="CSV",
                    help="reference CSV of what banners should lead to (qa/reference.py); default config/reference.csv when it "
                         "exists. Every banner with a row is also judged against it - see qa/reference_check.py")
    ap.add_argument("--no-reference", action="store_true", help="ignore the reference CSV for this run")
    ap.add_argument("--only-banner", action="append", metavar="BANNER_ID",
                    help="requires --resume-from: redo just this banner_id (repeatable) instead of every banner "
                        "that's missing or temporarily failed - every other banner's saved result is left as-is")
    ap.add_argument("--list-carousels", action="store_true",
                    help="fetch the feed, print every banner in --scope (with its section_index/label carousel "
                        "grouping) as JSON, and exit - no verification, no run folder. For the web UI's carousel "
                        "preview (see web/runner.py's list_carousels) to show before a real run starts")
    ap.add_argument("--exclude-carousel", action="append", type=int, metavar="SECTION_INDEX",
                    help="drop every banner in this carousel (repeatable) before verifying anything - see "
                        "run_feed_verify's excluded_carousels")
    ap.add_argument("--exclude-section", action="append", metavar="SECTION_ID",
                    help="like --exclude-carousel but keyed on the CMS section's stable _id instead of this pull's "
                        "positional section_index (repeatable) - what a saved schedule passes, see section_id")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    if args.only_banner and not args.resume_from:
        ap.error("--only-banner requires --resume-from")
    if args.list_carousels and args.resume_from:
        ap.error("--list-carousels fetches a fresh feed and can't be combined with --resume-from")

    warnings: list[str] = []      # things the person running the check should see; saved as warnings.json in the run folder
    if args.resume_from:
        out_dir, banners, previous = args.resume_from, load_banners(args.resume_from), load_partial(args.resume_from)
        print(f"resuming {out_dir}: {len(previous)} banner results already saved")
    else:
        out_dir = RUNS_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_feedverify"
        if args.from_run:
            banners = parse_banners(json.loads((args.from_run / f"{args.page}.response.json").read_text(encoding="utf-8")))
        else:
            banners, attempts, confirmed = fetch_confirmed_banners(
                args.page, args.confirm_asset_set, args.confirm_attempts, args.confirm_pause, run_log=RunLog())
            if args.confirm_asset_set:
                if confirmed:
                    print(f"confirmed a {args.confirm_asset_set!r} banner after {attempts} feed fetch(es)")
                else:
                    print("Premium cannot be loaded")     # the run still goes on with the last pull
                    warnings.append(PREMIUM_WARNING)
        if args.list_carousels:
            rows = [{"banner_id": b.banner_id, "alt_text": b.alt_text, "destination_raw": b.destination_raw,
                    "image_url": b.image_url, "section_index": b.section_index, "section_id": section_id(b),
                    "block_index": b.block_index, "position": b.position, "label": b.label,
                    "total_links": (1 if b.destination_raw else 0) + len(b.hotspots),
                    "hidden": b.hidden, "hidden_reason": b.hidden_reason}
                   for b in select_banners(banners, args.scope)]
            print(json.dumps(rows, ensure_ascii=False))
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        save_banners(out_dir, banners)
        if warnings:
            (out_dir / "warnings.json").write_text(json.dumps(warnings), encoding="utf-8")
        previous = None
    reference, ref_path = ({}, None) if args.no_reference else reference_check.load_default(args.reference)
    if ref_path:
        print(f"reference: {len(reference)} banner rows with expectations from {ref_path}")
    started = time.time()
    results = run_feed_verify(banners, out_dir, workers=args.workers, reference=reference or None, limit=args.limit, scope=args.scope,
                              retry_rounds=args.retry_rounds, retry_pause=args.retry_pause, previous=previous,
                              on_result=PartialLog(out_dir / "partial.jsonl"),
                              on_activity=ActivityLog(out_dir / "activity.jsonl"), cache=not args.no_cache,
                              only=set(args.only_banner) if args.only_banner else None,
                              excluded_carousels=set(args.exclude_carousel) if args.exclude_carousel else None,
                              excluded_sections=set(args.exclude_section) if args.exclude_section else None)
    if not args.only_banner:
        # A --only-banner retry's `results` list is deliberately incomplete (every banner outside
        # `only` is just carried over from the `previous` snapshot taken at startup - see `_wanted`
        # in run_feed_verify) - fine to return/print, but never write it as *the* results.json: if
        # the parent run this is retrying against is still alive, its own banners-in-progress are
        # still None here, and writing that out clobbers the live run's results.json with holes
        # (crashing anything that reads it) until the parent's own eventual write_outputs overwrites
        # it back. The one retried banner is already durably saved via on_result's PartialLog append.
        write_outputs(results, out_dir)
    print(format_summary(results))
    print(f"\n{time.time() - started:.0f}s | results.json and images: {out_dir}")


if __name__ == "__main__":
    main()
