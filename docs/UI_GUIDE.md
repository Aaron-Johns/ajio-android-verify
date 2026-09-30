# UI Guide

A detailed walkthrough of the web UI — what every control does, what the results actually mean, and
things worth knowing before you trust what it tells you. For getting the tool installed and running
in the first place, see the main `README.md`.

> **Two interfaces, one server.** This guide describes the original interface at `http://127.0.0.1:8000/`. A second,
> presentation-style **manager view** lives at `http://127.0.0.1:8000/manager/` (see the last section). Both read the
> same API and the same data, so a run started in one shows up in the other, and the original is never replaced.

The page has two halves: a **left sidebar** (start/schedule runs, recent run history) and a **main
panel** on the right. When you open the page the main panel shows **every run as a card to choose from** (blue =
finished, purple = running, red = the run itself crashed, grey = cancelled; its PASS/FAIL tally is on the card) instead of opening the last run's banners; click a card, or a run under
*Recent runs*, to see its banners. **&larr; All runs** at the top of the panel brings the list back, and it also
returns after you remove the run you were viewing.

## Starting a run

**Page.** The first thing in *New run* is which app page to check. There are seven buttons, all multi-select
(at least one stays on; a ring and a tick mark the ones chosen): **Home**, **Prem men**, **Prem women** and
**Prem kids** on the first line, **Non prem men**, **Non prem women** and **Non prem kids** under them. Home is
black with white letters, the premium pages are gold with black letters, the non-premium pages are white with black
letters. Behind them are seven different feeds (`home`, `premium-men`, `premium-women`, `kids-premium-page`,
`menswear`, `womenswear`, `kidswear`). **Only Home depends on l1/l2**: the other six pages return the same banners
whatever the cohort is, so when Home isn't chosen the l1 and l2 buttons grey out and are ignored, and each other page
is one run per state and pincode (fixed neutral cohort). Picking Home plus other pages runs Home for every l1 × l2
you chose and each other page once. Everything below (carousel picker, scope, retries, Excel, schedules) works the
same for every page; a run's title, its Recent runs entry and a scheduler's card say which page it is
(the l1/l2 pair for Home, the page name otherwise).

Four of the other settings — **l1, l2, state and pincode** — are *multi-select*. Pick more than one of any
of them and **Run now** runs every combination: 2 l1 × 2 l2 × 1 state × 3 pincodes is 12 runs (for Home; each
other page adds 1 × states × pincodes). A line under the pincode box counts them for you ("(Home: 2 l1 × 2 l2 + 2
other pages) × 1 state × 3 pincodes = 18 runs, one after another"), and the button greys out if the total goes
over **24**.

Each combination is its own run, because a run fetches the feed as exactly one cohort/location. The
first starts immediately (and is opened for you); the rest **start one after another**, each as soon
as the previous one has finished — twelve runs at once would be twelve times the load on Gemini and on
AJIO. They appear in Recent runs as they start, and you'll see a short notice saying how many are
queued. **Cancel run** on one of them stops the rest of that batch too (a run that *fails* doesn't —
the next combination still goes ahead). The queue lives in the server's memory: restarting the server
mid-batch lets the run in progress carry on but forgets the combinations that hadn't started yet.

**l1 / l2 segment.** These pick which user cohort the **Home** feed is fetched as (AJIO personalizes the home
feed per cohort; the other pages don't care). They're toggle buttons: click to switch each one on or off (at least one always
stays on).

**State.** The Indian state AJIO believes you're in, sent in the home feed's `x-location-detail`
header. A dropdown with a checkmark beside each chosen state — open it, type to filter, click (or use
the arrow keys and Enter) to tick as many as you like; ticked ones also show as removable chips
underneath. Every state is listed, and a multi-word one (e.g. Tamil Nadu) appears twice, once
space-separated and once underscore-separated, since it isn't confirmed which form AJIO's edge actually
expects. Choose none and it falls back to `KARNATAKA`, the tool's original default — it does **not**
reject the run the way an invalid value does.

**Pincode.** The delivery pincode used when looking up each banner's linked listing (sent as
`AJIO_PINCODE`), default `560029`. **Type a pincode and press Enter** and it becomes a chip; type
another to check that one too (you can also paste several separated by commas or spaces, and
Backspace on an empty box removes the last chip). The box only accepts 6-digit pincodes, and a
half-typed one left in the box stops **Run now** with a message rather than being quietly dropped.
AJIO's search-edge API returns different results — different product counts, sometimes a different
brand facet entirely — for the same listing slug depending on delivery pincode, so this can genuinely
change a banner from PASS to FAIL or vice versa. It's not validated against a real pincode list. It
also feeds the State field's `x-location-detail` header (the two stay in sync — one pincode describes
the whole request, not two independent ones). A manual per-banner Retry reuses the same run's original
pincode, not whatever's currently selected — state doesn't apply to a retry at all, since retrying
never re-calls the feed endpoint that state affects.

