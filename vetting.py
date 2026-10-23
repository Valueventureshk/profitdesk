"""Vetted supplier prices (COG → Products, 23 Oct 2026).

Per store and product, admins / Work-level staff set what the supplier SHOULD charge: Buy 1, Buy 2, Buy 3,
Buy 4 (the total for that many pieces of the product, USD incl. shipping). Once set, that is the cost every
order of the product gets (cog.cost_order, source "vetted"), so the dashboard shows what we should pay.

Dayone offers two discount policies and a store has one of them (setting `supplier_policy:<store id>`):
- "tiered"    (CUTEHOME): several pieces of the SAME product cost less per piece (Buy 2, Buy 3 ... totals).
- "per_item"  (every other store): several DIFFERENT products in one parcel get US$1-1.50 off each item.
              The amount is learned from the store's invoices (learn()).
learn() also suggests Buy 1-4 prices from invoices and flags invoiced orders whose charge doesn't match the
vetted prices once the policy's discount is allowed for: those go on the price-check sheet to raise with
the supplier.
"""
import json
from collections import defaultdict

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS pd_vetted (
    store_id   INTEGER NOT NULL,
    product_id TEXT    NOT NULL,
    tiers      TEXT    NOT NULL,        -- JSON {"1": usd, "2": usd, "3": usd, "4": usd}: total for N pieces
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, product_id)
);
CREATE TABLE IF NOT EXISTS pd_vetted_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    product_id TEXT    NOT NULL,
    tiers      TEXT,
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS pd_flag_marks (
    store_id   INTEGER NOT NULL,
    order_key  TEXT    NOT NULL,
    state      TEXT    NOT NULL,        -- escalated | ok (checked, nothing to do)
    note       TEXT,
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, order_key)
);
"""

TARGET_COG = 0.29            # the owner's target: supplier cost = 29% of the selling price
DEFAULT_POLICY = {6: "tiered"}   # CUTEHOME bundles the same product (tiered); the rest are per item
TOLERANCE = 0.10             # USD: a smaller gap between invoice and vetted price isn't a price change
DROP = ("unmatched", "duplicate", "superseded")


def init():
    with db._conn() as con:
        con.executescript(SCHEMA)


def policy(store_id: int) -> str:
    v = (db.get_setting(f"supplier_policy:{store_id}") or "").strip()
    return v if v in ("tiered", "per_item") else DEFAULT_POLICY.get(int(store_id), "per_item")


def clean_tiers(raw) -> dict:
    """{"1": 14.5, "2": 25.1, ...} with only positive numbers for 1-4 pieces."""
    out = {}
    for k in ("1", "2", "3", "4"):
        v = (raw or {}).get(k, (raw or {}).get(int(k)))
        try:
            v = round(float(v), 2)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[k] = v
    return out


def load() -> dict:
    """{(store id, product id): tiers}."""
    init()
    with db._conn() as con:
        return {(r[0], r[1]): json.loads(r[2] or "{}") for r in con.execute("SELECT store_id, product_id, tiers FROM pd_vetted")}


def save(store_id: int, product_id: str, tiers: dict, by: str) -> dict:
    tiers = clean_tiers(tiers)
    init()
    with db._conn() as con:
        if tiers:
            con.execute("INSERT INTO pd_vetted (store_id, product_id, tiers, by, at) VALUES (?,?,?,?,datetime('now'))"
                        " ON CONFLICT(store_id, product_id) DO UPDATE SET tiers = excluded.tiers, by = excluded.by,"
                        " at = excluded.at", (store_id, product_id, json.dumps(tiers), by))
        else:
            con.execute("DELETE FROM pd_vetted WHERE store_id = ? AND product_id = ?", (store_id, product_id))
        con.execute("INSERT INTO pd_vetted_log (store_id, product_id, tiers, by) VALUES (?,?,?,?)",
                    (store_id, product_id, json.dumps(tiers), by))
    return tiers


def pieces_cost(tiers: dict, q: int):
    """What q pieces of one product cost at its vetted prices (beyond 4: Buy 4 + the last step per extra piece)."""
    q = max(int(q or 1), 1)
    t = {int(k): float(v) for k, v in (tiers or {}).items()}
    if not t.get(1):
        return None
    if q in t:
        return t[q]
    known = sorted(k for k in t if k <= q)
    top = known[-1]
    step = (t[top] - t[known[-2]]) / (top - known[-2]) if len(known) >= 2 else t[1]
    return t[top] + step * (q - top)


def order_cost(mode: str, items: list, vetted: dict, per_item: float = 0.0):
    """Expected supplier charge for one order. items: [(product id, pieces)]; vetted: {product id: tiers}.
    Returns (total, {product id: cost}) or (None, {}) when a product has no vetted price."""
    n = sum(q for _, q in items)
    by = defaultdict(int)
    for pid, q in items:
        by[pid] += q
    out = {}
    for pid, q in by.items():
        t = vetted.get(pid)
        if not t or not t.get("1"):
            return None, {}
        if mode == "tiered" or q >= 2 and str(q) in t:
            c = pieces_cost(t, q)                          # same product several times: its own tiers
        else:
            c = float(t["1"]) * q
        if mode == "per_item" and n >= 2 and len(by) >= 2:
            c -= per_item * q                              # different products in one parcel: off each item
        out[pid] = max(c, 0.0)
    return sum(out.values()), out


def learn(lines: list, inv_lines: list, vetted: dict, policies: dict) -> dict:
    """From saved order lines (cog_lines: store_id, order_name, product_id, quantity, cancelled, day) and invoice
    lines: per store the learned per-item discount, per product suggested Buy 1-4 prices, and the invoiced
    orders whose charge doesn't match the vetted prices (the price-check sheet)."""
    from cog import order_key
    from products_desk import pid_of
    orders = defaultdict(lambda: defaultdict(int))      # (store, order key) -> {pid: pieces}
    names, days = {}, {}
    for l in lines:
        if l.get("cancelled") or not l.get("product_id"):
            continue
        k = (l["store_id"], order_key(l["order_name"]))
        orders[k][pid_of(l["product_id"])] += max(int(l.get("quantity") or 1), 1)
        names[k], days[k] = l["order_name"], l.get("day")
    charged, inv = defaultdict(float), {}
    for l in inv_lines:
        if l.get("status") in DROP or not l.get("store_id") or l.get("ai_check") == "review":
            continue
        k = (l["store_id"], order_key(l["order_key"]))
        charged[k] += float(l.get("amount") or 0)
        inv.setdefault(k, {"invoice_id": l.get("invoice_id"), "number": l.get("number"), "date": l.get("date"),
                           "supplier": l.get("supplier")})
    # suggested tiers: the latest invoice for an order with only this product, per number of pieces
    sug = defaultdict(dict)                             # (store, pid) -> {q: (date, charge)}
    for k, its in orders.items():
        if k not in charged or len(its) != 1:
            continue
        (pid, q), = its.items()
        if q > 4:
            continue
        d = (inv[k].get("date") or days.get(k) or "")
        cur = sug[(k[0], pid)].get(q)
        if not cur or d > cur[0]:
            sug[(k[0], pid)][q] = (d, round(charged[k], 2))
    suggested = {key: {str(q): v[1] for q, v in sorted(t.items())} for key, t in sug.items()}
    # per-item discount (per_item stores): singles at Buy 1 less what was charged, per item, latest 30 orders
    per_item = {}
    for sid in {k[0] for k in orders}:
        samples = []
        for k, its in orders.items():
            if k[0] != sid or k not in charged or len(its) < 2:
                continue
            ones = [(vetted.get((sid, p)) or {}).get("1") or (suggested.get((sid, p)) or {}).get("1") for p in its]
            if any(not x for x in ones):
                continue
            n = sum(its.values())
            full = sum(float(o) * q for o, q in zip(ones, its.values()))
            samples.append((inv[k].get("date") or "", (full - charged[k]) / n))
        samples = [d for _, d in sorted(samples)[-30:] if -0.5 <= d <= 5]
        samples.sort()
        per_item[sid] = round(samples[len(samples) // 2], 2) if samples else 0.0
    # price checks: invoiced orders where every product is vetted and the charge differs
    flags = []
    for k, its in orders.items():
        if k not in charged:
            continue
        sid = k[0]
        v = {p: vetted.get((sid, p)) for p in its}
        if any(not t for t in v.values()):
            continue
        exp, _ = order_cost(policies.get(sid, "per_item"), list(its.items()), v, per_item.get(sid, 0.0))
        if exp is None:
            continue
        gap = round(charged[k] - exp, 2)
        if abs(gap) > TOLERANCE:
            flags.append({"store_id": sid, "order": names[k], "order_key": k[1], "day": days.get(k),
                          "products": list(its), "pieces": sum(its.values()), "charged": round(charged[k], 2),
                          "expected": round(exp, 2), "gap": gap, "invoice": inv[k]})
    flags.sort(key=lambda f: (f["invoice"].get("date") or "", f["order"]), reverse=True)
    return {"per_item": per_item, "suggested": suggested, "flags": flags}


def round95(x: float) -> float:
    """Selling prices end in .95 (owner): the nearest price ending .95 (52.31 -> 51.95, 52.6 -> 52.95)."""
    import math
    lo = math.floor(x - 0.95) + 0.95
    hi = lo + 1
    return round(hi if hi - x < x - lo else lo, 2)


def recommend(cost_store: float, price: float) -> dict:
    """COG % of the selling price for this cost, and the price that hits the 29% target (ending .95)."""
    if not cost_store or not price:
        return {}
    pct = cost_store / price
    rec = round95(cost_store / TARGET_COG)
    return {"cog_pct": round(pct * 100, 1), "recommended": rec, "change": round(rec - price, 2)}
