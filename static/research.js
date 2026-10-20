/* EcomVital Apps → Research: competitor research as a spreadsheet. Click a cell to type, Enter or Tab saves
   (Tab moves right), Esc cancels. Columns come from the server (built-in + ones the team added). */

const $ = (id) => document.getElementById(id);
let cols = [], items = [], apifyOn = false, appName = "EcomVital", archived = PDUrl.get("archived") === "1", archivedCount = 0;
let sort = { key: PDUrl.get("sort") || "", dir: PDUrl.get("dir") === "asc" ? 1 : -1 };
let query = PDUrl.get("q") || "", nicheF = PDUrl.get("niche") || "", byF = PDUrl.get("by") || "", qualF = PDUrl.get("qual") || "";
let tab = PDUrl.get("tab") || "sheet", labelF = PDUrl.get("label") ?? "nugget,compete,proven", pquery = "";
let hidden = new Set();
try { hidden = new Set(JSON.parse(localStorage.getItem("pdResearchHide") || "[]")); } catch { /* private mode */ }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/research"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || body.detail || `Request failed (${r.status})`);
  return body;
}
const send = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 7000 : 2500);
}
const canEdit = () => window.pdLevel !== "view";
function when(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  return isNaN(t) ? iso : t.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
}

const value = (it, c) => (c.builtin ? it[c.key] : (it.extra || {})[c.key]);
const STAMP = { traffic: ["traffic_by", "traffic_at"], ads_count: ["ads_by", "ads_at"], ads_new_today: ["ads_by", "ads_at"] };
function compact(n) {
  if (n === null || n === undefined || n === "") return "";
  const v = Number(n);
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "m";
  if (Math.abs(v) >= 1e4) return (v / 1e3).toFixed(v >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "k";
  return v.toLocaleString();
}
function shortLink(url) {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname.replace(/\/$/, "");
    return host + (path && path.length < 30 ? path : path ? path.slice(0, 28) + "…" : "");
  } catch { return url; }
}
const pagesOf = (it) => { try { return JSON.parse(it.fb_pages || "[]"); } catch { return []; } };
function autoDisplay(it, c) {
  const v = it[c.key];
  if (c.key === "is_shopify") return v === null || v === undefined ? "" : v ? `<span class="rs-yes">✓ Shopify</span>` : `<span class="rs-no">No</span>`;
  if (c.key === "store_since") { if (!v) return ""; const d = Math.round((Date.now() - new Date(v + "T00:00")) / 864e5); return `${esc(v)}<small>${d} days</small>`; }
  if (c.key === "ds_score") return v === null || v === undefined ? "" : `${v}${it.qualified ? ` <span class="rs-yes" title="Looks like a dropshipper">✓</span>` : ""}`;
  if (c.key === "fb_pages") { const n = pagesOf(it).length; return n ? String(n) : ""; }
  if (c.key === "nuggets") return v ? `<span class="rs-lab nugget">${v}</span>` : "";
  return esc(v ?? "");
}
function display(it, c) {
  if (c.kind === "auto") return autoDisplay(it, c);
  const v = value(it, c);
  if (c.kind === "who") {
    return `<span class="rs-who${it.by_kind === "app" ? " app" : ""}">${esc(it.by_kind === "app" ? appName : v)}</span>`;
  }
  if (v === null || v === undefined || v === "") return "";
  if (c.kind === "link") return `<a href="${esc(v)}" target="_blank" rel="noopener" data-open>${esc(shortLink(v))}</a>${c.key === "site_url" && it.duplicate ? `<span class="rs-dup" title="Another row has the same website">twice</span>` : ""}`;
  if (c.kind === "number") return esc(c.key === "traffic" ? compact(v) : Number(v).toLocaleString() + (c.key === "ads_count" && it.ads_capped ? "+" : ""));
  if (c.kind === "date") { const d = new Date(v + "T00:00"); return esc(isNaN(d) ? v : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })); }
  return esc(v);
}
function title(it, c) {
  if (c.key === "ds_score" && it.ds_reasons) return `Why: ${it.ds_reasons}`;
  if (c.key === "fb_pages" && it.fb_pages) return pagesOf(it).map((p) => `${p.name || p.id} (${p.ads} ads)`).join(", ");
  if (c.key === "store_since" && it.store_since) return `Oldest product created ${it.store_since}${it.products_count ? `, ${it.products_count} products` : ""}`;
  const st = STAMP[c.key];
  if (c.key === "ads_count" && it.ads_capped) return `At least this many: the check stopped at its limit (Apify settings). ${it.ads_by ? `By ${it.ads_by}, ${when(it.ads_at)}` : ""}`;
  if (c.key === "ads_new_today" && it.ads_by === appName) return `Ads that started running today (Meta's US date). By ${appName}, ${when(it.ads_at)}`;
  if (st && it[st[0]]) return `Added by ${it[st[0]]}, ${when(it[st[1]])}`;
  const v = value(it, c);
  return v === null || v === undefined ? "" : String(v);
}

