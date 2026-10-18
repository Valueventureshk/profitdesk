/* EcomVital COG + Products Monitor.

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

// Check orders lives in cogdesk.js (loaded after this file)
async function loadDay() { if (window.cdLoadOrders) return window.cdLoadOrders(); }

/* ------------------------------------------------ products */

async function loadProducts() {
  const sid = $("productStore").value;
  if (!sid) return;
  PDUrl.set({ pstore: sid, q: $("search").value.trim() });
  loadPdk();
  if (sid === "all") {
    $("products").innerHTML = `<tbody><tr><td class="hint">Pick one store to see each variant's cost.</td></tr></tbody>`;
    $("productCount").textContent = "";
    return;
  }
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

/* ------------------------------------------------ products desk (one row per product) */

let pdk = { store: null, rows: [], busy: false };
const shortDate = (iso) => (iso ? new Date(iso.slice(0, 10) + "T12:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" }) : "—");

// Products sold in a period (store days); "all" = all time
function pdkPeriod() {
  const v = $("pdkRange").value;
  $("pdkDates").hidden = v !== "custom";
  if (v === "all") return ["", ""];
  const today = new Date((status?.today || iso(new Date())) + "T12:00:00");
  const back = (n) => { const d = new Date(today); d.setDate(d.getDate() - n); return iso(d); };
  if (v === "today") return [iso(today), iso(today)];
  if (v === "yesterday") return [back(1), back(1)];
  if (v === "7") return [back(6), iso(today)];
  if (v === "30") return [back(29), iso(today)];
  return [$("pdkFrom").value, $("pdkTo").value || $("pdkFrom").value];
}

async function loadPdk(force, quiet) {
  const sid = $("productStore").value;
  if (!sid) return;
  const [from, to] = pdkPeriod();
  if ($("pdkRange").value === "custom" && !from) return;
  const key = `${sid}|${from}|${to}`;
  if (force || pdk.store !== key) {
    if (!quiet) {
      pdk = { store: key, rows: [], busy: true };
      $("pdk").innerHTML = `<tbody><tr><td class="hint">Loading products… (the first time can take a minute: images and listed dates come from Shopify)</td></tr></tbody>`;
    }
    pdk.busy = true;
    try {
      const d = await api(`/api/cog/pdesk?store=${encodeURIComponent(sid)}&start=${from}&end=${to}`);
      pdk = { store: key, rows: d.products, busy: false };
      $("pdkFresh").textContent = `updated ${new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
    } catch (e) { if (!quiet) $("pdk").innerHTML = `<tbody><tr><td class="hint">${esc(e.message)}</td></tr></tbody>`; return; }
    finally { pdk.busy = false; }
  }
  drawPdk();
}
// Keep the open Products table current (new orders are costed every 5 minutes)
setInterval(() => {
  if (!$("productsView").hidden && !pdk.busy && !document.hidden && !document.querySelector(".drawer:not([hidden])")) loadPdk(true, true);
}, 60000);

function drawPdk() {
  const q = $("search").value.trim().toLowerCase();
  const sort = $("pdkSort").value, issues = $("pdkIssues").checked;
  const all = $("productStore").value === "all";
  let rows = pdk.rows.filter((r) => (!q || r.title.toLowerCase().includes(q) || (r.supplier || "").toLowerCase().includes(q)
    || (r.material || "").toLowerCase().includes(q)) && (!issues || r.tickets || r.notes.complaints));
  const key = { units: (r) => r.units, tickets: (r) => r.tickets, complaints: (r) => r.notes.complaints,
    recent: (r) => r.last_order || "", listed: (r) => r.listed || "", cost: (r) => r.cost || 0 }[sort];
  rows = rows.slice().sort((a, b) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : b.units - a.units));
  $("pdkCount").textContent = `${rows.length} products${$("pdkRange").value === "all" ? "" : " sold in this period"}`;
  $("pdk").innerHTML = `<thead><tr><th></th><th>Product</th><th>Supplier</th><th class="num">Selling price</th><th class="num">Latest cost</th><th class="num">${$("pdkRange").value === "all" ? "Bought" : "Bought in period"}</th>
      <th>Size chart</th><th>Quality check</th><th>Material</th><th class="num">CS tickets</th><th>Listed</th><th>Last order</th><th>Notes</th></tr></thead>
    <tbody>${rows.slice(0, 500).map((r) => {
      const i = pdk.rows.indexOf(r);
      const last = r.notes.last;
      return `<tr data-pdk="${i}">
        <td class="pdk-img">${r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy">` : `<span class="pdk-noimg"></span>`}</td>
        <td class="name pdk-name"><a href="#" data-open="${i}">${esc(r.title)}</a>
          <span class="sub">${all ? esc(r.store) + " · " : ""}${esc(r.status)}</span></td>
        <td><span class="pdk-edit" data-edit="supplier" data-i="${i}" title="Click to change" data-write-only-edit>${esc(r.supplier) || `<span class="hint">add</span>`}</span></td>
        <td class="num">${r.price_low !== null ? money(r.price_low, r.price_currency) : "—"}${
          r.price_high !== null && r.price_high !== r.price_low ? `<span class="sub">to ${money(r.price_high, r.price_currency)}</span>` : ""}</td>
        <td class="num"><a href="#" data-open="${i}">${r.cost !== null ? money(r.cost, "USD") : "—"}</a>
          ${r.cost_note ? `<span class="sub">${esc(r.cost_note)}</span>` : r.cost_is_bundle ? `<span class="sub flag">from a bundle order</span>` : ""}
          ${r.variant_low !== null ? `<span class="sub">by variant ${money(r.variant_low, "USD")}–${money(r.variant_high, "USD")}</span>` : ""}
          ${r.price_changes ? `<span class="sub flag">changed ${r.price_changes}×</span>` : ""}
          ${r.cost_day ? `<span class="sub">${shortDate(r.cost_day)}</span>` : ""}</td>
        <td class="num"><b>${r.units}</b><span class="sub">${r.orders} orders</span></td>
        <td><button class="pdk-chart ${r.size_chart || "unset"}" data-chart="${i}" title="Click to switch Yes / No${r.size_chart_auto ? ` · EcomVital's size chart: ${esc(r.size_chart_auto)}` : ""}">${
          r.size_chart === "yes" ? "Yes" : r.size_chart === "no" ? "No" : "pick"}</button>${r.size_chart_auto ? `<span class="sub">chart set up</span>` : ""}</td>
        <td><a href="#" data-open="${i}" data-at="qc">${r.qc_photos ? `${r.qc_photos} photo${r.qc_photos > 1 ? "s" : ""}` : `<span class="hint">add photos</span>`}</a></td>
        <td><span class="pdk-edit" data-edit="material" data-i="${i}" title="Click to change">${esc(r.material) || `<span class="hint">add</span>`}</span></td>
        <td class="num">${r.tickets ? `<a href="#" data-open="${i}" data-at="tickets" class="${r.tickets >= 3 ? "pdk-hot" : ""}">${r.tickets}</a>` : `<span class="hint">0</span>`}</td>
        <td>${shortDate(r.listed)}</td><td>${shortDate(r.last_order)}</td>
        <td class="pdk-notes"><a href="#" data-open="${i}" data-at="notes">${r.notes.complaints ? `<span class="src bad">${r.notes.complaints} complaint${r.notes.complaints > 1 ? "s" : ""}</span> ` : ""}${
          last ? esc(last.text.slice(0, 60)) + (last.text.length > 60 ? "…" : "") : `<span class="hint">add</span>`}</a></td></tr>`;
    }).join("")}</tbody>`;
  if (rows.length > 500) $("pdkCount").textContent += " (showing the first 500; search to narrow)";
}

$("pdk").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-open]");
  if (open) { e.preventDefault(); openPdk(pdk.rows[+open.dataset.open], open.dataset.at); return; }
  const ch = e.target.closest("[data-chart]");
  if (ch) {
    const r = pdk.rows[+ch.dataset.chart];
    const next = r.size_chart === "yes" ? "no" : "yes";
    try {
      await api("/api/cog/pdesk/info", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store: r.store_id, pid: r.pid, size_chart: next }) });
      r.size_chart = next;
      drawPdk();
    } catch (err) { toast(err.message, true); }
    return;
  }
  const ed = e.target.closest("[data-edit]");
  if (!ed) return;
  const r = pdk.rows[+ed.dataset.i], field = ed.dataset.edit;
  const v = prompt(field === "supplier" ? `Supplier for ${r.title}` : `Material for ${r.title} (e.g. "PU leather upper, rubber sole")`, r[field] || "");
  if (v === null) return;
  try {
    await api("/api/cog/pdesk/info", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: r.store_id, pid: r.pid, [field]: v }) });
    r[field] = v.trim();
    drawPdk();
    toast("Saved.");
  } catch (err) { toast(err.message, true); }
});
$("pdkSort").onchange = drawPdk;
$("pdkIssues").onchange = drawPdk;

