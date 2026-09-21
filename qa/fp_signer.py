"""Python port of Fynd's public request signer (@gofynd/fp-signature), as bundled in AJIO's app.

See analysis/FINDINGS.md section 6.4.1. The default secret is the public library default,
not an AJIO credential; it can be overridden via the FP_SIGNATURE_SECRET env var.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, quote

DEFAULT_SECRET = "1234567"
HEADERS_TO_IGNORE = {
    "authorization", "connection", "x-amzn-trace-id", "user-agent",
    "expect", "presigned-expires", "range",
}
HEADERS_TO_INCLUDE = [re.compile(p, re.I) for p in (r"x-fp-.*", r"host")]


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _rfc3986(s: str) -> str:
    return quote(s, safe="-_.~")


def _canonical_path(path: str) -> str:
    segs = [quote(unquote(s.replace("+", " ")), safe="-_.~!'()*") for s in path.split("/")]
    out: list[str] = []
    for seg in re.sub(r"/{2,}", "/", "/".join(segs)).split("/"):
        if seg == "..":
            if out:
                out.pop()
        elif seg != ".":
            out.append(seg)
    p = "/".join(out)
    return p if p.startswith("/") else "/" + p


def _canonical_query(query: str) -> str:
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() != "x-fp-signature"]
    enc = sorted((_rfc3986(k), _rfc3986(v)) for k, v in pairs if k)
    return "&".join(f"{k}={v}" for k, v in enc)


def now_fp_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sign(method: str, host: str, path: str, query: str = "", body: str = "",
         headers: dict[str, str] | None = None, fp_date: str | None = None,
         secret: str | None = None) -> dict[str, str]:
    """Return {"x-fp-date", "x-fp-signature"} for a request. `headers` are the extra headers
    the app sends; only host and x-fp-* ones end up in the signed set."""
    secret = secret or os.environ.get("FP_SIGNATURE_SECRET") or DEFAULT_SECRET
    fp_date = fp_date or now_fp_date()
    hdrs = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    hdrs["host"] = host
    hdrs["x-fp-date"] = fp_date
    hdrs.pop("x-fp-signature", None)
    signed = sorted(
        k for k in hdrs
        if k not in HEADERS_TO_IGNORE and any(p.search(k) for p in HEADERS_TO_INCLUDE)
    )
    canonical_headers = "\n".join(f"{k}:{hdrs[k]}" for k in signed)
    canonical = "\n".join([
        method.upper() or "GET",
        _canonical_path(path),
        _canonical_query(query),
        canonical_headers + "\n",
        ";".join(signed),
        _sha256_hex(body or ""),
    ])
    string_to_sign = fp_date + "\n" + _sha256_hex(canonical)
    sig = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).hexdigest()
    return {"x-fp-date": fp_date, "x-fp-signature": "v1.1:" + sig}
