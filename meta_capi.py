"""Apps → Tracking → Meta: send EcomVital's own tracking to Meta's Conversions API (built 23 Oct 2026).

Every pixel event (tracking.py, px_events) becomes a Meta server event: PageView, ViewContent, AddToCart,
InitiateCheckout, AddPaymentInfo, Purchase. Purchases also come from Shopify orders the browser missed ("order:<id>"
rows), so Meta gets every order. Each event carries what helps Meta match it to a person: hashed email / phone
(already stored hashed), IP, browser, Meta's _fbp / _fbc cookies (an fbc is rebuilt from the ad click's fbclid when
the cookie is missing) and a hashed visitor id.

Modes per store (setting `capi:<store>`), safest first:
  off     nothing is sent
  test    to the store's main pixel WITH Meta's test code: shows only in Events Manager → Test events, never counted
  shadow  to a SEPARATE dataset (pixel) no campaign uses: a week's parallel run next to Shopify's Meta app,
          comparing match quality and purchase counts, with no double counting
  live    to the main pixel (campaigns optimise on it). Meant for the day Shopify's Meta app stops sending; while
          both send, Meta only merges them if the event ids match, which isn't guaranteed.
Each mode change starts from that moment (no old events are sent). A purchase goes once per order and pixel
(capi_sent), with the order id as its event id.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

GRAPH = "https://graph.facebook.com"
MODES = ("off", "test", "shadow", "live")
EVENT_MAP = {"page_viewed": "PageView", "product_viewed": "ViewContent", "product_added_to_cart": "AddToCart",
             "checkout_started": "InitiateCheckout", "payment_info_submitted": "AddPaymentInfo",
             "checkout_completed": "Purchase"}
META_NAMES = {v: k for k, v in EVENT_MAP.items()}
DEFAULT_EVENTS = list(EVENT_MAP.values())
MAX_AGE = timedelta(days=6, hours=20)           # Meta refuses events older than 7 days
BATCH = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS capi_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id  INTEGER NOT NULL,
    at        TEXT    NOT NULL,
    pixel_id  TEXT,
    mode      TEXT,
    counts    TEXT,                       -- JSON {Meta event name: n} in this batch
    sent      INTEGER,
    received  INTEGER,                    -- what Meta says it took
    error     TEXT,
    trace     TEXT
);
CREATE INDEX IF NOT EXISTS capi_log_store ON capi_log (store_id, at);
CREATE TABLE IF NOT EXISTS capi_sent (
    store_id  INTEGER NOT NULL,
    pixel_id  TEXT    NOT NULL,
    order_key TEXT    NOT NULL,           -- one Purchase per order and pixel
    at        TEXT    NOT NULL,
    PRIMARY KEY (store_id, pixel_id, order_key)
);
"""

DEFAULTS = {"mode": "off", "test_code": "", "shadow_pixel": "", "events": DEFAULT_EVENTS, "since_rowid": None,
            "started_at": None, "by": "", "fails": 0}


class CapiError(Exception):
    pass


def cfg_of(raw: str | None) -> dict:
    try:
        got = json.loads(raw or "{}")
    except ValueError:
        got = {}
    out = {**DEFAULTS, **{k: v for k, v in got.items() if k in DEFAULTS}}
    out["events"] = [e for e in (out["events"] or []) if e in META_NAMES] or []
    return out


def target(cfg: dict, main_pixel: str) -> tuple:
    """(pixel id, test code or '') the mode sends to, or (None, reason)."""
    m = cfg["mode"]
    if m == "off":
        return None, "off"
    if m == "shadow":
        return (cfg["shadow_pixel"] or None), ("" if cfg["shadow_pixel"] else "pick the separate dataset (pixel) for the parallel run")
    if not main_pixel:
        return None, "add the store's Meta pixel (dataset) ID"
    if m == "test":
        return (main_pixel, cfg["test_code"]) if cfg["test_code"] else (None, "paste the test code from Events Manager → Test events")
    return main_pixel, ""


def sha(v: str) -> str:
    v = (v or "").strip().lower()
    return hashlib.sha256(v.encode()).hexdigest() if v else ""


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def fbc_from_touch(touch_json: str, touch_at: str) -> str:
    """Meta's fbc value rebuilt from the ad click (fb.1.<ms>.<fbclid>) when the browser cookie is missing."""
    try:
        t = json.loads(touch_json or "{}")
    except ValueError:
        return ""
    if not t.get("fbclid") or not touch_at:
        return ""
    try:
        ms = int(datetime.fromisoformat(touch_at).timestamp() * 1000)
    except ValueError:
        return ""
    return f"fb.1.{ms}.{t['fbclid']}"


