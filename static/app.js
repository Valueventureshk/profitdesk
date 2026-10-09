/* ProfitDesk dashboard.

   This file draws things. It never calculates a metric — every figure comes
   from the server already worked out. */

const $ = (id) => document.getElementById(id);

const state = {
  setup: null,
  scope: "all",
  start: null,
  end: null,
  data: null,
  accounts: null,
  metaAccounts: null,
};

// Sales, ad spend and ROAS lead; the other costs and results follow.
// Costs are neutral ("none"): spending less isn't automatically good.
const CARDS = [
  { key: "sales",          label: "Total Sales",     fmt: "money", dot: "var(--sales)",  dir: "up" },
  { key: "ad_spend",       label: "Ad Spend",        fmt: "money", dot: "var(--spend)",  dir: "none" },
  { key: "roas",           label: "ROAS",            fmt: "ratio", dot: "var(--accent)", dir: "up" },
  { key: "cogs",           label: "COG",             fmt: "money", dot: "var(--spend)",  dir: "none" },
  { key: "net_profit",     label: "Net Profit",      fmt: "money", dot: "var(--up)",     dir: "up", feature: true },
  { key: "net_margin",     label: "Net Margin",      fmt: "pct",   dot: "var(--up)",     dir: "up" },
  { key: "processing_fee", label: "Processing Fees", fmt: "money", dot: "var(--spend)",  dir: "none" },
  { key: "orders",         label: "Orders",          fmt: "int",   dot: "var(--sales)",  dir: "up" },
];

/* ------------------------------------------------ formatting */

function money(v) {
  if (v === null || v === undefined) return "—";
  const cur = state.data?.currency;
  const opts = { maximumFractionDigits: 0 };
  if (cur) { opts.style = "currency"; opts.currency = cur; }
  try { return new Intl.NumberFormat(undefined, opts).format(v); }
  catch { return Math.round(v).toLocaleString(); }
}

function fmt(v, kind) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (kind === "money") return money(v);
  if (kind === "ratio") return v.toFixed(2);
  if (kind === "pct")   return (v * 100).toFixed(1) + "%";
  return Math.round(v).toLocaleString();
}

function niceDate(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, {
    month: "short", day: "numeric",
  });
}

function niceTime(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString(undefined, {
    hour: "numeric", minute: "2-digit",
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/* ------------------------------------------------ plumbing */

async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty body is fine */ }
  if (r.status === 401 && body.login) {   // logged out or session expired
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    throw new Error("Please log in again.");
  }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

function jsonPost(path, data, method = "POST") {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

let toastTimer;
function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3000);
}

function popup(url) {
  const w = 560, h = 680;
  const x = window.screenX + (window.outerWidth - w) / 2;
  const y = window.screenY + 80;
  return window.open(url, "profitdesk_connect", `width=${w},height=${h},left=${x},top=${y}`);
}

window.addEventListener("message", (e) => {
  if (e.data === "profitdesk:connected") boot();
});

/* ------------------------------------------------ dates */

/* Which clock decides what "today" is: always a store's, never this computer's.
   One store: that store's own clock, so its page matches its Shopify reports.
   All stores: the clock of whichever store is furthest ahead. With an
   Australian store connected, "today" turns over when Australia's day does;
   the server then reads that date on each store's own clock, so stores that
   haven't reached it yet show nothing until their day begins. */
function storeZone() {
  const stores = state.setup?.stores || [];
  const group = (state.setup?.groups || []).find((g) => `g${g.id}` === state.scope);
  const pool = state.scope === "all" ? stores
    : group ? stores.filter((x) => group.store_ids.includes(x.id))
    : stores.filter((x) => String(x.id) === state.scope);
  let lead, leadStamp = "";
  for (const s of pool) {
    const stamp = new Intl.DateTimeFormat("sv-SE", {  // "2026-10-07 01:23", sortable
      timeZone: s.timezone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date());
    if (stamp > leadStamp) { leadStamp = stamp; lead = s.timezone; }
  }
  return lead;
}

function isoInZone(d, tz) {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
    }).format(d);
  } catch {
    return new Intl.DateTimeFormat("en-CA").format(d);
  }
}

