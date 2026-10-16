/* Apps → Agent: the ops agent's work log (jobs → steps), advice on any step (becomes a lesson), its questions,
   lessons, and the Slack connection + channels it reads. */

const $ = (id) => document.getElementById(id);
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
async function api(url, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(url, init);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(body.detail || body.error || `Something went wrong (${r.status}).`);
  return body;
}
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
const AREA = { general: "General", invoices: "Invoices", suppliers: "Suppliers", supply_chain: "Supply chain", customers: "Customers", products: "Products" };
const STEP = { read: "📖", think: "💭", act: "✅", wait: "⏳", done: "🏁", error: "⚠️", skip: "↷" };
const JOB = { running: "Running", waiting: "Waiting for you", done: "Done", failed: "Failed" };

const state = { page: PDUrl.get("tab") || "jobs", job: +(PDUrl.get("job") || 0) || null, status: null };

/* ------------------------------------------------ status + banner */

async function loadStatus() {
  try { state.status = await api("/api/agent/status"); } catch (e) { toast(e.message, true); return; }
  const s = state.status;
  $("qBadge").hidden = !s.open_questions;
  $("qBadge").textContent = s.open_questions;
  $("banner").innerHTML = (s.paused ? `<div class="warn ag-paused"><b>The agent is paused:</b> it files nothing until you resume.
      ${window.pdLevel === "manage" ? '<button class="btn btn-sm" data-pause="0">Resume</button>' : ""}</div>`
      : s.connected && window.pdLevel === "manage" ? '<div class="ag-pausebar"><button class="btn btn-sm btn-danger" data-pause="1">Pause the agent</button></div>' : "")
    + (s.connected ? ""
    : `<div class="warn">The agent isn't connected to Slack yet. ${s.owner ? '<a href="#" data-goto="slack">Set it up on the Slack tab</a> (about 5 minutes).' : "Ask the owner to connect it."}</div>`);
}

/* ------------------------------------------------ jobs */

async function loadJobs() {
  const q = new URLSearchParams();
  if ($("fStatus").value) q.set("status", $("fStatus").value);
  if ($("q").value.trim()) q.set("q", $("q").value.trim());
  let r;
  try { r = await api("/api/agent/jobs?" + q); } catch (e) { toast(e.message, true); return; }
  $("jobs").innerHTML = r.jobs.map((j) => `<button class="ag-job${j.id === state.job ? " on" : ""}" data-job="${j.id}">
      <span class="ag-st ${j.status}">${JOB[j.status] || j.status}</span><b>${esc(j.title)}</b>
      <span class="ag-sub">${AREA[j.area] || ""} · ${when(j.updated_at)} · ${j.steps} step${j.steps === 1 ? "" : "s"}${j.advice ? ` · ${j.advice} advice` : ""}</span>
      ${j.summary ? `<span class="ag-sum">${esc(j.summary)}</span>` : ""}</button>`).join("")
    || '<p class="empty">No work yet. Once Slack is connected, “Read the last 90 days” on the Slack tab starts it.</p>';
  if (state.job) loadJob(state.job);
}

