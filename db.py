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

-- Every login try (ok or wrong password): the owner's Security list and new-device alerts.
CREATE TABLE IF NOT EXISTS login_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT    NOT NULL,              -- UTC
    email   TEXT,
    user_id INTEGER,
    ok      INTEGER NOT NULL,
    ip      TEXT,
    ua      TEXT,
    via     TEXT                           -- web / time app / password change
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

-- Google Ads spend per completed store day (read once from Google, kept), like shopify_days.
CREATE TABLE IF NOT EXISTS google_days (
    store_id   INTEGER NOT NULL,
    date       TEXT    NOT NULL,
    spend      REAL    NOT NULL,
    saved_at   TEXT    NOT NULL,
    PRIMARY KEY (store_id, date)
);

-- WhatsApp: every message in / out (one conversation per phone number) and the files people sent.
CREATE TABLE IF NOT EXISTS wa_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id      TEXT UNIQUE,               -- Meta's message id (a retried webhook never counts twice)
    phone      TEXT    NOT NULL,
    direction  TEXT    NOT NULL,          -- in | out
    text       TEXT,
    file_id    INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS wa_messages_phone ON wa_messages (phone, id);
CREATE TABLE IF NOT EXISTS wa_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT    NOT NULL,
    user_email TEXT,
    filename   TEXT    NOT NULL,
    mime       TEXT,
    size       INTEGER,
    caption    TEXT,
    path       TEXT    NOT NULL,          -- on the volume, <DB dir>/wa_files/
    used_for   TEXT,                      -- e.g. invoice:<id>
    created_at TEXT    NOT NULL
);

-- The assistant (Ask chat, later WhatsApp): each person's scheduled messages and alerts, and the messages it
-- sent them (reports, alerts) waiting to be read.
CREATE TABLE IF NOT EXISTS assistant_jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT    NOT NULL,
    kind       TEXT    NOT NULL,          -- schedule | alert
    name       TEXT    NOT NULL,
    spec       TEXT    NOT NULL,          -- JSON
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL,
    last_run   TEXT,
    last_fire  TEXT,
    last_key   TEXT                       -- the slot it last fired for (no repeats)
);
CREATE TABLE IF NOT EXISTS assistant_inbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL,
    source     TEXT,                      -- schedule:<id> | alert:<id>
    created_at TEXT    NOT NULL,
    read_at    TEXT,
    sent_wa    TEXT                       -- when it went out on WhatsApp
);

-- Apps → Google Ads → Manage: rules the ads AI set up, every change (planned / proposed / done / undone)
-- with what it was before, and the chat with the ads AI, per store.
CREATE TABLE IF NOT EXISTS ads_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    params     TEXT    NOT NULL,          -- JSON
    enabled    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    created_by TEXT,
    last_run   TEXT,
    last_notes TEXT
);
CREATE TABLE IF NOT EXISTS ads_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    batch      TEXT    NOT NULL,          -- one plan (several steps) or one rule run
    source     TEXT    NOT NULL,          -- chat | rule:<id> | manual
    kind       TEXT    NOT NULL,
    params     TEXT    NOT NULL,          -- JSON
    summary    TEXT,
    detail     TEXT,                      -- JSON: the products and figures behind it
    before     TEXT,                      -- JSON: what it was, for undo
    status     TEXT    NOT NULL,          -- planned | proposed | done | failed | rejected | undone
    created_at TEXT    NOT NULL,
    decided_by TEXT,
    done_at    TEXT,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS ads_actions_store ON ads_actions (store_id, created_at);
CREATE TABLE IF NOT EXISTS ads_chat (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,          -- JSON {text, plans, rules}
    who        TEXT,
    created_at TEXT    NOT NULL
);

