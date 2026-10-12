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
  { key: "refunds_total",  label: "Refunds + chargebacks", fmt: "money", dot: "var(--spend)", dir: "none" },
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
  if (PDUrl.get("store")) state.scope = PDUrl.get("store");
  if (state.scope !== "all" && !ids.includes(state.scope)) state.scope = "all";
  const range = PDUrl.get("range");
  if (range && [...$("rangeSelect").options].some((o) => o.value === range)) {
    $("rangeSelect").value = range;
    if (range === "custom" && PDUrl.get("from") && PDUrl.get("to")) {
      $("startDate").value = PDUrl.get("from");
      $("endDate").value = PDUrl.get("to");
    }
  }

  renderNav();
  renderCurrencyPicker();
  if (!state.start) applyRange();
  await load();
  if (PDUrl.get("settings") || location.hash === "#settings") {
    if (location.hash === "#settings") history.replaceState(null, "", location.pathname + location.search);
    openSettings();
  }
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

// Desktop with the dark sidebar: the store list column goes; its two dropdowns sit in the top bar.
// Phones and narrow windows keep them in the store row, as before.
const WIDE = window.matchMedia("(min-width: 1100px)");
function placePickers() {
  const top = document.body.classList.contains("has-rail") && WIDE.matches;
  let box = $("topPicks");
  if (top && !box) {
    box = document.createElement("span");
    box.id = "topPicks";
    box.className = "top-picks";
    document.querySelector(".topbar .controls").prepend(box);
  }
  if (top && box.childElementCount < 2) box.append($("viewPick"), $("storePick"));
  if (!top && box) {
    $("storeNav").after($("viewPick"), $("storePick"));
    box.remove();
  }
}
window.addEventListener("pd-rail", placePickers);
WIDE.addEventListener("change", placePickers);

