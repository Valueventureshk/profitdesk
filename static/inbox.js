/* ProfitDesk Inbox: every store's support emails in one list (read-only for now). */

const $ = (id) => document.getElementById(id);
let messages = [];
let current = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path) {
  const r = await fetch(path);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/inbox"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

function when(iso) {
  const d = new Date(iso);
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

async function load() {
  const acct = $("mailboxSelect").value;
  try { messages = (await api(`/api/inbox/messages${acct ? `?account=${acct}` : ""}`)).messages; }
  catch (e) { $("sub").textContent = e.message; return; }
  $("sub").textContent = `${messages.length} emails`;
  $("list").innerHTML = messages.length ? messages.map((m) => `
    <button class="mail-row${m.id === current ? " on" : ""}" data-id="${m.id}">
      <span class="mail-top"><strong>${esc(m.from_name || m.from_addr)}</strong><small>${when(m.date)}</small></span>
      <span class="mail-subj">${esc(m.subject || "(no subject)")}</span>
      <span class="mail-snip">${esc(m.snippet || "")}</span>
      <span class="mail-store">${esc(m.store || m.address)}${m.attachments ? ` · 📎 ${m.attachments}` : ""}</span>
    </button>`).join("") : `<p class="hint inbox-empty">No emails yet. Connect a mailbox in Settings → Support mailboxes.</p>`;
  for (const b of $("list").querySelectorAll("[data-id]")) b.onclick = () => open(+b.dataset.id);
}

async function open(id) {
  current = id;
  for (const b of $("list").querySelectorAll("[data-id]")) b.classList.toggle("on", +b.dataset.id === id);
  let m;
  try { m = await api(`/api/inbox/messages/${id}`); } catch (e) { $("read").innerHTML = esc(e.message); return; }
  $("read").innerHTML = `
    <h2 class="mail-h">${esc(m.subject || "(no subject)")}</h2>
    <div class="mail-meta"><strong>${esc(m.from_name || m.from_addr)}</strong> &lt;${esc(m.from_addr)}&gt;
      <span>to ${esc(m.to_addr)} · ${esc(m.store || m.address)} ·
      ${new Date(m.date).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</span></div>
    ${m.attachments.length ? `<div class="mail-att">${m.attachments.map((a) => `📎 ${esc(a.name)}`).join(" · ")}</div>` : ""}
    <div class="mail-body">${esc(m.text || "(empty)")}</div>`;
}

$("refresh").onclick = load;
$("mailboxSelect").onchange = load;

(async function boot() {
  try {
    const r = await fetch("/api/mail-accounts");
    const opts = r.ok ? (await r.json()).accounts : [];
    $("mailboxSelect").innerHTML = `<option value="">All mailboxes</option>` +
      opts.map((a) => `<option value="${a.id}">${esc(a.store || a.address)}</option>`).join("");
    $("mailboxSelect").hidden = !opts.length;
  } catch { $("mailboxSelect").hidden = true; }
  load();
  setInterval(load, 120000);
})();
