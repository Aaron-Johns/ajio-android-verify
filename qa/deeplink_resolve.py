"""Parse a banner's destination_raw into a structured target (CLAUDE.md section 7,
analysis/FINDINGS.md section 3). The app only classifies link type by path on-device; the real
category/brand behind a slug is resolved server-side, so this yields a type plus the slug text
(as brand or category) and the numeric id, not a verified entity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

PLP = "PLP"
CATEGORY = "CATEGORY"
BRAND = "BRAND"
CAMPAIGN = "CAMPAIGN"
EXTERNAL = "EXTERNAL"
PRODUCT = "PRODUCT"
HOME = "HOME"
OTHER = "OTHER"
UNKNOWN = "UNKNOWN"

_MARKER_TYPES = {
    "c": CATEGORY, "b": BRAND, "brand": BRAND, "p": PRODUCT,
    "s": PLP, "s1": PLP, "search": PLP, "find": PLP,
    "cp": CAMPAIGN, "shop": CAMPAIGN, "sections": CAMPAIGN, "static-cms": CAMPAIGN, "capsule": CAMPAIGN,
}
_PREFIXED_MARKERS = {"c", "p"}  # manifest allows /<segment>/c/... and /<segment>/p/...
_PROMO_PATHS = {"jio-engage", "couponbonanza", "assured-gifts", "offers"}
_NON_PRODUCT_PATHS = {
    "login", "my-account", "wishlist", "cart", "ajio-wallet", "sharedcloset", "games", "gamezone",
    "top-shopper", "faq", "contactus", "selfcare", "help", "return-refund", "verify", "membership",
    "companion_app", "rcc", "odp",
}
_AJIO_HOST = re.compile(r"(^|\.)ajio\.com$", re.IGNORECASE)
_WRAPPER_HOSTS = ("ajio.page.link", "ajio.onelink.me", "ajioapps.onelink.me")
_BARE_HOST = re.compile(r"^[\w-]+(\.[\w-]+)+(/|$)")
_SLUG_ID = re.compile(r"^(?P<slug>.+?)-(?P<id>\d+)$")


@dataclass
class Target:
    type: str
    brand: str | None = None
    category: str | None = None
    identifier: str | None = None
    slug_text: str | None = None
    host: str | None = None
    raw: str | None = None
    reason: str | None = None  # set when type is UNKNOWN

    @property
    def resolved(self) -> bool:
        return self.type != UNKNOWN


def _unwrap(url: str) -> str:
    """ajioapps:// -> https:// and deep_link_value= attribution wrappers (FINDINGS 3.2)."""
    if url.startswith("ajioapps://"):
        url = "https://" + url[len("ajioapps://"):]
    parts = urlsplit(url)
    if parts.netloc.lower() in _WRAPPER_HOSTS:
        for pair in parts.query.split("&"):
            key, _, value = pair.partition("=")
            if key == "deep_link_value" and value:
                return _unwrap(unquote(value))
    return url


def _split_slug(segment: str) -> tuple[str | None, str | None]:
    """'clearance-store-1789044034' -> ('clearance store', '1789044034'); '830216' -> (None, '830216')."""
    segment = unquote(segment)
    match = _SLUG_ID.match(segment)
    if match:
        return match["slug"].replace("-", " "), match["id"]
    if segment.isdigit():
        return None, segment
    return segment.replace("-", " "), None


def resolve(destination_raw: str | None) -> Target:
    raw = destination_raw
    if not raw or not raw.strip():
        return Target(UNKNOWN, raw=raw, reason="empty_destination")

    url = _unwrap(raw.strip())
    if "://" not in url and not url.startswith("/") and _BARE_HOST.match(url):
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower() or None

    if host and parts.netloc.lower() in _WRAPPER_HOSTS:
        return Target(UNKNOWN, host=host, raw=raw, reason="wrapper_without_target")
    if host and not _AJIO_HOST.search(host):
        return Target(EXTERNAL, host=host, raw=raw)

    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return Target(HOME, host=host, raw=raw)

    marker_at = None
    if segments[0].lower() in _MARKER_TYPES:
        marker_at = 0
    elif len(segments) > 1 and segments[1].lower() in _PREFIXED_MARKERS:
        marker_at = 1

    if marker_at is None:
        first = segments[0].lower()
        if first in _PROMO_PATHS:
            return Target(CAMPAIGN, identifier=first, host=host, raw=raw)
        if first in _NON_PRODUCT_PATHS:
            return Target(OTHER, identifier=first, host=host, raw=raw)
        slug = _split_slug(segments[-1])[0]
        return Target(UNKNOWN, slug_text=slug, host=host, raw=raw, reason="unrecognized_path")

    target_type = _MARKER_TYPES[segments[marker_at].lower()]
    slug, identifier = (None, None)
    if len(segments) > marker_at + 1:
        slug, identifier = _split_slug(segments[marker_at + 1])
    if slug is None and marker_at == 1:
        slug = _split_slug(segments[0])[0]

    return Target(
        target_type,
        brand=slug if target_type == BRAND else None,
        category=slug if target_type == CATEGORY else None,
        identifier=identifier,
        slug_text=slug,
        host=host,
        raw=raw,
    )
