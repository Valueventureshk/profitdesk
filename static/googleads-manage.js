/* Apps → Google Ads → Manage: per-store managing settings, campaigns, the ads AI chat, its plans (Do it),
   rules (Turn on), and the change log (Undo). Every change goes through the server, which checks it with
   Google first and keeps what it was before. Uses $, esc, api, post, toast from googleads.js. */

let mg = null, mgBusy = false, mgPeriod = PDUrl.get("mperiod") || "today";
const MG_PERIOD = { today: "Today", yesterday: "Yesterday", 7: "Last 7 days", 14: "Last 14 days", 30: "Last 30 days", mtd: "This month",
                    lastmonth: "Last month", 90: "Last 90 days", custom: "Custom" };
function mgRange() {
  const t = storeToday();
  switch (mgPeriod) {
    case "today": return [t, t];
    case "yesterday": return [addDays(t, -1), addDays(t, -1)];
    case "mtd": return [t.slice(0, 8) + "01", t];
    case "lastmonth": { const e = addDays(t.slice(0, 8) + "01", -1); return [e.slice(0, 8) + "01", e]; }
    case "custom": return [$("mgFrom").value || addDays(t, -6), $("mgTo").value || t];
    default: return [addDays(t, -(Number(mgPeriod) - 1)), t];
  }
}
const mgMoney = (v, d = 2) => {
  if (v === null || v === undefined) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: mg?.structure?.currency || "USD", minimumFractionDigits: d, maximumFractionDigits: d }).format(v); }
  catch { return Number(v).toFixed(d); }
};
const mgWhen = (iso) => { try { return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }); } catch { return iso; } };
const KIND_WORD = { create_campaign: "Create campaign", move_products: "Move products", add_products: "Add products", remove_products: "Take products out",
  set_budget: "Budget", set_status: "Pause / enable", set_target_roas: "Target ROAS" };
const STATUS_WORD = { planned: ["changed", "Planned"], proposed: ["changed", "Waiting"], done: ["invoice", "Done"], failed: ["bad", "Failed"],
  rejected: ["new", "Cancelled"], undone: ["new", "Undone"] };

window.loadManage = async function (fresh = false) {
  const store = $("store").value;
  if (!store) return;
  $("mgCampNote").textContent = "Reading the Google Ads account…";
  const [a, b] = mgRange();
  PDUrl.set({ mperiod: mgPeriod === "today" ? null : mgPeriod });
  try { mg = await api(`/api/ads/manage?store=${store}&start=${a}&end=${b}${fresh ? "&fresh=1" : ""}`); }
  catch (e) { $("mgCampNote").textContent = e.message; toast(e.message, true); return; }
  drawManage();
};

