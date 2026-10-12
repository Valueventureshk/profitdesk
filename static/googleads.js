/* ProfitDesk Apps → Google Ads, laid out like pythago.io: filters, summary cards, one row per Shopify
   product (its Google variants combined) with 7 / 14 / 30 day columns, bulk Draft / Archive in Shopify,
   tags, a variant breakdown and a Period 1 vs Period 2 comparison. Plus ProfitDesk's own: any period,
   verdicts, breakeven ROAS, refunds and fees, totals that match the dashboard, the ad account's currency.
   Every figure comes from the server (google_desk.py); the page only adds up rows a filter leaves. */

const $ = (id) => document.getElementById(id);
let data = null, cmp = null, view = ["compare", "manage"].includes(PDUrl.get("view")) ? PDUrl.get("view") : "dash";
let period = PDUrl.get("period") || "30", page = 0, cpage = 0, sort = { key: "cost", win: "sel", dir: -1 }, csort = { key: "cost", p: "p1", dir: -1 };
let picked = new Set(), openP = null, vWin = "sel", tagFilter = new Set();
const PERIOD = { today: "Today", yesterday: "Yesterday", 7: "Last 7 days", 14: "Last 14 days", 30: "Last 30 days", mtd: "This month",
                 lastmonth: "Last month", 90: "Last 90 days", custom: "Custom" };
// header icons: Google Ads for Google spend, Shopify for Shopify sales / revenue / items sold
const G_ICON = `<svg class="gx-ico" viewBox="0 0 24 24" aria-label="Google Ads"><path fill="#FBBC04" d="M3.3 16.2 9.6 5.3a3.4 3.4 0 0 1 5.9 3.4L9.2 19.6a3.4 3.4 0 0 1-5.9-3.4z"/><path fill="#4285F4" d="M20.7 16.2 14.4 5.3a3.4 3.4 0 0 0-5.9 3.4l6.3 10.9a3.4 3.4 0 0 0 5.9-3.4z"/><circle fill="#34A853" cx="6.2" cy="17.9" r="3.4"/></svg>`;
const S_ICON = `<svg class="gx-ico" viewBox="0 0 24 24" aria-label="Shopify"><path fill="#95BF47" d="M15.3 3.2c-.1 0-.2 0-.3.1l-.6.2C14 2.4 13.4 2 12.6 2c-1.8 0-2.7 2.3-3 3.4l-1.6.5c-.5.2-.5.2-.6.6L6 19.5l9.7 1.8 5.3-1.2-3.4-16.5c0-.2-.2-.3-.3-.3zM12.9 4l-1.7.5c.3-1.3 1-1.9 1.5-2.1.1.4.2 1 .2 1.6zm-1.6-1.4.3.1c-.7.3-1.4 1.1-1.7 2.6l-1.3.4c.4-1.2 1.2-3.1 2.7-3.1z"/><path fill="#5E8E3E" d="m17.3 3.3-1.6 18 5.3-1.2-3.4-16.5c0-.2-.2-.3-.3-.3z"/><path fill="#fff" d="m12.6 7.6-.7 2c-.6-.3-1.4-.4-1.4-.4-1.1.1-1.2.8-1.2.9.1 1 2.6 1.2 2.7 3.5.1 1.8-1 3-2.5 3.1-1.8.1-2.8-1-2.8-1l.4-1.6s1 .7 1.7.7c.5 0 .7-.4.7-.7-.1-1.3-2.2-1.2-2.3-3.3-.1-1.8 1-3.6 3.6-3.7 1-.1 1.8.5 1.8.5z"/></svg>`;
const ICON = { g: G_ICON, s: S_ICON };
const VERDICT = { scale: ["invoice", "Scale"], keep: ["catalog", "Keep"], watch: ["changed", "Watch"], cut: ["bad", "Cut"], learning: ["new", "Learning"] };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, digits = 2, cur) {
  if (v === null || v === undefined) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || (view === "compare" ? cmp : data)?.currency || "USD", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v); }
  catch { return Number(v).toFixed(digits); }
}
const n = (v, d = 0) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v) => (v === null || v === undefined ? "—" : (v * 100).toFixed(2) + "%");
const ratio = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/googleads"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || (typeof body.detail === "string" ? body.detail : "") || `Request failed (${r.status})`);
  return body;
}
const post = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 7000 : 3000);
}
// ROAS cell colour: how far over / under the product's own breakeven ROAS it is
function heat(m) {
  if (!m || m.roas === null || m.roas === undefined) return "";
  if (!m.be_roas) return "gx-h0";
  const r = m.roas / m.be_roas;
  return r >= 1.3 ? "gx-h4" : r >= 1 ? "gx-h3" : r >= 0.8 ? "gx-h2" : "gx-h1";
}

