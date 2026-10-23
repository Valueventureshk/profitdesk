/* Inbox → Bulk email: one message to many customers from the store's mailbox (bulkmail.py). Replies come back here,
   not into the SCM / CS tickets; a reply can be answered here or turned into a ticket. */
(function () {
  if (!$("bulk")) return;
  const S = { data: null, open: null, show: "all", rec: null };
  const post = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STATUS = { draft: "Draft", sending: "Sending…", sent: "Sent", stopped: "Stopped" };
  const ST_NAMES = { info_received: "Label made, not moving", pending: "No tracking update yet", in_transit: "In transit",
    exception: "Problem / exception", expired: "Tracking expired", undelivered: "Not delivered" };
  const when = (iso) => (iso ? new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z").toLocaleString(undefined,
    { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");
  const DEFAULT_BODY = `Hi {first_name},

We wanted to give you an update on your order {order}.

Our delivery partner has had a delay, so your parcel is taking longer than usual to get moving. We're sorry for the wait. It's on its way to you, and you can follow it here:
{tracking_link}

If you have any questions, just reply to this email and we'll help.

Warm regards,
{store} team`;

  window.bulkShow = async () => { await loadList(); const id = +(PDUrl.get("bulk") || 0); if (id) openCampaign(id); };

  async function loadList() {
    try { S.data = await api("/api/inbox/bulk"); } catch (e) { $("bulkList").innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
    const unread = S.data.campaigns.reduce((n, c) => n + (c.counts.unread || 0), 0);
    $("bulkBadge").hidden = !unread; $("bulkBadge").textContent = unread;
    $("bulkList").innerHTML = S.data.campaigns.length ? S.data.campaigns.map((c) => `
      <button class="bulk-item${S.open === c.id ? " on" : ""}" data-c="${c.id}">
        <b>${esc(c.name)}</b><span>${esc(c.store)} · ${STATUS[c.status] || c.status}</span>
        <span>${c.counts.sent}/${c.counts.total} sent${c.counts.replied ? ` · <em>${c.counts.replied} replied${c.counts.unread ? ` (${c.counts.unread} new)` : ""}</em>` : ""}</span></button>`).join("")
      : `<p class="hint">No bulk emails yet.</p>`;
  }
  $("bulkList").onclick = (e) => { const b = e.target.closest("[data-c]"); if (b) openCampaign(+b.dataset.c); };
  $("bulkNew").onclick = () => newForm();

  // ---------------------------------------------------------------- new
  function newForm() {
    S.open = null; PDUrl.set({ bulk: null }); loadList();
    const stores = S.data ? S.data.stores : [];
    $("bulkBody").innerHTML = `<h2>New bulk email</h2>
      <div class="bulk-grid">
        <label class="field"><span>Store</span><select id="bStore" class="control">${stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Send from</span><select id="bBox" class="control"></select></label>
      </div>
      <h4>Who gets it</h4>
      <div class="bulk-grid">
        <label class="field"><span>Customers</span><select id="bKind" class="control">
          <option value="scm_stuck">Orders whose parcel is stuck (SCM)</option><option value="orders">These order numbers</option></select></label>
        <label class="field" data-k="scm_stuck"><span>Shipped at least … days ago</span><input id="bMin" class="control" type="number" value="7" min="0" max="90"></label>
        <label class="field" data-k="scm_stuck"><span>…and at most … days ago</span><input id="bMax" class="control" type="number" value="45" min="1" max="120"></label>
      </div>
      <div class="bulk-sts" data-k="scm_stuck">${Object.entries(ST_NAMES).map(([k, v]) => `<label><input type="checkbox" value="${k}"${k === "info_received" ? " checked" : ""}> ${esc(v)}</label>`).join("")}</div>
      <label class="field" data-k="orders" hidden><span>Order numbers (comma or one per line)</span><textarea id="bOrders" class="control" rows="3"></textarea></label>
      <p class="hint">Orders already emailed in the last 7 days are left out; delivered orders never get it.</p>
      <button class="btn btn-ghost" id="bCount">Count who gets it</button> <span id="bCountOut" class="hint"></span>
      <h4>The email</h4>
      <label class="field"><span>Name (for you)</span><input id="bName" class="control" placeholder="e.g. Shoe delay 24 Oct"></label>
      <label class="field"><span>Subject</span><input id="bSubj" class="control" value="An update on your order {order}"></label>
      <label class="field"><span>Email</span><textarea id="bText" class="control" rows="12">${esc(DEFAULT_BODY)}</textarea></label>
      <p class="hint">Fields: ${(S.data?.fields || []).map((f) => `<code>{${f}}</code>`).join(" ")}. Each customer gets their own email from the store's mailbox.</p>
      <button class="btn btn-primary" id="bSave">Save draft</button>`;
    const boxes = () => {
      const st = stores.find((s) => String(s.id) === $("bStore").value) || { mailboxes: [] };
      $("bBox").innerHTML = st.mailboxes.map((m) => `<option value="${m.id}"${m.can_send ? "" : " disabled"}>${esc(m.address)}${m.can_send ? "" : " (not signed in to send)"}</option>`).join("")
        || `<option value="">No mailbox for this store</option>`;
    };
    boxes();
    $("bStore").onchange = boxes;
    $("bKind").onchange = () => document.querySelectorAll("#bulkBody [data-k]").forEach((x) => { x.hidden = x.dataset.k !== $("bKind").value; });
    const aud = () => ({ kind: $("bKind").value, min_days: +$("bMin").value, max_days: +$("bMax").value, orders: $("bOrders").value,
      statuses: [...document.querySelectorAll(".bulk-sts input:checked")].map((x) => x.value) });
    $("bCount").onclick = async () => {
      try {
        const r = await post("/api/inbox/bulk/preview", { store: +$("bStore").value, ...aud() });
        $("bCountOut").innerHTML = `<b>${r.count} customers</b>${r.sample.length ? ` · e.g. ${r.sample.slice(0, 5).map((x) => esc(x.order_name)).join(", ")}` : ""}`;
      } catch (e) { toast(e.message, true); }
    };
    $("bSave").onclick = async () => {
      try {
        const r = await post("/api/inbox/bulk", { store: +$("bStore").value, account_id: +$("bBox").value || null, name: $("bName").value,
          subject: $("bSubj").value, body: $("bText").value, audience: aud() });
        toast(`Draft saved for ${r.recipients} customers.`);
        await loadList(); openCampaign(r.id);
      } catch (e) { toast(e.message, true); }
    };
  }

  // ---------------------------------------------------------------- one campaign
  async function openCampaign(id, keep) {
    S.open = id; PDUrl.set({ bulk: id });
    let d;
    try { d = await api(`/api/inbox/bulk/${id}?show=${S.show}`); } catch (e) { toast(e.message, true); return; }
    loadList();
    const c = d.campaign, n = c.counts;
    const draft = c.status === "draft", canSend = (c.mailboxes || []).some((m) => m.id === c.account_id && m.can_send);
    $("bulkBody").innerHTML = `<div class="bulk-head"><div><h2>${esc(c.name)}</h2>
        <p class="hint">${esc(c.store)} · ${STATUS[c.status] || c.status} · ${n.sent} of ${n.total} sent${n.failed ? ` · <span class="neg">${n.failed} failed</span>` : ""}
          · ${n.replied} replied${n.tickets ? ` · ${n.tickets} made into tickets` : ""}</p></div>
        <div class="bulk-actions">
          ${draft || c.status === "stopped" ? `<button class="btn btn-ghost" id="bTest" data-write-only>Send me a test</button>
            <button class="btn btn-primary" id="bGo" data-manage-only ${canSend ? "" : "disabled title='Sign the mailbox in to send first'"}>${draft ? `Send to ${n.queued} customers` : `Carry on (${n.queued} left)`}</button>` : ""}
          ${c.status === "sending" ? `<button class="btn btn-danger" id="bStop">Stop</button>` : ""}
          ${draft ? `<button class="btn btn-ghost" id="bDel" data-write-only>Delete draft</button>` : ""}</div></div>
      ${!canSend ? `<div class="warn">This store's mailbox isn't signed in for sending. Settings → Support mailboxes → Sign in to send.</div>` : ""}
      <details class="bulk-prev"${draft ? " open" : ""}><summary>The email (as the first customer gets it)</summary>
        <div class="bulk-mail"><b>${esc(c.preview.subject)}</b><pre>${esc(c.preview.body)}</pre></div></details>
      <div class="bulk-filter">${["all", "replied", "unread", "failed"].map((f) => `<button data-f="${f}" class="${S.show === f ? "on" : ""}">${{ all: "Everyone", replied: "Replied", unread: "New replies", failed: "Failed" }[f]}</button>`).join("")}</div>
      <div class="bulk-split"><div class="table-wrap"><table class="table bulk-tbl"><thead><tr><th>Order</th><th>Customer</th><th>Status</th><th>Reply</th></tr></thead><tbody>
        ${d.recipients.map((r) => `<tr class="clickable${S.rec === r.id ? " on" : ""}${r.replies && (!r.read_at || r.read_at < r.last_reply_at) ? " unread" : ""}" data-r="${r.id}">
          <td><b>${esc(r.order_name || "")}</b></td><td>${esc(r.name || "")}<span class="sub">${esc(r.email)}</span></td>
          <td>${{ queued: "Waiting", sent: "Sent " + esc(when(r.sent_at)), failed: `<span class="neg" title="${esc(r.error || "")}">Failed</span>`, skipped: "Skipped" }[r.status] || r.status}</td>
          <td>${r.replies ? `<b>${r.replies}</b> ${esc(when(r.last_reply_at))}${r.ticket_id ? ` · ticket` : ""}` : `<span class="hint">—</span>`}</td></tr>`).join("") || `<tr><td class="hint">Nobody here.</td></tr>`}
      </tbody></table></div><div class="bulk-conv" id="bConv"><p class="hint">Click a customer to see their replies.</p></div></div>`;
    const t = $("bTest");
    if (t) t.onclick = async () => { try { const r = await post(`/api/inbox/bulk/${id}/test`, {}); toast(`Test sent to ${r.to}.`); } catch (e) { toast(e.message, true); } };
    const g = $("bGo");
    if (g) g.onclick = async () => {
      if (!confirm(`Send "${c.name}" to ${n.queued} customers now from ${(c.mailboxes.find((m) => m.id === c.account_id) || {}).address}? One email every 2 seconds.`)) return;
      try { await post(`/api/inbox/bulk/${id}/send`, {}); toast("Sending…"); openCampaign(id); } catch (e) { toast(e.message, true); }
    };
    const st = $("bStop");
    if (st) st.onclick = async () => { await post(`/api/inbox/bulk/${id}/stop`, {}); openCampaign(id); };
    const del = $("bDel");
    if (del) del.onclick = async () => { if (!confirm("Delete this draft?")) return; await api(`/api/inbox/bulk/${id}`, { method: "DELETE" }); S.open = null; loadList(); $("bulkBody").innerHTML = ""; };
    $("bulkBody").querySelector(".bulk-filter").onclick = (e) => { const b = e.target.closest("[data-f]"); if (b) { S.show = b.dataset.f; openCampaign(id); } };
    $("bulkBody").querySelector(".bulk-tbl").onclick = (e) => { const tr = e.target.closest("[data-r]"); if (tr) openRecipient(+tr.dataset.r); };
    if (S.rec && keep) openRecipient(S.rec);
    if (c.status === "sending") setTimeout(() => { if (S.open === id && !$("bulk").hidden) openCampaign(id, true); }, 5000);
  }

  async function openRecipient(rid) {
    S.rec = rid;
    let d;
    try { d = await api(`/api/inbox/bulk/recipient/${rid}`); } catch (e) { toast(e.message, true); return; }
    const r = d.recipient;
    for (const tr of document.querySelectorAll(".bulk-tbl tr[data-r]")) tr.classList.toggle("on", +tr.dataset.r === rid);
    $("bConv").innerHTML = `<h3>${esc(r.name || r.email)} · ${esc(r.order_name || "")}</h3>
      ${d.messages.length ? d.messages.map((m) => `<div class="bulk-msg ${m.direction}"><b>${m.direction === "out" ? "Us" : esc(m.from_name || m.from_addr)}</b>
        <span class="hint">${esc(when(m.date))}</span><pre>${esc((m.text || "").slice(0, 4000))}</pre></div>`).join("")
        : `<p class="hint">${r.status === "sent" ? "Sent; no reply yet." : "Not sent yet."}</p>`}
      ${r.status === "sent" ? `<label class="field"><span>Reply</span><textarea id="bRe" class="control" rows="4"></textarea></label>
        <button class="btn btn-primary btn-sm" id="bReGo" data-write-only>Send reply</button>
        ${r.ticket_id ? `<a class="btn btn-ghost btn-sm" href="/inbox?ticket=${r.ticket_id}">Open ticket</a>`
          : r.replies ? `<button class="btn btn-ghost btn-sm" data-tk="SCM" data-write-only>Make SCM ticket</button>
            <button class="btn btn-ghost btn-sm" data-tk="CS" data-write-only>Make CS ticket</button>` : ""}` : ""}`;
    const go = $("bReGo");
    if (go) go.onclick = async () => {
      try { await post(`/api/inbox/bulk/recipient/${rid}/reply`, { text: $("bRe").value }); toast("Reply sent."); setTimeout(() => openRecipient(rid), 6000); }
      catch (e) { toast(e.message, true); }
    };
    for (const b of $("bConv").querySelectorAll("[data-tk]")) b.onclick = async () => {
      try { const t = await post(`/api/inbox/bulk/recipient/${rid}/ticket`, { label: b.dataset.tk }); toast(`Ticket made (${b.dataset.tk}).`); openRecipient(rid); }
      catch (e) { toast(e.message, true); }
    };
  }

  // keep the badge current
  setInterval(() => { if (!document.hidden) loadList(); }, 60000);
  if (PDUrl.get("tab") === "bulk") window.bulkShow(); else loadList();
})();
