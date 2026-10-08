/* ProfitDesk COG + Products Monitor.

   This file draws things. Every cost comes from the server (cog.py), already
   converted into the chosen currency. */

const $ = (id) => document.getElementById(id);
let status = null;
let productsCache = {};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function money(v, cur, digits = 2) {
  if (v === null || v === undefined) return "—";
  const opts = { minimumFractionDigits: digits, maximumFractionDigits: digits };
  if (cur) { opts.style = "currency"; opts.currency = cur; }
  try { return new Intl.NumberFormat(undefined, opts).format(v); }
  catch { return Number(v).toFixed(digits); }
}

const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function niceDate(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined,
    { weekday: "short", day: "numeric", month: "short" });
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

async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname);
    throw new Error("Please log in again.");
  }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function range() {
  const today = new Date((status?.today || iso(new Date())) + "T12:00:00");
  const back = (n) => { const d = new Date(today); d.setDate(d.getDate() - n); return iso(d); };
  const v = $("range").value;
  if (v === "today") return [iso(today), iso(today)];
  if (v === "yesterday") return [back(1), back(1)];
  if (v === "7") return [back(6), iso(today)];
  return [$("from").value || back(1), $("to").value || $("from").value || back(1)];
}

/* ------------------------------------------------ check COG */

const SOURCE = { invoice: "Invoice", catalog: "History", estimate: "Estimate", cancelled: "Cancelled" };

