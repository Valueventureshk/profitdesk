"""Apps → Tracking: ProfitDesk's own first-party tracking (like wetracked.io).

The ProfitDesk pixel (shopify-app/extensions/profitdesk-pixel, a Shopify web pixel) posts every
shopper event to /t/e: page / product views, add to cart, each checkout step and the purchase,
with the URL (UTMs, fbclid / gclid / ttclid) and Meta's browser cookies (_fbp / _fbc).

Each visitor (Shopify's clientId) keeps its ad touches. A touch is a landing URL with UTMs or a
click id; every later event is credited to the visitor's latest touch from the last 7 days
(last click), like wetracked.io's UTM reporting. Emails and phones are kept only as SHA-256 hashes
(what Meta's Conversions API takes), never in plain text.

Modes per store: watch (track and report, send nothing) | send (also Meta Conversions API, next).
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import db

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id")
CLICK_KEYS = ("fbclid", "gclid", "ttclid", "msclkid", "wbraid", "gbraid")
WINDOW = timedelta(days=7)
EVENTS = {"page_viewed", "product_viewed", "product_added_to_cart", "checkout_started",
          "checkout_contact_info_submitted", "checkout_shipping_info_submitted", "payment_info_submitted",
          "checkout_completed"}


def _hash(v: str, phone: bool = False) -> str:
    v = (v or "").strip().lower()
    if phone:
        v = re.sub(r"\D", "", v)
    return hashlib.sha256(v.encode()).hexdigest() if v else ""


def _gid_num(v) -> str:
    return str(v or "").rsplit("/", 1)[-1]


def touch_of(url: str) -> dict:
    """UTMs and click ids in a landing URL (empty when it has none)."""
    try:
        q = parse_qs(urlparse(url or "").query)
    except ValueError:
        return {}
    t = {k: q[k][0][:300] for k in UTM_KEYS + CLICK_KEYS if q.get(k) and q[k][0]}
    return t


def record(store_id: int, ev: dict, ip: str) -> bool:
    """Save one pixel event and update its visitor. False if it isn't a usable event."""
    name, eid, cid = ev.get("n"), str(ev.get("id") or "")[:80], str(ev.get("cid") or "")[:80]
    if name not in EVENTS or not eid or not cid:
        return False
    try:
        at = datetime.fromisoformat(str(ev.get("t")).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        at = datetime.now(timezone.utc)
    at_s = at.isoformat(timespec="seconds")
    d = ev.get("d") or {}
    url = str(ev.get("url") or "")[:1000]
    touch = touch_of(url)
    with db._conn() as con:
        v = con.execute("SELECT * FROM px_visitors WHERE store_id = ? AND client_id = ?", (store_id, cid)).fetchone()
        v = dict(v) if v else None
        if touch:
            # a fresh ad click: it becomes this visitor's latest touch (the first one is kept too)
            con.execute(
                "INSERT INTO px_visitors (store_id, client_id, first_at, last_at, first_touch, last_touch, touch_at,"
                " fbp, fbc, ip, ua) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(store_id, client_id) DO UPDATE SET"
                " last_at = excluded.last_at, last_touch = excluded.last_touch, touch_at = excluded.touch_at,"
                " fbp = COALESCE(NULLIF(excluded.fbp, ''), fbp), fbc = COALESCE(NULLIF(excluded.fbc, ''), fbc),"
                " ip = excluded.ip, ua = excluded.ua",
                (store_id, cid, at_s, at_s, json.dumps(touch), json.dumps(touch), at_s,
                 ev.get("fbp") or "", ev.get("fbc") or "", ip, (ev.get("ua") or "")[:400]))
            last, touch_at = touch, at
        else:
            con.execute(
                "INSERT INTO px_visitors (store_id, client_id, first_at, last_at, fbp, fbc, ip, ua) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(store_id, client_id) DO UPDATE SET last_at = excluded.last_at,"
                " fbp = COALESCE(NULLIF(excluded.fbp, ''), fbp), fbc = COALESCE(NULLIF(excluded.fbc, ''), fbc),"
                " ip = excluded.ip, ua = excluded.ua",
                (store_id, cid, at_s, at_s, ev.get("fbp") or "", ev.get("fbc") or "", ip, (ev.get("ua") or "")[:400]))
            last = json.loads(v["last_touch"]) if v and v.get("last_touch") else {}
            touch_at = datetime.fromisoformat(v["touch_at"]) if v and v.get("touch_at") else None
        credit = last if last and touch_at and at - touch_at <= WINDOW else {}
        try:
            value = float(d.get("value")) if d.get("value") is not None else None
        except (TypeError, ValueError):
            value = None
        con.execute(
            "INSERT OR IGNORE INTO px_events (id, store_id, client_id, name, at, url, ref, value, currency,"
            " product_id, variant_id, quantity, token, order_id, em_hash, ph_hash, fbp, fbc, ip, ua,"
            " a_source, a_medium, a_campaign, a_content, a_term, a_id, a_click)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, store_id, cid, name, at_s, url, str(ev.get("ref") or "")[:500], value, d.get("currency"),
             _gid_num(d.get("product_id")), _gid_num(d.get("variant_id")), d.get("quantity"),
             (d.get("token") or "")[:80], _gid_num(d.get("order_id")) or None,
             _hash(d.get("email")), _hash(d.get("phone"), True), ev.get("fbp") or "", ev.get("fbc") or "",
             ip, (ev.get("ua") or "")[:400],
             credit.get("utm_source"), credit.get("utm_medium"), credit.get("utm_campaign"), credit.get("utm_content"),
             credit.get("utm_term"), credit.get("utm_id"),
             next((k for k in CLICK_KEYS if credit.get(k)), None)))
    return True


