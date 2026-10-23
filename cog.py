"""COG (cost of goods): what each order really cost from the supplier.

Sources, best first, for every product line of every Shopify order:

    invoice    the order's own row(s) in the orders sheet, with the supplier's COG
               filled in from their invoice (one row per item; quantity 2 = 2 rows)
    catalog    no row yet: the product's latest known cost in that store, from the
               sheet's history (same product name, or the same product in
               another colour/size when the exact variant has never been bought)
    estimate   never bought before: the store's average COG % of sales, shown in a
               different colour so someone can check it

COG in the sheet is always USD and includes the supplier's shipping. Costs are
kept in USD here and converted to a store's currency only when shown.

Order numbers are matched without the "#" and spaces, so "#AS13079", "AS13079 "
and "as13079" are the same order. Each store's tag (AS…, …CH) is learned from
its own Shopify order names.
"""
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

SKIP_TABS = re.compile(r"copy of|issue|reference|coverage|^sheet\d*$", re.I)
ORDER_RE = re.compile(r"^([A-Z&]*?)(\d+)([A-Z&]*)$")


BOGO = re.compile(r"\bbogo\b|buy\s*(?:1|one)\s*get\s*(?:1|one)", re.I)


def pack(name: str) -> int:
    """Items the supplier sends per unit sold: 2 for a "BOGO" / "Buy 1 Get 1" product or variant, else 1."""
    return 2 if BOGO.search(name or "") else 1


INVOICE_SEQ = 10 ** 9      # history entries from uploaded invoices are numbered from here (ahead of the sheet)


def recency(h) -> tuple:
    """Sort key for cost history entries (seq, cost, supplier, day, items): invoices ahead of sheet rows, and
    among invoices the latest invoice DATE wins (an older invoice can be uploaded after a newer one)."""
    inv = h[0] >= INVOICE_SEQ
    return (inv, (h[3] or "") if inv else "", h[0])


def order_key(raw) -> str:
    return re.sub(r"[\s#]", "", str(raw or "")).upper()


def tag_of(key: str):
    """("AS", "") for AS13079, ("", "CH") for 15914CH; None if it isn't an order number."""
    m = ORDER_RE.match(key or "")
    return (m.group(1), m.group(3)) if m else None


