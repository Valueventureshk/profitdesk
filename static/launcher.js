/* Meta Ads → Launcher. Two ways in:
   - From approved tasks: advertorial / listing tasks → one campaign + one ad set, one ad per task.
   - Bulk upload: creatives uploaded here (no task), several texts, split into ad sets / ads as picked, into a new
     campaign, an existing campaign or an existing ad set.
   "Copy setup from a campaign" fills every setting from a live campaign. Built paused on Meta and switched on only when
   everything exists. Uses $, esc, api, toast, state and when from metaads.js. Every figure / check comes from the server. */

const L = { s: null, o: null, tasks: [], picked: new Set(), preset: "", draft: "", runsTimer: null,
  want: new URLSearchParams(location.search).get("tasks") || "",          // from a task's "Launch ads on Meta" button
  mode: "tasks", media: [], sel: [], bp: null, adsets: null,
  job: { bodies: [""], titles: [""], descriptions: [""], link: "", adsets: "one", ads: "each", group_size: 3 } };
try { L.mode = L.want ? "tasks" : (localStorage.getItem("pdLxMode") || "tasks"); } catch { /* private mode */ }

window.loadLauncher = async function loadLauncher() {
  const box = $("lx");
  if (!state.store) { box.innerHTML = '<p class="hint">Pick a store.</p>'; return; }
  box.innerHTML = '<section class="panel ma-card"><p class="hint">Reading the ad account…</p></section>';
  try {
    const [o, r, m] = await Promise.all([api(`/api/metaads/launcher/options?store=${state.store}`),
      api(`/api/metaads/launcher/ready?store=${state.store}`), api(`/api/metaads/bulk/media?store=${state.store}`)]);
    L.o = o; L.s = o.settings; L.tasks = r.tasks; L.picked = new Set(); L.preset = ""; L.draft = ""; L.bp = null;
    L.media = m.media; L.sel = []; L.adsets = null;
    if (L.want) for (const id of L.want.split(",")) if (L.tasks.some((t) => String(t.id) === id && !t.problems.length)) L.picked.add(+id);
    L.want = "";
    render();
    loadRuns();
  } catch (e) { box.innerHTML = `<section class="panel ma-card"><p class="hint">${esc(e.message)}</p></section>`; }
};

const manageLvl = () => window.pdLevel === "manage";
const canWork = () => ["work", "manage"].includes(window.pdLevel) || !window.pdLevel;
const opt = (list, val, label = (x) => x.name) => list.map((x) => `<option value="${esc(x.id)}"${String(x.id) === String(val) ? " selected" : ""}>${esc(label(x))}</option>`).join("");
const optMap = (obj, val) => Object.entries(obj).map(([k, v]) => `<option value="${esc(k)}"${String(k) === String(val) ? " selected" : ""}>${esc(v)}</option>`).join("");
const chk = (path, on, label, hint = "") => `<label class="lx-check"><input type="checkbox" data-s="${path}"${on ? " checked" : ""}> <span>${label}${hint ? `<em>${hint}</em>` : ""}</span></label>`;
const field = (label, inner, hint = "") => `<label class="field"><span>${label}</span>${inner}${hint ? `<em class="hint">${hint}</em>` : ""}</label>`;
const inp = (path, val, extra = "") => `<input class="control" data-s="${path}" value="${esc(val ?? "")}" ${extra}>`;
const sel = (path, inner) => `<select class="control" data-s="${path}">${inner}</select>`;
const bulk = () => L.mode === "bulk";
const intoAdset = () => bulk() && L.s.adset.mode === "existing";

/* ---------- sections */

function modeBar() {
  return `<div class="scm-pages lx-modes">
    <button data-lxmode="tasks"${!bulk() ? ' class="on"' : ""}>From approved tasks</button>
    <button data-lxmode="bulk"${bulk() ? ' class="on"' : ""}>Bulk upload</button></div>`;
}

