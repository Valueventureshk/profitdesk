"""Apps → PayPal Tracking: sends each order's tracking number to the PayPal payment it belongs to.

PayPal releases money sooner and sides with the seller more often when every PayPal sale has tracking.
The SCM desk already knows every parcel (`shipments`, kept by `_scm_loop` + 17TRACK); this sends the
ones that are moving.

    which parcels  SCM status in transit (setting `later` also allows ready for pickup / out for
                   delivery). Never delivered, info received, pending, not found or no tracking yet.
    which orders   paid through PayPal (Shopify transaction gateway contains "paypal").
    which payment  exact ids only: Shopify's payment id is PayPal's invoice id, or Shopify's
                   authorization code is PayPal's transaction id, found in a connected PayPal
                   account's transaction feed. No amount guessing: no exact id = "not matched".
    how            POST /v1/shipping/trackers-batch, one tracker per request (so each answer is
                   unambiguous), status SHIPPED, notify_buyer false. A carrier PayPal refuses is
                   resent once as OTHER + the carrier's name. Errors retried MAX_TRIES times.

Setting `pp_track` {on, stores: [ids], later, by, at} (off by default); `pp_track_last` = last run.
Every parcel seen gets a row in `pp_track` (state waiting / unmatched / ready / sent / error / failed);
a sent row is never sent again. Each Shopify order's payments are kept in `pp_track_orders`.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

import auth
import db
import demo
import paypal_client as paypal
from shopify_client import ShopifyClient

router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS pp_track (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id     INTEGER NOT NULL,
    order_id     TEXT NOT NULL,
    order_name   TEXT,
    customer     TEXT,
    email        TEXT,
    number       TEXT NOT NULL,
    company      TEXT,
    carrier      TEXT,               -- PayPal's carrier code (OTHER + carrier_other when not on PayPal's list)
    carrier_other TEXT,
    scm_status   TEXT,
    fulfilled_at TEXT,
    state        TEXT NOT NULL,      -- waiting | unmatched | ready | sent | error | failed
    conn_id      INTEGER,            -- the PayPal connection that has the payment
    txn_id       TEXT,               -- PayPal transaction (capture) id
    amount       REAL,
    currency     TEXT,
    matched_by   TEXT,               -- payment id | authorization
    note         TEXT,
    tries        INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    submitted_at TEXT,
    submitted_by TEXT,               -- 'auto' (the loop) or the person who pressed Run now
    updated_at   TEXT,
    UNIQUE (store_id, order_id, number)
);
CREATE INDEX IF NOT EXISTS pp_track_state ON pp_track(state);
CREATE TABLE IF NOT EXISTS pp_track_orders (
    store_id   INTEGER NOT NULL,
    order_id   TEXT NOT NULL,
    payments   TEXT NOT NULL,        -- JSON [{gateway, kind, payment_id, auth, amount, currency}]
    checked_at TEXT NOT NULL,
    PRIMARY KEY (store_id, order_id)
);
"""
_ready = {"ok": False}

MOVING = ("in_transit",)
LATER = ("pickup", "out_for_delivery")
NEVER = ("delivered",)    # owner, 17 Oct 2026: delivered parcels are never sent, whatever the settings say
WINDOW_DAYS = 60          # parcels fulfilled this recently (PayPal's feed here covers 75 days)
MAX_TRIES = 3
MAX_SENDS = 200           # per run, so a first run with a backlog finishes in a few minutes
RECHECK_HOURS = 6         # unmatched orders: ask Shopify for their payments again after this

# Shopify / 17TRACK carrier name (lower case, contains) -> PayPal carrier code. Order matters.
CARRIERS = (
    ("usps", "USPS"), ("fedex", "FEDEX"), ("ups", "UPS"),
    ("dhl", "DHL"), ("australia post", "AU_AU_POST"), ("auspost", "AU_AU_POST"),
    ("canada post", "CA_CANADA_POST"), ("royal mail", "ROYAL_MAIL"), ("yun", "YUNEXPRESS"),
    ("china post", "CN_CHINA_POST_EMS"), ("china ems", "CN_CHINA_POST_EMS"), ("4px", "FOUR_PX_EXPRESS"),
    ("cainiao", "CAINIAO"), ("yanwen", "YANWEN"), ("sf express", "SF_EX"), ("aramex", "ARAMEX"),
    ("new zealand post", "NZ_NZ_POST"), ("nz post", "NZ_NZ_POST"), ("deutsche post", "DEUTSCHE_DE"),
    ("postnl", "POSTNL_INTERNATIONAL"), ("colissimo", "FR_COLIS"), ("la poste", "LA_POSTE_SUIVI"),
    ("hong kong post", "HK_POST"),
)

