"""The AJIO app pages the tool can check. Each is one Fynd "theme" slug (`.../theme/v1.0/<app>/<slug>`).

Only `home` is personalised by the l1/l2 cohort; the other pages return the same banners whatever the
cohort is (user-confirmed 2026-09-30), so a run of one of them needs no l1/l2 choice.
`tier` is only for the UI (button colours): home | premium | standard.
"""
from __future__ import annotations

DEFAULT_PAGE = "home"

PAGES = [
    {"id": "home", "label": "Home", "tier": "home"},
    {"id": "premium-men", "label": "Prem men", "tier": "premium"},
    {"id": "premium-women", "label": "Prem women", "tier": "premium"},
    {"id": "kids-premium-page", "label": "Prem kids", "tier": "premium"},
    {"id": "menswear", "label": "Non prem men", "tier": "standard"},
    {"id": "womenswear", "label": "Non prem women", "tier": "standard"},
    {"id": "kidswear", "label": "Non prem kids", "tier": "standard"},
]
PAGE_IDS = [p["id"] for p in PAGES]
_LABELS = {p["id"]: p["label"] for p in PAGES}


def label(page: str) -> str:
    return _LABELS.get(page, page)
