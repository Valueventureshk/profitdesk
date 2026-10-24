"""Apps → Sheets (desk `sheets`, built 24 Oct 2026): the team's Google Sheets inside EcomVital, backed up with history.

Add a Sheet by its link; every 15 minutes EcomVital reads every tab (what people see, FORMATTED_VALUE) and keeps a new
version of a tab whenever it changed (zlib-compressed JSON in `sh_versions`, with what changed against the version before:
rows added / removed, cells changed, a sample). Versions are kept for 30 days, then one per day (the day's last).
Anyone with the desk can view the latest copy, the history (any version, changed cells marked) and download a version as
Excel. Work and Manage can edit a cell or add a row: written to Google with the Sheets API (only when the cell still
holds what the person saw), logged in `sh_edits`. Manage adds / removes Sheets.

The AI learns each Sheet (Haiku): what each tab is for, what a row means, what the columns hold (`ai_notes`), redone when
the headers change. Daily log (`sh_daily`, Hong Kong days): rows added, cells changed per tab and a short AI summary of
the day's work, written after midnight for the day before (and "today so far" on demand). The Ask assistant / connector
reads Sheets with the `sheets` tool.

Google access: reading uses the Sheets sign-in made on this desk (setting `sheets_google_token`, scope spreadsheets =
read + write), else the main Google sign-in (Settings → Google, read only). Editing needs the Sheets sign-in. The Google
account must be able to open the Sheet (shared with it).
"""
import asyncio
import hashlib
import io
import json
import re
import secrets
import zlib
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import anthropic
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import auth
import db
import google_ads_client as gads

