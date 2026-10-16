"""The cash statement: how "available + receivable" moved over a date range.

Every money movement from Airwallex and PayPal is sorted into a line on a
statement, like a bank statement but for everything that's yours: money you
can spend now plus money on its way (sales not yet settled, reserves, holds).

    closing  = position now  - every movement after the range
    opening  = closing       - every movement inside the range

so the opening and closing always tie back to the live card on the page.

Movements that only shift money between "available" and "receivable"
(reserve holds and releases, settlement) or between your own accounts
(PayPal -> Airwallex, PayPal -> PayPal, Airwallex sub-account transfers) don't
change the total. They are listed under "Moved between your accounts" for
reference. A PayPal withdrawal still on its way to Airwallex (up to
TRANSIT_DAYS) counts as available the whole time (cashflow.py adds it), so
here it changes nothing either; only its fee is money out.

Money sent to an Airwallex account that isn't connected here is money out
(a supplier like DayOne has its own Airwallex), unless the owner said that
payee is one of their own accounts (Expenses desk question, `ours`).

Each movement is converted at today's daily rate, the same rate the live
card uses, so the statement adds up exactly.
"""
import re
from datetime import datetime, timedelta, timezone

IN, OUT, FEES, MOVED = "Money in", "Money out", "Fees and costs", "Moved between your accounts"
SECTIONS = [IN, OUT, FEES, MOVED]


