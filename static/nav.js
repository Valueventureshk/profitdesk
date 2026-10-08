/* The section menu behind the PD logo, shared by every ProfitDesk page.
   Phones and desktop both open it by tapping/clicking the logo. */
(function () {
  const SECTIONS = [
    { href: "/", label: "Profit dashboard", note: "Sales, ad spend, fees and profit" },
    { href: "/cash", label: "Cash flow", note: "Balances and money arriving" },
    { href: "/cog", label: "COG + Products Monitor", note: "What every order cost, product costs" },
    { label: "Accounting", note: "Coming soon", soon: true },
  ];

  function build() {
    const trigger = document.querySelector("[data-section-menu]");
    if (!trigger) return;
    const menu = document.createElement("div");
    menu.className = "section-menu";
    menu.hidden = true;
    menu.innerHTML = SECTIONS.map((s) => s.soon
      ? `<div class="section-item soon"><strong>${s.label}</strong><span>${s.note}</span></div>`
      : `<a class="section-item${location.pathname === s.href ? " on" : ""}" href="${s.href}">
           <strong>${s.label}</strong><span>${s.note}</span></a>`).join("");
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
