/* ProfitDesk Reports desk: make and download CSV reports (built by the server). */

const $ = (id) => document.getElementById(id);
const HELP = {
  profit_daily: "One row per day: sales, orders, ad spend (Google / Meta), ROAS, fees, COG, net profit, margin, reserve held and net available, with a total row.",
  profit_stores: "One row per store for the period, with a total row. Pick All stores or a group.",
  cog_orders: "Every order line with its COG (USD), where the cost came from (invoice, history or estimate) and the supplier.",
  invoices: "Every uploaded supplier invoice line in the period: order, product, amount, previous price and its check.",
  cash_statement: "The Cash flow statement for the period: opening, money in and out, fees, moves between accounts, closing.",
  tickets: "Support tickets created in the period: store, type, SCM / CS, summary, orders, status and who closed them.",
  cash_snapshots: "One row per day: the Cash flow cards as they stood at 23:59 Hong Kong time (available now, held, receivable, available + receivable and its change from the day before, ads payable, after ad bills, available by 7 / 14 / 30 / 60 / 90 days).",
  ad_bills: "Ad spend owed to Meta and Google right now, per ad account (the dates are ignored).",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/reports"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3000);
}
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

async function load() {
  const d = await api("/api/reports");
  if (!$("type").options.length) {
    $("type").innerHTML = d.types.map((t) => `<option value="${t.type}">${esc(t.title)}</option>`).join("");
    showHelp();
  }
  $("count").textContent = `${d.reports.length} reports`;
  $("list").innerHTML = d.reports.length ? `<thead><tr><th>Report</th><th>Rows</th><th>Made</th><th></th></tr></thead><tbody>${
    d.reports.map((r) => `<tr><td class="name">${esc(r.title)}<span class="sub">${esc(r.created_by || "")}</span></td>
      <td>${r.rows}</td><td>${new Date(r.created_at.replace(" ", "T") + "Z").toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</td>
      <td class="rep-actions"><a class="btn btn-sm btn-primary" href="/api/reports/${r.id}/download">Download</a>
        <button class="btn btn-sm btn-ghost" data-del="${r.id}">Delete</button></td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">No reports yet. Create one above, or ask the chat ("make me a profit report for last week").</td></tr></tbody>`;
  for (const b of $("list").querySelectorAll("[data-del]")) b.onclick = async () => {
    await api(`/api/reports/${b.dataset.del}`, { method: "DELETE" });
    load();
  };
}

function money(v, cur) {
  if (v === null || v === undefined) return "–";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(v);
  } catch { return `${cur} ${Math.round(v).toLocaleString()}`; }
}

async function loadSnapshots() {
  let d;
  try { d = await api("/api/cash/snapshots"); } catch { return; }      // no Cash desk: stay hidden
  $("snapPanel").hidden = false;
  const rows = d.snapshots.slice(0, 14);
  const h7 = (x) => (x.horizons || []).find((h) => h.key === "d7");
  $("snaps").innerHTML = rows.length ? `<thead><tr><th>Day</th><th>Available now</th><th>Receivable</th>
      <th>Available + receivable</th><th>vs day before</th><th>Ads payable</th><th>After ad bills</th>
      <th>Available in 7 days</th></tr></thead><tbody>${rows.map((x) => {
        const c = x.currency;
        const ch = x.change === null || x.change === undefined ? "–"
          : `<span style="color:var(${x.change >= 0 ? "--up" : "--down"})">${x.change >= 0 ? "+" : "−"}${money(Math.abs(x.change), c)}</span>`;
        return `<tr title="${esc((x.problems || []).join("; "))}"><td class="name">${esc(new Date(x.day + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }))}</td>
          <td>${money(x.available, c)}</td><td>${money(x.receivable, c)}</td><td><strong>${money(x.position, c)}</strong></td>
          <td>${ch}</td><td>${money(x.ads_payable, c)}</td><td>${money(x.after_ads, c)}</td><td>${money(h7(x)?.available_by, c)}</td></tr>`;
      }).join("")}</tbody>`
    : `<tbody><tr><td class="hint">The first one is saved tonight at 23:59 Hong Kong time. For more days, create the "Cash at end of day" report.</td></tr></tbody>`;
}

function showHelp() {
  const t = $("type").value;
  $("help").textContent = HELP[t] || "";
  $("scopeBox").hidden = !["profit_daily", "profit_stores", "cog_orders"].includes(t);
}

$("type").onchange = () => { showHelp(); PDUrl.set({ type: $("type").value }); };
for (const id of ["from", "to", "scope"]) $(id).addEventListener("change", () => PDUrl.set({ [id]: $(id).value }));
$("make").onclick = async () => {
  const b = $("make");
  b.disabled = true;
  b.textContent = "Preparing…";
  try {
    const r = await api("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: $("type").value, start: $("from").value, end: $("to").value,
                             scope: $("scope").value, currency: $("currency").value }) });
    toast(`Ready: ${r.rows} rows.`);
    await load();
    location.href = r.download;
  } catch (e) { toast(e.message, true); }
  finally { b.disabled = false; b.textContent = "Create report"; }
};

(async function boot() {
  const today = new Date();
  const week = new Date(today); week.setDate(today.getDate() - 6);
  $("from").value = iso(week);
  $("to").value = iso(today);
  try {
    const setup = await api("/api/setup");
    $("currency").innerHTML = setup.currency_options.map((c) =>
      `<option${c === setup.display_currency ? " selected" : ""}>${esc(c)}</option>`).join("");
    $("scope").innerHTML = `<option value="all">All stores</option>` +
      (setup.groups || []).map((g) => `<option value="g${g.id}">${esc(g.name)} (group)</option>`).join("") +
      setup.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  } catch (e) { toast(e.message, true); }
  // Back to the report you were setting up before a refresh
  const has = (sel, v) => v && [...sel.options].some((o) => o.value === v);
  if (has($("type"), PDUrl.get("type"))) { $("type").value = PDUrl.get("type"); showHelp(); }
  if (has($("scope"), PDUrl.get("scope"))) $("scope").value = PDUrl.get("scope");
  if (PDUrl.get("from")) $("from").value = PDUrl.get("from");
  if (PDUrl.get("to")) $("to").value = PDUrl.get("to");
  load().catch((e) => toast(e.message, true));
  loadSnapshots();
})();
