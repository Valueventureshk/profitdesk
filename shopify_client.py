"""Shopify: installing the app on a store, then reading its sales.

The connector is a real Shopify app install. The merchant clicks Connect, lands
on Shopify's own permission screen asking for view access to orders, approves,
and Shopify hands back a token. Nothing is copied and pasted.

Only two figures are read: total sales and order count, per day.
"""
import hashlib
import hmac
import os
import re
from collections import defaultdict
from datetime import datetime, time, timezone
from urllib.parse import unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-07")

# View-only. This is the entire set of permissions the app asks a merchant for.
SCOPES = "read_orders"

SHOP_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

SHOP_QUERY = "{ shop { name currencyCode myshopifyDomain ianaTimezone } }"

ORDERS_QUERY = """
query Orders($cursor: String, $q: String!) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      createdAt
      test
      totalPriceSet { shopMoney { amount } }
      totalRefundedSet { shopMoney { amount } }
    }
  }
}
"""


class ShopifyError(RuntimeError):
    pass


# ---------------------------------------------------------------- app install

def app_configured() -> bool:
    """Whether .env holds a default app. Stores can also bring their own."""
    return bool(os.getenv("SHOPIFY_CLIENT_ID") and os.getenv("SHOPIFY_CLIENT_SECRET"))


def default_app():
    """The app in .env, as (client_id, client_secret), or None."""
    if not app_configured():
        return None
    return os.getenv("SHOPIFY_CLIENT_ID"), os.getenv("SHOPIFY_CLIENT_SECRET")


def check_install_link(link: str, shop_domain: str, client_id: str) -> str:
    """Only ever open a page on Shopify's own admin site, for this app and store.

    Shopify hands out install links in more than one shape, e.g.
      https://admin.shopify.com/store/<handle>/oauth/install_custom_app?client_id=...
      https://admin.shopify.com/?organization_id=...&redirect=<the above, encoded>
    so the check looks at the decoded link rather than one exact pattern.
    """
    link = (link or "").strip()
    handle = shop_domain.removesuffix(".myshopify.com")
    parts = urlsplit(link)
    decoded = link
    for _ in range(3):                      # links can be encoded more than once
        decoded = unquote(decoded)

    if parts.scheme != "https" or parts.netloc != "admin.shopify.com" or " " in link:
        raise ShopifyError(
            "That install link doesn't look right. Copy it from your app's Distribution "
            "page in Shopify. It starts with https://admin.shopify.com/"
        )
    named = re.findall(r"client_id=([0-9a-f]{32})", decoded)
    if named and client_id not in named:
        raise ShopifyError(
            "That install link belongs to a different app than this Client ID. "
            "Check both came from the same app."
        )
    other = re.search(r"/store/([a-z0-9][a-z0-9-]*)/", decoded)
    if other and other.group(1) != handle:
        raise ShopifyError(
            f"That install link is for the store {other.group(1)}, not {handle}. "
            "Check the store address matches the store you generated the link for."
        )
    return link