router = APIRouter()
API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/userinfo.email"]
HK = ZoneInfo("Asia/Hong_Kong")
EVERY = 15 * 60              # seconds between reads
KEEP_ALL_DAYS = 30           # every version kept this long, then one per day
AI_MODEL = "claude-haiku-5-5"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sh_sheets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    gid         TEXT NOT NULL UNIQUE,              -- Google's spreadsheet id
    title       TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',          -- what the team uses it for (typed by an admin)
    added_by    TEXT,
    added_at    TEXT NOT NULL,
    last_check  TEXT,
    last_change TEXT,
    last_error  TEXT,
    ai_notes    TEXT NOT NULL DEFAULT '',          -- what the AI learned about it
    ai_heads    TEXT NOT NULL DEFAULT '',          -- the headers it learned from (relearn when they change)
    ai_at       TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sh_versions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id  INTEGER NOT NULL,
    tab       TEXT NOT NULL,
    at        TEXT NOT NULL,
    hash      TEXT NOT NULL,
    rows      INTEGER NOT NULL,
    cols      INTEGER NOT NULL,
    data      BLOB NOT NULL,                       -- zlib(json [[cell]])
    diff      TEXT NOT NULL DEFAULT '{}'           -- vs the version before: {added, removed, changed, sample}
);
CREATE INDEX IF NOT EXISTS sh_versions_tab ON sh_versions(sheet_id, tab, id);
CREATE TABLE IF NOT EXISTS sh_daily (
    sheet_id  INTEGER NOT NULL,
    day       TEXT NOT NULL,                       -- Hong Kong day
    stats     TEXT NOT NULL DEFAULT '{}',
    summary   TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    PRIMARY KEY (sheet_id, day)
);
CREATE TABLE IF NOT EXISTS sh_edits (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id  INTEGER NOT NULL,
    tab       TEXT NOT NULL,
    cell      TEXT NOT NULL,
    old       TEXT,
    new       TEXT,
    by        TEXT,
    at        TEXT NOT NULL
);
"""
_ready = {"ok": False}
_states: dict = {}           # Google sign-in state -> who started it
_locks: dict = {}


class SheetsError(RuntimeError):
    pass


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
    return u.get("name") or u.get("email") or "?"


def _level(request: Request, need: str):
    if not auth.at_least(_user(request), "sheets", need):
        raise HTTPException(403, {"work": "Editing needs Work or Manage on the Sheets desk.",
                                  "manage": "Only admins (Manage on Sheets) can do that."}.get(need, "Not allowed."))


def sheet_id_of(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", url_or_id or "")
    sid = m.group(1) if m else (url_or_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", sid):
        raise HTTPException(400, "That doesn't look like a Google Sheet link. Copy it from the address bar while the Sheet is open.")
    return sid


def col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def pack(rows: list) -> bytes:
    return zlib.compress(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def unpack(blob: bytes) -> list:
    return json.loads(zlib.decompress(blob).decode())


# ---------------------------------------------------------------- Google

def write_token() -> str:
    return db.get_setting("sheets_google_token") or ""


async def _access(write: bool = False) -> str:
    token = write_token()
    if not token and not write:
        auth_row = db.get_google_auth() or {}
        token = auth_row.get("refresh_token") or ""
    if not token:
        raise SheetsError("Sign in with Google on the Sheets desk first (the account that can open these Sheets)."
                          if write else "Connect Google first: Sheets desk → Sign in with Google.")
    try:
        return await gads._access_token(token)
    except gads.GoogleAdsError as e:
        raise SheetsError(f"Google sign-in stopped working: sign in again on the Sheets desk. ({str(e)[:120]})")


async def _call(method: str, url: str, access: str, **kw):
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.request(method, url, headers={"Authorization": f"Bearer {access}"}, **kw)
        except httpx.RequestError as e:
            raise SheetsError(f"Couldn't reach Google Sheets ({type(e).__name__}).")
    if r.status_code in (401, 403):
        raise SheetsError("Google won't let EcomVital open this Sheet. Share it with the Google account signed in on the "
                          "Sheets desk (or sign in again), and make sure the Google Sheets API is on.")
    if r.status_code == 404:
        raise SheetsError("Google can't find this Sheet (deleted, or the link is wrong).")
    if r.status_code >= 400:
        raise SheetsError(f"Google Sheets error {r.status_code}: {r.text[:200]}")
    return r.json()


async def read_sheet(gid: str) -> tuple:
    """(title, {tab: [[cell]]}) for every tab, as people see the values."""
    access = await _access()
    info = await _call("GET", f"{API}/{gid}", access, params={"fields": "properties.title,sheets.properties(title,sheetId,hidden)"})
    tabs = [s["properties"]["title"] for s in info.get("sheets", [])]
    out = {}
    for i in range(0, len(tabs), 20):
        part = tabs[i:i + 20]
        url = f"{API}/{gid}/values:batchGet?" + "&".join(f"ranges={quote(repr_range(t))}" for t in part)
        got = await _call("GET", url + "&valueRenderOption=FORMATTED_VALUE", access)
        for t, vr in zip(part, got.get("valueRanges", [])):
            rows = [[("" if c is None else str(c)) for c in r] for r in vr.get("values", [])]
            out[t] = rows
    return (info.get("properties") or {}).get("title") or "", out


def repr_range(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


# ---------------------------------------------------------------- backup + history

def _hash(rows: list) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()[:32]


def diff(old: list, new: list) -> dict:
    """Cell-by-cell difference between two versions of a tab (by position)."""
    if old is None:
        return {"first": True, "added": len(new), "removed": 0, "changed": 0, "sample": []}
    changed, sample = 0, []
    for r in range(min(len(old), len(new))):
        a, b = old[r], new[r]
        for c in range(max(len(a), len(b))):
            x = a[c] if c < len(a) else ""
            y = b[c] if c < len(b) else ""
            if x != y:
                changed += 1
                if len(sample) < 25:
                    sample.append([f"{col_name(c)}{r + 1}", x[:120], y[:120]])
    added = max(0, len(new) - len(old))
    added_rows = [[f"row {len(old) + i + 1}", " | ".join(c for c in row if c)[:200]] for i, row in enumerate(new[len(old):len(old) + 10])]
    return {"added": added, "removed": max(0, len(old) - len(new)), "changed": changed, "sample": sample, "added_rows": added_rows}


def _latest(con, sheet_id: int, tab: str):
    r = con.execute("SELECT * FROM sh_versions WHERE sheet_id = ? AND tab = ? ORDER BY id DESC LIMIT 1", (sheet_id, tab)).fetchone()
    return dict(r) if r else None


async def backup(sheet: dict) -> dict:
    """Read the Sheet now and keep a new version of every tab that changed."""
    ensure()
    lock = _locks.setdefault(sheet["id"], asyncio.Lock())
    async with lock:
        try:
            title, tabs = await read_sheet(sheet["gid"])
        except SheetsError as e:
            with db._conn() as con:
                con.execute("UPDATE sh_sheets SET last_check = ?, last_error = ? WHERE id = ?", (_now(), str(e)[:300], sheet["id"]))
            raise
        changed = []
        with db._conn() as con:
            for tab, rows in tabs.items():
                h = _hash(rows)
                prev = _latest(con, sheet["id"], tab)
                if prev and prev["hash"] == h:
                    continue
                d = diff(unpack(prev["data"]) if prev else None, rows)
                con.execute("INSERT INTO sh_versions (sheet_id, tab, at, hash, rows, cols, data, diff) VALUES (?,?,?,?,?,?,?,?)",
                            (sheet["id"], tab, _now(), h, len(rows), max((len(r) for r in rows), default=0), pack(rows), json.dumps(d)))
                changed.append(tab)
            con.execute("UPDATE sh_sheets SET title = ?, last_check = ?, last_error = NULL" + (", last_change = ?" if changed else "")
                        + " WHERE id = ?", (title or sheet["title"], _now(), *([_now()] if changed else []), sheet["id"]))
        if changed:
            asyncio.get_event_loop().create_task(learn(sheet["id"]))
        return {"changed": changed, "tabs": len(tabs)}


def prune():
    """Keep every version for KEEP_ALL_DAYS, then the last one of each day per tab."""
    cut = (datetime.now(timezone.utc) - timedelta(days=KEEP_ALL_DAYS)).isoformat()
    with db._conn() as con:
        old = con.execute("SELECT id, sheet_id, tab, substr(at, 1, 10) d FROM sh_versions WHERE at < ? ORDER BY id", (cut,)).fetchall()
        keep = {}
        for r in old:
            keep[(r["sheet_id"], r["tab"], r["d"])] = r["id"]
        drop = [r["id"] for r in old if keep[(r["sheet_id"], r["tab"], r["d"])] != r["id"]]
        for i in range(0, len(drop), 500):
            part = drop[i:i + 500]
            con.execute(f"DELETE FROM sh_versions WHERE id IN ({','.join('?' * len(part))})", part)


# ---------------------------------------------------------------- AI: learn the Sheet, daily log

def _key() -> str:
    return db.get_setting("anthropic_api_key") or ""


async def _ask(system: str, text: str, max_tokens: int = 1200) -> str:
    client = anthropic.AsyncAnthropic(api_key=_key(), max_retries=2, timeout=120)
    resp = await client.messages.create(model=AI_MODEL, max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": text}])
    return "".join(b.text for b in resp.content if b.type == "text").strip()


LEARN_SYSTEM = """You look at a Google Sheet an e-commerce team (Shopify dropshipping stores) works in every day and explain it
for colleagues and for an AI assistant that will answer questions from it. For each tab: what it is for, what one row
means, the important columns (by header) and what they hold, and how the team seems to use it. Plain English, short
bullets, under 250 words in total. Only say what the headers and sample rows show; never guess numbers."""


async def learn(sheet_id: int, force: bool = False):
    """Teach the AI what the Sheet is (redone when the headers change)."""
    if not _key():
        return
    with db._conn() as con:
        s = dict(con.execute("SELECT * FROM sh_sheets WHERE id = ?", (sheet_id,)).fetchone() or {})
        tabs = {r["tab"]: unpack(r["data"]) for r in (dict(x) for x in con.execute(
            "SELECT v.tab, v.data FROM sh_versions v JOIN (SELECT tab, MAX(id) m FROM sh_versions WHERE sheet_id = ? GROUP BY tab) t"
            " ON v.id = t.m", (sheet_id,)))}
    if not s or not tabs:
        return
    heads = json.dumps({t: (rows[0] if rows else []) for t, rows in tabs.items()}, ensure_ascii=False)
    if not force and s.get("ai_heads") == heads:
        return
    sample = "\n\n".join(f"TAB “{t}” ({len(rows)} rows)\n" + "\n".join(" | ".join(r)[:400] for r in rows[:8]) for t, rows in list(tabs.items())[:20])
    try:
        notes = await _ask(LEARN_SYSTEM, f"Sheet: {s['title']}\nTeam's note: {s.get('note') or '(none)'}\n\n{sample[:30000]}")
    except Exception as e:
        print(f"Sheets learn failed: {str(e)[:200]}", flush=True)
        return
    with db._conn() as con:
        con.execute("UPDATE sh_sheets SET ai_notes = ?, ai_heads = ?, ai_at = ? WHERE id = ?", (notes, heads, _now(), sheet_id))


DAY_SYSTEM = """You write the daily log of a Google Sheet an e-commerce team works in. You get what changed in each tab that day
(rows added with their content, cells changed old → new). Write 3-6 short bullets of what was done that day in plain English
(e.g. "Added 14 orders for CUTEHOME", "Updated tracking numbers for 6 orders", "Changed the cost of X from 12 to 13").
Use only what you are given; no guesses, no advice."""


def _hk_range(day: str) -> tuple:
    d = date.fromisoformat(day)
    start = datetime.combine(d, datetime.min.time(), HK).astimezone(timezone.utc).isoformat()
    end = datetime.combine(d + timedelta(days=1), datetime.min.time(), HK).astimezone(timezone.utc).isoformat()
    return start, end


async def daily(sheet_id: int, day: str, with_ai: bool = True) -> dict:
    """The day's log for one Sheet (Hong Kong day): numbers per tab, plus the AI summary."""
    start, end = _hk_range(day)
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT tab, at, diff FROM sh_versions WHERE sheet_id = ? AND at >= ? AND at < ?"
                                             " ORDER BY id", (sheet_id, start, end))]
        edits = con.execute("SELECT COUNT(*) FROM sh_edits WHERE sheet_id = ? AND at >= ? AND at < ?", (sheet_id, start, end)).fetchone()[0]
    stats, lines = {}, []
    for r in rows:
        d = json.loads(r["diff"] or "{}")
        if d.get("first"):
            continue
        s = stats.setdefault(r["tab"], {"versions": 0, "added": 0, "removed": 0, "changed": 0})
        s["versions"] += 1
        for k in ("added", "removed", "changed"):
            s[k] += d.get(k, 0)
        for a in d.get("added_rows") or []:
            lines.append(f"[{r['tab']}] added {a[0]}: {a[1]}")
        for c in (d.get("sample") or [])[:10]:
            lines.append(f"[{r['tab']}] {c[0]}: “{c[1]}” → “{c[2]}”")
    summary = ""
    if with_ai and lines and _key():
        try:
            summary = await _ask(DAY_SYSTEM, f"Day {day}\n" + "\n".join(lines[:300]), 600)
        except Exception as e:
            summary = f"(AI summary failed: {str(e)[:120]})"
    out = {"day": day, "stats": stats, "edits_in_app": edits, "summary": summary,
           "total": {k: sum(s[k] for s in stats.values()) for k in ("added", "removed", "changed")}}
    with db._conn() as con:
        con.execute("INSERT OR REPLACE INTO sh_daily (sheet_id, day, stats, summary, at) VALUES (?, ?, ?, ?, ?)",
                    (sheet_id, day, json.dumps({"tabs": stats, "edits_in_app": edits, "total": out["total"]}), summary, _now()))
    return out


