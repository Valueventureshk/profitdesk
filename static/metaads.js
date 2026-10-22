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

/* ------------------------------------------------ Budget cut rules (several per store, autopilot or ask first) */

const CUT_STATE = { "cut now": "bad", "would cut (outside active hours)": "bad", "cut today": "bad", "paused today": "bad",
  "ROAS fine": "on", waiting: "off" };

function cutSentence(r, c) {
  const roasFrom = r.basis === "meta" ? "Meta's ROAS" : "ROAS (EcomVital tracking)";
  const act = r.action === "pause" ? "the campaign is <b>paused</b>"
    : `its daily budget is cut by <b>${num(r.pct, 0)}%</b> of ${r.base === "start" ? "its budget at the start of the day" : "its budget at that moment"}${+r.floor ? `, never below <b>${c} ${num(r.floor, 2)}</b>` : ""}`;
  const who = r.scope === "all" ? "any campaign" : `a picked campaign (${Object.keys(r.campaigns || {}).length} switched on)`;
  const trig = r.trigger === "campaign"
    ? `When ${who} has spent <b>${r.camp_by === "pct" ? `${num(r.camp_pct, 0)}% of its starting budget` : `${c} ${num(r.camp_spend, 2)}`}</b> today and its ${roasFrom} is below <b>${num(r.roas, 2)}</b>, ${act}. Once per campaign per day.`
    : `When today's spend of ${r.scope === "all" ? "all campaigns" : "the picked campaigns"} reaches <b>${c} ${num(r.spend, 0)}</b> and their overall ${roasFrom} is below <b>${num(r.roas, 2)}</b>, each of them with ROAS below ${num(r.roas, 2)} that spent today: ${act}.${+r.every ? ` Checks again every extra <b>${c} ${num(r.every, 0)}</b> of spend.` : " Once a day."}`;
  const extra = [];
  if (+r.hour_from > 0 || +r.hour_to < 24) extra.push(`Only between ${String(r.hour_from).padStart(2, "0")}:00 and ${String(r.hour_to).padStart(2, "0")}:00 (store clock).`);
  if (r.reset) extra.push("Budgets go back at midnight.");
  if (r.mode === "ask") extra.push("<b>Ask me first</b>: cuts wait for Do it on the Manage tab.");
  return `${trig} ${extra.join(" ")}`;
}

function cutField(label, inner, show = "") { return `<label${show ? ` data-show="${show}"` : ""}>${label}${inner}</label>`; }
function cutSelect(id, cur, opts) {
  return `<select class="control" data-f="${id}">${opts.map(([v, t]) => `<option value="${v}"${String(cur) === String(v) ? " selected" : ""}>${t}</option>`).join("")}</select>`;
}
function cutInput(id, v) { return `<input class="control" data-f="${id}" value="${esc(v)}">`; }