async function load() {
  try {
    const d = await api(`/api/research?archived=${archived ? 1 : 0}`);
    cols = d.columns; items = d.items; appName = d.app_name; archivedCount = d.archived_count; apifyOn = d.apify;
  } catch (e) { toast(e.message, true); return; }
  fillFilters();
  draw();
  clearTimeout(window.__poll);
  if (items.some((i) => i.check?.status === "running")) window.__poll = setTimeout(() => { if (!editing) load(); }, 8000);
}

function fillFilters() {
  const niches = [...new Set(items.map((i) => i.niche).filter(Boolean))].sort();
  $("niche").innerHTML = `<option value="">All niches</option>` + niches.map((n) => `<option>${esc(n)}</option>`).join("");
  $("niche").value = niches.includes(nicheF) ? nicheF : "";
  const who = [...new Set(items.map((i) => (i.by_kind === "app" ? appName : i.by_name)))].sort();
  $("by").innerHTML = `<option value="">Anyone</option>` + who.map((n) => `<option>${esc(n)}</option>`).join("");
  $("by").value = who.includes(byF) ? byF : "";
  $("archBtn").textContent = archived ? "Back to the sheet" : `Archived${archivedCount ? ` (${archivedCount})` : ""}`;
}

function shown() {
  const q = query.trim().toLowerCase();
  let rows = items.filter((it) => (!nicheF || it.niche === nicheF)
    && (!byF || (it.by_kind === "app" ? appName : it.by_name) === byF)
    && (qualF === "" || String(it.qualified ? 1 : 0) === qualF)
    && (!q || [it.name, it.site_url, it.fb_page, it.niche, it.notes, it.by_name, ...Object.values(it.extra || {})]
      .some((v) => String(v ?? "").toLowerCase().includes(q))));
  if (sort.key) {
    const c = cols.find((x) => x.key === sort.key);
    if (c) rows = rows.slice().sort((a, b) => {
      const sv = (r) => (c.kind === "who" ? (r.by_kind === "app" ? appName : r.by_name)
        : c.key === "fb_pages" ? (pagesOf(r).length || null) : value(r, c));
      let x = sv(a), y = sv(b);
      const ex = x === null || x === undefined || x === "", ey = y === null || y === undefined || y === "";
      if (ex || ey) return ex === ey ? 0 : ex ? 1 : -1;          // empty cells always last
      if (typeof x === "number" && typeof y === "number") return (x - y) * sort.dir;
      return String(x).localeCompare(String(y)) * sort.dir;
    });
  }
  return rows;
}

const visible = () => cols.filter((c) => !hidden.has(c.key));

function draw() {
  const vc = visible(), rows = shown();
  $("count").textContent = `${rows.length} of ${items.length} competitor${items.length === 1 ? "" : "s"}`;
  const head = `<thead><tr><th class="rn">#</th>${vc.map((c) => `<th><button data-sort="${esc(c.key)}" title="Sort">${esc(c.label)}${sort.key === c.key ? `<span class="arrow">${sort.dir > 0 ? "▲" : "▼"}</span>` : ""}</button></th>`).join("")}<th></th></tr></thead>`;
  const body = rows.map((it, i) => `<tr data-id="${it.id}"><td class="rn">${i + 1}</td>${vc.map((c) =>
    `<td class="c w-${c.key === "notes" ? "notes" : c.key === "name" ? "name" : c.kind}" data-key="${esc(c.key)}" title="${esc(title(it, c))}">
      <span class="rs-cell${c.kind === "number" || ["ds_score", "fb_pages", "nuggets"].includes(c.key) ? " num" : ""}${display(it, c) ? "" : " empty"}">${display(it, c)}</span></td>`).join("")}
    <td class="rs-acts">${runNote(it)}${!archived && canEdit() && it.domain ? `<button data-analyse="${it.id}" title="Free: Shopify check, store age, dropshipper score, bestsellers and winning products">${it.analysed_at ? "Re-analyse" : "Analyse"}</button>` : ""}${it.nuggets || it.analysed_at ? `<button data-prods="${it.id}">Products</button>` : ""}${it.ads_by === appName ? `<button data-ads="${it.id}" title="The ads EcomVital found">Ads</button>` : ""}${checkBtn(it)}<button data-log="${it.id}" title="Who changed what">History</button>${canEdit() ? `<button data-arch="${it.id}">${archived ? "Restore" : "Archive"}</button>` : ""}</td></tr>`).join("");
  const add = !archived && canEdit() ? `<tr class="rs-new"><td class="rn">+</td>${vc.map((c) =>
    ["name", "site_url", "niche", "fb_page"].includes(c.key)
      ? `<td><input data-new="${c.key}" placeholder="${c.key === "name" ? "Add competitor…" : c.key === "site_url" ? "website.com" : c.key === "niche" ? "Niche" : "facebook.com/…"}"></td>`
      : `<td></td>`).join("")}<td></td></tr>` : "";
  const empty = !rows.length && !add ? `<tr><td class="rn"></td><td colspan="${vc.length + 1}"><p class="hint" style="padding:8px 10px">Nothing here yet.</p></td></tr>` : "";
  $("sheet").innerHTML = `<table class="rs-grid">${head}<tbody>${body}${empty}${add}</tbody></table>`;
}