async def loop():
    """Every 15 minutes: back up every Sheet. After Hong Kong midnight: yesterday's daily log. Daily: prune old versions."""
    await asyncio.sleep(120)
    logged = None
    while True:
        try:
            ensure()
            with db._conn() as con:
                sheets = [dict(r) for r in con.execute("SELECT * FROM sh_sheets WHERE active = 1")]
            for s in sheets:
                try:
                    await backup(s)
                except Exception as e:
                    print(f"Sheets backup failed for {s.get('title') or s['gid']}: {str(e)[:200]}", flush=True)
            yesterday = (datetime.now(HK).date() - timedelta(days=1)).isoformat()
            if logged != yesterday:
                with db._conn() as con:
                    have = {r[0] for r in con.execute("SELECT sheet_id FROM sh_daily WHERE day = ?", (yesterday,))}
                for s in sheets:
                    if s["id"] not in have:
                        try:
                            await daily(s["id"], yesterday)
                        except Exception as e:
                            print(f"Sheets daily log failed: {str(e)[:200]}", flush=True)
                await asyncio.to_thread(prune)
                logged = yesterday
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Sheets loop failed: {str(e)[:200]}", flush=True)
        await asyncio.sleep(EVERY)


# ---------------------------------------------------------------- routes

def _sheet(con, sid: int) -> dict:
    r = con.execute("SELECT * FROM sh_sheets WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "That Sheet isn't connected any more.")
    return dict(r)


@router.get("/api/sheets")
def api_list(request: Request):
    ensure()
    with db._conn() as con:
        sheets = [dict(r) for r in con.execute("SELECT * FROM sh_sheets ORDER BY title COLLATE NOCASE")]
        tabs = {}
        for r in con.execute("SELECT sheet_id, tab, COUNT(*) n, MAX(at) last, MAX(rows) FROM sh_versions GROUP BY sheet_id, tab"):
            tabs.setdefault(r[0], []).append({"tab": r[1], "versions": r[2], "last": r[3]})
    return {"sheets": [{**{k: s[k] for k in ("id", "gid", "title", "note", "added_by", "added_at", "last_check", "last_change",
                                             "last_error", "ai_notes", "ai_at", "active")},
                        "url": f"https://docs.google.com/spreadsheets/d/{s['gid']}/edit", "tabs": tabs.get(s["id"], [])}
                       for s in sheets],
            "google": {"write": bool(write_token()), "email": db.get_setting("sheets_google_email") or "",
                       "read": bool(write_token() or (db.get_google_auth() or {}).get("refresh_token"))},
            "level": auth.level(_user(request), "sheets"), "ai": bool(_key())}


@router.post("/api/sheets")
async def api_add(payload: dict, request: Request):
    _level(request, "manage")
    ensure()
    gid = sheet_id_of(payload.get("url") or "")
    with db._conn() as con:
        if con.execute("SELECT 1 FROM sh_sheets WHERE gid = ?", (gid,)).fetchone():
            raise HTTPException(400, "This Sheet is already connected.")
    try:
        title, tabs = await read_sheet(gid)
    except SheetsError as e:
        raise HTTPException(400, str(e))
    with db._conn() as con:
        sid = con.execute("INSERT INTO sh_sheets (gid, title, note, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
                          (gid, title, str(payload.get("note") or "")[:500], _who(request), _now())).lastrowid
        s = _sheet(con, sid)
    try:
        await backup(s)
    except SheetsError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": sid, "title": title, "tabs": len(tabs)}


@router.put("/api/sheets/{sid}")
def api_update(sid: int, payload: dict, request: Request):
    _level(request, "manage")
    ensure()
    with db._conn() as con:
        _sheet(con, sid)
        if "note" in payload:
            con.execute("UPDATE sh_sheets SET note = ? WHERE id = ?", (str(payload["note"] or "")[:500], sid))
        if "active" in payload:
            con.execute("UPDATE sh_sheets SET active = ? WHERE id = ?", (1 if payload["active"] else 0, sid))
    return {"ok": True}


@router.delete("/api/sheets/{sid}")
def api_remove(sid: int, request: Request, keep: int = 1):
    """Disconnect a Sheet. keep=1 (default) keeps its backups (paused); keep=0 deletes them too."""
    _level(request, "manage")
    ensure()
    with db._conn() as con:
        _sheet(con, sid)
        if keep:
            con.execute("UPDATE sh_sheets SET active = 0 WHERE id = ?", (sid,))
        else:
            for t in ("sh_versions", "sh_daily", "sh_edits"):
                con.execute(f"DELETE FROM {t} WHERE sheet_id = ?", (sid,))
            con.execute("DELETE FROM sh_sheets WHERE id = ?", (sid,))
    return {"ok": True}


@router.post("/api/sheets/{sid}/backup")
async def api_backup_now(sid: int, request: Request):
    _level(request, "work")
    ensure()
    with db._conn() as con:
        s = _sheet(con, sid)
    try:
        return {"ok": True, **(await backup(s))}
    except SheetsError as e:
        raise HTTPException(400, str(e))


@router.get("/api/sheets/{sid}/tab")
def api_tab(sid: int, tab: str, version: int = 0, q: str = "", offset: int = 0, limit: int = 300):
    """A tab's latest backup (or one version): header + rows (searchable), with the cells that changed vs the version before."""
    ensure()
    with db._conn() as con:
        s = _sheet(con, sid)
        v = con.execute("SELECT * FROM sh_versions WHERE id = ? AND sheet_id = ?", (version, sid)).fetchone() if version \
            else con.execute("SELECT * FROM sh_versions WHERE sheet_id = ? AND tab = ? ORDER BY id DESC LIMIT 1", (sid, tab)).fetchone()
        if not v:
            raise HTTPException(404, "No backup of this tab yet.")
        v = dict(v)
        prev = con.execute("SELECT data FROM sh_versions WHERE sheet_id = ? AND tab = ? AND id < ? ORDER BY id DESC LIMIT 1",
                           (sid, v["tab"], v["id"])).fetchone()
        latest = con.execute("SELECT MAX(id) FROM sh_versions WHERE sheet_id = ? AND tab = ?", (sid, v["tab"])).fetchone()[0]
    rows = unpack(v["data"])
    old = unpack(prev["data"]) if prev else None
    width = max((len(r) for r in rows), default=0)
    idx = list(range(1, len(rows)))
    words = [w for w in (q or "").lower().split() if w]
    if words:
        idx = [i for i in idx if all(w in " ".join(rows[i]).lower() for w in words)]
    page = idx[offset:offset + min(limit, 1000)]
    changed = []
    if old is not None:
        for i in [0] + page:
            a = old[i] if i < len(old) else None
            for c in range(width):
                y = rows[i][c] if c < len(rows[i]) else ""
                x = (a[c] if c < len(a) else "") if a is not None else None
                if x is None or x != y:
                    changed.append(f"{i}:{c}")
    return {"sheet": s["title"], "tab": v["tab"], "version": v["id"], "latest": v["id"] == latest, "at": v["at"],
            "cols": [col_name(c) for c in range(width)], "header": (rows[0] if rows else []) + [""] * (width - len(rows[0] if rows else [])),
            "rows": [{"n": i + 1, "cells": rows[i] + [""] * (width - len(rows[i]))} for i in page],
            "total": len(idx), "offset": offset, "changed": changed,
            "diff": json.loads(v["diff"] or "{}")}


@router.get("/api/sheets/{sid}/history")
def api_history(sid: int, tab: str = "", limit: int = 200):
    ensure()
    with db._conn() as con:
        _sheet(con, sid)
        rows = [dict(r) for r in con.execute(
            "SELECT id, tab, at, rows, cols, diff FROM sh_versions WHERE sheet_id = ?" + (" AND tab = ?" if tab else "")
            + " ORDER BY id DESC LIMIT ?", (sid, *([tab] if tab else []), min(limit, 1000)))]
        edits = [dict(r) for r in con.execute("SELECT * FROM sh_edits WHERE sheet_id = ?" + (" AND tab = ?" if tab else "")
                                              + " ORDER BY id DESC LIMIT 100", (sid, *([tab] if tab else [])))]
    for r in rows:
        d = json.loads(r.pop("diff") or "{}")
        r.update(first=bool(d.get("first")), added=d.get("added", 0), removed=d.get("removed", 0), changed=d.get("changed", 0),
                 sample=(d.get("sample") or [])[:5])
    return {"versions": rows, "edits": edits}


@router.get("/api/sheets/{sid}/download")
def api_download(sid: int, version: int = 0, fmt: str = "xlsx"):
    """One version of a tab (or, without version, the latest backup of every tab) as Excel / CSV."""
    import csv
    ensure()
    with db._conn() as con:
        s = _sheet(con, sid)
        if version:
            vs = [dict(r) for r in con.execute("SELECT * FROM sh_versions WHERE id = ? AND sheet_id = ?", (version, sid))]
        else:
            vs = [dict(r) for r in con.execute(
                "SELECT v.* FROM sh_versions v JOIN (SELECT tab, MAX(id) m FROM sh_versions WHERE sheet_id = ? GROUP BY tab) t"
                " ON v.id = t.m ORDER BY v.tab", (sid,))]
    if not vs:
        raise HTTPException(404, "No backup yet.")
    base = re.sub(r"[^\w -]+", "", s["title"] or "sheet").strip()[:60] or "sheet"
    stamp = vs[0]["at"][:16].replace(":", "").replace("T", " ") if version else datetime.now(HK).strftime("%Y-%m-%d %H%M")
    if fmt == "csv" and len(vs) == 1:
        buf = io.StringIO()
        csv.writer(buf).writerows(unpack(vs[0]["data"]))
        name = f"{base} - {vs[0]['tab']} - {stamp}.csv"
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{quote(name)}"'})
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for v in vs:
        ws = wb.create_sheet((re.sub(r"[\[\]:*?/\\]", " ", v["tab"]) or "Sheet")[:31])
        for row in unpack(v["data"]):
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    name = f"{base}{' - ' + vs[0]['tab'] if version else ''} - backup {stamp}.xlsx"
    return Response(out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/api/sheets/{sid}/cell")
async def api_cell(sid: int, payload: dict, request: Request):
    """Change one cell in Google (Work / Manage). Refused when the cell no longer holds what the person saw."""
    _level(request, "work")
    ensure()
    tab, row, col = str(payload.get("tab") or ""), int(payload.get("row") or 0), int(payload.get("col") or -1)
    if not tab or row < 1 or col < 0:
        raise HTTPException(400, "Which cell?")
    cell = f"{col_name(col)}{row}"
    rng = quote(f"{repr_range(tab)}!{cell}")
    new = str(payload.get("value") if payload.get("value") is not None else "")[:50000]
    with db._conn() as con:
        s = _sheet(con, sid)
    try:
        access = await _access(write=True)
        now_ = await _call("GET", f"{API}/{s['gid']}/values/{rng}", access, params={"valueRenderOption": "FORMATTED_VALUE"})
        current = ((now_.get("values") or [[""]])[0] or [""])[0] if now_.get("values") else ""
        if "old" in payload and str(current) != str(payload.get("old") or ""):
            raise HTTPException(409, f"Someone changed {cell} in Google since this copy was taken (it now says “{str(current)[:80]}”). "
                                     "Press Refresh and try again.")
        await _call("PUT", f"{API}/{s['gid']}/values/{rng}", access, params={"valueInputOption": "USER_ENTERED"},
                    json={"range": f"{repr_range(tab)}!{cell}", "values": [[new]]})
    except SheetsError as e:
        raise HTTPException(400, str(e))
    with db._conn() as con:
        con.execute("INSERT INTO sh_edits (sheet_id, tab, cell, old, new, by, at) VALUES (?,?,?,?,?,?,?)",
                    (sid, tab, cell, str(current)[:5000], new[:5000], _who(request), _now()))
    try:
        await backup(s)
    except SheetsError:
        pass
    return {"ok": True, "cell": cell}


@router.post("/api/sheets/{sid}/row")
async def api_row(sid: int, payload: dict, request: Request):
    """Add a row at the end of a tab (Work / Manage)."""
    _level(request, "work")
    ensure()
    tab = str(payload.get("tab") or "")
    cells = [str(c if c is not None else "")[:5000] for c in (payload.get("cells") or [])][:200]
    if not tab or not any(c.strip() for c in cells):
        raise HTTPException(400, "Type at least one value for the new row.")
    with db._conn() as con:
        s = _sheet(con, sid)
    try:
        access = await _access(write=True)
        got = await _call("POST", f"{API}/{s['gid']}/values/{quote(repr_range(tab))}:append", access,
                          params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}, json={"values": [cells]})
    except SheetsError as e:
        raise HTTPException(400, str(e))
    where = ((got.get("updates") or {}).get("updatedRange") or "").split("!")[-1]
    with db._conn() as con:
        con.execute("INSERT INTO sh_edits (sheet_id, tab, cell, old, new, by, at) VALUES (?,?,?,?,?,?,?)",
                    (sid, tab, where or "new row", "", " | ".join(cells)[:5000], _who(request), _now()))
    try:
        await backup(s)
    except SheetsError:
        pass
    return {"ok": True, "range": where}


@router.get("/api/sheets/{sid}/daily")
def api_daily_list(sid: int, limit: int = 60):
    ensure()
    with db._conn() as con:
        _sheet(con, sid)
        rows = [dict(r) for r in con.execute("SELECT * FROM sh_daily WHERE sheet_id = ? ORDER BY day DESC LIMIT ?", (sid, limit))]
    for r in rows:
        r["stats"] = json.loads(r["stats"] or "{}")
    return {"days": rows, "today": datetime.now(HK).date().isoformat()}


@router.post("/api/sheets/{sid}/daily")
async def api_daily_now(sid: int, payload: dict, request: Request):
    """Write (or rewrite) a day's log now, e.g. today so far."""
    _level(request, "work")
    ensure()
    day = str(payload.get("day") or datetime.now(HK).date().isoformat())
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Pick a day.")
    with db._conn() as con:
        _sheet(con, sid)
    return await daily(sid, day)


@router.post("/api/sheets/{sid}/learn")
async def api_learn(sid: int, request: Request):
    _level(request, "work")
    ensure()
    if not _key():
        raise HTTPException(400, "Add the Anthropic key first (Settings → AI).")
    await learn(sid, force=True)
    with db._conn() as con:
        return {"ok": True, "ai_notes": _sheet(con, sid)["ai_notes"]}


# Google sign-in for the Sheets desk (read + write), through the app's shared Google callback

def start_signin(request: Request, redirect_uri: str) -> str:
    _level(request, "manage")
    cid = gads._cfg()[0]
    if not cid:
        raise HTTPException(400, "The Google sign-in keys aren't set up yet (Settings → Google).")
    state = secrets.token_urlsafe(24)
    _states[state] = _who(request)
    return gads.AUTH_URL + "?" + urlencode({"client_id": cid, "redirect_uri": redirect_uri, "response_type": "code",
                                            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
                                            "include_granted_scopes": "true", "state": state})


async def finish_signin(state: str, code: str, redirect_uri: str) -> str:
    _states.pop(state, None)
    token, email = await gads.exchange_code(code, redirect_uri)
    db.set_setting("sheets_google_token", token)
    db.set_setting("sheets_google_email", email or "")
    return email or "your Google account"


# ---------------------------------------------------------------- the Ask assistant / connector

def tool(args: dict) -> dict:
    """List the Sheets (with what the AI learned), or read rows of one tab from the latest backup (search words)."""
    ensure()
    with db._conn() as con:
        sheets = [dict(r) for r in con.execute("SELECT id, title, note, ai_notes, last_change FROM sh_sheets WHERE active = 1")]
        if not args.get("sheet"):
            tabs = {}
            for r in con.execute("SELECT sheet_id, tab, MAX(rows) FROM sh_versions GROUP BY sheet_id, tab"):
                tabs.setdefault(r[0], []).append({"tab": r[1], "rows": r[2]})
            return {"sheets": [{**s, "tabs": tabs.get(s["id"], [])} for s in sheets]}
        key = str(args["sheet"]).lower()
        s = next((x for x in sheets if str(x["id"]) == key or key in (x["title"] or "").lower()), None)
        if not s:
            return {"error": "No connected Sheet by that name or id."}
        tab = str(args.get("tab") or "")
        q = "SELECT tab, data, at FROM sh_versions WHERE sheet_id = ?" + (" AND tab = ?" if tab else "") + " ORDER BY id DESC LIMIT 1"
        v = con.execute(q, (s["id"], *([tab] if tab else []))).fetchone()
    if not v:
        return {"error": "No backup of that tab yet."}
    rows = unpack(v["data"])
    words = [w for w in str(args.get("contains") or "").lower().split() if w]
    body = [r for r in rows[1:] if all(w in " ".join(r).lower() for w in words)] if words else rows[1:]
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return {"sheet": s["title"], "tab": v["tab"], "backup_at": v["at"], "header": rows[0] if rows else [],
            "matching_rows": len(body), "rows": body[-limit:] if not words else body[:limit],
            "note": "Latest backup (EcomVital reads every 15 minutes)."}
