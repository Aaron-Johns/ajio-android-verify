"""Run this yourself (uses mitmdump's bundled Python, not the project's venv):

    mitmdump -nr analysis/traffic_capture/recap_20260925/session.flow -s analysis/traffic_capture/extract_account_token.py -q

Pulls the real logged-in account's cohort-call authorization token out of the capture and appends
it to .env as AJIO_ACCOUNT_TOKEN. Only prints a masked preview, never the full value.
"""
from pathlib import Path

from mitmproxy import http

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
best = None


def response(flow: http.HTTPFlow) -> None:
    global best
    if "cohort/getUserSegmentAndCohort" not in flow.request.pretty_url:
        return
    auth = flow.request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if len(token) > 700:  # the guest token is ~550 chars; the real account's is longer
        best = token  # keep the last (most recent) match


def done():
    if not best:
        print("No matching account-token cohort call found in the capture.")
        return
    existing = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    lines = [l for l in existing.splitlines() if not l.startswith("AJIO_ACCOUNT_TOKEN=")]
    lines.append(f"AJIO_ACCOUNT_TOKEN={best}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote AJIO_ACCOUNT_TOKEN to {ENV_FILE} ({len(best)} chars, starts {best[:6]}..., ends ...{best[-4:]})")
