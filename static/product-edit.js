/* ProfitDesk Product Importer: edit an imported product without opening Shopify.
   Title, description (with AI rewrite), images, prices, size chart, tags, and Activate.
   Uses importer.js helpers ($, api, send, esc, toast, thumb, money). */

let pe = null;            // { store, product, charts, size_chart_id, admin_url }
let peDirty = false;
let peSuggestion = null;

const PE_LANGS = ["English", "French", "Spanish", "German", "Italian", "Dutch"];

let peWasOpen = [];
function peShow() {
  const secs = [...document.querySelectorAll("main.content > section")].filter((x) => x.id !== "page-edit");
  peWasOpen = secs.filter((x) => !x.hidden);
  secs.forEach((x) => { x.hidden = true; });
  $("page-edit").hidden = false;
  $("pages").hidden = true;
  window.scrollTo(0, 0);
}

async function openProductEditor(storeId, productId) {
  if ($("page-edit").hidden) peShow();
  $("page-edit").innerHTML = `<div class="panel pe-loading">Loading the product from Shopify…</div>`;
  try {
    pe = await api(`/api/importer/product?store=${storeId}&id=${encodeURIComponent(productId)}`);
    peDirty = false; peSuggestion = null;
    peRender();
  } catch (e) {
    $("page-edit").innerHTML = `<div class="panel pe-loading">${esc(e.message)} <button class="btn btn-ghost" id="peBack0">← Back</button></div>`;
    $("peBack0").onclick = peClose;
  }
}

function peClose() {
  if (peDirty && !confirm("You have changes that aren't saved. Leave without saving?")) return;
  peDirty = false;
  $("page-edit").hidden = true;
  $("pages").hidden = false;
  peWasOpen.forEach((x) => { x.hidden = false; });
  if (!$("page-history").hidden) loadHistory().catch(() => {});
}
window.addEventListener("beforeunload", (e) => { if (peDirty) { e.preventDefault(); e.returnValue = ""; } });

function peImages() { return (pe.product.media?.nodes || []).filter((m) => m.mediaContentType === "IMAGE"); }
function peImgUrl(m) { return m.preview?.image?.url || ""; }

