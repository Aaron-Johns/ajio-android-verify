"""Hero carousel: step through its slides, tap each, and capture the listing page it opens.

The hero slides carry no accessibility label, so a slide is matched to its feed banner by
comparing the on-screen image with the feed's images. The carousel auto-rotates; it is paused
(and stays paused across a listing round-trip) and advanced with its own arrow.
"""
from __future__ import annotations

import csv
import io
import logging
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from qa import deeplink_resolve as dl
from qa import listing_client as lc
from qa.compare import AliasMap, brand_key
from qa.export_banners import download_images
from qa.feed_client import Banner
from qa.listing_client import Listing
from qa.spotcheck import filters
from qa.spotcheck import landing as lp
from qa.spotcheck import plp, vision
from qa.spotcheck.device import parse_bounds
from qa.status import SpotStatus

log = logging.getLogger("qa.spotcheck.hero")
SLIDES_PER_RUN = 6        # [A] hero slides tapped per run
SERVER_PAGES = 2          # [A] listing pages (25 products each) read from the server per slide
MAX_MATCH_DISTANCE = 0.15  # mean grey-level difference (0-1) above which a slide is considered unmatched
MIN_MATCH_MARGIN = 0.02    # best match must beat the runner-up by this much
SIGNATURE_SIZE = (48, 32)


@dataclass
class HeroControls:
    region: tuple[int, int, int, int]
    slide: tuple[int, int, int, int]
    pause: tuple[int, int, int, int] | None
    prev: tuple[int, int, int, int] | None
    next: tuple[int, int, int, int] | None


@dataclass
class Match:
    banner: Banner | None
    distance: float
    margin: float


