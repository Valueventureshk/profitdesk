/* Meta Ads → Launcher: approved advertorial / listing tasks → one campaign + one ad set, one ad per task (several
   creatives = one flexible ad). Built paused on Meta and switched on only when everything exists. Uses $, esc, api,
   toast, state and when from metaads.js. Every choice lives in L.s (the settings sent to the server). */

const L = { s: null, o: null, tasks: [], picked: new Set(), preset: "", draft: "", runsTimer: null,
  want: new URLSearchParams(location.search).get("tasks") || "" };          // from a task's "Launch ads on Meta" button

window.loadLauncher = async function loadLauncher() {
  const box = $("lx");
  if (!state.store) { box.innerHTML = '<p class="hint">Pick a store.</p>'; return; }
  box.innerHTML = '<section class="panel ma-card"><p class="hint">Reading the ad account…</p></section>';
  try {
    const [o, r] = await Promise.all([api(`/api/metaads/launcher/options?store=${state.store}`),
      api(`/api/metaads/launcher/ready?store=${state.store}`)]);
    L.o = o; L.s = o.settings; L.tasks = r.tasks; L.picked = new Set(); L.preset = ""; L.draft = "";
    if (L.want) for (const id of L.want.split(",")) if (L.tasks.some((t) => String(t.id) === id && !t.problems.length)) L.picked.add(+id);
    L.want = "";
    render();
    loadRuns();
  } catch (e) { box.innerHTML = `<section class="panel ma-card"><p class="hint">${esc(e.message)}</p></section>`; }
};

const manageLvl = () => window.pdLevel === "manage";
const opt = (list, val, label = (x) => x.name) => list.map((x) => `<option value="${esc(x.id)}"${String(x.id) === String(val) ? " selected" : ""}>${esc(label(x))}</option>`).join("");
const optMap = (obj, val) => Object.entries(obj).map(([k, v]) => `<option value="${esc(k)}"${k === val ? " selected" : ""}>${esc(v)}</option>`).join("");
const chk = (path, on, label, hint = "") => `<label class="lx-check"><input type="checkbox" data-s="${path}"${on ? " checked" : ""}> <span>${label}${hint ? `<em>${hint}</em>` : ""}</span></label>`;
const field = (label, inner, hint = "") => `<label class="field"><span>${label}</span>${inner}${hint ? `<em class="hint">${hint}</em>` : ""}</label>`;
const inp = (path, val, extra = "") => `<input class="control" data-s="${path}" value="${esc(val ?? "")}" ${extra}>`;
const sel = (path, inner) => `<select class="control" data-s="${path}">${inner}</select>`;

