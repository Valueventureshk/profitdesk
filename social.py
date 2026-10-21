"""Inbox → Comments & Messages: Facebook Page comments (posts and ads) and Messenger chats.

    pages      every Page a connected Meta token can manage (`me/accounts`, page access tokens kept in
               `fb_pages.page_token`, never sent to the browser), plus Pages the stores' ad accounts
               promote that no token manages yet ("not given to the System User"). Each Page → one store
               (several Pages per store), guessed from the ad account's promote pages, changeable (Manage).
    sync       `loop()` every POLL seconds per Page: the newest Page posts (`/feed`) and ads posts
               (`/ads_posts`, dark ads included) with their comment counts; posts whose count or
               updated_time changed get their comments re-read (newest first). First sync reads 90 days.
               Messenger: `/conversations`, messages of changed chats. Webhook `/api/meta/webhook` only
               wakes the poll for that Page (its body is never trusted as data).
    auto-hide  on by default per Page. A new comment (created after auto-hide was switched on) whose text
               matches the hide-words list (setting `social_words`) is hidden at once (POST is_hidden).
               Every hide/unhide is logged in `fb_hide_log` (who, word). Unhidden by a person = never
               auto-hidden again (`keep`).
    replies    manual only: reply as the Page under the comment, or a private Messenger reply; Messenger
               replies inside Meta's 24 h window (messaging_type RESPONSE).
    learn      words that keep coming back in comments the team hid on Facebook, as suggestions to add.

Permissions (on the System User token): pages_read_engagement + pages_read_user_content = read comments,
pages_manage_engagement = hide / reply, pages_messaging = Messenger, pages_manage_metadata = webhooks.
Settings: `social_on` ('0' = off, default on), `social_words` (JSON list), `social_learn` (last result),
`social_webhook_token` (verify token for Meta's webhook setup).
"""
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

import auth
import db
import meta_ads_client as meta

