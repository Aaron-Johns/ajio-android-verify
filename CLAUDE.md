# AJIO Feed Verify: Build Brief for Claude Code

This is a **new project**, not a continuation of an earlier UI-automation-based one. Read this whole file before writing code.

## 0. Markers used in this file

- **[D] Decided.** Implement as written.
- **[A] Assumed default.** Left open. Implement behind config, log it, tell the user. If contradicted later, update this file (§0.1).
- **[!] Hard constraint.** Do not violate.

## 0.1 Working agreement

- **Work in phases**, driven by slash commands in `.claude/commands/`: `/phase1-static-analysis`, `/phase2-traffic-capture`, `/phase3-api-client`, `/phase4-matching`, `/phase5-spotcheck`, `/phase6-app`, and `/checkpoint`. Do only the phase given, then stop and report.
- **Keep `PROGRESS.md` at the repo root**, appended after each phase: what was built, what was tested, [A] defaults relied on, decisions the user made, open items. Read it at the start of every session.
- **Keep this file current.** When the user answers something in §9, change the relevant [A] to [D] here.
- **Dev machine [A]: Windows.**
- **Secrets** (session tokens, device IDs, API keys) live in `.env`, loaded via environment variables only. Never write them into code, logs, reports, or committed files.
- **Legal boundary [!]:** this project reverse-engineers and calls a private API of an app the user doesn't control. Proceed on the user's word that this is authorized (internal QA, a vendor relationship, or their own account), but never expand scope past reading data — no writing/mutating calls to AJIO's backend, no bulk scraping beyond what QA needs, no distributing the decompiled source or patched APK anywhere.
- **Git:** `inputs/apk-source/` and `inputs/modified-apk/` are gitignored — decompiled source and a patched APK are large and not yours to redistribute, even in a private repo. Don't commit or push without being asked.

## 1. What we're building

A QA system that verifies AJIO promotional banners lead to the correct destination, **by reading the destination data directly from the app's own backend feed**, instead of tapping through the UI to find out.

The insight: the home-screen feed API response almost certainly already contains each banner's destination (a deep link, redirect URL, or category/brand identifier) before any tap happens. If we can read that response, verification becomes "does the feed's declared destination match the expected one", not "tap and see where we land."

Emulator-based tap-through becomes a secondary, low-frequency **spot-check**: confirms the app actually honors what its own feed says, catches client-side bugs the feed data wouldn't show. It is not the primary verification path.

## 2. Provided inputs (read-only)

All in `inputs/`, all **read-only** — `.claude/settings.json` denies edits there. Copy what's needed elsewhere; never modify or move originals.

