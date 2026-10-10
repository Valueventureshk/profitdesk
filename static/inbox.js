/* ProfitDesk Inbox: every store's support emails, sorted by the AI into tickets.
   This file draws things; sorting and every status come from the server. */

const $ = (id) => document.getElementById(id);
const MAIL_VIEWS = new Set(["all", "customer"]);
let view = "scm";
let current = null;          // {type: "ticket"|"mail", id}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/inbox"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify(data) });

let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 2500);
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
// Long links in emails (Gmail, tracking, Shopify) shown short but still clickable.
// ---- Translate: any email into the team's language (saved on the server, so each email is paid for once)
const TR_LANGS = ["English", "French", "Spanish", "German", "Italian", "Portuguese", "Dutch", "Arabic", "Chinese",
                  "Hindi", "Tamil", "Sinhala", "Urdu", "Tagalog"];
const trPref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const trSave = (k, v) => { try { localStorage.setItem(k, v); } catch { /* */ } };
let trOriginal = new Map();          // message id -> original text

function trBar() {
  const lang = trPref("pdTrLang", "English");
  return `<div class="tr-bar">
    <button class="btn btn-sm btn-ghost" id="trAll">🌐 Translate all</button>
    <select class="control" id="trLang" title="Translate into">${TR_LANGS.map((l) => `<option${l === lang ? " selected" : ""}>${l}</option>`).join("")}</select>
    <label class="tr-auto" title="Translate every conversation as soon as you open it"><input type="checkbox" id="trAuto"${trPref("pdTrAuto", "") ? " checked" : ""}> Always</label>
  </div>`;
}
const trButton = (id) => `<button class="tr-one" data-tr="${id}" title="Translate this email">Translate</button>`;

async function translateMsgs(ids, quiet) {
  ids = ids.filter((id) => !document.querySelector(`[data-mid="${id}"]`)?.classList.contains("translated"));
  if (!ids.length) return;
  const lang = $("trLang")?.value || trPref("pdTrLang", "English");
  for (const id of ids) { const b = document.querySelector(`[data-tr="${id}"]`); if (b) b.textContent = "Translating…"; }
  if ($("trAll")) $("trAll").textContent = "Translating…";
  try {
    const d = await api(`/api/inbox/translate?ids=${ids.join(",")}&lang=${encodeURIComponent(lang)}`);
    for (const id of ids) {
      const box = document.querySelector(`[data-mid="${id}"]`), t = d.translations[String(id)];
      if (!box || t === undefined) continue;
      box.querySelector(".msg-text").innerHTML = mailText(t);
      box.classList.add("translated");
      box.dataset.lang = lang;
      const b = box.querySelector("[data-tr]");
      if (b) b.textContent = "Show original";
    }
  } catch (e) {
    if (!quiet) toast(e.message, true);
    for (const id of ids) { const b = document.querySelector(`[data-tr="${id}"]`); if (b) b.textContent = "Translate"; }
  } finally { if ($("trAll")) $("trAll").textContent = "🌐 Translate all"; }
}
function showOriginal(id) {
  const box = document.querySelector(`[data-mid="${id}"]`);
  if (!box) return;
  box.querySelector(".msg-text").innerHTML = mailText(trOriginal.get(String(id)) || "");
  box.classList.remove("translated");
  const b = box.querySelector("[data-tr]");
  if (b) b.textContent = "Translate";
}
function wireTranslate() {
  const ids = [...document.querySelectorAll("#read [data-mid]")].map((x) => x.dataset.mid);
  if ($("trLang")) $("trLang").onchange = () => {
    trSave("pdTrLang", $("trLang").value);
    const on = ids.filter((id) => document.querySelector(`[data-mid="${id}"]`).classList.contains("translated"));
    on.forEach(showOriginal);
    if (on.length) translateMsgs(on);
  };
  if ($("trAuto")) $("trAuto").onchange = () => { trSave("pdTrAuto", $("trAuto").checked ? "1" : ""); if ($("trAuto").checked) translateMsgs(ids); };
  if ($("trAll")) $("trAll").onclick = () => {
    const allOn = ids.every((id) => document.querySelector(`[data-mid="${id}"]`).classList.contains("translated"));
    if (allOn) ids.forEach(showOriginal); else translateMsgs(ids);
  };
  for (const b of document.querySelectorAll("#read [data-tr]")) b.onclick = () => {
    const box = b.closest("[data-mid]");
    if (box.classList.contains("translated")) showOriginal(b.dataset.tr); else translateMsgs([b.dataset.tr]);
  };
  if (trPref("pdTrAuto", "")) translateMsgs(ids, true);
}

