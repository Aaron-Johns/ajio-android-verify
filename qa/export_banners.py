"""Export the captured feed banners to an Excel-friendly CSV plus downloaded image files.

Run: python -m qa.export_banners [--run runs/<stamp>] [--out data] [--no-images]

Writes <out>/banners.csv (UTF-8 with BOM so Excel opens it correctly) and <out>/images/.
Reads the saved response of a previous run (default: the latest runs/*/home.response.json), so
no feed call is made; only the banner images are downloaded, once per unique URL.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

from qa import deeplink_resolve as dl
from qa.feed_client import RUNS_DIR, parse_banners

COLUMNS = [
    "position", "banner_id", "section_type", "label",
    "image_url", "image_file", "image_status",
    "destination_raw", "destination_type", "target_brand", "target_category", "target_identifier",
    "destination_host", "destination_note", "hotspot_urls", "schedule", "user_type",
    "section_index", "block_index", "feed_slug", "fetched_at",
]
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}


def latest_run(slug: str = "home") -> Path:
    runs = sorted(RUNS_DIR.glob(f"*/{slug}.response.json"))
    if not runs:
        raise FileNotFoundError(f"no runs/*/{slug}.response.json found; run `python -m qa.feed_client {slug}` first")
    return runs[-1].parent


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


def download_images(urls: list[str], images_dir: Path, getter=requests.get, workers: int = 4) -> dict[str, tuple[str, str]]:
    """Return {url: (image_status, relative file path or '')}. Skips files that already exist."""
    images_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, tuple[str, str]] = {}

    def one(url: str) -> tuple[str, str, str]:
        status, data, ctype = fetch_image(url, getter)
        if data is None:
            return url, status, ""
        suffix = _EXT.get(ctype) or Path(url.split("?")[0]).suffix or ".bin"
        name = hashlib.sha256(data).hexdigest()[:16] + suffix
        target = images_dir / name
        if not target.exists():
            target.write_bytes(data)
        return url, "OK", f"{images_dir.name}/{name}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for url, status, rel in pool.map(one, urls):
            result[url] = (status, rel)
    return result


def _schedule_text(schedule: list[dict]) -> str:
    return "; ".join(f"{s.get('start', '?')} -> {s.get('end', '?')}" for s in schedule)


def _is_hero(b) -> bool:
    """Same rule as qa.spotcheck.hero.hero_candidates() - duplicated rather than imported, since
    that module imports download_images from this one and importing it back would be circular."""
    return b.block_index is not None and b.section_type == "hybrid-dynamic-banner" and bool(b.image_url)


def build_rows(theme: dict, images: dict[str, tuple[str, str]], slug: str, fetched_at: str, scope: str = "all") -> list[dict]:
    rows = []
    banners = parse_banners(theme)
    if scope == "hero":
        banners = [b for b in banners if _is_hero(b)]
    for b in banners:
        t = dl.resolve(b.destination_raw)
        status, path = images.get(b.image_url, ("no_image" if not b.image_url else "not_downloaded", ""))
        rows.append({
            "position": b.position,
            "banner_id": b.banner_id,
            "section_type": b.section_type,
            "label": b.label,
            "image_url": b.image_url or "",
            "image_file": path,
            "image_status": status,
            "destination_raw": b.destination_raw or "",
            "destination_type": t.type,
            "target_brand": t.brand or "",
            "target_category": t.category or "",
            "target_identifier": t.identifier or "",
            "destination_host": t.host or "",
            "destination_note": t.reason or "",
            "hotspot_urls": " | ".join(h.url for h in b.hotspots),
            "schedule": _schedule_text(b.schedule),
            "user_type": b.user_type or "",
            "section_index": b.section_index,
            "block_index": "" if b.block_index is None else b.block_index,
            "feed_slug": slug,
            "fetched_at": fetched_at,
        })
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


THUMB_MAX = (300, 150)  # px; keeps the workbook small while banners stay recognisable


def _thumbnail(path: Path):
    """Return an openpyxl Image scaled to THUMB_MAX, converted to PNG so any Excel can show it."""
    from openpyxl.drawing.image import Image as XlImage
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGBA")
        img.thumbnail(THUMB_MAX)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
    buf.seek(0)
    xl = XlImage(buf)
    return xl


def write_xlsx(rows: list[dict], path: Path, base_dir: Path) -> int:
    """Write an .xlsx with an embedded thumbnail in column A. Returns how many images were embedded."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "banners"
    ws.append(["image"] + COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "B2"
    ws.column_dimensions["A"].width = 44
    embedded = 0
    for i, row in enumerate(rows, start=2):
        ws.append([""] + [row[c] for c in COLUMNS])
        file = row["image_file"]
        if file and (base_dir / file).exists():
            try:
                xl = _thumbnail(base_dir / file)
            except Exception:
                continue
            ws.add_image(xl, f"A{i}")
            ws.row_dimensions[i].height = max(xl.height * 0.75 + 4, 18)
            embedded += 1
    for idx, name in enumerate(COLUMNS, start=2):
        letter = ws.cell(row=1, column=idx).column_letter
        ws.column_dimensions[letter].width = 60 if name in ("image_url", "destination_raw", "hotspot_urls") else 18
    for r in ws.iter_rows(min_row=2, min_col=2):
        for cell in r:
            cell.alignment = Alignment(vertical="center")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return embedded


def export(run_dir: Path, out_dir: Path, slug: str = "home", download: bool = True, getter=requests.get,
          scope: str = "all") -> list[dict]:
    theme = json.loads((run_dir / f"{slug}.response.json").read_text(encoding="utf-8"))
    fetched_at = run_dir.name
    try:
        fetched_at = datetime.strptime(run_dir.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    banners = parse_banners(theme)
    if scope == "hero":
        banners = [b for b in banners if _is_hero(b)]
    urls = sorted({b.image_url for b in banners if b.image_url})
    images = download_images(urls, out_dir / "images", getter) if download else {}
    rows = build_rows(theme, images, slug, fetched_at, scope=scope)
    for name, write in (("banners.xlsx", lambda p: write_xlsx(rows, p, out_dir)), ("banners.csv", lambda p: write_csv(rows, p))):
        try:
            write(out_dir / name)
        except PermissionError:
            print(f"WARNING: could not write {out_dir / name} - is it open in Excel? Close it and re-run.")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Export feed banners to CSV + images")
    ap.add_argument("--run", type=Path, help="run folder (default: latest with a home response)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    ap.add_argument("--slug", default="home")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--scope", choices=["hero", "all"], default="all", help="hero = only the hero carousel's slides")
    args = ap.parse_args()
    run_dir = args.run or latest_run(args.slug)
    rows = export(run_dir, args.out, args.slug, download=not args.no_images, scope=args.scope)
    ok = sum(1 for r in rows if r["image_status"] == "OK")
    print(f"{len(rows)} banners from {run_dir}; {ok} images OK; output folder: {args.out}")


if __name__ == "__main__":
    main()
