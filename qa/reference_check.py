"""Check a banner against its reference row: what it is SUPPOSED to lead to (CLAUDE.md section 6).

The reference CSV (qa/reference.py) is the human's statement of intent for a banner: which brand(s), which category,
which kind of page. feed_verify already checks the banner's own artwork against the listing its link opens; this adds a
second, independent judgement - the link against the human's expectation - and is judged on the listing the link REALLY
opens (AJIO's listing API), not on the link's text, because most banner links are opaque campaign slugs
(/s/min50percentoff-curated-407675) that say nothing about brands.

  expected_deeplink_type  PLP / CATEGORY / BRAND / CAMPAIGN / EXTERNAL - what the link parses as (a PLP accepts the
                          listing family: /s/, /c/ and brand pages)
  expected_brand          "Nike|Adidas" - every brand named must appear in the listing's Brands filter (alias-aware:
                          LEVI'S = LEVIS). The first is the dominant one [A] but all are required
  expected_category       the listing's own title must contain it, or be contained in it (case/punctuation-insensitive)

Result: {"status": MATCH | MISMATCH | UNCHECKED, "problems": [...], "unchecked": [...], "reason": "reference: ...", "expected": {...}}.
UNCHECKED means an expectation could not be tested (listing didn't load, link isn't a listing...) - never a pass and never
a failure. A row with no expectations gives no check at all.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from qa import deeplink_resolve as dl
from qa import listing_client as lc
from qa.compare import AliasMap, _type_ok, brand_key
from qa.reference import ReferenceRow, load_reference

log = logging.getLogger("qa.reference_check")

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "reference.csv"   # the user's real rows (never the .sample)

MATCH, MISMATCH, UNCHECKED = "MATCH", "MISMATCH", "UNCHECKED"


def load_default(path: str | Path | None = None) -> tuple[dict[str, ReferenceRow], Path | None]:
    """(rows that have expectations, the file used). No file = ({}, None): the reference check is simply off. A file that
    can't be read is reported and treated as off rather than stopping a run."""
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return {}, None
    try:
        rows = load_reference(path)
    except Exception as exc:                       # a bad CSV must not stop the checks
        log.warning("reference file %s could not be read (%s: %s) - running without it", path, type(exc).__name__, exc)
        return {}, None
    return {bid: row for bid, row in rows.items() if row.has_expectations}, path


def _expected_brands(row: ReferenceRow) -> list[str]:
    return [b.strip() for b in (row.expected_brand or "").split("|") if b.strip()]


def check(row: ReferenceRow, destination_raw: str | None, get_listing: Callable[[], lc.Listing], aliases: AliasMap) -> dict | None:
    """Judge one banner's own destination against its reference row. `get_listing()` is only called when a brand or
    category expectation needs the listing (and may raise: that becomes UNCHECKED)."""
    if not row.has_expectations:
        return None
    problems: list[str] = []
    unchecked: list[str] = []
    expected = {k: v for k, v in (("deeplink_type", row.expected_deeplink_type), ("brand", row.expected_brand),
                                  ("category", row.expected_category)) if v}

    target = None
    try:
        target = dl.resolve(destination_raw)
    except Exception as exc:                       # a link the resolver chokes on is unchecked, not a crash
        unchecked.append(f"link could not be parsed ({type(exc).__name__})")
    has_link = bool((destination_raw or "").strip())

    if row.expected_deeplink_type:
        if not has_link:
            problems.append(f"expected a {row.expected_deeplink_type} link but the banner has no link")
        elif target is not None and target.resolved:
            if not _type_ok(row.expected_deeplink_type, target.type):
                problems.append(f"expected a {row.expected_deeplink_type} link but it is {target.type}")
        elif target is not None:
            unchecked.append(f"link type could not be determined ({target.reason or 'unresolved'})")

    needs_listing = bool(row.expected_brand or row.expected_category)
    if needs_listing:
        if not has_link:
            problems.append("banner has no link, so nothing can be checked against the expected brand/category")
        elif not lc.listing_target(destination_raw):
            unchecked.append("the link is not a listing page, so its brands/category can't be read")
        else:
            listing = None
            try:
                listing = get_listing()
            except Exception as exc:
                unchecked.append(f"listing not available ({type(exc).__name__})")
            if listing is not None:
                if row.expected_brand:
                    names = list(listing.brands)
                    if not names:
                        unchecked.append("the listing has no brand list")
                    else:
                        missing = [b for b in _expected_brands(row) if not any(aliases.same_brand(b, n) for n in names)]
                        if missing:
                            problems.append(f"expected brand{'s' if len(missing) > 1 else ''} not on the listing: {', '.join(missing)}")
                if row.expected_category:
                    title = (listing.title or "").strip()
                    if not title:
                        unchecked.append("the listing has no title to compare the category with")
                    else:
                        want, got = brand_key(row.expected_category), brand_key(title)
                        if want and got and want not in got and got not in want:
                            problems.append(f"expected category '{row.expected_category}' but the listing is titled '{title}'")

    status = MISMATCH if problems else UNCHECKED if unchecked else MATCH
    return {"status": status, "problems": problems, "unchecked": unchecked, "expected": expected,
            "reason": ("reference: " + "; ".join(problems)) if problems else ""}


def note(result: dict) -> str:
    """The one-line reference finding of a saved result ("" unless it is a mismatch) - shown next to a banner's other
    reasons in the CLI summary, the Excel export and alert text."""
    chk = result.get("reference_check") or {}
    return chk.get("reason", "") if chk.get("status") == MISMATCH else ""