STATE_NAME = {"delivered": "Delivered: not sent", "waiting": "Waiting for in transit", "unmatched": "Not matched", "ready": "Ready to send",
              "sent": "Sent to PayPal", "error": "Error (will retry)", "failed": "Error (gave up)"}

_lock = asyncio.Lock()


def ensure():
    if not _ready["ok"]:
        with db._conn() as con:
            con.executescript(SCHEMA)
        _ready["ok"] = True


def config() -> dict:
    try:
        c = json.loads(db.get_setting("pp_track") or "{}")
    except ValueError:
        c = {}
    return {"on": bool(c.get("on")), "stores": [int(s) for s in c.get("stores") or []],
            "later": bool(c.get("later")), "by": c.get("by") or "", "at": c.get("at") or ""}


def carrier_of(company: str, carrier_name: str = "") -> tuple:
    """(PayPal carrier code, name for OTHER)."""
    for text in (company, carrier_name):
        low = (text or "").lower()
        if not low:
            continue
        for key, code in CARRIERS:
            if key == "ups" and not any(w == "ups" for w in low.replace("-", " ").split()):
                continue          # "ups" inside another word isn't UPS
            if key in low:
                return code, ""
    name = (company or carrier_name or "Other").strip()[:60]
    return "OTHER", name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- PayPal

