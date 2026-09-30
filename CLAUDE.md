# AJIO Feed Verify: Project Brief

QA tool that checks AJIO's home-screen promo banners lead to the right page, **by reading the app's own backend APIs**
(no emulator, no browser, no HTML scraping). Built, in use, and running as a local web app. This file is the standing
brief: rules, decisions and how things fit together. `PROGRESS.md` is the history, `README.md` the setup guide,
`docs/UI_GUIDE.md` and `docs/API.md` the user/developer docs, `analysis/FINDINGS.md` the reverse-engineered API facts.

## 0. Markers, working agreement

- **[D] Decided.** Implement as written. **[A] Assumed default.** Left open: keep it behind config/constants, say so, and
  update this file if the user contradicts it. **[!] Hard constraint.** Never violate.
- **Read `PROGRESS.md` at the start of a session; append to it after each piece of work** (what changed, what was tested,
  [A] defaults, user decisions, what was *not* verified). Keep `docs/UI_GUIDE.md` and `docs/API.md` in step with any UI or
  API change, and this file in step with any decision that changes it.
- **Git [!]:** commit only when asked; push only when asked; never force-push without an explicit go-ahead; no
  `Co-Authored-By` / "generated with Claude" lines in commits or PRs (the user asked for that removed, 2026-09-29).
  Nothing under `inputs/apk-source/`, `inputs/modified-apk/`, `inputs/additional-apks/` is ever committed (gitignored).
  Before committing, check the staged files for secrets, `analysis/traffic_capture/**/*.flow` (live tokens) and phone
  screenshots (`screen_*.png`, they show the account holder's details): all gitignored, keep it that way.
- **Secrets [!]:** only in `.env` (gitignored), read through environment variables. Never in code, logs, reports, docs or
  commits. Secret keys: `AJIO_THEME_BEARER`, `GEMINI_API_KEY` (required); `FP_SIGNATURE_SECRET`, `AJIO_ACCOUNT_TOKEN`
  (only where the feed/cohort code asks for them). Non-secret tuning keys, all optional: `AJIO_DEVICE_ID`, `AJIO_PINCODE`,
  `AJIO_LATITUDE`, `AJIO_LONGITUDE`, `AJIO_APPLICATION_ID`, `AJIO_USER_GROUPS`, `AJIO_EXPERIMENTAL_FEATURES`,
  `AJIO_FP_SDK_VERSION`, `GEMMA_CALLS_PER_MINUTE`, `GEMMA_PACER_SHARED` (`grep environ.get qa/` for the truth; `README.md`).
- **Legal boundary [!]:** this reverse-engineers and calls a private API of an app the user doesn't control. Proceed on the
  user's word that it is authorized, but **stay read-only**: no mutating calls to AJIO's backend, no bulk scraping beyond
  what QA needs, no distributing the decompiled source or patched APK.
- **Dev machine [D]: Windows 11.** `python` is not on PATH: use `.venv\Scripts\python.exe`. PowerShell or Git Bash (POSIX
  syntax; avoid apostrophes in inline heredocs, write a script file instead).
- **Pronouns:** use they/them for anyone whose pronouns aren't stated.

## 1. What it does

For each banner in the home feed: read its artwork with Gemma vision (brands, deal text, audience), fetch the listing the
banner links to from AJIO's listing API, and compare. Result per banner: **PASS**, **FAIL** (a concrete mismatch),
**INCONCLUSIVE** (couldn't decide), **UNAVAILABLE** (a temporary AJIO/Google/network error outlasted 5 tries: not a finding
about the banner), **SKIPPED** (nothing checkable, hidden, or user-skipped). FAIL and INCONCLUSIVE need a human.
Runs on demand or on schedules, with diffs against the previous run and alerts. **The tool never opens `ajio.com` pages**;
a banner's URL is only used to extract the listing slug.

## 2. Architecture and layout

```
inputs/                    read-only originals (see 3)            config/: ajio_brand_names_deduped.json, reference.csv (yours), reference.sample.csv (format example)
analysis/FINDINGS.md       API facts (read 6.x first); traffic_capture/ holds captures (gitignored raw flows)
qa/
  feed_client.py fp_signer.py cohort_client.py   home feed: Fynd "theme" endpoint, static bearer + local x-fp-signature,
                                                 cohort (l1/l2/state/pincode) via a cached guest token in qa/.cache/
  deeplink_resolve.py brand_resolver.py compare.py status.py reference.py    Phase 4 destination parsing/matching (5)
  reference_check.py reference_template.py       optional human expectations (6): judged in feed_verify, template writer
  listing_client.py        search-edge listing API (no auth, app-identity headers); luxe.ajio.com links use store=luxe
  feed_verify.py           THE pipeline + CLI: shared retry queue, activity log, cross-run cache, shown_result()
  banner_cache.py run_diff.py export_xlsx.py export_banners.py asset_set.py envfile.py
  spotcheck/               vision.py (Gemma + shared pacer), hero.py, filters.py (gender/brand rules), landing/plp/runner
                           (the optional emulator tap-through; not used by the web tool)
web/                       FastAPI + APScheduler + SQLite (web/app.sqlite3, gitignored). api.py runner.py db.py alerts.py notify.py
  static/index.html        classic UI at /   (the default; do not replace it)
  static/manager/index.html  manager UI at /manager/ (same API, alternate face; keep the two at feature parity)
scripts/                   autostart.ps1 (Windows logon task "AJIO Feed Verify server"), start_server.ps1; run.bat
tests/                     ~600 tests (qa/ logic, API, run view); web pages are verified live in headless Edge (selenium)
runs/  logs/  data/        generated, gitignored
.claude/commands/          phase slash commands and /checkpoint (historical; the phases are done)
```

- **How it runs:** the server (`uvicorn web.api:app`, 127.0.0.1 only [!]) starts each check as a `qa.feed_verify`
  subprocess. Schedules live in-process (APScheduler); missed fires while the PC was off are ignored, not caught up.
- **Restarting:** `web/*.py` changes need a server restart (stop the process on port 8000, then
  `Start-ScheduledTask "AJIO Feed Verify server"`). `web/static/*` never does. `qa/` changes reach new run subprocesses at
  once, but the server imports some `qa` code itself (`shown_result`, `load_final_results`...), so restart after those too.
  A restart adopts a run that is mid-flight but loses combos queued in memory (known gap).
- **Limits [A]:** 24 runs per multi-select launch (`MAX_COMBOS`), 10 workers, Gemma paced to 6 requests a minute shared by
  every process (account limit 30 RPM / 16K TPM), 5 tries per banner (10 s cooldown, retried banners jump the queue),
  cross-run banner cache 24 h, one scheduled run at a time (waits up to an hour, then "skipped").

## 3. Provided inputs (read-only in `inputs/`)

`apk-source/` (decompiled APK), `modified-apk/` (user's patched APK), `additional-apks/` (xxhdpi + arm64 splits; a rebuild
needs `adb install-multiple base split split` and the user's explicit go-ahead first), `brand_verify_combined.py` +
`ajio_brand_names_fixed.json` (resolver and ~7,000 brands), `brand_aliases.draft.json`, `resolver_fixtures.json` (42 pairs
that must pass exactly), `image_segmentation_2.py` (origin of the Gemma prompt/pattern).

## 4. Decided behaviour ([D], mostly user calls; details and dates in PROGRESS.md)

- **Pages [D] (2026-09-30):** seven feeds, one Fynd theme slug each (`qa/pages.py`): `home`, `premium-men`,
  `premium-women`, `kids-premium-page` (UI: Prem men/women/kids), `menswear`, `womenswear`, `kidswear` (Non prem
  men/women/kids). **Only `home` depends on l1/l2**; the other six accept any cohort, so they run once per
  state x pincode with a fixed neutral cohort and the l1/l2 buttons grey out. Page is a fifth axis (`--page` on
  `qa.feed_verify`, `page` column on runs/schedules, `pages` in the API; absent means home). UI colours: Home black,
  premium gold, non-premium white; Home + premium on one line, non-premium under them.
- **Feed:** endpoint, auth and signing are in FINDINGS 6.4.1 / 6.8; no session bootstrap is needed. l1 in
  `nontransacted|premium|nonpremium`, l2 in `men|women|unisex|nogender`, plus state (default KARNATAKA) and pincode
  (default 560029), all multi-select. **`l1:premium` sometimes returns a non-premium asset set: known, ~90% fine, accepted;
  do not re-flag it as a bug.**
- **Gender rule:** closed table over Men/Women/Boys/Girls/Infants (`qa/spotcheck/filters.py` `_BANNER_GENDER_RULES`); an
  unrecognised banner audience makes the whole banner INCONCLUSIVE; AJIO beauty banners are flagged for a human.
- **Hidden / out-of-schedule banners** stay visible in the UI (toggle) but are never processed: always SKIPPED.
- **Retry:** automatic retries only for temporary errors. The per-banner **Retry** button (FAIL, INCONCLUSIVE, UNAVAILABLE)
  is a fresh, cache-free check (`--only-banner --no-cache`); offered while a run is going only once tries are spent.
  A FAIL with an unchecked hotspot stays FAIL; only that hotspot is UNAVAILABLE. AJIO's intermittent `400 NullPointerException`
  is retried like a 5xx.
- **UI:** finished runs are blue, running purple, cancelled grey, a run that itself failed red. UNAVAILABLE never counts as
  "needs a look" and never raises an alert. Logos: manager = white A in a black
  square with a white border, classic = the opposite.
- **Not tracked on purpose:** Gemma token usage (the user declined).

## 5. Destination resolution and brand matching [D] (Phase 4, still true)

Statuses of `qa/compare.py`: `MATCH`, `MISMATCH`, `AMBIGUOUS_DEEPLINK`, `NO_REFERENCE`, `ERROR`. Findings that must not regress:

- **Whole-word substring matches auto-accept ambiguous brands** ("Polo" -> "Polo Plus", "Kids" -> "Aks Kids"). [!] The
  resolver's matching logic must not change; fix it at the status layer: a `word_boundary` result with more than one
  `possible_ajio_brands` is `AMBIGUOUS_DEEPLINK`, never a match. An empty brand list also needs verification.
- **22 alias groups** (`brand_aliases.draft.json`, approved as drafted 2026-09-21) need alias-aware comparison (LEVI'S/LEVIS,
  RED TAPE/REDTAPE...). **3 pairs collide under the resolver's normalisation** (GINI & JONY, Oomph, ZRI); the deduped copy
  `config/ajio_brand_names_deduped.json` (first spelling kept [A]) is for runtime, but the regression fixtures include
  "Gini & Jony" and "Oomph" and must run against the ORIGINAL list. `resolver_fixtures.json` must pass exactly.
- Multi-brand rule: `dominant` [A] (the first of a pipe-separated expected brand).

## 6. Reference data (built; the rows are the user's to write)

`config/reference.csv` holds the human's expectations for banners they care about: `banner_id, expected_brand,
expected_category, expected_deeplink_type, notes` (join key: the feed's section `_id`, or `<_id>:<block index>` for carousel
slides; extra context columns are ignored). `python -m qa.reference_template` writes/tops it up from a finished run and never
touches filled-in rows. **Every check reads it automatically when it exists and has expectations** (`qa/feed_verify.py`, flags
`--reference PATH` / `--no-reference`). `qa/reference_check.py` judges a banner's own link against its row, on the **listing the
link really opens** (not the link text): link type (PLP family), every expected brand in the listing's Brands filter (alias-aware),
category contained in the listing title [A]. A MISMATCH makes the banner FAIL (an already-FAIL banner keeps its own reasons and gains
the finding); untestable things are UNCHECKED, never pass or fail. It runs after the banner cache, skips hidden/skipped banners and
ignores hotspots. Shown as a `reference_check` field, in the reason, a "Reference" row in the detail views, Excel and alert text.
`config/reference.sample.csv` is a format example (3 stale placeholder rows), and the old Phase 4 `qa/compare.py` (URL-text based)
plus the emulator spot-check's `--reference` still use `qa/reference.py` on their own. **No real rows exist yet: the data is held by
a colleague of the user (2026-09-30), and the user chose to leave the check as an OPTION until it arrives, so don't chase it or
nag. Do not invent expectations, and do not treat placeholder rows as evidence.** Docs: `docs/UI_GUIDE.md`, "Reference data".

## 7. Verification conventions

- `qa/` is unit-tested with fakes; nothing in the test suite touches the network. Run
  `.venv\Scripts\python.exe -m pytest tests -q` (~12 s). Add tests with every behaviour change.
- `web/` API logic is tested with TestClient against a throwaway SQLite; pages are exercised live in headless Edge against the
  running server. Test files must never touch the user's real runs, schedules or alerts: create throwaway rows with a `ZZ`
  prefix and delete them, and report anything you did not verify (real toasts, reboot autostart, a real button click that
  would start or alter real runs).
- Live calls to AJIO are read-only and few. Do not loop them.

## 8. Open questions (implement the default, raise with the user)

1. Feed auth: **answered [D]**, no session bootstrap (static bearer + local signature; FINDINGS 6.4.1, 6.8).
2. **Reference rows: the mechanism is built (6); the real expectations are with the user's colleague. Optional until they arrive, no action needed.**
3. Alias groups and the 3-pair dedupe: **answered [D] 2026-09-21**.
4. Check cadence: **answered by use**, schedules are user-defined per scheduler (interval, start time, multi-select axes); no global default.
5. Emulator spot-check: built (`qa/spotcheck/`) but **not scheduled by the web app** [A]; run it by hand when wanted.
6. Scope boundary (read-only, no redistribution): **confirmed by working on the user's word**; see 0.
7. Known gaps, not built: resuming queued multi-run batches after a server restart; a FAIL -> UNAVAILABLE -> FAIL sequence
   re-alerts "new FAIL"; results saved for `luxe.ajio.com` links before the store fix are stale; other AJIO hosts/stores are unexamined.

## 9. Risks

- The endpoint, field names, signing or bot protection (Akamai is present) can change with an app update; re-derive from
  `analysis/FINDINGS.md` and a fresh traffic capture (`ajio_nps` emulator, patched APK; see the memory notes on the capture setup).
- AJIO's listing service returns intermittent server errors; Google's Gemma returns 500/503 and rate limits. These surface as
  UNAVAILABLE, not as banner findings.
- Frida is blocked by the app's anti-tamper; use the SharedPreferences `ssl_pinning` toggle described in FINDINGS instead.
- Verdicts describe the data behind the page (listing JSON), not the rendered page and not what the app does on tap.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
