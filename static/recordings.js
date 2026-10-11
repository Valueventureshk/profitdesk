/* ProfitDesk Apps → Session Recordings: per-store switch, the list of shopper sessions, and the
   replay (rrweb-player). Every figure comes from the server (recordings.py). */

const $ = (id) => document.getElementById(id);
let data = null, playing = null, player = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}
const dur = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`);
async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) { location.href = "/login?next=/recordings"; throw new Error("Log in again."); }
  if (!r.ok) throw new Error(body.error || body.detail || `Request failed (${r.status})`);
  return body;
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

async function load() {
  const q = new URLSearchParams({ store: $("fStore").value, days: $("fDays").value, device: $("fDevice").value, seen: $("fSeen").value });
  PDUrl.set({ store: $("fStore").value === "all" ? null : $("fStore").value, days: $("fDays").value === "7" ? null : $("fDays").value,
              device: $("fDevice").value || null, seen: $("fSeen").value || null });
  try { data = await api(`/api/recordings?${q}`); } catch (e) { toast(e.message, true); return; }
  if ($("fStore").options.length === 1) {
    $("fStore").innerHTML += data.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    if (PDUrl.get("store")) $("fStore").value = PDUrl.get("store");
  }
  drawSettings();
  drawList();
  if (!data.stores.some((s) => s.cfg.on) && !data.sessions.length) $("guide").open = true;
}

function drawSettings() {
  const on = data.stores.filter((s) => s.cfg.on);
  $("settings").innerHTML = `<div class="panel-head"><h2>Recording on these stores</h2>
      <span class="hint">${on.length ? `${on.length} recording` : "Off everywhere"}</span></div>
    <details${on.length ? "" : " open"} class="rec-cfg"><summary class="hint">Stores, how many visitors to record, how long to keep</summary>
    <div class="table-wrap"><table class="table"><thead><tr><th>Store</th><th>Record</th><th>Share of visitors</th><th>Max a day</th><th>Keep</th><th>Last 24 h</th><th>Kept</th></tr></thead><tbody>
    ${data.stores.map((s) => `<tr data-store="${s.id}"><td class="name">${esc(s.name)}<span class="sub">${esc(s.domain)}</span></td>
      <td><label class="rec-switch"><input type="checkbox" data-k="on"${s.cfg.on ? " checked" : ""} data-manage-only> ${s.cfg.on ? "On" : "Off"}</label></td>
      <td><input class="control rec-num" type="number" min="1" max="100" data-k="sample" value="${s.cfg.sample}" data-manage-only> %</td>
      <td><input class="control rec-num" type="number" min="10" max="5000" data-k="per_day" value="${s.cfg.per_day}" data-manage-only></td>
      <td><input class="control rec-num" type="number" min="1" max="90" data-k="keep_days" value="${s.cfg.keep_days}" data-manage-only> days</td>
      <td>${s.today}</td><td>${s.kept} · ${s.mb} MB</td></tr>`).join("")}</tbody></table></div>
    <p class="hint">Needs the store's App proxy (the same one the tracking page uses). Every input field is blanked out,
      the account and checkout pages are never recorded, and shoppers who said no to analytics in your cookie banner
      aren't recorded. Mention session recording in each store's privacy policy (EU / UK stores especially).</p></details>`;
  for (const el of $("settings").querySelectorAll("[data-k]")) el.onchange = async () => {
    const tr = el.closest("tr");
    const val = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.k === "on" && val && !confirm("Start recording shoppers on this store? A small script is added to its pages.")) { el.checked = false; return; }
    try {
      await api(`/api/recordings/settings/${tr.dataset.store}`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [el.dataset.k]: val }) });
      toast(el.dataset.k === "on" ? (val ? "Recording starts with the next visitors." : "Recording stopped; the script was removed.") : "Saved.");
      load();
    } catch (e) { toast(e.message, true); if (el.type === "checkbox") el.checked = !val; }
  };
}

function drawList() {
  const rows = data.sessions;
  $("list").innerHTML = rows.length ? `<thead><tr><th>When</th><th>Store</th><th>Device</th><th>Length</th><th>Journey</th><th>Did</th><th></th></tr></thead><tbody>
    ${rows.map((r, i) => `<tr class="clickable${r.watched ? " rec-seen" : ""}" data-i="${i}">
      <td>${esc(ago(r.started))}<span class="sub">${esc((r.tz || "").replace(/_/g, " "))}</span></td>
      <td>${esc(r.store)}</td>
      <td>${r.mobile ? "📱 Phone" : "💻 Computer"}<span class="sub">${r.width ? r.width + " px" : ""}</span></td>
      <td>${dur(r.seconds)}</td>
      <td class="rec-path"><b>${esc(r.landing)}</b>${r.pages.length > 1 ? ` → ${r.pages.length - 1} more page${r.pages.length > 2 ? "s" : ""}` : ""}
        ${r.referrer ? `<span class="sub">from ${esc(r.referrer.replace(/^https?:\/\//, "").slice(0, 60))}</span>` : ""}</td>
      <td>${[r.product ? `<span class="src catalog">Product</span>` : "", r.cart ? `<span class="src invoice">Cart</span>` : "",
             `<span class="hint">${r.clicks} clicks</span>`].join(" ")}</td>
      <td><button class="btn btn-sm btn-primary">▶ Watch</button></td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">${data.stores.some((s) => s.cfg.on) ? "No sessions recorded yet for these filters. They appear a few seconds after a shopper starts browsing."
        : "Turn recording on for a store above to start."}</td></tr></tbody>`;
  for (const tr of $("list").querySelectorAll("[data-i]")) tr.onclick = () => play(rows[+tr.dataset.i]);
}

async function play(r) {
  playing = r;
  $("player").hidden = false;
  $("pTitle").textContent = `${r.store} · ${r.mobile ? "Phone" : "Computer"} · ${dur(r.seconds)}`;
  $("pMeta").innerHTML = `${esc(new Date(r.started).toLocaleString())} · ${esc(r.pages.join(" → "))}`;
  $("pStage").innerHTML = `<p class="hint">Loading the recording…</p>`;
  let events;
  try { events = await api(`/api/recordings/events?sid=${encodeURIComponent(r.sid)}`); }
  catch (e) { $("pStage").innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
  if (events.length < 2) { $("pStage").innerHTML = `<div class="warn">This session is too short to replay.</div>`; return; }
  $("pStage").innerHTML = "";
  const Player = window.rrwebPlayer && (window.rrwebPlayer.default || window.rrwebPlayer.Player || window.rrwebPlayer);
  const box = $("pStage").getBoundingClientRect();
  try {
    player = new Player({ target: $("pStage"), props: { events, width: Math.max(320, box.width - 4),
      height: Math.min(window.innerHeight * 0.62, (box.width - 4) * (r.mobile ? 1.6 : 0.56)), autoPlay: true,
      showWarning: false, skipInactive: true, speedOption: [1, 2, 4, 8], mouseTail: { strokeStyle: "#E0392F" } } });
  } catch (e) { $("pStage").innerHTML = `<div class="warn">The player couldn't open this session (${esc(e.message)}).</div>`; }
  r.watched = 1;
}

function closePlayer() {
  try { player?.$destroy?.(); } catch { /* gone */ }
  player = null;
  $("pStage").innerHTML = "";
  $("player").hidden = true;
  drawList();
}
$("pClose").onclick = closePlayer;
$("player").onclick = (e) => { if (e.target === $("player")) closePlayer(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("player").hidden) closePlayer(); });
$("pDelete").onclick = async () => {
  if (!playing || !confirm("Delete this recording?")) return;
  try { await api(`/api/recordings?sid=${encodeURIComponent(playing.sid)}`, { method: "DELETE" }); closePlayer(); load(); toast("Deleted."); }
  catch (e) { toast(e.message, true); }
};

for (const id of ["fStore", "fDays", "fDevice", "fSeen"]) $(id).onchange = load;
$("refresh").onclick = load;
for (const [id, k] of [["fDays", "days"], ["fDevice", "device"], ["fSeen", "seen"]]) if (PDUrl.get(k)) $(id).value = PDUrl.get(k);
load();