function shiftDays(isoDate, n) {
  const d = new Date(isoDate + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function applyRange() {
  const choice = $("rangeSelect").value;
  $("customRange").hidden = choice !== "custom";

  const todayIso = isoInZone(new Date(), storeZone());

  if (choice === "custom") {
    state.start = $("startDate").value || state.start;
    state.end = $("endDate").value || state.end;
    return;
  }
  if (choice === "today") {
    state.start = state.end = todayIso;
  } else if (choice === "yesterday") {
    state.start = state.end = shiftDays(todayIso, -1);
  } else if (choice === "mtd") {
    state.start = todayIso.slice(0, 8) + "01";
    state.end = todayIso;
  } else {
    const days = parseInt(choice, 10);
    state.start = shiftDays(todayIso, -(days - 1));
    state.end = todayIso;
  }
  $("startDate").value = state.start;
  $("endDate").value = state.end;
}

/* ------------------------------------------------ boot */

async function boot() {
  state.setup = await api("/api/setup");

  if (!state.setup.stores.length) {
    $("setup").hidden = false;
    $("app").hidden = true;
    if (!state.setup.shopify.app_configured) {
      showNotice(
        "No Shopify app is set up in this copy yet, so the Connect button cannot ask " +
        "for access. Use an access token below, or add SHOPIFY_CLIENT_ID and " +
        "SHOPIFY_CLIENT_SECRET to your .env file."
      );
    }
    return;
  }

  $("setup").hidden = true;
  $("app").hidden = false;

  const ids = [...state.setup.stores.map((s) => String(s.id)),
               ...state.setup.groups.map((g) => `g${g.id}`)];
  if (state.scope !== "all" && !ids.includes(state.scope)) state.scope = "all";

  renderNav();
  renderCurrencyPicker();
  if (!state.start) applyRange();
  await load();
}

function renderCurrencyPicker() {
  const sel = $("currencySelect");
  const chosen = state.setup.display_currency;
  sel.innerHTML = state.setup.currency_options
    .map((c) => `<option value="${esc(c)}"${c === chosen ? " selected" : ""}>${esc(c)}</option>`)
    .join("");
}

/* Notices fold into one line; click it to see them all. The open/closed
   choice is kept across refreshes so it doesn't snap shut every load. */
let warningsOpen = false;
function renderWarnings(list) {
  const box = $("warnings");
  if (!list.length) { box.innerHTML = ""; return; }
  if (list.length === 1) { box.innerHTML = `<div class="warn">${esc(list[0])}</div>`; return; }
  box.innerHTML = `
    <div class="warn warn-group${warningsOpen ? " open" : ""}">
      <button type="button" class="warn-head" aria-expanded="${warningsOpen}">
        <span class="warn-count">${list.length} notices</span>
        <span class="warn-first">${esc(list[0])}</span>
        <span class="warn-toggle">${warningsOpen ? "Hide \u25B4" : "Show all \u25BE"}</span>
      </button>
      <ul${warningsOpen ? "" : " hidden"}>${list.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
    </div>`;
  box.querySelector(".warn-head").onclick = () => { warningsOpen = !warningsOpen; renderWarnings(list); };
}

function renderFxNote(d) {
  const el = $("fxNote");
  if (!d.fx) { el.hidden = true; return; }
  const parts = Object.entries(d.fx.rates).map(([code, f]) => `${esc(code)} ${f.toFixed(4)}`);
  el.innerHTML = `In <strong>${esc(d.currency)}</strong> · ${parts.join(" · ")}` +
    (d.fx.date ? ` · rate ${esc(d.fx.date.slice(8))}/${esc(d.fx.date.slice(5, 7))}` : "");
  el.title = `1 unit of each currency in ${d.currency}, daily rate from ${d.fx.source}` +
    (d.fx.date ? `, ${d.fx.date}` : "");
  el.hidden = false;
}

function renderNav() {
  const stores = state.setup.stores;
  const parts = [];

  const groups = state.setup.groups || [];
  if (stores.length > 1) {
    parts.push(`<div class="nav-label">Blended</div>`);
    parts.push(navItem("all", "All stores", "&#9638;"));
    for (const g of groups) parts.push(navItem(`g${g.id}`, g.name, "&#9707;"));
    parts.push(`<button class="nav-item nav-sub" data-manage-groups>
                  <span class="ico">+</span><span class="txt">New group</span></button>`.replace("<button ", "<button data-owner-only "));
    parts.push(`<div class="nav-label">Stores</div>`);
  } else {
    parts.push(`<div class="nav-label">Store</div>`);
  }

  for (const s of stores) {
    parts.push(navItem(String(s.id), s.name, null));
  }
  $("storeNav").innerHTML = parts.join("");

  for (const btn of $("storeNav").querySelectorAll(".nav-item[data-scope]")) {
    btn.onclick = () => pickScope(btn.dataset.scope);
  }
  const manage = $("storeNav").querySelector("[data-manage-groups]");
  if (manage) manage.onclick = openGroupSettings;

  // Phones: blended views (All stores + groups) in one dropdown.
  const isStore = stores.some((s) => String(s.id) === state.scope);
  $("viewPick").innerHTML = [
    isStore ? `<option value="" disabled>Blended</option>` : "",
    `<option value="all">All stores</option>`,
    ...groups.map((g) => `<option value="g${g.id}">${esc(g.name)}</option>`),
    `<option value="manage">Manage groups…</option>`,
  ].join("");
  $("viewPick").value = isStore ? "" : state.scope;
  $("viewPick").hidden = stores.length < 2;

  // Phones: the All stores button sits beside this, so the dropdown lists only
  // the stores and reads "Stores" while the blended view is showing.
  const options = stores.length > 1
    ? [`<option value="all" disabled>Stores</option>`,
       ...stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`)]
    : stores.map((s) => `<option value="all">${esc(s.name)}</option>`);  // one store: it is "all"
  $("storePick").innerHTML = options.join("");
  $("storePick").value = isStore ? state.scope : "all";
}

function pickScope(scope) {
  state.scope = scope;
  renderNav();
  if ($("rangeSelect").value !== "custom") applyRange();  // "today" depends on whose clock
  load();
}

function navItem(scope, label, icon) {
  const on = state.scope === scope ? " on" : "";
  const mark = icon ? `<span class="ico">${icon}</span>` : `<span class="dot"></span>`;
  return `<button class="nav-item${on}" data-scope="${esc(scope)}">
            ${mark}<span class="txt">${esc(label)}</span>
          </button>`;
}

/* ------------------------------------------------ dashboard */

async function load(fresh = false) {
  const params = new URLSearchParams({
    scope: state.scope, start: state.start, end: state.end,
  });
  if (fresh) params.set("fresh", "1");
  if ($("rangeSelect").value === "today") params.set("compare", "same_time");

  $("viewSub").textContent = "Loading…";
  try {
    state.data = await api("/api/dashboard?" + params);
  } catch (e) {
    $("viewSub").textContent = "Could not load";
    toast(e.message, true);
    return;
  }

  const d = state.data;
  $("viewTitle").textContent = d.title;
  $("viewSub").textContent = rangeLabel(d.range);

  renderWarnings(d.warnings);

  renderFxNote(d);
  renderCards(d);
  renderChart(d.series);
  renderStoreTable(d);
}

function rangeLabel(r) {
  const choice = $("rangeSelect").value;
  if (choice === "today") {
    if (!state.data?.previous?.until) return `Today so far, ${niceDate(r.start)} · vs all of yesterday`;
    // Each store is compared against the same share of its own day; the time
    // shown is the clock that picked "today".
    const now = new Intl.DateTimeFormat("en-GB", {
      timeZone: storeZone(), hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date());
    return `Today so far, ${niceDate(r.start)} · vs yesterday up to ${niceTime(now)}`;
  }
  if (choice === "yesterday") return `Yesterday, ${niceDate(r.start)} · vs the day before`;
  if (r.days === 1) return `${niceDate(r.start)} · vs the day before`;
  return `${niceDate(r.start)} – ${niceDate(r.end)} · vs previous ${r.days} days`;
}

// Small brand marks for the cards (inline, so nothing extra to load).
const LOGO = {
  shopify: `<svg class="logo" viewBox="0 0 24 24" aria-label="Shopify"><path fill="#95BF47" d="M5 7.5h14l-1.3 12.7a1 1 0 0 1-1 .9H7.3a1 1 0 0 1-1-.9z"/><path d="M8.8 7.5V6.6a3.2 3.2 0 0 1 6.4 0v.9" fill="none" stroke="#5E8E3E" stroke-width="1.6"/><path fill="#fff" d="M13.9 11.2c-.4-.2-.9-.4-1.6-.4-1.5 0-2.4.9-2.4 1.9 0 1.9 2.6 1.8 2.6 2.9 0 .4-.4.7-.9.7-.7 0-1.4-.4-1.4-.4l-.4 1.2s.7.5 1.8.5c1.5 0 2.5-.9 2.5-2.1 0-2-2.6-1.9-2.6-2.9 0-.3.3-.6.9-.6.7 0 1.1.3 1.1.3z"/></svg>`,
  google: `<svg class="logo" viewBox="0 0 48 48" aria-label="Google"><path fill="#EA4335" d="M24 9.5c3.5 0 6.7 1.2 9.2 3.6l6.9-6.9C35.9 2.4 30.5 0 24 0 14.6 0 6.5 5.4 2.6 13.2l8 6.2C12.4 13.7 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M47 24.5c0-1.6-.2-3.1-.4-4.5H24v9h12.9c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 7.2-10.4 7.2-17.7z"/><path fill="#FBBC05" d="M10.5 28.6c-.5-1.5-.8-3-.8-4.6s.3-3.1.8-4.6l-8-6.2C.9 16.5 0 20.1 0 24s.9 7.5 2.6 10.8z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.7-6c-2.2 1.5-4.9 2.3-8.2 2.3-6.3 0-11.6-4.2-13.5-9.9l-8 6.2C6.5 42.6 14.6 48 24 48z"/></svg>`,
  meta: `<svg class="logo" viewBox="0 0 24 24" aria-label="Meta"><path d="M3 14.6c0-4 2-7.1 4.4-7.1 3.6 0 5.6 9 9.2 9 2.4 0 4.4-1.9 4.4-4.4s-1.9-4.6-4.3-4.6c-3.6 0-5.6 9-9.3 9C5 16.5 3 15.6 3 14.6z" fill="none" stroke="#0866FF" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

function renderCards(d) {
  $("cards").innerHTML = CARDS.map((c) => {
    const value = fmt(d.totals[c.key], c.fmt);
    const delta = d.delta[c.key];
    let chip = `<span class="chip flat">—</span>`;
    if (delta !== null && delta !== undefined) {
      const rising = delta >= 0;
      const tone = c.dir === "none" ? "flat" : (rising ? "up" : "down");
      const arrow = rising ? "&#9650;" : "&#9660;";
      chip = `<span class="chip ${tone}">${arrow} ${Math.abs(delta).toFixed(1)}%</span>`;
    }
    // Profit and margin turn red when they go below zero; costs stay neutral.
    const loss = (c.key === "net_profit" || c.key === "net_margin") && d.totals[c.key] < 0;
    const dot = loss ? "var(--down)" : c.dot;
    return `<div class="card${c.feature ? " feature" : ""}${loss ? " loss" : ""}">
      <div class="card-label">
        ${c.key === "sales" ? LOGO.shopify : `<span class="dot" style="background:${dot}"></span>`}${c.label}${chip}
      </div>
      <div class="card-value">${value}</div>
      ${c.key === "ad_spend" ? spendSplit(d.totals) : ""}
      ${c.key === "roas" ? roasSplit(d.platform_roas) : ""}
      ${c.key === "orders" ? `<div class="card-note">AOV ${fmt(d.totals.aov, "money")}</div>` : ""}
      ${c.key === "cogs" ? cogNote(d.totals) : ""}
      ${c.key === "processing_fee" ? `<div class="card-note">Payments ${fmt(d.totals.payment_fee, "money")}
        · Shopify ${fmt(d.totals.shopify_fee, "money")}</div>` : ""}
      ${sparkline(d.series, c.key, dot)}
    </div>`;
  }).join("") + availableCard(d);
}

// What's yours to use now from these sales: net profit minus what PayPal and
// Airwallex hold back as reserve. Figures come from the server (metrics.py).
function availableCard(d) {
  const t = d.totals;
  if (t.net_available === undefined) return "";
  const delta = d.delta.net_available;
  const chip = delta === null || delta === undefined ? `<span class="chip flat">—</span>`
    : `<span class="chip ${delta >= 0 ? "up" : "down"}">${delta >= 0 ? "&#9650;" : "&#9660;"} ${Math.abs(delta).toFixed(1)}%</span>`;
  const loss = t.net_available < 0;
  const step = (label, v) => `<span class="avail-step"><span>${label}</span><b>${fmt(v, "money")}</b></span>`;
  return `<div class="card available${loss ? " loss" : ""}">
      <div class="card-label"><span class="dot" style="background:${loss ? "var(--down)" : "var(--up)"}"></span>Net available${chip}</div>
      <div class="card-value">${fmt(t.net_available, "money")}</div>
      <div class="avail-flow">
        ${step("Sales", t.sales)}<i>−</i>${step("Fees", t.processing_fee)}<i>−</i>${step("COG", t.cogs)}<i>−</i>${step("Ads", t.ad_spend)}<i>−</i>${step("Held in reserve", t.reserve_held)}
      </div>
      <div class="card-note">What's yours to use now from these sales. ${fmt(t.reserve_held, "money")} is held back
        (PayPal 21% for 60 days, plus all PayPal sales over A$35,160 in a month for 21 days; Airwallex 10% for ~90 days) and comes back later. This card doesn't change any other figure.</div>
    </div>`;
}

// ROAS per platform, by store (each store counted under the one platform it uses).
function roasSplit(p) {
  if (!p) return "";
  const line = (key, label) => p[key].stores
    ? `<div title="${p[key].stores} store${p[key].stores === 1 ? "" : "s"} on ${label}: ${fmt(p[key].sales, "money")} sales ÷ ${fmt(p[key].ad_spend, "money")} spend">${LOGO[key]}<span>${label}</span><b>${fmt(p[key].roas, "ratio")}</b></div>`
    : "";
  const rows = line("google", "Google") + line("meta", "Meta");
  return rows ? `<div class="spend-split">${rows}</div>` : "";
}

function cogNote(t) {
  const share = `${fmt(t.cogs_share, "pct")} of sales`;
  const est = t.cogs_estimated >= 0.5
    ? ` · <span class="est" title="Products with no cost history yet, costed at the store's average COG %">${fmt(t.cogs_estimated, "money")} estimated</span>` : "";
  return `<div class="card-note">${share}${est}</div>`;
}

function spendSplit(t) {
  return `<div class="spend-split">
      <div>${LOGO.google}<span>Google</span><b>${fmt(t.google_spend, "money")}</b></div>
      <div>${LOGO.meta}<span>Meta</span><b>${fmt(t.meta_spend, "money")}</b></div>
    </div>`;
}

function sparkline(series, key, colour) {
  const values = series.map((r) => (typeof r[key] === "number" ? r[key] : 0));
  if (values.length < 2) return `<div class="spark"></div>`;

  const lo = Math.min(...values, 0);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const step = 100 / (values.length - 1);

  const points = values
    .map((v, i) => `${(i * step).toFixed(2)},${(28 - ((v - lo) / span) * 26).toFixed(2)}`)
    .join(" ");

  return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none">
    <polyline points="${points}" fill="none" stroke="${colour}"
      stroke-width="1.6" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function renderChart(series) {
  if (!series.length) {
    $("chart").innerHTML = `<div class="empty">Nothing in this date range.</div>`;
    return;
  }
  if (series.length === 1) {
    $("chart").innerHTML = `<div class="empty">This is a single day, so there is no trend
      line to draw. Pick 7 days or longer to see one.</div>`;
    return;
  }

  const W = 1000, H = 240, padL = 56, padR = 12, padT = 14, padB = 26;
  const innerW = W - padL - padR, innerH = H - padT - padB;

  const peak = Math.max(...series.map((r) => Math.max(r.sales, r.ad_spend)), 1);
  const x = (i) => padL + (series.length === 1 ? innerW / 2 : (i / (series.length - 1)) * innerW);
  const y = (v) => padT + innerH - (v / peak) * innerH;

  const path = (key) => series.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r[key]).toFixed(1)}`).join("");

  const gridCount = 4;
  let grid = "";
  for (let i = 0; i <= gridCount; i++) {
    const v = (peak / gridCount) * i;
    const gy = y(v).toFixed(1);
    grid += `<line class="grid-line" x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}"/>
             <text class="axis-text" x="${padL - 8}" y="${gy}" text-anchor="end"
                   dominant-baseline="middle">${money(v)}</text>`;
  }

  const ticks = [0, Math.floor((series.length - 1) / 2), series.length - 1];
  const labels = [...new Set(ticks)].map((i) =>
    `<text class="axis-text" x="${x(i).toFixed(1)}" y="${H - 6}"
           text-anchor="${i === 0 ? "start" : i === series.length - 1 ? "end" : "middle"}"
     >${niceDate(series[i].date)}</text>`).join("");

  const area = `M${x(0).toFixed(1)},${(padT + innerH).toFixed(1)}`
    + series.map((r, i) => `L${x(i).toFixed(1)},${y(r.sales).toFixed(1)}`).join("")
    + `L${x(series.length - 1).toFixed(1)},${(padT + innerH).toFixed(1)}Z`;

  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs>
      <linearGradient id="salesFade" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%"   stop-color="var(--sales)" stop-opacity=".16"/>
        <stop offset="100%" stop-color="var(--sales)" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${grid}
    <path class="area-sales" d="${area}"/>
    <path class="line-sales" d="${path("sales")}"/>
    <path class="line-spend" d="${path("ad_spend")}"/>
    ${labels}
  </svg>`;
}

function linkedAccounts(s) {
  const meta = !!s.meta_account_name, google = !!s.google_account_name;
  if (meta && google) return "Meta + Google Ads connected";
  if (meta) return "Meta connected";
  if (google) return "Google Ads connected";
  return "No ad account linked";
}

function renderStoreTable(d) {
  const panel = $("storesPanel");
  if (!d.stores.length) { panel.hidden = true; return; }
  panel.hidden = false;

  // Biggest seller first. Order only; the figures themselves come from the server.
  const ranked = [...d.stores].sort((a, b) =>
    (b.totals.sales || 0) - (a.totals.sales || 0) || a.name.localeCompare(b.name));
  const rows = ranked.map((s) => {
    const t = s.totals;
    const profitClass = t.net_profit >= 0 ? "pos" : "neg";
    return `<tr>
      <td class="name">${esc(s.name)}
        <span class="sub">${esc(linkedAccounts(s))}</span></td>
      <td>${fmt(t.sales, "money")}</td>
      <td>${fmt(t.ad_spend, "money")}</td>
      <td class="roas">${fmt(t.roas, "ratio")}</td>
      <td>${fmt(t.processing_fee, "money")}</td>
      <td>${fmt(t.cogs_share, "pct")}</td>
      <td>${fmt(t.aov, "money")}</td>
      <td class="${profitClass}">${fmt(t.net_profit, "money")}</td>
      <td>${fmt(t.net_margin, "pct")}</td>
      <td>${fmt(t.orders, "int")}</td>
    </tr>`;
  }).join("");

  $("storeTable").innerHTML = `
    <thead><tr>
      <th>Store</th><th>Total sales</th><th>Ad spend</th><th>ROAS</th><th>Fees</th>
      <th title="Cost of goods as a % of sales">COG %</th><th>AOV</th>
      <th>Net profit</th><th>Margin</th><th>Orders</th>
    </tr></thead>
    <tbody>${rows}</tbody>
    ${ranked.length > 1 ? storeTotalsRow(d.totals) : ""}`;
}

// The same totals as the cards above, so the bottom line always agrees with them.
function storeTotalsRow(t) {
  return `<tfoot><tr>
      <td class="name">Total<span class="sub">All stores shown</span></td>
      <td>${fmt(t.sales, "money")}</td>
      <td>${fmt(t.ad_spend, "money")}</td>
      <td class="roas">${fmt(t.roas, "ratio")}</td>
      <td>${fmt(t.processing_fee, "money")}</td>
      <td title="Cost of goods as a share of total sales">${fmt(t.cogs_share, "pct")}</td>
      <td>${fmt(t.aov, "money")}</td>
      <td class="${t.net_profit >= 0 ? "pos" : "neg"}">${fmt(t.net_profit, "money")}</td>
      <td>${fmt(t.net_margin, "pct")}</td>
      <td>${fmt(t.orders, "int")}</td>
    </tr></tfoot>`;
}

/* ------------------------------------------------ connecting stores */

function showNotice(message, ok = false) {
  const el = $("setupNotice");
  el.textContent = message;
  el.className = "notice" + (ok ? " ok" : "");
  el.hidden = false;
}

function connectShopify(domain) {
  const value = (domain || "").trim();
  if (!value) { toast("Type your store address first.", true); return; }
  popup("/auth/shopify/start?shop_domain=" + encodeURIComponent(value));
}

async function connectWithApp() {
  const body = {
    shop_domain: $("newDomain").value,
    client_id: $("newClientId").value,
    client_secret: $("newClientSecret").value,
    install_link: $("newInstallLink").value,
  };
  if (!body.shop_domain.trim()) { toast("Type the store address first.", true); return; }
  // Open the window now, while the click still counts, or the browser blocks it.
  const win = popup("about:blank");
  try {
    const r = await jsonPost("/api/shopify/apps", body);
    if (win) win.location = r.open; else popup(r.open);
    for (const id of ["newClientId", "newClientSecret", "newInstallLink"]) $(id).value = "";
    toast("Approve ProfitDesk in the Shopify window to finish connecting.");
  } catch (e) {
    if (win) win.close();
    toast(e.message, true);
  }
}

async function connectWithToken(domain, token) {
  if (!domain.trim() || !token.trim()) {
    toast("Both the store address and the token are needed.", true);
    return;
  }
  try {
    const r = await jsonPost("/api/stores/token", {
      shop_domain: domain, access_token: token,
    });
    toast(`${r.name} connected.`);
    await boot();
    return true;
  } catch (e) {
    toast(e.message, true);
    return false;
  }
}

/* ------------------------------------------------ settings drawer */

// Settings as tidy groups of expandable items, each with its logo.
const SET_ICONS = {
  shopify: LOGO.shopify, google: LOGO.google, meta: LOGO.meta,
  gmail: `<svg class="logo" viewBox="0 0 24 24"><path fill="#4285F4" d="M2 6.5V18a1.5 1.5 0 0 0 1.5 1.5H6V10l6 4.5 6-4.5v9.5h2.5A1.5 1.5 0 0 0 22 18V6.5l-2.4-1.8L12 10.4 4.4 4.7z"/><path fill="#EA4335" d="M6 10v9.5h0V10l6 4.5 6-4.5L12 10.4 4.4 4.7 2 6.5z" opacity=".9"/><path fill="#34A853" d="M18 10v9.5h2.5A1.5 1.5 0 0 0 22 18V6.5z"/><path fill="#FBBC05" d="M2 6.5V18a1.5 1.5 0 0 0 1.5 1.5H6V10z"/></svg>`,
  ai: `<svg class="logo" viewBox="0 0 24 24"><path fill="#D97757" d="M12 2l1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9z"/><path fill="#D97757" d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" opacity=".7"/></svg>`,
  people: `<svg class="logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5"/><circle cx="17.5" cy="9" r="2.6"/><path d="M16 14.6c2.6-.2 4.9 1.4 5.5 4.4"/></svg>`,
  fees: `<svg class="logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="5.5" width="19" height="13" rx="2.5"/><path d="M2.5 10h19M6.5 15h4"/></svg>`,
  groups: `<svg class="logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></svg>`,
  backup: `<svg class="logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 18.5h10.5a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.5 9.5 4.5 4.5 0 0 0 7 18.5z"/><path d="M12 10v6m-2.5-2.5L12 16l2.5-2.5"/></svg>`,
  stores: `<svg class="logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 9.5V20h16V9.5"/><path d="M3 9.5 5 4h14l2 5.5a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0z"/><path d="M10 20v-5h4v5"/></svg>`,
};
const SET_META = {
  "Your stores": ["Connections", "stores", "Each store's Google tax, linked Google and Meta ad accounts"],
  "Add a Shopify store": ["Connections", "shopify", "Connect another Shopify store with its own app"],
  "Google Ads": ["Connections", "google", "Sign in with Google for ad spend and the spend sheet"],
  "Meta Ads": ["Connections", "meta", "Meta business access tokens for ad spend"],
  "Support mailboxes": ["Connections", "gmail", "Each store's support inbox for the Inbox desk"],
  "AI": ["Connections", "ai", "Claude: sorts emails, drafts replies, answers the chat"],
  "Processing fees": ["Money", "fees", "PayPal, Airwallex and Afterpay rates used for estimates"],
  "Store groups": ["Money", "groups", "Group stores to see them together on the dashboard"],
  "People": ["Team", "people", "Who can log in, their access level and desks"],
  "Backup": ["Data", "backup", "Download or restore everything in one file"],
};
let settingsOrganised = false;

function organiseSettings() {
  if (settingsOrganised) return;
  settingsOrganised = true;
  const body = document.querySelector("#drawer .drawer-body");
  const groups = {};
  for (const sec of [...body.querySelectorAll(":scope > section.block")]) {
    const h = sec.querySelector(":scope > h3");
    const name = h ? h.textContent.trim() : "";
    const [group, icon, sub] = SET_META[name] || ["Other", "stores", ""];
    const inner = document.createElement("div");
    inner.className = "set-inner";
    while (h.nextSibling) inner.appendChild(h.nextSibling);
    const wrap = document.createElement("div");
    wrap.className = "set-body";
    wrap.appendChild(inner);
    const head = document.createElement("button");
    head.type = "button";
    head.className = "set-head";
    head.innerHTML = `<span class="set-ico">${SET_ICONS[icon] || ""}</span>
      <span class="set-txt"><strong>${esc(name)}</strong><small>${esc(sub)}</small></span><span class="set-chev">›</span>`;
    head.onclick = () => sec.classList.toggle("open");
    h.replaceWith(head);
    sec.appendChild(wrap);
    sec.classList.add("set-item");
    sec.dataset.name = name;
    (groups[group] = groups[group] || []).push(sec);
  }
  for (const g of ["Connections", "Money", "Team", "Data", "Other"]) {
    if (!groups[g]) continue;
    const label = document.createElement("div");
    label.className = "set-group";
    label.textContent = g;
    body.appendChild(label);
    const order = Object.keys(SET_META);
    groups[g].sort((a, b) => order.indexOf(a.dataset.name) - order.indexOf(b.dataset.name));
    for (const sec of groups[g]) body.appendChild(sec);
  }
}

function openSettingsItem(id) {
  const sec = document.getElementById(id);
  if (sec) { sec.classList.add("open"); sec.scrollIntoView({ block: "start", behavior: "smooth" }); }
}

async function openSettings() {
  organiseSettings();
  $("drawer").hidden = false;
  renderGoogleBox();
  renderMetaBox();
  renderGroupSettings();
  renderFeeSettings();
  renderPeople();
  renderMailboxes();
  renderAI();
  renderStoreSettings();
  await Promise.all([loadAccounts(), loadMetaAccounts()]);
}

function closeSettings() { $("drawer").hidden = true; }

async function openGroupSettings() {
  const pending = openSettings();
  openSettingsItem("groupsBlock");
  await pending;
}

/* ------------------------------------------------ people */

const LEVELS = { owner: "Owner (everything, incl. Settings)", write: "Read & write", read: "Read only" };
const DESK_NAMES = { profit: "Profit dashboard", cash: "Cash flow", cog: "COG + Products", inbox: "Inbox", reports: "Reports" };

function accessFields(prefix, role = "read", desks = ["profit"]) {
  return `<label class="field"><span>Access level</span>
      <select id="${prefix}Role" class="control">${Object.entries(LEVELS).map(([k, v]) =>
        `<option value="${k}"${k === role ? " selected" : ""}>${v}</option>`).join("")}</select></label>
    <div class="field" id="${prefix}DeskBox"${role === "owner" ? " hidden" : ""}><span>Desks they can open</span>
      <div class="desk-picks">${Object.entries(DESK_NAMES).map(([k, v]) => `
        <label><input type="checkbox" data-desk="${k}"${desks.includes(k) ? " checked" : ""}> ${v}</label>`).join("")}</div></div>`;
}

function readAccess(prefix) {
  const role = $(prefix + "Role").value;
  const desks = [...document.querySelectorAll(`#${prefix}DeskBox [data-desk]`)]
    .filter((c) => c.checked).map((c) => c.dataset.desk);
  return { role, desks };
}

function wireAccess(prefix) {
  $(prefix + "Role").onchange = () => { $(prefix + "DeskBox").hidden = $(prefix + "Role").value === "owner"; };
}

async function renderPeople() {
  let r;
  try { r = await api("/api/users"); } catch (e) { toast(e.message, true); return; }
  const rows = r.users.map((u) => `
    <div class="person">
      <div class="group-row">
        <div class="group-name"><strong>${esc(u.name || u.email)}${u.id === r.me.id ? " (you)" : ""}</strong>
          <span>${esc(u.email)} · ${esc((LEVELS[u.role] || u.role).split(" (")[0])}${u.role === "owner" ? ""
            : " · " + u.desks.map((d) => DESK_NAMES[d] || d).join(", ")}${u.must_change ? " · hasn't logged in yet" : ""}</span></div>
        <button class="btn btn-sm btn-ghost" data-edit-user="${u.id}">Edit</button>
        ${u.id === r.me.id ? "" : `<button class="btn btn-sm btn-danger" data-remove-user="${u.id}">Remove</button>`}
      </div>
      <div class="person-edit" id="edit${u.id}" hidden></div>
    </div>`).join("");

  $("peopleBox").innerHTML = `${rows}
    <details class="advanced"><summary>Add a person</summary>
      <label class="field"><span>Name</span><input id="puName" type="text"></label>
      <label class="field"><span>Email (they log in with this)</span><input id="puEmail" type="email" autocomplete="off"></label>
      <label class="field"><span>Temporary password (10+ characters)</span>
        <input id="puPass" type="text" autocomplete="off"></label>
      ${accessFields("pu")}
      <p class="hint">Send them the email and temporary password yourself. At their first login
        they have to choose their own password before they can see anything.</p>
      <button id="puAdd" class="btn btn-primary btn-block">Add person</button>
    </details>
    <details class="advanced"><summary>Change my password</summary>
      <label class="field"><span>Current password</span>
        <input id="pwOld" type="password" autocomplete="current-password"></label>
      <label class="field"><span>New password (10+ characters)</span>
        <input id="pwNew" type="password" autocomplete="new-password"></label>
      <button id="pwSave" class="btn btn-ghost btn-block">Change password</button>
    </details>`;
  wireAccess("pu");

  $("puAdd").onclick = async () => {
    try {
      await jsonPost("/api/users", { name: $("puName").value, email: $("puEmail").value,
                                     password: $("puPass").value, ...readAccess("pu") });
      toast("Added. Send them their email and temporary password.");
      renderPeople();
    } catch (e) { toast(e.message, true); }
  };
  $("pwSave").onclick = async () => {
    try {
      await jsonPost("/api/me/password", { current: $("pwOld").value, new: $("pwNew").value });
      toast("Password changed. Other devices have been signed out.");
      renderPeople();
    } catch (e) { toast(e.message, true); }
  };
  for (const b of $("peopleBox").querySelectorAll("[data-edit-user]")) {
    b.onclick = () => {
      const u = r.users.find((x) => String(x.id) === b.dataset.editUser);
      const box = $("edit" + u.id);
      if (!box.hidden) { box.hidden = true; return; }
      const p = "ed" + u.id;
      box.innerHTML = `${accessFields(p, u.role, u.desks)}
        <label class="field"><span>New temporary password (only to reset it)</span>
          <input id="${p}Pass" type="text" autocomplete="off" placeholder="Leave empty to keep their password"></label>
        <button class="btn btn-primary btn-block" id="${p}Save">Save</button>`;
      box.hidden = false;
      wireAccess(p);
      $(p + "Save").onclick = async () => {
        try {
          await jsonPost(`/api/users/${u.id}`, { ...readAccess(p), password: $(p + "Pass").value || undefined }, "PUT");
          toast($(p + "Pass").value ? "Saved. They'll choose a new password at next login." : "Access saved.");
          renderPeople();
        } catch (e) { toast(e.message, true); }
      };
    };
  }
  for (const b of $("peopleBox").querySelectorAll("[data-remove-user]")) {
    b.onclick = async () => {
      const u = r.users.find((x) => String(x.id) === b.dataset.removeUser);
      if (!confirm(`Remove ${u.email}? They won't be able to log in any more.`)) return;
      await api(`/api/users/${u.id}`, { method: "DELETE" });
      toast(`${u.email} removed.`);
      renderPeople();
    };
  }
}

/* ------------------------------------------------ AI */

async function renderAI() {
  let r;
  try { r = await api("/api/ai"); } catch { $("aiBox").innerHTML = ""; return; }
  const last = r.last ? `Last run: ${r.last.sorted} emails sorted, ${r.last.tickets} new tickets,
    ${r.last.replies} replies matched.` : "";
  $("aiBox").innerHTML = `
    <div class="group-row"><div class="group-name"><strong>${r.has_key ? "Connected" : "Not connected yet"}</strong>
      <span>Model ${esc(r.model)} · tickets from ${new Date(r.tickets_from).toLocaleDateString(undefined,
        { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Hong_Kong" })} (Hong Kong time) on · ${last}</span>
      ${r.error ? `<span class="neg">${esc(r.error)}</span>` : ""}</div></div>
    <details class="advanced"${r.has_key ? "" : " open"}><summary>${r.has_key ? "Replace the API key" : "Add the API key"}</summary>
      <label class="field"><span>Anthropic API key (starts with sk-ant-)</span>
        <input id="aiKey" type="password" autocomplete="off"></label>
      <p class="hint">console.anthropic.com → API keys → Create key. Add a little credit under Billing
        (US$5 lasts months at your volume).</p>
      <button id="aiSave" class="btn btn-primary btn-block">Save key</button>
    </details>`;
  $("aiSave").onclick = async () => {
    try {
      await jsonPost("/api/ai", { api_key: $("aiKey").value }, "PUT");
      toast("Saved. Sorting starts now; tickets appear in the Inbox within a few minutes.");
      renderAI();
    } catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------ support mailboxes */

async function renderMailboxes() {
  let r;
  try { r = await api("/api/mail-accounts"); } catch (e) { $("mailBox").innerHTML = ""; return; }
  const when = (t) => (t ? new Date(t.replace(" ", "T") + "Z").toLocaleString(undefined,
    { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "not yet");
  const rows = r.accounts.map((a) => `
    <div class="group-row">
      <div class="group-name"><strong>${esc(a.address)}</strong>
        <span>${esc(a.store || "No store")} · ${a.emails} emails · checked ${when(a.last_checked)}</span>
        ${a.error ? `<span class="neg">${esc(a.error)}</span>` : ""}</div>
      <button class="btn btn-sm btn-danger" data-remove-mail="${a.id}">Remove</button>
    </div>`).join("");
  const stores = (state.setup?.stores || []).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  $("mailBox").innerHTML = `${rows || `<p class="hint">No mailboxes connected yet.</p>`}
    <details class="advanced"${r.accounts.length ? "" : " open"}><summary>Connect a mailbox</summary>
      <label class="field"><span>Store</span><select id="mbStore" class="control">${stores}</select></label>
      <label class="field"><span>Mailbox email</span><input id="mbAddress" type="email" autocomplete="off"
        placeholder="support@yourstore.com"></label>
      <label class="field"><span>App password (16 letters from Google)</span>
        <input id="mbPass" type="password" autocomplete="off"></label>
      <p class="hint">Google Workspace / Gmail: sign in to the mailbox → myaccount.google.com/apppasswords →
        name it "ProfitDesk" → Create, and paste the 16 letters here.</p>
      <button id="mbAdd" class="btn btn-primary btn-block">Connect mailbox</button>
    </details>`;
  $("mbAdd").onclick = async () => {
    const b = $("mbAdd");
    b.disabled = true;
    b.textContent = "Checking the mailbox…";
    try {
      const res = await jsonPost("/api/mail-accounts", { store_id: $("mbStore").value,
        address: $("mbAddress").value, password: $("mbPass").value });
      toast("Connected. The last 30 days of emails are being copied in; open the Inbox in a minute.");
      renderMailboxes();
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "Connect mailbox"; }
  };
  for (const b of $("mailBox").querySelectorAll("[data-remove-mail]")) {
    b.onclick = async () => {
      if (!confirm("Disconnect this mailbox? Its emails are removed from ProfitDesk (the mailbox itself isn't touched).")) return;
      await api(`/api/mail-accounts/${b.dataset.removeMail}`, { method: "DELETE" });
      renderMailboxes();
    };
  }
}

/* ------------------------------------------------ processing fee rates */

const FEE_FIELDS = [
  ["PayPal", [["paypal_pct", "% per payment"], ["paypal_fx_pct", "% to convert to HKD"]]],
  ["Airwallex cards, Apple Pay, Google Pay", [["awx_card_pct", "% per payment"], ["awx_card_fixed_hkd", "HK$ per payment"]]],
  ["Afterpay (through Airwallex)", [["awx_afterpay_pct", "% per payment"], ["awx_afterpay_fixed_hkd", "HK$ per payment"]]],
  ["Airwallex currency conversion", [["awx_fx_pct", "% to convert to HKD"]]],
];

function renderFeeSettings() {
  const r = state.setup.fee_rates;
  const used = [...new Set(state.setup.stores.map((s) => s.currency))].filter((c) => c !== "HKD");
  const plans = state.setup.stores.map((s) => {
    const pct = state.setup.shopify_fee_pct[s.shopify_plan];
    return `<li><span>${esc(s.name)}</span><small>${esc(s.shopify_plan || "plan not checked yet")}
      · ${pct === undefined ? "—" : pct + "%"}</small></li>`;
  }).join("");

  $("feeSettings").innerHTML = `
    ${FEE_FIELDS.map(([title, fields]) => `
      <div class="fee-group"><div class="fee-title">${esc(title)}</div>
        ${fields.map(([k, label]) => `<label class="inline-field">
          <input type="number" min="0" step="0.01" data-fee="${k}" value="${r[k]}">
          <span class="suffix">${label}</span></label>`).join("")}
        ${title === "PayPal" ? `<div class="fee-fixed">Fixed fee per payment:
          ${used.map((c) => `<label><input type="number" min="0" step="0.01"
             data-fixed="${c}" value="${r.paypal_fixed[c] ?? ""}"> ${c}</label>`).join("")}</div>` : ""}
      </div>`).join("")}
    <div class="fee-group"><div class="fee-title">Shopify's fee for using PayPal / Airwallex</div>
      <p class="hint">Set automatically from each store's Shopify plan: Basic 2%, Shopify 1%, Advanced 0.6%.</p>
      <ul class="fee-plans">${plans}</ul></div>
    <button id="saveFees" class="btn btn-primary btn-block">Save fee rates</button>`;

  $("saveFees").onclick = async () => {
    const body = { paypal_fixed: {} };
    for (const i of $("feeSettings").querySelectorAll("[data-fee]")) body[i.dataset.fee] = i.value;
    for (const i of $("feeSettings").querySelectorAll("[data-fixed]")) {
      if (i.value !== "") body.paypal_fixed[i.dataset.fixed] = i.value;
    }
    try {
      await jsonPost("/api/fee-rates", body, "PUT");
      toast("Fee rates saved.");
      state.setup = await api("/api/setup");
      renderFeeSettings();
      load(true);
    } catch (e) {
      toast(e.message, true);
    }
  };
}

/* ------------------------------------------------ store groups */

let editingGroup = null;  // group id being edited, "new", or null

function renderGroupSettings() {
  const groups = state.setup.groups || [];
  const rows = groups.map((g) => editingGroup === g.id ? groupEditor(g) : `
    <div class="group-row">
      <div class="group-name"><strong>${esc(g.name)}</strong>
        <span>${g.store_ids.length} store${g.store_ids.length === 1 ? "" : "s"}</span></div>
      <button class="btn btn-sm btn-ghost" data-edit-group="${g.id}">Edit</button>
    </div>`).join("");

  $("groupSettings").innerHTML = rows + (editingGroup === "new"
    ? groupEditor({ id: "new", name: "", store_ids: [] })
    : `<button id="newGroup" class="btn btn-ghost btn-block">+ New group</button>`);

  const add = $("newGroup");
  if (add) add.onclick = () => { editingGroup = "new"; renderGroupSettings(); };
  for (const b of $("groupSettings").querySelectorAll("[data-edit-group]")) {
    b.onclick = () => { editingGroup = Number(b.dataset.editGroup); renderGroupSettings(); };
  }
  const ed = $("groupSettings").querySelector(".group-editor");
  if (ed) wireGroupEditor(ed);
}

function groupEditor(g) {
  const stores = state.setup.stores;
  const boxes = stores.map((s) => {
    const tags = [s.meta_account_id ? "Meta" : "", s.google_customer_id ? "Google" : ""]
      .filter(Boolean).join(" + ") || "no ads linked";
    return `<label class="group-store">
      <input type="checkbox" value="${s.id}"${g.store_ids.includes(s.id) ? " checked" : ""}>
      <span>${esc(s.name)}</span><small>${tags}</small></label>`;
  }).join("");
  return `<div class="group-editor" data-group="${g.id}">
    <label class="field"><span>Group name</span>
      <input class="group-name-input" type="text" maxlength="60"
             placeholder="e.g. Meta stores" value="${esc(g.name)}"></label>
    <div class="group-quick">Tick:
      <button type="button" class="link" data-tick="meta">stores with Meta</button>
      <button type="button" class="link" data-tick="google">with Google</button>
      <button type="button" class="link" data-tick="both">with both</button>
      <button type="button" class="link" data-tick="none">none</button>
    </div>
    <div class="group-stores">${boxes}</div>
    <div class="row-actions">
      <button class="btn btn-sm btn-primary" data-save>${g.id === "new" ? "Create group" : "Save"}</button>
      <button class="btn btn-sm btn-ghost" data-cancel>Cancel</button>
      ${g.id === "new" ? "" : `<button class="btn btn-sm btn-danger" data-delete>Delete group</button>`}
    </div>
  </div>`;
}

function wireGroupEditor(ed) {
  const id = ed.dataset.group;
  const byId = Object.fromEntries(state.setup.stores.map((s) => [String(s.id), s]));
  const boxes = [...ed.querySelectorAll(".group-stores input")];

  for (const b of ed.querySelectorAll("[data-tick]")) {
    b.onclick = () => {
      for (const box of boxes) {
        const s = byId[box.value];
        const meta = !!s.meta_account_id, google = !!s.google_customer_id;
        box.checked = { meta, google, both: meta && google, none: false }[b.dataset.tick];
      }
    };
  }

  ed.querySelector("[data-cancel]").onclick = () => { editingGroup = null; renderGroupSettings(); };

  ed.querySelector("[data-save]").onclick = async () => {
    const body = {
      name: ed.querySelector(".group-name-input").value,
      store_ids: boxes.filter((b) => b.checked).map((b) => Number(b.value)),
    };
    try {
      if (id === "new") await jsonPost("/api/groups", body);
      else await jsonPost(`/api/groups/${id}`, body, "PUT");
      toast(`Group ${body.name.trim()} saved.`);
      editingGroup = null;
      await boot();
      renderGroupSettings();
    } catch (e) {
      toast(e.message, true);
    }
  };

  const del = ed.querySelector("[data-delete]");
  if (del) del.onclick = async () => {
    const g = state.setup.groups.find((x) => String(x.id) === id);
    if (!confirm(`Delete the group ${g.name}? The stores themselves stay connected.`)) return;
    await api(`/api/groups/${id}`, { method: "DELETE" });
    toast(`Group ${g.name} deleted.`);
    editingGroup = null;
    await boot();
    renderGroupSettings();
  };
}

function renderGoogleBox() {
  const g = state.setup.google;
  if (!g.configured) {
    $("googleBox").innerHTML = `
      <p class="hint">Paste the OAuth client from Google Cloud Console → Google Auth
        Platform → Clients. Its redirect URI must be <code>${esc(g.redirect_uri)}</code></p>
      <label class="field"><span>Client ID</span>
        <input id="gClientId" type="text" placeholder="…apps.googleusercontent.com" autocomplete="off"></label>
      <label class="field"><span>Client secret</span>
        <input id="gClientSecret" type="password" placeholder="GOCSPX-…" autocomplete="off"></label>
      <button id="gSave" class="btn btn-primary btn-block">Save Google keys</button>`;
    $("gSave").onclick = async () => {
      try {
        await jsonPost("/api/google/app", {
          client_id: $("gClientId").value, client_secret: $("gClientSecret").value,
        });
        toast("Google keys saved. Now sign in with Google.");
        state.setup = await api("/api/setup");
        renderGoogleBox();
      } catch (e) {
        toast(e.message, true);
      }
    };
    return;
  }
  if (!g.connected) {
    $("googleBox").innerHTML =
      `<button id="googleConnect" class="btn btn-primary btn-block">Sign in with Google</button>`;
    $("googleConnect").onclick = () => popup("/auth/google/start");
    return;
  }
  $("googleBox").innerHTML = `
    <div class="google-state">
      <div class="who"><strong>Connected</strong><span>${esc(g.email || "Google account")}</span></div>
      <button id="googleAgain" class="btn btn-sm btn-ghost" title="Renew the Google sign-in; store links stay as they are">Sign in again</button>
      <button id="googleOff" class="btn btn-sm btn-danger">Disconnect</button>
    </div>
    <label class="field" style="margin-top:10px"><span>Spend Sheet (filled by the Google Ads script)</span>
      <input id="gSheet" type="text" autocomplete="off"
        placeholder="https://docs.google.com/spreadsheets/d/…"
        value="${g.sheet_id ? `https://docs.google.com/spreadsheets/d/${esc(g.sheet_id)}` : ""}"></label>
    <button id="gSheetSave" class="btn btn-ghost btn-block">Save Sheet link</button>
    <p class="hint">Until Google approves direct access, a small script in each Google Ads
      account copies its spend into this Sheet every hour. The script is in the ProfitDesk
      folder as <code>google-ads-script.js</code>.</p>`;
  $("googleAgain").onclick = () => popup("/auth/google/start");
  $("gSheetSave").onclick = async () => {
    try {
      await jsonPost("/api/google/sheet", { url: $("gSheet").value });
      toast("Sheet saved.");
      state.setup = await api("/api/setup");
      state.accounts = null;
      renderGoogleBox();
      await loadAccounts();
      load(true);
    } catch (e) {
      toast(e.message, true);
    }
  };
  $("googleOff").onclick = async () => {
    await api("/api/google/disconnect", { method: "POST" });
    state.accounts = null;
    toast("Google disconnected.");
    await boot();
    renderGoogleBox();
    renderStoreSettings();
  };
}

function renderMetaBox() {
  const m = state.setup.meta;
  const tokenForm = `
    <label class="field">
      <span>System User access token</span>
      <input id="metaToken" type="password" placeholder="EAA..." autocomplete="off">
    </label>
    <button id="metaConnect" class="btn ${m.connected ? "btn-ghost" : "btn-primary"} btn-block">
      ${m.connected ? "Connect this business" : "Connect Meta Ads"}</button>`;

  const rows = m.connections.map((c) => `
    <div class="google-state meta-conn">
      <div class="who"><strong>Connected</strong><span>${esc(c.label)}</span></div>
      <button class="btn btn-sm btn-danger" data-meta-off="${c.id}">Disconnect</button>
    </div>`).join("");

  $("metaBox").innerHTML = m.connected
    ? `${rows}
       <details class="advanced">
         <summary>Add another Meta business</summary>
         <p class="hint">For a store whose ad account lives in a different business.
           Make a ProfitDesk system user and token in <strong>that</strong> business,
           the same way as the first, and paste it here.</p>
         ${tokenForm}
       </details>`
    : tokenForm;

  $("metaConnect").onclick = async () => {
    const token = $("metaToken").value.trim();
    if (!token) { toast("Paste the access token first.", true); return; }
    $("metaConnect").disabled = true;
    $("metaConnect").textContent = "Checking with Meta…";
    try {
      const r = await jsonPost("/api/meta/token", { access_token: token });
      toast(`Connected ${r.name} · ${r.accounts} ad account${r.accounts === 1 ? "" : "s"}.`);
      state.setup = await api("/api/setup");
      renderMetaBox();
      await loadMetaAccounts();
    } catch (e) {
      toast(e.message, true);
      renderMetaBox();
    }
  };

  for (const btn of $("metaBox").querySelectorAll("[data-meta-off]")) {
    btn.onclick = async () => {
      const c = m.connections.find((x) => String(x.id) === btn.dataset.metaOff);
      if (!confirm(`Disconnect ${c.label}? Stores using its ad accounts stop showing `
                 + `Meta spend until you connect it again.`)) return;
      await api(`/api/meta/connections/${c.id}`, { method: "DELETE" });
      state.metaAccounts = null;
      toast(`${c.label} disconnected.`);
      await boot();
      renderMetaBox();
      renderStoreSettings();
      await loadMetaAccounts();
    };
  }
}

async function loadMetaAccounts() {
  if (!state.setup.meta.connected) return;
  try {
    const r = await api("/api/meta/accounts");
    state.metaAccounts = r.accounts;
    if (r.problems.length) toast(r.problems.join(" "), true);
    renderStoreSettings();
  } catch (e) {
    toast(e.message, true);
  }
}

function metaSelect(store) {
  if (!state.setup.meta.connected) return `<select class="meta-account" disabled><option>Connect Meta first</option></select>`;
  if (!state.metaAccounts) return `<select class="meta-account" disabled><option>Loading accounts…</option></select>`;

  const options = [`<option value="">Not linked</option>`];
  for (const a of state.metaAccounts) {
    const taken = a.linked_to && a.account_id !== store.meta_account_id;
    const selected = a.account_id === store.meta_account_id ? " selected" : "";
    const tags = [a.currency, a.active ? "" : "inactive",
                  taken ? `on ${a.linked_to}` : ""]
      .filter(Boolean).join(", ");
    options.push(
      `<option value="${esc(a.account_id)}"${selected}${taken ? " disabled" : ""}>
        ${esc(a.name)} · ${esc(a.account_id)}${tags ? ` (${esc(tags)})` : ""}
      </option>`);
  }
  return `<select class="meta-account">${options.join("")}</select>`;
}

async function loadAccounts() {
  const g = state.setup.google;
  if (!g.connected) return;
  try {
    const r = await api("/api/google/accounts");
    state.accounts = r.accounts;
    renderStoreSettings();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderStoreSettings() {
  $("storeSettings").innerHTML = state.setup.stores.map((s) => `
    <div class="store-row" data-store="${s.id}">
      <div class="store-row-head">
        <strong>${esc(s.name)}</strong>
        <span>${esc(s.shop_domain)}</span>
      </div>


      <label class="inline-field">
        <span>Google Ads</span>
        ${accountSelect(s)}
      </label>

      <label class="inline-field">
        <span>Google tax</span>
        <input type="number" class="gtax" min="0" max="50" step="0.1" value="${s.google_tax_pct || 0}">
        <span class="suffix">% Google adds when it charges (e.g. 10 for GST)</span>
      </label>

      <label class="inline-field">
        <span>Meta Ads</span>
        ${metaSelect(s)}
      </label>

      <div class="row-actions">
        <button class="btn btn-sm remove btn-danger">Remove store</button>
      </div>
    </div>`).join("");

  for (const row of $("storeSettings").querySelectorAll(".store-row")) {
    const id = row.dataset.store;

    row.querySelector(".gtax").onchange = async (e) => {
      await jsonPost(`/api/stores/${id}`, { google_tax_pct: parseFloat(e.target.value) || 0 }, "PUT");
      toast("Google tax saved.");
      state.setup = await api("/api/setup");
      load(true);
    };

    const select = row.querySelector(".account");
    if (select) select.onchange = async (e) => {
      try {
        if (!e.target.value) {
          await api(`/api/stores/${id}/google`, { method: "DELETE" });
        } else {
          const account = state.accounts.find((a) => a.customer_id === e.target.value);
          await jsonPost(`/api/stores/${id}/google`, account);
        }
        toast("Ad account updated.");
        state.setup = await api("/api/setup");
        renderStoreSettings();
        load(true);
      } catch (err) {
        toast(err.message, true);
        renderStoreSettings();
      }
    };

    const metaPick = row.querySelector(".meta-account");
    if (metaPick && !metaPick.disabled) metaPick.onchange = async (e) => {
      try {
        if (!e.target.value) {
          await api(`/api/stores/${id}/meta`, { method: "DELETE" });
        } else {
          const account = state.metaAccounts.find((a) => a.account_id === e.target.value);
          await jsonPost(`/api/stores/${id}/meta`, account);
        }
        toast("Meta ad account updated.");
        state.setup = await api("/api/setup");
        renderStoreSettings();
        load(true);
      } catch (err) {
        toast(err.message, true);
        renderStoreSettings();
      }
    };

    row.querySelector(".remove").onclick = async () => {
      const store = state.setup.stores.find((s) => String(s.id) === id);
      if (!confirm(`Remove ${store.name}? Its figures disappear from ProfitDesk. `
                 + `Nothing changes inside Shopify.`)) return;
      await api(`/api/stores/${id}`, { method: "DELETE" });
      toast("Store removed.");
      await boot();
      if (state.setup.stores.length) { renderStoreSettings(); } else { closeSettings(); }
    };
  }
}

function accountSelect(store) {
  const g = state.setup.google;
  if (!g.connected) return `<select class="account" disabled><option>Sign in with Google first</option></select>`;
  if (!state.accounts) return `<select class="account" disabled><option>Loading accounts…</option></select>`;

  const options = [`<option value="">Not linked</option>`];
  for (const a of state.accounts) {
    const taken = a.linked_to && a.customer_id !== store.google_customer_id;
    const selected = a.customer_id === store.google_customer_id ? " selected" : "";
    options.push(
      `<option value="${esc(a.customer_id)}"${selected}${taken ? " disabled" : ""}>
        ${esc(a.name)} · ${esc(a.display_id)}${taken ? ` (on ${esc(a.linked_to)})` : ""}
      </option>`);
  }
  return `<select class="account">${options.join("")}</select>`;
}

/* ------------------------------------------------ wiring */

$("setupConnect").onclick = () => connectShopify($("setupDomain").value);
$("setupTokenConnect").onclick = () =>
  connectWithToken($("setupDomain").value, $("setupToken").value);

$("newConnect").onclick = connectWithApp;
$("newTokenConnect").onclick = async () => {
  if (await connectWithToken($("newDomain").value, $("newToken").value)) {
    $("newDomain").value = ""; $("newToken").value = "";
    renderStoreSettings();
  }
};

$("openSettings").onclick = openSettings;
$("restoreGo").onclick = async () => {
  const f = $("restoreFile").files[0];
  if (!f) { toast("Choose the backup file first.", true); return; }
  if (!confirm("Replace everything in this ProfitDesk with that backup?")) return;
  try {
    const r = await fetch("/api/restore", { method: "POST", body: f });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || "Couldn't restore that backup.");
    alert(`Restored ${body.stores} stores. Log in again.`);
    location.href = "/login";
  } catch (e) { toast(e.message, true); }
};
$("logOut").onclick = async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
};
$("addStore").onclick = async () => { await openSettings(); $("newDomain").focus(); };
for (const el of document.querySelectorAll("[data-close]")) el.onclick = closeSettings;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("drawer").hidden) closeSettings();
});

$("rangeSelect").onchange = () => { applyRange(); load(); };
$("storePick").onchange = (e) => pickScope(e.target.value);
$("viewPick").onchange = (e) => {
  if (e.target.value === "manage") { renderNav(); openGroupSettings(); return; }
  pickScope(e.target.value);
};
$("currencySelect").onchange = async (e) => {
  try {
    await jsonPost("/api/settings", { display_currency: e.target.value }, "PUT");
    state.setup.display_currency = e.target.value;
    load();
  } catch (err) {
    toast(err.message, true);
  }
};
$("startDate").onchange = () => { applyRange(); load(); };
$("endDate").onchange = () => { applyRange(); load(); };
$("refresh").onclick = () => {
  if ($("rangeSelect").value !== "custom") applyRange();  // "Today" moves on after midnight
  load(true);
};

boot().catch((e) => toast(e.message, true));