async function openPdk(r, at) {
  $("pdkTitle").textContent = r.title;
  $("pdkBody").innerHTML = `<p class="hint">Loading…</p>`;
  $("pdkDrawer").hidden = false;
  let d;
  try { d = await api(`/api/cog/pdesk/item?store=${r.store_id}&pid=${encodeURIComponent(r.pid)}`); }
  catch (e) { $("pdkBody").innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
  const c = d.cost || {};
  const shop = /^\d+$/.test(r.pid) ? `<a href="https://${esc(r.shop)}/admin/products/${esc(r.pid)}" target="_blank" rel="noopener">Open in Shopify</a>` : "";
  $("pdkBody").innerHTML = `
    <div class="block pdk-head">${r.image ? `<img src="${esc(r.image)}" alt="">` : ""}<div>
      <div class="hint">${esc(r.store)} · ${esc(r.status)} · listed ${shortDate(r.listed)}</div>
      <div><b>${r.units}</b> bought in ${r.orders} orders · first ${shortDate(r.first_order)} · last ${shortDate(r.last_order)}</div>
      <div>Selling price: ${r.price_low !== null ? money(r.price_low, r.price_currency) + (r.price_high !== r.price_low ? " – " + money(r.price_high, r.price_currency) : "") : "—"}
        · size chart: ${r.size_chart === "yes" ? "yes" : r.size_chart === "no" ? "no" : "not picked"}${r.size_chart_auto ? ` (set up: ${esc(r.size_chart_auto)})` : ""} ${shop ? " · " + shop : ""}</div></div></div>
    <div class="block"><h3>Latest confirmed cost: ${r.cost_note ? `${money(r.cost, "USD")} ${esc(r.cost_note)}` : c.cost !== null && c.cost !== undefined ? money(c.cost, "USD") : "—"}</h3>
      ${r.cost_note ? `<p class="hint">Price history below is per pair, as the supplier billed it.</p>` : ""}
      <p class="hint">${c.cost_day ? `Since ${shortDate(c.cost_day)}` : ""}${c.cost_supplier ? ` · ${esc(c.cost_supplier)}` : ""}${c.cost_is_bundle ? " · only bundle orders so far" : ""}</p>
      ${(c.changes || []).length > 1 ? `<table class="table pdk-mini"><thead><tr><th>From</th><th>Price for one</th><th>Supplier</th></tr></thead><tbody>${
        c.changes.slice().reverse().map((h) => `<tr><td>${shortDate(h.day)}</td><td class="num">${money(h.price, "USD")}</td><td>${esc(h.supplier || "")}</td></tr>`).join("")}</tbody></table>`
        : `<p class="hint">The price hasn't changed.</p>`}
      ${(c.variants || []).length > 1 ? `<details><summary class="hint">Per variant (${c.variants.length})</summary><table class="table pdk-mini"><tbody>${
        c.variants.map((v) => `<tr><td>${esc(v.variant)}</td><td class="num">${money(v.cost, "USD")}</td></tr>`).join("")}</tbody></table></details>` : ""}</div>
    <div class="block" id="pdkNotes"><h3>Notes</h3>
      <div class="pdk-add" data-write-only>
        <select id="pdkKind" class="control"><option value="complaint">Complaint</option><option value="note">Note</option></select>
        <textarea id="pdkText" class="control" rows="2" placeholder="e.g. Customer says the sole came off after 2 weeks (#AS13184)"></textarea>
        <button class="btn btn-sm btn-primary" id="pdkAddNote">Add</button></div>
      ${d.notes.length ? d.notes.map((n) => `<div class="pdk-note"><span class="src ${n.kind === "complaint" ? "bad" : "catalog"}">${n.kind === "complaint" ? "Complaint" : "Note"}</span>
        ${esc(n.text)}<span class="sub">${esc((n.by || "").split("@")[0])} · ${shortDate(n.at)}
        <a href="#" data-del-note="${n.id}" data-write-only>delete</a></span></div>`).join("") : `<p class="hint">No notes yet.</p>`}</div>
    <div class="block" id="pdkQc"><h3>Quality check</h3>
      <div class="pdk-add" data-write-only>
        <input type="file" id="pdkFiles" accept="image/*,.heic" multiple class="control">
        <input id="pdkCaption" class="control" placeholder="What you checked (optional), e.g. sample from supplier, stitching OK">
        <button class="btn btn-sm btn-primary" id="pdkUpload">Upload</button></div>
      <div class="pdk-photos">${d.photos.map((p) => `<figure><a href="/api/cog/pdesk/qc/${p.id}" target="_blank" rel="noopener"><img src="/api/cog/pdesk/qc/${p.id}?thumb=1" alt="" loading="lazy"></a>
        <figcaption>${esc(p.caption || "")}<span class="sub">${esc((p.by || "").split("@")[0])} · ${shortDate(p.at)} <a href="#" data-del-qc="${p.id}" data-write-only>delete</a></span></figcaption></figure>`).join("")
        || `<p class="hint">No photos yet.</p>`}</div></div>
    <div class="block" id="pdkTickets"><h3>CS tickets (${d.tickets.length})</h3>
      ${d.tickets.length ? d.tickets.map((t) => `<a class="pdk-ticket" href="/inbox?ticket=${t.id}" target="_blank" rel="noopener">
        <b>${esc(t.subject || "(no subject)")}</b><span class="sub">${esc(t.customer_name || "")} · ${shortDate(t.created_at)} · ${esc(t.status)}${t.labels ? " · " + esc(t.labels) : ""} · matched by ${esc(t.how || "")}</span>
        ${t.summary ? `<span class="pdk-sum">${esc(t.summary)}</span>` : ""}</a>`).join("") : `<p class="hint">No tickets matched to this product.</p>`}</div>`;
  if (at) document.getElementById({ qc: "pdkQc", notes: "pdkNotes", tickets: "pdkTickets" }[at])?.scrollIntoView({ block: "start" });
  const reopen = async (msg) => { toast(msg); await loadPdk(true); openPdk(pdk.rows.find((x) => x.store_id === r.store_id && x.pid === r.pid) || r, at); };
  const add = $("pdkAddNote");
  if (add) add.onclick = async () => {
    try {
      await api("/api/cog/pdesk/notes", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store: r.store_id, pid: r.pid, kind: $("pdkKind").value, text: $("pdkText").value }) });
      at = "notes"; reopen("Note added.");
    } catch (e) { toast(e.message, true); }
  };
  const up = $("pdkUpload");
  if (up) up.onclick = async () => {
    const files = $("pdkFiles").files;
    if (!files.length) return toast("Choose photos first.", true);
    const fd = new FormData();
    fd.append("store", r.store_id); fd.append("pid", r.pid); fd.append("caption", $("pdkCaption").value);
    for (const f of files) fd.append("files", f);
    up.disabled = true; up.textContent = "Uploading…";
    try { await api("/api/cog/pdesk/qc", { method: "POST", body: fd }); at = "qc"; reopen("Photos added."); }
    catch (e) { toast(e.message, true); up.disabled = false; up.textContent = "Upload"; }
  };
  $("pdkBody").onclick = async (e) => {
    const n = e.target.closest("[data-del-note]"), q = e.target.closest("[data-del-qc]");
    if (!n && !q) return;
    e.preventDefault();
    if (!confirm(n ? "Delete this note?" : "Delete this photo?")) return;
    try {
      await api(n ? `/api/cog/pdesk/notes/${n.dataset.delNote}` : `/api/cog/pdesk/qc/${q.dataset.delQc}`, { method: "DELETE" });
      at = n ? "notes" : "qc"; reopen("Deleted.");
    } catch (err) { toast(err.message, true); }
  };
}
for (const el of document.querySelectorAll("#pdkDrawer [data-close]")) el.onclick = () => { $("pdkDrawer").hidden = true; };

/* ------------------------------------------------ invoices */

const STATUS = {
  superseded: ["catalog", "Replaced"],
  ok: ["ok", "Matches"], new: ["new", "First invoice"], changed: ["changed", "Price changed"],
  duplicate: ["bad", "Already invoiced"], unmatched: ["bad", "No matching order"],
};

// Invoice archive: every uploaded invoice by its invoice date, with the original file to download
let invAll = [];
let aiPoll = null;
const AI_CHIP = {
  pending: `<span class="src catalog" title="The AI is reading the original file to double-check it">AI checking…</span>`,
  review: `<span class="src bad" title="The AI read some orders differently. These lines aren't used for costs until someone decides.">AI disagrees</span>`,
  failed: `<span class="src catalog">AI check failed</span>`,
  ai_only: `<span class="src new" title="Only the AI could read this file; its orders add up to the invoice total">Read by AI</span>`,
};
function aiChip(i) {
  if (AI_CHIP[i.ai_check]) return AI_CHIP[i.ai_check];
  return ["agreed", "confirmed"].includes(i.ai_check) ? `<span class="ai-ok" title="${esc(i.ai_note || "")}">AI ✓</span>` : "";
}

async function loadInvoices() {
  let d;
  try { d = await api("/api/invoices"); } catch (e) { toast(e.message, true); loadPriceChanges(); return; }
  $("invBadge").hidden = !d.to_check;
  $("invBadge").textContent = d.to_check;
  invAll = d.invoices;
  loadPriceChanges();                      // after the list, so "Invoices that need a look" has them
  const keep = (id) => $(id)?.value || "";
  const was = { from: keep("arcFrom"), to: keep("arcTo"), store: keep("arcStore"), sup: keep("arcSup") };
  const storesSeen = [...new Map(invAll.filter((i) => i.store_id).map((i) => [String(i.store_id), i.store])).entries()];
  const sups = [...new Set(invAll.map((i) => i.supplier).filter(Boolean))].sort();
  $("invArchiveBar").innerHTML = `
    <label>From <input type="date" class="control" id="arcFrom" value="${esc(was.from)}"></label>
    <label>To <input type="date" class="control" id="arcTo" value="${esc(was.to)}"></label>
    <select class="control" id="arcStore"><option value="">All stores</option>${storesSeen.map(([id, n]) => `<option value="${esc(id)}"${was.store === id ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>
    <select class="control" id="arcSup"><option value="">All suppliers</option>${sups.map((x) => `<option${was.sup === x ? " selected" : ""}>${esc(x)}</option>`).join("")}</select>
    <a class="btn btn-sm btn-primary" id="arcZip">⬇ Download all (zip)</a>
    ${(() => { const n = invAll.filter((i) => i.file_ext && ["", "skipped", "failed"].includes(i.ai_check || "")).length;
      return n ? `<button class="btn btn-sm btn-ghost" id="arcAi" data-write-only title="The AI reads each original file again and compares it with the first reading">Double-check ${n} with AI</button>` : ""; })()}`;
  if ($("arcAi")) $("arcAi").onclick = async () => {
    if (!confirm("The AI reads every invoice file it hasn't checked yet and compares it with the first reading. It costs a few US cents per invoice. Start?")) return;
    $("arcAi").disabled = true;
    try { const r = await api("/api/invoices/ai-check", { method: "POST" }); toast(`${r.queued} invoices queued for the AI double-check.`); loadInvoices(); }
    catch (e) { toast(e.message, true); $("arcAi").disabled = false; }
  };
  clearTimeout(aiPoll);
  if (invAll.some((i) => i.ai_check === "pending") && !$("invoicesView").hidden) aiPoll = setTimeout(loadInvoices, 15000);
  for (const id of ["arcFrom", "arcTo", "arcStore", "arcSup"]) $(id).onchange = drawArchive;
  drawArchive();
}

function drawArchive() {
  const from = $("arcFrom").value, to = $("arcTo").value, st = $("arcStore").value, sup = $("arcSup").value;
  const list = invAll.filter((i) => (!from || (i.date || "") >= from) && (!to || (i.date || "") <= to)
    && (!st || String(i.store_id) === st) && (!sup || i.supplier === sup));
  const q = new URLSearchParams({ start: from, end: to, store: st, supplier: sup });
  $("arcZip").href = `/api/invoices/archive?${q}`;
  const files = list.filter((i) => i.file_ext).length;
  const total = list.reduce((n, i) => n + (i.total || 0), 0);
  $("invCount").textContent = `${list.length} invoices · ${money(total, "USD")} · ${files} files kept`;
  if (!list.length) { $("invList").innerHTML = `<tbody><tr><td class="hint">No invoices match.</td></tr></tbody>`; return; }
  const months = new Map();
  for (const i of list) {
    const m = (i.date || "undated").slice(0, 7);
    if (!months.has(m)) months.set(m, []);
    months.get(m).push(i);
  }
  const monthName = (m) => (m === "undated" ? "No date" : new Date(m + "-01T12:00:00").toLocaleDateString(undefined, { month: "long", year: "numeric" }));
  $("invList").innerHTML = `<thead><tr><th>Invoice date</th><th>Invoice</th><th>Store</th>
      <th>Lines</th><th>Total</th><th>To check</th><th>File</th></tr></thead>` + [...months.entries()].map(([m, rows]) => `
    <tbody><tr class="arc-month"><td colspan="7">${esc(monthName(m))} <span class="hint">· ${rows.length} invoices · ${money(rows.reduce((n, i) => n + (i.total || 0), 0), "USD")}</span></td></tr>
    ${rows.map((i) => `
    <tr class="clickable" data-inv="${i.id}"><td>${i.date ? niceDate(i.date) : "—"}</td>
      <td class="name">${esc(i.supplier || "?")} #${esc(i.number || "—")}<span class="sub">${esc(i.filename || "")}</span></td>
      <td>${esc(i.store)}</td><td>${i.lines}</td><td>${money(i.total, "USD")}</td>
      <td>${[aiChip(i), i.to_check ? `<span class="src changed">${i.to_check} price change${i.to_check === 1 ? "" : "s"}</span>` : "",
             i.problems ? `<span class="src bad">${i.problems} unmatched</span>` : "",
             i.missing ? `<span class="src catalog">${i.missing} missing</span>` : ""].join(" ").trim() || "✓"}</td>
      <td onclick="event.stopPropagation()">${i.file_ext
        ? `<a class="btn btn-sm btn-ghost" href="/api/invoices/${i.id}/file" target="_blank" rel="noopener" title="Open the original">${i.file_ext.toUpperCase()}</a>
           <a class="btn btn-sm btn-ghost" href="/api/invoices/${i.id}/file?download=1" title="Download">⬇</a>`
        : `<span class="hint" title="Uploaded before files were kept: upload it again to attach the file">not kept</span>`}</td></tr>`).join("")}
    </tbody>`).join("");
  for (const tr of $("invList").querySelectorAll("[data-inv]")) tr.onclick = () => showInvoice(tr.dataset.inv);
}

// Price changes + invoices that need a look (Needs checking), and what was already decided (History).
// Every row can carry a note: it's kept with it and becomes a lesson the AI follows when it reads invoices.
let pcTab = "check";
const noteBox = (key, note, what) => `<details class="pc-note"${note ? " open" : ""}><summary>${note ? "📝 Note" : "📝 Add a note"}</summary>
  <textarea class="control" rows="2" data-note-text="${esc(key)}" placeholder="e.g. Supplier confirmed: new fabric from Oct. (The AI reads this as a lesson.)">${esc(note || "")}</textarea>
  <button class="btn btn-sm" data-note-save="${esc(key)}" data-what="${esc(what)}">Save note</button></details>`;

async function loadPriceChanges() {
  const box = $("priceChanges");
  box.hidden = false;
  let d, h = null;
  try { d = await api("/api/invoices/changes"); } catch { return; }
  if (pcTab === "history") { try { h = await api("/api/invoices/changes?view=history"); } catch { h = { changes: [] }; } }
  const pct = (g) => (g.old ? `${g.new < g.old ? "−" : "+"}${Math.abs(Math.round((g.new - g.old) / g.old * 100))}%` : "");
  const when = (t) => (t ? new Date(String(t).replace(" ", "T") + (String(t).includes("Z") || String(t).includes("+") ? "" : "Z")).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "");
  const nDown = d.changes.filter((g) => g.direction === "down" && g.state !== "checking").length;
  const nUp = d.changes.filter((g) => g.direction === "up" && g.state !== "checking").length;
  const needInv = (typeof invAll !== "undefined" ? invAll : []).filter((i) => i.ai_check === "review" || i.problems || i.missing);
  const lineKey = (g) => g.lines.map((x) => x.join(":")).join(",");
  const what = (g) => `${g.product} (${g.store}) ${g.old != null ? money(g.old, "USD") : "—"} → ${money(g.new, "USD")}`;
  const priceRow = (g, i, hist) => {
    const down = g.direction === "down";
    const btns = hist ? `<span class="pc-state">${esc(g.decision)}${g.decided_by ? ` · ${esc(g.decided_by.split("@")[0])}` : ""} ${when(g.decided_at)}</span>`
      : g.state === "checking"
        ? `<span class="pc-state">Checking with supplier${g.check_by ? ` · ${esc(g.check_by.split("@")[0])}` : ""} ${when(g.check_at)}</span>
           <button class="btn btn-sm btn-primary" data-pc="${i}" data-act="use_new">Supplier confirmed: use new price</button>
           <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="keep_old">Supplier corrected it: keep old price</button>
           <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="undo" title="Not checking any more">Undo</button>`
        : down
          ? `<button class="btn btn-sm btn-primary" data-pc="${i}" data-act="use_new">Use new price</button>
             <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="checking">Check with supplier</button>`
          : `<button class="btn btn-sm btn-primary pc-warn" data-pc="${i}" data-act="checking">Check with supplier</button>
             <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="use_new">Use new price</button>`;
    return `<tr class="${g.state === "checking" && !hist ? "pc-checking" : ""}">
      <td class="name">${esc(g.product)}<span class="sub">${esc(g.store)}${g.invoice ? ` · ${esc(g.invoice)}` : ""}${g.invoice_date ? ` · ${niceDate(g.invoice_date)}` : ""}</span></td>
      <td class="pc-num">${g.old !== null && g.old !== undefined ? money(g.old, "USD") : "—"}</td>
      <td class="pc-num"><b>${money(g.new, "USD")}</b> <span class="pc-dir ${down ? "down" : "up"}">${down ? "▼" : "▲"} ${pct(g)}</span>${
        !hist && g.likely === "quantity" ? `<span class="sub est" title="Suppliers usually move a price by up to about ${money(d.gap, "USD")} per item">${money(g.gap_each, "USD")} gap: probably a bundle or 2+ items, open the order first</span>`
        : !hist && g.likely === "price" ? `<span class="sub">Small change: likely a real supplier price change</span>` : ""}</td>
      <td class="pc-orders" title="${esc(g.orders.join(", "))}">${g.orders.length} order${g.orders.length === 1 ? "" : "s"}<span class="sub">${esc(g.orders.slice(0, 3).join(", "))}${g.orders.length > 3 ? "…" : ""}</span></td>
      <td class="pc-btns">${hist ? btns : `<span data-write-only>${btns}</span>`}${noteBox("p" + lineKey(g), g.note, what(g))}</td></tr>`;
  };
  const head = `<div class="panel-head"><h2>Invoices to check</h2>
      <nav class="scm-pages pc-tabs"><button data-pctab="check" class="${pcTab === "check" ? "on" : ""}">Needs checking (${d.changes.length + needInv.length})</button>
        <button data-pctab="history" class="${pcTab === "history" ? "on" : ""}">History</button></nav></div>`;
  if (pcTab === "history") {
    box.innerHTML = head + `<p class="hint pc-tip">Price changes already decided, newest first. Notes stay with them and the AI keeps following them.</p>
      ${h.changes.length ? `<div class="table-wrap"><table class="table pc-table"><thead><tr><th>Product</th><th>Before</th><th>Now</th><th>Orders</th><th>Decided</th></tr></thead>
        <tbody>${h.changes.map((g, i) => priceRow(g, i, true)).join("")}</tbody></table></div>` : '<p class="empty">Nothing decided yet.</p>'}`;
  } else {
    box.innerHTML = head + `
      ${needInv.length ? `<h3 class="pc-sub">Invoices that need a look (${needInv.length})</h3>
        <div class="table-wrap"><table class="table pc-table pc-inv"><thead><tr><th>Invoice</th><th>Store</th><th>Total</th><th>Why</th><th></th></tr></thead><tbody>
        ${needInv.map((i) => `<tr><td class="name">${esc(i.supplier || "?")} #${esc(i.number || "—")}<span class="sub">${i.date ? niceDate(i.date) : ""} · ${esc(i.filename || "")}</span></td>
          <td>${esc(i.store)}</td><td class="pc-num">${money(i.total, "USD")}</td>
          <td>${[i.ai_check === "review" ? '<span class="src bad">AI read it differently</span>' : "", i.problems ? `<span class="src bad">${i.problems} unmatched</span>` : "",
            i.missing ? `<span class="src catalog">${i.missing} missing</span>` : ""].join(" ")}</td>
          <td class="pc-btns"><button class="btn btn-sm btn-primary" data-openinv="${i.id}">Open</button>${noteBox("i" + i.id, i.note, "")}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <h3 class="pc-sub">Price changes to decide (${d.changes.length})
        <span class="pc-bulk" data-write-only>
          ${nDown ? `<button class="btn btn-sm btn-primary" data-bulk="use_new|down">Use new price for all ${nDown} cheaper</button>` : ""}
          ${nUp ? `<button class="btn btn-sm btn-ghost" data-bulk="checking|up">Mark all ${nUp} dearer as checking</button>` : ""}
        </span></h3>
      <p class="hint pc-tip">Cheaper: use the new price. Dearer: check with the supplier first.</p>
      ${d.changes.length ? `<div class="table-wrap"><table class="table pc-table"><thead><tr><th>Product</th><th>Before</th><th>Now</th><th>Orders</th><th></th></tr></thead><tbody>
      ${d.changes.map((g, i) => priceRow(g, i, false)).join("")}</tbody></table></div>` : '<p class="empty">No price changes to decide.</p>'}`;
  }
  for (const b of box.querySelectorAll("[data-pctab]")) b.onclick = () => { pcTab = b.dataset.pctab; loadPriceChanges(); };
  for (const b of box.querySelectorAll("[data-openinv]")) b.onclick = () => showInvoice(b.dataset.openinv);
  for (const b of box.querySelectorAll("[data-note-save]")) b.onclick = async () => {
    const key = b.dataset.noteSave;
    const note = box.querySelector(`[data-note-text="${CSS.escape(key)}"]`).value.trim();
    b.disabled = true;
    try {
      if (key[0] === "i") await api(`/api/invoices/${key.slice(1)}/note`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note }) });
      else await api("/api/invoices/changes/note", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note, what: b.dataset.what, lines: key.slice(1).split(",").map((x) => x.split(":").map(Number)) }) });
      toast(note ? "Note saved: the AI follows it from now on." : "Note removed.");
      if (key[0] === "i") { const inv = invAll.find((x) => String(x.id) === key.slice(1)); if (inv) inv.note = note; }
    } catch (e) { toast(e.message, true); }
    b.disabled = false;
  };
  for (const b of box.querySelectorAll("[data-bulk]")) b.onclick = async () => {
    const [action, direction] = b.dataset.bulk.split("|");
    const n = direction === "down" ? nDown : nUp;
    if (!confirm(action === "use_new" ? `Use the new (lower) price for all ${n} cheaper products? Their orders are re-costed.`
                                      : `Mark all ${n} dearer products as "checking with supplier"?`)) return;
    b.disabled = true; b.textContent = "Working…";
    try {
      const r = await api("/api/invoices/changes", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, direction }) });
      toast(action === "use_new" ? `New prices saved: ${r.lines} lines, ${r.recosted} orders re-costed.` : `${r.lines} lines marked as checking.`);
      productsCache = {};
      loadPriceChanges();
      loadInvoices();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  };
  for (const b of box.querySelectorAll("[data-pc]")) b.onclick = async () => {
    const g = d.changes[+b.dataset.pc];
    b.disabled = true;
    try {
      const r = await api("/api/invoices/changes", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: b.dataset.act, lines: g.lines }) });
      toast({ use_new: `New price saved for ${g.product.slice(0, 40)}. ${r.recosted} orders re-costed.`,
              checking: "Marked: checking with supplier. Other orders keep the old price meanwhile.",
              keep_old: "Kept the old price for these orders. A corrected invoice will replace this one.",
              undo: "Back to undecided." }[b.dataset.act]);
      productsCache = {};
      loadPriceChanges();
      loadInvoices();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  };
}

