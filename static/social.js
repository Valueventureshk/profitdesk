/* Inbox → Comments & Messages: Facebook Page comments (posts + ads) and Messenger chats.
   Uses inbox.js's helpers ($, esc, api, post, toast, when). Every figure and status comes from the server. */
(function () {
  let sview = "needs_reply";
  let overview = null;
  let currentItem = null;          // {type: "c"|"chat", id}
  let tab = "mail";

  const put = (path, data) => api(path, { method: "PUT", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify(data) });
  const isChats = () => sview.startsWith("chats_");
  const isSetup = () => sview === "pages" || sview === "words";

  function scopeQs() {
    const v = $("socialStore").value || "";
    if (v.startsWith("s:")) return `&store=${v.slice(2)}`;
    if (v.startsWith("p:")) return `&page=${encodeURIComponent(v.slice(2))}`;
    return "";
  }

  // ---------------------------------------------------------------- tabs
  function setTab(t, quiet) {
    tab = t;
    document.body.classList.toggle("tab-social", t === "social");
    document.body.classList.toggle("tab-bulk", t === "bulk");
    for (const b of $("inboxTabs").querySelectorAll("[data-tab]")) b.classList.toggle("on", b.dataset.tab === t);
    $("mailMain").hidden = t !== "mail";
    $("social").hidden = t !== "social";
    if ($("bulk")) $("bulk").hidden = t !== "bulk";
    if (!quiet) PDUrl.set({ tab: t === "mail" ? null : t });
    if (t === "social") { loadOverview().then(() => loadView()); }
    else if (t === "bulk") { if (window.bulkShow) window.bulkShow(); }
    else if (typeof refresh === "function") refresh();
  }
  $("inboxTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b && b.dataset.tab !== tab) setTab(b.dataset.tab);
  });

  // ---------------------------------------------------------------- overview
  async function loadOverview() {
    try {
      overview = await api(`/api/inbox/social?x=1${scopeQs()}`);
    } catch (e) { if (tab === "social") $("sub").textContent = e.message; return; }
    const c = overview.counts || {};
    for (const b of document.querySelectorAll("[data-scount]")) b.textContent = c[b.dataset.scount] || "";
    const n = (c.needs_reply || 0) + (c.chats_open || 0);
    $("socialBadge").hidden = !n;
    $("socialBadge").textContent = n;
    fillStores();
    if (tab === "social") {
      const working = overview.pages.filter((p) => p.state === "ok").length;
      $("sub").textContent = !overview.on ? "Switched off (Setup → Pages & stores)"
        : `${c.needs_reply || 0} comments need a reply · ${c.chats_open || 0} chats waiting · ${working} of ${overview.pages.length} Pages working`;
    }
  }

  function fillStores() {
    const sel = $("socialStore"), was = sel.value || PDUrl.get("sscope") || "";
    const byStore = new Map();
    for (const p of overview.pages) {
      const k = p.store_id || 0;
      if (!byStore.has(k)) byStore.set(k, []);
      byStore.get(k).push(p);
    }
    let html = `<option value="">All Pages</option>`;
    const names = new Map(overview.stores.map((s) => [s.id, s.name]));
    for (const [sid, pages] of [...byStore.entries()].sort((a, b) => String(names.get(a[0]) || "~").localeCompare(String(names.get(b[0]) || "~")))) {
      html += `<optgroup label="${esc(sid ? names.get(sid) || `Store ${sid}` : "No store yet")}">`;
      if (sid && pages.length > 1) html += `<option value="s:${sid}">All of ${esc(names.get(sid) || "")}</option>`;
      html += pages.map((p) => `<option value="p:${esc(p.page_id)}">${esc(p.name)}</option>`).join("") + `</optgroup>`;
    }
    sel.innerHTML = html;
    if ([...sel.options].some((o) => o.value === was)) sel.value = was;
  }
  $("socialStore").onchange = () => { PDUrl.set({ sscope: $("socialStore").value }); loadOverview(); loadView(); };

  // ---------------------------------------------------------------- nav
  $("snav").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sview]");
    if (!b) return;
    sview = b.dataset.sview;
    for (const x of $("snav").querySelectorAll("[data-sview]")) x.classList.toggle("on", x === b);
    PDUrl.set({ sview: sview === "needs_reply" ? null : sview, sitem: null });
    currentItem = null;
    loadView();
  });
  let searchTimer;
  $("socialSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadView, 300); };

  function loadView() {
    $("social").classList.toggle("training", isSetup());
    if (sview === "pages") return showPages();
    if (sview === "words") return showWords();
    loadList();
    if (!currentItem) $("sread").innerHTML = `<p class="hint inbox-empty">${isChats() ? "Pick a chat." : "Pick a comment."}</p>`;
  }

  // ---------------------------------------------------------------- lists
  const kindChip = (k) => (k === "ad" ? `<span class="tk tk-ad">Ad</span>` : k === "post" ? `<span class="tk tk-post">Post</span>` : "");
  function hiddenChip(c) {
    if (!c.is_hidden) return c.matched && !c.keep ? `<span class="tk tk-word" title="Contains a hide word">“${esc(c.matched)}”</span>` : "";
    const who = c.hidden_by === "auto" ? `auto · “${esc(c.hidden_reason || "")}”` : c.hidden_by ? `by ${esc(c.hidden_by)}` : "on Facebook";
    return `<span class="tk tk-hidden" title="Hidden: only the writer and their friends see it">Hidden · ${who}</span>`;
  }
  const commentRow = (c) => `
    <button class="mail-row${currentItem?.type === "c" && currentItem.id === c.id ? " on" : ""}" data-cid="${esc(c.id)}">
      <span class="mail-top"><strong>${esc(c.from_name || "Facebook user")}</strong><small>${when(sview === "hidden" ? c.hidden_at || c.created_time : c.created_time)}</small></span>
      <span class="soc-snip">${esc(c.message || (c.attachment ? "(photo / sticker)" : ""))}</span>
      <span class="mail-tags">${kindChip(c.post_kind)}${hiddenChip(c)}${c.page_replied || c.replied_at ? `<span class="tk tk-them">Replied</span>` : ""}${c.done_at ? `<span class="tk tk-done">Done</span>` : ""}<span class="mail-store">${esc(c.store ? `${c.store} · ${c.page}` : c.page)}</span></span>
    </button>`;
  const chatRow = (v) => `
    <button class="mail-row${currentItem?.type === "chat" && currentItem.id === v.id ? " on" : ""}" data-chat="${esc(v.id)}">
      <span class="mail-top"><strong>${esc(v.user_name || "Messenger user")}</strong><small>${when(v.updated_time)}</small></span>
      <span class="soc-snip">${esc(v.snippet || "")}</span>
      <span class="mail-tags">${(v.last_customer_at || "") > (v.last_page_at || "") && !v.done_at ? `<span class="tk tk-us">Waiting on us</span>` : ""}${v.window_open ? "" : `<span class="tk tk-done" title="Meta allows replies within 24 hours of the customer's last message">24 h passed</span>`}<span class="mail-store">${esc(v.store ? `${v.store} · ${v.page}` : v.page)}</span></span>
    </button>`;

  function emptyHint() {
    if (!overview) return "Nothing here.";
    if (!overview.connections) return "No Meta connection yet. The owner connects Meta in Settings → Meta.";
    const working = overview.pages.filter((p) => p.state === "ok");
    if (!working.length) return `No Page can be read yet. See <a href="#" data-goto="pages">Setup → Pages &amp; stores</a> for what each Page needs.`;
    return "Nothing here.";
  }

  async function loadList() {
    const q = encodeURIComponent($("socialSearch").value.trim());
    const want = sview;
    let html;
    try {
      if (isChats()) {
        const d = await api(`/api/inbox/social/chats?view=${sview === "chats_open" ? "open" : "all"}&q=${q}${scopeQs()}`);
        html = d.chats.map(chatRow).join("");
      } else {
        const view = sview === "ads" ? "all" : sview, kind = sview === "ads" ? "&kind=ad" : "";
        const d = await api(`/api/inbox/social/comments?view=${view}${kind}&q=${q}${scopeQs()}`);
        html = (sview === "hidden" ? `<div class="list-sep">Hidden in Facebook, newest first</div>` : "")
          + (sview === "matches" ? `<div class="list-sep">Written before auto-hide was on · still visible</div>` : "")
          + d.comments.map(commentRow).join("");
        if (sview === "matches" && d.comments.length && overview?.can?.manage) {
          html = `<div class="soc-bar"><button class="btn btn-sm" id="hideOlder">Hide these on every Page</button></div>` + html;
        }
      }
    } catch (e) { $("slist").innerHTML = `<p class="hint inbox-empty">${esc(e.message)}</p>`; return; }
    if (want !== sview) return;
    $("slist").innerHTML = html || `<p class="hint inbox-empty">${emptyHint()}</p>`;
    for (const b of $("slist").querySelectorAll("[data-cid]")) b.onclick = () => openComment(b.dataset.cid);
    for (const b of $("slist").querySelectorAll("[data-chat]")) b.onclick = () => openChat(b.dataset.chat);
    wireGoto($("slist"));
    if ($("hideOlder")) $("hideOlder").onclick = hideOlder;
  }

  function wireGoto(root) {
    for (const a of root.querySelectorAll("[data-goto]")) {
      a.onclick = (e) => { e.preventDefault(); $("snav").querySelector(`[data-sview="${a.dataset.goto}"]`).click(); };
    }
  }

  async function hideOlder() {
    const pages = overview.pages.filter((p) => p.features.moderate && p.state === "ok");
    if (!pages.length) return toast("No Page has the hide permission yet.", true);
    if (!confirm("Hide the visible comments of the last 30 days that contain a hide word?")) return;
    let n = 0;
    for (const p of pages) {
      try { n += (await post(`/api/inbox/social/pages/${encodeURIComponent(p.page_id)}/hide-older`, {})).count; }
      catch (e) { toast(e.message, true); }
    }
    toast(`Hiding ${n} comment${n === 1 ? "" : "s"} in the background…`);
    setTimeout(() => { loadOverview(); loadList(); }, 6000);
  }

  const toReader = () => { if (window.innerWidth <= 780) $("sread").scrollIntoView({ block: "start" }); };
  function markOn() {
    for (const b of $("slist").querySelectorAll(".mail-row")) {
      b.classList.toggle("on", (currentItem?.type === "c" && b.dataset.cid === currentItem.id)
        || (currentItem?.type === "chat" && b.dataset.chat === currentItem.id));
    }
  }

  // ---------------------------------------------------------------- one comment
  const needNote = (page, feat) => {
    const miss = (overview?.need?.[feat] || []).filter((p) => (page.missing || []).includes(p));
    return `<p class="notice soc-need">Needs the new token${miss.length ? ` (${miss.map(esc).join(", ")})` : ""}. The owner adds it in Meta Business Settings and pastes the new token in Settings → Meta.</p>`;
  };

  async function openComment(id) {
    currentItem = { type: "c", id };
    PDUrl.set({ sitem: `c:${id}` });
    markOn();
    $("sread").innerHTML = `<p class="hint">Loading…</p>`;
    let d;
    try { d = await api(`/api/inbox/social/comments/${encodeURIComponent(id)}`); }
    catch (e) { $("sread").innerHTML = `<p class="notice">${esc(e.message)}</p>`; return; }
    if (currentItem?.id !== id) return;
    toReader();
    const c = d.comment, page = d.page || {}, feats = page.features || {};
    const canMod = feats.moderate && page.state !== "not_assigned";
    const p = d.post;
    const logs = d.log.map((l) => `<li>${when(l.at)} · ${l.action === "hide" ? "Hidden" : "Unhidden"} ${l.by === "auto" ? "automatically" : `by ${esc(l.by)}`}${l.reason ? ` · ${esc(l.reason)}` : ""}${l.ok ? "" : ` · <span class="soc-err">failed: ${esc(l.error || "")}</span>`}</li>`).join("");
    const msg = (t) => `
      <div class="msg ${t.by_page ? "out" : "in"}${t.id === c.id ? " soc-sel" : ""}${t.is_hidden ? " soc-hid" : ""}" data-tid="${esc(t.id)}">
        <div class="msg-head"><strong>${esc(t.from_name || (t.by_page ? page.name : "Facebook user"))}</strong>
          <small>${when(t.created_time)}${t.by_page && t.replied_by ? ` · sent by ${esc(t.replied_by)}` : ""}</small></div>
        <div class="mail-body">${esc(t.message || "")}</div>
        ${t.attachment ? `<a href="${esc(t.attachment)}" target="_blank" rel="noopener"><img class="soc-att" src="${esc(t.attachment)}" alt="" loading="lazy"></a>` : ""}
        <div class="soc-msgbar">${hiddenChip(t)}
          ${!t.by_page && canMod ? `<button class="btn btn-sm btn-ghost" data-write-only data-hide="${esc(t.id)}" data-to="${t.is_hidden ? 0 : 1}">${t.is_hidden ? "Unhide" : "Hide"}</button>` : ""}
          ${t.permalink ? `<a class="soc-link" href="${esc(t.permalink)}" target="_blank" rel="noopener">Facebook ↗</a>` : ""}</div>
      </div>`;
    $("sread").innerHTML = `
      <div class="soc-head">
        <div><h2 class="mail-h">Comment on ${c.post_kind === "ad" ? "an ad" : "a post"}</h2>
          <div class="mail-meta">${esc(c.store ? `${c.store} · ` : "")}${esc(c.page)}</div></div>
        <div class="soc-actions" data-write-only>
          ${c.done_at ? `<button class="btn btn-sm btn-ghost" id="cUndone">Not done</button>` : `<button class="btn btn-sm" id="cDone" title="No reply needed">Done</button>`}
        </div>
      </div>
      ${p ? `<div class="soc-post">${p.picture ? `<img src="${esc(p.picture)}" alt="" loading="lazy">` : ""}
        <div><span class="tk ${p.kind === "ad" ? "tk-ad" : "tk-post"}">${p.kind === "ad" ? "Ad" : "Post"}</span>
          <p>${esc((p.message || "").slice(0, 300)) || "<i>(no text)</i>"}</p>
          ${p.permalink ? `<a class="soc-link" href="${esc(p.permalink)}" target="_blank" rel="noopener">Open on Facebook ↗</a>` : ""}</div></div>` : ""}
      <div class="soc-thread">${d.thread.map(msg).join("")}</div>
      ${logs ? `<details class="soc-log" open><summary>Hide / unhide history</summary><ul>${logs}</ul></details>` : ""}
      ${canMod ? `<div class="reply-box" data-write-only>
          <textarea id="cReply" class="control reply-text" rows="4" placeholder="Reply as ${esc(page.name || "the Page")}…  (Ctrl/⌘ + Enter sends)"></textarea>
          <div class="reply-bar">
            ${c.can_reply_privately && feats.messaging && !c.private_replied ? `<label class="tr-auto"><input type="checkbox" id="cPrivate"> Send as a private Messenger message instead</label>` : ""}
            ${c.private_replied ? `<span class="hint">A private message was already sent for this comment.</span>` : ""}
            <span class="reply-gap"></span>
            <button class="btn btn-primary" id="cSend">Reply</button>
          </div>
          <p class="hint reply-note">Posts publicly under the comment, as the Page. Replies are written by people here; nothing is sent automatically.</p>
        </div>` : needNote(page, page.state === "not_assigned" ? "read" : "moderate")}`;
    for (const b of $("sread").querySelectorAll("[data-hide]")) {
      b.onclick = async () => {
        const hide = b.dataset.to === "1";
        b.disabled = true;
        try {
          await post(`/api/inbox/social/comments/${encodeURIComponent(b.dataset.hide)}/hide`, { hidden: hide });
          toast(hide ? "Hidden on Facebook." : "Unhidden. It won't be hidden automatically again.");
          openComment(id); loadList(); loadOverview();
        } catch (e) { toast(e.message, true); b.disabled = false; }
      };
    }
    const done = async (v) => {
      try { await post(`/api/inbox/social/comments/${encodeURIComponent(id)}/done`, { done: v }); loadList(); loadOverview(); openComment(id); }
      catch (e) { toast(e.message, true); }
    };
    if ($("cDone")) $("cDone").onclick = () => done(true);
    if ($("cUndone")) $("cUndone").onclick = () => done(false);
    if ($("cSend")) {
      const send = async () => {
        const text = $("cReply").value.trim();
        if (!text) return;
        $("cSend").disabled = true;
        try {
          const priv = !!$("cPrivate")?.checked;
          await post(`/api/inbox/social/comments/${encodeURIComponent(id)}/reply`, { text, private: priv });
          toast(priv ? "Private message sent." : "Reply posted.");
          openComment(id); loadList(); loadOverview();
        } catch (e) { toast(e.message, true); $("cSend").disabled = false; }
      };
      $("cSend").onclick = send;
      $("cReply").onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); };
    }
  }

  // ---------------------------------------------------------------- one chat
  async function openChat(id) {
    currentItem = { type: "chat", id };
    PDUrl.set({ sitem: `m:${id}` });
    markOn();
    $("sread").innerHTML = `<p class="hint">Loading…</p>`;
    let d;
    try { d = await api(`/api/inbox/social/chats/${encodeURIComponent(id)}`); }
    catch (e) { $("sread").innerHTML = `<p class="notice">${esc(e.message)}</p>`; return; }
    if (currentItem?.id !== id) return;
    toReader();
    const v = d.chat, page = d.page || {};
    const att = (a) => (/^image\//.test(a.type) || (!a.type && a.url) ? `<a href="${esc(a.url)}" target="_blank" rel="noopener"><img class="soc-att" src="${esc(a.url)}" alt="" loading="lazy"></a>`
      : `<a class="soc-link" href="${esc(a.url)}" target="_blank" rel="noopener">📎 ${esc(a.name || "file")}</a>`);
    $("sread").innerHTML = `
      <div class="soc-head">
        <div><h2 class="mail-h">${esc(v.user_name || "Messenger user")}</h2>
          <div class="mail-meta">Messenger · ${esc(v.store ? `${v.store} · ` : "")}${esc(v.page)}</div></div>
        <div class="soc-actions" data-write-only>
          ${v.done_at ? `<button class="btn btn-sm btn-ghost" id="mUndone">Not done</button>` : `<button class="btn btn-sm" id="mDone" title="No reply needed">Done</button>`}
        </div>
      </div>
      <div class="soc-thread">${d.messages.map((m) => `
        <div class="msg ${m.by_page ? "out" : "in"}">
          <div class="msg-head"><strong>${esc(m.by_page ? page.name || "Page" : m.from_name || v.user_name || "Customer")}</strong>
            <small>${when(m.created_time)}${m.sent_by ? ` · sent by ${esc(m.sent_by)}` : ""}</small></div>
          ${m.text ? `<div class="mail-body">${esc(m.text)}</div>` : ""}
          ${(m.attachments || []).map(att).join("")}
        </div>`).join("") || `<p class="hint">No messages read yet.</p>`}</div>
      ${!page.features?.messaging ? needNote(page, "messaging")
        : v.window_open ? `<div class="reply-box" data-write-only>
          <textarea id="mReply" class="control reply-text" rows="4" placeholder="Message ${esc(v.user_name || "the customer")}…  (Ctrl/⌘ + Enter sends)"></textarea>
          <div class="reply-bar"><span class="reply-gap"></span><button class="btn btn-primary" id="mSend">Send</button></div>
          <p class="hint reply-note">Sends in Messenger as ${esc(page.name || "the Page")}.</p></div>`
        : `<p class="notice soc-need">More than 24 hours since the customer's last message, so Meta doesn't let a Page reply from here. Reply in Meta Business Suite, or wait until they write again.</p>`}`;
    const done = async (val) => {
      try { await post(`/api/inbox/social/chats/${encodeURIComponent(id)}/done`, { done: val }); loadList(); loadOverview(); openChat(id); }
      catch (e) { toast(e.message, true); }
    };
    if ($("mDone")) $("mDone").onclick = () => done(true);
    if ($("mUndone")) $("mUndone").onclick = () => done(false);
    if ($("mSend")) {
      const send = async () => {
        const text = $("mReply").value.trim();
        if (!text) return;
        $("mSend").disabled = true;
        try { await post(`/api/inbox/social/chats/${encodeURIComponent(id)}/reply`, { text }); toast("Sent."); openChat(id); loadList(); loadOverview(); }
        catch (e) { toast(e.message, true); $("mSend").disabled = false; }
      };
      $("mSend").onclick = send;
      $("mReply").onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); };
    }
    const th = $("sread").querySelector(".soc-thread");
    if (th && th.lastElementChild) th.lastElementChild.scrollIntoView({ block: "nearest" });
  }

  // ---------------------------------------------------------------- setup: pages
  const FEAT = [["read", "Read comments"], ["moderate", "Hide & reply"], ["messaging", "Messenger"], ["webhook", "Instant updates"]];
  function stateChip(p) {
    if (p.state === "ok") return `<span class="tk tk-them">Working</span>`;
    if (p.state === "needs_token") return `<span class="tk tk-us">Needs the new token</span>`;
    if (p.state === "not_assigned") return `<span class="tk tk-us">Not given to the System User</span>`;
    return `<span class="tk tk-esc" title="${esc(p.error || "")}">Error</span>`;
  }

  async function showPages() {
    await loadOverview();
    if (sview !== "pages" || !overview) return;
    const can = overview.can || {};
    const opts = (sel) => `<option value="">No store</option>` + overview.stores.map((s) => `<option value="${s.id}"${s.id === sel ? " selected" : ""}>${esc(s.name)}</option>`).join("");
    const rows = overview.pages.map((p) => `
      <div class="soc-page" data-page="${esc(p.page_id)}">
        <div class="soc-page-name">${p.picture ? `<img src="${esc(p.picture)}" alt="">` : `<span class="soc-pic"></span>`}
          <div><strong>${esc(p.name)}</strong><br>${stateChip(p)}</div></div>
        <div class="soc-page-store"><label class="hint">Store</label>
          ${can.manage ? `<select class="control" data-store>${opts(p.store_id)}</select>` : `<span>${esc(p.store || "—")}</span>`}
          ${p.store_by === "auto" && p.store_id ? `<small class="hint">guessed from the ad account</small>` : ""}</div>
        <div class="soc-feats">${FEAT.map(([k, n]) => `<span class="soc-feat ${p.features[k] ? "yes" : "no"}">${p.features[k] ? "✓" : "✗"} ${n}</span>`).join("")}
          ${p.missing.length ? `<div class="hint soc-miss">Missing on its token: ${p.missing.map(esc).join(", ")}</div>` : ""}
          ${p.state === "not_assigned" ? `<div class="hint soc-miss">Its ad account can advertise as this Page, but no connected token manages it.</div>` : ""}
          ${p.error ? `<div class="soc-err">${esc(p.error)}</div>` : ""}${p.msg_error ? `<div class="soc-err">Messenger: ${esc(p.msg_error)}</div>` : ""}</div>
        <div class="soc-page-sw">
          <label class="tr-auto"><input type="checkbox" data-autohide${p.autohide ? " checked" : ""}${can.manage ? "" : " disabled"}> Auto-hide</label>
          <label class="tr-auto"><input type="checkbox" data-sync${p.sync_on ? " checked" : ""}${can.manage ? "" : " disabled"}> Sync</label>
          <small class="hint">${p.synced_at ? `Read ${when(p.synced_at)}` : p.state === "ok" ? "First read soon (90 days)" : ""}</small>
        </div>
      </div>`).join("");
    const bad = overview.pages.some((p) => p.state !== "ok");
    $("sread").innerHTML = `<div class="soc-setup">
      <div class="soc-head"><div><h2 class="mail-h">Facebook Pages</h2>
        <p class="hint">Comments on posts and ads, and Messenger chats, from every Page EcomVital can reach. Checked every minute.</p></div>
        <div class="soc-actions">
          ${can.manage ? `<label class="tr-auto soc-master"><input type="checkbox" id="sOn"${overview.on ? " checked" : ""}> On</label>` : ""}
          <button class="btn btn-sm" id="sSync" data-write-only>Check now</button></div></div>
      ${overview.conn_errors.length ? `<div class="notice">${overview.conn_errors.map(esc).join("<br>")}</div>` : ""}
      ${overview.pages.length ? rows : `<p class="notice">No Pages found yet. ${overview.connections ? "Press Check now." : "Connect Meta in Settings → Meta first."}</p>`}
      ${bad && can.owner ? `<div class="soc-howto"><h3>To switch on the Pages that aren't working</h3><ol>
        <li>Open <b>business.facebook.com → Settings → Users → System users</b> and pick the system user of that business.</li>
        <li><b>Assign assets → Pages</b>: tick each Page (e.g. Perth Boutique, Maggie Whitfield) with <b>full control</b> (or at least “Content”, “Messages” and “Community activity”).</li>
        <li><b>Generate new token</b> for the EcomVital app with these ticked: ${["ads_management", "ads_read", "business_management", "pages_show_list", "pages_read_engagement", "pages_read_user_content", "pages_manage_engagement", "pages_messaging", "pages_manage_metadata", "pages_manage_ads"].map((x) => `<code>${x}</code>`).join(" ")}.</li>
        <li>Paste it in <b>Settings → Meta</b> (same business = it replaces the old one), then come back here and press <b>Check now</b>.</li></ol></div>` : ""}
      ${overview.webhook && can.owner ? `<details class="soc-log"><summary>Instant updates (optional, owner)</summary>
        <p class="hint">Without this, EcomVital still checks every minute. To make it instant: Meta app → Webhooks → Page → callback URL and verify token below, fields <code>feed</code> and <code>messages</code>.</p>
        <div class="scm-webhook"><code>${esc(overview.webhook.url)}</code></div>
        <div class="scm-webhook"><code class="scm-secret">${esc(overview.webhook.verify_token)}</code><button class="btn btn-sm btn-ghost" id="copyVt">Copy token</button></div>
      </details>` : ""}
      ${overview.last_sync ? `<p class="hint">Last check ${when(overview.last_sync)}${overview.running ? " · checking now…" : ""}</p>` : ""}
    </div>`;
    for (const row of $("sread").querySelectorAll("[data-page]")) {
      const pid = encodeURIComponent(row.dataset.page);
      const save = async (data, msg) => { try { await put(`/api/inbox/social/pages/${pid}`, data); toast(msg); loadOverview(); } catch (e) { toast(e.message, true); showPages(); } };
      const st = row.querySelector("[data-store]");
      if (st) st.onchange = () => save({ store_id: st.value ? +st.value : null }, "Store saved.");
      row.querySelector("[data-autohide]").onchange = (e) => save({ autohide: e.target.checked }, e.target.checked ? "Auto-hide on (from now)." : "Auto-hide off.");
      row.querySelector("[data-sync]").onchange = (e) => save({ sync_on: e.target.checked }, e.target.checked ? "Syncing." : "Not syncing this Page.");
    }
    if ($("sOn")) $("sOn").onchange = async (e) => { try { await put("/api/inbox/social/settings", { on: e.target.checked }); toast(e.target.checked ? "Switched on." : "Switched off: nothing is read or hidden."); loadOverview(); } catch (err) { toast(err.message, true); } };
    if ($("sSync")) $("sSync").onclick = async () => {
      try { await post("/api/inbox/social/sync", {}); toast("Checking every Page in the background…"); setTimeout(showPages, 8000); } catch (e) { toast(e.message, true); }
    };
    if ($("copyVt")) $("copyVt").onclick = () => { navigator.clipboard?.writeText(overview.webhook.verify_token); toast("Copied."); };
  }

  // ---------------------------------------------------------------- setup: hide words + learning
  let learnTimer;
  async function showWords() {
    await loadOverview();
    if (sview !== "words" || !overview) return;
    const can = overview.can || {};
    let learnt = null;
    try { learnt = await api("/api/inbox/social/learn"); } catch { /* shown as empty */ }
    const ws = overview.words || [];
    const r = learnt?.result;
    const pagesOk = overview.pages.filter((p) => p.features.read && p.state !== "not_assigned");
    $("sread").innerHTML = `<div class="soc-setup">
      <h2 class="mail-h">Hide words</h2>
      <p class="hint">A new comment with any of these is hidden on Facebook straight away (the writer and their friends still see it, nobody else does). Whole words only; “rip off” also catches ripoff and rip-off; a word ending in * catches longer forms (scammer* = scammers); spellings like sc@m are caught too. People can unhide any comment; one unhidden by a person is never hidden again.</p>
      <div class="soc-words">${ws.map((w, i) => `<span class="soc-word">${esc(w)}${can.manage ? `<button title="Remove" data-rm="${i}">×</button>` : ""}</span>`).join("") || `<span class="hint">No words: nothing is hidden automatically.</span>`}</div>
      ${can.manage ? `<div class="soc-add"><input class="control" id="wAdd" placeholder="Add words, comma separated (e.g. scam*, rip off)"><button class="btn btn-sm" id="wAddBtn">Add</button>
        <button class="btn btn-sm btn-ghost" id="wReset" title="Back to the starter list">Starter list</button></div>` : ""}
      <h3>Would this comment be hidden?</h3>
      <div class="soc-add"><input class="control" id="wTest" placeholder="Paste a comment"><span id="wTestOut" class="hint"></span></div>
      <h3>Learn from what the team has hidden</h3>
      <p class="hint">Reads the last 90 days of comments that were hidden on Facebook by your team (not by EcomVital) and finds the words that keep coming back in them but rarely in normal comments.</p>
      ${can.manage ? `<div class="soc-add"><select class="control" id="lPage"><option value="">All Pages</option>${pagesOk.map((p) => `<option value="${esc(p.page_id)}"${/perth/i.test(p.name) ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
        <button class="btn btn-sm" id="lRun"${learnt?.running ? " disabled" : ""}>${learnt?.running ? "Learning…" : "Learn now"}</button>
        <span class="hint" id="lStep">${esc(learnt?.step || "")}</span></div>` : ""}
      ${!pagesOk.length ? `<p class="notice soc-need">No Page can be read yet, so there is nothing to learn from. It starts once a token has pages_read_user_content.</p>` : ""}
      ${r ? `<p class="hint">Last learned ${when(r.at)}: ${r.hidden} hidden comments vs ${r.visible} visible ones · the current list already catches ${r.hidden ? Math.round(r.caught_by_list / r.hidden * 100) : 0}% of the hidden ones.</p>
        ${r.suggestions.length ? `<div class="soc-sugg">${r.suggestions.map((s, i) => `<label class="soc-sug"><input type="checkbox" data-sg="${i}"${can.manage ? "" : " disabled"}>
          <b>${esc(s.term)}</b> <small>in ${s.hidden} hidden · ${s.visible} visible</small>
          ${s.examples.map((x) => `<span class="soc-ex">“${esc(x)}”</span>`).join("")}</label>`).join("")}</div>
          ${can.manage ? `<button class="btn btn-sm btn-primary" id="lAccept">Add ticked words to the list</button>` : ""}`
        : `<p class="hint">No new words to suggest.</p>`}` : ""}
    </div>`;
    const saveWords = async (list, msg) => {
      try { await put("/api/inbox/social/settings", { words: list }); toast(msg); showWords(); } catch (e) { toast(e.message, true); }
    };
    for (const b of $("sread").querySelectorAll("[data-rm]")) b.onclick = () => saveWords(ws.filter((_, i) => i !== +b.dataset.rm), "Removed.");
    if ($("wAddBtn")) {
      const add = () => { const extra = $("wAdd").value.split(",").map((x) => x.trim()).filter(Boolean); if (extra.length) saveWords([...ws, ...extra], "Added."); };
      $("wAddBtn").onclick = add;
      $("wAdd").onkeydown = (e) => { if (e.key === "Enter") add(); };
      $("wReset").onclick = () => { if (confirm("Replace the list with the starter list?")) put("/api/inbox/social/settings", { words: null }).then(() => api("/api/inbox/social").then(() => showWords())).catch((e) => toast(e.message, true)); };
    }
    let tt;
    $("wTest").oninput = () => {
      clearTimeout(tt);
      tt = setTimeout(async () => {
        const text = $("wTest").value;
        if (!text.trim()) { $("wTestOut").textContent = ""; return; }
        try {
          const d = await post("/api/inbox/social/test", { text });
          $("wTestOut").innerHTML = d.matched ? `<b class="soc-err">Hidden</b> (matches “${esc(d.matched)}”)` : `<b class="soc-ok">Stays visible</b>`;
        } catch (e) { $("wTestOut").textContent = e.message; }
      }, 250);
    };
    if ($("lRun")) $("lRun").onclick = async () => {
      const pid = $("lPage").value;
      try { await post("/api/inbox/social/learn", { pages: pid ? [pid] : [] }); toast("Learning in the background…"); pollLearn(); } catch (e) { toast(e.message, true); }
    };
    if (learnt?.running) pollLearn();
    if ($("lAccept")) $("lAccept").onclick = async () => {
      const terms = [...$("sread").querySelectorAll("[data-sg]:checked")].map((b) => r.suggestions[+b.dataset.sg].term);
      if (!terms.length) return toast("Tick the words to add.", true);
      try { await post("/api/inbox/social/learn/accept", { terms }); toast(`Added ${terms.length} word${terms.length === 1 ? "" : "s"}.`); showWords(); } catch (e) { toast(e.message, true); }
    };
  }
  function pollLearn() {
    clearTimeout(learnTimer);
    learnTimer = setTimeout(async () => {
      if (sview !== "words") return;
      try {
        const d = await api("/api/inbox/social/learn");
        if (d.running) { if ($("lStep")) $("lStep").textContent = d.step; pollLearn(); } else showWords();
      } catch { /* try again on next open */ }
    }, 3000);
  }

  // ---------------------------------------------------------------- boot
  (function boot() {
    const v = PDUrl.get("sview");
    if (v && $("snav").querySelector(`[data-sview="${v}"]`)) {
      sview = v;
      for (const x of $("snav").querySelectorAll("[data-sview]")) x.classList.toggle("on", x.dataset.sview === v);
    }
    if (PDUrl.get("tab") === "social") {
      setTab("social", true);
      const item = PDUrl.get("sitem") || "";
      if (item.startsWith("c:")) openComment(item.slice(2));
      else if (item.startsWith("m:")) openChat(item.slice(2));
    } else if (PDUrl.get("tab") === "bulk") { setTab("bulk", true); loadOverview(); }
    else loadOverview();
    setInterval(() => {
      if (document.hidden) return;
      loadOverview();
      if (tab === "social" && !isSetup()) loadList();
    }, 60000);
  })();
})();
