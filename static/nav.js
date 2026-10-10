/* The section menu behind the PD logo, shared by every ProfitDesk page.
   Phones and desktop both open it by tapping/clicking the logo. */
(function () {
  const SECTIONS = [
    { href: "/", desk: "profit", label: "Profit dashboard", note: "Sales, ad spend, fees and profit" },
    { href: "/cash", desk: "cash", label: "Cash flow", note: "Balances and money arriving" },
    { href: "/cog", desk: "cog", label: "COG + Products Monitor", note: "What every order cost, product costs" },
    { href: "/inbox", desk: "inbox", label: "Inbox", note: "Customer emails from every store" },
    { href: "/reports", desk: "reports", label: "Reports", note: "Download spreadsheets of any figures" },
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
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