/* ------------------------------------------------ dates (the store's own clock, like the dashboard) */
let stores = [];
const storeOf = () => stores.find((s) => String(s.id) === String($("store").value));
function storeToday() {
  try { return new Intl.DateTimeFormat("en-CA", { timeZone: storeOf()?.timezone || undefined }).format(new Date()); }
  catch { return new Date().toISOString().slice(0, 10); }
}
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, k) => { const d = new Date(s + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + k); return iso(d); };
const spanDays = (a, b) => Math.round((new Date(b + "T12:00:00Z") - new Date(a + "T12:00:00Z")) / 864e5) + 1;
function range() {
  const t = storeToday();
  switch (period) {
    case "today": return [t, t];
    case "yesterday": return [addDays(t, -1), addDays(t, -1)];
    case "mtd": return [t.slice(0, 8) + "01", t];
    case "lastmonth": { const e = addDays(t.slice(0, 8) + "01", -1); return [e.slice(0, 8) + "01", e]; }
    case "custom": return [$("from").value || PDUrl.get("from") || addDays(t, -29), $("to").value || PDUrl.get("to") || t];
    default: return [addDays(t, -(Number(period) - 1)), t];
  }
}
const fmtDate = (s) => new Date(s + "T12:00:00Z").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/* ------------------------------------------------ columns (pythago's, then ProfitDesk's) */
const COLS = [
  { id: "cost", label: "Cost", icon: "g", t: "Google spend in the picked period", v: (m) => m.cost, f: (v) => money(v) },
  { id: "conversions", label: "Conv.", t: "Google conversions (Google's attribution)", v: (m) => m.conversions, f: (v) => n(v, 1), cls: "gx-purple" },
  { id: "qty", label: "Qty.", icon: "s", t: "Items sold on Shopify (all channels)", v: (m) => m.qty, f: (v) => n(v), sub: (m) => (m.ref_units ? `(−${n(m.ref_units)})` : "") },
  { id: "sales", label: "CV", icon: "s", t: "Shopify conversion value: what the product sold for", v: (m) => m.sales, f: (v) => money(v) },
  { id: "refunds", label: "Refunds", t: "Refunded on Shopify in the period", v: (m) => m.refunds, f: (v) => (v ? `<span class="gx-neg">−${money(v)}</span>` : "—") },
  { id: "clicks", label: "Clicks", v: (m) => m.clicks, f: (v) => n(v), cls: "gx-purple" },
  { id: "ctr", label: "CTR", v: (m) => m.ctr, f: (v) => pct(v), cls: "gx-green" },
  { id: "cpc", label: "CPC", v: (m) => m.cpc, f: (v) => money(v) },
  ...["7", "14", "30"].flatMap((w) => [
    { id: "roas" + w, w, key: "roas", label: `ROAS ${w}D`, v: (m) => m.roas, f: (v) => ratio(v), heat: true },
    { id: "cost" + w, w, key: "cost", label: `Cost ${w}D`, icon: "g", t: "Google spend", v: (m) => m.cost, f: (v) => money(v) },
    { id: "sales" + w, w, key: "sales", label: `Sales ${w}D`, icon: "s", t: "Shopify sales", v: (m) => m.sales, f: (v) => money(v) },
    { id: "impressions" + w, w, key: "impressions", label: `👁 ${w}D`, t: "Impressions", v: (m) => m.impressions, f: (v) => n(v) },
  ]),
  { id: "cog", label: "Sum COG", v: (m) => m.cog, f: (v) => money(v) },
  { id: "fees", label: "Fees", t: "Payment fees at the store's rate", v: (m) => m.fees, f: (v) => money(v) },
  { id: "profit", label: "Net profit", t: "Sales − refunds − COG − fees − Google spend", v: (m) => m.profit, f: (v) => money(v), sign: true },
  { id: "poas", label: "POAS", t: "Net profit ÷ spend", v: (m) => m.poas, f: (v) => ratio(v), sign: true },
  { id: "roas", label: "ROAS", t: "Shopify sales ÷ spend, picked period", v: (m) => m.roas, f: (v) => ratio(v), heat: true },
  { id: "be_roas", label: "BE ROAS", t: "Breakeven ROAS: where the product stops losing money", v: (m) => m.be_roas, f: (v) => ratio(v) },
  { id: "verdict", label: "Verdict", t: "Scale / Keep / Watch / Cut / Learning", v: (m) => ({ scale: 5, keep: 4, watch: 3, cut: 1, learning: 2 }[m.verdict] || 0),
    f: (v, m) => { if (!m.verdict) return "—"; const [c, l] = VERDICT[m.verdict] || ["new", m.verdict]; return `<span class="src ${c}">${l}</span>`; } },
  { id: "value", label: "G value", t: "Google conversion value (Google's attribution)", v: (m) => m.value, f: (v) => money(v) },
  { id: "ad_days", label: "Ad days", t: "Days the product had Google spend", v: (m) => m.ad_days, f: (v) => n(v) },
];
const DEFAULT_ON = COLS.filter((c) => !["fees", "be_roas", "value", "ad_days"].includes(c.id)).map((c) => c.id);
let colsOn = (() => { try { const v = JSON.parse(localStorage.getItem("pdGxCols")); if (Array.isArray(v) && v.length) return v; } catch { /* none */ } return DEFAULT_ON; })();
const shownCols = () => COLS.filter((c) => colsOn.includes(c.id));
const win = (c) => c.w || "sel";

/* ------------------------------------------------ loading */
async function load(fresh = false) {
  const store = $("store").value || PDUrl.get("store") || "";
  $("refresh").disabled = true;
  $("sync").textContent = "Reading Google Ads and Shopify…";
  const [a, b] = stores.length ? range() : [null, null];
  const c = $("country").value;
  try {
    const got = await api(`/api/googleads?store=${encodeURIComponent(store)}${a ? `&start=${a}&end=${b}` : ""}${c ? `&country=${c}` : ""}${fresh ? "&fresh=1" : ""}`);
    data = got;
    if (!stores.length) {
      stores = got.stores;
      $("store").innerHTML = stores.map((s) => `<option value="${s.id}"${s.direct ? "" : " disabled"}>${esc(s.name)}${s.direct ? "" : " (needs access)"}</option>`).join("");
      if (got.store) $("store").value = String(got.store);
      if (period !== "30" && !got.error) { $("refresh").disabled = false; return load(fresh); }   // now the store's clock is known
    }
  } catch (e) { toast(e.message, true); $("refresh").disabled = false; $("sync").textContent = e.message; return; }
  $("refresh").disabled = false;
  if (data.store) $("store").value = String(data.store);
  PDUrl.set({ store: data.store || null, period: period === "30" ? null : period, view: view === "dash" ? null : view,
              from: period === "custom" ? data.start : null, to: period === "custom" ? data.end : null });
  if (data.error) { $("problems").innerHTML = `<div class="warn">${esc(data.error)}</div>`; $("table").innerHTML = ""; $("cards").innerHTML = ""; return; }
  $("problems").innerHTML = data.missing_product_ids
    ? `<div class="notice">Shopify sales are still being matched to products for this store (a few minutes after the update). Refresh shortly.</div>` : "";
  if (period === "custom") { $("from").value = data.start; $("to").value = data.end; }
  fillCountries($("country"), data.countries, data.country);
  picked = new Set([...picked].filter((pid) => data.products.some((p) => p.pid === pid)));
  drawHelp();
  drawAll();
}
function fillCountries(sel, list, cur) {
  const keep = cur || sel.value;
  sel.innerHTML = `<option value="">All Countries</option>` + (list || []).map((c) => `<option${c === keep ? " selected" : ""}>${esc(c)}</option>`).join("");
}