def norm(name: str) -> str:
    s = (name or "").lower().replace("™", "").replace("️", "")
    s = re.sub(r"[^\w/&+.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def base_name(name: str) -> str:
    """The product without its variant: "Kaia sneakers - White / 9" -> "Kaia sneakers".
    Titles can contain " - " themselves ("Beth - Orthopedic Sandals - 9 / White"), so
    only the last part is dropped, and only when it looks like a variant (has a "/"
    or is short, like "Black" or "XL")."""
    parts = (name or "").rsplit(" - ", 1)
    if len(parts) == 2 and ("/" in parts[1] or len(parts[1].strip()) <= 20):
        # sizes with a dash of their own: "Shoes - Black / Women 9 - Men 8" -> "Shoes"
        more = parts[0].rsplit(" - ", 1)
        if "/" not in parts[1] and len(more) == 2 and "/" in more[1]:
            return norm(more[0])
        return norm(parts[0])
    return norm(name)


def title_match(title: str, product: str) -> float:
    """How well an invoice title (no variant) matches a product line ("title - variant")."""
    t, p = norm(title), norm(product)
    if not t or not p:
        return 0.0
    if p.startswith(t):
        return 1.0
    return max(SequenceMatcher(None, t, p[:len(t) + 2]).ratio(),
               SequenceMatcher(None, t, base_name(product)).ratio())


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _date(v):
    s = str(v or "").strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _week_no(title: str) -> int:
    m = re.search(r"(\d+)", title)
    if "april" in title.lower():
        return 0
    return int(m.group(1)) if m else -1


def parse_sheet(tabs: dict) -> list:
    """Every product row of the orders sheet, oldest tab first.
    [{seq, tab, day, order, product, subtotal, cog, supplier, payment, fulfillment}]"""
    rows, seq = [], 0
    for title in sorted(tabs, key=_week_no):
        if SKIP_TABS.search(title.strip("✅ ").strip()) or _week_no(title) < 0:
            continue
        values = tabs[title]
        if not values:
            continue
        head = [str(h).strip().lower() for h in values[0]]

        def col(*names):
            for n in names:
                if n in head:
                    return head.index(n)
            return None
        c_order, c_prod = col("order number"), col("product name")
        c_sub, c_cog = col("subtotal"), col("cog")
        c_sup, c_pay, c_ful = col("supplier"), col("payment status"), col("fulfillment")
        if c_order is None or c_prod is None or c_cog is None:
            continue
        day, order = None, None
        for v in values[1:]:
            cell = lambda i: v[i] if i is not None and i < len(v) else ""
            d = _date(cell(0))
            if d:
                day = d
            key = order_key(cell(c_order))
            if key:
                order = key
            product = str(cell(c_prod) or "").strip()
            if not product or not order:
                continue
            seq += 1
            rows.append({
                "seq": seq, "tab": title, "day": day, "order": order, "product": product,
                "subtotal": _num(cell(c_sub)), "cog": _num(cell(c_cog)),
                "supplier": str(cell(c_sup) or "").strip(),
                "payment": str(cell(c_pay) or "").strip(),
                "fulfillment": str(cell(c_ful) or "").strip(),
            })
    return rows


def store_tags(order_names: dict) -> dict:
    """{store_id: [order names]} -> {(prefix, suffix): store_id}."""
    tags = {}
    for sid, names in order_names.items():
        counts = defaultdict(int)
        for n in names:
            t = tag_of(order_key(n))
            if t:
                counts[t] += 1
        if counts:
            tags[max(counts, key=counts.get)] = sid
    return tags


class Catalog:
    """Per store: every product's cost history from the sheet."""

    def __init__(self, rows: list, tags: dict, order_items: dict = None):
        """order_items: {(store id, order key): items in the Shopify order}. A sheet row can cover two items
        ("x2", or one row for a pair), so the order's real item count decides whether a price is a single."""
        self.by_order = defaultdict(list)            # (store, order) -> rows
        self.exact = defaultdict(list)               # (store, product) -> [(seq, cog, supplier, day)]
        self.base = defaultdict(list)                # (store, base product) -> [...]
        self.store_cog = defaultdict(float)
        self.store_sales = defaultdict(float)
        self.unmatched_tags = defaultdict(int)
        self.inv_by_order = defaultdict(list)        # (store, order) -> uploaded invoice lines
        self.vetted, self.policy, self.per_item = {}, {}, {}   # vetted supplier prices (vetting.py), loaded below
        try:
            import vetting
            import db as _db
            self.vetted = vetting.load()
            for sid in {k[0] for k in self.vetted}:
                self.policy[sid] = vetting.policy(sid)
                self.per_item[sid] = float(_db.get_setting(f"supplier_per_item:{sid}") or 0)
        except Exception as e:                       # costing must never stop over this
            print(f"Vetted prices not loaded: {e}", flush=True)
        for r in rows:
            t = tag_of(r["order"])
            sid = tags.get(t) if t else None
            if sid is None:
                self.unmatched_tags[t[0] + "…" + t[1] if t else "?"] += 1
                continue
            r["store"] = sid
            self.by_order[(sid, r["order"])].append(r)
        # Suppliers give a discount when an order has several items (1 pair $16.30, 2 pairs $13 each,
        # or two different products), so every price is kept with the order's item count (one sheet
        # row per item). Only prices from 1-item orders are "single" prices.
        self._rates = {}
        order_items = order_items or {}
        for (sid, order), rs in self.by_order.items():
            n = max(len(rs), int(order_items.get((sid, order_key(order))) or 0))
            for r in rs:
                if r["cog"] and r["cog"] > 0:
                    entry = (r["seq"], r["cog"], r["supplier"], r["day"], n)
                    self.exact[(sid, norm(r["product"]))].append(entry)
                    self.base[(sid, base_name(r["product"]))].append(entry)
                    if r["subtotal"] and r["subtotal"] > 0:
                        self.store_cog[sid] += r["cog"]
                        self.store_sales[sid] += r["subtotal"]

    def add_invoices(self, lines: list):
        """Uploaded invoice lines (db.invoice_lines with number/date/supplier).
        They are this order's real cost, ahead of the sheet. A changed price only
        becomes the product's cost for other orders once someone approves it."""
        for l in lines:
            if not l.get("store_id") or not l.get("product") or l["status"] in ("unmatched", "duplicate", "superseded"):
                continue
            if l.get("ai_check") == "review":            # the AI read this invoice differently: wait for a person
                continue
            # "kept": the supplier said the higher price was a mistake, so this order costs the old price
            units = l.get("units") or 1.0
            charged = l["previous"] * units if l.get("check_state") == "kept" and l.get("previous") is not None \
                else l["amount"]
            entry = {"order": l["order_key"], "product": l["product"], "cog": charged, "units": units,
                     "supplier": l.get("supplier") or "", "day": l.get("date"),
                     "invoice": l.get("number") or "invoice"}
            self.inv_by_order[(l["store_id"], l["order_key"])].append(entry)
            if l["status"] != "changed" or l.get("approved_at"):
                hist = (10 ** 9 + int(l["invoice_id"]) * 1000 + int(l["line_no"]),
                        l["amount"] / units, entry["supplier"], entry["day"],   # price per item
                        int(l.get("tier") or round(units)))                     # items in the order
                self.exact[(l["store_id"], norm(l["product"]))].append(hist)
                self.base[(l["store_id"], base_name(l["product"]))].append(hist)

    def latest_for(self, store, product, tier):
        """Latest price per item for this bundle size (how many in the order), or None."""
        for hist in (self.exact.get((store, norm(product))), self.base.get((store, base_name(product)))):
            same = [h for h in hist or [] if len(h) > 4 and h[4] == tier]
            if same:
                return max(same, key=recency)
        return None

    def single(self, store, product):
        """Latest price per item from an order with just this one item, or None."""
        return self.latest_for(store, product, 1)

    def bundle_rate(self, store, items: int) -> tuple:
        """(usual discount off single prices for an order of `items` items, orders it's learned from).
        Learned from the last 60 costed bundle orders of that size in the store (sheet rows and
        invoices) whose every item has a single price; then any bundle size in the store; then 0."""
        if store not in self._rates:
            samples = defaultdict(list)                          # size -> [(day, discount)]
            orders = {}
            for (sid, order), rs in self.by_order.items():
                if sid == store and len(rs) >= 2 and all(r["cog"] and r["cog"] > 0 for r in rs):
                    orders[order] = (rs[0]["day"] or "", [(r["product"], 1, r["cog"]) for r in rs])
            for (sid, order), es in self.inv_by_order.items():     # invoices win over the sheet
                if sid == store and sum(e.get("units") or 1 for e in es) >= 2:
                    orders[order] = (es[0]["day"] or "", [(e["product"], e.get("units") or 1, e["cog"]) for e in es])
            for day, its in orders.values():
                singles = [self.single(store, p) for p, _, _ in its]
                if any(x is None for x in singles):
                    continue
                full = sum(x[1] * u for x, (_, u, _) in zip(singles, its))
                paid = sum(c for _, _, c in its)
                if full > 0:
                    n = int(round(sum(u for _, u, _ in its)))
                    samples[min(n, 4)].append((str(day), 1 - paid / full))
            self._rates[store] = samples
        samples = self._rates[store]

        def median(xs):
            xs = sorted(d for _, d in sorted(xs)[-60:])
            return xs[len(xs) // 2] if xs else None
        mine = samples.get(min(items, 4)) or []
        if len(mine) >= 3:
            return max(0.0, median(mine)), len(mine)
        every = [x for v in samples.values() for x in v]
        if len(every) >= 3:
            return max(0.0, median(every)), len(every)
        return 0.0, 0

    def latest(self, store, product):
        hist = self.exact.get((store, norm(product)))
        if hist:
            return max(hist, key=recency)
        hist = self.base.get((store, base_name(product)))
        return max(hist, key=recency) if hist else None

    def cog_pct(self, store, usd_to_store: float):
        """Store's average COG as a share of sales (sales in store currency)."""
        if not self.store_sales.get(store):
            return None
        return self.store_cog[store] * usd_to_store / self.store_sales[store]

    def products(self, store):
        """Every product with its latest cost and whether the cost has changed."""
        out = []
        for (sid, name), hist in self.exact.items():
            if sid != store:
                continue
            hist = sorted(hist, key=recency)
            costs = [h[1] for h in hist]
            out.append({"product": name, "cost": hist[-1][1], "supplier": hist[-1][2],
                        "last_day": hist[-1][3], "times": len(hist),
                        "low": min(costs), "high": max(costs),
                        "changed": len(set(round(c, 2) for c in costs)) > 1})
        return out


def _similar(a, b) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def cost_order(order: dict, store, catalog: Catalog, usd_to_store: float) -> list:
    """Cost every product line of one Shopify order. Costs in USD.
    Returns the order's lines with cost, source and supplier added."""
    key = order_key(order["name"])
    rows = [r for r in catalog.by_order.get((store, key), [])]
    free = list(rows)
    inv_free = list(catalog.inv_by_order.get((store, key), []))
    pct = catalog.cog_pct(store, usd_to_store)
    label = lambda x: f"{x['title']} - {x['variant']}" if x["variant"] else x["title"]
    items = 0 if order["cancelled"] else sum(max(x["quantity"], 1) * pack(label(x)) for x in order["lines"])
    out = []
    # Vetted prices (owner, 23 Oct 2026): what the supplier should charge is the cost, invoices or not.
    vet_cost = {}
    if catalog.vetted and not order["cancelled"]:
        import vetting
        pid_ = lambda x: str(x.get("product_id") or "").rsplit("/", 1)[-1]
        mine = [(pid_(x), max(x["quantity"], 1)) for x in order["lines"] if pid_(x)]
        distinct = {p for p, _ in mine}
        vet_items = [(p, q) for p, q in mine if catalog.vetted.get((store, p))]
        if vet_items:
            mode = catalog.policy.get(store, "per_item")
            by = defaultdict(int)
            for p, q in vet_items:
                by[p] += q
            for p, q in by.items():
                t = catalog.vetted[(store, p)]
                c = vetting.pieces_cost(t, q) if (mode == "tiered" or str(q) in t) else float(t["1"]) * q
                if mode == "per_item" and len(distinct) >= 2:
                    c -= catalog.per_item.get(store, 0.0) * q
                vet_cost[p] = (max(c or 0.0, 0.0), q, mode)
    for li in order["lines"]:
        name = label(li)
        qty = max(li["quantity"], 1) * pack(name)          # BOGO: one line, two pairs from the supplier
        vp = str(li.get("product_id") or "").rsplit("/", 1)[-1]
        if vp in vet_cost:
            total, q_all, mode = vet_cost[vp]
            share = total * max(li["quantity"], 1) / q_all          # lines of the same product share its cost
            out.append({**li, "product": name, "cost": share, "source": "vetted", "supplier": "",
                        "note": f"Vetted price, Buy {q_all}" + (" (tiered)" if mode == "tiered" else "")})
            continue
        line = {**li, "product": name, "cost": None, "source": None, "supplier": "", "note": ""}
        one = catalog.single(store, name)
        if items >= 2 and one:
            line["single"] = one[1] * qty                       # what it would cost bought on its own
        if order["cancelled"]:
            line.update(cost=0.0, source="cancelled", note="Order cancelled")
            out.append(line)
            continue
        # 0. uploaded supplier invoice lines for this exact product, up to the quantity
        got = [e for e in inv_free if norm(e["product"]) == norm(name)][:qty]
        if got:
            for e in got:
                inv_free.remove(e)
            line.update(cost=sum(e["cog"] for e in got), source="invoice", supplier=got[0]["supplier"],
                        note=f"Invoice {got[0]['invoice']}" + (f" ({len(got)} of {qty} items)" if len(got) < qty else ""))
            out.append(line)
            continue
        # 1. this order's own sheet rows (best name match first), up to the quantity
        picked = sorted(free, key=lambda r: -_similar(r["product"], name))
        picked = [r for r in picked if _similar(r["product"], name) >= 0.6][:qty]
        costed = [r for r in picked if r["cog"] is not None and r["cog"] > 0]
        if picked and len(costed) == len(picked):
            for r in picked:
                free.remove(r)
            line.update(cost=sum(r["cog"] for r in picked), source="invoice",
                        supplier=picked[0]["supplier"])
            if len(picked) < qty:
                # e.g. one row with "x2": a single row may already cover both items
                line["note"] = f"{len(picked)} sheet row(s) for {qty} items"
            out.append(line)
            continue
        # 2. not invoiced yet. One item: its single price. Several items (any products): single
        #    prices less the store's usual discount for that many items.
        if items >= 2 and one:
            rate, seen = catalog.bundle_rate(store, items)
            line.update(cost=one[1] * qty * (1 - rate), source="catalog", supplier=one[2],
                        note=f"Single price {one[1]:.2f} USD" + (
                            f", less the usual {items if items < 4 else '4+'}-item discount {rate * 100:.1f}%"
                            f" (from {seen} orders)" if rate else ", no bundle discount seen yet"))
            out.append(line)
            continue
        known = one or catalog.latest(store, name)
        if known:
            line.update(cost=known[1] * qty, source="catalog", supplier=known[2],
                        note=f"Last cost {known[1]:.2f} USD ({known[3] or 'earlier'})"
                             + ("" if len(known) < 5 or known[4] == 1 else f", from a {known[4]}-item order"))
            out.append(line)
            continue
        # 3. never bought: the store's average COG %
        if pct is not None:
            # A free replacement or a shipping-only order still ships the item: estimate from its normal price
            # (before discounts) when the customer paid much less than that, never from $0.
            paid, normal = li.get("subtotal") or 0.0, li.get("original") or 0.0
            base = normal if normal > 0 and paid < 0.5 * normal else paid
            line.update(cost=base * pct / usd_to_store if usd_to_store else None, source="estimate",
                        note=f"No cost history; store average {pct * 100:.1f}% of "
                             + (f"its normal price {normal:.2f} (customer paid {paid:.2f})" if base == normal and paid < normal else "sales"))
        else:
            line.update(source="estimate", note="No cost history for this store yet")
        out.append(line)
    return out
