"""Meta (Facebook + Instagram) Ads: paste one access token, then read ad spend.

Each token is a System User token from one Meta business's Business Settings.
Several businesses can be connected; each store points at one ad account,
read through whichever token can see it.

Only one figure is read: account-level spend per day. No clicks, no
impressions, no Meta-reported purchases or ROAS — those are Meta's own
attribution guesses, and this tool compares spend against real Shopify sales.
"""
import hashlib
import hmac
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

GRAPH = "https://graph.facebook.com"

# Meta error codes worth a plain-English explanation.
_EXPIRED = {190}
_NO_PERMISSION = {10, 200, 270, 294}
_RATE_LIMITED = {4, 17, 32, 613, 80000, 80004}


class MetaAdsError(RuntimeError):
    pass


def _version() -> str:
    return os.getenv("META_API_VERSION", "v26.0")


def _params(token: str, extra: dict = None) -> dict:
    """Every call carries the token, plus a signature if an app secret is set.

    The signature (appsecret_proof) is only required when the Meta app has
    "Require app secret" switched on, but it never hurts to send it.
    """
    p = {"access_token": token, **(extra or {})}
    secret = os.getenv("META_APP_SECRET", "")
    if secret:
        p["appsecret_proof"] = hmac.new(
            secret.encode(), token.encode(), hashlib.sha256
        ).hexdigest()
    return p


def _explain(r: httpx.Response) -> MetaAdsError:
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    code = err.get("code")
    msg = err.get("message", r.text[:200])
    if code in _EXPIRED:
        return MetaAdsError(
            "Meta did not accept that access token. Check the whole token was copied, "
            "or make a new System User token in Business Settings and paste it into "
            "Settings > Meta Ads."
        )
    if code in _NO_PERMISSION:
        return MetaAdsError(
            "Meta refused access. Check the System User has this ad account assigned "
            "and the token includes the ads_read permission."
        )
    if code in _RATE_LIMITED:
        return MetaAdsError("Meta is rate-limiting requests right now. Wait a few minutes and press Refresh.")
    return MetaAdsError(f"Meta Ads error {code or r.status_code}: {msg}")


async def _get_all(client, path, token, params):
    """GET a Graph API list and follow every page."""
    url = f"{GRAPH}/{_version()}/{path}"
    query = _params(token, params)
    rows = []
    while url:
        try:
            r = await client.get(url, params=query)
        except httpx.RequestError as e:
            raise MetaAdsError(f"Could not reach Meta ({type(e).__name__}).") from e
        if r.status_code >= 400:
            raise _explain(r)
        body = r.json()
        rows.extend(body.get("data", []))
        url = body.get("paging", {}).get("next")
        query = None  # the "next" link already carries every parameter
    return rows


# ---------------------------------------------------------------- connection

async def whoami(token: str) -> dict:
    """Check a pasted token works. Returns Meta's id and name for it."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(f"{GRAPH}/{_version()}/me",
                                 params=_params(token, {"fields": "id,name"}))
        except httpx.RequestError as e:
            raise MetaAdsError(f"Could not reach Meta ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r)
    me = r.json()
    return {"id": str(me.get("id", "")), "name": me.get("name") or "Meta user"}


async def list_accounts(token: str):
    """Every ad account this token can read, for the dropdown."""
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _get_all(client, "me/adaccounts", token, {
            "fields": "name,account_id,currency,account_status,timezone_name",
            "limit": 200,
        })
    accounts = [{
        "account_id": str(a.get("account_id", "")),
        "name": a.get("name") or f"Account {a.get('account_id')}",
        "currency": a.get("currency", ""),
        "active": a.get("account_status") == 1,
        "timezone": a.get("timezone_name", ""),
    } for a in rows if a.get("account_id")]
    accounts.sort(key=lambda a: a["name"].lower())
    return accounts


# ---------------------------------------------------------------- reporting

_HOURLY = "hourly_stats_aggregated_by_advertiser_time_zone"


def _zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


async def account_timezone(token: str, account_id: str) -> str:
    acct = str(account_id).removeprefix("act_")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(f"{GRAPH}/{_version()}/act_{acct}",
                                 params=_params(token, {"fields": "timezone_name"}))
        except httpx.RequestError as e:
            raise MetaAdsError(f"Could not reach Meta ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r)
    return r.json().get("timezone_name") or "UTC"


async def _hourly(token, account_id, since, until):
    """Spend per hour as [(start of hour as an aware datetime, spend)], read on
    the ad account's own clock."""
    acct = str(account_id).removeprefix("act_")
    tz = _zone(await account_timezone(token, acct))
    until = min(until, datetime.now(tz).date().isoformat())
    if since > until:
        return []
    async with httpx.AsyncClient(timeout=90) as client:
        rows = await _get_all(client, f"act_{acct}/insights", token, {
            "level": "account",
            "fields": "spend",
            "breakdowns": _HOURLY,
            "time_range": json.dumps({"since": since, "until": until}),
            "time_increment": 1,
            "limit": 500,
        })
    out = []
    for row in rows:
        slot = row.get(_HOURLY, "")          # e.g. "09:00:00 - 09:59:59"
        d = row.get("date_start")
        if d and slot[:2].isdigit():
            local = datetime.fromisoformat(d).replace(hour=int(slot[:2]), tzinfo=tz)
            out.append((local, float(row.get("spend") or 0)))
    return out


def _widen(start: str, end: str):
    """Account days that can overlap the store's days, whatever the two clocks."""
    return ((date.fromisoformat(start) - timedelta(days=1)).isoformat(),
            (date.fromisoformat(end) + timedelta(days=1)).isoformat())


async def spend_until_hour(token: str, account_id: str, day: str, hour: int,
                           store_tz: str = None):
    """One store day's spend from midnight to the end of `hour` (0-23), on the
    store's clock. Meta reports by the hour, so this is as fine as it gets."""
    tz = _zone(store_tz)
    since, until = _widen(day, day)
    total = 0.0
    for when, spend in await _hourly(token, account_id, since, until):
        local = when.astimezone(tz)
        if local.date().isoformat() == day and local.hour <= hour:
            total += spend
    return total


async def daily_spend(token: str, account_id: str, start: str, end: str,
                      store_tz: str = None, account_tz: str = None):
    """Account-level spend per day, on the store's clock.

    When the ad account keeps the same clock as the store (the usual case),
    Meta's own daily figures are used. When the clocks differ, spend is read by
    the hour and each hour is moved onto the store's day it belongs to, so a
    Berlin-time ad account still lines up with a Melbourne-time store.
    """
    if store_tz and account_tz and _zone(store_tz).key != _zone(account_tz).key:
        tz = _zone(store_tz)
        since, until = _widen(start, end)
        spend = {}
        for when, amount in await _hourly(token, account_id, since, until):
            d = when.astimezone(tz).date().isoformat()
            if start <= d <= end:
                spend[d] = spend.get(d, 0.0) + amount
        return spend
    return await _daily_on_account_clock(token, account_id, start, end)


async def _daily_on_account_clock(token: str, account_id: str, start: str, end: str):
    """Meta's own daily figures, in the ad account's currency and timezone."""
    acct = str(account_id).removeprefix("act_")
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _get_all(client, f"act_{acct}/insights", token, {
            "level": "account",
            "fields": "spend",
            "time_range": json.dumps({"since": start, "until": end}),
            "time_increment": 1,
            "limit": 500,
        })
    spend = {}
    for row in rows:
        d = row.get("date_start")
        if d:
            spend[d] = spend.get(d, 0.0) + float(row.get("spend") or 0)
    return spend
