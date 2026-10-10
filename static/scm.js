/* ProfitDesk SCM desk: every order from every store with its tracking
   (Shopify for orders and tracking numbers, 17TRACK for where the parcel is). */

const $ = (id) => document.getElementById(id);
const VIEWS = [
  ["all", "All"], ["awaiting", "Waiting for tracking"], ["pending", "Pending"], ["info_received", "Info received"],
  ["in_transit", "In transit"], ["out_for_delivery", "Out for delivery"], ["pickup", "Ready for pickup"],
  ["delivered", "Delivered"], ["exception", "Exception"], ["stuck", "Stuck"], ["cancelled", "Cancelled"],
];
const LABEL = {
  awaiting: "Waiting for tracking", pending: "Pending", info_received: "Info received", in_transit: "In transit",
  out_for_delivery: "Out for delivery", pickup: "Ready for pickup", delivered: "Delivered", exception: "Exception",
  failed_attempt: "Failed attempt", expired: "Expired",
};
let state = { view: "all", page: 1 };
let data = null;

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
  const d = new Date(iso);
  return d.toLocaleString(undefined, withTime
    ? { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { month: "short", day: "2-digit" });
}
function ago(iso) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
}
const chip = (s) => `<span class="scm-chip st-${esc(s)}">${esc(LABEL[s] || s)}</span>`;

async function load() {
  const q = new URLSearchParams({ view: state.view, page: state.page, q: $("q").value, store: $("store").value,
    carrier: $("carrier").value, country: $("country").value, start: $("from").value, end: $("to").value });
  try {
    data = await api(`/api/scm/shipments?${q}`);
  } catch (e) { toast(e.message, true); return; }
  render();
}

function fill(sel, items, label) {
  const keep = sel.value;
  sel.innerHTML = `<option value="">${label}</option>` + items.map((x) =>
    typeof x === "object" ? `<option value="${x.id}">${esc(x.name)}</option>` : `<option>${esc(x)}</option>`).join("");
  sel.value = keep;
}

