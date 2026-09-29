"""Resolves the real `user-groups` value AJIO assigns to a device, instead of guessing one.

See analysis/FINDINGS.md 6.10. AJIO's own app never invents a user-groups header value - it asks a
cohort-resolution endpoint what this device has been assigned, then relays that answer verbatim on
every content request (theme, search-edge, ...). Two calls, both confirmed live to work standalone
(no emulator, no Akamai sensor data):

  1. POST /uaas/jwt/token/client  - guest OAuth bootstrap (hardcoded public client credentials,
     ~14-18 day expiry) returning a JWT. This is NOT feed_client.py's static AJIO_THEME_BEARER -
     the cohort endpoint specifically needs this guest JWT (FINDINGS 6.8/6.10 - sending the wrong
     one to the wrong endpoint is a confirmed 401).
  2. GET  /service/am/runtime/v2/cohort/getUserSegmentAndCohort/device/<device-id>  - keyed only by
     device-id (no l1/l2 input at all); returns every vertical/context's assigned cohort string.

A brand-new, never-before-seen device-id resolves to the same baseline
(`l1:nontransacted|l2:p_null,false,unisex,noasp`) as a long-lived one - see FINDINGS 6.10 for why
this means "premium"/other cohorts are very unlikely to be reachable by fabricating a device-id -
*unless* the caller has a real account's own session behind it, see AJIO_ACCOUNT_TOKEN below.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

from qa.envfile import load_env

load_env()
log = logging.getLogger("qa.cohort_client")

API_HOST = "api.services.ajio.com"
GUEST_TOKEN_PATH = "/uaas/jwt/token/client"
COHORT_PATH_TMPL = "/service/am/runtime/v2/cohort/getUserSegmentAndCohort/device/{device_id}"
CLIENT_VERSION = "9.38.1"
USER_AGENT = "Ajio/9.38000.0 (Android 16)"

# Set AJIO_ACCOUNT_TOKEN in .env to use a real, logged-in AJIO account's own session instead of the
# anonymous guest flow below - the only way this tool can reach a genuinely assigned cohort like
# "premium" (the guest flow only ever reaches the default baseline, see FINDINGS 6.10). This is the
# `authorization` bearer value from a real logged-in request (captured the same way AJIO_THEME_BEARER
# was - traffic capture on your own account, see analysis/FINDINGS.md), NOT a cookie or the cohort
# response body itself. Never commit or log this value; it identifies a real person's account.

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "guest_token.json"
_EXPIRY_MARGIN_S = 3600  # refresh a bit before the server's own expiry, not right at the edge

VERTICAL = "rilfnl_v1"  # AJIO's own storefront (matches search-edge's "rilfnl" path)
CONTEXT = "plp"  # one of the seven l1|l2 contexts ("genesys" is l1-only - see FINDINGS 6.10)
DEFAULT_USER_GROUPS = "l1:nontransacted|l2:p_null,false,unisex,noasp"  # confirmed baseline, FINDINGS 6.10


class CohortError(RuntimeError):
    """The guest-token or cohort call failed; callers should catch this and fall back to
    DEFAULT_USER_GROUPS rather than fail the whole request over a personalization lookup."""


def _load_cached_token() -> str | None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if data.get("expires_at", 0) > time.time() + _EXPIRY_MARGIN_S:
        return data.get("access_token")
    return None


def _save_cached_token(access_token: str, expires_in: float) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({"access_token": access_token, "expires_at": time.time() + expires_in}),
                          encoding="utf-8")


def fetch_guest_token(run_log: Any = None, timeout: float = 30.0, session: requests.Session | None = None,
                      force_refresh: bool = False) -> str:
    """A guest OAuth bootstrap token (FINDINGS 6.3/6.10). Cached to disk (qa/.cache/, gitignored)
    across runs since it's valid for days; refreshed automatically once expired or on a 401."""
    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached
    http = session or requests
    url = f"https://{API_HOST}{GUEST_TOKEN_PATH}"
    headers = {"client_type": "Android", "accept": "application/json", "client_version": CLIENT_VERSION,
              "user-agent": USER_AGENT, "x-tenant": "B2C"}
    data = {"grantType": "client_credentials", "clientName": "trusted_client", "clientSecret": "secret"}
    try:
        resp = http.post(url, headers=headers, data=data, timeout=timeout)
    except requests.RequestException as exc:
        raise CohortError(f"guest token request failed: {type(exc).__name__}: {exc}") from exc
    if run_log:
        run_log.record("guest_token", headers, url, resp, 1)
    if resp.status_code != 200:
        raise CohortError(f"guest token request returned HTTP {resp.status_code}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise CohortError("guest token response had no access_token")
    _save_cached_token(token, body.get("expires_in", 3600))
    return token


def fetch_cohort(device_id: str, guest_token: str | None = None, run_log: Any = None, timeout: float = 30.0,
                 session: requests.Session | None = None, _retried: bool = False) -> dict:
    """The raw cohort-resolution response for one device-id (FINDINGS 6.10).

    Auth precedence: an explicit `guest_token` argument, then AJIO_ACCOUNT_TOKEN (a real account's
    own session, if set), then the anonymous guest-OAuth flow. Using the account token is NOT
    silently downgraded to guest on failure - see the 401 handling below - since that would quietly
    return baseline/guest-tier results while looking like it succeeded.
    """
    http = session or requests
    account_token = os.environ.get("AJIO_ACCOUNT_TOKEN", "").strip()
    using_account = guest_token is None and bool(account_token)
    token = guest_token or account_token or fetch_guest_token(run_log=run_log, timeout=timeout, session=session)
    url = f"https://{API_HOST}{COHORT_PATH_TMPL.format(device_id=device_id)}"
    headers = {"accept": "application/json", "client_type": "Android", "client_version": CLIENT_VERSION,
              "user-agent": USER_AGENT, "device-id": device_id, "authorization": f"Bearer {token}"}
    try:
        resp = http.get(url, params={"client_type": "Android", "client_version": CLIENT_VERSION},
                        headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise CohortError(f"cohort request failed: {type(exc).__name__}: {exc}") from exc
    if run_log:
        run_log.record("cohort", headers, url, resp, 1)
    if resp.status_code == 401 and not _retried:
        if using_account:
            raise CohortError("AJIO_ACCOUNT_TOKEN was rejected (401) - it has likely expired; capture a "
                              "fresh authorization value from a real logged-in request and update .env "
                              "(not falling back to the guest flow, since that would silently return "
                              "guest-tier results while looking like it used your account)")
        # the cached guest token may have gone stale server-side even though our expiry math said it was fine
        fresh = fetch_guest_token(run_log=run_log, timeout=timeout, session=session, force_refresh=True)
        return fetch_cohort(device_id, guest_token=fresh, run_log=run_log, timeout=timeout, session=session,
                            _retried=True)
    if resp.status_code != 200:
        raise CohortError(f"cohort request returned HTTP {resp.status_code}")
    return resp.json()


def resolve_user_groups(device_id: str, vertical: str = VERTICAL, context: str = CONTEXT, run_log: Any = None,
                        timeout: float = 30.0, session: requests.Session | None = None) -> str:
    """The real 'user-groups' header value AJIO assigns to this device-id, in the same
    'l1:<x>|l2:<y>' shape the app itself sends (FINDINGS 6.10). Raises CohortError on any failure -
    callers should catch this and fall back to DEFAULT_USER_GROUPS."""
    data = fetch_cohort(device_id, run_log=run_log, timeout=timeout, session=session)
    try:
        raw = data["userCohortValue"][vertical][context]["cohorts"]
    except (KeyError, TypeError) as exc:
        raise CohortError(f"cohort response missing {vertical}.{context}.cohorts") from exc
    l1, sep, l2 = raw.partition("|")
    if not sep:
        raise CohortError(f"cohort value {raw!r} has no l2 part (wrong context? 'genesys' is l1-only)")
    return f"l1:{l1}|l2:{l2}"
