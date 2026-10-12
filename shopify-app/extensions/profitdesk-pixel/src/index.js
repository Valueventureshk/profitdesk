// ProfitDesk tracking pixel (Shopify web pixel, strict sandbox).
// Sends each shopper event to ProfitDesk with the ad click it came from (UTMs, fbclid / gclid /
// ttclid in the landing URL) and Meta's browser cookies (_fbp, _fbc), so ProfitDesk can attribute
// add-to-carts, checkouts and orders to campaigns, ad sets and ads, and later send them to Meta's
// Conversions API. Shopify only runs it for shoppers who allowed tracking.
import { register } from "@shopify/web-pixels-extension";

const EVENTS = [
  "page_viewed",
  "product_viewed",
  "product_added_to_cart",
  "checkout_started",
  "checkout_contact_info_submitted",
  "checkout_shipping_info_submitted",
  "payment_info_submitted",
  "checkout_completed",
];

register(({ analytics, browser, settings, init }) => {
  const endpoint = settings.endpoint;
  const key = settings.accountID;
  if (!endpoint || !key) return;

  async function cookies() {
    const get = async (name) => {
      try { return (await browser.cookie.get(name)) || ""; } catch (e) { return ""; }
    };
    return { fbp: await get("_fbp"), fbc: await get("_fbc") };
  }

  function money(m) {
    return m && typeof m.amount === "number" ? { value: m.amount, currency: m.currencyCode } : {};
  }

  function details(name, data) {
    data = data || {};
    if (name === "product_viewed" && data.productVariant) {
      const v = data.productVariant;
      return { product_id: v.product && v.product.id, variant_id: v.id, title: v.product && v.product.title, ...money(v.price) };
    }
    if (name === "product_added_to_cart" && data.cartLine) {
      const l = data.cartLine, v = l.merchandise || {};
      return { product_id: v.product && v.product.id, variant_id: v.id, title: v.product && v.product.title,
               quantity: l.quantity, ...money(l.cost && l.cost.totalAmount) };
    }
    const c = data.checkout;
    if (c) {
      return {
        token: c.token, order_id: c.order && c.order.id, email: c.email || "", phone: c.phone || "",
        ...money(c.totalPrice),
        items: (c.lineItems || []).map((l) => ({ variant_id: l.variant && l.variant.id,
                                                   product_id: l.variant && l.variant.product && l.variant.product.id,
                                                   quantity: l.quantity })),
      };
    }
    return {};
  }

  async function send(event) {
    const ctx = event.context || {};
    const doc = ctx.document || {};
    const body = {
      k: key,
      n: event.name,
      id: event.id,
      t: event.timestamp,
      cid: event.clientId,
      url: (doc.location && doc.location.href) || "",
      ref: doc.referrer || "",
      ua: (ctx.navigator && ctx.navigator.userAgent) || "",
      lang: (ctx.navigator && ctx.navigator.language) || "",
      ...(await cookies()),
      d: details(event.name, event.data),
    };
    try {
      await fetch(endpoint, { method: "POST", keepalive: true, headers: { "Content-Type": "text/plain" },
                              body: JSON.stringify(body) });
    } catch (e) { /* offline or blocked: nothing to do */ }
  }

  for (const name of EVENTS) analytics.subscribe(name, send);
});
