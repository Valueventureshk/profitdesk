"""COG desk: Check orders (every order with what it cost and whether that cost needs a look) and
Invoices by date (which supplier invoices are paid, what's on credit).

Pure functions: app.py reads the tables and passes rows in. All costs in USD (suppliers bill in USD).

An order's "kind" says where its cost comes from and whether someone should look:
  cancel  cancelled, no cost
  up      an invoice line's price differs from the last price and nobody decided yet (also cheaper: "down")
  review  someone asked the supplier (checking)
  real    someone accepted the new price
  kept    the supplier corrected it: the old price is used
  bundle  someone said: it's a bundle / BOGO billed differently, not a price change
  note    someone explained it with a note
  new     first invoice for this product
  same    invoiced at the same price as last time
  inv     invoiced (nothing to compare with)
  est     not invoiced yet: the product's last price or the store's usual COG %
"""
from collections import defaultdict

CUTOFF_DEFAULT = "2026-09-01"     # owner: everything invoiced before 1 Sep 2026 is paid
DROP = ("unmatched", "duplicate", "superseded")


def invoice_paid(inv: dict, cutoff: str) -> bool:
    return (inv.get("date") or "") < cutoff or bool(inv.get("paid"))


def replaced(lines: list) -> bool:
    """Every line of the invoice was replaced by a corrected invoice."""
    return bool(lines) and all(l["status"] == "superseded" for l in lines)


def _change(l: dict):
    """(old, new) for a changed line, per item; bundles compare against their items at single prices."""
    units = l.get("units") or 1
    if (l.get("tier") or 1) >= 2 and l.get("singles"):
        return l["singles"], l["amount"]
    if l.get("previous") is None:
        return None, l["amount"] / units
    return l["previous"], l["amount"] / units


def order_kind(cancelled: bool, inv_lines: list, sources: set, mark: dict) -> tuple:
    """(kind, change %) for one order. inv_lines: its invoice lines that count (not unmatched/duplicate/superseded)."""
    if cancelled:
        return "cancel", None
    pct, direction = None, None
    open_change = False
    states = set()
    for l in inv_lines:
        if l["status"] != "changed":
            continue
        old, new = _change(l)
        if old:
            p = round((new - old) / old * 100)
            if pct is None or abs(p) > abs(pct):
                pct = p
        if l.get("approved_at"):
            states.add("real")
        elif l.get("check_state") in ("kept", "bundle", "checking"):
            states.add({"kept": "kept", "bundle": "bundle", "checking": "review"}[l["check_state"]])
        else:
            open_change = True
    if mark.get("answer") in ("bundle", "real", "review", "note"):     # the team answered this order
        return mark["answer"], pct
    if open_change:
        return ("up" if (pct or 0) >= 0 else "down"), pct
    for s in ("review", "bundle", "kept", "real"):
        if s in states:
            return s, pct
    if not inv_lines:
        return "est", None
    if any(l["status"] == "new" for l in inv_lines):
        return "new", None
    if all(l.get("previous") is not None for l in inv_lines):
        return "same", None
    return "inv", None


def order_rows(lines: list, inv_by_order: dict, invoices: dict, fulfilled: set, marks: dict,
               sale_usd, cutoff: str, stores: dict) -> list:
    """One row per order for Check orders.
    lines: cog_lines rows; inv_by_order: {(store, order key): [invoice lines]}; invoices: {id: invoice};
    fulfilled: {(store, order name)}; marks: {(store, order name): {answer, note, ...}};
    sale_usd(store_id, amount) -> USD; stores: {id: name}."""
    from cog import order_key
    orders = {}
    for r in lines:
        k = (r["store_id"], r["order_name"])
        o = orders.setdefault(k, {
            "store_id": r["store_id"], "store": stores.get(r["store_id"], "?"), "order": r["order_name"],
            "created": r["created"], "day": r["day"], "cancelled": bool(r["cancelled"]), "items": [],
            "sale": 0.0, "cog": 0.0, "singles": 0.0, "singles_known": True, "count": 0, "sources": set()})
        qty = r["quantity"] or 1
        o["items"].append({"product": r["product"], "quantity": qty, "cost": r["cost_usd"] or 0.0,
                           "single": r.get("single_usd"), "source": r["source"], "supplier": r["supplier"] or ""})
        o["sources"].add(r["source"])
        if not r["cancelled"]:
            o["sale"] += sale_usd(r["store_id"], r["subtotal"] or 0.0)
            o["cog"] += r["cost_usd"] or 0.0
            o["count"] += qty
            if r.get("single_usd") is None:
                o["singles_known"] = False
            else:
                o["singles"] += r["single_usd"]
    out = []
    for (sid, name), o in orders.items():
        inv_lines = [l for l in inv_by_order.get((sid, order_key(name)), []) if l["status"] not in DROP]
        mark = marks.get((sid, name)) or {}
        kind, pct = order_kind(o["cancelled"], inv_lines, o["sources"], mark)
        if kind == "est" and "catalog" not in o["sources"] and "invoice" not in o["sources"]:
            sub = "est_avg"          # store's usual COG %
        elif kind == "est":
            sub = "est_last"         # product's last price
        else:
            sub = kind
        ids = sorted({l["invoice_id"] for l in inv_lines})
        invs = [invoices[i] for i in ids if i in invoices]
        if o["cancelled"]:
            state = "cancelled"
        elif invs:
            state = "invoiced"
        elif (sid, name) in fulfilled:
            state = "notinv"
        else:
            state = "unfulfilled"
        bundle = None
        if o["count"] >= 2 and o["singles_known"] and o["singles"] > 0 and not o["cancelled"]:
            disc = round(o["singles"] - o["cog"], 2)
            if abs(disc) >= 0.01:
                bundle = disc
        out.append({
            "store_id": sid, "store": o["store"], "order": name, "created": o["created"], "day": o["day"],
            "items": [{**i, "shown": (i["single"] if bundle is not None and i["single"] is not None else i["cost"])}
                      for i in o["items"]],
            "count": o["count"], "bundle_discount": bundle,
            "sale": round(o["sale"], 2), "cog": round(o["cog"], 2),
            "cog_pct": round(o["cog"] / o["sale"] * 100, 1) if o["sale"] else None,
            "kind": kind, "sub": sub, "pct": pct, "state": state,
            "paid": bool(invs) and all(invoice_paid(i, cutoff) for i in invs),
            "invoices": [{"id": i["id"], "number": i.get("number"), "date": i.get("date"), "supplier": i.get("supplier")} for i in invs],
            "supplier": next((i["supplier"] for i in o["items"] if i["supplier"]), "") or (invs[0].get("supplier") if invs else ""),
            "note": mark.get("note") or "", "answer": mark.get("answer") or "", "answer_by": mark.get("by") or "",
        })
    out.sort(key=lambda o: o["created"] or "")
    return out