function cutSettings(r, c) {
  const hours = Array.from({ length: 25 }, (_, h) => [h, `${String(h).padStart(2, "0")}:00`]);
  return `<div class="ma-rule-fields ma-cut-fields" data-rule="${r.id}">
    ${cutField("Name", cutInput("name", r.name))}
    ${cutField("What starts it", cutSelect("trigger", r.trigger, [["campaign", "Each campaign by itself"], ["account", "Today's total spend (poor day)"]]))}
    ${cutField("Campaigns", cutSelect("scope", r.scope, [["picked", "Only the ones I switch on"], ["all", "All campaigns"]]))}
    ${cutField("Campaign has spent", cutSelect("camp_by", r.camp_by, [["pct", "% of its starting budget"], ["amount", `an amount (${c})`]]), "campaign")}
    ${cutField("% of starting budget", cutInput("camp_pct", r.camp_pct), "campaign pct")}
    ${cutField(`Amount spent (${c})`, cutInput("camp_spend", r.camp_spend), "campaign amount")}
    ${cutField(`Today's spend reaches (${c})`, cutInput("spend", r.spend), "account")}
    ${cutField(`Check again every (${c}, 0 = once a day)`, cutInput("every", r.every), "account")}
    ${cutField("ROAS below", cutInput("roas", r.roas))}
    ${cutField("ROAS from", cutSelect("basis", r.basis, [["pixel", "EcomVital tracking"], ["meta", "Meta's own count"]]))}
    ${cutField("Then", cutSelect("action", r.action, [["cut", "Cut the daily budget"], ["pause", "Pause the campaign"]]))}
    ${cutField("Cut by (%)", cutInput("pct", r.pct), "cut")}
    ${cutField("% taken from", cutSelect("base", r.base, [["start", "Budget at the start of the day"], ["now", "Budget at that moment"]]), "cut")}
    ${cutField(`Never below (${c}, 0 = no floor)`, cutInput("floor", r.floor), "cut")}
    ${cutField("Active from (store clock)", cutSelect("hour_from", r.hour_from, hours.slice(0, 24)))}
    ${cutField("Active until", cutSelect("hour_to", r.hour_to, hours.slice(1)))}
    ${cutField("At midnight", cutSelect("reset", r.reset ? 1 : 0, [[0, "Keep the new budget"], [1, "Put the budget back"]]))}
    ${cutField("How", cutSelect("mode", r.mode, [["auto", "Autopilot: does it"], ["ask", "Ask me first (Manage tab)"]]))}
    <div class="row-actions"><button class="btn btn-sm" data-cutsave="${r.id}">Save</button></div></div>`;
}

function cutShowFields(box) {
  const v = (f) => (box.querySelector(`[data-f="${f}"]`) || {}).value;
  const on = new Set([v("trigger"), v("action"), v("trigger") === "campaign" ? v("camp_by") : ""]);
  for (const l of box.querySelectorAll("[data-show]")) l.hidden = !l.dataset.show.split(" ").every((x) => on.has(x));
}

/* Which campaigns a rule covers: a dropdown of switch · name · budget (from the server's campaign list), plus "All campaigns". */
function cutBudgetTxt(x, c) {
  if (x.daily_budget) return `${c} ${num(x.daily_budget, 2)}`;
  if (x.lifetime_budget) return `${c} ${num(x.lifetime_budget, 2)} lifetime`;
  return x.known ? "on ad sets" : "–";
}
function cutCampaigns(r, manage, c) {
  const picked = r.campaigns || {};
  const all = r.scope === "all";
  const list = (state.cutCamps || []).slice();
  for (const [id, name] of Object.entries(picked)) if (!list.some((x) => x.id === id)) list.push({ id, name });
  const dis = !manage || all ? " disabled" : "";
  const rows = list.map((x) => `<label class="ma-sw-row${all ? " dim" : ""}"><span class="ma-sw"><input type="checkbox" data-cutcamp="${r.id}" data-id="${esc(x.id)}" data-name="${esc(x.name)}"${all || picked[x.id] !== undefined ? " checked" : ""}${dis}><i></i></span>
      <span class="nm" title="${esc(x.name)}">${esc(x.name)}</span><span class="bud">${cutBudgetTxt(x, c)}</span></label>`).join("");
  const n = Object.keys(picked).length;
  return `<details class="ma-cut-camps" data-open="camps-${r.id}"${state.logOpen[`camps-${r.id}`] ? " open" : ""}>
    <summary><span>Applies to: <b>${all ? "All campaigns" : `${n} campaign${n === 1 ? "" : "s"}`}</b></span><span class="caret">▾</span></summary>
    <div class="ma-sw-list">
      <label class="ma-sw-row ma-sw-all"><span class="ma-sw"><input type="checkbox" data-cutall="${r.id}"${all ? " checked" : ""}${manage ? "" : " disabled"}><i></i></span>
        <span class="nm">All campaigns <span class="ma-sub">(new ones included)</span></span><span class="bud">Daily budget</span></label>
      ${state.cutCamps ? rows || '<p class="hint">No campaigns ran in the last 7 days.</p>' : '<p class="hint">Loading campaigns…</p>'}</div></details>`;
}

function cutWatch(rows, c, at) {
  if (!rows || !rows.length) return "";
  return `<table class="table ma-cut-watch"><thead><tr><th>Campaign</th><th>Budget at start</th><th>Budget now</th><th>Spend today</th><th>% of start</th><th>Starts at</th><th>ROAS</th><th>Rule</th></tr></thead>
    <tbody>${rows.map((w) => `<tr><td>${esc(w.name)}</td><td>${w.start == null ? "–" : `${c} ${num(w.start, 2)}`}</td><td>${w.budget == null ? "–" : `${c} ${num(w.budget, 2)}`}</td>
      <td>${c} ${num(w.spend, 2)}</td><td>${w.spent_pct == null ? "–" : pct(w.spent_pct)}</td><td>${w.trigger_at == null ? "–" : `${c} ${num(w.trigger_at, 2)}`}</td>
      <td>${roasTxt(w.roas)}</td><td><span class="ma-pill ${CUT_STATE[w.state] || "off"}">${esc(w.state)}</span></td></tr>`).join("")}</tbody></table>
    ${at ? `<p class="hint">As of ${when(at)}.</p>` : ""}`;
}

async function loadCut(force = false) {
  const box = $("cutrule");
  if (!force && box.querySelector("details.ma-cut-set[open], .ma-cut-preview")) return;      // someone is editing / reading a check
  let s;
  try { s = await api(`/api/metaads/cutrules?store=${state.store}`); } catch { box.hidden = true; return; }
  state.cut = s;
  const manage = window.pdLevel === "manage";
  const c = s.currency;
  box.hidden = false;
  box.classList.toggle("on", s.rules.some((r) => r.on));
  box.innerHTML = `<div class="ma-cut-top"><b>Budget cut rules</b>
      <span class="hint">Each rule has its own switch. Checked every 5 minutes. Every change is logged with Undo on the Manage tab.</span>
      ${manage ? `<span class="ma-cut-add">${cutSelect("tpl", "campaign", Object.entries(s.templates))}<button class="btn btn-sm" id="cutAdd">+ Add rule</button></span>` : ""}</div>
    ${s.rules.map((r) => {
      const st = r.state || {};
      const today = st.checked_day === s.today ? (st.runs || []) : [];
      const done = st.done && st.done.day === s.today ? Object.values(st.done.items || {}) : [];
      const status = !r.on ? (r.stopped_at ? `Switched off ${when(r.stopped_at)}.` : "Off.")
        : r.trigger === "account" ? (today.length ? `Today: ${today.map((x) => `${c} ${num(x.point, 0)} → ROAS ${(+x.roas).toFixed(2)}${x.cut ? `, ${x.cut} cut` : ", nothing cut"}`).join(" · ")}.` : `Watching today${st.watch ? ` (${esc(st.watch.note)})` : ""}.`)
        : `Today: ${done.filter((x) => ["cut", "pause", "proposed"].includes(x.action)).length} acted on${st.watch ? `, ${esc(st.watch.note)}` : ""}.`;
      const feed = (r.feed || []).slice(0, 10).map((f) => `<li class="${f.status === "done" ? "down" : ""}"><time>${when(f.done_at || f.created_at)}</time>
        <span>${f.status === "done" ? (f.source.startsWith("strategy:cutback") ? "▲ " : "▼ ") : f.status === "proposed" ? "… " : ""}${esc(f.summary || f.error || f.name)}${f.status === "proposed" ? " (waiting for Do it)" : ""}</span></li>`).join("");
      return `<div class="ma-cut ${r.on ? "on" : ""}" data-rule="${r.id}">
        <div class="ma-strat-head">
          <div><span class="ma-pill ${r.on ? "on" : "off"}">${r.on ? (r.mode === "ask" ? "ON · ASK FIRST" : "AUTOPILOT ON") : "Off"}</span> <b>${esc(r.name)}</b>
            <p class="hint">${cutSentence(r, c)} ${status}</p></div>
          <div class="ma-strat-acts">
            ${manage ? `<button class="btn ${r.on ? "btn-danger ma-off" : "btn-primary"}" data-cuttoggle="${r.id}">${r.on ? "Turn off this rule" : "Turn on"}</button>` : ""}
            <span class="ma-cut-btns"><button class="btn btn-sm btn-ghost" data-cutcheck="${r.id}">Check now</button>
            ${manage && !r.on ? `<button class="btn btn-sm btn-ghost" data-cutdel="${r.id}">Remove</button>` : ""}</span>
            ${manage ? `<details class="ma-cut-set"><summary>Settings</summary>${cutSettings(r, c)}</details>` : ""}
          </div>
        </div>
        ${cutCampaigns(r, manage, c)}
        <div class="ma-cut-check" id="cutCheck-${r.id}"></div>
        ${r.trigger === "campaign" && r.on && st.watch && st.watch.rows && st.watch.rows.length ? `<details class="ma-log" data-log="watch-${r.id}"${state.logOpen[`watch-${r.id}`] ? " open" : ""}><summary>Today per campaign</summary>${cutWatch(st.watch.rows, c, st.watch.at)}</details>` : ""}
        ${feed ? `<details class="ma-log" data-log="cut-${r.id}"${state.logOpen[`cut-${r.id}`] ? " open" : ""}><summary>Changes made (last ${(r.feed || []).slice(0, 10).length})</summary><ul class="ma-feed">${feed}</ul></details>` : ""}
      </div>`;
    }).join("")}`;
  keepLogs(box);
  for (const d of box.querySelectorAll("details[data-open]")) d.addEventListener("toggle", () => { state.logOpen[d.dataset.open] = d.open; });
  for (const f of box.querySelectorAll(".ma-cut-fields")) { cutShowFields(f); f.addEventListener("change", () => cutShowFields(f)); }
  if (!state.cutCamps) {
    api(`/api/metaads/overview?store=${state.store}&level=campaign&period=7&active=0`).then((d) => {
      state.cutCamps = (d.rows || []).map((x) => ({ id: x.id, name: x.name, daily_budget: x.daily_budget, lifetime_budget: x.lifetime_budget, known: true }));
      loadCut(true);
    }).catch(() => { state.cutCamps = []; });
  }
}

async function cutPut(rid, body, msg) {
  try { await api(`/api/metaads/cutrules/${state.store}/${rid}`, { method: "PUT", json: body }); if (msg) toast(msg); loadCut(true); return true; }
  catch (e) { toast(e.message, true); loadCut(true); return false; }
}

$("cutrule").addEventListener("click", async (e) => {
  const el = e.target.closest("[data-cuttoggle],[data-cutsave],[data-cutcheck],[data-cutdel],#cutAdd");
  if (!el) return;
  const rules = (state.cut || {}).rules || [];
  const c = (state.cut || {}).currency || "";
  if (el.id === "cutAdd") {
    try {
      const tpl = $("cutrule").querySelector('[data-f="tpl"]').value;
      await api(`/api/metaads/cutrules/${state.store}`, { json: { template: tpl } });
      toast("Rule added (off). Pick its campaigns and settings, then Turn on.");
      loadCut(true);
    } catch (err) { toast(err.message, true); }
  } else if (el.dataset.cuttoggle) {
    const r = rules.find((x) => x.id === el.dataset.cuttoggle);
    const on = !r.on;
    if (on && !confirm(`Turn on “${r.name}”?\n\n${cutSentence(r, c).replace(/<[^>]+>/g, "")}\n\n${r.mode === "ask" ? "Changes wait for Do it on the Manage tab." : "EcomVital makes these changes on Meta without asking."}`)) return;
    cutPut(r.id, { on }, on ? "Rule on." : "Rule off.");
  } else if (el.dataset.cutsave) {
    const body = {};
    for (const f of $("cutrule").querySelectorAll(`.ma-cut-fields[data-rule="${el.dataset.cutsave}"] [data-f]`)) body[f.dataset.f] = f.value;
    body.reset = body.reset === "1";
    cutPut(el.dataset.cutsave, body, "Saved");
  } else if (el.dataset.cutdel) {
    const r = rules.find((x) => x.id === el.dataset.cutdel);
    if (!confirm(`Remove the rule “${r.name}”? Its past changes stay in the log.`)) return;
    try { await api(`/api/metaads/cutrules/${state.store}/${r.id}`, { method: "DELETE" }); toast("Removed"); loadCut(true); }
    catch (err) { toast(err.message, true); }
  } else if (el.dataset.cutcheck) {
    const out = $(`cutCheck-${el.dataset.cutcheck}`);
    out.className = "ma-cut-check ma-cut-preview";
    out.innerHTML = '<p class="hint">Checking with Meta…</p>';
    try {
      const d = await api(`/api/metaads/cutrules/preview?store=${state.store}&rule=${el.dataset.cutcheck}`);
      out.innerHTML = `<p class="hint"><b>Check now (nothing changed):</b> ${d.cuts.length ? `would act on ${d.cuts.length}: ${d.cuts.map((x) => `${esc(x.name)} (${esc(x.why)})`).join("; ")}` : esc(d.note) || "nothing to do."}
        <button class="btn btn-sm btn-ghost" data-cutclose="1">Close</button></p>${cutWatch(d.watch, d.currency, d.at)}`;
    } catch (err) { out.innerHTML = `<p class="hint">${esc(err.message)} <button class="btn btn-sm btn-ghost" data-cutclose="1">Close</button></p>`; }
  }
});
$("cutrule").addEventListener("click", (e) => {
  const b = e.target.closest("[data-cutclose]");
  if (b) { const box = b.closest(".ma-cut-check"); box.className = "ma-cut-check"; box.innerHTML = ""; }
});
$("cutrule").addEventListener("change", (e) => {
  const al = e.target.closest("[data-cutall]");
  if (al) {
    const r = ((state.cut || {}).rules || []).find((x) => x.id === al.dataset.cutall) || {};
    const body = { scope: al.checked ? "all" : "picked" };
    if (!al.checked && !Object.keys(r.campaigns || {}).length) {     // start from every campaign on; switch off the ones to leave out
      body.campaigns = Object.fromEntries((state.cutCamps || []).map((x) => [x.id, x.name]));
    }
    cutPut(al.dataset.cutall, body, al.checked ? "Rule covers all campaigns." : "Now switch off the campaigns this rule should leave alone.");
    return;
  }
  const cb = e.target.closest("[data-cutcamp]");
  if (!cb) return;
  cutPut(cb.dataset.cutcamp, { campaign: { id: cb.dataset.id, name: cb.dataset.name, on: cb.checked } },
    `${cb.dataset.name}: ${cb.checked ? "in the rule" : "out of the rule"}`);
});

/* ------------------------------------------------ overview */

async function loadOverview(fresh = false, quiet = false) {
  if (!state.store) return;
  if (!quiet) $("table").innerHTML = '<tbody><tr><td class="empty">Reading Meta…</td></tr></tbody>';
  try {
    const p = $("days").value;
    if (p === "custom" && !($("dFrom").value && $("dTo").value)) {
      $("table").innerHTML = '<tbody><tr><td class="empty">Pick the two dates, then Show.</td></tr></tbody>';
      return;
    }
    const dates = p === "custom" ? `&start=${$("dFrom").value}&end=${$("dTo").value}` : "";
    const view = `&active=${$("activeOnly").checked ? 1 : 0}&q=${encodeURIComponent($("q").value.trim())}`;
    state.data = await api(`/api/metaads/overview?store=${state.store}&level=${state.level}&period=${p}${dates}${view}${fresh ? "&fresh=1" : ""}`);
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
  const shown = new Set(d.shown || []);
  const rows = d.rows.filter((r) => shown.has(r.id));
  const f = d.foot, campaign = state.level === "campaign";
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
    || `<tr><td class="empty" colspan="16">Nothing ${$("activeOnly").checked ? "running or spending " : ""}in this window.</td></tr>`}</tbody>
    ${rows.length > 1 ? `<tfoot><tr class="ma-total">
      <td class="ma-name"><span><span class="name">Total</span><span class="sub">${num(f.rows)} ${LEVEL[state.level].toLowerCase()}s shown</span></span></td><td></td>
      <td>${footBudget(f.budget)}</td><td>${money(f.spend)}</td><td>${num(f.impressions)}</td><td>${num(f.clicks)}</td>
      <td>${pct(f.ctr)}</td><td>${f.cpc === null ? "–" : money(f.cpc)}</td><td>${num(f.atc)}</td><td>${num(f.purchases)}</td>
      <td>${money(f.value)}</td><td><b>${roasTxt(f.roas)}</b></td>
      <td class="ma-px">${num(f.px_purchases)}</td><td class="ma-px">${money(f.px_value)}</td><td class="ma-px"><b>${roasTxt(f.px_roas)}</b></td>
      ${campaign ? `<td class="ma-auto">${f.auto_raises ? `<b>+${money(f.auto_added)}</b><span class="sub">${num(f.auto_raises)}×</span>` : "–"}</td>` : ""}</tr></tfoot>` : ""}`;
}