**Scope.** "Hero carousel" checks just the main rotating banners at the top of the home screen — the
highest-visibility, highest-priority set. "All banners on the feed" checks every banner anywhere on
the page, which takes proportionally longer and calls the vision model many more times.

**Number of banners.** Leave blank to check everything the scope selects. Setting a number caps it to
the first N banners in feed order — useful for a fast smoke-test, but note it's a positional cut, not
a random sample, so it'll always be the same early banners on the page, never the ones further down.

**Banners at a time (workers).** How many banners get checked concurrently, 1–10. Gemma requests are
**paced to 6 a minute across all workers** (sized to a 16K tokens-per-minute limit, ~2,200 input tokens per
call), so a worker that needs Gemma while the pace is used up shows **Waiting turn** on its card - going
above ~10 workers buys nothing, since about that many are already enough to keep the pace busy at the
~90 s a Gemma call takes. The pace is **shared by every run on this PC** through a small lock file
(`qa/.cache/gemma_pacer.json`), so two runs at once, a scheduled run plus a manual one, or a Retry during a run
queue behind each other instead of each pacing itself. To change the pace set `GEMMA_CALLS_PER_MINUTE` in `.env`
(`0` = no limit); `GEMMA_PACER_SHARED=0` goes back to pacing each run on its own. Higher is faster
wall-clock time, but it's also more simultaneous load on Gemini and on AJIO's listing-search API —
if you start seeing more `listing_fetch_failed`/`vision_failed` transient errors than usual, dropping
this back down is worth trying before assuming something else is wrong.

## The carousel picker

Click **"Load carousels to choose which to run"** before hitting Run now to preview the feed without
checking anything yet. It fetches fresh (a plain feed call, no vision/listing work) and lays out every
carousel's real banners right in the main grid, each carousel under its own checkbox heading —
unchecking one hides its banners from view immediately and, if you then click Run now, drops that
entire carousel from the run (no image download, no vision call, no listing fetch for anything in it —
not even a placeholder in the results).

