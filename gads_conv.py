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
                            "email": o.get("email"), "phone": o.get("phone"), "click": order_click(o),
                            "landing": [((o.get("customerJourneySummary") or {}).get(k) or {}).get("landingPage") for k in ("firstVisit", "lastVisit")]})
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
    """Send conversions to Google Ads through Google's Data Manager API (events:ingest), the way Google requires for
    new integrations: items [{order, created, value, currency, click: {gclid|gbraid|wbraid}, email, phone}].
    One event per request (Data Manager rejects a whole request when one event is bad), so one bad order can't block
    the rest. Returns [{order, ok, error}] in the same order."""
    access = await gads._access_token(rt)
    cid = str(customer_id).replace("-", "")
    login = str(login_cid or customer_id).replace("-", "")
    dest = {"operatingAccount": {"accountType": "GOOGLE_ADS", "accountId": cid},
            "loginAccount": {"accountType": "GOOGLE_ADS", "accountId": login},
            "productDestinationId": action_resource.rsplit("/", 1)[-1]}
    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
    out = []
    async with httpx.AsyncClient(timeout=60) as client:
        for it in items:
            ev = {"adIdentifiers": dict(it["click"]), "eventTimestamp": datetime.fromisoformat(it["created"].replace("Z", "+00:00")).isoformat(),
                  "transactionId": str(it["order"]), "conversionValue": it["value"], "currency": it["currency"], "eventSource": "WEB"}
            if with_ids:
                ids = []
                for u in identifiers(it.get("email"), it.get("phone")):
                    ids.append({"emailAddress": u["hashedEmail"]} if "hashedEmail" in u else {"phoneNumber": u["hashedPhoneNumber"]})
                if ids:
                    ev["userData"] = {"userIdentifiers": ids}
            body = {"destinations": [dest], "encoding": "HEX", "events": [ev],
                    "consent": {"adUserData": "CONSENT_GRANTED", "adPersonalization": "CONSENT_GRANTED"}}
            try:
                r = await client.post("https://datamanager.googleapis.com/v1/events:ingest", headers=headers, json=body)
            except httpx.RequestError as e:
                out.append({"order": it["order"], "ok": False, "error": f"Couldn't reach Google (TRANSIENT {type(e).__name__})"})
                continue
            if r.status_code < 300:
                out.append({"order": it["order"], "ok": True, "error": None})
                continue
            try:
                err = r.json().get("error") or {}
                msg = f"{err.get('status', r.status_code)}: {err.get('message', '')[:300]}"
            except ValueError:
                msg = f"{r.status_code}: {r.text[:300]}"
            if r.status_code == 403 and ("scope" in msg.lower() or "insufficient" in msg.lower()):
                msg = "NEEDS_SIGN_IN: sign in to Google again in Settings (ProfitDesk needs the new Data Manager permission). " + msg
            elif r.status_code == 403 and ("disabled" in msg.lower() or "has not been used" in msg.lower()):
                msg = "API_OFF: switch on the Data Manager API in the Google Cloud project. " + msg
            out.append({"order": it["order"], "ok": False, "error": msg})
    return out

# ---------------------------------------------------------------- what to send (from the ProfitDesk pixel)

import json as _json
from datetime import timedelta

import db

GOOGLE_KEYS = ("gclid", "gbraid", "wbraid")
KIND_OF_EVENT = {"checkout_completed": "purchase", "product_added_to_cart": "add_to_cart", "checkout_started": "begin_checkout"}
CLICK_DAYS = 30            # a click older than this doesn't get the conversion (the actions' click-through window)


def _gclick(touch_json) -> dict:
    try:
        t = _json.loads(touch_json or "{}")
    except ValueError:
        return {}
    for k in GOOGLE_KEYS:
        if t.get(k):
            return {k: t[k]}
    return {}


def click_for(visitor: dict, at: datetime) -> dict:
    """The Google click a visitor's event belongs to: their latest Google click within 30 days before it."""
    if not visitor:
        return {}
    cands = [(visitor.get("g_click"), visitor.get("g_at")), (visitor.get("last_touch"), visitor.get("touch_at")),
             (visitor.get("first_touch"), visitor.get("first_at"))]
    for touch, when in cands:
        c = _gclick(touch)
        if not c or not when:
            continue
        try:
            t = datetime.fromisoformat(when)
        except ValueError:
            continue
        if timedelta(0) <= at - t <= timedelta(days=CLICK_DAYS) or abs((at - t).total_seconds()) < 120:
            return c
    return {}


def todo(store_id: int, since: str, retry_hours: int = 48) -> list:
    """Pixel purchases / adds to cart / checkouts since `since` (UTC ISO) not sent yet, each with its Google click.
    [{kind, ref, at, value, currency, client_id, order_id, click}] (click {} = no Google click)."""
    with db._conn() as con:
        done = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT kind, ref, status FROM gconv_uploads WHERE store_id = ? AND at >= ?", (store_id, since))}
        evs = [dict(r) for r in con.execute(
            "SELECT id, client_id, name, at, value, currency, order_id FROM px_events WHERE store_id = ? AND at >= ? AND name IN "
            "('checkout_completed', 'product_added_to_cart', 'checkout_started') ORDER BY at", (store_id, since))]
        vis = {r["client_id"]: dict(r) for r in con.execute(
            "SELECT client_id, first_at, first_touch, last_touch, touch_at, g_click, g_at FROM px_visitors WHERE store_id = ? AND "
            "client_id IN (SELECT DISTINCT client_id FROM px_events WHERE store_id = ? AND at >= ?)", (store_id, store_id, since))}
    out, seen = [], set()
    for e in evs:
        kind = KIND_OF_EVENT[e["name"]]
        ref = str(e["order_id"]) if kind == "purchase" and e.get("order_id") else e["id"]
        if (kind, ref) in seen or done.get((kind, ref)) in ("sent", "failed", "no_click"):
            continue
        seen.add((kind, ref))
        at = datetime.fromisoformat(e["at"])
        out.append({"kind": kind, "ref": ref, "at": e["at"], "value": e.get("value"), "currency": e.get("currency"),
                    "client_id": e["client_id"], "order_id": e.get("order_id"), "click": click_for(vis.get(e["client_id"]), at)})
    return out