// The new part of an email, and the earlier conversation it quotes (shown small, folded).
const ATTRIB = /(\bwrote\s*:|a écrit\s*:|escribió\s*:|schrieb\s*.*:|ha scritto\s*:|-----\s*original message\s*-----|^_{10,}$)/i;
function splitQuote(text) {
  const lines = String(text || "").replace(/\ufeff/g, "").split(/\r?\n/);
  let cut = -1;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim();
    if (ATTRIB.test(l)) { cut = i; if (i > 0 && /^(on|le|el|am)\b/i.test(lines[i - 1].trim()) && !/^(on|le|el|am)\b/i.test(l)) cut = i - 1; break; }
    if (/^(from|de|von)\s*:/i.test(l) && lines.slice(i + 1, i + 4).some((x) => /^(sent|envoyé|enviado|date|gesendet)\s*:/i.test(x.trim()))) { cut = i; break; }
    if (l.startsWith(">") && lines.slice(i, i + 3).filter((x) => x.trim().startsWith(">")).length >= 2) { cut = i; break; }
  }
  if (cut < 0) return { main: text, quote: "", head: "" };
  const main = lines.slice(0, cut).join("\n").replace(/\s+$/, "");
  const rest = lines.slice(cut);
  const head = ATTRIB.test(rest[0]) || /^(on|le|el|am)\b/i.test(rest[0].trim()) ? rest.shift().trim().replace(/^>\s?/, "") : "";
  if (head && rest.length && /(wrote|a écrit|escribió)\s*:\s*$/i.test(rest[0]) && !ATTRIB.test(head)) rest.shift();
  const quote = rest.map((l) => l.replace(/^(\s*>)+\s?/, "")).join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return { main: main || "(no new text)", quote, head: head.replace(/\s*<[^>]*$/, "").replace(/\s*(wrote|a écrit|escribió)\s*:?\s*$/i, "") };
}
function mailText(text) {
  const { main, quote, head } = splitQuote(text);
  return `<div class="mail-body">${body(main)}</div>` + (quote ? `
    <div class="mail-quote" title="Click to show or hide the earlier message">
      <div class="mail-quote-head">↩ ${esc(head || "Earlier message")}</div>
      <div class="mail-body">${body(quote)}</div></div>` : "");
}
document.addEventListener("click", (e) => {
  const q = e.target.closest(".mail-quote");
  if (q && !e.target.closest("a")) q.classList.toggle("open");
});

function body(text) {
  return esc(text || "(empty)").replace(/https?:\/\/[^\s<>"]+/g, (url) => {
    let label = url;
    try {
      const u = new URL(url.replace(/&amp;/g, "&"));
      const path = u.pathname.length > 18 ? u.pathname.slice(0, 18) + "…" : u.pathname;
      label = u.hostname.replace(/^www\./, "") + (path === "/" ? "" : path);
    } catch { label = url.slice(0, 40) + "…"; }
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" title="${url}">${label}</a>`;
  });
}

const full = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

const LABEL = { scm: "SCM", cs: "CS" };
const KIND = { support: "Support", inquiry: "Inquiry", legal: "Legal" };
function chips(t) {
  const out = [];
  if (t.kind !== "support") out.push(`<span class="tk tk-${t.kind}">${KIND[t.kind]}</span>`);
  for (const l of (t.labels || "").split(",").filter(Boolean)) out.push(`<span class="tk tk-${l}">${LABEL[l]}</span>`);
  if (t.status === "closed") out.push(`<span class="tk tk-done">Closed</span>`);
  else out.push(t.waiting === "us" ? `<span class="tk tk-us">Waiting on us</span>`
                                   : `<span class="tk tk-them">We replied last</span>`);
  if (t.escalated) out.push(`<span class="tk tk-esc">Escalated</span>`);
  return out.join("");
}

async function loadCounts() {
  try {
    const c = await api("/api/inbox/counts");
    for (const b of document.querySelectorAll("[data-count]")) b.textContent = c[b.dataset.count] || "";
    $("aiWarn").innerHTML = c.ai_error ? `<div class="warn">${esc(c.ai_error)}</div>` : "";
    $("sub").textContent = `${c.waiting_us} tickets waiting on us`;
  } catch { /* shown elsewhere */ }
}

