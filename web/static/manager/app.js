"use strict";
/* Banner proof desk: a front end for the AJIO Feed Verify API.
   Opened from disk it talks to http://127.0.0.1:8000; served by the server it uses the same origin; ?api= overrides. */
const API = new URLSearchParams(location.search).get("api") || (location.protocol === "file:" ? "http://127.0.0.1:8000" : "");
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const JSON_H = { "Content-Type": "application/json" };

const VERDICTS = ["FAIL", "INCONCLUSIVE", "PASS", "UNAVAILABLE", "SKIPPED"];
const ORDER = [...VERDICTS, "PROCESSING", "PENDING"];
const WORD = { PASS: "Pass", FAIL: "Fail", INCONCLUSIVE: "Inconclusive", UNAVAILABLE: "Unavailable", SKIPPED: "Skipped", PENDING: "Waiting", PROCESSING: "Checking" };
const LONG = { FAIL: "Fail: a person needs to look", INCONCLUSIVE: "Inconclusive: a person needs to look", UNAVAILABLE: "Unavailable: not a banner problem" };
const COLOR = { PASS: "var(--pass)", FAIL: "var(--fail)", INCONCLUSIVE: "var(--maybe)", UNAVAILABLE: "var(--off)", SKIPPED: "var(--skip)", PENDING: "var(--skip)", PROCESSING: "var(--live)" };
const NOTIFY_LABEL = { off: "Off", new_fails: "New fails only", any_change: "Any change" };
const HIDDEN_LABEL = { block_hidden: "Hidden block", component_hidden: "Hidden component", outside_schedule: "Outside its schedule", cms_hidden: "Hidden block" };
const RUNS_LOADED = 120;

const S = {
  meta: null, runs: [], scheds: [], alerts: { unread: 0, items: [] },
  run: null, banners: [], filter: new Set(), showHidden: false, carousel: "", q: "",
  stream: null, openId: null, order: [], token: 0, timer: null,
  schedFilter: "all", schedQuery: "", range: { id: null, from: "", to: "" }, dlgAxes: null, dlgExcluded: new Set(), dlgCars: null, prefill: null,
  retrying: new Map(),
};

/* ---------- plumbing ---------- */
async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* keep statusText */ }
    throw new Error(detail || `HTTP ${r.status}`);
  }
  return r.json();
}
const post = (path, body, method = "POST") => api(path, { method, headers: JSON_H, body: body === undefined ? undefined : JSON.stringify(body) });
function toast(msg, isErr) {
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : ""); el.textContent = msg;
  $("toasts").appendChild(el); setTimeout(() => el.remove(), isErr ? 9000 : 6500);
}
const fail = (what) => (e) => toast(`${what}: ${e.message}`, true);
const ask = (msg) => window.confirm(msg);

/* ---------- formatting ---------- */
const pretty = (s) => String(s || "").replace(/[_-]/g, " ").replace(/^./, (c) => c.toUpperCase());
const stateName = (s) => String(s || "").toUpperCase();    // states read exactly as AJIO spells them, in capitals
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
const fmtWhen = (iso) => iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true }) : "";
const dayKey = (iso) => new Date(iso).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
const fmtInterval = (m) => m % 1440 === 0 ? `${m / 1440}d` : m % 60 === 0 ? `${m / 60}h` : `${m}m`;
const intervalParts = (m) => m % 1440 === 0 ? { n: m / 1440, unit: 1440 } : m % 60 === 0 ? { n: m / 60, unit: 60 } : { n: m, unit: 1 };
const fmtDuration = (s) => s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}m` : s >= 60 ? `${Math.round(s / 60)} min` : `${s}s`;
const pageLabel = (id) => S.meta?.page_options.find((p) => p.id === id)?.label || id;
const pagesOf = (s) => s.pages && s.pages.length ? s.pages : ["home"];
const cohortOf = (r) => (r.page || "home") === "home" ? `${pretty(r.l1)}, ${r.l2}` : `${stateName(r.state)} ${r.pincode}`;
const verdictOf = (b) => b.result || "PENDING";
const hiddenText = (reason) => (reason || "").split(", ").filter(Boolean).map((x) => HIDDEN_LABEL[x] || x).join(", ");
const imgSrc = (runId, b) => {
  const name = String(b.image_file || "").split(/[\\/]/).pop();
  return name ? `${API}/api/runs/${encodeURIComponent(runId)}/images/${encodeURIComponent(name)}` : b.image_url || "";
};
const cropSrc = (runId, h) => {
  const name = String(h.image_file || "").split(/[\\/]/).pop();
  return name ? `${API}/api/runs/${encodeURIComponent(runId)}/images/${encodeURIComponent(name)}` : "";
};

/* ---------- reasons, ported from the classic UI ---------- */
function referenceMismatch(b) { const r = b.reference_check; return r && r.status === "MISMATCH" ? r.reason : ""; }
function referenceText(b) {
  const r = b.reference_check; if (!r) return "";
  if (r.status === "MATCH") return "Matches what the reference says it should lead to";
  if (r.status === "MISMATCH") return r.reason || "Doesn't match the reference";
  return `Couldn't be checked against the reference: ${(r.unchecked || []).join("; ")}`;
}
function explainReasonBase(b) {
  if (b.reason === "user_skipped") return "Skipped by user";
  if (b.reason) return b.reason;
  const c = b.banner_check || {}, parts = [];
  if ((c.missing_brands || []).length) parts.push(`missing brands ${c.missing_brands.join(", ")}`);
  if (c.title_matches_deal === false) parts.push(`title "${b.listing_title || ""}" doesn't match the deal "${c.banner_deal || ""}"`);
  if (c.gender_matches === false) parts.push(`banner targets "${c.banner_gender || ""}" but the listing's genders are ${(c.listing_genders || []).join(", ")}`);
  if (c.gender_matches === "AJIO_BEAUTY") parts.push("AJIO beauty banner: the gender check is skipped, so it needs a person");
  if (c.gender_matches === "INCONCLUSIVE") parts.push(`the banner's audience reading "${c.banner_gender || ""}" isn't one the tool recognises`);
  if ((c.extra_brands || []).length) parts.push(`the listing has brands the banner doesn't name: ${c.extra_brands.join(", ")}`);
  return parts.join("; ");
}
function explainReason(b) {
  const base = explainReasonBase(b), ref = referenceMismatch(b);
  return ref && !base.includes(ref) ? (base ? `${base}; ${ref}` : ref) : base;
}
const shortExtraBrands = (text) => text.replace(/extra brands in the listing: \[([^\]]*)\]/g, (m, inner) => {
  const items = inner.split(",").map((s) => s.trim()).filter(Boolean);
  return items.length > 3 ? `extra brands in the listing: [${items.slice(0, 3).join(", ")}, ...]` : m;
});
function hotspotReason(h) {
  if (h.reason) return h.reason;
  const c = h.banner_check || {}, parts = [];
  if ((c.missing_brands || []).length) parts.push(`missing brands ${c.missing_brands.join(", ")}`);
  if (c.title_matches_deal === false) parts.push("the title doesn't match the deal");
  if (c.gender_matches === false) parts.push("audience mismatch");
  if ((c.extra_brands || []).length) parts.push(`extra brands ${c.extra_brands.join(", ")}`);
  return parts.join("; ");
}
const verdictLabel = (b) => b.result === "PROCESSING" && b.max_tries ? `Checking, try ${b.try_number} of ${b.max_tries}` : WORD[verdictOf(b)] || b.result;

