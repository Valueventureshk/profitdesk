"""The cash flow engine: what you have now and what lands when.

Every cash figure on the Cash flow page is worked out here, from each
connected account's balances and its pending (not-yet-settled) transactions.

    Available now        sum of available balances
    Arriving by day N    pending net amounts whose estimated settlement date is
                         on or before today + N (anything overdue or undated
                         counts as arriving by tomorrow)
    Available by day N   available now + arriving by day N
    Receivable           every pending amount, whatever its date: sales not yet
                         settled, reserves still held, PayPal holds
    Available + receivable   available now + receivable: everything that is
                         yours, spendable now or on its way

In transit between own accounts: a PayPal withdrawal to our own Airwallex leaves
PayPal's balance at once and lands in Airwallex a day or few later. It stays
counted as available the whole way (statement.in_transit: withdrawals with no
Airwallex deposit yet, up to statement.TRANSIT_DAYS old, or whose deposit is
still pending). Only a fee on the withdrawal is money out.

Pending amounts are net: refunds and payouts waiting to leave are negative.
Card spending on hold (ISSUING_AUTHORISATION_HOLD/RELEASE) is left out: the
provider has already taken it off the available balance, so counting it as
"arriving" would subtract it twice.
Days follow the business's own clock (Hong Kong). Everything is converted to
one display currency at the latest daily rate.
"""
from datetime import date, datetime, timedelta

HORIZONS = [("tomorrow", "By tomorrow", 1), ("d7", "Next 7 days", 7), ("d14", "Next 14 days", 14),
            ("d30", "Next 30 days", 30), ("d60", "Next 60 days", 60), ("d90", "Next 90 days", 90)]

# How pending items are grouped for the breakdown.
KIND = {
    "PAYMENT": "Sales settling",
    "PAYMENT_RESERVE_RELEASE": "Reserve releases",
    "PAYMENT_RESERVE_HOLD": "Reserve holds",
    "PAYPAL_HOLD_RELEASE": "PayPal holds released",
    "REFUND": "Refunds",
    "REFUND_FAILURE": "Refunds",
    "REFUND_REVERSAL": "Refunds",
    "PAYOUT": "Payouts",
    "PAYOUT_FAILURE": "Payouts",
    "PAYOUT_REVERSAL": "Payouts",
    "DISPUTE": "Disputes",
    "DISPUTE_LOST": "Disputes",
    "DISPUTE_REVERSAL": "Disputes",
    "PRE_CHARGEBACK_ACCEPTED": "Disputes",
    "FEE": "Fees",
}


# Already reflected in the available balance; not money still to arrive or leave.
ALREADY_IN_BALANCE = {"ISSUING_AUTHORISATION_HOLD", "ISSUING_AUTHORISATION_RELEASE"}


def _kind(t: str) -> str:
    return KIND.get(t, "Other")


def _due(estimated: str, tz) -> date | None:
    if not estimated:
        return None
    try:
        return datetime.fromisoformat(estimated.replace("Z", "+00:00")).astimezone(tz).date()
    except ValueError:
        return None