def normalize_domain(raw: str) -> str:
    """Accept whatever the merchant types and produce a myshopify.com domain."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].strip()
    if d and "." not in d:
        d = f"{d}.myshopify.com"
    if not SHOP_DOMAIN_RE.match(d):
        raise ShopifyError(
            "That does not look like a Shopify store address. It should look like "
            "your-store.myshopify.com"
        )
    return d


def install_url(shop_domain: str, redirect_uri: str, state: str, app) -> str:
    if not app:
        raise ShopifyError(
            "There are no Shopify app keys for this store yet. In Settings, add the "
            "store with its app's Client ID and Client secret."
        )
    return f"https://{shop_domain}/admin/oauth/authorize?" + urlencode({
        "client_id": app[0],
        "scope": SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    })


def verify_hmac(params: dict, app) -> bool:
    """Confirm the callback really came from Shopify and was not tampered with."""
    secret = app[1] if app else ""
    received = params.get("hmac", "")
    if not secret or not received:
        return False
    message = "&".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k not in ("hmac", "signature")
    )
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


async def exchange_code(shop_domain: str, code: str, app) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            json={
                "client_id": app[0],
                "client_secret": app[1],
                "code": code,
            },
        )
    if r.status_code != 200:
        raise ShopifyError(f"Shopify would not complete the install ({r.status_code}).")
    token = r.json().get("access_token")
    if not token:
        raise ShopifyError("Shopify completed the install but sent no access token back.")
    return token


# ---------------------------------------------------------------- reading data

class ShopifyClient:
    def __init__(self, shop_domain: str, access_token: str):
        self.domain = shop_domain
        self.endpoint = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

    async def _post(self, client, query, variables=None):
        try:
            r = await client.post(
                self.endpoint,
                headers=self.headers,
                json={"query": query, "variables": variables or {}},
            )
        except httpx.RequestError as e:
            raise ShopifyError(
                f"Could not reach {self.domain}. Check your internet connection. "
                f"({type(e).__name__})"
            ) from e
        if r.status_code in (401, 403):
            raise ShopifyError(
                f"{self.domain} no longer accepts this connection. Remove the store "
                "and connect it again."
            )
        if r.status_code == 404:
            raise ShopifyError(
                f"No Shopify API at {self.domain}. Check the store address, or set "
                "SHOPIFY_API_VERSION in .env to a version your store supports."
            )
        if r.status_code == 423:
            raise ShopifyError(f"{self.domain} is locked or frozen, so its data is unavailable.")
        if r.status_code == 429:
            raise ShopifyError(f"{self.domain} is rate limiting us. Wait a moment and refresh.")
        if r.status_code >= 400:
            raise ShopifyError(f"{self.domain} returned {r.status_code}: {r.text[:200]}")
        payload = r.json()
        if payload.get("errors"):
            first = payload["errors"][0]
            raise ShopifyError(str(first.get("message", first)))
        return payload["data"]

    async def shop_info(self):
        async with httpx.AsyncClient(timeout=30) as client:
            data = await self._post(client, SHOP_QUERY)
        return data["shop"]

    async def daily_sales(self, start: str, end: str, tz_name: str = "UTC",
                          until: time = None):
        """Total sales and order count per day, in the store's own timezone.

        Bucketing by the shop's timezone rather than UTC is what makes these
        figures line up with the merchant's own Shopify reports.

        `until` stops the last day at that time of day, for "same time
        yesterday" comparisons.
        """
        tz = _zone(tz_name)
        lo = datetime.combine(datetime.fromisoformat(start).date(), time.min, tz)
        hi = datetime.combine(datetime.fromisoformat(end).date(), until or time.max, tz)
        q = (
            f"created_at:>='{lo.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}' "
            f"created_at:<='{hi.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}'"
        )

        days = defaultdict(lambda: {"sales": 0.0, "orders": 0})
        cursor = None
        async with httpx.AsyncClient(timeout=90) as client:
            while True:
                data = await self._post(client, ORDERS_QUERY, {"cursor": cursor, "q": q})
                conn = data["orders"]
                for o in conn["nodes"]:
                    if o.get("test"):
                        continue
                    created = datetime.fromisoformat(
                        o["createdAt"].replace("Z", "+00:00")
                    ).astimezone(tz)
                    bucket = days[created.date().isoformat()]
                    # Original total minus money actually refunded. Shopify's
                    # "current" total already drops returned items but not refunds
                    # without a return, so it can't be combined with refunds safely.
                    bucket["sales"] += (
                        _money(o, "totalPriceSet") - _money(o, "totalRefundedSet")
                    )
                    bucket["orders"] += 1
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                cursor = conn["pageInfo"]["endCursor"]

        return {d: v for d, v in sorted(days.items())}


def _zone(name: str):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _money(node, field) -> float:
    try:
        return float(node[field]["shopMoney"]["amount"])
    except (KeyError, TypeError, ValueError):
        return 0.0
