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

# The menu parts of the app (not banner pages: nothing is verified, a run just fetches them with their pictures; qa/app_menus.py).
# `kind` is the qa.app_menus kind. `by_l1`: the content depends on the shopper segment (l1), so the run is repeated per l1 chosen
# (l2 never mattered in the menu tests, so it stays neutral). `by_state`: depends on the state (only the ads send one).
# Pincode is not an input of any of them.
MENU_PAGES = [
    {"id": "menu-top-nav", "label": "Top Nav", "tier": "menu", "kind": "top-nav", "by_l1": True, "by_state": False},
    {"id": "menu-bottom-nav", "label": "Bottom Nav", "tier": "menu", "kind": "bottom-nav", "by_l1": False, "by_state": False},
    {"id": "menu-ads", "label": "Ads", "tier": "menu", "kind": "ads", "by_l1": True, "by_state": True},
    {"id": "menu-trending", "label": "Trending", "tier": "menu", "kind": "trending", "by_l1": True, "by_state": False},
]
MENU_IDS = [p["id"] for p in MENU_PAGES]
RUN_PAGE_IDS = PAGE_IDS + MENU_IDS      # what a run (not a schedule) may be started for


def menu_page(page: str) -> dict | None:
    """The MENU_PAGES entry for a page id, or None for a banner page."""
    return next((p for p in MENU_PAGES if p["id"] == page), None)