def _when(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None


def _num(v) -> float:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _f(d: dict, snake: str):
    if snake in d:
        return d[snake]
    head, *rest = snake.split("_")
    return d.get(head + "".join(w.title() for w in rest))


def payee(name: str) -> str:
    """Meta, Google Ads and Shopify are grouped by name; anything else keeps its own."""
    n = (name or "").lower()
    if "facebk" in n or "meta" in n or "facebook" in n or "fb.com" in n:
        return "Meta ads"
    if "google" in n:
        return "Google Ads"
    if "shopify" in n:
        return "Shopify"
    if "tiktok" in n:
        return "TikTok ads"
    if "pinterest" in n:
        return "Pinterest ads"
    return (name or "").strip() or "Card spend"


def gateway(name: str) -> str:
    """Which connected account a Shopify payment gateway pays into."""
    n = (name or "").lower()
    if "paypal" in n:
        return "PayPal"
    if "airwallex" in n:
        return "Airwallex"
    return "Other"


class Card:
    """Merchant names for Airwallex card spend, from the card transaction list."""

    def __init__(self, items: list):
        self.by_id, self.loose = {}, []
        for i in items:
            if i.get("status") == "FAILED":
                continue
            m = i.get("merchant") or {}
            name = (m.get("additional_merchant_info") or {}).get("merchant_full_name") or m.get("name")
            info = {"merchant": payee(name), "card": i.get("card_nickname") or ""}
            for k in (i.get("transaction_id"), i.get("lifecycle_id")):
                if k:
                    self.by_id[k] = info
            self.loose.append((_when(i.get("transaction_date")), i.get("billing_currency"),
                               round(abs(_num(i.get("billing_amount"))), 2), info))

    def find(self, source_id, when, currency, amount):
        if source_id in self.by_id:
            return self.by_id[source_id]
        amount = round(abs(amount), 2)
        for t, cur, amt, info in self.loose:
            if cur == currency and amt == amount and t and when and abs((t - when).total_seconds()) < 600:
                return info
        return None


def airwallex_moves(label: str, rows: list, cards: list) -> list:
    """Movements from one Airwallex account's financial transactions."""
    card = Card(cards)
    out, conversions = [], {}
    # A card hold, its release and its capture share or chain ids; whatever one of
    # them matched, the others in the same lifecycle get the same merchant.
    by_source = {}
    for t in rows:
        if (_f(t, "transaction_type") or "").startswith("ISSUING"):
            info = card.find(_f(t, "source_id"), _when(_f(t, "created_at")), t.get("currency"),
                             _num(t.get("amount")))
            if info:
                by_source[_f(t, "source_id")] = info
    for t in rows:
        typ = _f(t, "transaction_type") or ""
        cur = t.get("currency")
        when = _when(_f(t, "created_at"))
        amount, net, fee = _num(t.get("amount")), _num(t.get("net")), _num(t.get("fee"))
        desc = (t.get("description") or "").strip()
        status = t.get("status")
        base = {"time": when, "currency": cur, "source": f"Airwallex · {label}",
                "id": t.get("id") or _f(t, "source_id"), "sid": _f(t, "source_id"), "desc": desc}
        if not when or status in ("FAILED",):
            continue

        if typ == "PAYMENT":
            out.append({**base, "section": IN, "line": "Sales received · Airwallex",
                        "amount": amount, "count": 1, "kind": "sales"})
            if fee:
                out.append({**base, "section": FEES, "line": "Card processing fees · Airwallex",
                            "amount": -fee})
        elif typ == "PAYMENT_RESERVE_HOLD":
            out.append({**base, "section": MOVED, "line": "Held back as reserve", "amount": 0.0,
                        "info": -net})
        elif typ == "PAYMENT_RESERVE_RELEASE":
            # Listed when it actually lands, not when the sale created it.
            landed = _when(_f(t, "settled_at")) if status == "SETTLED" else None
            if landed:
                out.append({**base, "time": landed, "section": MOVED,
                            "line": "Reserve released to available", "amount": 0.0, "info": net})
        elif typ.startswith("REFUND"):
            out.append({**base, "section": OUT, "line": "Refunds to customers", "amount": net})
        elif typ.startswith(("DISPUTE", "PRE_CHARGEBACK", "CHARGEBACK")):
            out.append({**base, "section": OUT, "line": "Disputes and chargebacks", "amount": net})
        elif typ.startswith("ISSUING"):
            info = by_source.get(_f(t, "source_id")) or card.find(_f(t, "source_id"), when, cur, amount)
            name = (info or {}).get("merchant") or (
                "Card spend" if net < 0 else "Card refunds and released holds")
            out.append({**base, "section": OUT, "line": name,
                        "amount": net, "detail": (info or {}).get("card"), "card": True})
        elif typ == "FEE":
            out.append({**base, "section": FEES, "line": "Airwallex account fees", "amount": net})
        elif typ.startswith("PAYOUT"):
            m = re.search(r"\bto (.+?)(?: \(|$)", desc)
            who = m.group(1).strip() if m else (desc or "Bank transfer")
            out.append({**base, "section": OUT, "line": f"Paid to {who}", "amount": amount,
                        "payout": True})
            if fee:
                out.append({**base, "section": FEES, "line": "Transfer fees · Airwallex",
                            "amount": -abs(fee)})
        elif typ == "DEPOSIT":
            gross = amount or net
            out.append({**base, "section": IN, "line": "Deposits received", "amount": gross,
                        "deposit": True, "detail": desc[:60], "status": status})
            if abs(gross - net) >= 0.005:
                out.append({**base, "section": FEES, "line": "Deposit fees · Airwallex",
                            "amount": net - gross})
        elif typ.startswith("CONVERSION"):
            key = str(_f(t, "created_at"))[:19]     # both legs are booked together
            conversions.setdefault(key, []).append({**base, "amount": net})
        elif typ.startswith("DC_") or _f(t, "source_type") == "TRANSFER":
            out.append({**base, "section": MOVED, "line": "Transfers between Airwallex accounts",
                        "amount": net, "info": net, "own": True})
        else:
            out.append({**base, "section": OUT if net < 0 else IN,
                        "line": f"Other · {typ.replace('_', ' ').lower() or 'Airwallex'}",
                        "amount": net})

    for legs in conversions.values():
        out.append({"time": legs[0]["time"], "source": legs[0]["source"], "section": FEES,
                    "line": "Currency conversions (at today's rate)", "legs": legs})
    return out


# PayPal event codes: https://developer.paypal.com/docs/transaction-search/transaction-event-codes/
PP_INTERNAL = ("T15", "T21")            # holds and releases: available <-> withheld
PP_DISPUTE_HOLD = {"T1110", "T1111"}    # held for a dispute, then given back


def paypal_moves(label: str, rows: list) -> list:
    out, conversions = [], {}
    for d in rows:
        t = d.get("transaction_info") or {}
        code = t.get("transaction_event_code") or ""
        status = t.get("transaction_status")
        when = _when(t.get("transaction_initiation_date"))
        amt = t.get("transaction_amount") or {}
        cur, amount = amt.get("currency_code"), _num(amt)
        fee = _num(t.get("fee_amount"))
        if not when or status in ("D", "V"):
            continue
        who = ((d.get("payer_info") or {}).get("payer_name") or {}).get("alternate_full_name") or ""
        domain = ((d.get("payer_info") or {}).get("email_address") or "").split("@")[-1]
        base = {"time": when, "currency": cur, "source": f"PayPal · {label}", "code": code,
                "id": t.get("transaction_id"), "desc": (t.get("transaction_subject") or t.get("transaction_note") or "")[:120],
                "who": who or domain}

        if code.startswith(PP_INTERNAL) or code in PP_DISPUTE_HOLD:
            if code == "T2104" or code == "T2102":
                out.append({**base, "section": MOVED, "line": "Reserve released to available",
                            "amount": 0.0, "info": amount})
            elif code in ("T2103", "T2101"):
                out.append({**base, "section": MOVED, "line": "Held back as reserve",
                            "amount": 0.0, "info": -amount})
            continue
        if code.startswith("T02"):
            conversions.setdefault(t.get("transaction_initiation_date"), []).append(
                {**base, "amount": amount})
            continue
        if code.startswith("T04"):
            out.append({**base, "section": MOVED, "line": "PayPal withdrawals", "amount": amount,
                        "info": amount, "withdrawal": True})
        elif code.startswith("T00") and amount > 0:
            out.append({**base, "section": IN, "line": "Sales received · PayPal", "amount": amount,
                        "count": 1, "kind": "sales", "pp_pay": True})
        elif code.startswith("T00"):
            out.append({**base, "section": OUT, "line": payee(who or domain), "amount": amount,
                        "pp_pay": True})
        elif code.startswith("T01"):
            line = "PayPal dispute fees" if code == "T0114" else "PayPal fees"
            out.append({**base, "section": FEES, "line": line, "amount": amount})
        elif code in ("T1101", "T1104"):
            # a withdrawal to the bank (or a bank deposit) that bounced back: own money returning, not a dispute
            out.append({**base, "section": MOVED, "line": "PayPal withdrawal returned", "amount": amount,
                        "info": amount, "returned": True})
        elif code == "T1105":
            # PayPal released a general account hold: available <-> held, the total doesn't change
            out.append({**base, "section": MOVED, "line": "Reserve released to available", "amount": 0.0,
                        "info": amount})
        elif code == "T1108":
            # PayPal reversed one of its fees: a (negative) cost, not a refund to a customer
            out.append({**base, "section": FEES, "line": "PayPal fees", "amount": amount})
        elif code == "T1107":
            out.append({**base, "section": OUT, "line": "Refunds to customers", "amount": amount})
        elif code.startswith(("T11", "T12")):
            out.append({**base, "section": OUT, "line": "Disputes and chargebacks", "amount": amount})
        else:
            out.append({**base, "section": OUT if amount < 0 else IN,
                        "line": f"Other · PayPal {code}", "amount": amount})
        if fee:
            out.append({**base, "section": FEES, "line": "PayPal transaction fees", "amount": fee})

    for legs in conversions.values():
        out.append({"time": legs[0]["time"], "source": legs[0]["source"], "section": FEES,
                    "line": "Currency conversions (at today's rate)", "legs": legs})
    return out


TRANSIT_DAYS = 7      # a PayPal withdrawal not in Airwallex after this went to a bank not connected here
OWN_PAYPAL = "Between your PayPal accounts"
OWN_AWX_OUT = "To your own Airwallex account (not connected here)"


def transfer_key(m: dict) -> str:
    """Who an Airwallex transfer went to, as the Expenses desk remembers it (per payee)."""
    who = m.get("desc") or "an Airwallex account not connected here"
    return "awx-transfer:" + re.sub(r"\s+", " ", re.sub(r"\d{3,}", "#", who).strip().lower())


def _pair_own(moves: list, ours: set = ()):
    """Airwallex account-to-account transfers: a debit in one connected account and
    the matching credit in another cancel out. Whatever has no partner went to (or
    came from) an Airwallex account that isn't connected here: money out, unless the
    owner said that payee is one of their own accounts (`ours`: transfer keys)."""
    own = [m for m in moves if m.get("own")]
    for m in own:
        if m.get("matched"):
            continue
        for o in own:
            if (o is not m and not o.get("matched") and o["currency"] == m["currency"]
                    and abs(o["amount"] + m["amount"]) < 0.01
                    and abs((o["time"] - m["time"]).total_seconds()) < 900):
                m["matched"] = o["matched"] = True
                break
    for m in own:
        if m.get("matched"):
            continue
        if m["amount"] < 0:
            m["key"] = transfer_key(m)
            if m["key"] in ours or f"tx:{m.get('id')}" in ours:
                m["line"] = OWN_AWX_OUT
            else:
                m.update(section=OUT, line="Sent to an Airwallex account not connected here")
        else:
            m["line"] = "Received from an Airwallex account not connected here"


def _pair_paypal(moves: list):
    """Money sent from one connected PayPal account to another: the payment out of
    one and the payment into the other are one move, not a cost and not a sale."""
    pays = [m for m in moves if m.get("pp_pay")]
    for out in pays:
        if out["amount"] >= 0 or out.get("pp_own"):
            continue
        for into in pays:
            if (into["amount"] > 0 and not into.get("pp_own") and into["source"] != out["source"]
                    and into["currency"] == out["currency"] and abs(into["amount"] + out["amount"]) < 0.01
                    and abs((into["time"] - out["time"]).total_seconds()) <= 86400):
                for x in (out, into):
                    x.update(section=MOVED, line=OWN_PAYPAL, info=x["amount"], amount=0.0, pp_own=True)
                    x.pop("kind", None)
                    x.pop("count", None)
                break


def _pair_transfers(moves: list, now: datetime = None):
    """A PayPal withdrawal and the Airwallex deposit it becomes are one move.
    Matched by currency and amount within TRANSIT_DAYS; the deposit side is relabelled.
    Both sides count 0: the money stays available on its way (cashflow.py counts a
    withdrawal not landed yet, at most TRANSIT_DAYS old, as available "in transit").
    A withdrawal not in Airwallex after TRANSIT_DAYS went to a bank not connected here."""
    now = now or datetime.now(timezone.utc)
    deposits = [m for m in moves if m.get("deposit")]
    returns = [m for m in moves if m.get("returned") and not m.get("partner")]
    for w in [m for m in moves if m.get("withdrawal") and not m.get("partner")]:
        sent = w["info"]                                    # the amount as PayPal booked it (negative)
        back = next((r for r in returns if r["currency"] == w["currency"] and abs(r["info"] + sent) < 0.01
                     and timedelta(0) <= r["time"] - w["time"] <= timedelta(days=30)), None)
        if back:
            # bounced back to PayPal: it never left, both sides count 0
            returns.remove(back)
            back.update(partner=w, amount=0.0)
            w.update(partner=back, amount=0.0, transit=False)
            continue
        for dep in sorted(deposits, key=lambda d: d["time"]):
            if (dep["currency"] == w["currency"] and abs(dep["amount"] + sent) < 0.01
                    and timedelta(minutes=-10) <= dep["time"] - w["time"] <= timedelta(days=TRANSIT_DAYS)):
                dep.update(section=MOVED, deposit=False, partner=w, info=dep["amount"], amount=0.0)
                w.update(partner=dep, amount=0.0)
                deposits.remove(dep)
                break
        else:
            if now - w["time"] <= timedelta(days=TRANSIT_DAYS):
                w.update(transit=True, amount=0.0)


def pair(moves: list, now: datetime = None, ours: set = ()):
    """Every own-account pairing, in order. Run once on all accounts' moves together."""
    _pair_transfers(moves, now)
    _pair_paypal(moves)
    _pair_own(moves, ours)


def in_transit(moves: list) -> list:
    """PayPal withdrawals on their way (after pair()), plus withdrawals whose Airwallex
    deposit is still pending: money that is still yours and spendable soon."""
    out = []
    for m in moves:
        if not m.get("withdrawal"):
            continue
        p = m.get("partner")
        if m.get("transit") or (p and p.get("status") == "PENDING"):
            out.append({"currency": m["currency"], "amount": -m["info"], "time": m["time"].isoformat(),
                        "from": m["source"], "to": p["source"] if p else None,
                        "deposit_id": p and p.get("id")})
    return out


def build(moves: list, factor, position_now: float, start: datetime, end: datetime,
          now: datetime, shopify: dict = None, ad_spend: float = None, ours: set = ()) -> dict:
    """moves: from airwallex_moves/paypal_moves. Returns the statement.
    shopify: Shopify sales on the same clock, {"PayPal"|"Airwallex"|"Other": {orders, amount}}.
    ours: Airwallex transfer keys the owner confirmed are their own accounts."""
    pair(moves, now, ours)
    # Into the display currency at today's rate.
    for m in moves:
        if "legs" in m:
            m["amount"] = sum(l["amount"] * factor(l["currency"]) for l in m["legs"])
            m["detail"] = " → ".join(f"{l['currency']} {abs(l['amount']):,.2f}"
                                     for l in sorted(m["legs"], key=lambda l: l["amount"]))
        else:
            m["amount"] = m["amount"] * factor(m["currency"])
            if "info" in m:
                m["info"] = m["info"] * factor(m["currency"])

    after = sum(m["amount"] for m in moves if m["time"] >= end)
    inside = [m for m in moves if start <= m["time"] < end]

    # PayPal -> Airwallex: one line when both ends are in the range (they cancel),
    # otherwise say which end is missing so the amount makes sense.
    for m in inside:
        p = m.get("partner")
        if (m.get("withdrawal") and p and p.get("returned")) or (m.get("returned") and p):
            m["line"] = "PayPal withdrawal sent back to PayPal"
        elif m.get("withdrawal"):
            m["line"] = ("PayPal → Airwallex" if p and start <= p["time"] < end else
                         "Sent from PayPal, arriving in Airwallex later" if p
                         else "On its way from PayPal (still counted as available)" if m.get("transit")
                         else "Withdrawn from PayPal to a bank not connected here")
        elif p:
            m["line"] = ("PayPal → Airwallex" if start <= p["time"] < end
                         else "Arrived in Airwallex from PayPal (sent earlier)")
            m.pop("detail", None)
    closing = position_now - after
    opening = closing - sum(m["amount"] for m in inside)

    lines: dict = {}
    for m in inside:
        key = (m["section"], m["line"])
        ln = lines.setdefault(key, {"section": m["section"], "line": m["line"], "amount": 0.0,
                                    "info": 0.0, "count": 0, "sources": set(), "details": {}})
        ln["amount"] += m["amount"]
        ln["info"] += m.get("info", 0.0)
        ln["count"] += 1
        ln["sources"].add(m["source"].split(" · ")[0])
        if m.get("detail") and m["section"] != MOVED:
            ln["details"][m["detail"]] = ln["details"].get(m["detail"], 0.0) + m["amount"]

    sections = []
    for name in SECTIONS:
        rows = [l for l in lines.values() if l["section"] == name
                and (abs(l["amount"]) >= 0.005 or abs(l["info"]) >= 0.005)]
        rows.sort(key=lambda l: -abs(l["amount"] if name != MOVED else l["info"]))
        if not rows:
            continue
        sections.append({
            "name": name,
            "total": sum(l["amount"] for l in rows),
            "lines": [{
                "line": l["line"], "amount": l["amount"],
                "info": l["info"] if name == MOVED else None,
                "count": l["count"], "via": " + ".join(sorted(l["sources"])),
                "details": [{"name": k, "amount": v} for k, v in
                            sorted(l["details"].items(), key=lambda kv: kv[1])][:6]
                if l["details"] and name != FEES else [],
            } for l in rows],
        })

    received = sum(m["amount"] for m in inside if m.get("kind") == "sales")
    orders = sum(1 for m in inside if m.get("kind") == "sales")
    match = None
    if shopify is not None:
        match = []
        for g in ("Airwallex", "PayPal", "Other"):
            sold = shopify.get(g, {"orders": 0, "amount": 0.0})
            got = [m for m in inside if m.get("kind") == "sales" and m["source"].startswith(g)]
            if abs(sold["amount"]) < 0.5 and not got:
                continue
            match.append({"via": g, "shopify": sold["amount"], "orders": sold["orders"],
                          "received": sum(m["amount"] for m in got), "payments": len(got)})
    shopify_sales = sum(g["amount"] for g in shopify.values()) if shopify else (0.0 if shopify is not None else None)
    ads_paid = -sum(m["amount"] for m in inside
                    if m["section"] == OUT and m["line"] in ("Meta ads", "Google Ads", "TikTok ads",
                                                             "Pinterest ads"))
    fees = -sum(m["amount"] for m in inside if m["section"] == FEES)
    return {
        "start": start.isoformat(), "end": min(end, now).isoformat(),
        "last_day": (end - timedelta(days=1)).date().isoformat(), "to_now": end > now,
        "opening": opening, "closing": closing, "change": closing - opening,
        "sections": sections,
        "checks": {
            "received": received, "payments": orders,
            "shopify_sales": shopify_sales,
            "matched": (received / shopify_sales) if shopify_sales else None,
            "by_gateway": match,
            "ads_paid": ads_paid, "ad_spend": ad_spend,
            "fees": fees, "fee_rate": (fees / received) if received else None,
        },
    }
