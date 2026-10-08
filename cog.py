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

    def __init__(self, rows: list, tags: dict):
        self.by_order = defaultdict(list)            # (store, order) -> rows
        self.exact = defaultdict(list)               # (store, product) -> [(seq, cog, supplier, day)]
        self.base = defaultdict(list)                # (store, base product) -> [...]
        self.store_cog = defaultdict(float)
        self.store_sales = defaultdict(float)
        self.unmatched_tags = defaultdict(int)
        self.inv_by_order = defaultdict(list)        # (store, order) -> uploaded invoice lines
        for r in rows:
            t = tag_of(r["order"])
            sid = tags.get(t) if t else None
            if sid is None:
                self.unmatched_tags[t[0] + "…" + t[1] if t else "?"] += 1
                continue
            r["store"] = sid
            self.by_order[(sid, r["order"])].append(r)
            if r["cog"] and r["cog"] > 0:
                entry = (r["seq"], r["cog"], r["supplier"], r["day"])
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
            if not l.get("store_id") or not l.get("product") or l["status"] in ("unmatched", "duplicate"):
                continue
            entry = {"order": l["order_key"], "product": l["product"], "cog": l["amount"],
                     "supplier": l.get("supplier") or "", "day": l.get("date"),
                     "invoice": l.get("number") or "invoice"}
            self.inv_by_order[(l["store_id"], l["order_key"])].append(entry)
            if l["status"] != "changed" or l.get("approved_at"):
                hist = (10 ** 9 + int(l["invoice_id"]) * 1000 + int(l["line_no"]),
                        l["amount"], entry["supplier"], entry["day"])
                self.exact[(l["store_id"], norm(l["product"]))].append(hist)
                self.base[(l["store_id"], base_name(l["product"]))].append(hist)

    def latest(self, store, product):
        hist = self.exact.get((store, norm(product)))
        if hist:
            return max(hist)
        hist = self.base.get((store, base_name(product)))
        return max(hist) if hist else None

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
            hist = sorted(hist)
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
    out = []
    for li in order["lines"]:
        name = f"{li['title']} - {li['variant']}" if li["variant"] else li["title"]
        qty = max(li["quantity"], 1)
        line = {**li, "product": name, "cost": None, "source": None, "supplier": "", "note": ""}
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
        # 2. the product's latest known cost in this store
        known = catalog.latest(store, name)
        if known:
            line.update(cost=known[1] * qty, source="catalog", supplier=known[2],
                        note=f"Last cost {known[1]:.2f} USD ({known[3] or 'earlier'})")
            out.append(line)
            continue
        # 3. never bought: the store's average COG %
        if pct is not None:
            line.update(cost=li["subtotal"] * pct / usd_to_store if usd_to_store else None,
                        source="estimate", note=f"No cost history; store average {pct * 100:.1f}% of sales")
        else:
            line.update(source="estimate", note="No cost history for this store yet")
        out.append(line)
    return out
