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
  if (!r.ok) throw new Error(body.detail || body.error || `Something went wrong (${r.status}).`);
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

const PAGES = ["Collection page", "Product pages", "Landing page", "Advertorial", "Ad creatives", "Ad copy"];
const COLUMNS = [
  ["todo", "To do"], ["doing", "In progress"], ["changes", "Changes asked"],
  ["submitted", "Submitted for review"], ["approved", "Waiting to launch"],
];
const CTA_NAMES = { SHOP_NOW: "Shop now", LEARN_MORE: "Learn more", ORDER_NOW: "Order now", BUY_NOW: "Buy now",
  GET_OFFER: "Get offer", SIGN_UP: "Sign up" };
const IMG = /\.(jpe?g|png|gif|webp|heic|heif)$/i;
const VID = /\.(mp4|mov|m4v|webm|avi)$/i;

let state = { page: PDUrl.get("page") || "board", data: null, open: null, editing: null, kind: "listing" };

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
  const pages = (t.brief.pages || []).slice(0, 4).map((p) => `<span class="tk-tag">${esc(p)}</span>`).join("");
  return `<button class="tk-card${t.priority === "high" ? " high" : ""}" data-open="${t.id}">
    <div class="tk-card-top"><span class="tk-kindtag ${t.kind}">${t.kind === "listing" ? "Listing + ads" : "Custom"}</span>
      ${t.priority === "high" ? '<span class="tk-high">High</span>' : ""}${t.special_notes ? '<span class="tk-noteflag" title="Has special notes">Notes</span>' : ""}</div>
    <strong>${esc(t.title)}</strong>
    ${t.store ? `<span class="tk-meta">${esc(t.store)}</span>` : ""}
    ${pages ? `<div class="tk-tags">${pages}</div>` : ""}
    <div class="tk-card-foot"><span class="tk-who">${t.assignee ? esc(t.assignee) : "<em>Not given to anyone</em>"}</span>
      ${dueText(t.due, today)}${t.files_staff ? `<span class="tk-files" title="Files staff uploaded">📎 ${t.files_staff}</span>` : ""}</div>
  </button>`;
}

