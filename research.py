"""Apps → Research: the team's competitor research workspace, a spreadsheet that knows what it holds.

One row per competitor. Who did the research is kept on every row: a person's name when typed in by
hand, "EcomVital" when the app found or filled it (Apify ad counts, traffic checks: next phases).
Basic columns are built in (BUILTIN); anyone with Manage adds more from the page (`research_columns`,
values in the row's `extra` JSON), so the sheet grows without code changes. Every cell change is kept
in `research_log` (who, when, old → new).
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

import auth
import db

router = APIRouter()
APP_NAME = "EcomVital"

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    by_kind       TEXT NOT NULL DEFAULT 'person',   -- person | app
    by_name       TEXT NOT NULL,                    -- the person's name, or EcomVital
    by_user       INTEGER,
    name          TEXT NOT NULL DEFAULT '',         -- competitor name
    site_url      TEXT NOT NULL DEFAULT '',
    domain        TEXT NOT NULL DEFAULT '',         -- from site_url, to spot the same store twice
    fb_page       TEXT NOT NULL DEFAULT '',         -- Facebook page link (the app counts its ads from it later)
    niche         TEXT NOT NULL DEFAULT '',
    traffic       REAL,                             -- monthly visits
    traffic_by    TEXT,                             -- a person's name, or EcomVital (API)
    traffic_at    TEXT,
    ads_count     INTEGER,                          -- active Meta ads
    ads_new_today INTEGER,                          -- ads that started running today
    ads_by        TEXT,
    ads_at        TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    extra         TEXT NOT NULL DEFAULT '{}',       -- the added columns {key: value}
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    updated_by    TEXT
);
CREATE TABLE IF NOT EXISTS research_columns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text',        -- text | number | link | date
    sort       INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    field   TEXT NOT NULL,
    old     TEXT,
    new     TEXT,
    by_name TEXT,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_log_item ON research_log(item_id);
"""
_ready = {"ok": False}

# key, label, kind, who may type it (False = the app fills it; people can still correct it)
BUILTIN = [
    ("by_name", "Researched by", "who"),
    ("name", "Competitor", "text"),
    ("site_url", "Website", "link"),
    ("fb_page", "Facebook page", "link"),
    ("niche", "Niche", "text"),
    ("traffic", "Monthly traffic", "number"),
    ("ads_count", "Active ads", "number"),
    ("ads_new_today", "New ads today", "number"),
    ("notes", "Notes", "text"),
]
EDITABLE = {"name", "site_url", "fb_page", "niche", "traffic", "ads_count", "ads_new_today", "notes"}
NUMBERS = {"traffic", "ads_count", "ads_new_today"}
STAMPED = {"traffic": ("traffic_by", "traffic_at"), "ads_count": ("ads_by", "ads_at"),
           "ads_new_today": ("ads_by", "ads_at")}
KINDS = ("text", "number", "link", "date")


def ensure():
    if not _ready["ok"]:
        with db._conn() as con:
            con.executescript(SCHEMA)
        _ready["ok"] = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _who(request: Request) -> str:
    u = _user(request)
    return (u.get("name") or "").strip() or u.get("email") or "?"


def _level(request: Request, need: str):
    if not auth.at_least(_user(request), "research", need):
        raise HTTPException(403, "That needs Manage access to Research." if need == "manage"
                            else "Your access to Research is view-only.")


def domain_of(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def clean_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def _number(v):
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().lower().replace(",", "").replace(" ", "")
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    try:
        n = round(float(s) * mult, 2)
        return int(n) if n == int(n) else n
    except ValueError:
        raise HTTPException(400, f"“{v}” isn't a number (you can type 12000, 12k or 1.2m).")


def columns() -> list:
    ensure()
    with db._conn() as con:
        extra = [dict(r) for r in con.execute("SELECT key, label, kind, sort FROM research_columns ORDER BY sort, id")]
    return ([{"key": k, "label": l, "kind": kind, "builtin": True} for k, l, kind in BUILTIN]
            + [{**c, "builtin": False} for c in extra])


def _row(r) -> dict:
    d = dict(r)
    try:
        d["extra"] = json.loads(d.get("extra") or "{}")
    except ValueError:
        d["extra"] = {}
    return d


def items(archived: bool = False) -> list:
    ensure()
    with db._conn() as con:
        rows = [_row(r) for r in con.execute(
            "SELECT * FROM research_items WHERE archived = ? ORDER BY id DESC", (1 if archived else 0,))]
    dup = {}
    for r in rows:
        if r["domain"]:
            dup[r["domain"]] = dup.get(r["domain"], 0) + 1
    for r in rows:
        r["duplicate"] = bool(r["domain"]) and dup.get(r["domain"], 0) > 1
    return rows


def _log(con, item_id: int, field: str, old, new, by: str):
    con.execute("INSERT INTO research_log (item_id, field, old, new, by_name, at) VALUES (?,?,?,?,?,?)",
                (item_id, field, None if old is None else str(old), None if new is None else str(new), by, _now()))


def add_item(fields: dict, by_name: str, by_kind: str = "person", by_user=None) -> dict:
    """A new row. Used by the page (a person) and later by the app's own research (by_kind 'app')."""
    ensure()
    now = _now()
    site = clean_url(fields.get("site_url") or "")
    with db._conn() as con:
        cur = con.execute(
            "INSERT INTO research_items (by_kind, by_name, by_user, name, site_url, domain, fb_page, niche, notes,"
            " created_at, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (by_kind, by_name, by_user, (fields.get("name") or "").strip(), site, domain_of(site),
             clean_url(fields.get("fb_page") or ""), (fields.get("niche") or "").strip(),
             (fields.get("notes") or "").strip(), now, now, by_name))
        item_id = cur.lastrowid
        _log(con, item_id, "created", None, by_kind, by_name)
    for k in NUMBERS:
        if fields.get(k) not in (None, ""):
            set_cell(item_id, k, fields[k], by_name)
    return get_item(item_id)


