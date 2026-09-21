"""Verify a listing page against the banner that opened it, using the listing's own filters.

Two rules: every brand named in the banner must be an option in the listing's Brands filter
(searched one by one in the filter's search box), and the listing title must match the deal the
banner offers. Banner brands and deal are read from the banner image by the vision model.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

from qa.brand_resolver import normalize_brand
from qa.compare import AliasMap, brand_key
from qa.spotcheck import landing as lp

BRAND_SEARCH_ID = "brand_facet_value_row_search_et"
_ROW = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")
_DEAL_NOISE = {"percent", "off", "and", "the", "on", "flat", "extra", "products", "product"}


def _by_id(source: str, rid: str) -> list[tuple[str, tuple | None]]:
    from qa.spotcheck.device import parse_bounds
    return [(n.get("text") or "", parse_bounds(n.get("bounds") or ""))
            for n in ET.fromstring(source).iter() if (n.get("resource-id") or "").endswith(f"id/{rid}")]


def filter_button(source: str):
    boxes = _by_id(source, "plp_filter_view")
    return boxes[0][1] if boxes else None


def facet_tab(source: str, name: str):
    for text, box in _by_id(source, "facet_row_name_tv"):
        if text == name:
            return box
    return None


def brand_rows(source: str) -> list[tuple[str, int]]:
    """Brand options currently listed in the Brands filter as (name, product count)."""
    rows = []
    for text, _ in _by_id(source, "general_facet_value_row_tv"):
        m = _ROW.match(text)
        rows.append((m.group(1), int(m.group(2))) if m else (text, 0))
    return rows


def same_brand(a: str, b: str, aliases: AliasMap | None = None) -> bool:
    return brand_key(a) == brand_key(b) or bool(aliases and aliases.same_brand(a, b))


def match_brand(name: str, options, aliases: AliasMap | None = None) -> tuple[str | None, str | None]:
    """(option, 'exact'|'partial'). Partial = one name's whole words are all inside the other's (banners abbreviate)."""
    options = list(options)
    for n in options:
        if same_brand(name, n, aliases):
            return n, "exact"
    words = set(normalize_brand(name).split())
    for n in options:
        other = set(normalize_brand(n).split())
        if words and other and (words <= other or other <= words):
            return n, "partial"
    return None, None


def deal_tokens(text: str | None) -> set[str]:
    t = (text or "").lower().replace("₹", " rs ").replace("%", " percent ")
    t = re.sub(r"\bup\s+to\b", "upto", t)
    return {w for w in normalize_brand(t).split() if w not in _DEAL_NOISE and w != "rs"}


def deal_matches_title(deal: str | None, title: str | None) -> bool | None:
    """None when either side is missing; else True if one side's key words (min/upto/under, amounts) contain the other's."""
    d, t = deal_tokens(deal), deal_tokens(title)
    if not d or not t:
        return None
    return d <= t or t <= d


def open_brand_filter(device) -> bool:
    _, _, src = device.state()
    box = filter_button(src)
    if box is None:
        return False
    device.tap(box)
    time.sleep(3)
    _, _, src = device.state()
    tab = facet_tab(src, "Brands")
    if tab is None:
        return False
    device.tap(tab)
    time.sleep(2)
    return True


def search_brand(device, name: str, aliases: AliasMap | None = None) -> dict:
    device.type_into(BRAND_SEARCH_ID, name)
    time.sleep(2.5)
    _, _, src = device.state()
    rows = brand_rows(src)
    matched, kind = match_brand(name, [n for n, _ in rows], aliases)
    return {"brand": name, "found": matched is not None, "matched_as": matched, "match": kind,
            "products": next((c for n, c in rows if n == matched), None), "search_results": [n for n, _ in rows][:8]}


def verify_against_banner(device, banner_info: dict, listing_title: str | None, aliases: AliasMap | None = None) -> dict:
    """banner_info is the vision output: brands_mentioned + deal_offered. Leaves the filter panel open."""
    brands = [b for b in banner_info.get("brands_mentioned") or [] if b]
    deal = banner_info.get("deal_offered") or None
    out: dict = {"banner_brands": brands, "banner_deal": deal, "listing_title": listing_title}
    title_ok = deal_matches_title(deal, listing_title)
    out["title_matches_deal"] = title_ok
    out["brand_checks"] = []
    if brands:
        if not open_brand_filter(device):
            out["error"] = "could_not_open_brand_filter"
        else:
            out["brand_checks"] = [search_brand(device, b, aliases) for b in brands]
    return _finish(out, brands, title_ok)


def _finish(out: dict, brands: list[str], title_ok: bool | None) -> dict:
    out["missing_brands"] = [c["brand"] for c in out["brand_checks"] if not c["found"]]
    if "error" in out or (not brands and title_ok is None):
        out["result"] = "INCONCLUSIVE"
    elif title_ok is False or out["missing_brands"]:
        out["result"] = "FAIL"
    elif title_ok is None or not brands:
        out["result"] = "INCONCLUSIVE"
    else:
        out["result"] = "PASS"
    return out


def verify_from_listing(banner_info: dict, listing_title: str | None, listing_brands: dict[str, int],
                        aliases: AliasMap | None = None) -> dict:
    """Same verdict as verify_against_banner, but the Brands filter comes from the server's listing response."""
    brands = [b for b in banner_info.get("brands_mentioned") or [] if b]
    deal = banner_info.get("deal_offered") or None
    out: dict = {"source": "server", "banner_brands": brands, "banner_deal": deal, "listing_title": listing_title,
                 "title_matches_deal": deal_matches_title(deal, listing_title), "brand_checks": []}
    for b in brands:
        matched, kind = match_brand(b, listing_brands, aliases)
        out["brand_checks"].append({"brand": b, "found": matched is not None, "matched_as": matched, "match": kind,
                                    "products": listing_brands.get(matched)})
    return _finish(out, brands, out["title_matches_deal"])
