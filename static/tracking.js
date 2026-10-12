/* ProfitDesk Apps → Tracking: the wetracked.io-style report (adds to cart, checkouts, purchases per
   campaign / ad set / ad) and each store's pixel setup. Figures come from the server (tracking.py). */

const $ = (id) => document.getElementById(id);
let stores = [], data = null, level = PDUrl.get("level") || "campaign", page = 0, sort = { k: "purchase_value", dir: -1 };
const FIELD = { campaign: "a_campaign", adset: "a_term", ad: "a_content" };
const KEY = { utm_id: "a_id", utm_source: "a_source", utm_medium: "a_medium", utm_campaign: "a_campaign", utm_content: "a_content", utm_term: "a_term" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v) {
  if (!v) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: data?.currency || "USD" }).format(v); }
  catch { return Number(v).toFixed(2); }
}
const count = (v) => (v ? Number(v).toLocaleString() : "—");
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/tracking"; throw new Error("Log in again."); }
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

// the store's own today (its clock), so "Today" matches Shopify
function storeToday() {
  const st = stores.find((s) => String(s.id) === $("store").value);
  try { return new Intl.DateTimeFormat("en-CA", { timeZone: st?.timezone || undefined }).format(new Date()); }
  catch { return new Date().toISOString().slice(0, 10); }
}
function range() {
  const t = new Date(storeToday() + "T12:00:00"), iso = (d) => d.toISOString().slice(0, 10);
  const back = (n) => { const d = new Date(t); d.setDate(d.getDate() - n); return iso(d); };
  const r = $("range").value;
  if (r === "today") return [iso(t), iso(t)];
  if (r === "yesterday") return [back(1), back(1)];
  if (r === "custom") return [$("from").value || iso(t), $("to").value || iso(t)];
  return [back(Number(r) - 1), iso(t)];
}

async function boot() {
  try { stores = (await api("/api/tracking/stores")).stores; } catch (e) { toast(e.message, true); return; }
  $("store").innerHTML = stores.map((s) => `<option value="${s.id}">${esc(s.domain.replace(/\.myshopify\.com$/, ""))} · ${esc(s.name)}${s.installed ? "" : " (pixel off)"}</option>`).join("");
  const want = PDUrl.get("store") || (stores.find((s) => s.installed) || stores[0] || {}).id;
  if (want) $("store").value = String(want);
  if (PDUrl.get("range")) $("range").value = PDUrl.get("range");
  if (PDUrl.get("key")) $("key").value = PDUrl.get("key");
  for (const b of document.querySelectorAll("#level [data-l]")) b.classList.toggle("on", b.dataset.l === level);
  try { if (localStorage.getItem("pdTrkNote") === "off") $("note").hidden = true; } catch { /* private */ }
  load();
}

async function load() {
  const [start, end] = range();
  $("dates").hidden = $("range").value !== "custom";
  const field = $("key").value ? KEY[$("key").value] : FIELD[level];
  PDUrl.set({ store: $("store").value, range: $("range").value === "today" ? null : $("range").value,
              level: level === "campaign" ? null : level, key: $("key").value || null });
  $("reload").disabled = true;
  try { data = await api(`/api/tracking/report?store=${$("store").value}&start=${start}&end=${end}&field=${field}`); }
  catch (e) { toast(e.message, true); $("reload").disabled = false; return; }
  $("reload").disabled = false;
  $("cur").textContent = data.currency;
  const h = data.health;
  $("health").innerHTML = h.last ? `<span class="trk-dot ${h.hour ? "on" : ""}"></span>${h.hour ? `${h.hour} events in the last hour` : "No events in the last hour"}`
    : `<span class="trk-dot"></span>No events from this store yet`;
  page = 0;
  draw();
}

