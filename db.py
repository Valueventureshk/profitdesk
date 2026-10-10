"""SQLite storage.

Only these things are stored: connected stores (with their Shopify token and
cost percentage), the linked Google Ads and Meta Ads account per store, one
Google sign-in shared across all stores, and one Meta access token per Meta
business (each store's ad account is read through the token that can see it).

Spend figures are fetched live every time the dashboard loads. Shopify sales
are fetched live too, but each day's totals are also saved (shopify_days),
because Shopify only shares the last 60 days of orders: saved days are how
history older than that stays available.
"""
import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(__file__), "profitdesk.db")
# On Railway the data must live on the attached volume, or every deploy starts
# from an empty database. Use it whenever DB_PATH doesn't already point there.
_VOLUME = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
if _VOLUME and not os.path.abspath(DB_PATH).startswith(_VOLUME.rstrip("/") + "/"):
    DB_PATH = os.path.join(_VOLUME, "profitdesk.db")
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)  # e.g. a fresh /data volume

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    shop_domain         TEXT    NOT NULL UNIQUE,
    access_token        TEXT    NOT NULL,
    currency            TEXT    NOT NULL DEFAULT 'USD',
    timezone            TEXT    NOT NULL DEFAULT 'UTC',
    cost_pct            REAL    NOT NULL DEFAULT 0,
    google_customer_id  TEXT    UNIQUE,
    google_login_cid    TEXT,
    google_account_name TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One Shopify app per store. A custom-distribution app can only ever be
-- installed on one store, so each store brings its own app keys. Stores
-- without a row here fall back to the app in .env.
CREATE TABLE IF NOT EXISTS shopify_apps (
    shop_domain   TEXT PRIMARY KEY,
    client_id     TEXT NOT NULL,
    client_secret TEXT NOT NULL
);

-- One row per Meta business connected, each with its own System User token.
CREATE TABLE IF NOT EXISTS meta_connections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_user_id TEXT UNIQUE,
    access_token TEXT NOT NULL,
    name         TEXT,
    label        TEXT
);

-- Before meta_connections there was a single token. Kept only so init() can
-- move an old one across.
CREATE TABLE IF NOT EXISTS meta_auth (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT NOT NULL,
    name         TEXT
);

-- Named sets of stores ("Meta stores", "Google stores"...) that can be viewed
-- as one blended dashboard, like All stores but only for the members.
CREATE TABLE IF NOT EXISTS store_groups (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS store_group_members (
    group_id INTEGER NOT NULL,
    store_id INTEGER NOT NULL,
    PRIMARY KEY (group_id, store_id)
);

-- People who can log in. Only a salted scrypt hash of each password is kept.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    name          TEXT,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Logged-in browsers. Only a SHA-256 of each session token is kept.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    expires_at REAL    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Money accounts for the Cash flow page (Airwallex, PayPal).
-- Keys are read-only API credentials created in each provider.
CREATE TABLE IF NOT EXISTS cash_connections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    provider   TEXT    NOT NULL,
    label      TEXT    NOT NULL,
    client_id  TEXT    NOT NULL,
    secret     TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Each store's Shopify sales per day (on the store's own clock), saved as they
-- are read, so days older than Shopify's 60-day window can still be shown.
CREATE TABLE IF NOT EXISTS shopify_days (
    store_id   INTEGER NOT NULL,
    date       TEXT    NOT NULL,
    sales      REAL    NOT NULL,
    orders     INTEGER NOT NULL,
    payments   TEXT    NOT NULL DEFAULT '{}',   -- {gateway: [orders, amount]}
    timezone   TEXT,
    saved_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, date)
);

-- Every Shopify order line with what it cost from the supplier (see cog.py).
-- Kept, like shopify_days, so costs stay known after Shopify's 60-day window.
CREATE TABLE IF NOT EXISTS cog_lines (
    store_id   INTEGER NOT NULL,
    order_name TEXT    NOT NULL,
    line_no    INTEGER NOT NULL,
    day        TEXT    NOT NULL,      -- store's own clock
    created    TEXT    NOT NULL,
    cancelled  INTEGER NOT NULL DEFAULT 0,
    fulfillment TEXT,
    product    TEXT    NOT NULL,
    sku        TEXT,
    variant_id TEXT,
    quantity   INTEGER NOT NULL,
    subtotal   REAL    NOT NULL,      -- what the customer paid, store currency
    cost_usd   REAL,                  -- supplier cost incl. shipping, USD
    source     TEXT,                  -- invoice | catalog | estimate | cancelled
    supplier   TEXT,
    note       TEXT,
    PRIMARY KEY (store_id, order_name, line_no)
);
CREATE INDEX IF NOT EXISTS cog_lines_day ON cog_lines (store_id, day);

-- Each order's payment and the provider's fee on it: the actual fee from
-- PayPal / Airwallex once they've posted it, otherwise the rate-card estimate.
CREATE TABLE IF NOT EXISTS order_fees (
    store_id   INTEGER NOT NULL,
    order_name TEXT    NOT NULL,
    payment_id TEXT    NOT NULL,
    day        TEXT    NOT NULL,
    created    TEXT    NOT NULL,
    gateway    TEXT,
    amount     REAL    NOT NULL,      -- store currency
    fee_est    REAL,                  -- store currency
    fee_actual REAL,                  -- store currency, NULL until the provider posts it
    PRIMARY KEY (store_id, order_name, payment_id)
);
CREATE INDEX IF NOT EXISTS order_fees_day ON order_fees (store_id, day);

