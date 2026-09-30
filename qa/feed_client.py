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

import requests

from qa import cohort_client, fp_signer
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
# AJIO_USER_GROUPS, if set explicitly, is an override (sent verbatim - useful for deliberate
# experimentation, e.g. the web UI's l1/l2 picker). Left unset, the real value is fetched live from
# AJIO's own cohort-resolution endpoint (FINDINGS 6.10) - not guessed - with DEFAULT_USER_GROUPS as
# the last-resort fallback if that call fails for any reason (network, Akamai, endpoint change).
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
class Hotspot:
    """One clickable sub-region of a banner image - a single creative can carry several, each
    linking somewhere different (e.g. a beauty-brand collage banner with one hotspot per brand).
    x/y/width/height are pixels relative to the image's own reported width/height (Banner.image_width/
    image_height), from the CMS editor - not integer-snapped, and not guaranteed to match the
    dimensions of whatever's actually served for image_url (see qa.feed_verify's hotspot cropping,
    which rescales against the real downloaded image size rather than trusting these verbatim)."""
    url: str
    x: float
    y: float
    width: float
    height: float
    alt: str = ""


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
    hotspots: list[Hotspot] = field(default_factory=list)
    image_width: int | None = None
    image_height: int | None = None
    schedule: list[dict] = field(default_factory=list)
    user_type: str | None = None
    alt_text: str = ""  # the app exposes this as the banner's accessibility label (used by the spot-check)
    hidden: bool = False        # CMS visibility flag off, and/or outside its schedule window as of fetch time - see _cms_hidden_reasons/_in_schedule
    hidden_reason: str = ""     # comma-joined subset of "block_hidden" | "component_hidden" | "outside_schedule", or "" (not hidden)


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


def _resolve_user_groups(device_id: str, run_log: RunLog | None = None) -> str:
    """AJIO_USER_GROUPS env override wins if set (verbatim, no validation - it's an experimental
    combo, see FINDINGS 6.10); otherwise fetch this device's real assigned cohort live, falling
    back to the confirmed default baseline if that call fails for any reason."""
    override = os.environ.get("AJIO_USER_GROUPS", "").strip()
    if override:
        return override
    try:
        return cohort_client.resolve_user_groups(device_id, run_log=run_log)
    except cohort_client.CohortError as exc:
        log.warning("cohort resolution failed (%s); falling back to the default baseline user-groups", exc)
        return cohort_client.DEFAULT_USER_GROUPS


def _build_headers(bearer: str, device_id: str, path: str, query: str, user_groups: str) -> dict[str, str]:
    signed = fp_signer.sign("GET", API_HOST, path, query, headers={"x-fp-sdk-version": SDK_VERSION})
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {bearer}",
        "x-currency-code": "INR",
        "user-agent": "Platform/Android",
        "x-location-detail": LOCATION,
        "user-groups": user_groups,
        "user_groups": user_groups,
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
    user_groups = _resolve_user_groups(device_id, run_log=run_log)
    last: str = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        headers = _build_headers(bearer, device_id, path, query, user_groups)
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


def _cms_hidden_reasons(props: dict) -> list[str]:
    """AJIO's CMS editor exposes a per-block/section visibility checkbox - there is no field named
    "hide" anywhere in the raw response; the two observed in practice are `showBlock` and
    `showComponent` (each `{"type": "checkbox", "value": bool}`; live sample: 16/335 and 8/88 set to
    false respectively) - kept distinct here (rather than one combined flag) so the UI can say which
    one it was. An explicit False on either is treated as a deliberate hide; a missing key or True
    means shown, matching the app's own default when the prop is absent entirely."""
    return [reason for key, reason in (("showBlock", "block_hidden"), ("showComponent", "component_hidden"))
           if (props.get(key) or {}).get("value") is False]


def _in_schedule(schedule: list[dict], now: datetime) -> bool:
    """True if `now` falls inside any of this banner's schedule windows (predicate.schedule - see
    Banner.schedule), or if it has none at all (unscheduled = always visible). `cron` is ignored:
    every window observed live is "* * * * * *" or "" (no recurring restriction beyond start/end), so
    only the window bounds are checked - a genuinely cron-restricted window would currently read as
    "on" for its whole start-end span rather than just the cron-matching moments within it."""
    if not schedule:
        return True
    for window in schedule:
        start, end = window.get("start"), window.get("end")
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
        except (ValueError, AttributeError):
            continue   # a malformed window shouldn't wrongly hide or show the banner - just skip it
        if (start_dt is None or now >= start_dt) and (end_dt is None or now <= end_dt):
            return True
    return False