async function loadList() {
  const acct = $("mailboxSelect").value;
  PDUrl.set({ view: view === "scm" ? null : view, box: acct });
  const q = acct ? `&account=${acct}` : "";
  if (MAIL_VIEWS.has(view)) {
    let d;
    try { d = await api(`/api/inbox/messages?view=${view}${q}`); } catch (e) { $("list").innerHTML = esc(e.message); return; }
    $("list").innerHTML = d.messages.length ? d.messages.map((m) => `
      <button class="mail-row${current?.type === "mail" && current.id === m.id ? " on" : ""}" data-mail="${m.id}">
        <span class="mail-top"><strong>${esc(m.from_name || m.from_addr)}</strong><small>${when(m.date)}</small></span>
        <span class="mail-subj">${esc(m.subject || "(no subject)")}</span>
        <span class="mail-snip">${esc(m.snippet || "")}</span>
        <span class="mail-store">${esc(m.store || m.address)}${m.category ? ` · ${esc(m.category)}` : " · not sorted"}${m.attachments ? ` · 📎 ${m.attachments}` : ""}</span>
      </button>`).join("") : `<p class="hint inbox-empty">Nothing here.</p>`;
  } else {
    let d;
    try { d = await api(`/api/inbox/tickets?view=${view}${q}`); } catch (e) { $("list").innerHTML = esc(e.message); return; }
    $("list").innerHTML = d.tickets.length ? d.tickets.map((t) => `
      <button class="mail-row${current?.type === "ticket" && current.id === t.id ? " on" : ""}" data-ticket="${t.id}">
        <span class="mail-top"><strong>${esc(t.customer_name || t.customer_email)}</strong>
          <small>${when(t.status === "closed" ? t.closed_at : t.last_message_at)}</small></span>
        <span class="mail-subj">${esc(t.summary || t.subject || "(no subject)")}</span>
        <span class="mail-tags">${chips(t)}<span class="mail-store">${esc(t.store)} · ${t.emails} email${t.emails === 1 ? "" : "s"}</span></span>
      </button>`).join("") : `<p class="hint inbox-empty">Nothing here.</p>`;
  }
  for (const b of $("list").querySelectorAll("[data-mail]")) b.onclick = () => openMail(+b.dataset.mail);
  for (const b of $("list").querySelectorAll("[data-ticket]")) b.onclick = () => openTicket(+b.dataset.ticket);
}

function mark() {
  for (const b of $("list").querySelectorAll("[data-mail],[data-ticket]")) {
    const id = +(b.dataset.mail || b.dataset.ticket);
    const type = b.dataset.mail ? "mail" : "ticket";
    b.classList.toggle("on", current && current.type === type && current.id === id);
  }
}

async function openMail(id) {
  current = { type: "mail", id };
  PDUrl.set({ mail: id, ticket: null });
  mark();
  let m;
  try { m = await api(`/api/inbox/messages/${id}`); } catch (e) { $("read").innerHTML = esc(e.message); return; }
  if (m.ticket_id) return openTicket(m.ticket_id);
  $("read").innerHTML = `
    <h2 class="mail-h">${esc(m.subject || "(no subject)")}</h2>
    <div class="mail-meta"><strong>${esc(m.from_name || m.from_addr)}</strong> &lt;${esc(m.from_addr)}&gt;
      <span>to ${esc(m.to_addr)} · ${esc(m.store || m.address)} · ${full(m.date)}
      · ${m.category ? `sorted as <b>${esc(m.category)}</b>` : "not sorted yet"}</span></div>
    <div class="mail-actions" data-write-only>
      <span class="hint">Not right? Make it a ticket:</span>
      <button class="btn btn-sm btn-ghost" data-make="support">Customer ticket</button>
      <button class="btn btn-sm btn-ghost" data-make="inquiry">Inquiry</button>
      <button class="btn btn-sm btn-ghost" data-make="legal">Legal</button>
    </div>
    ${trBar()}
    ${m.attachments.length ? `<div class="mail-att">${m.attachments.map((a) => `📎 ${esc(a.name)}`).join(" · ")}</div>` : ""}
    <div class="mail-one" data-mid="${m.id}"><div class="tr-onebar">${trButton(m.id)}</div><div class="msg-text">${mailText(m.text)}</div></div>`;
  trOriginal = new Map([[String(m.id), m.text]]);
  wireTranslate();
  for (const b of $("read").querySelectorAll("[data-make]")) {
    b.onclick = async () => {
      try {
        const r = await post(`/api/inbox/messages/${id}/ticket`, { kind: b.dataset.make });
        toast("Ticket created.");
        refresh();
        openTicket(r.ticket_id);
      } catch (e) { toast(e.message, true); }
    };
  }
}

