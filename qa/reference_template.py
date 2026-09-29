"""Write (or top up) the fill-in reference CSV from a finished run, so real expectations are quick to enter.

Run: python -m qa.reference_template [--run runs/<stamp>] [--scope hero|all] [--out config/reference.csv]

Every non-hidden banner of the run becomes a row: the five reference columns (expected_brand, expected_category and
expected_deeplink_type blank for YOU to fill in, notes free text) followed by read-only context columns (carousel label,
alt text, the banner's link and what type it parses as, what the artwork says, the listing's title, the tool's own verdict)
that help you decide what the banner should lead to. The loader ignores the context columns.

An existing file is never overwritten: rows for banners not in it yet are appended, and every row you already filled in is
left exactly as it is (so you can run this after each new feed pull to pick up new banners). Rows with no expectations are
ignored by the check, so a half-filled file is fine. Once config/reference.csv has expectations, every check picks it up
automatically (qa/feed_verify.py; see qa/reference_check.py for what is compared).
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

from qa import deeplink_resolve as dl
from qa.feed_client import RUNS_DIR
from qa.feed_verify import load_banners, load_final_results, select_banners, shown_result
from qa.reference import COLUMNS
from qa.reference_check import DEFAULT_PATH

CONTEXT = ("label", "alt_text", "destination_raw", "destination_type", "artwork_brands", "artwork_deal", "listing_title",
           "tool_result", "seen_in_run")
HEADER_NOTE = """# Reference rows: what each banner is SUPPOSED to lead to. Fill in expected_* for the banners you care about; leave the rest blank.
#   expected_deeplink_type  PLP | CATEGORY | BRAND | CAMPAIGN | EXTERNAL   (PLP accepts /s/ /c/ and brand pages)
#   expected_brand          Nike|Adidas  - every brand named must be in the listing's Brands filter (the first is the dominant one)
#   expected_category       e.g. Clearance Store - must match the listing's title
#   notes                   free text. The columns after notes are context only and are ignored.
# banner_id is the feed's section _id, or <section _id>:<block index> for a carousel slide. Lines starting with # are ignored.
"""


def latest_run_dir() -> Path:
    runs = sorted(p.parent for p in RUNS_DIR.glob("*/banners.json") if (p.parent / "results.json").exists() or (p.parent / "partial.jsonl").exists())
    if not runs:
        raise FileNotFoundError(f"no finished run with banners found under {RUNS_DIR}")
    return runs[-1]


def context_row(banner, result: dict | None, run_id: str) -> dict:
    check = (result or {}).get("banner_check") or {}
    try:
        dtype = dl.resolve(banner.destination_raw).type if (banner.destination_raw or "").strip() else ""
    except Exception:
        dtype = ""
    return {"banner_id": banner.banner_id, "label": banner.label or "", "alt_text": banner.alt_text or "",
            "destination_raw": banner.destination_raw or "", "destination_type": dtype or "",
            "artwork_brands": " | ".join(check.get("banner_brands") or []), "artwork_deal": check.get("banner_deal") or "",
            "listing_title": (result or {}).get("listing_title") or "", "tool_result": shown_result(result) if result else "",
            "seen_in_run": run_id}


def _read_existing(path: Path) -> tuple[list[str], set[str]]:
    """(header columns, banner_ids already present) of an existing file, ignoring # lines like the loader does."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:          # utf-8-sig: Excel and this tool both write a BOM
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    ids = {(r.get("banner_id") or "").strip() for r in reader}
    return list(reader.fieldnames or COLUMNS), {i for i in ids if i}


def write_template(run_dir: Path, out: Path, scope: str = "hero") -> dict:
    """Create `out`, or append the banners it doesn't have yet. Returns {"added": n, "already": n, "path": ..., "created": bool}."""
    banners = [b for b in select_banners(load_banners(run_dir), scope) if not b.hidden]
    results = load_final_results(run_dir)
    rows = [context_row(b, results.get(b.banner_id), run_dir.name) for b in banners]
    seen, unique = set(), []
    for r in rows:                                   # one row per banner id, feed order
        if r["banner_id"] not in seen:
            seen.add(r["banner_id"]); unique.append(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=[*COLUMNS, *CONTEXT], extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(unique)
        out.write_text(HEADER_NOTE + buf.getvalue(), encoding="utf-8-sig")
        return {"added": len(unique), "already": 0, "path": out, "created": True}
    header, present = _read_existing(out)
    new = [r for r in unique if r["banner_id"] not in present]
    if new:
        text = out.read_text(encoding="utf-8-sig")
        with open(out, "a", encoding="utf-8", newline="") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            csv.DictWriter(f, fieldnames=header, extrasaction="ignore", restval="", lineterminator="\n").writerows(new)
    return {"added": len(new), "already": len(unique) - len(new), "path": out, "created": False}


def main() -> None:
    ap = argparse.ArgumentParser(description="Write or top up the fill-in reference CSV from a finished run")
    ap.add_argument("--run", type=Path, help="run folder to read (default: the newest one with results)")
    ap.add_argument("--scope", choices=["hero", "all"], default="hero")
    ap.add_argument("--out", type=Path, default=DEFAULT_PATH)
    args = ap.parse_args()
    run_dir = args.run or latest_run_dir()
    got = write_template(run_dir, args.out, args.scope)
    verb = "wrote" if got["created"] else "topped up"
    rest = "" if got["created"] else f", {got['already']} already there (left untouched)"
    print(f"{verb} {got['path']}: {got['added']} new banner row{'s' if got['added'] != 1 else ''}{rest} (from {run_dir.name}, scope {args.scope})")


if __name__ == "__main__":
    main()
