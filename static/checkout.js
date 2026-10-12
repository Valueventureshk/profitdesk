/* Checkout desk: offers (secret payment links), subscriptions, payments, and the Airwallex key. */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const money = (v, cur) => (v == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "USD" }).format(v));
  const when = (s) => (s ? new Date(s).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
  const day = (s) => (s ? new Date(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");
  let D = null;

  function toast(msg) {
    const t = $("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3500);
  }
  async function api(url, opts = {}) {
    const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
                                 body: opts.body ? JSON.stringify(opts.body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || j.error || `Error ${r.status}`);
    return j;
  }
  const storeName = (id) => (D.stores.find((s) => s.id === id) || {}).name || `#${id}`;

  async function load() {
    const st = $("fStore").value;
    D = await api("/api/checkout" + (st ? `?store=${st}` : ""));
    if ($("fStore").options.length === 1) {
      $("fStore").innerHTML += D.stores.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    }
    $("testbar").innerHTML = D.test ? `<div class="warn">Test mode is on: no payment above ${money(D.cap, "USD").replace("US", "")} can be taken,
      and Shopify orders are created as test orders (left out of every figure). Turn it off in Settings when you go live.</div>` : "";
    $("keyState").textContent = D.keys_set ? (D.webhook_set ? "Key and notifications set" : "Key set · notifications not set yet") : "Key not set yet";
    $("hookUrl").textContent = D.webhook_url;
    $("capV").textContent = D.cap.toFixed(2);
    $("kTest").checked = D.test;
    drawOffers(); drawSubs(); drawOrders();
  }

  // ---- offers
  function drawOffers() {
    const rows = D.offers.map((o) => `<tr>
      <td><b>${esc(o.title)}</b><div class="hint">${esc(storeName(o.store_id))} · ${esc(o.product_title || "")}</div></td>
      <td class="num">${o.sub_price != null ? money(o.sub_price, o.currency) + ` / ${o.interval_days} d` : "—"}</td>
      <td class="num">${o.one_price != null ? money(o.one_price, o.currency) : "—"}</td>
      <td class="num">${o.shipping ? money(o.shipping, o.currency) : "Free"}</td>
      <td>${esc(o.countries)}</td>
      <td>${o.active ? "On" : "Off"}</td>
      <td><button class="btn btn-sm btn-ghost" data-copy="${esc(D.base_url)}/pay/${esc(o.slug)}">Copy link</button>
        <a class="btn btn-sm btn-ghost" href="/pay/${esc(o.slug)}" target="_blank">Open</a>
        <button class="btn btn-sm btn-ghost" data-edit="${o.id}" data-manage-only>Edit</button></td></tr>`).join("");
    $("offers").innerHTML = `<thead><tr><th>Offer</th><th class="num">Subscription</th><th class="num">One-time</th><th class="num">Shipping</th>
      <th>Ships to</th><th>Live</th><th></th></tr></thead><tbody>${rows || `<tr><td colspan="7" class="hint">No offers yet. Create one with + New offer.</td></tr>`}</tbody>`;
    $("offers").querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); toast("Link copied"); } catch { prompt("Copy this link", b.dataset.copy); }
    }));
    $("offers").querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => offerForm(D.offers.find((o) => o.id === +b.dataset.edit))));
  }

  function offerForm(o) {
    const f = $("offerForm");
    const isNew = !o;
    o = o || { store_id: +$("fStore").value || (D.stores[0] || {}).id, interval_days: 30, shipping: 0, countries: "AU", active: 1 };
    f.hidden = false;
    f.innerHTML = `
      <label>Store <select id="oStore" class="control" ${isNew ? "" : "disabled"}>${D.stores.map((s) =>
        `<option value="${s.id}"${s.id === o.store_id ? " selected" : ""}>${esc(s.name)} (${s.currency})</option>`).join("")}</select></label>
      <label>Product <span class="co-row"><input id="oFind" class="control" placeholder="Search the store's products" ${isNew ? "" : "disabled"}>
        <button class="btn btn-sm btn-ghost" id="oFindGo" ${isNew ? "" : "disabled"}>Search</button></span></label>
      <div id="oFound" class="co-found"></div>
      <div class="hint" id="oPicked">${o.product_title ? "Product: <b>" + esc(o.product_title) + "</b>" : "No product picked yet"}</div>
      <label>Name on the payment page <input id="oTitle" class="control" value="${esc(o.title || "")}"></label>
      <div class="co-row">
        <label>Subscription price (each delivery) <input id="oSub" class="control" type="number" step="0.01" value="${o.sub_price ?? ""}"></label>
        <label>Every (days) <input id="oEvery" class="control" type="number" min="1" value="${o.interval_days}"></label>
        <label>One-time price <input id="oOne" class="control" type="number" step="0.01" value="${o.one_price ?? ""}"></label>
      </div>
      <div class="co-row">
        <label>Shipping <input id="oShip" class="control" type="number" step="0.01" value="${o.shipping || 0}"></label>
        <label>Ships to (country codes) <input id="oCountries" class="control" value="${esc(o.countries)}"></label>
        <label>Live <select id="oActive" class="control"><option value="1"${o.active ? " selected" : ""}>On</option><option value="0"${o.active ? "" : " selected"}>Off</option></select></label>
      </div>
      <label>Description (optional, shown under the order summary) <textarea id="oDesc" class="control" rows="3">${esc(o.description || "")}</textarea></label>
      <p class="hint">Leave a price empty to hide that option. For a test, use a price of 2 and Every = 1 day, so a renewal happens tomorrow.</p>
      <button class="btn btn-primary" id="oSave">${isNew ? "Create offer" : "Save"}</button>
      <button class="btn btn-ghost" id="oCancel">Close</button>`;
    let picked = isNew ? null : { variant_id: o.variant_id, product_title: o.product_title, image: o.image };
    const find = async () => {
      $("oFound").innerHTML = `<span class="hint">Searching…</span>`;
      try {
        const r = await api(`/api/checkout/variants?store=${$("oStore").value}&q=${encodeURIComponent($("oFind").value)}`);
        $("oFound").innerHTML = r.variants.slice(0, 40).map((v, i) => `<button class="co-pick" data-i="${i}">
          ${v.image ? `<img src="${esc(v.image)}" alt="">` : ""}<span>${esc(v.product_title)}<small>${money(v.price, r.currency)} · ${esc(v.status)}</small></span></button>`).join("")
          || `<span class="hint">Nothing found.</span>`;
        $("oFound").querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => {
          picked = r.variants[+b.dataset.i];
          $("oPicked").innerHTML = "Product: <b>" + esc(picked.product_title) + "</b>";
          if (!$("oTitle").value) $("oTitle").value = picked.product_title;
          $("oFound").innerHTML = "";
        }));
      } catch (e) { $("oFound").innerHTML = `<span class="warn">${esc(e.message)}</span>`; }
    };
    $("oFindGo").onclick = find;
    $("oFind").onkeydown = (e) => { if (e.key === "Enter") find(); };
    $("oCancel").onclick = () => { f.hidden = true; };
    $("oSave").onclick = async () => {
      if (!picked) return toast("Pick a product first");
      const body = { id: isNew ? undefined : o.id, store_id: +$("oStore").value, ...picked, title: $("oTitle").value.trim(),
        sub_price: $("oSub").value, one_price: $("oOne").value, interval_days: +$("oEvery").value || 30,
        shipping: $("oShip").value || 0, countries: $("oCountries").value, active: +$("oActive").value, description: $("oDesc").value };
      delete body.price; delete body.status;
      try { await api("/api/checkout/offer", { method: "POST", body }); f.hidden = true; toast("Saved"); load(); }
      catch (e) { toast(e.message); }
    };
  }
  $("newOffer").onclick = () => offerForm(null);

  // ---- subscriptions
  const SUBST = { active: "Active", paused: "Paused", cancelled: "Cancelled", failed: "Stopped: payment failed" };
  function drawSubs() {
    $("subCount").textContent = `${D.subs.filter((s) => s.status === "active").length} active`;
    const rows = D.subs.map((s) => {
      const btns = [];
      if (s.status === "active") btns.push(["charge_now", "Charge now"], ["pause", "Pause"], ["cancel", "Cancel"]);
      if (s.status === "paused") btns.push(["resume", "Resume"], ["cancel", "Cancel"]);
      if (s.status === "failed") btns.push(["restart", "Try again"]);
      return `<tr><td><b>${esc(s.name)}</b><div class="hint">${esc(s.email)}${s.test ? " · test" : ""}</div></td>
        <td>${esc(storeName(s.store_id))}</td>
        <td class="num">${money(s.price + (s.shipping || 0), s.currency)} / ${s.interval_days} d</td>
        <td>${SUBST[s.status] || s.status}${s.last_error ? `<div class="hint warn">${esc(s.last_error)}</div>` : ""}</td>
        <td>${s.status === "active" ? day(s.next_at) : "—"}</td>
        <td class="num">${s.renewals}</td>
        <td>${btns.map(([a, t]) => `<button class="btn btn-sm btn-ghost" data-sub="${s.id}" data-a="${a}" data-manage-only>${t}</button>`).join(" ")}</td></tr>`;
    }).join("");
    $("subs").innerHTML = `<thead><tr><th>Customer</th><th>Store</th><th class="num">Charge</th><th>Status</th><th>Next charge</th>
      <th class="num">Renewals</th><th></th></tr></thead><tbody>${rows || `<tr><td colspan="7" class="hint">No subscriptions yet.</td></tr>`}</tbody>`;
    $("subs").querySelectorAll("[data-sub]").forEach((b) => (b.onclick = async () => {
      if (b.dataset.a === "cancel" && !confirm("Cancel this subscription? The customer won't be charged again.")) return;
      if (b.dataset.a === "charge_now" && !confirm("Charge this customer's saved card now for their next delivery?")) return;
      b.disabled = true;
      try { await api(`/api/checkout/sub/${b.dataset.sub}`, { method: "POST", body: { action: b.dataset.a } }); toast("Done"); load(); }
      catch (e) { toast(e.message); b.disabled = false; }
    }));
  }

  // ---- payments
  const KIND = { one: "One-time", first: "Subscription start", renewal: "Renewal" };
  const ST = { started: "Not paid", paid: "Paid", ordered: "Paid · order created", failed: "Failed", refunded: "Refunded" };
  function drawOrders() {
    const rows = D.orders.map((o) => {
      const acts = [];
      if (["paid", "ordered"].includes(o.status)) acts.push(["refund", "Refund"]);
      if (o.status === "paid" && o.error) acts.push(["reorder", "Create order again"]);
      return `<tr><td>${when(o.created_at)}<div class="hint">${esc(o.ref)}${o.test ? " · test" : ""}</div></td>
        <td>${KIND[o.kind] || o.kind}</td>
        <td><b>${esc(o.name || "")}</b><div class="hint">${esc(o.email || "")}</div></td>
        <td>${esc(storeName(o.store_id))}</td>
        <td class="num">${money(o.amount, o.currency)}</td>
        <td>${ST[o.status] || o.status}${o.error ? `<div class="hint warn">${esc(o.error)}</div>` : ""}</td>
        <td>${esc(o.shopify_name || "—")}</td>
        <td>${acts.map(([a, t]) => `<button class="btn btn-sm btn-ghost" data-ref="${esc(o.ref)}" data-a="${a}" data-manage-only>${t}</button>`).join(" ")}</td></tr>`;
    }).join("");
    $("orders").innerHTML = `<thead><tr><th>When</th><th>Type</th><th>Customer</th><th>Store</th><th class="num">Amount</th><th>Status</th>
      <th>Shopify</th><th></th></tr></thead><tbody>${rows || `<tr><td colspan="8" class="hint">No payments yet.</td></tr>`}</tbody>`;
    $("orders").querySelectorAll("[data-ref]").forEach((b) => (b.onclick = async () => {
      if (b.dataset.a === "refund" && !confirm("Refund this payment in full through Airwallex? (Mark the Shopify order as refunded or cancel it in Shopify too.)")) return;
      b.disabled = true;
      try { await api(`/api/checkout/order/${encodeURIComponent(b.dataset.ref)}`, { method: "POST", body: { action: b.dataset.a } }); toast("Done"); load(); }
      catch (e) { toast(e.message); b.disabled = false; }
    }));
  }

  // ---- settings
  api("/api/settings/checkout").then((k) => {
    $("kClient").placeholder = k.client_id || "Client ID";
    $("kAcct").value = k.account_id || "";
  }).catch(() => {});
  $("kSave").onclick = async () => {
    $("kMsg").textContent = "Checking…";
    try {
      await api("/api/settings/checkout", { method: "PUT", body: { client_id: $("kClient").value, api_key: $("kKey").value,
        account_id: $("kAcct").value, clear_account: !$("kAcct").value, webhook_secret: $("kHook").value, test: $("kTest").checked, check: true } });
      $("kKey").value = ""; $("kHook").value = ""; $("kClient").value = "";
      $("kMsg").textContent = "Saved. Airwallex accepted the key.";
      load();
    } catch (e) { $("kMsg").textContent = e.message; }
  };

  $("fStore").onchange = load;
  $("refresh").onclick = load;
  load().catch((e) => toast(e.message));
})();