A bar above the carousels has **Select all** and **Deselect all**, so to check just a few you can untick everything
in one click and tick the ones you want. The same two buttons are on the carousel list in the scheduler form (and in
the manager view's New check drawer and schedule form). Run now, Start check and Save all refuse when every carousel is
unticked, since that would be a run with nothing in it.

**When this is worth using:** you know a particular carousel is noisy, irrelevant to what you're
checking today, or you just want to keep a run fast by narrowing scope without hand-picking individual
banners. **What it's not:** a way to preview verdicts — nothing in the preview has been checked yet,
it's purely "here's what exists in this carousel."

Changing l1/l2/scope after loading a preview invalidates it (a different segment or scope can have an
entirely different set of carousels) — reload it if you change those and still want to pick carousels.

With several l1/l2 selected the preview shows the **first** of each (the title says so). Carousels you
uncheck are left out by the carousel's own id, so the choice carries over to every combination that has
that same carousel; one that only exists in another combination's feed is simply not affected.

## Reading the results grid

Each card is colored and labeled by its current status:

| Status | Meaning | Needs a human? |
|---|---|---|
| **PENDING** | Not started — still queued behind other banners/workers | No |
| **PROCESSING X/N TRIES** | Being checked right now, or waiting out the pause between automatic retry attempts (X = current attempt, N = max, always 5) | No |
| **PASS** | The banner's claimed brand/deal/audience all matched the destination it links to | No |
| **FAIL** | The pipeline found a concrete mismatch (wrong brand, deal text doesn't match the listing title, wrong audience) | **Yes** |
| **INCONCLUSIVE** | The pipeline couldn't confidently resolve something — an ambiguous gender read, an AJIO Beauty banner (gender check doesn't apply), or a banner with no deal text / no brand so part of the comparison has nothing to compare (everything else it could check was fine). The reason line says which | **Yes** |
| **UNAVAILABLE** | AJIO, Google or the network didn't answer, and the banner used up all 5 automatic tries. Says nothing about the banner itself. Slate grey with a dashed border in the classic UI; **Couldn't check** in the manager view | No - **Retry this banner** |
| **SKIPPED** | Nothing to check (banner has no listing link at all — a webview/external/cart-type destination), it's currently hidden/out-of-schedule, or you skipped it manually | No |

**The activity tag.** While a banner is in progress, a small tag at the **bottom left of its card** says
what it's doing right now, in one or two words: **Downloading** (the banner image), **Cache check**
(seen this artwork + link before?), **Listing lookup** (AJIO's page for the link), **Reading image**
(Gemma reads the whole banner) / **Reading hotspot** (a crop of one link on a multi-link banner),
**Comparing** (brands, deal and gender against the listing), **Waiting turn** (its Gemma request is queued
behind the per-minute pace), then for a banner that hit a temporary error
**Cooling down** (its short wait before the next try) and **Retry queued** (waiting for a free worker).
It disappears once the banner has a verdict. A run that started before this existed, or a banner picked up
mid-way, just shows **Working**. A banner that's fully served from the cache can go through these in a
fraction of a second, so you may only catch the tag on cards that do real work.

**How retries are ordered.** There's one shared queue. A banner that fails on a temporary error (a Gemini
5xx or timeout, a listing hiccup) goes straight back in - after a short 10-second cooldown, not after the
whole first pass - and the next free worker picks it up *ahead of banners that haven't been tried yet*.
While it cools down, workers carry on with untried banners, so a failed banner never holds one up. Each
banner gets at most 5 tries (the "X/5"); a call that hangs is cut off by its own timeout (Gemini 3 min,
listing and image downloads 30 s) and counts as a failed try. The retry pass no longer exists as a
separate step, so a banner waiting for its retry shows PROCESSING for seconds, not minutes.

**UNAVAILABLE** is what used to show up as INCONCLUSIVE whenever a *temporary server or network error* outlasted
the automatic retries (a listing lookup that kept failing, an image that wouldn't download, Gemma erroring or
overloaded). It is kept out of the "needs a look" count so an outage can't make a run look like it found problems,
and it is not part of the alert diff (a run that only had outages never alerts). It has its own filter chip, a
`U` in run tallies, and the same **Retry this banner** button as any banner that ran out of tries. A banner in a
run that was stopped part-way through its retries is UNAVAILABLE too. AJIO's listing service also sometimes
answers a good request with an HTTP 400 (a `NullPointerException` on their side); that particular error is now
retried automatically with a growing pause, like a 5xx, so most of them clear on their own and never reach this
status.

**FAIL and INCONCLUSIVE are not the same thing**, even though both need a look: FAIL means the
pipeline made a determination and it didn't match — that's a real, specific finding you can act on.
INCONCLUSIVE means the pipeline couldn't confidently determine the answer at all — it's "needs a
human eye," not "confirmed wrong." Don't read an INCONCLUSIVE count as a bug count.

Click any card to open the full detail modal: every brand found on the banner vs. what's actually in
the listing, the deal text comparison, gender/audience match, listing title, and — for a banner with
more than one link (see the **MU** tag below) — each individual link's own separate result.

**Luxe links.** A banner whose link is on `luxe.ajio.com` is checked against AJIO's **Luxe** store, not the
standard one - the same page name is a different catalogue there (more brands, different products), and
its result carries `listing_store: luxe`. Runs made before this was added checked those banners against the
standard store, so their FAIL/INCONCLUSIVE results on Luxe links can be wrong (typically "missing brand");
re-run, or use **Retry this banner**.

**Tags** in the top-right corner of a card:
- A grey **H** means the banner is hidden — either AJIO's own CMS flag has it switched off, or the
  fetch happened outside the banner's scheduled display window. Hover it for which. **Hidden banners
  are excluded from the grid, and from every count, by default** — toggle "Show hidden / out-of-
  schedule banners" at the top of the grid to include them. Worth remembering if you're reporting a
  pass/fail count to someone else: the default view is not "every banner on the feed," it's "every
  banner a real user would actually see right now." **A hidden banner is never actually verified** —
  no image download, no vision call, no listing fetch — it's marked SKIPPED with a "hidden: ..." reason
  the moment its turn comes up, since there's nothing to check for a banner no real user can currently
  see. Toggling it into view shows that SKIPPED card, not a real PASS/FAIL/INCONCLUSIVE verdict.
- A colored **MU** means the banner has more than one clickable destination (a hotspot overlay on top
  of the main image) — open the card to see every link's own result, since the top-level status is an
  aggregate and can hide one link's problem behind another's pass.

## Per-banner actions

**Skip / Unskip** — only offered on a still-**PENDING** card, deliberately not on one already
PROCESSING (an in-flight network call can't be safely interrupted mid-request, so there'd be no honest
way to "cancel" it instantly). Clicking Skip greys the card out and marks it SKIPPED the moment its
turn would otherwise come up — genuinely never sent, no image download, no API calls. Click the same
button (now labeled Unskip) to undo, as long as it hasn't already been processed in the meantime.

**Retry this banner** — on every **FAIL**, **INCONCLUSIVE** and **UNAVAILABLE** card (and in the card's
detail popup). It checks that one banner again **from scratch**: the image and its links are looked at
again, nothing is taken from the saved-results cache (it runs with the cache off) and nothing from the previous try
is carried over. The rest of the run is untouched, the retry counts as one more try, and its result replaces the old
one on the card, in the run's tally and in the Excel export. It is one check, not five: a FAIL that comes back FAIL
stays FAIL. While a run is still going, Retry is only offered on a banner that has used up its automatic tries (a
retry writes next to the live run, whose own final write would overwrite it); once the run has finished every FAIL /
INCONCLUSIVE / UNAVAILABLE card has it. For UNAVAILABLE, consider *why* it failed 5 times first — a genuinely
transient AJIO-side hiccup is worth retrying and will often resolve; a consistently-reproducing error probably
won't be fixed by another try (check the reason text in the card detail).

**Multi-link (hotspot) banners.** Each hotspot has its own status in the detail popup, and a hotspot whose link
could not be checked because of a temporary server/network error shows **UNAVAILABLE** on its own block, not
INCONCLUSIVE. If another hotspot of the same banner genuinely FAILs, the banner stays **FAIL** (a finding is never
hidden) and its reason ends with "N hotspots couldn't be checked"; the banner is still retried automatically and
offers Retry. An *automatic* re-try of a temporary error redoes only the hotspots that hit the error and keeps the
ones already checked properly (so one glitched link on a six-link banner costs one vision call, not six); a manual
**Retry this banner** redoes everything.

**Cancel run** — stops the run immediately. Whatever's already been checked stays exactly as it is;
nothing gets discarded. There's no "resume" button in the UI currently — a cancelled run's remaining
un-checked banners just stay PENDING forever in that run's own history entry.

## Exporting to Excel

**Download Excel** lights up once a run is done, failed, or cancelled (greyed out the whole time it's
active — see "things to consider" below for why). It builds a fresh `.xlsx` every time you click it —
never a cached/stale copy — with one row per banner: its verdict, the reason text, brands found, deal
comparison, and its actual banner image embedded directly in the row (not a link — a real thumbnail
you can see scrolling the sheet). The click also opens the run's folder in Windows Explorer, since
everything here runs locally on your own machine anyway.

For a large `--scope all` run, the embedded images make this a genuinely big file (megabytes, not
kilobytes) — that's expected, not a bug.

## Scheduling recurring runs

A **scheduler** is a saved set of run settings that fires on its own every so often, tells you when
something *changed* since its last run, and keeps every run it makes in one place. The **Scheduler**
block in the left sidebar has three things:

- **A search box** beside the "Scheduler" heading filters the list as you type. Every word you type has to appear
  somewhere in the scheduler's name, its l1 / l2, state, pincode, interval (`2h`, `1d`), last result (`done`,
  `failed`...) or `enabled` / `disabled`, in any order and ignoring case - so `premium assam` finds the schedulers
  that cover both. Esc clears it; the filter survives the list's automatic refresh. It only filters the sidebar list
  (the expanded page has its own All / Enabled / Disabled / Needs attention chips).