-- Apps → Google Ads: the team's own product tags (per store), e.g. "Winter bestseller".
CREATE TABLE IF NOT EXISTS gads_tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    color    TEXT    NOT NULL DEFAULT '#7C3AED'
);
CREATE TABLE IF NOT EXISTS gads_tag_products (
    tag_id     INTEGER NOT NULL,
    product_id TEXT    NOT NULL,          -- Shopify product id (number) or Google item id
    PRIMARY KEY (tag_id, product_id)
);

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
CREATE TABLE IF NOT EXISTS px_visitors (
    store_id    INTEGER NOT NULL,          -- tracking.py: one shopper (Shopify clientId) per store
    client_id   TEXT    NOT NULL,
    first_at    TEXT, last_at TEXT,
    first_touch TEXT, last_touch TEXT,     -- JSON: UTMs + click ids of the first / latest ad click
    touch_at    TEXT,
    fbp TEXT, fbc TEXT, ip TEXT, ua TEXT,
    PRIMARY KEY (store_id, client_id)
);
-- Tracking → Google Ads: every purchase / add to cart / checkout ProfitDesk sent (or skipped) to Google Ads.
CREATE TABLE IF NOT EXISTS gconv_uploads (
    store_id   INTEGER NOT NULL,
    kind       TEXT    NOT NULL,          -- purchase | add_to_cart | begin_checkout
    ref        TEXT    NOT NULL,          -- Shopify order id (purchase) or pixel event id
    at         TEXT    NOT NULL,          -- when it happened (UTC)
    value      REAL, currency TEXT,
    click      TEXT,                      -- gclid | gbraid | wbraid | '' (no Google click)
    status     TEXT    NOT NULL,          -- sent | failed | retry | no_click
    error      TEXT,
    tries      INTEGER NOT NULL DEFAULT 0,
    sent_at    TEXT,
    PRIMARY KEY (store_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS gconv_store ON gconv_uploads (store_id, at);
CREATE TABLE IF NOT EXISTS px_events (
    id         TEXT PRIMARY KEY,           -- Shopify's event id (a retry never counts twice)
    store_id   INTEGER NOT NULL,
    client_id  TEXT NOT NULL,
    name       TEXT NOT NULL,
    at         TEXT NOT NULL,              -- UTC
    url TEXT, ref TEXT,
    value REAL, currency TEXT,
    product_id TEXT, variant_id TEXT, quantity INTEGER,
    token TEXT, order_id TEXT,             -- checkout token / Shopify order id (checkout events)
    em_hash TEXT, ph_hash TEXT,            -- SHA-256 only, for Meta's Conversions API
    fbp TEXT, fbc TEXT, ip TEXT, ua TEXT,
    a_source TEXT, a_medium TEXT, a_campaign TEXT, a_content TEXT, a_term TEXT, a_id TEXT, a_click TEXT,
    sent_meta  TEXT                        -- when it went to Meta (send mode)
);
CREATE INDEX IF NOT EXISTS px_events_store ON px_events (store_id, at);
CREATE INDEX IF NOT EXISTS px_events_order ON px_events (store_id, order_id);
CREATE TABLE IF NOT EXISTS outside_days (
    date        TEXT PRIMARY KEY,         -- Hong Kong day: PayPal / Airwallex refunds not in Shopify + chargebacks
    currency    TEXT NOT NULL,            -- the display currency they were converted to
    refunds     REAL NOT NULL DEFAULT 0,
    count       INTEGER NOT NULL DEFAULT 0,
    chargebacks REAL NOT NULL DEFAULT 0,
    saved_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS refund_days (
    store_id   INTEGER NOT NULL,          -- Shopify refunds per day (store clock), saved like shopify_days
    date       TEXT    NOT NULL,
    refunds    REAL    NOT NULL DEFAULT 0,
    count      INTEGER NOT NULL DEFAULT 0,
    timezone   TEXT,
    saved_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (store_id, date)
);
CREATE TABLE IF NOT EXISTS rec_sessions (
    sid       TEXT PRIMARY KEY,           -- the visitor's browser session (recordings.py)
    store_id  INTEGER NOT NULL,
    started   TEXT NOT NULL,
    last_at   TEXT NOT NULL,
    first_ts  INTEGER,                    -- first / last event time (ms) -> length
    last_ts   INTEGER,
    chunks    INTEGER NOT NULL DEFAULT 0,
    bytes     INTEGER NOT NULL DEFAULT 0,
    mobile    INTEGER NOT NULL DEFAULT 0,
    width     INTEGER,
    landing   TEXT,
    last_url  TEXT,
    referrer  TEXT,
    pages     TEXT NOT NULL DEFAULT '[]',
    clicks    INTEGER NOT NULL DEFAULT 0,
    product   INTEGER NOT NULL DEFAULT 0,  -- saw a product page
    cart      INTEGER NOT NULL DEFAULT 0,  -- opened the cart page
    tz        TEXT,                        -- browser time zone (rough location)
    lang      TEXT,
    watched   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS rec_sessions_store ON rec_sessions (store_id, started);
CREATE TABLE IF NOT EXISTS disputes (
    key        TEXT PRIMARY KEY,         -- pp:<connection>:<id> | awx:<connection>:<id>
    provider   TEXT NOT NULL,
    state      TEXT,                     -- respond | waiting | review | closed
    due        TEXT,
    data       TEXT NOT NULL,            -- the case in the desk's shape (disputes.py)
    store_id   INTEGER,                  -- the Shopify order it's about, once found
    order_name TEXT,
    link_by    TEXT,                     -- someone linked the order by hand
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dispute_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    key    TEXT NOT NULL,
    at     TEXT NOT NULL DEFAULT (datetime('now')),
    who    TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS dispute_log_key ON dispute_log (key);
CREATE TABLE IF NOT EXISTS supplier_accounts (
    name           TEXT PRIMARY KEY,     -- as on the orders sheet / invoices (Dayone, Graypop...)
    opening_date   TEXT,                 -- business clock: the account counts from this day
    opening_amount REAL NOT NULL DEFAULT 0,   -- USD owed to them that morning (negative = credit)
    note           TEXT,
    set_by         TEXT,
    set_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS supplier_payees (
    key        TEXT PRIMARY KEY,         -- an Expenses payee key, or tx:<id> for one payment
    supplier   TEXT NOT NULL,            -- '' = not a supplier payment
    set_by     TEXT,
    set_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
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

-- Size Charts desk: charts (blocks JSON, see sizecharts.py) and which store's
-- products get them (kind: product | collection | tag | type | vendor | all).
CREATE TABLE IF NOT EXISTS size_charts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',      -- active | draft
    blocks      TEXT NOT NULL DEFAULT '[]',
    countries   TEXT NOT NULL DEFAULT '',            -- comma-separated ISO codes, empty = everywhere
    source      TEXT,                                -- manual | panda | kiwi | ai | ai-import
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS size_chart_rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chart_id  INTEGER NOT NULL,
    store_id  INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    value     TEXT NOT NULL DEFAULT '',
    label     TEXT
);
CREATE INDEX IF NOT EXISTS size_chart_rules_store ON size_chart_rules(store_id, kind, value);
CREATE INDEX IF NOT EXISTS size_chart_rules_chart ON size_chart_rules(chart_id);

-- Product Importer: one row per product per target store.
-- Gmail labels, in step with each mailbox (mail_labels.py). Per email, like Gmail.
CREATE TABLE IF NOT EXISTS mail_tags (
    account_id  INTEGER NOT NULL,
    name        TEXT NOT NULL,
    PRIMARY KEY (account_id, name)
);
CREATE TABLE IF NOT EXISTS mail_message_tags (
    message_id  INTEGER NOT NULL,
    name        TEXT NOT NULL,
    PRIMARY KEY (message_id, name)
);
CREATE INDEX IF NOT EXISTS mail_message_tags_name ON mail_message_tags(name);
CREATE TABLE IF NOT EXISTS tag_colors (
    name   TEXT PRIMARY KEY,
    color  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_translations (
    message_id  INTEGER NOT NULL,
    lang        TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (message_id, lang)
);

CREATE TABLE IF NOT EXISTS widgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL UNIQUE,          -- the private read-only link
    name        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'all',   -- all | store id | g<group id>
    period      TEXT NOT NULL DEFAULT 'today', -- today | yesterday | 7 | 30 | mtd
    metrics     TEXT NOT NULL,                 -- JSON list of metric keys, in order
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_used   TEXT
);

CREATE TABLE IF NOT EXISTS import_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    source_url  TEXT,
    title       TEXT,
    image       TEXT,
    store_id    INTEGER,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | done | skipped | failed
    product_id  TEXT,
    admin_url   TEXT,
    error       TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    done_at     TEXT
);
CREATE INDEX IF NOT EXISTS import_items_job ON import_items(job_id);

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


class _Row(sqlite3.Row):
    """sqlite3.Row that unlocks keys sealed by vault.py when they're read."""
    __slots__ = ()

    def __getitem__(self, k):
        v = sqlite3.Row.__getitem__(self, k)
        if v.__class__ is str and v.startswith("enc1:"):
            import vault
            return vault.unseal(v)
        return v

    def __iter__(self):
        return (self[i] for i in range(len(self)))


@contextmanager
def _conn():
    # WAL: readers never wait for a writer (background jobs copy mail, re-cost orders... while pages
    # read). busy_timeout: a second writer waits up to 10 s instead of failing.
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = _Row
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA synchronous = NORMAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


# Columns added after the first release. Older databases get them on startup.
_ADDED_COLUMNS = {
    "sessions": [
        ("ip", "TEXT"),               # where it logged in from
        ("ua", "TEXT"),               # browser / device
        ("via", "TEXT"),
        ("last_seen", "REAL"),        # unix time, updated at most every 5 minutes
    ],
    "tickets": [
        ("handler", "TEXT"),          # who on the team is handling it (name shown to everyone)
        ("handler_email", "TEXT"),
        ("handler_at", "TEXT"),
        ("auto_asked", "TEXT"),       # when the AI asked the customer for their order number
    ],
    "invoices": [
        ("file_ext", "TEXT"),        # the original file is kept: <DB dir>/invoice_files/<id>.<ext>
        ("file_size", "INTEGER"),
        ("file_at", "TEXT"),
        ("ai_check", "TEXT"),        # pending | agreed | ai_only | review | confirmed | skipped | failed
        ("ai_note", "TEXT"),
        ("review", "TEXT"),          # JSON {diffs, ai}: what the AI read differently
    ],
    "invoice_lines": [
        ("units", "REAL NOT NULL DEFAULT 1"),   # items this line is for (one line can bill 2 pairs)
        ("tier", "INTEGER"),                    # items in the whole Shopify order (2+ = a bundle)
        ("singles", "REAL"),                    # what these items cost when bought on their own (USD)
        ("each", "REAL"),                       # price per item for the whole bundle (supplier may split it unevenly)
        ("check_state", "TEXT"),     # checking (asked the supplier) | kept (supplier corrected: old price stands)
        ("check_by", "TEXT"),
        ("check_at", "TEXT"),
    ],
    "px_visitors": [("g_click", "TEXT"), ("g_at", "TEXT")],
    "px_events": [("got_at", "TEXT")],       # when EcomVital received it (UTC; `at` is the shopper's own clock)
    "gconv_uploads": [("adj_value", "REAL"), ("adj_status", "TEXT"), ("adj_at", "TEXT"), ("adj_error", "TEXT")],  # refund / cancel corrections   # the latest Google click (JSON {gclid|gbraid|wbraid}) and when
    "cog_lines": [("single_usd", "REAL"), ("product_id", "TEXT"), ("country", "TEXT"), ("original", "REAL")],  # original: price before discounts   # country: shipping country code   # product_id: Shopify product GID     # the line at single-item prices (bundles show their discount)
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
        ("auto_ask", "INTEGER NOT NULL DEFAULT 0"),         # reply asking for the order number when none is found
        ("gmail_token", "TEXT"),                            # Google sign-in for sending (gmail.send only)
        ("gmail_email", "TEXT"),
        ("gmail_at", "TEXT"),
    ],
    "mail_messages": [
        ("direction", "TEXT NOT NULL DEFAULT 'in'"),       # in = from a customer, out = our reply
        ("tag_suggest", "TEXT"),                           # AI's suggested labels (JSON), staff confirm
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
        ("access", "TEXT"),          # JSON {desk: view | work | manage}; empty = from role + desks
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
        # WAL: write everything into the main file and drop the side files first, so nothing of
        # the old database can be replayed onto the restored one
        try:
            old = sqlite3.connect(DB_PATH, timeout=30)
            old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            old.close()
        except sqlite3.Error:
            pass
        os.replace(tmp, DB_PATH)
        for side in (DB_PATH + "-wal", DB_PATH + "-shm"):
            try:
                os.remove(side)
            except OSError:
                pass
        _wal()
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    init()  # bring an older backup's tables up to date


def _wal():
    """Switch the database to WAL once (it's stored in the file and stays on)."""
    try:
        con = sqlite3.connect(DB_PATH, timeout=30)
        con.execute("PRAGMA journal_mode = WAL")
        con.close()
    except sqlite3.Error:
        pass


def init():
    _wal()
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
    seal_keys()


def seal_keys() -> int:
    """Lock any key still stored plain (vault.py)."""
    import vault
    with _conn() as con:
        return vault.seal_all(con)


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


def saved_shopify_days(store_id: int, start: str, end: str, tz: str = None) -> dict:
    """tz: only days saved on that clock (a store whose clock changed gets fresh ones)."""
    import json
    with _conn() as con:
        rows = con.execute("SELECT * FROM shopify_days WHERE store_id = ? AND date BETWEEN ? AND ?"
                           + (" AND timezone = ?" if tz else ""),
                           (store_id, start, end, *([tz] if tz else []))).fetchall()
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
            " fulfillment, product, sku, variant_id, quantity, subtotal, cost_usd, source, supplier, note, single_usd, product_id,"
            " country, original) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(store_id, r["order"], r["line_no"], r["day"], r["created"], int(r["cancelled"]),
              r.get("fulfillment"), r["product"], r.get("sku"), r.get("variant_id"), r["quantity"],
              r["subtotal"], r.get("cost"), r.get("source"), r.get("supplier"), r.get("note"), r.get("single"),
              r.get("product_id"), r.get("country"), r.get("original"))
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
            "UPDATE cog_lines SET cost_usd = ?, source = ?, supplier = ?, note = ?, single_usd = ?"
            " WHERE store_id = ? AND order_name = ? AND line_no = ?",
            [(r["cost"], r["source"], r["supplier"], r["note"], r.get("single"), store_id, r["order_name"], r["line_no"])
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
            " product, amount, status, previous, note, units, tier, each, singles) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _line_rows(iid, checked["lines"]))
    return iid


def _line_rows(iid: int, lines: list) -> list:
    return [(iid, l["line_no"], l["order"], l.get("order_name"), l["store_id"], l["title"],
             l["product"], l["amount"], l["status"], l["previous"], l["note"], l.get("units") or 1.0,
             l.get("tier"), l.get("each"), l.get("singles")) for l in lines]


def replace_invoice_lines(iid: int, inv: dict, checked: dict):
    """A person picked the AI's reading of an invoice: its lines replace the first reading."""
    import json
    with _conn() as con:
        con.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (iid,))
        con.execute("UPDATE invoices SET total = ?, store_id = ?, missing = ? WHERE id = ?",
                    (inv["total"], checked["store_id"], json.dumps(checked["missing"]), iid))
        con.executemany(
            "INSERT INTO invoice_lines (invoice_id, line_no, order_key, order_name, store_id, title,"
            " product, amount, status, previous, note, units, tier, each, singles) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _line_rows(iid, checked["lines"]))


def set_invoice_ai(iid: int, state: str, note: str = "", review=None):
    import json
    with _conn() as con:
        con.execute("UPDATE invoices SET ai_check = ?, ai_note = ?, review = ? WHERE id = ?",
                    (state, note, json.dumps(review) if review is not None else None, iid))


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
                "SELECT l.*, i.number, i.date, i.supplier, i.ai_check FROM invoice_lines l"
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


def mail_list(account_ids: list = None, limit: int = 200, category: str = None, where: tuple = None) -> list:
    """where: (extra SQL condition on m, its args), e.g. the All mail exclusions."""
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
        if where:
            q += f" AND ({where[0]})"
            args += list(where[1])
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
            "SELECT id, email, name, role, desks, access, must_change, created_at FROM users ORDER BY id")]


def get_user(user_id: int):
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str):
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def create_user(email: str, name: str, password_hash: str, role: str = "owner",
                desks: str = "profit,cash,cog", must_change: bool = False, access: str = None) -> int:
    with _conn() as con:
        return con.execute(
            "INSERT INTO users (email, name, password_hash, role, desks, must_change, access)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (email, name, password_hash, role, desks, int(must_change), access),
        ).lastrowid


def set_user_password(user_id: int, password_hash: str, must_change: bool = False):
    with _conn() as con:
        con.execute("UPDATE users SET password_hash = ?, must_change = ? WHERE id = ?",
                    (password_hash, int(must_change), user_id))


def set_user_access(user_id: int, role: str, desks: str, name: str = None, access: str = None):
    with _conn() as con:
        con.execute("UPDATE users SET role = ?, desks = ?, access = ?, name = COALESCE(?, name) WHERE id = ?",
                    (role, desks, access, name, user_id))


def delete_user(user_id: int):
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def create_session(token_hash: str, user_id: int, expires_at: float, ip=None, ua=None, via=None):
    import time
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < strftime('%s','now')")
        con.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, ip, ua, via, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token_hash, user_id, expires_at, ip, (ua or "")[:300], via, time.time()),
        )


def log_login(email, user_id, ok: bool, ip, ua, via):
    from datetime import datetime, timezone
    with _conn() as con:
        con.execute("INSERT INTO login_log (at, email, user_id, ok, ip, ua, via) VALUES (?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), (email or "")[:200], user_id,
                     int(ok), ip, (ua or "")[:300], via))
        con.execute("DELETE FROM login_log WHERE id < (SELECT MAX(id) - 3000 FROM login_log)")


