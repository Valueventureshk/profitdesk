/* EcomVital Disputes desk: PayPal cases and Airwallex chargebacks, the order's evidence, AI drafts
   and the actions PayPal / Airwallex allow right now. Every figure and rule comes from the server. */

const $ = (id) => document.getElementById(id);
let data = null, state = PDUrl.get("s") || "respond", openKey = PDUrl.get("case") || null, current = null;
let month = PDUrl.get("m") || null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, cur) {
  if (v === null || v === undefined) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "USD" }).format(v); }
  catch { return Number(v).toFixed(2) + " " + (cur || ""); }
}
function day(d) {
  if (!d) return "—";
  const t = new Date(d + "T00:00:00");
  return isNaN(t) ? d : t.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
function monthName(m) {
  if (m === "all") return "All time";
  const t = new Date(m + "-01T00:00:00");
  return isNaN(t) ? m : t.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}
const perCur = (o) => Object.entries(o || {}).map(([cur, v]) => money(v, cur)).join(" · ") || money(0, "AUD");
function when(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  return isNaN(t) ? iso : t.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/disputes"; throw new Error("Log in again."); }
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
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 8000 : 3500);
}

const LOGO = {
  paypal: `<svg class="logo" viewBox="0 0 24 24" aria-label="PayPal"><path fill="#003087" d="M7.1 21.5H3.6L6.5 3h6.4c3.6 0 5.6 1.8 5 5-.7 3.8-3.4 5.3-6.9 5.3H9.2z"/><path fill="#0070E0" d="M9.4 21.5H6.9l.6-3.9h2.1c3.5 0 6.3-1.6 7-5.6.2-1 .1-1.9-.2-2.6 1.6.8 2.3 2.4 1.9 4.4-.7 3.8-3.4 5.3-6.9 5.3H10z"/></svg>`,
  airwallex: `<svg class="logo" viewBox="0 0 24 24" aria-label="Airwallex"><path fill="#FF4F42" d="M12 3l9 16h-4.2L12 10.4 7.2 19H3z"/></svg>`,
};
const STATE_NAME = { respond: "Needs our response", waiting: "Waiting on customer", review: "PayPal / bank deciding", closed: "Closed" };
const PROVIDER = { paypal: "PayPal", airwallex: "Airwallex" };
const who = (c) => `<span class="dsp-prov ${c.provider}">${LOGO[c.provider] || ""}${PROVIDER[c.provider] || c.provider}</span>`;
const stateName = (c) => (c.state === "review" ? (c.provider === "paypal" ? "PayPal deciding" : "Card bank deciding") : STATE_NAME[c.state] || c.state);
const OUTCOME = { won: ["invoice", "Won"], lost: ["bad", "Lost"], refunded: ["catalog", "Refunded"], cancelled: ["new", "Cancelled by customer"] };
const EVIDENCE_TYPES = [["PROOF_OF_FULFILLMENT", "Proof it was shipped / delivered (tracking)"], ["PROOF_OF_REFUND", "Proof of refund"],
  ["PROOF_OF_RETURN", "Proof of return"], ["ITEM_DESCRIPTION", "Item description"], ["PROOF_OF_DELIVERY_SIGNATURE", "Delivery signature"],
  ["RETURN_POLICY", "Return policy"], ["OTHER", "Other"]];
const CHALLENGE_REASONS = [["PRODUCT_RECEIVED", "Customer received the item"], ["CUSTOMER_WITHDRAWN", "Customer withdrew the dispute"],
  ["CUSTOMER_REFUNDED", "Customer was already refunded"], ["PURCHASE_HISTORY", "Customer's purchase history"],
  ["NOT_ENTITLED", "Customer isn't entitled to a refund"], ["AUTHENTIC_PRODUCT", "The product is authentic"], ["OTHER_REASONS", "Other"]];
const ACCEPT_REASONS = [["AGREEMENT_REACHED_WITH_CUSTOMER", "Agreed with the customer"], ["VALID_CUSTOMER_DISPUTE", "The customer is right"],
  ["LOW_VALUE_TRANSACTION", "Not worth fighting"], ["CUSTOMER_RELATIONSHIP_MAINTENANCE", "Keep the customer happy"], ["OTHERS", "Other"]];
