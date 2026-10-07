/* ProfitDesk cash flow page.

   This file draws things. Every figure comes from the server (cashflow.py),
   already converted into the chosen currency. */

const $ = (id) => document.getElementById(id);
let data = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function money(v, cur = data?.currency) {
  if (v === null || v === undefined) return "—";
  const opts = { maximumFractionDigits: 0 };
  if (cur) { opts.style = "currency"; opts.currency = cur; }
  try { return new Intl.NumberFormat(undefined, opts).format(v); }
  catch { return Math.round(v).toLocaleString(); }
}

function niceDate(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined,
    { weekday: "short", day: "numeric", month: "short" });
}

let toastTimer;
function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3000);
}

async function api(path, options) {
  const r = await fetch(path, options);
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && body.login) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname);
    throw new Error("Please log in again.");
  }
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

const post = (path, body, method = "POST") => api(path, {
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

/* ------------------------------------------------ load + draw */

async function load() {
  $("sub").textContent = "Asking your accounts…";
  try {
    data = await api("/api/cash?currency=" + encodeURIComponent($("currencySelect").value));
  } catch (e) {
    $("sub").textContent = "Couldn't load";
    toast(e.message, true);
    return;
  }
  $("problems").innerHTML = data.problems.map((p) => `<div class="warn">${esc(p)}</div>`).join("");
  renderConnections();

  const s = data.summary;
  $("empty").hidden = !!s;
  $("schedulePanel").hidden = $("accountsPanel").hidden = !s;
  if (!s) { $("cards").innerHTML = ""; $("sub").textContent = "No accounts connected yet"; return; }

  $("sub").textContent = `As of ${niceDate(data.today)} · all figures in ${data.currency}` +
    (data.fx ? ` · daily rate ${data.fx.date || ""}` : "");
  renderCards(s);
  renderSchedule(s);
  renderAccounts(s);
}

function kindsNote(byKind) {
  const parts = Object.entries(byKind).filter(([, v]) => Math.abs(v) >= 0.5)
    .slice(0, 3).map(([k, v]) => `${esc(k)} ${money(v)}`);
  return parts.join(" · ");
}

function renderCards(s) {
  const now = `<div class="card feature">
      <div class="card-label"><span class="dot" style="background:var(--up)"></span>Available now</div>
      <div class="card-value">${money(s.available)}</div>
      <div class="card-note">${s.reserved ? `Plus ${money(s.reserved)} held (payment reserves and card holds)` : "&nbsp;"}</div>
    </div>`;
  const horizons = s.horizons.map((h) => `<div class="card">
      <div class="card-label"><span class="dot" style="background:var(--sales)"></span>${esc(h.label)}</div>
      <div class="card-value">${money(h.available_by)}</div>
      <div class="card-note">${h.incoming >= 0 ? "+" : ""}${money(h.incoming)} arriving by ${niceDate(h.until)}</div>
      <div class="card-note">${kindsNote(h.by_kind)}</div>
    </div>`).join("");
  $("cards").innerHTML = now + horizons;
}

const FIRST_DAYS = 7;
let showAllDays = false;

function renderSchedule(s) {
  $("laterNote").textContent = Math.abs(s.later) >= 0.5 ? `${money(s.later)} more after 90 days` : "";
  $("moreDays").hidden = true;
  if (!s.schedule.length) {
    $("schedule").innerHTML = `<tbody><tr><td class="hint">Nothing pending in the next 90 days.</td></tr></tbody>`;
    return;
  }
  let running = s.available;
  const rows = s.schedule.map((d, i) => {
    running += d.total;
    const kinds = Object.entries(d.kinds).map(([k, v]) => `${esc(k)} ${money(v)}`).join(" · ");
    const hide = i >= FIRST_DAYS && !showAllDays ? " hidden" : "";
    return `<tr${hide}><td class="name">${niceDate(d.date)}<span class="sub">${kinds}</span></td>
      <td>${d.total >= 0 ? "+" : ""}${money(d.total)}</td><td>${money(running)}</td></tr>`;
  }).join("");
  $("schedule").innerHTML = `<thead><tr><th>Day</th><th>Arriving</th><th>Available after</th></tr></thead>
    <tbody>${rows}</tbody>`;
  const extra = s.schedule.length - FIRST_DAYS;
  if (extra > 0) {
    $("moreDays").hidden = false;
    $("moreDays").textContent = showAllDays ? "Show first 7 days only"
      : `Show ${extra} more day${extra === 1 ? "" : "s"} (to ${niceDate(s.schedule[s.schedule.length - 1].date)})`;
  }
}

function renderAccounts(s) {
  const rows = s.accounts.map((a) => {
    const bal = a.balances.filter((b) => b.available || b.reserved || b.pending)
      .map((b) => `${esc(b.currency)} ${b.available.toLocaleString(undefined, { maximumFractionDigits: 2 })}`)
      .join(" · ");
    const d30 = a.incoming.d30 ?? 0;
    return `<tr><td class="name">${esc(a.label)}<span class="sub">${bal || "No balances"}</span></td>
      <td>${money(a.available)}</td><td>${money(a.reserved)}</td><td>${money(d30)}</td></tr>`;
  }).join("");
  $("accounts").innerHTML = `<thead><tr><th>Account</th><th>Available</th><th>Held</th>
    <th>Arriving 30 days</th></tr></thead><tbody>${rows}</tbody>`;
}

function renderConnections() {
  const list = data.connected || [];
  $("connList").innerHTML = list.length ? list.map((c) => `
    <div class="group-row">
      <div class="group-name"><strong>${esc(c.label)}</strong><span>${esc(c.provider === "paypal" ? "PayPal" : c.provider === "airwallex" ? "Airwallex" : c.provider)}</span></div>
      <button class="btn btn-sm btn-ghost" data-rename="${c.id}">Rename</button>
      <button class="btn btn-sm btn-danger" data-remove="${c.id}">Remove</button>
    </div>`).join("") : `<p class="hint">No accounts connected yet.</p>`;
  $("addAwx").open = !list.length;
  for (const b of $("connList").querySelectorAll("[data-rename]")) {
    b.onclick = async () => {
      const c = list.find((x) => String(x.id) === b.dataset.rename);
      const name = prompt("Name for this account", c.label);
      if (!name || name === c.label) return;
      try { await post(`/api/cash/${c.id}`, { label: name }, "PUT"); load(); }
      catch (e) { toast(e.message, true); }
    };
  }
  for (const b of $("connList").querySelectorAll("[data-remove]")) {
    b.onclick = async () => {
      const c = list.find((x) => String(x.id) === b.dataset.remove);
      if (!confirm(`Remove ${c.label}? Its key is deleted from ProfitDesk.`)) return;
      await api(`/api/cash/${c.id}`, { method: "DELETE" });
      toast(`${c.label} removed.`);
      load();
    };
  }
}

/* ------------------------------------------------ wiring */

$("awxAdd").onclick = async () => {
  const btn = $("awxAdd");
  btn.disabled = true;
  btn.textContent = "Checking with Airwallex…";
  try {
    const r = await post("/api/cash/airwallex", {
      label: $("awxLabel").value, client_id: $("awxClient").value, api_key: $("awxKey").value,
      account_ids: $("awxAccounts").value,
    });
    for (const id of ["awxLabel", "awxClient", "awxKey", "awxAccounts"]) $(id).value = "";
    toast(`Airwallex connected · ${r.accounts} account${r.accounts === 1 ? "" : "s"}, ` +
          `${r.currencies.length} currencies.`);
    load();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect Airwallex";
  }
};

$("ppAdd").onclick = async () => {
  const btn = $("ppAdd");
  btn.disabled = true;
  btn.textContent = "Checking with PayPal…";
  try {
    const r = await post("/api/cash/paypal", {
      label: $("ppLabel").value, client_id: $("ppClient").value, secret: $("ppSecret").value,
    });
    for (const id of ["ppLabel", "ppClient", "ppSecret"]) $(id).value = "";
    $("addPp").open = false;
    toast(`PayPal connected · ${r.currencies.length} currenc${r.currencies.length === 1 ? "y" : "ies"}.`);
    load();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect PayPal";
  }
};

$("moreDays").onclick = () => {
  showAllDays = !showAllDays;
  renderSchedule(data.summary);
  if (!showAllDays) $("schedulePanel").scrollIntoView({ block: "start", behavior: "smooth" });
};

$("refresh").onclick = load;
$("currencySelect").onchange = async (e) => {
  try { await post("/api/settings", { display_currency: e.target.value }, "PUT"); } catch { /* shown on load */ }
  load();
};

(async function boot() {
  try {
    const setup = await api("/api/setup");
    $("currencySelect").innerHTML = setup.currency_options
      .map((c) => `<option${c === setup.display_currency ? " selected" : ""}>${esc(c)}</option>`).join("");
  } catch (e) {
    toast(e.message, true);
  }
  load();
})();
