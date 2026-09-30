"""Check a banner's price / discount claim against the listing it opens, by sorting the listing (AJIO's own sort options).

  "MIN. 40% OFF"          the listing's lowest discount must be at least 40 - MIN_DISCOUNT_SLACK
  "20-70% OFF" / "20% to 70%"   the same low-end rule with x = 20; the top of the range is not checked at all
  "UNDER Rs 799"          sorted by price high to low, the first price is at most 799 + UNDER_PRICE_SLACK
  "STARTING AT Rs 399"    sorted by price low to high, the first price is exactly 399

The sorts are the API's `query` values `:discount-desc`, `:prce-asc`, `:prce-desc` (its own spelling). There is no
`:discount-asc`, so the lowest discount is read from the last page of `:discount-desc`. Anything else a banner says
("UP TO 60%", "FLAT 50%") has no rule here and is left alone; a listing that can't be sorted or paged that far is
UNCHECKED, never a failure: the banner keeps the verdict its other checks gave it and the reason says what couldn't be
read (e.g. "couldn't get the minimum discount since the page was too large"). The slacks are the user's numbers (2026-09-30).
"""
from __future__ import annotations

import math
import re
from typing import Callable

from qa.listing_client import product_row

MIN_DISCOUNT_SLACK = 10     # percentage points a listing's lowest discount may sit under the banner's "min x%"
UNDER_PRICE_SLACK = 1       # rupees the dearest item may sit over the banner's "under Rs x"
PAGE_SIZE = 60              # the largest page size the API accepted (100 is refused)

_NUM = r"(\d[\d,]*)"
_RUPEE = r"(?:rs\.?|₹|inr)?\s*"
_RULES = (
    ("min_discount", re.compile(r"\bmin(?:imum)?\.?\s*" + _NUM + r"\s*%")),
    ("discount_range", re.compile(_NUM + r"\s*%?\s*(?:-|–|—|to)\s*" + _NUM + r"\s*%")),
    ("under_price", re.compile(r"\bunder\s*" + _RUPEE + _NUM)),
    ("starting_price", re.compile(r"\bstarting\s*(?:at|from)?\s*" + _RUPEE + _NUM)),
)


def parse_deal(text: str | None) -> tuple[str, int, int | None] | None:
    """(rule, x, y) for a deal this module knows how to check, else None. A range keeps its y, though nothing checks it."""
    t = (text or "").lower()
    for name, pattern in _RULES:
        m = pattern.search(t)
        if m:
            nums = [int(g.replace(",", "")) for g in m.groups()]
            return name, nums[0], nums[1] if len(nums) > 1 else None
    return None


def _lowest_discount(get: Callable, total: int):
    """The lowest discount on the listing: the last product of the last page of the discount-descending sort."""
    last_page = max(0, math.ceil(total / PAGE_SIZE) - 1)
    values = [product_row(p)["discount_percent"] for p in get(":discount-desc", last_page, PAGE_SIZE).products]
    values = [v for v in values if v is not None]
    return min(values) if values else None


def _first(get: Callable, sort: str, field: str):
    for p in get(sort, 0, PAGE_SIZE).products:
        value = product_row(p)[field]
        if value is not None:
            return value
    return None


def check(deal: str | None, total_results: int, get: Callable) -> dict | None:
    """None if the banner's deal has no rule here. Else {"status": MATCH | MISMATCH | UNCHECKED, "rule", "observed", "reason"}.
    `get(sort, page, page_size)` returns the listing (with its products) in that order."""
    rule = parse_deal(deal)
    if rule is None:
        return None
    kind, x, y = rule
    out: dict = {"rule": kind, "deal": deal, "observed": {}}
    problems: list[str] = []
    try:
        if kind in ("min_discount", "discount_range"):
            if not total_results:
                return {**out, "status": "UNCHECKED", "reason": "the listing has no products to sort"}
            low = out["observed"]["lowest_discount"] = _lowest_discount(get, total_results)
            if low is None:
                return {**out, "status": "UNCHECKED", "reason": "the listing shows no discounts to compare"}
            if low < x - MIN_DISCOUNT_SLACK:
                problems.append(f"lowest discount on the listing is {low}%, banner promises at least {x}% "
                                f"(allowed down to {x - MIN_DISCOUNT_SLACK}%)")
        elif kind == "under_price":
            top = out["observed"]["highest_price"] = _first(get, ":prce-desc", "price")
            if top is None:
                return {**out, "status": "UNCHECKED", "reason": "the listing shows no prices to compare"}
            if top > x + UNDER_PRICE_SLACK:
                problems.append(f"dearest item on the listing is Rs {top}, banner says under Rs {x}")
        else:
            bottom = out["observed"]["lowest_price"] = _first(get, ":prce-asc", "price")
            if bottom is None:
                return {**out, "status": "UNCHECKED", "reason": "the listing shows no prices to compare"}
            if bottom != x:
                problems.append(f"cheapest item on the listing is Rs {bottom}, banner says starting at Rs {x}")
    except Exception as exc:                 # the sorted page couldn't be loaded or paged to: not a finding about the banner
        too_big = kind in ("min_discount", "discount_range") and "HTTP 403" in str(exc)    # the API refuses pages that deep
        why = ("couldn't get the minimum discount since the page was too large" if too_big
               else f"couldn't load the sorted listing: {type(exc).__name__}: {exc}"[:200])
        # note: the banner keeps the verdict its other checks gave it, but this is put beside it so the gap is visible
        return {**out, "status": "UNCHECKED", "reason": why, "note": True}
    if problems:
        return {**out, "status": "MISMATCH", "reason": "; ".join(problems)}
    return {**out, "status": "MATCH", "reason": ""}