function copyBar() {
  const o = L.o, bp = L.bp;
  return `<section class="panel ma-card">
    <div class="panel-head"><h2>Start from a setup</h2><span class="hint">Pick a saved preset, or copy every setting of a campaign that works</span></div>
    <div class="lx-presetbar">
      ${field("Preset", `<select class="control" id="lxPreset"><option value="">— the last settings used —</option>${(o.presets || []).map((p) => `<option${p.name === L.preset ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select>`)}
      ${manageLvl() ? `<button class="btn" id="lxSavePreset">Save these settings as a preset</button>${L.preset ? `<button class="btn btn-ghost" id="lxDelPreset">Remove preset</button>` : ""}` : ""}
    </div>
    <div class="lx-presetbar">
      ${field("Copy setup from a campaign", `<select class="control" id="lxBpCamp"><option value="">Pick a campaign…</option>${opt(o.campaigns, bp && bp.campaign.id, (x) => `${x.name} · ${x.status || ""}`)}</select>`)}
      ${bp && bp.adsets.length > 1 ? field("Ad set to copy", `<select class="control" id="lxBpSet">${opt(bp.adsets, bp.adset && bp.adset.id, (x) => `${x.name} · ${x.status || ""}`)}</select>`) : ""}
      <button class="btn" id="lxBpGo">Copy setup</button>
    </div>
    ${bp ? `<div class="lx-bp"><p><b>Copied from</b> ${esc(bp.campaign.name)}${bp.adset ? ` › ${esc(bp.adset.name)}` : ""}${bp.ad ? ` › ${esc(bp.ad)}` : ""}. Every setting below now matches it; change anything you like.</p>
      <ul>${bp.features.map((f) => `<li>✓ ${esc(f)}</li>`).join("")}</ul>
      ${bp.warnings.length ? `<ul class="lx-warn">${bp.warnings.map((w) => `<li>! ${esc(w)}</li>`).join("")}</ul>` : ""}</div>` : ""}
  </section>`;
}

function tasksSection() {
  const ready = L.tasks.filter((t) => !t.problems.length), blocked = L.tasks.filter((t) => t.problems.length);
  const drafts = L.o.drafts || [];
  const taskRow = (t) => `<label class="lx-task${t.problems.length ? " off" : ""}">
      <input type="checkbox" data-task="${t.id}"${L.picked.has(t.id) ? " checked" : ""}${t.problems.length ? " disabled" : ""}>
      <span><b>T-${t.id} · ${esc(t.title)}</b>
        <em>${t.kind === "advertorial" ? "Advertorial" : "Listing"} · ${t.creatives.length} creative${t.creatives.length === 1 ? "" : "s"}
        (${t.creatives.filter((x) => x.video).length} video) · ${t.creatives.length > 1 ? "one flexible ad" : "one ad"}
        ${t.problems.length ? ` · <span class="lx-bad">${esc(t.problems.join(", "))}</span>` : ""}</em>
        <em>${esc(t.copy.headline || "")}${t.link ? ` → ${esc(t.link.replace(/^https?:\/\//, "").slice(0, 60))}` : ""}</em></span></label>`;
  return `<section class="panel ma-card">
    <div class="panel-head"><h2>1. Pick tasks</h2><span class="hint">All picked tasks go into one ad set · ${esc(L.o.account)} · ${esc(L.o.currency)} · clock ${esc(L.o.timezone)}</span></div>
    ${ready.length ? ready.map(taskRow).join("") : '<p class="hint">No approved advertorial or listing task for this store yet.</p>'}
    ${blocked.length ? `<details class="lx-more"><summary>${blocked.length} not ready yet</summary>${blocked.map(taskRow).join("")}</details>` : ""}
    ${drafts.length ? `<div class="lx-drafts"><b>Saved for an admin:</b> ${drafts.map((d) => `<span class="lx-chip">${esc(d.preset || "Draft")} · ${d.task_ids.map((i) => "T-" + i).join(", ")} · ${esc((d.by || "").split("@")[0])}
       <button class="btn btn-sm" data-draft="${esc(d.id)}">Open</button><button class="btn btn-sm btn-ghost" data-undraft="${esc(d.id)}">×</button></span>`).join("")}</div>` : ""}
  </section>`;
}

function bulkSection() {
  const j = L.job, o = L.o;
  const pos = (id) => L.sel.indexOf(id);
  const tile = (m) => {
    const n = pos(m.id);
    return `<label class="lx-tile${n >= 0 ? " on" : ""}" title="${esc(m.name)}">
      <input type="checkbox" data-media="${m.id}"${n >= 0 ? " checked" : ""}>
      ${m.kind === "image" ? `<img src="/api/metaads/bulk/media/${m.id}/view" alt="" loading="lazy">` : `<span class="lx-vid">▶<em>video</em></span>`}
      ${n >= 0 ? `<b class="lx-num">${n + 1}</b>` : ""}
      <span class="lx-tname">${esc(m.name)}</span>
      ${canWork() ? `<button class="lx-x" data-unmedia="${m.id}" title="Remove from the list">×</button>` : ""}</label>`;
  };
  const list = (key, label, many, area) => `<div class="lx-texts"><b>${label}</b>
    ${j[key].map((t, i) => area ? `<textarea class="control" rows="3" data-job="${key}.${i}" placeholder="${label} ${i + 1}">${esc(t)}</textarea>`
      : `<input class="control" data-job="${key}.${i}" value="${esc(t)}" placeholder="${label} ${i + 1}">`).join("")}
    ${j[key].length < 5 && many ? `<button class="btn btn-sm btn-ghost" data-addtext="${key}">+ Add another (up to 5)</button>` : ""}</div>`;
  const drafts = o.bulk_drafts || [];
  const n = L.sel.length;
  return `<section class="panel ma-card">
    <div class="panel-head"><h2>1. Creatives</h2><span class="hint">${esc(o.account)} · ${esc(o.currency)} · clock ${esc(o.timezone)}</span></div>
    ${canWork() ? `<label class="lx-drop" id="lxDrop"><input type="file" id="lxFiles" multiple accept="image/*,video/*" hidden>
      <b>Drop images and videos here</b> or click to choose (up to 50 at a time, 500 MB each)<span id="lxUp"></span></label>` : ""}
    ${L.media.length ? `<div class="lx-selbar"><span><b>${n}</b> picked${n ? " (numbers = order)" : ""}</span>
      <button class="btn btn-sm btn-ghost" id="lxAll">Pick all</button><button class="btn btn-sm btn-ghost" id="lxNone">Clear</button></div>
      <div class="lx-tiles">${L.media.map(tile).join("")}</div>` : '<p class="hint">No creatives uploaded for this store yet.</p>'}
    ${drafts.length ? `<div class="lx-drafts"><b>Saved for an admin:</b> ${drafts.map((d) => `<span class="lx-chip">${esc(d.preset || "Bulk")} · ${d.bulk.media_ids.length} creatives · ${esc((d.by || "").split("@")[0])}${d.note ? ` · ${esc(d.note)}` : ""}
       <button class="btn btn-sm" data-bdraft="${esc(d.id)}">Open</button><button class="btn btn-sm btn-ghost" data-unbdraft="${esc(d.id)}">×</button></span>`).join("")}</div>` : ""}
  </section>
  <section class="panel ma-card">
    <div class="panel-head"><h2>2. Ad copy and link</h2><span class="hint">Several texts = Meta tries them and shows the best (one ad holds them all)</span></div>
    <div class="lx-copy">
      ${list("bodies", "Primary text", true, true)}
      <div>${list("titles", "Headline", true, false)}${list("descriptions", "Description (optional)", true, false)}</div>
    </div>
    <div class="lx-grid">
      ${field("Landing page link", `<input class="control" data-job="link" value="${esc(j.link)}" placeholder="https://yourstore.com/products/…">`)}
      ${field("Display link (optional)", inp("ad.display_link", L.s.ad.display_link, 'placeholder="yourstore.com"'))}
    </div>
  </section>
  <section class="panel ma-card">
    <div class="panel-head"><h2>3. How to split them</h2></div>
    <div class="lx-grid">
      ${field("Where", sel("adset.mode", optMap({ new: "New ad set(s)", existing: "Add the ads to an existing ad set" }, L.s.adset.mode)))}
      ${intoAdset() ? field("Existing ad set", `<select class="control" data-s="adset.existing_id"><option value="">${L.adsets ? "Pick…" : "Loading…"}</option>${opt(L.adsets || [], L.s.adset.existing_id, (x) => `${x.name} · ${x.campaign || ""}`)}</select>`)
        : field("Ad sets", `<select class="control" data-job="adsets">${optMap(o.bulk_choices.adsets, j.adsets)}</select>`)}
      ${!intoAdset() && j.adsets === "groups" ? field("Creatives per ad set", `<input class="control" data-job="group_size" value="${j.group_size}" inputmode="numeric">`) : ""}
      ${field("Ads in each ad set", `<select class="control" data-job="ads">${optMap(o.bulk_choices.ads, j.ads)}</select>`)}
    </div>
    <p class="hint" id="lxSplit">${splitText()}</p>
  </section>`;
}

/* What the split makes, in words, before the server's check gives the exact list. Counts only, from the picks. */
function splitText() {
  const n = L.sel.length;
  if (!n) return "Pick creatives to see what this makes.";
  const j = L.job;
  if (intoAdset()) return `${j.ads === "flexible" ? "One flexible ad (or a few, 10 images / 10 videos each)" : `${n} ads`} added to the existing ad set. Check with Meta for the exact list.`;
  const sets = j.adsets === "one" ? "one ad set" : j.adsets === "each" ? `${n} ad sets` : `ad sets of ${j.group_size}`;
  return `${n} creative${n === 1 ? "" : "s"} → ${sets}, ${j.ads === "flexible" ? "one flexible ad in each" : "one ad per creative"}. Check with Meta for the exact list.`;
}

function campaignSection(num) {
  const c = L.s.campaign, o = L.o, cur = o.currency;
  return `<section class="panel ma-card">
    <div class="panel-head"><h2>${num}. Campaign</h2></div>
    <div class="lx-grid">
      ${field("Campaign", sel("campaign.mode", `<option value="new"${c.mode === "new" ? " selected" : ""}>New campaign (Sales)</option><option value="existing"${c.mode === "existing" ? " selected" : ""}>Add to an existing campaign</option>`))}
      ${c.mode === "existing" ? field("Existing campaign", sel("campaign.existing_id", `<option value="">Pick…</option>${opt(o.campaigns, c.existing_id, (x) => `${x.name}${x.cbo ? " (campaign budget)" : ""} · ${x.status || ""}`)}`))
        : field("Campaign name", inp("campaign.name", c.name), "{store} {date} {preset} are filled in")}
      ${c.mode === "new" ? field("Budget", sel("campaign.budget_mode", `<option value="abo"${c.budget_mode === "abo" ? " selected" : ""}>On each ad set (ABO)</option><option value="cbo"${c.budget_mode === "cbo" ? " selected" : ""}>On the campaign (CBO / Advantage+ budget)</option>`)) : ""}
      ${field(`Daily budget (${esc(cur)})`, inp("campaign.daily_budget", c.daily_budget, 'inputmode="decimal"'), c.budget_mode === "cbo" && c.mode === "new" ? "For the whole campaign" : bulk() ? "For each new ad set" : c.mode === "existing" ? "Used for the new ad set unless the campaign holds the budget" : "")}
      ${field("Bid strategy", sel("campaign.bid_strategy", optMap(o.choices.bid, c.bid_strategy)))}
      ${["COST_CAP", "LOWEST_COST_WITH_BID_CAP"].includes(c.bid_strategy) ? field(`${c.bid_strategy === "COST_CAP" ? "Cost per purchase goal" : "Bid cap"} (${esc(cur)})`, inp("campaign.bid_amount", c.bid_amount, 'inputmode="decimal"')) : ""}
      ${c.bid_strategy === "LOWEST_COST_WITH_MIN_ROAS" ? field("ROAS goal", inp("campaign.roas_floor", c.roas_floor, 'inputmode="decimal" placeholder="e.g. 2.0"')) : ""}
    </div>
  </section>`;
}

function adsetSection(num) {
  const a = L.s.adset, o = L.o;
  const copied = a.audience === "copied";
  return `<section class="panel ma-card">
    <div class="panel-head"><h2>${num}. Ad set</h2></div>
    <div class="lx-grid">
      ${field("Ad set name", inp("adset.name", a.name), bulk() ? "{file} {n} {countries} {date} are filled in" : "{tasks} {countries} {date} are filled in")}
      ${copied ? "" : field("Countries", inp("adset.countries", a.countries, 'placeholder="AU" style="text-transform:uppercase"'), "Two-letter codes, e.g. AU or US CA")}
      ${copied ? "" : field("Age from", inp("adset.age_min", a.age_min, 'inputmode="numeric"'))}
      ${copied ? "" : field("Age to", inp("adset.age_max", a.age_max, 'inputmode="numeric"'), "65 = 65 and over")}
      ${copied ? "" : field("Gender", sel("adset.genders", optMap({ all: "Everyone", women: "Women", men: "Men" }, a.genders)))}
      ${field("Pixel", sel("adset.pixel_id", `<option value="">Pick…</option>${opt(o.pixels, a.pixel_id)}`))}
      ${field("Optimise for", sel("adset.event", optMap(o.choices.events, a.event)))}
      ${field("Attribution", sel("adset.attribution", optMap(o.choices.attribution, a.attribution)))}
    </div>
    ${copied ? `<div class="lx-bp"><p><b>Audience copied exactly</b> from the ad set “${esc(a.targeting_from)}”: locations, ages, custom audiences, interests and placements stay as they are there.</p>
      <button class="btn btn-sm" id="lxOwnAud">Use my own audience instead</button></div>` : `<div class="lx-switches">
      ${chk("adset.advantage_audience", a.advantage_audience, "Advantage+ audience", " — Meta may go beyond your age / gender as suggestions")}
      ${chk("adset.placements_adv", a.placements !== "manual", "Advantage+ placements", " — all of Facebook, Instagram, Messenger, Audience Network")}
    </div>
    ${a.placements === "manual" ? `<div class="lx-switches">
      ${["facebook", "instagram", "messenger", "audience_network"].map((p) => `<label class="lx-check"><input type="checkbox" data-plat="${p}"${(a.platforms || []).includes(p) ? " checked" : ""}> ${p.replace("_", " ")}</label>`).join("")}
    </div>` : ""}`}
    <div class="lx-grid">
      ${field("Start", sel("adset.start", `<option value="now"${a.start !== "at" ? " selected" : ""}>As soon as it's launched</option><option value="at"${a.start === "at" ? " selected" : ""}>At a set time</option>`))}
      ${a.start === "at" ? field(`Start at (${esc(o.timezone)})`, inp("adset.start_at", a.start_at, 'type="datetime-local"'), `<a href="#" id="lxTomorrow">Tomorrow 00:01</a>`) : ""}
      ${field(`End (optional, ${esc(o.timezone)})`, inp("adset.end_at", a.end_at, 'type="datetime-local"'))}
    </div>
  </section>`;
}

function adSection(num) {
  const ad = L.s.ad, o = L.o;
  return `<section class="panel ma-card">
    <div class="panel-head"><h2>${num}. Ads</h2><span class="hint">${bulk() ? "Copy and link from step 2" : "One ad per task; copy and landing page come from the task"}</span></div>
    <div class="lx-grid">
      ${field("Ad name", inp("ad.name", ad.name), bulk() ? "{file} {n} {date} are filled in" : "{task} {date} are filled in")}
      ${field("Facebook Page", sel("ad.page_id", `<option value="">Pick…</option>${opt(o.pages, ad.page_id)}`))}
      ${field("Instagram account", sel("ad.instagram_id", `<option value="">Use the Facebook Page</option>${opt(o.instagram, ad.instagram_id)}`))}
      ${field("Button", sel("ad.cta", optMap(o.choices.cta, ad.cta)))}
      ${bulk() ? "" : field("Tasks with several creatives", sel("ad.multi", optMap({ flexible: "One flexible ad (Meta mixes them)", separate: "One ad per creative" }, ad.multi)))}
    </div>
    <div class="lx-switches">${chk("ad.utm", ad.utm, "Tracking tags on the link", ad.url_tags ? " — the copied campaign's own tags" : " — so EcomVital credits the sales to this campaign / ad set / ad")}</div>
    <details class="lx-enh" open><summary>Advantage+ creative enhancements <em class="hint">(${Object.keys(o.choices.enhancements).filter((k) => (ad.enhancements || {})[k]).length} on)</em></summary>
      <div class="lx-switches lx-cols">${Object.entries(o.choices.enhancements).map(([k, v]) => chk(`enh.${k}`, (ad.enhancements || {})[k], esc(v))).join("")}</div>
    </details>
  </section>`;
}

function render() {
  if (!L.s.adset.mode) L.s.adset.mode = "new";
  if (!bulk()) L.s.adset.mode = "new";
  const parts = [modeBar(), copyBar(), bulk() ? bulkSection() : tasksSection()];
  let num = bulk() ? 4 : 2;
  if (!intoAdset()) { parts.push(campaignSection(num++), adsetSection(num++)); }
  parts.push(adSection(num++));
  const count = bulk() ? `${L.sel.length} creative${L.sel.length === 1 ? "" : "s"} picked` : `${L.picked.size} task${L.picked.size === 1 ? "" : "s"} picked`;
  parts.push(`<section class="panel ma-card">
    <div class="panel-head"><h2>${num}. Check, then launch</h2><span class="hint">${count}</span></div>
    <div class="row-actions lx-acts">
      <button class="btn" id="lxCheck">Check with Meta</button>
      ${manageLvl() ? `<button class="btn btn-primary" id="lxLaunch">Launch</button>` : `<button class="btn btn-primary" id="lxDraft">Save for an admin to launch</button>`}
    </div>
    <div id="lxResult"></div>
    <div id="lxRuns">${L.runsHtml || ""}</div>
  </section>`);
  $("lx").innerHTML = parts.join("");
  if (intoAdset() && !L.adsets) loadAdsets();
}

async function loadAdsets() {
  try { L.adsets = (await api(`/api/metaads/launcher/adsets?store=${state.store}`)).adsets; }
  catch (e) { L.adsets = []; toast(e.message, true); }
  render();
}

/* ---------- changes */

function setPath(path, val) {
  const [part, key] = path.split(".");
  if (part === "enh") { L.s.ad.enhancements = { ...(L.s.ad.enhancements || {}), [key]: val }; return; }
  if (path === "adset.placements_adv") { L.s.adset.placements = val ? "advantage" : "manual"; return; }
  L.s[part][key] = val;
}
function setJob(path, val) {
  const [key, i] = path.split(".");
  if (i !== undefined) L.job[key][+i] = val; else L.job[key] = val;
  const sp = $("lxSplit");
  if (sp) sp.textContent = splitText();
}

document.addEventListener("change", (e) => {
  if (!e.target.closest("#lx")) return;
  const el = e.target;
  if (el.dataset.task) { el.checked ? L.picked.add(+el.dataset.task) : L.picked.delete(+el.dataset.task); render(); return; }
  if (el.dataset.media) {
    const id = +el.dataset.media;
    L.sel = el.checked ? [...L.sel.filter((x) => x !== id), id] : L.sel.filter((x) => x !== id);
    render();
    return;
  }
  if (el.id === "lxFiles") { upload(el.files); return; }
  if (el.dataset.plat) {
    const p = new Set(L.s.adset.platforms || []);
    el.checked ? p.add(el.dataset.plat) : p.delete(el.dataset.plat);
    L.s.adset.platforms = [...p];
    return;
  }
  if (el.id === "lxPreset") {
    L.preset = el.value;
    const p = (L.o.presets || []).find((x) => x.name === el.value);
    if (p) { L.s = JSON.parse(JSON.stringify(p.settings)); L.bp = null; }
    render();
    return;
  }
  if (el.dataset.job) { setJob(el.dataset.job, el.value); if (["adsets", "ads"].includes(el.dataset.job)) render(); return; }
  if (!el.dataset.s) return;
  setPath(el.dataset.s, el.type === "checkbox" ? el.checked : el.value);
  if (["campaign.mode", "campaign.bid_strategy", "adset.start", "adset.placements_adv", "campaign.budget_mode", "adset.mode"].includes(el.dataset.s)
      || el.dataset.s.startsWith("enh.")) render();
});
document.addEventListener("input", (e) => {
  const el = e.target;
  if (!el.closest || !el.closest("#lx")) return;
  if (el.dataset.job) { setJob(el.dataset.job, el.value); return; }
  if (el.dataset.s && el.type !== "checkbox" && el.tagName !== "SELECT") setPath(el.dataset.s, el.value);
});
document.addEventListener("dragover", (e) => { const d = e.target.closest && e.target.closest("#lxDrop"); if (d) { e.preventDefault(); d.classList.add("over"); } });
document.addEventListener("dragleave", (e) => { const d = e.target.closest && e.target.closest("#lxDrop"); if (d) d.classList.remove("over"); });
document.addEventListener("drop", (e) => {
  const d = e.target.closest && e.target.closest("#lxDrop");
  if (!d) return;
  e.preventDefault();
  d.classList.remove("over");
  upload(e.dataTransfer.files);
});

function upload(files) {
  const list = [...(files || [])];
  if (!list.length) return;
  const fd = new FormData();
  for (const f of list.slice(0, 50)) fd.append("files", f);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/metaads/bulk/media/${state.store}`);
  xhr.upload.onprogress = (ev) => { const u = $("lxUp"); if (u && ev.total) u.textContent = ` · uploading ${Math.round((ev.loaded / ev.total) * 100)}%`; };
  xhr.onload = async () => {
    let r = {};
    try { r = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
    if (xhr.status >= 400) { toast(r.detail || r.error || "Upload failed.", true); render(); return; }
    const m = await api(`/api/metaads/bulk/media?store=${state.store}`);
    L.media = m.media;
    L.sel = [...L.sel, ...r.added.map((x) => x.id).filter((id) => !L.sel.includes(id))];
    toast(`${r.added.length} uploaded and picked${r.skipped.length ? ` · skipped (not an image or video): ${r.skipped.join(", ")}` : ""}`);
    render();
  };
  xhr.onerror = () => { toast("Upload failed (connection).", true); render(); };
  xhr.send(fd);
  const u = $("lxUp");
  if (u) u.textContent = " · uploading…";
}