async function showInvoice(id) {
  let d;
  try { d = await api(`/api/invoices/${id}`); } catch (e) { toast(e.message, true); return; }
  const i = d.invoice;
  const rows = d.lines.map((l) => {
    const [cls, label] = STATUS[l.status] || ["bad", l.status];
    const approve = l.status === "changed" && !l.approved_at
      ? (l.check_state === "kept" ? `<span class="hint">Old price kept (supplier corrected)</span>`
         : `<span class="hint">${l.check_state === "checking" ? "Checking with supplier · " : ""}Decide in “Price changes to decide” above</span>`)
      : (l.approved_at ? `<span class="hint">New price used${l.approved_by ? " · " + esc(l.approved_by.split("@")[0]) : ""}</span>` : "");
    return `<tr><td class="name">${esc(l.order_name || l.order_key)}<span class="sub">${esc(l.title)}</span>
        ${l.product && l.product !== l.title ? `<span class="sub">→ ${esc(l.product)}</span>` : ""}</td>
      <td>${money(l.amount, "USD")}</td>
      <td>${l.previous !== null && l.previous !== undefined ? money(l.previous, "USD") : "—"}</td>
      <td class="inv-status"><span class="src ${cls}">${label}</span>${l.note ? `<span class="sub">${esc(l.note)}</span>` : ""}${approve}</td></tr>`;
  }).join("");
  const missing = i.missing.length ? `<div class="inv-missing"><strong>Fulfilled but not on this invoice (${i.missing.length})</strong>
      ${i.missing.map((m) => `<div>${esc(m.order)} <span class="hint">${esc(m.products.join(" · "))}</span></div>`).join("")}
      <p class="hint">These were marked fulfilled in Shopify within this invoice's order range. Ask the supplier if they'll be on a later invoice.</p></div>` : "";
  let review = "";
  if (i.ai_check === "review") {
    const rv = (() => { try { return JSON.parse(i.review || "{}"); } catch { return {}; } })();
    const m = (v) => (v === null || v === undefined ? "—" : money(v, "USD"));
    review = `<div class="inv-review"><strong>The AI read this invoice differently (${(rv.diffs || []).length})</strong>
      <p class="hint">Until you decide, this invoice's lines aren't used for any cost. Open the original file and check these orders.</p>
      <div class="table-wrap"><table class="table"><thead><tr><th>Order</th><th>First reading</th><th>AI reading</th><th></th></tr></thead><tbody>
      ${(rv.diffs || []).map((x) => `<tr><td class="name">${esc(x.order)}</td><td>${m(x.file)}</td><td>${m(x.ai)}</td><td class="hint">${esc(x.what)}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="inv-review-btns" data-write-only>
        ${i.file_ext ? `<a class="btn btn-sm btn-ghost" href="/api/invoices/${i.id}/file" target="_blank" rel="noopener">Open the original</a>` : ""}
        <button class="btn btn-sm btn-primary" data-review="file">The first reading is right</button>
        <button class="btn btn-sm btn-primary" data-review="ai">Use the AI's reading</button>
        <button class="btn btn-sm btn-ghost" data-review="recheck">Ask the AI again</button></div></div>`;
  } else if (i.ai_check) {
    const say = { agreed: "AI double-check: same orders and amounts as the first reading.",
                  confirmed: i.ai_note || "Checked by a person.", ai_only: i.ai_note || "Read by the AI only.",
                  pending: "The AI is double-checking this invoice…", skipped: `Not double-checked: ${i.ai_note || ""}`,
                  failed: `AI double-check failed: ${i.ai_note || ""}` }[i.ai_check] || "";
    review = say ? `<p class="hint inv-ai">${esc(say)}${["skipped", "failed"].includes(i.ai_check) && i.file_ext
      ? ` <button class="btn btn-sm btn-ghost" data-review="recheck" data-write-only>Double-check now</button>` : ""}</p>` : "";
  }
  $("invDetail").hidden = false;
  $("invDetail").innerHTML = `<div class="panel-head"><h2>${esc(i.supplier || "?")} invoice #${esc(i.number || "—")}
      · ${esc(i.store)} · ${i.date ? niceDate(i.date) : ""}</h2>
      <span><button class="btn btn-sm btn-ghost" id="invClose">Close</button>
      <button class="btn btn-sm btn-danger" id="invDelete" data-write-only>Delete</button></span></div>
    ${review}${missing}
    <div class="inv-note"><label>📝 Note on this invoice <span class="hint">(the AI reads it as a lesson)</span>
      <textarea class="control" rows="2" id="invNote" placeholder="e.g. This store's invoices include shipping in the item price">${esc(i.note || "")}</textarea></label>
      <button class="btn btn-sm" id="invNoteSave">Save note</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>Order / product</th><th>Invoice</th><th>Before</th><th>Check</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  $("invClose").onclick = () => { $("invDetail").hidden = true; };
  $("invNoteSave").onclick = async () => {
    try {
      await api(`/api/invoices/${i.id}/note`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: $("invNote").value.trim() }) });
      toast("Note saved: the AI follows it from now on.");
    } catch (e) { toast(e.message, true); }
  };
  for (const b of $("invDetail").querySelectorAll("[data-review]")) b.onclick = async () => {
    const use = b.dataset.review;
    if (use === "ai" && !confirm("Replace this invoice's lines with the AI's reading? Orders are matched and costed again.")) return;
    b.disabled = true;
    try {
      await api(`/api/invoices/${id}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                 body: JSON.stringify({ use }) });
      toast({ file: "Saved: the first reading is used.", ai: "Saved: the AI's reading is used. Orders re-costed.",
              recheck: "The AI is reading it again; this takes up to a minute." }[use]);
      showInvoice(id);
      loadInvoices();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  };
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

/* ------------------------------------------------ suppliers */

let supData = null;

async function loadSuppliers() {
  let d;
  try { d = await api("/api/suppliers"); } catch (e) { $("supList").innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
  supData = d;
  const usd = (v) => money(v, "USD");
  const day = (iso) => (iso ? niceDate(iso) : "—");
  const names = d.names;
  const pick = (sel) => `<option value="">Choose supplier…</option>${names.map((n) => `<option${n === sel ? " selected" : ""}>${esc(n)}</option>`).join("")}
      <option value="__none">Not a supplier payment</option>`;
  $("supBadge").hidden = !d.to_assign.length;
  $("supBadge").textContent = d.to_assign.length;
  $("supAssign").innerHTML = (d.problems || []).map((p) => `<div class="warn">${esc(p)}</div>`).join("") + (d.to_assign.length ? `
    <section class="panel sup-assign"><div class="panel-head"><h2>Supplier payments to assign <span class="hint">(${d.to_assign.length})</span></h2></div>
      <p class="hint">Payments filed under “Supplier payments” in Expenses since ${day(d.payments_from)}. Say which supplier each went to;
        tick “every payment to this payee” when the payee is always the same supplier.</p>
      <div class="table-wrap"><table class="table"><thead><tr><th>Date</th><th>Paid to</th><th>Amount</th><th>Supplier</th><th></th></tr></thead><tbody>
      ${d.to_assign.map((p, i) => `<tr><td>${day(p.day)}</td><td class="name">${esc(p.name)}${p.detail ? `<span class="sub">${esc(p.detail)}</span>` : ""}</td>
        <td>${money(p.amount, p.currency)}${p.currency !== "USD" ? `<span class="sub">${usd(p.usd)}</span>` : ""}</td>
        <td data-write-only><select class="control" data-pay="${i}">${pick("")}</select>
          <label class="hint sup-all"><input type="checkbox" data-all="${i}"${p.key.startsWith("payee:") ? " checked" : ""}> every payment to this payee</label></td>
        <td data-write-only><button class="btn btn-sm btn-primary" data-save="${i}">Save</button></td></tr>`).join("")}
      </tbody></table></div></section>` : "");
  for (const b of $("supAssign").querySelectorAll("[data-save]")) b.onclick = async () => {
    const i = +b.dataset.save, p = d.to_assign[i];
    const v = $("supAssign").querySelector(`[data-pay="${i}"]`).value;
    if (!v) { toast("Choose the supplier first.", true); return; }
    const all = $("supAssign").querySelector(`[data-all="${i}"]`).checked;
    b.disabled = true;
    try {
      await api("/api/suppliers/payee", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: all ? p.key : "tx:" + p.id, supplier: v === "__none" ? "" : v }) });
      toast("Saved.");
      loadSuppliers();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  };

  $("supList").innerHTML = d.suppliers.map((s, n) => {
    const opening = `<form class="sup-open" data-open="${n}" data-manage-only>
        <label>Opening date <input type="date" class="control" name="date" value="${esc(s.opening_date || "")}" max="${esc(d.today)}" required></label>
        <label>Owed to ${esc(s.supplier)} that morning (USD) <input type="number" step="0.01" class="control" name="amount" value="${s.set ? s.opening : ""}" placeholder="0.00" required></label>
        <label>Note <input class="control" name="note" placeholder="e.g. from their statement of 1 Oct"></label>
        <button class="btn btn-sm btn-primary">Save opening</button>
        <span class="hint">Negative if they held a credit of yours.</span></form>`;
    const invRows = s.invoices.map((i) => `<tr class="clickable" data-inv="${i.id}"><td>${day(i.date)}</td>
        <td class="name">#${esc(i.number || "—")}<span class="sub">${esc(i.store)}</span></td>
        <td>${usd(i.total)}</td><td>${usd(i.expected)}</td>
        <td class="${Math.abs(i.difference) >= 0.01 ? "est" : ""}">${i.difference > 0 ? "+" : ""}${usd(i.difference)}</td>
        <td>${i.matches ? `<span class="src invoice">Matches</span>`
          : `<span class="src bad">${i.issues.length} to check</span><span class="sub">${esc(i.issues.slice(0, 3).join(" · "))}${i.issues.length > 3 ? " …" : ""}</span>`}</td></tr>`).join("");
    const pays = s.payments.map((p) => `<tr><td>${day(p.day)}</td><td class="name">${esc(p.name)}</td>
        <td>${money(p.amount, p.currency)}</td><td>${usd(p.usd)}</td>
        <td data-write-only><button class="btn btn-sm btn-ghost" data-unpay="${esc(p.by_payment ? "tx:" + p.id : p.key)}"
          title="${p.by_payment ? "Assign this payment again" : "Assign payments to this payee again"}">Change</button></td></tr>`).join("");
    const late = s.late_count ? `<details class="sup-late"><summary><span class="src catalog">${s.late_count} orders</span>
        fulfilled over 14 days ago and on no invoice yet · ${usd(s.late_cost)}</summary>
        <div class="table-wrap"><table class="table"><thead><tr><th>Fulfilled</th><th>Order</th><th>Store</th><th>Items</th><th>Expected COG</th></tr></thead><tbody>
        ${s.late.map((o) => `<tr><td>${day(o.day)}</td><td class="name">${esc(o.order)}</td><td>${esc(o.store)}</td><td>${o.items}</td><td>${usd(o.cost)}</td></tr>`).join("")}
        </tbody></table></div><p class="hint">Ask ${esc(s.supplier)} whether these will be on the next invoice.</p></details>` : "";
    return `<section class="panel sup-card">
      <div class="panel-head"><h2>${esc(s.supplier)}</h2>
        <div class="sup-owed"><span class="hint">Owed now</span><b>${s.set ? usd(s.owed) : "—"}</b></div></div>
      ${s.set ? `<div class="sup-eq">
          <div><span class="hint">Opening ${day(s.opening_date)}</span><b>${usd(s.opening)}</b></div><i>+</i>
          <div><span class="hint">Fulfilled since · ${s.orders} orders</span><b>${usd(s.fulfilled)}</b>
            <span class="sub">${usd(s.invoiced)} invoiced · ${usd(s.not_invoiced)} not invoiced yet${s.estimated ? ` · <span class="est">${usd(s.estimated)} estimated</span>` : ""}</span></div><i>−</i>
          <div><span class="hint">Paid since · ${s.payments.length}</span><b>${usd(s.paid)}</b></div><i>=</i>
          <div><span class="hint">Owed now</span><b>${usd(s.owed)}</b></div></div>
          <details class="sup-change" data-manage-only><summary class="hint">Change the opening balance</summary>${opening}</details>`
        : `<div class="warn">Set the opening balance to start this account: the date to count from and what you owed ${esc(s.supplier)} that morning.</div>${opening}`}
      ${late}
      ${s.stores.length ? `<div class="table-wrap"><table class="table sup-stores"><thead><tr><th>Store</th><th>Fulfilled orders</th><th>COG</th><th>Not invoiced yet</th></tr></thead><tbody>
        ${s.stores.map((x) => `<tr><td class="name">${esc(x.store)}</td><td>${x.orders}</td><td>${usd(x.cost)}</td><td>${usd(x.not_invoiced)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${invRows ? `<h3 class="sup-h">Invoices since ${day(s.opening_date)}</h3><div class="table-wrap"><table class="table"><thead><tr><th>Date</th><th>Invoice</th>
        <th>Invoiced</th><th title="EcomVital's own costing of the same orders: single prices, bundles at singles less the usual discount">Expected</th><th>Difference</th><th>Check</th></tr></thead><tbody>${invRows}</tbody></table></div>` : ""}
      ${pays ? `<h3 class="sup-h">Payments since ${day(s.opening_date)}</h3><div class="table-wrap"><table class="table"><thead><tr><th>Date</th><th>Paid to</th><th>Amount</th><th>USD</th><th></th></tr></thead><tbody>${pays}</tbody></table></div>` : ""}
    </section>`;
  }).join("") || `<p class="hint">No supplier orders yet.</p>`;

  for (const f of $("supList").querySelectorAll("[data-open]")) f.onsubmit = async (e) => {
    e.preventDefault();
    const s = d.suppliers[+f.dataset.open], fd = new FormData(f);
    try {
      await api("/api/suppliers/opening", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ supplier: s.supplier, date: fd.get("date"), amount: fd.get("amount"), note: fd.get("note") }) });
      toast(`Opening balance saved for ${s.supplier}.`);
      loadSuppliers();
    } catch (err) { toast(err.message, true); }
  };
  for (const b of $("supList").querySelectorAll("[data-unpay]")) b.onclick = async () => {
    try {
      await api("/api/suppliers/payee", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: b.dataset.unpay, supplier: null }) });
      loadSuppliers();
    } catch (e) { toast(e.message, true); }
  };
  for (const tr of $("supList").querySelectorAll("[data-inv]")) tr.onclick = () => {
    document.querySelector('.cog-tab[data-tab="invoices"]').click();
    showInvoice(tr.dataset.inv);
  };
}

const INV_OK = /\.(pdf|xlsx|csv|txt)$/i;

// Everything inside dropped folders (and their sub-folders), like dragging the whole "INVOICES" folder.
async function filesFromDrop(dt) {
  const out = [];
  const walk = async (entry) => {
    if (!entry) return;
    if (entry.isFile) {
      const f = await new Promise((res) => entry.file(res, () => res(null)));
      if (f) out.push(f);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((res) => reader.readEntries(res, () => res([])));
        if (!batch.length) break;
        for (const e of batch) await walk(e);
      }
    }
  };
  const entries = [...(dt.items || [])].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null));
  if (entries.some(Boolean)) { for (const e of entries) await walk(e); }
  else out.push(...dt.files);
  return out;
}

async function uploadInvoices(files) {
  files = files.filter((f) => INV_OK.test(f.name) && !f.name.startsWith("."));
  if (!files.length) { $("invResult").innerHTML = `<div class="warn">No PDF, Excel or CSV invoices in what you dropped.</div>`; return; }
  const one = files.length === 1;
  const done = { ok: 0, dup: 0, bad: [], lines: 0, total: 0, changed: 0, newp: 0, problems: 0 };
  let last = null;
  for (const [i, f] of files.entries()) {
    $("invResult").innerHTML = `<div class="hint">${one ? "" : `${i + 1} of ${files.length} · `}Reading ${esc(f.name)}…</div>`;
    try {
      const r = await api(`/api/invoices?filename=${encodeURIComponent(f.name)}`,
                          { method: "POST", body: await f.arrayBuffer() });
      if (r.attached) { done.attached = (done.attached || 0) + 1; if (one) $("invResult").innerHTML = `<div class="notice ok">${esc(f.name)}: this invoice was already uploaded; its file is now kept in the archive.</div>`; continue; }
      const c = r.counts;
      done.ok++; done.lines += r.lines; done.total += r.total;
      done.changed += c.changed || 0; done.newp += c.new || 0; done.problems += (c.unmatched || 0) + (c.duplicate || 0);
      last = r;
      if (one) {
        const off = Math.abs(r.sum - r.total) >= 0.01
          ? ` <span class="est">Lines add up to ${money(r.sum, "USD")}, not the invoice total ${money(r.total, "USD")}.</span>` : "";
        $("invResult").innerHTML = `<div class="notice ok">${esc(r.supplier)} #${esc(r.number)}: ${r.lines} lines,
          ${money(r.total, "USD")}. ${c.ok || 0} match · ${c.new || 0} first time · ${c.changed || 0} price changes ·
          ${(c.unmatched || 0) + (c.duplicate || 0)} problems · ${r.missing} fulfilled orders missing.${off}</div>`;
      }
    } catch (e) {
      if (/already uploaded/i.test(e.message)) done.dup++;
      else done.bad.push(`${f.name}: ${e.message}`);
      if (one) $("invResult").innerHTML = `<div class="warn">${esc(f.name)}: ${esc(e.message)}</div>`;
    }
  }
  await loadInvoices();
  if (one) { if (last) showInvoice(last.id); return; }
  $("invResult").innerHTML = `<div class="notice ${done.bad.length ? "" : "ok"}"><b>${done.ok} invoices added</b>
      (${done.lines} lines, ${money(done.total, "USD")})${done.attached ? ` · ${done.attached} earlier invoices now have their file kept` : ""}${done.dup ? ` · ${done.dup} were already uploaded` : ""}.
      ${done.changed} price changes to check · ${done.newp} first-time products · ${done.problems} lines need a look.
      Order costs are updated from these invoices now. The AI is double-checking each file in the background.</div>
    ${done.bad.length ? `<div class="warn">Couldn't read ${done.bad.length}:<br>${done.bad.map(esc).join("<br>")}</div>` : ""}`;
}

$("invFile").onchange = (e) => { uploadInvoices([...e.target.files]); e.target.value = ""; };
if ($("invFolder")) $("invFolder").onchange = (e) => { uploadInvoices([...e.target.files]); e.target.value = ""; };
const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", async (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  uploadInvoices(await filesFromDrop(e.dataTransfer));
});

/* ------------------------------------------------ wiring */

for (const b of document.querySelectorAll(".cog-tab")) {
  b.onclick = () => {
    for (const x of document.querySelectorAll(".cog-tab")) x.classList.toggle("on", x === b);
    PDUrl.set({ tab: b.dataset.tab === "check" ? null : b.dataset.tab });
    $("checkView").hidden = b.dataset.tab !== "check";
    $("productsView").hidden = b.dataset.tab !== "products";
    $("invoicesView").hidden = b.dataset.tab !== "invoices";
    $("suppliersView").hidden = b.dataset.tab !== "suppliers";
    if (b.dataset.tab === "suppliers") loadSuppliers();
    if (b.dataset.tab === "products") loadProducts();
    if (b.dataset.tab === "invoices") { loadInvoices(); if (window.cdLoadDays) window.cdLoadDays(); }
  };
}
$("range").onchange = () => {
  if ($("range").value === "custom") { $("dates").hidden = false; return; }
  loadDay();
};
$("from").onchange = $("to").onchange = () => { if ($("range").value === "custom") loadDay(); };
$("storeSelect").onchange = loadDay;
$("productStore").onchange = loadProducts;
$("pdkRange").onchange = () => { PDUrl.set({ prange: $("pdkRange").value === "all" ? null : $("pdkRange").value }); loadPdk(); };
$("pdkFrom").onchange = $("pdkTo").onchange = () => loadPdk();
$("search").oninput = () => { PDUrl.set({ q: $("search").value.trim() }); drawPdk(); if ($("productStore").value !== "all") loadProducts(); };
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
    $("productStore").innerHTML = `<option value="all">All stores</option>` + opts;
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
  if (has($("pdkRange"), PDUrl.get("prange")) && PDUrl.get("prange") !== "custom") $("pdkRange").value = PDUrl.get("prange");
  if (PDUrl.get("q")) $("search").value = PDUrl.get("q");
  const tab = document.querySelector(`.cog-tab[data-tab="${PDUrl.get("tab")}"]`);
  loadDay();
  loadInvoices();
  if (tab) tab.click();
})();

window.addEventListener("pd-changed", () => { if (typeof loadPriceChanges === "function") { productsCache = {}; loadPriceChanges(); loadInvoices(); } });
