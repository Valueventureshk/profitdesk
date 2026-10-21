/* Apps → Meta Ads: Overview (campaigns / ad sets / ads with Meta's and the pixel's results) and Manage
   (Claude proposes changes and rules; every change waits for "Do it"; done changes can be undone).
   Every figure comes from the server; this page only formats them. */

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
const num = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (v) => (v === null || v === undefined ? "–" : `${state.cur} ${num(v, 2)}`);
const pct = (v) => (v === null || v === undefined ? "–" : `${num(v * 100, 2)}%`);
const roasTxt = (v) => (v === null || v === undefined ? "–" : num(v, 2));
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
const LEVEL = { campaign: "Campaign", adset: "Ad set", ad: "Ad" };
const STATUS = { ACTIVE: "Running", PAUSED: "Paused", CAMPAIGN_PAUSED: "Campaign paused", ADSET_PAUSED: "Ad set paused",
  IN_PROCESS: "Processing", WITH_ISSUES: "Has issues", DISAPPROVED: "Rejected by Meta", PENDING_REVIEW: "In review",
  PREAPPROVED: "Approved", PENDING_BILLING_INFO: "Billing needed" };

const state = { page: PDUrl.get("tab") || "overview", level: PDUrl.get("level") || "campaign", store: null, stores: [],
  data: null, manage: null, cur: "" };

/* ------------------------------------------------ stores */

async function loadStores() {
  let r;
  try { r = await api("/api/metaads/stores"); } catch (e) { toast(e.message, true); return; }
  state.stores = r.stores;
  if (!r.stores.length) {
    $("overviewPage").innerHTML = '<p class="empty">No store has a Meta ad account linked yet (Settings → Your stores).</p>';
    return;
  }
  const want = PDUrl.get("store") || localStorage.getItem("pdMetaStore");
  state.store = (r.stores.find((s) => String(s.id) === String(want)) || r.stores[0]).id;
  $("store").innerHTML = r.stores.map((s) => `<option value="${s.id}"${s.id === state.store ? " selected" : ""}>${esc(s.name)}${s.waiting ? ` (${s.waiting} waiting)` : ""}</option>`).join("");
  storeInfo();
}

function storeInfo() {
  const s = state.stores.find((x) => x.id === state.store);
  state.cur = s ? s.currency : "";
  $("acct").textContent = s ? `Ad account: ${s.account} · ${s.currency}${s.managing ? " · Claude managing (Ask me first)" : ""}` : "";
  const b = $("waitBadge");
  b.hidden = !(s && s.waiting);
  b.textContent = s ? s.waiting : "";
  PDUrl.set({ store: state.store });
  try { localStorage.setItem("pdMetaStore", state.store); } catch { /* private mode */ }
}

/* ------------------------------------------------ strategy: "$10 up every purchase" (autopilot) */

/* the "Changes made" lists stay folded unless opened, and stay as they were across the 30-second refresh */
state.logOpen = state.logOpen || {};
function keepLogs(box) {
  for (const d of box.querySelectorAll("details.ma-log")) d.addEventListener("toggle", () => { state.logOpen[d.dataset.log] = d.open; });
}