def get_item(item_id: int) -> dict:
    with db._conn() as con:
        r = con.execute("SELECT * FROM research_items WHERE id = ?", (item_id,)).fetchone()
    if not r:
        raise HTTPException(404, "That row isn't there any more.")
    return _row(r)


def set_cell(item_id: int, key: str, value, by_name: str) -> dict:
    ensure()
    item = get_item(item_id)
    custom = {c["key"]: c for c in columns() if not c["builtin"]}
    with db._conn() as con:
        if key in EDITABLE:
            if key in NUMBERS:
                value = _number(value)
            elif key in ("site_url", "fb_page"):
                value = clean_url(str(value or ""))
            else:
                value = str(value or "").strip()
            old = item.get(key)
            if old == value:
                return item
            sets, vals = [f"{key} = ?"], [value]
            if key == "site_url":
                sets.append("domain = ?"); vals.append(domain_of(value))
            if key in STAMPED:
                by_col, at_col = STAMPED[key]
                sets += [f"{by_col} = ?", f"{at_col} = ?"]; vals += [by_name, _now()]
            con.execute(f"UPDATE research_items SET {', '.join(sets)}, updated_at = ?, updated_by = ? WHERE id = ?",
                        (*vals, _now(), by_name, item_id))
            _log(con, item_id, key, old, value, by_name)
        elif key in custom:
            kind = custom[key]["kind"]
            if kind == "number":
                value = _number(value)
            elif kind == "link":
                value = clean_url(str(value or ""))
            else:
                value = str(value or "").strip()
            extra = dict(item["extra"])
            old = extra.get(key)
            if old == value:
                return item
            if value in (None, ""):
                extra.pop(key, None)
            else:
                extra[key] = value
            con.execute("UPDATE research_items SET extra = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                        (json.dumps(extra), _now(), by_name, item_id))
            _log(con, item_id, key, old, value, by_name)
        else:
            raise HTTPException(400, "That column can't be changed here.")
    return get_item(item_id)


# ---------------------------------------------------------------- routes (desk "research")

@router.get("/api/research")
def api_list(archived: int = 0):
    rows = items(bool(archived))
    with db._conn() as con:
        n_arch = con.execute("SELECT COUNT(*) FROM research_items WHERE archived = 1").fetchone()[0]
    return {"columns": columns(), "items": rows, "app_name": APP_NAME, "archived_count": n_arch,
            "kinds": list(KINDS)}


@router.post("/api/research/items")
def api_add(payload: dict, request: Request):
    _level(request, "work")
    item = add_item(payload or {}, _who(request), "person", _user(request).get("id"))
    return {"ok": True, "item": {**item, "duplicate": False}}


@router.put("/api/research/items/{item_id}")
def api_set(item_id: int, payload: dict, request: Request):
    """{key, value}: one cell."""
    _level(request, "work")
    item = set_cell(item_id, str(payload.get("key") or ""), payload.get("value"), _who(request))
    return {"ok": True, "item": item}


@router.post("/api/research/items/{item_id}/archive")
def api_archive(item_id: int, payload: dict, request: Request):
    _level(request, "work")
    back = bool((payload or {}).get("restore"))
    get_item(item_id)
    with db._conn() as con:
        con.execute("UPDATE research_items SET archived = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                    (0 if back else 1, _now(), _who(request), item_id))
        _log(con, item_id, "archived", None, "no" if back else "yes", _who(request))
    return {"ok": True}


@router.get("/api/research/items/{item_id}/log")
def api_log(item_id: int):
    ensure()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT field, old, new, by_name, at FROM research_log WHERE item_id = ? ORDER BY id DESC LIMIT 200",
            (item_id,))]
    labels = {c["key"]: c["label"] for c in columns()}
    for r in rows:
        r["label"] = labels.get(r["field"], r["field"])
    return {"log": rows}


@router.post("/api/research/columns")
def api_add_column(payload: dict, request: Request):
    _level(request, "manage")
    ensure()
    label = (payload.get("label") or "").strip()[:40]
    kind = payload.get("kind") or "text"
    if not label:
        raise HTTPException(400, "Give the column a name.")
    if kind not in KINDS:
        raise HTTPException(400, "Pick a column type.")
    key = "c_" + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "col")[:30]
    taken = {c["key"] for c in columns()} | {c["label"].lower() for c in columns()}
    if label.lower() in taken:
        raise HTTPException(400, "There's already a column with that name.")
    base, n = key, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    with db._conn() as con:
        sort = (con.execute("SELECT MAX(sort) FROM research_columns").fetchone()[0] or 0) + 1
        con.execute("INSERT INTO research_columns (key, label, kind, sort, created_by, created_at) VALUES (?,?,?,?,?,?)",
                    (key, label, kind, sort, _who(request), _now()))
    return {"ok": True, "columns": columns()}


@router.put("/api/research/columns/{key}")
def api_edit_column(key: str, payload: dict, request: Request):
    """Rename, or remove (the values stay in each row's extra, so adding it back brings them back)."""
    _level(request, "manage")
    ensure()
    with db._conn() as con:
        if payload.get("remove"):
            con.execute("DELETE FROM research_columns WHERE key = ?", (key,))
        else:
            label = (payload.get("label") or "").strip()[:40]
            if not label:
                raise HTTPException(400, "Give the column a name.")
            con.execute("UPDATE research_columns SET label = ? WHERE key = ?", (label, key))
    return {"ok": True, "columns": columns()}