-- Supplier invoices and their lines (see invoices.py). Invoice lines are the
-- real COG of the order items they name.
CREATE TABLE IF NOT EXISTS invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier    TEXT,
    number      TEXT,
    date        TEXT,
    store_id    INTEGER,
    total       REAL,
    filename    TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    missing     TEXT NOT NULL DEFAULT '[]'      -- fulfilled orders in range but not invoiced
);
CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id INTEGER NOT NULL,
    line_no    INTEGER NOT NULL,
    order_key  TEXT    NOT NULL,
    order_name TEXT,
    store_id   INTEGER,
    title      TEXT,
    product    TEXT,                 -- the Shopify product line it matched
    amount     REAL    NOT NULL,     -- USD
    status     TEXT    NOT NULL,     -- ok | changed | new | duplicate | unmatched
    previous   REAL,
    note       TEXT,
    approved_by TEXT,
    approved_at TEXT,
    PRIMARY KEY (invoice_id, line_no)
);

-- Store support mailboxes (read over IMAP with an app password) and the emails
-- copied from them. Nothing in the mailbox itself is changed.
CREATE TABLE IF NOT EXISTS mail_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id     INTEGER,
    address      TEXT    NOT NULL UNIQUE,
    password     TEXT    NOT NULL,
    provider     TEXT    NOT NULL DEFAULT 'google',
    last_uid     INTEGER NOT NULL DEFAULT 0,
    last_checked TEXT,
    last_error   TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mail_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    message_id  TEXT,
    in_reply_to TEXT,
    refs        TEXT,
    from_name   TEXT,
    from_addr   TEXT,
    to_addr     TEXT,
    subject     TEXT,
    date        TEXT,
    text        TEXT,
    html        TEXT,
    attachments TEXT NOT NULL DEFAULT '[]',
    UNIQUE (account_id, uid)
);
CREATE INDEX IF NOT EXISTS mail_messages_date ON mail_messages (date);

-- Tickets: one customer problem, made of the emails (in and out) about it.
-- kind: support (SCM/CS labels) | inquiry | legal. Solved ones stay as records.
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL,
    store_id        INTEGER,
    kind            TEXT    NOT NULL DEFAULT 'support',
    labels          TEXT    NOT NULL DEFAULT '',
    customer_email  TEXT,
    customer_name   TEXT,
    subject         TEXT,
    summary         TEXT,
    orders          TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'open',      -- open | closed
    waiting         TEXT    NOT NULL DEFAULT 'us',        -- us | customer
    snoozed_until   TEXT,
    escalated       INTEGER NOT NULL DEFAULT 0,
    last_message_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at       TEXT,
    closed_by       TEXT,
    close_note      TEXT
);
CREATE INDEX IF NOT EXISTS tickets_customer ON tickets (account_id, customer_email);

-- Teaching the AI (see ai_coach.py): the owner's SOPs and the AI's questions.
CREATE TABLE IF NOT EXISTS ai_sops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ai_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    answer      TEXT,
    answered_by TEXT,
    answered_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Reports desk: downloadable CSV files (see reports.py).
CREATE TABLE IF NOT EXISTS reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    rows       INTEGER NOT NULL DEFAULT 0,
    content    TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The Cash flow page's top cards as they stood at 23:59 each day (Hong Kong clock),
-- so days can be compared later. data = JSON of the figures, in `currency`.
CREATE TABLE IF NOT EXISTS cash_snapshots (
    day        TEXT PRIMARY KEY,
    taken_at   TEXT NOT NULL,
    currency   TEXT NOT NULL,
    data       TEXT NOT NULL
);

-- Expenses desk: the owner's category picks. key = a payee ("payee:pgs tech
-- limited", "merchant:canva") or one transaction ("tx:<id>"). Suggestions are
-- the AI's guesses for payees nobody has picked yet.
CREATE TABLE IF NOT EXISTS expense_rules (
    key        TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    name       TEXT,
    set_by     TEXT,
    set_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS expense_suggestions (
    key        TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- SCM desk: one row per parcel (tracking number) of every order, plus one row
-- with number '' for orders still waiting for tracking. Tracking fields come
-- from 17TRACK (track17.py); order fields from Shopify.
CREATE TABLE IF NOT EXISTS shipments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id      INTEGER NOT NULL,
    order_id      TEXT NOT NULL,
    order_name    TEXT,
    order_at      TEXT,
    email         TEXT,
    customer      TEXT,
    city          TEXT,
    country       TEXT,
    items         INTEGER,
    cancelled     INTEGER NOT NULL DEFAULT 0,
    fulfillment   TEXT,
    number        TEXT NOT NULL DEFAULT '',
    company       TEXT,
    tracking_url  TEXT,
    fulfilled_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    sub_status    TEXT,
    last_event    TEXT,
    last_event_at TEXT,
    last_location TEXT,
    carrier_name  TEXT,
    last_mile     TEXT,
    last_mile_number TEXT,
    origin        TEXT,
    destination   TEXT,
    transit_days  REAL,
    eta_from      TEXT,
    eta_to        TEXT,
    delivered_at  TEXT,
    events        TEXT,
    registered    INTEGER NOT NULL DEFAULT 0,
    register_error TEXT,
    tracked_at    TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (store_id, order_id, number)
);
CREATE INDEX IF NOT EXISTS shipments_number ON shipments(number);

-- SCM desk: the team's notes on an order (special events, supplier replies...)
-- and a "needs attention" flag. Kept per order, so they survive parcel changes.
CREATE TABLE IF NOT EXISTS scm_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    order_id   TEXT NOT NULL,
    text       TEXT NOT NULL,
    author     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS scm_notes_order ON scm_notes(store_id, order_id);
CREATE TABLE IF NOT EXISTS scm_flags (
    store_id   INTEGER NOT NULL,
    order_id   TEXT NOT NULL,
    flagged_by TEXT,
    flagged_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, order_id)
);
CREATE INDEX IF NOT EXISTS shipments_order_at ON shipments(order_at);