async function openTicket(id) {
  current = { type: "ticket", id };
  PDUrl.set({ ticket: id, mail: null });
  mark();
  let d;
  try { d = await api(`/api/inbox/tickets/${id}`); } catch (e) { $("read").innerHTML = esc(e.message); return; }
  const t = d.ticket;
  const labels = (t.labels || "").split(",").filter(Boolean);
  const snoozed = t.snoozed_until && new Date(t.snoozed_until) > new Date();
  const thread = d.messages.map((m) => `
    <div class="msg ${m.direction === "out" ? "out" : "in"}" data-mid="${m.id}">
      <div class="msg-head"><strong>${m.direction === "out" ? "You" : esc(m.from_name || m.from_addr)}</strong>
        <span>${trButton(m.id)} ${full(m.date)}</span></div>
      <div class="msg-text">${mailText(m.text)}</div>
      ${m.attachments.length ? `<div class="mail-att">${m.attachments.map((a) => `📎 ${esc(a.name)}`).join(" · ")}</div>` : ""}
    </div>`).join("");
  $("read").innerHTML = `
    <div class="tk-layout">
      <div class="tk-main">
        <h2 class="mail-h">${esc(t.subject || "(no subject)")}</h2>
        <div class="mail-meta"><strong>${esc(t.customer_name || "")}</strong> &lt;${esc(t.customer_email)}&gt;
          <span>${esc(t.store)} · ${esc(t.mailbox)} · ${d.messages.length} emails</span></div>
        ${trBar()}
        ${t.summary ? `<div class="tk-summary"><b>AI summary</b> ${esc(t.summary)}${t.orders ? ` · orders ${esc(t.orders)}` : ""}</div>` : ""}
        ${thread}
        <div class="reply-box">
          <div class="reply-actions">
            <button class="btn btn-primary" id="tkDraft" data-write-only>Draft reply with AI</button>
            ${t.gmail_link ? `<a class="btn btn-ghost" href="${esc(t.gmail_link)}" target="_blank" rel="noopener">Open in Gmail ↗</a>` : ""}
          </div>
          <div id="tkDraftBox" hidden>
            <textarea id="tkDraftText" class="control draft-text" rows="12"></textarea>
            <div class="reply-actions">
              <button class="btn btn-ghost" id="tkCopy">Copy</button>
              <span class="hint">Check it, copy it, and send it from Gmail. Sending from ProfitDesk comes next.</span>
            </div>
          </div>
        </div>
      </div>
      <aside class="tk-side">
        <div class="tk-orders" id="tkOrders"><div class="tk-side-label">Orders</div><p class="hint">Looking up…</p></div>
        <div class="tk-side-label">Status</div>
        <label class="tk-check"><input type="checkbox" disabled ${t.status === "open" && t.waiting === "customer" ? "checked" : ""}> We replied last</label>
        <label class="tk-check"><input type="checkbox" disabled ${t.status === "open" && t.waiting === "us" ? "checked" : ""}> Waiting on us</label>
        <label class="tk-check"><input type="checkbox" disabled ${t.status === "closed" ? "checked" : ""}> Solved</label>
        ${t.status === "closed" ? `<p class="hint">Closed ${full(t.closed_at)} by ${esc(t.closed_by || "")}${t.close_note ? ` · ${esc(t.close_note)}` : ""}</p>` : ""}
        <div data-write-only>
          ${t.status === "open"
            ? `<button class="btn btn-primary btn-block" data-act="close">Close ticket</button>`
            : `<button class="btn btn-ghost btn-block" data-act="reopen">Reopen</button>`}
          <div class="tk-side-label">Type</div>
          <div class="tk-toggles">
            <label><input type="checkbox" data-label="scm" ${labels.includes("scm") ? "checked" : ""}> SCM · order</label>
            <label><input type="checkbox" data-label="cs" ${labels.includes("cs") ? "checked" : ""}> CS · product</label>
          </div>
          <select class="control" id="tkKind">${Object.entries(KIND).map(([k, v]) =>
            `<option value="${k}"${k === t.kind ? " selected" : ""}>${v}</option>`).join("")}</select>
          <div class="tk-side-label">Follow-up</div>
          <label class="tk-check"><input type="checkbox" id="tkEsc" ${t.escalated ? "checked" : ""}> Escalated · needs owner</label>
          ${snoozed
            ? `<p class="hint">Snoozed until ${full(t.snoozed_until)}</p><button class="btn btn-sm btn-ghost" data-act="wake">Wake now</button>`
            : `<div class="tk-snooze"><input type="date" id="tkSnooze" class="control"><button class="btn btn-sm btn-ghost" data-act="snooze">Snooze</button></div>`}
          <button class="btn btn-sm btn-ghost tk-notcust" data-act="not_customer">Not a customer email</button>
        </div>
        ${t.gmail_link ? `<div class="tk-side-label">Email thread</div><a href="${esc(t.gmail_link)}" target="_blank" rel="noopener">Open in Gmail ↗</a>` : ""}
      </aside>
    </div>`;
  loadOrders(id);
  trOriginal = new Map(d.messages.map((m) => [String(m.id), m.text]));
  wireTranslate();
  if ($("tkDraft")) $("tkDraft").onclick = async () => {
    const b = $("tkDraft");
    b.disabled = true;
    b.textContent = "Writing…";
    try {
      const r = await post(`/api/inbox/tickets/${id}/draft`, {});
      $("tkDraftText").value = r.draft;
      $("tkDraftBox").hidden = false;
      $("tkDraftText").focus();
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "Draft again"; }
  };
  if ($("tkCopy")) $("tkCopy").onclick = async () => {
    try { await navigator.clipboard.writeText($("tkDraftText").value); toast("Copied."); }
    catch { $("tkDraftText").select(); document.execCommand("copy"); toast("Copied."); }
  };
  const act = async (payload, msg) => {
    try { await post(`/api/inbox/tickets/${id}`, payload); toast(msg); refresh(); openTicket(id); }
    catch (e) { toast(e.message, true); }
  };
  for (const b of $("read").querySelectorAll("[data-act]")) {
    b.onclick = () => {
      const a = b.dataset.act;
      if (a === "close") return act({ action: "close" }, "Ticket closed.");
      if (a === "reopen") return act({ action: "reopen" }, "Ticket reopened.");
      if (a === "wake") return act({ action: "wake" }, "Back in the list.");
      if (a === "snooze") {
        if (!$("tkSnooze").value) return toast("Pick a date first.", true);
        return act({ action: "snooze", until: $("tkSnooze").value }, "Snoozed.");
      }
      if (a === "not_customer") {
        if (!confirm("Not a customer email? The ticket is removed and the email stays in All mail.")) return;
        post(`/api/inbox/tickets/${id}`, { action: "not_customer" })
          .then(() => { toast("Removed."); current = null; PDUrl.set({ ticket: null, mail: null }); $("read").innerHTML = ""; refresh(); })
          .catch((e) => toast(e.message, true));
      }
    };
  }
  for (const c of $("read").querySelectorAll("[data-label]")) {
    c.onchange = () => act({ action: "labels", labels: [...$("read").querySelectorAll("[data-label]")]
      .filter((x) => x.checked).map((x) => x.dataset.label) }, "Type saved.");
  }
  if ($("tkKind")) $("tkKind").onchange = () => act({ action: "kind", kind: $("tkKind").value }, "Type saved.");
  if ($("tkEsc")) $("tkEsc").onchange = () => act({ action: "escalate", on: $("tkEsc").checked },
    $("tkEsc").checked ? "Escalated." : "No longer escalated.");
}

