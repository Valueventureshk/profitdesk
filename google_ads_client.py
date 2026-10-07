"""Google: sign in once, then read ad spend.

Sign-in is shared across every store. Each store then points at one ad account
from the dropdown.

Only one figure is read: account-level cost per day. No clicks, no impressions,
no conversions, no per-campaign breakdown.
"""
import os
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SCOPES = [
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GoogleAdsError(RuntimeError):
    pass


def _cfg():
    return (
        os.getenv("GOOGLE_CLIENT_ID", ""),
        os.getenv("GOOGLE_CLIENT_SECRET", ""),
        os.getenv("GOOGLE_DEVELOPER_TOKEN", ""),
        os.getenv("GOOGLE_ADS_API_VERSION", "v25"),
    )


def is_configured() -> bool:
    cid, secret, _, _ = _cfg()
    return bool(cid and secret)


def has_developer_token() -> bool:
    return bool(os.getenv("GOOGLE_DEVELOPER_TOKEN", ""))


# ---------------------------------------------------------------- sign-in

def build_auth_url(redirect_uri: str, state: str) -> str:
    cid, _, _, _ = _cfg()
    if not cid:
        raise GoogleAdsError("GOOGLE_CLIENT_ID is not set in .env")
    return AUTH_URL + "?" + urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


async def exchange_code(code: str, redirect_uri: str):
    cid, secret, _, _ = _cfg()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if r.status_code != 200:
            raise GoogleAdsError(f"Google sign-in failed: {r.text[:300]}")
        tokens = r.json()

        email = None
        if tokens.get("access_token"):
            u = await client.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if u.status_code == 200:
                email = u.json().get("email")

    if not tokens.get("refresh_token"):
        raise GoogleAdsError(
            "Google did not send back a lasting connection. Remove ProfitDesk at "
            "myaccount.google.com/permissions and sign in again."
        )
    return tokens["refresh_token"], email


async def _access_token(refresh_token: str) -> str:
    cid, secret, _, _ = _cfg()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(TOKEN_URL, data={
                "refresh_token": refresh_token,
                "client_id": cid,
                "client_secret": secret,
                "grant_type": "refresh_token",
            })
    except httpx.RequestError as e:
        raise GoogleAdsError(
            f"Could not reach Google to refresh the connection ({type(e).__name__})."
        ) from e
    if r.status_code != 200:
        raise GoogleAdsError("Google sign-in expired. Connect Google again in Settings.")
    return r.json()["access_token"]


# ---------------------------------------------------------------- reporting

def _headers(access_token, dev_token, login_cid=None):
    h = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": dev_token,
        "Content-Type": "application/json",
    }
    if login_cid:
        h["login-customer-id"] = str(login_cid).replace("-", "")
    return h


async def _search(client, version, customer_id, query, headers):
    url = f"https://googleads.googleapis.com/{version}/customers/{customer_id}/googleAds:search"
    rows, page = [], None
    while True:
        body = {"query": query, "pageSize": 10000}
        if page:
            body["pageToken"] = page
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 403:
            raise GoogleAdsError(
                "Google Ads refused the request. Usually the developer token is still "
                "limited to test accounts, or this Google login has no access to that "
                "ad account."
            )
        if r.status_code >= 400:
            raise GoogleAdsError(f"Google Ads error {r.status_code}: {r.text[:300]}")
        payload = r.json()
        rows.extend(payload.get("results", []))
        page = payload.get("nextPageToken")
        if not page:
            return rows


async def list_accounts(refresh_token: str):
    """Every ad account this Google login can reach, flattened for the dropdown."""
    _, _, dev_token, version = _cfg()
    if not dev_token:
        raise GoogleAdsError(
            "No Google Ads developer token yet. Add GOOGLE_DEVELOPER_TOKEN to .env "
            "once Google approves yours."
        )

    access = await _access_token(refresh_token)
    accounts, seen = [], set()

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"https://googleads.googleapis.com/{version}/customers:listAccessibleCustomers",
            headers=_headers(access, dev_token),
        )
        if r.status_code >= 400:
            raise GoogleAdsError(f"Could not list ad accounts ({r.status_code}): {r.text[:300]}")
        roots = [n.split("/")[-1] for n in r.json().get("resourceNames", [])]

        tree = """
            SELECT customer_client.id, customer_client.descriptive_name,
                   customer_client.manager, customer_client.currency_code,
                   customer_client.status
            FROM customer_client
            WHERE customer_client.status = 'ENABLED'
        """
        for root in roots:
            try:
                rows = await _search(client, version, root, tree,
                                     _headers(access, dev_token, root))
            except GoogleAdsError:
                continue  # one unreachable manager shouldn't empty the whole list
            for row in rows:
                cc = row.get("customerClient", {})
                cid = str(cc.get("id", ""))
                if not cid or cid in seen or cc.get("manager"):
                    continue
                seen.add(cid)
                accounts.append({
                    "customer_id": cid,
                    "login_customer_id": root if root != cid else None,
                    "name": cc.get("descriptiveName") or f"Account {cid}",
                    "currency": cc.get("currencyCode", ""),
                    "display_id": f"{cid[:3]}-{cid[3:6]}-{cid[6:]}" if len(cid) == 10 else cid,
                })

    accounts.sort(key=lambda a: a["name"].lower())
    return accounts


async def spend_until_hour(refresh_token, customer_id, login_cid, day, hour):
    """One day's spend from midnight to the end of `hour` (0-23), in the ad
    account's timezone. Google reports by the hour, so this is as fine as it gets."""
    _, _, dev_token, version = _cfg()
    if not dev_token:
        raise GoogleAdsError("No Google Ads developer token set.")

    access = await _access_token(refresh_token)
    query = (
        "SELECT segments.hour, metrics.cost_micros FROM customer "
        f"WHERE segments.date = '{day}'"
    )
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _search(client, version, str(customer_id).replace("-", ""),
                             query, _headers(access, dev_token, login_cid))
    return sum(
        int(r.get("metrics", {}).get("costMicros", 0)) / 1_000_000
        for r in rows
        if int(r.get("segments", {}).get("hour", 99)) <= hour
    )


async def daily_spend(refresh_token, customer_id, login_cid, start, end):
    """Account-level cost per day. Spend is the only thing we take from Google."""
    _, _, dev_token, version = _cfg()
    if not dev_token:
        raise GoogleAdsError("No Google Ads developer token set.")

    access = await _access_token(refresh_token)
    query = (
        "SELECT segments.date, metrics.cost_micros FROM customer "
        f"WHERE segments.date BETWEEN '{start}' AND '{end}'"
    )
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _search(client, version, str(customer_id).replace("-", ""),
                             query, _headers(access, dev_token, login_cid))

    spend = {}
    for row in rows:
        d = row.get("segments", {}).get("date")
        if d:
            spend[d] = int(row.get("metrics", {}).get("costMicros", 0)) / 1_000_000
    return spend
