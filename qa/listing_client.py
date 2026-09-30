"""Read a listing page (PLP) straight from AJIO's search-edge API: title, brand facet, products. No emulator.

The app calls GET search-edge.services.ajio.com/rilfnlwebservices/v6/rilfnl/products/category/83 with
`curatedid=<slug>` for "/s/<slug>" links. The request carries app-identity headers only (no Authorization).
Read-only: one GET per page of results.
"""
from __future__ import annotations

import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlencode, urlsplit

import requests

from qa import deeplink_resolve as dl
from qa.envfile import load_env
from qa.feed_client import MAX_ATTEMPTS, RETRY_STATUSES, FeedError, RunLog

HOST = "search-edge.services.ajio.com"
PATH = "/rilfnlwebservices/v6/rilfnl/products/category/83"
PAGE_SIZE = 25   # [A] the app's own page size


@dataclass
class Listing:
    slug: str
    title: str | None
    total_results: int
    page: int
    brands: dict[str, int] = field(default_factory=dict)          # brand facet: name -> product count
    genders: dict[str, int] = field(default_factory=dict)         # gender facet: name -> product count (Men/Women/Girls/Boys/Infants)
    facets: dict[str, int] = field(default_factory=dict)          # facet name -> number of options
    products: list[dict] = field(default_factory=list)
    raw_query: str | None = None
    discount_ranges: dict[int, int] = field(default_factory=dict)  # "Discount Ranges" facet: N -> products discounted N% or more


def _device_id() -> str:
    return os.environ.get("AJIO_DEVICE_ID") or f"{uuid.uuid4()}R"


def _headers(device_id: str) -> dict[str, str]:
    return {"accept": "application/json", "client_type": "Android", "client_version": "9.38.1", "os": "1",
            "x-tenant-id": "AJIO", "ai": "com.ril.ajio", "vr": "AN-2.1.21", "user-agent": "Ajio/9.38000.0 (Android 16)",
            "device-id": device_id, "requestid": str(uuid.uuid4()), "accept-encoding": "gzip"}


CURATED, CATEGORY = "curated", "category"   # /s/<slug> links vs /c/<slug> links


def listing_target(destination_raw: str | None) -> tuple[str, str] | None:
    """('curated'|'category', slug) for AJIO /s/ and /c/ links; None for anything this endpoint doesn't serve."""
    parts = urlsplit(dl._unwrap((destination_raw or "").strip()))
    if "://" not in (destination_raw or ""):
        parts = urlsplit("https://" + (destination_raw or "").strip().lstrip("/"))
    segs = [s for s in parts.path.split("/") if s]
    for i, seg in enumerate(segs[:-1]):
        if seg.lower() in ("s", "c") and i <= 1:
            return (CURATED if seg.lower() == "s" else CATEGORY), unquote(segs[i + 1])
    return None


LUXE = "luxe"


def listing_store(destination_raw: str | None) -> str | None:
    """Which AJIO store serves this link: "luxe" for a link on luxe.ajio.com, else None (the standard store).
    Same slug, different catalogue - e.g. luxe.ajio.com/s/allstarsearlyoffersmen-403091 is 1,254 products from
    4 brands in the standard store (no BOSS) but 6,794 from 14 brands, BOSS included, in the Luxe one - so a Luxe
    link checked against the standard store reports brands "missing" that the page really has."""
    raw = dl._unwrap((destination_raw or "").strip())
    if "://" not in (destination_raw or ""):
        raw = "https://" + raw.lstrip("/")
    host = (urlsplit(raw).hostname or "").lower()
    return LUXE if host.startswith("luxe.") else None


def _is_server_bug(resp) -> bool:
    """AJIO's listing service sometimes answers a perfectly valid request with HTTP 400 and a Java NullPointerException
    ("facetData is null") - a fault on their side that passes on its own (the same links work minutes later), so it is
    retried like a 5xx instead of being reported as a bad request."""
    return resp.status_code == 400 and "NullPointerException" in (resp.text or "")


def _path(slug: str, kind: str) -> str:
    return PATH if kind == CURATED else PATH.rsplit("/", 1)[0] + f"/{quote(slug, safe='')}"


def _params(slug: str, page: int, page_size: int, kind: str = CURATED, store: str | None = None,
            sort: str | None = None) -> dict[str, str]:
    p = {"advfilter": "true", "store": "rilfnl", "fields": "FULL",
         "pageSize": str(page_size), "currentPage": str(page), "platform": "android", "displayRatings": "true",
         "pincode": os.environ.get("AJIO_PINCODE", "560029"), "latitude": os.environ.get("AJIO_LATITUDE", "12.933113"),
         "longitude": os.environ.get("AJIO_LONGITUDE", "77.601536"), "userState": "LOGGED_IN"}
    if kind == CURATED:
        p.update({"curatedid": slug, "curated": "true"})
    if store:
        p["store"] = store
    if sort:
        p["query"] = sort      # ":discount-desc" | ":prce-asc" | ":prce-desc" - the API's own sort codes (see qa/deal_sort_check.py)
    return p