/* ------------------------------------------------ filters */
function filtered() {
  const q = $("q").value.trim().toLowerCase(), v = $("verdict").value, st = $("status").value;
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  const minR = num("minRoas"), maxR = num("maxRoas"), minD = num("minDays"), minC = num("minCost");
  const tags = data.product_tags || {};
  return data.products.filter((p) => {
    const m = p.w.sel;
    if (!(m.cost || m.sales || p.w["30"].cost || p.w["30"].sales)) return false;
    if (q && !p.title.toLowerCase().includes(q) && !p.pid.includes(q)) return false;
    if (v && m.verdict !== v) return false;
    if (st && p.status !== st) return false;
    if (minR !== null && !((m.roas ?? -1) >= minR)) return false;
    if (maxR !== null && !(m.roas !== null && m.roas <= maxR)) return false;
    if (minD !== null && !(m.ad_days >= minD)) return false;
    if (minC !== null && !(m.cost >= minC)) return false;
    if (tagFilter.size && !(tags[p.pid] || []).some((t) => tagFilter.has(t))) return false;
    return true;
  });
}
const filtersOn = () => ["q", "minRoas", "maxRoas", "minDays", "minCost", "status", "verdict"].some((id) => $(id).value !== "") || tagFilter.size > 0;
function sorted(list) {
  const get = (p) => {
    if (sort.key === "title") return p.title.toLowerCase();
    const c = COLS.find((x) => x.id === sort.key);
    const x = c ? c.v(p.w[win(c)]) : p.w[sort.win][sort.key];
    return x === null || x === undefined ? -Infinity : x;
  };
  return list.slice().sort((a, b) => (get(a) > get(b) ? 1 : get(a) < get(b) ? -1 : 0) * sort.dir);
}

/* ------------------------------------------------ cards */
function sums(list, w = "sel") {
  // the rows a filter leaves, added up; ratios from those sums (the server's own when nothing is filtered)
  const s = {};
  for (const k of ["cost", "qty", "sales", "cog", "fees", "refunds", "conversions", "value", "ref_units"]) s[k] = list.reduce((a, p) => a + (p.w[w][k] || 0), 0);
  s.profit = s.sales - s.refunds - s.cog - s.fees - s.cost;
  s.roas = s.cost ? s.sales / s.cost : null;
  s.poas = s.cost ? s.profit / s.cost : null;
  const kept = s.sales - s.refunds - s.cog - s.fees;
  s.be_roas = kept > 0 ? s.sales / kept : null;
  return s;
}
function drawCards(list) {
  const f = filtersOn() || data.country;
  const s = f ? sums(list) : data.summary.sel;
  const card = (value, label, cls = "") => `<div class="gx-c"><div class="gx-cv ${cls}">${value}</div><div class="gx-cl">${label}</div></div>`;
  $("cards").innerHTML =
    card(n(list.length), "Products filtered") +
    card(money(s.cost, 0), "Total cost - Google Ads spend") +
    card(n(s.qty), "Shopify - quantity (items)") +
    card(money(s.sales, 0), "Shopify - conversion value") +
    card(ratio(s.roas), "ROAS based on Shopify conversion value") +
    card(money(s.cog, 0), "Sum COG (cost of goods)") +
    card(money(s.profit, 0), "Net profit (rev − refunds − COG − fees − ad cost)", s.profit >= 0 ? "gx-pos" : "gx-negc") +
    card(ratio(s.poas), "POAS (profit on ad spend)", (s.poas ?? 0) >= 0 ? "gx-pos" : "gx-negc");
  const bits = [`Breakeven ROAS ${ratio(s.be_roas)}`, `refunds ${money(s.refunds, 0)}`, `fees ${money(s.fees, 0)}`];
  if (!f && s.cost_other >= 0.5) bits.push(`Google spend not on a product ${money(s.cost_other, 0)} (Performance Max video / display / search, Search campaigns)`);
  if (!f) bits.push("totals match the dashboard for the same days");
  else bits.push(data.country ? `${data.country} only: totals are the products' own` : "totals are the filtered products' own");
  if (data.fx_rate) bits.push(`store figures converted from ${data.store_currency}`);
  $("cardNote").textContent = bits.join(" · ");
}

