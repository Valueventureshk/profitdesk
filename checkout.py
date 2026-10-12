"""Checkout: ProfitDesk's own payment page (Lasso-style) for selected Shopify stores, paid through Airwallex, so a
store that can't use Shopify Payments can still sell monthly subscriptions.

    Offer        one product (a Shopify variant) with a one-time price and/or a subscription price every N days,
                 reached by its secret link /pay/<slug>. Prices are decided here on the server, never by the page.
    First order  POST /api/v1/pa/customers/create (subscriptions) -> payment_intents/create -> Airwallex's Drop-in
                 element charges the card on our page (card numbers never touch ProfitDesk) and, for a subscription,
                 saves it with a merchant-triggered "scheduled" consent. We then read the intent back from Airwallex,
                 check it SUCCEEDED for the amount we asked, and create the paid order in Shopify (orderCreate).
    Renewals     co_subs whose next_at has passed: a new intent for the saved customer, confirmed by us
                 (triggered_by merchant, the saved consent + card) -> a new paid Shopify order. Failed: retried after
                 1 day, three tries, then the subscription stops (status failed) and shows on the desk.
    Test mode    (setting co_test, on until the owner turns it off): nothing above CAP is ever charged, and Shopify
                 orders are created as test orders, which every ProfitDesk figure already leaves out.

Keys (pasted by the owner in Checkout -> Settings, never in chat): setting co_awx JSON
{client_id, api_key, account_id (optional, for an organisation key), webhook_secret}. Restricted key with
"Payment Acceptance" read + write.
"""
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

import db
from airwallex_client import API, AirwallexError, _token

CAP = 5.0                    # test mode: the most any payment can be
RETRY_DAYS = (1, 1, 1)       # a failed renewal is tried again after these gaps, then the subscription stops

SCHEMA = """
CREATE TABLE IF NOT EXISTS co_offers (
    id INTEGER PRIMARY KEY,
    store_id INTEGER NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,                 -- what the page calls it
    variant_id TEXT NOT NULL,            -- Shopify variant GID put on the order
    product_title TEXT,
    image TEXT,
    description TEXT,
    one_price REAL,                      -- one-time price (NULL = subscription only)
    sub_price REAL,                      -- price per delivery (NULL = one-time only)
    interval_days INTEGER NOT NULL DEFAULT 30,
    shipping REAL NOT NULL DEFAULT 0,
    countries TEXT NOT NULL DEFAULT 'AU',  -- where it ships, comma separated
    currency TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS co_orders (
    id INTEGER PRIMARY KEY,
    ref TEXT UNIQUE NOT NULL,            -- ours, also Airwallex's merchant_order_id
    offer_id INTEGER NOT NULL,
    store_id INTEGER NOT NULL,
    sub_id INTEGER,                      -- the subscription it started or renewed
    kind TEXT NOT NULL,                  -- one | first | renewal
    intent_id TEXT UNIQUE,
    customer_id TEXT,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    email TEXT, name TEXT, phone TEXT, address TEXT,   -- address JSON
    status TEXT NOT NULL DEFAULT 'started',            -- started | paid | ordered | failed | refunded
    shopify_id TEXT, shopify_name TEXT,
    test INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE TABLE IF NOT EXISTS co_subs (
    id INTEGER PRIMARY KEY,
    offer_id INTEGER NOT NULL,
    store_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,          -- the customer's manage link
    customer_id TEXT NOT NULL,
    consent_id TEXT,
    method_id TEXT, method_type TEXT,
    email TEXT, name TEXT, phone TEXT, address TEXT,
    price REAL NOT NULL, shipping REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL,
    interval_days INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- active | paused | cancelled | failed
    next_at TEXT NOT NULL,
    fails INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    renewals INTEGER NOT NULL DEFAULT 0,
    test INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    ended_at TEXT
);
"""


class CheckoutError(RuntimeError):
    pass


def init():
    with db._conn() as con:
        con.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def keys() -> dict:
    try:
        return json.loads(db.get_setting("co_awx") or "{}")
    except ValueError:
        return {}


def test_mode() -> bool:
    return (db.get_setting("co_test") or "1") == "1"


def _row(r):
    return dict(r) if r else None


# ---------------------------------------------------------------- Airwallex