-- Small app-wide preferences, e.g. the currency the dashboard shows.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS google_auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    refresh_token TEXT NOT NULL,
    email         TEXT
);
"""


class AlreadyLinked(RuntimeError):
    """Raised when an ad account is already attached to a different store."""


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# Columns added after the first release. Older databases get them on startup.
_ADDED_COLUMNS = {
    "shipments": [("province", "TEXT"), ("fulfillment_gid", "TEXT"), ("pushed", "TEXT NOT NULL DEFAULT ''"),
                  ("push_error", "TEXT")],
    "stores": [
        ("meta_account_id", "TEXT UNIQUE"),
        ("meta_account_name", "TEXT"),
        ("meta_currency", "TEXT"),
        ("meta_connection_id", "INTEGER"),
        ("meta_timezone", "TEXT"),
        ("google_currency", "TEXT"),
        ("shopify_plan", "TEXT"),
        ("google_source", "TEXT"),      # "sheet" when spend comes from the script's Sheet
        ("google_timezone", "TEXT"),
        ("google_tax_pct", "REAL"),     # tax Google adds when it charges (e.g. 10% GST)
    ],
    "cash_connections": [
        ("account_id", "TEXT"),          # Airwallex sub-account (x-login-as), if any
    ],
    "mail_accounts": [
        ("last_uid_sent", "INTEGER NOT NULL DEFAULT 0"),   # Sent folder, read for our replies
    ],
    "mail_messages": [
        ("direction", "TEXT NOT NULL DEFAULT 'in'"),       # in = from a customer, out = our reply
        ("category", "TEXT"),        # customer | inquiry | legal | other (AI); NULL = not sorted
        ("labels", "TEXT"),          # scm,cs
        ("ticket_id", "INTEGER"),
        ("ai", "TEXT"),              # the AI's full answer, for reference
        ("processed", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "users": [
        ("role", "TEXT NOT NULL DEFAULT 'owner'"),   # owner | write | read
        ("desks", "TEXT NOT NULL DEFAULT 'profit,cash,cog'"),
        ("must_change", "INTEGER NOT NULL DEFAULT 0"),  # temporary password: change at first login
    ],
}


# ---------------------------------------------------------------- backup & restore

REQUIRED_TABLES = {"stores", "users", "settings"}


def backup_bytes() -> bytes:
    """A consistent copy of the whole database, safe to take while running."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "backup.db")
        src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(path, "rb") as f:
            return f.read()


def restore_bytes(data: bytes):
    """Replace the database with a backup, after checking it's a ProfitDesk one."""
    import tempfile
    if not data.startswith(b"SQLite format 3\x00"):
        raise ValueError("That file isn't a ProfitDesk backup.")
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".restore")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        con = sqlite3.connect(tmp)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError("That file isn't a ProfitDesk backup (missing "
                             + ", ".join(sorted(missing)) + ").")
        os.replace(tmp, DB_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    init()  # bring an older backup's tables up to date


def init():
    with _conn() as con:
        con.executescript(SCHEMA)
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    # SQLite can't add a UNIQUE column, so the index carries it.
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl.replace(' UNIQUE', '')}")
                    if "UNIQUE" in decl:
                        con.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_{name}_uniq"
                                    f" ON {table}({name})")

        # Move a single-token Meta setup over to connections, keeping its links.
        old = con.execute("SELECT * FROM meta_auth WHERE id = 1").fetchone()
        if old:
            cur = con.execute(
                "INSERT INTO meta_connections (access_token, name, label) VALUES (?, ?, ?)",
                (old["access_token"], old["name"], old["name"]),
            )
            con.execute(
                "UPDATE stores SET meta_connection_id = ? WHERE meta_account_id IS NOT NULL",
                (cur.lastrowid,),
            )
            con.execute("DELETE FROM meta_auth")


# ---------------------------------------------------------------- stores

def list_stores():
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM stores ORDER BY sort_order, id"
        )]


def get_store(store_id: int):
    with _conn() as con:
        row = con.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
    return dict(row) if row else None


def upsert_store(name: str, shop_domain: str, access_token: str, currency: str,
                 timezone: str = "UTC") -> int:
    """Connect a store, or refresh the token if it is already connected."""
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM stores WHERE shop_domain = ?", (shop_domain,)
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE stores SET access_token = ?, name = ?, currency = ?,"
                " timezone = ? WHERE id = ?",
                (access_token, name, currency, timezone, existing["id"]),
            )
            return existing["id"]
        cur = con.execute(
            "INSERT INTO stores (name, shop_domain, access_token, currency, timezone, sort_order)"
            " VALUES (?, ?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM stores))",
            (name, shop_domain, access_token, currency, timezone),
        )
        return cur.lastrowid


def save_shopify_days(store_id: int, days: dict, tz: str):
    """days: {date: {"sales", "orders", "payments"}}; replaces what was saved."""
    import json
    with _conn() as con:
        con.executemany(
            "INSERT INTO shopify_days (store_id, date, sales, orders, payments, timezone, saved_at)"
            " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))"
            " ON CONFLICT(store_id, date) DO UPDATE SET sales = excluded.sales,"
            " orders = excluded.orders, payments = excluded.payments,"
            " timezone = excluded.timezone, saved_at = excluded.saved_at",
            [(store_id, d, float(v["sales"]), int(v["orders"]), json.dumps(v.get("payments") or {}), tz)
             for d, v in days.items()])