function render() {
  const s = L.s, o = L.o, c = s.campaign, a = s.adset, ad = s.ad;
  const cur = o.currency;
  const ready = L.tasks.filter((t) => !t.problems.length), blocked = L.tasks.filter((t) => t.problems.length);
  const taskRow = (t) => `<label class="lx-task${t.problems.length ? " off" : ""}">
      <input type="checkbox" data-task="${t.id}"${L.picked.has(t.id) ? " checked" : ""}${t.problems.length ? " disabled" : ""}>
      <span><b>T-${t.id} · ${esc(t.title)}</b>
        <em>${t.kind === "advertorial" ? "Advertorial" : "Listing"} · ${t.creatives.length} creative${t.creatives.length === 1 ? "" : "s"}
        (${t.creatives.filter((x) => x.video).length} video) · ${t.creatives.length > 1 ? "one flexible ad" : "one ad"}
        ${t.problems.length ? ` · <span class="lx-bad">${esc(t.problems.join(", "))}</span>` : ""}</em>
        <em>${esc(t.copy.headline || "")}${t.link ? ` → ${esc(t.link.replace(/^https?:\/\//, "").slice(0, 60))}` : ""}</em></span></label>`;
  const presets = o.presets || [], drafts = o.drafts || [];
  $("lx").innerHTML = `
  <section class="panel ma-card">
    <div class="panel-head"><h2>1. Pick tasks</h2><span class="hint">All picked tasks go into one ad set · ${esc(o.account)} · ${esc(cur)} · clock ${esc(o.timezone)}</span></div>
    ${ready.length ? ready.map(taskRow).join("") : '<p class="hint">No approved advertorial or listing task for this store yet.</p>'}
    ${blocked.length ? `<details class="lx-more"><summary>${blocked.length} not ready yet</summary>${blocked.map(taskRow).join("")}</details>` : ""}
    ${drafts.length ? `<div class="lx-drafts"><b>Saved for an admin:</b> ${drafts.map((d) => `<span class="lx-chip">${esc(d.preset || "Draft")} · ${d.task_ids.map((i) => "T-" + i).join(", ")} · ${esc((d.by || "").split("@")[0])}
       <button class="btn btn-sm" data-draft="${esc(d.id)}">Open</button><button class="btn btn-sm btn-ghost" data-undraft="${esc(d.id)}">×</button></span>`).join("")}</div>` : ""}
  </section>
  <section class="panel ma-card lx-presetbar">
    ${field("Preset", `<select class="control" id="lxPreset"><option value="">— start from the last settings —</option>${presets.map((p) => `<option${p.name === L.preset ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select>`)}
    ${manageLvl() ? `<button class="btn" id="lxSavePreset">Save these settings as a preset</button>${L.preset ? `<button class="btn btn-ghost" id="lxDelPreset">Remove preset</button>` : ""}` : ""}
  </section>
  <section class="panel ma-card">
    <div class="panel-head"><h2>2. Campaign</h2></div>
    <div class="lx-grid">
      ${field("Campaign", sel("campaign.mode", `<option value="new"${c.mode === "new" ? " selected" : ""}>New campaign (Sales)</option><option value="existing"${c.mode === "existing" ? " selected" : ""}>Add to an existing campaign</option>`))}
      ${c.mode === "existing" ? field("Existing campaign", sel("campaign.existing_id", `<option value="">Pick…</option>${opt(o.campaigns, c.existing_id, (x) => `${x.name}${x.cbo ? " (campaign budget)" : ""} · ${x.status || ""}`)}`))
        : field("Campaign name", inp("campaign.name", c.name), "{store} {date} {preset} are filled in")}
      ${c.mode === "new" ? field("Budget", sel("campaign.budget_mode", `<option value="abo"${c.budget_mode === "abo" ? " selected" : ""}>On the ad set (ABO)</option><option value="cbo"${c.budget_mode === "cbo" ? " selected" : ""}>On the campaign (CBO / Advantage+ budget)</option>`)) : ""}
      ${field(`Daily budget (${esc(cur)})`, inp("campaign.daily_budget", c.daily_budget, 'inputmode="decimal"'), c.mode === "existing" ? "Used for the new ad set unless the campaign holds the budget" : "")}
      ${field("Bid strategy", sel("campaign.bid_strategy", optMap(o.choices.bid, c.bid_strategy)))}
      ${["COST_CAP", "LOWEST_COST_WITH_BID_CAP"].includes(c.bid_strategy) ? field(`${c.bid_strategy === "COST_CAP" ? "Cost per purchase goal" : "Bid cap"} (${esc(cur)})`, inp("campaign.bid_amount", c.bid_amount, 'inputmode="decimal"')) : ""}
      ${c.bid_strategy === "LOWEST_COST_WITH_MIN_ROAS" ? field("ROAS goal", inp("campaign.roas_floor", c.roas_floor, 'inputmode="decimal" placeholder="e.g. 2.0"')) : ""}
    </div>
  </section>
  <section class="panel ma-card">
    <div class="panel-head"><h2>3. Ad set</h2></div>
    <div class="lx-grid">
      ${field("Ad set name", inp("adset.name", a.name), "{tasks} {countries} {date} are filled in")}
      ${field("Countries", inp("adset.countries", a.countries, 'placeholder="AU" style="text-transform:uppercase"'), "Two-letter codes, e.g. AU or US CA")}
      ${field("Age from", inp("adset.age_min", a.age_min, 'inputmode="numeric"'))}
      ${field("Age to", inp("adset.age_max", a.age_max, 'inputmode="numeric"'), "65 = 65 and over")}
      ${field("Gender", sel("adset.genders", optMap({ all: "Everyone", women: "Women", men: "Men" }, a.genders)))}
      ${field("Pixel", sel("adset.pixel_id", `<option value="">Pick…</option>${opt(o.pixels, a.pixel_id)}`))}
      ${field("Optimise for", sel("adset.event", optMap(o.choices.events, a.event)))}
      ${field("Attribution", sel("adset.attribution", optMap(o.choices.attribution, a.attribution)))}
    </div>
    <div class="lx-switches">
      ${chk("adset.advantage_audience", a.advantage_audience, "Advantage+ audience", " — Meta may go beyond your age / gender as suggestions")}
      ${chk("adset.placements_adv", a.placements !== "manual", "Advantage+ placements", " — all of Facebook, Instagram, Messenger, Audience Network")}
    </div>
    ${a.placements === "manual" ? `<div class="lx-switches">
      ${["facebook", "instagram", "messenger", "audience_network"].map((p) => `<label class="lx-check"><input type="checkbox" data-plat="${p}"${(a.platforms || []).includes(p) ? " checked" : ""}> ${p.replace("_", " ")}</label>`).join("")}
    </div>` : ""}
    <div class="lx-grid">
      ${field("Start", sel("adset.start", `<option value="now"${a.start !== "at" ? " selected" : ""}>As soon as it's launched</option><option value="at"${a.start === "at" ? " selected" : ""}>At a set time</option>`))}
      ${a.start === "at" ? field(`Start at (${esc(o.timezone)})`, inp("adset.start_at", a.start_at, 'type="datetime-local"'), `<a href="#" id="lxTomorrow">Tomorrow 00:01</a>`) : ""}
      ${field(`End (optional, ${esc(o.timezone)})`, inp("adset.end_at", a.end_at, 'type="datetime-local"'))}
    </div>
  </section>
  <section class="panel ma-card">
    <div class="panel-head"><h2>4. Ads</h2><span class="hint">One ad per task; copy and landing page come from the task</span></div>
    <div class="lx-grid">
      ${field("Ad name", inp("ad.name", ad.name), "{task} {date} are filled in")}
      ${field("Facebook Page", sel("ad.page_id", `<option value="">Pick…</option>${opt(o.pages, ad.page_id)}`))}
      ${field("Instagram account", sel("ad.instagram_id", `<option value="">Use the Facebook Page</option>${opt(o.instagram, ad.instagram_id)}`))}
      ${field("Button", sel("ad.cta", optMap(o.choices.cta, ad.cta)))}
      ${field("Tasks with several creatives", sel("ad.multi", optMap({ flexible: "One flexible ad (Meta mixes them)", separate: "One ad per creative" }, ad.multi)))}
    </div>
    <div class="lx-switches">${chk("ad.utm", ad.utm, "Tracking tags on the link", " — so EcomVital credits the sales to this campaign / ad set / ad")}</div>
    <details class="lx-enh" open><summary>Advantage+ creative enhancements <em class="hint">(${Object.keys(o.choices.enhancements).filter((k) => (ad.enhancements || {})[k]).length} on)</em></summary>
      <div class="lx-switches lx-cols">${Object.entries(o.choices.enhancements).map(([k, v]) => chk(`enh.${k}`, (ad.enhancements || {})[k], esc(v))).join("")}</div>
    </details>
  </section>
  <section class="panel ma-card">
    <div class="panel-head"><h2>5. Check, then launch</h2><span class="hint">${L.picked.size} task${L.picked.size === 1 ? "" : "s"} picked</span></div>
    <div class="row-actions lx-acts">
      <button class="btn" id="lxCheck">Check with Meta</button>
      ${manageLvl() ? `<button class="btn btn-primary" id="lxLaunch">Launch</button>` : `<button class="btn btn-primary" id="lxDraft">Save for an admin to launch</button>`}
    </div>
    <div id="lxResult"></div>
    <div id="lxRuns"></div>
  </section>`;
}

function setPath(path, val) {
  const [part, key] = path.split(".");
  if (part === "enh") { L.s.ad.enhancements = { ...(L.s.ad.enhancements || {}), [key]: val }; return; }
  if (path === "adset.placements_adv") { L.s.adset.placements = val ? "advantage" : "manual"; return; }
  L.s[part][key] = val;
}

document.addEventListener("change", (e) => {
  if (!e.target.closest("#lx")) return;
  const el = e.target;
  if (el.dataset.task) { el.checked ? L.picked.add(+el.dataset.task) : L.picked.delete(+el.dataset.task); render(); return; }
  if (el.dataset.plat) {
    const p = new Set(L.s.adset.platforms || []);
    el.checked ? p.add(el.dataset.plat) : p.delete(el.dataset.plat);
    L.s.adset.platforms = [...p];
    return;
  }
  if (el.id === "lxPreset") {
    L.preset = el.value;
    const p = (L.o.presets || []).find((x) => x.name === el.value);
    if (p) L.s = JSON.parse(JSON.stringify(p.settings));
    render();
    return;
  }
  if (!el.dataset.s) return;
  setPath(el.dataset.s, el.type === "checkbox" ? el.checked : el.value);
  if (["campaign.mode", "campaign.bid_strategy", "adset.start", "adset.placements_adv", "campaign.budget_mode"].includes(el.dataset.s)
      || el.dataset.s.startsWith("enh.")) render();
});
document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.closest("#lx") && el.dataset.s && el.type !== "checkbox" && el.tagName !== "SELECT") setPath(el.dataset.s, el.value);
});

const body = () => ({ store: state.store, task_ids: [...L.picked], settings: L.s, preset: L.preset, draft_id: L.draft || undefined });

document.addEventListener("click", async (e) => {
  const t = e.target;
  if (!t.closest || !t.closest("#lx")) return;
  if (t.id === "lxTomorrow") {
    e.preventDefault();
    const d = new Date(Date.now() + 864e5);
    L.s.adset.start_at = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T00:01`;
    render();
    return;
  }
  if (t.id === "lxSavePreset") {
    const name = prompt("Name this preset (e.g. ABO test AUD 30, Advantage+ audience off):", L.preset || "");
    if (!name) return;
    try { const r = await api(`/api/metaads/launcher/presets/${state.store}`, { json: { name, settings: L.s } }); L.o.presets = r.presets; L.preset = name; render(); toast("Preset saved"); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (t.id === "lxDelPreset") {
    if (!confirm(`Remove the preset “${L.preset}”?`)) return;
    try { const r = await api(`/api/metaads/launcher/presets/${state.store}`, { json: { remove: L.preset } }); L.o.presets = r.presets; L.preset = ""; render(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (t.dataset.draft) {
    const d = (L.o.drafts || []).find((x) => x.id === t.dataset.draft);
    if (d) { L.s = JSON.parse(JSON.stringify(d.settings)); L.picked = new Set(d.task_ids); L.preset = d.preset || ""; L.draft = d.id; render(); toast("Draft opened: check it, then Launch"); }
    return;
  }
  if (t.dataset.undraft) {
    if (!confirm("Remove this saved draft?")) return;
    try { const r = await api(`/api/metaads/launcher/drafts/${state.store}`, { json: { remove: t.dataset.undraft } }); L.o.drafts = r.drafts; render(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (t.id === "lxCheck") { await check(); return; }
  if (t.id === "lxDraft") {
    if (!L.picked.size) { toast("Pick at least one task.", true); return; }
    try { const r = await api(`/api/metaads/launcher/drafts/${state.store}`, { json: body() }); L.o.drafts = r.drafts; render(); toast("Saved: an admin can open it and launch"); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (t.id === "lxLaunch") {
    const ok = await check();
    if (!ok) return;
    const startTxt = L.s.adset.start === "at" ? `starting ${L.s.adset.start_at.replace("T", " ")} (${L.o.timezone})` : "starting now";
    if (!confirm(`Launch ${L.picked.size} task${L.picked.size === 1 ? "" : "s"} on Meta, ${startTxt}? Ads go ACTIVE and can spend.`)) return;
    try { const r = await api("/api/metaads/launcher/launch", { json: body() }); toast(r.carrying_on ? "Carrying on the earlier launch…" : "Launching… you can leave this page"); L.draft = ""; loadRuns(true); }
    catch (err) { toast(err.message, true); }
  }
});

async function check() {
  const out = $("lxResult");
  if (!L.picked.size) { toast("Pick at least one task.", true); return false; }
  out.innerHTML = '<p class="hint">Checking…</p>';
  try {
    const r = await api("/api/metaads/launcher/check", { json: body() });
    const bad = [...r.missing.map((m) => "Fill in: " + m), ...r.task_problems];
    out.innerHTML = `<div class="lx-check-out">
      <p><b>Campaign:</b> ${esc(r.names.campaign)}<br><b>Ad set:</b> ${esc(r.names.adset)}</p>
      <ul>${r.names.ads.map((x) => `<li>${esc(x.name)} <em class="hint">· ${esc(x.kind)}</em></li>`).join("")}</ul>
      ${bad.length ? `<p class="lx-bad">${bad.map(esc).join("<br>")}</p>` : ""}
      ${r.meta_error ? `<p class="lx-bad">Meta: ${esc(r.meta_error)}</p>` : ""}
      ${r.meta.length ? `<p class="lx-ok">${r.meta.map(esc).join(" ")}</p>` : ""}
      ${!bad.length && !r.meta_error ? `<p class="lx-ok">Ready${r.start && r.start !== "now" ? ` · starts ${esc(r.start)}` : " · starts as soon as it's launched"}.</p>` : ""}
    </div>`;
    return !bad.length && !r.meta_error;
  } catch (err) { out.innerHTML = `<p class="lx-bad">${esc(err.message)}</p>`; return false; }
}

async function loadRuns(poll = false) {
  clearTimeout(L.runsTimer);
  try {
    const r = await api(`/api/metaads/launcher/runs?store=${state.store}`);
    const label = { running: "Building", waiting: "Waiting for Meta", failed: "Stopped", done: "Done" };
    $("lxRuns").innerHTML = r.runs.length ? `<h4 class="lx-h4">Launches</h4><table class="table"><tbody>${r.runs.map((x) => `<tr>
        <td>#${x.id}</td><td>${x.task_ids.map((i) => "T-" + i).join(", ")}</td>
        <td><b>${esc(label[x.status] || x.status)}</b>${x.step ? ` · ${esc(x.step)}` : ""}${x.error ? `<br><span class="lx-bad">${esc(x.error)}</span>` : ""}</td>
        <td>${x.ads} ad${x.ads === 1 ? "" : "s"}</td><td>${esc(when(x.at))}</td>
        <td>${x.ads_manager ? `<a href="${esc(x.ads_manager)}" target="_blank">Ads Manager ↗</a>` : ""}</td></tr>`).join("")}</tbody></table>` : "";
    if (r.runs.some((x) => x.running) || poll) L.runsTimer = setTimeout(() => loadRuns(r.runs.some((x) => x.running)), 5000);
    if (!r.runs.some((x) => x.running) && poll === false) {
      const d = await api(`/api/metaads/launcher/ready?store=${state.store}`);
      if (d.tasks.length !== L.tasks.length) { L.tasks = d.tasks; }
    }
  } catch { /* the page still works without the list */ }
}
