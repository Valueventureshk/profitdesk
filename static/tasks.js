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
const storeTodo = () => (state.data && state.data.store_todo) || (state.task && state.task.store_todo) || {};
const storeChecks = () => (state.data && state.data.store_checks) || (state.task && state.task.store_checks) || {};
const todoLabel = (k) => todoNames()[k] || storeTodo()[k] || k;
const KIND_NAME = { listing: "Listing + ads", custom: "Custom", store: "New store", advertorial: "Advertorial + ad creative" };
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
  if (state.page === "queue") return loadQueue();
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
    <div class="tk-card-top"><span class="tk-kindtag ${t.kind}">${KIND_NAME[t.kind] || t.kind}</span>
      ${t.priority === "high" ? '<span class="tk-high">High</span>' : ""}${t.special_notes ? '<span class="tk-noteflag" title="Has special notes">Notes</span>' : ""}</div>
    <strong><span class="tk-id">T-${t.id}</span> ${esc(t.title)}</strong>
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
      <td><span class="name"><span class="tk-id">T-${t.id}</span> ${esc(t.title)}</span><span class="sub">${KIND_NAME[t.kind] || t.kind}</span></td>
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
  const adminFiles = t.files.filter((f) => f.side === "admin" && f.kind !== "instruction" && f.kind !== "product");
  const productPhotos = t.files.filter((f) => f.kind === "product");
  const creatives = t.files.filter((f) => f.side === "staff" && f.kind === "creative");
  const otherFiles = t.files.filter((f) => f.side === "staff" && f.kind !== "creative" && f.kind !== "product");
  const me = state.data && state.data.me ? state.data.me.email : "";
  const canRm = (f) => !closed && (manage || (f.by === me && t.status !== "approved"));
  const dis = canWork ? "" : " disabled";
  $("dTitle").textContent = `T-${t.id} · ${t.title}`;

  const links = (v) => lines(v).map(linkify).join("<br>");
  const todo = b.todo || null;                 // null = a task from before the to-do picks
  const briefRows = [
    ["Competitor ads", links(b.competitor_ads)], ["Their landing page", links(b.competitor_landing)],
    ["Their products", links(b.competitor_products)],
    ["To do", t.kind === "store" ? "" : (todo || []).map((k) => `<span class="tk-tag">${esc(todoLabel(k))}</span>`).join(" ")],
    ["Collection", b.collection && esc(b.collection)], ["Products", links(b.products)],
    ["Build", (b.pages || []).map((p) => `<span class="tk-tag">${esc(p)}</span>`).join(" ")],
    ["Ad angle", b.angle && esc(b.angle).replace(/\n/g, "<br>")], ["Headline ideas", lines(b.headlines).map(esc).join("<br>")],
    ["Daily budget", b.budget && esc(b.budget)], ["Countries", b.countries && esc(b.countries)],
    ["Details", b.description && esc(b.description).replace(/\n/g, "<br>")],
    ...(t.kind === "advertorial" ? [
      ["Product", b.product_url && linkify(b.product_url)],
      ["About the product", b.product_description && esc(b.product_description).replace(/\n/g, "<br>")],
      ["Product photos", productPhotos.length ? `<div class="tk-files">${productPhotos.map((x) => fileTile(x, manage && !closed)).join("")}</div>` : ""],
      ["Landing page", linkify(b.send_to_url || (b.product_url || "").replace(/(\/collections\/[^/?#]+)\/products\/.*$/, "$1"))],
      ["Competitor advertorial", b.competitor_advertorial ? linkify(b.competitor_advertorial) : "None: we write our own"],
      ["Reviews", [b.review_rating && `${esc(b.review_rating)} ★`, b.review_count && `${esc(b.review_count)} reviews`].filter(Boolean).join(" · ")],
      ["Review texts", b.reviews && `<details><summary>${lines(b.reviews).length} line(s)</summary>${esc(b.reviews).replace(/\n/g, "<br>")}</details>`],
    ] : []),
    ...(t.kind === "store" ? [
      ["Brand domain", b.brand_domain && linkify(/^https?:/.test(b.brand_domain) ? b.brand_domain : "https://" + b.brand_domain)],
      ["Shopify domain", b.shopify_domain && esc(b.shopify_domain)],
      ["Market", [b.country, b.language, b.currency].filter(Boolean).map(esc).join(" · ")],
      ["Ads on", b.ads_platform && esc({ meta: "Meta", google: "Google", both: "Meta + Google" }[b.ads_platform] || b.ads_platform)],
      ["Competitor", b.competitor_url && linkify(b.competitor_url)],
      ["Customer reviews", b.reviews && `<details><summary>${lines(b.reviews).length} line(s)</summary>${esc(b.reviews).replace(/\n/g, "<br>")}</details>`],
      ["Support email", b.support_email && b.support_email !== "no" && b.support_email !== "yes" ? esc(b.support_email) : ""],
      ["Store steps", (b.todo || []).map((k) => `<span class="tk-tag">${esc(todoLabel(k))}</span>`).join(" ")],
    ] : []),
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

  const advPage = r.page_id ? `<div class="tk-main tk-adv">
        <b>Advertorial page</b> ${r.page_published ? '<span class="tk-st tk-st-done">Live</span>' : '<span class="tk-st">Hidden until approved</span>'}<br>
        <a href="/tasks/${t.id}/advertorial" target="_blank" rel="noopener">Preview ↗</a> ·
        <a href="${esc(r.page_admin || "#")}" target="_blank" rel="noopener">Open in Shopify ↗</a>
        ${r.advertorial_url ? `<br><span class="hint">Link for the ads: ${linkify(r.advertorial_url)}</span>` : ""}</div>`
    : `<div class="tk-main tk-adv"><b>Advertorial page</b> <span class="tk-st">Not made yet</span>
        <p class="hint">In ChatGPT or Claude (EcomVital connector) say “do task T-${t.id}”. The page is made in the store, hidden until it's approved.</p></div>`;
  const handBack = t.kind === "advertorial" ? `${advPage}
      <h4>The ad</h4>${copyBox}
      <h4>Ad image <em class="tk-opt">(square)</em></h4>${creativeBox}
      <h4>Other files <em class="tk-opt">(page pictures, etc.)</em></h4>
      <div class="tk-files">${otherFiles.map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">None</p>'}</div>
      ${canWork ? uploader("staff", "file", "+ Upload page pictures") : ""}`
    : t.kind === "store" ? `<div class="tk-main">${f("landing_url", "Store link · the live store", "https://brand.com")}</div>
      ${(todo || []).map(item).join("")}
      ${f("other_links", "Other links (one per line)", "", 2)}
      <h4>Files</h4>
      <div class="tk-files">${creatives.concat(otherFiles).map((x) => fileTile(x, canRm(x))).join("") || '<p class="tk-empty">None</p>'}</div>
      ${canWork ? uploader("staff", "file", "+ Upload files") : ""}`
    : t.kind !== "listing" ? `${f("other_links", "Links (one per line)", "", 3)}
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
    if (manage && t.status === "submitted") acts.push(`<button class="btn btn-primary" data-move="approved"${t.kind === "advertorial" && r.page_id && !r.page_published ? ' data-confirm="Approve and publish the advertorial page in the store?">Approve &amp; publish page' : ">Approve"}</button><button class="btn" data-changes>Send back for changes</button>`);
    if (manage && t.status === "approved" && t.kind === "listing") acts.push(`${t.launch_ready ? "<button class=\"btn btn-primary\" data-launch>Launch ads on Meta…</button>"
      : "<button class=\"btn\" disabled title=\"The launch strategies are being set up\">Launch ads on Meta · coming soon</button>"}<button class="btn" data-move="launched" data-confirm="Mark as launched without creating the ads here?">Mark launched (set up by hand)</button>`);
    if (manage && t.status === "approved" && t.kind !== "listing") acts.push(`<button class="btn btn-primary" data-move="done">${t.kind === "store" ? "Store is live: mark done" : "Mark done"}</button>`);
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
      <span>${t.kind === "custom" ? "Custom task" : KIND_NAME[t.kind] || t.kind}</span>
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
    ${t.kind === "store" && (t.store_pending || []).length ? `<div class="warn tk-pending"><b>Still to do by the admin</b> (reminded daily):
      ${t.store_pending.map((x) => `<br>• ${esc(x)}`).join("")}${manage ? `<br><button class="btn btn-sm" data-edit>Tick them in the brief</button>` : ""}</div>` : ""}
    <section class="tk-sec"><h3>The brief <em class="tk-opt">from ${esc((t.created_by || "").split("@")[0])}</em></h3>
      ${briefRows.length ? `<dl class="tk-dl">${briefRows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>` : '<p class="tk-empty">No details given</p>'}
      ${adminFiles.length ? `<div class="tk-files">${adminFiles.map((x) => fileTile(x, manage && !closed)).join("")}</div>` : ""}
      ${manage && !closed ? uploader("admin", "file", "+ Add reference files to the brief") : ""}
    </section>
    ${t.kind === "advertorial" ? `<section class="tk-sec tk-prompts"><h3>Prompts <em class="tk-opt">for ChatGPT / Claude, or by hand</em></h3>
      <div id="tkPrompts"><p class="tk-empty">Loading…</p></div></section>` : ""}
    <section class="tk-sec tk-work"><h3>${t.kind === "listing" || t.kind === "advertorial" ? "What was made" : t.kind === "store" ? "Building the store" : "The work"}</h3>
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
  $("dBody").querySelectorAll(".tk-rec audio, .tk-rec video").forEach(fixLength);
  if (t.kind === "advertorial") loadPrompts(t.id);
}

const PROMPT_NAMES = {
  do_the_task_with_connector: ["Do the whole task", "Paste into ChatGPT or Claude with the EcomVital connector switched on."],
  write_without_connector: ["Write the copy only", "For a chat without the connector: paste, then copy the results into this task by hand."],
  ad_image: ["Ad image", "Attach the product photo, then paste this into ChatGPT's image maker."],
  ad_image_alternative: ["Ad image, second scene", "Another scene to test."],
  page_images: ["Page pictures", "One prompt per picture, product photo attached each time."],
  after_making_images: ["After making the images", ""],
};

async function loadPrompts(id) {
  const box = $("tkPrompts");
  let d;
  try { d = await api(`/api/tasks/${id}/prompts`); } catch (e) { if (box) box.innerHTML = `<p class="tk-empty">${esc(e.message)}</p>`; return; }
  if (!$("tkPrompts") || !state.task || state.task.id !== id) return;
  const one = (text, i) => `<div class="tk-prompt"><pre>${esc(text)}</pre><button class="btn btn-sm" data-copy="${i}">Copy</button></div>`;
  const all = [];
  let html = d.facts_error ? `<p class="warn">${esc(d.facts_error)}</p>` : "";
  for (const [k, [name, hint]] of Object.entries(PROMPT_NAMES)) {
    const v = (d.prompts || {})[k];
    if (!v) continue;
    const list = Array.isArray(v) ? v : [v];
    html += `<div class="tk-pgroup"><b>${esc(name)}</b>${hint ? ` <span class="hint">${esc(hint)}</span>` : ""}
      ${list.map((x) => { all.push(x); return one(x, all.length - 1); }).join("")}</div>`;
  }
  html += `<details class="tk-steps"><summary>The steps the AI follows</summary><ol>${(d.steps || []).map((x) => `<li>${esc(x.replace(/^\d+\.\s*/, ""))}</li>`).join("")}</ol></details>`;
  $("tkPrompts").innerHTML = html;
  $("tkPrompts").onclick = async (e) => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    try { await navigator.clipboard.writeText(all[+b.dataset.copy]); b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 1500); }
    catch { toast("Couldn't copy: select the text and copy it.", true); }
  };
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
let recUI = null;          // the floating controls: a picture-in-picture window (stays on top of every tab), else the page bar
const clockOf = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

// Chrome / Edge: a small always-on-top window, so Stop stays in reach while you record other tabs or apps.
// Opened first, while the click still counts as a click (the browser's screen picker takes longer than that).
async function openRecWindow() {
  if (!window.documentPictureInPicture) return null;
  try {
    const w = await documentPictureInPicture.requestWindow({ width: 300, height: 92 });
    w.document.title = "EcomVital recording";
    w.document.body.innerHTML = `<style>
      body{margin:0;font:600 14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#14181F;color:#fff;
        display:flex;align-items:center;gap:10px;padding:0 14px;height:100vh;box-sizing:border-box}
      i{width:10px;height:10px;border-radius:50%;background:#EF4444;animation:b 1s infinite;flex:none}
      i.p{animation:none;background:#F59E0B}
      @keyframes b{50%{opacity:.3}}
      span{flex:1}
      button{font:inherit;font-size:13px;border:0;border-radius:8px;padding:7px 12px;cursor:pointer}
      #s{background:#fff;color:#14181F} #p{background:#334155;color:#fff}</style>
      <i id="d"></i><span id="t">Starting…</span><button id="p">Pause</button><button id="s">Stop</button>`;
    return w;
  } catch { return null; }
}

async function record(kind) {
  if (recorder) { toast("A recording is already running: press Stop first.", true); return null; }
  if (!navigator.mediaDevices || !window.MediaRecorder) { toast("This browser can't record. Use Chrome, or upload a recording.", true); return null; }
  const pip = await openRecWindow();
  let stream, extra = [];
  try {
    if (kind === "voice") stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    else {
      const screen = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 30 }, audio: true });
      let mic = null;
      try { mic = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { /* screen only */ }
      extra = [screen, mic].filter(Boolean);
      stream = new MediaStream([...screen.getVideoTracks(), ...(mic ? mic.getAudioTracks() : screen.getAudioTracks())]);
    }
  } catch {
    if (pip) pip.close();
    toast(`Recording didn't start: allow the ${kind === "voice" ? "microphone" : "screen and microphone"} when the browser asks.`, true);
    return null;
  }
  const types = kind === "voice" ? ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"] : ["video/webm;codecs=vp9,opus", "video/webm", "video/mp4"];
  const mime = types.find((x) => MediaRecorder.isTypeSupported(x)) || "";
  const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  const title = document.title;
  let took = 0, since = Date.now();                     // recorded time, without the paused stretches
  const ui = pip ? { t: pip.document.getElementById("t"), p: pip.document.getElementById("p"), s: pip.document.getElementById("s"), d: pip.document.getElementById("d") }
    : { t: $("recTime"), p: $("recPause"), s: $("recStop"), d: $("recBar").querySelector(".tk-dot") };
  const what = kind === "voice" ? "Voice" : "Screen";
  const tick = setInterval(() => {
    const ms = took + (mr.state === "recording" ? Date.now() - since : 0);
    ui.t.textContent = `${mr.state === "paused" ? "Paused" : what} ${clockOf(ms)}`;
    document.title = `${mr.state === "paused" ? "❚❚" : "●"} ${clockOf(ms)} · ${title}`;   // shows on the tab too
  }, 250);
  const stop = () => { if (mr.state !== "inactive") mr.stop(); };
  ui.s.onclick = stop;
  ui.p.onclick = () => {
    if (mr.state === "recording") { mr.pause(); took += Date.now() - since; ui.p.textContent = "Resume"; ui.d.classList.add("p"); }
    else if (mr.state === "paused") { mr.resume(); since = Date.now(); ui.p.textContent = "Pause"; ui.d.classList.remove("p"); }
  };
  recorder = mr;
  recUI = { pip, stop };
  if (pip) pip.addEventListener("pagehide", stop);      // closing the little window = Stop
  else $("recBar").hidden = false;
  return new Promise((resolve) => {
    mr.onstop = () => {
      clearInterval(tick);
      for (const s of [stream, ...extra]) for (const tr of s.getTracks()) tr.stop();
      recorder = null;
      if (pip) { pip.removeEventListener("pagehide", stop); pip.close(); }
      recUI = null;
      $("recBar").hidden = true;
      document.title = title;
      window.focus();
      const type = (mr.mimeType || mime || (kind === "voice" ? "audio/webm" : "video/webm")).split(";")[0];
      const ext = type.includes("mp4") ? (kind === "voice" ? "m4a" : "mp4") : "webm";
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
      resolve(chunks.length ? new File(chunks, `${kind === "voice" ? "voice-note" : "screen-recording"}-${stamp}.${ext}`, { type }) : null);
    };
    for (const tr of stream.getVideoTracks()) tr.onended = stop;   // the browser's own "Stop sharing"
    mr.start(1000);
    since = Date.now();
  });
}

// Recordings made in the browser don't say how long they are, so players can't seek; this makes them work out the length.
function fixLength(el) {
  el.addEventListener("loadedmetadata", () => {
    if (el.duration !== Infinity) return;
    el.currentTime = 1e7;
    el.addEventListener("timeupdate", function back() { el.removeEventListener("timeupdate", back); el.currentTime = 0; });
  }, { once: true });
}

// Watch / listen before it's added: keep it, record again, or throw it away.
function previewRecording(file, kind) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const audio = file.type.startsWith("audio");
    const box = $("recPrev");
    box.querySelector(".tk-prev-media").innerHTML = audio ? `<audio src="${url}" controls autoplay></audio>` : `<video src="${url}" controls autoplay></video>`;
    fixLength(box.querySelector("audio, video"));
    box.querySelector(".tk-prev-info").textContent = `${audio ? "Voice note" : "Screen recording"} · ${(file.size / 1048576).toFixed(1)} MB. Check it plays back right before adding it.`;
    box.hidden = false;
    const done = (answer) => {
      const m = box.querySelector("audio, video");
      if (m) m.pause();
      box.hidden = true;
      box.querySelector(".tk-prev-media").innerHTML = "";
      URL.revokeObjectURL(url);
      resolve(answer);
    };
    $("prevUse").onclick = () => done("use");
    $("prevAgain").onclick = () => done("again");
    $("prevDrop").onclick = () => { if (confirm("Throw this recording away?")) done("drop"); };
    box.querySelector(".drawer-scrim").onclick = null;
  }).then(async (answer) => {
    if (answer === "use") return file;
    if (answer === "again") { const f = await record(kind); return f ? previewRecording(f, kind) : null; }
    return null;
  });
}

$("recStop").onclick = () => { if (recUI) recUI.stop(); };

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
  for (const u of state.recUrls || []) URL.revokeObjectURL(u);
  state.recUrls = state.recs.map((f) => URL.createObjectURL(f));
  $("eRecs").innerHTML = state.recs.map((f, i) => `<div class="tk-recq">
    <div class="tk-recq-head">${f.type.startsWith("audio") ? "🎙" : "🖥"} ${esc(f.name)}
      <span class="tk-opt">${(f.size / 1048576).toFixed(1)} MB · sent when you save</span><button type="button" data-unrec="${i}">Remove</button></div>
    ${f.type.startsWith("audio") ? `<audio src="${state.recUrls[i]}" controls preload="metadata"></audio>` : `<video src="${state.recUrls[i]}" controls preload="metadata"></video>`}</div>`).join("");
  $("eRecs").querySelectorAll("audio, video").forEach(fixLength);
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
  for (const el of $("editor").querySelectorAll("[data-store-only]")) el.hidden = kind !== "store";
  for (const el of $("editor").querySelectorAll("[data-adv-only]")) el.hidden = kind !== "advertorial";
  $("eName").placeholder = kind === "store" ? "e.g. New store: Bella Rosa UK (left empty = from the domain)"
    : kind === "advertorial" ? "e.g. Alina bag advertorial (left empty = from the link)" : "e.g. Autumn knitwear: collection + ads";
}

function renderStoreChecks(b) {
  $("sChecks").innerHTML = Object.entries(storeChecks()).map(([k, label]) => {
    const v = String(b[k] || "no").toLowerCase() === "no" ? "no" : "yes";
    return `<label class="tk-yn"><span>${esc(label)}</span><select class="control" data-check="${k}">
      <option value="yes"${v === "yes" ? " selected" : ""}>Yes</option><option value="no"${v === "no" ? " selected" : ""}>No (remind me)</option></select></label>`;
  }).join("");
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
  $("sBrand").value = b.brand_domain || ""; $("sShopify").value = b.shopify_domain || "";
  $("sCountry").value = b.country || ""; $("sLang").value = b.language || ""; $("sCur").value = b.currency || "";
  $("sAds").value = b.ads_platform || "meta"; $("sComp").value = b.competitor_url || "";
  $("sReviews").value = b.reviews || "";
  $("aLink").value = b.product_url || ""; $("aComp").value = b.competitor_advertorial || "";
  $("aDesc").value = b.product_description || ""; $("aSend").value = b.send_to_url || ""; $("aPhoto").value = "";
  $("aRating").value = b.review_rating || ""; $("aCount").value = b.review_count || ""; $("aReviews").value = b.reviews || "";
  const admin = !!(state.data && state.data.manage);
  for (const btn of $("eKind").querySelectorAll("button")) btn.hidden = !admin && btn.dataset.kind !== "advertorial";
  $("sEmail").value = b.support_email && !["yes", "no"].includes(b.support_email) ? b.support_email : "";
  renderStoreChecks(b);
  state.recs = [];
  renderRecs();
  setKind(t ? t.kind : (admin ? "listing" : "advertorial"));
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
  const checks = {};
  for (const el of $("sChecks").querySelectorAll("[data-check]")) checks[el.dataset.check] = el.value;
  if (checks.support_email === "yes" && $("sEmail").value.trim()) checks.support_email = $("sEmail").value.trim();
  const brief = listing ? { ...state.editBrief, competitor_ads: $("eCompetitor").value, competitor_landing: $("eCompLanding").value,
    competitor_products: $("eCompProducts").value, todo: readTodo() }
    : state.kind === "store" ? { ...state.editBrief, brand_domain: $("sBrand").value, shopify_domain: $("sShopify").value,
      country: $("sCountry").value, language: $("sLang").value, currency: $("sCur").value, ads_platform: $("sAds").value,
      competitor_url: $("sComp").value, reviews: $("sReviews").value, ...checks,
      todo: (state.editBrief.todo && state.editBrief.todo.length) ? state.editBrief.todo : Object.keys(storeTodo()) }
    : state.kind === "advertorial" ? { ...state.editBrief, product_url: $("aLink").value.trim(), competitor_advertorial: $("aComp").value.trim(),
      product_description: $("aDesc").value.trim(), send_to_url: $("aSend").value.trim(),
      review_rating: $("aRating").value.trim(), review_count: $("aCount").value.trim(), reviews: $("aReviews").value }
    : { ...state.editBrief, description: $("eDesc").value };
  if (state.kind === "advertorial" && !$("eName").value.trim()) {
    const m = (brief.product_url || brief.send_to_url).match(/\/(?:products|collections)\/([^/?#]+)/);
    if (m) $("eName").value = "Advertorial: " + decodeURIComponent(m[1]).replace(/-/g, " ");
  }
  if (state.kind === "store" && !brief.brand_domain && !brief.shopify_domain) { toast("Fill in the brand domain or the Shopify domain.", true); return; }
  const body = { kind: state.kind, title: $("eName").value, store_id: $("eStore").value, assignee_id: $("eWho").value,
    due: $("eDue").value, priority: $("ePrio").value, special_notes: $("eNotes").value, brief };
  if (listing && !brief.todo.length && !confirm("Nothing is ticked under To do. Create it anyway?")) return;
  const btn = $("eSave");
  btn.disabled = true;
  try {
    const r = state.editing ? await api(`/api/tasks/${state.editing}`, { method: "PUT", json: body }) : await api("/api/tasks", { json: body });
    const id = state.editing || r.id;
    const photos = state.kind === "advertorial" ? [...$("aPhoto").files] : [];
    if (photos.length) {
      btn.textContent = "Uploading the product photo…";
      try { await sendFiles(id, "admin", "product", photos); } catch (e) { toast(`Task saved, but the photo didn't upload: ${e.message}`, true); }
    }
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

/* ------------------------------------------------ queue (admins): each person's open tasks, in order */

const QUEUE_STATES = ["todo", "doing", "changes"];       // still with the staff member
function renderQueue() {
  const d = state.data, today = d.today;
  const open = d.tasks.filter((t) => QUEUE_STATES.includes(t.status));
  const waiting = d.tasks.filter((t) => !QUEUE_STATES.includes(t.status));
  const people = d.people.filter((p) => !p.owner || open.some((t) => t.assignee_id === p.id));
  const cols = [...people.map((p) => ({ id: p.id, name: p.name, p })), { id: 0, name: "Not given to anyone" }];
  const most = Math.max(1, ...cols.map((c) => open.filter((t) => (t.assignee_id || 0) === c.id).length));
  $("queue").innerHTML = cols.map((c) => {
    const mine = open.filter((t) => (t.assignee_id || 0) === c.id);
    const late = mine.filter((t) => t.due && t.due < today).length;
    const review = waiting.filter((t) => t.assignee_id === c.id && c.id).length;
    const doing = mine.filter((t) => t.status === "doing").length;
    if (!c.id && !mine.length) return "";
    return `<div class="tk-qcol" data-person="${c.id}">
      <div class="tk-qhead"><strong>${esc(c.name)}</strong>
        <span class="tk-qload"><i style="width:${Math.round(mine.length / most * 100)}%"></i></span>
        <span class="tk-qnums"><b>${mine.length}</b> to do${doing ? ` · ${doing} in progress` : ""}${late ? ` · <em>${late} late</em>` : ""}${review ? ` · ${review} with admins` : ""}</span></div>
      <ol class="tk-qlist">${mine.map((t, i) => `<li class="tk-qitem${t.priority === "high" ? " high" : ""}" draggable="true" data-task="${t.id}">
          <span class="tk-qpos">${i + 1}</span>
          <span class="tk-qmain"><a href="#" data-open="${t.id}">${esc(t.title)}</a>
            <span class="tk-meta">${esc(t.store || "")}${t.store ? " · " : ""}${esc(t.status_name)}</span>${dueText(t.due, today)}</span>
          <span class="tk-qbtns">
            <button class="btn btn-sm btn-ghost" data-qup="${t.id}" title="Earlier"${i ? "" : " disabled"}>▲</button>
            <button class="btn btn-sm btn-ghost" data-qdown="${t.id}" title="Later"${i < mine.length - 1 ? "" : " disabled"}>▼</button>
            <button class="btn btn-sm ${t.priority === "high" ? "tk-qhigh" : "btn-ghost"}" data-qpri="${t.id}" title="High priority on / off">High</button>
            <select class="control tk-qgive" data-qgive="${t.id}" title="Give to someone else"><option value="">Give to…</option>
              ${people.filter((p) => p.id !== c.id).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select>
          </span></li>`).join("") || '<li class="tk-empty">Nothing in the queue</li>'}</ol></div>`;
  }).join("");
}

function queueOrder(person) {
  const open = state.data.tasks.filter((t) => QUEUE_STATES.includes(t.status) && (t.assignee_id || 0) === person);
  return open.map((t) => t.id);
}
async function saveQueue(person, order, priority) {
  try {
    const r = await api("/api/tasks/queue", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person: person || null, order, priority }) });
    if (r.given) toast(`Given to the new person${state.data.wa_ready ? " (sent on WhatsApp)" : ""}.`);
  } catch (e) { toast(e.message, true); }
  await loadQueue();
}
async function loadQueue() {
  try { state.data = await api("/api/tasks?view=board"); } catch (e) { toast(e.message, true); return; }
  fillPickers();
  renderQueue();
}
async function moveTo(id, person, before) {
  const t = state.data.tasks.find((x) => x.id === id);
  const from = t.assignee_id || 0;
  const order = queueOrder(person).filter((x) => x !== id);
  const at = before ? order.indexOf(before) : -1;
  if (at >= 0) order.splice(at, 0, id); else order.push(id);
  if (from !== person) {
    const who = person ? state.data.people.find((p) => p.id === person)?.name : "nobody";
    if (!confirm(`Give “${t.title}” to ${who}?`)) { renderQueue(); return; }
    await saveQueue(from, queueOrder(from).filter((x) => x !== id));
  }
  await saveQueue(person, order);
}
$("queue").addEventListener("click", (e) => {
  const up = e.target.closest("[data-qup]"), down = e.target.closest("[data-qdown]"), pri = e.target.closest("[data-qpri]");
  const b = up || down || pri;
  if (!b) return;
  const id = +(b.dataset.qup || b.dataset.qdown || b.dataset.qpri);
  const t = state.data.tasks.find((x) => x.id === id);
  const person = t.assignee_id || 0;
  const order = queueOrder(person);
  if (pri) { saveQueue(person, order, { [id]: t.priority === "high" ? "normal" : "high" }); return; }
  const i = order.indexOf(id), j = up ? i - 1 : i + 1;
  if (j < 0 || j >= order.length) return;
  [order[i], order[j]] = [order[j], order[i]];
  saveQueue(person, order);
});
$("queue").addEventListener("change", (e) => {
  const g = e.target.closest("[data-qgive]");
  if (g && g.value) moveTo(+g.dataset.qgive, +g.value);
});
let dragId = null;
$("queue").addEventListener("dragstart", (e) => { const li = e.target.closest("[data-task]"); if (li) { dragId = +li.dataset.task; li.classList.add("dragging"); } });
$("queue").addEventListener("dragend", (e) => { const li = e.target.closest("[data-task]"); if (li) li.classList.remove("dragging"); });
$("queue").addEventListener("dragover", (e) => { if (dragId && e.target.closest(".tk-qcol")) e.preventDefault(); });
$("queue").addEventListener("drop", (e) => {
  const col = e.target.closest(".tk-qcol");
  if (!col || !dragId) return;
  e.preventDefault();
  const before = e.target.closest("[data-task]");
  const id = dragId;
  dragId = null;
  if (before && +before.dataset.task === id) return;
  moveTo(id, +col.dataset.person, before ? +before.dataset.task : null);
});

function showPage(p) {
  state.page = p;
  PDUrl.set({ page: p === "board" ? null : p });
  for (const b of $("pages").querySelectorAll("button")) b.classList.toggle("on", b.dataset.page === p);
  $("queuePage").hidden = p !== "queue";
  $("boardPage").hidden = p !== "board";
  $("historyPage").hidden = p !== "history";
  $("dayPage").hidden = p !== "day";
  $("bar").hidden = p === "day" || p === "queue";
  selBar();
  if (p === "day") loadDay(); else if (p === "queue") loadQueue(); else load();
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
  if (el.dataset.rec) { const k = el.dataset.rec; record(k).then((f) => (f ? previewRecording(f, k) : null)).then((f) => addRecordings([f])); return; }
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


/* ------------------------------------------------ store kit (playbook + theme) */

async function openKit() {
  $("kit").hidden = false;
  $("kitPlaybook").value = "Loading…";
  try {
    const d = await api("/api/tasks/kit/playbook");
    $("kitPlaybook").value = d.playbook || "";
    $("kitAt").textContent = d.updated ? `Saved ${when(d.updated)}` : "Not saved yet";
    const th = d.kit && d.kit.theme;
    $("kitTheme").textContent = th ? `${th.name} · ${(th.size / 1048576).toFixed(1)} MB · uploaded ${when(th.at)}` : "No theme uploaded yet.";
  } catch (e) { toast(e.message, true); }
}
async function openAdvKit() {
  try {
    const d = await api("/api/tasks/kit/advertorial");
    $("kitAdv").value = d.playbook || "";
    $("kitAdvAt").textContent = d.custom ? `Your version · saved ${when(d.updated)}` : "EcomVital's first version (from your 8 examples)";
  } catch (e) { toast(e.message, true); }
}
$("storeKit").onclick = () => { openKit(); openAdvKit(); };
$("kitAdvSave").onclick = async () => {
  try { await api("/api/tasks/kit/advertorial", { method: "PUT", json: { playbook: $("kitAdv").value } }); toast("Advertorial playbook saved"); openAdvKit(); }
  catch (e) { toast(e.message, true); }
};
$("kitAdvReset").onclick = async () => {
  if (!confirm("Go back to EcomVital's version of the advertorial playbook? Your edits are dropped.")) return;
  try { await api("/api/tasks/kit/advertorial", { method: "PUT", json: { playbook: "" } }); toast("Back to EcomVital's version"); openAdvKit(); }
  catch (e) { toast(e.message, true); }
};
for (const el of $("kit").querySelectorAll("[data-close]")) el.onclick = () => { $("kit").hidden = true; };
$("kitSave").onclick = async () => {
  try { await api("/api/tasks/kit/playbook", { method: "PUT", json: { playbook: $("kitPlaybook").value } }); toast("Playbook saved"); openKit(); }
  catch (e) { toast(e.message, true); }
};
$("kitFile").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  $("kitTheme").textContent = "Uploading…";
  try { await api("/api/tasks/kit/theme", { method: "POST", body: fd }); toast("Theme saved in the store kit"); openKit(); }
  catch (err) { toast(err.message, true); openKit(); }
  e.target.value = "";
};
