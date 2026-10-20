"""EcomVital World (Phase 1): a live, isometric view of one store's shoppers. Viewing only.

Where the figures come from: the EcomVital pixel's events (`px_events`, see tracking.py).
- One avatar per pixel visitor (Shopify's anonymous clientId), sent to the browser only as a salted
  hash, so the page never sees the real id. Emails / phones / IP / browser / cookies are never read here.
- A visitor's *session* = their events with no gap longer than `leave_minutes`. State from the
  session: checkout (a checkout step, no purchase yet) > cart (added to cart) > browsing; "inactive"
  once nothing has arrived for `inactive_minutes`; gone after `leave_minutes`. Both thresholds are
  the owner's setting `world:<store>` (Tracking desk, Manage).
- Activity time = when EcomVital *received* the event (`got_at`), not the shopper's own clock;
  older rows (before got_at existed) fall back to `at`. Late events just refresh the snapshot.
- Purchases: pixel `checkout_completed` (celebrates on that shopper's avatar) and rows `order:<id>`
  (orders Shopify had that the pixel never saw: counted in the purchase total, celebrated at the
  till with no avatar, because there is no session to link them to; never guessed).
  One celebration per order (order id, else checkout token), also when a late pixel copy replaces
  an `order:` row.
- Live visitors are counted from sessions, never from hourly totals. Today's purchases (store
  clock) are a separate, historical figure.

The browser polls GET /api/world/state every few seconds with the cursor (px_events rowid) it last
saw; each answer is a full snapshot of the active sessions (so a reconnect just catches up) plus the
purchases newer than the cursor. Snapshots are built in a thread and kept 2 s per store, so many
screens cost one query.
"""
import asyncio
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

import auth
import db

router = APIRouter()

DEFAULT_STORES = (6,)                     # Phase 1: CUTEHOME only (setting `world_stores` = "6,7" to add more)
DEFAULTS = {"inactive_minutes": 5, "leave_minutes": 30}
CHECKOUT = {"checkout_started", "checkout_contact_info_submitted", "checkout_shipping_info_submitted",
            "payment_info_submitted"}
MAX_AVATARS = 400                         # more than this: the newest shown, the rest counted
_cache: dict = {}                         # store -> (built_at, snapshot)
_celebrated: dict = {}                    # store -> {order key: rowid first seen} (one party per order)


def stores_on() -> list:
    raw = db.get_setting("world_stores") or ""
    ids = [int(x) for x in re.findall(r"\d+", raw)] or list(DEFAULT_STORES)
    return ids


