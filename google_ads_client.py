"""Google: sign in once, then read ad spend.

Sign-in is shared across every store. Each store then points at one ad account
from the dropdown.

Only one figure is read: account-level cost per day. No clicks, no impressions,
no conversions, no per-campaign breakdown.

The Google sign-in keys (OAuth client ID and secret) come from Settings, or
from .env. Google no longer requires a developer token: what decides whether
real ad accounts can be read is the Google Cloud project's API access level
(Explorer or above). A token in .env is still sent if present.
"""
import os

import db
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SCOPES = [
    "https://www.googleapis.com/auth/adwords",
    # Sending conversions (purchases, adds to cart) to Google Ads: Google's Data Manager API (gads_conv.py).
    "https://www.googleapis.com/auth/datamanager",
    "https://www.googleapis.com/auth/userinfo.email",
    # View-only, for the spend Sheet the Google Ads script fills (google_sheet.py).
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


class GoogleAdsError(RuntimeError):
    pass


def _cfg():
    return (
        db.get_setting("google_client_id") or os.getenv("GOOGLE_CLIENT_ID", ""),
        db.get_setting("google_client_secret") or os.getenv("GOOGLE_CLIENT_SECRET", ""),
        os.getenv("GOOGLE_DEVELOPER_TOKEN", ""),
        os.getenv("GOOGLE_ADS_API_VERSION", "v25"),
    )


def is_configured() -> bool:
    cid, secret, _, _ = _cfg()
    return bool(cid and secret)


def save_app(client_id: str, client_secret: str):
    db.set_setting("google_client_id", client_id)
    db.set_setting("google_client_secret", client_secret)


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
        "Content-Type": "application/json",
    }
    if dev_token:  # no longer required by Google, but harmless if set
        h["developer-token"] = dev_token
    if login_cid:
        h["login-customer-id"] = str(login_cid).replace("-", "")
    return h


def _why(r) -> str:
    """Google's own reason (errors[].message + error code), not the generic wrapper text."""
    try:
        err = r.json().get("error", {})
        out = []
        for d in err.get("details") or []:
            for e in d.get("errors") or []:
                code = "".join(f"{k}={v}" for k, v in (e.get("errorCode") or {}).items())
                out.append(f"{e.get('message', '')} ({code})".strip())
        return "; ".join(out)[:500] or err.get("message", "")[:300]
    except ValueError:
        return r.text[:300]


# Google Ads API operations per Pacific day (Basic access since 13 Oct 2026: 15,000; Explorer was 2,880). Each search page counts 1 and EVERY
# operation inside a mutate counts 1 (validateOnly included): a new campaign with 1,300 product variants costs ~1,350.
# Background jobs check busy() and wait when the day's allowance runs low, so pages people open keep working.
DAILY_LIMIT = 15000
RESERVE = 3000                   # kept for people opening pages and campaign changes
_ops = {"day": None, "n": 0}


def _pacific_day() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def ops_today() -> int:
    d = _pacific_day()
    if _ops["day"] != d:
        _ops["day"] = d
        try:
            _ops["n"] = int(db.get_setting(f"gads_ops:{d}") or 0)
        except ValueError:
            _ops["n"] = 0
    return _ops["n"]


def _count(n: int = 1):
    ops_today()
    before = _ops["n"]
    _ops["n"] += n
    if _ops["n"] // 10 != before // 10:
        db.set_setting(f"gads_ops:{_ops['day']}", str(_ops["n"]))


def left_today() -> int:
    return max(0, DAILY_LIMIT - ops_today())


def note_exhausted(text: str):
    """Google said the day's allowance is used up: remember until when (and that the day is full)."""
    import re as _re, time as _t
    m = _re.search(r"Retry in (\d+) seconds", text or "")
    _ops["blocked_until"] = _t.time() + (int(m.group(1)) if m else 3600)
    ops_today()
    if _ops["n"] < DAILY_LIMIT:
        _ops["n"] = DAILY_LIMIT
        db.set_setting(f"gads_ops:{_ops['day']}", str(_ops["n"]))


def busy() -> bool:
    """True when background work should wait: the day's requests are close to Google's limit (or Google said so)."""
    return ops_today() >= DAILY_LIMIT - RESERVE or _ops.get("blocked_until", 0) > __import__("time").time()


