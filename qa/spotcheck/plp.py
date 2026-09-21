"""Extract everything the AJIO product-listing screen exposes, scrolling to load more products.

The listing screen is native (resource ids like plp_row_brand_tv), so all data comes from the
accessibility tree; nothing is read from AJIO's network. Cards cut off by the screen edge are
returned partially filled and completed if a later scroll shows them fully.
"""
from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
MAX_SCREENS = 6          # [A] screens scrolled per listing
STALE_SCREENS = 2        # stop after this many scrolls that add no new product


@dataclass
class Product:
    label: str
    brand: str | None = None
    name: str | None = None
    price: int | None = None
    mrp: int | None = None
    discount_percent: int | None = None
    offer_price: int | None = None
    rating: float | None = None
    tags: list[str] = field(default_factory=list)
    delivery: str | None = None
    wishlist_count: str | None = None
    is_ad: bool = False

    @property
    def complete(self) -> bool:
        return bool(self.brand and self.name and self.price)


@dataclass
class Header:
    title: str | None = None
    subtitle: str | None = None
    sort: str | None = None
    sub_categories: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)


@dataclass
class PlpData:
    header: Header
    products: list[Product]
    screens: int
    screenshots: list[str] = field(default_factory=list)
    stopped: str = ""

    def to_dict(self) -> dict:
        return {"header": asdict(self.header), "screens": self.screens, "stopped": self.stopped,
                "screenshots": self.screenshots, "products": [asdict(p) for p in self.products]}


def _box(text: str | None):
    m = _BOUNDS.fullmatch(text or "")
    return tuple(int(g) for g in m.groups()) if m else None


def _inside(inner, outer, slack: int = 2) -> bool:
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
            and inner[2] <= outer[2] + slack and inner[3] <= outer[3] + slack)


def _nodes(source: str) -> list[dict]:
    return [{"id": (n.get("resource-id") or "").rsplit("/", 1)[-1], "text": n.get("text") or "",
             "desc": n.get("content-desc") or "", "box": _box(n.get("bounds"))}
            for n in ET.fromstring(source).iter()]


def _number(text: str | None) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", text or "")
    return float(cleaned) if cleaned and cleaned != "." else None


def _int(text: str | None) -> int | None:
    n = _number(text)
    return int(n) if n is not None else None


def parse_header(source: str) -> Header:
    nodes = _nodes(source)
    first = lambda i: next((n["text"] for n in nodes if n["id"] == i and n["text"]), None)  # noqa: E731
    boxes = {i: next((n["box"] for n in nodes if n["id"] == i and n["box"]), None)
             for i in ("rv_quick_filter_sub_categories", "rv_quick_filter_categories")}

    def titles_in(container: str) -> list[str]:
        area = boxes[container]
        return [n["text"] for n in nodes if area and n["id"] == "title" and n["text"] and n["box"] and _inside(n["box"], area)]

    # In the app's own naming the *_categories row holds the category tiles and *_sub_categories the filter chips.
    return Header(first("toolbar_title_tv"), first("toolbar_subtitle_tv"), first("plp_sort_subheading_tv"),
                  titles_in("rv_quick_filter_categories"), titles_in("rv_quick_filter_sub_categories"))


def parse_products(source: str) -> list[Product]:
    nodes = _nodes(source)
    products = []
    for card in (n for n in nodes if n["id"] == "actionContainer" and n["box"]):
        inside = [n for n in nodes if n is not card and n["box"] and _inside(n["box"], card["box"])]
        text = lambda i: next((n["text"] for n in inside if n["id"] == i and n["text"]), None)  # noqa: E731
        rating = _number(text("new_rating_star"))
        tags = [t for t in (text("plp_row_exclusive_tv"), text("selling_fast_tag")) if t]
        products.append(Product(
            label=card["desc"], brand=text("plp_row_brand_tv"), name=text("plp_row_prd_name_tv"),
            price=_int(text("plp_row_final_price_tv")), mrp=_int(text("plp_row_mrp_price_tv")),
            discount_percent=_int(text("plp_row_discount_percent_tv")), offer_price=_int(text("plp_row_offer_price_tv")),
            rating=(rating / 10 if rating and rating > 5 else rating), tags=tags,
            delivery=text("priority_date"), wishlist_count=text("plp_row_add_to_closet_wishcount_iv"),
            is_ad=any(n["id"] == "plp_ad_tv" for n in inside),
        ))
    return products


def merge_products(seen: dict[str, Product], found: list[Product]) -> int:
    """Add products keyed by accessibility label; a complete card replaces a partial one. Returns new count."""
    added = 0
    for p in found:
        key = p.label or f"{p.brand}|{p.name}"
        if key not in seen:
            seen[key] = p
            added += 1
        elif p.complete and not seen[key].complete:
            seen[key] = p
    return added


def capture_plp(device, out_dir: Path, prefix: str, max_screens: int = MAX_SCREENS) -> PlpData:
    """Read the listing on screen, then scroll and keep reading until it stops yielding new products."""
    seen: dict[str, Product] = {}
    shots: list[str] = []
    header, stale, stopped = Header(), 0, "max_screens"
    screens = 0
    for screens in range(1, max_screens + 1):
        _, _, source = device.state()
        if screens == 1:
            header = parse_header(source)
        shots.append(device.screenshot(out_dir / f"{prefix}_plp{screens}.png"))
        stale = 0 if merge_products(seen, parse_products(source)) else stale + 1
        if stale >= STALE_SCREENS:
            stopped = "no_new_products"
            break
        if screens < max_screens:
            device.scroll("down", 0.7)
    return PlpData(header, list(seen.values()), screens, shots, stopped)


def checks(data: PlpData) -> list[dict]:
    """Sanity checks on the listing itself: is it populated and internally consistent?"""
    full = [p for p in data.products if p.complete]
    bad_discount = [p.label for p in full if p.mrp and p.price and p.discount_percent is not None
                    and abs(round((p.mrp - p.price) / p.mrp * 100) - p.discount_percent) > 1]
    price_above_mrp = [p.label for p in full if p.mrp and p.price and p.price > p.mrp]
    labels = [p.label for p in data.products if p.label]
    results = [
        ("has_title", bool(data.header.title), data.header.title or "no toolbar title"),
        ("has_products", bool(full), f"{len(full)} complete products, {len(data.products) - len(full)} partial"),
        ("all_products_priced", all(p.price for p in full), "every complete card shows a price"),
        ("no_duplicate_products", len(labels) == len(set(labels)), f"{len(labels) - len(set(labels))} duplicates"),
        ("discount_matches_price_and_mrp", not bad_discount, f"{len(bad_discount)} cards where shown % differs from price/MRP"),
        ("price_not_above_mrp", not price_above_mrp, f"{len(price_above_mrp)} cards priced above MRP"),
    ]
    return [{"check": name, "ok": ok, "detail": detail} for name, ok, detail in results]


PRODUCT_COLUMNS = ["brand", "name", "price", "mrp", "discount_percent", "offer_price", "rating", "tags",
                   "delivery", "wishlist_count", "is_ad", "label"]


def product_rows(data: PlpData) -> list[dict]:
    return [{**{c: getattr(p, c) for c in PRODUCT_COLUMNS if c != "tags"}, "tags": " | ".join(p.tags)} for p in data.products]


def write_products_csv(rows: list[dict], path: Path, extra_columns: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=extra_columns + PRODUCT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