def saved_shopify_days(store_id: int, start: str, end: str) -> dict:
    import json
    with _conn() as con:
        rows = con.execute("SELECT * FROM shopify_days WHERE store_id = ? AND date BETWEEN ? AND ?",
                           (store_id, start, end)).fetchall()
    return {r["date"]: {"sales": r["sales"], "orders": r["orders"],
                        "payments": json.loads(r["payments"] or "{}")} for r in rows}


def shopify_history():
    """{store_id: (oldest saved day, newest, days saved)}"""
    with _conn() as con:
        rows = con.execute("SELECT store_id, MIN(date) a, MAX(date) b, COUNT(*) n"
                           " FROM shopify_days GROUP BY store_id").fetchall()
    return {r["store_id"]: (r["a"], r["b"], r["n"]) for r in rows}


def first_saved_shopify_day(store_id: int):
    with _conn() as con:
        r = con.execute("SELECT MIN(date) AS d FROM shopify_days WHERE store_id = ?",
                        (store_id,)).fetchone()
    return r["d"] if r else None


def replace_cog_lines(store_id: int, start: str, end: str, rows: list):
    """Replace a store's costed order lines for days start..end (store clock)."""
    with _conn() as con:
        con.execute("DELETE FROM cog_lines WHERE store_id = ? AND day BETWEEN ? AND ?",
                    (store_id, start, end))
        con.executemany(
            "INSERT OR REPLACE INTO cog_lines (store_id, order_name, line_no, day, created, cancelled,"
            " fulfillment, product, sku, variant_id, quantity, subtotal, cost_usd, source, supplier, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(store_id, r["order"], r["line_no"], r["day"], r["created"], int(r["cancelled"]),
              r.get("fulfillment"), r["product"], r.get("sku"), r.get("variant_id"), r["quantity"],
              r["subtotal"], r.get("cost"), r.get("source"), r.get("supplier"), r.get("note"))
             for r in rows])


def replace_order_fees(store_id: int, start: str, end: str, rows: list):
    with _conn() as con:
        con.execute("DELETE FROM order_fees WHERE store_id = ? AND day BETWEEN ? AND ?",
                    (store_id, start, end))
        con.executemany(
            "INSERT OR REPLACE INTO order_fees (store_id, order_name, payment_id, day, created, gateway,"
            " amount, fee_est, fee_actual) VALUES (?,?,?,?,?,?,?,?,?)",
            [(store_id, r["order"], r["payment_id"] or f"{r['order']}#{n}", r["day"], r["created"],
              r["gateway"], r["amount"], r["fee_est"], r["fee_actual"]) for n, r in enumerate(rows)])


def order_fees(store_id: int, start: str, end: str) -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM order_fees WHERE store_id = ? AND day BETWEEN ? AND ?",
            (store_id, start, end))]


def cog_lines(store_ids: list, start: str, end: str) -> list:
    if not store_ids:
        return []
    marks = ",".join("?" * len(store_ids))
    with _conn() as con:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM cog_lines WHERE store_id IN ({marks}) AND day BETWEEN ? AND ?"
            " ORDER BY created, order_name, line_no", (*store_ids, start, end))]


def cog_days(store_id: int, start: str, end: str) -> dict:
    """{day: {"cost_usd", "estimated_usd", "subtotal", "lines"}} for one store."""
    with _conn() as con:
        rows = con.execute(
            "SELECT day, SUM(COALESCE(cost_usd, 0)) c,"
            " SUM(CASE WHEN source = 'estimate' THEN COALESCE(cost_usd, 0) ELSE 0 END) e,"
            " SUM(CASE WHEN cancelled = 0 THEN subtotal ELSE 0 END) s, COUNT(*) n"
            " FROM cog_lines WHERE store_id = ? AND day BETWEEN ? AND ? GROUP BY day",
            (store_id, start, end)).fetchall()
    return {r["day"]: {"cost_usd": r["c"], "estimated_usd": r["e"], "subtotal": r["s"],
                       "lines": r["n"]} for r in rows}


def update_cog_costs(store_id: int, rows: list):
    """rows: [{order_name, line_no, cost, source, supplier, note}]"""
    with _conn() as con:
        con.executemany(
            "UPDATE cog_lines SET cost_usd = ?, source = ?, supplier = ?, note = ?"
            " WHERE store_id = ? AND order_name = ? AND line_no = ?",
            [(r["cost"], r["source"], r["supplier"], r["note"], store_id, r["order_name"], r["line_no"])
             for r in rows])


def store_order_names() -> dict:
    """{store_id: [recent order names]} from saved order lines (to learn order tags)."""
    with _conn() as con:
        rows = con.execute("SELECT store_id, order_name FROM cog_lines"
                           " GROUP BY store_id, order_name ORDER BY MAX(created) DESC").fetchall()
    out = {}
    for r in rows:
        if len(out.setdefault(r["store_id"], [])) < 200:
            out[r["store_id"]].append(r["order_name"])
    return out


def save_invoice(inv: dict, checked: dict, filename: str, user: str) -> int:
    import json
    with _conn() as con:
        iid = con.execute(
            "INSERT INTO invoices (supplier, number, date, store_id, total, filename, uploaded_by, missing)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (inv["supplier"], inv["number"], inv["date"], checked["store_id"], inv["total"],
             filename, user, json.dumps(checked["missing"]))).lastrowid
        con.executemany(
            "INSERT INTO invoice_lines (invoice_id, line_no, order_key, order_name, store_id, title,"
            " product, amount, status, previous, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(iid, l["line_no"], l["order"], l.get("order_name"), l["store_id"], l["title"],
              l["product"], l["amount"], l["status"], l["previous"], l["note"])
             for l in checked["lines"]])
    return iid


