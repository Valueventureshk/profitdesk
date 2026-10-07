"""The cash flow engine: what you have now and what lands when.

Every cash figure on the Cash flow page is worked out here, from each
connected account's balances and its pending (not-yet-settled) transactions.

    Available now        sum of available balances
    Arriving by day N    pending net amounts whose estimated settlement date is
                         on or before today + N (anything overdue or undated
                         counts as arriving by tomorrow)
    Available by day N   available now + arriving by day N

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


def summarize(accounts: list, factor, today: date, tz) -> dict:
    """accounts: [{"id", "label", "provider", "balances": [...], "pending": [...]}]
    factor(currency) -> multiplier into the display currency."""
    available = reserved = 0.0
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

    return {
        "available": available,
        "reserved": reserved,
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
