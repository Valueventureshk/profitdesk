/* ProfitDesk SCM desk: every order from every store with its tracking, laid out
   like Parcel Panel's Orders page. Orders and tracking numbers come from Shopify,
   where the parcel is from 17TRACK; notes and flags are the team's own. */

const $ = (id) => document.getElementById(id);
const VIEWS = [
  ["all", "All"], ["awaiting", "Waiting for tracking"], ["pending", "Pending"], ["info_received", "Info received"],
  ["in_transit", "In transit"], ["out_for_delivery", "Out for delivery"], ["pickup", "Ready for pickup"],
  ["delivered", "Delivered"], ["exception", "Exception"],
];
const MORE = [["stuck", "Stuck"], ["flagged", "Flagged"], ["noted", "With notes"], ["untracked", "Not tracked"], ["cancelled", "Cancelled"]];
const LABEL = {
  awaiting: "Waiting for tracking", pending: "Pending", info_received: "Info received", in_transit: "In transit",
  out_for_delivery: "Out for delivery", pickup: "Ready for pickup", delivered: "Delivered", exception: "Exception",
  failed_attempt: "Failed attempt", expired: "Expired", untracked: "Not tracked",
};
let state = { view: "all", page: 1 };
let data = null;
const picked = new Map();     // row id -> {store_id, order_id}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/scm"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3000);
}
function when(iso, withTime = true) {
  if (!iso) return "–";
  const d = new Date(iso.includes("T") || iso.includes("Z") ? iso : iso.replace(" ", "T") + "Z");
  return d.toLocaleString(undefined, withTime
    ? { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { month: "short", day: "2-digit" });
}
function ago(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
}
const statusOf = (r) => (r.number && !r.registered ? "untracked" : r.status);
const chip = (s) => `<span class="scm-chip st-${esc(s)}"><i></i>${esc(LABEL[s] || s)}</span>`;
const days = (n) => (n === null || n === undefined ? "–" : `${Math.round(n * 10) / 10} d`);

function query(extra = {}) {
  const range = $("range").value;
  let start = $("from").value, end = $("to").value;
  if (range !== "custom") {
    const d = new Date(Date.now() - (+range - 1) * 86400000);
    start = d.toISOString().slice(0, 10);
    end = "";
  }
  return new URLSearchParams({ view: state.view, page: state.page, q: $("q").value, store: $("store").value,
    carrier: $("carrier").value, country: $("country").value, start, end, sort: $("sort").value,
    date_by: $("dateBy").value, ...extra });
}

const DEFAULTS = { store: "", carrier: "", country: "", range: "60", dateBy: "order", sort: "order_desc" };
function markFilters() {
  let any = false;
  for (const [id, def] of Object.entries(DEFAULTS)) {
    const on = $(id).value !== def;
    $(id).classList.toggle("on", on);
    if (on && !["sort"].includes(id)) any = true;
  }
  $("reset").hidden = !any && !$("q").value;
}

async function load() {
  markFilters();
  try { data = await api(`/api/scm/shipments?${query()}`); }
  catch (e) { toast(e.message, true); return; }
  render();
}

function fill(sel, items, label) {
  const keep = sel.value;
  sel.innerHTML = `<option value="">${label}</option>` + items.map((x) =>
    typeof x === "object" ? `<option value="${x.id}">${esc(x.name)}</option>` : `<option>${esc(x)}</option>`).join("");
  sel.value = keep;
}

function renderTiles() {
  const s = data.summary || {}, c = data.counts;
  const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : "–");
  const tiles = [
    ["Orders", (s.orders || 0).toLocaleString(), `${(s.parcels || 0).toLocaleString()} parcels`, "all"],
    ["Delivered", (s.delivered || 0).toLocaleString(), `${pct(s.delivered || 0, s.tracked || 0)} of tracked parcels`, "delivered"],
    ["Valid tracking", pct(s.with_updates || 0, s.tracked || 0), `${(s.with_updates || 0).toLocaleString()} with carrier updates`, "in_transit"],
    ["Avg delivery time", days(s.avg_delivery_days), "shipped → delivered", "delivered"],
    ["Avg dispatch time", days(s.avg_dispatch_days), "order → tracking uploaded", "awaiting"],
    ["Waiting for tracking", (c.awaiting || 0).toLocaleString(), "supplier hasn't shipped", "awaiting"],
    ["Exceptions", (c.exception || 0).toLocaleString(), "failed, returned, expired", "exception"],
    ["Stuck", (c.stuck || 0).toLocaleString(), "no movement 7+ days", "stuck"],
  ];
  $("tiles").innerHTML = tiles.map(([t, v, n, view]) => `<button class="scm-tile${["exception", "stuck"].includes(view) && v !== "0" ? " warn" : ""}" data-v="${view}">
    <span class="t">${t}</span><strong>${v}</strong><span class="n">${n}</span></button>`).join("");
  for (const b of $("tiles").querySelectorAll("[data-v]")) b.onclick = () => setView(b.dataset.v);
}

