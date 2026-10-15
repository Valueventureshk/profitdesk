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


async def billing(token: str) -> dict:
    """{account_id: {name, currency, account_status, balance}} for every ad account
    this token can read. balance = spend owed, not yet charged, in cents."""
    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _get_all(client, "me/adaccounts", token, {
            "fields": "name,account_id,currency,account_status,balance", "limit": 200,
        })
    return {str(a.get("account_id")): a for a in rows if a.get("account_id")}


async def level_spend(token: str, account_id: str, since: str, until: str, level: str, hour: int | None = None) -> list:
    """Spend per campaign / ad set / ad over since..until (ad account days):
    [{"id", "name", "campaign", "adset", "spend", "impressions", "clicks"}]. For the Tracking report.
    With hour (0-23, ad account clock): only that hour of the day (Meta's hourly breakdown)."""
    level = level if level in ("campaign", "adset", "ad") else "campaign"
    acct = str(account_id).removeprefix("act_")
    fields = {"campaign": "campaign_id,campaign_name", "adset": "adset_id,adset_name,campaign_name",
              "ad": "ad_id,ad_name,adset_name,campaign_name"}[level] + ",spend,impressions,inline_link_clicks"
    async with httpx.AsyncClient(timeout=60) as client:
        q = {"level": level, "fields": fields, "limit": 500, "time_range": json.dumps({"since": since, "until": until})}
        if hour is not None:
            q["breakdowns"] = "hourly_stats_aggregated_by_advertiser_time_zone"
        rows = await _get_all(client, f"act_{acct}/insights", token, q)
    if hour is not None:
        rows = [r for r in rows if str(r.get("hourly_stats_aggregated_by_advertiser_time_zone") or "").startswith(f"{hour:02d}:")]
    return [{"id": r.get(f"{level}_id"), "name": r.get(f"{level}_name") or "", "campaign": r.get("campaign_name") or "",
             "adset": r.get("adset_name") or "", "spend": float(r.get("spend") or 0),
             "impressions": int(r.get("impressions") or 0), "clicks": int(r.get("inline_link_clicks") or 0)}
            for r in rows]


# ---------------------------------------------------------------- creating ads (Tasks desk → Launch)
# Everything is created PAUSED: an admin checks it in Ads Manager and switches it on there.
# Needs the ads_management permission on the System User token.

_ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF", "TWD", "COP", "IDR", "PYG", "CRC"}


def minor_units(amount: float, currency: str) -> int:
    """Meta takes budgets in the account currency's smallest unit (cents)."""
    return int(round(float(amount) * (1 if (currency or "").upper() in _ZERO_DECIMAL else 100)))


def _explain_write(r: httpx.Response) -> MetaAdsError:
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    if err.get("code") in _NO_PERMISSION or err.get("code") == 100 and "permission" in str(err).lower():
        return MetaAdsError("Meta refused to create ads. The System User token needs the ads_management "
                            "permission and the ad account (and Facebook Page) assigned to it.")
    detail = err.get("error_user_msg") or err.get("error_user_title") or err.get("message") or r.text[:300]
    return MetaAdsError(f"Meta said: {detail}")


