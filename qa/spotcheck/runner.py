"""Phase 5 spot-check: tap sampled banners on the emulator and confirm the app lands where the feed said.

Run (Appium server + emulator up):  python -m qa.spotcheck.runner [--count 5] [--seed N] [--no-vision]

Banners are matched to on-screen elements by the feed's altText, which the app exposes as the
element's accessibility label. Only banners currently visible after scrolling, whose altText is
unique both in the feed and on screen, can be sampled.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from qa import deeplink_resolve as dl
from qa.compare import AliasMap, compare_banner
from qa.export_banners import fetch_image
from qa.feed_client import RUNS_DIR, Banner, RunLog, fetch_banners, parse_banners
from qa.reference import ReferenceRow, load_reference
from qa.spotcheck import device as dev
from qa.spotcheck import hero
from qa.spotcheck import landing as lp
from qa.spotcheck import vision
from qa.status import SpotStatus, Status

log = logging.getLogger("qa.spotcheck")

BANNERS_PER_RUN = 5   # [A] CLAUDE.md section 9 item 5 (weekly cadence is applied by the Phase 6 scheduler)
MAX_SCANS = 6         # screens scrolled while looking for candidates / a specific banner


@dataclass
class SpotResult:
    banner_id: str
    alt_text: str
    destination_raw: str | None
    declared_type: str
    status: SpotStatus
    reason: str
    evidence: str = "none"
    landing_kind: str | None = None
    landing_title: str | None = None
    landing_activity: str | None = None
    screenshot: str | None = None
    phase4_status: str | None = None
    vision: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def locate(device, alt_text: str) -> tuple[int, int, int, int] | None:
    """Scroll from the top until exactly one visible clickable element carries this label."""
    device.scroll_to_top()
    for _ in range(MAX_SCANS):
        _, _, source = device.state()
        boxes = dev.clickable_descs(source, device.width, device.height).get(alt_text, [])
        if len(boxes) == 1:
            return boxes[0]
        if len(boxes) > 1:
            return None  # ambiguous label on screen
        device.scroll("down")
    return None


def collect_candidates(device, banners: list[Banner], wanted: int) -> list[Banner]:
    """Feed banners that can be found on screen by label (unique in the feed and on screen)."""
    feed_counts = Counter(b.alt_text for b in banners if b.alt_text)
    by_alt = {b.alt_text: b for b in banners if b.alt_text and feed_counts[b.alt_text] == 1 and b.destination_raw}
    found: dict[str, Banner] = {}
    device.scroll_to_top()
    for _ in range(MAX_SCANS):
        _, _, source = device.state()
        for alt, boxes in dev.clickable_descs(source, device.width, device.height).items():
            if alt in by_alt and len(boxes) == 1:
                found[by_alt[alt].banner_id] = by_alt[alt]
        if len(found) >= wanted * 3:
            break
        device.scroll("down")
    return list(found.values())


def _vision_check(banner: Banner, shot: str | None, out_dir: Path, analyzer, aliases) -> tuple[lp.VisionVerdict | None, dict]:
    try:
        if not banner.image_url or not shot:
            raise vision.VisionUnavailable("no banner image or landing screenshot to analyse")
        status, data, _ = fetch_image(banner.image_url)
        if data is None:
            raise vision.VisionUnavailable(f"banner image download failed: {status}")
        banner_path = out_dir / f"{_safe(banner.banner_id)}_banner.webp"
        banner_path.write_bytes(data)
        banner_res, landing_res = analyzer(banner_path), analyzer(Path(shot))
        verdict = vision.compare_banner_to_landing(banner_res, landing_res, aliases)
        return verdict, {"banner": banner_res, "landing": landing_res,
                         "verdict": verdict.verdict if verdict else "NO_COMPARABLE_BRANDS"}
    except vision.VisionUnavailable as exc:
        return None, {"unavailable": str(exc)}
    except Exception as exc:  # a failed model call must not sink the whole check
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def check_banner(device, banner: Banner, out_dir: Path, analyzer: Callable, aliases: AliasMap,
                 reference: ReferenceRow | None = None, resolver=None, use_vision: bool = True) -> SpotResult:
    target = dl.resolve(banner.destination_raw)
    base = dict(banner_id=banner.banner_id, alt_text=banner.alt_text,
                destination_raw=banner.destination_raw, declared_type=target.type)

    box = locate(device, banner.alt_text)
    if box is None:
        return SpotResult(**base, status=SpotStatus.INCONCLUSIVE, reason="banner_not_found_uniquely_on_screen")

    landing = None
    for attempt in (1, 2):
        _, _, before = device.state()
        device.tap(box)
        package, activity, source = device.capture_after_tap(before)
        shot = device.screenshot(out_dir / f"{_safe(banner.banner_id)}.png")
        landing = lp.parse_landing(package, activity, source, before, shot)
        if landing.kind != lp.HOME:
            break
        box = locate(device, banner.alt_text) or box  # the first tap may have missed; look again

    phase4 = compare_banner(banner.banner_id, banner.destination_raw, reference, resolver, aliases) if reference and resolver else None
    ambiguous = not target.resolved or (phase4 is not None and phase4.status is Status.AMBIGUOUS_DEEPLINK)
    verdict, vision_info = (None, {})
    if ambiguous and use_vision:
        verdict, vision_info = _vision_check(banner, landing.screenshot, out_dir, analyzer, aliases)

    judgement = lp.judge(target, landing, verdict, aliases)
    return SpotResult(**base, status=judgement.status, reason=judgement.reason, evidence=judgement.evidence,
                      landing_kind=landing.kind, landing_title=landing.title, landing_activity=landing.activity,
                      screenshot=landing.screenshot, phase4_status=phase4.status.value if phase4 else None,
                      vision=vision_info)


def run_spotcheck(device, banners: list[Banner], out_dir: Path, count: int = BANNERS_PER_RUN, seed: int | None = None,
                  analyzer: Callable | None = None, aliases: AliasMap | None = None,
                  references: dict[str, ReferenceRow] | None = None, use_vision: bool = True) -> list[SpotResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    aliases = aliases or AliasMap.from_file()
    analyzer = analyzer or vision.analyze_image
    references = references or {}
    resolver = None
    if references:
        from qa.brand_resolver import BrandResolver
        resolver = BrandResolver()

    device.return_home()
    candidates = collect_candidates(device, banners, count)
    picked = random.Random(seed).sample(candidates, min(count, len(candidates)))
    log.info("%d tappable candidates on screen, checking %d", len(candidates), len(picked))

    results: list[SpotResult] = []
    for banner in picked:
        try:
            result = check_banner(device, banner, out_dir, analyzer, aliases, references.get(banner.banner_id), resolver, use_vision)
        except Exception as exc:
            result = SpotResult(banner.banner_id, banner.alt_text, banner.destination_raw,
                                dl.resolve(banner.destination_raw).type, SpotStatus.INCONCLUSIVE,
                                f"error: {type(exc).__name__}: {exc}")
        results.append(result)
        try:
            device.return_home()
        except Exception as exc:
            log.warning("could not return to home after %s: %s", banner.banner_id, exc)
    return results


def format_summary(results: list[SpotResult]) -> str:
    counts = Counter(r.status.value for r in results)
    lines = [f"{len(results)} banners tapped: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    for r in results:
        flag = "!!! APP_DEVIATES_FROM_FEED" if r.status is SpotStatus.APP_DEVIATES_FROM_FEED else r.status.value
        lines.append(f"{flag:27} {r.alt_text[:34]!r:36} declared {r.declared_type:8} {r.destination_raw} -> "
                     f"landed {r.landing_kind} {r.landing_title!r}: {r.reason} [{r.evidence}]")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Tap sampled feed banners on the emulator and confirm where they land")
    ap.add_argument("--count", type=int, default=BANNERS_PER_RUN)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--from-run", type=Path, help="use this run's saved home response instead of a live feed call")
    ap.add_argument("--reference", type=Path, help="reference CSV (enables Phase 4 ambiguity checks)")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--hero", action="store_true", help="step through the hero carousel and capture each listing page")
    ap.add_argument("--slides", type=int, default=hero.SLIDES_PER_RUN, help="hero slides to tap (with --hero)")
    ap.add_argument("--app-filters", action="store_true", help="with --hero: scroll and search the filter in the app instead of reading the listing from the server")
    ap.add_argument("--server", default=dev.APPIUM_SERVER)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RUNS_DIR / f"{stamp}_{'hero' if args.hero else 'spotcheck'}"
    if args.from_run:
        banners = parse_banners(json.loads((args.from_run / "home.response.json").read_text(encoding="utf-8")))
    else:
        banners = fetch_banners("home", run_log=RunLog())

    if args.hero:
        with dev.Device(args.server) as device:
            results = hero.run_hero(device, banners, out_dir, RUNS_DIR.parent / "data", args.slides,
                                     server_mode=not args.app_filters)
        hero.write_outputs(results, out_dir)
        print(hero.format_summary(results))
        print(f"\nresults, products.csv and screenshots: {out_dir}")
        return

    references = load_reference(args.reference) if args.reference else {}

    with dev.Device(args.server) as device:
        results = run_spotcheck(device, banners, out_dir, args.count, args.seed, references=references,
                                use_vision=not args.no_vision)

    (out_dir / "spotcheck.json").write_text(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False), encoding="utf-8")
    print(format_summary(results))
    print(f"\nresults and screenshots: {out_dir}")


if __name__ == "__main__":
    main()
