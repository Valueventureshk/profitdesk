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
  if (!r.ok) { const e = new Error(body.error || `Request failed (${r.status})`); e.status = r.status; throw e; }
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
    const q = ids.filter((id) => String(id) === String(trMeta.first) && splitQuote(trOriginal.get(String(id)) || "").quote);
    const d = await api(`/api/inbox/translate?ids=${ids.join(",")}&lang=${encodeURIComponent(lang)}&quotes=${q.join(",")}`);
    for (const id of ids) {
      const box = document.querySelector(`[data-mid="${id}"]`), t = d.translations[String(id)];
      if (!box || t === undefined) continue;
      // translated new part + the original quoted history (its own email above gets translated too)
      const orig = splitQuote(trOriginal.get(String(id)) || "");
      const qt = (d.quotes || {})[String(id)];
      box.dataset.qdone = qt ? "1" : "";
      box.querySelector(".msg-text").innerHTML = `<div class="mail-body">${body(splitQuote(t).main)}</div>` +
        (orig.quote ? quoteBox(orig.head, qt || orig.quote, !!qt) : "");
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

// ---- Reply box (tickets and plain emails share it)
function composerHtml(o) {
  return `<div class="reply-box" data-write-only>
    <textarea id="tkReply" class="control reply-text" rows="7" placeholder="Write your reply to ${esc(o.name)}…  (Ctrl/⌘ + Enter sends)"></textarea>
    <div class="reply-bar">
      ${o.draft ? `<button class="btn btn-sm btn-ghost" id="tkDraft">✦ Draft with AI</button>` : ""}
      <span class="reply-tr"><button class="btn btn-sm btn-ghost" id="tkTr" title="Translate what's in the box">🌐 Translate to</button><select class="control" id="tkTrLang">${TR_LANGS.map((l) => `<option${l === o.lang ? " selected" : ""}>${l}</option>`).join("")}</select></span>
      <span class="reply-gap"></span>
      ${o.gmail ? `<a class="btn btn-sm btn-ghost" href="${esc(o.gmail)}" target="_blank" rel="noopener">Open in Gmail ↗</a>` : ""}
      <button class="btn btn-primary" id="tkSend"${o.canSend ? "" : " disabled"}>Send</button>
    </div>
    <p class="hint reply-note">${o.canSend ? `Sends from <b>${esc(o.mailbox)}</b>. It shows in Gmail too (Sent, same conversation).`
      : `To send from here, sign <b>${esc(o.mailbox)}</b> in once: Settings → Support mailboxes → <b>Sign in to send</b>. Until then, copy your reply into Gmail.`}</p>
  </div>`;
}
function wireComposer(o) {   // o: {key, sendUrl, draftUrl, onSent}
  const box = $("tkReply");
  if (!box) return;
  try { box.value = sessionStorage.getItem(o.key) || ""; } catch { /* */ }
  box.oninput = () => { try { sessionStorage.setItem(o.key, box.value); } catch { /* */ } };
  box.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $("tkSend").click(); } };
  if ($("tkDraft") && o.draftUrl) $("tkDraft").onclick = async () => {
    const b = $("tkDraft");
    if (box.value.trim() && !confirm("Replace what's in the reply box with an AI draft?")) return;
    b.disabled = true; b.textContent = "Writing…";
    try { const r = await post(o.draftUrl, {}); box.value = r.draft; box.oninput(); box.focus(); }
    catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "✦ Draft again"; }
  };
  $("tkTr").onclick = async () => {
    if (!box.value.trim()) return toast("Write the reply first.", true);
    const b = $("tkTr");
    b.disabled = true; b.textContent = "Translating…";
    try {
      const r = await post("/api/inbox/translate-text", { text: box.value, lang: $("tkTrLang").value });
      box.value = r.text; box.oninput();
      toast(`Translated into ${$("tkTrLang").value}. Check it, then Send.`);
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "🌐 Translate to"; }
  };
  $("tkSend").onclick = async () => {
    const text = box.value.trim();
    if (!text) return toast("Write the reply first.", true);
    const b = $("tkSend");
    b.disabled = true; b.textContent = "Sending…";
    try {
      try { await post(o.sendUrl, { text, seen: o.seen, opened: o.opened }); }
      catch (e) {
        if (e.status !== 409) throw e;
        if (!confirm(`${e.message}\n\nRead the conversation first? Press Cancel to check it, or OK to send your reply anyway.`)) {
          if (o.onConflict) o.onConflict();
          return;
        }
        await post(o.sendUrl, { text, force: true });
      }
      try { sessionStorage.removeItem(o.key); } catch { /* */ }
      box.value = "";
      box.closest(".reply-box").insertAdjacentHTML("beforebegin", `<div class="msg out sent-now">
        <div class="msg-head"><strong>You</strong><span>Sent just now ✓</span></div>
        <div class="msg-text"><div class="mail-body">${body(text)}</div></div></div>`);
      toast("Sent. It's in Gmail's Sent folder too.");
      if (o.onSent) setTimeout(o.onSent, 12000);
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "Send"; }
  };
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
let trMeta = { mailbox: "", store: "", first: null };   // the open conversation: our address, store, oldest email
function quoteWho(head) {
  const h = (head || "").toLowerCase();
  const ours = (trMeta.mailbox && h.includes(trMeta.mailbox.toLowerCase())) ||
    (trMeta.store && h.includes(trMeta.store.toLowerCase().slice(0, 12)));
  return ours ? "Your team's earlier reply" : head ? "Customer's earlier email" : "Earlier message";
}
function quoteBox(head, quote, translated) {
  return `<div class="mail-quote${translated ? " tr" : ""}" title="Click to show or hide the earlier message">
      <div class="mail-quote-head">↩ <b>${quoteWho(head)}</b>${head ? ` · ${esc(head)}` : ""}${translated ? ` <span class="tr-tag">translated</span>` : ""}</div>
      <div class="mail-body">${body(quote)}</div></div>`;
}
function mailText(text) {
  const { main, quote, head } = splitQuote(text);
  return `<div class="mail-body">${body(main)}</div>` + (quote ? quoteBox(head, quote) : "");
}
document.addEventListener("click", async (e) => {
  const q = e.target.closest(".mail-quote");
  if (!q || e.target.closest("a")) return;
  q.classList.toggle("open");
  const box = q.closest("[data-mid]");
  if (!q.classList.contains("open") || !box || !box.classList.contains("translated") || box.dataset.qdone) return;
  const id = box.dataset.mid, lang = box.dataset.lang || "English";
  box.dataset.qdone = "1";
  q.querySelector(".mail-quote-head").insertAdjacentHTML("beforeend", ` <span class="tr-tag">translating…</span>`);
  try {
    const d = await api(`/api/inbox/translate?quotes=${id}&lang=${encodeURIComponent(lang)}`);
    const t = (d.quotes || {})[id], orig = splitQuote(trOriginal.get(String(id)) || "");
    if (t) { const fresh = document.createElement("div"); fresh.innerHTML = quoteBox(orig.head, t, true); fresh.firstElementChild.classList.add("open"); q.replaceWith(fresh.firstElementChild); }
    else q.querySelector(".tr-tag")?.remove();
  } catch (err) { box.dataset.qdone = ""; q.querySelector(".tr-tag")?.remove(); toast(err.message, true); }
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

// ---- Attachments: photos as thumbnails, other files as cards (fetched from Gmail on first open)
function attGrid(m) {
  const list = m.attachments || [];
  if (!list.length) return "";
  const size = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
  const icon = (t, n) => (/pdf/.test(t) ? "PDF" : /sheet|excel|csv/.test(t + n) ? "XLS" : /word|document/.test(t) ? "DOC"
    : /zip|compressed/.test(t) ? "ZIP" : /video/.test(t) ? "VID" : (n.split(".").pop() || "FILE").slice(0, 4).toUpperCase());
  return `<div class="att-grid">${list.map((a, i) => {
    const url = `/api/inbox/attachments/${m.id}/${i}`;
    const img = /^image\//.test(a.type || "") || /\.(heic|heif)$/i.test(a.name || "");
    const heic = /heic|heif/i.test((a.type || "") + (a.name || ""));
    return `<div class="att${img ? " att-img" : ""}">
      ${img ? `<a href="${url}${heic ? "?jpeg=1" : ""}" target="_blank" rel="noopener" title="Open full size"><img src="${url}?thumb=1" alt="${esc(a.name)}" loading="lazy" onerror="this.parentNode.classList.add('att-noprev');this.remove()"></a>`
            : `<a class="att-ico" href="${url}${/pdf/.test(a.type || "") ? "" : "?download=1"}" target="_blank" rel="noopener">${icon(a.type || "", a.name || "")}</a>`}
      <div class="att-meta"><span title="${esc(a.name)}">${esc(a.name)}</span><small>${a.size ? size(a.size) : ""}</small>
        <a href="${url}?download=1" title="Download">⬇</a></div></div>`;
  }).join("")}</div>`;
}

const tagChips = (tags) => (tags || []).map((g) =>
  `<span class="tg-chip" style="--tg:${esc(g.color)}">${esc(g.name)}</span>`).join("");
const mailRow = (m) => `
      <button class="mail-row${current?.type === "mail" && current.id === m.id ? " on" : ""}" data-mail="${m.id}">
        <span class="mail-top"><strong>${esc(m.from_name || m.from_addr)}</strong><small>${when(m.date)}</small></span>
        <span class="mail-subj">${esc(m.subject || "(no subject)")}</span>
        <span class="mail-snip">${esc(m.snippet || "")}</span>
        ${m.tags && m.tags.length ? `<span class="mail-tags">${tagChips(m.tags)}</span>` : ""}
        <span class="mail-store">${esc(m.store || m.address)}${m.category ? ` · ${esc(m.category)}` : " · not sorted"}${m.attachments ? ` · 📎 ${m.attachments}` : ""}</span>
      </button>`;
const ticketRow = (t) => `
      <button class="mail-row${current?.type === "ticket" && current.id === t.id ? " on" : ""}" data-ticket="${t.id}">
        <span class="mail-top"><strong>${esc(t.customer_name || t.customer_email)}</strong>
          <small>${when(t.status === "closed" ? t.closed_at : t.last_message_at)}</small></span>
        <span class="mail-subj">${esc(t.summary || t.subject || "(no subject)")}</span>
        <span class="mail-tags">${chips(t)}${tagChips(t.tags)}<span class="mail-store">${esc(t.store)} · ${t.emails} email${t.emails === 1 ? "" : "s"}</span></span>
      </button>`;

async function loadList() {
  const acct = $("mailboxSelect").value;
  PDUrl.set({ view: view === "scm" ? null : view, box: acct });
  const q = acct ? `&account=${acct}` : "";
  const empty = `<p class="hint inbox-empty">Nothing here.</p>`;
  if (view.startsWith("label:")) {
    const label = encodeURIComponent(view.slice(6));
    let tk, ms;
    try {
      [tk, ms] = await Promise.all([api(`/api/inbox/tickets?view=label&label=${label}${q}`), api(`/api/inbox/messages?view=label&label=${label}${q}`)]);
    } catch (e) { $("list").innerHTML = esc(e.message); return; }
    $("list").innerHTML = (tk.tickets.map(ticketRow).join("") +
      (ms.messages.length ? `<div class="list-sep">Emails (no ticket)</div>` + ms.messages.map(mailRow).join("") : "")) || empty;
  } else if (MAIL_VIEWS.has(view)) {
    let d;
    try { d = await api(`/api/inbox/messages?view=${view}${q}`); } catch (e) { $("list").innerHTML = esc(e.message); return; }
    $("list").innerHTML = d.messages.map(mailRow).join("") || empty;
  } else {
    let d;
    try { d = await api(`/api/inbox/tickets?view=${view}${q}`); } catch (e) { $("list").innerHTML = esc(e.message); return; }
    $("list").innerHTML = d.tickets.map(ticketRow).join("") || empty;
  }
  for (const b of $("list").querySelectorAll("[data-mail]")) b.onclick = () => openMail(+b.dataset.mail);
  for (const b of $("list").querySelectorAll("[data-ticket]")) b.onclick = () => openTicket(+b.dataset.ticket);
}

// ---- Labels (Gmail labels, two-way): the left menu section and the picker on tickets/emails
async function loadLabels() {
  const acct = $("mailboxSelect").value;
  let d;
  try { d = await api(`/api/inbox/labels${acct ? `?account=${acct}` : ""}`); } catch { return; }
  $("labelNav").innerHTML = d.labels.length ? `<div class="inav-label">Labels</div>` + d.labels.map((l) => `
    <button data-view="label:${esc(l.name)}" class="lbl${view === "label:" + l.name ? " on" : ""}" title="${esc(l.name)}"><i class="tg-dot" style="--tg:${esc(l.color)}"></i><span class="lbl-name">${esc(l.name)}</span><b>${l.open || ""}</b></button>`).join("") : "";
}

let tagCtx = null;    // {target: {ticket_id}|{message_id}, tags, suggest, all}
function renderTagBox() {
  const c = tagCtx, el = $("tgBox");
  if (!c || !el) return;
  el.innerHTML = `
    <div class="tg-list">${c.tags.map((g) => `<span class="tg-chip" style="--tg:${esc(g.color)}">${esc(g.name)}<button data-tg-del="${esc(g.name)}" data-write-only title="Remove label">×</button></span>`).join("") || `<span class="hint">No labels</span>`}</div>
    ${c.suggest.length ? `<div class="tg-sug" data-write-only><span>✦ Suggested:</span>${c.suggest.map((g) => `<button class="tg-chip sug" style="--tg:${esc(g.color)}" data-tg-add="${esc(g.name)}" title="Add this label">+ ${esc(g.name)}</button>`).join("")}</div>` : ""}
    <div class="tg-add" data-write-only>
      <input class="control" id="tgSearch" placeholder="+ Add label…" autocomplete="off">
      <div class="tg-menu" id="tgMenu" hidden></div>
    </div>`;
  const search = $("tgSearch"), menu = $("tgMenu");
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const have = new Set(c.tags.map((g) => g.name));
    const opts = c.all.filter((g) => !have.has(g.name) && g.name.toLowerCase().includes(q)).slice(0, 40);
    const exact = c.all.some((g) => g.name.toLowerCase() === q);
    menu.innerHTML = opts.map((g) => `<button data-tg-add="${esc(g.name)}"><i class="tg-dot" style="--tg:${esc(g.color)}"></i>${esc(g.name)}</button>`).join("")
      + (q && !exact ? `<button data-tg-add="${esc(search.value.trim())}" class="tg-new">Create label “${esc(search.value.trim())}”</button>` : "")
      || `<span class="hint">No more labels</span>`;
    menu.hidden = false;
  };
  search.onfocus = draw;
  search.oninput = draw;
  search.onkeydown = (e) => { if (e.key === "Enter") { const b = menu.querySelector("[data-tg-add]"); if (b) b.click(); } if (e.key === "Escape") menu.hidden = true; };
  search.onblur = () => setTimeout(() => { menu.hidden = true; }, 200);
  el.onclick = async (e) => {
    const add = e.target.closest("[data-tg-add]"), del = e.target.closest("[data-tg-del]");
    if (!add && !del) return;
    const name = (add || del).dataset.tgAdd || (add || del).dataset.tgDel;
    (add || del).disabled = true;
    try {
      const r = await post("/api/inbox/labels", { action: add ? "add" : "remove", name, ...c.target });
      const color = (n) => (c.all.find((g) => g.name === n) || c.suggest.find((g) => g.name === n) || {}).color || "#64748B";
      if (add && !c.all.some((g) => g.name === name)) c.all.push({ name, color: "#64748B" });
      c.tags = r.tags.map((n) => ({ name: n, color: color(n) }));
      c.suggest = c.suggest.filter((g) => g.name !== name);
      renderTagBox();
      toast(add ? `Labelled “${name}” (in Gmail too).` : `Removed “${name}” (in Gmail too).`);
      loadLabels();
      loadList();
    } catch (err) { toast(err.message, true); (add || del).disabled = false; }
  };
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
  trMeta = { mailbox: m.address || m.to_addr || "", store: m.store || "", first: m.id };
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
    <div class="tg-box tg-inline" id="tgBox"></div>
    ${trBar()}
    ${attGrid(m)}
    <div class="mail-one" data-mid="${m.id}"><div class="tr-onebar">${trButton(m.id)}</div><div class="msg-text">${mailText(m.text)}</div></div>
    ${composerHtml({ name: m.from_name || m.from_addr, draft: false, lang: m.reply_lang, canSend: m.can_send, mailbox: m.mailbox })}`;
  wireComposer({ key: `pdReplyMail:${id}`, sendUrl: `/api/inbox/messages/${id}/reply` });
  trOriginal = new Map([[String(m.id), m.text]]);
  tagCtx = { target: { message_id: m.id }, tags: m.tags || [], suggest: m.tag_suggest || [], all: m.all_tags || [] };
  renderTagBox();
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
  trMeta = { mailbox: t.mailbox || "", store: t.store || "", first: d.messages[0]?.id };
  const thread = d.messages.map((m) => `
    <div class="msg ${m.direction === "out" ? "out" : "in"}" data-mid="${m.id}">
      <div class="msg-head"><strong>${m.direction === "out" ? "You" : esc(m.from_name || m.from_addr)}</strong>
        <span>${trButton(m.id)} ${full(m.date)}</span></div>
      <div class="msg-text">${mailText(m.text)}</div>
      ${attGrid(m)}
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
        ${composerHtml({ name: t.customer_name || t.customer_email, draft: true, lang: t.reply_lang, gmail: t.gmail_link,
                         canSend: t.can_send, mailbox: t.mailbox })}
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
          <div class="tk-side-label">Labels</div>
          <div class="tg-box" id="tgBox"></div>
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
  tagCtx = { target: { ticket_id: id }, tags: t.tags || [], suggest: t.tag_suggest || [], all: t.all_tags || [] };
  renderTagBox();
  wireTranslate();
  wireComposer({ key: `pdReply:${id}`, sendUrl: `/api/inbox/tickets/${id}/reply`, draftUrl: `/api/inbox/tickets/${id}/draft`,
                  seen: Math.max(0, ...d.messages.map((m) => m.id)), opened: Date.now() / 1000,
                  onConflict: () => openTicket(id),
                  onSent: () => { if (current && current.type === "ticket" && current.id === id) { openTicket(id); refresh(); } } });
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

function refresh() { loadCounts(); loadLabels(); if (view !== "training") loadList(); }

for (const b of $("nav").querySelectorAll("[data-view]")) {
  b.title = [...b.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
}
$("nav").addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (!b) return;
  view = b.dataset.view;
  for (const x of $("nav").querySelectorAll("[data-view]")) x.classList.toggle("on", x === b);
  document.querySelector(".inbox3").classList.toggle("training", view === "training");
  if (b.dataset.restoring) delete b.dataset.restoring;
  else PDUrl.set({ ticket: null, mail: null });
  if (view === "training") { current = null; PDUrl.set({ view: "training" }); loadTraining(); return; }
  loadList();
});
$("refresh").onclick = refresh;
$("mailboxSelect").onchange = () => { loadList(); loadLabels(); };

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
  if ((PDUrl.get("view") || "").startsWith("label:")) {
    view = PDUrl.get("view");
    for (const x of $("nav").querySelectorAll("[data-view]")) x.classList.remove("on");
    refresh();
  } else if (vb) { vb.dataset.restoring = "1"; vb.click(); loadCounts(); loadLabels(); } else refresh();
  if (t) openTicket(+t); else if (m) openMail(+m);
  setInterval(refresh, 60000);
})();