// How the order was paid, as a small badge (from Shopify's gateway names).
function payIcon(gateways) {
  const g = (gateways || []).join(" ").toLowerCase();
  if (!g) return "";
  if (g.includes("afterpay")) return `<span class="pay pay-afterpay" title="Afterpay (via Airwallex)">afterpay</span>`;
  if (g.includes("paypal")) return `<span class="pay pay-paypal" title="PayPal"><b>Pay</b>Pal</span>`;
  if (g.includes("airwallex")) return `<span class="pay pay-card" title="Card via Airwallex">▭ Card</span>`;
  if (g.includes("shopify_payments")) return `<span class="pay pay-card" title="Shopify Payments">▭ Card</span>`;
  return `<span class="pay" title="${esc(gateways.join(", "))}">${esc(gateways[0])}</span>`;
}

// A parcel as SCM sees it: status, tracking number (→ 17TRACK), latest checkpoint
const PARCEL = {
  awaiting: "Waiting for tracking", pending: "Pending", info_received: "Info received", in_transit: "In transit",
  out_for_delivery: "Out for delivery", pickup: "Ready for pickup", delivered: "Delivered", exception: "Exception",
  failed_attempt: "Failed attempt", expired: "Expired", untracked: "Not tracked",
};
function parcelBox(p) {
  const day = (x) => x ? new Date(x).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
  const carrier = p.carrier_name || p.company || "";
  const eta = p.status !== "delivered" && p.eta_to ? `Expected by ${day(p.eta_to)}` : "";
  return `<div class="ord-parcel">
    <div class="ord-parcel-top"><span class="scm-chip st-${esc(p.status)}"><i></i>${esc(PARCEL[p.status] || p.status)}</span>
      <a class="ord-scm" href="${esc(p.scm_url)}" target="_blank" rel="noopener" title="Open this order in SCM (notes, flags)">SCM ↗</a></div>
    ${p.number ? `<div class="ord-num"><span>${esc(carrier || "Tracking")}</span>
        <code title="Click to copy" data-copy="${esc(p.number)}">${esc(p.number)}</code></div>
      <div class="ord-links"><a href="${esc(p.track17)}" target="_blank" rel="noopener">17TRACK ↗</a>
        ${p.tracking_page ? `<a href="${esc(p.tracking_page)}" target="_blank" rel="noopener">Customer's page ↗</a>` : ""}</div>`
      : `<div class="hint">The supplier hasn't added tracking yet.</div>`}
    ${p.last_event ? `<div class="ord-event">${esc(p.last_event)}<small>${[p.last_location, day(p.last_event_at)].filter(Boolean).map(esc).join(" · ")}</small></div>` : ""}
    ${p.status === "delivered" && p.delivered_at ? `<div class="hint">Delivered ${day(p.delivered_at)}${p.transit_days ? ` · ${Math.round(p.transit_days)} days in transit` : ""}</div>` : eta ? `<div class="hint">${esc(eta)}</div>` : ""}
    ${p.last_mile_number && p.last_mile_number !== p.number ? `<div class="hint">Local carrier: ${esc(p.last_mile || "")} ${esc(p.last_mile_number)}</div>` : ""}
  </div>`;
}
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copy]");
  if (!c) return;
  navigator.clipboard?.writeText(c.dataset.copy).then(() => toast("Tracking number copied."));
});

async function loadOrders(id) {
  let d;
  try { d = await api(`/api/inbox/tickets/${id}/orders`); }
  catch (e) { if ($("tkOrders")) $("tkOrders").innerHTML = `<div class="tk-side-label">Orders</div><p class="hint">${esc(e.message)}</p>`; return; }
  if (!current || current.id !== id || !$("tkOrders")) return;
  const money = (v, c) => { try { return new Intl.NumberFormat(undefined, { style: "currency", currency: c }).format(v); } catch { return v.toFixed(2); } };
  $("tkOrders").innerHTML = `<div class="tk-side-label">Order${d.orders.length === 1 ? "" : "s"}</div>` + (d.orders.length ? d.orders.map((o) => `
    <div class="ord">
      <div class="ord-head"><a href="${esc(o.admin_url)}" target="_blank" rel="noopener">${esc(o.name)} ↗</a>
        <span>${new Date(o.created).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span></div>
      <div class="ord-row"><b>${money(o.total, o.currency)}</b> ${payIcon(o.gateways)}${o.refunded ? `<div class="hint">refunded ${money(o.refunded, o.currency)}</div>` : ""}</div>
      <div class="ord-tags">
        <span class="tk ${o.cancelled ? "tk-esc" : "tk-done"}">${o.cancelled ? "Cancelled" : esc((o.financial || "").toLowerCase().replace(/_/g, " "))}</span>
        <span class="tk ${o.fulfillment === "FULFILLED" ? "tk-them" : "tk-us"}">${esc((o.fulfillment || "").toLowerCase().replace(/_/g, " "))}</span>
      </div>
      ${o.items.map((i) => `<div class="ord-item">${i.quantity}× ${esc(i.title)}${i.variant ? `<small>${esc(i.variant)}</small>` : ""}</div>`).join("")}
      ${o.parcels && o.parcels.length ? o.parcels.map(parcelBox).join("")
        : o.tracking.length ? o.tracking.map((t) => `<div class="ord-track">${esc(t.company || "Tracking")}:
        ${t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.number || "track")} ↗</a>` : esc(t.number || "")}</div>`).join("")
        : `<div class="ord-track hint">No tracking yet</div>`}
      ${o.ship_to ? `<div class="ord-ship hint">Ships to ${esc(o.ship_to)}</div>` : ""}
    </div>`).join("") : `<p class="hint">${d.error ? esc(d.error) : "No Shopify order found for this customer."}</p>`);
}

