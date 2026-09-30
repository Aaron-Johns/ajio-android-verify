"""Check a banner's price / discount claim against the listing it opens, using AJIO's own sort and filter data.

Prices (the listing sorted by price, the API's `query=:prce-asc | :prce-desc`):
  "UNDER Rs 799"          the dearest item is at most 799 + UNDER_PRICE_SLACK
  "UP TO Rs 799"          the same (a price cap); "UP TO Rs 500 OFF" (an amount off) and "UP TO 60%" (a discount) have no rule
  "STARTING AT Rs 399"    the cheapest item is exactly 399

Discounts (the listing's own "Discount Ranges" filter, which comes back with the listing: "30% and above" -> N products):
  "MIN. 45% OFF" / "20-70% OFF"   the floor is x - MIN_DISCOUNT_SLACK (35 for 45, 10 for 20); the top of a range is never checked.
      The filter steps are multiples of 10, so the floor is rounded DOWN to the step at or under it (35 -> "30% and above").
      The filter is cumulative, so every lower step holds at least as many products as the floor's step. If a lower step holds MORE,
      those extra products are discounted less than the floor -> mismatch; if every lower step holds the same number, nothing is
      below the floor -> correct. And the step just ABOVE the floor step must hold strictly FEWER products than it (some product has
      to sit between the floor and the next step; user, 2026-09-30). [A] products under the lowest step (under 10%) are in no
      step at all and are not seen.

Anything else a banner says ("UP TO 60%" - user: a bare up-to discount is never checked - or "FLAT 50%") has no rule; a listing that can't be sorted, or has no discount filter, is
UNCHECKED, never a failure: the banner keeps the verdict its other checks gave it.
"""
from __future__ import annotations

import re
from typing import Callable

from qa.listing_client import product_row

MIN_DISCOUNT_SLACK = 10     # percentage points under the banner's "min x%" that the listing's floor may sit
UNDER_PRICE_SLACK = 1       # rupees the dearest item may sit over the banner's "under Rs x"
PAGE_SIZE = 60              # the largest page size the API accepted (100 is refused)

_NUM = r"(\d[\d,]*)"
_RUPEE = r"(?:rs\.?|₹|inr)?\s*"
_RULES = (
    ("min_discount", re.compile(r"\bmin(?:imum)?\.?\s*" + _NUM + r"\s*%")),
    ("discount_range", re.compile(_NUM + r"\s*%?\s*(?:-|–|—|to)\s*" + _NUM + r"\s*%")),
    ("under_price", re.compile(r"\bunder\s*" + _RUPEE + _NUM)),
    # "UP TO Rs 999" is a price cap, like "under"; "UP TO Rs 500 OFF" is an amount off and "UP TO 60%" a discount: neither is checked
    ("under_price", re.compile(r"\bup\s*to\s*(?:rs\.?|₹|inr)\s*" + _NUM + r"(?![\d,])(?!\s*(?:/-)?\s*off\b)")),
    ("starting_price", re.compile(r"\bstarting\s*(?:at|from)?\s*" + _RUPEE + _NUM)),
)
_DISCOUNT_RULES = ("min_discount", "discount_range")


def parse_deal(text: str | None) -> tuple[str, int] | None:
    """(rule, x) for a deal this module knows how to check (x = the first number, the "min" / the range's low end), else None."""
    t = (text or "").lower()
    for name, pattern in _RULES:
        m = pattern.search(t)
        if m:
            return name, int(m.group(1).replace(",", ""))
    return None


def summary(r: dict) -> str:
    """The banner detail view's "Deal works on the listing?" answer: True, False, or "Couldn't check" (why is in the reason).
    Works from a saved sort_check, so results saved before this existed can be given one too ("" for a shape it doesn't know)."""
    kind = (parse_deal(r.get("deal")) or (None,))[0]
    if kind is None or (kind in _DISCOUNT_RULES and r["status"] == "MATCH" and "step" not in (r.get("observed") or {})):
        return ""                                       # no rule now, or saved by an earlier version of the discount check
    return {"MATCH": "True", "MISMATCH": "False"}.get(r["status"], "Couldn't check")