def cfg(store_id: int) -> dict:
    try:
        got = json.loads(db.get_setting(f"world:{store_id}") or "{}")
    except ValueError:
        got = {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        try:
            out[k] = int(got.get(k, out[k]))
        except (TypeError, ValueError):
            pass
    return out


def _salt() -> str:
    s = db.get_setting("world_salt")
    if not s:
        s = secrets.token_hex(16)
        db.set_setting("world_salt", s)
    return s


def _hid(salt: str, v: str) -> str:
    return hashlib.sha256((salt + "|" + v).encode()).hexdigest()[:12]


def _ts(v):
    try:
        return datetime.fromisoformat(v).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def zone_of(name: str, url: str) -> str:
    """Where in the shop the avatar stands: from the event, and the page's path (no query string)."""
    if name in CHECKOUT or name == "checkout_completed":
        return "checkout"
    if name == "product_added_to_cart":
        return "cart"
    path = re.sub(r"^https?://[^/]+", "", url or "").split("?")[0].split("#")[0].lower()
    if name == "product_viewed" or "/products/" in path:
        return "product"
    if "/cart" in path:
        return "cart"
    if "/collections" in path or "/search" in path:
        return "aisle"
    if "/checkouts" in path:
        return "checkout"
    return "entrance"


def build(store: dict) -> dict:
    """One snapshot of the store's world (runs in a thread)."""
    sid = store["id"]
    c = cfg(sid)
    salt = _salt()
    now = datetime.now(timezone.utc)
    leave = timedelta(minutes=max(c["leave_minutes"], c["inactive_minutes"] + 1))
    idle = timedelta(minutes=c["inactive_minutes"])
    # wide margin on `at` (indexed): a session can have started up to ~2 h before its last event
    since = (now - leave - timedelta(hours=2)).isoformat(timespec="seconds")
    try:
        tz = ZoneInfo(store.get("timezone") or "UTC")
    except Exception:
        tz = timezone.utc
    day0 = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_from = day0.astimezone(timezone.utc).isoformat(timespec="seconds")
    with db._conn() as con:
        rows = con.execute(
            "SELECT rowid r, id, client_id, name, at, got_at, url, order_id, token, value, currency FROM px_events"
            " WHERE store_id = ? AND at >= ? ORDER BY rowid", (sid, since)).fetchall()
        cursor = con.execute("SELECT MAX(rowid) FROM px_events").fetchone()[0] or 0
        # today's purchases: one per order, pixel + Shopify-order rows (history, not live)
        t = con.execute(
            "SELECT COUNT(*), SUM(v), MAX(c) FROM (SELECT COALESCE(order_id, token, id) o, MAX(COALESCE(value, 0)) v,"
            " MAX(currency) c FROM px_events WHERE store_id = ? AND at >= ? AND name = 'checkout_completed' GROUP BY o)",
            (sid, day_from)).fetchone()

    by_client: dict = {}
    purchases = []
    last = None
    for r in rows:
        seen = _ts(r["got_at"]) or _ts(r["at"])
        if not seen:
            continue
        last = max(last, seen) if last else seen
        if r["name"] == "checkout_completed":
            purchases.append((r, seen))
        if str(r["id"]).startswith("order:"):
            continue                                   # an order from Shopify: no session, no avatar
        by_client.setdefault(r["client_id"], []).append((seen, r))

    avatars = []
    for cid, evs in by_client.items():
        evs.sort(key=lambda x: x[0])
        if now - evs[-1][0] > leave:
            continue
        start = len(evs) - 1                           # this session: back to the first gap longer than `leave`
        while start > 0 and evs[start][0] - evs[start - 1][0] <= leave:
            start -= 1
        sess = evs[start:]
        names = [e[1]["name"] for e in sess]
        if "checkout_completed" in names:
            i = len(names) - 1 - names[::-1].index("checkout_completed")
            after = sess[i + 1:]
            if not after or not any(e[1]["name"] in ("page_viewed", "product_viewed", "product_added_to_cart")
                                    and e[0] - sess[i][0] > timedelta(minutes=2) for e in after):
                if now - sess[i][0] <= timedelta(minutes=2):
                    stage = "purchased"                # shown while its party plays, then gone
                else:
                    continue                           # done shopping: leaves the store
            else:
                sess = after                           # came back to shop again: a fresh visit
                names = [e[1]["name"] for e in sess]
                stage = None
        else:
            stage = None
        if stage is None:
            if any(n in CHECKOUT for n in names):
                stage = "checkout"
            elif "product_added_to_cart" in names:
                stage = "cart"
            else:
                stage = "browsing"
        seen, r = sess[-1]
        idle_now = now - seen >= idle and stage != "purchased"
        avatars.append({
            "id": _hid(salt, cid),
            "stage": stage,
            "state": "inactive" if idle_now else stage,
            "zone": zone_of(r["name"], r["url"]),
            "since": sess[0][0].isoformat(timespec="seconds"),
            "seen": seen.isoformat(timespec="seconds"),
            "pages": sum(1 for n in names if n in ("page_viewed",)),
        })
    avatars.sort(key=lambda a: a["seen"], reverse=True)

    marks = _celebrated.setdefault(sid, {})
    party = []
    for r, seen in purchases:
        key = str(r["order_id"] or r["token"] or r["id"])
        first = marks.setdefault(key, r["r"])
        if first != r["r"]:
            continue                                   # this order already had its party
        is_order = str(r["id"]).startswith("order:")
        party.append({"rowid": first, "order": _hid(salt, "o:" + key),
                      "avatar": None if is_order else _hid(salt, r["client_id"]),
                      "source": "shopify_order" if is_order else "pixel",
                      "value": r["value"], "currency": r["currency"],
                      "at": (_ts(r["at"]) or seen).isoformat(timespec="seconds"),
                      "got": seen.isoformat(timespec="seconds")})
    if len(marks) > 5000:                              # keep the memory small
        for k in list(marks)[:-3000]:
            marks.pop(k, None)

    counts = {k: sum(1 for a in avatars if a["state"] == k) for k in ("browsing", "cart", "checkout", "inactive", "purchased")}
    return {
        "store": {"id": sid, "name": (store.get("name") or "").strip(), "currency": store.get("currency"),
                  "timezone": store.get("timezone")},
        "mode": "live",
        "now": now.isoformat(timespec="seconds"),
        "last_event": last.isoformat(timespec="seconds") if last else None,
        "cursor": cursor,
        "settings": c,
        "active": counts["browsing"] + counts["cart"] + counts["checkout"],
        "counts": counts,
        "avatars": avatars[:MAX_AVATARS],
        "hidden": max(0, len(avatars) - MAX_AVATARS),
        "today": {"date": day0.date().isoformat(), "orders": t[0] or 0, "value": round(t[1] or 0, 2),
                  "currency": t[2] or store.get("currency")},
        "_party": party,
    }


def _store(store_id: int) -> dict:
    if store_id not in stores_on():
        raise HTTPException(404, "That store isn't in World yet.")
    st = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    return st


@router.get("/api/world/stores")
def api_world_stores():
    on = stores_on()
    out = []
    for x in db.list_stores():
        if not x.get("access_token"):
            continue
        out.append({"id": x["id"], "name": (x["name"] or "").strip(), "live": x["id"] in on,
                    "pixel": bool(db.get_setting(f"px_key:{x['id']}"))})
    out.sort(key=lambda s: (not s["live"], s["name"].lower()))
    return {"stores": out, "default": on[0] if on else None}


@router.get("/api/world/state")
async def api_world_state(store: int, cursor: int = 0):
    """Snapshot of the active sessions + purchases newer than `cursor` (0 = first look: none)."""
    st = _store(store)
    hit = _cache.get(store)
    if not hit or time.time() - hit[0] > 2:
        snap = await asyncio.to_thread(build, st)
        hit = (time.time(), snap)
        _cache[store] = hit
    snap = dict(hit[1])
    party = snap.pop("_party")
    snap["purchases"] = [p for p in party if cursor and p["rowid"] > cursor][-30:]
    return snap


@router.put("/api/world/settings/{store_id}")
def api_world_settings(store_id: int, payload: dict, request: Request):
    if not auth.at_least(getattr(request.state, "user", None) or {}, "tracking", "manage"):
        raise HTTPException(403, "Changing World's settings needs Manage access to Tracking.")
    _store(store_id)
    c = cfg(store_id)
    try:
        ina = int(payload.get("inactive_minutes", c["inactive_minutes"]))
        lv = int(payload.get("leave_minutes", c["leave_minutes"]))
    except (TypeError, ValueError):
        raise HTTPException(400, "Use whole minutes.")
    if not 1 <= ina <= 60 or not ina < lv <= 240:
        raise HTTPException(400, "Inactive after 1–60 minutes; leaves the store later than that, up to 240.")
    db.set_setting(f"world:{store_id}", json.dumps({"inactive_minutes": ina, "leave_minutes": lv}))
    _cache.pop(store_id, None)
    return {"ok": True, "settings": cfg(store_id)}
