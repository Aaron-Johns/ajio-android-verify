"""Packs the files the app needs to run into one zip: no runs, no database, no secrets (.env), no tests or notes, no APK files or the app's
decompiled source. The .bat / .js / .ps1 files go in as .bat.txt / .js.txt / .ps1.txt (convert_scripts.py turns them back).

    .venv\\Scripts\\python.exe scripts\\make_core_zip.py [output.zip]        (default: ajio-feed-verify-core.zip on the Desktop)
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = "ajio-feed-verify"                      # the folder the zip unpacks into
CONVERTED = (".bat", ".js", ".ps1")
IGNORED = {"__pycache__", ".cache"}

# what "core" means: everything under these folders, plus these single files
FOLDERS = ["qa", "web"]
FILES = ["run.bat", "requirements.txt", ".env.example", "convert_scripts.py", "config/ajio_brand_names_deduped.json",
         "inputs/brand_aliases.draft.json",            # read by qa/compare.py on every run
         "scripts/start_server.ps1", "scripts/autostart.ps1"]
# inside those folders, never: the database, other requirements files, compiled files
LEFT_OUT = {"web/app.sqlite3", "web/requirements.txt"}

START_HERE = """AJIO Feed Verify: the core files

1. Unzip this folder.
2. Double-click convert_scripts.py. The .bat, .js and .ps1 files are packed as .txt so the zip is not blocked; this turns them back into
   scripts. (Double-click it again to turn them into .txt files whenever you want to zip or send the folder on.)
3. Put your keys in a file called .env in this folder. Copy .env.example to .env and fill in AJIO_THEME_BEARER and GEMINI_API_KEY (whoever
   gave you this folder can give you the values). Never share or commit .env.
4. In a terminal in this folder (Python 3.11 or newer; the pinned versions were taken from 3.14):
       python -m venv .venv
       .venv\\Scripts\\python.exe -m pip install -r requirements.txt
   Read the end of the pip output: if it ends in an error, nothing was installed and the next step fails with "No module named uvicorn".
5. Double-click run.bat. It starts the server and opens http://127.0.0.1:8000 (the classic view; the newer one is at /manager/).

Then: pick a page and press Run now. Under Menu you can also fetch the app's Top Nav, Bottom Nav, Ads and Trending; browse them on the Menu page and
download them as Excel. Runs and results appear in a runs folder next to this one; they are created as you go.

To start the server automatically when you log in to Windows: powershell -ExecutionPolicy Bypass -File scripts\\autostart.ps1
"""


def core_files() -> list[Path]:
    found: list[Path] = []
    for folder in FOLDERS:
        found += [p for p in sorted((ROOT / folder).rglob("*")) if p.is_file() and not IGNORED & set(p.relative_to(ROOT).parts)]
    found += [ROOT / f for f in FILES]
    return [p for p in found if p.relative_to(ROOT).as_posix() not in LEFT_OUT]


def build(target: Path) -> list[str]:
    names = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in core_files():
            rel = path.relative_to(ROOT).as_posix()
            arc = f"{FOLDER}/{rel}" + (".txt" if path.suffix.lower() in CONVERTED else "")
            z.write(path, arc)
            names.append(arc)
        z.writestr(f"{FOLDER}/START_HERE.txt", START_HERE)
        names.append(f"{FOLDER}/START_HERE.txt")
    return names


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop" / "ajio-feed-verify-core.zip"
    listed = build(out)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(listed)} files)")