/* ------------------------------------------------ table */
function th(label, key, w, title = "", cls = "", icon = "") {
  const on = sort.key === key && (sort.win === w || key === "title");
  return `<th class="gx-sort ${cls}${on ? " on" : ""}" data-key="${key}" data-w="${w}" title="${esc(title)}">${ICON[icon] || ""}${label} <i>${on ? (sort.dir < 0 ? "↓" : "↑") : "⇅"}</i></th>`;
}
function drawAll() {
  const list = sorted(filtered());
  drawCards(list);
  const [a, b] = [data.start, data.end];
  $("perfDates").textContent = a === b ? a : `${a} to ${b}`;
  $("sub").textContent = `${data.currency} · the Google Ads account's currency · store clock (same days as the dashboard)`;
  $("sync").textContent = `Last synced: ${ago(data.cached_at)}`;
  const per = Number($("per").value), pages = Math.max(1, Math.ceil(list.length / per));
  page = Math.min(page, pages - 1);
  const rows = list.slice(page * per, page * per + per);
  const cols = shownCols(), tags = data.tags || [], ptags = data.product_tags || {};
  const allOn = rows.length && rows.every((p) => picked.has(p.pid));
  $("table").innerHTML = `<thead><tr><th class="gx-pin gx-cb"><input type="checkbox" id="all"${allOn ? " checked" : ""}></th>
      ${th("Product", "title", "sel", "", "gx-pin gx-prodh")}<th class="gx-pin gx-st">Status</th><th class="gx-pin gx-vr">Variants</th>
      ${cols.map((c) => th(c.label, c.id, win(c), c.t || "", c.id.startsWith("roas") ? "gx-hc" : "", c.icon)).join("")}</tr></thead>
    <tbody>${rows.map((p) => {
      const mine = (ptags[p.pid] || []).map((id) => tags.find((t) => t.id === id)).filter(Boolean);
      return `<tr data-pid="${esc(p.pid)}" class="${picked.has(p.pid) ? "on" : ""}">
        <td class="gx-pin gx-cb">${p.unlinked ? "" : `<input type="checkbox" data-pick${picked.has(p.pid) ? " checked" : ""}>`}</td>
        <td class="gx-pin gx-prod">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : `<span class="gx-noimg"></span>`}
          <span class="gx-pt"><span class="gx-pn" title="${esc(p.title)}">${p.handle && data.shop ? `<a href="https://${esc(data.shop)}/products/${esc(p.handle)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</span>
          <span class="gx-ps">${p.unlinked ? `<span class="gx-muted" title="Google reported this Shopping / Performance Max spend without saying which product it was for (usually a product since removed from the feed or Merchant Center). It counts in the totals.">Google didn't say which product · counted in totals</span>` : ""}${p.unlinked ? "" : mine.map((t) => `<span class="gx-tag" style="--c:${esc(t.color)}">${esc(t.name)}</span>`).join("") || `<span class="gx-muted">${esc(p.pid.startsWith("item:") ? p.pid.slice(5) : "ID " + p.pid)}</span>`}</span></span></td>
        <td class="gx-pin gx-st"><span class="gx-status ${esc((p.status || "").toLowerCase())}">${esc(p.status || "—")}</span></td>
        <td class="gx-pin gx-vr">${p.unlinked ? "—" : `<button class="gx-vbtn" data-var="${data.products.indexOf(p)}">${p.variants.length} ▤</button>`}</td>
        ${cols.map((c) => { const m = p.w[win(c)], v = c.v(m);
          return `<td class="${c.heat ? "gx-hc " + heat(m) : ""} ${c.sign ? ((v ?? 0) >= 0 ? "gx-posc" : "gx-negc") : ""} ${c.cls || ""}">${c.f(v, m)}${c.sub && c.sub(m) ? `<span class="gx-subn">${c.sub(m)}</span>` : ""}</td>`; }).join("")}</tr>`;
    }).join("") || `<tr><td class="gx-muted gx-empty" colspan="40">No products match.</td></tr>`}</tbody>`;
  const from = list.length ? page * per + 1 : 0, to = Math.min(list.length, page * per + per);
  $("showing").textContent = `Showing ${rows.length} of ${n(list.length)} products${picked.size ? ` · ${picked.size} selected` : ""}`;
  $("showing2").textContent = `Showing ${from}-${to} of ${n(list.length)} products`;
  $("pages").innerHTML = pager(page, pages, "pg");
  for (const x of $("pages").querySelectorAll("[data-pg]")) x.onclick = () => { page = Number(x.dataset.pg); drawAll(); $("wrap").scrollTop = 0; };
  for (const x of $("table").querySelectorAll(".gx-sort")) x.onclick = () => {
    const key = x.dataset.key, w = x.dataset.w;
    sort = sort.key === key ? { key, win: w, dir: -sort.dir } : { key, win: w, dir: key === "title" ? 1 : -1 };
    drawAll();
  };
  $("all").onchange = () => { for (const p of rows) $("all").checked ? picked.add(p.pid) : picked.delete(p.pid); drawAll(); };
  for (const cb of $("table").querySelectorAll("[data-pick]")) cb.onchange = () => {
    const pid = cb.closest("tr").dataset.pid;
    cb.checked ? picked.add(pid) : picked.delete(pid);
    drawAll();
  };
  for (const x of $("table").querySelectorAll("[data-var]")) x.onclick = () => openVariants(data.products[+x.dataset.var]);
  $("clearF").hidden = !filtersOn();
  $("tagsN").textContent = tagFilter.size ? `(${tagFilter.size})` : "";
}
function pager(cur, pages, attr) {
  if (pages <= 1) return "";
  const nums = [...new Set([0, cur - 2, cur - 1, cur, cur + 1, cur + 2, pages - 1])].filter((x) => x >= 0 && x < pages).sort((a, b) => a - b);
  let out = `<button class="gx-pg" data-${attr}="${Math.max(0, cur - 1)}"${cur ? "" : " disabled"}>Previous</button>`;
  let last = -1;
  for (const x of nums) { if (x - last > 1) out += `<span class="gx-muted">…</span>`; out += `<button class="gx-pg${x === cur ? " on" : ""}" data-${attr}="${x}">${x + 1}</button>`; last = x; }
  return out + `<button class="gx-pg" data-${attr}="${Math.min(pages - 1, cur + 1)}"${cur < pages - 1 ? "" : " disabled"}>Next</button>`;
}
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - (t || 0));
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} hours ago`;
}
function drawHelp() {
  $("help").innerHTML = `<p><b>CV / ↗</b>: Shopify sales of the product in the period, all channels (fair for stores that only advertise on Google).
      <b>Conv. / G value</b>: what Google says its ads sold (its own attribution). <b>Qty</b>: items sold, refunded items in brackets.
      <b>COG</b>: from invoices and product history. <b>Fees</b>: payment fees at this store's rate (${pct(data.fee_rate)}).</p>
    <p><b>Net profit</b> = sales − refunds − COG − fees − Google spend. <b>ROAS</b> = sales ÷ spend. <b>POAS</b> = net profit ÷ spend.
      <b>Breakeven ROAS</b> = sales ÷ (sales − refunds − COG − fees). ROAS colours: green 30%+ over breakeven, light green over, yellow up to 20% under, red further under.</p>
    <p><b>Verdicts</b>: Learning = under ${money(data.min_spend, 0)} spend · Scale = ROAS 30%+ over breakeven · Keep = over breakeven ·
      Watch = up to 20% under · Cut = further under, or spend with no sales.</p>
    <p><b>Not on a product</b>: Performance Max also spends on video, display and search ads Google puts on no product, and Search
      campaigns have none. It's in the totals (so they match your Google Ads account and the dashboard) but in no product row.
      <b>Country</b>: Google by the feed country in the product id (shopify_AU_…), Shopify by the order's shipping country.</p>`;
}