const nice = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());

function due(c) {
  if (c.state === "closed") return `<span class="src ${(OUTCOME[c.outcome] || ["new"])[0]}">${esc((OUTCOME[c.outcome] || [, nice(c.outcome || "closed")])[1])}</span>`;
  if (c.state !== "respond") return `<span class="src catalog">${esc(stateName(c))}</span>`;
  if (c.days_left === null || c.days_left === undefined) return `<span class="hint">—</span>`;
  const d = c.days_left;
  const cls = d < 0 ? "bad" : d <= 3 ? "changed" : "catalog";
  const txt = d < 0 ? "Overdue" : d < 1 ? `${Math.max(1, Math.round(d * 24))} h left` : `${Math.floor(d)} days left`;
  return `<span class="src ${cls}" title="Respond by ${esc(when(c.due))}">${txt}</span>`;
}

async function load(fresh = false) {
  $("refresh").disabled = true;
  try {
    data = await api(`/api/disputes${fresh ? "?refresh=1" : ""}`);
  } catch (e) { toast(e.message, true); $("refresh").disabled = false; return; }
  $("refresh").disabled = false;
  $("problems").innerHTML = (data.problems || []).map((p) => `<div class="warn">${esc(p)}</div>`).join("") +
    (data.connected ? "" : `<div class="warn">Connect PayPal or Airwallex first (Cash flow → Settings).</div>`);
  for (const b of document.querySelectorAll("[data-n]")) b.textContent = data.counts[b.dataset.n] || "";
  const stake = Object.entries(data.at_stake).map(([cur, v]) => money(v, cur)).join(" · ") || "—";
  const soon = data.cases.filter((c) => c.state === "respond" && c.days_left !== null && c.days_left <= 3).length;
  $("cards").innerHTML = `
    <div class="card"><div class="card-label">Open cases</div><div class="card-value">${data.cases.filter((c) => c.state !== "closed").length}</div><div class="card-note">${data.counts.respond || 0} need our response</div></div>
    <div class="card"><div class="card-label">Money at stake</div><div class="card-value dsp-stake">${stake}</div><div class="card-note">open cases</div></div>
    <div class="card"><div class="card-label">Due within 3 days</div><div class="card-value">${soon}</div><div class="card-note">${soon ? "answer these first" : "nothing urgent"}</div></div>`;
  const months = Object.keys(data.closed_months || {}).filter((m) => m !== "all").sort().reverse();
  if (!month || !(data.closed_months || {})[month]) month = months[0] || "all";
  $("month").innerHTML = [...months, "all"].map((m) => `<option value="${m}"${m === month ? " selected" : ""}>${esc(monthName(m))}</option>`).join("");
  drawClosed();
  drawList();
  if (openKey) openCase(openKey, false);
}

function drawClosed() {
  const m = (data.closed_months || {})[month] || { count: 0, won_count: 0, lost_count: 0, other_count: 0, total: {}, won: {}, lost: {}, by_outcome: {} };
  const bo = m.by_outcome || {};
  const split = (a, b, na, nb) => [bo[a] ? `${bo[a]} ${na}` : "", bo[b] ? `${bo[b]} ${nb}` : ""].filter(Boolean).join(" · ");
  $("closedCards").innerHTML = `
    <div class="card"><div class="card-label">Closed cases</div><div class="card-value">${m.count}</div>
      <div class="card-note">${esc(perCur(m.total))} in total</div></div>
    <div class="card dsp-won"><div class="card-label">Won</div><div class="card-value dsp-stake">${esc(perCur(m.won))}</div>
      <div class="card-note">${m.won_count} case${m.won_count === 1 ? "" : "s"}${split("won", "cancelled", "won", "withdrawn by the customer") ? " · " + esc(split("won", "cancelled", "won", "withdrawn by the customer")) : ""}</div></div>
    <div class="card dsp-lost"><div class="card-label">Lost</div><div class="card-value dsp-stake">${esc(perCur(m.lost))}</div>
      <div class="card-note">${m.lost_count} case${m.lost_count === 1 ? "" : "s"}${split("lost", "refunded", "lost", "refunded / accepted") ? " · " + esc(split("lost", "refunded", "lost", "refunded / accepted")) : ""}</div></div>`;
}

