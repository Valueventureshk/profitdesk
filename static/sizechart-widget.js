/* ProfitDesk size chart for Shopify product pages. Added to a store as a script tag
   (SCM-style switch in ProfitDesk → Size Charts → Settings). It asks the store's own
   app proxy (/apps/track/sizechart) for this product's chart and draws a button and
   a pop-up. Works on any theme; does nothing on pages without a chart. */
(function () {
  if (window.__pdSizeChart) return;
  window.__pdSizeChart = true;
  var m = location.pathname.match(/\/products\/([^\/?#]+)/);
  if (!m) return;
  var handle = decodeURIComponent(m[1]);
  var root = (window.Shopify && Shopify.routes && Shopify.routes.root) || "/";
  var country = (window.Shopify && Shopify.country) || "";
  var base = root.replace(/\/$/, "") + "/apps/track/sizechart";

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  var ICONS = {
    ruler: '<path d="M3 15l12-12 6 6-12 12z"/><path d="M7 11l2 2M10 8l2 2M13 5l2 2"/>',
    tape: '<circle cx="9" cy="12" r="6"/><circle cx="9" cy="12" r="2"/><path d="M15 12h6v4h-6"/>',
    hanger: '<path d="M12 7a2 2 0 112-2M12 7v2L3 16h18l-9-7"/>',
    shirt: '<path d="M8 3l-5 3 2 4 3-1v12h8V9l3 1 2-4-5-3a4 4 0 01-8 0z"/>',
    none: ""
  };

  function css(cfg) {
    var acc = cfg.accent || "#111";
    return ".pdsc-trigger{display:inline-flex;align-items:center;gap:6px;cursor:pointer;margin:8px 0;font:inherit;line-height:1.2;" +
      "font-size:" + (+cfg.font_size || 14) + "px;color:" + cfg.color + ";" + (cfg.bold ? "font-weight:600;" : "") +
      (cfg.display === "link" ? "background:none;border:0;padding:0;text-decoration:underline;" :
        "background:" + cfg.bg + ";border:1px solid " + cfg.border + ";border-radius:6px;padding:8px 14px;" + (cfg.underline ? "text-decoration:underline;" : "")) + "}" +
      ".pdsc-trigger svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}" +
      ".pdsc-wrap{text-align:" + (cfg.align || "left") + "}" +
      ".pdsc-ov{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px}" +
      ".pdsc-box{background:#fff;color:#111;max-width:860px;width:100%;max-height:88vh;overflow:auto;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.3);font:inherit}" +
      ".pdsc-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 20px;border-bottom:1px solid #eee;position:sticky;top:0;background:#fff;z-index:1}" +
      ".pdsc-head h2{margin:0;font-size:18px;font-weight:600}.pdsc-x{background:none;border:0;font-size:26px;line-height:1;cursor:pointer;color:#111;padding:0 4px}" +
      ".pdsc-body{padding:16px 20px 22px}.pdsc-units{display:inline-flex;border:1px solid #ddd;border-radius:999px;overflow:hidden;margin-bottom:10px}" +
      ".pdsc-units button{background:none;border:0;padding:5px 14px;cursor:pointer;font:inherit;font-size:13px}.pdsc-units button.on{background:" + acc + ";color:#fff}" +
      ".pdsc-title{text-align:center;font-size:17px;margin:6px 0 12px}.pdsc-label{font-weight:600;margin:12px 0 6px}" +
      ".pdsc-scroll{overflow-x:auto;margin:6px 0 12px}.pdsc-table{border-collapse:collapse;width:100%;font-size:14px;min-width:420px}" +
      ".pdsc-table th,.pdsc-table td{border:1px solid #e5e5e5;padding:9px 10px;text-align:center}.pdsc-table thead th{background:#fafafa;font-weight:600}" +
      ".pdsc-table tbody th{font-weight:600}.pdsc-text{font-size:14px;line-height:1.55;margin:8px 0}.pdsc-text table{border-collapse:collapse;width:100%}" +
      ".pdsc-text td,.pdsc-text th{border:1px solid #e5e5e5;padding:8px;text-align:center}.pdsc-img{text-align:center;margin:10px 0}.pdsc-img img{max-width:100%;height:auto}" +
      ".pdsc-hr{border:0;border-top:1px solid #eee;margin:14px 0}.pdsc-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px}" +
      ".pdsc-tab{border:1px solid #ddd;background:#fff;border-radius:999px;padding:5px 14px;cursor:pointer;font:inherit;font-size:13px}.pdsc-tab.on{background:" + acc + ";color:#fff;border-color:" + acc + "}" +
      ".pdsc-adv{border:1px solid #eee;border-radius:10px;padding:14px;margin:4px 0 16px;background:#fafafa}" +
      ".pdsc-adv h3{margin:0 0 10px;font-size:15px}.pdsc-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}" +
      ".pdsc-grid label{display:grid;gap:4px;font-size:13px}.pdsc-grid input,.pdsc-grid select{padding:9px 10px;border:1px solid #ddd;border-radius:8px;font:inherit;font-size:14px;background:#fff;width:100%;box-sizing:border-box}" +
      ".pdsc-go{margin-top:12px;width:100%;padding:11px;border:0;border-radius:8px;background:" + acc + ";color:#fff;font:inherit;font-weight:600;cursor:pointer}" +
      ".pdsc-res{margin-top:12px;display:flex;align-items:center;gap:12px}.pdsc-size{min-width:52px;height:52px;padding:0 8px;border-radius:10px;background:" + acc + ";color:#fff;display:grid;place-items:center;font-size:20px;font-weight:700}" +
      ".pdsc-res small{display:block;color:#555}@media(max-width:560px){.pdsc-box{max-height:92vh}.pdsc-grid{grid-template-columns:1fr}}";
  }

  function anchor(cfg) {
    var form = document.querySelector('form[action*="/cart/add"]');
    if (cfg.position === "above_cart") {
      var add = document.querySelector('form[action*="/cart/add"] [name="add"], form[action*="/cart/add"] button[type="submit"]');
      if (add) return { el: add.closest(".product-form__buttons") || add, where: "before" };
    }
    if (cfg.position === "below_title") {
      var h = document.querySelector(".product__title, .product-single__title, h1");
      if (h) return { el: h.closest(".product__title") || h, where: "after" };
    }
    var v = document.querySelector("variant-radios, variant-selects, variant-picker, .product-form__input, .product-variants, .variant-wrapper, fieldset[name]");
    if (v) return { el: v, where: "before" };
    if (form) return { el: form, where: "before" };
    var t = document.querySelector("h1");
    return t ? { el: t, where: "after" } : null;
  }

  function build(d) {
    var cfg = d.cfg, tx = d.text, chart = d.chart;
    var st = document.createElement("style");
    st.textContent = css(cfg);
    document.head.appendChild(st);
    var wrap = document.createElement("div");
    wrap.className = "pdsc-wrap";
    var label = (cfg.prefix ? cfg.prefix + " " : "") + (tx.label || "Size chart") + (cfg.suffix ? " " + cfg.suffix : "");
    wrap.innerHTML = '<button type="button" class="pdsc-trigger">' + (ICONS[cfg.icon] ? '<svg viewBox="0 0 24 24">' + ICONS[cfg.icon] + "</svg>" : "") + "<span>" + esc(label) + "</span></button>";
    var a = anchor(cfg);
    if (!a) return;
    a.el.parentNode.insertBefore(wrap, a.where === "after" ? a.el.nextSibling : a.el);
    wrap.querySelector("button").addEventListener("click", function () { open(d); });
  }

  function open(d) {
    var tx = d.text, chart = d.chart, inches = false;
    var ov = document.createElement("div");
    ov.className = "pdsc-ov";
    ov.innerHTML = '<div class="pdsc-box" role="dialog" aria-modal="true"><div class="pdsc-head"><h2>' + esc(tx.chart) +
      '</h2><button type="button" class="pdsc-x" aria-label="Close">&times;</button></div><div class="pdsc-body">' +
      (d.advisor ? '<div class="pdsc-adv"><h3>' + esc(tx.advisor) + '</h3><div class="pdsc-grid">' +
        '<label>' + esc(tx.height) + ' (cm)<input type="number" inputmode="numeric" name="height" placeholder="170"></label>' +
        '<label>' + esc(tx.weight) + ' (kg)<input type="number" inputmode="numeric" name="weight" placeholder="65"></label>' +
        '<label>' + esc(tx.fit) + '<select name="fit"><option value="snug">' + esc(tx.snug) + '</option><option value="regular" selected>' + esc(tx.regular) + '</option><option value="loose">' + esc(tx.loose) + "</option></select></label>" +
        '<label>' + esc(tx.body) + '<select name="body"><option value="slim">' + esc(tx.slim) + '</option><option value="average" selected>' + esc(tx.average) + '</option><option value="curvy">' + esc(tx.curvy) + '</option><option value="athletic">' + esc(tx.athletic) + "</option></select></label>" +
        '</div><button type="button" class="pdsc-go">' + esc(tx.go) + '</button><div class="pdsc-res" hidden></div></div>' : "") +
      (chart.html_in ? '<div class="pdsc-units"><button type="button" data-u="cm" class="on">CM</button><button type="button" data-u="in">IN</button></div>' : "") +
      '<div class="pdsc-content">' + chart.html + "</div></div></div>";
    document.body.appendChild(ov);
    document.documentElement.style.overflow = "hidden";
    function close() { ov.remove(); document.documentElement.style.overflow = ""; }
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    ov.querySelector(".pdsc-x").addEventListener("click", close);
    document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", k); } });
    function wireTabs() {
      ov.querySelectorAll(".pdsc-tab").forEach(function (b) {
        b.addEventListener("click", function () {
          var g = b.dataset.tab.split("-")[0];
          ov.querySelectorAll('.pdsc-tab[data-tab^="' + g + '-"]').forEach(function (x) { x.classList.toggle("on", x === b); });
          ov.querySelectorAll('.pdsc-pane[data-pane^="' + g + '-"]').forEach(function (p) { p.hidden = p.dataset.pane !== b.dataset.tab; });
        });
      });
    }
    wireTabs();
    ov.querySelectorAll(".pdsc-units button").forEach(function (b) {
      b.addEventListener("click", function () {
        inches = b.dataset.u === "in";
        ov.querySelectorAll(".pdsc-units button").forEach(function (x) { x.classList.toggle("on", x === b); });
        ov.querySelector(".pdsc-content").innerHTML = inches ? chart.html_in : chart.html;
        wireTabs();
      });
    });
    var go = ov.querySelector(".pdsc-go");
    if (go) go.addEventListener("click", function () {
      var f = ov.querySelector(".pdsc-adv"), res = ov.querySelector(".pdsc-res");
      var body = { handle: handle, country: country, height: f.querySelector('[name="height"]').value, weight: f.querySelector('[name="weight"]').value,
        fit: f.querySelector('[name="fit"]').value, body: f.querySelector('[name="body"]').value, height_unit: "cm", weight_unit: "kg" };
      if (!body.height || !body.weight) { f.querySelector('[name="height"]').focus(); return; }
      go.disabled = true; go.textContent = tx.busy;
      fetch(base + "/advise", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function (r) { return r.json(); })
        .then(function (a) {
          res.hidden = false;
          res.innerHTML = a.size ? '<span class="pdsc-size">' + esc(a.size) + "</span><span><b>" + esc(tx.rec) + " " + esc(a.size) + "</b>" +
            (a.range && a.range !== a.size ? " (" + esc(a.range) + ")" : "") + "<small>" + esc(a.reason || "") + "</small></span>" : "<span>" + esc(a.error || "") + "</span>";
        })
        .catch(function () {})
        .then(function () { go.disabled = false; go.textContent = tx.again; });
    });
  }

  function start() {
    fetch(base + "?handle=" + encodeURIComponent(handle) + "&country=" + encodeURIComponent(country), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.chart) build(d); })
      .catch(function () {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