function peRender() {
  const p = pe.product, st = pe.store;
  const active = p.status === "ACTIVE";
  const cur = st.currency || "";
  const looksSize = (m) => /size|chart|guide|measure|taille|talla|尺码|尺寸/i.test(peImgUrl(m) + " " + (m.alt || ""));
  $("page-edit").innerHTML = `
  <div class="panel pe-bar">
    <button class="btn btn-ghost" id="peBack">← Back</button>
    <div class="pe-bartitle"><b>${esc(p.title)}</b><span class="hint">${esc(st.name)}</span>
      <span class="imp-st ${active ? "st-done" : ""}">${active ? "Active" : p.status === "DRAFT" ? "Draft" : esc(p.status)}</span></div>
    <span class="pe-barbtns">
      <a class="btn btn-ghost" href="${esc(pe.admin_url)}" target="_blank" rel="noopener">Open in Shopify</a>
      ${p.onlineStoreUrl || p.onlineStorePreviewUrl ? `<a class="btn btn-ghost" href="${esc(p.onlineStoreUrl || p.onlineStorePreviewUrl)}" target="_blank" rel="noopener">${active ? "View in store" : "Preview"}</a>` : ""}
      <button class="btn btn-ghost" id="peSave">Save</button>
      ${active ? `<button class="btn btn-ghost" id="peDraft">Set to draft</button>`
               : `<button class="btn btn-primary pe-activate" id="peActivate">Save &amp; activate in Shopify</button>`}
    </span>
  </div>

  <div class="pe-grid">
    <div class="pe-col">
      <div class="panel pe-card">
        <label class="imp-field"><span>Title</span><input class="control" id="peTitle" value="${esc(p.title)}"></label>
        <div class="imp-field"><span>Description</span>
          <div class="pe-tools">
            <button type="button" data-cmd="bold" title="Bold"><b>B</b></button>
            <button type="button" data-cmd="italic" title="Italic"><i>I</i></button>
            <button type="button" data-cmd="formatBlock" data-arg="H3" title="Heading">H</button>
            <button type="button" data-cmd="formatBlock" data-arg="P" title="Paragraph">¶</button>
            <button type="button" data-cmd="insertUnorderedList" title="Bullet list">• List</button>
            <button type="button" data-cmd="removeFormat" title="Clear formatting">Clear</button>
            <button type="button" id="peHtml" title="Edit the HTML">&lt;/&gt; HTML</button>
          </div>
          <div class="pe-desc" id="peDesc" contenteditable="true">${p.descriptionHtml || ""}</div>
          <textarea class="control pe-htmlbox" id="peDescHtml" hidden></textarea>
        </div>
      </div>

      <div class="panel pe-card">
        <div class="pe-cardhead"><h3>✦ Rewrite with AI</h3><span class="hint">Nothing changes until you press “Use this”.</span></div>
        <div class="pe-row3">
          <label class="imp-field"><span>Language</span><select class="control" id="peLang">${PE_LANGS.map((l) => `<option${l === st.language ? " selected" : ""}>${l}</option>`).join("")}</select></label>
          <label class="imp-field"><span>Tone</span><select class="control" id="peTone"><option value="warm">Warm &amp; elegant</option><option value="premium">Premium</option><option value="short">Short &amp; punchy</option><option value="playful">Playful</option></select></label>
          <label class="imp-field"><span>Title style (optional)</span><input class="control" id="peStyle" placeholder="e.g. Name™ – short description"></label>
        </div>
        <label class="imp-field"><span>Extra instructions (optional)</span><input class="control" id="peInstr" placeholder="e.g. mention it's perfect for weddings, keep it under 120 words"></label>
        <button class="btn btn-ghost" id="peRewrite">✦ Rewrite title &amp; description</button>
        <div id="peSuggest"></div>
      </div>

      <div class="panel pe-card">
        <div class="pe-cardhead"><h3>Images</h3><span class="hint">Image changes save to Shopify straight away.</span></div>
        <div class="pe-imgs" id="peImgs">${peImages().map((m, i) => `
          <figure class="pe-img${i === 0 ? " main" : ""}" data-mid="${esc(m.id)}">
            ${peImgUrl(m) ? `<img src="${esc(thumb(peImgUrl(m), 300))}" alt="">` : `<span class="hint">Processing…</span>`}
            ${i === 0 ? `<span class="pe-mainbadge">Main</span>` : ""}
            <span class="pe-imgbtns">
              ${i > 0 ? `<button data-move="${i - 1}" title="Move left">←</button>` : ""}
              ${i < peImages().length - 1 ? `<button data-move="${i + 1}" title="Move right">→</button>` : ""}
              ${i > 0 ? `<button data-move="0" title="Make main image">★</button>` : ""}
              <button data-del title="Remove">✕</button>
            </span>
          </figure>`).join("") || `<p class="hint">No images yet.</p>`}</div>
        <label class="sc-drop pe-drop" id="peDrop"><input type="file" id="peFiles" accept="image/*" multiple hidden><span>Drop your own images here or click to choose</span></label>
        <div class="pe-urlrow"><input class="control" id="peUrl" placeholder="…or paste an image link"><button class="btn btn-ghost" id="peAddUrl">Add</button></div>
      </div>
    </div>

    <div class="pe-col pe-side">
      <div class="panel pe-card">
        <div class="pe-cardhead"><h3>Prices</h3><span class="hint">${esc(cur)}</span></div>
        <div class="pe-setall">
          <input class="control" type="number" step="0.01" id="peAllPrice" placeholder="Price for all">
          <input class="control" type="number" step="0.01" id="peAllCmp" placeholder="Compare-at for all">
          <button class="btn btn-sm btn-ghost" id="peApplyAll">Apply</button>
        </div>
        <div class="pe-variants">
          <div class="pe-vhead"><span>Variant</span><span>Price</span><span>Compare-at</span></div>
          ${p.variants.nodes.map((v) => `<div class="pe-vrow" data-vid="${esc(v.id)}">
            <span class="pe-vname">${esc(v.title === "Default Title" ? "Price" : v.title)}</span>
            <input class="control" type="number" step="0.01" data-f="price" value="${esc(v.price)}">
            <input class="control" type="number" step="0.01" data-f="compare_at" value="${esc(v.compareAtPrice || "")}">
          </div>`).join("")}
        </div>
      </div>

      <div class="panel pe-card">
        <div class="pe-cardhead"><h3>Size chart</h3><a class="pe-chartlink" id="peChartLink" target="_blank" rel="noopener"></a></div>
        <select class="control" id="peChart">${peChartOptions()}</select>
        <label class="btn btn-ghost pe-scup"><input type="file" id="peScFiles" accept="image/*" multiple hidden>⬆ Upload a size chart image (AI builds it)</label>
        <div id="peScDone"></div>
        ${peImages().length ? `<div class="pe-scai">
          <span class="hint">…or, if one of the product photos is the size chart, tick it:</span>
          <div class="pe-scpick">${peImages().map((m) => peImgUrl(m) ? `<label><input type="checkbox" data-scurl="${esc(peImgUrl(m))}"${looksSize(m) ? " checked" : ""}><img src="${esc(thumb(peImgUrl(m), 120))}" alt=""></label>` : "").join("")}</div>
          <button class="btn btn-sm btn-ghost" id="peScAI">✦ Make size chart from ticked images</button>
        </div>` : ""}
      </div>

      <div class="panel pe-card">
        <div class="pe-cardhead"><h3>Organisation</h3></div>
        <label class="imp-field"><span>Product type</span><input class="control" id="peType" value="${esc(p.productType || "")}"></label>
        <label class="imp-field"><span>Vendor</span><input class="control" id="peVendor" value="${esc(p.vendor || "")}"></label>
        <label class="imp-field"><span>Tags</span><input class="control" id="peTags" value="${esc((p.tags || []).join(", "))}"><small>Comma separated</small></label>
      </div>
    </div>
  </div>`;
  peWire();
}