def seen_device(user_id: int, ip, ua) -> bool:
    """Has this person logged in successfully from this browser + address in the last 90 days?"""
    with _conn() as con:
        return bool(con.execute(
            "SELECT 1 FROM login_log WHERE user_id = ? AND ok = 1 AND ip IS ? AND ua IS ?"
            " AND at > datetime('now', '-90 days') LIMIT 1", (user_id, ip, (ua or "")[:300])).fetchone())


def list_sessions() -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT s.rowid AS id, s.token_hash, s.user_id, s.created_at, s.ip, s.ua, s.via, s.last_seen,"
            " u.name, u.email FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.expires_at > strftime('%s','now') ORDER BY COALESCE(s.last_seen, 0) DESC")]


def delete_session_ids(ids: list):
    with _conn() as con:
        con.executemany("DELETE FROM sessions WHERE rowid = ?", [(int(i),) for i in ids])


def recent_logins(limit: int = 40) -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT l.*, u.name FROM login_log l LEFT JOIN users u ON u.id = l.user_id"
            " ORDER BY l.id DESC LIMIT ?", (limit,))]


def get_session_user(token_hash: str, now: float):
    with _conn() as con:
        row = con.execute(
            "SELECT u.id, u.email, u.name, u.role, u.desks, u.access, u.must_change, s.last_seen"
            " FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash, now),
        ).fetchone()
        if row and (row["last_seen"] or 0) < now - 300:
            con.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
    if not row:
        return None
    out = dict(row)
    out.pop("last_seen", None)
    return out


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
    import vault
    if vault.is_secret_setting(key):
        value = vault.seal(value)
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


