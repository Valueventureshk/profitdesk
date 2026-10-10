/* ProfitDesk Expenses desk: actual payments out of Airwallex and PayPal, by
   category. Every figure comes from the server (expenses.py). */

const $ = (id) => document.getElementById(id);
let data = null;
let period = "month";
let openCat = null;
const LOGO = {
  google: `<svg class="logo" viewBox="0 0 48 48" aria-label="Google"><path fill="#EA4335" d="M24 9.5c3.5 0 6.7 1.2 9.2 3.6l6.9-6.9C35.9 2.4 30.5 0 24 0 14.6 0 6.5 5.4 2.6 13.2l8 6.2C12.4 13.7 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M47 24.5c0-1.6-.2-3.1-.4-4.5H24v9h12.9c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 7.2-10.4 7.2-17.7z"/><path fill="#FBBC05" d="M10.5 28.6c-.5-1.5-.8-3-.8-4.6s.3-3.1.8-4.6l-8-6.2C.9 16.5 0 20.1 0 24s.9 7.5 2.6 10.8z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.7-6c-2.2 1.5-4.9 2.3-8.2 2.3-6.3 0-11.6-4.2-13.5-9.9l-8 6.2C6.5 42.6 14.6 48 24 48z"/></svg>`,
  meta: `<svg class="logo" viewBox="0 0 24 24" aria-label="Meta"><path d="M3 14.6c0-4 2-7.1 4.4-7.1 3.6 0 5.6 9 9.2 9 2.4 0 4.4-1.9 4.4-4.4s-1.9-4.6-4.3-4.6c-3.6 0-5.6 9-9.3 9C5 16.5 3 15.6 3 14.6z" fill="none" stroke="#0866FF" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, cur = data?.currency) {
  if (v === null || v === undefined) return "—";
  const opts = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  if (cur) { opts.style = "currency"; opts.currency = cur; }
  try { return new Intl.NumberFormat(undefined, opts).format(v); }
  catch { return v.toFixed(2); }
}
function niceDate(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined,
    { weekday: "short", day: "numeric", month: "short" });
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/expenses"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || body.detail || `Request failed (${r.status})`);
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

function options(selected) {
  return `<option value="">Pick a category…</option>` + data.category_list.map((c) =>
    `<option value="${c.key}"${c.key === selected ? " selected" : ""}>${esc(c.name)}</option>`).join("");
}

async function load(fresh = false) {
  const q = new URLSearchParams({ period, currency: $("currencySelect").value || "" });
  if (period === "custom") { q.set("start", $("from").value); q.set("end", $("to").value); }
  if (fresh) q.set("_", Date.now());
  $("refresh").disabled = true;
  try {
    data = await api(`/api/expenses?${q}`);
    render();
    if (data.suggesting) setTimeout(() => load(), 8000);   // AI suggestions arrive in a few seconds
  } catch (e) {
    toast(e.message, true);
    $("cards").innerHTML = `<div class="card"><div class="card-label">Couldn't load</div><div class="card-note">${esc(e.message)}</div></div>`;
  } finally { $("refresh").disabled = false; }
}

function render() {
  const range = data.start === data.end ? niceDate(data.start) : `${niceDate(data.start)} – ${niceDate(data.end)}`;
  $("sub").textContent = `${range}${data.live ? " · today keeps changing until midnight (Hong Kong time)" : ""} · actual payments, nothing estimated`;
  $("problems").innerHTML = (data.problems || []).map((p) => `<div class="warning">${esc(p)}</div>`).join("");
  renderSort();
  renderCards();
  renderDetail();
  renderDays();
}

function renderSort() {
  const list = data.to_sort || [];
  $("sortPanel").hidden = !list.length;
  if (!list.length) return;
  $("sortNote").textContent = `${list.length} payee${list.length === 1 ? "" : "s"} · ${money(data.unsorted)}`;
  $("sortList").innerHTML = list.map((s, i) => {
    const sug = s.suggestion;
    const ex = s.examples.map((e) => `${niceDate(e.day)} ${money(e.amount)}${e.original ? ` (${esc(e.original)})` : ""}${e.detail ? ` · ${esc(e.detail)}` : ""}`).join("<br>");
    return `<div class="exp-sort-row">
      <div class="exp-sort-what"><strong>${esc(s.name)}</strong>
        <span class="sub">${s.count} payment${s.count === 1 ? "" : "s"} via ${esc(s.sources)}</span>
        <span class="exp-ex">${ex}</span></div>
      <div class="exp-sort-amt">${money(s.total)}</div>
      <div class="exp-sort-pick" data-write-only>
        <select class="control" data-i="${i}">${options(sug?.category)}</select>
        ${sug ? `<span class="exp-ai" title="${esc(sug.reason)}">✦ AI suggests: ${esc(sug.reason)}</span>` : ""}
        <button class="btn btn-sm btn-primary" data-save="${i}">Save</button>
      </div></div>`;
  }).join("");
  for (const b of $("sortList").querySelectorAll("[data-save]")) b.onclick = async () => {
    const s = list[+b.dataset.save];
    const cat = $("sortList").querySelector(`select[data-i="${b.dataset.save}"]`).value;
    if (!cat) { toast("Pick a category first.", true); return; }
    b.disabled = true;
    try {
      await api("/api/expenses/sort", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: s.key, category: cat, name: s.name }) });
      toast(`${s.name} saved.`);
      load();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  };
}

