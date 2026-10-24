/* EcomVital Classroom: courses by section (like Skool), lessons with a video, notes, transcript and an AI step-by-step
   guide. Every figure and text comes from the server (classroom.py); this file only shows them and sends edits. */
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
    if (opts.json !== undefined) {
      opts = { ...opts, method: opts.method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.json) };
    }
    const r = await fetch(path, opts);
    let b = {};
    try { b = await r.json(); } catch { /* empty */ }
    if (r.status === 401 && b.login) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); throw new Error("Please log in again."); }
    if (!r.ok) throw new Error(b.detail || b.error || `Something went wrong (${r.status}).`);
    return b;
  }
  const size = (n) => (n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? Math.round(n / 1e6) + " MB" : Math.round((n || 0) / 1e3) + " KB");
  const mins = (s) => (s ? (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.max(1, Math.round(s / 60))} min`) : "");
  const q = new URLSearchParams(location.search);

  let home = null, course = null, lesson = null, filter = "all", tab = "notes", poll = null;

  /* ------------------------------------------------ home: courses by section */
  async function loadHome() {
    try { home = await api("/api/classroom"); } catch (e) { toast(e.message, true); return; }
    drawHome();
  }
  const cover = (c) => c.cover ? `<img src="${esc(c.cover)}" alt="" loading="lazy">`
    : `<div class="cl-cover-text"><span>${esc(c.title)}</span></div>`;
  function card(c) {
    return `<button class="cl-card" data-course="${c.id}">
      <div class="cl-cover">${cover(c)}${c.status !== "published" ? '<span class="cl-draft">Draft</span>' : ""}</div>
      <div class="cl-card-body"><strong>${esc(c.title)}</strong><p>${esc(c.description || "")}</p>
        <div class="cl-prog"><i style="width:${c.progress}%"></i></div>
        <span class="cl-pct">${c.progress}%${c.lessons ? ` · ${c.done}/${c.lessons} lessons` : " · no lessons yet"}</span></div></button>`;
  }
  function drawHome() {
    const words = ($("q").value || "").trim().toLowerCase();
    $("chips").innerHTML = [`<button data-f="all" class="${filter === "all" ? "on" : ""}">All</button>`]
      .concat(home.sections.map((s) => `<button data-f="${s.id}" class="${String(filter) === String(s.id) ? "on" : ""}">${esc(s.name)}</button>`)).join("");
    const pick = home.courses.filter((c) => (!words || (c.title + " " + c.description).toLowerCase().includes(words)));
    const groups = [...home.sections, { id: null, name: "Other" }]
      .filter((s) => filter === "all" || String(filter) === String(s.id))
      .map((s) => ({ s, list: pick.filter((c) => (c.section_id || null) === s.id) })).filter((g) => g.list.length);
    $("grid").innerHTML = groups.length ? groups.map((g) => `<h2 class="cl-sec">${esc(g.s.name)}</h2>
      <div class="cl-grid">${g.list.map(card).join("")}</div>`).join("")
      : `<div class="panel cl-empty"><b>No courses yet.</b>${home.manage ? " Press “+ New course” to make the first one: give it a title, a section and a cover, then add lessons (upload a recording, record your screen, or paste a link)." : " Your admins haven't published any yet."}</div>`;
  }
  $("chips").onclick = (e) => { const b = e.target.closest("[data-f]"); if (b) { filter = b.dataset.f; drawHome(); } };
  $("q").oninput = () => drawHome();
  $("grid").onclick = (e) => { const b = e.target.closest("[data-course]"); if (b) openCourse(+b.dataset.course); };

  function setUrl(params) {
    const u = new URL(location.href);
    for (const k of ["course", "lesson"]) u.searchParams.delete(k);
    for (const [k, v] of Object.entries(params)) if (v) u.searchParams.set(k, v);
    history.replaceState(null, "", u);
  }
  function showHome() {
    clearTimeout(poll);
    $("course").hidden = true; $("home").hidden = false; course = lesson = null; setUrl({}); loadHome();
  }
  $("back").onclick = (e) => { e.preventDefault(); showHome(); };

  /* ------------------------------------------------ one course */
  async function openCourse(cid, lid) {
    try { course = await api(`/api/classroom/courses/${cid}`); } catch (e) { toast(e.message, true); return; }
    $("home").hidden = true; $("course").hidden = false;
    drawSide();
    const first = lid || (course.lessons.find((l) => !l.done) || course.lessons[0] || {}).id;
    if (first) openLesson(first); else {
      lesson = null; setUrl({ course: cid });
      $("lesson").innerHTML = `<div class="panel cl-empty"><b>${esc(course.title)}</b><p>${esc(course.description || "")}</p>
        <p class="hint">No lessons yet.${home && home.manage ? " Press “+ Lesson”." : ""}</p></div>`;
    }
  }
  function drawSide() {
    $("cTitle").textContent = course.title + (course.status !== "published" ? " (draft)" : "");
    $("cBar").style.width = course.progress + "%";
    $("cProg").textContent = `${course.progress}% complete`;
    const manage = home && home.manage;
    $("lessons").innerHTML = course.lessons.map((l, i) => `<li class="${lesson && lesson.id === l.id ? "on" : ""}" data-lesson="${l.id}">
        <span class="cl-tick ${l.done ? "done" : ""}">${l.done ? "✓" : i + 1}</span>
        <span class="cl-ltitle">${esc(l.title)}<small>${[l.duration ? mins(l.duration) : "", l.has_transcript ? "transcript" : "", l.has_steps ? "guide" : ""].filter(Boolean).join(" · ")}</small></span>
        ${manage ? `<span class="cl-move"><button data-up="${l.id}" title="Earlier"${i ? "" : " disabled"}>▲</button><button data-down="${l.id}" title="Later"${i < course.lessons.length - 1 ? "" : " disabled"}>▼</button></span>` : ""}</li>`).join("")
      || '<li class="hint">No lessons yet.</li>';
  }
  $("lessons").onclick = async (e) => {
    const up = e.target.closest("[data-up]"), down = e.target.closest("[data-down]");
    if (up || down) {
      e.stopPropagation();
      const ids = course.lessons.map((l) => l.id), id = +(up || down).dataset[up ? "up" : "down"], i = ids.indexOf(id), j = up ? i - 1 : i + 1;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      try { await api("/api/classroom/reorder", { json: { kind: "lesson", ids } }); await refreshCourse(); } catch (err) { toast(err.message, true); }
      return;
    }
    const li = e.target.closest("[data-lesson]");
    if (li) openLesson(+li.dataset.lesson);
  };
  async function refreshCourse() {
    try { course = await api(`/api/classroom/courses/${course.id}`); } catch { return; }
    drawSide();
  }

  /* ------------------------------------------------ one lesson */
  async function openLesson(lid) {
    clearTimeout(poll);
    try { lesson = await api(`/api/classroom/lessons/${lid}`); } catch (e) { toast(e.message, true); return; }
    setUrl({ course: course.id, lesson: lid });
    drawSide();
    drawLesson();
    if (lesson.tr_status === "working" || lesson.proc_status === "working") poll = setTimeout(() => recheck(lid), 15000);
  }
  async function recheck(lid) {
    if (!lesson || lesson.id !== lid) return;
    try {
      const l = await api(`/api/classroom/lessons/${lid}`);
      if (l.proc_status !== lesson.proc_status && l.proc_status !== "working") { const keepTab = tab; lesson = l; tab = keepTab; drawLesson(); }
      if (l.tr_status !== "working" && l.proc_status !== "working") { lesson = l; drawLesson(); refreshCourse(); if (l.tr_status === "done") toast("Transcript ready."); else if (l.tr_status === "failed") toast("Transcript failed: " + (l.tr_error || ""), true); return; }
    } catch { /* try again */ }
    poll = setTimeout(() => recheck(lid), 15000);
  }
  function player(l) {
    if (l.has_video) return /\.(mp3|m4a|wav)$/i.test(l.video_name || "") || ["mp3", "m4a", "wav"].includes(l.video_ext)
      ? `<audio controls preload="metadata" id="media" src="/api/classroom/video/${l.id}"></audio>`
      : `<video controls preload="metadata" id="media" src="/api/classroom/video/${l.id}"></video>`;
    if (l.embed) return `<div class="cl-embed"><iframe src="${esc(l.embed)}" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe></div>`;
    if (l.video_url) return `<div class="cl-novideo"><a href="${esc(l.video_url)}" target="_blank" rel="noopener">Open the recording ↗</a></div>`;
    return `<div class="cl-novideo">${home && home.manage ? "No recording yet: press Edit lesson to upload one, record your screen, or paste a link." : "No recording for this lesson."}</div>`;
  }
  const md = (t) => esc(t).replace(/^### (.*)$/gm, "<h4>$1</h4>").replace(/^## (.*)$/gm, "<h3>$1</h3>").replace(/^# (.*)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[(\d+:\d{2}(?::\d{2})?)\]/g, '<a href="#" class="cl-ts" data-ts="$1">[$1]</a>')
    .replace(/\n/g, "<br>");
  function drawLesson() {
    const l = lesson, manage = home && home.manage;
    const trState = { none: "No transcript yet.", working: "Transcribing… (about a minute per 10 minutes of video; you can leave this page)",
      failed: "Transcript failed: " + (l.tr_error || ""), done: "" }[l.tr_status] || "";
    const tabBody = {
      notes: l.notes ? `<div class="cl-text">${md(l.notes)}</div>` : `<p class="hint">No notes for this lesson.</p>`,
      steps: l.steps ? `<div class="cl-text">${md(l.steps)}</div>` : `<p class="hint">No step-by-step guide yet.${manage ? " Press “✨ Make step-by-step guide”: the AI writes it from the transcript and your notes." : ""}</p>`,
      transcript: `${trState ? `<p class="hint ${l.tr_status === "failed" ? "cl-bad" : ""}">${esc(trState)}</p>` : ""}${l.transcript ? `<div class="cl-text cl-tr">${md(l.transcript)}</div>` : ""}`,
    }[tab];
    $("lesson").innerHTML = `
      <div class="cl-player">${player(l)}</div>
      <div class="cl-lhead"><div><h2>${esc(l.title)}</h2><span class="hint">${esc(l.course_title)}${l.duration ? " · " + mins(l.duration) : ""}</span></div>
        <div class="cl-lacts">
          <button class="btn ${l.done ? "" : "btn-primary"}" id="done">${l.done ? "✓ Completed" : "Mark complete"}</button>
          ${l.has_video ? `<a class="btn btn-ghost" href="/api/classroom/video/${l.id}?download=1" title="Save the video to your computer">⬇ Video</a>` : ""}
          ${manage ? `<button class="btn" id="editLesson">Edit lesson</button>` : ""}</div></div>
      ${l.proc_status === "working" ? `<p class="hint">Compressing the video… it plays as uploaded meanwhile.</p>` : ""}
      ${l.proc_status === "failed" && manage ? `<p class="hint cl-bad">Compression didn't work (${esc(l.proc_error || "")}); the original file is kept.</p>` : ""}
      ${l.proc_status === "done" && l.orig_size && l.video_size && l.orig_size > l.video_size && manage ? `<p class="hint">Compressed from ${size(l.orig_size)} to ${size(l.video_size)}.</p>` : ""}
      <nav class="cl-tabs">${[["notes", "Notes"], ["steps", "Step by step"], ["transcript", "Transcript"]].map(([k, v]) =>
        `<button data-tab="${k}" class="${tab === k ? "on" : ""}">${v}${k === "transcript" && l.tr_status === "working" ? " …" : ""}</button>`).join("")}</nav>
      <div class="cl-tabacts">
        ${tab === "transcript" ? `${l.has_transcript ? `<a class="btn btn-sm" href="/api/classroom/lessons/${l.id}/download?kind=transcript">⬇ Transcript (.txt)</a>` : ""}
          ${manage && l.has_video && l.tr_status !== "working" ? `<button class="btn btn-sm" id="transcribe">${l.has_transcript ? "Transcribe again" : "✨ Transcribe"}</button>` : ""}
          ${manage ? `<button class="btn btn-sm btn-ghost" data-edit="transcript">Edit</button>` : ""}` : ""}
        ${tab === "steps" ? `${manage ? `<button class="btn btn-sm" id="makeSteps">✨ ${l.has_steps ? "Write it again" : "Make step-by-step guide"}</button>
          <button class="btn btn-sm btn-ghost" data-edit="steps">Edit</button>` : ""}` : ""}
        ${tab === "notes" && manage ? `<button class="btn btn-sm btn-ghost" data-edit="notes">Edit</button>` : ""}
        <a class="btn btn-sm btn-ghost" href="/api/classroom/lessons/${l.id}/download?kind=guide" title="Notes + guide + transcript in one file, to give to an AI">⬇ Everything (.md)</a>
      </div>
      <div class="panel cl-tabbody">${tabBody}</div>`;
  }
  $("lesson").addEventListener("click", async (e) => {
    const t = e.target.closest("[data-tab]");
    if (t) { tab = t.dataset.tab; drawLesson(); return; }
    const ts = e.target.closest("[data-ts]");
    if (ts) {
      e.preventDefault();
      const m = $("media");
      if (!m) return;
      const p = ts.dataset.ts.split(":").map(Number);
      m.currentTime = p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1];
      m.play(); m.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (e.target.closest("#done")) {
      try { await api("/api/classroom/progress", { json: { lesson: lesson.id, done: !lesson.done } }); lesson.done = !lesson.done; drawLesson(); refreshCourse(); }
      catch (err) { toast(err.message, true); }
      return;
    }
    if (e.target.closest("#editLesson")) { lessonForm(lesson); return; }
    if (e.target.closest("#transcribe")) {
      try { await api(`/api/classroom/lessons/${lesson.id}/transcribe`, { json: {} }); toast("Transcribing… it shows here when it's ready."); openLesson(lesson.id); }
      catch (err) { toast(err.message, true); }
      return;
    }
    const mk = e.target.closest("#makeSteps");
    if (mk) {
      if (lesson.has_steps && !confirm("Write the guide again? Your edits to the current guide are replaced.")) return;
      mk.disabled = true; mk.textContent = "Writing… (up to a minute)";
      try { await api(`/api/classroom/lessons/${lesson.id}/steps`, { json: {} }); toast("Guide written."); openLesson(lesson.id); refreshCourse(); }
      catch (err) { toast(err.message, true); mk.disabled = false; mk.textContent = "✨ Make step-by-step guide"; }
      return;
    }
    const ed = e.target.closest("[data-edit]");
    if (ed) textForm(ed.dataset.edit);
  });

  /* ------------------------------------------------ admin forms */
  function modal(html) {
    $("modalBody").innerHTML = html;
    $("modal").hidden = false;
    return $("modalBody");
  }
  const close = () => { $("modal").hidden = true; };
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal") || e.target.closest("[data-close]")) close(); });

  function textForm(field) {
    const label = { notes: "Notes", steps: "Step-by-step guide", transcript: "Transcript" }[field];
    const box = modal(`<h3>${label} · ${esc(lesson.title)}</h3>
      <textarea class="control cl-big" id="txt">${esc(lesson[field] || "")}</textarea>
      <p class="hint">${field === "transcript" ? "You can paste a transcript from YouTube / Loom here too. Keep [12:04] times to make them clickable." : "Simple formatting works: ## heading, **bold**, numbered lines."}</p>
      <div class="cl-acts"><button class="btn btn-primary" id="saveTxt">Save</button><button class="btn btn-ghost" data-close>Cancel</button></div>`);
    box.querySelector("#saveTxt").onclick = async () => {
      try { await api("/api/classroom/lessons", { json: { id: lesson.id, [field]: box.querySelector("#txt").value } }); close(); toast("Saved."); openLesson(lesson.id); refreshCourse(); }
      catch (err) { toast(err.message, true); }
    };
  }

  function sectionOptions(sel) {
    return `<option value="">No section</option>` + home.sections.map((s) => `<option value="${s.id}"${String(sel) === String(s.id) ? " selected" : ""}>${esc(s.name)}</option>`).join("");
  }
  function courseForm(c) {
    const box = modal(`<h3>${c ? "Edit course" : "New course"}</h3>
      <label class="field">Title<input class="control" id="ct" value="${esc(c ? c.title : "")}" placeholder="e.g. How we build a new store"></label>
      <label class="field">Short description<textarea class="control" id="cd" rows="2" placeholder="What the team learns here">${esc(c ? c.description : "")}</textarea></label>
      <label class="field">Section<select class="control" id="cs">${sectionOptions(c ? c.section_id : (filter !== "all" ? filter : ""))}</select></label>
      <label class="cl-check"><input type="checkbox" id="cp"${c && c.status === "published" ? " checked" : ""}> Published (the team can see it)</label>
      ${c ? `<div class="field">Cover image (16:9 looks best)<div class="cl-coverrow">
          <div class="cl-cover small">${cover({ ...c, cover: c.cover })}</div>
          <label class="btn btn-sm">Upload image<input type="file" id="cf" accept="image/*" hidden></label>
          ${c.cover ? '<button class="btn btn-sm btn-ghost" id="cfx">Remove</button>' : ""}</div></div>` : '<p class="hint">You can add a cover image after saving. Without one, the title shows on a dark card.</p>'}
      <div class="cl-acts"><button class="btn btn-primary" id="cSave">Save</button><button class="btn btn-ghost" data-close>Cancel</button>
        ${c ? '<button class="btn btn-ghost cl-danger" id="cDel">Delete course</button>' : ""}</div>`);
    box.querySelector("#cSave").onclick = async () => {
      try {
        const r = await api("/api/classroom/courses", { json: { id: c && c.id, title: box.querySelector("#ct").value, description: box.querySelector("#cd").value,
          section_id: box.querySelector("#cs").value, status: box.querySelector("#cp").checked ? "published" : "draft" } });
        close(); toast("Saved.");
        home = await api("/api/classroom");
        openCourse(r.id, lesson && lesson.id);
      } catch (err) { toast(err.message, true); }
    };
    const cf = box.querySelector("#cf");
    if (cf) cf.onchange = async () => {
      const fd = new FormData(); fd.append("file", cf.files[0]);
      try { await api(`/api/classroom/courses/${c.id}/cover`, { method: "POST", body: fd }); toast("Cover saved."); course = await api(`/api/classroom/courses/${c.id}`); courseForm(course); }
      catch (err) { toast(err.message, true); }
    };
    const cfx = box.querySelector("#cfx");
    if (cfx) cfx.onclick = async () => { try { await api(`/api/classroom/courses/${c.id}/cover`, { method: "DELETE" }); course = await api(`/api/classroom/courses/${c.id}`); courseForm(course); } catch (err) { toast(err.message, true); } };
    const del = box.querySelector("#cDel");
    if (del) del.onclick = async () => {
      if (!confirm(`Delete “${c.title}” with all its lessons and videos? This can't be undone.`)) return;
      try { await api(`/api/classroom/courses/${c.id}`, { method: "DELETE" }); close(); toast("Course deleted."); showHome(); } catch (err) { toast(err.message, true); }
    };
  }
  $("newCourse").onclick = () => courseForm(null);
  $("editCourse").onclick = () => courseForm(course);

  function lessonForm(l) {
    const box = modal(`<h3>${l ? "Edit lesson" : "New lesson"}</h3>
      <label class="field">Title<input class="control" id="lt" value="${esc(l ? l.title : "")}" placeholder="e.g. Buying the domain and connecting it"></label>
      <div class="field">Recording
        <div class="cl-rec">
          <label class="btn btn-sm">Upload a video / audio file<input type="file" id="lf" accept="video/*,audio/*" hidden></label>
          <button class="btn btn-sm" id="lrec">● Record (screen + camera)</button>
          ${l && l.has_video ? `<span class="hint">Now: ${esc(l.video_name || "video")} · ${size(l.video_size)}</span>` : ""}
        </div>
        <div class="cl-upbar" id="up" hidden><i></i><span></span></div>
        <input class="control" id="lu" value="${esc(l ? l.video_url : "")}" placeholder="…or paste a YouTube, Loom, Vimeo or Google Drive link">
        <span class="hint">Uploaded files are transcribed automatically${home.has_voice_key ? "" : " once the OpenAI key is added (Settings → WhatsApp, voice notes key)"}. Links can't be transcribed: paste their transcript instead.</span>
      </div>
      <label class="field">Notes for the team<textarea class="control" id="ln" rows="5" placeholder="Links, logins to ask for, key points…">${esc(l ? l.notes : "")}</textarea></label>
      <div class="cl-acts"><button class="btn btn-primary" id="lSave">Save</button><button class="btn btn-ghost" data-close>Cancel</button>
        ${l ? '<button class="btn btn-ghost cl-danger" id="lDel">Delete lesson</button>' : ""}</div>`);
    let lid = l && l.id;
    const saveText = async () => {
      const r = await api("/api/classroom/lessons", { json: { id: lid, course_id: course.id, title: box.querySelector("#lt").value || "New lesson",
        video_url: box.querySelector("#lu").value, notes: box.querySelector("#ln").value } });
      lid = r.id;
      return lid;
    };
    const upload = (file, extra = {}) => new Promise((resolve, reject) => {
      const bar = box.querySelector("#up"); bar.hidden = false;
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/classroom/lessons/${lid}/video`);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.querySelector("i").style.width = (e.loaded / e.total * 100) + "%"; bar.querySelector("span").textContent = `Uploading ${size(e.loaded)} of ${size(e.total)}…`; } };
      xhr.onload = () => { let b = {}; try { b = JSON.parse(xhr.responseText); } catch { /* */ } if (xhr.status < 400) resolve(b); else reject(new Error(b.detail || b.error || `Upload failed (${xhr.status}).`)); };
      xhr.onerror = () => reject(new Error("The upload was interrupted. Check your connection and try again."));
      const fd = new FormData(); fd.append("file", file);
      for (const [k, v] of Object.entries(extra)) fd.append(k, typeof v === "object" ? JSON.stringify(v) : v);
      xhr.send(fd);
    });
    const sendFile = async (file, extra) => {
      try {
        await saveText();
        const r = await upload(file, extra);
        toast(r.transcribing ? "Uploaded. Compressing, then transcribing…" : "Uploaded. Compressing…");
        close(); await refreshCourse(); openLesson(lid);
      } catch (err) { toast(err.message, true); }
    };
    box.querySelector("#lf").onchange = (e) => { if (e.target.files[0]) sendFile(e.target.files[0]); };
    box.querySelector("#lrec").onclick = async () => {
      try { await saveText(); } catch (err) { toast(err.message, true); return; }
      const title = box.querySelector("#lt").value || "Lesson";
      close();
      window.EVStudio.open({ title, onSave: (file, extra) => { lessonForm({ ...(l || {}), id: lid, title, video_url: box.querySelector("#lu").value, notes: box.querySelector("#ln").value }); sendFileTo(file, extra); } });
    };
    const sendFileTo = async (file, extra) => {
      const bar = $("modalBody").querySelector("#up");
      try {
        const r = await new Promise((resolve, reject) => {
          bar.hidden = false;
          const xhr = new XMLHttpRequest();
          xhr.open("POST", `/api/classroom/lessons/${lid}/video`);
          xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.querySelector("i").style.width = (e.loaded / e.total * 100) + "%"; bar.querySelector("span").textContent = `Uploading ${size(e.loaded)} of ${size(e.total)}…`; } };
          xhr.onload = () => { let b = {}; try { b = JSON.parse(xhr.responseText); } catch { /* */ } if (xhr.status < 400) resolve(b); else reject(new Error(b.detail || b.error || `Upload failed (${xhr.status}).`)); };
          xhr.onerror = () => reject(new Error("The upload was interrupted. Download the recording to your computer from the studio next time as a backup, then try again."));
          const fd = new FormData(); fd.append("file", file);
          for (const [k, v] of Object.entries(extra || {})) fd.append(k, typeof v === "object" ? JSON.stringify(v) : v);
          xhr.send(fd);
        });
        toast(r.transcribing ? "Saved. Compressing, then transcribing…" : "Saved. Compressing…");
        close(); await refreshCourse(); openLesson(lid);
      } catch (err) {
        toast(err.message, true);
        if (confirm("The recording didn't reach EcomVital. Save it to your computer so it isn't lost? You can upload that file to the lesson later.")) {
          const a = document.createElement("a"); a.href = URL.createObjectURL(file); a.download = file.name || "recording.webm";
          document.body.appendChild(a); a.click(); a.remove();
        }
      }
    };
    box.querySelector("#lSave").onclick = async () => {
      try { await saveText(); close(); toast("Saved."); await refreshCourse(); openLesson(lid); } catch (err) { toast(err.message, true); }
    };
    const del = box.querySelector("#lDel");
    if (del) del.onclick = async () => {
      if (!confirm(`Delete “${l.title}” and its video? This can't be undone.`)) return;
      try { await api(`/api/classroom/lessons/${l.id}`, { method: "DELETE" }); close(); toast("Lesson deleted."); openCourse(course.id); } catch (err) { toast(err.message, true); }
    };
  }
  $("newLesson").onclick = () => lessonForm(null);

  /* ------------------------------------------------ sections */
  function sectionsForm() {
    const box = modal(`<h3>Sections</h3><p class="hint">Group the courses, e.g. Front end, Back end, Store creation, Customer service, Fulfilment, SCM.</p>
      <ol class="cl-seclist">${home.sections.map((s, i) => `<li><input class="control" data-name="${s.id}" value="${esc(s.name)}">
        <button class="btn btn-sm btn-ghost" data-sup="${s.id}"${i ? "" : " disabled"}>▲</button>
        <button class="btn btn-sm btn-ghost" data-sdown="${s.id}"${i < home.sections.length - 1 ? "" : " disabled"}>▼</button>
        <button class="btn btn-sm btn-ghost cl-danger" data-sdel="${s.id}">Remove</button></li>`).join("")}</ol>
      <div class="cl-rec"><input class="control" id="sNew" placeholder="New section name"><button class="btn btn-sm" id="sAdd">Add</button></div>
      <div class="cl-acts"><button class="btn btn-primary" data-close>Done</button></div>`);
    const again = async () => { home = await api("/api/classroom"); drawHome(); sectionsForm(); };
    box.querySelectorAll("[data-name]").forEach((inp) => {
      inp.onchange = async () => { try { await api("/api/classroom/sections", { json: { id: inp.dataset.name, name: inp.value } }); toast("Saved."); home = await api("/api/classroom"); drawHome(); } catch (err) { toast(err.message, true); } };
    });
    box.querySelector("#sAdd").onclick = async () => { try { await api("/api/classroom/sections", { json: { name: box.querySelector("#sNew").value } }); again(); } catch (err) { toast(err.message, true); } };
    box.onclick = async (e) => {
      const up = e.target.closest("[data-sup]"), down = e.target.closest("[data-sdown]"), del = e.target.closest("[data-sdel]");
      if (del) {
        if (!confirm("Remove this section? Its courses stay, under “Other”.")) return;
        try { await api(`/api/classroom/sections/${del.dataset.sdel}`, { method: "DELETE" }); again(); } catch (err) { toast(err.message, true); }
      }
      if (up || down) {
        const ids = home.sections.map((s) => s.id), id = +(up || down).dataset[up ? "sup" : "sdown"], i = ids.indexOf(id), j = up ? i - 1 : i + 1;
        [ids[i], ids[j]] = [ids[j], ids[i]];
        try { await api("/api/classroom/reorder", { json: { kind: "section", ids } }); again(); } catch (err) { toast(err.message, true); }
      }
    };
  }
  $("sections").onclick = () => sectionsForm();

  /* ------------------------------------------------ start */
  (async () => {
    await loadHome();
    if (q.get("lesson") && !q.get("course")) {
      try { const l = await api(`/api/classroom/lessons/${q.get("lesson")}`); openCourse(l.course_id, l.id); } catch (e) { toast(e.message, true); }
    } else if (q.get("course")) openCourse(+q.get("course"), q.get("lesson") ? +q.get("lesson") : null);
  })();
})();