| File / folder | What it is |
|---|---|
| `apk-source/` | Decompiled AJIO APK (apktool/jadx output) — user drops this in before Phase 1 |
| `modified-apk/` | The APK the user already patched to run on their emulator (pinning/checks removed) |
| `additional-apks/` | Two split APKs from the Android App Bundle install: an `xxhdpi` density split and an `arm64` (arm64-v8a) architecture split. Needed alongside a rebuilt base APK — see §4.1 |
| `brand_verify_combined.py` | Brand resolver: normalized exact → whole-word substring → difflib fuzzy → manual-verification flag. [!] Matching logic must not change |
| `ajio_brand_names_fixed.json` | Canonical brand list, flat array of ~7,000 strings |
| `brand_aliases.draft.json` | 22 groups of same-brand name variants found in the brand list (e.g. LEVI'S / LEVIS). Draft — confirm with user (§9) |
| `resolver_fixtures.json` | 42 known-good input/output pairs for the resolver. Regression test — must pass exactly |
| `image_segmentation_2.py` | Existing Gemma banner-analysis script (prompt + Interactions API pattern). Reused only for the Phase 5 spot-check, and only when a deep link is ambiguous |

If `apk-source/` or `modified-apk/` still contain only the placeholder `.txt` file when a phase starts, stop and ask the user to drop the real files in first.

## 3. Stack and structure [D]

- Python 3.11+.
- `mitmproxy` (traffic capture), `frida` + `frida-tools` (SSL-pinning bypass on-device, if the modified APK doesn't already remove it), `requests` (direct API calls once the endpoint is known).
- FastAPI + APScheduler + SQLite for the app-level scheduler/history/UI (Phase 6) — much lighter than a UI-automation project needs, since the primary check is just an HTTP call.
- Appium/UiAutomator2 only for Phase 5 (optional spot-check), not for primary verification.

```
ajio-feed-verify/
  inputs/                      # read-only, see §2
  config/settings.yaml
  analysis/FINDINGS.md         # Phase 1 output
  analysis/traffic_capture/    # Phase 2 raw captures
  qa/
    feed_client.py             # Phase 3: direct API client
    deeplink_resolve.py        # Phase 4: parse deep link -> brand/category/PLP
    brand_resolver.py          # ported from inputs/brand_verify_combined.py
    compare.py  status.py  reference.py
    spotcheck/                 # Phase 5: optional emulator tap-through + vision
  worker.py  scheduler.py
  web/
  runs/
  tests/
  PROGRESS.md
```

## 4. Phase 1: static analysis of the decompiled source [D]

Search `inputs/apk-source/` (smali or Java, plus `AndroidManifest.xml` and `res/`) for:

1. **The home-feed / CMS-widget endpoint.** Search for strings like `cms`, `widget`, `banner`, `carousel`, `home`, `feed`, and for the app's base URL constant. Report the full URL pattern and HTTP method.
2. **The banner/widget data model.** Classes named like `Banner`, `Widget`, `CmsWidget`, `HomeData`, `PromoTile`. Report every field, especially anything that could hold a destination: `redirectUrl`, `deepLink`, `targetUrl`, `actionUrl`, `navigationUrl`, `linkType`, `linkValue`.
3. **Deep-link handling.** The manifest's `intent-filter` entries (`android:scheme`, `android:host`, `android:pathPattern`) and the activity/class that parses an incoming deep link. This tells you the URL grammar: what a deep link into a PLP, a category, or a brand page actually looks like.
4. **Auth / session requirements.** How the app attaches identity to a request — session token, device ID, cookie, an HMAC-style request signature. Search for `Interceptor`, `Authorization`, `X-`, `signature`, `hmac`, `deviceId`, `sessionId`. This determines whether Phase 3 can call the API standing alone or needs a session bootstrapped from a real login.
5. **Certificate pinning.** `CertificatePinner`, custom `TrustManager`, `checkServerTrusted`. Report whether it's present in `apk-source/` and, by diffing against `modified-apk/` if feasible (unzip both, compare relevant classes/resources), what the user's patch actually changed.

Write findings to `analysis/FINDINGS.md`: the endpoint URL(s), the full field list per relevant class, the deep-link URL grammar with examples, the auth mechanism, and the pinning status. Include exact file paths and code snippets, not just descriptions. [!] Don't write any network or capture code yet.

### 4.1 If a rebuild is ever needed [D]

Nothing in this project's primary path (Phases 1–4, 6) requires rebuilding an APK — Phase 1 only reads the decompiled source, and Phase 3 talks to the API directly. A rebuild only comes up if Phase 2 or Phase 5 needs a *newly* re-patched APK (e.g. the existing `modified-apk/` doesn't cover something Phase 1 found, or an app update breaks it).

**Confirmed:** the user's install is an Android App Bundle split install, not a single APK. `inputs/additional-apks/` holds two split APKs alongside the patched base:
- a density split for `xxhdpi`
- an architecture split for `arm64` (arm64-v8a)

Rebuild sequence, if it's ever needed:
1. Repackage the modified decompiled source back into an APK: `apktool b <apk-source-dir> -o base-rebuilt.apk`.
2. Sign it (debug key is fine for an emulator): `apksigner sign --ks debug.keystore base-rebuilt.apk`.
3. Install all three together in one call — **order and inclusion of all three matters**, a partial or wrongly-ordered `install-multiple` can silently produce a broken or half-installed app:
   ```
   adb install-multiple base-rebuilt.apk <xxhdpi-split>.apk <arm64-split>.apk
   ```
4. If `additional-apks/` is missing either split by the time this is needed, stop and ask the user rather than guessing a substitute density/ABI.

[!] Still get the user's explicit go-ahead before running this on their emulator, even though the mechanics are now known — a rebuild replaces whatever is currently installed and working.

## 5. Phase 2: traffic capture [D]

Confirm the static findings against what the app actually sends and receives.

1. **Emulator setup.** Boot the emulator, install `inputs/modified-apk/`. If it still has pinning (Phase 1 found none removed), set up Frida with an SSL-unpinning script against the app process; otherwise skip straight to the proxy.
2. **mitmproxy.** Start mitmproxy on the host, point the emulator's HTTP proxy at it (`emulator -http-proxy` or Android proxy settings), launch the app, and capture the home-screen load.
3. **Compare** the captured request/response against Phase 1's findings: same endpoint, same field names, and — critically — the actual auth headers/cookies/tokens the app sends. Note what's ephemeral (expires) vs. static (an API key baked into the app) so Phase 3 knows what it can hardcode versus must refresh.
4. Save raw captures (request + response, headers redacted of anything secret) to `analysis/traffic_capture/`. Update `analysis/FINDINGS.md` with what matched, what didn't, and the exact headers Phase 3 needs to send.

[!] Capture only what's needed to verify banner destinations — the home feed load and, if a banner's target isn't obvious from that response alone, the destination page's own load. Don't sweep up unrelated traffic (checkout, payments, personal account data).

## 6. Phase 3: direct API client [D]

Build `qa/feed_client.py`: calls the feed endpoint directly over plain `requests`, no emulator involved, using what Phase 2 confirmed.

- **Session handling [A]:** if the endpoint needs an ephemeral token, add a `qa/session_bootstrap.py` that drives the emulator once (headless, via Appium) to obtain a fresh token and caches it with its expiry; `feed_client.py` refreshes automatically when it's stale. If the endpoint works with a static key/no auth, skip this entirely — flag which case applies once Phase 2 confirms it.
- Parse the response into a list of banner records: `{banner_id, image_url, position, destination_raw}`, where `destination_raw` is whatever field carried the target (deep link, redirect URL, or a structured object).
- Retries with backoff on 429/5xx. Log raw responses (headers redacted) to the run folder for auditability — a QA tool should be able to show its receipts.
- Unit test against a saved, redacted sample response, not the live API, for anything CI-like.

## 7. Phase 4: destination resolution and matching [D]

1. **Parse `destination_raw`** using the deep-link grammar from Phase 1 (§4.3) into a structured target: `{type: PLP|CATEGORY|BRAND|CAMPAIGN|EXTERNAL, brand: ..., category: ..., identifier: ...}`. Put this in `qa/deeplink_resolve.py`, kept separate from the brand resolver.
2. **Resolve brand/category text** through the ported resolver (`qa/brand_resolver.py`, from `inputs/brand_verify_combined.py`, verbatim matching logic, same as the retired UI-automation project — see §7.1 below for the carried-over findings).
3. **Compare** against reference data (§8) using the same alias-aware approach: `inputs/brand_aliases.draft.json` groups (pending user confirmation), multi-brand rule `dominant` [A].
4. **Statuses:** `MATCH`, `MISMATCH`, `AMBIGUOUS_DEEPLINK` (grammar didn't fully resolve — falls through to Phase 5's vision fallback if a spot-check is running), `NO_REFERENCE`, `ERROR`. `INCOMPLETE_NAVIGATION` doesn't apply here since there's no hop chain in the primary path; it can reappear inside Phase 5's tap-through.

### 7.1 Resolver findings carried over from prior analysis [D]

These were found by running the actual resolver against the actual 7,000-brand list and are still true here — the brand list and resolver code are unchanged:

- **Step 2 (whole-word substring match) auto-accepts ambiguous matches.** A query like "Kids", "Red", "Polo", or "Fashion" matches many brands and the resolver silently picks the shortest one at 0.95 confidence. Real examples: `Polo → Polo Plus` (ignoring U.S. Polo Assn., Beverly Hills Polo Club, +4 others), `Kids → Aks Kids` (ignoring 14+ others). [!] Fix at the status layer, not in the resolver: treat any `word_boundary` result whose `possible_ajio_brands` has more than one entry as `AMBIGUOUS_DEEPLINK`/needs-verification, never an automatic match.
- **Empty brand list from parsing looks clean** (no verification key gets written) — treat empty as needing verification.
- **22 name-variant pairs need alias-aware comparison** (`brand_aliases.draft.json`), e.g. LEVI'S/LEVIS, RED TAPE/REDTAPE, U.S. Polo Assn./US POLO ASSN. Plain string comparison falsely reports these as mismatches. **[D] User approved all 22 groups as drafted (2026-09-21).**
- **3 pairs collide under the resolver's own normalization** and always return ambiguous: GINI & JONY/GINI AND JONY, Oomph/OOMPH!, ZRI/Zri. **[D] User approved deduping in a copy of the data file (2026-09-21):** `config/ajio_brand_names_deduped.json`, original in `inputs/` untouched. [A] The user didn't pick canonical spellings; the first name of each pair is kept (GINI & JONY, Oomph, ZRI). The regression fixtures include "Gini & Jony" and "Oomph", so they must run against the ORIGINAL list, not the deduped copy.
- Regression test: `resolver_fixtures.json` must pass exactly after porting.

## 8. Reference data [D]

Same shape as before — ask the user for 2–3 real rows before building `reference.py` beyond a stub:

```
banner_id, expected_brand, expected_category, expected_deeplink_type, notes
```

Matching a feed banner to a reference row is much easier here than in the UI-automation version: the feed response likely has a stable `banner_id` or campaign ID, not just a rotating image. Confirm this in Phase 1/2 and use it as the join key if present; fall back to image URL or perceptual hash only if the feed has no stable ID.

## 9. Open questions: implement defaults, but raise these with the user

1. Does the feed endpoint need a live session/auth token, or is it callable statelessly? (Determines whether §6's session bootstrap is needed at all.) **Answered by Phase 3 [D]:** no session bootstrap; the theme endpoint takes a static bearer (`AJIO_THEME_BEARER` in `.env`) plus a locally computed `x-fp-signature`. See `analysis/FINDINGS.md` 6.4.1 and 6.8.
2. Real reference data rows and what identifies a banner in them (§8). **Open (2026-09-21):** the user chose to proceed with a stub and placeholder rows derived from the live feed (`config/reference.sample.csv`); real rows are still needed. [A] Join key is the feed's section `_id` (`<_id>:<block index>` for carousel blocks), since the feed has no numeric banner ID.
3. Confirm `brand_aliases.draft.json` groups and the dedupe of the 3 colliding pairs (§7.1). **Answered 2026-09-21 [D]:** all 22 groups approved as drafted; dedupe approved (in a copy).
4. How often should the direct API check run? Since it needs no emulator, it can run far more frequently than a UI-automation check could — hourly is plausible. [A] default: every 2 hours.
5. How often should the Phase 5 emulator spot-check run, and how many banners per run? [A] default: weekly, 5 banners.
6. Confirm scope boundary (§0.1): read-only calls to AJIO's backend, no distribution of `apk-source/` or the patched APK.

## 10. Phase 5: spot-check (optional, secondary) [D]

Reuses Appium + the emulator, much like the retired UI-automation approach, but only to answer one question per sampled banner: **does tapping this banner actually land where the feed said it would?** Not to discover the destination from scratch.

- Sample `spotcheck.banners_per_run` banners (default 5) from the current feed pull.
- Tap each, capture the landing page, and check it against the feed's declared destination using the same PLP/brand signals as before (activity name, product grid, brand text on cards).
- If the feed's destination type was `AMBIGUOUS_DEEPLINK` from Phase 4, use Gemma vision (`inputs/image_segmentation_2.py`, ported with the same bug fixes as before — verification-flag OR-not-override, robust JSON-fence parsing, no `getpass` at import time, delete uploaded files after use) to classify the landing page and resolve the ambiguity.
- Status: `CONFIRMED`, `APP_DEVIATES_FROM_FEED` (the interesting bug this catches — feed says one thing, app does another), `INCONCLUSIVE`.

## 11. Phase 6: app (scheduler, history, UI) [D]

- `config/settings.yaml`: feed-check interval, spot-check interval and sample size, resolver thresholds (85/5 defaults, from the brand list), reference data source, session/auth settings, screenshot/log retention.
- APScheduler running the feed check on its own cadence and the spot-check on its own, independently.
- SQLite run history; web UI (127.0.0.1 only) showing recent feed-check results, spot-check results, and any `APP_DEVIATES_FROM_FEED` findings highlighted, since those are the actionable bugs.
- Replay mode: re-run matching against a saved feed capture with no live network or emulator, for testing rule changes.

## 12. Known risks

- The feed endpoint, field names, or auth mechanism can change with an app update — nothing here is stable long-term the way a public documented API would be. Re-run Phase 1 after any AJIO app update.
- A signed/HMAC request scheme, if present, may be expensive to reverse-engineer fully; Phase 1 should flag this early rather than assume `requests` alone will work.
- Frida attaching to a hardened app can be flaky; if the modified APK already strips pinning, prefer that over live Frida injection for daily runs — reserve Frida for re-deriving things after an app update.
- The deep-link grammar may not cover every case (some banners might point to a WebView or an external browser link) — Phase 4's `AMBIGUOUS_DEEPLINK` status exists exactly for this, feeding Phase 5's vision fallback.
