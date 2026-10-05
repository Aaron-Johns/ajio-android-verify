# API Reference

For anyone building their own UI (or script, or agent) against this tool instead of using the bundled
`web/static/index.html` or the manager view in `web/static/manager/`. This is the same API those UIs talk to — nothing here is a special/hidden
interface, it's the whole surface.

## Base URL, auth, CORS

- **Base URL:** `http://127.0.0.1:8000` (only binds to localhost — nothing here is reachable from
  another machine, by design; see `CLAUDE.md` §11).
- **Auth:** none. Anyone who can reach `127.0.0.1:8000` on the machine running the server can call
  everything below.
- **CORS:** wide open (`allow_origins=["*"]`) specifically so a browser-based UI on a different origin
  (e.g. a dev server on `localhost:5173`) can call it directly.
- **Format:** JSON in, JSON out, everywhere except the two file-download endpoints (images, Excel
  export) and the SSE stream.
- **Errors:** standard FastAPI/Starlette shape, `{"detail": "<message>"}`, with the HTTP status code
  carrying the meaning (`404` unknown id, `409` conflicting state, `422` bad input, `502` an upstream
  AJIO call failed).

## Core concepts

- **A run** is one execution of the check pipeline against a given l1/l2 cohort and scope. It's
  identified by a `run_id` (a string, actually a timestamp — `20260928T111636Z_feedverify` — but treat
  it as an opaque id, don't parse it except as documented under `export.xlsx` below). A run is a
  background subprocess; the API starts it and lets you poll or stream its progress.
- **A banner** is one promotional tile from AJIO's home feed. Every banner in a run's scope gets a row
  in that run's banner list from the moment the run starts (as `PENDING`), whether or not it's been
  checked yet — you always get the full list immediately, verdicts fill in as they're computed.
- **A schedule** is a saved run configuration that fires on a recurring interval on its own,
  independent of any manually-started run, and reports what changed since its last run.
- **A combination** is one (page, l1, l2, state, pincode) tuple. A run fetches the feed as exactly one, so a
  request that names several of any of them expands into their cross product — one run per
  combination — capped at `/api/meta`'s `max_combos` (24). **Only the `home` page uses l1/l2**: any other page
  ignores them (it runs once per state × pincode with the neutral cohort `nontransacted` / `unisex`). Those runs are started **one after another**,
  never concurrently, and share a `batch_id`.

## Runs

### `POST /api/runs` — start a run (or several)

Body:
```json
{
  "pages": ["home", "menswear"],
  "l1s": ["nontransacted", "premium"],
  "l2s": ["unisex"],
  "states": ["KARNATAKA"],
  "pincodes": ["560029", "400001"],
  "scope": "hero",
  "banner_limit": null,
  "workers": 3,
  "excluded_carousels": [],
  "excluded_sections": []
}
```
- `pages` — which app pages to check, each one of `/api/meta`'s `page_options[].id` (`home`, `premium-men`,
  `premium-women`, `kids-premium-page`, `menswear`, `womenswear`, `kidswear`). Optional; left out it means
  `["home"]`, so older clients behave as before. `home` expands over `l1s` × `l2s`; every other page is one run
  per state × pincode. The example above is 2 × 1 × 2 = 4 Home runs plus 2 menswear runs = 6.
  A **menu page** (`menu-top-nav`, `menu-bottom-nav`, `menu-ads`, `menu-trending`, from `menu_options`; see *Menu runs* below) may be mixed in: it
  is one run per `l1s` entry if its content depends on l1, otherwise one run (`menu-ads` also one per state), never per pincode or l2.
- `l1s` (required, ≥1) — each one of `/api/meta`'s `l1_options` (only used for the `home` page).
- `l2s` (required, ≥1) — each one of `/api/meta`'s `l2_options`.
- `states` — Indian states used in the home-feed request's `x-location-detail` header. Default
  `["KARNATAKA"]`; an empty list or blank entries also mean `["KARNATAKA"]` rather than an error (a
  non-blank but unrecognized value still 422s). Each must be one of `/api/meta`'s `state_options`.
- `pincodes` — delivery pincodes used for every listing lookup (sent to AJIO as `AJIO_PINCODE`), each a
  non-empty numeric string. Default `["560029"]`; empty means the default too. Different pincodes can
  get different product/brand data back from AJIO's search-edge API for the same listing, so this can
  change results.
- `scope` — `"hero"` (default) or `"all"`.
- `banner_limit` — `null`/omitted for everything in scope, or an integer ≥1 to cap it (first N in feed
  order).
- `workers` — 1 to `/api/meta`'s `max_workers` (currently 10). Default 3. Gemma requests are paced to
  `GEMMA_CALLS_PER_MINUTE` (default 7) across all workers and all runs on the machine (a shared lock file), so
  more workers only means more of them waiting their turn, not more requests.
- `excluded_carousels` — list of `section_index` ints to drop entirely from this run. Get real
  section indexes from `GET /api/feed-preview` first if you want to use this. A section index is
  positional, so it only means the same thing across combinations that fetch the same feed.
- `excluded_sections` — list of CMS section ids (`section_id` in `GET /api/feed-preview`) to drop
  entirely. Prefer this over `excluded_carousels` when starting several combinations: an id keeps
  naming the same carousel when the feed's order shifts, and in every combination whose feed has it.

