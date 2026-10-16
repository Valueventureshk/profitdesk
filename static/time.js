/* Apps → Time: the team's week, one person's day (10-minute blocks with screenshots, apps & sites), downloads, computers.
   Every figure comes from the server; the page only formats them. */

const $ = (id) => document.getElementById(id);
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
let toastTimer;
function toast(msg, bad = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 7000 : 3000);
}
async function api(url, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(url, init);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(body.detail || body.error || `Something went wrong (${r.status}).`);
  return body;
}
const hm = (mins) => (mins ? `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m` : "–");
const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = new Date(s + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const actClass = (a) => (a === null || a === undefined ? "none" : a >= 0.6 ? "hi" : a >= 0.3 ? "mid" : "lo");

const state = { page: PDUrl.get("tab") || "week", info: null, weekStart: null, light: null };

async function loadInfo() {
  state.info = await api("/api/time/people");
  const i = state.info;
  $("person").innerHTML = i.people.map((p) => `<option value="${p.id}">${esc(p.name)}${p.has_app ? "" : " (no app yet)"}</option>`).join("");
  $("person").value = PDUrl.get("user") || i.me;
  $("day").value = PDUrl.get("day") || i.today;
  state.weekStart = PDUrl.get("week") || addDays(i.today, -6);
  $("keep").textContent = i.settings.keep_days;
  $("sKeep").value = i.settings.keep_days;
  $("sIdle").value = i.settings.idle_minutes;
  $("sDel").value = i.settings.staff_delete_minutes;
}

/* ------------------------------------------------ week */

async function loadWeek() {
  PDUrl.set({ week: state.weekStart });
  let r;
  try { r = await api(`/api/time/week?start=${state.weekStart}`); } catch (e) { toast(e.message, true); return; }
  const fmt = (d) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  $("wLabel").textContent = `${fmt(r.days[0])} – ${fmt(r.days[6])}`;
  $("week").innerHTML = `<thead><tr><th>Person</th>${r.days.map((d) => `<th>${fmt(d)}</th>`).join("")}<th>Total</th><th>Activity</th></tr></thead>
    <tbody>${r.people.map((p) => `<tr><td class="name">${esc(p.name)}</td>${p.days.map((d, i) => `<td>
      ${d.minutes ? `<a href="#" data-open="${p.id}" data-d="${r.days[i]}">${hm(d.minutes)}</a><span class="tm-act ${actClass(d.activity)}">${pct(d.activity)}</span>` : "–"}</td>`).join("")}
      <td><b>${hm(p.minutes)}</b></td><td><span class="tm-act ${actClass(p.activity)}">${pct(p.activity)}</span></td></tr>`).join("")
      || `<tr><td class="empty" colspan="10">No time tracked this week yet. Staff install the app from “Get the app”.</td></tr>`}</tbody>`;
}

/* ------------------------------------------------ day */

async function loadDay() {
  const user = $("person").value, day = $("day").value;
  PDUrl.set({ user, day });
  $("timeline").innerHTML = '<p class="empty">Loading…</p>';
  let r;
  try { r = await api(`/api/time/day?user=${user}&day=${day}`); } catch (e) { $("timeline").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  state.dayData = r;
  const t = r.totals;
  $("tiles").innerHTML = [["Worked", hm(t.minutes)], ["Active", hm(t.active)], ["Activity", pct(t.activity)],
    ["Started", r.first || "–"], ["Last seen", r.last || "–"]].map(([k, v]) => `<div class="tm-tile"><span>${k}</span><b>${v}</b></div>`).join("");
  // group the blocks by hour, Hubstaff-style rows
  const hours = {};
  for (const b of r.blocks) (hours[b.start.slice(0, 2)] = hours[b.start.slice(0, 2)] || []).push(b);
  $("timeline").innerHTML = Object.keys(hours).sort().map((h) => `<div class="tm-hour"><div class="tm-hlabel">${h}:00</div><div class="tm-blocks">
      ${hours[h].map((b) => `<div class="tm-block ${actClass(b.activity)}">
        ${b.shots.length ? `<button class="tm-shot" data-shot="${b.shots[0].id}"><img src="/api/time/shot/${b.shots[0].id}?thumb=1" alt="" loading="lazy">
          ${b.shots.length > 1 ? `<span class="tm-more">+${b.shots.length - 1}</span>` : ""}</button>` : '<div class="tm-noshot">no screenshot</div>'}
        <div class="tm-binfo"><b>${esc(b.start)}</b> · ${b.minutes} min · <span class="tm-act ${actClass(b.activity)}">${pct(b.activity)}</span></div>
        <div class="tm-bapp" title="${esc(b.app)}">${esc(b.app)}</div>
        ${b.tasks.length ? `<div class="tm-btask">${b.tasks.map(esc).join(", ")}</div>` : ""}</div>`).join("")}</div></div>`).join("")
    || '<p class="empty">Nothing tracked this day.</p>';
  const total = r.apps.reduce((s, a) => s + a.minutes, 0) || 1;
  $("apps").innerHTML = r.apps.length ? `<thead><tr><th>App / site</th><th>Time</th><th>Share</th><th>Activity</th></tr></thead><tbody>${r.apps.map((a) => `<tr>
      <td class="name">${esc(a.app)}</td><td>${hm(a.minutes)}</td><td><span class="tm-share"><i style="width:${Math.round(a.minutes / total * 100)}%"></i></span></td>
      <td>${pct(a.minutes ? a.active / a.minutes : null)}</td></tr>`).join("")}</tbody>` : '<tbody><tr><td class="empty">No apps recorded.</td></tr></tbody>';
}

function openShot(id) {
  const r = state.dayData;
  let shot = null;
  for (const b of r.blocks) for (const s of b.shots) if (s.id === id) shot = s;
  if (!shot) return;
  state.light = shot;
  $("lImg").src = `/api/time/shot/${id}`;
  $("lInfo").textContent = `${shot.at} · ${shot.screens || 1} screen(s) · ${shot.app || ""}${shot.title ? " · " + shot.title : ""}`;
  const ageMin = (Date.now() - new Date(shot.taken_at).getTime()) / 60000;
  $("lDel").hidden = !(r.manage || (r.mine && ageMin <= r.can_delete_until));
  $("light").hidden = false;
}

/* ------------------------------------------------ app downloads + computers */

function renderDownloads() {
  const files = state.info.downloads;
  const pick = (re) => files.find((f) => re.test(f));
  const rows = [["Windows", pick(/windows/i), "Windows 10 or 11"], ["Mac (Apple chip: M1–M4)", pick(/mac.*(arm|apple)/i), "Most Macs from 2021 on"],
    ["Mac (Intel)", pick(/mac.*(intel|x86|x64)/i), "Older Macs"]];
  $("downloads").innerHTML = rows.map(([name, f, note]) => `<div class="tm-dlrow"><div><b>${name}</b><span class="hint">${note}</span></div>
    ${f ? `<a class="btn btn-primary" href="/static/downloads/${encodeURIComponent(f)}" download>Download</a>` : '<span class="hint">Being built: check back soon</span>'}</div>`).join("");
}

async function loadDevices() {
  let r;
  try { r = await api("/api/time/devices"); } catch (e) { toast(e.message, true); return; }
  const when = (s) => (s ? new Date(s).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
  $("devices").innerHTML = r.devices.length ? `<thead><tr><th>Person</th><th>Computer</th><th>App</th><th>Signed in</th><th>Last seen</th><th></th></tr></thead><tbody>${r.devices.map((d) => `<tr>
      <td class="name">${esc(d.person)}</td><td>${esc(d.name)} <span class="sub">${esc(d.os)}</span></td><td>${esc(d.version || "")}</td><td>${when(d.created_at)}</td><td>${when(d.last_seen)}</td>
      <td>${d.revoked_at ? "signed out" : `<button class="btn btn-sm" data-revoke="${d.id}">Sign out</button>`}</td></tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="empty">No computers yet.</td></tr></tbody>';
}

/* ------------------------------------------------ page */

function showPage(p) {
  state.page = p;
  PDUrl.set({ tab: p === "week" ? null : p });
  for (const b of $("pages").querySelectorAll("button")) b.classList.toggle("on", b.dataset.page === p);
  for (const k of ["week", "day", "app", "devices"]) $(k + "Page").hidden = k !== p;
  if (p === "week") loadWeek();
  if (p === "day") loadDay();
  if (p === "app") renderDownloads();
  if (p === "devices") loadDevices();
}

$("pages").onclick = (e) => { const b = e.target.closest("button[data-page]"); if (b) showPage(b.dataset.page); };
$("wPrev").onclick = () => { state.weekStart = addDays(state.weekStart, -7); loadWeek(); };
$("wNext").onclick = () => { state.weekStart = addDays(state.weekStart, 7); loadWeek(); };
$("person").onchange = $("day").onchange = loadDay;
$("dPrev").onclick = () => { $("day").value = addDays($("day").value, -1); loadDay(); };
$("dNext").onclick = () => { $("day").value = addDays($("day").value, 1); loadDay(); };
$("lClose").onclick = () => { $("light").hidden = true; };
$("light").onclick = (e) => { if (e.target === $("light")) $("light").hidden = true; };
$("lDel").onclick = async () => {
  if (!confirm("Remove this screenshot for good? (It's noted that it was removed.)")) return;
  try { await api(`/api/time/shot/${state.light.id}`, { method: "DELETE" }); toast("Removed"); $("light").hidden = true; loadDay(); }
  catch (e) { toast(e.message, true); }
};
$("sSave").onclick = async () => {
  try {
    await api("/api/time/settings", { method: "PUT", json: { keep_days: $("sKeep").value, idle_minutes: $("sIdle").value, staff_delete_minutes: $("sDel").value } });
    toast("Saved");
  } catch (e) { toast(e.message, true); }
};
document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-open],[data-shot],[data-revoke]");
  if (!el) return;
  if (el.dataset.open) { e.preventDefault(); $("person").value = el.dataset.open; $("day").value = el.dataset.d; showPage("day"); }
  else if (el.dataset.shot) openShot(+el.dataset.shot);
  else if (el.dataset.revoke) {
    if (!confirm("Sign this computer out of EcomVital Time? It stops sending until someone signs in again.")) return;
    try { await api(`/api/time/devices/${el.dataset.revoke}/revoke`, { json: {} }); loadDevices(); } catch (err) { toast(err.message, true); }
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("light").hidden = true; });

(async () => {
  await (window.pdMe ? window.pdMe() : null);
  try { await loadInfo(); } catch (e) { toast(e.message, true); return; }
  showPage(state.page);
})();
