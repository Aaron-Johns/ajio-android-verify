"""Direct client for AJIO's home feed (Fynd Platform theme API), no emulator involved.

Endpoint, headers and response model: analysis/FINDINGS.md sections 6.1-6.6.
Every call is signed with qa.fp_signer and authenticated with the app's static theme bearer
(AJIO_THEME_BEARER in .env; NOT the guest JWT, see FINDINGS 6.8). Requests/responses are
logged (secrets redacted) to a run folder.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from qa import fp_signer
from qa.envfile import load_env

load_env()
log = logging.getLogger("qa.feed_client")

API_HOST = "api.services.ajio.com"
APPLICATION_ID = os.environ.get("AJIO_APPLICATION_ID", "6924384620d2931b59eb94ec")
THEME_PATH = "/api/service/application/theme/v1.0/{app_id}/{slug}"
SDK_VERSION = os.environ.get("AJIO_FP_SDK_VERSION", "1.10.6-9")
LOCATION = os.environ.get(
    "AJIO_LOCATION_DETAIL",
    '{"country":"INDIA","city":"BENGALURU","pincode":"560029","state":"KARNATAKA"}',
)
USER_GROUPS = os.environ.get("AJIO_USER_GROUPS", "l1:nontransacted|l2:p_null,false,unisex,noasp")
EXPERIMENTS = os.environ.get("AJIO_EXPERIMENTAL_FEATURES", "CMSABExp2,CMSABExp10,CMSABExp4")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
REDACT_HEADERS = {"authorization", "cookie", "set-cookie", "x-acf-sensor-data", "device-id", "ad_id", "x-auth-token"}

DEST_KEYS = ("redirectURL", "redirectImageURL", "cta_redirect_url")
BANNER_SECTIONS = {"hybrid-banner", "hybrid-dynamic-banner", "hybrid-swipe-gallery", "floating-widget"}
RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


class FeedError(RuntimeError):
    pass


@dataclass
class Banner:
    banner_id: str
    image_url: str | None
    position: int
    destination_raw: str | None
    section_type: str
    label: str
    section_index: int
    block_index: int | None
    hotspot_urls: list[str] = field(default_factory=list)
    schedule: list[dict] = field(default_factory=list)
    user_type: str | None = None
    alt_text: str = ""  # the app exposes this as the banner's accessibility label (used by the spot-check)


class RunLog:
    """Writes redacted request/response records under runs/<utc timestamp>/."""

    def __init__(self, base: Path | None = None):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = (base or RUNS_DIR) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _redact(headers: dict[str, str]) -> dict[str, str]:
        return {k: ("<REDACTED>" if k.lower() in REDACT_HEADERS else v) for k, v in headers.items()}

    def record(self, name: str, req_headers: dict, url: str, resp: requests.Response | None, attempt: int) -> None:
        entry: dict[str, Any] = {"url": url, "attempt": attempt, "request_headers": self._redact(req_headers)}
        if resp is not None:
            entry["status"] = resp.status_code
            entry["response_headers"] = self._redact(dict(resp.headers))
        (self.dir / f"{name}.attempt{attempt}.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
        if resp is not None and resp.status_code == 200:
            (self.dir / f"{name}.response.json").write_bytes(resp.content)


def _theme_bearer() -> str:
    bearer = os.environ.get("AJIO_THEME_BEARER", "").strip()
    if not bearer:
        raise FeedError("AJIO_THEME_BEARER is not set. Put the app's static theme bearer in .env (see FINDINGS 6.8).")
    return bearer


def _device_id() -> str:
    return os.environ.get("AJIO_DEVICE_ID") or f"{uuid.uuid4()}R"


def _build_headers(bearer: str, device_id: str, path: str, query: str) -> dict[str, str]:
    signed = fp_signer.sign("GET", API_HOST, path, query, headers={"x-fp-sdk-version": SDK_VERSION})
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {bearer}",
        "x-currency-code": "INR",
        "user-agent": "Platform/Android",
        "x-location-detail": LOCATION,
        "user-groups": USER_GROUPS,
        "user_groups": USER_GROUPS,
        "x-experimental-features": EXPERIMENTS,
        "x-fp-sdk-version": SDK_VERSION,
        "x-fp-date": signed["x-fp-date"],
        "x-fp-signature": signed["x-fp-signature"],
        "device-id": device_id,
        "accept-encoding": "gzip",
    }


def fetch_theme(slug: str = "home", run_log: RunLog | None = None, timeout: float = 30.0,
                session: requests.Session | None = None, sleep=time.sleep) -> dict:
    """GET one theme page (home, menswear, ...) and return the parsed JSON."""
    http = session or requests
    path = THEME_PATH.format(app_id=APPLICATION_ID, slug=slug)
    query = "company=1"
    url = f"https://{API_HOST}{path}?{query}"
    bearer = _theme_bearer()
    device_id = _device_id()
    last: str = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        headers = _build_headers(bearer, device_id, path, query)
        resp = None
        try:
            resp = http.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        if run_log:
            run_log.record(slug, headers, url, resp, attempt)
        if resp is not None:
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUSES:
                raise FeedError(f"{slug}: {last} ({resp.text[:200]!r})")
            retry_after = resp.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.isdigit() else 0.0
        else:
            delay = 0.0
        if attempt < MAX_ATTEMPTS:
            sleep(max(delay, 2 ** attempt + random.uniform(0, 1)))
    raise FeedError(f"{slug}: gave up after {MAX_ATTEMPTS} attempts ({last})")


def _props(unit: dict) -> dict:
    return unit.get("props") or {}


def _image_props(props: dict) -> list[dict]:
    return [p["value"] for p in props.values()
            if isinstance(p, dict) and isinstance(p.get("value"), dict) and "image" in p["value"]]


def _banner_from_unit(unit: dict, section: dict, position: int, section_index: int,
                      block_index: int | None) -> Banner | None:
    props = _props(unit)
    dest_values = [props[k].get("value") for k in DEST_KEYS if isinstance(props.get(k), dict)]
    dest = next((v for v in dest_values if isinstance(v, str) and v.strip()), dest_values[0] if dest_values else None)
    images = _image_props(props)
    if not dest_values and not (images and section.get("name") in BANNER_SECTIONS):
        return None
    preferred = next((props[k]["value"] for k in ("bannerImage", "image")
                      if isinstance(props.get(k), dict) and isinstance(props[k].get("value"), dict)), None)
    main = preferred or (images[0] if images else {})
    hotspots = [h.get("url") for img in images for h in (img.get("hotspots") or []) if h.get("url")]
    banner_id = section.get("_id", "") if block_index is None else f"{section.get('_id', '')}:{block_index}"
    predicate = unit.get("predicate") or section.get("predicate") or {}
    return Banner(
        banner_id=banner_id,
        image_url=main.get("image") if isinstance(main.get("image"), str) else None,
        position=position,
        destination_raw=dest,
        section_type=section.get("name", ""),
        label=section.get("label", ""),
        section_index=section_index,
        block_index=block_index,
        hotspot_urls=hotspots,
        schedule=predicate.get("schedule") or [],
        user_type=(predicate.get("user") or {}).get("user_type"),
        alt_text=str((props.get("altText") or {}).get("value") or "").strip(),
    )


def parse_banners(theme: dict) -> list[Banner]:
    """Flatten a theme response into banner records (FINDINGS 6.6): one per flat banner section,
    one per block for carousel-style sections."""
    banners: list[Banner] = []
    for s_idx, section in enumerate(theme.get("sections") or []):
        blocks = section.get("blocks") or []
        units = [(b, i) for i, b in enumerate(blocks)] if blocks else [(section, None)]
        for unit, b_idx in units:
            banner = _banner_from_unit(unit, section, len(banners), s_idx, b_idx)
            if banner:
                banners.append(banner)
    return banners


def fetch_banners(slug: str = "home", run_log: RunLog | None = None) -> list[Banner]:
    return parse_banners(fetch_theme(slug, run_log=run_log))


def host_of(destination: str | None) -> str:
    return urlsplit(destination or "").netloc


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch AJIO feed banners")
    ap.add_argument("slug", nargs="?", default="home")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--json", action="store_true", help="print all records as JSON")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_log = RunLog()
    banners = fetch_banners(args.slug, run_log=run_log)
    log.info("fetched %d banner records for %r; logs in %s", len(banners), args.slug, run_log.dir)
    if args.json:
        print(json.dumps([asdict(b) for b in banners], indent=2))
        return
    for b in banners[: args.limit]:
        print(f"{b.position:>3} [{b.section_type}] {b.label[:28]:<28} -> {b.destination_raw}")


if __name__ == "__main__":
    main()
