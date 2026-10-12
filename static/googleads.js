/* ProfitDesk Apps → Google Ads: one row per Shopify product (its Google variants combined), 7 / 14 /
   30 day columns, and a variant breakdown. Every figure comes from the server (google_desk.py). */

const $ = (id) => document.getElementById(id);
let data = null, win = Number(PDUrl.get("w") || 30), shown = 60, sort = { key: "cost", win: 30, dir: -1 }, openP = null, vWin = 30;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, digits = 2) {
  if (v === null || v === undefined) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: data?.currency || "USD", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v); }
  catch { return Number(v).toFixed(digits); }
}
const n = (v, d = 0) => (v === null || v === undefined ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v) => (v === null || v === undefined ? "—" : (v * 100).toFixed(2) + "%");
const ratio = (v) => (v === null || v === undefined ? "—" : v.toFixed(2));
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/googleads"; throw new Error("Log in again."); }
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

const VERDICT = { scale: ["invoice", "Scale"], keep: ["catalog", "Keep"], watch: ["changed", "Watch"], cut: ["bad", "Cut"], learning: ["new", "Learning"] };
// ROAS cell colour: how far over / under the product's own breakeven ROAS it is
function heat(m) {
  if (!m || m.roas === null || m.roas === undefined) return "";
  if (!m.be_roas) return "gd-h0";
  const r = m.roas / m.be_roas;
  return r >= 1.3 ? "gd-h4" : r >= 1 ? "gd-h3" : r >= 0.8 ? "gd-h2" : "gd-h1";
}

async function load(fresh = false) {
  const store = $("store").value || PDUrl.get("store") || "";
  $("refresh").disabled = true;
  $("sub").textContent = "Reading Google Ads and Shopify…";
  try { data = await api(`/api/googleads?store=${encodeURIComponent(store)}${fresh ? "&fresh=1" : ""}`); }
  catch (e) { const m = typeof e.message === "string" ? e.message : "Couldn't load Google Ads."; toast(m, true); $("refresh").disabled = false; $("sub").textContent = m; return; }
  $("refresh").disabled = false;
  if (!$("store").options.length || fresh === "stores") {
    $("store").innerHTML = data.stores.map((s) => `<option value="${s.id}"${s.direct ? "" : " disabled"}>${esc(s.name)}${s.direct ? "" : " (needs access)"}</option>`).join("");
  }
  if (data.store) $("store").value = String(data.store);
  PDUrl.set({ store: data.store || null });
  if (data.error) {
    $("problems").innerHTML = `<div class="warn">${esc(data.error)}</div>`;
    $("cards").innerHTML = "";
    $("table").innerHTML = "";
    $("sub").textContent = "";
    return;
  }
  $("problems").innerHTML = data.missing_product_ids
    ? `<div class="notice">Shopify sales are still being matched to products for this store (a few minutes after the update). Refresh shortly.</div>` : "";
  $("help").innerHTML = `<p><b>Shopify sales / qty</b>: everything the product sold on the store in the period, all channels (fair for stores
      that only advertise on Google). <b>Google conv. value</b>: what Google says its ads sold (its own attribution).
      <b>COG</b>: from invoices and product history. <b>Fees</b>: payment fees at this store's rate (${pct(data.fee_rate)}).</p>
    <p><b>Net profit</b> = sales − COG − fees − Google spend. <b>ROAS</b> = sales ÷ spend. <b>POAS</b> = net profit ÷ spend.
      <b>Breakeven ROAS</b>: the ROAS where the product stops losing money (sales ÷ (sales − COG − fees)).</p>
    <p><b>Verdicts</b>: Learning = under ${money(data.min_spend, 0)} spend in the period · Scale = ROAS 30%+ over breakeven ·
      Keep = over breakeven · Watch = up to 20% under · Cut = further under, or spend with no sales.
      ROAS colours follow the same bands. Google's days are the ad account's own.</p>
    <p><b>Not on a product</b>: Performance Max also spends on video, display and search ads that Google doesn't put on any product,
      and Search campaigns have no products. That spend is in the totals (Google spend, ROAS, net profit) so they match your Google
      Ads account, but in no product row.</p>`;
  drawCards();
  draw();
}

