"""Apps → Research: the team's competitor research workspace, a spreadsheet that knows what it holds.

One row per competitor. Who did the research is kept on every row: a person's name when typed in by
hand, "EcomVital" when the app found or filled it (Apify ad counts, traffic checks: next phases).
Basic columns are built in (BUILTIN); anyone with Manage adds more from the page (`research_columns`,
values in the row's `extra` JSON), so the sheet grows without code changes. Every cell change is kept
in `research_log` (who, when, old → new).
"""
import asyncio
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

import apify_client as apify
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
-- Each competitor's Meta ads as the app last saw them (Apify scraper), one row per ad.
CREATE TABLE IF NOT EXISTS research_ads (
    item_id    INTEGER NOT NULL,
    ad_id      TEXT NOT NULL,
    page_name  TEXT,
    start_date TEXT,                 -- YYYY-MM-DD, Meta's (US Pacific) date
    active     INTEGER NOT NULL DEFAULT 1,
    copies     INTEGER NOT NULL DEFAULT 1,
    text       TEXT,
    link       TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (item_id, ad_id)
);
-- Every Apify run: what it cost, how many ads, or why it failed.
CREATE TABLE IF NOT EXISTS research_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER,
    status      TEXT NOT NULL,       -- running | done | error
    apify_run   TEXT,
    ads         INTEGER,
    capped      INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL,
    error       TEXT,
    by_name     TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
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
            have = {r[1] for r in con.execute("PRAGMA table_info(research_items)")}
            if "ads_capped" not in have:
                con.execute("ALTER TABLE research_items ADD COLUMN ads_capped INTEGER NOT NULL DEFAULT 0")
            # a run cut off by a restart never finishes: mark it
            con.execute("UPDATE research_runs SET status = 'error', error = 'Stopped by an app restart; check again.',"
                        " finished_at = ? WHERE status = 'running'", (_now(),))
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
        last = {r[0]: {"status": r[1], "error": r[2], "at": r[3]} for r in con.execute(
            "SELECT item_id, status, error, COALESCE(finished_at, started_at) FROM research_runs"
            " WHERE id IN (SELECT MAX(id) FROM research_runs GROUP BY item_id)")}
    for r in rows:
        r["check"] = last.get(r["id"])
    return {"columns": columns(), "items": rows, "app_name": APP_NAME, "archived_count": n_arch,
            "kinds": list(KINDS), "apify": bool(db.get_setting("apify_token"))}


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


# ---------------------------------------------------------------- Apify: active ads per Facebook page

META_TZ = ZoneInfo("America/Los_Angeles")     # the dates the Ad Library shows ("Started running on …")
HK = ZoneInfo("Asia/Hong_Kong")               # the business clock (daily check time)
_sem = asyncio.Semaphore(2)
_running: set = set()


def apify_config() -> dict:
    try:
        c = json.loads(db.get_setting("research_apify") or "{}")
    except ValueError:
        c = {}
    return {"country": (c.get("country") or "ALL").upper(), "cap": int(c.get("cap") or 500),
            "daily": bool(c.get("daily")), "hour": int(c.get("hour") if c.get("hour") is not None else 9),
            "last_daily": c.get("last_daily") or ""}