async def _call(method: str, path: str, body: dict = None, what: str = "the payment") -> dict:
    k = keys()
    if not k.get("client_id") or not k.get("api_key"):
        raise CheckoutError("The checkout's Airwallex key isn't set yet (Checkout → Settings).")
    async with httpx.AsyncClient(timeout=40) as client:
        tok = await _token(client, k["client_id"], k["api_key"], k.get("account_id") or None)
        try:
            r = await client.request(method, f"{API}{path}", json=body,
                                     headers={"Authorization": f"Bearer {tok}"})
        except httpx.RequestError as e:
            raise CheckoutError(f"Could not reach Airwallex ({type(e).__name__}).") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        msg = data.get("message") or data.get("code") or r.text[:200]
        if r.status_code in (401, 403):
            msg = f"Airwallex refused the checkout key for {what} ({msg}). It needs Payment Acceptance read + write."
        raise CheckoutError(f"Airwallex: {msg}")
    return data


async def create_customer(email: str, name: str, phone: str) -> dict:
    first, _, last = (name or "").partition(" ")
    body = {"request_id": str(uuid.uuid4()), "merchant_customer_id": f"pd-{uuid.uuid4().hex[:16]}",
            "email": email, "first_name": first or None, "last_name": last or None}
    if phone:
        body["phone_number"] = phone
    return await _call("POST", "/api/v1/pa/customers/create", {k: v for k, v in body.items() if v}, "customers")


async def create_intent(amount: float, currency: str, ref: str, customer_id: str = None, descriptor: str = None,
                        metadata: dict = None) -> dict:
    if test_mode() and amount > CAP:
        raise CheckoutError(f"Test mode: payments over {CAP:.2f} are blocked. Turn test mode off in Checkout → Settings.")
    body = {"request_id": str(uuid.uuid4()), "amount": round(amount, 2), "currency": currency,
            "merchant_order_id": ref, "metadata": metadata or {}}
    if customer_id:
        body["customer_id"] = customer_id
    if descriptor:
        body["descriptor"] = descriptor[:22]
    return await _call("POST", "/api/v1/pa/payment_intents/create", body, "payments")


async def get_intent(intent_id: str) -> dict:
    return await _call("GET", f"/api/v1/pa/payment_intents/{intent_id}", None, "payments")


async def get_consent(consent_id: str) -> dict:
    return await _call("GET", f"/api/v1/pa/payment_consents/{consent_id}", None, "saved cards")


async def charge_saved(sub: dict, amount: float, ref: str) -> dict:
    """A renewal: a new intent for the subscriber, confirmed by us with their saved card (merchant-triggered)."""
    intent = await create_intent(amount, sub["currency"], ref, sub["customer_id"],
                                 metadata={"sub": str(sub["id"]), "kind": "renewal"})
    body = {"request_id": str(uuid.uuid4()), "customer_id": sub["customer_id"], "triggered_by": "merchant",
            "payment_method": {"id": sub["method_id"], "type": sub["method_type"] or "card"}}
    if sub.get("consent_id"):
        body["payment_consent_id"] = sub["consent_id"]
    if (sub["method_type"] or "card") == "card":
        body["payment_method_options"] = {"card": {"auto_capture": True}}
    return await _call("POST", f"/api/v1/pa/payment_intents/{intent['id']}/confirm", body, "payments")


async def refund(intent_id: str, amount: float = None, reason: str = None) -> dict:
    body = {"request_id": str(uuid.uuid4()), "payment_intent_id": intent_id}
    if amount:
        body["amount"] = round(amount, 2)
    if reason:
        body["reason"] = reason[:128]
    return await _call("POST", "/api/v1/pa/refunds/create", body, "refunds")


