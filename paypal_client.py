"""PayPal: current balances per currency, for the cash flow page.

Read-only. Uses a live REST app (Client ID + Secret) created at
developer.paypal.com with the "Transaction search" feature switched on.

    login      POST /v1/oauth2/token          (client credentials, ~9 h token)
    balances   GET  /v1/reporting/balances    available + withheld per currency
    holds      GET  /v1/reporting/transactions (last 75 days, 31-day windows)

PayPal's feed doesn't carry a release date, but each hold names the sale it
belongs to, and PayPal's rules on the account fix when it comes back:

    T2103 reserve hold   (a % of each sale)          released RESERVE_DAYS later (T2104)
    T2101 general hold   (sales over a monthly cap)  released GENERAL_DAYS later (T2102)

A hold whose sale already has its release is done. Whatever is still held is
scheduled at hold date + those days. Any withheld money this can't place stays
in "held" with no date.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

API = "https://api-m.paypal.com"
_tokens: dict[str, tuple[float, str]] = {}
_feed: dict[str, tuple[float, list]] = {}

RESERVE_DAYS = int(os.getenv("PAYPAL_RESERVE_DAYS", "60"))
GENERAL_DAYS = int(os.getenv("PAYPAL_HOLD_DAYS", "21"))
HOLDS = {"T2103": ("T2104", RESERVE_DAYS, "PAYMENT_RESERVE_RELEASE"),
         "T2101": ("T2102", GENERAL_DAYS, "PAYPAL_HOLD_RELEASE")}


class PayPalError(RuntimeError):
    pass


def _explain(r: httpx.Response, what: str) -> PayPalError:
    try:
        body = r.json()
        msg = body.get("error_description") or body.get("message") or body.get("name") or r.text[:200]
    except ValueError:
        msg = r.text[:200]
    if r.status_code == 401:
        return PayPalError("PayPal didn't accept that Client ID and Secret. Check both were copied "
                           "in full from the Live tab (not Sandbox).")
    if r.status_code == 403:
        return PayPalError("PayPal refused access to balances. In the app on developer.paypal.com, "
                           "tick \"Transaction search\" under Features, save, then wait a few "
                           "minutes and try again.")
    return PayPalError(f"PayPal error {r.status_code} reading {what}: {msg}")


async def _token(client, client_id: str, secret: str) -> str:
    hit = _tokens.get(client_id)
    if hit and time.time() < hit[0]:
        return hit[1]
    try:
        r = await client.post(f"{API}/v1/oauth2/token", auth=(client_id, secret),
                              data={"grant_type": "client_credentials"})
    except httpx.RequestError as e:
        raise PayPalError(f"Could not reach PayPal ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r, "the login")
    body = r.json()
    # Reuse until 10 minutes before PayPal says it expires.
    _tokens[client_id] = (time.time() + max(60, int(body.get("expires_in", 3600)) - 600),
                          body.get("access_token"))
    return body.get("access_token")


def _amount(m) -> float:
    try:
        return float((m or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def releases(rows: list) -> list:
    """Holds not yet released, each with its expected release date."""
    released = {(t.get("transaction_event_code"), t.get("paypal_reference_id")) for t in rows}
    out = []
    for t in rows:
        rule = HOLDS.get(t.get("transaction_event_code"))
        ref = t.get("paypal_reference_id") or t.get("transaction_id")
        if not rule or (rule[0], ref) in released:
            continue
        amt = t.get("transaction_amount") or {}
        try:
            held = datetime.fromisoformat(t["transaction_initiation_date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        out.append({
            "currency": amt.get("currency_code"),
            "net": -float(amt.get("value") or 0),          # a hold is negative; its release is not
            "type": rule[2],
            "estimated": (held + timedelta(days=rule[1])).isoformat(),
            "created": held.isoformat(),
        })
    return out


def _fit(balances: list, pending: list) -> list:
    """Keep the schedule within what PayPal says is withheld, per currency.
    Earliest releases are already past their date in a few cases (PayPal pays
    them out late); never schedule more than is actually held."""
    held = {b["currency"]: b["reserved"] for b in balances}
    out, used = [], {}
    for p in sorted(pending, key=lambda p: p["estimated"], reverse=True):  # newest holds are surest
        room = held.get(p["currency"], 0.0) - used.get(p["currency"], 0.0)
        if room <= 0.005 or p["net"] <= 0:
            continue
        net = min(p["net"], room)
        used[p["currency"]] = used.get(p["currency"], 0.0) + net
        out.append({**p, "net": round(net, 2)})
    # Held money the feed hasn't caught up with yet (it lags a few hours):
    # assume the longer of today's rules from now, so it still counts as receivable.
    now = datetime.now(timezone.utc)
    for cur, total in held.items():
        rest = round(total - used.get(cur, 0.0), 2)
        if rest >= 0.01:
            out.append({"currency": cur, "net": rest, "type": "PAYPAL_HOLD_RELEASE",
                        "estimated": (now + timedelta(days=GENERAL_DAYS)).isoformat(),
                        "created": now.isoformat()})
    return sorted(out, key=lambda p: p["estimated"])


async def snapshot(client_id: str, secret: str) -> dict:
    """{"balances": [...], "pending": [...]} in the same shape as Airwallex."""
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _token(client, client_id, secret)
        try:
            r = await client.get(f"{API}/v1/reporting/balances",
                                 headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError as e:
            raise PayPalError(f"Could not reach PayPal ({type(e).__name__}).") from e
        if r.status_code == 401:
            _tokens.pop(client_id, None)
        if r.status_code >= 400:
            raise _explain(r, "balances")
        body = r.json()

    balances = [{
            "currency": b.get("currency"),
            "available": _amount(b.get("available_balance")),
            "pending": 0.0,
            "reserved": _amount(b.get("withheld_balance")),
            "total": _amount(b.get("total_balance")),
        } for b in body.get("balances", []) if b.get("currency")]
    try:
        rows = await transactions(client_id, secret)
    except (PayPalError, httpx.HTTPError):
        rows = []          # balances still show; holds just stay undated
    return {"balances": balances, "pending": _fit(balances, releases(rows))}


async def transactions(client_id: str, secret: str, days: int = 75, fields: str = "transaction_info",
                       full: bool = False) -> list:
    """Every transaction_info row from the last `days` days (31-day windows,
    PayPal's limit per search). Kept for 5 minutes; PayPal's feed itself
    lags by up to a few hours."""
    hit = _feed.get(client_id)
    if hit and time.time() < hit[0] and days == 75 and not full:
        return hit[1]
    end = datetime.now(timezone.utc) - timedelta(minutes=5)
    out = []
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _token(client, client_id, secret)
        start = end - timedelta(days=days)
        while start < end:
            stop = min(start + timedelta(days=31), end)
            page = 1
            while True:
                r = await client.get(f"{API}/v1/reporting/transactions", params={
                    "start_date": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "end_date": stop.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "fields": fields, "page_size": 500, "page": page,
                }, headers={"Authorization": f"Bearer {token}"})
                if r.status_code >= 400:
                    raise _explain(r, "transactions")
                body = r.json()
                out.extend((d if full else d.get("transaction_info", {}))
                           for d in body.get("transaction_details", []))
                if page >= int(body.get("total_pages") or 1) or page >= 40:
                    break
                page += 1
            start = stop
    if days == 75 and not full:
        _feed[client_id] = (time.time() + 300, out)
    return out


def payment_fees(rows: list) -> dict:
    """{Shopify payment id: {"fee", "currency", "amount"}} from the transaction feed:
    Shopify passes its payment id to PayPal as the invoice id."""
    out = {}
    for t in rows:
        code = t.get("transaction_event_code") or ""
        amt = t.get("transaction_amount") or {}
        if not code.startswith("T00") or _amount(amt) <= 0 or not t.get("invoice_id"):
            continue
        out[t["invoice_id"]] = {"fee": abs(_amount(t.get("fee_amount"))),
                                "currency": amt.get("currency_code"), "amount": _amount(amt)}
    return out