# which UTM each level reads by default (the usual Meta URL template: campaign / ad set / ad names)
LEVEL_FIELD = {"campaign": "a_campaign", "adset": "a_term", "ad": "a_content"}
KEY_FIELD = {"utm_id": "a_id", "utm_source": "a_source", "utm_medium": "a_medium", "utm_campaign": "a_campaign",
             "utm_content": "a_content", "utm_term": "a_term"}
METRICS = {"atc": ("product_added_to_cart",), "ic": ("checkout_started",), "purchase": ("checkout_completed",)}


def report(store_id: int, start_utc: str, end_utc: str, field: str) -> dict:
    """Rows grouped by one attribution field: count + value of adds to cart, checkouts and purchases.
    Purchases count once per order."""
    col = field if field in KEY_FIELD.values() else "a_campaign"
    rows = {}
    with db._conn() as con:
        for r in con.execute(
                f"SELECT COALESCE(NULLIF({col}, ''), 'N/A') g, name, COUNT(*) n, SUM(COALESCE(value, 0)) v"
                " FROM px_events WHERE store_id = ? AND at >= ? AND at < ? AND name IN"
                " ('product_added_to_cart', 'checkout_started') GROUP BY g, name", (store_id, start_utc, end_utc)):
            g = rows.setdefault(r["g"], {"name": r["g"]})
            k = "atc" if r["name"] == "product_added_to_cart" else "ic"
            g[k], g[k + "_value"] = r["n"], r["v"]
        for r in con.execute(
                f"SELECT g, COUNT(*) n, SUM(v) v FROM (SELECT COALESCE(NULLIF({col}, ''), 'N/A') g,"
                " COALESCE(order_id, token, id) o, MAX(COALESCE(value, 0)) v FROM px_events WHERE store_id = ? AND"
                " at >= ? AND at < ? AND name = 'checkout_completed' GROUP BY o) GROUP BY g",
                (store_id, start_utc, end_utc)):
            g = rows.setdefault(r["g"], {"name": r["g"]})
            g["purchase"], g["purchase_value"] = r["n"], r["v"]
    out = sorted(rows.values(), key=lambda x: (x["name"] == "N/A", -(x.get("purchase_value") or 0), -(x.get("atc") or 0)))
    total = {k: sum(x.get(k) or 0 for x in out) for k in ("atc", "atc_value", "ic", "ic_value", "purchase", "purchase_value")}
    return {"rows": out, "total": total}


def health(store_id: int) -> dict:
    """Is the pixel sending? Events in the last hour / day, last event time."""
    now = datetime.now(timezone.utc)
    with db._conn() as con:
        last = con.execute("SELECT MAX(at) FROM px_events WHERE store_id = ?", (store_id,)).fetchone()[0]
        hour = con.execute("SELECT COUNT(*) FROM px_events WHERE store_id = ? AND at >= ?",
                           (store_id, (now - timedelta(hours=1)).isoformat(timespec="seconds"))).fetchone()[0]
        day = con.execute("SELECT name, COUNT(*) FROM px_events WHERE store_id = ? AND at >= ? GROUP BY name",
                          (store_id, (now - timedelta(days=1)).isoformat(timespec="seconds"))).fetchall()
    return {"last": last, "hour": hour, "day": {r[0]: r[1] for r in day}}


