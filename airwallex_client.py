"""Airwallex: current balances and money on its way, for the cash flow page.

Read-only. Uses a restricted API key (Client ID + API key) created in the
Airwallex web app with view access to Balances and Financial transactions.

    login                 POST /api/v1/authentication/login   (token lasts 30 min)
                          with x-login-as: <account id> when one key covers
                          several sub-accounts (each read separately)
    balances              GET  /api/v1/balances/current
    upcoming settlements  GET  /api/v1/financial_transactions?status=PENDING
                          each has net amount, currency and estimated_settled_at:
                          card / Afterpay payments not yet settled, payment
                          reserve releases (the held-back % paid out later),
                          pending refunds and payouts.
"""
import time
from datetime import datetime, timedelta, timezone

import httpx

API = "https://api.airwallex.com"
_tokens: dict[tuple, tuple[float, str]] = {}


class AirwallexError(RuntimeError):
    pass


def _explain(r: httpx.Response, what: str) -> AirwallexError:
    try:
        body = r.json()
        msg = body.get("message") or body.get("code") or r.text[:200]
    except ValueError:
        msg = r.text[:200]
    if r.status_code == 401:
        return AirwallexError("Airwallex didn't accept that Client ID and API key. Check both "
                              "were copied in full, or create a new restricted key.")
    if r.status_code == 403:
        return AirwallexError(f"Airwallex refused access to {what}. Give the API key view "
                              "access to Balances and Financial transactions.")
    return AirwallexError(f"Airwallex error {r.status_code} reading {what}: {msg}")


