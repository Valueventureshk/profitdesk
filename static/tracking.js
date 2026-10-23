/* EcomVital Apps → Tracking: the wetracked.io report, same layout and behaviour (pinned header and
   first column, sort on every column, row checkboxes, column builder, rows per page), plus Meta spend,
   cost per purchase and ROAS. Every figure comes from the server (tracking.py). */

const $ = (id) => document.getElementById(id);
let stores = [], data = null, level = PDUrl.get("level") || "campaign", page = 0, sort = { k: null, dir: -1 };
let picked = new Set();
const KEY = { utm_id: "a_id", utm_source: "a_source", utm_medium: "a_medium", utm_campaign: "a_campaign", utm_content: "a_content", utm_term: "a_term" };
const LEVEL_KEY = { campaign: "utm_campaign", adset: "utm_term", ad: "utm_content" };
const PLURAL = { campaign: "campaigns", adset: "adsets", ad: "ads" };
const RANGES = [["today", "Today"], ["yesterday", "Yesterday"], ["7", "Last 7 days"], ["14", "Last 14 days"], ["30", "Last 30 days"],
                ["mtd", "This month"], ["lastmonth", "Last month"], ["custom", "Custom range"]];
// every metric the column builder offers; count metrics show count + conversion value (two columns)
const METRICS = [
  { id: "atc", label: "Total adds to cart", sub: "Count + conversion value", cols: [["atc", "Total adds to cart", "n"], ["atc_value", "Total adds to cart conversion value", "m"]] },
  { id: "ic", label: "Total checkouts initiated", sub: "Count + conversion value", cols: [["ic", "Total checkouts initiated", "n"], ["ic_value", "Total checkouts initiated conversion value", "m"]] },
  { id: "purchase", label: "Total purchases", sub: "Count + conversion value", cols: [["purchase", "Total purchases", "n"], ["purchase_value", "Total purchases conversion value", "m"]] },
  { id: "spend", label: "Meta spend", sub: "From Meta Ads", cols: [["spend", "Meta spend", "m"]] },
  { id: "cpa", label: "Cost per purchase", sub: "Spend ÷ purchases", cols: [["cpa", "Cost per purchase", "m"]] },
  { id: "roas", label: "ROAS", sub: "Purchase value ÷ spend", cols: [["roas", "ROAS", "r"]] },
  { id: "impressions", label: "Impressions", sub: "From Meta Ads", cols: [["impressions", "Impressions", "n"]] },
  { id: "link_clicks", label: "Link clicks", sub: "From Meta Ads", cols: [["link_clicks", "Link clicks", "n"]] },
  { id: "cpc", label: "Cost per link click", sub: "Spend ÷ link clicks", cols: [["cpc", "Cost per link click", "m"]] },
];
const SPEND_IDS = ["spend", "cpa", "roas", "impressions", "link_clicks", "cpc"];
const DEFAULT_COLS = [["spend", 1], ["atc", 1], ["ic", 1], ["purchase", 1], ["cpa", 1], ["roas", 1], ["impressions", 0], ["link_clicks", 0], ["cpc", 0]];
let colState = (() => {
  try {
    const v = JSON.parse(localStorage.getItem("pdTrkCols2"));
    if (Array.isArray(v) && v.length) {
      const known = v.filter(([id]) => METRICS.some((m) => m.id === id));
      for (const d of DEFAULT_COLS) if (!known.some(([id]) => id === d[0])) known.push([d[0], 0]);
      return known;
    }
  } catch { /* none saved */ }
  return DEFAULT_COLS.map((x) => [...x]);
})();
const state = { store: PDUrl.get("store") || "", range: PDUrl.get("range") || "today", key: PDUrl.get("key") || "", cur: PDUrl.get("cur") || "", hours: PDUrl.get("hours") || (PDUrl.get("hour") ? `${PDUrl.get("hour")}-${Number(PDUrl.get("hour")) + 1}` : "") };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, cur) {
  if (v === null || v === undefined || v === 0) return "<em>—</em>";
  try { return esc(new Intl.NumberFormat(undefined, { style: "currency", currency: cur || data?.currency || "USD" }).format(v)); }
  catch { return Number(v).toFixed(2); }
}
const fmt = (kind, v) => (kind === "m" ? money(v) : kind === "r" ? (v ? Number(v).toFixed(2) : "<em>—</em>") : (v ? Number(v).toLocaleString() : "<em>—</em>"));
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/tracking"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || (typeof body.detail === "string" ? body.detail : "") || `Request failed (${r.status})`);
  return body;
}
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 7000 : 3000);
}
const store = () => stores.find((s) => String(s.id) === String(state.store));

// the store's own today (its clock), like Shopify and wetracked.io
function storeToday() {
  try { return new Intl.DateTimeFormat("en-CA", { timeZone: store()?.timezone || undefined }).format(new Date()); }
  catch { return new Date().toISOString().slice(0, 10); }
}
function range() {
  const t = new Date(storeToday() + "T12:00:00Z"), iso = (d) => d.toISOString().slice(0, 10);
  const back = (n) => { const d = new Date(t); d.setUTCDate(d.getUTCDate() - n); return iso(d); };
  switch (state.range) {
    case "today": return [iso(t), iso(t)];
    case "yesterday": return [back(1), back(1)];
    case "mtd": return [iso(t).slice(0, 8) + "01", iso(t)];
    case "lastmonth": { const a = new Date(t); a.setUTCDate(0); const e = iso(a); a.setUTCDate(1); return [iso(a), e]; }
    case "custom": return [$("from").value || iso(t), $("to").value || iso(t)];
    default: return [back(Number(state.range) - 1), iso(t)];
  }
}

