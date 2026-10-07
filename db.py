"""SQLite storage.

Only these things are stored: connected stores (with their Shopify token and
cost percentage), the linked Google Ads and Meta Ads account per store, one
Google sign-in shared across all stores, and one Meta access token per Meta
business (each store's ad account is read through the token that can see it).

No sales or spend figures are stored. Those are fetched live every time the
dashboard loads.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(__file__), "profitdesk.db")

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
    "stores": [
        ("meta_account_id", "TEXT UNIQUE"),
        ("meta_account_name", "TEXT"),
        ("meta_currency", "TEXT"),
        ("meta_connection_id", "INTEGER"),
        ("meta_timezone", "TEXT"),
        ("google_currency", "TEXT"),
        ("shopify_plan", "TEXT"),
    ],
}


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


def delete_store(store_id: int):
    with _conn() as con:
        con.execute("DELETE FROM stores WHERE id = ?", (store_id,))
        con.execute("DELETE FROM store_group_members WHERE store_id = ?", (store_id,))


def rename_store(store_id: int, name: str):
    with _conn() as con:
        con.execute("UPDATE stores SET name = ? WHERE id = ?", (name, store_id))


def set_cost_pct(store_id: int, pct: float):
    pct = max(0.0, min(100.0, float(pct)))
    with _conn() as con:
        con.execute("UPDATE stores SET cost_pct = ? WHERE id = ?", (pct, store_id))


# ---------------------------------------------------------------- google link

def link_google(store_id: int, customer_id: str, login_cid, name: str, currency: str = None):
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
            " google_account_name = ?, google_currency = ? WHERE id = ?",
            (customer_id, login_cid, name, currency or None, store_id),
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
