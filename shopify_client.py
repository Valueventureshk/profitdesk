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
# Everything ProfitDesk uses or will use (see SHOPIFY-APP-RELEASE.md). Must match the
# scopes in each store's app version, or Shopify refuses the extra ones.
SCOPES = ("read_orders,read_all_orders,write_orders,write_fulfillments,write_merchant_managed_fulfillment_orders,"
          "write_third_party_fulfillment_orders,write_assigned_fulfillment_orders,write_products,write_inventory,"
          "read_locations,write_publications,write_online_store_pages,write_online_store_navigation,write_content,"
          "write_themes,write_files,write_metaobjects,write_metaobject_definitions,write_discounts,write_pixels,"
          "read_customer_events,write_script_tags,read_customers,write_marketing_events,write_translations,"
          "read_analytics,read_shipping")

SHOP_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

SHOP_QUERY = "{ shop { name currencyCode myshopifyDomain ianaTimezone } }"
PLAN_QUERY = "{ shop { plan { displayName } } }"

ORDERS_QUERY = """
query Orders($cursor: String, $q: String!) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      createdAt
      test
      paymentGatewayNames
      totalPriceSet { shopMoney { amount } }
      totalRefundedSet { shopMoney { amount } }
    }
  }
}
"""


# Every order with its products, for the COG monitor. Smaller pages because each
# order carries its line items (Shopify limits how much one query may ask for).
ORDER_LINES_QUERY = """
query OrderLines($cursor: String, $q: String!) {
  orders(first: 40, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      name
      createdAt
      cancelledAt
      test
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount } }
      transactions(first: 6) {
        kind
        status
        gateway
        paymentId
        amountSet { shopMoney { amount } }
      }
      lineItems(first: 20) {
        nodes {
          title
          variantTitle
          quantity
          sku
          variant { id }
          product { id }
          discountedTotalSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""


ORDER_LINES_QUERY_LITE = ORDER_LINES_QUERY.replace(
    "          variant { id }\n          product { id }\n", "")


# One customer's orders, for the ticket side panel and AI drafts.
ORDER_LOOKUP_QUERY = """
query Lookup($q: String!) {
  orders(first: 6, query: $q, sortKey: CREATED_AT, reverse: true) {
    nodes {
      id name createdAt cancelledAt paymentGatewayNames
      displayFinancialStatus displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      totalRefundedSet { shopMoney { amount } }
      email
      shippingAddress { city province country }
      lineItems(first: 15) { nodes { title variantTitle quantity } }
      fulfillments(first: 5) { status createdAt trackingInfo { number url company } }
    }
  }
}
"""
ORDER_LOOKUP_LITE = ORDER_LOOKUP_QUERY.replace(
    "      fulfillments(first: 5) { status createdAt trackingInfo { number url company } }\n", "")


SHIPMENTS_QUERY = """
query Shipments($cursor: String, $q: String!) {
  orders(first: 100, after: $cursor, query: $q, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      legacyResourceId name createdAt updatedAt cancelledAt test
      displayFulfillmentStatus email currentSubtotalLineItemsQuantity
      shippingAddress { name city province countryCodeV2 }
      fulfillments(first: 10) { status createdAt trackingInfo { number url company } }
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

    async def plan(self) -> str:
        """The store's Shopify plan name, e.g. "Basic" or "Shopify"."""
        async with httpx.AsyncClient(timeout=30) as client:
            data = await self._post(client, PLAN_QUERY)
        return ((data["shop"].get("plan") or {}).get("displayName")) or ""

    async def access_scopes(self) -> set:
        """The permissions the store actually granted this app."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"https://{self.domain}/admin/oauth/access_scopes.json",
                                 headers=self.headers)
        if r.status_code >= 400:
            raise ShopifyError(f"Shopify error {r.status_code} reading the app's permissions.")
        return {x.get("handle") for x in r.json().get("access_scopes", [])}

    async def order_lines(self, start: str, end: str, tz_name: str = "UTC") -> list:
        """Every order created between two dates (store clock), with its products.
        [{name, created, day, cancelled, fulfillment, lines: [{title, variant, quantity,
          sku, variant_id, product_id, subtotal}]}]"""
        import asyncio as _aio
        tz = _zone(tz_name)
        lo = datetime.combine(datetime.fromisoformat(start).date(), time.min, tz)
        hi = datetime.combine(datetime.fromisoformat(end).date(), time.max, tz)
        q = (f"created_at:>='{lo.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}' "
             f"created_at:<='{hi.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}'")
        out, cursor, query = [], None, ORDER_LINES_QUERY
        async with httpx.AsyncClient(timeout=90) as client:
            while True:
                for attempt in range(6):
                    try:
                        data = await self._post(client, query, {"cursor": cursor, "q": q})
                        break
                    except ShopifyError as e:
                        if "read_products" in str(e) and query is ORDER_LINES_QUERY:
                            # The app can't see products yet (read_products not granted):
                            # same order lines, without product and variant ids.
                            query = ORDER_LINES_QUERY_LITE
                            continue
                        if "throttl" not in str(e).lower() and "rate" not in str(e).lower():
                            raise
                        await _aio.sleep(2 + attempt * 3)
                else:
                    raise ShopifyError(f"{self.domain} kept rate limiting us; try again shortly.")
                conn = data["orders"]
                for o in conn["nodes"]:
                    if o.get("test"):
                        continue
                    created = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(tz)
                    out.append({
                        "name": o["name"], "created": created.isoformat(),
                        "day": created.date().isoformat(),
                        "cancelled": bool(o.get("cancelledAt")),
                        "fulfillment": o.get("displayFulfillmentStatus") or "",
                        "total": float(((o.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount") or 0),
                        # The money taken for the order: a sale, or an authorisation captured later.
                        "payments": [{
                            "gateway": t.get("gateway") or "", "payment_id": t.get("paymentId") or "",
                            "amount": float(((t.get("amountSet") or {}).get("shopMoney") or {}).get("amount") or 0),
                        } for t in (o.get("transactions") or [])
                            if t.get("status") == "SUCCESS" and t.get("kind") in ("SALE", "CAPTURE")],
                        "lines": [{
                            "title": li.get("title") or "", "variant": li.get("variantTitle") or "",
                            "quantity": int(li.get("quantity") or 0), "sku": li.get("sku") or "",
                            "variant_id": (li.get("variant") or {}).get("id"),
                            "product_id": (li.get("product") or {}).get("id"),
                            "subtotal": float(((li.get("discountedTotalSet") or {}).get("shopMoney") or {}).get("amount") or 0),
                        } for li in o["lineItems"]["nodes"]],
                    })
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                cursor = conn["pageInfo"]["endCursor"]
        return out

    async def lookup_orders(self, names: list = None, email: str = None) -> list:
        """Orders by their numbers (e.g. "#BR5728"), else the customer's latest by email."""
        names = [n if n.startswith("#") else f"#{n}" for n in (names or []) if n]
        if names:
            q = " OR ".join(f"name:{n}" for n in names[:5])
        elif email:
            q = f"email:{email}"
        else:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                data = await self._post(client, ORDER_LOOKUP_QUERY, {"q": q})
            except ShopifyError as e:
                if "access" not in str(e).lower() and "fulfill" not in str(e).lower():
                    raise
                data = await self._post(client, ORDER_LOOKUP_LITE, {"q": q})
        out = []
        for o in data["orders"]["nodes"]:
            money = (o.get("totalPriceSet") or {}).get("shopMoney") or {}
            addr = o.get("shippingAddress") or {}
            out.append({
                "id": o["id"].rsplit("/", 1)[-1], "name": o["name"], "created": o["createdAt"],
                "cancelled": o.get("cancelledAt"),
                "financial": o.get("displayFinancialStatus") or "",
                "gateways": o.get("paymentGatewayNames") or [],
                "fulfillment": o.get("displayFulfillmentStatus") or "",
                "total": float(money.get("amount") or 0), "currency": money.get("currencyCode") or "",
                "refunded": float((((o.get("totalRefundedSet") or {}).get("shopMoney")) or {}).get("amount") or 0),
                "ship_to": ", ".join(x for x in (addr.get("city"), addr.get("province"), addr.get("country")) if x),
                "items": [{"title": li.get("title"), "variant": li.get("variantTitle") or "",
                           "quantity": li.get("quantity")} for li in o["lineItems"]["nodes"]],
                "tracking": [{"number": t.get("number"), "url": t.get("url"), "company": t.get("company"),
                              "status": f.get("status"), "date": f.get("createdAt")}
                             for f in (o.get("fulfillments") or []) for t in (f.get("trackingInfo") or [])],
            })
        return out

    async def shipments(self, since: str, field: str = "updated_at") -> list:
        """Orders created or changed since `since` (UTC ISO) with their tracking numbers.
        [{order_id, name, created, cancelled, fulfillment, email, customer, city, country,
          items, parcels: [{number, company, url, fulfilled}]}]"""
        import asyncio as _aio
        q = f"{field}:>='{since}'"
        out, cursor = [], None
        async with httpx.AsyncClient(timeout=90) as client:
            while True:
                for attempt in range(6):
                    try:
                        data = await self._post(client, SHIPMENTS_QUERY, {"cursor": cursor, "q": q})
                        break
                    except ShopifyError as e:
                        if "rate limit" in str(e).lower() or "throttled" in str(e).lower():
                            await _aio.sleep(2 + attempt * 2)
                            continue
                        raise
                conn = data["orders"]
                for o in conn["nodes"]:
                    if o.get("test"):
                        continue
                    a = o.get("shippingAddress") or {}
                    parcels, seen = [], set()
                    for f in o.get("fulfillments") or []:
                        if f.get("status") in ("CANCELLED", "ERROR", "FAILURE"):
                            continue
                        for t in f.get("trackingInfo") or []:
                            n = (t.get("number") or "").strip().replace(" ", "")
                            if n and n not in seen:
                                seen.add(n)
                                parcels.append({"number": n, "company": t.get("company") or "",
                                                "url": t.get("url") or "", "fulfilled": f.get("createdAt")})
                    out.append({"order_id": o["legacyResourceId"], "name": o["name"],
                                "created": o["createdAt"], "cancelled": bool(o.get("cancelledAt")),
                                "fulfillment": o.get("displayFulfillmentStatus") or "",
                                "email": o.get("email") or "", "customer": a.get("name") or "",
                                "city": a.get("city") or "", "province": a.get("province") or "",
                                "country": a.get("countryCodeV2") or "",
                                "items": o.get("currentSubtotalLineItemsQuantity") or 0,
                                "parcels": parcels})
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                cursor = conn["pageInfo"]["endCursor"]
        return out

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

        # payments: how each order was paid, {method: [orders, amount]}, so the
        # profit engine can work out processing fees per payment method.
        days = defaultdict(lambda: {"sales": 0.0, "orders": 0, "payments": {}})
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
                    # Fees are charged on what was paid, and providers keep them
                    # when an order is refunded, so the base is the original total.
                    via = ", ".join(o.get("paymentGatewayNames") or [])
                    paid = bucket["payments"].setdefault(via, [0, 0.0])
                    paid[0] += 1
                    paid[1] += _money(o, "totalPriceSet")
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