/* ---------- small html builders ---------- */
function barHtml(counts) {
  const total = Object.values(counts || {}).reduce((a, n) => a + n, 0);
  if (!total) return '<div class="mini"></div>';
  return '<div class="mini" aria-hidden="true">' + ORDER.filter((v) => counts[v]).map((v) => `<i style="width:${(counts[v] / total) * 100}%;background:${COLOR[v]}"></i>`).join("") + "</div>";
}
const tallyHtml = (counts) => {
  const c = counts || {};
  const chips = ORDER.filter((k) => c[k]).map((k) => `<span class="${k}" style="background:${COLOR[k]}">${c[k]} ${esc(WORD[k].toLowerCase())}</span>`).join("");
  return chips ? `<div class="tally">${chips}</div>` : "";
};
const tagsHtml = (vals, max = 5) => vals.slice(0, max).map((v) => `<span class="tag">${esc(v)}</span>`).join("") + (vals.length > max ? `<span class="tag">+${vals.length - max}</span>` : "");
const kvHtml = (rows) => `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
const diffText = (d) => !d ? "" : d.baseline ? `<span class="hint">${esc(d.summary)}</span>` : `<span ${d.counts?.newly_failing ? 'style="color:var(--fail);font-weight:600"' : ""}>${esc(d.summary)}</span>`;
function runStatusText(r) {
  const c = r.counts || {}, done = Object.values(c).reduce((a, n) => a + n, 0);
  if (r.status === "running") return `Running, ${done}${r.total ? " of " + r.total : ""} done`;
  if (r.status === "cancelled") return "Cancelled";
  if (r.status === "failed") return "Failed to run";
  const bad = (c.FAIL || 0) + (c.INCONCLUSIVE || 0);
  return bad ? `${bad} to look at` : done ? "All clear" : "Nothing checked";
}
const runHealth = (r) => r.status === "running" ? "h-running" : r.status === "failed" ? "h-fail" : r.status === "cancelled" ? "h-off" : "h-done";
function download(runId) {
  const a = document.createElement("a");
  a.href = `${API}/api/runs/${encodeURIComponent(runId)}/export.xlsx`; a.download = "";
  document.body.appendChild(a); a.click(); a.remove();
}

/* =====================================================================
   Rail: navigation and recent runs
   ===================================================================== */
async function loadRuns() {
  S.runs = await api("/api/runs?limit=100");
  paintRail();
  if (S.viewName === "runs") paintRunsView();
}
function paintRail() {
  const box = $("runs");
  if (!S.runs.length) { box.innerHTML = '<p class="rail-empty">No runs yet. Start a check to see banners here.</p>'; return; }
  let last = "", html = "";
  for (const r of S.runs.slice(0, 60)) {
    const d = dayKey(r.started_at);
    if (d !== last) { html += `<div class="day">${esc(d)}</div>`; last = d; }
    html += `<div class="run" role="button" tabindex="0" data-run="${esc(r.run_id)}" aria-current="${S.run?.run_id === r.run_id}">
      <b>${esc(pageLabel(r.page))}</b>${barHtml(r.counts)}
      <small>${esc(new Date(r.started_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }))}, ${esc(cohortOf(r))}</small>
      <small>${esc(runStatusText(r))}</small>
      <button class="rm" type="button" data-hide="${esc(r.run_id)}" title="Remove from this list (the files stay on disk)" aria-label="Remove this run from the list">&times;</button></div>`;
  }
  box.innerHTML = html;
}
function setNav(which) {
  for (const [id, key] of [["nav-runs", "runs"], ["nav-schedules", "schedules"]]) {
    if (key === which) $(id).setAttribute("aria-current", "page"); else $(id).removeAttribute("aria-current");
  }
}
function paintBadge() {
  const b = $("nav-badge"), n = S.alerts.unread || 0;
  b.hidden = !n; b.textContent = n; b.title = `${plural(n, "unread alert")}`;
}
async function refreshSched() {
  const [list, al] = await Promise.all([api("/api/schedules"), api("/api/alerts?limit=30")]);
  S.scheds = list; S.alerts = al; paintBadge();
  if (S.viewName === "sched-list") paintSchedList();
}

/* =====================================================================
   Router
   ===================================================================== */
function closeStream() { if (S.stream) { S.stream.close(); S.stream = null; } }
function route() {
  closeStream(); clearTimeout(S.timer); closeDrawer(true); S.token++;
  $("rail").dataset.open = "false";
  const [a, b, c] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  setNav(a === "schedules" ? "schedules" : "runs");
  paintRail();
  if (a === "schedules") {
    if (b === "new") return viewSchedForm(null);
    if (b && c === "edit") return viewSchedForm(Number(b));
    if (b) return viewSchedDetail(Number(b));
    return viewSchedList();
  }
  if (a === "runs" && b) return viewRun(decodeURIComponent(b));
  return viewRuns();
}
const go = (h) => { if (location.hash === h) route(); else location.hash = h; };
const view = (html) => { $("view").innerHTML = html; $("main").scrollTop = 0; };

/* =====================================================================
   All runs
   ===================================================================== */
// Deletes a run for good (its row and its whole folder), unlike the x in the rail, which only hides it from the list.
async function deleteRun(id) {
  if (!ask("Delete this run for good? Its banners, images and results are removed and cannot be brought back. Save it as Excel first if you want to keep it.")) return;
  try { await api(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("Run deleted."); await loadRuns(); }
  catch (e) { toast("Couldn't delete the run: " + e.message, true); }
}
function runCardHtml(r, { excel, manage } = {}) {
  const total = Object.values(r.counts || {}).reduce((a, n) => a + n, 0);
  const took = r.finished_at ? fmtDuration(Math.max(0, Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000))) : "";
  const left = (r.excluded_sections || []).length;
  const home = (r.page || "home") === "home";
  return `<div class="card click ${runHealth(r)}" data-run="${esc(r.run_id)}" tabindex="0" role="button" title="Open this run's banners">
    <h4>${esc(pageLabel(r.page))}<span class="status ${esc(r.status)}">${esc(pretty(r.status))}</span></h4>
    <p class="quiet">${esc(fmtTime(r.started_at))}</p>
    <p>${home ? `${esc(pretty(r.l1))}, ${esc(r.l2)}<br>` : ""}${esc(stateName(r.state))}, pincode ${esc(r.pincode)}</p>
    ${barHtml(r.counts)}${tallyHtml(r.counts) || '<p class="quiet">No results yet</p>'}
    <p>${diffText(r.diff)}</p>
    <div class="tags"><span class="tag">${plural(total, "banner")}</span>${took ? `<span class="tag">took ${took}</span>` : ""}${left ? `<span class="tag">${plural(left, "carousel")} left out</span>` : ""}</div>
    ${(() => {
      const canXlsx = (excel && r.status === "done") || (manage && r.status !== "running" && total > 0), canDelete = manage && r.status !== "running";
      return canXlsx || canDelete ? `<div class="acts">${canXlsx ? `<button class="ghost sm" type="button" data-xlsx="${esc(r.run_id)}" title="Save this run's results as an Excel file">Save as Excel</button>` : ""}${canDelete ? `<button class="ghost sm danger" type="button" data-delrun="${esc(r.run_id)}" title="Delete this run and its files for good">Delete</button>` : ""}</div>` : "";
    })()}</div>`;
}
function viewRuns() {
  S.viewName = "runs"; S.run = null;
  view(`<div class="page"><div class="page-head"><h2>All runs</h2><span class="sub" id="runs-sub"></span></div><div class="sect"><div id="runs-body"></div></div></div>`);
  paintRunsView();
  S.timer = setTimeout(async () => { try { await loadRuns(); } catch (e) { /* next tick */ } }, 0);
}
function paintRunsView() {
  const body = $("runs-body"); if (!body) return;
  $("runs-sub").textContent = S.runs.length ? `${plural(S.runs.length, "run")}. Open one to see its banners.` : "";
  body.innerHTML = S.runs.length ? `<div class="cards">${S.runs.map((r) => runCardHtml(r, { excel: true, manage: true })).join("")}</div>`
    : '<div class="empty"><b>No runs yet</b>Start a check and its banners will show up here.</div>';
}

/* =====================================================================
   One run
   ===================================================================== */
const tally = () => { const c = {}; for (const b of S.banners) if (S.showHidden || !b.hidden) c[verdictOf(b)] = (c[verdictOf(b)] || 0) + 1; return c; };
function paintVerdict(fresh) {
  const c = tally(), total = ORDER.reduce((a, v) => a + (c[v] || 0), 0), el = $("verdict");
  if (!el) return;
  el.className = "verdict" + (fresh ? " fresh" : "");
  el.innerHTML = total ? ORDER.filter((v) => c[v]).map((v) => `<button type="button" class="seg" data-v="${v}" style="flex:${c[v]} 1 0;background:${COLOR[v]}" aria-pressed="${S.filter.has(v)}" data-dim="${S.filter.size > 0 && !S.filter.has(v)}" title="Show only ${esc(WORD[v].toLowerCase())}"><span class="n">${c[v]}</span><span class="w">${esc(WORD[v])}</span></button>`).join("") : "";
}
function matches(b) {
  if (b.hidden && !S.showHidden) return false;
  if (S.filter.size && !S.filter.has(verdictOf(b))) return false;
  if (S.carousel !== "" && String(b.section_index) !== S.carousel) return false;
  if (S.q) {
    const bc = b.banner_check || {};
    const hay = [b.alt_text, b.reason, b.slug, b.listing_title, b.destination_raw, bc.banner_deal, ...(bc.banner_brands || [])].join(" ").toLowerCase();
    if (!hay.includes(S.q)) return false;
  }
  return true;
}
function paintCarousels() {
  const sel = $("carousel"); if (!sel) return;
  const titles = new Map();
  for (const b of S.banners) if (b.section_index != null && (!titles.has(b.section_index) || (!titles.get(b.section_index) && b.carousel_label))) titles.set(b.section_index, b.carousel_label || "");
  sel.innerHTML = '<option value="">All carousels</option>' + [...titles].sort((a, b) => a[0] - b[0]).map(([i, t]) => `<option value="${i}">Carousel ${i}${t ? " (" + esc(t) + ")" : ""}</option>`).join("");
  sel.value = titles.has(Number(S.carousel)) ? S.carousel : ""; S.carousel = sel.value;
}
function frameHtml(b) {
  const v = verdictOf(b), bc = b.banner_check || {};
  const why = ["FAIL", "INCONCLUSIVE", "UNAVAILABLE"].includes(v) ? shortExtraBrands(explainReason(b)) : "";
  const brands = bc.banner_brands || [], deal = bc.banner_deal || "";
  const flags = [(b.total_links || 0) > 1 ? '<span class="flag mu" title="More than one link in this banner">MU</span>' : "",
    b.hidden ? `<span class="flag" title="${esc(hiddenText(b.hidden_reason) || "Hidden")}">H</span>` : ""].join("");
  const slide = b.block_index != null ? `slide ${Number(b.block_index) + 1}` : "";
  return `<div class="frame" data-b="${esc(b.banner_id)}" data-skip="${!!b.skip_requested}" aria-current="${S.openId === b.banner_id}">
    <button type="button" class="open" data-act="open" aria-label="${esc(verdictLabel(b))}: ${esc(b.alt_text || why || "banner")}">
      <div class="art" style="border-left-color:${COLOR[v]}"><img loading="lazy" alt="" src="${esc(imgSrc(S.run.run_id, b))}">
        ${flags ? `<div class="corner">${flags}</div>` : ""}
        ${v === "PROCESSING" ? `<span class="doing" title="What this banner is doing right now"><i class="live"></i>${esc(b.activity || "Working")}</span>` : ""}</div></button>
    <div class="cap"><span class="verdict-word" style="color:${COLOR[v]}">${esc(verdictLabel(b))}${b.skip_requested ? " (will be skipped)" : ""}</span>
      <p class="alt">${esc(b.alt_text || "(no alt text)")}${slide ? ` <span class="quiet">${esc(slide)}</span>` : ""}</p>
      ${b.destination_raw ? `<p class="dest"><a href="${esc(b.destination_raw)}" target="_blank" rel="noopener noreferrer">${esc(b.destination_raw)}</a></p>` : ""}
      ${why ? `<p class="why"><b>Reason:</b> ${esc(why)}</p>` : ""}
      ${brands.length || deal ? `<div class="tags">${brands.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}${deal ? `<span class="tag">${esc(deal)}</span>` : ""}</div>` : ""}</div>
    ${b.can_retry || v === "PENDING" ? `<div class="btns">${b.can_retry ? `<button class="ghost sm" type="button" data-act="retry" title="Check this banner again from scratch">Retry this banner</button>` : ""}${v === "PENDING" ? `<button class="ghost sm" type="button" data-act="skip">${b.skip_requested ? "Unskip" : "Skip"}</button>` : ""}</div>` : ""}</div>`;
}
function paintCount(shown) {
  const el = $("vcount"); if (!el) return;
  const total = Object.values(tally()).reduce((a, n) => a + n, 0);
  el.innerHTML = shown === total ? `<b>${total}</b> ${total === 1 ? "slide" : "slides"}` : `<b>${shown}</b> of ${total} slides`;
}
function paintSheet() {
  const el = $("sheet"); if (!el) return;
  const shown = S.banners.filter(matches);
  S.order = shown.map((b) => b.banner_id);
  paintCount(shown.length);
  if (!shown.length) {
    el.innerHTML = S.banners.length ? '<div class="empty"><b>No banners match</b>Clear a verdict, the carousel or the search to see more.</div>'
      : '<div class="empty"><b>No banners in this run yet</b>They appear here as each one is checked.</div>';
    return;
  }
  const groups = new Map();
  for (const b of shown) { if (!groups.has(b.section_index)) groups.set(b.section_index, []); groups.get(b.section_index).push(b); }
  el.innerHTML = [...groups].sort((a, b) => a[0] - b[0]).map(([i, list]) =>
    `<section class="group"><h3>Carousel ${esc(i)}<span>${esc(list.find((x) => x.carousel_label)?.carousel_label || "")}</span></h3><div class="frames">${list.map(frameHtml).join("")}</div></section>`).join("");
}
let repaintTimer = null;
function repaintSoon() { clearTimeout(repaintTimer); repaintTimer = setTimeout(() => { paintVerdict(false); paintSheet(); paintRunNote(); }, 120); }
function upsert(row) {
  const i = S.banners.findIndex((b) => b.banner_id === row.banner_id);
  if (i >= 0) S.banners[i] = row; else S.banners.push(row);
  if (S.openId === row.banner_id) paintDrawer();
  repaintSoon();
}
function paintRunNote() {
  const r = S.run, el = $("note"); if (!r || !el) return;
  const hidden = S.banners.filter((b) => b.hidden).length, bits = [...(r.warnings || [])];
  el.classList.toggle("warn", !!(r.warnings || []).length);
  if (r.status === "running") { const d = S.banners.filter((b) => !["PENDING", "PROCESSING"].includes(verdictOf(b))).length; bits.push(`Still running: ${d} of ${S.banners.length} done. Results appear as they finish.`); }
  else if (r.status === "cancelled") bits.push("This run was cancelled, so some banners were never checked.");
  else if (r.status === "failed") bits.push("This run itself failed: " + (r.error ? String(r.error).slice(-300) : "no reason recorded") + ".");
  if (hidden) bits.push(S.showHidden ? `${plural(hidden, "hidden banner")} included in the count.` : `${plural(hidden, "hidden banner")} left out of the count.`);
  el.textContent = bits.join(" ");
}
async function viewRun(runId) {
  S.viewName = "run";
  const token = ++S.token;
  S.filter = new Set(); S.carousel = ""; S.q = ""; S.showHidden = false; S.openId = null;
  view('<div class="page"><p class="hint">Loading the run.</p></div>');
  let run, banners;
  try { [run, banners] = await Promise.all([api(`/api/runs/${encodeURIComponent(runId)}`), api(`/api/runs/${encodeURIComponent(runId)}/banners`)]); }
  catch (e) { if (token === S.token) view(`<div class="page"><div class="empty"><b>Can't open this run</b>${esc(e.message)}. It may have been deleted (runs are removed after 30 days).<br><a href="#/runs">Back to all runs</a></div></div>`); return; }
  if (token !== S.token) return;
  S.run = run; S.banners = banners; paintRail();
  const sched = run.schedule_id != null ? S.scheds.find((s) => s.id === run.schedule_id) : null;
  const home = (run.page || "home") === "home";
  view(`<div class="page">
    ${run.schedule_id != null ? `<a class="back" href="#/schedules/${run.schedule_id}">&larr; ${esc(sched ? sched.name : "This run's schedule")}</a>` : '<a class="back" href="#/runs">&larr; All runs</a>'}
    <div class="page-head"><h2>${esc(pageLabel(run.page))}</h2>
      <span class="sub">${home ? esc(pretty(run.l1) + ", " + run.l2) + ", " : ""}${esc(stateName(run.state))}, pincode ${esc(run.pincode)}, ${esc(run.scope)}${run.banner_limit ? ", first " + run.banner_limit : ""}, started ${esc(fmtTime(run.started_at))}</span>
      <span class="acts" id="run-acts">
        <button class="ghost danger" type="button" id="stop" ${run.status === "running" ? "" : "hidden"}>Stop run</button>
        <button class="ghost" type="button" id="xlsx" ${run.status === "running" ? "disabled title=\"Available once the run has finished\"" : ""}>Download Excel</button>
        <button class="ghost" type="button" id="hide-run" title="Remove from the runs list (the files stay on disk)">Remove from list</button></span></div>
    <div class="vrow"><div class="verdict" id="verdict" role="group" aria-label="Verdicts in this run"></div><div class="vcount" id="vcount" aria-live="polite"></div></div>
    <p class="note" id="note"></p>
    <div id="diff"></div>
    <div class="tools">
      <select id="carousel" aria-label="Carousel"></select>
      <input id="q" type="search" placeholder="Search brand, deal, link or reason" aria-label="Search banners">
      <label class="chk"><input id="show-hidden" type="checkbox"> Show hidden and out-of-schedule banners</label>
      <button class="ghost sm" type="button" id="clear-filters" hidden>Clear filters</button>
    </div>
    <div id="sheet"></div></div>`);
  paintCarousels(); paintVerdict(true); paintSheet(); paintRunNote(); loadDiff(run);
  if (run.status !== "running") banners.filter((b) => b.result === "PROCESSING").forEach((b) => watchRetry(b.banner_id));   // a Retry still going
  if (run.status === "running") {
    const es = new EventSource(`${API}/api/runs/${encodeURIComponent(runId)}/stream`);
    S.stream = es;
    es.onmessage = (e) => upsert(JSON.parse(e.data));
    es.addEventListener("done", async () => { closeStream(); await loadRuns(); if (token === S.token) { S.run = S.runs.find((x) => x.run_id === runId) || { ...S.run, status: "done" }; $("stop").hidden = true; $("xlsx").disabled = false; paintRunNote(); loadDiff(S.run); } });
    es.onerror = () => { /* the browser reconnects; a finished run ends with the done event */ };
  }
}
async function loadDiff(run) {
  const box = $("diff"); if (!box) return;
  if (run.schedule_id == null || !run.diff) { box.innerHTML = ""; return; }
  box.innerHTML = `<p class="diffline">Since this schedule's previous run: ${diffText(run.diff)} ${run.diff.baseline ? "" : '<button class="ghost sm" type="button" id="diff-more">Show what changed</button>'}</p><div id="diff-list"></div>`;
  const more = $("diff-more"); if (!more) return;
  more.onclick = async () => {
    const list = $("diff-list");
    if (list.innerHTML) { list.innerHTML = ""; more.textContent = "Show what changed"; return; }
    try {
      const d = await api(`/api/runs/${encodeURIComponent(run.run_id)}/diff`);
      const sect = (title, arr) => arr && arr.length ? `<h4 style="margin:10px 0 2px">${esc(title)}</h4><ul class="difflist">${arr.map((x) => `<li><b>${esc(x.alt_text || x.banner_id)}</b> <span class="was">${esc(x.was || "")}${x.was ? " to " : ""}${esc(x.now || "")}</span>${x.reason ? `<br>${esc(x.reason)}` : ""}</li>`).join("")}</ul>` : "";
      list.innerHTML = sect("Newly failing", d.newly_failing) + sect("Newly inconclusive", d.newly_inconclusive) + sect("Recovered", d.recovered) + sect("New in the feed", d.new_banners) + sect("Gone from the feed", d.removed_banners) +
        (d.still_failing ? `<p class="hint">${plural(d.still_failing, "banner")} still failing, same as before.</p>` : "") || '<p class="hint">Nothing changed.</p>';
      more.textContent = "Hide details";
    } catch (e) { toast("Couldn't load the changes: " + e.message, true); }
  };
}