function drawList() {
  for (const b of document.querySelectorAll("#tabs [data-s]")) b.classList.toggle("on", b.dataset.s === state);
  const onTab = document.querySelector("#tabs .on");
  if (onTab) $("tabs").scrollLeft = onTab.offsetLeft - 8;     // phones: the chosen tab stays in view
  const closed = state === "closed";
  const rows = data.cases.filter((c) => c.state === state && (!closed || month === "all" || (c.closed_day || "").slice(0, 7) === month));
  $("list").innerHTML = rows.length ? `<thead><tr><th>${closed ? "Outcome" : "Due"}</th><th>Case</th><th>Order</th><th>Customer</th><th class="num">Amount</th>
      <th>Came in</th>${closed ? "<th>Closed</th>" : ""}</tr></thead><tbody>
    ${rows.map((c) => `<tr class="clickable${c.key === openKey ? " on" : ""}" data-key="${esc(c.key)}">
      <td>${due(c)}</td>
      <td class="name">${who(c)} ${esc(c.stage)} · ${esc(c.reason)}<span class="sub">${esc(c.account)} · ${esc(c.id)}</span></td>
      <td>${c.order_name ? `${esc(c.order_name)}<span class="sub">${esc(c.store)}</span>` : `<span class="hint">not linked</span>`}</td>
      <td>${esc(c.customer || "")}</td>
      <td class="num">${money(c.amount, c.currency)}</td>
      <td class="dsp-date">${esc(day(c.opened_day))}</td>
      ${closed ? `<td class="dsp-date">${esc(day(c.closed_day))}</td>` : ""}</tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">${closed && month !== "all" ? `No cases closed in ${esc(monthName(month))}.` : "No cases here."}</td></tr></tbody>`;
  for (const tr of $("list").querySelectorAll("[data-key]")) tr.onclick = () => openCase(tr.dataset.key);
}