/* ------------------------------------------------ actions, tags */
function drawActMenu() {
  const tags = data?.tags || [];
  const item = (t, act) => `<button data-${act}="${t.id}"><span class="gx-dot" style="--c:${esc(t.color)}"></span>${esc(t.name)}</button>`;
  $("actAdd").innerHTML = tags.map((t) => item(t, "add")).join("") || `<span class="gx-muted gx-mi">No tags yet</span>`;
  $("actRemove").innerHTML = tags.map((t) => item(t, "rm")).join("") || `<span class="gx-muted gx-mi">No tags yet</span>`;
}
async function act(kind, arg) {
  const ids = [...picked];
  if (!ids.length) return toast("Tick products first.", true);
  $("actMenu").hidden = true;
  if (kind === "csv") return exportCsv(data.products.filter((p) => picked.has(p.pid)));
  if (kind === "add" || kind === "rm") {
    try { const t = await post(`/api/googleads/tags/${arg}/products`, kind === "add" ? { add: ids } : { remove: ids });
          data.tags = t.tags; data.product_tags = t.product_tags; drawAll(); toast(kind === "add" ? "Tag added." : "Tag removed."); }
    catch (e) { toast(e.message, true); }
    return;
  }
  const word = { DRAFT: "Draft", ARCHIVED: "Archived", ACTIVE: "Active" }[kind];
  const real = ids.filter((x) => /^\d+$/.test(x));
  if (!real.length) return toast("Those aren't Shopify products.", true);
  if (!confirm(`Set ${real.length} product${real.length > 1 ? "s" : ""} to ${word} in Shopify?${kind !== "ACTIVE" ? " They stop showing on the store (and in Google Shopping) until set back to Active." : ""}`)) return;
  try {
    const r = await post("/api/googleads/status", { store: data.store, products: real, status: kind });
    for (const p of data.products) if (r.done.includes(p.pid)) p.status = word;
    picked = new Set();
    drawAll();
    toast(r.errors.length ? `${r.done.length} done, ${r.errors.length} failed: ${r.errors[0]}` : `${r.done.length} set to ${word} in Shopify.`, !!r.errors.length);
  } catch (e) { toast(e.message, true); }
}
function drawTags() {
  const tags = data?.tags || [], ptags = data?.product_tags || {};
  const count = (id) => Object.values(ptags).filter((l) => l.includes(id)).length;
  $("tList").innerHTML = tags.map((t) => `<div class="gx-trow"><span class="gx-tag" style="--c:${esc(t.color)}">${esc(t.name)}</span>
      <span class="gx-muted">${count(t.id)} products</span><button class="gx-link" data-del="${t.id}">Delete</button></div>`).join("")
    || `<div class="gx-emptytags"><b>No tags yet</b><p class="gx-muted">Create your first tag above to start organizing products</p></div>`;
  for (const b of $("tList").querySelectorAll("[data-del]")) b.onclick = async () => {
    if (!confirm("Delete this tag? Products keep everything else.")) return;
    try { const t = await api(`/api/googleads/tags/${b.dataset.del}`, { method: "DELETE" }); data.tags = t.tags; data.product_tags = t.product_tags; tagFilter.delete(+b.dataset.del); drawTags(); drawAll(); }
    catch (e) { toast(e.message, true); }
  };
  $("tagsMenu").innerHTML = tags.map((t) => `<label><input type="checkbox" data-tf="${t.id}"${tagFilter.has(t.id) ? " checked" : ""}><span class="gx-tag" style="--c:${esc(t.color)}">${esc(t.name)}</span></label>`).join("")
    || `<span class="gx-muted gx-mi">No tags yet. Make them with 🏷 above the table.</span>`;
  for (const c of $("tagsMenu").querySelectorAll("[data-tf]")) c.onchange = () => { c.checked ? tagFilter.add(+c.dataset.tf) : tagFilter.delete(+c.dataset.tf); page = 0; drawAll(); };
}

