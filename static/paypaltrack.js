/* EcomVital Apps → PayPal Tracking: the on/off switch, which stores, and every parcel's row
   (sent / ready / waiting / not matched / errors). All matching and sending happens on the server. */

const $ = (id) => document.getElementById(id);
let data = null, tab = PDUrl.get("s") ?? "sent", store = PDUrl.get("store") || "", query = PDUrl.get("q") || "";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, cur) {
  if (v === null || v === undefined) return "";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "USD" }).format(v); }
  catch { return Number(v).toFixed(2) + " " + (cur || ""); }
}
function when(iso) {
  if (!iso) return "";
  const t = new Date(iso.length === 19 ? iso + "Z" : iso);
  return isNaN(t) ? iso : t.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/paypal-tracking"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || body.detail || `Request failed (${r.status})`);
  return body;
}
const send = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 8000 : 4000);
}

const SCM = { in_transit: "In transit", info_received: "Info received", pending: "Not found yet", pickup: "Ready for pickup",
  out_for_delivery: "Out for delivery", delivered: "Delivered", failed_attempt: "Delivery attempt failed",
  exception: "Problem", expired: "Tracking expired", awaiting: "Waiting for tracking" };
const PILL = { sent: "ok", ready: "new", waiting: "cancelled", unmatched: "changed", error: "bad", failed: "bad" };

async function load() {
  const q = new URLSearchParams(Object.entries({ state: tab, store, q: query }).filter(([, v]) => v));
  try { data = await api(`/api/paypal-tracking?${q}`); } catch (e) { toast(e.message, true); return; }
  draw();
}

function draw() {
  const c = data.config, n = data.counts || {};
  $("problems").innerHTML = (data.last?.problems || []).map((p) => `<div class="warn">${esc(p)}</div>`).join("") +
    (data.connected ? "" : `<div class="warn">No PayPal account is connected yet. Connect it on Cash flow → Settings first.</div>`);
  $("on").checked = c.on;
  $("onText").textContent = c.on ? (c.stores.length ? "On: sending tracking to PayPal" : "On, but no store is ticked") : "Off: nothing is sent";
  $("onNote").textContent = c.on
    ? `Every 15 minutes, in-transit parcels of the ticked stores go to the matching PayPal payment.${c.by ? ` Switched on by ${c.by}, ${when(c.at)}.` : ""}`
    : "While off, the list below still shows what it would send (Ready to send). Press Preview to check now.";
  $("later").checked = c.later;
  $("stores").innerHTML = data.stores.map((s) => `<label><input type="checkbox" data-store="${s.id}" data-manage-only${s.on ? " checked" : ""}> ${esc(s.name)}</label>`).join("")
    || `<span class="hint">No stores connected.</span>`;
  const l = data.last;
  $("lastRun").textContent = l ? `Last check ${when(l.at)}${l.by && l.by !== "auto" ? ` by ${l.by}` : ""}: ${l.sent} sent, ${l.ready} ready, ${l.waiting} waiting, ${l.unmatched} not matched, ${l.errors} errors${l.sending ? "" : " (preview, nothing sent)"}.` : "Not checked yet.";
  const errors = (n.error || 0) + (n.failed || 0);
  $("cards").innerHTML = `
    <div class="card"><div class="card-label">Sent to PayPal</div><div class="card-value">${n.sent || 0}</div><div class="card-note">tracking numbers on their PayPal payment</div></div>
    <div class="card"><div class="card-label">Ready to send</div><div class="card-value">${n.ready || 0}</div><div class="card-note">${c.on ? "go with the next run" : "would go once switched on"}</div></div>
    <div class="card"><div class="card-label">Waiting for in transit</div><div class="card-value">${n.waiting || 0}</div><div class="card-note">PayPal orders not moving yet (info received…)</div></div>
    <div class="card"><div class="card-label">Not matched</div><div class="card-value">${n.unmatched || 0}</div><div class="card-note">no exact PayPal payment found, not sent</div></div>
    <div class="card"><div class="card-label">Errors</div><div class="card-value">${errors}</div><div class="card-note">${n.failed ? `${n.failed} gave up after ${data.rules.max_tries} tries` : "retried automatically"}</div></div>`;
  for (const b of document.querySelectorAll("[data-n]")) b.textContent = (b.dataset.n === "errors" ? errors : n[b.dataset.n]) || "";
  for (const b of document.querySelectorAll("#tabs [data-s]")) b.classList.toggle("on", b.dataset.s === tab);
  const sel = $("storeFilter");
  if (sel.options.length === 1) sel.innerHTML += data.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  sel.value = store;
  drawList();
}

function why(r) {
  if (r.state === "sent") return r.note ? `<div class="why">${esc(r.note)}</div>` : "";
  if (r.state === "error" || r.state === "failed")
    return `<div class="why bad">${esc(r.error || "")}${r.state === "failed" ? ` <button class="btn btn-sm" data-retry="${r.id}" data-manage-only>Try again</button>` : ` (try ${r.tries} of ${data.rules.max_tries})`}</div>`;
  if (r.state === "unmatched") return `<div class="why">${esc(r.note || "")}</div>`;
  if (r.state === "waiting") return `<div class="why">Sent once 17TRACK shows it in transit.</div>`;
  return "";
}