def _paypal_error(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"PayPal error {r.status_code}: {r.text[:200]}"
    if r.status_code == 403:
        return ("PayPal refused to add tracking with this connection's keys. On developer.paypal.com → Apps & "
                "Credentials → this app, check it is a Live app on the same PayPal account, then try again.")
    parts = [body.get("message") or body.get("name") or ""]
    for e in body.get("errors") or []:
        parts.append(e.get("message") or e.get("name") or "")
        parts += [f"{d.get('field', '')} {d.get('issue', '')} {d.get('description', '')}".strip()
                  for d in e.get("details") or []]
    parts += [f"{d.get('field', '')} {d.get('issue', '')}".strip() for d in body.get("details") or []]
    return f"PayPal error {r.status_code}: " + "; ".join(p for p in parts if p)[:400]


async def add_tracker(conn: dict, tracker: dict) -> tuple:
    """(ok, note). One tracker: {transaction_id, tracking_number, carrier, carrier_name_other?}."""
    body = {"trackers": [{**tracker, "status": "SHIPPED", "notify_buyer": False}]}
    async with httpx.AsyncClient(timeout=60) as client:
        token = await paypal._token(client, conn["client_id"], conn["secret"])
        try:
            r = await client.post(f"{paypal.API}/v1/shipping/trackers-batch", json=body,
                                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        except httpx.RequestError as e:
            return False, f"Couldn't reach PayPal ({type(e).__name__})."
    if r.status_code == 401:
        paypal._tokens.pop(conn["client_id"], None)
    if r.status_code >= 400:
        return False, _paypal_error(r)
    out = r.json() if r.content else {}
    if out.get("tracker_identifiers"):
        return True, ""
    errs = out.get("errors") or []
    text = "; ".join(filter(None, [e.get("message") or e.get("name") for e in errs]
                     + [f"{d.get('field', '')} {d.get('issue', '')} {d.get('description', '')}".strip()
                        for e in errs for d in e.get("details") or []]))
    if "already" in text.lower() or "exist" in text.lower():
        return True, "Already on PayPal"
    return False, ("PayPal: " + text)[:400] if text else "PayPal didn't confirm the tracking number."


def _carrier_refused(note: str) -> bool:
    low = (note or "").lower()
    return "carrier" in low and any(w in low for w in ("invalid", "not supported", "unsupported", "enum", "not valid"))


async def _feed_index() -> tuple:
    """({invoice id: hit}, {transaction id: hit}, {conn id: conn}, problems) over every PayPal connection.
    hit = {conn_id, txn_id, amount, currency}. Only money in (T00…, positive)."""
    by_invoice, by_txn, conns, problems = {}, {}, {}, []
    for c in db.list_cash_connections():
        if c["provider"] != "paypal":
            continue
        conns[c["id"]] = c
        try:
            rows = await paypal.transactions(c["client_id"], c["secret"])
        except Exception as e:
            problems.append(f"{c['label']}: {e}")
            c["unread"] = True
            continue
        for t in rows:
            code = t.get("transaction_event_code") or ""
            amt = t.get("transaction_amount") or {}
            if not code.startswith("T00") or paypal._amount(amt) <= 0 or not t.get("transaction_id"):
                continue
            hit = {"conn_id": c["id"], "txn_id": t["transaction_id"], "amount": paypal._amount(amt),
                   "currency": amt.get("currency_code") or ""}
            by_txn[t["transaction_id"]] = hit
            if t.get("invoice_id"):
                by_invoice[t["invoice_id"]] = hit
    return by_invoice, by_txn, conns, problems


# ---------------------------------------------------------------- Shopify payments

PAYMENTS_QUERY = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Order {
      legacyResourceId
      transactions(first: 20) {
        gateway kind status paymentId authorizationCode
        amountSet { shopMoney { amount currencyCode } }
      }
    }
  }
}"""


async def _shopify_payments(store: dict, order_ids: list) -> dict:
    """{order id: [payment]} straight from Shopify, 50 orders per request."""
    out = {}
    cl = ShopifyClient(store["shop_domain"], store["access_token"])
    for i in range(0, len(order_ids), 50):
        part = order_ids[i:i + 50]
        for attempt in range(4):
            try:
                data = await cl.gql(PAYMENTS_QUERY, {"ids": [f"gid://shopify/Order/{o}" for o in part]})
                break
            except Exception as e:
                if "throttl" in str(e).lower() or "rate" in str(e).lower():
                    await asyncio.sleep(2 + attempt * 3)
                    continue
                raise
        else:
            continue
        for n in data.get("nodes") or []:
            if not n or not n.get("legacyResourceId"):
                continue
            out[str(n["legacyResourceId"])] = [{
                "gateway": t.get("gateway") or "", "kind": t.get("kind") or "",
                "payment_id": t.get("paymentId") or "", "auth": t.get("authorizationCode") or "",
                "amount": float(((t.get("amountSet") or {}).get("shopMoney") or {}).get("amount") or 0),
                "currency": ((t.get("amountSet") or {}).get("shopMoney") or {}).get("currencyCode") or "",
            } for t in n.get("transactions") or []
                if t.get("status") == "SUCCESS" and t.get("kind") in ("SALE", "CAPTURE")]
    return out


def _is_paypal(p: dict) -> bool:
    return "paypal" in (p.get("gateway") or "").lower()


def _match(payments: list, by_invoice: dict, by_txn: dict):
    """(hit, how) for the order's PayPal payment found in a connected PayPal account, else (None, None)."""
    for p in sorted([p for p in payments if _is_paypal(p)], key=lambda p: -(p.get("amount") or 0)):
        if p.get("payment_id") and p["payment_id"] in by_invoice:
            return by_invoice[p["payment_id"]], "payment id"
        if p.get("auth") and p["auth"] in by_txn:
            return by_txn[p["auth"]], "authorization"
    return None, None


# ---------------------------------------------------------------- the run

def _parcels(store_ids: list) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()[:19]
    with db._conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT store_id, order_id, order_name, customer, email, number, company, carrier_name, status,"
            " fulfilled_at FROM shipments WHERE number != '' AND cancelled = 0 AND fulfilled_at >= ?"
            f" AND store_id IN ({','.join('?' * len(store_ids))})", (since, *store_ids))]