// one hour of one day (store clock): "13:00–14:00"; today's current hour is "so far", later hours aren't offered
const pad = (h) => String(h).padStart(2, "0");
const slot = (h) => `${pad(h)}:00–${pad((h + 1) % 24)}:00`;
function oneDay() { const [a, b] = range(); return a === b; }
function storeHour() {
  try { return Number(new Intl.DateTimeFormat("en-GB", { timeZone: store()?.timezone || undefined, hour: "2-digit", hourCycle: "h23" }).format(new Date())); }
  catch { return new Date().getHours(); }
}
// state.hours = "from-to" on the store's clock (to is exclusive, 1..24), "" = all day
const hrs = () => (state.hours ? state.hours.split("-").map(Number) : null);
const fmtH = (h) => `${pad(h % 24)}:00`;
function hoursName() {
  const h = hrs();
  if (!h) return "All day";
  const sofar = range()[0] === storeToday() && h[1] > storeHour();
  return `${fmtH(h[0])}–${fmtH(h[1])}` + (sofar ? " · so far" : "");
}
function hourMenu() {
  const box = $("hourMenu");
  const open = box.hidden;
  for (const m of document.querySelectorAll(".trk-menu")) m.hidden = true;
  if (!open) return;
  const today = range()[0] === storeToday(), now = storeHour();
  const lastTo = today ? now + 1 : 24;                       // today: up to the current hour (so far)
  const h = hrs() || [0, lastTo];
  const opt = (v, cur, label) => `<option value="${v}"${v === cur ? " selected" : ""}>${label}</option>`;
  const froms = [], tos = [];
  for (let i = 0; i < lastTo; i++) froms.push(opt(i, h[0], fmtH(i)));
  for (let i = 1; i <= lastTo; i++) tos.push(opt(i, Math.min(h[1], lastTo), i === 24 ? "24:00" : fmtH(i) + (today && i === lastTo ? " (now)" : "")));
  const singles = [];
  for (let i = 0; i < lastTo; i++) {
    const v = `${i}-${i + 1}`;
    singles.push(`<button data-v="${v}" class="${v === state.hours ? "on" : ""}">${slot(i)}${today && i === now ? " · so far" : ""}</button>`);
  }
  box.innerHTML = `<button data-v="" class="${state.hours ? "" : "on"}">All day</button>
    <div class="trk-hrange"><span>From</span><select id="hFrom" class="control">${froms.join("")}</select>
      <span>to</span><select id="hTo" class="control">${tos.join("")}</select>
      <button id="hGo" class="trk-hgo">Show</button></div>
    <div class="trk-hsep">Or one hour</div>${singles.join("")}`;
  box.hidden = false;
  for (const b of box.querySelectorAll("button[data-v]")) b.onclick = () => { box.hidden = true; state.hours = b.dataset.v; load(); };
  $("hGo").onclick = () => {
    const a = Number($("hFrom").value), b = Number($("hTo").value);
    if (b <= a) { toast("The end time must be after the start time.", true); return; }
    box.hidden = true;
    state.hours = a === 0 && b === 24 ? "" : `${a}-${b}`;
    load();
  };
}
const hourQ = () => (state.hours && oneDay() ? `&hours=${state.hours}` : "");

function menu(box, items, current, onPick) {
  const open = box.hidden;
  for (const m of document.querySelectorAll(".trk-menu")) m.hidden = true;
  if (!open) return;
  box.innerHTML = items.map(([v, l]) => `<button data-v="${esc(v)}" class="${String(v) === String(current) ? "on" : ""}">${esc(l)}</button>`).join("");
  box.hidden = false;
  for (const b of box.querySelectorAll("button")) b.onclick = () => { box.hidden = true; onPick(b.dataset.v); };
}
document.addEventListener("click", (e) => {
  for (const [m, b] of [["storeMenu", "storeBtn"], ["rangeMenu", "rangeBtn"], ["hourMenu", "hourBtn"]]) {
    if (!$(m).hidden && !$(m).contains(e.target) && !$(b).contains(e.target)) $(m).hidden = true;
  }
});

async function boot() {
  try { stores = (await api("/api/tracking/stores")).stores; } catch (e) { toast(e.message, true); return; }
  if (!store()) state.store = String((stores.find((s) => s.installed) || stores[0] || {}).id || "");
  try { if (localStorage.getItem("pdTrkNote") === "off") $("note").hidden = true; } catch { /* private */ }
  if (!stores.length) { toast("No Shopify stores connected yet.", true); return; }
  load();
}

function syncControls() {
  const st = store();
  $("storeName").textContent = st ? st.name : "Store";
  $("rangeName").textContent = (RANGES.find((r) => r[0] === state.range) || [, "Today"])[1];
  $("dates").hidden = state.range !== "custom";
  $("hourBox").hidden = !oneDay();
  if (!oneDay()) state.hours = "";
  if (state.hours && range()[0] === storeToday() && hrs()[0] > storeHour()) state.hours = "";
  $("hourName").textContent = hoursName();
  for (const r of document.querySelectorAll("#level input")) r.checked = r.value === level;
  $("key").value = state.key || LEVEL_KEY[level];
  $("keyName").textContent = $("key").selectedOptions[0]?.textContent || "";
  $("q").placeholder = `Search ${PLURAL[level]}..`;
  $("rowsName").textContent = $("rows").value;
}

async function load() {
  syncControls();
  const [start, end] = range();
  const key = state.key || LEVEL_KEY[level];
  PDUrl.set({ store: state.store, range: state.range === "today" ? null : state.range, level: level === "campaign" ? null : level,
              key: state.key || null, cur: state.cur || null, hours: state.hours || null, hour: null });
  $("reload").disabled = true;
  try {
    data = await api(`/api/tracking/report?store=${state.store}&start=${start}&end=${end}&field=${KEY[key]}&currency=${encodeURIComponent(state.cur)}${hourQ()}`);
  } catch (e) { toast(e.message, true); $("reload").disabled = false; return; }
  $("reload").disabled = false;
  const curs = data.currencies || [data.currency];
  $("cur").innerHTML = curs.map((c) => `<option${c === data.currency ? " selected" : ""}>${esc(c)}</option>`).join("");
  $("curName").textContent = data.currency;
  $("spendNote").textContent = data.spend_note || "";
  const h = data.health || {};
  $("health").innerHTML = h.last ? `<span class="trk-dot ${h.hour ? "on" : ""}"></span>${h.hour ? `${h.hour} events in the last hour` : "No events in the last hour"}`
    : `<span class="trk-dot"></span>No events from this store yet`;
  picked = new Set();
  page = 0;
  draw();
}

function columns() {
  const out = [];
  for (const [id, on] of colState) {
    const m = METRICS.find((x) => x.id === id);
    if (!m || !on || (SPEND_IDS.includes(id) && !data.has_spend)) continue;
    out.push(...m.cols);
  }
  return out;
}

const SORT_IC = `<svg viewBox="0 0 16 16" aria-hidden="true"><path class="up" d="M5 6.5 8 3.5l3 3"/><path class="dn" d="M5 9.5 8 12.5l3-3"/></svg>`;
// column widths; phones get a narrower pinned name column so the figures stay in view
const widths = () => (innerWidth < 640 ? [170, 150] : [300, 196]);