async def _token(client, client_id: str, api_key: str, account_id: str = None) -> str:
    hit = _tokens.get((client_id, account_id))
    if hit and time.time() < hit[0]:
        return hit[1]
    headers = {"x-client-id": client_id, "x-api-key": api_key}
    if account_id:
        headers["x-login-as"] = account_id
    try:
        r = await client.post(f"{API}/api/v1/authentication/login", headers=headers)
    except httpx.RequestError as e:
        raise AirwallexError(f"Could not reach Airwallex ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r, "the login")
    token = r.json().get("token")
    _tokens[(client_id, account_id)] = (time.time() + 25 * 60, token)  # reuse for 25 of its 30 minutes
    return token


async def _get(client, path, token, params=None, what="data"):
    try:
        r = await client.get(f"{API}{path}", params=params,
                             headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as e:
        raise AirwallexError(f"Could not reach Airwallex ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r, what)
    return r.json()


def _f(d: dict, snake: str):
    """Read a field whether Airwallex sent it as snake_case or camelCase; some
    accounts answer in one, some in the other."""
    if snake in d:
        return d[snake]
    head, *rest = snake.split("_")
    return d.get(head + "".join(w.title() for w in rest))


async def snapshot(client_id: str, api_key: str, lookback_days: int = 120,
                   account_id: str = None) -> dict:
    """{"balances": [...], "pending": [...]} straight from Airwallex.

    Reserve holds are released ~90 days after the payment, so pending items are
    searched from `lookback_days` ago to catch every one still waiting.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _token(client, client_id, api_key, account_id)
        balances = await _get(client, "/api/v1/balances/current", token, what="balances")

        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%dT%H:%M:%S+0000")
        pending, page = [], 0
        while True:
            body = await _get(client, "/api/v1/financial_transactions", token, {
                "status": "PENDING", "from_created_at": since,
                "page_num": page, "page_size": 1000,
            }, what="financial transactions")
            pending.extend(body.get("items", []))
            if not _f(body, "has_more") or page >= 50:
                break
            page += 1

    rows = balances if isinstance(balances, list) else balances.get("items", [])
    return {
        "balances": [{
            "currency": b.get("currency"),
            "available": float(_f(b, "available_amount") or 0),
            "pending": float(_f(b, "pending_amount") or 0),
            "reserved": float(_f(b, "reserved_amount") or 0),
            "total": float(_f(b, "total_amount") or 0),
        } for b in rows if b.get("currency")],
        "pending": [{
            "currency": t.get("currency"),
            "net": float(t.get("net") if t.get("net") is not None else t.get("amount") or 0),
            "type": _f(t, "transaction_type") or _f(t, "source_type") or "",
            "estimated": _f(t, "estimated_settled_at") or "",
            "created": _f(t, "created_at") or "",
        } for t in pending if t.get("currency")],
    }


async def transactions(client_id: str, api_key: str, since: datetime, until: datetime,
                       account_id: str = None) -> list:
    """Every financial transaction (settled and pending) created in [since, until)."""
    fmt = "%Y-%m-%dT%H:%M:%S+0000"
    out, page = [], 0
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _token(client, client_id, api_key, account_id)
        while True:
            body = await _get(client, "/api/v1/financial_transactions", token, {
                "from_created_at": since.astimezone(timezone.utc).strftime(fmt),
                "to_created_at": until.astimezone(timezone.utc).strftime(fmt),
                "page_num": page, "page_size": 1000,
            }, what="financial transactions")
            out.extend(body.get("items", []))
            if not _f(body, "has_more") or page >= 50:
                break
            page += 1
    return out


async def card_transactions(client_id: str, api_key: str, since: datetime,
                            account_id: str = None) -> list:
    """Card (Issuing) transactions since `since`: merchant name and card nickname
    for each card charge. Empty if the key can't see cards.

    Airwallex answers at most about a month per search (oldest first), so the
    period is read in 25-day windows."""
    fmt = "%Y-%m-%dT%H:%M:%S+0000"
    out = []
    now = datetime.now(timezone.utc)
    start = since.astimezone(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            token = await _token(client, client_id, api_key, account_id)
            while start < now:
                stop = min(start + timedelta(days=25), now)
                page = 0
                while page < 50:
                    body = await _get(client, "/api/v1/issuing/transactions", token, {
                        "from_created_at": start.strftime(fmt), "to_created_at": stop.strftime(fmt),
                        "page_num": page, "page_size": 200,
                    }, what="card transactions")
                    out.extend(body.get("items", []))
                    if not _f(body, "has_more"):
                        break
                    page += 1
                start = stop
    except AirwallexError:
        return out
    seen, unique = set(), []
    for i in out:                      # windows touch at their edges
        k = (i.get("transaction_id"), i.get("transaction_type"), i.get("status"))
        if k not in seen:
            seen.add(k)
            unique.append(i)
    return unique


async def payment_fees(client_id: str, api_key: str, since: datetime, account_id: str = None) -> dict:
    """{Shopify payment id: {"fee", "currency", "amount"}} for card / Afterpay
    payments since `since`. Each payment's fee is on its financial transaction;
    the payment attempt it comes from carries Shopify's payment id as
    merchant_order_id, which is what links it to the order."""
    now = datetime.now(timezone.utc)
    fin = await transactions(client_id, api_key, since, now, account_id)
    by_attempt = {}
    for t in fin:
        if (_f(t, "transaction_type") or "") != "PAYMENT":
            continue
        sid = _f(t, "source_id")
        if sid:
            by_attempt[sid] = {"fee": abs(float(t.get("fee") or 0)), "currency": t.get("currency"),
                               "amount": float(t.get("amount") or 0)}
    if not by_attempt:
        return {}
    order_of = {}
    fmt = "%Y-%m-%dT%H:%M:%S+0000"
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _token(client, client_id, api_key, account_id)
        start = (since - timedelta(days=1)).astimezone(timezone.utc)
        try:
            while start < now:
                stop = min(start + timedelta(days=25), now)
                page = 0
                while page < 100:
                    body = await _get(client, "/api/v1/pa/payment_attempts", token, {
                        "from_created_at": start.strftime(fmt), "to_created_at": stop.strftime(fmt),
                        "page_num": page, "page_size": 200}, what="payments")
                    for a in body.get("items", []):
                        if a.get("merchant_order_id"):
                            order_of[a["id"]] = a["merchant_order_id"]
                    if not _f(body, "has_more"):
                        break
                    page += 1
                start = stop
        except AirwallexError:
            pass
        # Anything the list missed, one by one (a handful at most).
        for sid in [s for s in by_attempt if s not in order_of][:150]:
            try:
                a = await _get(client, f"/api/v1/pa/payment_attempts/{sid}", token, what="a payment")
                if a.get("merchant_order_id"):
                    order_of[sid] = a["merchant_order_id"]
            except AirwallexError:
                continue
    return {order_of[s]: v for s, v in by_attempt.items() if s in order_of}
