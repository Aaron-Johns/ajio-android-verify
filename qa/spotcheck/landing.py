"""Classify the screen the app landed on after a banner tap, and judge it against the feed.

The AJIO app is a single-activity React Native shell: navigating to a listing page does NOT
change the Android activity, so the landing type is read from the accessibility tree
(native resource ids such as plp_sort_by_view / toolbar_title_tv) rather than the activity name.
Only in-app WebViews (CMS pages) and other apps show up as a different activity/package.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from qa import deeplink_resolve as dl
from qa.brand_resolver import normalize_brand
from qa.compare import AliasMap
from qa.status import SpotStatus

APP_PACKAGE = "com.ril.ajio"

PLP = "PLP"
WEBVIEW = "WEBVIEW"
EXTERNAL = "EXTERNAL"
HOME = "HOME"          # tap changed nothing
OTHER = "OTHER"        # navigated to an in-app screen we don't recognise

_PLP_IDS = {"plp_sort_by_view", "plp_filter_view", "plp_row_product_iv"}
NAVIGATION_SIMILARITY = 0.85   # screens more similar than this are treated as "did not navigate"
TITLE_MATCH_MIN = 0.5          # share of slug words that must appear in the page title


@dataclass
class Landing:
    kind: str
    package: str
    activity: str
    title: str | None = None
    brands: list[str] = field(default_factory=list)
    product_count: int = 0
    similarity_to_before: float | None = None
    screenshot: str | None = None


@dataclass
class VisionVerdict:
    verdict: str            # "MATCH" | "DIFFERENT"
    detail: str = ""


def _nodes(page_source: str) -> list[dict]:
    root = ET.fromstring(page_source)
    out = []
    for n in root.iter():
        rid = n.get("resource-id") or ""
        out.append({
            "id": rid.rsplit("/", 1)[-1],
            "text": n.get("text") or "",
            "desc": n.get("content-desc") or "",
            "cls": n.get("class") or "",
            "clickable": n.get("clickable") == "true",
            "bounds": n.get("bounds") or "",
        })
    return out


def fingerprint(page_source: str) -> frozenset:
    return frozenset((n["id"], n["text"], n["desc"]) for n in _nodes(page_source) if n["id"] or n["text"] or n["desc"])


def similarity(before_source: str, after_source: str) -> float:
    a, b = fingerprint(before_source), fingerprint(after_source)
    return len(a & b) / len(a | b) if (a | b) else 1.0


def parse_landing(package: str, activity: str, page_source: str, before_source: str | None = None,
                  screenshot: str | None = None) -> Landing:
    sim = similarity(before_source, page_source) if before_source else None
    base = dict(package=package, activity=activity, similarity_to_before=sim, screenshot=screenshot)
    if package != APP_PACKAGE:
        return Landing(EXTERNAL, **base)

    nodes = _nodes(page_source)
    title = next((n["text"] for n in nodes if n["id"] == "toolbar_title_tv" and n["text"]), None)

    if "WebView" in activity or any(n["cls"].endswith("WebView") for n in nodes):
        return Landing(WEBVIEW, title=title, **base)

    if any(n["id"] in _PLP_IDS for n in nodes):
        return Landing(
            PLP, title=title,
            brands=[n["text"] for n in nodes if n["id"] == "plp_row_brand_tv" and n["text"]],
            product_count=sum(1 for n in nodes if n["id"] == "plp_row_product_iv"),
            **base,
        )

    if sim is not None and sim >= NAVIGATION_SIMILARITY:
        return Landing(HOME, title=title, **base)
    return Landing(OTHER, title=title, **base)


# ---------------------------------------------------------------- judging

def _words(text: str | None) -> set[str]:
    return set(normalize_brand(text or "").split())


def title_matches_slug(title: str | None, slug_text: str | None) -> bool:
    slug = _words(slug_text)
    return bool(slug) and len(slug & _words(title)) / len(slug) >= TITLE_MATCH_MIN


def brands_overlap(a: list[str], b: list[str], aliases: AliasMap | None = None) -> bool:
    """True if any brand in `a` names the same brand as one in `b` (alias-aware, whole-word tolerant)."""
    for x in a:
        for y in b:
            if aliases and aliases.same_brand(x, y):
                return True
            wx, wy = _words(x), _words(y)
            if wx and wy and (wx <= wy or wy <= wx):
                return True
    return False


@dataclass
class Judgement:
    status: SpotStatus
    reason: str
    evidence: str  # "type_only" | "type_and_text" | "vision" | "none"


def _deviates(reason: str) -> Judgement:
    return Judgement(SpotStatus.APP_DEVIATES_FROM_FEED, reason, "type_only")


def _inconclusive(reason: str) -> Judgement:
    return Judgement(SpotStatus.INCONCLUSIVE, reason, "none")


def judge(target: dl.Target, landing: Landing, vision: VisionVerdict | None = None,
          aliases: AliasMap | None = None) -> Judgement:
    """Decide whether the landing screen is where the feed said it would be.

    APP_DEVIATES_FROM_FEED is only returned for clear contradictions (wrong kind of screen, a
    different brand, a tap that navigates nowhere); anything merely unrecognised is INCONCLUSIVE.
    """
    kind, declared = landing.kind, target.type

    if kind == HOME:
        return _deviates("tap_did_not_navigate")

    if not target.resolved:  # AMBIGUOUS_DEEPLINK in Phase 4 terms: vision, then page title vs slug
        if vision and vision.verdict == "MATCH":
            return Judgement(SpotStatus.CONFIRMED, f"vision: {vision.detail}", "vision")
        if kind == WEBVIEW and title_matches_slug(landing.title, target.slug_text):
            return Judgement(SpotStatus.CONFIRMED, f"page title {landing.title!r} matches link slug", "type_and_text")
        if vision and vision.verdict == "DIFFERENT":
            return _deviates(f"vision: {vision.detail}")
        return _inconclusive("ambiguous_link_no_supporting_evidence")

    if declared in (dl.PLP, dl.CATEGORY, dl.BRAND):
        if kind in (WEBVIEW, EXTERNAL):
            return _deviates(f"expected_listing_page_but_landed_on_{kind.lower()}")
        if kind == OTHER:
            return _inconclusive("landed_on_unrecognised_in_app_screen")
        if landing.product_count == 0:
            return _inconclusive("listing_page_shows_no_products")
        if declared == dl.BRAND and landing.brands and target.brand:
            if brands_overlap([target.brand], landing.brands, aliases):
                return Judgement(SpotStatus.CONFIRMED, "listing shows the linked brand", "type_and_text")
            return _deviates(f"linked_brand_{target.brand!r}_not_on_listing")
        if declared == dl.CATEGORY and target.category:
            if title_matches_slug(landing.title, target.category):
                return Judgement(SpotStatus.CONFIRMED, f"listing title {landing.title!r} matches category", "type_and_text")
            return _inconclusive(f"listing_title_{landing.title!r}_differs_from_category_{target.category!r}")
        return Judgement(SpotStatus.CONFIRMED, "opened a product listing (slug-id links can't be verified further)", "type_only")

    if declared == dl.CAMPAIGN:
        if kind == EXTERNAL:
            return _deviates("expected_in_app_campaign_page_but_left_the_app")
        if kind in (WEBVIEW, OTHER):
            evidence = "type_and_text" if kind == WEBVIEW and title_matches_slug(landing.title, target.slug_text) else "type_only"
            return Judgement(SpotStatus.CONFIRMED, f"campaign page opened ({kind.lower()})", evidence)
        return _inconclusive("campaign_link_opened_a_listing")

    if declared == dl.EXTERNAL:
        if kind in (EXTERNAL, WEBVIEW):
            return Judgement(SpotStatus.CONFIRMED, f"external link opened ({kind.lower()})", "type_only")
        return _deviates("expected_external_link_but_stayed_in_app")

    if declared == dl.PRODUCT and kind == PLP:
        return _deviates("expected_product_page_but_landed_on_listing")

    return _inconclusive(f"no_rule_for_declared_{declared}_landed_{kind}")
