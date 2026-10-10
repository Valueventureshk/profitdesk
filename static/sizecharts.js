/* ProfitDesk Size Charts desk: list, editor (blocks), applies-to rules, import,
   AI from image, and per-store display settings. Rendering is done by the server
   (sizecharts.py) so the preview matches the store exactly. */

const $ = (id) => document.getElementById(id);
let meta = { stores: [], templates: [] };
let state = { page: 1 };
let picked = new Set();
let chart = null;          // the chart being edited: {id, name, status, countries, blocks, rules}
let previewHtml = { cm: "", in: null }, previewUnit = "cm";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/sizecharts"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}
const send = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3000);
}
const storeName = (id) => meta.stores.find((s) => s.id === +id)?.name || "?";

// ------------------------------------------------ list
async function loadList() {
  const q = new URLSearchParams({ q: $("q").value, status: $("fStatus").value, store: $("fStore").value, page: state.page });
  const d = await api(`/api/sizecharts?${q}`);
  meta = { stores: d.stores, templates: d.templates };
  if ($("fStore").options.length <= 1) {
    $("fStore").innerHTML += d.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    $("addTemplate").innerHTML += d.templates.map((t) => `<option value="${t.key}">${esc(t.name)}</option>`).join("");
  }
  $("list").innerHTML = `<thead><tr><th class="scm-check"><input type="checkbox" id="pickAll"></th><th>Name</th><th>Status</th><th>Applies to</th><th>Updated</th><th></th></tr></thead><tbody>${
    d.charts.map((c) => `<tr data-id="${c.id}"><td class="scm-check" onclick="event.stopPropagation()"><input type="checkbox" data-pick="${c.id}"${picked.has(String(c.id)) ? " checked" : ""}></td>
      <td class="name sc-namecell"><span class="sc-ico">${Garments.svg(Garments.guess(c.name), 22)}</span><span><strong>${esc(c.name)}</strong>${c.countries ? `<span class="sub">only ${esc(c.countries)}</span>` : ""}</span></td>
      <td><span class="imp-st ${c.status === "active" ? "st-done" : ""}">${c.status === "active" ? "Active" : "Draft"}</span></td>
      <td>${c.applies.length ? c.applies.map((a) => `<span class="sc-chip">${esc(a.store)} · ${a.count} ${a.kinds.join("/")}</span>`).join(" ") : `<span class="hint">not assigned</span>`}</td>
      <td class="hint">${esc((c.updated_at || "").slice(0, 10))}</td>
      <td class="sc-rowbtns" onclick="event.stopPropagation()" data-write-only><button class="btn btn-sm btn-ghost" data-dup="${c.id}">Duplicate</button></td></tr>`).join("")
    || `<tr><td colspan="6" class="hint">No size charts yet. Create one, import from Panda/Kiwi, or let the AI read an image.</td></tr>`}</tbody>`;
  for (const tr of $("list").querySelectorAll("tr[data-id]")) tr.onclick = () => openEditor(+tr.dataset.id);
  for (const cb of $("list").querySelectorAll("[data-pick]")) cb.onchange = () => { cb.checked ? picked.add(cb.dataset.pick) : picked.delete(cb.dataset.pick); renderBulk(); };
  $("pickAll").onchange = (e) => { for (const cb of $("list").querySelectorAll("[data-pick]")) { cb.checked = e.target.checked; cb.onchange(); } };
  for (const b of $("list").querySelectorAll("[data-dup]")) b.onclick = async () => {
    const r = await send(`/api/sizecharts/${b.dataset.dup}/duplicate`, {});
    toast("Duplicated."); openEditor(r.id);
  };
  $("pager").innerHTML = d.pages > 1 ? `<button class="btn btn-sm btn-ghost" id="pPrev"${state.page <= 1 ? " disabled" : ""}>‹</button>
    <span class="hint">Page ${state.page} of ${d.pages} · ${d.total.toLocaleString()} charts</span>
    <button class="btn btn-sm btn-ghost" id="pNext"${state.page >= d.pages ? " disabled" : ""}>›</button>` : `<span class="hint">${d.total} charts</span>`;
  if ($("pPrev")) { $("pPrev").onclick = () => { state.page--; loadList(); }; $("pNext").onclick = () => { state.page++; loadList(); }; }
  renderBulk();
}
function renderBulk() { $("bulk").hidden = !picked.size; $("bulkCount").textContent = `${picked.size} selected`; }
for (const b of document.querySelectorAll("[data-bulk]")) b.onclick = async () => {
  const ids = [...picked];
  if (b.dataset.bulk === "export") { location.href = `/api/sizecharts/export?ids=${ids.join(",")}`; return; }
  if (b.dataset.bulk === "delete" && !confirm(`Delete ${ids.length} size chart(s)?`)) return;
  try { await send("/api/sizecharts/bulk", { ids, action: b.dataset.bulk }); picked.clear(); toast("Done."); loadList(); }
  catch (e) { toast(e.message, true); }
};
let typing;
$("q").oninput = () => { clearTimeout(typing); typing = setTimeout(() => { state.page = 1; loadList(); }, 300); };
$("fStore").onchange = $("fStatus").onchange = () => { state.page = 1; loadList(); };

