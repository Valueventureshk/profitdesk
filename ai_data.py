"""The assistant's read-only look into the business data (owner only).

When no ready-made tool answers a question ("average shipping time over 60 days", "orders per country"),
the assistant writes one SELECT over the tables below. The database is opened read-only, a SQLite
authorizer allows reading only these tables, and secret columns (tokens, keys, password hashes) read as NULL.
"""
import re
import sqlite3

import db

# table -> what it holds (shown to the AI with the columns)
TABLES = {
    "stores": "One row per store: id, name, currency, timezone (store clock).",
    "store_groups": "Store groups (id, name). store_group_members links store_id to group_id.",
    "store_group_members": "group_id, store_id.",
    "cog_lines": "Every Shopify order line with its product cost. day = order day on the store clock (YYYY-MM-DD); "
                 "subtotal = line value after discounts in store currency; cost_usd = COG in USD; source invoice/catalog/"
                 "estimate/cancelled; cancelled=1 means cancelled; supplier; fulfillment status.",
    "order_fees": "Payment per order: gateway (paypal/airwallex/shopify_payments…), amount (store currency), fee_est, fee_actual, day.",
    "shopify_days": "Saved Shopify totals per store per day (sales, orders) on the store clock.",
    "refund_days": "Saved Shopify refunds per store per day.",
    "google_days": "Saved Google Ads spend per store per day (account currency).",
    "outside_days": "PayPal / Airwallex refunds not in Shopify and chargebacks, per day.",
    "shipments": "SCM desk: one row per parcel (tracking number; number '' = no tracking yet). order_at, fulfilled_at, "
                 "delivered_at, last_event_at are ISO UTC timestamps. status: pending, info_received, in_transit, pickup, "
                 "out_for_delivery, failed_attempt, delivered, exception, expired. transit_days from 17TRACK. "
                 "country/city = destination. company = carrier name from Shopify. cancelled=1 = cancelled order.",
    "scm_notes": "Team notes per order (SCM desk).",
    "scm_flags": "Orders flagged 'needs attention' (SCM desk).",
    "disputes": "Disputes desk: provider paypal/airwallex, state (respond/waiting/review/closed), due, data JSON "
                "(amount, currency, reason, customer, outcome), store_id, order_name.",
    "dispute_log": "Team actions on disputes.",
    "tickets": "Inbox tickets: kind (scm/cs…), status, waiting (1 = waiting on us), escalated, labels, store_id, "
               "customer_email, subject, summary, created_at, closed_at.",
    "mail_messages": "Emails in the support mailboxes (from_addr, to_addr, subject, date, text).",
    "px_events": "Tracking pixel events: name (page_viewed, product_added_to_cart, checkout_started, checkout_completed…), "
                 "at (UTC), value, a_source (attributed source), store_id.",
    "px_visitors": "Pixel visitors: first_at, first_touch (JSON of the first ad click).",
    "gconv_uploads": "Conversions sent to Google (one row per event): store, action, ok/error, at.",
    "invoices": "Supplier invoice files: supplier, store, totals, status, ai_check.",
    "invoice_lines": "Lines read from supplier invoices: order, product, price, matched.",
    "supplier_accounts": "Supplier openings (date + USD owed).",
    "supplier_payees": "Which payee is which supplier.",
    "expense_rules": "Owner's category per payee (Expenses desk).",
    "cash_snapshots": "Cash flow cards saved at 23:59 Hong Kong each day (from 10 Oct 2026).",
    "ads_actions": "Google Ads changes made through EcomVital (with before/after).",
    "ads_rules": "Google Ads rules per store.",
    "co_offers": "Checkout offers (own payment page).",
    "co_orders": "Checkout payments (amount, status, test).",
    "co_subs": "Checkout subscriptions (status, next_at, renewals).",
    "import_items": "Product Importer: products imported into our stores.",
    "size_charts": "Size charts.",
    "gads_tags": "Google desk product tags.",
    "gads_tag_products": "Products per Google desk tag.",
    "change_requests": "Requests to change EcomVital (request_change tool): who, title, details, desk, kind, priority, status "
                       "(new / planned / done / declined), owner_note, created_at.",
    "rec_sessions": "Session recordings per store.",
}
_SECRET = re.compile(r"token|secret|password|hash|api_key|consent_id|method_id|^html$|^fbp$|^client_id$", re.I)
MAX_ROWS = 200


def _authorizer(action, a, b, dbname, trigger):
    if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        if a not in TABLES and not (a or "").startswith("sqlite_"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_IGNORE if b and _SECRET.search(b) else sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_RECURSIVE:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def schema() -> dict:
    con = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=10)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        out = {}
        for t, about in TABLES.items():
            if t not in have:
                continue
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})") if not _SECRET.search(r[1])]
            out[t] = {"about": about, "columns": cols}
        return out
    finally:
        con.close()


def query(sql: str) -> dict:
    sql = (sql or "").strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", sql) or ";" in sql:
        return {"error": "Only one SELECT (or WITH … SELECT) is allowed."}
    con = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=10)
    steps = [0]

    def _stop():   # a few seconds of work at most, then give up
        steps[0] += 1
        return 1 if steps[0] > 30000 else 0
    try:
        con.set_authorizer(_authorizer)
        con.set_progress_handler(_stop, 10000)
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
        more = len(rows) > MAX_ROWS
        return {"columns": cols, "rows": [list(r) for r in rows[:MAX_ROWS]], "row_count": min(len(rows), MAX_ROWS),
                "truncated": more}
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = "The query took too long. Narrow the dates or aggregate more."
        elif "not authorized" in msg or "prohibited" in msg:
            msg = "That table isn't available. Call data_schema for the tables you can read."
        return {"error": msg[:300]}
    finally:
        con.close()