/* ------------------------------------------------ AI training (owners) */

async function loadTraining() {
  let d;
  try { d = await api("/api/ai-training"); } catch (e) { $("read").innerHTML = `<p class="hint inbox-empty">${esc(e.message)}</p>`; return; }
  const j = d.job || {};
  const learned = d.learned ? `Last learned ${new Date(d.learned.at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
    from ${d.learned.conversations} conversations.` : "Not learned yet.";
  const open = d.questions.filter((q) => !q.answer);
  $("read").innerHTML = `<div class="train">
    <section class="train-sec">
      <h2>Teach the AI how you handle customers</h2>
      <p class="hint">The AI reads your SOPs and your team's past replies (up to 500 conversations), writes a playbook,
        and asks you about anything unclear. Drafts then follow your SOPs, your answers and the playbook.</p>
      <div class="train-status"><b>${learned}</b> ${d.examples ? `${d.examples} past conversations kept as examples.` : ""}
        <span class="hint">${d.emails} emails copied so far${d.emails_from ? `, back to ${new Date(d.emails_from).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}` : ""}.</span></div>
      ${j.running ? `<div class="notice">${esc(j.step || "Working…")} ${j.total ? `(${j.done}/${j.total})` : ""}</div>`
        : j.error ? `<div class="warn">${esc(j.error)}</div>` : j.step ? `<div class="notice ok">${esc(j.step)}</div>` : ""}
      <div class="train-run">
        <label>Copy older emails first
          <select id="trDays" class="control"><option value="0">No, use what's copied</option>
            <option value="90">Last 3 months</option><option value="180" selected>Last 6 months</option>
            <option value="365">Last 12 months</option></select></label>
        <label>Conversations to learn from
          <select id="trLimit" class="control"><option>100</option><option>250</option><option selected>500</option></select></label>
        <button class="btn btn-primary" id="trLearn" ${j.running ? "disabled" : ""}>${d.learned ? "Learn again" : "Start learning"}</button>
      </div>
      <p class="hint">Uses Claude Sonnet 5.5. Learning from 500 conversations costs roughly US$2–4 of your API credit and takes a few minutes.</p>
    </section>

    <section class="train-sec">
      <h3>Questions from the AI ${open.length ? `<span class="tab-badge">${open.length}</span>` : ""}</h3>
      ${d.questions.length ? d.questions.map((q) => `
        <div class="qa ${q.answer ? "done" : ""}">
          <div class="qa-q">${esc(q.question)}</div>
          <textarea class="control" rows="2" data-q="${q.id}" placeholder="Your answer (this becomes a rule for the AI)">${esc(q.answer || "")}</textarea>
          <div class="qa-actions"><button class="btn btn-sm btn-primary" data-save-q="${q.id}">${q.answer ? "Update answer" : "Save answer"}</button>
            <button class="btn btn-sm btn-ghost" data-dismiss-q="${q.id}">Not relevant</button>
            ${q.answer ? `<span class="hint">Answered${q.answered_by ? " by " + esc(q.answered_by) : ""}</span>` : ""}</div>
        </div>`).join("") : `<p class="hint">Questions appear here after learning.</p>`}
    </section>

    <section class="train-sec">
      <h3>SOPs</h3>
      ${d.sops.map((x) => `<details class="sop"><summary>${esc(x.title)}</summary>
          <input class="control" data-sop-title="${x.id}" value="${esc(x.title)}">
          <textarea class="control" rows="10" data-sop-body="${x.id}">${esc(x.body)}</textarea>
          <div class="qa-actions"><button class="btn btn-sm btn-primary" data-sop-save="${x.id}">Save</button>
          <button class="btn btn-sm btn-danger" data-sop-del="${x.id}">Delete</button></div></details>`).join("")}
      <details class="advanced"${d.sops.length ? "" : " open"}><summary>Add an SOP</summary>
        <label class="field"><span>Title</span><input id="sopTitle" class="control" placeholder="Refunds and returns"></label>
        <label class="field"><span>Text</span><textarea id="sopBody" class="control" rows="8" placeholder="Paste the SOP here"></textarea></label>
        <button class="btn btn-primary" id="sopAdd">Add SOP</button>
        <label class="btn btn-ghost">Upload a PDF or text file<input type="file" id="sopFile" accept=".pdf,.txt,.md" hidden></label>
      </details>
    </section>

    <section class="train-sec">
      <h3>Playbook ${d.playbook ? "" : `<span class="hint">(written after learning)</span>`}</h3>
      ${d.playbook ? `<p class="hint">Written by the AI from your past conversations. You can correct it; your SOPs and answers always win.</p>
        <textarea id="playbook" class="control playbook" rows="22">${esc(d.playbook)}</textarea>
        <button class="btn btn-primary" id="pbSave">Save playbook</button>` : ""}
    </section>
  </div>`;

  const send = (path, data, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  $("trLearn").onclick = async () => {
    try { await send("/api/ai-training/learn", { days: +$("trDays").value, limit: +$("trLimit").value }); toast("Learning started."); pollTraining(); }
    catch (e) { toast(e.message, true); }
  };
  for (const b of $("read").querySelectorAll("[data-save-q]")) b.onclick = async () => {
    const id = b.dataset.saveQ;
    try { await send(`/api/ai-training/questions/${id}`, { answer: $("read").querySelector(`[data-q="${id}"]`).value }); toast("Saved. The AI will follow this."); loadTraining(); loadCounts(); }
    catch (e) { toast(e.message, true); }
  };
  for (const b of $("read").querySelectorAll("[data-dismiss-q]")) b.onclick = async () => {
    await send(`/api/ai-training/questions/${b.dataset.dismissQ}`, { dismiss: true }); loadTraining(); loadCounts();
  };
  for (const b of $("read").querySelectorAll("[data-sop-save]")) b.onclick = async () => {
    const id = b.dataset.sopSave;
    await send(`/api/ai-training/sops/${id}`, { title: $("read").querySelector(`[data-sop-title="${id}"]`).value,
      body: $("read").querySelector(`[data-sop-body="${id}"]`).value }, "PUT");
    toast("SOP saved."); loadTraining();
  };
  for (const b of $("read").querySelectorAll("[data-sop-del]")) b.onclick = async () => {
    if (!confirm("Delete this SOP?")) return;
    await api(`/api/ai-training/sops/${b.dataset.sopDel}`, { method: "DELETE" }); loadTraining();
  };
  $("sopAdd").onclick = async () => {
    try { await send("/api/ai-training/sops", { title: $("sopTitle").value, body: $("sopBody").value }); toast("SOP added."); loadTraining(); }
    catch (e) { toast(e.message, true); }
  };
  $("sopFile").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try { await api(`/api/ai-training/sops?filename=${encodeURIComponent(f.name)}`, { method: "POST", body: await f.arrayBuffer() }); toast("SOP added from file."); loadTraining(); }
    catch (err) { toast(err.message, true); }
  };
  if ($("pbSave")) $("pbSave").onclick = async () => {
    await send("/api/ai-training/playbook", { playbook: $("playbook").value }, "PUT"); toast("Playbook saved.");
  };
  if (j.running) pollTraining();
}