- **Make scheduler** — opens the create form in the main window.
- **A scrolling list** of your schedulers, one compact line each. Click one to open its runs; the small **x**
  at its right end deletes it after a confirmation (its past runs and alerts are kept). A green dot means enabled. A count of unread alerts
  shows on the block's title and beside any scheduler that has some.
- **Expand** (or clicking the block's title) — opens the full list in the main window.

Everything opens *inside the main window* and drills down the same way the rest of the app does:

1. **All schedulers** ("Expand"). Every scheduler is a card in the banner cards' colour scheme — blue
   once its latest run has finished (the tally on the card says how it went), red if the last run
   itself failed to run, purple while a run is going, grey if disabled. Each card shows its interval,
   start, next run, its l1/l2/state/pincode selections, how many runs it makes per fire and so far,
   the latest run's PASS/FAIL tally and what changed since the run before, plus
   **View / Edit / Run now / Disable / Delete**. Above the cards: a
   one-line summary (schedulers, enabled, needing attention, total runs, roughly how many runs a day
   they add up to), the **alerts** (with *Mark all read* and *Send test notification*) and filter
   chips — All / Enabled / Disabled / Needs attention (an unread alert, or a last fire that failed or
   was skipped).
2. **One scheduler's runs** (click a card). Each run is a card like a banner card: when it started,
   its l1/l2, state and pincode, its status, PASS/FAIL/INCONCLUSIVE/SKIPPED tally, what changed since
   the previous run of the same combination, how long it took and how many carousels were left out.
   Runs made by the same fire are grouped under one heading with a combined tally. The page refreshes
   itself every few seconds while a run is still going. **Settings** and **Alerts** are folded
   underneath.
   - **Date range.** Above the runs, **From** and **to** date pickers show only the runs that *started*
     between those two calendar days, both days included (this PC's local time). Fill in one or both: a single
     "From" means "from then on", a single "to" means "up to then", the same date twice means that one day,
     and dates entered the wrong way round are swapped rather than showing nothing. The heading shows how many
     of the runs are in view ("3 of 5"), **Clear dates** brings them all back, and the range is kept while the
     page refreshes itself. It looks at the latest 120 runs of the scheduler (the page says so when it hits that).
   - **Download Excel** sits at the bottom of every *completed* run's card (status done - not a cancelled or
     failed one, which have no card button; open those and use the normal button). It downloads that run's
     spreadsheet without opening the run; the ordinary Download Excel on a run's own page also opens its folder,
     this one only downloads.
3. **A run's banners** (click a run card). The normal listing page, exactly as for any run — with a
   **← scheduler name** button above it to go back to that scheduler's runs. (Any run a scheduler made
   shows that button, wherever you opened it from, including Recent runs.)