async def run(send: bool = True, by: str = "auto", store_ids: list = None) -> dict:
    """Bring every parcel's row up to date; when `send` (switched on, or Run now) send the ready ones
    of the stores switched on. Returns a summary."""
    ensure()
    async with _lock:
        cfg = config()
        stores = {s["id"]: s for s in db.list_stores()
                  if s.get("access_token") and not demo.is_demo(s) and (not store_ids or s["id"] in store_ids)}
        summary = {"at": _now(), "by": by, "sent": 0, "errors": 0, "ready": 0, "unmatched": 0, "waiting": 0,
                   "not_paypal": 0, "problems": [], "sending": bool(send)}
        if not stores:
            return summary
        by_invoice, by_txn, conns, problems = await _feed_index()
        summary["problems"] += problems
        if not conns:
            summary["problems"].append("No PayPal account is connected (Cash flow → Settings).")
        allowed = MOVING + (LATER if cfg["later"] else ())
        parcels = _parcels(list(stores))
        with db._conn() as con:
            done, known = {}, {}
            for r in con.execute("SELECT store_id, order_id, number, state, conn_id, txn_id, amount, currency,"
                                 " matched_by FROM pp_track"):
                done[(r[0], r[1], r[2])] = r[3]
                if r[5]:
                    known[(r[0], r[1], r[2])] = ({"conn_id": r[4], "txn_id": r[5], "amount": r[6], "currency": r[7]}, r[8])
            fees = {}
            for r in con.execute("SELECT store_id, order_name, gateway, payment_id, amount FROM order_fees"):
                fees.setdefault((r[0], (r[1] or "").lstrip("#")), []).append(
                    {"gateway": r[2] or "", "payment_id": r[3] or "", "auth": "", "amount": r[4] or 0})
            cached = {(r[0], r[1]): (json.loads(r[2]), r[3]) for r in con.execute(
                "SELECT store_id, order_id, payments, checked_at FROM pp_track_orders")}

        # each order's payments: saved order_fees when they already settle it, else Shopify (kept)
        recheck = (datetime.now(timezone.utc) - timedelta(hours=RECHECK_HOURS)).isoformat()
        pays, ask = {}, {}
        for p in parcels:
            key = (p["store_id"], p["order_id"])
            if key in pays or done.get((p["store_id"], p["order_id"], p["number"])) == "sent":
                continue
            fee = fees.get((p["store_id"], (p["order_name"] or "").lstrip("#")))
            if fee and (not any(_is_paypal(f) for f in fee) or _match(fee, by_invoice, by_txn)[0]):
                pays[key] = fee
                continue
            hit = cached.get(key)
            if hit and (_match(hit[0], by_invoice, by_txn)[0] or not any(_is_paypal(x) for x in hit[0])
                        or hit[1] > recheck):
                pays[key] = hit[0]
                continue
            ask.setdefault(p["store_id"], []).append(p["order_id"])
        for sid, ids in ask.items():
            try:
                got = await _shopify_payments(stores[sid], sorted(set(ids)))
            except Exception as e:
                summary["problems"].append(f"{(stores[sid].get('name') or '').strip()}: Shopify said {e}")
                continue
            with db._conn() as con:
                for oid, plist in got.items():
                    pays[(sid, oid)] = plist
                    con.execute("INSERT OR REPLACE INTO pp_track_orders (store_id, order_id, payments, checked_at)"
                                " VALUES (?, ?, ?, ?)", (sid, oid, json.dumps(plist), _now()))

        to_send = []
        with db._conn() as con:
            for p in parcels:
                key = (p["store_id"], p["order_id"], p["number"])
                prev = done.get(key)
                code, other = carrier_of(p["company"], p["carrier_name"])
                base = {"order_name": p["order_name"], "customer": p["customer"], "email": p["email"],
                        "company": p["company"] or p["carrier_name"] or "", "scm_status": p["status"],
                        "fulfilled_at": p["fulfilled_at"]}
                if prev == "sent":
                    con.execute("UPDATE pp_track SET scm_status = ? WHERE store_id = ? AND order_id = ? AND number = ?",
                                (p["status"], *key))
                    continue
                plist = pays.get((p["store_id"], p["order_id"]))
                if plist is None:
                    continue                      # Shopify couldn't be asked this time
                if not any(_is_paypal(x) for x in plist):
                    summary["not_paypal"] += 1
                    if prev:
                        con.execute("DELETE FROM pp_track WHERE store_id = ? AND order_id = ? AND number = ?", key)
                    continue
                hit, how = _match(plist, by_invoice, by_txn)
                if not hit and key in known and (conns.get(known[key][0]["conn_id"]) or {}).get("unread"):
                    hit, how = known[key]         # PayPal couldn't be read this time: keep the earlier match
                moving = p["status"] in allowed and p["status"] not in NEVER
                if p["status"] in NEVER:
                    state = "delivered"           # never sent (an earlier error on it is dropped)
                elif prev in ("error", "failed") and hit:
                    state = prev                  # keeps its tries; retried below
                elif not moving:
                    state = "waiting"
                elif not hit:
                    state = "unmatched"
                else:
                    state = "ready"
                note = None
                if state == "unmatched":
                    note = ("Paid by PayPal, but this payment isn't in a connected PayPal account yet "
                            "(PayPal's list can lag a few hours, or the order went to a PayPal account not connected here).")
                con.execute(
                    "INSERT INTO pp_track (store_id, order_id, number, order_name, customer, email, company, carrier,"
                    " carrier_other, scm_status, fulfilled_at, state, conn_id, txn_id, amount, currency, matched_by,"
                    " note, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(store_id, order_id, number) DO UPDATE SET order_name = excluded.order_name,"
                    " customer = excluded.customer, email = excluded.email, company = excluded.company,"
                    " carrier = excluded.carrier, carrier_other = excluded.carrier_other,"
                    " scm_status = excluded.scm_status, fulfilled_at = excluded.fulfilled_at, state = excluded.state,"
                    " conn_id = excluded.conn_id, txn_id = excluded.txn_id, amount = excluded.amount,"
                    " currency = excluded.currency, matched_by = excluded.matched_by,"
                    " note = COALESCE(excluded.note, pp_track.note), updated_at = excluded.updated_at",
                    (*key, base["order_name"], base["customer"], base["email"], base["company"], code, other,
                     base["scm_status"], base["fulfilled_at"], state, (hit or {}).get("conn_id"),
                     (hit or {}).get("txn_id"), (hit or {}).get("amount"), (hit or {}).get("currency"), how,
                     note, _now()))
                if state in ("waiting", "unmatched", "ready"):
                    summary[state] += 1
                if state in ("ready", "error") and moving and hit:
                    to_send.append(key)

        if not send:
            return _save_last(summary)
        on_stores = set(cfg["stores"])
        with db._conn() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM pp_track WHERE state IN ('ready', 'error') AND tries < ?"
                " ORDER BY fulfilled_at", (MAX_TRIES,))]
        rows = [r for r in rows if (r["store_id"], r["order_id"], r["number"]) in set(to_send)
                and r["store_id"] in on_stores]
        sent_txn = _sent_pairs()
        for r in rows[:MAX_SENDS]:
            if not config()["on"]:                 # switched off while running: stop at once
                summary["problems"].append("Stopped: the app was switched off during this run.")
                break
            if r.get("scm_status") in NEVER:
                continue
            if (r["txn_id"], r["number"]) in sent_txn:
                _mark(r["id"], "sent", note="Already sent on another row", by=by)
                continue
            conn = conns.get(r["conn_id"])
            if not conn:
                continue
            tracker = {"transaction_id": r["txn_id"], "tracking_number": r["number"], "carrier": r["carrier"]}
            if r["carrier"] == "OTHER":
                tracker["carrier_name_other"] = r["carrier_other"] or "Other"
            ok, note = await add_tracker(conn, tracker)
            if not ok and r["carrier"] != "OTHER" and _carrier_refused(note):
                tracker = {**tracker, "carrier": "OTHER", "carrier_name_other": r["company"] or r["carrier"]}
                ok, note = await add_tracker(conn, tracker)
                if ok:
                    note = f"Sent as carrier OTHER ({tracker['carrier_name_other']}): PayPal refused {r['carrier']}"
            if ok:
                _mark(r["id"], "sent", note=note or None, by=by, carrier=tracker["carrier"],
                      other=tracker.get("carrier_name_other", ""))
                sent_txn.add((r["txn_id"], r["number"]))
                summary["sent"] += 1
                summary["ready"] = max(0, summary["ready"] - 1)
            else:
                tries = r["tries"] + 1
                _mark(r["id"], "failed" if tries >= MAX_TRIES else "error", error=note, tries=tries)
                summary["errors"] += 1
        return _save_last(summary)