def list_invoices() -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT i.*, COUNT(l.line_no) lines,"
            " SUM(CASE WHEN l.status = 'changed' AND l.approved_at IS NULL THEN 1 ELSE 0 END) to_check,"
            " SUM(CASE WHEN l.status IN ('unmatched', 'duplicate') THEN 1 ELSE 0 END) problems"
            " FROM invoices i LEFT JOIN invoice_lines l ON l.invoice_id = i.id"
            " GROUP BY i.id ORDER BY i.date DESC, i.id DESC")]


def invoice_lines(invoice_id: int = None) -> list:
    with _conn() as con:
        if invoice_id is None:
            return [dict(r) for r in con.execute(
                "SELECT l.*, i.number, i.date, i.supplier FROM invoice_lines l"
                " JOIN invoices i ON i.id = l.invoice_id")]
        return [dict(r) for r in con.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY line_no", (invoice_id,))]


def get_invoice(invoice_id: int):
    with _conn() as con:
        r = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    return dict(r) if r else None


def approve_invoice_line(invoice_id: int, line_no: int, user: str):
    with _conn() as con:
        con.execute("UPDATE invoice_lines SET approved_by = ?, approved_at = datetime('now')"
                    " WHERE invoice_id = ? AND line_no = ?", (user, invoice_id, line_no))


def delete_invoice(invoice_id: int):
    with _conn() as con:
        con.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (invoice_id,))
        con.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))


def list_mail_accounts() -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM mail_accounts ORDER BY id")]


def add_mail_account(store_id, address: str, password: str, provider: str = "google") -> int:
    with _conn() as con:
        existing = con.execute("SELECT id FROM mail_accounts WHERE address = ?", (address,)).fetchone()
        if existing:
            con.execute("UPDATE mail_accounts SET password = ?, store_id = ?, last_error = NULL WHERE id = ?",
                        (password, store_id, existing["id"]))
            return existing["id"]
        return con.execute("INSERT INTO mail_accounts (store_id, address, password, provider)"
                           " VALUES (?, ?, ?, ?)", (store_id, address, password, provider)).lastrowid


def delete_mail_account(account_id: int):
    with _conn() as con:
        con.execute("DELETE FROM mail_messages WHERE account_id = ?", (account_id,))
        con.execute("DELETE FROM mail_accounts WHERE id = ?", (account_id,))


def mail_checked(account_id: int, last_uid: int = None, error: str = None, last_uid_sent: int = None):
    with _conn() as con:
        con.execute("UPDATE mail_accounts SET last_checked = datetime('now'), last_error = ?,"
                    " last_uid = COALESCE(?, last_uid), last_uid_sent = COALESCE(?, last_uid_sent)"
                    " WHERE id = ?", (error, last_uid, last_uid_sent, account_id))


def save_mail(account_id: int, messages: list, direction: str = "in"):
    """Sent-folder messages are stored with a negative uid (IMAP numbers each folder
    separately, and uid is unique per mailbox here)."""
    import json
    sign = -1 if direction == "out" else 1
    with _conn() as con:
        con.executemany(
            "INSERT OR IGNORE INTO mail_messages (account_id, uid, message_id, in_reply_to, refs,"
            " from_name, from_addr, to_addr, subject, date, text, html, attachments, direction)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(account_id, sign * m["uid"], m["message_id"], m["in_reply_to"], m["references"],
              m["from_name"], m["from_addr"], m["to_addr"], m["subject"], m["date"],
              m["text"], m["html"], json.dumps(m["attachments"]), direction) for m in messages])


def mail_list(account_ids: list = None, limit: int = 200, category: str = None) -> list:
    with _conn() as con:
        q = ("SELECT m.id, m.account_id, m.from_name, m.from_addr, m.subject, m.date,"
             " substr(m.text, 1, 160) snippet, m.attachments, m.category, m.labels, m.ticket_id,"
             " a.address, a.store_id"
             " FROM mail_messages m JOIN mail_accounts a ON a.id = m.account_id WHERE m.direction = 'in'")
        args = []
        if account_ids:
            q += f" AND m.account_id IN ({','.join('?' * len(account_ids))})"
            args = list(account_ids)
        if category:
            cats = category.split(",")
            q += f" AND m.category IN ({','.join('?' * len(cats))})"
            args += cats
        q += " ORDER BY m.date DESC LIMIT ?"
        return [dict(r) for r in con.execute(q, (*args, limit))]


def mail_get(message_id: int):
    with _conn() as con:
        r = con.execute("SELECT m.*, a.address, a.store_id FROM mail_messages m"
                        " JOIN mail_accounts a ON a.id = m.account_id WHERE m.id = ?",
                        (message_id,)).fetchone()
    return dict(r) if r else None


def delete_store(store_id: int):
    with _conn() as con:
        con.execute("DELETE FROM stores WHERE id = ?", (store_id,))
        con.execute("DELETE FROM shopify_days WHERE store_id = ?", (store_id,))
        con.execute("DELETE FROM cog_lines WHERE store_id = ?", (store_id,))
        con.execute("DELETE FROM order_fees WHERE store_id = ?", (store_id,))
        con.execute("DELETE FROM store_group_members WHERE store_id = ?", (store_id,))


def rename_store(store_id: int, name: str):
    with _conn() as con:
        con.execute("UPDATE stores SET name = ? WHERE id = ?", (name, store_id))