function renderNav() {
  placePickers();
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

let quietTimer = null;
async function load(fresh = false, quiet = false) {
  clearTimeout(quietTimer);
  const custom = $("rangeSelect").value === "custom";
  PDUrl.set({ store: state.scope === "all" ? null : state.scope,
              range: $("rangeSelect").value === "today" ? null : $("rangeSelect").value,
              from: custom ? state.start : null, to: custom ? state.end : null });
  const params = new URLSearchParams({
    scope: state.scope, start: state.start, end: state.end,
  });
  if (fresh) params.set("fresh", "1");
  if ($("rangeSelect").value === "today") params.set("compare", "same_time");

  if (!quiet) $("viewSub").textContent = "Loading…";
  let got;
  try {
    got = await api("/api/dashboard?" + params);
  } catch (e) {
    if (quiet) return;
    $("viewSub").textContent = "Could not load";
    toast(e.message, true);
    return;
  }
  // The server answered with its last figures and is refreshing them: ask again shortly, quietly.
  if (got.refreshing) quietTimer = setTimeout(() => load(false, true), 4000);
  if (quiet && state.data && got.updated_at === state.data.updated_at) return;
  state.data = got;

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
  shopify: `<svg class="logo logo-shopify" viewBox="0 0 24 24" aria-label="Shopify"><path fill="#95BF47" d="M15.337 23.979l7.216-1.561s-2.604-17.613-2.625-17.73c-.018-.116-.114-.192-.211-.192s-1.929-.136-1.929-.136-1.275-1.274-1.439-1.411c-.045-.037-.075-.057-.121-.074l-.914 21.104h.023zM11.71 11.305s-.81-.424-1.774-.424c-1.447 0-1.504.906-1.504 1.141 0 1.232 3.24 1.715 3.24 4.629 0 2.295-1.44 3.76-3.406 3.76-2.354 0-3.54-1.465-3.54-1.465l.646-2.086s1.245 1.066 2.28 1.066c.675 0 .975-.545.975-.932 0-1.619-2.654-1.694-2.654-4.359-.034-2.237 1.571-4.416 4.827-4.416 1.257 0 1.875.361 1.875.361l-.945 2.715-.02.009zM11.17.83c.136 0 .271.038.405.135-.984.465-2.064 1.639-2.508 3.992-.656.213-1.293.405-1.889.578C7.697 3.75 8.951.84 11.17.84V.83zm1.235 2.949v.135c-.754.232-1.583.484-2.394.736.466-1.777 1.333-2.645 2.085-2.971.193.501.309 1.176.309 2.1zm.539-2.234c.694.074 1.141.867 1.429 1.755-.349.114-.735.231-1.158.366v-.252c0-.752-.096-1.371-.271-1.871v.002zm2.992 1.289c-.02 0-.06.021-.078.021s-.289.075-.714.21c-.423-1.233-1.176-2.37-2.508-2.37h-.115C12.135.209 11.669 0 11.265 0 8.159 0 6.675 3.877 6.21 5.846c-1.194.365-2.063.636-2.16.674-.675.213-.694.232-.772.87-.075.462-1.83 14.063-1.83 14.063L15.009 24l.927-21.166z"/><path fill="#5E8E3E" d="M15.337 23.979l7.216-1.561s-2.604-17.613-2.625-17.73c-.018-.116-.114-.192-.211-.192s-1.929-.136-1.929-.136-1.275-1.274-1.439-1.411c-.045-.037-.075-.057-.121-.074l-.914 21.104h.023z"/></svg>`,
  google: `<svg class="logo" viewBox="0 0 48 48" aria-label="Google"><path fill="#EA4335" d="M24 9.5c3.5 0 6.7 1.2 9.2 3.6l6.9-6.9C35.9 2.4 30.5 0 24 0 14.6 0 6.5 5.4 2.6 13.2l8 6.2C12.4 13.7 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M47 24.5c0-1.6-.2-3.1-.4-4.5H24v9h12.9c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 7.2-10.4 7.2-17.7z"/><path fill="#FBBC05" d="M10.5 28.6c-.5-1.5-.8-3-.8-4.6s.3-3.1.8-4.6l-8-6.2C.9 16.5 0 20.1 0 24s.9 7.5 2.6 10.8z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.7-6c-2.2 1.5-4.9 2.3-8.2 2.3-6.3 0-11.6-4.2-13.5-9.9l-8 6.2C6.5 42.6 14.6 48 24 48z"/></svg>`,
  paypal: `<svg class="logo" viewBox="0 0 24 24" aria-label="PayPal"><path fill="#003087" d="M7.016 19.198h-4.2a.562.562 0 0 1-.555-.65L5.093.584A.692.692 0 0 1 5.776 0h7.222c3.417 0 5.904 2.488 5.846 5.5-.006.25-.027.5-.066.747A6.794 6.794 0 0 1 12.071 12H8.743a.69.69 0 0 0-.682.583l-.325 2.056-.013.083-.692 4.39-.015.087z"/><path fill="#0070E0" d="M19.79 6.142c-.01.087-.01.175-.023.261a7.76 7.76 0 0 1-7.695 6.598H9.007l-.283 1.795-.013.083-.692 4.39-.134.843-.014.088H6.86l-.497 3.15a.562.562 0 0 0 .555.65h3.94c.34 0 .63-.249.683-.585l.952-6.031a.692.692 0 0 1 .683-.584h2.126a6.793 6.793 0 0 0 6.707-5.752c.306-1.95-.466-3.744-1.709-4.906z"/></svg>`,
  airwallex: `<svg class="logo" viewBox="0 0 24 24" aria-label="Airwallex"><rect width="24" height="24" rx="5" fill="#612FFF"/><path fill="#fff" d="M17.9 15.6 13.6 5.9c-.6-1.3-2.5-1.3-3.1 0L6 15.6c-.4.9.3 1.8 1.2 1.6l3.6-.9c.8-.2 1.6-.2 2.4 0l3.6.9c.9.2 1.5-.7 1.1-1.6zm-5.9-2.4c-.6 0-1.2.1-1.7.2l1.7-4 1.8 4c-.6-.1-1.2-.2-1.8-.2z"/></svg>`,
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
      ${c.key === "orders" ? `<div class="card-note card-aov">AOV <b>${fmt(d.totals.aov, "money")}</b></div>` : ""}
      ${c.key === "refunds_total" ? refundSplit(d.totals) : ""}
      ${c.key === "cogs" ? cogNote(d.totals) : ""}
      ${c.key === "processing_fee" ? feeSplit(d.totals) : ""}
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
        ${step("Sales", t.sales)}<i>−</i>${step("Refunds", t.refunds_total)}<i>−</i>${step("Fees", t.processing_fee)}<i>−</i>${step("COG", t.cogs)}<i>−</i>${step("Ads", t.ad_spend)}<i>−</i>${step("Held in reserve", t.reserve_held)}
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

// Processing fees by provider: figures from the server (metrics.py); "other" covers any other gateway
function feeSplit(t) {
  const other = t.other_payment_fee || 0;
  return `<div class="spend-split">
      <div>${LOGO.paypal}<span>PayPal</span><b>${fmt(t.paypal_fee, "money")}</b></div>
      <div>${LOGO.airwallex}<span>Airwallex</span><b>${fmt(t.airwallex_fee, "money")}</b></div>
      ${Math.abs(other) >= 0.5 ? `<div><span class="nologo"></span><span>Other gateways</span><b>${fmt(other, "money")}</b></div>` : ""}
      <div><span class="nologo"></span><span>Shopify</span><b>${fmt(t.shopify_fee, "money")}</b></div>
    </div>`;
}

// Refunds (by the day they were made) and chargebacks
function refundSplit(t) {
  return `<div class="spend-split">
      <div><span class="nologo"></span><span>Refunds${t.refund_count ? ` (${fmt(t.refund_count, "int")})` : ""}</span><b>${fmt(t.refunds, "money")}</b></div>
      <div><span class="nologo"></span><span>Chargebacks</span><b>${fmt(t.chargebacks, "money")}</b></div>
    </div>`;
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
        <span class="sub">${esc(s.outside ? "Refunds + chargebacks made in PayPal / Airwallex" : linkedAccounts(s))}</span></td>
      <td>${fmt(t.sales, "money")}</td>
      <td>${fmt(t.ad_spend, "money")}</td>
      <td class="roas">${fmt(t.roas, "ratio")}</td>
      <td>${fmt(t.processing_fee, "money")}</td>
      <td>${fmt(t.refunds_total, "money")}</td>
      <td>${fmt(t.cogs_share, "pct")}</td>
      <td>${fmt(t.aov, "money")}</td>
      <td class="${profitClass}">${fmt(t.net_profit, "money")}</td>
      <td>${fmt(t.net_margin, "pct")}</td>
      <td>${fmt(t.orders, "int")}</td>
    </tr>`;
  }).join("");

  $("storeTable").innerHTML = `
    <thead><tr>
      <th>Store</th><th>Total sales</th><th>Ad spend</th><th>ROAS</th><th>Fees</th><th title="Refunds made in the period (by refund day) + chargebacks">Refunds</th>
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
      <td>${fmt(t.refunds_total, "money")}</td>
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
  phone: `<svg class="logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2.5" width="12" height="19" rx="2.5"/><rect x="8.5" y="6" width="3" height="3" rx=".6"/><rect x="12.5" y="6" width="3" height="3" rx=".6"/><rect x="8.5" y="10.5" width="7" height="3" rx=".6"/><path d="M11 18.5h2"/></svg>`,
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
  "iPhone widget": ["Data", "phone", "Pick figures to show on your iPhone home screen"],
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
  PDUrl.set({ settings: 1 });
  organiseSettings();
  $("drawer").hidden = false;
  renderGoogleBox();
  renderMetaBox();
  renderGroupSettings();
  renderFeeSettings();
  renderPeople();
  renderMailboxes();
  renderAI();
  renderWA();
  renderWidgets();
  renderStoreSettings();
  await Promise.all([loadAccounts(), loadMetaAccounts()]);
}

function closeSettings() { $("drawer").hidden = true; PDUrl.set({ settings: null }); }

async function openGroupSettings() {
  const pending = openSettings();
  openSettingsItem("groupsBlock");
  await pending;
}

/* ------------------------------------------------ people */

const DESK_NAMES = { profit: "Profit dashboard", cash: "Cash flow", cog: "COG + Products", inbox: "Inbox", scm: "SCM",
  disputes: "Disputes", expenses: "Expenses", reports: "Reports", importer: "Product Importer", sizecharts: "Size Charts",
  recordings: "Session Recordings", googleads: "Google Ads", tracking: "Tracking" };
const LVL_NAMES = { "": "No access", view: "View", work: "Work", manage: "Manage" };
const LVL_HELP = "View = see only · Work = the daily tasks (reply, upload, approve prices, answer disputes) · " +
  "Manage = also the desk's sensitive actions (refund or accept a dispute, supplier opening balances, AI training)";
const PRESETS = {
  manager: ["Manager", Object.fromEntries(Object.keys(DESK_NAMES).map((d) => [d, "manage"]))],
  va: ["VA (Inbox + SCM)", { inbox: "work", scm: "work" }],
  cs: ["Customer service (Inbox, SCM, Disputes)", { inbox: "work", scm: "work", disputes: "work" }],
  viewer: ["Viewer (see everything)", Object.fromEntries(Object.keys(DESK_NAMES).map((d) => [d, "view"]))],
};

function accessFields(prefix, role = "staff", access = {}) {
  return `<label class="field"><span>Access</span>
      <select id="${prefix}Role" class="control">
        <option value="staff"${role !== "owner" ? " selected" : ""}>Team member: pick each desk below</option>
        <option value="owner"${role === "owner" ? " selected" : ""}>Owner: everything, incl. Settings, connections and people</option></select></label>
    <div class="field" id="${prefix}DeskBox"${role === "owner" ? " hidden" : ""}>
      <span>Start from <span class="preset-picks">${Object.entries(PRESETS).map(([k, [n]]) =>
        `<button type="button" class="btn btn-sm btn-ghost" data-preset="${k}">${n}</button>`).join("")}</span></span>
      <div class="desk-levels">${Object.entries(DESK_NAMES).map(([k, v]) => `
        <label><span>${v}</span><select class="control" data-desk="${k}">${Object.entries(LVL_NAMES).map(([lv, ln]) =>
          `<option value="${lv}"${(access[k] || "") === lv ? " selected" : ""}>${ln}</option>`).join("")}</select></label>`).join("")}</div>
      <p class="hint">${LVL_HELP}</p></div>`;
}

function readAccess(prefix) {
  const role = $(prefix + "Role").value;
  const access = {};
  for (const sel of document.querySelectorAll(`#${prefix}DeskBox [data-desk]`)) if (sel.value) access[sel.dataset.desk] = sel.value;
  return { role, access };
}

function wireAccess(prefix) {
  $(prefix + "Role").onchange = () => { $(prefix + "DeskBox").hidden = $(prefix + "Role").value === "owner"; };
  for (const b of $(prefix + "DeskBox").querySelectorAll("[data-preset]")) b.onclick = () => {
    const pick = PRESETS[b.dataset.preset][1];
    for (const sel of $(prefix + "DeskBox").querySelectorAll("[data-desk]")) sel.value = pick[sel.dataset.desk] || "";
  };
}

function accessSummary(u) {
  if (u.role === "owner") return "Owner";
  const by = { manage: [], work: [], view: [] };
  for (const [d, lv] of Object.entries(u.access || {})) by[lv]?.push(DESK_NAMES[d] || d);
  return [["Manages", by.manage], ["Works in", by.work], ["Views", by.view]]
    .filter(([, l]) => l.length).map(([t, l]) => `${t} ${l.join(", ")}`).join(" · ");
}

async function renderPeople() {
  let r;
  try { r = await api("/api/users"); } catch (e) { toast(e.message, true); return; }
  const rows = r.users.map((u) => `
    <div class="person">
      <div class="group-row">
        <div class="group-name"><strong>${esc(u.name || u.email)}${u.id === r.me.id ? " (you)" : ""}</strong>
          <span>${esc(u.email)} · ${esc(accessSummary(u))}${u.must_change ? " · hasn't logged in yet" : ""}</span></div>
        <button class="btn btn-sm btn-ghost" data-edit-user="${u.id}">Edit</button>
        ${u.id === r.me.id ? "" : `<button class="btn btn-sm btn-danger" data-remove-user="${u.id}">Remove</button>`}
      </div>
      <div class="person-edit" id="edit${u.id}" hidden></div>
    </div>`).join("");

  $("peopleBox").innerHTML = `${rows}
    <details class="advanced"><summary>Add a person</summary>
      <label class="field"><span>First name (shown to the team, e.g. on Inbox tickets they handle)</span><input id="puName" type="text" autocomplete="off"></label>
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
      if (!$("puName").value.trim()) { toast("Type their first name.", true); $("puName").focus(); return; }
      await jsonPost("/api/users", { name: $("puName").value.trim().split(/\s+/)[0], email: $("puEmail").value,
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
      box.innerHTML = `${accessFields(p, u.role, u.access)}
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

/* ------------------------------------------------ iPhone widget (Scriptable) */

let widgetMeta = null, widgetEditing = null;   // null = list, 0 = new, id = editing

async function renderWidgets() {
  let r;
  try { r = await api("/api/widgets"); } catch { $("widgetBox").innerHTML = ""; return; }
  widgetMeta = r;
  const scopes = [["all", "All stores"], ...(state.setup.groups || []).map((g) => [`g${g.id}`, `${g.name} (group)`]),
                  ...state.setup.stores.map((x) => [String(x.id), x.name])];
  const scopeName = (v) => (scopes.find((x) => x[0] === v) || [, "?"])[1];
  const label = (k) => (r.metrics.find((m) => m.key === k) || {}).label || k;
  const period = (k) => (r.periods.find((p) => p.key === k) || {}).label || k;
  const form = (w) => `
    <div class="wg-form">
      <label class="field"><span>Name</span><input id="wgName" class="control" value="${esc(w.name || "")}" placeholder="e.g. Today, all stores"></label>
      <div class="wg-two">
        <label class="field"><span>Store</span><select id="wgScope" class="control">${scopes.map(([v, n]) => `<option value="${esc(v)}"${v === (w.scope || "all") ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
        <label class="field"><span>Period</span><select id="wgPeriod" class="control">${r.periods.map((p) => `<option value="${p.key}"${p.key === (w.period || "today") ? " selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
      </div>
      <span class="wg-lab">Figures to show <small>(tap in the order you want; small widget shows the first 3, medium 6, large 10)</small></span>
      <div class="wg-picks" id="wgPicks">${r.metrics.map((m) => {
        const i = (w.metrics || []).indexOf(m.key);
        return `<button type="button" class="wg-pick${i >= 0 ? " on" : ""}" data-k="${m.key}">${i >= 0 ? `<em>${i + 1}</em>` : ""}${esc(m.label)}</button>`;
      }).join("")}</div>
      <div class="wg-btns"><button class="btn btn-ghost" id="wgCancel">Cancel</button><button class="btn btn-primary" id="wgSave">${w.id ? "Save" : "Create widget"}</button></div>
    </div>`;

  if (widgetEditing !== null) {
    const w = widgetEditing ? r.widgets.find((x) => x.id === widgetEditing) || {} :
      { metrics: ["sales", "ad_spend", "roas", "cogs", "net_profit", "processing_fee"] };
    $("widgetBox").innerHTML = form(w);
    let picks = [...(w.metrics || [])];
    const draw = () => {
      for (const b of $("wgPicks").querySelectorAll("[data-k]")) {
        const i = picks.indexOf(b.dataset.k);
        b.classList.toggle("on", i >= 0);
        b.innerHTML = (i >= 0 ? `<em>${i + 1}</em>` : "") + esc(label(b.dataset.k));
      }
    };
    $("wgPicks").onclick = (e) => {
      const b = e.target.closest("[data-k]");
      if (!b) return;
      const k = b.dataset.k;
      picks = picks.includes(k) ? picks.filter((x) => x !== k) : [...picks, k];
      draw();
    };
    $("wgCancel").onclick = () => { widgetEditing = null; renderWidgets(); };
    $("wgSave").onclick = async () => {
      const body = { name: $("wgName").value, scope: $("wgScope").value, period: $("wgPeriod").value, metrics: picks };
      try {
        await jsonPost(widgetEditing ? `/api/widgets/${widgetEditing}` : "/api/widgets", body, widgetEditing ? "PUT" : "POST");
        toast(widgetEditing ? "Saved. Your iPhone widget shows it at its next refresh." : "Widget created. Now copy its script to your iPhone.");
        widgetEditing = null;
        renderWidgets();
      } catch (e) { toast(e.message, true); }
    };
    return;
  }

  $("widgetBox").innerHTML = `
    ${r.widgets.map((w) => `<div class="group-row wg-row"><div class="group-name"><strong>${esc(w.name)}</strong>
        <span>${esc(scopeName(w.scope))} · ${esc(period(w.period))}${w.last_used ? ` · last read ${esc(w.last_used.slice(0, 16).replace("T", " "))} UTC` : " · not on a phone yet"}</span>
        <span class="wg-chips">${w.metrics.map((k) => `<i>${esc(label(k))}</i>`).join("")}</span></div>
      <div class="wg-rowbtns"><button class="btn btn-sm btn-primary" data-wcopy="${w.id}">Copy script</button>
        <button class="btn btn-sm btn-ghost" data-wedit="${w.id}">Edit</button>
        <button class="btn btn-sm btn-ghost" data-wdel="${w.id}">Turn off</button></div></div>`).join("")}
    <button class="btn btn-ghost btn-block" id="wgNew">+ New widget</button>
    <details class="advanced"${r.widgets.length ? "" : " open"}><summary>How to put it on your iPhone</summary>
      <ol class="wg-steps">
        <li>On your iPhone, install <b>Scriptable</b> from the App Store (free).</li>
        <li>Open ProfitDesk on the iPhone (Safari), go to Settings → iPhone widget and tap <b>Copy script</b>.</li>
        <li>Open Scriptable, tap <b>+</b>, paste, and name it <b>ProfitDesk</b> at the top. Tap ▶ to preview.</li>
        <li>On the home screen, press and hold an empty spot → <b>Edit</b> → <b>Add widget</b> → Scriptable → pick a size → Add widget.</li>
        <li>Tap the new widget → <b>Script</b>: ProfitDesk. Done. It updates about every 15 minutes; tapping it opens ProfitDesk.</li>
        <li>Lock screen: customise the lock screen → add a Scriptable widget → choose ProfitDesk (shows the first 1–3 figures).</li>
      </ol>
      <p class="hint">Want a second widget (say one per store)? Create another here, copy its script into a new
        Scriptable script with its own name. Changing figures here updates the phone without re-copying.
        “Turn off” stops that link straight away.</p>
    </details>`;
  $("wgNew").onclick = () => { widgetEditing = 0; renderWidgets(); };
  for (const b of $("widgetBox").querySelectorAll("[data-wedit]")) b.onclick = () => { widgetEditing = +b.dataset.wedit; renderWidgets(); };
  for (const b of $("widgetBox").querySelectorAll("[data-wdel]")) b.onclick = async () => {
    if (!confirm("Turn this widget off? It stops working on any phone straight away.")) return;
    try { await api(`/api/widgets/${b.dataset.wdel}`, { method: "DELETE" }); renderWidgets(); toast("Turned off."); }
    catch (e) { toast(e.message, true); }
  };
  // Fetch every script now, so the tap copies straight away (Safari blocks a copy that waits on the network).
  const scripts = {};
  for (const w of r.widgets) api(`/api/widgets/${w.id}/script`).then((d) => { scripts[w.id] = d.script; }).catch(() => {});
  for (const b of $("widgetBox").querySelectorAll("[data-wcopy]")) b.onclick = () => {
    const id = b.dataset.wcopy;
    const done = () => toast("Script copied. Paste it into a new Scriptable script on your iPhone.");
    const text = scripts[id];
    if (text && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done, () => showScript(text));
    } else if (window.ClipboardItem && navigator.clipboard?.write) {
      // still loading: hand Safari a promise so the copy keeps the tap's permission
      const blob = api(`/api/widgets/${id}/script`).then((d) => { scripts[id] = d.script; return new Blob([d.script], { type: "text/plain" }); });
      navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]).then(done,
        () => blob.then(() => showScript(scripts[id])).catch((e) => toast(e.message, true)));
    } else {
      api(`/api/widgets/${id}/script`).then((d) => showScript(d.script)).catch((e) => toast(e.message, true));
    }
  };
}

// Fallback: the script in a box, already selected, with its own Copy button
function showScript(text) {
  let dlg = $("wgScriptDlg");
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "wgScriptDlg";
    dlg.className = "wg-dlg";
    dlg.innerHTML = `<h3>Widget script</h3>
      <p class="hint">Your browser didn't allow automatic copying. Tap <b>Copy</b> below, or select all the text and copy it.</p>
      <textarea id="wgScriptText" class="control" readonly></textarea>
      <div class="wg-btns"><button class="btn btn-ghost" id="wgScriptClose">Close</button><button class="btn btn-primary" id="wgScriptCopy">Copy</button></div>`;
    document.body.appendChild(dlg);
    $("wgScriptClose").onclick = () => dlg.close();
    $("wgScriptCopy").onclick = () => {
      const ta = $("wgScriptText");
      ta.focus(); ta.select(); ta.setSelectionRange(0, ta.value.length);
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      if (ok) { toast("Script copied. Paste it into a new Scriptable script on your iPhone."); dlg.close(); }
      else navigator.clipboard?.writeText(ta.value).then(() => { toast("Script copied."); dlg.close(); },
        () => toast("Select all the text and copy it by hand (press and hold → Select All → Copy).", true));
    };
  }
  $("wgScriptText").value = text || "";
  dlg.showModal();
  const ta = $("wgScriptText");
  ta.focus(); ta.select();
}

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

/* ------------------------------------------------ WhatsApp */

async function renderWA() {
  if (!$("waBox")) return;
  let r;
  try { r = await api("/api/whatsapp/settings"); } catch { $("waBox").innerHTML = ""; return; }
  const userOpts = (sel) => r.users.map((u) => `<option value="${esc(u.email)}"${u.email === sel ? " selected" : ""}>${esc(u.name)}</option>`).join("");
  const row = (p = {}) => `<div class="wa-person"><input class="control wa-phone" placeholder="Phone with country code, e.g. 85291234567" value="${esc(p.phone || "")}">
      <select class="control wa-email">${userOpts(p.email)}</select><button class="btn btn-sm btn-ghost wa-del" title="Remove">✕</button></div>`;
  $("waBox").innerHTML = `
    <div class="group-row"><div class="group-name"><strong>${r.connected ? "Connected" : "Not connected yet"}</strong>
      <span>${r.connected ? `Phone number ID ${esc(r.phone_id)} · ${r.people.length} people can use it` : "Fill in the four boxes below."}</span></div></div>
    <details class="advanced"${r.connected ? "" : " open"}><summary>${r.connected ? "Connection details" : "Connect"}</summary>
      <p class="hint">In Meta's WhatsApp setup, set the webhook: <b>Callback URL</b> <code>${esc(r.webhook)}</code> and
        <b>Verify token</b> <code>${esc(r.verify)}</code>, then subscribe to <b>messages</b>.</p>
      <label class="field"><span>Access token (System user, permanent)${r.has_token ? " · saved, paste to replace" : ""}</span><input id="waToken" type="password" autocomplete="off"></label>
      <label class="field"><span>App secret (Meta app → Settings → Basic)${r.has_secret ? " · saved, paste to replace" : ""}</span><input id="waSecret" type="password" autocomplete="off"></label>
      <label class="field"><span>Phone number ID</span><input id="waPhone" value="${esc(r.phone_id || "")}"></label>
      <label class="field"><span>WhatsApp Business Account ID</span><input id="waWaba" value="${esc(r.waba_id || "")}"></label>
      <label class="field"><span>Meta app ID</span><input id="waApp" value="${esc(r.app_id || "")}"></label>
    </details>
    <h4 class="wa-h">Who can use it</h4>
    <p class="hint">Each number chats as that person, with their ProfitDesk access. Messages from any other number are ignored.</p>
    <div id="waPeople">${(r.people.length ? r.people : [{}]).map(row).join("")}</div>
    <button class="btn btn-sm btn-ghost" id="waAdd">+ Add a number</button>
    <div class="wa-btns"><button id="waSave" class="btn btn-primary">Save</button>
      ${r.connected ? `<button id="waTest" class="btn btn-ghost">Send a test message</button>` : ""}</div>`;
  const wire = () => { for (const b of $("waPeople").querySelectorAll(".wa-del")) b.onclick = () => { b.closest(".wa-person").remove(); }; };
  wire();
  $("waAdd").onclick = () => { $("waPeople").insertAdjacentHTML("beforeend", row()); wire(); };
  $("waSave").onclick = async () => {
    const people = [...$("waPeople").querySelectorAll(".wa-person")].map((d) => ({ phone: d.querySelector(".wa-phone").value, email: d.querySelector(".wa-email").value }));
    try {
      const out = await jsonPost("/api/whatsapp/settings", { token: $("waToken").value, app_secret: $("waSecret").value,
        phone_id: $("waPhone").value, waba_id: $("waWaba").value, app_id: $("waApp").value, people }, "PUT");
      toast([out.setup ? out.setup.join(" · ") : "Saved.", out.template ? "Template: " + out.template : "", out.template_error ? "Template: " + out.template_error : ""].filter(Boolean).join(" · "), !!out.template_error);
      renderWA();
    } catch (e) { toast(e.message, true); }
  };
  if ($("waTest")) $("waTest").onclick = async () => {
    const p = $("waPeople").querySelector(".wa-phone")?.value;
    try { const out = await jsonPost("/api/whatsapp/test", { phone: p }); toast(out.note || "Sent."); }
    catch (e) { toast(e.message, true); }
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
        <span>${esc(a.store || "All stores (company email)")} · ${a.emails} emails · checked ${when(a.last_checked)}</span>
        ${a.error ? `<span class="neg">${esc(a.error)}</span>` : ""}
        <span class="${a.can_send ? "pos" : ""}">${a.can_send ? "✓ Can send replies from the Inbox" : "Replies: not signed in to send yet"}</span>
        <label class="mb-ask"><input type="checkbox" data-auto-ask="${a.id}"${a.auto_ask ? " checked" : ""}${a.can_send ? "" : " disabled"}>
          When we can't find a customer's order, the AI replies asking for their order number, name and checkout email (once per ticket)</label></div>
      <div class="mb-btns">
        ${a.can_send ? `<a class="btn btn-sm btn-ghost" href="/auth/gmail/start?account=${a.id}" target="_blank" rel="noopener" title="Renew this mailbox's Google sign-in">Sign in again</a>` : ""}
        ${a.can_send ? `<button class="btn btn-sm btn-ghost" data-test-send="${a.id}">Send a test email</button>` : ""}
        ${a.can_send ? `<button class="btn btn-sm btn-ghost" data-gmail-out="${a.id}">Stop sending</button>`
                     : `<a class="btn btn-sm btn-primary" href="/auth/gmail/start?account=${a.id}" target="_blank" rel="noopener">Sign in to send</a>`}
        <button class="btn btn-sm btn-danger" data-remove-mail="${a.id}">Remove</button>
      </div>
    </div>`).join("");
  const stores = (state.setup?.stores || []).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  $("mailBox").innerHTML = `${rows || `<p class="hint">No mailboxes connected yet.</p>`}
    ${r.accounts.length ? `<details class="advanced"${r.accounts.some((a) => a.can_send) ? "" : " open"}><summary>Replying from the Inbox: how to sign in</summary>
      <ol class="wg-steps">
        <li>Once only: switch on the <a href="${esc(r.gmail_api_link)}" target="_blank" rel="noopener"><b>Gmail API</b></a>
          in your Google Cloud project (the one ProfitDesk's Google sign-in uses) → click <b>Enable</b>.</li>
        <li>Click <b>Sign in to send</b> next to a mailbox. A Google tab opens.</li>
        <li>Choose that mailbox's Google account (the same address). If Google says the app isn't verified,
          click <b>Advanced</b> → <b>Go to ProfitDesk</b>. It only asks to <b>send email</b> on that address.</li>
        <li>Click <b>Continue</b>. The tab says it's done; close it and this list shows ✓.</li>
      </ol>
      <p class="hint">Replies go out from the store's address, appear in that mailbox's Sent folder and stay in the
        same Gmail conversation, exactly as if you typed them in Gmail.</p></details>` : ""}
    <details class="advanced"${r.accounts.length ? "" : " open"}><summary>Connect a mailbox</summary>
      <label class="field"><span>Store</span><select id="mbStore" class="control">${stores}
        <option value="">All stores (company email, e.g. the Shopify / PayPal / Airwallex login)</option></select></label>
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
  for (const c of $("mailBox").querySelectorAll("[data-auto-ask]")) c.onchange = async () => {
    try { await jsonPost(`/api/mail-accounts/${c.dataset.autoAsk}/settings`, { auto_ask: c.checked });
          toast(c.checked ? "On: customers whose order we can't find get asked for their order number." : "Off."); }
    catch (e) { toast(e.message, true); c.checked = !c.checked; }
  };
  for (const b of $("mailBox").querySelectorAll("[data-test-send]")) b.onclick = async () => {
    const to = prompt("Send a test email to which address?");
    if (!to) return;
    b.disabled = true;
    try { await jsonPost(`/api/mail-accounts/${b.dataset.testSend}/test-send`, { to }); toast(`Test email sent to ${to}. Check that inbox (and spam).`); }
    catch (e) { toast(e.message, true); }
    b.disabled = false;
  };
  for (const b of $("mailBox").querySelectorAll("[data-gmail-out]")) b.onclick = async () => {
    if (!confirm("Stop sending replies from this mailbox? You can sign in again any time.")) return;
    await jsonPost(`/api/mail-accounts/${b.dataset.gmailOut}/gmail-signout`, {});
    renderMailboxes();
  };
  // Back from the Google tab: refresh the ✓
  if (!window.__mbFocus) { window.__mbFocus = true; window.addEventListener("focus", () => { if (!$("drawer").hidden) renderMailboxes(); }); }
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
      folder as <code>google-ads-script.js</code>.</p>
    <details class="advanced" id="gKeysBox"><summary>Change Google sign-in keys</summary>
      <p class="hint">From Google Cloud → Google Auth Platform → Clients. Redirect URI: <code>${esc(g.redirect_uri || "")}</code>.
        After saving, click <b>Sign in again</b> above; store links stay as they are.</p>
      <label class="field"><span>Client ID</span>
        <input id="gClientId2" type="text" placeholder="…apps.googleusercontent.com" autocomplete="off"></label>
      <label class="field"><span>Client secret</span>
        <input id="gClientSecret2" type="password" placeholder="GOCSPX-…" autocomplete="off"></label>
      <button id="gSave2" class="btn btn-primary btn-block">Save new keys</button></details>`;
  $("gSave2").onclick = async () => {
    try {
      await jsonPost("/api/google/app", { client_id: $("gClientId2").value, client_secret: $("gClientSecret2").value });
      toast("New Google keys saved. Now click “Sign in again”.");
      $("gKeysBox").open = false;
    } catch (e) { toast(e.message, true); }
  };
  if (PDUrl.get("gkeys")) { $("gKeysBox").open = true; $("gKeysBox").scrollIntoView({ block: "center" }); }
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