function draw() {
  const cols = columns();
  const q = $("q").value.trim().toLowerCase();
  let rows = data.rows.filter((r) => !q || String(r.name).toLowerCase().includes(q));
  if (sort.k) {
    rows = rows.slice().sort((a, b) => (sort.k === "name" ? String(a.name).localeCompare(String(b.name)) * -sort.dir
      : ((a[sort.k] ?? -Infinity) - (b[sort.k] ?? -Infinity)) * sort.dir));
  }
  const per = Number($("rows").value), pages = Math.max(1, Math.ceil(rows.length / per));
  page = Math.min(page, pages - 1);
  const shown = rows.slice(page * per, page * per + per);
  const [W_FIRST, W_COL] = widths();
  const width = W_FIRST + cols.length * W_COL;
  const colgroup = `<colgroup><col style="width:${W_FIRST}px">${cols.map(() => `<col style="width:${W_COL}px">`).join("")}</colgroup>`;
  const label = { campaign: "Campaign", adset: "Adset", ad: "Ad" }[level];
  const allOn = shown.length > 0 && shown.every((r) => picked.has(r.name));
  const sortBtn = (k) => `<button class="trk-sortb" data-sort="${k}" data-dir="${sort.k === k ? (sort.dir < 0 ? "desc" : "asc") : ""}" title="Sort">${SORT_IC}</button>`;
  for (const t of ["tHead", "tBody", "tFoot"]) $(t).style.width = width + "px";

  $("tHead").innerHTML = `${colgroup}<thead><tr><th class="trk-pin"><span class="trk-cell"><label class="trk-cb"><input type="checkbox" id="cbAll"${allOn ? " checked" : ""}><i></i></label>${label}</span>${sortBtn("name")}</th>
    ${cols.map(([k, l]) => `<th><span class="trk-cell" title="${esc(l)}">${esc(l)}</span>${sortBtn(k)}</th>`).join("")}</tr></thead>`;

  $("tBody").innerHTML = `${colgroup}<tbody>${shown.map((r) => `<tr data-name="${esc(r.name)}"${picked.has(r.name) ? ' class="on"' : ""}>
      <th class="trk-pin"><span class="trk-cell"><label class="trk-cb"><input type="checkbox" data-pick${picked.has(r.name) ? " checked" : ""}><i></i></label>
      <span class="trk-name" title="${r.name === "N/A" ? "Shoppers who didn't come from an ad link: direct visits, Google search, social bios, your own team" : esc(r.name)} · click to see each event">${r.name === "N/A" ? "No ad (direct / search / social)" : esc(r.name)}</span></span></th>
      ${cols.map(([k, , kind]) => `<td><span class="trk-cell">${fmt(kind, r[k])}</span></td>`).join("")}</tr>`).join("")
    || `<tr><td class="trk-empty" colspan="${cols.length + 1}">${data.health?.last ? "No results in this period." : "No events from this store yet. Switch the pixel on in Setup."}</td></tr>`}</tbody>`;

  // totals row: the ticked rows when any are ticked, otherwise every row (server totals)
  const sel = rows.filter((r) => picked.has(r.name));
  const tot = sel.length ? sumRows(sel) : data.total;
  $("tFoot").innerHTML = `${colgroup}<tbody><tr><th class="trk-pin"><span class="trk-cell">${sel.length ? `Results from ${sel.length} selected` : `Results from ${rows.length} ${PLURAL[level]}`}</span></th>
    ${cols.map(([k, , kind]) => `<td><span class="trk-cell">${fmt(kind, tot[k])}</span></td>`).join("")}</tr></tbody>`;

  $("barXIn").style.width = width + "px";
  $("pager").innerHTML = `<button class="trk-btn trk-sm" id="prev"${page ? "" : " disabled"} title="Previous page">‹</button>
    <span>Showing <b>${rows.length ? page * per + 1 : 0}-${Math.min(rows.length, page * per + per)}</b> of ${rows.length}</span>
    <button class="trk-btn trk-sm" id="next"${page < pages - 1 ? "" : " disabled"} title="Next page">›</button>`;
  $("prev").onclick = () => { page--; draw(); };
  $("next").onclick = () => { page++; draw(); };
  for (const b of document.querySelectorAll("#tHead [data-sort]")) b.onclick = () => {
    const k = b.dataset.sort;
    sort = sort.k !== k ? { k, dir: -1 } : sort.dir < 0 ? { k, dir: 1 } : { k: null, dir: -1 };
    draw();
  };
  $("cbAll").onchange = () => { for (const r of shown) $("cbAll").checked ? picked.add(r.name) : picked.delete(r.name); draw(); };
  for (const cb of document.querySelectorAll("#tBody [data-pick]")) {
    cb.onchange = () => { const n = cb.closest("tr").dataset.name; cb.checked ? picked.add(n) : picked.delete(n); draw(); };
  }
  for (const s of document.querySelectorAll("#tBody .trk-name")) s.onclick = () => openEvents(s.closest("tr").dataset.name);
  scrollSync();
}

// totals of the ticked rows: plain sums of the server's per-row figures, ratios from those sums
function sumRows(rows) {
  const t = {};
  for (const k of ["atc", "atc_value", "ic", "ic_value", "purchase", "purchase_value", "spend", "impressions", "link_clicks"]) t[k] = rows.reduce((a, r) => a + (r[k] || 0), 0);
  t.cpa = t.spend && t.purchase ? t.spend / t.purchase : null;
  t.roas = t.spend ? t.purchase_value / t.spend : null;
  t.cpc = t.spend && t.link_clicks ? t.spend / t.link_clicks : null;
  return t;
}

// one sideways position for header, body, totals and the bottom scrollbar (like wetracked.io)
let syncing = false;
function scrollSync() {
  const parts = [$("sxHead"), $("sxBody"), $("sxFoot"), $("barX")];
  for (const p of parts) p.onscroll = () => {
    if (syncing) return;
    syncing = true;
    for (const o of parts) if (o !== p && o.scrollLeft !== p.scrollLeft) o.scrollLeft = p.scrollLeft;
    syncing = false;
    edge();
  };
  edge();
}
function edge() {
  const sx = $("sxBody"), x = sx.scrollLeft;
  $("grid").classList.toggle("scrolled", x > 2);
  $("grid").classList.toggle("more", sx.scrollWidth - sx.clientWidth - x > 2);
  $("barX").hidden = sx.scrollWidth <= sx.clientWidth + 1;
}
let lastW = innerWidth;
window.addEventListener("resize", () => { if (!data) return; if ((innerWidth < 640) !== (lastW < 640)) draw(); else edge(); lastW = innerWidth; });