/* ------------------------------------------------ variants */
function openVariants(p) {
  openP = p;
  vWin = "sel";
  for (const b of $("vWin").querySelectorAll("[data-w]")) b.classList.toggle("on", b.dataset.w === vWin);
  $("vModal").hidden = false;
  drawVariants();
}
function drawVariants() {
  const p = openP, m = p.w[vWin];
  $("vTitle").textContent = p.title;
  $("vCount").textContent = `${p.variants.length} variants for this product`;
  const card = (value, label, cls = "") => `<div class="gx-c"><div class="gx-cv ${cls}">${value}</div><div class="gx-cl">${label}</div></div>`;
  $("vCards").innerHTML = card(n(p.variants.length), "Variants") + card(money(m.cost), "Total cost") + card(n(m.conversions, 0), "Online conv.")
    + card(`${n(m.net_qty)} ${m.ref_units ? `<small class="gx-neg">(−${n(m.ref_units)})</small>` : ""}`, "Net qty.") + card(money(m.net_sales), "Net sales")
    + card(ratio(m.roas), "ROAS") + card(money(m.cog), "Sum COG") + card(money(m.profit), "Net profit", m.profit >= 0 ? "gx-pos" : "gx-negc")
    + card(ratio(m.poas), "POAS", (m.poas ?? 0) >= 0 ? "gx-pos" : "gx-negc");
  $("vTable").innerHTML = `<thead><tr><th>Variant name</th><th>Price</th><th>COG</th><th>${G_ICON}Ad cost</th><th>Impressions</th><th>Clicks</th><th>Online conv.</th>
      <th>${S_ICON}Offline qty.</th><th>${S_ICON}Offline rev.</th><th>Refunds</th><th>Ref. units</th><th>${S_ICON}Net rev.</th><th>Sum COG</th><th>Net profit</th><th>POAS</th><th>CTR</th><th>CPC</th><th>ROAS</th><th>Verdict</th></tr></thead>
    <tbody>${p.variants.map((v) => {
      const x = v.w[vWin], [vc, vl] = VERDICT[x.verdict] || ["new", x.verdict];
      return `<tr><td class="gx-prod">${v.image ? `<img src="${esc(v.image)}" alt="" loading="lazy">` : ""}<span>${esc(v.title)}</span></td>
        <td>${money(v.price)}</td><td>${money(v.unit_cog)}</td><td>${money(x.cost)}</td><td>${n(x.impressions)}</td><td>${n(x.clicks)}</td>
        <td>${n(x.conversions, 0)}</td><td>${n(x.qty)}</td><td>${money(x.sales)}</td><td>${x.refunds ? `<span class="gx-neg">−${money(x.refunds)}</span>` : "—"}</td><td>${n(x.ref_units)}</td>
        <td>${money(x.net_sales)}</td><td>${money(x.cog)}</td><td class="${x.profit >= 0 ? "gx-posc" : "gx-negc"}">${money(x.profit)}</td>
        <td class="${(x.poas ?? 0) >= 0 ? "gx-posc" : "gx-negc"}">${ratio(x.poas)}</td><td>${pct(x.ctr)}</td><td>${money(x.cpc)}</td>
        <td class="gx-hc ${heat(x)}">${ratio(x.roas)}</td><td><span class="src ${vc}">${vl}</span></td></tr>`;
    }).join("")}</tbody>`;
}

/* ------------------------------------------------ CSV */
function exportCsv(list) {
  const cols = shownCols();
  const head = ["Product", "Product ID", "Status", "Variants", "Tags", ...cols.map((c) => c.label.replace(/^[^\w$]+\s*/, ""))];
  const tags = data.tags || [], ptags = data.product_tags || {};
  const fmt = (v) => (v === null || v === undefined ? "" : typeof v === "number" ? v.toFixed(2) : String(v));
  const lines = [head, ...list.map((p) => [p.title, p.pid, p.status, p.variants.length,
    (ptags[p.pid] || []).map((id) => tags.find((t) => t.id === id)?.name).filter(Boolean).join("; "),
    ...cols.map((c) => { const m = p.w[win(c)]; return c.id === "verdict" ? m.verdict : fmt(c.v(m)); })])];
  const csv = lines.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `Google products ${$("store").selectedOptions[0]?.textContent || ""} ${data.start} to ${data.end} ${data.currency}.csv`;
  a.click();
}

