"""Tickets: turning the copied emails into customer problems to work on.

Runs after each mailbox read (see app._mail_loop), oldest email first, for
emails dated from the start date on (setting "tickets_from"; earlier ones stay
in All mail only):

    our reply (Sent folder)   joins the ticket it answers (by email headers,
                              else the customer's latest ticket); the ticket is
                              now waiting on the customer
    customer email            the AI (ai_brain.triage) sorts it:
        other                 stays in All mail only
        inquiry / legal       ticket of that kind
        customer              support ticket labelled SCM and/or CS
      A continuation joins its ticket (reopening it if it was solved); a new
      problem or a different order starts a new ticket. "Thanks, got it" closes it.

Team actions (close, reopen, snooze, escalate, relabel, move) live in app.py.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import ai_brain
import db

KIND_OF = {"customer": "support", "inquiry": "inquiry", "legal": "legal"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ticket_for_headers(con, msg) -> int:
    """The ticket a message replies to, from its In-Reply-To / References headers."""
    ids = re.findall(r"<[^>]+>", f"{msg['in_reply_to'] or ''} {msg['refs'] or ''}")
    if not ids:
        return None
    marks = ",".join("?" * len(ids))
    r = con.execute(f"SELECT ticket_id FROM mail_messages WHERE account_id = ? AND ticket_id IS NOT NULL"
                    f" AND message_id IN ({marks}) ORDER BY date DESC LIMIT 1",
                    (msg["account_id"], *ids)).fetchone()
    return r["ticket_id"] if r else None


def _candidates(con, account_id: int, email: str) -> list:
    """The customer's tickets that a new email could continue (open, or solved
    in the last 45 days)."""
    since = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    return [dict(r) for r in con.execute(
        "SELECT id, kind, labels, status, subject, summary, orders FROM tickets"
        " WHERE account_id = ? AND customer_email = ? AND (status = 'open' OR closed_at >= ?)"
        " ORDER BY id DESC LIMIT 8", (account_id, email, since))]


def _attach(con, msg_id: int, ticket_id: int, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE mail_messages SET ticket_id = ?, processed = 1{', ' + sets if sets else ''}"
                f" WHERE id = ?", (ticket_id, *fields.values(), msg_id))


def _merge(a: str, b) -> str:
    items = [x for x in (a or "").split(",") if x] + [x for x in (b or []) if x]
    return ",".join(dict.fromkeys(items))


async def process(api_key: str, limit: int = 150) -> dict:
    """Sort new emails into tickets. Returns counts; raises ai_brain.AIError when
    the AI can't be used (emails are then left for the next run)."""
    start = db.get_setting("tickets_from") or "2026-10-07T16:00:00+00:00"
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        accounts = {a["id"]: dict(a) for a in con.execute("SELECT * FROM mail_accounts")}
        todo = [dict(r) for r in con.execute(
            "SELECT * FROM mail_messages WHERE processed = 0 AND date >= ? ORDER BY date LIMIT ?",
            (start, limit))]
    done = {"replies": 0, "sorted": 0, "tickets": 0, "closed": 0, "other": 0, "tokens_in": 0, "tokens_out": 0}
    for msg in todo:
        acct = accounts.get(msg["account_id"])
        if not acct:
            continue
        own = acct["address"].lower()
        if msg["direction"] == "out":
            with db._conn() as con:
                tid = _ticket_for_headers(con, msg)
                if not tid:
                    to = [a.strip().lower() for a in (msg["to_addr"] or "").split(",") if a.strip()]
                    r = con.execute("SELECT id FROM tickets WHERE account_id = ? AND customer_email IN"
                                    f" ({','.join('?' * len(to)) or 'NULL'}) ORDER BY (status = 'open') DESC,"
                                    " last_message_at DESC LIMIT 1", (msg["account_id"], *to)).fetchone() if to else None
                    tid = r["id"] if r else None
                if tid:
                    _attach(con, msg["id"], tid)
                    con.execute("UPDATE tickets SET waiting = 'customer', last_message_at = ? WHERE id = ?",
                                (msg["date"], tid))
                    done["replies"] += 1
                else:
                    con.execute("UPDATE mail_messages SET processed = 1 WHERE id = ?", (msg["id"],))
            continue
        if (msg["from_addr"] or "").lower() == own:
            with db._conn() as con:
                con.execute("UPDATE mail_messages SET processed = 1, category = 'other' WHERE id = ?", (msg["id"],))
            continue

        with db._conn() as con:
            header_tid = _ticket_for_headers(con, msg)
            cands = _candidates(con, msg["account_id"], (msg["from_addr"] or "").lower())
        msg["reply_to_ticket"] = header_tid
        msg["attachments_list"] = json.loads(msg["attachments"] or "[]")
        ans = await ai_brain.triage(api_key, msg, stores.get(acct["store_id"], ""), cands)
        done["sorted"] += 1
        done["tokens_in"] += ans.get("usage", {}).get("in", 0)
        done["tokens_out"] += ans.get("usage", {}).get("out", 0)
        cat = ans["category"]
        fields = {"category": cat, "labels": ",".join(ans.get("labels") or []), "ai": json.dumps(ans)}
        with db._conn() as con:
            if cat == "other":
                con.execute("UPDATE mail_messages SET processed = 1, category = ?, ai = ? WHERE id = ?",
                            (cat, fields["ai"], msg["id"]))
                done["other"] += 1
                continue
            kind = KIND_OF[cat]
            valid = {c["id"] for c in cands} | ({header_tid} if header_tid else set())
            tid = ans.get("ticket_id") if ans.get("ticket_id") in valid else header_tid
            labels = ans.get("labels") or [] if kind == "support" else []
            if tid:
                t = dict(con.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone())
                status = "closed" if ans.get("resolves") else "open"
                con.execute(
                    "UPDATE tickets SET labels = ?, summary = ?, orders = ?, status = ?, waiting = ?,"
                    " last_message_at = ?, closed_at = ?, closed_by = ?, close_note = ?,"
                    " snoozed_until = CASE WHEN ? = 'open' THEN NULL ELSE snoozed_until END WHERE id = ?",
                    (_merge(t["labels"], labels), ans.get("summary") or t["summary"],
                     _merge(t["orders"], ans.get("orders")), status,
                     "customer" if ans.get("resolves") else "us", msg["date"],
                     _now() if status == "closed" else None,
                     "AI" if status == "closed" else None,
                     "Customer confirmed, no reply needed" if status == "closed" else None,
                     status, tid))
            else:
                if ans.get("resolves"):
                    # A lone "thanks" with nothing to continue: keep it out of the queue.
                    con.execute("UPDATE mail_messages SET processed = 1, category = ?, labels = ?, ai = ?"
                                " WHERE id = ?", (cat, fields["labels"], fields["ai"], msg["id"]))
                    continue
                tid = con.execute(
                    "INSERT INTO tickets (account_id, store_id, kind, labels, customer_email, customer_name,"
                    " subject, summary, orders, status, waiting, last_message_at, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?, 'open', 'us', ?, ?)",
                    (msg["account_id"], acct["store_id"], kind, ",".join(labels),
                     (msg["from_addr"] or "").lower(), msg["from_name"], msg["subject"],
                     ans.get("summary"), ",".join(ans.get("orders") or []), msg["date"], msg["date"])).lastrowid
                done["tickets"] += 1
            if ans.get("resolves") and tid:
                done["closed"] += 1
            _attach(con, msg["id"], tid, **fields)
    return done