def _save_config(cfg: dict):
    db.set_setting("research_apify", json.dumps(cfg))


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _day(v):
    """Meta's start date as YYYY-MM-DD from unix seconds / ms, ISO text or 'Oct 15, 2026'."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        n = float(v)
        if n > 1e11:
            n /= 1000
        return datetime.fromtimestamp(n, META_TZ).date().isoformat()
    s = str(v).strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(META_TZ).date().isoformat() if "T" in s else s[:10]
        except ValueError:
            return s[:10]
    for f in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def parse_ad(it: dict) -> dict:
    """One scraper result → {ad_id, page_name, start_date, active, copies, text, link}. Reads both
    snake_case (Meta's own names) and camelCase."""
    snap = _first(it, "snapshot", "adSnapshot") or {}
    cards = snap.get("cards") or []
    body = snap.get("body")
    text = body.get("text") if isinstance(body, dict) else body
    if not text and cards:
        text = (cards[0] or {}).get("body")
    active = _first(it, "is_active", "isActive")
    return {
        "ad_id": str(_first(it, "ad_archive_id", "adArchiveID", "adArchiveId", "adId", "ad_id", "id") or ""),
        "page_name": _first(it, "page_name", "pageName") or snap.get("page_name") or "",
        "start_date": _day(_first(it, "start_date", "startDate", "start_date_formatted", "startDateFormatted")),
        "active": 1 if active is None or active else 0,
        "copies": int(_first(it, "collation_count", "collationCount") or 1),
        "text": (str(text or "")[:2000]),
        "link": _first(snap, "link_url", "linkUrl") or ((cards[0] or {}).get("link_url") if cards else "") or "",
    }


def meta_today() -> str:
    return datetime.now(META_TZ).date().isoformat()


async def check_item(item_id: int, by_name: str) -> dict:
    """Ask Apify for one competitor's active ads, save them, fill Active ads + New ads today."""
    ensure()
    item = get_item(item_id)
    token = db.get_setting("apify_token")
    if not token:
        raise HTTPException(400, "Paste the Apify key first (Apify button at the top).")
    if not item.get("fb_page"):
        raise HTTPException(400, "Add the competitor's Facebook page first.")
    if item_id in _running:
        return {"ok": True, "running": True}
    cfg = apify_config()
    _running.add(item_id)
    with db._conn() as con:
        run_id = con.execute("INSERT INTO research_runs (item_id, status, by_name, started_at) VALUES (?, 'running', ?, ?)",
                             (item_id, by_name, _now())).lastrowid
    try:
        async with _sem:
            run, raw = await apify.run_actor(token, apify.page_input(item["fb_page"], cfg["country"], cfg["cap"]))
        if raw:
            db.set_setting("research_apify_sample", json.dumps(raw[0])[:6000])
        ads = {}
        for r in raw:
            a = parse_ad(r)
            if a["ad_id"] and not r.get("error"):
                ads[a["ad_id"]] = a
        live = [a for a in ads.values() if a["active"]]
        capped = len(raw) >= cfg["cap"]
        today = meta_today()
        new_today = sum(1 for a in live if a["start_date"] == today)
        now = _now()
        with db._conn() as con:
            for a in ads.values():
                con.execute(
                    "INSERT INTO research_ads (item_id, ad_id, page_name, start_date, active, copies, text, link, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id, ad_id) DO UPDATE SET page_name = excluded.page_name,"
                    " start_date = excluded.start_date, active = excluded.active, copies = excluded.copies, text = excluded.text,"
                    " link = excluded.link, last_seen = excluded.last_seen",
                    (item_id, a["ad_id"], a["page_name"], a["start_date"], a["active"], a["copies"], a["text"], a["link"], now, now))
            if not capped:        # everything came back: ads not in this answer have stopped
                seen = list(ads)
                con.execute(f"UPDATE research_ads SET active = 0 WHERE item_id = ? AND ad_id NOT IN ({','.join('?' * len(seen))})"
                            if seen else "UPDATE research_ads SET active = 0 WHERE item_id = ?", (item_id, *seen))
            old = con.execute("SELECT ads_count, ads_new_today FROM research_items WHERE id = ?", (item_id,)).fetchone()
            con.execute("UPDATE research_items SET ads_count = ?, ads_new_today = ?, ads_capped = ?, ads_by = ?, ads_at = ?,"
                        " updated_at = ?, updated_by = ? WHERE id = ?",
                        (len(live), new_today, 1 if capped else 0, APP_NAME, now, now, APP_NAME, item_id))
            if old["ads_count"] != len(live):
                _log(con, item_id, "ads_count", old["ads_count"], len(live), APP_NAME)
            if old["ads_new_today"] != new_today:
                _log(con, item_id, "ads_new_today", old["ads_new_today"], new_today, APP_NAME)
            con.execute("UPDATE research_runs SET status = 'done', apify_run = ?, ads = ?, capped = ?, cost_usd = ?,"
                        " finished_at = ? WHERE id = ?", (run.get("id"), len(live), 1 if capped else 0,
                                                          run.get("usageTotalUsd"), now, run_id))
        return {"ok": True, "ads": len(live), "new_today": new_today, "capped": capped, "cost_usd": run.get("usageTotalUsd")}
    except (apify.ApifyError, Exception) as e:
        msg = str(e) if isinstance(e, apify.ApifyError) else f"Something went wrong: {e}"
        with db._conn() as con:
            con.execute("UPDATE research_runs SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
                        (msg[:500], _now(), run_id))
        return {"ok": False, "error": msg}
    finally:
        _running.discard(item_id)


async def check_all(by_name: str) -> int:
    ensure()
    with db._conn() as con:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM research_items WHERE archived = 0 AND fb_page != '' ORDER BY id")]
    for i in ids:
        await check_item(i, by_name)
    return len(ids)


async def loop():
    """Daily check of every competitor with a Facebook page, at the set Hong Kong hour (only when switched on)."""
    await asyncio.sleep(240)
    while True:
        try:
            cfg = apify_config()
            now = datetime.now(HK)
            if cfg["daily"] and db.get_setting("apify_token") and now.hour >= cfg["hour"] \
                    and cfg["last_daily"] != now.date().isoformat():
                cfg["last_daily"] = now.date().isoformat()
                _save_config(cfg)
                await check_all(APP_NAME)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Research daily ads check failed: {e}")
        await asyncio.sleep(600)


def _owner(request: Request):
    if _user(request).get("role", "owner") != "owner":
        raise HTTPException(403, "Only the owner can change the Apify connection.")


@router.get("/api/research/apify")
async def api_apify(request: Request, check: int = 0):
    ensure()
    token = db.get_setting("apify_token")
    out = {"connected": bool(token), "key_end": token[-4:] if token else "", "config": apify_config(),
           "owner": _user(request).get("role", "owner") == "owner"}
    if token and check:
        try:
            out["account"] = await apify.me(token)
        except apify.ApifyError as e:
            out["error"] = str(e)
    with db._conn() as con:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        r = con.execute("SELECT COUNT(*), SUM(cost_usd), SUM(ads) FROM research_runs WHERE status = 'done' AND started_at LIKE ?",
                        (month + "%",)).fetchone()
        out["this_month"] = {"runs": r[0], "cost_usd": r[1], "ads": r[2]}
        out["recent"] = [dict(x) for x in con.execute(
            "SELECT r.*, i.name FROM research_runs r LEFT JOIN research_items i ON i.id = r.item_id ORDER BY r.id DESC LIMIT 15")]
    try:
        sample = json.loads(db.get_setting("research_apify_sample") or "null")
        out["sample_keys"] = sorted(sample.keys()) if isinstance(sample, dict) else None
    except ValueError:
        out["sample_keys"] = None
    return out


@router.put("/api/research/apify")
async def api_apify_save(payload: dict, request: Request):
    """{token} to connect (checked with Apify first), {remove: true}, or settings {country, cap, daily, hour}."""
    _owner(request)
    if payload.get("remove"):
        db.set_setting("apify_token", "")
        return {"ok": True}
    if payload.get("token"):
        token = str(payload["token"]).strip()
        try:
            acct = await apify.me(token)
        except apify.ApifyError as e:
            raise HTTPException(400, str(e))
        db.set_setting("apify_token", token)
        return {"ok": True, "account": acct}
    cfg = apify_config()
    if "country" in payload:
        c = str(payload["country"] or "ALL").strip().upper()
        if c != "ALL" and not re.fullmatch(r"[A-Z]{2}", c):
            raise HTTPException(400, "Country is a two-letter code like US, or ALL.")
        cfg["country"] = c
    if "cap" in payload:
        cfg["cap"] = max(10, min(int(payload["cap"] or 500), 5000))
    if "daily" in payload:
        cfg["daily"] = bool(payload["daily"])
    if "hour" in payload:
        cfg["hour"] = max(0, min(int(payload["hour"]), 23))
    _save_config(cfg)
    return {"ok": True, "config": cfg}


@router.post("/api/research/items/{item_id}/check")
async def api_check(item_id: int, request: Request):
    """Starts the check in the background; the page shows it running and refreshes."""
    _level(request, "manage")
    item = get_item(item_id)
    if not db.get_setting("apify_token"):
        raise HTTPException(400, "Paste the Apify key first (Apify button at the top).")
    if not item.get("fb_page"):
        raise HTTPException(400, "Add the competitor's Facebook page first.")
    asyncio.create_task(check_item(item_id, _who(request)))
    return {"ok": True, "started": True}


@router.post("/api/research/check-all")
async def api_check_all(request: Request):
    _level(request, "manage")
    if not db.get_setting("apify_token"):
        raise HTTPException(400, "Paste the Apify key first (Apify button at the top).")
    asyncio.create_task(check_all(_who(request)))
    return {"ok": True, "started": True}


@router.get("/api/research/items/{item_id}/ads")
def api_ads(item_id: int):
    ensure()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM research_ads WHERE item_id = ? ORDER BY active DESC, start_date DESC LIMIT 1000", (item_id,))]
        last = con.execute("SELECT * FROM research_runs WHERE item_id = ? ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
    return {"ads": rows, "today": meta_today(), "last_run": dict(last) if last else None}