def _first_price(get: Callable, sort: str):
    for p in get(sort, 0, PAGE_SIZE).products:
        value = product_row(p)["price"]
        if value is not None:
            return value
    return None


def _discount_floor(x: int, ranges: dict[int, int]) -> tuple[str, dict, str]:
    """(status, observed, reason) for a "min x%" claim from the Discount Ranges filter ({30: products discounted 30% or more, ...})."""
    floor = x - MIN_DISCOUNT_SLACK
    if floor <= 0:
        return "MATCH", {"step": 0, "count": None, "lower_steps": []}, ""
    step = max((s for s in ranges if s <= floor), default=None)
    if step is None:
        return "UNCHECKED", {}, f"the listing's discount filter starts above {floor}%"
    lower = sorted(s for s in ranges if s < step)
    observed = {"step": step, "count": ranges[step], "lower_steps": lower}
    more = [s for s in lower if ranges[s] > ranges[step]]
    if more:
        s = max(more)                                   # the step just under the floor that holds extra products
        observed.update(lower_step=s, lower_count=ranges[s])
        return "MISMATCH", observed, (f"{ranges[s] - ranges[step]} products on the listing are discounted less than {step}% "
                                      f"(the {s}% and above filter holds {ranges[s]}, the {step}% and above filter only {ranges[step]}); "
                                      f"banner promises at least {x}% (allowed down to {floor}%)")
    above = min((s for s in ranges if s > step), default=None)
    if above is not None and ranges[above] >= ranges[step]:
        # the step above the floor must hold FEWER products: some product has to sit between the floor and the next step
        observed.update(above_step=above, above_count=ranges[above])
        return "MISMATCH", observed, (f"no products on the listing are discounted between {step}% and {above}% (the {above}% and above filter "
                                      f"holds {ranges[above]}, the same as the {step}% and above filter); "
                                      f"banner promises at least {x}% (allowed down to {floor}%)")
    return "MATCH", observed, ""


def check(deal: str | None, get: Callable, discount_ranges: dict[int, int] | None = None) -> dict | None:
    """None if the banner's deal has no rule here. Else {"status": MATCH | MISMATCH | UNCHECKED, "rule", "observed", "reason",
    "summary"}. `get(sort, page, page_size)` returns the listing (with its products) in that price order; `discount_ranges` is
    the listing's Discount Ranges filter."""
    parsed = parse_deal(deal)
    if parsed is None:
        return None
    kind, x = parsed
    out: dict = {"rule": kind, "deal": deal, "observed": {}}
    if kind in _DISCOUNT_RULES:
        if not discount_ranges:
            result = {**out, "status": "UNCHECKED", "reason": "the listing has no discount filter to compare"}
        else:
            status, observed, reason = _discount_floor(x, discount_ranges)
            result = {**out, "status": status, "observed": observed, "reason": reason}
        return {**result, "summary": summary(result)}
    try:
        if kind == "under_price":
            seen = out["observed"]["highest_price"] = _first_price(get, ":prce-desc")
            problem = f"dearest item on the listing is Rs {seen}, banner says under Rs {x}" if seen is not None and seen > x + UNDER_PRICE_SLACK else ""
        else:
            seen = out["observed"]["lowest_price"] = _first_price(get, ":prce-asc")
            problem = f"cheapest item on the listing is Rs {seen}, banner says starting at Rs {x}" if seen is not None and seen != x else ""
    except Exception as exc:                 # the sorted page couldn't be loaded: not a finding about the banner
        # note: the banner keeps the verdict its other checks gave it, but this is put beside it so the gap is visible
        result = {**out, "status": "UNCHECKED", "reason": f"couldn't load the sorted listing: {type(exc).__name__}: {exc}"[:200], "note": True}
    else:
        if seen is None:
            result = {**out, "status": "UNCHECKED", "reason": "the listing shows no prices to compare"}
        else:
            result = {**out, "status": "MISMATCH" if problem else "MATCH", "reason": problem}
    return {**result, "summary": summary(result)}