function drawManage() {
  const s = mg.settings;
  $("mgChatStore").textContent = `${mg.name} · ${mg.structure.currency}`;
  $("mgSet").innerHTML = `<div class="gx-mgrow">
      <label class="gx-switch"><input type="checkbox" id="mgOn"${s.enabled ? " checked" : ""}><i></i><b>Manage ${esc(mg.name)} from EcomVital</b></label>
      <span class="gx-seg" id="mgMode"><button data-m="approve" class="${s.mode === "approve" ? "on" : ""}">Ask me first</button><button data-m="auto" class="${s.mode === "auto" ? "on" : ""}">Autopilot</button></span>
      <label class="gx-f">Max budget per campaign / day <input id="mgCap" type="number" min="1" value="${s.budget_cap}"></label>
      <label class="gx-f">Max for a new campaign <input id="mgNewCap" type="number" min="1" value="${s.new_budget_cap}"></label>
      <button class="gx-btn" id="mgSave">Save</button></div>
    <p class="gx-muted gx-mgnote">${s.enabled
      ? (s.mode === "auto" ? "<b>Autopilot</b>: rules carry out their changes by themselves (within the caps) and log them here with Undo. Chat plans still wait for your Do it."
                           : "<b>Ask me first</b>: rules check every hour and put what they want under “Waiting for you”. Nothing changes until you approve.")
      : "Managing is off: rules don't run. You can still chat with the ads AI and do its plans."}
      ${mg.has_ai ? "" : " <b>Add the Anthropic API key in Settings → AI to chat.</b>"}</p>`;
  $("mgOn").onchange = () => saveMg({ enabled: $("mgOn").checked });
  for (const b of $("mgMode").querySelectorAll("[data-m]")) b.onclick = () => {
    if (b.dataset.m === "auto" && !confirm("Autopilot: rules will change Google Ads by themselves (within the caps), without asking. Turn it on?")) return;
    saveMg({ mode: b.dataset.m });
  };
  $("mgSave").onclick = () => saveMg({ budget_cap: $("mgCap").value, new_budget_cap: $("mgNewCap").value });

  const camps = mg.structure.campaigns;
  const st = mg.structure;
  $("mgCampNote").textContent = `${mg.breakeven_roas ? `Breakeven ROAS ${mg.breakeven_roas} (last 30 days: sales ÷ (sales − refunds − COG − fees)) · ` : ""}${camps.length} campaigns · ${MG_PERIOD[mgPeriod]}: ${st.start === st.end ? st.start : `${st.start} to ${st.end}`} · ${st.currency} · ROAS = Shopify sales ÷ spend, G ROAS = Google's conversion value ÷ spend`;
  const tracks = camps.some((c) => c.p.conversions || c.p.atc);
  const plist = (c) => c.groups.map((g) => {
    const p = g.products, w = { all: "all products", only: `${p.count} variants`, except: `all but ${p.count}`, custom: "by labels (read-only)", none: "no products" }[p.mode] || p.mode;
    return `<span class="gx-pl ${p.mode}" title="${esc(g.name)} (${esc(g.status)})">${esc(g.name)}: ${w}</span>`;
  }).join(" ");
  const r2 = (v) => (v === null || v === undefined ? "—" : v.toFixed(2));
  $("mgCamps").innerHTML = `<thead><tr><th>Campaign</th><th>Status</th><th>Type</th><th>${G_ICON}Budget / day</th><th>Target ROAS</th><th>Products</th>
      <th>${G_ICON}Spend</th><th>${S_ICON}Sales</th><th>ROAS</th><th>Clicks</th><th>Conv.</th><th>Conv. value</th><th>G ROAS</th><th>Add to carts</th><th></th></tr></thead><tbody>${camps.map((c) => `<tr data-c="${c.id}">
      <td class="gx-cname"><b>${esc(c.name)}</b><span class="gx-muted">ID ${c.id}</span></td>
      <td><span class="gx-status ${c.status === "ENABLED" ? "active" : "draft"}">${c.status === "ENABLED" ? "Enabled" : "Paused"}</span></td>
      <td class="gx-muted">${esc((c.type || "").replace("PERFORMANCE_MAX", "PMax").replace("_", " ").toLowerCase())}</td>
      <td>${mgMoney(c.budget)}</td><td>${troas(c)}</td><td class="gx-pls">${plist(c) || "—"}</td>
      <td>${mgMoney(c.p.cost)}</td><td>${mgMoney(c.p.sales)}</td><td class="gx-hc ${roasCls(c.p.roas)}">${r2(c.p.roas)}</td>
      <td>${c.p.clicks.toLocaleString()}</td><td class="gx-purple">${c.p.conversions.toFixed(1)}</td><td>${mgMoney(c.p.value)}</td>
      <td>${r2(c.p.groas)}</td><td>${c.p.atc ? c.p.atc.toFixed(0) : "—"}</td>
      <td class="gx-cact" data-manage-only><button class="gx-btn gx-sm" data-bud="${c.id}">Budget</button>
        <button class="gx-btn gx-sm" data-st="${c.id}">${c.status === "ENABLED" ? "Pause" : "Enable"}</button></td></tr>`).join("")
    || `<tr><td colspan="15" class="gx-muted gx-empty">No campaigns.</td></tr>`}</tbody>`;
  const spent = camps.reduce((x, c) => x + c.p.cost, 0), sold = camps.reduce((x, c) => x + c.p.sales, 0);
  const conv = camps.reduce((x, c) => x + c.p.conversions, 0), atc = camps.reduce((x, c) => x + c.p.atc, 0);
  if (spent > 20 && sold > 0 && !conv) $("mgCampNote").innerHTML += ` · <b class="gx-neg">Google recorded no purchases while Shopify sold ${esc(mgMoney(sold, 0))}${atc ? ` (it does see ${atc.toFixed(0)} add to carts)` : ""}: this account's purchase tracking isn't working.</b>`;
  for (const b of $("mgCamps").querySelectorAll("[data-bud]")) b.onclick = () => {
    const c = camps.find((x) => x.id === b.dataset.bud);
    const v = prompt(`New daily budget for “${c.name}” (now ${mgMoney(c.budget)}):`, c.budget);
    if (v === null || v === "") return;
    manual("set_budget", { campaign: c.id, amount: Number(v) });
  };
  for (const b of $("mgCamps").querySelectorAll("[data-st]")) b.onclick = () => {
    const c = camps.find((x) => x.id === b.dataset.st), to = c.status === "ENABLED" ? "PAUSED" : "ENABLED";
    if (confirm(`${to === "PAUSED" ? "Pause" : "Enable"} “${c.name}” in Google Ads?`)) manual("set_status", { campaign: c.id, status: to });
  };
  drawPending(); drawRules(); drawLog(); drawChat();
}
// target ROAS against the store's breakeven: Google's ROAS is revenue ÷ spend, so a target under breakeven aims at a loss
function troas(c) {
  const be = mg.breakeven_roas;
  if (!c.target_roas) return c.type === "SEARCH" ? "—" : `<span class="gx-muted" title="No target: Google spends the whole budget for the most value">none</span>`;
  if (!be) return c.target_roas;
  const ok = c.target_roas >= be;
  return `<span class="${ok ? "gx-posc" : "gx-negc"}" title="Breakeven ROAS ${be}: ${ok ? "the target is at or above breakeven" : "the target is under breakeven, so Google may buy sales that lose money"}">${c.target_roas}${ok ? "" : " ⚠"}</span>`;
}
const roasCls = (r) => (r === null || r === undefined ? "" : r >= 3 ? "gx-h4" : r >= 2 ? "gx-h3" : r >= 1.5 ? "gx-h2" : "gx-h1");