function draw() {
  const lvl = { campaign: "campaigns", adset: "ad sets", ad: "ads" }[level];
  $("q").placeholder = `Search ${lvl}..`;
  const q = $("q").value.trim().toLowerCase();
  const rows = data.rows.filter((r) => !q || r.name.toLowerCase().includes(q))
    .sort((a, b) => ((a[sort.k] || 0) - (b[sort.k] || 0)) * sort.dir || a.name.localeCompare(b.name));
  const per = Number($("rows").value), pages = Math.max(1, Math.ceil(rows.length / per));
  page = Math.min(page, pages - 1);
  const shown = rows.slice(page * per, page * per + per);
  const cols = [["atc", "Total adds to cart"], ["atc_value", "Total adds to cart conversion value"], ["ic", "Total checkouts initiated"],
                ["ic_value", "Total checkouts initiated conversion value"], ["purchase", "Total purchases"], ["purchase_value", "Total purchases conversion value"]];
  const head = (k, label) => `<th class="trk-sort${sort.k === k ? " on" : ""}" data-k="${k}">${label}<span>${sort.k === k ? (sort.dir < 0 ? "↓" : "↑") : "↕"}</span></th>`;
  const name = { campaign: "Campaign", adset: "Adset", ad: "Ad" }[level];
  const t = data.total;
  $("table").innerHTML = `<thead><tr><th class="trk-first">${name}</th>${cols.map(([k, l]) => head(k, l)).join("")}</tr></thead>
    <tbody>${shown.map((r) => `<tr class="trk-row" data-name="${esc(r.name)}"><th class="trk-first" title="${esc(r.name)} · click to see each event">${esc(r.name)}</th>${cols.map(([k]) =>
      `<td>${k.endsWith("_value") ? money(r[k]) : count(r[k])}</td>`).join("")}</tr>`).join("")
      || `<tr><td class="trk-empty" colspan="7">${data.health.last ? `No ${lvl} with events in this period.` : "No events from this store yet. Switch the pixel on in Setup."}</td></tr>`}</tbody>
    <tfoot><tr><th class="trk-first">Results from ${rows.length} ${lvl}</th>${cols.map(([k]) => `<td>${k.endsWith("_value") ? money(t[k]) : count(t[k])}</td>`).join("")}</tr></tfoot>`;
  $("pager").innerHTML = `${pages > 1 ? `<button class="btn btn-sm btn-ghost" id="prev"${page ? "" : " disabled"}>‹</button>` : ""}
    Showing <b>${rows.length ? page * per + 1 : 0}-${Math.min(rows.length, page * per + per)}</b> of ${rows.length}
    ${pages > 1 ? `<button class="btn btn-sm btn-ghost" id="next"${page < pages - 1 ? "" : " disabled"}>›</button>` : ""}`;
  if ($("prev")) $("prev").onclick = () => { page--; draw(); };
  if ($("next")) $("next").onclick = () => { page++; draw(); };
  for (const tr of $("table").querySelectorAll(".trk-row")) tr.onclick = () => openEvents(tr.dataset.name);
  for (const th of $("table").querySelectorAll(".trk-sort")) th.onclick = () => {
    sort = sort.k === th.dataset.k ? { k: sort.k, dir: -sort.dir } : { k: th.dataset.k, dir: -1 };
    draw();
  };
}

function when(iso) {
  const st = stores.find((s) => String(s.id) === $("store").value);
  try { return new Date(iso).toLocaleString(undefined, { timeZone: st?.timezone || undefined, day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }); }
  catch { return iso; }
}
const KIND = { "Add to cart": "catalog", "Checkout started": "changed", "Purchase": "invoice" };

async function openEvents(name) {
  const [start, end] = range();
  const field = $("key").value ? KEY[$("key").value] : FIELD[level];
  $("evModal").hidden = false;
  $("evTitle").textContent = name;
  $("evSub").textContent = "Loading…";
  $("evTable").innerHTML = "";
  let d;
  try { d = await api(`/api/tracking/events?store=${$("store").value}&start=${start}&end=${end}&field=${field}&value=${encodeURIComponent(name)}`); }
  catch (e) { $("evSub").textContent = e.message; return; }
  const n = (k) => d.events.filter((e) => e.label === k).length;
  $("evSub").textContent = `${n("Add to cart")} adds to cart · ${n("Checkout started")} checkouts · ${n("Purchase")} purchases. Each one is credited to the ad click shown (last click within 7 days).`;
  $("evTable").innerHTML = `<thead><tr><th>When</th><th>Event</th><th>Value</th><th>Campaign</th><th>Ad set</th><th>Ad</th><th>Click</th><th>Page</th><th></th></tr></thead>
    <tbody>${d.events.map((e) => `<tr><td>${esc(when(e.at))}</td><td><span class="src ${KIND[e.label] || "new"}">${esc(e.label)}</span>${e.order_id ? `<span class="sub">order ${esc(e.order_id)}</span>` : ""}</td>
      <td>${money(e.value)}</td><td title="${esc(e.a_campaign || "")}">${esc(e.a_campaign || "—")}</td><td title="${esc(e.a_term || "")}">${esc(e.a_term || "—")}</td>
      <td title="${esc(e.a_content || "")}">${esc(e.a_content || "—")}</td><td>${esc({ fbclid: "Meta", gclid: "Google", ttclid: "TikTok", msclkid: "Bing" }[e.a_click] || e.a_source || "—")}</td>
      <td class="trk-path" title="${esc(e.path)}">${esc(e.path)}</td><td><button class="btn btn-sm btn-ghost" data-cid="${esc(e.client_id)}">Journey</button></td></tr>`).join("")
      || `<tr><td class="trk-empty" colspan="9">No single events in this period.</td></tr>`}</tbody>`;
  for (const b of $("evTable").querySelectorAll("[data-cid]")) b.onclick = () => openJourney(b.dataset.cid, name);
}

