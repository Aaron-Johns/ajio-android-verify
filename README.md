# AJIO Feed Verify

A QA tool that checks whether AJIO's home-screen promotional banners actually lead to the
destination they claim to. Instead of tapping through the app to find out, it reads the destination
straight out of AJIO's own backend feed (the same data the app itself uses to build the home screen),
then checks that destination's real listing page to confirm the banner's brand/deal/audience claims
actually match what's there. It is a local web app with no phone or emulator involved.

This doc gets a new person from "just cloned the repo" to "running checks in the browser." For how
the tool was built and why, see `PROGRESS.md`; for the reverse-engineered API details, see
`analysis/FINDINGS.md`.

## Before you start

**This talks to AJIO's private backend API, not a public documented one.** Only use it against your
own account or with the same authorization the rest of this project already operates under (internal
QA / a vendor relationship) — see `CLAUDE.md` §0.1 for the full scope boundary. It only ever reads
data; it never taps a button or writes anything back to AJIO.

## 1. Prerequisites

- **Python 3.11 or newer** (built and tested on 3.14). Check with `python --version`.
- **git**, to clone the repo.
- Windows is assumed throughout this doc (the project's dev machine) — the web tool itself is
  plain Python/FastAPI and should run anywhere, but a couple of conveniences (`run.bat`, the
  "open folder" button on Excel exports) are Windows-only.

## 2. Get the code and set up the environment

```
git clone <this repo's URL>
cd ajio-feed-verify
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell refuses to run the activation script, run this once in that terminal first:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## 3. Set up `.env`

Secrets are never committed to the repo (`.env` is gitignored) — you need your own copy. Copy
`.env.example` to `.env` in the repo root (it lists every key, with the required ones first) and fill in
these two:

```
AJIO_THEME_BEARER=<required - the app's static theme bearer token>
GEMINI_API_KEY=<required - a Gemini API key, used to read banner images>
```

**Where do these come from?** Ask whoever gave you access to this project for a working `.env` —
that's the fast path. If you need to derive `AJIO_THEME_BEARER` yourself from scratch (e.g. the app
updated and it changed), that requires redoing the traffic-capture work described in
`analysis/FINDINGS.md` §6, which needs an Android emulator and is a more involved process — not
something to attempt just to get the web UI running for the first time.

A handful of other `.env` keys exist with sensible built-in defaults (device ID, pincode/location for
listing lookups, the app's application ID) — you only need to touch these if you have a specific
reason to. Search for `os.environ.get(` in `qa/` if you're curious what's configurable. The delivery
pincode specifically also has its own field in the web UI (see `docs/UI_GUIDE.md`), set per run rather
than needing an `.env` edit — the `.env` value is only the fallback when nothing overrides it.

## 4. Run it

Easiest way: double-click **`run.bat`** in the repo root. It starts the server and opens your browser
automatically after a few seconds.

Or manually:
```
.venv\Scripts\python.exe -m uvicorn web.api:app --port 8000
```
then open **http://127.0.0.1:8000** yourself. Either way, the server only listens on your own
machine (127.0.0.1) — nothing here is exposed to the network.

Leave that terminal window open while you work; closing it (or `Ctrl+C` inside it) stops the server.

### Start the server when you log in (optional, for scheduled runs)

Scheduled checks only fire while the server is running. To have Windows start it for you, run this once:
```
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1
```
It registers a per-user task, "AJIO Feed Verify server", that starts the server hidden 30 seconds after you log in (no admin
rights needed; logs go to `logs\server.log`). `-Status` shows whether it is installed and running, `-Remove` undoes it. It
never starts a second copy if the server is already up. If the server itself crashes or is killed, nothing restarts it until the
next logon, or run `Start-ScheduledTask "AJIO Feed Verify server"`.

To test it: `scripts\test_autostart.ps1` checks the task is set up right and, with `-Restart`, that the server comes back
through it. For the real proof, sign out and in, then run `scripts\test_autostart.ps1 -AfterLogon` (details in the script's header).

More on schedules and autostart: **[`docs/UI_GUIDE.md`](docs/UI_GUIDE.md)** (the scheduler section: how schedules run, what happens
to times missed while the PC is off) and **[`docs/API.md`](docs/API.md)** (the schedules and alerts endpoints).

## 5. Using it

Quick start: pick an **l1 / l2 segment** and a **scope** in the left panel, then hit **Run now**. The
grid on the right fills in live as each banner is checked — click any card for its full detail, and
once the run finishes, **Download Excel** gives you every result in a spreadsheet with the banner
images embedded.

There are **two interfaces on the same server and data**: the one above at `http://127.0.0.1:8000/`, and a
newer **manager view** at **http://127.0.0.1:8000/manager/** (runs as a contact sheet with a verdict strip, a
says-and-shows detail panel, schedules and alerts). Switch with the black glowing **Manager view** button in the original's
header, or **Classic view** in the manager's left rail. A run started in one shows up in the other.

Besides banners, the app's **menus, ads and trending** can be fetched too: tick **Top Nav / Bottom Nav / Ads / Trending** under *Menu* in *New run*, then browse them on the **Menu** page
(first line: the four parts; click one and its sub-entries open in the next line) and download them as an Excel sheet with the pictures. Nothing is checked, it only fetches the pictures and details.

Optionally you can tell the tool what each banner is *supposed* to lead to: `python -m qa.reference_template` writes a fill-in
`config/reference.csv` from your latest run, and every later check compares the banners you filled in against it (see
"Reference data" in `docs/UI_GUIDE.md`).

That's enough to get a result. For everything else — what each status actually means, the carousel
picker, Skip/Retry, scheduling, the cache, and a list of things worth knowing before you trust what
it tells you — see **[`docs/UI_GUIDE.md`](docs/UI_GUIDE.md)**.

## 6. Where to look for more

- `docs/UI_GUIDE.md` — the full UI walkthrough, in depth (the last section covers the manager view).
- `docs/API.md` — the HTTP API both interfaces use.
- `PROGRESS.md` — what's been built, phase by phase, with what was tested and what's still open.
- `analysis/FINDINGS.md` — the actual reverse-engineered API details (endpoints, auth, signing).
- `CLAUDE.md` — the original project brief and scope boundaries.