function render() {
  $("tabs").innerHTML = VIEWS.map(([k, name]) =>
    `<button data-v="${k}" class="${state.view === k ? "on" : ""}${k === "stuck" && data.counts.stuck ? " warn" : ""}">${name} (${data.counts[k] ?? 0})</button>`).join("");
  for (const b of $("tabs").querySelectorAll("[data-v]")) b.onclick = () => { state.view = b.dataset.v; state.page = 1; load(); };
  if ($("store").options.length <= 1) fill($("store"), data.stores, "All stores");
  fill($("carrier"), data.carriers, "All carriers");
  fill($("country"), data.countries, "All destinations");
  const st = data.status || {};
  const errs = Object.entries(st).filter(([k, v]) => v && (k.startsWith("shopify_error_") || k === "track_error")).map(([, v]) => v);
  $("problems").innerHTML = errs.map((p) => `<div class="warning">${esc(p)}</div>`).join("");
  $("sub").textContent = st.last_sync ? `Every order from every store · updated ${ago(st.last_sync) === "today" ? new Date(st.last_sync).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : ago(st.last_sync)} · checks every 10 minutes`
    : "Every order from every store · the first sync takes a few minutes";
  $("count").textContent = `Showing ${data.total.toLocaleString()} ${data.total === 1 ? "parcel" : "parcels"}`;

  $("table").innerHTML = `<thead><tr><th>Order</th><th>Tracking number</th><th>Carrier</th><th>Last checkpoint</th>
      <th>Transit</th><th>Order date</th><th>Status</th></tr></thead><tbody>${data.rows.map((r) => {
    const carrier = r.carrier_name || r.company || "–";
    const tn = r.number
      ? (r.tracking_url ? `<a href="${esc(r.tracking_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(r.number)}</a>` : esc(r.number))
      : `<span class="hint">no tracking yet</span>`;
    const last = r.last_event ? `<span class="scm-last">${esc(r.last_event)}</span><span class="sub">${esc(r.last_location || "")}${r.last_location ? " · " : ""}${ago(r.last_event_at)}</span>`
      : (r.register_error ? `<span class="sub">17TRACK: ${esc(r.register_error)}</span>` : "–");
    const transit = r.number ? (r.transit_days != null ? `${r.transit_days}d` : "–")
      : (r.age_days != null ? `<span class="${r.age_days >= 5 ? "scm-late" : ""}">${r.age_days}d unfulfilled</span>` : "–");
    return `<tr data-id="${r.id}"><td class="name"><strong>${esc(r.order_name)}</strong><span class="sub">${esc(r.store)}</span></td>
      <td class="mono">${tn}${r.last_mile_number && r.last_mile_number !== r.number ? `<span class="sub">${esc(r.last_mile)} ${esc(r.last_mile_number)}</span>` : ""}</td>
      <td>${esc(carrier)}${r.last_mile && r.last_mile !== carrier ? `<span class="sub">→ ${esc(r.last_mile)}</span>` : ""}</td>
      <td class="scm-lastcell">${last}</td><td class="mono">${transit}</td><td>${when(r.order_at)}</td><td>${chip(r.status)}</td></tr>`;
  }).join("") || `<tr><td colspan="7" class="hint">Nothing here.</td></tr>`}</tbody>`;
  for (const tr of $("table").querySelectorAll("[data-id]")) tr.onclick = () => openShipment(tr.dataset.id);

  $("pager").innerHTML = data.pages > 1 ? `<button class="btn btn-sm btn-ghost" id="prev"${state.page <= 1 ? " disabled" : ""}>‹</button>
    <span class="hint">Page ${state.page} of ${data.pages}</span>
    <button class="btn btn-sm btn-ghost" id="next"${state.page >= data.pages ? " disabled" : ""}>›</button>` : "";
  if ($("prev")) { $("prev").onclick = () => { state.page--; load(); }; $("next").onclick = () => { state.page++; load(); }; }
}

async function openShipment(id) {
  let d;
  try { d = await api(`/api/scm/shipments/${id}`); } catch (e) { toast(e.message, true); return; }
  const s = d.shipment;
  $("dTitle").innerHTML = `${esc(s.order_name)} <span class="sub">${esc(s.store)}</span>`;
  const events = s.events || [];
  $("dBody").innerHTML = `
    <div class="scm-d-top">${chip(s.status)}${s.shopify_url ? ` <a class="btn btn-sm btn-ghost" href="${esc(s.shopify_url)}" target="_blank" rel="noopener">Open in Shopify</a>` : ""}</div>
    <dl class="scm-facts">
      <dt>Customer</dt><dd>${esc(s.customer || "–")}<span class="sub">${esc(s.email || "")}</span></dd>
      <dt>Ship to</dt><dd>${esc([s.city, s.country].filter(Boolean).join(", ") || "–")}</dd>
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
      : `<p class="hint">${s.number ? (s.registered ? "No carrier updates yet." : "Not sent to 17TRACK yet (check the stores and start date in Tracking connection).") : "The supplier hasn't uploaded tracking yet."}</p>`}
    <h3>Customer emails</h3>
    ${d.tickets.length ? `<ul class="scm-tickets">${d.tickets.map((t) => `<li><a href="/inbox#ticket-${t.id}">${esc(t.subject || "(no subject)")}</a>
      <span class="sub">${esc(t.status)} · ${esc(t.labels || t.kind)} · ${when(t.created_at.replace(" ", "T") + "Z", false)}</span></li>`).join("")}</ul>`
      : `<p class="hint">None.</p>`}`;
  for (const a of $("dBody").querySelectorAll("[data-open]")) a.onclick = (e) => { e.preventDefault(); openShipment(a.dataset.open); };
  $("drawer").hidden = false;
}
$("dClose").onclick = () => { $("drawer").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("drawer").hidden = true; });

let typing;
$("q").oninput = () => { clearTimeout(typing); typing = setTimeout(() => { state.page = 1; load(); }, 300); };
for (const id of ["store", "carrier", "country", "from", "to"]) $(id).onchange = () => { state.page = 1; load(); };
$("sync").onclick = async () => {
  try { await api("/api/scm/sync", { method: "POST" }); toast("Syncing. New orders show up in a minute or two."); }
  catch (e) { toast(e.message, true); }
  setTimeout(load, 60000);
};

async function loadTracking() {
  let t;
  try { t = await api("/api/tracking"); } catch { return; }     // not an owner
  $("trackStores").innerHTML = t.store_list.map((s) => `<label class="scm-pick"><input type="checkbox" value="${s.id}"${t.stores.includes(s.id) ? " checked" : ""}> ${esc(s.name)}</label>`).join("");
  $("trackFrom").value = t.from;
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
  const body = { stores: [...$("trackStores").querySelectorAll("input:checked")].map((i) => +i.value), from: $("trackFrom").value };
  if ($("key").value.trim()) body.key = $("key").value.trim();
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

(function boot() {
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const t = new Date();
  $("to").value = iso(t);
  $("from").value = iso(new Date(t.getTime() - 59 * 86400000));
  load();
  loadTracking();
})();