// ------------------------------------------------ editor
function showPage(p) {
  $("page-list").hidden = p !== "list";
  $("page-edit").hidden = p !== "edit";
  $("page-settings").hidden = p !== "settings";
  for (const x of $("pages").querySelectorAll("[data-page]")) x.classList.toggle("on", x.dataset.page === (p === "edit" ? "list" : p));
}
async function openEditor(id, preset) {
  chart = id ? await api(`/api/sizecharts/${id}`) : { id: null, name: "", status: "active", countries: "", blocks: [], rules: [], ...(preset || {}) };
  $("edName").value = chart.name;
  edIcon();
  $("edStatus").value = chart.status;
  $("edCountries").value = chart.countries || "";
  showPage("edit");
  setTab("content");
  renderBlocks();
  renderRules();
  window.scrollTo(0, 0);
}
function setTab(t) {
  for (const b of $("edTabs").querySelectorAll("[data-t]")) b.classList.toggle("on", b.dataset.t === t);
  for (const x of ["content", "applies", "where"]) $(`t-${x}`).hidden = x !== t;
}
for (const b of $("edTabs").querySelectorAll("[data-t]")) b.onclick = () => setTab(b.dataset.t);

function newBlock(type) {
  if (type === "table") return { type, label: "", unit: "cm", header: ["Size", "Bust", "Waist", "Hip"], rows: [["S", "", "", ""], ["M", "", "", ""], ["L", "", "", ""]] };
  if (type === "title") return { type, text: "Size guide" };
  if (type === "text") return { type, html: "<p>How to measure: …</p>" };
  if (type === "image") return { type, url: "", alt: "", width: 60 };
  if (type === "tabs") return { type, tabs: [{ label: "CM", blocks: [newBlock("table")] }, { label: "Inches", blocks: [{ ...newBlock("table"), unit: "in" }] }] };
  return { type };
}

function tableEditor(b, path) {
  const cols = Math.max(b.header.length, ...b.rows.map((r) => r.length), 1);
  const cell = (v, r, c) => `<input class="sc-cell" data-path="${path}" data-r="${r}" data-c="${c}" value="${esc(v ?? "")}">`;
  return `<div class="sc-tblbar">
      <input class="control sc-small" data-path="${path}" data-f="label" placeholder="Table label (optional)" value="${esc(b.label)}">
      <select class="control sc-small" data-path="${path}" data-f="unit"><option value="cm"${b.unit === "cm" ? " selected" : ""}>Measurements in cm (customers can switch to inches)</option><option value="in"${b.unit === "in" ? " selected" : ""}>Measurements in inches</option><option value=""${!b.unit ? " selected" : ""}>No unit (e.g. shoe sizes)</option></select>
    </div>
    <div class="sc-grid-wrap"><table class="sc-grid"><thead><tr>${Array.from({ length: cols }, (_, c) => `<th>${cell(b.header[c], -1, c)}</th>`).join("")}<th></th></tr></thead>
    <tbody>${b.rows.map((row, r) => `<tr>${Array.from({ length: cols }, (_, c) => `<td>${cell(row[c], r, c)}</td>`).join("")}<td><button class="sc-x" data-path="${path}" data-delrow="${r}" title="Remove row">×</button></td></tr>`).join("")}</tbody></table></div>
    <div class="sc-tblbtns"><button class="btn btn-sm btn-ghost" data-path="${path}" data-op="addrow">+ Row</button>
      <button class="btn btn-sm btn-ghost" data-path="${path}" data-op="addcol">+ Column</button>
      <button class="btn btn-sm btn-ghost" data-path="${path}" data-op="delcol">− Last column</button>
      <button class="btn btn-sm btn-ghost" data-path="${path}" data-op="paste">Paste from Excel / Sheets</button></div>`;
}