function drawCards() {
  $("sub").textContent = `${win} days: ${data.summary[win].start} to ${data.today} (today so far included, same as the dashboard) · ${data.currency}${data.google_currency && data.google_currency !== data.currency ? ` (Google spend converted from ${data.google_currency})` : ""}`
    + ` · updated ${new Date(data.cached_at * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
  const s = data.summary[win];
  const card = (label, value, note = "", cls = "") => `<div class="card"><div class="card-label">${label}</div>
      <div class="card-value ${cls}">${value}</div><div class="card-note">${note}</div></div>`;
  $("cards").innerHTML =
    card("Products", n(s.products), `with spend or sales · ${win} days`) +
    card("Google spend", money(s.cost, 0), s.cost_other >= 0.5
      ? `whole account · on products ${money(s.cost_products, 0)} · not on a product ${money(s.cost_other, 0)}`
      : `${n(s.clicks)} clicks · CPC ${money(s.cpc)}`) +
    card("Shopify qty", n(s.qty), `items sold · ${n(s.orders)} orders`) +
    card("Shopify sales", money(s.sales, 0), `refunds ${money(s.refunds, 0)} · Google says ${money(s.value, 0)}`) +
    card("ROAS", ratio(s.roas), `breakeven ${ratio(s.be_roas)}`) +
    card("Sum COG", money(s.cog, 0), `fees ${money(s.fees, 0)}`) +
    card("Net profit", money(s.profit, 0), "sales − refunds − COG − fees − Google spend", s.profit >= 0 ? "pos" : "neg") +
    card("POAS", ratio(s.poas), "net profit ÷ spend", (s.poas ?? 0) >= 0 ? "pos" : "neg");
}

function rows() {
  const q = $("q").value.trim().toLowerCase(), v = $("verdict").value, st = $("status").value;
  const list = data.products.filter((p) => (!q || p.title.toLowerCase().includes(q))
    && (!v || p.w[win].verdict === v) && (!st || p.status === st) && (p.w[30].cost || p.w[30].sales));
  const get = (p) => {
    const x = p.w[sort.win][sort.key];
    return sort.key === "title" ? p.title.toLowerCase() : (x === null || x === undefined ? -Infinity : x);
  };
  return list.sort((a, b) => (get(a) > get(b) ? 1 : get(a) < get(b) ? -1 : 0) * sort.dir);
}

function head(label, key, w = win, title = "") {
  const on = sort.key === key && sort.win === w;
  return `<th class="gd-sort${on ? " on" : ""}" data-key="${key}" data-w="${w}" title="${esc(title)}">${label}${on ? (sort.dir < 0 ? " ↓" : " ↑") : ""}</th>`;
}

function draw() {
  const list = rows();
  $("count").textContent = `(${list.length} products)`;
  const W = [7, 14, 30];
  $("table").innerHTML = `<thead><tr><th class="gd-stick">Product</th><th>Status</th><th>Variants</th>${head("Verdict", "roas", win, "Sort by ROAS")}
      ${head("BE ROAS", "be_roas", win, "Breakeven ROAS")}
      ${W.map((w) => head(`ROAS ${w}D`, "roas", w) + head(`$ ${w}D`, "cost", w, "Google spend") + head(`Sales ${w}D`, "sales", w, "Shopify sales") + head(`👁 ${w}D`, "impressions", w, "Impressions")).join("")}
      ${head("Clicks", "clicks")}${head("CTR", "ctr")}${head("CPC", "cpc")}${head("Qty", "qty")}${head("G conv.", "conversions", win, "Google conversions")}${head("G value", "value", win, "Google conversion value")}
      ${head("Sum COG", "cog")}${head("Fees", "fees")}${head("Net profit", "profit")}${head("POAS", "poas")}</tr></thead>
    <tbody>${list.slice(0, shown).map((p, i) => {
      const m = p.w[win], [vc, vl] = VERDICT[m.verdict] || ["new", m.verdict];
      return `<tr>
        <td class="gd-stick gd-prod">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : `<span class="gd-noimg"></span>`}
          <span>${p.handle && data.shop ? `<a href="https://${esc(data.shop)}/products/${esc(p.handle)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</span></td>
        <td><span class="src ${p.status === "Active" ? "invoice" : "new"}">${esc(p.status || "—")}</span></td>
        <td><button class="btn btn-sm btn-ghost gd-var" data-i="${data.products.indexOf(p)}">${p.variants.length} ▾</button></td>
        <td><span class="src ${vc}">${vl}</span></td>
        <td>${ratio(m.be_roas)}</td>
        ${W.map((w) => { const x = p.w[w]; return `<td class="${heat(x)}">${ratio(x.roas)}</td><td>${money(x.cost)}</td><td>${money(x.sales)}</td><td>${n(x.impressions)}</td>`; }).join("")}
        <td>${n(m.clicks)}</td><td>${pct(m.ctr)}</td><td>${money(m.cpc)}</td><td>${n(m.qty)}</td><td>${n(m.conversions, 1)}</td><td>${money(m.value)}</td>
        <td>${money(m.cog)}</td><td>${money(m.fees)}</td><td class="${m.profit >= 0 ? "pos" : "neg"}">${money(m.profit)}</td><td class="${(m.poas ?? 0) >= 0 ? "pos" : "neg"}">${ratio(m.poas)}</td></tr>`;
    }).join("") || `<tr><td class="hint" colspan="30">No products match.</td></tr>`}</tbody>`;
  $("more").innerHTML = list.length > shown ? `<button class="btn btn-sm btn-ghost" id="moreBtn">Show ${Math.min(100, list.length - shown)} more</button>` : "";
  if ($("moreBtn")) $("moreBtn").onclick = () => { shown += 100; draw(); };
  for (const th of $("table").querySelectorAll(".gd-sort")) th.onclick = () => {
    const key = th.dataset.key, w = Number(th.dataset.w);
    sort = sort.key === key && sort.win === w ? { key, win: w, dir: -sort.dir } : { key, win: w, dir: key === "title" ? 1 : -1 };
    draw();
  };
  for (const b of $("table").querySelectorAll(".gd-var")) b.onclick = () => openVariants(data.products[+b.dataset.i]);
}

function openVariants(p) {
  openP = p;
  vWin = win;
  for (const b of $("vWin").querySelectorAll("[data-w]")) b.classList.toggle("on", Number(b.dataset.w) === vWin);
  $("vModal").hidden = false;
  drawVariants();
}

function drawVariants() {
  const p = openP, m = p.w[vWin];
  $("vTitle").textContent = p.title;
  const card = (label, value, cls = "") => `<div class="card"><div class="card-value ${cls}">${value}</div><div class="card-label">${label}</div></div>`;
  $("vCards").innerHTML = card("Variants", n(p.variants.length)) + card("Google spend", money(m.cost)) + card("Google conv.", n(m.conversions, 1))
    + card("Shopify qty", n(m.qty)) + card("Shopify sales", money(m.sales)) + card("ROAS", ratio(m.roas)) + card("Sum COG", money(m.cog))
    + card("Net profit", money(m.profit), m.profit >= 0 ? "pos" : "neg") + card("POAS", ratio(m.poas), (m.poas ?? 0) >= 0 ? "pos" : "neg");
  $("vTable").innerHTML = `<thead><tr><th>Variant</th><th>Price</th><th>COG / unit</th><th>Google spend</th><th>Impressions</th><th>Clicks</th>
      <th>G conv.</th><th>G value</th><th>Shopify qty</th><th>Shopify sales</th><th>Sum COG</th><th>Fees</th><th>Net profit</th><th>POAS</th><th>CTR</th><th>CPC</th><th>ROAS</th><th>Verdict</th></tr></thead>
    <tbody>${p.variants.map((v) => {
      const x = v.w[vWin], [vc, vl] = VERDICT[x.verdict] || ["new", x.verdict];
      return `<tr><td class="gd-prod">${v.image ? `<img src="${esc(v.image)}" alt="" loading="lazy">` : ""}<span>${esc(v.title)}</span></td>
        <td>${money(v.price)}</td><td>${money(v.unit_cog)}</td><td>${money(x.cost)}</td><td>${n(x.impressions)}</td><td>${n(x.clicks)}</td>
        <td>${n(x.conversions, 1)}</td><td>${money(x.value)}</td><td>${n(x.qty)}</td><td>${money(x.sales)}</td><td>${money(x.cog)}</td><td>${money(x.fees)}</td>
        <td class="${x.profit >= 0 ? "pos" : "neg"}">${money(x.profit)}</td><td class="${(x.poas ?? 0) >= 0 ? "pos" : "neg"}">${ratio(x.poas)}</td>
        <td>${pct(x.ctr)}</td><td>${money(x.cpc)}</td><td class="${heat(x)}">${ratio(x.roas)}</td><td><span class="src ${vc}">${vl}</span></td></tr>`;
    }).join("")}</tbody>`;
}

function exportCsv() {
  const list = rows();
  const W = [7, 14, 30];
  const head = ["Product", "Status", "Variants", "Verdict", "Breakeven ROAS", ...W.flatMap((w) => [`ROAS ${w}D`, `Spend ${w}D`, `Sales ${w}D`, `Impressions ${w}D`]),
    "Clicks", "CTR", "CPC", "Qty", "Google conversions", "Google conv. value", "Sum COG", "Fees", "Net profit", "POAS"];
  const fmt = (v) => (v === null || v === undefined ? "" : typeof v === "number" ? v.toFixed(2) : String(v));
  const lines = [head, ...list.map((p) => {
    const m = p.w[win];
    return [p.title, p.status, p.variants.length, m.verdict, fmt(m.be_roas), ...W.flatMap((w) => [fmt(p.w[w].roas), fmt(p.w[w].cost), fmt(p.w[w].sales), p.w[w].impressions]),
      m.clicks, fmt(m.ctr), fmt(m.cpc), m.qty, fmt(m.conversions), fmt(m.value), fmt(m.cog), fmt(m.fees), fmt(m.profit), fmt(m.poas)];
  })];
  const csv = lines.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `Google products ${$("store").selectedOptions[0]?.textContent || ""} ${win}d ${data.today}.csv`;
  a.click();
}

for (const b of $("win").querySelectorAll("[data-w]")) b.onclick = () => {
  win = Number(b.dataset.w);
  for (const x of $("win").querySelectorAll("[data-w]")) x.classList.toggle("on", x === b);
  sort = { ...sort, win: sort.key === "title" ? sort.win : win };
  PDUrl.set({ w: win === 30 ? null : win });
  if (data && !data.error) { drawCards(); draw(); }
};
for (const b of $("vWin").querySelectorAll("[data-w]")) b.onclick = () => {
  vWin = Number(b.dataset.w);
  for (const x of $("vWin").querySelectorAll("[data-w]")) x.classList.toggle("on", x === b);
  drawVariants();
};
$("vClose").onclick = () => { $("vModal").hidden = true; };
$("vModal").onclick = (e) => { if (e.target === $("vModal")) $("vModal").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("vModal").hidden = true; });
for (const id of ["q", "verdict", "status"]) $(id).oninput = () => { shown = 60; draw(); };
$("store").onchange = () => { PDUrl.set({ store: $("store").value }); load(); };
$("refresh").onclick = () => load(true);
$("csv").onclick = exportCsv;
for (const b of $("win").querySelectorAll("[data-w]")) b.classList.toggle("on", Number(b.dataset.w) === win);
load();
