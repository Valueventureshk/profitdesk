/* Product Importer → Ad copy: a Meta ad's primary text / headline / description from a link or a screenshot,
   each improvable with AI. Uses importer.js's $, esc, toast. */
(function () {
  const F = ["primary_text", "headline", "description"];
  const LIMIT = { primary_text: 125, headline: 40, description: 30 };   // what shows before Meta cuts it off
  let original = null;

  async function call(url, body, form) {
    const r = await fetch(url, form ? { method: "POST", body } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let d = {};
    try { d = await r.json(); } catch { /* empty */ }
    if (!r.ok) throw new Error(d.detail || d.error || `Something went wrong (${r.status}).`);
    return d;
  }

  function counts() {
    for (const f of F) {
      const n = $(`ac_${f}`).value.length;
      const el = document.querySelector(`.ac-field[data-f="${f}"] .ac-count`);
      el.textContent = `${n} characters${n > LIMIT[f] ? ` · Meta shows about ${LIMIT[f]} before “See more” / cutting it` : ""}`;
      el.classList.toggle("over", n > LIMIT[f]);
    }
  }

  function show(d, keepOriginal) {
    for (const f of F) $(`ac_${f}`).value = d[f] || "";
    if (!keepOriginal) original = { ...d };
    $("acFrom").textContent = d.source ? `Read from ${d.source === "screenshot" ? "the screenshot" : d.source === "ad library" ? "Meta's Ad Library" : "the ad's preview"}`
      + (d.page_name ? ` · advertiser: ${d.page_name}` : "") : "";
    $("acOut").hidden = false;
    $("acOriginal").hidden = !keepOriginal;
    counts();
    loadRecent();
  }

  async function busy(btn, label, fn) {
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    try { await fn(); } catch (e) { toast(e.message, true); }
    btn.disabled = false;
    btn.textContent = old;
  }

  $("acRead").onclick = () => busy($("acRead"), "Reading…", async () => {
    const url = $("acUrl").value.trim();
    if (!url) throw new Error("Paste the ad's link first.");
    $("acHelp").hidden = true;
    try {
      show(await call("/api/importer/adcopy/read", { url }));
    } catch (e) {
      // Meta often won't share an ad's text: open the ad so it can be screenshotted and pasted here
      $("acHelp").hidden = false;
      $("acOpen").href = /^https?:\/\//.test(url) ? url : `https://www.facebook.com/ads/library/?id=${encodeURIComponent(url)}`;
      throw e;
    }
  });
  $("acUrl").onkeydown = (e) => { if (e.key === "Enter") $("acRead").click(); };

  async function readImage(file) {
    if (!file || !file.type.startsWith("image/")) { toast("That isn't a picture.", true); return; }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("url", $("acUrl").value.trim());
    const drop = $("acDrop");
    drop.classList.add("busy");
    drop.querySelector("b").textContent = "Reading the screenshot…";
    try { show(await call("/api/importer/adcopy/image", fd, true)); } catch (e) { toast(e.message, true); }
    drop.classList.remove("busy");
    drop.querySelector("b").textContent = "Or paste (Ctrl/Cmd + V) or drop a screenshot of the ad here";
  }
  $("acFile").onchange = (e) => { readImage(e.target.files[0]); e.target.value = ""; };
  $("acDrop").addEventListener("dragover", (e) => { e.preventDefault(); $("acDrop").classList.add("over"); });
  $("acDrop").addEventListener("dragleave", () => $("acDrop").classList.remove("over"));
  $("acDrop").addEventListener("drop", (e) => { e.preventDefault(); $("acDrop").classList.remove("over"); readImage(e.dataTransfer.files[0]); });
  document.addEventListener("paste", (e) => {
    if ($("page-adcopy").hidden) return;
    const item = [...(e.clipboardData || {}).items || []].find((i) => i.type.startsWith("image/"));
    if (item) { e.preventDefault(); readImage(item.getAsFile()); }
  });

  document.addEventListener("click", (e) => {
    const b = e.target.closest("#page-adcopy [data-improve], #page-adcopy [data-copy]");
    if (!b) return;
    if (b.dataset.copy) {
      navigator.clipboard.writeText($(`ac_${b.dataset.copy}`).value).then(() => toast("Copied"));
      return;
    }
    busy(b, "Improving…", async () => {
      const body = { which: b.dataset.improve, note: $("acNote").value.trim() };
      for (const f of F) body[f] = $(`ac_${f}`).value;
      const d = await call("/api/importer/adcopy/improve", body);
      for (const f of F) $(`ac_${f}`).value = d[f] ?? $(`ac_${f}`).value;
      $("acNotes").textContent = d.notes ? `AI: ${d.notes}` : "";
      $("acOriginal").hidden = !original;
      counts();
    });
  });
  for (const f of F) $(`ac_${f}`).addEventListener("input", counts);
  $("acOriginal").onclick = () => { if (original) { for (const f of F) $(`ac_${f}`).value = original[f] || ""; $("acNotes").textContent = ""; counts(); } };
  $("acCopyAll").onclick = () => {
    const t = `Primary text:\n${$("ac_primary_text").value}\n\nHeadline:\n${$("ac_headline").value}\n\nDescription:\n${$("ac_description").value}`;
    navigator.clipboard.writeText(t).then(() => toast("Copied all 3"));
  };

  async function loadRecent() {
    try {
      const r = await (await fetch("/api/importer/adcopy/recent")).json();
      $("acRecent").innerHTML = (r.items || []).map((i) => `<button class="ac-recent-row" data-recent='${esc(JSON.stringify(i))}'>
        <b>${esc(i.headline || i.primary_text.slice(0, 60) || "(no text)")}</b><span>${esc(i.page_name || "")} · ${esc(i.source || "")} · ${esc((i.by || "").split("@")[0])}</span></button>`).join("")
        || '<p class="hint">Nothing yet.</p>';
    } catch { /* fine */ }
  }
  document.addEventListener("click", (e) => {
    const r = e.target.closest("[data-recent]");
    if (r) show(JSON.parse(r.dataset.recent));
  });
  loadRecent();
})();