function blockEditor(b, path) {
  if (b.type === "title") return `<input class="control" data-path="${path}" data-f="text" value="${esc(b.text)}">`;
  if (b.type === "table") return tableEditor(b, path);
  if (b.type === "text") return `<textarea class="control sc-text" rows="4" data-path="${path}" data-f="html">${esc(b.html)}</textarea><small class="hint">Simple HTML allowed: &lt;p&gt;, &lt;b&gt;, &lt;ul&gt;&lt;li&gt;, tables.</small>`;
  if (b.type === "image") return `<input class="control" data-path="${path}" data-f="url" placeholder="Image URL (https://…)" value="${esc(b.url)}">
    <div class="imp-inline"><input class="control" data-path="${path}" data-f="alt" placeholder="Alt text" value="${esc(b.alt)}">
    Width <input class="control" type="number" data-path="${path}" data-f="width" value="${esc(b.width)}"> %</div>`;
  if (b.type === "tabs") return b.tabs.map((t, j) => `<div class="sc-tabblock"><div class="imp-inline"><b>Tab</b>
      <input class="control" data-path="${path}.tabs.${j}" data-f="label" value="${esc(t.label)}">
      <button class="btn btn-sm btn-ghost" data-path="${path}" data-deltab="${j}">Remove tab</button></div>
      ${t.blocks.map((x, k) => `<div class="sc-sub">${blockEditor(x, `${path}.tabs.${j}.blocks.${k}`)}</div>`).join("")}</div>`).join("") +
    `<button class="btn btn-sm btn-ghost" data-path="${path}" data-addtab="1">+ Tab</button>`;
  return `<span class="hint">A divider line.</span>`;
}
const LABELS = { title: "Title", table: "Table", text: "Text", image: "Image", divider: "Divider", tabs: "Tabs" };