def set_google_tax(store_id: int, pct: float):
    pct = max(0.0, min(50.0, float(pct or 0)))
    with _conn() as con:
        con.execute("UPDATE stores SET google_tax_pct = ? WHERE id = ?", (pct, store_id))


def set_cost_pct(store_id: int, pct: float):
    pct = max(0.0, min(100.0, float(pct)))
    with _conn() as con:
        con.execute("UPDATE stores SET cost_pct = ? WHERE id = ?", (pct, store_id))


# ---------------------------------------------------------------- google link

def link_google(store_id: int, customer_id: str, login_cid, name: str, currency: str = None,
                timezone: str = None, source: str = None):
    """Attach one ad account to one store, and only one."""
    with _conn() as con:
        clash = con.execute(
            "SELECT name FROM stores WHERE google_customer_id = ? AND id != ?",
            (customer_id, store_id),
        ).fetchone()
        if clash:
            raise AlreadyLinked(
                f"That ad account is already connected to {clash['name']}. "
                "One ad account can only feed one store."
            )
        con.execute(
            "UPDATE stores SET google_customer_id = ?, google_login_cid = ?,"
            " google_account_name = ?, google_currency = ?, google_timezone = ?,"
            " google_source = ? WHERE id = ?",
            (customer_id, login_cid, name, currency or None, timezone or None,
             source or None, store_id),
        )


def unlink_google(store_id: int):
    with _conn() as con:
        con.execute(
            "UPDATE stores SET google_customer_id = NULL, google_login_cid = NULL,"
            " google_account_name = NULL WHERE id = ?",
            (store_id,),
        )


def linked_customer_ids():
    """customer_id -> store name, so the dropdown can grey out taken accounts."""
    with _conn() as con:
        return {
            r["google_customer_id"]: r["name"]
            for r in con.execute(
                "SELECT google_customer_id, name FROM stores"
                " WHERE google_customer_id IS NOT NULL"
            )
        }


# ---------------------------------------------------------------- store groups

def list_groups():
    """[{id, name, store_ids}] in the order they were made."""
    with _conn() as con:
        groups = [dict(r) for r in con.execute("SELECT id, name FROM store_groups ORDER BY id")]
        members = con.execute(
            "SELECT m.group_id, m.store_id FROM store_group_members m"
            " JOIN stores s ON s.id = m.store_id ORDER BY s.sort_order, s.id"
        ).fetchall()
    for g in groups:
        g["store_ids"] = [m["store_id"] for m in members if m["group_id"] == g["id"]]
    return groups


def save_group(name: str, store_ids, group_id: int = None) -> int:
    """Create a group, or rename it and replace its members."""
    with _conn() as con:
        if group_id is None:
            group_id = con.execute("INSERT INTO store_groups (name) VALUES (?)", (name,)).lastrowid
        else:
            if not con.execute("SELECT 1 FROM store_groups WHERE id = ?", (group_id,)).fetchone():
                raise KeyError(group_id)
            con.execute("UPDATE store_groups SET name = ? WHERE id = ?", (name, group_id))
            con.execute("DELETE FROM store_group_members WHERE group_id = ?", (group_id,))
        known = {r["id"] for r in con.execute("SELECT id FROM stores")}
        con.executemany(
            "INSERT OR IGNORE INTO store_group_members (group_id, store_id) VALUES (?, ?)",
            [(group_id, int(s)) for s in store_ids if int(s) in known],
        )
    return group_id


def delete_group(group_id: int):
    with _conn() as con:
        con.execute("DELETE FROM store_group_members WHERE group_id = ?", (group_id,))
        con.execute("DELETE FROM store_groups WHERE id = ?", (group_id,))


# ---------------------------------------------------------------- users & sessions

def list_users():
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, email, name, role, desks, must_change, created_at FROM users ORDER BY id")]


def get_user(user_id: int):
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str):
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def create_user(email: str, name: str, password_hash: str, role: str = "owner",
                desks: str = "profit,cash,cog", must_change: bool = False) -> int:
    with _conn() as con:
        return con.execute(
            "INSERT INTO users (email, name, password_hash, role, desks, must_change)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (email, name, password_hash, role, desks, int(must_change)),
        ).lastrowid


def set_user_password(user_id: int, password_hash: str, must_change: bool = False):
    with _conn() as con:
        con.execute("UPDATE users SET password_hash = ?, must_change = ? WHERE id = ?",
                    (password_hash, int(must_change), user_id))


def set_user_access(user_id: int, role: str, desks: str, name: str = None):
    with _conn() as con:
        con.execute("UPDATE users SET role = ?, desks = ?, name = COALESCE(?, name) WHERE id = ?",
                    (role, desks, name, user_id))


