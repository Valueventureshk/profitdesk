"""Supplier accounts: what we owe each supplier, at any moment.

    owed now = opening balance (owner's figure on the opening date)
             + COG of every order the supplier fulfilled since then
             - payments sent to the supplier since then (Airwallex)

Every order's COG is ProfitDesk's own costing (cog_lines): the invoice price once the supplier's
invoice is uploaded, otherwise the expected price (single price, or single prices less the usual
bundle discount). So "owed" splits into invoiced and fulfilled-but-not-invoiced-yet.

An order belongs to the supplier on its cost lines (invoice / sheet / product history); lines
with no supplier yet go to the store's usual supplier. Fulfilled = the first parcel's fulfilment
time (SCM), on the business clock.

Each supplier invoice is checked against ProfitDesk's own expectation for the same orders:
it "matches" when every order on it passes the price checks, no fulfilled order in its range is
missing from it, nothing is billed twice and the AI's second reading agrees.
All amounts in USD (suppliers bill in USD).
"""
from collections import defaultdict
from datetime import datetime, timedelta

LATE_DAYS = 14          # fulfilled this long ago and still on no invoice: worth asking the supplier


def _day(ts: str, tz) -> str:
    """ISO time (UTC) -> business-clock date."""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return (ts or "")[:10]
    return t.astimezone(tz).date().isoformat() if t.tzinfo else t.date().isoformat()


def default_suppliers(lines: list) -> dict:
    """{store id: the supplier most of its costed lines come from}."""
    count = defaultdict(lambda: defaultdict(int))
    for l in lines:
        if l.get("supplier"):
            count[l["store_id"]][l["supplier"]] += 1
    return {sid: max(c, key=c.get) for sid, c in count.items()}


def fulfilled_orders(lines: list, fulfilled: dict, tz) -> list:
    """One row per (supplier, store, order) fulfilled: cost, day, invoiced or not.
    lines: cog_lines rows (+ "on_invoice"); fulfilled: {(store, order name): first fulfilment time}."""
    usual = default_suppliers(lines)
    out = {}
    for l in lines:
        if l.get("cancelled"):
            continue
        when = fulfilled.get((l["store_id"], l["order_name"]))
        if not when:
            continue
        sup = l.get("supplier") or usual.get(l["store_id"]) or "Unknown supplier"
        k = (sup, l["store_id"], l["order_name"])
        o = out.setdefault(k, {"supplier": sup, "store_id": l["store_id"], "order": l["order_name"],
                               "day": _day(when, tz), "cost": 0.0, "items": 0, "invoiced": True,
                               "estimated": False})
        o["cost"] += l.get("cost_usd") or 0.0
        o["items"] += l.get("quantity") or 1
        o["invoiced"] = o["invoiced"] and bool(l.get("on_invoice"))
        o["estimated"] = o["estimated"] or l.get("source") == "estimate"
    return list(out.values())


def invoice_check(inv: dict, lines: list, rate_of) -> dict:
    """Expected vs invoiced for one invoice, and what doesn't match.
    rate_of(store, items) -> usual bundle discount (0..1)."""
    expected, issues = 0.0, []
    for l in lines:
        if l["status"] in ("unmatched", "duplicate", "superseded"):
            if l["status"] != "superseded":
                issues.append(f"{l['order_name'] or l['order_key']}: "
                              f"{'not one of our orders' if l['status'] == 'unmatched' else 'billed twice'}")
            continue
        units = l.get("units") or 1
        if (l.get("tier") or 1) >= 2 and l.get("singles") is not None:
            expected += l["singles"] * (1 - rate_of(l["store_id"], l["tier"]))
        elif l.get("previous") is not None and (l.get("tier") or 1) <= 1:
            expected += l["previous"] * units
        else:
            expected += l["amount"]                      # first time: nothing to expect yet
        if l["status"] == "changed" and not l.get("approved_at") and l.get("check_state") != "kept":
            issues.append(f"{l['order_name'] or l['order_key']}: price to decide")
    missing = inv.get("missing_list") or []
    issues += [f"{m.get('order')}: fulfilled but not on this invoice" for m in missing]
    if inv.get("ai_check") == "review":
        issues.append("The AI read this invoice differently")
    return {"expected": round(expected, 2), "difference": round((inv["total"] or 0) - expected, 2),
            "issues": sorted(set(issues)), "matches": not issues}


def ledger(account: dict, orders: list, payments: list, invoices: list, today: str) -> dict:
    """One supplier's account from its opening date to today."""
    start = account.get("opening_date") or "0000-00-00"
    mine = [o for o in orders if o["day"] >= start]
    paid = [p for p in payments if p["day"] >= start]
    cost = sum(o["cost"] for o in mine)
    invoiced = sum(o["cost"] for o in mine if o["invoiced"])
    total_paid = sum(p["usd"] for p in paid)
    late_day = (datetime.fromisoformat(today) - timedelta(days=LATE_DAYS)).date().isoformat()
    late = sorted((o for o in mine if not o["invoiced"] and o["day"] <= late_day), key=lambda o: o["day"])
    stores = defaultdict(lambda: {"orders": 0, "cost": 0.0, "not_invoiced": 0.0})
    for o in mine:
        s = stores[o["store_id"]]
        s["orders"] += 1
        s["cost"] += o["cost"]
        if not o["invoiced"]:
            s["not_invoiced"] += o["cost"]
    opening = account.get("opening_amount") or 0.0
    return {
        "supplier": account["name"], "opening_date": account.get("opening_date"), "opening": opening,
        "set": bool(account.get("opening_date")),
        "orders": len(mine), "fulfilled": round(cost, 2),
        "invoiced": round(invoiced, 2), "not_invoiced": round(cost - invoiced, 2),
        "estimated": round(sum(o["cost"] for o in mine if o["estimated"]), 2),
        "paid": round(total_paid, 2), "owed": round(opening + cost - total_paid, 2),
        "payments": sorted(paid, key=lambda p: p["time"], reverse=True),
        "invoices": [i for i in invoices if (i.get("date") or "") >= start],
        "late": late[:200], "late_count": len(late), "late_cost": round(sum(o["cost"] for o in late), 2),
        "stores": dict(stores),
    }