function renderBlocks() {
  $("blocks").innerHTML = chart.blocks.map((b, i) => `<div class="sc-block">
      <div class="sc-bhead"><b>${LABELS[b.type] || b.type}</b><span>
        <button class="sc-x" data-move="${i}" data-dir="-1" title="Move up">↑</button>
        <button class="sc-x" data-move="${i}" data-dir="1" title="Move down">↓</button>
        <button class="sc-x" data-del="${i}" title="Remove">×</button></span></div>
      ${blockEditor(b, String(i))}</div>`).join("") || `<p class="hint">Empty. Add a template, a table, or read one from an image.</p>`;
  wireBlocks();
  preview();
}
function at(path) {
  const parts = path.split(".");
  let o = chart.blocks[+parts[0]];
  for (let i = 1; i < parts.length; i += 2) o = o[parts[i]][+parts[i + 1]];
  return o;
}
function wireBlocks() {
  const box = $("blocks");
  for (const el of box.querySelectorAll("[data-f]")) el.oninput = el.onchange = () => {
    const b = at(el.dataset.path);
    b[el.dataset.f] = el.dataset.f === "width" ? +el.value : el.value;
    schedulePreview();
  };
  for (const el of box.querySelectorAll(".sc-cell")) {
    el.oninput = () => {
      const b = at(el.dataset.path), r = +el.dataset.r, c = +el.dataset.c;
      if (r < 0) b.header[c] = el.value; else { b.rows[r] = b.rows[r] || []; b.rows[r][c] = el.value; }
      schedulePreview();
    };
    el.onpaste = (e) => {
      const t = (e.clipboardData || window.clipboardData).getData("text");
      if (!t.includes("\t") && !t.includes("\n")) return;
      e.preventDefault();
      pasteGrid(at(el.dataset.path), t, +el.dataset.r, +el.dataset.c);
    };
  }
  for (const b of box.querySelectorAll("[data-op]")) b.onclick = () => {
    const t = at(b.dataset.path), cols = Math.max(t.header.length, ...t.rows.map((r) => r.length));
    if (b.dataset.op === "addrow") t.rows.push(Array(cols).fill(""));
    if (b.dataset.op === "addcol") { t.header.push(""); t.rows.forEach((r) => r.push("")); }
    if (b.dataset.op === "delcol" && cols > 1) { t.header.length = cols - 1; t.rows.forEach((r) => { r.length = Math.min(r.length, cols - 1); }); }
    if (b.dataset.op === "paste") {
      const txt = prompt("Paste the table copied from Excel or Google Sheets (first row = headings):");
      if (txt) { t.header = []; t.rows = []; pasteGrid(t, txt, -1, 0); return; }
    }
    renderBlocks();
  };
  for (const b of box.querySelectorAll("[data-delrow]")) b.onclick = () => { at(b.dataset.path).rows.splice(+b.dataset.delrow, 1); renderBlocks(); };
  for (const b of box.querySelectorAll("[data-del]")) b.onclick = () => { chart.blocks.splice(+b.dataset.del, 1); renderBlocks(); };
  for (const b of box.querySelectorAll("[data-move]")) b.onclick = () => {
    const i = +b.dataset.move, j = i + +b.dataset.dir;
    if (j < 0 || j >= chart.blocks.length) return;
    [chart.blocks[i], chart.blocks[j]] = [chart.blocks[j], chart.blocks[i]];
    renderBlocks();
  };
  for (const b of box.querySelectorAll("[data-addtab]")) b.onclick = () => { at(b.dataset.path).tabs.push({ label: "Tab", blocks: [newBlock("table")] }); renderBlocks(); };
  for (const b of box.querySelectorAll("[data-deltab]")) b.onclick = () => { at(b.dataset.path).tabs.splice(+b.dataset.deltab, 1); renderBlocks(); };
}
function pasteGrid(t, text, r0, c0) {
  const lines = text.replace(/\r/g, "").split("\n").filter((l) => l.trim() !== "").map((l) => l.split("\t"));
  lines.forEach((cells, i) => {
    const r = r0 + i;
    cells.forEach((v, j) => {
      const c = c0 + j;
      if (r < 0) t.header[c] = v.trim();
      else { t.rows[r] = t.rows[r] || []; t.rows[r][c] = v.trim(); }
    });
  });
  const cols = Math.max(t.header.length, ...t.rows.map((x) => x.length));
  t.header = Array.from({ length: cols }, (_, i) => t.header[i] ?? "");
  t.rows = t.rows.map((x) => Array.from({ length: cols }, (_, i) => (x || [])[i] ?? ""));
  renderBlocks();
}
for (const b of document.querySelectorAll("[data-add]")) b.onclick = () => { chart.blocks.push(newBlock(b.dataset.add)); renderBlocks(); };
$("addTemplate").onchange = async (e) => {
  const key = e.target.value;
  e.target.value = "";
  if (!key) return;
  const tpl = TEMPLATES_FROM_SERVER[key];
  if (tpl) { chart.blocks.push(...JSON.parse(JSON.stringify(tpl))); renderBlocks(); }
};
let TEMPLATES_FROM_SERVER = {};

let pvTimer;
function schedulePreview() { clearTimeout(pvTimer); pvTimer = setTimeout(preview, 350); }
async function preview() {
  try {
    const d = await send("/api/sizecharts/preview", { blocks: chart.blocks });
    previewHtml = { cm: d.html, in: d.html_in };
    $("prevUnits").hidden = !d.html_in;
    if (!d.html_in) previewUnit = "cm";
    $("preview").innerHTML = (previewUnit === "in" && d.html_in ? d.html_in : d.html) || `<p class="hint">Nothing to show yet.</p>`;
    wirePreviewTabs();
  } catch { /* keep the old preview */ }
}
function wirePreviewTabs() {
  for (const b of $("preview").querySelectorAll(".pdsc-tab")) b.onclick = () => {
    const g = b.dataset.tab.split("-")[0];
    for (const x of $("preview").querySelectorAll(`.pdsc-tab[data-tab^="${g}-"]`)) x.classList.toggle("on", x === b);
    for (const p of $("preview").querySelectorAll(`.pdsc-pane[data-pane^="${g}-"]`)) p.hidden = p.dataset.pane !== b.dataset.tab;
  };
}
for (const b of $("prevUnits").querySelectorAll("[data-u]")) b.onclick = () => {
  previewUnit = b.dataset.u;
  for (const x of $("prevUnits").querySelectorAll("[data-u]")) x.classList.toggle("on", x === b);
  $("preview").innerHTML = previewUnit === "in" && previewHtml.in ? previewHtml.in : previewHtml.cm;
  wirePreviewTabs();
};