NAMES = {"product_added_to_cart": "Add to cart", "checkout_started": "Checkout started",
         "checkout_completed": "Purchase", "product_viewed": "Product view", "page_viewed": "Page view",
         "checkout_contact_info_submitted": "Contact info", "checkout_shipping_info_submitted": "Shipping info",
         "payment_info_submitted": "Payment info"}


def events(store_id: int, start_utc: str, end_utc: str, field: str, value: str, limit: int = 500) -> list:
    """The single events behind one report row (adds to cart, checkouts, purchases), newest first,
    with the full ad click each one is credited to."""
    col = field if field in KEY_FIELD.values() else "a_campaign"
    cond = f"COALESCE(NULLIF({col}, ''), 'N/A') = ?"
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, client_id, name, at, url, value, currency, product_id, variant_id, quantity, order_id,"
            " a_source, a_medium, a_campaign, a_term, a_content, a_id, a_click FROM px_events"
            f" WHERE store_id = ? AND at >= ? AND at < ? AND {cond} AND name IN"
            " ('product_added_to_cart', 'checkout_started', 'checkout_completed') ORDER BY at DESC LIMIT ?",
            (store_id, start_utc, end_utc, value, limit))]
    seen = set()
    out = []
    for r in rows:
        if r["name"] == "checkout_completed":            # one line per order
            k = r["order_id"] or r["id"]
            if k in seen:
                continue
            seen.add(k)
        r["label"] = NAMES.get(r["name"], r["name"])
        r["path"] = re.sub(r"^https?://[^/]+", "", r["url"] or "").split("?")[0][:160]
        out.append(r)
    return out


def journey(store_id: int, client_id: str) -> list:
    """Everything one shopper did (newest last), for the per-visitor view."""
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT name, at, url, value, order_id, a_campaign, a_term, a_content, a_click FROM px_events"
            " WHERE store_id = ? AND client_id = ? ORDER BY at LIMIT 300", (store_id, client_id))]
    for r in rows:
        r["label"] = NAMES.get(r["name"], r["name"])
        r["path"] = re.sub(r"^https?://[^/]+", "", r["url"] or "").split("?")[0][:160]
    return rows


LEVEL_OF_FIELD = {"a_campaign": "campaign", "a_term": "adset", "a_content": "ad"}


def with_spend(rep: dict, spends: list, factor: float) -> dict:
    """Add Meta spend to report rows (matched by the name in the UTM = the campaign / ad set / ad name),
    plus cost per purchase and ROAS (purchase value / spend). Rows that spent but tracked nothing are
    added too: they matter most."""
    by = {}
    for x in spends:
        k = (x["name"] or "").strip().lower()
        if not k:
            continue
        t = by.setdefault(k, {"name": x["name"], "spend": 0.0, "impressions": 0, "clicks": 0})
        t["spend"] += x["spend"] * factor
        t["impressions"] += x["impressions"]
        t["clicks"] += x["clicks"]
    rows = {r["name"].strip().lower(): r for r in rep["rows"]}
    for k, t in by.items():
        r = rows.get(k)
        if r is None:
            if not t["spend"]:
                continue
            r = rows[k] = {"name": t["name"]}
            rep["rows"].append(r)
        r.update(spend=t["spend"], impressions=t["impressions"], link_clicks=t["clicks"])
    for r in rep["rows"]:
        sp = r.get("spend") or 0.0
        r["cpa"] = sp / r["purchase"] if sp and r.get("purchase") else None
        r["roas"] = (r.get("purchase_value") or 0) / sp if sp else None
    tot = rep["total"]
    tot["spend"] = sum(r.get("spend") or 0 for r in rep["rows"])
    tot["cpa"] = tot["spend"] / tot["purchase"] if tot["spend"] and tot["purchase"] else None
    tot["roas"] = tot["purchase_value"] / tot["spend"] if tot["spend"] else None
    rep["has_spend"] = True
    return rep
