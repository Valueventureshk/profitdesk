"""Apps → Time (like Hubstaff): hours, activity, apps / sites and screenshots from the EcomVital Time desktop app.

The desktop app (desktop/time_app.py, built for Mac + Windows) signs in once with the person's EcomVital email +
password and gets a **device token** (kept hashed here, revocable). While the person has pressed Start it sends, every
minute, one row per minute: was there keyboard / mouse activity (counts only, never what was typed), and the app +
window title in front most of that minute. Once in every 10-minute block, at a random moment, it sends a screenshot
(all screens, JPEG). Screenshots are kept `keep_days` (default 30), then deleted; minutes stay.

Worked time = minutes tracked; activity % = minutes with input ÷ minutes tracked (Hubstaff's measure).
Days are the business clock (Hong Kong) unless a person's own clock is asked for.
"""
import hashlib
import secrets
from datetime import datetime, timezone

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS time_devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    token_hash TEXT    NOT NULL UNIQUE,
    name       TEXT,                  -- computer name
    os         TEXT,
    version    TEXT,
    created_at TEXT NOT NULL,
    last_seen  TEXT,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS time_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    device_id  INTEGER,
    started_at TEXT NOT NULL,         -- UTC
    ended_at   TEXT,
    last_seen  TEXT
);
CREATE TABLE IF NOT EXISTS time_minutes (
    user_id    INTEGER NOT NULL,
    minute     TEXT    NOT NULL,      -- UTC, 'YYYY-MM-DDTHH:MM'
    session_id INTEGER,
    active     INTEGER NOT NULL DEFAULT 0,
    keys       INTEGER NOT NULL DEFAULT 0,
    clicks     INTEGER NOT NULL DEFAULT 0,
    moves      INTEGER NOT NULL DEFAULT 0,
    app        TEXT,
    title      TEXT,
    task_id    INTEGER,
    PRIMARY KEY (user_id, minute)
);
CREATE INDEX IF NOT EXISTS time_minutes_min ON time_minutes (minute);
CREATE TABLE IF NOT EXISTS time_shots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    session_id INTEGER,
    taken_at   TEXT    NOT NULL,      -- UTC
    screens    INTEGER,
    width      INTEGER,
    height     INTEGER,
    bytes      INTEGER,
    app        TEXT,
    title      TEXT,
    deleted_at TEXT,
    deleted_by TEXT
);
CREATE INDEX IF NOT EXISTS time_shots_user ON time_shots (user_id, taken_at);
"""

DEFAULTS = {"keep_days": 30, "block_minutes": 10, "idle_minutes": 5, "staff_delete_minutes": 10}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_device(user_id: int, name: str, os_name: str, version: str) -> str:
    token = "evt_" + secrets.token_urlsafe(32)
    with db._conn() as con:
        con.execute("INSERT INTO time_devices (user_id, token_hash, name, os, version, created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                    (user_id, digest(token), (name or "")[:80], (os_name or "")[:40], (version or "")[:20], now(), now()))
    return token


def device_for(token: str):
    """(device row, user row) for a device token, or None."""
    if not token or not token.startswith("evt_"):
        return None
    with db._conn() as con:
        d = con.execute("SELECT * FROM time_devices WHERE token_hash = ? AND revoked_at IS NULL", (digest(token),)).fetchone()
        if not d:
            return None
        con.execute("UPDATE time_devices SET last_seen = ? WHERE id = ?", (now(), d["id"]))
    u = db.get_user(d["user_id"])
    return (dict(d), dict(u)) if u else None


def minute_key(iso: str) -> str:
    """Any ISO time → its UTC minute 'YYYY-MM-DDTHH:MM'."""
    t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    if not t.tzinfo:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def app_group(app: str, title: str) -> str:
    """What to total time under: the app, and for browsers the site / page title's last part (e.g. 'Shopify')."""
    a = (app or "").strip() or "Unknown"
    low = a.lower()
    if any(b in low for b in ("chrome", "safari", "edge", "firefox", "brave", "arc", "opera")):
        t = (title or "").strip()
        for sep in (" - ", " – ", " — ", " | "):
            if sep in t:
                parts = [p.strip() for p in t.split(sep) if p.strip()]
                # browsers end titles with their own name: take the part before it (usually the site)
                parts = [p for p in parts if p.lower() not in ("google chrome", "microsoft edge", "safari", "mozilla firefox", "brave", "arc")]
                if parts:
                    return f"{a} · {parts[-1][:60]}"
        return f"{a} · {t[:60]}" if t else a
    return a