// ------------------------------------------------ applies-to rules
const KINDS = [["product", "Products"], ["collection", "Collection"], ["tag", "Tag"], ["type", "Product type"], ["vendor", "Vendor"], ["all", "All products"]];
function renderRules() {
  const byKey = {};
  chart.rules.forEach((r, i) => { const k = `${r.store_id}|${r.kind}`; (byKey[k] = byKey[k] || []).push([r, i]); });
  $("rules").innerHTML = chart.rules.map((r, i) => `<div class="sc-rule">
      <select class="control" data-ri="${i}" data-rf="store_id">${meta.stores.map((s) => `<option value="${s.id}"${+r.store_id === s.id ? " selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
      <select class="control" data-ri="${i}" data-rf="kind">${KINDS.map(([k, n]) => `<option value="${k}"${r.kind === k ? " selected" : ""}>${n}</option>`).join("")}</select>
      <span class="sc-rval">${ruleValue(r, i)}</span>
      <button class="sc-x" data-rdel="${i}" title="Remove">×</button></div>`).join("") || `<p class="hint">Not assigned yet, so it won't show anywhere.</p>`;
  for (const el of $("rules").querySelectorAll("[data-rf]")) el.onchange = () => {
    const r = chart.rules[+el.dataset.ri];
    r[el.dataset.rf] = el.dataset.rf === "store_id" ? +el.value : el.value;
    if (el.dataset.rf !== "store_id" || r.kind === "product" || r.kind === "collection") { r.value = ""; r.label = ""; }
    renderRules();
  };
  for (const b of $("rules").querySelectorAll("[data-rdel]")) b.onclick = () => { chart.rules.splice(+b.dataset.rdel, 1); renderRules(); };
  for (const el of $("rules").querySelectorAll("[data-rtext]")) el.oninput = () => { const r = chart.rules[+el.dataset.rtext]; r.value = el.value.trim(); r.label = el.value.trim(); };
  for (const el of $("rules").querySelectorAll("[data-psearch]")) el.oninput = () => productSearch(el);
  for (const el of $("rules").querySelectorAll("[data-coll]")) {
    loadCollections(+chart.rules[+el.dataset.coll].store_id).then((cols) => {
      const r = chart.rules[+el.dataset.coll];
      el.innerHTML = `<option value="">Choose a collection…</option>` + cols.map((c) => `<option value="${esc(c.id)}"${r.value === c.id ? " selected" : ""}>${esc(c.title)}</option>`).join("");
    });
    el.onchange = () => { const r = chart.rules[+el.dataset.coll]; r.value = el.value; r.label = el.options[el.selectedIndex].text; };
  }
}
function ruleValue(r, i) {
  if (r.kind === "all") return `<span class="hint">every product in this store</span>`;
  if (r.kind === "collection") return `<select class="control" data-coll="${i}"><option>Loading…</option></select>`;
  if (r.kind === "product") return r.value ? `<span class="sc-chip">${esc(r.label || r.value)}</span>`
    : `<span class="sc-ps"><input class="control" data-psearch="${i}" placeholder="Search products in ${esc(storeName(r.store_id))}"><span class="sc-psres"></span></span>`;
  return `<input class="control" data-rtext="${i}" value="${esc(r.value)}" placeholder="${r.kind === "tag" ? "e.g. dresses" : r.kind === "type" ? "e.g. Dress" : "e.g. Vendor name"}">`;
}
const collCache = {};
async function loadCollections(store) {
  if (!collCache[store]) collCache[store] = api(`/api/sizecharts/collections?store=${store}`).then((d) => d.collections).catch(() => []);
  return collCache[store];
}
let psTimer;
function productSearch(el) {
  clearTimeout(psTimer);
  psTimer = setTimeout(async () => {
    const r = chart.rules[+el.dataset.psearch];
    const out = el.parentNode.querySelector(".sc-psres");
    try {
      const d = await api(`/api/sizecharts/products?store=${r.store_id}&q=${encodeURIComponent(el.value)}`);
      out.innerHTML = d.products.map((p) => `<button type="button" data-pid="${esc(p.id)}" data-pt="${esc(p.title)}">${p.image ? `<img src="${esc(p.image)}&width=60" alt="">` : ""}${esc(p.title)}</button>`).join("") || `<span class="hint">No match</span>`;
      for (const b of out.querySelectorAll("[data-pid]")) b.onclick = () => {
        // pick: this rule gets the product; keep adding more products as extra rules
        r.value = b.dataset.pid; r.label = b.dataset.pt;
        chart.rules.push({ store_id: r.store_id, kind: "product", value: "", label: "" });
        renderRules();
      };
    } catch (e) { out.textContent = e.message; }
  }, 300);
}
$("addRule").onclick = () => {
  const last = chart.rules[chart.rules.length - 1];
  chart.rules.push({ store_id: last ? last.store_id : meta.stores[0]?.id, kind: "product", value: "", label: "" });
  renderRules();
};

$("edSave").onclick = async () => {
  chart.name = $("edName").value.trim() || "Size chart";
  chart.status = $("edStatus").value;
  chart.countries = $("edCountries").value;
  const body = { name: chart.name, status: chart.status, countries: chart.countries, blocks: chart.blocks,
    rules: chart.rules.filter((r) => r.kind === "all" || r.value), source: chart.source };
  try {
    const r = chart.id ? await send(`/api/sizecharts/${chart.id}`, body, "PUT") : await send("/api/sizecharts", body);
    chart.id = r.id;
    toast("Saved.");
  } catch (e) { toast(e.message, true); }
};
$("edBack").onclick = () => { showPage("list"); loadList(); };
function edIcon() { $("edIcon").innerHTML = Garments.svg(Garments.guess($("edName").value), 26); }
$("edName").addEventListener("input", edIcon);

// "Create size chart": a gallery of templates with garment icons
$("btnNew").onclick = () => {
  const card = (key, icon, name, sub) => `<button class="sc-tpl" data-tpl="${key}"><span class="sc-ico sc-ico-xl">${icon}</span><b>${esc(name)}</b><small>${esc(sub)}</small></button>`;
  const cols = (key) => (TEMPLATES_FROM_SERVER[key] || []).find((b) => b.type === "table")?.header.slice(1).join(" · ") || "";
  $("gallery").innerHTML = card("", Garments.svg("hanger", 40), "Blank", "Start from scratch")
    + (meta.templates || []).map((t) => card(t.key, Garments.svg(Garments.forTemplate(t.key), 40), t.name, cols(t.key))).join("")
    + `<button class="sc-tpl sc-tpl-ai" data-tpl="__ai"><span class="sc-ico sc-ico-xl">✦</span><b>From an image</b><small>AI reads a supplier's chart</small></button>`;
  $("newDlg").showModal();
};
$("newCancel").onclick = () => $("newDlg").close();
$("gallery").onclick = (e) => {
  const b = e.target.closest("[data-tpl]");
  if (!b) return;
  $("newDlg").close();
  const key = b.dataset.tpl;
  if (key === "__ai") return $("btnAI").click();
  const t = (meta.templates || []).find((x) => x.key === key);
  openEditor(null, key ? { name: t?.name || "", blocks: JSON.parse(JSON.stringify(TEMPLATES_FROM_SERVER[key] || [])) } : {});
};

// ------------------------------------------------ AI from image
let aiFiles = [], aiTarget = "new";
function openAI(target) {
  aiTarget = target; aiFiles = []; $("aiThumbs").innerHTML = "";
  const st = chart?.rules?.[0]?.store_id;
  const lang = meta.stores.find((s) => s.id === +st)?.lang;
  $("aiLang").value = lang === "fr" ? "French" : lang === "es" ? "Spanish" : "English";
  $("aiDlg").showModal();
}
function addAIFiles(list) {
  for (const f of list) if (f.type.startsWith("image/")) aiFiles.push(f);
  $("aiThumbs").innerHTML = aiFiles.map((f) => `<img src="${URL.createObjectURL(f)}" alt="">`).join("");
}
$("aiFiles").onchange = (e) => addAIFiles(e.target.files);
$("aiDrop").ondragover = (e) => { e.preventDefault(); $("aiDrop").classList.add("on"); };
$("aiDrop").ondragleave = () => $("aiDrop").classList.remove("on");
$("aiDrop").ondrop = (e) => { e.preventDefault(); $("aiDrop").classList.remove("on"); addAIFiles(e.dataTransfer.files); };
document.addEventListener("paste", (e) => { if ($("aiDlg").open) addAIFiles([...e.clipboardData.files]); });
$("aiCancel").onclick = () => $("aiDlg").close();
$("aiGo").onclick = async () => {
  if (!aiFiles.length) { toast("Add an image first.", true); return; }
  const fd = new FormData();
  aiFiles.forEach((f) => fd.append("images", f));
  fd.append("language", $("aiLang").value);
  fd.append("unit", $("aiUnit").value);
  $("aiGo").disabled = true; $("aiGo").textContent = "Reading… (about 20 seconds)";
  try {
    const d = await api("/api/sizecharts/from-image", { method: "POST", body: fd });
    $("aiDlg").close();
    if (aiTarget === "edit" && chart) { chart.blocks.push(...d.blocks); renderBlocks(); }
    else openEditor(null, { name: d.name, blocks: d.blocks, source: "ai" });
    toast("Done. Check the numbers, assign the products, then Save.");
  } catch (e) { toast(e.message, true); }
  finally { $("aiGo").disabled = false; $("aiGo").textContent = "Read the chart"; }
};
$("btnAI").onclick = () => { chart = null; openAI("new"); };
$("edAI").onclick = () => openAI("edit");

// ------------------------------------------------ import
$("btnImport").onclick = () => {
  $("impStore").innerHTML = `<option value="">Don't link (import as draft)</option>` + meta.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  $("impNote").textContent = "";
  $("impDlg").showModal();
};
$("impCancel").onclick = () => $("impDlg").close();
$("impGo").onclick = async () => {
  const f = $("impFile").files[0];
  if (!f) { toast("Choose the export file.", true); return; }
  let data;
  try { data = JSON.parse(await f.text()); } catch { toast("That file isn't valid JSON.", true); return; }
  $("impGo").disabled = true; $("impGo").textContent = "Importing… (a minute for big files)";
  try {
    const r = await send("/api/sizecharts/import", { data, store_id: $("impStore").value, source: /panda/i.test(f.name) ? "panda" : /kiwi/i.test(f.name) ? "kiwi" : "import" });
    $("impNote").textContent = `Imported ${r.imported} charts. ${r.linked} linked to their product (Active)${r.unlinked ? `, ${r.unlinked} not matched (Draft, assign them yourself)` : ""}.`;
    toast("Imported.");
    loadList();
  } catch (e) { toast(e.message, true); }
  finally { $("impGo").disabled = false; $("impGo").textContent = "Import"; }
};

// ------------------------------------------------ display settings
const ICONS = { ruler: "📏 Ruler", tape: "Tape", hanger: "Hanger", shirt: "Shirt", none: "No icon" };
async function loadSettings() {
  const d = await api("/api/sizecharts/settings");
  $("settings").innerHTML = d.stores.map((s) => {
    const c = s.cfg, t = d.text[s.lang] || d.text.en;
    return `<div class="panel sc-set" data-store="${s.id}">
      <div class="sc-sethead"><b>${esc(s.name)}</b><span class="hint">${esc(s.lang.toUpperCase())}</span>
        <label class="sc-switch"><input type="checkbox" data-k="enabled"${c.enabled ? " checked" : ""}><span>${c.enabled ? "On" : "Off"}</span></label></div>
      <div class="sc-setgrid">
        <label>Show as<select class="control" data-k="display"><option value="button"${c.display === "button" ? " selected" : ""}>Button</option><option value="link"${c.display === "link" ? " selected" : ""}>Link</option></select></label>
        <label>Label<input class="control" data-k="label" value="${esc(c.label)}" placeholder="${esc(t.label)}"></label>
        <label>Icon<select class="control" data-k="icon">${Object.entries(ICONS).map(([k, n]) => `<option value="${k}"${c.icon === k ? " selected" : ""}>${n}</option>`).join("")}</select></label>
        <label>Position<select class="control" data-k="position"><option value="size_label"${c.position === "size_label" ? " selected" : ""}>Next to the “Size” label (recommended)</option><option value="above_variants"${c.position === "above_variants" ? " selected" : ""}>Above the size options</option><option value="below_title"${c.position === "below_title" ? " selected" : ""}>Below the product title</option><option value="above_cart"${c.position === "above_cart" ? " selected" : ""}>Above Add to cart</option></select></label>
        <label>Text colour<input type="color" data-k="color" value="${esc(c.color)}"></label>
        <label>Button colour<input type="color" data-k="bg" value="${esc(c.bg)}"></label>
        <label>Border<input type="color" data-k="border" value="${esc(c.border)}"></label>
        <label>Accent<input type="color" data-k="accent" value="${esc(c.accent)}"></label>
        <label>Font size<input class="control" type="number" data-k="font_size" value="${esc(c.font_size)}"></label>
        <label>Align<select class="control" data-k="align"><option value="left"${c.align === "left" ? " selected" : ""}>Left</option><option value="center"${c.align === "center" ? " selected" : ""}>Center</option><option value="right"${c.align === "right" ? " selected" : ""}>Right</option></select></label>
        <label class="imp-check"><input type="checkbox" data-k="bold"${c.bold ? " checked" : ""}><span>Bold</span></label>
        <label class="imp-check"><input type="checkbox" data-k="advisor"${c.advisor ? " checked" : ""}><span>✦ “${esc(t.advisor)}” AI advisor</span></label>
      </div>
      <div class="sc-setfoot"><span class="sc-sample" data-sample></span>
        <button class="btn btn-sm btn-primary" data-save>Save</button></div></div>`;
  }).join("");
  for (const card of $("settings").querySelectorAll("[data-store]")) {
    const sample = () => {
      const v = (k) => card.querySelector(`[data-k="${k}"]`);
      const link = v("display").value === "link";
      const label = v("label").value || v("label").placeholder;
      card.querySelector("[data-sample]").innerHTML = `<span style="display:inline-flex;gap:6px;align-items:center;font-size:${+v("font_size").value || 14}px;color:${v("color").value};${v("bold").checked ? "font-weight:600;" : ""}${link ? "text-decoration:underline;" : `background:${v("bg").value};border:1px solid ${v("border").value};border-radius:6px;padding:7px 13px;`}">📏 ${esc(label)}</span>`;
    };
    card.addEventListener("input", sample);
    sample();
    card.querySelector("[data-save]").onclick = async () => {
      const body = {};
      for (const el of card.querySelectorAll("[data-k]")) body[el.dataset.k] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
      try {
        await send(`/api/sizecharts/settings/${card.dataset.store}`, body, "PUT");
        toast(body.enabled ? "Saved. Size charts are on for this store." : "Saved.");
        loadSettings();
      } catch (e) { toast(e.message, true); }
    };
  }
}

for (const b of $("pages").querySelectorAll("[data-page]")) b.onclick = () => {
  showPage(b.dataset.page);
  if (b.dataset.page === "settings") loadSettings().catch((e) => toast(e.message, true));
  else loadList().catch((e) => toast(e.message, true));
};

(async function boot() {
  try {
    await loadList();
    const t = await send("/api/sizecharts/preview", { blocks: [] });   // warm-up
    TEMPLATES_FROM_SERVER = await api("/api/sizecharts/templates");
    const q = new URLSearchParams(location.search);
    if (q.get("edit")) await openEditor(+q.get("edit"));
    else if (q.get("new") && q.get("product")) {
      const title = q.get("title") || "";
      await openEditor(null, { name: title, rules: [{ store_id: +q.get("store"), kind: "product", value: q.get("product"), label: title }] });
      toast("New chart for this product. Add a table, a template or ✦ Add from image, then Save.");
    }
    if (q.get("edit") || q.get("new")) history.replaceState(null, "", "/sizecharts");
  } catch (e) { if (!TEMPLATES_FROM_SERVER) toast(e.message, true); }
})();
