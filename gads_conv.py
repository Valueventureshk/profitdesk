"""Apps → Tracking → Google Ads: send purchases to Google Ads from the server (offline click conversions).

Shopify keeps, on every order, the page the customer first / last landed on (customerJourneySummary). When they
came from a Google ad that page carries Google's click id (gclid, or gbraid / wbraid on iPhone). ProfitDesk
reads new orders, takes the click id, and uploads the purchase to the store's Google Ads account under its own
conversion action "ProfitDesk Purchase": value = order total less tax, the store's currency, and the order
number as order_id, so Google never counts the same order twice. Hashed email / phone go along when the
account allows it (enhanced conversions), which helps Google match orders.

Watch = the conversion action is "secondary": Google records it but doesn't bid on it (safe next to the
existing tag). Send = "primary": Google's bidding uses it.
"""
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

import gads_manage
import google_ads_client as gads

ACTION_NAMES = {"purchase": "ProfitDesk Purchase", "add_to_cart": "ProfitDesk Add to cart", "begin_checkout": "ProfitDesk Begin checkout"}
CATEGORY = {"purchase": "PURCHASE", "add_to_cart": "ADD_TO_CART", "begin_checkout": "BEGIN_CHECKOUT"}

ORDERS_QUERY = """
query GOrders($cursor: String, $q: String!) {
  orders(first: 50, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      name createdAt cancelledAt test email phone
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      currentTotalTaxSet { shopMoney { amount } }
      customerJourneySummary {
        firstVisit { landingPage occurredAt }
        lastVisit { landingPage occurredAt }
      }
    }
  }
}
"""


def click_ids(url: str) -> dict:
    """{gclid | gbraid | wbraid: value} from a landing page URL."""
    if not url:
        return {}
    try:
        q = parse_qs(urlparse(url if "://" in url else "https://x" + (url if url.startswith("/") else "/" + url)).query)
    except ValueError:
        return {}
    return {k: q[k][0] for k in ("gclid", "gbraid", "wbraid") if q.get(k) and q[k][0]}


def order_click(o: dict) -> dict:
    """The Google click behind an order: last visit first (Google credits the last click), else the first."""
    j = o.get("customerJourneySummary") or {}
    for v in (j.get("lastVisit"), j.get("firstVisit")):
        got = click_ids((v or {}).get("landingPage") or "")
        if got:
            return got
    return {}


