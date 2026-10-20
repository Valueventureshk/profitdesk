"""Apify: runs the Facebook Ad Library scraper (Curious Coder's actor) for the Research desk.

    start   POST /v2/acts/<actor>/runs           input JSON, returns the run (id, status, dataset)
    wait    GET  /v2/actor-runs/<id>?waitForFinish=60   repeated until it ends
    items   GET  /v2/datasets/<id>/items         the ads
    me      GET  /v2/users/me, /v2/users/me/limits     who the key belongs to, this month's usage

The key is the owner's Apify API token (Apify → Settings → API & Integrations), pasted on the Research
page, kept in setting `apify_token`, never sent back to the browser or written in chat.
"""
import asyncio
import time

import httpx

API = "https://api.apify.com/v2"
ACTOR = "curious_coder~facebook-ads-library-scraper"
DONE = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(RuntimeError):
    pass


def _explain(r: httpx.Response, what: str) -> ApifyError:
    try:
        err = (r.json() or {}).get("error") or {}
        msg = err.get("message") or r.text[:200]
    except ValueError:
        msg = r.text[:200]
    if r.status_code == 401:
        return ApifyError("Apify didn't accept that key. Copy the API token again from Apify → Settings → API & Integrations.")
    if r.status_code == 402 or "credit" in msg.lower() or "usage" in msg.lower() and "limit" in msg.lower():
        return ApifyError(f"Apify says the account is out of credit for this month ({msg}).")
    return ApifyError(f"Apify error {r.status_code} on {what}: {msg}")


async def _get(client, token: str, path: str, **params):
    try:
        r = await client.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as e:
        raise ApifyError(f"Couldn't reach Apify ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise _explain(r, path)
    return r.json()


async def me(token: str) -> dict:
    """{username, plan, used_usd, limit_usd} (usage figures when Apify gives them)."""
    async with httpx.AsyncClient(timeout=30) as client:
        user = (await _get(client, token, "/users/me")).get("data") or {}
        try:
            lim = (await _get(client, token, "/users/me/limits")).get("data") or {}
        except ApifyError:
            lim = {}
    cur, limits = lim.get("current") or {}, lim.get("limits") or {}
    return {"username": user.get("username") or "", "plan": ((user.get("plan") or {}).get("id") or ""),
            "used_usd": cur.get("monthlyUsageUsd"), "limit_usd": limits.get("maxMonthlyUsageUsd")}


async def run_actor(token: str, payload: dict, max_wait: int = 900) -> tuple:
    """Start the scraper, wait for it to end, return (run, items). Raises ApifyError when it fails."""
    async with httpx.AsyncClient(timeout=90) as client:
        try:
            r = await client.post(f"{API}/acts/{ACTOR}/runs", params={"waitForFinish": 60}, json=payload,
                                  headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError as e:
            raise ApifyError(f"Couldn't reach Apify ({type(e).__name__}).") from e
        if r.status_code >= 400:
            raise _explain(r, "starting the scraper")
        run = r.json().get("data") or {}
        end = time.time() + max_wait
        while run.get("status") not in DONE and time.time() < end:
            run = (await _get(client, token, f"/actor-runs/{run['id']}", waitForFinish=60)).get("data") or run
            if run.get("status") not in DONE:
                await asyncio.sleep(2)
        if run.get("status") not in DONE:
            raise ApifyError("The scraper is taking more than 15 minutes; it carries on at Apify. Try again later.")
        if run.get("status") != "SUCCEEDED":
            raise ApifyError(f"The scraper ended with status {run.get('status')}: {run.get('statusMessage') or ''}".strip())
        items, offset = [], 0
        while True:
            page = await _get(client, token, f"/datasets/{run['defaultDatasetId']}/items",
                              clean="true", format="json", offset=offset, limit=1000)
            if not isinstance(page, list) or not page:
                break
            items += page
            offset += len(page)
            if len(page) < 1000 or offset >= 20000:
                break
    return run, items


def page_input(url: str, country: str = "ALL", cap: int = 500) -> dict:
    """The scraper's input for one Facebook page (or an Ad Library link): its active ads."""
    return {"urls": [{"url": url}], "count": cap, "limitPerSource": cap, "scrapeAdDetails": False,
            "scrapePageAds.activeStatus": "active", "scrapePageAds.countryCode": country or "ALL",
            "scrapePageAds.sortBy": "impressions_desc", "scrapePageAds.period": ""}
