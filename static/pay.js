/* The payment page: details -> ProfitDesk prices it and opens an Airwallex payment -> Airwallex's Drop-in takes the
   card (and saves it for a subscription) -> ProfitDesk checks the payment and creates the Shopify order. */
(function () {
  const O = window.OFFER;
  const $ = (id) => document.getElementById(id);
  const money = (v) => new Intl.NumberFormat(undefined, { style: "currency", currency: O.currency }).format(v || 0);
  const every = (d) => (d === 30 || d === 31 ? "month" : d === 7 ? "week" : d === 14 ? "2 weeks" : d === 1 ? "day" : `${d} days`);
  const COUNTRIES = { AU: "Australia", NZ: "New Zealand", US: "United States", CA: "Canada", GB: "United Kingdom",
    IE: "Ireland", SG: "Singapore", HK: "Hong Kong", DE: "Germany", FR: "France", NL: "Netherlands" };
  const STATES = { AU: [["NSW", "New South Wales"], ["VIC", "Victoria"], ["QLD", "Queensland"], ["WA", "Western Australia"],
    ["SA", "South Australia"], ["TAS", "Tasmania"], ["ACT", "Australian Capital Territory"], ["NT", "Northern Territory"]] };

  let plan = O.sub_price ? "sub" : "one";
  let started = null;

  // ---- order summary
  function price() { return plan === "sub" ? O.sub_price : O.one_price; }
  function drawSummary() {
    const p = price(), total = p + (O.shipping || 0);
    $("img").src = O.image || "";
    $("img").hidden = !O.image;
    $("ptitle").textContent = O.title;
    $("pplan").textContent = plan === "sub" ? `Delivered every ${every(O.interval_days)}` : "One-time purchase";
    $("pprice").textContent = money(p);
    $("sub").textContent = money(p);
    $("ship").textContent = O.shipping ? money(O.shipping) : "Free";
    $("cur").textContent = O.currency;
    $("total").textContent = money(total);
    $("sumTotalTop").textContent = money(total);
    $("then").textContent = plan === "sub" ? `Then ${money(total)} every ${every(O.interval_days)}. Skip, pause or cancel anytime.` : "";
    $("desc").textContent = O.description || "";
    $("agreeWrap").hidden = plan !== "sub";
    $("agreeText").textContent = `I agree to be charged ${money(total)} today and every ${every(O.interval_days)} after that ` +
      `until I cancel. I can skip, pause or cancel anytime from the link in my confirmation.`;
  }

  function drawPlans() {
    const opts = [];
    if (O.sub_price) {
      const save = O.one_price && O.one_price > O.sub_price ? Math.round((1 - O.sub_price / O.one_price) * 100) : 0;
      opts.push(["sub", `Subscribe & save${save ? `<span class="plan-badge">Save ${save}%</span>` : ""}`,
        `Delivered every ${every(O.interval_days)} · skip or cancel anytime`, O.sub_price]);
    }
    if (O.one_price) opts.push(["one", "One-time purchase", "Just this order", O.one_price]);
    $("plans").innerHTML = opts.map(([k, t, s, p]) => `<label class="plan${k === plan ? " on" : ""}">
      <input type="radio" name="plan" value="${k}"${k === plan ? " checked" : ""}>
      <div class="plan-t"><b>${t}</b><span>${s}</span></div><div class="plan-p">${money(p)}</div></label>`).join("");
    $("plans").querySelectorAll("input").forEach((r) => (r.onchange = () => { plan = r.value; drawPlans(); drawSummary(); }));
  }

  function drawAddress() {
    $("country").innerHTML = O.countries.map((c) => `<option value="${c}">${COUNTRIES[c] || c}</option>`).join("");
    const states = () => {
      const s = STATES[$("country").value];
      $("province").hidden = !s;
      $("province").innerHTML = s ? `<option value="">State</option>` + s.map(([k, n]) => `<option value="${k}">${n}</option>`).join("") : "";
    };
    $("country").onchange = states;
    states();
  }

  function err(id, msg) { $(id).textContent = msg || ""; $(id).hidden = !msg; }

  function details() {
    return {
      slug: O.slug, plan, agree: $("agree").checked, email: $("email").value.trim(),
      name: `${$("first").value.trim()} ${$("last").value.trim()}`.trim(), phone: $("phone").value.trim(),
      address: { address1: $("address1").value, address2: $("address2").value, city: $("city").value,
                 province: $("province").hidden ? "" : $("province").value, zip: $("zip").value, country: $("country").value },
    };
  }

  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.detail || j.error || "Something went wrong. Please try again."), { status: r.status });
    return j;
  }

  // ---- step 1 -> 2
  $("go").onclick = async () => {
    const d = details();
    if (!d.email.includes("@")) return err("err", "Please enter your email.");
    if (!d.name || !d.address.address1 || !d.address.city || !d.address.zip) return err("err", "Please fill in your name and full address.");
    if (!$("province").hidden && !d.address.province) return err("err", "Please choose your state.");
    if (plan === "sub" && !d.agree) return err("err", "Please tick the box to agree to the subscription.");
    err("err");
    $("go").disabled = true;
    $("go").textContent = "One moment…";
    try {
      started = await post("/pay/api/start", d);
      started.plan = plan;
      $("review").innerHTML = `<div><span>Contact</span>${esc(d.email)}</div>
        <div><span>Ship to</span>${esc([d.address.address1, d.address.address2, d.address.city, d.address.province, d.address.zip, d.address.country].filter(Boolean).join(", "))}</div>
        <div><span>Pay</span>${money(started.amount)}${plan === "sub" ? ` today, then every ${every(O.interval_days)}` : ""}</div>`;
      $("step-details").hidden = true;
      $("step-pay").hidden = false;
      window.scrollTo(0, 0);
      await mountDropIn();
    } catch (e) {
      err("err", e.message);
    } finally {
      $("go").disabled = false;
      $("go").textContent = "Continue to payment";
    }
  };
  $("edit").onclick = () => { $("step-pay").hidden = true; $("step-details").hidden = false; $("dropin").innerHTML = ""; };

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- step 2: Airwallex Drop-in
  async function mountDropIn() {
    $("dropin").innerHTML = "";
    err("err2");
    const sdk = window.AirwallexComponentsSDK;
    if (!sdk) return err("err2", "The payment form couldn't load. Please refresh the page.");
    await sdk.init({ env: started.env, enabledElements: ["payments"], origin: window.location.origin });
    const opts = { intent_id: started.intent_id, client_secret: started.client_secret, currency: started.currency,
                   country_code: started.country };
    if (started.plan === "sub") {
      opts.customer_id = started.customer_id;
      opts.payment_consent = { next_triggered_by: "merchant", merchant_trigger_reason: "scheduled" };
      opts.submitType = "subscribe";
    }
    const el = await sdk.createElement("dropIn", opts);
    el.mount("dropin");
    el.on("success", (e) => finish(((e.detail || {}).consent || {}).id));
    el.on("error", (e) => {
      const x = (e.detail || {}).error || {};
      err("err2", x.message || "The payment didn't go through. Please check your details or try another card.");
    });
  }

  // ---- step 3: ProfitDesk checks the payment and creates the order
  async function finish(consentId) {
    $("step-pay").hidden = true;
    $("step-done").hidden = false;
    $("doneTitle").textContent = "Confirming your order…";
    for (let i = 0; i < 6; i++) {
      try {
        const r = await post("/pay/api/done", { ref: started.ref, intent_id: started.intent_id, consent_id: consentId });
        $("doneTitle").textContent = "Thank you!";
        $("doneText").textContent = r.order ? `Your order ${r.order} is confirmed. A confirmation email is on its way.`
          : "Your payment went through and your order is being created. A confirmation email is on its way.";
        if (r.manage) {
          $("doneSub").innerHTML = `Your next delivery is charged on ${new Date(r.next).toLocaleDateString()}. ` +
            `<a href="${esc(r.manage)}">Manage your subscription</a> (skip, pause or cancel). Keep this link.`;
        }
        return;
      } catch (e) {
        if (e.status !== 409 || i === 5) {
          $("doneTitle").textContent = "Payment received";
          $("doneText").textContent = "We're finishing your order. If you don't get a confirmation email within an hour, please contact us.";
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
  }

  $("sumToggle").onclick = () => $("side").classList.toggle("open");
  $("testbar").hidden = !O.test;
  drawPlans();
  drawSummary();
  drawAddress();
})();