async function loadJob(id) {
  state.job = id;
  PDUrl.set({ job: id });
  for (const b of document.querySelectorAll(".ag-job")) b.classList.toggle("on", +b.dataset.job === id);
  let r;
  try { r = await api(`/api/agent/jobs/${id}`); } catch (e) { $("job").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const adv = {};
  for (const a of r.advice) (adv[a.step_id || 0] = adv[a.step_id || 0] || []).push(a);
  const canAdvise = window.pdLevel !== "view";
  const advBox = (stepId) => `${(adv[stepId || 0] || []).map((a) => `<div class="ag-adv"><b>${esc((a.by || "").split("@")[0])}</b> ${esc(a.text)}
      ${a.lesson_id ? '<span class="ag-lesson">→ lesson</span>' : ""}</div>`).join("")}
    ${canAdvise ? `<details class="ag-advise"><summary>Advise</summary>
      <textarea class="control" rows="2" placeholder="What should it do differently, or keep doing?"></textarea>
      <div class="ag-advise-acts"><label><input type="checkbox" checked> Keep as a lesson</label>
        <button class="btn btn-sm btn-primary" data-advise="${stepId || ""}">Send advice</button></div></details>` : ""}`;
  $("job").innerHTML = `<div class="ag-job-head"><span class="ag-st ${r.job.status}">${JOB[r.job.status] || r.job.status}</span>
      <h2>${esc(r.job.title)}</h2><span class="hint">${AREA[r.job.area] || ""} · started ${when(r.job.created_at)}</span>
      ${r.job.summary ? `<p class="ag-sumline">${esc(r.job.summary)}</p>` : ""}</div>
    <ol class="ag-steps">${r.steps.map((s) => `<li class="ag-step ${s.kind}"><span class="ag-ico">${STEP[s.kind] || "•"}</span>
      <div><div class="ag-step-head"><b>${esc(s.title)}</b><time>${when(s.at)}</time></div>
        ${s.detail ? `<p>${esc(s.detail)}</p>` : ""}
        ${s.data && s.data.invoice_id ? `<a class="ag-link" href="/cog?tab=invoices">See it in COG → Invoices ↗</a>` : ""}
        ${advBox(s.id)}</div></li>`).join("") || '<li class="empty">No steps yet…</li>'}</ol>
    ${r.questions.length ? `<div class="ag-jq"><h3>Questions from this job</h3>${r.questions.map((q) => `<p>❓ ${esc(q.question)}${q.answer ? `<br><b>Answer:</b> ${esc(q.answer)}` : ' <a href="#" data-goto="questions">Answer</a>'}</p>`).join("")}</div>` : ""}
    <div class="ag-jobadv"><h3>Advice on the whole job</h3>${advBox(0)}</div>`;
  if (r.job.status === "running") setTimeout(() => { if (state.job === id && state.page === "jobs") loadJob(id); }, 4000);
}

async function sendAdvice(btn) {
  const box = btn.closest(".ag-advise");
  const text = box.querySelector("textarea").value.trim();
  if (!text) return;
  try {
    await api("/api/agent/advice", { json: { job_id: state.job, step_id: btn.dataset.advise ? +btn.dataset.advise : null, text,
      lesson: box.querySelector("input[type=checkbox]").checked } });
    toast("Thanks: it will follow this next time.");
    loadJob(state.job);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------ questions + lessons */

async function loadQuestions() {
  let r;
  try { r = await api("/api/agent/questions?open_only=0"); } catch (e) { toast(e.message, true); return; }
  const canAnswer = window.pdLevel !== "view";
  $("questions").innerHTML = r.questions.map((q) => `<div class="ag-q${q.answer ? " done" : ""}">
      <div class="ag-q-head"><span class="ag-area">${AREA[q.area] || q.area}</span><time>${when(q.asked_at)}</time>
        ${q.job_id ? `<a href="#" data-jobref="${q.job_id}">from job #${q.job_id}</a>` : ""}</div>
      <p class="ag-q-text">${esc(q.question)}</p>
      ${q.context ? `<p class="hint">${esc(q.context)}</p>` : ""}
      ${q.kind === "store" && !q.answer ? storeButtons(q)
        : q.answer ? `<p class="ag-ans"><b>${esc((q.answered_by || "").split("@")[0])}:</b> ${esc(q.answer)}</p>`
        : canAnswer ? `<div class="ag-answer"><textarea class="control" rows="2" placeholder="Your answer (it becomes a lesson)"></textarea>
          <button class="btn btn-sm btn-primary" data-answer="${q.id}">Answer</button></div>` : ""}</div>`).join("")
    || '<p class="empty">No questions yet. When it\'s unsure, it asks here instead of guessing.</p>';
}

function storeButtons(q) {
  let d = {};
  try { d = JSON.parse(q.data || "{}"); } catch { /* none */ }
  const owner = state.status && state.status.owner;
  const files = (d.files || []).slice(0, 4).map(esc).join(", ");
  return `<p class="hint">Files: ${files}${(d.files || []).length > 4 ? " …" : ""}${d.filed ? ` · ${d.filed} already filed before this check` : ""}</p>
    ${owner ? `<div class="ag-yesno"><button class="btn btn-sm btn-primary" data-store="${q.id}" data-inc="1">Yes, include (a store we ran before)</button>
      <button class="btn btn-sm" data-store="${q.id}" data-inc="0">No, not ours</button>
      ${d.filed ? `<label><input type="checkbox" data-rm="${q.id}" checked> and remove the ${d.filed} already filed</label>` : ""}</div>`
      : '<p class="hint">The owner decides.</p>'}`;
}

/* ------------------------------------------------ suppliers + ask */

const money = (v, cur = "USD") => (v === null || v === undefined ? "–" : `${cur} ${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

async function loadSuppliers() {
  $("suppliers").innerHTML = '<p class="empty">Working it out (reads the bank payments; can take a minute)…</p>';
  let r;
  try { r = await api("/api/agent/suppliers"); } catch (e) { $("suppliers").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const proofRow = (p) => `<tr><td>${esc(p.paid_on || "")}</td><td>${money(p.amount, p.currency || "")}</td><td>${esc(p.reference || "")}</td>
    <td>${esc(p.payee || p.supplier || "")}</td><td>${p.matched ? '<span class="ag-st done">In the bank</span>' : '<span class="ag-st waiting">Not found yet</span>'}
      <span class="ag-sub">${esc(p.match_note || "")}</span></td><td><a href="/api/agent/proof/${p.id}" target="_blank" rel="noopener">${esc(p.name)}</a></td></tr>`;
  $("suppliers").innerHTML = r.suppliers.map((s) => `<section class="panel ag-sup">
      <div class="panel-head"><h2>${esc(s.supplier)}</h2><span class="hint">${s.opening_set ? `Opening ${money(s.opening)} on ${esc(s.opening_date)}`
        : 'No opening balance yet: <a href="/cog?tab=suppliers">set it in COG → Suppliers</a>'}</span></div>
      <div class="ag-tiles">${[["Invoiced", s.invoiced], ["Paid (bank)", s.paid], ["Invoiced, unpaid", s.unpaid_invoiced],
        ["Shipped, not invoiced", s.not_invoiced], ["On credit", s.credit]].map(([k, v]) => `<div><span>${k}</span><b>${money(v)}</b></div>`).join("")}</div>
      <div class="ag-pos">
        <p><b>Paid up to:</b> ${s.paid_up_to ? `invoice ${esc(s.paid_up_to.number || "?")} (${esc(s.paid_up_to.date || "")}, ${esc(s.paid_up_to.store || "")}), up to order ${esc(s.paid_up_to.last_order || "?")}` : "nothing paid off yet"}
          ${s.part_paid ? ` · then <b>${esc(s.part_paid.paid)}</b> on invoice ${esc(s.part_paid.number || "?")} (${esc(s.part_paid.date || "")})` : ""}
          ${s.overpaid ? ` · <b>${money(s.overpaid)}</b> paid ahead of the invoices` : ""}</p>
        <p><b>Invoiced up to:</b> ${Object.entries(s.invoiced_up_to).map(([st, v]) => `${esc(st)} order ${esc(v.order)} (${esc(v.date || "")})`).join(" · ") || "no invoices yet"}</p>
        ${s.late_count ? `<p class="hint">${s.late_count} order(s) shipped 14+ days ago aren't on an invoice yet (COG → Suppliers lists them).</p>` : ""}
      </div>
      <details><summary>${s.invoices.length} invoice(s)</summary><div class="table-wrap"><table class="table ag-tbl"><thead><tr><th>Date</th><th>Invoice</th><th>Store</th>
        <th>Orders</th><th>Total</th><th>Paid?</th></tr></thead><tbody>${s.invoices.slice().reverse().map((i) => `<tr><td>${esc(i.date || "")}</td>
        <td>${esc(i.number || "(no number)")}</td><td>${esc(i.store || "")}</td><td>${esc(i.first_order)}${i.last_order && i.last_order !== i.first_order ? ` – ${esc(i.last_order)}` : ""} <span class="ag-sub">${i.orders}</span></td>
        <td>${money(i.total)}</td><td>${esc(i.paid)}</td></tr>`).join("")}</tbody></table></div></details>
      <details${s.proofs.length ? " open" : ""}><summary>${s.proofs.length} bank transfer notice(s) from Slack</summary>${s.proofs.length ? `<div class="table-wrap"><table class="table ag-tbl"><thead><tr><th>Date</th><th>Amount</th>
        <th>Reference</th><th>To</th><th>Bank check</th><th>File</th></tr></thead><tbody>${s.proofs.map(proofRow).join("")}</tbody></table></div>` : '<p class="hint ag-pad">None yet.</p>'}</details>
    </section>`).join("")
    + (r.proofs_unassigned.length ? `<section class="panel ag-sup"><div class="panel-head"><h2>Transfer notices not linked to a supplier</h2></div>
      <div class="table-wrap"><table class="table ag-tbl"><tbody>${r.proofs_unassigned.map(proofRow).join("")}</tbody></table></div></section>` : "")
    || '<p class="empty">No supplier accounts yet.</p>';
}

async function loadChat() {
  let r;
  try { r = await api("/api/agent/chat"); } catch (e) { toast(e.message, true); return; }
  $("chat").innerHTML = r.chat.map((c) => `<div class="ag-msg ${c.role}"><b>${c.role === "user" ? esc((c.who || "").split("@")[0]) : "Agent"}</b>
    <div>${esc(c.content).replace(/\n/g, "<br>")}</div></div>`).join("")
    || `<p class="hint ag-pad">${r.ai ? "Ask about suppliers, invoices, payments or what's in Slack." : "Add the AI key in Settings → AI first."}</p>`;
  $("chat").scrollTop = $("chat").scrollHeight;
}

async function loadLessons() {
  $("lArea").innerHTML = Object.entries(AREA).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  let r;
  try { r = await api("/api/agent/lessons"); } catch (e) { toast(e.message, true); return; }
  const can = window.pdLevel === "manage";
  $("lessons").innerHTML = r.lessons.map((l) => `<div class="ag-lessonrow${l.active ? "" : " off"}">
      <span class="ag-area">${AREA[l.area] || l.area}</span><p>${esc(l.text).replace(/\n/g, "<br>")}</p>
      <span class="ag-sub">${esc((l.created_by || "").split("@")[0])} · ${when(l.created_at)} · ${l.source && l.source.startsWith("advice") ? "from advice" : l.source && l.source.startsWith("answer") ? "from an answer" : esc(l.source || "")}</span>
      ${can ? `<button class="btn btn-sm btn-ghost" data-lesson="${l.id}" data-on="${l.active ? 0 : 1}">${l.active ? "Switch off" : "Switch on"}</button>` : ""}</div>`).join("")
    || '<p class="empty">No lessons yet.</p>';
}

/* ------------------------------------------------ slack */

function renderConnect() {
  const s = state.status;
  if (!s) return;
  if (s.connected) {
    $("connect").innerHTML = `<div class="panel-head"><h2>Slack: connected</h2><span class="hint">${esc(s.team.team || "")} · bot ${esc(s.team.bot_name || "")}</span></div>
      ${s.owner ? `<details class="ag-pad"><summary>Replace the token</summary>${connectForm()}</details>` : ""}`;
  } else if (!s.owner) {
    $("connect").innerHTML = '<div class="panel-head"><h2>Slack isn\'t connected</h2></div><p class="hint ag-pad">The owner connects it.</p>';
  } else {
    $("connect").innerHTML = `<div class="panel-head"><h2>Connect Slack (about 5 minutes)</h2></div>
      <ol class="ag-guide">
        <li>Open <a href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noopener">api.slack.com/apps</a> → <b>Create New App</b> → <b>From a manifest</b> → pick <b>NF Ecom</b>.</li>
        <li>Choose <b>JSON</b>, delete what's there, paste this, then <b>Next → Create</b>:
          <div class="ag-manifest"><button type="button" class="btn btn-sm" id="copyManifest">Copy</button><pre id="manifest">${esc(JSON.stringify(s.manifest, null, 2))}</pre></div></li>
        <li><b>Install to NF Ecom</b> (left menu: Install App) → Allow.</li>
        <li>Copy the <b>Bot User OAuth Token</b> (starts with xoxb-) from <b>OAuth &amp; Permissions</b>, and the <b>Signing Secret</b> from <b>Basic Information</b>. Paste both here:</li>
      </ol>
      ${connectForm()}
      <ol class="ag-guide" start="5">
        <li>Back in Slack's app settings: <b>Event Subscriptions</b> → press <b>Retry</b> next to the Request URL until it says <b>Verified</b> → Save.</li>
        <li>In each channel the agent should read, type <code>/invite @EcomVital Agent</code>. Then press <b>Refresh channels</b> below.</li>
      </ol>`;
    $("copyManifest").onclick = () => { navigator.clipboard.writeText($("manifest").textContent).then(() => toast("Copied")); };
  }
  const go = $("connectGo");
  if (go) go.onclick = async () => {
    go.disabled = true;
    try {
      const r = await api("/api/agent/slack/connect", { json: { token: $("sTok").value, secret: $("sSec").value } });
      toast(`Connected to ${r.team.team}: in ${r.channels} channel(s).`);
      await loadStatus();
      showPage("slack");
    } catch (e) { toast(e.message, true); go.disabled = false; }
  };
}
const connectForm = () => `<div class="ag-connect"><label>Bot User OAuth Token<input class="control" id="sTok" type="password" placeholder="xoxb-…" autocomplete="off"></label>
  <label>Signing Secret<input class="control" id="sSec" type="password" autocomplete="off"></label>
  <button class="btn btn-primary" id="connectGo">Connect</button></div>`;

function renderChannels() {
  const s = state.status;
  const can = window.pdLevel === "manage";
  $("chans").innerHTML = s.channels.length ? `<thead><tr><th>Channel</th><th>What it is</th><th>Who's on the other side</th><th>Messages kept</th>
      <th>Invoices filed</th><th>Last read</th><th></th></tr></thead><tbody>${s.channels.map((c) => `<tr data-chan="${c.id}">
      <td class="name">${c.private ? "🔒" : "#"} ${esc(c.name)}</td>
      <td>${can ? `<select class="control" data-role>${Object.entries(s.roles).map(([k, v]) => `<option value="${k}"${c.role === k ? " selected" : ""}>${v}</option>`).join("")}</select>` : esc(c.role_name)}</td>
      <td>${can ? `<input class="control" data-supplier value="${esc(c.supplier || "")}" placeholder="e.g. Graypop">` : esc(c.supplier || "")}</td>
      <td>${c.messages}</td><td>${c.invoices}</td><td>${when(c.read_at) || "never"}</td>
      <td>${can ? `<button class="btn btn-sm" data-read="${c.id}">Read 90 days</button>` : ""}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="empty">${s.connected ? "The agent isn't in any channel yet: /invite @EcomVital Agent, then Refresh channels." : "Connect Slack first."}</td></tr></tbody>`;
}

/* ------------------------------------------------ page */

function showPage(p) {
  state.page = p;
  PDUrl.set({ tab: p === "jobs" ? null : p });
  for (const b of $("pages").querySelectorAll("button")) b.classList.toggle("on", b.dataset.page === p);
  for (const k of ["jobs", "questions", "suppliers", "ask", "lessons", "slack"]) $(k + "Page").hidden = k !== p;
  if (p === "suppliers") loadSuppliers();
  if (p === "ask") loadChat();
  if (p === "jobs") loadJobs();
  if (p === "questions") loadQuestions();
  if (p === "lessons") loadLessons();
  if (p === "slack") { renderConnect(); renderChannels(); }
}

$("pages").onclick = (e) => { const b = e.target.closest("button[data-page]"); if (b) showPage(b.dataset.page); };
$("fStatus").onchange = loadJobs;
let qt;
$("q").oninput = () => { clearTimeout(qt); qt = setTimeout(loadJobs, 300); };
$("lessonForm").onsubmit = async (e) => {
  e.preventDefault();
  try { await api("/api/agent/lessons", { json: { area: $("lArea").value, text: $("lText").value } }); $("lText").value = ""; loadLessons(); }
  catch (err) { toast(err.message, true); }
};
$("checkProofs").onclick = async () => {
  $("checkProofs").disabled = true;
  try { const r = await api("/api/agent/proofs/check", { json: {} }); toast(`${r.matched} of ${r.proofs} transfer notice(s) newly matched to the bank.`); loadSuppliers(); }
  catch (e) { toast(e.message, true); }
  $("checkProofs").disabled = false;
};
$("askForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("ask").value.trim();
  if (!text) return;
  $("askBtn").disabled = true;
  $("askBtn").textContent = "Thinking…";
  $("chat").insertAdjacentHTML("beforeend", `<div class="ag-msg user"><b>you</b><div>${esc(text)}</div></div>`);
  $("ask").value = "";
  try { await api("/api/agent/chat", { json: { text } }); } catch (err) { toast(err.message, true); }
  $("askBtn").disabled = false;
  $("askBtn").textContent = "Ask";
  loadChat();
};
$("sync").onclick = async () => {
  try { const r = await api("/api/agent/slack/sync", { json: {} }); toast(`In ${r.channels} channel(s).`); await loadStatus(); renderChannels(); }
  catch (e) { toast(e.message, true); }
};
$("undoCount").onclick = async () => {
  try {
    const r = await api(`/api/agent/filed?contains=${encodeURIComponent($("undoText").value.trim())}`);
    $("undoOut").textContent = `${r.files.length} invoice(s) filed from files named like that`;
  } catch (e) { toast(e.message, true); }
};
$("undoGo").onclick = async () => {
  const t = $("undoText").value.trim();
  if (!t || !confirm(`Remove every invoice the agent filed from files with “${t}” in their name, and put those orders' costs back?`)) return;
  $("undoGo").disabled = true;
  try {
    const r = await api("/api/agent/undo-filed", { json: { contains: t } });
    $("undoOut").textContent = `Removed ${r.removed}.`;
    toast(`Removed ${r.removed} wrongly filed invoice(s); costs put back.`);
  } catch (e) { toast(e.message, true); }
  $("undoGo").disabled = false;
};
$("readAll").onclick = async () => {
  if (!confirm("Read the last 90 days of every channel the agent is in, and file every supplier invoice it finds? It runs in the background; follow it on the Work log.")) return;
  try { const r = await api("/api/agent/read-all", { json: {} }); toast(`Started: ${r.channels} channel(s). Watch the Work log.`); showPage("jobs"); }
  catch (e) { toast(e.message, true); }
};

document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-job],[data-advise],[data-answer],[data-lesson],[data-read],[data-goto],[data-jobref],[data-pause],[data-store]");
  if (!el) return;
  try {
    if (el.dataset.store) {
      const inc = el.dataset.inc === "1";
      const rm = document.querySelector(`[data-rm="${el.dataset.store}"]`);
      const remove = !inc && rm && rm.checked;
      if (remove && !confirm("Remove the invoices already filed for this store and put those orders' costs back?")) return;
      el.disabled = true;
      const r = await api("/api/agent/store-decision", { json: { question_id: +el.dataset.store, include: inc, remove_filed: remove } });
      toast(inc ? "Included: its waiting invoices are being filed (Work log)." : `Left out${r.removed ? `; removed ${r.removed} filed invoice(s)` : ""}.`);
      loadQuestions();
      loadStatus();
      return;
    }
    if (el.dataset.pause !== undefined) {
      await api("/api/agent/pause", { json: { paused: el.dataset.pause === "1" } });
      toast(el.dataset.pause === "1" ? "Paused: it stops after the file it's on." : "Resumed.");
      await loadStatus();
      return;
    }
    if (el.dataset.job) loadJob(+el.dataset.job);
    else if (el.hasAttribute("data-advise")) sendAdvice(el);
    else if (el.dataset.goto) { e.preventDefault(); showPage(el.dataset.goto); }
    else if (el.dataset.jobref) { e.preventDefault(); state.job = +el.dataset.jobref; showPage("jobs"); }
    else if (el.dataset.answer) {
      const t = el.closest(".ag-answer").querySelector("textarea").value.trim();
      if (!t) return;
      await api(`/api/agent/questions/${el.dataset.answer}`, { json: { answer: t } });
      toast("Thanks: it's now a lesson.");
      loadQuestions();
      loadStatus();
    } else if (el.dataset.lesson) {
      await api(`/api/agent/lessons/${el.dataset.lesson}`, { method: "PUT", json: { active: el.dataset.on === "1" } });
      loadLessons();
    } else if (el.dataset.read) {
      const r = await api(`/api/agent/channel/${el.dataset.read}/read`, { json: {} });
      toast("Reading… follow it on the Work log.");
      state.job = r.job;
      showPage("jobs");
    }
  } catch (err) { toast(err.message, true); }
});
document.addEventListener("change", async (e) => {
  const row = e.target.closest("[data-chan]");
  if (!row || !(e.target.matches("[data-role]") || e.target.matches("[data-supplier]"))) return;
  try {
    await api(`/api/agent/channel/${row.dataset.chan}`, { method: "PUT", json: { role: row.querySelector("[data-role]").value, supplier: row.querySelector("[data-supplier]").value } });
    toast("Saved");
  } catch (err) { toast(err.message, true); }
});

(async () => {
  await (window.pdMe ? window.pdMe() : null);
  await loadStatus();
  showPage(state.page);
  setInterval(() => { if (document.visibilityState === "visible") { loadStatus(); if (state.page === "jobs") loadJobs(); } }, 20000);
})();
