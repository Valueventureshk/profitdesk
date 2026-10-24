/* COG desk: Check orders + Invoices by date. Uses cog.js's $, api, esc, money, range, niceDate, toast, status.
   Every figure comes from the server (cog_desk.py); this file only draws and sends answers. */
(function () {
  const usd = (v) => (v === null || v === undefined ? "—" : "US$" + money(v));
  const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const GROUP = { up: "up", down: "up", review: "up", inv: "inv", real: "inv", kept: "inv", bundle: "inv", note: "inv",
                  same: "same", new: "new", est: "est", cancel: "" };
  const STATE = { invoiced: '<span class="cd-tag t-same">Invoiced</span>', notinv: '<span class="cd-tag t-est">Not invoiced yet</span>',
                  unfulfilled: '<span class="cd-tag t-same">Not fulfilled</span>',
                  billed: '<span class="cd-tag t-same" title="Older than this supplier\'s newest invoice for the store, so it was billed; its invoice file isn\'t in the app">Billed, no invoice file</span>', cancelled: '<span class="cd-tag t-same">Cancelled</span>' };
  const TAG = {
    cancel: '<span class="cd-tag t-same">No cost: cancelled</span>', inv: '<span class="cd-tag t-inv">✓ Invoiced price</span>',
    same: '<span class="cd-tag t-same">= Same as last time</span>', new: '<span class="cd-tag t-new">★ First-time product</span>',
    est: '<span class="cd-tag t-est">~ Estimated: no invoice yet</span>', bundle: '<span class="cd-tag t-inv">✓ Bundle (you said)</span>',
    real: '<span class="cd-tag t-inv">✓ New price accepted</span>', kept: '<span class="cd-tag t-inv">✓ Old price kept</span>',
    review: '<span class="cd-tag t-bad">↗ Asked the supplier</span>', note: '<span class="cd-tag t-same">📝 Explained</span>' };
  const SUB = {
    inv: "Supplier's invoice for this order", same: "Matches the last invoice for this product",
    new: "Never bought before: price from its first invoice", est_last: "The product's last invoiced price, until its invoice arrives",
    est_avg: "Store's usual cost %, until the invoice arrives", est_vetted: "Vetted price (what the supplier should charge)", up: "Click to compare with the last invoice",
    down: "Click to compare with the last invoice", review: "Waiting for the supplier's answer",
    real: "New price used from now on", kept: "Supplier corrected it: the old price is used",
    bundle: "Billed as a bundle / BOGO: not a price rise", note: "" };
  const cd = { orders: [], filter: "all", open: null, review: null, noteFor: null, start: "", end: "" };
  const keyOf = (o) => `${o.store_id}|${o.order}`;
  const when = (t) => { const d = new Date(t); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); };

  /* ------------------------------------------------ check orders */

  window.cdLoadOrders = async function () {
    const [from, to] = range();
    const custom = $("range").value === "custom";
    PDUrl.set({ range: $("range").value, store: $("storeSelect").value === "all" ? null : $("storeSelect").value,
                from: custom ? from : null, to: custom ? to : null });
    $("dates").hidden = !custom;
    if (!custom) { $("from").value = from; $("to").value = to; }
    const label = from === to ? niceDate(from) : `${niceDate(from)} – ${niceDate(to)}`;
    $("sub").textContent = label + " · loading…";
    let d;
    try { d = await api(`/api/cog/orders?start=${from}&end=${to}&store=${$("storeSelect").value}`); }
    catch (e) { toast(e.message, true); $("sub").textContent = label; return; }
    cd.orders = d.orders; cd.start = from; cd.end = to;
    $("sub").textContent = `${label} · costs in USD` + (status?.synced_at ? ` · updated ${new Date(status.synced_at * 1000)
      .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : "");
    $("cdTotals").textContent = `${d.orders.length} orders · sales ${usd(d.sale)} · COG ${usd(d.cog)}`;
    draw();
  };

  function shown() {
    const q = ($("cdFind").value || "").trim().toLowerCase();
    return cd.orders.filter((o) => (cd.filter === "all" || GROUP[o.kind] === cd.filter)
      && (!q || o.order.toLowerCase().includes(q) || o.items.some((i) => i.product.toLowerCase().includes(q))));
  }

  function draw() {
    const n = (g) => cd.orders.filter((o) => GROUP[o.kind] === g).length;
    $("cdcAll").textContent = cd.orders.length;
    for (const [id, g] of [["cdcInv", "inv"], ["cdcSame", "same"], ["cdcUp", "up"], ["cdcNew", "new"], ["cdcEst", "est"]]) $(id).textContent = n(g);
    const open = cd.orders.filter((o) => o.kind === "up" || o.kind === "down").length;
    $("cdBadge").textContent = open; $("cdBadge").hidden = !open;
    for (const b of document.querySelectorAll(".cd-chip")) b.classList.toggle("on", b.dataset.f === cd.filter);
    const list = shown();
    $("cdRows").innerHTML = list.length ? list.map((o) => {
      const flagged = o.kind === "up" || o.kind === "down";
      const src = flagged ? `<span class="cd-tag ${o.kind === "up" ? "t-up" : "t-down"}">${o.kind === "up" ? "▲ Price went up" : "▼ Price went down"}${o.pct != null ? ` ${Math.abs(o.pct)}%` : ""}</span>` : (TAG[o.kind] || "");
      const sub = o.kind === "note" ? "" : (SUB[o.sub] || SUB[o.kind] || "");
      const cancel = o.kind === "cancel";
      const items = o.items.map((i) => `<div class="cd-it"><span>${esc(i.product)}${i.quantity > 1 ? ` ×${i.quantity}` : ""}</span><span class="cd-num">${cancel ? "–" : usd(i.shown)}</span></div>`).join("")
        + (o.bundle_discount != null ? (o.bundle_discount > 0
          ? `<div class="cd-it disc"><span>(bundle discount for ${o.count} items)</span><span class="cd-num">−${usd(o.bundle_discount)}</span></div>`
          : `<div class="cd-it dear"><span>(billed more than ${o.count} singles)</span><span class="cd-num">+${usd(-o.bundle_discount)}</span></div>`) : "");
      return `<tr class="${o.state === "invoiced" ? "click" : ""}" data-k="${esc(keyOf(o))}">
        <td class="cd-n"><b>${esc(o.order)}</b><span class="sub">${esc(o.store)}</span><span class="sub">${esc(when(o.created))}</span></td>
        <td class="items">${items}</td>
        <td class="cd-n">${cancel ? "–" : subtotal(o)}</td>
        <td class="cd-n"><b>${cancel ? "–" : usd(o.cog)}</b></td>
        <td class="cd-n"><b class="${(o.cog_pct || 0) >= 40 ? "hi" : ""}">${cancel || o.cog_pct == null ? "–" : Math.round(o.cog_pct) + "%"}</b>${cancel ? "" : '<span class="sub">of the sale</span>'}</td>
        <td><div class="cd-flags">${cancel ? "" : o.paid ? '<span class="cd-tag t-inv">Paid</span>' : '<span class="cd-tag t-bad">Unpaid</span>'}${STATE[o.state] || ""}</div>
          <div class="cd-flags">${src}</div><span class="sub">${esc(sub)}</span></td>
        <td class="onote">${o.note ? `<button class="cd-notebtn has" data-note>📝 ${esc(o.note)}</button>` : `<button class="cd-notebtn" data-note data-write-only>+ Add note</button>`}</td>
        <td>${cancel ? "" : '<button class="btn btn-sm cd-ai" data-ai data-write-only>✨ AI check</button>'}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="cd-empty">${cd.orders.length ? "No orders match." : "No orders costed for this period yet."}</td></tr>`;
  }

  // Bold: what the customer paid (Shopify order total). Grey under it: the items before discount, the discount
  // and why, shipping; and a tag for staff-made draft orders (replacements).
  function subtotal(o) {
    const s = o.shop;
    if (!s) return `<b>${usd(o.sale)}</b>`;
    let h = `<b>${usd(s.total)}</b>`;
    if (s.discount >= 0.01) {
      h += `<span class="cd-disc">${usd(s.subtotal)} before discount</span>`;
      h += `<span class="cd-disc">−${usd(s.discount)}${s.discount_names && s.discount_names.length ? " · " + esc(s.discount_names.join(", ")) : " discount"}</span>`;
    }
    if (s.shipping >= 0.01) h += `<span class="cd-disc">incl. ${usd(s.shipping)} shipping</span>`;
    if (s.draft) h += `<span class="cd-tag t-draft" title="Made by staff in Shopify (Draft Orders), e.g. a replacement">Staff draft order</span>`;
    return h;
  }
  const byKey = (k) => cd.orders.find((o) => keyOf(o) === k);
  $("cdChips").onclick = (e) => { const b = e.target.closest(".cd-chip"); if (b) { cd.filter = b.dataset.f; draw(); } };
  $("cdFind").oninput = draw;
  $("cdRows").onclick = (e) => {
    const tr = e.target.closest("tr[data-k]"); if (!tr) return;
    const o = byKey(tr.dataset.k);
    if (e.target.closest("[data-note]")) { openNote(o); return; }
    if (e.target.closest("[data-ai]")) { aiCheck([o]); return; }
    if (tr.classList.contains("click")) openCompare(o);
  };

  // ---- compare pop-up: this order's invoice vs the last invoice with this product in the same store
  const invCard = (inv, cls, label) => `<div class="cd-inv ${cls}"><h3>${label}</h3>
      <div class="meta">${esc(inv.supplier || "Supplier")} · invoice #${esc(inv.number || "—")}${inv.date ? ` · ${niceDate(inv.date)}` : ""}</div>
      <table><tbody>${inv.lines.map((l) => `<tr><td>${esc(l.order)}<span class="sub">${esc(l.product || l.title || "")} · ×${+l.units}</span></td>
        <td class="r">${usd(l.amount)}</td></tr>`).join("")}</tbody></table><div class="total">${usd(inv.total)}</div></div>`;

  async function openCompare(o) {
    cd.open = o;
    let d;
    try { d = await api(`/api/cog/compare?store_id=${o.store_id}&order=${encodeURIComponent(o.order)}`); }
    catch (e) { toast(e.message, true); return; }
    cd.review = { kind: "order", o, text: d.review_text, channel: d.channel, supplier: d.supplier };
    $("cdCmpTitle").textContent = `${o.order} · ${o.store} · ${o.items.map((i) => i.product).join(" + ")}`.slice(0, 160);
    const head = o.kind === "up" || o.kind === "down" ? `Cost went ${o.kind}${o.pct != null ? ` ${Math.abs(o.pct)}%` : ""}. ` : "";
    $("cdCmpLead").textContent = head + (d.hint || "") + (o.answer ? ` You answered: ${o.answer}${o.answer_by ? ` (${o.answer_by.split("@")[0]})` : ""}.` : "");
    $("cdCmpBody").innerHTML = invCard(d.this, "new", "This order's invoice")
      + (d.last ? invCard(d.last, "", "Last invoice with this product, same store") : `<div class="cd-inv"><h3>No earlier invoice</h3><div class="meta">First time this product is invoiced in ${esc(o.store)}.</div></div>`);
    $("cdRevSub").textContent = d.channel ? `Sends it to ${d.supplier} in #${d.channel} to review.` : `Marks it as checking (no Slack invoice channel for ${d.supplier} yet).`;
    $("cdLearned").hidden = true; $("cdNoteBox").hidden = true; $("cdNoteText").value = "";
    const undo = $("cdAnswers").querySelector('[data-a="clear"]');
    if (undo) undo.remove();
    if (o.answer) $("cdAnswers").insertAdjacentHTML("beforeend", '<button data-a="clear"><b>Undo my answer</b><span>Back to “to check”.</span></button>');
    $("cdCmp").hidden = false;
  }

  window.cdOpenCompare = (o) => openCompare({ kind: "", answer: "", ...o });     // Products → price checks

  async function answer(o, a, extra = {}) {
    const r = await post("/api/invoices/orders/answer", { store_id: o.store_id, order: o.order, answer: a, ...extra });
    window.dispatchEvent(new Event("pd-changed"));
    await cdLoadOrders();
    return r;
  }

  $("cdAnswers").onclick = async (e) => {
    const b = e.target.closest("button[data-a]"); if (!b) return;
    const o = cd.open, a = b.dataset.a;
    if (a === "note") { $("cdNoteBox").hidden = false; $("cdNoteText").focus(); return; }
    if (a === "review") { $("cdCmp").hidden = true; openReview(); return; }
    for (const x of $("cdAnswers").querySelectorAll("button")) x.disabled = true;
    try {
      const r = await answer(o, a);
      $("cdLearned").textContent = a === "clear" ? "Undone: the order is back to “to check”." :
        (r.lesson ? `Learned: ${r.lesson}` : "Saved.") + (a === "real" ? " Orders not invoiced yet are re-costed at the new price." : "");
      $("cdLearned").hidden = false;
      if (a === "clear") b.remove();
    } catch (err) { toast(err.message, true); }
    for (const x of $("cdAnswers").querySelectorAll("button")) x.disabled = false;
  };
  $("cdNoteSave").onclick = async () => {
    const t = $("cdNoteText").value.trim();
    if (!t) { toast("Write the note first.", true); return; }
    try { await answer(cd.open, "note", { note: t }); $("cdLearned").textContent = "Saved. The app reads your note as a lesson for orders like this."; $("cdLearned").hidden = false; }
    catch (err) { toast(err.message, true); }
  };

  // ---- send to the supplier (Slack): an order from the compare pop-up / AI, or a flagged invoice
  function openReview() {
    const r = cd.review;
    $("cdRevTitle").textContent = `Send to ${r.supplier || "the supplier"} for review`;
    $("cdRevLead").innerHTML = r.channel ? `The app wrote the message; check or change it, then it posts in <b>#${esc(r.channel)}</b>.`
      : `There's no Slack invoice channel for ${esc(r.supplier || "this supplier")} yet (Agent desk → Slack). Copy the message, or just mark it as checking.`;
    $("cdRevMsg").value = r.text || "";
    $("cdRevGo").hidden = !r.channel;
    $("cdRevOnly").hidden = r.kind !== "order";
    $("cdRev").hidden = false;
  }
  $("cdRevGo").onclick = async () => {
    const r = cd.review, text = $("cdRevMsg").value.trim();
    $("cdRevGo").disabled = true;
    try {
      if (r.kind === "order") await answer(r.o, "review", { post: true, text });
      else { await post("/api/suppliers/invoice-review", { id: r.id, post: true, text }); cdLoadDays(); }
      $("cdRev").hidden = true;
      toast(`Posted in #${r.channel}. It shows “Asked the supplier” until it's sorted.`);
    } catch (err) { toast(err.message, true); }
    $("cdRevGo").disabled = false;
  };
  $("cdRevOnly").onclick = async () => {
    try { await answer(cd.review.o, "review"); $("cdRev").hidden = true; toast("Marked: checking with the supplier."); }
    catch (err) { toast(err.message, true); }
  };

  // ---- notes on orders
  function openNote(o) {
    cd.noteFor = o;
    $("cdOnTitle").textContent = `Note on ${o.order} · ${o.store}`;
    $("cdOnText").value = o.note || "";
    $("cdOn").hidden = false; $("cdOnText").focus();
  }
  $("cdOnSave").onclick = async () => {
    const o = cd.noteFor, note = $("cdOnText").value.trim();
    try {
      await post("/api/invoices/orders/note", { store_id: o.store_id, order: o.order, note });
      o.note = note; $("cdOn").hidden = true; draw();
      toast(note ? "Note saved. The AI reads it for orders like this." : "Note removed.");
    } catch (err) { toast(err.message, true); }
  };

  // ---- AI check: verdict + why + a fix the team applies (nothing changes until they do)
  const TONE = { good: "t-inv", warn: "t-up", bad: "t-bad" };
  const FIX = { bundle: "Mark as bundle", real: "Accept new price", review: "Ask the supplier" };
  async function aiCheck(list) {
    if (!list.length) { toast("Nothing to check here."); return; }
    $("cdAiTitle").textContent = list.length > 1 ? `AI check: ${list.length} orders` : `AI check: ${list[0].order}`;
    $("cdAiLead").textContent = "Reading the orders, their invoices, past prices and your notes…";
    $("cdAiBody").innerHTML = "";
    $("cdAi").hidden = false;
    let d;
    try { d = await post("/api/invoices/orders/ai", { orders: list.map((o) => [o.store_id, o.order]), start: cd.start, end: cd.end }); }
    catch (err) { $("cdAiLead").textContent = err.message; return; }
    const rows = d.orders.filter((a) => list.length === 1 || a.tone !== "good" || a.fix !== "none");
    const fine = d.orders.length - rows.length;
    $("cdAiLead").textContent = (list.length > 1 ? `${fine} look right and are left out. ` : "") + "Apply what you agree with; nothing changes until you do.";
    $("cdAiBody").innerHTML = rows.map((a) => `<div class="cd-airow"><div><b>${esc(a.order)}</b>
        <span class="hint">${esc((cd.orders.find((o) => o.store_id === a.store_id && o.order === a.order) || {}).store || "")}</span>
        <span class="cd-tag ${TONE[a.tone] || "t-same"}">${esc(a.verdict)}</span><p>${esc(a.why)}</p></div>
        ${FIX[a.fix] ? `<button class="btn btn-sm btn-primary" data-apply="${esc(a.store_id + "|" + a.order)}" data-fix="${a.fix}">${FIX[a.fix]}</button>` : ""}</div>`).join("")
      || '<p class="hint">Everything looks right.</p>';
  }
  $("cdAiAll").onclick = () => {
    const look = cd.orders.filter((o) => ["up", "new", "est"].includes(GROUP[o.kind]) && o.kind !== "review");
    if (look.length > 40) toast(`Checking the first 40 of ${look.length} orders.`);
    aiCheck(look.slice(0, 40));
  };
  $("cdAiBody").onclick = async (e) => {
    const b = e.target.closest("[data-apply]"); if (!b) return;
    const o = byKey(b.dataset.apply);
    if (!o) { toast("That order isn't in this list any more.", true); return; }
    if (b.dataset.fix === "review") {
      try {
        const d = await api(`/api/cog/compare?store_id=${o.store_id}&order=${encodeURIComponent(o.order)}`);
        cd.review = { kind: "order", o, text: d.review_text, channel: d.channel, supplier: d.supplier };
        $("cdAi").hidden = true; openReview();
      } catch (err) { toast(err.message, true); }
      return;
    }
    b.disabled = true;
    try {
      await answer(o, b.dataset.fix);
      b.replaceWith(Object.assign(document.createElement("span"), { className: "cd-tag t-inv", textContent: "✓ Applied" }));
      toast("Applied. The app learned it for next time.");
    } catch (err) { toast(err.message, true); b.disabled = false; }
  };

  /* ------------------------------------------------ invoices by date */

  const inv = { d: null, open: null, paid: {}, notes: {}, cutoff: null };
  const dayName = (d) => new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  const cutoff = () => inv.cutoff || inv.d.cutoff;
  const isPaid = (x) => x.date < cutoff() || (x.id in inv.paid ? inv.paid[x.id] : x.marked);
  const dirty = () => Object.keys(inv.paid).length || Object.keys(inv.notes).length || (inv.cutoff && inv.cutoff !== inv.d.cutoff);

  window.cdLoadDays = async function () {
    let d;
    try { d = await api(`/api/suppliers/by-day?supplier=${encodeURIComponent($("cdSup").value)}`); }
    catch (e) { toast(e.message, true); return; }
    inv.d = d; inv.paid = {}; inv.notes = {}; inv.cutoff = null;
    const keep = $("cdSup").value;
    $("cdSup").innerHTML = '<option value="">All suppliers</option>' + d.suppliers.map((s) => `<option${s === keep ? " selected" : ""}>${esc(s)}</option>`).join("");
    $("cdCutoff").value = d.cutoff;
    $("cdCredit").textContent = usd(d.credit);
    $("cdEst").textContent = usd(d.estimated);
    $("cdEstNote").textContent = `${d.estimated_orders} order${d.estimated_orders === 1 ? "" : "s"} not invoiced yet (our estimate)` +
      (d.not_shipped ? `, incl. ${d.not_shipped} placed but not shipped yet (${usd(d.not_shipped_cost)})` : "") +
      (d.billed_no_file ? `. Not counted: ${d.billed_no_file} older orders (${usd(d.billed_no_file_cost)}) already billed on invoices that never reached the app.` : "");
    $("cdTotal").textContent = usd(d.total);
    $("cdAdj").textContent = (d.adj_net > 0 ? "+" : d.adj_net < 0 ? "−" : "") + usd(Math.abs(d.adj_net || 0));
    $("cdAdjNote").textContent = `${usd(d.adj_refund || 0)} refunds due · ${usd(d.adj_payable || 0)} extra to pay`;
    $("aSupList").innerHTML = d.suppliers.map((x) => `<option value="${esc(x)}">`).join("");
    drawAdj();
    drawDays();
  };

  /* ------------------------------------------------ manual adjustments */
  const ADJ_STATUS = { open: '<span class="cd-tag t-up">Open</span>', done: '<span class="cd-tag t-inv">Done</span>', cancelled: '<span class="cd-tag t-same">Cancelled</span>' };
  function drawAdj() {
    const all = $("cdAdjAll").checked;
    const list = (inv.d.adjustments || []).filter((a) => all || a.status === "open");
    const can = !document.body.classList.contains("read-only");
    $("cdAdjRows").innerHTML = list.map((a) => `<tr class="${a.status !== "open" ? "settled" : ""}">
      <td>${a.kind === "refund" ? '<span class="cd-tag t-inv">Refund due to us</span>' : '<span class="cd-tag t-bad">Extra to pay</span>'}</td>
      <td>${esc(a.supplier)}</td>
      <td class="cd-n">${a.kind === "refund" ? "−" : "+"}${usd(a.amount)}</td>
      <td>${a.due_day ? esc(a.due_day) : '<span class="hint">—</span>'}</td>
      <td class="note">${a.note ? esc(a.note) : '<span class="hint">—</span>'}</td>
      <td><span class="hint">${esc((a.by || "").split("@")[0])} · ${esc((a.at || "").slice(0, 10))}${a.edited_by ? `<br>edited by ${esc(a.edited_by.split("@")[0])}` : ""}</span></td>
      <td>${ADJ_STATUS[a.status] || ""}${a.done_by ? `<span class="sub">${esc(a.done_by.split("@")[0])} · ${esc((a.done_at || "").slice(0, 10))}</span>` : ""}</td>
      <td class="cd-adjbtn" data-write-only>${a.status === "open"
        ? `<button class="btn btn-sm" data-adone="${a.id}">${a.kind === "refund" ? "Received" : "Paid"}</button>
           <button class="btn btn-sm btn-ghost" data-aedit="${a.id}">Edit</button>
           <button class="btn btn-sm btn-ghost" data-acancel="${a.id}">Cancel</button>`
        : `<button class="btn btn-sm btn-ghost" data-areopen="${a.id}">Reopen</button>`}</td></tr>`).join("")
      || `<tr><td colspan="8" class="cd-empty">${all ? "No adjustments yet." : "Nothing open."}${can ? " Add a refund due or an amount to pay above." : ""}</td></tr>`;
  }
  let adjEditing = null;
  function adjForm(a, kind) {
    adjEditing = a ? a.id : null;
    $("cdAdjTitle").textContent = a ? "Edit adjustment" : kind === "refund" ? "Refund coming from a supplier" : "Extra amount to pay";
    $("aKind").value = a ? a.kind : kind;
    $("aSup").value = a ? a.supplier : ($("cdSup").value || "");
    $("aAmt").value = a ? a.amount : "";
    $("aDue").value = a ? a.due_day || "" : "";
    $("aNote").value = a ? a.note || "" : "";
    $("cdAdjBox").hidden = false; $("aAmt").focus();
  }
  $("cdAdjAdd").onclick = () => adjForm(null, "refund");
  $("cdAdjAdd2").onclick = () => adjForm(null, "payable");
  $("cdAdjAll").onchange = drawAdj;
  $("aSave").onclick = async () => {
    $("aSave").disabled = true;
    try {
      await post("/api/suppliers/adjustments", { id: adjEditing, kind: $("aKind").value, supplier: $("aSup").value, amount: $("aAmt").value,
        due_day: $("aDue").value, note: $("aNote").value });
      $("cdAdjBox").hidden = true; toast("Saved. Total payable and Cash flow include it now.");
      await cdLoadDays();
    } catch (err) { toast(err.message, true); }
    $("aSave").disabled = false;
  };
  $("cdAdjRows").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const find = (id) => (inv.d.adjustments || []).find((a) => String(a.id) === id);
    if (b.dataset.aedit) { adjForm(find(b.dataset.aedit)); return; }
    const id = b.dataset.adone || b.dataset.acancel || b.dataset.areopen;
    const status = b.dataset.adone ? "done" : b.dataset.acancel ? "cancelled" : "open";
    if (status === "cancelled" && !confirm("Cancel this adjustment? It stops counting (you can reopen it).")) return;
    b.disabled = true;
    try { await post(`/api/suppliers/adjustments/${id}/status`, { status }); toast(status === "open" ? "Reopened." : "Updated."); await cdLoadDays(); }
    catch (err) { toast(err.message, true); b.disabled = false; }
  });

  function drawDays() {
    const d = inv.d;
    const byDate = Object.fromEntries(d.days.map((x) => [x.date, x]));
    // every day from the newest invoice back 30 days (empty days too), then older days that have invoices
    const newest = d.days.length ? d.days[0].date : d.today;
    const t = new Date(newest + "T12:00:00"), stop = new Date(newest + "T12:00:00");
    stop.setDate(stop.getDate() - 30);
    const list = [];
    for (; t >= stop; t.setDate(t.getDate() - 1)) list.push(iso(t));
    for (const x of d.days) if (!list.includes(x.date)) list.push(x.date);
    const rows = [];
    for (const day of list) {
      const g = byDate[day];
      if (!g) { if ($("cdShowEmpty").checked) rows.push(`<tr class="empty"><td>${dayName(day)}</td><td colspan="5">No invoice</td></tr>`); continue; }
      const its = d.invoices.filter((x) => x.date === day);
      const paid = its.filter(isPaid).length, all = paid === its.length, none = paid === 0;
      const before = day < cutoff();
      const flagged = its.some((x) => x.flag);
      const note = day in inv.notes ? inv.notes[day] : (d.day_notes[day] || {}).note || "";
      rows.push(`<tr class="day-row ${inv.open === day ? "open" : ""}" data-d="${day}">
        <td><b>${day ? dayName(day) : "No date"}</b>${before ? '<span class="sub">before the cut-off</span>' : ""}</td>
        <td>${g.count} invoice${g.count > 1 ? "s" : ""}<span class="sub">${Object.entries(g.by_supplier).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</span></td>
        <td class="cd-n">${usd(g.total)}</td>
        <td><span class="cd-tag ${all ? "t-inv" : none ? "t-bad" : "t-up"}">${paid}/${its.length} paid</span>${flagged ? ' <span class="cd-tag t-up">⚑ checking</span>' : ""}</td>
        <td>${before ? '<span class="hint">Paid (cut-off)</span>' : `<select class="mark" data-mark="${day}" aria-label="Mark ${dayName(day)}">
          <option value="">${all ? "All paid" : none ? "Not paid" : "Part paid"}</option><option value="paid">All paid</option><option value="unpaid">None paid</option></select>`}</td>
        <td class="note">${note ? `<span class="notetext">📝 ${esc(note)}</span>` : '<span class="hint">+ note</span>'}</td></tr>`);
      if (inv.open === day) rows.push(`<tr class="inner"><td colspan="6"><div class="cd-invgrid">
        ${its.map((x) => `<div class="cd-ivr ${x.flag ? "fl" : ""}"><span class="cd-ivn"><b>${esc(x.supplier)}</b> · ${esc(x.store)} <span class="hint">#${esc(x.number || "—")}</span>
          ${x.orders && x.orders.count ? `<span class="sub">Bills ${x.orders.count} order${x.orders.count === 1 ? "" : "s"}${x.orders.count > 1 ? `: ${esc(x.orders.first)} to ${esc(x.orders.last)}` : `: ${esc(x.orders.first)}`}</span>` : ""}
          ${x.flag ? `<span class="sub">⚑ ${esc(x.flag)}</span>` : ""}${x.note ? `<span class="sub">📝 ${esc(x.note)}</span>` : ""}</span>
          <span class="cd-n">${usd(x.total)}</span>
          ${x.file ? `<a class="btn btn-sm btn-ghost" href="/api/invoices/${x.id}/file" target="_blank" rel="noopener">Open</a>` : ""}
          ${before ? '<span class="cd-tag t-inv">Paid</span>' : `<select data-one="${x.id}" aria-label="Paid status of ${esc(x.number)}">
            <option value="1"${isPaid(x) ? " selected" : ""}>Paid</option><option value="0"${!isPaid(x) ? " selected" : ""}>Not paid</option></select>`}
          ${x.flag ? `<button class="btn btn-sm" data-rev="${x.id}" data-write-only>Send to supplier</button>` : ""}
          ${x.paid_by && !before ? `<span class="hint">${x.marked ? "paid" : "unpaid"} · ${esc(x.paid_by.split("@")[0])}</span>` : ""}</div>`).join("")}
        <label class="cd-field">Note for this day (the AI reads it too)
          <textarea rows="2" data-daynote="${day}" placeholder="e.g. Checking 1 invoice with the supplier; will pay once sorted">${esc(note)}</textarea></label>
        <p class="hint">When the supplier sends a corrected invoice, it replaces the old one here.</p></div></td></tr>`);
    }
    $("cdDays").innerHTML = rows.join("") || '<tr><td colspan="6" class="cd-empty">No invoices yet.</td></tr>';
    $("cdSave").disabled = !dirty();
    $("cdDirty").textContent = dirty() ? "Saving…" : "Click a day to see its invoices. Paid / Not paid saves as soon as you pick it.";
  }

  $("cdDays").addEventListener("click", async (e) => {
    const rv = e.target.closest("[data-rev]");
    if (rv) {
      const x = inv.d.invoices.find((i) => String(i.id) === rv.dataset.rev);
      try {
        const r = await post("/api/suppliers/invoice-review", { id: x.id, flag: x.flag });
        cd.review = { kind: "invoice", id: x.id, text: r.text, channel: r.channel, supplier: x.supplier };
        openReview();
      } catch (err) { toast(err.message, true); }
      return;
    }
    if (e.target.closest("select, textarea, button, a")) return;
    const tr = e.target.closest("tr.day-row"); if (!tr) return;
    inv.open = inv.open === tr.dataset.d ? null : tr.dataset.d;
    drawDays();
  });
  // every Paid / Not paid pick and every day note saves at once (a separate Save button was easy to miss:
  // opening another day or tab before pressing it lost the marks)
  async function saveNow(body, msg) {
    $("cdDirty").textContent = "Saving…";
    try {
      await post("/api/suppliers/marks", body);
      for (const [id, v] of Object.entries(body.paid || {})) {
        const x = inv.d.invoices.find((i) => String(i.id) === String(id));
        if (x) { x.marked = v; x.paid = v || x.before_cutoff; x.paid_by = "you"; }
        delete inv.paid[id];
      }
      for (const day of Object.keys(body.notes || {})) {
        inv.d.day_notes[day] = { note: body.notes[day] }; delete inv.notes[day];
      }
      toast(msg);
      drawDays();
      const keepOpen = inv.open;
      await cdLoadDays();                     // fresh totals (On credit, Total payable)
      inv.open = keepOpen; drawDays();
    } catch (err) {
      toast("Not saved: " + err.message, true);
      $("cdDirty").textContent = "Not saved: " + err.message;
    }
  }
  $("cdDays").addEventListener("change", (e) => {
    const m = e.target.closest("[data-mark]"), one = e.target.closest("[data-one]"), n = e.target.closest("[data-daynote]");
    const paid = {};
    if (m && m.value) for (const x of inv.d.invoices) if (x.date === m.dataset.mark && x.date >= cutoff()) { paid[x.id] = m.value === "paid"; inv.paid[x.id] = paid[x.id]; }
    if (one) { paid[one.dataset.one] = one.value === "1"; inv.paid[one.dataset.one] = paid[one.dataset.one]; }
    if (Object.keys(paid).length) {
      drawDays();
      const k = Object.keys(paid).length, v = Object.values(paid)[0];
      saveNow({ paid }, `Saved: ${k > 1 ? k + " invoices" : "invoice"} marked ${v ? "paid" : "not paid"}.`);
    }
    if (n) saveNow({ notes: { [n.dataset.daynote]: n.value } }, "Note saved.");
  });
  $("cdDays").addEventListener("input", (e) => {
    const n = e.target.closest("[data-daynote]");
    if (!n) return;
    inv.notes[n.dataset.daynote] = n.value;
    $("cdSave").disabled = false; $("cdDirty").textContent = "Note not saved yet: click outside the box to save it";
  });
  $("cdCutoff").onchange = async () => {
    const v = $("cdCutoff").value;
    if (!v || v === inv.d.cutoff) return;
    if (!confirm(`Count every invoice dated before ${v} as paid?`)) { $("cdCutoff").value = inv.d.cutoff; return; }
    await saveNow({ cutoff: v }, "Cut-off saved.");
  };
  $("cdShowEmpty").onchange = drawDays;
  $("cdSup").onchange = () => {
    if (dirty() && !confirm("Leave without saving your changes?")) return;
    cdLoadDays();
  };
  $("cdSave").onclick = async () => {
    $("cdSave").disabled = true;
    try {
      await post("/api/suppliers/marks", { paid: inv.paid, notes: inv.notes, cutoff: inv.cutoff });
      toast("Saved. On credit and Cash flow now use these paid / not paid marks.");
      await cdLoadDays();
    } catch (err) { toast(err.message, true); $("cdSave").disabled = false; }
  };

  // ---- pop-ups close
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-cdclose]") || e.target.classList.contains("cd-modal"))
      for (const m of document.querySelectorAll(".cd-modal")) m.hidden = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") for (const m of document.querySelectorAll(".cd-modal")) m.hidden = true; });
})();