/* ------------------------------------------------ column builder */
let draft = null;
function openCols() {
  draft = colState.map((x) => [...x]);
  drawCols();
  $("colsModal").hidden = false;
}
function drawCols() {
  const total = METRICS.reduce((a, m) => a + m.cols.length, 0);
  const on = draft.filter(([, v]) => v).reduce((a, [id]) => a + (METRICS.find((m) => m.id === id)?.cols.length || 0), 0);
  $("colsCount").textContent = `${on} of ${total} columns`;
  $("colsList").innerHTML = draft.map(([id, v], i) => {
    const m = METRICS.find((x) => x.id === id);
    const off = SPEND_IDS.includes(id) && data && !data.has_spend;
    return `<div class="trk-colrow${v ? " on" : ""}" data-i="${i}">
      <span class="trk-grip" title="Drag to move">⋮⋮</span>
      <span class="trk-coln"><b>${esc(m.label)}</b><small>${esc(m.sub)}${off ? " · needs Meta spend for this store" : ""}</small></span>
      <label class="trk-tog"><input type="checkbox" data-on="${i}"${v ? " checked" : ""}><i></i></label></div>`;
  }).join("");
  for (const g of $("colsList").querySelectorAll(".trk-grip")) g.onpointerdown = (e) => dragCol(e, g);
  for (const c of $("colsList").querySelectorAll("[data-on]")) c.onchange = () => { draft[+c.dataset.on][1] = c.checked ? 1 : 0; drawCols(); };
}
// drag a metric by its handle (mouse or finger); the list's new order becomes the draft
function dragCol(e, grip) {
  e.preventDefault();
  const list = $("colsList"), row = grip.closest(".trk-colrow");
  row.classList.add("drag");
  // listen on the window: moving the row in the list would drop a capture held by the handle
  const move = (ev) => {
    const box = list.getBoundingClientRect();
    if (ev.clientY < box.top + 24) list.scrollTop -= 8;
    else if (ev.clientY > box.bottom - 24) list.scrollTop += 8;
    const after = [...list.children].filter((r) => r !== row).find((r) => { const b = r.getBoundingClientRect(); return ev.clientY < b.top + b.height / 2; });
    if (after !== row.nextElementSibling) list.insertBefore(row, after || null);
  };
  const done = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", done);
    window.removeEventListener("pointercancel", done);
    draft = [...list.children].map((r) => draft[+r.dataset.i]);
    drawCols();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", done);
  window.addEventListener("pointercancel", done);
}
$("colsBtn").onclick = openCols;
$("colsX").onclick = $("colsCancel").onclick = () => { $("colsModal").hidden = true; };
$("colsReset").onclick = () => { draft = DEFAULT_COLS.map((x) => [...x]); drawCols(); };
$("colsApply").onclick = () => {
  colState = draft;
  try { localStorage.setItem("pdTrkCols2", JSON.stringify(colState)); } catch { /* private */ }
  $("colsModal").hidden = true;
  if (data) draw();
};

/* ------------------------------------------------ single events + journey */
function when(iso) {
  try { return new Date(iso).toLocaleString(undefined, { timeZone: store()?.timezone || undefined, day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }); }
  catch { return iso; }
}
const KIND = { "Add to cart": "catalog", "Checkout started": "changed", "Purchase": "invoice" };
async function openEvents(name) {
  const [start, end] = range();
  const key = state.key || LEVEL_KEY[level];
  $("evModal").hidden = false;
  $("evTitle").textContent = name;
  $("evSub").textContent = "Loading…";
  $("evTable").innerHTML = "";
  let d;
  try { d = await api(`/api/tracking/events?store=${state.store}&start=${start}&end=${end}&field=${KEY[key]}&value=${encodeURIComponent(name)}${hourQ()}`); }
  catch (e) { $("evSub").textContent = e.message; return; }
  const n = (k) => d.events.filter((e) => e.label === k).length;
  $("evSub").textContent = `${n("Add to cart")} adds to cart · ${n("Checkout started")} checkouts · ${n("Purchase")} purchases. Each one is credited to the ad click shown (last click within 7 days).`;
  const mny = (v) => (v ? money(v, data?.store_currency) : "—");
  $("evTable").innerHTML = `<thead><tr><th>When</th><th>Event</th><th>Value</th><th>Campaign</th><th>Ad set</th><th>Ad</th><th>Click</th><th>Page</th><th></th></tr></thead>
    <tbody>${d.events.map((e) => `<tr><td>${esc(when(e.at))}</td><td><span class="src ${KIND[e.label] || "new"}">${esc(e.label)}</span>${e.order_id ? `<span class="sub">order ${esc(e.order_id)}</span>` : ""}</td>
      <td>${mny(e.value)}</td><td title="${esc(e.a_campaign || "")}">${esc(e.a_campaign || "—")}</td><td title="${esc(e.a_term || "")}">${esc(e.a_term || "—")}</td>
      <td title="${esc(e.a_content || "")}">${esc(e.a_content || "—")}</td><td>${esc({ fbclid: "Meta", gclid: "Google", ttclid: "TikTok", msclkid: "Bing" }[e.a_click] || e.a_source || "—")}</td>
      <td class="trk-path" title="${esc(e.path)}">${esc(e.path)}</td><td><button class="btn btn-sm btn-ghost" data-cid="${esc(e.client_id)}">Journey</button></td></tr>`).join("")
      || `<tr><td class="trk-empty" colspan="9">No single events in this period.</td></tr>`}</tbody>`;
  for (const b of $("evTable").querySelectorAll("[data-cid]")) b.onclick = () => openJourney(b.dataset.cid, name);
}
async function openJourney(cid, back) {
  $("evSub").textContent = "Loading this shopper's visits…";
  let d;
  try { d = await api(`/api/tracking/journey?store=${state.store}&client=${encodeURIComponent(cid)}`); }
  catch (e) { $("evSub").textContent = e.message; return; }
  $("evTitle").textContent = "One shopper's journey";
  $("evSub").innerHTML = `${d.events.length} events, oldest first. <a href="#" id="evBack">← Back to ${esc(back)}</a>`;
  $("evTable").innerHTML = `<thead><tr><th>When</th><th>Event</th><th>Value</th><th>Credited to</th><th>Page</th></tr></thead><tbody>
    ${d.events.map((e) => `<tr><td>${esc(when(e.at))}</td><td><span class="src ${KIND[e.label] || "new"}">${esc(e.label)}</span></td><td>${e.value ? esc(String(e.value)) : "—"}</td>
      <td>${esc([e.a_campaign, e.a_term, e.a_content].filter(Boolean).join(" › ") || "—")}</td><td class="trk-path" title="${esc(e.path)}">${esc(e.path)}</td></tr>`).join("")}</tbody>`;
  $("evBack").onclick = (ev) => { ev.preventDefault(); openEvents(back); };
}

