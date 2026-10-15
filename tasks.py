"""Tasks desk: admins give staff work (mostly listing a collection / products + its ads), staff hand back
links, creatives and ad copy, admins approve, then launch the ads on Meta (created paused) or mark it done.

Kinds: "listing" (store, collection, pages to build, ad brief → staff return the links + creatives + copy)
and "custom" (title, description, files). Every task has a Special notes box staff always see first.

Flow: todo → doing → submitted → (changes → submitted …) → approved ("Waiting to launch")
      → launched (listing) or done (custom). launched / done / cancelled = History.
Every step, comment and upload is a row in task_log: the end-of-day view reads it.
Files sit on the volume in <DB dir>/task_files/<task id>/<file id>-<name>.
"""
import json
import re

STATUSES = ("todo", "doing", "submitted", "changes", "approved", "launched", "done", "cancelled")
HISTORY = ("launched", "done", "cancelled")
KINDS = ("listing", "custom")

# what the admin fills in (brief) and what staff hand back (result), per kind
BRIEF_FIELDS = ("collection", "products", "pages", "angle", "headlines", "budget", "countries", "description")
RESULT_FIELDS = ("collection_url", "product_urls", "landing_url", "advertorial_url", "primary_text", "headline",
                 "ad_description", "cta", "other_links", "notes")
CTAS = ("SHOP_NOW", "LEARN_MORE", "ORDER_NOW", "BUY_NOW", "GET_OFFER", "SIGN_UP")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL DEFAULT 'listing',     -- listing | custom
    title         TEXT    NOT NULL,
    store_id      INTEGER,
    assignee_id   INTEGER,                                -- users.id
    status        TEXT    NOT NULL DEFAULT 'todo',
    priority      TEXT    NOT NULL DEFAULT 'normal',      -- normal | high
    due           TEXT,                                   -- YYYY-MM-DD
    special_notes TEXT,                                   -- staff always see this first
    brief         TEXT,                                   -- JSON, admin's input
    result        TEXT,                                   -- JSON, staff's hand-back
    launch        TEXT,                                   -- JSON, Meta ids made by Launch (kept to resume)
    created_by    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    submitted_at  TEXT,
    approved_by   TEXT,
    approved_at   TEXT,
    closed_at     TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS tasks_assignee ON tasks (assignee_id);
CREATE TABLE IF NOT EXISTS task_files (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL,
    side     TEXT    NOT NULL DEFAULT 'staff',            -- admin (brief) | staff (hand-back)
    kind     TEXT    NOT NULL DEFAULT 'creative',         -- creative (goes in the ads) | file
    name     TEXT    NOT NULL,
    mime     TEXT,
    size     INTEGER,
    by       TEXT,
    at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS task_files_task ON task_files (task_id);
CREATE TABLE IF NOT EXISTS task_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL,
    by       TEXT,                                        -- email
    at       TEXT    NOT NULL DEFAULT (datetime('now')),  -- UTC
    action   TEXT    NOT NULL,                            -- created, status, comment, file, edit, launch
    text     TEXT
);
CREATE INDEX IF NOT EXISTS task_log_task ON task_log (task_id);
CREATE INDEX IF NOT EXISTS task_log_at ON task_log (at);
"""

STATUS_NAMES = {"todo": "To do", "doing": "In progress", "submitted": "Submitted for review",
                "changes": "Changes asked", "approved": "Waiting to launch", "launched": "Launched",
                "done": "Done", "cancelled": "Cancelled"}


def _clean(d: dict, fields) -> dict:
    out = {}
    for k in fields:
        v = (d or {}).get(k)
        if isinstance(v, list):
            v = [str(x).strip()[:2000] for x in v if str(x).strip()][:50]
        elif v is not None:
            v = str(v).strip()[:8000]
        if v:
            out[k] = v
    return out


def clean_brief(d: dict) -> dict:
    out = _clean(d, BRIEF_FIELDS)
    if "pages" in out and not isinstance(out["pages"], list):
        out["pages"] = [p.strip() for p in out["pages"].split(",") if p.strip()]
    return out


def clean_result(d: dict) -> dict:
    out = _clean(d, RESULT_FIELDS)
    if out.get("cta") and out["cta"] not in CTAS:
        out.pop("cta")
    return out


def loads(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except ValueError:
        return default if default is not None else {}


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "file").strip())[-80:].strip("-.")
    return name or "file"


def lines(v) -> list:
    """A text box with one item per line (several primary texts / headlines / URLs)."""
    if isinstance(v, list):
        return [x for x in v if x]
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]