def summarize(accounts: list, factor, today: date, tz, transit: list = ()) -> dict:
    """accounts: [{"id", "label", "provider", "balances": [...], "pending": [...]}]
    factor(currency) -> multiplier into the display currency.
    transit: statement.in_transit() (money on its way between own accounts)."""
    in_transit = sum(t["amount"] * factor(t["currency"]) for t in transit)
    available = in_transit
    reserved = 0.0
    incoming = {key: 0.0 for key, _, _ in HORIZONS}
    by_kind = {key: {} for key, _, _ in HORIZONS}
    later = 0.0
    days: dict[str, dict] = {}
    per_account = []
    by_type: dict[str, dict] = {}   # raw Airwallex types, to check what "Other" is

    for acct in accounts:
        a_avail = sum(b["available"] * factor(b["currency"]) for b in acct["balances"])
        a_reserved = sum(b["reserved"] * factor(b["currency"]) for b in acct["balances"])
        a_incoming = {key: 0.0 for key, _, _ in HORIZONS}
        available += a_avail
        reserved += a_reserved

        for p in acct["pending"]:
            if p["type"] in ALREADY_IN_BALANCE:
                continue
            amount = p["net"] * factor(p["currency"])
            due = _due(p["estimated"], tz)
            t = by_type.setdefault(p["type"] or "?", {"count": 0, "amount": 0.0,
                                                      "undated": 0, "overdue": 0})
            t["count"] += 1
            t["amount"] += amount
            t["undated"] += due is None
            t["overdue"] += due is not None and due < today
            when = max(due or today, today)            # overdue/undated: treat as today
            ahead = (when - today).days
            kind = _kind(p["type"])
            for key, _, n in HORIZONS:
                if ahead <= n:
                    incoming[key] += amount
                    a_incoming[key] += amount
                    by_kind[key][kind] = by_kind[key].get(kind, 0.0) + amount
            if ahead > HORIZONS[-1][2]:
                later += amount
            else:
                day = days.setdefault(when.isoformat(), {"date": when.isoformat(), "total": 0.0,
                                                         "kinds": {}})
                day["total"] += amount
                day["kinds"][kind] = day["kinds"].get(kind, 0.0) + amount

        per_account.append({
            "id": acct["id"], "label": acct["label"], "provider": acct["provider"],
            "available": a_avail, "reserved": a_reserved,
            "incoming": a_incoming,
            "balances": acct["balances"],
        })

    receivable = incoming[HORIZONS[-1][0]] + later
    return {
        "available": available,
        "in_transit": in_transit,
        "transit": [{**t, "value": t["amount"] * factor(t["currency"])} for t in transit],
        "reserved": reserved,
        "receivable": receivable,
        "position": available + receivable,
        "horizons": [{
            "key": key, "label": label, "days": n,
            "until": (today + timedelta(days=n)).isoformat(),
            "incoming": incoming[key],
            "available_by": available + incoming[key],
            "by_kind": dict(sorted(by_kind[key].items(), key=lambda kv: -abs(kv[1]))),
        } for key, label, n in HORIZONS],
        "later": later,
        "schedule": [days[d] for d in sorted(days)],
        "accounts": per_account,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -abs(kv[1]["amount"]))),
    }


# ---------------------------------------------------------------- arrived today
# A pending item that settles leaves the pending list and is in the available balance from then on.
# So "arrived" = an item we saw due by tomorrow (or overdue) that is no longer pending in an account
# that answered this time. Kept in the setting `cash_arrivals` {"seen": {...}, "arrived": {...}}.
KEEP_DAYS = 3


def _key(acct: dict, p: dict) -> str:
    return f"{acct['id']}|{p.get('id') or ''}|{p['type']}|{p['currency']}|{p.get('created') or ''}"


def track_arrivals(state: dict, accounts: list, today: date, tz, now: datetime) -> dict:
    """Update `state` with this call's pending lists. Returns the new state."""
    seen = dict(state.get("seen") or {})
    arrived = dict(state.get("arrived") or {})
    answered = {str(a["id"]) for a in accounts}
    current = set()
    for acct in accounts:
        for p in acct["pending"]:
            if p["type"] in ALREADY_IN_BALANCE:
                continue
            k = _key(acct, p)
            current.add(k)
            due = _due(p["estimated"], tz)
            when = max(due or today, today)
            if (when - today).days <= 1:
                seen[k] = {"due": when.isoformat(), "net": p["net"], "currency": p["currency"],
                           "kind": _kind(p["type"]), "account": acct["label"]}
    for k, s in list(seen.items()):
        if k.split("|", 1)[0] in answered and k not in current:   # settled (early ones count today too)
            arrived[k] = {**s, "on": today.isoformat(), "at": now.isoformat(timespec="minutes")}
            del seen[k]
    for k in list(arrived):
        if k in current:                   # back on the pending list: it hadn't arrived after all
            del arrived[k]
    cut = (today - timedelta(days=KEEP_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v["due"] >= cut}
    arrived = {k: v for k, v in arrived.items() if v["on"] >= cut}
    return {"seen": seen, "arrived": arrived}


def arrived_today(state: dict, factor, today: date) -> dict:
    """{"total", "count", "kinds", "last"} of what arrived today, in the display currency."""
    total, kinds, last, n = 0.0, {}, "", 0
    for a in (state.get("arrived") or {}).values():
        if a["on"] != today.isoformat():
            continue
        v = a["net"] * factor(a["currency"])
        total += v
        n += 1
        kinds[a["kind"]] = kinds.get(a["kind"], 0.0) + v
        last = max(last, a["at"])
    return {"total": total, "count": n, "last": last,
            "kinds": dict(sorted(kinds.items(), key=lambda kv: -abs(kv[1])))}