function setView(v) { state.view = v; state.page = 1; load(); }

function render() {
  renderTiles();
  const c = data.counts;
  const moreOn = MORE.some(([k]) => k === state.view);
  $("tabs").innerHTML = VIEWS.map(([k, name]) =>
    `<button data-v="${k}" class="${state.view === k ? "on" : ""}">${name} <em>${(c[k] ?? 0).toLocaleString()}</em></button>`).join("") +
    `<select class="scm-more${moreOn ? " on" : ""}" id="more"><option value="">More views</option>${MORE.map(([k, n]) =>
      `<option value="${k}"${state.view === k ? " selected" : ""}>${n} (${(c[k] ?? 0).toLocaleString()})</option>`).join("")}</select>`;
  for (const b of $("tabs").querySelectorAll("[data-v]")) b.onclick = () => setView(b.dataset.v);
  $("more").onchange = (e) => e.target.value && setView(e.target.value);
  if ($("store").options.length <= 1) fill($("store"), data.stores, "All stores");
  fill($("carrier"), data.carriers, "All carriers");
  fill($("country"), data.countries, "All destinations");
  const st = data.status || {};
  const errs = Object.entries(st).filter(([k, v]) => v && (k.startsWith("shopify_error_") || k === "track_error")).map(([, v]) => v);
  $("problems").innerHTML = errs.map((p) => `<div class="warning">${esc(p)}</div>`).join("");
  $("sub").textContent = st.last_sync ? `Every order from every store · updated ${ago(st.last_sync) === "today" ? new Date(st.last_sync).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : ago(st.last_sync)} · checks every 10 minutes`
    : "Every order from every store · the first sync takes a few minutes";
  $("count").textContent = `Showing ${data.total.toLocaleString()} ${data.total === 1 ? "shipment" : "shipments"}`;
  $("export").href = `/api/scm/export?${query()}`;

  $("table").innerHTML = `<thead><tr><th class="scm-check"><input type="checkbox" id="pickAll"></th><th>Order</th><th>Tracking number</th><th>Carrier</th><th>Last checkpoint</th>
      <th>Transit</th><th>Order date</th><th>Status</th></tr></thead><tbody>${data.rows.map((r) => {
    const carrier = r.carrier_name || r.company || "–";
    const tn = r.number
      ? `<a href="${esc(r.tracking_url || "#")}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(r.number)}</a>`
      : `<span class="hint">no tracking yet</span>`;
    const last = r.last_event ? `<span class="scm-last" title="${esc(r.last_event)}">${esc(r.last_event)}</span><span class="sub">${esc(r.last_location || "")}${r.last_location ? " · " : ""}${ago(r.last_event_at)}</span>`
      : (r.register_error ? `<span class="sub">17TRACK: ${esc(r.register_error)}</span>` : "–");
    const transit = r.number ? (r.transit_days != null ? `${r.transit_days} d` : "–")
      : (r.age_days != null ? `<span class="${r.age_days >= 5 ? "scm-late" : ""}">${r.age_days} d unfulfilled</span>` : "–");
    const marks = `${r.flagged ? `<span class="scm-flag" title="Flagged">⚑</span>` : ""}${r.notes ? `<span class="scm-note-mark" title="${esc(r.last_note || "")}">✎ ${r.notes}</span>` : ""}`;
    return `<tr data-id="${r.id}" class="${r.flagged ? "flagged" : ""}"><td class="scm-check" onclick="event.stopPropagation()"><input type="checkbox" data-pick="${r.id}" data-store="${r.store_id}" data-order="${esc(r.order_id)}"${picked.has(String(r.id)) ? " checked" : ""}></td>
      <td class="name"><strong>${esc(r.order_name)}</strong> ${marks}<span class="sub">${esc(r.store)}${r.country ? ` · ${esc(r.country)}` : ""}</span></td>
      <td class="mono">${tn}${r.last_mile_number && r.last_mile_number !== r.number ? `<span class="sub">${esc(r.last_mile)} ${esc(r.last_mile_number)}</span>` : ""}</td>
      <td>${esc(carrier)}${r.last_mile && r.last_mile !== carrier ? `<span class="sub">→ ${esc(r.last_mile)}</span>` : ""}</td>
      <td class="scm-lastcell">${last}</td><td class="mono">${transit}</td><td>${when(r.order_at)}</td><td>${chip(statusOf(r))}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="hint">Nothing here.</td></tr>`}</tbody>`;
  for (const tr of $("table").querySelectorAll("tr[data-id]")) tr.onclick = () => openShipment(tr.dataset.id);
  for (const cb of $("table").querySelectorAll("[data-pick]")) cb.onchange = () => {
    if (cb.checked) picked.set(cb.dataset.pick, { store: cb.dataset.store, order: cb.dataset.order });
    else picked.delete(cb.dataset.pick);
    renderBulk();
  };
  $("pickAll").onchange = (e) => {
    for (const cb of $("table").querySelectorAll("[data-pick]")) { cb.checked = e.target.checked; cb.onchange(); }
  };
  renderBulk();

  $("pager").innerHTML = data.pages > 1 ? `<button class="btn btn-sm btn-ghost" id="prev"${state.page <= 1 ? " disabled" : ""}>‹</button>
    <span class="hint">Page ${state.page} of ${data.pages}</span>
    <button class="btn btn-sm btn-ghost" id="next"${state.page >= data.pages ? " disabled" : ""}>›</button>` : "";
  if ($("prev")) { $("prev").onclick = () => { state.page--; load(); }; $("next").onclick = () => { state.page++; load(); }; }
}

function renderBulk() {
  $("bulk").hidden = !picked.size;
  $("bulkCount").textContent = `${picked.size} selected`;
}
async function bulkFlag(flagged) {
  const seen = new Set();
  for (const { store, order } of picked.values()) {
    const k = `${store}/${order}`;
    if (seen.has(k)) continue;
    seen.add(k);
    await post(`/api/scm/orders/${store}/${encodeURIComponent(order)}/flag`, { flagged });
  }
  picked.clear();
  toast(flagged ? "Flagged." : "Flags removed.");
  load();
}
$("bulkFlag").onclick = () => bulkFlag(true).catch((e) => toast(e.message, true));
$("bulkUnflag").onclick = () => bulkFlag(false).catch((e) => toast(e.message, true));

async function openShipment(id) {
  let d;
  try { d = await api(`/api/scm/shipments/${id}`); } catch (e) { toast(e.message, true); return; }
  const s = d.shipment;
  const base = `/api/scm/orders/${s.store_id}/${encodeURIComponent(s.order_id)}`;
  $("dTitle").innerHTML = `${esc(s.order_name)} <span class="sub">${esc(s.store)}</span>`;
  const events = s.events || [];
  $("dBody").innerHTML = `
    <div class="scm-d-top">${chip(statusOf(s))}
      <button class="btn btn-sm ${d.flag ? "btn-primary" : "btn-ghost"}" id="dFlag" data-write-only title="${d.flag ? `Flagged by ${esc(d.flag.flagged_by || "")}` : "Mark this order as needing attention"}">⚑ ${d.flag ? "Flagged" : "Flag"}</button>
      ${s.shopify_url ? `<a class="btn btn-sm btn-ghost" href="${esc(s.shopify_url)}" target="_blank" rel="noopener">Open in Shopify</a>` : ""}
      ${s.tracking_page ? `<a class="btn btn-sm btn-ghost" href="${esc(s.tracking_page)}" target="_blank" rel="noopener">Customer's tracking page</a>` : ""}</div>
    <h3>Notes</h3>
    <div class="scm-notes">
      <div class="scm-note-add" data-write-only><textarea id="noteText" rows="2" placeholder="Add a note: supplier reply, customer request, reshipment, refund…"></textarea>
        <button class="btn btn-sm btn-primary" id="noteAdd">Add note</button></div>
      ${d.notes.length ? d.notes.map((n) => `<div class="scm-note"><p>${esc(n.text).replace(/\n/g, "<br>")}</p>
        <span class="sub">${esc(n.author || "")} · ${when(n.created_at)} <a href="#" data-del="${n.id}" data-write-only>delete</a></span></div>`).join("")
        : `<p class="hint">No notes yet.</p>`}
    </div>
    <h3>Details</h3>
    <dl class="scm-facts">
      <dt>Customer</dt><dd>${esc(s.customer || "–")}<span class="sub">${esc(s.email || "")}</span></dd>
      <dt>Ship to</dt><dd>${esc([s.city, s.province, s.country].filter(Boolean).join(", ") || "–")}</dd>
      <dt>Ordered</dt><dd>${when(s.order_at)} · ${s.items || 0} item${s.items === 1 ? "" : "s"}</dd>
      <dt>Fulfilled</dt><dd>${when(s.fulfilled_at)}</dd>
      <dt>Tracking</dt><dd class="mono">${esc(s.number || "none yet")}<span class="sub">${esc(s.carrier_name || s.company || "")}${s.last_mile ? ` → ${esc(s.last_mile)} ${esc(s.last_mile_number || "")}` : ""}</span></dd>
      ${s.eta_from || s.eta_to ? `<dt>Expected</dt><dd>${when(s.eta_from, false)}${s.eta_to && s.eta_to !== s.eta_from ? ` – ${when(s.eta_to, false)}` : ""}</dd>` : ""}
      ${s.delivered_at ? `<dt>Delivered</dt><dd>${when(s.delivered_at)}</dd>` : ""}
    </dl>
    ${d.other_parcels.length ? `<h3>Other parcels in this order</h3><ul class="scm-others">${d.other_parcels.map((o) =>
      `<li><a href="#" data-open="${o.id}">${esc(o.number || "waiting for tracking")}</a> ${chip(o.status)}</li>`).join("")}</ul>` : ""}
    <h3>Tracking</h3>
    ${events.length ? `<ol class="scm-timeline">${events.map((e) => `<li><span class="when">${when(e.time)}</span>
      <span class="what">${esc(e.text)}</span><span class="sub">${esc([e.location, e.carrier].filter(Boolean).join(" · "))}</span></li>`).join("")}</ol>`
      : `<p class="hint">${s.number ? (s.registered ? "No carrier updates yet." : "Not sent to 17TRACK yet (check the stores and start date in SCM → Settings).") : "The supplier hasn't uploaded tracking yet."}</p>`}
    <h3>Customer emails</h3>
    ${d.tickets.length ? `<ul class="scm-tickets">${d.tickets.map((t) => `<li><a href="/inbox#ticket-${t.id}">${esc(t.subject || "(no subject)")}</a>
      <span class="sub">${esc(t.status)} · ${esc(t.labels || t.kind)} · ${when(t.created_at.replace(" ", "T") + "Z", false)}</span></li>`).join("")}</ul>`
      : `<p class="hint">None.</p>`}`;
  for (const a of $("dBody").querySelectorAll("[data-open]")) a.onclick = (e) => { e.preventDefault(); openShipment(a.dataset.open); };
  if ($("dFlag")) $("dFlag").onclick = async () => {
    try { await post(`${base}/flag`, { flagged: !d.flag }); openShipment(id); load(); } catch (e) { toast(e.message, true); }
  };
  if ($("noteAdd")) $("noteAdd").onclick = async () => {
    const text = $("noteText").value.trim();
    if (!text) return;
    try { await post(`${base}/notes`, { text }); openShipment(id); load(); } catch (e) { toast(e.message, true); }
  };
  for (const a of $("dBody").querySelectorAll("[data-del]")) a.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Delete this note?")) return;
    try { await api(`/api/scm/notes/${a.dataset.del}`, { method: "DELETE" }); openShipment(id); load(); } catch (err) { toast(err.message, true); }
  };
  $("drawer").hidden = false;
}
$("dClose").onclick = () => { $("drawer").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("drawer").hidden = true; });

let typing;
$("q").oninput = () => { clearTimeout(typing); typing = setTimeout(() => { state.page = 1; load(); }, 300); };
for (const id of ["store", "carrier", "country", "from", "to", "sort", "dateBy"]) $(id).onchange = () => { state.page = 1; load(); };
$("reset").onclick = () => {
  for (const [id, def] of Object.entries(DEFAULTS)) $(id).value = def;
  $("q").value = "";
  $("customDates").hidden = true;
  state.page = 1;
  load();
};
$("range").onchange = () => { $("customDates").hidden = $("range").value !== "custom"; state.page = 1; load(); };
$("sync").onclick = async () => {
  try { await post("/api/scm/sync"); toast("Syncing. New orders show up in a minute or two."); }
  catch (e) { toast(e.message, true); }
  setTimeout(load, 60000);
};
for (const b of $("pages").querySelectorAll("[data-page]")) b.onclick = () => {
  for (const x of $("pages").querySelectorAll("[data-page]")) x.classList.toggle("on", x === b);
  $("ordersPage").hidden = b.dataset.page !== "orders";
  $("settingsPage").hidden = b.dataset.page !== "settings";
};

async function loadTracking() {
  let t;
  try { t = await api("/api/tracking"); } catch { return; }     // not an owner
  $("trackStores").innerHTML = t.store_list.map((s) => `<label class="scm-pick"><input type="checkbox" value="${s.id}"${t.stores.includes(s.id) ? " checked" : ""}> ${esc(s.name)}</label>`).join("");
  $("trackFrom").value = t.from;
  $("pageStores").innerHTML = t.store_list.map((s) => `<div class="scm-page-row"><span>${esc(s.name)}</span>
    <label class="scm-pick"><input type="checkbox" data-drop="${s.id}"${t.dropship.includes(s.id) ? " checked" : ""}> Dropship mode</label>
    <label class="scm-pick" title="Send each tracking update to Shopify, so Shopify emails the customer when the parcel is out for delivery and delivered"><input type="checkbox" data-push="${s.id}"${(t.push || []).includes(s.id) ? " checked" : ""}> Tell Shopify (delivery emails)</label>
    <select class="control scm-lang" data-lang="${s.id}" title="Language of the tracking page">${[["en","English"],["es","Español"],["fr","Français"]].map(([k, n]) => `<option value="${k}"${(t.langs?.[s.id] || "en") === k ? " selected" : ""}>${n}</option>`).join("")}</select>
    <a href="/proxy/track?shop=${encodeURIComponent(t.domains[s.id])}" target="_blank" rel="noopener">Preview page</a>
    <span class="hint" title="Customers who opened the tracking page from a link in a shipping email">${(t.visits?.[s.id]?.email_links) ? `${t.visits[s.id].email_links} from emails · last ${ago(t.visits[s.id].last)}` : "no email visits yet"}</span></div>`).join("");
  $("webhook").textContent = t.webhook;
  $("key").placeholder = t.connected ? "Connected · paste a new key to replace it" : "Paste the key from 17TRACK → Settings → Security";
  const qt = t.quota;
  $("quota").textContent = qt ? `${(qt.quota_remain ?? 0).toLocaleString()} of ${(qt.quota_total ?? 0).toLocaleString()} parcels left` : (t.quota_error || (t.connected ? "" : "Not connected"));
  const st = t.status || {};
  $("trackStatus").textContent = [st.track_error, st.last_registered != null ? `Last run sent ${st.last_registered} new parcels to 17TRACK.` : "",
    st.last_webhook ? `Last update from 17TRACK ${ago(st.last_webhook)}.` : (t.connected ? "No webhook updates received yet." : "")].filter(Boolean).join(" ");
}
$("copyHook").onclick = () => { navigator.clipboard?.writeText($("webhook").textContent); toast("Copied."); };
$("saveTrack").onclick = async () => {
  const body = { stores: [...$("trackStores").querySelectorAll("input:checked")].map((i) => +i.value), from: $("trackFrom").value,
    dropship: [...$("pageStores").querySelectorAll("[data-drop]:checked")].map((i) => +i.dataset.drop),
    push: [...$("pageStores").querySelectorAll("[data-push]:checked")].map((i) => +i.dataset.push),
    langs: Object.fromEntries([...$("pageStores").querySelectorAll("[data-lang]")].map((x) => [x.dataset.lang, x.value])) };
  const key = $("key").value.trim();
  if (key) {
    if (/\s/.test(key) || key.length < 20) { toast("That doesn't look like a 17TRACK key. Leave the box empty to keep the saved one.", true); return; }
    body.key = key;
  }
  const b = $("saveTrack");
  b.disabled = true;
  try {
    await api("/api/tracking", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("key").value = "";
    toast("Saved. Parcels are being sent to 17TRACK.");
    loadTracking();
  } catch (e) { toast(e.message, true); }
  finally { b.disabled = false; }
};

async function loadCredits() {
  let c;
  try { c = await api("/api/scm/credits"); } catch { return; }
  if (!c || (c.left === undefined && !c.error)) return;
  const box = $("credits");
  box.hidden = false;
  if (c.error) { box.className = "scm-credits low"; box.innerHTML = `<strong>17TRACK</strong><span>${esc(c.error)}</span>`; return; }
  const pct = c.total ? Math.max(0, Math.min(100, (c.left / c.total) * 100)) : 0;
  box.className = "scm-credits" + (c.low ? " low" : "");
  box.innerHTML = `<div class="scm-credits-main"><span class="t">17TRACK credits</span>
      <strong>${(c.left ?? 0).toLocaleString()}</strong><span class="of">/ ${(c.total ?? 0).toLocaleString()}</span></div>
    <div class="scm-credits-bar"><i style="width:${pct}%"></i></div>
    <div class="scm-credits-note">${c.low ? `<b>Running low, top up now</b>`
      : `${c.days_left != null ? `lasts ~${c.days_left} days` : ""}${c.queued ? ` · ${c.queued.toLocaleString()} queued` : ""}`}
      <a href="https://api.17track.net/en" target="_blank" rel="noopener">Top up ↗</a></div>`;
}

(function boot() {
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const t = new Date();
  $("to").value = iso(t);
  $("from").value = iso(new Date(t.getTime() - 59 * 86400000));
  load();
  loadTracking();
  loadCredits();
  setInterval(loadCredits, 5 * 60 * 1000);
})();