function drawList() {
  const rows = data.rows;
  if (!rows.length) {
    $("list").innerHTML = `<tbody><tr><td class="hint">${tab === "sent" && !(data.counts.sent) ? "Nothing sent yet." : "Nothing here."}</td></tr></tbody>`;
    return;
  }
  $("list").innerHTML = `<thead><tr><th>Order</th><th>Customer</th><th>Tracking number</th><th>SCM status</th><th>PayPal payment</th><th>Result</th></tr></thead><tbody>
    ${rows.map((r) => `<tr>
      <td><strong>${esc(r.order_name)}</strong><small>${esc(r.store)}</small></td>
      <td>${esc(r.customer)}<small>${esc(r.email)}</small></td>
      <td><span class="mono">${esc(r.number)}</span><small>${esc(r.company || "")}${r.carrier ? ` → PayPal: ${esc(r.carrier === "OTHER" ? `Other (${r.carrier_other})` : r.carrier)}` : ""}</small></td>
      <td>${esc(SCM[r.scm_status] || r.scm_status)}<small>${r.fulfilled_at ? "shipped " + esc(when(r.fulfilled_at)) : ""}</small></td>
      <td>${r.txn_id ? `<span class="mono">${esc(r.txn_id)}</span><small>${esc(money(r.amount, r.currency))}${r.paypal_account ? " · " + esc(r.paypal_account) : ""}${r.matched_by ? " · by " + esc(r.matched_by) : ""}</small>` : `<span class="hint">—</span>`}</td>
      <td><span class="src ${PILL[r.state] || "new"}">${esc(r.state_name)}</span>${r.submitted_at ? `<small>${esc(when(r.submitted_at))}${r.submitted_by && r.submitted_by !== "auto" ? " · " + esc(r.submitted_by) : ""}</small>` : ""}${why(r)}</td>
    </tr>`).join("")}</tbody>`;
}

async function saveSettings(body) {
  try { await send("/api/paypal-tracking/settings", body, "PUT"); toast("Saved."); }
  catch (e) { toast(e.message, true); }
  load();
}

async function run(dry) {
  const b = dry ? $("preview") : $("runNow");
  b.disabled = true;
  const old = b.textContent;
  b.textContent = dry ? "Checking…" : "Sending…";
  try {
    const r = await send("/api/paypal-tracking/run", { dry });
    if (r.running) toast("A check is already running; refresh in a minute.");
    else {
      const s = r.summary;
      toast(r.dry ? `Preview: ${s.ready} ready to send, ${s.waiting} waiting, ${s.unmatched} not matched. Nothing was sent.`
        : `${s.sent} sent to PayPal${s.errors ? `, ${s.errors} errors` : ""}.`, s.errors > 0);
      if (r.dry && tab !== "ready") { tab = "ready"; PDUrl.set({ s: tab }); }
    }
  } catch (e) { toast(e.message, true); }
  b.disabled = false;
  b.textContent = old;
  load();
}

$("on").onchange = (e) => {
  const on = e.target.checked;
  if (on) {
    const ready = data.counts.ready || 0;
    const picked = data.config.stores.length;
    if (!picked && !confirm("No store is ticked yet, so nothing will be sent until you tick one. Switch on anyway?")) { e.target.checked = false; return; }
    if (!confirm(`Switch on? Every 15 minutes, in-transit tracking numbers of the ticked stores will be added to their PayPal payments.${ready ? ` ${ready} are ready to go now.` : ""}`)) { e.target.checked = false; return; }
  }
  saveSettings({ on });
};
$("later").onchange = (e) => saveSettings({ later: e.target.checked });
$("stores").onchange = () => saveSettings({ stores: [...document.querySelectorAll("[data-store]:checked")].map((x) => +x.dataset.store) });
$("preview").onclick = () => run(true);
$("runNow").onclick = () => {
  if (!data.config.on) { toast("Switch the app on first (or use Preview to see what it would send).", true); return; }
  run(false);
};
$("tabs").onclick = (e) => {
  const b = e.target.closest("[data-s]");
  if (!b) return;
  tab = b.dataset.s;
  PDUrl.set({ s: tab });
  load();
};
$("storeFilter").onchange = (e) => { store = e.target.value; PDUrl.set({ store }); load(); };
let typing;
$("q").value = query;
$("q").oninput = (e) => {
  clearTimeout(typing);
  typing = setTimeout(() => { query = e.target.value; PDUrl.set({ q: query }); load(); }, 300);
};
$("list").onclick = async (e) => {
  const b = e.target.closest("[data-retry]");
  if (!b) return;
  try { await send("/api/paypal-tracking/retry", { id: +b.dataset.retry }); toast("It will be tried again on the next run."); }
  catch (err) { toast(err.message, true); }
  load();
};
load();
setInterval(load, 60000);