def delete_user(user_id: int):
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def create_session(token_hash: str, user_id: int, expires_at: float):
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < strftime('%s','now')")
        con.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def get_session_user(token_hash: str, now: float):
    with _conn() as con:
        row = con.execute(
            "SELECT u.id, u.email, u.name, u.role, u.desks, u.must_change"
            " FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash, now),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str):
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_sessions_for(user_id: int, keep=None):
    """Sign a user out everywhere (e.g. after a password change)."""
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------- cash connections

def list_cash_connections():
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM cash_connections ORDER BY id")]


def add_cash_connection(provider: str, label: str, client_id: str, secret: str,
                        account_id: str = None) -> int:
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM cash_connections WHERE provider = ? AND client_id = ?"
            " AND COALESCE(account_id, '') = ?",
            (provider, client_id, account_id or "")).fetchone()
        if existing:
            con.execute("UPDATE cash_connections SET label = ?, secret = ? WHERE id = ?",
                        (label, secret, existing["id"]))
            return existing["id"]
        return con.execute(
            "INSERT INTO cash_connections (provider, label, client_id, secret, account_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (provider, label, client_id, secret, account_id or None)).lastrowid


def rename_cash_connection(connection_id: int, label: str):
    with _conn() as con:
        con.execute("UPDATE cash_connections SET label = ? WHERE id = ?", (label, connection_id))


def delete_cash_connection(connection_id: int):
    with _conn() as con:
        con.execute("DELETE FROM cash_connections WHERE id = ?", (connection_id,))


# ---------------------------------------------------------------- cash snapshots

def save_cash_snapshot(day: str, taken_at: str, currency: str, data: dict):
    with _conn() as con:
        con.execute(
            "INSERT INTO cash_snapshots (day, taken_at, currency, data) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(day) DO UPDATE SET taken_at = excluded.taken_at,"
            " currency = excluded.currency, data = excluded.data",
            (day, taken_at, currency, json.dumps(data)))


def cash_snapshots(start: str = "0000", end: str = "9999") -> list:
    """Oldest first: [{day, taken_at, currency, **figures}]"""
    with _conn() as con:
        rows = con.execute("SELECT * FROM cash_snapshots WHERE day BETWEEN ? AND ? ORDER BY day",
                           (start, end)).fetchall()
    return [{"day": r["day"], "taken_at": r["taken_at"], "currency": r["currency"], **json.loads(r["data"])}
            for r in rows]


# ---------------------------------------------------------------- expenses

def expense_rules() -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM expense_rules ORDER BY set_at")]


def set_expense_rule(key: str, category: str, name: str, by: str):
    with _conn() as con:
        if category:
            con.execute("INSERT INTO expense_rules (key, category, name, set_by) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET category = excluded.category, name = excluded.name,"
                        " set_by = excluded.set_by, set_at = datetime('now')", (key, category, name, by))
        else:
            con.execute("DELETE FROM expense_rules WHERE key = ?", (key,))


def expense_suggestions() -> dict:
    with _conn() as con:
        return {r["key"]: dict(r) for r in con.execute("SELECT * FROM expense_suggestions")}


def save_expense_suggestions(picks: list):
    with _conn() as con:
        con.executemany("INSERT OR REPLACE INTO expense_suggestions (key, category, reason) VALUES (?, ?, ?)",
                        [(p["key"], p["category"], p.get("reason", "")) for p in picks])


# ---------------------------------------------------------------- shipments

_SHIP_ORDER = ("order_name", "order_at", "email", "customer", "city", "province", "country", "items",
               "cancelled", "fulfillment")
TRACK_FIELDS = ("status", "sub_status", "last_event", "last_event_at", "last_location", "carrier_name",
                "last_mile", "last_mile_number", "origin", "destination", "transit_days", "eta_from",
                "eta_to", "delivered_at", "events")


def save_order_shipments(store_id: int, o: dict):
    """One Shopify order: its parcels, or one 'waiting for tracking' row."""
    base = {"order_name": o["name"], "order_at": o["created"], "email": o["email"],
            "customer": o["customer"], "city": o["city"], "province": o.get("province") or "",
            "country": o["country"],
            "items": o["items"], "cancelled": int(o["cancelled"]), "fulfillment": o["fulfillment"]}
    numbers = [p["number"] for p in o["parcels"]]
    with _conn() as con:
        if numbers:
            con.execute(f"DELETE FROM shipments WHERE store_id = ? AND order_id = ? AND number NOT IN "
                        f"({','.join('?' * len(numbers))})", (store_id, o["order_id"], *numbers))
        else:
            con.execute("DELETE FROM shipments WHERE store_id = ? AND order_id = ? AND number != ''",
                        (store_id, o["order_id"]))
        rows = o["parcels"] or [{"number": "", "company": "", "url": "", "fulfilled": None}]
        for p in rows:
            vals = {**base, "company": p["company"], "tracking_url": p["url"], "fulfilled_at": p["fulfilled"],
                    "fulfillment_gid": p.get("fid")}
            cols = list(vals)
            con.execute(
                f"INSERT INTO shipments (store_id, order_id, number, {', '.join(cols)}, status)"
                f" VALUES (?, ?, ?, {', '.join('?' * len(cols))}, ?)"
                f" ON CONFLICT(store_id, order_id, number) DO UPDATE SET "
                + ", ".join(f"{c} = excluded.{c}" for c in cols) + ", updated_at = datetime('now')",
                (store_id, o["order_id"], p["number"], *vals.values(), "awaiting" if not p["number"] else "pending"))


def save_tracking(number: str, info: dict):
    cols = [c for c in TRACK_FIELDS if c in info]
    with _conn() as con:
        con.execute(f"UPDATE shipments SET {', '.join(f'{c} = ?' for c in cols)}, registered = 1,"
                    f" tracked_at = datetime('now') WHERE number = ?", (*[info[c] for c in cols], number))


def shipments_to_register(store_ids: list, since: str, limit: int = 400) -> list:
    if not store_ids:
        return []
    with _conn() as con:
        return [dict(r) for r in con.execute(
            f"SELECT number, MIN(order_name) order_name, MIN(order_at) order_at, MIN(country) country,"
            f" MIN(store_id) store_id, MIN(order_id) order_id FROM shipments"
            f" WHERE number != '' AND registered = 0 AND cancelled = 0 AND register_error IS NULL"
            f" AND store_id IN ({','.join('?' * len(store_ids))}) AND COALESCE(fulfilled_at, order_at) >= ?"
            f" GROUP BY number ORDER BY MIN(fulfilled_at) DESC LIMIT ?", (*store_ids, since, limit))]


def mark_registered(numbers: list, errors: dict):
    with _conn() as con:
        con.executemany("UPDATE shipments SET registered = 1 WHERE number = ?", [(n,) for n in numbers])
        con.executemany("UPDATE shipments SET register_error = ? WHERE number = ?",
                        [(e[:200], n) for n, e in errors.items()])


def shipments_to_refresh(limit: int = 400) -> list:
    """Registered parcels not finished and not updated for a day."""
    with _conn() as con:
        return [r["number"] for r in con.execute(
            "SELECT DISTINCT number FROM shipments WHERE registered = 1 AND number != '' AND cancelled = 0"
            " AND status NOT IN ('delivered', 'expired')"
            " AND (tracked_at IS NULL OR tracked_at < datetime('now', '-1 day'))"
            " ORDER BY tracked_at LIMIT ?", (limit,))]


# ---------------------------------------------------------------- settings

def get_setting(key: str, default=None):
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------- shopify apps

def save_shopify_app(shop_domain: str, client_id: str, client_secret: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO shopify_apps (shop_domain, client_id, client_secret) VALUES (?, ?, ?)"
            " ON CONFLICT(shop_domain) DO UPDATE SET client_id = excluded.client_id,"
            " client_secret = excluded.client_secret",
            (shop_domain, client_id, client_secret),
        )


def get_shopify_app(shop_domain: str):
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM shopify_apps WHERE shop_domain = ?", (shop_domain,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- meta link

def set_store_plan(store_id: int, plan: str):
    with _conn() as con:
        con.execute("UPDATE stores SET shopify_plan = ? WHERE id = ?", (plan, store_id))


def set_meta_timezone(store_id: int, tz: str):
    with _conn() as con:
        con.execute("UPDATE stores SET meta_timezone = ? WHERE id = ?", (tz, store_id))


def link_meta(store_id: int, account_id: str, name: str, currency: str,
              connection_id: int, timezone: str = None):
    """Attach one Meta ad account to one store, and only one."""
    with _conn() as con:
        clash = con.execute(
            "SELECT name FROM stores WHERE meta_account_id = ? AND id != ?",
            (account_id, store_id),
        ).fetchone()
        if clash:
            raise AlreadyLinked(
                f"That Meta ad account is already connected to {clash['name']}. "
                "One ad account can only feed one store."
            )
        con.execute(
            "UPDATE stores SET meta_account_id = ?, meta_account_name = ?,"
            " meta_currency = ?, meta_connection_id = ?, meta_timezone = ? WHERE id = ?",
            (account_id, name, currency, connection_id, timezone or None, store_id),
        )


def unlink_meta(store_id: int):
    with _conn() as con:
        con.execute(
            "UPDATE stores SET meta_account_id = NULL, meta_account_name = NULL,"
            " meta_currency = NULL, meta_connection_id = NULL WHERE id = ?",
            (store_id,),
        )


def linked_meta_ids():
    """account_id -> store name, so the dropdown can grey out taken accounts."""
    with _conn() as con:
        return {
            r["meta_account_id"]: r["name"]
            for r in con.execute(
                "SELECT meta_account_id, name FROM stores WHERE meta_account_id IS NOT NULL"
            )
        }


# ---------------------------------------------------------------- meta connections

def save_meta_connection(meta_user_id: str, access_token: str, name, label) -> int:
    """Add a Meta business, or swap in a new token for one already connected."""
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM meta_connections WHERE meta_user_id = ? OR access_token = ?",
            (meta_user_id, access_token),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE meta_connections SET meta_user_id = ?, access_token = ?, name = ?,"
                " label = ? WHERE id = ?",
                (meta_user_id, access_token, name, label, existing["id"]),
            )
            return existing["id"]
        return con.execute(
            "INSERT INTO meta_connections (meta_user_id, access_token, name, label)"
            " VALUES (?, ?, ?, ?)",
            (meta_user_id, access_token, name, label),
        ).lastrowid