def build(row: dict, visitor: dict | None, domain: str, store_currency: str) -> dict | None:
    """One px_events row (+ its visitor) -> a Meta server event, or None when Meta wouldn't take it."""
    name = EVENT_MAP.get(row["name"])
    if not name:
        return None
    from_order = str(row["id"]).startswith("order:")
    v = visitor or {}
    ua = "" if from_order else (row.get("ua") or "")
    ip = "" if from_order else (row.get("ip") or "")
    if from_order:                    # an order the browser missed: use what we know of the same shopper, if matched
        ua, ip = v.get("ua") or "", v.get("ip") or ""
    user = {}
    if row.get("em_hash"):
        user["em"] = [row["em_hash"]]
    if row.get("ph_hash"):
        user["ph"] = [row["ph_hash"]]
    if ip:
        user["client_ip_address"] = ip
    if ua:
        user["client_user_agent"] = ua
    fbp = row.get("fbp") or v.get("fbp") or ""
    fbc = row.get("fbc") or v.get("fbc") or (fbc_from_touch(v.get("last_touch"), v.get("touch_at")) if row.get("a_click") == "fbclid" else "")
    if fbp:
        user["fbp"] = fbp
    if fbc:
        user["fbc"] = fbc
    cid = str(row.get("client_id") or "")
    if cid and not cid.startswith("order:"):
        user["external_id"] = [sha(cid)]
    if not user:
        return None
    ev = {"event_name": name, "event_time": _epoch(row["at"]), "action_source": "website",
          "event_id": str(row["id"]), "user_data": user,
          "event_source_url": row.get("url") or (f"https://{domain}/" if domain else "")}
    if not ua:                         # Meta needs a browser for website events; an order with no visit isn't one
        ev["action_source"] = "system_generated"
        ev.pop("event_source_url", None)
    data = {}
    if row.get("value") is not None:
        data["value"] = round(float(row["value"]), 2)
        data["currency"] = (row.get("currency") or store_currency or "").upper()
    item = row.get("variant_id") or row.get("product_id")
    if item and name in ("ViewContent", "AddToCart"):
        data.update(content_ids=[str(item)], content_type="product")
        if row.get("quantity"):
            data["num_items"] = int(row["quantity"])
    if name == "Purchase":
        if row.get("order_id"):
            data["order_id"] = str(row["order_id"])
            ev["event_id"] = str(row["order_id"])          # the same id whichever way the purchase reached us
        if "value" not in data:
            return None
    if data:
        ev["custom_data"] = data
    return ev


def order_key(row: dict) -> str:
    return str(row.get("order_id") or row.get("token") or row["id"])


def explain(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
    except ValueError:
        return r.text[:300]
    msg = err.get("error_user_msg") or err.get("message") or str(err)[:300]
    code = err.get("code")
    if code in (190, 102):
        return "The Meta token isn't valid any more (paste a new one, or reconnect Meta in Settings)."
    if code in (10, 200, 294) or "permission" in msg.lower():
        return ("Meta says the token can't send to this pixel. Give the System User access to the dataset (Business settings → "
                "Data sources → Datasets → the pixel → Assign people / Partners), or paste a Conversions API token made in "
                f"Events Manager. ({msg[:160]})")
    if code == 100 and "does not exist" in msg.lower():
        return f"Meta can't find that pixel (dataset) ID, or the token can't see it. ({msg[:160]})"
    return f"Meta said: {msg[:300]}"


async def send(token: str, pixel: str, events: list, test_code: str, version: str) -> dict:
    body = {"data": json.dumps(events), "access_token": token}
    if test_code:
        body["test_event_code"] = test_code
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(f"{GRAPH}/{version}/{pixel}/events", data=body)
        except httpx.RequestError as e:
            raise CapiError(f"Could not reach Meta ({type(e).__name__}).") from e
    if r.status_code >= 400:
        raise CapiError(explain(r))
    got = r.json()
    return {"received": int(got.get("events_received") or 0), "trace": got.get("fbtrace_id") or "",
            "messages": got.get("messages") or []}


def too_old(at_iso: str, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(at_iso.replace("Z", "+00:00")) > MAX_AGE
    except ValueError:
        return True


def valid_pixel(v) -> str:
    pid = re.sub(r"\D", "", str(v or ""))
    if pid and not 10 <= len(pid) <= 20:
        raise ValueError("A Meta pixel (dataset) ID is 15-16 digits.")
    return pid