function runNote(it) {
  const c = it.check;
  if (!c) return "";
  if (c.status === "running") return `<span class="rs-run">Checking ads…</span>`;
  if (c.status === "error") return `<span class="rs-run bad" title="${esc(c.error || "")}">Last check failed</span>`;
  return "";
}
function checkBtn(it) {
  if (archived || window.pdLevel !== "manage" || !apifyOn) return "";
  if (it.check?.status === "running") return "";
  return it.fb_page ? `<button data-check="${it.id}" title="Ask Apify for this page's active ads now (uses Apify credit)">Check ads</button>`
    : `<button disabled title="Add the Facebook page first" style="opacity:.4;cursor:default">Check ads</button>`;
}

// ---- editing a cell
let editing = null;
function startEdit(td) {
  if (!canEdit() || archived) return;
  const key = td.dataset.key, id = +td.closest("tr").dataset.id;
  const c = cols.find((x) => x.key === key);
  if (!c || c.kind === "who" || c.kind === "auto") return;
  const it = items.find((x) => x.id === id);
  const v = value(it, c) ?? "";
  td.classList.add("editing");
  td.innerHTML = key === "notes" ? `<textarea>${esc(v)}</textarea>`
    : `<input type="${c.kind === "date" ? "date" : "text"}" value="${esc(v)}" ${c.kind === "number" ? 'inputmode="decimal" placeholder="e.g. 12000 or 12k"' : ""}>`;
  const inp = td.querySelector("input, textarea");
  inp.focus();
  if (inp.select && c.kind !== "date") inp.select();
  editing = { td, id, key, old: String(v), inp };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
    else if (e.key === "Enter" && !(key === "notes" && e.shiftKey)) { e.preventDefault(); commit(true, "down"); }
    else if (e.key === "Tab") { e.preventDefault(); commit(true, e.shiftKey ? "left" : "right"); }
  });
  inp.addEventListener("blur", () => setTimeout(() => { if (editing && editing.inp === inp) commit(false); }, 120));
}
function cancelEdit() { editing = null; draw(); }
let pending = Promise.resolve();
function commit(move, dir) {
  pending = save(move, dir);
  return pending;
}
async function save(move, dir) {
  const ed = editing;
  if (!ed) return;
  editing = null;
  const val = ed.inp.value;
  const next = move ? neighbour(ed.id, ed.key, dir) : null;
  if (val.trim() === ed.old.trim()) { draw(); if (next) openAt(next); return; }
  ed.td.classList.add("saving");
  try {
    const r = await send(`/api/research/items/${ed.id}`, { key: ed.key, value: val }, "PUT");
    const i = items.findIndex((x) => x.id === ed.id);
    if (i >= 0) items[i] = { ...items[i], ...r.item };
    if (ed.key === "site_url" || ed.key === "niche") { await load(); } else { draw(); }
  } catch (e) { toast(e.message, true); draw(); return; }
  if (next) openAt(next);
}
function neighbour(id, key, dir) {
  const vc = visible().filter((c) => c.kind !== "who" && c.kind !== "auto"), rows = shown();
  let ci = vc.findIndex((c) => c.key === key), ri = rows.findIndex((r) => r.id === id);
  if (dir === "right") ci++; else if (dir === "left") ci--; else if (dir === "down") ri++;
  if (ci >= vc.length) { ci = 0; ri++; }
  if (ci < 0) { ci = vc.length - 1; ri--; }
  if (ri < 0 || ri >= rows.length) return null;
  return { id: rows[ri].id, key: vc[ci].key };
}
function openAt(p) {
  const td = document.querySelector(`tr[data-id="${p.id}"] td[data-key="${CSS.escape(p.key)}"]`);
  if (td) startEdit(td);
}

