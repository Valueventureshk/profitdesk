/* EcomVital World, phase 1: one store's shoppers walking around a roofless isometric shop.
   Viewing only. Live mode asks /api/world/state every 4 s (a snapshot of the active sessions plus
   purchases since the last cursor); Demo mode makes up visitors in this browser and says so
   everywhere. No figure is worked out here beyond counting what the server sent. */
(async function () {
  const $ = (s) => document.querySelector(s);
  const { iso, unIso } = WorldEnv;
  const POLL_MS = 4000, DEMO_MS = 2500;
  const q = new URLSearchParams(location.search);

  // ---------------------------------------------------------------- which store
  let stores = [], storeId = null, me = null;
  try {
    const r = await fetch("/api/world/stores");
    if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); return; }
    const d = await r.json();
    stores = d.stores || [];
    storeId = Number(q.get("store")) || d.default;
  } catch (e) { /* offline: demo still works */ }
  let store = stores.find((s) => s.id === storeId && s.live) || stores.find((s) => s.live) || null;
  if (store) storeId = store.id;
  const others = stores.filter((s) => !store || s.id !== store.id);
  window.pdMe && window.pdMe().then((m) => { me = m; setupSettings(); });

  // ---------------------------------------------------------------- scene
  const app = new PIXI.Application();
  await app.init({ resizeTo: window, background: 0xE9E5DA, antialias: true, autoDensity: true,
                   resolution: Math.min(window.devicePixelRatio || 1, 2) });
  $("#stage").appendChild(app.canvas);
  WorldChars.init(app);
  const env = WorldEnv.build(app, [store || { id: null, name: "Your store" }, ...others]);
  const world = env.root, lot = env.mainLot;
  app.stage.addChild(world);
  const fxLayer = new PIXI.Container();
  world.addChild(fxLayer);
  $("#storeName").textContent = store ? store.name : "No store in World yet";

  // ---------------------------------------------------------------- camera: pan, pinch, wheel, fly
  const cam = { x: 0, y: 0, s: 1 };
  let flight = null;
  const clampS = (s) => Math.max(0.18, Math.min(3.2, s));
  function applyCam() { world.position.set(cam.x, cam.y); world.scale.set(cam.s); }
  function rectOf(b, lift = 130) {
    return { x0: iso(b.x0, b.y1).x, x1: iso(b.x1, b.y0).x, y0: iso(b.x0, b.y0).y - lift, y1: iso(b.x1, b.y1).y };
  }
  function viewFor(r, pad = 40) {
    const narrow = app.screen.width < 700;
    pad = narrow ? Math.min(pad, 8) : pad;
    const top = narrow ? 120 : 84, bottom = narrow ? 170 : 40, left = narrow ? 0 : 290;
    const w = app.screen.width - left - pad * 2, h = app.screen.height - top - bottom - pad * 2;
    const s = clampS(Math.min(w / (r.x1 - r.x0), h / (r.y1 - r.y0)));
    return { s, x: left + pad + w / 2 - ((r.x0 + r.x1) / 2) * s, y: top + pad + h / 2 - ((r.y0 + r.y1) / 2) * s };
  }
  function flyTo(v, ms = 1200) { flight = { from: { ...cam }, to: v, t: 0, ms }; }
  const overview = () => viewFor(rectOf({ x0: 0, y0: 0, x1: env.size, y1: env.size }, 60), 10);
  const storeView = () => viewFor(rectOf(lot.bounds));
  function zoomAt(sx, sy, f) {
    const wx = (sx - cam.x) / cam.s, wy = (sy - cam.y) / cam.s;
    cam.s = clampS(cam.s * f); cam.x = sx - wx * cam.s; cam.y = sy - wy * cam.s; applyCam();
  }

  const pointers = new Map();
  let drag = null;
  const cv = app.canvas;
  cv.style.touchAction = "none";
  cv.addEventListener("pointerdown", (e) => {
    if (tv.on) return;
    cv.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    flight = null;
    drag = { x: e.clientX, y: e.clientY, at: Date.now(), moved: 0, pinch: pointers.size === 2 ? pinchInfo() : null };
  });
  function pinchInfo() {
    const [a, b] = [...pointers.values()];
    return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
  }
  cv.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId) || !drag) return;
    const prev = pointers.get(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size >= 2) {
      const now = pinchInfo();
      if (!drag.pinch) drag.pinch = now;
      zoomAt(now.mx, now.my, now.d / drag.pinch.d);
      cam.x += now.mx - drag.pinch.mx; cam.y += now.my - drag.pinch.my; applyCam();
      drag.pinch = now; drag.moved += 10;
    } else {
      cam.x += e.clientX - prev.x; cam.y += e.clientY - prev.y; applyCam();
      drag.moved += Math.abs(e.clientX - prev.x) + Math.abs(e.clientY - prev.y);
    }
  });
  const up = (e) => {
    pointers.delete(e.pointerId);
    if (drag && pointers.size === 0) {
      if (drag.moved < 8 && Date.now() - drag.at < 500) tap(e.clientX, e.clientY);
      drag = null;
    } else if (drag) drag.pinch = null;
  };
  cv.addEventListener("pointerup", up);
  cv.addEventListener("pointercancel", up);
  cv.addEventListener("wheel", (e) => { e.preventDefault(); if (tv.on) return; flight = null; zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0016)); }, { passive: false });

  function tap(sx, sy) {
    const wx = (sx - cam.x) / cam.s, wy = (sy - cam.y) / cam.s;
    // a shopper under the finger: say what they're doing (nothing personal is known)
    let best = null, bd = 26 / Math.max(cam.s, 0.5);
    for (const p of people.values()) {
      const s = p.c.position, d = Math.hypot(s.x - wx, s.y - 20 - wy);
      if (d < bd) { bd = d; best = p; }
    }
    if (best) return showTip(best, sx, sy);
    hideTip();
    const t = unIso(wx, wy);
    const hit = env.lots.find((l) => t.x >= l.bounds.x0 - 0.5 && t.x <= l.bounds.x1 + 0.5 && t.y >= l.bounds.y0 - 0.5 && t.y <= l.bounds.y1 + 0.5);
    if (!hit || hit.park) return;
    if (hit.main) return flyTo(storeView());
    toast(`${hit.name} isn't in World yet`, "Phase 1 shows one store. The others join in phase 2.", "info");
  }

  // ---------------------------------------------------------------- shoppers
  const people = new Map();
  const floorZ = (x, y) => (x >= lot.bounds.x0 && x <= lot.bounds.x0 + 14 && y >= lot.bounds.y0 && y <= lot.bounds.y0 + 11 ? 7 : 3);
  const inside = (x, y) => floorZ(x, y) === 7;
  const DOOR_IN = { x: lot.door.x, y: lot.door.y - 3.3 }, DOOR_OUT = { x: lot.door.x, y: lot.door.y };
  function spotIn(zone) {
    const rs = lot.zones[zone] || lot.zones.entrance;
    const r = rs[Math.floor(Math.random() * rs.length)];
    return { x: r.x0 + Math.random() * (r.x1 - r.x0), y: r.y0 + Math.random() * (r.y1 - r.y0) };
  }
  function route(p, to) {
    const path = [];
    if (!inside(p.x, p.y) && inside(to.x, to.y)) path.push({ ...DOOR_OUT }, { ...DOOR_IN });
    if (inside(p.x, p.y) && !inside(to.x, to.y)) path.push({ ...DOOR_IN }, { ...DOOR_OUT });
    path.push(to);
    p.path = path;
  }
  function place(p) {
    const s = iso(p.x, p.y, floorZ(p.x, p.y));
    p.c.position.set(s.x, s.y);
    p.c.zIndex = p.x + p.y + 0.5;
  }
  function spawn(a, arriving) {
    const c = WorldChars.shopper(a.id);
    const p = { id: a.id, c, x: DOOR_OUT.x + (Math.random() - 0.5) * 2, y: DOOR_OUT.y + 1.2 + Math.random(),
                path: [], zone: a.zone, state: null, speed: 1.4 + Math.random() * 0.6, wander: 4 + Math.random() * 8, leaving: false, data: a };
    if (!arriving) { const s = spotIn(a.zone); p.x = s.x; p.y = s.y; } else route(p, spotIn(a.zone));
    env.objects.addChild(c);
    place(p);
    people.set(a.id, p);
    return p;
  }
  function leave(p) {
    p.leaving = true;
    route(p, { x: DOOR_OUT.x + (Math.random() - 0.5) * 3, y: DOOR_OUT.y + 2.5 });
  }
  function step(dt) {
    for (const p of people.values()) {
      const tgt = p.path[0];
      if (tgt) {
        const dx = tgt.x - p.x, dy = tgt.y - p.y, d = Math.hypot(dx, dy), mv = p.speed * dt;
        if (d <= mv) { p.x = tgt.x; p.y = tgt.y; p.path.shift(); } else { p.x += dx / d * mv; p.y += dy / d * mv; }
        const sx = iso(dx, dy).x;
        if (Math.abs(sx) > 0.01) p.c.parts.body.scale.x = sx > 0 ? 1 : -1;
        p.c.parts.body.y = -Math.abs(Math.sin(performance.now() / 90 + p.speed * 10)) * 1.6;
        place(p);
      } else {
        p.c.parts.body.y = 0;
        if (p.leaving) {
          p.c.alpha -= dt * 1.5;
          if (p.c.alpha <= 0) { p.c.destroy({ children: true }); people.delete(p.id); }
          continue;
        }
        if (p.state !== "inactive" && p.state !== "purchased") {
          p.wander -= dt;
          if (p.wander <= 0) { p.wander = 5 + Math.random() * 10; route(p, spotIn(p.zone)); }
        }
      }
    }
  }

  // ---------------------------------------------------------------- purchases
  const partied = new Set();
  const effects = [];
  const money = (v, cur) => {
    if (v === null || v === undefined) return "";
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "AUD", maximumFractionDigits: 2 }).format(v); }
    catch (e) { return (cur || "") + " " + Number(v).toFixed(2); }
  };
  function celebrate(pu, cur) {
    if (partied.has(pu.order)) return;
    partied.add(pu.order);
    const p = pu.avatar && people.get(pu.avatar);
    const late = pu.got && pu.at ? (Date.parse(pu.got) - Date.parse(pu.at)) / 60000 : 0;
    const label = pu.value ? "+" + money(pu.value, pu.currency || cur) : "New order!";
    const sub = pu.source === "shopify_order" ? "from Shopify · no session" : late > 3 ? `placed ${Math.round(late)} min ago` : "";
    const fx = WorldChars.party(label, sub);
    if (p) { fx.position.set(p.c.x, p.c.y); p.state = "purchased"; WorldChars.setState(p.c, "purchased"); }
    else { const s = iso(lot.till.x, lot.till.y, lot.till.z); fx.position.set(s.x, s.y); }
    fxLayer.addChild(fx);
    effects.push(fx);
    toast("🎉 " + (pu.value ? `New order · ${money(pu.value, pu.currency || cur)}` : "New order"),
      pu.source === "shopify_order" ? "Confirmed by Shopify. The pixel didn't see this shopper, so no one walks to the till."
        : late > 3 ? `Arrived late: placed ${Math.round(late)} min ago.` : "Confirmed purchase from a live shopper.", "win");
  }

  // ---------------------------------------------------------------- snapshot in, scene out
  let snap = null, cursor = 0, lastOk = 0, skew = 0, failing = false;
  function applySnap(s, fresh) {
    snap = s;
    const ids = new Set();
    for (const a of s.avatars) {
      ids.add(a.id);
      let p = people.get(a.id);
      if (!p) p = spawn(a, !fresh);
      else if (p.leaving) { p.leaving = false; p.c.alpha = 1; route(p, spotIn(a.zone)); }
      if (p.zone !== a.zone) { p.zone = a.zone; if (a.state !== "inactive") route(p, spotIn(a.zone)); }
      if (p.state !== a.state && !(p.state === "purchased")) { p.state = a.state; WorldChars.setState(p.c, a.state); }
      p.data = a;
    }
    for (const p of people.values()) if (!ids.has(p.id) && !p.leaving) leave(p);
    const cutoff = Date.now() - skew - 10 * 60000;          // after a long gap: count them, don't throw ten parties
    for (const pu of s.purchases || []) {
      if (Date.parse(pu.got || pu.at) >= cutoff) celebrate(pu, s.store && s.store.currency);
      else partied.add(pu.order);
    }
    hud();
  }

  // ---------------------------------------------------------------- live mode
  let mode = q.get("mode") === "demo" || !store ? "demo" : "live";
  let timer = null;
  async function poll() {
    clearTimeout(timer);
    if (mode !== "live") return;
    if (document.hidden) { timer = setTimeout(poll, POLL_MS); return; }
    try {
      const r = await fetch(`/api/world/state?store=${storeId}&cursor=${cursor}`, { cache: "no-store" });
      if (r.status === 401) { location.href = "/login?next=/world"; return; }
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
      const s = await r.json();
      if (mode !== "live") return;
      skew = Date.now() - Date.parse(s.now);
      const first = !cursor;
      cursor = s.cursor; lastOk = Date.now(); failing = false;
      applySnap(s, first);
    } catch (e) { failing = String(e.message || e); }
    badge();
    timer = setTimeout(poll, POLL_MS);
  }

  // ---------------------------------------------------------------- demo mode (made-up visitors, this browser only)
  const sim = {
    n: 0, list: new Map(), today: { orders: 12, value: 984.5 }, parties: [],
    rnd: (a) => a[Math.floor(Math.random() * a.length)],
    arrive() { const id = "demo-" + (++this.n); this.list.set(id, { id, stage: "browsing", state: "browsing", zone: this.rnd(["entrance", "aisle", "product"]), since: new Date().toISOString(), seen: new Date().toISOString(), pages: 1 }); },
    tick() {
      const now = new Date().toISOString();
      if (this.list.size < 70 && Math.random() < 0.75) this.arrive();
      if (Math.random() < 0.3) this.arrive();
      for (const s of [...this.list.values()]) {
        const r = Math.random();
        if (s.state === "purchased") { this.list.delete(s.id); continue; }
        if (s.state === "inactive") { if (r < 0.12) { s.state = s.stage; s.seen = now; } else if (r < 0.2) this.list.delete(s.id); continue; }
        if (r < 0.04) { s.state = "inactive"; continue; }
        if (r < 0.08) { this.list.delete(s.id); continue; }
        s.seen = now;
        if (s.stage === "browsing") {
          if (r < 0.16) { s.stage = s.state = "cart"; s.zone = "cart"; }
          else if (r < 0.45) { s.zone = this.rnd(["aisle", "product", "product"]); s.pages++; }
        } else if (s.stage === "cart") {
          if (r < 0.22) { s.stage = s.state = "checkout"; s.zone = "checkout"; }
          else if (r < 0.4) s.zone = this.rnd(["product", "cart"]);
        } else if (s.stage === "checkout" && r < 0.3) {
          s.stage = s.state = "purchased";
          const v = Math.round((39 + Math.random() * 110) * 100) / 100;
          this.today.orders++; this.today.value += v;
          this.parties.push({ order: "demo-o" + this.n + "-" + s.id, avatar: s.id, source: "pixel", value: v, currency: (store && store.currency) || "AUD", at: now, got: now });
        }
      }
      if (Math.random() < 0.03) {                            // now and then an order the pixel never saw
        const v = Math.round((45 + Math.random() * 80) * 100) / 100;
        this.today.orders++; this.today.value += v;
        this.parties.push({ order: "demo-x" + Date.now(), avatar: null, source: "shopify_order", value: v, currency: "AUD", at: now, got: now });
      }
    },
    snapshot() {
      const av = [...this.list.values()].map((a) => ({ ...a }));
      const counts = { browsing: 0, cart: 0, checkout: 0, inactive: 0, purchased: 0 };
      av.forEach((a) => counts[a.state]++);
      const p = this.parties; this.parties = [];
      return { mode: "demo", store: { id: storeId, name: store ? store.name : "Demo store", currency: (store && store.currency) || "AUD" },
               now: new Date().toISOString(), last_event: new Date().toISOString(), avatars: av, counts, hidden: 0,
               active: counts.browsing + counts.cart + counts.checkout, purchases: p, settings: settings(),
               today: { orders: this.today.orders, value: this.today.value, currency: (store && store.currency) || "AUD" } };
    },
  };
  function demoLoop() {
    clearTimeout(timer);
    if (mode !== "demo") return;
    sim.tick();
    skew = 0; lastOk = Date.now(); failing = false;
    applySnap(sim.snapshot(), false);
    badge();
    timer = setTimeout(demoLoop, DEMO_MS);
  }

  function setMode(m) {
    if (m === "live" && !store) { toast("Live needs a store", "No store is switched on for World yet.", "info"); m = "demo"; }
    mode = m;
    document.body.classList.toggle("is-demo", m === "demo");
    document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
    for (const p of people.values()) p.c.destroy({ children: true });
    people.clear(); partied.clear(); cursor = 0; snap = null;
    $("#demoBanner").hidden = m !== "demo";
    window.PDUrl && PDUrl.set({ mode: m === "demo" ? "demo" : "" });
    hud();
    if (m === "demo") { for (let i = 0; i < 34; i++) sim.arrive(); sim.list.forEach((s) => { if (Math.random() < 0.25) { s.stage = s.state = "cart"; s.zone = "cart"; } else if (Math.random() < 0.15) { s.stage = s.state = "checkout"; s.zone = "checkout"; } }); applySnap(sim.snapshot(), true); timer = setTimeout(demoLoop, DEMO_MS); }
    else poll();
    badge();
  }
  document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.addEventListener("visibilitychange", () => { if (!document.hidden && mode === "live") poll(); });

  // ---------------------------------------------------------------- HUD
  const secs = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; };
  function badge() {
    const b = $("#badge"), sub = $("#fresh");
    b.className = "w-badge";
    if (mode === "demo") { b.classList.add("demo"); b.textContent = "DEMO — simulated visitors"; sub.textContent = "Made up in this browser. Not real shoppers, not real orders."; return; }
    if (!lastOk) { b.classList.add("wait"); b.textContent = failing ? "LIVE · can't reach EcomVital" : "LIVE · connecting…"; sub.textContent = failing && failing !== "true" ? String(failing) : ""; return; }
    const age = Date.now() - lastOk;
    const stale = failing || age > POLL_MS * 4;
    b.classList.add(stale ? "stale" : "live");
    b.textContent = stale ? `LIVE · reconnecting… data as of ${secs(age)} ago` : `LIVE · data as of ${secs(age)} ago`;
    const le = snap && snap.last_event ? Date.now() - skew - Date.parse(snap.last_event) : null;
    sub.textContent = le === null ? "No shopper events in the last couple of hours." : `Newest shopper event ${secs(le)} ago`;
  }
  setInterval(badge, 1000);
  function hud() {
    const s = snap;
    const c = (s && s.counts) || {};
    $("#active").textContent = s ? s.active : "–";
    for (const k of ["browsing", "cart", "checkout", "inactive"]) $(`#n-${k}`).textContent = s ? c[k] || 0 : "–";
    const set = (s && s.settings) || settings();
    $("#activeHint").textContent = `Shoppers with activity in the last ${set.inactive_minutes} min. Grey ones have been idle ${set.inactive_minutes}+ min; they leave after ${set.leave_minutes} min.` + (s && s.hidden ? ` ${s.hidden} more not drawn.` : "");
    $("#todayN").textContent = s && s.today ? s.today.orders : "–";
    $("#todayV").textContent = s && s.today ? money(s.today.value, s.today.currency) : "";
    $("#todayDay").textContent = mode === "demo" ? "Demo figures" : s && s.today && s.today.date ? `${s.today.date}, store clock · history, not live` : "";
  }

  // ---------------------------------------------------------------- settings (inactivity threshold)
  let cfg = { inactive_minutes: 5, leave_minutes: 30 };
  function settings() { return (snap && snap.mode === "live" && snap.settings) || cfg; }
  function setupSettings() {
    const can = me && (me.role === "owner" || (me.access || {}).tracking === "manage");
    $("#setSave").hidden = !can;
    $("#setNote").textContent = can ? "Applies to everyone watching this store." : "Changing these needs Manage access to Tracking.";
    $("#setIn").disabled = $("#setOut").disabled = !can;
  }
  $("#setBtn").addEventListener("click", () => {
    const pop = $("#setPop"), s = settings();
    $("#setIn").value = s.inactive_minutes; $("#setOut").value = s.leave_minutes; $("#setErr").textContent = "";
    pop.hidden = !pop.hidden;
  });
  $("#setClose").addEventListener("click", () => { $("#setPop").hidden = true; });
  $("#setSave").addEventListener("click", async () => {
    const body = { inactive_minutes: Number($("#setIn").value), leave_minutes: Number($("#setOut").value) };
    if (!store) return;
    const r = await fetch(`/api/world/settings/${storeId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $("#setErr").textContent = d.detail || d.error || "Couldn't save."; return; }
    cfg = d.settings; $("#setPop").hidden = true;
    toast("Saved", `Inactive after ${cfg.inactive_minutes} min, leaves after ${cfg.leave_minutes} min.`, "info");
    if (mode === "live") poll();
  });

  // ---------------------------------------------------------------- tips + toasts
  function showTip(p, sx, sy) {
    const a = p.data || {}, t = $("#tip");
    const mins = a.since ? Math.max(1, Math.round((Date.now() - skew - Date.parse(a.since)) / 60000)) : null;
    const idle = a.seen ? Math.round((Date.now() - skew - Date.parse(a.seen)) / 60000) : 0;
    t.innerHTML = `<b>${mode === "demo" ? "Simulated shopper" : "Shopper"}</b><span class="w-st st-${p.state}">${WorldChars.NAMES[p.state] || p.state}</span>` +
      `<span>${mins ? `${mins} min in the store` : ""}${a.pages ? ` · ${a.pages} page${a.pages > 1 ? "s" : ""}` : ""}${idle >= 1 ? ` · last seen ${idle} min ago` : ""}</span>` +
      `<small>Anonymous: no name, email or location is known here.</small>`;
    t.style.left = Math.min(sx + 12, window.innerWidth - 230) + "px"; t.style.top = Math.max(sy - 20, 70) + "px";
    t.hidden = false;
    clearTimeout(showTip.t); showTip.t = setTimeout(hideTip, 5000);
  }
  function hideTip() { $("#tip").hidden = true; }
  function toast(title, text, kind) {
    const box = $("#toasts"), el = document.createElement("div");
    el.className = "w-toast " + (kind || "");
    el.innerHTML = `<b></b><span></span>`;
    el.querySelector("b").textContent = title; el.querySelector("span").textContent = text || "";
    box.prepend(el);
    while (box.children.length > 4) box.lastChild.remove();
    setTimeout(() => el.classList.add("out"), 5200);
    setTimeout(() => el.remove(), 5800);
  }

  // ---------------------------------------------------------------- TV mode: no controls, the camera tours by itself
  const tv = { on: false, i: 0, t: 0, hold: 0 };
  function tvShot() {
    const shots = [() => storeView(), () => overview(), () => storeView(), () => {
      const busy = [...people.values()].filter((p) => !p.leaving && p.state !== "inactive");
      if (!busy.length) return storeView();
      const p = busy[Math.floor(Math.random() * busy.length)];
      const s = Math.min(2.2, storeView().s * 2.4);
      return { s, x: app.screen.width / 2 - p.c.x * s, y: app.screen.height / 2 - (p.c.y - 20) * s };
    }];
    const holds = [14, 9, 10, 7];
    const k = tv.i % shots.length;
    tv.i++; tv.hold = holds[k];
    flyTo(shots[k](), 2400);
  }
  function setTV(on) {
    tv.on = on;
    document.body.classList.toggle("tv", on);
    $("#setPop").hidden = true; hideTip();
    if (on) {
      tv.i = 0; tvShot();
      if (document.documentElement.requestFullscreen && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
      $("#tvHint").classList.add("show"); setTimeout(() => $("#tvHint").classList.remove("show"), 3500);
    } else flyTo(storeView(), 800);
    window.PDUrl && PDUrl.set({ tv: on ? 1 : "" });
  }
  $("#tvBtn").addEventListener("click", () => setTV(true));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && tv.on) setTV(false); });
  document.addEventListener("dblclick", () => { if (tv.on) setTV(false); });
  $("#fsBtn").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen && document.documentElement.requestFullscreen().catch(() => {});
  });
  $("#focusBtn").addEventListener("click", () => flyTo(storeView()));
  $("#allBtn").addEventListener("click", () => flyTo(overview()));

  // ---------------------------------------------------------------- frame loop
  app.ticker.add((tk) => {
    const dt = Math.min(0.1, tk.deltaMS / 1000);
    if (flight) {
      flight.t += tk.deltaMS;
      const k = Math.min(1, flight.t / flight.ms), e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      cam.s = flight.from.s + (flight.to.s - flight.from.s) * e;
      cam.x = flight.from.x + (flight.to.x - flight.from.x) * e;
      cam.y = flight.from.y + (flight.to.y - flight.from.y) * e;
      applyCam();
      if (k >= 1) flight = null;
    }
    if (tv.on && !flight) { tv.hold -= dt; if (tv.hold <= 0) tvShot(); }
    step(dt);
    for (let i = effects.length - 1; i >= 0; i--) if (!effects[i].step(dt)) { effects[i].destroy({ children: true }); effects.splice(i, 1); }
  });
  let lastW = window.innerWidth;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - lastW) > 60) {        // after Pixi has resized its canvas
      lastW = window.innerWidth;
      setTimeout(() => { if (!tv.on) { flight = null; Object.assign(cam, storeView()); applyCam(); } }, 120);
    }
  });

  Object.assign(cam, overview()); applyCam();
  setTimeout(() => flyTo(storeView(), 1600), 400);
  setMode(mode);
  if (q.get("tv") === "1") setTV(true);
  window.__world = { people, cam, get snap() { return snap; }, get mode() { return mode; } };   // for checking only
})();