/* ------------------------------------------------ compare (period 1 vs period 2) */
function cmpDefaults(days = 30) {
  const t = storeToday();
  $("p1e").value = t; $("p1s").value = addDays(t, -(days - 1));
  $("p2e").value = addDays($("p1s").value, -1); $("p2s").value = addDays($("p2e").value, -(days - 1));
}
async function loadCompare(fresh = false) {
  if (!$("p1s").value) cmpDefaults(30);
  $("cmpGo").disabled = true;
  $("cshowing").textContent = "Reading both periods…";
  const c = $("ccountry").value;
  try {
    cmp = await api(`/api/googleads/compare?store=${encodeURIComponent($("store").value)}&p1_start=${$("p1s").value}&p1_end=${$("p1e").value}`
      + `&p2_start=${$("p2s").value}&p2_end=${$("p2e").value}${c ? `&country=${c}` : ""}${fresh ? "&fresh=1" : ""}`);
  } catch (e) { toast(e.message, true); $("cmpGo").disabled = false; $("cshowing").textContent = e.message; return; }
  $("cmpGo").disabled = false;
  fillCountries($("ccountry"), cmp.countries, cmp.country);
  drawCompare();
}
const delta = (a, b) => (b ? (a - b) / Math.abs(b) : null);
function dpill(d, good = 1) {
  if (d === null || d === undefined || !isFinite(d)) return `<span class="gx-d">—</span>`;
  const cls = good === 0 ? "gx-dn" : (d >= 0) === (good > 0) ? "gx-dg" : "gx-db";
  return `<span class="gx-d ${cls}">${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}%</span>`;
}
const CMP = [
  { key: "cost", label: "Ad spend", f: (v) => money(v), good: 0 },
  { key: "sales", label: "Revenue", f: (v) => money(v), good: 1 },
  { key: "roas", label: "ROAS", f: (v) => ratio(v), good: 1 },
  { key: "qty", label: "Conversions", t: "Items sold on Shopify", f: (v) => n(v), good: 1 },
  { key: "profit", label: "Net profit", f: (v) => money(v), good: 1, sign: true },
  { key: "cog", label: "COG", f: (v) => money(v), good: 0 },
  { key: "poas", label: "POAS", f: (v) => ratio(v), good: 1 },
];
function drawCompare() {
  const s1 = cmp.country ? null : cmp.summary.p1, s2 = cmp.country ? null : cmp.summary.p2;
  const r = cmp.ranges;
  $("p1Label").textContent = `${fmtDate(r.p1.start)} - ${fmtDate(r.p1.end)}`;
  $("p2Label").textContent = `${fmtDate(r.p2.start)} - ${fmtDate(r.p2.end)}`;
  $("p1Days").textContent = `(${spanDays(r.p1.start, r.p1.end)} days)`;
  $("p2Days").textContent = `(${spanDays(r.p2.start, r.p2.end)} days)`;
  $("avail").innerHTML = `📅 Shopify product data available from <b>${esc(cmp.data_from ? fmtDate(cmp.data_from) : "—")}</b> to <b>${fmtDate(cmp.today)}</b> · Google Ads: all history · ${esc(cmp.currency)}`;
  const q = $("cq").value.trim().toLowerCase(), st = $("cstatus").value;
  let list = cmp.products.filter((p) => (p.w.p1.cost || p.w.p1.sales || p.w.p2.cost || p.w.p2.sales)
    && (!q || p.title.toLowerCase().includes(q) || p.pid.includes(q)) && (!st || p.status === st));
  const filt = q || st || cmp.country;
  const t1 = filt || !s1 ? sums(list, "p1") : s1, t2 = filt || !s2 ? sums(list, "p2") : s2;
  $("ccards").innerHTML = CMP.map((c) => `<div class="gx-c gx-cc"><div class="gx-cl">${c.label}</div>
      <div class="gx-crow"><span class="gx-p p1">P1</span><b class="${c.sign ? (t1[c.key] >= 0 ? "gx-pos" : "gx-negc") : ""}">${c.f(t1[c.key])}</b></div>
      <div class="gx-crow gx-muted"><span class="gx-p p2">P2</span>${c.f(t2[c.key])}</div>${dpill(delta(t1[c.key], t2[c.key]), c.good)}</div>`).join("");
  const get = (p) => { const x = p.w[csort.p][csort.key]; return csort.key === "title" ? p.title.toLowerCase() : x ?? -Infinity; };
  list = list.sort((a, b) => (get(a) > get(b) ? 1 : get(a) < get(b) ? -1 : 0) * csort.dir);
  const per = Number($("cper").value), pages = Math.max(1, Math.ceil(list.length / per));
  cpage = Math.min(cpage, pages - 1);
  const rows = list.slice(cpage * per, cpage * per + per);
  const G = [["cost", `${G_ICON}Ad spend`, 0], ["sales", `${S_ICON}Revenue`, 1], ["roas", "ROAS", 1], ["cog", "COG", 0], ["profit", "Net profit", 1]];
  const fm = (k, v) => (k === "roas" ? ratio(v) : money(v));
  const sh = (k, p, label) => `<th class="gx-sort${csort.key === k && csort.p === p ? " on" : ""}" data-ck="${k}" data-cp="${p}"><span class="gx-p ${p}">${label}</span></th>`;
  $("ctable").innerHTML = `<thead><tr><th rowspan="2" class="gx-prodh">Product</th>${G.map(([k, l]) => `<th colspan="3" class="gx-grp">${l}</th>`).join("")}</tr>
      <tr>${G.map(([k]) => sh(k, "p1", "P1") + sh(k, "p2", "P2") + `<th>Δ%</th>`).join("")}</tr></thead>
    <tbody>${rows.map((p) => `<tr><td class="gx-prod">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : `<span class="gx-noimg"></span>`}
        <span class="gx-pt"><span class="gx-pn" title="${esc(p.title)}">${esc(p.title)}</span><span class="gx-ps"><span class="gx-vtag">▤ ${p.variants.length} variants</span></span></span></td>
      ${G.map(([k, , good]) => { const a = p.w.p1[k], b = p.w.p2[k], d = delta(a ?? 0, b ?? 0);
        return `<td>${fm(k, a)}</td><td class="gx-muted">${fm(k, b)}</td><td class="gx-dc ${d === null ? "" : good === 0 ? "gx-dcn" : (d >= 0) === !!good ? "gx-dcg" : "gx-dcb"}">${d === null ? "—" : `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}%`}</td>`; }).join("")}</tr>`).join("")
      || `<tr><td class="gx-muted gx-empty" colspan="16">No products in either period.</td></tr>`}</tbody>`;
  $("cshowing").textContent = `Showing ${rows.length} of ${n(list.length)} products`;
  $("cshowing2").textContent = `Showing ${list.length ? cpage * per + 1 : 0}-${Math.min(list.length, cpage * per + per)} of ${n(list.length)} products`;
  $("cpages").innerHTML = pager(cpage, pages, "cpg");
  for (const x of $("cpages").querySelectorAll("[data-cpg]")) x.onclick = () => { cpage = Number(x.dataset.cpg); drawCompare(); };
  for (const x of $("ctable").querySelectorAll("[data-ck]")) x.onclick = () => {
    const key = x.dataset.ck, p = x.dataset.cp;
    csort = csort.key === key && csort.p === p ? { key, p, dir: -csort.dir } : { key, p, dir: -1 };
    drawCompare();
  };
}
function cmpCsv() {
  const G = ["cost", "sales", "roas", "cog", "profit"];
  const head = ["Product", "Product ID", ...G.flatMap((k) => [`${k} P1`, `${k} P2`])];
  const lines = [head, ...cmp.products.map((p) => [p.title, p.pid, ...G.flatMap((k) => [p.w.p1[k] ?? "", p.w.p2[k] ?? ""])])];
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n")], { type: "text/csv" }));
  a.download = `Google compare ${$("store").selectedOptions[0]?.textContent || ""} ${$("p1s").value}_${$("p1e").value} vs ${$("p2s").value}_${$("p2e").value}.csv`;
  a.click();
}

/* ------------------------------------------------ columns picker */
function drawColsPicker() {
  $("cList").innerHTML = COLS.map((c) => `<label><input type="checkbox" data-col="${c.id}"${colsOn.includes(c.id) ? " checked" : ""}> ${esc(c.label)}${c.t ? ` <small class="gx-muted">${esc(c.t)}</small>` : ""}</label>`).join("");
  for (const c of $("cList").querySelectorAll("[data-col]")) c.onchange = () => {
    colsOn = COLS.map((x) => x.id).filter((id) => (id === c.dataset.col ? c.checked : colsOn.includes(id)));
    try { localStorage.setItem("pdGxCols", JSON.stringify(colsOn)); } catch { /* private */ }
    if (data && !data.error) drawAll();
  };
}