RETRY_CODES = ("TOO_RECENT", "CLICK_NOT_FOUND", "CONVERSION_PRECEDES_GCLID", "RESOURCE_EXHAUSTED", "INTERNAL", "TRANSIENT",
               "NEEDS_SIGN_IN", "API_OFF", "UNAVAILABLE", "DEADLINE_EXCEEDED", "429", "500", "503")
TERMS_CODES = ("CUSTOMER_NOT_ACCEPTED_CUSTOMER_DATA_TERMS", "CUSTOMER_DATA_POLICY", "ENHANCED_CONVERSIONS", "customer data terms")


def save(store_id: int, rows: list):
    """rows: [{kind, ref, at, value, currency, click, status, error}] -> gconv_uploads (tries counted)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db._conn() as con:
        for r in rows:
            con.execute(
                "INSERT INTO gconv_uploads (store_id, kind, ref, at, value, currency, click, status, error, tries, sent_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(store_id, kind, ref) DO UPDATE SET status = excluded.status,"
                " error = excluded.error, tries = tries + 1, value = excluded.value, sent_at = excluded.sent_at",
                (store_id, r["kind"], r["ref"], r["at"], r.get("value"), r.get("currency"), r.get("click") or "",
                 r["status"], r.get("error"), now if r["status"] == "sent" else None))


def counts(store_id: int, since: str) -> dict:
    """{kind: {status: count}} since `since`."""
    out = {}
    with db._conn() as con:
        for k, s, n in con.execute("SELECT kind, status, COUNT(*) FROM gconv_uploads WHERE store_id = ? AND at >= ? GROUP BY kind, status",
                                   (store_id, since)):
            out.setdefault(k, {})[s] = n
    return out


async def daily_by_action(rt, customer_id, login_cid, start, end) -> dict:
    """{action name: {date: conversions}} (Google's attribution, by day) for purchase actions."""
    rows = await gads_manage._q(rt, customer_id, login_cid,
                                "SELECT segments.conversion_action_name, segments.conversion_action_category, segments.date, "
                                f"metrics.all_conversions FROM customer WHERE segments.date BETWEEN '{start}' AND '{end}' "
                                "AND segments.conversion_action_category = 'PURCHASE'")
    out = {}
    for r in rows:
        s = r.get("segments") or {}
        nm = s.get("conversionActionName")
        out.setdefault(nm, {})[s.get("date")] = out.get(nm, {}).get(s.get("date"), 0) + float((r.get("metrics") or {}).get("allConversions") or 0)
    return out


# ---------------------------------------------------------------- refunds and cancellations (adjustments)

async def adjust(rt, customer_id, login_cid, action_resource: str, items: list) -> list:
    """items [{order (name sent as orderId), value (new value; 0 = cancelled / fully refunded), currency}].
    Value 0 -> RETRACTION (Google drops the purchase), else RESTATEMENT (Google uses the new value).
    Returns [{order, ok, error}]."""
    version, cid, headers = await gads_manage._ctx(rt, customer_id, login_cid)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
    adj = []
    for it in items:
        a = {"conversionAction": action_resource, "orderId": it["order"], "adjustmentDateTime": now,
             "adjustmentType": "RETRACTION" if it["value"] <= 0 else "RESTATEMENT"}
        if it["value"] > 0:
            a["restatementValue"] = {"adjustedValue": it["value"], "currencyCode": it["currency"]}
        adj.append(a)
    url = f"https://googleads.googleapis.com/{version}/customers/{cid}:uploadConversionAdjustments"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=headers, json={"conversionAdjustments": adj, "partialFailure": True})
    if r.status_code >= 400:
        raise gads.GoogleAdsError(f"Google refused the corrections: {gads._why(r)}")
    bad = {}
    for d in ((r.json().get("partialFailureError") or {}).get("details") or []):
        for e in d.get("errors") or []:
            idx = next((int(f.get("index", 0)) for f in (e.get("location") or {}).get("fieldPathElements") or []
                        if f.get("fieldName") == "conversion_adjustments"), None)
            if idx is not None:
                code = "".join(f"{k}={v}" for k, v in (e.get("errorCode") or {}).items())
                bad[idx] = f"{e.get('message', '')} ({code})"
    return [{"order": it["order"], "ok": i not in bad, "error": bad.get(i)} for i, it in enumerate(items)]


ADJ_RETRY = ("TOO_RECENT", "CONVERSION_NOT_FOUND", "RESOURCE_EXHAUSTED", "INTERNAL", "TRANSIENT")


def sent_purchases(store_id: int, since: str) -> list:
    """Purchases sent to Google since `since` that may still need a correction: [{ref, value, adj_value, adj_status}]."""
    with db._conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT ref, value, currency, adj_value, adj_status FROM gconv_uploads WHERE store_id = ? AND kind = 'purchase' "
            "AND status = 'sent' AND at >= ? AND COALESCE(adj_status, '') != 'final'", (store_id, since))]


def save_adj(store_id: int, ref: str, value: float, status: str, error: str = None):
    with db._conn() as con:
        con.execute("UPDATE gconv_uploads SET adj_value = ?, adj_status = ?, adj_at = ?, adj_error = ? WHERE store_id = ? "
                    "AND kind = 'purchase' AND ref = ?", (value, status, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                          error, store_id, ref))
