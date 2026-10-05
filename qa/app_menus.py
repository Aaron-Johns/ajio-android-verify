"""The menu parts of the AJIO app that are not banner pages: top navigation, bottom navigation, sponsored ads and trending.

A run of one of these only fetches the data and its pictures (no verification): `runs/<stamp>_<kind>_menu/` gets `menu.json`
(a flat list of items, each with a parent, so the UIs can drill down), `menu_meta.json` (small, for run lists) and `images/`.

How the app loads them (analysis/FINDINGS.md, recorded app session of 25 Sep 2026):
  top / bottom navigation  Fynd content API `navigations?slug=...`, the same signing and headers as the home feed (qa/feed_client.py),
                           with the shopper's `user-groups` cohort header. The top menu differs for premium shoppers, the bottom one did not.
  trending                 `recommendation/v1/trends?type=Cohort&value=<l1>&store=ajio`, no token. premium and nontransacted get the
                           same list, nonpremium gets none.
  ads                      AJIO's ad partner (OnlineSales, ajio-ba.o-s.io): one request per ad slot, with the shopper's segment, state
                           and login status as inputs. A third party's server, not AJIO's; it is only ever asked what the app asks.
Read-only: GET requests only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from qa import feed_client as fc
from qa.feed_client import MAX_ATTEMPTS, RETRY_STATUSES, FeedError, fetch_image

log = logging.getLogger("qa.app_menus")

KINDS = ("top-nav", "bottom-nav", "ads", "trending")
NAV_PATH = "/api/service/application/content/v2.0/navigations"
NAV_SLUGS = {"top-nav": "rn-ajio-new-top-nav", "bottom-nav": "rn-ajio-new-bottom-nav"}
TRENDS_URL = "https://api.services.ajio.com/rilfnlwebservices/recommendation/v1/trends"
ADS_URL = "https://ajio-ba.o-s.io/v2/bsda/pt"
ADS_CLIENT_ID = "10058742"          # AJIO's account at the ad partner, visible in every ad request the app makes (not a credential)
# (placement id, name shown to a person, how many ads the app asks for) - the three slots seen in the recorded session
AD_SLOTS = [("_sections_ajio", "Home screen", 10), ("_myaccount_banner", "My account banner", 25), ("_orderlisting_banner", "Order list banner", 25)]
APP_VERSION = "9.38.1"
APP_AGENT = "Ajio/9.38000.0 (Android 16)"
IMAGE_EXT = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/svg+xml": ".svg", "image/avif": ".avif"}
IMAGE_WORKERS = 6


# ---- fetching ------------------------------------------------------------------------------------------------------

def _get_json(url: str, headers: Callable[[], dict], label: str, params: dict | None = None,
              session=None, sleep=time.sleep, timeout: float = 30.0):
    """GET a JSON document, trying again on a temporary error (the same policy as the feed: qa.feed_client.RETRY_STATUSES). `headers` is called
    for every attempt, so a signed request gets a fresh signature."""
    http = session or requests
    last = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = None
        try:
            resp = http.get(url, params=params, headers=headers(), timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        if resp is not None:
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUSES:
                raise FeedError(f"{label}: {last} ({resp.text[:200]!r})")
        if attempt < MAX_ATTEMPTS:
            sleep(2 ** attempt + random.uniform(0, 1))
    raise FeedError(f"{label}: gave up after {MAX_ATTEMPTS} attempts ({last})")


def fetch_navigation(kind: str, user_groups: str | None = None, session=None, sleep=time.sleep) -> dict:
    slug = NAV_SLUGS[kind]
    query = f"slug={slug}"
    bearer, device = fc._theme_bearer(), fc._device_id()
    groups = user_groups or fc._resolve_user_groups(device)

    def headers() -> dict:
        h = fc._build_headers(bearer, device, NAV_PATH, query, groups)
        h.update({"client_type": "Android", "client_version": APP_VERSION, "requestid": str(uuid.uuid4()), "x-featured-store-data": ""})
        return h

    return _get_json(f"https://{fc.API_HOST}{NAV_PATH}?{query}", headers, f"navigation {slug}", session=session, sleep=sleep)


def fetch_trends(l1: str, session=None, sleep=time.sleep) -> dict:
    device = fc._device_id()
    headers = lambda: {"accept": "application/json, text/plain, */*", "user-agent": APP_AGENT, "device-id": device, "accept-encoding": "gzip"}
    return _get_json(TRENDS_URL, headers, "trending", params={"type": "Cohort", "value": l1, "store": "ajio"}, session=session, sleep=sleep)


def fetch_ads_slot(placement: str, how_many: int, l1: str, l2: str, state: str, session=None, sleep=time.sleep) -> dict:
    device = fc._device_id()
    params = {"client_id": ADS_CLIENT_ID, "pt": placement, "pcnt_au": str(how_many), "rn": str(int(time.time() * 1000)), "cli_ubid": device,
              "f.sis": "ajio", "f.platform": "MOBILE", "f.channel": "Android", "f.user_cohort": l1,
              "f.user_secondary_cohort": f"p_null,false,{l2},noasp", "f.user_type": "new" if l1 == "nontransacted" else "Existing",
              "f.user_loginstatus": "NON_LOGGED_IN", "f.state": state, "f.city": ""}
    headers = lambda: {"accept": "application/json, text/plain, */*", "user-agent": APP_AGENT, "client_type": "Android", "client_version": APP_VERSION,
                       "accept-encoding": "gzip"}
    return _get_json(ADS_URL, headers, f"ads {placement}", params=params, session=session, sleep=sleep)


# ---- turning the responses into a flat list of items ------------------------------------------------------------

def _item(items: list[dict], parent: int | None, level: int, title: str, **fields) -> int:
    """Append an item and return its id (its index). `images` are the picture addresses, the first is the main one."""
    item = {"id": len(items), "parent": parent, "level": level, "title": title, "link": "", "opens": "", "description": "", "alt": "",
            "images": [], "image_files": [], "active": True, "audience": "", **fields}
    items.append(item)
    return item["id"]


def _target(action: dict | None) -> tuple[str, str]:
    """(web address, what it opens) of a navigation entry. External pages carry a real address; the others are screens inside the app."""
    page = (action or {}).get("page") or {}
    kind = page.get("type", "")
    query_url = ((page.get("query") or {}).get("url") or [""])[0]
    if kind == "external" or query_url:
        return query_url, "web page"
    if kind == "sections":
        return "", "app screen: " + page.get("url", "")
    return "", f"app screen: {kind}" if kind else "app screen"


def flatten_navigation(data) -> list[dict]:
    """Every entry of a navigations response, parents before children (`level` 0 is the top row)."""
    blocks = data["items"] if isinstance(data, dict) else data[0]["items"]
    items: list[dict] = []

    def walk(nodes, parent: int | None, level: int) -> None:
        for n in nodes or []:
            pictures = [i.get("value") for i in n.get("images") or [] if i.get("value")]
            for key in ("image", "inactive_image"):
                v = n.get(key)
                pictures.append(v.get("value") if isinstance(v, dict) else v)
            link, opens = _target(n.get("action"))
            me = _item(items, parent, level, (n.get("display") or "").strip(), link=link, opens=opens,
                       images=list(dict.fromkeys(p for p in pictures if p)), active=bool(n.get("active", True)),
                       audience=(n.get("user") or {}).get("user_type", "") or ",".join(n.get("acl") or []))
            walk(n.get("sub_navigation"), me, level + 1)

    for block in blocks:
        walk(block.get("navigation"), None, 0)
    return items


def flatten_trends(data: dict) -> list[dict]:
    items: list[dict] = []
    for t in data.get("topTrends") or []:
        _item(items, None, 0, t.get("displayName", ""), description=t.get("description", ""), images=[t["image"]] if t.get("image") else [],
              opens="in-app search: " + t.get("redirectQuery", ""))
    return items


def flatten_ads(slots: list[tuple[str, dict]]) -> list[dict]:
    """`slots`: (slot name, response) in order. Each slot is a top row entry; its ads hang under it."""
    items: list[dict] = []
    for name, data in slots:
        ads = sorted((a for lst in (data.get("ads") or {}).values() for a in lst), key=lambda a: int(a.get("rank") or 0))
        parent = _item(items, None, 0, name, opens=f"{len(ads)} ad{'' if len(ads) == 1 else 's'}")
        for a in ads:
            el = a.get("elements") or {}
            _item(items, parent, 1, "", description=f"rank {a.get('rank', '')}", link=el.get("destination_url", ""), opens="web page",
                  images=[u for u in (el.get("mobile_image"), el.get("desktop_image")) if u])
    return items


def collect(kind: str, l1: str, l2: str, state: str, user_groups: str | None = None, session=None, sleep=time.sleep) -> list[dict]:
    if kind in NAV_SLUGS:
        return flatten_navigation(fetch_navigation(kind, user_groups, session=session, sleep=sleep))
    if kind == "trending":
        return flatten_trends(fetch_trends(l1, session=session, sleep=sleep))
    if kind == "ads":
        return flatten_ads([(name, fetch_ads_slot(pt, n, l1, l2, state, session=session, sleep=sleep)) for pt, name, n in AD_SLOTS])
    raise ValueError(f"unknown menu kind: {kind}")


# ---- saving -------------------------------------------------------------------------------------------------------

def image_file_name(url: str, content_type: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12] + IMAGE_EXT.get(content_type, ".img")


def download_images(items: list[dict], out_dir: Path, fetcher=fetch_image, workers: int = IMAGE_WORKERS) -> int:
    """Save every picture once into out_dir/images and fill each item's `image_files` (aligned with `images`; None where a download failed,
    the address in `images` still works then). Returns how many addresses could not be downloaded."""
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    urls = list(dict.fromkeys(u for it in items for u in it["images"]))

    def get(url: str) -> tuple[str, str | None]:
        status, data, ctype = fetcher(url)
        if not data:
            log.warning("picture not saved (%s): %s", status, url)
            return url, None
        name = image_file_name(url, ctype)
        (images_dir / name).write_bytes(data)
        return url, name

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        saved = dict(pool.map(get, urls))
    for it in items:
        it["image_files"] = [saved.get(u) for u in it["images"]]
    return sum(1 for v in saved.values() if v is None)


def save_menu(out_dir: Path, kind: str, items: list[dict], meta: dict) -> None:
    (out_dir / "menu.json").write_text(json.dumps({"kind": kind, **meta, "items": items}, ensure_ascii=False), encoding="utf-8")
    (out_dir / "menu_meta.json").write_text(json.dumps({"kind": kind, **meta, "items": len(items),
                                                         "with_images": sum(1 for i in items if i["images"])}, ensure_ascii=False), encoding="utf-8")


def load_menu(out_dir: Path) -> dict | None:
    try:
        return json.loads((out_dir / "menu.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_menu_meta(out_dir: Path) -> dict | None:
    try:
        return json.loads((out_dir / "menu_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def new_run_dir(kind: str, base: Path | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = (base or fc.RUNS_DIR) / f"{stamp}_{kind.replace('-', '')}_menu"      # "_menu" is how web/runner.py finds the folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch one menu part of the AJIO app (top / bottom navigation, ads, trending) with its pictures.")
    ap.add_argument("--kind", choices=KINDS, required=True)
    ap.add_argument("--l1", default="nontransacted", choices=["nontransacted", "premium", "nonpremium"])
    ap.add_argument("--l2", default="unisex")
    ap.add_argument("--state", default="KARNATAKA")
    ap.add_argument("--pincode", default=os.environ.get("AJIO_PINCODE", "560029"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    out_dir = new_run_dir(args.kind)       # made first, so the web server can find the run folder straight away
    started = time.time()
    try:
        # the cohort the app would send: the web runner sets AJIO_USER_GROUPS to the same string, so by hand --l1 / --l2 do the same
        groups = os.environ.get("AJIO_USER_GROUPS") or f"l1:{args.l1}|l2:p_null,false,{args.l2},noasp"
        items = collect(args.kind, args.l1, args.l2, args.state, user_groups=groups)
        failed = download_images(items, out_dir)
    except FeedError as exc:
        print(f"could not fetch {args.kind}: {exc}")
        return 1
    meta = {"l1": args.l1, "l2": args.l2, "state": args.state, "pincode": args.pincode, "fetched_at": datetime.now(timezone.utc).isoformat()}
    save_menu(out_dir, args.kind, items, meta)
    pictures = sum(len(i["images"]) for i in items)
    print(f"{args.kind}: {len(items)} items, {pictures - failed} of {pictures} pictures saved in {time.time() - started:.0f}s: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