function renderCards() {
  const shown = data.categories.filter((c) => c.key !== "ignore" && (c.count || ["supplier", "meta", "google", "fees"].includes(c.key)));
  const total = `<div class="card feature exp-total">
      <div class="card-label">Total outgoings</div>
      <div class="card-value">${money(data.total)}</div>
      <div class="card-note">${data.unsorted ? `incl. ${money(data.unsorted)} still to sort` : "everything sorted"}</div>
    </div>`;
  $("cards").innerHTML = total + shown.map((c) => `<div class="card exp-card${openCat === c.key ? " on" : ""}" data-cat="${c.key}" role="button" tabindex="0">
      <div class="card-label">${LOGO[c.key] || ""}${esc(c.name)}</div>
      <div class="card-value">${money(c.total)}</div>
      <div class="card-note">${c.count} payment${c.count === 1 ? "" : "s"}${c.lines.length ? ` · ${esc(c.lines[0].name)}${c.lines.length > 1 ? ` +${c.lines.length - 1}` : ""}` : ""}</div>
    </div>`).join("");
  for (const el of $("cards").querySelectorAll("[data-cat]")) {
    const open = () => { openCat = openCat === el.dataset.cat ? null : el.dataset.cat; renderCards(); renderDetail(); };
    el.onclick = open;
    el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") open(); };
  }
}

function renderDetail() {
  const c = openCat && data.categories.find((x) => x.key === openCat);
  $("detailPanel").hidden = !c;
  if (!c) return;
  $("detailTitle").textContent = `${c.name} · ${money(c.total)}`;
  const movable = (l) => !l.key.startsWith("fees:") && !["refunds", "chargebacks"].includes(l.key);
  $("detail").innerHTML = c.lines.length ? `<thead><tr><th>Paid to</th><th>Payments</th><th>Amount</th><th data-write-only>Category</th></tr></thead><tbody>${
    c.lines.map((l, i) => `<tr><td class="name">${esc(l.name)}</td><td>${l.count}</td><td>${money(l.total)}</td>
      <td data-write-only>${movable(l) ? `<select class="control exp-move" data-l="${i}">${options(c.key)}</select>` : ""}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">Nothing in this period.</td></tr></tbody>`;
  for (const sel of $("detail").querySelectorAll("[data-l]")) sel.onchange = async () => {
    const l = c.lines[+sel.dataset.l];
    try {
      await api("/api/expenses/sort", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: l.key, category: sel.value, name: l.name }) });
      toast(`${l.name} moved.`);
      load();
    } catch (e) { toast(e.message, true); }
  };
}

function renderDays() {
  const days = data.days.slice().reverse();
  $("daysPanel").hidden = !days.length;
  if (!days.length) return;
  const cols = data.categories.filter((c) => c.key !== "ignore" && c.count);
  if (data.unsorted) cols.push({ key: "unsorted", name: "To sort" });
  $("daysNote").textContent = data.live ? "Today keeps changing until midnight" : "";
  $("days").innerHTML = `<thead><tr><th>Day</th><th>Total</th>${cols.map((c) => `<th>${esc(c.name)}</th>`).join("")}</tr></thead><tbody>${
    days.map((d) => `<tr><td class="name">${niceDate(d.day)}${d.day === data.today ? " <span class=\"sub\">so far</span>" : ""}</td>
      <td><strong>${money(d.total)}</strong></td>${cols.map((c) => `<td>${d.by[c.key] ? money(d.by[c.key]) : "–"}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

for (const b of $("periods").querySelectorAll("[data-p]")) b.onclick = () => {
  for (const x of $("periods").querySelectorAll("[data-p]")) x.classList.toggle("on", x === b);
  period = b.dataset.p;
  $("customBox").hidden = period !== "custom";
  if (period !== "custom") load();
};
$("go").onclick = () => load();
$("refresh").onclick = () => load(true);
$("detailClose").onclick = () => { openCat = null; renderCards(); renderDetail(); };
$("currencySelect").onchange = () => load();

(async function boot() {
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const t = new Date();
  $("to").value = iso(t);
  $("from").value = iso(new Date(t.getFullYear(), t.getMonth(), 1));
  try {
    const setup = await api("/api/setup");
    $("currencySelect").innerHTML = setup.currency_options.map((c) =>
      `<option${c === setup.display_currency ? " selected" : ""}>${esc(c)}</option>`).join("");
  } catch { /* falls back to the server's default */ }
  load();
})();