Response:
```json
{"run_id": "20260928T111636Z_feedverify", "batch_id": "3f9c2a71b0de", "combos": 4, "queued": 3}
```
`run_id` is the **first** combination's run, started immediately; `queued` says how many more will
start, each as soon as the previous one has finished (they appear in `GET /api/runs` as they start,
with the same `batch_id`). Cancelling a run of the batch stops the rest of it; a run that fails does
not. The queue is in the server's memory — a server restart keeps the run in progress but forgets
combinations that hadn't started. `422` if any list is empty (`l1s`/`l2s`), any value isn't
recognized, a pincode isn't numeric, or the combination count is over the cap (the message says how
many it came to).

The first run starts in the background immediately; this call returns right away, it does not wait for
completion.

### `GET /api/runs?limit=50&schedule_id=` — run history

Returns a list of run summary objects, newest first. With `schedule_id`, only that schedule's runs
(hidden ones included), which is how a UI lists a schedule's history; without it, the recent runs,
minus any dismissed with `POST .../hide`.

```json
{
  "run_id": "20260928T111636Z_feedverify",
  "out_dir": "D:\\ajio-feed-verify\\runs\\20260928T111636Z_feedverify",
  "scope": "hero",
  "banner_limit": null,
  "workers": 3,
  "l1": "premium",
  "l2": "women",
  "pincode": "560029",
  "state": "KARNATAKA",
  "status": "done",
  "pid": 6072,
  "schedule_id": null,
  "started_at": "2026-09-28T11:16:36.988419+00:00",
  "finished_at": "2026-09-28T11:26:43.865897+00:00",
  "error": null,
  "hidden": 0,
  "batch_id": "3f9c2a71b0de",
  "excluded_carousels": [2, 15, 21, 34, 42, 45, 72, 89],
  "excluded_sections": [],
  "counts": {"FAIL": 2, "SKIPPED": 1, "PASS": 10, "INCONCLUSIVE": 3},
  "warnings": [],
  "diff": null
}
```
- `status` — `"running"`, `"done"`, `"failed"`, or `"cancelled"`.
- `l1` / `l2` / `state` / `pincode` — a run is always exactly one combination, so these are single
  values (unlike a schedule's plural fields).
- `batch_id` — shared by every run started together: one `POST /api/runs`, one scheduler fire, one
  schedule's run-now. `null` for runs from before batches existed. Group by it to show a fire's runs
  together.
- `counts` — a tally of `result` values across every banner that has one so far (missing/PENDING/
  PROCESSING banners aren't counted; a banner still inside its automatic retries counts as `PROCESSING`, and one
  that spent them on a server/network error as `UNAVAILABLE`). Recomputed fresh on every call, not cached.
- `warnings` — things the run flagged for a person, as plain sentences (from `warnings.json` in the run folder); empty when none. Today: "Premium cannot be loaded: ..." when AJIO kept returning the
  regular banner set for a Premium check, so the run checked that set. Both UIs show it above the banners.
- `kind` — `"feed"` for a banner run, `"menu"` for a menu run. `menu` — for a menu run only: `{kind, l1, l2, state, pincode, fetched_at, items, with_images}` once it has
  finished (`null` while it runs).
- `excluded_sections` — CMS section ids left out of the run (a schedule's saved carousel exclusions, or
  the ids the UI sent). Empty when none.
- `diff` — only for a *finished run that belongs to a schedule*, else `null`: the headline of what
  changed since the schedule's previous finished run **of the same l1/l2/state/pincode**:
  `{"baseline": false, "baseline_reason": null, "previous_run_id": "...", "counts": {...},
  "summary": "1 new FAIL, 1 fixed"}`. `baseline: true` means there was nothing comparable (the first
  run of that combination, or the schedule's settings changed since) and `baseline_reason` says which;
  a baseline never raises an alert. Full detail is at `GET /api/runs/{run_id}/diff`.
- `error` — the last ~4000 chars of stderr/stdout if `status == "failed"`, else `null`.
- Hidden runs (see `POST .../hide`) are excluded from the un-filtered list.

### `GET /api/runs/{run_id}` — one run's summary

Same shape as one item from the list above. `404` if `run_id` is unknown.

### `GET /api/runs/{run_id}/diff` — what changed since the previous run

Only for a finished run that belongs to a schedule (`404` otherwise): the full change list behind the
run summary's `diff` headline.
```json
{"baseline": false, "previous_run_id": "...", "counts": {"newly_failing": 1, "newly_inconclusive": 0,
 "recovered": 0, "new_banners": 0, "removed_banners": 0, "still_failing": 3},
 "newly_failing": [{"banner_id": "6a7c...:2", "alt_text": "...", "destination_raw": "...", "image_url": "...",
                    "was": "PASS", "now": "FAIL", "reason": "missing brands ['Levis']"}],
 "newly_inconclusive": [], "recovered": [], "new_banners": [], "removed_banners": [], "still_failing": 3}
```
Each list holds entries shaped like the one above (reasons are cut to 200 characters); `still_failing`
is just a count. A baseline has `baseline: true`, a `baseline_reason`, and empty lists. Computed on
first request for a run that finished before diffs existed.

### `GET /api/runs/{run_id}/banners` — every banner in this run, with its current status

Returns a flat list, one object per banner, in feed order. This is the single richest endpoint —
every field a UI needs to render a full result lives here. `404` if `run_id` is unknown.

```json
{
  "banner_id": "6a7c9d92a67938188bdf229b:2",
  "alt_text": "Buda Jeans Min 70% off",
  "destination_raw": "https://ajio.com/s/min50percentoff-curated-406999",
  "image_url": "https://assets-jiocdn.ajio.com/.../M-UHP-ST-MB-....jpeg",
  "asset_set": "ST",
  "section_index": 4,
  "block_index": 2,
  "position": 6,
  "image_file": "D:\\...\\images\\6a7c9d92a67938188bdf229b_2.webp",
  "result": "PASS",
  "reason": "",
  "hotspot_checks": [],
  "listing_kind": "curated",
  "slug": "min50percentoff-curated-406999",
  "listing_title": "Min 50 Percent Off",
  "total_results": 693,
  "brands_in_filter": 1,
  "banner_check": {
    "source": "server",
    "banner_brands": ["Catwalk"],
    "banner_deal": "MIN. 50% OFF",
    "listing_title": "Min 50 Percent Off",
    "title_matches_deal": true,
    "ajio_beauty_flag": false,
    "banner_gender": "women",
    "listing_genders": ["Women"],
    "gender_matches": true,
    "brand_checks": [{"brand": "Catwalk", "found": true, "matched_as": "CATWALK", "match": "exact", "products": 693}],
    "ignored_brands": [],
    "brand_list_exhaustive": true,
    "extra_brands": [],
    "missing_brands": [],
    "result": "PASS"
  },
  "attempts": 1,
  "try_number": 1,
  "max_tries": 5,
  "retries_exhausted": false,
  "total_links": 1,
  "hidden": false,
  "hidden_reason": ""
}
```

**Field notes** (fields not obvious from the name):
- `result` — one of `PENDING`, `PROCESSING`, `PASS`, `FAIL`, `INCONCLUSIVE`, `UNAVAILABLE`, `SKIPPED`. See the status
  table in `docs/UI_GUIDE.md` for what each means; briefly: `PASS`/`FAIL` are the pipeline's actual
  determinations, `INCONCLUSIVE` means it couldn't confidently decide, `UNAVAILABLE` means a temporary
  server/network error outlasted the 5 automatic tries (not a finding about the banner; `retries_exhausted` is
  `true`, so a per-banner retry is offered), `SKIPPED` means there was
  nothing checkable (or it was manually skipped), `PENDING`/`PROCESSING` are in-progress states that
  only appear while the run is live.
- `banner_check.spelling_errors` — present only when Gemma found misspelt words on the banner's own picture (not on a link's
  crop): `[{"word": "SUMER", "correction": "summer"}]`. It makes the banner `FAIL`, and the reason reads `spelling: "SUMER" should be "summer"`.
- `try_number` / `max_tries` — which attempt this is out of the max (always 5: 1 initial + 4 automatic
  retries). Only meaningful while `result == "PROCESSING"`.
- `activity` — present only while `result == "PROCESSING"` in a live run: what the banner is doing right
  now, one or two words: `Downloading`, `Cache check`, `Listing lookup`, `Reading image`,
  `Reading hotspot`, `Comparing`, `Waiting turn` (its Gemma request is queued behind the per-minute pace), `Cooling down` (a failed banner waiting out its ~10 s cooldown),
  `Retry queued` (cooldown over, waiting for a free worker), or `Working` when nothing more specific is
  known (a run started before this existed). A banner that has reported any activity counts as
  `PROCESSING` even before its image is on disk. Read from the run folder's `activity.jsonl`.
- `reference_check` — present only for a banner that has a row with expectations in `config/reference.csv`:
  `{"status": "MATCH" | "MISMATCH" | "UNCHECKED", "problems": [...], "unchecked": [...], "expected": {...}, "reason": "reference: ..."}`.
  A `MISMATCH` makes `result` `FAIL` (its `reason` text is in `reference_check.reason`; a banner that already failed keeps its own
  `reason`). `UNCHECKED` means an expectation couldn't be tested and never changes `result`. See `docs/UI_GUIDE.md`, "Reference data".
- `retries_exhausted` — `true` once a banner has used all its automatic tries and is still failing on
  a transient-looking error (or the run itself died before it could finish retrying). A `FAIL` banner whose
  hotspot never got checked can be `retries_exhausted` too; it stays `FAIL` and its `hotspot_checks[]` entry for
  that link has `result: "UNAVAILABLE"`.
- `can_retry` — `true` when the UI should offer **Retry**: the banner's result is `FAIL`, `INCONCLUSIVE` or
  `UNAVAILABLE`, and either its run is no longer running or the banner has used up its automatic tries. Use this,
  not `retries_exhausted`, to decide whether to show the button.
- `image_file` — a local filesystem path (only meaningful to the server itself); to actually display
  the image, either use `image_url` directly (a public CDN link) or, for a locally-saved crop that has
  no public URL, fetch it via `GET /api/runs/{run_id}/images/{filename}` using `image_file`'s basename.
- `hotspot_checks` — present (possibly empty) when the banner has more than one clickable region. Each
  entry has `hotspot_index`, `url`, `image_file`, and the same shape as `banner_check` (its own
  `result`/`reason`/brand-check fields) for that specific hotspot's own destination.
- `total_links` — `destination_raw` (if present) plus hotspot count; a UI showing an "MU" (multi-link)
  indicator should use `total_links > 1`.
- `hidden` / `hidden_reason` — whether AJIO's CMS has this banner switched off or it's outside its
  scheduled display window right now. `hidden_reason` is a comma-joined subset of `block_hidden`,
  `component_hidden`, `outside_schedule` (or the legacy token `cms_hidden` on data from before these
  were split out). A hidden banner is never actually verified - no image download, vision call, or
  listing fetch - it goes straight to `result: "SKIPPED"` with `reason: "hidden: <hidden_reason>"`.
- `skip_requested` — only present (and only meaningful) on a `PENDING` banner; `true` if someone has
  asked for it to be skipped (see the skip endpoint below).

**While a run is live**, banners not yet reached come back as lightweight placeholders instead of the
full shape above:
```json
{"banner_id": "...", "alt_text": "...", "destination_raw": "...", "image_url": "...",
 "result": "PENDING", "reason": "", "try_number": 0, "max_tries": 5, "skip_requested": false}
```
(`result` is `"PROCESSING"` with `try_number: 1` instead, once a worker has actually started on it.)

**Polling vs. streaming:** this endpoint is safe to poll repeatedly (e.g. every few seconds) while a
run is active — it always reflects current on-disk state. For push-based updates instead, see the SSE
stream below.

### `GET /api/runs/{run_id}/stream` — live updates (Server-Sent Events)

`Content-Type: text/event-stream`. Every ~1 second while the run is active, it re-reads the run's
state and pushes one `data:` event per banner whose row has changed since the last tick:
```
data: {"banner_id": "...", "result": "PASS", ...}

data: {"banner_id": "...", "result": "PROCESSING", "try_number": 2, ...}

event: done
data: {"status": "done"}
```
Each `data:` payload is exactly one banner row, same shape as an entry from the `/banners` list above.
The connection closes itself (after sending a final `event: done` with the run's final `status`) once
the run is no longer `"running"` — a client doesn't need to poll `GET /api/runs/{run_id}` separately to
know when to stop listening.

### `GET /api/runs/{run_id}/images/{filename}` — a locally-saved image

(Served with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`, so a picture can only ever be a picture. Menu runs never save SVG files.)

Serves a file from that run's own `images/` folder (a hotspot crop, or the banner's own cached copy).
`filename` must be a bare filename, no path separators (`400` otherwise); get it from a banner row's
`image_file` field, using only the last path segment. `404` if the run or the file doesn't exist.

### `GET /api/runs/{run_id}/export.xlsx` — download results as Excel

(For a menu run this is the menu workbook: see *Menu runs* below.)

Returns the `.xlsx` file directly (`Content-Disposition: attachment`, filename
`{l1}_{l2}_{timestamp}.xlsx`), one row per banner with its verdict/detail columns and its actual
banner image embedded in the row. Rebuilt fresh on every call, not cached.
- `409` if the run's `status == "running"` (its own results file can still be being rewritten by the
  run's own subprocess — wait until it's done/failed/cancelled).
- `404` if there's nothing to export at all (the run died before checking a single banner).

### `POST /api/runs/{run_id}/open-folder` — open the run's folder in Explorer

Windows-only, server-side (`os.startfile`) — only makes sense because this is a local, single-user
tool where the server and the caller are the same machine. Returns `{"opened": true}`.

### `POST /api/runs/{run_id}/cancel` — stop a run

Terminates the run's subprocess. Whatever's already been checked is kept as-is; nothing in progress is
discarded, it just stops advancing. Returns `{"cancelled": true}`, or `409` if the run isn't currently
running (either already finished, or — see `docs/UI_GUIDE.md`'s "things to consider" — the API lost
track of it after a server restart, in which case the subprocess is likely still alive but can't be
cancelled through this endpoint anymore).

### `DELETE /api/runs/{run_id}` — delete a run for good

Removes the run's database row **and its whole folder under `runs/`** (banner images, results, everything); alerts about it stay, without their `run_id`. This is the
Delete button on a run card in both UIs, and unlike `hide` below it cannot be undone. `404` for an unknown run, `409` while the run is `running` (stop it first) or if the folder could not be removed
(a file in it may be open; nothing is deleted then). Returns `{"deleted": true}`. Save the run as Excel first (`GET .../export.xlsx`) if the results are wanted.

### `POST /api/runs/{run_id}/hide` — dismiss from the run history list

UI-only bookkeeping — the run's folder on disk is never touched. Returns `{"hidden": true}`.

**Retention:** the server deletes runs (row and folder) that started more than 30 days ago, two minutes after each start and then daily (`web/retention.py`). Runs that are `running` are skipped. There is no endpoint for it; a deleted run answers 404, and alerts about it keep existing with `run_id: null`.

## Menu runs

The parts of the app that are not banner pages: the **top navigation**, the **bottom navigation**, the **sponsored ads** and **trending**. A run of one only fetches its data and
pictures (nothing is checked, so there is no verdict, no retry and no diff), and it is started like any run: `POST /api/runs` with a menu id in `pages`
(`menu-top-nav`, `menu-bottom-nav`, `menu-ads`, `menu-trending`). Its run folder is `runs/<stamp>_<kind>_menu/` with `menu.json`, `menu_meta.json` and `images/`
(`qa/app_menus.py`). What each one depends on (tested 2026-10-05): the top navigation on the shopper segment `l1` (premium gets a different menu, nonpremium and nontransacted the
same one, `l2` made no difference); the bottom navigation on nothing; trending on `l1` (premium and nontransacted get the same ten, nonpremium none); the ads on the segment, state and
login status, one request per ad slot to AJIO's ad partner (OnlineSales; a third party's server, asked only what the app asks). Pincode is an input of none of them.
Schedules cannot cover menu pages (`422`).

### `GET /api/runs/{run_id}/menu` — a menu run's items

```json
{"kind": "top-nav", "l1": "premium", "l2": "unisex", "state": "KARNATAKA", "fetched_at": "2026-10-05T12:00:00+00:00",
 "items": [{"id": 1, "parent": 0, "level": 1, "title": "Footwear", "link": "https://www.ajio.com/shop/footwear", "opens": "web page",
            "description": "", "alt": "", "images": ["https://assets.../footwear.png"], "image_files": ["3fa1c0b2d9e4.png"], "active": true, "audience": "all_user"}]}
```
A flat list, parents before children (`parent` null at the top row, `level` 0 there), so a client can drill down. `images` are the picture addresses, `image_files` the copies saved in the run's
`images/` folder (same order; `null` where a download failed), served by `GET /api/runs/{run_id}/images/{filename}`. `link` is a plain `http(s)` web address or empty (anything else in the source data, such as `javascript:` or `data:`, is dropped); `opens` says what the entry opens
(`web page`, `app screen: /sections/...`, `in-app search: ...`, or a count of ads for an ad slot). Ads: each slot is a top-row entry, its ads are its children (no title, `description` says
`rank N`). `404` for a run that isn't a menu run, or has no data yet (running, failed or stopped).

### `GET /api/menu/export.xlsx` and the menu run's `export.xlsx`

`GET /api/menu/export.xlsx` is one workbook with the newest finished run of each menu kind: sheets **Top menu**, **Bottom menu**, **Sponsored ads** and **Trending** (those that have a run) plus a
*Read me*; `404` if none has finished. `GET /api/runs/{run_id}/export.xlsx` on a menu run gives just that run's sheet. One row per item: the picture embedded as a thumbnail and clickable (it opens the
item's link), then Alt text (only where the app supplies one), Title (the text shown under the picture), Link, what it opens, and for the menus the path and audience. (`qa/export_menu_xlsx.py`, xlsxwriter.)

## Per-banner actions

### `POST /api/runs/{run_id}/banners/{banner_id}/retry`

Redoes just this one banner in place, without touching anything else in the run. Intended for a
banner where `can_retry == true` on its `/banners` row, though nothing stops you calling it on any banner_id.
It is a **fresh check**: the subprocess runs with `--no-cache` (no cross-run cached verdict is reused), redoes the
banner whatever its previous result was (FAIL and non-temporary INCONCLUSIVE included) and re-checks every hotspot
rather than reusing the earlier ones. Its tries count from 1 again (a banner that had used all 5 shows "1 of 5", never "6 of 5"), and like any
banner it is retried automatically, up to 5 tries, if it hits a temporary error. The new result is
appended to `partial.jsonl` and overlays the run's `results.json` when read (`results.json` itself is not
rewritten). Fire-and-forget: returns `{"started": true}` immediately, the actual recheck runs in
the background. While it runs, that banner's row on `GET /api/runs/{run_id}/banners` is `PROCESSING` with its
`try_number` (this is held by the server, so every client and tab sees it, not just the one that clicked): poll that
endpoint until the row is no longer `PROCESSING`. A result saved by a retry carries `tried_at` (epoch seconds), which is
how a newer result is told from the run's older one now that tries restart at 1. `409` if a retry for this exact banner is already
in flight (a double-click guard, not a hard limit — try again once the first one finishes).

### `POST /api/runs/{run_id}/banners/{banner_id}/skip`

Only meaningful while the banner is still `PENDING` (nothing enforces this server-side, but calling it
on a banner that's already `PROCESSING` or has a final result has no effect — the check only happens
right before a banner's real work starts). Marks it to be skipped instead of ever being checked.
Returns `{"requested": true}` immediately; the banner's row will show `skip_requested: true` and
eventually resolve to `result: "SKIPPED"` once its turn in the queue would have come up.

### `POST /api/runs/{run_id}/banners/{banner_id}/unskip`

Undoes the above — removes the skip request. Only effective if the banner hasn't actually been
processed (and marked `SKIPPED` for real) yet. Returns `{"requested": true}`.

## Feed preview (no verification)

### `GET /api/feed-preview?l1=...&l2=...&scope=hero&page=home`

Fetches the feed fresh and returns every banner in scope — no vision call, no listing fetch, just the
raw feed data, grouped implicitly by `section_index`. Use this to build a "pick which carousels to
run" UI before calling `POST /api/runs`. `page` (default `home`) is one of `page_options`; for any page other than
`home` l1/l2 are ignored. `422` on a bad l1/l2/scope/page; `502` if the upstream feed fetch
itself fails.

```json
[
  {
    "banner_id": "6a69ddd109fa97d1e45b8dd4:2",
    "alt_text": "Biba Flat 50",
    "destination_raw": "https://ajio.com/s/flat50percentoff-curated-405636",
    "image_url": "https://assets-jiocdn.ajio.com/...",
    "section_index": 24,
    "section_id": "6a69ddd109fa97d1e45b8dd4",
    "block_index": 2,
    "position": 131,
    "label": "Brand in Focus-SEP",
    "total_links": 1,
    "hidden": false,
    "hidden_reason": ""
  }
]
```
`label` is the carousel's human-readable name (what a UI should show as the group heading — "Carousel
24" is a fallback, `label` is almost always populated). Group entries client-side by `section_index`
(or `section_id`) to build the picker. `section_index` is the carousel's *position* in this fetch and
shifts when AJIO reorders the feed; `section_id` is the CMS section's own id and doesn't. Pass the ids
the user unchecked as `excluded_sections` (preferred) — or their positions as `excluded_carousels` —
when starting the real run; a schedule stores them as `excluded_sections`.

## Cache

### `POST /api/cache/clear`

Clears the cross-run banner-verdict cache. Global — not scoped to one run. Only affects runs *started
after* this call. Returns `{"cleared": <N>}`, the number of cached entries that were deleted (`0` if
the cache was already empty).

## Schedules

A schedule is a saved run configuration that fires automatically on an interval, one run per
combination of its l1/l2/state/pincode selections (see "Core concepts"), and raises an alert when a run
finds something new to fix.

**How it fires.** The interval is anchored at `start_at` when there is one: the first fire is that
moment if it's still ahead, otherwise the next `start_at + k × interval` after now. Without one (only
schedules saved before `start_at` existed) it counts one interval from whenever the server started
or the schedule was last edited. Each fire starts its combinations one after another, each waiting until
`max_concurrent_runs` (1) other runs — scheduled or manual — aren't active, and skips (recording
`last_status: "skipped"`) a combination that still can't start after an hour. Fires missed while the
server was down aren't made up. A fire still working through its combinations when the next tick comes
suppresses that tick.

**Alerts.** Each finished run is compared with the previous finished run of the *same schedule and the
same l1/l2/state/pincode* (`GET /api/runs/{id}/diff`). A banner FAIL now that wasn't FAIL before —
including one that's new to the feed — is a regression. `notify_mode`: `"off"` (record diffs, alert on
nothing), `"new_fails"` (default — alert on regressions) or `"any_change"` (also new INCONCLUSIVEs,
recoveries, banners appearing or leaving). The first run of a combination, and the first run after the
schedule's scope/count/exclusions changed, are baselines and never alert. A run that *fails* also
alerts unless the mode is `"off"`. An alert is a row (`GET /api/alerts`) and, if `notify_toast`, a
Windows notification (Windows only; there's no email or chat delivery). For a schedule with several
combinations the alert title names the combination.

### The schedule object

```json
{
  "id": 8, "name": "Daily check", "interval_minutes": 1440, "scope": "hero", "banner_limit": null, "workers": 3,
  "l1s": ["premium", "nonpremium"], "l2s": ["men"], "states": ["KARNATAKA"], "pincodes": ["560029", "400001"],
  "combo_count": 4,
  "start_at": "2026-09-30T04:30:00+00:00",
  "enabled": 1, "created_at": "2026-09-28T11:50:12.020836+00:00",
  "excluded_sections": [{"id": "6a7c9d92a67938188bdf229b", "label": "Bank offers"}],
  "notify_mode": "new_fails", "notify_toast": true,
  "last_run_at": "2026-09-30T04:30:02+00:00", "last_run_id": "20260930T043002Z_feedverify",
  "last_status": "done", "last_message": "[premium/men · KARNATAKA · 560029] 1 new FAIL",
  "next_run_at": "2026-10-01T04:30:00+05:30",
  "run_count": 12, "unread_alerts": 1,
  "latest_run": { "...the schedule's most recent run summary, or null..." }
}
```
- `pages` / `l1s` / `l2s` / `states` / `pincodes` — the selections; `combo_count` is how many runs a fire makes
  (l1 × l2 only for `home`; see "combination"). `pages` is `["home"]` for a schedule saved before pages existed.
  (The names `l1` / `l2` / `state` / `pincode` no longer appear on a schedule.)
- `start_at` — UTC ISO, or `null` (see "How it fires").
- `excluded_sections` — carousels left out of every run, by the CMS section's stable `_id` (so they stay
  left out when the feed's order shifts) with a display `label`.
- `last_status` — `running`, `done`, `failed`, `cancelled`, `skipped` (a fire couldn't get a free slot),
  `error` (a fire couldn't start), or `null`. `last_message` says more; for a multi-combination schedule
  it's prefixed with the combination it's about.
- `next_run_at` — when the timer fires next; `null` if disabled.
- `run_count` / `unread_alerts` / `latest_run` — for building a list page without further calls.
- `enabled` is `0`/`1` (SQLite integer boolean, not a JSON bool — check for truthiness, not `=== true`).

### `GET /api/schedules` — list all

An array of schedule objects, newest first.

### `GET /api/schedules/{schedule_id}` — one schedule

The schedule object plus `"alerts": [...]` (its 10 most recent, see `GET /api/alerts`). `404` if the id
is unknown. Its runs are `GET /api/runs?schedule_id={schedule_id}`.

### `POST /api/schedules` — create

Body: `{"name": str, "interval_minutes": int (≥1), "pages": [str], "l1s": [str], "l2s": [str], "states": [str],
"pincodes": [str], "start_at": str|null, "scope": str, "banner_limit": int|null, "workers": int,
"enabled": bool, "excluded_sections": [{"id": str, "label": str}], "notify_mode": str,
"notify_toast": bool}`. Only `name`, `interval_minutes`, `l1s` and `l2s` are required; the rest default
as in `POST /api/runs` (`pages` → `["home"]`, `states` → `["KARNATAKA"]`, `pincodes` → `["560029"]`), `start_at` → `null`,
`notify_mode` → `"new_fails"`, `notify_toast` → `true`, `enabled` → `true`. `start_at` is an ISO date-time; with no timezone it's
read as the server machine's local time (send an offset or `Z` to be exact) and it's stored as UTC.
Returns the created schedule object. An enabled schedule's timer starts immediately.
`422` for the same reasons as `POST /api/runs` (including more than `max_combos` combinations), an
unknown `notify_mode` (`/api/meta`'s `notify_modes`), or an unparseable `start_at`.

### `PATCH /api/schedules/{schedule_id}` — update

Body: any subset of the create fields — only the keys you include are changed. `banner_limit` and
`start_at` may be set to an explicit `null` (to mean "everything" / "no start time"); for every other
field `null` means "leave it". `excluded_sections` replaces the whole list (`[]` clears it). Editing
one of `l1s`/`l2s`/`states`/`pincodes` is checked against the others as they'll be, so the
combination cap holds. The timer is rebuilt from the new settings. Returns the updated schedule
object. `404` if the id is unknown, `422` as for create.

### `DELETE /api/schedules/{schedule_id}`

Returns `{"deleted": true}` regardless of whether the id existed (idempotent). Its past runs and
alerts are kept, and a fire that's part-way through its combinations starts no more.

### `POST /api/schedules/{schedule_id}/run-now`

Starts the schedule's runs immediately, without waiting for its interval — its first combination
right away, the others one after another. Deliberately not held back by the "one run at a time" gate
scheduled fires wait behind. Returns `{"run_id", "batch_id", "combos", "queued"}`, same as
`POST /api/runs`. `404` if the schedule id is unknown.

## Alerts

### `GET /api/alerts?limit=50&unread=false&schedule_id=`

```json
{"unread": 2, "items": [{"id": 5, "created_at": "...", "schedule_id": 8, "run_id": "20260930T043002Z_feedverify",
  "kind": "regression", "title": "Daily check [premium/men · KARNATAKA · 560029]: 1 new FAIL",
  "message": "FAIL - BUDA JEANS: missing brands ['Levis']", "payload": {"newly_failing": 1, "...": 0},
  "is_read": false, "delivery": "ok"}]}
```
`unread` is the total across all alerts, whatever the filters. `kind` is `regression`, `run_failed` or
`test`. `delivery` is how the Windows notification went — `"ok"`, `"skipped: ..."` or `"error: ..."` —
or `null` if no notification was asked for. Newest first.

### `POST /api/alerts/{alert_id}/read` and `POST /api/alerts/read-all`

Mark one / every alert read. Both return `{"unread": <count left>}`.

### `DELETE /api/alerts/{alert_id}` and `DELETE /api/alerts`

Remove one alert (idempotent), or clear every alert. `DELETE /api/alerts?schedule_id=N` clears only that
schedule's. They return `{"unread": <count left>}` (the clear also returns `"removed": <n>`). This only
removes the row from the list - a Windows notification that was already shown isn't affected.

### `POST /api/notify/test`

Records a `test` alert and tries a Windows notification (it will pop up on the machine running the
server). Returns `{"alert_id": ..., "delivery": "ok" | "skipped: ..." | "error: ..."}`.

## Meta

### `GET /api/meta`

```json
{
  "l1_options": ["nontransacted", "premium", "nonpremium"],
  "l2_options": ["men", "women", "unisex", "nogender"],
  "page_options": [
    {"id": "home", "label": "Home", "tier": "home"},
    {"id": "premium-men", "label": "Prem men", "tier": "premium"},
    {"id": "premium-women", "label": "Prem women", "tier": "premium"},
    {"id": "kids-premium-page", "label": "Prem kids", "tier": "premium"},
    {"id": "menswear", "label": "Non prem men", "tier": "standard"},
    {"id": "womenswear", "label": "Non prem women", "tier": "standard"},
    {"id": "kidswear", "label": "Non prem kids", "tier": "standard"}
  ],
  "menu_options": [
    {"id": "menu-top-nav", "label": "Top Nav", "tier": "menu", "kind": "top-nav", "by_l1": true, "by_state": false},
    {"id": "menu-bottom-nav", "label": "Bottom Nav", "tier": "menu", "kind": "bottom-nav", "by_l1": false, "by_state": false},
    {"id": "menu-ads", "label": "Ads", "tier": "menu", "kind": "ads", "by_l1": true, "by_state": true},
    {"id": "menu-trending", "label": "Trending", "tier": "menu", "kind": "trending", "by_l1": true, "by_state": false}
  ],
  "scopes": ["hero", "all"],
  "state_options": ["ANDHRA PRADESH", "ANDHRA_PRADESH", "...", "WEST BENGAL", "WEST_BENGAL"],
  "notify_modes": ["off", "new_fails", "any_change"],
  "max_workers": 10,
  "max_concurrent_runs": 1,
  "max_combos": 24,
  "default_state": "KARNATAKA",
  "default_pincode": "560029"
}
```
`page_options` are the banner pages; `menu_options` are the menu parts (a run may name either, a schedule only banner pages).
Call this first — it's the source of truth for every valid `l1`/`l2`/`scope`/`workers`/`state`/
`notify_mode` value and for the combination cap. `state_options` has 35 entries (28 Indian states; the
7 multi-word ones appear twice, once space-separated and once underscore-separated) — truncated above,
see the live response for the full list.

---

## Prompt for an AI agent building against this API

Paste the block below into an AI assistant/agent that needs to build a UI or automation against this
API, to bring it fully up to speed in one shot.

```
You are working with the AJIO Feed Verify local API. It's a QA tool: it reads AJIO's home-feed
banners directly from AJIO's backend (destination, brand claims, deal text) and checks each one
against its real listing page, without using an emulator. Base URL: http://127.0.0.1:8000. No auth.
JSON everywhere except two file-download endpoints and one SSE stream.

CONCEPTS
- A "run" is one check pass over a given (l1, l2) user-cohort segment and a scope ("hero" = main
  carousel only, "all" = every banner on the feed). Runs execute in the background; you start one and
  then poll or stream its progress. Identified by an opaque run_id string.
- A "banner" is one promotional tile. Every banner in a run's scope appears in its banner list from
  the moment the run starts (as PENDING), before it's actually been checked.
- A "combination" is one (l1, l2, state, pincode). A run is exactly one combination; a request that
  names several of any of them (lists) becomes one run per combination in the cross product, started
  one after another (never concurrently), capped at meta.max_combos (24), sharing a batch_id.
- A "schedule" is a saved run config that fires on its own on a recurring interval (anchored at an
  optional start_at), runs every combination each fire, diffs each run against the previous run of the
  same combination, and raises an alert when there's a new FAIL (or, if asked, any change).

START HERE
1. GET /api/meta - get valid page_options, l1_options, l2_options, scopes, state_options, notify_modes, max_workers,
   max_combos.
2. (Optional) GET /api/feed-preview?l1=..&l2=..&scope=.. to see real banners grouped by
   section_index/label/section_id before running anything - no vision/listing calls, just the raw feed.
   Useful for letting a user exclude whole carousels via POST /api/runs's excluded_sections (stable
   ids - preferred) or excluded_carousels (positional section_index ints).
3. POST /api/runs with {l1s: [..], l2s: [..], states (optional list, each one of state_options,
   default ["KARNATAKA"]; empty/blank also means the default, only a non-blank unrecognized value 422s -
   Indian state sent in the home feed's x-location-detail header), pincodes (optional list of numeric
   strings, default ["560029"] - delivery pincode for listing lookups, sent as AJIO_PINCODE, can change
   PASS/FAIL results), scope, banner_limit (null=all), workers (1-10), excluded_sections and/or
   excluded_carousels (optional)} -> {"run_id": "<the first combination's>", "batch_id", "combos",
   "queued"}. Returns immediately; the first run starts in the background and the other `queued`
   combinations start one after another as each finishes (find them in GET /api/runs by batch_id).
4. Either poll GET /api/runs/{run_id}/banners repeatedly (safe to call as often as you like, always
   reflects current state), or open GET /api/runs/{run_id}/stream (SSE) for push updates - one `data:`
   event per changed banner row, then `event: done` with final status when the run stops being
   "running". Check GET /api/runs/{run_id}["status"] ("running"/"done"/"failed"/"cancelled") if you
   need the run-level state rather than per-banner.

BANNER RESULT VALUES (the `result` field): PENDING (not started), PROCESSING (being checked now, or
between automatic retry rounds - try_number/max_tries tell you which attempt out of 5), PASS (claim
verified correct), FAIL (a real mismatch found - brand, deal text, or audience), INCONCLUSIVE (pipeline
couldn't confidently resolve it - ambiguous gender read, an AJIO-beauty banner where gender doesn't
apply, or a transient error that used up all 5 retries), SKIPPED (nothing checkable, e.g. no listing
link - or manually skipped). FAIL and INCONCLUSIVE both need a human to look, but mean different
things: FAIL is a specific finding, INCONCLUSIVE is "couldn't tell." A banner's `retries_exhausted:
true` is the signal to offer a manual retry (POST .../banners/{id}/retry).

KEY GOTCHAS
- excluded_carousels drops those banners from the run entirely (not even a PENDING placeholder) -
  different from per-banner skip, which only works on a still-PENDING banner in an already-started run
  (POST/DELETE-style pair: .../skip and .../unskip, reversible only until that banner is actually
  processed).
- GET .../export.xlsx (a real .xlsx with embedded banner images) 409s while the run is still
  "running" - only fetch it once status is done/failed/cancelled.
- image_url on a banner is a public CDN link, safe to hit directly from a browser. image_file is a
  server-local path - to actually see that image from a UI, use image_url instead, or for a
  locally-saved hotspot crop with no public URL, fetch GET /api/runs/{run_id}/images/{basename-of-
  image_file}.
- hidden/hidden_reason: AJIO's CMS can mark a banner hidden, or it can be outside its scheduled display
  window - both mean "not shown to a real user right now." These banners still appear in the run (still
  get a `banner_id`/row, still counted in `total_links` etc.) but are never actually verified - they go
  straight to `result: "SKIPPED"`, `reason: "hidden: <hidden_reason>"`, with no image/vision/listing
  work done. Decide deliberately whether your UI shows these by default (the bundled UI hides them by
  default with a toggle).
- Schedules' `enabled` field is 0/1 (SQLite integer), not a JSON boolean - check truthiness. A
  schedule's selections are the plural lists l1s/l2s/states/pincodes (no singular l1/l2/state/pincode
  on the schedule object); a *run's* l1/l2/state/pincode are single values.
- A schedule's runs are GET /api/runs?schedule_id=N (group by batch_id for "one fire"); a finished
  scheduled run's summary carries `diff` (headline, vs the previous run of the SAME combination -
  baseline:true never alerts) and GET /api/runs/{id}/diff has the detail. Alerts: GET /api/alerts.
- A schedule's start_at is an ISO date-time; send an offset or Z, or it's read as the server machine's
  local time. The interval is anchored there (first fire = start_at if ahead, else the next
  start_at + k*interval). Fires missed while the server was down are not made up.
- Cancelling a run of a multi-combination batch stops the rest of that batch; a failed one doesn't.
  The batch queue is in memory and is lost (running run kept) if the server restarts.
- pincode is fixed per run at start time and stored on the run row - POST .../banners/{id}/retry
  always reuses that run's own pincode, not whatever a caller might pass elsewhere; there's no way to
  retry a single banner under a different pincode without starting a whole new run.
- state has no effect on a retry at all (not even reused, unlike pincode) - retry never re-calls the
  feed endpoint, and state only affects that call's x-location-detail header. state's own pincode
  inside x-location-detail always mirrors the run's pincode field - they're not two independent values.
- There is no cancel-then-resume: a cancelled run's un-checked banners just stay PENDING forever in
  that run's history entry; starting a fresh run is the way to redo it.
- All endpoints return {"detail": "..."} on error, with the HTTP status carrying the meaning: 404
  unknown id, 409 conflicting/premature state, 422 bad input, 502 an upstream AJIO call failed.
```