def list_meta_connections():
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM meta_connections ORDER BY id")]


def get_meta_connection(connection_id):
    if connection_id is None:
        return None
    with _conn() as con:
        row = con.execute("SELECT * FROM meta_connections WHERE id = ?",
                          (connection_id,)).fetchone()
    return dict(row) if row else None


def set_meta_label(connection_id: int, label: str):
    with _conn() as con:
        con.execute("UPDATE meta_connections SET label = ? WHERE id = ?", (label, connection_id))


def delete_meta_connection(connection_id: int):
    """Remove one business's token, and unlink the stores that read through it."""
    with _conn() as con:
        con.execute(
            "UPDATE stores SET meta_account_id = NULL, meta_account_name = NULL,"
            " meta_currency = NULL, meta_connection_id = NULL WHERE meta_connection_id = ?",
            (connection_id,),
        )
        con.execute("DELETE FROM meta_connections WHERE id = ?", (connection_id,))


# ---------------------------------------------------------------- google sign-in

def save_google_auth(refresh_token: str, email):
    with _conn() as con:
        con.execute(
            "INSERT INTO google_auth (id, refresh_token, email) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET refresh_token = excluded.refresh_token,"
            " email = excluded.email",
            (refresh_token, email),
        )


def get_google_auth():
    with _conn() as con:
        row = con.execute("SELECT * FROM google_auth WHERE id = 1").fetchone()
    return dict(row) if row else None


def clear_google_auth():
    with _conn() as con:
        con.execute("DELETE FROM google_auth")
        con.execute(
            "UPDATE stores SET google_customer_id = NULL, google_login_cid = NULL,"
            " google_account_name = NULL"
        )