router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS fb_pages (
    page_id      TEXT PRIMARY KEY,
    name         TEXT,
    conn_id      INTEGER,            -- meta_connections id whose token manages it (NULL = none yet)
    page_token   TEXT,               -- Page access token (secret, never sent to the browser)
    tasks        TEXT,               -- JSON: what the System User may do on the Page (MODERATE, MESSAGING...)
    perms        TEXT,               -- JSON: permissions granted on that token
    picture      TEXT,
    store_id     INTEGER,
    store_by     TEXT,               -- 'auto' (guessed) or the person who picked it
    promoted_by  TEXT,               -- JSON [store ids] whose ad account can advertise as this Page
    sync_on      INTEGER NOT NULL DEFAULT 1,
    autohide     INTEGER NOT NULL DEFAULT 1,
    autohide_from TEXT,              -- only comments created after this are auto-hidden
    backfilled_at TEXT,
    synced_at    TEXT,
    msgs_at      TEXT,
    subscribed   TEXT,               -- webhook subscription result
    error        TEXT,
    msg_error    TEXT,
    cooldown_until TEXT,
    full_at      TEXT,               -- last full read of the ads posts list
    seen_at      TEXT,
    gone         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fb_posts (
    post_id      TEXT PRIMARY KEY,
    page_id      TEXT NOT NULL,
    kind         TEXT,               -- post | ad
    message      TEXT,
    permalink    TEXT,
    picture      TEXT,
    created_time TEXT,
    updated_time TEXT,
    comment_count INTEGER,
    checked_at   TEXT
);
CREATE INDEX IF NOT EXISTS fb_posts_page ON fb_posts(page_id);
CREATE TABLE IF NOT EXISTS fb_comments (
    id           TEXT PRIMARY KEY,
    page_id      TEXT NOT NULL,
    post_id      TEXT,
    parent_id    TEXT,
    from_id      TEXT,
    from_name    TEXT,
    message      TEXT,
    attachment   TEXT,               -- picture/sticker link, if any
    created_time TEXT,
    is_hidden    INTEGER NOT NULL DEFAULT 0,
    can_hide     INTEGER,
    can_reply_privately INTEGER,
    permalink    TEXT,
    by_page      INTEGER NOT NULL DEFAULT 0,
    matched      TEXT,               -- hide word it matches (NULL = none)
    keep         INTEGER NOT NULL DEFAULT 0,   -- a person unhid it: never auto-hide again
    hide_tries   INTEGER NOT NULL DEFAULT 0,
    replied_by   TEXT,
    replied_at   TEXT,
    private_replied INTEGER NOT NULL DEFAULT 0,
    done_by      TEXT,
    done_at      TEXT,
    seen_at      TEXT
);
CREATE INDEX IF NOT EXISTS fb_comments_page ON fb_comments(page_id, created_time);
CREATE INDEX IF NOT EXISTS fb_comments_parent ON fb_comments(parent_id);
CREATE INDEX IF NOT EXISTS fb_comments_post ON fb_comments(post_id);
CREATE TABLE IF NOT EXISTS fb_hide_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT NOT NULL,
    page_id    TEXT,
    action     TEXT NOT NULL,        -- hide | unhide
    by         TEXT NOT NULL,        -- 'auto' or the person
    reason     TEXT,                 -- the word matched, or a person's note
    ok         INTEGER NOT NULL,
    error      TEXT,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fb_hide_log_c ON fb_hide_log(comment_id);
CREATE TABLE IF NOT EXISTS fb_convs (
    id           TEXT PRIMARY KEY,
    page_id      TEXT NOT NULL,
    user_id      TEXT,               -- the customer's Page-scoped id (PSID)
    user_name    TEXT,
    snippet      TEXT,
    updated_time TEXT,
    unread       INTEGER,
    last_customer_at TEXT,
    last_page_at TEXT,
    done_by      TEXT,
    done_at      TEXT,
    synced_at    TEXT
);
CREATE INDEX IF NOT EXISTS fb_convs_page ON fb_convs(page_id, updated_time);
CREATE TABLE IF NOT EXISTS fb_msgs (
    id           TEXT PRIMARY KEY,
    conv_id      TEXT NOT NULL,
    page_id      TEXT,
    from_id      TEXT,
    from_name    TEXT,
    by_page      INTEGER NOT NULL DEFAULT 0,
    text         TEXT,
    attachments  TEXT,               -- JSON [{type, url, name}]
    created_time TEXT,
    sent_by      TEXT                -- the EcomVital person who sent it from here
);
CREATE INDEX IF NOT EXISTS fb_msgs_conv ON fb_msgs(conv_id, created_time);
"""
_ready = {"ok": False}

POLL = 45                 # seconds between light syncs
PAGES_EVERY = 15 * 60     # re-read which Pages each token manages
PROMOTE_EVERY = 60 * 60   # re-read which Pages each store's ad account promotes
FULL_ADS_EVERY = 10 * 60  # whole ads-posts list (light syncs read its first page only)
BACKFILL_DAYS = 90
MAX_COMMENTS_PER_POST = 1500
MAX_HIDE_TRIES = 3

NEED = {
    "read": ("pages_read_engagement", "pages_read_user_content"),
    "moderate": ("pages_manage_engagement",),
    "messaging": ("pages_messaging",),
    "webhook": ("pages_manage_metadata",),
}
FEATURE_NAME = {"read": "read comments", "moderate": "hide and reply", "messaging": "Messenger",
                "webhook": "instant updates"}

# Starter list. Left out on purpose: "wish" ("I wish I'd bought two"), "cheap*" ("cheaper than in store"),
# "scam*" (scampi): happy customers write those too.
DEFAULT_WORDS = [
    "scam", "scams", "scammer*", "scammed", "scammy", "rip off", "cheap", "cheapo", "cheaply made", "cheat", "cheated",
    "cheater*", "cheating", "fraud*", "fake", "temu", "shein", "aliexpress", "ali express",
    "wish.com", "ebay", "amazon", "alibaba", "dhgate", "china", "chinese", "junk", "dropship*", "knock off",
    "rubbish", "garbage", "waste of money", "don't buy", "do not buy", "never arrived", "never received",
    "thief", "thieves", "stolen", "beware",
]

_state = {"pages_at": 0.0, "promote_at": 0.0, "running": False, "last": "", "last_error": "",
          "wake": None, "due": set(), "conn_errors": {}, "learn": {"running": False, "step": ""}}


def ensure():
    if not _ready["ok"]:
        with db._conn() as con:
            con.executescript(SCHEMA)
        _ready["ok"] = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(s) -> str:
    """Meta's '2026-10-21T03:04:05+0000' → ISO UTC '2026-10-21T03:04:05+00:00'."""
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return str(s)


def is_on() -> bool:
    return (db.get_setting("social_on") or "1") != "0"


def _who(request: Request) -> str:
    u = getattr(request.state, "user", None) or {}
    return u.get("name") or u.get("email") or "someone"


def _level(request: Request, need: str):
    if not auth.at_least(getattr(request.state, "user", None) or {}, "inbox", need):
        raise HTTPException(403, "That needs Manage access to the Inbox desk." if need == "manage"
                            else "Your access to the Inbox is view-only.")


# ---------------------------------------------------------------- hide words

def words() -> list:
    raw = db.get_setting("social_words")
    if raw is None:
        return list(DEFAULT_WORDS)
    try:
        got = json.loads(raw)
        return [str(w) for w in got if str(w).strip()]
    except ValueError:
        return list(DEFAULT_WORDS)


_LEET = str.maketrans({"@": "a", "0": "o", "$": "s", "3": "e", "1": "i", "!": "i", "5": "s", "4": "a"})


def _norm(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    return t.replace("'", "")


_compiled = {"key": None, "pats": []}


def _patterns(ws: list):
    key = tuple(ws)
    if _compiled["key"] != key:
        pats = []
        for w in ws:
            t = _norm(w).strip()
            star = t.endswith("*")
            t = t.rstrip("*").strip()
            parts = [p for p in re.split(r"[\s\-_]+", t) if p]
            if not parts:
                continue
            body = r"[\s\-_.]*".join(re.escape(p) for p in parts)
            pats.append((w, re.compile(r"(?<![\w])" + body + (r"\w*" if star else "") + r"(?![\w])")))
        _compiled.update(key=key, pats=pats)
    return _compiled["pats"]


def match(text: str, ws: list = None):
    """The first hide word this text contains (whole words; 'rip off' also catches ripoff / rip-off;
    'scam*' catches scammer; s c @ m style spellings), else None."""
    if not text:
        return None
    plain = _norm(text)
    leet = plain.translate(_LEET)
    for w, pat in _patterns(ws if ws is not None else words()):
        if pat.search(plain) or (leet != plain and pat.search(leet)):
            return w
    return None


def rematch_recent():
    """After the list changes: which recent visible comments match now."""
    ensure()
    ws = words()
    since = (datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    with db._conn() as con:
        rows = con.execute("SELECT id, message, matched FROM fb_comments WHERE by_page = 0 AND created_time >= ?",
                           (since,)).fetchall()
        for r in rows:
            m = match(r["message"], ws)
            if m != r["matched"]:
                con.execute("UPDATE fb_comments SET matched = ? WHERE id = ?", (m, r["id"]))


# ---------------------------------------------------------------- Meta calls

class GraphError(RuntimeError):
    def __init__(self, msg, code=None, status=0):
        super().__init__(msg)
        self.code = code
        self.status = status


_PERM_CODES = {10, 200, 210, 230, 270, 283, 294}
_RATE_CODES = {4, 17, 32, 613, 80001, 80006}


async def _call(client, method: str, path: str, token: str, params: dict = None):
    params = params or {}
    url = f"{meta.GRAPH}/{meta._version()}/{path}"
    try:
        if method == "GET":
            r = await client.get(url, params=meta._params(token, params))
        else:
            body = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
            r = await client.post(url, data=meta._params(token, body))
    except httpx.RequestError as e:
        raise GraphError(f"Could not reach Meta ({type(e).__name__}).") from e
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code = err.get("code")
        msg = err.get("error_user_msg") or err.get("message") or r.text[:200]
        raise GraphError(f"{msg} (Meta code {code})" if code else msg, code, r.status_code)
    try:
        return r.json()
    except ValueError:
        return {}


async def _pages_of(client, path, token, params, limit_items=None, stop=None):
    """Follow a Graph list. stop(row) → True ends early."""
    out = []
    body = await _call(client, "GET", path, token, params)
    while True:
        for row in body.get("data", []):
            if stop and stop(row):
                return out
            out.append(row)
            if limit_items and len(out) >= limit_items:
                return out
        nxt = (body.get("paging") or {}).get("next")
        if not nxt:
            return out
        try:
            r = await client.get(nxt)
        except httpx.RequestError as e:
            raise GraphError(f"Could not reach Meta ({type(e).__name__}).") from e
        if r.status_code >= 400:
            return out
        body = r.json()


def features(perms) -> dict:
    """{feature: True/False}; perms None = unknown (treated as allowed; Meta's answer decides)."""
    if perms is None:
        return {k: True for k in NEED}
    have = set(perms)
    return {k: all(p in have for p in need) for k, need in NEED.items()}


def missing(perms) -> list:
    if perms is None:
        return []
    have = set(perms)
    return sorted({p for need in NEED.values() for p in need if p not in have})


# ---------------------------------------------------------------- pages

def _page_rows(include_token=False):
    ensure()
    cols = "*" if include_token else ("page_id, name, conn_id, tasks, perms, picture, store_id, store_by, promoted_by,"
                                       " sync_on, autohide, autohide_from, backfilled_at, synced_at, msgs_at,"
                                       " subscribed, error, msg_error, cooldown_until, seen_at, gone,"
                                       " page_token IS NOT NULL AS has_token")
    with db._conn() as con:
        return [dict(r) for r in con.execute(f"SELECT {cols} FROM fb_pages ORDER BY name COLLATE NOCASE")]


def _plain(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def refresh_pages(force_promote=False):
    """Pages each token manages (+ their Page tokens), and which store each belongs to."""
    ensure()
    now = _now()
    conns = db.list_meta_connections()
    found = {}
    for c in conns:
        token = c["access_token"]
        async with httpx.AsyncClient(timeout=40) as client:
            perms = None
            try:
                got = await _call(client, "GET", "me/permissions", token)
                perms = sorted(p["permission"] for p in got.get("data", []) if p.get("status") == "granted")
            except GraphError:
                perms = None
            try:
                rows = await _pages_of(client, "me/accounts", token,
                                       {"fields": "id,name,access_token,tasks,picture{url}", "limit": 100})
                _state["conn_errors"].pop(c["id"], None)
            except GraphError as e:
                _state["conn_errors"][c["id"]] = f"{c.get('label') or c.get('name')}: {e}"
                continue
        for p in rows:
            pid = str(p["id"])
            score = sum(features(perms).values())
            if pid in found and found[pid]["score"] >= score:
                continue
            found[pid] = {"score": score, "name": p.get("name") or pid, "conn": c["id"], "token": p.get("access_token"),
                          "tasks": json.dumps(p.get("tasks") or []), "perms": json.dumps(perms) if perms is not None else None,
                          "picture": ((p.get("picture") or {}).get("data") or {}).get("url")}
    with db._conn() as con:
        for pid, f in found.items():
            con.execute(
                "INSERT INTO fb_pages (page_id, name, conn_id, page_token, tasks, perms, picture, seen_at, gone, autohide_from)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)"
                " ON CONFLICT(page_id) DO UPDATE SET name = excluded.name, conn_id = excluded.conn_id,"
                " page_token = COALESCE(excluded.page_token, fb_pages.page_token), tasks = excluded.tasks,"
                " perms = excluded.perms, picture = excluded.picture, seen_at = excluded.seen_at, gone = 0,"
                " autohide_from = COALESCE(fb_pages.autohide_from, excluded.autohide_from)",
                (pid, f["name"], f["conn"], f["token"], f["tasks"], f["perms"], f["picture"], now, now))
        if conns and not _state["conn_errors"]:
            # a Page no token manages any more keeps its comments but stops syncing
            keep = list(found) or [""]
            con.execute(f"UPDATE fb_pages SET page_token = NULL, conn_id = NULL WHERE page_id NOT IN"
                        f" ({','.join('?' * len(keep))})", keep)
    _state["pages_at"] = asyncio.get_event_loop().time()
    await _promote_pages(force_promote)


async def _promote_pages(force=False):
    """Pages each store's ad account can advertise as → store guess; Pages no token manages are listed too."""
    t = asyncio.get_event_loop().time()
    if not force and _state["promote_at"] and t - _state["promote_at"] < PROMOTE_EVERY:
        return
    _state["promote_at"] = t
    stores = [s for s in db.list_stores() if s.get("meta_account_id") and s.get("meta_connection_id")]
    by_page, names = {}, {}
    for s in stores:
        conn = db.get_meta_connection(s["meta_connection_id"])
        if not conn:
            continue
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                rows = await _pages_of(client, f"act_{s['meta_account_id']}/promote_pages", conn["access_token"],
                                       {"fields": "id,name", "limit": 100})
        except GraphError:
            continue
        for p in rows:
            by_page.setdefault(str(p["id"]), set()).add(s["id"])
            names[str(p["id"])] = p.get("name") or str(p["id"])
    all_stores = db.list_stores()
    with db._conn() as con:
        known = {r["page_id"]: dict(r) for r in con.execute("SELECT page_id, name, store_id, store_by FROM fb_pages")}
        for pid, sids in by_page.items():
            if pid not in known:
                con.execute("INSERT INTO fb_pages (page_id, name, seen_at, autohide_from) VALUES (?, ?, ?, ?)",
                            (pid, names[pid], _now(), _now()))
                known[pid] = {"page_id": pid, "name": names[pid], "store_id": None, "store_by": None}
            con.execute("UPDATE fb_pages SET promoted_by = ? WHERE page_id = ?", (json.dumps(sorted(sids)), pid))
        for pid, p in known.items():
            if p["store_by"] not in (None, "auto"):
                continue                      # a person picked it
            sids = by_page.get(pid) or set()
            guess = next(iter(sids)) if len(sids) == 1 else None
            if guess is None:                 # same name as a store
                n = _plain(p["name"])
                hits = [s["id"] for s in all_stores if n and (_plain(s["name"]) == n)]
                guess = hits[0] if len(hits) == 1 else None
            if guess is not None and guess != p["store_id"]:
                con.execute("UPDATE fb_pages SET store_id = ?, store_by = 'auto' WHERE page_id = ?", (guess, pid))


# ---------------------------------------------------------------- comments sync

_C_FIELDS = ("id,message,from{id,name},created_time,is_hidden,can_hide,can_reply_privately,parent{id},"
             "permalink_url,attachment{type,url,media{image{src}}}")
_P_FIELDS = "id,message,permalink_url,created_time,updated_time,full_picture,comments.limit(0).summary(true)"


def _upsert_comment(con, page, post_id, c, ws) -> bool:
    """True when it's new."""
    cid = str(c["id"])
    frm = c.get("from") or {}
    by_page = 1 if str(frm.get("id") or "") == page["page_id"] else 0
    att = c.get("attachment") or {}
    pic = ((att.get("media") or {}).get("image") or {}).get("src") or (att.get("url") if att.get("type") in ("photo", "sticker", "animated_image_share") else None)
    msg = c.get("message") or ""
    row = con.execute("SELECT id FROM fb_comments WHERE id = ?", (cid,)).fetchone()
    vals = dict(post_id=post_id, parent_id=str((c.get("parent") or {}).get("id") or "") or None,
                from_id=str(frm.get("id") or "") or None, from_name=frm.get("name"), message=msg, attachment=pic,
                created_time=_ts(c.get("created_time")), is_hidden=1 if c.get("is_hidden") else 0,
                can_hide=1 if c.get("can_hide") else 0, can_reply_privately=1 if c.get("can_reply_privately") else 0,
                permalink=c.get("permalink_url"), by_page=by_page, matched=None if by_page else match(msg, ws))
    if row:
        con.execute("UPDATE fb_comments SET " + ", ".join(f"{k} = ?" for k in vals) + " WHERE id = ?",
                    (*vals.values(), cid))
        return False
    vals.update(id=cid, page_id=page["page_id"], seen_at=_now())
    con.execute(f"INSERT INTO fb_comments ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})", tuple(vals.values()))
    return True


async def _sync_comments(client, page: dict, backfill: bool, full_ads: bool):
    token, pid = page["page_token"], page["page_id"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    posts = []
    old = lambda row: backfill and _ts(row.get("updated_time") or row.get("created_time")) < cutoff  # noqa: E731
    posts += [("post", p) for p in await _pages_of(client, f"{pid}/feed", token, {"fields": _P_FIELDS, "limit": 25},
                                                   limit_items=600 if backfill else 25, stop=old)]
    try:
        ads = await _pages_of(client, f"{pid}/ads_posts", token,
                              {"fields": _P_FIELDS, "limit": 100, "include_inline_create": "true"},
                              limit_items=1500 if (backfill or full_ads) else 100)
        posts += [("ad", p) for p in ads]
    except GraphError as e:
        if e.code not in _PERM_CODES and e.code not in (100,):
            raise
    ws = words()
    with db._conn() as con:
        known = {r["post_id"]: dict(r) for r in con.execute(
            "SELECT post_id, updated_time, comment_count FROM fb_posts WHERE page_id = ?", (pid,))}
    todo, seen = [], set()
    for kind, p in posts:
        post_id = str(p["id"])
        if post_id in seen:
            continue
        seen.add(post_id)
        count = ((p.get("comments") or {}).get("summary") or {}).get("total_count") or 0
        upd = _ts(p.get("updated_time"))
        k = known.get(post_id)
        with db._conn() as con:
            con.execute(
                "INSERT INTO fb_posts (post_id, page_id, kind, message, permalink, picture, created_time, updated_time, comment_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(post_id) DO UPDATE SET"
                " kind = CASE WHEN fb_posts.kind = 'ad' THEN 'ad' ELSE excluded.kind END, message = excluded.message,"
                " permalink = excluded.permalink, picture = excluded.picture, updated_time = excluded.updated_time,"
                " comment_count = excluded.comment_count",
                (post_id, pid, kind, (p.get("message") or "")[:2000], p.get("permalink_url"), p.get("full_picture"),
                 _ts(p.get("created_time")), upd, count))
        if count and (backfill or not k or k["comment_count"] != count or k["updated_time"] != upd):
            todo.append(post_id)
    new = 0
    for post_id in todo:
        stop_known = not backfill
        with db._conn() as con:
            have = {r[0] for r in con.execute("SELECT id FROM fb_comments WHERE post_id = ?", (post_id,))}
        streak = {"n": 0}

        def stop(row, have=have, streak=streak, stop_known=stop_known):
            if _ts(row.get("created_time")) < cutoff:
                return True
            if stop_known and str(row.get("id")) in have:
                streak["n"] += 1
                return streak["n"] > 40       # well past the new ones
            return False
        try:
            rows = await _pages_of(client, f"{post_id}/comments", token,
                                   {"fields": _C_FIELDS, "filter": "stream", "order": "reverse_chronological",
                                    "limit": 100}, limit_items=MAX_COMMENTS_PER_POST, stop=stop)
        except GraphError as e:
            if e.code in _PERM_CODES or e.code in _RATE_CODES:
                raise
            continue                           # a deleted post: skip it
        with db._conn() as con:
            for c in rows:
                new += _upsert_comment(con, page, post_id, c, ws)
            con.execute("UPDATE fb_posts SET checked_at = ? WHERE post_id = ?", (_now(), post_id))
        await asyncio.sleep(0.15)
    return new


async def _autohide(client, page: dict):
    """Hide new comments that match the list."""
    if not page.get("autohide") or not page.get("autohide_from"):
        return 0
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, matched FROM fb_comments WHERE page_id = ? AND is_hidden = 0 AND by_page = 0 AND keep = 0"
            " AND matched IS NOT NULL AND hide_tries < ? AND created_time >= ? ORDER BY created_time DESC LIMIT 50",
            (page["page_id"], MAX_HIDE_TRIES, page["autohide_from"]))]
    done = 0
    for r in rows:
        ok, err = await _set_hidden(client, page, r["id"], True)
        _log(r["id"], page["page_id"], "hide", "auto", r["matched"], ok, err)
        with db._conn() as con:
            if ok:
                con.execute("UPDATE fb_comments SET is_hidden = 1 WHERE id = ?", (r["id"],))
            else:
                con.execute("UPDATE fb_comments SET hide_tries = hide_tries + 1 WHERE id = ?", (r["id"],))
        done += ok
        if not ok and err and ("code 10)" in err or "code 200)" in err):
            break                               # no permission: stop trying this round
    return done


async def _set_hidden(client, page, cid, hidden: bool):
    try:
        got = await _call(client, "POST", cid, page["page_token"], {"is_hidden": "true" if hidden else "false"})
        return bool(got.get("success", True)), None
    except GraphError as e:
        return False, str(e)


def _log(cid, pid, action, by, reason, ok, err=None):
    with db._conn() as con:
        con.execute("INSERT INTO fb_hide_log (comment_id, page_id, action, by, reason, ok, error, at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (cid, pid, action, by, reason, 1 if ok else 0, err, _now()))


# ---------------------------------------------------------------- Messenger sync

async def _sync_msgs(client, page: dict, backfill: bool):
    token, pid = page["page_token"], page["page_id"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    convs = await _pages_of(client, f"{pid}/conversations", token,
                            {"platform": "messenger", "fields": "id,updated_time,snippet,unread_count,participants",
                             "limit": 25}, limit_items=300 if backfill else 25,
                            stop=lambda c: backfill and _ts(c.get("updated_time")) < cutoff)
    with db._conn() as con:
        known = {r[0]: r[1] for r in con.execute("SELECT id, updated_time FROM fb_convs WHERE page_id = ?", (pid,))}
    for c in convs:
        cid, upd = str(c["id"]), _ts(c.get("updated_time"))
        people = (c.get("participants") or {}).get("data") or []
        cust = next((x for x in people if str(x.get("id")) != pid), {})
        with db._conn() as con:
            con.execute(
                "INSERT INTO fb_convs (id, page_id, user_id, user_name, snippet, updated_time, unread)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id,"
                " user_name = excluded.user_name, snippet = excluded.snippet, updated_time = excluded.updated_time,"
                " unread = excluded.unread", (cid, pid, str(cust.get("id") or "") or None, cust.get("name"),
                                              (c.get("snippet") or "")[:300], upd, c.get("unread_count") or 0))
        if known.get(cid) == upd and not backfill:
            continue
        try:
            msgs = await _pages_of(client, f"{cid}/messages", token,
                                   {"fields": "id,message,from,created_time,attachments{mime_type,name,image_data{url},file_url}",
                                    "limit": 50}, limit_items=200 if backfill else 50)
        except GraphError as e:
            if e.code in _PERM_CODES or e.code in _RATE_CODES:
                raise
            continue
        last_c = last_p = ""
        with db._conn() as con:
            for m in msgs:
                frm = m.get("from") or {}
                by_page = 1 if str(frm.get("id") or "") == pid else 0
                at = _ts(m.get("created_time"))
                atts = [{"type": a.get("mime_type") or "", "name": a.get("name") or "",
                         "url": (a.get("image_data") or {}).get("url") or a.get("file_url") or ""}
                        for a in ((m.get("attachments") or {}).get("data") or [])]
                con.execute(
                    "INSERT INTO fb_msgs (id, conv_id, page_id, from_id, from_name, by_page, text, attachments, created_time)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text,"
                    " attachments = excluded.attachments",
                    (str(m["id"]), cid, pid, str(frm.get("id") or ""), frm.get("name"), by_page, m.get("message") or "",
                     json.dumps(atts) if atts else None, at))
                if by_page:
                    last_p = max(last_p, at)
                else:
                    last_c = max(last_c, at)
            con.execute("UPDATE fb_convs SET last_customer_at = MAX(COALESCE(last_customer_at, ''), ?),"
                        " last_page_at = MAX(COALESCE(last_page_at, ''), ?), synced_at = ?,"
                        " done_by = CASE WHEN ? > COALESCE(done_at, '') THEN NULL ELSE done_by END,"
                        " done_at = CASE WHEN ? > COALESCE(done_at, '') THEN NULL ELSE done_at END WHERE id = ?",
                        (last_c, last_p, _now(), last_c, last_c, cid))
        await asyncio.sleep(0.15)


# ---------------------------------------------------------------- the loop

async def _subscribe(client, page):
    try:
        await _call(client, "POST", f"{page['page_id']}/subscribed_apps", page["page_token"],
                    {"subscribed_fields": "feed,messages"})
        res = "ok"
    except GraphError as e:
        res = f"error: {e}"
    with db._conn() as con:
        con.execute("UPDATE fb_pages SET subscribed = ? WHERE page_id = ?", (res, page["page_id"]))


async def sync_page(page: dict):
    feats = features(json.loads(page["perms"]) if page.get("perms") else None)
    if not page.get("page_token") or not page.get("sync_on"):
        return
    if page.get("cooldown_until") and page["cooldown_until"] > _now():
        return
    full_ads = (page.get("full_at") or "") < (datetime.now(timezone.utc)
                                              - timedelta(seconds=FULL_ADS_EVERY)).isoformat(timespec="seconds")
    async with httpx.AsyncClient(timeout=60) as client:
        if feats["webhook"] and not (page.get("subscribed") or "").startswith("ok"):
            if not page.get("subscribed"):
                await _subscribe(client, page)
        err = None
        if feats["read"]:
            try:
                backfill = not page.get("backfilled_at")
                await _sync_comments(client, page, backfill, full_ads)
                with db._conn() as con:
                    con.execute("UPDATE fb_pages SET synced_at = ?, error = NULL, full_at = ?,"
                                " backfilled_at = COALESCE(backfilled_at, ?) WHERE page_id = ?",
                                (_now(), _now() if full_ads else page.get("full_at"), _now(), page["page_id"]))
                if backfill:
                    asyncio.create_task(_learn_quiet())
            except GraphError as e:
                err = str(e)
                cool = (datetime.now(timezone.utc) + timedelta(minutes=15 if e.code in _RATE_CODES else 5)).isoformat(timespec="seconds")
                with db._conn() as con:
                    con.execute("UPDATE fb_pages SET error = ?, cooldown_until = ? WHERE page_id = ?",
                                (err, cool, page["page_id"]))
        if feats["read"] and feats["moderate"] and not err:
            await _autohide(client, page)
        if feats["messaging"]:
            try:
                await _sync_msgs(client, page, not page.get("msgs_at"))
                with db._conn() as con:
                    con.execute("UPDATE fb_pages SET msgs_at = ?, msg_error = NULL WHERE page_id = ?",
                                (_now(), page["page_id"]))
            except GraphError as e:
                with db._conn() as con:
                    con.execute("UPDATE fb_pages SET msg_error = ? WHERE page_id = ?", (str(e), page["page_id"]))


async def sync_all(force_pages=False):
    if _state["running"]:
        return
    _state["running"] = True
    try:
        ensure()
        t = asyncio.get_event_loop().time()
        if force_pages or not _state["pages_at"] or t - _state["pages_at"] > PAGES_EVERY:
            try:
                await refresh_pages(force_promote=force_pages)
            except Exception as e:     # noqa: BLE001 — one bad token must not stop the loop
                _state["last_error"] = f"Pages: {type(e).__name__}: {e}"
        pages = [p for p in _page_rows(include_token=True) if p.get("page_token") and p.get("sync_on")]
        due = _state["due"]
        pages.sort(key=lambda p: p["page_id"] not in due)
        _state["due"] = set()
        for p in pages:
            try:
                await sync_page(p)
            except Exception as e:     # noqa: BLE001
                with db._conn() as con:
                    con.execute("UPDATE fb_pages SET error = ? WHERE page_id = ?",
                                (f"{type(e).__name__}: {e}"[:300], p["page_id"]))
        _state["last"] = _now()
    finally:
        _state["running"] = False


async def loop():
    await asyncio.sleep(40)
    _state["wake"] = asyncio.Event()
    while True:
        if is_on():
            try:
                await sync_all()
            except Exception as e:     # noqa: BLE001
                _state["last_error"] = f"{type(e).__name__}: {e}"
        try:
            await asyncio.wait_for(_state["wake"].wait(), POLL)
        except asyncio.TimeoutError:
            pass
        _state["wake"].clear()


def wake(page_id=None):
    if page_id:
        _state["due"].add(str(page_id))
    if _state["wake"]:
        _state["wake"].set()


# ---------------------------------------------------------------- learning

_STOP = set("""a an the and or but if then so to of in on at for with from by as is are was were be been being it its
this that these those i me my we our you your he she they them their his her him not no yes do does did done have has
had will would can could should just very really too also all any some more most much many get got go going
what when where who why how which than there here out up down about over into only one two its im ive dont didnt
cant wont isnt arent wasnt thats theres youre theyre ill id lol ok okay oh hi hello please thanks thank love like
nice good great beautiful wow amazing want need would order ordered""".split())


def _terms(text: str) -> set:
    toks = [t for t in re.findall(r"[a-z][a-z0-9.]+", _norm(text)) if len(t) >= 3]
    toks = [t.strip(".") for t in toks]
    uni = {t for t in toks if t not in _STOP and len(t) >= 3}
    bi = {f"{a} {b}" for a, b in zip(toks, toks[1:]) if not (a in _STOP and b in _STOP)}
    return uni | bi


def learn(page_ids=None) -> dict:
    """Words that recur in comments hidden on Facebook (by the team, not by EcomVital) and seldom in
    visible ones: suggestions for the hide list."""
    ensure()
    q = "SELECT c.id, c.message, c.is_hidden, c.page_id FROM fb_comments c WHERE c.by_page = 0 AND c.message != ''"
    args = []
    if page_ids:
        q += f" AND c.page_id IN ({','.join('?' * len(page_ids))})"
        args += list(page_ids)
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(q, args)]
        auto = {r[0] for r in con.execute("SELECT comment_id FROM fb_hide_log WHERE by = 'auto' AND action = 'hide' AND ok = 1")}
    hidden = [r for r in rows if r["is_hidden"] and r["id"] not in auto]
    visible = [r for r in rows if not r["is_hidden"]]
    ws = words()
    caught = sum(1 for r in hidden if match(r["message"], ws))
    hd, vd, ex = {}, {}, {}
    for r in hidden:
        for t in _terms(r["message"]):
            hd[t] = hd.get(t, 0) + 1
            ex.setdefault(t, [])
            if len(ex[t]) < 2:
                ex[t].append(r["message"][:140])
    for r in visible:
        for t in _terms(r["message"]):
            if t in hd:
                vd[t] = vd.get(t, 0) + 1
    H, V = max(len(hidden), 1), max(len(visible), 1)
    sugg = []
    for t, n in hd.items():
        if n < 2:
            continue
        ratio = ((n + 0.5) / (H + 1)) / ((vd.get(t, 0) + 0.5) / (V + 1))
        if ratio < 3:
            continue
        if match(t, ws):
            continue                             # the list already catches it
        sugg.append({"term": t, "hidden": n, "visible": vd.get(t, 0), "score": round(n * math.log(ratio), 2),
                     "examples": ex.get(t, [])})
    sugg.sort(key=lambda s: -s["score"])
    # a two-word phrase that only repeats a suggested single word adds nothing
    singles = {s["term"] for s in sugg if " " not in s["term"]}
    sugg = [s for s in sugg if " " not in s["term"] or not (set(s["term"].split()) & singles)][:40]
    out = {"at": _now(), "pages": page_ids or [], "hidden": len(hidden), "visible": len(visible),
           "caught_by_list": caught, "suggestions": sugg}
    db.set_setting("social_learn", json.dumps(out))
    return out


async def _learn_quiet():
    try:
        await asyncio.to_thread(learn, None)
    except Exception:              # noqa: BLE001
        pass


# ---------------------------------------------------------------- read helpers

def _page_map():
    return {p["page_id"]: p for p in _page_rows()}


def _store_names():
    return {s["id"]: (s.get("name") or "").strip() for s in db.list_stores()}


def _page_view(p: dict, stores: dict) -> dict:
    perms = json.loads(p["perms"]) if p.get("perms") else None
    feats = features(perms) if p.get("has_token") else {k: False for k in NEED}
    tasks = json.loads(p["tasks"]) if p.get("tasks") else []
    if not p.get("has_token"):
        state = "not_assigned"
    elif not feats["read"]:
        state = "needs_token"
    elif p.get("error"):
        state = "error"
    else:
        state = "ok"
    return {"page_id": p["page_id"], "name": p["name"], "picture": p.get("picture"), "store_id": p.get("store_id"),
            "store": stores.get(p.get("store_id")) or "", "store_by": p.get("store_by"),
            "promoted_by": json.loads(p["promoted_by"]) if p.get("promoted_by") else [],
            "state": state, "features": feats, "missing": missing(perms) if p.get("has_token") else [],
            "tasks": tasks, "sync_on": bool(p["sync_on"]), "autohide": bool(p["autohide"]),
            "autohide_from": p.get("autohide_from"), "synced_at": p.get("synced_at"), "msgs_at": p.get("msgs_at"),
            "backfilled": bool(p.get("backfilled_at")), "error": p.get("error"), "msg_error": p.get("msg_error"),
            "subscribed": p.get("subscribed")}


def counts(store: int = 0, page: str = "") -> dict:
    ensure()
    w, a = _scope(store, page, "c")
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    with db._conn() as con:
        def one(sql, extra=()):
            return con.execute(sql, (*a, *extra)).fetchone()[0]
        out = {
            "needs_reply": one(f"SELECT COUNT(*) FROM fb_comments c WHERE {w} AND {_NEEDS} AND c.created_time >= ?", (since,)),
            "hidden": one(f"SELECT COUNT(*) FROM fb_comments c WHERE {w} AND c.is_hidden = 1 AND c.created_time >= ?", (since,)),
            "matches": one(f"SELECT COUNT(*) FROM fb_comments c WHERE {w} AND c.is_hidden = 0 AND c.matched IS NOT NULL"
                           " AND c.keep = 0 AND c.by_page = 0 AND c.created_time >= ?", (since,)),
        }
        w2, a2 = _scope(store, page, "v")
        out["chats_open"] = con.execute(
            f"SELECT COUNT(*) FROM fb_convs v WHERE {w2} AND v.done_at IS NULL AND COALESCE(v.last_customer_at, '') >"
            " COALESCE(v.last_page_at, '') AND v.last_customer_at >= ?", (*a2, since)).fetchone()[0]
    return out


_NEEDS = ("c.by_page = 0 AND c.is_hidden = 0 AND c.parent_id IS NULL AND c.done_at IS NULL AND c.replied_at IS NULL"
          " AND NOT EXISTS (SELECT 1 FROM fb_comments r WHERE r.parent_id = c.id AND r.by_page = 1)")


def _scope(store: int, page: str, alias: str):
    if page:
        return f"{alias}.page_id = ?", [page]
    if store:
        return f"{alias}.page_id IN (SELECT page_id FROM fb_pages WHERE store_id = ?)", [store]
    return "1 = 1", []


def _comment_view(r: dict, pages: dict, stores: dict) -> dict:
    p = pages.get(r["page_id"]) or {}
    return {**{k: r.get(k) for k in ("id", "page_id", "post_id", "parent_id", "from_name", "message", "attachment",
                                       "created_time", "permalink", "matched", "replied_by", "replied_at", "done_by",
                                       "done_at")},
            "is_hidden": bool(r.get("is_hidden")), "by_page": bool(r.get("by_page")), "keep": bool(r.get("keep")),
            "can_reply_privately": bool(r.get("can_reply_privately")), "private_replied": bool(r.get("private_replied")),
            "page": p.get("name") or r["page_id"], "store": stores.get(p.get("store_id")) or "",
            "post_kind": r.get("post_kind"), "post_text": (r.get("post_text") or "")[:160],
            "replies": r.get("replies") or 0, "page_replied": bool(r.get("page_replied")),
            "hidden_by": r.get("hidden_by"), "hidden_reason": r.get("hidden_reason"), "hidden_at": r.get("hidden_at")}


_HID = ("(SELECT l.{f} FROM fb_hide_log l WHERE l.comment_id = c.id AND l.action = 'hide' AND l.ok = 1"
        " ORDER BY l.id DESC LIMIT 1)")


def list_comments(view="needs_reply", store=0, page="", q="", before="", kind="", limit=100) -> list:
    ensure()
    w, a = _scope(store, page, "c")
    cond = {"needs_reply": _NEEDS, "all": "c.by_page = 0", "hidden": "c.is_hidden = 1",
            "matches": "c.is_hidden = 0 AND c.matched IS NOT NULL AND c.keep = 0 AND c.by_page = 0",
            "done": "c.by_page = 0 AND (c.done_at IS NOT NULL OR c.replied_at IS NOT NULL OR EXISTS"
                    " (SELECT 1 FROM fb_comments r WHERE r.parent_id = c.id AND r.by_page = 1))"}.get(view, _NEEDS)
    sql = (f"SELECT c.*, p.kind AS post_kind, p.message AS post_text,"
           f" (SELECT COUNT(*) FROM fb_comments r WHERE r.parent_id = c.id) AS replies,"
           f" EXISTS (SELECT 1 FROM fb_comments r WHERE r.parent_id = c.id AND r.by_page = 1) AS page_replied,"
           f" {_HID.format(f='by')} AS hidden_by, {_HID.format(f='reason')} AS hidden_reason,"
           f" {_HID.format(f='at')} AS hidden_at"
           f" FROM fb_comments c LEFT JOIN fb_posts p ON p.post_id = c.post_id WHERE {w} AND {cond}")
    if kind in ("ad", "post"):
        sql += " AND p.kind = ?"
        a.append(kind)
    if q:
        sql += " AND (c.message LIKE ? OR c.from_name LIKE ?)"
        a += [f"%{q}%", f"%{q}%"]
    if before:
        sql += " AND c.created_time < ?"
        a.append(before)
    sql += " ORDER BY " + ("COALESCE(hidden_at, c.created_time)" if view == "hidden" else "c.created_time") + " DESC LIMIT ?"
    a.append(min(int(limit or 100), 300))
    pages, stores = _page_map(), _store_names()
    with db._conn() as con:
        return [_comment_view(dict(r), pages, stores) for r in con.execute(sql, a)]


# ---------------------------------------------------------------- routes

def _page_full(page_id: str) -> dict:
    with db._conn() as con:
        p = con.execute("SELECT * FROM fb_pages WHERE page_id = ?", (page_id,)).fetchone()
    if not p:
        raise HTTPException(404, "That Facebook Page isn't connected.")
    return dict(p)


def _need_feature(page: dict, feat: str):
    if not page.get("page_token"):
        raise HTTPException(400, f"No Meta token manages the Page “{page['name']}” yet. In Meta Business Settings, give the"
                                 " Page to the System User, then make a new token and paste it in Settings → Meta.")
    perms = json.loads(page["perms"]) if page.get("perms") else None
    if not features(perms)[feat]:
        need = ", ".join(p for p in NEED[feat] if perms is not None and p not in perms)
        raise HTTPException(400, f"Needs the new token: the token for “{page['name']}” doesn't have {need} yet"
                                 f" (needed to {FEATURE_NAME[feat]}).")


def _comment(cid: str) -> dict:
    with db._conn() as con:
        c = con.execute("SELECT * FROM fb_comments WHERE id = ?", (cid,)).fetchone()
    if not c:
        raise HTTPException(404, "Comment not found.")
    return dict(c)


@router.get("/api/inbox/social")
def api_overview(request: Request, store: int = 0, page: str = ""):
    ensure()
    stores = _store_names()
    pages = [_page_view(p, stores) for p in _page_rows() if not p.get("gone")]
    u = getattr(request.state, "user", None) or {}
    token = db.get_setting("social_webhook_token")
    is_owner = u.get("role", "owner") == "owner"
    if is_owner and not token:
        token = secrets.token_urlsafe(18)
        db.set_setting("social_webhook_token", token)
    conns = db.list_meta_connections()
    return {"on": is_on(), "pages": pages, "counts": counts(store, page), "words": words(),
            "stores": [{"id": k, "name": v} for k, v in sorted(stores.items(), key=lambda x: x[1].lower())],
            "can": {"work": auth.at_least(u, "inbox", "work"), "manage": auth.at_least(u, "inbox", "manage"),
                    "owner": is_owner},
            "connections": len(conns), "conn_errors": list(_state["conn_errors"].values()),
            "last_sync": _state["last"], "running": _state["running"], "last_error": _state["last_error"],
            "webhook": ({"url": f"{(os.getenv('PUBLIC_URL') or str(request.base_url)).rstrip('/')}/api/meta/webhook", "verify_token": token}
                        if is_owner else None),
            "need": {k: list(v) for k, v in NEED.items()}}


@router.get("/api/inbox/social/counts")
def api_counts(store: int = 0, page: str = ""):
    return counts(store, page)


@router.get("/api/inbox/social/comments")
def api_comments(view: str = "needs_reply", store: int = 0, page: str = "", q: str = "", before: str = "", kind: str = ""):
    return {"comments": list_comments(view, store, page, q.strip(), before, kind)}


@router.get("/api/inbox/social/comments/{cid}")
def api_comment(cid: str):
    ensure()
    c = _comment(cid)
    top = c["parent_id"] or c["id"]
    pages, stores = _page_map(), _store_names()
    with db._conn() as con:
        thread = [dict(r) for r in con.execute(
            "SELECT * FROM fb_comments WHERE id = ? OR parent_id = ? ORDER BY created_time", (top, top))]
        post = con.execute("SELECT post_id, kind, message, permalink, picture, created_time FROM fb_posts WHERE post_id = ?",
                           (c["post_id"],)).fetchone()
        log = [dict(r) for r in con.execute(
            "SELECT l.comment_id, l.action, l.by, l.reason, l.ok, l.error, l.at FROM fb_hide_log l"
            " WHERE l.comment_id IN (SELECT id FROM fb_comments WHERE id = ? OR parent_id = ?) ORDER BY l.id", (top, top))]
    last_hide = {}
    for entry in log:
        if entry["action"] == "hide" and entry["ok"]:
            last_hide[entry["comment_id"]] = entry
    for t in [c, *thread]:
        h = last_hide.get(t["id"])
        if h:
            t.update(hidden_by=h["by"], hidden_reason=h["reason"], hidden_at=h["at"])
        if post:
            t["post_kind"] = post["kind"]
    p = pages.get(c["page_id"]) or {}
    pv = _page_view(p, stores) if p else {}
    return {"comment": _comment_view(c, pages, stores), "thread": [_comment_view(t, pages, stores) for t in thread],
            "post": dict(post) if post else None, "log": log, "page": pv}


@router.post("/api/inbox/social/comments/{cid}/reply")
async def api_reply(cid: str, payload: dict, request: Request):
    _level(request, "work")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write a reply first.")
    c = _comment(cid)
    page = _page_full(c["page_id"])
    who = _who(request)
    async with httpx.AsyncClient(timeout=30) as client:
        if payload.get("private"):
            _need_feature(page, "messaging")
            try:
                await _call(client, "POST", f"{page['page_id']}/messages", page["page_token"],
                            {"recipient": {"comment_id": cid}, "message": {"text": text}})
            except GraphError as e:
                raise HTTPException(400, f"Meta didn't send the private message: {e}")
            with db._conn() as con:
                con.execute("UPDATE fb_comments SET private_replied = 1, replied_by = ?, replied_at = ? WHERE id = ?",
                            (who, _now(), cid))
            wake(page["page_id"])
            return {"ok": True, "private": True}
        _need_feature(page, "moderate")
        target = c["parent_id"] or cid           # Facebook threads are one level deep
        try:
            got = await _call(client, "POST", f"{target}/comments", page["page_token"], {"message": text})
        except GraphError as e:
            raise HTTPException(400, f"Meta didn't post the reply: {e}")
    with db._conn() as con:
        if got.get("id"):
            con.execute("INSERT OR IGNORE INTO fb_comments (id, page_id, post_id, parent_id, from_id, from_name, message,"
                        " created_time, by_page, replied_by, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (str(got["id"]), page["page_id"], c["post_id"], target, page["page_id"], page["name"], text,
                         _now(), who, _now()))
        con.execute("UPDATE fb_comments SET replied_by = ?, replied_at = ? WHERE id = ?", (who, _now(), cid))
    return {"ok": True, "id": got.get("id")}


@router.post("/api/inbox/social/comments/{cid}/hide")
async def api_hide(cid: str, payload: dict, request: Request):
    _level(request, "work")
    hide = bool(payload.get("hidden", True))
    c = _comment(cid)
    page = _page_full(c["page_id"])
    _need_feature(page, "moderate")
    async with httpx.AsyncClient(timeout=30) as client:
        ok, err = await _set_hidden(client, page, cid, hide)
    who = _who(request)
    _log(cid, page["page_id"], "hide" if hide else "unhide", who, (payload.get("reason") or "").strip()[:200] or None, ok, err)
    if not ok:
        raise HTTPException(400, f"Meta refused: {err}")
    with db._conn() as con:
        con.execute("UPDATE fb_comments SET is_hidden = ?, keep = CASE WHEN ? THEN keep ELSE 1 END WHERE id = ?",
                    (1 if hide else 0, 1 if hide else 0, cid))
    return {"ok": True}


@router.post("/api/inbox/social/comments/{cid}/done")
def api_done(cid: str, payload: dict, request: Request):
    _level(request, "work")
    _comment(cid)
    done = payload.get("done", True)
    with db._conn() as con:
        con.execute("UPDATE fb_comments SET done_by = ?, done_at = ? WHERE id = ?",
                    (_who(request) if done else None, _now() if done else None, cid))
    return {"ok": True}


@router.get("/api/inbox/social/chats")
def api_chats(view: str = "open", store: int = 0, page: str = "", q: str = ""):
    ensure()
    w, a = _scope(store, page, "v")
    sql = f"SELECT v.* FROM fb_convs v WHERE {w}"
    if view == "open":
        sql += " AND v.done_at IS NULL AND COALESCE(v.last_customer_at, '') > COALESCE(v.last_page_at, '')"
    if q:
        sql += " AND (v.user_name LIKE ? OR v.snippet LIKE ?)"
        a += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY v.updated_time DESC LIMIT 200"
    pages, stores = _page_map(), _store_names()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(sql, a)]
    for r in rows:
        p = pages.get(r["page_id"]) or {}
        r.update(page=p.get("name") or r["page_id"], store=stores.get(p.get("store_id")) or "",
                 window_open=_window_open(r))
    return {"chats": rows}


def _window_open(conv: dict) -> bool:
    last = conv.get("last_customer_at")
    if not last:
        return False
    try:
        return datetime.fromisoformat(last) > datetime.now(timezone.utc) - timedelta(hours=24)
    except ValueError:
        return False


@router.get("/api/inbox/social/chats/{conv_id}")
def api_chat(conv_id: str):
    ensure()
    with db._conn() as con:
        v = con.execute("SELECT * FROM fb_convs WHERE id = ?", (conv_id,)).fetchone()
        if not v:
            raise HTTPException(404, "Chat not found.")
        msgs = [dict(r) for r in con.execute(
            "SELECT id, from_name, by_page, text, attachments, created_time, sent_by FROM fb_msgs WHERE conv_id = ?"
            " ORDER BY created_time", (conv_id,))]
    v = dict(v)
    for m in msgs:
        m["attachments"] = json.loads(m["attachments"]) if m.get("attachments") else []
    pages, stores = _page_map(), _store_names()
    p = pages.get(v["page_id"]) or {}
    v.update(page=p.get("name") or v["page_id"], store=stores.get(p.get("store_id")) or "", window_open=_window_open(v))
    return {"chat": v, "messages": msgs, "page": _page_view(p, stores) if p else {}}


@router.post("/api/inbox/social/chats/{conv_id}/reply")
async def api_chat_reply(conv_id: str, payload: dict, request: Request):
    _level(request, "work")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write a message first.")
    with db._conn() as con:
        v = con.execute("SELECT * FROM fb_convs WHERE id = ?", (conv_id,)).fetchone()
    if not v:
        raise HTTPException(404, "Chat not found.")
    v = dict(v)
    page = _page_full(v["page_id"])
    _need_feature(page, "messaging")
    if not _window_open(v):
        raise HTTPException(400, "Meta only lets a Page reply within 24 hours of the customer's last message."
                                 " Reply from Meta Business Suite instead, or wait for the customer to write again.")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            got = await _call(client, "POST", f"{page['page_id']}/messages", page["page_token"],
                              {"recipient": {"id": v["user_id"]}, "messaging_type": "RESPONSE", "message": {"text": text}})
        except GraphError as e:
            raise HTTPException(400, f"Meta didn't send it: {e}")
    who, now = _who(request), _now()
    mid = str(got.get("message_id") or f"local:{secrets.token_hex(6)}")
    with db._conn() as con:
        con.execute("INSERT OR IGNORE INTO fb_msgs (id, conv_id, page_id, from_id, from_name, by_page, text, created_time, sent_by)"
                    " VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)", (mid, conv_id, page["page_id"], page["page_id"], page["name"],
                                                            text, now, who))
        con.execute("UPDATE fb_convs SET last_page_at = ?, snippet = ?, updated_time = ? WHERE id = ?",
                    (now, text[:300], now, conv_id))
    return {"ok": True}


@router.post("/api/inbox/social/chats/{conv_id}/done")
def api_chat_done(conv_id: str, payload: dict, request: Request):
    _level(request, "work")
    done = payload.get("done", True)
    with db._conn() as con:
        con.execute("UPDATE fb_convs SET done_by = ?, done_at = ? WHERE id = ?",
                    (_who(request) if done else None, _now() if done else None, conv_id))
    return {"ok": True}


@router.put("/api/inbox/social/settings")
def api_settings(payload: dict, request: Request):
    _level(request, "manage")
    if "on" in payload:
        db.set_setting("social_on", "1" if payload["on"] else "0")
        if payload["on"]:
            wake()
    if "words" in payload and payload["words"] is None:          # back to the starter list
        db.set_setting("social_words", json.dumps(DEFAULT_WORDS))
        rematch_recent()
    elif "words" in payload:
        ws, seen = [], set()
        for w in payload["words"] or []:
            w = re.sub(r"\s+", " ", str(w)).strip()[:60]
            if w and w.lower() not in seen:
                seen.add(w.lower())
                ws.append(w)
        db.set_setting("social_words", json.dumps(ws))
        rematch_recent()
    return {"ok": True, "on": is_on(), "words": words()}


@router.put("/api/inbox/social/pages/{page_id}")
def api_page(page_id: str, payload: dict, request: Request):
    _level(request, "manage")
    _page_full(page_id)
    with db._conn() as con:
        if "store_id" in payload:
            sid = payload["store_id"] or None
            con.execute("UPDATE fb_pages SET store_id = ?, store_by = ? WHERE page_id = ?",
                        (int(sid) if sid else None, _who(request), page_id))
        if "sync_on" in payload:
            con.execute("UPDATE fb_pages SET sync_on = ? WHERE page_id = ?", (1 if payload["sync_on"] else 0, page_id))
        if "autohide" in payload:
            on = bool(payload["autohide"])
            # switching it back on starts from now: older comments are never hidden by surprise
            con.execute("UPDATE fb_pages SET autohide = ?, autohide_from = CASE WHEN ? THEN ? ELSE autohide_from END"
                        " WHERE page_id = ?", (1 if on else 0, 1 if on else 0, _now(), page_id))
    wake(page_id)
    return {"ok": True}


@router.post("/api/inbox/social/pages/{page_id}/hide-older")
async def api_hide_older(page_id: str, request: Request):
    """Hide the visible comments of the last 30 days that match the list (from before auto-hide was on)."""
    _level(request, "manage")
    page = _page_full(page_id)
    _need_feature(page, "moderate")
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    with db._conn() as con:
        ids = [dict(r) for r in con.execute(
            "SELECT id, matched FROM fb_comments WHERE page_id = ? AND is_hidden = 0 AND by_page = 0 AND keep = 0"
            " AND matched IS NOT NULL AND created_time >= ?", (page_id, since))]
    who = _who(request)

    async def run():
        async with httpx.AsyncClient(timeout=30) as client:
            for r in ids:
                ok, err = await _set_hidden(client, page, r["id"], True)
                _log(r["id"], page_id, "hide", who, f"{r['matched']} (older comment)", ok, err)
                if ok:
                    with db._conn() as con:
                        con.execute("UPDATE fb_comments SET is_hidden = 1 WHERE id = ?", (r["id"],))
                await asyncio.sleep(0.3)
    asyncio.create_task(run())
    return {"ok": True, "count": len(ids)}


@router.post("/api/inbox/social/test")
def api_test(payload: dict):
    text = payload.get("text") or ""
    return {"matched": match(text), "text": text}


@router.post("/api/inbox/social/sync")
async def api_sync(request: Request):
    _level(request, "work")
    if not _state["running"]:
        asyncio.create_task(sync_all(force_pages=True))
    return {"ok": True, "started": True}


@router.get("/api/inbox/social/learn")
def api_learn_get():
    raw = db.get_setting("social_learn")
    try:
        got = json.loads(raw) if raw else None
    except ValueError:
        got = None
    return {"result": got, "running": _state["learn"]["running"], "step": _state["learn"]["step"]}


@router.post("/api/inbox/social/learn")
async def api_learn(payload: dict, request: Request):
    _level(request, "manage")
    pages = [str(p) for p in payload.get("pages") or []]
    if _state["learn"]["running"]:
        return {"ok": True, "started": False}

    async def run():
        _state["learn"].update(running=True, step="Reading comments from Facebook…")
        try:
            for p in _page_rows(include_token=True):
                if (not pages or p["page_id"] in pages) and p.get("page_token") and not p.get("backfilled_at"):
                    _state["learn"]["step"] = f"Reading 90 days of comments on {p['name']}…"
                    await sync_page(p)
            _state["learn"]["step"] = "Looking for words that come back…"
            await asyncio.to_thread(learn, pages or None)
        finally:
            _state["learn"].update(running=False, step="")
    asyncio.create_task(run())
    return {"ok": True, "started": True}


@router.post("/api/inbox/social/learn/accept")
def api_learn_accept(payload: dict, request: Request):
    _level(request, "manage")
    add = [re.sub(r"\s+", " ", str(t)).strip()[:60] for t in payload.get("terms") or [] if str(t).strip()]
    ws = words()
    low = {w.lower() for w in ws}
    ws += [t for t in add if t.lower() not in low]
    db.set_setting("social_words", json.dumps(ws))
    rematch_recent()
    raw = db.get_setting("social_learn")
    if raw:
        try:
            got = json.loads(raw)
            got["suggestions"] = [s for s in got.get("suggestions", []) if s["term"] not in add]
            db.set_setting("social_learn", json.dumps(got))
        except ValueError:
            pass
    return {"ok": True, "words": ws}


# ---------------------------------------------------------------- webhook (optional speed-up)

@router.get("/api/meta/webhook")
def webhook_verify(request: Request):
    q = request.query_params
    token = db.get_setting("social_webhook_token")
    if q.get("hub.mode") == "subscribe" and token and hmac.compare_digest(q.get("hub.verify_token") or "", token):
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise HTTPException(403, "Wrong verify token.")


@router.post("/api/meta/webhook")
async def webhook(request: Request):
    body = await request.body()
    secret = os.getenv("META_APP_SECRET", "")
    if secret:
        sig = request.headers.get("x-hub-signature-256", "")
        good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            raise HTTPException(403, "Bad signature.")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {}
    # Only wakes the poll for the Page; what changed is read back from Meta itself.
    for e in data.get("entry") or []:
        if e.get("id"):
            wake(str(e["id"]))
    return {"ok": True}


# ---------------------------------------------------------------- for the assistant

def hidden_for_chat(days: int = 7, store: int = 0) -> dict:
    ensure()
    rows = list_comments("hidden", store=store, limit=50)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = [r for r in rows if (r.get("hidden_at") or r["created_time"] or "") >= since]
    return {"counts": counts(store), "hidden": [
        {"page": r["page"], "store": r["store"], "comment": (r["message"] or "")[:200], "from": r["from_name"],
         "on": r["post_kind"], "hidden_by": r["hidden_by"] or "on Facebook (before EcomVital)",
         "word": r["hidden_reason"], "when": r["hidden_at"] or r["created_time"]} for r in rows]}