### The scheduler form

- **Name** and **Runs every** (minutes, hours or days).
- **Start** — a calendar date (**defaults to today**; the browser's date picker opens on click), then
  the time as `hh : mm` with an **AM/PM** dropdown, and a **Now** button that sets today, right now.
  It's this computer's local time. The line underneath shows when the **first run** will happen: the
  start time itself if it's still ahead, otherwise — if the time has already passed — the next slot
  counting `every N` from the start (start 9:00 AM, every 2 h, saved at 11:30 AM → first run 1:00 PM).
  The default (today, now) makes the first run one interval from now, as before. Only a scheduler
  saved before this existed has no start time; it keeps counting one interval from whenever the server
  last started (or you last edited it) until you edit it and save a start time.
- **l1 / l2 / State / Pincode** — the same multi-select controls as the New run panel. A scheduler
  covers every combination and makes **one run per combination each time it fires**, one after
  another. The count line and the 24-run cap work the same way. **Copy from New run** fills all of
  this from the New run panel.
- **Scope, number of banners, banners at a time.**
- **Alert me on** — *Off*, *New FAILs only* or *Any change* — and **Also show a Windows notification**.
- **Enabled.**
- **Carousels to leave out** — **Load carousels…** fetches the feed (for the first l1/l2 chosen) and
  lets you untick carousels. These are saved by the carousel's own id, so they stay left out when the
  feed's order shifts; ones that have dropped out of the feed show as removable chips.

### How a scheduler behaves

- **One run at a time.** A scheduled fire waits until no other run — scheduled or manual — is active
  before it starts each combination, and gives up on that combination (recording "skipped" on the
  scheduler) if that takes more than an hour. Two schedulers firing together simply queue up.
  **Run now** on a scheduler is never held back: it starts its first combination immediately and the
  rest one after another.
- **Changes are compared like with like.** Every run of a scheduler is compared with the previous
  finished run *of the same l1/l2/state/pincode*, never with a different combination's. A banner counts
  as a **new FAIL** if it's FAIL now and wasn't FAIL before, including a banner that wasn't in the
  previous run at all. **Any change** additionally covers new INCONCLUSIVEs, recoveries (FAIL → PASS)
  and banners appearing or leaving. The **first run of a combination, and the first run after you edit
  what it covers** (scope, count, carousels left out…), only set a baseline and never alert. A run that
  *fails outright* alerts too, unless alerts are Off.
- **Alerts** appear in the list page, on the badge, and — if ticked — as a Windows notification. For a
  scheduler with several combinations the alert names which one changed
  (`Daily check [premium/men · ASSAM · 560029]: 1 new FAIL`). Each alert has an **×** to remove it; the
  Schedulers page has **Clear all** (asks first) and a scheduler's own Alerts fold-out has **Clear these**
  (a Windows notification already shown isn't affected). **Send test notification** proves the
  notification path works without waiting for a real change (this one does pop up on screen).
  Notifications are Windows-only; there's no email or chat delivery.
- **The server has to be running.** Schedulers fire from inside the web server; if it's stopped, nothing
  fires, and fires missed while it was down aren't made up afterwards (it carries on from the next slot
  of the start-time-anchored schedule). To have it start by itself whenever you log in to Windows, run
  `powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1` once (`-Status` to check, `-Remove` to
  undo; no admin rights needed). It registers a per-user task that starts the server hidden 30 s after
  logon, restarts it if it crashes, never starts a second copy if one is already listening on port 8000,
  and logs to `logs\server.log`. It runs only while you're logged in - that's also what lets the Windows
  notifications appear. **A time that passes while the PC is off is ignored**, not run late: on the next
  start every schedule waits for its next slot. (A run that was *in progress* when the PC shut down is
  not resumed - it's marked failed on the next start, with what it had checked kept in its folder.) A restart while a run is in progress is fine: the run keeps
  going, and the server re-adopts it on startup (so it can be cancelled again and its diff and alerts
  still happen); a run whose process died with the machine is marked failed instead of hanging on
  "running".
- A fire that takes longer than its interval (many combinations, slow runs) doesn't stack: the next tick
  is skipped while the previous fire is still working through its combinations.

## Clearing the cache

A repeat banner — same artwork, same destination link — normally skips straight to its earlier
verdict instead of redoing the (slower, costs a real vision-model call) check, whenever it's seen
again in a later run. **Clear cache** wipes that saved history so the next run checks absolutely
everything fresh. You'll rarely need this: a one-off transient failure is never cached in the first
place (only a clean, complete verdict is saved), so this button doesn't fix "one banner had a weird
error" — it's for when the *matching logic itself* changes (a brand alias gets added, a gender rule
gets adjusted) and you want old verdicts recomputed under the new rules rather than reused stale. A per-banner
**Retry** never reads this cache, so it doesn't need clearing first.

## Things to consider when using it

- **Hidden banners are invisible by default, including in your counts, and are never actually
  verified** — they're marked SKIPPED without a real check, so even toggled into view they don't add
  PASS/FAIL/INCONCLUSIVE results. If you're telling someone "N banners checked, M passed," that's
  implicitly "of the ones currently visible to a real user" — say so.
- **`l1:premium` currently doesn't reliably get you AJIO's real premium-tier hero banners** — a
  premium run's *hero carousel* results specifically should be treated as suspect right now (see
  `PROGRESS.md` for the details); this doesn't affect non-hero carousels the same way.
