/* Tasks desk: admins give staff work, staff hand back links / creatives / ad copy,
   admins approve and launch the ads on Meta (created paused). History keeps the finished ones. */

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
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 7000 : 3000);
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
  if (!r.ok) {
    const err = new Error(body.detail || body.error || `Something went wrong (${r.status}).`);
    err.body = body;
    throw err;
  }
  return body;
}
const linkify = (u) => /^https?:\/\//.test(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>` : esc(u);
const lines = (v) => (Array.isArray(v) ? v : String(v || "").split("\n")).map((x) => x.trim()).filter(Boolean);
const when = (utc) => {
  if (!utc) return "";
  const d = new Date(utc.replace(" ", "T") + "Z");
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
};
const dueText = (d, today) => {
  if (!d) return "";
  const late = today && d < today;
  const txt = new Date(d + "T00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `<span class="tk-due${late ? " late" : ""}">${late ? "Late · " : "Due "}${txt}</span>`;
};

const TODO_DEFAULT = { advertorial: "Create advertorial", collection: "Create collection", product_pages: "Create product pages",
  branded_page: "Create branded page", creatives: "Create creatives", ad_copy: "Ad copy, headline and description" };
const todoNames = () => (state.data && state.data.todo) || (state.task && state.task.todo) || TODO_DEFAULT;
const todoLabel = (k) => todoNames()[k] || k;
const AUDIO = /\.(m4a|mp3|wav|ogg|oga|aac)$|^voice-note/i;
const COLUMNS = [
  ["todo", "To do"], ["doing", "In progress"], ["changes", "Changes asked"],
  ["submitted", "Submitted for review"], ["approved", "Waiting to launch"],
];
const CTA_NAMES = { SHOP_NOW: "Shop now", LEARN_MORE: "Learn more", ORDER_NOW: "Order now", BUY_NOW: "Buy now",
  GET_OFFER: "Get offer", SIGN_UP: "Sign up" };
const IMG = /\.(jpe?g|png|gif|webp|heic|heif)$/i;
const VID = /\.(mp4|mov|m4v|webm|avi)$/i;

let state = { page: PDUrl.get("page") || "board", data: null, open: null, editing: null, kind: "listing",
  sel: new Set(), recs: [], todo: [] };

/* ------------------------------------------------ board + history */

async function load() {
  const q = new URLSearchParams({ view: state.page === "history" ? "history" : "board" });
  if ($("fPerson").value) q.set("assignee", $("fPerson").value);
  if ($("fStore").value) q.set("store", $("fStore").value);
  if ($("q").value.trim()) q.set("q", $("q").value.trim());
  try {
    state.data = await api("/api/tasks?" + q);
  } catch (e) { toast(e.message, true); return; }
  fillPickers();
  if (state.page === "history") renderHistory(); else renderBoard();
}

function fillPickers() {
  const d = state.data;
  const keep = (sel, opts, first) => {
    const v = sel.value;
    sel.innerHTML = first + opts;
    sel.value = v;
  };
  keep($("fPerson"), d.people.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join(""), '<option value="">Everyone</option>');
  keep($("dayPerson"), d.people.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join(""), '<option value="">Everyone</option>');
  keep($("fStore"), d.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join(""), '<option value="">All stores</option>');
  keep($("eStore"), d.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join(""), '<option value="">No store</option>');
  keep($("eWho"), d.people.map((p) => `<option value="${p.id}">${esc(p.name)}${p.level === "manage" ? " (admin)" : ""}</option>`).join(""), '<option value="">Nobody yet</option>');
  $("sub").textContent = d.manage ? "Listing, landing pages and ads: give the work, check it, launch it"
    : "Your tasks: open one, do the work, add your links and creatives, then submit";
}

function card(t) {
  const today = state.data.today;
  const short = (k) => (k === "ad_copy" ? "Ad copy" : todoLabel(k).replace(/^Create (\w)/, (m, c) => c.toUpperCase()));
  const items = t.brief.todo ? t.brief.todo.map(short) : (t.brief.pages || []);
  const pages = items.slice(0, 6).map((p) => `<span class="tk-tag">${esc(p)}</span>`).join("");
  const pick = state.data.manage && state.data.launch_ready && t.status === "approved" && t.kind === "listing";
  return `<div class="tk-card${t.priority === "high" ? " high" : ""}${state.sel.has(t.id) ? " picked" : ""}" data-open="${t.id}" role="button" tabindex="0">
    ${pick ? `<label class="tk-sel" title="Pick to launch together"><input type="checkbox" data-sel="${t.id}"${state.sel.has(t.id) ? " checked" : ""}> Launch</label>` : ""}
    <div class="tk-card-top"><span class="tk-kindtag ${t.kind}">${t.kind === "listing" ? "Listing + ads" : "Custom"}</span>
      ${t.priority === "high" ? '<span class="tk-high">High</span>' : ""}${t.special_notes ? '<span class="tk-noteflag" title="Has special notes">Notes</span>' : ""}</div>
    <strong>${esc(t.title)}</strong>
    ${t.store ? `<span class="tk-meta">${esc(t.store)}</span>` : ""}
    ${pages ? `<div class="tk-tags">${pages}</div>` : ""}
    <div class="tk-card-foot"><span class="tk-who">${t.assignee ? esc(t.assignee) : "<em>Not given to anyone</em>"}</span>
      ${dueText(t.due, today)}${t.files_staff ? `<span class="tk-files" title="Files staff uploaded">📎 ${t.files_staff}</span>` : ""}</div>
  </div>`;
}

function selBar() {
  const ok = new Set((state.data ? state.data.tasks : []).filter((t) => t.status === "approved").map((t) => t.id));
  for (const id of [...state.sel]) if (!ok.has(id)) state.sel.delete(id);
  const b = $("launchSel");
  b.hidden = !state.sel.size || state.page !== "board";
  b.textContent = `Launch selected (${state.sel.size})…`;
}

function renderBoard() {
  const by = {};
  for (const t of state.data.tasks) (by[t.status] = by[t.status] || []).push(t);
  $("boardPage").innerHTML = COLUMNS.map(([k, name]) => `
    <div class="tk-col tk-col-${k}">
      <div class="tk-col-head"><span>${name}</span><b>${(by[k] || []).length}</b></div>
      <div class="tk-col-body">${(by[k] || []).map(card).join("") || '<p class="tk-empty">Nothing here</p>'}</div>
    </div>`).join("");
  selBar();
}

function renderHistory() {
  const rows = state.data.tasks;
  $("hist").innerHTML = rows.length ? `<thead><tr><th>Task</th><th>Store</th><th>Who</th><th>Result</th><th>Closed</th></tr></thead>
    <tbody>${rows.map((t) => `<tr data-open="${t.id}" class="tk-hist-row">
      <td><span class="name">${esc(t.title)}</span><span class="sub">${t.kind === "listing" ? "Listing + ads" : "Custom"}</span></td>
      <td>${esc(t.store)}</td><td>${esc(t.assignee)}</td>
      <td><span class="tk-st tk-st-${t.status}">${esc(t.status_name)}</span></td><td>${when(t.closed_at)}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="empty">No finished tasks yet. Launched, done and cancelled tasks land here.</td></tr></tbody>';
}

/* ------------------------------------------------ one task */

async function openTask(id) {
  state.open = id;
  PDUrl.set({ task: id });
  $("drawer").hidden = false;
  $("dBody").innerHTML = '<p class="empty">Loading…</p>';
  let t;
  try { t = await api(`/api/tasks/${id}`); } catch (e) { $("dBody").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  state.task = t;
  renderTask();
}

function closeTask() {
  state.open = null;
  state.task = null;
  $("drawer").hidden = true;
  PDUrl.set({ task: null });
}

function fileTile(f, canRemove) {
  const url = `/api/tasks/file/${f.id}`;
  const img = IMG.test(f.name), vid = VID.test(f.name);
  const view = img ? `<img src="${url}?thumb=1" alt="" loading="lazy">`
    : vid ? `<video src="${url}#t=0.5" preload="metadata" muted></video><span class="tk-play">▶</span>`
    : `<span class="tk-fileicon">${esc((f.name.split(".").pop() || "file").toUpperCase())}</span>`;
  return `<div class="tk-file"><a href="${url}" target="_blank" rel="noopener" class="tk-file-view">${view}</a>
    <div class="tk-file-name" title="${esc(f.name)}">${esc(f.name)}</div>
    <div class="tk-file-act"><a href="${url}?download=1">Download</a>${canRemove ? `<button data-rmfile="${f.id}">Remove</button>` : ""}</div></div>`;
}

function uploader(side, kind, label) {
  return `<label class="tk-drop" data-side="${side}" data-kind="${kind}">
    <input type="file" multiple ${kind === "creative" ? 'accept="image/*,video/*"' : ""} hidden>
    <span>${label}</span><em>Click or drop files here · images and videos up to 500 MB each</em></label>`;
}

function player(f, canRemove) {
  const url = `/api/tasks/file/${f.id}`;
  const audio = (f.mime || "").startsWith("audio") || AUDIO.test(f.name);
  return `<div class="tk-rec">${audio ? `<audio src="${url}" controls preload="metadata"></audio>` : `<video src="${url}" controls preload="metadata"></video>`}
    <div class="tk-file-act"><span>${audio ? "🎙" : "🖥"} ${esc(f.who || "")} · ${when(f.at)}</span>
      <span><a href="${url}?download=1">Download</a>${canRemove ? ` · <button data-rmfile="${f.id}">Remove</button>` : ""}</span></div></div>`;
}

function renderTask() {
  const t = state.task;
  const b = t.brief, r = t.result;
  const manage = t.manage, closed = ["launched", "done", "cancelled"].includes(t.status);
  const canWork = (t.mine || manage) && !closed && (manage || !["submitted", "approved"].includes(t.status));
  const recs = t.files.filter((f) => f.side === "admin" && f.kind === "instruction");
  const adminFiles = t.files.filter((f) => f.side === "admin" && f.kind !== "instruction");
  const creatives = t.files.filter((f) => f.side === "staff" && f.kind === "creative");
  const otherFiles = t.files.filter((f) => f.side === "staff" && f.kind !== "creative");
  const me = state.data && state.data.me ? state.data.me.email : "";
  const canRm = (f) => !closed && (manage || (f.by === me && t.status !== "approved"));
  const dis = canWork ? "" : " disabled";
  $("dTitle").textContent = t.title;

  const links = (v) => lines(v).map(linkify).join("<br>");
  const todo = b.todo || null;                 // null = a task from before the to-do picks
  const briefRows = [
    ["Competitor ads", links(b.competitor_ads)], ["Their landing page", links(b.competitor_landing)],
    ["Their products", links(b.competitor_products)],
    ["To do", (todo || []).map((k) => `<span class="tk-tag">${esc(todoLabel(k))}</span>`).join(" ")],
    ["Collection", b.collection && esc(b.collection)], ["Products", links(b.products)],
    ["Build", (b.pages || []).map((p) => `<span class="tk-tag">${esc(p)}</span>`).join(" ")],
    ["Ad angle", b.angle && esc(b.angle).replace(/\n/g, "<br>")], ["Headline ideas", lines(b.headlines).map(esc).join("<br>")],
    ["Daily budget", b.budget && esc(b.budget)], ["Countries", b.countries && esc(b.countries)],
    ["Details", b.description && esc(b.description).replace(/\n/g, "<br>")],
  ].filter(([, v]) => v);

  const f = (k, label, ph = "", rows = 0) => rows
    ? `<label class="field"><span>${label}</span><textarea class="control" data-r="${k}" rows="${rows}" placeholder="${esc(ph)}"${dis}>${esc(r[k] || "")}</textarea></label>`
    : `<label class="field"><span>${label}</span><input class="control" data-r="${k}" placeholder="${esc(ph)}" value="${esc(r[k] || "")}"${dis}></label>`;
  const creativeBox = `<div class="tk-files">${creatives.map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">No creatives yet</p>'}</div>
      ${canWork ? uploader("staff", "creative", "+ Upload ad creatives") : ""}`;
  const copyBox = `${f("primary_text", "Primary text", "The text above the ad", 5)}
      <div class="tk-row">${f("headline", "Headline (one per line if several)", "", 2)}${f("ad_description", "Description (optional)", "", 2)}</div>
      <label class="field"><span>Button</span><select class="control" data-r="cta"${dis}>${Object.entries(CTA_NAMES).map(([k, v]) =>
        `<option value="${k}"${(r.cta || "SHOP_NOW") === k ? " selected" : ""}>${v}</option>`).join("")}</select></label>`;
  const ITEM = {
    advertorial: () => f("advertorial_url", "Advertorial link", "https://…"),
    collection: () => f("collection_url", "Collection link", "https://store.com/collections/…"),
    product_pages: () => f("product_urls", "Product page links (one per line)", "", 3),
    branded_page: () => f("branded_url", "Branded page link", "https://…"),
    creatives: () => creativeBox,
    ad_copy: () => copyBox,
  };
  const cant = r.cant || {}, custom = r.custom || {};
  const OUT = { advertorial: "advertorial_url", collection: "collection_url", product_pages: "product_urls", branded_page: "branded_url" };
  const made = (k) => k === "creatives" ? creatives.length > 0
    : k === "ad_copy" ? !!(r.primary_text && r.headline)
    : OUT[k] ? !!r[OUT[k]] : !!custom[k];
  const item = (k) => {
    const state_ = cant[k] ? '<span class="tk-st tk-st-changes">Couldn\'t do</span>' : made(k) ? '<span class="tk-st tk-st-done">✓ Made</span>' : '<span class="tk-st">To do</span>';
    const body = ITEM[k] ? ITEM[k]() : `<label class="field"><span>Link or answer</span><textarea class="control" data-custom="${esc(k)}" rows="2"${dis}>${esc(custom[k] || "")}</textarea></label>`;
    return `<div class="tk-item"><div class="tk-item-head"><b>${esc(todoLabel(k))}</b>${state_}</div>
      ${body}
      <details class="tk-cant"${cant[k] ? " open" : ""}><summary>Can't do this? Tell the admin why</summary>
        <textarea class="control" data-cant="${esc(k)}" rows="2" placeholder="e.g. The supplier has no photos of the blue one"${dis}>${esc(cant[k] || "")}</textarea></details></div>`;
  };

  const handBack = t.kind !== "listing" ? `${f("other_links", "Links (one per line)", "", 3)}
      <h4>Files</h4>
      <div class="tk-files">${creatives.concat(otherFiles).map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">None</p>'}</div>
      ${canWork ? uploader("staff", "file", "+ Upload files") : ""}`
    : `<div class="tk-main">${f("landing_url", "Main landing page link · the ads go here", "https://…")}</div>
      ${todo ? todo.map(item).join("") : `
        <div class="tk-row">${f("collection_url", "Collection link", "https://store.com/collections/…")}${f("advertorial_url", "Advertorial link", "https://…")}</div>
        ${f("product_urls", "Product page links (one per line)", "", 3)}
        <h4>Ad copy</h4>${copyBox}
        <h4>Ad creatives <em class="tk-opt">(these go in the ads)</em></h4>${creativeBox}`}
      ${f("other_links", "Other links (one per line)", "", 2)}
      <h4>Other files</h4>
      <div class="tk-files">${otherFiles.map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">None</p>'}</div>
      ${canWork ? uploader("staff", "file", "+ Upload other files") : ""}`;

  // the buttons that move the task on, for who's looking
  const acts = [];
  if (!closed) {
    if ((t.mine || manage) && ["todo", "changes"].includes(t.status)) acts.push(`<button class="btn" data-move="doing">Start</button>`);
    if (canWork && ["todo", "doing", "changes"].includes(t.status)) acts.push(`<button class="btn" data-save>Save</button><button class="btn btn-primary" data-submit>Submit for review</button>`);
    if (t.mine && !manage && t.status === "submitted") acts.push(`<button class="btn" data-move="doing">Take back to edit</button>`);
    if (manage && t.status === "submitted") acts.push(`<button class="btn btn-primary" data-move="approved">Approve</button><button class="btn" data-changes>Send back for changes</button>`);
    if (manage && t.status === "approved" && t.kind === "listing") acts.push(`${t.launch_ready ? "<button class=\"btn btn-primary\" data-launch>Launch ads on Meta…</button>"
      : "<button class=\"btn\" disabled title=\"The launch strategies are being set up\">Launch ads on Meta · coming soon</button>"}<button class="btn" data-move="launched" data-confirm="Mark as launched without creating the ads here?">Mark launched (set up by hand)</button>`);
    if (manage && t.status === "approved" && t.kind === "custom") acts.push(`<button class="btn btn-primary" data-move="done">Mark done</button>`);
    if (manage && t.status === "approved") acts.push(`<button class="btn" data-changes>Send back for changes</button>`);
    if (canWork && t.status === "approved" && manage) acts.push(`<button class="btn" data-save>Save edits</button>`);
  } else if (manage) {
    acts.push(`<button class="btn" data-move="approved" data-confirm="Reopen this task (back to Waiting to launch)?">Reopen</button>`);
  }
  const adminActs = manage ? `<div class="tk-admin-acts">
      ${!closed && t.assignee_id ? `<button class="btn btn-sm" data-followup${t.wa && t.wa.ready && t.wa.number ? "" : ` disabled title="${t.wa && t.wa.ready ? "No WhatsApp number for this person: add it in Settings → WhatsApp" : "WhatsApp isn't connected (Settings → WhatsApp)"}"`}>Follow up on WhatsApp</button>` : ""}
      ${!closed ? '<button class="btn btn-sm btn-ghost" data-edit>Edit brief</button>' : ""}
      ${!closed ? '<button class="btn btn-sm btn-ghost" data-move="cancelled" data-confirm="Cancel this task? It moves to History.">Cancel task</button>' : ""}
      <button class="btn btn-sm btn-ghost btn-danger" data-delete>Delete</button></div>` : "";

  const launch = t.launch || {};
  const nAds = typeof launch.ads === "number" ? launch.ads : Object.keys(launch.ads || {}).length;
  const launchInfo = launch.campaign_id ? `<div class="tk-launched">Launched on Meta${launch.strategy_name ? ` with <b>${esc(launch.strategy_name)}</b>` : ""}:
      campaign <b>${esc(launch.campaign_id)}</b>, ${nAds} ad(s) from this task, all paused${(launch.with || []).length ? `, together with ${launch.with.map((i) => `<a href="#" data-open="${i}">#${i}</a>`).join(", ")}` : ""}.
      <a href="https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${esc(launch.account_id || "")}&selected_campaign_ids=${esc(launch.campaign_id)}" target="_blank" rel="noopener">Open in Ads Manager ↗</a></div>`
    : launch.batch ? `<div class="warn">Launch #${esc(launch.batch)} started but hasn't finished. Press “Launch ads on Meta…” to carry it on.</div>` : "";

  $("dBody").innerHTML = `
    <div class="tk-head">
      <span class="tk-st tk-st-${t.status}">${esc(t.status_name)}</span>
      <span>${t.kind === "listing" ? "Listing + ads" : "Custom task"}</span>
      ${t.store ? `<span>· ${esc(t.store)}</span>` : ""}
      <span>· ${t.assignee ? esc(t.assignee) : "Not given to anyone"}</span>
      ${t.due ? `<span>· ${dueText(t.due, state.data && state.data.today)}</span>` : ""}
      ${t.priority === "high" ? '<span class="tk-high">High priority</span>' : ""}
    </div>
    ${t.special_notes || recs.length || (manage && !closed) ? `<div class="tk-special"><b>Special instructions</b>
      ${t.special_notes ? `<p>${esc(t.special_notes).replace(/\n/g, "<br>")}</p>` : ""}
      ${recs.length ? `<div class="tk-recs">${recs.map((x) => player(x, manage && !closed)).join("")}</div>` : ""}
      ${manage && !closed ? `<div class="tk-recbar"><button type="button" class="btn btn-sm" data-rec="voice">🎙 Record voice note</button>
        <button type="button" class="btn btn-sm" data-rec="screen">🖥 Record screen</button>
        <label class="btn btn-sm">⬆ Upload a recording<input type="file" accept="audio/*,video/*" data-recfile multiple hidden></label></div>` : ""}
    </div>` : ""}
    ${launchInfo}
    <section class="tk-sec"><h3>The brief <em class="tk-opt">from ${esc((t.created_by || "").split("@")[0])}</em></h3>
      ${briefRows.length ? `<dl class="tk-dl">${briefRows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>` : '<p class="tk-empty">No details given</p>'}
      ${adminFiles.length ? `<div class="tk-files">${adminFiles.map((x) => fileTile(x, manage && !closed)).join("")}</div>` : ""}
      ${manage && !closed ? uploader("admin", "file", "+ Add reference files to the brief") : ""}
    </section>
    <section class="tk-sec tk-work"><h3>${t.kind === "listing" ? "What was made" : "The work"}</h3>
      ${handBack}
      ${f("notes", "Notes for the admin", "", 2)}
    </section>
    <div class="tk-acts">${acts.join("")}</div>
    ${adminActs}
    <section class="tk-sec"><h3>Activity</h3>
      <div class="tk-log">${t.log.slice().reverse().map((l) => `<div class="tk-log-row ${l.action}"><b>${esc(l.who)}</b>
        <span>${l.action === "comment" ? esc(l.text).replace(/\n/g, "<br>") : esc(l.text)}</span><time>${when(l.at)}</time></div>`).join("")}</div>
      ${t.status !== "cancelled" ? `<div class="tk-comment"><textarea id="cText" class="control" rows="2" placeholder="Write a comment or question"></textarea>
        <button class="btn btn-sm" data-comment>Post</button></div>` : ""}
    </section>`;
}

function readResult() {
  const out = { custom: {}, cant: {} };
  for (const el of $("dBody").querySelectorAll("[data-r]")) out[el.dataset.r] = el.value;
  for (const el of $("dBody").querySelectorAll("[data-custom]")) out.custom[el.dataset.custom] = el.value;
  for (const el of $("dBody").querySelectorAll("[data-cant]")) out.cant[el.dataset.cant] = el.value;
  return out;
}

async function saveResult(submit) {
  const t = state.task;
  try {
    await api(`/api/tasks/${t.id}/result`, { json: { result: readResult(), submit } });
    toast(submit ? "Sent for review" : "Saved");
    await openTask(t.id);
    load();
  } catch (e) { toast(e.message, true); }
}

async function move(status, comment = "") {
  try {
    await api(`/api/tasks/${state.task.id}/status`, { json: { status, comment } });
    toast("Done");
    await openTask(state.task.id);
    load();
  } catch (e) { toast(e.message, true); }
}

async function sendFiles(taskId, side, kind, files) {
  const fd = new FormData();
  fd.append("side", side);
  fd.append("kind", kind);
  for (const f of files) fd.append("files", f);
  const r = await fetch(`/api/tasks/${taskId}/files`, { method: "POST", body: fd });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || body.error || "Upload failed.");
  return body;
}

async function upload(drop, files) {
  if (!files.length) return;
  // keep what staff typed: save the hand-back first, quietly
  if (drop.dataset.side === "staff" && $("dBody").querySelector("[data-r]:not([disabled])")) {
    try { await api(`/api/tasks/${state.task.id}/result`, { json: { result: readResult(), submit: false } }); } catch { /* upload anyway */ }
  }
  drop.classList.add("busy");
  drop.querySelector("span").textContent = `Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`;
  try {
    const body = await sendFiles(state.task.id, drop.dataset.side, drop.dataset.kind, files);
    toast(`Uploaded ${body.added}`);
  } catch (e) { toast(e.message, true); }
  await openTask(state.task.id);
  load();
}

/* ------------------------------------------------ voice notes + screen recordings */

let recorder = null;
async function record(kind) {
  if (recorder) { toast("A recording is already running: press Stop first.", true); return null; }
  if (!navigator.mediaDevices || !window.MediaRecorder) { toast("This browser can't record. Use Chrome, or upload a recording.", true); return null; }
  let stream, extra = [];
  try {
    if (kind === "voice") stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    else {
      const screen = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      let mic = null;
      try { mic = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { /* screen only */ }
      extra = [screen, mic].filter(Boolean);
      stream = new MediaStream([...screen.getVideoTracks(), ...(mic ? mic.getAudioTracks() : screen.getAudioTracks())]);
    }
  } catch {
    toast(`Recording didn't start: allow the ${kind === "voice" ? "microphone" : "screen and microphone"} when the browser asks.`, true);
    return null;
  }
  const types = kind === "voice" ? ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"] : ["video/webm;codecs=vp9,opus", "video/webm", "video/mp4"];
  const mime = types.find((x) => MediaRecorder.isTypeSupported(x)) || "";
  const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  const started = Date.now();
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    $("recTime").textContent = `Recording ${kind === "voice" ? "voice" : "screen"} ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 500);
  recorder = mr;
  $("recBar").hidden = false;
  return new Promise((resolve) => {
    mr.onstop = () => {
      clearInterval(tick);
      for (const s of [stream, ...extra]) for (const tr of s.getTracks()) tr.stop();
      recorder = null;
      $("recBar").hidden = true;
      const type = (mr.mimeType || mime || (kind === "voice" ? "audio/webm" : "video/webm")).split(";")[0];
      const ext = type.includes("mp4") ? (kind === "voice" ? "m4a" : "mp4") : "webm";
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
      resolve(chunks.length ? new File(chunks, `${kind === "voice" ? "voice-note" : "screen-recording"}-${stamp}.${ext}`, { type }) : null);
    };
    for (const tr of stream.getVideoTracks()) tr.onended = () => { if (mr.state !== "inactive") mr.stop(); };  // "Stop sharing"
    mr.start(1000);
  });
}
$("recStop").onclick = () => { if (recorder && recorder.state !== "inactive") recorder.stop(); };

async function addRecordings(files) {
  files = files.filter(Boolean);
  if (!files.length) return;
  if (!$("editor").hidden) {                 // new / edited task: sent when it's saved
    state.recs.push(...files);
    renderRecs();
    return;
  }
  try {
    toast("Uploading the recording…");
    await sendFiles(state.task.id, "admin", "instruction", files);
    toast("Recording added to the special instructions");
  } catch (e) { toast(e.message, true); }
  await openTask(state.task.id);
}

function renderRecs() {
  $("eRecs").innerHTML = state.recs.map((f, i) => `<div class="tk-recq">${f.type.startsWith("audio") ? "🎙" : "🖥"} ${esc(f.name)}
    <span class="tk-opt">${(f.size / 1048576).toFixed(1)} MB · sent when you save</span><button type="button" data-unrec="${i}">Remove</button></div>`).join("");
}

/* ------------------------------------------------ launch on Meta: one or several approved tasks, with a strategy */

async function openLauncher(ids) {
  $("launcher").hidden = false;
  const box = $("lBody");
  box.innerHTML = '<p class="empty">Reading the ad account…</p>';
  let o;
  try { o = await api(`/api/tasks/launch/options?ids=${ids.join(",")}`); } catch (e) { box.innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
  const d = o.defaults, pend = o.pending;
  const blocked = o.tasks.some((t) => t.problems.length);
  const sel = (list, v, none) => `<option value="">${none}</option>` + list.map((x) => `<option value="${esc(x.id)}"${x.id === v ? " selected" : ""}>${esc(x.name)} (${esc(x.id)})</option>`).join("");
  box.innerHTML = `
    <p class="hint">Into <b>${esc(o.account || o.account_id)}</b> (${esc(o.store)}, ${esc(o.currency)}). Everything is created <b>paused</b>:
      check it in Ads Manager, then switch it on there.</p>
    ${o.errors.length ? `<p class="warn">${o.errors.map(esc).join("<br>")}</p>` : ""}
    ${pend ? `<p class="warn">This launch (#${pend.id}) was started before and ${pend.status === "waiting" ? "is waiting for Meta to process videos" : "stopped"}${pend.error ? `: ${esc(pend.error)}` : ""}.
      Launch carries on where it stopped, with the same strategy.</p>` : ""}
    <h4 class="tk-formhead">${o.tasks.length} landing page${o.tasks.length > 1 ? "s" : ""}</h4>
    <div class="tk-ltasks">${o.tasks.map((t) => `<div class="tk-ltask${t.problems.length ? " bad" : ""}">
      <div><b>#${t.id} ${esc(t.title)}</b><br>${t.landing_url ? linkify(t.landing_url) : "<em>no landing page</em>"}
        ${t.headline ? `<br><span class="tk-opt">“${esc(t.headline)}”</span>` : ""}
        ${t.problems.length ? `<br><span class="tk-bad">This task ${esc(t.problems.join(", "))}.</span>` : ""}</div>
      <div class="tk-lthumbs">${t.creatives.slice(0, 6).map((c) => c.video ? '<span class="tk-fileicon">VIDEO</span>' : `<img src="/api/tasks/file/${c.id}?thumb=1" alt="">`).join("")}
        <span class="tk-opt">${t.creatives.length} creative${t.creatives.length === 1 ? "" : "s"}</span></div></div>`).join("")}</div>
    <h4 class="tk-formhead">Strategy</h4>
    <div class="tk-strats">${o.strategies.map((s) => `<label class="tk-strat${pend && pend.strategy !== s.key ? " off" : ""}">
      <input type="radio" name="lStrat" value="${s.key}" data-budget="${s.budget}"${(pend ? pend.strategy : d.strategy) === s.key ? " checked" : ""}${pend ? " disabled" : ""}>
      <span><b>${esc(s.name)}</b><em>${esc(s.about)}</em></span></label>`).join("")}</div>
    <p class="hint">These are starter strategies; tell us your own tested ones and they'll be added here.</p>
    <label class="field"><span>Campaign name</span><input class="control" id="lName" value="${esc(d.campaign_name)}"></label>
    <div class="tk-row">
      <label class="field"><span>Facebook Page</span><select class="control" id="lPage">${sel(o.pages, d.page_id, "Pick the Page")}</select></label>
      <label class="field"><span>Pixel (optimise for purchases)</span>${o.pixels.length ? `<select class="control" id="lPixel">${sel(o.pixels, d.pixel_id, "Pick the pixel")}</select>`
        : `<input class="control" id="lPixel" value="${esc(d.pixel_id)}" placeholder="Pixel ID">`}</label>
    </div>
    <div class="tk-row">
      <label class="field"><span id="lBudgetLbl">Daily budget</span><input class="control" id="lBudget" inputmode="decimal" value="${esc(d.daily_budget)}"></label>
      <label class="field"><span>Countries</span><input class="control" id="lCountries" value="${esc(d.countries)}" placeholder="US CA GB"></label>
    </div>
    <p class="hint">Each creative becomes one ad, going to its own task's landing page with that task's ad copy.</p>
    <div class="row-actions"><button class="btn btn-primary" id="lGo"${blocked ? " disabled" : ""}>${pend ? "Carry on the launch" : "Create paused campaign"}</button>
      <button class="btn" data-close>Close</button></div>`;
  const budgetLabel = () => {
    const r = box.querySelector("input[name=lStrat]:checked");
    $("lBudgetLbl").textContent = `Daily budget (${o.currency}) ${r && r.dataset.budget === "campaign" ? "for the whole campaign" : ids.length > 1 && r && r.value !== "x" ? "per ad set (each landing page)" : "for the ad set"}`;
  };
  box.querySelectorAll("input[name=lStrat]").forEach((x) => { x.onchange = budgetLabel; });
  budgetLabel();
  $("lGo").onclick = async () => {
    const btn = $("lGo");
    const strat = box.querySelector("input[name=lStrat]:checked");
    btn.disabled = true;
    btn.textContent = "Creating on Meta… (videos can take a minute)";
    try {
      const r = await api("/api/tasks/launch", { json: { ids, strategy: strat ? strat.value : "",
        campaign_name: $("lName").value, page_id: $("lPage").value, pixel_id: $("lPixel").value,
        daily_budget: $("lBudget").value, countries: $("lCountries").value } });
      if (r.waiting.length) {
        toast(`Campaign made (paused). Meta is still processing ${r.waiting.length} video(s): press Carry on in a minute.`, true);
        openLauncher(ids);
      } else {
        toast(`Done: ${r.ads} paused ad(s) on Meta. Switch them on in Ads Manager.`);
        $("launcher").hidden = true;
        state.sel.clear();
        if (state.task) await openTask(state.task.id);
      }
      load();
    } catch (e) {
      toast(e.message, true);
      if (e.body && e.body.batch_id) openLauncher(ids);
      else { btn.disabled = false; btn.textContent = "Create paused campaign"; }
    }
  };
}

/* ------------------------------------------------ new / edit */

function setKind(kind) {
  state.kind = kind;
  for (const b of $("eKind").querySelectorAll("button")) b.classList.toggle("on", b.dataset.kind === kind);
  for (const el of $("editor").querySelectorAll("[data-listing]")) el.hidden = kind !== "listing";
  for (const el of $("editor").querySelectorAll("[data-custom-only]")) el.hidden = kind !== "custom";
}

function renderTodo() {
  const names = todoNames();
  const custom = state.todo.filter((k) => !names[k]);
  $("eTodo").innerHTML = Object.entries(names).map(([k, v]) => `<label><input type="checkbox" value="${k}"${state.todo.includes(k) ? " checked" : ""}> ${esc(v)}</label>`).join("")
    + custom.map((k) => `<label class="tk-custom"><input type="checkbox" value="${esc(k)}" checked> ${esc(k)}</label>`).join("");
}

function readTodo() {
  return [...$("eTodo").querySelectorAll("input:checked")].map((x) => x.value);
}

function openEditor(t = null) {
  state.editing = t ? t.id : null;
  state.editBrief = (t && t.brief) || {};
  const b = state.editBrief;
  $("eTitle").textContent = t ? "Edit task" : "New task";
  $("eSave").textContent = t ? "Save" : "Create task";
  $("eName").value = t ? t.title : "";
  $("eStore").value = t ? (t.store_id || "") : ($("fStore").value || "");
  $("eWho").value = t ? (t.assignee_id || "") : "";
  $("eDue").value = t ? (t.due || "") : "";
  $("ePrio").value = t ? t.priority : "normal";
  $("eNotes").value = t ? (t.special_notes || "") : "";
  $("eCompetitor").value = b.competitor_ads || "";
  $("eCompLanding").value = b.competitor_landing || "";
  $("eCompProducts").value = b.competitor_products || "";
  $("eDesc").value = b.description || "";
  state.todo = t ? (b.todo || []) : Object.keys(todoNames());
  renderTodo();
  state.recs = [];
  renderRecs();
  setKind(t ? t.kind : "listing");
  waHint();
  $("editor").hidden = false;
  $("eName").focus();
}

function waHint() {
  const d = state.data || {};
  const p = (d.people || []).find((x) => String(x.id) === $("eWho").value);
  const el = $("eWa");
  el.hidden = !p || !d.wa_ready;
  if (!p) return;
  el.textContent = p.wa ? `${p.name} gets this on WhatsApp, with Doing / Done buttons.`
    : `${p.name} has no WhatsApp number yet, so they won't get a message (owner: Settings → WhatsApp).`;
  el.classList.toggle("tk-wa-off", !p.wa);
}

async function saveEditor() {
  const listing = state.kind === "listing";
  // older tasks keep the brief fields this form no longer shows
  const brief = listing ? { ...state.editBrief, competitor_ads: $("eCompetitor").value, competitor_landing: $("eCompLanding").value,
    competitor_products: $("eCompProducts").value, todo: readTodo() } : { ...state.editBrief, description: $("eDesc").value };
  const body = { kind: state.kind, title: $("eName").value, store_id: $("eStore").value, assignee_id: $("eWho").value,
    due: $("eDue").value, priority: $("ePrio").value, special_notes: $("eNotes").value, brief };
  if (listing && !brief.todo.length && !confirm("Nothing is ticked under To do. Create it anyway?")) return;
  const btn = $("eSave");
  btn.disabled = true;
  try {
    const r = state.editing ? await api(`/api/tasks/${state.editing}`, { method: "PUT", json: body }) : await api("/api/tasks", { json: body });
    const id = state.editing || r.id;
    if (state.recs.length) {
      btn.textContent = "Uploading recordings…";
      try { await sendFiles(id, "admin", "instruction", state.recs); } catch (e) { toast(`Task saved, but the recording didn't upload: ${e.message}`, true); }
      state.recs = [];
    }
    toast(`${state.editing ? "Saved" : "Task made"}${r.whatsapp ? ` · ${r.whatsapp}` : ""}`, /failed|no WhatsApp/.test(r.whatsapp || ""));
    $("editor").hidden = true;
    await openTask(id);
    load();
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
  btn.textContent = state.editing ? "Save" : "Create task";
}

/* ------------------------------------------------ end of day */

const ACTION_NAMES = { created: "Created", status: "", comment: "Comment", file: "", edit: "", launch: "", whatsapp: "", followup: "" };

async function loadDay() {
  if (!state.data) await load();
  if (!$("day").value) $("day").value = (state.data && state.data.today) || new Date().toISOString().slice(0, 10);
  const q = new URLSearchParams({ day: $("day").value });
  if ($("dayPerson").value) q.set("person", $("dayPerson").value);
  $("dayOut").innerHTML = '<p class="empty">Loading…</p>';
  let d;
  try { d = await api("/api/tasks/day?" + q); } catch (e) { $("dayOut").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  $("dayOut").innerHTML = d.people.map((r) => {
    const tasksTouched = new Set(r.events.map((e) => e.task_id)).size;
    return `<section class="panel tk-dayp">
      <div class="panel-head"><h2>${esc(r.person.name)}</h2>
        <span class="hint">${r.events.length ? `${tasksTouched} task(s) worked on · ${r.submitted} submitted · ${r.files.length} file(s) uploaded` : "Nothing logged this day"}</span></div>
      <div class="tk-dayg">
        <div><h4>What they did</h4>${r.events.length ? r.events.map((e) => `<div class="tk-log-row ${e.action}">
          <time>${new Date(e.at.replace(" ", "T") + "Z").toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>
          <span><a href="#" data-open="${e.task_id}">${esc(e.title)}</a>${e.store ? ` <em class="tk-opt">${esc(e.store)}</em>` : ""}<br>
          ${ACTION_NAMES[e.action] ? ACTION_NAMES[e.action] + ": " : ""}${esc(e.text)}</span></div>`).join("") : '<p class="tk-empty">No activity</p>'}
          ${r.files.length ? `<div class="tk-files small">${r.files.filter((f) => IMG.test(f.name)).slice(0, 12).map((f) =>
            `<a href="/api/tasks/file/${f.id}" target="_blank" rel="noopener" class="tk-file-view"><img src="/api/tasks/file/${f.id}?thumb=1" alt=""></a>`).join("")}</div>` : ""}</div>
        <div><h4>Still open</h4>${r.open.length ? r.open.map((o) => `<div class="tk-openrow"><a href="#" data-open="${o.id}">${esc(o.title)}</a>
          <span class="tk-st tk-st-${o.status}">${esc(o.status_name)}</span>${o.due ? dueText(o.due, d.day) : ""}</div>`).join("") : '<p class="tk-empty">No open tasks</p>'}</div>
      </div></section>`;
  }).join("") || '<p class="empty">Nobody has the Tasks desk yet. Give people access in Settings → People.</p>';
}

/* ------------------------------------------------ page */

function showPage(p) {
  state.page = p;
  PDUrl.set({ page: p === "board" ? null : p });
  for (const b of $("pages").querySelectorAll("button")) b.classList.toggle("on", b.dataset.page === p);
  $("boardPage").hidden = p !== "board";
  $("historyPage").hidden = p !== "history";
  $("dayPage").hidden = p !== "day";
  $("bar").hidden = p === "day";
  selBar();
  if (p === "day") loadDay(); else load();
}

document.addEventListener("click", (e) => {
  if (e.target.closest(".tk-sel")) return;            // the Launch tick box on a card, not the card
  const el = e.target.closest("[data-open],[data-close],[data-move],[data-save],[data-submit],[data-changes],[data-launch],[data-edit],[data-delete],[data-comment],[data-rmfile],[data-followup],[data-rec],[data-unrec]");
  if (!el) return;
  if (el.dataset.open) { e.preventDefault(); openTask(+el.dataset.open); return; }
  if (el.hasAttribute("data-close")) {
    if (el.closest("#editor")) $("editor").hidden = true;
    else if (el.closest("#launcher")) $("launcher").hidden = true;
    else closeTask();
    return;
  }
  if (el.dataset.rec) { record(el.dataset.rec).then((f) => addRecordings([f])); return; }
  if (el.dataset.unrec) { state.recs.splice(+el.dataset.unrec, 1); renderRecs(); return; }
  if (el.dataset.move) {
    if (el.dataset.confirm && !confirm(el.dataset.confirm)) return;
    move(el.dataset.move);
  } else if (el.hasAttribute("data-save")) saveResult(false);
  else if (el.hasAttribute("data-submit")) saveResult(true);
  else if (el.hasAttribute("data-changes")) {
    const why = prompt("What needs changing? (the staff member sees this)");
    if (why && why.trim()) move("changes", why.trim());
  } else if (el.hasAttribute("data-launch")) openLauncher([state.task.id]);
  else if (el.hasAttribute("data-edit")) openEditor(state.task);
  else if (el.hasAttribute("data-followup")) {
    if (!confirm(`WhatsApp ${state.task.assignee} asking for an update on this task? Their reply shows here and comes to you.`)) return;
    el.disabled = true;
    api(`/api/tasks/${state.task.id}/followup`, { json: {} }).then((r) => { toast(r.text); openTask(state.task.id); })
      .catch((x) => { toast(x.message, true); el.disabled = false; });
  }
  else if (el.hasAttribute("data-delete")) {
    if (!confirm("Delete this task and all its files for good? (Cancel keeps it in History instead.)")) return;
    api(`/api/tasks/${state.task.id}`, { method: "DELETE" }).then(() => { toast("Deleted"); closeTask(); load(); }).catch((x) => toast(x.message, true));
  } else if (el.hasAttribute("data-comment")) {
    const text = $("cText").value.trim();
    if (!text) return;
    api(`/api/tasks/${state.task.id}/comment`, { json: { text } }).then(() => openTask(state.task.id)).catch((x) => toast(x.message, true));
  } else if (el.dataset.rmfile) {
    if (!confirm("Remove this file?")) return;
    api(`/api/tasks/file/${el.dataset.rmfile}`, { method: "DELETE" }).then(() => openTask(state.task.id)).catch((x) => toast(x.message, true));
  }
});

// uploads: pick or drop
document.addEventListener("change", (e) => {
  if (e.target.dataset.sel) {
    const id = +e.target.dataset.sel;
    if (e.target.checked) state.sel.add(id); else state.sel.delete(id);
    e.target.closest(".tk-card").classList.toggle("picked", e.target.checked);
    selBar();
    return;
  }
  if (e.target.matches("[data-recfile], #eRecFile")) { addRecordings([...e.target.files]); e.target.value = ""; return; }
  const drop = e.target.closest(".tk-drop");
  if (drop && e.target.type === "file") upload(drop, [...e.target.files]);
});
document.addEventListener("dragover", (e) => { const d = e.target.closest(".tk-drop"); if (d) { e.preventDefault(); d.classList.add("over"); } });
document.addEventListener("dragleave", (e) => { const d = e.target.closest(".tk-drop"); if (d) d.classList.remove("over"); });
document.addEventListener("drop", (e) => {
  const d = e.target.closest(".tk-drop");
  if (!d) return;
  e.preventDefault();
  d.classList.remove("over");
  upload(d, [...e.dataTransfer.files]);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches(".tk-card")) { openTask(+e.target.dataset.open); return; }
  if (e.key !== "Escape") return;
  if (!$("launcher").hidden) $("launcher").hidden = true;
  else if (!$("editor").hidden) $("editor").hidden = true; else if (!$("drawer").hidden) closeTask();
});

$("pages").onclick = (e) => { const b = e.target.closest("button[data-page]"); if (b) showPage(b.dataset.page); };
$("newTask").onclick = () => openEditor();
$("launchSel").onclick = () => openLauncher([...state.sel]);
$("eTodoAdd").onclick = () => {
  const v = $("eTodoNew").value.trim();
  if (!v) return;
  state.todo = [...readTodo(), v];
  $("eTodoNew").value = "";
  renderTodo();
};
$("eTodoNew").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("eTodoAdd").click(); } };
$("eSave").onclick = saveEditor;
$("eWho").onchange = waHint;
$("eKind").onclick = (e) => { const b = e.target.closest("button[data-kind]"); if (b) setKind(b.dataset.kind); };
$("fPerson").onchange = $("fStore").onchange = () => load();
let qTimer;
$("q").oninput = () => { clearTimeout(qTimer); qTimer = setTimeout(load, 300); };
$("day").onchange = $("dayPerson").onchange = loadDay;

(async () => {
  await (window.pdMe ? window.pdMe() : null);
  if (state.page === "day" && window.pdLevel !== "manage") state.page = "board";
  showPage(state.page);
  const t = PDUrl.get("task");
  if (t) openTask(+t);
  setInterval(() => { if (document.visibilityState === "visible" && state.page !== "day" && $("editor").hidden) load(); }, 60000);
})();