/* ---------- retry / skip ---------- */
// Follows a banner's manual Retry until the server stops reporting it as in progress. The server knows a retry is in flight,
// so this also picks up one started from the classic view or another tab, and shows its real try number.
async function watchRetry(id) {
  const runId = S.run.run_id, key = runId + "/" + id;
  if (S.retrying.has(key)) return;
  S.retrying.set(key, true);
  for (let i = 0; i < 300; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (!S.run || S.run.run_id !== runId) break;
    try {
      const rows = await api(`/api/runs/${encodeURIComponent(runId)}/banners`), row = rows.find((r) => r.banner_id === id);
      if (row) { upsert(row); if (row.result !== "PROCESSING") break; }
    } catch (e) { /* try again next tick */ }
  }
  S.retrying.delete(key);
}
async function retryBanner(id) {
  const b = S.banners.find((x) => x.banner_id === id); if (!b || S.retrying.has(S.run.run_id + "/" + id)) return;
  try { await api(`/api/runs/${encodeURIComponent(S.run.run_id)}/banners/${encodeURIComponent(id)}/retry`, { method: "POST" }); }
  catch (e) { toast("Couldn't start the retry: " + e.message, true); return; }
  // a retry starts again at try 1 of the usual number
  upsert({ ...b, result: "PROCESSING", reason: "", try_number: 1, max_tries: b.max_tries || 5, retries_exhausted: false, can_retry: false, activity: "Checking again" });
  watchRetry(id);
}
async function toggleSkip(id) {
  const b = S.banners.find((x) => x.banner_id === id); if (!b) return;
  const act = b.skip_requested ? "unskip" : "skip";
  try { await api(`/api/runs/${encodeURIComponent(S.run.run_id)}/banners/${encodeURIComponent(id)}/${act}`, { method: "POST" }); upsert({ ...b, skip_requested: !b.skip_requested }); }
  catch (e) { toast(`Couldn't ${act} this banner: ${e.message}`, true); }
}

