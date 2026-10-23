/* COG → Products: vetted supplier prices (blue), the all-time panel, COG % and suggested selling price with Apply,
   and the price-check sheet. Every figure comes from the server (/api/cog/pdesk); this file only shows them. */
(function () {
  const POLICY = { tiered: "Tiered pricing (same product, less per piece)", per_item: "Per-item discount (different products)" };
  const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);

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
      <button class="pdk-card click" data-show="unvetted"><span>Sold, no vetted price</span>
        <b class="${p.unvetted ? "pdk-warn" : "pdk-ok"}">${p.unvetted}</b>
        <small>${p.unvetted ? "ask the supplier for the real price (and bundle / qty prices)" : "every sold product is vetted"}</small></button>
      <button class="pdk-card click" data-show="vetted"><span>Vetted products</span><b class="pdk-blue">${p.vetted}</b>
        <small>their cost is the vetted price</small></button>
      <button class="pdk-card click" data-flagsheet><span>Price checks</span>
        <b class="${p.flags ? "pdk-red" : "pdk-ok"}">${p.flags}</b>
        <small>${p.flags ? "invoices that don't match the vetted price" : "invoices match the vetted prices"}</small></button>
      ${one ? `<div class="pdk-card"><span>Supplier discount</span><b class="pdk-small">${esc(POLICY[pol] || pol)}</b>
        <small>${pol === "per_item" ? `learned from invoices: −${money(p.per_item[sid] || 0, "USD")} per item when products ship together` : "Buy 2 / 3 / 4 prices per product"}
        <a href="#" data-policy="${pol === "tiered" ? "per_item" : "tiered"}" data-manage-only>switch</a></small></div>` : ""}`;
  };

  // ---- the vetted / COG % / suggested price cells of one product row
  window.pdkCells = (r, i) => {
    const v = r.vetted || {}, s = r.suggested || {};
    const tiers = ["1", "2", "3", "4"].filter((k) => v[k] || s[k]);
    const vet = v["1"]
      ? `<b class="pdk-blue">${money(v["1"], "USD")}</b>`
      : `<span class="hint">not vetted</span>`;
    const dd = tiers.length ? `<details class="pdk-dd"><summary>Buy 1–4</summary>${tiers.map((k) =>
      `<span class="sub">Buy ${k}: ${v[k] ? `<b class="pdk-blue">${money(v[k], "USD")}</b>` : `<span title="from invoices, not vetted">${money(s[k], "USD")} (invoice)</span>`}</span>`).join("")}</details>` : "";
    const cur = r.price_currency;
    const guide = r.recommended
      ? (Math.abs(r.price_change) < 0.5 ? `<span class="sub pdk-ok">on target</span>`
        : `<span class="sub ${r.price_change > 0 ? "pdk-warn" : "pdk-ok"}">${r.price_change > 0 ? "raise" : "lower"} by ${money(Math.abs(r.price_change), cur)}</span>`)
      : "";
    return `<td class="num"><a href="#" data-vet="${i}" title="Set the vetted prices">${vet}</a>${dd}
        <a href="#" class="sub" data-vet="${i}" data-write-only>${v["1"] ? "edit" : "set prices"}</a></td>
      <td class="num">${r.cog_pct !== null && r.cog_pct !== undefined ? `<b class="${r.cog_pct > 29 ? "pdk-warn" : ""}">${r.cog_pct}%</b>
        <span class="sub">${r.base_from === "vetted" ? "vetted cost" : "latest cost"} vs price</span>` : "—"}</td>
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
      d.addEventListener("click", (e) => { if (e.target === d || e.target.closest("[data-x]")) d.hidden = true; });
    }
    d.innerHTML = `<div class="vet-box"><div class="vet-head"><h3>${esc(title)}</h3><button class="btn btn-sm btn-ghost" data-x>Close</button></div>${html}</div>`;
    d.hidden = false;
    return d;
  }

  function editVetted(r) {
    const v = r.vetted || {}, s = r.suggested || {};
    const d = dialog(`Vetted prices · ${r.title}`, `
      <p class="hint">What ${esc(r.supplier || "the supplier")} should charge (USD, incl. shipping) for 1, 2, 3 and 4 pieces of this
        product in one order, in ${esc(r.store)}. ${r.policy === "tiered" ? "This store is on tiered pricing: more pieces, less per piece."
        : "This store is on the per-item discount: Buy 1 is what counts; the discount for different products shipped together is learned from invoices."}
        Grey numbers come from invoices. Saving re-costs every order of this product (the dashboard too).</p>
      <div class="vet-grid">${["1", "2", "3", "4"].map((k) => `<label><span>Buy ${k}</span>
        <input class="control" type="number" step="0.01" min="0" data-tier="${k}" value="${v[k] ?? ""}" placeholder="${s[k] ? s[k] + " (invoice)" : ""}"></label>`).join("")}</div>
      <div class="vet-actions"><button class="btn btn-primary" data-save>Save vetted prices</button>
        ${Object.keys(s).length ? `<button class="btn btn-ghost" data-fill>Use the invoice prices</button>` : ""}
        ${v["1"] ? `<button class="btn btn-ghost" data-clear>Remove vetting</button>` : ""}</div>`);
    const tiers = () => Object.fromEntries([...d.querySelectorAll("[data-tier]")].map((x) => [x.dataset.tier, x.value]).filter(([, x]) => x !== ""));
    const save = async (t) => {
      try {
        const got = await api("/api/cog/pdesk/vetted", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ store: r.store_id, pid: r.pid, tiers: t }) });
        d.hidden = true;
        toast(`Saved. Re-costing ${got.recosting} order${got.recosting === 1 ? "" : "s"} of this product.`);
        loadPdk(true, true);
      } catch (e) { toast(e.message, true); }
    };
    const fill = d.querySelector("[data-fill]");
    if (fill) fill.onclick = () => d.querySelectorAll("[data-tier]").forEach((x) => { if (!x.value && s[x.dataset.tier]) x.value = s[x.dataset.tier]; });
    d.querySelector("[data-save]").onclick = () => {
      const t = tiers();
      if (!t["1"]) { toast("Buy 1 is needed.", true); return; }
      save(t);
    };
    const clr = d.querySelector("[data-clear]");
    if (clr) clr.onclick = () => { if (confirm("Remove the vetted prices? Its orders go back to invoice / last prices.")) save({}); };
  }

  async function applyPrice(r) {
    let pv;
    try {
      pv = await api("/api/cog/pdesk/apply-price", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store: r.store_id, pid: r.pid, price: r.recommended, dry: true }) });
    } catch (e) { toast(e.message, true); return; }
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
        await api("/api/cog/pdesk/apply-price", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ store: r.store_id, pid: r.pid, price: r.recommended }) });
        d.hidden = true;
        toast("Price changed in Shopify.");
        loadPdk(true, true);
      } catch (e) { toast(e.message, true); }
    };
  }

  // ---- the price-check sheet
  async function flagSheet(onlyPid) {
    const sid = $("productStore").value;
    let f;
    try { f = await api(`/api/cog/pdesk/flags?store=${encodeURIComponent(sid)}`); } catch (e) { toast(e.message, true); return; }
    let rows = f.flags;
    if (onlyPid) rows = rows.filter((x) => x.store_id === onlyPid.store_id && x.products.includes(onlyPid.pid));
    const d = dialog("Price checks: invoices that don't match the vetted price", `
      <p class="hint">The invoice charged a different amount than the vetted prices${Object.values(f.policies || {}).includes("per_item") ? " (after the learned per-item discount for different products)" : ""}.
        Compare the two invoices, raise it with the supplier, then mark it.</p>
      ${rows.length ? `<div class="table-wrap"><table class="table vet-tbl"><thead><tr><th>Order</th><th>Products</th><th>Invoice</th>
        <th class="num">Charged</th><th class="num">Vetted</th><th class="num">Difference</th><th></th></tr></thead><tbody>
        ${rows.map((x, n) => `<tr><td><b>${esc(x.order)}</b><span class="sub">${esc(x.store)} · ${esc(x.day || "")}</span></td>
          <td>${x.titles.map(esc).join(" + ")}<span class="sub">${x.pieces} piece${x.pieces > 1 ? "s" : ""}</span></td>
          <td>${esc(x.invoice.number || "")}<span class="sub">${esc(x.invoice.date || "")} · ${esc(x.invoice.supplier || "")}</span></td>
          <td class="num">${money(x.charged, "USD")}</td><td class="num pdk-blue">${money(x.expected, "USD")}</td>
          <td class="num"><b class="${x.gap > 0 ? "pdk-red" : "pdk-ok"}">${x.gap > 0 ? "+" : ""}${money(x.gap, "USD")}</b></td>
          <td class="vet-flagbtns">${x.state !== "open" ? `<span class="cd-tag ${x.state === "ok" ? "t-inv" : "t-bad"}">${x.state === "ok" ? "Checked" : "Escalated"}</span>` : ""}
            <button class="btn btn-sm" data-cmp="${n}">Compare invoices</button>
            <button class="btn btn-sm" data-mark="escalated" data-n="${n}" data-write-only>Escalated</button>
            <button class="btn btn-sm btn-ghost" data-mark="ok" data-n="${n}" data-write-only>OK</button></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="hint">Nothing to check: every invoice matches the vetted prices.</p>`}`);
    d.onclick = async (e) => {
      const c = e.target.closest("[data-cmp]");
      if (c) {
        const x = rows[+c.dataset.cmp];
        d.hidden = true;
        if (window.cdOpenCompare) window.cdOpenCompare({ store_id: x.store_id, order: x.order, store: x.store, items: x.titles.map((t) => ({ product: t })) });
        return;
      }
      const m = e.target.closest("[data-mark]");
      if (m) {
        const x = rows[+m.dataset.n];
        const note = m.dataset.mark === "escalated" ? (prompt("Note (optional), e.g. asked Dayone on Slack", "") ?? null) : "";
        if (note === null) return;
        try {
          await api("/api/cog/pdesk/flags/mark", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ store: x.store_id, order_key: x.order_key, state: m.dataset.mark, note }) });
          toast("Saved.");
          flagSheet(onlyPid);
          loadPdk(true, true);
        } catch (err) { toast(err.message, true); }
        return;
      }
      if (e.target === d || e.target.closest("[data-x]")) d.hidden = true;
    };
  }

  $("pdk").addEventListener("click", (e) => {
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
    const p = e.target.closest("[data-policy]");
    if (p) {
      e.preventDefault();
      if (!confirm(`Switch this store to ${POLICY[p.dataset.policy]}? Its orders' costs follow on the next re-cost.`)) return;
      try {
        await api("/api/cog/pdesk/policy", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ store: $("productStore").value, mode: p.dataset.policy }) });
        loadPdk(true);
      } catch (err) { toast(err.message, true); }
    }
  });
})();
