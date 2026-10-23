/* COG → Products: vetted supplier prices (blue), the all-time panel, COG % and suggested selling price with Apply,
   and the price-check sheet. Every figure comes from the server (/api/cog/pdesk); this file only shows them. */
(function () {
  const POLICY = { tiered: "Tiered pricing (same product, less per piece)", per_item: "Per-item discount (different products)" };
  const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
  const post = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  // "Buy 2" for single items; "4 pcs (2 sales)" for a bundle sold in packs
  const pieceName = (k, r) => (r.offer && r.offer.type === "bundle"
    ? `${k} pcs (${k / r.offer.pack} sale${k / r.offer.pack === 1 ? "" : "s"})` : `Buy ${k}`);

  // ---- the panel above the table (always all time for the store picked)
  window.pdkDrawPanel = (p, sid) => {
    const box = $("pdkPanel");
    if (!p) { box.innerHTML = ""; return; }
    const one = sid && sid !== "all";
    const pol = one ? p.policies[sid] : null;
    const over = p.avg_cog_pct !== null && p.avg_cog_pct > p.target;
    box.innerHTML = `
      <div class="pdk-card"><span>Average COG % <em>all time</em></span>
        <b class="${p.avg_cog_pct === null ? "" : over ? "pdk-red" : "pdk-ok"}">${pct(p.avg_cog_pct)}</b>
        <small>target ${p.target}% of the selling price</small></div>
      <button class="pdk-card click" data-show="unvetted"><span>Sold, not checked by us</span>
        <b class="${p.unvetted ? "pdk-warn" : "pdk-ok"}">${p.unvetted}</b>
        <small>${p.unvetted ? `confirm with the supplier (incl. bundle / qty prices)${p.no_price ? ` · ${p.no_price} have no price at all` : ""}` : "every sold product is checked"}</small></button>
      <button class="pdk-card click" data-show="auto"><span>Filled by the app</span><b class="pdk-brown">${p.auto || 0}</b>
        <small>from invoices, until you check them</small></button>
      <button class="pdk-card click" data-show="vetted"><span>Checked by us</span><b class="pdk-blue">${p.vetted}</b>
        <small>confirmed prices</small></button>
      <button class="pdk-card click" data-flagsheet><span>Price checks</span>
        <b class="${p.flags ? "pdk-red" : "pdk-ok"}">${p.flags}</b>
        <small>${p.flags ? "invoices that don't match the vetted price" : "invoices match the vetted prices"}</small></button>
      ${one ? `<div class="pdk-card"><span>Supplier discount</span><b class="pdk-small">${esc(POLICY[pol] || pol)}</b>
        <small>${pol === "per_item" ? `learned from invoices: −${money(p.per_item[sid] || 0, "USD")} per item when products ship together` : "prices per number of pieces, per product"}
        <a href="#" data-policy="${pol === "tiered" ? "per_item" : "tiered"}" data-manage-only>switch</a><br>
        price checks from <b>${esc((p.starts || {})[sid] || "the first invoice")}</b> <a href="#" data-checksfrom data-manage-only>change</a></small></div>` : ""}`;
  };

  // ---- the vetted / COG % / suggested price cells of one product row
  window.pdkCells = (r, i) => {
    const v = r.vetted || {}, s = r.suggested || {};
    const keys = [...new Set([...Object.keys(v), ...Object.keys(s)])].map(Number).sort((a, b) => a - b);
    const has = Object.keys(v).length > 0;
    const first = has ? Math.min(...Object.keys(v).map(Number)) : null;
    const col = r.vetted_source === "team" ? "pdk-blue" : "pdk-brown";
    const vet = has ? `<b class="${col}">${money(v[first], "USD")}</b><span class="sub">${esc(pieceName(first, r))} · from ${esc(r.vetted_from || "")}${r.vetted_source === "auto" ? " · by the app" : ""}</span>`
      : `<span class="hint">no price yet</span>`;
    const dd = keys.length ? `<details class="pdk-dd"><summary>${r.offer && r.offer.type === "bundle" ? `bundle of ${r.offer.pack}` : "Buy 1–4"}</summary>${keys.map((k) =>
      `<span class="sub">${esc(pieceName(k, r))}: ${v[k] ? `<b class="${col}">${money(v[k], "USD")}</b>` : `<span title="from invoices, not vetted">${money(s[k], "USD")} (invoice)</span>`}</span>`).join("")}</details>` : "";
    const cur = r.price_currency;
    const guide = r.recommended
      ? (Math.abs(r.price_change) < 0.5 ? `<span class="sub pdk-ok">on target</span>`
        : `<span class="sub ${r.price_change > 0 ? "pdk-warn" : "pdk-ok"}">${r.price_change > 0 ? "raise" : "lower"} by ${money(Math.abs(r.price_change), cur)}</span>`)
      : "";
    return `<td class="num"><a href="#" data-vet="${i}" title="Set the vetted prices">${vet}</a>${dd}
        ${r.vetted_source === "auto" ? `<a href="#" class="sub" data-confirm="${i}" data-write-only>✓ confirm</a>` : ""}
        <a href="#" class="sub" data-vet="${i}" data-write-only>${has ? "edit / new price" : "set prices"}</a></td>
      <td class="num">${r.cog_pct !== null && r.cog_pct !== undefined ? `<b class="${r.cog_pct > 29 ? "pdk-warn" : ""}">${r.cog_pct}%</b>
        <span class="sub">${{ vetted: "vetted", invoice: "invoiced", latest: "latest" }[r.base_from] || ""} cost${r.base_pieces > 1 ? ` of ${r.base_pieces} pcs (one sale)` : ""}
          ${r.base_cost ? money(r.base_cost, "USD") : ""} vs price</span>` : "—"}</td>
      <td class="num">${r.recommended ? `<b>${money(r.recommended, cur)}</b>${guide}
        ${Math.abs(r.price_change) >= 0.5 ? `<button class="btn btn-sm" data-apply="${i}" data-manage-only>Apply</button>` : ""}` : "—"}</td>`;
  };

  // ---- small dialog
  function dialog(title, html) {
    let d = $("vetDlg");
    if (!d) {
      d = document.createElement("div");
      d.id = "vetDlg";
      d.className = "vet-dlg";
      document.body.appendChild(d);
    }
    d.innerHTML = `<div class="vet-box"><div class="vet-head"><h3>${esc(title)}</h3><button class="btn btn-sm btn-ghost" data-x>Close</button></div>${html}</div>`;
    d.onclick = (e) => { if (e.target === d || e.target.closest("[data-x]")) d.hidden = true; };
    d.hidden = false;
    return d;
  }

  function editVetted(r) {
    const v = r.vetted || {}, s = r.suggested || {};
    let offer = { ...(r.offer || { type: "qty", pack: 1 }) };
    const today = new Date().toISOString().slice(0, 10);
    const d = dialog(`Vetted prices · ${r.title}`, `
      <p class="hint">What ${esc(r.supplier || "the supplier")} should charge (USD, incl. shipping) in ${esc(r.store)}, as the total
        for that many pieces in one order. ${r.policy === "tiered" ? "This store is on tiered pricing: more pieces of the same product, less per piece."
        : "This store is on the per-item discount: the discount for different products shipped together is learned from invoices."}
        Grey numbers come from invoices. The price is the cost of every order of this product from the date below (orders
        before keep their earlier price), and only those orders' invoices are checked against it.</p>
      <div class="vet-row"><label><span>How it's sold</span><select class="control" data-otype>
          <option value="qty"${offer.type === "qty" ? " selected" : ""}>Single items (Buy 1, 2, 3, 4)</option>
          <option value="bundle"${offer.type === "bundle" ? " selected" : ""}>Bundle only (e.g. Buy 1 Get 1: packs of 2)</option></select></label>
        <label data-packbox ${offer.type === "bundle" ? "" : "hidden"}><span>Pieces per sale</span>
          <input class="control" type="number" min="2" max="6" data-pack value="${offer.pack > 1 ? offer.pack : 2}"></label>
        <label><span>Price applies from</span><input class="control" type="date" data-from value="${esc(today)}" max="${esc(today)}"></label></div>
      <div class="vet-grid" data-grid></div>
      <button class="btn btn-sm btn-ghost" data-more>+ Add another quantity (e.g. Buy 8, Buy 10)</button>
      <div class="vet-actions"><button class="btn btn-primary" data-save>Save vetted prices</button>
        ${Object.keys(s).length ? `<button class="btn btn-ghost" data-fill>Use the invoice prices</button>` : ""}</div>
      ${(r.vetted_history || []).length ? `<h4 class="vet-h4">Prices set</h4><div class="vet-hist">${r.vetted_history.map((h) =>
        `<div><b class="${h.source === "team" ? "pdk-blue" : "pdk-brown"}">from ${esc(h.from)}</b> ${Object.entries(h.tiers).map(([k, x]) => `${esc(pieceName(+k, r))} ${money(x, "USD")}`).join(" · ")}
          <span class="hint">${h.source === "team" ? esc((h.by || "").split("@")[0]) : "filled by the app"}</span> <a href="#" data-del="${esc(h.from)}" data-write-only>remove</a></div>`).join("")}</div>` : ""}`);
    // 4 boxes by default (Buy 1-4, or 1-4 sales of a bundle); more can be added, and saved extra ones always show
    const extra = new Set([...Object.keys(v), ...Object.keys(s)].map(Number));
    const keysFor = () => {
      const base = offer.type === "bundle" ? [1, 2, 3, 4].map((n) => n * offer.pack) : [1, 2, 3, 4];
      const more = [...extra].filter((k) => !base.includes(k) && (Object.keys(v).map(Number).includes(k) || added.has(k)));
      return [...new Set([...base, ...more])].sort((a, b) => a - b);
    };
    const added = new Set();
    const draw = () => {
      const keep = Object.fromEntries([...d.querySelectorAll("[data-tier]")].map((x) => [x.dataset.tier, x.value]));
      d.querySelector("[data-grid]").innerHTML = keysFor().map((k) => `<label><span>${esc(pieceName(k, { offer }))}</span>
        <input class="control" type="number" step="0.01" min="0" data-tier="${k}" value="${keep[k] ?? v[k] ?? ""}" placeholder="${s[k] ? s[k] + " (invoice)" : ""}"></label>`).join("");
    };
    draw();
    d.querySelector("[data-more]").onclick = () => {
      const n = parseInt(prompt(offer.type === "bundle" ? "How many pieces (e.g. 10)?" : "Buy how many (e.g. 8)?", "") || "", 10);
      if (!n || n < 1 || n > 50) return;
      added.add(n);
      extra.add(n);
      draw();
    };
    d.querySelector("[data-otype]").onchange = (e) => {
      offer = { type: e.target.value, pack: e.target.value === "bundle" ? Math.max(2, +d.querySelector("[data-pack]").value || 2) : 1 };
      d.querySelector("[data-packbox]").hidden = offer.type !== "bundle";
      draw();
    };
    d.querySelector("[data-pack]").onchange = (e) => { offer.pack = Math.max(2, Math.min(6, +e.target.value || 2)); draw(); };
    const tiers = () => Object.fromEntries([...d.querySelectorAll("[data-tier]")].map((x) => [x.dataset.tier, x.value]).filter(([, x]) => x !== ""));
    const save = async (t, from) => {
      try {
        const got = await post("/api/cog/pdesk/vetted", { store: r.store_id, pid: r.pid, tiers: t, offer, from }, "PUT");
        d.hidden = true;
        toast(Object.keys(t).length ? `Saved from ${got.from}. Re-costing ${got.recosting} order${got.recosting === 1 ? "" : "s"} of this product.` : "Removed.");
        loadPdk(true, true);
      } catch (e) { toast(e.message, true); }
    };
    const fill = d.querySelector("[data-fill]");
    if (fill) fill.onclick = () => d.querySelectorAll("[data-tier]").forEach((x) => { if (!x.value && s[x.dataset.tier]) x.value = s[x.dataset.tier]; });
    d.querySelector("[data-save]").onclick = () => {
      const t = tiers();
      const smallest = keysFor()[0];
      if (!t[smallest]) { toast(`${pieceName(smallest, { offer })} is needed.`, true); return; }
      save(t, d.querySelector("[data-from]").value);
    };
    d.querySelectorAll("[data-del]").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); if (confirm(`Remove the price from ${a.dataset.del}?`)) save({}, a.dataset.del); };
    });
  }

  async function applyPrice(r) {
    let pv;
    try { pv = await post("/api/cog/pdesk/apply-price", { store: r.store_id, pid: r.pid, price: r.recommended, dry: true }); }
    catch (e) { toast(e.message, true); return; }
    const cur = r.price_currency;
    const d = dialog(`Change the price · ${pv.title}`, `
      <p class="hint">${esc(r.store)}: COG is ${r.cog_pct}% now; the target is 29%. Every variant moves by the same %, and the
        was-price too, so the sale badge stays the same. This changes the price in Shopify straight away.</p>
      <table class="table vet-tbl"><thead><tr><th>Variant</th><th class="num">Price now</th><th class="num">New price</th><th class="num">Was-price</th></tr></thead><tbody>
      ${pv.changes.slice(0, 40).map((c, n) => `<tr><td>${n + 1}</td><td class="num">${money(+c._old, cur)}</td><td class="num"><b>${money(+c.price, cur)}</b></td>
        <td class="num">${c.compareAtPrice ? `${money(+c._old_was, cur)} → ${money(+c.compareAtPrice, cur)}` : "—"}</td></tr>`).join("")}</tbody></table>
      <div class="vet-actions"><button class="btn btn-primary" data-go>Apply in Shopify</button></div>`);
    d.querySelector("[data-go]").onclick = async () => {
      try {
        await post("/api/cog/pdesk/apply-price", { store: r.store_id, pid: r.pid, price: r.recommended });
        d.hidden = true;
        toast("Price changed in Shopify.");
        loadPdk(true, true);
      } catch (e) { toast(e.message, true); }
    };
  }

  // ---- the price-check sheet
  const STATE = { ok: "Checked", refunded: "Refunded", escalated: "Escalated" };
  async function flagSheet(onlyPid) {
    const sid = $("productStore").value;
    let f;
    try { f = await api(`/api/cog/pdesk/flags?store=${encodeURIComponent(sid)}`); } catch (e) { toast(e.message, true); return; }
    let rows = f.flags;
    if (onlyPid) rows = rows.filter((x) => x.store_id === onlyPid.store_id && x.products.includes(onlyPid.pid));
    const d = dialog("Price checks: invoices that don't match the vetted price", `
      <p class="hint">The invoice charged a different amount than the vetted price for that order's date${Object.values(f.policies || {}).includes("per_item") ? " (after the learned per-item discount for different products)" : ""}.
        Compare the two invoices, raise it with the supplier, then mark it: Escalated (asked), Refunded (the supplier paid
        the difference back) or OK (nothing to do).</p>
      ${rows.length ? `<div class="table-wrap"><table class="table vet-tbl"><thead><tr><th>Order</th><th>Products</th><th>Invoice</th>
        <th class="num">Charged</th><th class="num">Vetted</th><th class="num">Difference</th><th></th></tr></thead><tbody>
        ${rows.map((x, n) => `<tr><td><b>${esc(x.order)}</b><span class="sub">${esc(x.store)} · ${esc(x.day || "")}</span></td>
          <td>${x.titles.map(esc).join(" + ")}<span class="sub">${x.pieces} piece${x.pieces > 1 ? "s" : ""}</span></td>
          <td>${esc(x.invoice.number || "")}<span class="sub">${esc(x.invoice.date || "")} · ${esc(x.invoice.supplier || "")}</span></td>
          <td class="num">${money(x.charged, "USD")}</td><td class="num pdk-blue">${money(x.expected, "USD")}</td>
          <td class="num"><b class="${x.gap > 0 ? "pdk-red" : "pdk-ok"}">${x.gap > 0 ? "+" : ""}${money(x.gap, "USD")}</b></td>
          <td class="vet-flagbtns">${x.state !== "open" ? `<span class="cd-tag ${x.state === "escalated" ? "t-bad" : "t-inv"}" title="${esc(x.mark_note || "")}">${STATE[x.state] || x.state}</span>` : ""}
            <button class="btn btn-sm" data-cmp="${n}">Compare invoices</button>
            <button class="btn btn-sm" data-mark="escalated" data-n="${n}" data-write-only>Escalated</button>
            <button class="btn btn-sm" data-mark="refunded" data-n="${n}" data-write-only title="The supplier paid the difference back">Refunded</button>
            <button class="btn btn-sm btn-ghost" data-mark="ok" data-n="${n}" data-write-only>OK</button></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="hint">Nothing to check: every invoice matches the vetted prices.</p>`}`);
    d.onclick = async (e) => {
      if (e.target === d || e.target.closest("[data-x]")) { d.hidden = true; return; }
      const c = e.target.closest("[data-cmp]");
      if (c) {
        const x = rows[+c.dataset.cmp];
        d.hidden = true;
        if (window.cdOpenCompare) window.cdOpenCompare({ store_id: x.store_id, order: x.order, store: x.store, items: x.titles.map((t) => ({ product: t })) });
        return;
      }
      const m = e.target.closest("[data-mark]");
      if (!m) return;
      const x = rows[+m.dataset.n];
      const note = m.dataset.mark === "ok" ? "" : prompt(m.dataset.mark === "refunded" ? "Note (optional), e.g. refund on the 25 Oct invoice" : "Note (optional), e.g. asked Dayone on Slack", "");
      if (note === null) return;
      try {
        await post("/api/cog/pdesk/flags/mark", { store: x.store_id, order_key: x.order_key, state: m.dataset.mark, note });
        toast("Saved.");
        flagSheet(onlyPid);
        loadPdk(true, true);
      } catch (err) { toast(err.message, true); }
    };
  }

  $("pdk").addEventListener("click", async (e) => {
    const c = e.target.closest("[data-confirm]");
    if (c) {
      e.preventDefault(); e.stopPropagation();
      const r = pdk.rows[+c.dataset.confirm];
      if (!confirm(`Confirm the app's prices for ${r.title} as checked (they turn blue)?`)) return;
      try {
        await post("/api/cog/pdesk/vetted", { store: r.store_id, pid: r.pid, tiers: r.vetted, offer: r.offer, from: r.vetted_from }, "PUT");
        toast("Confirmed.");
        loadPdk(true, true);
      } catch (err) { toast(err.message, true); }
      return;
    }
    const v = e.target.closest("[data-vet]");
    if (v) { e.preventDefault(); e.stopPropagation(); editVetted(pdk.rows[+v.dataset.vet]); return; }
    const a = e.target.closest("[data-apply]");
    if (a) { e.preventDefault(); applyPrice(pdk.rows[+a.dataset.apply]); return; }
    const f = e.target.closest("[data-flags]");
    if (f) { e.preventDefault(); const r = pdk.rows[+f.dataset.flags]; flagSheet({ store_id: r.store_id, pid: r.pid }); }
  }, true);

  $("pdkPanel").addEventListener("click", async (e) => {
    const s = e.target.closest("[data-show]");
    if (s) { $("pdkShow").value = s.dataset.show; drawPdk(); return; }
    if (e.target.closest("[data-flagsheet]")) { flagSheet(); return; }
    if (e.target.closest("[data-checksfrom]")) {
      e.preventDefault();
      const cur = (pdk.panel?.starts || {})[$("productStore").value] || "";
      const v = prompt("Check invoices of orders from this day (YYYY-MM-DD), e.g. the day you switched supplier. Empty = all.", cur);
      if (v === null) return;
      try { await post("/api/cog/pdesk/checks-from", { store: $("productStore").value, from: v.trim() }, "PUT"); loadPdk(true); }
      catch (err) { toast(err.message, true); }
      return;
    }
    const p = e.target.closest("[data-policy]");
    if (p) {
      e.preventDefault();
      if (!confirm(`Switch this store to ${POLICY[p.dataset.policy]}? Its vetted products' orders are re-costed.`)) return;
      try { await post("/api/cog/pdesk/policy", { store: $("productStore").value, mode: p.dataset.policy }, "PUT"); loadPdk(true); }
      catch (err) { toast(err.message, true); }
    }
  });
})();