// ---- adding a row
async function addRow() {
  const vals = {};
  for (const inp of document.querySelectorAll("[data-new]")) vals[inp.dataset.new] = inp.value.trim();
  if (!vals.name && !vals.site_url) { toast("Type the competitor's name or website first.", true); return; }
  try {
    const r = await send("/api/research/items", vals);
    await load();
    toast(`${r.item.name || r.item.domain || "Competitor"} added.`);
    const dup = items.find((x) => x.id === r.item.id)?.duplicate;
    if (dup) toast("Added, but another row already has this website.", true);
    document.querySelector('[data-new="name"]')?.focus();
  } catch (e) { toast(e.message, true); }
}

// ---- history drawer
async function showLog(id) {
  const it = items.find((x) => x.id === id);
  $("drawerTitle").textContent = `History: ${it?.name || it?.domain || "row " + id}`;
  $("drawerBody").innerHTML = `<p class="hint">Loading…</p>`;
  $("drawer").hidden = false;
  try {
    const { log } = await api(`/api/research/items/${id}/log`);
    $("drawerBody").innerHTML = log.map((l) => `<div class="rs-log">${l.field === "created" ? "Row added" : l.field === "archived" ? (l.new === "yes" ? "Archived" : "Restored")
      : `<b>${esc(l.label)}</b>: ${l.old ? `<s>${esc(l.old)}</s> → ` : ""}${esc(l.new || "(empty)")}`}<small>${esc(l.by_name || "")} · ${esc(when(l.at))}</small></div>`).join("") || `<p class="hint">No changes yet.</p>`;
  } catch (e) { $("drawerBody").innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
}

async function showAds(id) {
  const it = items.find((x) => x.id === id);
  $("drawerTitle").textContent = `Ads: ${it?.name || it?.domain || "row " + id}`;
  $("drawerBody").innerHTML = `<p class="hint">Loading…</p>`;
  $("drawer").hidden = false;
  try {
    const d = await api(`/api/research/items/${id}/ads`);
    const on = d.ads.filter((a) => a.active).length;
    const lr = d.last_run;
    $("drawerBody").innerHTML = `<p class="hint">${on} running now${lr ? ` · last check ${esc(when(lr.finished_at || lr.started_at))}${lr.cost_usd ? ` · cost $${Number(lr.cost_usd).toFixed(3)}` : ""}${lr.capped ? " · stopped at the limit, there may be more" : ""}` : ""}. New today = started ${esc(d.today)} (Meta's US date).</p>`
      + d.ads.map((a) => `<div class="rs-ad${a.active ? "" : " off"}"><small>${a.start_date ? "Started " + esc(a.start_date) : "Start date unknown"}${a.start_date === d.today ? " · <b>new today</b>" : ""}${a.copies > 1 ? ` · ${a.copies} copies` : ""}${a.active ? "" : " · stopped"} · <a href="https://www.facebook.com/ads/library/?id=${encodeURIComponent(a.ad_id)}" target="_blank" rel="noopener">See on Meta</a>${a.link ? ` · <a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(shortLink(a.link))}</a>` : ""}</small><div class="txt">${esc(a.text || "(no text)")}</div></div>`).join("")
      || `<p class="hint">No ads saved yet.</p>`;
  } catch (e) { $("drawerBody").innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
}

async function apifyPanel() {
  const pop = $("apifyPop");
  pop.innerHTML = `<p class="hint">Checking the Apify connection…</p>`;
  let d;
  try { d = await api("/api/research/apify?check=1"); } catch (e) { pop.innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
  const c = d.config, a = d.account || {}, m = d.this_month || {};
  const usage = a.used_usd !== undefined && a.used_usd !== null ? ` · used $${Number(a.used_usd).toFixed(2)}${a.limit_usd ? ` of $${Number(a.limit_usd).toFixed(2)}` : ""} this month at Apify` : "";
  pop.innerHTML = `<strong>Apify connection</strong>
    ${d.connected ? `<div class="row">Connected${a.username ? ` as <b>${esc(a.username)}</b>` : ""} (key ending …${esc(d.key_end)})${esc(usage)}${d.error ? ` <span class="rs-run bad">${esc(d.error)}</span>` : ""}
       <button type="button" class="btn btn-sm btn-danger" id="apRemove">Disconnect</button></div>`
      : `<div class="row hint" style="margin:0">Paste the API token from Apify → Settings → API &amp; Integrations. It stays in EcomVital; nobody sees it again.</div>`}
    <div class="row"><input type="password" id="apToken" placeholder="${d.connected ? "Paste a new key to replace it" : "apify_api_…"}" autocomplete="off" style="min-width:280px">
      <button type="button" class="btn btn-sm btn-primary" id="apSave">${d.connected ? "Replace key" : "Connect"}</button></div>
    <div class="row">
      <label class="field">Countries <input type="text" id="apCountry" value="${esc(c.country)}" maxlength="3" size="4" title="ALL, or a two-letter code like US"></label>
      <label class="field">Max ads per check <input type="number" id="apCap" value="${c.cap}" min="10" max="5000" step="10" style="width:90px"></label>
      <label class="field"><input type="checkbox" id="apDaily"${c.daily ? " checked" : ""}> Check every competitor daily at</label>
      <select id="apHour">${Array.from({ length: 24 }, (_, h) => `<option value="${h}"${h === c.hour ? " selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select> <span class="hint" style="margin:0">Hong Kong time</span>
      <button type="button" class="btn btn-sm" id="apSet">Save settings</button>
    </div>
    <p class="hint" style="margin:4px 0">Cost: about $0.75 per 1,000 ads found (Apify gives $5 free each month). A brand with 50 ads ≈ 4 cents a check.
      This month in EcomVital: ${m.runs || 0} checks, ${m.ads || 0} ads${m.cost_usd ? `, $${Number(m.cost_usd).toFixed(2)}` : ""}.</p>
    ${(d.recent || []).length ? `<table><tr><th>When</th><th>Competitor</th><th>Result</th><th>Cost</th></tr>${d.recent.map((r) => `<tr><td>${esc(when(r.started_at))}</td><td>${esc(r.name || "")}</td><td>${r.status === "done" ? `${r.ads} ads${r.capped ? "+" : ""}` : r.status === "running" ? "checking…" : `<span class="rs-run bad">${esc(r.error || "failed")}</span>`}</td><td>${r.cost_usd ? "$" + Number(r.cost_usd).toFixed(3) : ""}</td></tr>`).join("")}</table>` : ""}`;
  const save = async (body, msg) => {
    try { await send("/api/research/apify", body, "PUT"); toast(msg); apifyPanel(); load(); }
    catch (e) { toast(e.message, true); }
  };
  $("apSave").onclick = () => { const t = $("apToken").value.trim(); if (!t) { toast("Paste the key first.", true); return; } save({ token: t }, "Apify connected."); };
  $("apSet").onclick = () => save({ country: $("apCountry").value, cap: +$("apCap").value, daily: $("apDaily").checked, hour: +$("apHour").value }, "Saved.");
  const rm = $("apRemove");
  if (rm) rm.onclick = () => { if (confirm("Disconnect Apify? Checks stop until a key is pasted again.")) save({ remove: true }, "Disconnected."); };
}

// ---- wiring
$("sheet").addEventListener("click", async (e) => {
  if (e.target.closest("[data-open]")) return;                  // a link opens, it doesn't edit
  const s = e.target.closest("[data-sort]");
  if (s) {
    const k = s.dataset.sort;
    sort = sort.key === k ? (sort.dir === -1 ? { key: k, dir: 1 } : { key: "", dir: -1 }) : { key: k, dir: -1 };
    PDUrl.set({ sort: sort.key, dir: sort.key && sort.dir > 0 ? "asc" : "" });
    draw();
    return;
  }
  const ck = e.target.closest("[data-check]");
  if (ck) {
    try { await send(`/api/research/items/${ck.dataset.check}/check`); toast("Checking with Apify… usually 1–3 minutes."); load(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  const an = e.target.closest("[data-analyse]");
  if (an) {
    an.disabled = true; an.textContent = "Analysing…";
    try {
      const r = await send(`/api/research/items/${an.dataset.analyse}/analyse`);
      if (r.ok === false) toast(r.error, true);
      else toast(r.shopify ? `Shopify store: score ${r.score}${r.qualified ? " (dropshipper)" : ""}, ${r.bestsellers} bestsellers read.` : "Not a Shopify store.");
    } catch (err) { toast(err.message, true); }
    load();
    return;
  }
  const pr = e.target.closest("[data-prods]");
  if (pr) { productsFor = +pr.dataset.prods; labelF = ""; switchTab("products"); return; }
  const adsB = e.target.closest("[data-ads]");
  if (adsB) { showAds(+adsB.dataset.ads); return; }
  const lg = e.target.closest("[data-log]");
  if (lg) { showLog(+lg.dataset.log); return; }
  const ar = e.target.closest("[data-arch]");
  if (ar) {
    try { await send(`/api/research/items/${ar.dataset.arch}/archive`, { restore: archived }); toast(archived ? "Restored." : "Archived (find it under Archived)."); load(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  const td = e.target.closest("td[data-key]");
  if (!td || td.classList.contains("editing")) return;
  // save the open cell first (Enter, Tab or leaving it), then open this one where it is after redrawing
  const p = { id: +td.closest("tr").dataset.id, key: td.dataset.key };
  if (editing) await commit(false); else await pending;
  openAt(p);
});
$("sheet").addEventListener("keydown", (e) => {
  if (e.target.matches("[data-new]") && e.key === "Enter") { e.preventDefault(); addRow(); }
});
$("q").value = query;
let typing;
$("q").oninput = (e) => { clearTimeout(typing); typing = setTimeout(() => { query = e.target.value; PDUrl.set({ q: query }); draw(); }, 150); };
$("niche").onchange = (e) => { nicheF = e.target.value; PDUrl.set({ niche: nicheF }); draw(); };
$("by").onchange = (e) => { byF = e.target.value; PDUrl.set({ by: byF }); draw(); };
$("qual").value = qualF;
$("qual").onchange = (e) => { qualF = e.target.value; PDUrl.set({ qual: qualF }); draw(); };
$("archBtn").onclick = () => { archived = !archived; PDUrl.set({ archived: archived ? "1" : "" }); load(); };
$("apifyBtn").onclick = () => { const p = $("apifyPop"); p.hidden = !p.hidden; $("colsPop").hidden = true; $("addColPop").hidden = true; if (!p.hidden) apifyPanel(); };
$("checkAllBtn").onclick = async () => {
  if (!apifyOn) { toast("Connect Apify first (Apify button).", true); return; }
  const n = items.filter((i) => i.fb_page).length;
  if (!n) { toast("No competitor has a Facebook page yet.", true); return; }
  if (!confirm(`Check the active ads of ${n} competitor${n === 1 ? "" : "s"} now? This uses Apify credit (about $0.75 per 1,000 ads found).`)) return;
  try { await send("/api/research/check-all"); toast("Checking… the sheet updates as each one finishes."); setTimeout(load, 3000); }
  catch (e) { toast(e.message, true); }
};
$("drawerClose").onclick = () => { $("drawer").hidden = true; };

$("colsBtn").onclick = () => {
  const pop = $("colsPop");
  pop.hidden = !pop.hidden;
  $("addColPop").hidden = true;
  if (pop.hidden) return;
  const isManager = window.pdLevel === "manage";
  pop.innerHTML = `<strong>Show columns</strong>` + cols.map((c) => `<label class="rs-col"><input type="checkbox" data-col="${esc(c.key)}"${hidden.has(c.key) ? "" : " checked"}${c.key === "name" ? " disabled" : ""}> ${esc(c.label)}${!c.builtin && isManager ? ` <button type="button" class="btn btn-sm" data-rename="${esc(c.key)}">Rename</button> <button type="button" class="btn btn-sm btn-danger" data-remove="${esc(c.key)}">Remove</button>` : ""}</label>`).join("")
    + `<span class="hint" style="margin:0">Hiding is only on this computer.</span>`;
};
$("colsPop").addEventListener("change", (e) => {
  const k = e.target.dataset.col;
  if (!k) return;
  if (e.target.checked) hidden.delete(k); else hidden.add(k);
  try { localStorage.setItem("pdResearchHide", JSON.stringify([...hidden])); } catch { /* private mode */ }
  draw();
});
$("colsPop").addEventListener("click", async (e) => {
  const rn = e.target.closest("[data-rename]"), rm = e.target.closest("[data-remove]");
  if (!rn && !rm) return;
  e.preventDefault();
  const key = (rn || rm).dataset[rn ? "rename" : "remove"];
  const c = cols.find((x) => x.key === key);
  let body;
  if (rn) { const label = prompt("New name for this column:", c.label); if (!label) return; body = { label }; }
  else { if (!confirm(`Remove the column “${c.label}”? What's typed in it is kept, so adding a column with the same name brings it back.`)) return; body = { remove: true }; }
  try { await send(`/api/research/columns/${encodeURIComponent(key)}`, body, "PUT"); $("colsPop").hidden = true; load(); }
  catch (err) { toast(err.message, true); }
});
$("addColBtn").onclick = () => { $("addColPop").hidden = !$("addColPop").hidden; $("colsPop").hidden = true; if (!$("addColPop").hidden) $("colLabel").focus(); };
$("colCancel").onclick = () => { $("addColPop").hidden = true; };
$("addColPop").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await send("/api/research/columns", { label: $("colLabel").value, kind: $("colKind").value });
    toast(`Column “${$("colLabel").value}” added.`);
    $("colLabel").value = "";
    $("addColPop").hidden = true;
    load();
  } catch (err) { toast(err.message, true); }
};