/* ------------------------------------------------ setup (pixel, Meta) */
function drawSetup() {
  $("setup").innerHTML = `<div class="panel-head"><h2>Tracking setup</h2><button class="btn btn-sm btn-ghost" id="setupClose">Close</button></div>
    <p class="hint trk-pad">The EcomVital pixel is a Shopify web pixel inside each store's EcomVital app (like the wetracked.io Connect app).
      Shopify only runs it for shoppers who allowed tracking in the store's cookie banner. <b>Watch only</b>: tracks and reports, sends nothing
      to Meta, so it can run next to wetracked.io without double counting.</p>
    <div class="table-wrap"><table class="table"><thead><tr><th>Store</th><th>Pixel</th><th>Last 24 h</th><th>Meta pixel ID</th><th>Conversions API token</th><th>Send to Meta</th></tr></thead><tbody>
    ${stores.map((s) => { const d = s.health.day || {}; return `<tr data-s="${s.id}"><td class="name">${esc(s.name)}<span class="sub">${esc(s.domain)}</span></td>
      <td>${s.installed ? `<span class="src invoice">On</span> <button class="btn btn-sm btn-ghost" data-install>Re-install</button>` : `<button class="btn btn-sm btn-primary" data-install>Switch on</button>`}</td>
      <td class="hint">${s.health.last ? `${d.page_viewed || 0} views · ${d.product_added_to_cart || 0} carts · ${d.checkout_started || 0} checkouts · ${d.checkout_completed || 0} orders` : "—"}</td>
      <td><input class="control trk-in" data-f="meta_pixel" value="${esc(s.cfg.meta_pixel)}" placeholder="Dataset ID"></td>
      <td><input class="control trk-in" data-f="meta_token" type="password" placeholder="${s.cfg.has_token ? "Saved · paste to replace" : "Paste token"}"></td>
      <td><button class="btn btn-sm btn-ghost" data-meta="${s.id}">Meta sending…</button></td></tr>`; }).join("")}</tbody></table></div>`;
  for (const b of $("setup").querySelectorAll("[data-meta]")) b.onclick = () => { mStore = b.dataset.meta; $("mpanel").hidden = false; drawMeta(); $("mpanel").scrollIntoView({ behavior: "smooth" }); };
  $("setupClose").onclick = () => { $("setup").hidden = true; };
  for (const b of $("setup").querySelectorAll("[data-install]")) b.onclick = async () => {
    b.disabled = true;
    try { await api(`/api/tracking/install/${b.closest("tr").dataset.s}`, { method: "POST" }); toast("Pixel switched on. Events start with the next visitors."); await refreshStores(); }
    catch (e) { toast(e.message, true); b.disabled = false; }
  };
  for (const el of $("setup").querySelectorAll("[data-f]")) el.onchange = async () => {
    try {
      await api(`/api/tracking/settings/${el.closest("tr").dataset.s}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [el.dataset.f]: el.value }) });
      toast("Saved.");
      if (el.dataset.f === "meta_token") el.value = "";
      await refreshStores();
    } catch (e) { toast(e.message, true); }
  };
}
async function refreshStores() {
  stores = (await api("/api/tracking/stores")).stores;
  if (!$("setup").hidden) drawSetup();
}

/* ------------------------------------------------ Google Ads conversion tracking (per Google store) */
let gStores = null, gStore = null;
async function drawGoogle() {
  const box = $("gpanel");
  if (!gStores) {
    box.innerHTML = `<p class="hint trk-pad">Loading…</p>`;
    try { gStores = (await api("/api/tracking/google/stores")).stores; } catch (e) { box.innerHTML = `<p class="hint trk-pad">${esc(e.message)}</p>`; return; }
    gStore = gStore || String((gStores[0] || {}).id || "");
  }
  box.innerHTML = `<div class="panel-head"><h2>Google Ads conversion tracking</h2>
      <span><select class="control" id="gStore">${gStores.map((s) => `<option value="${s.id}"${String(s.id) === gStore ? " selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
      <button class="btn btn-sm btn-ghost" id="gClose">Close</button></span></div>
    <p class="hint trk-pad">What each Google Ads account records. <b>Primary</b> = Google bids on it and shows it as Conversions; <b>secondary</b> = recorded only.
      A store needs exactly one working primary purchase action. Next step: EcomVital's own Purchase / Add to cart / Checkout sent from the pixel (needs the pixel on the store).</p>
    <div id="gPlan"></div><div id="gOurs"></div>
    <div id="gBody"><p class="hint trk-pad">Reading the account…</p></div>`;
  drawPlan();
  drawOurs();
  $("gClose").onclick = () => { $("gpanel").hidden = true; };
  $("gStore").onchange = () => { gStore = $("gStore").value; drawGoogle(); };
  let d;
  const mine = gStore;
  try { d = await api(`/api/tracking/google/actions?store=${gStore}`); }
  catch (e) { if (mine === gStore) $("gBody").innerHTML = `<p class="hint trk-pad">${esc(e.message)}</p>`; return; }
  if (mine !== gStore) return;                          // another store was picked meanwhile
  const acts = d.actions.filter((a) => a.status !== "HIDDEN" && (a.primary || ["PURCHASE", "ADD_TO_CART", "BEGIN_CHECKOUT"].includes(a.category)));
  const prim = acts.filter((a) => a.primary && a.category === "PURCHASE");
  const best = acts.filter((a) => a.category === "PURCHASE").sort((x, y) => y.conversions_30d - x.conversions_30d)[0];
  let advice = "";
  if (!prim.some((a) => a.conversions_30d > 0) && best && best.conversions_30d > 0)
    advice = `<div class="notice trk-pad">The primary purchase action recorded nothing in 30 days, but <b>${esc(best.name)}</b> recorded ${best.conversions_30d}.
      Make it primary (and the empty one secondary) so Google counts and bids on real purchases.
      <button class="btn btn-sm btn-primary" id="gFix" data-manage-only>Fix: make “${esc(best.name)}” primary</button></div>`;
  else if (prim.filter((a) => a.conversions_30d > 0).length > 1)
    advice = `<div class="notice trk-pad">More than one primary purchase action is recording: Google may count some orders twice.</div>`;
  $("gBody").innerHTML = `${advice}<div class="table-wrap"><table class="table"><thead><tr><th>Conversion action</th><th>Type</th><th>Used for bidding</th><th>Last 30 days</th><th>Value</th><th></th></tr></thead><tbody>
    ${acts.map((a) => `<tr><td class="name">${esc(a.name)}<span class="sub">${esc((a.type || "").replace(/_/g, " ").toLowerCase())}</span></td>
      <td>${esc((a.category || "").replace(/_/g, " ").toLowerCase())}</td>
      <td>${a.primary ? `<span class="src invoice">Primary</span>` : `<span class="src new">Secondary</span>`}</td>
      <td class="num">${a.conversions_30d}${!a.conversions_30d && a.primary ? ` <span class="src bad">nothing recorded</span>` : ""}</td>
      <td class="num">${a.value_30d ? a.value_30d.toLocaleString(undefined, { maximumFractionDigits: 0 }) : "—"}</td>
      <td data-manage-only><button class="btn btn-sm btn-ghost" data-flip="${esc(a.name)}" data-to="${a.primary ? "0" : "1"}">${a.primary ? "Make secondary" : "Make primary"}</button></td></tr>`).join("")}</tbody></table></div>`;
  const flip = async (primary, secondary) => {
    if (!confirm(`Change what Google Ads counts and bids on for ${gStores.find((s) => String(s.id) === gStore)?.name}?\n\nPrimary: ${primary.join(", ") || "—"}\nSecondary: ${secondary.join(", ") || "—"}`)) return;
    try { await api("/api/tracking/google/primary", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ store: gStore, primary, secondary }) });
          toast("Changed in Google Ads. New conversions follow the new setting."); drawGoogle(); }
    catch (e) { toast(e.message, true); }
  };
  for (const b of $("gBody").querySelectorAll("[data-flip]")) b.onclick = () => (b.dataset.to === "1" ? flip([b.dataset.flip], []) : flip([], [b.dataset.flip]));
  if ($("gFix")) $("gFix").onclick = () => flip([best.name], prim.filter((a) => a.name !== best.name).map((a) => a.name));
}

async function drawPlan() {
  let p;
  try { p = await api("/api/tracking/google/plan"); } catch (e) { $("gPlan").innerHTML = ""; return; }
  if (!p.steps.length) { $("gPlan").innerHTML = ""; return; }
  $("gPlan").innerHTML = `<div class="gpl"><b>Recommended now (TrackBee cancelled)</b>
      <ol>${p.steps.map((s) => `<li><b>${esc(s.name)}</b>: ${esc(s.text)}</li>`).join("")}</ol>
      <p class="hint">This changes which purchase action Google bids on in these accounts. ZAVA is left as it is.</p>
      <span data-manage-only><button class="btn btn-sm btn-primary" id="gPlanGo">Do it</button></span></div>`;
  $("gPlanGo").onclick = async () => {
    if (!confirm("Carry out these changes in Google Ads now?")) return;
    $("gPlanGo").disabled = true; $("gPlanGo").textContent = "Working…";
    try { const r = await api("/api/tracking/google/plan", { method: "POST" });
          toast(r.failed.length ? `Done ${r.done.length}, failed: ${r.failed.join(" · ")}` : `Done for ${r.done.length} store(s).`, !!r.failed.length); }
    catch (e) { toast(e.message, true); }
    drawGoogle();
  };
}
const GMODE = { watch: "Watch (secondary)", auto: "Auto switch at 95%", primary: "Primary (Google bids on it)" };
async function drawOurs() {
  const mine = gStore;
  let d;
  try { d = await api(`/api/tracking/google/status?store=${gStore}`); } catch (e) { $("gOurs").innerHTML = `<p class="hint trk-pad">${esc(e.message)}</p>`; return; }
  if (mine !== gStore) return;
  const c = d.settings, n = (k, st) => (d.counts[k] || {})[st] || 0;
  const cmp = d.compare || {};
  const rows = cmp.dates ? cmp.dates.map((dt, i) => `<tr><td>${dt.slice(5)}</td><td class="num"><b>${cmp.ours[i] || "—"}</b></td>${Object.values(cmp.others).map((v) => `<td class="num">${v[i] || "—"}</td>`).join("")}</tr>`).reverse().join("") : "";
  $("gOurs").innerHTML = `<div class="gours"><div class="gours-h"><b>EcomVital → Google Ads</b>
      ${d.pixel ? "" : `<span class="src bad">Pixel off in this store</span>`}
      <span data-manage-only><label class="trk-gsw"><input type="checkbox" id="gOn"${c.enabled ? " checked" : ""}> Send to Google</label>
      <button class="btn btn-sm btn-ghost" id="gSend" title="Send waiting events to Google now">Send now</button>
      <select class="control" id="gMode">${Object.entries(GMODE).map(([k, v]) => `<option value="${k}"${c.mode === k ? " selected" : ""}>${v}</option>`).join("")}</select></span></div>
    <p class="hint">${c.enabled ? `Sending since ${new Date(c.started_at).toLocaleString()} · ${d.ours_primary ? "<b>ProfitDesk Purchase is primary</b>" : "ProfitDesk Purchase is secondary (recorded, not bid on)"}` : "Off: nothing is sent."}
      ${c.note ? ` · ${esc(c.note)}` : ""}${d.last_error ? ` · <span class="neg">${esc(d.last_error)}</span>` : ""}</p>
    <div class="gstats">
      <span><b>${d.google_visitors_14d}</b> visitors from a Google click (14 days)</span>
      <span><b>${n("purchase", "sent")}</b> purchases sent</span><span><b>${n("add_to_cart", "sent")}</b> adds to cart sent</span>
      <span><b>${n("begin_checkout", "sent")}</b> checkouts sent</span>
      <span>${n("purchase", "no_click")} purchases had no Google click</span>
      ${(d.adjusted.done || 0) + (d.adjusted.final || 0) ? `<span>${(d.adjusted.done || 0) + (d.adjusted.final || 0)} refunds / cancellations corrected in Google</span>` : ""}
      ${n("purchase", "retry") + n("purchase", "failed") ? `<span class="neg">${n("purchase", "retry")} waiting to retry · ${n("purchase", "failed")} failed</span>` : ""}</div>
    ${rows ? `<details${c.enabled ? " open" : ""}><summary class="hint">Purchases per day as Google counts them: EcomVital vs the others</summary>
      <div class="table-wrap"><table class="table"><thead><tr><th>Day</th><th>ProfitDesk Purchase</th>${Object.keys(cmp.others).map((k) => `<th>${esc(k)}${cmp.primary.includes(k) ? " (primary)" : ""}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></details>` : ""}
    ${d.errors.length ? `<details><summary class="hint">Last problems</summary>${d.errors.map((e) => `<div class="hint">${esc(e.kind)} ${esc(e.at)}: ${esc(e.error || "")}</div>`).join("")}</details>` : ""}</div>`;
  const save = async (body) => {
    if (body.mode === "primary" && !confirm("Make ProfitDesk Purchase the purchase action Google bids on? Set the old primary to secondary below, so nothing counts twice.")) return drawOurs();
    try { await api(`/api/tracking/google/settings/${gStore}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); toast("Saved."); }
    catch (e) { toast(e.message, true); }
    drawGoogle();
  };
  $("gOn").onchange = () => save({ enabled: $("gOn").checked });
  $("gSend").onclick = async () => {
    $("gSend").disabled = true; $("gSend").textContent = "Sending…";
    try { const r = await api(`/api/tracking/google/send/${gStore}`, { method: "POST" }); toast(r.last_error ? `Some still waiting: ${r.last_error.slice(0, 160)}` : "Sent.", !!r.last_error); }
    catch (e) { toast(e.message, true); }
    drawOurs();
  };
  $("gMode").onchange = () => save({ mode: $("gMode").value });
}

