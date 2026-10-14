"""COG → Products: one row per Shopify product, for buying and quality decisions.

Sales, last order and the latest confirmed cost come from the costed order lines and the cost history
(cog.py); title, image and listed date from Shopify; size chart from the Size Charts rules. The team adds
the rest by hand: supplier (when it differs), material, quality-check photos and notes (complaints kept
apart from other notes, for the monthly product meeting).

CS tickets are matched to products automatically, best effort: by the order numbers the inbox AI read in
each ticket, else by the customer's latest order before the ticket (SCM parcels). When an order has
several products, words of the product titles found in the ticket's subject / summary pick the product;
if none do, the ticket counts for every product in that order.
"""
import re

import cog

SCHEMA = """
CREATE TABLE IF NOT EXISTS pd_info (
    store_id   INTEGER NOT NULL,
    product_id TEXT    NOT NULL,
    supplier   TEXT,
    material   TEXT,
    updated_by TEXT,
    updated_at TEXT,
    PRIMARY KEY (store_id, product_id)
);
CREATE TABLE IF NOT EXISTS pd_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    product_id TEXT    NOT NULL,
    kind       TEXT    NOT NULL DEFAULT 'note',      -- complaint | note
    text       TEXT    NOT NULL,
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS pd_notes_product ON pd_notes (store_id, product_id);
CREATE TABLE IF NOT EXISTS pd_qc (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    product_id TEXT    NOT NULL,
    caption    TEXT,
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS pd_qc_product ON pd_qc (store_id, product_id);
"""

TICKET_KINDS = ("support", "legal")          # customer problems (not pre-sale questions or spam)

# words that say nothing about which product a ticket is about
_STOP = set("""women womens woman men mens unisex with without for and the from your this that comfort comfortable
orthopedic orthopaedic orthopédique orthopédiques confort femme homme elegant everyday style stylish premium
classic size black white beige brown grey gray navy pair pairs set new free shipping order product item
relief pain support""".split())


def pid_of(gid) -> str:
    """'gid://shopify/Product/123' or '123' -> '123'."""
    return str(gid or "").rsplit("/", 1)[-1]


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-zà-ÿ]{4,}", (text or "").lower()) if w not in _STOP}


def match_tickets(tickets: list, order_products: dict, customer_orders: dict) -> dict:
    """{(store id, product id): [(ticket id, how)]}.
    tickets: rows with id, orders, subject, summary, customer_email, created_at.
    order_products: {order key: [(store id, product id, title)]}.
    customer_orders: {email: [(order date, order key)] oldest first}."""
    out = {}
    for t in tickets:
        keys = [cog.order_key(o) for o in (t.get("orders") or "").split(",") if o.strip()]
        keys = [k for k in dict.fromkeys(keys) if k in order_products]
        how = "order number"
        if not keys and t.get("customer_email"):
            when = (t.get("created_at") or "")[:10] or "9999"
            mine = [k for d, k in customer_orders.get(t["customer_email"].lower(), []) if (d or "")[:10] <= when
                    and k in order_products]
            if mine:
                keys, how = [mine[-1]], "customer's latest order"
        prods = list(dict.fromkeys(p for k in keys for p in order_products[k]))
        if len(prods) > 1:
            said = words(f"{t.get('subject') or ''} {t.get('summary') or ''}")
            hit = [p for p in prods if words(cog.base_name(p[2])) & said]
            if hit:
                prods = hit
        for sid, pid, _ in dict.fromkeys(prods):
            out.setdefault((sid, pid), []).append((t["id"], how))
    return out


def cost_history(catalog, store: int, names: list) -> dict:
    """Latest confirmed cost (from orders with one item: the price for one) and the price history, for a
    product's variant names. History entries: (seq, price, supplier, day, items in the order)."""
    hist = []
    for n in set(names):
        hist += [h for h in catalog.exact.get((store, cog.norm(n)), []) if len(h) > 4]
    hist.sort()
    singles = [h for h in hist if h[4] == 1]
    last = singles[-1] if singles else (hist[-1] if hist else None)
    per_variant = {}
    for n in set(names):
        s = [h for h in catalog.exact.get((store, cog.norm(n)), []) if len(h) > 4 and h[4] == 1]
        if s:
            per_variant[n] = max(s)[1]
    changes, prev = [], None
    for h in singles:                                  # one row each time the single price moved
        if prev is None or abs(h[1] - prev) > 0.005:
            changes.append({"day": h[3], "price": round(h[1], 2), "supplier": h[2]})
            prev = h[1]
    vals = sorted({round(v, 2) for v in per_variant.values()})
    return {
        "cost": round(last[1], 2) if last else None,
        "cost_day": last[3] if last else None,
        "cost_supplier": last[2] if last else "",
        "cost_is_bundle": bool(last and last[4] != 1),
        "variant_low": vals[0] if len(vals) > 1 else None,
        "variant_high": vals[-1] if len(vals) > 1 else None,
        "changes": changes,
        "variants": sorted(({"variant": n, "cost": round(v, 2)} for n, v in per_variant.items()), key=lambda x: x["variant"]),
    }
