"""Alias-aware brand comparison (the 22 approved alias groups, CLAUDE.md section 5) and the PLP link-type family."""
from __future__ import annotations

import json
from pathlib import Path

from qa import deeplink_resolve as dl
from qa.brand_resolver import normalize_brand

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
