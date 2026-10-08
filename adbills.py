"""Ad bills: ad spend you owe Meta and Google that they haven't charged yet.

    Meta     the ad account's own "balance" (amount due, not yet billed), exact.
    Google   Google doesn't share its balance outside Google Ads, but its balance
             is simply the spend since it last charged you. So:
               last charge   the newest successful Airwallex card charge whose
                             merchant name carries this account's number
                             (e.g. "GOOGLE*ADS8372710664" is account 837-271-0664)
               owed          spend after that moment, from the hourly figures the
                             Google Ads script writes to the Sheet

Only ad accounts linked to a store in ProfitDesk are counted.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GOOGLE_ID = re.compile(r"GOOGLE\s*\*?\s*ADS\s*(\d{10})", re.I)


def _when(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace("+0000", "+00:00"))
    except (TypeError, ValueError):
        return None


def _zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _digits(cid) -> str:
    return re.sub(r"\D", "", str(cid or ""))


def google_charges(card_items: list) -> dict:
    """{account digits: [charge, ...]} from Airwallex card transactions.
    An authorisation and its clearing are one charge (same lifecycle);
    its time is the earliest of the two, i.e. when Google charged."""
    charges: dict = {}
    for i in card_items:
        m = i.get("merchant") or {}
        hit = GOOGLE_ID.search(m.get("name") or "")
        if not hit:
            continue
        when = _when(i.get("transaction_date") or i.get("posted_date"))
        if not when:
            continue
        key = i.get("lifecycle_id") or i.get("transaction_id")
        acct = charges.setdefault(hit.group(1), {})
        c = acct.setdefault(key, {"time": when, "ok": False, "failed": False,
                                  "amount": 0.0, "currency": None, "card": i.get("card_nickname") or "",
                                  "reason": None})
        c["time"] = min(c["time"], when)
        if i.get("status") == "FAILED":
            c["failed"] = True
            c["reason"] = i.get("failure_reason")
        else:
            c["ok"] = True
        amount = abs(float(i.get("transaction_amount") or 0))
        if amount:
            c["amount"] = amount
            c["currency"] = i.get("transaction_currency")
        if i.get("transaction_type") == "CLEARING" and i.get("status") != "FAILED":
            c["cleared"] = True
    return {k: sorted(v.values(), key=lambda c: c["time"]) for k, v in charges.items()}


def spend_since(rows: list, acct_tz: str, since: datetime, now: datetime) -> float:
    """Spend after `since`, from the Sheet's rows [(date, hour|"day", cost)].

    Hourly figures are used wherever the Sheet has them (the last few days);
    older days use daily totals. The hour the charge happened in is split
    pro rata, as is a day that only has a daily total."""
    tz = _zone(acct_tz)
    t = since.astimezone(tz)
    hourly: dict = {}
    daily: dict = {}
    for d, h, cost in rows:
        if h == "day":
            daily[d] = cost
        else:
            hourly.setdefault(d, {})[int(h)] = hourly.get(d, {}).get(int(h), 0.0) + cost
    total = 0.0
    day = t.date()
    last = now.astimezone(tz).date()
    while day <= last:
        key = day.isoformat()
        if key in hourly:
            for h, cost in hourly[key].items():
                start = datetime(day.year, day.month, day.day, h, tzinfo=tz)
                if start >= t:
                    total += cost
                elif start + timedelta(hours=1) > t:
                    total += cost * (1 - (t - start).total_seconds() / 3600)
        elif key in daily:
            if day == t.date():
                midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
                total += daily[key] * (1 - (t - midnight).total_seconds() / 86400)
            else:
                total += daily[key]
        day += timedelta(days=1)
    return total


def google_row(store: dict, tab: dict, charges: list, now: datetime) -> dict:
    """One connected Google Ads account."""
    ok = [c for c in charges if c["ok"]]
    failed = [c for c in charges if c["failed"] and not c["ok"]
              and now - c["time"] < timedelta(days=7)]
    last = ok[-1] if ok else None
    # Declines after the last successful charge mean Google is still waiting.
    failed = [c for c in failed if not last or c["time"] > last["time"]]
    row = {
        "platform": "Google", "store": store["name"], "account_id": store["google_customer_id"],
        "account": store.get("google_account_name") or "",
        "currency": (tab or {}).get("meta", {}).get("currency") or store.get("google_currency") or "",
        "owed": None, "last_charge": None,
        "failed": [{"time": c["time"].isoformat(), "amount": c["amount"], "currency": c["currency"],
                    "card": c["card"], "reason": (c["reason"] or "").replace("_", " ").lower()}
                   for c in failed],
    }
    if not tab:
        row["note"] = "No spend figures from the Google Ads script yet."
        return row
    if last:
        # A declined attempt for the same amount just before is the same bill,
        # retried: the bill covers spend up to the first attempt.
        billed = last["time"]
        for c in charges:
            if (c["failed"] and c["currency"] == last["currency"]
                    and abs(c["amount"] - last["amount"]) < 0.01
                    and timedelta(0) < last["time"] - c["time"] < timedelta(days=3)):
                billed = min(billed, c["time"])
        row["last_charge"] = {"time": last["time"].isoformat(), "amount": last["amount"],
                              "currency": last["currency"], "card": last["card"],
                              "first_try": billed.isoformat() if billed != last["time"] else None}
        row["owed"] = spend_since(tab["rows"], tab["meta"]["timezone"], billed, now)
    else:
        row["note"] = ("No Google charge for this account on your Airwallex cards in the last "
                       "60 days, so ProfitDesk can't tell what's been paid. It may be paid "
                       "another way.")
    return row


def meta_row(store: dict, account: dict) -> dict:
    """balance is in the account currency's smallest unit (cents)."""
    cents = float(account.get("balance") or 0)
    zero_decimal = account.get("currency") in {"JPY", "KRW", "VND", "IDR", "CLP", "TWD", "HUF", "ISK"}
    return {"platform": "Meta", "store": store["name"], "account_id": store["meta_account_id"],
            "account": account.get("name") or store.get("meta_account_name") or "",
            "currency": account.get("currency") or store.get("meta_currency") or "",
            "owed": cents if zero_decimal else cents / 100.0,
            "status": account.get("account_status"), "failed": [], "last_charge": None}


def summarize(rows: list, factor) -> dict:
    """Totals in the display currency. Accounts whose amount isn't known are listed but
    left out of the total."""
    total = 0.0
    for r in rows:
        r["owed_display"] = None if r["owed"] is None else r["owed"] * factor(r["currency"])
        if r["owed_display"] is not None:
            total += r["owed_display"]
        for f in r["failed"]:
            f["amount_display"] = f["amount"] * factor(f["currency"] or r["currency"])
    rows.sort(key=lambda r: (r["owed_display"] is None, -(r["owed_display"] or 0)))
    return {
        "total": total,
        "meta": sum(r["owed_display"] or 0 for r in rows if r["platform"] == "Meta"),
        "google": sum(r["owed_display"] or 0 for r in rows if r["platform"] == "Google"),
        "unknown": sum(1 for r in rows if r["owed"] is None),
        "declined": sum(len(r["failed"]) for r in rows),
        "accounts": rows,
    }