async def _post(client, path: str, token: str, data: dict, files=None) -> dict:
    body = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in data.items()}
    try:
        r = await client.post(f"{GRAPH}/{_version()}/{path}", data=_params(token, body), files=files)
    except httpx.RequestError as e:
        raise MetaAdsError(f"Could not reach Meta ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain_write(r)
    return r.json()


async def _get(client, path: str, token: str, params: dict) -> dict:
    try:
        r = await client.get(f"{GRAPH}/{_version()}/{path}", params=_params(token, params))
    except httpx.RequestError as e:
        raise MetaAdsError(f"Could not reach Meta ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r)
    return r.json()


async def launch_options(token: str, account_id: str) -> dict:
    """Facebook Pages and pixels the ad account can use, for the Launch form."""
    act = f"act_{account_id}"
    out = {"pages": [], "pixels": [], "errors": []}
    async with httpx.AsyncClient(timeout=30) as client:
        for key, path, fields in (("pages", f"{act}/promote_pages", "id,name"),
                                  ("pixels", f"{act}/adspixels", "id,name")):
            try:
                rows = await _get_all(client, path, token, {"fields": fields, "limit": 100})
                out[key] = [{"id": str(x["id"]), "name": x.get("name") or x["id"]} for x in rows]
            except MetaAdsError as e:
                if str(e) not in out["errors"]:
                    out["errors"].append(str(e))
    return out


async def upload_image(client, token: str, account_id: str, data: bytes, name: str) -> str:
    got = await _post(client, f"act_{account_id}/adimages", token, {}, files={"filename": (name, data)})
    images = got.get("images") or {}
    first = next(iter(images.values()), {})
    if not first.get("hash"):
        raise MetaAdsError(f"Meta didn't take the image {name}.")
    return first["hash"]


async def upload_video(client, token: str, account_id: str, path: str, name: str) -> str:
    with open(path, "rb") as fh:
        got = await _post(client, f"act_{account_id}/advideos", token, {"name": name},
                          files={"source": (name, fh)})
    if not got.get("id"):
        raise MetaAdsError(f"Meta didn't take the video {name}.")
    return str(got["id"])


async def video_thumbnail(client, token: str, video_id: str):
    """Meta's preferred still of an uploaded video, or None while it's still processing."""
    got = await _get(client, f"{video_id}", token, {"fields": "status,thumbnails{uri,is_preferred}"})
    if (got.get("status") or {}).get("video_status") not in (None, "ready"):
        return None
    thumbs = (got.get("thumbnails") or {}).get("data") or []
    pick = next((t for t in thumbs if t.get("is_preferred")), thumbs[0] if thumbs else None)
    return pick.get("uri") if pick else None


async def create_campaign(client, token, account_id, name, validate=False, daily_minor=None) -> str:
    """daily_minor set = campaign budget (CBO): Meta shares it across the ad sets."""
    data = {"name": name, "objective": "OUTCOME_SALES", "status": "PAUSED", "special_ad_categories": []}
    if daily_minor:
        data.update({"daily_budget": daily_minor, "bid_strategy": "LOWEST_COST_WITHOUT_CAP"})
    else:
        data["is_adset_budget_sharing_enabled"] = "false"
    if validate:
        data["execution_options"] = ["validate_only"]
    got = await _post(client, f"act_{account_id}/campaigns", token, data)
    return str(got.get("id") or "")


async def create_adset(client, token, account_id, campaign_id, name, daily_minor, pixel_id, countries,
                       validate=False) -> str:
    data = {"name": name, "campaign_id": campaign_id, "status": "PAUSED",
            "billing_event": "IMPRESSIONS", "optimization_goal": "OFFSITE_CONVERSIONS",
            "promoted_object": {"pixel_id": pixel_id, "custom_event_type": "PURCHASE"},
            "targeting": {"geo_locations": {"countries": countries},
                          "targeting_automation": {"advantage_audience": 1}}}
    if daily_minor:                                  # None = the campaign holds the budget (CBO)
        data.update({"daily_budget": daily_minor, "bid_strategy": "LOWEST_COST_WITHOUT_CAP"})
    if validate:
        data["execution_options"] = ["validate_only"]
    got = await _post(client, f"act_{account_id}/adsets", token, data)
    return str(got.get("id") or "")


async def create_ad(client, token, account_id, adset_id, name, page_id, link, message, headline,
                    description, cta, image_hash=None, video_id=None, thumb_url=None) -> dict:
    call = {"type": cta or "SHOP_NOW", "value": {"link": link}}
    if video_id:
        story = {"page_id": page_id, "video_data": {
            "video_id": video_id, "image_url": thumb_url, "message": message, "title": headline,
            "link_description": description or "", "call_to_action": call}}
    else:
        story = {"page_id": page_id, "link_data": {
            "image_hash": image_hash, "link": link, "message": message, "name": headline,
            "description": description or "", "call_to_action": call}}
    creative = await _post(client, f"act_{account_id}/adcreatives", token,
                           {"name": name, "object_story_spec": story})
    ad = await _post(client, f"act_{account_id}/ads", token,
                     {"name": name, "adset_id": adset_id, "creative": {"creative_id": creative["id"]},
                      "status": "PAUSED"})
    return {"creative_id": str(creative["id"]), "ad_id": str(ad["id"])}
