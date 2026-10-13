/* The chat button (bottom right on every desk). The server's assistant answers
   with the app's own figures; this file only draws the conversation. */
(function () {
  if (document.querySelector(".pd-chat-btn")) return;
  const KEY = "pdChat";
  let history = [];
  try { history = JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch { history = []; }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Just enough Markdown for answers: links, bold, lists and small tables.
  function md(text) {
    const lines = esc(text).split("\n");
    let html = "", table = [];
    const flush = () => {
      if (!table.length) return;
      const rows = table.filter((r) => !/^\|\s*:?-{2,}/.test(r)).map((r) =>
        r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      html += `<table>${rows.map((r, i) => `<tr>${r.map((c) => i ? `<td>${c}</td>` : `<th>${c}</th>`).join("")}</tr>`).join("")}</table>`;
      table = [];
    };
    for (const l of lines) {
      if (l.trim().startsWith("|")) { table.push(l.trim()); continue; }
      flush();
      if (/^\s*[-*] /.test(l)) html += `<div class="li">• ${l.replace(/^\s*[-*] /, "")}</div>`;
      else if (l.trim()) html += `<p>${l}</p>`;
    }
    flush();
    return html
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/\[([^\]]+)\]\(((?:\/api\/reports\/\d+\/download)|https?:\/\/[^)\s]+)\)/g,
               '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  const btn = document.createElement("button");
  btn.className = "pd-chat-btn";
  btn.title = "Ask EcomVital";
  btn.innerHTML = "✦ Ask";
  const box = document.createElement("div");
  box.className = "pd-chat";
  box.hidden = true;
  box.innerHTML = `
    <div class="pd-chat-head"><strong>Ask EcomVital</strong>
      <span><button class="pd-chat-x" data-clear title="New conversation">↺</button>
      <button class="pd-chat-x" data-close title="Close">✕</button></span></div>
    <div class="pd-chat-log"></div>
    <form class="pd-chat-form"><textarea rows="2" placeholder="e.g. Profit by store last week · cash in 30 days · COG report for September"></textarea>
      <button class="btn btn-primary btn-sm" type="submit">Send</button></form>`;
  document.body.append(btn, box);
  const log = box.querySelector(".pd-chat-log");
  const input = box.querySelector("textarea");

  function draw(pending) {
    log.innerHTML = (history.length ? history : [{ role: "assistant",
      content: "Ask me about profit, cash flow, COG, the support inbox, store health, live tracking, Google Ads, or ask for a report. You can also say \"every day at 5pm send me a performance report\" or \"tell me when Meta brings 20+ adds to cart in an hour\"." }])
      .map((m) => `<div class="pd-msg ${m.role}">${m.role === "user" ? esc(m.content) : md(m.content)}
        ${(m.links || []).map((l) => `<a class="btn btn-sm btn-ghost pd-dl" href="${esc(l.url)}">⬇ ${esc(l.title)}</a>`).join("")}
        ${(m.actions || []).map((a) => `<div class="pd-act${a.done ? " done" : ""}"><b>${esc(a.title)}</b><span>${esc(a.detail)}</span>
          ${a.done ? `<em>${esc(a.done)}</em>` : `<span class="pd-act-btns"><button class="btn btn-sm btn-primary" data-act-ok="${esc(a.id)}">Confirm</button>
            <button class="btn btn-sm btn-ghost" data-act-no="${esc(a.id)}">Cancel</button></span>`}</div>`).join("")}</div>`)
      .join("") + (pending ? `<div class="pd-msg assistant pd-wait">Thinking…</div>` : "");
    log.scrollTop = log.scrollHeight;
    for (const b of log.querySelectorAll("[data-act-ok],[data-act-no]")) b.onclick = () => decide(b);
  }
  // The assistant only proposes; nothing changes until the person clicks Confirm here.
  async function decide(b) {
    const id = b.dataset.actOk || b.dataset.actNo, ok = !!b.dataset.actOk;
    const msg = history.find((m) => (m.actions || []).some((a) => a.id === id));
    const a = msg && msg.actions.find((x) => x.id === id);
    b.closest(".pd-act-btns").innerHTML = `<em>${ok ? "Working…" : "Cancelling…"}</em>`;
    try {
      const r = await fetch(`/api/chat/actions/${encodeURIComponent(id)}`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: ok }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || "That didn't work.");
      if (a) a.done = body.text;
      if (ok) window.dispatchEvent(new Event("pd-changed"));
    } catch (err) { if (a) a.done = `⚠ ${err.message}`; }
    save();
    draw();
  }
  function save() { try { sessionStorage.setItem(KEY, JSON.stringify(history.slice(-20))); } catch { /* ignore */ } }

  // Messages the assistant sent by itself (scheduled reports, alerts): a badge on the button, shown in the chat when opened.
  let inbox = [];
  async function pollInbox() {
    try {
      const r = await fetch("/api/assistant/inbox?unread=1");
      if (!r.ok) return;
      inbox = (await r.json()).messages || [];
      btn.innerHTML = inbox.length ? `✦ Ask <span class="pd-badge">${inbox.length}</span>` : "✦ Ask";
      if (!box.hidden && inbox.length) showInbox();
    } catch { /* offline */ }
  }
  function showInbox() {
    if (!inbox.length) return;
    for (const m of inbox) history.push({ role: "assistant", content: `**${m.title}**\n${m.body}` });
    const ids = inbox.map((m) => m.id);
    inbox = [];
    btn.innerHTML = "✦ Ask";
    fetch("/api/assistant/inbox/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
    save();
    draw();
  }
  pollInbox();
  setInterval(pollInbox, 60000);
  btn.onclick = () => { box.hidden = !box.hidden; if (!box.hidden) { showInbox(); draw(); input.focus(); } };
  box.querySelector("[data-close]").onclick = () => { box.hidden = true; };
  box.querySelector("[data-clear]").onclick = () => { history = []; save(); draw(); };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); box.querySelector("form").requestSubmit(); }
  });
  box.querySelector("form").onsubmit = async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    history.push({ role: "user", content: q });
    draw(true);
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history.map((m) => ({ role: m.role, content: m.content })) }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || "The assistant couldn't answer.");
      history.push({ role: "assistant", content: body.text, links: body.links, actions: body.actions });
    } catch (err) {
      history.push({ role: "assistant", content: `⚠ ${err.message}` });
    }
    save();
    draw();
  };
})();