// ---- Find competitors
let hunt = null;
const estimate = () => {
  if (!hunt) return;
  const n = document.querySelectorAll("#countries input:checked").length, cap = +$("cap").value || 0, deep = +$("deepMax").value || 0;
  const usd = (n * cap + deep * hunt.deep_cap) * hunt.price_per_ad;
  $("huntEst").textContent = `Up to ${n * cap} ads searched + up to ${deep} stores deep-checked (${hunt.deep_cap} ads each): at most about $${usd.toFixed(2)} of Apify credit. Shopify checks, bestsellers and labels are free.`;
};
async function loadHunt() {
  if (window.pdLevel !== "manage") return;
  try { hunt = await api("/api/research/hunt"); } catch (e) { return; }
  const c = hunt.config;
  if (!$("countries").dataset.ready) {
    $("countries").innerHTML = hunt.countries.map((x) => `<label title="${esc(x.name)}"><input type="checkbox" value="${x.code}"${c.countries.includes(x.code) ? " checked" : ""}> ${x.code === "GB" ? "UK" : x.code}</label>`).join("");
    $("cap").value = c.cap; $("match").value = c.match; $("deepMax").value = c.deep_max;
    $("countries").dataset.ready = "1";
  }
  $("kwList").innerHTML = c.keywords.map((k) => `<option value="${esc(k)}">`).join("");
  $("kwChips").innerHTML = c.keywords.map((k) => `<button type="button" data-kw="${esc(k)}">${esc(k)}</button>`).join("");
  estimate();
  const run = hunt.hunts.find((h) => h.status === "running");
  const last = hunt.hunts[0];
  const res = (h) => `${h.ads ?? 0} ads · ${h.stores ?? 0} stores · ${h.shopify ?? 0} Shopify · <b>${h.qualified ?? 0} dropshippers</b> (${h.new_stores ?? 0} new) · ${h.deep ?? 0} deep-checked · <b>${h.nuggets ?? 0} golden nuggets</b>${h.cost_usd ? ` · $${Number(h.cost_usd).toFixed(3)}` : ""}`;
  $("huntStatus").innerHTML = run ? `<div class="rs-status">Running “${esc(run.keyword)}”: ${esc(run.stage || "")}</div>`
    : last ? `<div class="rs-status${last.status === "error" ? " bad" : ""}">Last search “${esc(last.keyword)}” (${esc(when(last.finished_at || last.started_at))}): ${last.status === "error" ? esc(last.error || "stopped") : res(last)}</div>` : "";
  $("runHunt").disabled = !!run;
  $("runHunt").textContent = run ? "Running…" : "Run";
  $("huntMore").innerHTML = `
    <p class="hint" style="margin:8px 0 0">Saved keywords, one per line (the chips above):</p>
    <textarea id="kwText">${esc(c.keywords.join("\n"))}</textarea>
    <div class="rs-pop-actions" style="margin:0 0 8px"><button type="button" class="btn btn-sm" id="kwSave">Save keywords</button></div>
    <label class="field" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="autoOn"${c.auto ? " checked" : ""} style="width:auto;margin:0"> Search one saved keyword automatically every day at
      <select id="autoHour" style="width:auto;margin:0">${Array.from({ length: 24 }, (_, h) => `<option value="${h}"${h === c.auto_hour ? " selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select> Hong Kong time (next: “${esc(c.keywords[c.auto_next % Math.max(1, c.keywords.length)] || "")}”)</label>
    <p class="hint" style="margin:4px 0">Every morning EcomVital also re-reads the bestsellers of every dropshipper found (free), so new products jumping into a top 25 show up the same day.</p>
    ${hunt.hunts.length ? `<table><tr><th>When</th><th>Keyword</th><th>Countries</th><th>Result</th><th>By</th></tr>${hunt.hunts.map((h) => `<tr><td>${esc(when(h.started_at))}</td><td>${esc(h.keyword)}</td><td>${esc(h.countries.join(", ").replace("GB", "UK"))}</td><td>${h.status === "running" ? esc(h.stage || "running") : h.status === "error" ? `<span class="rs-run bad">${esc(h.error || "")}</span>` : res(h)}</td><td>${esc(h.by_name || "")}</td></tr>`).join("")}</table>` : ""}`;
  $("kwSave").onclick = async () => {
    try { await send("/api/research/hunt/settings", { keywords: $("kwText").value.split("\n") }, "PUT"); toast("Keywords saved."); loadHunt(); }
    catch (e) { toast(e.message, true); }
  };
  const saveAuto = async () => {
    try { await send("/api/research/hunt/settings", { auto: $("autoOn").checked, auto_hour: +$("autoHour").value }, "PUT"); toast("Saved."); loadHunt(); }
    catch (e) { toast(e.message, true); }
  };
  $("autoOn").onchange = saveAuto; $("autoHour").onchange = saveAuto;
  clearTimeout(window.__huntPoll);
  if (run) window.__huntPoll = setTimeout(() => { loadHunt(); if (!editing) load(); }, 5000);
  else if (window.__wasRunning) { window.__wasRunning = false; load(); loadProducts(); }
  if (run) window.__wasRunning = true;
}
$("kwChips").onclick = (e) => { const b = e.target.closest("[data-kw]"); if (b) { $("kw").value = b.dataset.kw; $("kw").focus(); } };
for (const id of ["cap", "deepMax"]) $(id).oninput = estimate;
$("countries").onchange = estimate;
$("runHunt").onclick = async () => {
  const keyword = $("kw").value.trim();
  if (!keyword) { toast("Type or pick a keyword first.", true); return; }
  if (!apifyOn) { toast("Connect Apify first (Apify button).", true); return; }
  const countries = [...document.querySelectorAll("#countries input:checked")].map((x) => x.value);
  try {
    const r = await send("/api/research/hunt", { keyword, countries, cap: +$("cap").value, match: $("match").value, deep_max: +$("deepMax").value });
    toast(`Searching “${keyword}”… usually 3–10 minutes. You can leave this page.`);
    loadHunt();
  } catch (e) { toast(e.message, true); }
};

// ---- Winning products
let productsFor = 0;
const LABELS = [["nugget,compete,proven", "Best picks"], ["nugget", "Golden nuggets"], ["compete", "Compete, same market"], ["proven", "Proven, other market"], ["top", "Top 25"], ["", "Everything"]];
const LABEL_NAME = { nugget: "Golden nugget", compete: "Compete", proven: "Proven", top: "Top 25", ads: "On Meta" };
async function loadProducts() {
  const q = new URLSearchParams(Object.entries({ label: labelF, item: productsFor || "", q: pquery }).filter(([, v]) => v));
  let d;
  try { d = await api(`/api/research/products?${q}`); } catch (e) { toast(e.message, true); return; }
  $("nugCount").textContent = d.counts.nugget || "";
  const store = productsFor ? items.find((i) => i.id === productsFor) : null;
  $("labels").innerHTML = (store ? `<button class="on" data-clear>${esc(store.name || store.domain)} ✕</button>` : "")
    + LABELS.map(([k, n]) => `<button data-label="${k}" class="${labelF === k ? "on" : ""}">${n}${k && !k.includes(",") && d.counts[k] ? ` ${d.counts[k]}` : ""}</button>`).join("");
  const today = Date.now();
  $("products").innerHTML = d.products.length ? `<table class="rs-pgrid"><thead><tr><th></th><th>Product</th><th>Store</th><th>Bestseller</th><th>Created</th><th>Meta ads</th><th>Why</th></tr></thead><tbody>
    ${d.products.map((p) => {
      const move = p.prev_rank && p.rank ? p.prev_rank - p.rank : null;
      return `<tr><td>${p.image ? `<img src="${esc(p.image)}&width=120" onerror="this.src='${esc(p.image)}'" alt="" loading="lazy">` : ""}</td>
      <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>${p.price ? `<small class="num">${esc(p.price)}</small>` : ""}</td>
      <td>${esc(p.store || p.domain)}<small>${esc(p.domain)}${p.store_since ? ` · since ${esc(p.store_since)}` : ""}</small></td>
      <td class="num">${p.rank ? "#" + p.rank : "—"}${move ? `<small>${move > 0 ? "▲" + move : "▼" + -move} since last check</small>` : ""}${p.best_unreliable ? `<small title="This store's best-selling page looks like newest first">rank may not be sales</small>` : ""}</td>
      <td class="num">${esc(p.created_at || "")}<small>${p.age_days >= 0 ? p.age_days + " days ago" : ""}</small></td>
      <td class="num">${p.meta_ads || "—"}${p.meta_first ? `<small>since ${esc(p.meta_first)}</small>` : ""}</td>
      <td>${p.label ? `<span class="rs-lab ${p.label}">${LABEL_NAME[p.label] || p.label}</span>` : ""}<div class="why">${esc(p.why || "")}</div></td></tr>`;
    }).join("")}</tbody></table>` : `<p class="hint" style="padding:10px">No products here yet. Run Find competitors, or press Analyse on a competitor's row.</p>`;
}
function switchTab(t) {
  tab = t;
  PDUrl.set({ tab: t === "sheet" ? "" : t });
  for (const b of document.querySelectorAll("#tabs [data-tab]")) b.classList.toggle("on", b.dataset.tab === t);
  $("sheetPanel").hidden = t !== "sheet";
  $("productsPanel").hidden = t !== "products";
  if (t === "products") loadProducts();
}
$("tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) { if (b.dataset.tab === "products") productsFor = 0; switchTab(b.dataset.tab); } };
$("labels").onclick = (e) => {
  if (e.target.closest("[data-clear]")) { productsFor = 0; loadProducts(); return; }
  const b = e.target.closest("[data-label]");
  if (b) { labelF = b.dataset.label; PDUrl.set({ label: labelF }); loadProducts(); }
};
let ptyping;
$("pq").oninput = (e) => { clearTimeout(ptyping); ptyping = setTimeout(() => { pquery = e.target.value; loadProducts(); }, 250); };

window.pdMe().then(async () => { await load(); loadHunt(); switchTab(tab); });