/* ------------------------------------------------ wiring */
function setView(v) {
  view = v;
  for (const b of $("view").querySelectorAll("[data-v]")) b.classList.toggle("on", b.dataset.v === v);
  $("dashView").hidden = v !== "dash";
  $("cmpView").hidden = v !== "compare";
  $("mgView").hidden = v !== "manage";
  PDUrl.set({ view: v === "dash" ? null : v });
  if (v === "compare") loadCompare();
  else if (v === "manage") window.loadManage?.();
  else if (data && !data.error) drawAll();
}
for (const b of $("view").querySelectorAll("[data-v]")) b.onclick = () => setView(b.dataset.v);
$("toCompare").onclick = () => { const [a, b] = [data.start, data.end], d = spanDays(a, b); $("p1s").value = a; $("p1e").value = b;
  $("p2e").value = addDays(a, -1); $("p2s").value = addDays(a, -d); setView("compare"); };
for (const b of document.querySelectorAll("[data-q]")) b.onclick = () => { cmpDefaults(Number(b.dataset.q)); loadCompare(); };
$("cmpGo").onclick = () => loadCompare();
for (const id of ["p1s", "p1e", "p2s", "p2e"]) $(id).onchange = () => { if ($("p1s").value && $("p1e").value && $("p2s").value && $("p2e").value) loadCompare(); };
for (const id of ["cq", "cstatus"]) $(id).oninput = () => { cpage = 0; if (cmp) drawCompare(); };
$("ccountry").onchange = () => loadCompare();
$("cper").onchange = () => { cpage = 0; if (cmp) drawCompare(); };
$("ccsv").onclick = () => cmp && cmpCsv();

for (const id of ["q", "minRoas", "maxRoas", "minDays", "minCost", "status", "verdict"]) $(id).oninput = () => { page = 0; if (data && !data.error) drawAll(); };
$("country").onchange = () => { page = 0; load(); };
$("clearF").onclick = () => { for (const id of ["q", "minRoas", "maxRoas", "minDays", "minCost", "status", "verdict"]) $(id).value = ""; tagFilter = new Set(); drawTags(); page = 0; drawAll(); };
$("per").onchange = () => { page = 0; drawAll(); };
$("store").onchange = async () => {
  picked = new Set(); tagFilter = new Set(); $("country").value = ""; $("ccountry").value = ""; page = 0; cpage = 0;
  PDUrl.set({ store: $("store").value });
  await load();
  if (view === "compare") loadCompare();
  if (view === "manage") window.loadManage?.();
};
$("refresh").onclick = () => (view === "compare" ? loadCompare(true) : load(true));
$("csv").onclick = () => data && exportCsv(sorted(filtered()));
$("period").value = period;
$("dates").hidden = period !== "custom";
$("period").onchange = () => {
  period = $("period").value;
  $("dates").hidden = period !== "custom";
  if (period === "custom" && !$("from").value) { const [a, b] = range(); $("from").value = a; $("to").value = b; }
  page = 0;
  load();
};
$("from").onchange = $("to").onchange = () => { if ($("from").value && $("to").value) load(); };
$("full").onclick = () => { $("tableCard").classList.toggle("gx-fullscreen"); document.body.classList.toggle("gx-noscroll"); };
const dd = (btn, menu, before) => { $(btn).onclick = (e) => { e.stopPropagation(); if (before) before(); $(menu).hidden = !$(menu).hidden; }; };
dd("actBtn", "actMenu", drawActMenu);
dd("tagsBtn", "tagsMenu", drawTags);
document.addEventListener("click", (e) => {
  for (const [m, b] of [["actMenu", "actBtn"], ["tagsMenu", "tagsBtn"]]) if (!$(m).hidden && !$(m).contains(e.target) && e.target !== $(b)) $(m).hidden = true;
});
$("actMenu").onclick = (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.act) act(b.dataset.act);
  else if (b.dataset.add) act("add", b.dataset.add);
  else if (b.dataset.rm) act("rm", b.dataset.rm);
};
$("manageTags").onclick = () => { drawTags(); $("tModal").hidden = false; $("tName").focus(); };
$("tAdd").onclick = async () => {
  if (!$("tName").value.trim()) return toast("Give the tag a name.", true);
  try { const t = await post("/api/googleads/tags", { store: data.store, name: $("tName").value, color: $("tColor").value });
        data.tags = t.tags; data.product_tags = t.product_tags; $("tName").value = ""; drawTags(); toast("Tag added. Tick products, then Actions → Add tag."); }
  catch (e) { toast(e.message, true); }
};
$("tName").onkeydown = (e) => { if (e.key === "Enter") $("tAdd").click(); };
$("colsBtn").onclick = () => { drawColsPicker(); $("cModal").hidden = false; };
$("cReset").onclick = () => { colsOn = DEFAULT_ON; try { localStorage.removeItem("pdGxCols"); } catch { /* private */ } drawColsPicker(); if (data && !data.error) drawAll(); };
for (const b of $("vWin").querySelectorAll("[data-w]")) b.onclick = () => {
  vWin = b.dataset.w;
  for (const x of $("vWin").querySelectorAll("[data-w]")) x.classList.toggle("on", x === b);
  drawVariants();
};
const closers = [["vModal", ["vX", "vClose"]], ["tModal", ["tX"]], ["cModal", ["cX", "cDone"]]];
for (const [m, btns] of closers) {
  for (const b of btns) $(b).onclick = () => { $(m).hidden = true; };
  $(m).onclick = (e) => { if (e.target === $(m)) $(m).hidden = true; };
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  for (const [m] of closers) $(m).hidden = true;
  $("tableCard").classList.remove("gx-fullscreen");
  document.body.classList.remove("gx-noscroll");
});

(async () => {
  await load();
  if (view !== "dash") setView(view);
})();
