"""Vetted supplier prices (COG → Products, 23 Oct 2026).

Per store and product, admins / Work-level staff set what the supplier SHOULD charge, as the total for a number
of PIECES (what the supplier ships, USD incl. shipping):
- single items ("qty" offer): Buy 1, Buy 2, Buy 3, Buy 4 pieces;
- bundles ("bundle" offer, e.g. a Buy 1 Get 1 product sold only in packs of 2): Buy 2, 4, 6 ... pieces.
An order line's pieces = its quantity × cog.pack(name) (a BOGO sale is 2 pieces).

Every price has a "from" date (default the day it's set; can be back-dated): it costs the orders placed on or
after that day (cog.cost_order, source "vetted"), until a newer price takes over, so a supplier price change only
counts from when it applies. Older orders keep the earlier price (or their invoice / last price when none).

Dayone offers two discount policies and a store has one of them (setting `supplier_policy:<store id>`):
- "tiered"    (CUTEHOME): several pieces of the SAME product cost less per piece (the totals above).
- "per_item"  (every other store): several DIFFERENT products in one parcel get US$1-1.50 off each piece,
              learned from the store's invoices (learn()).
learn() also suggests prices from invoices and lists invoiced orders whose charge doesn't match the vetted price
for that order's date (the price-check sheet), from each store's checks start (CUTEHOME: 17 Oct 2026, the
switch to Dayone).
"""
import json
import math
from collections import defaultdict

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS pd_vet_prices (
    store_id   INTEGER NOT NULL,
    product_id TEXT    NOT NULL,
    from_day   TEXT    NOT NULL,        -- store day this price applies from (YYYY-MM-DD)
    tiers      TEXT    NOT NULL,        -- JSON {"pieces": usd total}, e.g. {"1": 14.5, "2": 25.1} or {"2": 16.3, "4": 30}
    offer      TEXT,                    -- JSON {"type": "qty" | "bundle", "pack": 2}
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, product_id, from_day)
);
CREATE TABLE IF NOT EXISTS pd_flag_marks (
    store_id   INTEGER NOT NULL,
    order_key  TEXT    NOT NULL,
    state      TEXT    NOT NULL,        -- escalated | refunded (supplier paid the difference back) | ok
    note       TEXT,
    by         TEXT,
    at         TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, order_key)
);
"""

TARGET_COG = 0.29                     # the owner's target: supplier cost = 29% of the selling price
DEFAULT_POLICY = {6: "tiered"}        # CUTEHOME bundles the same product (tiered); the rest are per item
DEFAULT_CHECKS_FROM = {6: "2026-10-17"}   # CUTEHOME moved to Dayone on 17 Oct 2026
TOLERANCE = 0.10                      # USD: a smaller gap between invoice and vetted price isn't a price change
DROP = ("unmatched", "duplicate", "superseded")
MAX_PIECES = 12


def init():
    with db._conn() as con:
        con.executescript(SCHEMA)


def policy(store_id: int) -> str:
    v = (db.get_setting(f"supplier_policy:{store_id}") or "").strip()
    return v if v in ("tiered", "per_item") else DEFAULT_POLICY.get(int(store_id), "per_item")


def checks_from(store_id: int) -> str:
    """Invoices of orders before this store day aren't price-checked (e.g. CUTEHOME before the switch to Dayone)."""
    return (db.get_setting(f"price_checks_from:{store_id}") or DEFAULT_CHECKS_FROM.get(int(store_id), "")).strip()


def clean_tiers(raw) -> dict:
    """{"pieces": usd} with positive numbers for 1-12 pieces."""
    out = {}
    for k, v in (raw or {}).items():
        try:
            n, v = int(k), round(float(v), 2)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= MAX_PIECES and v > 0:
            out[str(n)] = v
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def clean_offer(raw, tiers: dict) -> dict:
    t = (raw or {}).get("type")
    try:
        pack = max(1, min(int((raw or {}).get("pack") or 1), 6))
    except (TypeError, ValueError):
        pack = 1
    if t != "bundle":
        return {"type": "qty", "pack": 1}
    return {"type": "bundle", "pack": pack if pack > 1 else (min(int(k) for k in tiers) if tiers else 2)}


