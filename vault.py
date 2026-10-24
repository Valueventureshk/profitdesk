"""Locks the keys people paste into EcomVital (Shopify, Meta, PayPal, Airwallex, AI ...).

Stored values are encrypted ("enc1:..."); db._conn's rows unlock them when read, so the rest of
the app sees plain values and needs no changes. A copy of the database or a backup file alone is
useless: the lock key lives outside the database, in the DATA_KEY variable (comma-separated) and/or
the file data.key next to the database (made on first start). Several keys may be listed: the first
locks new values, any of them unlocks (so a backup from another server opens once its key is added).

seal_all() locks anything still plain; it runs at start-up and every minute, so a freshly pasted
key is plain for at most a minute.
"""
import os
import threading

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

PREFIX = "enc1:"

# (table, column) holding keys. Tables made by other modules may not exist yet.
COLUMNS = (
    ("stores", "access_token"),
    ("shopify_apps", "client_secret"),
    ("meta_connections", "access_token"),
    ("meta_auth", "access_token"),
    ("cash_connections", "secret"),
    ("mail_accounts", "password"),
    ("mail_accounts", "gmail_token"),
    ("google_auth", "refresh_token"),
    ("fb_pages", "page_token"),
)
SETTINGS = ("anthropic_api_key", "openai_api_key", "sheets_google_token", "apify_token", "co_awx", "google_client_secret",
            "kit_link_key", "slack_bot_token", "slack_signing_secret", "social_webhook_token",
            "track17_key", "wa_app_secret", "wa_token", "wa_verify", "world_salt")
SETTING_PREFIXES = ("px_meta_token:",)

_lock = threading.Lock()
_state = {"fernet": None, "unreadable": 0}


def _key_file(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "data.key")


def _db_path():
    import db
    return db.DB_PATH


def _read_keys() -> list:
    keys = [k.strip() for k in os.getenv("DATA_KEY", "").split(",") if k.strip()]
    path = _key_file(_db_path())
    if os.path.exists(path):
        with open(path) as f:
            keys += [l.strip() for l in f if l.strip() and l.strip() not in keys]
    if not keys:
        key = Fernet.generate_key().decode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key + "\n")
        keys = [key]
    return keys


def _fernet() -> MultiFernet:
    f = _state["fernet"]
    if f is None:
        with _lock:
            if _state["fernet"] is None:
                _state["fernet"] = MultiFernet([Fernet(k.encode()) for k in _read_keys()])
            f = _state["fernet"]
    return f


def reset():
    """Forget the loaded keys (after the database moved or a key was added)."""
    with _lock:
        _state["fernet"] = None


def keys_text() -> str:
    """Every lock key, one per line (for the owner to keep, or to move to another server)."""
    return "\n".join(_read_keys()) + "\n"


def add_keys(text: str) -> int:
    """Add lock keys from another server (one per line). Returns how many were new."""
    have = set(_read_keys())
    new = []
    for line in (text or "").replace(",", "\n").splitlines():
        k = line.strip()
        if not k or k in have or k in new:
            continue
        try:
            Fernet(k.encode())
        except (ValueError, TypeError):
            raise ValueError("That isn't an EcomVital lock key.")
        new.append(k)
    if new:
        with open(_key_file(_db_path()), "a") as f:
            f.write("\n".join(new) + "\n")
        reset()
    return len(new)


def seal(value):
    if not isinstance(value, str) or not value or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def unseal(value: str) -> str:
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        _state["unreadable"] += 1
        return ""          # never hand the locked text to Shopify / Meta as if it were a key


def is_secret_setting(key: str) -> bool:
    return key in SETTINGS or key.startswith(SETTING_PREFIXES)


def seal_all(con) -> int:
    """Lock every plain key (con = a db._conn connection). Returns how many were locked."""
    import sqlite3
    done = 0
    con = _raw(con)
    for table, col in COLUMNS:
        try:
            rows = con.execute(f"SELECT rowid, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"
                               f" AND substr({col}, 1, 5) != ?", (PREFIX,)).fetchall()
        except sqlite3.OperationalError:          # table / column not made yet
            continue
        for r in rows:
            con.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (seal(r[1]), r[0]))
            done += 1
    marks = ",".join("?" * len(SETTINGS))
    likes = " OR ".join("key LIKE ?" for _ in SETTING_PREFIXES)
    rows = con.execute(f"SELECT key, value FROM settings WHERE (key IN ({marks}) OR {likes})"
                       " AND value IS NOT NULL AND value != '' AND substr(value, 1, 5) != ?",
                       (*SETTINGS, *(p + "%" for p in SETTING_PREFIXES), PREFIX)).fetchall()
    for r in rows:
        con.execute("UPDATE settings SET value = ? WHERE key = ?", (seal(r[1]), r[0]))
        done += 1
    return done


def _raw(con):
    """A cursor on the same connection that returns stored values as they are (still locked)."""
    cur = con.cursor()
    cur.row_factory = None
    return cur


def status(con) -> dict:
    """How many keys are locked / plain / unreadable with this server's lock keys."""
    import sqlite3
    locked = plain = bad = 0
    vals = []
    con = _raw(con)
    for table, col in COLUMNS:
        try:
            vals += [r[0] for r in con.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''")]
        except sqlite3.OperationalError:
            continue
    marks = ",".join("?" * len(SETTINGS))
    likes = " OR ".join("key LIKE ?" for _ in SETTING_PREFIXES)
    vals += [r[0] for r in con.execute(f"SELECT value FROM settings WHERE (key IN ({marks}) OR {likes})"
                                       " AND value IS NOT NULL AND value != ''",
                                       (*SETTINGS, *(p + "%" for p in SETTING_PREFIXES)))]
    f = _fernet()
    for v in vals:
        if not v.startswith(PREFIX):
            plain += 1
            continue
        try:
            f.decrypt(v[len(PREFIX):].encode())
            locked += 1
        except InvalidToken:
            bad += 1
    return {"locked": locked, "plain": plain, "unreadable": bad,
            "key_from": "variable" if os.getenv("DATA_KEY") else "file"}