function peChartOptions() {
  return `<option value="0">No size chart</option>` + pe.charts.map((c) => `<option value="${c.id}"${c.id === pe.size_chart_id ? " selected" : ""}>${esc(c.name)}${c.status === "draft" ? " (draft)" : ""}</option>`).join("");
}
function peChartLink() {
  const id = +$("peChart").value, a = $("peChartLink");
  a.href = id ? `/sizecharts?edit=${id}` : `/sizecharts?new=1&store=${pe.store.id}&product=${encodeURIComponent(pe.product.id)}&title=${encodeURIComponent($("peTitle").value)}`;
  a.textContent = id ? "Edit this chart ↗" : "Create a chart for this product ↗";
}
function peChartMade(r) {
  if (!pe.charts.some((c) => c.id === r.id)) pe.charts.push({ id: r.id, name: r.name, status: "active" });
  pe.size_chart_id = r.id;
  $("peChart").innerHTML = peChartOptions();
  $("peChart").value = r.id;
  peChartLink();
  $("peScDone").innerHTML = `<div class="pe-scdone">✓ “${esc(r.name)}” made and assigned to this product. <a href="/sizecharts?edit=${r.id}" target="_blank" rel="noopener">Review &amp; edit in Size Charts ↗</a></div>`;
}
// Back from the Size Charts tab: show the chart now assigned there (unless the box was changed here)
window.addEventListener("focus", async () => {
  if (!pe || $("page-edit").hidden || $("peChart")?.dataset.touched) return;
  try {
    const d = await api(`/api/importer/product/chart?store=${pe.store.id}&id=${encodeURIComponent(pe.product.id)}`);
    pe.charts = d.charts; pe.size_chart_id = d.size_chart_id;
    $("peChart").innerHTML = peChartOptions();
    peChartLink();
  } catch { /* keep what's shown */ }
});