async function openCase(key, scroll = true) {
  openKey = key;
  PDUrl.set({ case: key });
  const box = $("case");
  box.hidden = false;
  box.innerHTML = `<p class="hint">Opening the case…</p>`;
  if (scroll) box.scrollIntoView({ behavior: "smooth", block: "start" });
  try { current = await api(`/api/disputes/case?key=${encodeURIComponent(key)}`); }
  catch (e) { box.innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
  drawCase();
}

function drawCase() {
  const c = current, ctx = c.context || {}, o = ctx.order;
  const msgs = (c.messages || []).map((m) => `<div class="dsp-msg ${esc(m.by)}"><b>${esc({ customer: "Customer", us: "Us", paypal: "PayPal", bank: "Card bank" }[m.by] || m.by)}</b>
      <span class="hint">${esc(when(m.at))}</span><div>${esc(m.text).replace(/\n/g, "<br>")}</div>
      ${m.docs?.length ? `<span class="hint">📎 ${esc(m.docs.join(", "))}</span>` : ""}</div>`).join("");
  const parcels = (ctx.shipments || []).map((s) => `<div class="dsp-parcel"><b>${esc(s.company || "")} ${esc(s.number)}</b>
      <span class="src ${s.status === "delivered" ? "invoice" : "catalog"}">${esc(nice(s.status))}</span>
      ${s.tracking_url ? `<a href="${esc(s.tracking_url)}" target="_blank" rel="noopener">track</a>` : ""}
      <div class="hint">Shipped ${esc(when(s.fulfilled_at))}${s.delivered_at ? ` · delivered ${esc(when(s.delivered_at))}` : ""}</div>
      ${(s.events || []).slice(0, 4).map((e) => `<div class="dsp-scan">${esc(when(e.time))} · ${esc(e.location || "")} ${esc(e.desc || "")}</div>`).join("")}</div>`).join("");
  const orderBox = o ? `<div class="dsp-order"><b>${esc(o.name)}</b> · ${esc(o.store)} · ${money(o.total, o.currency)}
      <div class="hint">${esc(o.customer)} ${esc(o.email)} · ${esc([o.city, o.country].filter(Boolean).join(", "))} · ordered ${esc(when(o.created))}</div>
      ${(o.items || []).map((i) => `<div>${i.qty} × ${esc(i.product)}</div>`).join("")}
      ${c.linked_by ? `<div class="hint">Linked by ${esc(c.linked_by)}</div>` : ""}</div>`
    : `<div class="warn">EcomVital couldn't find the order for this payment. Type the order number to link it:
        <span class="dsp-link"><input class="control" id="linkOrder" placeholder="#BR2342"><button class="btn btn-sm btn-primary" id="linkGo" data-write-only>Link</button></span></div>`;
  const forms = c.actions.map((a) => actionForm(a, c, ctx)).join("");
  const locked = c.actions.filter((a) => !c.can?.[a]);
  $("case").innerHTML = `
    <div class="panel-head"><h2>${who(c)} ${esc(c.stage)} · ${esc(c.reason)} · ${money(c.amount, c.currency)}</h2>
      <span>${due(c)} <button class="btn btn-sm btn-ghost" id="caseClose">Close</button></span></div>
    <p class="hint">${esc(PROVIDER[c.provider])} account “${esc(c.account)}” · case ${esc(c.id)} · ${esc(stateName(c))} · status ${esc(nice(c.status))}
      ${c.due && c.state === "respond" ? ` · respond by <b>${esc(when(c.due))}</b>` : ""} · opened ${esc(when(c.created))}</p>
    ${c.reason_detail && !/^[a-z_]+$/.test(c.reason_detail) ? `<p class="hint">${esc(c.reason_detail)}</p>` : ""}
    ${c.requested_evidence?.length ? `<div class="notice">PayPal asks for: <b>${esc(c.requested_evidence.map(nice).join(", "))}</b></div>` : ""}
    <div class="dsp-grid">
      <div><h3>The order</h3>${orderBox}
        ${parcels ? `<h3>Parcels</h3>${parcels}` : (o ? `<p class="hint">No tracking number on this order yet.</p>` : "")}
        ${o ? `<p><a class="btn btn-sm btn-ghost" href="/api/disputes/pack?key=${encodeURIComponent(c.key)}" target="_blank" rel="noopener">View the evidence pack</a>
          <span class="hint">order, items, parcels with every scan, emails with the customer</span></p>` : ""}
        ${(ctx.emails || []).length ? `<p class="hint">${ctx.emails.length} emails with this customer are in the pack.</p>` : ""}</div>
      <div><h3>Conversation</h3>${msgs || `<p class="hint">No messages.</p>`}</div>
    </div>
    ${c.state !== "closed" && c.actions.length ? `<div class="dsp-ai" data-write-only>
        <button class="btn btn-sm btn-primary" id="aiDraft"${c.has_ai ? "" : " disabled title='Add the Anthropic key in Settings → AI'"}>✨ AI: what should we do?</button>
        <div id="aiOut"></div></div>` : ""}
    ${forms ? `<div class="dsp-actions" data-write-only>${forms}</div>` : (c.state !== "closed" ? `<p class="hint">Nothing to do on this case right now.</p>` : "")}
    ${locked.length ? `<p class="hint">Needs Manage access: ${esc(locked.map((a) => c.action_names[a]).join(", "))}.</p>` : ""}
    ${(c.log || []).length ? `<h3>What the team did</h3>${c.log.map((l) => `<div class="dsp-log"><span class="hint">${esc(when(l.at.replace(" ", "T") + "Z"))}</span> <b>${esc(l.who)}</b> ${esc(l.action)} <span class="hint">${esc(l.detail || "")}</span></div>`).join("")}` : ""}`;
  $("caseClose").onclick = () => { $("case").hidden = true; openKey = null; PDUrl.set({ case: null }); drawList(); };
  if ($("linkGo")) $("linkGo").onclick = async () => {
    try {
      await api("/api/disputes/link", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: c.key, order: $("linkOrder").value }) });
      toast("Order linked."); openCase(c.key, false); load();
    } catch (e) { toast(e.message, true); }
  };
  if ($("aiDraft")) $("aiDraft").onclick = aiDraft;
  for (const f of $("case").querySelectorAll("form[data-act]")) f.onsubmit = (e) => { e.preventDefault(); act(f); };
  for (const b of $("case").querySelectorAll("[data-toggle]")) b.onclick = () => {
    const f = $("case").querySelector(`form[data-act="${b.dataset.toggle}"]`);
    for (const x of $("case").querySelectorAll("form[data-act]")) if (x !== f) x.hidden = true;
    f.hidden = !f.hidden;
  };
}

