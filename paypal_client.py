"""PayPal: current balances per currency, for the cash flow page.

Read-only. Uses a live REST app (Client ID + Secret) created at
developer.paypal.com with the "Transaction search" feature switched on.

    login      POST /v1/oauth2/token          (client credentials, ~9 h token)
    balances   GET  /v1/reporting/balances    available + withheld per currency

PayPal doesn't say when held (withheld) money will be released, so it is
shown as held rather than placed on a day in the schedule.
"""
import time

import httpx

API = "https://api-m.paypal.com"
_tokens: dict[str, tuple[float, str]] = {}


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


async def snapshot(client_id: str, secret: str) -> dict:
    """{"balances": [...], "pending": []} in the same shape as Airwallex."""
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

    return {
        "balances": [{
            "currency": b.get("currency"),
            "available": _amount(b.get("available_balance")),
            "pending": 0.0,
            "reserved": _amount(b.get("withheld_balance")),
            "total": _amount(b.get("total_balance")),
        } for b in body.get("balances", []) if b.get("currency")],
        "pending": [],
    }