function peCollect() {
  if (!$("peDescHtml").hidden) $("peDesc").innerHTML = $("peDescHtml").value;
  return {
    store: pe.store.id, id: pe.product.id,
    title: $("peTitle").value.trim(),
    description_html: $("peDesc").innerHTML,
    product_type: $("peType").value.trim(),
    vendor: $("peVendor").value.trim(),
    tags: $("peTags").value.split(",").map((t) => t.trim()).filter(Boolean),
    variants: [...document.querySelectorAll(".pe-vrow")].map((r) => ({
      id: r.dataset.vid, title: r.querySelector(".pe-vname").textContent,
      price: r.querySelector('[data-f="price"]').value, compare_at: r.querySelector('[data-f="compare_at"]').value })),
    size_chart_id: +$("peChart").value,
  };
}

async function peSave(status) {
  const body = peCollect();
  if (status) body.status = status;
  const btns = [...document.querySelectorAll(".pe-barbtns button")];
  btns.forEach((b) => { b.disabled = true; });
  try {
    pe = await send("/api/importer/product", body, "PUT");
    peDirty = false;
    peRender();
    toast(status === "ACTIVE" ? "Saved and live in Shopify." : status === "DRAFT" ? "Saved as draft." : "Saved to Shopify.");
  } catch (e) { toast(e.message, true); btns.forEach((b) => { b.disabled = false; }); }
}

async function peReload(msg) {
  const keep = peDirty ? peCollect() : null;
  pe = await api(`/api/importer/product?store=${pe.store.id}&id=${encodeURIComponent(pe.product.id)}`);
  if (keep) Object.assign(pe.product, { title: keep.title, descriptionHtml: keep.description_html });
  peRender();
  if (keep) peDirty = true;
  if (msg) toast(msg);
}

