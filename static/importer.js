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
    };
    for (const cb of box.querySelectorAll("input[value]")) cb.onchange = () => {
      const now = [...box.querySelectorAll("input[value]:checked")].map((i) => +i.value);
      try { localStorage.setItem("impStores", JSON.stringify(now)); } catch { /* */ }
      renderStorePickers();
      box.querySelector("details").open = true;
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
async function loadUrl(url, button) {
  const old = button.textContent;
  button.disabled = true;
  try {
    const u = parseUrl(url);
    button.textContent = "Loading…";
    let currency = null;
    try { currency = ((await getJson(`https://${u.host}/meta.json`)).currency || "").toUpperCase() || null; } catch { /* optional */ }
    let raw = [];
    if (u.kind === "product") {
      raw = [(await getJson(`https://${u.host}/products/${u.handle}.json`)).product];
    } else {
      const base = u.kind === "collection" ? `https://${u.host}/collections/${u.handle}/products.json` : `https://${u.host}/products.json`;
      for (let page = 1; page <= 20; page++) {
        const batch = (await getJson(`${base}?limit=250&page=${page}`)).products || [];
        raw = raw.concat(batch);
        button.textContent = `Loading… ${raw.length}`;
        if (batch.length < 250) break;
      }
    }
    raw = raw.filter((p) => p && p.id && p.title);
    if (!raw.length) throw new Error("No products found there.");
    return { host: u.host, kind: u.kind, currency, raw: new Map(raw.map((p) => [String(p.id), p])),
      products: raw.map((p) => summary(p, u.host)) };
  } catch (e) { toast(e.message, true); return null; }
  finally { button.disabled = false; button.textContent = old; }
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

function renderMulti() {
  const d = current;
  const q = ($("multiFilter")?.value || "").toLowerCase();
  const list = d.products.filter((p) => !q || p.title.toLowerCase().includes(q));
  $("multiGrid").innerHTML = list.map((p) => productCard(p, d.currency)).join("") || `<p class="hint">No products match.</p>`;
  for (const cb of $("multiGrid").querySelectorAll("[data-id]")) cb.onchange = () => {
    cb.checked ? picked.add(cb.dataset.id) : picked.delete(cb.dataset.id);
    cb.closest(".imp-prod").classList.toggle("on", cb.checked);
    $("multiCount").textContent = `${picked.size} selected`;
  };
  $("multiCount").textContent = `${picked.size} selected`;
}

$("multiLoad").onclick = async () => {
  const d = await loadUrl($("multiUrl").value, $("multiLoad"));
  if (!d) return;
  current = d;
  picked = new Set();
  $("multiList").innerHTML = `<div class="imp-listbar">
      <span><b>${d.products.length.toLocaleString()}</b> products from <b>${esc(d.host)}</b>${d.currency ? ` · ${esc(d.currency)}` : ""}</span>
      <input id="multiFilter" class="control" placeholder="Filter by title">
      <button class="btn btn-sm btn-ghost" id="selAll">Select all</button>
      <button class="btn btn-sm btn-ghost" id="selNone">Clear</button>
      <span class="hint" id="multiCount"></span>
      <button class="btn btn-primary" id="multiImport">Import selected</button></div>
    <div class="imp-grid" id="multiGrid"></div>`;
  $("multiFilter").oninput = renderMulti;
  $("selAll").onclick = () => {
    const q = $("multiFilter").value.toLowerCase();
    for (const p of current.products) if (!q || p.title.toLowerCase().includes(q)) picked.add(String(p.id));
    renderMulti();
  };
  $("selNone").onclick = () => { picked.clear(); renderMulti(); };
  $("multiImport").onclick = () => startImport([...picked]);
  renderMulti();
};

// ------------------------------------------------ import + progress
async function startImport(ids) {
  const st = chosenStores();
  if (!st.length) { toast("Pick at least one store in “Import into”.", true); return; }
  if (!ids.length) { toast("Pick at least one product.", true); return; }
  const names = stores.filter((s) => st.includes(s.id)).map((s) => s.name).join(", ");
  if (ids.length > 500) { toast("Import up to 500 products at a time.", true); return; }
  if (!confirm(`Import ${ids.length} product${ids.length === 1 ? "" : "s"} into ${names}?\nThey'll be created as ${cfg.active ? "ACTIVE (visible to customers)" : "Draft"}.`)) return;
  try {
    const r = await send("/api/importer/import", { host: current.host, currency: current.currency, stores: st,
      products: ids.map((id) => current.raw.get(String(id))).filter(Boolean) });
    $("progress").hidden = false;
    $("progress").scrollIntoView({ behavior: "smooth" });
    poll(r.job);
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

async function poll(job) {
  clearTimeout(pollTimer);
  let d;
  try { d = await api(`/api/importer/jobs/${job}`); } catch { pollTimer = setTimeout(() => poll(job), 3000); return; }
  const done = d.items.filter((i) => i.status !== "queued").length;
  const ok = d.items.filter((i) => i.status === "done").length;
  const bad = d.items.filter((i) => i.status === "failed").length;
  const skip = d.items.filter((i) => i.status === "skipped").length;
  $("progTitle").textContent = d.running ? `Importing… ${done} of ${d.items.length}` : `Import finished`;
  $("progNote").textContent = `${ok} imported · ${skip} skipped · ${bad} failed`;
  $("progBar").style.width = `${d.items.length ? (done / d.items.length) * 100 : 0}%`;
  $("progTable").innerHTML = itemRows(d.items);
  if (d.running) pollTimer = setTimeout(() => poll(job), 2500);
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
  } catch (e) { toast(e.message, true); }
})();
