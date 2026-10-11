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
        <td class="${o.estimated ? "est" : ""}">${money(o.cog, cur)}${o.bundle ? `<span class="sub bundle-off"
          title="${o.bundle.estimated ? "Estimated: single prices less this store's usual discount for this many items" : "From the supplier's invoice"}">Singles ${money(o.bundle.singles, cur)} ·
          ${o.bundle.discount >= -0.005 ? `bundle discount ${money(o.bundle.discount, cur)}` : `<span class="est">bundle ${money(-o.bundle.discount, cur)} dearer</span>`}${o.bundle.estimated ? " (est.)" : ""}</span>` : ""}</td>
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
  loadPriceChanges();
  let d;
  try { d = await api("/api/invoices"); } catch (e) { toast(e.message, true); return; }
  $("invBadge").hidden = !d.to_check;
  $("invBadge").textContent = d.to_check;
  invAll = d.invoices;
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

// Price changes across all invoices, one row per store + product + new price
async function loadPriceChanges() {
  let d;
  try { d = await api("/api/invoices/changes"); } catch { return; }
  const box = $("priceChanges");
  box.hidden = !d.changes.length;
  if (!d.changes.length) return;
  const pct = (g) => (g.old ? `${g.new < g.old ? "−" : "+"}${Math.abs(Math.round((g.new - g.old) / g.old * 100))}%` : "");
  const when = (t) => (t ? new Date(t.replace(" ", "T") + "Z").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "");
  const nDown = d.changes.filter((g) => g.direction === "down" && g.state !== "checking").length;
  const nUp = d.changes.filter((g) => g.direction === "up" && g.state !== "checking").length;
  box.innerHTML = `<div class="panel-head"><h2>Price changes to decide <span class="hint">(${d.changes.length})</span></h2>
      <span class="pc-bulk" data-write-only>
        ${nDown ? `<button class="btn btn-sm btn-primary" data-bulk="use_new|down">Use new price for all ${nDown} cheaper</button>` : ""}
        ${nUp ? `<button class="btn btn-sm btn-ghost" data-bulk="checking|up">Mark all ${nUp} dearer as checking</button>` : ""}
      </span></div>
    <p class="hint pc-tip">Cheaper: use the new price. Dearer: check with the supplier first.</p>
    <div class="table-wrap"><table class="table pc-table"><thead><tr><th>Product</th><th>Before</th><th>Now</th><th>Orders</th><th></th></tr></thead><tbody>
    ${d.changes.map((g, i) => {
      const down = g.direction === "down";
      const btns = g.state === "checking"
        ? `<span class="pc-state">Checking with supplier${g.check_by ? ` · ${esc(g.check_by.split("@")[0])}` : ""} ${when(g.check_at)}</span>
           <button class="btn btn-sm btn-primary" data-pc="${i}" data-act="use_new">Supplier confirmed: use new price</button>
           <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="keep_old">Supplier corrected it: keep old price</button>
           <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="undo" title="Not checking any more">Undo</button>`
        : down
          ? `<button class="btn btn-sm btn-primary" data-pc="${i}" data-act="use_new">Use new price</button>
             <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="checking">Check with supplier</button>`
          : `<button class="btn btn-sm btn-primary pc-warn" data-pc="${i}" data-act="checking">Check with supplier</button>
             <button class="btn btn-sm btn-ghost" data-pc="${i}" data-act="use_new">Use new price</button>`;
      return `<tr class="${g.state === "checking" ? "pc-checking" : ""}">
        <td class="name">${esc(g.product)}<span class="sub">${esc(g.store)}</span></td>
        <td>${g.old !== null && g.old !== undefined ? money(g.old, "USD") : "—"}</td>
        <td><b>${money(g.new, "USD")}</b> <span class="pc-dir ${down ? "down" : "up"}">${down ? "▼" : "▲"} ${pct(g)}</span></td>
        <td class="hint" title="${esc(g.orders.join(", "))}">${g.orders.length} · ${esc(g.orders.slice(0, 3).join(", "))}${g.orders.length > 3 ? "…" : ""}</td>
        <td class="pc-btns" data-write-only>${btns}</td></tr>`;
    }).join("")}</tbody></table></div>`;
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
    <div class="table-wrap"><table class="table"><thead><tr><th>Order / product</th><th>Invoice</th><th>Before</th><th>Check</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  $("invClose").onclick = () => { $("invDetail").hidden = true; };
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

window.addEventListener("pd-changed", () => { if (typeof loadPriceChanges === "function") { productsCache = {}; loadPriceChanges(); loadInvoices(); } });