/* ---------- detail drawer ---------- */
const mark = (ok) => ok === true ? '<span class="t" title="Matches">&#10003;</span>' : ok === false ? '<span class="f" title="Does not match">&#10005;</span>' : '<span class="u" title="Not checked">&ndash;</span>';
const yn = (v) => v === true ? "Yes" : v === false ? "No" : v;
function ledgerRows(b) {
  const bc = b.banner_check || {}, rows = [];
  if ((bc.banner_brands || []).length) {
    const has = (bc.brand_checks || []).map((c) => c.found ? `${esc(c.matched_as)}${c.products ? " (" + Number(c.products).toLocaleString() + ")" : ""}` : `${esc(c.brand)} is not in the listing`).join("<br>") || "No brand filter";
    const extra = (bc.extra_brands || []).length ? `<br>Also lists ${esc(bc.extra_brands.join(", "))}` : "";
    rows.push(["Brands", esc(bc.banner_brands.join(", ")), has + extra, !(bc.missing_brands || []).length && !(bc.extra_brands || []).length]);
  }
  if (bc.banner_deal) rows.push(["Deal", esc(bc.banner_deal), esc(b.listing_title || bc.listing_title || "No title"), bc.title_matches_deal ?? null]);
  if (bc.banner_gender) rows.push(["Audience", esc(pretty(bc.banner_gender)), esc((bc.listing_genders || []).join(", ") || "No gender filter"), typeof bc.gender_matches === "boolean" ? bc.gender_matches : null]);
  const sc = bc.sort_check;
  if (sc && sc.status) rows.push(["Deal on the listing", esc(sc.deal || bc.banner_deal || ""), esc(sc.status === "MATCH" ? "Holds up when sorted" : sc.status === "MISMATCH" ? "Does not hold when sorted" : "Couldn't check"), sc.status === "MATCH" ? true : sc.status === "MISMATCH" ? false : null]);
  const rc = b.reference_check;
  if (rc) rows.push(["Reference", "What a person said it should lead to", esc(referenceText(b)), rc.status === "MATCH" ? true : rc.status === "MISMATCH" ? false : null]);
  return rows;
}
function hotspotBlock(h) {
  const c = h.banner_check || {}, src = cropSrc(S.run.run_id, h);
  const rows = [["Result", esc(WORD[h.result] || h.result)], ["Link", h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">${esc(h.url)}</a>` : ""], ["Reason", esc(hotspotReason(h))], ["Listing title", esc(h.listing_title)],
    ["Banner brands", esc((c.banner_brands || []).join(", "))], ["Banner deal", esc(c.banner_deal)], ["Banner audience", esc(c.banner_gender)], ["Listing genders", esc((c.listing_genders || []).join(", "))],
    ["Missing brands", esc((c.missing_brands || []).join(", "))], ["Extra brands", esc((c.extra_brands || []).join(", "))]].filter(([, v]) => v);
  return `<div class="hotblock" style="border-left-color:${COLOR[h.result] || "var(--skip)"}"><h5>Link ${Number(h.hotspot_index ?? 0) + 1}</h5>${src ? `<img loading="lazy" alt="" src="${esc(src)}">` : ""}${kvHtml(rows)}</div>`;
}
function paintDrawer() {
  const b = S.banners.find((x) => x.banner_id === S.openId), d = $("drawer");
  if (!b) { d.innerHTML = ""; return; }
  const v = verdictOf(b), bc = b.banner_check || {}, rows = ledgerRows(b), reason = explainReason(b);
  const i = S.order.indexOf(b.banner_id);
  const ledger = rows.length ? `<table class="ledger"><caption>Says and shows</caption>
    <tr class="lh"><td></td><td class="says">The banner says</td><td class="has">The listing has</td><td class="mk"></td></tr>
    ${rows.map(([k, says, has, ok]) => `<tr><th scope="row">${esc(k)}</th><td class="says">${says}</td><td class="has">${has}</td><td class="mk">${mark(ok)}</td></tr>`).join("")}</table>` : "";
  const all = [
    ["Hidden", b.hidden ? esc(hiddenText(b.hidden_reason)) : ""],
    ["Carousel and slide", `${esc(b.section_index)}${b.carousel_label ? " (" + esc(b.carousel_label) + ")" : ""}${b.block_index != null ? ", slide " + (Number(b.block_index) + 1) : ""}`],
    ["Position in the feed", esc(b.position)],
    ["Destination", b.destination_raw ? `<a href="${esc(b.destination_raw)}" target="_blank" rel="noopener noreferrer">${esc(b.destination_raw)}</a>` : ""],
    ["Listing title", esc(b.listing_title)], ["Products in the listing", b.total_results != null ? Number(b.total_results).toLocaleString() : ""],
    ["Store", b.listing_store ? esc(b.listing_store) : ""],
    ["Banner brands", esc((bc.banner_brands || []).join(", "))], ["Banner deal", esc(bc.banner_deal)], ["Title matches deal", esc(yn(bc.title_matches_deal))],
    ["Deal works on the listing", bc.sort_check && bc.sort_check.summary ? esc(bc.sort_check.summary) : ""],
    ["Banner audience", esc(bc.banner_gender)], ["Listing genders", esc((bc.listing_genders || []).join(", "))], ["Audience matches", esc(yn(bc.gender_matches))],
    ["Missing brands", esc((bc.missing_brands || []).join(", "))], ["Extra brands", esc((bc.extra_brands || []).join(", "))],
    ["Tries", `${esc(b.attempts ?? 1)}${b.retries_exhausted ? ", all used" : ""}`],
  ].filter(([, x]) => x !== undefined && x !== null && x !== "");
  d.innerHTML = `<div class="d-head"><span class="stamp"><i style="background:${COLOR[v]}"></i>${esc(LONG[v] || verdictLabel(b))}</span>
      <span class="steps"><button class="x" type="button" data-act="prev" aria-label="Previous banner" ${i > 0 ? "" : "disabled"}>&lsaquo;</button><button class="x" type="button" data-act="next" aria-label="Next banner" ${i >= 0 && i < S.order.length - 1 ? "" : "disabled"}>&rsaquo;</button><button class="x" type="button" data-act="close" aria-label="Close detail">&times;</button></span></div>
    <div class="d-art"><img alt="${esc(b.alt_text || "Banner artwork")}" src="${esc(imgSrc(S.run.run_id, b))}"></div>
    <div class="d-body">
      ${reason ? `<p class="why-big">${esc(reason)}</p>` : v === "PASS" ? '<p class="why-big">Everything the banner promises is on the listing.</p>' : ""}
      ${ledger}
      <details class="fold" open><summary>All details</summary>${kvHtml(all)}</details>
      ${(b.hotspot_checks || []).length ? `<details class="fold" open><summary>Links inside this banner (${b.hotspot_checks.length})</summary>${b.hotspot_checks.map(hotspotBlock).join("")}</details>` : ""}
      <div class="d-actions">${b.can_retry ? '<button class="primary" type="button" data-act="retry-d">Retry this banner</button>' : ""}${v === "PENDING" ? `<button class="ghost" type="button" data-act="skip-d">${b.skip_requested ? "Unskip" : "Skip this banner"}</button>` : ""}</div>
      <p class="hint" id="d-note"></p>
    </div>`;
}
function openDrawer(id) { S.openId = id; paintDrawer(); paintSheet(); $("drawer").dataset.open = "true"; $("scrim").hidden = false; $("drawer").querySelector('[data-act="close"]')?.focus(); }
function closeDrawer(quiet) { S.openId = null; $("drawer").dataset.open = "false"; $("scrim").hidden = true; if (!quiet && S.viewName === "run") paintSheet(); }
function stepDrawer(delta) { const i = S.order.indexOf(S.openId), n = S.order[i + delta]; if (n) { S.openId = n; paintDrawer(); paintSheet(); $("drawer").scrollTop = 0; } }

/* =====================================================================
   Selection widgets: axes (pages, l1, l2, state, pincode) shared by the check dialog and the schedule form
   ===================================================================== */
const openPickers = new Set();
document.addEventListener("mousedown", (e) => { for (const p of [...openPickers]) if (!p.root.contains(e.target)) p.close(); });

function makePills(box, items, chosen, opts, changed) {
  const paint = () => {
    box.innerHTML = items.map((it) => `<button type="button" class="pill ${opts.cls ? opts.cls(it) : ""}" data-k="${esc(it.id)}" aria-pressed="${chosen.has(it.id)}">${esc(it.label)}</button>`).join("");
  };
  box.onclick = (e) => {
    const b = e.target.closest(".pill"); if (!b || b.disabled) return;
    const k = b.dataset.k;
    if (chosen.has(k)) { if (chosen.size > 1) chosen.delete(k); } else chosen.add(k);
    paint(); changed();
  };
  paint();
  return paint;
}
function makeStatePicker(root, options, initial, changed, dflt) {
  let chosen = initial.filter((x) => options.includes(x)), query = "";
  root.className = "ms";
  root.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox" aria-expanded="false"><span class="lbl"></span><span aria-hidden="true">&#9662;</span></button>
    <div class="ms-pop" hidden><input type="text" placeholder="Type to filter" autocomplete="off" aria-label="Filter states"><div class="ms-list" role="listbox" aria-multiselectable="true"></div>
      <div class="ms-foot"><span class="cnt"></span><span><button type="button" class="ghost sm" data-x="clear">Clear</button> <button type="button" class="ghost sm" data-x="done">Done</button></span></div></div>
    <div class="chips"></div>`;
  const btn = root.querySelector(".ms-btn"), pop = root.querySelector(".ms-pop"), search = pop.querySelector("input"), list = pop.querySelector(".ms-list"), chips = root.querySelector(".chips");
  const self = { root, close };
  function paint() {
    root.querySelector(".lbl").textContent = chosen.length === 0 ? `Select states (blank means ${stateName(dflt)})` : chosen.length === 1 ? stateName(chosen[0]) : `${chosen.length} states selected`;
    chips.innerHTML = chosen.map((v) => `<span class="xchip">${esc(stateName(v))}<button type="button" data-rm="${esc(v)}" aria-label="Remove ${esc(stateName(v))}">&times;</button></span>`).join("");
    pop.querySelector(".cnt").textContent = `${chosen.length} selected`;
    const rows = options.filter((o) => o.toLowerCase().includes(query));
    list.innerHTML = rows.length ? rows.map((o) => `<div class="ms-opt${chosen.includes(o) ? " sel" : ""}" role="option" aria-selected="${chosen.includes(o)}" data-v="${esc(o)}"><span class="ck">&#10003;</span><span>${esc(o)}</span></div>`).join("") : '<p class="hint">No state matches.</p>';
  }
  function toggle(v) { chosen = chosen.includes(v) ? chosen.filter((x) => x !== v) : [...chosen, v]; paint(); changed(); }
  function open() { pop.hidden = false; btn.setAttribute("aria-expanded", "true"); openPickers.add(self); query = ""; search.value = ""; paint(); search.focus(); }
  function close() { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); openPickers.delete(self); }
  btn.onclick = () => (pop.hidden ? open() : close());
  search.oninput = () => { query = search.value.trim().toLowerCase(); paint(); };
  search.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); btn.focus(); } else if (e.key === "Enter") { e.preventDefault(); const f = options.find((o) => o.toLowerCase().includes(query)); if (f) toggle(f); } };
  list.onmousedown = (e) => e.preventDefault();
  list.onclick = (e) => { const o = e.target.closest(".ms-opt"); if (o) toggle(o.dataset.v); };
  pop.onclick = (e) => { const x = e.target.closest("[data-x]"); if (!x) return; if (x.dataset.x === "clear") { chosen = []; paint(); changed(); } else { close(); btn.focus(); } };
  chips.onclick = (e) => { const b = e.target.closest("[data-rm]"); if (b) toggle(b.dataset.rm); };
  paint();
  return { get: () => chosen.slice(), set: (v) => { chosen = v.filter((x) => options.includes(x)); paint(); } };
}
function makePincodeInput(root, initial, changed) {
  let chosen = [...new Set(initial)];
  root.innerHTML = '<div class="pin"><input type="text" inputmode="numeric" autocomplete="off" placeholder="Type a pincode, press Enter" aria-label="Add a pincode"></div><p class="hint" hidden></p>';
  const box = root.querySelector(".pin"), input = box.querySelector("input"), msg = root.querySelector(".hint");
  const say = (t) => { msg.textContent = t; msg.hidden = !t; };
  function paint() {
    box.querySelectorAll(".xchip").forEach((c) => c.remove());
    for (const v of chosen) {
      const chip = document.createElement("span"); chip.className = "xchip";
      chip.innerHTML = `${esc(v)}<button type="button" aria-label="Remove ${esc(v)}">&times;</button>`;
      chip.querySelector("button").onclick = (e) => { e.stopPropagation(); chosen = chosen.filter((x) => x !== v); paint(); changed(); };
      box.insertBefore(chip, input);
    }
  }
  function commit() {
    const bad = []; let added = false;
    for (const t of input.value.split(/[\s,;]+/).filter(Boolean)) { if (!/^\d{6}$/.test(t)) bad.push(t); else if (!chosen.includes(t)) { chosen.push(t); added = true; } }
    input.value = bad.join(" ");
    say(bad.length ? `${bad.map((b) => `"${b}"`).join(", ")} ${bad.length === 1 ? "isn't" : "aren't"} a 6-digit pincode.` : "");
    if (added) { paint(); changed(); }
  }
  input.oninput = () => { input.value = input.value.replace(/[^\d,;\s]/g, ""); say(""); };
  input.onkeydown = (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); } else if (e.key === "Backspace" && !input.value && chosen.length) { chosen.pop(); paint(); changed(); } };
  box.onclick = () => input.focus();
  paint();
  return { get() { commit(); return chosen.slice(); }, pending: () => input.value.trim(), set(v) { chosen = [...new Set(v)]; input.value = ""; say(""); paint(); } };
}
function makeAxes(root, init, onChange) {
  const m = S.meta;
  const st = { pages: new Set(init.pages || ["home"]), l1: new Set(init.l1s || [m.l1_options[0]]), l2: new Set(init.l2s || [m.l2_options[0]]) };
  root.innerHTML = `<div class="axis"><span class="lab">Pages</span><div class="pills" data-ax="pages"></div></div>
    <div class="axis" data-hl><span class="lab">l1 segment (Home only)</span><div class="pills" data-ax="l1"></div></div>
    <div class="axis" data-hl><span class="lab">l2 segment (Home only)</span><div class="pills" data-ax="l2"></div></div>
    <div class="form" style="margin-top:0;grid-template-columns:1fr 1fr"><div><span class="lab">State</span><div data-ax="state"></div></div><div><span class="lab">Pincode</span><div data-ax="pin"></div></div></div>
    <p class="combo" data-ax="combo"></p>`;
  const q = (k) => root.querySelector(`[data-ax="${k}"]`);
  const changed = () => { paint(); if (onChange) onChange(); };
  const state = makeStatePicker(q("state"), m.state_options, init.states || [m.default_state], changed, m.default_state);
  const pin = makePincodeInput(q("pin"), init.pincodes || [m.default_pincode], changed);
  const repaintPills = [
    makePills(q("pages"), m.page_options.map((p) => ({ id: p.id, label: p.label, tier: p.tier })), st.pages, { cls: (it) => it.tier === "premium" ? "prem" : it.tier === "home" ? "home" : "" }, changed),
    makePills(q("l1"), m.l1_options.map((x) => ({ id: x, label: pretty(x) })), st.l1, {}, changed),
    makePills(q("l2"), m.l2_options.map((x) => ({ id: x, label: pretty(x) })), st.l2, {}, changed),
  ];
  function read() {
    const s = state.get(), p = pin.get();
    return { pages: [...st.pages], l1s: [...st.l1], l2s: [...st.l2], states: s.length ? s : [m.default_state], pincodes: p.length ? p : [m.default_pincode] };
  }
  const count = () => { const v = read(); return v.pages.reduce((n, p) => n + (p === "home" ? v.l1s.length * v.l2s.length : 1), 0) * v.states.length * v.pincodes.length; };
  function paint() {
    const v = read(), n = count(), homeOn = v.pages.includes("home"), others = v.pages.length - (homeOn ? 1 : 0), over = n > m.max_combos;
    root.querySelectorAll("[data-hl]").forEach((el) => { el.classList.toggle("dimmed", !homeOn); el.querySelectorAll(".pill").forEach((p) => { p.disabled = !homeOn; }); });
    const what = [homeOn ? `Home: ${v.l1s.length} l1 × ${v.l2s.length} l2` : null, others ? plural(others, "other page") : null].filter(Boolean).join(" + ");
    const c = q("combo"); c.className = "combo" + (over ? " over" : "");
    c.textContent = n === 1 ? "1 run." : `(${what}) × ${plural(v.states.length, "state")} × ${plural(v.pincodes.length, "pincode")} = ${n} runs` + (over ? `. That is over the limit of ${m.max_combos}; narrow it down.` : ", one after another.");
  }
  paint();
  return {
    read, count, over: () => count() > m.max_combos,
    problem() { pin.get(); const p = pin.pending(); return p ? `"${p}" in the pincode box isn't a 6-digit pincode. Fix or clear it first.` : ""; },
    set(v) { st.pages = new Set(v.pages || ["home"]); st.l1 = new Set(v.l1s); st.l2 = new Set(v.l2s); state.set(v.states || []); pin.set(v.pincodes || []); repaintPills.forEach((f, i) => f()); paint(); },
  };
}
/* carousel checklist from /api/feed-preview, keyed by the CMS section id */
async function loadCarouselList(axes, scope) {
  const { pages, l1s, l2s } = axes.read();
  const rows = await api(`/api/feed-preview?page=${encodeURIComponent(pages[0])}&l1=${encodeURIComponent(l1s[0])}&l2=${encodeURIComponent(l2s[0])}&scope=${encodeURIComponent(scope)}`);
  const groups = new Map();
  for (const b of rows) { if (!groups.has(b.section_id)) groups.set(b.section_id, { id: b.section_id, count: 0, image: b.image_url, label: b.label || b.alt_text || `Carousel ${b.section_index}` }); groups.get(b.section_id).count++; }
  return [...groups.values()];
}
function carouselChecklist(box, cars, excluded, staleLabels) {
  const inFeed = new Set((cars || []).map((c) => c.id));
  let html = "";
  if (cars) html += `<div class="row-inline" style="margin-top:8px"><button type="button" class="ghost sm" data-all="1">Select all</button><button type="button" class="ghost sm" data-all="0">Deselect all</button><span class="hint">${plural(cars.length, "carousel")}. Untick one to leave it out.</span></div>
    <div class="carlist">${cars.map((c) => `<label class="carrow"><input type="checkbox" data-sec="${esc(c.id)}" ${excluded.has(c.id) ? "" : "checked"}>${c.image ? `<img loading="lazy" alt="" src="${esc(c.image)}">` : ""}<span>${esc(c.label)}</span><span class="n">${plural(c.count, "banner")}</span></label>`).join("")}</div>`;
  const stale = [...excluded].filter((id) => !inFeed.has(id));
  if (stale.length) html += `<p class="hint">${cars ? "Left out, but not in the current feed:" : "Currently left out:"}</p><div class="chips">${stale.map((id) => `<span class="xchip">${esc(staleLabels.get(id) || id.slice(0, 10) + "...")}<button type="button" data-rm="${esc(id)}" aria-label="Stop leaving this one out">&times;</button></span>`).join("")}</div>`;
  else if (!cars) html += '<p class="hint">None left out. Every carousel in scope is checked.</p>';
  box.innerHTML = html;
  box.onchange = (e) => { const cb = e.target.closest("[data-sec]"); if (!cb) return; const c = cars.find((x) => x.id === cb.dataset.sec); if (cb.checked) excluded.delete(c.id); else { excluded.add(c.id); staleLabels.set(c.id, c.label); } };
  box.onclick = (e) => {
    const all = e.target.closest("[data-all]"), rm = e.target.closest("[data-rm]");
    if (all) { for (const c of cars) { if (all.dataset.all === "1") excluded.delete(c.id); else { excluded.add(c.id); staleLabels.set(c.id, c.label); } } carouselChecklist(box, cars, excluded, staleLabels); }
    else if (rm) { excluded.delete(rm.dataset.rm); carouselChecklist(box, cars, excluded, staleLabels); }
  };
}

