/* ProfitDesk session recordings: added to a store's pages (ScriptTag) when Session Recordings is on.
   Records what a visitor sees and does (rrweb: page structure + changes, not video), never what they
   type (every input is masked), and not at all when the shopper declined analytics in Shopify's
   cookie banner. Chunks go through the store's app proxy (/apps/track/rec), so only real stores can
   send them. A visitor is sampled once per browser session. */
(function () {
  if (window.__pdRec) return;
  window.__pdRec = 1;
  if (navigator.webdriver || /^\/(account|checkouts?|challenge|password|admin)/.test(location.pathname)) return;
  var BASE = "/apps/track/rec";
  var LIB = "https://cdn.jsdelivr.net/npm/@rrweb/record@2.1.7/umd/record.min.js";
  var MAX_BYTES = 6e6, MAX_MS = 30 * 60 * 1000;
  var ss = null;
  try { ss = window.sessionStorage; ss.setItem("pdRecT", "1"); } catch (e) { return; }

  function allowed() {
    try {
      var cp = window.Shopify && window.Shopify.customerPrivacy;
      if (cp && typeof cp.analyticsProcessingAllowed === "function") return cp.analyticsProcessingAllowed();
    } catch (e) { /* no consent API: no banner on this store */ }
    return true;
  }
  function newId() {
    try { if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, ""); } catch (e) { /* older browser */ }
    return (Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)).slice(0, 32);
  }

  var state = ss.getItem("pdRecOn");
  if (state === "0") return;
  if (state === "1" && ss.getItem("pdRecSid")) { begin(); return; }
  fetch(BASE + "/config", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (c) {
    if (!c || !c.on) return;
    var on = Math.random() * 100 < (c.sample || 0);
    ss.setItem("pdRecOn", on ? "1" : "0");
    if (!on) return;
    ss.setItem("pdRecSid", newId());
    ss.setItem("pdRecT0", String(Date.now()));
    begin();
  }).catch(function () { /* app proxy missing: do nothing */ });

  function begin() {
    if (!allowed()) return;
    if (Date.now() - Number(ss.getItem("pdRecT0") || Date.now()) > MAX_MS) return;
    var s = document.createElement("script");
    s.src = LIB;
    s.async = true;
    s.onload = go;
    document.head.appendChild(s);
  }

  function go() {
    var R = window.rrwebRecord;
    var record = R && (R.record || (R["default"] && R["default"].record));
    if (typeof record !== "function") return;
    var sid = ss.getItem("pdRecSid");
    var buf = [], size = 0, stopped = false, stopFn = null, sending = Promise.resolve();
    var meta = { u: location.href.slice(0, 500), r: (document.referrer || "").slice(0, 300), w: window.innerWidth,
                 h: window.innerHeight, m: /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ? 1 : 0,
                 tz: (Intl.DateTimeFormat().resolvedOptions().timeZone || "").slice(0, 60), l: (navigator.language || "").slice(0, 20) };

    function gzip(text) {
      if (typeof CompressionStream === "undefined") return Promise.resolve(null);
      var stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
      return new Response(stream).blob().catch(function () { return null; });
    }
    function flush(final) {
      if (!buf.length) return;
      var seq = Number(ss.getItem("pdRecSeq") || 0) + 1;
      ss.setItem("pdRecSeq", String(seq));
      var body = JSON.stringify({ meta: meta, events: buf });
      buf = [];
      size = 0;
      var total = Number(ss.getItem("pdRecBytes") || 0) + body.length;
      ss.setItem("pdRecBytes", String(total));
      if (total > MAX_BYTES && stopFn) { stopped = true; stopFn(); }
      var url = BASE + "?sid=" + sid + "&seq=" + seq;
      if (final && navigator.sendBeacon && body.length < 60000) {
        navigator.sendBeacon(url, new Blob([body], { type: "text/plain" }));
        return;
      }
      sending = sending.then(function () {
        return gzip(body).then(function (z) {
          return fetch(z ? url + "&z=1" : url, { method: "POST", credentials: "same-origin", keepalive: !!final && (z ? z.size : body.length) < 60000,
                                                 headers: { "Content-Type": "text/plain" }, body: z || body });
        }).catch(function () { /* dropped chunk: the replay skips it */ });
      });
    }
    stopFn = record({
      emit: function (e) {
        if (stopped) return;
        buf.push(e);
        size += e.type === 2 ? 200000 : 300;          // a full page snapshot goes out straight away
        if (size >= 150000) flush(false);
      },
      maskAllInputs: true,
      maskInputOptions: { password: true, email: true, tel: true, text: true, textarea: true, number: true, search: true },
      blockSelector: "iframe, .pd-rec-block",
      slimDOMOptions: "all",
      sampling: { mousemove: 80, scroll: 150, media: 800, input: "last" },
      recordCanvas: false,
      collectFonts: false,
      inlineImages: false,
    });
    setInterval(function () { flush(false); }, 5000);
    window.addEventListener("pagehide", function () { flush(true); });
    document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(true); });
    setTimeout(function () { if (stopFn) { flush(false); stopped = true; stopFn(); } },
               Math.max(0, MAX_MS - (Date.now() - Number(ss.getItem("pdRecT0") || Date.now()))));
  }
})();