/* ------------------------------------------------ EcomVital → Meta (Conversions API) */
let mStore = null;
const MMODE = {
  off: ["Off", "Nothing is sent to Meta."],
  test: ["Test", "Sends to the store's pixel with Meta's test code: the events show only in Events Manager → Test events and are never counted. Safe to try any time."],
  shadow: ["Parallel run", "Sends to a separate dataset that no campaign uses, so you can compare it with Shopify's Meta app for a week without counting anything twice."],
  live: ["Live", "Sends to the pixel your campaigns optimise on. Use it from the day Shopify's Meta app stops sending (otherwise Meta may count purchases twice)."],
};
async function drawMeta() {
  const box = $("mpanel");
  mStore = mStore || String(state.store || (stores[0] || {}).id || "");
  box.innerHTML = `<div class="panel-head"><h2>EcomVital → Meta (Conversions API)</h2>
      <span><select class="control" id="mStore">${stores.map((s) => `<option value="${s.id}"${String(s.id) === mStore ? " selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
      <button class="btn btn-sm btn-ghost" id="mClose">Close</button></span></div><div id="mBody"><p class="hint trk-pad">Loading…</p></div>`;
  $("mClose").onclick = () => { box.hidden = true; };
  $("mStore").onchange = () => { mStore = $("mStore").value; drawMeta(); };
  const mine = mStore;
  let d;
  try { d = await api(`/api/tracking/meta/status?store=${mStore}`); } catch (e) { $("mBody").innerHTML = `<p class="hint trk-pad">${esc(e.message)}</p>`; return; }
  if (mine !== mStore) return;
  const sent = Object.entries(d.sent_24h);
  const tok = d.token === "pasted" ? "a pasted Conversions API token" : d.token === "connection" ? "your Meta connection (Settings)" : "";
  $("mBody").innerHTML = `
    <p class="hint trk-pad">EcomVital sends each page view, product view, add to cart, checkout, payment step and purchase it tracks to Meta from the server,
      with what helps Meta match them to people: hashed email / phone, IP, browser, Meta's click cookies. Purchases from orders the browser missed go too.
      Keep Shopify's Facebook &amp; Instagram app on until you decide to rely on this.</p>
    <div class="mstate trk-pad"><span class="src ${d.mode === "off" ? "new" : d.mode === "live" ? "invoice" : "estimate"}">${MMODE[d.mode][0]}</span>
      ${d.mode === "off" ? "" : d.pixel ? `Sending to dataset <b>${esc(d.pixel)}</b>${d.mode === "test" ? ` with test code <b>${esc(d.test_code)}</b>` : ""} · token: ${esc(tok || "none")}
        ${d.started_at ? ` · since ${new Date(d.started_at).toLocaleString()}` : ""}` : `<span class="neg">Not sending: ${esc(d.missing)}</span>`}
      ${d.events_manager ? ` · <a href="${esc(d.events_manager)}" target="_blank">Events Manager ↗</a>` : ""}</div>
    ${d.mode !== "off" ? `<div class="gstats trk-pad">
      ${sent.length ? sent.map(([k, v]) => `<span><b>${v}</b> ${esc(k)}</span>`).join("") : `<span>Nothing sent yet in the last 24 h${d.waiting ? "" : " (new events go within a minute)"}</span>`}
      <span>Meta took <b>${d.received_24h}</b></span>${d.errors_24h ? `<span class="neg">${d.errors_24h} refused batch${d.errors_24h === 1 ? "" : "es"}</span>` : ""}
      ${d.waiting ? `<span>${d.waiting} new event${d.waiting === 1 ? "" : "s"} to look at</span>` : ""}</div>` : ""}
    <div class="mgrid trk-pad" data-manage-only>
      <label>Store's Meta pixel (dataset) ID<input class="control" id="mMain" value="${esc(d.main_pixel)}" placeholder="e.g. 681154293413634"></label>
      <label>Token<input class="control" id="mTok" type="password" placeholder="${tok ? `Using ${tok} · paste to replace` : "Paste a Conversions API token"}"></label>
      <label>Mode<select class="control" id="mMode">${d.choices.modes.map((m) => `<option value="${m}"${m === d.mode ? " selected" : ""}>${MMODE[m][0]}</option>`).join("")}</select></label>
      <label id="mTestL">Test code (Events Manager → Test events)<input class="control" id="mTest" value="${esc(d.test_code)}" placeholder="TEST12345"></label>
      <label id="mShadowL">Separate dataset for the parallel run<input class="control" id="mShadow" value="${esc(d.shadow_pixel)}" placeholder="a new dataset's ID"></label>
    </div>
    <p class="hint trk-pad" id="mModeHint"></p>
    <div class="trk-pad mevents" data-manage-only><b>Events to send:</b> ${d.choices.events.map((e) => `<label><input type="checkbox" data-mev="${e}"${d.events.includes(e) ? " checked" : ""}> ${e}</label>`).join("")}</div>
    <div class="trk-pad row-actions" style="justify-content:flex-start" data-manage-only>
      <button class="btn btn-primary" id="mSave">Save</button>
      <button class="btn btn-ghost" id="mSend"${d.mode === "off" ? " disabled" : ""}>Send now</button>
      <button class="btn btn-ghost" id="mPrev">What would be sent?</button></div>
    <div id="mPreview"></div>
    ${d.log.length ? `<details class="trk-pad"><summary class="hint">Last batches sent</summary><div class="table-wrap"><table class="table"><thead><tr><th>When</th><th>Mode</th><th>Dataset</th><th>Events</th><th>Meta took</th><th></th></tr></thead><tbody>
      ${d.log.map((l) => `<tr><td>${new Date(l.at).toLocaleString()}</td><td>${esc(MMODE[l.mode] ? MMODE[l.mode][0] : l.mode || "")}</td><td>${esc(l.pixel_id || "")}</td>
        <td>${Object.entries(l.counts || {}).map(([k, v]) => `${v} ${esc(k)}`).join(", ") || l.sent}</td><td class="num">${l.received}</td>
        <td>${l.error ? `<span class="neg">${esc(l.error)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div></details>` : ""}`;
  const hint = () => {
    const m = $("mMode").value;
    $("mModeHint").textContent = MMODE[m][1];
    $("mTestL").hidden = m !== "test";
    $("mShadowL").hidden = m !== "shadow";
  };
  hint();
  $("mMode").onchange = hint;
  $("mSave").onclick = async () => {
    const mode = $("mMode").value;
    const body = { main_pixel: $("mMain").value, mode, test_code: $("mTest").value, shadow_pixel: $("mShadow").value,
      events: [...$("mBody").querySelectorAll("[data-mev]")].filter((x) => x.checked).map((x) => x.dataset.mev) };
    if ($("mTok").value.trim()) body.meta_token = $("mTok").value.trim();
    if (mode === "live" && d.mode !== "live") {
      if (!confirm("Live sends every event to the pixel your campaigns optimise on.\n\nOnly do this once Shopify's Facebook & Instagram app has stopped sending data for this store, or Meta may count purchases twice.\n\nGo live?")) return;
      body.confirm = "live";
    }
    try { await api(`/api/tracking/meta/settings/${mStore}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
          toast(mode === "off" ? "Saved. Nothing is sent." : "Saved. New events go to Meta within a minute."); drawMeta(); }
    catch (e) { toast(e.message, true); }
  };
  $("mSend").onclick = async () => {
    $("mSend").disabled = true; $("mSend").textContent = "Sending…";
    try { const r = await api(`/api/tracking/meta/send/${mStore}`, { method: "POST" });
          toast(r.error ? r.error : r.skipped ? `Not sent: ${r.skipped}` : `Sent ${r.sent || 0} · Meta took ${r.received || 0}`, !!(r.error || r.skipped)); }
    catch (e) { toast(e.message, true); }
    drawMeta();
  };
  $("mPrev").onclick = async () => {
    $("mPreview").innerHTML = '<p class="hint trk-pad">Looking…</p>';
    try {
      const r = await api(`/api/tracking/meta/preview?store=${mStore}`);
      const lbl = { em: "email", ph: "phone", client_ip_address: "IP", client_user_agent: "browser", fbp: "fbp cookie", fbc: "ad click (fbc)", external_id: "visitor id" };
      $("mPreview").innerHTML = r.events.length ? `<div class="table-wrap trk-pad"><p class="hint">The latest tracked events as Meta would get them (nothing was sent; values hidden):</p>
        <table class="table"><thead><tr><th>Event</th><th>Time</th><th>Value</th><th>Matching details it carries</th></tr></thead><tbody>
        ${r.events.slice().reverse().map((e) => `<tr><td>${esc(e.event)}${e.source !== "website" ? ` <span class="hint">(from the order)</span>` : ""}</td><td>${new Date(e.time).toLocaleString()}</td>
          <td class="num">${e.value != null ? `${esc(e.currency || "")} ${e.value.toFixed(2)}` : "—"}</td><td>${e.matching.map((k) => lbl[k] || k).join(", ")}</td></tr>`).join("")}</tbody></table></div>`
        : '<p class="hint trk-pad">No recent events for this store.</p>';
    } catch (e) { $("mPreview").innerHTML = `<p class="hint trk-pad neg">${esc(e.message)}</p>`; }
  };
}