def webhook_ok(body: bytes, ts: str, sig: str) -> bool:
    secret = keys().get("webhook_secret") or ""
    if not secret or not ts or not sig:
        return False
    want = hmac.new(secret.encode(), ts.encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


# ---------------------------------------------------------------- offers

def offers(store_id: int = None) -> list:
    with db._conn() as con:
        rows = con.execute("SELECT * FROM co_offers" + (" WHERE store_id = ?" if store_id else "") + " ORDER BY id DESC",
                           (store_id,) if store_id else ()).fetchall()
    return [dict(r) for r in rows]


def offer_by_slug(slug: str):
    with db._conn() as con:
        return _row(con.execute("SELECT * FROM co_offers WHERE slug = ? AND active = 1", (slug,)).fetchone())


def offer(oid: int):
    with db._conn() as con:
        return _row(con.execute("SELECT * FROM co_offers WHERE id = ?", (oid,)).fetchone())


FIELDS = ("store_id", "title", "variant_id", "product_title", "image", "description", "one_price", "sub_price",
          "interval_days", "shipping", "countries", "currency", "active")


def save_offer(data: dict) -> dict:
    d = {k: data.get(k) for k in FIELDS if k in data}
    for k in ("one_price", "sub_price"):
        if k in d:
            d[k] = float(d[k]) if d[k] not in (None, "") else None
    if "shipping" in d:
        d["shipping"] = float(d["shipping"] or 0)
    if "interval_days" in d:
        d["interval_days"] = max(1, int(d["interval_days"] or 30))
    if "countries" in d:
        d["countries"] = ",".join(c.strip().upper() for c in str(d["countries"] or "").split(",") if c.strip()) or "AU"
    if data.get("id"):
        with db._conn() as con:
            sets = ", ".join(f"{k} = ?" for k in d)
            con.execute(f"UPDATE co_offers SET {sets} WHERE id = ?", (*d.values(), int(data["id"])))
        return offer(int(data["id"]))
    if not d.get("variant_id") or not d.get("store_id") or not (d.get("one_price") or d.get("sub_price")):
        raise CheckoutError("Pick a product and give it a one-time price, a subscription price, or both.")
    d.setdefault("title", d.get("product_title") or "Offer")
    slug = secrets.token_urlsafe(9)
    with db._conn() as con:
        cols = list(d) + ["slug", "created_at"]
        con.execute(f"INSERT INTO co_offers ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    (*d.values(), slug, _now()))
    return offer_by_slug(slug) or {}


def public_offer(o: dict) -> dict:
    """What the payment page may see."""
    return {k: o[k] for k in ("slug", "title", "product_title", "image", "description", "one_price", "sub_price",
                              "interval_days", "shipping", "currency")} | {
        "countries": o["countries"].split(","), "test": test_mode()}


# ---------------------------------------------------------------- orders + subscriptions

def order(ref: str = None, intent_id: str = None):
    with db._conn() as con:
        if ref:
            return _row(con.execute("SELECT * FROM co_orders WHERE ref = ?", (ref,)).fetchone())
        return _row(con.execute("SELECT * FROM co_orders WHERE intent_id = ?", (intent_id,)).fetchone())


def update_order(oid: int, **kw):
    with db._conn() as con:
        con.execute(f"UPDATE co_orders SET {', '.join(f'{k} = ?' for k in kw)} WHERE id = ?", (*kw.values(), oid))


def new_order(o: dict, kind: str, amount: float, who: dict, sub_id: int = None, customer_id: str = None) -> dict:
    ref = f"PD{datetime.now(timezone.utc):%y%m%d}-{secrets.token_hex(3).upper()}"
    with db._conn() as con:
        con.execute("INSERT INTO co_orders (ref, offer_id, store_id, sub_id, kind, customer_id, amount, currency, email,"
                    " name, phone, address, test, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ref, o["id"], o["store_id"], sub_id, kind, customer_id, round(amount, 2), o["currency"],
                     who.get("email"), who.get("name"), who.get("phone"), json.dumps(who.get("address") or {}),
                     1 if test_mode() else 0, _now()))
    return order(ref)


def sub(sid: int = None, token: str = None):
    with db._conn() as con:
        if token:
            return _row(con.execute("SELECT * FROM co_subs WHERE token = ?", (token,)).fetchone())
        return _row(con.execute("SELECT * FROM co_subs WHERE id = ?", (sid,)).fetchone())


def update_sub(sid: int, **kw):
    with db._conn() as con:
        con.execute(f"UPDATE co_subs SET {', '.join(f'{k} = ?' for k in kw)} WHERE id = ?", (*kw.values(), sid))


def new_sub(o: dict, od: dict, consent_id: str, method_id: str, method_type: str) -> dict:
    token = secrets.token_urlsafe(18)
    nxt = (datetime.now(timezone.utc) + timedelta(days=o["interval_days"])).isoformat(timespec="seconds")
    with db._conn() as con:
        cur = con.execute(
            "INSERT INTO co_subs (offer_id, store_id, token, customer_id, consent_id, method_id, method_type, email, name,"
            " phone, address, price, shipping, currency, interval_days, next_at, test, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o["id"], o["store_id"], token, od["customer_id"], consent_id, method_id, method_type, od["email"], od["name"],
             od["phone"], od["address"], o["sub_price"], o["shipping"], o["currency"], o["interval_days"], nxt,
             od["test"], _now()))
        sid = cur.lastrowid
    update_order(od["id"], sub_id=sid)
    return sub(sid)


