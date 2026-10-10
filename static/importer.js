/* ProfitDesk Product Importer: load products from any Shopify store and copy
   them into our stores with the owner's rules (importer.py does the work). */

const $ = (id) => document.getElementById(id);
let cfg = {}, stores = [], picked = new Set();
let current = null;          // last loaded {token, host, currency, products}
let pollTimer = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/importer"; throw new Error("Log in again."); }
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
const thumb = (u, w) => (u ? `${u}${u.includes("?") ? "&" : "?"}width=${w}` : "");
const money = (v, cur) => {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "USD" }).format(v); }
  catch { return `${cur || ""} ${Number(v).toFixed(2)}`; }
};

// ------------------------------------------------ stores picker (shared by both pages)
function chosenStores() {
  try { return JSON.parse(localStorage.getItem("impStores") || "[]").filter((id) => stores.some((s) => s.id === id)); }
  catch { return []; }
}
function renderStorePickers() {
  const sel = new Set(chosenStores());
  for (const box of document.querySelectorAll("[data-stores]")) {
    box.innerHTML = `<details class="imp-pick"><summary>Import into (${sel.size}) </summary><div class="imp-pick-list">
      <label class="imp-pick-all"><input type="checkbox" data-all${sel.size === stores.length ? " checked" : ""}> All stores</label>
      ${stores.map((s) => `<label><input type="checkbox" value="${s.id}"${sel.has(s.id) ? " checked" : ""}> ${esc(s.name)} <span class="hint">${esc(s.currency)}</span></label>`).join("")}</div></details>`;
    box.querySelector("[data-all]").onchange = (e) => {
      try { localStorage.setItem("impStores", JSON.stringify(e.target.checked ? stores.map((s) => s.id) : [])); } catch { /* */ }
      renderStorePickers();
      renderCollectionPickers();
    };
    for (const cb of box.querySelectorAll("input[value]")) cb.onchange = () => {
      const now = [...box.querySelectorAll("input[value]:checked")].map((i) => +i.value);
      try { localStorage.setItem("impStores", JSON.stringify(now)); } catch { /* */ }
      renderStorePickers();
      box.querySelector("details").open = true;
      renderCollectionPickers();
    };
  }
}

// ------------------------------------------------ price preview (same rules as the server)
function endWhole(x, d) {
  if (!d || !/^\d+$/.test(d)) return x;
  const whole = Math.floor(x), frac = x - whole, step = 10 ** d.length;
  let t = Math.floor(whole / step) * step + +d;
  if (t < whole) t += step;
  return t + frac;
}
function endCents(x, c) { return c === "" || c == null || !/^\d+$/.test(String(c)) ? Math.round(x * 100) / 100 : Math.floor(x) + +String(c).padEnd(2, "0").slice(0, 2) / 100; }
function previewPrice(src) {
  let x = src;
  if (cfg.price_enabled) {
    const sign = cfg.price_mode === "decrease" ? -1 : 1;
    x = Math.max(0, x * (1 + sign * (+cfg.price_pct || 0) / 100) + sign * (+cfg.price_fixed || 0));
    x = endWhole(x, String(cfg.price_whole_end || ""));
    x = endCents(x, String(cfg.price_cents ?? ""));
  }
  return Math.round(x * 100) / 100;
}

