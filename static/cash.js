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
  lastLoad = Date.now();
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
  renderAdBills(s.ads);
}

function kindsNote(byKind) {
  const parts = Object.entries(byKind).filter(([, v]) => Math.abs(v) >= 0.5)
    .slice(0, 3).map(([k, v]) => `${esc(k)} ${money(v)}`);
  return parts.join(" · ");
}

function renderCards(s) {
  const position = `<div class="card position" id="positionCard" role="button" tabindex="0"
      title="Open the statement">
      <div class="card-label"><span class="dot" style="background:var(--sales)"></span>Available + receivable</div>
      <div class="card-value">${money(s.position)}${s.ads_payable !== undefined && Math.abs(s.ads_payable) >= 0.5
        ? `<span class="ads-owed" title="Ad spend Meta and Google haven't charged yet">−${money(s.ads_payable)} ads payable</span>` : ""}</div>
      ${s.after_ads !== undefined ? `<div class="after-ads">${money(s.after_ads)} <span>after ad bills</span></div>` : ""}
      <div class="card-note">${money(s.available)} available now + ${money(s.receivable)} receivable
        (sales settling, reserves and PayPal holds still to be released)${s.ads
          ? ` · ad bills: Meta ${money(s.ads.meta)}, Google ${money(s.ads.google)}` : ""}</div>
      <div class="card-link">View statement →</div>
    </div>`;
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
  $("cards").innerHTML = position + now + horizons;
  $("positionCard").onclick = openStatement;
  $("positionCard").onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") openStatement(); };
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
    const kinds = Object.entries(d.kinds).filter(([, v]) => Math.abs(v) >= 0.5)
      .map(([k, v]) => `<span class="kind">${esc(k)} ${money(v)}</span>`).join("");
    const hide = i >= FIRST_DAYS && !showAllDays ? " hidden" : "";
    return `<tr${hide}><td class="name">${niceDate(d.date)}<span class="sub">${kinds}</span></td>
      <td>${d.total >= 0 ? "+" : ""}${money(d.total)}</td><td>${money(running)}</td></tr>`;
  }).join("");
  $("schedule").innerHTML = `<thead><tr><th>Day</th><th>Arriving</th><th>Balance after</th></tr></thead>
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

function renderAdBills(ads) {
  $("adsPanel").hidden = !ads;
  if (!ads) return;
  $("adsNote").textContent = `${money(ads.total)} owed, not yet charged`;
  const when = (iso) => new Date(iso).toLocaleString(undefined,
    { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  const rows = ads.accounts.map((a) => {
    const id = a.platform === "Google"
      ? String(a.account_id).replace(/(\d{3})(\d{3})(\d{4})/, "$1-$2-$3") : a.account_id;
    const last = a.last_charge
      ? `Last charged ${when(a.last_charge.time)} · ${a.last_charge.currency || ""} ${Number(a.last_charge.amount).toLocaleString(undefined, { maximumFractionDigits: 2 })} · card ${esc(a.last_charge.card)}` +
        (a.last_charge.monthly ? " · monthly bill, so counting from the 1st" : "") +
        (a.tax_pct ? ` · includes ${a.tax_pct}% tax` : "")
      : (a.platform === "Meta" ? "Amount owed, from Meta" : "");
    const fails = a.failed.map((f) => `<span class="ads-fail">Declined ${when(f.time)}: ${money(f.amount_display)} on ${esc(f.card)}${f.reason ? ` (${esc(f.reason)})` : ""}</span>`).join("");
    const note = a.note ? `<span class="sub">${esc(a.note)}</span>` : "";
    return `<tr><td class="name">${esc(a.store)} · ${esc(a.platform)}
        <span class="sub">${esc(a.account)} · ${esc(id)}</span>
        <span class="sub">${last}</span>${note}${fails}</td>
      <td class="ads-amt">${a.owed_display === null ? "—" : money(a.owed_display)}</td></tr>`;
  }).join("");
  $("adsTable").innerHTML = `<thead><tr><th>Ad account</th><th>Owed</th></tr></thead><tbody>${rows}</tbody>
    <tfoot><tr><td class="name">Total ads payable${ads.unknown ? `<span class="sub">${ads.unknown} account${ads.unknown === 1 ? "" : "s"} unknown, not included</span>` : ""}</td>
      <td class="ads-amt">${money(ads.total)}</td></tr></tfoot>`;
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

/* ------------------------------------------------ statement */

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function stmtRange() {
  const today = new Date((data?.today || iso(new Date())) + "T12:00:00");
  const back = (n) => { const d = new Date(today); d.setDate(d.getDate() - n); return iso(d); };
  const v = $("stmtPreset").value;
  if (v === "today") return [iso(today), iso(today)];
  if (v === "yesterday") return [back(1), back(1)];
  if (v === "7") return [back(6), iso(today)];
  if (v === "30") return [back(29), iso(today)];
  if (v === "month") return [iso(new Date(today.getFullYear(), today.getMonth(), 1)), iso(today)];
  return [$("stmtFrom").value || back(1), $("stmtTo").value || $("stmtFrom").value || back(1)];
}

function openStatement() {
  const dlg = $("stmt");
  if (!dlg.open) dlg.showModal();
  loadStatement();
}

let stmtSeq = 0;
async function loadStatement() {
  const [from, to] = stmtRange();
  const custom = $("stmtPreset").value === "custom";
  PDUrl.set({ statement: $("stmtPreset").value, from: custom ? from : null, to: custom ? to : null });
  $("stmtDates").hidden = $("stmtPreset").value !== "custom";
  if ($("stmtPreset").value !== "custom") { $("stmtFrom").value = from; $("stmtTo").value = to; }
  $("stmtSub").textContent = from === to ? niceDate(from) : `${niceDate(from)} – ${niceDate(to)}`;
  $("stmtBody").innerHTML = `<p class="hint stmt-wait">Reading every movement from Airwallex and PayPal…</p>`;
  const seq = ++stmtSeq;
  let st;
  try {
    st = await api(`/api/cash/statement?start=${from}&end=${to}&currency=` +
                   encodeURIComponent($("currencySelect").value));
  } catch (e) {
    if (seq === stmtSeq) $("stmtBody").innerHTML = `<div class="warn">${esc(e.message)}</div>`;
    return;
  }
  if (seq !== stmtSeq) return;            // a newer range was picked meanwhile
  renderStatement(st);
}

function renderStatement(st) {
  const cur = st.currency;
  const m2 = (v) => {
    const opts = { style: "currency", currency: cur, minimumFractionDigits: 2, maximumFractionDigits: 2 };
    try { return new Intl.NumberFormat(undefined, opts).format(v); } catch { return v.toFixed(2); }
  };
  const sgn = (v) => (Math.abs(v) < 0.005 ? m2(0) : (v > 0 ? "+" : "−") + m2(Math.abs(v)));
  const sec = st.sections.map((s) => {
    const moved = s.name === "Moved between your accounts";
    const rows = s.lines.map((l) => {
      const n = l.count > 1 && !moved ? ` <span class="stmt-n">×${l.count}</span>` : "";
      const via = l.via ? `<span class="stmt-via">${esc(l.via)}</span>` : "";
      const details = (l.details || []).filter((d) => Math.abs(d.amount) >= 0.5)
        .map((d) => `<div class="stmt-detail"><span>${esc(d.name)}</span><span>${sgn(d.amount)}</span></div>`).join("");
      // Moves inside your own money show their size in grey; only money still
      // travelling (or arriving from an earlier day) changes the total.
      const amount = moved && Math.abs(l.amount) < 0.005
        ? `<span class="stmt-info">${m2(Math.abs(l.info))}</span>`
        : sgn(l.amount);
      return `<div class="stmt-row${moved ? " moved" : ""}">
          <div class="stmt-what">${esc(l.line)}${n}${via}</div>
          <div class="stmt-amt ${!moved && l.amount < 0 ? "out" : ""}">${amount}</div>
        </div>${details}`;
    }).join("");
    return `<section class="stmt-sec">
        <div class="stmt-sec-head"><span>${esc(s.name)}</span>
          ${moved && Math.abs(s.total) < 0.005 ? `<span class="hint">no change to your total</span>` : `<span>${sgn(s.total)}</span>`}</div>
        ${rows}
      </section>`;
  }).join("");

  const c = st.checks;
  const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
  const checks = [];
  if (c.shopify_sales !== null && c.shopify_sales !== undefined) {
    const gw = (c.by_gateway || []).map((g) => {
      const gap = g.received - g.shopify;
      const label = g.via === "Other" ? "Other payment methods (not connected here)" : `Paid via ${g.via}`;
      return `<div class="stmt-row"><div class="stmt-what">${esc(label)}<span class="stmt-via">Shopify ${m2(g.shopify)} (${g.orders} orders) · received ${m2(g.received)} (${g.payments})</span></div>
        <div class="stmt-amt">${Math.abs(gap) < 0.5 ? "✓ matches" : sgn(gap)}</div></div>`;
    }).join("");
    checks.push(`<div class="stmt-row"><div class="stmt-what">Shopify sales (all stores)<span class="stmt-via">after refunds, on Hong Kong time like this statement</span></div><div class="stmt-amt">${m2(c.shopify_sales)}</div></div>
      <div class="stmt-row"><div class="stmt-what">Sales received in Airwallex + PayPal<span class="stmt-via">${c.payments} payments</span></div><div class="stmt-amt">${m2(c.received)}</div></div>
      <div class="stmt-row strong"><div class="stmt-what">Matched</div><div class="stmt-amt">${pct(c.matched)}</div></div>${gw}`);
  }
  if (c.ad_spend !== null && c.ad_spend !== undefined) {
    checks.push(`<div class="stmt-row"><div class="stmt-what">Ads paid from these accounts<span class="stmt-via">Meta + Google charges</span></div><div class="stmt-amt">${m2(c.ads_paid)}</div></div>
      <div class="stmt-row"><div class="stmt-what">Ad spend reported by Meta + Google</div><div class="stmt-amt">${m2(c.ad_spend)}</div></div>`);
  }
  if (c.fee_rate !== null) {
    checks.push(`<div class="stmt-row"><div class="stmt-what">Fees and costs as % of sales received</div><div class="stmt-amt">${pct(c.fee_rate)}</div></div>`);
  }

  $("stmtBody").innerHTML = `
    ${(st.problems || []).map((p) => `<div class="warn">${esc(p)}</div>`).join("")}
    <div class="stmt-row stmt-total"><div class="stmt-what">Opening balance<span class="stmt-via">start of ${niceDate(st.start.slice(0, 10))}</span></div>
      <div class="stmt-amt">${m2(st.opening)}</div></div>
    ${sec || `<p class="hint stmt-wait">No movements in this period.</p>`}
    <div class="stmt-row stmt-total"><div class="stmt-what">Closing balance<span class="stmt-via">${
      st.to_now ? "now" : "end of " + niceDate(st.last_day)}</span></div>
      <div class="stmt-amt">${m2(st.closing)}</div></div>
    <div class="stmt-row stmt-change"><div class="stmt-what">Change over the period</div>
      <div class="stmt-amt ${st.change < 0 ? "out" : "in"}">${sgn(st.change)}</div></div>
    ${checks.length ? `<section class="stmt-sec"><div class="stmt-sec-head"><span>Checks</span></div>${checks.join("")}
      <p class="hint stmt-note">A gap usually means a refund (Shopify takes it off the order's day, the money leaves on the day it's refunded), an order paid some other way, or a payment that hasn't shown up yet. Ads are paid when Meta and Google bill your card, which can be a day or two after the spend.</p></section>` : ""}
    <p class="hint stmt-note">Each currency is converted at today's daily rate, so this ties exactly to the live card. Because of that, "Currency conversions" also includes how rates have moved since the day you converted. PayPal's activity can run a few hours behind.</p>`;
}

$("stmtClose").onclick = () => $("stmt").close();
$("stmt").addEventListener("close", () => PDUrl.set({ statement: null, from: null, to: null }));
$("stmt").addEventListener("click", (e) => { if (e.target === $("stmt")) $("stmt").close(); });
$("stmtPreset").onchange = () => {
  if ($("stmtPreset").value === "custom") { $("stmtDates").hidden = false; return; }
  loadStatement();
};
$("stmtFrom").onchange = $("stmtTo").onchange = () => {
  if ($("stmtPreset").value === "custom" && $("stmtFrom").value) loadStatement();
};

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
// Keep figures current while the page is open: every 5 minutes, and on coming back to it.
setInterval(() => { if (!document.hidden) load(); }, 5 * 60 * 1000);
let lastLoad = Date.now();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Date.now() - lastLoad > 60 * 1000) load();
});
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
  await load();
  const sp = PDUrl.get("statement");      // the statement was open before a refresh
  if (sp && [...$("stmtPreset").options].some((o) => o.value === sp)) {
    $("stmtPreset").value = sp;
    if (sp === "custom") { $("stmtFrom").value = PDUrl.get("from") || ""; $("stmtTo").value = PDUrl.get("to") || ""; }
    openStatement();
  }
})();
