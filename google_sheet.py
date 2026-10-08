"""Google Ads spend from a Google Sheet, filled by google-ads-script.js.

Until the Google Cloud project is approved to call the Google Ads API directly,
a small script inside each Google Ads account copies its spend into one shared
Sheet every hour (one tab per ad account). ProfitDesk reads that Sheet through
the same Google sign-in, with view-only Sheets access, which needs no approval.

Tab layout (see google-ads-script.js):
    row 1   ProfitDesk | customer ID | account name | currency | time zone | updated
    row 2   date | hour | cost
    rows    YYYY-MM-DD | "day" or 0-23 | spend in the account's currency
"""
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote

import httpx

import db
import google_ads_client as gads

API = "https://sheets.googleapis.com/v4/spreadsheets"
CACHE_TTL = 300  # the script only writes hourly, so five minutes is plenty

_tabs: dict[str, tuple[float, dict]] = {}


class SheetError(RuntimeError):
    pass


def sheet_id() -> str:
    return db.get_setting("google_sheet_id") or ""


def save_sheet(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", url_or_id or "")
    sid = m.group(1) if m else (url_or_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", sid):
        raise SheetError("That doesn't look like a Google Sheet link. Copy it from the "
                         "browser's address bar while the Sheet is open.")
    db.set_setting("google_sheet_id", sid)
    _tabs.clear()
    return sid


async def _get(client, url, access, params=None):
    try:
        r = await client.get(url, params=params, headers={"Authorization": f"Bearer {access}"})
    except httpx.RequestError as e:
        raise SheetError(f"Could not reach Google Sheets ({type(e).__name__}).") from e
    if r.status_code in (401, 403):
        raise SheetError(
            "Google wouldn't let ProfitDesk read the spend Sheet. Sign in with Google "
            "again (Settings → Google Ads → Disconnect, then Sign in), using the account "
            "that owns the Sheet, and make sure the Google Sheets API is turned on in "
            "the ProfitDesk Google Cloud project.")
    if r.status_code == 404:
        raise SheetError("The spend Sheet wasn't found. Check the link in Settings → Google Ads.")
    if r.status_code >= 400:
        raise SheetError(f"Google Sheets error {r.status_code}: {r.text[:200]}")
    return r.json()


async def _read_all(refresh_token: str) -> dict:
    """{customer_id: {"meta": {...}, "rows": [(date, hour, cost)]}} for every tab."""
    sid = sheet_id()
    if not sid:
        raise SheetError("No spend Sheet is set yet (Settings → Google Ads).")
    hit = _tabs.get(sid)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    access = await gads._access_token(refresh_token)
    async with httpx.AsyncClient(timeout=60) as client:
        info = await _get(client, f"{API}/{sid}", access, {"fields": "sheets.properties.title"})
        titles = [s["properties"]["title"] for s in info.get("sheets", [])]
        if not titles:
            return {}
        ranges = [f"'{t}'!A1:F" for t in titles]
        url = f"{API}/{sid}/values:batchGet?" + "&".join(f"ranges={quote(r)}" for r in ranges)
        got = await _get(client, url, access)

    tabs = {}
    for vr in got.get("valueRanges", []):
        values = vr.get("values", [])
        if not values or not values[0] or values[0][0] != "ProfitDesk":
            continue  # not a tab the script wrote
        head = values[0] + [""] * 6
        rows = []
        for v in values[2:]:
            if len(v) >= 3 and v[0]:
                try:
                    rows.append((v[0], v[1], float(str(v[2]).replace(",", ""))))
                except ValueError:
                    continue
        tabs[head[1]] = {
            "meta": {"customer_id": head[1], "name": head[2], "currency": head[3],
                     "timezone": head[4], "updated": head[5]},
            "rows": rows,
        }
    _tabs[sid] = (time.time(), tabs)
    return tabs


async def list_accounts(refresh_token: str):
    """Ad accounts the Sheet has data for, shaped like the API account list."""
    tabs = await _read_all(refresh_token)
    return sorted(({
        "customer_id": cid.replace("-", ""),
        "display_id": cid,
        "login_customer_id": None,
        "name": t["meta"]["name"] or cid,
        "currency": t["meta"]["currency"],
        "timezone": t["meta"]["timezone"],
        "updated": t["meta"]["updated"],
        "source": "sheet",
    } for cid, t in tabs.items()), key=lambda a: a["name"].lower())


def _zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


async def _tab(refresh_token, customer_id):
    tabs = await _read_all(refresh_token)
    want = str(customer_id).replace("-", "")
    for cid, t in tabs.items():
        if cid.replace("-", "") == want:
            return t
    raise SheetError(f"The spend Sheet has no tab for Google Ads account {customer_id} yet. "
                     "Check the script in that account has run.")


async def daily_spend(refresh_token, customer_id, start, end, store_tz=None):
    """Spend per day on the store's clock.

    Same clock as the ad account: the script's daily totals. Different clock:
    hourly figures are moved onto the store's day where the Sheet has them
    (the last few days); older days use the account's daily totals.
    """
    t = await _tab(refresh_token, customer_id)
    acct_tz = t["meta"]["timezone"]
    daily = {d: c for d, h, c in t["rows"] if h == "day"}
    if not store_tz or _zone(store_tz).key == _zone(acct_tz).key:
        return {d: c for d, c in daily.items() if start <= d <= end}

    a, s = _zone(acct_tz), _zone(store_tz)
    hourly_days = {d for d, h, c in t["rows"] if h != "day"}
    out = {d: c for d, c in daily.items() if start <= d <= end and d not in hourly_days}
    for d, h, c in t["rows"]:
        if h == "day":
            continue
        local = datetime.fromisoformat(d).replace(hour=int(h), tzinfo=a).astimezone(s)
        key = local.date().isoformat()
        if start <= key <= end:
            out[key] = out.get(key, 0.0) + c
    return out


async def spend_until_hour(refresh_token, customer_id, day, hour, store_tz=None):
    """One store-day's spend from midnight to the end of `hour`, store clock."""
    t = await _tab(refresh_token, customer_id)
    a, s = _zone(t["meta"]["timezone"]), _zone(store_tz)
    total = 0.0
    for d, h, c in t["rows"]:
        if h == "day":
            continue
        local = datetime.fromisoformat(d).replace(hour=int(h), tzinfo=a).astimezone(s)
        if local.date().isoformat() == day and local.hour <= hour:
            total += c
    return total


# ---------------------------------------------------------------- any sheet (orders / COG)

async def read_tabs(refresh_token: str, sid: str, rows: int = None) -> dict:
    """{tab title: [[cell, ...], ...]} for every tab of any Sheet the signed-in
    Google account can view. `rows` limits each tab to its first N rows."""
    access = await gads._access_token(refresh_token)
    async with httpx.AsyncClient(timeout=120) as client:
        info = await _get(client, f"{API}/{sid}",
                          access, {"fields": "properties.title,sheets.properties(title,gridProperties)"})
        titles = [s["properties"]["title"] for s in info.get("sheets", [])]
        if not titles:
            return {}
        end = f"{rows}" if rows else ""
        ranges = [f"'{t}'!A1:Z{end}" for t in titles]
        url = f"{API}/{sid}/values:batchGet?" + "&".join(f"ranges={quote(r)}" for r in ranges)
        got = await _get(client, url + "&valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING", access)
    return {t: vr.get("values", []) for t, vr in zip(titles, got.get("valueRanges", []))}