async def orders(client, start_iso: str) -> list:
    """Orders created since start (UTC ISO), with what an upload needs."""
    out, cursor = [], None
    q = f"created_at:>='{start_iso}'"
    async with httpx.AsyncClient(timeout=90) as http:
        while True:
            data = await client._post(http, ORDERS_QUERY, {"cursor": cursor, "q": q})
            conn = data["orders"]
            for o in conn["nodes"]:
                if o.get("test"):
                    continue
                tot = (o.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
                tax = ((o.get("currentTotalTaxSet") or {}).get("shopMoney") or {}).get("amount") or 0
                out.append({"name": o["name"], "created": o["createdAt"], "cancelled": bool(o.get("cancelledAt")),
                            "value": round(float(tot.get("amount") or 0) - float(tax or 0), 2), "currency": tot.get("currencyCode"),
                            "email": o.get("email"), "phone": o.get("phone"), "click": order_click(o)})
            if not conn["pageInfo"]["hasNextPage"]:
                return out
            cursor = conn["pageInfo"]["endCursor"]


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def identifiers(email: str = None, phone: str = None) -> list:
    out = []
    if email and "@" in email:
        e = email.strip().lower()
        local, _, dom = e.partition("@")
        if dom in ("gmail.com", "googlemail.com"):
            local = local.split("+")[0].replace(".", "")
        out.append({"hashedEmail": _sha(f"{local}@{dom}")})
    if phone:
        p = re.sub(r"[^\d+]", "", phone)
        if p.startswith("+") and len(p) > 7:
            out.append({"hashedPhoneNumber": _sha(p)})
    return out


async def actions(rt, customer_id, login_cid) -> dict:
    """ProfitDesk's conversion actions in this account: {kind: {resource, status, primary, id}}."""
    rows = await gads_manage._q(rt, customer_id, login_cid,
                                "SELECT conversion_action.resource_name, conversion_action.id, conversion_action.name, "
                                "conversion_action.status, conversion_action.primary_for_goal, conversion_action.type, "
                                "conversion_action.category FROM conversion_action WHERE conversion_action.status != 'REMOVED'")
    out = {}
    for r in rows:
        a = r.get("conversionAction") or {}
        for kind, name in ACTION_NAMES.items():
            if a.get("name") == name:
                out[kind] = {"resource": a["resourceName"], "id": str(a.get("id")), "status": a.get("status"),
                             "primary": bool(a.get("primaryForGoal"))}
    return out


async def all_actions(rt, customer_id, login_cid) -> list:
    """Every conversion action in the account with the last 30 days' conversions, for the tracking check."""
    rows = await gads_manage._q(rt, customer_id, login_cid,
                                "SELECT conversion_action.id, conversion_action.name, conversion_action.status, conversion_action.type, "
                                "conversion_action.category, conversion_action.primary_for_goal, conversion_action.origin "
                                "FROM conversion_action WHERE conversion_action.status != 'REMOVED'")
    stats = {}
    try:
        got = await gads_manage._q(rt, customer_id, login_cid,
                                   "SELECT segments.conversion_action, metrics.all_conversions, metrics.all_conversions_value "
                                   "FROM customer WHERE segments.date DURING LAST_30_DAYS")
        for r in got:
            k = (r.get("segments") or {}).get("conversionAction")
            m = r.get("metrics") or {}
            s = stats.setdefault(k, [0.0, 0.0])
            s[0] += float(m.get("allConversions") or 0)
            s[1] += float(m.get("allConversionsValue") or 0)
    except gads.GoogleAdsError:
        pass
    out = []
    for r in rows:
        a = r.get("conversionAction") or {}
        rn = f"customers/{customer_id}/conversionActions/{a.get('id')}".replace("-", "")
        s = next((v for k, v in stats.items() if k and k.endswith(f"/{a.get('id')}")), [0.0, 0.0])
        out.append({"id": str(a.get("id")), "name": a.get("name"), "status": a.get("status"), "type": a.get("type"),
                    "category": a.get("category"), "primary": bool(a.get("primaryForGoal")), "origin": a.get("origin"),
                    "conversions_30d": round(s[0], 1), "value_30d": round(s[1], 2)})
    return sorted(out, key=lambda x: (not x["primary"], x["category"] or "", x["name"] or ""))


async def ensure_action(rt, customer_id, login_cid, kind: str, primary: bool) -> dict:
    """Create ProfitDesk's conversion action of this kind if missing; set primary / secondary."""
    have = (await actions(rt, customer_id, login_cid)).get(kind)
    if have:
        if have["primary"] != primary:
            await gads_manage.mutate(rt, customer_id, login_cid, [{"conversionActionOperation": {
                "update": {"resourceName": have["resource"], "primaryForGoal": primary}, "updateMask": "primary_for_goal"}}])
            have["primary"] = primary
        return have
    cid = str(customer_id).replace("-", "")
    await gads_manage.mutate(rt, customer_id, login_cid, [{"conversionActionOperation": {"create": {
        "name": ACTION_NAMES[kind], "type": "UPLOAD_CLICKS", "category": CATEGORY[kind], "status": "ENABLED",
        "primaryForGoal": primary, "countingType": "MANY_PER_CLICK" if kind == "purchase" else "ONE_PER_CLICK",
        "clickThroughLookbackWindowDays": 30,
        "valueSettings": {"defaultValue": 0, "alwaysUseDefaultValue": False}}}}])
    return (await actions(rt, customer_id, login_cid)).get(kind) or {}


def _gtime(iso: str) -> str:
    """Google's 'yyyy-mm-dd hh:mm:ss+hh:mm'."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return t.strftime("%Y-%m-%d %H:%M:%S+00:00")


async def upload(rt, customer_id, login_cid, action_resource: str, items: list, with_ids: bool = True) -> list:
    """Upload click conversions: items [{order, created, value, currency, click: {gclid|gbraid|wbraid}, email, phone}].
    Returns [{order, ok, error}] in the same order. Partial failure: one bad row doesn't stop the rest."""
    version, cid, headers = await gads_manage._ctx(rt, customer_id, login_cid)
    convs = []
    for it in items:
        c = {"conversionAction": action_resource, "conversionDateTime": _gtime(it["created"]),
             "conversionValue": it["value"], "currencyCode": it["currency"], "orderId": it["order"], **it["click"]}
        if with_ids:
            ids = identifiers(it.get("email"), it.get("phone"))
            if ids:
                c["userIdentifiers"] = ids
        convs.append(c)
    url = f"https://googleads.googleapis.com/{version}/customers/{cid}:uploadClickConversions"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=headers, json={"conversions": convs, "partialFailure": True})
    if r.status_code >= 400:
        raise gads.GoogleAdsError(f"Google refused the upload: {gads._why(r)}")
    body = r.json()
    bad = {}
    for d in ((body.get("partialFailureError") or {}).get("details") or []):
        for e in d.get("errors") or []:
            idx = next((int(f.get("index", 0)) for f in (e.get("location") or {}).get("fieldPathElements") or []
                        if f.get("fieldName") == "conversions"), None)
            if idx is not None:
                code = "".join(f"{k}={v}" for k, v in (e.get("errorCode") or {}).items())
                bad[idx] = f"{e.get('message', '')} ({code})"
    return [{"order": it["order"], "ok": i not in bad, "error": bad.get(i)} for i, it in enumerate(items)]