function peWire() {
  const mark = () => { peDirty = true; };
  $("peBack").onclick = peClose;
  $("peSave").onclick = () => peSave();
  if ($("peActivate")) $("peActivate").onclick = () => peSave("ACTIVE");
  if ($("peDraft")) $("peDraft").onclick = () => peSave("DRAFT");
  $("peChart").onchange = () => { $("peChart").dataset.touched = "1"; peChartLink(); mark(); };
  peChartLink();
  $("peScFiles").onchange = async (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    const lab = $("peScFiles").parentElement;
    lab.classList.add("busy"); lab.lastChild.textContent = "Reading the chart… (about 10 seconds)";
    const fd = new FormData();
    fd.append("store", pe.store.id); fd.append("id", pe.product.id); fd.append("name", $("peTitle").value);
    files.forEach((f) => fd.append("files", f));
    try { peChartMade(await api("/api/importer/product/size-chart-ai", { method: "POST", body: fd })); }
    catch (err) { toast(err.message, true); }
    finally { lab.classList.remove("busy"); lab.lastChild.textContent = "⬆ Upload a size chart image (AI builds it)"; e.target.value = ""; }
  };

  // description toolbar
  for (const b of document.querySelectorAll(".pe-tools [data-cmd]")) b.onclick = () => {
    $("peDesc").focus();
    document.execCommand(b.dataset.cmd, false, b.dataset.arg || null);
    mark();
  };
  $("peHtml").onclick = () => {
    const box = $("peDescHtml"), ed = $("peDesc");
    if (box.hidden) { box.value = ed.innerHTML; box.hidden = false; ed.hidden = true; $("peHtml").classList.add("on"); }
    else { ed.innerHTML = box.value; box.hidden = true; ed.hidden = false; $("peHtml").classList.remove("on"); }
  };

  // prices: fill every row
  $("peApplyAll").onclick = () => {
    const pr = $("peAllPrice").value, cp = $("peAllCmp").value;
    for (const r of document.querySelectorAll(".pe-vrow")) {
      if (pr !== "") r.querySelector('[data-f="price"]').value = pr;
      if (cp !== "") r.querySelector('[data-f="compare_at"]').value = cp;
    }
    if (pr !== "" || cp !== "") mark();
  };

  // AI rewrite
  $("peRewrite").onclick = async () => {
    const b = $("peRewrite");
    b.disabled = true; b.textContent = "Writing…";
    try {
      const c = peCollect();
      peSuggestion = await send("/api/importer/product/rewrite", {
        title: c.title, description_html: c.description_html, product_type: c.product_type,
        options: (pe.product.options || []).map((o) => `${o.name}: ${o.values.join(", ")}`).join("; "),
        language: $("peLang").value, tone: $("peTone").value, title_style: $("peStyle").value, instructions: $("peInstr").value });
      $("peSuggest").innerHTML = `<div class="pe-suggest"><span class="hint">Suggestion</span>
        <b class="pe-sgtitle">${esc(peSuggestion.title)}</b><div class="pe-sgdesc">${peSuggestion.description_html}</div>
        <div class="pe-sgbtns"><button class="btn btn-sm btn-primary" id="peUse">Use this</button><button class="btn btn-sm btn-ghost" id="peUseTitle">Use title only</button><button class="btn btn-sm btn-ghost" id="peNo">Discard</button></div></div>`;
      $("peUse").onclick = () => { $("peTitle").value = peSuggestion.title; $("peDesc").innerHTML = peSuggestion.description_html; $("peDescHtml").value = peSuggestion.description_html; mark(); $("peSuggest").innerHTML = ""; toast("Applied. Press Save to send it to Shopify."); };
      $("peUseTitle").onclick = () => { $("peTitle").value = peSuggestion.title; mark(); toast("Title applied. Press Save to send it to Shopify."); };
      $("peNo").onclick = () => { $("peSuggest").innerHTML = ""; };
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "✦ Rewrite again"; }
  };

  // images
  $("peImgs").onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const mid = btn.closest("[data-mid]").dataset.mid;
    try {
      if (btn.hasAttribute("data-del")) {
        if (!confirm("Remove this image from the product?")) return;
        await send("/api/importer/product/media/remove", { store: pe.store.id, id: pe.product.id, media_ids: [mid] });
        await peReload("Image removed.");
      } else {
        await send("/api/importer/product/media/move", { store: pe.store.id, id: pe.product.id, media_id: mid, position: +btn.dataset.move });
        setTimeout(() => peReload(btn.dataset.move === "0" ? "Main image changed." : "Moved."), 900);
      }
    } catch (err) { toast(err.message, true); }
  };
  const upload = async (files, urls) => {
    const fd = new FormData();
    fd.append("store", pe.store.id); fd.append("id", pe.product.id);
    for (const f of files || []) fd.append("files", f);
    if (urls) fd.append("urls", urls);
    toast("Uploading to Shopify…");
    try {
      const r = await api("/api/importer/product/media", { method: "POST", body: fd });
      setTimeout(() => peReload(`${r.added} image${r.added === 1 ? "" : "s"} added.`), 2500);
    } catch (err) { toast(err.message, true); }
  };
  $("peFiles").onchange = (e) => upload(e.target.files);
  $("peDrop").ondragover = (e) => { e.preventDefault(); $("peDrop").classList.add("on"); };
  $("peDrop").ondragleave = () => $("peDrop").classList.remove("on");
  $("peDrop").ondrop = (e) => { e.preventDefault(); $("peDrop").classList.remove("on"); upload([...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"))); };
  $("peAddUrl").onclick = () => { const u = $("peUrl").value.trim(); if (u) upload([], u); };

  // size chart from images
  if ($("peScAI")) $("peScAI").onclick = async () => {
    const urls = [...document.querySelectorAll("[data-scurl]:checked")].map((x) => x.dataset.scurl);
    if (!urls.length) return toast("Tick the image(s) that show the size chart.", true);
    const b = $("peScAI");
    b.disabled = true; b.textContent = "Reading the chart…";
    try {
      peChartMade(await send("/api/importer/product/size-chart-ai", { store: pe.store.id, id: pe.product.id, urls, name: $("peTitle").value }));
    } catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "✦ Make size chart from ticked images"; }
  };
}

$("page-edit").addEventListener("input", (e) => {
  if (pe && !e.target.closest(".pe-scpick, #peUrl, .pe-setall, .pe-row3, #peInstr, #peFiles")) peDirty = true;
});

// "Edit" buttons in the import progress and History tables
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-edit-store]");
  if (!b) return;
  e.preventDefault();
  openProductEditor(b.dataset.editStore, b.dataset.editId);
});