def _sent_pairs() -> set:
    with db._conn() as con:
        return {(r[0], r[1]) for r in con.execute("SELECT txn_id, number FROM pp_track WHERE state = 'sent'")}


def _mark(row_id: int, state: str, note=None, error=None, tries=None, by=None, carrier=None, other=None):
    sets, vals = ["state = ?", "updated_at = ?"], [state, _now()]
    if state == "sent":
        sets += ["submitted_at = ?", "submitted_by = ?", "error = NULL"]
        vals += [_now(), by or "auto"]
    if note is not None:
        sets.append("note = ?"); vals.append(note)
    if error is not None:
        sets.append("error = ?"); vals.append(error)
    if tries is not None:
        sets.append("tries = ?"); vals.append(tries)
    if carrier:
        sets += ["carrier = ?", "carrier_other = ?"]; vals += [carrier, other or ""]
    with db._conn() as con:
        con.execute(f"UPDATE pp_track SET {', '.join(sets)} WHERE id = ?", (*vals, row_id))


def _save_last(summary: dict) -> dict:
    db.set_setting("pp_track_last", json.dumps(summary))
    return summary


async def loop():
    """Every 15 minutes: rows kept current; sends only while switched on."""
    await asyncio.sleep(180)
    while True:
        try:
            cfg = config()
            await run(send=cfg["on"] and bool(cfg["stores"]), by="auto")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"PayPal tracking run failed: {e}")
        await asyncio.sleep(900)