async def _search(client, version, customer_id, query, headers):
    url = f"https://googleads.googleapis.com/{version}/customers/{customer_id}/googleAds:search"
    rows, page = [], None
    while True:
        _count()
        body = {"query": query}                     # Google fixes the page size (10,000 rows)
        if page:
            body["pageToken"] = page
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 403:
            raise GoogleAdsError(
                "Google Ads refused the request. Usually the ProfitDesk Google Cloud "
                "project is still on Test access (Google Cloud Console > Google Ads API "
                f"> Access levels), or this Google login can't see that ad account. (Google: {_why(r)})"
            )
        if r.status_code == 429 or "RESOURCE_EXHAUSTED" in r.text[:2000]:
            import re as _re
            m = _re.search(r"Retry in (\d+) seconds", r.text)
            note_exhausted(r.text)
            raise GoogleAdsError("Google Ads' daily request allowance is used up; it resets within "
                                 f"{(int(m.group(1)) // 3600 + 1) if m else 1} hours. Saved figures are shown meanwhile.")
        if r.status_code >= 400:
            raise GoogleAdsError(f"Google Ads error {r.status_code}: {_why(r)}")
        payload = r.json()
        rows.extend(payload.get("results", []))
        page = payload.get("nextPageToken")
        if not page:
            return rows


async def list_accounts(refresh_token: str):
    """Every ad account this Google login can reach, flattened for the dropdown."""
    _, _, dev_token, version = _cfg()

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


async def _hourly(refresh_token, customer_id, login_cid, start, end, store_tz):
    """[(store-clock datetime, cost)] for each hour Google reports between start and end (store days).
    Google reports in the ad account's own time zone, so a day either side is read and shifted."""
    from datetime import date as _date, datetime as _dt, timedelta as _td
    from zoneinfo import ZoneInfo
    _, _, dev_token, version = _cfg()
    access = await _access_token(refresh_token)
    lo = (_date.fromisoformat(start) - _td(days=1)).isoformat()
    hi = (_date.fromisoformat(end) + _td(days=1)).isoformat()
    query = ("SELECT customer.time_zone, segments.date, segments.hour, metrics.cost_micros FROM customer "
             f"WHERE segments.date BETWEEN '{lo}' AND '{hi}'")
    cid = str(customer_id).replace("-", "")
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _search(client, version, cid, query, _headers(access, dev_token, login_cid or customer_id))
    out = []
    for r in rows:
        seg = r.get("segments", {})
        tz = ZoneInfo((r.get("customer") or {}).get("timeZone") or "UTC")
        try:
            t = _dt.fromisoformat(seg["date"]).replace(hour=int(seg.get("hour", 0)), tzinfo=tz)
        except (KeyError, ValueError):
            continue
        out.append((t.astimezone(ZoneInfo(store_tz)) if store_tz else t,
                    int(r.get("metrics", {}).get("costMicros", 0)) / 1_000_000))
    return out


async def spend_until_hour(refresh_token, customer_id, login_cid, day, hour, store_tz=None):
    """One store day's spend from midnight to the end of `hour` (0-23), on the store's clock
    (Google reports by the hour, so this is as fine as it gets)."""
    rows = await _hourly(refresh_token, customer_id, login_cid, day, day, store_tz)
    return sum(c for t, c in rows if t.date().isoformat() == day and t.hour <= hour)


async def daily_spend(refresh_token, customer_id, login_cid, start, end, store_tz=None):
    """Account-level cost per store day. Spend is the only thing we take from Google."""
    spend = {}
    for t, c in await _hourly(refresh_token, customer_id, login_cid, start, end, store_tz):
        d = t.date().isoformat()
        if start <= d <= end:
            spend[d] = spend.get(d, 0.0) + c
    return spend


# ---------------------------------------------------------------- Google Ads desk (Apps)

async def product_report(refresh_token, customer_id, login_cid, start, end):
    """Every Shopping / Performance Max product's daily figures (account's own days):
    [{date, item_id, title, impressions, clicks, cost, conversions, value}] plus the account currency.
    One request (pages of 10,000 rows) whatever the number of products."""
    _, _, dev_token, version = _cfg()
    access = await _access_token(refresh_token)
    query = ("SELECT customer.currency_code, segments.date, segments.product_item_id, segments.product_title, "
             "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value "
             f"FROM shopping_performance_view WHERE segments.date BETWEEN '{start}' AND '{end}'")
    cid = str(customer_id).replace("-", "")
    async with httpx.AsyncClient(timeout=120) as client:
        rows = await _search(client, version, cid, query, _headers(access, dev_token, login_cid or customer_id))
    out, currency = [], ""
    for r in rows:
        seg, m = r.get("segments", {}), r.get("metrics", {})
        currency = currency or (r.get("customer") or {}).get("currencyCode", "")
        out.append({"date": seg.get("date"), "item_id": seg.get("productItemId") or "",
                    "title": seg.get("productTitle") or "",
                    "impressions": int(m.get("impressions", 0) or 0), "clicks": int(m.get("clicks", 0) or 0),
                    "cost": int(m.get("costMicros", 0) or 0) / 1_000_000,
                    "conversions": float(m.get("conversions", 0) or 0),
                    "value": float(m.get("conversionsValue", 0) or 0)})
    return out, currency


