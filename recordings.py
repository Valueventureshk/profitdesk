"""Session recordings (Apps → Session Recordings): what shoppers did on a store, to replay.

The storefront script (static/recorder.js, rrweb) sends chunks of events through the store's app
proxy. Each chunk is kept gzipped on the volume, next to the database:

    <DB dir>/recordings/<session id>/<seq>.json.gz

and the session's summary (store, device, pages, clicks, product / cart pages seen, length) in the
rec_sessions table. Old sessions are deleted after the store's "keep" days, and the oldest ones
first whenever all recordings together pass the size cap.
"""
import gzip
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

import db

DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "recordings")
DEFAULTS = {"on": False, "sample": 30, "per_day": 200, "keep_days": 14}
TOTAL_CAP = int(os.getenv("RECORDINGS_CAP_MB", "3000")) * 1024 * 1024
MAX_CHUNKS = 400
_SID = re.compile(r"^[a-z0-9]{12,40}$")


def config(store_id: int) -> dict:
    try:
        saved = json.loads(db.get_setting(f"rec:{store_id}") or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_config(store_id: int, cfg: dict) -> dict:
    out = config(store_id)
    if "on" in cfg:
        out["on"] = bool(cfg["on"])
    for k, lo, hi in (("sample", 1, 100), ("per_day", 10, 5000), ("keep_days", 1, 90)):
        if k in cfg:
            out[k] = max(lo, min(hi, int(float(cfg[k]))))
    db.set_setting(f"rec:{store_id}", json.dumps(out))
    return out


def started_today(store_id: int) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with db._conn() as con:
        return con.execute("SELECT COUNT(*) FROM rec_sessions WHERE store_id = ? AND started >= ?",
                           (store_id, since)).fetchone()[0]


def valid_sid(sid: str) -> bool:
    return bool(_SID.match(sid or ""))


def save_chunk(store_id: int, sid: str, seq: int, payload: dict) -> bool:
    """Keep one chunk and update the session summary. False when the session is full."""
    events = payload.get("events") or []
    meta = payload.get("meta") or {}
    if not isinstance(events, list) or not events:
        return True
    with db._conn() as con:
        row = con.execute("SELECT * FROM rec_sessions WHERE sid = ?", (sid,)).fetchone()
        if row and (row["store_id"] != store_id or row["chunks"] >= MAX_CHUNKS):
            return False
    os.makedirs(os.path.join(DIR, sid), exist_ok=True)
    raw = json.dumps(events, separators=(",", ":")).encode()
    with gzip.open(os.path.join(DIR, sid, f"{int(seq):05d}.json.gz"), "wb", compresslevel=6) as f:
        f.write(raw)
    stamps = [e.get("timestamp") for e in events if isinstance(e, dict) and isinstance(e.get("timestamp"), (int, float))]
    url = str(meta.get("u") or "")[:500]
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0][:200] or "/"
    clicks = sum(1 for e in events if isinstance(e, dict) and e.get("type") == 3
                 and (e.get("data") or {}).get("source") == 2 and (e.get("data") or {}).get("type") == 2)
    now = datetime.now(timezone.utc).isoformat()
    with db._conn() as con:
        row = con.execute("SELECT * FROM rec_sessions WHERE sid = ?", (sid,)).fetchone()
        if not row:
            con.execute(
                "INSERT INTO rec_sessions (sid, store_id, started, last_at, first_ts, last_ts, chunks, bytes, mobile,"
                " width, landing, last_url, referrer, pages, clicks, product, cart, tz, lang)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, store_id, now, now, min(stamps) if stamps else None, max(stamps) if stamps else None, 1, len(raw),
                 int(bool(meta.get("m"))), int(meta.get("w") or 0), path, path, str(meta.get("r") or "")[:300],
                 json.dumps([path]), clicks, int("/products/" in path), int(path.startswith("/cart")),
                 str(meta.get("tz") or "")[:60], str(meta.get("l") or "")[:20]))
        else:
            pages = json.loads(row["pages"] or "[]")
            if path and (not pages or pages[-1] != path) and len(pages) < 60:
                pages.append(path)
            con.execute(
                "UPDATE rec_sessions SET last_at = ?, first_ts = COALESCE(MIN(first_ts, ?), first_ts, ?),"
                " last_ts = MAX(COALESCE(last_ts, 0), ?), chunks = chunks + 1, bytes = bytes + ?, last_url = ?,"
                " pages = ?, clicks = clicks + ?, product = MAX(product, ?), cart = MAX(cart, ?) WHERE sid = ?",
                (now, min(stamps) if stamps else None, min(stamps) if stamps else None, max(stamps) if stamps else 0,
                 len(raw), path, json.dumps(pages), clicks, int("/products/" in path), int(path.startswith("/cart")), sid))
    return True


def events(sid: str) -> list:
    """Every event of a session, in order (chunks that never arrived are simply missing)."""
    folder = os.path.join(DIR, sid)
    out = []
    if not valid_sid(sid) or not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        try:
            with gzip.open(os.path.join(folder, name), "rb") as f:
                out += json.loads(f.read())
        except (OSError, ValueError):
            continue
    out.sort(key=lambda e: e.get("timestamp") or 0)
    return out


def delete(sid: str):
    if valid_sid(sid):
        shutil.rmtree(os.path.join(DIR, sid), ignore_errors=True)
        with db._conn() as con:
            con.execute("DELETE FROM rec_sessions WHERE sid = ?", (sid,))


def cleanup(store_ids: list):
    """Drop sessions past their store's keep days, then the oldest until under the size cap."""
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT sid, store_id, last_at, bytes FROM rec_sessions ORDER BY last_at")]
    keep = {sid: config(sid)["keep_days"] for sid in store_ids}
    now = datetime.now(timezone.utc)
    left = []
    for r in rows:
        days = keep.get(r["store_id"], DEFAULTS["keep_days"])
        if r["last_at"] < (now - timedelta(days=days)).isoformat():
            delete(r["sid"])
        else:
            left.append(r)
    total = sum(r["bytes"] or 0 for r in left)
    for r in left:
        if total <= TOTAL_CAP:
            break
        delete(r["sid"])
        total -= r["bytes"] or 0