# ---------------------------------------------------------------- routes (desk "paypaltrack")

def _who(request: Request) -> str:
    u = getattr(request.state, "user", None) or {}
    return u.get("name") or u.get("email") or "?"


def _manage(request: Request):
    if not auth.at_least(getattr(request.state, "user", None) or {}, "paypaltrack", "manage"):
        raise HTTPException(403, "That needs Manage access to PayPal Tracking.")


@router.get("/api/paypal-tracking")
def api_list(state: str = "", store: int = 0, q: str = "", limit: int = 500):
    ensure()
    cfg = config()
    stores = [{"id": s["id"], "name": (s.get("name") or "").strip(), "on": s["id"] in cfg["stores"]}
              for s in db.list_stores() if s.get("access_token") and not demo.is_demo(s)]
    names = {s["id"]: s["name"] for s in stores}
    where, args = [], []
    if state:
        where.append("state IN (" + ",".join("?" * len(state.split(","))) + ")")
        args += state.split(",")
    if store:
        where.append("store_id = ?"); args.append(store)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append("(lower(order_name) LIKE ? OR lower(customer) LIKE ? OR lower(email) LIKE ?"
                     " OR lower(number) LIKE ? OR lower(COALESCE(txn_id, '')) LIKE ?)")
        args += [like] * 5
    with db._conn() as con:
        counts = {r[0]: r[1] for r in con.execute(
            "SELECT state, COUNT(*) FROM pp_track" + (" WHERE store_id = ?" if store else "") + " GROUP BY state",
            (store,) if store else ())}
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM pp_track" + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY COALESCE(submitted_at, updated_at) DESC LIMIT ?", (*args, min(limit, 2000)))]
    labels = {c["id"]: c["label"] for c in db.list_cash_connections() if c["provider"] == "paypal"}
    for r in rows:
        r["store"] = names.get(r["store_id"], "")
        r["paypal_account"] = labels.get(r["conn_id"], "")
        r["state_name"] = STATE_NAME.get(r["state"], r["state"])
    try:
        last = json.loads(db.get_setting("pp_track_last") or "null")
    except ValueError:
        last = None
    return {"config": cfg, "stores": stores, "counts": counts, "rows": rows, "last": last,
            "running": _lock.locked(), "connected": bool(labels), "states": STATE_NAME,
            "rules": {"moving": list(MOVING), "later": list(LATER), "window_days": WINDOW_DAYS, "max_tries": MAX_TRIES}}


@router.put("/api/paypal-tracking/settings")
def api_settings(payload: dict, request: Request):
    _manage(request)
    cfg = config()
    if "on" in payload:
        cfg["on"] = bool(payload["on"])
        cfg["by"], cfg["at"] = _who(request), _now()
    if "stores" in payload:
        cfg["stores"] = sorted({int(s) for s in payload["stores"] or []})
    if "later" in payload:
        cfg["later"] = bool(payload["later"])
    db.set_setting("pp_track", json.dumps(cfg))
    return {"ok": True, "config": cfg}


@router.post("/api/paypal-tracking/run")
async def api_run(payload: dict, request: Request):
    """dry = check everything and show what would be sent, send nothing. Otherwise sends the ready
    parcels of the stores switched on (only while the app is on)."""
    _manage(request)
    if _lock.locked():
        return {"ok": True, "running": True}
    cfg = config()
    dry = bool(payload.get("dry")) or not cfg["on"]
    summary = await run(send=not dry, by=_who(request))
    return {"ok": True, "dry": dry, "summary": summary}


@router.post("/api/paypal-tracking/retry")
def api_retry(payload: dict, request: Request):
    _manage(request)
    ensure()
    with db._conn() as con:
        n = con.execute("UPDATE pp_track SET state = 'error', tries = 0, updated_at = ? WHERE id = ? AND state = 'failed'",
                        (_now(), int(payload.get("id") or 0))).rowcount
    return {"ok": bool(n)}