function renderBoard() {
  const by = {};
  for (const t of state.data.tasks) (by[t.status] = by[t.status] || []).push(t);
  $("boardPage").innerHTML = COLUMNS.map(([k, name]) => `
    <div class="tk-col tk-col-${k}">
      <div class="tk-col-head"><span>${name}</span><b>${(by[k] || []).length}</b></div>
      <div class="tk-col-body">${(by[k] || []).map(card).join("") || '<p class="tk-empty">Nothing here</p>'}</div>
    </div>`).join("");
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

function renderTask() {
  const t = state.task;
  const b = t.brief, r = t.result;
  const manage = t.manage, closed = ["launched", "done", "cancelled"].includes(t.status);
  const canWork = (t.mine || manage) && !closed && (manage || !["submitted", "approved"].includes(t.status));
  const adminFiles = t.files.filter((f) => f.side === "admin");
  const creatives = t.files.filter((f) => f.side === "staff" && f.kind === "creative");
  const otherFiles = t.files.filter((f) => f.side === "staff" && f.kind !== "creative");
  const me = state.data && state.data.me ? state.data.me.email : "";
  const canRm = (f) => !closed && (manage || (f.by === me && t.status !== "approved"));
  $("dTitle").textContent = t.title;

  const briefRows = [
    ["Collection", b.collection && esc(b.collection)], ["Products", lines(b.products).map(linkify).join("<br>")],
    ["Build", (b.pages || []).map((p) => `<span class="tk-tag">${esc(p)}</span>`).join(" ")],
    ["Ad angle", b.angle && esc(b.angle).replace(/\n/g, "<br>")], ["Headline ideas", lines(b.headlines).map(esc).join("<br>")],
    ["Daily budget", b.budget && esc(b.budget)], ["Countries", b.countries && esc(b.countries)], ["Details", b.description && esc(b.description).replace(/\n/g, "<br>")],
  ].filter(([, v]) => v);

  const f = (k, label, ph = "", rows = 0) => rows
    ? `<label class="field"><span>${label}</span><textarea class="control" data-r="${k}" rows="${rows}" placeholder="${esc(ph)}"${canWork ? "" : " disabled"}>${esc(r[k] || "")}</textarea></label>`
    : `<label class="field"><span>${label}</span><input class="control" data-r="${k}" placeholder="${esc(ph)}" value="${esc(r[k] || "")}"${canWork ? "" : " disabled"}></label>`;

  const handBack = t.kind === "listing" ? `
      <div class="tk-row">${f("collection_url", "Collection link", "https://store.com/collections/…")}${f("landing_url", "Landing page link (the ads go here)", "https://…")}</div>
      <div class="tk-row">${f("advertorial_url", "Advertorial link", "https://…")}${f("other_links", "Other links", "one per line", 1)}</div>
      ${f("product_urls", "Product page links (one per line)", "", 3)}
      <h4>Ad copy</h4>
      ${f("primary_text", "Primary text", "The text above the ad", 5)}
      <div class="tk-row">${f("headline", "Headline (one per line if several)", "", 2)}${f("ad_description", "Description (optional)", "", 2)}</div>
      <label class="field"><span>Button</span><select class="control" data-r="cta"${canWork ? "" : " disabled"}>${Object.entries(CTA_NAMES).map(([k, v]) =>
        `<option value="${k}"${(r.cta || "SHOP_NOW") === k ? " selected" : ""}>${v}</option>`).join("")}</select></label>
      <h4>Ad creatives <em class="tk-opt">(these go in the ads)</em></h4>
      <div class="tk-files">${creatives.map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">No creatives yet</p>'}</div>
      ${canWork ? uploader("staff", "creative", "+ Upload ad creatives") : ""}
      <h4>Other files</h4>
      <div class="tk-files">${otherFiles.map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">None</p>'}</div>
      ${canWork ? uploader("staff", "file", "+ Upload other files") : ""}`
    : `${f("other_links", "Links (one per line)", "", 3)}
      <h4>Files</h4>
      <div class="tk-files">${creatives.concat(otherFiles).map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">None</p>'}</div>
      ${canWork ? uploader("staff", "file", "+ Upload files") : ""}`;

  // the buttons that move the task on, for who's looking
  const acts = [];
  if (!closed) {
    if ((t.mine || manage) && ["todo", "changes"].includes(t.status)) acts.push(`<button class="btn" data-move="doing">Start</button>`);
    if (canWork && ["todo", "doing", "changes"].includes(t.status)) acts.push(`<button class="btn" data-save>Save</button><button class="btn btn-primary" data-submit>Submit for review</button>`);
    if (t.mine && !manage && t.status === "submitted") acts.push(`<button class="btn" data-move="doing">Take back to edit</button>`);
    if (manage && t.status === "submitted") acts.push(`<button class="btn btn-primary" data-move="approved">Approve</button><button class="btn" data-changes>Send back for changes</button>`);
    if (manage && t.status === "approved" && t.kind === "listing") acts.push(`<button class="btn btn-primary" data-launch>Launch ads on Meta…</button><button class="btn" data-move="launched" data-confirm="Mark as launched without creating the ads here?">Mark launched (set up by hand)</button>`);
    if (manage && t.status === "approved" && t.kind === "custom") acts.push(`<button class="btn btn-primary" data-move="done">Mark done</button>`);
    if (manage && t.status === "approved") acts.push(`<button class="btn" data-changes>Send back for changes</button>`);
    if (canWork && t.status === "approved" && manage) acts.push(`<button class="btn" data-save>Save edits</button>`);
  } else if (manage) {
    acts.push(`<button class="btn" data-move="approved" data-confirm="Reopen this task (back to Waiting to launch)?">Reopen</button>`);
  }
  const adminActs = manage ? `<div class="tk-admin-acts">
      ${!closed ? '<button class="btn btn-sm btn-ghost" data-edit>Edit brief</button>' : ""}
      ${!closed ? '<button class="btn btn-sm btn-ghost" data-move="cancelled" data-confirm="Cancel this task? It moves to History.">Cancel task</button>' : ""}
      <button class="btn btn-sm btn-ghost btn-danger" data-delete>Delete</button></div>` : "";

  const launch = t.launch || {};
  const launchInfo = launch.campaign_id ? `<div class="tk-launched">Meta campaign <b>${esc(launch.campaign_id)}</b> · ${Object.keys(launch.ads || {}).length} ad(s), all paused.
      <a href="https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=${esc(launch.account_id || "")}&selected_campaign_ids=${esc(launch.campaign_id)}" target="_blank" rel="noopener">Open in Ads Manager ↗</a></div>` : "";

  $("dBody").innerHTML = `
    <div class="tk-head">
      <span class="tk-st tk-st-${t.status}">${esc(t.status_name)}</span>
      <span>${t.kind === "listing" ? "Listing + ads" : "Custom task"}</span>
      ${t.store ? `<span>· ${esc(t.store)}</span>` : ""}
      <span>· ${t.assignee ? esc(t.assignee) : "Not given to anyone"}</span>
      ${t.due ? `<span>· ${dueText(t.due, state.data && state.data.today)}</span>` : ""}
      ${t.priority === "high" ? '<span class="tk-high">High priority</span>' : ""}
    </div>
    ${t.special_notes ? `<div class="tk-special"><b>Special notes</b><p>${esc(t.special_notes).replace(/\n/g, "<br>")}</p></div>` : ""}
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
    <div id="launchBox"></div>
    ${adminActs}
    <section class="tk-sec"><h3>Activity</h3>
      <div class="tk-log">${t.log.slice().reverse().map((l) => `<div class="tk-log-row ${l.action}"><b>${esc(l.who)}</b>
        <span>${l.action === "comment" ? esc(l.text).replace(/\n/g, "<br>") : esc(l.text)}</span><time>${when(l.at)}</time></div>`).join("")}</div>
      ${t.status !== "cancelled" ? `<div class="tk-comment"><textarea id="cText" class="control" rows="2" placeholder="Write a comment or question"></textarea>
        <button class="btn btn-sm" data-comment>Post</button></div>` : ""}
    </section>`;
}

function readResult() {
  const out = {};
  for (const el of $("dBody").querySelectorAll("[data-r]")) out[el.dataset.r] = el.value;
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

async function upload(drop, files) {
  if (!files.length) return;
  const fd = new FormData();
  fd.append("side", drop.dataset.side);
  fd.append("kind", drop.dataset.kind);
  for (const f of files) fd.append("files", f);
  // keep what staff typed: save the hand-back first, quietly
  if (drop.dataset.side === "staff" && $("dBody").querySelector("[data-r]:not([disabled])")) {
    try { await api(`/api/tasks/${state.task.id}/result`, { json: { result: readResult(), submit: false } }); } catch { /* upload anyway */ }
  }
  drop.classList.add("busy");
  drop.querySelector("span").textContent = `Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`;
  try {
    const r = await fetch(`/api/tasks/${state.task.id}/files`, { method: "POST", body: fd });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || "Upload failed.");
    toast(`Uploaded ${body.added}`);
  } catch (e) { toast(e.message, true); }
  await openTask(state.task.id);
  load();
}

/* ------------------------------------------------ launch on Meta */

async function openLaunch() {
  const box = $("launchBox");
  box.innerHTML = '<div class="tk-launch"><p class="empty">Reading the ad account…</p></div>';
  let o;
  try { o = await api(`/api/tasks/${state.task.id}/launch`); } catch (e) { box.innerHTML = `<div class="tk-launch"><p class="warn">${esc(e.message)}</p></div>`; return; }
  const d = o.defaults;
  const creatives = state.task.files.filter((f) => f.side === "staff" && f.kind === "creative" && (IMG.test(f.name) || VID.test(f.name)));
  const done = (o.progress && o.progress.ads) || {};
  const sel = (list, v, none) => `<option value="">${none}</option>` + list.map((x) => `<option value="${esc(x.id)}"${x.id === v ? " selected" : ""}>${esc(x.name)} (${esc(x.id)})</option>`).join("");
  box.innerHTML = `<div class="tk-launch">
    <h3>Launch on Meta</h3>
    <p class="hint">Makes one campaign (Sales), one ad set and one ad per creative in <b>${esc(o.account || o.account_id)}</b> (${esc(o.currency)}).
      Everything is created <b>paused</b>: check it in Ads Manager, then switch it on there.</p>
    ${o.errors.length ? `<p class="warn">${o.errors.map(esc).join("<br>")}</p>` : ""}
    <label class="field"><span>Campaign name</span><input class="control" id="lName" value="${esc(d.campaign_name)}"></label>
    <div class="tk-row">
      <label class="field"><span>Facebook Page</span><select class="control" id="lPage">${sel(o.pages, d.page_id, "Pick the Page")}</select></label>
      <label class="field"><span>Pixel (optimise for purchases)</span>${o.pixels.length ? `<select class="control" id="lPixel">${sel(o.pixels, d.pixel_id, "Pick the pixel")}</select>`
        : `<input class="control" id="lPixel" value="${esc(d.pixel_id)}" placeholder="Pixel ID">`}</label>
    </div>
    <div class="tk-row">
      <label class="field"><span>Daily budget (${esc(o.currency)})</span><input class="control" id="lBudget" inputmode="decimal" value="${esc(d.daily_budget)}"></label>
      <label class="field"><span>Countries</span><input class="control" id="lCountries" value="${esc(d.countries)}" placeholder="US CA GB"></label>
    </div>
    <label class="field"><span>Ads go to</span><input class="control" id="lLink" value="${esc(d.link)}"></label>
    <label class="field"><span>Primary text</span><textarea class="control" id="lMsg" rows="4">${esc(d.message)}</textarea></label>
    <div class="tk-row">
      <label class="field"><span>Headline</span><input class="control" id="lHead" value="${esc(d.headline)}"></label>
      <label class="field"><span>Button</span><select class="control" id="lCta">${Object.entries(CTA_NAMES).map(([k, v]) => `<option value="${k}"${d.cta === k ? " selected" : ""}>${v}</option>`).join("")}</select></label>
    </div>
    <label class="field"><span>Description (optional)</span><input class="control" id="lDesc" value="${esc(d.description)}"></label>
    <div class="field"><span>Creatives (one ad each)</span><div class="tk-pick">${creatives.map((f) => `<label class="${done[f.id] ? "made" : ""}">
      <input type="checkbox" value="${f.id}" checked${done[f.id] ? " disabled" : ""}>
      ${IMG.test(f.name) ? `<img src="/api/tasks/file/${f.id}?thumb=1" alt="">` : '<span class="tk-fileicon">VIDEO</span>'}
      <span>${esc(f.name)}${done[f.id] ? " · made" : ""}</span></label>`).join("") || '<p class="tk-empty">No image or video creatives uploaded</p>'}</div></div>
    ${o.progress && o.progress.campaign_id ? `<p class="hint">Already made: campaign ${esc(o.progress.campaign_id)}${o.progress.adset_id ? ", ad set" : ""}, ${Object.keys(done).length} ad(s). Launch carries on from there.</p>` : ""}
    <div class="row-actions"><button class="btn btn-primary" id="lGo">Create paused ads</button><button class="btn" id="lCancel">Close</button></div>
  </div>`;
  $("lCancel").onclick = () => { box.innerHTML = ""; };
  $("lGo").onclick = async () => {
    const ids = [...box.querySelectorAll(".tk-pick input:checked")].map((x) => +x.value);
    const btn = $("lGo");
    btn.disabled = true;
    btn.textContent = "Creating on Meta… (videos can take a minute)";
    try {
      const r = await api(`/api/tasks/${state.task.id}/launch`, { json: {
        campaign_name: $("lName").value, page_id: $("lPage").value, pixel_id: $("lPixel").value,
        daily_budget: $("lBudget").value, countries: $("lCountries").value, link: $("lLink").value,
        message: $("lMsg").value, headline: $("lHead").value, description: $("lDesc").value, cta: $("lCta").value,
        file_ids: ids } });
      if (r.waiting.length) toast(`Campaign made (paused). Meta is still processing ${r.waiting.length} video(s): press Create again in a minute.`, true);
      else toast(`Done: ${r.ads} paused ad(s) on Meta. Switch them on in Ads Manager.`);
      await openTask(state.task.id);
      load();
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
      btn.textContent = "Create paused ads";
    }
  };
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ------------------------------------------------ new / edit */

function setKind(kind) {
  state.kind = kind;
  for (const b of $("eKind").querySelectorAll("button")) b.classList.toggle("on", b.dataset.kind === kind);
  for (const el of $("editor").querySelectorAll("[data-listing]")) el.hidden = kind !== "listing";
  for (const el of $("editor").querySelectorAll("[data-custom-only]")) el.hidden = kind !== "custom";
}

function openEditor(t = null) {
  state.editing = t ? t.id : null;
  const b = (t && t.brief) || {};
  $("eTitle").textContent = t ? "Edit task" : "New task";
  $("eSave").textContent = t ? "Save" : "Create task";
  $("eName").value = t ? t.title : "";
  $("eStore").value = t ? (t.store_id || "") : ($("fStore").value || "");
  $("eWho").value = t ? (t.assignee_id || "") : "";
  $("eDue").value = t ? (t.due || "") : "";
  $("ePrio").value = t ? t.priority : "normal";
  $("eNotes").value = t ? (t.special_notes || "") : "";
  $("eCollection").value = b.collection || "";
  $("eProducts").value = b.products || "";
  $("eAngle").value = b.angle || "";
  $("eHeadlines").value = b.headlines || "";
  $("eBudget").value = b.budget || "";
  $("eCountries").value = b.countries || "";
  $("eDesc").value = b.description || "";
  $("eDesc2").value = b.description || "";
  const picked = t ? (b.pages || []) : PAGES;
  $("ePages").innerHTML = PAGES.map((p) => `<label><input type="checkbox" value="${esc(p)}"${picked.includes(p) ? " checked" : ""}> ${esc(p)}</label>`).join("");
  setKind(t ? t.kind : "listing");
  $("editor").hidden = false;
  $("eName").focus();
}

async function saveEditor() {
  const listing = state.kind === "listing";
  const brief = listing ? {
    collection: $("eCollection").value, products: $("eProducts").value, angle: $("eAngle").value,
    headlines: $("eHeadlines").value, budget: $("eBudget").value, countries: $("eCountries").value,
    pages: [...$("ePages").querySelectorAll("input:checked")].map((x) => x.value), description: $("eDesc2").value,
  } : { description: $("eDesc").value };
  const body = { kind: state.kind, title: $("eName").value, store_id: $("eStore").value, assignee_id: $("eWho").value,
    due: $("eDue").value, priority: $("ePrio").value, special_notes: $("eNotes").value, brief };
  const btn = $("eSave");
  btn.disabled = true;
  try {
    if (state.editing) {
      await api(`/api/tasks/${state.editing}`, { method: "PUT", json: body });
      toast("Saved");
      $("editor").hidden = true;
      await openTask(state.editing);
    } else {
      const r = await api("/api/tasks", { json: body });
      toast("Task made");
      $("editor").hidden = true;
      await openTask(r.id);
    }
    load();
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
}

/* ------------------------------------------------ end of day */

const ACTION_NAMES = { created: "Created", status: "", comment: "Comment", file: "", edit: "", launch: "" };

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
  if (p === "day") loadDay(); else load();
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-open],[data-close],[data-move],[data-save],[data-submit],[data-changes],[data-launch],[data-edit],[data-delete],[data-comment],[data-rmfile]");
  if (!el) return;
  if (el.dataset.open) { e.preventDefault(); openTask(+el.dataset.open); return; }
  if (el.hasAttribute("data-close")) {
    if (el.closest("#editor")) $("editor").hidden = true; else closeTask();
    return;
  }
  if (el.dataset.move) {
    if (el.dataset.confirm && !confirm(el.dataset.confirm)) return;
    move(el.dataset.move);
  } else if (el.hasAttribute("data-save")) saveResult(false);
  else if (el.hasAttribute("data-submit")) saveResult(true);
  else if (el.hasAttribute("data-changes")) {
    const why = prompt("What needs changing? (the staff member sees this)");
    if (why && why.trim()) move("changes", why.trim());
  } else if (el.hasAttribute("data-launch")) openLaunch();
  else if (el.hasAttribute("data-edit")) openEditor(state.task);
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
  if (e.key !== "Escape") return;
  if (!$("editor").hidden) $("editor").hidden = true; else if (!$("drawer").hidden) closeTask();
});

$("pages").onclick = (e) => { const b = e.target.closest("button[data-page]"); if (b) showPage(b.dataset.page); };
$("newTask").onclick = () => openEditor();
$("eSave").onclick = saveEditor;
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