def _banner_from_unit(unit: dict, section: dict, position: int, section_index: int,
                      block_index: int | None, now: datetime) -> Banner | None:
    props = _props(unit)
    dest_values = [props[k].get("value") for k in DEST_KEYS if isinstance(props.get(k), dict)]
    dest = next((v for v in dest_values if isinstance(v, str) and v.strip()), dest_values[0] if dest_values else None)
    images = _image_props(props)
    if not dest_values and not (images and section.get("name") in BANNER_SECTIONS):
        return None
    preferred = next((props[k]["value"] for k in ("bannerImage", "image")
                      if isinstance(props.get(k), dict) and isinstance(props[k].get("value"), dict)), None)
    main = preferred or (images[0] if images else {})
    # hotspots come from `main` specifically (the image actually shown as image_url), not any other
    # image-carrying prop on the unit (e.g. a popup_image) - their x/y/width/height are only
    # meaningful relative to the one image they were drawn on.
    hotspots = [Hotspot(url=h["url"], x=h.get("x", 0), y=h.get("y", 0), width=h.get("width", 0),
                        height=h.get("height", 0), alt=h.get("alt") or "")
               for h in (main.get("hotspots") or []) if h.get("url")]
    banner_id = section.get("_id", "") if block_index is None else f"{section.get('_id', '')}:{block_index}"
    predicate = unit.get("predicate") or section.get("predicate") or {}
    schedule = predicate.get("schedule") or []
    reasons = _cms_hidden_reasons(props) + (["outside_schedule"] if not _in_schedule(schedule, now) else [])
    return Banner(
        banner_id=banner_id,
        image_url=main.get("image") if isinstance(main.get("image"), str) else None,
        position=position,
        destination_raw=dest,
        section_type=section.get("name", ""),
        label=section.get("label", ""),
        section_index=section_index,
        block_index=block_index,
        hotspots=hotspots,
        image_width=main.get("imageWidth"),
        image_height=main.get("imageHeight"),
        schedule=schedule,
        user_type=(predicate.get("user") or {}).get("user_type"),
        alt_text=str((props.get("altText") or {}).get("value") or "").strip(),
        hidden=bool(reasons),
        hidden_reason=", ".join(reasons),
    )


def parse_banners(theme: dict, now: datetime | None = None) -> list[Banner]:
    """Flatten a theme response into banner records (FINDINGS 6.6): one per flat banner section,
    one per block for carousel-style sections. `now` is "the time it was fetched" for schedule
    purposes (see _in_schedule) - defaults to the actual current time, but callers replaying an old
    capture (e.g. feed_verify.py --from-run) can pass the original fetch time instead."""
    now = now or datetime.now(timezone.utc)
    banners: list[Banner] = []
    for s_idx, section in enumerate(theme.get("sections") or []):
        blocks = section.get("blocks") or []
        units = [(b, i) for i, b in enumerate(blocks)] if blocks else [(section, None)]
        for unit, b_idx in units:
            banner = _banner_from_unit(unit, section, len(banners), s_idx, b_idx, now)
            if banner:
                banners.append(banner)
    return banners


def fetch_banners(slug: str = "home", run_log: RunLog | None = None) -> list[Banner]:
    return parse_banners(fetch_theme(slug, run_log=run_log))


# The CDN picks the format from Accept and serves AVIF (which Windows/Excel often can't open)
# unless WebP is offered explicitly.
IMAGE_ACCEPT = "image/webp,image/png,image/jpeg"


def fetch_image(url: str, getter=requests.get, timeout: float = 30.0) -> tuple[str, bytes | None, str]:
    try:
        resp = getter(url, headers={"Accept": IMAGE_ACCEPT}, timeout=timeout)
    except requests.RequestException as exc:
        return f"error: {type(exc).__name__}", None, ""
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}", None, ctype
    if not ctype.startswith("image/"):
        return f"not an image ({ctype or 'no content-type'})", None, ctype
    return "OK", resp.content, ctype


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
