"""Detects which art set a banner image belongs to, from the CDN filename convention AJIO uses.

Prototype - see analysis/FINDINGS.md 6.10 ("Confirmed, concrete difference"). Comparing captured
`l1:nontransacted` vs `l1:premium` home-feed responses for the same campaigns showed two differently
sized/named asset sets: filenames follow `...UHP-<SET>-<SEGMENT>-...` where `<SET>` was `ST` (standard,
1024x672 landscape) for the default predicate and `PR` (premium, 1024x1000 near-square, taller) for
the premium-matched one. `<SEGMENT>` (the carousel/section code - MB, ALS, RE, WE, ...) was originally
assumed to always be "MB" since that's what the one captured example used; a live premium/men pull
(2026-09-28) showed 15 real UHP-PR- banners, all using other segment codes (ALS, RE, SBI, BBX, FC, NB)
and zero using MB - the original `-MB-`-anchored regex silently matched none of them, making every
--confirm-asset-set PR run report "never saw a 'PR' banner" even on a pull that actually had 15. Fixed
by only anchoring on `UHP-<SET>-`, not on what follows it. Only confirmed for those two `l1` values on
one page (home) - other `l1` tokens (supervalue, economy, classic, midpremium_control, ...) may use
`ST`, a code of their own, or none at all, and not every banner has an image to begin with. Treat this
as a diagnostic signal, not a verified rule to gate PASS/FAIL on.
"""
from __future__ import annotations

import re

_SET_RE = re.compile(r"UHP-([A-Z]{2,4})-")

KNOWN_SETS = {"ST": "standard", "PR": "premium"}


def detect_asset_set(image_url: str | None) -> str | None:
    """The <SET> code from a `...UHP-<SET>-...` filename, or None if the URL doesn't follow it."""
    if not image_url:
        return None
    m = _SET_RE.search(image_url)
    return m.group(1) if m else None
