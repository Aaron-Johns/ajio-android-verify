"""Turns the script files (.bat, .js, .ps1) of this folder, and every folder below it, into .txt files and back again.

Why: a zip or an email that holds .bat / .js / .ps1 files is often blocked or stripped. As .txt files they travel safely.
Double-click this file (or run `python convert_scripts.py`), and it does whichever way applies:

    run.bat.txt, app.js.txt, start_server.ps1.txt ...   ->  run.bat, app.js, start_server.ps1 ...    (the folder you just unzipped)
    run.bat, app.js, start_server.ps1 ...                ->  run.bat.txt, app.js.txt, ...             (to pack it up again)

Names are only ever extended with, or stripped of, ".txt", and the contents are never touched, so a round trip gives back the exact files.
Nothing is overwritten: if the name it would write to is already taken, that file is left alone and listed. Python's own folders
(.venv, .git, __pycache__ ...) and the generated runs/ and logs/ are skipped, so the packages inside a virtual environment are safe.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXTENSIONS = (".bat", ".js", ".ps1")
SKIP_DIRS = {".venv", "venv", ".git", "node_modules", "site-packages", "__pycache__", "runs", "logs", ".cache"}


def files() -> list[Path]:
    """Every file under ROOT, except those inside a skipped folder."""
    return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts[:-1])]


def convert() -> str:
    everything = files()
    as_text = [p for p in everything if p.name.lower().endswith(tuple(e + ".txt" for e in EXTENSIONS))]
    if as_text:                                      # an unzipped folder: bring the scripts back
        moves, verb = [(p, p.with_name(p.name[:-4])) for p in as_text], "Turned back into scripts"
    else:                                            # a working folder: make it safe to zip or email
        moves, verb = [(p, p.with_name(p.name + ".txt")) for p in everything if p.suffix.lower() in EXTENSIONS], "Turned into .txt"
    done, skipped = [], []
    for source, target in moves:
        if target.exists():
            skipped.append(source)
            continue
        source.rename(target)
        done.append(target)
    lines = [f"{verb}: {len(done)} file(s)."] + [f"  {p.relative_to(ROOT)}" for p in done]
    if skipped:
        lines += [f"Left alone, because the name it would become already exists: {len(skipped)}"] + [f"  {p.relative_to(ROOT)}" for p in skipped]
    if not moves:
        lines = ["Nothing to convert: no .bat, .js or .ps1 files (or their .txt copies) here."]
    lines.append("Run this again to convert them back the other way.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(convert())
    try:
        if sys.stdin is not None and sys.stdin.isatty():      # a double-click opens a window that would close before it could be read
            input("\nPress Enter to close.")
    except EOFError:                                          # no keyboard attached (run from a script): nothing to wait for
        pass