/* =====================================================================
   Start a check (dialog)
   ===================================================================== */
const D = { labels: new Map() };
function dlgSettings() {
  const lim = $("d-limit").value;
  return { ...S.dlgAxes.read(), scope: $("d-scope").value, banner_limit: lim ? parseInt(lim, 10) : null,
    workers: Math.max(1, Math.min(S.meta.max_workers, parseInt($("d-workers").value || "3", 10))), excluded_sections: [...S.dlgExcluded] };
}
function dlgMsg(t, err) { $("d-msg").textContent = t; $("d-msg").className = "msg" + (err ? " err" : ""); }
function openDlg() {
  if (!S.dlgAxes) {
    S.dlgAxes = makeAxes($("dlg-axes"), { pages: ["home"], l1s: [S.meta.l1_options[0]], l2s: [S.meta.l2_options[0]], states: [S.meta.default_state], pincodes: [S.meta.default_pincode] }, () => { S.dlgCars = null; S.dlgExcluded.clear(); carouselChecklist($("d-cars"), null, S.dlgExcluded, D.labels); $("d-go").disabled = S.dlgAxes.over(); });
    $("d-scope").innerHTML = S.meta.scopes.map((s) => `<option value="${esc(s)}">${esc(s === "hero" ? "Hero carousels only" : s === "all" ? "Every banner on the feed" : s)}</option>`).join("");
    $("d-workers").max = S.meta.max_workers;
    carouselChecklist($("d-cars"), null, S.dlgExcluded, D.labels);
  }
  dlgMsg(""); $("d-go").disabled = S.dlgAxes.over(); $("dlg").showModal();
}
async function dlgLoadCars() {
  const btn = $("d-load-car"); btn.disabled = true; btn.textContent = "Loading";
  try {
    S.dlgCars = await loadCarouselList(S.dlgAxes, $("d-scope").value);
    const first = S.dlgAxes.read();
    $("d-car-hint").textContent = `Showing ${first.pages[0] === "home" ? pretty(first.l1s[0]) + ", " + first.l2s[0] : pageLabel(first.pages[0])} (the first of your selection).`;
    carouselChecklist($("d-cars"), S.dlgCars, S.dlgExcluded, D.labels);
  } catch (e) { toast("Couldn't load the carousels: " + e.message, true); }
  finally { btn.disabled = false; btn.textContent = "Load carousels to choose which to run"; }
}
async function dlgStart(ev) {
  ev.preventDefault();
  const problem = S.dlgAxes.problem(); if (problem) { dlgMsg(problem, true); return; }
  if (S.dlgCars && S.dlgCars.length && S.dlgCars.every((c) => S.dlgExcluded.has(c.id))) { dlgMsg("Every carousel is unticked, so there would be nothing to check. Tick at least one.", true); return; }
  $("d-go").disabled = true; dlgMsg("Starting.");
  try {
    const { run_id, combos, queued } = await post("/api/runs", dlgSettings());
    $("dlg").close();
    if (queued) toast(`Started run 1 of ${combos}. The other ${queued} start one after another as each finishes.`);
    await loadRuns(); go(`#/runs/${encodeURIComponent(run_id)}`);
  } catch (e) { dlgMsg(e.message, true); $("d-go").disabled = false; }
}

/* =====================================================================
   Schedules
   ===================================================================== */