/* The total row's daily budget: running ones; paused ones and budgets kept elsewhere in small print. */
function footBudget(b) {
  if (!b) return "";
  const where = state.level === "campaign" ? "on their ad sets" : "on their campaign";
  const notes = [];
  if (b.all !== b.running) notes.push(`incl. paused: ${money(b.all)}`);
  if (b.elsewhere_n) notes.push(`${num(b.elsewhere_n)} with the budget ${where}, not added`);
  if (b.lifetime_n) notes.push(`${num(b.lifetime_n)} lifetime budget${b.lifetime_n === 1 ? "" : "s"}, not added`);
  return `<b title="Daily budgets of the running ${LEVEL[state.level].toLowerCase()}s shown">${b.running_n ? money(b.running) : "–"}</b>`
    + `<span class="sub">${b.running_n ? `${num(b.running_n)} running` : "none running"}</span>`
    + notes.map((n) => `<span class="sub">${esc(n)}</span>`).join("");
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

const SOURCE = (s) => s === "ai" ? "Claude" : s === "manual" ? "By hand" : s.startsWith("rule:") ? "Rule" : s.startsWith("undo:") ? "Undo"
  : s.startsWith("strategy:cutback") ? "Budget put back" : s.startsWith("strategy:cut") ? "Budget-cut rule" : s === "strategy:up" ? "$10 up" : s;
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
  $("launcherPage").hidden = p !== "launcher";
  for (const id of ["strategy", "cutrule"]) $(id).classList.toggle("ma-hide", p === "launcher");
  if (p === "launcher") { if (window.loadLauncher) window.loadLauncher(); }
  else if (p === "manage") loadManage(); else loadOverview();
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
$("store").onchange = () => { state.store = +$("store").value; state.cutCamps = null; storeInfo(); loadStrategy(); showPage(state.page); };
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
let viewTimer;
$("activeOnly").onchange = $("q").oninput = () => {      // the server picks the rows and adds up the total row
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => state.data && loadOverview(false, true), 250);
};
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