/* ------------------------------------------------ wiring */
$("storeBtn").onclick = () => menu($("storeMenu"), stores.map((s) => [s.id, `${s.name} · ${s.domain}${s.installed ? "" : " (pixel off)"}`]), state.store, (v) => { state.store = v; state.cur = ""; load(); });
$("rangeBtn").onclick = () => menu($("rangeMenu"), RANGES, state.range, (v) => {
  state.range = v;
  if (v === "custom") { const [a, b] = range(); $("from").value ||= a; $("to").value ||= b; }
  load();
});
$("from").onchange = $("to").onchange = load;
$("hourBtn").onclick = hourMenu;
for (const r of document.querySelectorAll("#level input")) r.onchange = () => { level = r.value; state.key = ""; sort = { k: null, dir: -1 }; load(); };
$("key").onchange = () => { state.key = $("key").value === LEVEL_KEY[level] ? "" : $("key").value; load(); };
$("cur").onchange = () => { state.cur = $("cur").value; load(); };
$("reload").onclick = load;
$("q").oninput = () => { page = 0; if (data) draw(); };
$("rows").onchange = () => { page = 0; $("rowsName").textContent = $("rows").value; if (data) draw(); };
$("noteX").onclick = () => { $("note").hidden = true; try { localStorage.setItem("pdTrkNote", "off"); } catch { /* private */ } };
$("gBtn").onclick = () => { $("gpanel").hidden = !$("gpanel").hidden; if (!$("gpanel").hidden) drawGoogle(); };
$("mBtn").onclick = () => { $("mpanel").hidden = !$("mpanel").hidden; if (!$("mpanel").hidden) drawMeta(); };
$("setupBtn").onclick = () => { $("setup").hidden = !$("setup").hidden; if (!$("setup").hidden) drawSetup(); };
$("evClose").onclick = () => { $("evModal").hidden = true; };
for (const m of ["evModal", "colsModal"]) $(m).onclick = (e) => { if (e.target === $(m)) $(m).hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("evModal").hidden = true; $("colsModal").hidden = true; } });
boot();