let pollTimer;
function pollTraining() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (view !== "training") return;
    const d = await api("/api/ai-training").catch(() => null);
    if (!d) return;
    if (d.job.running) {
      const box = $("read").querySelector(".notice");
      if (box) box.textContent = `${d.job.step || "Working…"} ${d.job.total ? `(${d.job.done}/${d.job.total})` : ""}`;
      pollTraining();
    } else { loadTraining(); loadCounts(); }
  }, 4000);
}

function refresh() { loadCounts(); if (view !== "training") loadList(); }

for (const b of $("nav").querySelectorAll("[data-view]")) {
  b.title = [...b.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
  b.onclick = () => {
    view = b.dataset.view;
    for (const x of $("nav").querySelectorAll("[data-view]")) x.classList.toggle("on", x === b);
    document.querySelector(".inbox3").classList.toggle("training", view === "training");
    if (b.dataset.restoring) delete b.dataset.restoring;
    else PDUrl.set({ ticket: null, mail: null });
    if (view === "training") { current = null; PDUrl.set({ view: "training" }); loadTraining(); return; }
    loadList();
  };
}
$("refresh").onclick = refresh;
$("mailboxSelect").onchange = loadList;

(async function boot() {
  try {
    const r = await fetch("/api/mail-accounts");
    const opts = r.ok ? (await r.json()).accounts : [];
    $("mailboxSelect").innerHTML = `<option value="">All stores</option>` +
      opts.map((a) => `<option value="${a.id}">${esc(a.store || a.address)}</option>`).join("");
    $("mailboxSelect").hidden = !opts.length;
  } catch { $("mailboxSelect").hidden = true; }
  // Back to the view and email from before a refresh
  const box = PDUrl.get("box");
  if (box && [...$("mailboxSelect").options].some((o) => o.value === box)) $("mailboxSelect").value = box;
  const t = PDUrl.get("ticket"), m = PDUrl.get("mail");
  const vb = $("nav").querySelector(`[data-view="${PDUrl.get("view")}"]`);
  if (vb) { vb.dataset.restoring = "1"; vb.click(); loadCounts(); } else refresh();
  if (t) openTicket(+t); else if (m) openMail(+m);
  setInterval(refresh, 60000);
})();
