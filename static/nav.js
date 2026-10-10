/* The section menu behind the PD logo, shared by every ProfitDesk page.
   Phones and desktop both open it by tapping/clicking the logo. */
(function () {
  const SECTIONS = [
    { href: "/", desk: "profit", label: "Profit dashboard", note: "Sales, ad spend, fees and profit" },
    { href: "/cash", desk: "cash", label: "Cash flow", note: "Balances and money arriving" },
    { href: "/cog", desk: "cog", label: "COG + Products Monitor", note: "What every order cost, product costs" },
    { href: "/inbox", desk: "inbox", label: "Inbox", note: "Customer emails from every store" },
    { href: "/reports", desk: "reports", label: "Reports", note: "Download spreadsheets of any figures" },
    { href: "/scm", desk: "scm", label: "SCM", note: "Every order's shipping and tracking" },
    { href: "/expenses", desk: "expenses", label: "Expenses", note: "Every payment out, by category" },
  ];

  // Who's logged in: only their desks show in the menu; owner-only and
  // change buttons hide for people without that access (the server enforces it too).
  let me = null;
  const ready = fetch("/api/me").then((r) => (r.ok ? r.json() : null)).then((m) => {
    me = m;
    if (!m) return;
    document.body.classList.toggle("not-owner", m.role !== "owner");
    document.body.classList.toggle("read-only", m.role === "read");
  }).catch(() => {});
  window.pdMe = () => ready.then(() => me);

  async function build() {
    const trigger = document.querySelector("[data-section-menu]");
    if (!trigger) return;
    await ready;
    const menu = document.createElement("div");
    menu.className = "section-menu";
    menu.hidden = true;
    const allowed = SECTIONS.filter((s) => !s.desk || !me || (me.desks || []).includes(s.desk))
      .filter((s) => !s.soon || !me || me.role === "owner");
    const dark = () => document.documentElement.dataset.theme === "dark";
    const themeItem = () => `<button class="section-item theme-toggle" data-theme-toggle>
        <strong>${dark() ? "☀ Day mode" : "☾ Night mode"}</strong><span>Switch the colours on this device</span></button>`;
    menu.innerHTML = allowed.map((s) => s.soon
      ? `<div class="section-item soon"><strong>${s.label}</strong><span>${s.note}</span></div>`
      : `<a class="section-item${location.pathname === s.href ? " on" : ""}" href="${s.href}">
           <strong>${s.label}</strong><span>${s.note}</span></a>`).join("") + themeItem();
    const wireTheme = () => {
      menu.querySelector("[data-theme-toggle]").onclick = (e) => {
        e.stopPropagation();
        const next = dark() ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem("pdTheme", next); } catch { /* private mode */ }
        menu.querySelector("[data-theme-toggle]").outerHTML = themeItem();
        wireTheme();
        window.dispatchEvent(new Event("pd-theme"));
      };
    };
    wireTheme();
    document.body.appendChild(menu);

    const place = () => {
      const r = trigger.getBoundingClientRect();
      menu.style.top = `${r.bottom + 6 + window.scrollY}px`;
      menu.style.left = `${Math.max(8, r.left)}px`;
    };
    trigger.setAttribute("role", "button");
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.tabIndex = 0;
    const toggle = (e) => {
      e.preventDefault();
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      if (!menu.hidden) place();
    };
    trigger.addEventListener("click", toggle);
    trigger.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") toggle(e); });
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.hidden = true; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.hidden = true; });
  }
  // Desktop: a dark sidebar like Shopify's admin, on every desk.
  const ICON = {
    "/": '<path d="M3 13h4v6H3zM10 8h4v11h-4zM17 4h4v15h-4z"/>',
    "/cash": '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9v.01M18 15v.01"/>',
    "/cog": '<path d="M4 7l8-4 8 4v10l-8 4-8-4z"/><path d="M4 7l8 4 8-4M12 11v10"/>',
    "/inbox": '<path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1.5 2.5h5L16 13h5"/>',
    "/scm": '<path d="M2 7h11v9H2zM13 10h4l4 3v3h-8z"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
    "/expenses": '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    "/reports": '<path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    theme: '<path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/>',
    logout: '<path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10"/>',
  };
  const svg = (k) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[k] || ""}</svg>`;

  async function buildRail() {
    if (document.querySelector(".pd-rail") || !document.querySelector("[data-section-menu]")) return;
    await ready;
    if (!me) return;
    const here = location.pathname.replace(/\/$/, "") || "/";
    const allowed = SECTIONS.filter((s) => (me.desks || []).includes(s.desk));
    const rail = document.createElement("aside");
    rail.className = "pd-rail";
    const dark = () => document.documentElement.dataset.theme === "dark";
    rail.innerHTML = `
      <a class="pd-rail-brand" href="/"><span class="brand-mark">PD</span><span>ProfitDesk</span></a>
      <nav class="pd-rail-nav">${allowed.map((s) => `<a class="pd-rail-item${here === s.href ? " on" : ""}" href="${s.href}" title="${s.note}">
        ${svg(s.href)}<span>${s.label.replace(" + Products Monitor", "")}</span></a>`).join("")}</nav>
      <div class="pd-rail-foot">
        ${me.role === "owner" ? `<a class="pd-rail-item" href="/#settings">${svg("settings")}<span>Settings</span></a>` : ""}
        <button class="pd-rail-item" data-rail-theme>${svg("theme")}<span>${dark() ? "Day mode" : "Night mode"}</span></button>
        <button class="pd-rail-item" data-rail-out>${svg("logout")}<span>Log out</span></button>
        <div class="pd-rail-user"><span class="pd-rail-avatar">${(me.email || "?").slice(0, 2).toUpperCase()}</span>
          <span class="pd-rail-who">${me.email || ""}<small>${me.role === "owner" ? "Owner" : me.role === "write" ? "Read & write" : "Read only"}</small></span></div>
      </div>`;
    document.body.prepend(rail);
    document.body.classList.add("has-rail");
    rail.querySelector("[data-rail-theme]").onclick = (e) => {
      const next = dark() ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("pdTheme", next); } catch { /* private mode */ }
      e.currentTarget.querySelector("span").textContent = next === "dark" ? "Day mode" : "Night mode";
      window.dispatchEvent(new Event("pd-theme"));
    };
    rail.querySelector("[data-rail-out]").onclick = async () => {
      await fetch("/api/logout", { method: "POST" });
      location.href = "/login";
    };
    // The dashboard opens Settings when it's asked for in the address (from the sidebar).
    if (here === "/" && location.hash === "#settings") {
      for (let i = 0; i < 40; i++) {
        const b = document.getElementById("openSettings");
        if (b && b.offsetParent !== null) { b.click(); history.replaceState(null, "", "/"); break; }
        await new Promise((r) => setTimeout(r, 250));
      }
    }
  }

  function start() { build(); buildRail(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