async function saveMg(body) {
  try { mg.settings = await api(`/api/ads/settings/${mg.store}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); drawManage(); toast("Saved."); }
  catch (e) { toast(e.message, true); }
}
async function manual(kind, params) {
  try { const r = await post(`/api/ads/manual/${mg.store}`, { kind, params }); toast(r.done.join("; ")); loadManage(true); }
  catch (e) { toast(e.message, true); }
}

function batches(filter) {
  const out = new Map();
  for (const a of mg.actions.filter(filter).slice().reverse()) {
    if (!out.has(a.batch)) out.set(a.batch, []);
    out.get(a.batch).push(a);
  }
  return [...out.entries()].reverse();
}
function planCard(batch, steps, live) {
  const src = steps[0].source;
  const who = src === "chat" ? "Plan from the chat" : src === "manual" ? "By hand" : `Rule: ${esc((mg.rules.find((r) => `rule:${r.id}` === src) || {}).name || src)}`;
  return `<div class="gx-plan" data-batch="${esc(batch)}"><div class="gx-planh"><b>${who}</b><span class="gx-muted">${mgWhen(steps[0].created_at)}</span></div>
    <ol>${steps.map((a) => `<li><span class="src ${STATUS_WORD[a.status]?.[0] || "new"}">${STATUS_WORD[a.status]?.[1] || a.status}</span>
      <b>${KIND_WORD[a.kind] || a.kind}</b> ${esc(a.summary || "")}${stepParams(a)}${a.error ? `<div class="gx-err">${esc(a.error)}</div>` : ""}</li>`).join("")}</ol>
    ${live ? `<div class="gx-planb" data-manage-only><button class="gx-go" data-go="${esc(batch)}">${src === "chat" ? "Do it" : "Approve"}</button>
      <button class="gx-btn" data-no="${esc(batch)}">${src === "chat" ? "Cancel" : "Reject"}</button></div>` : ""}</div>`;
}
function stepParams(a) {
  const p = a.params, bits = [];
  if (p.name) bits.push(`“${p.name}”`);
  if (p.budget) bits.push(`${mgMoney(p.budget)}/day`);
  if (p.amount) bits.push(`${mgMoney(p.amount)}/day`);
  if (p.products) bits.push(`${p.products.length} products`);
  const cn = (ref) => (mg.structure.campaigns.find((c) => c.id === String(ref)) || {}).name || String(ref).replace(/^new:/i, "new ");
  if (p.from_campaign) bits.push(`from “${cn(p.from_campaign)}”`);
  if (p.to_campaign) bits.push(`to “${cn(p.to_campaign)}”`);
  if (p.campaign) bits.push(`“${cn(p.campaign)}”`);
  if (p.status) bits.push(p.status.toLowerCase());
  if (p.target_roas) bits.push(`target ROAS ${p.target_roas}`);
  const det = Array.isArray(a.detail) ? `<details class="gx-det"><summary>${a.detail.length} products</summary><table>${a.detail.map((d) =>
    `<tr><td>${esc(d.title || d.product)}</td>${d.cost !== undefined ? `<td>${mgMoney(d.cost)}</td>` : ""}${d.sales !== undefined ? `<td>${mgMoney(d.sales)}</td>` : ""}${d.roas !== undefined ? `<td>ROAS ${d.roas}</td>` : ""}${d.qty !== undefined ? `<td>${d.qty} sold</td>` : ""}${d.atc ? `<td>${d.atc} ATC</td>` : ""}</tr>`).join("")}</table></details>` : "";
  return (bits.length ? ` <span class="gx-muted">· ${esc(bits.join(" · "))}</span>` : "") + det;
}
function wireBatch(root) {
  for (const b of root.querySelectorAll("[data-go]")) b.onclick = async () => {
    if (mgBusy) return;
    mgBusy = true; b.disabled = true; b.textContent = "Working…";
    try { const r = await post(`/api/ads/batch/${mg.store}/${encodeURIComponent(b.dataset.go)}`, { go: true }); toast(`Done: ${r.done.join("; ")}`); }
    catch (e) { toast(e.message, true); }
    mgBusy = false;
    loadManage(true);
  };
  for (const b of root.querySelectorAll("[data-no]")) b.onclick = async () => {
    try { await post(`/api/ads/batch/${mg.store}/${encodeURIComponent(b.dataset.no)}`, { go: false }); loadManage(); }
    catch (e) { toast(e.message, true); }
  };
}
function drawPending() {
  const list = batches((a) => a.status === "planned" || a.status === "proposed");
  $("mgPending").innerHTML = list.map(([b, steps]) => planCard(b, steps, true)).join("") || `<p class="gx-muted gx-pad">Nothing waiting.</p>`;
  wireBatch($("mgPending"));
}
function drawRules() {
  $("mgRules").innerHTML = mg.rules.map((r) => `<div class="gx-rule${r.enabled ? " on" : ""}">
      <label class="gx-switch" data-manage-only><input type="checkbox" data-ron="${r.id}"${r.enabled ? " checked" : ""}><i></i></label>
      <div class="gx-rtext"><b>${esc(r.name)}</b><span>${esc(r.text)}</span>
        ${r.last_run ? `<span class="gx-muted">Last check ${mgWhen(r.last_run)}: ${esc((r.last_notes || []).join(" · "))}</span>` : `<span class="gx-muted">Not run yet</span>`}</div>
      <span class="gx-rbtn" data-manage-only><button class="gx-btn gx-sm" data-rrun="${r.id}">Check now</button><button class="gx-link" data-rdel="${r.id}">Delete</button></span></div>`).join("")
    || `<p class="gx-muted gx-pad">No rules yet. Tell the ads AI what to keep an eye on, e.g. “In campaign A, if a product's ROAS is under 2 over 3 days, move it to Zombie”.</p>`;
  for (const c of $("mgRules").querySelectorAll("[data-ron]")) c.onchange = () => ruleDo(c.dataset.ron, { enabled: c.checked });
  for (const b of $("mgRules").querySelectorAll("[data-rrun]")) b.onclick = async () => { b.disabled = true; b.textContent = "Checking…"; await ruleDo(b.dataset.rrun, { run: true }); };
  for (const b of $("mgRules").querySelectorAll("[data-rdel]")) b.onclick = () => { if (confirm("Delete this rule?")) ruleDo(b.dataset.rdel, { delete: true }); };
}
async function ruleDo(id, body) {
  try { await post(`/api/ads/rules/${id}`, body); loadManage(); }
  catch (e) { toast(e.message, true); loadManage(); }
}
function drawLog() {
  const rows = mg.actions.filter((a) => !["planned", "proposed"].includes(a.status)).slice(0, 40);
  $("mgLog").innerHTML = rows.length ? `<table class="gx-table gx-log"><thead><tr><th>When</th><th>Change</th><th>From</th><th>Status</th><th></th></tr></thead><tbody>${rows.map((a) => {
    const [c, w] = STATUS_WORD[a.status] || ["new", a.status];
    return `<tr><td>${mgWhen(a.done_at || a.created_at)}</td><td class="gx-wrapcell"><b>${KIND_WORD[a.kind] || a.kind}</b> ${esc(a.summary || "")}${a.error ? `<div class="gx-err">${esc(a.error)}</div>` : ""}</td>
      <td class="gx-muted">${esc(a.source === "chat" ? "Chat" : a.source === "manual" ? "By hand" : "Rule")} · ${esc(a.decided_by || "")}</td>
      <td><span class="src ${c}">${w}</span></td><td>${a.status === "done" ? `<button class="gx-btn gx-sm" data-undo="${a.id}" data-manage-only>Undo</button>` : ""}</td></tr>`;
  }).join("")}</tbody></table>` : `<p class="gx-muted gx-pad">No changes yet.</p>`;
  for (const b of $("mgLog").querySelectorAll("[data-undo]")) b.onclick = async () => {
    if (!confirm("Undo this change in Google Ads?")) return;
    try { const r = await post(`/api/ads/undo/${b.dataset.undo}`, {}); toast(r.message); loadManage(true); }
    catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------ chat */
function md(t) {
  // small, safe Markdown: tables, bold, line breaks
  const lines = esc(t).split("\n"), out = [];
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*\|/.test(lines[i]) && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] || "")) {
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(lines[i]); i += 2;
      const body = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) body.push(cells(lines[i++]));
      i--;
      out.push(`<table class="gx-mdt"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`);
    } else out.push(lines[i]);
  }
  return out.join("\n").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n(?!<)/g, "<br>").replace(/^#+\s*(.+)$/gm, "<b>$1</b>");
}
function drawChat() {
  const msgs = mg.chat;
  $("mgMsgs").innerHTML = msgs.map((m) => {
    if (m.role === "user") return `<div class="gx-msg me">${esc(m.content.text)}<span>${esc(m.who || "")}</span></div>`;
    const plans = (m.content.plans || []).map((b) => { const steps = mg.actions.filter((a) => a.batch === b).reverse(); return steps.length ? planCard(b, steps, steps.some((a) => a.status === "planned")) : ""; }).join("");
    const rules = (m.content.rules || []).map((id) => { const r = mg.rules.find((x) => x.id === id); return r ? `<div class="gx-plan"><div class="gx-planh"><b>Rule: ${esc(r.name)}</b></div>
        <p>${esc(r.text)}</p><div class="gx-planb" data-manage-only>${r.enabled ? `<span class="src invoice">On</span>` : `<button class="gx-go" data-ronb="${r.id}">Turn on</button>`}</div></div>` : ""; }).join("");
    return `<div class="gx-msg ai">${md(m.content.text || "")}${plans}${rules}</div>`;
  }).join("") || `<div class="gx-muted gx-pad">Ask the ads AI anything about ${esc(mg.name)}'s Google Ads, or tell it what to do:<br><br>
      • “Which products spent over $20 in the last 30 days with ROAS under 1.5?”<br>
      • “Create a campaign Winners at $100 a day with every product that spent over $20 at ROAS 2.5+, and remove them from the current campaign.”<br>
      • “In Winners, if a product's ROAS is under 2 over 3 days, move it to Zombie.”<br>
      • “If Scaling is over 2.5 ROAS, add $50 a day to its budget.”</div>`;
  wireBatch($("mgMsgs"));
  for (const b of $("mgMsgs").querySelectorAll("[data-ronb]")) b.onclick = () => ruleDo(b.dataset.ronb, { enabled: true });
  $("mgMsgs").scrollTop = $("mgMsgs").scrollHeight;
}
async function send() {
  const text = $("mgIn").value.trim();
  if (!text || mgBusy) return;
  mgBusy = true;
  $("mgSend").disabled = true;
  mg.chat.push({ role: "user", content: { text }, who: "you" });
  drawChat();
  $("mgIn").value = "";
  $("mgMsgs").insertAdjacentHTML("beforeend", `<div class="gx-msg ai gx-think">Looking at the account…</div>`);
  $("mgMsgs").scrollTop = $("mgMsgs").scrollHeight;
  try { await post(`/api/ads/chat/${mg.store}`, { message: text }); }
  catch (e) { toast(e.message, true); }
  mgBusy = false;
  $("mgSend").disabled = false;
  await loadManage();
}
$("mgSend").onclick = send;
$("mgIn").onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); };
$("mgReload").onclick = () => loadManage(true);
$("mgPeriod").value = mgPeriod;
$("mgDates").hidden = mgPeriod !== "custom";
$("mgPeriod").onchange = () => {
  mgPeriod = $("mgPeriod").value;
  $("mgDates").hidden = mgPeriod !== "custom";
  if (mgPeriod === "custom" && !$("mgFrom").value) { const [a, b] = mgRange(); $("mgFrom").value = a; $("mgTo").value = b; }
  loadManage();
};
$("mgFrom").onchange = $("mgTo").onchange = () => { if ($("mgFrom").value && $("mgTo").value) loadManage(); };