async function loadDay() {
  const [from, to] = range();
  $("dates").hidden = $("range").value !== "custom";
  if ($("range").value !== "custom") { $("from").value = from; $("to").value = to; }
  $("sub").textContent = (from === to ? niceDate(from) : `${niceDate(from)} – ${niceDate(to)}`) + " · loading…";
  let d;
  try {
    d = await api(`/api/cog/day?start=${from}&end=${to}&store=${$("storeSelect").value}` +
                  `&currency=${encodeURIComponent($("currencySelect").value)}`);
  } catch (e) { toast(e.message, true); return; }
  const cur = d.currency;
  const s = d.summary;
  $("sub").textContent = (from === to ? niceDate(from) : `${niceDate(from)} – ${niceDate(to)}`) +
    ` · all figures in ${cur}` + (d.synced_at ? ` · costs updated ${new Date(d.synced_at * 1000)
      .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : "");

  const card = (label, value, note = "", dot = "var(--spend)") => `<div class="card">
      <div class="card-label"><span class="dot" style="background:${dot}"></span>${label}</div>
      <div class="card-value">${value}</div><div class="card-note">${note}</div></div>`;
  $("cards").innerHTML =
    card("Sales", money(s.sales, cur, 0), `${s.orders} orders`, "var(--sales)") +
    card("COG", money(s.cog, cur, 0), `${pct(s.cog_pct)} of sales`) +
    card("From invoices", String(s.from_invoice), "product lines with the supplier's cost") +
    card("Not yet invoiced", String(s.from_history + s.estimated_lines),
         `${s.from_history} from history · <span class="est">${s.estimated_lines} estimated</span>`);

  $("estNote").innerHTML = s.estimated_lines
    ? `<div class="warn">${s.estimated_lines} product line${s.estimated_lines === 1 ? "" : "s"} had no cost history,
        so ${money(s.estimated_cog, cur)} is estimated from each store's average COG %. Upload the supplier's
        invoice (coming next) or add them to the orders sheet to replace the estimate.</div>` : "";

  $("orderCount").textContent = `${d.orders.length} orders`;
  const multiStore = $("storeSelect").value === "all";
  $("orders").innerHTML = d.orders.length ? `<thead><tr><th>Time</th><th>Order</th><th>Products</th>
      <th>Sales</th><th>COG</th><th>COG %</th></tr></thead><tbody>${d.orders.map((o) => {
    const t = new Date(o.created);
    const when = (from === to ? "" : t.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " ") +
      t.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    const lines = o.lines.map((l) => `<div class="cog-line">
        <span class="src ${esc(l.source)}" title="${esc(l.note || "")}">${SOURCE[l.source] || "?"}</span>
        <span class="cog-prod">${esc(l.product)}${l.quantity > 1 ? ` <b>×${l.quantity}</b>` : ""}
          ${l.supplier ? `<small>${esc(l.supplier)}</small>` : ""}</span>
        <span class="cog-amt ${l.source === "estimate" ? "est" : ""}">${money(l.cog, cur)}</span></div>`).join("");
    return `<tr class="${o.cancelled ? "cancelled" : ""}">
        <td>${when}</td>
        <td class="name">${esc(o.order)}${multiStore ? `<span class="sub">${esc(o.store)}</span>` : ""}
          ${o.cancelled ? `<span class="sub">cancelled</span>` : ""}</td>
        <td class="cog-lines">${lines}</td>
        <td>${money(o.sales, cur)}</td>
        <td class="${o.estimated ? "est" : ""}">${money(o.cog, cur)}</td>
        <td>${pct(o.cog_pct)}</td></tr>`;
  }).join("")}</tbody>` : `<tbody><tr><td class="hint">No orders costed for this period yet.${
    status && !status.synced_at ? " The first costing run is still going; refresh in a few minutes." : ""}</td></tr></tbody>`;
}

/* ------------------------------------------------ products */

async function loadProducts() {
  const sid = $("productStore").value;
  if (!sid) return;
  if (!productsCache[sid]) {
    $("products").innerHTML = `<tbody><tr><td class="hint">Loading…</td></tr></tbody>`;
    try { productsCache[sid] = (await api(`/api/cog/products?store=${sid}`)).products; }
    catch (e) { $("products").innerHTML = `<tbody><tr><td class="hint">${esc(e.message)}</td></tr></tbody>`; return; }
  }
  const q = $("search").value.trim().toLowerCase();
  const items = productsCache[sid].filter((p) => !q || p.product.includes(q));
  const changed = items.filter((p) => p.changed).length;
  $("productCount").textContent = `${items.length} products · ${changed} with a changed cost`;
  $("products").innerHTML = `<thead><tr><th>Product</th><th>Supplier</th><th>Latest cost</th>
      <th>Lowest – highest</th><th>Bought</th><th>Last</th></tr></thead><tbody>${items.slice(0, 400).map((p) => `
    <tr class="${p.changed ? "changed" : ""}"><td class="name">${esc(p.product)}
        ${p.changed ? `<span class="sub flag">Cost has changed</span>` : ""}</td>
      <td>${esc(p.supplier)}</td><td>${money(p.cost, "USD")}</td>
      <td>${p.changed ? `${money(p.low, "USD")} – ${money(p.high, "USD")}` : "—"}</td>
      <td>${p.times}×</td><td>${p.last_day ? niceDate(p.last_day) : "—"}</td></tr>`).join("")}</tbody>`;
}

/* ------------------------------------------------ wiring */

for (const b of document.querySelectorAll(".cog-tab")) {
  b.onclick = () => {
    for (const x of document.querySelectorAll(".cog-tab")) x.classList.toggle("on", x === b);
    $("checkView").hidden = b.dataset.tab !== "check";
    $("productsView").hidden = b.dataset.tab !== "products";
    if (b.dataset.tab === "products") loadProducts();
  };
}
$("range").onchange = () => {
  if ($("range").value === "custom") { $("dates").hidden = false; return; }
  loadDay();
};
$("from").onchange = $("to").onchange = () => { if ($("range").value === "custom") loadDay(); };
$("storeSelect").onchange = loadDay;
$("productStore").onchange = loadProducts;
$("search").oninput = loadProducts;
$("currencySelect").onchange = loadDay;
$("refresh").onclick = async () => {
  const btn = $("refresh");
  btn.disabled = true;
  btn.textContent = "Updating…";
  try {
    await api("/api/cog/sync?days=2", { method: "POST" });
    productsCache = {};
    toast("Re-read the sheet and re-costed the last 2 days.");
    loadDay();
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = "Refresh"; }
};

(async function boot() {
  try {
    const [setup, st] = await Promise.all([api("/api/setup"), api("/api/cog/status")]);
    status = st;
    $("currencySelect").innerHTML = setup.currency_options
      .map((c) => `<option${c === setup.display_currency ? " selected" : ""}>${esc(c)}</option>`).join("");
    const opts = st.stores.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
    $("storeSelect").innerHTML = `<option value="all">All stores</option>` + opts;
    $("productStore").innerHTML = opts;
    if (!st.sheet) {
      $("problems").innerHTML = `<div class="warn">The orders sheet isn't connected yet.</div>`;
    }
  } catch (e) { toast(e.message, true); }
  loadDay();
})();