def supplier_accounts() -> dict:
    with _conn() as con:
        return {r["name"]: dict(r) for r in con.execute("SELECT * FROM supplier_accounts")}


def set_supplier_account(name: str, opening_date: str, amount: float, note: str, user: str):
    with _conn() as con:
        con.execute("INSERT INTO supplier_accounts (name, opening_date, opening_amount, note, set_by, set_at)"
                    " VALUES (?,?,?,?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
                    " opening_date = excluded.opening_date, opening_amount = excluded.opening_amount,"
                    " note = excluded.note, set_by = excluded.set_by, set_at = excluded.set_at",
                    (name, opening_date, amount, note, user))


def supplier_payees() -> dict:
    with _conn() as con:
        return {r["key"]: r["supplier"] for r in con.execute("SELECT key, supplier FROM supplier_payees")}


def set_supplier_payee(key: str, supplier, user: str):
    with _conn() as con:
        if supplier is None:
            con.execute("DELETE FROM supplier_payees WHERE key = ?", (key,))
        else:
            con.execute("INSERT OR REPLACE INTO supplier_payees (key, supplier, set_by, set_at)"
                        " VALUES (?,?,?,datetime('now'))", (key, supplier, user))


def save_dispute(case: dict):
    import json
    with _conn() as con:
        con.execute("INSERT INTO disputes (key, provider, state, due, data, fetched_at) VALUES (?,?,?,?,?,datetime('now'))"
                    " ON CONFLICT(key) DO UPDATE SET state = excluded.state, due = excluded.due,"
                    " data = excluded.data, fetched_at = excluded.fetched_at",
                    (case["key"], case["provider"], case["state"], case.get("due"), json.dumps(case)))