function actionForm(a, c, ctx) {
  if (!c.can?.[a]) return "";
  const name = c.action_names[a];
  const hasShip = (ctx.shipments || []).length > 0, hasOrder = !!ctx.order;
  const note = (ph, field = "note") => `<textarea class="control" name="${field}" rows="5" maxlength="2000" placeholder="${esc(ph)}"></textarea>`;
  const filesIn = `<label class="hint">Add files (PDF, JPG, PNG; 10 MB each) <input type="file" name="files" multiple accept=".pdf,.jpg,.jpeg,.png,.gif${c.provider === "airwallex" ? ",.webp" : ""}"></label>`;
  const pack = hasOrder ? `<label class="hint"><input type="checkbox" name="pack" value="yes" checked> Attach the evidence pack</label>` : "";
  const danger = ["accept-claim", "accept", "make-offer", "accept-offer"].includes(a);
  let body = "";
  if (a === "send-message") body = note("Message to the customer (they see it)", "message") + filesIn;
  else if (a === "provide-evidence" || a === "appeal") body = `<label class="hint">What it proves <select class="control" name="evidence_type">${EVIDENCE_TYPES.map(([k, v]) =>
      `<option value="${k}"${k === (hasShip ? "PROOF_OF_FULFILLMENT" : "OTHER") ? " selected" : ""}>${v}</option>`).join("")}</select></label>
      ${note("Your explanation for PayPal (facts: dates, tracking, what the customer said)")}
      ${hasShip ? `<label class="hint"><input type="checkbox" name="tracking" value="yes" checked> Send the tracking numbers</label>` : ""}${pack}${filesIn}`;
  else if (a === "provide-supporting-info") body = note("Your explanation for PayPal") + pack + filesIn;
  else if (a === "challenge") body = `<label class="hint">Why it should be reversed <select class="control" name="challenge_reason">${CHALLENGE_REASONS.map(([k, v]) =>
      `<option value="${k}"${k === (hasShip ? "PRODUCT_RECEIVED" : "OTHER_REASONS") ? " selected" : ""}>${v}</option>`).join("")}</select></label>
      ${note("Your explanation for the card bank")}${pack}${filesIn}<p class="hint">Airwallex takes images (JPG, PNG, WEBP): the evidence pack is sent as PNG pages.</p>`;
  else if (a === "make-offer") body = `<label class="hint">Offer <select class="control" name="offer_type">${(c.offer_types.length ? c.offer_types : ["REFUND"]).map((t) =>
      `<option value="${esc(t)}">${esc(nice(t))}</option>`).join("")}</select></label>
      <label class="hint">Amount (${esc(c.currency)}) <input class="control" name="amount" type="number" step="0.01" min="0" max="${c.amount}"></label>${note("Note to the customer")}`;
  else if (a === "accept-claim") body = `${c.accept_types.length ? `<label class="hint">How <select class="control" name="accept_type">${c.accept_types.map((t) =>
      `<option value="${esc(t)}">${esc(nice(t))}</option>`).join("")}</select></label>` : ""}
      <label class="hint">Partial refund amount (only for a partial refund) <input class="control" name="amount" type="number" step="0.01" min="0" max="${c.amount}"></label>${note("Note")}
      <p class="warn">PayPal refunds the customer from your balance and closes the case in their favour.</p>`;
  else if (a === "accept") body = `<label class="hint">Why <select class="control" name="accept_reason">${ACCEPT_REASONS.map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
      ${note("Note")}<p class="warn">The customer keeps the money and the case closes.</p>`;
  else if (a === "acknowledge-return-item") body = `${c.return_types.length ? `<label class="hint">What came back <select class="control" name="ack_type">${c.return_types.map((t) =>
      `<option value="${esc(t)}">${esc(nice(t))}</option>`).join("")}</select></label>` : ""}${note("Note")}`;
  else body = note("Note");
  return `<button type="button" class="btn btn-sm ${danger ? "btn-ghost" : "btn-primary"}" data-toggle="${a}">${esc(name)}</button>
    <form data-act="${a}" hidden class="dsp-form${danger ? " danger" : ""}"><h4>${esc(name)}</h4>${body}
      <button class="btn btn-sm ${danger ? "btn-danger" : "btn-primary"}">${esc(name)}</button></form>`;
}

