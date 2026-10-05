# UI Guide

A detailed walkthrough of the web UI — what every control does, what the results actually mean, and
things worth knowing before you trust what it tells you. For getting the tool installed and running
in the first place, see the main `README.md`.

> **Two interfaces, one server.** This guide describes the original interface at `http://127.0.0.1:8000/`. A second,
> newer **manager view** (the "banner proof desk") lives at `http://127.0.0.1:8000/manager/` (see the last section). Both read the
> same API and the same data, so a run started in one shows up in the other, and the original is never replaced.

The page has two halves: a **left sidebar** (start/schedule runs, recent run history) and a **main
panel** on the right. When you open the page the main panel shows **every run as a card to choose from** (blue =
finished, purple = running, red = the run itself crashed, grey = cancelled; its PASS/FAIL tally is on the card) instead of opening the last run's banners; click a card, or a run under
*Recent runs*, to see its banners. **&larr; All runs** at the top of the panel brings the list back, and it also
returns after you remove the run you were viewing.

Every run card in that list (a finished, failed or cancelled run) has two buttons: **Save as Excel** (downloads the run's spreadsheet; shown when the run has any results) and
**Delete**, which asks first and then removes the run for good, its entry and its whole folder on disk. That is different from the x in *Recent runs*, which only hides a run from the list and keeps its files.
A run that is still going has neither button (stop it first). Runs are also deleted automatically after 30 days (see below).

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

**Banners at a time (workers).** How many banners get checked concurrently, 1–10 (default 10). Gemma requests are
**paced to 6 a minute across all workers** (sized to a 16K tokens-per-minute limit, 2,150 input tokens per
call: the prompt 1,061 + the picture 1,089, the same at any picture size), so a worker that needs Gemma while the pace is used up shows **Waiting turn** on its card - going
above ~10 workers buys nothing, since about that many are already enough to keep the pace busy at the
~90 s a Gemma call takes (with the old default of 3 a run could never get near the pace: about 2 banners a minute). The pace is **shared by every run on this PC** through a small lock file
(`qa/.cache/gemma_pacer.json`), so two runs at once, a scheduled run plus a manual one, or a Retry during a run
queue behind each other instead of each pacing itself. To change the pace set `GEMMA_CALLS_PER_MINUTE` in `.env`
(`0` = no limit); `GEMMA_PACER_SHARED=0` goes back to pacing each run on its own. Higher is faster
wall-clock time, but it's also more simultaneous load on Gemini and on AJIO's listing-search API —
if you start seeing more `listing_fetch_failed`/`vision_failed` transient errors than usual, dropping
this back down is worth trying before assuming something else is wrong.

## Menu: what the app shows besides its banners

Under the page buttons in *New run* (and in the manager's *Start a check*) there is a **Menu** row: **Top Nav**, **Bottom Nav**, **Ads** and **Trending**. They are ticked like pages and can be mixed with them.
A menu part is not checked against anything: its run only fetches the pictures and details (a few seconds). What each depends on: Top Nav on the l1 segment (premium gets a different menu; nonpremium and
nontransacted the same one), Bottom Nav on nothing, Trending on l1 (nonpremium has none), Ads on l1, the state and the login status. So l1 stays usable when a menu part is ticked, l2 greys out (it is for Home),
the pincode is ignored, and scope, banner count, banners at a time and the carousel picker are dimmed when only menu parts are ticked. The run count line says how many runs that makes.
Menu parts cannot be put on a schedule.

The **Menu** page (a **Menu** button in the classic header, **Menu** in the manager's rail) shows what was fetched. The first line holds the four parts, each with its item count and when it was fetched.
Click one and a second line opens with its entries; click an entry that has sub-entries (it shows a number and an arrow) and its sub-entries open in the next line, and so on (Top Nav: All / Men / Women / Kids,
then Men's categories, then theirs; Bottom Nav: Home / Right Now / Clearance / Categories / Account and the whole category tree under Categories; Ads: the three ad slots, then each slot's ad pictures; Trending:
the hashtags). Click an open entry again to close the lines below it. An entry that has a picture shows it with its title under it; one without shows just the text. Under each entry is its link (opens in a new tab),
or what it opens inside the app when it has no web address; faded entries are marked inactive. If you have fetched a part more than once (for example Top Nav for premium and for nontransacted), a *Showing* list picks which fetch.
**Download Excel** gives one workbook with the newest fetch of each part (sheets Top menu, Bottom menu, Sponsored ads, Trending): one row per item with the picture, its alt text (only where the app gives one), its title
(the text under the picture) and its link in separate columns; clicking the picture opens the link. **This part as Excel** gives just the one you are looking at. A menu run's card (All runs, Recent runs) shows
its item count, opens the Menu page and has **Save as Excel** and **Delete** like any other run.

## The carousel picker

Click **"Load carousels to choose which to run"** before hitting Run now to preview the feed without
checking anything yet. It fetches fresh (a plain feed call, no vision/listing work) and lays out every
carousel's real banners right in the main grid, each carousel under its own checkbox heading —
unchecking one hides its banners from view immediately and, if you then click Run now, drops that
entire carousel from the run (no image download, no vision call, no listing fetch for anything in it —
not even a placeholder in the results).

A bar above the carousels has **Select all** and **Deselect all**, so to check just a few you can untick everything
in one click and tick the ones you want. The same two buttons are on the carousel list in the scheduler form (and in
the manager view's Start a check dialog and schedule form). Run now, Start check and Save all refuse when every carousel is
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

**The deal check.** When a banner's deal is a price or discount claim, the tool also checks it against the listing itself.
*Prices:* the listing is sorted by price: "UNDER ₹799" (or "UP TO ₹799", also a price cap) needs the dearest item to be at most ₹800; "STARTING AT ₹399" needs the cheapest item to be exactly ₹399.
*Discounts:* the listing's own "Discount Ranges" filter ("30% and above", "40% and above" ... each with a product count) is used. "MIN. 45% OFF" (or "20-70%":
only the low end counts) has a floor of 35%, which is rounded down to the 30% step; every lower step must hold the same number of products as the 30% step. If a
lower step holds more, those extra products are discounted less than the floor, which is a FAIL and the reason says how many. The step just above the
floor step must also hold *fewer* products than it (some product has to sit between the floor and the next step); if it holds the same number, that is a FAIL too. (Products discounted under
10% are in no step, so they aren't seen.) A bare "UP TO x%" discount, "UP TO ₹x OFF" (an amount off), "FLAT x%" and other wording are not checked this way, and a listing that can't be sorted or has no
discount filter is simply not checked (never a failure): the banner keeps the PASS or FAIL its other checks gave it.

**Spelling.** Gemma also reads the banner's text for spelling mistakes (brand names, deliberate styling and non-English words are not flagged). A misspelt word makes the banner a
**FAIL** whatever else it matches, and the reason names it, for example `spelling: "SUMER" should be "summer"`; the details list it as **Misspelt words**. Only the banner's own
picture is read for this, not the crops of its links (a crop can cut a word in half), so a banner that is only links is not spell-checked. Banners checked before this existed, and
ones reused from the 24-hour banner cache, carry no spelling reading. Use Retry on a false alarm (Gemma can misread a stylised font).

Click a banner to see the result of this check under its details, as **Deal works on the listing?** (classic) or **Deal on the listing** and **Deal works on the listing**
(manager): just **True**, **False** or "Couldn't check" (the reason for a False is in the banner's Reason row). Banners whose deal has no such rule (a bare "UP TO 60%") show no row.

The carousel filter above the banners lists each carousel by number and, beside it, the carousel's own title from the feed (for example
"Carousel 3 · MAIN SECTION 25TH"; these are the CMS names the feed team gave the sections).

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
is carried over. The rest of the run is untouched, its tries start again at 1 of 5 (never "6 of 5"), and its result
replaces the old one on the card, in the run's tally and in the Excel export. A temporary error is tried again
automatically, up to 5 tries, like in a normal run; a FAIL that comes back FAIL stays FAIL. The card shows "PROCESSING 1/5
TRIES" (classic) or "Checking, try 1 of 5" (manager) in **both views, and in any other tab**, because the server knows the
retry is going: open the run in the other view mid-retry and it is shown there too, and settles on its own. While a run is still going, Retry is only offered on a banner that has used up its automatic tries (a
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
  logon, never starts a second copy if one is already listening on port 8000,
  and logs to `logs\server.log`. It runs only while you're logged in - that's also what lets the Windows
  notifications appear. If the server itself crashes or is killed, nothing restarts it until the next logon (or
`Start-ScheduledTask "AJIO Feed Verify server"`). **A time that passes while the PC is off is ignored**, not run late: on the next
  start every schedule waits for its next slot. (A run that was *in progress* when the PC shut down is
  not resumed - it's marked failed on the next start, with what it had checked kept in its folder.) A restart while a run is in progress is fine: the run keeps
  going, and the server re-adopts it on startup (so it can be cancelled again and its diff and alerts
  still happen); a run whose process died with the machine is marked failed instead of hanging on
  "running".
- A fire that takes longer than its interval (many combinations, slow runs) doesn't stack: the next tick
  is skipped while the previous fire is still working through its combinations.

## Old runs are deleted automatically

A run that started more than **30 days** ago is deleted for good: its entry in Recent runs and its folder under
`runs\` (banner images and results). It happens by itself two minutes after the server starts
and then once a day, so nothing needs doing and there is no button. A run that is still running is never touched,
hidden runs are deleted like any other, and alerts about a deleted run stay in the alert list but lose their
"Open run" button. Export a run to Excel first if you want to keep it. The 30 days are `RETENTION_DAYS` in
`web/retention.py`.

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
  When AJIO keeps returning the regular banner set for a Premium check, the run says so itself: a highlighted line, **"Premium cannot be loaded: ..."**, above the banners in both views (the classic
  UI under the run header, the manager view in the note above the sheet). The run still goes on with the set it got; the line is there so those results aren't read as Premium ones.
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

The newer face of the same tool, at `http://127.0.0.1:8000/manager/` (three static files: `web/static/manager/index.html`, `style.css`, `app.js`; no restart or
install; it calls the same `/api/...` endpoints, so a run started in one view shows in the other). The **Manager view** button in the original's header and **Classic view** in
the manager's left rail switch between the two. Light / dark follows the system and shares the original's toggle setting. Links are `#/`-based, so the back button and bookmarks work:
`#/runs`, `#/runs/<run id>`, `#/menu`, `#/menu/<run id>` (a menu run's address `#/runs/<id>` redirects there), `#/schedules`, `#/schedules/new`, `#/schedules/<id>`, `#/schedules/<id>/edit`.

- **Layout.** A left rail (Runs / Menu / Schedules with an unread-alert badge, *Start a check*, the recent runs each with a small result bar and an x that removes it from the list, then three square
  tiles in one row: Switch theme, Clear cache and Classic view) and the main area. Type is Bricolage Grotesque throughout.
- **All runs.** Every run as a card: page, when, cohort (l1/l2 for Home), state and pincode (states in capitals, as AJIO spells them), result bar and tally, what changed since the schedule's
  previous run, how long it took, carousels left out, and two buttons on a run that isn't running: **Save as Excel** and **Delete** (asks first, then removes the run and its folder for good; the x in the rail only hides it).
- **One run.** A **verdict strip** across the top: one segment per result with its count; click a segment to show only those banners (click again to clear). At the right end of the strip is the **slide count**: "13 slides", or "5 of 13 slides" while a verdict, carousel or search filter is on. Under it: a carousel filter (number and the
  carousel's own title), a search box (brand, deal, link, reason), a *Show hidden and out-of-schedule banners* switch (the counts follow it; the classic view always counts them) and the banners as a contact sheet
  grouped by carousel. Each card shows the artwork with a coloured edge, the verdict word (*Checking, try 2 of 5* while retrying), the alt text and slide, the destination link, the reason (FAIL / INCONCLUSIVE / UNAVAILABLE),
  the brand and deal tags, **MU** (several links) and **H** (hidden, hover for why) tags, the live activity tag while a banner is in progress, and **Retry this banner** / **Skip** / **Unskip** where they apply.
  Above: **Stop run** (while running), **Download Excel** (also opens the run's folder; disabled while running), **Remove from list**, and for a scheduled run the diff line with **Show what changed**.
- **Banner detail** (click a card; left / right arrow keys step through the list, Esc closes): the verdict, the reason, a **Says and shows** table (brands, deal, audience, *Deal on the listing*, reference) with a tick or
  cross per row, then every field the classic popup has (hidden reason, carousel and slide, position in the feed, destination, listing title and size, brands, deal, title matches deal, *Deal works on the listing*,
  audience, missing and extra brands, tries) and one block per link inside a multi-link banner with its crop.
- **Start a check** (rail button): the page buttons (Home black, premium gold, non-premium plain), l1 / l2 (greyed out without Home), a searchable multi-select **State** list with chips, **Pincode** chips
  (6 digits, Enter to add), the run-count line and the 24-run cap, scope, number of banners, banners at a time, **Load carousels** (a checklist with pictures; untick to leave one out, Select all / Deselect all),
  and **Repeat automatically** (opens a new schedule with these choices). **Clear cache** is one of the three square tiles at the bottom of the left rail, with **Switch theme** and **Classic view**: it forgets every saved verdict so the next checks do every banner fresh.
- **Schedules.** The list has a summary, the alerts (*Mark all read*, *Clear all*, *Send test notification*, each alert's x and *Open run*), All / Enabled / Disabled / Needs attention chips, a search box
  (every word must appear in the name, page, state, pincode, interval or last result) and a card per schedule with **View / Edit / Run now / Enable or Disable / Delete**. A schedule's page shows its runs grouped
  by the fire that made them, **From / to** date pickers (both days included, this PC's local time), Download Excel on each finished run, and folded *Settings* and *Alerts*. The form has name, runs every,
  start date and time with a *Now* button and the "First run" line, the same axes as *Start a check*, scope, banner count, workers, *Alert me on* (Off / New fails only / Any change), the Windows notification, Enabled,
  and *Carousels to leave out*.

**Wording differs on purpose.** Results read as *Pass / Fail / Inconclusive / Unavailable / Skipped / Waiting / Checking*. A run that was stopped early is labelled so instead of counting the banners it never
reached as needing a look. Not in this view: the classic view's sidebar list of schedules, and the old overview page.
