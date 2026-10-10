// Garment line icons for the Size Charts desk (drawn for ProfitDesk, 48×48, stroke = currentColor).
(function () {
  const P = {
    dress: "M19 5v7M29 5v7M19 12h10c0 3 .8 5.6 2 8H17c1.2-2.4 2-5 2-8zM17 20 9.5 40.5c9.5 3.6 19.5 3.6 29 0L31 20M20 20l-3 21M28 20l3 21",
    top: "M18 5v6c0 2.5-3 4.5-3 9v23h18V20c0-4.5-3-6.5-3-9V5M18 11.5c2.8 3.2 9.2 3.2 12 0",
    tshirt: "M18 7c1.4 2.4 3.4 3.6 6 3.6S28.6 9.4 30 7l9 4 4.5 9.5-6 3L34 20.5V42H14V20.5L10.5 23.5l-6-3L9 11z",
    shirt: "M18 6l6 6.5L30 6l9 4 5 22.5-5 1.2-4-15V43H13V18.7l-4 15-5-1.2L9 10zM18 6l-2.2 6.4L21 15l3-2.5 3 2.5 5.2-2.6L30 6M24 12.5V43M27 20h.01M27 27h.01M27 34h.01",
    jacket: "M17 6l7 4.5L31 6l8 4 5 23.5-5 1.2-3-13.5V43H12V21.2L9 34.7l-5-1.2L9 10zM17 6l1.8 10.5L24 23l5.2-6.5L31 6M24 23v20M15 33h5M28 33h5",
    pants: "M13 5h22l3.5 38h-9.5L24 18.5 19 43H9.5zM13 10.5h22M24 10.5v8",
    jeans: "M13 5h22l3.5 38h-9.5L24 18.5 19 43H9.5zM13 10.5h22M24 10.5v8M16 10.5c0 3.3 2 5.3 5 5.3M32 10.5c0 3.3-2 5.3-5 5.3M30 5v3",
    skirt: "M15 7h18v6H15zM15 13 8.5 40h31L33 13M20 13l-3 27M24 13v27M28 13l3 27",
    bra: "M15.5 23 17.5 8M32.5 23 30.5 8M6 24.5c1.2 8 6.3 12.5 12 12.5 4 0 6-3.2 6-7.6 0 4.4 2 7.6 6 7.6 5.7 0 10.8-4.5 12-12.5M6 24.5c5.5-2.4 11-1.8 14.5 2.2L24 30l3.5-3.3c3.5-4 9-4.6 14.5-2.2",
    swim: "M16 5v8c0 4 3.6 7 8 7s8-3 8-7V5M16 13c-1.4 9-2 16-1 23 4 .3 7 2.6 9 6.5 2-3.9 5-6.2 9-6.5 1-7 .4-14-1-23",
    heel: "M6 21c3 0 5.5 2.2 7.6 5.6 3.7 6 11 8.8 19.4 8.8 5.3 0 9.5 1.8 9.5 4.6H23.5c-3.3 0-5.6-3-8.8-3H11.5v6.5h-3V35C7 30.5 6 26 6 21z",
    shoe: "M4 35V20.5c3.5 0 6 1.8 9 1.8s4.5-1.8 4.5-4.3l3.3.8c2.8 6.5 9 10.4 16 11.4 4.6.7 7.7 3 7.7 6.3V37H4zM4 35h40M18.5 23.5l3-2M21 27l3-2M24 30.5l3-2",
    ring: "M24 18.5a12 12 0 1 0 .01 0M24 22.5a8 8 0 1 0 .01 0M18.5 10.5 24 5l5.5 5.5L24 15z",
    kids: "M18 7c1.2 2 3.4 3 6 3s4.8-1 6-3l7 3.5 3 8-5 2.5-2-2.5V26c0 2 2 4 2 6v9h-8v-7l-1-2-1 2v7h-8v-9c0-2 2-4 2-6v-6.5L17 22l-5-2.5 3-8z",
    hanger: "M20 10a4 4 0 1 1 6.4 3.2c-1.5 1.1-2.4 2-2.4 3.8v1L6 31.5c-2 1.5-1 4.5 1.5 4.5h33c2.5 0 3.5-3 1.5-4.5L24 18",
  };
  // Most specific first: "t-shirt" before "shirt", "jean" before "pant"…
  const RULES = [
    ["dress", /dress|robe|vestido|gown|kleid|abito|maxi|midi/],
    ["swim", /swim|bikini|maillot de bain|banador|beach/],
    ["bra", /\bbra\b|bras\b|soutien|lingerie|sujetador|underwear|brief|culotte|panty|panties/],
    ["skirt", /skirt|jupe|falda/],
    ["jeans", /jean|denim|vaquero/],
    ["pants", /pant|trouser|legging|short|jogger|cargo|combinaison|jumpsuit|romper|mono\b/],
    ["jacket", /jacket|coat|veste|manteau|blazer|chaqueta|abrigo|parka|cardigan|hood|sweat|pull|jumper|sweater|knit|gilet/],
    ["tshirt", /t-?shirt|\btee\b|camiseta/],
    ["shirt", /shirt|chemise\b|camisa|polo/],
    ["top", /\btop|blouse|chemisier|blusa|cami|tank|bodysuit|corset|debardeur|tunic|tunique|women clothing|vetement/],
    ["heel", /heel|talon|tacon|pump|sandal|boot|botte|bota|women shoe|mule|escarpin/],
    ["shoe", /shoe|sneaker|chaussure|zapat|basket|loafer|mocassin|trainer|foot/],
    ["ring", /ring\b|bague|anillo|bracelet|necklace|jewel|bijou|watch|montre/],
    ["kids", /kid|child|enfant|nino|nina|baby|bebe/],
  ];
  const TEMPLATE = { women_clothing: "top", women_dress: "dress", men_shirts: "shirt", men_tshirt: "tshirt", jacket: "jacket",
    pants: "pants", jeans: "jeans", skirt: "skirt", swimwear: "swim", bra: "bra", women_shoes: "heel", men_shoes: "shoe", kids: "kids" };

  function guess(text) {
    const t = String(text || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    const hit = RULES.find(([, re]) => re.test(t));
    return hit ? hit[0] : "hanger";
  }
  function svg(kind, size) {
    const d = P[kind] || P.hanger;
    return `<svg class="pd-garment" viewBox="0 0 48 48" width="${size || 24}" height="${size || 24}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
  }
  window.Garments = { svg, guess, forTemplate: (key) => TEMPLATE[key] || "hanger", kinds: Object.keys(P) };
})();