async function openJourney(cid, back) {
  $("evSub").textContent = "Loading this shopper's visits…";
  let d;
  try { d = await api(`/api/tracking/journey?store=${$("store").value}&client=${encodeURIComponent(cid)}`); }
  catch (e) { $("evSub").textContent = e.message; return; }
  $("evTitle").textContent = "One shopper's journey";
  $("evSub").innerHTML = `${d.events.length} events, oldest first. <a href="#" id="evBack">← Back to ${esc(back)}</a>`;
  $("evTable").innerHTML = `<thead><tr><th>When</th><th>Event</th><th>Value</th><th>Credited to</th><th>Page</th></tr></thead><tbody>
    ${d.events.map((e) => `<tr><td>${esc(when(e.at))}</td><td><span class="src ${KIND[e.label] || "new"}">${esc(e.label)}</span></td><td>${money(e.value)}</td>
      <td>${esc([e.a_campaign, e.a_term, e.a_content].filter(Boolean).join(" › ") || "—")}</td><td class="trk-path" title="${esc(e.path)}">${esc(e.path)}</td></tr>`).join("")}</tbody>`;
  $("evBack").onclick = (ev) => { ev.preventDefault(); openEvents(back); };
}

function drawSetup() {
  $("setup").innerHTML = `<div class="panel-head"><h2>Tracking setup</h2><button class="btn btn-sm btn-ghost" id="setupClose">Close</button></div>
    <p class="hint trk-pad">The ProfitDesk pixel is a Shopify web pixel inside each store's ProfitDesk app (like the wetracked.io Connect app).
      Shopify only runs it for shoppers who allowed tracking in the store's cookie banner. <b>Watch only</b>: tracks and reports, sends nothing
      to Meta, so it can run next to wetracked.io without double counting.</p>
    <div class="table-wrap"><table class="table"><thead><tr><th>Store</th><th>Pixel</th><th>Last 24 h</th><th>Meta pixel ID</th><th>Conversions API token</th><th>Mode</th></tr></thead><tbody>
    ${stores.map((s) => { const d = s.health.day || {}; return `<tr data-s="${s.id}"><td class="name">${esc(s.name)}<span class="sub">${esc(s.domain)}</span></td>
      <td>${s.installed ? `<span class="src invoice">On</span> <button class="btn btn-sm btn-ghost" data-install>Re-install</button>` : `<button class="btn btn-sm btn-primary" data-install>Switch on</button>`}</td>
      <td class="hint">${s.health.last ? `${d.page_viewed || 0} views · ${d.product_added_to_cart || 0} carts · ${d.checkout_started || 0} checkouts · ${d.checkout_completed || 0} orders` : "—"}</td>
      <td><input class="control trk-in" data-f="meta_pixel" value="${esc(s.cfg.meta_pixel)}" placeholder="Dataset ID"></td>
      <td><input class="control trk-in" data-f="meta_token" type="password" placeholder="${s.cfg.has_token ? "Saved · paste to replace" : "Paste token"}"></td>
      <td><select class="control" data-f="mode"><option value="watch"${s.cfg.mode === "watch" ? " selected" : ""}>Watch only</option>
        <option value="send" disabled>Send to Meta (next)</option></select></td></tr>`; }).join("")}</tbody></table></div>`;
  $("setupClose").onclick = () => { $("setup").hidden = true; };
  for (const b of $("setup").querySelectorAll("[data-install]")) b.onclick = async () => {
    const id = b.closest("tr").dataset.s;
    b.disabled = true;
    try { await api(`/api/tracking/install/${id}`, { method: "POST" }); toast("Pixel switched on. Events start with the next visitors."); await refreshStores(); }
    catch (e) { toast(e.message, true); b.disabled = false; }
  };
  for (const el of $("setup").querySelectorAll("[data-f]")) el.onchange = async () => {
    const id = el.closest("tr").dataset.s;
    try { await api(`/api/tracking/settings/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [el.dataset.f]: el.value }) });
          toast("Saved."); if (el.dataset.f === "meta_token") el.value = ""; await refreshStores(); }
    catch (e) { toast(e.message, true); }
  };
}
async function refreshStores() {
  stores = (await api("/api/tracking/stores")).stores;
  if (!$("setup").hidden) drawSetup();
}

for (const b of document.querySelectorAll("#level [data-l]")) b.onclick = () => {
  level = b.dataset.l;
  for (const x of document.querySelectorAll("#level [data-l]")) x.classList.toggle("on", x === b);
  $("key").value = "";
  load();
};
$("store").onchange = load;
$("range").onchange = () => { if ($("range").value === "custom") { $("dates").hidden = false; return; } load(); };
$("from").onchange = $("to").onchange = load;
$("key").onchange = load;
$("reload").onclick = load;
$("q").oninput = () => { page = 0; draw(); };
$("rows").onchange = () => { page = 0; draw(); };
$("noteX").onclick = () => { $("note").hidden = true; try { localStorage.setItem("pdTrkNote", "off"); } catch { /* private */ } };
$("evClose").onclick = () => { $("evModal").hidden = true; };
$("evModal").onclick = (e) => { if (e.target === $("evModal")) $("evModal").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("evModal").hidden = true; });
$("setupBtn").onclick = () => { $("setup").hidden = !$("setup").hidden; if (!$("setup").hidden) drawSetup(); };
boot();
