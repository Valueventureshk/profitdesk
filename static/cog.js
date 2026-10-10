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
  const custom = $("range").value === "custom";
  PDUrl.set({ range: $("range").value, store: $("storeSelect").value === "all" ? null : $("storeSelect").value,
              from: custom ? from : null, to: custom ? to : null });
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
  PDUrl.set({ pstore: sid, q: $("search").value.trim() });
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

/* ------------------------------------------------ invoices */

const STATUS = {
  ok: ["ok", "Matches"], new: ["new", "First invoice"], changed: ["changed", "Price changed"],
  duplicate: ["bad", "Already invoiced"], unmatched: ["bad", "No matching order"],
};

async function loadInvoices() {
  let d;
  try { d = await api("/api/invoices"); } catch (e) { toast(e.message, true); return; }
  $("invBadge").hidden = !d.to_check;
  $("invBadge").textContent = d.to_check;
  $("invCount").textContent = `${d.invoices.length} invoices`;
  $("invList").innerHTML = d.invoices.length ? `<thead><tr><th>Date</th><th>Invoice</th><th>Store</th>
      <th>Lines</th><th>Total</th><th>To check</th></tr></thead><tbody>${d.invoices.map((i) => `
    <tr class="clickable" data-inv="${i.id}"><td>${i.date ? niceDate(i.date) : "—"}</td>
      <td class="name">${esc(i.supplier || "?")} #${esc(i.number || "—")}<span class="sub">${esc(i.filename || "")}</span></td>
      <td>${esc(i.store)}</td><td>${i.lines}</td><td>${money(i.total, "USD")}</td>
      <td>${[i.to_check ? `<span class="src changed">${i.to_check} price change${i.to_check === 1 ? "" : "s"}</span>` : "",
             i.problems ? `<span class="src bad">${i.problems} unmatched</span>` : "",
             i.missing ? `<span class="src catalog">${i.missing} missing</span>` : ""].join(" ") || "✓"}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">No invoices uploaded yet.</td></tr></tbody>`;
  for (const tr of $("invList").querySelectorAll("[data-inv]")) tr.onclick = () => showInvoice(tr.dataset.inv);
}

