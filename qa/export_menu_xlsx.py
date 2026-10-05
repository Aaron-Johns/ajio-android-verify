"""Excel workbook of the menu parts of the app (qa/app_menus.py): one sheet per kind, one row per item, the picture embedded as a thumbnail
and clickable (it opens the item's link), then the alt text, the title (the text shown under the picture) and the link in their own columns.

    build_workbook([{"menu": <menu.json as a dict>, "dir": <the run folder>}, ...], path)

xlsxwriter, not openpyxl, because only xlsxwriter can make a picture itself a link."""
from __future__ import annotations

import io
from pathlib import Path

import xlsxwriter
from PIL import Image

from qa.app_menus import web_url

SHEETS = {"top-nav": "Top menu", "bottom-nav": "Bottom menu", "ads": "Sponsored ads", "trending": "Trending"}
NAV_ICON, AD_WIDTH, AD_HEIGHT, TREND_W, TREND_H = 56, 440, 125, 70, 100


def _thumb(path: Path, max_w: int, max_h: int) -> tuple[bytes, int, int] | None:
    """A saved picture shrunk to fit max_w x max_h, as PNG bytes; None when it can't be embedded (an SVG, a file that is gone)."""
    try:
        im = Image.open(path)
        im.seek(0)
        im = im.convert("RGBA")
        scale = min(max_w / im.width, max_h / im.height, 1.0)
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue(), im.width, im.height
    except Exception:
        return None


def _path_of(items: dict[int, dict], item: dict | None) -> str:
    names = []
    while item:
        names.append(item["title"])
        item = items.get(item["parent"])
    return " > ".join(reversed(names))


def build_workbook(entries: list[dict], path: Path) -> Path:
    wb = xlsxwriter.Workbook(str(path))
    head = wb.add_format({"bold": True, "bg_color": "#1F3A5F", "font_color": "#FFFFFF", "valign": "vcenter", "text_wrap": True, "border": 1})
    wrap = wb.add_format({"text_wrap": True, "valign": "vcenter"})
    bold = wb.add_format({"bold": True, "text_wrap": True, "valign": "vcenter"})
    link_fmt = wb.add_format({"font_color": "#0563C1", "underline": 1, "valign": "vcenter", "text_wrap": True})

    readme = wb.add_worksheet("Read me")
    readme.set_column(0, 0, 130)
    lines = ["AJIO app menus: one row per item",
             "Columns: the picture, its alt text (only where the app supplies one), its title (the text shown under the picture) and its link. Clicking the picture opens the link.",
             "Entries marked 'app screen' open a screen inside the app and have no web address, so their picture is not a link."]
    for e in entries:
        m = e["menu"]
        lines.append(f"{SHEETS[m['kind']]}: fetched {m.get('fetched_at', '')[:16].replace('T', ' ')} UTC, shopper segment (l1) {m.get('l1', '')}"
                     + (f", state {m['state']}" if m["kind"] == "ads" else "") + f", {len(m['items'])} entries"
                     + ("; slots: " + ", ".join(f"{i['title']} ({i['opens']})" for i in m["items"] if i["parent"] is None) if m["kind"] == "ads" else ""))
    for i, line in enumerate(lines):
        readme.write(i, 0, line, bold if i == 0 else wrap)

    for e in entries:
        m, base = e["menu"], Path(e["dir"])
        kind, items = m["kind"], m["items"]
        by_id = {i["id"]: i for i in items}
        ws = wb.add_worksheet(SHEETS[kind])

        def picture(row: int, col: int, item: dict, which: int, max_w: int, max_h: int) -> None:
            files = item.get("image_files") or []
            name = files[which] if which < len(files) else None
            t = _thumb(base / "images" / name, max_w, max_h) if name else None
            if not t:
                return
            opts = {"image_data": io.BytesIO(t[0]), "object_position": 1, "x_offset": 4, "y_offset": 3, "description": item["title"] or "picture"}
            if web_url(item.get("link")):
                opts["url"] = web_url(item["link"])
            ws.insert_image(row, col, "picture.png", opts)

        def link(row: int, col: int, item: dict) -> None:
            ws.write_url(row, col, web_url(item["link"]), link_fmt, string=web_url(item["link"])) if web_url(item.get("link")) else ws.write(row, col, item.get("opens", ""), wrap)

        def address(item: dict) -> str:
            return " | ".join(item.get("images") or [])

        if kind in ("top-nav", "bottom-nav"):
            cols, widths = ["Image", "Second image", "Alt text", "Title", "Link", "What it opens", "Path", "Active", "Audience", "Picture address"], [11, 11, 14, 24, 55, 26, 60, 8, 14, 50]
            rows = items
        elif kind == "ads":
            cols, widths = ["Image", "Second image", "Alt text", "Title", "Link", "Slot", "Rank", "Picture address"], [62, 30, 14, 14, 60, 22, 7, 50]
            rows = [i for i in items if i["parent"] is not None] or []
        else:
            cols, widths = ["Image", "Alt text", "Title", "Description", "What it opens (in-app search)", "Picture address"], [14, 14, 24, 60, 40, 50]
            rows = items
        for c, (name, w) in enumerate(zip(cols, widths)):
            ws.write(0, c, name, head)
            ws.set_column(c, c, w)
        ws.set_row(0, 30)
        ws.freeze_panes(1, 0)
        for r, item in enumerate(rows, start=1):
            if kind in ("top-nav", "bottom-nav"):
                ws.set_row(r, 46)
                picture(r, 0, item, 0, NAV_ICON, NAV_ICON)
                picture(r, 1, item, 1, NAV_ICON, NAV_ICON)
                ws.write(r, 2, item.get("alt", ""), wrap)
                ws.write(r, 3, item["title"], bold)
                link(r, 4, item)
                ws.write(r, 5, item.get("opens", ""), wrap)
                ws.write(r, 6, _path_of(by_id, item), wrap)
                ws.write(r, 7, "yes" if item.get("active", True) else "no")
                ws.write(r, 8, item.get("audience", ""))
                ws.write(r, 9, address(item), wrap)
            elif kind == "ads":
                ws.set_row(r, 96)
                picture(r, 0, item, 0, AD_WIDTH, AD_HEIGHT)
                picture(r, 1, item, 1, 220, 62)
                ws.write(r, 2, item.get("alt", ""), wrap)
                ws.write(r, 3, item["title"], bold)
                link(r, 4, item)
                ws.write(r, 5, by_id[item["parent"]]["title"], wrap)
                ws.write(r, 6, item.get("description", "").replace("rank ", ""), wrap)
                ws.write(r, 7, address(item), wrap)
            else:
                ws.set_row(r, 80)
                picture(r, 0, item, 0, TREND_W, TREND_H)
                ws.write(r, 1, item.get("alt", ""), wrap)
                ws.write(r, 2, item["title"], bold)
                ws.write(r, 3, item.get("description", ""), wrap)
                ws.write(r, 4, item.get("opens", ""), wrap)
                ws.write(r, 5, address(item), wrap)
        ws.autofilter(0, 0, max(len(rows), 1), len(cols) - 1)
    wb.close()
    return path
