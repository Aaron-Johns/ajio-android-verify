"""Reference data: what each banner is expected to lead to (CLAUDE.md section 8).

CSV columns: banner_id, expected_brand, expected_category, expected_deeplink_type, notes.
banner_id is the feed's section _id, or "<section _id>:<block index>" for carousel blocks
(qa.feed_client.Banner.banner_id). Lines starting with '#' are ignored. A pipe-separated
expected_brand ("Nike|Adidas") lists several brands; the first one is the dominant brand [A].
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

COLUMNS = ("banner_id", "expected_brand", "expected_category", "expected_deeplink_type", "notes")


@dataclass(frozen=True)
class ReferenceRow:
    banner_id: str
    expected_brand: str | None = None
    expected_category: str | None = None
    expected_deeplink_type: str | None = None
    notes: str | None = None

    @property
    def has_expectations(self) -> bool:
        return bool(self.expected_brand or self.expected_category or self.expected_deeplink_type)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def load_reference(path: str | Path) -> dict[str, ReferenceRow]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:      # a BOM (Excel, qa/reference_template.py) must not hide the first # line
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    rows: dict[str, ReferenceRow] = {}
    for record in csv.DictReader(lines):
        banner_id = _clean(record.get("banner_id"))
        if not banner_id:
            continue
        deeplink_type = _clean(record.get("expected_deeplink_type"))
        rows[banner_id] = ReferenceRow(
            banner_id=banner_id,
            expected_brand=_clean(record.get("expected_brand")),
            expected_category=_clean(record.get("expected_category")),
            expected_deeplink_type=deeplink_type.upper() if deeplink_type else None,
            notes=_clean(record.get("notes")),
        )
    return rows