def compare(order_lines: list, history: list) -> dict:
    """This order's invoice lines next to the latest earlier invoice lines for the same products, same store.
    order_lines: this order's invoice lines (with number/date/supplier); history: every invoice line of the store."""
    from cog import norm
    first = min((l["invoice_id"] for l in order_lines), default=0)
    day = min((l.get("date") or "" for l in order_lines), default="")
    products = {norm(l.get("product") or l.get("title") or "") for l in order_lines}
    earlier = [h for h in history if h["status"] not in DROP and norm(h.get("product") or "") in products
               and h["invoice_id"] != first and ((h.get("date") or "") < day or
                                                 ((h.get("date") or "") == day and h["invoice_id"] < first))
               and h["order_key"] not in {l["order_key"] for l in order_lines}]
    last = None
    if earlier:
        top = max(earlier, key=lambda h: (h.get("date") or "", h["invoice_id"], h["line_no"]))
        last = [h for h in earlier if h["invoice_id"] == top["invoice_id"] and h["order_key"] == top["order_key"]]
    side = lambda ls: ls and {
        "invoice_id": ls[0]["invoice_id"], "number": ls[0].get("number"), "date": ls[0].get("date"),
        "supplier": ls[0].get("supplier"),
        "lines": [{"order": l.get("order_name") or l["order_key"], "product": l.get("product") or l.get("title"),
                   "title": l.get("title"), "units": l.get("units") or 1, "amount": l["amount"], "status": l["status"],
                   "previous": l.get("previous")} for l in ls],
        "total": round(sum(l["amount"] for l in ls), 2)}
    return {"this": side(order_lines), "last": side(last) if last else None}


def by_day(invoices: list, lines_of: dict, cutoff: str, supplier: str = "") -> list:
    """Invoices for the Invoices tab: replaced ones left out, each with paid / flag."""
    out = []
    for i in invoices:
        if supplier and (i.get("supplier") or "") != supplier:
            continue
        ls = lines_of.get(i["id"], [])
        if replaced(ls):
            continue
        flags = []
        if i.get("ai_check") == "review":
            flags.append("The AI read this invoice differently")
        n_change = sum(1 for l in ls if l["status"] == "changed" and not l.get("approved_at")
                       and l.get("check_state") not in ("kept", "bundle", "checking"))
        if n_change:
            flags.append(f"Price changed on {n_change} line{'s' if n_change > 1 else ''}, not decided yet")
        n_bad = sum(1 for l in ls if l["status"] in ("unmatched", "duplicate"))
        if n_bad:
            flags.append(f"{n_bad} line{'s' if n_bad > 1 else ''} not matching our orders")
        if any(l.get("check_state") == "checking" for l in ls):
            flags.append("Checking with the supplier")
        out.append({"id": i["id"], "date": i.get("date") or "", "supplier": i.get("supplier") or "Unknown supplier",
                    "store": i.get("store") or "", "number": i.get("number") or "", "total": round(i.get("total") or 0.0, 2),
                    "paid": invoice_paid(i, cutoff), "marked": bool(i.get("paid")), "before_cutoff": (i.get("date") or "") < cutoff,
                    "paid_by": i.get("paid_by") or "", "paid_at": i.get("paid_at") or "",
                    "flag": "; ".join(flags), "note": i.get("note") or "", "file": bool(i.get("file_ext"))})
    out.sort(key=lambda x: (x["date"], x["supplier"], x["store"]), reverse=True)
    return out


def not_invoiced(orders: list, cutoff: str, supplier: str = "") -> dict:
    """Shipped orders the supplier hasn't billed yet, since the cut-off: our estimate of what's coming.
    orders: suppliers.fulfilled_orders rows."""
    total, n, per = 0.0, 0, defaultdict(float)
    for o in orders:
        if o["invoiced"] or o["day"] < cutoff or (supplier and o["supplier"] != supplier):
            continue
        total += o["cost"]
        n += 1
        per[o["supplier"]] += o["cost"]
    return {"total": round(total, 2), "orders": n, "by_supplier": {k: round(v, 2) for k, v in per.items()}}


def group_days(invs: list) -> list:
    """by_day rows grouped per invoice date: count, total, count per supplier (newest first)."""
    days = {}
    for i in invs:
        d = days.setdefault(i["date"], {"date": i["date"], "count": 0, "total": 0.0, "by_supplier": defaultdict(int)})
        d["count"] += 1
        d["total"] += i["total"]
        d["by_supplier"][i["supplier"]] += 1
    return [{**d, "total": round(d["total"], 2), "by_supplier": dict(d["by_supplier"])}
            for d in sorted(days.values(), key=lambda d: d["date"], reverse=True)]