def parse_listing(slug: str, page: int, data: dict) -> Listing:
    title = next((m.get("content") for m in data.get("metaElementData", []) if m.get("name") == "pageTitle" and m.get("content")), None)
    title = title or data.get("freeTextSearch") or None   # category (/c/) listings carry their title here
    facets = {f.get("name"): len(f.get("values") or []) for f in data.get("facets", [])}
    brand_facet = next((f for f in data.get("facets", []) if f.get("name") == "Brands"), {})
    brands = {v["name"]: v.get("count", 0) for v in brand_facet.get("values") or [] if v.get("name")}
    gender_facet = next((f for f in data.get("facets", []) if f.get("name") == "Gender"), {})
    genders = {v["name"]: v.get("count", 0) for v in gender_facet.get("values") or [] if v.get("name")}
    discount_facet = next((f for f in data.get("facets", []) if f.get("name") == "Discount Ranges"), {})
    discount_ranges = {int(m.group(1)): v.get("count", 0) for v in discount_facet.get("values") or []
                       if (m := re.match(r"\s*(\d+)\s*%", v.get("name") or ""))}
    return Listing(slug=slug, title=title, total_results=(data.get("pagination") or {}).get("totalResults", 0), page=page,
                   brands=brands, genders=genders, facets=facets, products=data.get("products") or [],
                   raw_query=((data.get("currentQuery") or {}).get("query") or {}).get("value"),
                   discount_ranges=discount_ranges)


def fetch_listing(slug: str, page: int = 0, page_size: int = PAGE_SIZE, kind: str = CURATED, run_log: RunLog | None = None,
                  timeout: float = 30.0, session: requests.Session | None = None, sleep=time.sleep,
                  store: str | None = None, sort: str | None = None) -> Listing:
    """`store`: None for the standard store, LUXE for a luxe.ajio.com link (see listing_store). `sort`: an API sort code."""
    load_env()
    http = session or requests
    query = urlencode(_params(slug, page, page_size, kind, store, sort))
    url = f"https://{HOST}{_path(slug, kind)}?{query}"
    device_id, last = _device_id(), "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        headers = _headers(device_id)
        resp = None
        try:
            resp = http.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        if run_log:
            run_log.record(f"listing_{slug}_p{page}{(sort or '').replace(':', '_')}", headers, url, resp, attempt)
        if resp is not None:
            if resp.status_code == 200:
                return parse_listing(slug, page, resp.json())
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUSES and not _is_server_bug(resp):
                raise FeedError(f"listing {slug}: {last} ({resp.text[:200]!r})")
        if attempt < MAX_ATTEMPTS:
            sleep(2 ** attempt + random.uniform(0, 1))
    raise FeedError(f"listing {slug}: gave up after {MAX_ATTEMPTS} attempts ({last})")


PRODUCT_COLUMNS = ["code", "brand", "name", "price", "mrp", "discount_percent", "offer_price", "rating", "rating_count",
                   "category", "gender", "url"]


def _value(price: dict | None) -> int | None:
    v = (price or {}).get("value")
    return int(v) if v is not None else None


def product_row(p: dict) -> dict:
    discount = "".join(c for c in str(p.get("discountPercent") or "") if c.isdigit())
    return {"code": p.get("code"), "brand": (p.get("fnlColorVariantData") or {}).get("brandName"), "name": p.get("name"),
            "price": _value(p.get("price")), "mrp": _value(p.get("wasPriceData")),
            "discount_percent": int(discount) if discount else None, "offer_price": _value(p.get("offerPrice")),
            "rating": p.get("averageRating"), "rating_count": p.get("ratingCount"),
            "category": p.get("verticalNameText"), "gender": p.get("segmentNameText"), "url": p.get("url")}


def product_rows(listing: Listing) -> list[dict]:
    return [product_row(p) for p in listing.products]


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "https://www.ajio.com/s/min70percentoffcurated-402881"
    kind, slug = listing_target(arg) or (CURATED, arg)
    lst = fetch_listing(slug, kind=kind, run_log=RunLog(), store=listing_store(arg))
    print(f"title={lst.title!r} total={lst.total_results} products_on_page={len(lst.products)} brands={len(lst.brands)}")
    print("facets:", lst.facets)