// ------------------------------------------------ load (in the browser: Shopify blocks servers from this)
function parseUrl(raw) {
  let url = (raw || "").trim();
  if (!url) throw new Error("Paste a product, collection or store link.");
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  let u;
  try { u = new URL(url); } catch { throw new Error("That doesn't look like a web address."); }
  const path = u.pathname.replace(/\/$/, "");
  let m = path.match(/\/products\/([^/?#]+)/);
  if (m) return { host: u.host, kind: "product", handle: m[1].replace(/\.json$/, "") };
  m = path.match(/\/collections\/([^/?#]+)/);
  if (m) return { host: u.host, kind: "collection", handle: m[1] };
  return { host: u.host, kind: "store" };
}
async function getJson(url) {
  let r;
  try { r = await fetch(url, { credentials: "omit" }); }
  catch { throw new Error("That store didn't answer, or it blocks product copying."); }
  if (r.status === 404) throw new Error("Nothing found at that address. The product may have been removed.");
  if (r.status === 401 || r.status === 403) throw new Error("That store blocks product copying (or is password-protected).");
  if (r.status === 429) throw new Error("That store is limiting requests. Wait a minute and try again.");
  if (!r.ok) throw new Error(`That store answered with an error (${r.status}).`);
  try { return await r.json(); } catch { throw new Error("That address isn't a Shopify store."); }
}
function summary(p, host) {
  const prices = (p.variants || []).map((v) => +v.price || 0);
  const imgs = p.images || [];
  return { id: p.id, handle: p.handle, title: p.title, image: imgs[0]?.src || null, images: imgs.length,
    variants: (p.variants || []).length, price_min: prices.length ? Math.min(...prices) : 0,
    price_max: prices.length ? Math.max(...prices) : 0, url: `https://${host}/products/${p.handle}` };
}
function sourceBase(u) {
  return u.kind === "collection" ? `https://${u.host}/collections/${u.handle}/products.json` : `https://${u.host}/products.json`;
}
async function loadUrl(url, button) {
  const old = button.textContent;
  button.disabled = true;
  try {
    const u = parseUrl(url);
    button.textContent = "Loading…";
    let currency = null;
    try { currency = ((await getJson(`https://${u.host}/meta.json`)).currency || "").toUpperCase() || null; } catch { /* optional */ }
    let raw = [], more = false;
    if (u.kind === "product") {
      raw = [(await getJson(`https://${u.host}/products/${u.handle}.json`)).product];
    } else {
      raw = (await getJson(`${sourceBase(u)}?limit=250&page=1`)).products || [];
      more = raw.length === 250;
    }
    raw = raw.filter((p) => p && p.id && p.title);
    if (!raw.length) throw new Error("No products found there.");
    return { host: u.host, kind: u.kind, src: u, currency, nextPage: 2, more,
      raw: new Map(raw.map((p) => [String(p.id), p])), products: raw.map((p) => summary(p, u.host)) };
  } catch (e) { toast(e.message, true); return null; }
  finally { button.disabled = false; button.textContent = old; }
}
async function loadNextPage() {
  if (!current?.more) return 0;
  const batch = ((await getJson(`${sourceBase(current.src)}?limit=250&page=${current.nextPage}`)).products || [])
    .filter((p) => p && p.id && p.title);
  current.nextPage += 1;
  current.more = batch.length === 250;
  for (const p of batch) {
    if (!current.raw.has(String(p.id))) { current.raw.set(String(p.id), p); current.products.push(summary(p, current.host)); }
  }
  return batch.length;
}

// ------------------------------------------------ collection picker (shared)
let collectionTitle = "";
async function renderCollectionPickers() {
  const st = chosenStores();
  let list = [];
  if (st.length) {
    try { list = (await api(`/api/importer/collections?stores=${st.join(",")}`)).collections; } catch { /* keep empty */ }
  }
  if (collectionTitle && !list.some((c) => c.title === collectionTitle)) list.unshift({ title: collectionTitle, stores: [], isNew: true });
  for (const box of document.querySelectorAll("[data-collection]")) {
    box.innerHTML = `<select class="control imp-coll${collectionTitle ? " on" : ""}" title="Add the imported products to a collection">
      <option value="">No collection</option>
      ${list.map((c) => `<option value="${esc(c.title)}"${c.title === collectionTitle ? " selected" : ""}>${esc(c.title)}${c.isNew ? " (new)" : c.stores.length < st.length ? ` (in ${c.stores.length} of ${st.length} stores, created in the rest)` : ""}</option>`).join("")}
      <option value="__new">+ New collection…</option></select>`;
    box.querySelector("select").onchange = (e) => {
      if (e.target.value === "__new") {
        const name = (prompt("Name of the new collection (created as a manual collection in each chosen store)") || "").trim();
        collectionTitle = name || collectionTitle;
      } else collectionTitle = e.target.value;
      renderCollectionPickers();
    };
  }
}

function productCard(p, cur, check = true) {
  return `<label class="imp-prod${picked.has(String(p.id)) ? " on" : ""}">
    ${check ? `<input type="checkbox" data-id="${p.id}"${picked.has(String(p.id)) ? " checked" : ""}>` : ""}
    <span class="imp-img">${p.image ? `<img src="${esc(thumb(p.image, 300))}" loading="lazy" alt="">` : ""}</span>
    <span class="imp-title">${esc(p.title)}</span>
    <span class="imp-meta">${money(p.price_min, cur)}${p.price_max > p.price_min ? ` – ${money(p.price_max, cur)}` : ""}
      <span class="hint">· ${p.variants} variant${p.variants === 1 ? "" : "s"} · ${p.images} image${p.images === 1 ? "" : "s"}</span></span>
    <span class="imp-new">yours: ${money(previewPrice(p.price_min), cur)} <span class="hint">before currency conversion</span></span>
    <a href="${esc(p.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">view source ↗</a>
  </label>`;
}

$("oneLoad").onclick = async () => {
  const d = await loadUrl($("oneUrl").value, $("oneLoad"));
  if (!d) return;
  current = d;
  picked = new Set(d.products.map((p) => String(p.id)));
  const p = d.products[0];
  $("onePreview").innerHTML = `<div class="imp-one">${productCard(p, d.currency, false)}
    <div class="imp-actions"><p class="hint">From <b>${esc(d.host)}</b>${d.currency ? ` · prices in ${esc(d.currency)}` : ""}</p>
    <button class="btn btn-primary" id="oneImport">Import product</button></div></div>`;
  $("oneImport").onclick = () => startImport([String(p.id)]);
};

const PER_PAGE = 50;
let multiPage = 1;
function filtered() {
  const q = ($("multiFilter")?.value || "").toLowerCase();
  return current.products.filter((p) => !q || p.title.toLowerCase().includes(q));
}
function renderMulti() {
  const d = current, list = filtered();
  const pages = Math.max(1, Math.ceil(list.length / PER_PAGE));
  multiPage = Math.min(multiPage, pages);
  const shown = list.slice((multiPage - 1) * PER_PAGE, multiPage * PER_PAGE);
  $("multiGrid").innerHTML = shown.map((p) => productCard(p, d.currency)).join("") || `<p class="hint">No products match.</p>`;
  for (const cb of $("multiGrid").querySelectorAll("[data-id]")) cb.onchange = () => {
    cb.checked ? picked.add(cb.dataset.id) : picked.delete(cb.dataset.id);
    cb.closest(".imp-prod").classList.toggle("on", cb.checked);
    $("multiCount").textContent = `${picked.size} selected`;
  };
  $("multiCount").textContent = `${picked.size} selected`;
  $("multiLoaded").innerHTML = `<b>${d.products.length.toLocaleString()}</b>${d.more ? "+" : ""} products from <b>${esc(d.host)}</b>${d.currency ? ` · ${esc(d.currency)}` : ""}`;
  $("multiPager").innerHTML = `<button class="btn btn-sm btn-ghost" id="mPrev"${multiPage <= 1 ? " disabled" : ""}>‹ Prev</button>
    <span class="hint">Page ${multiPage} of ${pages}${d.more ? "+" : ""}</span>
    <button class="btn btn-sm btn-ghost" id="mNext"${multiPage >= pages ? " disabled" : ""}>Next ›</button>
    ${d.more ? `<button class="btn btn-sm btn-ghost" id="mMore">Load 250 more</button>` : ""}`;
  $("mPrev").onclick = () => { multiPage--; renderMulti(); $("multiList").scrollIntoView({ behavior: "smooth" }); };
  $("mNext").onclick = () => { multiPage++; renderMulti(); $("multiList").scrollIntoView({ behavior: "smooth" }); };
  if ($("mMore")) $("mMore").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Loading…";
    const before = current.products.length;
    try { await loadNextPage(); multiPage = Math.floor(before / PER_PAGE) + 1; $("multiFilter").value = ""; }
    catch (err) { toast(err.message, true); }
    renderMulti();
  };
}

$("multiLoad").onclick = async () => {
  const d = await loadUrl($("multiUrl").value, $("multiLoad"));
  if (!d) return;
  current = d;
  picked = new Set();
  multiPage = 1;
  $("multiList").innerHTML = `<div class="imp-listbar">
      <span id="multiLoaded"></span>
      <input id="multiFilter" class="control" placeholder="Filter loaded products by title">
      <button class="btn btn-sm btn-ghost" id="selPage">Select page</button>
      <button class="btn btn-sm btn-ghost" id="selAll">Select all loaded</button>
      <button class="btn btn-sm btn-ghost" id="selNone">Clear</button>
      <span class="hint" id="multiCount"></span>
      <button class="btn btn-ghost" id="importAll">Import all products</button>
      <button class="btn btn-primary" id="multiImport">Import selected</button></div>
    <div class="imp-grid" id="multiGrid"></div>
    <div class="scm-pager imp-pager" id="multiPager"></div>`;
  $("multiFilter").oninput = () => { multiPage = 1; renderMulti(); };
  $("selPage").onclick = () => {
    for (const p of filtered().slice((multiPage - 1) * PER_PAGE, multiPage * PER_PAGE)) picked.add(String(p.id));
    renderMulti();
  };
  $("selAll").onclick = () => { for (const p of filtered()) picked.add(String(p.id)); renderMulti(); };
  $("selNone").onclick = () => { picked.clear(); renderMulti(); };
  $("multiImport").onclick = () => startImport([...picked]);
  $("importAll").onclick = async (e) => {
    const b = e.target;
    b.disabled = true;
    try {
      while (current.more) { b.textContent = `Loading all… ${current.products.length}`; await loadNextPage(); }
    } catch (err) { toast(err.message, true); }
    b.disabled = false; b.textContent = "Import all products";
    renderMulti();
    startImport(current.products.map((p) => String(p.id)));
  };
  renderMulti();
};

// ------------------------------------------------ import + progress
let jobs = [];
async function startImport(ids) {
  const st = chosenStores();
  if (!st.length) { toast("Pick at least one store in “Import into”.", true); return; }
  if (!ids.length) { toast("Pick at least one product.", true); return; }
  const names = stores.filter((s) => st.includes(s.id)).map((s) => s.name).join(", ");
  if (!confirm(`Import ${ids.length.toLocaleString()} product${ids.length === 1 ? "" : "s"} into ${names}?` +
    `${collectionTitle ? `\nAdd them to the collection “${collectionTitle}”.` : ""}` +
    `\nThey'll be created as ${cfg.active ? "ACTIVE (visible to customers)" : "Draft"}.`)) return;
  jobs = [];
  try {
    for (let i = 0; i < ids.length; i += 500) {
      const r = await send("/api/importer/import", { host: current.host, currency: current.currency, stores: st,
        collection: collectionTitle, products: ids.slice(i, i + 500).map((id) => current.raw.get(String(id))).filter(Boolean) });
      jobs.push(r.job);
    }
    $("progress").hidden = false;
    $("progress").scrollIntoView({ behavior: "smooth" });
    poll();
  } catch (e) { toast(e.message, true); }
}

const STATUS = { queued: "Waiting", done: "Imported", skipped: "Skipped", failed: "Failed" };
function itemRows(items) {
  return `<thead><tr><th>Product</th><th>Store</th><th>Status</th><th></th></tr></thead><tbody>${items.map((i) => `<tr>
    <td class="name"><span class="imp-mini">${i.image ? `<img src="${esc(thumb(i.image, 80))}" alt="">` : ""}</span>${esc(i.title)}
      <span class="sub"><a href="${esc(i.source_url)}" target="_blank" rel="noopener">${esc((i.source_url || "").replace(/^https?:\/\//, "").slice(0, 60))}</a></span></td>
    <td>${esc(i.store)}</td>
    <td><span class="imp-st st-${i.status}">${STATUS[i.status] || i.status}</span>${i.error ? `<span class="sub">${esc(i.error)}</span>` : ""}</td>
    <td>${i.admin_url ? `<a class="btn btn-sm btn-ghost" href="${esc(i.admin_url)}" target="_blank" rel="noopener">Open in Shopify</a>` : ""}</td></tr>`).join("")}</tbody>`;
}

async function poll() {
  clearTimeout(pollTimer);
  let items = [], running = false;
  try {
    for (const j of jobs) {
      const d = await api(`/api/importer/jobs/${j}`);
      items = items.concat(d.items);
      running = running || d.running;
    }
  } catch { pollTimer = setTimeout(poll, 3000); return; }
  const done = items.filter((i) => i.status !== "queued").length;
  const ok = items.filter((i) => i.status === "done").length;
  const bad = items.filter((i) => i.status === "failed").length;
  const skip = items.filter((i) => i.status === "skipped").length;
  $("progTitle").textContent = running ? `Importing… ${done.toLocaleString()} of ${items.length.toLocaleString()}` : "Import finished";
  $("progNote").textContent = `${ok} imported · ${skip} skipped · ${bad} failed`;
  $("progBar").style.width = `${items.length ? (done / items.length) * 100 : 0}%`;
  const recent = items.filter((i) => i.status !== "queued").slice(-100).reverse();
  $("progTable").innerHTML = itemRows(running ? recent : items.slice(0, 500));
  if (running) pollTimer = setTimeout(poll, 2500);
  else toast(bad ? `Done, ${bad} failed. See the reasons below.` : "All done.", !!bad);
}

// ------------------------------------------------ history
async function loadHistory(page = 1) {
  const d = await api(`/api/importer/history?page=${page}`);
  $("histNote").textContent = `${d.total.toLocaleString()} imports`;
  $("histTable").innerHTML = d.items.length ? itemRows(d.items) : `<tbody><tr><td class="hint">Nothing imported yet.</td></tr></tbody>`;
  $("histPager").innerHTML = d.pages > 1 ? `<button class="btn btn-sm btn-ghost" id="hPrev"${page <= 1 ? " disabled" : ""}>‹</button>
    <span class="hint">Page ${page} of ${d.pages}</span><button class="btn btn-sm btn-ghost" id="hNext"${page >= d.pages ? " disabled" : ""}>›</button>` : "";
  if ($("hPrev")) { $("hPrev").onclick = () => loadHistory(page - 1); $("hNext").onclick = () => loadHistory(page + 1); }
}

// ------------------------------------------------ settings
const SETTINGS = [
  ["Products", [
    ["active", "check", "Make imported products Active", "Off: they arrive as Draft, so you can check them (and swap images) before customers see them."],
    ["unique", "check", "Import only new products", "Skips a product if one with the same handle is already in that store."],
    ["single_variant", "check", "Import without variants", "Creates one variant using the first price."],
    ["vendor", "check", "Keep the source vendor name", ""],
    ["tags", "check", "Keep the source tags", ""],
    ["custom_tags", "tags", "Add these tags to every imported product", "Comma separated, e.g. imported, summer-26"],
    ["product_type", "text", "Product type", "Leave empty to keep the source's"],
    ["taxable", "check", "Charge tax on imported variants", ""],
    ["track_inventory", "check", "Track inventory", "Off (dropshipping): stock isn't tracked and the product keeps selling."],
  ]],
  ["SKU and barcode", [
    ["sku", "check", "Import SKU if it exists", ""], ["random_sku", "check", "Make a random SKU when there isn't one", ""],
    ["barcode", "check", "Import barcode if it exists", ""], ["random_barcode", "check", "Make a random barcode when there isn't one", ""],
  ]],
  ["Description", [
    ["strip_links", "check", "Remove links from the description", "The text stays, only the link goes."],
    ["strip_alt", "check", "Remove ALT text from description images", ""],
    ["source_metafield", "check", "Save the source URL on the product", "Stored in the metafield profitdesk.source_url."],
  ]],
  ["Currency", [
    ["convert_currency", "check", "Convert prices into each store's currency", "At today's rate: a USD source goes into ZAVA in AUD, Vizo in EUR, Tendance in CAD."],
  ]],
  ["Price", [
    ["price_enabled", "check", "Edit prices", ""],
    ["price_rule", "price", "Change the price", ""],
    ["price_whole_end", "text", "Whole number ends with", "e.g. 9 makes 37.40 → 39.40. Empty: unchanged."],
    ["price_cents", "text", "Cents", "e.g. 95 makes 39.40 → 39.95, 99 → 39.99. Empty: unchanged."],
  ]],
  ["Compare-at price", [
    ["compare_mode", "select", "Compare-at price", "", [["source", "Use the source's (adjusted below)"], ["multiply", "Price × multiplier"], ["none", "No compare-at price"]]],
    ["compare_pct", "number", "Increase the source's compare-at by %", ""],
    ["compare_multiplier", "number", "Multiplier", "e.g. 1.5 → a 39.95 price shows 59.95 crossed out (after rounding)."],
    ["compare_whole_end", "text", "Whole number ends with", ""],
    ["compare_cents", "text", "Cents", ""],
  ]],
];

function renderSettings() {
  $("settingsForm").innerHTML = SETTINGS.map(([group, fields]) => `<fieldset><legend>${group}</legend>${fields.map(([k, type, label, help, opts]) => {
    if (type === "check") return `<label class="imp-check"><input type="checkbox" data-k="${k}"${cfg[k] ? " checked" : ""}><span>${label}${help ? `<small>${help}</small>` : ""}</span></label>`;
    if (type === "price") return `<div class="imp-field"><span>${label}</span><div class="imp-inline">
        <select class="control" data-k="price_mode"><option value="increase"${cfg.price_mode !== "decrease" ? " selected" : ""}>Increase</option><option value="decrease"${cfg.price_mode === "decrease" ? " selected" : ""}>Decrease</option></select>
        by <input class="control" type="number" step="0.1" data-k="price_pct" value="${esc(cfg.price_pct)}"> %
        and <input class="control" type="number" step="0.01" data-k="price_fixed" value="${esc(cfg.price_fixed)}"> fixed</div>
        <small>Example: source 20.00 → yours ${previewPrice(20).toFixed(2)} (before currency conversion)</small></div>`;
    if (type === "select") return `<label class="imp-field"><span>${label}</span><select class="control" data-k="${k}">${opts.map(([v, n]) => `<option value="${v}"${cfg[k] === v ? " selected" : ""}>${n}</option>`).join("")}</select></label>`;
    if (type === "tags") return `<label class="imp-field"><span>${label}</span><input class="control" data-k="${k}" value="${esc((cfg[k] || []).join(", "))}"><small>${help}</small></label>`;
    return `<label class="imp-field"><span>${label}</span><input class="control" ${type === "number" ? 'type="number" step="0.01"' : ""} data-k="${k}" value="${esc(cfg[k] ?? "")}">${help ? `<small>${help}</small>` : ""}</label>`;
  }).join("")}</fieldset>`).join("");
}

function readSettings() {
  const out = {};
  for (const el of $("settingsForm").querySelectorAll("[data-k]")) {
    const k = el.dataset.k;
    if (el.type === "checkbox") out[k] = el.checked;
    else if (k === "custom_tags") out[k] = el.value.split(",").map((t) => t.trim()).filter(Boolean);
    else if (el.type === "number") out[k] = el.value === "" ? 0 : +el.value;
    else out[k] = el.value;
  }
  return out;
}
$("settingsForm").addEventListener("input", () => { cfg = { ...cfg, ...readSettings() }; });
$("saveSettings").onclick = async () => {
  try { const r = await send("/api/importer/settings", readSettings(), "PUT"); cfg = r.settings; renderSettings(); toast("Settings saved."); }
  catch (e) { toast(e.message, true); }
};

// ------------------------------------------------ pages
for (const b of $("pages").querySelectorAll("[data-page]")) b.onclick = () => {
  for (const x of $("pages").querySelectorAll("[data-page]")) x.classList.toggle("on", x === b);
  for (const id of ["one", "multi", "history", "settings"]) $(`page-${id}`).hidden = b.dataset.page !== id;
  if (b.dataset.page === "history") loadHistory().catch((e) => toast(e.message, true));
};

(async function boot() {
  try {
    const d = await api("/api/importer/settings");
    cfg = d.settings;
    stores = d.stores;
    renderStorePickers();
    renderSettings();
    renderCollectionPickers();
  } catch (e) { toast(e.message, true); }
})();
