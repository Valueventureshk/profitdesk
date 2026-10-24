/* EcomVital Sheets: connected Google Sheets with backups, history, daily log, and editing for Work / Manage.
   Every value comes from the server (sheets.py); this file only shows them and sends edits. */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let toastTimer;
  function toast(msg, bad) {
    const el = $("toast");
    el.textContent = msg; el.className = "toast" + (bad ? " bad" : ""); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3000);
  }
  async function api(path, opts = {}) {
    if (opts.json !== undefined) opts = { ...opts, method: opts.method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.json) };
    const r = await fetch(path, opts);
    let b = {};
    try { b = await r.json(); } catch { /* empty */ }
    if (r.status === 401 && b.login) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); throw new Error("Please log in again."); }
    if (!r.ok) throw new Error(b.detail || b.error || `Something went wrong (${r.status}).`);
    return b;
  }
  const ago = (iso) => {
    if (!iso) return "never";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago`
      : new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  };
  const when = (iso) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  const q = new URLSearchParams(location.search);

  let data = null, sheet = null, tab = null, view = "sheet", grid = null, version = 0, search = "", offset = 0;
  const canEdit = () => data && (data.level === "work" || data.level === "manage");
  const canManage = () => data && data.level === "manage";

  async function load(keep) {
    try { data = await api("/api/sheets"); } catch (e) { toast(e.message, true); return; }
    const g = data.google;
    $("gstate").textContent = g.write ? `Google: ${g.email} (read + edit)` : g.read ? "Google: read only (sign in to edit)" : "Google not connected";
    $("gsign").textContent = g.write ? "Change Google account" : "Sign in with Google";
    drawList();
    const want = keep && sheet ? sheet.id : +q.get("sheet") || (data.sheets[0] || {}).id;
    const s = data.sheets.find((x) => x.id === want) || data.sheets[0];
    if (s) openSheet(s.id, keep ? tab : q.get("tab")); else drawEmpty();
  }
  function drawList() {
    $("list").innerHTML = `<div class="sh-list-head">Sheets</div>` + (data.sheets.map((s) => `<button class="sh-item ${sheet && sheet.id === s.id ? "on" : ""}" data-sheet="${s.id}">
        <b>${esc(s.title || "Untitled")}</b>${s.active ? "" : ' <span class="sh-tag">paused</span>'}
        <span class="sub">${s.last_error ? `<span class="sh-bad">⚠ ${esc(s.last_error.slice(0, 80))}</span>` : `backed up ${ago(s.last_check)} · changed ${ago(s.last_change)}`}</span></button>`).join("")
      || '<p class="hint sh-pad">No Sheets yet.</p>');
  }
  function drawEmpty() {
    $("main").innerHTML = `<div class="panel sh-empty"><b>Connect your first Sheet.</b>
      <ol><li>${data.google.write ? "Google is signed in." : "Press <b>Sign in with Google</b> (top right) with the Google account that can open the Sheets."}</li>
      <li>Press <b>+ Add a Sheet</b> and paste its link.</li>
      <li>EcomVital reads every tab, keeps a backup every 15 minutes when something changed, learns what the Sheet is for, and writes a daily log.</li></ol>
      ${canManage() ? "" : "<p class='hint'>Ask an admin to connect the Sheets.</p>"}</div>`;
  }
  $("list").onclick = (e) => { const b = e.target.closest("[data-sheet]"); if (b) { view = "sheet"; version = 0; search = ""; offset = 0; openSheet(+b.dataset.sheet); } };

  function setUrl() {
    const u = new URL(location.href);
    u.searchParams.set("sheet", sheet.id);
    if (tab) u.searchParams.set("tab", tab); else u.searchParams.delete("tab");
    history.replaceState(null, "", u);
  }
  async function openSheet(id, wantTab) {
    sheet = data.sheets.find((s) => s.id === id);
    drawList();
    const tabs = sheet.tabs.map((t) => t.tab);
    tab = tabs.includes(wantTab) ? wantTab : tabs.includes(tab) ? tab : tabs[0] || null;
    setUrl();
    await drawSheet();
  }

  function head() {
    return `<div class="sh-head">
        <div><h2>${esc(sheet.title || "Untitled")}</h2>
          <span class="hint">Backed up ${ago(sheet.last_check)} · last change ${ago(sheet.last_change)}${sheet.note ? " · " + esc(sheet.note) : ""}</span></div>
        <div class="sh-acts">
          <a class="btn btn-sm btn-ghost" href="${esc(sheet.url)}" target="_blank" rel="noopener">Open in Google ↗</a>
          <button class="btn btn-sm" id="now" data-write-only>Back up now</button>
          <a class="btn btn-sm btn-ghost" href="/api/sheets/${sheet.id}/download">⬇ Excel (all tabs)</a>
          ${canManage() ? `<button class="btn btn-sm btn-ghost" id="settings">Settings</button>` : ""}
        </div></div>
      ${sheet.last_error ? `<div class="sh-warn">⚠ ${esc(sheet.last_error)}</div>` : ""}
      <nav class="sh-views">${[["sheet", "Sheet"], ["history", "History"], ["daily", "Daily log"], ["ai", "What the AI learned"]].map(([k, v]) =>
        `<button data-view="${k}" class="${view === k ? "on" : ""}">${v}</button>`).join("")}</nav>
      ${["sheet", "history"].includes(view) ? `<div class="sh-tabs">${sheet.tabs.map((t) => `<button data-tab="${esc(t.tab)}" class="${t.tab === tab ? "on" : ""}">${esc(t.tab)}</button>`).join("")}</div>` : ""}`;
  }
  async function drawSheet() {
    if (!sheet.tabs.length) { $("main").innerHTML = head() + `<div class="panel sh-empty">No backup yet. Press “Back up now”.</div>`; return; }
    if (view === "history") return drawHistory();
    if (view === "daily") return drawDaily();
    if (view === "ai") return drawAI();
    $("main").innerHTML = head() + `<div class="panel sh-empty">Loading ${esc(tab)}…</div>`;
    try {
      grid = await api(`/api/sheets/${sheet.id}/tab?tab=${encodeURIComponent(tab)}&version=${version}&q=${encodeURIComponent(search)}&offset=${offset}&limit=300`);
    } catch (e) { $("main").innerHTML = head() + `<div class="panel sh-empty">${esc(e.message)}</div>`; return; }
    const ch = new Set(grid.changed);
    const editable = canEdit() && grid.latest;
    $("main").innerHTML = head() + `
      ${grid.latest ? "" : `<div class="sh-old">Version from <b>${when(grid.at)}</b> (read only). Cells that changed from the version before are marked.
        <a class="btn btn-sm" href="/api/sheets/${sheet.id}/download?version=${grid.version}">⬇ Download this version</a>
        <button class="btn btn-sm btn-ghost" id="toLatest">Back to the latest</button></div>`}
      <div class="sh-bar">
        <input type="search" class="control" id="find" placeholder="Search this tab" value="${esc(search)}">
        <span class="hint">${grid.total} row${grid.total === 1 ? "" : "s"}${search ? " match" : ""} · copy of ${when(grid.at)}${editable ? " · double-click a cell to edit (saved to Google)" : ""}</span>
        ${editable ? '<button class="btn btn-sm" id="addRow">+ Add a row</button>' : ""}
        <a class="btn btn-sm btn-ghost" href="/api/sheets/${sheet.id}/download?version=${grid.version}&fmt=csv">⬇ CSV</a>
      </div>
      <div class="panel sh-gridwrap"><table class="sh-grid">
        <thead><tr><th class="sh-n"></th>${grid.cols.map((c) => `<th>${c}</th>`).join("")}</tr>
          <tr class="sh-hdr"><th class="sh-n">1</th>${grid.header.map((h, c) => `<th class="${ch.has(`0:${c}`) ? "sh-ch" : ""}">${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${grid.rows.map((r) => `<tr><td class="sh-n">${r.n}</td>${r.cells.map((v, c) =>
          `<td data-r="${r.n}" data-c="${c}" class="${ch.has(`${r.n - 1}:${c}`) ? "sh-ch" : ""}" title="${esc(v.length > 60 ? v : "")}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table></div>
      ${grid.total > offset + grid.rows.length || offset ? `<div class="sh-pager">
        ${offset ? '<button class="btn btn-sm" id="prev">← Previous 300</button>' : ""}
        <span class="hint">Rows ${offset + 1}–${offset + grid.rows.length} of ${grid.total}</span>
        ${grid.total > offset + grid.rows.length ? '<button class="btn btn-sm" id="next">Next 300 →</button>' : ""}</div>` : ""}`;
    const f = $("find");
    let t;
    f.oninput = () => { clearTimeout(t); t = setTimeout(() => { search = f.value.trim(); offset = 0; drawSheet().then(() => { const x = $("find"); x.focus(); x.setSelectionRange(x.value.length, x.value.length); }); }, 400); };
  }

  async function drawHistory() {
    $("main").innerHTML = head() + `<div class="panel sh-empty">Loading history…</div>`;
    let h;
    try { h = await api(`/api/sheets/${sheet.id}/history?tab=${encodeURIComponent(tab)}`); } catch (e) { toast(e.message, true); return; }
    $("main").innerHTML = head() + `<p class="hint">Every saved version of “${esc(tab)}”. A new version is kept each time something changes
        (checked every 15 minutes). Everything is kept for 30 days, then the last version of each day.</p>
      <div class="panel"><table class="table sh-hist"><thead><tr><th>When</th><th>Rows</th><th>What changed</th><th></th></tr></thead><tbody>
      ${h.versions.map((v) => `<tr><td>${when(v.at)}</td><td>${v.rows}</td>
        <td>${v.first ? "First backup" : `${v.added ? `+${v.added} row${v.added > 1 ? "s" : ""} ` : ""}${v.removed ? `−${v.removed} row${v.removed > 1 ? "s" : ""} ` : ""}${v.changed ? `${v.changed} cell${v.changed > 1 ? "s" : ""} changed` : ""}`}
          ${v.sample.length ? `<span class="sub">${v.sample.map((s) => `${esc(s[0])}: “${esc(s[1])}” → “${esc(s[2])}”`).join(" · ")}</span>` : ""}</td>
        <td><button class="btn btn-sm" data-version="${v.id}">View</button> <a class="btn btn-sm btn-ghost" href="/api/sheets/${sheet.id}/download?version=${v.id}">⬇</a></td></tr>`).join("")}
      </tbody></table></div>
      ${h.edits.length ? `<h3 class="sh-h3">Edits made in EcomVital</h3><div class="panel"><table class="table sh-hist"><thead><tr><th>When</th><th>Who</th><th>Cell</th><th>Change</th></tr></thead><tbody>
        ${h.edits.map((e) => `<tr><td>${when(e.at)}</td><td>${esc(e.by)}</td><td>${esc(e.tab)} ${esc(e.cell)}</td><td>“${esc(e.old || "")}” → “${esc(e.new || "")}”</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  }

  async function drawDaily() {
    $("main").innerHTML = head() + `<div class="panel sh-empty">Loading the log…</div>`;
    let d;
    try { d = await api(`/api/sheets/${sheet.id}/daily`); } catch (e) { toast(e.message, true); return; }
    $("main").innerHTML = head() + `<div class="sh-bar"><span class="hint">What was done in this Sheet each day (Hong Kong days). Written after midnight; the AI summarises the changes.</span>
        <button class="btn btn-sm" id="today" data-write-only>Write today's log so far</button></div>
      ${d.days.length ? d.days.map((x) => `<div class="panel sh-day"><div class="sh-dayhead"><b>${new Date(x.day + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</b>
          <span class="hint">${x.stats.total ? `+${x.stats.total.added} rows · ${x.stats.total.changed} cells changed${x.stats.total.removed ? ` · −${x.stats.total.removed} rows` : ""}` : ""}${x.stats.edits_in_app ? ` · ${x.stats.edits_in_app} edits in EcomVital` : ""}</span></div>
          ${x.summary ? `<div class="sh-sum">${esc(x.summary).replace(/\n/g, "<br>")}</div>` : '<p class="hint">No changes that day.</p>'}
          ${Object.keys(x.stats.tabs || {}).length ? `<div class="sub">${Object.entries(x.stats.tabs).map(([t, s]) => `${esc(t)}: +${s.added} rows, ${s.changed} cells`).join(" · ")}</div>` : ""}</div>`).join("")
        : '<div class="panel sh-empty">No daily log yet: the first one is written tonight after midnight (or press “Write today\'s log so far”).</div>'}`;
  }

  function drawAI() {
    $("main").innerHTML = head() + `<div class="panel sh-ai">${sheet.ai_notes ? `<div class="sh-sum">${esc(sheet.ai_notes).replace(/\n/g, "<br>")}</div>
        <p class="hint">Learned ${ago(sheet.ai_at)}. EcomVital's Ask assistant uses this and the latest backup to answer questions about the Sheet.</p>`
      : `<p class="hint">${data.ai ? "Not learned yet: it happens after the first backup." : "Add the Anthropic key (Settings → AI) so the AI can learn the Sheets."}</p>`}
      <button class="btn btn-sm" id="relearn" data-write-only>Learn it again</button></div>`;
  }

  /* ------------------------------------------------ clicks */
  $("main").addEventListener("click", async (e) => {
    const v = e.target.closest("[data-view]");
    if (v) { view = v.dataset.view; version = 0; drawSheet(); return; }
    const t = e.target.closest("[data-tab]");
    if (t) { tab = t.dataset.tab; version = 0; search = ""; offset = 0; setUrl(); drawSheet(); return; }
    const ver = e.target.closest("[data-version]");
    if (ver) { version = +ver.dataset.version; view = "sheet"; offset = 0; search = ""; drawSheet(); return; }
    if (e.target.closest("#toLatest")) { version = 0; drawSheet(); return; }
    if (e.target.closest("#next")) { offset += 300; drawSheet(); return; }
    if (e.target.closest("#prev")) { offset = Math.max(0, offset - 300); drawSheet(); return; }
    const now = e.target.closest("#now");
    if (now) {
      now.disabled = true; now.textContent = "Reading…";
      try { const r = await api(`/api/sheets/${sheet.id}/backup`, { json: {} }); toast(r.changed.length ? `Saved a new version of ${r.changed.length} tab${r.changed.length > 1 ? "s" : ""}.` : "Nothing changed since the last backup."); await load(true); }
      catch (err) { toast(err.message, true); now.disabled = false; now.textContent = "Back up now"; }
      return;
    }
    const td = e.target.closest("#today");
    if (td) {
      td.disabled = true; td.textContent = "Writing…";
      try { await api(`/api/sheets/${sheet.id}/daily`, { json: {} }); drawDaily(); } catch (err) { toast(err.message, true); td.disabled = false; }
      return;
    }
    const rl = e.target.closest("#relearn");
    if (rl) {
      rl.disabled = true; rl.textContent = "Learning…";
      try { const r = await api(`/api/sheets/${sheet.id}/learn`, { json: {} }); sheet.ai_notes = r.ai_notes; sheet.ai_at = new Date().toISOString(); drawAI(); } catch (err) { toast(err.message, true); rl.disabled = false; }
      return;
    }
    if (e.target.closest("#settings")) { settingsForm(); return; }
    if (e.target.closest("#addRow")) { rowForm(); return; }
  });

  // double-click a cell to edit it (latest copy, Work / Manage): saved to Google, refused if it changed meanwhile
  $("main").addEventListener("dblclick", (e) => {
    const cell = e.target.closest("td[data-r]");
    if (!cell || !canEdit() || !grid || !grid.latest || cell.querySelector("textarea")) return;
    const r = +cell.dataset.r, c = +cell.dataset.c;
    const old = grid.rows.find((x) => x.n === r).cells[c];
    cell.innerHTML = `<textarea class="sh-edit">${esc(old)}</textarea>`;
    const ta = cell.querySelector("textarea");
    ta.focus(); ta.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const val = ta.value;
      if (!save || val === old) { cell.textContent = old; return; }
      cell.textContent = val; cell.classList.add("sh-saving");
      try {
        await api(`/api/sheets/${sheet.id}/cell`, { json: { tab, row: r, col: c, value: val, old } });
        toast(`Saved ${grid.cols[c]}${r} to Google.`);
        drawSheet();
      } catch (err) { toast(err.message, true); cell.textContent = old; cell.classList.remove("sh-saving"); }
    };
    ta.onkeydown = (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); finish(true); }
      if (ev.key === "Escape") finish(false);
    };
    ta.onblur = () => finish(true);
  });

  /* ------------------------------------------------ forms */
  function modal(html) { $("modalBody").innerHTML = html; $("modal").hidden = false; return $("modalBody"); }
  const close = () => { $("modal").hidden = true; };
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal") || e.target.closest("[data-close]")) close(); });

  $("add").onclick = () => {
    const box = modal(`<h3>Add a Google Sheet</h3>
      <label class="field">Sheet link<input class="control" id="u" placeholder="https://docs.google.com/spreadsheets/d/…"></label>
      <label class="field">What the team uses it for (helps the AI)<input class="control" id="n" placeholder="e.g. Daily orders sent to suppliers, one row per item"></label>
      <p class="hint">${data.google.write ? `It must be shared with ${esc(data.google.email)}.` : "Sign in with Google first (top right) with an account that can open the Sheet."}</p>
      <div class="sh-acts"><button class="btn btn-primary" id="go">Add and back up</button><button class="btn btn-ghost" data-close>Cancel</button></div>`);
    box.querySelector("#go").onclick = async (ev) => {
      ev.target.disabled = true; ev.target.textContent = "Reading the Sheet…";
      try {
        const r = await api("/api/sheets", { json: { url: box.querySelector("#u").value, note: box.querySelector("#n").value } });
        close(); toast(`Connected “${r.title}” (${r.tabs} tab${r.tabs > 1 ? "s" : ""}). The AI is learning it now.`);
        q.set("sheet", r.id); await load(false); openSheet(r.id);
      } catch (err) { toast(err.message, true); ev.target.disabled = false; ev.target.textContent = "Add and back up"; }
    };
  };

  function settingsForm() {
    const box = modal(`<h3>${esc(sheet.title)}</h3>
      <label class="field">What the team uses it for<input class="control" id="n" value="${esc(sheet.note || "")}"></label>
      <label class="sh-check"><input type="checkbox" id="a"${sheet.active ? " checked" : ""}> Back it up every 15 minutes</label>
      <div class="sh-acts"><button class="btn btn-primary" id="save">Save</button><button class="btn btn-ghost" data-close>Cancel</button>
        <button class="btn btn-ghost sh-danger" id="del">Remove from EcomVital…</button></div>`);
    box.querySelector("#save").onclick = async () => {
      try { await api(`/api/sheets/${sheet.id}`, { method: "PUT", json: { note: box.querySelector("#n").value, active: box.querySelector("#a").checked } }); close(); toast("Saved."); load(true); }
      catch (err) { toast(err.message, true); }
    };
    box.querySelector("#del").onclick = async () => {
      const keep = confirm("Keep its backups in EcomVital?\n\nOK = stop backing it up but keep the history.\nCancel = choose whether to delete everything.");
      if (!keep && !confirm(`Delete “${sheet.title}” and ALL its backups and history from EcomVital? The Google Sheet itself isn't touched. This can't be undone.`)) return;
      try { await api(`/api/sheets/${sheet.id}?keep=${keep ? 1 : 0}`, { method: "DELETE" }); close(); toast(keep ? "Paused; history kept." : "Removed."); sheet = null; load(false); }
      catch (err) { toast(err.message, true); }
    };
  }

  function rowForm() {
    const box = modal(`<h3>Add a row to “${esc(tab)}”</h3><p class="hint">Added at the bottom of the tab in Google.</p>
      <div class="sh-rowform">${grid.header.map((h, c) => `<label class="field">${esc(h || grid.cols[c])}<input class="control" data-c="${c}"></label>`).join("")}</div>
      <div class="sh-acts"><button class="btn btn-primary" id="go">Add row</button><button class="btn btn-ghost" data-close>Cancel</button></div>`);
    box.querySelector("#go").onclick = async () => {
      const cells = [...box.querySelectorAll("[data-c]")].map((i) => i.value);
      try { const r = await api(`/api/sheets/${sheet.id}/row`, { json: { tab, cells } }); close(); toast(`Row added (${r.range || "bottom"}).`); drawSheet(); }
      catch (err) { toast(err.message, true); }
    };
  }

  load(false);
})();
