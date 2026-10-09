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
  btn.title = "Ask ProfitDesk";
  btn.innerHTML = "✦ Ask";
  const box = document.createElement("div");
  box.className = "pd-chat";
  box.hidden = true;
  box.innerHTML = `
    <div class="pd-chat-head"><strong>Ask ProfitDesk</strong>
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
      content: "Ask me about profit, cash flow, COG, the support inbox, or ask for a report to download." }])
      .map((m) => `<div class="pd-msg ${m.role}">${m.role === "user" ? esc(m.content) : md(m.content)}
        ${(m.links || []).map((l) => `<a class="btn btn-sm btn-ghost pd-dl" href="${esc(l.url)}">⬇ ${esc(l.title)}</a>`).join("")}</div>`)
      .join("") + (pending ? `<div class="pd-msg assistant pd-wait">Thinking…</div>` : "");
    log.scrollTop = log.scrollHeight;
  }
  function save() { try { sessionStorage.setItem(KEY, JSON.stringify(history.slice(-20))); } catch { /* ignore */ } }

  btn.onclick = () => { box.hidden = !box.hidden; if (!box.hidden) { draw(); input.focus(); } };
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
      history.push({ role: "assistant", content: body.text, links: body.links });
    } catch (err) {
      history.push({ role: "assistant", content: `⚠ ${err.message}` });
    }
    save();
    draw();
  };
})();