async function loadStrategy() {
  if (!state.store) return;
  loadCut();
  let s;
  try { s = await api(`/api/metaads/strategy?store=${state.store}`); } catch { $("strategy").hidden = true; return; }
  state.strat = s;
  const manage = window.pdLevel === "manage";
  const box = $("strategy");
  box.hidden = false;
  box.classList.toggle("on", !!s.on);
  const feed = s.feed.slice(0, 8).map((f) => `<li class="${f.raised ? "up" : ""}"><time>${when(f.at)}</time>
    <span>${f.raised ? "▲ " : ""}${esc(f.outcome || "")}</span></li>`).join("");
  box.innerHTML = `<div class="ma-strat-head">
      <div><span class="ma-pill ${s.on ? "on" : "off"}">${s.on ? "AUTOPILOT ON" : "Off"}</span>
        <b>${esc(s.name)}</b>
        <p class="hint">Every purchase your pixel credits to a Meta campaign raises that campaign's daily budget by
          <b>${s.currency} ${num(s.step, 0)}</b>, ${s.cap ? `up to <b>${s.currency} ${num(s.cap, 0)}</b> a day per campaign` : "<b>with no cap</b>"}${Object.keys(s.caps || {}).length ? ` (own cap: ${Object.values(s.caps).map((c) => `${esc(c.name)} ${c.cap ? s.currency + " " + num(c.cap, 0) : "no cap"}`).join(", ")})` : ""}.
          ${s.on ? `Running since ${when(s.started_at)}: ${s.purchases} purchase${s.purchases === 1 ? "" : "s"}, ${s.raises} raise${s.raises === 1 ? "" : "s"} (+${s.currency} ${num(s.added, 0)}).`
            : s.stopped_at ? `Switched off ${when(s.stopped_at)}.` : "Counts purchases from the moment it's switched on."}</p></div>
      ${manage ? `<div class="ma-strat-acts">
        <button class="btn ${s.on ? "btn-danger ma-off" : "btn-primary"}" id="stratToggle">${s.on ? "Turn off autopilot" : "Turn on"}</button>
        <details><summary>Settings</summary><div class="ma-rule-fields">
          <label>Raise by (${s.currency})<input class="control" id="stratStep" value="${s.step}"></label>
          <label>Cap per campaign (${s.currency}/day, 0 = no cap)<input class="control" id="stratCap" value="${s.cap}"></label>
          <div class="row-actions"><button class="btn btn-sm" id="stratSave">Save</button></div>
          <div class="ma-caps"><b>Own cap for one campaign</b>
            ${Object.entries(s.caps || {}).map(([id, c]) => `<div class="ma-cap-row"><span>${esc(c.name || id)}</span>
              <b>${s.currency} ${c.cap ? num(c.cap, 0) : "no cap"}</b><button class="btn btn-sm btn-ghost" data-capdel="${esc(id)}">Remove</button></div>`).join("")
              || '<p class="hint">None: every campaign uses the cap above.</p>'}
            <div class="ma-cap-add"><select class="control" id="capCamp"><option value="">Loading campaigns…</option></select>
              <input class="control" id="capVal" placeholder="Cap (${s.currency}/day)"><button class="btn btn-sm" id="capAdd">Set cap</button></div></div>
          </div></details></div>` : ""}
    </div>
    ${feed ? `<details class="ma-log" data-log="up"${state.logOpen.up ? " open" : ""}><summary>Changes made (last ${s.feed.slice(0, 8).length})</summary>
      <ul class="ma-feed">${feed}</ul></details>` : ""}`;
  keepLogs(box);
  if (!manage) return;
  $("stratToggle").onclick = async () => {
    const on = !s.on;
    if (on && !confirm(`Turn on “${s.name}”? From now on every purchase raises its campaign's daily budget by ${s.currency} ${s.step} on Meta, without asking.`)) return;
    try {
      await api(`/api/metaads/strategy/${state.store}`, { method: "PUT", json: { on } });
      toast(on ? "Autopilot on: budgets now rise with every purchase." : "Autopilot off. No more raises.");
      loadStrategy();
    } catch (e) { toast(e.message, true); }
  };
  const setCap = async (body, msg) => {
    try { await api(`/api/metaads/strategy/${state.store}`, { method: "PUT", json: { campaign_cap: body } }); toast(msg); loadStrategy(); }
    catch (e) { toast(e.message, true); }
  };
  for (const b of box.querySelectorAll("[data-capdel]")) b.onclick = () => setCap({ id: b.dataset.capdel, cap: null }, "Back to the general cap.");
  $("capAdd").onclick = () => {
    const o = $("capCamp").selectedOptions[0];
    if (!o || !o.value) { toast("Pick the campaign.", true); return; }
    setCap({ id: o.value, name: o.textContent, cap: $("capVal").value }, `Cap saved for ${o.textContent}.`);
  };
  api(`/api/metaads/overview?store=${state.store}&level=campaign&days=7`).then((d) => {
    const rows = (d.rows || d.items || []).filter((r) => r.id);
    $("capCamp").innerHTML = '<option value="">Pick a campaign</option>' + rows.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("");
  }).catch(() => { $("capCamp").innerHTML = '<option value="">Couldn\'t load campaigns</option>'; });
  $("stratSave").onclick = async () => {
    try {
      await api(`/api/metaads/strategy/${state.store}`, { method: "PUT", json: { step: $("stratStep").value, cap: $("stratCap").value } });
      toast("Saved");
      loadStrategy();
    } catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------ rule: "Budget cut on a poor day" (autopilot, once a day) */

async function loadCut() {
  let s;
  try { s = await api(`/api/metaads/cutrule?store=${state.store}`); } catch { $("cutrule").hidden = true; return; }
  const manage = window.pdLevel === "manage";
  const box = $("cutrule");
  box.hidden = false;
  box.classList.toggle("on", !!s.on);
  const c = s.currency;
  const last = s.last;
  const every = +s.every || 0;
  const runs = s.checked_day === s.today ? (s.runs || []) : [];
  const next = every > 0 ? (s.checked_day === s.today && s.checked != null ? +s.checked + every : +s.spend) : null;
  const status = !s.on ? (s.stopped_at ? `Switched off ${when(s.stopped_at)}.` : "Off.")
    : every > 0 ? `Today: ${runs.length ? runs.map((r) => `${c} ${num(r.point, 0)} → ROAS ${(+r.roas).toFixed(2)}${r.cut ? `, ${r.cut} cut` : ", nothing cut"}`).join(" · ") + ". " : ""}Next check at ${c} ${num(next, 0)} spend. Resets at midnight.`
    : (s.checked_day === s.today || s.fired_day === s.today) ? `Already checked today${last && last.day === s.today ? ` (${when(last.at)}): spend ${c} ${num(last.spend, 0)}, ROAS ${last.roas.toFixed(2)}` : ""}. Next check tomorrow.`
    : `Watching today: checks every 5 minutes, runs at most once a day.`;
  const feed = (s.feed || []).slice(0, 8).map((f) => `<li class="${f.status === "done" ? "down" : ""}"><time>${when(f.done_at || f.created_at)}</time>
    <span>${f.status === "done" ? "▼ " : ""}${esc(f.summary || f.error || f.name)}</span></li>`).join("");
  box.innerHTML = `<div class="ma-strat-head">
      <div><span class="ma-pill ${s.on ? "on" : "off"}">${s.on ? "AUTOPILOT ON" : "Off"}</span>
        <b>${esc(s.name)}</b>
        <p class="hint">When today's spend across all campaigns reaches <b>${c} ${num(s.spend, 0)}</b> and today's overall ROAS
          (EcomVital tracking) is below <b>${num(s.roas, 1)}</b>, every campaign with ROAS below ${num(s.roas, 1)} gets its daily budget cut by
          <b>${num(s.pct, 0)}%</b>. Campaigns at ${num(s.roas, 1)} or above are not touched. ${every > 0 ? `Checks again at every extra <b>${c} ${num(every, 0)}</b> of spend (${c} ${num(+s.spend + every, 0)}, ${c} ${num(+s.spend + 2 * every, 0)} …), each checkpoint once a day.` : "Once a day at most."} ${status}</p></div>
      ${manage ? `<div class="ma-strat-acts">
        <button class="btn ${s.on ? "btn-danger ma-off" : "btn-primary"}" id="cutToggle">${s.on ? "Turn off this rule" : "Turn on"}</button>
        <details><summary>Settings</summary><div class="ma-rule-fields">
          <label>Today's spend reaches (${c})<input class="control" id="cutSpend" value="${s.spend}"></label>
          <label>ROAS below<input class="control" id="cutRoas" value="${s.roas}"></label>
          <label>Cut budget by (%)<input class="control" id="cutPct" value="${s.pct}"></label>
          <label>Check again every (${c} of extra spend, 0 = once a day)<input class="control" id="cutEvery" value="${every}"></label>
          <div class="row-actions"><button class="btn btn-sm" id="cutSave">Save</button></div>
        </div></details></div>` : ""}
    </div>
    ${last && last.results || feed ? `<details class="ma-log" data-log="cut"${state.logOpen.cut ? " open" : ""}><summary>Changes made${last && last.results
        ? ` (last run ${esc(last.day)}: ${last.results.filter((x) => / → /.test(x)).length} cut${last.results.filter((x) => /refused|couldn't/i.test(x)).length ? `, ${last.results.filter((x) => /refused|couldn't/i.test(x)).length} refused by Meta` : ""})` : ""}</summary>
      ${last && last.results ? `<ul class="ma-feed">${last.results.map((x) => `<li class="${/ → /.test(x) ? "down" : ""}"><span>${/ → /.test(x) ? "▼ " : ""}${esc(x)}</span></li>`).join("")}</ul>` : ""}
      ${feed ? `<ul class="ma-feed">${feed}</ul>` : ""}</details>` : ""}`;
  keepLogs(box);
  if (!manage) return;
  $("cutToggle").onclick = async () => {
    const on = !s.on;
    if (on && !confirm(`Turn on “${s.name}”? Once a day, if spend reaches ${c} ${s.spend} with ROAS below ${s.roas}, the weak campaigns' budgets are cut by ${s.pct}% on Meta, without asking.`)) return;
    try { await api(`/api/metaads/cutrule/${state.store}`, { method: "PUT", json: { on } }); toast(on ? "Budget-cut rule on." : "Budget-cut rule off."); loadCut(); }
    catch (e) { toast(e.message, true); }
  };
  $("cutSave").onclick = async () => {
    try {
      await api(`/api/metaads/cutrule/${state.store}`, { method: "PUT", json: { spend: $("cutSpend").value, roas: $("cutRoas").value, pct: $("cutPct").value, every: $("cutEvery").value } });
      toast("Saved"); loadCut();
    } catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------ overview */

async function loadOverview(fresh = false) {
  if (!state.store) return;
  $("table").innerHTML = '<tbody><tr><td class="empty">Reading Meta…</td></tr></tbody>';
  try {
    const p = $("days").value;
    if (p === "custom" && !($("dFrom").value && $("dTo").value)) {
      $("table").innerHTML = '<tbody><tr><td class="empty">Pick the two dates, then Show.</td></tr></tbody>';
      return;
    }
    const dates = p === "custom" ? `&start=${$("dFrom").value}&end=${$("dTo").value}` : "";
    state.data = await api(`/api/metaads/overview?store=${state.store}&level=${state.level}&period=${p}${dates}${fresh ? "&fresh=1" : ""}`);
  } catch (e) { $("table").innerHTML = `<tbody><tr><td class="empty">${esc(e.message)}</td></tr></tbody>`; return; }
  state.cur = state.data.currency;
  renderOverview();
}

function statusPill(r) {
  const eff = r.effective || r.status;
  const cls = eff === "ACTIVE" ? "on" : ["DISAPPROVED", "WITH_ISSUES"].includes(eff) ? "bad" : "off";
  return `<span class="ma-pill ${cls}">${esc(STATUS[eff] || eff || "")}</span>`;
}

function renderOverview() {
  const d = state.data, t = d.total;
  const manage = window.pdLevel === "manage";
  $("tiles").innerHTML = [
    ["Spend", money(t.spend)], ["Meta purchases", num(t.purchases)], ["Meta ROAS", roasTxt(t.roas)],
    ["Pixel purchases", num(t.px_purchases)], ["Pixel ROAS", roasTxt(t.px_roas)],
    ["Autopilot raised", t.auto_raises ? `+${money(t.auto_added)}<small>${num(t.auto_raises)} raise${t.auto_raises === 1 ? "" : "s"}</small>` : "–"],
  ].map(([k, v]) => `<div class="ma-tile"><span>${k}</span><b>${v}</b></div>`).join("")
    + `<div class="ma-tile ma-when"><span>${esc(d.since)} → ${esc(d.until)}</span></div>`;
  const q = $("q").value.trim().toLowerCase();
  const rows = d.rows.filter((r) => (!$("activeOnly").checked || r.effective === "ACTIVE" || r.spend)
    && (!q || `${r.name} ${r.campaign || ""} ${r.adset || ""}`.toLowerCase().includes(q)));
  const parent = (r) => r.level === "ad" ? `${esc(r.campaign)} › ${esc(r.adset)}` : r.level === "adset" ? esc(r.campaign) : "";
  const budget = (r) => {
    if (r.level === "ad") return "";
    if (r.lifetime_budget) return `<span class="ma-sub">lifetime ${money(r.lifetime_budget)}</span>`;
    if (!r.daily_budget) return '<span class="ma-sub">on the ' + (r.level === "adset" ? "campaign" : "ad sets") + "</span>";
    return manage ? `<button class="ma-link" data-budget="${r.id}">${money(r.daily_budget)}</button>` : money(r.daily_budget);
  };
  $("table").innerHTML = `<thead><tr><th>${LEVEL[state.level]}</th><th>Status</th><th>Daily budget</th><th>Spend</th><th>Impr.</th>
      <th>Link clicks</th><th>CTR</th><th>CPC</th><th>Add to cart</th><th>Purchases</th><th>Value</th><th>ROAS</th>
      <th class="ma-px">Pixel purch.</th><th class="ma-px">Pixel value</th><th class="ma-px">Pixel ROAS</th>
      ${state.level === "campaign" ? `<th class="ma-auto" title="What the “$10 up” autopilot added to the daily budget in these days (Undo-ed raises not counted)">Autopilot +</th>` : ""}</tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td class="ma-name">${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy">` : ""}<span><span class="name">${esc(r.name)}</span>
        ${parent(r) ? `<span class="sub">${parent(r)}</span>` : ""}</span></td>
      <td>${statusPill(r)}${manage && ["ACTIVE", "PAUSED"].includes(r.status) ? ` <button class="ma-link" data-toggle="${r.id}">${r.status === "ACTIVE" ? "Pause" : "Switch on"}</button>` : ""}</td>
      <td>${budget(r)}</td><td>${money(r.spend)}</td><td>${num(r.impressions)}</td><td>${num(r.clicks)}</td>
      <td>${pct(r.ctr)}</td><td>${r.cpc === null ? "–" : money(r.cpc)}</td><td>${num(r.atc)}</td><td>${num(r.purchases)}</td>
      <td>${money(r.value)}</td><td><b>${roasTxt(r.roas)}</b></td>
      <td class="ma-px">${num(r.px_purchases)}</td><td class="ma-px">${money(r.px_value)}</td><td class="ma-px"><b>${roasTxt(r.px_roas)}</b></td>
      ${state.level === "campaign" ? `<td class="ma-auto">${r.auto_raises ? `<b>+${money(r.auto_added)}</b><span class="sub">${num(r.auto_raises)}×</span>` : "–"}</td>` : ""}</tr>`).join("")
    || `<tr><td class="empty" colspan="16">Nothing ${$("activeOnly").checked ? "running or spending " : ""}in this window.</td></tr>`}</tbody>`;
}

async function quick(change, id, to) {
  const r = state.data.rows.find((x) => x.id === id);
  try {
    const out = await api(`/api/metaads/quick/${state.store}`, { json: { change, level: r.level, id, name: r.name, to } });
    toast(out.done.join(" · ") + " (Undo on the Manage tab)");
    loadOverview(true);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------ manage */

async function loadManage() {
  if (!state.store) return;
  try { state.manage = await api(`/api/metaads/manage?store=${state.store}`); } catch (e) { toast(e.message, true); return; }
  state.cur = state.manage.currency;
  renderManage();
}

const SOURCE = (s) => s === "ai" ? "Claude" : s === "manual" ? "By hand" : s.startsWith("rule:") ? "Rule" : s.startsWith("undo:") ? "Undo" : s;
const changeText = (a) => a.change === "status" ? `${a.to_value === "ACTIVE" ? "Switch on" : "Pause"} ${LEVEL[a.level].toLowerCase()} “${esc(a.name)}”`
  : `Daily budget of ${LEVEL[a.level].toLowerCase()} “${esc(a.name)}” → ${money(Number(a.to_value))}`;

function renderManage() {
  const m = state.manage, s = m.settings;
  const manage = window.pdLevel === "manage";
  $("enabled").checked = !!s.enabled;
  $("cap").value = s.budget_cap;
  $("capCur").textContent = m.currency;
  $("mngState").textContent = s.enabled ? "On · Ask me first" : "Off: rules don't run";

  // waiting proposals, one card per batch
  const batches = {};
  for (const a of m.actions.slice().reverse()) if (a.status === "proposed") (batches[a.batch] = batches[a.batch] || []).push(a);
  const keys = Object.keys(batches);
  $("waitCount").textContent = keys.length ? `${keys.length} proposal${keys.length > 1 ? "s" : ""}` : "";
  const st = state.stores.find((x) => x.id === state.store);
  if (st) { st.waiting = keys.length; storeInfo(); }
  $("proposals").innerHTML = keys.map((b) => {
    const list = batches[b];
    return `<div class="ma-prop"><div class="ma-prop-head"><b>${SOURCE(list[0].source)}${list[0].source.startsWith("rule:") ? `: ${esc((m.rules.find((r) => `rule:${r.id}` === list[0].source) || {}).name || "")}` : ""}</b>
        <span class="hint">${when(list[0].created_at)} · ${list.length} change${list.length > 1 ? "s" : ""}</span></div>
      <ul>${list.map((a) => `<li>${changeText(a)}<span class="ma-why">${esc(a.reason)}</span></li>`).join("")}</ul>
      ${manage ? `<div class="row-actions"><button class="btn btn-primary btn-sm" data-do="${esc(b)}">Do it</button><button class="btn btn-sm" data-reject="${esc(b)}">Reject</button></div>` : ""}</div>`;
  }).join("") || '<p class="empty">Nothing waiting. Rules and Claude\'s proposals appear here for your “Do it”.</p>';

  // rules
  const field = (r, k, label) => `<label>${label}<input class="control" data-rp="${k}" value="${esc(r.params[k])}"${manage ? "" : " disabled"}></label>`;
  $("rules").innerHTML = m.rules.map((r) => `<div class="ma-rule${r.enabled ? " on" : ""}" data-rule="${r.id}">
      <div class="ma-rule-head"><b>${esc(r.name)}</b>
        ${manage ? `<label class="ma-switch"><input type="checkbox" data-ruleon="${r.id}"${r.enabled ? " checked" : ""}> ${r.enabled ? "On" : "Off"}</label>` : `<span>${r.enabled ? "On" : "Off"}</span>`}</div>
      <p>${esc(r.text)}</p>
      <details><summary>Numbers</summary><div class="ma-rule-fields">
        <label>Level<select class="control" data-rp="level"${manage ? "" : " disabled"}>${(r.kind === "scale_budget" ? ["adset", "campaign"] : r.kind === "no_sale" ? ["ad", "adset"] : ["ad", "adset", "campaign"])
          .map((l) => `<option value="${l}"${r.params.level === l ? " selected" : ""}>${LEVEL[l]}</option>`).join("")}</select></label>
        ${field(r, "days", "Days")}${field(r, "min_spend", `Min spend (${m.currency})`)}
        ${"min_roas" in r.params ? field(r, "min_roas", "ROAS") : ""}${"step_pct" in r.params ? field(r, "step_pct", "Raise by %") : ""}
        ${"max_budget" in r.params ? field(r, "max_budget", `Max budget (${m.currency})`) : ""}
        <label>Count<select class="control" data-rp="basis"${manage ? "" : " disabled"}><option value="meta"${r.params.basis === "meta" ? " selected" : ""}>Meta's</option>
          <option value="pixel"${r.params.basis === "pixel" ? " selected" : ""}>EcomVital pixel</option></select></label>
        ${field(r, "campaign", "Only campaigns named")}
        ${manage ? `<div class="row-actions"><button class="btn btn-sm" data-rulesave="${r.id}">Save numbers</button><button class="btn btn-sm" data-rulerun="${r.id}">Check now</button>
          <button class="btn btn-sm btn-ghost btn-danger" data-ruledel="${r.id}">Delete</button></div>` : ""}</div></details>
      ${r.last_run ? `<p class="hint">Last checked ${when(r.last_run)}: ${r.last_notes.slice(0, 3).map(esc).join(" · ")}${r.last_notes.length > 3 ? ` … +${r.last_notes.length - 3}` : ""}</p>` : '<p class="hint">Not checked yet</p>'}
    </div>`).join("") || '<p class="empty">No rules yet. Add one below, or ask Claude (“pause ads that spend $40 with no sale”).</p>';
  $("ruleForm").innerHTML = `<div class="ma-rule-fields">
      <label>Rule<select class="control" id="nKind">${m.kinds.map((k) => `<option value="${k.kind}">${esc(k.name)}</option>`).join("")}</select></label>
      <label>Name<input class="control" id="nName" placeholder="optional"></label></div>
    <p class="hint">It's saved switched off with starter numbers: open its Numbers, set yours, then turn it on.</p>
    <button class="btn btn-sm btn-primary" id="nAdd">Add rule</button>`;
  $("nAdd").onclick = async () => {
    try {
      await api(`/api/metaads/rules/${state.store}`, { json: { kind: $("nKind").value, name: $("nName").value, params: {} } });
      toast("Rule added (off). Set its numbers, then turn it on.");
      loadManage();
    } catch (e) { toast(e.message, true); }
  };

  // chat
  $("chat").innerHTML = m.chat.map((c) => `<div class="ma-msg ${c.role}"><b>${c.role === "user" ? esc((c.who || "").split("@")[0]) : "Claude"}</b>
    <div>${esc(c.content).replace(/\n/g, "<br>")}</div></div>`).join("") || `<p class="hint">${m.ai ? "Ask about your results, or tell Claude what to change. It proposes; you press Do it." : "Add the AI key in Settings → AI first."}</p>`;
  $("chat").scrollTop = $("chat").scrollHeight;

  // log
  const done = m.actions.filter((a) => a.status !== "proposed");
  $("log").innerHTML = done.length ? `<thead><tr><th>When</th><th>Change</th><th>From</th><th>Result</th><th></th></tr></thead><tbody>${done.map((a) => `<tr>
      <td>${when(a.done_at || a.created_at)}</td><td class="ma-wrap">${a.summary ? esc(a.summary) : changeText(a)}<span class="sub">${esc(a.reason || "")}</span></td>
      <td>${SOURCE(a.source)}${a.decided_by ? `<span class="sub">${esc(a.decided_by.split("@")[0])}</span>` : ""}</td>
      <td><span class="ma-pill ${a.status === "done" ? "on" : a.status === "failed" ? "bad" : "off"}">${esc(a.status)}</span>${a.error ? `<span class="sub ma-err">${esc(a.error)}</span>` : ""}</td>
      <td>${manage && a.status === "done" && a.before ? `<button class="btn btn-sm" data-undo="${a.id}">Undo</button>` : ""}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="empty">No changes yet.</td></tr></tbody>';
}

/* ------------------------------------------------ page */

function showPage(p) {
  state.page = p;
  PDUrl.set({ tab: p === "overview" ? null : p });
  for (const b of $("pages").querySelectorAll("button")) b.classList.toggle("on", b.dataset.page === p);
  $("overviewPage").hidden = p !== "overview";
  $("managePage").hidden = p !== "manage";
  if (p === "manage") loadManage(); else loadOverview();
}

$("pages").onclick = (e) => { const b = e.target.closest("button[data-page]"); if (b) showPage(b.dataset.page); };
$("levels").onclick = (e) => {
  const b = e.target.closest("button[data-level]");
  if (!b) return;
  state.level = b.dataset.level;
  PDUrl.set({ level: state.level === "campaign" ? null : state.level });
  for (const x of $("levels").querySelectorAll("button")) x.classList.toggle("on", x === b);
  loadOverview();
};
$("store").onchange = () => { state.store = +$("store").value; storeInfo(); loadStrategy(); showPage(state.page); };
$("days").onchange = () => {
  const custom = $("days").value === "custom";
  $("customDates").hidden = !custom;
  if (custom && state.data && !$("dFrom").value) { $("dFrom").value = state.data.since; $("dTo").value = state.data.until; }
  PDUrl.set({ period: $("days").value, from: custom ? $("dFrom").value : null, to: custom ? $("dTo").value : null });
  if (!custom) loadOverview();
};
// back to the picked period after a refresh
if (PDUrl.get("period") && [...$("days").options].some((o) => o.value === PDUrl.get("period"))) {
  $("days").value = PDUrl.get("period");
  if ($("days").value === "custom") {
    $("customDates").hidden = false;
    $("dFrom").value = PDUrl.get("from") || "";
    $("dTo").value = PDUrl.get("to") || "";
  }
}
$("dGo").onclick = () => { PDUrl.set({ period: "custom", from: $("dFrom").value, to: $("dTo").value }); loadOverview(); };
$("refresh").onclick = () => loadOverview(true);
$("activeOnly").onchange = $("q").oninput = () => state.data && renderOverview();
$("enabled").onchange = async () => {
  try {
    await api(`/api/metaads/settings/${state.store}`, { method: "PUT", json: { enabled: $("enabled").checked } });
    toast($("enabled").checked ? "Managing on: switched-on rules are checked every hour, changes wait for Do it." : "Managing off.");
    const s = state.stores.find((x) => x.id === state.store);
    if (s) s.managing = $("enabled").checked;
    storeInfo();
    loadManage();
  } catch (e) { toast(e.message, true); $("enabled").checked = !$("enabled").checked; }
};
$("cap").onchange = async () => {
  try { await api(`/api/metaads/settings/${state.store}`, { method: "PUT", json: { budget_cap: $("cap").value } }); toast("Budget cap saved"); } catch (e) { toast(e.message, true); }
};
$("askForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("ask").value.trim();
  if (!text) return;
  $("askBtn").disabled = true;
  $("askBtn").textContent = "Thinking…";
  state.manage.chat.push({ role: "user", content: text, who: "you" });
  renderManage();
  $("ask").value = "";
  try {
    await api(`/api/metaads/chat/${state.store}`, { json: { text } });
  } catch (err) { toast(err.message, true); }
  $("askBtn").disabled = false;
  $("askBtn").textContent = "Ask";
  loadManage();
};
$("ask").onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("askForm").requestSubmit(); };

document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-toggle],[data-budget],[data-do],[data-reject],[data-undo],[data-rulesave],[data-rulerun],[data-ruledel]");
  if (!el) return;
  try {
    if (el.dataset.toggle) {
      const r = state.data.rows.find((x) => x.id === el.dataset.toggle);
      const to = r.status === "ACTIVE" ? "PAUSED" : "ACTIVE";
      if (!confirm(`${to === "PAUSED" ? "Pause" : "Switch on"} “${r.name}” on Meta now?`)) return;
      await quick("status", r.id, to);
    } else if (el.dataset.budget) {
      const r = state.data.rows.find((x) => x.id === el.dataset.budget);
      const v = prompt(`New daily budget for “${r.name}” (${state.cur}). Now ${num(r.daily_budget, 2)}.\nTip: changes over ~20% restart Meta's learning.`, num(r.daily_budget, 2).replace(/,/g, ""));
      if (!v) return;
      await quick("budget", r.id, Number(v.replace(/[^\d.]/g, "")));
    } else if (el.dataset.do) {
      el.disabled = true;
      el.textContent = "Doing it on Meta…";
      const r = await api(`/api/metaads/batch/${state.store}/${encodeURIComponent(el.dataset.do)}/do`, { json: {} });
      toast(r.done.join(" · "));
      loadManage();
    } else if (el.dataset.reject) {
      await api(`/api/metaads/batch/${state.store}/${encodeURIComponent(el.dataset.reject)}/reject`, { json: {} });
      loadManage();
    } else if (el.dataset.undo) {
      if (!confirm("Put this back as it was on Meta?")) return;
      const r = await api(`/api/metaads/action/${el.dataset.undo}/undo`, { json: {} });
      toast(r.done.join(" · "));
      loadManage();
    } else if (el.dataset.rulesave) {
      const box = el.closest("[data-rule]");
      const params = {};
      for (const f of box.querySelectorAll("[data-rp]")) params[f.dataset.rp] = f.value;
      await api(`/api/metaads/rule/${el.dataset.rulesave}`, { method: "PUT", json: { params } });
      toast("Rule saved");
      loadManage();
    } else if (el.dataset.rulerun) {
      el.disabled = true;
      el.textContent = "Checking…";
      const r = await api(`/api/metaads/rule/${el.dataset.rulerun}/run`, { json: {} });
      toast(r.made.length ? `It wants ${r.made[0].changes} change(s): see Waiting for you.` : "Nothing to change right now.");
      loadManage();
    } else if (el.dataset.ruledel) {
      if (!confirm("Delete this rule?")) return;
      await api(`/api/metaads/rule/${el.dataset.ruledel}`, { method: "DELETE" });
      loadManage();
    }
  } catch (err) {
    toast(err.message, true);
    if (state.page === "manage") loadManage();
  }
});
document.addEventListener("change", async (e) => {
  if (!e.target.dataset.ruleon) return;
  try {
    await api(`/api/metaads/rule/${e.target.dataset.ruleon}`, { method: "PUT", json: { enabled: e.target.checked } });
    toast(e.target.checked ? (state.manage.settings.enabled ? "Rule on" : "Rule on. Switch managing on (top) for it to run every hour.") : "Rule off");
    loadManage();
  } catch (err) { toast(err.message, true); }
});

(async () => {
  await (window.pdMe ? window.pdMe() : null);
  await loadStores();
  for (const x of $("levels").querySelectorAll("button")) x.classList.toggle("on", x.dataset.level === state.level);
  if (state.store) { loadStrategy(); showPage(state.page); }
  setInterval(() => { if (document.visibilityState === "visible") loadStrategy(); }, 30000);
})();
