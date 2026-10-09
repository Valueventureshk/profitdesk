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
    ${m.attachments.length ? `<div class="mail-att">${m.attachments.map((a) => `📎 ${esc(a.name)}`).join(" · ")}</div>` : ""}
    <div class="mail-body">${esc(m.text || "(empty)")}</div>`;
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
  mark();
  let d;
  try { d = await api(`/api/inbox/tickets/${id}`); } catch (e) { $("read").innerHTML = esc(e.message); return; }
  const t = d.ticket;
  const labels = (t.labels || "").split(",").filter(Boolean);
  const snoozed = t.snoozed_until && new Date(t.snoozed_until) > new Date();
  const thread = d.messages.map((m) => `
    <div class="msg ${m.direction === "out" ? "out" : ""}">
      <div class="msg-head"><strong>${m.direction === "out" ? "You" : esc(m.from_name || m.from_addr)}</strong>
        <span>${full(m.date)}</span></div>
      <div class="mail-body">${esc(m.text || "(empty)")}</div>
      ${m.attachments.length ? `<div class="mail-att">${m.attachments.map((a) => `📎 ${esc(a.name)}`).join(" · ")}</div>` : ""}
    </div>`).join("");
  $("read").innerHTML = `
    <div class="tk-layout">
      <div class="tk-main">
        <h2 class="mail-h">${esc(t.subject || "(no subject)")}</h2>
        <div class="mail-meta"><strong>${esc(t.customer_name || "")}</strong> &lt;${esc(t.customer_email)}&gt;
          <span>${esc(t.store)} · ${esc(t.mailbox)} · ${d.messages.length} emails</span></div>
        ${t.summary ? `<div class="tk-summary"><b>AI summary</b> ${esc(t.summary)}${t.orders ? ` · orders ${esc(t.orders)}` : ""}</div>` : ""}
        ${thread}
        <div class="reply-box">
          <p class="hint">Replying from ProfitDesk comes next. For now reply in Gmail; your reply shows up here
            within 2 minutes and the ticket switches to "We replied last".</p>
          ${t.gmail_link ? `<a class="btn btn-ghost" href="${esc(t.gmail_link)}" target="_blank" rel="noopener">Open in Gmail ↗</a>` : ""}
        </div>
      </div>
      <aside class="tk-side">
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
          .then(() => { toast("Removed."); current = null; $("read").innerHTML = ""; refresh(); })
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

function refresh() { loadCounts(); loadList(); }

for (const b of $("nav").querySelectorAll("[data-view]")) {
  b.onclick = () => {
    view = b.dataset.view;
    for (const x of $("nav").querySelectorAll("[data-view]")) x.classList.toggle("on", x === b);
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
  refresh();
  setInterval(refresh, 60000);
})();
