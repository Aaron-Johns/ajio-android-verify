"""Statuses and the ambiguity rule for brand resolutions (CLAUDE.md 7 and 7.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    AMBIGUOUS_DEEPLINK = "AMBIGUOUS_DEEPLINK"
    NO_REFERENCE = "NO_REFERENCE"
    ERROR = "ERROR"


class SpotStatus(str, Enum):
    """Phase 5 tap-through outcomes (CLAUDE.md section 10)."""
    CONFIRMED = "CONFIRMED"
    APP_DEVIATES_FROM_FEED = "APP_DEVIATES_FROM_FEED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class Comparison:
    banner_id: str
    status: Status
    reason: str
    destination_raw: str | None = None
    target: object | None = None
    brand_resolution: dict | None = field(default=None)


def brand_needs_review(brand_text: str | None, resolution: dict | None) -> str | None:
    """Return why a brand lookup can't be trusted, or None if it can be auto-accepted.

    The resolver itself is frozen (verbatim port), so its known weak spots are handled here:
    an empty brand text looks clean, and a multi-candidate word_boundary result is silently
    accepted at 0.95 confidence (e.g. "Polo" -> "Polo Plus"), so both need verification.
    """
    if not brand_text or not brand_text.strip():
        return "empty_brand"
    if resolution is None:
        return "no_resolution"
    if resolution.get("needs_verification", True):
        return f"resolver_{resolution.get('match_type', 'unknown')}"
    if resolution.get("match_type") == "word_boundary" and len(resolution.get("possible_ajio_brands") or []) > 1:
        return "word_boundary_multiple_candidates"
    if not resolution.get("ajio_brand"):
        return "no_brand"
    return None