const needsAttention = (s) => s.unread_alerts > 0 || ["failed", "error", "skipped"].includes(s.last_status);
function schedHealth(s) {
  const r = s.latest_run;
  if (!s.enabled) return "h-off";
  if (["failed", "error"].includes(s.last_status)) return "h-fail";
  if (r && r.status === "running") return "h-running";
  if (r && r.status === "done") return "h-done";
  return "h-off";
}
const schedMatchesFilter = (s, k = S.schedFilter) => k === "all" || (k === "enabled" && s.enabled) || (k === "disabled" && !s.enabled) || (k === "attention" && needsAttention(s));
function schedSearchText(s) { return [s.name, ...pagesOf(s).map(pageLabel), ...s.l1s, ...s.l2s, ...s.states, ...s.pincodes, fmtInterval(s.interval_minutes), s.last_status || "", s.enabled ? "enabled" : "disabled"].join(" ").toLowerCase(); }
const schedMatchesQuery = (s) => { const w = S.schedQuery.toLowerCase().split(/\s+/).filter(Boolean); if (!w.length) return true; const t = schedSearchText(s); return w.every((x) => t.includes(x)); };
const alertSummary = (s) => s.notify_mode === "off" ? "Off" : `${NOTIFY_LABEL[s.notify_mode] || s.notify_mode}, ${s.notify_toast ? "in the app and as a Windows notification" : "in the app only"}`;
function alertHtml(a) {
  return `<div class="alert ${a.is_read ? "read" : ""} ${a.kind === "test" ? "test" : ""}"><button class="ax" type="button" data-del-alert="${a.id}" aria-label="Remove this alert" title="Remove this alert">&times;</button>
    <div class="t">${esc(a.title)}</div><div class="when">${esc(fmtTime(a.created_at))}${a.delivery ? `, Windows notification: ${esc(a.delivery)}` : ""}</div>
    ${a.message ? `<pre>${esc(a.message)}</pre>` : ""}
    <div class="acts">${a.run_id ? `<button class="ghost sm" type="button" data-open-run="${esc(a.run_id)}">Open run</button>` : ""}${a.is_read ? "" : `<button class="ghost sm" type="button" data-read="${a.id}">Mark read</button>`}</div></div>`;
}
function schedActions(s) {
  return [["view", "View"], ["edit", "Edit"], ["run", "Run now"], ["toggle", s.enabled ? "Disable" : "Enable"], ["delete", "Delete"]];
}
function schedCardHtml(s) {
  const r = s.latest_run, word = !s.enabled ? "Disabled" : s.last_status ? pretty(s.last_status) : "Waiting";
  return `<div class="card click ${schedHealth(s)}" data-sched="${s.id}" tabindex="0" role="button" title="Open this schedule's runs">
    ${s.unread_alerts ? `<span class="unread">${plural(s.unread_alerts, "alert")}</span>` : ""}
    <h4>${esc(s.name)}<span class="status ${s.enabled ? "" : ""}" style="background:${!s.enabled ? "var(--skip);color:#1b2228" : schedHealth(s) === "h-fail" ? "var(--fail)" : schedHealth(s) === "h-running" ? "var(--live)" : "var(--pass)"}">${esc(word)}</span></h4>
    <p class="quiet">Every ${fmtInterval(s.interval_minutes)}${s.start_at ? `, started ${esc(fmtWhen(s.start_at))}` : ""}</p>
    <p class="quiet">${s.enabled ? (s.next_run_at ? `Next run ${esc(fmtWhen(s.next_run_at))}` : "Next run unknown") : "Disabled. It won't run on its own."}</p>
    <div class="tags"><span class="tag">page</span>${tagsHtml(pagesOf(s).map(pageLabel))}</div>
    ${pagesOf(s).includes("home") ? `<div class="tags"><span class="tag">l1</span>${tagsHtml(s.l1s)}</div><div class="tags"><span class="tag">l2</span>${tagsHtml(s.l2s)}</div>` : ""}
    <div class="tags"><span class="tag">state</span>${tagsHtml(s.states.map(stateName), 3)}</div><div class="tags"><span class="tag">pincode</span>${tagsHtml(s.pincodes, 3)}</div>
    <p class="quiet">${plural(s.combo_count, "run")} per fire, ${plural(s.run_count, "run")} so far</p>
    ${r ? `${barHtml(r.counts)}${tallyHtml(r.counts)}<p class="quiet">Latest ${esc(fmtTime(r.started_at))}</p><p>${diffText(r.diff)}</p>` : '<p class="quiet">Hasn\'t run yet.</p>'}
    <div class="acts">${schedActions(s).map(([k, l]) => `<button class="ghost sm ${k === "delete" ? "danger" : ""}" type="button" data-act="${k}">${l}</button>`).join("")}</div></div>`;
}
function viewSchedList() {
  S.viewName = "sched-list";
  view(`<div class="page"><div class="page-head"><h2>Schedules</h2><span class="sub">Saved checks that run on their own and flag what changed.</span>
    <span class="acts"><a class="primary" href="#/schedules/new" style="text-decoration:none;display:inline-block">New schedule</a></span></div>
    <div id="sched-sum" class="sum"></div>
    <div class="sect"><h3>Alerts <span id="al-badge"></span><span class="right"><button class="ghost sm" type="button" id="al-readall">Mark all read</button><button class="ghost sm danger" type="button" id="al-clear">Clear all</button><button class="ghost sm" type="button" id="al-test">Send test notification</button></span></h3><div id="al-list"></div></div>
    <div class="sect"><h3>Schedules <span class="right" id="sched-chips"></span></h3>
      <div class="tools" style="border:0"><input type="search" id="sched-q" placeholder="Search name, page, state, pincode, interval or last result" aria-label="Search schedules" style="min-width:280px"></div>
      <div id="sched-cards"></div></div></div>`);
  $("sched-q").value = S.schedQuery;
  paintSchedList();
  refreshSched().catch(fail("Couldn't load schedules"));
  S.timer = setTimeout(function tick() { if (S.viewName !== "sched-list") return; refreshSched().catch(() => {}); S.timer = setTimeout(tick, 30000); }, 30000);
}
function paintSchedList() {
  const cards = $("sched-cards"); if (!cards) return;
  const scheds = S.scheds, unread = S.alerts.unread, perDay = Math.round(scheds.filter((s) => s.enabled).reduce((n, s) => n + s.combo_count * 1440 / s.interval_minutes, 0));
  $("sched-sum").innerHTML = `<span><b>${scheds.length}</b> schedule${scheds.length === 1 ? "" : "s"}</span><span><b>${scheds.filter((s) => s.enabled).length}</b> enabled</span><span><b>${scheds.filter(needsAttention).length}</b> need attention</span><span><b>${scheds.reduce((n, s) => n + s.run_count, 0)}</b> runs so far</span><span>about <b>${perDay}</b> run${perDay === 1 ? "" : "s"} a day when they all fire</span>`;
  $("al-badge").innerHTML = unread ? `<span class="badge">${unread} unread</span>` : "";
  $("al-readall").disabled = !unread; $("al-clear").disabled = !S.alerts.items.length;
  $("al-list").innerHTML = S.alerts.items.length ? S.alerts.items.slice(0, 8).map(alertHtml).join("") : '<p class="hint">No alerts yet. A schedule raises one when a run finds something new to fix.</p>';
  const chips = [["all", "All"], ["enabled", "Enabled"], ["disabled", "Disabled"], ["attention", "Needs attention"]];
  $("sched-chips").innerHTML = chips.map(([k, l]) => `<button class="chipbtn" type="button" data-sfilter="${k}" aria-pressed="${S.schedFilter === k}">${l} ${scheds.filter((s) => schedMatchesFilter(s, k)).length}</button>`).join("");
  const shown = scheds.filter((s) => schedMatchesFilter(s) && schedMatchesQuery(s));
  cards.innerHTML = shown.length ? `<div class="cards">${shown.map(schedCardHtml).join("")}</div>` : scheds.length ? '<div class="empty"><b>No schedule matches</b>Try another filter or clear the search.</div>' : '<div class="empty"><b>No schedules yet</b>Make one to have a check run by itself and tell you what changed.</div>';
}
async function schedAction(act, s) {
  try {
    if (act === "view") go(`#/schedules/${s.id}`);
    else if (act === "edit") go(`#/schedules/${s.id}/edit`);
    else if (act === "toggle") { await post(`/api/schedules/${s.id}`, { enabled: !s.enabled }, "PATCH"); await refreshSched(); if (S.viewName === "sched-detail") viewSchedDetail(s.id); }
    else if (act === "run") { const { run_id, combos, queued } = await post(`/api/schedules/${s.id}/run-now`); if (queued) toast(`Started run 1 of ${combos} for "${s.name}". The other ${queued} start one after another.`); await loadRuns(); go(`#/runs/${encodeURIComponent(run_id)}`); }
    else if (act === "delete") { if (!ask(`Delete the schedule "${s.name}"? Its past runs and alerts are kept.`)) return; await api(`/api/schedules/${s.id}`, { method: "DELETE" }); await refreshSched(); go("#/schedules"); }
  } catch (e) { toast(`Couldn't ${act === "run" ? "start the run" : act}: ${e.message}`, true); }
}
async function alertAction(el) {
  try {
    if (el.dataset.delAlert) await api(`/api/alerts/${el.dataset.delAlert}`, { method: "DELETE" });
    else if (el.dataset.read) await api(`/api/alerts/${el.dataset.read}/read`, { method: "POST" });
    await refreshSched(); if (S.viewName === "sched-detail") viewSchedDetail(Number(location.hash.split("/")[2]));
  } catch (e) { toast(e.message, true); }
}
/* one schedule's runs */
const runInRange = (r, from, to) => { if (from && to && from > to) [from, to] = [to, from]; if (!from && !to) return true; const t = new Date(r.started_at).getTime(); return t >= (from ? new Date(from + "T00:00:00").getTime() : -Infinity) && t <= (to ? new Date(to + "T23:59:59.999").getTime() : Infinity); };
function groupRuns(runs) {
  const groups = [], by = new Map();
  for (const r of runs) { const k = r.batch_id || r.run_id; if (!by.has(k)) { const g = { key: k, runs: [] }; by.set(k, g); groups.push(g); } by.get(k).runs.push(r); }
  for (const g of groups) g.runs.sort((a, b) => (a.started_at < b.started_at ? -1 : 1));
  return groups;
}
function runCardsHtml(runs) {
  let html = "", loose = [];
  const flush = () => { if (loose.length) { html += `<div class="cards">${loose.join("")}</div>`; loose = []; } };
  for (const g of groupRuns(runs)) {
    if (g.runs.length === 1) { loose.push(runCardHtml(g.runs[0], { excel: true })); continue; }
    flush();
    const totals = {}; for (const r of g.runs) for (const [k, v] of Object.entries(r.counts || {})) totals[k] = (totals[k] || 0) + v;
    const going = g.runs.filter((r) => r.status === "running").length;
    html += `<div class="fire"><b>${esc(fmtTime(g.runs[0].started_at))}</b>${tallyHtml(totals)}<span class="n">${g.runs.length} runs${going ? `, ${going} still going` : ""}</span></div><div class="cards">${g.runs.map((r) => runCardHtml(r, { excel: true })).join("")}</div>`;
  }
  flush();
  return html;
}
async function viewSchedDetail(id) {
  S.viewName = "sched-detail";
  const token = ++S.token; clearTimeout(S.timer);
  let s, runs;
  try { [s, runs] = await Promise.all([api(`/api/schedules/${id}`), api(`/api/runs?schedule_id=${id}&limit=${RUNS_LOADED}`)]); }
  catch (e) { if (token === S.token) { toast("Couldn't open this schedule: " + e.message, true); go("#/schedules"); } return; }
  if (token !== S.token) return;
  if (S.range.id !== id) S.range = { id, from: "", to: "" };
  const home = pagesOf(s).includes("home");
  const settings = kvHtml([
    ["Runs", `every ${fmtInterval(s.interval_minutes)}${s.enabled ? "" : " (disabled)"}`], ["Started", s.start_at ? esc(fmtWhen(s.start_at)) : "when it was saved"],
    ["Next run", s.enabled && s.next_run_at ? esc(fmtWhen(s.next_run_at)) : "None"], ["Last run", s.last_run_at ? esc(fmtWhen(s.last_run_at)) : "never"],
    ["Last result", s.last_status ? `${esc(pretty(s.last_status))} ${esc(s.last_message || "")}` : "None"],
    ["Pages", tagsHtml(pagesOf(s).map(pageLabel), 9)], ...(home ? [["l1", tagsHtml(s.l1s, 9)], ["l2", tagsHtml(s.l2s, 9)]] : []),
    ["State", tagsHtml(s.states.map(stateName), 6)], ["Pincode", tagsHtml(s.pincodes, 6)], ["Runs per fire", s.combo_count === 1 ? "1" : `${s.combo_count}, one after another`],
    ["Scope", `${esc(s.scope)}${s.banner_limit ? ` (first ${s.banner_limit})` : ""}, ${s.workers} at a time`], ["Alerts", esc(alertSummary(s))],
    ["Carousels left out", (s.excluded_sections || []).length ? tagsHtml(s.excluded_sections.map((x) => x.label || x.id.slice(0, 10) + "..."), 20) : "none"]]);
  view(`<div class="page"><a class="back" href="#/schedules">&larr; All schedules</a>
    <div class="page-head"><h2>${esc(s.name)}</h2><span class="sub">${s.enabled ? "Runs" : "Disabled, would run"} every ${fmtInterval(s.interval_minutes)}${s.start_at ? `, started ${esc(fmtWhen(s.start_at))}` : ""}</span>
      <span class="acts">${schedActions(s).filter(([k]) => k !== "view").map(([k, l]) => `<button class="ghost ${k === "delete" ? "danger" : ""}" type="button" data-sd="${k}">${l}</button>`).join("")}</span></div>
    <div class="sum">${s.enabled ? `<span>Next run <b>${s.next_run_at ? esc(fmtWhen(s.next_run_at)) : "unknown"}</b></span>` : ""}<span><b>${s.combo_count}</b> run${s.combo_count === 1 ? "" : "s"} per fire</span>${s.last_status ? `<span>${esc(pretty(s.last_status))} ${esc(s.last_message || "")}</span>` : ""}</div>
    <div class="sect"><h3>Runs <span id="sd-count" class="hint"></span><span class="right hint">Click a run to see its banners</span></h3>
      <div class="range"><label for="sd-from">From</label><input type="date" id="sd-from"><label for="sd-to">to</label><input type="date" id="sd-to"><button class="ghost sm" type="button" id="sd-clear" hidden>Clear dates</button></div>
      <div id="sd-runs"></div></div>
    <details class="fold"><summary>Settings</summary>${settings}</details>
    <details class="fold" ${s.unread_alerts ? "open" : ""}><summary>Alerts (${s.alerts.length}${s.unread_alerts ? `, ${s.unread_alerts} unread` : ""})</summary>
      ${s.alerts.length ? `<p><button class="ghost sm danger" type="button" id="al-clear-s">Clear these</button></p>${s.alerts.map(alertHtml).join("")}` : '<p class="hint">No alerts from this schedule.</p>'}</details></div>`);
  const from = $("sd-from"), to = $("sd-to"); from.value = S.range.from; to.value = S.range.to;
  const paintRuns = () => {
    const filtering = !!(S.range.from || S.range.to), shown = runs.filter((r) => runInRange(r, S.range.from, S.range.to));
    $("sd-count").textContent = filtering ? `(${shown.length} of ${runs.length})` : `(${runs.length})`;
    $("sd-clear").hidden = !filtering;
    $("sd-runs").innerHTML = !runs.length ? '<p class="hint">This schedule hasn\'t run yet. It will at its next run time, or use Run now.</p>' : !shown.length ? '<div class="empty">No runs started in this date range.</div>' : runCardsHtml(shown) + (runs.length >= RUNS_LOADED ? `<p class="hint">Only the latest ${RUNS_LOADED} runs are loaded.</p>` : "");
  };
  const onRange = () => { S.range = { id, from: from.value, to: to.value }; from.max = to.value || ""; to.min = from.value || ""; paintRuns(); };
  from.oninput = onRange; to.oninput = onRange;
  $("sd-clear").onclick = () => { from.value = ""; to.value = ""; onRange(); };
  onRange();
  const cs = $("al-clear-s");
  if (cs) cs.onclick = async () => { if (!ask(`Remove all alerts from "${s.name}"?`)) return; try { await api(`/api/alerts?schedule_id=${s.id}`, { method: "DELETE" }); await refreshSched(); viewSchedDetail(id); } catch (e) { toast(e.message, true); } };
  $("view").querySelectorAll("[data-sd]").forEach((b) => { b.onclick = () => schedAction(b.dataset.sd, s); });
  if (runs.some((r) => r.status === "running")) S.timer = setTimeout(function again() {
    if (token !== S.token || S.viewName !== "sched-detail") return;
    if (document.activeElement && document.activeElement.closest && document.activeElement.closest(".range")) { S.timer = setTimeout(again, 4000); return; }
    viewSchedDetail(id);
  }, 8000);
}
/* create or edit a schedule */
const pad2 = (n) => String(n).padStart(2, "0");
async function viewSchedForm(id) {
  S.viewName = "sched-form";
  const token = ++S.token; let s = null;
  if (id != null) { try { s = await api(`/api/schedules/${id}`); } catch (e) { toast("Couldn't open this schedule: " + e.message, true); go("#/schedules"); return; } if (token !== S.token) return; }
  const pre = id == null ? S.prefill : null; S.prefill = null;
  const v = s || { name: "", interval_minutes: 120, pages: pre?.pages || ["home"], l1s: pre?.l1s || [S.meta.l1_options[0]], l2s: pre?.l2s || [S.meta.l2_options[0]], states: pre?.states || [S.meta.default_state], pincodes: pre?.pincodes || [S.meta.default_pincode],
    scope: pre?.scope || "hero", banner_limit: pre?.banner_limit ?? null, workers: pre?.workers || 3, enabled: 1, notify_mode: "new_fails", notify_toast: true, excluded_sections: pre?.excluded || [] };
  const { n, unit } = intervalParts(v.interval_minutes);
  const excluded = new Set((v.excluded_sections || []).map((x) => x.id)), labels = new Map((v.excluded_sections || []).map((x) => [x.id, x.label])); let cars = null;
  view(`<div class="page"><a class="back" href="${s ? `#/schedules/${s.id}` : "#/schedules"}">&larr; ${s ? esc(s.name) : "All schedules"}</a>
    <div class="page-head"><h2>${s ? "Edit schedule" : "New schedule"}</h2></div>
    <div class="form">
      <div class="full"><label class="lab" for="sf-name">Name</label><input class="field" id="sf-name" placeholder="For example, hourly hero check"></div>
      <div><span class="lab">Runs every</span><div class="row-inline"><input class="field" type="number" id="sf-every" min="1" style="width:6em"><select class="field" id="sf-unit"><option value="1">minutes</option><option value="60">hours</option><option value="1440">days</option></select></div></div>
      <div class="full"><span class="lab">Start</span><div class="startrow"><input class="field" type="date" id="sf-date" aria-label="Start date" style="width:auto"><span class="hint">at</span><input class="field" type="text" id="sf-hh" inputmode="numeric" maxlength="2" placeholder="hh" aria-label="Start hour"><b>:</b><input class="field" type="text" id="sf-mm" inputmode="numeric" maxlength="2" placeholder="mm" aria-label="Start minutes"><select class="field" id="sf-ampm" aria-label="AM or PM" style="width:auto"><option>AM</option><option>PM</option></select><button class="ghost" type="button" id="sf-now">Now</button></div><p class="hint" id="sf-first"></p></div>
      <div class="full" id="sf-axes"></div>
      <div><label class="lab" for="sf-scope">Scope</label><select class="field" id="sf-scope">${S.meta.scopes.map((x) => `<option value="${esc(x)}" ${x === v.scope ? "selected" : ""}>${esc(x === "hero" ? "Hero carousels only" : "Every banner on the feed")}</option>`).join("")}</select></div>
      <div><label class="lab" for="sf-limit">Number of banners</label><input class="field" type="number" id="sf-limit" min="1" placeholder="All"></div>
      <div><label class="lab" for="sf-workers">Banners at a time</label><input class="field" type="number" id="sf-workers" min="1" max="${S.meta.max_workers}"></div>
      <div><label class="lab" for="sf-notify">Alert me on</label><select class="field" id="sf-notify">${S.meta.notify_modes.map((m) => `<option value="${m}" ${m === v.notify_mode ? "selected" : ""}>${esc(NOTIFY_LABEL[m] || m)}</option>`).join("")}</select></div>
      <div class="full"><label class="checkline"><input type="checkbox" id="sf-toast"> Also show a Windows notification</label><p class="hint">"New fails only" alerts when a banner fails now and didn't in the previous run of this schedule. "Any change" also covers new inconclusive banners, recoveries, and banners appearing or leaving. The first run, and the first run after editing, only set a baseline.</p></div>
      <div class="full"><label class="checkline"><input type="checkbox" id="sf-enabled"> Enabled (runs on its interval)</label></div>
      <div class="full"><span class="lab">Carousels to leave out</span><p class="hint">Saved by each carousel's own id, so an exclusion still applies when the feed's order shifts.</p><button class="ghost" type="button" id="sf-load-car">Load carousels</button><div id="sf-cars"></div></div>
    </div>
    <p class="row-inline" style="margin-top:18px"><button class="primary" type="button" id="sf-save">${s ? "Save changes" : "Create schedule"}</button><a class="ghost" href="${s ? `#/schedules/${s.id}` : "#/schedules"}">Cancel</a></p></div>`);
  $("sf-name").value = v.name; $("sf-every").value = n; $("sf-unit").value = unit; $("sf-limit").value = v.banner_limit || ""; $("sf-workers").value = v.workers;
  $("sf-toast").checked = !!v.notify_toast; $("sf-enabled").checked = !!v.enabled;
  const axes = makeAxes($("sf-axes"), { pages: pagesOf(v), l1s: v.l1s, l2s: v.l2s, states: v.states, pincodes: v.pincodes }, () => { cars = null; excluded.clear(); carouselChecklist($("sf-cars"), null, excluded, labels); });
  const fillStart = (d) => { $("sf-date").value = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; $("sf-hh").value = String(d.getHours() % 12 || 12); $("sf-mm").value = pad2(d.getMinutes()); $("sf-ampm").value = d.getHours() >= 12 ? "PM" : "AM"; };
  const readStart = () => {
    const ds = $("sf-date").value, hh = parseInt($("sf-hh").value, 10), mm = parseInt($("sf-mm").value, 10);
    if (!ds) return { error: "Pick a start date." }; if (!(hh >= 1 && hh <= 12)) return { error: "The hour must be 1 to 12." }; if (!(mm >= 0 && mm <= 59)) return { error: "The minutes must be 0 to 59." };
    const [y, m, d] = ds.split("-").map(Number); return { date: new Date(y, m - 1, d, (hh % 12) + ($("sf-ampm").value === "PM" ? 12 : 0), mm) };
  };
  const firstRun = () => {
    const st = readStart(), every = parseInt($("sf-every").value, 10) * parseInt($("sf-unit").value, 10), el = $("sf-first");
    if (st.error || !(every >= 1)) { el.textContent = ""; return; }
    const start = st.date.getTime(), now = Date.now(), step = every * 60000, first = start > now ? start : start + Math.ceil((now - start) / step) * step;
    el.textContent = `First run: ${fmtWhen(new Date(first).toISOString())}` + (start > now ? "" : `. The start time has passed, so this is the next slot after it, counting every ${fmtInterval(every)} from the start.`);
  };
  fillStart(s && s.start_at ? new Date(s.start_at) : new Date());
  for (const k of ["sf-date", "sf-hh", "sf-mm", "sf-ampm", "sf-every", "sf-unit"]) $(k).addEventListener("input", firstRun);
  for (const k of ["sf-hh", "sf-mm"]) { $(k).addEventListener("input", () => { $(k).value = $(k).value.replace(/\D/g, ""); }); $(k).addEventListener("blur", () => { if ($(k).value !== "") $(k).value = k === "sf-mm" ? pad2($(k).value) : String(parseInt($(k).value, 10)); }); }
  $("sf-now").onclick = () => { fillStart(new Date()); firstRun(); };
  firstRun();
  carouselChecklist($("sf-cars"), null, excluded, labels);
  $("sf-load-car").onclick = async () => {
    const btn = $("sf-load-car"); btn.disabled = true; btn.textContent = "Loading";
    try { cars = await loadCarouselList(axes, $("sf-scope").value); for (const c of cars) if (excluded.has(c.id)) labels.set(c.id, c.label); carouselChecklist($("sf-cars"), cars, excluded, labels); }
    catch (e) { toast("Couldn't load the carousels: " + e.message, true); } finally { btn.disabled = false; btn.textContent = "Load carousels"; }
  };
  $("sf-save").onclick = async () => {
    const every = parseInt($("sf-every").value, 10); if (!(every >= 1)) { toast("Enter how often it should run (1 or more).", true); return; }
    const st = readStart(); if (st.error) { toast(st.error, true); return; }
    const problem = axes.problem(); if (problem) { toast(problem, true); return; }
    if (cars && cars.length && cars.every((c) => excluded.has(c.id))) { toast("Every carousel is left out, so this schedule would have nothing to check. Tick at least one.", true); return; }
    if (axes.over()) { toast(`That is ${axes.count()} runs per fire; the limit is ${S.meta.max_combos}. Narrow the selection.`, true); return; }
    const lim = $("sf-limit").value;
    const body = { name: $("sf-name").value.trim() || "unnamed schedule", interval_minutes: every * parseInt($("sf-unit").value, 10), start_at: st.date.toISOString(), ...axes.read(), scope: $("sf-scope").value,
      banner_limit: lim ? parseInt(lim, 10) : null, workers: Math.max(1, Math.min(S.meta.max_workers, parseInt($("sf-workers").value || "3", 10))), enabled: $("sf-enabled").checked,
      notify_mode: $("sf-notify").value, notify_toast: $("sf-toast").checked, excluded_sections: [...excluded].map((sid) => ({ id: sid, label: labels.get(sid) || "" })) };
    try { const saved = await post(s ? `/api/schedules/${s.id}` : "/api/schedules", body, s ? "PATCH" : "POST"); await refreshSched(); go(`#/schedules/${saved.id}`); }
    catch (e) { toast("Couldn't save the schedule: " + e.message, true); }
  };
}

/* =====================================================================
   Wiring
   ===================================================================== */
document.addEventListener("click", (e) => {
  const t = e.target;
  // runs rail
  const hide = t.closest("[data-hide]");
  if (hide) { e.stopPropagation(); const id = hide.dataset.hide; api(`/api/runs/${encodeURIComponent(id)}/hide`, { method: "POST" }).then(async () => { await loadRuns(); if (S.run && S.run.run_id === id) go("#/runs"); }).catch(fail("Couldn't remove it")); return; }
  const railRun = t.closest(".run[data-run]");
  if (railRun) { go(`#/runs/${encodeURIComponent(railRun.dataset.run)}`); return; }
  // run cards and excel buttons
  const xl = t.closest("[data-xlsx]");
  if (xl) { e.stopPropagation(); download(xl.dataset.xlsx); return; }
  const del = t.closest("[data-delrun]");
  if (del) { e.stopPropagation(); deleteRun(del.dataset.delrun); return; }
  const rc = t.closest(".card[data-run]");
  if (rc) { go(`#/runs/${encodeURIComponent(rc.dataset.run)}`); return; }
  // schedule cards
  const sc = t.closest(".card[data-sched]");
  if (sc) {
    const s = S.scheds.find((x) => x.id === Number(sc.dataset.sched)), act = t.closest("[data-act]");
    if (act) { e.stopPropagation(); if (s) schedAction(act.dataset.act, s); } else go(`#/schedules/${sc.dataset.sched}`);
    return;
  }
  const sf = t.closest("[data-sfilter]"); if (sf) { S.schedFilter = sf.dataset.sfilter; paintSchedList(); return; }
  // alerts
  const al = t.closest("[data-del-alert],[data-read]"); if (al) { alertAction(al); return; }
  const open = t.closest("[data-open-run]"); if (open) { go(`#/runs/${encodeURIComponent(open.dataset.openRun)}`); return; }
  if (t.closest("#al-readall")) { post("/api/alerts/read-all").then(refreshSched).catch(fail("Couldn't mark them read")); return; }
  if (t.closest("#al-clear")) { if (ask("Remove every alert from every schedule? This can't be undone.")) api("/api/alerts", { method: "DELETE" }).then(refreshSched).catch(fail("Couldn't clear")); return; }
  if (t.closest("#al-test")) { post("/api/notify/test").then(async (r) => { await refreshSched(); toast(r.delivery === "ok" ? "Test notification sent. You should see a Windows notification now." : `The test alert was recorded, but the Windows notification didn't show: ${r.delivery}`, r.delivery !== "ok"); }).catch(fail("Couldn't send it")); return; }
  // run view
  const seg = t.closest(".seg");
  if (seg) { S.filter.has(seg.dataset.v) ? S.filter.delete(seg.dataset.v) : S.filter.add(seg.dataset.v); paintVerdict(false); paintSheet(); $("clear-filters").hidden = !(S.filter.size || S.carousel); return; }
  const frame = t.closest(".frame[data-b]");
  if (frame) {
    const id = frame.dataset.b, act = t.closest("[data-act]")?.dataset.act;
    if (act === "retry") retryBanner(id); else if (act === "skip") toggleSkip(id); else if (act === "open") openDrawer(id);
    return;
  }
  if (t.closest("#stop")) { if (ask("Stop this run? What has been checked stays as it is.")) post(`/api/runs/${encodeURIComponent(S.run.run_id)}/cancel`).then(async () => { await loadRuns(); toast("Stopping the run."); }).catch(fail("Couldn't stop it")); return; }
  if (t.closest("#xlsx")) { const id = S.run.run_id; download(id); api(`/api/runs/${encodeURIComponent(id)}/open-folder`, { method: "POST" }).catch(() => {}); return; }
  if (t.closest("#hide-run")) { const id = S.run.run_id; api(`/api/runs/${encodeURIComponent(id)}/hide`, { method: "POST" }).then(async () => { await loadRuns(); go("#/runs"); }).catch(fail("Couldn't remove it")); return; }
  if (t.closest("#clear-filters")) { S.filter = new Set(); S.carousel = ""; $("carousel").value = ""; paintVerdict(false); paintSheet(); $("clear-filters").hidden = true; return; }
  // drawer
  const da = t.closest("#drawer [data-act]");
  if (da) {
    const a = da.dataset.act;
    if (a === "close") closeDrawer(); else if (a === "prev") stepDrawer(-1); else if (a === "next") stepDrawer(1);
    else if (a === "retry-d") { const id = S.openId; closeDrawer(); retryBanner(id); } else if (a === "skip-d") toggleSkip(S.openId);
    return;
  }
  if (t.closest("#scrim")) { closeDrawer(); return; }
  if (t.closest("#rail-toggle")) { const r = $("rail"); r.dataset.open = r.dataset.open === "true" ? "false" : "true"; return; }
  if (t.closest("#new-check")) { openDlg(); return; }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "carousel") { S.carousel = e.target.value; paintSheet(); $("clear-filters").hidden = !(S.filter.size || S.carousel); }
  else if (e.target.id === "q") { S.q = e.target.value.trim().toLowerCase(); paintSheet(); }
  else if (e.target.id === "show-hidden") { S.showHidden = e.target.checked; paintVerdict(false); paintSheet(); paintRunNote(); }
  else if (e.target.id === "sched-q") { S.schedQuery = e.target.value; paintSchedList(); }
});
document.addEventListener("keydown", (e) => {
  const card = e.target.closest && e.target.closest(".card.click,.run[data-run]");
  if (card && e.target === card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); card.click(); return; }
  if ($("dlg").open) return;
  if ($("drawer").dataset.open === "true") {
    if (e.key === "Escape") closeDrawer(); else if (e.key === "ArrowLeft" && !/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) stepDrawer(-1); else if (e.key === "ArrowRight" && !/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) stepDrawer(1);
  }
  if (e.key === "Escape" && e.target.id === "sched-q" && e.target.value) { e.target.value = ""; S.schedQuery = ""; paintSchedList(); }
});
$("dlg-form").addEventListener("submit", dlgStart);
$("dlg-close").addEventListener("click", () => $("dlg").close());
$("d-load-car").addEventListener("click", dlgLoadCars);
$("d-scope").addEventListener("change", () => { S.dlgCars = null; S.dlgExcluded.clear(); carouselChecklist($("d-cars"), null, S.dlgExcluded, D.labels); });
$("clear-cache").addEventListener("click", async () => {
  if (!ask("Clear the saved verdicts? Banners seen before will be checked fresh the next time.")) return;
  try { const { cleared } = await post("/api/cache/clear"); toast(cleared ? `Cleared ${plural(cleared, "saved verdict")}.` : "The cache was already empty."); } catch (e) { toast("Couldn't clear the cache: " + e.message, true); }
});
$("d-repeat").addEventListener("click", () => {
  const p = dlgSettings(); if (S.dlgAxes.problem()) { dlgMsg(S.dlgAxes.problem(), true); return; }
  S.prefill = { ...p, excluded: [...S.dlgExcluded].map((id) => ({ id, label: D.labels.get(id) || "" })) };
  $("dlg").close(); go("#/schedules/new");
});
$("theme-toggle").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"), next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next; try { localStorage.setItem("theme", next); } catch (e) { /* not saved */ }
});
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* no storage */ }
window.addEventListener("hashchange", route);
if (API) $("link-classic").href = API + "/";

(async function init() {
  try {
    S.meta = await api("/api/meta");
    await Promise.all([loadRuns(), refreshSched()]);
    if (!location.hash) history.replaceState(null, "", "#/runs");
    route();
  } catch (e) {
    view(`<div class="page"><div class="empty"><b>Can't reach the server</b>${esc(e.message)}. Start it with scripts\\start_server.ps1, then reload this page.</div></div>`);
  }
  setInterval(() => { if (!document.hidden) loadRuns().catch(() => {}); }, 15000);
  setInterval(() => { if (!document.hidden) refreshSched().catch(() => {}); }, 30000);
})();
