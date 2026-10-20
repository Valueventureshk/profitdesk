/* EcomVital Apps → Research: competitor research as a spreadsheet. Click a cell to type, Enter or Tab saves
   (Tab moves right), Esc cancels. Columns come from the server (built-in + ones the team added). */

const $ = (id) => document.getElementById(id);
let cols = [], items = [], appName = "EcomVital", archived = PDUrl.get("archived") === "1", archivedCount = 0;
let sort = { key: PDUrl.get("sort") || "", dir: PDUrl.get("dir") === "asc" ? 1 : -1 };
let query = PDUrl.get("q") || "", nicheF = PDUrl.get("niche") || "", byF = PDUrl.get("by") || "";
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
function display(it, c) {
  const v = value(it, c);
  if (c.kind === "who") {
    return `<span class="rs-who${it.by_kind === "app" ? " app" : ""}">${esc(it.by_kind === "app" ? appName : v)}</span>`;
  }
  if (v === null || v === undefined || v === "") return "";
  if (c.kind === "link") return `<a href="${esc(v)}" target="_blank" rel="noopener" data-open>${esc(shortLink(v))}</a>${c.key === "site_url" && it.duplicate ? `<span class="rs-dup" title="Another row has the same website">twice</span>` : ""}`;
  if (c.kind === "number") return esc(c.key === "traffic" ? compact(v) : Number(v).toLocaleString());
  if (c.kind === "date") { const d = new Date(v + "T00:00"); return esc(isNaN(d) ? v : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })); }
  return esc(v);
}
function title(it, c) {
  const st = STAMP[c.key];
  if (st && it[st[0]]) return `Added by ${it[st[0]]}, ${when(it[st[1]])}`;
  const v = value(it, c);
  return v === null || v === undefined ? "" : String(v);
}

async function load() {
  try {
    const d = await api(`/api/research?archived=${archived ? 1 : 0}`);
    cols = d.columns; items = d.items; appName = d.app_name; archivedCount = d.archived_count;
  } catch (e) { toast(e.message, true); return; }
  fillFilters();
  draw();
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
    && (!q || [it.name, it.site_url, it.fb_page, it.niche, it.notes, it.by_name, ...Object.values(it.extra || {})]
      .some((v) => String(v ?? "").toLowerCase().includes(q))));
  if (sort.key) {
    const c = cols.find((x) => x.key === sort.key);
    if (c) rows = rows.slice().sort((a, b) => {
      let x = c.kind === "who" ? (a.by_kind === "app" ? appName : a.by_name) : value(a, c);
      let y = c.kind === "who" ? (b.by_kind === "app" ? appName : b.by_name) : value(b, c);
      const ex = x === null || x === undefined || x === "", ey = y === null || y === undefined || y === "";
      if (ex || ey) return ex === ey ? 0 : ex ? 1 : -1;          // empty cells always last
      if (c.kind === "number") return (x - y) * sort.dir;
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
      <span class="rs-cell${c.kind === "number" ? " num" : ""}${display(it, c) ? "" : " empty"}">${display(it, c)}</span></td>`).join("")}
    <td class="rs-acts"><button data-log="${it.id}" title="Who changed what">History</button>${canEdit() ? `<button data-arch="${it.id}">${archived ? "Restore" : "Archive"}</button>` : ""}</td></tr>`).join("");
  const add = !archived && canEdit() ? `<tr class="rs-new"><td class="rn">+</td>${vc.map((c) =>
    ["name", "site_url", "niche", "fb_page"].includes(c.key)
      ? `<td><input data-new="${c.key}" placeholder="${c.key === "name" ? "Add competitor…" : c.key === "site_url" ? "website.com" : c.key === "niche" ? "Niche" : "facebook.com/…"}"></td>`
      : `<td></td>`).join("")}<td></td></tr>` : "";
  const empty = !rows.length && !add ? `<tr><td class="rn"></td><td colspan="${vc.length + 1}"><p class="hint" style="padding:8px 10px">Nothing here yet.</p></td></tr>` : "";
  $("sheet").innerHTML = `<table class="rs-grid">${head}<tbody>${body}${empty}${add}</tbody></table>`;
}

// ---- editing a cell
let editing = null;
function startEdit(td) {
  if (!canEdit() || archived) return;
  const key = td.dataset.key, id = +td.closest("tr").dataset.id;
  const c = cols.find((x) => x.key === key);
  if (!c || c.kind === "who") return;
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
  const vc = visible().filter((c) => c.kind !== "who"), rows = shown();
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
$("archBtn").onclick = () => { archived = !archived; PDUrl.set({ archived: archived ? "1" : "" }); load(); };
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

window.pdMe().then(load);
