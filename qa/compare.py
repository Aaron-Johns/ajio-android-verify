"""Compare a feed banner's declared destination with reference data (CLAUDE.md section 7)."""
from __future__ import annotations

import json
from pathlib import Path

from qa import deeplink_resolve as dl
from qa.brand_resolver import BrandResolver, normalize_brand
from qa.reference import ReferenceRow
from qa.status import Comparison, Status, brand_needs_review

ALIASES_FILE = Path(__file__).resolve().parent.parent / "inputs" / "brand_aliases.draft.json"

# Listing-page types the app renders in the same PLP fragment (FINDINGS 3.2). [A] An expected
# type of PLP accepts any of them; every other expected type must match exactly.
_PLP_FAMILY = {dl.PLP, dl.CATEGORY, dl.BRAND}


def brand_key(name: str | None) -> str:
    """Same key the alias draft was generated with: normalized, spaces removed."""
    return normalize_brand(name or "").replace(" ", "")


class AliasMap:
    def __init__(self, groups: list[list[str]]):
        self._canonical: dict[str, str] = {}
        for group in groups:
            keys = [brand_key(name) for name in group]
            for key in keys:
                self._canonical[key] = keys[0]

    @classmethod
    def from_file(cls, path: str | Path = ALIASES_FILE) -> "AliasMap":
        return cls(json.loads(Path(path).read_text(encoding="utf-8"))["groups"])

    def same_brand(self, a: str | None, b: str | None) -> bool:
        ka, kb = brand_key(a), brand_key(b)
        if not ka or not kb:
            return False
        return self._canonical.get(ka, ka) == self._canonical.get(kb, kb)


def _type_ok(expected: str, actual: str) -> bool:
    return actual in _PLP_FAMILY if expected == dl.PLP else actual == expected


def compare_banner(banner_id: str, destination_raw: str | None, reference: ReferenceRow | None,
                   resolver: BrandResolver, aliases: AliasMap) -> Comparison:
    def done(status: Status, reason: str, target=None, resolution=None) -> Comparison:
        return Comparison(banner_id, status, reason, destination_raw, target, resolution)

    if reference is None:
        return done(Status.NO_REFERENCE, "no_reference_row")
    if not reference.has_expectations:
        return done(Status.NO_REFERENCE, "reference_row_has_no_expectations")

    try:
        target = dl.resolve(destination_raw)
        if target.reason == "empty_destination":
            return done(Status.ERROR, "empty_destination", target)  # [A] a linkless banner is a feed defect
        if not target.resolved:
            return done(Status.AMBIGUOUS_DEEPLINK, target.reason or "unresolved_link", target)

        if reference.expected_deeplink_type and not _type_ok(reference.expected_deeplink_type, target.type):
            return done(Status.MISMATCH, f"type_expected_{reference.expected_deeplink_type}_got_{target.type}", target)

        resolution = None
        if reference.expected_brand:
            expected_brand = reference.expected_brand.split("|")[0].strip()  # dominant brand [A]
            if target.type != dl.BRAND:
                return done(Status.AMBIGUOUS_DEEPLINK, "brand_not_in_link", target)
            resolution = resolver.resolve(target.brand) if target.brand else None
            why = brand_needs_review(target.brand, resolution)
            if why:
                return done(Status.AMBIGUOUS_DEEPLINK, why, target, resolution)
            if not aliases.same_brand(resolution["ajio_brand"], expected_brand):
                return done(Status.MISMATCH, f"brand_expected_{expected_brand}_got_{resolution['ajio_brand']}",
                            target, resolution)

        if reference.expected_category:
            if target.type != dl.CATEGORY:
                return done(Status.AMBIGUOUS_DEEPLINK, "category_not_in_link", target, resolution)
            if brand_key(target.category) != brand_key(reference.expected_category):
                return done(Status.MISMATCH,
                            f"category_expected_{reference.expected_category}_got_{target.category}", target, resolution)

        return done(Status.MATCH, "all_expectations_met", target, resolution)
    except Exception as exc:  # a broken row must not stop the run
        return done(Status.ERROR, f"{type(exc).__name__}: {exc}")
