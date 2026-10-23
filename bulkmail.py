"""Inbox → Bulk email (built 24 Oct 2026): one message to many customers (e.g. "your parcel is delayed"), sent one
by one from the store's own Gmail mailbox, each as its own email. Replies that come back in those email threads land
in the campaign, not in the SCM / CS tickets; a teammate can turn a reply into a ticket when the customer needs help.

Audiences:
  scm_stuck  orders of a store whose parcels were fulfilled N+ days ago and are still not moving (SCM statuses, e.g.
             info_received = label made, not picked up), not delivered
  orders     order numbers pasted by a person
Merge fields in the subject and text: {first_name} {name} {order} {tracking_number} {tracking_link} {store}
"""
import json
import re
from datetime import datetime, timedelta, timezone

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS bulk_campaigns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    account_id INTEGER,                    -- the mailbox it's sent from
    name       TEXT    NOT NULL,
    subject    TEXT    NOT NULL,
    body       TEXT    NOT NULL,
    audience   TEXT,                       -- JSON: how the list was made
    status     TEXT    NOT NULL DEFAULT 'draft',   -- draft | sending | sent | stopped
    created_by TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    sent_by    TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS bulk_recipients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    order_name  TEXT,
    email       TEXT    NOT NULL,
    name        TEXT,
    tracking    TEXT,
    tracking_url TEXT,
    status      TEXT    NOT NULL DEFAULT 'queued',  -- queued | sent | failed | skipped
    error       TEXT,
    message_id  TEXT,                            -- our email's Message-ID: replies point at it
    gmail_thread TEXT,
    sent_at     TEXT,
    replies     INTEGER NOT NULL DEFAULT 0,
    last_reply_at TEXT,
    read_at     TEXT,                            -- a teammate has looked at the latest reply
    ticket_id   INTEGER                          -- a ticket made from the reply
);
CREATE INDEX IF NOT EXISTS bulk_recipients_campaign ON bulk_recipients (campaign_id);
CREATE INDEX IF NOT EXISTS bulk_recipients_msg ON bulk_recipients (message_id);
"""
STUCK = ("info_received", "pending", "in_transit", "exception", "expired", "undelivered")
FIELDS = ("first_name", "name", "order", "tracking_number", "tracking_link", "store")
MAX_RECIPIENTS = 2000


def init():
    with db._conn() as con:
        con.executescript(SCHEMA)
        have = {r[1] for r in con.execute("PRAGMA table_info(mail_messages)")}
        if "bulk_recipient_id" not in have:
            con.execute("ALTER TABLE mail_messages ADD COLUMN bulk_recipient_id INTEGER")


def audience(store_id: int, kind: str, statuses=None, min_days: int = 7, max_days: int = 45, orders=None,
             skip_emailed: bool = True) -> list:
    """[{order_name, email, name, tracking, tracking_url, status, fulfilled_at}] one per order (newest parcel)."""
    init()
    with db._conn() as con:
        if kind == "orders":
            names = []
            for o in orders or []:
                o = str(o).strip()
                if o:
                    names.append(o if o.startswith("#") else "#" + o)
            if not names:
                return []
            rows = [dict(r) for r in con.execute(
                f"SELECT order_name, email, customer, number, tracking_url, status, fulfilled_at, order_at FROM shipments"
                f" WHERE store_id = ? AND order_name IN ({','.join('?' * len(names))})", (store_id, *names))]
        else:
            sts = [s for s in (statuses or ["info_received"]) if s in STUCK] or ["info_received"]
            now = datetime.now(timezone.utc)
            lo = (now - timedelta(days=max(int(max_days), int(min_days)))).isoformat()
            hi = (now - timedelta(days=int(min_days))).isoformat()
            rows = [dict(r) for r in con.execute(
                f"SELECT order_name, email, customer, number, tracking_url, status, fulfilled_at, order_at FROM shipments"
                f" WHERE store_id = ? AND status IN ({','.join('?' * len(sts))}) AND fulfilled_at IS NOT NULL"
                f" AND fulfilled_at != '' AND fulfilled_at >= ? AND fulfilled_at < ?", (store_id, *sts, lo, hi))]
            # an order with a parcel already delivered isn't stuck
            done = {r[0] for r in con.execute("SELECT DISTINCT order_name FROM shipments WHERE store_id = ? AND status = 'delivered'",
                                              (store_id,))}
            rows = [r for r in rows if r["order_name"] not in done]
        emailed = set()
        if skip_emailed:
            emailed = {r[0] for r in con.execute(
                "SELECT DISTINCT r.order_name FROM bulk_recipients r JOIN bulk_campaigns c ON c.id = r.campaign_id"
                " WHERE c.store_id = ? AND r.status = 'sent' AND r.sent_at >= ?",
                (store_id, (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()))}
    by = {}
    for r in sorted(rows, key=lambda r: r.get("fulfilled_at") or ""):
        if r["order_name"] in emailed or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (r.get("email") or "").strip()):
            continue
        by[r["order_name"]] = {"order_name": r["order_name"], "email": r["email"].strip().lower(),
                               "name": (r.get("customer") or "").strip(), "tracking": r.get("number") or "",
                               "tracking_url": r.get("tracking_url") or "", "status": r.get("status"),
                               "fulfilled_at": r.get("fulfilled_at")}
    return list(by.values())[:MAX_RECIPIENTS]


def render(text: str, r: dict, store: str) -> str:
    first = (r.get("name") or "").split(" ")[0] or "there"
    vals = {"first_name": first, "name": r.get("name") or "there", "order": r.get("order_name") or "",
            "tracking_number": r.get("tracking") or "", "tracking_link": r.get("tracking_url") or "",
            "store": store}
    return re.sub(r"\{(\w+)\}", lambda m: str(vals.get(m.group(1), m.group(0))), text or "")


def unknown_fields(*texts) -> list:
    return sorted({f for t in texts for f in re.findall(r"\{(\w+)\}", t or "") if f not in FIELDS})


def recipient_for_reply(con, msg: dict):
    """The campaign recipient an incoming email answers (its In-Reply-To / References carry our Message-ID)."""
    ids = re.findall(r"<[^>]+>", f"{msg.get('in_reply_to') or ''} {msg.get('refs') or ''}")
    if not ids:
        return None
    try:
        r = con.execute(f"SELECT id FROM bulk_recipients WHERE message_id IN ({','.join('?' * len(ids))}) LIMIT 1",
                        ids).fetchone()
    except Exception:               # tables not made yet
        return None
    return r[0] if r else None


def summary(con, cid: int) -> dict:
    c = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM bulk_recipients WHERE campaign_id = ? GROUP BY status", (cid,))}
    rep = con.execute("SELECT COUNT(*), SUM(CASE WHEN replies > 0 AND (read_at IS NULL OR read_at < last_reply_at) THEN 1 ELSE 0 END),"
                      " SUM(CASE WHEN ticket_id IS NOT NULL THEN 1 ELSE 0 END) FROM bulk_recipients WHERE campaign_id = ? AND replies > 0",
                      (cid,)).fetchone()
    return {"queued": c.get("queued", 0), "sent": c.get("sent", 0), "failed": c.get("failed", 0),
            "skipped": c.get("skipped", 0), "total": sum(c.values()), "replied": rep[0] or 0, "unread": rep[1] or 0,
            "tickets": rep[2] or 0}