async function showInvoice(id) {
  let d;
  try { d = await api(`/api/invoices/${id}`); } catch (e) { toast(e.message, true); return; }
  const i = d.invoice;
  const rows = d.lines.map((l) => {
    const [cls, label] = STATUS[l.status] || ["bad", l.status];
    const approve = l.status === "changed" && !l.approved_at
      ? `<button class="btn btn-sm btn-primary" data-write-only data-approve="${l.line_no}">Checked with supplier — use new price</button>`
      : (l.approved_at ? `<span class="hint">Approved${l.approved_by ? " by " + esc(l.approved_by) : ""}</span>` : "");
    return `<tr><td class="name">${esc(l.order_name || l.order_key)}<span class="sub">${esc(l.title)}</span>
        ${l.product && l.product !== l.title ? `<span class="sub">→ ${esc(l.product)}</span>` : ""}</td>
      <td>${money(l.amount, "USD")}</td>
      <td>${l.previous !== null && l.previous !== undefined ? money(l.previous, "USD") : "—"}</td>
      <td class="inv-status"><span class="src ${cls}">${label}</span>${l.note ? `<span class="sub">${esc(l.note)}</span>` : ""}${approve}</td></tr>`;
  }).join("");
  const missing = i.missing.length ? `<div class="inv-missing"><strong>Fulfilled but not on this invoice (${i.missing.length})</strong>
      ${i.missing.map((m) => `<div>${esc(m.order)} <span class="hint">${esc(m.products.join(" · "))}</span></div>`).join("")}
      <p class="hint">These were marked fulfilled in Shopify within this invoice's order range. Ask the supplier if they'll be on a later invoice.</p></div>` : "";
  $("invDetail").hidden = false;
  $("invDetail").innerHTML = `<div class="panel-head"><h2>${esc(i.supplier || "?")} invoice #${esc(i.number || "—")}
      · ${esc(i.store)} · ${i.date ? niceDate(i.date) : ""}</h2>
      <span><button class="btn btn-sm btn-ghost" id="invClose">Close</button>
      <button class="btn btn-sm btn-danger" id="invDelete" data-write-only>Delete</button></span></div>
    ${missing}
    <div class="table-wrap"><table class="table"><thead><tr><th>Order / product</th><th>Invoice</th><th>Before</th><th>Check</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  $("invClose").onclick = () => { $("invDetail").hidden = true; };
  $("invDelete").onclick = async () => {
    if (!confirm(`Delete invoice #${i.number}? Its orders go back to their previous costs.`)) return;
    await api(`/api/invoices/${id}`, { method: "DELETE" });
    $("invDetail").hidden = true;
    toast("Invoice deleted.");
    loadInvoices();
  };
  for (const b of $("invDetail").querySelectorAll("[data-approve]")) {
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/api/invoices/${id}/approve/${b.dataset.approve}`, { method: "POST" });
        toast("New price saved for this product.");
        productsCache = {};
        showInvoice(id);
        loadInvoices();
      } catch (e) { toast(e.message, true); b.disabled = false; }
    };
  }
  $("invDetail").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function uploadInvoices(files) {
  for (const f of files) {
    $("invResult").innerHTML = `<div class="hint">Reading ${esc(f.name)}…</div>`;
    try {
      const r = await api(`/api/invoices?filename=${encodeURIComponent(f.name)}`,
                          { method: "POST", body: await f.arrayBuffer() });
      const c = r.counts;
      const off = Math.abs(r.sum - r.total) >= 0.01
        ? ` <span class="est">Lines add up to ${money(r.sum, "USD")}, not the invoice total ${money(r.total, "USD")}.</span>` : "";
      $("invResult").innerHTML = `<div class="notice ok">${esc(r.supplier)} #${esc(r.number)}: ${r.lines} lines,
        ${money(r.total, "USD")}. ${c.ok || 0} match · ${c.new || 0} first time · ${c.changed || 0} price changes ·
        ${(c.unmatched || 0) + (c.duplicate || 0)} problems · ${r.missing} fulfilled orders missing.${off}</div>`;
      await loadInvoices();
      showInvoice(r.id);
    } catch (e) {
      $("invResult").innerHTML = `<div class="warn">${esc(f.name)}: ${esc(e.message)}</div>`;
    }
  }
}

$("invFile").onchange = (e) => { uploadInvoices([...e.target.files]); e.target.value = ""; };
const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  uploadInvoices([...e.dataTransfer.files]);
});

/* ------------------------------------------------ wiring */

for (const b of document.querySelectorAll(".cog-tab")) {
  b.onclick = () => {
    for (const x of document.querySelectorAll(".cog-tab")) x.classList.toggle("on", x === b);
    PDUrl.set({ tab: b.dataset.tab === "check" ? null : b.dataset.tab });
    $("checkView").hidden = b.dataset.tab !== "check";
    $("productsView").hidden = b.dataset.tab !== "products";
    $("invoicesView").hidden = b.dataset.tab !== "invoices";
    if (b.dataset.tab === "products") loadProducts();
    if (b.dataset.tab === "invoices") loadInvoices();
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
  // Back to the view from before a refresh
  const has = (sel, v) => v && [...sel.options].some((o) => o.value === v);
  if (has($("range"), PDUrl.get("range"))) $("range").value = PDUrl.get("range");
  if ($("range").value === "custom") { $("from").value = PDUrl.get("from") || ""; $("to").value = PDUrl.get("to") || ""; }
  if (has($("storeSelect"), PDUrl.get("store"))) $("storeSelect").value = PDUrl.get("store");
  if (has($("productStore"), PDUrl.get("pstore"))) $("productStore").value = PDUrl.get("pstore");
  if (PDUrl.get("q")) $("search").value = PDUrl.get("q");
  const tab = document.querySelector(`.cog-tab[data-tab="${PDUrl.get("tab")}"]`);
  loadDay();
  loadInvoices();
  if (tab) tab.click();
})();