- **Restarting the server during a run is safe, but it costs the queue.** The run keeps going and the
  server re-adopts it on startup (cancel works again, and it's marked done when it finishes), but a
  multi-select **Run now** forgets the combinations that hadn't started yet — start them again if you
  need them. If the *machine* was shut down mid-run, the run's process is gone: it's marked failed on
  the next start, with whatever it had checked still in its folder on disk.
- **A higher "banners at a time" isn't free** — it's more simultaneous load on both Gemini and AJIO's
  own API. If a run is throwing more transient errors than feels normal, that's worth trying lower
  before assuming something else broke.
- **FAIL vs. INCONCLUSIVE tell you different things** — don't collapse them into one "broken banners"
  number when reporting results; a FAIL is an actual finding, an INCONCLUSIVE is "someone needs to
  look at this."
- **The carousel picker and per-banner Skip solve different problems** — reach for the carousel
  picker when you know upfront you don't care about a whole section; reach for Skip when you're
  already watching a run and spot one specific banner you don't need checked.

## Reference data (optional): what each banner is *supposed* to lead to

The normal check asks "does the banner's artwork match the page its link opens?". **Reference data** adds a second,
independent question: "is that the page a human says this banner should open?". It is a small CSV you fill in for the
banners you care about; banners without a row are checked exactly as before.

1. **Create the file.** Run `.venv\Scripts\python.exe -m qa.reference_template` (add `--run runs\<folder>` for a specific run,
   `--scope all` for every banner instead of the hero carousels). It writes `config/reference.csv` from your newest finished
   run: one row per visible banner, with `expected_deeplink_type`, `expected_brand`, `expected_category` and `notes` blank,
   followed by read-only context (carousel label, alt text, the banner's link and what type it is, what the artwork says, the
   listing's title, the tool's own verdict) to help you decide what to write. Run it again after a new feed pull and it only
   **adds** rows for banners it hasn't seen; rows you have filled in are never touched, and it never overwrites the file.
2. **Fill in what you know.** Blank means "no opinion". A row with all three expectation columns blank is ignored.
   - `expected_deeplink_type`: `PLP`, `CATEGORY`, `BRAND`, `CAMPAIGN` or `EXTERNAL`. `PLP` accepts any listing page (`/s/`, `/c/`, brand pages).
   - `expected_brand`: one brand, or several separated by `|` (`Nike|Adidas`). **Every** brand named must appear in the listing's
     Brands filter; spelling variants count as the same brand (LEVI'S = LEVIS).
   - `expected_category`: for example `Clearance Store`. The listing's own title has to contain it, or be contained in it (case and
     punctuation ignored).
3. **Nothing to switch on.** Every check (web UI, schedules, per-banner Retry) reads `config/reference.csv` when it exists and
   has at least one expectation. `python -m qa.feed_verify --reference some.csv` uses another file, `--no-reference` ignores it.

**How it is judged.** Against the listing the link really opens (fetched from AJIO's listing API), not against the link's text,
because most banner links are opaque campaign names. A **mismatch makes the banner FAIL**, even if its artwork and page agree
with each other; a banner that already FAILs keeps its own reasons and gets the reference finding next to them ("...; reference:
expected brand not on the listing: Puma"). The finding is in the card's reason, in a **Reference** row of the banner's detail
popup / sheet, in the Excel "Reason" column and in scheduler alert text. Things that can't be tested (the listing didn't load, the
link isn't a listing page, the listing has no brand list) are recorded as *couldn't be checked* and are neither a pass nor a
failure; a listing that didn't load is retried as usual. The check runs after the saved-results cache, so editing the CSV takes
effect on the very next check, and a hidden or skipped banner is never judged. Only the banner's own link is compared, not its
hotspots.

**Limits.** Saved rows are matched by banner id (the feed's section `_id`, or `<_id>:<slide number>`), so a carousel slide that
moves position loses its row; and no real expectations exist until you write them: `config/reference.sample.csv` is a format
example, not data.

## The manager view (`/manager/`)

A second face for the same tool, built for someone who wants to know *is the feed OK, what needs a look, when is the
next check* rather than tune workers. It needs no restart or install - it is one static page served next to the
original, calling the same `/api/...` endpoints. The **Classic view** link at the bottom of its sidebar (and the
black, glowing **Manager view** button in the original's header) switch between the two.

- **Overview.** A pass-rate ring and one plain sentence ("67 banners need a human look"), four tiles (passing,
  need a look, active schedules, next scheduled check), the latest check of each feed, the banners that need a
  look with their reason (click one to open it), your schedules and recent alerts. A live strip appears while a
  check is running. Numbers come from the **latest finished check of each feed** (l1 / l2 / state / pincode /
  scope), so older repeats of the same feed don't double-count.
- **Checks.** Every run as a row with a segmented result bar; filter by need-a-look / running / scheduled / manual.
  The x removes a run from the list (its files stay on disk), same as the original.
- **One check.** A segmented bar and clickable status chips (click to filter), a carousel filter, **Problems
  first** ordering, the show-hidden switch, **Excel report**, **Stop**, per-card **Retry** / **Skip**, and the
  live activity tag while a banner is in progress. Click a card for the **detail sheet**: verdict in one line, every
  field the original's popup shows, the hotspots with their crops; **left / right arrow keys** step through the
  list you came from and **Esc** closes it.
- **New check** (button top right) opens a side drawer with the same page picker (Home / premium / non-premium
  buttons, see "Starting a run") and multi-select l1 / l2 / state / pincode controls, the scope, a carousel checklist (untick a carousel to leave it out) and, under *Advanced*, the banner
  cap, parallelism and **Forget saved results** (the cache clear). **Repeat automatically...** carries these
  choices into the new-schedule form.
- **Schedule search.** A search box beside the **Schedules** heading filters the schedule cards as you type - every
  word has to appear in the schedule's name, customer type / department, state, pincode, interval (`2 hours` or
  `2h`), last result or active / paused, in any order. It works together with the All / Active / Paused / Needs
  attention tabs (their counts follow the search), Esc clears it, and it is remembered when you come back to the page.
- **A schedule's checks** get the same two extras as the original's runs page: **From / to date pickers** (checks
  that *started* between those days, both included, this PC's local time; one-sided, single-day and reversed ranges
  all work; **Clear dates** resets; kept across the page's live refresh) and a **Download Excel** button on every
  completed check's tile (status complete - not a cancelled or stopped one), which downloads without opening the
  check. As in the original, only the latest 120 checks are loaded.
- **Schedules** and **Alerts** have everything the original's scheduler has: create / edit (start date + time,
  every N minutes/hours/days, multi-select axes, carousels to leave out, alert mode + Windows notification),
  pause / resume, run now, delete, a schedule's checks grouped by the fire that made them, and mark read / remove /
  clear all / send a test notification.

**Wording differs on purpose.** PASS / FAIL / INCONCLUSIVE / PROCESSING / PENDING / SKIPPED read as *Passed /
Failed / Inconclusive / Checking / Queued / Skipped*. "Need a look" = Failed + Inconclusive, the same set the
original calls "human verification needed". A check that was **stopped early** (cancelled or crashed) is labelled
so instead of counting the banners it never reached as needing review. Banners the feed marks hidden are left out
of a check's counts unless you switch them on (the original counts them).

Links are `#/`-based (`/manager/#/runs/<run id>`, `#/schedules/3`), so the browser's back button and bookmarks work.
Light / dark follows the system and shares the original's toggle setting.