async def account_daily(refresh_token, customer_id, login_cid, start, end):
    """Whole-account cost per account day (every campaign type), with the account's currency and clock:
    ({date: cost}, currency, tz). The Google Ads desk's total spend, matching Google's own reports."""
    _, _, dev_token, version = _cfg()
    access = await _access_token(refresh_token)
    query = ("SELECT customer.currency_code, customer.time_zone, segments.date, metrics.cost_micros FROM customer "
             f"WHERE segments.date BETWEEN '{start}' AND '{end}'")
    cid = str(customer_id).replace("-", "")
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _search(client, version, cid, query, _headers(access, dev_token, login_cid or customer_id))
    out, currency, tz = {}, "", "UTC"
    for r in rows:
        c, d = r.get("customer") or {}, (r.get("segments") or {}).get("date")
        currency, tz = currency or c.get("currencyCode", ""), c.get("timeZone") or tz
        if d:
            out[d] = out.get(d, 0.0) + int((r.get("metrics") or {}).get("costMicros", 0) or 0) / 1_000_000
    return out, currency, tz


async def campaign_spend(refresh_token, customer_id, login_cid, start, end):
    """Every campaign's cost between start and end (account days), with its type, plus the account's
    currency and clock: ([{campaign, type, status, cost, impressions, clicks, conversions, value}], currency, tz)."""
    _, _, dev_token, version = _cfg()
    access = await _access_token(refresh_token)
    query = ("SELECT customer.currency_code, customer.time_zone, campaign.name, campaign.advertising_channel_type, "
             "campaign.status, metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, "
             f"metrics.conversions_value FROM campaign WHERE segments.date BETWEEN '{start}' AND '{end}'")
    cid = str(customer_id).replace("-", "")
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _search(client, version, cid, query, _headers(access, dev_token, login_cid or customer_id))
    out, currency, tz = [], "", "UTC"
    for r in rows:
        c, camp, m = r.get("customer") or {}, r.get("campaign") or {}, r.get("metrics") or {}
        currency, tz = currency or c.get("currencyCode", ""), c.get("timeZone") or tz
        out.append({"campaign": camp.get("name") or "", "type": camp.get("advertisingChannelType") or "",
                    "status": camp.get("status") or "", "cost": int(m.get("costMicros", 0) or 0) / 1_000_000,
                    "impressions": int(m.get("impressions", 0) or 0), "clicks": int(m.get("clicks", 0) or 0),
                    "conversions": float(m.get("conversions", 0) or 0), "value": float(m.get("conversionsValue", 0) or 0)})
    return out, currency, tz


async def hourly_tab(refresh_token, customer_id, login_cid, start):
    """Spend by the hour since `start` (account days), shaped like a spend-Sheet tab, for Ads payable:
    {"meta": {"currency", "timezone"}, "rows": [(date, hour, cost)]}."""
    from datetime import date as _date
    _, _, dev_token, version = _cfg()
    access = await _access_token(refresh_token)
    query = ("SELECT customer.currency_code, customer.time_zone, segments.date, segments.hour, metrics.cost_micros "
             f"FROM customer WHERE segments.date BETWEEN '{start}' AND '{_date.today().isoformat()}'")
    cid = str(customer_id).replace("-", "")
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _search(client, version, cid, query, _headers(access, dev_token, login_cid or customer_id))
    meta, out = {"currency": "", "timezone": "UTC"}, []
    for r in rows:
        c = r.get("customer") or {}
        meta = {"currency": c.get("currencyCode") or meta["currency"], "timezone": c.get("timeZone") or meta["timezone"]}
        seg = r.get("segments", {})
        if seg.get("date"):
            out.append((seg["date"], int(seg.get("hour", 0)), int(r.get("metrics", {}).get("costMicros", 0) or 0) / 1_000_000))
    return {"meta": meta, "rows": out}