def load() -> dict:
    """{(store id, product id): [versions oldest first]}, each {"from", "tiers", "offer", "by", "at"}."""
    init()
    out = defaultdict(list)
    with db._conn() as con:
        for r in con.execute("SELECT store_id, product_id, from_day, tiers, offer, by, at FROM pd_vet_prices"
                             " ORDER BY from_day, at"):
            out[(r[0], r[1])].append({"from": r[2], "tiers": json.loads(r[3] or "{}"),
                                      "offer": json.loads(r[4] or "{}") or {"type": "qty", "pack": 1},
                                      "by": r[5], "at": r[6]})
    return dict(out)


def active(versions: list, day: str):
    """The version that applies on this store day (newest from-date on or before it), or None."""
    best = None
    for v in versions or []:
        if (v["from"] or "") <= (day or "9999"):
            best = v
    return best


def latest(versions: list):
    return versions[-1] if versions else None


def save(store_id: int, product_id: str, tiers: dict, offer: dict, from_day: str, by: str) -> dict:
    tiers = clean_tiers(tiers)
    offer = clean_offer(offer, tiers)
    init()
    with db._conn() as con:
        if tiers:
            con.execute("INSERT INTO pd_vet_prices (store_id, product_id, from_day, tiers, offer, by, at)"
                        " VALUES (?,?,?,?,?,?,datetime('now')) ON CONFLICT(store_id, product_id, from_day)"
                        " DO UPDATE SET tiers = excluded.tiers, offer = excluded.offer, by = excluded.by, at = excluded.at",
                        (store_id, product_id, from_day, json.dumps(tiers), json.dumps(offer), by))
        else:
            con.execute("DELETE FROM pd_vet_prices WHERE store_id = ? AND product_id = ? AND from_day = ?",
                        (store_id, product_id, from_day))
    return {"tiers": tiers, "offer": offer, "from": from_day}


def base(tiers: dict):
    """(pieces, usd) of the smallest offer: one sale of a bundle product, one piece otherwise."""
    if not tiers:
        return None
    k = min(int(x) for x in tiers)
    return k, float(tiers[str(k)])


def pieces_cost(tiers: dict, q: int):
    """What q pieces cost at these prices: the exact offer, else the nearest smaller offer plus the last step per
    extra piece (below the smallest offer: its price per piece)."""
    t = {int(k): float(v) for k, v in (tiers or {}).items()}
    if not t:
        return None
    q = max(int(q or 1), 1)
    if q in t:
        return t[q]
    ks = sorted(t)
    if q < ks[0]:
        return t[ks[0]] / ks[0] * q
    below = [k for k in ks if k <= q]
    top = below[-1]
    step = (t[top] - t[below[-2]]) / (top - below[-2]) if len(below) >= 2 else t[top] / top
    return t[top] + step * (q - top)


def product_cost(mode: str, tiers: dict, pieces: int, distinct: int, per_item: float) -> float:
    """One product's cost in one order: tiered stores (or an exact offer) use the offers; per-item stores price
    each piece and take the per-item discount off when different products ship together."""
    if mode == "tiered" or str(pieces) in tiers:
        c = pieces_cost(tiers, pieces)
    else:
        k, p = base(tiers)
        c = p / k * pieces
    if mode == "per_item" and distinct >= 2:
        c -= per_item * pieces
    return max(c or 0.0, 0.0)