async function act(f) {
  const a = f.dataset.act, c = current;
  const sure = { "accept-claim": `Accept and refund ${money(c.amount, c.currency)}? PayPal refunds the customer now.`,
                 accept: `Accept this chargeback of ${money(c.amount, c.currency)}? The customer keeps the money.`,
                 "make-offer": "Send this offer to the customer?", "accept-offer": "Accept the customer's offer?",
                 escalate: "Escalate this to a PayPal claim?" }[a] || `${c.action_names[a]}: send it to ${c.provider === "paypal" ? "PayPal" : "Airwallex"} now?`;
  if (!confirm(sure)) return;
  const fd = new FormData(f);
  fd.set("key", c.key);
  fd.set("action", a);
  const btn = f.querySelector("button:not([type=button])");
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    await api("/api/disputes/act", { method: "POST", body: fd });
    toast(`Done: ${c.action_names[a]}.`);
    await openCase(c.key, false);
    load();
  } catch (e) { toast(e.message, true); btn.disabled = false; btn.textContent = c.action_names[a]; }
}

async function aiDraft() {
  const b = $("aiDraft");
  b.disabled = true;
  b.textContent = "Reading the case…";
  try {
    const d = await api("/api/disputes/draft", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: current.key }) });
    const rec = { fight: "Fight it", refund: "Refund", offer: "Make an offer", wait: "Reply and wait" }[d.recommendation] || d.recommendation;
    $("aiOut").innerHTML = `<div class="notice"><b>AI suggests: ${esc(rec)}</b>${d.offer_amount ? ` (${money(d.offer_amount, current.currency)})` : ""}
      <div>${esc(d.why)}</div>${d.missing?.length ? `<div class="hint">Would help: ${esc(d.missing.join(" · "))}</div>` : ""}
      <div class="hint">The drafts below are filled in. Check them before sending.</div></div>`;
    const fill = (act, field, text) => { const el = $("case").querySelector(`form[data-act="${act}"] [name="${field}"]`); if (el && text) el.value = text; };
    fill("send-message", "message", d.message);
    for (const a of ["provide-evidence", "appeal", "provide-supporting-info", "challenge"]) fill(a, "note", d.evidence_notes);
    const et = $("case").querySelector(`form[data-act="provide-evidence"] [name="evidence_type"]`);
    if (et && [...et.options].some((o) => o.value === d.evidence_type)) et.value = d.evidence_type;
    if (d.offer_amount) fill("make-offer", "amount", String(d.offer_amount));
    const first = { fight: ["provide-evidence", "challenge", "provide-supporting-info", "appeal"], wait: ["send-message"],
                    offer: ["make-offer", "send-message"], refund: ["accept-claim", "accept"] }[d.recommendation] || [];
    const pick = first.find((a) => $("case").querySelector(`form[data-act="${a}"]`));
    if (pick) $("case").querySelector(`[data-toggle="${pick}"]`).click();
  } catch (e) { toast(e.message, true); }
  b.disabled = false;
  b.textContent = "✨ AI: what should we do?";
}

for (const b of document.querySelectorAll("#tabs [data-s]")) b.onclick = () => { state = b.dataset.s; PDUrl.set({ s: state === "respond" ? null : state }); drawList(); };
$("month").onchange = () => {
  month = $("month").value;
  state = "closed";
  PDUrl.set({ m: month, s: "closed" });
  drawClosed();
  drawList();
};
$("refresh").onclick = () => load(true);
load();