def due_subs() -> list:
    with db._conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM co_subs WHERE status = 'active' AND next_at <= ?", (_now(),))]


def listing(store_id: int = None, limit: int = 300) -> dict:
    w, a = (" WHERE store_id = ?", (store_id,)) if store_id else ("", ())
    with db._conn() as con:
        orders = [dict(r) for r in con.execute(f"SELECT * FROM co_orders{w} ORDER BY id DESC LIMIT {int(limit)}", a)]
        subs = [dict(r) for r in con.execute(f"SELECT * FROM co_subs{w} ORDER BY id DESC", a)]
    for s in subs:
        s.pop("token", None)
        s.pop("method_id", None)
    return {"orders": orders, "subs": subs}


def shopify_order_input(o: dict, od: dict, intent_id: str, sub_token: str = None, base_url: str = "",
                        shipping: float = None) -> dict:
    """orderCreate input: the variant at the price charged, shipping, the Airwallex payment as a successful sale."""
    addr = json.loads(od["address"] or "{}")
    first, _, last = (od["name"] or "").partition(" ")
    money = lambda v: {"shopMoney": {"amount": f"{v:.2f}", "currencyCode": od["currency"]}}
    shipping = float(o["shipping"] or 0) if shipping is None else float(shipping)
    item = round(od["amount"] - shipping, 2)
    a = {"firstName": first, "lastName": last or first, "address1": addr.get("address1", ""),
         "address2": addr.get("address2", ""), "city": addr.get("city", ""), "zip": addr.get("zip", ""),
         "countryCode": addr.get("country", "AU"), "phone": od["phone"] or None}
    if addr.get("province"):
        a["provinceCode"] = addr["province"]
    tags = ["profitdesk-checkout", {"one": "one-time", "first": "subscription", "renewal": "subscription-renewal"}[od["kind"]]]
    if od["test"]:
        tags.append("checkout-test")
    note = f"Paid on the ProfitDesk checkout (Airwallex {intent_id})."
    attrs = [{"key": "Airwallex payment", "value": intent_id}]
    if sub_token:
        note += f" Subscription every {o['interval_days']} days."
        attrs.append({"key": "Manage subscription", "value": f"{base_url}/pay/manage/{sub_token}"})
    return {
        "test": bool(od["test"]), "email": od["email"], "currency": od["currency"],
        "lineItems": [{"variantId": o["variant_id"], "quantity": 1, "priceSet": money(item)}],
        "shippingAddress": {k: v for k, v in a.items() if v is not None},
        "billingAddress": {k: v for k, v in a.items() if v is not None},
        "shippingLines": [{"title": "Shipping" if shipping else "Free shipping", "priceSet": money(shipping)}],
        "financialStatus": "PAID",
        "transactions": [{"kind": "SALE", "status": "SUCCESS", "gateway": "Airwallex", "authorizationCode": intent_id,
                          "amountSet": money(od["amount"])}],
        "tags": tags, "note": note, "customAttributes": attrs,
        "sourceName": "profitdesk-checkout", "sourceIdentifier": od["ref"],
    }


ORDER_CREATE = """
mutation OrderCreate($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
  orderCreate(order: $order, options: $options) {
    order { id name }
    userErrors { field message }
  }
}
"""


def intent_paid(intent: dict, od: dict) -> bool:
    """The intent really is paid, for the amount and currency of our order."""
    return (intent.get("status") == "SUCCEEDED"
            and abs(float(intent.get("amount") or 0) - od["amount"]) < 0.005
            and (intent.get("currency") or "").upper() == od["currency"].upper())


def method_of(intent: dict) -> tuple:
    """(payment method id, type) the intent was paid with."""
    pm = ((intent.get("latest_payment_attempt") or {}).get("payment_method") or {})
    return pm.get("id"), pm.get("type")


def stamp() -> float:
    return time.time()
