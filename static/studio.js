/* EcomVital Studio: record a tutorial in the browser (Classroom lessons).
   Screen + camera bubble (or screen only / camera only), camera picker, bubble shape / size / place (drag it on the
   preview), soft shadow, ring, mirror, background blur (MediaPipe selfie segmentation, loaded only when switched on),
   microphone picker with a level meter, computer sound, 720p / 1080p, 3-2-1 countdown, pause, mute, chapter marks,
   floating controls that stay on top of every window (Document Picture-in-Picture, Chrome / Edge), then a review with
   trim, chapters, "Save to the lesson" and "Download to my computer".
   The camera is drawn onto the screen frames with MediaStreamTrackProcessor / Generator, which keeps working while you
   are in another window or app (Chrome / Edge). Elsewhere screen-only and camera-only still work.
   window.EVStudio.open({ title, onSave(file, { trim_start, trim_end, chapters }) }) */
(function () {
  const SIZES = { s: 0.2, m: 0.28, l: 0.38 };            // bubble height as a share of the video height
  const KEY = "evStudio1";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const keep = (o) => { try { localStorage.setItem(KEY, JSON.stringify(o)); } catch { /* fine */ } };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (s) => { s = Math.max(0, Math.floor(s)); return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const canMix = "MediaStreamTrackProcessor" in window && "MediaStreamTrackGenerator" in window;

  const css = `
  .evs{position:fixed;inset:0;z-index:120;background:rgba(10,12,16,.72);display:grid;place-items:center;padding:16px;font:14px Inter,-apple-system,sans-serif;color:#111}
  .evs-box{background:#fff;border-radius:16px;width:min(1100px,100%);max-height:94vh;overflow:auto;box-shadow:0 30px 80px rgba(0,0,0,.4);display:grid;grid-template-columns:minmax(0,1fr) 320px}
  .evs-stage{background:#0b0d10;display:grid;place-items:center;min-height:380px;position:relative;border-radius:16px 0 0 16px;overflow:hidden}
  .evs-stage canvas,.evs-stage video{max-width:100%;max-height:72vh;display:block}
  .evs-hint{position:absolute;left:12px;bottom:10px;color:#cbd5e1;font-size:12px}
  .evs-side{padding:16px;display:grid;gap:12px;align-content:start}
  .evs-side h3{margin:0;font-size:16px}
  .evs-f{display:grid;gap:5px;font-size:12.5px;color:#475569}
  .evs-f select,.evs-f input[type=text]{font:inherit;font-size:13px;border:1px solid #d7dce3;border-radius:8px;padding:6px 8px;color:#111;background:#fff}
  .evs-seg{display:flex;gap:4px;flex-wrap:wrap}
  .evs-seg button{font:inherit;font-size:12.5px;border:1px solid #d7dce3;background:#fff;border-radius:8px;padding:5px 9px;cursor:pointer;color:#111}
  .evs-seg button.on{background:#111827;color:#fff;border-color:#111827}
  .evs-tog{display:flex;gap:8px;align-items:center;font-size:13px;color:#111}
  .evs-meter{height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden}.evs-meter i{display:block;height:100%;width:0;background:#10b981;transition:width .08s}
  .evs-go{font:inherit;font-weight:650;border:0;border-radius:10px;padding:11px;cursor:pointer;background:#ef4444;color:#fff;font-size:15px}
  .evs-btn{font:inherit;font-weight:600;border:1px solid #d7dce3;background:#fff;border-radius:9px;padding:8px 12px;cursor:pointer;color:#111}
  .evs-btn.pri{background:#111827;color:#fff;border-color:#111827}
  .evs-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .evs-note{font-size:12px;color:#64748b;line-height:1.45}
  .evs-count{position:absolute;inset:0;display:grid;place-items:center;font-size:120px;font-weight:800;color:#fff;background:rgba(0,0,0,.45)}
  .evs-bar{position:fixed;z-index:130;left:50%;bottom:18px;transform:translateX(-50%);display:flex;gap:8px;align-items:center;background:#111827;color:#fff;border-radius:999px;padding:8px 10px 8px 16px;box-shadow:0 10px 30px rgba(0,0,0,.35);font:600 14px Inter,sans-serif}
  .evs-bar button{font:inherit;font-size:13px;border:0;border-radius:999px;padding:6px 12px;cursor:pointer;background:#334155;color:#fff}
  .evs-bar button.stop{background:#fff;color:#111}
  .evs-dot{width:10px;height:10px;border-radius:50%;background:#ef4444;animation:evsb 1s infinite}.evs-dot.p{animation:none;background:#f59e0b}
  @keyframes evsb{50%{opacity:.3}}
  .evs-trim{display:grid;gap:6px}.evs-trim input[type=range]{width:100%}
  .evs-ch{display:grid;gap:4px;max-height:140px;overflow:auto}.evs-ch div{display:flex;gap:6px;align-items:center}.evs-ch input{flex:1}
  @media(max-width:860px){.evs-box{grid-template-columns:1fr}.evs-stage{border-radius:16px 16px 0 0}}`;

  function el(html) { const d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild; }

  async function open(opts) {
    if (!document.getElementById("evsCss")) { const s = document.createElement("style"); s.id = "evsCss"; s.textContent = css; document.head.appendChild(s); }
    const st = Object.assign({ mode: "both", cam: "", mic: "", shape: "circle", size: "m", pos: { x: 0.86, y: 0.8 }, shadow: true, ring: true,
      mirror: true, blur: false, sys: true, quality: "1080" }, load());
    if (!canMix && st.mode === "both") st.mode = "screen";
    const root = el(`<div class="evs"><div class="evs-box">
      <div class="evs-stage" id="evsStage"><canvas id="evsPrev" width="1280" height="720"></canvas><span class="evs-hint" id="evsHint"></span></div>
      <div class="evs-side" id="evsSide"></div></div></div>`);
    document.body.appendChild(root);
    const $ = (id) => root.querySelector("#" + id);
    let cams = [], mics = [], camStream = null, micStream = null, ac = null, analyser = null, meterRaf = 0, prevRaf = 0;
    let seg = null, segBusy = false, mask = null, maskW = 0, maskH = 0;
    const camVideo = document.createElement("video"); camVideo.muted = true; camVideo.playsInline = true;
    const work = document.createElement("canvas"), blurC = document.createElement("canvas");

    async function devices() {
      try { const tmp = await navigator.mediaDevices.getUserMedia({ audio: true, video: true }); tmp.getTracks().forEach((t) => t.stop()); }
      catch { try { const t2 = await navigator.mediaDevices.getUserMedia({ audio: true }); t2.getTracks().forEach((t) => t.stop()); } catch { /* none */ } }
      const all = await navigator.mediaDevices.enumerateDevices();
      cams = all.filter((d) => d.kind === "videoinput"); mics = all.filter((d) => d.kind === "audioinput");
      if (!cams.some((d) => d.deviceId === st.cam)) st.cam = (cams[0] || {}).deviceId || "";
      if (!mics.some((d) => d.deviceId === st.mic)) st.mic = (mics[0] || {}).deviceId || "";
    }
    async function startCam() {
      if (camStream) camStream.getTracks().forEach((t) => t.stop());
      camStream = null;
      if (st.mode === "screen" || !st.cam) { camVideo.srcObject = null; return; }
      try {
        camStream = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: st.cam }, width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30 } } });
        camVideo.srcObject = camStream; await camVideo.play();
      } catch (e) { hint("Camera not available: " + (e.message || e.name)); }
    }
    async function startMic() {
      if (micStream) micStream.getTracks().forEach((t) => t.stop());
      micStream = null;
      if (!st.mic) return;
      try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: st.mic }, echoCancellation: true, noiseSuppression: true, autoGainControl: true, sampleRate: 48000 } });
        if (!ac) ac = new AudioContext();
        analyser = ac.createAnalyser(); analyser.fftSize = 512;
        ac.createMediaStreamSource(micStream).connect(analyser);
      } catch (e) { hint("Microphone not available: " + (e.message || e.name)); }
    }
    function hint(t) { $("evsHint").textContent = t || ""; }
    function meter() {
      if (analyser && $("evsMeter")) {
        const a = new Uint8Array(analyser.fftSize); analyser.getByteTimeDomainData(a);
        let peak = 0; for (const v of a) peak = Math.max(peak, Math.abs(v - 128));
        $("evsMeter").style.width = Math.min(100, peak / 128 * 260) + "%";
      }
      meterRaf = requestAnimationFrame(meter);
    }

    // ---- background blur (MediaPipe selfie segmentation, loaded on demand)
    async function loadSeg() {
      if (seg) return seg;
      hint("Loading background blur…");
      const base = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
      const vision = await import(`${base}/vision_bundle.mjs`);
      const files = await vision.FilesetResolver.forVisionTasks(`${base}/wasm`);
      seg = await vision.ImageSegmenter.createFromOptions(files, { baseOptions: { modelAssetPath:
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite", delegate: "GPU" },
        runningMode: "VIDEO", outputConfidenceMasks: true, outputCategoryMask: false });
      hint("");
      return seg;
    }
    function runSeg(src, w, h) {
      if (!seg || segBusy) return;
      segBusy = true;
      try {
        work.width = 256; work.height = Math.round(256 * h / w);
        work.getContext("2d").drawImage(src, 0, 0, work.width, work.height);
        seg.segmentForVideo(work, performance.now(), (r) => {
          const m = r.confidenceMasks && r.confidenceMasks[0];
          if (m) { mask = m.getAsFloat32Array().slice(); maskW = m.width; maskH = m.height; }
        });
      } catch { /* skip a frame */ }
      segBusy = false;
    }
    // the person sharp, the room blurred (onto `out` at w x h)
    function blurred(src, w, h) {
      blurC.width = w; blurC.height = h;
      const b = blurC.getContext("2d");
      b.filter = "blur(14px)"; b.drawImage(src, 0, 0, w, h); b.filter = "none";
      if (mask) {
        const mc = new OffscreenCanvas(maskW, maskH), mx = mc.getContext("2d"), img = mx.createImageData(maskW, maskH);
        for (let i = 0; i < mask.length; i++) { img.data[i * 4 + 3] = Math.min(255, mask[i] * 320); }
        mx.putImageData(img, 0, 0);
        const pc = new OffscreenCanvas(w, h), px = pc.getContext("2d");
        px.drawImage(mc, 0, 0, w, h); px.globalCompositeOperation = "source-in"; px.drawImage(src, 0, 0, w, h);
        b.drawImage(pc, 0, 0);
      }
      return blurC;
    }

    // ---- drawing: the screen (letterboxed) and the camera bubble
    function bubbleRect(W, H) {
      const h = Math.round(H * SIZES[st.size]), bw = st.shape === "wide" ? Math.round(h * 16 / 9) : h;   // round / rounded / square = 1:1
      const x = Math.round(Math.min(Math.max(st.pos.x * W - bw / 2, 8), W - bw - 8)), y = Math.round(Math.min(Math.max(st.pos.y * H - h / 2, 8), H - h - 8));
      return { x, y, w: bw, h };
    }
    function shapePath(ctx, r) {
      ctx.beginPath();
      if (st.shape === "circle") ctx.arc(r.x + r.w / 2, r.y + r.h / 2, r.h / 2, 0, Math.PI * 2);
      else ctx.roundRect(r.x, r.y, r.w, r.h, st.shape === "square" ? Math.round(r.h * 0.06) : Math.round(r.h * 0.12));
    }
    function drawCover(ctx, src, sw, sh, r, mirror) {
      const s = Math.max(r.w / sw, r.h / sh), dw = sw * s, dh = sh * s;
      ctx.save();
      if (mirror) { ctx.translate(r.x + r.w, r.y); ctx.scale(-1, 1); ctx.drawImage(src, (r.w - dw) / 2, (r.h - dh) / 2, dw, dh); }
      else ctx.drawImage(src, r.x + (r.w - dw) / 2, r.y + (r.h - dh) / 2, dw, dh);
      ctx.restore();
    }
    function drawCam(ctx, W, H, src, sw, sh, full) {
      if (!src || !sw) return;
      let img = src;
      if (st.blur && seg) { runSeg(src, sw, sh); img = blurred(src, sw, sh); }
      if (full) { drawCover(ctx, img, sw, sh, { x: 0, y: 0, w: W, h: H }, st.mirror); return; }
      const r = bubbleRect(W, H);
      if (st.shadow) { ctx.save(); ctx.shadowColor = "rgba(0,0,0,.45)"; ctx.shadowBlur = Math.round(H * 0.03); ctx.shadowOffsetY = Math.round(H * 0.008); shapePath(ctx, r); ctx.fillStyle = "#000"; ctx.fill(); ctx.restore(); }
      ctx.save(); shapePath(ctx, r); ctx.clip(); drawCover(ctx, img, sw, sh, r, st.mirror); ctx.restore();
      if (st.ring) { ctx.save(); shapePath(ctx, r); ctx.lineWidth = Math.max(3, Math.round(H * 0.006)); ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.stroke(); ctx.restore(); }
    }
    function drawScreen(ctx, W, H, src, sw, sh) {
      ctx.fillStyle = "#0b0d10"; ctx.fillRect(0, 0, W, H);
      if (!src || !sw) return;
      const s = Math.min(W / sw, H / sh), dw = sw * s, dh = sh * s;
      ctx.drawImage(src, (W - dw) / 2, (H - dh) / 2, dw, dh);
    }

    // ---- live preview while setting up (a sample screen behind the bubble)
    function preview() {
      const c = $("evsPrev"), ctx = c.getContext("2d"), W = c.width, H = c.height;
      if (st.mode === "camera") { ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H); drawCam(ctx, W, H, camVideo, camVideo.videoWidth, camVideo.videoHeight, true); }
      else {
        const g = ctx.createLinearGradient(0, 0, W, H); g.addColorStop(0, "#1f2937"); g.addColorStop(1, "#334155");
        ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = "rgba(255,255,255,.08)"; for (let i = 0; i < 6; i++) ctx.fillRect(80, 90 + i * 90, W - 160 - i * 80, 34);
        ctx.fillStyle = "rgba(255,255,255,.5)"; ctx.font = "600 26px Inter, sans-serif"; ctx.fillText("Your screen shows here", 80, 60);
        if (st.mode === "both") drawCam(ctx, W, H, camVideo, camVideo.videoWidth, camVideo.videoHeight, false);
      }
      prevRaf = requestAnimationFrame(preview);
    }
    // drag the bubble on the preview
    $("evsStage").addEventListener("pointerdown", (e) => {
      const c = e.target.closest("#evsPrev");
      if (!c || st.mode !== "both") return;
      const rect = c.getBoundingClientRect();
      const move = (ev) => { st.pos = { x: Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height)) }; };
      move(e);
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); keep(st); side(); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
    });

    const seg2 = (key, list) => `<div class="evs-seg">${list.map(([v, t]) => `<button data-k="${key}" data-v="${v}" class="${String(st[key]) === v ? "on" : ""}">${t}</button>`).join("")}</div>`;
    function side() {
      $("evsSide").innerHTML = `<h3>Record a tutorial${opts.title ? `<br><small style="font-weight:500;color:#64748b">${esc(opts.title)}</small>` : ""}</h3>
        <div class="evs-f">What to record${seg2("mode", [["both", "Screen + camera"], ["screen", "Screen only"], ["camera", "Camera only"]])}
          ${!canMix ? '<span class="evs-note">Screen + camera needs Chrome or Edge.</span>' : ""}</div>
        ${st.mode !== "screen" ? `<label class="evs-f">Camera<select data-sel="cam">${cams.map((d, i) => `<option value="${esc(d.deviceId)}"${d.deviceId === st.cam ? " selected" : ""}>${esc(d.label || "Camera " + (i + 1))}</option>`).join("") || "<option>No camera found</option>"}</select></label>` : ""}
        ${st.mode === "both" ? `<div class="evs-f">Camera shape${seg2("shape", [["circle", "● Round"], ["rounded", "▢ Rounded"], ["square", "■ Square"], ["wide", "▭ Wide"]])}</div>
          <div class="evs-f">Size${seg2("size", [["s", "Small"], ["m", "Medium"], ["l", "Large"]])}</div>
          <div class="evs-f">Place${seg2("corner", [["tl", "↖"], ["tr", "↗"], ["bl", "↙"], ["br", "↘"]])}<span class="evs-note">or drag the bubble on the preview</span></div>
          <label class="evs-tog"><input type="checkbox" data-t="shadow"${st.shadow ? " checked" : ""}> Soft shadow around the camera</label>
          <label class="evs-tog"><input type="checkbox" data-t="ring"${st.ring ? " checked" : ""}> White ring</label>` : ""}
        ${st.mode !== "screen" ? `<label class="evs-tog"><input type="checkbox" data-t="blur"${st.blur ? " checked" : ""}> Blur my background</label>
          <label class="evs-tog"><input type="checkbox" data-t="mirror"${st.mirror ? " checked" : ""}> Mirror the camera</label>` : ""}
        <label class="evs-f">Microphone<select data-sel="mic"><option value="">No microphone</option>${mics.map((d, i) => `<option value="${esc(d.deviceId)}"${d.deviceId === st.mic ? " selected" : ""}>${esc(d.label || "Microphone " + (i + 1))}</option>`).join("")}</select>
          <div class="evs-meter"><i id="evsMeter"></i></div></label>
        ${st.mode !== "camera" ? `<label class="evs-tog"><input type="checkbox" data-t="sys"${st.sys ? " checked" : ""}> Record the computer's sound too</label>` : ""}
        <div class="evs-f">Quality${seg2("quality", [["720", "720p (smaller)"], ["1080", "1080p HD"]])}</div>
        <button class="evs-go" id="evsGo">● Start recording</button>
        <button class="evs-btn" id="evsClose">Cancel</button>
        <p class="evs-note">You'll pick the screen, window or tab to share. A 3-2-1 countdown starts, and floating controls stay on top
          (pause, mute, chapter mark, stop). Press <b>M</b> in the controls to mark a chapter. Clicks aren't highlighted: browsers
          can't see clicks in other windows.</p>`;
    }
    root.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-k]");
      if (b) {
        const k = b.dataset.k, v = b.dataset.v;
        if (k === "corner") st.pos = { tl: { x: 0.14, y: 0.2 }, tr: { x: 0.86, y: 0.2 }, bl: { x: 0.14, y: 0.8 }, br: { x: 0.86, y: 0.8 } }[v];
        else st[k] = v;
        keep(st); side();
        if (k === "mode") await startCam();
        return;
      }
      if (e.target.closest("#evsClose")) { cleanup(); return; }
      if (e.target.closest("#evsGo")) { go(); }
    });
    root.addEventListener("change", async (e) => {
      const s = e.target.closest("[data-sel]"), t = e.target.closest("[data-t]");
      if (s) { st[s.dataset.sel] = s.value; keep(st); if (s.dataset.sel === "cam") await startCam(); else await startMic(); }
      if (t) {
        st[t.dataset.t] = t.checked; keep(st);
        if (t.dataset.t === "blur" && t.checked) { try { await loadSeg(); } catch (err) { st.blur = false; keep(st); side(); hint("Background blur couldn't load: " + (err.message || err)); } }
      }
    });
    function cleanup() {
      cancelAnimationFrame(prevRaf); cancelAnimationFrame(meterRaf);
      for (const s of [camStream, micStream]) if (s) s.getTracks().forEach((t) => t.stop());
      if (ac) ac.close().catch(() => {});
      root.remove();
    }

    await devices();
    side();
    await startCam(); await startMic();
    if (st.blur) loadSeg().catch(() => { st.blur = false; side(); });
    preview(); meter();

    /* ------------------------------------------------ recording */
    async function go() {
      let screen = null;
      const H = st.quality === "720" ? 720 : 1080;
      if (st.mode !== "camera") {
        try {
          screen = await navigator.mediaDevices.getDisplayMedia({ video: { width: { ideal: H * 16 / 9 }, height: { ideal: H }, frameRate: { ideal: 30, max: 30 }, cursor: "always" },
            audio: !!st.sys, selfBrowserSurface: "exclude", surfaceSwitching: "include", systemAudio: st.sys ? "include" : "exclude" });
        } catch { hint("Sharing was cancelled."); return; }
      }
      // sound: microphone + computer sound mixed
      if (!ac) ac = new AudioContext();
      const dest = ac.createMediaStreamDestination(), micGain = ac.createGain();
      if (micStream) { ac.createMediaStreamSource(micStream).connect(micGain); micGain.connect(dest); }
      if (screen && screen.getAudioTracks().length) ac.createMediaStreamSource(screen).connect(dest);
      // picture
      let videoTrack, stopMix = () => {};
      if (st.mode === "screen") videoTrack = screen.getVideoTracks()[0];
      else {
        const sTrack = screen && screen.getVideoTracks()[0], set = sTrack ? sTrack.getSettings() : {};
        const W0 = st.mode === "camera" ? Math.round(H * 16 / 9) : Math.min(set.width || H * 16 / 9, Math.round(H * 16 / 9));
        const H0 = st.mode === "camera" ? H : Math.round(W0 * (set.height || H) / (set.width || H * 16 / 9));
        const W = W0 - W0 % 2, Hh = H0 - H0 % 2;
        if (canMix) {
          const out = new OffscreenCanvas(W, Hh), ctx = out.getContext("2d");
          const gen = new MediaStreamTrackGenerator({ kind: "video" }), writer = gen.writable.getWriter();
          let screenFrame = null, camFrame = null, running = true;
          const camTrack = camStream && camStream.getVideoTracks()[0];
          const readLoop = async (track, onFrame) => {
            const reader = new MediaStreamTrackProcessor({ track }).readable.getReader();
            while (running) { const { value, done } = await reader.read(); if (done) break; onFrame(value); }
            reader.releaseLock();
          };
          let busy = false;
          const paint = async () => {
            if (busy) return;                 // drop a frame rather than queue them
            busy = true;
            if (st.mode === "camera") drawCam(ctx, W, Hh, camFrame, camFrame && camFrame.displayWidth, camFrame && camFrame.displayHeight, true);
            else {
              drawScreen(ctx, W, Hh, screenFrame, screenFrame && screenFrame.displayWidth, screenFrame && screenFrame.displayHeight);
              if (camFrame) drawCam(ctx, W, Hh, camFrame, camFrame.displayWidth, camFrame.displayHeight, false);
            }
            const vf = new VideoFrame(out, { timestamp: Math.round(performance.now() * 1000) });
            try { await writer.write(vf); } catch { /* recorder gone */ }
            busy = false;
          };
          // the camera (steady 30 fps) drives the output; a still screen sends few frames, so it never freezes the face
          if (sTrack) readLoop(sTrack, (f) => { if (screenFrame) screenFrame.close(); screenFrame = f; if (!camTrack) paint(); });
          if (camTrack) readLoop(camTrack, (f) => { if (camFrame) camFrame.close(); camFrame = f; paint(); });
          stopMix = () => { running = false; for (const f of [screenFrame, camFrame]) if (f) f.close(); writer.close().catch(() => {}); };
          videoTrack = gen;
        } else {
          videoTrack = camStream.getVideoTracks()[0];          // camera only, as is
        }
      }
      const stream = new MediaStream([videoTrack, ...dest.stream.getAudioTracks()]);
      const types = ["video/mp4;codecs=avc1.640028,mp4a.40.2", "video/mp4", "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"];
      const type = types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
      const rec = new MediaRecorder(stream, { ...(type ? { mimeType: type } : {}), videoBitsPerSecond: H >= 1080 ? 8_000_000 : 4_500_000, audioBitsPerSecond: 160_000 });
      const chunks = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };

      // countdown on the stage
      cancelAnimationFrame(prevRaf);
      const cd = el(`<div class="evs-count">3</div>`); $("evsStage").appendChild(cd);
      for (const n of [3, 2, 1]) { cd.textContent = n; await new Promise((r) => setTimeout(r, 800)); }
      cd.remove();

      let elapsed = 0, lastStart = performance.now(), paused = false, muted = false;
      const marks = [];
      const now = () => elapsed + (paused ? 0 : (performance.now() - lastStart) / 1000);
      // floating controls: on top of every window when the browser allows it, else a bar on this page
      let pip = null;
      if (window.documentPictureInPicture) {
        try { pip = await documentPictureInPicture.requestWindow({ width: 380, height: 70 }); } catch { pip = null; }
      }
      const host = pip ? pip.document.body : document.body;
      if (pip) { const s = pip.document.createElement("style"); s.textContent = css + "body{margin:0;background:#111827}.evs-bar{position:static;transform:none;border-radius:0;height:100vh;box-sizing:border-box;box-shadow:none}"; pip.document.head.appendChild(s); pip.document.title = "EcomVital recording"; }
      const bar = el(`<div class="evs-bar"><span class="evs-dot" id="d"></span><span id="t">0:00</span>
        <button id="p">Pause</button><button id="m">Mute</button><button id="c" title="Mark a chapter (M)">+ Chapter</button><button class="stop" id="s">Stop</button></div>`);
      host.appendChild(bar);
      const q = (id) => bar.querySelector("#" + id);
      root.hidden = true;
      const tick = setInterval(() => { const s = clock(now()); q("t").textContent = s; document.title = `● ${s} Recording · EcomVital`; }, 500);
      const oldTitle = document.title;
      const pause = () => {
        if (rec.state === "recording") { rec.pause(); elapsed = now(); paused = true; q("p").textContent = "Resume"; q("d").classList.add("p"); }
        else if (rec.state === "paused") { rec.resume(); lastStart = performance.now(); paused = false; q("p").textContent = "Pause"; q("d").classList.remove("p"); }
      };
      const mark = () => { const at = now(); marks.push({ at, label: `Chapter ${marks.length + 1}` }); q("c").textContent = `+ Chapter (${marks.length})`; };
      q("p").onclick = pause;
      q("m").onclick = () => { muted = !muted; micGain.gain.value = muted ? 0 : 1; q("m").textContent = muted ? "Unmute" : "Mute"; };
      q("c").onclick = mark;
      (pip ? pip.document : document).addEventListener("keydown", (e) => { if (e.key === "m" || e.key === "M") mark(); });
      const done = new Promise((resolve) => { rec.onstop = resolve; });
      const stop = () => { if (rec.state !== "inactive") { elapsed = now(); paused = true; rec.stop(); } };
      q("s").onclick = stop;
      if (screen) screen.getVideoTracks()[0].addEventListener("ended", stop);
      if (pip) pip.addEventListener("pagehide", stop);
      rec.start(1000);
      await done;
      clearInterval(tick); document.title = oldTitle; stopMix();
      bar.remove(); if (pip) pip.close();
      if (screen) screen.getTracks().forEach((t) => t.stop());
      root.hidden = false;
      const blob = new Blob(chunks, { type: (type || "video/webm").split(";")[0] });
      review(blob, elapsed, marks);
    }

    /* ------------------------------------------------ review: trim, chapters, save or download */
    function review(blob, length, marks) {
      cancelAnimationFrame(meterRaf);
      const ext = blob.type.includes("mp4") ? "mp4" : "webm";
      const name = `${(opts.title || "recording").replace(/[^\w-]+/g, "-").slice(0, 50)}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${ext}`;
      const url = URL.createObjectURL(blob);
      $("evsStage").innerHTML = `<video id="evsVid" src="${url}" controls playsinline></video>`;
      let a = 0, b = length;
      const total = Math.max(1, Math.round(length));
      $("evsSide").innerHTML = `<h3>Your recording · ${clock(length)}</h3>
        <span class="evs-note">${(blob.size / 1e6).toFixed(1)} MB as recorded. EcomVital compresses it when you save (smaller, same quality to the eye).</span>
        <div class="evs-trim"><b style="font-size:13px">Trim</b>
          <label class="evs-f">Start at <span id="evsA">0:00</span><input type="range" id="evsTA" min="0" max="${total}" value="0" step="1"></label>
          <label class="evs-f">End at <span id="evsB">${clock(length)}</span><input type="range" id="evsTB" min="0" max="${total}" value="${total}" step="1"></label>
          <span class="evs-note">Drag to cut the start or the end; the video jumps there so you can check.</span></div>
        <div class="evs-f"><b style="font-size:13px;color:#111">Chapters</b><div class="evs-ch" id="evsCh">${marks.map((m, i) => `<div><span>${clock(m.at)}</span><input type="text" data-ch="${i}" value="${esc(m.label)}"></div>`).join("") || '<span class="evs-note">No chapter marks.</span>'}</div></div>
        ${opts.onSave ? '<button class="evs-go" id="evsSave" style="background:#111827">Save to the lesson</button>' : ""}
        <a class="evs-btn" id="evsDl" href="${url}" download="${esc(name)}" style="text-align:center;text-decoration:none">⬇ Download to my computer</a>
        <div class="evs-row"><button class="evs-btn" id="evsAgain">Record again</button><button class="evs-btn" id="evsDiscard">Discard</button></div>
        <span class="evs-note">The download is the full recording as it was made (before trimming).</span>`;
      const vid = $("evsVid");
      $("evsTA").oninput = (e) => { a = Math.min(+e.target.value, b - 1); e.target.value = a; $("evsA").textContent = clock(a); vid.currentTime = a; };
      $("evsTB").oninput = (e) => { b = Math.max(+e.target.value, a + 1); e.target.value = b; $("evsB").textContent = clock(b); vid.currentTime = b; };
      $("evsSide").addEventListener("input", (e) => { const c = e.target.closest("[data-ch]"); if (c) marks[+c.dataset.ch].label = c.value; });
      $("evsAgain").onclick = () => { if (!confirm("Throw this recording away and record again?")) return; URL.revokeObjectURL(url); $("evsStage").innerHTML = '<canvas id="evsPrev" width="1280" height="720"></canvas><span class="evs-hint" id="evsHint"></span>'; side(); preview(); meter(); };
      $("evsDiscard").onclick = () => { if (!confirm("Discard this recording?")) return; URL.revokeObjectURL(url); cleanup(); };
      const save = $("evsSave");
      if (save) save.onclick = () => {
        const file = new File([blob], name, { type: blob.type });
        const chapters = marks.filter((m) => m.at >= a && m.at <= b).map((m) => ({ at: Math.max(0, m.at - a), label: m.label }));
        cleanup();
        opts.onSave(file, { trim_start: a > 0 ? a : "", trim_end: b < total ? b : "", chapters });
      };
    }
  }

  window.EVStudio = { open };
})();