def learn(lines: list, inv_lines: list, vetted: dict, policies: dict, starts: dict) -> dict:
    """From saved order lines (cog_lines: store_id, order_name, product, product_id, quantity, cancelled, day) and
    invoice lines: per store the learned per-item discount, per product prices suggested by invoices (by pieces), and
    the invoiced orders whose charge doesn't match the vetted price of their day (the price-check sheet)."""
    from cog import order_key, pack
    from products_desk import pid_of
    orders = defaultdict(lambda: defaultdict(int))      # (store, order key) -> {pid: pieces}
    names, days = {}, {}
    for l in lines:
        if l.get("cancelled") or not l.get("product_id"):
            continue
        k = (l["store_id"], order_key(l["order_name"]))
        orders[k][pid_of(l["product_id"])] += max(int(l.get("quantity") or 1), 1) * pack(l.get("product") or "")
        names[k], days[k] = l["order_name"], l.get("day")
    charged, inv = defaultdict(float), {}
    for l in inv_lines:
        if l.get("status") in DROP or not l.get("store_id") or l.get("ai_check") == "review":
            continue
        k = (l["store_id"], order_key(l["order_key"]))
        charged[k] += float(l.get("amount") or 0)
        inv.setdefault(k, {"invoice_id": l.get("invoice_id"), "number": l.get("number"), "date": l.get("date"),
                           "supplier": l.get("supplier")})
    # suggested prices: the latest invoice (from the store's checks start) of an order with only this product
    sug = defaultdict(dict)                             # (store, pid) -> {pieces: (date, charge)}
    for k, its in orders.items():
        if k not in charged or len(its) != 1 or (days.get(k) or "") < starts.get(k[0], ""):
            continue
        (pid, q), = its.items()
        if q > MAX_PIECES:
            continue
        d = (inv[k].get("date") or days.get(k) or "")
        cur = sug[(k[0], pid)].get(q)
        if not cur or d > cur[0]:
            sug[(k[0], pid)][q] = (d, round(charged[k], 2))
    suggested = {key: {str(q): v[1] for q, v in sorted(t.items())} for key, t in sug.items()}

    def tiers_on(sid, pid, day):
        v = active(vetted.get((sid, pid)), day)
        return v["tiers"] if v else None
    # per-item discount (per_item stores): every piece at its own price less what was charged, per piece
    per_item = {}
    for sid in {k[0] for k in orders}:
        samples = []
        for k, its in orders.items():
            if k[0] != sid or k not in charged or len(its) < 2 or (days.get(k) or "") < starts.get(sid, ""):
                continue
            unit = []
            for p in its:
                t = tiers_on(sid, p, days.get(k)) or suggested.get((sid, p))
                b = base(t) if t else None
                unit.append(b[1] / b[0] if b else None)
            if any(u is None for u in unit):
                continue
            n = sum(its.values())
            full = sum(u * q for u, q in zip(unit, its.values()))
            samples.append((inv[k].get("date") or "", (full - charged[k]) / n))
        samples = [d for _, d in sorted(samples)[-30:] if -0.5 <= d <= 5]
        samples.sort()
        per_item[sid] = round(samples[len(samples) // 2], 2) if samples else 0.0
    # price checks: invoiced orders (from the store's checks start) whose every product has a vetted price for
    # the order's day, and the charge differs
    flags = []
    for k, its in orders.items():
        sid, day = k[0], days.get(k) or ""
        if k not in charged or day < starts.get(sid, ""):
            continue
        ts = {p: tiers_on(sid, p, day) for p in its}
        if any(not t for t in ts.values()):
            continue
        mode = policies.get(sid, "per_item")
        exp = sum(product_cost(mode, ts[p], q, len(its), per_item.get(sid, 0.0)) for p, q in its.items())
        gap = round(charged[k] - exp, 2)
        if abs(gap) > TOLERANCE:
            flags.append({"store_id": sid, "order": names[k], "order_key": k[1], "day": day,
                          "products": list(its), "pieces": sum(its.values()), "charged": round(charged[k], 2),
                          "expected": round(exp, 2), "gap": gap, "invoice": inv[k]})
    flags.sort(key=lambda f: (f["invoice"].get("date") or "", f["order"]), reverse=True)
    return {"per_item": per_item, "suggested": suggested, "flags": flags}


def round95(x: float) -> float:
    """Selling prices end in .95 (owner): the nearest price ending .95 (52.31 -> 51.95, 52.6 -> 52.95)."""
    lo = math.floor(x - 0.95) + 0.95
    hi = lo + 1
    return round(hi if hi - x < x - lo else lo, 2)


def recommend(cost_store: float, price: float) -> dict:
    """COG % of the selling price for this cost, and the price that hits the 29% target (ending .95)."""
    if not cost_store or not price:
        return {}
    rec = round95(cost_store / TARGET_COG)
    return {"cog_pct": round(cost_store / price * 100, 1), "recommended": rec, "change": round(rec - price, 2)}