def list_disputes() -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM disputes")]


def get_dispute(key: str):
    with _conn() as con:
        r = con.execute("SELECT * FROM disputes WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else None


def link_dispute(key: str, store_id, order_name, by: str = None):
    with _conn() as con:
        con.execute("UPDATE disputes SET store_id = ?, order_name = ?, link_by = ? WHERE key = ?",
                    (store_id, order_name, by, key))


def log_dispute(key: str, who: str, action: str, detail: str = ""):
    with _conn() as con:
        con.execute("INSERT INTO dispute_log (key, who, action, detail) VALUES (?,?,?,?)",
                    (key, who, action, (detail or "")[:4000]))


def dispute_log(key: str) -> list:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM dispute_log WHERE key = ? ORDER BY id DESC", (key,))]


def save_refund_days(store_id: int, days: dict, tz: str):
    """days: {date: {"refunds", "count"}}; replaces what was saved."""
    with _conn() as con:
        con.executemany(
            "INSERT INTO refund_days (store_id, date, refunds, count, timezone, saved_at)"
            " VALUES (?,?,?,?,?, datetime('now')) ON CONFLICT(store_id, date) DO UPDATE SET"
            " refunds = excluded.refunds, count = excluded.count, timezone = excluded.timezone,"
            " saved_at = excluded.saved_at",
            [(store_id, d, float(v.get("refunds") or 0), int(v.get("count") or 0), tz) for d, v in days.items()])


def saved_refund_days(store_id: int, start: str, end: str, tz: str = None) -> dict:
    with _conn() as con:
        rows = con.execute("SELECT date, refunds, count FROM refund_days WHERE store_id = ? AND date BETWEEN ? AND ?"
                           + (" AND timezone = ?" if tz else ""),
                           (store_id, start, end, *([tz] if tz else []))).fetchall()
    return {r["date"]: {"refunds": r["refunds"], "count": r["count"]} for r in rows}


def save_outside_days(days: dict, currency: str):
    """days: {date: {"refunds", "count", "chargebacks"}} for every day of the window (zeros too)."""
    with _conn() as con:
        con.executemany(
            "INSERT OR REPLACE INTO outside_days (date, currency, refunds, count, chargebacks, saved_at)"
            " VALUES (?,?,?,?,?, datetime('now'))",
            [(d, currency, v["refunds"], v["count"], v["chargebacks"]) for d, v in days.items()])


def outside_days(start: str, end: str, currency: str, max_age_minutes: int = 400):
    """Saved rows for start..end, or None when any day is missing, in another currency, or stale."""
    with _conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM outside_days WHERE date BETWEEN ? AND ? AND currency = ?"
            " AND saved_at >= datetime('now', ?)", (start, end, currency, f"-{int(max_age_minutes)} minutes"))]
    from datetime import date as _d, timedelta as _t
    want = (_d.fromisoformat(end) - _d.fromisoformat(start)).days + 1
    return rows if len(rows) >= want else None