def find_hero_controls(source: str, width: int) -> HeroControls | None:
    """Locate the hero carousel (a wide, tall horizontal scroller) and its tappable parts."""
    root = ET.fromstring(source)
    boxes = [(n, parse_bounds(n.get("bounds") or "")) for n in root.iter()]
    regions = [b for n, b in boxes if b and n.get("class", "").endswith("HorizontalScrollView")
               and (b[2] - b[0]) >= 0.9 * width and (b[3] - b[1]) >= 400]
    if not regions:
        return None
    region = min(regions, key=lambda b: b[1])
    inside = [b for n, b in boxes if b and n.get("clickable") == "true" and n.get("content-desc", "") == ""
              and b[0] >= region[0] and b[1] >= region[1] and b[2] <= region[2] and b[3] <= region[3]]
    big = [b for b in inside if (b[2] - b[0]) >= 0.9 * (region[2] - region[0]) and (b[3] - b[1]) >= 0.8 * (region[3] - region[1])]
    if not big:
        return None
    small = [b for b in inside if (b[2] - b[0]) < 150 and (b[3] - b[1]) < 150]
    mid_y = (region[1] + region[3]) // 2
    pick = lambda cands: min(cands, key=lambda b: (b[1], b[0])) if cands else None  # noqa: E731
    return HeroControls(
        region=region, slide=max(big, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])),
        pause=pick([b for b in small if b[0] < 100 and b[1] < region[1] + 120]),
        prev=pick([b for b in small if b[0] < 60 and abs((b[1] + b[3]) // 2 - mid_y) < 150]),
        next=pick([b for b in small if b[2] > width - 60 and abs((b[1] + b[3]) // 2 - mid_y) < 150]),
    )


def signature(img: Image.Image) -> list[int]:
    return list(img.convert("L").resize(SIGNATURE_SIZE).tobytes())


def distance(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) / 255


def match_slide(crop: Image.Image, candidates: list[tuple[Banner, list[int]]]) -> Match:
    if not candidates:
        return Match(None, 1.0, 0.0)
    sig = signature(crop)
    ranked = sorted(((distance(sig, s), b) for b, s in candidates), key=lambda t: t[0])
    best, second = ranked[0][0], (ranked[1][0] if len(ranked) > 1 else 1.0)
    margin = second - best
    ok = best <= MAX_MATCH_DISTANCE and margin >= MIN_MATCH_MARGIN
    return Match(ranked[0][1] if ok else None, best, margin)


def hero_candidates(banners: list[Banner]) -> list[Banner]:
    return [b for b in banners if b.block_index is not None and b.section_type == "hybrid-dynamic-banner" and b.image_url]


def load_candidates(banners: list[Banner], data_dir: Path) -> list[tuple[Banner, list[int]]]:
    urls = sorted({b.image_url for b in banners})
    files = download_images(urls, data_dir / "images")
    out = []
    for b in banners:
        status, rel = files.get(b.image_url, ("missing", ""))
        if status == "OK" and rel:
            with Image.open(data_dir / rel) as img:
                out.append((b, signature(img)))
    return out


def crop_region(png: bytes, box: tuple[int, int, int, int]) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB").crop(box)


def ensure_paused(device, controls: HeroControls, wait: float = 5.0) -> str:
    """Pause auto-rotation if it is running (tapping pause on an already-paused carousel would resume it)."""
    a = signature(crop_region(device.screenshot_png(), controls.region))
    time.sleep(wait)
    b = signature(crop_region(device.screenshot_png(), controls.region))
    if distance(a, b) < 0.02:
        return "already_paused"
    if controls.pause is None:
        return "playing_but_no_pause_button"
    device.tap(controls.pause)
    time.sleep(1.0)
    return "paused"


def run_hero(device, banners: list[Banner], out_dir: Path, data_dir: Path, slides: int = SLIDES_PER_RUN,
             aliases: AliasMap | None = None, plp_screens: int = plp.MAX_SCREENS, analyzer=None,
             server_mode: bool = True) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    aliases = aliases or AliasMap.from_file()
    candidates = load_candidates(hero_candidates(banners), data_dir)

    device.return_home()
    device.scroll_to_top()
    _, _, source = device.state()
    controls = find_hero_controls(source, device.width)
    if controls is None:
        raise RuntimeError("could not find the hero carousel on the home screen")
    pause_state = ensure_paused(device, controls)

    results, first_sig = [], None
    for i in range(slides):
        if i > 0:
            if controls.next is None:
                break
            device.tap(controls.next)
            time.sleep(1.5)
        png = device.screenshot_png()
        crop = crop_region(png, controls.region)
        sig = signature(crop)
        if first_sig is None:
            first_sig = sig
        elif distance(sig, first_sig) < 0.02:
            results.append({"slide": i, "stopped": "carousel_wrapped_to_first_slide"})
            break
        hero_shot = out_dir / f"slide{i}_hero.png"
        crop.save(hero_shot)
        match = match_slide(crop, candidates)
        results.append(_tap_slide(device, i, controls, match, str(hero_shot), out_dir, aliases, plp_screens, pause_state, analyzer, server_mode))
        device.return_home()
    return results


def _analyze(analyzer, hero_shot: str, attempts: int = 3) -> dict:
    """Read the banner with the vision model, retrying transient failures (timeouts, 5xx)."""
    for attempt in range(1, attempts + 1):
        try:
            return analyzer(Path(hero_shot))
        except vision.VisionUnavailable:
            raise
        except Exception as exc:
            if attempt == attempts:
                raise
            log.warning("vision call failed (%s), retrying", type(exc).__name__)
            time.sleep(2.0 * attempt)


def _banner_check(device, hero_shot: str, title: str | None, aliases: AliasMap, analyzer) -> dict:
    """Banner brands must all be options in the listing's Brands filter; the title must match the banner's deal."""
    try:
        info = _analyze(analyzer, hero_shot)
        return filters.verify_against_banner(device, info, title, aliases)
    except vision.VisionUnavailable as exc:
        return {"result": "INCONCLUSIVE", "error": f"vision_unavailable: {exc}"}
    except Exception as exc:
        return {"result": "INCONCLUSIVE", "error": f"{type(exc).__name__}: {exc}"}


def _server_listing(banner: Banner | None) -> list[Listing] | None:
    """Fetch the listing the banner's feed link points at, straight from the server. None if not possible."""
    target = lc.listing_target(banner.destination_raw) if banner else None
    if not target:
        return None
    kind, slug = target
    store = lc.listing_store(banner.destination_raw)
    try:
        return [lc.fetch_listing(slug, page=p, kind=kind, store=store) for p in range(SERVER_PAGES)]
    except Exception as exc:
        log.warning("server listing fetch failed for %s: %s", slug, exc)
        return None


def _server_check(pages: list[Listing], hero_shot: str, aliases: AliasMap, analyzer, app_title: str | None):
    """Banner brands/deal vs the server's Brands facet and title; products come from the same response."""
    first = pages[0]
    try:
        check = filters.verify_from_listing(_analyze(analyzer, hero_shot), first.title, first.brands, aliases,
                                            first.genders, first.total_results)
    except vision.VisionUnavailable as exc:
        check = {"result": "INCONCLUSIVE", "error": f"vision_unavailable: {exc}"}
    except Exception as exc:
        check = {"result": "INCONCLUSIVE", "error": f"{type(exc).__name__}: {exc}"}
    rows = [r for pg in pages for r in lc.product_rows(pg)]
    products = [plp.Product(label=r["code"] or "", brand=r["brand"], name=r["name"], price=r["price"], mrp=r["mrp"],
                            discount_percent=r["discount_percent"], offer_price=r["offer_price"], rating=r["rating"])
                for r in rows]
    checks = plp.checks(plp.PlpData(plp.Header(title=first.title), products, len(pages)))
    check["app_title_matches_server"] = (brand_key(app_title) == brand_key(first.title)) if app_title and first.title else None
    return check, {"slug": first.slug, "title": first.title, "total_results": first.total_results,
                   "brands_in_filter": len(first.brands), "facets": first.facets, "checks": checks, "products": rows}


def _tap_slide(device, index: int, controls: HeroControls, match: Match, hero_shot: str, out_dir: Path,
               aliases: AliasMap, plp_screens: int, pause_state: str, analyzer=None, server_mode: bool = True) -> dict:
    analyzer = analyzer or (lambda p: vision.analyze_image(p, extra=vision.HERO_EXTRA))
    banner = match.banner
    record: dict = {
        "slide": index, "hero_screenshot": hero_shot, "carousel": pause_state,
        "matched_banner": None if banner is None else {
            "banner_id": banner.banner_id, "alt_text": banner.alt_text, "destination_raw": banner.destination_raw,
            "declared_type": dl.resolve(banner.destination_raw).type},
        "match_distance": round(match.distance, 4), "match_margin": round(match.margin, 4),
    }
    _, _, before = device.state()
    device.tap(controls.slide)
    package, activity, source = device.capture_after_tap(before)
    shot = device.screenshot(out_dir / f"slide{index}_landing.png")
    landing = lp.parse_landing(package, activity, source, before, shot)
    for _ in range(3):  # a listing still showing skeleton placeholders is not yet recognisable
        if landing.kind != lp.OTHER:
            break
        time.sleep(3.0)
        package, activity, source = device.state()
        shot = device.screenshot(out_dir / f"slide{index}_landing.png")
        landing = lp.parse_landing(package, activity, source, before, shot)
    record["landing"] = {"kind": landing.kind, "title": landing.title, "activity": landing.activity, "screenshot": shot}

    if landing.kind == lp.PLP:
        served = _server_listing(banner) if server_mode else None
        data = plp.capture_plp(device, out_dir, f"slide{index}", 1 if served else plp_screens)
        record["plp"] = {**data.to_dict(), "checks": plp.checks(data)}
        record["plp_ok"] = all(c["ok"] for c in record["plp"]["checks"])
        result = _server_check(served, hero_shot, aliases, analyzer, data.header.title) if served else None
        if result:
            record["banner_check"], record["server_listing"] = result
        else:
            record["banner_check"] = _banner_check(device, hero_shot, data.header.title, aliases, analyzer)

    if banner is None:
        record.update(status=SpotStatus.INCONCLUSIVE.value, reason="hero_slide_not_matched_to_a_feed_banner", evidence="none")
    else:
        judgement = lp.judge(dl.resolve(banner.destination_raw), landing, None, aliases)
        record.update(status=judgement.status.value, reason=judgement.reason, evidence=judgement.evidence)
    return record


def format_summary(results: list[dict]) -> str:
    lines = []
    for r in results:
        if "stopped" in r:
            lines.append(f"slide {r['slide']}: stopped ({r['stopped']})")
            continue
        m = r["matched_banner"]
        listing = r.get("plp")
        detail = ""
        if listing:
            failed = [c["check"] for c in listing["checks"] if not c["ok"]]
            detail = (f" | listing {listing['header']['title']!r} {listing['header']['subtitle']!r}: "
                      f"{len(listing['products'])} products, checks {'all OK' if not failed else 'FAILED ' + ', '.join(failed)}")
        bc = r.get("banner_check")
        if bc:
            detail += f" | banner check {bc['result']}" + (f" (missing brands {bc['missing_brands']})" if bc.get("missing_brands") else "") \
                + (" (title differs from banner deal)" if bc.get("title_matches_deal") is False else "")
            if bc.get("source") == "server":
                sl = r["server_listing"]
                detail += (f" | server listing {sl['title']!r}: {sl['total_results']} total, {len(sl['products'])} read, "
                           f"{sl['brands_in_filter']} brands in filter"
                           + (" | APP TITLE DIFFERS FROM SERVER" if bc.get("app_title_matches_server") is False else ""))
        flag = "!!! APP_DEVIATES_FROM_FEED" if r["status"] == SpotStatus.APP_DEVIATES_FROM_FEED.value else r["status"]
        who = f"{m['alt_text'][:40]!r} -> {m['destination_raw']}" if m else "(no feed match)"
        lines.append(f"slide {r['slide']}: {flag} | {who} | landed {r['landing']['kind']}{detail} | {r['reason']}")
    return "\n".join(lines)


def flatten_products(results: list[dict]) -> list[dict]:
    rows = []
    for r in results:
        for p in (r.get("plp") or {}).get("products", []):
            m = r.get("matched_banner") or {}
            rows.append({"slide": r["slide"], "banner_alt_text": m.get("alt_text", ""), "destination_raw": m.get("destination_raw", ""),
                         "listing_title": r["landing"]["title"] or "",
                         **{k: (" | ".join(v) if isinstance(v, list) else v) for k, v in p.items()}})
    return rows


def flatten_server_products(results: list[dict]) -> list[dict]:
    rows = []
    for r in results:
        m = r.get("matched_banner") or {}
        for p in (r.get("server_listing") or {}).get("products", []):
            rows.append({"slide": r["slide"], "banner_alt_text": m.get("alt_text", ""), "destination_raw": m.get("destination_raw", ""),
                         "listing_title": r["server_listing"]["title"] or "", **p})
    return rows


def write_outputs(results: list[dict], out_dir: Path) -> None:
    (out_dir / "hero_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    server_rows = flatten_server_products(results)
    if server_rows:
        columns = ["slide", "banner_alt_text", "destination_raw", "listing_title"] + lc.PRODUCT_COLUMNS
        with open(out_dir / "server_products.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(server_rows)
    plp.write_products_csv(flatten_products(results), out_dir / "products.csv",
                           ["slide", "banner_alt_text", "destination_raw", "listing_title"])