const tasksBody = () => ({ store: state.store, task_ids: [...L.picked], settings: L.s, preset: L.preset, draft_id: L.draft || undefined });
const bulkBody = () => ({ store: state.store, settings: L.s, preset: L.preset, draft_id: L.draft || undefined,
  bulk: { ...L.job, media_ids: L.sel } });

document.addEventListener("click", async (e) => {
  const t = e.target;
  if (!t.closest || !t.closest("#lx")) return;
  const b = t.closest("button,a");
  if (!b) return;
  if (b.dataset.lxmode) {
    L.mode = b.dataset.lxmode; L.draft = "";
    try { localStorage.setItem("pdLxMode", L.mode); } catch { /* private mode */ }
    render();
    return;
  }
  if (b.id === "lxTomorrow") {
    e.preventDefault();
    const d = new Date(Date.now() + 864e5);
    L.s.adset.start_at = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T00:01`;
    render();
    return;
  }
  if (b.dataset.addtext) { e.preventDefault(); L.job[b.dataset.addtext].push(""); render(); return; }
  if (b.dataset.unmedia) {
    e.preventDefault();
    const id = +b.dataset.unmedia;
    try { await api(`/api/metaads/bulk/media/${state.store}/${id}`, { method: "DELETE" }); L.media = L.media.filter((m) => m.id !== id); L.sel = L.sel.filter((x) => x !== id); render(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (b.id === "lxAll") { L.sel = L.media.map((m) => m.id); render(); return; }
  if (b.id === "lxNone") { L.sel = []; render(); return; }
  if (b.id === "lxOwnAud") { L.s.adset.audience = "form"; L.s.adset.targeting_raw = null; L.s.adset.targeting_from = ""; render(); return; }
  if (b.id === "lxBpGo") {
    const camp = $("lxBpCamp").value;
    if (!camp) { toast("Pick the campaign to copy.", true); return; }
    const set = $("lxBpSet") && L.bp && L.bp.campaign.id === camp ? $("lxBpSet").value : "";
    b.disabled = true; b.textContent = "Reading the campaign…";
    try {
      const r = await api(`/api/metaads/launcher/blueprint?store=${state.store}&campaign=${encodeURIComponent(camp)}${set ? `&adset=${encodeURIComponent(set)}` : ""}`);
      L.bp = r; L.s = r.settings; L.preset = "";
      const j = L.job;
      if (!j.bodies.some((x) => x.trim()) && r.copy.bodies.length) j.bodies = r.copy.bodies.slice();
      if (!j.titles.some((x) => x.trim()) && r.copy.titles.length) j.titles = r.copy.titles.slice();
      if (!j.descriptions.some((x) => x.trim()) && r.copy.descriptions.length) j.descriptions = r.copy.descriptions.slice();
      if (!j.link && r.copy.link) j.link = r.copy.link;
      toast("Setup copied. Check the list, then add your creatives.");
    } catch (err) { toast(err.message, true); }
    render();
    return;
  }
  if (b.id === "lxSavePreset") {
    const name = prompt("Name this preset (e.g. ABO test AUD 30, Advantage+ audience off):", L.preset || "");
    if (!name) return;
    try { const r = await api(`/api/metaads/launcher/presets/${state.store}`, { json: { name, settings: L.s } }); L.o.presets = r.presets; L.preset = name; render(); toast("Preset saved: staff can pick it from the list"); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (b.id === "lxDelPreset") {
    if (!confirm(`Remove the preset “${L.preset}”?`)) return;
    try { const r = await api(`/api/metaads/launcher/presets/${state.store}`, { json: { remove: L.preset } }); L.o.presets = r.presets; L.preset = ""; render(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (b.dataset.draft) {
    const d = (L.o.drafts || []).find((x) => x.id === b.dataset.draft);
    if (d) { L.s = JSON.parse(JSON.stringify(d.settings)); L.picked = new Set(d.task_ids); L.preset = d.preset || ""; L.draft = d.id; render(); toast("Draft opened: check it, then Launch"); }
    return;
  }
  if (b.dataset.undraft || b.dataset.unbdraft) {
    if (!confirm("Remove this saved draft?")) return;
    const url = b.dataset.undraft ? "launcher" : "bulk";
    try {
      const r = await api(`/api/metaads/${url}/drafts/${state.store}`, { json: { remove: b.dataset.undraft || b.dataset.unbdraft } });
      if (url === "launcher") L.o.drafts = r.drafts; else L.o.bulk_drafts = r.drafts;
      render();
    } catch (err) { toast(err.message, true); }
    return;
  }
  if (b.dataset.bdraft) {
    const d = (L.o.bulk_drafts || []).find((x) => x.id === b.dataset.bdraft);
    if (d) {
      L.s = JSON.parse(JSON.stringify(d.settings)); L.preset = d.preset || ""; L.draft = d.id;
      const pad = (a) => (a && a.length ? a.slice() : [""]);
      L.job = { ...d.bulk, bodies: pad(d.bulk.bodies), titles: pad(d.bulk.titles), descriptions: pad(d.bulk.descriptions) };
      L.sel = d.bulk.media_ids.filter((id) => L.media.some((m) => m.id === id));
      render();
      toast("Draft opened: check it, then Launch");
    }
    return;
  }
  if (b.id === "lxCheck") { await check(); return; }
  if (b.id === "lxDraft") {
    try {
      if (bulk()) {
        if (!L.sel.length) { toast("Pick at least one creative.", true); return; }
        const note = prompt("A note for the admin (optional):", "") || "";
        const r = await api(`/api/metaads/bulk/drafts/${state.store}`, { json: { ...bulkBody(), note } });
        L.o.bulk_drafts = r.drafts;
      } else {
        if (!L.picked.size) { toast("Pick at least one task.", true); return; }
        const r = await api(`/api/metaads/launcher/drafts/${state.store}`, { json: tasksBody() });
        L.o.drafts = r.drafts;
      }
      render();
      toast("Saved: an admin can open it and launch");
    } catch (err) { toast(err.message, true); }
    return;
  }
  if (b.id === "lxLaunch") {
    const r0 = await check();
    if (!r0) return;
    const a = L.s.adset;
    const startTxt = intoAdset() ? "in the existing ad set" : a.start === "at" ? `starting ${a.start_at.replace("T", " ")} (${L.o.timezone})` : "starting now";
    const what = bulk() ? `${r0.totals.ads} ad${r0.totals.ads === 1 ? "" : "s"}${r0.totals.adsets ? ` in ${r0.totals.adsets} ad set${r0.totals.adsets === 1 ? "" : "s"}` : ""}`
      : `${L.picked.size} task${L.picked.size === 1 ? "" : "s"}`;
    if (!confirm(`Launch ${what} on Meta, ${startTxt}? Ads go ACTIVE and can spend.`)) return;
    try {
      const r = await api(bulk() ? "/api/metaads/bulk/launch" : "/api/metaads/launcher/launch", { json: bulk() ? bulkBody() : tasksBody() });
      toast(r.carrying_on ? "Carrying on the earlier launch…" : "Launching… you can leave this page");
      L.draft = "";
      loadRuns(true);
    } catch (err) { toast(err.message, true); }
  }
});

async function check() {
  const out = $("lxResult");
  if (bulk() ? !L.sel.length : !L.picked.size) { toast(bulk() ? "Pick at least one creative." : "Pick at least one task.", true); return false; }
  out.innerHTML = '<p class="hint">Checking with Meta…</p>';
  try {
    if (bulk()) {
      const r = await api("/api/metaads/bulk/check", { json: bulkBody() });
      const bad = r.missing.map((m) => "Fill in: " + m);
      out.innerHTML = `<div class="lx-check-out">
        <p><b>${r.totals.creatives} creative${r.totals.creatives === 1 ? "" : "s"} → ${r.totals.adsets ? `${r.totals.adsets} ad set${r.totals.adsets === 1 ? "" : "s"}, ` : ""}${r.totals.ads} ad${r.totals.ads === 1 ? "" : "s"}</b>${r.campaign ? ` in the new campaign “${esc(r.campaign)}”` : ""}</p>
        ${r.adsets.slice(0, 30).map((s) => `<div class="lx-tree"><b>${esc(s.name)}</b><ul>${s.ads.map((x) => `<li>${esc(x.name)} <em class="hint">· ${esc(x.kind)}${x.creatives > 1 ? `, ${x.creatives} creatives` : ""}</em></li>`).join("")}</ul></div>`).join("")}
        ${r.adsets.length > 30 ? `<p class="hint">…and ${r.adsets.length - 30} more ad sets.</p>` : ""}
        ${bad.length ? `<p class="lx-bad">${bad.map(esc).join("<br>")}</p>` : ""}
        ${r.meta_error ? `<p class="lx-bad">Meta: ${esc(r.meta_error)}</p>` : ""}
        ${r.meta.length ? `<p class="lx-ok">${r.meta.map(esc).join(" ")}</p>` : ""}
        ${!bad.length && !r.meta_error ? `<p class="lx-ok">Ready${r.start ? ` · starts ${esc(r.start)}` : ""}.</p>` : ""}
      </div>`;
      return !bad.length && !r.meta_error ? r : false;
    }
    const r = await api("/api/metaads/launcher/check", { json: tasksBody() });
    const bad = [...r.missing.map((m) => "Fill in: " + m), ...r.task_problems];
    out.innerHTML = `<div class="lx-check-out">
      <p><b>Campaign:</b> ${esc(r.names.campaign)}<br><b>Ad set:</b> ${esc(r.names.adset)}</p>
      <ul>${r.names.ads.map((x) => `<li>${esc(x.name)} <em class="hint">· ${esc(x.kind)}</em></li>`).join("")}</ul>
      ${bad.length ? `<p class="lx-bad">${bad.map(esc).join("<br>")}</p>` : ""}
      ${r.meta_error ? `<p class="lx-bad">Meta: ${esc(r.meta_error)}</p>` : ""}
      ${r.meta.length ? `<p class="lx-ok">${r.meta.map(esc).join(" ")}</p>` : ""}
      ${!bad.length && !r.meta_error ? `<p class="lx-ok">Ready${r.start && r.start !== "now" ? ` · starts ${esc(r.start)}` : " · starts as soon as it's launched"}.</p>` : ""}
    </div>`;
    return !bad.length && !r.meta_error ? r : false;
  } catch (err) { out.innerHTML = `<p class="lx-bad">${esc(err.message)}</p>`; return false; }
}

async function loadRuns(poll = false) {
  clearTimeout(L.runsTimer);
  const box = $("lxRuns");
  if (!box) return;
  try {
    const r = await api(`/api/metaads/launcher/runs?store=${state.store}`);
    const label = { running: "Building", waiting: "Waiting for Meta", failed: "Stopped", done: "Done" };
    const what = (x) => x.kind === "bulk" ? `Bulk · ${x.creatives} creative${x.creatives === 1 ? "" : "s"}` : x.task_ids.map((i) => "T-" + i).join(", ");
    L.runsHtml = r.runs.length ? `<h4 class="lx-h4">Launches</h4><table class="table"><tbody>${r.runs.map((x) => `<tr>
        <td>#${x.id}</td><td>${esc(what(x))}</td>
        <td><b>${esc(label[x.status] || x.status)}</b>${x.step ? ` · ${esc(x.step)}` : ""}${x.error ? `<br><span class="lx-bad">${esc(x.error)}</span>` : ""}</td>
        <td>${x.ads} ad${x.ads === 1 ? "" : "s"}</td><td>${esc(when(x.at))}</td>
        <td>${x.ads_manager ? `<a href="${esc(x.ads_manager)}" target="_blank">Ads Manager ↗</a>` : ""}</td></tr>`).join("")}</tbody></table>` : "";
    if ($("lxRuns")) $("lxRuns").innerHTML = L.runsHtml;
    if (r.runs.some((x) => x.running) || poll) L.runsTimer = setTimeout(() => loadRuns(r.runs.some((x) => x.running)), 5000);
  } catch { /* the page still works without the list */ }
}
