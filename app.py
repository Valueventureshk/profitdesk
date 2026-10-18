"""EcomVital — ROAS and net profit across every Shopify store you run.

Run:  python app.py     then open http://127.0.0.1:8787

Nothing is stored except your connections and your cost percentage. Sales and
ad spend are read live from Shopify, Google and Meta every time the dashboard loads.
"""
import asyncio
import html
import json
import os
import random
import re
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, time as clock, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import airwallex_client as awx
import paypal_client as paypal
import auth
import cashflow
import statement
import adbills
import sizecharts
import importer
import trackpage
import track17
import expenses
import cog
import cog_desk
import invoices
import invoice_ai
import suppliers
import disputes
import recordings
import google_desk
import gads_manage
import gads_conv
import whatsapp
import ads_engine
import ads_brain
import tracking
import mail_client
import gmail_send
import mail_labels
import tickets
import ai_brain
import anthropic
import ai_coach
import reports
import ai_chat
import ai_data
import products_desk
import adcopy
import paypal_track
import timetrack
import agent
import slack_client
import meta_manage
import meta_rules
import meta_brain
import tasks
import db
import demo
import fx
import google_ads_client as gads
import google_sheet as gsheet
import meta_ads_client as meta
import metrics
import shopify_client as shop
import checkout as co
from shopify_client import ShopifyClient, ShopifyError

# On Railway (or any host that sets RAILWAY_ENVIRONMENT) listen on all
# interfaces; on a Mac stay on this computer only.
HOST = "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else os.getenv("HOST", "127.0.0.1")
# While EcomVital has no accounts yet, creating the owner or restoring a
# backup needs this code when it's set (always set it on a public server).
SETUP_CODE = os.getenv("SETUP_CODE", "")
PORT = int(os.getenv("PORT", "8787"))
# Where people reach EcomVital. On a server set PUBLIC_URL (e.g.
# https://app.example.com); every return address for Shopify and Google is
# built from it.
BASE_URL = (os.getenv("PUBLIC_URL") or f"http://{HOST}:{PORT}").rstrip("/")
GOOGLE_REDIRECT = f"{BASE_URL}/auth/google/callback"
# Shopify only returns to addresses listed in each store's app; until every app lists the new address, this can stay
# on the old one (Railway variable SHOPIFY_REDIRECT_URL).
SHOPIFY_REDIRECT = os.getenv("SHOPIFY_REDIRECT_URL") or f"{BASE_URL}/auth/shopify/callback"

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CACHE_TTL = 60  # seconds; the Refresh button bypasses it
_timing: dict = {}   # last dashboard build: seconds per step (owner: /api/debug/timing)
LIVE_DAYS = 2   # the last days always read live from Shopify; older ones from the saved copy


# ---- stall watchdog: when the server is stuck on one job (pages wait meanwhile), note where
_stalls: list = []            # [(when, seconds, "file:line function" stack of EcomVital code)]
_beat = {"t": time.time()}


async def _heartbeat():
    while True:
        _beat["t"] = time.time()
        await asyncio.sleep(0.2)


def _watchdog(loop_thread_id: int):
    import sys
    import threading
    import traceback
    noted = 0.0
    while True:
        time.sleep(0.25)
        late = time.time() - _beat["t"]
        if late > 0.6 and _beat["t"] != noted:
            frame = sys._current_frames().get(loop_thread_id)
            if frame is None:
                continue
            ours = [f"{os.path.basename(fs.filename)}:{fs.lineno} {fs.name}" for fs in traceback.extract_stack(frame)
                    if os.path.dirname(os.path.abspath(fs.filename)) == os.path.dirname(os.path.abspath(__file__))]
            noted = _beat["t"]
            _stalls.append([datetime.now(timezone.utc).isoformat(timespec="seconds"), round(late, 2), ours[-6:]])
            del _stalls[:-60]
        elif _stalls and _beat["t"] == noted:
            _stalls[-1][1] = round(max(_stalls[-1][1], late), 2)


@asynccontextmanager
async def lifespan(app):
    db.init()
    co.init()
    import threading
    asyncio.create_task(_heartbeat())
    threading.Thread(target=_watchdog, args=(threading.get_ident(),), daemon=True).start()
    saver = asyncio.create_task(_save_all_history())
    coster = asyncio.create_task(_cog_loop())
    reader = asyncio.create_task(_mail_loop())
    snapper = asyncio.create_task(_snapshot_loop())
    tracker = asyncio.create_task(_scm_loop())
    asyncio.create_task(_outside_loop())
    asyncio.create_task(_gdesk_loop())
    asyncio.create_task(_ads_loop())
    asyncio.create_task(_mads_loop())
    asyncio.create_task(_time_loop())
    asyncio.create_task(_up_loop())
    asyncio.create_task(_gconv_loop())
    asyncio.create_task(_px_order_loop())
    asyncio.create_task(_assist_loop())
    asyncio.create_task(_co_loop())
    asyncio.create_task(paypal_track.loop())
    asyncio.create_task(_arrivals_loop())
    for i in db.list_invoices():                     # AI double-checks cut off by a restart
        if i.get("ai_check") == "pending":
            asyncio.create_task(_ai_check_invoice(i["id"]))
    yield
    tracker.cancel()
    saver.cancel()
    coster.cancel()
    reader.cancel()
    snapper.cancel()


app = FastAPI(title="EcomVital", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/api/debug/timing")
def api_debug_timing():
    """Owner: seconds per step of the last dashboard build."""
    return _timing


@app.get("/api/debug/google-usage")
def api_debug_google_usage(days: int = 7):
    """Owner: Google Ads API operations per Pacific day (resets 3pm Hong Kong), split by report / change type."""
    return {"days": gads.usage(min(max(days, 1), 30)), "left_today": gads.left_today(),
            "note": "Breakdown recorded from 14 Oct 2026; earlier days have the total only."}


@app.get("/api/debug/stalls")
def api_debug_stalls():
    """Owner: the last times the server was stuck on one job, how long, and where."""
    return {"stalls": list(reversed(_stalls))}
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
app.add_middleware(GZipMiddleware, minimum_size=1024)   # pages, scripts and data ~5x smaller on the wire

_google_states: set[str] = set()
_shopify_states: dict[str, str] = {}
_cache: dict[tuple, tuple[float, dict]] = {}


# ---------------------------------------------------------------- login wall

# Reachable without logging in: the login page itself and what it needs.
_OPEN = {"/login", "/api/login", "/api/logout", "/api/first-user", "/api/first-restore",
         "/api/scm/webhook", "/api/whatsapp/webhook", "/api/slack/events",
         "/healthz", "/about", "/privacy", "/terms", "/api/interest",
         "/.well-known/apple-developer-merchantid-domain-association"}

# The public website (ecomvital.com): its own pages; anything else there goes to the app.
SITE_HOSTS = {h.strip().lower() for h in os.getenv("SITE_HOSTS", "ecomvital.com,www.ecomvital.com").split(",") if h.strip()}
_SITE_PAGES = {"/": "home", "/about": "home", "/privacy": "privacy", "/terms": "terms"}


# Each desk's pages and data. Anything not listed (Settings, connections, people,
# backups, store setup) is for owners only.
_DESK_PATHS = {
    "profit": ("/", "/api/dashboard", "/api/history"),
    "cash": ("/cash", "/api/cash"),
    "cog": ("/cog", "/api/cog", "/api/invoices", "/api/suppliers"),
    "inbox": ("/inbox", "/api/inbox"),
    "reports": ("/reports", "/api/reports"),
    "expenses": ("/expenses", "/api/expenses"),
    "scm": ("/scm", "/api/scm"),
    "importer": ("/importer", "/api/importer"),
    "sizecharts": ("/sizecharts", "/api/sizecharts"),
    "disputes": ("/disputes", "/api/disputes"),
    "recordings": ("/recordings", "/api/recordings"),
    "googleads": ("/googleads", "/api/googleads", "/api/ads"),
    "tracking": ("/tracking", "/api/tracking"),
    "checkout": ("/checkout", "/api/checkout"),
    "tasks": ("/tasks", "/api/tasks"),
    "metaads": ("/metaads", "/api/metaads"),
    "agent": ("/agent", "/api/agent"),
    "time": ("/time", "/api/time"),
    "paypaltrack": ("/paypal-tracking", "/api/paypal-tracking"),
}
_DESK_HOME = {"profit": "/", "cash": "/cash", "cog": "/cog", "inbox": "/inbox", "reports": "/reports",
             "expenses": "/expenses", "scm": "/scm", "importer": "/importer",
             "sizecharts": "/sizecharts", "disputes": "/disputes",
             "recordings": "/recordings", "googleads": "/googleads",
             "tracking": "/tracking", "checkout": "/checkout", "tasks": "/tasks", "metaads": "/metaads", "agent": "/agent", "time": "/time",
             "paypaltrack": "/paypal-tracking"}
# Shared by every desk's pages: the user's own details and the dropdown lists.
_EVERYONE = {"/api/me", "/api/me/password", "/api/logout", "/change-password",
             "/api/setup", "/api/chat", "/api/assistant/inbox", "/api/assistant/inbox/read"}
# What "read & write" people may change (owners may change anything).
_WRITE_OK = ("/api/googleads/tags", "/api/cog/pdesk", "/api/invoices", "/api/suppliers/payee", "/api/disputes", "/api/cog/sync", "/api/inbox", "/api/expenses", "/api/scm", "/api/importer", "/api/sizecharts", "/api/tasks", "/api/agent/advice", "/api/agent/questions")
# What anyone with the desk may do, read-only people included (it changes nothing).
_ANY_OK = ("/api/reports",)


def _desk_of(path: str):
    for desk, prefixes in _DESK_PATHS.items():
        for p in prefixes:
            if path == p or (p != "/" and path.startswith(p + "/")) or (p != "/" and path == p):
                return desk
    return None


def _denied(request: Request, user: dict):
    """None if this user may make this request, else the response to send."""
    path, method = request.url.path, request.method
    is_api = path.startswith("/api/")
    if user.get("must_change") and path not in _EVERYONE:
        if is_api:
            return JSONResponse({"error": "Choose your own password first.", "change_password": True},
                                status_code=403)
        return RedirectResponse("/change-password", status_code=303)
    if user.get("role", "owner") == "owner" or path in _EVERYONE:
        return None
    access = auth.access_of(user)
    desk = _desk_of(path)
    if desk is None or desk not in access:
        if is_api:
            return JSONResponse({"error": "You don't have access to that."}, status_code=403)
        first = next((d for d in auth.DESKS if d in access), "")
        return RedirectResponse(_DESK_HOME.get(first, "/change-password"), status_code=303)
    if method in ("GET", "HEAD") or any(path.startswith(p) for p in _ANY_OK):
        return None
    # daily work (Work level) is what _WRITE_OK lists; anything else changing a desk is Manage
    need = "work" if any(path.startswith(p) for p in _WRITE_OK) else "manage"
    if not auth.at_least(user, desk, need):
        return JSONResponse({"error": "Your access is view-only for this." if need == "work"
                             else "That needs Manage access to this desk."}, status_code=403)
    return None


def _need_level(request: Request, desk: str, need: str = "manage"):
    """Inside a route: sensitive actions (refunds, accepting a dispute...) need Manage."""
    if not auth.at_least(getattr(request.state, "user", None) or {}, desk, need):
        raise HTTPException(403, "That needs Manage access to this desk. Ask a manager or the owner.")


def _shopify_launch(request: Request):
    """The app opened from inside a store's Shopify admin (signed by Shopify): if the
    store hasn't granted everything EcomVital asks for, send it to Shopify's approval
    screen. Works whichever Shopify login is used, and needs no EcomVital login."""
    q = dict(request.query_params)
    if request.url.path != "/" or "hmac" not in q or "shop" not in q:
        return None
    domain = shop.normalize_domain(q["shop"])
    keys = _shopify_app(domain)
    if not keys or not shop.verify_hmac(q, keys):
        return None
    store = next((x for x in db.list_stores() if x["shop_domain"] == domain), None)
    have = _scopes.get(store["id"], (0, set()))[1] if store else set()
    have = have | {"read_" + g[6:] for g in have if g.startswith("write_")}
    wanted = set(shop.SCOPES.split(","))
    if store and wanted <= have:
        return None
    state = secrets.token_urlsafe(24)
    _shopify_states[state] = domain
    url = shop.install_url(domain, SHOPIFY_REDIRECT, state, keys)
    # Inside the admin the app may be framed; break out to the top window.
    return HTMLResponse(f'<!doctype html><meta charset="utf-8"><title>EcomVital</title>'
                        f'<p style="font-family:sans-serif">Opening Shopify\'s permission screen…</p>'
                        f'<script>window.top.location.href = {json.dumps(url)};</script>'
                        f'<p><a href="{html.escape(url)}" target="_top">Continue</a></p>')


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in SITE_HOSTS:
        if path in _SITE_PAGES:
            return _legal(_SITE_PAGES[path])
        if not (path.startswith("/static/") or path in ("/api/interest", "/healthz")):
            return RedirectResponse(f"{BASE_URL}{path}", status_code=302)
    user = auth.user_for(request.cookies.get(auth.SESSION_COOKIE))
    request.state.user = user
    if path in _OPEN or path.startswith(("/static/", "/proxy/", "/apps/", "/w/", "/t/", "/pay/", "/api/time/client/")) or path == "/proxy":
        return await call_next(request)
    launched = _shopify_launch(request)
    if launched:
        return launched
    if user:
        blocked = _denied(request, user)
        return blocked or await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "Please log in again.", "login": True}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    from urllib.parse import quote
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def _set_session(response, request: Request, token: str):
    secure = (request.url.scheme == "https"
              or request.headers.get("x-forwarded-proto") == "https"
              or BASE_URL.startswith("https://"))
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=secure, path="/")


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def _legal(name):
    with open(os.path.join(STATIC_DIR, "legal", f"{name}.html")) as f:
        return HTMLResponse(f.read())


# Public pages Google asks for before an OAuth app can be published.
@app.get("/about", response_class=HTMLResponse)
def about_page():
    return _legal("home")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    return _legal("privacy")


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    return _legal("terms")


@app.get("/.well-known/apple-developer-merchantid-domain-association")
def apple_pay_domain_file():
    """Apple Pay domain verification for the checkout (Airwallex's file, Payments → Apple Pay → web domains)."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "applepay",
                           "apple-developer-merchantid-domain-association"), "rb") as f:
        return Response(f.read(), media_type="application/octet-stream")


_interest_hits: dict = defaultdict(list)


@app.post("/api/interest")
async def api_interest(request: Request, payload: dict):
    """ecomvital.com "Register your interest" (public). Bots fill the hidden field; a few tries per hour per address."""
    if payload.get("website"):
        return {"ok": True}
    ip = _client_ip(request)
    now = time.time()
    _interest_hits[ip] = [t for t in _interest_hits[ip] if now - t < 3600] + [now]
    if len(_interest_hits[ip]) > 5:
        raise HTTPException(429, "Too many tries. Please try again later.")
    clip = lambda k, n=200: str(payload.get(k) or "").strip()[:n]
    if "@" not in clip("email") or not clip("name"):
        raise HTTPException(400, "Please add your name and email.")
    with db._conn() as con:
        con.execute("CREATE TABLE IF NOT EXISTS site_interest (id INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT, email TEXT,"
                    " store TEXT, stores TEXT, spend TEXT, note TEXT, ip TEXT)")
        con.execute("INSERT INTO site_interest (at, name, email, store, stores, spend, note, ip) VALUES (?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), clip("name"), clip("email"), clip("store"),
                     clip("stores", 20), clip("spend", 30), clip("note", 2000), ip))
    return {"ok": True}


@app.get("/interest", response_class=HTMLResponse)
def interest_page():
    """Owner: who registered interest on ecomvital.com."""
    with db._conn() as con:
        con.execute("CREATE TABLE IF NOT EXISTS site_interest (id INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT, email TEXT,"
                    " store TEXT, stores TEXT, spend TEXT, note TEXT, ip TEXT)")
        rows = [dict(r) for r in con.execute("SELECT * FROM site_interest ORDER BY id DESC")]
    e = html.escape
    body = "".join(f"<tr><td>{e(r['at'][:16].replace('T', ' '))}</td><td>{e(r['name'])}</td><td><a href='mailto:{e(r['email'])}'>{e(r['email'])}</a></td>"
                   f"<td>{e(r['store'])}</td><td>{e(r['stores'])}</td><td>{e(r['spend'])}</td><td>{e(r['note'])}</td></tr>" for r in rows)
    return HTMLResponse("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                        "<title>Beta interest</title><link rel='stylesheet' href='/static/legal/site.css'><div class='wrap legal'>"
                        f"<h1>Beta interest</h1><p class='meta'>{len(rows)} sign-ups from ecomvital.com</p><table><tr><th>When (UTC)</th><th>Name</th>"
                        f"<th>Email</th><th>Store</th><th>Stores</th><th>Ad spend</th><th>Note</th></tr>{body}</table></div>")


@app.get("/healthz")
def healthz():
    """Up check for Railway, plus where the database lives (paths only) so a
    missing volume is easy to spot."""
    vol = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
    path = os.path.abspath(db.DB_PATH)
    return {"ok": True, "db_path": path, "volume_mount": vol or None,
            "db_on_volume": bool(vol) and path.startswith(vol.rstrip("/") + "/"),
            "has_accounts": auth.has_users(),
            "google_requests_today": gads.ops_today(), "google_paused": gads.busy(),
            "stalls_10min": [x[:2] for x in _stalls if x[0] >= (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")],
            "version": (os.getenv("RAILWAY_GIT_COMMIT_SHA") or "")[:7] or None}


@app.get("/login", response_class=HTMLResponse)
def login_page():
    with open(os.path.join(STATIC_DIR, "login.html")) as f:
        html = f.read()
    stamp = _stamp("styles.css")
    html = html.replace("/static/styles.css", f"/static/styles.css?v={stamp}")
    html = html.replace("__FIRST_RUN__", "true" if not auth.has_users() else "false")
    html = html.replace("__NEEDS_CODE__",
                        "true" if SETUP_CODE or os.getenv("RAILWAY_ENVIRONMENT") else "false")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def _check_setup_code(code: str):
    if os.getenv("RAILWAY_ENVIRONMENT") and not SETUP_CODE:
        # A public server with no accounts must never be claimable by whoever
        # finds it first.
        raise HTTPException(400, "Setup is locked. Add a SETUP_CODE variable in Railway first.")
    if SETUP_CODE and not secrets.compare_digest(code or "", SETUP_CODE):
        raise HTTPException(400, "That setup code isn't right.")


@app.post("/api/login")
def api_login(payload: dict, request: Request):
    try:
        token = auth.log_in(payload.get("email"), payload.get("password"), _client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    _set_session(resp, request, token)
    return resp


@app.post("/api/first-user")
def api_first_user(payload: dict, request: Request):
    """Create the owner account. Only works while there are no accounts at all."""
    if auth.has_users():
        raise HTTPException(400, "EcomVital already has an owner. Log in instead.")
    _check_setup_code(payload.get("setup_code"))
    try:
        auth.create_user(payload.get("email"), payload.get("name"), payload.get("password"), "owner")
        token = auth.log_in(payload.get("email"), payload.get("password"), _client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    _set_session(resp, request, token)
    return resp


_MAX_BACKUP = 50 * 1024 * 1024


@app.post("/api/first-restore")
async def api_first_restore(request: Request):
    """Fill a brand-new EcomVital from a backup, before any account exists.
    Needs the setup code; afterwards you log in with the accounts in the backup."""
    if auth.has_users():
        raise HTTPException(400, "EcomVital already has accounts. Log in, then restore "
                                 "from Settings → Backup.")
    _check_setup_code(request.headers.get("x-setup-code"))
    return await _restore(request)


@app.post("/api/restore")
async def api_restore(request: Request):
    """Replace everything with a backup (logged in)."""
    return await _restore(request)


async def _restore(request: Request):
    data = await request.body()
    if not data or len(data) > _MAX_BACKUP:
        raise HTTPException(400, "Choose a EcomVital backup file (.db) to upload.")
    try:
        db.restore_bytes(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _cache.clear()
    _plans.clear()
    resp = JSONResponse({"ok": True, "stores": len(db.list_stores())})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")  # log in again with the backup's accounts
    return resp


@app.get("/api/backup")
def api_backup():
    """Download the whole database: stores, connections, settings and accounts."""
    from fastapi.responses import Response
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    return Response(db.backup_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="profitdesk-backup-{stamp}.db"',
                             "Cache-Control": "no-store"})


@app.post("/api/logout")
def api_logout(request: Request):
    auth.log_out(request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/api/me")
def api_me(request: Request):
    """Who's logged in and what they may use (to show only their desks)."""
    me = dict(request.state.user)
    me["access"] = auth.access_of(me)
    me["desks"] = [d for d in auth.DESKS if d in me["access"]]
    return me


@app.get("/api/users")
def api_users(request: Request):
    users = db.list_users()
    for u in users:
        u["access"] = auth.access_of(u)
        u["desks"] = [d for d in auth.DESKS if d in u["access"]]
    return {"me": request.state.user, "users": users}


@app.post("/api/users")
def api_add_user(payload: dict):
    """Add a person with a temporary password they must change at first login."""
    first = ((payload.get("name") or "").strip().split() or [""])[0]
    if not first:
        raise HTTPException(400, "Type their first name: it's how the team sees them (e.g. on Inbox tickets).")
    payload = {**payload, "name": first[:40]}
    try:
        uid = auth.create_user(payload.get("email"), payload.get("name"), payload.get("password"),
                               payload.get("role"), payload.get("desks"), temporary=True,
                               access=payload.get("access"))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return {"id": uid}


@app.put("/api/users/{user_id}")
def api_update_user(user_id: int, payload: dict, request: Request):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(404, "That person no longer exists.")
    try:
        role, desks, access = auth.clean_access(
            payload.get("role", user["role"]), payload.get("desks", (user["desks"] or "").split(",")),
            payload.get("access") if "access" in payload or payload.get("role") == "owner"
            else (auth.access_of(user) if user["role"] != "owner" else None))
        if user_id == request.state.user["id"] and role != "owner":
            raise auth.AuthError("You can't take owner access away from yourself.")
        if user["role"] == "owner" and role != "owner" and \
                sum(1 for u in db.list_users() if u["role"] == "owner") <= 1:
            raise auth.AuthError("EcomVital needs at least one owner.")
        db.set_user_access(user_id, role, desks, (payload.get("name") or "").strip() or None, access)
        if payload.get("password"):
            auth.reset_password(user_id, payload["password"])
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def api_remove_user(user_id: int, request: Request):
    if user_id == request.state.user["id"]:
        raise HTTPException(400, "You can't remove your own account while logged in with it.")
    db.delete_user(user_id)
    return {"ok": True}


@app.get("/change-password", response_class=HTMLResponse)
def change_password_page():
    return _page("change.html", ("styles.css",))


@app.post("/api/me/password")
def api_change_password(payload: dict, request: Request):
    me = request.state.user
    try:
        auth.change_password(me["id"], payload.get("current"), payload.get("new"))
        token = auth.log_in(me["email"], payload.get("new"), _client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    _set_session(resp, request, token)  # this device stays logged in; others are signed out
    return resp


@app.exception_handler(HTTPException)
async def _http_err(request, exc):
    # Same shape as every other error, so the dashboard can show the message.
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(ShopifyError)
async def _shopify_err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(gads.GoogleAdsError)
async def _gads_err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(meta.MetaAdsError)
async def _meta_err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(fx.FxError)
async def _fx_err(request, exc):
    return JSONResponse({"error": f"{exc} Figures can't be converted right now; "
                                  "try Refresh in a minute."}, status_code=503)


@app.exception_handler(gsheet.SheetError)
async def _sheet_err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(awx.AirwallexError)
async def _awx_err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(db.AlreadyLinked)
async def _linked_err(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:400]}, status_code=500)


def _shopify_app(domain: str):
    """This store's own app keys, falling back to the app in .env."""
    row = db.get_shopify_app(domain)
    return (row["client_id"], row["client_secret"]) if row else shop.default_app()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """The dashboard page, with each script and stylesheet stamped by its edit
    time so a browser never keeps running an old copy after an update.

    After a merchant installs from a custom install link, Shopify lands them
    here with ?shop=...&hmac=... . When that checks out, carry straight on to
    the connection step so the store finishes connecting by itself.
    """
    params = dict(request.query_params)
    if params.get("shop") and params.get("hmac"):
        try:
            domain = shop.normalize_domain(params["shop"])
        except ShopifyError:
            domain = None
        if domain and shop.verify_hmac(params, _shopify_app(domain)):
            return RedirectResponse(f"/auth/shopify/start?shop_domain={domain}")

    return _page("index.html", ("app.js", "nav.js", "styles.css", "skin.css", "chat.js"))


_stamps: dict = {}


def _stamp(name: str) -> str:
    """Short fingerprint of a static file's contents. (Edit times aren't reliable on
    Railway: a deploy can keep the old time, and browsers then keep the old file.)"""
    path = os.path.join(STATIC_DIR, name)
    st = os.stat(path)
    key = (name, st.st_mtime_ns, st.st_size)
    if key not in _stamps:
        import hashlib
        with open(path, "rb") as f:
            _stamps[key] = hashlib.md5(f.read()).hexdigest()[:10]
    return _stamps[key]


def _page(filename: str, assets) -> HTMLResponse:
    """Serve a page with each script and stylesheet stamped by its edit time,
    so a browser never keeps running an old copy after an update."""
    with open(os.path.join(STATIC_DIR, filename)) as f:
        html = f.read()
    for name in assets:
        stamp = _stamp(name)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- setup state

@app.get("/cog", response_class=HTMLResponse)
def cog_page():
    return _page("cog.html", ("cog.js", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.put("/api/cog/sheet")
async def api_cog_sheet(payload: dict):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", payload.get("url") or "")
    sid = m.group(1) if m else (payload.get("url") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", sid):
        raise HTTPException(400, "Paste the orders sheet's link from the browser's address bar.")
    db.set_setting("cog_sheet_id", sid)
    rows = await _cog_rows(force=True)
    asyncio.create_task(_cog_sync())
    return {"ok": True, "rows": len(rows)}


@app.post("/api/cog/sync")
async def api_cog_sync(days: int = None):
    """Re-read the sheet and re-cost orders now (days=None: the whole window)."""
    await _cog_rows(force=True)
    if days is None:
        asyncio.create_task(_cog_sync())
        return {"ok": True, "started": True}
    return {"ok": True, "report": await _cog_sync(days_back=days)}


@app.get("/api/cog/status")
def api_cog_status():
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    tags = _cog.get("tags") or {}
    rows = (_cog.get("rows") or (0, []))[1]
    catalog = _cog.get("catalog")
    synced = _cog.get("synced")
    real = [x for x in db.list_stores() if not demo.is_demo(x)]
    return {
        "today": max((_store_today(x) for x in real), default=date.today()).isoformat(),
        "stores": [{"id": x["id"], "name": x["name"].strip()} for x in real],
        "sheet": bool(db.get_setting("cog_sheet_id")),
        "sheet_rows": len(rows),
        "tags": sorted([{"tag": (p or "") + "…" + (q or "") if q else (p or "") + "…",
                         "store": stores.get(sid, "?")} for (p, q), sid in tags.items()],
                       key=lambda t: t["store"]),
        "not_connected": dict(catalog.unmatched_tags) if catalog else {},
        "synced_at": synced[0] if synced else None,
        "report": synced[1] if synced else None,
        "fees_last_14_days": _fee_coverage(),
    }


def _fee_coverage() -> dict:
    """Per store and payment method: how many recent payments have their actual fee."""
    start = (date.today() - timedelta(days=14)).isoformat()
    names = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        rows = con.execute(
            "SELECT store_id, gateway, COUNT(*) n, SUM(fee_actual IS NOT NULL) a,"
            " SUM(COALESCE(fee_actual, 0)) fa, SUM(CASE WHEN fee_actual IS NOT NULL THEN fee_est ELSE 0 END) fe"
            " FROM order_fees WHERE day >= ? GROUP BY store_id, gateway", (start,)).fetchall()
    return [{"store": names.get(r["store_id"], "?"), "gateway": r["gateway"], "payments": r["n"],
             "actual": r["a"], "actual_fees": round(r["fa"], 2), "estimate_for_same": round(r["fe"], 2)}
            for r in rows]


@app.get("/api/cog/day")
async def api_cog_day(start: str, end: str = None, store: str = "all", currency: str = None):
    """Every order in a day (or range) in time order, each product line with its COG."""
    end = end or start
    stores = [x for x in db.list_stores() if not demo.is_demo(x)
              and (store == "all" or str(x["id"]) == str(store))]
    base = (currency or _display_currency()).upper()
    table = await fx.table(base)
    f = lambda code: fx.factor(table, code, base) if table else 1.0
    names = {x["id"]: (x["name"].strip(), x["currency"]) for x in stores}
    orders = {}
    for r in db.cog_lines(list(names), start, end):
        name, cur = names[r["store_id"]]
        o = orders.setdefault((r["store_id"], r["order_name"]), {
            "store": name, "order": r["order_name"], "created": r["created"], "day": r["day"],
            "cancelled": bool(r["cancelled"]), "fulfillment": r["fulfillment"], "lines": [],
            "sales": 0.0, "cog": 0.0, "estimated": False, "items": 0, "singles": 0.0, "singles_known": True})
        cost = (r["cost_usd"] or 0.0) * f("USD")
        if not r["cancelled"]:
            o["items"] += r["quantity"] or 1
            if r.get("single_usd") is None:
                o["singles_known"] = False
            else:
                o["singles"] += r["single_usd"] * f("USD")
        sales = 0.0 if r["cancelled"] else r["subtotal"] * f(cur)
        o["lines"].append({"product": r["product"], "quantity": r["quantity"], "sku": r["sku"],
                           "sales": sales, "cog": cost, "cog_usd": r["cost_usd"],
                           "source": r["source"], "supplier": r["supplier"], "note": r["note"]})
        o["sales"] += sales
        o["cog"] += cost
        o["estimated"] = o["estimated"] or r["source"] == "estimate"
    out = sorted(orders.values(), key=lambda o: datetime.fromisoformat(o["created"]))
    for o in out:
        o["cog_pct"] = (o["cog"] / o["sales"]) if o["sales"] else None
        # 2+ items: what they'd cost bought one by one, and the supplier's bundle discount
        if o["items"] >= 2 and o["singles_known"] and o["singles"] > 0:
            o["bundle"] = {"singles": o["singles"], "discount": o["singles"] - o["cog"],
                           "estimated": any(l["source"] != "invoice" for l in o["lines"])}
        del o["singles"], o["singles_known"]
    sales = sum(o["sales"] for o in out)
    cogs = sum(o["cog"] for o in out)
    est = [l for o in out for l in o["lines"] if l["source"] == "estimate"]
    return {
        "currency": base, "start": start, "end": end, "orders": out,
        "summary": {"orders": len(out), "sales": sales, "cog": cogs,
                    "cog_pct": (cogs / sales) if sales else None,
                    "estimated_lines": len(est), "estimated_cog": sum(l["cog"] for l in est),
                    "from_history": sum(1 for o in out for l in o["lines"] if l["source"] == "catalog"),
                    "from_invoice": sum(1 for o in out for l in o["lines"] if l["source"] == "invoice")},
        "synced_at": (_cog.get("synced") or (None,))[0],
    }


# ------------------------------------------------ COG desk: Check orders + Invoices by date (cog_desk.py)

_cogdesk_ready = {"ok": False}


def _cogdesk_db():
    """Team answers / notes per order, notes per invoice day, and paid marks on invoices."""
    if _cogdesk_ready["ok"]:
        return
    _invoice_notes_db()
    with db._conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS cog_order_marks (
                store_id   INTEGER NOT NULL,
                order_name TEXT    NOT NULL,
                answer     TEXT,              -- bundle | real | review | note
                note       TEXT,
                by         TEXT,
                at         TEXT,
                PRIMARY KEY (store_id, order_name)
            );
            CREATE TABLE IF NOT EXISTS invoice_day_notes (
                day  TEXT PRIMARY KEY,
                note TEXT,
                by   TEXT,
                at   TEXT
            );""")
        have = {r[1] for r in con.execute("PRAGMA table_info(invoices)")}
        for col in ("paid INTEGER", "paid_by TEXT", "paid_at TEXT"):
            if col.split()[0] not in have:
                con.execute(f"ALTER TABLE invoices ADD COLUMN {col}")
    _cogdesk_ready["ok"] = True


def _cog_cutoff() -> str:
    return db.get_setting("invoice_paid_cutoff") or cog_desk.CUTOFF_DEFAULT


def _cog_marks(keys=None) -> dict:
    _cogdesk_db()
    with db._conn() as con:
        return {(r["store_id"], r["order_name"]): dict(r) for r in con.execute("SELECT * FROM cog_order_marks")}


def _invoice_map() -> dict:
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    return {i["id"]: {**i, "store": stores.get(i["store_id"], "Several stores")} for i in db.list_invoices()}


@app.get("/api/cog/orders")
async def api_cog_orders(start: str, end: str = None, store: str = "all"):
    """Check orders: every order in the period, its items and cost (USD), where the cost comes from, paid or not."""
    _cogdesk_db()
    end = end or start
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and (store == "all" or str(x["id"]) == str(store))]
    cur = {x["id"]: x["currency"] for x in stores}
    table = await fx.table("USD")
    sale_usd = lambda sid, v: v * (fx.factor(table, cur.get(sid) or "USD", "USD") if table else 1.0)
    lines = db.cog_lines(list(cur), start, end)
    keys = {(r["store_id"], cog.order_key(r["order_name"])) for r in lines}
    inv_by_order = defaultdict(list)
    for l in db.invoice_lines():
        k = (l["store_id"], cog.order_key(l["order_key"]))
        if k in keys:
            inv_by_order[k].append(l)
    names = {r["order_name"] for r in lines}
    with db._conn() as con:
        fulfilled = {(r[0], r[1]) for r in con.execute(
            "SELECT DISTINCT store_id, order_name FROM shipments WHERE fulfilled_at IS NOT NULL AND fulfilled_at != ''"
            f" AND order_name IN ({','.join('?' * len(names)) or "''"})", tuple(names))} if names else set()
    imap = _invoice_map()
    lines_of = defaultdict(list)
    for l in db.invoice_lines():
        lines_of[l["invoice_id"]].append(l)
    all_inv = [l for ls in lines_of.values() for l in ls]
    shares = cog_desk.supplier_shares(all_inv)
    rows = cog_desk.order_rows(lines, inv_by_order, imap, fulfilled, _cog_marks(), sale_usd, _cog_cutoff(),
                               {x["id"]: x["name"].strip() for x in stores}, cog_desk.billed_up_to(imap, lines_of),
                               _usual_suppliers(), shares)
    count = defaultdict(int)
    for o in rows:
        count[o["kind"]] += 1
    return {"start": start, "end": end, "orders": rows, "counts": count, "cutoff": _cog_cutoff(),
            "sale": round(sum(o["sale"] for o in rows), 2), "cog": round(sum(o["cog"] for o in rows), 2)}


def _review_channel(supplier: str):
    """The supplier's invoice channel in Slack (the Agent desk gives each channel a role and a supplier)."""
    if not db.get_setting("slack_bot_token"):
        return None
    _agent_db()
    s = agent.norm(supplier)
    with db._conn() as con:
        chans = [dict(r) for r in con.execute("SELECT * FROM slack_channels WHERE role = 'supplier_invoice'")]
    for ch in chans:
        n = agent.norm(ch.get("supplier") or "") or agent.norm(ch.get("name"))
        if s and (s in n or n in s or s in agent.norm(ch.get("name"))):
            return ch
    return None


def _usd(v) -> str:
    return f"US${(v or 0):,.2f}"


@app.get("/api/cog/compare")
def api_cog_compare(store_id: int, order: str):
    """This order's invoice next to the last invoice with the same product in the same store, plus a hint and
    the message to send the supplier if it looks wrong."""
    _cogdesk_db()
    key = cog.order_key(order)
    every = [l for l in db.invoice_lines() if l["store_id"] == store_id]
    mine = [l for l in every if cog.order_key(l["order_key"]) == key and l["status"] not in cog_desk.DROP]
    if not mine:
        raise HTTPException(404, "This order isn't on an invoice yet.")
    cmp = cog_desk.compare(mine, every)
    with db._conn() as con:
        items = con.execute("SELECT COALESCE(SUM(quantity), 0) FROM cog_lines WHERE store_id = ? AND order_name = ?"
                            " AND cancelled = 0", (store_id, order)).fetchone()[0]
    rows = len(mine)
    units = sum(l.get("units") or 1 for l in mine)
    changed = [l for l in mine if l["status"] == "changed"]
    hint = ""
    if items >= 2 and rows < items:
        hint = f"Shopify says this order has {items} items, the invoice has {rows} line{'s' if rows > 1 else ''}. Likely a bundle."
    elif items >= 2:
        hint = f"{items} items in this order: suppliers often give a bundle price on 2+ items."
    elif changed:
        old, new = cog_desk._change(changed[0])
        if old is not None:
            gap = abs(new - old)
            hint = (f"A {_usd(gap)} change on one item: usually a real supplier price change." if gap <= PRICE_GAP
                    else f"A {_usd(gap)} gap on one item is big for a price change: check the quantity.")
    sup = mine[0].get("supplier") or "supplier"
    first = changed[0] if changed else mine[0]
    last = cmp["last"]
    text = (f"Hi {sup} team,\n\nCould you check this line on the {first.get('date') or ''} invoice #{first.get('number') or '—'}?\n"
            f"• {order} {first.get('product') or first.get('title')}: billed {_usd(first['amount'])}"
            + (f", last time {_usd(last['total'])} for the same item ({last['lines'][0]['order']})" if last else "")
            + ".\n\nIs this a new price, or should it be corrected? Thank you!")
    ch = _review_channel(sup)
    return {**cmp, "hint": hint, "items": items, "units": units, "supplier": sup,
            "review_text": text, "channel": ch and ch["name"],
            "lines": [[l["invoice_id"], l["line_no"]] for l in changed]}


def _order_lines(store_id: int, order: str) -> list:
    key = cog.order_key(order)
    return [l for l in db.invoice_lines() if l["store_id"] == store_id and cog.order_key(l["order_key"]) == key
            and l["status"] not in cog_desk.DROP]


def _set_mark(store_id: int, order: str, who: str, **cols):
    _cogdesk_db()
    with db._conn() as con:
        con.execute("INSERT OR IGNORE INTO cog_order_marks (store_id, order_name) VALUES (?, ?)", (store_id, order))
        for k, v in cols.items():
            con.execute(f"UPDATE cog_order_marks SET {k} = ? WHERE store_id = ? AND order_name = ?", (v, store_id, order))
        con.execute("UPDATE cog_order_marks SET by = ?, at = ? WHERE store_id = ? AND order_name = ?",
                    (who, agent.now(), store_id, order))


@app.post("/api/invoices/orders/answer")
async def api_cog_order_answer(payload: dict, request: Request):
    """What a flagged order is: bundle (billed together, not a price rise), real (new price from now on),
    review (asked the supplier), note (explained in words), clear (undo). Every answer is kept as a lesson."""
    store_id, order = int(payload.get("store_id") or 0), str(payload.get("order") or "")
    answer = payload.get("answer")
    if answer not in ("bundle", "real", "review", "note", "clear") or not store_id or not order:
        raise HTTPException(400, "Pick an answer.")
    who = _who(request)
    lines = _order_lines(store_id, order)
    changed = [[l["invoice_id"], l["line_no"]] for l in lines if l["status"] == "changed"]
    store = next((x["name"].strip() for x in db.list_stores() if x["id"] == store_id), "?")
    products = " + ".join(dict.fromkeys((l.get("product") or l.get("title") or "")[:60] for l in lines)) or "this product"
    lesson = ""
    if answer == "clear":
        with db._conn() as con:
            for iid, no in changed:
                con.execute("UPDATE invoice_lines SET check_state = NULL, check_by = NULL, check_at = NULL"
                            " WHERE invoice_id = ? AND line_no = ? AND check_state = 'bundle'", (iid, no))
        _set_mark(store_id, order, who, answer=None)
        _note_lesson(f"answer:{store_id}:{order}", "", who)
        await _recost(store_id, {order})
        return {"ok": True}
    if answer == "bundle":
        with db._conn() as con:
            for iid, no in changed:
                con.execute("UPDATE invoice_lines SET check_state = 'bundle', check_by = ?, check_at = datetime('now')"
                            " WHERE invoice_id = ? AND line_no = ?", (who, iid, no))
            items = con.execute("SELECT COALESCE(SUM(quantity), 0) FROM cog_lines WHERE store_id = ? AND order_name = ?"
                                " AND cancelled = 0", (store_id, order)).fetchone()[0]
        lesson = (f"When {store} sells “{products}” in a {items}-item order, the supplier bills it as a bundle "
                  f"(order {order}: {_usd(sum(l['amount'] for l in lines))} for {items}). That's a bundle or BOGO, not a price rise.")
        await _recost(store_id, {order})
    elif answer == "real":
        if changed:
            await _decide_prices("use_new", changed, who)
        lesson = f"{store}: the new price of “{products}” on order {order} is a real supplier price change (accepted)."
    elif answer == "review":
        if changed:
            await _decide_prices("checking", changed, who)
        text = str(payload.get("text") or "").strip()
        posted = None
        if text and payload.get("post"):
            sup = (lines[0].get("supplier") if lines else "") or ""
            ch = _review_channel(sup)
            if not ch:
                raise HTTPException(400, f"No Slack invoice channel for {sup or 'this supplier'} yet (Agent desk → Slack: "
                                         "give the channel the role “Supplier invoices” and the supplier's name).")
            try:
                await slack_client.post(db.get_setting("slack_bot_token"), ch["id"], text[:3500])
            except Exception as e:
                raise HTTPException(400, f"Slack didn't take the message: {str(e)[:200]}")
            posted = ch["name"]
        _set_mark(store_id, order, who, answer="review")
        return {"ok": True, "posted": posted}
    note = str(payload.get("note") or "").strip()[:1500]
    if answer == "note":
        if not note:
            raise HTTPException(400, "Write the note first.")
        lesson = f"Order {order} ({store}, {products}): {note}"
        _set_mark(store_id, order, who, answer="note", note=note)
    else:
        _set_mark(store_id, order, who, answer=answer)
    _note_lesson(f"answer:{store_id}:{order}", lesson, who)
    return {"ok": True, "lesson": lesson}


@app.post("/api/invoices/orders/note")
def api_cog_order_note(payload: dict, request: Request):
    """A note on an order, for the team; the AI reads it as a lesson for orders like it."""
    store_id, order = int(payload.get("store_id") or 0), str(payload.get("order") or "")
    if not store_id or not order:
        raise HTTPException(400, "Which order?")
    note = str(payload.get("note") or "").strip()[:1500]
    who = _who(request)
    _set_mark(store_id, order, who, note=note or None)
    store = next((x["name"].strip() for x in db.list_stores() if x["id"] == store_id), "?")
    with db._conn() as con:
        prods = [r[0] for r in con.execute("SELECT product FROM cog_lines WHERE store_id = ? AND order_name = ?", (store_id, order))]
    _note_lesson(f"ordernote:{store_id}:{order}", f"Order {order} ({store}, {' + '.join(prods)[:200]}): {note}" if note else "", who)
    return {"ok": True}


AI_CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["orders"], "properties": {"orders": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["store_id", "order", "verdict", "tone", "why", "fix"],
                               "properties": {"store_id": {"type": "integer"}, "order": {"type": "string"},
                                              "verdict": {"type": "string"}, "tone": {"type": "string", "enum": ["good", "warn", "bad"]},
                                              "why": {"type": "string"},
                                              "fix": {"type": "string", "enum": ["bundle", "real", "review", "none"]}}}}}}
AI_CHECK_SYSTEM = """You check the cost of goods (COG) of Shopify orders for a dropshipping business. Suppliers (Dayone, Graypop …)
bill in USD per order. For each order you get its items, what it sold for, what it cost, where the cost comes from, its invoice
lines with the previous price for the product, and the last earlier invoice for the same product in the same store.
Know this:
- Suppliers give a bundle discount on orders with 2+ items, and may bill 2 items as one line, or a Buy 1 Get 1 (BOGO) as 2 rows
  or as 1 row. A cost that went up because the order has more items is a bundle, not a price rise (fix "bundle").
- A small rise on a 1-item order (up to about US$2.50 per item) is usually a real supplier price change; if nothing in the
  lessons says the supplier announced it, suggest asking them (fix "review"). If the team's lessons say it's real, fix "real".
- A big gap on one item is more likely a quantity / bundle mix-up than a price change.
- Estimated orders (not invoiced yet): say if the estimate looks low or high against recent invoices; no fix.
- Follow the team's lessons and notes: they are the owner's rules.
Return one entry per order: a short verdict (max 6 words), tone (good = looks right, warn = worth a look, bad = likely wrong),
why in 1-2 plain sentences with the numbers, and the fix to offer (none if nothing to do). Never invent figures."""


@app.post("/api/invoices/orders/ai")
async def api_cog_orders_ai(payload: dict, request: Request):
    """AI check of one or more orders (Sonnet): verdict, why and a fix the team can apply. Changes nothing itself."""
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the AI key first (Settings → AI).")
    want = [(int(s), str(o)) for s, o in (payload.get("orders") or [])][:40]
    if not want:
        raise HTTPException(400, "Which orders?")
    days = [d for d in (payload.get("start"), payload.get("end")) if d]
    start, end = (min(days), max(days)) if days else ("0000", "9999")
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            f"SELECT * FROM cog_lines WHERE ({' OR '.join(['(store_id = ? AND order_name = ?)'] * len(want))})",
            tuple(x for w in want for x in w))]
    every = db.invoice_lines()
    stores = {x["id"]: (x["name"].strip(), x["currency"]) for x in db.list_stores()}
    table = await fx.table("USD")
    sale_usd = lambda sid, v: v * (fx.factor(table, stores.get(sid, ("", "USD"))[1], "USD") if table else 1.0)
    keys = {(r["store_id"], cog.order_key(r["order_name"])) for r in rows}
    inv_by_order = defaultdict(list)
    for l in every:
        k = (l["store_id"], cog.order_key(l["order_key"]))
        if k in keys:
            inv_by_order[k].append(l)
    orders = cog_desk.order_rows(rows, inv_by_order, _invoice_map(), set(), _cog_marks(), sale_usd, _cog_cutoff(),
                                 {k: v[0] for k, v in stores.items()})
    ctx = []
    for o in orders:
        mine = [l for l in inv_by_order.get((o["store_id"], cog.order_key(o["order"])), []) if l["status"] not in cog_desk.DROP]
        cmp = cog_desk.compare(mine, [l for l in every if l["store_id"] == o["store_id"]]) if mine else None
        ctx.append({"store_id": o["store_id"], "store": o["store"], "order": o["order"], "items_in_order": o["count"],
                    "sale_usd": o["sale"], "cog_usd": o["cog"], "cog_pct": o["cog_pct"], "where_cost_comes_from": o["kind"],
                    "change_pct": o["pct"], "bundle_discount_usd": o["bundle_discount"], "team_note": o["note"],
                    "items": [{"product": i["product"], "qty": i["quantity"], "cost_usd": round(i["cost"], 2),
                               "single_price_usd": i["single"], "source": i["source"]} for i in o["items"]],
                    "this_invoice": cmp and cmp["this"], "last_invoice_same_product": cmp and cmp["last"]})
    try:
        _agent_db()
        lessons = [l["text"] for l in agent.lessons("invoices")][-80:]
    except Exception:
        lessons = []
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=180)
    try:
        resp = await client.messages.create(
            model="claude-sonnet-5-5", max_tokens=6000, system=AI_CHECK_SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": AI_CHECK_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps({"team_lessons": lessons, "orders": ctx}, ensure_ascii=False, default=str)}])
    except anthropic.APIError as e:
        raise HTTPException(400, f"The AI couldn't answer ({type(e).__name__}). Try again in a minute.")
    got = json.loads(next((b.text for b in resp.content if b.type == "text"), "{}") or "{}")
    return {"orders": got.get("orders") or []}


_usual_cache = {}


def _usual_suppliers() -> dict:
    """{store id: the supplier most of its costed lines come from} (lines with no supplier go to it)."""
    hit = _usual_cache.get("v")
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT store_id, supplier FROM cog_lines WHERE supplier IS NOT NULL AND supplier != ''"
                                             " AND day >= date('now', '-90 day')")]
    v = suppliers.default_suppliers(rows)
    _usual_cache["v"] = (time.time(), v)
    return v


PLACED_DAYS = 30      # orders not shipped yet count as owed from the day they're placed, for this long


def _fulfilled_rows(since_day: date) -> list:
    """suppliers.fulfilled_orders for orders since a day (invoiced or not): shipped ones from their fulfilment day, and
    (owner, 17 Oct 2026) orders placed in the last PLACED_DAYS but not shipped yet from the day they were placed, so a
    new order's COG shows in suppliers payable straight away. Rows carry "shipped"."""
    with db._conn() as con:
        lines = [dict(r) for r in con.execute(
            "SELECT store_id, order_name, supplier, source, cost_usd, quantity, cancelled, created FROM cog_lines WHERE day >= ?",
            ((since_day - timedelta(days=45)).isoformat(),))]
        fulfilled = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT store_id, order_name, MIN(fulfilled_at) FROM shipments WHERE fulfilled_at IS NOT NULL"
            " AND fulfilled_at != '' GROUP BY store_id, order_name")}
    shipped = set(fulfilled)
    recent = (datetime.now(timezone.utc) - timedelta(days=PLACED_DAYS)).isoformat()
    for l in lines:
        k = (l["store_id"], l["order_name"])
        if k not in fulfilled and not l["cancelled"] and (l.get("created") or "") >= recent[:10]:
            fulfilled[k] = l["created"]
    inv_lines = db.invoice_lines()
    on_invoice = {(l["store_id"], cog.order_key(l["order_key"])) for l in inv_lines
                  if l["status"] not in cog_desk.DROP and l.get("ai_check") != "review"}
    for l in lines:
        l["on_invoice"] = (l["store_id"], cog.order_key(l["order_name"])) in on_invoice
    cog_desk.set_suppliers(lines, inv_lines, cog_desk.supplier_shares(inv_lines))     # cross-fulfilled orders
    rows = suppliers.fulfilled_orders(lines, fulfilled, CASH_TZ)
    for o in rows:
        o["shipped"] = (o["store_id"], o["order"]) in shipped
    return rows


_payable_cache = {}


def _supplier_payable(supplier: str = "", fresh: bool = False) -> dict:
    """On credit (invoiced, not marked paid, after the cut-off) + estimated (shipped, not invoiced yet)."""
    hit = _payable_cache.get(supplier)
    if hit and not fresh and time.time() - hit[0] < 60:
        return hit[1]
    _cogdesk_db()
    cutoff = _cog_cutoff()
    lines_of = defaultdict(list)
    for l in db.invoice_lines():
        lines_of[l["invoice_id"]].append(l)
    imap = _invoice_map()
    invs = cog_desk.by_day(list(imap.values()), lines_of, cutoff, supplier)
    credit = round(sum(i["total"] for i in invs if not i["paid"]), 2)
    est = cog_desk.not_invoiced(_fulfilled_rows(date.fromisoformat(cutoff)), cutoff, supplier,
                                cog_desk.billed_up_to(imap, lines_of))
    out = {"cutoff": cutoff, "credit": credit, "estimated": est["total"], "estimated_orders": est["orders"],
           "billed_no_file": est["billed_no_file"], "billed_no_file_cost": est["billed_no_file_cost"],
           "not_shipped": est["not_shipped"], "not_shipped_cost": est["not_shipped_cost"],
           "total": round(credit + est["total"], 2), "invoices": invs,
           "by_supplier": est["by_supplier"]}
    _payable_cache[supplier] = (time.time(), out)
    return out


@app.get("/api/suppliers/by-day")
async def api_suppliers_by_day(supplier: str = ""):
    """Invoices tab: every invoice by its date, paid or not, plus on credit / estimated / total payable."""
    out = await asyncio.to_thread(_supplier_payable, supplier, True)
    with db._conn() as con:
        notes = {r["day"]: {"note": r["note"], "by": r["by"]} for r in con.execute("SELECT * FROM invoice_day_notes WHERE note IS NOT NULL")}
    sups = sorted({i["supplier"] for i in out["invoices"]})
    return {**out, "days": cog_desk.group_days(out["invoices"]), "day_notes": notes, "suppliers": sups,
            "today": datetime.now(CASH_TZ).date().isoformat()}


@app.post("/api/suppliers/marks")
def api_suppliers_marks(payload: dict, request: Request):
    """Save the Invoices tab: paid / not paid per invoice, notes per day (lessons for the AI), the cut-off date."""
    _cogdesk_db()
    who = _who(request)
    cutoff = payload.get("cutoff")
    if cutoff:
        try:
            date.fromisoformat(cutoff)
        except ValueError:
            raise HTTPException(400, "The cut-off isn't a date.")
        db.set_setting("invoice_paid_cutoff", cutoff)
    with db._conn() as con:
        for iid, paid in (payload.get("paid") or {}).items():
            con.execute("UPDATE invoices SET paid = ?, paid_by = ?, paid_at = ? WHERE id = ?",
                        (1 if paid else 0, who, agent.now(), int(iid)))
        for day, note in (payload.get("notes") or {}).items():
            note = str(note or "").strip()[:1500]
            con.execute("INSERT INTO invoice_day_notes (day, note, by, at) VALUES (?, ?, ?, ?) ON CONFLICT(day) DO UPDATE"
                        " SET note = excluded.note, by = excluded.by, at = excluded.at", (day, note or None, who, agent.now()))
    for day, note in (payload.get("notes") or {}).items():
        note = str(note or "").strip()
        _note_lesson(f"daynote:{day}", f"Supplier invoices of {day}: {note}" if note else "", who)
    _payable_cache.clear()
    return {"ok": True}


@app.post("/api/suppliers/invoice-review")
async def api_supplier_invoice_review(payload: dict, request: Request):
    """Draft (post=false) or send (post=true) a message about a flagged invoice to the supplier's Slack invoice channel."""
    inv = db.get_invoice(int(payload.get("id") or 0))
    if not inv:
        raise HTTPException(404, "That invoice no longer exists.")
    sup = inv.get("supplier") or ""
    ch = _review_channel(sup)
    store = next((x["name"].strip() for x in db.list_stores() if x["id"] == inv.get("store_id")), "")
    flag = str(payload.get("flag") or "").strip()
    text = str(payload.get("text") or "").strip() or (
        f"Hi {sup or 'there'} team,\n\nCould you check invoice #{inv.get('number') or '—'} ({store}{', ' if store else ''}"
        f"{inv.get('date') or ''}, {_usd(inv.get('total'))})?\n" + (f"• {flag}\n" if flag else "")
        + "\nPlease send a corrected invoice or let us know. We'll pay it once sorted. Thank you!")
    if not payload.get("post"):
        return {"text": text, "channel": ch and ch["name"]}
    if not ch:
        raise HTTPException(400, f"No Slack invoice channel for {sup or 'this supplier'} yet (Agent desk → Slack: give the "
                                 "channel the role “Supplier invoices” and the supplier's name).")
    try:
        await slack_client.post(db.get_setting("slack_bot_token"), ch["id"], text[:3500])
    except Exception as e:
        raise HTTPException(400, f"Slack didn't take the message: {str(e)[:200]}")
    with db._conn() as con:
        con.execute("UPDATE invoice_lines SET check_state = 'checking', check_by = ?, check_at = datetime('now')"
                    " WHERE invoice_id = ? AND status = 'changed' AND approved_at IS NULL AND check_state IS NULL",
                    (_who(request), inv["id"]))
    _payable_cache.clear()
    return {"ok": True, "posted": ch["name"]}


INVOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "invoice_files")
INVOICE_TYPES = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "csv": "text/csv", "txt": "text/plain"}


def _save_invoice_file(iid: int, data: bytes, filename: str):
    """Keep the original invoice (for audits and the accountant), next to the database."""
    ext = "pdf" if data[:4] == b"%PDF" else "xlsx" if data[:2] == b"PK" else \
        ("csv" if filename.lower().endswith(".csv") else "txt")
    os.makedirs(INVOICE_DIR, exist_ok=True)
    with open(os.path.join(INVOICE_DIR, f"{iid}.{ext}"), "wb") as f:
        f.write(data)
    with db._conn() as con:
        con.execute("UPDATE invoices SET file_ext = ?, file_size = ?, file_at = datetime('now') WHERE id = ?",
                    (ext, len(data), iid))


def _invoice_file_name(inv: dict, stores: dict) -> str:
    store = re.sub(r"[^\w &.-]", "", stores.get(inv["store_id"], "Several stores")).strip()
    sup = re.sub(r"[^\w &.-]", "", inv.get("supplier") or "Supplier").strip()
    num = re.sub(r"[^\w-]", "", inv.get("number") or str(inv["id"]))
    return f"{inv.get('date') or 'undated'} · {store} · {sup} #{num}.{inv.get('file_ext') or 'pdf'}"


@app.get("/api/invoices/{invoice_id:int}/file")
def api_invoice_file(invoice_id: int, download: int = 0):
    inv = db.get_invoice(invoice_id)
    if not inv or not inv.get("file_ext"):
        raise HTTPException(404, "The original file of this invoice wasn't kept (it was uploaded before files were "
                                 "stored). Upload it again to attach it.")
    path = os.path.join(INVOICE_DIR, f"{invoice_id}.{inv['file_ext']}")
    if not os.path.exists(path):
        raise HTTPException(404, "The file is missing from storage. Upload the invoice again to attach it.")
    from urllib.parse import quote
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    name = _invoice_file_name(inv, stores)
    with open(path, "rb") as f:
        data = f.read()
    inline = inv["file_ext"] == "pdf" and not download
    return Response(data, media_type=INVOICE_TYPES.get(inv["file_ext"], "application/octet-stream") if inline
                    else "application/octet-stream",
                    headers={"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
                             "X-Content-Type-Options": "nosniff"})


@app.get("/api/invoices/archive")
def api_invoice_archive(start: str = "", end: str = "", store: str = "", supplier: str = ""):
    """Every kept invoice file in the period (by invoice date), as one zip for the accountant."""
    import io
    import zipfile
    from urllib.parse import quote
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    picked = [i for i in db.list_invoices() if i.get("file_ext")
              and (not start or (i["date"] or "") >= start) and (not end or (i["date"] or "") <= end)
              and (not store or str(i["store_id"]) == store) and (not supplier or (i["supplier"] or "") == supplier)]
    if not picked:
        raise HTTPException(404, "No kept invoice files match. (Invoices uploaded before files were stored need "
                                 "uploading again.)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in sorted(picked, key=lambda x: (x["date"] or "", x["id"])):
            path = os.path.join(INVOICE_DIR, f"{i['id']}.{i['file_ext']}")
            if os.path.exists(path):
                month = (i["date"] or "undated")[:7]
                z.write(path, f"{month}/{_invoice_file_name(i, stores)}")
    name = f"Invoices {start or 'all'} to {end or 'today'}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.post("/api/invoices")
async def api_invoice_upload(request: Request, filename: str = "invoice"):
    """Upload one supplier invoice (PDF, Excel, or text/CSV with one line per item)."""
    data = await request.body()
    return await _ingest_invoice(data, filename, (getattr(request.state, "user", None) or {}).get("email", ""))


async def _ingest_invoice(data: bytes, filename: str, user: str) -> dict:
    """Read, match and keep one supplier invoice; costs of matched orders become the invoiced ones (estimates replaced),
    and the AI reads it again in the background. Used by the upload button and by the agent (Slack invoices)."""
    if not data:
        raise HTTPException(400, "That file is empty.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "That file is too big (15 MB max).")
    ai_state = None
    def _read(data):
        if data[:4] == b"%PDF":
            text = invoices.pdf_text(data)
        elif data[:2] == b"PK":                        # Excel (.xlsx), e.g. Graypop's invoices
            text = invoices.xlsx_text(data, filename)
        else:
            text = data.decode("utf-8", "ignore")
        return invoices.parse(text)
    try:
        inv = await asyncio.to_thread(_read, data)     # big PDFs never hold up the other pages
    except invoices.InvoiceError as e:
        # the normal reader couldn't read it: the AI reads it alone, used only if it adds up
        key = db.get_setting("anthropic_api_key")
        if not key:
            raise HTTPException(400, str(e))
        try:
            ai = await invoice_ai.read(key, data, filename, _invoice_lessons())
        except invoice_ai.AIError as e2:
            raise HTTPException(400, f"{e} The AI couldn't read it either: {e2}")
        inv = invoice_ai.as_invoice(ai)
        if not inv["lines"]:
            raise HTTPException(400, f"{e} The AI found no orders in it either.")
        inv["date"] = inv["date"] or date.today().isoformat()
        sums = round(sum(float(o["amount"]) for o in ai["orders"]), 2)
        adds_up = ai.get("total") is None or abs(sums - float(ai["total"])) < 0.01
        ai_state = ("ai_only", f"Read by the AI only ({e})", None) if adds_up else \
            ("review", "Read by the AI only, and its orders don't add up to the invoice total",
             {"diffs": [{"order": "Orders vs total", "file": None, "ai": sums,
                         "what": f"The AI's orders add up to {sums:.2f}, the invoice says {float(ai['total']):.2f}"}],
              "ai": ai})
    for old in db.list_invoices():
        if old["number"] and old["number"] == inv["number"] and old["supplier"] == inv["supplier"] \
                and abs((old["total"] or 0) - inv["total"]) < 0.01:
            if not old.get("file_ext"):          # uploaded before files were kept: keep this copy now
                _save_invoice_file(old["id"], data, filename)
                if (old.get("ai_check") or "") in ("", "skipped", "failed"):
                    db.set_invoice_ai(old["id"], "pending", "Queued")
                    asyncio.create_task(_ai_check_invoice(old["id"]))
                return {"ok": True, "attached": True, "id": old["id"], "supplier": inv["supplier"],
                        "number": inv["number"], "lines": len(inv["lines"]), "total": inv["total"],
                        "sum": inv["total"], "counts": {}, "missing": 0}
            raise HTTPException(400, f"Invoice {inv['number']} from {inv['supplier']} is already uploaded.")
    checked, sids, queried = await _check_invoice(inv)
    iid = db.save_invoice(inv, checked, filename[:120], user)
    _save_invoice_file(iid, data, filename)
    if ai_state:
        db.set_invoice_ai(iid, *ai_state)
    else:
        db.set_invoice_ai(iid, "pending")
        asyncio.create_task(_ai_check_invoice(iid))      # the AI reads it too, in the background
    with db._conn() as con:
        for l in checked["lines"]:
            key = (l.get("store_id"), l.get("order") , cog.norm(l.get("product") or ""))
            old = queried.get(key)
            if old and l.get("status") not in ("unmatched", "duplicate"):
                con.execute("UPDATE invoice_lines SET status = 'superseded', note = ? WHERE invoice_id = ? AND line_no = ?",
                            (f"Replaced by invoice {inv['number'] or '?'}", old[0], old[1]))
    for sid in sids:
        names = {l["order_name"] for l in checked["lines"] if l.get("order_name") and l["store_id"] == sid}
        await _recost(sid, names)
    return {"ok": True, "id": iid, "supplier": inv["supplier"], "number": inv["number"],
            "lines": len(inv["lines"]), "total": inv["total"], "sum": checked["sum"],
            "counts": checked["counts"], "missing": len(checked["missing"])}


async def _check_invoice(inv: dict, skip_invoice: int = None):
    """Match an invoice's lines to orders and check prices. (checked, store ids, lines it corrects)."""
    catalog = await _catalog()
    tags = _cog.get("tags") or {}
    store_of = lambda key: tags.get(cog.tag_of(key)) if cog.tag_of(key) else None
    sids = {store_of(l["order"]) for l in inv["lines"]} - {None}
    order_lines = {}
    if sids:
        start = (date.today() - timedelta(days=150)).isoformat()
        for r in db.cog_lines(list(sids), start, date.today().isoformat()):
            order_lines.setdefault((r["store_id"], cog.order_key(r["order_name"])), []).append(r)
    queried = {}          # lines whose price is still being checked: a corrected invoice replaces them
    invoiced = {}
    for l in db.invoice_lines():
        if l["status"] in ("unmatched", "duplicate", "superseded") or l["invoice_id"] == skip_invoice:
            continue
        key = (l["store_id"], l["order_key"], cog.norm(l["product"] or ""))
        if l["status"] == "changed" and not l["approved_at"]:
            queried[key] = (l["invoice_id"], l["line_no"])
        else:
            invoiced[key] = l["number"]
    return invoices.check(inv, store_of, order_lines, catalog, invoiced), sids, queried


_ai_reading = asyncio.Semaphore(2)


async def _recost_invoice(iid: int):
    by_store = defaultdict(set)
    for l in db.invoice_lines(iid):
        if l["store_id"] and l["order_name"]:
            by_store[l["store_id"]].add(l["order_name"])
    for sid, names in by_store.items():
        await _recost(sid, names)


async def _ai_check_invoice(iid: int):
    """Second, independent reading of a kept invoice file by the AI, compared with the first."""
    inv = db.get_invoice(iid)
    if not inv:
        return
    key = db.get_setting("anthropic_api_key")
    if not key:
        db.set_invoice_ai(iid, "skipped", "No AI key in Settings → AI")
        return
    path = os.path.join(INVOICE_DIR, f"{iid}.{inv.get('file_ext') or ''}")
    if not inv.get("file_ext") or not os.path.exists(path):
        db.set_invoice_ai(iid, "skipped", "The original file isn't kept; upload it again to double-check it")
        return
    with open(path, "rb") as f:
        data = f.read()
    async with _ai_reading:
        db.set_invoice_ai(iid, "pending", "Reading…")
        try:
            ai = await invoice_ai.read(key, data, inv.get("filename") or "", _invoice_lessons())
        except invoice_ai.AIError as e:
            db.set_invoice_ai(iid, "failed", str(e))
            return
        except Exception as e:                       # never leave it "pending" forever
            db.set_invoice_ai(iid, "failed", f"{type(e).__name__}")
            return
    diffs = invoice_ai.compare(db.invoice_lines(iid), inv["total"], ai)
    cost = f"AI read {len(ai['orders'])} orders ({ai['usage']['in']} + {ai['usage']['out']} tokens)"
    if diffs:
        db.set_invoice_ai(iid, "review", f"{len(diffs)} difference{'s' if len(diffs) != 1 else ''} · {cost}",
                          {"diffs": diffs, "ai": ai})
        await _recost_invoice(iid)                   # its lines are left out until someone decides
    else:
        db.set_invoice_ai(iid, "agreed", cost)
        if inv.get("ai_check") == "review" or "was on hold" in (inv.get("ai_note") or ""):   # count it again
            await _recost_invoice(iid)


@app.post("/api/invoices/{invoice_id:int}/review")
async def api_invoice_review(invoice_id: int, payload: dict):
    """A person looked at where the AI read the invoice differently and picked the right reading:
    file = the first reading was right; ai = use the AI's reading instead; recheck = ask the AI again."""
    inv = db.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "That invoice no longer exists.")
    use = payload.get("use")
    if use == "recheck":
        db.set_invoice_ai(invoice_id, "pending", "Queued", json.loads(inv["review"]) if inv.get("review") else None)
        if inv.get("ai_check") == "review":
            with db._conn() as con:                  # remember it was on hold, so clearing it re-costs
                con.execute("UPDATE invoices SET ai_note = 'Queued (was on hold)' WHERE id = ?", (invoice_id,))
        asyncio.create_task(_ai_check_invoice(invoice_id))
        return {"ok": True}
    if use == "file":
        db.set_invoice_ai(invoice_id, "confirmed", "Checked by a person: the first reading is right")
    elif use == "ai":
        review = json.loads(inv.get("review") or "{}")
        if not review.get("ai"):
            raise HTTPException(400, "There's no AI reading for this invoice.")
        new = invoice_ai.as_invoice(review["ai"])
        new.update(supplier=inv["supplier"], number=inv["number"], date=inv["date"])
        old_orders = {(l["store_id"], l["order_name"]) for l in db.invoice_lines(invoice_id) if l["order_name"]}
        checked, _, _ = await _check_invoice(new, skip_invoice=invoice_id)
        db.replace_invoice_lines(invoice_id, new, checked)
        db.set_invoice_ai(invoice_id, "confirmed", "Checked by a person: the AI's reading is used")
        for sid, name in old_orders:
            await _recost(sid, {name})
    else:
        raise HTTPException(400, "Pick which reading is right.")
    await _recost_invoice(invoice_id)
    return {"ok": True}


@app.post("/api/invoices/ai-check")
async def api_invoice_ai_check_all():
    """Double-check every kept invoice the AI hasn't read yet (oldest first, two at a time)."""
    if not db.get_setting("anthropic_api_key"):
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    todo = [i["id"] for i in sorted(db.list_invoices(), key=lambda x: (x["date"] or "", x["id"]))
            if i.get("file_ext") and (i.get("ai_check") or "") in ("", "skipped", "failed")]
    for iid in todo:
        db.set_invoice_ai(iid, "pending", "Queued")

    async def run():
        await asyncio.gather(*(_ai_check_invoice(i) for i in todo))
    asyncio.create_task(run())
    return {"ok": True, "queued": len(todo)}


@app.get("/api/suppliers")
async def api_suppliers():
    """Every supplier's account: opening + fulfilled orders' COG - payments = owed now."""
    accounts = db.supplier_accounts()
    today = datetime.now(CASH_TZ).date()
    starts = [a["opening_date"] for a in accounts.values() if a.get("opening_date")]
    since_day = min([date.fromisoformat(d) for d in starts] + [today - timedelta(days=60)])
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        lines = [dict(r) for r in con.execute(
            "SELECT store_id, order_name, supplier, source, cost_usd, quantity, cancelled FROM cog_lines"
            " WHERE day >= ?", ((since_day - timedelta(days=45)).isoformat(),))]
        fulfilled = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT store_id, order_name, MIN(fulfilled_at) FROM shipments WHERE fulfilled_at IS NOT NULL"
            " AND fulfilled_at != '' GROUP BY store_id, order_name")}
    on_invoice = set()
    inv_lines = defaultdict(list)
    for l in db.invoice_lines():
        inv_lines[l["invoice_id"]].append(l)
        if l["status"] not in ("unmatched", "duplicate", "superseded") and l.get("ai_check") != "review":
            on_invoice.add((l["store_id"], cog.order_key(l["order_key"])))
    for l in lines:
        l["on_invoice"] = (l["store_id"], cog.order_key(l["order_name"])) in on_invoice
    all_inv = db.invoice_lines()
    cog_desk.set_suppliers(lines, all_inv, cog_desk.supplier_shares(all_inv))      # cross-fulfilled orders
    orders = suppliers.fulfilled_orders(lines, fulfilled, CASH_TZ)

    # payments: what the Expenses desk files under "Supplier payments", told apart by payee
    payments, to_assign, problems = [], [], []
    if db.list_cash_connections():
        t0 = datetime.combine(since_day, clock.min, CASH_TZ)
        moves, problems = await _expense_moves(t0, datetime.now(CASH_TZ) + timedelta(minutes=1))
        table = await fx.table("USD")
        factor = (lambda code: fx.factor(table, code, "USD")) if table else (lambda code: 1.0)
        rules, until = expenses.rule_map(db.expense_rules())
        payees = db.supplier_payees()
        for it in expenses.apply_rules(expenses.classify(moves, factor), rules, until):
            if it["category"] != "supplier" or it["amount"] <= 0:
                continue
            p = {"id": it["id"], "key": it["key"], "name": it["name"], "detail": it["detail"],
                 "time": it["time"].isoformat(), "day": it["time"].astimezone(CASH_TZ).date().isoformat(),
                 "amount": it["amount"], "currency": it["currency"],
                 "usd": it["amount"] * factor(it["currency"])}
            who = payees.get("tx:" + str(it["id"]), payees.get(it["key"]))
            if who is None:
                to_assign.append(p)
            elif who:
                payments.append({**p, "supplier": who, "by_payment": ("tx:" + str(it["id"])) in payees})

    catalog = await _catalog()
    rate_of = lambda sid, n: catalog.bundle_rate(sid, n)[0] if catalog else 0.0
    invs = defaultdict(list)
    for i in db.list_invoices():
        i["missing_list"] = json.loads(i["missing"] or "[]")
        chk = suppliers.invoice_check(i, inv_lines.get(i["id"], []), rate_of)
        invs[i["supplier"] or "Unknown supplier"].append({
            "id": i["id"], "number": i["number"], "date": i["date"], "store": stores.get(i["store_id"], "Several"),
            "total": i["total"], "ai_check": i.get("ai_check"), **chk})

    names = set(accounts) | {o["supplier"] for o in orders} | set(invs)
    out = []
    for name in sorted(names, key=lambda n: (n == "Unknown supplier", n.lower())):
        acct = accounts.get(name) or {"name": name}
        led = suppliers.ledger(acct, [o for o in orders if o["supplier"] == name],
                               [p for p in payments if p["supplier"] == name], invs.get(name, []), today.isoformat())
        led["stores"] = [{"store": stores.get(sid, "?"), **v} for sid, v in
                         sorted(led["stores"].items(), key=lambda kv: -kv[1]["cost"])]
        for o in led["late"]:
            o["store"] = stores.get(o["store_id"], "?")
        out.append(led)
    return {"suppliers": out, "to_assign": sorted(to_assign, key=lambda p: p["time"], reverse=True),
            "names": sorted(n for n in names if n != "Unknown supplier"), "today": today.isoformat(),
            "problems": problems, "payments_from": since_day.isoformat()}


@app.post("/api/suppliers/opening")
async def api_supplier_opening(payload: dict, request: Request):
    """Owner sets a supplier's opening balance: what was owed to them on a date (USD)."""
    name = (payload.get("supplier") or "").strip()
    try:
        day = date.fromisoformat(payload.get("date") or "")
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Pick the date and type the amount owed that day (0 if nothing).")
    if not name:
        raise HTTPException(400, "Which supplier?")
    if day > datetime.now(CASH_TZ).date() or (datetime.now(CASH_TZ).date() - day).days > 380:
        raise HTTPException(400, "The opening date must be within the last year.")
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    db.set_supplier_account(name, day.isoformat(), amount, (payload.get("note") or "")[:300], user)
    return {"ok": True}


@app.post("/api/suppliers/payee")
async def api_supplier_payee(payload: dict, request: Request):
    """Which supplier a payment (or every payment to that payee) went to. supplier '' = not a
    supplier; null = forget the choice."""
    key = (payload.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "Which payment?")
    sup = payload.get("supplier")
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    db.set_supplier_payee(key, sup.strip() if isinstance(sup, str) else None, user)
    return {"ok": True}


@app.get("/api/invoices")
def api_invoices():
    _invoice_notes_db()
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    out = []
    for i in db.list_invoices():
        i["store"] = stores.get(i["store_id"], "Several / unknown")
        i["missing"] = len(json.loads(i["missing"] or "[]"))
        out.append(i)
    return {"invoices": out, "to_check": sum(i["to_check"] or 0 for i in out)}


@app.get("/api/invoices/{invoice_id:int}")
def api_invoice(invoice_id: int):
    inv = db.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "That invoice no longer exists.")
    inv["missing"] = json.loads(inv["missing"] or "[]")
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    inv["store"] = stores.get(inv["store_id"], "Several / unknown")
    return {"invoice": inv, "lines": db.invoice_lines(invoice_id)}


def _rematch_invoice_lines() -> int:
    """Invoice rows once left as "Product not found in this order" get another try when the order has room for
    them now (e.g. a BOGO line counts as two items, so its second invoice row belongs to it)."""
    done = 0
    with db._conn() as con:
        stuck = [dict(r) for r in con.execute(
            "SELECT invoice_id, line_no, order_key, title, store_id FROM invoice_lines"
            " WHERE status = 'unmatched' AND note = 'Product not found in this order' AND store_id IS NOT NULL")]
        for l in stuck:
            cands = [dict(r) for r in con.execute(
                "SELECT order_name, product, SUM(quantity) AS q FROM cog_lines WHERE store_id = ? AND cancelled = 0"
                " AND REPLACE(UPPER(order_name), '#', '') = ? GROUP BY order_name, product",
                (l["store_id"], cog.order_key(l["order_key"])))]
            best = None
            for c in sorted(cands, key=lambda c: -cog.title_match(l["title"], c["product"])):
                if cog.title_match(l["title"], c["product"]) < 0.6:
                    break
                taken = con.execute(
                    "SELECT COUNT(*) FROM invoice_lines WHERE store_id = ? AND order_name = ? AND product = ?"
                    " AND status NOT IN ('unmatched', 'duplicate', 'superseded')",
                    (l["store_id"], c["order_name"], c["product"])).fetchone()[0]
                if taken < (c["q"] or 1) * cog.pack(c["product"]):
                    best = c
                    break
            if best:
                con.execute("UPDATE invoice_lines SET order_name = ?, product = ?, status = 'ok', note = ''"
                            " WHERE invoice_id = ? AND line_no = ?",
                            (best["order_name"], best["product"], l["invoice_id"], l["line_no"]))
                done += 1
    return done


@app.post("/api/invoices/recheck")
async def api_invoice_recheck():
    """Re-check every uploaded invoice line in date order with prices per item (a line can be for
    several items), rebuild the product cost history, and re-cost the orders. Decisions already
    made (use new price / checking / keep old) stay."""
    _rematch_invoice_lines()
    lines = db.invoice_lines()
    with db._conn() as con:
        qty = {(r[0], r[1], r[2]): r[3] * cog.pack(r[2]) for r in con.execute(     # BOGO lines are 2 items each
            "SELECT store_id, order_name, product, SUM(quantity) FROM cog_lines GROUP BY store_id, order_name, product")}
        items = defaultdict(int)                                                     # items in the whole order
        for sid, name, product, q in con.execute(
                "SELECT store_id, order_name, product, quantity FROM cog_lines WHERE cancelled = 0"):
            items[(sid, name)] += (q or 0) * cog.pack(product)
    per = {}
    for l in lines:
        if l["status"] not in ("unmatched", "duplicate", "superseded") and l.get("product"):
            k = (l["invoice_id"], l["store_id"], l["order_name"], l["product"])
            per[k] = per.get(k, 0) + 1
    if not _cog.get("tags"):
        _cog["tags"] = cog.store_tags(db.store_order_names())
    catalog = cog.Catalog(await _cog_rows(), _cog["tags"], _order_items())
    changed = {"lines": 0, "now_ok": 0, "still_changed": 0}
    touched = defaultdict(set)
    by_inv = defaultdict(list)                      # one invoice at a time, oldest first
    for l in sorted(lines, key=lambda x: (x.get("date") or "", x["invoice_id"], x["line_no"])):
        if l["status"] in ("unmatched", "duplicate", "superseded") or not l.get("product"):
            continue
        by_inv[(l.get("date") or "", l["invoice_id"])].append(l)
    for key in sorted(by_inv):
        rows = []
        for l in by_inv[key]:
            q = qty.get((l["store_id"], l["order_name"], l["product"])) or 1
            units = max(1.0, q / per[(l["invoice_id"], l["store_id"], l["order_name"], l["product"])])
            rows.append({**l, "units": units, "tier": items.get((l["store_id"], l["order_name"])) or 1,
                         "_was": l["status"]})
        invoices.judge(rows, catalog)               # 1 item: single price; 2+: the order against singles
        for r in rows:
            if r["status"] != "changed" and r["_was"] == "changed":
                changed["now_ok"] += 1
            if r["status"] == "changed":
                changed["still_changed"] += 1
            with db._conn() as con:
                con.execute("UPDATE invoice_lines SET units = ?, tier = ?, each = NULL, singles = ?, status = ?,"
                            " previous = ?, note = ? WHERE invoice_id = ? AND line_no = ?",
                            (r["units"], r["tier"], r.get("singles"), r["status"], r["previous"], r["note"],
                             r["invoice_id"], r["line_no"]))
            changed["lines"] += 1
            touched[r["store_id"]].add(r["order_name"])
        catalog.add_invoices(rows)
    _cog.pop("catalog", None)
    _cog["catalog"] = catalog
    with db._conn() as con:                         # every order costed from history or estimates too
        for sid, name in con.execute("SELECT DISTINCT store_id, order_name FROM cog_lines WHERE day >= date('now', '-150 day')"):
            touched[sid].add(name)
    for sid, names in touched.items():
        await _recost(sid, names)
    return {"ok": True, **changed, "orders": sum(len(v) for v in touched.values())}


def _invoice_notes_db():
    """Notes on price changes (invoice_lines.check_note) and on invoices (invoices.note)."""
    with db._conn() as con:
        for table, col in (("invoice_lines", "check_note"), ("invoices", "note")):
            if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")


def _invoice_lessons() -> list:
    """What the team taught about invoices (notes become lessons): read by the AI before it reads an invoice."""
    try:
        _agent_db()
        return [l["text"] for l in agent.lessons("invoices") if l["area"] == "invoices"]
    except Exception:
        return []


def _note_lesson(source: str, text: str, who: str):
    """Keep one lesson per note (updated when the note changes, switched off when it's cleared)."""
    _agent_db()
    with db._conn() as con:
        old = con.execute("SELECT id FROM agent_lessons WHERE source = ?", (source,)).fetchone()
        if old:
            con.execute("UPDATE agent_lessons SET text = ?, active = ? WHERE id = ?", (text[:2000], 1 if text else 0, old["id"]))
        elif text:
            con.execute("INSERT INTO agent_lessons (area, text, source, created_by, created_at) VALUES ('invoices', ?, ?, ?, ?)",
                        (text[:2000], source, who, agent.now()))


@app.get("/api/invoices/changes")
def api_invoice_changes(view: str = ""):
    """Every price change still to decide (or, view=history, already decided), one row per store + product + new price."""
    _invoice_notes_db()
    history = view == "history"
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    groups = {}
    for l in db.invoice_lines():
        if l["status"] != "changed":
            continue
        decided = bool(l["approved_at"]) or l.get("check_state") in ("kept", "bundle")
        if decided != history:
            continue
        if l.get("ai_check") == "review" and not history:
            continue
        if (l.get("tier") or 1) >= 2:               # a bundle that cost more than its items on their own
            k = ("bundle", l["invoice_id"], l["store_id"], l["order_name"] or l["order_key"], decided and (l["approved_at"] and "used" or "kept"))
            g = groups.setdefault(k, {"store_id": l["store_id"], "store": stores.get(l["store_id"], "?"),
                                      "product": "", "items": [], "bundle": True, "new": 0.0, "old": 0.0,
                                      "lines": [], "orders": [l["order_name"] or l["order_key"]],
                                      "state": l.get("check_state") or "", "check_by": l.get("check_by"),
                                      "check_at": l.get("check_at")})
            u = l.get("units") or 1
            g["items"].append(f"{(l['product'] or l['title']).split(' - ')[0][:38]} ×{u:g}")
            g["product"] = f"Bundle of {l.get('tier')}: " + " + ".join(g["items"])
            g["new"] = round(g["new"] + l["amount"], 2)
            g["old"] = round(g["old"] + (l.get("singles") or 0), 2)
            g["lines"].append([l["invoice_id"], l["line_no"]])
            if l.get("check_state") == "checking":
                g.update(state="checking", check_by=l.get("check_by"), check_at=l.get("check_at"))
            continue
        unit = round(l["amount"] / (l.get("units") or 1), 2)          # per item
        k = (l["store_id"], l["product"], unit, decided and (l["approved_at"] and "used" or "kept"))
        g = groups.setdefault(k, {"store_id": l["store_id"], "store": stores.get(l["store_id"], "?"), "product": l["product"],
                                  "new": unit, "old": l["previous"], "lines": [], "orders": [],
                                  "state": l.get("check_state") or "", "check_by": l.get("check_by"), "check_at": l.get("check_at")})
        g["lines"].append([l["invoice_id"], l["line_no"]])
        g["orders"].append(l["order_name"] or l["order_key"])
        if l.get("check_state") == "checking":
            g.update(state="checking", check_by=l.get("check_by"), check_at=l.get("check_at"))
    by_line = {(l["invoice_id"], l["line_no"]): l for l in db.invoice_lines() if l["status"] == "changed"}
    for g in groups.values():
        ls = [by_line.get(tuple(x)) or {} for x in g["lines"]]
        g["note"] = next((x.get("check_note") for x in ls if x.get("check_note")), "")
        g["invoice"] = next((f"{x.get('supplier') or ''} #{x.get('number') or '—'}" for x in ls if x), "")
        g["invoice_date"] = next((x.get("date") for x in ls if x.get("date")), "")
        if history:
            first = ls[0] if ls else {}
            g["decision"] = "Used the new price" if first.get("approved_at") else \
                "Bundle / BOGO, not a price change" if first.get("check_state") == "bundle" else "Kept the old price (supplier corrected it)"
            g["decided_by"] = first.get("approved_by") or first.get("check_by") or ""
            g["decided_at"] = first.get("approved_at") or first.get("check_at") or ""
    if history:
        out = sorted(groups.values(), key=lambda g: g.get("decided_at") or "", reverse=True)[:300]
    else:
        out = sorted(groups.values(), key=lambda g: (g["state"] == "checking", (g["new"] or 0) < (g["old"] or 0), g["store"]))
    for g in out:
        g["direction"] = "down" if g["old"] is not None and g["new"] < g["old"] else "up"
        # Owner's rule of thumb: suppliers move a price by about $1.50–2.50 per item; a bigger gap is usually a
        # bundle or a 2+ item order read as one item (on either side), so it's worth opening the order first.
        g["gap_each"] = round(abs(g["new"] - g["old"]), 2) if g["old"] is not None else None
        if g.get("bundle") or g["old"] is None:
            g["likely"] = ""
        else:
            g["likely"] = "price" if abs(g["new"] - g["old"]) <= PRICE_GAP else "quantity"
    return {"changes": out, "gap": PRICE_GAP}


PRICE_GAP = 2.5   # USD per item: above this, a "price change" is more likely a bundle / quantity mix-up


@app.post("/api/invoices/changes/note")
def api_invoice_change_note(payload: dict, request: Request):
    """A note on a price change (why it's dearer, what the supplier said …): kept on its lines and as a lesson the AI follows."""
    _invoice_notes_db()
    lines = [(int(a), int(b)) for a, b in payload.get("lines") or []]
    if not lines:
        raise HTTPException(400, "Which price change?")
    note = str(payload.get("note") or "").strip()[:1500]
    who = (getattr(request.state, "user", None) or {}).get("email", "")
    with db._conn() as con:
        for iid, no in lines:
            con.execute("UPDATE invoice_lines SET check_note = ? WHERE invoice_id = ? AND line_no = ?", (note or None, iid, no))
    what = str(payload.get("what") or "").strip()[:300]
    _note_lesson(f"pricenote:{lines[0][0]}:{lines[0][1]}", f"Price change, {what}: {note}" if note else "", who)
    return {"ok": True}


@app.post("/api/invoices/{invoice_id:int}/note")
def api_invoice_note(invoice_id: int, payload: dict, request: Request):
    """A note on an invoice: kept with it and as a lesson the AI follows when reading invoices."""
    _invoice_notes_db()
    inv = db.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "That invoice no longer exists.")
    note = str(payload.get("note") or "").strip()[:1500]
    who = (getattr(request.state, "user", None) or {}).get("email", "")
    with db._conn() as con:
        con.execute("UPDATE invoices SET note = ? WHERE id = ?", (note or None, invoice_id))
    _note_lesson(f"invnote:{invoice_id}", f"Invoice {inv.get('supplier') or ''} #{inv.get('number') or '—'} ({inv.get('filename') or ''}): {note}"
                 if note else "", who)
    return {"ok": True}


@app.post("/api/invoices/changes")
async def api_invoice_change_decide(payload: dict, request: Request):
    """use_new: the new price becomes the product's cost (future orders too).
    checking: asked the supplier; the old price stays for other orders meanwhile.
    keep_old: supplier says it was a mistake; these orders cost the old price (a corrected invoice replaces them)."""
    lines = payload.get("lines") or []
    if payload.get("direction") in ("up", "down") and not lines:      # bulk: every cheaper / dearer one
        lines = [ln for g in api_invoice_changes()["changes"]
                 if g["direction"] == payload["direction"] and g["state"] != "checking" for ln in g["lines"]]
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    return await _decide_prices(payload.get("action"), lines, user)


async def _decide_prices(action: str, lines: list, user: str) -> dict:
    lines = [(int(a), int(b)) for a, b in lines or []]
    if action not in ("use_new", "checking", "keep_old", "undo") or not lines:
        raise HTTPException(400, "Pick what to do.")
    stores, names = set(), set()
    with db._conn() as con:
        for iid, no in lines:
            if action == "use_new":
                con.execute("UPDATE invoice_lines SET approved_by = ?, approved_at = datetime('now'), check_state = NULL"
                            " WHERE invoice_id = ? AND line_no = ?", (user, iid, no))
            elif action == "undo":
                con.execute("UPDATE invoice_lines SET check_state = NULL, check_by = NULL, check_at = NULL"
                            " WHERE invoice_id = ? AND line_no = ?", (iid, no))
            else:
                con.execute("UPDATE invoice_lines SET check_state = ?, check_by = ?, check_at = datetime('now')"
                            " WHERE invoice_id = ? AND line_no = ?",
                            ("checking" if action == "checking" else "kept", user, iid, no))
            r = con.execute("SELECT store_id, order_name, product FROM invoice_lines WHERE invoice_id = ? AND line_no = ?",
                            (iid, no)).fetchone()
            if r and r["store_id"]:
                stores.add(r["store_id"])
                if r["order_name"]:
                    names.add((r["store_id"], r["order_name"]))
                if action == "use_new" and r["product"]:
                    # every order of this product not on an invoice yet takes the new price
                    for o in con.execute("SELECT DISTINCT order_name FROM cog_lines WHERE store_id = ? AND product = ?"
                                         " AND source IN ('catalog', 'estimate') AND day >= date('now', '-90 day')",
                                         (r["store_id"], r["product"])):
                        names.add((r["store_id"], o[0]))
    for sid in stores:
        await _recost(sid, {n for s2, n in names if s2 == sid})
    return {"ok": True, "lines": len(lines), "recosted": len(names)}


@app.post("/api/invoices/{invoice_id}/approve/{line_no}")
async def api_invoice_approve(request: Request, invoice_id: int, line_no: int):
    """A changed price was checked with the supplier: it becomes the product's cost."""
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    db.approve_invoice_line(invoice_id, line_no, user)
    inv = db.get_invoice(invoice_id)
    if inv and inv["store_id"]:
        names = {l["order_name"] for l in db.invoice_lines(invoice_id) if l["order_name"]}
        await _recost(inv["store_id"], names)
    return {"ok": True}


@app.delete("/api/invoices/{invoice_id:int}")
async def api_invoice_delete(invoice_id: int):
    inv = db.get_invoice(invoice_id)
    names = {l["order_name"] for l in db.invoice_lines(invoice_id) if l["order_name"]}
    db.delete_invoice(invoice_id)
    if inv and inv.get("file_ext"):
        try:
            os.remove(os.path.join(INVOICE_DIR, f"{invoice_id}.{inv['file_ext']}"))
        except OSError:
            pass
    if inv and inv["store_id"]:
        await _recost(inv["store_id"], names)
    return {"ok": True}


@app.get("/api/cog/products")
async def api_cog_products(store: int):
    rows = await _cog_rows()
    if not _cog.get("catalog"):
        raise HTTPException(400, "Product costs are still loading. Try again in a minute.")
    items = _cog["catalog"].products(store)
    items.sort(key=lambda p: (not p["changed"], -(p["times"])))
    return {"products": items}


# ---------------------------------------------------------------- COG → Products (products_desk.py)

PD_PRODUCTS = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on Product { legacyResourceId title handle status createdAt
  productType vendor tags featuredImage { url(transform: {maxWidth: 160}) } collections(first: 10) { nodes { id } }
  priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount } } } } }"""
_pd_shop: dict = {}       # store id -> {product id: (read at, info)}
QC_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "qc")


def _pd_db():
    with db._conn() as con:
        con.executescript(products_desk.SCHEMA)
        for table, cols in products_desk.COLUMNS.items():
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, kind in cols:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


async def _pd_info(store, pids: list) -> dict:
    """Title, image, listed date, status, and what size-chart rules look at, per product. Kept 6 hours."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    have = _pd_shop.setdefault(store["id"], {})
    now = time.time()
    want = [p for p in pids if p.isdigit() and not (p in have and now - have[p][0] < PINFO_KEEP)]
    gate = asyncio.Semaphore(3)

    async def batch(ids):
        async with gate:
            try:
                got = await client.gql(PD_PRODUCTS, {"ids": [f"gid://shopify/Product/{p}" for p in ids]})
            except Exception:
                return
        for n in got.get("nodes") or []:
            if n:
                have[str(n["legacyResourceId"])] = (now, n)
        for p in ids:                                   # deleted in Shopify: don't ask again for 6 hours
            have.setdefault(p, (now, None))
    await asyncio.gather(*(batch(want[i:i + 25]) for i in range(0, len(want), 25)))
    return {p: have[p][1] for p in pids if p in have and have[p][1]}


def _pd_money(m) -> float:
    try:
        return float((m or {}).get("amount"))
    except (TypeError, ValueError):
        return None


def _pd_lines(store_ids: list) -> list:
    marks = ",".join("?" * len(store_ids))
    with db._conn() as con:
        return [dict(r) for r in con.execute(
            f"SELECT store_id, order_name, product, product_id, quantity, cancelled, day, supplier, cost_usd, source FROM cog_lines"
            f" WHERE store_id IN ({marks})", store_ids)]


def _pd_groups(lines: list) -> dict:
    """{(store id, product id): {names, units, orders, first, last, supplier}}; lines without a product id
    join the product with the same title in that store, else a group of their own ('t:<title>')."""
    by_title = {}
    for l in lines:
        if l["product_id"]:
            by_title[(l["store_id"], cog.base_name(l["product"]))] = products_desk.pid_of(l["product_id"])
    groups = {}
    for l in lines:
        pid = products_desk.pid_of(l["product_id"]) if l["product_id"] else \
            by_title.get((l["store_id"], cog.base_name(l["product"]))) or "t:" + cog.base_name(l["product"])
        g = groups.setdefault((l["store_id"], pid), {"names": set(), "units": 0, "orders": set(), "first": None,
                                                     "last": None, "supplier": "", "title": l["product"]})
        g["names"].add(l["product"])
        if l["cancelled"]:
            continue
        g["units"] += max(int(l["quantity"] or 1), 1) * cog.pack(l["product"])
        g["orders"].add(l["order_name"])
        if cog.pack(l["product"]) > 1 and l["cost_usd"] and l["source"] in ("invoice", "catalog"):
            # BOGO: what one sale (two pairs) cost on the latest costed order, invoices first
            k = (l["source"] == "invoice", l["day"])
            if not g.get("sale_cost") or k > g["sale_cost"][0]:
                g["sale_cost"] = (k, l["cost_usd"] / max(int(l["quantity"] or 1), 1))
        if not g["last"] or l["day"] > g["last"]:
            g["last"], g["title"] = l["day"], l["product"]
            g["supplier"] = l["supplier"] or g["supplier"]
        if not g["first"] or l["day"] < g["first"]:
            g["first"] = l["day"]
    return groups


def _pd_tickets(lines: list) -> dict:
    """{(store id, product id): [(ticket id, how)]} for customer problem tickets."""
    groups_pid = {}
    for l in lines:
        if l["product_id"]:
            groups_pid[(l["store_id"], cog.base_name(l["product"]))] = products_desk.pid_of(l["product_id"])
    order_products = {}
    for l in lines:
        pid = products_desk.pid_of(l["product_id"]) if l["product_id"] else \
            groups_pid.get((l["store_id"], cog.base_name(l["product"]))) or "t:" + cog.base_name(l["product"])
        order_products.setdefault(cog.order_key(l["order_name"]), []).append((l["store_id"], pid, l["product"]))
    marks = ",".join("?" * len(products_desk.TICKET_KINDS))
    with db._conn() as con:
        tks = [dict(r) for r in con.execute(
            f"SELECT id, orders, subject, summary, customer_email, created_at FROM tickets WHERE kind IN ({marks})",
            products_desk.TICKET_KINDS)]
        cust = {}
        for email, name, at in con.execute("SELECT LOWER(email), order_name, order_at FROM shipments"
                                           " WHERE email IS NOT NULL AND email != '' ORDER BY order_at"):
            cust.setdefault(email, []).append((at, cog.order_key(name)))
    return products_desk.match_tickets(tks, order_products, cust)


@app.get("/api/cog/pdesk")
async def api_pdesk(store: str = "all", start: str = "", end: str = ""):
    """COG → Products: one row per product (variants together) with sales, latest confirmed cost, size chart,
    CS tickets, QC photos, material and notes. start/end (store days): only products sold then, with that
    period's units and orders (cost, tickets and the rest stay all-time)."""
    _pd_db()
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")
              and (store in ("", "all") or str(x["id"]) == str(store))]
    if not stores:
        raise HTTPException(404, "Store not found.")
    lines = await asyncio.to_thread(_pd_lines, [x["id"] for x in stores])
    groups = _pd_groups(lines)
    if start or end:                       # the period's sales only; products with none are left out
        lo, hi = start or "0000", end or start or "9999"
        sold = _pd_groups([l for l in lines if lo <= (l["day"] or "") <= hi])
        groups = {k: {**groups[k], "units": v["units"], "orders": v["orders"]}
                  for k, v in sold.items() if v["units"] and k in groups}
    tick = await asyncio.to_thread(_pd_tickets, lines)
    catalog = await _catalog()
    infos = await asyncio.gather(*(_pd_info(x, sorted({p for (sid, p) in groups if sid == x["id"] and p.isdigit()}))
                                   for x in stores))
    info = {x["id"]: i for x, i in zip(stores, infos)}
    with db._conn() as con:
        extra = {(r["store_id"], r["product_id"]): dict(r) for r in con.execute("SELECT * FROM pd_info")}
        qc = {(r[0], r[1]): r[2] for r in con.execute("SELECT store_id, product_id, COUNT(*) FROM pd_qc GROUP BY 1, 2")}
        notes = {}
        for r in con.execute("SELECT store_id, product_id, kind, text, by, at FROM pd_notes ORDER BY id"):
            n = notes.setdefault((r[0], r[1]), {"complaints": 0, "notes": 0, "last": None})
            n["complaints" if r[2] == "complaint" else "notes"] += 1
            n["last"] = {"kind": r[2], "text": r[3], "by": r[4], "at": r[5]}
    rules = {x["id"]: _sc_store_rules(x["id"]) for x in stores}
    names = {x["id"]: x["name"].strip() for x in stores}
    shops = {x["id"]: x["shop_domain"] for x in stores}
    stores_cur = {x["id"]: x["currency"] for x in stores}
    out = []
    for (sid, pid), g in groups.items():
        p = info[sid].get(pid) if pid.isdigit() else None
        cost = products_desk.cost_history(catalog, sid, list(g["names"])) if catalog else {}
        ex = extra.get((sid, pid)) or {}
        chart = _sc_match(sid, {**p, "id": f"gid://shopify/Product/{pid}"}, "", rules[sid]) if p else None
        tk = tick.get((sid, pid)) or []
        out.append({
            "store_id": sid, "store": names[sid], "pid": pid, "shop": shops[sid],
            "title": (p or {}).get("title") or cog.base_name(g["title"]) or g["title"],
            "image": ((p or {}).get("featuredImage") or {}).get("url"), "handle": (p or {}).get("handle"),
            "status": ((p or {}).get("status") or "").title() or ("Not in Shopify" if pid.isdigit() else "No product id"),
            "listed": ((p or {}).get("createdAt") or "")[:10] or None,
            "supplier": ex.get("supplier") or cost.get("cost_supplier") or g["supplier"] or "",
            "supplier_set": bool(ex.get("supplier")), "material": ex.get("material") or "",
            **({"cost": round(g["sale_cost"][1], 2), "cost_day": g["sale_cost"][0][1], "cost_is_bundle": False,
                "cost_note": "per BOGO sale (2 pairs)" + ("" if g["sale_cost"][0][0] else ", from history")}
               if g.get("sale_cost") else
               {"cost": cost.get("cost"), "cost_day": cost.get("cost_day"), "cost_is_bundle": cost.get("cost_is_bundle"),
                "cost_note": ""}),
            "variant_low": cost.get("variant_low"), "variant_high": cost.get("variant_high"),
            "price_changes": max(0, len(cost.get("changes") or []) - 1),
            "units": g["units"], "orders": len(g["orders"]), "first_order": g["first"], "last_order": g["last"],
            "size_chart": ex.get("size_chart") or "", "size_chart_auto": chart["name"] if chart else None,
            "price_low": _pd_money(((p or {}).get("priceRangeV2") or {}).get("minVariantPrice")),
            "price_high": _pd_money(((p or {}).get("priceRangeV2") or {}).get("maxVariantPrice")),
            "price_currency": ((((p or {}).get("priceRangeV2") or {}).get("minVariantPrice") or {}).get("currencyCode")
                               or stores_cur[sid]),
            "tickets": len(tk), "tickets_by_order": sum(1 for _, h in tk if h == "order number"),
            "qc_photos": qc.get((sid, pid), 0), "notes": notes.get((sid, pid)) or {"complaints": 0, "notes": 0, "last": None},
        })
    out.sort(key=lambda r: -r["units"])
    return {"products": out, "stores": [{"id": x["id"], "name": names[x["id"]]} for x in stores],
            "start": start, "end": end, "costed_at": (_cog.get("synced") or (None,))[0]}


@app.get("/api/cog/pdesk/item")
async def api_pdesk_item(store: int, pid: str):
    """One product: cost history and cost per variant, the matched tickets, notes and QC photos."""
    _pd_db()
    lines = [l for l in await asyncio.to_thread(_pd_lines, [store])]
    groups = _pd_groups(lines)
    g = groups.get((store, pid))
    if not g:
        raise HTTPException(404, "No orders for that product.")
    catalog = await _catalog()
    cost = products_desk.cost_history(catalog, store, list(g["names"])) if catalog else {}
    tk = dict((await asyncio.to_thread(_pd_tickets, lines)).get((store, pid)) or [])
    with db._conn() as con:
        tickets_ = [dict(r) for r in con.execute(
            f"SELECT id, kind, labels, subject, summary, status, created_at, customer_name, orders FROM tickets"
            f" WHERE id IN ({','.join('?' * len(tk)) or 'NULL'}) ORDER BY created_at DESC", list(tk))]
        notes = [dict(r) for r in con.execute("SELECT id, kind, text, by, at FROM pd_notes WHERE store_id = ? AND product_id = ?"
                                              " ORDER BY id DESC", (store, pid))]
        photos = [dict(r) for r in con.execute("SELECT id, caption, by, at FROM pd_qc WHERE store_id = ? AND product_id = ?"
                                               " ORDER BY id DESC", (store, pid))]
    for t in tickets_:
        t["how"] = tk.get(t["id"])
    return {"cost": cost, "tickets": tickets_, "notes": notes, "photos": photos}


@app.put("/api/cog/pdesk/info")
def api_pdesk_info(payload: dict, request: Request):
    """Supplier (when it differs from the cost history's) and material, typed by the team."""
    _pd_db()
    who = (getattr(request.state, "user", None) or {}).get("email", "")
    sid, pid = int(payload["store"]), str(payload["pid"])
    with db._conn() as con:
        cur = dict(con.execute("SELECT * FROM pd_info WHERE store_id = ? AND product_id = ?", (sid, pid)).fetchone() or {})
        sup = str(payload.get("supplier", cur.get("supplier") or "")).strip()[:80]
        mat = str(payload.get("material", cur.get("material") or "")).strip()[:300]
        chart = str(payload.get("size_chart", cur.get("size_chart") or "")).lower()
        chart = chart if chart in ("yes", "no") else None
        con.execute("INSERT OR REPLACE INTO pd_info (store_id, product_id, supplier, material, size_chart, updated_by, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))", (sid, pid, sup or None, mat or None, chart, who))
    return {"ok": True}


@app.post("/api/cog/pdesk/notes")
def api_pdesk_note(payload: dict, request: Request):
    _pd_db()
    text = str(payload.get("text") or "").strip()[:2000]
    if not text:
        raise HTTPException(400, "Write the note first.")
    kind = "complaint" if payload.get("kind") == "complaint" else "note"
    who = (getattr(request.state, "user", None) or {}).get("email", "")
    with db._conn() as con:
        con.execute("INSERT INTO pd_notes (store_id, product_id, kind, text, by) VALUES (?, ?, ?, ?, ?)",
                    (int(payload["store"]), str(payload["pid"]), kind, text, who))
    return {"ok": True}


@app.delete("/api/cog/pdesk/notes/{note_id}")
def api_pdesk_note_delete(note_id: int):
    with db._conn() as con:
        con.execute("DELETE FROM pd_notes WHERE id = ?", (note_id,))
    return {"ok": True}


@app.post("/api/cog/pdesk/qc")
async def api_pdesk_qc_add(request: Request):
    """Quality-check photos of the real product (JPEG copies, phone photos incl. HEIC)."""
    _pd_db()
    form = await request.form()
    sid, pid = int(form.get("store")), str(form.get("pid"))
    caption = str(form.get("caption") or "").strip()[:300]
    who = (getattr(request.state, "user", None) or {}).get("email", "")
    os.makedirs(QC_DIR, exist_ok=True)
    added = 0
    for f in [f for f in form.getlist("files") if hasattr(f, "read")][:20]:
        data = await f.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, f"{f.filename} is bigger than 25 MB.")
        tmp = os.path.join(QC_DIR, f"up-{secrets.token_hex(6)}")
        with open(tmp, "wb") as fh:
            fh.write(data)
        with db._conn() as con:
            qid = con.execute("INSERT INTO pd_qc (store_id, product_id, caption, by) VALUES (?, ?, ?, ?)",
                              (sid, pid, caption, who)).lastrowid
        ok = await asyncio.to_thread(_att_image, tmp, os.path.join(QC_DIR, f"{qid}.jpg"), 2000)
        ok = ok and await asyncio.to_thread(_att_image, tmp, os.path.join(QC_DIR, f"{qid}_t.jpg"), 360)
        os.remove(tmp)
        if not ok:
            with db._conn() as con:
                con.execute("DELETE FROM pd_qc WHERE id = ?", (qid,))
            raise HTTPException(400, f"{f.filename} isn't a photo EcomVital can read.")
        added += 1
    return {"ok": True, "added": added}


@app.get("/api/cog/pdesk/qc/{qc_id}")
def api_pdesk_qc_file(qc_id: int, thumb: int = 0):
    path = os.path.join(QC_DIR, f"{qc_id}{'_t' if thumb else ''}.jpg")
    if not os.path.exists(path):
        raise HTTPException(404, "Photo not found.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/cog/pdesk/qc/{qc_id}")
def api_pdesk_qc_delete(qc_id: int):
    with db._conn() as con:
        con.execute("DELETE FROM pd_qc WHERE id = ?", (qc_id,))
    for suffix in ("", "_t"):
        try:
            os.remove(os.path.join(QC_DIR, f"{qc_id}{suffix}.jpg"))
        except OSError:
            pass
    return {"ok": True}


# ---------------------------------------------------------------- inbox (see mail_client.py)

_reading: set = set()


async def _read_mailbox(acct: dict) -> int:
    """Copy a mailbox's new emails in (Inbox, then Sent for our own replies),
    saving after every batch of 50."""
    if acct["id"] in _reading:          # already being read (e.g. right after connecting)
        return 0
    _reading.add(acct["id"])

    def save_in(batch, top):
        db.save_mail(acct["id"], batch, "in")
        db.mail_checked(acct["id"], last_uid=top)

    def save_out(batch, top):
        db.save_mail(acct["id"], batch, "out")
        db.mail_checked(acct["id"], last_uid_sent=top)
    try:
        count, last = await asyncio.to_thread(mail_client.fetch_new, acct["address"], acct["password"],
                                              acct["last_uid"], acct["provider"], save_in, "INBOX")
        sent, last_sent = await asyncio.to_thread(mail_client.fetch_new, acct["address"], acct["password"],
                                                  acct.get("last_uid_sent") or 0, acct["provider"],
                                                  save_out, "SENT")
        db.mail_checked(acct["id"], last_uid=last, last_uid_sent=last_sent)
        return count + sent
    except Exception as e:
        db.mail_checked(acct["id"], error=str(e)[:300])
        return 0
    finally:
        _reading.discard(acct["id"])


_ai_state: dict = {}


async def _sort_mail():
    """Let the AI sort new emails into tickets (needs the Anthropic key)."""
    key = db.get_setting("anthropic_api_key")
    if not key:
        _ai_state["error"] = "Add the Anthropic API key in Settings → AI to start sorting emails."
        return
    try:
        got = await tickets.process(key, finder=_customer_orders)
        for tid, mid in got.get("ask") or []:
            try:
                await _ask_order_number(key, tid, mid)
            except Exception as e:                   # a failed ask never stops the sorting
                _ai_state["ask_error"] = f"Ticket {tid}: {str(e)[:150]}"
        got["ask"] = len(got.get("ask") or [])
        _ai_state.update(error=None, last=got, at=time.time())
        tot = _ai_state.setdefault("total", {"in": 0, "out": 0})
        tot["in"] += got["tokens_in"]
        tot["out"] += got["tokens_out"]
    except ai_brain.AIError as e:
        _ai_state["error"] = str(e)
    except Exception as e:
        _ai_state["error"] = f"Sorting failed ({type(e).__name__}: {str(e)[:120]})"
        print(f"Sorting emails failed: {e}", flush=True)


def _customer_orders(email: str, orders: list) -> tuple:
    """(store id, [order names]) a customer email is about: from the order numbers in it (each store's
    order tag), else the customer's latest orders by email address (SCM parcels)."""
    tags = _cog.get("tags") or {}
    sid, names = None, []
    for o in orders or []:
        k = cog.order_key(o)
        s2 = tags.get(cog.tag_of(k)) if cog.tag_of(k) else None
        if s2:
            sid = sid or s2
            names.append("#" + k)
    if names or not email:
        return sid, names
    with db._conn() as con:
        rows = con.execute("SELECT store_id, order_name FROM shipments WHERE lower(email) = ? AND order_name IS NOT NULL"
                           " GROUP BY store_id, order_name ORDER BY MAX(order_at) DESC LIMIT 3", (email.lower(),)).fetchall()
    return (rows[0][0] if rows else None), [r[1] for r in rows]


async def _ask_order_number(api_key: str, ticket_id: int, message_id: int):
    """We can't tell which order a new customer email is about: the AI writes one short reply asking
    for the order number, full name and checkout email, sent from the same mailbox and thread."""
    with db._conn() as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not t or t["auto_asked"]:
            return
    m = db.mail_get(message_id)
    acct = next((a for a in db.list_mail_accounts() if a["id"] == t["account_id"]), None)
    if not m or not acct:
        return
    store = next((x for x in db.list_stores() if x["id"] == t["store_id"]), None)
    sig = f"{store['name'].strip()} Customer Care" if store else "Customer Care Team"
    text = await ai_brain.ask_order(api_key, m, sig)
    if not text:
        return
    with db._conn() as con:                          # mark first, so it can never go twice
        con.execute("UPDATE tickets SET auto_asked = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), ticket_id))
    await _send_reply(acct, (m["from_addr"] or "").strip(), m["subject"], text, m["message_id"], m.get("refs"),
                      t["store_id"])


@app.post("/api/mail-accounts/{account_id}/settings")
def api_mail_account_settings(account_id: int, payload: dict):
    with db._conn() as con:
        con.execute("UPDATE mail_accounts SET auto_ask = ? WHERE id = ?", (1 if payload.get("auto_ask") else 0, account_id))
    return {"ok": True}


async def _mail_loop():
    """Copy new emails from every connected mailbox every 2 minutes, then sort them."""
    await asyncio.sleep(20)
    while True:
        for acct in db.list_mail_accounts():
            try:
                await _read_mailbox(acct)   # fresh row each time: last_uid moves on
            except Exception as e:
                print(f"Reading {acct['address']} failed: {e}", flush=True)
            try:
                await _sync_labels(acct)
            except Exception as e:
                print(f"Labels for {acct['address']} failed: {e}", flush=True)
        await _sort_mail()
        try:
            await _suggest_labels()
        except Exception as e:
            print(f"Label suggestions failed: {e}", flush=True)
        await asyncio.sleep(120)


# ---------------------------------------------------------------- Inbox attachments (from Gmail, kept 60 days)

ATT_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "attachments")
ATT_KEEP_DAYS = 60
ATT_INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}   # everything else downloads


def _att_cleanup():
    """Copies not opened for ATT_KEEP_DAYS are removed (the original stays in Gmail)."""
    cutoff = time.time() - ATT_KEEP_DAYS * 86400
    for root, _dirs, files in os.walk(ATT_DIR):
        for f in files:
            path = os.path.join(root, f)
            try:
                if os.path.getatime(path) < cutoff and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


_att_gate = asyncio.Semaphore(3)          # at most 3 emails fetched from Gmail at once
_att_locks: dict = {}


async def _att_cached(m: dict) -> str:
    """Folder with every attachment of this email, fetched from Gmail once (whole email in one trip)."""
    folder = os.path.join(ATT_DIR, str(m["id"]))
    if os.path.exists(os.path.join(folder, ".done")):
        return folder
    lock = _att_locks.setdefault(m["id"], asyncio.Lock())
    async with lock:
        if os.path.exists(os.path.join(folder, ".done")):
            return folder
        acct = next((a for a in db.list_mail_accounts() if a["id"] == m["account_id"]), None)
        if not acct:
            raise HTTPException(404, "That mailbox was removed.")
        async with _att_gate:
            try:
                parts = await asyncio.to_thread(mail_client.fetch_attachment, acct["address"], acct["password"],
                                                m["message_id"], None)
            except mail_client.MailError as e:
                raise HTTPException(400, str(e))
        os.makedirs(folder, exist_ok=True)
        for i, (_n, _t, data) in enumerate(parts):
            with open(os.path.join(folder, str(i)), "wb") as f:
                f.write(data)
        open(os.path.join(folder, ".done"), "w").close()
        if random.random() < 0.05:
            asyncio.get_running_loop().run_in_executor(None, _att_cleanup)
    _att_locks.pop(m["id"], None)
    return folder


def _att_image(path: str, out: str, size: int) -> bool:
    """A JPEG copy of a photo (also iPhone HEIC), at most `size` pixels wide/high."""
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            im.convert("RGB").save(out, "JPEG", quality=82)
        return True
    except Exception:
        return False


@app.get("/api/inbox/attachments/{message_id}/{index}")
async def api_inbox_attachment(message_id: int, index: int, download: int = 0, thumb: int = 0, jpeg: int = 0):
    """download=1 the original file; thumb=1 a small JPEG preview; jpeg=1 a full-size JPEG (for HEIC photos)."""
    m = db.mail_get(message_id)
    if not m:
        raise HTTPException(404, "That email no longer exists.")
    info = json.loads(m["attachments"] or "[]")
    if index < 0 or index >= len(info):
        raise HTTPException(404, "That attachment isn't in the email.")
    meta = info[index]
    folder = await _att_cached(m)
    path = os.path.join(folder, str(index))
    if not os.path.exists(path):
        raise HTTPException(404, "That attachment isn't in the email.")
    os.utime(path)                                       # opened again: keep it longer
    from urllib.parse import quote
    name = meta.get("name") or "attachment"
    ctype = meta.get("type") or "application/octet-stream"
    if (thumb or jpeg) and not download:
        out = f"{path}.{'thumb' if thumb else 'full'}.jpg"
        if not os.path.exists(out):
            ok = await asyncio.to_thread(_att_image, path, out, 480 if thumb else 2400)
            if not ok:
                raise HTTPException(415, "No preview for this file.")
        with open(out, "rb") as f:
            data = f.read()
        return Response(data, media_type="image/jpeg", headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(name.rsplit('.', 1)[0] + '.jpg')}",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"})
    with open(path, "rb") as f:
        data = f.read()
    inline = ctype in ATT_INLINE and not download
    headers = {"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"}
    if inline and ctype.startswith("image/"):
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return Response(data, media_type=ctype if inline else "application/octet-stream", headers=headers)


# ---------------------------------------------------------------- Inbox labels (Gmail labels, two-way)

_label_full: dict = {}          # account id -> time of the last wide (45-day) label read

TAG_PALETTE = ["#DC2626", "#EA580C", "#D97706", "#CA8A04", "#16A34A", "#059669", "#0891B2", "#2563EB",
               "#4F46E5", "#7C3AED", "#C026D3", "#DB2777", "#64748B"]


def _tag_color(name: str, saved: dict = None) -> str:
    if saved and name in saved:
        return saved[name]
    n = name.lower()
    for words, c in ((("angry", "dispute", "chargeback", "fraud", "legal"), "#DC2626"),
                     (("follow", "pending", "waiting"), "#CA8A04"), (("agreed", "resolved", "done", "closed"), "#16A34A"),
                     (("funnel",), "#EA580C"), (("invoice", "payment", "refund"), "#2563EB"),
                     (("return", "exchange"), "#0891B2"), (("address",), "#7C3AED"), (("marketing",), "#DB2777")):
        if any(w in n for w in words):
            return c
    return TAG_PALETTE[sum(map(ord, name)) % len(TAG_PALETTE)]


def _tag_colors() -> dict:
    with db._conn() as con:
        return {r[0]: r[1] for r in con.execute("SELECT name, color FROM tag_colors")}


async def _sync_labels(acct: dict):
    """Gmail → EcomVital: the mailbox's label list and each recent email's labels."""
    wide = time.time() - _label_full.get(acct["id"], 0) > 1800
    names, found = await asyncio.to_thread(mail_labels.sync, acct["address"], acct["password"], 45 if wide else 4)
    if wide:
        _label_full[acct["id"]] = time.time()
    with db._conn() as con:
        con.execute("DELETE FROM mail_tags WHERE account_id = ?", (acct["id"],))
        con.executemany("INSERT OR IGNORE INTO mail_tags (account_id, name) VALUES (?, ?)", [(acct["id"], n) for n in names])
        if found:
            mids = list(found)
            for i in range(0, len(mids), 500):
                part = mids[i:i + 500]
                rows = con.execute(f"SELECT id, message_id FROM mail_messages WHERE account_id = ? AND message_id IN"
                                   f" ({','.join('?' * len(part))})", (acct["id"], *part)).fetchall()
                for r in rows:
                    con.execute("DELETE FROM mail_message_tags WHERE message_id = ?", (r[0],))
                    con.executemany("INSERT OR IGNORE INTO mail_message_tags (message_id, name) VALUES (?, ?)",
                                    [(r[0], n) for n in found.get(r[1], [])])


def _tags_of(message_ids: list) -> list:
    if not message_ids:
        return []
    with db._conn() as con:
        return sorted({r[0] for r in con.execute(
            f"SELECT name FROM mail_message_tags WHERE message_id IN ({','.join('?' * len(message_ids))})", message_ids)},
            key=str.lower)


@app.get("/api/inbox/labels")
def api_inbox_labels(account: int = None):
    """Every label (merged across mailboxes by name) with how many open tickets / emails carry it."""
    colors = _tag_colors()
    with db._conn() as con:
        q = "SELECT DISTINCT name FROM mail_tags" + (" WHERE account_id = ?" if account else "")
        names = [r[0] for r in con.execute(q, (account,) if account else ())]
        acct_sql = " AND m.account_id = ?" if account else ""
        counts = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT g.name, COUNT(DISTINCT CASE WHEN t.status = 'open' THEN t.id END), COUNT(DISTINCT m.id)"
            " FROM mail_message_tags g JOIN mail_messages m ON m.id = g.message_id"
            " LEFT JOIN tickets t ON t.id = m.ticket_id WHERE 1=1" + acct_sql + " GROUP BY g.name",
            (account,) if account else ())}
    names = sorted(set(names) | set(counts), key=str.lower)
    return {"labels": [{"name": n, "color": _tag_color(n, colors), "open": counts.get(n, (0, 0))[0],
                        "emails": counts.get(n, (0, 0))[1]} for n in names]}


@app.post("/api/inbox/labels")
async def api_inbox_label_change(payload: dict):
    """Add / remove a label on a ticket (its whole Gmail conversation) or a single email; also sets a colour."""
    action = payload.get("action")
    name = (payload.get("name") or "").strip()
    if action == "color":
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", payload.get("color") or ""):
            raise HTTPException(400, "Pick a colour.")
        with db._conn() as con:
            con.execute("INSERT OR REPLACE INTO tag_colors (name, color) VALUES (?, ?)", (name, payload["color"]))
        return {"ok": True}
    if action not in ("add", "remove") or not name or len(name) > 80 or SYSTEM_LABEL.match(name):
        raise HTTPException(400, "Pick a label.")
    with db._conn() as con:
        if payload.get("ticket_id"):
            t = con.execute("SELECT account_id FROM tickets WHERE id = ?", (int(payload["ticket_id"]),)).fetchone()
            if not t:
                raise HTTPException(404, "That ticket no longer exists.")
            msgs = [dict(r) for r in con.execute("SELECT id, message_id, direction FROM mail_messages WHERE ticket_id = ?"
                                                 " ORDER BY date DESC", (int(payload["ticket_id"]),))]
            account_id = t[0]
        else:
            m = con.execute("SELECT id, message_id, direction, account_id FROM mail_messages WHERE id = ?",
                            (int(payload.get("message_id") or 0),)).fetchone()
            if not m:
                raise HTTPException(404, "That email no longer exists.")
            msgs, account_id = [dict(m)], m["account_id"]
    acct = next((a for a in db.list_mail_accounts() if a["id"] == account_id), None)
    if not acct:
        raise HTTPException(404, "That mailbox was removed.")
    target = next((x for x in msgs if x["message_id"] and x["direction"] == "in"), None) or \
        next((x for x in msgs if x["message_id"]), None)
    if not target:
        raise HTTPException(400, "Gmail can't find this conversation (no Message-ID).")
    try:
        await asyncio.to_thread(mail_labels.change, acct["address"], acct["password"], target["message_id"], name,
                                action == "add")
        # other emails of the ticket that Gmail keeps in separate conversations
        for x in msgs:
            if x["message_id"] and x is not target and action == "remove":
                await asyncio.to_thread(mail_labels.change, acct["address"], acct["password"], x["message_id"], name, False)
    except mail_labels.LabelError as e:
        raise HTTPException(400, str(e))
    with db._conn() as con:
        con.execute("INSERT OR IGNORE INTO mail_tags (account_id, name) VALUES (?, ?)", (account_id, name))
        for x in msgs:
            if action == "add":
                con.execute("INSERT OR IGNORE INTO mail_message_tags (message_id, name) VALUES (?, ?)", (x["id"], name))
            else:
                con.execute("DELETE FROM mail_message_tags WHERE message_id = ? AND name = ?", (x["id"], name))
            sug = con.execute("SELECT tag_suggest FROM mail_messages WHERE id = ?", (x["id"],)).fetchone()
            if sug and sug[0] and name in sug[0]:
                con.execute("UPDATE mail_messages SET tag_suggest = ? WHERE id = ?",
                            (json.dumps([g for g in json.loads(sug[0]) if g != name]), x["id"]))
    return {"ok": True, "tags": _tags_of([x["id"] for x in msgs])}


SYSTEM_LABEL = mail_labels.SYSTEM


async def _suggest_labels():
    """AI suggests labels for new customer emails, learning from how the team labelled past ones."""
    key = db.get_setting("anthropic_api_key")
    if not key:
        return
    since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with db._conn() as con:
        todo = [dict(r) for r in con.execute(
            "SELECT m.id, m.account_id, m.subject, m.text FROM mail_messages m WHERE m.direction = 'in'"
            " AND m.tag_suggest IS NULL AND m.date >= ? AND m.ticket_id IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM mail_message_tags g WHERE g.message_id = m.id)"
            " ORDER BY m.date DESC LIMIT 12", (since,))]
    for m in todo:
        with db._conn() as con:
            names = [r[0] for r in con.execute("SELECT name FROM mail_tags WHERE account_id = ?", (m["account_id"],))]
            examples = [dict(r) for r in con.execute(
                "SELECT m.subject, substr(m.text, 1, 300) text, group_concat(g.name, ' | ') labels"
                " FROM mail_message_tags g JOIN mail_messages m ON m.id = g.message_id"
                " WHERE m.account_id = ? AND m.direction = 'in' GROUP BY m.id ORDER BY m.date DESC LIMIT 40",
                (m["account_id"],))]
        if not names:
            with db._conn() as con:
                con.execute("UPDATE mail_messages SET tag_suggest = '[]' WHERE id = ?", (m["id"],))
            continue
        try:
            got = await ai_brain.suggest_labels(key, m, names, examples)
        except Exception as e:
            print(f"Label suggestion for {m['id']} failed: {e}", flush=True)
            got = []
        with db._conn() as con:
            con.execute("UPDATE mail_messages SET tag_suggest = ? WHERE id = ?",
                        (json.dumps([g for g in got if g in names][:3]), m["id"]))


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page():
    return _page("inbox.html", ("inbox.js", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/inbox/mailboxes")
def api_inbox_mailboxes():
    """The Inbox store picker, for anyone with the Inbox desk: each mailbox's id, store and address only."""
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT id, address, store_id FROM mail_accounts ORDER BY id")]
    return {"accounts": [{"id": a["id"], "address": a["address"], "store": stores.get(a["store_id"], "")} for a in rows]}


@app.get("/api/mail-accounts")
def api_mail_accounts():
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        counts = {r["account_id"]: r["n"] for r in con.execute(
            "SELECT account_id, COUNT(*) n FROM mail_messages GROUP BY account_id")}
    cid = gads._cfg()[0] or ""
    project = cid.split("-", 1)[0] if cid[:1].isdigit() else ""
    return {"gmail_api_link": "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                              + (f"?project={project}" if project else ""),
            "accounts": [{"id": a["id"], "address": a["address"], "store": stores.get(a["store_id"], ""),
                          "store_id": a["store_id"], "last_checked": a["last_checked"],
                          "error": a["last_error"], "emails": counts.get(a["id"], 0),
                          "can_send": bool(a.get("gmail_token")), "send_since": a.get("gmail_at"),
                          "auto_ask": bool(a.get("auto_ask"))}
                         for a in db.list_mail_accounts()]}


@app.post("/api/mail-accounts")
async def api_add_mail_account(payload: dict):
    """Connect a store's support mailbox with an app password (tested before saving)."""
    address = (payload.get("address") or "").strip().lower()
    password = re.sub(r"\s+", "", payload.get("password") or "")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
        raise HTTPException(400, "Type the mailbox's full email address.")
    if not password:
        raise HTTPException(400, "Paste the mailbox's App password.")
    try:
        await asyncio.to_thread(mail_client.check, address, password)
    except mail_client.MailError as e:
        raise HTTPException(400, str(e))
    store_id = payload.get("store_id") or None
    aid = db.add_mail_account(int(store_id) if store_id else None, address, password)
    acct = next(a for a in db.list_mail_accounts() if a["id"] == aid)
    asyncio.create_task(_read_mailbox(acct))       # big mailboxes take a minute or two
    return {"ok": True, "id": aid}


@app.delete("/api/mail-accounts/{account_id}")
def api_remove_mail_account(account_id: int):
    db.delete_mail_account(account_id)
    return {"ok": True}


def _store_names() -> dict:
    return {x["id"]: x["name"].strip() for x in db.list_stores()}


@app.get("/api/inbox/counts")
def api_inbox_counts():
    now = datetime.now(timezone.utc).isoformat()
    with db._conn() as con:
        one = lambda q, *a: con.execute(q, a).fetchone()[0]
        awake = "(snoozed_until IS NULL OR snoozed_until <= ?)"
        return {
            "all": one("SELECT COUNT(*) FROM mail_messages WHERE direction = 'in'"),
            "customer": one("SELECT COUNT(*) FROM mail_messages WHERE direction = 'in' AND category = 'customer'"),
            "inquiry": one(f"SELECT COUNT(*) FROM tickets WHERE kind IN ('inquiry','legal') AND status = 'open' AND {awake}", now),
            "inquiry_done": one("SELECT COUNT(*) FROM tickets WHERE kind IN ('inquiry','legal') AND status = 'closed'"),
            "scm": one(f"SELECT COUNT(*) FROM tickets WHERE kind = 'support' AND status = 'open' AND ',' || labels || ',' LIKE '%,scm,%' AND {awake}", now),
            "cs": one(f"SELECT COUNT(*) FROM tickets WHERE kind = 'support' AND status = 'open' AND ',' || labels || ',' LIKE '%,cs,%' AND {awake}", now),
            "unlabelled": one(f"SELECT COUNT(*) FROM tickets WHERE kind = 'support' AND status = 'open' AND labels = '' AND {awake}", now),
            "waiting_us": one(f"SELECT COUNT(*) FROM tickets WHERE status = 'open' AND waiting = 'us' AND {awake}", now),
            "snoozed": one("SELECT COUNT(*) FROM tickets WHERE status = 'open' AND snoozed_until > ?", now),
            "escalated": one("SELECT COUNT(*) FROM tickets WHERE status = 'open' AND escalated = 1"),
            "records": one("SELECT COUNT(*) FROM tickets WHERE kind = 'support' AND status = 'closed'"),
            "ai_error": _ai_state.get("error"),
            "questions": one("SELECT COUNT(*) FROM ai_questions WHERE answer IS NULL OR answer = ''"),
        }


@app.get("/api/inbox/messages")
def api_inbox_messages(account: int = None, view: str = "all", label: str = ""):
    stores = _store_names()
    cat = {"customer": "customer", "all": None}.get(view)
    rows = db.mail_list([account] if account else None, category=cat)
    if view == "label":
        with db._conn() as con:
            ids = {r[0] for r in con.execute(
                "SELECT g.message_id FROM mail_message_tags g JOIN mail_messages m ON m.id = g.message_id"
                " WHERE g.name = ? AND m.ticket_id IS NULL AND m.direction = 'in'", (label,))}
        rows = [r for r in db.mail_list([account] if account else None, limit=2000) if r["id"] in ids]
    colors = _tag_colors()
    tagmap = defaultdict(list)
    if rows:
        with db._conn() as con:
            for mid, n in con.execute(f"SELECT message_id, name FROM mail_message_tags WHERE message_id IN"
                                      f" ({','.join(str(int(r['id'])) for r in rows)})"):
                tagmap[mid].append({"name": n, "color": _tag_color(n, colors)})
    for r in rows:
        r["store"] = stores.get(r["store_id"], "")
        r["attachments"] = len(json.loads(r["attachments"] or "[]"))
        r["tags"] = tagmap.get(r["id"], [])
    return {"messages": rows}


@app.get("/api/inbox/messages/{message_id}")
def api_inbox_message(message_id: int):
    m = db.mail_get(message_id)
    if not m:
        raise HTTPException(404, "That email no longer exists.")
    m["store"] = _store_names().get(m["store_id"], "")
    m["attachments"] = json.loads(m["attachments"] or "[]")
    m.pop("html", None)          # shown as plain text (safe)
    m.pop("ai", None)
    acct = next((a for a in db.list_mail_accounts() if a["id"] == m["account_id"]), None)
    m["can_send"] = bool(acct and acct.get("gmail_token"))
    m["mailbox"] = (acct or {}).get("address", "")
    m["reply_lang"] = LANG_NAME.get(db.get_setting(f"track_lang:{m['store_id']}") or "en", "English")
    m.update(_label_info(m["account_id"], [m["id"]], [m]))
    return m


def _label_info(account_id: int, message_ids: list, msgs: list) -> dict:
    colors = _tag_colors()
    with db._conn() as con:
        names = [r[0] for r in con.execute("SELECT name FROM mail_tags WHERE account_id = ? ORDER BY name COLLATE NOCASE",
                                           (account_id,))]
    tags = _tags_of(message_ids)
    sug = []
    for x in msgs:
        try:
            sug += json.loads(x.get("tag_suggest") or "[]")
        except ValueError:
            pass
    return {"tags": [{"name": n, "color": _tag_color(n, colors)} for n in tags],
            "tag_suggest": [{"name": n, "color": _tag_color(n, colors)} for n in dict.fromkeys(sug) if n not in tags],
            "all_tags": [{"name": n, "color": _tag_color(n, colors)} for n in names]}


TRANSLATE_LANGS = ("English", "French", "Spanish", "German", "Italian", "Portuguese", "Dutch", "Arabic",
                   "Chinese", "Hindi", "Tamil", "Sinhala", "Urdu", "Tagalog")


@app.get("/api/inbox/translate")
async def api_inbox_translate(ids: str = "", lang: str = "English", quotes: str = ""):
    """Translations of these emails (saved, so each email is only translated once per language).
    quotes: emails whose quoted earlier conversation should be translated too."""
    if lang not in TRANSLATE_LANGS:
        raise HTTPException(400, "Pick a language from the list.")
    wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:60]
    qwanted = [int(x) for x in quotes.split(",") if x.strip().isdigit()][:20]
    out_quotes = await _translate_quotes(qwanted, lang) if qwanted else {}
    if not wanted:
        return {"translations": {}, "quotes": out_quotes}
    marks = ",".join("?" * len(wanted))
    with db._conn() as con:
        done = {r[0]: r[1] for r in con.execute(
            f"SELECT message_id, text FROM mail_translations WHERE lang = ? AND message_id IN ({marks})", (lang, *wanted))}
        todo = [dict(r) for r in con.execute(
            f"SELECT id, text FROM mail_messages WHERE id IN ({marks})", wanted) if r[0] not in done]
    # Only the new part of each email: the quoted history is the earlier emails, translated on their own.
    todo = [{"id": m["id"], "text": ai_brain.new_part(m["text"])} for m in todo]
    todo = [m for m in todo if m["text"].strip()]
    if todo:
        key = db.get_setting("anthropic_api_key")
        if not key:
            raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")

        async def one(m):                               # every email at the same time
            try:
                return m, (await ai_brain.translate(key, [m["text"]], lang))[0], None
            except anthropic.AuthenticationError:
                return m, None, "Anthropic didn't accept the API key (Settings → AI)."
            except (anthropic.APIError, ValueError) as e:
                return m, None, "Couldn't translate right now: " + str(e)[:150]
        results = await asyncio.gather(*[one(m) for m in todo])
        with db._conn() as con:
            for m, t, err in results:
                if t is not None:
                    con.execute("INSERT OR REPLACE INTO mail_translations (message_id, lang, text) VALUES (?, ?, ?)",
                                (m["id"], lang, t))
                    done[m["id"]] = t
        errs = [e for _, t, e in results if e]
        if errs and not done:
            raise HTTPException(400, errs[0])
    return {"translations": {str(k): v for k, v in done.items()}, "quotes": out_quotes}


async def _translate_quotes(ids: list, lang: str) -> dict:
    """The quoted part of these emails, translated (saved under '<language>|quote')."""
    key_lang = f"{lang}|quote2"      # v2: fixed quote extraction (old saved ones ignored)
    marks = ",".join("?" * len(ids))
    with db._conn() as con:
        done = {r[0]: r[1] for r in con.execute(
            f"SELECT message_id, text FROM mail_translations WHERE lang = ? AND message_id IN ({marks})", (key_lang, *ids))}
        todo = [{"id": r[0], "text": ai_brain.quoted_part(r[1])} for r in con.execute(
            f"SELECT id, text FROM mail_messages WHERE id IN ({marks})", ids) if r[0] not in done]
    todo = [m for m in todo if m["text"].strip()]
    key = db.get_setting("anthropic_api_key")
    if todo and key:
        async def one(m):
            try:
                return m, (await ai_brain.translate(key, [m["text"]], lang))[0]
            except (anthropic.APIError, ValueError):
                return m, None
        with db._conn() as con:
            for m, t in await asyncio.gather(*[one(m) for m in todo]):
                if t is not None:
                    con.execute("INSERT OR REPLACE INTO mail_translations (message_id, lang, text) VALUES (?, ?, ?)",
                                (m["id"], key_lang, t))
                    done[m["id"]] = t
    return {str(k): v for k, v in done.items()}


_TICKET_VIEWS = {
    "scm": "kind = 'support' AND status = 'open' AND ',' || labels || ',' LIKE '%,scm,%' AND {awake}",
    "cs": "kind = 'support' AND status = 'open' AND ',' || labels || ',' LIKE '%,cs,%' AND {awake}",
    "unlabelled": "kind = 'support' AND status = 'open' AND labels = '' AND {awake}",
    "inquiry": "kind IN ('inquiry','legal') AND status = 'open' AND {awake}",
    "inquiry_done": "kind IN ('inquiry','legal') AND status = 'closed'",
    "records": "kind = 'support' AND status = 'closed'",
    "snoozed": "status = 'open' AND snoozed_until > :now",
    "escalated": "status = 'open' AND escalated = 1",
}


@app.get("/api/inbox/tickets")
def api_inbox_tickets(view: str = "scm", account: int = None, label: str = ""):
    args = {"now": datetime.now(timezone.utc).isoformat()}
    if view == "label":
        where = ("t.id IN (SELECT m.ticket_id FROM mail_messages m JOIN mail_message_tags g ON g.message_id = m.id"
                 " WHERE g.name = :label)")
        args["label"] = label
    else:
        where = _TICKET_VIEWS.get(view)
    if not where:
        raise HTTPException(400, "Unknown list.")
    where = where.replace("{awake}", "(snoozed_until IS NULL OR snoozed_until <= :now)")
    if account:
        where += " AND account_id = :acct"
        args["acct"] = account
    order = ("(status = 'open') DESC, last_message_at DESC" if view == "label" else
             "closed_at DESC" if "closed" in where else "(waiting = 'us') DESC, last_message_at DESC")
    stores = _store_names()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            f"SELECT t.*, (SELECT COUNT(*) FROM mail_messages m WHERE m.ticket_id = t.id) emails,"
            f" (SELECT group_concat(DISTINCT g.name) FROM mail_message_tags g JOIN mail_messages m ON m.id = g.message_id"
            f"  WHERE m.ticket_id = t.id) tags"
            f" FROM tickets t WHERE {where} ORDER BY {order} LIMIT 300", args)]
    colors = _tag_colors()
    for r in rows:
        r["store"] = stores.get(r["store_id"], "")
        r["tags"] = [{"name": n, "color": _tag_color(n, colors)} for n in sorted((r["tags"] or "").split(","), key=str.lower) if n]
    return {"tickets": rows}


def _gmail_link(address: str, message_id: str) -> str:
    from urllib.parse import quote
    mid = (message_id or "").strip("<>")
    return f"https://mail.google.com/mail/u/{quote(address)}/#search/rfc822msgid%3A{quote(mid)}" if mid else ""


async def _reply_input(request: Request) -> tuple:
    """A reply's fields and attached files (FormData with files, or plain JSON)."""
    if (request.headers.get("content-type") or "").startswith("multipart/"):
        form = await request.form()
        payload = {k: form.get(k) for k in ("text", "seen", "opened", "force")}
        payload["force"] = str(payload.get("force") or "").lower() in ("1", "true")
        files = []
        for f in form.getlist("files")[:10]:
            if hasattr(f, "read"):
                data = await f.read()
                files.append((f.filename or "file", f.content_type or "application/octet-stream", data))
        if sum(len(d) for _, _, d in files) > gmail_send.MAX_FILES_BYTES:
            raise HTTPException(400, "The attachments are bigger than 20 MB together. Send fewer or smaller files.")
        return payload, files
    return await request.json(), []


async def _send_reply(acct: dict, to: str, subject: str, text: str, mid: str, refs: str, store_id, files=None) -> None:
    """Send through the mailbox's Gmail, then read it back from Sent."""
    if not acct or not acct.get("gmail_token"):
        raise HTTPException(400, "This mailbox isn't signed in for sending yet (Settings → Support mailboxes → Sign in to send).")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to or ""):
        raise HTTPException(400, "There's no email address to reply to.")
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    thread = await asyncio.to_thread(gmail_send.thread_id, acct["address"], acct["password"], mid or "")
    try:
        await gmail_send.send(acct["gmail_token"], acct["address"], (store or {}).get("name", "").strip(), to,
                              subject or "", text, in_reply_to=mid or "", references=refs or "", thread=thread,
                              files=files)
    except gmail_send.SendError as e:
        raise HTTPException(400, str(e))

    async def pick_up():
        await asyncio.sleep(4)
        a = next((x for x in db.list_mail_accounts() if x["id"] == acct["id"]), None)
        if a:
            await _read_mailbox(a)
            await _sort_mail()
    asyncio.create_task(pick_up())


@app.post("/api/inbox/messages/{message_id}/reply")
async def api_inbox_message_reply(message_id: int, request: Request):
    """Reply to any email in All mail (no ticket needed)."""
    payload, files = await _reply_input(request)
    text = (payload.get("text") or "").strip()
    if not text and not files:
        raise HTTPException(400, "Write the reply first.")
    m = db.mail_get(message_id)
    if not m:
        raise HTTPException(404, "That email no longer exists.")
    acct = next((a for a in db.list_mail_accounts() if a["id"] == m["account_id"]), None)
    to = m["from_addr"] if m.get("direction", "in") == "in" else m["to_addr"]
    await _send_reply(acct, (to or "").strip(), m["subject"], text, m["message_id"], m.get("refs"), m["store_id"], files)
    return {"ok": True}


_recent_replies: dict = {}      # ticket id -> (time, who) of the last reply sent from EcomVital


@app.post("/api/inbox/tickets/{ticket_id}/reply")
async def api_inbox_reply(ticket_id: int, request: Request):
    """Send a reply from the store's mailbox through Gmail (it shows in Gmail's Sent and the same thread).
    seen: newest email id the sender had on screen. If a teammate replied since, ask first (409)."""
    payload, files = await _reply_input(request)
    text = (payload.get("text") or "").strip()
    if not text and not files:
        raise HTTPException(400, "Write the reply first.")
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    if not payload.get("force"):
        seen = int(payload.get("seen") or 0)
        with db._conn() as con:
            newer = con.execute("SELECT direction, from_addr, date FROM mail_messages WHERE ticket_id = ? AND id > ?"
                                " ORDER BY id DESC LIMIT 1", (ticket_id, seen)).fetchone() if seen else None
        recent = _recent_replies.get(ticket_id)
        if recent and recent[1] != user and time.time() - recent[0] < 900 and recent[0] > float(payload.get("opened") or 0):
            mins = max(1, round((time.time() - recent[0]) / 60))
            raise HTTPException(409, f"{recent[1] or 'Someone'} replied to this customer {mins} min ago from EcomVital.")
        if newer:
            who = "Your team" if newer["direction"] == "out" else "The customer"
            raise HTTPException(409, f"{who} sent a new email in this conversation since you opened it.")
    with db._conn() as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not t:
            raise HTTPException(404, "That ticket no longer exists.")
        acct = con.execute("SELECT * FROM mail_accounts WHERE id = ?", (t["account_id"],)).fetchone()
        last = con.execute("SELECT message_id, refs, subject FROM mail_messages WHERE ticket_id = ? AND direction = 'in'"
                           " ORDER BY date DESC LIMIT 1", (ticket_id,)).fetchone() or \
            con.execute("SELECT message_id, refs, subject FROM mail_messages WHERE ticket_id = ? ORDER BY date DESC LIMIT 1",
                        (ticket_id,)).fetchone()
    if not acct or not acct["gmail_token"]:
        raise HTTPException(400, "This mailbox isn't signed in for sending yet (Settings → Support mailboxes → Sign in to send).")
    to = (t["customer_email"] or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
        raise HTTPException(400, "This ticket has no customer email address to reply to.")
    store = next((x for x in db.list_stores() if x["id"] == t["store_id"]), None)
    mid = (last["message_id"] if last else "") or ""
    thread = await asyncio.to_thread(gmail_send.thread_id, acct["address"], acct["password"], mid)
    try:
        await gmail_send.send(acct["gmail_token"], acct["address"], (store or {}).get("name", "").strip(), to,
                              (last["subject"] if last else "") or t["subject"] or "", text,
                              in_reply_to=mid, references=(last["refs"] if last else "") or "", thread=thread,
                              files=files)
    except gmail_send.SendError as e:
        raise HTTPException(400, str(e))
    _recent_replies[ticket_id] = (time.time(), user)
    u = getattr(request.state, "user", None) or {}
    with db._conn() as con:
        con.execute("UPDATE tickets SET handler = ?, handler_email = ?, handler_at = ? WHERE id = ? AND handler IS NULL",
                    (_display_name(u), u.get("email"), datetime.now(timezone.utc).isoformat(), ticket_id))
    print(f"Reply sent on ticket {ticket_id} by {user}", flush=True)

    async def pick_up():                      # read it back from Sent so the ticket shows "we replied"
        await asyncio.sleep(4)
        a = next((x for x in db.list_mail_accounts() if x["id"] == acct["id"]), None)
        if a:
            await _read_mailbox(a)
            await _sort_mail()
    asyncio.create_task(pick_up())
    return {"ok": True}


@app.post("/api/inbox/translate-text")
async def api_inbox_translate_text(payload: dict):
    """Translate a reply before sending (e.g. written in English, sent in French)."""
    lang = payload.get("lang") or "English"
    if lang not in TRANSLATE_LANGS:
        raise HTTPException(400, "Pick a language from the list.")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write the reply first.")
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    try:
        return {"text": (await ai_brain.translate(key, [text[:12000]], lang))[0]}
    except (anthropic.APIError, ValueError) as e:
        raise HTTPException(400, f"Couldn't translate right now: {str(e)[:150]}")


@app.get("/api/inbox/tickets/{ticket_id}")
def api_inbox_ticket(ticket_id: int, request: Request):
    with db._conn() as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not t:
            raise HTTPException(404, "That ticket no longer exists.")
        t = dict(t)
        msgs = [dict(r) for r in con.execute(
            "SELECT id, direction, from_name, from_addr, to_addr, subject, date, text, attachments, message_id, tag_suggest"
            " FROM mail_messages WHERE ticket_id = ? ORDER BY date", (ticket_id,))]
        acct = con.execute("SELECT address, gmail_token FROM mail_accounts WHERE id = ?", (t["account_id"],)).fetchone()
    for m in msgs:
        m["attachments"] = json.loads(m["attachments"] or "[]")
    t["store"] = _store_names().get(t["store_id"], "")
    t["mailbox"] = acct["address"] if acct else ""
    t["can_send"] = bool(acct and acct["gmail_token"])
    t["me"] = (getattr(request.state, "user", None) or {}).get("email")
    t.update(_label_info(t["account_id"], [m["id"] for m in msgs], [m for m in msgs if m["direction"] == "in"][-1:]))
    for m in msgs:
        m.pop("tag_suggest", None)
    t["reply_lang"] = LANG_NAME.get(db.get_setting(f"track_lang:{t['store_id']}") or "en", "English")
    t["gmail_link"] = _gmail_link(t["mailbox"], msgs[-1]["message_id"]) if msgs else ""
    return {"ticket": t, "messages": msgs}


def _display_name(u: dict) -> str:
    """First name only, as typed when the person was added (else from their email)."""
    name = (u.get("name") or "").strip()
    if name:
        return name.split()[0][:40]
    return re.split(r"[._\-+0-9]", (u.get("email") or "team").split("@")[0])[0].title() or "Team"


@app.post("/api/inbox/tickets/{ticket_id}")
def api_inbox_ticket_action(ticket_id: int, payload: dict, request: Request):
    """Team actions: close (override), reopen, snooze, wake, escalate, labels, kind."""
    user = (getattr(request.state, "user", None) or {}).get("email", "team")
    act = payload.get("action")
    now = datetime.now(timezone.utc).isoformat()
    with db._conn() as con:
        if not con.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
            raise HTTPException(404, "That ticket no longer exists.")
        if act == "close":
            con.execute("UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?, close_note = ?,"
                        " snoozed_until = NULL WHERE id = ?",
                        (now, user, (payload.get("note") or "Closed by the team")[:300], ticket_id))
        elif act == "reopen":
            con.execute("UPDATE tickets SET status = 'open', closed_at = NULL, closed_by = NULL,"
                        " close_note = NULL, waiting = 'us' WHERE id = ?", (ticket_id,))
        elif act == "snooze":
            until = payload.get("until") or ""
            try:
                when = datetime.fromisoformat(until).replace(tzinfo=CASH_TZ) if len(until) == 10 \
                    else datetime.fromisoformat(until)
            except ValueError:
                raise HTTPException(400, "Pick a date to snooze until.")
            con.execute("UPDATE tickets SET snoozed_until = ? WHERE id = ?",
                        (when.astimezone(timezone.utc).isoformat(), ticket_id))
        elif act == "wake":
            con.execute("UPDATE tickets SET snoozed_until = NULL WHERE id = ?", (ticket_id,))
        elif act == "escalate":
            con.execute("UPDATE tickets SET escalated = ? WHERE id = ?",
                        (1 if payload.get("on", True) else 0, ticket_id))
        elif act == "labels":
            labels = [x for x in ("scm", "cs") if x in (payload.get("labels") or [])]
            con.execute("UPDATE tickets SET labels = ?, kind = 'support' WHERE id = ?",
                        (",".join(labels), ticket_id))
        elif act == "kind":
            kind = payload.get("kind")
            if kind not in ("support", "inquiry", "legal"):
                raise HTTPException(400, "Unknown ticket type.")
            con.execute("UPDATE tickets SET kind = ? WHERE id = ?", (kind, ticket_id))
        elif act in ("claim", "release"):
            u = getattr(request.state, "user", None) or {}
            if act == "claim":
                con.execute("UPDATE tickets SET handler = ?, handler_email = ?, handler_at = ? WHERE id = ?",
                            (_display_name(u), u.get("email"), now, ticket_id))
            else:
                con.execute("UPDATE tickets SET handler = NULL, handler_email = NULL, handler_at = NULL WHERE id = ?",
                            (ticket_id,))
        elif act == "not_customer":
            con.execute("UPDATE mail_messages SET category = 'other', ticket_id = NULL WHERE ticket_id = ?",
                        (ticket_id,))
            con.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        else:
            raise HTTPException(400, "Unknown action.")
    return {"ok": True}


@app.post("/api/inbox/messages/{message_id}/ticket")
def api_inbox_make_ticket(message_id: int, payload: dict):
    """Turn an email the AI marked "other" into a ticket (one-click correction)."""
    kind = payload.get("kind") or "support"
    if kind not in ("support", "inquiry", "legal"):
        raise HTTPException(400, "Unknown ticket type.")
    with db._conn() as con:
        m = con.execute("SELECT * FROM mail_messages WHERE id = ?", (message_id,)).fetchone()
        if not m:
            raise HTTPException(404, "That email no longer exists.")
        if m["ticket_id"]:
            return {"ok": True, "ticket_id": m["ticket_id"]}
        acct = con.execute("SELECT store_id FROM mail_accounts WHERE id = ?", (m["account_id"],)).fetchone()
        tid = con.execute(
            "INSERT INTO tickets (account_id, store_id, kind, labels, customer_email, customer_name, subject,"
            " summary, status, waiting, last_message_at, created_at) VALUES (?,?,?,?,?,?,?,?, 'open','us',?,?)",
            (m["account_id"], acct["store_id"] if acct else None, kind, payload.get("labels") or "",
             (m["from_addr"] or "").lower(), m["from_name"], m["subject"], m["subject"],
             m["date"], m["date"])).lastrowid
        cat = {"support": "customer"}.get(kind, kind)
        con.execute("UPDATE mail_messages SET ticket_id = ?, category = ?, processed = 1 WHERE id = ?",
                    (tid, cat, message_id))
    return {"ok": True, "ticket_id": tid}


# ---------------------------------------------------------------- order panel + AI drafts

async def _ticket_orders(t: dict) -> tuple:
    """(orders, store) for a ticket: by the order numbers it mentions, else by email."""
    store = next((x for x in db.list_stores() if x["id"] == t["store_id"]), None)
    if not store or demo.is_demo(store) or not store.get("access_token"):
        return [], store
    names = [n.strip() for n in (t.get("orders") or "").split(",") if n.strip()]
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    got = await client.lookup_orders(names=names) if names else []
    if not got and t.get("customer_email"):
        got = await client.lookup_orders(email=t["customer_email"])
    handle = store["shop_domain"].split(".")[0]
    for o in got:
        o["admin_url"] = f"https://admin.shopify.com/store/{handle}/orders/{o['id']}"
    return got, store


@app.get("/api/inbox/tickets/{ticket_id}/orders")
async def api_ticket_orders(ticket_id: int):
    with db._conn() as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        raise HTTPException(404, "That ticket no longer exists.")
    try:
        orders, _ = await _ticket_orders(dict(t))
    except ShopifyError as e:
        return {"orders": [], "error": str(e)}
    # Each order's parcels as SCM sees them (17TRACK status, latest update)
    store = next((x for x in db.list_stores() if x["id"] == t["store_id"]), None)
    ids = [str(o["id"]) for o in orders]
    by_order = defaultdict(list)
    if ids and store:
        with db._conn() as con:
            rows = con.execute(
                f"SELECT id, order_id, number, company, carrier_name, last_mile, last_mile_number, status, sub_status,"
                f" last_event, last_event_at, last_location, eta_from, eta_to, delivered_at, transit_days, cancelled"
                f" FROM shipments WHERE store_id = ? AND order_id IN ({','.join('?' * len(ids))}) ORDER BY id",
                (t["store_id"], *ids)).fetchall()
        for r in rows:
            r = dict(r)
            if r["number"]:
                r["track17"] = f"https://t.17track.net/en#nums={r['number']}"
                r["tracking_page"] = f"https://{store['shop_domain']}/apps/track?nums={r['number']}"
            else:
                r["status"] = "awaiting"
            r["scm_url"] = f"/scm?open={r['id']}"
            by_order[r["order_id"]].append(r)
    for o in orders:
        o["parcels"] = by_order.get(str(o["id"]), [])
    return {"orders": orders}


def _orders_text(orders: list) -> str:
    lines = []
    for o in orders:
        items = "; ".join(f"{i['quantity']}× {i['title']}{(' - ' + i['variant']) if i['variant'] else ''}"
                          for i in o["items"])
        track = "; ".join(f"{t['company'] or ''} {t['number'] or ''} {t['url'] or ''}".strip()
                          for t in o["tracking"]) or "no tracking yet"
        refund = f", refunded {o['refunded']:.2f}" if o["refunded"] else ""
        lines.append(f"{o['name']} placed {o['created'][:10]}, {o['total']:.2f} {o['currency']}, payment "
                     f"{o['financial']}, fulfilment {o['fulfillment']}"
                     f"{', CANCELLED' if o['cancelled'] else ''}{refund}"
                     f", ships to {o['ship_to'] or '?'}. Items: {items}. Tracking: {track}.")
    return "\n".join(lines)


def _knowledge() -> str:
    with db._conn() as con:
        sops = [dict(r) for r in con.execute("SELECT title, body FROM ai_sops ORDER BY id")]
        qa = [dict(r) for r in con.execute("SELECT question, answer FROM ai_questions WHERE answer IS NOT NULL"
                                           " AND answer != '' ORDER BY id")]
    parts = []
    if sops:
        parts.append("## SOPs (always follow)\n" + "\n\n".join(f"### {x['title']}\n{x['body']}" for x in sops))
    if qa:
        parts.append("## Owner's answers (always follow)\n" + "\n".join(f"Q: {x['question']}\nA: {x['answer']}" for x in qa))
    book = db.get_setting("ai_playbook")
    if book:
        parts.append("## Playbook (learned from past conversations)\n" + book)
    return "\n\n".join(parts) or "(No SOPs or playbook yet: follow the past replies and be careful.)"


@app.post("/api/inbox/tickets/{ticket_id}/draft")
async def api_ticket_draft(ticket_id: int):
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    with db._conn() as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not t:
            raise HTTPException(404, "That ticket no longer exists.")
        t = dict(t)
        msgs = [dict(r) for r in con.execute(
            "SELECT direction, from_name, date, text FROM mail_messages WHERE ticket_id = ? ORDER BY date",
            (ticket_id,))]
    thread = "\n\n".join(f"{'CUSTOMER' if m['direction'] == 'in' else 'US'} ({m['date'][:16]}):\n"
                          f"{ai_coach.clean_text(m['text'], 3000)}" for m in msgs)
    try:
        orders, store = await _ticket_orders(t)
    except ShopifyError:
        orders, store = [], None
    examples = json.loads(db.get_setting("ai_examples") or "[]")
    last_in = next((m["text"] for m in reversed(msgs) if m["direction"] == "in"), "")
    try:
        text = await ai_coach.draft(key, _knowledge(), thread, _orders_text(orders),
                                    (store or {}).get("name", "").strip(), ai_coach.similar(examples, last_in))
    except ai_coach.CoachError as e:
        raise HTTPException(400, str(e))
    return {"draft": text}


# ---------------------------------------------------------------- AI training (owner)

_learn: dict = {"running": False}


@app.get("/api/ai-training")
def api_training():
    with db._conn() as con:
        sops = [dict(r) for r in con.execute("SELECT * FROM ai_sops ORDER BY id")]
        qs = [dict(r) for r in con.execute("SELECT * FROM ai_questions ORDER BY (answer IS NULL OR answer = '') DESC, id DESC")]
        mail = con.execute("SELECT COUNT(*) n, MIN(date) first FROM mail_messages").fetchone()
    examples = json.loads(db.get_setting("ai_examples") or "[]")
    return {"sops": sops, "questions": qs, "playbook": db.get_setting("ai_playbook") or "",
            "learned": json.loads(db.get_setting("ai_learned") or "null"), "examples": len(examples),
            "emails": mail["n"], "emails_from": mail["first"], "job": _learn}


@app.post("/api/ai-training/sops")
async def api_add_sop(request: Request, filename: str = None):
    """Add an SOP: JSON {title, body}, or a raw PDF / text file with ?filename=."""
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    if filename:
        data = await request.body()
        try:
            body = invoices.pdf_text(data) if data[:4] == b"%PDF" else data.decode("utf-8", "ignore")
        except invoices.InvoiceError as e:
            raise HTTPException(400, str(e))
        title = re.sub(r"\.[a-z0-9]+$", "", filename)[:120]
    else:
        payload = await request.json()
        title, body = (payload.get("title") or "").strip(), (payload.get("body") or "").strip()
    if not title or not body.strip():
        raise HTTPException(400, "Give the SOP a title and some text.")
    with db._conn() as con:
        con.execute("INSERT INTO ai_sops (title, body, created_by) VALUES (?, ?, ?)", (title, body[:60000], user))
    return {"ok": True}


@app.put("/api/ai-training/sops/{sop_id}")
def api_edit_sop(sop_id: int, payload: dict):
    with db._conn() as con:
        con.execute("UPDATE ai_sops SET title = ?, body = ?, updated_at = datetime('now') WHERE id = ?",
                    ((payload.get("title") or "").strip()[:120], (payload.get("body") or "")[:60000], sop_id))
    return {"ok": True}


@app.delete("/api/ai-training/sops/{sop_id}")
def api_delete_sop(sop_id: int):
    with db._conn() as con:
        con.execute("DELETE FROM ai_sops WHERE id = ?", (sop_id,))
    return {"ok": True}


@app.post("/api/ai-training/questions/{qid}")
def api_answer_question(qid: int, payload: dict, request: Request):
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    with db._conn() as con:
        if payload.get("dismiss"):
            con.execute("DELETE FROM ai_questions WHERE id = ?", (qid,))
        else:
            con.execute("UPDATE ai_questions SET answer = ?, answered_by = ?, answered_at = datetime('now')"
                        " WHERE id = ?", ((payload.get("answer") or "").strip()[:4000], user, qid))
    return {"ok": True}


@app.put("/api/ai-training/playbook")
def api_save_playbook(payload: dict):
    db.set_setting("ai_playbook", (payload.get("playbook") or "")[:200000])
    return {"ok": True}


async def _import_history(days: int):
    for acct in db.list_mail_accounts():
        for folder, direction in (("INBOX", "in"), ("SENT", "out")):
            _learn["step"] = f"Copying {days} days of {'sent' if direction == 'out' else 'received'} emails from {acct['address']}…"
            await asyncio.to_thread(mail_client.fetch_history, acct["address"], acct["password"], days, folder,
                                    acct["provider"], lambda b, d=direction, a=acct["id"]: db.save_mail(a, b, d))


async def _learn_job(days: int, limit: int):
    key = db.get_setting("anthropic_api_key")
    _learn.update(running=True, error=None, step="Starting…", done=0, total=0, started=time.time())
    try:
        if days:
            await _import_history(days)
        _learn["step"] = "Finding past conversations where the team replied…"
        with db._conn() as con:
            msgs = [dict(r) for r in con.execute(
                "SELECT account_id, direction, from_addr, to_addr, subject, date, text, category FROM mail_messages")]
            sops = "\n\n".join(f"### {r['title']}\n{r['body']}" for r in con.execute("SELECT * FROM ai_sops"))
            answers = "\n".join(f"Q: {r['question']}\nA: {r['answer']}" for r in con.execute(
                "SELECT * FROM ai_questions WHERE answer IS NOT NULL AND answer != ''"))
        convos = ai_coach.threads(msgs, limit)
        if not convos:
            raise ai_coach.CoachError("No past conversations with a reply from the team were found. "
                                      "Copy more email history first.")
        db.set_setting("ai_examples", json.dumps(convos, ensure_ascii=False))
        batches = [convos[i:i + 20] for i in range(0, len(convos), 20)]
        _learn.update(total=len(batches) + 1, step=f"Reading {len(convos)} conversations…")
        lessons, questions = [], []
        for b in batches:
            got = await ai_coach.learn_batch(key, b, sops[:30000])
            lessons += got.get("lessons", [])
            questions += got.get("questions", [])
            _learn["done"] += 1
        _learn["step"] = "Writing the playbook…"
        book, top = await ai_coach.write_playbook(key, lessons, sops[:60000], answers)
        db.set_setting("ai_playbook", book)
        with db._conn() as con:
            have = {r["question"].lower() for r in con.execute("SELECT question FROM ai_questions")}
            for q in top:
                if q.lower() not in have:
                    con.execute("INSERT INTO ai_questions (question) VALUES (?)", (q,))
        db.set_setting("ai_learned", json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                                 "conversations": len(convos), "lessons": len(lessons)}))
        _learn.update(done=_learn["total"], step=f"Done: learned from {len(convos)} conversations.")
    except (ai_coach.CoachError, mail_client.MailError) as e:
        _learn["error"] = str(e)
    except Exception as e:
        _learn["error"] = f"Learning stopped ({type(e).__name__}: {str(e)[:150]})"
        print(f"AI learning failed: {e}", flush=True)
    finally:
        _learn["running"] = False


@app.post("/api/ai-training/learn")
async def api_learn(payload: dict):
    if not db.get_setting("anthropic_api_key"):
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    if _learn.get("running"):
        raise HTTPException(400, "Already learning. Wait for it to finish.")
    days = max(0, min(int(payload.get("days") or 0), 365))
    limit = max(20, min(int(payload.get("limit") or 500), 500))
    asyncio.create_task(_learn_job(days, limit))
    return {"ok": True}


# ---------------------------------------------------------------- reports desk

def _user_desks(user: dict) -> list:
    if not user:
        return list(auth.DESKS)
    return list(auth.access_of(user))


async def _make_report(kind: str, start: str = None, end: str = None, scope: str = "all",
                       currency: str = None, user: dict = None) -> dict:
    """Build a report with the app's own figures and save it. Returns its id, title, rows."""
    if kind not in reports.TYPES:
        raise HTTPException(400, f"Unknown report. Choose one of: {', '.join(reports.TYPES)}.")
    title, desk = reports.TYPES[kind]
    if desk not in _user_desks(user):
        raise HTTPException(403, f"You don't have access to the {desk} desk for this report.")
    today = datetime.now(CASH_TZ).date()
    end = end or today.isoformat()
    start = start or (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    if start > end:
        start, end = end, start
    stores = _store_names()
    cur = (currency or _display_currency()).upper()
    if kind in ("profit_daily", "profit_stores"):
        dash = await api_dashboard(scope=scope or "all", start=start, end=end, currency=cur)
        cols, rows = (reports.profit_daily if kind == "profit_daily" else reports.profit_stores)(dash)
        if kind == "profit_stores" and not dash.get("stores"):
            cols, rows = reports.profit_daily(dash)
        title += f" · {dash['title']} · {cur}"
    elif kind == "cog_orders":
        ids = list(stores) if scope in (None, "", "all") else [int(scope)] if str(scope).isdigit() else list(stores)
        cols, rows = reports.cog_orders(db.cog_lines(ids, start, end), stores)
    elif kind == "invoices":
        lines = [l for l in db.invoice_lines() if start <= (l["date"] or "") <= end]
        cols, rows = reports.invoices(lines, stores)
    elif kind == "cash_statement":
        st = await api_cash_statement(start=start, end=end, currency=cur)
        cols, rows = reports.cash_statement(st)
        title += f" · {cur}"
    elif kind == "tickets":
        with db._conn() as con:
            rows_in = [dict(r) for r in con.execute(
                "SELECT t.*, (SELECT COUNT(*) FROM mail_messages m WHERE m.ticket_id = t.id) emails FROM tickets t"
                " WHERE substr(t.created_at, 1, 10) BETWEEN ? AND ? ORDER BY t.created_at", (start, end))]
        cols, rows = reports.tickets(rows_in, stores)
    elif kind == "cash_snapshots":
        before = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        snaps = db.cash_snapshots(before, end)       # the day before, for the first day's change
        cols, rows = reports.cash_snapshots(snaps)
        if snaps and snaps[0]["day"] < start:
            rows = rows[1:]
    else:  # ad_bills (now)
        st = await _cash_state(cur, with_ads=True)
        cols, rows = reports.ad_bills((st["summary"] or {}).get("ads") or {})
        start = end = today.isoformat()
        title += f" · {cur}"
    title += f" · {start}" + (f" to {end}" if end != start else "")
    with db._conn() as con:
        rid = con.execute("INSERT INTO reports (type, title, params, rows, content, created_by) VALUES (?,?,?,?,?,?)",
                          (kind, title, json.dumps({"start": start, "end": end, "scope": scope, "currency": cur}),
                           len(rows), reports.to_csv(cols, rows), (user or {}).get("email", ""))).lastrowid
    return {"id": rid, "title": title, "rows": len(rows), "download": f"/api/reports/{rid}/download"}


@app.get("/reports", response_class=HTMLResponse)
def reports_page():
    return _page("reports.html", ("reports.js", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/reports")
def api_reports(request: Request):
    desks = _user_desks(getattr(request.state, "user", None))
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, type, title, params, rows, created_by, created_at FROM reports ORDER BY id DESC LIMIT 200")]
    allowed = [r for r in rows if reports.TYPES.get(r["type"], ("", ""))[1] in desks]
    return {"reports": allowed,
            "types": [{"type": k, "title": v[0]} for k, v in reports.TYPES.items() if v[1] in desks]}


@app.post("/api/reports")
async def api_create_report(payload: dict, request: Request):
    return await _make_report(payload.get("type"), payload.get("start"), payload.get("end"),
                              payload.get("scope") or "all", payload.get("currency"),
                              getattr(request.state, "user", None))


@app.get("/api/reports/{report_id}/download")
def api_download_report(report_id: int, request: Request):
    from fastapi.responses import Response
    with db._conn() as con:
        r = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not r:
        raise HTTPException(404, "That report no longer exists.")
    if reports.TYPES.get(r["type"], ("", ""))[1] not in _user_desks(getattr(request.state, "user", None)):
        raise HTTPException(403, "You don't have access to this report.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", r["title"]).strip("-")[:90] + ".csv"
    return Response(r["content"], media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.delete("/api/reports/{report_id}")
def api_delete_report(report_id: int):
    with db._conn() as con:
        con.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    return {"ok": True}


# ---------------------------------------------------------------- chat button

def _need(user, desk):
    if desk not in _user_desks(user):
        raise HTTPException(403, f"No access to the {desk} desk.")


def _round(o):
    if isinstance(o, float):
        return round(o, 2)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


_chat_actions: dict = {}      # pending action id -> {user, kind, lines/args, title, detail, at}


def _price_groups(direction: str = "all", store: str = "all", contains: str = "") -> list:
    out = []
    for g in api_invoice_changes()["changes"]:
        if direction in ("up", "down") and g["direction"] != direction:
            continue
        if store not in ("", "all", None) and str(g["store_id"]) != str(store):
            continue
        if contains and contains.lower() not in (g["product"] or "").lower():
            continue
        out.append(g)
    return out


def _propose(user: dict, kind: str, title: str, detail: str, payload: dict) -> dict:
    """Actions are never run by the AI: it proposes, the person clicks Confirm."""
    aid = secrets.token_urlsafe(9)
    _chat_actions[aid] = {"user": (user or {}).get("email"), "kind": kind, "payload": payload, "title": title,
                          "detail": detail, "at": time.time()}
    for k in [k for k, v in _chat_actions.items() if time.time() - v["at"] > 3600]:
        _chat_actions.pop(k, None)
    return {"proposed_action": {"id": aid, "title": title, "detail": detail},
            "note": "Not done yet. The person must click Confirm in the chat."}


@app.post("/api/chat/actions/{action_id}")
async def api_chat_action(action_id: str, payload: dict, request: Request):
    user = getattr(request.state, "user", None) or {}
    a = _chat_actions.get(action_id)
    if not a or a["user"] != user.get("email"):
        raise HTTPException(404, "That action has expired. Ask again.")
    if payload.get("confirm") is not True:
        _chat_actions.pop(action_id, None)
        return {"ok": True, "text": "Cancelled. Nothing was changed."}
    if a["kind"] == "ads_plan":
        if not auth.at_least(user, "googleads", "manage"):
            raise HTTPException(403, "You need Manage access to the Google Ads desk to confirm this.")
        _chat_actions.pop(action_id, None)
        st = _ads_store(a["payload"]["store"])
        done = await _ads_run_batch(st, a["payload"]["batch"], user.get("email", ""))
        return {"ok": True, "text": "Done in Google Ads: " + "; ".join(done)}
    if not auth.at_least(user, "cog", "work"):
        raise HTTPException(403, "You need Work access to the COG desk to confirm this.")
    _chat_actions.pop(action_id, None)
    if a["kind"] == "price_decision":
        r = await _decide_prices(a["payload"]["action"], a["payload"]["lines"], user.get("email", ""))
        return {"ok": True, "text": f"Done: {r['lines']} invoice lines updated, {r['recosted']} orders re-costed."}
    raise HTTPException(400, "Unknown action.")


async def _chat_tool(name: str, args: dict, user: dict) -> dict:
    cur = _display_currency()
    got = await _assist_tool(name, args, user)
    if got is not None:
        return got
    if name == "get_price_changes":
        _need(user, "cog")
        groups = _price_groups(args.get("direction") or "all", args.get("store") or "all", args.get("product_contains") or "")
        return {"count": len(groups), "lines": sum(len(g["lines"]) for g in groups),
                "changes": [{"store": g["store"], "product": g["product"], "old_usd": g["old"], "new_usd": g["new"],
                             "direction": g["direction"], "state": g["state"] or "to decide", "orders": len(g["orders"])}
                            for g in groups[:60]]}
    if name == "propose_price_decision":
        if not auth.at_least(user or {}, "cog", "work"):
            return {"error": "Changing prices needs Work access to the COG desk."}
        groups = _price_groups(args.get("direction") or "all", args.get("store") or "all", args.get("product_contains") or "")
        groups = [g for g in groups if not (args["action"] == "checking" and g["state"] == "checking")]
        if not groups:
            return {"error": "No price changes match that."}
        lines = [ln for g in groups for ln in g["lines"]]
        label = {"use_new": "Use the new price", "checking": "Mark as checking with supplier",
                 "keep_old": "Keep the old price (supplier corrected)"}[args["action"]]
        up = sum(1 for g in groups if g["direction"] == "up")
        detail = (f"{len(groups)} products ({len(groups) - up} cheaper, {up} dearer), {len(lines)} invoice lines. "
                  + "; ".join(f"{g['product'][:40]} {g['old']}→{g['new']}" for g in groups[:5])
                  + (" …" if len(groups) > 5 else ""))
        return _propose(user, "price_decision", f"{label}: {len(groups)} products", detail,
                        {"action": args["action"], "lines": lines})
    if name == "list_stores":
        return {"stores": [{"id": x["id"], "name": x["name"].strip(), "currency": x["currency"]}
                           for x in db.list_stores() if not demo.is_demo(x)],
                "groups": [{"id": f"g{g['id']}", "name": g["name"]} for g in db.list_groups()]}
    if name == "get_profit":
        _need(user, "profit")
        d = await api_dashboard(scope=args.get("scope") or "all", start=args["start"], end=args["end"], currency=cur)
        keep = ("sales", "orders", "aov", "ad_spend", "google_spend", "meta_spend", "roas", "processing_fee",
                "paypal_fee", "airwallex_fee", "refunds", "refund_count", "chargebacks", "refunds_total", "cogs", "cogs_estimated", "cogs_share", "net_profit", "net_margin", "reserve_held", "net_available")
        return _round({"title": d["title"], "currency": d["currency"], "range": d["range"],
                       "totals": {k: d["totals"].get(k) for k in keep},
                       "change_vs_previous_pct": {k: d["delta"].get(k) for k in ("sales", "ad_spend", "net_profit", "roas")},
                       "platform_roas": d.get("platform_roas"),
                       "stores": [{"name": x["name"].strip(), **{k: x["totals"].get(k) for k in
                                   ("sales", "orders", "ad_spend", "roas", "cogs_share", "net_profit", "net_margin")}}
                                  for x in d.get("stores") or []],
                       "warnings": d.get("warnings", [])[:5]})
    if name == "get_cash":
        _need(user, "cash")
        st = await _cash_state(cur, with_ads=True)
        sm = st["summary"] or {}
        return _round({"currency": st["base"], "available_now": sm.get("available"), "held": sm.get("reserved"),
                       "receivable": sm.get("receivable"), "available_plus_receivable": sm.get("position"),
                       "ads_payable": sm.get("ads_payable"), "after_ad_bills": sm.get("after_ads"),
                       "note": "available_by in horizons does NOT deduct ads_payable; subtract it for after-ad-bills",
                       "horizons": [{"label": h["label"], "until": h["until"], "available_by": h["available_by"],
                                     "arriving": h["incoming"]} for h in sm.get("horizons", [])],
                       "accounts": [{"name": a["label"], "available": a["available"], "held": a["reserved"]}
                                    for a in sm.get("accounts", [])]})
    if name == "get_cash_history":
        _need(user, "cash")
        snaps = db.cash_snapshots(args["start"], args["end"])
        return _round({"snapshots": snaps[-31:],
                       "note": "Saved at 23:59 Hong Kong time each day. available_by does NOT deduct ads payable."
                               if snaps else "No end-of-day snapshots saved for those days."})
    if name == "get_cash_statement":
        _need(user, "cash")
        st = await api_cash_statement(start=args["start"], end=args["end"], currency=cur)
        return _round({k: st.get(k) for k in ("start", "end", "currency", "opening", "closing", "change",
                                              "sections", "checks")})
    if name == "get_expenses":
        _need(user, "expenses")
        e = await api_expenses(period="custom", start=args["start"], end=args["end"], currency=cur)
        return _round({"currency": e["currency"], "start": e["start"], "end": e["end"], "total": e["total"],
                       "unsorted": e["unsorted"],
                       "categories": [{"name": c["name"], "total": c["total"], "payments": c["count"],
                                       "top": [{"name": l["name"], "total": l["total"]} for l in c["lines"][:5]]}
                                      for c in e["categories"] if c["count"] and c["key"] != "ignore"],
                       "to_sort": [{"name": x["name"], "total": x["total"]} for x in e["to_sort"][:10]],
                       "own_account_unknown": [{"name": x["name"], "total": x["total"]} for x in e["to_confirm"][:10]]})
    if name == "get_cog":
        _need(user, "cog")
        stores = {x["id"]: x for x in db.list_stores() if not demo.is_demo(x)}
        ids = list(stores) if (args.get("store") or "all") == "all" else [int(args["store"])]
        table = await fx.table(cur)
        f = lambda c: fx.factor(table, c, cur) if table else 1.0
        sales = cog_total = est = 0.0
        src, prods = {}, {}
        for l in db.cog_lines(ids, args["start"], args["end"]):
            if l["cancelled"]:
                continue
            sv = l["subtotal"] * f(stores[l["store_id"]]["currency"])
            cv = (l["cost_usd"] or 0) * f("USD")
            sales += sv
            cog_total += cv
            est += cv if l["source"] == "estimate" else 0
            src[l["source"]] = src.get(l["source"], 0) + 1
            p = prods.setdefault(l["product"].split(" - ")[0], [0, 0.0, 0.0])
            p[0] += l["quantity"]
            p[1] += sv
            p[2] += cv
        top = sorted(((k, v) for k, v in prods.items() if v[0] >= 2 and v[1] > 0),
                     key=lambda kv: -kv[1][2] / kv[1][1])[:8]
        return _round({"currency": cur, "sales": sales, "cog": cog_total,
                       "cog_pct": (cog_total / sales * 100) if sales else None, "estimated_cog": est,
                       "lines_by_source": src,
                       "highest_cog_share_products": [{"product": k, "units": v[0], "sales": v[1], "cog": v[2],
                                                       "cog_pct": v[2] / v[1] * 100} for k, v in top]})
    if name == "get_inbox":
        _need(user, "inbox")
        counts = api_inbox_counts()
        with db._conn() as con:
            waiting = [dict(r) for r in con.execute(
                "SELECT id, kind, labels, customer_email, summary, last_message_at, escalated FROM tickets"
                " WHERE status = 'open' AND waiting = 'us' ORDER BY last_message_at LIMIT 10")]
        return {"counts": {k: v for k, v in counts.items() if k != "ai_error"}, "oldest_waiting_on_us": waiting}
    if name == "create_report":
        return await _make_report(args["type"], args.get("start"), args.get("end"),
                                  args.get("scope") or "all", cur, user)
    raise HTTPException(400, "Unknown tool.")


@app.post("/api/chat")
async def api_chat(payload: dict, request: Request):
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "The assistant needs the Anthropic API key (Settings → AI).")
    user = getattr(request.state, "user", None)
    history = payload.get("messages") or []
    if not history or history[-1].get("role") != "user":
        raise HTTPException(400, "Ask something first.")
    today = max((_store_today(x) for x in db.list_stores() if not demo.is_demo(x)),
                default=datetime.now(CASH_TZ).date()).isoformat()
    try:
        return await ai_chat.reply(key, history, lambda n, a: _chat_tool(n, a, user), today, _display_currency(), notes=_ai_notes())
    except ai_chat.ChatError as e:
        raise HTTPException(400, str(e))


@app.get("/api/ai")
def api_ai_status():
    return {"has_key": bool(db.get_setting("anthropic_api_key")), "model": ai_brain.MODEL,
            "error": _ai_state.get("error"), "last": _ai_state.get("last"),
            "tokens": _ai_state.get("total"),
            "tickets_from": db.get_setting("tickets_from") or "2026-10-07T16:00:00+00:00"}


@app.put("/api/ai")
async def api_ai_save(payload: dict):
    key = (payload.get("api_key") or "").strip()
    if not key.startswith("sk-ant-"):
        raise HTTPException(400, "That doesn't look like an Anthropic API key (it starts with sk-ant-).")
    db.set_setting("anthropic_api_key", key)
    _ai_state.pop("error", None)
    asyncio.create_task(_sort_mail())
    return {"ok": True}


@app.get("/api/history")
async def api_history(fresh: int = 0):
    """How far back each store's sales go: Shopify's window, whether the store has
    approved full order history, and the days EcomVital has saved."""
    saved = db.shopify_history()
    stores = [x for x in db.list_stores() if not demo.is_demo(x)]
    if fresh:
        for x in stores:
            _scopes.pop(x["id"], None)
    granted = await asyncio.gather(*[_granted(x) for x in stores])
    return [{"store": x["name"].strip(), "full_history": "read_all_orders" in g,
             "shopify_from": None if "read_all_orders" in g else _shopify_cutoff(x),
             "saved_from": (saved.get(x["id"]) or (None,))[0],
             "saved_days": (saved.get(x["id"]) or (None, None, 0))[2]}
            for x, g in zip(stores, granted)]


@app.get("/api/setup")
def api_setup():
    auth = db.get_google_auth()
    meta_conns = db.list_meta_connections()
    stores = [{
        "id": s["id"],
        "name": s["name"],
        "shop_domain": s["shop_domain"],
        "currency": s["currency"],
        "timezone": s["timezone"],
        "shopify_plan": s["shopify_plan"],
        "cost_pct": s["cost_pct"],
        "google_tax_pct": s["google_tax_pct"] or 0,
        "google_customer_id": s["google_customer_id"],
        "google_account_name": s["google_account_name"],
        "meta_account_id": s["meta_account_id"],
        "meta_account_name": s["meta_account_name"],
    } for s in db.list_stores()]
    return {
        "stores": stores,
        "groups": db.list_groups(),
        "fee_rates": _fee_rates(),
        "shopify_fee_pct": metrics.SHOPIFY_THIRD_PARTY_PCT,
        "shopify": {
            "app_configured": shop.app_configured(),
            "redirect_uri": SHOPIFY_REDIRECT,
        },
        "google": {
            "configured": gads.is_configured(),
            "connected": bool(auth),
            "email": auth["email"] if auth else None,
            "sheet_id": gsheet.sheet_id(),

            "redirect_uri": GOOGLE_REDIRECT,
        },
        "display_currency": _display_currency(),
        "currency_options": sorted(set(fx.COMMON) | {s["currency"] for s in db.list_stores()}),
        "meta": {
            "connected": bool(meta_conns),
            "connections": [{"id": c["id"], "label": c["label"] or c["name"]}
                            for c in meta_conns],
        },
    }


# ---------------------------------------------------------------- shopify install

@app.get("/auth/shopify/start")
def shopify_start(shop_domain: str = ""):
    domain = shop.normalize_domain(shop_domain)
    state = secrets.token_urlsafe(24)
    _shopify_states[state] = domain
    return RedirectResponse(shop.install_url(domain, SHOPIFY_REDIRECT, state,
                                             _shopify_app(domain)))


@app.post("/api/shopify/apps")
def api_save_shopify_app(payload: dict):
    """Save a store's own app keys and say which page to open next.

    With an install link (custom distribution apps), that page is Shopify's
    install screen; EcomVital picks the connection up when Shopify comes back.
    Without one, it goes straight to the normal permission screen.
    """
    domain = shop.normalize_domain(payload.get("shop_domain", ""))
    client_id = (payload.get("client_id") or "").strip()
    secret = (payload.get("client_secret") or "").strip()
    saved = db.get_shopify_app(domain)
    if not client_id and not secret and saved:
        # Retrying a store whose keys are already saved: no need to type them again.
        client_id, secret = saved["client_id"], saved["client_secret"]
    if not client_id or not secret:
        raise HTTPException(400, "Both the Client ID and the Client secret are needed.")
    if not re.fullmatch(r"[0-9a-f]{32}", client_id):
        raise HTTPException(400, "That Client ID doesn't look right. It's 32 letters and "
                                 "numbers, from your app's Settings page in the Dev Dashboard.")
    if not secret.startswith("shpss_"):
        raise HTTPException(400, "That Client secret doesn't look right. It starts with "
                                 "shpss_ and is on the same Settings page as the Client ID.")
    link = (payload.get("install_link") or "").strip()
    if link:
        link = shop.check_install_link(link, domain, client_id)
    db.save_shopify_app(domain, client_id, secret)
    return {"open": link or f"/auth/shopify/start?shop_domain={domain}"}


@app.get("/auth/shopify/callback", response_class=HTMLResponse)
async def shopify_callback(request: Request):
    params = dict(request.query_params)
    state = params.get("state", "")
    expected_domain = _shopify_states.pop(state, None)

    if not expected_domain:
        return _closer("That install link had expired. Start the connection again.")
    if params.get("shop") != expected_domain:
        return _closer("The store that replied is not the one we asked. Nothing was connected.")
    app_keys = _shopify_app(expected_domain)
    if not shop.verify_hmac(params, app_keys):
        return _closer("Shopify's reply failed its signature check. Nothing was connected.")

    token = await shop.exchange_code(expected_domain, params.get("code", ""), app_keys)
    info = await ShopifyClient(expected_domain, token).shop_info()
    db.upsert_store(
        info.get("name") or expected_domain,
        info.get("myshopifyDomain") or expected_domain,
        token,
        info.get("currencyCode", "USD"),
        info.get("ianaTimezone", "UTC"),
    )
    _cache.clear()
    return _closer(f"{info.get('name')} is connected. You can close this tab.", ok=True)


@app.post("/api/stores/token")
async def api_connect_with_token(payload: dict):
    """Fallback for stores where a custom app token is easier than an install link."""
    domain = shop.normalize_domain(payload.get("shop_domain", ""))
    token = (payload.get("access_token") or "").strip()
    if not token:
        raise HTTPException(400, "Paste the Admin API access token from your Shopify app.")
    info = await ShopifyClient(domain, token).shop_info()
    sid = db.upsert_store(
        info.get("name") or domain,
        info.get("myshopifyDomain") or domain,
        token,
        info.get("currencyCode", "USD"),
        info.get("ianaTimezone", "UTC"),
    )
    _cache.clear()
    return {"id": sid, "name": info.get("name")}


@app.delete("/api/stores/{store_id}")
def api_delete_store(store_id: int):
    db.delete_store(store_id)
    _cache.clear()
    return {"ok": True}


@app.put("/api/stores/{store_id}")
def api_update_store(store_id: int, payload: dict):
    if "cost_pct" in payload:
        db.set_cost_pct(store_id, payload["cost_pct"])
    if "google_tax_pct" in payload:
        db.set_google_tax(store_id, payload["google_tax_pct"])
        _ads_cache.clear()
    if payload.get("name"):
        db.rename_store(store_id, payload["name"].strip())
    _cache.clear()
    return {"ok": True}


# ---------------------------------------------------------------- google

@app.post("/api/google/app")
def api_google_app(payload: dict):
    """Save the Google sign-in keys (OAuth client) from Settings."""
    cid = (payload.get("client_id") or "").strip()
    secret = (payload.get("client_secret") or "").strip()
    if not cid.endswith(".apps.googleusercontent.com"):
        raise HTTPException(400, "That Client ID doesn't look right. It ends with "
                                 ".apps.googleusercontent.com")
    if not secret:
        raise HTTPException(400, "Paste the Client secret too.")
    gads.save_app(cid, secret)
    return {"ok": True}


_gmail_states: dict = {}      # sign-in state -> mailbox id


@app.get("/auth/gmail/start", response_class=HTMLResponse)
def gmail_start(account: int):
    acct = next((a for a in db.list_mail_accounts() if a["id"] == account), None)
    if not acct:
        raise HTTPException(404, "Mailbox not found.")
    state = secrets.token_urlsafe(24)
    _gmail_states[state] = account
    try:
        return RedirectResponse(gmail_send.auth_url(GOOGLE_REDIRECT, state, acct["address"]))
    except gmail_send.SendError as e:
        return _closer(str(e))


@app.get("/api/mail-accounts/{account_id}/gmail-check")
async def api_mail_gmail_check(account_id: int):
    acct = next((a for a in db.list_mail_accounts() if a["id"] == account_id), None)
    if not acct or not acct.get("gmail_token"):
        raise HTTPException(400, "This mailbox isn't signed in to send yet.")
    try:
        await gmail_send.check(acct["gmail_token"])
    except gmail_send.SendError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/mail-accounts/{account_id}/test-send")
async def api_mail_test_send(account_id: int, payload: dict):
    """Send one short test email from a mailbox (owner, Settings), to prove sending really works."""
    acct = next((a for a in db.list_mail_accounts() if a["id"] == account_id), None)
    to = (payload.get("to") or "").strip()
    if not acct or not acct.get("gmail_token"):
        raise HTTPException(400, "This mailbox isn't signed in to send yet.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
        raise HTTPException(400, "Type the address to send the test to.")
    store = next((x for x in db.list_stores() if x["id"] == acct["store_id"]), None)
    try:
        await gmail_send.send(acct["gmail_token"], acct["address"], (store or {}).get("name", "").strip(), to,
                              "EcomVital test email",
                              f"This is a test from EcomVital, sent from {acct['address']}.\n\n"
                              "If you can read this, replies from the Inbox will reach customers from this address.")
    except gmail_send.SendError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/mail-accounts/{account_id}/gmail-signout")
def api_mail_gmail_signout(account_id: int):
    with db._conn() as con:
        con.execute("UPDATE mail_accounts SET gmail_token = NULL, gmail_email = NULL, gmail_at = NULL WHERE id = ?",
                    (account_id,))
    return {"ok": True}


@app.get("/auth/google/start")
def google_start():
    state = secrets.token_urlsafe(24)
    _google_states.add(state)
    return RedirectResponse(gads.build_auth_url(GOOGLE_REDIRECT, state))


@app.get("/auth/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request):
    params = request.query_params
    if params.get("error"):
        return _closer(f"Google sign-in was cancelled ({params['error']}).")
    state = params.get("state", "")
    if state in _gmail_states:                       # a support mailbox signing in to send
        aid = _gmail_states.pop(state)
        acct = next((a for a in db.list_mail_accounts() if a["id"] == aid), None)
        try:
            token, email = await gads.exchange_code(params.get("code", ""), GOOGLE_REDIRECT)
        except gads.GoogleAdsError as e:
            return _closer(str(e))
        if not acct:
            return _closer("That mailbox was removed from EcomVital.")
        if (email or "").lower() != acct["address"].lower():
            return _closer(f"You signed in as {email}, but this mailbox is {acct['address']}. "
                           f"Sign in again and choose {acct['address']}.")
        with db._conn() as con:
            con.execute("UPDATE mail_accounts SET gmail_token = ?, gmail_email = ?, gmail_at = datetime('now') WHERE id = ?",
                        (token, email, aid))
        return _closer(f"{email} can now send replies from EcomVital. You can close this tab.", ok=True)
    if state not in _google_states:
        return _closer("That sign-in link had expired. Start the connection again.")
    _google_states.discard(state)

    refresh_token, email = await gads.exchange_code(params.get("code", ""), GOOGLE_REDIRECT)
    db.save_google_auth(refresh_token, email)
    _cache.clear()
    return _closer(f"Connected as {email or 'your Google account'}. You can close this tab.", ok=True)


@app.post("/api/google/sheet")
def api_google_sheet(payload: dict):
    """Set the Google Sheet the Google Ads script writes spend into."""
    sid = gsheet.save_sheet(payload.get("url") or "")
    _cache.clear()
    return {"ok": True, "sheet_id": sid}


@app.get("/api/google/compare")
async def api_google_compare(days: int = 7):
    """Owner check before switching stores from the Sheet to the Google Ads API: each linked store's
    daily spend both ways (store clock days, before any Google tax)."""
    auth = db.get_google_auth()
    if not auth:
        raise HTTPException(400, "Google isn't connected.")
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=max(1, min(days, 30)) - 1)
    out = []

    async def one(st):
        row = {"store": st["name"].strip(), "account": st["google_account_name"] or st["google_customer_id"],
               "source": st["google_source"] or "api"}
        try:
            row["sheet"] = await gsheet.daily_spend(auth["refresh_token"], st["google_customer_id"], start.isoformat(),
                                                    end.isoformat(), st["timezone"])
        except Exception as e:
            row["sheet_error"] = str(e)[:160]
        try:
            row["api"] = await gads.daily_spend(auth["refresh_token"], st["google_customer_id"], st["google_login_cid"],
                                                start.isoformat(), end.isoformat(), st["timezone"])
        except Exception as e:
            row["api_error"] = str(e)[:600]
        out.append(row)
    await asyncio.gather(*(one(x) for x in db.list_stores() if x.get("google_customer_id") and not demo.is_demo(x)))
    return {"start": start.isoformat(), "end": end.isoformat(), "stores": sorted(out, key=lambda r: r["store"])}


@app.post("/api/google/source")
def api_google_source(payload: dict):
    """Owner: read a store's Google spend straight from Google ("api") or from the script's Sheet."""
    sid, src = int(payload.get("store_id") or 0), payload.get("source")
    if src not in ("api", "sheet"):
        raise HTTPException(400, "Pick api or sheet.")
    with db._conn() as con:
        con.execute("UPDATE stores SET google_source = ? WHERE id = ?", ("sheet" if src == "sheet" else None, sid))
    _cache.clear()
    return {"ok": True}


@app.post("/api/google/disconnect")
def api_google_disconnect():
    db.clear_google_auth()
    _cache.clear()
    return {"ok": True}


@app.get("/api/google/accounts")
async def api_google_accounts():
    auth = db.get_google_auth()
    if not auth:
        raise HTTPException(400, "Sign in with Google first.")
    # With a spend Sheet set, the accounts are the Sheet's tabs; otherwise ask
    # the Google Ads API (needs the Cloud project approved for Explorer+).
    if gsheet.sheet_id():
        accounts = await gsheet.list_accounts(auth["refresh_token"])
    else:
        accounts = await gads.list_accounts(auth["refresh_token"])
    taken = db.linked_customer_ids()
    for a in accounts:
        a["linked_to"] = taken.get(a["customer_id"])
    return {"accounts": accounts}


@app.post("/api/stores/{store_id}/google")
def api_link_google(store_id: int, payload: dict):
    cid = (payload.get("customer_id") or "").strip()
    if not cid:
        raise HTTPException(400, "Pick an ad account.")
    db.link_google(store_id, cid, payload.get("login_customer_id"),
                   payload.get("name") or f"Account {cid}", payload.get("currency"),
                   payload.get("timezone"), payload.get("source"))
    _cache.clear()
    return {"ok": True}


@app.delete("/api/stores/{store_id}/google")
def api_unlink_google(store_id: int):
    db.unlink_google(store_id)
    _cache.clear()
    return {"ok": True}


# ---------------------------------------------------------------- cash flow

CASH_TZ = ZoneInfo(os.getenv("CASH_TIMEZONE", "Asia/Hong_Kong"))  # the business's own clock


@app.get("/cash", response_class=HTMLResponse)
def cash_page():
    return _page("cash.html", ("cash.js", "nav.js", "styles.css", "skin.css", "chat.js"))


_ads_cache: dict = {}


async def _ad_bills_raw() -> list:
    """Every linked ad account and what it owes, in its own currency (see adbills.py).
    Kept for 5 minutes: card charges and Meta balances don't move faster than that."""
    hit = _ads_cache.get("rows")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    now = datetime.now(timezone.utc)
    stores = db.list_stores()
    rows = []

    meta_stores = [x for x in stores if x["meta_account_id"]]
    if meta_stores:
        got = await asyncio.gather(*[meta.billing(c["access_token"])
                                     for c in db.list_meta_connections()], return_exceptions=True)
        accounts = {}
        for g in got:
            if not isinstance(g, Exception):
                accounts.update(g)
        for x in meta_stores:
            a = accounts.get(str(x["meta_account_id"]))
            if a:
                rows.append(adbills.meta_row(x, a))
            else:
                rows.append({"platform": "Meta", "store": x["name"], "account_id": x["meta_account_id"],
                             "account": x["meta_account_name"] or "", "currency": x["meta_currency"] or "",
                             "owed": None, "failed": [], "last_charge": None,
                             "note": "Meta didn't return this ad account. Check it's still in the connected business."})

    google_stores = [x for x in stores if x["google_customer_id"]]
    auth = db.get_google_auth()
    if google_stores:
        tabs, cards = {}, []
        if auth and gsheet.sheet_id():
            try:
                tabs = {_digits_only(k): v for k, v in (await gsheet._read_all(auth["refresh_token"])).items()}
            except Exception:
                tabs = {}
        got = await asyncio.gather(*[awx.card_transactions(c["client_id"], c["secret"],
                                                           now - timedelta(days=60), c["account_id"])
                                     for c in db.list_cash_connections() if c["provider"] == "airwallex"],
                                   return_exceptions=True)
        for g in got:
            if not isinstance(g, Exception):
                cards.extend(g)
        charges = adbills.google_charges(cards)
        since = (now - timedelta(days=62)).date().isoformat()

        async def direct(x):
            # stores read directly from Google: hourly spend from the API (the Sheet stays a fallback)
            if x.get("google_source") == "sheet" or not auth:
                return None
            try:
                return await gads.hourly_tab(auth["refresh_token"], x["google_customer_id"], x["google_login_cid"], since)
            except Exception:
                return None
        direct_tabs = await asyncio.gather(*(direct(x) for x in google_stores))
        for x, api_tab in zip(google_stores, direct_tabs):
            cid = _digits_only(x["google_customer_id"])
            rows.append(adbills.google_row(x, api_tab or tabs.get(cid), charges.get(cid, []), now))

    _ads_cache["rows"] = (time.time(), rows)
    return rows


def _digits_only(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


async def _cash_state(currency: str = None, with_ads: bool = False) -> dict:
    """Live balances and pending money from every connected account, summarised."""
    conns = db.list_cash_connections()
    base = (currency or _display_currency()).upper()
    if not conns:
        return {"base": base, "conns": [], "summary": None, "problems": [], "fx": None,
                "factor": lambda code: 1.0, "today": datetime.now(CASH_TZ).date()}

    async def one(c):
        if c["provider"] == "airwallex":
            return await awx.snapshot(c["client_id"], c["secret"], account_id=c["account_id"])
        if c["provider"] == "paypal":
            return await paypal.snapshot(c["client_id"], c["secret"])
        raise RuntimeError(f"Unknown provider {c['provider']}")

    ads_task = asyncio.ensure_future(_ad_bills_raw()) if with_ads else None
    got = await asyncio.gather(*[one(c) for c in conns], return_exceptions=True)
    accounts, problems = [], []
    for c, g in zip(conns, got):
        if isinstance(g, Exception):
            problems.append(f"{c['label']}: {g}")
            continue
        accounts.append({"id": c["id"], "label": c["label"], "provider": c["provider"], **g})

    ad_rows = None
    if ads_task:
        try:
            ad_rows = [dict(r, failed=[dict(f) for f in r["failed"]]) for r in await ads_task]
        except Exception as e:
            problems.append(f"Ad bills: {e}")

    codes = {b["currency"] for a in accounts for b in a["balances"]} | \
            {p["currency"] for a in accounts for p in a["pending"]} | \
            {r["currency"] for r in (ad_rows or []) if r.get("currency")}
    fx_table = await fx.table(base) if codes - {base} else None
    factor = (lambda code: fx.factor(fx_table, code, base)) if fx_table else (lambda code: 1.0)
    today = datetime.now(CASH_TZ).date()
    transit, transit_ok = [], True
    try:
        transit = await _in_transit(conns, accounts)
    except Exception as e:
        transit_ok = False
        problems.append(f"Money on its way between your accounts couldn't be checked: {e}")
    summary = cashflow.summarize(accounts, factor, today, CASH_TZ, transit)
    try:                    # what was due today and has already landed (cashflow.track_arrivals)
        arr = json.loads(db.get_setting("cash_arrivals") or "{}")
        if transit_ok:      # otherwise paired deposits would come and go and look like arrivals
            arr = cashflow.track_arrivals(arr, accounts, today, CASH_TZ, datetime.now(CASH_TZ))
            db.set_setting("cash_arrivals", json.dumps(arr))
        summary["arrived_today"] = cashflow.arrived_today(arr, factor, today)
    except Exception as e:
        print(f"Cash arrivals failed: {e}")
    if ad_rows is not None:
        ads = adbills.summarize(ad_rows, factor)
        summary["ads"] = ads
        summary["ads_payable"] = ads["total"]
        summary["after_ads"] = summary["position"] - ads["total"]
    return {"base": base, "conns": conns, "problems": problems, "factor": factor, "today": today,
            "fx_table": fx_table, "summary": summary}


_transit_cache: dict = {}


async def _in_transit(conns: list, accounts: list) -> list:
    """PayPal withdrawals still on their way to our Airwallex (statement.in_transit). Their
    pending Airwallex deposits are taken out of the accounts' pending list, since the money
    is counted as available in transit instead."""
    moves = []
    for a in accounts:
        if a["provider"] == "paypal" and a.get("withdrawals"):
            moves += statement.paypal_moves(a["label"], [{"transaction_info": t} for t in a["withdrawals"]])
    sent = [m for m in moves if m.get("withdrawal")]
    if not sent:
        return []
    now = datetime.now(timezone.utc)
    since = min(m["time"] for m in sent) - timedelta(days=1)
    ck = since.date().isoformat()
    hit = _transit_cache.get(ck)
    if hit and time.time() - hit[0] < 180:
        deposits = hit[1]
    else:
        awx_conns = [c for c in conns if c["provider"] == "airwallex"]
        got = await asyncio.gather(*[awx.transactions(c["client_id"], c["secret"], since, now, c["account_id"])
                                     for c in awx_conns])     # a failure: nothing counted in transit
        deposits = [m for c, rows in zip(awx_conns, got)
                    for m in statement.airwallex_moves(c["label"], rows, []) if m.get("deposit")]
        _transit_cache.clear()
        _transit_cache[ck] = (time.time(), deposits)
    moves += [dict(d) for d in deposits]
    statement._pair_transfers(moves, now)
    transit = statement.in_transit(moves)
    landing = {t["deposit_id"] for t in transit if t.get("deposit_id")}
    if landing:
        for a in accounts:
            a["pending"] = [p for p in a["pending"] if p.get("id") not in landing]
    return transit


@app.get("/api/cash")
async def api_cash(currency: str = None):
    st = await _cash_state(currency, with_ads=True)
    if not st["conns"]:
        return {"currency": st["base"], "connected": [], "summary": None, "problems": []}
    fx_table = st["fx_table"]
    sup = None
    try:                                  # what suppliers are owed (COG → Invoices): on credit + estimated
        pay = await asyncio.to_thread(_supplier_payable)
        k = fx.factor(fx_table, "USD", st["base"]) if fx_table else 1.0
        sup = {"credit": pay["credit"] * k, "estimated": pay["estimated"] * k, "total": pay["total"] * k, "cutoff": pay["cutoff"]}
        sm = st["summary"] or {}
        sup["after"] = (sm["after_ads"] if sm.get("after_ads") is not None else sm.get("position") or 0.0) - sup["total"]
    except Exception as e:
        print(f"Supplier payable failed: {e}")
    return {
        "currency": st["base"],
        "suppliers_payable": sup,
        "today": st["today"].isoformat(),
        "connected": [{"id": c["id"], "label": c["label"], "provider": c["provider"]}
                      for c in st["conns"]],
        "summary": st["summary"],
        "problems": st["problems"],
        "fx": None if not fx_table else {"date": fx_table.get("date"),
                                         "source": fx_table.get("source")},
    }


def _snapshot_figures(st: dict) -> dict:
    """The Cash flow page's top cards, as plain numbers."""
    sm = st["summary"] or {}
    ads = sm.get("ads") or {}
    return {
        "available": sm.get("available"), "held": sm.get("reserved"), "receivable": sm.get("receivable"),
        "position": sm.get("position"), "ads_payable": sm.get("ads_payable"),
        "ads_meta": ads.get("meta"), "ads_google": ads.get("google"), "after_ads": sm.get("after_ads"),
        "horizons": [{"key": h["key"], "label": h["label"], "until": h["until"],
                      "available_by": h["available_by"], "arriving": h["incoming"]}
                     for h in sm.get("horizons", [])],
        "accounts": [{"name": a["label"], "available": a["available"], "held": a["reserved"]}
                     for a in sm.get("accounts", [])],
        "problems": st["problems"],
    }


async def _take_snapshot() -> dict:
    """Save the cash position for today (business clock). Fresh ad bills, not the 5-minute copy."""
    taken = datetime.now(CASH_TZ)
    _ads_cache.pop("rows", None)
    st = await _cash_state(_display_currency(), with_ads=True)
    if not st["summary"]:
        raise RuntimeError("No cash accounts connected.")
    figures = _snapshot_figures(st)
    db.save_cash_snapshot(taken.date().isoformat(), taken.isoformat(timespec="seconds"), st["base"], figures)
    return figures


async def _arrivals_loop():
    """Every 30 minutes, look at the pending lists so "Arrived" shows even when nobody had the page open."""
    await asyncio.sleep(120)
    while True:
        try:
            if db.list_cash_connections():
                await _cash_state(_display_currency())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Cash arrivals check failed: {e}")
        await asyncio.sleep(1800)


async def _snapshot_loop():
    """Every day at 23:59 Hong Kong time, save the Cash flow cards for that day."""
    while True:
        now = datetime.now(CASH_TZ)
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            # Restarted inside the last minute and today's isn't saved yet: take it now.
            if not db.cash_snapshots(now.date().isoformat(), now.date().isoformat()):
                target = now
            else:
                target += timedelta(days=1)
        await asyncio.sleep(max(0.0, (target - datetime.now(CASH_TZ)).total_seconds()))
        for attempt in range(3):
            try:
                await _take_snapshot()
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Cash snapshot failed: {e}")
                await asyncio.sleep(10)
        await asyncio.sleep(90)      # past midnight, so the next target is tomorrow's


@app.get("/api/cash/snapshots")
def api_cash_snapshots(start: str = None, end: str = None):
    """Saved end-of-day cash cards, newest first, each with its change from the day before."""
    snaps = db.cash_snapshots(start or "0000", end or "9999")
    prev = None
    for x in snaps:
        x["change"] = (x["position"] - prev["position"]) if prev and prev["currency"] == x["currency"] \
            and None not in (x.get("position"), prev.get("position")) else None
        prev = x
    return {"snapshots": snaps[::-1]}


@app.get("/api/cash/statement")
async def api_cash_statement(start: str, end: str = None, currency: str = None):
    """How "available + receivable" moved between two dates (business clock)."""
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end or start, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Pick a start and end date.")
    now = datetime.now(CASH_TZ)
    if d1 < d0:
        d0, d1 = d1, d0
    d1 = min(d1, now.date())
    if (now.date() - d0).days > 92:
        raise HTTPException(400, "The statement goes back up to 92 days. Pick a later start date.")
    t0 = datetime.combine(d0, datetime.min.time(), CASH_TZ)
    t1 = datetime.combine(d1 + timedelta(days=1), datetime.min.time(), CASH_TZ)

    st = await _cash_state(currency)
    if not st["summary"]:
        raise HTTPException(400, "Connect Airwallex or PayPal first.")

    async def moves_for(c):
        if c["provider"] == "airwallex":
            rows, early, cards = await asyncio.gather(
                # From a week early, so card holds and transfers that began just
                # before the range can be paired with what happened inside it.
                awx.transactions(c["client_id"], c["secret"], t0 - timedelta(days=7), now,
                                 c["account_id"]),
                # Reserve releases landing in the range were created ~90 days before.
                awx.transactions(c["client_id"], c["secret"], t0 - timedelta(days=100),
                                 min(t1 - timedelta(days=80), t0), c["account_id"]),
                awx.card_transactions(c["client_id"], c["secret"], t0 - timedelta(days=14),
                                      c["account_id"]))
            early = [r for r in early if awx._f(r, "transaction_type") == "PAYMENT_RESERVE_RELEASE"]
            return statement.airwallex_moves(c["label"], rows + early, cards)
        if c["provider"] == "paypal":
            days = (now - t0).days + 8
            rows = await paypal.transactions(c["client_id"], c["secret"], days=days,
                                             fields="transaction_info,payer_info", full=True)
            return statement.paypal_moves(c["label"], rows)
        return []

    got = await asyncio.gather(*[moves_for(c) for c in st["conns"]], return_exceptions=True)
    moves, problems = [], list(st["problems"])
    for c, g in zip(st["conns"], got):
        if isinstance(g, Exception):
            problems.append(f"{c['label']}: {g}")
        else:
            moves.extend(g)

    # Shopify sales on the same (business) clock as the statement, split by how
    # they were paid, so each payment provider can be checked against its own.
    async def store_sales(store):
        if not store.get("access_token"):
            return store, {}
        client = ShopifyClient(store["shop_domain"], store["access_token"])
        return store, await client.daily_sales(d0.isoformat(), d1.isoformat(), str(CASH_TZ))

    shop_rows = await asyncio.gather(*[store_sales(x) for x in db.list_stores()],
                                     return_exceptions=True)
    by_gateway, shop_ok = {}, True
    for got in shop_rows:
        if isinstance(got, Exception):
            shop_ok = False
            problems.append(f"Shopify: {got}")
            continue
        store, days = got
        f = st["factor"](store["currency"])
        for d in days.values():
            # Net of refunds: what customers paid and kept paid.
            total = sum(v[1] for v in d["payments"].values()) or 0.0
            refunds = total - d["sales"]
            for name, (n, amount) in d["payments"].items():
                key = statement.gateway(name)
                g = by_gateway.setdefault(key, {"orders": 0, "amount": 0.0})
                g["orders"] += n
                g["amount"] += (amount - (refunds * amount / total if total else 0)) * f

    ad_spend = None
    try:
        dash = await api_dashboard(scope="all", start=d0.isoformat(), end=d1.isoformat(),
                                   currency=st["base"])
        ad_spend = dash["totals"].get("ad_spend")
    except Exception:
        pass

    out = statement.build(moves, st["factor"], st["summary"]["position"], t0, t1, now,
                          shopify=by_gateway if shop_ok else None, ad_spend=ad_spend, ours=_own_payees())
    return {"currency": st["base"], "problems": problems, **out}


# ---------------------------------------------------------------- expenses desk

# ---------------------------------------------------------------- disputes desk

@app.get("/disputes", response_class=HTMLResponse)
def disputes_page():
    return _page("disputes.html", ("disputes.js", "nav.js", "styles.css", "skin.css", "chat.js"))


_disp = {"at": 0.0, "problems": []}
_disp_lock = asyncio.Lock()


def _who(request: Request) -> str:
    u = getattr(request.state, "user", None) or {}
    return u.get("name") or u.get("email") or "?"


async def _disputes_refresh(force: bool = False) -> list:
    """Read every PayPal and Airwallex account's disputes (5-minute cache). Open PayPal cases are
    read in full (deadline, messages, what's allowed); closed ones only when opened."""
    async with _disp_lock:
        if not force and time.time() - _disp["at"] < 300:
            return _disp["problems"]
        problems = []
        since = datetime.now(timezone.utc) - timedelta(days=200)
        sem = asyncio.Semaphore(5)

        async def paypal_one(c, row):
            known = db.get_dispute(f"pp:{c['id']}:{row.get('dispute_id')}")
            old = json.loads(known["data"]) if known else {}
            if old.get("updated") == row.get("update_time") and old.get("full"):
                return
            case = disputes.from_paypal(row, c)
            if case["state"] != "closed":
                async with sem:
                    try:
                        case = disputes.from_paypal(await paypal.dispute(c["client_id"], c["secret"], row["dispute_id"]), c)
                    except paypal.PayPalError:
                        pass
            await asyncio.to_thread(db.save_dispute, case)

        async def one(c):
            try:
                if c["provider"] == "paypal":
                    rows = await paypal.disputes(c["client_id"], c["secret"], since)
                    await asyncio.gather(*(paypal_one(c, r) for r in rows))
                elif c["provider"] == "airwallex":
                    got = await awx.disputes(c["client_id"], c["secret"], c.get("account_id"))
                    await asyncio.to_thread(lambda: [db.save_dispute(disputes.from_airwallex(d, c)) for d in got])
            except (paypal.PayPalError, awx.AirwallexError) as e:
                problems.append(f"{c['label']}: {e}")
        await asyncio.gather(*(one(c) for c in db.list_cash_connections()))
        for row in db.list_disputes():
            if not row["order_name"]:
                try:
                    found = await _dispute_order(json.loads(row["data"]))
                except Exception:
                    found = None
                if found:
                    db.link_dispute(row["key"], *found)
        _disp.update(at=time.time(), problems=problems)
        return problems


async def _dispute_order(case: dict):
    """(store id, order name) of the Shopify order a case is about, or None: by payment id, then by
    order number, then by amount within 3 hours of the payment."""
    ids = [x for x in (case.get("txn_id"), case.get("invoice")) if x]
    with db._conn() as con:
        for i in ids:
            r = con.execute("SELECT store_id, order_name FROM order_fees WHERE payment_id = ? LIMIT 1", (i,)).fetchone()
            if r:
                return r[0], r[1]
        if case.get("invoice"):
            key = cog.order_key(case["invoice"])
            if cog.tag_of(key):
                r = con.execute("SELECT store_id, order_name FROM cog_lines WHERE order_name IN (?, ?) LIMIT 1",
                                (key, "#" + key)).fetchone()
                if r:
                    return r[0], r[1]
    when, amount = case.get("txn_time"), case.get("txn_amount") or case.get("amount")
    if case["provider"] == "airwallex" and case.get("txn_id") and not when:
        c = next((x for x in db.list_cash_connections() if x["id"] == case["conn_id"]), None)
        if c:
            pi = await awx.payment_intent(c["client_id"], c["secret"], case["txn_id"], c.get("account_id"))
            when = pi.get("created_at")
            if pi.get("merchant_order_id") and pi["merchant_order_id"] != case.get("invoice"):
                return await _dispute_order({**case, "invoice": pi["merchant_order_id"], "txn_time": when,
                                             "txn_id": ""})
    if not when or not amount:
        return None
    try:
        t = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return None
    gw = "%paypal%" if case["provider"] == "paypal" else "%airwallex%"
    with db._conn() as con:
        rows = con.execute("SELECT store_id, order_name, created FROM order_fees WHERE lower(gateway) LIKE ?"
                           " AND abs(amount - ?) < 0.011 AND created BETWEEN ? AND ?",
                           (gw, amount, (t - timedelta(hours=3)).isoformat()[:19],
                            (t + timedelta(hours=3)).isoformat()[:19])).fetchall()
    return (rows[0][0], rows[0][1]) if len(rows) == 1 else None


def _dispute_context(store_id, order_name) -> dict:
    """What EcomVital knows about the order: products, parcels + scans, emails with the customer."""
    if not order_name:
        return {}
    stores = {x["id"]: x for x in db.list_stores()}
    st = stores.get(store_id) or {}
    with db._conn() as con:
        lines = [dict(r) for r in con.execute(
            "SELECT product, quantity, subtotal, created FROM cog_lines WHERE store_id = ? AND order_name = ?"
            " ORDER BY line_no", (store_id, order_name))]
        paid = con.execute("SELECT SUM(amount) FROM order_fees WHERE store_id = ? AND order_name = ?",
                           (store_id, order_name)).fetchone()[0]
        ships = [dict(r) for r in con.execute(
            "SELECT number, company, tracking_url, fulfilled_at, status, sub_status, delivered_at, customer, email,"
            " city, country, destination, events, order_at FROM shipments WHERE store_id = ? AND order_name = ?",
            (store_id, order_name))]
    first = ships[0] if ships else {}
    order = {"name": order_name, "store": (st.get("name") or "").strip(), "currency": st.get("currency") or "",
             "created": (lines[0]["created"] if lines else first.get("order_at")) or "",
             "total": paid or sum(l["subtotal"] or 0 for l in lines),
             "customer": first.get("customer") or "", "email": first.get("email") or "",
             "city": first.get("city") or "", "country": first.get("country") or "",
             "items": [{"product": l["product"], "qty": l["quantity"]} for l in lines]}
    shipments = []
    for s in ships:
        if not s["number"]:
            continue
        ev = json.loads(s.pop("events") or "[]")
        shipments.append({**s, "events": [{"time": e.get("time"), "location": e.get("location"),
                                            "desc": e.get("text")} for e in ev]})
    emails = []
    if order["email"] or order_name:
        with db._conn() as con:
            tids = [r[0] for r in con.execute(
                "SELECT id FROM tickets WHERE (customer_email = ? AND ? != '') OR orders LIKE ?",
                (order["email"].lower(), order["email"], f"%{order_name.lstrip('#')}%"))]
            if tids:
                marks = ",".join("?" * len(tids))
                for m in con.execute(f"SELECT date, direction, text FROM mail_messages WHERE ticket_id IN ({marks})"
                                     " ORDER BY date", tids):
                    emails.append({"at": m["date"], "direction": m["direction"],
                                   "text": ai_brain.new_part(m["text"] or "")[:3000]})
    return {"order": order, "shipments": shipments, "emails": emails, "refunds": []}


def _case_out(row: dict, request: Request = None) -> dict:
    case = json.loads(row["data"])
    case.update(store_id=row["store_id"], order_name=row["order_name"], linked_by=row["link_by"],
                days_left=disputes.days_left(case.get("due")))
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    case["store"] = stores.get(row["store_id"], "")
    if request is not None:
        u = request.state.user
        case["can"] = {a: auth.at_least(u, "disputes", disputes.ACTIONS[a][1]) for a in case["actions"]}
    case["action_names"] = {a: disputes.ACTIONS[a][0] for a in case["actions"]}
    return case


@app.get("/api/disputes")
async def api_disputes(refresh: int = 0):
    problems = await _disputes_refresh(bool(refresh))
    cases = [_case_out(r) for r in db.list_disputes()]
    for c in cases:
        c.pop("messages", None)
    order = {"respond": 0, "waiting": 1, "review": 2, "closed": 3}
    cases.sort(key=lambda c: (order.get(c["state"], 4), c.get("due") or "9999", c.get("updated") or ""), reverse=False)
    closed = [c for c in cases if c["state"] == "closed"]
    closed.sort(key=lambda c: c.get("updated") or "", reverse=True)
    cases = [c for c in cases if c["state"] != "closed"] + closed
    counts = defaultdict(int)
    at_stake = defaultdict(float)
    for c in cases:
        counts[c["state"]] += 1
        if c["state"] != "closed":
            at_stake[c["currency"]] += c["amount"]
    return {"cases": cases, "counts": counts, "at_stake": at_stake, "problems": problems,
            "connected": any(c["provider"] in ("paypal", "airwallex") for c in db.list_cash_connections())}


async def _dispute_conn(key: str):
    row = db.get_dispute(key)
    if not row:
        raise HTTPException(404, "That case isn't in the list any more. Refresh.")
    case = json.loads(row["data"])
    c = next((x for x in db.list_cash_connections() if x["id"] == case["conn_id"]), None)
    if not c:
        raise HTTPException(400, "The account this case belongs to isn't connected any more.")
    return row, case, c


async def _dispute_fresh(key: str) -> dict:
    """Re-read one case from PayPal / Airwallex now (after an action, or when opened)."""
    row, case, c = await _dispute_conn(key)
    try:
        if case["provider"] == "paypal":
            case = disputes.from_paypal(await paypal.dispute(c["client_id"], c["secret"], case["id"]), c)
        else:
            case = disputes.from_airwallex(await awx.dispute(c["client_id"], c["secret"], case["id"], c.get("account_id")), c)
    except (paypal.PayPalError, awx.AirwallexError) as e:
        raise HTTPException(400, str(e))
    db.save_dispute(case)
    return db.get_dispute(key)


@app.get("/api/disputes/case")
async def api_dispute(key: str, request: Request):
    row = await _dispute_fresh(key)
    if not row["order_name"]:
        found = await _dispute_order(json.loads(row["data"]))
        if found:
            db.link_dispute(key, *found)
            row = db.get_dispute(key)
    case = _case_out(row, request)
    ctx = _dispute_context(row["store_id"], row["order_name"])
    case["context"] = ctx
    case["log"] = db.dispute_log(key)
    case["has_ai"] = bool(db.get_setting("anthropic_api_key"))
    return case


@app.post("/api/disputes/link")
async def api_dispute_link(payload: dict, request: Request):
    """Link a case to its order by hand (order number, e.g. #BR2342)."""
    key, name = payload.get("key") or "", (payload.get("order") or "").strip()
    if not db.get_dispute(key):
        raise HTTPException(404, "That case isn't in the list any more.")
    k = cog.order_key(name)
    with db._conn() as con:
        r = con.execute("SELECT store_id, order_name FROM cog_lines WHERE order_name IN (?, ?) LIMIT 1",
                        (k, "#" + k)).fetchone()
    if not r:
        raise HTTPException(400, f"No order {name} in any store (orders of the last ~5 months).")
    db.link_dispute(key, r[0], r[1], _who(request))
    db.log_dispute(key, _who(request), "Linked order", r[1])
    return {"ok": True}


@app.get("/api/disputes/pack")
async def api_dispute_pack(key: str, page: int = 0):
    """The evidence pack, as it will be sent: PDF (PayPal) or PNG pages (Airwallex)."""
    row = db.get_dispute(key)
    if not row:
        raise HTTPException(404, "That case isn't in the list any more.")
    case = json.loads(row["data"])
    lines = disputes.pack_lines(case, _dispute_context(row["store_id"], row["order_name"]))
    title = f"Evidence · {case['reason']} · {row['order_name'] or case['id']}"
    if case["provider"] == "paypal":
        return Response(disputes.pack_pdf(title, lines), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="evidence {case["id"]}.pdf"'})
    pngs = disputes.pack_pngs(title, lines)
    return Response(pngs[min(page, len(pngs) - 1)], media_type="image/png")


@app.post("/api/disputes/draft")
async def api_dispute_draft(payload: dict):
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    row = db.get_dispute(payload.get("key") or "")
    if not row:
        raise HTTPException(404, "That case isn't in the list any more.")
    case = json.loads(row["data"])
    lines = disputes.pack_lines(case, _dispute_context(row["store_id"], row["order_name"]))
    try:
        return await disputes.draft(key, case, lines)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/disputes/act")
async def api_dispute_act(request: Request):
    """Do one thing on a case. Form fields: key, action, note, message, amount, offer_type,
    accept_type, evidence_type, tracking (yes/no), pack (yes/no) + files."""
    form = await request.form()
    key, action = form.get("key") or "", form.get("action") or ""
    if action not in disputes.ACTIONS:
        raise HTTPException(400, "Pick what to do.")
    _need_level(request, "disputes", disputes.ACTIONS[action][1])
    row, case, c = await _dispute_conn(key)
    if action not in case["actions"]:
        raise HTTPException(400, "That isn't possible on this case right now (refresh to see what is).")
    note = (form.get("note") or "").strip()[:2000]
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read"):
            data = await f.read()
            if data:
                if len(data) > 10 * 1024 * 1024:
                    raise HTTPException(400, f"{f.filename} is over 10 MB.")
                ext = (f.filename.rsplit(".", 1)[-1] or "bin").lower()
                files.append((disputes.safe_name(f.filename, ext), data, f.content_type or "application/octet-stream"))
    ctx = _dispute_context(row["store_id"], row["order_name"])
    lines = disputes.pack_lines(case, ctx)
    title = f"Evidence · {case['reason']} · {row['order_name'] or case['id']}"
    with_pack = form.get("pack") == "yes" and bool(ctx)
    try:
        if case["provider"] == "paypal":
            body = {}
            if action == "send-message":
                if not (form.get("message") or "").strip():
                    raise HTTPException(400, "Type the message.")
                body = {"message": form.get("message").strip()[:2000]}
            elif action in ("provide-evidence", "appeal"):
                if with_pack:
                    files.insert(0, (disputes.safe_name(f"Evidence {row['order_name'] or case['id']}", "pdf"),
                                     disputes.pack_pdf(title, lines), "application/pdf"))
                ev = {"evidence_type": form.get("evidence_type") or "OTHER", "notes": note or "See the attached evidence."}
                if form.get("tracking") == "yes" and ctx.get("shipments"):
                    ev["evidence_type"] = "PROOF_OF_FULFILLMENT"
                    ev["evidence_info"] = {"tracking_info": [{"carrier_name": "OTHER", "carrier_name_other": (s["company"] or "Carrier")[:60],
                                                              "tracking_url": s.get("tracking_url") or None,
                                                              "tracking_number": s["number"]} for s in ctx["shipments"][:5]]}
                    for t in ev["evidence_info"]["tracking_info"]:
                        if not t["tracking_url"]:
                            t.pop("tracking_url")
                if files:
                    ev["documents"] = [{"name": n} for n, _, _ in files]
                body = {"evidences": [ev]}
            elif action == "provide-supporting-info":
                if with_pack:
                    files.insert(0, (disputes.safe_name(f"Evidence {row['order_name'] or case['id']}", "pdf"),
                                     disputes.pack_pdf(title, lines), "application/pdf"))
                body = {"notes": note or "See the attached evidence."}
            elif action == "make-offer":
                body = {"note": note or "Offer to resolve.", "offer_type": form.get("offer_type") or "REFUND"}
                if form.get("amount"):
                    body["offer_amount"] = {"currency_code": case["currency"], "value": f"{float(form.get('amount')):.2f}"}
            elif action == "accept-claim":
                body = {"note": note or "Accepted."}
                if form.get("accept_type"):
                    body["accept_claim_type"] = form.get("accept_type")
                if form.get("amount") and form.get("accept_type") == "PARTIAL_REFUND":
                    body["refund_amount"] = {"currency_code": case["currency"], "value": f"{float(form.get('amount')):.2f}"}
            elif action == "acknowledge-return-item":
                body = {"note": note or "Return received."}
                if form.get("ack_type"):
                    body["acknowledgement_type"] = form.get("ack_type")
            else:                                        # escalate, accept-offer, deny-offer
                body = {"note": note or action.replace("-", " ").capitalize()}
            await paypal.dispute_action(c["client_id"], c["secret"], case["id"], action, body, files)
        else:
            if action == "accept":
                await awx.dispute_action(c["client_id"], c["secret"], case["id"], "accept",
                                         {"reason": form.get("accept_reason") or "OTHERS", "description": note,
                                          "accepted_by": _who(request)}, c.get("account_id"))
            else:                                        # challenge
                imgs = [(f"evidence-{n + 1}.png", png, "image/png") for n, png in
                        enumerate(disputes.pack_pngs(title, lines))] if with_pack else []
                ids = [await awx.upload_file(c["client_id"], c["secret"], n, d, t, c.get("account_id"))
                       for n, d, t in imgs + files]
                body = {"challenged_by": _who(request), "product_type": "PHYSICAL_GOODS",
                        "product_description": ", ".join(f"{i['qty']} x {i['product']}" for i in
                                                         (ctx.get("order") or {}).get("items") or [])[:500] or "Physical goods",
                        "reason": form.get("challenge_reason") or "OTHER_REASONS"}
                if ids:
                    body["supporting_documents"] = {"documents": [{"type": "PRIMARY", "file_ids": ids,
                                                                   "description": (note or "Order, shipping and tracking evidence")[:500]}]}
                ship = (ctx.get("shipments") or [None])[0]
                if ship and case["stage"] == "Information request":
                    body["delivery_info"] = {k: v for k, v in {"tracking_number": ship["number"],
                                             "shipping_company": ship["company"], "shipped_at": ship["fulfilled_at"],
                                             "delivered_at": ship.get("delivered_at")}.items() if v}
                await awx.dispute_action(c["client_id"], c["secret"], case["id"], "challenge", body, c.get("account_id"))
    except (paypal.PayPalError, awx.AirwallexError) as e:
        db.log_dispute(key, _who(request), f"{disputes.ACTIONS[action][0]} (failed)", str(e))
        raise HTTPException(400, str(e))
    detail = form.get("message") or note
    if files:
        detail += f" · files: {', '.join(n for n, _, _ in files)}"
    if form.get("amount"):
        detail += f" · {form.get('amount')} {case['currency']}"
    db.log_dispute(key, _who(request), disputes.ACTIONS[action][0], detail.strip(" ·"))
    _disp["at"] = 0
    try:
        await _dispute_fresh(key)
    except HTTPException:
        pass
    return {"ok": True}


@app.get("/expenses", response_class=HTMLResponse)
def expenses_page():
    return _page("expenses.html", ("expenses.js", "nav.js", "styles.css", "skin.css", "chat.js"))


_exp_cache: dict = {}
_suggesting: set = set()


def _own_payees() -> set:
    """Airwallex payees the owner confirmed are their own accounts (not connected here)."""
    return {r["key"] for r in db.expense_rules()
            if r["key"].startswith(("awx-transfer:", "tx:")) and r["category"] == "ignore"}


async def _expense_moves(since: datetime, until: datetime) -> tuple:
    """Every Airwallex and PayPal movement in [since, until), paired like the statement.
    Kept for 3 minutes per window. Returns (moves, problems)."""
    ck = (since.isoformat(timespec="minutes"), until.isoformat(timespec="minutes")[:15])
    hit = _exp_cache.get(ck)
    if hit and time.time() - hit[0] < 180:
        return hit[1], hit[2]
    now = datetime.now(CASH_TZ)

    async def moves_for(c):
        if c["provider"] == "airwallex":
            rows, cards = await asyncio.gather(
                awx.transactions(c["client_id"], c["secret"], since, until, c["account_id"]),
                awx.card_transactions(c["client_id"], c["secret"], since - timedelta(days=7), c["account_id"]))
            return statement.airwallex_moves(c["label"], rows, cards)
        if c["provider"] == "paypal":
            rows = await paypal.transactions(c["client_id"], c["secret"], days=(now - since).days + 1,
                                             fields="transaction_info,payer_info", full=True)
            return [m for m in statement.paypal_moves(c["label"], rows) if since <= m["time"] < until]
        return []

    conns = db.list_cash_connections()
    got = await asyncio.gather(*[moves_for(c) for c in conns], return_exceptions=True)
    moves, problems = [], []
    for c, g in zip(conns, got):
        if isinstance(g, Exception):
            problems.append(f"{c['label']}: {g}")
        else:
            moves.extend(g)
    statement.pair(moves, datetime.now(timezone.utc), _own_payees())
    _exp_cache.clear()
    _exp_cache[ck] = (time.time(), moves, problems)
    return moves, problems


def _period(period: str, start: str, end: str) -> tuple:
    """Named period (today, yesterday, month, last_month, custom) -> first and last day."""
    today = datetime.now(CASH_TZ).date()
    if period == "today":
        return today, today
    if period == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if period == "custom" and start:
        try:
            d0, d1 = date.fromisoformat(start), date.fromisoformat(end or start)
        except ValueError:
            raise HTTPException(400, "Pick a start and end date.")
        d0, d1 = min(d0, d1), min(max(d0, d1), today)
        if (today - d0).days > 400:
            raise HTTPException(400, "Expenses go back up to about 13 months.")
        return d0, d1
    return today.replace(day=1), today


@app.get("/api/expenses")
async def api_expenses(period: str = "month", start: str = None, end: str = None, currency: str = None):
    d0, d1 = _period(period, start, end)
    t0 = datetime.combine(d0, clock.min, CASH_TZ)
    t1 = datetime.combine(d1 + timedelta(days=1), clock.min, CASH_TZ)
    now = datetime.now(CASH_TZ)
    if not db.list_cash_connections():
        raise HTTPException(400, "Connect Airwallex or PayPal first (Cash flow → Settings).")
    since, until = expenses.window(t0, t1, now)
    moves, problems = await _expense_moves(since, until)
    base = (currency or _display_currency()).upper()
    codes = {m["currency"] for m in moves if m.get("currency")} | \
            {l["currency"] for m in moves for l in m.get("legs", [])}
    table = await fx.table(base) if codes - {base} else None
    factor = (lambda code: fx.factor(table, code, base)) if table else (lambda code: 1.0)

    rules = db.expense_rules()
    items = expenses.apply_rules(expenses.classify(moves, factor), *expenses.rule_map(rules))
    out = expenses.summarize(items, factor, t0, t1, CASH_TZ, base)
    hints = db.expense_suggestions()
    for s in out["to_sort"]:
        h = hints.get(s["key"])
        s["suggestion"] = {"category": h["category"], "reason": h["reason"]} if h else None

    missing = [s for s in out["to_sort"] if not s["suggestion"]]
    api_key = db.get_setting("anthropic_api_key")
    if missing and api_key and not _suggesting:
        _suggesting.add(1)
        asyncio.create_task(_suggest_expenses(api_key, missing, rules))
    sups = set(db.supplier_accounts()) | set(db.supplier_payees().values())
    with db._conn() as con:
        sups |= {r[0] for r in con.execute("SELECT DISTINCT supplier FROM invoices WHERE supplier IS NOT NULL")}
    return {**out, "start": d0.isoformat(), "end": d1.isoformat(), "period": period,
            "suppliers": sorted((x for x in sups if x), key=str.lower),
            "today": now.date().isoformat(), "live": d1 >= now.date(), "problems": problems,
            "suggesting": bool(_suggesting), "category_list": [{"key": k, "name": n} for k, n in expenses.CATEGORIES],
            "fx": None if not table else {"date": table.get("date")}}


async def _suggest_expenses(api_key, missing, rules):
    try:
        db.save_expense_suggestions(await expenses.suggest(api_key, missing, rules))
    except Exception as e:
        print(f"Expense suggestions failed: {e}")
    finally:
        _suggesting.clear()


@app.post("/api/expenses/sort")
def api_expense_sort(payload: dict, request: Request):
    """The owner's pick for a payee (or one transaction). Applies to every payment to it."""
    key, category = (payload.get("key") or "").strip(), payload.get("category") or ""
    if not key or (category and category not in expenses.NAMES):
        raise HTTPException(400, "Pick a category.")
    user = getattr(request.state, "user", None) or {}
    db.set_expense_rule(key, category, payload.get("name") or "", user.get("email", ""))
    return {"ok": True}


@app.get("/api/expenses/rules")
def api_expense_rules():
    return {"rules": db.expense_rules(), "categories": [{"key": k, "name": n} for k, n in expenses.CATEGORIES]}


# ---------------------------------------------------------------- SCM desk

@app.get("/scm", response_class=HTMLResponse)
def scm_page():
    return _page("scm.html", ("scm.js", "nav.js", "styles.css", "skin.css", "chat.js"))


SCM_VIEWS = {
    "all": None,
    "awaiting": "s.status = 'awaiting'",
    "untracked": "s.number != '' AND s.registered = 0",
    "pending": "s.status = 'pending' AND s.registered = 1",
    "info_received": "s.status = 'info_received'",
    "in_transit": "s.status = 'in_transit'",
    "out_for_delivery": "s.status = 'out_for_delivery'",
    "pickup": "s.status = 'pickup'",
    "delivered": "s.status = 'delivered'",
    "exception": "s.status IN ('exception', 'failed_attempt', 'expired')",
    # Moving nowhere: no carrier update for 7 days, or no tracking 5 days after the order.
    "stuck": "((s.status IN ('info_received', 'in_transit', 'pending') AND s.number != '' AND s.registered = 1"
             " AND COALESCE(s.last_event_at, s.fulfilled_at) < :stuck_since)"
             " OR (s.status = 'awaiting' AND s.order_at < :late_since))",
    "flagged": "EXISTS (SELECT 1 FROM scm_flags f WHERE f.store_id = s.store_id AND f.order_id = s.order_id)",
    "noted": "EXISTS (SELECT 1 FROM scm_notes n WHERE n.store_id = s.store_id AND n.order_id = s.order_id)",
}


def _scm_where(view, q, store, carrier, country, start, end, date_by: str = "order") -> tuple:
    now = datetime.now(timezone.utc)
    params = {"stuck_since": (now - timedelta(days=7)).isoformat(),
              "late_since": (now - timedelta(days=5)).isoformat(),
              "start": start or (now - timedelta(days=60)).date().isoformat(),
              "end": (date.fromisoformat(end) + timedelta(days=1)).isoformat() if end else "9999"}
    col = "s.fulfilled_at" if date_by == "fulfilled" else "s.order_at"
    where = [f"{col} >= :start", f"{col} < :end"]
    where.append("s.cancelled = 1" if view == "cancelled" else "s.cancelled = 0")
    if SCM_VIEWS.get(view):
        where.append(SCM_VIEWS[view])
    if q:
        params["q"] = f"%{q.strip().lstrip('#').lower()}%"
        where.append("(lower(s.order_name) LIKE :q OR lower(s.number) LIKE :q OR lower(s.email) LIKE :q"
                     " OR lower(s.customer) LIKE :q OR lower(COALESCE(s.last_mile_number, '')) LIKE :q)")
    if store:
        params["store"] = int(store)
        where.append("s.store_id = :store")
    if carrier:
        params["carrier"] = carrier
        where.append("COALESCE(NULLIF(s.carrier_name, ''), s.company) = :carrier")
    if country:
        params["country"] = country
        where.append("s.country = :country")
    return " AND ".join(where), params


_SCM_COLS = ("id, store_id, order_id, order_name, order_at, customer, email, country, city, items, number,"
             " company, tracking_url, fulfilled_at, status, sub_status, last_event, last_event_at, last_location,"
             " carrier_name, last_mile, last_mile_number, transit_days, eta_from, eta_to, delivered_at,"
             " registered, register_error, cancelled, fulfillment")


SCM_SORT = {
    "order_desc": "s.order_at DESC", "order_asc": "s.order_at ASC",
    "update_desc": "COALESCE(s.last_event_at, '') DESC", "update_asc": "COALESCE(s.last_event_at, '9999') ASC",
    "transit_desc": "COALESCE(s.transit_days, julianday('now') - julianday(s.fulfilled_at), 0) DESC",
}


def _scm_rows(where, params, order_by, limit=50, offset=0) -> list:
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            f"SELECT {_SCM_COLS},"
            " (SELECT COUNT(*) FROM scm_notes n WHERE n.store_id = s.store_id AND n.order_id = s.order_id) notes,"
            " (SELECT text FROM scm_notes n WHERE n.store_id = s.store_id AND n.order_id = s.order_id"
            "  ORDER BY n.id DESC LIMIT 1) last_note,"
            " EXISTS (SELECT 1 FROM scm_flags f WHERE f.store_id = s.store_id AND f.order_id = s.order_id) flagged"
            f" FROM shipments s WHERE {where} ORDER BY {order_by}, s.id DESC LIMIT :lim OFFSET :off",
            {**params, "lim": limit, "off": offset})]
    now = datetime.now(timezone.utc)
    names = _store_names()
    for r in rows:
        r["store"] = names.get(r["store_id"], "")
        since = r["fulfilled_at"] if r["number"] else r["order_at"]
        r["age_days"] = (now - datetime.fromisoformat(since.replace("Z", "+00:00"))).days if since else None
        end_at = r["delivered_at"] or now.isoformat()
        if r["fulfilled_at"] and r["transit_days"] is None:
            r["transit_days"] = (datetime.fromisoformat(end_at.replace("Z", "+00:00"))
                                 - datetime.fromisoformat(r["fulfilled_at"].replace("Z", "+00:00"))).days
        r["quiet_days"] = (now - datetime.fromisoformat(r["last_event_at"].replace("Z", "+00:00"))).days \
            if r.get("last_event_at") else None
    return rows


_credits_cache: dict = {}


async def _credits() -> dict:
    """17TRACK credits left (checked at most every 5 minutes) and how long they'll last."""
    key = _tracking_config()["key"]
    if not key:
        return None
    hit = _credits_cache.get("v")
    if not hit or time.time() - hit[0] > 300:
        try:
            q = await track17.quota(key)
        except track17.TrackError as e:
            return {"error": str(e)}
        cfg = _tracking_config()
        with db._conn() as con:
            per_day = con.execute(
                "SELECT COUNT(DISTINCT number) / 7.0 FROM shipments WHERE number != '' AND cancelled = 0"
                " AND fulfilled_at >= ?", ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)).fetchone()[0]
        queued = len(db.shipments_to_register(cfg["stores"], cfg["from"], limit=100000))
        left = q.get("quota_remain") or 0
        hit = _credits_cache["v"] = (time.time(), {
            "left": left, "total": q.get("quota_total"), "used": q.get("quota_used"), "queued": queued,
            "per_day": round(per_day or 0),
            "days_left": max(0, int((left - queued) / per_day)) if per_day else None,
            "low": left <= 250})
    return hit[1]


@app.get("/api/scm/credits")
async def api_scm_credits():
    return await _credits() or {}


@app.get("/api/scm/shipments")
def api_scm_shipments(view: str = "all", q: str = None, store: str = None, carrier: str = None,
                      country: str = None, start: str = None, end: str = None, page: int = 1,
                      sort: str = "order_desc", date_by: str = "order"):
    args = (q, store, carrier, country, start, end, date_by)
    where, params = _scm_where(view, *args)
    base_where, base_params = _scm_where("all", *args)
    with db._conn() as con:
        total = con.execute(f"SELECT COUNT(*) FROM shipments s WHERE {where}", params).fetchone()[0]
        counts = {}
        for v in list(SCM_VIEWS) + ["cancelled"]:
            w, p = _scm_where(v, *args)
            counts[v] = con.execute(f"SELECT COUNT(*) FROM shipments s WHERE {w}", p).fetchone()[0]
        carriers = [r[0] for r in con.execute(
            f"SELECT COALESCE(NULLIF(s.carrier_name, ''), s.company) c FROM shipments s WHERE {base_where}"
            " AND COALESCE(NULLIF(s.carrier_name, ''), s.company, '') != '' GROUP BY c ORDER BY COUNT(*) DESC",
            base_params)]
        countries = [r[0] for r in con.execute(
            f"SELECT s.country FROM shipments s WHERE {base_where} AND s.country != '' GROUP BY s.country"
            " ORDER BY COUNT(*) DESC", base_params)]
        st = dict(con.execute(
            "SELECT SUM(s.number != '') parcels, SUM(s.number != '' AND s.registered = 1) tracked,"
            " SUM(s.status = 'delivered') delivered,"
            " SUM(s.registered = 1 AND s.status NOT IN ('pending', 'awaiting', 'expired')) with_updates,"
            " AVG(CASE WHEN s.status = 'delivered' AND s.delivered_at IS NOT NULL AND s.fulfilled_at IS NOT NULL"
            "     THEN julianday(s.delivered_at) - julianday(s.fulfilled_at) END) avg_delivery_days,"
            " AVG(CASE WHEN s.number != '' AND s.fulfilled_at IS NOT NULL"
            "     THEN julianday(s.fulfilled_at) - julianday(s.order_at) END) avg_dispatch_days,"
            " COUNT(DISTINCT s.store_id || ':' || s.order_id) orders"
            f" FROM shipments s WHERE {base_where}", base_params).fetchone())
    rows = _scm_rows(where, params, SCM_SORT.get(sort, SCM_SORT["order_desc"]), 50, max(0, page - 1) * 50)
    names = _store_names()
    return {"rows": rows, "total": total, "page": page, "pages": max(1, -(-total // 50)),
            "counts": counts, "carriers": carriers, "countries": countries, "summary": st,
            "stores": [{"id": k, "name": v} for k, v in names.items()],
            "status": _scm_status()}


@app.get("/api/scm/export")
def api_scm_export(view: str = "all", q: str = None, store: str = None, carrier: str = None,
                   country: str = None, start: str = None, end: str = None, sort: str = "order_desc",
                   date_by: str = "order"):
    """The current view as a spreadsheet (CSV)."""
    from fastapi.responses import Response
    where, params = _scm_where(view, q, store, carrier, country, start, end, date_by)
    rows = _scm_rows(where, params, SCM_SORT.get(sort, SCM_SORT["order_desc"]), 20000, 0)
    cols = ["Store", "Order", "Order date", "Customer", "Email", "Ship to", "Items", "Tracking number", "Carrier",
            "Last mile", "Last mile number", "Fulfilled", "Status", "Last checkpoint", "Last update",
            "Transit days", "Delivered", "Flagged", "Notes", "Latest note"]
    out = [[r["store"], r["order_name"], (r["order_at"] or "")[:16], r["customer"], r["email"],
            ", ".join(x for x in (r["city"], r["country"]) if x), r["items"], r["number"],
            r["carrier_name"] or r["company"], r["last_mile"], r["last_mile_number"], (r["fulfilled_at"] or "")[:16],
            "untracked" if r["number"] and not r["registered"] else r["status"], r["last_event"],
            (r["last_event_at"] or "")[:16], r["transit_days"], (r["delivered_at"] or "")[:16],
            "yes" if r["flagged"] else "", r["notes"], r["last_note"] or ""] for r in rows]
    name = f"scm-{view}-{datetime.now(CASH_TZ):%Y-%m-%d}.csv"
    return Response(reports.to_csv(cols, out), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/scm/orders/{store_id}/{order_id}/notes")
def api_scm_add_note(store_id: int, order_id: str, payload: dict, request: Request):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write a note first.")
    user = getattr(request.state, "user", None) or {}
    with db._conn() as con:
        con.execute("INSERT INTO scm_notes (store_id, order_id, text, author) VALUES (?, ?, ?, ?)",
                    (store_id, order_id, text[:4000], user.get("name") or user.get("email") or ""))
    return {"ok": True}


@app.delete("/api/scm/notes/{note_id}")
def api_scm_delete_note(note_id: int, request: Request):
    user = getattr(request.state, "user", None) or {}
    with db._conn() as con:
        n = con.execute("SELECT author FROM scm_notes WHERE id = ?", (note_id,)).fetchone()
        if not n:
            raise HTTPException(404, "That note no longer exists.")
        if not auth.at_least(user, "scm", "manage") and n["author"] not in (user.get("name"), user.get("email")):
            raise HTTPException(403, "Only the person who wrote a note (or an owner) can delete it.")
        con.execute("DELETE FROM scm_notes WHERE id = ?", (note_id,))
    return {"ok": True}


@app.post("/api/scm/orders/{store_id}/{order_id}/flag")
def api_scm_flag(store_id: int, order_id: str, payload: dict, request: Request):
    user = getattr(request.state, "user", None) or {}
    with db._conn() as con:
        if payload.get("flagged"):
            con.execute("INSERT OR REPLACE INTO scm_flags (store_id, order_id, flagged_by) VALUES (?, ?, ?)",
                        (store_id, order_id, user.get("name") or user.get("email") or ""))
        else:
            con.execute("DELETE FROM scm_flags WHERE store_id = ? AND order_id = ?", (store_id, order_id))
    return {"ok": True}


@app.get("/api/scm/shipments/{shipment_id}")
def api_scm_shipment(shipment_id: int):
    with db._conn() as con:
        r = con.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        if not r:
            raise HTTPException(404, "That shipment no longer exists.")
        r = dict(r)
        others = [dict(x) for x in con.execute(
            "SELECT id, number, status, company FROM shipments WHERE store_id = ? AND order_id = ? AND id != ?",
            (r["store_id"], r["order_id"], r["id"]))]
        key = (r["order_name"] or "").lstrip("#")
        tickets = [dict(t) for t in con.execute(
            "SELECT id, subject, status, kind, labels, created_at FROM tickets WHERE store_id = ?"
            " AND (orders LIKE ? OR customer_email = ?) ORDER BY created_at DESC LIMIT 10",
            (r["store_id"], f"%{key}%", r["email"] or "-"))] if key else []
    with db._conn() as con:
        notes = [dict(n) for n in con.execute(
            "SELECT id, text, author, created_at FROM scm_notes WHERE store_id = ? AND order_id = ? ORDER BY id DESC",
            (r["store_id"], r["order_id"]))]
        flag = con.execute("SELECT flagged_by, flagged_at FROM scm_flags WHERE store_id = ? AND order_id = ?",
                           (r["store_id"], r["order_id"])).fetchone()
    store = next((x for x in db.list_stores() if x["id"] == r["store_id"]), None)
    r["events"] = json.loads(r["events"] or "[]")
    r["store"] = (store or {}).get("name", "").strip()
    r["shopify_url"] = (f"https://admin.shopify.com/store/{store['shop_domain'].removesuffix('.myshopify.com')}"
                        f"/orders/{r['order_id']}") if store else None
    r["tracking_page"] = (f"https://{store['shop_domain']}/apps/track?nums={r['number']}"
                          if store and r["number"] else None)
    return {"shipment": r, "other_parcels": others, "tickets": tickets, "notes": notes,
            "flag": dict(flag) if flag else None}


def _scm_status() -> dict:
    try:
        return json.loads(db.get_setting("scm_status") or "{}")
    except ValueError:
        return {}


def _scm_set_status(**kw):
    st = _scm_status()
    st.update(kw)
    db.set_setting("scm_status", json.dumps(st))


def _tracking_config() -> dict:
    try:
        stores = [int(x) for x in json.loads(db.get_setting("track_stores") or "[]")]
    except ValueError:
        stores = []
    return {"key": db.get_setting("track17_key") or "", "stores": stores,
            "from": db.get_setting("track_from") or datetime.now(timezone.utc).date().isoformat()}


_scm_running = asyncio.Lock()


async def _scm_sync_orders():
    """Every store's orders and tracking numbers from Shopify (changes since the last run)."""
    for store in db.list_stores():
        if demo.is_demo(store) or not store.get("access_token"):
            continue
        key = f"scm_sync:{store['id']}"
        last = db.get_setting(key)
        started = datetime.now(timezone.utc) - timedelta(minutes=5)
        try:
            client = ShopifyClient(store["shop_domain"], store["access_token"])
            if last:
                orders = await client.shipments(last, "updated_at")
            else:
                since = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
                orders = await client.shipments(since, "created_at")
            for o in orders:
                db.save_order_shipments(store["id"], o)
            db.set_setting(key, started.strftime("%Y-%m-%dT%H:%M:%SZ"))
        except Exception as e:
            _scm_set_status(**{f"shopify_error_{store['id']}": f"{store['name'].strip()}: {e}"})
            continue
        _scm_set_status(**{f"shopify_error_{store['id']}": None})


async def _scm_track():
    """Register new parcels with 17TRACK and pick up anything the webhook missed."""
    cfg = _tracking_config()
    if not cfg["key"]:
        return
    todo = db.shipments_to_register(cfg["stores"], cfg["from"])
    registered = 0
    try:
        for i in range(0, len(todo), 40):
            batch = todo[i:i + 40]
            ok, bad = await track17.register(cfg["key"], [{
                "number": t["number"], "order_no": t["order_name"], "order_time": (t["order_at"] or "")[:10],
                "destination_country": t["country"], "tag": f"{t['store_id']}:{t['order_id']}"} for t in batch])
            db.mark_registered(ok, bad)
            registered += len(ok)
            await asyncio.sleep(0.5)
        # The webhook brings updates as they happen; this catches up once a day.
        stale = db.shipments_to_refresh()
        for i in range(0, len(stale), 40):
            for item in await track17.track_info(cfg["key"], stale[i:i + 40]):
                if item.get("track_info"):
                    db.save_tracking(item["number"], track17.summarize(item))
            await asyncio.sleep(0.5)
        _scm_set_status(track_error=None, last_track=datetime.now(timezone.utc).isoformat(),
                        last_registered=registered)
    except track17.TrackError as e:
        _scm_set_status(track_error=str(e))


# 17TRACK status -> Shopify fulfillment event. Each is sent once per parcel.
SHOPIFY_EVENT = {"in_transit": "IN_TRANSIT", "pickup": "READY_FOR_PICKUP", "out_for_delivery": "OUT_FOR_DELIVERY",
                 "failed_attempt": "ATTEMPTED_DELIVERY", "delivered": "DELIVERED"}


def _push_stores() -> set:
    return {x["id"] for x in db.list_stores() if db.get_setting(f"scm_push:{x['id']}") == "1"}


async def _scm_push():
    """Send new tracking statuses to Shopify for stores with "Tell Shopify" on."""
    on = _push_stores()
    if not on:
        return
    stores = {x["id"]: x for x in db.list_stores()}
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            f"SELECT id, store_id, fulfillment_gid, status, pushed, last_event_at, delivered_at, eta_to, city,"
            f" province, country FROM shipments WHERE registered = 1 AND cancelled = 0 AND fulfillment_gid IS NOT NULL"
            f" AND status IN ({','.join('?' * len(SHOPIFY_EVENT))}) AND store_id IN ({','.join('?' * len(on))})"
            f" AND instr(',' || pushed || ',', ',' || status || ',') = 0 LIMIT 200",
            (*SHOPIFY_EVENT, *on))]
    for r in rows:
        store = stores.get(r["store_id"])
        if not store:
            continue
        local = r["status"] in ("out_for_delivery", "delivered", "failed_attempt", "pickup")
        try:
            await ShopifyClient(store["shop_domain"], store["access_token"]).fulfillment_event(
                r["fulfillment_gid"], SHOPIFY_EVENT[r["status"]],
                happened_at=r["delivered_at"] if r["status"] == "delivered" else r["last_event_at"],
                city=r["city"] if local else None, province=r["province"] if local else None,
                estimated=r["eta_to"])
            err = None
        except Exception as e:
            err = str(e)[:200]
        with db._conn() as con:
            con.execute("UPDATE shipments SET pushed = trim(pushed || ',' || ?, ','), push_error = ? WHERE id = ?",
                        (r["status"], err, r["id"]))
        await asyncio.sleep(0.6)


async def _scm_run():
    async with _scm_running:
        if not db.get_setting("scm_resync_v3"):
            # One full re-read so older orders get their state / province too.
            with db._conn() as con:
                con.execute("DELETE FROM settings WHERE key LIKE 'scm_sync:%'")
            db.set_setting("scm_resync_v3", "1")
        await _scm_sync_orders()
        await _scm_track()
        await _scm_push()
        _scm_set_status(last_sync=datetime.now(timezone.utc).isoformat())


async def _scm_loop():
    await asyncio.sleep(90)
    while True:
        try:
            await _scm_run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"SCM sync failed: {e}")
        await asyncio.sleep(600)


@app.post("/api/scm/sync")
async def api_scm_sync():
    if _scm_running.locked():
        return {"ok": True, "running": True}
    asyncio.create_task(_scm_run())
    return {"ok": True}


@app.post("/api/scm/webhook")
async def api_scm_webhook(request: Request):
    """17TRACK pushes every tracking update here (set this URL in the 17TRACK dashboard)."""
    body = await request.body()
    key = _tracking_config()["key"]
    if not key or not track17.verify(body, request.headers.get("sign", ""), key):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        push = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "bad body"}, status_code=400)
    data = push.get("data") or {}
    if push.get("event") == "TRACKING_UPDATED" and data.get("number") and data.get("track_info"):
        db.save_tracking(data["number"], track17.summarize(data))
    _scm_set_status(last_webhook=datetime.now(timezone.utc).isoformat())
    return {"ok": True}


def _find_parcels(store_id: int, order: str, email: str, nums: str) -> tuple:
    """A customer's parcels: by order number + email, or by tracking number(s)."""
    with db._conn() as con:
        if nums:
            wanted = [n.strip().replace(" ", "") for n in nums.split(",") if n.strip()][:10]
            if not wanted:
                return [], "Enter a tracking number."
            marks = ",".join("?" * len(wanted))
            rows = con.execute(f"SELECT * FROM shipments WHERE store_id = ? AND cancelled = 0 AND"
                               f" (number IN ({marks}) OR last_mile_number IN ({marks}))",
                               (store_id, *wanted, *wanted)).fetchall()
            return [dict(r) for r in rows], ("" if rows else "We couldn't find that tracking number yet. "
                                             "It can take a day after shipping to show up.")
        name = (order or "").strip().lstrip("#")
        if not name or not (email or "").strip():
            return [], ""
        rows = con.execute("SELECT * FROM shipments WHERE store_id = ? AND cancelled = 0 AND"
                           " ltrim(order_name, '#') = ? COLLATE NOCASE AND lower(email) = ?",
                           (store_id, name, email.strip().lower())).fetchall()
        return [dict(r) for r in rows], ("" if rows else "We couldn't find an order with that number and email. "
                                         "Check both match your order confirmation.")


@app.get("/proxy/track")
@app.get("/proxy/track/")
@app.get("/proxy/track/{rest:path}")
async def proxy_track(request: Request, order: str = None, email: str = None, nums: str = None, rest: str = ""):
    """The customer tracking page. Reached through each store's Shopify app proxy
    (store.com/apps/track), which signs the request; owners can preview it directly."""
    from fastapi.responses import Response
    query = {}
    for k, v in request.query_params.multi_items():
        query.setdefault(k, []).append(v)
    query = {k: (v if len(v) > 1 else v[0]) for k, v in query.items()}
    shop_domain = (query.get("shop") or "").strip().lower()
    store = next((x for x in db.list_stores() if x["shop_domain"].lower() == shop_domain), None)
    if not store:
        return Response("Store not found.", status_code=404)
    app_keys = _shopify_app(store["shop_domain"])
    signed = bool(app_keys) and trackpage.verify_proxy(query, app_keys[1])
    if not signed and not getattr(request.state, "user", None):
        return Response("Not allowed.", status_code=401)
    if rest.strip("/") == "rec/config":                 # session recordings: is this store recording?
        cfg = recordings.config(store["id"])
        on = signed and cfg["on"] and recordings.started_today(store["id"]) < cfg["per_day"]
        return JSONResponse({"on": bool(on), "sample": cfg["sample"]}, headers={"Cache-Control": "no-store"})
    if rest.strip("/").startswith("sizechart"):
        return await _sizechart_for(store, request.query_params.get("handle") or "",
                                    request.query_params.get("country") or "")
    parcels, message = _find_parcels(store["id"], order, email, nums)
    if signed and nums and not order:
        # Arrived from a link in a shipping email (the email links carry the tracking number).
        key = f"track_visits:{store['id']}"
        try:
            v = json.loads(db.get_setting(key) or "{}")
        except ValueError:
            v = {}
        v["email_links"] = v.get("email_links", 0) + 1
        v["last"] = datetime.now(timezone.utc).isoformat()
        db.set_setting(key, json.dumps(v))
    dropship = (db.get_setting(f"track_dropship:{store['id']}") or "1") == "1"
    lang = db.get_setting(f"track_lang:{store['id']}") or "en"
    body = trackpage.page(store["name"].strip(), parcels, {"order": order, "email": email}, message, dropship,
                          (query.get("path_prefix") or "/apps/track") if signed else "/proxy/track", lang)
    if signed:
        return Response(body, media_type="application/liquid")
    # Owner preview, outside the store's theme.
    hidden = f'<input type="hidden" name="shop" value="{html.escape(shop_domain)}">'
    body = body.replace('<button type="submit">', hidden + '<button type="submit">', 1).replace(
        "?nums='+encodeURIComponent(n)", f"?shop={shop_domain}&nums='+encodeURIComponent(n)", 1)
    return HTMLResponse(f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
                        f'initial-scale=1"><title>Track your order · {html.escape(store["name"])}</title>'
                        f'<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;margin:0">'
                        f'<p style="background:#fef3c7;margin:0;padding:8px 16px;font-size:13px">Preview: on the store '
                        f'this shows inside its own theme at {html.escape(shop_domain)}/apps/track</p>{body}')


STOREFRONT_Q = """
query Store {
  menus(first: 25) { nodes { id handle title items { id title type url resourceId tags
    items { id title type url resourceId tags items { id title type url resourceId tags } } } } }
  pages(first: 100) { nodes { id title handle isPublished } }
  urlRedirects(first: 250) { nodes { id path target } }
}"""
MENU_UPDATE = """
mutation M($id: ID!, $title: String!, $handle: String, $items: [MenuItemUpdateInput!]!) {
  menuUpdate(id: $id, title: $title, handle: $handle, items: $items) { menu { id } userErrors { field message } }
}"""
PAGE_UPDATE = """
mutation P($id: ID!, $page: PageUpdateInput!) {
  pageUpdate(id: $id, page: $page) { page { id isPublished } userErrors { field message } }
}"""
REDIRECT_CREATE = """
mutation R($r: UrlRedirectInput!) {
  urlRedirectCreate(urlRedirect: $r) { urlRedirect { id } userErrors { field message } }
}"""
TRACK_TITLE = {"en": "Track your order", "es": "Seguir mi pedido", "fr": "Suivre ma commande"}


def _is_track_link(item: dict, track_pages: set) -> bool:
    url = (item.get("url") or "").lower()
    if "/apps/track" in url and "parcel" not in url:
        return False                                 # already ours
    if "parcelpanel" in url or "parcel-panel" in url or "/apps/parcel" in url:
        return True
    return any(url.rstrip("/").endswith(f"/pages/{h}") for h in track_pages)


def _menu_input(items: list, change) -> list:
    out = []
    for it in items:
        new = {"id": it["id"], "title": it["title"], "type": it["type"], "tags": it.get("tags") or [],
               "items": _menu_input(it.get("items") or [], change)}
        if it.get("resourceId"):
            new["resourceId"] = it["resourceId"]
        if it.get("url") and it["type"] == "HTTP":
            new["url"] = it["url"]
        out.append(change(it, new))
    return out


async def _storefront_plan(store: dict, apply: bool) -> dict:
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    data = await client.gql(STOREFRONT_Q)
    menus, pages = data["menus"]["nodes"], data["pages"]["nodes"]
    redirects = {r["path"].lower(): r["target"] for r in data["urlRedirects"]["nodes"]}
    # Only real order-tracking pages (not e.g. a product page with "tracking" in its name).
    track_pages = {p["handle"] for p in pages if re.fullmatch(
        r"(order-)?track(ing)?(-(your|my))?(-order)?|order-tracking|track-order(s)?|seguimiento(-del?-pedido)?"
        r"|rastre(ar|o)(-(tu|mi))?(-pedido)?|suivi(-de)?(-(la|ma|votre))?(-commande)?", p["handle"].lower())}
    lang = db.get_setting(f"track_lang:{store['id']}") or "en"
    actions, errors = [], []

    def walk(items):
        for it in items:
            yield it
            yield from walk(it.get("items") or [])

    has_ours = any("/apps/track" in (it.get("url") or "") and "parcel" not in (it.get("url") or "")
                   for m in menus for it in walk(m["items"]))
    found = False
    for m in menus:
        hits = [it for it in walk(m["items"]) if _is_track_link(it, track_pages)]
        if not hits:
            continue
        found = True
        actions.append(f"Menu '{m['title']}': point " + ", ".join(f"'{h['title']}' ({h.get('url')})" for h in hits)
                       + " to /apps/track")
        if apply:
            def change(it, new):
                if _is_track_link(it, track_pages):
                    new.update(type="HTTP", url="/apps/track")
                    new.pop("resourceId", None)
                return new
            r = await client.gql(MENU_UPDATE, {"id": m["id"], "title": m["title"], "handle": m["handle"],
                                              "items": _menu_input(m["items"], change)})
            errors += [e["message"] for e in r["menuUpdate"]["userErrors"]]
    if not found and not has_ours:
        target = next((m for m in menus if m["handle"] == "footer"), None) or \
            next((m for m in menus if m["handle"] == "main-menu"), None)
        if target:
            actions.append(f"Menu '{target['title']}': add '{TRACK_TITLE[lang]}' → /apps/track")
            if apply:
                items = _menu_input(target["items"], lambda it, new: new)
                items.append({"title": TRACK_TITLE[lang], "type": "HTTP", "url": "/apps/track", "tags": [], "items": []})
                r = await client.gql(MENU_UPDATE, {"id": target["id"], "title": target["title"],
                                                  "handle": target["handle"], "items": items})
                errors += [e["message"] for e in r["menuUpdate"]["userErrors"]]
        else:
            errors.append("No footer or main menu found")
    elif has_ours and not found:
        actions.append("Menu already links to /apps/track")
    for p in pages:
        if p["handle"] not in track_pages:
            continue
        path = f"/pages/{p['handle']}"
        if p["isPublished"]:
            actions.append(f"Page '{p['title']}' ({path}): unpublish")
            if apply:
                r = await client.gql(PAGE_UPDATE, {"id": p["id"], "page": {"isPublished": False}})
                errors += [e["message"] for e in r["pageUpdate"]["userErrors"]]
        if path.lower() not in redirects:
            actions.append(f"Redirect {path} → /apps/track")
            if apply:
                r = await client.gql(REDIRECT_CREATE, {"r": {"path": path, "target": "/apps/track"}})
                errors += [e["message"] for e in r["urlRedirectCreate"]["userErrors"]]
    return {"store": store["name"].strip(), "menus": [m["title"] for m in menus],
            "tracking_pages": sorted(track_pages), "actions": actions, "errors": errors}


REDIRECT_DELETE = """
mutation D($id: ID!) { urlRedirectDelete(id: $id) { deletedUrlRedirectId userErrors { field message } } }"""


async def _storefront_revert(store: dict) -> dict:
    """Undo the switch: menu links back to Parcel Panel, tracking pages republished, our redirects removed."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    data = await client.gql(STOREFRONT_Q)
    menus, pages = data["menus"]["nodes"], data["pages"]["nodes"]
    done, errors = [], []

    def walk(items):
        for it in items:
            yield it
            yield from walk(it.get("items") or [])
    for m in menus:
        ours = [it for it in walk(m["items"]) if (it.get("url") or "").rstrip("/").endswith("/apps/track")]
        if not ours:
            continue
        added = store["id"] in (8, 12)          # Perth Boutique, Beige Rue: the link was new

        def change(it, new):
            if (it.get("url") or "").rstrip("/").endswith("/apps/track"):
                if "seguimiento" in it["title"].lower():
                    new.update(type="HTTP", url="/pages/seguimiento-del-pedido")
                else:
                    new.update(type="HTTP", url="/apps/parcelpanel")
            return new
        items = _menu_input(m["items"], change)
        if added:
            items = [i for i in items if not (i.get("url") == "/apps/parcelpanel" and i["title"] in TRACK_TITLE.values())]
            done.append(f"Menu '{m['title']}': removed the added tracking link")
        else:
            done.append(f"Menu '{m['title']}': {len(ours)} link(s) back to Parcel Panel")
        r = await client.gql(MENU_UPDATE, {"id": m["id"], "title": m["title"], "handle": m["handle"], "items": items})
        errors += [e["message"] for e in r["menuUpdate"]["userErrors"]]
    redirects = (await client.gql('query { urlRedirects(first: 250, query: "target:/apps/track") { nodes { id path target } } }'))
    for rd in redirects["urlRedirects"]["nodes"]:
        if rd["target"].rstrip("/").endswith("/apps/track"):
            r = await client.gql(REDIRECT_DELETE, {"id": rd["id"]})
            errors += [e["message"] for e in r["urlRedirectDelete"]["userErrors"]]
            done.append(f"Removed redirect {rd['path']}")
            handle = rd["path"].rsplit("/", 1)[-1]
            page = next((p for p in pages if p["handle"] == handle and not p["isPublished"]), None)
            if page:
                r = await client.gql(PAGE_UPDATE, {"id": page["id"], "page": {"isPublished": True}})
                errors += [e["message"] for e in r["pageUpdate"]["userErrors"]]
                done.append(f"Republished page {rd['path']}")
    return {"store": store["name"].strip(), "actions": done, "errors": errors}


@app.post("/api/storefront/tracking")
async def api_storefront_tracking(payload: dict):
    """Owner: point each store's menus at the EcomVital tracking page (apply=false shows the plan)."""
    apply = bool(payload.get("apply"))
    only = {int(x) for x in payload.get("stores") or []}
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")
              and (not only or x["id"] in only)]
    out = []
    for x in stores:
        try:
            if payload.get("revert"):
                out.append(await _storefront_revert(x))
                continue
            out.append(await _storefront_plan(x, apply))
        except Exception as e:
            out.append({"store": x["name"].strip(), "actions": [], "errors": [str(e)[:300]]})
    return {"applied": apply, "stores": out}


@app.get("/api/tracking")
async def api_tracking():
    cfg = _tracking_config()
    out = {"connected": bool(cfg["key"]), "stores": cfg["stores"], "from": cfg["from"],
           "push": sorted(_push_stores()),
           "langs": {x["id"]: db.get_setting(f"track_lang:{x['id']}") or "en" for x in db.list_stores()},
           "visits": {x["id"]: json.loads(db.get_setting(f"track_visits:{x['id']}") or "{}") for x in db.list_stores()},
           "dropship": [x["id"] for x in db.list_stores()
                        if (db.get_setting(f"track_dropship:{x['id']}") or "1") == "1"],
           "domains": {x["id"]: x["shop_domain"] for x in db.list_stores()},
           "webhook": f"{BASE_URL}/api/scm/webhook", "status": _scm_status(),
           "store_list": [{"id": k, "name": v} for k, v in _store_names().items()]}
    if cfg["key"]:
        try:
            out["quota"] = await track17.quota(cfg["key"])
        except track17.TrackError as e:
            out["quota_error"] = str(e)
    return out


@app.put("/api/tracking")
async def api_tracking_save(payload: dict):
    if "key" in payload and payload["key"] is not None:
        key = payload["key"].strip()
        if key:
            try:
                await track17.quota(key)
            except track17.TrackError as e:
                raise HTTPException(400, str(e))
        db.set_setting("track17_key", key)
    if "push" in payload:
        on = {int(x) for x in payload["push"] or []}
        for x in db.list_stores():
            was = db.get_setting(f"scm_push:{x['id']}") == "1"
            if x["id"] in on and not was:
                # Only changes from now on: don't email customers about parcels that already moved.
                with db._conn() as con:
                    con.execute("UPDATE shipments SET pushed = status WHERE store_id = ?", (x["id"],))
            db.set_setting(f"scm_push:{x['id']}", "1" if x["id"] in on else "0")
    if isinstance(payload.get("langs"), dict):
        for sid, lang in payload["langs"].items():
            if lang in ("en", "es", "fr"):
                db.set_setting(f"track_lang:{int(sid)}", lang)
    if "dropship" in payload:
        on = {int(x) for x in payload["dropship"] or []}
        for x in db.list_stores():
            db.set_setting(f"track_dropship:{x['id']}", "1" if x["id"] in on else "0")
    if "stores" in payload:
        db.set_setting("track_stores", json.dumps([int(x) for x in payload["stores"] or []]))
    if payload.get("from"):
        try:
            date.fromisoformat(payload["from"])
        except ValueError:
            raise HTTPException(400, "Pick a start date.")
        db.set_setting("track_from", payload["from"])
    if not _scm_running.locked():
        asyncio.create_task(_scm_run())
    return {"ok": True}


# ---------------------------------------------------------------- Product Importer desk

@app.get("/importer", response_class=HTMLResponse)
def importer_page():
    return _page("importer.html", ("importer.js", "product-edit.js", "adcopy.js", "adcopy.css", "nav.js", "styles.css", "skin.css", "chat.js"))


def _importer_cfg() -> dict:
    try:
        saved = json.loads(db.get_setting("importer_cfg") or "{}")
    except ValueError:
        saved = {}
    return {**importer.DEFAULTS, **saved}


@app.get("/api/importer/settings")
def api_importer_settings():
    return {"settings": _importer_cfg(),
            "stores": [{"id": x["id"], "name": x["name"].strip(), "currency": x["currency"], "domain": x["shop_domain"]}
                       for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]}


@app.put("/api/importer/settings")
def api_importer_settings_save(payload: dict):
    cfg = _importer_cfg()
    for k, v in (payload or {}).items():
        if k in importer.DEFAULTS:
            cfg[k] = v
    db.set_setting("importer_cfg", json.dumps(cfg))
    return {"ok": True, "settings": cfg}


_import_running: set = set()


async def _run_import(job_id: str, token_data: dict, ids: list, store_ids: list, user: str):
    cfg = _importer_cfg()
    stores = {x["id"]: x for x in db.list_stores()}
    src_cur = token_data.get("currency")
    tables = {}
    ai_charts = {}      # source product id -> size chart id made by the AI (shared by every target store)
    try:
        for sid in store_ids:
            store = stores.get(sid)
            if not store:
                continue
            client = ShopifyClient(store["shop_domain"], store["access_token"])
            extras = await _store_extras(client, token_data.get("collection") or "")
            factor = 1.0
            if cfg.get("convert_currency") and src_cur and src_cur != store["currency"]:
                if store["currency"] not in tables:
                    tables[store["currency"]] = await fx.table(store["currency"])
                factor = fx.factor(tables[store["currency"]], src_cur, store["currency"])
            for pid in ids:
                p = token_data["raw"].get(str(pid))
                if not p:
                    continue
                src_url = f"https://{token_data['host']}/products/{p.get('handle')}"
                with db._conn() as con:
                    row = con.execute("SELECT id FROM import_items WHERE job_id = ? AND store_id = ? AND source_url = ?",
                                      (job_id, sid, src_url)).fetchone()
                status, err, gid, admin = "failed", None, None, None
                try:
                    if cfg.get("unique") and p.get("handle"):
                        ex = await client.gql(importer.HANDLE_EXISTS, {"q": f"handle:{p['handle']}"})
                        if ex["products"]["nodes"]:
                            status, err = "skipped", "Already in this store (same handle)"
                            gid = ex["products"]["nodes"][0]["id"]
                    if status != "skipped":
                        inp = importer.build_input(p, cfg, factor, src_url)
                        r = await client.gql(importer.PRODUCT_SET, {"input": inp})
                        errs = r["productSet"]["userErrors"]
                        if errs:
                            err = "; ".join(f"{e.get('message')}" for e in errs)[:500]
                        else:
                            gid, status = r["productSet"]["product"]["id"], "done"
                            notes = []
                            if extras["publication"]:
                                pr = await client.gql(importer.PUBLISH, {"id": gid, "input": [
                                    {"publicationId": extras["publication"]}]})
                                if pr["publishablePublish"]["userErrors"]:
                                    notes.append("Not added to Online Store")
                            if extras["collection"]:
                                cr = await client.gql(importer.COLLECTION_ADD, {"id": extras["collection"],
                                                                                "productIds": [gid]})
                                if cr["collectionAddProducts"]["userErrors"]:
                                    notes.append("Not added to the collection")
                            elif extras["error"]:
                                notes.append(extras["error"])
                            sc_note = await _import_size_chart(cfg, p, sid, gid, ai_charts, user)
                            if sc_note:
                                notes.append(sc_note)
                            err = "; ".join(notes) or None
                except Exception as e:
                    err = str(e)[:500]
                if gid:
                    admin = (f"https://admin.shopify.com/store/{store['shop_domain'].removesuffix('.myshopify.com')}"
                             f"/products/{gid.rsplit('/', 1)[-1]}")
                with db._conn() as con:
                    con.execute("UPDATE import_items SET status = ?, error = ?, product_id = ?, admin_url = ?,"
                                " done_at = datetime('now') WHERE id = ?", (status, err, gid, admin, row["id"]))
                await asyncio.sleep(0.4)
    finally:
        _import_running.discard(job_id)


@app.get("/api/importer/collections")
async def api_importer_collections(stores: str = ""):
    """Manual collections in the chosen stores (products can only be added to manual ones), by title."""
    ids = [int(x) for x in stores.split(",") if x.strip().isdigit()]
    all_stores = {x["id"]: x for x in db.list_stores()}

    async def one(sid):
        st = all_stores.get(sid)
        if not st:
            return sid, []
        try:
            r = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(
                importer.COLLECTIONS, {"q": "collection_type:custom"})
            return sid, [c["title"] for c in r["collections"]["nodes"]]
        except Exception:
            return sid, []
    got = await asyncio.gather(*[one(i) for i in ids])
    titles = {}
    for sid, names in got:
        for t in names:
            titles.setdefault(t, []).append(sid)
    return {"collections": [{"title": t, "stores": v} for t, v in sorted(titles.items(), key=lambda kv: kv[0].lower())]}


async def _store_extras(client, title: str) -> dict:
    """Online Store publication id, and the collection with this title (created if missing)."""
    out = {"publication": None, "collection": None, "error": None}
    try:
        pubs = (await client.gql(importer.PUBLICATIONS))["publications"]["nodes"]
        out["publication"] = next((p["id"] for p in pubs if (p.get("name") or "").lower() == "online store"), None)
    except Exception:
        pass
    if title:
        try:
            found = (await client.gql(importer.COLLECTIONS, {"q": f'collection_type:custom title:"{title}"'}))
            hit = next((c for c in found["collections"]["nodes"] if c["title"].strip().lower() == title.strip().lower()), None)
            if hit:
                out["collection"] = hit["id"]
            else:
                r = await client.gql(importer.COLLECTION_CREATE, {"input": {"title": title}})
                errs = r["collectionCreate"]["userErrors"]
                if errs:
                    out["error"] = "Collection: " + "; ".join(e["message"] for e in errs)
                else:
                    out["collection"] = r["collectionCreate"]["collection"]["id"]
                    if out["publication"]:
                        await client.gql(importer.PUBLISH, {"id": out["collection"],
                                                            "input": [{"publicationId": out["publication"]}]})
        except Exception as e:
            out["error"] = f"Collection: {e}"[:200]
    return out


async def _import_size_chart(cfg: dict, p: dict, store_id: int, gid: str, ai_charts: dict, user: str) -> str:
    """Give a freshly imported product its size chart. Returns a note for the history, or ''."""
    mode = cfg.get("size_chart_mode") or "none"
    if mode == "none":
        return ""
    chart_id = None
    if mode == "chart":
        chart_id = int(cfg.get("size_chart_id") or 0) or None
    elif mode == "ai":
        pid = str(p.get("id"))
        if pid not in ai_charts:
            ai_charts[pid] = None
            key = db.get_setting("anthropic_api_key")
            imgs = importer.size_chart_images(p)
            if key and imgs:
                lang = LANG_NAME.get(db.get_setting(f"track_lang:{store_id}") or "en", "English")
                try:
                    res = await sizecharts.read_images(key, [("url", u) for u in imgs], lang, "cm")
                    if res.get("found"):
                        ai_charts[pid] = _sc_save({"name": p.get("title") or "Size chart", "status": "active",
                                                   "blocks": sizecharts.blocks_from_ai(res), "source": "ai-import"},
                                                  None, user)
                except Exception as e:
                    return f"Size chart: {e}"[:150]
        chart_id = ai_charts.get(pid)
        if not chart_id:
            return "No size chart found in the source"
    if not chart_id:
        return ""
    with db._conn() as con:
        con.execute("INSERT INTO size_chart_rules (chart_id, store_id, kind, value, label) VALUES (?, ?, 'product', ?, ?)",
                    (chart_id, store_id, gid, p.get("title")))
    _sc_cache.clear()
    return ""


@app.post("/api/importer/import")
async def api_importer_import(payload: dict, request: Request):
    """The browser loads the source store's products (Shopify blocks servers from doing
    that) and sends the chosen ones here with the source host and currency."""
    host = re.sub(r"[^a-z0-9.\-]", "", (payload.get("host") or "").lower())[:200]
    currency = (payload.get("currency") or "").upper()[:3] or None
    products = [p for p in (payload.get("products") or []) if isinstance(p, dict) and p.get("id") and p.get("title")][:500]
    store_ids = [int(x) for x in payload.get("stores") or []]
    if not products:
        raise HTTPException(400, "Pick at least one product.")
    if not store_ids:
        raise HTTPException(400, "Pick at least one store to import into.")
    data = {"host": host, "currency": currency, "raw": {str(p["id"]): p for p in products},
            "collection": (payload.get("collection") or "").strip()[:255]}
    ids = list(data["raw"])
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    job_id = secrets.token_hex(6)
    with db._conn() as con:
        for sid in store_ids:
            for pid in ids:
                p = data["raw"][pid]
                imgs = p.get("images") or []
                con.execute("INSERT INTO import_items (job_id, source_url, title, image, store_id, created_by)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (job_id, f"https://{host}/products/{p.get('handle')}", p.get("title"),
                             imgs[0].get("src") if imgs else None, sid, user))
    _import_running.add(job_id)
    asyncio.create_task(_run_import(job_id, data, ids, store_ids, user))
    return {"job": job_id, "items": len(ids) * len(store_ids)}


@app.get("/api/importer/jobs/{job_id}")
def api_importer_job(job_id: str):
    names = _store_names()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM import_items WHERE job_id = ? ORDER BY id", (job_id,))]
    for r in rows:
        r["store"] = names.get(r["store_id"], "")
    return {"items": rows, "running": job_id in _import_running}


@app.get("/api/importer/history")
def api_importer_history(page: int = 1):
    names = _store_names()
    with db._conn() as con:
        total = con.execute("SELECT COUNT(*) FROM import_items").fetchone()[0]
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM import_items ORDER BY id DESC LIMIT 100 OFFSET ?", ((max(1, page) - 1) * 100,))]
    for r in rows:
        r["store"] = names.get(r["store_id"], "")
    return {"items": rows, "total": total, "page": page, "pages": max(1, -(-total // 100))}


# ---------------------------------------------------------------- iPhone widget (Scriptable)

# key, label, format, which way is good ("up" gets colour; costs stay neutral)
WIDGET_METRICS = [("sales", "Sales", "money", "up"), ("ad_spend", "Ad spend", "money", ""),
                  ("roas", "ROAS", "ratio", "up"), ("cogs", "COG", "money", ""),
                  ("net_profit", "Net profit", "money", "up"), ("net_margin", "Net margin", "pct", "up"),
                  ("processing_fee", "Fees", "money", ""), ("orders", "Orders", "int", "up"),
                  ("google_spend", "Google ads", "money", ""), ("meta_spend", "Meta ads", "money", ""),
                  ("aov", "Avg order", "money", "up")]
WIDGET_PERIODS = {"today": "Today", "yesterday": "Yesterday", "7": "Last 7 days", "30": "Last 30 days", "mtd": "This month"}
_widget_cache: dict = {}        # token -> (time, payload)


def _widget_row(r) -> dict:
    d = dict(r)
    d["metrics"] = json.loads(d["metrics"] or "[]")
    d["url"] = f"{BASE_URL}/w/{d['token']}"
    return d


def _widget_clean(payload: dict) -> dict:
    keys = {k for k, *_ in WIDGET_METRICS}
    metrics = [m for m in payload.get("metrics") or [] if m in keys][:10]
    if not metrics:
        raise HTTPException(400, "Pick at least one figure to show.")
    period = payload.get("period") if payload.get("period") in WIDGET_PERIODS else "today"
    return {"name": (payload.get("name") or "").strip()[:60] or "EcomVital", "scope": str(payload.get("scope") or "all"),
            "period": period, "metrics": json.dumps(metrics)}


@app.get("/api/widgets")
def api_widgets():
    with db._conn() as con:
        rows = [_widget_row(r) for r in con.execute("SELECT * FROM widgets ORDER BY id")]
    return {"widgets": rows, "metrics": [{"key": k, "label": l} for k, l, *_ in WIDGET_METRICS],
            "periods": [{"key": k, "label": v} for k, v in WIDGET_PERIODS.items()]}


@app.post("/api/widgets")
def api_widget_create(payload: dict, request: Request):
    w = _widget_clean(payload)
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    with db._conn() as con:
        con.execute("INSERT INTO widgets (token, name, scope, period, metrics, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(24), w["name"], w["scope"], w["period"], w["metrics"], user))
    return api_widgets()


@app.put("/api/widgets/{wid:int}")
def api_widget_update(wid: int, payload: dict):
    w = _widget_clean(payload)
    with db._conn() as con:
        con.execute("UPDATE widgets SET name = ?, scope = ?, period = ?, metrics = ? WHERE id = ?",
                    (w["name"], w["scope"], w["period"], w["metrics"], wid))
        tok = con.execute("SELECT token FROM widgets WHERE id = ?", (wid,)).fetchone()
    if tok:
        _widget_cache.pop(tok[0], None)
    return api_widgets()


@app.delete("/api/widgets/{wid:int}")
def api_widget_delete(wid: int):
    """Turning a widget off kills its link straight away."""
    with db._conn() as con:
        tok = con.execute("SELECT token FROM widgets WHERE id = ?", (wid,)).fetchone()
        con.execute("DELETE FROM widgets WHERE id = ?", (wid,))
    if tok:
        _widget_cache.pop(tok[0], None)
    return api_widgets()


@app.get("/api/widgets/{wid:int}/script")
def api_widget_script(wid: int):
    with db._conn() as con:
        r = con.execute("SELECT * FROM widgets WHERE id = ?", (wid,)).fetchone()
    if not r:
        raise HTTPException(404, "Widget not found.")
    w = _widget_row(r)
    with open(os.path.join(STATIC_DIR, "widget-scriptable.js")) as f:
        code = f.read()
    code = code.replace("__WIDGET_URL__", w["url"]).replace("__APP_URL__", BASE_URL).replace("__WIDGET_NAME__", w["name"])
    return {"name": w["name"], "script": code}


@app.get("/w/{token}")
async def widget_data(token: str):
    """The widget's figures. Read-only; the link itself is the key (turn it off in Settings)."""
    with db._conn() as con:
        r = con.execute("SELECT * FROM widgets WHERE token = ?", (token,)).fetchone()
        if r:
            con.execute("UPDATE widgets SET last_used = datetime('now') WHERE id = ?", (r["id"],))
    if not r:
        return JSONResponse({"error": "This widget was turned off in EcomVital."}, status_code=404)
    hit = _widget_cache.get(token)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    w = _widget_row(r)
    stores = db.list_stores()
    if w["scope"].startswith("g"):
        g = next((g for g in db.list_groups() if f"g{g['id']}" == w["scope"]), None)
        pool = [x for x in stores if g and x["id"] in g["store_ids"]]
    elif w["scope"] != "all":
        pool = [x for x in stores if str(x["id"]) == w["scope"]]
    else:
        pool = stores
    if not pool:
        return JSONResponse({"error": "The store or group for this widget no longer exists."}, status_code=404)
    today = max(_store_today(x) for x in pool)          # the furthest-ahead store's date
    p = w["period"]
    if p == "today":
        start = end = today
    elif p == "yesterday":
        start = end = today - timedelta(days=1)
    elif p == "mtd":
        start, end = today.replace(day=1), today
    else:
        start, end = today - timedelta(days=int(p) - 1), today
    try:
        d = await api_dashboard(scope=w["scope"], start=start.isoformat(), end=end.isoformat(),
                                compare="same_time" if p == "today" else "period")
    except HTTPException as e:
        return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)
    info = {k: (l, f, good) for k, l, f, good in WIDGET_METRICS}
    payload = {
        "name": w["name"], "title": d["title"], "period": WIDGET_PERIODS[p], "currency": d["currency"],
        "range": d["range"], "compare": "vs yesterday at this time" if p == "today" else "vs previous period",
        "items": [{"key": k, "label": info[k][0], "format": info[k][1], "good": info[k][2],
                   "value": d["totals"].get(k), "delta": (d.get("delta") or {}).get(k)} for k in w["metrics"]],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "open_url": (f"{BASE_URL}/?" + "&".join(x for x in (f"store={w['scope']}" if w["scope"] != "all" else "",
                                                            f"range={p}" if p != "today" else "") if x)).rstrip("?"),
    }
    _widget_cache[token] = (time.time(), payload)
    return payload


# ---------------------------------------------------------------- Importer: edit a product in EcomVital

def _imp_store(store_id) -> dict:
    store = next((x for x in db.list_stores() if x["id"] == int(store_id or 0)), None)
    if not store:
        raise HTTPException(404, "Store not found.")
    return store


def _imp_gid(pid: str) -> str:
    pid = str(pid or "")
    if not re.fullmatch(r"gid://shopify/Product/\d+", pid):
        raise HTTPException(400, "Unknown product.")
    return pid


async def _imp_gql(client, query: str, variables: dict, key: str, err_key: str = "userErrors") -> dict:
    try:
        data = (await client.gql(query, variables))[key]
    except ShopifyError as e:
        raise HTTPException(400, str(e)[:300])
    msg = importer.errors(data, err_key)
    if msg:
        raise HTTPException(400, msg[:300])
    return data


@app.get("/api/importer/product")
async def api_importer_product(store: int, id: str):
    st = _imp_store(store)
    gid = _imp_gid(id)
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    try:
        p = (await client.gql(importer.PRODUCT_GET, {"id": gid}))["product"]
    except ShopifyError as e:
        raise HTTPException(400, str(e)[:300])
    if not p:
        raise HTTPException(404, "This product is no longer in Shopify.")
    with db._conn() as con:
        r = con.execute("SELECT chart_id FROM size_chart_rules WHERE store_id = ? AND kind = 'product' AND value = ?",
                        (st["id"], gid)).fetchone()
        charts = [dict(x) for x in con.execute("SELECT id, name, status FROM size_charts ORDER BY name COLLATE NOCASE")]
    lang = db.get_setting(f"track_lang:{st['id']}") or "en"
    return {"product": p, "size_chart_id": r[0] if r else 0, "charts": charts, "copies": _imp_copies(st["id"], gid),
            "store": {"id": st["id"], "name": st["name"].strip(), "currency": st.get("currency"), "lang": lang,
                      "language": LANG_NAME.get(lang, "English")},
            "admin_url": f"https://{st['shop_domain']}/admin/products/{gid.rsplit('/', 1)[1]}"}


def _imp_copies(store_id: int, gid: str) -> list:
    """The same source product imported into other stores, with the size chart each copy has now."""
    names = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        src = [r[0] for r in con.execute("SELECT DISTINCT source_url FROM import_items WHERE store_id = ? AND product_id = ?"
                                         " AND source_url IS NOT NULL AND source_url != ''", (store_id, gid))]
        out = {}
        for u in src:
            for sid, pid in con.execute("SELECT store_id, product_id FROM import_items WHERE source_url = ? AND status = 'done'"
                                        " AND product_id IS NOT NULL AND NOT (store_id = ? AND product_id = ?)", (u, store_id, gid)):
                if sid in names and (sid, pid) not in out:
                    r = con.execute("SELECT chart_id FROM size_chart_rules WHERE store_id = ? AND kind = 'product' AND value = ?",
                                    (sid, pid)).fetchone()
                    out[(sid, pid)] = {"store": sid, "store_name": names[sid], "product_id": pid, "chart_id": r[0] if r else 0}
    return sorted(out.values(), key=lambda c: c["store_name"])


@app.get("/api/importer/charts")
def api_importer_charts():
    """Size charts to pick from in the Product Importer (people with importer access, no Size Charts desk needed)."""
    with db._conn() as con:
        return {"charts": [dict(x) for x in con.execute(
            "SELECT id, name, status FROM size_charts ORDER BY name COLLATE NOCASE")]}


@app.put("/api/importer/product")
async def api_importer_product_save(payload: dict):
    st = _imp_store(payload.get("store"))
    gid = _imp_gid(payload.get("id"))
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    prod = {"id": gid}
    for k, f in (("title", "title"), ("description_html", "descriptionHtml"), ("product_type", "productType"),
                 ("vendor", "vendor")):
        if k in payload:
            prod[f] = str(payload[k] or "")
    if "tags" in payload:
        prod["tags"] = [t.strip() for t in payload["tags"] if str(t).strip()][:250]
    if payload.get("status") in ("ACTIVE", "DRAFT", "ARCHIVED"):
        prod["status"] = payload["status"]
    if prod.get("title") == "":
        raise HTTPException(400, "The title can't be empty.")
    if len(prod) > 1:
        await _imp_gql(client, importer.PRODUCT_UPDATE, {"product": prod}, "productUpdate")
    variants = []
    for v in payload.get("variants") or []:
        if not str(v.get("id", "")).startswith("gid://shopify/ProductVariant/"):
            continue
        try:
            price = round(float(v["price"]), 2)
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, f"Check the price of {v.get('title') or 'a variant'}.")
        cmp_ = v.get("compare_at")
        cmp_ = round(float(cmp_), 2) if cmp_ not in (None, "", 0, "0") else None
        variants.append({"id": v["id"], "price": f"{price:.2f}", "compareAtPrice": f"{cmp_:.2f}" if cmp_ else None})
    if variants:
        await _imp_gql(client, importer.VARIANTS_UPDATE, {"productId": gid, "variants": variants}, "productVariantsBulkUpdate")
    # option names and values ("Size" / "S" -> "Small"): renaming a value renames it on every variant that has it
    if payload.get("options"):
        now = (await client.gql(importer.PRODUCT_GET, {"id": gid}))["product"]
        have = {o["id"]: o for o in (now or {}).get("options") or []}
        for o in payload["options"]:
            cur = have.get(o.get("id"))
            if not cur:
                continue
            name = str(o.get("name") or "").strip()[:255]
            old_vals = {v["id"]: v["name"] for v in cur.get("optionValues") or []}
            vals = [{"id": v["id"], "name": str(v.get("name") or "").strip()[:255]} for v in o.get("values") or []
                    if v.get("id") in old_vals and str(v.get("name") or "").strip() and str(v["name"]).strip() != old_vals[v["id"]]]
            if not name:
                raise HTTPException(400, "An option name can't be empty.")
            seen = [v["name"].lower() for v in vals] + [n.lower() for i, n in old_vals.items() if i not in {v["id"] for v in vals}]
            if len(seen) != len(set(seen)):
                raise HTTPException(400, f"Two values of “{name}” would have the same name.")
            if name != cur["name"] or vals:
                await _imp_gql(client, importer.OPTION_UPDATE, {"productId": gid, "option": {"id": cur["id"], "name": name},
                                                                "values": vals or None}, "productOptionUpdate")
    if "size_chart_id" in payload:
        cid = int(payload.get("size_chart_id") or 0)
        title = prod.get("title") or payload.get("title") or ""
        # this product, plus its copies in the other stores that were ticked
        targets = [(st["id"], gid)] + [(int(c["store"]), str(c["product_id"])) for c in payload.get("chart_copies") or []
                                       if c.get("store") and str(c.get("product_id", "")).startswith("gid://shopify/Product/")]
        allowed = {(c["store"], c["product_id"]) for c in _imp_copies(st["id"], gid)}
        with db._conn() as con:
            for sid, pid in targets:
                if (sid, pid) != (st["id"], gid) and (sid, pid) not in allowed:
                    continue
                con.execute("DELETE FROM size_chart_rules WHERE store_id = ? AND kind = 'product' AND value = ?", (sid, pid))
                if cid:
                    con.execute("INSERT INTO size_chart_rules (chart_id, store_id, kind, value, label) VALUES (?, ?, 'product', ?, ?)",
                                (cid, sid, pid, title))
        _sc_cache.clear()
    if prod.get("title"):
        with db._conn() as con:
            con.execute("UPDATE import_items SET title = ? WHERE product_id = ? AND store_id = ?", (prod["title"], gid, st["id"]))
    return await api_importer_product(st["id"], gid)


@app.post("/api/importer/product/media")
async def api_importer_media_add(request: Request):
    """Add images: uploaded files (via Shopify's staged upload) and/or web addresses."""
    form = await request.form()
    st = _imp_store(form.get("store"))
    gid = _imp_gid(form.get("id"))
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    media = []
    files = [f for f in form.getlist("files") if hasattr(f, "read")]
    for f in files[:20]:
        data = await f.read()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(400, f"{f.filename} is bigger than 20 MB.")
        mt = f.content_type if (f.content_type or "").startswith("image/") else "image/jpeg"
        t = await _imp_gql(client, importer.STAGED_UPLOAD, {"input": [{
            "filename": f.filename or "image.jpg", "mimeType": mt, "resource": "IMAGE", "httpMethod": "POST",
            "fileSize": str(len(data))}]}, "stagedUploadsCreate")
        target = t["stagedTargets"][0]
        async with httpx.AsyncClient(timeout=120) as http:
            up = await http.post(target["url"], data={x["name"]: x["value"] for x in target["parameters"]},
                                 files={"file": (f.filename or "image.jpg", data, mt)})
        if up.status_code >= 300:
            raise HTTPException(400, f"Shopify didn't accept {f.filename} ({up.status_code}).")
        media.append({"originalSource": target["resourceUrl"], "mediaContentType": "IMAGE"})
    for u in (form.get("urls") or "").split():
        if u.startswith("http"):
            media.append({"originalSource": u, "mediaContentType": "IMAGE"})
    if not media:
        raise HTTPException(400, "Choose an image or paste an image link.")
    await _imp_gql(client, importer.PRODUCT_UPDATE, {"product": {"id": gid}, "media": media}, "productUpdate")
    return {"added": len(media)}


@app.post("/api/importer/product/media/remove")
async def api_importer_media_remove(payload: dict):
    st = _imp_store(payload.get("store"))
    gid = _imp_gid(payload.get("id"))
    ids = [m for m in payload.get("media_ids") or [] if str(m).startswith("gid://shopify/")]
    if not ids:
        raise HTTPException(400, "No image chosen.")
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    await _imp_gql(client, importer.MEDIA_REMOVE, {"files": [{"id": m, "referencesToRemove": [gid]} for m in ids]}, "fileUpdate")
    return {"removed": len(ids)}


@app.post("/api/importer/product/media/move")
async def api_importer_media_move(payload: dict):
    st = _imp_store(payload.get("store"))
    gid = _imp_gid(payload.get("id"))
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    await _imp_gql(client, importer.MEDIA_REORDER, {"id": gid, "moves": [
        {"id": payload.get("media_id"), "newPosition": str(max(0, int(payload.get("position") or 0)))}]},
        "productReorderMedia", "mediaUserErrors")
    return {"ok": True}


@app.post("/api/importer/product/rewrite")
async def api_importer_rewrite(payload: dict):
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    try:
        return await importer.rewrite(key, payload.get("title") or "", payload.get("description_html") or "", payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/importer/product/chart")
def api_importer_product_chart(store: int, id: str):
    gid = _imp_gid(id)
    with db._conn() as con:
        r = con.execute("SELECT chart_id FROM size_chart_rules WHERE store_id = ? AND kind = 'product' AND value = ?",
                        (int(store), gid)).fetchone()
        charts = [dict(x) for x in con.execute("SELECT id, name, status FROM size_charts ORDER BY name COLLATE NOCASE")]
    return {"size_chart_id": r[0] if r else 0, "charts": charts}


@app.post("/api/importer/product/size-chart-ai")
async def api_importer_size_chart_ai(request: Request):
    """Read size-chart images (ticked product photos, or uploaded files) into a size chart
    and assign it to this product."""
    images = []
    if (request.headers.get("content-type") or "").startswith("multipart/"):
        form = await request.form()
        payload = {"store": form.get("store"), "id": form.get("id"), "name": form.get("name")}
        for f in form.getlist("files")[:6]:
            if hasattr(f, "read"):
                data = await f.read()
                if len(data) > 8 * 1024 * 1024:
                    raise HTTPException(400, f"{f.filename} is bigger than 8 MB.")
                mt = f.content_type if f.content_type in ("image/png", "image/jpeg", "image/webp", "image/gif") else "image/jpeg"
                images.append((mt, data))
    else:
        payload = await request.json()
        images = [("url", u) for u in payload.get("urls") or [] if str(u).startswith("http")][:6]
    st = _imp_store(payload.get("store"))
    gid = _imp_gid(payload.get("id"))
    if not images:
        raise HTTPException(400, "Choose the size chart image(s).")
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    lang = LANG_NAME.get(db.get_setting(f"track_lang:{st['id']}") or "en", "English")
    try:
        res = await sizecharts.read_images(key, images, lang, "cm")
    except sizecharts.AIError as e:
        raise HTTPException(400, str(e))
    if not res.get("found"):
        raise HTTPException(400, "The AI didn't find a size chart in those images.")
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    name = (payload.get("name") or "").strip() or res.get("title") or "Size chart"
    cid = _sc_save({"name": name, "status": "active", "blocks": sizecharts.blocks_from_ai(res), "source": "ai-import",
                    "rules": [{"store_id": st["id"], "kind": "product", "value": gid, "label": payload.get("name") or ""}]},
                   None, user)
    return {"id": cid, "name": name}


# ---------------------------------------------------------------- Size Charts desk

@app.get("/sizecharts", response_class=HTMLResponse)
def sizecharts_page():
    return _page("sizecharts.html", ("sizecharts.js", "garments.js", "nav.js", "styles.css", "skin.css", "chat.js"))


SC_DEFAULTS = {"enabled": False, "display": "button", "label": "", "prefix": "", "suffix": "", "icon": "ruler",
               "font_size": 14, "color": "#111111", "bg": "#ffffff", "border": "#d0d0d0", "bold": True,
               "underline": False, "align": "left", "position": "size_label", "advisor": True, "accent": "#111111"}
SC_TEXT = {
    "en": {"label": "Size chart", "advisor": "Find my size", "height": "Height", "weight": "Weight", "fit": "Fit",
           "snug": "Snug", "regular": "Regular", "loose": "Loose", "body": "Body type", "slim": "Slim",
           "average": "Average", "curvy": "Curvy", "athletic": "Athletic", "go": "Get my size",
           "rec": "We recommend", "again": "Try again", "busy": "Working it out…", "chart": "Size chart"},
    "es": {"label": "Guía de tallas", "advisor": "Encuentra tu talla", "height": "Altura", "weight": "Peso",
           "fit": "Ajuste", "snug": "Ceñido", "regular": "Normal", "loose": "Holgado", "body": "Tipo de cuerpo",
           "slim": "Delgado", "average": "Medio", "curvy": "Con curvas", "athletic": "Atlético",
           "go": "Ver mi talla", "rec": "Te recomendamos", "again": "Probar otra vez", "busy": "Calculando…",
           "chart": "Guía de tallas"},
    "fr": {"label": "Guide des tailles", "advisor": "Trouver ma taille", "height": "Taille (cm)", "weight": "Poids",
           "fit": "Coupe", "snug": "Ajustée", "regular": "Normale", "loose": "Ample", "body": "Morphologie",
           "slim": "Mince", "average": "Moyenne", "curvy": "Pulpeuse", "athletic": "Athlétique",
           "go": "Voir ma taille", "rec": "Nous recommandons", "again": "Recommencer", "busy": "Calcul en cours…",
           "chart": "Guide des tailles"},
}
LANG_NAME = {"en": "English", "es": "Spanish", "fr": "French"}


def _sc_cfg(store_id: int) -> dict:
    try:
        saved = json.loads(db.get_setting(f"sizechart_cfg:{store_id}") or "{}")
    except ValueError:
        saved = {}
    return {**SC_DEFAULTS, **saved}


def _sc_row(r) -> dict:
    d = dict(r)
    d["blocks"] = json.loads(d.get("blocks") or "[]")
    return d


def _sc_rules(chart_ids: list) -> dict:
    if not chart_ids:
        return {}
    out = {}
    with db._conn() as con:
        for r in con.execute(f"SELECT * FROM size_chart_rules WHERE chart_id IN ({','.join('?' * len(chart_ids))})"
                             " ORDER BY id", chart_ids):
            out.setdefault(r["chart_id"], []).append(dict(r))
    return out


@app.get("/api/sizecharts")
def api_sizecharts(q: str = "", status: str = "", store: str = "", page: int = 1, brief: int = 0):
    where, params = ["1=1"], []
    if q:
        where.append("lower(c.name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if status in ("active", "draft"):
        where.append("c.status = ?")
        params.append(status)
    if store.isdigit():
        where.append("EXISTS (SELECT 1 FROM size_chart_rules r WHERE r.chart_id = c.id AND r.store_id = ?)")
        params.append(int(store))
    w = " AND ".join(where)
    with db._conn() as con:
        if brief:
            rows = [dict(r) for r in con.execute(f"SELECT c.id, c.name, c.status FROM size_charts c WHERE {w}"
                                                 " ORDER BY c.name LIMIT 2000", params)]
            return {"charts": rows}
        total = con.execute(f"SELECT COUNT(*) FROM size_charts c WHERE {w}", params).fetchone()[0]
        rows = [dict(r) for r in con.execute(
            f"SELECT c.id, c.name, c.status, c.countries, c.source, c.updated_at FROM size_charts c WHERE {w}"
            " ORDER BY c.updated_at DESC, c.id DESC LIMIT 50 OFFSET ?", (*params, max(0, page - 1) * 50))]
    rules = _sc_rules([r["id"] for r in rows])
    names = _store_names()
    for r in rows:
        rs = rules.get(r["id"], [])
        r["rules"] = len(rs)
        summary = {}
        for x in rs:
            summary.setdefault(names.get(x["store_id"], "?"), []).append(x["kind"])
        r["applies"] = [{"store": k, "count": len(v), "kinds": sorted(set(v))} for k, v in summary.items()]
    return {"charts": rows, "total": total, "page": page, "pages": max(1, -(-total // 50)),
            "stores": [{"id": k, "name": v, "lang": db.get_setting(f"track_lang:{k}") or "en"} for k, v in names.items()],
            "templates": [{"key": k, "name": v[0]} for k, v in sizecharts.TEMPLATES.items()]}


@app.get("/api/sizecharts/{chart_id:int}")
def api_sizechart(chart_id: int):
    with db._conn() as con:
        r = con.execute("SELECT * FROM size_charts WHERE id = ?", (chart_id,)).fetchone()
    if not r:
        raise HTTPException(404, "That size chart no longer exists.")
    c = _sc_row(r)
    c["rules"] = _sc_rules([chart_id]).get(chart_id, [])
    return c


def _sc_save(payload: dict, chart_id: int = None, user: str = "") -> int:
    name = (payload.get("name") or "").strip()[:200] or "Size chart"
    status = payload.get("status") if payload.get("status") in ("active", "draft") else "draft"
    blocks = sizecharts.normalise(payload.get("blocks"))
    countries = ",".join(sorted({c.strip().upper()[:2] for c in re.split(r"[,\s]+", payload.get("countries") or "") if c.strip()}))
    with db._conn() as con:
        if chart_id:
            con.execute("UPDATE size_charts SET name = ?, status = ?, blocks = ?, countries = ?, updated_at = datetime('now')"
                        " WHERE id = ?", (name, status, json.dumps(blocks), countries, chart_id))
        else:
            chart_id = con.execute("INSERT INTO size_charts (name, status, blocks, countries, source, created_by)"
                                   " VALUES (?, ?, ?, ?, ?, ?)", (name, status, json.dumps(blocks), countries,
                                                                    payload.get("source") or "manual", user)).lastrowid
        if "rules" in payload:
            con.execute("DELETE FROM size_chart_rules WHERE chart_id = ?", (chart_id,))
            for x in payload.get("rules") or []:
                if x.get("kind") not in ("product", "collection", "tag", "type", "vendor", "all") or not x.get("store_id"):
                    continue
                if x["kind"] == "product" and x.get("value"):     # a product shows one chart
                    con.execute("DELETE FROM size_chart_rules WHERE store_id = ? AND kind = 'product' AND value = ?",
                                (int(x["store_id"]), str(x["value"])[:300]))
                con.execute("INSERT INTO size_chart_rules (chart_id, store_id, kind, value, label) VALUES (?, ?, ?, ?, ?)",
                            (chart_id, int(x["store_id"]), x["kind"], str(x.get("value") or "")[:300],
                             str(x.get("label") or "")[:300]))
    _sc_cache.clear()
    return chart_id


@app.post("/api/sizecharts")
def api_sizechart_create(payload: dict, request: Request):
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    return {"id": _sc_save(payload, None, user)}


@app.put("/api/sizecharts/{chart_id:int}")
def api_sizechart_update(chart_id: int, payload: dict):
    return {"id": _sc_save(payload, chart_id)}


@app.delete("/api/sizecharts/{chart_id:int}")
def api_sizechart_delete(chart_id: int):
    with db._conn() as con:
        con.execute("DELETE FROM size_chart_rules WHERE chart_id = ?", (chart_id,))
        con.execute("DELETE FROM size_charts WHERE id = ?", (chart_id,))
    _sc_cache.clear()
    return {"ok": True}


@app.post("/api/sizecharts/{chart_id:int}/duplicate")
def api_sizechart_duplicate(chart_id: int, request: Request):
    c = api_sizechart(chart_id)
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    return {"id": _sc_save({"name": c["name"] + " (copy)", "status": "draft", "blocks": c["blocks"],
                             "countries": c["countries"], "source": c.get("source")}, None, user)}


@app.post("/api/sizecharts/bulk")
def api_sizechart_bulk(payload: dict):
    ids = [int(i) for i in payload.get("ids") or []]
    action = payload.get("action")
    if not ids:
        raise HTTPException(400, "Pick at least one chart.")
    with db._conn() as con:
        marks = ",".join("?" * len(ids))
        if action in ("active", "draft"):
            con.execute(f"UPDATE size_charts SET status = ?, updated_at = datetime('now') WHERE id IN ({marks})",
                        (action, *ids))
        elif action == "delete":
            con.execute(f"DELETE FROM size_chart_rules WHERE chart_id IN ({marks})", ids)
            con.execute(f"DELETE FROM size_charts WHERE id IN ({marks})", ids)
        else:
            raise HTTPException(400, "Unknown action.")
    _sc_cache.clear()
    return {"ok": True}


@app.get("/api/sizecharts/templates")
def api_sizechart_templates():
    return {k: sizecharts.template(k) for k in sizecharts.TEMPLATES}


@app.post("/api/sizecharts/preview")
def api_sizechart_preview(payload: dict):
    blocks = sizecharts.normalise(payload.get("blocks"))
    return {"html": sizecharts.render(blocks), "html_in": sizecharts.render(blocks, True) if sizecharts.has_cm(blocks) else None,
            "blocks": blocks}


@app.get("/api/sizecharts/export")
def api_sizechart_export(ids: str = ""):
    from fastapi.responses import Response
    want = [int(i) for i in ids.split(",") if i.strip().isdigit()]
    with db._conn() as con:
        q = "SELECT * FROM size_charts" + (f" WHERE id IN ({','.join('?' * len(want))})" if want else "")
        rows = [_sc_row(r) for r in con.execute(q, want)]
    rules = _sc_rules([r["id"] for r in rows])
    out = [{"name": r["name"], "status": r["status"], "countries": r["countries"], "blocks": r["blocks"],
            "rules": [{k: x[k] for k in ("store_id", "kind", "value", "label")} for x in rules.get(r["id"], [])]}
           for r in rows]
    return Response(json.dumps({"profitdesk_size_charts": out}, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="size-charts-{date.today()}.json"'})


async def _all_products(store) -> list:
    """Every product's id, title and handle in a store (for matching imported charts)."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    out, cursor = [], None
    for _ in range(100):
        r = await client.gql("""query P($c: String) { products(first: 250, after: $c) {
            pageInfo { hasNextPage endCursor } nodes { id title handle } } }""", {"c": cursor})
        out += r["products"]["nodes"]
        if not r["products"]["pageInfo"]["hasNextPage"]:
            break
        cursor = r["products"]["pageInfo"]["endCursor"]
    return out


@app.post("/api/sizecharts/import")
async def api_sizechart_import(payload: dict, request: Request):
    """A Panda / Kiwi / EcomVital export. Charts named after a product are linked to that
    product in the chosen store (Panda's export drops the links)."""
    data = payload.get("data")
    store_id = int(payload.get("store_id") or 0)
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    if isinstance(data, dict) and "profitdesk_size_charts" in data:
        n = 0
        for c in data["profitdesk_size_charts"]:
            _sc_save({**c, "source": "import"}, None, user)
            n += 1
        return {"imported": n, "linked": 0}
    charts = sizecharts.parse_export(data)
    if not charts:
        raise HTTPException(400, "No size charts found in that file.")
    by_title = {}
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if store:
        for p in await _all_products(store):
            by_title.setdefault(sizecharts.norm_title(p["title"]), p)
    imported = linked = 0
    for c in charts:
        p = by_title.get(sizecharts.norm_title(c["name"]))
        rules = [{"store_id": store_id, "kind": "product", "value": p["id"], "label": p["title"]}] if p else []
        _sc_save({"name": c["name"], "status": "active" if p else "draft", "blocks": c["blocks"],
                  "rules": rules, "source": payload.get("source") or "panda"}, None, user)
        imported += 1
        linked += bool(p)
    return {"imported": imported, "linked": linked, "unlinked": imported - linked}


@app.post("/api/sizecharts/from-image")
async def api_sizechart_from_image(request: Request):
    """Staff upload size-chart image(s); Claude reads them into tables (draft, not saved yet)."""
    form = await request.form()
    images = []
    for f in form.getlist("images"):
        if hasattr(f, "read"):
            data = await f.read()
            if len(data) > 8 * 1024 * 1024:
                raise HTTPException(400, f"{f.filename} is bigger than 8 MB.")
            mt = f.content_type if f.content_type in ("image/png", "image/jpeg", "image/webp", "image/gif") else "image/jpeg"
            images.append((mt, data))
    for u in (form.get("urls") or "").split():
        if u.startswith("http"):
            images.append(("url", u))
    if not images:
        raise HTTPException(400, "Add at least one image.")
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    lang = form.get("language") or "English"
    try:
        res = await sizecharts.read_images(key, images, lang, form.get("unit", "cm"))
    except sizecharts.AIError as e:
        raise HTTPException(400, str(e))
    if not res.get("found"):
        raise HTTPException(400, "The AI didn't find a size chart in those images.")
    blocks = sizecharts.blocks_from_ai(res)
    return {"name": res.get("title") or "Size chart", "blocks": blocks}


@app.get("/api/sizecharts/products")
async def api_sizechart_products(store: int, q: str = ""):
    st = next((x for x in db.list_stores() if x["id"] == store), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    term = re.sub(r"[\"'\\\\]", " ", q).strip()
    r = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(
        """query S($q: String) { products(first: 25, query: $q, sortKey: TITLE) {
            nodes { id title handle featuredMedia { preview { image { url } } } } } }""",
        {"q": f"title:*{term}*" if term else None})
    return {"products": [{"id": p["id"], "title": p["title"], "handle": p["handle"],
                          "image": ((((p.get("featuredMedia") or {}).get("preview") or {}).get("image") or {}).get("url"))}
                         for p in r["products"]["nodes"]]}


@app.get("/api/sizecharts/collections")
async def api_sizechart_collections(store: int):
    st = next((x for x in db.list_stores() if x["id"] == store), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    r = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(
        "query { collections(first: 250, sortKey: TITLE) { nodes { id title handle } } }")
    return {"collections": r["collections"]["nodes"]}


# ---- display settings per store, and the storefront script

SCRIPT_TAGS = """query { scriptTags(first: 50) { nodes { id src } } }"""
SCRIPT_CREATE = """mutation C($i: ScriptTagInput!) { scriptTagCreate(input: $i) { scriptTag { id } userErrors { field message } } }"""
SCRIPT_DELETE = """mutation D($id: ID!) { scriptTagDelete(id: $id) { deletedScriptTagId userErrors { field message } } }"""


async def _sc_script(store, on: bool) -> str:
    """Add or remove the size chart script on the store. Returns an error message or ''."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    src = f"{BASE_URL}/static/sizechart-widget.js"
    try:
        tags = (await client.gql(SCRIPT_TAGS))["scriptTags"]["nodes"]
        ours = [t for t in tags if "sizechart-widget.js" in (t.get("src") or "")]
        if on and not ours:
            r = await client.gql(SCRIPT_CREATE, {"i": {"src": src, "displayScope": "ONLINE_STORE", "cache": False}})
            errs = r["scriptTagCreate"]["userErrors"]
            if errs:
                return "; ".join(e["message"] for e in errs)
        if not on:
            for t in ours:
                await client.gql(SCRIPT_DELETE, {"id": t["id"]})
    except Exception as e:
        return str(e)[:200]
    return ""


# ---------------------------------------------------------------- Apps → Tracking (tracking.py)

@app.get("/tracking", response_class=HTMLResponse)
def tracking_page():
    return _page("tracking.html", ("tracking.js", "nav.js", "styles.css", "skin.css", "chat.js"))


_px_hits: dict = {}
_px_keys: dict = {"at": 0.0, "map": {}}


def _px_store_for(key: str):
    """Store id for a pixel key (cached a minute)."""
    if time.time() - _px_keys["at"] > 60:
        m = {}
        for x in db.list_stores():
            k = db.get_setting(f"px_key:{x['id']}")
            if k:
                m[k] = x["id"]
        _px_keys.update(at=time.time(), map=m)
    return _px_keys["map"].get(key)


_px_order_state: dict = {}      # store id -> {last_run, added, written, error}


async def _px_orders(st: dict) -> dict:
    """One store: read its orders of the last 2 days (10+ minutes old, so the pixel has had its chance), add the
    ones the pixel missed as purchases, and write EcomVital's ad lines on each Shopify order (setting
    px_orders:<id> {"write": false} turns the writing off)."""
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ripe = datetime.now(timezone.utc) - timedelta(minutes=10)
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    got = await client.gql(tracking.ORDERS_QUERY, {"q": f"created_at:>='{since}'"})
    orders = [o for o in got["orders"]["nodes"]
              if datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")) <= ripe]
    with db._conn() as con:
        before = con.execute("SELECT COUNT(*) FROM px_events WHERE store_id = ? AND id LIKE 'order:%'", (st["id"],)).fetchone()[0]
    done = await asyncio.to_thread(tracking.add_orders, st["id"], orders)
    with db._conn() as con:
        after = con.execute("SELECT COUNT(*) FROM px_events WHERE store_id = ? AND id LIKE 'order:%'", (st["id"],)).fetchone()[0]
    cfg = json.loads(db.get_setting(f"px_orders:{st['id']}") or "{}")
    written, errors = 0, []
    if cfg.get("write", True):
        for o, credit, how in done:
            lines = tracking.order_lines(o, credit, how)
            if lines is None:
                continue
            try:
                r = (await client.gql(tracking.ORDER_ATTRS, {"input": {"id": o["id"], "customAttributes": lines}}))["orderUpdate"]
                if r.get("userErrors"):
                    errors.append(f"{o['name']}: " + "; ".join(e["message"] for e in r["userErrors"]))
                else:
                    written += 1
            except Exception as e:
                errors.append(f"{o['name']}: {str(e)[:150]}")
    return {"orders": len(orders), "added": after - before, "written": written, "errors": errors[:5],
            "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds")}


async def _px_order_loop():
    """Every 3 minutes, for every store with the EcomVital pixel: purchases from Shopify's orders (see _px_orders)."""
    await asyncio.sleep(120)
    while True:
        for st in [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")
                   and db.get_setting(f"px_key:{x['id']}")]:
            try:
                _px_order_state[st["id"]] = await _px_orders(st)
            except Exception as e:
                _px_order_state[st["id"]] = {**_px_order_state.get(st["id"], {}), "error": str(e)[:200],
                                             "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        await asyncio.sleep(180)


@app.get("/api/tracking/orders-sync")
async def api_tracking_orders_sync(store: int = 0, run: int = 0):
    """Owner check: what the order reader did per store; run=1 runs it now for that store."""
    if run and store:
        st = next((x for x in db.list_stores() if x["id"] == store), None)
        if not st:
            raise HTTPException(404, "Store not found.")
        _px_order_state[store] = await _px_orders(st)
    return {"stores": _px_order_state}


@app.options("/t/e")
def px_options():
    return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST",
                                              "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400"})


@app.post("/t/e")
async def px_event(request: Request):
    """One shopper event from the EcomVital pixel (open: shoppers' browsers send it)."""
    cors = {"Access-Control-Allow-Origin": "*"}
    body = await request.body()
    if len(body) > 20000:
        return Response(status_code=413, headers=cors)
    try:
        ev = json.loads(body)
    except ValueError:
        return Response(status_code=400, headers=cors)
    sid = _px_store_for(str(ev.get("k") or ""))
    if not sid:
        return Response(status_code=403, headers=cors)
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "")
    now = time.time()
    hits = [t for t in _px_hits.get(ip, []) if now - t < 60]
    if len(hits) > 120:                                # one browser can't flood it
        return Response(status_code=429, headers=cors)
    _px_hits[ip] = hits + [now]
    await asyncio.to_thread(tracking.record, sid, ev, ip)
    return Response(status_code=204, headers=cors)


PX_QUERY = "query { webPixel { id settings } }"
PX_CREATE = """mutation C($p: WebPixelInput!) { webPixelCreate(webPixel: $p) { webPixel { id settings } userErrors { field message code } } }"""
PX_UPDATE = """mutation U($id: ID!, $p: WebPixelInput!) { webPixelUpdate(id: $id, webPixel: $p) { webPixel { id settings } userErrors { field message code } } }"""


def _px_cfg(store_id: int) -> dict:
    try:
        cfg = json.loads(db.get_setting(f"px:{store_id}") or "{}")
    except ValueError:
        cfg = {}
    return {"mode": cfg.get("mode") or "watch", "meta_pixel": cfg.get("meta_pixel") or "",
            "has_token": bool(db.get_setting(f"px_meta_token:{store_id}"))}


@app.get("/api/tracking/stores")
async def api_tracking_stores():
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]
    out = []
    for x in stores:
        out.append({"id": x["id"], "name": x["name"].strip(), "domain": x["shop_domain"], "currency": x["currency"],
                    "timezone": x["timezone"], "installed": bool(db.get_setting(f"px_key:{x['id']}")),
                    "cfg": _px_cfg(x["id"]), "health": tracking.health(x["id"])})
    return {"stores": out}


@app.post("/api/tracking/install/{store_id}")
async def api_tracking_install(store_id: int):
    """Switch the EcomVital pixel on in a store (the app version must include the pixel extension)."""
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not store:
        raise HTTPException(404, "Store not found.")
    key = db.get_setting(f"px_key:{store_id}") or secrets.token_urlsafe(18)
    settings = json.dumps({"accountID": key, "endpoint": f"{BASE_URL}/t/e"})
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    try:
        cur = (await client.gql(PX_QUERY)).get("webPixel")
    except Exception:
        cur = None
    try:
        if cur:
            r = (await client.gql(PX_UPDATE, {"id": cur["id"], "p": {"settings": settings}}))["webPixelUpdate"]
        else:
            r = (await client.gql(PX_CREATE, {"p": {"settings": settings}}))["webPixelCreate"]
    except Exception as e:
        raise HTTPException(400, f"Shopify didn't accept it: {str(e)[:200]}. The store's EcomVital app needs the "
                                 "new version with the tracking pixel first.")
    if r.get("userErrors"):
        raise HTTPException(400, "Shopify said: " + "; ".join(e["message"] for e in r["userErrors"]))
    db.set_setting(f"px_key:{store_id}", key)
    _px_keys["at"] = 0
    return {"ok": True}


@app.put("/api/tracking/settings/{store_id}")
def api_tracking_settings(store_id: int, payload: dict):
    cfg = _px_cfg(store_id)
    if "meta_pixel" in payload:
        pid = re.sub(r"\D", "", str(payload.get("meta_pixel") or ""))
        if pid and not 10 <= len(pid) <= 20:
            raise HTTPException(400, "A Meta pixel (dataset) ID is 15-16 digits.")
        cfg["meta_pixel"] = pid
    if payload.get("meta_token"):
        db.set_setting(f"px_meta_token:{store_id}", str(payload["meta_token"]).strip())
    if payload.get("mode") in ("watch", "send"):
        if payload["mode"] == "send":
            raise HTTPException(400, "Sending to Meta comes next, after the watch-only week.")
        cfg["mode"] = payload["mode"]
    db.set_setting(f"px:{store_id}", json.dumps({k: cfg[k] for k in ("mode", "meta_pixel")}))
    return {"ok": True}


def _utc_range(st, start: str, end: str):
    tz = _zone(st)
    try:
        a = datetime.combine(date.fromisoformat(start), clock.min, tz).astimezone(timezone.utc)
        b = datetime.combine(date.fromisoformat(end) + timedelta(days=1), clock.min, tz).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(400, "Pick the dates.")
    return a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds")


@app.get("/api/tracking/events")
async def api_tracking_events(store: int, start: str, end: str, value: str, field: str = "a_campaign", hour: str = ""):
    """The single adds to cart, checkouts and purchases behind one report row (optionally one hour of one day)."""
    st = next((x for x in db.list_stores() if x["id"] == store), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    a, b = _utc_range(st, start, end)
    if hour.isdigit() and 0 <= int(hour) <= 23 and start == end:
        t = datetime.combine(date.fromisoformat(start), clock(int(hour)), _zone(st)).astimezone(timezone.utc)
        a, b = t.isoformat(timespec="seconds"), (t + timedelta(hours=1)).isoformat(timespec="seconds")
    return {"events": await asyncio.to_thread(tracking.events, store, a, b, field, value), "currency": st["currency"],
            "timezone": st["timezone"]}


@app.get("/api/tracking/journey")
async def api_tracking_journey(store: int, client: str):
    """Everything one shopper did on the store, with the ad clicks."""
    return {"events": await asyncio.to_thread(tracking.journey, store, client)}


@app.get("/api/tracking/report")
async def api_tracking_report(store: int, start: str, end: str, field: str = "a_campaign", since: str = "",
                              currency: str = "", hour: str = ""):
    """Rows like wetracked.io's report: adds to cart, checkouts and purchases (count + value) per
    campaign / ad set / ad or any UTM. Days are the store's own."""
    st = next((x for x in db.list_stores() if x["id"] == store), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    tz = _zone(st)
    try:
        a = datetime.combine(date.fromisoformat(start), clock.min, tz).astimezone(timezone.utc)
        b = datetime.combine(date.fromisoformat(end) + timedelta(days=1), clock.min, tz).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(400, "Pick the dates.")
    hr = None
    if hour.strip():                            # one hour of one day, on the store's clock (00-01 … 23-00)
        if start != end or not hour.isdigit() or not 0 <= int(hour) <= 23:
            raise HTTPException(400, "Pick one day to look at a single hour.")
        hr = int(hour)
        a = datetime.combine(date.fromisoformat(start), clock(hr), tz).astimezone(timezone.utc)
        b = a + timedelta(hours=1)
    a_s = a.isoformat(timespec="seconds")
    if since:                                   # from an exact moment (side-by-side checks against another tool)
        try:
            a_s = max(a_s, datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds"))
        except ValueError:
            raise HTTPException(400, "since must be a date-time.")
    out = await asyncio.to_thread(tracking.report, store, a_s, b.isoformat(timespec="seconds"), field)
    note = None
    lvl = tracking.LEVEL_OF_FIELD.get(field)
    conn = next((c for c in db.list_meta_connections() if c["id"] == st.get("meta_connection_id")), None)
    if lvl and st.get("meta_account_id") and conn:
        try:
            if hr is None:
                spends = await meta.level_spend(conn["access_token"], st["meta_account_id"], start, end, lvl)
            else:
                # the same hour on the ad account's clock (Meta reports hours there)
                try:
                    atz = ZoneInfo(await meta.account_timezone(conn["access_token"], st["meta_account_id"]))
                except (ZoneInfoNotFoundError, ValueError):
                    atz = timezone.utc
                at = a.astimezone(atz)
                spends = await meta.level_spend(conn["access_token"], st["meta_account_id"], at.date().isoformat(),
                                                at.date().isoformat(), lvl, hour=at.hour)
                if at.minute:
                    note = "The Meta ad account's clock is offset by half an hour from the store's, so spend is for the closest hour."
            table = await fx.table(st["currency"])
            f = fx.factor(table, st.get("meta_currency") or st["currency"], st["currency"]) if table else 1.0
            out = tracking.with_spend(out, spends, f)
        except Exception as e:
            note = f"Couldn't read Meta spend: {str(e)[:150]}"
    elif lvl:
        note = "This store has no Meta ad account linked, so spend isn't shown."
    cur = (currency or st["currency"]).upper()
    if cur != st["currency"]:
        table = await fx.table(cur)
        if table:
            out = tracking.in_currency(out, fx.factor(table, st["currency"], cur))
        else:
            cur = st["currency"]
    return {**out, "currency": cur, "store_currency": st["currency"], "health": tracking.health(store),
            "spend_note": note, "currencies": sorted(set(fx.CURRENCIES) if hasattr(fx, "CURRENCIES") else
                                                     {"AUD", "USD", "EUR", "GBP", "CAD", "NZD", "HKD", "SGD", "LKR", st["currency"]})}


# ---------------------------------------------------------------- Apps → Google Ads desk

@app.get("/googleads", response_class=HTMLResponse)
def googleads_page():
    return _page("googleads.html", ("googleads.js", "googleads-manage.js", "nav.js", "styles.css", "skin.css", "chat.js"))


_gdesk_cache: dict = {}
GD_PRODUCTS = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on Product { legacyResourceId title handle status
  featuredImage { url(transform: {maxWidth: 120}) }
  variants(first: 100) { nodes { legacyResourceId title price image { url(transform: {maxWidth: 120}) } } } } } }"""


_gads_acct: dict = {}      # Google Ads customer id -> (currency, time zone)
_pinfo: dict = {}          # store id -> {product id: (read at, info)}: titles, images, variants, status
PINFO_KEEP = 6 * 3600


async def _product_info(store, pids: list) -> dict:
    """Shopify title, image, status and variants per product id. Kept 6 hours per store (only products not
    seen yet are read, 50 at a time, three requests at once); status changes from the desk update it."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    have = _pinfo.setdefault(store["id"], {})
    now = time.time()
    want = [p for p in pids if p.isdigit() and not (p in have and now - have[p][0] < PINFO_KEEP)]
    gate = asyncio.Semaphore(3)

    async def batch(ids):
        async with gate:
            try:
                got = await client.gql(GD_PRODUCTS, {"ids": [f"gid://shopify/Product/{p}" for p in ids]})
            except Exception as e:
                _gdesk_cache["_info_error"] = f"{type(e).__name__}: {str(e)[:300]}"
                return
        for n in got.get("nodes") or []:
            if not n:
                continue
            have[str(n["legacyResourceId"])] = (now, {
                "title": n["title"], "handle": n["handle"], "status": (n.get("status") or "").title(),
                "image": (n.get("featuredImage") or {}).get("url"),
                "variants": {str(v["legacyResourceId"]): {"title": v["title"], "price": float(v["price"] or 0) or None,
                                                          "image": (v.get("image") or {}).get("url")}
                             for v in (n.get("variants") or {}).get("nodes") or []}})
    await asyncio.gather(*(batch(want[i:i + 50]) for i in range(0, len(want), 50)))
    # copies: prices are converted per request
    return {p: json.loads(json.dumps(have[p][1])) for p in pids if p in have}


def _gd_store(store: str):
    stores = [x for x in db.list_stores() if x.get("google_customer_id") and not demo.is_demo(x)]
    listing = [{"id": x["id"], "name": x["name"].strip(), "currency": x["currency"], "timezone": x["timezone"],
                "google_currency": x.get("google_currency"), "direct": x.get("google_source") != "sheet"} for x in stores]
    st = next((x for x in stores if str(x["id"]) == str(store)), None) or next((x for x in stores if x.get("google_source") != "sheet"), None)
    return st, listing


def _gd_tags(store_id: int) -> dict:
    with db._conn() as con:
        tags = [dict(r) for r in con.execute("SELECT id, name, color FROM gads_tags WHERE store_id = ? ORDER BY name", (store_id,))]
        links = {}
        for tid, pid in con.execute("SELECT t.id, p.product_id FROM gads_tags t JOIN gads_tag_products p ON p.tag_id = t.id"
                                    " WHERE t.store_id = ?", (store_id,)):
            links.setdefault(pid, []).append(tid)
    return {"tags": tags, "product_tags": links}


async def _gdesk(st, windows: dict, fresh: int = 0, country: str = "", min_spend: float = None) -> dict:
    """Every product of one store over the given store-day periods ({key: (first, last)}), in the Google Ads
    account's currency. The summary totals come from the dashboard's engine (whole store, no country filter)."""
    min_spend = min_spend if min_spend is not None else float(db.get_setting(f"gads_min:{st['id']}") or 25)
    today = _store_today(st)
    key = (st["id"], min_spend, tuple(sorted(windows.items())), country, today)
    hit = _gdesk_cache.get(key)
    if hit and not fresh and time.time() - hit[0] < 2400:
        return {**hit[1], "cached_at": hit[0]}
    auth = db.get_google_auth()
    if not auth:
        raise HTTPException(400, "Google isn't connected (Settings → Google Ads).")
    lo = min(date.fromisoformat(a) for a, _ in windows.values())
    hi = max(date.fromisoformat(b) for _, b in windows.values())
    args = (auth["refresh_token"], st["google_customer_id"], st["google_login_cid"])
    meta_conn = next((c for c in db.list_meta_connections() if c["id"] == st.get("meta_connection_id")), None)
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    tm, t0 = {}, time.time()

    async def timed(name, coro):
        t = time.time()
        try:
            return await coro
        finally:
            tm[name] = round(time.time() - t, 2)
    try:
        # the ad account's clock and currency (kept), then everything at once
        acc = _gads_acct.get(st["google_customer_id"])
        if not acc:
            _a, cur0, tz0 = await timed("account", gads.account_daily(*args, today.isoformat(), today.isoformat()))
            acc = _gads_acct[st["google_customer_id"]] = (cur0, tz0)
        gcur, atz = acc
        try:
            atzi = ZoneInfo(atz)
        except (ZoneInfoNotFoundError, ValueError):
            atzi = _zone(st)
        # each store-day period as the ad account's days (product rows only; the totals are exact by the hour)
        g_day = lambda d, last=False: (datetime.combine(d + timedelta(days=1 if last else 0), clock.min, _zone(st))
                                       - timedelta(seconds=1 if last else 0)).astimezone(atzi).date().isoformat()
        g_windows = {k: (g_day(date.fromisoformat(a)), g_day(date.fromisoformat(b), True)) for k, (a, b) in windows.items()}

        async def refunds_or_none():
            try:
                return await client.refund_lines(lo.isoformat(), hi.isoformat(), st["timezone"])
            except ShopifyError:
                return []
        (dash_rows, _notes), (rows, gcur2), refunds = await asyncio.gather(
            timed("dashboard", _store_window(st, lo.isoformat(), hi.isoformat(), auth, meta_conn)),
            timed("products", gads.product_report(*args, g_day(lo), g_day(hi, True))),
            timed("refunds", refunds_or_none()))
        gcur = gcur or gcur2 or st.get("google_currency") or st["currency"]
    except gads.GoogleAdsError as e:
        raise HTTPException(400, str(e))
    with db._conn() as con:
        lines = [dict(r) for r in con.execute(
            "SELECT day, product, variant_id, product_id, quantity, subtotal, cost_usd, cancelled, country FROM cog_lines"
            " WHERE store_id = ? AND day >= ? AND day <= ?", (st["id"], lo.isoformat(), hi.isoformat()))]
        fee = con.execute("SELECT SUM(COALESCE(fee_actual, fee_est)), SUM(amount) FROM order_fees"
                          " WHERE store_id = ? AND day >= ?", (st["id"], lo.isoformat())).fetchone()
        first_day = con.execute("SELECT MIN(day) FROM cog_lines WHERE store_id = ?", (st["id"],)).fetchone()[0]
    fee_rate = (fee[0] / fee[1]) if fee and fee[1] else 0.0
    pids = {google_desk.parse_item(r["item_id"])[0] for r in rows}
    pids |= {str(l["product_id"] or "").rsplit("/", 1)[-1] for l in lines if l.get("product_id")}
    info = await timed("shopify_products", _product_info(st, sorted(p for p in pids if p)))
    # everything in the Google Ads account's currency (this desk is for running the ads)
    table = await fx.table(gcur)
    f = lambda c: fx.factor(table, c, gcur) if table and c else 1.0
    for k in info:                                               # variant prices in the same currency
        for v in (info[k].get("variants") or {}).values():
            if v.get("price"):
                v["price"] *= f(st["currency"])
    tax = 1 + (st.get("google_tax_pct") or 0) / 100.0          # as the dashboard counts Google (e.g. ZAVA GST)
    out = await timed("maths", asyncio.to_thread(google_desk.build, rows, tax, lines, f("USD"), info, fee_rate, windows, g_windows,
                                                 min_spend, f(st["currency"]), refunds, country))
    if not country:
        google_desk.with_dashboard(out, _in_currency(dash_rows, {**st, "google_currency": gcur}, table, gcur), windows)
    out.update(store=st["id"], currency=gcur, store_currency=st["currency"], google_currency=gcur, fee_rate=fee_rate,
               min_spend=min_spend, today=today.isoformat(), account_timezone=atz, store_timezone=st["timezone"],
               shop=st["shop_domain"], country=country, data_from=first_day, timing={**tm, "total": round(time.time() - t0, 2)},
               fx_rate=None if gcur == st["currency"] else f(st["currency"]),
               missing_product_ids=not any(l.get("product_id") for l in lines),
               info_error=None if info else _gdesk_cache.get("_info_error"),
               sample_items=[r["item_id"] for r in rows[:3]] if not info else [])
    _gdesk_cache[key] = (time.time(), out)
    return {**out, "cached_at": time.time()}


def _gd_dates(start: str, end: str, today: date, default_days: int = 30):
    try:
        e = min(date.fromisoformat(end), today) if end else today
        a = date.fromisoformat(start) if start else e - timedelta(days=default_days - 1)
    except ValueError:
        raise HTTPException(400, "Pick the dates.")
    if a > e:
        raise HTTPException(400, "The start date has to be on or before the end date.")
    if (e - a).days > 731:
        raise HTTPException(400, "Pick up to two years at a time.")
    return a, e


@app.get("/api/googleads")
async def api_googleads(store: str = "", fresh: int = 0, min_spend: float = None, start: str = "", end: str = "",
                        country: str = ""):
    """One store's Google products for the picked period (default last 30 days) plus the last 7 / 14 / 30
    days, one row per Shopify product (google_desk.py). Each store on its own, in its Google Ads account's
    currency. Store days, today so far included (the dashboard's days); the summary totals come from the
    dashboard's engine, so the two agree."""
    st, listing = _gd_store(store)
    if not st:
        return {"stores": listing, "error": "No store reads Google Ads directly yet."}
    if st.get("google_source") == "sheet":
        return {"stores": listing, "store": st["id"],
                "error": f"{st['name'].strip()}'s ad account isn't readable directly yet: give shop.valueventures@gmail.com "
                         "access to it in Google Ads, then switch it from the Sheet."}
    today = _store_today(st)
    a, e = _gd_dates(start, end, today)
    out = await _gdesk(st, google_desk.windows_for(today, a, e), fresh, country.upper()[:3], min_spend)
    return {**out, "start": a.isoformat(), "end": e.isoformat(), "stores": listing, **_gd_tags(st["id"])}


@app.get("/api/googleads/compare")
async def api_googleads_compare(store: str, p1_start: str, p1_end: str, p2_start: str, p2_end: str, fresh: int = 0,
                                country: str = ""):
    """Period 1 against period 2 for every product (spend, revenue, ROAS, COG, net profit ...)."""
    st, listing = _gd_store(store)
    if not st or st.get("google_source") == "sheet":
        raise HTTPException(400, "This store's Google Ads account isn't readable directly yet.")
    today = _store_today(st)
    a1, e1 = _gd_dates(p1_start, p1_end, today)
    a2, e2 = _gd_dates(p2_start, p2_end, today)
    out = await _gdesk(st, {"p1": (a1.isoformat(), e1.isoformat()), "p2": (a2.isoformat(), e2.isoformat())},
                       fresh, country.upper()[:3])
    return {**out, "stores": listing, **_gd_tags(st["id"])}


@app.post("/api/googleads/tags")
def api_googleads_tag_add(payload: dict):
    name = (payload.get("name") or "").strip()[:40]
    color = (payload.get("color") or "#7C3AED")[:9]
    if not name:
        raise HTTPException(400, "Give the tag a name.")
    with db._conn() as con:
        con.execute("INSERT INTO gads_tags (store_id, name, color) VALUES (?, ?, ?)", (int(payload["store"]), name, color))
    return _gd_tags(int(payload["store"]))


@app.delete("/api/googleads/tags/{tag_id}")
def api_googleads_tag_delete(tag_id: int):
    with db._conn() as con:
        r = con.execute("SELECT store_id FROM gads_tags WHERE id = ?", (tag_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Tag not found.")
        con.execute("DELETE FROM gads_tag_products WHERE tag_id = ?", (tag_id,))
        con.execute("DELETE FROM gads_tags WHERE id = ?", (tag_id,))
    return _gd_tags(r[0])


@app.post("/api/googleads/tags/{tag_id}/products")
def api_googleads_tag_products(tag_id: int, payload: dict):
    """{add: [product ids]} or {remove: [product ids]}."""
    with db._conn() as con:
        r = con.execute("SELECT store_id FROM gads_tags WHERE id = ?", (tag_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Tag not found.")
        con.executemany("INSERT OR IGNORE INTO gads_tag_products (tag_id, product_id) VALUES (?, ?)",
                        [(tag_id, str(p)) for p in payload.get("add") or []])
        con.executemany("DELETE FROM gads_tag_products WHERE tag_id = ? AND product_id = ?",
                        [(tag_id, str(p)) for p in payload.get("remove") or []])
    return _gd_tags(r[0])


GD_STATUS = """mutation($p: ProductUpdateInput!) { productUpdate(product: $p) { product { id status } userErrors { message } } }"""


@app.post("/api/googleads/status")
async def api_googleads_status(payload: dict):
    """Set products Active / Draft / Archived in Shopify straight from the desk: {store, products, status}."""
    status = (payload.get("status") or "").upper()
    if status not in ("ACTIVE", "DRAFT", "ARCHIVED"):
        raise HTTPException(400, "Pick Active, Draft or Archived.")
    st = next((x for x in db.list_stores() if str(x["id"]) == str(payload.get("store"))), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    pids = [str(p) for p in payload.get("products") or [] if str(p).isdigit()][:250]
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    done, errors = [], []
    for pid in pids:
        try:
            got = await client.gql(GD_STATUS, {"p": {"id": f"gid://shopify/Product/{pid}", "status": status}})
            errs = (got.get("productUpdate") or {}).get("userErrors") or []
            if errs:
                errors.append(f"{pid}: {errs[0].get('message')}")
            else:
                done.append(pid)
        except Exception as e:
            errors.append(f"{pid}: {str(e)[:120]}")
    for pid in done:
        hit = _pinfo.get(st["id"], {}).get(pid)
        if hit:
            hit[1]["status"] = status.title()
    for k, (_t, out) in list(_gdesk_cache.items()):         # show the new status without a full reload
        if isinstance(k, tuple) and k and k[0] == st["id"]:
            for p in out.get("products", []):
                if p["pid"] in done:
                    p["status"] = status.title()
    return {"done": done, "errors": errors}


@app.get("/api/ads/structure")
async def api_ads_structure(store: int):
    """One store's Google Ads campaigns, budgets, bidding, asset / ad groups and which products each runs."""
    st = next((x for x in db.list_stores() if x["id"] == store and x.get("google_customer_id")), None)
    if not st:
        raise HTTPException(404, "Store has no Google Ads account.")
    auth = db.get_google_auth()
    if not auth:
        raise HTTPException(400, "Google isn't connected.")
    try:
        return await gads_manage.structure(auth["refresh_token"], st["google_customer_id"], st["google_login_cid"])
    except gads.GoogleAdsError as e:
        raise HTTPException(400, str(e))


_glive: dict = {}            # (store id, start, end) -> (time, {day: spend}) for days still moving


async def _google_days(store, start: str, end: str, auth) -> dict:
    """Google spend per store day. Days before yesterday (store clock) are read from Google once and kept in
    google_days; only yesterday and today are read live (at most every 10 minutes). The dashboard used to ask
    Google for the whole period on every view, which used up the API's daily allowance."""
    today = _store_today(store)
    live_from = (today - timedelta(days=1)).isoformat()
    out = {}
    if start < live_from:
        last_done = min(end, (today - timedelta(days=2)).isoformat())
        with db._conn() as con:
            have = {r[0]: r[1] for r in con.execute("SELECT date, spend FROM google_days WHERE store_id = ? AND date BETWEEN ? AND ?",
                                                    (store["id"], start, last_done))}
        missing = [d for d in _dates(start, last_done) if d not in have]
        if missing:
            try:
                got = await gads.daily_spend(auth["refresh_token"], store["google_customer_id"], store["google_login_cid"],
                                             missing[0], missing[-1], store["timezone"])
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                with db._conn() as con:
                    con.executemany("INSERT OR REPLACE INTO google_days (store_id, date, spend, saved_at) VALUES (?,?,?,?)",
                                    [(store["id"], d, got.get(d, 0.0), now) for d in _dates(missing[0], missing[-1])])
                for d in _dates(missing[0], missing[-1]):
                    have[d] = got.get(d, 0.0)
            except gads.GoogleAdsError:
                if not have:
                    raise
        out.update(have)
    a = max(start, live_from)
    if a <= end:
        key = (store["id"], a, end)
        hit = _glive.get(key)
        if hit and time.time() - hit[0] < 600:
            out.update(hit[1])
        else:
            try:
                got = await gads.daily_spend(auth["refresh_token"], store["google_customer_id"], store["google_login_cid"],
                                             a, end, store["timezone"])
                _glive[key] = (time.time(), got)
                out.update(got)
            except gads.GoogleAdsError:
                # Google's daily allowance used up (or Google down): keep the last figures we read, however old,
                # else the Google Ads script's Sheet, so spend never drops to $0 and profit never looks too good.
                if hit:
                    out.update(hit[1])
                else:
                    try:
                        got = await gsheet.daily_spend(auth["refresh_token"], store["google_customer_id"],
                                                       a, end, store["timezone"])
                    except Exception:
                        got = None
                    if not got:
                        raise
                    out.update(got)
    return out


_store_urls: dict = {}


async def _store_url(st) -> str:
    """The store's public web address (Shopify primary domain), for new campaigns' final URL."""
    if st["id"] not in _store_urls:
        got = await ShopifyClient(st["shop_domain"], st["access_token"]).gql("{ shop { primaryDomain { url } } }", {})
        _store_urls[st["id"]] = ((got.get("shop") or {}).get("primaryDomain") or {}).get("url") or f"https://{st['shop_domain']}"
    return _store_urls[st["id"]]


@app.post("/api/ads/selftest")
async def api_ads_selftest(payload: dict):
    """Owner check: ask Google to validate (validateOnly, nothing changes) a new feed-only Performance Max
    campaign and a product-list change on the store's first enabled Performance Max asset group."""
    st = next((x for x in db.list_stores() if x["id"] == int(payload["store"]) and x.get("google_customer_id")), None)
    auth = db.get_google_auth()
    args = (auth["refresh_token"], st["google_customer_id"], st["google_login_cid"])
    s = await gads_manage.structure(*args)
    out = {}
    items = [r["item_id"] for r in (await gads.product_report(*args, (date.today() - timedelta(days=3)).isoformat(),
                                                               date.today().isoformat()))[0] if r["item_id"]][:2]
    out["items"] = items
    try:
        await gads_manage.create_pmax(*args, "EcomVital test (validate only)", 10, s["merchant_ids"][0], await _store_url(st),
                                      items, None, "PAUSED", validate_only=True)
        out["create_pmax"] = "ok"
    except Exception as e:
        out["create_pmax"] = str(e)[:800]
    g = next((g for c in s["campaigns"] if c["status"] == "ENABLED" and c["type"] == "PERFORMANCE_MAX"
              for g in c["groups"] if g["status"] == "ENABLED"), None)
    if g:
        try:
            await gads_manage.mutate(*args, gads_manage.tree_ops(g, "except", items[:1]), validate_only=True)
            out["tree_except"] = "ok"
        except Exception as e:
            out["tree_except"] = str(e)[:800]
        c = next(c for c in s["campaigns"] if c["id"] == g["campaign"])
        try:
            await gads_manage.set_budget(*args, c["budget_resource"], c["budget"] + 1, validate_only=True)
            out["budget"] = "ok"
        except Exception as e:
            out["budget"] = str(e)[:800]
    return out


# ---------------------------------------------------------------- Apps → Google Ads → Manage (ads AI + rules)

ADS_DEFAULTS = {"enabled": False, "mode": "approve", "budget_cap": 300.0, "new_budget_cap": 150.0}
_ads_cache: dict = {}     # store id -> (time, days_rows, item index)


def _ads_settings(store_id: int) -> dict:
    try:
        got = json.loads(db.get_setting(f"ads_manage:{store_id}") or "{}")
    except ValueError:
        got = {}
    return {**ADS_DEFAULTS, **got}


def _ads_store(store_id) -> dict:
    st = next((x for x in db.list_stores() if str(x["id"]) == str(store_id) and x.get("google_customer_id")), None)
    if not st:
        raise HTTPException(404, "That store has no Google Ads account.")
    if st.get("google_source") == "sheet":
        raise HTTPException(400, "This store's ad account isn't readable directly yet.")
    return st


def _ads_args(st):
    auth = db.get_google_auth()
    if not auth:
        raise HTTPException(400, "Google isn't connected (Settings → Google Ads).")
    return auth["refresh_token"], st["google_customer_id"], st["google_login_cid"]


async def _ads_data(st, fresh=False):
    """Google per product / campaign / day for the last 60 days (kept 20 minutes) and every item id seen per product."""
    hit = _ads_cache.get(st["id"])
    if hit and not fresh and time.time() - hit[0] < 1200:
        return hit[1], hit[2]
    today = _store_today(st)
    rows = await gads_manage.product_days(*_ads_args(st), (today - timedelta(days=61)).isoformat(),
                                          (today + timedelta(days=1)).isoformat())
    index = {}
    for r in rows:
        if r["item"]:
            index.setdefault(google_desk.parse_item(r["item"])[0], set()).add(r["item"])
    _ads_cache[st["id"]] = (time.time(), rows, index)
    return rows, index


def _ads_lines(st, days=62):
    start = (_store_today(st) - timedelta(days=days)).isoformat()
    with db._conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT day, product_id, variant_id, quantity, subtotal, cancelled FROM cog_lines WHERE store_id = ? AND day >= ?",
            (st["id"], start))]


_ads_catalog: dict = {}      # store id -> (time, [catalogue items])


async def _ads_catalog_of(st) -> list:
    hit = _ads_catalog.get(st["id"])
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    got = await gads_manage.catalog(*_ads_args(st))
    _ads_catalog[st["id"]] = (time.time(), got)
    return got


async def _ads_items(st, pids: list, feed_label: str = None) -> list:
    """Every Google item id (one per variant) for these Shopify products. Google's own catalogue first (exactly the
    variants Google has, for the campaign's feed label); products Google doesn't list yet fall back to each Shopify
    variant under the feed's prefix (shopify_au_…)."""
    try:
        cat = await _ads_catalog_of(st)
    except gads.GoogleAdsError:
        cat = []
    want = {str(p) for p in pids}
    from_cat, seen = [], set()
    for i in cat:
        pid = google_desk.parse_item(i["item"])[0]
        if pid in want and (not feed_label or not i["feed_label"] or i["feed_label"].upper() == feed_label.upper()):
            from_cat.append(i["item"])
            seen.add(pid)
    pids = [p for p in pids if str(p) not in seen]
    if not pids:
        return sorted(set(from_cat))
    _rows, index = await _ads_data(st)
    prefixes = {re.match(r"^(shopify_[a-z]{2,3}_)", i, re.I).group(1) for v in index.values() for i in v
                if re.match(r"^(shopify_[a-z]{2,3}_)", i, re.I)} or {"shopify_" + (st.get("country") or "us").lower() + "_"}
    info = await _product_info(st, [str(p) for p in pids])
    out = set()
    for pid in pids:
        pid = str(pid)
        out |= index.get(pid, set())
        for vid in (info.get(pid, {}).get("variants") or {}):
            for pre in prefixes:
                out.add(f"{pre}{pid}_{vid}")
    seen = {}
    for i in sorted(out | set(from_cat)):                   # one spelling per item (Google matches any case)
        seen.setdefault(i.lower(), i)
    return sorted(seen.values())


def _ads_find_campaign(structure: dict, ref) -> dict:
    """A campaign by id or by name (case-insensitive), or None."""
    if ref is None or ref == "":
        return None
    ref = str(ref).strip()
    by_id = next((c for c in structure["campaigns"] if c["id"] == ref), None)
    return by_id or next((c for c in structure["campaigns"] if (c["name"] or "").strip().lower() == ref.lower()), None)


def _ads_groups(c: dict, purpose: str) -> list:
    groups = [g for g in c["groups"] if g["status"] == "ENABLED"] or c["groups"]
    if not groups:
        raise gads.GoogleAdsError(f"“{c['name']}” has no asset group / ad group to put products in.")
    return groups[:1] if purpose == "add" else groups


async def _ads_execute(st, a: dict, created: dict, validate_only=False) -> dict:
    """Carry out one action in Google Ads. Returns {before, result}. created: names of campaigns made earlier in the
    same plan -> ids. Every change is tried with validateOnly first."""
    args = _ads_args(st)
    cfg = _ads_settings(st["id"])
    p = a["params"]
    s = await gads_manage.structure(*args)

    def camp(ref):
        if isinstance(ref, str) and ref.lower().startswith("new:"):
            ref = created.get(ref[4:].strip().lower()) or ref[4:].strip()
        c = _ads_find_campaign(s, ref)
        if not c:
            raise gads.GoogleAdsError(f"No campaign called / with id “{ref}” in this account.")
        return c

    async def run(ops):
        # Google applies a change all-or-nothing, so a big one isn't tried in test mode first (that would cost the
        # day's allowance twice); small ones are, which catches mistakes before anything is sent
        if validate_only or len(ops) <= 50:
            await gads_manage.mutate(*args, ops, validate_only=True)
        if not validate_only:
            await gads_manage.mutate(*args, ops)
    k = a["kind"]
    if k == "set_budget":
        c = camp(p["campaign"])
        amount = float(p["amount"])
        if amount > float(cfg["budget_cap"]):
            raise gads.GoogleAdsError(f"{amount:.2f} a day is over this store's cap ({cfg['budget_cap']:.2f}). Raise the cap in Manage settings first.")
        if c["budget_shared"]:
            raise gads.GoogleAdsError(f"“{c['name']}” uses a shared budget; change it in Google Ads.")
        await run([{"campaignBudgetOperation": {"update": {"resourceName": c["budget_resource"],
                    "amountMicros": str(int(round(amount * 1_000_000)))}, "updateMask": "amount_micros"}}])
        return {"before": {"campaign": c["id"], "budget": c["budget"]}, "result": f"{c['name']}: {c['budget']:.2f} → {amount:.2f} a day"}
    if k == "set_status":
        c = camp(p["campaign"])
        status = p["status"].upper()
        if status not in ("ENABLED", "PAUSED"):
            raise gads.GoogleAdsError("Status must be ENABLED or PAUSED.")
        await run([{"campaignOperation": {"update": {"resourceName": c["resource"], "status": status}, "updateMask": "status"}}])
        return {"before": {"campaign": c["id"], "status": c["status"]}, "result": f"{c['name']}: {c['status']} → {status}"}
    if k == "set_target_roas":
        c = camp(p["campaign"])
        t = float(p["target_roas"]) if p.get("target_roas") else None
        await run([{"campaignOperation": {"update": {"resourceName": c["resource"], "maximizeConversionValue": {"targetRoas": t} if t else {}},
                                          "updateMask": "maximize_conversion_value.target_roas"}}])
        return {"before": {"campaign": c["id"], "target_roas": c["target_roas"]}, "result": f"{c['name']}: target ROAS {c['target_roas']} → {t}"}
    if k == "create_campaign":
        budget = float(p["budget"])
        if budget > float(cfg["new_budget_cap"]):
            raise gads.GoogleAdsError(f"A new campaign at {budget:.2f} a day is over this store's cap for new campaigns "
                                      f"({cfg['new_budget_cap']:.2f}). Raise it in Manage settings first.")
        if _ads_find_campaign(s, p["name"]):
            raise gads.GoogleAdsError(f"There's already a campaign called “{p['name']}”.")
        if not s["merchant_ids"]:
            raise gads.GoogleAdsError("This account has no Merchant Center link on any campaign, so I can't make a shopping campaign.")
        labels0 = [c["feed_label"] for c in s["campaigns"] if c.get("feed_label")]
        cc = [c.upper() for c in (p.get("countries") or []) if c]
        items = await _ads_items(st, p.get("products") or [], p.get("feed_label") or (cc[0] if len(cc) == 1 and cc[0] in labels0 else None))
        if not items:
            raise gads.GoogleAdsError("No products to put in the new campaign.")
        status = (p.get("status") or "ENABLED").upper()
        countries = [c.upper() for c in (p.get("countries") or []) if c]
        labels = [c["feed_label"] for c in s["campaigns"] if c.get("feed_label")]
        feed = p.get("feed_label") or (countries[0] if len(countries) == 1 and countries[0] in labels else (labels[0] if labels else None))
        kw = dict(target_roas=p.get("target_roas"), status=status, feed_label=feed, countries=countries)
        cost = len(items) + 6 + len(countries) + 2 * ((len(items) + 949) // 950)
        made = await gads_manage.create_pmax(*args, p["name"], budget, s["merchant_ids"][0], await _store_url(st), items,
                                             validate_only=True, **kw)      # settings + a sample of products
        if not validate_only:
            if cost > gads.left_today():
                raise gads.GoogleAdsError(
                    f"Making this campaign is about {cost:,} operations for Google ({len(items):,} product variants) and only "
                    f"{gads.left_today():,} of today's {gads.DAILY_LIMIT:,} are left. It resets at 3pm Hong Kong.")
            made = await gads_manage.create_pmax(*args, p["name"], budget, s["merchant_ids"][0], await _store_url(st), items, **kw)
            created[p["name"].strip().lower()] = made["campaign_id"]
        return {"before": {"created": made.get("campaign_id")},
                "result": f"Made “{p['name']}” (Performance Max, feed only, bidding for conversion value"
                          + (f", target ROAS {p['target_roas']}" if p.get("target_roas") else "") + (f", shown in {', '.join(countries)}" if countries else "")
                          + f") at {budget:.2f} a day with {len(p.get('products') or [])} products ({len(items)} variants), {status.lower()}, no end date"
                          + f". Uses about {cost:,} of Google's {gads.DAILY_LIMIT:,} daily operations ({gads.left_today():,} left now)"}
    if k in ("move_products", "add_products", "remove_products"):
        pids = [str(x) for x in p.get("products") or []]
        if not pids:
            raise gads.GoogleAdsError("No products given.")
        items = await _ads_items(st, pids)
        ops, before, words = [], {}, []
        temp = 1
        src = camp(p["from_campaign"] if k == "move_products" else p["campaign"]) if (k != "add_products" and (p.get("from_campaign") or p.get("campaign"))) else None
        dst = camp(p["to_campaign"] if k == "move_products" else p["campaign"]) if k in ("move_products", "add_products") and (p.get("to_campaign") or p.get("campaign")) else None
        if k == "move_products" and src and dst and src["id"] == dst["id"]:
            raise gads.GoogleAdsError("From and to are the same campaign.")
        cat_attrs = {}
        if src and any(g["products"]["mode"] == "custom" for g in src["groups"]):
            try:
                cat_attrs = {i["item"].lower(): i for i in await _ads_catalog_of(st)}
            except gads.GoogleAdsError:
                cat_attrs = {}

        def change(g, add=(), remove=()):
            nonlocal temp
            before[g["resource"]] = {"nodes": gads_manage.plain_nodes(g)}
            if g["products"]["mode"] == "custom":           # picked by labels / categories: edit inside the tree
                if remove:
                    nodes = gads_manage.custom_exclude(g, remove, cat_attrs)
                else:
                    nodes = gads_manage.custom_include(g, add)
                o = gads_manage.rebuild_ops(g, nodes, temp)
                temp += len(nodes) + 5
                return o
            mode, its = gads_manage.new_tree(g["products"], add=add, remove=remove)
            o = gads_manage.tree_ops(g, mode, its, temp)
            temp += len(its) + 5
            return o
        if src:
            for g in _ads_groups(src, "remove"):
                ops += change(g, remove=items)
            words.append(f"out of “{src['name']}”")
        if dst:
            for g in _ads_groups(dst, "add"):
                ops += change(g, add=items)
            words.append(f"into “{dst['name']}”")
        if not ops:
            raise gads.GoogleAdsError("Nothing to change.")
        await run(ops)
        return {"before": {"groups": before}, "result": f"{len(pids)} product(s) ({len(items)} variants) " + " and ".join(words)}
    raise gads.GoogleAdsError(f"Unknown action “{k}”.")


async def _ads_undo(st, a: dict) -> str:
    args = _ads_args(st)
    b = json.loads(a["before"] or "{}")
    s = await gads_manage.structure(*args)
    if a["kind"] == "set_budget":
        c = _ads_find_campaign(s, b["campaign"])
        await gads_manage.set_budget(*args, c["budget_resource"], b["budget"])
        return f"{c['name']} back to {b['budget']:.2f} a day"
    if a["kind"] == "set_status":
        c = _ads_find_campaign(s, b["campaign"])
        await gads_manage.set_status(*args, c["resource"], b["status"])
        return f"{c['name']} back to {b['status']}"
    if a["kind"] == "set_target_roas":
        c = _ads_find_campaign(s, b["campaign"])
        await gads_manage.set_target_roas(*args, c["resource"], b["target_roas"])
        return f"{c['name']} target ROAS back to {b['target_roas']}"
    if a["kind"] == "create_campaign":
        c = _ads_find_campaign(s, b.get("created"))
        if c:
            await gads_manage.set_status(*args, c["resource"], "PAUSED")
        return "New campaign paused"
    if a["kind"] in ("move_products", "add_products", "remove_products"):
        groups = {g["resource"]: g for c in s["campaigns"] for g in c["groups"]}
        ops, temp = [], 1
        for res, prod in (b.get("groups") or {}).items():
            g = groups.get(res)
            if not g:
                continue
            if "nodes" in prod:                              # the tree exactly as it was
                ops += gads_manage.rebuild_ops(g, prod["nodes"], temp)
                temp += len(prod["nodes"]) + 5
            else:
                ops += gads_manage.tree_ops(g, prod["mode"], prod["items"], temp)
                temp += len(prod["items"]) + 5
        if ops:
            await gads_manage.mutate(*args, ops)
        return "Product lists put back as they were"
    raise gads.GoogleAdsError("Can't undo this one.")


def _ads_log(store_id, batch, source, kind, params, summary, detail=None, status="proposed"):
    with db._conn() as con:
        cur = con.execute("INSERT INTO ads_actions (store_id, batch, source, kind, params, summary, detail, status, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?)", (store_id, batch, source, kind, json.dumps(params), summary,
                                                          json.dumps(detail) if detail is not None else None, status,
                                                          datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return cur.lastrowid


def _ads_set(action_id, **kw):
    cols = ", ".join(f"{k} = ?" for k in kw)
    with db._conn() as con:
        con.execute(f"UPDATE ads_actions SET {cols} WHERE id = ?", (*kw.values(), action_id))


async def _ads_run_batch(st, batch: str, who: str):
    """Carry out a plan / proposal step by step, in order; stops at the first step Google refuses."""
    with db._conn() as con:
        acts = [dict(r) for r in con.execute("SELECT * FROM ads_actions WHERE store_id = ? AND batch = ? AND status IN "
                                             "('planned', 'proposed') ORDER BY id", (st["id"], batch))]
    created, done = {}, []
    for a in acts:
        a["params"] = json.loads(a["params"])
        try:
            got = await _ads_execute(st, a, created)
            _ads_set(a["id"], status="done", before=json.dumps(got["before"]), decided_by=who, error=None,
                     done_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), summary=got["result"])
            done.append(got["result"])
        except (gads.GoogleAdsError, HTTPException, ShopifyError) as e:
            _ads_set(a["id"], status="failed", decided_by=who, error=str(getattr(e, "detail", None) or e)[:600],
                     done_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            for rest in acts[acts.index(a) + 1:]:
                _ads_set(rest["id"], status="failed", error="Not done: an earlier step failed.")
            raise HTTPException(400, f"Step {acts.index(a) + 1} failed: {str(getattr(e, 'detail', None) or e)[:400]}"
                                     + (f" (done before it: {'; '.join(done)})" if done else ""))
    _ads_cache.pop(st["id"], None)
    _gdesk_cache.clear()
    return done


async def _ads_rule_ctx(st, rules: list) -> dict:
    args = _ads_args(st)
    today = _store_today(st)
    rows, _index = await _ads_data(st, fresh=True)
    s = await gads_manage.structure(*args)
    cdays = await gads_manage.campaign_days(*args, (today - timedelta(days=61)).isoformat(), (today + timedelta(days=1)).isoformat())
    new = []
    if any(r["kind"] == "new_products" for r in rules):
        days = max(int(r["params"].get("days_new", 7)) for r in rules if r["kind"] == "new_products")
        since = (today - timedelta(days=days)).isoformat()
        got = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(
            "query($q: String!) { products(first: 100, query: $q, sortKey: CREATED_AT, reverse: true) { nodes { legacyResourceId title createdAt status } } }",
            {"q": f"created_at:>={since} status:active"})
        new = [{"id": str(n["legacyResourceId"]), "title": n["title"], "created": n["createdAt"]}
               for n in (got.get("products") or {}).get("nodes") or []]
    return {"today": today, "days_rows": rows, "lines": _ads_lines(st), "structure": s, "new_products": new,
            "budget_cap": _ads_settings(st["id"])["budget_cap"],
            "campaign_cost": lambda cid, a, b: sum(v for d, v in cdays.get(str(cid), {}).items() if a <= d <= b)}


def _ads_resolve(rule: dict, structure: dict) -> dict:
    """Campaign names in a rule -> ids (so a rule can name a campaign made in the same chat)."""
    p = dict(rule["params"])
    for k in ("campaign", "from_campaign", "to_campaign", "move_to"):
        if p.get(k):
            c = _ads_find_campaign(structure, p[k])
            if c:
                p[k] = c["id"]
    return {**rule, "params": p}


async def _ads_run_rules(st, only_rule: int = None, who="rules") -> list:
    """Run the store's switched-on rules (or one rule now): Autopilot carries out what they want, otherwise it waits
    for Approve. The same change isn't asked twice while one is waiting, and budget raises at most once a day."""
    with db._conn() as con:
        rules = [dict(r) for r in con.execute("SELECT * FROM ads_rules WHERE store_id = ?" + (" AND id = ?" if only_rule else " AND enabled = 1"),
                                              (st["id"], only_rule) if only_rule else (st["id"],))]
    if not rules:
        return []
    for r in rules:
        r["params"] = json.loads(r["params"])
    ctx = await _ads_rule_ctx(st, rules)
    cfg = _ads_settings(st["id"])
    out = []
    for r in rules:
        rr = _ads_resolve(r, ctx["structure"])
        got = ads_engine.evaluate(rr, ctx)
        notes = got["notes"]
        for a in got["actions"]:
            with db._conn() as con:
                cool = (datetime.now(timezone.utc) - timedelta(hours=a.get("cooldown_hours", 6))).isoformat(timespec="seconds")
                prior = [dict(x) for x in con.execute(
                    "SELECT params, status, created_at FROM ads_actions WHERE store_id = ? AND source = ? AND kind = ? AND "
                    "(status = 'proposed' OR (status = 'done' AND created_at >= ?))", (st["id"], f"rule:{r['id']}", a["kind"], cool))]
            if a["kind"] == "set_budget" and prior:
                notes.append("Budget already raised (or waiting) in the last 24 hours.")
                continue
            if "products" in a["params"]:
                taken = {x for pr in prior for x in json.loads(pr["params"]).get("products", [])}
                a["params"]["products"] = [x for x in a["params"]["products"] if x not in taken]
                a["detail"] = [d for d in a.get("detail", []) if d["product"] not in taken]
                if not a["params"]["products"]:
                    continue
            batch = f"rule{r['id']}-{secrets.token_hex(4)}"
            aid = _ads_log(st["id"], batch, f"rule:{r['id']}", a["kind"], a["params"], a["summary"], a.get("detail"))
            out.append(aid)
            if cfg["mode"] == "auto" and cfg["enabled"]:
                try:
                    await _ads_run_batch(st, batch, "autopilot")
                    notes.append(f"Done: {a['summary']}")
                except HTTPException as e:
                    notes.append(f"Failed: {e.detail}")
            else:
                notes.append(f"Waiting for Approve: {a['summary']}")
        with db._conn() as con:
            con.execute("UPDATE ads_rules SET last_run = ?, last_notes = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(notes), r["id"]))
    return out


async def _ads_loop():
    """Every hour: each managed store's rules."""
    await asyncio.sleep(240)
    while True:
        for st in db.list_stores():
            try:
                if st.get("google_customer_id") and _ads_settings(st["id"])["enabled"] and not gads.busy():
                    await _ads_run_rules(st)
            except Exception as e:
                print(f"Ads rules failed for {st['name']}: {str(getattr(e, 'detail', None) or e)[:300]}", flush=True)
        await asyncio.sleep(3600)


def _ads_actions_of(store_id, limit=80):
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM ads_actions WHERE store_id = ? ORDER BY id DESC LIMIT ?", (store_id, limit))]
    for r in rows:
        r["params"] = json.loads(r["params"] or "{}")
        r["detail"] = json.loads(r["detail"]) if r.get("detail") else None
        r.pop("before", None)
    return rows


def _ads_rules_of(store_id, names=None):
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM ads_rules WHERE store_id = ? ORDER BY id", (store_id,))]
    for r in rows:
        r["params"] = json.loads(r["params"])
        r["last_notes"] = json.loads(r["last_notes"]) if r.get("last_notes") else []
        r["text"] = ads_engine.describe(r, names or {})
    return rows


async def _ads_overview(st, start: str = None, end: str = None) -> dict:
    """Campaigns with budget, status, product list and their results for start..end (default today): Google spend,
    clicks, conversions, conversion value, add to carts, and the Shopify sales of the products they spent on."""
    args = _ads_args(st)
    today = _store_today(st)
    start, end = start or today.isoformat(), end or today.isoformat()
    s, tot, rows = await asyncio.gather(gads_manage.structure(*args), gads_manage.campaign_totals(*args, start, end),
                                        gads_manage.product_days(*args, start, end))
    lines = _ads_lines(st, (today - date.fromisoformat(start)).days + 1)
    for c in s["campaigns"]:
        t = tot.get(c["id"], {"cost": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "value": 0.0, "atc": 0.0})
        cs = ads_engine.campaign_stats(rows, lines, start, end, c["id"], t["cost"])
        c["p"] = {**t, "sales": cs["sales"], "roas": cs["roas"], "groas": t["value"] / t["cost"] if t["cost"] else None,
                  "products": cs["products"]}
        for g in c["groups"]:
            g.pop("tree", None)
            g["products"] = {"mode": g["products"]["mode"], "count": len(g["products"]["items"])}
    s["start"], s["end"] = start, end
    return s


def _ads_who(request: Request) -> str:
    u = getattr(request.state, "user", None) or {}
    return (u.get("email") if isinstance(u, dict) else None) or "someone"


@app.get("/api/ads/manage")
async def api_ads_manage(store: int, fresh: int = 0, start: str = "", end: str = ""):
    st = _ads_store(store)
    if fresh:
        _ads_cache.pop(st["id"], None)
    today = _store_today(st)
    try:
        a = date.fromisoformat(start) if start else today
        b = min(date.fromisoformat(end), today) if end else today
    except ValueError:
        raise HTTPException(400, "Pick the dates.")
    if a > b or (b - a).days > 400:
        raise HTTPException(400, "Pick a period of up to a year.")
    try:
        s = await _ads_overview(st, a.isoformat(), b.isoformat())
    except gads.GoogleAdsError as e:
        raise HTTPException(400, str(e))
    names = {c["id"]: c["name"] for c in s["campaigns"]}
    with db._conn() as con:
        chat = [dict(r) for r in con.execute("SELECT role, content, who, created_at FROM ads_chat WHERE store_id = ? ORDER BY id DESC LIMIT 60", (st["id"],))]
    for m in chat:
        m["content"] = json.loads(m["content"])
    # the store's breakeven ROAS over the last 30 days (dashboard figures): below it, ads lose money
    be = None
    try:
        rows, _n = await _store_window(st, (today - timedelta(days=29)).isoformat(), today.isoformat(), db.get_google_auth(),
                                       next((c for c in db.list_meta_connections() if c["id"] == st.get("meta_connection_id")), None))
        t = metrics.summarize(rows)
        kept = t["sales"] - t["refunds"] - t["chargebacks"] - t["cogs"] - t["processing_fee"]
        be = round(t["sales"] / kept, 2) if kept > 0 else None
    except Exception:
        pass
    return {"store": st["id"], "name": st["name"].strip(), "settings": _ads_settings(st["id"]), "structure": s, "breakeven_roas": be,
            "rules": _ads_rules_of(st["id"], names), "actions": _ads_actions_of(st["id"]), "chat": chat[::-1],
            "has_ai": bool(db.get_setting("anthropic_api_key")), "today": _store_today(st).isoformat()}


@app.put("/api/ads/settings/{store_id}")
def api_ads_settings(store_id: int, payload: dict):
    _ads_store(store_id)
    cfg = _ads_settings(store_id)
    if "enabled" in payload:
        cfg["enabled"] = bool(payload["enabled"])
    if payload.get("mode") in ("approve", "auto"):
        cfg["mode"] = payload["mode"]
    for k in ("budget_cap", "new_budget_cap"):
        if payload.get(k) not in (None, ""):
            v = float(payload[k])
            if v <= 0:
                raise HTTPException(400, "Caps must be above 0.")
            cfg[k] = v
    db.set_setting(f"ads_manage:{store_id}", json.dumps(cfg))
    return cfg


@app.post("/api/ads/batch/{store_id}/{batch}")
async def api_ads_batch(store_id: int, batch: str, request: Request, payload: dict):
    """Do it / Approve (go) or Cancel / Reject a plan or a rule's proposal."""
    st = _ads_store(store_id)
    if payload.get("go"):
        done = await _ads_run_batch(st, batch, _ads_who(request))
        return {"done": done}
    with db._conn() as con:
        con.execute("UPDATE ads_actions SET status = 'rejected', decided_by = ? WHERE store_id = ? AND batch = ? AND status IN ('planned', 'proposed')",
                    (_ads_who(request), store_id, batch))
    return {"ok": True}


@app.post("/api/ads/undo/{action_id}")
async def api_ads_undo(action_id: int, request: Request):
    with db._conn() as con:
        a = con.execute("SELECT * FROM ads_actions WHERE id = ?", (action_id,)).fetchone()
    if not a or a["status"] != "done":
        raise HTTPException(400, "Only a change that was carried out can be undone.")
    st = _ads_store(a["store_id"])
    try:
        msg = await _ads_undo(st, dict(a))
    except gads.GoogleAdsError as e:
        raise HTTPException(400, str(e))
    _ads_set(action_id, status="undone", error=f"Undone by {_ads_who(request)}: {msg}")
    _gdesk_cache.clear()
    return {"ok": True, "message": msg}


@app.post("/api/ads/rules/{rule_id}")
async def api_ads_rule(rule_id: int, payload: dict):
    """{enabled: true|false} switches a rule; {run: true} runs it now; {delete: true} deletes it."""
    with db._conn() as con:
        r = con.execute("SELECT * FROM ads_rules WHERE id = ?", (rule_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Rule not found.")
        if payload.get("delete"):
            con.execute("DELETE FROM ads_rules WHERE id = ?", (rule_id,))
            return {"ok": True}
        if "enabled" in payload:
            con.execute("UPDATE ads_rules SET enabled = ? WHERE id = ?", (1 if payload["enabled"] else 0, rule_id))
    if payload.get("run"):
        st = _ads_store(r["store_id"])
        await _ads_run_rules(st, only_rule=rule_id)
    return {"ok": True}


@app.get("/api/ads/catalog/{store_id}")
async def api_ads_catalog(store_id: int, products: str = ""):
    """Owner check: Google's catalogue items for some Shopify products (comma separated ids), counted by feed label / label0."""
    st = _ads_store(store_id)
    want = {p for p in products.split(",") if p}
    items = [i for i in await gads_manage.catalog(*_ads_args(st)) if not want or google_desk.parse_item(i["item"])[0] in want]
    by = {}
    for i in items:
        k = f"{i['feed_label']}|{i['label0']}"
        by[k] = by.get(k, 0) + 1
    return {"items": len(items), "by_feed_and_label0": by}


@app.post("/api/ads/dryrun/{store_id}")
async def api_ads_dryrun(store_id: int, payload: dict):
    """Owner check: Google's test run of a list of steps (validateOnly), nothing changes."""
    st = _ads_store(store_id)
    out, created = [], {}
    for sp in payload.get("steps") or []:
        params = {k: v for k, v in sp.items() if k != "kind"}
        try:
            got = await _ads_execute(st, {"kind": sp["kind"], "params": params}, created, validate_only=True)
            out.append({"kind": sp["kind"], "ok": True, "result": got["result"]})
        except (gads.GoogleAdsError, HTTPException) as e:
            out.append({"kind": sp["kind"], "ok": False, "error": str(getattr(e, "detail", None) or e)[:600]})
    return {"steps": out}


@app.post("/api/ads/manual/{store_id}")
async def api_ads_manual(store_id: int, payload: dict, request: Request):
    """A change made by hand on the Manage tab (budget, pause / enable): logged like the AI's, with Undo."""
    st = _ads_store(store_id)
    kind = payload.get("kind")
    if kind not in ("set_budget", "set_status", "set_target_roas"):
        raise HTTPException(400, "Unknown change.")
    batch = f"manual-{secrets.token_hex(4)}"
    _ads_log(st["id"], batch, "manual", kind, payload.get("params") or {}, "Change by hand", status="planned")
    done = await _ads_run_batch(st, batch, _ads_who(request))
    return {"done": done}


@app.post("/api/ads/chat/{store_id}")
async def api_ads_chat(store_id: int, payload: dict, request: Request):
    """Talk to the ads AI about one store: it reads the figures, plans changes (Do it button) and sets up rules
    (Turn on button). It never changes Google Ads by itself."""
    st = _ads_store(store_id)
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic API key in Settings → AI first.")
    text = (payload.get("message") or "").strip()
    if not text:
        raise HTTPException(400, "Write a message first.")
    who = _ads_who(request)
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db._conn() as con:
        con.execute("INSERT INTO ads_chat (store_id, role, content, who, created_at) VALUES (?, 'user', ?, ?, ?)",
                    (st["id"], json.dumps({"text": text}), who, now()))
        hist = [dict(r) for r in con.execute("SELECT role, content FROM ads_chat WHERE store_id = ? ORDER BY id DESC LIMIT 16", (st["id"],))][::-1]
    history = [{"role": h["role"], "content": json.loads(h["content"]).get("text") or ""} for h in hist]
    made = {"plans": [], "rules": []}

    async def tool(name, args):
        return await _ads_tool(st, name, args, who, made)
    try:
        got = await ads_brain.reply(key, history, tool, {
            "store": st["name"].strip(), "currency": (await _ads_overview_currency(st)), "today": _store_today(st).isoformat(),
            "settings": _ads_settings(st["id"])})
    except ads_brain.BrainError as e:
        raise HTTPException(400, str(e))
    msg = {"text": got["text"], "plans": made["plans"], "rules": made["rules"]}
    with db._conn() as con:
        con.execute("INSERT INTO ads_chat (store_id, role, content, who, created_at) VALUES (?, 'assistant', ?, 'ads AI', ?)",
                    (st["id"], json.dumps(msg), now()))
    return msg


async def _ads_overview_currency(st) -> str:
    acc = _gads_acct.get(st["google_customer_id"])
    return acc[0] if acc else (st.get("google_currency") or st["currency"])


async def _ads_tool(st, name: str, a: dict, who: str, made: dict) -> dict:
    """What the ads AI can look at and propose. Nothing here changes Google Ads."""
    today = _store_today(st)
    if name == "get_campaigns":
        s = await _ads_overview(st, *ads_engine.window(today, 7))
        s30 = {c["id"]: c["p"] for c in (await _ads_overview(st, *ads_engine.window(today, 30)))["campaigns"]}
        for c in s["campaigns"]:
            c["d7"], c["d30"] = c["p"], s30.get(c["id"])
        return {"currency": s["currency"], "campaigns": [{
            "id": c["id"], "name": c["name"], "status": c["status"], "type": c["type"], "budget_per_day": c["budget"],
            "bidding": c["bidding"], "target_roas": c["target_roas"],
            "product_lists": [f"{g['name']} ({g['status']}): {g['products']['mode']}"
                              + (f" {g['products']['count']} variants" if g['products']['count'] else "") for g in c["groups"]],
            "last_7_days": c["d7"], "last_30_days": c["d30"]} for c in s["campaigns"]],
            "note": "product_lists: all = every product; only N = just those variants; except N = all but those; "
                    "custom = picked by labels/categories (EcomVital can't edit those). ROAS = Shopify sales / spend."}
    if name == "product_verdicts":
        w = str(a.get("window") or "30")
        if w not in ("7", "14", "30"):
            w = "30"
        got = await _gdesk(st, google_desk.windows_for(today, today - timedelta(days=29), today))
        want = set(a.get("verdicts") or [])
        out, counts = [], {}
        for p in got.get("products") or []:
            if p.get("unlinked"):
                continue
            m = p["w"].get(w) or {}
            v = m.get("verdict") or ""
            counts[v] = counts.get(v, 0) + 1
            if want and v not in want:
                continue
            out.append({"product_id": p["pid"], "title": p["title"], "status": p.get("status"), "verdict": v,
                        "variants": len(p.get("variants") or []), "spend": round(m.get("cost") or 0, 2),
                        "sales": round(m.get("sales") or 0, 2), "roas": round(m["roas"], 2) if m.get("roas") else None,
                        "breakeven_roas": round(m["be_roas"], 2) if m.get("be_roas") else None,
                        "profit": round(m.get("profit") or 0, 2)})
        return {"window": f"last {w} store days (today included)", "currency": got.get("currency"),
                "verdict_counts": counts, "count": len(out), "products": out[: int(a.get("limit") or 300)],
                "note": "Same figures and verdicts as the Google Ads desk's Products page. product_id is the Shopify product id "
                        "to use in propose_plan; a plan with a product includes all its variants."}
    if name == "find_products":
        rows, _ = await _ads_data(st)
        s = await gads_manage.structure(*_ads_args(st))
        lines = _ads_lines(st)
        start, end = ads_engine.window(today, a.get("days") or 30)
        c = _ads_find_campaign(s, a.get("campaign")) if a.get("campaign") else None
        if a.get("campaign") and not c:
            return {"error": f"No campaign “{a['campaign']}”."}
        ps = ads_engine.product_stats(rows, lines, start, end, c["id"] if c else None)
        names = {x["id"]: x["name"] for x in s["campaigns"]}
        info = await _product_info(st, [p for p in ps if p.isdigit()])
        basis = a.get("basis") or "shopify"
        out = []
        for pid, x in ps.items():
            if pid == google_desk.UNLINKED or not (x["cost"] or x["qty"]):
                continue
            r = ads_engine.roas(x, basis)
            if a.get("min_spend") is not None and x["cost"] < a["min_spend"]:
                continue
            if a.get("max_spend") is not None and x["cost"] > a["max_spend"]:
                continue
            if a.get("min_roas") is not None and (r is None or r < a["min_roas"]):
                continue
            if a.get("max_roas") is not None and (r is not None and r > a["max_roas"]):
                continue
            t = info.get(pid, {}).get("title") or pid
            if a.get("title_contains") and a["title_contains"].lower() not in t.lower():
                continue
            out.append({"product_id": pid, "title": t, "status": info.get(pid, {}).get("status"), "spend": round(x["cost"], 2),
                        "shopify_sales": round(x["sales"], 2), "shopify_qty": x["qty"], "roas": round(r, 2) if r is not None else None,
                        "google_conv_value": round(x["value"], 2), "add_to_carts": round(x["atc"], 1),
                        "impressions": x["impressions"], "clicks": x["clicks"],
                        "spent_in_campaigns": [names.get(k, k) for k in x["campaigns"]]})
        out.sort(key=lambda r: -r["spend"])
        return {"period": f"{start} to {end} (store days)", "basis": basis, "count": len(out),
                "products": out[: int(a.get("limit") or 200)],
                "note": "All variants of a product are combined. Shopify sales are the product's sales on the store from every channel."}
    if name == "propose_plan":
        steps = a.get("steps") or []
        if not steps:
            return {"error": "No steps."}
        batch = f"plan-{secrets.token_hex(5)}"
        allowed = ("create_campaign", "move_products", "add_products", "remove_products", "set_budget", "set_status", "set_target_roas")
        for sp in steps:
            if sp.get("kind") not in allowed:
                return {"error": f"Unknown step kind {sp.get('kind')}. Use one of {allowed}."}
        for sp in steps:
            params = {k: v for k, v in sp.items() if k not in ("kind", "summary") and v not in (None, "", [])}
            _ads_log(st["id"], batch, "chat", sp["kind"], params, sp.get("summary") or sp["kind"],
                     {"products": len(params.get("products") or [])}, status="planned")
        # Google checks each step in test mode first (steps that use a campaign made earlier in the plan are checked when run)
        checks, created = [], {}
        for sp in steps:
            params = {k: v for k, v in sp.items() if k not in ("kind", "summary") and v not in (None, "", [])}
            if any(isinstance(v, str) and v.lower().startswith("new:") for v in params.values()):
                checks.append(f"{sp['kind']}: checked when it runs (uses the new campaign)")
                continue
            try:
                await _ads_execute(st, {"kind": sp["kind"], "params": params}, created, validate_only=True)
                checks.append(f"{sp['kind']}: Google accepted it in test mode")
            except (gads.GoogleAdsError, HTTPException) as e:
                with db._conn() as con:
                    con.execute("UPDATE ads_actions SET status = 'failed', error = ? WHERE batch = ? AND status = 'planned'",
                                (f"Google's test run refused it: {str(getattr(e, 'detail', None) or e)[:300]}", batch))
                return {"error": f"Google's test run refused step '{sp['kind']}': {str(getattr(e, 'detail', None) or e)[:400]}. "
                                 "Nothing was proposed; explain this to the person and suggest a fix."}
        made["plans"].append(batch)
        return {"plan": batch, "steps": len(steps), "google_test": checks,
                "status": "Shown to the person to approve (Do it in the app, or reply YES on WhatsApp). Nothing changes until they do."}
    if name == "save_rule":
        kind = a.get("kind")
        if kind not in ads_engine.KINDS:
            return {"error": f"kind must be one of {ads_engine.KINDS}"}
        params = a.get("params") or {}
        need = {"roas_floor": ["campaign", "days", "min_roas"], "graduate": ["from_campaign", "to_campaign", "max_spend"],
                "new_products": ["campaign"], "scale_budget": ["campaign", "days", "min_roas", "step"]}[kind]
        miss = [k for k in need if params.get(k) in (None, "")]
        if miss:
            return {"error": f"Missing {miss}"}
        with db._conn() as con:
            cur = con.execute("INSERT INTO ads_rules (store_id, name, kind, params, enabled, created_at, created_by) VALUES (?,?,?,?,0,?,?)",
                              (st["id"], (a.get("name") or kind)[:80], kind, json.dumps(params),
                               datetime.now(timezone.utc).isoformat(timespec="seconds"), who))
            rid = cur.lastrowid
        made["rules"].append(rid)
        return {"rule": rid, "status": "Saved switched OFF. The person turns it on with the Turn on button."}
    if name == "list_rules":
        s = await gads_manage.structure(*_ads_args(st))
        return {"rules": [{k: r[k] for k in ("id", "name", "kind", "params", "enabled", "last_run", "last_notes", "text")}
                          for r in _ads_rules_of(st["id"], {c["id"]: c["name"] for c in s["campaigns"]})]}
    if name == "recent_changes":
        return {"changes": [{k: r[k] for k in ("id", "kind", "summary", "status", "source", "created_at", "error")}
                            for r in _ads_actions_of(st["id"], 30)]}
    return {"error": f"Unknown tool {name}"}


@app.get("/api/tracking/google/peek")
async def api_tracking_google_peek(store: int, days: int = 7):
    """Read only: the store's recent orders and which carry a Google click id; the account's conversion actions."""
    st = _ads_store(store)
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 60)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    got = await gads_conv.orders(ShopifyClient(st["shop_domain"], st["access_token"]), since)
    acts = await gads_conv.all_actions(*_ads_args(st))
    clicked = [o for o in got if o["click"]]
    return {"orders": len(got), "with_google_click": len(clicked), "value_with_click": round(sum(o["value"] for o in clicked), 2),
            "examples": [{"order": o["name"], "value": o["value"], "click": list(o["click"])} for o in clicked[:5]],
            "landing_samples": [{"order": o["name"], "landing": o["landing"]} for o in got[-8:]],
            "conversion_actions": acts}


@app.get("/api/tracking/google/stores")
def api_tracking_google_stores():
    return {"stores": [{"id": x["id"], "name": x["name"].strip()} for x in db.list_stores()
                       if x.get("google_customer_id") and not demo.is_demo(x) and x.get("google_source") != "sheet"]}


@app.get("/api/tracking/google/actions")
async def api_tracking_google_actions(store: int):
    st = _ads_store(store)
    try:
        return {"actions": await gads_conv.all_actions(*_ads_args(st))}
    except gads.GoogleAdsError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tracking/google/primary")
async def api_tracking_google_primary(payload: dict):
    """Set Google conversion actions primary (used for bidding) or secondary: {store, primary: [names], secondary: [names]}."""
    st = _ads_store(payload["store"])
    args = _ads_args(st)
    acts = {a["name"]: a for a in await gads_conv.all_actions(*args)}
    cid = str(st["google_customer_id"]).replace("-", "")
    ops = []
    for flag, names in ((True, payload.get("primary") or []), (False, payload.get("secondary") or [])):
        for nm in names:
            a = acts.get(nm)
            if not a:
                raise HTTPException(400, f"No conversion action called “{nm}”.")
            ops.append({"conversionActionOperation": {"update": {"resourceName": f"customers/{cid}/conversionActions/{a['id']}",
                                                                 "primaryForGoal": flag}, "updateMask": "primary_for_goal"}})
    await gads_manage.mutate(*args, ops, validate_only=True)
    await gads_manage.mutate(*args, ops)
    return {"ok": True, "actions": await gads_conv.all_actions(*args)}


# ---------------------------------------------------------------- Tracking → Google Ads (EcomVital conversions)

GCONV_DEFAULT = {"enabled": False, "mode": "watch", "started_at": None, "switched_at": None, "ids": True, "note": None}
GCONV_ORDERS = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on Order { legacyResourceId name createdAt email phone
  currentTotalPriceSet { shopMoney { amount currencyCode } } currentTotalTaxSet { shopMoney { amount } } } } }"""
_gconv_state: dict = {}      # store id -> {"actions": (time, {...}), "last_run", "last_error"}


def _gconv_cfg(store_id: int) -> dict:
    try:
        got = json.loads(db.get_setting(f"gconv:{store_id}") or "{}")
    except ValueError:
        got = {}
    return {**GCONV_DEFAULT, **got}


def _gconv_save(store_id: int, cfg: dict):
    db.set_setting(f"gconv:{store_id}", json.dumps(cfg))


def _gconv_ours_primary(cfg: dict) -> bool:
    return cfg["mode"] == "primary" or (cfg["mode"] == "auto" and bool(cfg.get("switched_at")))


async def _gconv_actions(st, cfg, fresh=False) -> dict:
    """EcomVital's three conversion actions in the store's account, made if missing; purchase primary or not
    as the mode says (adds to cart and checkouts always secondary)."""
    hit = _gconv_state.get(st["id"], {}).get("actions")
    want = _gconv_ours_primary(cfg)
    if hit and not fresh and time.time() - hit[0] < 3600 and hit[1].get("purchase", {}).get("primary") == want:
        return hit[1]
    args = _ads_args(st)
    acts = {"purchase": await gads_conv.ensure_action(*args, "purchase", want),
            "add_to_cart": await gads_conv.ensure_action(*args, "add_to_cart", False),
            "begin_checkout": await gads_conv.ensure_action(*args, "begin_checkout", False)}
    _gconv_state.setdefault(st["id"], {})["actions"] = (time.time(), acts)
    return acts


async def _gconv_run(st):
    """Send the store's new pixel purchases / adds to cart / checkouts that came from a Google click."""
    cfg = _gconv_cfg(st["id"])
    if not cfg["enabled"] or not cfg.get("started_at"):
        return
    args = _ads_args(st)
    acts = await _gconv_actions(st, cfg)
    since = max(cfg["started_at"], (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds"))
    items = await asyncio.to_thread(gads_conv.todo, st["id"], since)
    if not items:
        return
    rows = [dict(i, status="no_click") for i in items if not i["click"]]
    send = [i for i in items if i["click"]]
    # purchases: the order's own value (total less tax), currency, email / phone from Shopify
    orders = {}
    oids = sorted({i["order_id"] for i in send if i["kind"] == "purchase" and i.get("order_id")})
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    for k in range(0, len(oids), 50):
        got = await client.gql(GCONV_ORDERS, {"ids": [f"gid://shopify/Order/{o}" for o in oids[k:k + 50]]})
        for n in got.get("nodes") or []:
            if n:
                tot = (n.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
                tax = float(((n.get("currentTotalTaxSet") or {}).get("shopMoney") or {}).get("amount") or 0)
                orders[str(n["legacyResourceId"])] = {"name": n["name"], "value": round(float(tot.get("amount") or 0) - tax, 2),
                                                       "currency": tot.get("currencyCode"), "email": n.get("email"), "phone": n.get("phone")}
    for kind in ("purchase", "add_to_cart", "begin_checkout"):
        batch = [i for i in send if i["kind"] == kind]
        if not batch or not acts.get(kind):
            continue
        up = []
        for i in batch:
            o = orders.get(str(i.get("order_id"))) if kind == "purchase" else None
            up.append({"order": (o or {}).get("name") or i["ref"] if kind == "purchase" else i["ref"], "created": i["at"],
                       "value": (o or {}).get("value") if o else round(float(i.get("value") or 0), 2),
                       "currency": (o or {}).get("currency") or i.get("currency") or st["currency"],
                       "click": i["click"], "email": (o or {}).get("email"), "phone": (o or {}).get("phone")})
        for k in range(0, len(up), 200):
            chunk, src = up[k:k + 200], batch[k:k + 200]
            try:
                res = await gads_conv.upload(*args, acts[kind]["resource"], chunk, with_ids=cfg.get("ids", True))
                if cfg.get("ids", True) and any(r["error"] and any(c in r["error"] for c in gads_conv.TERMS_CODES) for r in res):
                    cfg["ids"] = False                    # the account hasn't accepted Google's customer data terms
                    cfg["note"] = "Google Ads hasn't accepted the Customer data terms, so emails / phones aren't sent (Goals → Settings)."
                    _gconv_save(st["id"], cfg)
                    res = await gads_conv.upload(*args, acts[kind]["resource"], chunk, with_ids=False)
            except gads.GoogleAdsError as e:
                res = [{"ok": False, "error": str(e)[:300]} for _ in chunk]
            for i, u, r in zip(src, chunk, res):
                retry = (not r["ok"]) and any(c in (r["error"] or "") for c in gads_conv.RETRY_CODES)
                age = datetime.now(timezone.utc) - datetime.fromisoformat(i["at"])
                rows.append({**i, "value": u["value"], "currency": u["currency"], "click": next(iter(i["click"])),
                             "status": "sent" if r["ok"] else ("retry" if retry and age < timedelta(hours=48 if "NEEDS_SIGN_IN" not in (r["error"] or "") and "API_OFF" not in (r["error"] or "") else 24 * 7) else "failed"),
                             "error": r.get("error")})
    await asyncio.to_thread(gads_conv.save, st["id"], rows)


async def _gconv_adjust(st):
    """Refunds and cancellations of purchases EcomVital sent (last 50 days): Google gets the order's new value
    (total now, less tax) or drops it when cancelled / fully refunded, so its ROAS is on real revenue."""
    cfg = _gconv_cfg(st["id"])
    if not cfg["enabled"]:
        return
    since = (datetime.now(timezone.utc) - timedelta(days=50)).isoformat(timespec="seconds")
    sent = await asyncio.to_thread(gads_conv.sent_purchases, st["id"], since)
    if not sent:
        return
    client = ShopifyClient(st["shop_domain"], st["access_token"])
    acts = await _gconv_actions(st, cfg)
    todo = []
    for k in range(0, len(sent), 50):
        part = sent[k:k + 50]
        got = await client.gql("query($ids: [ID!]!) { nodes(ids: $ids) { ... on Order { legacyResourceId name cancelledAt "
                               "currentTotalPriceSet { shopMoney { amount currencyCode } } currentTotalTaxSet { shopMoney { amount } } } } }",
                               {"ids": [f"gid://shopify/Order/{x['ref']}" for x in part]})
        by = {str(n["legacyResourceId"]): n for n in got.get("nodes") or [] if n}
        for x in part:
            n = by.get(str(x["ref"]))
            if not n:
                continue
            tot = (n.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
            tax = float(((n.get("currentTotalTaxSet") or {}).get("shopMoney") or {}).get("amount") or 0)
            new = 0.0 if n.get("cancelledAt") else max(0.0, round(float(tot.get("amount") or 0) - tax, 2))
            known = x["adj_value"] if x["adj_value"] is not None else x["value"]
            if known is not None and abs(new - known) >= 0.5:
                todo.append({"ref": x["ref"], "order": n["name"], "value": new, "currency": tot.get("currencyCode") or x.get("currency")})
    if not todo:
        return
    res = await gads_conv.adjust(*_ads_args(st), acts["purchase"]["resource"], todo)
    for t, r in zip(todo, res):
        if r["ok"]:
            await asyncio.to_thread(gads_conv.save_adj, st["id"], t["ref"], t["value"], "final" if t["value"] <= 0 else "done")
        elif any(c in (r["error"] or "") for c in gads_conv.ADJ_RETRY):
            await asyncio.to_thread(gads_conv.save_adj, st["id"], t["ref"], None, "retry", r["error"])
        else:
            await asyncio.to_thread(gads_conv.save_adj, st["id"], t["ref"], None, "failed", r["error"])


async def _gconv_compare(st, days: int = 14) -> dict:
    """Last N days, Google's own count per day: ProfitDesk Purchase against the other purchase actions."""
    args = _ads_args(st)
    today = _store_today(st)
    start = (today - timedelta(days=days - 1)).isoformat()
    got = await gads_conv.daily_by_action(*args, start, today.isoformat())
    acts = await gads_conv.all_actions(*args)
    ours = gads_conv.ACTION_NAMES["purchase"]
    others = [a["name"] for a in acts if a["category"] == "PURCHASE" and a["name"] != ours and a["status"] == "ENABLED"
              and (a["primary"] or got.get(a["name"]))]
    dates = [(today - timedelta(days=k)).isoformat() for k in range(days - 1, -1, -1)]
    return {"dates": dates, "ours": [round(got.get(ours, {}).get(d, 0), 1) for d in dates],
            "others": {nm: [round(got.get(nm, {}).get(d, 0), 1) for d in dates] for nm in others},
            "primary": [a["name"] for a in acts if a["category"] == "PURCHASE" and a["primary"]]}


async def _gconv_autoswitch(st):
    """Auto mode: after 7 days, when ProfitDesk Purchase caught at least 95% of the current primary's purchases over
    the last 7 complete days, make ours primary and the others secondary (one primary purchase action)."""
    cfg = _gconv_cfg(st["id"])
    if not cfg["enabled"] or cfg["mode"] != "auto" or cfg.get("switched_at") or not cfg.get("started_at"):
        return
    if datetime.now(timezone.utc) - datetime.fromisoformat(cfg["started_at"]) < timedelta(days=8):
        return
    c = await _gconv_compare(st, 8)
    ours = sum(c["ours"][:7])
    prim = [nm for nm in c["primary"] if nm != gads_conv.ACTION_NAMES["purchase"]]
    theirs = max((sum(c["others"].get(nm, [0] * 8)[:7]) for nm in prim), default=0)
    if theirs <= 0 or ours < 0.95 * theirs:
        cfg["note"] = f"Auto switch waiting: ours {ours:g} vs {theirs:g} purchases over 7 days (needs 95%)."
        _gconv_save(st["id"], cfg)
        return
    args = _ads_args(st)
    acts = {a["name"]: a for a in await gads_conv.all_actions(*args)}
    cid = str(st["google_customer_id"]).replace("-", "")
    ops = [{"conversionActionOperation": {"update": {"resourceName": f"customers/{cid}/conversionActions/{acts[nm]['id']}",
                                                     "primaryForGoal": False}, "updateMask": "primary_for_goal"}} for nm in prim if nm in acts]
    if ops:
        await gads_manage.mutate(*args, ops)
    cfg["switched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cfg["note"] = f"Switched: ProfitDesk Purchase is primary ({ours:g} vs {theirs:g} over 7 days); {', '.join(prim)} now secondary."
    _gconv_save(st["id"], cfg)
    await _gconv_actions(st, cfg, fresh=True)


async def _gconv_loop():
    """Every 10 minutes: send each switched-on store's new Google-click events; once an hour, the auto switch."""
    await asyncio.sleep(200)
    with db._conn() as con:      # rejected by the old upload method (Google moved new integrations to Data Manager): send again
        con.execute("DELETE FROM gconv_uploads WHERE status = 'failed' AND error LIKE '%Data Manager API%'")
    n = 0
    while True:
        for st in db.list_stores():
            try:
                if st.get("google_customer_id") and _gconv_cfg(st["id"])["enabled"]:
                    await _gconv_run(st)
                    if n % 6 == 0 and not gads.busy():
                        await _gconv_autoswitch(st)
                        await _gconv_adjust(st)
                    _gconv_state.setdefault(st["id"], {}).update(last_run=time.time(), last_error=None)
            except Exception as e:
                _gconv_state.setdefault(st["id"], {}).update(last_error=str(getattr(e, "detail", None) or e)[:300])
                print(f"Google conversions failed for {st['name']}: {str(e)[:300]}", flush=True)
        n += 1
        await asyncio.sleep(600)


@app.get("/api/tracking/google/status")
async def api_tracking_google_status(store: int):
    st = _ads_store(store)
    cfg = _gconv_cfg(st["id"])
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat(timespec="seconds")
    try:
        cmp_ = await _gconv_compare(st)
    except gads.GoogleAdsError as e:
        cmp_ = {"error": str(e)}
    with db._conn() as con:
        errs = [dict(r) for r in con.execute("SELECT kind, ref, at, error FROM gconv_uploads WHERE store_id = ? AND status IN ('failed', 'retry')"
                                             " ORDER BY at DESC LIMIT 5", (st["id"],))]
        visitors = con.execute("SELECT COUNT(*) FROM px_visitors WHERE store_id = ? AND g_click IS NOT NULL AND g_at >= ?",
                               (st["id"], since)).fetchone()[0]
    s = _gconv_state.get(st["id"], {})
    with db._conn() as con:
        adj = {r[0]: r[1] for r in con.execute("SELECT adj_status, COUNT(*) FROM gconv_uploads WHERE store_id = ? AND kind = 'purchase' "
                                               "AND adj_status IS NOT NULL GROUP BY adj_status", (st["id"],))}
    return {"store": st["id"], "settings": cfg, "ours_primary": _gconv_ours_primary(cfg), "counts": gads_conv.counts(st["id"], since),
            "adjusted": adj,
            "google_visitors_14d": visitors, "compare": cmp_, "errors": errs,
            "last_run": s.get("last_run"), "last_error": s.get("last_error"), "pixel": bool(db.get_setting(f"px_key:{st['id']}"))}


@app.post("/api/tracking/google/send/{store_id}")
async def api_tracking_google_send(store_id: int):
    """Send this store's waiting events to Google now (instead of waiting for the 10-minute round)."""
    st = _ads_store(store_id)
    await _gconv_run(st)
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat(timespec="seconds")
    with db._conn() as con:
        err = con.execute("SELECT error FROM gconv_uploads WHERE store_id = ? AND status IN ('retry', 'failed') ORDER BY at DESC LIMIT 1",
                          (st["id"],)).fetchone()
    return {"counts": gads_conv.counts(st["id"], since), "last_error": err[0] if err else None}


@app.put("/api/tracking/google/settings/{store_id}")
async def api_tracking_google_settings(store_id: int, payload: dict):
    """{enabled, mode: watch | auto | primary}. Turning on starts sending from now; mode primary makes
    ProfitDesk Purchase the purchase action Google bids on."""
    st = _ads_store(store_id)
    cfg = _gconv_cfg(st["id"])
    if "enabled" in payload:
        cfg["enabled"] = bool(payload["enabled"])
        if cfg["enabled"] and not cfg.get("started_at"):
            cfg["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if payload.get("mode") in ("watch", "auto", "primary"):
        cfg["mode"] = payload["mode"]
        if cfg["mode"] != "auto":
            cfg["switched_at"] = None
    _gconv_save(st["id"], cfg)
    if cfg["enabled"]:
        try:
            await _gconv_actions(st, cfg, fresh=True)
        except gads.GoogleAdsError as e:
            raise HTTPException(400, str(e))
    return cfg


GCONV_BRIDGE = "Google Shopping App Purchase"


async def _gconv_plan() -> list:
    """The changes still needed for the agreed setup (TrackBee cancelled):
    a store with a working Google Shopping App Purchase -> it becomes the primary purchase action (bridge) and EcomVital
    runs next to it on auto switch; a store without one -> ProfitDesk Purchase becomes primary straight away.
    ZAVA (its own Google tag works) is left alone."""
    steps = []
    for st in db.list_stores():
        if not st.get("google_customer_id") or demo.is_demo(st) or st.get("google_source") == "sheet" or "ZAVA" in st["name"].upper():
            continue
        if not db.get_setting(f"px_key:{st['id']}"):
            continue
        acts = await gads_conv.all_actions(*_ads_args(st))
        cfg = _gconv_cfg(st["id"])
        ours = gads_conv.ACTION_NAMES["purchase"]
        bridge = next((a for a in acts if a["name"] == GCONV_BRIDGE and a["status"] == "ENABLED" and a["conversions_30d"] > 0), None)
        prim = [a["name"] for a in acts if a["category"] == "PURCHASE" and a["primary"] and a["name"] != ours]
        if bridge:
            make_secondary = [nm for nm in prim if nm != GCONV_BRIDGE]
            need = not bridge["primary"] or make_secondary or cfg["mode"] != "auto" or not cfg["enabled"]
            if need:
                steps.append({"store": st["id"], "name": st["name"].strip(), "primary": [] if bridge["primary"] else [GCONV_BRIDGE],
                              "secondary": make_secondary, "mode": "auto",
                              "text": (f"Make “{GCONV_BRIDGE}” primary ({bridge['conversions_30d']:g} purchases in 30 days)" if not bridge["primary"] else f"Keep “{GCONV_BRIDGE}” primary")
                                      + (f"; set {', '.join('“' + n + '”' for n in make_secondary)} secondary" if make_secondary else "")
                                      + "; send ProfitDesk Purchase next to it and switch over automatically at 95%+"})
        else:
            if cfg["mode"] != "primary" or not cfg["enabled"] or prim:
                steps.append({"store": st["id"], "name": st["name"].strip(), "primary": [], "secondary": prim, "mode": "primary",
                              "text": "No working purchase tracker left: make ProfitDesk Purchase primary now"
                                      + (f"; set {', '.join('“' + n + '”' for n in prim)} secondary" if prim else "")})
    return steps


@app.get("/api/tracking/google/plan")
async def api_tracking_google_plan():
    return {"steps": await _gconv_plan()}


@app.post("/api/tracking/google/plan")
async def api_tracking_google_plan_do(request: Request):
    """Do it: carry out the plan, store by store (the person clicked)."""
    done, failed = [], []
    for sp in await _gconv_plan():
        st = _ads_store(sp["store"])
        args = _ads_args(st)
        try:
            cfg = _gconv_cfg(st["id"])
            cfg.update(enabled=True, mode=sp["mode"], switched_at=None)
            cfg["started_at"] = cfg.get("started_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
            _gconv_save(st["id"], cfg)
            await _gconv_actions(st, cfg, fresh=True)          # creates ours (primary when mode primary)
            acts = {a["name"]: a for a in await gads_conv.all_actions(*args)}
            cid = str(st["google_customer_id"]).replace("-", "")
            ops = [{"conversionActionOperation": {"update": {"resourceName": f"customers/{cid}/conversionActions/{acts[nm]['id']}",
                                                             "primaryForGoal": flag}, "updateMask": "primary_for_goal"}}
                   for flag, names in ((True, sp["primary"]), (False, sp["secondary"])) for nm in names if nm in acts]
            if ops:
                await gads_manage.mutate(*args, ops, validate_only=True)
                await gads_manage.mutate(*args, ops)
            done.append(f"{sp['name']}: {sp['text']}")
        except (gads.GoogleAdsError, HTTPException) as e:
            failed.append(f"{sp['name']}: {str(getattr(e, 'detail', None) or e)[:200]}")
    return {"done": done, "failed": failed}


# ---------------------------------------------------------------- the assistant: store health, live tracking, Google Ads
# control, schedules and alerts (Ask chat now; WhatsApp uses the same tools)

HEALTH_PRODUCTS = """{ products(first: 250, query: "status:active") { nodes { legacyResourceId title totalInventory tracksInventory
  featuredImage { url } variants(first: 5) { nodes { price } } } } }"""


def _src_of_click(a_click, a_source) -> str:
    c, s = (a_click or "").lower(), (a_source or "").lower()
    if c == "fbclid" or s in ("facebook", "fb", "meta", "instagram", "ig"):
        return "meta"
    if c in ("gclid", "gbraid", "wbraid") or s in ("google", "youtube"):
        return "google"
    if c == "ttclid" or s == "tiktok":
        return "tiktok"
    return "other"


def _live_hours(store_ids: list, hours: int) -> dict:
    """{store id: {hour (UTC ISO, start): {source: {atc, ic, purchase}}}} from the pixel, last N clock hours."""
    since = (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=max(1, min(hours, 48)) - 1))
    out = {}
    with db._conn() as con:
        rows = con.execute(
            f"SELECT store_id, substr(at, 1, 13), name, a_click, a_source, COUNT(*) FROM px_events WHERE at >= ? AND store_id IN "
            f"({','.join('?' * len(store_ids))}) AND name IN ('product_added_to_cart', 'checkout_started', 'checkout_completed') "
            "GROUP BY 1, 2, 3, 4, 5", (since.isoformat(timespec="seconds"), *store_ids)).fetchall()
    key = {"product_added_to_cart": "atc", "checkout_started": "ic", "checkout_completed": "purchase"}
    for sid, hr, name, clk, src, n in rows:
        slot = out.setdefault(sid, {}).setdefault(hr + ":00", {})
        s = slot.setdefault(_src_of_click(clk, src), {"atc": 0, "ic": 0, "purchase": 0})
        s[key[name]] += n
    return out


async def _store_health(st) -> dict:
    """Everything that can quietly go wrong in one store, problems first."""
    probs, ok, info = [], [], {}
    sid = st["id"]
    # Shopify: permissions and products
    try:
        _scopes.pop(sid, None)
        got = await _granted(st)
        have = got | {"read_" + g[6:] for g in got if g.startswith("write_")}
        miss = sorted(WANTED_SCOPES - have)
        (probs if miss else ok).append(f"Shopify permissions: {'missing ' + ', '.join(miss) if miss else 'all granted'}")
    except Exception as e:
        probs.append(f"Shopify: couldn't connect ({str(e)[:100]})")
    try:
        got = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(HEALTH_PRODUCTS, {})
        nodes = (got.get("products") or {}).get("nodes") or []
        no_img = [n["title"] for n in nodes if not n.get("featuredImage")]
        no_price = [n["title"] for n in nodes if any(float(v.get("price") or 0) <= 0 for v in (n.get("variants") or {}).get("nodes") or [])]
        no_stock = [n["title"] for n in nodes if n.get("tracksInventory") and (n.get("totalInventory") or 0) <= 0]
        info["active_products_checked"] = len(nodes)
        for label, lst in (("active without an image", no_img), ("with a variant priced 0", no_price), ("active but out of stock", no_stock)):
            if lst:
                probs.append(f"{len(lst)} product(s) {label}: " + ", ".join(lst[:5]) + (" …" if len(lst) > 5 else ""))
        if not (no_img or no_price or no_stock):
            ok.append(f"Products: {len(nodes)} active checked, no image / price / stock problems")
    except Exception as e:
        probs.append(f"Products: couldn't read ({str(e)[:100]})")
    # tracking pixel
    h = tracking.health(sid)
    if not db.get_setting(f"px_key:{sid}"):
        info["pixel"] = "not installed"
    elif not h.get("last"):
        probs.append("Tracking pixel: installed but no events yet")
    else:
        last = datetime.fromisoformat(h["last"])
        (probs if datetime.now(timezone.utc) - last > timedelta(hours=6) else ok).append(
            f"Tracking pixel: last event {last.strftime('%d %b %H:%M')} UTC, {h.get('hour', 0)} in the last hour")
    # Google: conversion tracking + EcomVital uploads + share of Shopify orders Google sees
    if st.get("google_customer_id") and st.get("google_source") != "sheet":
        try:
            acts = await gads_conv.all_actions(*_ads_args(st))
            prim = [a for a in acts if a["category"] == "PURCHASE" and a["primary"]]
            rec = sum(a["conversions_30d"] for a in prim)
            if not prim:
                probs.append("Google: no primary purchase action — Google bids on nothing")
            elif rec <= 0 and any(a["name"] == gads_conv.ACTION_NAMES["purchase"] for a in prim) and _gconv_cfg(sid).get("started_at") \
                    and datetime.now(timezone.utc) - datetime.fromisoformat(_gconv_cfg(sid)["started_at"]) < timedelta(days=14):
                ok.append("Google: ProfitDesk Purchase is primary and new (started under 14 days ago), still building up")
            elif rec <= 0:
                probs.append(f"Google: primary purchase action ({', '.join(a['name'] for a in prim)}) recorded 0 in 30 days")
            else:
                ok.append(f"Google: primary purchase = {', '.join(a['name'] for a in prim)} ({rec:g} in 30 days)")
            if len([a for a in prim if a["conversions_30d"] > 0]) > 1:
                probs.append("Google: more than one recording primary purchase action — purchases may count twice")
            cfg = _gconv_cfg(sid)
            if cfg["enabled"]:
                c = gads_conv.counts(sid, (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds"))
                p = c.get("purchase", {})
                ok.append(f"EcomVital → Google ({cfg['mode']}): last 7 days {p.get('sent', 0)} purchases sent, "
                          f"{p.get('no_click', 0)} without a Google click, {p.get('failed', 0)} failed")
                if p.get("failed"):
                    probs.append(f"EcomVital → Google: {p['failed']} purchase uploads failed in 7 days")
                stuck = p.get("retry", 0)
                if stuck:
                    with db._conn() as con:
                        why = con.execute("SELECT error FROM gconv_uploads WHERE store_id = ? AND status = 'retry' ORDER BY at DESC LIMIT 1", (sid,)).fetchone()
                    probs.append(f"EcomVital → Google: {stuck} purchases waiting to be re-sent" + (f" ({why[0][:160]})" if why and why[0] else ""))
            with db._conn() as con:
                orders = con.execute("SELECT COUNT(DISTINCT order_name) FROM cog_lines WHERE store_id = ? AND day >= ? AND cancelled = 0",
                                     (sid, (_store_today(st) - timedelta(days=29)).isoformat())).fetchone()[0]
            info["google_vs_shopify_orders_30d"] = f"Google primary {rec:g} vs Shopify {orders} orders (all channels)"
        except Exception as e:
            probs.append(f"Google: couldn't read ({str(e)[:120]})")
    if st.get("meta_account_id"):
        conn = next((c for c in db.list_meta_connections() if c["id"] == st.get("meta_connection_id")), None)
        (ok if conn else probs).append("Meta: ad account linked" if conn else "Meta: ad account's business isn't connected")
    # COG / invoices
    with db._conn() as con:
        since = (_store_today(st) - timedelta(days=14)).isoformat()
        src = dict(con.execute("SELECT COALESCE(source, '?'), COUNT(*) FROM cog_lines WHERE store_id = ? AND day >= ? GROUP BY 1",
                               (sid, since)).fetchall())
        last_inv = con.execute("SELECT MAX(uploaded_at) FROM invoices WHERE store_id = ? OR store_id IS NULL", (sid,)).fetchone()[0]
    tot = sum(v for k, v in src.items() if k != "cancelled")
    if tot:
        est = src.get("estimate", 0) / tot
        line = (f"COG last 14 days: {src.get('invoice', 0)} lines from invoices, {src.get('catalog', 0)} from history, "
                f"{src.get('estimate', 0)} estimated ({est:.0%})")
        (probs if est > 0.25 else ok).append(line + (" — invoices may be behind" if est > 0.25 else ""))
    info["last_invoice_uploaded"] = last_inv
    return {"store": st["name"].strip(), "problems": probs, "fine": ok, "info": info}


def _assist_user(user) -> str:
    return (user or {}).get("email") or "owner"


def _ai_notes() -> list:
    """The assistant's lasting notes (rules / corrections the owner told it to remember)."""
    with db._conn() as con:
        con.execute("CREATE TABLE IF NOT EXISTS assistant_notes (id INTEGER PRIMARY KEY, note TEXT NOT NULL, by TEXT,"
                    " at TEXT NOT NULL)")
        return [dict(r) for r in con.execute("SELECT id, note, by, at FROM assistant_notes ORDER BY id")]


async def _assist_tool(name: str, args: dict, user: dict):
    """The assistant's newer tools; None when the name isn't one of them."""
    real = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]
    pick = lambda sid: [x for x in real if sid in (None, "", "all") or str(x["id"]) == str(sid)]
    if name == "remember":
        if (user or {}).get("role") != "owner":
            return {"error": "Only the owner can give me lasting rules. I'll follow it for this chat."}
        note = str(args.get("note") or "").strip()[:500]
        if not note:
            return {"error": "Empty note."}
        _ai_notes()
        with db._conn() as con:
            cur = con.execute("INSERT INTO assistant_notes (note, by, at) VALUES (?,?,?)",
                              (note, (user or {}).get("email"), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return {"saved": cur.lastrowid, "note": note}
    if name == "forget":
        if (user or {}).get("role") != "owner":
            return {"error": "Only the owner can delete notes."}
        _ai_notes()
        with db._conn() as con:
            n = con.execute("DELETE FROM assistant_notes WHERE id = ?", (int(args.get("id") or 0),)).rowcount
        return {"deleted": n}
    if name == "product_sales":
        _need(user, "profit")
        sts = pick(args.get("store"))
        names = {x["id"]: (x["name"].strip(), x["currency"]) for x in sts}
        rows = await asyncio.to_thread(db.cog_lines, list(names), args["start"], args["end"])
        agg = {}
        for r in rows:
            if r["cancelled"]:
                continue
            prod = r["product"] or "?"
            key_name = prod if args.get("by_variant") else prod.rsplit(" - ", 1)[0]
            k = (r["store_id"], r.get("product_id") if not args.get("by_variant") and r.get("product_id") else key_name)
            a = agg.setdefault(k, {"store": names[r["store_id"]][0], "currency": names[r["store_id"]][1], "product": key_name,
                                   "orders": set(), "units": 0, "sales": 0.0, "cost_usd": 0.0})
            a["orders"].add(r["order_name"])
            a["units"] += r["quantity"] or 1
            a["sales"] += r["subtotal"] or 0.0
            a["cost_usd"] += r["cost_usd"] or 0.0
        out = sorted(agg.values(), key=lambda a: -a["sales"])
        lim = int(args.get("limit") or 50)
        return {"period": f"{args['start']} to {args['end']} (store days)", "products": len(out),
                "orders": len({(r["store_id"], r["order_name"]) for r in rows if not r["cancelled"]}),
                "rows": [_round({**a, "orders": len(a["orders"])}) for a in out[:lim]],
                "note": "Sales are the order lines' value after discounts, in each store's currency; cost is COG in USD. "
                        "Covers orders already costed (synced every 5 minutes)."}
    if name == "get_disputes":
        _need(user, "disputes")
        want = (args.get("state") or "open").lower()
        with db._conn() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM disputes ORDER BY due")]
        out = []
        for r in rows:
            if want == "open" and r["state"] == "closed":
                continue
            if want not in ("open", "all") and r["state"] != want:
                continue
            d = json.loads(r["data"] or "{}")
            st = next((x["name"].strip() for x in real if x["id"] == r["store_id"]), None)
            out.append({"provider": r["provider"], "state": r["state"], "due": r["due"], "store": st, "order": r["order_name"],
                        "amount": d.get("amount"), "currency": d.get("currency"), "reason": d.get("reason"),
                        "stage": d.get("stage"), "customer": d.get("customer"), "outcome": d.get("outcome")})
        return {"count": len(out), "disputes": out[:80], "link": f"{BASE_URL}/disputes"}
    if name in ("data_schema", "data_query"):
        if (user or {}).get("role") != "owner":
            return {"error": "Only the owner can look at the raw tables. Use the desk tools instead."}
        if name == "data_schema":
            return {"tables": await asyncio.to_thread(ai_data.schema),
                    "stores": [{"id": x["id"], "name": x["name"].strip(), "currency": x["currency"]} for x in real]}
        return await asyncio.to_thread(ai_data.query, args.get("sql") or "")
    if name == "find_parcels":
        _need(user, "scm")
        q = str(args.get("query") or "").strip()
        if not q:
            return {"error": "Give an order number or email."}
        key = q.lstrip("#").upper()
        with db._conn() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM shipments WHERE UPPER(REPLACE(order_name, '#', '')) = ? OR LOWER(email) = LOWER(?) "
                "ORDER BY order_at DESC LIMIT 20", (key, q))]
        keep = ("order_name", "order_at", "customer", "city", "country", "fulfillment", "number", "company", "tracking_url",
                "fulfilled_at", "status", "sub_status", "last_event", "last_event_at", "last_location", "transit_days")
        names = {x["id"]: x["name"].strip() for x in real}
        return {"parcels": [{"store": names.get(r["store_id"]), **{k: r.get(k) for k in keep}} for r in rows],
                "note": "number '' = not fulfilled / no tracking yet."}
    if name == "store_health":
        sts = pick(args.get("store"))
        got = await asyncio.gather(*(_store_health(x) for x in sts), return_exceptions=True)
        return {"stores": [g if not isinstance(g, Exception) else {"error": str(g)[:200]} for g in got]}
    if name == "tracking_live":
        sts = pick(args.get("store"))
        data = await asyncio.to_thread(_live_hours, [x["id"] for x in sts], int(args.get("hours") or 3))
        names = {x["id"]: x["name"].strip() for x in sts}
        return {"hours_utc": "clock hours in UTC", "stores": {names[k]: v for k, v in data.items()},
                "note": "Only stores with the EcomVital pixel; only shoppers who allowed tracking."}
    if name in ("google_campaigns", "google_products", "propose_google_change"):
        _need(user, "googleads")
        st = _ads_store(args["store"])
        if name == "google_campaigns":
            today = _store_today(st)
            a7 = await _ads_overview(st, *ads_engine.window(today, 7))
            t = await _ads_overview(st, today.isoformat(), today.isoformat())
            tp = {c["id"]: c["p"] for c in t["campaigns"]}
            mg = await api_ads_manage(st["id"], 0, today.isoformat(), today.isoformat())
            return _round({"currency": a7["currency"], "breakeven_roas": mg.get("breakeven_roas"), "campaigns": [{
                "id": c["id"], "name": c["name"], "status": c["status"], "type": c["type"], "budget": c["budget"],
                "target_roas": c["target_roas"], "today": tp.get(c["id"]), "last_7_days": c["p"],
                "products": [f"{g['name']}: {g['products']['mode']} {g['products']['count'] or ''}".strip() for g in c["groups"]]}
                for c in a7["campaigns"]]})
        if name == "google_products":
            return await _ads_tool(st, "find_products", {**args, "basis": "shopify"}, _assist_user(user), {"plans": [], "rules": []})
        if not auth.at_least(user or {}, "googleads", "manage"):
            return {"error": "Changing Google Ads needs Manage access to the Google Ads desk."}
        made = {"plans": [], "rules": []}
        got = await _ads_tool(st, "propose_plan", {"summary": args.get("summary"), "steps": args.get("steps") or []},
                              _assist_user(user), made)
        if got.get("error"):
            return got
        steps = "; ".join(f"{sp.get('kind')}: " + ", ".join(f"{k}={v if not isinstance(v, list) else str(len(v)) + ' products'}"
                                                             for k, v in sp.items() if k not in ("kind", "summary"))
                          for sp in args.get("steps") or [])
        return _propose(user, "ads_plan", f"{st['name'].strip()}: {args.get('summary') or 'Google Ads change'}", steps[:600],
                        {"store": st["id"], "batch": made["plans"][0]})
    if name == "find_shopify_products":
        st = next((x for x in real if str(x["id"]) == str(args.get("store"))), None)
        if not st:
            return {"error": "Pick one store (see list_stores)."}
        got = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(
            "query($q: String!, $n: Int!) { products(first: $n, query: $q) { nodes { legacyResourceId title handle status totalInventory } } }",
            {"q": args.get("query") or "", "n": max(1, min(int(args.get("limit") or 20), 50))})
        base, shop = await _store_url(st), st["shop_domain"].replace(".myshopify.com", "")
        return {"products": [{"id": str(n["legacyResourceId"]), "title": n["title"], "status": n["status"], "stock": n.get("totalInventory"),
                              "admin_link": f"https://admin.shopify.com/store/{shop}/products/{n['legacyResourceId']}",
                              "store_link": f"{base}/products/{n['handle']}"} for n in (got.get("products") or {}).get("nodes") or []]}
    if name == "google_product_issues":
        st = _ads_store(args["store"])
        try:
            rows = await gads_manage._q(*_ads_args(st),
                "SELECT shopping_product.item_id, shopping_product.title, shopping_product.status, shopping_product.issues, "
                "shopping_product.feed_label FROM shopping_product WHERE shopping_product.status != 'ELIGIBLE'")
        except gads.GoogleAdsError as e:
            return {"error": f"Google didn't give product statuses: {str(e)[:300]}"}
        base, shop = await _store_url(st), st["shop_domain"].replace(".myshopify.com", "")
        pids = sorted({google_desk.parse_item((r.get("shoppingProduct") or {}).get("itemId") or "")[0] for r in rows} - {google_desk.UNLINKED})
        info = await _product_info(st, [p for p in pids if p.isdigit()])
        out = {}
        for r in rows:
            sp = r.get("shoppingProduct") or {}
            pid = google_desk.parse_item(sp.get("itemId") or "")[0]
            o = out.setdefault(pid, {"product_id": pid, "title": (info.get(pid) or {}).get("title") or sp.get("title"),
                                     "google_status": sp.get("status"), "variants": 0, "issues": set(),
                                     "admin_link": f"https://admin.shopify.com/store/{shop}/products/{pid}" if pid.isdigit() else None,
                                     "store_link": f"{base}/products/{(info.get(pid) or {}).get('handle')}" if (info.get(pid) or {}).get("handle") else None})
            o["variants"] += 1
            for iss in sp.get("issues") or []:
                o["issues"].add(iss.get("description") or iss.get("detail") or iss.get("errorCode") or str(iss)[:80])
        res = sorted(out.values(), key=lambda x: x["title"] or "")
        for x in res:
            x["issues"] = sorted(x["issues"])[:5]
        return {"count": len(res), "products": res[:60],
                "note": "From Google Ads (read only). Fixes are made in Shopify / the Google app, not here."}
    if name == "create_schedule":
        try:
            hh, mm = [int(x) for x in str(args["time"]).split(":")[:2]]
            ZoneInfo(args.get("timezone") or "Asia/Hong_Kong")
        except (ValueError, ZoneInfoNotFoundError):
            return {"error": "Time must be HH:MM and the time zone an IANA name like Asia/Hong_Kong."}
        spec = {"instruction": args["instruction"], "time": f"{hh:02d}:{mm:02d}", "days": (args.get("days") or "daily").lower(),
                "timezone": args.get("timezone") or "Asia/Hong_Kong"}
        return _assist_job(user, "schedule", args["name"], spec)
    if name == "create_alert":
        spec = {"kind": args["kind"], "store": str(args.get("store") or "all"), "threshold": float(args.get("threshold") or 0)}
        return _assist_job(user, "alert", args["name"], spec)
    if name == "list_schedules_alerts":
        with db._conn() as con:
            rows = [dict(r) for r in con.execute("SELECT id, kind, name, spec, enabled, last_fire FROM assistant_jobs WHERE user_email = ?",
                                                 (_assist_user(user),))]
        for r in rows:
            r["spec"] = json.loads(r["spec"])
        return {"jobs": rows}
    if name == "delete_schedule_alert":
        with db._conn() as con:
            n = con.execute("DELETE FROM assistant_jobs WHERE id = ? AND user_email = ?", (int(args["id"]), _assist_user(user))).rowcount
        return {"deleted": bool(n)}
    return None


def _assist_job(user, kind, name, spec) -> dict:
    with db._conn() as con:
        cur = con.execute("INSERT INTO assistant_jobs (user_email, kind, name, spec, created_at) VALUES (?,?,?,?,?)",
                          (_assist_user(user), kind, name[:80], json.dumps(spec), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return {"saved": cur.lastrowid, "kind": kind, "spec": spec, "note": "Active now. Messages arrive in the Ask chat (and WhatsApp once connected)."}


def _assist_deliver(email: str, title: str, body: str, source: str):
    with db._conn() as con:
        iid = con.execute("INSERT INTO assistant_inbox (user_email, title, body, source, created_at) VALUES (?,?,?,?,?)",
                          (email, title[:200], body[:20000], source, datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
    try:
        asyncio.get_running_loop().create_task(_wa_push(email, iid, title, body))
    except RuntimeError:
        pass


def _assist_user_row(email: str) -> dict:
    return next((u for u in db.list_users() if u.get("email") == email), None) or {"email": email, "role": "owner"}


async def _assist_run_schedule(job: dict):
    key = db.get_setting("anthropic_api_key")
    if not key:
        return
    spec = json.loads(job["spec"])
    user = _assist_user_row(job["user_email"])
    today = max((_store_today(x) for x in db.list_stores() if not demo.is_demo(x)), default=datetime.now(CASH_TZ).date()).isoformat()
    hist = [{"role": "user", "content": f"(Scheduled message '{job['name']}': no one is waiting in the chat, so don't ask "
                                         f"questions, just do it and report.) {spec['instruction']}"}]
    try:
        got = await ai_chat.reply(key, hist, lambda n, a: _chat_tool(n, a, user), today, _display_currency(), notes=_ai_notes())
        _assist_deliver(job["user_email"], f"📅 {job['name']}", got["text"], f"schedule:{job['id']}")
    except Exception as e:
        _assist_deliver(job["user_email"], f"📅 {job['name']} (failed)", f"Couldn't make it: {str(e)[:300]}", f"schedule:{job['id']}")


async def _assist_check_alert(job: dict):
    spec = json.loads(job["spec"])
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and (spec["store"] in ("all", "") or str(x["id"]) == spec["store"])]
    now = datetime.now(timezone.utc)
    k, thr = spec["kind"], spec["threshold"]
    hits = []
    if k in ("meta_atc_hour", "atc_hour", "purchases_hour"):
        data = await asyncio.to_thread(_live_hours, [x["id"] for x in stores], 1)
        slot = now.strftime("%Y-%m-%dT%H") + ":00"
        for x in stores:
            s = (data.get(x["id"]) or {}).get(slot) or {}
            if k == "meta_atc_hour":
                n = (s.get("meta") or {}).get("atc", 0)
            elif k == "atc_hour":
                n = sum(v.get("atc", 0) for v in s.values())
            else:
                n = sum(v.get("purchase", 0) for v in s.values())
            if n >= thr:
                hits.append((f"{slot}:{x['id']}", f"{x['name'].strip()}: {n:g} {'adds to cart from Meta' if k == 'meta_atc_hour' else 'adds to cart' if k == 'atc_hour' else 'purchases'} "
                                                   f"this hour (since {now.strftime('%H')}:00 UTC)"))
    elif k == "no_events":
        for x in stores:
            if not db.get_setting(f"px_key:{x['id']}"):
                continue
            h = tracking.health(x["id"])
            if h.get("last") and now - datetime.fromisoformat(h["last"]) > timedelta(hours=thr or 3):
                hits.append((f"{now.strftime('%Y-%m-%d')}:{x['id']}", f"{x['name'].strip()}: no pixel events for {thr or 3:g}+ hours"))
    elif k == "google_tracking_stopped":
        day = (now - timedelta(days=1)).isoformat(timespec="seconds")
        with db._conn() as con:
            for x in stores:
                if not _gconv_cfg(x["id"])["enabled"]:
                    continue
                sent = con.execute("SELECT COUNT(*) FROM gconv_uploads WHERE store_id = ? AND status = 'sent' AND at >= ?", (x["id"], day)).fetchone()[0]
                vis = con.execute("SELECT COUNT(*) FROM px_visitors WHERE store_id = ? AND g_at >= ?", (x["id"], day)).fetchone()[0]
                if vis >= max(thr, 20) and sent == 0:
                    hits.append((f"{now.strftime('%Y-%m-%d')}:{x['id']}", f"{x['name'].strip()}: {vis} Google visitors in 24 h but nothing sent to Google"))
    fired = set(json.loads(job["last_key"] or "[]"))
    new = [(key, text) for key, text in hits if key not in fired]
    if new:
        _assist_deliver(job["user_email"], f"🔔 {job['name']}", "\n".join(t for _, t in new), f"alert:{job['id']}")
        fired = sorted(fired | {key for key, _ in new})[-200:]
        with db._conn() as con:
            con.execute("UPDATE assistant_jobs SET last_fire = ?, last_key = ? WHERE id = ?",
                        (now.isoformat(timespec="seconds"), json.dumps(fired), job["id"]))


DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


async def _assist_loop():
    """Every minute: schedules that are due; every 5 minutes: alerts."""
    await asyncio.sleep(120)
    n = 0
    while True:
        try:
            with db._conn() as con:
                jobs = [dict(r) for r in con.execute("SELECT * FROM assistant_jobs WHERE enabled = 1")]
            for j in jobs:
                spec = json.loads(j["spec"])
                if j["kind"] == "schedule":
                    now = datetime.now(ZoneInfo(spec.get("timezone") or "Asia/Hong_Kong"))
                    days = spec.get("days", "daily")
                    today = DAY_NAMES[now.weekday()]
                    due_day = days == "daily" or (days == "weekdays" and now.weekday() < 5) or today in days
                    key = now.strftime("%Y-%m-%d")
                    hh, mm = [int(x) for x in spec["time"].split(":")]
                    late = (now.hour * 60 + now.minute) - (hh * 60 + mm)          # minutes past the set time
                    if due_day and 0 <= late < 120 and j.get("last_key") != key:
                        with db._conn() as con:
                            con.execute("UPDATE assistant_jobs SET last_key = ?, last_run = ? WHERE id = ?",
                                        (key, datetime.now(timezone.utc).isoformat(timespec="seconds"), j["id"]))
                        asyncio.create_task(_assist_run_schedule(j))
                elif n % 5 == 0:
                    await _assist_check_alert(j)
        except Exception as e:
            print(f"Assistant loop: {str(e)[:300]}", flush=True)
        n += 1
        await asyncio.sleep(60)


@app.get("/api/assistant/inbox")
def api_assistant_inbox(request: Request, unread: int = 1):
    me = _assist_user(getattr(request.state, "user", None))
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, title, body, created_at, read_at FROM assistant_inbox WHERE user_email = ?" + (" AND read_at IS NULL" if unread else "")
            + " ORDER BY id DESC LIMIT 30", (me,))]
    return {"messages": rows[::-1]}


@app.post("/api/assistant/inbox/read")
def api_assistant_read(request: Request, payload: dict):
    me = _assist_user(getattr(request.state, "user", None))
    ids = [int(i) for i in payload.get("ids") or []]
    with db._conn() as con:
        con.executemany("UPDATE assistant_inbox SET read_at = ? WHERE id = ? AND user_email = ?",
                        [(datetime.now(timezone.utc).isoformat(timespec="seconds"), i, me) for i in ids])
    return {"ok": True}


# ---------------------------------------------------------------- WhatsApp: chat with the assistant

_wa_pending: dict = {}       # phone -> [action ids waiting for YES]
_wa_busy: dict = {}          # phone -> lock, one answer at a time per person


def _wa_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "wa_files")
    os.makedirs(d, exist_ok=True)
    return d


def _wa_log(phone, direction, text=None, wa_id=None, file_id=None) -> bool:
    try:
        with db._conn() as con:
            con.execute("INSERT INTO wa_messages (wa_id, phone, direction, text, file_id, created_at) VALUES (?,?,?,?,?,?)",
                        (wa_id, phone, direction, (text or "")[:20000], file_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return True
    except Exception:
        return False                          # already had this message id


def _wa_window_open(phone) -> bool:
    """Meta lets a business send free text for 24 hours after the person's last message."""
    with db._conn() as con:
        r = con.execute("SELECT MAX(created_at) FROM wa_messages WHERE phone = ? AND direction = 'in'", (phone,)).fetchone()
    return bool(r and r[0] and datetime.now(timezone.utc) - datetime.fromisoformat(r[0]) < timedelta(hours=23, minutes=50))


async def _wa_send(phone, text):
    await whatsapp.send_text(phone, text)
    _wa_log(phone, "out", text)


async def _wa_flush(phone, email):
    """Reports / alerts that waited for the person to reply (sent outside the 24-hour window as a template heads-up)."""
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT id, title, body FROM assistant_inbox WHERE user_email = ? AND sent_wa = 'waiting' ORDER BY id",
                                             (email,))]
    for m in rows:
        await _wa_send(phone, f"*{m['title']}*\n{m['body']}")
        with db._conn() as con:
            con.execute("UPDATE assistant_inbox SET sent_wa = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(timespec="seconds"), m["id"]))


async def _wa_push(email, inbox_id, title, body):
    """A report / alert also goes to the person's WhatsApp: as text inside the 24-hour window, else a template heads-up."""
    phone = whatsapp.phone_of(email) if whatsapp.ready() else None
    if not phone:
        return
    try:
        if _wa_window_open(phone):
            await _wa_send(phone, f"*{title}*\n{body}")
            mark = datetime.now(timezone.utc).isoformat(timespec="seconds")
        else:
            await whatsapp.send_template(phone, title)
            mark = "waiting"
        with db._conn() as con:
            con.execute("UPDATE assistant_inbox SET sent_wa = ? WHERE id = ?", (mark, inbox_id))
    except whatsapp.WAError as e:
        print(f"WhatsApp push failed: {e}", flush=True)


class _FakeReq:
    """Enough of a request for the invoice upload route (file bytes + who sent it)."""
    def __init__(self, data, user):
        self._d, self.state = data, type("S", (), {"user": user})()

    async def body(self):
        return self._d


async def _wa_file(phone, user, msg) -> tuple:
    """Save a photo / document someone sent. Invoices (PDF / Excel, or a caption saying invoice) go into COG → Invoices.
    Returns (file id, a line for the assistant / the reply)."""
    kind = msg["type"]
    m = msg.get(kind) or {}
    data, mime = await whatsapp.media(m["id"])
    caption = m.get("caption") or ""
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf",
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx"}.get(mime, "")
    name = re.sub(r"[^\w.\- ]", "_", m.get("filename") or f"{kind}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}{ext}")[:120]
    with db._conn() as con:
        fid = con.execute("INSERT INTO wa_files (phone, user_email, filename, mime, size, caption, path, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (phone, user.get("email"), name, mime, len(data), caption, "", datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid
    path = os.path.join(_wa_dir(), f"{fid}_{name}")
    with open(path, "wb") as f:
        f.write(data)
    with db._conn() as con:
        con.execute("UPDATE wa_files SET path = ? WHERE id = ?", (path, fid))
    is_invoice = "invoice" in (caption + " " + name).lower() or mime in ("application/pdf",
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if is_invoice and auth.at_least(user, "cog", "work"):
        try:
            got = await api_invoice_upload(_FakeReq(data, user), filename=name)
            with db._conn() as con:
                con.execute("UPDATE wa_files SET used_for = ? WHERE id = ?", (f"invoice:{got.get('id')}", fid))
            return fid, (f"File #{fid} ({name}) added to COG → Invoices: supplier {got.get('supplier')}, invoice {got.get('number')}"
                         + (", already uploaded before (attached)" if got.get("attached") else "") + ".")
        except HTTPException as e:
            return fid, f"File #{fid} ({name}) saved, but it couldn't be read as an invoice: {e.detail}"
    return fid, f"File #{fid} ({name}, {mime}) saved in EcomVital" + (f" with the note “{caption}”" if caption else "") + "."


async def _wa_handle(msg, contact_name=None):
    phone = whatsapp.norm_phone(msg.get("from"))
    who = whatsapp.person(phone)
    if not who:
        return                                # not someone the owner allowed: ignored
    seen = ((msg.get("text") or {}).get("body") or (msg.get("button") or {}).get("text")
            or ((msg.get("interactive") or {}).get("button_reply") or {}).get("title") or msg.get("type"))
    if not _wa_log(phone, "in", seen, msg.get("id")):
        return                                # Meta retried a message we already answered
    user = _assist_user_row(who["email"])
    lock = _wa_busy.setdefault(phone, asyncio.Lock())
    async with lock:
        await whatsapp.mark_read(msg["id"])
        await _wa_flush(phone, user["email"])
        t = msg.get("type")
        text = ((msg.get("text") or {}).get("body") or "").strip()
        if t == "button":
            text = ((msg.get("button") or {}).get("text") or "").strip()
        elif t == "interactive":
            text = (((msg.get("interactive") or {}).get("button_reply") or {}).get("title") or "").strip()
        note = ""
        if t == "audio":                                  # voice note -> text
            try:
                data, mime = await whatsapp.media((msg.get("audio") or {}).get("id"))
                text = await whatsapp.transcribe(data, mime)
                note = "voice note, transcribed"
                await _wa_send(phone, f"🎙️ _{text[:600]}_")
                _wa_log(phone, "in", f"(voice) {text}")
            except whatsapp.WAError as e:
                await _wa_send(phone, "I can't listen to voice notes yet: add an OpenAI key in EcomVital Settings → WhatsApp (voice notes)."
                               if "NO_KEY" in str(e) else f"Couldn't understand the voice note ({e}). Please type it.")
                return
            if not text:
                await _wa_send(phone, "The voice note came through empty. Please try again or type it.")
                return
            t = "text"
        if t in ("image", "document", "video"):
            try:
                _fid, note = await _wa_file(phone, user, msg)
            except whatsapp.WAError as e:
                note = f"(Couldn't download the file: {e})"
            text = ((msg.get(t) or {}).get("caption") or "").strip()
            if not text:
                await _wa_send(phone, note)
                return
        # Tasks: "doing" / "done", button taps, replies to a task message or a Follow up
        try:
            answer = _task_wa_reply(phone, user, msg, text)
        except Exception as e:                            # never leave them without an answer
            print(f"WhatsApp task reply failed: {e!r}", flush=True)
            answer = "Sorry, I couldn't update the task. Please open it in EcomVital: " + f"{BASE_URL}/tasks"
        if answer:
            await _wa_send(phone, answer)
            return
        if who.get("tasks_only"):
            await _wa_send(phone, "This number is for your EcomVital tasks: reply *Doing* or *Done* (e.g. “done #12”), "
                                  f"or reply to a task message with an update. Your tasks: {BASE_URL}/tasks")
            return
        # YES / NO to the last proposed change
        word = text.lower().strip(" .!")
        if _wa_pending.get(phone) and word in ("yes", "y", "confirm", "ok", "do it", "go", "no", "n", "cancel", "stop"):
            ok = word not in ("no", "n", "cancel", "stop")
            outs = []
            for aid in _wa_pending.pop(phone):
                try:
                    r = await api_chat_action(aid, {"confirm": ok}, _FakeReq(b"", user))
                    outs.append(r.get("text") or "Done.")
                except HTTPException as e:
                    outs.append(f"⚠ {e.detail}")
            await _wa_send(phone, "\n".join(outs))
            return
        key = db.get_setting("anthropic_api_key")
        if not key:
            await _wa_send(phone, "The assistant needs the Anthropic API key (EcomVital Settings → AI).")
            return
        with db._conn() as con:
            past = [dict(r) for r in con.execute("SELECT direction, text FROM wa_messages WHERE phone = ? ORDER BY id DESC LIMIT 20", (phone,))][::-1]
        hist = [{"role": "user" if m["direction"] == "in" else "assistant", "content": m["text"] or ""} for m in past[:-1] if m["text"]]
        me = (f"[WhatsApp from {user.get('name') or user.get('email')} ({user.get('email')}, {'owner' if user.get('role') == 'owner' else 'staff'}); "
              "on WhatsApp they approve a proposed change by replying YES, there is no Confirm button] ")
        if gads.busy():
            me += ("[System note: EcomVital's Google Ads API daily request allowance is used up until it resets (midnight US Pacific). "
                   "Shopify sales, COG, Meta and saved Google spend for past days still show; fresh Google Ads figures and Google Ads "
                   "changes wait until the reset.] ")
        hist.append({"role": "user", "content": me + (f"[{note}] " if note else "") + (text or "(sent a file)")})
        today = max((_store_today(x) for x in db.list_stores() if not demo.is_demo(x)), default=datetime.now(CASH_TZ).date()).isoformat()
        try:
            got = await ai_chat.reply(key, hist, lambda n, a: _chat_tool(n, a, user), today, _display_currency(), notes=_ai_notes())
        except ai_chat.ChatError as e:
            await _wa_send(phone, f"⚠ {e}")
            return
        reply = got["text"]
        for l in got.get("links") or []:
            reply += f"\n📎 {l['title']}: {BASE_URL}{l['url']}"
        if got.get("actions"):
            _wa_pending[phone] = [a["id"] for a in got["actions"]]
            reply += "\n\n" + "\n".join(f"➡️ *{a['title']}*\n{a['detail']}" for a in got["actions"]) + "\n\nReply *YES* to do it or *NO* to cancel."
        await _wa_send(phone, reply)


@app.get("/api/whatsapp/webhook")
def api_wa_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") and q.get("hub.verify_token") == whatsapp.cfg()["verify"]:
        return Response(q.get("hub.challenge") or "", media_type="text/plain")
    raise HTTPException(403, "Wrong verify token.")


@app.post("/api/whatsapp/webhook")
async def api_wa_webhook(request: Request):
    body = await request.body()
    if not whatsapp.signature_ok(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403, "Bad signature.")
    try:
        data = json.loads(body)
    except ValueError:
        return {"ok": True}
    for e in data.get("entry") or []:
        for ch in e.get("changes") or []:
            v = ch.get("value") or {}
            for m in v.get("messages") or []:
                asyncio.create_task(_wa_handle(m))
    return {"ok": True}


@app.get("/api/whatsapp/settings")
def api_wa_settings():
    c = whatsapp.cfg()
    if not c["verify"]:
        db.set_setting("wa_verify", secrets.token_urlsafe(18))
        c = whatsapp.cfg()
    users = [{"email": u["email"], "name": u.get("name") or u["email"], "role": u.get("role")} for u in db.list_users()]
    return {"connected": whatsapp.ready(), "has_token": bool(c["token"]), "has_secret": bool(c["app_secret"]),
            "phone_id": c["phone_id"], "waba_id": c["waba_id"], "verify": c["verify"], "people": c["people"],
            "app_id": db.get_setting("wa_app_id") or "",
            "has_voice_key": bool(db.get_setting("openai_api_key")),
            "webhook": f"{BASE_URL}/api/whatsapp/webhook", "users": users}


@app.put("/api/whatsapp/settings")
async def api_wa_settings_save(payload: dict):
    for k, s in (("token", "wa_token"), ("app_secret", "wa_app_secret"), ("phone_id", "wa_phone_id"), ("waba_id", "wa_waba_id"),
                 ("app_id", "wa_app_id"), ("openai_key", "openai_api_key")):
        v = (payload.get(k) or "").strip()
        if v:
            db.set_setting(s, v)
    if "people" in payload:
        people = [{"phone": whatsapp.norm_phone(p.get("phone")), "email": p.get("email"),
                   **({"tasks_only": True} if p.get("tasks_only") else {})} for p in payload["people"] or []
                  if whatsapp.norm_phone(p.get("phone")) and p.get("email")]
        db.set_setting("wa_people", json.dumps(people))
    out = {"ok": True}
    if whatsapp.ready() and whatsapp.cfg()["waba_id"]:
        if payload.get("token") or payload.get("app_secret") or payload.get("setup"):
            out["setup"] = await whatsapp.setup(db.get_setting("wa_app_id") or "")
        try:
            out["template"] = await whatsapp.ensure_template()
        except whatsapp.WAError as e:
            out["template_error"] = str(e)
    return out


@app.get("/api/whatsapp/diagnose")
async def api_wa_diagnose():
    """Owner check: what Meta has set up, and the last messages EcomVital received / sent."""
    with db._conn() as con:
        last = [dict(r) for r in con.execute("SELECT phone, direction, substr(text, 1, 80) AS text, created_at FROM wa_messages ORDER BY id DESC LIMIT 8")]
    return {"meta": await whatsapp.diagnose(db.get_setting("wa_app_id") or ""), "last_messages": last}


@app.get("/api/whatsapp/conversation")
def api_wa_conversation(limit: int = 120):
    """Owner: the WhatsApp conversations, oldest first (to check how the assistant did)."""
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT phone, direction, text, created_at FROM wa_messages ORDER BY id DESC LIMIT ?",
                                             (min(limit, 500),))]
    return {"messages": rows[::-1]}


@app.post("/api/whatsapp/setup")
async def api_wa_setup():
    """Run Meta's setup steps again (register the number, subscribe the app, set the webhook, the template)."""
    out = {"setup": await whatsapp.setup(db.get_setting("wa_app_id") or "")}
    try:
        out["template"] = await whatsapp.ensure_template()
    except whatsapp.WAError as e:
        out["template_error"] = str(e)
    return out


@app.post("/api/whatsapp/send")
async def api_wa_send(payload: dict):
    """Owner: send a message from EcomVital's WhatsApp to an allowed number (inside the 24-hour window)."""
    phone = whatsapp.norm_phone(payload.get("phone"))
    if not whatsapp.person(phone):
        raise HTTPException(400, "That number isn't in 'Who can use it'.")
    if not _wa_window_open(phone):
        raise HTTPException(400, "They haven't messaged in the last 24 hours, so Meta only allows the approved template.")
    try:
        await _wa_send(phone, payload.get("text") or "")
    except whatsapp.WAError as e:
        raise HTTPException(400, str(e))
    return {"sent": True}


@app.post("/api/whatsapp/test")
async def api_wa_test(payload: dict):
    """Send a test message to one allowed number (it must have messaged EcomVital's number in the last 24 h,
    otherwise the template is sent)."""
    phone = whatsapp.norm_phone(payload.get("phone"))
    if not whatsapp.person(phone):
        raise HTTPException(400, "Add that number under 'Who can use it' first.")
    try:
        if _wa_window_open(phone):
            await _wa_send(phone, "✅ EcomVital is connected. Ask me anything, e.g. “check all store health”.")
            return {"sent": "text"}
        await whatsapp.send_template(phone, "WhatsApp connected")
        return {"sent": "template", "note": "Outside the 24-hour window, so the template was sent (needs Meta's approval first)."}
    except whatsapp.WAError as e:
        raise HTTPException(400, str(e))


@app.get("/api/googleads/check")
async def api_googleads_check(store: int):
    """Last 30 days of one store's Google spend four ways, to find where figures differ:
    Google's own "Last 30 days" (account clock, yesterday back), every campaign, the product report the
    desk uses, and the dashboard's store-clock days. All in the ad account's currency."""
    st = next((x for x in db.list_stores() if x["id"] == store and x.get("google_customer_id")), None)
    if not st:
        raise HTTPException(404, "Store has no Google Ads account.")
    auth = db.get_google_auth()
    if not auth:
        raise HTTPException(400, "Google isn't connected.")
    args = (auth["refresh_token"], st["google_customer_id"], st["google_login_cid"])
    probe, cur, atz = await gads.campaign_spend(*args, date.today().isoformat(), date.today().isoformat())
    try:
        a_today = datetime.now(ZoneInfo(atz)).date()
    except (ZoneInfoNotFoundError, ValueError):
        a_today = date.today()
    g_end, g_start = a_today - timedelta(days=1), a_today - timedelta(days=30)
    s_today = _now_in(st).date()
    d_start = s_today - timedelta(days=29)
    try:
        camps, cur, atz = await gads.campaign_spend(*args, g_start.isoformat(), g_end.isoformat())
        prods, _ = await gads.product_report(*args, d_start.isoformat(), s_today.isoformat())
        camps_desk, _, _ = await gads.campaign_spend(*args, d_start.isoformat(), s_today.isoformat())
        dash = await gads.daily_spend(*args, d_start.isoformat(), s_today.isoformat(), st["timezone"])
    except gads.GoogleAdsError as e:
        raise HTTPException(400, str(e))
    by = {}
    for c in camps:
        b = by.setdefault(c["campaign"], {**c, "cost": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "value": 0.0})
        for k in ("cost", "clicks", "impressions", "conversions", "value"):
            b[k] += c[k]
    return {"store": st["name"].strip(), "account": st.get("google_account_name") or st["google_customer_id"],
            "currency": cur, "account_timezone": atz, "store_timezone": st["timezone"], "store_currency": st["currency"],
            "google_last30": {"start": g_start.isoformat(), "end": g_end.isoformat(), "cost": round(sum(c["cost"] for c in camps), 2)},
            "campaigns": sorted(({**v, "cost": round(v["cost"], 2)} for v in by.values() if v["cost"]), key=lambda x: -x["cost"]),
            "desk_window": {"start": d_start.isoformat(), "end": s_today.isoformat(),
                            "all_campaigns": round(sum(c["cost"] for c in camps_desk), 2),
                            "products_only": round(sum(r["cost"] for r in prods), 2),
                            "dashboard_store_days": round(sum(dash.values()), 2)},
            "google_tax_pct": st.get("google_tax_pct")}


@app.put("/api/googleads/settings/{store_id}")
def api_googleads_settings(store_id: int, payload: dict):
    try:
        v = max(0.0, min(10000.0, float(payload.get("min_spend"))))
    except (TypeError, ValueError):
        raise HTTPException(400, "Type the minimum spend.")
    db.set_setting(f"gads_min:{store_id}", str(v))
    return {"ok": True}


# ---------------------------------------------------------------- Apps → Session Recordings

# ---------------------------------------------------------------- Checkout (own payment page, Airwallex)

_co_locks: dict = defaultdict(asyncio.Lock)
_co_hits: dict = defaultdict(list)          # ip -> start times (card-testing guard)


def _co_store(sid: int):
    return next((x for x in db.list_stores() if x["id"] == sid), None)


@app.get("/checkout", response_class=HTMLResponse)
def checkout_page():
    return _page("checkout.html", ("checkout.js", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/checkout")
def api_checkout(store: int = None):
    k = co.keys()
    stores = [{"id": x["id"], "name": x["name"].strip(), "currency": x["currency"]} for x in db.list_stores()
              if not demo.is_demo(x) and x.get("access_token")]
    return {"offers": co.offers(store), "stores": stores, "test": co.test_mode(), "cap": co.CAP,
            "keys_set": bool(k.get("client_id") and k.get("api_key")), "webhook_set": bool(k.get("webhook_secret")),
            "webhook_url": f"{BASE_URL}/pay/webhook", "base_url": BASE_URL, **co.listing(store)}


@app.get("/api/checkout/variants")
async def api_checkout_variants(store: int, q: str = ""):
    st = _co_store(store)
    if not st:
        raise HTTPException(404, "No such store.")
    got = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(
        "query P($q: String!) { products(first: 10, query: $q, sortKey: RELEVANCE) { nodes { id title status"
        " featuredMedia { preview { image { url } } } variants(first: 30) { nodes { id title price } } } } }",
        {"q": q or ""})
    out = []
    for p in got["products"]["nodes"]:
        img = (((p.get("featuredMedia") or {}).get("preview") or {}).get("image") or {}).get("url")
        for v in p["variants"]["nodes"]:
            out.append({"variant_id": v["id"], "product_title": p["title"] + ("" if v["title"] == "Default Title" else f" - {v['title']}"),
                        "price": float(v["price"] or 0), "image": img, "status": p["status"]})
    return {"variants": out, "currency": st["currency"]}


@app.post("/api/checkout/offer")
def api_checkout_offer(payload: dict):
    if not payload.get("id"):
        st = _co_store(int(payload.get("store_id") or 0))
        if not st:
            raise HTTPException(400, "Pick a store.")
        payload["currency"] = st["currency"]
    try:
        return co.save_offer(payload)
    except co.CheckoutError as e:
        raise HTTPException(400, str(e))


@app.post("/api/checkout/sub/{sid}")
async def api_checkout_sub(sid: int, payload: dict):
    s = co.sub(sid)
    if not s:
        raise HTTPException(404, "No such subscription.")
    act = payload.get("action")
    if act == "cancel":
        co.update_sub(sid, status="cancelled", ended_at=co._now())
    elif act == "pause":
        co.update_sub(sid, status="paused")
    elif act in ("resume", "restart"):
        co.update_sub(sid, status="active", fails=0, last_error=None,
                      next_at=max(s["next_at"], co._now()) if act == "resume" else co._now())
    elif act == "charge_now":
        if s["status"] != "active":
            raise HTTPException(400, "Only an active subscription can be charged.")
        return await _co_renew(s)
    else:
        raise HTTPException(400, "Unknown action.")
    return co.sub(sid) | {"token": None, "method_id": None}


@app.post("/api/checkout/order/{ref}")
async def api_checkout_order(ref: str, payload: dict):
    """Owner / manager: refund a checkout payment, or create its Shopify order again after a Shopify error."""
    od = co.order(ref=ref)
    if not od or not od["intent_id"]:
        raise HTTPException(404, "No such payment.")
    try:
        if payload.get("action") == "refund":
            await co.refund(od["intent_id"], reason=payload.get("reason") or "Refund from EcomVital")
            co.update_order(od["id"], status="refunded")
        elif payload.get("action") == "reorder":
            await _co_complete(ref, od["intent_id"])
        else:
            raise HTTPException(400, "Unknown action.")
    except co.CheckoutError as e:
        raise HTTPException(400, str(e))
    return co.order(ref=ref)


@app.get("/api/settings/checkout")
def api_checkout_keys():
    k = co.keys()
    return {"client_id": (k.get("client_id") or "")[:4] + "…" if k.get("client_id") else "", "account_id": k.get("account_id") or "",
            "api_key_set": bool(k.get("api_key")), "webhook_set": bool(k.get("webhook_secret")), "test": co.test_mode()}


@app.put("/api/settings/checkout")
async def api_checkout_keys_put(payload: dict):
    """Owner: the checkout's own Airwallex key (blank fields keep what's saved), test mode."""
    k = co.keys()
    for f in ("client_id", "api_key", "account_id", "webhook_secret"):
        v = (payload.get(f) or "").strip()
        if v:
            k[f] = v
        elif f == "account_id" and payload.get("clear_account"):
            k.pop(f, None)
    db.set_setting("co_awx", json.dumps(k))
    if "test" in payload:
        db.set_setting("co_test", "1" if payload["test"] else "0")
    if payload.get("check") and k.get("client_id"):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await awx._token(client, k["client_id"], k["api_key"], k.get("account_id") or None)
            await co._call("GET", "/api/v1/pa/payment_intents?page_size=1", None, "payments")
        except (co.CheckoutError, awx.AirwallexError) as e:
            raise HTTPException(400, str(e))
    return api_checkout_keys()


# public: the payment page, its two calls, the customer's manage page, Airwallex's webhook

@app.get("/pay/{slug}", response_class=HTMLResponse)
def pay_page(slug: str):
    o = co.offer_by_slug(slug)
    if not o:
        return HTMLResponse("<!doctype html><meta charset='utf-8'><p style='font-family:sans-serif'>This page isn't available.</p>", 404)
    st = _co_store(o["store_id"]) or {}
    with open(os.path.join(STATIC_DIR, "pay.html")) as f:
        page = f.read()
    data = co.public_offer(o) | {"store": (st.get("name") or "").strip()}
    page = page.replace("__OFFER__", json.dumps(data).replace("</", "<\\/")).replace("__STORE__", html.escape(data["store"]))
    for name in ("pay.js", "pay.css"):
        page = page.replace(f"/static/{name}", f"/static/{name}?v={_stamp(name)}")
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@app.post("/pay/api/start")
async def pay_start(request: Request, payload: dict):
    """The shopper's details -> an Airwallex payment (and customer, for a subscription) for the price set here."""
    ip = _client_ip(request)
    now = time.time()
    _co_hits[ip] = [t for t in _co_hits[ip] if now - t < 3600] + [now]
    if len(_co_hits[ip]) > 12:
        raise HTTPException(429, "Too many tries. Please wait a while and try again.")
    o = co.offer_by_slug(payload.get("slug") or "")
    if not o:
        raise HTTPException(404, "This offer isn't available.")
    plan = "first" if payload.get("plan") == "sub" else "one"
    price = o["sub_price"] if plan == "first" else o["one_price"]
    if not price:
        raise HTTPException(400, "That option isn't available.")
    addr = payload.get("address") or {}
    who = {"email": (payload.get("email") or "").strip(), "name": (payload.get("name") or "").strip(),
           "phone": (payload.get("phone") or "").strip(),
           "address": {k: str(addr.get(k) or "").strip()[:120] for k in ("address1", "address2", "city", "province", "zip", "country")}}
    if "@" not in who["email"] or not who["name"] or not who["address"]["address1"] or not who["address"]["city"]:
        raise HTTPException(400, "Please fill in your email, name and address.")
    if who["address"]["country"] not in o["countries"].split(","):
        raise HTTPException(400, "Sorry, we don't ship there.")
    if plan == "first" and not payload.get("agree"):
        raise HTTPException(400, "Please tick the box to agree to the subscription terms.")
    amount = round(price + (o["shipping"] or 0), 2)
    if co.test_mode() and amount > co.CAP:
        raise HTTPException(400, f"Test mode: payments over {co.CAP:.2f} are blocked.")
    od = None
    try:
        cust = await co.create_customer(who["email"], who["name"], who["phone"]) if plan == "first" else None
        od = co.new_order(o, plan, amount, who, customer_id=cust and cust["id"])
        intent = await co.create_intent(amount, o["currency"], od["ref"], cust and cust["id"],
                                        descriptor=(_co_store(o["store_id"]) or {}).get("name", "").strip(),
                                        metadata={"ref": od["ref"], "kind": plan})
    except co.CheckoutError as e:
        if od:
            co.update_order(od["id"], status="failed", error=str(e)[:300])
        raise HTTPException(400, str(e))
    co.update_order(od["id"], intent_id=intent["id"])
    return {"ref": od["ref"], "intent_id": intent["id"], "client_secret": intent["client_secret"],
            "currency": o["currency"], "amount": amount, "customer_id": cust and cust["id"],
            "country": who["address"]["country"], "env": "prod"}


async def _co_shopify(o: dict, od: dict, intent_id: str, token: str = None, shipping: float = None):
    st = _co_store(o["store_id"])
    try:
        got = await ShopifyClient(st["shop_domain"], st["access_token"]).gql(co.ORDER_CREATE, {
            "order": co.shopify_order_input(o, od, intent_id, token, BASE_URL, shipping),
            "options": {"sendReceipt": True, "sendFulfillmentReceipt": True, "inventoryBehaviour": "DECREMENT_OBEYING_POLICY"}})
        res = got["orderCreate"]
        if res.get("userErrors"):
            raise ShopifyError("; ".join(e["message"] for e in res["userErrors"]))
        co.update_order(od["id"], status="ordered", shopify_id=res["order"]["id"], shopify_name=res["order"]["name"], error=None)
    except Exception as e:
        # paid, but Shopify didn't take the order: the desk shows it with "Create the order again"
        co.update_order(od["id"], status="paid", error=f"Shopify order not created: {str(e)[:300]}")


async def _co_complete(ref: str, intent_id: str, consent_id: str = None) -> dict:
    """After the shopper paid: check with Airwallex, start the subscription, create the Shopify order. Safe to run
    twice (the page and the webhook both call it)."""
    async with _co_locks[ref]:
        od = co.order(ref=ref)
        if not od or od["intent_id"] != intent_id:
            raise HTTPException(404, "Payment not found.")
        if od["status"] == "ordered":
            return od
        intent = await co.get_intent(intent_id)
        if not co.intent_paid(intent, od):
            raise HTTPException(409, f"The payment isn't complete ({intent.get('status')}).")
        if od["status"] == "started":
            co.update_order(od["id"], status="paid", paid_at=co._now())
        o = co.offer(od["offer_id"])
        token = None
        if od["kind"] == "first":
            s = co.sub(od["sub_id"]) if od["sub_id"] else None
            if not s:
                consent_id = intent.get("payment_consent_id") or consent_id
                mid, mtype = co.method_of(intent)
                if consent_id:
                    try:
                        c = await co.get_consent(consent_id)
                        if c.get("customer_id") and c["customer_id"] != od["customer_id"]:
                            consent_id = None
                        else:
                            pm = c.get("payment_method") or {}
                            mid, mtype = pm.get("id") or mid, pm.get("type") or mtype
                    except co.CheckoutError:
                        pass
                s = co.new_sub(o, co.order(ref=ref), consent_id, mid, mtype)
            token = s["token"]
        await _co_shopify(o, co.order(ref=ref), intent_id, token)
        return co.order(ref=ref) | {"manage": f"{BASE_URL}/pay/manage/{token}" if token else None}


@app.post("/pay/api/done")
async def pay_done(payload: dict):
    try:
        od = await _co_complete(payload.get("ref") or "", payload.get("intent_id") or "", payload.get("consent_id"))
    except co.CheckoutError as e:
        raise HTTPException(400, str(e))
    s = co.sub(od["sub_id"]) if od.get("sub_id") else None
    return {"ok": True, "order": od.get("shopify_name"), "pending": od["status"] != "ordered",
            "manage": f"{BASE_URL}/pay/manage/{s['token']}" if s else None, "next": s and s["next_at"]}


async def _co_renew(s: dict) -> dict:
    """Charge one subscription's next delivery with the saved card and create its Shopify order."""
    o = co.offer(s["offer_id"])
    amount = round(s["price"] + (s["shipping"] or 0), 2)
    who = {"email": s["email"], "name": s["name"], "phone": s["phone"], "address": json.loads(s["address"] or "{}")}
    od = co.new_order(o | {"currency": s["currency"]}, "renewal", amount, who, sub_id=s["id"], customer_id=s["customer_id"])
    try:
        if not s.get("method_id"):
            raise co.CheckoutError("No saved card for this subscription.")
        intent = await co.charge_saved(s, amount, od["ref"])
        co.update_order(od["id"], intent_id=intent.get("id"))
        if not co.intent_paid(intent, od):
            raise co.CheckoutError(f"Payment {intent.get('status') or 'failed'}")
        co.update_order(od["id"], status="paid", paid_at=co._now())
        nxt = (datetime.now(timezone.utc) + timedelta(days=s["interval_days"])).isoformat(timespec="seconds")
        co.update_sub(s["id"], next_at=nxt, fails=0, last_error=None, renewals=s["renewals"] + 1)
        await _co_shopify(o, co.order(ref=od["ref"]), intent["id"], s["token"], s["shipping"])
    except Exception as e:
        fails = s["fails"] + 1
        co.update_order(od["id"], status="failed", error=str(e)[:300])
        if fails > len(co.RETRY_DAYS):
            co.update_sub(s["id"], fails=fails, last_error=str(e)[:300], status="failed", ended_at=co._now())
        else:
            co.update_sub(s["id"], fails=fails, last_error=str(e)[:300],
                          next_at=(datetime.now(timezone.utc) + timedelta(days=co.RETRY_DAYS[fails - 1])).isoformat(timespec="seconds"))
    return co.order(ref=od["ref"])


async def _co_loop():
    await asyncio.sleep(300)
    while True:
        try:
            if co.keys().get("api_key"):
                for s in co.due_subs():
                    await _co_renew(s)
        except Exception as e:
            print(f"Checkout renewals: {e}", flush=True)
        await asyncio.sleep(600)


@app.get("/pay/manage/{token}", response_class=HTMLResponse)
def pay_manage_page(token: str):
    s = co.sub(token=token)
    if not s:
        return HTMLResponse("<!doctype html><meta charset='utf-8'><p style='font-family:sans-serif'>This link isn't valid.</p>", 404)
    with open(os.path.join(STATIC_DIR, "pay-manage.html")) as f:
        page = f.read()
    return HTMLResponse(page.replace("__SUB__", json.dumps(_pay_sub_view(s)).replace("</", "<\\/"))
                        .replace("/static/pay.css", f"/static/pay.css?v={_stamp('pay.css')}"), headers={"Cache-Control": "no-cache"})


def _pay_sub_view(s: dict) -> dict:
    o = co.offer(s["offer_id"]) or {}
    st = _co_store(s["store_id"]) or {}
    return {"store": (st.get("name") or "").strip(), "product": o.get("title"), "image": o.get("image"),
            "price": s["price"], "shipping": s["shipping"], "currency": s["currency"], "every": s["interval_days"],
            "status": s["status"], "next": s["next_at"], "email": s["email"]}


@app.post("/pay/api/manage/{token}")
def pay_manage(token: str, payload: dict):
    s = co.sub(token=token)
    if not s:
        raise HTTPException(404, "This link isn't valid.")
    act = payload.get("action")
    if act == "cancel" and s["status"] in ("active", "paused"):
        co.update_sub(s["id"], status="cancelled", ended_at=co._now())
    elif act == "skip" and s["status"] == "active":
        nxt = datetime.fromisoformat(s["next_at"]) + timedelta(days=s["interval_days"])
        co.update_sub(s["id"], next_at=nxt.isoformat(timespec="seconds"))
    elif act == "pause" and s["status"] == "active":
        co.update_sub(s["id"], status="paused")
    elif act == "resume" and s["status"] == "paused":
        co.update_sub(s["id"], status="active", next_at=max(s["next_at"], co._now()))
    else:
        raise HTTPException(400, "That can't be done right now.")
    return _pay_sub_view(co.sub(token=token))


@app.post("/api/whatsapp/profile")
async def api_whatsapp_profile(payload: dict):
    """Owner: set the WhatsApp number's profile (EcomVital logo, about line, description, website, email)."""
    img = None
    if payload.get("logo", True):
        with open(os.path.join(STATIC_DIR, "brand", "ecomvital-640.jpg"), "rb") as f:
            img = f.read()
    try:
        return await whatsapp.set_profile(db.get_setting("wa_app_id") or "", img, "image/jpeg",
                                          about=payload.get("about") or "EcomVital assistant",
                                          description=payload.get("description") or "EcomVital: profit, ads and store operations assistant for our team.",
                                          websites=payload.get("websites") or ["https://www.ecomvital.com"],
                                          email=payload.get("email") or "hello@ecomvital.com")
    except whatsapp.WAError as e:
        raise HTTPException(400, str(e))


@app.post("/pay/webhook")
async def pay_webhook(request: Request):
    """Airwallex's notifications (signed with the webhook's secret). Backup for a shopper who closed the page
    before we heard back: a paid first payment still gets its subscription and Shopify order."""
    body = await request.body()
    if not co.webhook_ok(body, request.headers.get("x-timestamp", ""), request.headers.get("x-signature", "")):
        raise HTTPException(401, "Bad signature.")
    try:
        ev = json.loads(body)
    except ValueError:
        return {"ok": True}
    obj = (ev.get("data") or {}).get("object") or {}
    if ev.get("name") == "payment_intent.succeeded" and obj.get("id"):
        od = co.order(intent_id=obj["id"])
        if od and od["kind"] in ("one", "first") and od["status"] != "ordered":
            async def later():
                await asyncio.sleep(20)          # let the page's own call finish first
                try:
                    await _co_complete(od["ref"], obj["id"])
                except Exception as e:
                    print(f"Checkout webhook {od['ref']}: {e}", flush=True)
            asyncio.create_task(later())
    return {"ok": True}


@app.get("/recordings", response_class=HTMLResponse)
def recordings_page():
    return _page("recordings.html", ("recordings.js", "nav.js", "styles.css", "skin.css", "chat.js"))


async def _script_tag(store, filename: str, on: bool) -> str:
    """Add or remove one of our storefront scripts (ScriptTag). Returns an error message or ''."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    try:
        tags = (await client.gql(SCRIPT_TAGS))["scriptTags"]["nodes"]
        ours = [t for t in tags if filename in (t.get("src") or "")]
        if on and not ours:
            r = await client.gql(SCRIPT_CREATE, {"i": {"src": f"{BASE_URL}/static/{filename}",
                                                       "displayScope": "ONLINE_STORE", "cache": False}})
            errs = r["scriptTagCreate"]["userErrors"]
            if errs:
                return "; ".join(e["message"] for e in errs)
        if not on:
            for t in ours:
                await client.gql(SCRIPT_DELETE, {"id": t["id"]})
    except Exception as e:
        return str(e)[:200]
    return ""


@app.post("/api/storefront/repoint")
async def api_storefront_repoint():
    """Owner: after an address change, point every store's EcomVital storefront scripts (size charts, recorder) at
    the current address. Each old ScriptTag is replaced by one with the same file on BASE_URL."""
    out = {}
    for st in [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]:
        client = ShopifyClient(st["shop_domain"], st["access_token"])
        done = []
        try:
            tags = (await client.gql(SCRIPT_TAGS))["scriptTags"]["nodes"]
            for t in tags:
                src = t.get("src") or ""
                name = next((f for f in ("sizechart-widget.js", "recorder.js") if f in src), None)
                if not name or src.startswith(f"{BASE_URL}/"):
                    continue
                r = await client.gql(SCRIPT_CREATE, {"i": {"src": f"{BASE_URL}/static/{name}", "displayScope": "ONLINE_STORE",
                                                           "cache": False}})
                if r["scriptTagCreate"]["userErrors"]:
                    done.append(f"{name}: " + "; ".join(e["message"] for e in r["scriptTagCreate"]["userErrors"]))
                    continue
                await client.gql(SCRIPT_DELETE, {"id": t["id"]})
                done.append(f"{name}: moved")
        except Exception as e:
            done.append(f"error: {str(e)[:150]}")
        out[st["name"].strip()] = done or ["nothing to move"]
    return out


@app.post("/proxy/track/rec")
async def proxy_recording(request: Request, sid: str = "", seq: int = 0, z: int = 0):
    """One chunk of a shopper's session, from the store's app proxy (signed by Shopify)."""
    query = dict(request.query_params)
    store = next((x for x in db.list_stores() if x["shop_domain"].lower() == (query.get("shop") or "").lower()), None)
    keys = _shopify_app(store["shop_domain"]) if store else None
    if not store or not keys or not trackpage.verify_proxy(query, keys[1]) or not recordings.valid_sid(sid):
        return JSONResponse({"error": "Not allowed."}, status_code=401)
    cfg = recordings.config(store["id"])
    if not cfg["on"]:
        return JSONResponse({"stop": True})
    body = await request.body()
    if len(body) > 4 * 1024 * 1024:
        return JSONResponse({"error": "Too big."}, status_code=413)
    try:
        if z:
            import zlib
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = d.decompress(body, 20 * 1024 * 1024)
            if d.unconsumed_tail:
                return JSONResponse({"error": "Too big."}, status_code=413)
        payload = json.loads(body)
    except (ValueError, Exception):
        return JSONResponse({"error": "Bad chunk."}, status_code=400)
    with db._conn() as con:
        known = con.execute("SELECT 1 FROM rec_sessions WHERE sid = ?", (sid,)).fetchone()
    if not known and recordings.started_today(store["id"]) >= cfg["per_day"]:
        return JSONResponse({"stop": True})
    ok = await asyncio.to_thread(recordings.save_chunk, store["id"], sid, max(0, min(seq, 99999)), payload)
    return JSONResponse({"ok": ok, "stop": not ok})


_rec_clean = {"at": 0.0}


@app.get("/api/recordings")
async def api_recordings(store: str = "all", days: int = 7, device: str = "", seen: str = ""):
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]
    if time.time() - _rec_clean["at"] > 3600:
        _rec_clean["at"] = time.time()
        await asyncio.to_thread(recordings.cleanup, [x["id"] for x in stores])
    names = {x["id"]: x["name"].strip() for x in stores}
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))).isoformat()
    q, args = "SELECT * FROM rec_sessions WHERE started >= ?", [since]
    if store != "all":
        q += " AND store_id = ?"
        args.append(int(store))
    if device in ("mobile", "desktop"):
        q += " AND mobile = ?"
        args.append(1 if device == "mobile" else 0)
    if seen == "cart":
        q += " AND cart = 1"
    elif seen == "product":
        q += " AND product = 1"
    elif seen == "new":
        q += " AND watched = 0"
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY started DESC LIMIT 400", args)]
        totals = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT store_id, COUNT(*), SUM(bytes) FROM rec_sessions GROUP BY store_id")}
    for r in rows:
        r["store"] = names.get(r["store_id"], "?")
        r["pages"] = json.loads(r["pages"] or "[]")
        r["seconds"] = round(((r["last_ts"] or 0) - (r["first_ts"] or 0)) / 1000) if r["first_ts"] else 0
    return {"sessions": rows,
            "stores": [{"id": x["id"], "name": names[x["id"]], "domain": x["shop_domain"], "cfg": recordings.config(x["id"]),
                        "today": recordings.started_today(x["id"]), "kept": (totals.get(x["id"]) or (0, 0))[0],
                        "mb": round(((totals.get(x["id"]) or (0, 0))[1] or 0) / 1048576, 1)} for x in stores]}


@app.get("/api/recordings/events")
async def api_recording_events(sid: str):
    ev = await asyncio.to_thread(recordings.events, sid)
    if not ev:
        raise HTTPException(404, "Nothing recorded for this session (yet).")
    with db._conn() as con:
        con.execute("UPDATE rec_sessions SET watched = 1 WHERE sid = ?", (sid,))
    return JSONResponse(ev)


@app.delete("/api/recordings")
def api_recording_delete(sid: str):
    recordings.delete(sid)
    return {"ok": True}


@app.put("/api/recordings/settings/{store_id}")
async def api_recording_settings(store_id: int, payload: dict):
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not store:
        raise HTTPException(404, "Store not found.")
    was = recordings.config(store_id)["on"]
    cfg = recordings.save_config(store_id, payload)
    if cfg["on"] != was:
        err = await _script_tag(store, "recorder.js", cfg["on"])
        if err:
            recordings.save_config(store_id, {"on": was})
            raise HTTPException(400, f"Shopify didn't accept the change: {err}")
    return {"ok": True, "cfg": cfg}


@app.get("/api/sizecharts/settings")
def api_sizechart_settings():
    return {"stores": [{"id": x["id"], "name": x["name"].strip(), "domain": x["shop_domain"],
                        "lang": db.get_setting(f"track_lang:{x['id']}") or "en", "cfg": _sc_cfg(x["id"]),
                        "embed_link": _sc_embed_link(x) if _sc_cfg(x["id"]).get("embed") else None}
                       for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")],
            "text": SC_TEXT}


@app.put("/api/sizecharts/settings/{store_id}")
async def api_sizechart_settings_save(store_id: int, payload: dict):
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not store:
        raise HTTPException(404, "Store not found.")
    cfg = _sc_cfg(store_id)
    was = cfg["enabled"]
    for k, v in (payload or {}).items():
        if k in SC_DEFAULTS:
            cfg[k] = v
    err = ""
    if bool(cfg["enabled"]) != bool(was):
        err = await _sc_script(store, bool(cfg["enabled"]))
        if err and "theme app extension" in err.lower():
            # newer store apps may not add script tags: the size chart comes as the app embed instead
            # (shopify-app/extensions/ecomvital-embed), switched on once in the theme editor
            cfg["embed"], err = True, ""
        if err:
            cfg["enabled"] = was
    db.set_setting(f"sizechart_cfg:{store_id}", json.dumps(cfg))
    _sc_cache.clear()
    if err:
        raise HTTPException(400, f"Couldn't switch the size chart on the store: {err}")
    return {"ok": True, "cfg": cfg, "embed_link": _sc_embed_link(store) if cfg.get("embed") and cfg["enabled"] else None}


def _sc_embed_link(store) -> str:
    """Theme editor with EcomVital's size chart app embed ready to switch on (the owner clicks Save)."""
    app_ = db.get_shopify_app(store["shop_domain"]) or {}
    cid = app_.get("client_id") or ""
    return (f"https://{store['shop_domain']}/admin/themes/current/editor?context=apps"
            + (f"&activateAppId={cid}/sizechart" if cid else ""))


_sc_cache: dict = {}       # (store_id, handle) -> (time, product info)
_advise_hits: dict = {}    # ip -> [times]

PRODUCT_INFO = """query P($q: String!) { products(first: 1, query: $q) { nodes {
  id title tags productType vendor collections(first: 100) { nodes { id } } } } }"""


async def _sc_product(store, handle: str):
    key = (store["id"], handle)
    hit = _sc_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = await ShopifyClient(store["shop_domain"], store["access_token"]).gql(
        PRODUCT_INFO, {"q": f"handle:'{handle}'"})
    nodes = r["products"]["nodes"]
    info = nodes[0] if nodes else None
    _sc_cache[key] = (time.time(), info)
    return info


def _sc_store_rules(store_id: int) -> list:
    with db._conn() as con:
        return [dict(r) for r in con.execute(
            "SELECT r.kind, r.value, c.id, c.name, c.blocks, c.countries FROM size_chart_rules r"
            " JOIN size_charts c ON c.id = r.chart_id WHERE r.store_id = ? AND c.status = 'active'", (store_id,))]


def _sc_match(store_id: int, p: dict, country: str, rules: list = None):
    """The chart for this product: product > collection > tag > type > vendor > all."""
    rules = _sc_store_rules(store_id) if rules is None else rules
    tags = {t.lower() for t in p.get("tags") or []}
    cols = {c["id"] for c in ((p.get("collections") or {}).get("nodes") or [])}
    order = ["product", "collection", "tag", "type", "vendor", "all"]

    def fits(r):
        k, v = r["kind"], (r["value"] or "")
        return ((k == "product" and v == p["id"]) or (k == "collection" and v in cols) or
                (k == "tag" and v.lower() in tags) or (k == "type" and v.lower() == (p.get("productType") or "").lower()) or
                (k == "vendor" and v.lower() == (p.get("vendor") or "").lower()) or k == "all")

    def here(r):
        cs = [c for c in (r["countries"] or "").split(",") if c]
        return not cs or not country or country.upper() in cs
    hits = [r for r in rules if fits(r) and here(r)]
    hits.sort(key=lambda r: order.index(r["kind"]))
    return hits[0] if hits else None


async def _sizechart_for(store, handle: str, country: str):
    cfg = _sc_cfg(store["id"])
    if not cfg["enabled"] or not handle:
        return JSONResponse({"chart": None})
    try:
        p = await _sc_product(store, re.sub(r"[^a-zA-Z0-9\-_.%]", "", handle)[:255])
    except Exception:
        return JSONResponse({"chart": None})
    hit = _sc_match(store["id"], p, country) if p else None
    if not hit:
        return JSONResponse({"chart": None})
    blocks = json.loads(hit["blocks"])
    lang = db.get_setting(f"track_lang:{store['id']}") or "en"
    text = SC_TEXT.get(lang, SC_TEXT["en"])
    return JSONResponse({"chart": {"id": hit["id"], "name": hit["name"], "html": sizecharts.render(blocks),
                                   "html_in": sizecharts.render(blocks, True) if sizecharts.has_cm(blocks) else None},
                         "cfg": {k: cfg[k] for k in SC_DEFAULTS if k != "enabled"},
                         "text": {**text, "label": cfg["label"] or text["label"]},
                         "advisor": bool(cfg["advisor"] and db.get_setting("anthropic_api_key"))},
                        headers={"Cache-Control": "public, max-age=120"})


@app.post("/proxy/track/sizechart/advise")
async def proxy_sizechart_advise(request: Request):
    """The 'Find my size' answer, through the store's app proxy."""
    query = dict(request.query_params)
    store = next((x for x in db.list_stores() if x["shop_domain"].lower() == (query.get("shop") or "").lower()), None)
    keys = _shopify_app(store["shop_domain"]) if store else None
    if not store or not keys or not trackpage.verify_proxy(query, keys[1]):
        return JSONResponse({"error": "Not allowed."}, status_code=401)
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or "?"
    now = time.time()
    hits = [t for t in _advise_hits.get(ip, []) if now - t < 3600]
    if len(hits) >= 20:
        return JSONResponse({"error": "Please try again later."}, status_code=429)
    _advise_hits[ip] = hits + [now]
    try:
        body = await request.json()
    except Exception:
        body = {}
    p = await _sc_product(store, re.sub(r"[^a-zA-Z0-9\-_.%]", "", str(body.get("handle") or ""))[:255])
    hit = _sc_match(store["id"], p, str(body.get("country") or "")) if p else None
    key = db.get_setting("anthropic_api_key")
    if not hit or not key:
        return JSONResponse({"error": "No size chart for this product."}, status_code=404)
    lang = LANG_NAME.get(db.get_setting(f"track_lang:{store['id']}") or "en", "English")
    answers = {k: str(body.get(k) or "")[:20] for k in ("height", "weight", "height_unit", "weight_unit", "fit", "body", "usual")}
    try:
        res = await sizecharts.advise(key, sizecharts.tables_text(json.loads(hit["blocks"])), answers, lang)
    except sizecharts.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    n = int(db.get_setting(f"sizechart_advice:{store['id']}") or 0) + 1
    db.set_setting(f"sizechart_advice:{store['id']}", str(n))
    return JSONResponse(res)


@app.post("/api/cash/airwallex")
async def api_add_airwallex(payload: dict):
    cid = (payload.get("client_id") or "").strip()
    key = (payload.get("api_key") or "").strip()
    label = (payload.get("label") or "").strip() or "Airwallex"
    if not cid or not key:
        raise HTTPException(400, "Paste both the Client ID and the API key from Airwallex.")
    # One key can cover several sub-accounts; each is read separately by its ID.
    # Lines may be just "acct_…" or "Store name acct_…" (the name labels it).
    named = {}
    for line in (payload.get("account_ids") or "").splitlines():
        m = re.search(r"(acct_[A-Za-z0-9_-]+)", line)
        if m:
            named[m.group(1)] = line.replace(m.group(1), "").strip(" \t-:,|")
    ids = list(named)
    targets = ids or [None]
    checked = await asyncio.gather(
        *[awx.snapshot(cid, key, lookback_days=1, account_id=a) for a in targets],
        return_exceptions=True)
    bad = [f"{a or 'main account'}: {e}" for a, e in zip(targets, checked) if isinstance(e, Exception)]
    if bad:
        raise HTTPException(400, "Couldn't read " + "; ".join(bad))
    for n, a in enumerate(targets, 1):
        name = label if len(targets) == 1 else (named.get(a) or f"{label} · {a[-6:]}")
        db.add_cash_connection("airwallex", name[:60], cid, key, a)
    currencies = sorted({b["currency"] for s in checked for b in s["balances"]})
    return {"ok": True, "accounts": len(targets), "currencies": currencies}


@app.post("/api/cash/paypal")
async def api_add_paypal(payload: dict):
    cid = (payload.get("client_id") or "").strip()
    secret = (payload.get("secret") or "").strip()
    label = (payload.get("label") or "").strip() or "PayPal"
    if not cid or not secret:
        raise HTTPException(400, "Paste both the Client ID and the Secret from PayPal.")
    try:
        snap = await paypal.snapshot(cid, secret)
    except paypal.PayPalError as e:
        raise HTTPException(400, str(e))
    db.add_cash_connection("paypal", label[:60], cid, secret)
    return {"ok": True, "accounts": 1, "currencies": sorted(b["currency"] for b in snap["balances"])}


@app.put("/api/cash/{connection_id}")
def api_rename_cash(connection_id: int, payload: dict):
    label = (payload.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "Give it a name.")
    db.rename_cash_connection(connection_id, label[:60])
    return {"ok": True}


@app.delete("/api/cash/{connection_id}")
def api_remove_cash(connection_id: int):
    db.delete_cash_connection(connection_id)
    return {"ok": True}


# ---------------------------------------------------------------- store groups

def _group_payload(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the group a name, like Meta stores.")
    ids = payload.get("store_ids") or []
    if not isinstance(ids, list):
        raise HTTPException(400, "Pick the stores for this group.")
    return name[:60], ids


@app.post("/api/groups")
def api_create_group(payload: dict):
    name, ids = _group_payload(payload)
    return {"id": db.save_group(name, ids)}


@app.put("/api/groups/{group_id}")
def api_update_group(group_id: int, payload: dict):
    name, ids = _group_payload(payload)
    try:
        db.save_group(name, ids, group_id)
    except KeyError:
        raise HTTPException(404, "That store group no longer exists.")
    return {"ok": True}


@app.delete("/api/groups/{group_id}")
def api_delete_group(group_id: int):
    db.delete_group(group_id)
    return {"ok": True}


# ---------------------------------------------------------------- currency

def _display_currency() -> str:
    """The currency every figure is shown in. Defaults to the one most stores use."""
    saved = db.get_setting("display_currency")
    if saved:
        return saved
    codes = [s["currency"] for s in db.list_stores()]
    return max(set(codes), key=codes.count) if codes else "AUD"


@app.put("/api/settings")
def api_settings(payload: dict):
    code = (payload.get("display_currency") or "").strip().upper()
    if code:
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise HTTPException(400, "Currency should be a three-letter code like AUD or EUR.")
        db.set_setting("display_currency", code)
    return {"ok": True, "display_currency": _display_currency()}


def _in_currency(rows, store, fx_table, base):
    """One store's daily rows, re-worked in the display currency.

    Sales and costs convert from the store's currency; each ad platform's spend
    converts from that ad account's own currency. The rows are then rebuilt
    through metrics.day, so every figure still comes from the one definition.
    """
    if not fx_table:
        return rows
    f_sales = fx.factor(fx_table, store["currency"], base)
    f_google = fx.factor(fx_table, store["google_currency"] or store["currency"], base)
    f_meta = fx.factor(fx_table, store["meta_currency"] or store["currency"], base)
    return [
        metrics.day(r["date"], r["sales"] * f_sales, r["orders"],
                    r["google_spend"] * f_google, r["meta_spend"] * f_meta,
                    r["cogs"] * f_sales, r["payment_fee"] * f_sales, r["shopify_fee"] * f_sales,
                    r["cogs_estimated"] * f_sales, r["reserve_held"] * f_sales,
                    r["paypal_sales"] * f_sales, paypal_fee=r["paypal_fee"] * f_sales,
                    airwallex_fee=r["airwallex_fee"] * f_sales, refunds=r["refunds"] * f_sales,
                    refund_count=r["refund_count"], chargebacks=r["chargebacks"] * f_sales)
        for r in rows
    ]


# ---------------------------------------------------------------- processing fees

def _fee_rates() -> dict:
    """The saved rate card, with any missing entries filled from the defaults."""
    rates = json.loads(json.dumps(metrics.DEFAULT_FEE_RATES))
    try:
        saved = json.loads(db.get_setting("fee_rates") or "{}")
    except ValueError:
        saved = {}
    for k, v in saved.items():
        if k == "paypal_fixed" and isinstance(v, dict):
            rates["paypal_fixed"].update({c: float(x) for c, x in v.items()})
        elif k in rates and not isinstance(rates[k], dict):
            rates[k] = float(v)
    return rates


@app.put("/api/fee-rates")
def api_fee_rates(payload: dict):
    rates = _fee_rates()
    for k, v in payload.items():
        try:
            if k == "paypal_fixed" and isinstance(v, dict):
                rates["paypal_fixed"].update({c.upper(): max(0.0, float(x)) for c, x in v.items()})
            elif k in rates and not isinstance(rates[k], dict):
                rates[k] = max(0.0, min(100.0, float(v))) if k.endswith("_pct") else max(0.0, float(v))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} needs to be a number.")
    db.set_setting("fee_rates", json.dumps(rates))
    _cache.clear()
    return {"ok": True, "fee_rates": rates}


_plans: dict[int, tuple[float, str]] = {}


async def _store_plan(store) -> str:
    """The store's Shopify plan, checked at most every six hours."""
    hit = _plans.get(store["id"])
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    try:
        plan = await ShopifyClient(store["shop_domain"], store["access_token"]).plan()
    except ShopifyError:
        plan = store["shopify_plan"] or ""
    if plan != store["shopify_plan"]:
        db.set_store_plan(store["id"], plan)
    _plans[store["id"]] = (time.time(), plan)
    return plan


async def _fee_context(store, notes):
    """Everything processing_fees needs for one store: rates, plan, HK$ value."""
    plan = await _store_plan(store)
    if plan and plan not in metrics.SHOPIFY_THIRD_PARTY_PCT:
        notes.append(f"{store['name']}: Shopify plan '{plan}' isn't in the fee table, "
                     "so Shopify's third-party fee is counted as 0%.")
    hkd = 1.0
    if store["currency"] != "HKD":
        try:
            hkd = fx.factor(await fx.table(store["currency"]), "HKD", store["currency"])
        except fx.FxError:
            hkd = 0.0
            notes.append(f"{store['name']}: couldn't get the HK$ exchange rate, so "
                         "Airwallex's fixed HK$ fee per order is left out for now.")
    return _fee_rates(), plan, hkd


def _fees_for(store, payments, ctx, notes):
    rates, plan, hkd = ctx
    unknown = sorted({g for g in payments if metrics.payment_method(g) == "other"})
    if unknown:
        notes.append(f"{store['name']}: some orders were paid with {', '.join(unknown)}, "
                     "which has no fee rate yet, so only Shopify's fee is counted for them.")
    return metrics.processing_fees(payments, rates, plan, store["currency"], hkd)


# ---------------------------------------------------------------- meta

def _business_label(accounts, fallback):
    """Name a connection by the ad accounts it reads. Meta only reveals the
    business name to tokens with business_management, which we don't ask for."""
    names = [a["name"] for a in accounts]
    if not names:
        return f"{fallback} · no ad accounts assigned yet"
    shown = ", ".join(names[:2])
    return shown + (f" +{len(names) - 2} more" if len(names) > 2 else "")


@app.post("/api/meta/token")
async def api_meta_token(payload: dict):
    """Connect one Meta business. Pasting a new token for a business that is
    already connected replaces its old one."""
    token = (payload.get("access_token") or "").strip()
    if not token:
        raise HTTPException(400, "Paste the System User access token from Meta Business Settings.")
    me = await meta.whoami(token)
    accounts = await meta.list_accounts(token)
    label = _business_label(accounts, me["name"])
    db.save_meta_connection(me["id"], token, me["name"], label)
    _cache.clear()
    return {"ok": True, "name": label, "accounts": len(accounts)}


@app.delete("/api/meta/connections/{connection_id}")
def api_meta_disconnect(connection_id: int):
    db.delete_meta_connection(connection_id)
    _cache.clear()
    return {"ok": True}


@app.get("/api/meta/check-manage")
async def api_meta_check_manage(store: int):
    """Owner check before EcomVital changes ads: what the store's Meta token may do, the Pages and pixels the
    ad account can use, and a campaign create in Meta's validate-only mode (checked by Meta, nothing made)."""
    st, conn = _task_meta(store)
    token, act = conn["access_token"], st["meta_account_id"]
    out = {"store": st["name"].strip(), "account_id": act, "account": st.get("meta_account_name") or ""}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            got = await meta._get(client, "me/permissions", token, {})
            out["permissions"] = sorted(p["permission"] for p in got.get("data", []) if p.get("status") == "granted")
        except meta.MetaAdsError as e:
            out["permissions_error"] = str(e)
        try:
            got = await meta._get(client, f"act_{act}", token, {"fields": "name,account_status,currency,user_tasks"})
            out["account_tasks"] = got.get("user_tasks") or []
            out["account_status"] = got.get("account_status")
        except meta.MetaAdsError as e:
            out["account_error"] = str(e)
        try:
            await meta.create_campaign(client, token, act, "EcomVital access check (not created)", validate=True)
            out["can_create"] = True
        except meta.MetaAdsError as e:
            out["can_create"] = False
            out["create_error"] = str(e)
    opts = await meta.launch_options(token, act)
    out.update({"pages": opts["pages"], "pixels": opts["pixels"], "errors": opts["errors"]})
    return out


@app.get("/api/meta/accounts")
async def api_meta_accounts():
    """Every ad account across every connected Meta business, for the dropdowns."""
    conns = db.list_meta_connections()
    if not conns:
        raise HTTPException(400, "Connect Meta first.")
    results = await asyncio.gather(
        *[meta.list_accounts(c["access_token"]) for c in conns], return_exceptions=True
    )
    taken = db.linked_meta_ids()
    accounts, seen, problems = [], set(), []
    for conn, got in zip(conns, results):
        if isinstance(got, Exception):
            problems.append(f"{conn['label'] or conn['name']}: {got}")
            continue
        label = _business_label(got, conn["name"])
        if label != conn["label"]:
            db.set_meta_label(conn["id"], label)
        for a in got:
            if a["account_id"] in seen:
                continue  # visible to two tokens; the first one is enough
            seen.add(a["account_id"])
            accounts.append({**a, "connection_id": conn["id"],
                             "linked_to": taken.get(a["account_id"])})
    accounts.sort(key=lambda a: a["name"].lower())
    return {"accounts": accounts, "problems": problems}


@app.post("/api/stores/{store_id}/meta")
def api_link_meta(store_id: int, payload: dict):
    aid = (payload.get("account_id") or "").strip()
    conn_id = payload.get("connection_id")
    if not aid:
        raise HTTPException(400, "Pick a Meta ad account.")
    if not db.get_meta_connection(conn_id):
        raise HTTPException(400, "That ad account's Meta connection is gone. Reopen Settings.")
    db.link_meta(store_id, aid, payload.get("name") or f"Account {aid}",
                 payload.get("currency") or "", int(conn_id), payload.get("timezone"))
    _cache.clear()
    return {"ok": True}


@app.delete("/api/stores/{store_id}/meta")
def api_unlink_meta(store_id: int):
    db.unlink_meta(store_id)
    _cache.clear()
    return {"ok": True}


# ---------------------------------------------------------------- dashboard

_dash: dict = {}          # (scope, start, end, compare, currency) -> (time, response)
_dash_busy: dict = {}     # same key -> the refresh running now (one at a time per view)
DASH_FRESH = 45           # seconds: answer straight from the last figures, no refresh
DASH_KEEP = 20 * 60       # up to this old: answer at once with them and refresh in the background


@app.get("/api/dashboard")
async def api_dashboard(scope: str = "all", start: str = None, end: str = None,
                        fresh: int = 0, compare: str = "period", currency: str = None):
    """The dashboard answers at once with the last figures for this view and refreshes them in the
    background ("refreshing": the page asks again a few seconds later). Refresh (fresh=1) waits."""
    key = (scope, start, end, compare, (currency or "").upper())

    async def run():
        try:
            out = await _dashboard_compute(scope, start, end, fresh, compare, currency)
            _dash[key] = (time.time(), out)
            return out
        finally:
            _dash_busy.pop(key, None)

    hit = _dash.get(key)
    age = time.time() - hit[0] if hit else None
    if hit and not fresh and age < DASH_FRESH:
        return {**hit[1], "cached_age": round(age)}
    if hit and not fresh and age < DASH_KEEP:
        if key not in _dash_busy:
            _dash_busy[key] = asyncio.create_task(run())
        return {**hit[1], "cached_age": round(age), "refreshing": True}
    task = _dash_busy.get(key)
    if task is None:
        task = _dash_busy[key] = asyncio.create_task(run())
    return await asyncio.shield(task)


async def _dashboard_compute(scope: str = "all", start: str = None, end: str = None,
                             fresh: int = 0, compare: str = "period", currency: str = None):
    """compare="same_time": when the range is just today, measure it against
    yesterday up to the same time of day, rather than all of yesterday."""
    stores = db.list_stores()
    if not stores:
        raise HTTPException(400, "Connect a store first.")

    # scope: "all", a store id, or "g<group id>" for a store group.
    group = None
    if scope.startswith("g"):
        group = next((g for g in db.list_groups() if f"g{g['id']}" == scope), None)
        if not group:
            raise HTTPException(404, "That store group no longer exists.")
        stores = [s for s in stores if s["id"] in group["store_ids"]]
        if not stores:
            raise HTTPException(400, f"The group {group['name']} has no stores in it yet. "
                                     "Add some in Settings → Store groups.")
    elif scope != "all":
        stores = [s for s in stores if str(s["id"]) == str(scope)]
        if not stores:
            raise HTTPException(404, "Store not found.")

    end = end or date.today().isoformat()
    start = start or (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if span < 1:
        raise HTTPException(400, "The end date has to be on or after the start date.")
    prev_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    prev_start = (date.fromisoformat(prev_end) - timedelta(days=span - 1)).isoformat()

    auth = db.get_google_auth()
    meta_conns = {c["id"]: c for c in db.list_meta_connections()}
    meta_of = lambda s: meta_conns.get(s["meta_connection_id"])
    if fresh:
        _cache.clear()

    # A single day, compared with the day before it up to the same point in
    # each store's own day (see _same_time_yesterday).
    same_time = compare == "same_time" and span == 1

    _timing.clear()
    t_all = time.time()
    if same_time:
        results = await asyncio.gather(*[
            _store_window(s, start, end, auth, meta_of(s)) for s in stores
        ])
        earlier = await asyncio.gather(*[
            _same_time_yesterday(s, prev_end, auth, meta_of(s)) for s in stores
        ])
    else:
        results = await asyncio.gather(*[
            _store_window(s, prev_start, end, auth, meta_of(s)) for s in stores
        ])
        earlier = [None] * len(stores)

    _timing["_stores"] = round(time.time() - t_all, 2)
    t = time.time()
    base = (currency or _display_currency()).upper()
    used = {c for s in stores
            for c in (s["currency"], s["meta_currency"], s["google_currency"]) if c}
    fx_table = await fx.table(base) if used - {base} else None

    _timing["_fx"] = round(time.time() - t, 2)
    t = time.time()
    over = await _paypal_over_limit(min(prev_start, start), end)
    _timing["_paypal_limit"] = round(time.time() - t, 2)
    t = time.time()
    warnings, current, previous, per_store = [], [], [], []
    for store, (rows, notes), cut in zip(stores, results, earlier):
        warnings.extend(notes)
        rows = [metrics.with_monthly_hold(r, over.get(r["date"], 0.0)) for r in rows]
        rows = _in_currency(rows, store, fx_table, base)
        cur = [r for r in rows if r["date"] >= start]
        if cut:
            pre, cut_notes = cut
            pre = [metrics.with_monthly_hold(r, over.get(r["date"], 0.0)) for r in pre]
            pre = _in_currency(pre, store, fx_table, base)
            warnings.extend(cut_notes)
        else:
            pre = [r for r in rows if r["date"] <= prev_end]
        current.append(cur)
        previous.append(pre)
        per_store.append({
            "id": store["id"],
            "name": store["name"],
            "currency": store["currency"],
            "cost_pct": store["cost_pct"],
            "google_account_name": store["google_account_name"],
            "meta_account_name": store["meta_account_name"],
            "totals": metrics.summarize(cur),
        })

    # All stores: refunds made straight in PayPal / Airwallex (Shopify never saw them) and
    # chargebacks, as their own line. Group and store views can't tell which store they're from.
    _timing["_per_store_maths"] = round(time.time() - t, 2)
    t = time.time()
    if scope == "all":
        try:
            outside = await _outside_refunds(stores, min(prev_start, start), end, base, fx_table, same_time)
        except Exception as e:
            outside = None
            warnings.append(f"Couldn't read PayPal / Airwallex refunds and chargebacks: {str(e)[:120]}")
        if outside:
            cur_rows = [r for r in outside if r["date"] >= start]
            pre_rows = [] if same_time else [r for r in outside if r["date"] <= prev_end]
            current.append(cur_rows)
            previous.append(pre_rows)
            if any(r["refunds"] or r["chargebacks"] for r in cur_rows):
                per_store.append({"id": None, "name": "PayPal / Airwallex (not in Shopify)", "currency": base,
                                  "cost_pct": None, "google_account_name": None, "meta_account_name": None,
                                  "outside": True, "totals": metrics.summarize(cur_rows)})

    _timing["_outside_refunds"] = round(time.time() - t, 2)
    t = time.time()
    series = metrics.with_ratios(metrics.blend(current))
    totals = metrics.summarize(series)
    prev_totals = metrics.summarize(metrics.blend(previous))

    _timing["_totals"] = round(time.time() - t, 2)
    t = time.time()
    short = await _short_history(stores, min(start, prev_start))
    _timing["_short_history"] = round(time.time() - t, 2)
    _timing["_TOTAL"] = round(time.time() - t_all, 2)
    if short:
        names = ", ".join(x["name"].strip() for x in short)
        warnings.append(
            f"Shopify only shares the last 60 days of orders with EcomVital for {names}, so "
            f"sales before {short[0]['_from']} show as zero (ad spend still counts). EcomVital "
            "now saves every day, so history grows from here; older days need Shopify's "
            "\"Read all orders\" approval for each store's app.")

    if fx_table and fx_table.get("stale"):
        warnings.append("Couldn't reach the exchange-rate service, so the last saved "
                        f"rates (from {fx_table.get('date')}) are being used.")

    return {
        "scope": scope,
        "title": "All stores" if scope == "all" else group["name"] if group else stores[0]["name"],
        "currency": base,
        # What each store's own currency is worth in the display currency.
        "fx": None if not fx_table else {
            "date": fx_table.get("date"),
            "source": fx_table.get("source"),
            "rates": {c: fx.factor(fx_table, c, base) for c in sorted(used - {base})},
        },
        "range": {"start": start, "end": end, "days": span},
        "previous": {
            "start": prev_start, "end": prev_end,
            # Time of day yesterday was cut at, on the (first) store's clock.
            "until": _now_in(stores[0]).strftime("%H:%M") if same_time else None,
        },
        "totals": totals,
        "platform_roas": metrics.platform_roas([{
            "platform": ("both" if x["google_account_name"] and x["meta_account_name"] else
                         "google" if x["google_account_name"] else
                         "meta" if x["meta_account_name"] else None),
            "totals": x["totals"]} for x in per_store]),
        "delta": metrics.compare(totals, prev_totals),
        "series": series,
        "stores": per_store if scope == "all" or group else [],
        "warnings": sorted(set(warnings)),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


_outside_cache: dict = {}


async def _outside_refunds(stores: list, start: str, end: str, base: str, fx_table, same_time: bool,
                           live: bool = False) -> list:
    """Daily rows (display currency, Hong Kong days) of money returned to customers outside Shopify:
    PayPal / Airwallex refunds that match no Shopify refund (same amount within 3 days), plus
    every chargeback / dispute debit (money won back counts negative, on the day it returns)."""
    ck = (start, end, base)
    hit = _outside_cache.get(ck)
    saved = db.outside_days(start, end, base) if not hit and not live else None
    if saved is not None:                             # kept fresh by _outside_loop every 15 minutes
        rows = [metrics.day(r["date"], 0.0, 0, 0.0, 0.0, 0.0, refunds=r["refunds"], refund_count=r["count"],
                            chargebacks=r["chargebacks"]) for r in saved
                if r["refunds"] or r["chargebacks"] or r["count"]]
        _outside_cache[ck] = (time.time(), rows)
    elif hit and time.time() - hit[0] < 300:
        rows = hit[1]
    else:
        since = datetime.combine(date.fromisoformat(start) - timedelta(days=3), clock.min, CASH_TZ)
        until = datetime.combine(date.fromisoformat(end) + timedelta(days=1), clock.min, CASH_TZ)
        moves, _problems = await _expense_moves(since, until)
        if not fx_table and any((m.get("currency") or base) != base for m in moves):
            fx_table = await fx.table(base)
        shop = []                                     # Shopify refunds paid back through PayPal / Airwallex
        for st in stores:
            if demo.is_demo(st):
                continue
            try:
                _d, recs = await ShopifyClient(st["shop_domain"], st["access_token"]).daily_refunds(
                    (date.fromisoformat(start) - timedelta(days=3)).isoformat(), end, st["timezone"])
            except Exception:
                continue
            for r in recs:
                shop.append({"amount": r["amount"], "currency": st["currency"],
                             "time": datetime.fromisoformat(r["time"]), "used": False})
        days, counted = {}, []
        for m in moves:
            line = m.get("line")
            if line not in ("Refunds to customers", "Disputes and chargebacks"):
                continue
            # Disputes count only once lost (owner, 16 Oct 2026): Airwallex cases from their outcome below
            # (_lost_disputes); in PayPal a reversal (T1106) is a lost claim. Holds and open chargebacks don't count.
            if line == "Disputes and chargebacks" and (not str(m.get("source", "")).startswith("PayPal")
                                                       or m.get("code") not in PP_LOST):
                continue
            when = m["time"].astimezone(CASH_TZ)
            d = when.date().isoformat()
            if not start <= d <= end:
                continue
            amt = -float(m.get("amount") or 0.0)          # money out is negative in the statement
            if line == "Refunds to customers":
                if amt <= 0:
                    continue
                twin = next((x for x in shop if not x["used"] and x["currency"] == m.get("currency")
                             and abs(x["amount"] - amt) < 0.02 and abs((x["time"] - when).total_seconds()) < 3 * 86400),
                            None)
                if twin:                                  # already counted from Shopify
                    twin["used"] = True
                    continue
            f = fx.factor(fx_table, m.get("currency") or base, base) if fx_table else 1.0
            row = days.setdefault(d, {"refunds": 0.0, "count": 0, "chargebacks": 0.0})
            if line == "Refunds to customers":
                row["refunds"] += amt * f
                row["count"] += 1
                counted.append({"amount": amt, "currency": m.get("currency"), "time": when, "used": False})
            else:
                row["chargebacks"] += amt * f
        if not fx_table:
            fx_table = await fx.table(base)
        for d, amt in await _lost_disputes(start, end, base, fx_table, counted):
            row = days.setdefault(d, {"refunds": 0.0, "count": 0, "chargebacks": 0.0})
            row["chargebacks"] += amt
        rows = [metrics.day(d, 0.0, 0, 0.0, 0.0, 0.0, refunds=v["refunds"], refund_count=v["count"],
                            chargebacks=v["chargebacks"]) for d, v in sorted(days.items())]
        _outside_cache[ck] = (time.time(), rows)
    return rows


PP_LOST = {"T1106"}           # PayPal took the payment back for good (claim decided for the buyer)
_refunded_cache: dict = {}    # (store id, order) -> (time, refunded in Shopify)


async def _lost_disputes(start: str, end: str, base: str, fx_table, counted: list = ()) -> list:
    """[(Hong Kong day it closed, amount in base)] for Airwallex disputes lost or accepted (money gone), and PayPal
    chargebacks lost (only known when PayPal's Disputes feature is on). Won / open / cancelled cases count nothing.
    A case whose order was already refunded in Shopify counts only what wasn't refunded there (no double count)."""
    cases = []
    for r in db.list_disputes():
        c = json.loads(r["data"])
        if c.get("state") != "closed" or c.get("outcome") not in ("lost", "refunded"):
            continue
        if c.get("provider") == "paypal" and c.get("stage") != "Chargeback":
            continue                                # PayPal claims: the T1106 reversal / T1107 refund counts them
        try:
            when = datetime.fromisoformat(str(c.get("updated") or c.get("created")).replace("Z", "+00:00")).astimezone(CASH_TZ)
        except ValueError:
            continue
        d = when.date().isoformat()
        if start <= d <= end and c.get("amount"):
            cases.append((d, c, r.get("store_id"), r.get("order_name")))
    stores = {x["id"]: x for x in db.list_stores()}
    want = defaultdict(list)
    for d, c, sid, name in cases:
        hit = _refunded_cache.get((sid, name))
        if sid in stores and name and not (hit and time.time() - hit[0] < 6 * 3600):
            want[sid].append(name)
    for sid, names in want.items():
        st = stores[sid]
        client = ShopifyClient(st["shop_domain"], st["access_token"])
        for i in range(0, len(names), 5):
            try:
                for o in await client.lookup_orders(names[i:i + 5]):
                    _refunded_cache[(sid, o["name"])] = (time.time(), o["refunded"], o["currency"] or st["currency"])
            except Exception:
                pass
    out = []
    for d, c, sid, name in cases:
        try:
            closed = datetime.fromisoformat(str(c.get("updated") or c.get("created")).replace("Z", "+00:00"))
        except ValueError:
            closed = None
        twin = closed and next((x for x in counted if not x["used"] and x["currency"] == c.get("currency")
                                and abs(x["amount"] - c["amount"]) < 0.02
                                and abs((x["time"] - closed).total_seconds()) < 5 * 86400), None)
        if twin:                                    # settled by a refund that's already counted
            twin["used"] = True
            continue
        f = fx.factor(fx_table, c.get("currency") or base, base) if fx_table else 1.0
        amt = c["amount"] * f
        hit = _refunded_cache.get((sid, name))
        if hit and hit[1]:
            amt -= hit[1] * (fx.factor(fx_table, hit[2], base) if fx_table else 1.0)
        if amt >= 0.5:
            out.append((d, amt))
    return out


async def _gdesk_loop():
    """Every 30 minutes: read each Google store's default Google Ads desk view (last 30 days) ahead, so the
    page opens at once. About 7 x 48 product reports a day, well inside Basic access (15,000 a day)."""
    await asyncio.sleep(150)
    while True:
        for st in [x for x in db.list_stores() if x.get("google_customer_id") and not demo.is_demo(x)
                   and x.get("google_source") != "sheet" and x.get("access_token")]:
            if gads.busy():
                break
            try:
                today = _store_today(st)
                await _gdesk(st, google_desk.windows_for(today, today - timedelta(days=29), today), fresh=1)
            except Exception as e:
                print(f"Google desk refresh failed for {st['name']}: {str(e)[:200]}", flush=True)
        await asyncio.sleep(3 * 3600)


@app.get("/api/debug/paypal-disputes")
async def api_debug_paypal_disputes(days: int = 3):
    """Owner check: the raw PayPal dispute / chargeback / reversal rows (event codes T11xx, T12xx) of the last few days."""
    out = []
    for c in [x for x in db.list_cash_connections() if x["provider"] == "paypal"]:
        rows = await paypal.transactions(c["client_id"], c["secret"], days=max(1, min(days, 31)),
                                         fields="transaction_info", full=True)
        for d in rows:
            t = d.get("transaction_info") or {}
            code = t.get("transaction_event_code") or ""
            if code.startswith(("T11", "T12")):
                amt = t.get("transaction_amount") or {}
                out.append({"account": c["label"], "code": code, "status": t.get("transaction_status"),
                            "at": t.get("transaction_initiation_date"), "amount": amt.get("value"),
                            "currency": amt.get("currency_code"), "ref": t.get("paypal_reference_id"),
                            "ref_type": t.get("paypal_reference_id_type"), "id": t.get("transaction_id")})
    return {"rows": sorted(out, key=lambda r: r["at"] or "")}


async def _outside_loop():
    """Every 15 minutes: work out the recent days (all 190 every 6 hours) of refunds / chargebacks made outside Shopify and
    save them, so the dashboard reads them instead of downloading PayPal and Airwallex history."""
    await asyncio.sleep(90)
    n = 0
    while True:
        try:
            if db.list_cash_connections():
                try:
                    await _disputes_refresh()                # case outcomes: lost disputes count as refunds
                except Exception as e:
                    print(f"Disputes refresh failed: {e}", flush=True)
                stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]
                base = _display_currency().upper()
                end = datetime.now(CASH_TZ).date()
                # all 190 days (a 90-day view compares with the 90 before) every 6 hours, else the last 30
                start = end - timedelta(days=189 if n % 24 == 0 else 29)
                _outside_cache.pop((start.isoformat(), end.isoformat(), base), None)
                rows = await _outside_refunds_live(stores, start.isoformat(), end.isoformat(), base)
                got = {r["date"]: {"refunds": r["refunds"], "count": r["refund_count"], "chargebacks": r["chargebacks"]}
                       for r in rows}
                await asyncio.to_thread(db.save_outside_days, {
                    d: got.get(d, {"refunds": 0.0, "count": 0, "chargebacks": 0.0})
                    for d in _dates(start.isoformat(), end.isoformat())}, base)
                _outside_cache.clear()
        except Exception as e:
            print(f"Outside refunds refresh failed: {e}", flush=True)
        n += 1
        await asyncio.sleep(900)


async def _outside_refunds_live(stores: list, start: str, end: str, base: str) -> list:
    """The full calculation, bypassing the saved days (used by _outside_loop)."""
    return await _outside_refunds(stores, start, end, base, None, False, live=True)


async def _store_window(store, start, end, auth, meta_auth):
    """Live sales and spend for one store, as daily rows. Never raises."""
    key = (store["id"], start, end, store["google_tax_pct"])
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    notes = []

    if demo.is_demo(store):
        got_sales, got_spend = demo.series(store, start, end)
        rows = [
            metrics.day(d, got_sales[d]["sales"], got_sales[d]["orders"],
                        got_spend[d]["google"], got_spend[d]["meta"],
                        got_sales[d]["sales"] * (store["cost_pct"] or 0) / 100.0)
            for d in _dates(start, end)
        ]
        return rows, ["These are demo stores with invented figures. "
                      "Connect a real Shopify store to replace them."]

    # Days that haven't begun on this store's clock have nothing to fetch.
    store_today = _store_today(store).isoformat()
    if start > store_today:
        rows = [metrics.day(d, 0.0, 0, 0.0, 0.0, 0.0) for d in _dates(start, end)]
        _cache[key] = (time.time(), (rows, notes))
        return rows, notes
    end_for_api = min(end, store_today)

    # Completed days come from EcomVital's saved copy (refreshed every 6 hours and on every read);
    # only the last LIVE_DAYS days, and any day not saved yet, are asked from Shopify. A 30-day
    # view used to download 60 days of orders for every store on every load.
    recent_from = max(start, (_store_today(store) - timedelta(days=LIVE_DAYS)).isoformat())

    def live_start(saved: dict, lo: str) -> str:
        missing = [d for d in _dates(lo, min(end_for_api, (date.fromisoformat(recent_from) - timedelta(days=1)).isoformat()))
                   if d not in saved] if lo < recent_from else []
        return min(missing) if missing else recent_from

    async def sales():
        # Shopify only shares the last 60 days; older days come from the totals
        # EcomVital saved while they were still inside that window.
        cutoff = _shopify_cutoff(store)
        if start < cutoff and "read_all_orders" in await _granted(store):
            cutoff = start            # approved for full history: ask Shopify for all of it
        saved = db.saved_shopify_days(store["id"], start, end_for_api, store["timezone"])
        live_from = live_start(saved, max(start, cutoff))
        got = {}
        if live_from <= end_for_api:
            client = ShopifyClient(store["shop_domain"], store["access_token"])
            got = await client.daily_sales(live_from, end_for_api, store["timezone"])
            _save_sales(store, got, live_from, end_for_api)
        older = {d: v for d, v in saved.items() if d < live_from}
        if start < cutoff:
            older = {**db.saved_shopify_days(store["id"], start,
                                             (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat()), **older}
        return {**older, **got}

    async def spend():
        if not store["google_customer_id"]:
            return {}
        if not auth:
            notes.append(f"{store['name']}: Google is not connected, so ad spend shows as zero.")
            return {}
        if store["google_source"] == "sheet":
            got = await gsheet.daily_spend(auth["refresh_token"], store["google_customer_id"],
                                           start, end_for_api, store["timezone"])
        else:
            got = await _google_days(store, start, end_for_api, auth)
        return {d: metrics.with_google_tax(v, store["google_tax_pct"]) for d, v in got.items()}

    async def meta_spend():
        if not store["meta_account_id"]:
            return {}
        if not meta_auth:
            notes.append(f"{store['name']}: the Meta business for its ad account is no longer "
                         "connected, so Meta spend shows as zero. Reconnect it in Settings.")
            return {}
        account_tz = store["meta_timezone"]
        if not account_tz:   # linked before clocks were recorded; look it up once
            account_tz = await meta.account_timezone(meta_auth["access_token"],
                                                     store["meta_account_id"])
            db.set_meta_timezone(store["id"], account_tz)
        return await meta.daily_spend(meta_auth["access_token"], store["meta_account_id"],
                                      start, end_for_api, store["timezone"], account_tz)

    async def refunds():
        saved = db.saved_refund_days(store["id"], start, end_for_api, store["timezone"])
        live_from = live_start(saved, start)
        days = {}
        if live_from <= end_for_api:
            client = ShopifyClient(store["shop_domain"], store["access_token"])
            days, _records = await client.daily_refunds(live_from, end_for_api, store["timezone"])
            db.save_refund_days(store["id"], {d: days.get(d, {"refunds": 0.0, "count": 0})
                                              for d in _dates(live_from, end_for_api)}, store["timezone"])
        return {**{d: v for d, v in saved.items() if d < live_from}, **days}

    async def timed(name, coro):
        t = time.time()
        try:
            return await coro
        finally:
            _timing.setdefault(store["name"].strip(), {})[name] = round(time.time() - t, 2)
    got_sales, got_spend, got_meta, got_refunds = await asyncio.gather(
        timed("shopify sales", sales()), timed("google", spend()), timed("meta", meta_spend()),
        timed("refunds", refunds()), return_exceptions=True
    )
    if isinstance(got_refunds, Exception):
        notes.append(f"{store['name']}: couldn't read refunds ({str(got_refunds)[:120]}).")
        got_refunds = {}

    if isinstance(got_sales, Exception):
        notes.append(f"{store['name']}: {got_sales}")
        got_sales = {}
    if isinstance(got_spend, Exception):
        notes.append(f"{store['name']}: {got_spend}")
        got_spend = {}
    if isinstance(got_meta, Exception):
        notes.append(f"{store['name']}: {got_meta}")
        got_meta = {}
    if store["google_customer_id"] is None and store["meta_account_id"] is None:
        notes.append(f"{store['name']}: no ad accounts linked yet, so ad spend shows as zero.")

    t = time.time()
    ctx = await _fee_context(store, notes)
    _timing.setdefault(store["name"].strip(), {})["fee context"] = round(time.time() - t, 2)
    t = time.time()
    cogs = await _cog_for(store, start, end,
                          {d: got_sales.get(d, {}).get("sales", 0.0) for d in _dates(start, end)})
    _timing[store["name"].strip()]["cog"] = round(time.time() - t, 2)
    t = time.time()
    tracked = _tracked_fees(store, start, end)
    _timing[store["name"].strip()]["fees"] = round(time.time() - t, 2)
    rows = []
    for d in _dates(start, end):
        s = got_sales.get(d, {"sales": 0.0, "orders": 0, "payments": {}})
        pp, aw, other, shop_fee = _fee_split(store, s["payments"], ctx, notes, tracked.get(d, {}))
        c, est = cogs.get(d, (0.0, 0.0))
        rf = got_refunds.get(d, {"refunds": 0.0, "count": 0})
        rows.append(metrics.day(d, s["sales"], s["orders"],
                                got_spend.get(d, 0.0), got_meta.get(d, 0.0),
                                c, pp + aw + other, shop_fee, est, metrics.reserve_held(s["payments"]),
                                metrics.paypal_sales(s["payments"]), paypal_fee=pp, airwallex_fee=aw,
                                refunds=rf["refunds"], refund_count=rf["count"]))

    _cache[key] = (time.time(), (rows, notes))
    return rows, notes


def _zone(store):
    try:
        return ZoneInfo(store["timezone"] or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _now_in(store) -> datetime:
    return datetime.now(_zone(store))


def _shopify_cutoff(store) -> str:
    """Oldest day Shopify still shares for this store (its 60-day window)."""
    return (_store_today(store) - timedelta(days=59)).isoformat()


def _save_sales(store, got: dict, start: str, end: str):
    """Keep each day's totals, zero days included, so they outlive the 60-day window."""
    days = {d: got.get(d, {"sales": 0.0, "orders": 0, "payments": {}}) for d in _dates(start, end)}
    db.save_shopify_days(store["id"], days, store["timezone"])


async def _save_all_history():
    """Save every day Shopify still shares, for every store. Runs at start-up and
    every 6 hours, so nothing ages out of the 60-day window unsaved."""
    while True:
        for x in db.list_stores():
            if demo.is_demo(x) or not x.get("access_token"):
                continue
            try:
                start, end = _shopify_cutoff(x), _store_today(x).isoformat()
                client = ShopifyClient(x["shop_domain"], x["access_token"])
                got = await client.daily_sales(start, end, x["timezone"])
                _save_sales(x, got, start, end)
                ref, _records = await client.daily_refunds(start, end, x["timezone"])
                db.save_refund_days(x["id"], {d: ref.get(d, {"refunds": 0.0, "count": 0})
                                              for d in _dates(start, end)}, x["timezone"])
            except Exception as e:
                print(f"Saving Shopify history for {x['name']} failed: {e}", flush=True)
        await asyncio.sleep(6 * 3600)


# ---------------------------------------------------------------- COG (see cog.py)

_cog: dict = {}          # "rows": (time, parsed sheet rows)
_cog_lock = asyncio.Lock()


async def _cog_rows(force: bool = False) -> list:
    """The orders sheet, parsed. Re-read at most every 30 minutes."""
    sid = db.get_setting("cog_sheet_id")
    auth = db.get_google_auth()
    if not sid or not auth:
        return []
    hit = _cog.get("rows")
    if hit and not force and time.time() - hit[0] < 1800:
        return hit[1]
    rows = cog.parse_sheet(await gsheet.read_tabs(auth["refresh_token"], sid))
    _cog["rows"] = (time.time(), rows)
    return rows


async def _usd_to(currency: str) -> float:
    table = await fx.table(currency)
    return fx.factor(table, "USD", currency) if table else 1.0


async def _cog_sync(days_back: int = None, store_ids: list = None) -> dict:
    """Fetch each store's orders with their products for a window, cost every line
    (cog.cost_order) and save them. days_back=None means as far as Shopify allows
    (60 days, or back to the sheet's first day for stores with full history)."""
    async with _cog_lock:
        rows = await _cog_rows()
        stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")
                  and (not store_ids or x["id"] in store_ids)]
        first_day = min((r["day"] for r in rows if r["day"]), default=None)
        fetched, report = {}, {}
        for x in stores:
            today = _store_today(x)
            start = (today - timedelta(days=days_back)).isoformat() if days_back is not None \
                else _shopify_cutoff(x)
            if days_back is None and first_day and "read_all_orders" in await _granted(x):
                start = min(start, first_day)
            try:
                orders = await ShopifyClient(x["shop_domain"], x["access_token"]).order_lines(
                    start, today.isoformat(), x["timezone"])
                fetched[x["id"]] = (start, today.isoformat(), orders)
            except Exception as e:
                report[x["name"].strip()] = f"error: {e}"
        fee_map = await _actual_fees(fetched)
        # PayPal payments already matched by Shopify's payment id belong to those orders,
        # so the amount-and-time fallback never takes them.
        fee_map["_claimed"] = {p["payment_id"] for v in fetched.values() for o in v[2]
                               for p in (o.get("payments") or []) if p["payment_id"] in fee_map}
        # Each store's tag (AS…, …CH) from its own order names, then its cost history.
        known = {sid: [o["name"] for o in v[2]] for sid, v in fetched.items()}
        saved = _cog.get("tags") or {}
        tags = {**saved, **cog.store_tags(known)}
        _cog["tags"] = tags
        def build_catalog():
            c = cog.Catalog(rows, tags, _order_items())
            c.add_invoices(db.invoice_lines())
            return c
        catalog = await asyncio.to_thread(build_catalog)     # heavy: off the line pages wait on
        _cog["catalog"] = catalog

        def cost_all(orders, sid, rate):
            out = []
            for o in orders:
                for n, line in enumerate(cog.cost_order(o, sid, catalog, rate)):
                    out.append({**line, "order": o["name"], "line_no": n, "day": o["day"],
                                "created": o["created"], "cancelled": o["cancelled"],
                                "fulfillment": o["fulfillment"], "country": o.get("country")})
            return out
        for x in stores:
            if x["id"] not in fetched:
                continue
            start, end, orders = fetched[x["id"]]
            rate = await _usd_to(x["currency"])
            out = await asyncio.to_thread(cost_all, orders, x["id"], rate)
            await asyncio.to_thread(db.replace_cog_lines, x["id"], start, end, out)
            db.replace_order_fees(x["id"], start, end, await _order_fee_rows(x, orders, fee_map))
            src = defaultdict(int)
            for r in out:
                src[r["source"]] += 1
            report[x["name"].strip()] = {"from": start, "orders": len(orders), **src}
        _cache.clear()
        _cog["synced"] = (time.time(), report)
        return report


async def _actual_fees(fetched: dict) -> dict:
    """{Shopify payment id: {fee, currency}} from every PayPal and Airwallex account,
    for the last 75 days at most (older orders keep their estimate)."""
    if not fetched:
        return {}
    oldest = min(v[0] for v in fetched.values())
    since = max(datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc),
                datetime.now(timezone.utc) - timedelta(days=75))
    days = (datetime.now(timezone.utc) - since).days + 2

    async def one(c):
        if c["provider"] == "paypal":
            return paypal.payment_fees(await paypal.transactions(c["client_id"], c["secret"], days=days))
        return await awx.payment_fees(c["client_id"], c["secret"], since, c["account_id"])

    conns = db.list_cash_connections()
    got = await asyncio.gather(*[one(c) for c in conns], return_exceptions=True)
    out, pool = {}, []
    for c, g in zip(conns, got):
        if isinstance(g, Exception):
            print(f"Fees from {c['label']} failed: {g}", flush=True)
            continue
        out.update(g)
        if c["provider"] == "paypal":
            pool += [dict(v, invoice_id=k) for k, v in g.items()]
    # Some stores' PayPal checkout passes PayPal a different id than Shopify's payment id
    # (e.g. Revionel, Dovaro). Those payments are matched by amount and time instead.
    out["_paypal_pool"] = pool
    return out


def _paypal_by_amount(fee_map: dict, amount: float, currency: str, created: str):
    """A PayPal payment of this amount and currency within 3 hours of the order, not yet
    claimed by its own invoice id or by another order."""
    pool = fee_map.get("_paypal_pool") or []
    try:
        t0 = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    claimed = fee_map.setdefault("_claimed", set())
    best = None
    for e in pool:
        if e["invoice_id"] in claimed or e.get("currency") != currency or abs(e["amount"] - amount) > 0.01:
            continue
        try:
            tw = datetime.fromisoformat(str(e.get("time")).replace("Z", "+00:00").replace("+0000", "+00:00"))
        except ValueError:
            continue
        gap = abs((tw - t0).total_seconds())
        if gap <= 3 * 3600 and (best is None or gap < best[0]):
            best = (gap, e)
    if not best:
        return None
    claimed.add(best[1]["invoice_id"])
    return best[1]


async def _order_fee_rows(store, orders: list, fee_map: dict) -> list:
    """Each order payment with its rate-card estimate and, when posted, the actual fee."""
    rates, plan, hkd = await _fee_context(store, [])
    cur = store["currency"]
    table = None
    rows = []
    for o in orders:
        for p in o.get("payments") or []:
            est = metrics.processing_fees({p["gateway"]: [1, p["amount"]]}, rates, plan, cur, hkd)[0]
            hit = fee_map.get(p["payment_id"])
            if not hit and "paypal" in (p["gateway"] or "").lower():
                hit = _paypal_by_amount(fee_map, p["amount"], cur, o["created"])
            actual = None
            if hit:
                actual = hit["fee"]
                if hit["currency"] and hit["currency"] != cur:
                    table = table or await fx.table(cur)
                    actual *= fx.factor(table, hit["currency"], cur) if table else 1.0
            rows.append({"order": o["name"], "payment_id": p["payment_id"], "day": o["day"],
                         "created": o["created"], "gateway": p["gateway"], "amount": p["amount"],
                         "fee_est": est, "fee_actual": actual})
    return rows


async def _cog_loop():
    """Keep COG current: recent days every 5 minutes, the whole window every 6 hours."""
    await asyncio.sleep(60)
    n = 0
    while True:
        try:
            if db.get_setting("cog_sheet_id"):
                if n % 72 == 0:
                    await _cog_rows(force=True)
                    await _cog_sync()
                else:
                    await _cog_sync(days_back=2)
                _payable_cache.clear()                   # new orders' COG straight into suppliers payable
        except Exception as e:
            print(f"COG sync failed: {e}", flush=True)
        n += 1
        await asyncio.sleep(5 * 60)


def _order_items() -> dict:
    """{(store id, order key): items in the Shopify order} from the costed order lines (cancelled orders left out)."""
    out = defaultdict(int)
    with db._conn() as con:
        for sid, name, product, q in con.execute(
                "SELECT store_id, order_name, product, quantity FROM cog_lines WHERE cancelled = 0"):
            out[(sid, cog.order_key(name))] += max(int(q or 1), 1) * cog.pack(product)
    return dict(out)


async def _catalog() -> "cog.Catalog":
    """The current product cost history (sheet + approved invoices), rebuilt if needed."""
    if not _cog.get("tags"):
        _cog["tags"] = cog.store_tags(db.store_order_names())
    if not _cog.get("catalog"):
        catalog = cog.Catalog(await _cog_rows(), _cog["tags"], _order_items())
        catalog.add_invoices(db.invoice_lines())
        _cog["catalog"] = catalog
    return _cog["catalog"]


async def _recost(store_id: int, order_names: set):
    """Re-cost saved order lines (no Shopify call), e.g. after an invoice upload. The work runs in a
    thread so pages don't wait on it."""
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not store or not order_names:
        return
    sheet_rows = await _cog_rows()
    tags = _cog.get("tags") or cog.store_tags(db.store_order_names())
    rate = await _usd_to(store["currency"])

    def work():
        catalog = cog.Catalog(sheet_rows, tags, _order_items())
        catalog.add_invoices(db.invoice_lines())
        with db._conn() as con:
            marks = ",".join("?" * len(order_names))
            rows = [dict(r) for r in con.execute(
                f"SELECT * FROM cog_lines WHERE store_id = ? AND order_name IN ({marks})"
                " ORDER BY order_name, line_no", (store_id, *order_names))]
        orders = {}
        for r in rows:
            o = orders.setdefault(r["order_name"], {"name": r["order_name"], "cancelled": bool(r["cancelled"]),
                                                    "lines": [], "nos": []})
            o["lines"].append({"title": r["product"], "variant": "", "quantity": r["quantity"],
                               "subtotal": r["subtotal"], "sku": r["sku"], "original": r.get("original") or 0.0})
            o["nos"].append(r["line_no"])
        updates = []
        for o in orders.values():
            for no, line in zip(o["nos"], cog.cost_order(o, store_id, catalog, rate)):
                updates.append({"order_name": o["name"], "line_no": no, "cost": line["cost"],
                                "source": line["source"], "supplier": line["supplier"], "note": line["note"],
                                "single": line.get("single")})
        db.update_cog_costs(store_id, updates)
        return catalog
    _cog["catalog"] = await asyncio.to_thread(work)
    _cache.clear()


def _fee_bucket(gateways: str) -> str:
    m = metrics.payment_method(gateways or "")
    return "paypal" if m == "paypal" else "airwallex" if m.startswith("airwallex") else "other"


def _tracked_fees(store, start: str, end: str, until: datetime = None) -> dict:
    """{day: {provider: (fees of tracked orders, amount those payments cover)}}, provider
    paypal | airwallex | other: actual fee where posted, estimate otherwise."""
    out = {}
    if demo.is_demo(store):
        return out
    for r in db.order_fees(store["id"], start, end):
        if until is not None and datetime.fromisoformat(r["created"]) > until:
            continue
        fee = r["fee_actual"] if r["fee_actual"] is not None else (r["fee_est"] or 0.0)
        b = out.setdefault(r["day"], {})
        k, c = b.get(_fee_bucket(r["gateway"]), (0.0, 0.0))
        b[_fee_bucket(r["gateway"])] = (k + fee, c + r["amount"])
    return out


def _fee_split(store, payments: dict, ctx, notes, tracked: dict) -> tuple:
    """A store-day's (PayPal fee, Airwallex fee, other provider fee, Shopify fee): tracked orders'
    fees (actual where posted) plus the rate-card estimate for the rest, per provider."""
    rates, plan, hkd = ctx
    unknown = sorted({g for g in payments if metrics.payment_method(g) == "other"})
    if unknown:
        notes.append(f"{store['name']}: some orders were paid with {', '.join(unknown)}, "
                     "which has no fee rate yet, so only Shopify's fee is counted for them.")
    groups = {"paypal": {}, "airwallex": {}, "other": {}}
    for g, v in payments.items():
        groups[_fee_bucket(g)][g] = v
    fees, shop_total = {}, 0.0
    for b, pays in groups.items():
        est, shop = metrics.processing_fees(pays, rates, plan, store["currency"], hkd)
        shop_total += shop
        known, covered = tracked.get(b, (0.0, 0.0))
        fees[b] = metrics.provider_fee_day(known, covered, sum(v[1] for v in pays.values()), est)
    return fees["paypal"], fees["airwallex"], fees["other"], shop_total


async def _cog_for(store, start: str, end: str, sales: dict, until: datetime = None) -> dict:
    """{day: (cog, estimated part)} in the store's currency for its dashboard rows.
    Days with sales not (yet) covered by costed order lines use the store's average
    COG %, so the figure is never missing, and that part counts as estimated."""
    if demo.is_demo(store):
        return {d: (v * (store["cost_pct"] or 0) / 100.0, 0.0) for d, v in sales.items()}
    rate = await _usd_to(store["currency"])
    if until is None:
        days = db.cog_days(store["id"], start, end)
    else:   # part of a day: only orders placed by `until`
        days = {}
        for r in db.cog_lines([store["id"]], start, end):
            if datetime.fromisoformat(r["created"]) > until:
                continue
            d = days.setdefault(r["day"], {"cost_usd": 0.0, "estimated_usd": 0.0, "subtotal": 0.0})
            d["cost_usd"] += r["cost_usd"] or 0.0
            if r["source"] == "estimate":
                d["estimated_usd"] += r["cost_usd"] or 0.0
            if not r["cancelled"]:
                d["subtotal"] += r["subtotal"]
    catalog = _cog.get("catalog")
    pct = catalog.cog_pct(store["id"], rate) if catalog else None
    if pct is None:
        pct = _store_cog_pct(store["id"], rate)
    out = {}
    for d, sale in sales.items():
        got = days.get(d)
        cost = (got["cost_usd"] * rate) if got else 0.0
        est = (got["estimated_usd"] * rate) if got else 0.0
        uncovered = max(0.0, sale - (got["subtotal"] if got else 0.0))
        if uncovered > 0.01 and pct:
            cost += uncovered * pct
            est += uncovered * pct
        out[d] = (cost, est)
    return out


def _store_cog_pct(store_id: int, rate: float):
    """Average COG % from the store's saved order lines (when the sheet isn't loaded yet)."""
    with db._conn() as con:
        r = con.execute("SELECT SUM(cost_usd) c, SUM(subtotal) s FROM cog_lines WHERE store_id = ?"
                        " AND source IN ('invoice', 'catalog') AND cancelled = 0", (store_id,)).fetchone()
    return (r["c"] * rate / r["s"]) if r and r["s"] else None


async def _paypal_over_limit(start: str, end: str) -> dict:
    """{day: share of that day's PayPal sales above PayPal's monthly limit}.
    The month's PayPal sales come from every store's saved daily totals (one
    PayPal account takes them all), converted to AUD."""
    first = date.fromisoformat(start).replace(day=1).isoformat()
    table = await fx.table("AUD")
    per_day = defaultdict(float)
    for x in db.list_stores():
        if demo.is_demo(x):
            continue
        f = fx.factor(table, x["currency"], "AUD") if table else 1.0
        for d, v in db.saved_shopify_days(x["id"], first, end).items():
            per_day[d] += metrics.paypal_sales(v["payments"]) * f
    out, month, before = {}, None, 0.0
    for d in sorted(per_day):
        if d[:7] != month:
            month, before = d[:7], 0.0
        out[d] = metrics.over_limit_share(per_day[d], before)
        before += per_day[d]
    return out


_scopes: dict = {}


async def _short_history(stores, earliest: str) -> list:
    """Stores whose sales can't reach back to `earliest`: Shopify's 60-day limit
    (no read_all_orders permission) and no saved days that old. Kept 6 hours."""
    out = []
    for x in stores:
        if demo.is_demo(x) or not x.get("access_token"):
            continue
        cutoff = _shopify_cutoff(x)
        if earliest >= cutoff:
            continue
        saved = db.first_saved_shopify_day(x["id"])
        if saved and saved <= earliest:
            continue
        if saved and saved < cutoff:
            cutoff = saved
        if "read_all_orders" not in await _granted(x):
            out.append({**x, "_from": cutoff})
    return out


async def _granted(store) -> set:
    """The permissions this store granted its app, checked at most every 6 hours."""
    hit = _scopes.get(store["id"])
    if not hit or time.time() - hit[0] > 6 * 3600:
        try:
            got = await ShopifyClient(store["shop_domain"], store["access_token"]).access_scopes()
        except Exception:
            got = set()
        hit = _scopes[store["id"]] = (time.time(), got)
    return hit[1]


WANTED_SCOPES = {
    "read_orders", "read_all_orders", "write_orders", "write_fulfillments",
    "write_merchant_managed_fulfillment_orders", "write_third_party_fulfillment_orders",
    "write_assigned_fulfillment_orders", "write_products", "write_inventory", "read_locations",
    "write_publications", "write_online_store_pages", "write_online_store_navigation", "write_content",
    "write_themes", "write_files", "write_metaobjects", "write_metaobject_definitions", "write_discounts",
    "write_pixels", "read_customer_events", "write_script_tags", "read_customers", "write_marketing_events",
    "write_translations", "read_analytics", "read_shipping"}


@app.get("/api/fees/paypal-check")
async def api_paypal_check(store: int, days: int = 30):
    """Owner check: a store's orders paid by PayPal, and whether each payment shows up in
    the connected PayPal account (by Shopify payment id, or by amount within a day)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT order_name, payment_id, day, created, gateway, amount, fee_actual FROM order_fees"
            " WHERE store_id = ? AND day >= ? AND lower(COALESCE(gateway, '')) LIKE '%paypal%' ORDER BY created DESC",
            (store, since))]
    feed = []
    for c in db.list_cash_connections():
        if c["provider"] == "paypal":
            try:
                feed += await paypal.transactions(c["client_id"], c["secret"], days=days + 2)
            except Exception as e:
                return {"error": str(e)}
    pay_in = [t for t in feed if (t.get("transaction_event_code") or "").startswith("T00")
              and float((t.get("transaction_amount") or {}).get("value") or 0) > 0]
    by_invoice = {t.get("invoice_id"): t for t in pay_in if t.get("invoice_id")}
    for r in rows:
        hit = by_invoice.get(r["payment_id"])
        how = "payment id" if hit else None
        if not hit:
            t0 = datetime.fromisoformat(r["created"].replace("Z", "+00:00"))
            for t in pay_in:
                amt = float((t.get("transaction_amount") or {}).get("value") or 0)
                when = t.get("transaction_initiation_date")
                try:
                    tw = datetime.fromisoformat(when.replace("Z", "+00:00").replace("+0000", "+00:00"))
                except Exception:
                    continue
                if abs(amt - r["amount"]) < 0.02 and abs((tw - t0).total_seconds()) < 86400:
                    hit, how = t, "same amount, same day"
                    break
        r["in_connected_paypal"] = bool(hit)
        r["matched_by"] = how
        if hit:
            r["paypal_invoice_id"] = hit.get("invoice_id")
            r["paypal_txn"] = hit.get("transaction_id")
    return {"store": _store_names().get(store), "days": days, "paypal_orders": len(rows),
            "found_in_connected_paypal": sum(r["in_connected_paypal"] for r in rows), "orders": rows,
            "connected_paypal_payments": len(pay_in)}


@app.get("/api/shopify/permissions")
async def api_shopify_permissions():
    """Which of the wanted permissions each store's app has actually been granted (fresh)."""
    async def one(store):
        _scopes.pop(store["id"], None)
        got = await _granted(store)
        # A write permission includes its read.
        have = got | {"read_" + g[6:] for g in got if g.startswith("write_")}
        return {"store": store["name"].strip(), "granted": len(WANTED_SCOPES & have),
                "of": len(WANTED_SCOPES), "missing": sorted(WANTED_SCOPES - have),
                "error": None if got else "Couldn't read this store's permissions",
                "approve": f"{BASE_URL}/auth/shopify/start?shop_domain={store['shop_domain']}",
                "client_id": (_shopify_app(store["shop_domain"]) or ("",))[0]}
    stores = [x for x in db.list_stores() if not demo.is_demo(x) and x.get("access_token")]
    return {"stores": await asyncio.gather(*[one(x) for x in stores])}


def _store_today(store) -> date:
    return _now_in(store).date()


async def _same_time_yesterday(store, day, auth, meta_auth):
    """The day before the one being viewed, cut at the same point in the day.

    "The same point" is measured on this store's own clock: how much of the
    viewed day has passed *for this store*. A Melbourne store at 1am is an hour
    in; an Amsterdam store whose day hasn't started yet is zero hours in, so it
    is compared as nothing against nothing; a day that is already over for the
    store is compared against the whole of the day before.

    Sales and orders are cut to the minute. Ad platforms only report by the
    hour, so spend runs to the end of that hour.
    """
    notes = []
    tz = _zone(store)
    viewed = date.fromisoformat(day) + timedelta(days=1)
    elapsed = _now_in(store) - datetime.combine(viewed, clock.min, tz)
    if elapsed <= timedelta(0):
        return [metrics.day(day, 0.0, 0, 0.0, 0.0, 0.0)], notes
    cut = datetime.combine(date.fromisoformat(day), clock.min, tz) + min(
        elapsed, timedelta(hours=24) - timedelta(seconds=1))
    until = cut.time()
    now = cut  # its .hour is the hour the ad platforms are cut at

    if demo.is_demo(store):
        seconds = until.hour * 3600 + until.minute * 60 + until.second
        s, p = demo.day_share(store, day, seconds / 86400)
        return [metrics.day(day, s["sales"], s["orders"], p["google"], p["meta"],
                            s["sales"] * (store["cost_pct"] or 0) / 100.0)], notes

    async def sales():
        client = ShopifyClient(store["shop_domain"], store["access_token"])
        got = await client.daily_sales(day, day, store["timezone"], until=until)
        return got.get(day, {"sales": 0.0, "orders": 0, "payments": {}})

    async def google():
        if not (store["google_customer_id"] and auth):
            return 0.0
        if store["google_source"] == "sheet":
            got = await gsheet.spend_until_hour(auth["refresh_token"], store["google_customer_id"],
                                                day, now.hour, store["timezone"])
        else:
            got = await gads.spend_until_hour(auth["refresh_token"], store["google_customer_id"],
                                              store["google_login_cid"], day, now.hour, store["timezone"])
        return metrics.with_google_tax(got, store["google_tax_pct"])

    async def meta_spend():
        if not (store["meta_account_id"] and meta_auth):
            return 0.0
        return await meta.spend_until_hour(meta_auth["access_token"],
                                           store["meta_account_id"], day, now.hour,
                                           store["timezone"])

    got_sales, got_google, got_meta = await asyncio.gather(
        sales(), google(), meta_spend(), return_exceptions=True
    )
    for label, got in (("yesterday's sales", got_sales), ("yesterday's Google spend", got_google),
                       ("yesterday's Meta spend", got_meta)):
        if isinstance(got, Exception):
            notes.append(f"{store['name']}: could not read {label} for the comparison ({got}).")
    if isinstance(got_sales, Exception):
        got_sales = {"sales": 0.0, "orders": 0, "payments": {}}
    if isinstance(got_google, Exception):
        got_google = 0.0
    if isinstance(got_meta, Exception):
        got_meta = 0.0

    pp, aw, other, shop_fee = _fee_split(store, got_sales["payments"], await _fee_context(store, notes), notes,
                                         _tracked_fees(store, day, day, until=cut).get(day, {}))
    c, est = (await _cog_for(store, day, day, {day: got_sales["sales"]}, until=cut)).get(day, (0.0, 0.0))
    try:
        rdays, _r = await ShopifyClient(store["shop_domain"], store["access_token"]).daily_refunds(
            day, day, store["timezone"], until=until)
        rf = rdays.get(day, {"refunds": 0.0, "count": 0})
    except Exception:
        rf = {"refunds": 0.0, "count": 0}
    return [metrics.day(day, got_sales["sales"], got_sales["orders"],
                        got_google, got_meta, c, pp + aw + other, shop_fee, est,
                        metrics.reserve_held(got_sales["payments"]),
                        metrics.paypal_sales(got_sales["payments"]), paypal_fee=pp, airwallex_fee=aw,
                        refunds=rf["refunds"], refund_count=rf["count"])], notes


def _dates(start: str, end: str):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        yield d.isoformat()
        d += timedelta(days=1)


def _closer(message: str, ok: bool = False) -> str:
    tone = "#0F9D74" if ok else "#B3402A"
    return f"""<!doctype html><meta charset="utf-8"><title>EcomVital</title>
<style>body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
background:#F7F8FA;color:#14181F;display:grid;place-items:center;height:100vh;
margin:0;text-align:center;padding:24px}}
strong{{color:{tone}}}</style>
<div><p><strong>{message}</strong></p></div>
<script>if(window.opener){{window.opener.postMessage('profitdesk:connected','*');
setTimeout(()=>window.close(),1500);}}</script>"""


# ---------------------------------------------------------------- Apps → Meta Ads desk (overview + Manage)

MADS_DEFAULTS = {"enabled": False, "mode": "approve", "budget_cap": 300.0}
_mads_ready = {"ok": False}
_mads_cache: dict = {}        # (store, kind, ...) -> (time, data)
MADS_KEEP = 600               # Meta figures are re-read at most every 10 minutes (Refresh forces it)
_PX_FIELD = {"campaign": "a_campaign", "adset": "a_term", "ad": "a_content"}


def _mads_db():
    if _mads_ready["ok"]:
        return
    with db._conn() as con:
        con.executescript(meta_manage.SCHEMA)
    _mads_ready["ok"] = True


def _mads_settings(store_id: int) -> dict:
    try:
        got = json.loads(db.get_setting(f"meta_manage:{store_id}") or "{}")
    except ValueError:
        got = {}
    return {**MADS_DEFAULTS, **got, "mode": "approve"}         # Autopilot isn't offered yet


def _mads_store(store_id):
    st = next((x for x in db.list_stores() if str(x["id"]) == str(store_id)), None)
    if not st:
        raise HTTPException(404, "Store not found.")
    conn = next((c for c in db.list_meta_connections() if c["id"] == st.get("meta_connection_id")), None)
    if not st.get("meta_account_id") or not conn:
        raise HTTPException(400, f"{st['name'].strip()} has no Meta ad account connected (Settings → Your stores).")
    return st, conn


def _mads_window(st, days: int):
    days = max(1, min(int(days or 7), 30))
    today = _store_today(st)
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat(), days


async def _mads_cached(key, fresh, make):
    hit = _mads_cache.get(key)
    if hit and not fresh and time.time() - hit[0] < MADS_KEEP:
        return hit[1]
    data = await make()
    _mads_cache[key] = (time.time(), data)
    return data


async def _mads_rows(st, conn, level: str, days: int, fresh=False, window=None) -> dict:
    """One level's objects with Meta's figures and EcomVital's pixel figures over the last N days (or the picked
    window), account currency, plus what the "$10 up" autopilot added to each campaign's budget in that window."""
    level = level if level in meta_manage.LEVELS else "campaign"
    since, until, days = window or _mads_window(st, days)
    cur = st.get("meta_currency") or st["currency"]
    token, act = conn["access_token"], st["meta_account_id"]
    try:
        got = await _mads_cached((st["id"], "insights", level, since, until), fresh,
                                 lambda: meta_manage.insights(token, act, level, since, until))
        objs = await _mads_cached((st["id"], "objects", level, since, until), fresh,
                                  lambda: meta_manage.objects(token, act, level, cur, [i for i, m in got.items() if m["spend"]]))
    except meta.MetaAdsError as e:
        raise HTTPException(400, str(e))
    # EcomVital's pixel, matched by the names the UTMs carry (as on the Tracking desk), in the account currency
    tz = _zone(st)
    a = datetime.combine(date.fromisoformat(since), clock.min, tz).astimezone(timezone.utc).isoformat(timespec="seconds")
    b = datetime.combine(date.fromisoformat(until) + timedelta(days=1), clock.min, tz).astimezone(timezone.utc).isoformat(timespec="seconds")
    px = await asyncio.to_thread(tracking.report, st["id"], a, b, _PX_FIELD[level])
    try:
        f = fx.factor(await fx.table(cur), st["currency"], cur)
    except Exception:
        f = 1.0
    px_by = {(r["name"] or "").strip().lower(): r for r in px.get("rows") or []}
    rows = []
    for o in objs:
        m = got.get(o["id"], {})
        p = px_by.get((o["name"] or "").strip().lower(), {})
        spend = m.get("spend", 0.0)
        r = {**o, "level": level, **{k: m.get(k, 0) for k in ("spend", "impressions", "clicks", "purchases", "value", "atc")},
             "px_purchases": p.get("purchase") or 0, "px_value": (p.get("purchase_value") or 0) * f}
        r["ctr"] = r["clicks"] / r["impressions"] if r["impressions"] else None
        r["cpc"] = spend / r["clicks"] if spend and r["clicks"] else None
        r["roas"] = r["value"] / spend if spend else None
        r["px_roas"] = r["px_value"] / spend if spend else None
        r["cpa"] = spend / r["purchases"] if spend and r["purchases"] else None
        rows.append(r)
    auto = await asyncio.to_thread(_mads_autopilot, st, since, until)
    for r in rows:
        x = auto.get(str(r["id"])) if level == "campaign" else None
        r["auto_added"], r["auto_raises"] = (x["added"], x["raises"]) if x else (0.0, 0)
    rows.sort(key=lambda r: (r["effective"] != "ACTIVE", -r["spend"], r["name"].lower()))
    tot = {k: sum(r[k] for r in rows) for k in ("spend", "impressions", "clicks", "purchases", "value", "atc", "px_purchases", "px_value")}
    tot["auto_added"] = sum(x["added"] for x in auto.values())         # every campaign, shown or not
    tot["auto_raises"] = sum(x["raises"] for x in auto.values())
    tot["roas"] = tot["value"] / tot["spend"] if tot["spend"] else None
    tot["px_roas"] = tot["px_value"] / tot["spend"] if tot["spend"] else None
    return {"rows": rows, "total": tot, "since": since, "until": until, "days": days, "currency": cur,
            "pixel": tracking.health(st["id"]) if hasattr(tracking, "health") else None}


# ---- Strategy "$10 up every purchase" (Autopilot): every pixel purchase credited to a Meta campaign raises that
# campaign's daily budget by `step`, from the moment it's switched on until someone switches it off. One raise per
# order (mads_boosts), never above `cap`. Each raise is a logged change with Undo, like any other.

UP_DEFAULTS = {"on": False, "name": "$10 up every purchase", "step": 10.0, "cap": 500.0, "started_at": None,
               "stopped_at": None, "by": ""}
_up_lock: dict = {}


def _up_cfg(store_id: int) -> dict:
    try:
        got = json.loads(db.get_setting(f"meta_up:{store_id}") or "{}")
    except ValueError:
        got = {}
    return {**UP_DEFAULTS, **got}


async def _up_run(st, conn) -> list:
    """Raise budgets for purchases the pixel saw since the strategy was switched on (each order once)."""
    cfg = _up_cfg(st["id"])
    if not cfg["on"] or not cfg["started_at"]:
        return []
    lock = _up_lock.setdefault(st["id"], asyncio.Lock())
    if lock.locked():
        return []
    async with lock:
        _mads_db()
        with db._conn() as con:
            seen = {r[0] for r in con.execute("SELECT order_key FROM mads_boosts WHERE store_id = ?", (st["id"],))}
            buys = [dict(r) for r in con.execute(
                "SELECT id, at, value, order_id, token, a_campaign, a_source FROM px_events WHERE store_id = ? AND"
                " name = 'checkout_completed' AND at >= ? ORDER BY at", (st["id"], cfg["started_at"]))]
        todo, keys = [], set()
        for b in buys:
            key = b["order_id"] or b["token"] or b["id"]
            if key in seen or key in keys:
                continue
            keys.add(key)
            todo.append((key, b))
        if not todo:
            return []
        cur = st.get("meta_currency") or st["currency"]
        camps = await _mads_cached((st["id"], "up-campaigns"), False,
                                   lambda: meta_manage.objects(conn["access_token"], st["meta_account_id"], "campaign", cur))
        by_name = {(c["name"] or "").strip().lower(): c for c in camps}
        by_id = {c["id"]: c for c in camps}
        out, fresh = [], False
        for key, b in todo:
            utm = (b["a_campaign"] or "").strip()
            c = by_id.get(utm) or by_name.get(utm.lower())
            if utm and not c and not fresh:          # a campaign started since the list was read: read it again once
                fresh = True
                camps = await _mads_cached((st["id"], "up-campaigns"), True,
                                           lambda: meta_manage.objects(conn["access_token"], st["meta_account_id"], "campaign", cur))
                by_name = {(x["name"] or "").strip().lower(): x for x in camps}
                by_id = {x["id"]: x for x in camps}
                c = by_id.get(utm) or by_name.get(utm.lower())
            outcome, action = "", None
            if not utm:
                outcome = "No ad campaign on this purchase (not from an ad, or the click was over 7 days ago)."
            elif not c:
                outcome = f"“{utm}” isn't a running campaign in this Meta ad account (another channel, or paused)."
            else:
                try:
                    now_ = await meta_manage.read(conn["access_token"], c["id"])
                    budget = meta_manage._money(now_.get("daily_budget"), cur)
                    if not budget:
                        outcome = f"{c['name']}: its budget is on the ad sets (or lifetime), so it wasn't raised."
                    elif cfg["cap"] and budget + cfg["step"] > cfg["cap"] + 0.009:
                        outcome = f"{c['name']}: already at {budget:.2f}; +{cfg['step']:g} would pass the cap {cfg['cap']:g}."
                    else:
                        batch = _mads_batch("up")
                        with db._conn() as con:
                            _mads_log(con, st["id"], batch, "strategy:up", {
                                "change": "budget", "level": "campaign", "id": c["id"], "name": c["name"],
                                "to": round(budget + cfg["step"], 2),
                                "reason": f"{cfg['name']}: purchase {('#' + str(b['order_id'])) if b['order_id'] else ''} "
                                          f"({(b['value'] or 0):.2f}) from this campaign"})
                            action = con.execute("SELECT max(id) FROM mads_actions WHERE batch = ?", (batch,)).fetchone()[0]
                        done = await _mads_run_batch(st, conn, batch, f"{cfg['name']} (autopilot)", cap=cfg["cap"] or 0)
                        outcome = done[0] if done else "Raised"
                except (meta.MetaAdsError, HTTPException) as e:
                    outcome = f"Meta refused: {str(getattr(e, 'detail', None) or e)[:300]}"
            with db._conn() as con:
                con.execute("INSERT OR IGNORE INTO mads_boosts (store_id, order_key, at, value, campaign, campaign_id, action_id, outcome,"
                            " raised) VALUES (?,?,?,?,?,?,?,?,?)",
                            (st["id"], key, b["at"], b["value"], utm, c["id"] if c else None, action, outcome,
                             1 if action and outcome.startswith("Daily budget") else 0))
            out.append(outcome)
        return out


async def _up_loop():
    """Every minute: stores with the strategy on."""
    await asyncio.sleep(90)
    while True:
        for st in db.list_stores():
            try:
                if st.get("meta_account_id") and _up_cfg(st["id"])["on"]:
                    st2, conn = _mads_store(st["id"])
                    await _up_run(st2, conn)
            except Exception as e:
                print(f"$10-up strategy failed for {st['name']}: {str(getattr(e, 'detail', None) or e)[:300]}", flush=True)
        await asyncio.sleep(60)


@app.get("/api/metaads/strategy")
def api_up_get(store: int):
    _mads_db()
    st, _conn = _mads_store(store)
    cfg = _up_cfg(st["id"])
    with db._conn() as con:
        feed = [dict(r) for r in con.execute("SELECT * FROM mads_boosts WHERE store_id = ? ORDER BY at DESC LIMIT 40", (st["id"],))]
        since = cfg["started_at"] or "9999"
        n = con.execute("SELECT COUNT(*), COALESCE(SUM(raised), 0) FROM mads_boosts WHERE store_id = ? AND at >= ?", (st["id"], since)).fetchone()
    return {**cfg, "currency": st.get("meta_currency") or st["currency"], "feed": feed, "purchases": n[0], "raises": n[1],
            "added": n[1] * cfg["step"]}


@app.put("/api/metaads/strategy/{store_id}")
def api_up_set(store_id: int, payload: dict, request: Request):
    """Switch the strategy on (it counts purchases from this moment) or off, or change its step / cap."""
    st, _conn = _mads_store(store_id)
    cfg = _up_cfg(st["id"])
    who = (request.state.user or {}).get("email", "")
    now_ = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "on" in payload:
        on = bool(payload["on"])
        if on and not cfg["on"]:
            cfg.update(on=True, started_at=now_, stopped_at=None, by=who)
        elif not on and cfg["on"]:
            cfg.update(on=False, stopped_at=now_, by=who)
    for k in ("step", "cap"):
        if k in payload:
            try:                                  # cap 0 (or empty) = no cap
                v = float(payload[k] or 0) if k == "cap" else float(payload[k])
                cfg[k] = max(0.0, v) if k == "cap" else max(1.0, v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number.")
    db.set_setting(f"meta_up:{st['id']}", json.dumps({k: cfg[k] for k in UP_DEFAULTS}))
    return {"ok": True}


@app.get("/metaads", response_class=HTMLResponse)
def metaads_page():
    return _page("metaads.html", ("metaads.js", "metaads.css", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/metaads/stores")
def api_mads_stores():
    _mads_db()
    with db._conn() as con:
        waiting = dict(con.execute("SELECT store_id, COUNT(DISTINCT batch) FROM mads_actions WHERE status = 'proposed' GROUP BY 1").fetchall())
    return {"stores": [{"id": x["id"], "name": x["name"].strip(), "account": x.get("meta_account_name") or x["meta_account_id"],
                        "currency": x.get("meta_currency") or x["currency"], "managing": _mads_settings(x["id"])["enabled"],
                        "waiting": waiting.get(x["id"], 0)}
                       for x in db.list_stores() if x.get("meta_account_id") and not demo.is_demo(x)]}


@app.get("/api/metaads/overview")
async def api_mads_overview(store: int, level: str = "campaign", days: int = 7, fresh: int = 0, period: str = "",
                            start: str = "", end: str = ""):
    """period: today | yesterday | 3 | 7 | 14 | 30 | month | lastmonth | custom (start, end: store days)."""
    _mads_db()
    st, conn = _mads_store(store)
    return await _mads_rows(st, conn, level, days, bool(fresh), _mads_period(st, period, start, end))


def _mads_period(st, period: str, start: str, end: str):
    """(since, until, days) on the store's clock for the picked period, or None for the old `days` window."""
    today = _store_today(st)
    if period == "today":
        a = b = today
    elif period == "yesterday":
        a = b = today - timedelta(days=1)
    elif period in ("3", "7", "14", "30", "90"):
        a, b = today - timedelta(days=int(period) - 1), today
    elif period == "month":
        a, b = today.replace(day=1), today
    elif period == "lastmonth":
        b = today.replace(day=1) - timedelta(days=1)
        a = b.replace(day=1)
    elif period == "custom":
        try:
            a, b = date.fromisoformat(start), min(date.fromisoformat(end), today)
        except ValueError:
            raise HTTPException(400, "Pick both dates.")
        if a > b:
            raise HTTPException(400, "The start date has to be on or before the end date.")
        if (b - a).days > 400:
            raise HTTPException(400, "Pick up to about a year at a time.")
    else:
        return None
    return a.isoformat(), b.isoformat(), (b - a).days + 1


def _mads_autopilot(st, since: str, until: str) -> dict:
    """What the "$10 up" autopilot added to each campaign's daily budget in these store days (raises still in place;
    undone ones left out): {campaign id: {added, raises}}."""
    tz = _zone(st)
    a = datetime.combine(date.fromisoformat(since), clock.min, tz).astimezone(timezone.utc).isoformat(timespec="seconds")
    b = datetime.combine(date.fromisoformat(until) + timedelta(days=1), clock.min, tz).astimezone(timezone.utc).isoformat(timespec="seconds")
    out = {}
    with db._conn() as con:
        for r in con.execute("SELECT obj_id, to_value, before FROM mads_actions WHERE store_id = ? AND source = 'strategy:up'"
                             " AND status = 'done' AND done_at >= ? AND done_at < ?", (st["id"], a, b)):
            try:
                added = float(r["to_value"]) - float(json.loads(r["before"] or "{}").get("daily_budget") or 0)
            except (TypeError, ValueError):
                continue
            x = out.setdefault(str(r["obj_id"]), {"added": 0.0, "raises": 0})
            x["added"] += max(0.0, added)
            x["raises"] += 1
    return out


def _mads_log(con, store_id, batch, source, step: dict, status="proposed"):
    con.execute("INSERT INTO mads_actions (store_id, batch, source, change, level, obj_id, name, to_value, reason, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (store_id, batch, source, step["change"], step["level"], str(step["id"]), step.get("name") or "",
                 str(step["to"]), (step.get("reason") or "")[:500], status, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def _mads_batch(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"


async def _mads_step(st, conn, a: dict, cap=None) -> dict:
    """Check one change with Meta (validate-only), then make it. Returns {before, result}."""
    token, cur = conn["access_token"], st.get("meta_currency") or st["currency"]
    before = await meta_manage.read(token, a["obj_id"])
    name = before.get("name") or a["name"]
    if a["change"] == "status":
        to = "ACTIVE" if str(a["to_value"]).upper() in ("ACTIVE", "ON", "ENABLED") else "PAUSED"
        await meta_manage.set_status(token, a["obj_id"], to, validate=True)
        await meta_manage.set_status(token, a["obj_id"], to)
        return {"before": {"status": before.get("status")}, "result": f"{'Switched on' if to == 'ACTIVE' else 'Paused'} {name}"}
    amount = float(a["to_value"])
    if not before.get("daily_budget"):
        raise meta.MetaAdsError(f"{name} has no daily budget of its own (its budget is on the "
                                f"{'campaign' if a['level'] == 'adset' else 'ad sets'}, or it's a lifetime budget).")
    cap = cap if cap is not None else _mads_settings(st["id"])["budget_cap"]
    if cap and amount > cap:
        raise meta.MetaAdsError(f"{amount:.2f} is above the daily budget cap ({cap:g}). Raise the cap in Manage settings.")
    await meta_manage.set_budget(token, a["obj_id"], amount, cur, validate=True)
    await meta_manage.set_budget(token, a["obj_id"], amount, cur)
    was = meta_manage._money(before.get("daily_budget"), cur)
    return {"before": {"daily_budget": was}, "result": f"Daily budget of {name}: {was:.2f} → {amount:.2f} {cur}"}


async def _mads_run_batch(st, conn, batch: str, who: str, cap=None) -> list:
    """Carry out a proposal / plan step by step; stops at the first step Meta refuses."""
    with db._conn() as con:
        acts = [dict(r) for r in con.execute("SELECT * FROM mads_actions WHERE store_id = ? AND batch = ? AND status = 'proposed'"
                                             " ORDER BY id", (st["id"], batch))]
    if not acts:
        raise HTTPException(400, "Nothing waiting in that proposal (already done or rejected).")
    done = []
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, a in enumerate(acts):
        try:
            got = await _mads_step(st, conn, a, cap)
        except (meta.MetaAdsError, ValueError) as e:
            with db._conn() as con:
                con.execute("UPDATE mads_actions SET status = 'failed', error = ?, decided_by = ?, done_at = ? WHERE id = ?",
                            (str(e)[:600], who, now(), a["id"]))
                for rest in acts[i + 1:]:
                    con.execute("UPDATE mads_actions SET status = 'failed', error = 'Not done: an earlier step failed.' WHERE id = ?", (rest["id"],))
            raise HTTPException(400, f"Step {i + 1} failed: {str(e)[:400]}" + (f" (done before it: {'; '.join(done)})" if done else ""))
        with db._conn() as con:
            con.execute("UPDATE mads_actions SET status = 'done', before = ?, summary = ?, decided_by = ?, done_at = ?, error = NULL"
                        " WHERE id = ?", (json.dumps(got["before"]), got["result"], who, now(), a["id"]))
        done.append(got["result"])
    for k in [k for k in _mads_cache if k[0] == st["id"]]:
        _mads_cache.pop(k, None)
    return done


async def _mads_run_rules(st, conn, only_rule: int = None) -> list:
    """The store's switched-on rules (or one rule now): what they want becomes one proposal per rule, waiting for Do it.
    The same change isn't proposed again while one waits, nor within a day of being done or rejected."""
    _mads_db()
    with db._conn() as con:
        rules = [dict(r) for r in con.execute("SELECT * FROM mads_rules WHERE store_id = ?" + (" AND id = ?" if only_rule else " AND enabled = 1"),
                                              (st["id"], only_rule) if only_rule else (st["id"],))]
        day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        recent = {(r["obj_id"], r["change"]) for r in con.execute(
            "SELECT obj_id, change FROM mads_actions WHERE store_id = ? AND (status = 'proposed' OR"
            " (status IN ('done', 'rejected') AND COALESCE(done_at, created_at) >= ?))", (st["id"], day_ago))}
    made = []
    cap = _mads_settings(st["id"])["budget_cap"]
    for rule in rules:
        p = json.loads(rule["params"])
        data = await _mads_rows(st, conn, p["level"], p["days"], fresh=True)
        wants = [w for w in meta_rules.evaluate(rule["kind"], p, data["rows"], cap) if (w["id"], w["change"]) not in recent]
        notes = [f"{w['name']}: {w['reason']}" for w in wants][:20]
        with db._conn() as con:
            if wants:
                batch = _mads_batch(f"rule{rule['id']}")
                for w in wants:
                    _mads_log(con, st["id"], batch, f"rule:{rule['id']}", w)
                    recent.add((w["id"], w["change"]))
                made.append({"rule": rule["name"], "batch": batch, "changes": len(wants)})
            con.execute("UPDATE mads_rules SET last_run = ?, last_notes = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         json.dumps(notes or ["Nothing to change."]), rule["id"]))
    return made


async def _mads_loop():
    """Every hour: each managed store's switched-on Meta rules (proposals wait for Do it)."""
    await asyncio.sleep(300)
    while True:
        for st in db.list_stores():
            try:
                if st.get("meta_account_id") and _mads_settings(st["id"])["enabled"]:
                    st2, conn = _mads_store(st["id"])
                    await _mads_run_rules(st2, conn)
            except Exception as e:
                print(f"Meta rules failed for {st['name']}: {str(getattr(e, 'detail', None) or e)[:300]}", flush=True)
        await asyncio.sleep(3600)


def _mads_rules_of(st) -> list:
    _mads_db()
    cur = st.get("meta_currency") or st["currency"]
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM mads_rules WHERE store_id = ? ORDER BY id", (st["id"],))]
    for r in rows:
        r["params"] = json.loads(r["params"])
        r["last_notes"] = json.loads(r["last_notes"]) if r.get("last_notes") else []
        r["text"] = meta_rules.describe(r["kind"], r["params"], cur + " ")
        r["kind_name"] = meta_rules.NAMES.get(r["kind"], r["kind"])
    return rows


def _mads_actions_of(store_id, limit=120) -> list:
    _mads_db()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM mads_actions WHERE store_id = ? ORDER BY id DESC LIMIT ?", (store_id, limit))]
    for r in rows:
        r["before"] = json.loads(r["before"]) if r.get("before") else None
    return rows


@app.get("/api/metaads/manage")
def api_mads_manage(store: int):
    _mads_db()
    st, _conn = _mads_store(store)
    with db._conn() as con:
        chat = [dict(r) for r in con.execute("SELECT role, content, who, created_at FROM mads_chat WHERE store_id = ? ORDER BY id DESC LIMIT 60",
                                             (st["id"],))]
    return {"settings": _mads_settings(st["id"]), "rules": _mads_rules_of(st), "actions": _mads_actions_of(st["id"]),
            "chat": chat[::-1], "kinds": [{"kind": k, "name": meta_rules.NAMES[k], "defaults": meta_rules.DEFAULTS[k]} for k in meta_rules.KINDS],
            "currency": st.get("meta_currency") or st["currency"], "ai": bool(db.get_setting("anthropic_api_key"))}


@app.put("/api/metaads/settings/{store_id}")
def api_mads_settings(store_id: int, payload: dict):
    st, _conn = _mads_store(store_id)
    cfg = _mads_settings(store_id)
    if "enabled" in payload:
        cfg["enabled"] = bool(payload["enabled"])
    if "budget_cap" in payload:
        try:
            cfg["budget_cap"] = max(0.0, float(payload["budget_cap"] or 0))
        except ValueError:
            raise HTTPException(400, "The budget cap must be a number.")
    db.set_setting(f"meta_manage:{store_id}", json.dumps({k: cfg[k] for k in ("enabled", "budget_cap")}))
    return {"ok": True, "settings": _mads_settings(store_id)}


@app.post("/api/metaads/rules/{store_id}")
def api_mads_rule_add(store_id: int, payload: dict, request: Request):
    _mads_db()
    st, _conn = _mads_store(store_id)
    kind = payload.get("kind")
    try:
        p = meta_rules.clean(kind, payload.get("params") or {})
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    name = str(payload.get("name") or meta_rules.NAMES[kind]).strip()[:120]
    with db._conn() as con:
        rid = con.execute("INSERT INTO mads_rules (store_id, name, kind, params, enabled, created_at, created_by) VALUES (?,?,?,?,0,?,?)",
                          (st["id"], name, kind, json.dumps(p), datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           (request.state.user or {}).get("email", ""))).lastrowid
    return {"ok": True, "id": rid}


@app.put("/api/metaads/rule/{rule_id}")
def api_mads_rule_edit(rule_id: int, payload: dict):
    _mads_db()
    with db._conn() as con:
        r = con.execute("SELECT * FROM mads_rules WHERE id = ?", (rule_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Rule not found.")
        if "params" in payload:
            try:
                p = meta_rules.clean(r["kind"], {**json.loads(r["params"]), **(payload["params"] or {})})
            except (ValueError, TypeError) as e:
                raise HTTPException(400, str(e))
            con.execute("UPDATE mads_rules SET params = ? WHERE id = ?", (json.dumps(p), rule_id))
        if "name" in payload and str(payload["name"]).strip():
            con.execute("UPDATE mads_rules SET name = ? WHERE id = ?", (str(payload["name"]).strip()[:120], rule_id))
        if "enabled" in payload:
            con.execute("UPDATE mads_rules SET enabled = ? WHERE id = ?", (1 if payload["enabled"] else 0, rule_id))
    return {"ok": True}


@app.delete("/api/metaads/rule/{rule_id}")
def api_mads_rule_delete(rule_id: int):
    _mads_db()
    with db._conn() as con:
        con.execute("DELETE FROM mads_rules WHERE id = ?", (rule_id,))
    return {"ok": True}


@app.post("/api/metaads/rule/{rule_id}/run")
async def api_mads_rule_run(rule_id: int):
    """Check one rule now (even switched off): anything it wants waits as a proposal."""
    _mads_db()
    with db._conn() as con:
        r = con.execute("SELECT store_id FROM mads_rules WHERE id = ?", (rule_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Rule not found.")
    st, conn = _mads_store(r["store_id"])
    made = await _mads_run_rules(st, conn, only_rule=rule_id)
    return {"ok": True, "made": made}


@app.post("/api/metaads/batch/{store_id}/{batch}/{what}")
async def api_mads_batch(store_id: int, batch: str, what: str, request: Request):
    """Do it (carry out a waiting proposal / plan) or Reject it."""
    _mads_db()
    st, conn = _mads_store(store_id)
    who = (request.state.user or {}).get("email", "")
    if what == "reject":
        with db._conn() as con:
            con.execute("UPDATE mads_actions SET status = 'rejected', decided_by = ?, done_at = ? WHERE store_id = ? AND batch = ?"
                        " AND status = 'proposed'", (who, datetime.now(timezone.utc).isoformat(timespec="seconds"), st["id"], batch))
        return {"ok": True}
    if what != "do":
        raise HTTPException(404, "Unknown action.")
    done = await _mads_run_batch(st, conn, batch, who)
    return {"ok": True, "done": done}


@app.post("/api/metaads/action/{action_id}/undo")
async def api_mads_undo(action_id: int, request: Request):
    """Put one done change back as it was (a new logged change, so it can be undone too)."""
    _mads_db()
    with db._conn() as con:
        a = con.execute("SELECT * FROM mads_actions WHERE id = ?", (action_id,)).fetchone()
    if not a or a["status"] != "done" or not a["before"]:
        raise HTTPException(400, "Only a done change can be undone.")
    a = dict(a)
    st, conn = _mads_store(a["store_id"])
    before = json.loads(a["before"])
    step = {"change": a["change"], "level": a["level"], "id": a["obj_id"], "name": a["name"], "reason": f"Undo of change #{action_id}",
            "to": before.get("status") if a["change"] == "status" else before.get("daily_budget")}
    if step["to"] in (None, ""):
        raise HTTPException(400, "Nothing saved to put back.")
    batch = _mads_batch("undo")
    with db._conn() as con:
        _mads_log(con, st["id"], batch, f"undo:{action_id}", step)
    done = await _mads_run_batch(st, conn, batch, (request.state.user or {}).get("email", ""))
    with db._conn() as con:
        con.execute("UPDATE mads_actions SET status = 'undone' WHERE id = ?", (action_id,))
    return {"ok": True, "done": done}


@app.post("/api/metaads/quick/{store_id}")
async def api_mads_quick(store_id: int, payload: dict, request: Request):
    """A change made by hand on the Overview (pause / switch on / budget): logged like any other, so Undo works."""
    _mads_db()
    st, conn = _mads_store(store_id)
    if payload.get("change") not in ("status", "budget") or payload.get("level") not in meta_manage.LEVELS or not payload.get("id"):
        raise HTTPException(400, "Pick what to change.")
    step = {"change": payload["change"], "level": payload["level"], "id": str(payload["id"]), "name": payload.get("name") or "",
            "to": payload.get("to"), "reason": "Changed by hand on the Overview"}
    batch = _mads_batch("manual")
    with db._conn() as con:
        _mads_log(con, st["id"], batch, "manual", step)
    done = await _mads_run_batch(st, conn, batch, (request.state.user or {}).get("email", ""))
    return {"ok": True, "done": done}


async def _mads_tool(st, conn, name: str, args: dict, who: str):
    """The Meta ads AI's tools (meta_brain.TOOLS)."""
    cur = st.get("meta_currency") or st["currency"]
    if name == "get_results":
        data = await _mads_rows(st, conn, args.get("level") or "campaign", args.get("days") or 7)
        keep = ("id", "name", "campaign", "adset", "status", "effective", "daily_budget", "lifetime_budget", "spend", "impressions",
                "clicks", "ctr", "cpc", "purchases", "value", "roas", "atc", "cpa", "px_purchases", "px_value", "px_roas")
        rows = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in keep}
                for r in data["rows"] if not args.get("only_active") or r["effective"] == "ACTIVE"]
        return {"since": data["since"], "until": data["until"], "currency": cur, "rows": rows[:150], "total": data["total"]}
    if name == "propose_plan":
        steps = args.get("steps") or []
        if not steps:
            raise HTTPException(400, "A plan needs at least one step.")
        known = {}
        for lv in meta_manage.LEVELS:
            for r in (await _mads_rows(st, conn, lv, 7))["rows"]:
                known[r["id"]] = r
        clean = []
        for s in steps[:40]:
            r = known.get(str(s.get("id")))
            if not r or s.get("change") not in ("status", "budget"):
                raise HTTPException(400, f"Unknown id {s.get('id')}: use ids from get_results.")
            if s["change"] == "budget":
                float(s["to"])
            clean.append({**s, "id": str(s["id"]), "level": r["level"], "name": r["name"]})
        batch = _mads_batch("ai")
        with db._conn() as con:
            for s in clean:
                _mads_log(con, st["id"], batch, "ai", s)
        return {"ok": True, "batch": batch, "steps": len(clean), "note": "Waiting for the person's Do it on the Manage tab."}
    if name == "save_rule":
        p = meta_rules.clean(args.get("kind"), args.get("params") or {})
        with db._conn() as con:
            rid = con.execute("INSERT INTO mads_rules (store_id, name, kind, params, enabled, created_at, created_by) VALUES (?,?,?,?,0,?,?)",
                              (st["id"], str(args.get("name") or meta_rules.NAMES[args["kind"]])[:120], args["kind"], json.dumps(p),
                               datetime.now(timezone.utc).isoformat(timespec="seconds"), f"ads AI for {who}")).lastrowid
        return {"ok": True, "id": rid, "text": meta_rules.describe(args["kind"], p, cur + " "), "note": "Saved OFF: the person turns it on."}
    if name == "list_rules":
        return {"rules": [{k: r[k] for k in ("id", "name", "kind", "enabled", "text", "last_run", "last_notes")} for r in _mads_rules_of(st)]}
    if name == "recent_changes":
        return {"changes": [{k: a[k] for k in ("id", "batch", "source", "change", "name", "to_value", "reason", "status", "summary", "error", "created_at")}
                            for a in _mads_actions_of(st["id"], 40)]}
    raise HTTPException(400, f"Unknown tool {name}.")


@app.post("/api/metaads/chat/{store_id}")
async def api_mads_chat(store_id: int, payload: dict, request: Request):
    _mads_db()
    st, conn = _mads_store(store_id)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write something first.")
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the AI key first (Settings → AI).")
    who = (request.state.user or {}).get("email", "")
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db._conn() as con:
        con.execute("INSERT INTO mads_chat (store_id, role, content, who, created_at) VALUES (?, 'user', ?, ?, ?)", (st["id"], text, who, now()))
        hist = [dict(r) for r in con.execute("SELECT role, content FROM mads_chat WHERE store_id = ? ORDER BY id DESC LIMIT 16", (st["id"],))][::-1]
    ctx = {"store": st["name"].strip(), "today": _store_today(st).isoformat(), "currency": st.get("meta_currency") or st["currency"],
           "settings": _mads_settings(st["id"])}
    try:
        out = await meta_brain.reply(key, hist, lambda n, a: _mads_tool(st, conn, n, a, who), ctx)
    except meta_brain.BrainError as e:
        raise HTTPException(400, str(e))
    with db._conn() as con:
        con.execute("INSERT INTO mads_chat (store_id, role, content, who, created_at) VALUES (?, 'assistant', ?, 'ads AI', ?)",
                    (st["id"], out["text"], now()))
    return {"text": out["text"]}


# ---------------------------------------------------------------- Apps → Agent (ops agent: Slack, invoices, job log)

_agent_ready = {"ok": False}
AGENT_DAYS = 90                    # free Slack keeps 90 days; that's as far back as the agent can read


def _agent_db():
    if _agent_ready["ok"]:
        return
    with db._conn() as con:
        con.executescript(agent.SCHEMA)
        for table, col, kind in (("agent_questions", "kind", "TEXT"), ("agent_questions", "data", "TEXT")):
            if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
        con.execute("UPDATE agent_jobs SET status = 'failed', summary = 'Stopped by an app restart: read the channel again to carry on '"
                    " || '(nothing is filed twice).' WHERE status = 'running'")
    _agent_ready["ok"] = True


def _slack_token() -> str:
    t = db.get_setting("slack_bot_token")
    if not t:
        raise HTTPException(400, "Slack isn't connected yet (Agent → Slack).")
    return t


def _agent_who(request: Request) -> str:
    return ((getattr(request.state, "user", None) or {}).get("email") or "")


async def _agent_sync_slack(token: str) -> dict:
    """Refresh the channels the agent is in, and the people (for names in the log)."""
    _agent_db()
    chans = await slack_client.channels(token)
    people = await slack_client.users(token)
    with db._conn() as con:
        for u in people:
            con.execute("INSERT OR REPLACE INTO slack_users (id, name, real_name, is_bot, team) VALUES (?,?,?,?,?)",
                        (u["id"], u.get("name"), (u.get("profile") or {}).get("real_name") or u.get("real_name"),
                         1 if u.get("is_bot") else 0, u.get("team_id")))
        for c in chans:
            role, supplier = agent.guess(c.get("name"))
            con.execute("INSERT INTO slack_channels (id, name, private, role, supplier, members) VALUES (?,?,?,?,?,?)"
                        " ON CONFLICT(id) DO UPDATE SET name = excluded.name, private = excluded.private, members = excluded.members",
                        (c["id"], c.get("name"), 1 if c.get("is_private") else 0, role, supplier, c.get("num_members")))
    return {"channels": len(chans), "people": len(people)}


def _slack_names() -> dict:
    with db._conn() as con:
        return {r["id"]: r["real_name"] or r["name"] for r in con.execute("SELECT * FROM slack_users")}


def _agent_save_msg(con, channel: str, m: dict):
    files = [{"id": f.get("id"), "name": f.get("name") or f.get("title"), "mimetype": f.get("mimetype"),
              "size": f.get("size"), "url": f.get("url_private_download") or f.get("url_private")}
             for f in m.get("files") or [] if f.get("id")]
    con.execute("INSERT OR REPLACE INTO slack_messages (channel, ts, thread_ts, user, text, files, subtype, at) VALUES (?,?,?,?,?,?,?,?)",
                (channel, m["ts"], m.get("thread_ts"), m.get("user") or m.get("bot_id"), m.get("text") or "",
                 json.dumps(files) if files else None, m.get("subtype"), agent.ts_iso(m["ts"])))


def _agent_invoice_file(f: dict) -> bool:
    """A file worth the AI's look: PDF / Excel / CSV / Word / image. DayOne's daily "<store> <date> Tracking.xlsx"
    sheets are left alone without opening them."""
    name = (f.get("name") or "").lower()
    kind = name.endswith(agent.CANDIDATE_EXT) or (f.get("mimetype") or "") in (
        "application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel",
        "text/csv", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/png", "image/jpeg")
    return kind and "tracking" not in name


def _named_invoice(name: str) -> bool:
    return bool(re.search(r"invoice|\binv\b|\binv[-_ ]?\d", (name or "").lower()))


def _file_text(data: bytes, name: str) -> str:
    try:
        if data[:4] == b"%PDF":
            return invoices.pdf_text(data)
        if data[:2] == b"PK" and (name or "").lower().endswith(".docx"):
            import io
            import zipfile
            xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8", "ignore")
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))
        if data[:2] == b"PK":
            return invoices.xlsx_text(data, name)
        return data.decode("utf-8", "ignore")
    except Exception as e:
        return f"(couldn't read the text: {type(e).__name__})"


def _agent_paused() -> bool:
    return db.get_setting("agent_paused") == "1"


PROOF_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "agent_proofs")


def _agent_store_question(store: str, file_names: list, job_id=None):
    """One Yes / No question per store that isn't connected: include its invoices or not."""
    n = agent.norm(store)
    with db._conn() as con:
        old = con.execute("SELECT id, data FROM agent_questions WHERE kind = 'store' AND answer IS NULL AND data LIKE ?",
                          (f'%"norm": "{n}"%',)).fetchone()
        filed = con.execute("SELECT COUNT(*) FROM agent_files WHERE invoice_id IS NOT NULL AND lower(name) LIKE ?",
                            (f"%{store.lower()}%",)).fetchone()[0]
        if old:
            d = json.loads(old["data"] or "{}")
            d["files"] = sorted(set(d.get("files", []) + file_names))[:50]
            d["filed"] = filed
            con.execute("UPDATE agent_questions SET data = ? WHERE id = ?", (json.dumps(d), old["id"]))
            return old["id"]
        return con.execute("INSERT INTO agent_questions (area, kind, question, context, data, job_id, asked_at) VALUES (?,?,?,?,?,?,?)",
                           ("invoices", "store", f"“{store}” isn't one of your connected stores. Include its invoices in the supplier "
                            "accounts (a store you ran before), or leave them out (not yours)?",
                            "Until you answer, its invoices wait and change nothing.",
                            json.dumps({"store": store, "norm": n, "files": file_names[:50], "filed": filed}), job_id, agent.now())).lastrowid


async def _agent_file_invoices(job_id: int, ch: dict, items: list):
    """Look at each file once: the AI says what it is. Invoices are filed (a store that isn't connected waits for the
    owner's yes / no), bank transfer notices are kept for the supplier accounts, the rest is left alone.
    items: [(message dict, file dict)]."""
    token = _slack_token()
    names = _slack_names()
    stores = db.list_stores()
    decided = agent.store_decisions()
    filed = skipped = failed = 0
    for m, f in items:
        if _agent_paused():
            agent.step(job_id, "wait", "Paused by a person", "Press Resume on the Agent desk, then read the channel again to carry on.")
            break
        with db._conn() as con:
            if con.execute("SELECT 1 FROM agent_files WHERE file_id = ?", (f["id"],)).fetchone():
                continue
        when_ = agent.ts_iso(m["ts"])[:10]
        who = names.get(m.get("user"), m.get("user") or "someone")
        agent.step(job_id, "read", f"Found {f['name']}", f"Posted by {who} in #{ch['name']} on {when_}"
                   + (f": “{(m.get('text') or '')[:200]}”" if m.get("text") else ""), {"file": f["id"], "ts": m["ts"]})
        outcome, invoice_id = "", None
        low = (f["name"] or "").lower()
        is_image = low.endswith(tuple(agent.IMAGE_TYPES))
        try:
            data = await slack_client.download(token, f["url"])
            key = db.get_setting("anthropic_api_key")
            if key:
                try:
                    what = await agent.classify(key, f["name"] or "", "" if is_image else await asyncio.to_thread(_file_text, data, f["name"] or ""),
                                                image=data if is_image and len(data) < 4_500_000 else None)
                except Exception as e:
                    what = {"kind": "invoice" if _named_invoice(f["name"]) else "other", "confidence": 0.5,
                            "reason": f"AI unavailable ({type(e).__name__}); went by the name"}
            else:
                what = {"kind": "invoice" if _named_invoice(f["name"]) else "other", "confidence": 0.9 if _named_invoice(f["name"]) else 0.5,
                        "reason": "No AI key: went by the name"}
            sure = float(what.get("confidence") or 0)
            kind = what.get("kind")
            agent.step(job_id, "think", f"Looks like: {'bank transfer notice' if kind == 'payment' else kind} ({sure:.0%} sure)"
                       + (f" · store “{what.get('store')}”" if what.get("store") else ""), what.get("reason") or "")

            # a bank transfer notice / payment proof: keep it, for the supplier accounts
            if kind == "payment" and sure >= 0.7:
                os.makedirs(PROOF_DIR, exist_ok=True)
                ext = "." + low.rsplit(".", 1)[-1] if "." in low else ".bin"
                path = os.path.join(PROOF_DIR, f"{f['id']}{ext}")
                with open(path, "wb") as fh:
                    fh.write(data)
                sup = (what.get("supplier") or ch.get("supplier") or "").strip()
                with db._conn() as con:
                    con.execute("INSERT OR REPLACE INTO agent_payments (file_id, channel, ts, name, path, supplier, amount, currency, paid_on,"
                                " reference, payee, note, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (f["id"], ch["id"], m["ts"], f["name"], path, sup, what.get("amount"), (what.get("currency") or "").upper()[:3],
                                 (what.get("paid_on") or when_)[:10], (what.get("reference") or "")[:120], (what.get("payee") or "")[:120],
                                 (what.get("reason") or "")[:500], agent.now()))
                outcome = (f"Kept bank transfer notice: {what.get('currency') or ''} {what.get('amount') if what.get('amount') is not None else '?'}"
                           f" on {what.get('paid_on') or when_}" + (f", ref {what.get('reference')}" if what.get("reference") else "")
                           + (f", to {sup}" if sup else ""))
                agent.step(job_id, "act", outcome, "Kept with the supplier's payments; checked against the bank (Airwallex / PayPal) "
                           "payments on the Agent desk → Suppliers.")
                with db._conn() as con:
                    con.execute("INSERT OR REPLACE INTO agent_files (file_id, channel, ts, name, job_id, invoice_id, outcome, at)"
                                " VALUES (?,?,?,?,?,NULL,?,?)", (f["id"], ch["id"], m["ts"], f["name"], job_id, outcome, agent.now()))
                filed += 1
                continue

            if kind != "invoice" or sure < 0.75:
                outcome = f"Not filed: {kind} ({sure:.0%}) – {what.get('reason') or ''}"[:300]
                if kind == "invoice" or (sure < 0.6 and _named_invoice(f["name"])):
                    agent.ask("invoices", f"Is “{f['name']}” from #{ch['name']} ({when_}) an invoice? I wasn't sure enough to file it "
                              f"({what.get('reason') or ''}). If yes, upload it in COG → Invoices and tell me how to recognise them.",
                              "", job_id)
                    agent.step(job_id, "wait", "Not sure: asked you instead of filing it")
                else:
                    agent.step(job_id, "skip", "Not an invoice: left alone")
                with db._conn() as con:
                    con.execute("INSERT OR REPLACE INTO agent_files (file_id, channel, ts, name, job_id, invoice_id, outcome, at)"
                                " VALUES (?,?,?,?,?,NULL,?,?)", (f["id"], ch["id"], m["ts"], f["name"], job_id, outcome, agent.now()))
                skipped += 1
                continue

            # whose store? one that isn't connected waits for the owner's yes / no
            store = agent.store_from_name(f["name"]) or (what.get("store") or "").strip()
            if store and not agent.known_store(store, stores):
                d = decided.get(agent.norm(store))
                if d == "exclude":
                    outcome = f"Not filed: “{store}” isn't yours (your answer)"
                    agent.step(job_id, "skip", outcome)
                    with db._conn() as con:
                        con.execute("INSERT OR REPLACE INTO agent_files (file_id, channel, ts, name, job_id, invoice_id, outcome, at)"
                                    " VALUES (?,?,?,?,?,NULL,?,?)", (f["id"], ch["id"], m["ts"], f["name"], job_id, outcome, agent.now()))
                    skipped += 1
                    continue
                if d != "include":
                    _agent_store_question(store, [f["name"]], job_id)
                    agent.step(job_id, "wait", f"“{store}” isn't a connected store: asked you whether to include it",
                               "This invoice waits (changes nothing) until you answer on the Questions tab.")
                    skipped += 1
                    continue                     # not recorded: filed (or not) once you answer

            r = await _ingest_invoice(data, f["name"] or "invoice", f"agent (Slack #{ch['name']})")
            invoice_id = r.get("id")
            if invoice_id and not r.get("attached"):
                r["supplier"] = _agent_fix_supplier(invoice_id, ch, what.get("supplier") or "", job_id) or r.get("supplier")
            if r.get("attached"):
                outcome = f"Already in the app (invoice {r.get('number') or '?'}): kept this file with it"
                agent.step(job_id, "skip", outcome)
                skipped += 1
            else:
                counts = ", ".join(f"{v} {k}" for k, v in (r.get("counts") or {}).items() if v)
                outcome = (f"Filed invoice {r.get('number') or '(no number)'} from {r.get('supplier') or '?'}"
                           + (f" for {store}" if store else "") + f": {r.get('lines')} lines, total {r.get('total')}")
                agent.step(job_id, "act", outcome,
                           (f"Matched: {counts}. " if counts else "") +
                           (f"{r['missing']} order(s) expected on it weren't. " if r.get("missing") else "") +
                           "Matched orders now cost what was invoiced (estimates replaced); the AI reads it again to "
                           "double-check, and holds it for a person if it disagrees.", {"invoice_id": invoice_id})
                filed += 1
        except HTTPException as e:
            msg = str(e.detail)
            if "already uploaded" in msg:
                outcome = msg
                agent.step(job_id, "skip", "Already in the app", msg)
                skipped += 1
            else:
                outcome = f"Couldn't file it: {msg}"
                agent.step(job_id, "error", "Couldn't read it as an invoice", msg)
                agent.ask("invoices", f"I couldn't read “{f['name']}” from #{ch['name']} ({when_}) as an invoice. "
                          "Is it one? If yes, what's different about it (layout, supplier, currency)?", msg, job_id)
                failed += 1
        except slack_client.SlackError as e:
            outcome = f"Slack: {e}"
            agent.step(job_id, "error", "Couldn't download it from Slack", str(e))
            failed += 1
        with db._conn() as con:
            con.execute("INSERT OR REPLACE INTO agent_files (file_id, channel, ts, name, job_id, invoice_id, outcome, at)"
                        " VALUES (?,?,?,?,?,?,?,?)", (f["id"], ch["id"], m["ts"], f["name"], job_id, invoice_id, outcome, agent.now()))
    return filed, skipped, failed


def _agent_supplier_for(ch: dict, ai_supplier: str = "") -> str:
    """The real supplier for a file in a channel: the supplier names the orders sheet uses (Dayone, Graypop …), picked
    by the channel's supplier (dayone → Dayone, gray → Graypop) or what the AI read. '' if none fits."""
    with db._conn() as con:
        names = [r[0] for r in con.execute("SELECT DISTINCT supplier FROM cog_lines WHERE supplier IS NOT NULL AND supplier != ''")]
    names += list(db.supplier_accounts().keys())
    stores = {agent.norm(x["name"]) for x in db.list_stores()}
    names = [n for n in dict.fromkeys(names) if agent.norm(n) and agent.norm(n) not in stores]
    for hint in (ch.get("supplier") or "", ai_supplier or ""):
        h = agent.norm(hint)
        if not h:
            continue
        for n in names:
            if agent.norm(n) == h or (len(h) >= 3 and agent.norm(n).startswith(h)) or (len(agent.norm(n)) >= 3 and h.startswith(agent.norm(n))):
                return n
    return ""


def _agent_fix_supplier(invoice_id: int, ch: dict, ai_supplier: str = "", job_id=None) -> str:
    """Invoices from a supplier's channel belong to that supplier, whatever name the reader picked up (DayOne's PDFs
    carry the store's name, so the reader took it as the supplier)."""
    inv = db.get_invoice(invoice_id)
    real = _agent_supplier_for(ch, ai_supplier)
    if not inv or not real or (inv.get("supplier") or "") == real:
        return ""
    with db._conn() as con:
        con.execute("UPDATE invoices SET supplier = ? WHERE id = ?", (real, invoice_id))
    if job_id:
        agent.step(job_id, "act", f"Supplier set to {real}", f"The reader had “{inv.get('supplier') or '?'}” (the store's name on the invoice); "
                   f"it came from #{ch['name']}, so it's {real}'s invoice.")
    return real


@app.post("/api/agent/fix-suppliers")
async def api_agent_fix_suppliers(request: Request):
    """Put the right supplier on invoices the agent already filed (one-off repair of the 15 Oct filing)."""
    _agent_db()
    if (request.state.user or {}).get("role", "owner") != "owner":
        raise HTTPException(403, "Only an owner.")
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT f.invoice_id, f.name, c.* FROM agent_files f JOIN slack_channels c ON c.id = f.channel"
                                             " WHERE f.invoice_id IS NOT NULL")]
    jid = agent.start("repair", "Put the right supplier on invoices filed from Slack", "invoices", {"by": _agent_who(request)})
    changed = []
    for r in rows:
        inv = db.get_invoice(r["invoice_id"])
        real = _agent_fix_supplier(r["invoice_id"], r)
        if real:
            changed.append((r["invoice_id"], inv.get("supplier"), real))
    agent.step(jid, "act", f"Relabelled {len(changed)} invoice(s)", "; ".join(f"#{i}: {a} → {b}" for i, a, b in changed[:40]) or "Nothing to change.")
    agent.finish(jid, "done", f"{len(changed)} relabelled")
    return {"ok": True, "changed": len(changed), "detail": changed[:80]}


def _agent_review_filed_stores():
    """Invoices already filed for stores that aren't connected: ask about each store once."""
    stores = db.list_stores()
    decided = agent.store_decisions()
    by = defaultdict(list)
    with db._conn() as con:
        for r in con.execute("SELECT name FROM agent_files WHERE invoice_id IS NOT NULL"):
            s = agent.store_from_name(r["name"])
            if s and not agent.known_store(s, stores) and agent.norm(s) not in decided:
                by[s].append(r["name"])
    for s, files in by.items():
        _agent_store_question(s, files)
    return len(by)


_agent_locks: dict = {}


async def _agent_read_channel(job_id: int, ch: dict, file_invoices: bool = True):
    """One read of a channel at a time: a second run waits for the first, then only handles what's left."""
    lock = _agent_locks.setdefault(ch["id"], asyncio.Lock())
    if lock.locked():
        agent.step(job_id, "wait", f"Waiting: another run is reading #{ch['name']}", "It carries on with whatever is left when that one ends.")
    async with lock:
        with db._conn() as con:
            fresh = con.execute("SELECT * FROM slack_channels WHERE id = ?", (ch["id"],)).fetchone()
        await _agent_read_channel_now(job_id, dict(fresh) if fresh else ch, file_invoices)


async def _agent_read_channel_now(job_id: int, ch: dict, file_invoices: bool = True):
    """Copy a channel's last 90 days (and threads) into EcomVital, then file any supplier invoices in it."""
    token = _slack_token()
    try:
        oldest = (datetime.now(timezone.utc) - timedelta(days=AGENT_DAYS)).timestamp()
        if ch.get("read_to"):
            oldest = max(oldest, float(ch["read_to"]))
        msgs = await slack_client.history(token, ch["id"], f"{oldest:.6f}")
        threads = [m for m in msgs if m.get("reply_count")]
        for m in threads:
            msgs += [dict(r, thread_ts=m["ts"]) for r in await slack_client.replies(token, ch["id"], m["ts"])]
        with db._conn() as con:
            for m in msgs:
                if m.get("ts"):
                    _agent_save_msg(con, ch["id"], m)
            newest = max([float(m["ts"]) for m in msgs if m.get("ts")] + [float(ch.get("read_to") or 0)])
            con.execute("UPDATE slack_channels SET read_to = ?, read_at = ? WHERE id = ?",
                        (f"{newest:.6f}" if newest else ch.get("read_to"), agent.now(), ch["id"]))
        since = agent.ts_iso((datetime.now(timezone.utc) - timedelta(days=AGENT_DAYS)).timestamp())
        with db._conn() as con:
            kept = [dict(r) for r in con.execute("SELECT * FROM slack_messages WHERE channel = ? AND files IS NOT NULL AND at >= ?"
                                                 " ORDER BY ts", (ch["id"], since))]
        files = [({"ts": k["ts"], "user": k["user"], "text": k["text"]}, f) for k in kept for f in json.loads(k["files"]) if f.get("id")]
        agent.step(job_id, "read", f"Read {len(msgs)} messages from #{ch['name']}",
                   f"Since {agent.ts_iso(oldest)[:10]}, including {len(threads)} thread(s); {len(files)} file(s) attached. "
                   "Kept in EcomVital (free Slack forgets after 90 days).")
        if file_invoices and ch.get("role") == "supplier_invoice":
            with db._conn() as con:
                done_ids = {r[0] for r in con.execute("SELECT file_id FROM agent_files WHERE channel = ?", (ch["id"],))}
            inv = sorted([x for x in files if _agent_invoice_file(x[1]) and x[1]["id"] not in done_ids], key=lambda x: float(x[0]["ts"]))
            other = [x[1]["name"] for x in files if not _agent_invoice_file(x[1])]
            agent.step(job_id, "think", f"{len(inv)} file(s) to look at; {len(other)} left alone (tracking sheets, photos)",
                       "Each file is read by the AI first: only real invoices are filed, with the same reader as COG → Invoices. "
                       "Unsure ones become questions for you.")
            filed, skipped, failed = await _agent_file_invoices(job_id, ch, inv)
            agent.finish(job_id, "done", f"{len(msgs)} messages read · {filed} filed (invoices + bank transfer notices), {skipped} skipped or waiting for you, {failed} couldn't be read")
        else:
            agent.finish(job_id, "done", f"{len(msgs)} messages read")
    except Exception as e:
        agent.step(job_id, "error", "Stopped", str(getattr(e, "detail", None) or e)[:600])
        agent.finish(job_id, "failed", str(getattr(e, "detail", None) or e)[:300])


def _agent_channel(cid: str) -> dict:
    _agent_db()
    with db._conn() as con:
        r = con.execute("SELECT * FROM slack_channels WHERE id = ?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Channel not found: press Refresh channels.")
    return dict(r)


@app.get("/agent", response_class=HTMLResponse)
def agent_page():
    return _page("agent.html", ("agent.js", "agent.css", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/agent/status")
def api_agent_status(request: Request):
    _agent_db()
    team = json.loads(db.get_setting("slack_team") or "{}")
    try:
        _agent_review_filed_stores()
    except Exception:
        pass
    with db._conn() as con:
        chans = [dict(r) for r in con.execute("SELECT * FROM slack_channels ORDER BY role, name")]
        counts = dict(con.execute("SELECT channel, COUNT(*) FROM slack_messages GROUP BY channel").fetchall())
        filed = dict(con.execute("SELECT channel, COUNT(*) FROM agent_files WHERE invoice_id IS NOT NULL GROUP BY channel").fetchall())
        open_q = con.execute("SELECT COUNT(*) FROM agent_questions WHERE answer IS NULL").fetchone()[0]
        running = con.execute("SELECT COUNT(*) FROM agent_jobs WHERE status = 'running'").fetchone()[0]
    for c in chans:
        c["messages"], c["invoices"] = counts.get(c["id"], 0), filed.get(c["id"], 0)
        c["role_name"] = agent.ROLES.get(c["role"], c["role"])
    owner = (request.state.user or {}).get("role", "owner") == "owner"
    return {"connected": bool(db.get_setting("slack_bot_token")), "paused": _agent_paused(), "team": team, "channels": chans, "roles": agent.ROLES,
            "areas": agent.AREAS, "open_questions": open_q, "running": running, "days": AGENT_DAYS,
            "manifest": slack_client.manifest(BASE_URL) if owner else None, "owner": owner}


@app.post("/api/agent/slack/connect")
async def api_agent_connect(payload: dict, request: Request):
    """Owner: paste the Slack app's bot token and signing secret."""
    if (request.state.user or {}).get("role", "owner") != "owner":
        raise HTTPException(403, "Only an owner can connect Slack.")
    token = str(payload.get("token") or "").strip()
    secret = str(payload.get("secret") or "").strip()
    if not token.startswith("xoxb-"):
        raise HTTPException(400, "Paste the Bot User OAuth Token: it starts with xoxb-.")
    if not re.fullmatch(r"[0-9a-f]{32}", secret):
        raise HTTPException(400, "Paste the Signing Secret (Basic Information → App Credentials): 32 letters and numbers.")
    try:
        me = await slack_client.whoami(token)
    except slack_client.SlackError as e:
        raise HTTPException(400, str(e))
    db.set_setting("slack_bot_token", token)
    db.set_setting("slack_signing_secret", secret)
    db.set_setting("slack_team", json.dumps(me))
    got = await _agent_sync_slack(token)
    return {"ok": True, "team": me, **got}


@app.post("/api/agent/slack/sync")
async def api_agent_sync():
    try:
        return {"ok": True, **(await _agent_sync_slack(_slack_token()))}
    except slack_client.SlackError as e:
        raise HTTPException(400, str(e))


@app.put("/api/agent/channel/{cid}")
def api_agent_channel(cid: str, payload: dict):
    ch = _agent_channel(cid)
    role = payload.get("role") if payload.get("role") in agent.ROLES else ch["role"]
    supplier = str(payload.get("supplier") if "supplier" in payload else ch["supplier"] or "").strip()[:60]
    with db._conn() as con:
        con.execute("UPDATE slack_channels SET role = ?, supplier = ? WHERE id = ?", (role, supplier, cid))
    return {"ok": True}


@app.post("/api/agent/channel/{cid}/read")
async def api_agent_read(cid: str, request: Request):
    """Read a channel's last 90 days (and file its invoices if it's a supplier-invoice channel), in the background."""
    _slack_token()
    ch = _agent_channel(cid)
    jid = agent.start("history", f"Read #{ch['name']}" + (" and file its invoices" if ch["role"] == "supplier_invoice" else ""),
                      agent.ROLE_AREA.get(ch["role"], "general"), {"channel": cid, "by": _agent_who(request)})
    asyncio.create_task(_agent_read_channel(jid, ch))
    return {"ok": True, "job": jid}


@app.post("/api/agent/read-all")
async def api_agent_read_all(request: Request):
    """Every channel the agent is in: 90 days of messages, and every supplier invoice filed."""
    _slack_token()
    _agent_db()
    with db._conn() as con:
        chans = [dict(r) for r in con.execute("SELECT * FROM slack_channels ORDER BY CASE role WHEN 'supplier_invoice' THEN 0 ELSE 1 END, name")]
    if not chans:
        raise HTTPException(400, "The agent isn't in any channel yet: /invite @EcomVital Agent in each, then Refresh channels.")

    async def run():
        for ch in chans:
            jid = agent.start("history", f"Read #{ch['name']}" + (" and file its invoices" if ch["role"] == "supplier_invoice" else ""),
                              agent.ROLE_AREA.get(ch["role"], "general"), {"channel": ch["id"], "by": _agent_who(request)})
            await _agent_read_channel(jid, ch)
    asyncio.create_task(run())
    return {"ok": True, "channels": len(chans)}


@app.post("/api/agent/pause")
def api_agent_pause(payload: dict):
    db.set_setting("agent_paused", "1" if payload.get("paused") else "0")
    return {"ok": True, "paused": _agent_paused()}


@app.get("/api/agent/filed")
def api_agent_filed(contains: str = ""):
    """Invoices the agent filed from Slack, optionally only files whose name contains some text (to review / undo)."""
    _agent_db()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM agent_files WHERE invoice_id IS NOT NULL" +
                                             (" AND lower(name) LIKE ?" if contains else "") + " ORDER BY ts",
                                             (f"%{contains.lower()}%",) if contains else ())]
    return {"files": rows}


@app.post("/api/agent/undo-filed")
async def api_agent_undo_filed(payload: dict, request: Request):
    """Remove invoices the agent filed from files that weren't invoices (e.g. tracking sheets) and put the order costs back."""
    if (request.state.user or {}).get("role", "owner") != "owner":
        raise HTTPException(403, "Only an owner can undo the agent's filing.")
    contains = str(payload.get("contains") or "").strip()
    if not contains:
        raise HTTPException(400, "Say which files, e.g. tracking.")
    return {"ok": True, "removed": await _agent_undo(contains, _agent_who(request))}


async def _agent_undo(contains: str, who: str) -> int:
    """Remove invoices the agent filed from files whose name contains this, putting order costs back. Agent-made only."""
    _agent_db()
    contains = contains.strip().lower()
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM agent_files WHERE invoice_id IS NOT NULL AND lower(name) LIKE ?",
                                             (f"%{contains}%",))]
    jid = agent.start("undo", f"Undo: invoices filed from files named “{contains}”", "invoices", {"by": who})
    removed = 0
    for r in rows:
        inv = db.get_invoice(r["invoice_id"])
        if inv and not (inv.get("uploaded_by") or "").startswith("agent"):
            agent.step(jid, "skip", f"Kept invoice {r['invoice_id']}", "A person uploaded it, not the agent.")
            continue
        if inv:
            await api_invoice_delete(r["invoice_id"])
            removed += 1
        with db._conn() as con:
            con.execute("DELETE FROM agent_files WHERE file_id = ?", (r["file_id"],))
    agent.step(jid, "act", f"Removed {removed} invoice(s) filed from files named “{contains}”",
               "Their orders' costs went back to what they were before (sheet / catalog / estimate).")
    agent.finish(jid, "done", f"{removed} removed")
    return removed


@app.post("/api/agent/store-decision")
async def api_agent_store_decision(payload: dict, request: Request):
    """The owner's answer about a store that isn't connected: include its invoices (a store run before) or not."""
    _agent_db()
    if (request.state.user or {}).get("role", "owner") != "owner":
        raise HTTPException(403, "Only an owner decides which stores count.")
    with db._conn() as con:
        q = con.execute("SELECT * FROM agent_questions WHERE id = ? AND kind = 'store'", (payload.get("question_id"),)).fetchone()
    if not q:
        raise HTTPException(404, "Question not found.")
    d = json.loads(q["data"] or "{}")
    include = bool(payload.get("include"))
    who = _agent_who(request)
    decisions = agent.store_decisions()
    decisions[d["norm"]] = "include" if include else "exclude"
    db.set_setting("agent_stores", json.dumps(decisions))
    text = (f"Yes: include “{d['store']}” (a store we ran before)." if include else f"No: “{d['store']}” isn't ours, leave its invoices out.")
    with db._conn() as con:
        con.execute("UPDATE agent_questions SET answer = ?, answered_by = ?, answered_at = ? WHERE id = ?", (text, who, agent.now(), q["id"]))
        con.execute("INSERT INTO agent_lessons (area, text, source, created_by, created_at) VALUES (?,?,?,?,?)",
                    ("invoices", f"Invoices for the store “{d['store']}”: " + ("include them (an old store of ours)." if include
                                                                              else "not ours, never file them."),
                     f"answer:{q['id']}", who, agent.now()))
    removed = 0
    if not include and payload.get("remove_filed"):
        removed = await _agent_undo(d["store"], who)
    started = False
    if include:                                  # the invoices that waited: file them now (several answers = one run)
        with db._conn() as con:
            chans = [dict(r) for r in con.execute("SELECT * FROM slack_channels WHERE role = 'supplier_invoice'")]

        async def run():
            for ch in chans:
                lock = _agent_locks.setdefault(ch["id"], asyncio.Lock())
                if lock.locked():                # a run is already going: it picks these up too
                    continue
                jid = agent.start("history", f"File the invoices that waited for an answer (#{ch['name']})", "invoices", {"by": who})
                await _agent_read_channel(jid, ch)
        asyncio.create_task(run())
        started = True
    return {"ok": True, "removed": removed, "filing": started}


async def _agent_bank_payments():
    """Supplier accounts as COG → Suppliers shows them (opening, invoices, bank payments), and every payment to a supplier
    or still to sort, from Airwallex / PayPal."""
    data = await api_suppliers()
    pays = []
    for led in data["suppliers"]:
        pays += [{**p, "supplier": led["supplier"]} for p in led["payments"]]
    pays += [{**p, "supplier": None} for p in data["to_assign"]]
    return data, pays


async def _agent_match_proofs() -> dict:
    """Check each kept bank transfer notice against the real payments out of Airwallex / PayPal (same amount within 1%
    or $1, within 5 days)."""
    _agent_db()
    data, pays = await _agent_bank_payments()
    try:
        table = await fx.table("USD")
    except Exception:
        table = None
    usd = lambda amt, cur: amt * (fx.factor(table, cur, "USD") if table and cur and cur != "USD" else 1.0)
    with db._conn() as con:
        proofs = [dict(r) for r in con.execute("SELECT * FROM agent_payments ORDER BY paid_on")]
    taken = {p["matched"] for p in proofs if p["matched"]}
    matched = 0
    for pr in proofs:
        if pr["matched"] or pr["amount"] is None:
            continue
        try:
            want = usd(float(pr["amount"]), pr["currency"] or "USD")
            day = date.fromisoformat((pr["paid_on"] or "")[:10])
        except (ValueError, TypeError):
            continue
        best = None
        for p in pays:
            if str(p["id"]) in taken:
                continue
            try:
                gap = abs((date.fromisoformat(p["day"]) - day).days)
            except ValueError:
                continue
            if gap <= 5 and abs(p["usd"] - want) <= max(1.0, want * 0.01):
                if not best or gap < best[0]:
                    best = (gap, p)
        with db._conn() as con:
            if best:
                p = best[1]
                taken.add(str(p["id"]))
                matched += 1
                con.execute("UPDATE agent_payments SET matched = ?, match_note = ? WHERE id = ?",
                            (str(p["id"]), f"Matches {p['name']}: {p['currency']} {p['amount']:.2f} on {p['day']}"
                             + (f" (filed under {p['supplier']})" if p.get("supplier") else " (not yet assigned to a supplier in Expenses)"),
                             pr["id"]))
            else:
                con.execute("UPDATE agent_payments SET match_note = ? WHERE id = ?",
                            (f"No bank payment of about USD {want:.2f} found within 5 days of {day}", pr["id"]))
    return {"matched": matched, "proofs": len(proofs)}


def _order_sort(name: str):
    m = re.findall(r"\d+", name or "")
    return (int(m[-1]) if m else 0, name or "")


async def _agent_positions() -> list:
    """Per supplier: opening balance, invoices oldest first with the payments applied to them (oldest first), so:
    paid up to which invoice / order, invoiced up to which order, and what's on credit (invoiced but unpaid, plus
    shipped but not invoiced yet). Transfer notices kept by the agent are shown with their bank match."""
    _agent_db()
    data, _pays = await _agent_bank_payments()
    lines = defaultdict(list)
    for l in db.invoice_lines():
        if l["status"] not in ("duplicate", "superseded") and (l.get("order_name") or l.get("order_key")):
            lines[l["invoice_id"]].append(l.get("order_name") or l.get("order_key"))
    excluded = {k for k, v in agent.store_decisions().items() if v == "exclude"}
    with db._conn() as con:
        proofs = [dict(r) for r in con.execute("SELECT * FROM agent_payments ORDER BY paid_on")]
        files = {r["invoice_id"]: r["name"] for r in con.execute("SELECT invoice_id, name FROM agent_files WHERE invoice_id IS NOT NULL")}
    out = []
    for led in data["suppliers"]:
        invs = []
        for i in sorted(led["invoices"], key=lambda x: (x.get("date") or "", x["id"])):
            fname = files.get(i["id"], "")
            st = agent.store_from_name(fname) or i.get("store") or ""
            if agent.norm(st) in excluded:
                continue
            orders = sorted(set(lines.get(i["id"], [])), key=_order_sort)
            invs.append({"id": i["id"], "number": i.get("number"), "date": i.get("date"), "store": st or i.get("store"),
                         "total": round(float(i.get("total") or 0), 2), "first_order": orders[0] if orders else "",
                         "last_order": orders[-1] if orders else "", "orders": len(orders), "ai_check": i.get("ai_check")})
        opening = float(led.get("opening") or 0)
        left = float(led.get("paid") or 0) - opening              # payments clear the opening balance first
        paid_to, part = None, None
        for i in invs:
            if left >= i["total"] - 0.005:
                left -= i["total"]
                i["paid"] = "paid"
                paid_to = i
            elif left > 0.005:
                i["paid"] = f"part paid ({left:.2f} of {i['total']:.2f})"
                part = i
                left = 0
            else:
                i["paid"] = "on credit"
        invoiced = round(sum(i["total"] for i in invs), 2)
        unpaid = round(max(0.0, opening + invoiced - float(led.get("paid") or 0)), 2)
        last_by_store = {}
        for i in invs:
            if i["last_order"]:
                last_by_store[i["store"]] = {"order": i["last_order"], "invoice": i["number"], "date": i["date"]}
        mine = [p for p in proofs if agent.norm(led["supplier"]) and (agent.norm(led["supplier"]) in agent.norm(p["supplier"])
                                                                       or agent.norm(p["supplier"]) in agent.norm(led["supplier"]))
                and p["supplier"]]
        out.append({
            "supplier": led["supplier"], "opening": opening, "opening_date": led.get("opening_date"), "opening_set": led.get("set"),
            "invoiced": invoiced, "paid": round(float(led.get("paid") or 0), 2), "unpaid_invoiced": unpaid,
            "not_invoiced": led.get("not_invoiced"), "credit": round(unpaid + float(led.get("not_invoiced") or 0), 2),
            "overpaid": round(max(0.0, left), 2) if invs else 0.0,
            "paid_up_to": paid_to and {k: paid_to[k] for k in ("number", "date", "store", "last_order")},
            "part_paid": part and {k: part[k] for k in ("number", "date", "store", "last_order", "paid")},
            "invoiced_up_to": last_by_store, "invoices": invs[-60:], "late_count": led.get("late_count"),
            "proofs": [{k: p[k] for k in ("id", "name", "amount", "currency", "paid_on", "reference", "payee", "matched", "match_note")} for p in mine],
        })
    unassigned = [{k: p[k] for k in ("id", "name", "amount", "currency", "paid_on", "reference", "payee", "supplier", "matched", "match_note")}
                  for p in proofs if not any(p["id"] in [x["id"] for x in o["proofs"]] for o in out)]
    return {"suppliers": out, "proofs_unassigned": unassigned, "payments_from": data.get("payments_from")}


@app.get("/api/agent/suppliers")
async def api_agent_suppliers():
    _agent_db()
    return await _agent_positions()


@app.post("/api/agent/proofs/check")
async def api_agent_proofs_check():
    return await _agent_match_proofs()


@app.get("/api/agent/proof/{pid}")
def api_agent_proof(pid: int):
    _agent_db()
    with db._conn() as con:
        p = con.execute("SELECT * FROM agent_payments WHERE id = ?", (pid,)).fetchone()
    if not p or not p["path"] or not os.path.exists(p["path"]):
        raise HTTPException(404, "File not found.")
    return FileResponse(p["path"], filename=p["name"], content_disposition_type="inline")


async def _agent_tool(name: str, args: dict, who: str):
    """The agent brain's tools (agent.BRAIN_TOOLS): the app's own figures, never invented."""
    if name == "supplier_positions":
        data = await _agent_positions()
        want = agent.norm(args.get("supplier") or "")
        data["suppliers"] = [s for s in data["suppliers"] if not want or want in agent.norm(s["supplier"])]
        return data
    if name == "find_invoices":
        rows = db.list_invoices()
        want, st = agent.norm(args.get("supplier") or ""), agent.norm(args.get("store") or "")
        out = [{k: i.get(k) for k in ("id", "supplier", "number", "date", "total", "filename", "ai_check", "uploaded_by")}
               for i in rows if (not want or want in agent.norm(i.get("supplier"))) and (not st or st in agent.norm(i.get("filename")))
               and (not args.get("since") or (i.get("date") or "") >= args["since"])]
        return {"invoices": sorted(out, key=lambda x: x["date"] or "")[-150:], "count": len(out)}
    if name == "search_slack":
        q = f"%{(args.get('text') or '').strip()}%"
        days = max(1, min(int(args.get("days") or 30), 90))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        names = _slack_names()
        with db._conn() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT m.*, c.name chan FROM slack_messages m JOIN slack_channels c ON c.id = m.channel WHERE m.at >= ? AND"
                " (m.text LIKE ? OR m.files LIKE ?)" + (" AND c.name LIKE ?" if args.get("channel") else "") + " ORDER BY m.at DESC LIMIT 60",
                (since, q, q) + ((f"%{args['channel']}%",) if args.get("channel") else ()))]
        return {"messages": [{"channel": r["chan"], "at": r["at"], "who": names.get(r["user"], r["user"]), "text": (r["text"] or "")[:600],
                              "files": [x.get("name") for x in json.loads(r["files"] or "[]")]} for r in rows]}
    if name == "ask_team":
        qid = agent.ask(args.get("area") if args.get("area") in agent.AREAS else "general", str(args.get("question") or "")[:1000],
                        str(args.get("why") or "")[:1000])
        return {"ok": True, "question": qid, "note": "It's on the Questions tab; the answer becomes a lesson."}
    if name == "check_transfer_notices":
        return await _agent_match_proofs()
    raise HTTPException(400, f"Unknown tool {name}.")


@app.get("/api/agent/chat")
def api_agent_chat_get():
    _agent_db()
    with db._conn() as con:
        return {"chat": [dict(r) for r in con.execute("SELECT * FROM agent_chat ORDER BY id DESC LIMIT 60")][::-1],
                "ai": bool(db.get_setting("anthropic_api_key"))}


@app.post("/api/agent/chat")
async def api_agent_chat(payload: dict, request: Request):
    """Talk to the agent: it reasons over invoices, payments, transfer notices, Slack and its lessons with its tools."""
    _agent_db()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write something first.")
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the AI key first (Settings → AI).")
    who = _agent_who(request)
    with db._conn() as con:
        con.execute("INSERT INTO agent_chat (role, content, who, at) VALUES ('user', ?, ?, ?)", (text, who, agent.now()))
        hist = [dict(r) for r in con.execute("SELECT role, content FROM agent_chat ORDER BY id DESC LIMIT 16")][::-1]
    try:
        out = await agent.brain(key, hist, lambda n, a: _agent_tool(n, a, who), datetime.now(CASH_TZ).date().isoformat())
    except agent.BrainError as e:
        raise HTTPException(400, str(e))
    with db._conn() as con:
        con.execute("INSERT INTO agent_chat (role, content, who, at) VALUES ('assistant', ?, 'agent', ?)", (out["text"], agent.now()))
    return {"text": out["text"]}


@app.get("/api/agent/jobs")
def api_agent_jobs(status: str = "", area: str = "", q: str = "", limit: int = 100):
    _agent_db()
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if area:
        where.append("area = ?")
        args.append(area)
    if q.strip():
        where.append("(title LIKE ? OR summary LIKE ?)")
        args += [f"%{q.strip()}%"] * 2
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT j.*, (SELECT COUNT(*) FROM agent_steps s WHERE s.job_id = j.id) steps,"
                                             " (SELECT COUNT(*) FROM agent_advice a WHERE a.job_id = j.id) advice FROM agent_jobs j"
                                             + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC LIMIT ?",
                                             (*args, max(1, min(limit, 500))))]
    return {"jobs": rows}


@app.get("/api/agent/jobs/{job_id}")
def api_agent_job(job_id: int):
    _agent_db()
    with db._conn() as con:
        j = con.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
        if not j:
            raise HTTPException(404, "Job not found.")
        steps = [dict(r) for r in con.execute("SELECT * FROM agent_steps WHERE job_id = ? ORDER BY id", (job_id,))]
        advice = [dict(r) for r in con.execute("SELECT * FROM agent_advice WHERE job_id = ? ORDER BY id", (job_id,))]
        qs = [dict(r) for r in con.execute("SELECT * FROM agent_questions WHERE job_id = ? ORDER BY id", (job_id,))]
    for s in steps:
        s["data"] = json.loads(s["data"]) if s.get("data") else None
    return {"job": {**dict(j), "source": json.loads(j["source"] or "{}")}, "steps": steps, "advice": advice, "questions": qs}


@app.post("/api/agent/advice")
def api_agent_advice(payload: dict, request: Request):
    """Advice on a job / step. Kept as a lesson the agent reads before similar work (unless lesson is false)."""
    _agent_db()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write the advice first.")
    who = _agent_who(request)
    with db._conn() as con:
        j = con.execute("SELECT area FROM agent_jobs WHERE id = ?", (payload.get("job_id"),)).fetchone()
        if not j:
            raise HTTPException(404, "Job not found.")
        aid = con.execute("INSERT INTO agent_advice (job_id, step_id, by, at, text) VALUES (?,?,?,?,?)",
                          (payload["job_id"], payload.get("step_id"), who, agent.now(), text[:2000])).lastrowid
        lid = None
        if payload.get("lesson", True):
            area = payload.get("area") if payload.get("area") in agent.AREAS else (j["area"] or "general")
            lid = con.execute("INSERT INTO agent_lessons (area, text, source, created_by, created_at) VALUES (?,?,?,?,?)",
                              (area, text[:2000], f"advice:{aid}", who, agent.now())).lastrowid
            con.execute("UPDATE agent_advice SET lesson_id = ? WHERE id = ?", (lid, aid))
    return {"ok": True, "advice": aid, "lesson": lid}


@app.get("/api/agent/lessons")
def api_agent_lessons():
    _agent_db()
    with db._conn() as con:
        return {"lessons": [dict(r) for r in con.execute("SELECT * FROM agent_lessons ORDER BY active DESC, id DESC")]}


@app.post("/api/agent/lessons")
def api_agent_lesson_add(payload: dict, request: Request):
    _agent_db()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write the lesson first.")
    area = payload.get("area") if payload.get("area") in agent.AREAS else "general"
    with db._conn() as con:
        lid = con.execute("INSERT INTO agent_lessons (area, text, source, created_by, created_at) VALUES (?,?,?,?,?)",
                          (area, text[:2000], "manual", _agent_who(request), agent.now())).lastrowid
    return {"ok": True, "id": lid}


@app.put("/api/agent/lessons/{lid}")
def api_agent_lesson_edit(lid: int, payload: dict):
    _agent_db()
    with db._conn() as con:
        if "text" in payload and str(payload["text"]).strip():
            con.execute("UPDATE agent_lessons SET text = ? WHERE id = ?", (str(payload["text"]).strip()[:2000], lid))
        if "active" in payload:
            con.execute("UPDATE agent_lessons SET active = ? WHERE id = ?", (1 if payload["active"] else 0, lid))
        if payload.get("area") in agent.AREAS:
            con.execute("UPDATE agent_lessons SET area = ? WHERE id = ?", (payload["area"], lid))
    return {"ok": True}


@app.get("/api/agent/questions")
def api_agent_questions(open_only: int = 1):
    _agent_db()
    with db._conn() as con:
        return {"questions": [dict(r) for r in con.execute("SELECT * FROM agent_questions" + (" WHERE answer IS NULL" if open_only else "")
                                                           + " ORDER BY id DESC LIMIT 200")]}


@app.post("/api/agent/questions/{qid}")
def api_agent_answer(qid: int, payload: dict, request: Request):
    """Answer the agent's question; the answer becomes a lesson."""
    _agent_db()
    text = str(payload.get("answer") or "").strip()
    if not text:
        raise HTTPException(400, "Write the answer first.")
    who = _agent_who(request)
    with db._conn() as con:
        q = con.execute("SELECT * FROM agent_questions WHERE id = ?", (qid,)).fetchone()
        if not q:
            raise HTTPException(404, "Question not found.")
        con.execute("UPDATE agent_questions SET answer = ?, answered_by = ?, answered_at = ? WHERE id = ?", (text[:3000], who, agent.now(), qid))
        con.execute("INSERT INTO agent_lessons (area, text, source, created_by, created_at) VALUES (?,?,?,?,?)",
                    (q["area"], f"Q: {q['question']}\nA: {text}"[:3000], f"answer:{qid}", who, agent.now()))
    return {"ok": True}


async def _slack_event(ev: dict):
    """A Slack message as it happens: kept; a file in a supplier-invoice channel is filed as an invoice."""
    _agent_db()
    if ev.get("type") != "message" or not ev.get("channel"):
        return
    m = ev.get("message") if ev.get("subtype") == "message_changed" else ev
    if not m or not m.get("ts") or ev.get("subtype") in ("message_deleted", "channel_join", "channel_leave"):
        return
    with db._conn() as con:
        _agent_save_msg(con, ev["channel"], m)
        ch = con.execute("SELECT * FROM slack_channels WHERE id = ?", (ev["channel"],)).fetchone()
    if not ch:                                   # a channel the agent was just invited to
        try:
            await _agent_sync_slack(_slack_token())
        except Exception:
            return
        with db._conn() as con:
            ch = con.execute("SELECT * FROM slack_channels WHERE id = ?", (ev["channel"],)).fetchone()
        if not ch:
            return
    ch = dict(ch)
    files = [{"id": x.get("id"), "name": x.get("name") or x.get("title"), "mimetype": x.get("mimetype"),
              "url": x.get("url_private_download") or x.get("url_private")} for x in m.get("files") or []]
    inv = [(m, f) for f in files if f.get("id") and _agent_invoice_file(f)]
    if ch["role"] == "supplier_invoice" and inv:
        jid = agent.start("invoice", f"New invoice file in #{ch['name']}", "invoices", {"channel": ch["id"], "ts": m["ts"]})
        filed, skipped, failed = await _agent_file_invoices(jid, ch, inv)
        agent.finish(jid, "done" if not failed else "waiting",
                     f"{filed} filed (invoice / transfer notice), {skipped} skipped or waiting for you, {failed} couldn't be read" + (" (question for you)" if failed else ""))


@app.post("/api/slack/events")
async def api_slack_events(request: Request):
    """Slack's Events API (signed). Answered at once; the work happens in the background."""
    body = await request.body()
    secret = db.get_setting("slack_signing_secret") or ""
    if not secret or not slack_client.verify(secret, request.headers.get("X-Slack-Request-Timestamp", ""), body,
                                             request.headers.get("X-Slack-Signature", "")):
        raise HTTPException(401, "Bad signature.")
    payload = json.loads(body or b"{}")
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    if request.headers.get("X-Slack-Retry-Num"):          # already got it the first time
        return {"ok": True}
    ev = payload.get("event") or {}
    if ev:
        async def run():
            try:
                await _slack_event(ev)
            except Exception as e:
                print(f"Slack event failed: {str(getattr(e, 'detail', None) or e)[:300]}", flush=True)
        asyncio.create_task(run())
    return {"ok": True}


# ---------------------------------------------------------------- Apps → Time (desktop app: hours, activity, screenshots)

TIME_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "time_shots")
DOWNLOADS_DIR = os.path.join(STATIC_DIR, "downloads")
_time_ready = {"ok": False}


def _time_db():
    if _time_ready["ok"]:
        return
    with db._conn() as con:
        con.executescript(timetrack.SCHEMA)
    _time_ready["ok"] = True


def _time_cfg() -> dict:
    try:
        got = json.loads(db.get_setting("time_settings") or "{}")
    except ValueError:
        got = {}
    return {**timetrack.DEFAULTS, **{k: v for k, v in got.items() if k in timetrack.DEFAULTS}}


def _time_device(request: Request):
    """The desktop app's device token (Authorization: Bearer evt_…) → (device, user); 401 if not valid."""
    _time_db()
    h = request.headers.get("Authorization", "")
    got = timetrack.device_for(h[7:].strip() if h.lower().startswith("bearer ") else "")
    if not got:
        raise HTTPException(401, "Signed out: sign in again in EcomVital Time.")
    return got


def _time_day_bounds(day: date):
    a = datetime.combine(day, clock.min, CASH_TZ).astimezone(timezone.utc)
    b = datetime.combine(day + timedelta(days=1), clock.min, CASH_TZ).astimezone(timezone.utc)
    return a.strftime("%Y-%m-%dT%H:%M"), b.strftime("%Y-%m-%dT%H:%M")


def _time_totals(con, user_id: int, day: date) -> dict:
    a, b = _time_day_bounds(day)
    r = con.execute("SELECT COUNT(*), COALESCE(SUM(active), 0) FROM time_minutes WHERE user_id = ? AND minute >= ? AND minute < ?",
                    (user_id, a, b)).fetchone()
    return {"minutes": r[0], "active": r[1], "activity": round(r[1] / r[0], 3) if r[0] else None}


# ---- what the desktop app calls (open path; it proves itself with its device token)

@app.post("/api/time/client/login")
async def api_time_login(payload: dict, request: Request):
    """Sign the desktop app in with the person's EcomVital email + password; it gets its own device token."""
    _time_db()
    try:
        token = auth.log_in(payload.get("email"), payload.get("password"), _client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    user = auth.user_for(token)
    auth.log_out(token)                          # only the device token is kept
    if not user:
        raise HTTPException(400, "Couldn't sign in.")
    if user.get("must_change"):
        raise HTTPException(400, "Choose your own password first: log in at hq.ecomvital.com once, then sign in here.")
    dev = timetrack.new_device(user["id"], payload.get("device") or "", payload.get("os") or "", payload.get("version") or "")
    return {"ok": True, "token": dev, "name": user.get("name") or user["email"], "email": user["email"]}


def _time_latest() -> str:
    """The app version the download page has (written by the installer build)."""
    try:
        with open(os.path.join(DOWNLOADS_DIR, "version.txt")) as fh:
            return fh.read().strip()[:20]
    except OSError:
        return ""


@app.get("/api/time/client/config")
def api_time_config(request: Request):
    d, u = _time_device(request)
    today = datetime.now(CASH_TZ).date()
    with db._conn() as con:
        tot = _time_totals(con, u["id"], today)
        try:
            tasks_ = [{"id": r[0], "title": r[1]} for r in con.execute(
                "SELECT id, title FROM tasks WHERE assignee_id = ? AND status NOT IN ('launched', 'done', 'cancelled') ORDER BY id DESC LIMIT 30",
                (u["id"],))]
        except Exception:
            tasks_ = []
    return {"name": u.get("name") or u["email"], "email": u["email"], **_time_cfg(), "today": tot, "tasks": tasks_,
            "latest": _time_latest()}


@app.post("/api/time/client/start")
def api_time_start(payload: dict, request: Request):
    d, u = _time_device(request)
    with db._conn() as con:
        sid = con.execute("INSERT INTO time_sessions (user_id, device_id, started_at, last_seen) VALUES (?,?,?,?)",
                          (u["id"], d["id"], timetrack.now(), timetrack.now())).lastrowid
    return {"ok": True, "session": sid}


@app.post("/api/time/client/stop")
def api_time_stop(payload: dict, request: Request):
    d, u = _time_device(request)
    with db._conn() as con:
        con.execute("UPDATE time_sessions SET ended_at = ?, last_seen = ? WHERE id = ? AND user_id = ?",
                    (timetrack.now(), timetrack.now(), payload.get("session"), u["id"]))
    return {"ok": True}


@app.post("/api/time/client/minutes")
def api_time_minutes(payload: dict, request: Request):
    """A batch of tracked minutes (sent every minute; queued on the computer while offline)."""
    d, u = _time_device(request)
    rows = payload.get("rows") or []
    saved = 0
    with db._conn() as con:
        for r in rows[:2000]:
            try:
                m = timetrack.minute_key(r["minute"])
            except (KeyError, ValueError, TypeError):
                continue
            con.execute("INSERT OR REPLACE INTO time_minutes (user_id, minute, session_id, active, keys, clicks, moves, app, title, task_id)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (u["id"], m, payload.get("session"), 1 if r.get("active") else 0, int(r.get("keys") or 0),
                         int(r.get("clicks") or 0), int(r.get("moves") or 0), str(r.get("app") or "")[:120],
                         str(r.get("title") or "")[:300], int(r["task_id"]) if r.get("task_id") else None))
            saved += 1
        con.execute("UPDATE time_sessions SET last_seen = ? WHERE id = ? AND user_id = ?", (timetrack.now(), payload.get("session"), u["id"]))
        tot = _time_totals(con, u["id"], datetime.now(CASH_TZ).date())
    return {"ok": True, "saved": saved, "today": tot}


@app.post("/api/time/client/discard")
def api_time_discard(payload: dict, request: Request):
    """The person said "remove" after being away: the idle minutes (and screenshots in them) are taken back."""
    d, u = _time_device(request)
    try:
        a, b = timetrack.minute_key(payload["from"]), timetrack.minute_key(payload["to"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "Which minutes?")
    with db._conn() as con:
        n = con.execute("DELETE FROM time_minutes WHERE user_id = ? AND minute >= ? AND minute <= ? AND active = 0", (u["id"], a, b)).rowcount
        gone = [dict(r) for r in con.execute("SELECT id, taken_at FROM time_shots WHERE user_id = ? AND taken_at >= ? AND taken_at <= ?"
                                             " AND deleted_at IS NULL", (u["id"], a, b + ":59"))]
        con.execute("UPDATE time_shots SET deleted_at = ?, deleted_by = 'idle removed by the person' WHERE user_id = ? AND taken_at >= ?"
                    " AND taken_at <= ? AND deleted_at IS NULL", (timetrack.now(), u["id"], a, b + ":59"))
    for g in gone:
        for nm in (f"{g['id']}.jpg", f"{g['id']}_t.jpg"):
            try:
                os.remove(os.path.join(TIME_DIR, str(u["id"]), g["taken_at"][:10], nm))
            except OSError:
                pass
    with db._conn() as con:
        tot = _time_totals(con, u["id"], datetime.now(CASH_TZ).date())
    return {"ok": True, "removed": n, "today": tot}


@app.post("/api/time/client/shot")
async def api_time_shot(request: Request):
    """One screenshot (JPEG of all screens) with when it was taken and what was in front."""
    d, u = _time_device(request)
    form = await request.form()
    up = form.get("file")
    if not up or not hasattr(up, "read"):
        raise HTTPException(400, "No screenshot.")
    data = await up.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "Screenshot too big.")
    taken = str(form.get("taken_at") or timetrack.now())
    with db._conn() as con:
        sid = con.execute("INSERT INTO time_shots (user_id, session_id, taken_at, screens, width, height, bytes, app, title) VALUES (?,?,?,?,?,?,?,?,?)",
                          (u["id"], form.get("session"), taken, int(form.get("screens") or 1), int(form.get("width") or 0),
                           int(form.get("height") or 0), len(data), str(form.get("app") or "")[:120],
                           str(form.get("title") or "")[:300])).lastrowid
    folder = os.path.join(TIME_DIR, str(u["id"]), taken[:10])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{sid}.jpg")
    with open(path, "wb") as fh:
        fh.write(data)
    await asyncio.to_thread(_att_image, path, os.path.join(folder, f"{sid}_t.jpg"), 400)
    return {"ok": True, "id": sid}


# ---- the Time desk

def _time_can_see(request: Request, user_id: int) -> bool:
    me = getattr(request.state, "user", None) or {}
    return me.get("id") == user_id or auth.at_least(me, "time", "manage")


@app.get("/time", response_class=HTMLResponse)
def time_page():
    return _page("time.html", ("time.js", "time.css", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/time/people")
def api_time_people(request: Request):
    _time_db()
    me = request.state.user
    manage = auth.at_least(me, "time", "manage")
    with db._conn() as con:
        tracked = {r[0] for r in con.execute("SELECT DISTINCT user_id FROM time_devices")}
    people = [{"id": u["id"], "name": (u.get("name") or "").strip() or u["email"].split("@")[0], "email": u["email"],
               "has_app": u["id"] in tracked} for u in db.list_users() if manage or u["id"] == me["id"]]
    downloads = sorted(f for f in os.listdir(DOWNLOADS_DIR)) if os.path.isdir(DOWNLOADS_DIR) else []
    return {"people": people, "manage": manage, "me": me["id"], "today": datetime.now(CASH_TZ).date().isoformat(),
            "settings": _time_cfg(), "downloads": downloads, "owner": me.get("role", "owner") == "owner"}


@app.get("/api/time/day")
def api_time_day(request: Request, user: int, day: str = ""):
    """One person's day: totals, 10-minute blocks (activity + screenshots), apps & sites."""
    _time_db()
    if not _time_can_see(request, user):
        raise HTTPException(403, "You can only see your own time.")
    try:
        d = date.fromisoformat(day) if day else datetime.now(CASH_TZ).date()
    except ValueError:
        raise HTTPException(400, "Pick a day.")
    a, b = _time_day_bounds(d)
    cfg = _time_cfg()
    with db._conn() as con:
        mins = [dict(r) for r in con.execute("SELECT * FROM time_minutes WHERE user_id = ? AND minute >= ? AND minute < ? ORDER BY minute",
                                             (user, a, b))]
        shots = [dict(r) for r in con.execute("SELECT * FROM time_shots WHERE user_id = ? AND taken_at >= ? AND taken_at < ? AND deleted_at IS NULL"
                                              " ORDER BY taken_at", (user, a + ":00", b + ":00"))]
        tot = _time_totals(con, user, d)
        tasks_ = {r[0]: r[1] for r in con.execute("SELECT id, title FROM tasks")} if any(m["task_id"] for m in mins) else {}
    blocks = {}
    for m in mins:
        t = datetime.fromisoformat(m["minute"] + ":00+00:00").astimezone(CASH_TZ)
        k = t.replace(minute=t.minute - t.minute % cfg["block_minutes"], second=0).strftime("%H:%M")
        bl = blocks.setdefault(k, {"start": k, "minutes": 0, "active": 0, "apps": defaultdict(int), "shots": [], "tasks": set()})
        bl["minutes"] += 1
        bl["active"] += m["active"]
        bl["apps"][timetrack.app_group(m["app"], m["title"])] += 1
        if m["task_id"]:
            bl["tasks"].add(tasks_.get(m["task_id"], f"#{m['task_id']}"))
    for s in shots:
        t = datetime.fromisoformat(s["taken_at"].replace("Z", "+00:00")).astimezone(CASH_TZ)
        k = t.replace(minute=t.minute - t.minute % cfg["block_minutes"], second=0).strftime("%H:%M")
        bl = blocks.setdefault(k, {"start": k, "minutes": 0, "active": 0, "apps": defaultdict(int), "shots": [], "tasks": set()})
        bl["shots"].append({"id": s["id"], "at": t.strftime("%H:%M:%S"), "app": s["app"], "title": s["title"],
                            "screens": s["screens"], "taken_at": s["taken_at"]})
    out_blocks = []
    for k in sorted(blocks):
        bl = blocks[k]
        top = sorted(bl["apps"].items(), key=lambda kv: -kv[1])
        out_blocks.append({"start": k, "minutes": bl["minutes"], "active": bl["active"],
                           "activity": round(bl["active"] / bl["minutes"], 2) if bl["minutes"] else None,
                           "app": top[0][0] if top else "", "shots": bl["shots"], "tasks": sorted(bl["tasks"])})
    apps = defaultdict(lambda: {"minutes": 0, "active": 0})
    for m in mins:
        g = apps[timetrack.app_group(m["app"], m["title"])]
        g["minutes"] += 1
        g["active"] += m["active"]
    first = datetime.fromisoformat(mins[0]["minute"] + ":00+00:00").astimezone(CASH_TZ).strftime("%H:%M") if mins else None
    last = datetime.fromisoformat(mins[-1]["minute"] + ":00+00:00").astimezone(CASH_TZ).strftime("%H:%M") if mins else None
    me = request.state.user
    return {"day": d.isoformat(), "user": user, "totals": tot, "first": first, "last": last, "blocks": out_blocks,
            "apps": sorted(({"app": k, **v} for k, v in apps.items()), key=lambda x: -x["minutes"])[:40],
            "can_delete_until": cfg["staff_delete_minutes"], "manage": auth.at_least(me, "time", "manage"), "mine": me["id"] == user}


@app.get("/api/time/week")
def api_time_week(request: Request, start: str = ""):
    """Everyone the viewer may see: hours and activity per day for 7 days from start (business clock)."""
    _time_db()
    me = request.state.user
    manage = auth.at_least(me, "time", "manage")
    try:
        s = date.fromisoformat(start) if start else datetime.now(CASH_TZ).date() - timedelta(days=6)
    except ValueError:
        raise HTTPException(400, "Pick a start day.")
    days = [s + timedelta(days=i) for i in range(7)]
    rows = []
    with db._conn() as con:
        users = [u for u in db.list_users() if manage or u["id"] == me["id"]]
        for u in users:
            per = [_time_totals(con, u["id"], d) for d in days]
            if not manage or any(p["minutes"] for p in per) or u["id"] == me["id"]:
                tm = sum(p["minutes"] for p in per)
                ta = sum(p["active"] for p in per)
                rows.append({"id": u["id"], "name": (u.get("name") or "").strip() or u["email"].split("@")[0], "days": per,
                             "minutes": tm, "activity": round(ta / tm, 3) if tm else None})
    return {"days": [d.isoformat() for d in days], "people": rows}


@app.get("/api/time/shot/{shot_id}")
def api_time_shot_file(shot_id: int, request: Request, thumb: int = 0):
    _time_db()
    with db._conn() as con:
        s = con.execute("SELECT * FROM time_shots WHERE id = ? AND deleted_at IS NULL", (shot_id,)).fetchone()
    if not s or not _time_can_see(request, s["user_id"]):
        raise HTTPException(404, "Screenshot not found.")
    folder = os.path.join(TIME_DIR, str(s["user_id"]), s["taken_at"][:10])
    path = os.path.join(folder, f"{shot_id}_t.jpg" if thumb else f"{shot_id}.jpg")
    if not os.path.exists(path):
        path = os.path.join(folder, f"{shot_id}.jpg")
    if not os.path.exists(path):
        raise HTTPException(404, "That screenshot was deleted (kept for a limited number of days).")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/time/shot/{shot_id}")
def api_time_shot_delete(shot_id: int, request: Request):
    """People may remove their own screenshot within a few minutes (something private on screen); managers any. Logged."""
    _time_db()
    me = request.state.user
    with db._conn() as con:
        s = con.execute("SELECT * FROM time_shots WHERE id = ? AND deleted_at IS NULL", (shot_id,)).fetchone()
        if not s:
            raise HTTPException(404, "Screenshot not found.")
        mins = (datetime.now(timezone.utc) - datetime.fromisoformat(s["taken_at"].replace("Z", "+00:00"))).total_seconds() / 60
        manage = auth.at_least(me, "time", "manage")
        if not manage and (s["user_id"] != me["id"] or mins > _time_cfg()["staff_delete_minutes"]):
            raise HTTPException(403, f"You can remove your own screenshot only within {_time_cfg()['staff_delete_minutes']} minutes.")
        con.execute("UPDATE time_shots SET deleted_at = ?, deleted_by = ? WHERE id = ?", (timetrack.now(), me.get("email", ""), shot_id))
    folder = os.path.join(TIME_DIR, str(s["user_id"]), s["taken_at"][:10])
    for n in (f"{shot_id}.jpg", f"{shot_id}_t.jpg"):
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            pass
    return {"ok": True}


@app.get("/api/time/devices")
def api_time_devices(request: Request):
    _time_db()
    if not auth.at_least(request.state.user, "time", "manage"):
        raise HTTPException(403, "Managers only.")
    names = {u["id"]: (u.get("name") or u["email"]) for u in db.list_users()}
    with db._conn() as con:
        rows = [dict(r) for r in con.execute("SELECT id, user_id, name, os, version, created_at, last_seen, revoked_at FROM time_devices ORDER BY last_seen DESC")]
    for r in rows:
        r["person"] = names.get(r["user_id"], "?")
    return {"devices": rows}


@app.post("/api/time/devices/{dev_id}/revoke")
def api_time_revoke(dev_id: int, request: Request):
    _time_db()
    if not auth.at_least(request.state.user, "time", "manage"):
        raise HTTPException(403, "Managers only.")
    with db._conn() as con:
        con.execute("UPDATE time_devices SET revoked_at = ? WHERE id = ?", (timetrack.now(), dev_id))
    return {"ok": True}


@app.put("/api/time/settings")
def api_time_settings(payload: dict, request: Request):
    if request.state.user.get("role", "owner") != "owner":
        raise HTTPException(403, "Only an owner.")
    cfg = _time_cfg()
    for k in ("keep_days", "idle_minutes", "staff_delete_minutes"):
        if k in payload:
            try:
                cfg[k] = max(1, min(int(payload[k]), 365))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number.")
    db.set_setting("time_settings", json.dumps(cfg))
    return {"ok": True, "settings": cfg}


async def _time_loop():
    """Once a day: screenshots older than keep_days are deleted (minutes and hours stay)."""
    await asyncio.sleep(600)
    while True:
        try:
            _time_db()
            cut = (datetime.now(timezone.utc) - timedelta(days=_time_cfg()["keep_days"])).isoformat(timespec="seconds")
            with db._conn() as con:
                old = [dict(r) for r in con.execute("SELECT id, user_id, taken_at FROM time_shots WHERE taken_at < ? AND deleted_at IS NULL", (cut,))]
                for s in old:
                    for n in (f"{s['id']}.jpg", f"{s['id']}_t.jpg"):
                        try:
                            os.remove(os.path.join(TIME_DIR, str(s["user_id"]), s["taken_at"][:10], n))
                        except OSError:
                            pass
                    con.execute("UPDATE time_shots SET deleted_at = ?, deleted_by = 'kept-days' WHERE id = ?", (timetrack.now(), s["id"]))
        except Exception as e:
            print(f"Time clean-up failed: {e}", flush=True)
        await asyncio.sleep(86400)


# ---------------------------------------------------------------- Tasks desk

TASK_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "task_files")
TASK_FILE_MAX = 500 * 1024 * 1024                  # one video can be big; images are far smaller
_tasks_ready = {"ok": False}


def _tasks_db():
    if _tasks_ready["ok"]:
        return
    with db._conn() as con:
        con.executescript(tasks.SCHEMA)
        if "queue_pos" not in {r[1] for r in con.execute("PRAGMA table_info(tasks)")}:
            con.execute("ALTER TABLE tasks ADD COLUMN queue_pos REAL")      # admins' order in each person's queue
    _tasks_ready["ok"] = True


def _task_user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _task_manager(user: dict) -> bool:
    return auth.at_least(user, "tasks", "manage")


def _task_people() -> list:
    """Everyone who can be given a task: owners and anyone with the Tasks desk."""
    out = []
    for u in db.list_users():
        lvl = auth.level(u, "tasks")
        if lvl:
            out.append({"id": u["id"], "name": (u.get("name") or "").strip() or u["email"].split("@")[0],
                        "email": u["email"], "level": lvl, "owner": u.get("role", "owner") == "owner",
                        "wa": bool(whatsapp.phone_of(u["email"]))})
    return out


def _task_row(con, task_id: int, user: dict) -> dict:
    row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found.")
    t = dict(row)
    if not _task_manager(user) and t["assignee_id"] != user.get("id"):
        raise HTTPException(403, "That task belongs to someone else.")
    return t


def _task_log(con, task_id: int, user: dict, action: str, text: str = ""):
    con.execute("INSERT INTO task_log (task_id, by, action, text) VALUES (?, ?, ?, ?)",
                (task_id, user.get("email", ""), action, (text or "")[:4000]))
    con.execute("UPDATE tasks SET updated_at = datetime('now') WHERE id = ?", (task_id,))


def _task_out(t: dict, people: dict, stores: dict, files=None, full=False) -> dict:
    out = {k: t[k] for k in ("id", "kind", "title", "store_id", "assignee_id", "status", "priority", "due",
                             "special_notes", "created_by", "created_at", "updated_at", "submitted_at",
                             "approved_by", "approved_at", "closed_at")}
    out["store"] = stores.get(t["store_id"], "")
    p = people.get(t["assignee_id"])
    out["assignee"] = p["name"] if p else ""
    out["status_name"] = tasks.STATUS_NAMES.get(t["status"], t["status"])
    out["brief"] = tasks.loads(t["brief"])
    out["result"] = tasks.loads(t["result"])
    out["launch"] = tasks.loads(t["launch"])
    if files is not None:
        out["files"] = files
    if not full:
        out["brief"] = {k: v for k, v in out["brief"].items() if k in ("collection", "pages", "todo")}
        out["has_result"] = bool(out.pop("result"))
        out["launched"] = bool(out.pop("launch").get("campaign_id"))
    return out


def _task_everyone() -> list:
    """Everyone in People, for "Give it to". Someone without the Tasks desk gets Work on it when given a task."""
    have = {p["id"]: p for p in _task_people()}
    return [have.get(u["id"]) or {"id": u["id"], "name": (u.get("name") or "").strip() or u["email"].split("@")[0],
                                  "email": u["email"], "level": None, "owner": False,
                                  "wa": bool(whatsapp.phone_of(u["email"]))} for u in db.list_users()]


def _task_give_access(user_id: int):
    """Given a task without the Tasks desk: add Work on Tasks to what they already have, so they can open it."""
    u = db.get_user(user_id)
    if not u or auth.level(u, "tasks"):
        return
    access = {**auth.access_of(u), "tasks": "work"}
    role, desks, acc = auth.clean_access("staff", access=access)
    db.set_user_access(user_id, role, desks, None, acc)


def _task_lookups():
    people = {p["id"]: p for p in _task_everyone()}
    stores = {s["id"]: s["name"].strip() for s in db.list_stores() if not demo.is_demo(s)}
    return people, stores


# ---- Tasks on WhatsApp: new task / follow up go to the staff member's WhatsApp; "doing" / "done" replies move
# the task, any other reply to a task message (or the first one after a Follow up) is logged and passed on.

_TASK_TPL = {"task": "ecomvital_task_new", "followup": "ecomvital_task_followup"}


def _task_first_name(u: dict) -> str:
    return ((u.get("name") or "").strip() or (u.get("email") or "").split("@")[0]).split(" ")[0]


def _task_due_words(due) -> str:
    try:
        d = date.fromisoformat(due)
        return f"{d.day} {d:%b}"
    except (TypeError, ValueError):
        return "no due date"


def _task_link(task_id) -> str:
    return f"{BASE_URL}/tasks?task={task_id}"


async def _task_wa_send(task_id: int, kind: str, by: dict) -> str:
    """WhatsApp the task's assignee: a new task ("task") or an admin asking for an update ("followup").
    Returns what happened in words ('' when WhatsApp isn't connected or nobody has the task). Logged in task_log."""
    _tasks_db()
    if not whatsapp.ready():
        return ""
    with db._conn() as con:
        t = dict(con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    staff = next((u for u in db.list_users() if u["id"] == t["assignee_id"]), None)
    if not staff:
        return ""
    phone = whatsapp.phone_of(staff["email"])
    who = _task_first_name(staff)
    if not phone:
        return f"{who} has no WhatsApp number (Settings → WhatsApp)"
    store = next((x["name"].strip() for x in db.list_stores() if x["id"] == t["store_id"]), "") or "-"
    due = _task_due_words(t["due"])
    admin = _task_first_name(by) if by.get("email") else "An admin"
    notes = (t["special_notes"] or "").strip()
    if kind == "task":
        text = (f"*New task for you:* {t['title']}\nStore: {store}\nDue: {due}"
                + (f"\n\n*Special notes:* {notes[:500]}" if notes else "")
                + f"\n\n{_task_link(task_id)}\n\nTap *Doing* when you start and *Done* when it's finished.")
        params = [who, t["title"], store, due, notes[:500] or "none"]
    else:
        text = (f"*{admin} is asking for an update* on your task: {t['title']} (due {due}).\n{_task_link(task_id)}\n\n"
                "Reply to this message with a short update, or tap *Doing* / *Done*.")
        params = [who, admin, t["title"], due]
    pay = [f"task:{task_id}:doing", f"task:{task_id}:done"]
    try:
        if _wa_window_open(phone):
            ids = await whatsapp.send_buttons(phone, text, [(pay[0], "Doing"), (pay[1], "Done")])
            how = "message"
        else:
            try:
                ids = await whatsapp.send_named(phone, _TASK_TPL[kind], params, pay, str(task_id))
                how = "template"
            except whatsapp.WAError as e:
                # the task template isn't approved yet: the general heads-up, the full text follows when they reply
                print(f"Task template {_TASK_TPL[kind]}: {e}", flush=True)
                with db._conn() as con:
                    con.execute("INSERT INTO assistant_inbox (user_email, title, body, source, created_at, sent_wa) VALUES (?,?,?,?,?,?)",
                                (staff["email"], f"Task: {t['title']}"[:200], text, "tasks",
                                 datetime.now(timezone.utc).isoformat(timespec="seconds"), "waiting"))
                ids = await whatsapp.send_template(phone, ("New task: " if kind == "task" else "Update asked: ") + t["title"])
                how = "heads-up"
    except whatsapp.WAError as e:
        msg = f"WhatsApp to {who} failed: {e}"
        with db._conn() as con:
            _task_log(con, task_id, by, "whatsapp", msg)
        return msg
    _wa_log(phone, "out", text)
    what = {"task": f"Sent the task to {who} on WhatsApp", "followup": f"Asked {who} for an update on WhatsApp"}[kind]
    if how == "heads-up":
        what += " (a short heads-up: the full message follows when they reply)"
    with db._conn() as con:
        for i in ids:
            if i:
                con.execute("INSERT OR IGNORE INTO task_wa (wa_id, task_id, phone, kind, asked_by) VALUES (?, ?, ?, ?, ?)",
                            (i, task_id, phone, kind, by.get("email", "")))
        _task_log(con, task_id, by, "followup" if kind == "followup" else "whatsapp", what)
    return what


def _task_pick(con, user: dict, verb: str, phone: str):
    """The task a bare "doing" / "done" means: their only open one, else the one we last messaged them about,
    else the latest. Returns (task, how many open)."""
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM tasks WHERE assignee_id = ? AND status NOT IN ({','.join('?' * len(tasks.HISTORY))})"
        " ORDER BY updated_at DESC, id DESC", (user.get("id"), *tasks.HISTORY))]
    if not rows:
        return None, 0
    want = ("todo", "changes") if verb == "doing" else ("doing", "changes", "todo")
    fit = [r for r in rows if r["status"] in want] or rows
    if len(fit) > 1:
        last = {r[0]: i for i, r in enumerate(con.execute(
            "SELECT task_id FROM task_wa WHERE phone = ? ORDER BY at DESC, rowid DESC", (phone,)))}
        fit.sort(key=lambda r: last.get(r["id"], 10 ** 6))
    return fit[0], len(rows)


def _task_wa_move(user: dict, phone: str, task_id, verb: str) -> str:
    """A "doing" / "done" reply: move the task like the buttons on the desk would. Returns the answer to send."""
    _tasks_db()
    with db._conn() as con:
        note = ""
        if task_id:
            row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            t = dict(row) if row else None
            if not t or (t["assignee_id"] != user.get("id") and not _task_manager(user)):
                return f"I can't find task #{task_id} among your tasks."
        else:
            t, n_open = _task_pick(con, user, verb, phone)
            if not t:
                return "You have no open tasks right now. 👍"
            if n_open > 1:
                note = f"\n\n(You have {n_open} open tasks; I took this one. For another, reply e.g. “{verb} #12”.)"
        name, link = f"#{t['id']} {t['title']}", _task_link(t["id"])
        if t["status"] in tasks.HISTORY:
            return f"Task {name} is already {tasks.STATUS_NAMES[t['status']].lower()}."
        if t["status"] in ("submitted", "approved"):
            return f"Task {name} is already with the admins ({tasks.STATUS_NAMES[t['status']]}). To change it, open it: {link}"
        if verb == "doing":
            if t["status"] == "doing":
                return f"Task {name} is already in progress. 👍 Reply *Done* when it's finished." + note
            con.execute("UPDATE tasks SET status = 'doing', closed_at = NULL WHERE id = ?", (t["id"],))
            _task_log(con, t["id"], user, "status", "Started (WhatsApp)")
            return f"✅ Task {name} is now *In progress*. Reply *Done* when it's finished.\n{link}" + note
        result = tasks.loads(t["result"])
        n_cr = con.execute("SELECT COUNT(*) FROM task_files WHERE task_id = ? AND side = 'staff' AND kind = 'creative'",
                           (t["id"],)).fetchone()[0]
        lacking = tasks.missing(t["kind"], tasks.loads(t["brief"]), result, n_cr)
        if lacking:
            if t["status"] == "todo":
                con.execute("UPDATE tasks SET status = 'doing' WHERE id = ?", (t["id"],))
                _task_log(con, t["id"], user, "status", "Started (WhatsApp)")
            _task_log(con, t["id"], user, "whatsapp", "Replied Done, but the hand-back links are missing (asked for them)")
            return (f"Almost there! Task {name} still needs: {', '.join(lacking)}. If you can't make one, add a note for the "
                    f"admin under it. Add them here, then press Submit or reply *Done* again:\n{link}" + note)
        con.execute("UPDATE tasks SET status = 'submitted', submitted_at = datetime('now'), closed_at = NULL WHERE id = ?", (t["id"],))
        _task_log(con, t["id"], user, "status", "Submitted for review (WhatsApp)")
        creatives = con.execute("SELECT COUNT(*) FROM task_files WHERE task_id = ? AND side = 'staff' AND kind = 'creative'",
                                (t["id"],)).fetchone()[0]
    if t.get("created_by"):
        _assist_deliver(t["created_by"], f"Task ready for review: {t['title']}"[:200],
                        f"{_task_first_name(user)} marked task #{t['id']} done on WhatsApp. Check it: {link}", "tasks")
    tip = "" if t["kind"] != "listing" or creatives else "\n(No ad creatives uploaded yet: add them if the ads need them.)"
    return f"✅ Task {name} is *Submitted for review*. Thanks! The admin has been told.{tip}" + note


def _task_wa_update(user: dict, ref: dict, text: str) -> str:
    """Free text in reply to a task message / Follow up: logged on the task and passed to the admin who asked."""
    _tasks_db()
    with db._conn() as con:
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (ref["task_id"],)).fetchone()
        if not row:
            return "That task doesn't exist any more."
        t = dict(row)
        _task_log(con, t["id"], user, "comment", f"(WhatsApp) {text}")
        con.execute("UPDATE task_wa SET answered_at = datetime('now') WHERE task_id = ? AND phone = ? AND answered_at IS NULL",
                    (t["id"], ref["phone"]))
    to = ref.get("asked_by") or t.get("created_by")
    admin = _assist_user_row(to) if to else None
    if to:
        _assist_deliver(to, f"Update on task: {t['title']}"[:200],
                        f"{_task_first_name(user)} replied on WhatsApp about task #{t['id']}:\n“{text[:1500]}”\n{_task_link(t['id'])}", "tasks")
    return (f"Thanks, I've passed that to {_task_first_name(admin)} and noted it on task #{t['id']}." if admin
            else f"Thanks, noted on task #{t['id']}.")


def _task_wa_reply(phone: str, user: dict, msg: dict, text: str):
    """A staff WhatsApp message about a task, or None when it isn't one (then the assistant answers it)."""
    _tasks_db()
    t = msg.get("type")
    payload = ""
    if t == "button":
        payload = (msg.get("button") or {}).get("payload") or ""
    elif t == "interactive":
        payload = ((msg.get("interactive") or {}).get("button_reply") or {}).get("id") or ""
    m = re.fullmatch(r"task:(\d+):(doing|done)", payload)
    if m:
        return _task_wa_move(user, phone, int(m.group(1)), m.group(2))
    ctx = (msg.get("context") or {}).get("id")
    with db._conn() as con:
        ref = con.execute("SELECT * FROM task_wa WHERE wa_id = ?", (ctx,)).fetchone() if ctx else None
        if not ref and not ctx:     # the first message after a Follow up counts as the answer, even without a swipe-reply
            ref = con.execute("SELECT * FROM task_wa WHERE phone = ? AND kind = 'followup' AND answered_at IS NULL"
                              " AND at >= datetime('now', '-1 day') ORDER BY at DESC LIMIT 1", (phone,)).fetchone()
    ref = dict(ref) if ref else None
    verb, num = tasks.wa_verb(text)
    if verb:
        return _task_wa_move(user, phone, num or (ref["task_id"] if ref else None), verb)
    if ref and text:
        return _task_wa_update(user, ref, text)
    return None


@app.post("/api/tasks/{task_id}/followup")
async def api_task_followup(request: Request, task_id: int):
    """Admin: WhatsApp the staff member asking for an update. Their reply is logged here and sent to whoever asked."""
    _tasks_db()
    _need_level(request, "tasks", "manage")
    user = _task_user(request)
    with db._conn() as con:
        t = _task_row(con, task_id, user)
    if t["status"] in tasks.HISTORY:
        raise HTTPException(400, "This task is finished.")
    if not t["assignee_id"]:
        raise HTTPException(400, "Give the task to someone first.")
    if not whatsapp.ready():
        raise HTTPException(400, "WhatsApp isn't connected (Settings → WhatsApp).")
    got = await _task_wa_send(task_id, "followup", user)
    if not got.startswith("Asked"):
        raise HTTPException(400, got or "Couldn't send it.")
    return {"ok": True, "text": got}


@app.get("/api/whatsapp/task-templates")
async def api_task_templates():
    """Owner: are the task message templates approved by Meta?"""
    if not whatsapp.ready() or not whatsapp.cfg()["waba_id"]:
        raise HTTPException(400, "WhatsApp isn't connected.")
    return {"templates": await whatsapp.template_status(list(_TASK_TPL.values()))}


@app.post("/api/whatsapp/task-templates")
async def api_task_templates_submit():
    """Owner: send the task message templates to Meta for approval (only the ones not there yet)."""
    if not whatsapp.ready() or not whatsapp.cfg()["waba_id"]:
        raise HTTPException(400, "WhatsApp isn't connected.")
    return {"templates": await whatsapp.ensure_task_templates(BASE_URL)}


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page():
    return _page("tasks.html", ("tasks.js", "tasks.css", "nav.js", "styles.css", "skin.css", "chat.js"))


@app.get("/api/tasks")
def api_tasks(request: Request, view: str = "board", assignee: int = 0, store: int = 0, q: str = ""):
    """The board (open tasks) or History (launched / done / cancelled). Staff see only their own."""
    _tasks_db()
    user = _task_user(request)
    manage = _task_manager(user)
    people, stores = _task_lookups()
    where, args = [], []
    marks = ",".join("?" * len(tasks.HISTORY))
    where.append(f"status {'IN' if view == 'history' else 'NOT IN'} ({marks})")
    args += list(tasks.HISTORY)
    if not manage:
        where.append("assignee_id = ?")
        args.append(user.get("id"))
    elif assignee:
        where.append("assignee_id = ?")
        args.append(assignee)
    if store:
        where.append("store_id = ?")
        args.append(store)
    if q.strip():
        where.append("(title LIKE ? OR brief LIKE ? OR result LIKE ?)")
        args += [f"%{q.strip()}%"] * 3
    order = "closed_at DESC" if view == "history" else \
        "COALESCE(queue_pos, 1e9), CASE priority WHEN 'high' THEN 0 ELSE 1 END, COALESCE(due, '9999'), id"
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY {order}"
                                             f"{' LIMIT 300' if view == 'history' else ''}", args)]
        counts = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT task_id, SUM(side = 'staff'), COUNT(*) FROM task_files GROUP BY task_id")}
    out = []
    for t in rows:
        x = _task_out(t, people, stores)
        x["files_staff"], x["files_all"] = counts.get(t["id"], (0, 0))
        out.append(x)
    return {"todo": tasks.TODO, "launch_ready": tasks.LAUNCH_READY, "tasks": out, "manage": manage, "me": {"id": user.get("id"), "email": user.get("email")},
            "people": list(people.values()) if manage else [],
            "stores": [{"id": k, "name": v} for k, v in sorted(stores.items(), key=lambda kv: kv[1].lower())],
            "statuses": tasks.STATUS_NAMES, "ctas": tasks.CTAS, "wa_ready": whatsapp.ready(),
            "today": datetime.now(CASH_TZ).date().isoformat()}


@app.post("/api/tasks/queue")
async def api_tasks_queue(request: Request, payload: dict):
    """Queue (admins): one person's open tasks in the order to do them; tasks dropped in from someone else are
    given to this person (logged, and sent on WhatsApp like any new task). Optional priority per task."""
    _tasks_db()
    _need_level(request, "tasks", "manage")
    user = _task_user(request)
    person = int(payload.get("person") or 0) or None
    ids = [int(x) for x in payload.get("order") or []]
    pri = {int(k): v for k, v in (payload.get("priority") or {}).items() if v in ("normal", "high")}
    names = {x["id"]: x["name"] for x in _task_people()}
    if person and person not in names:
        raise HTTPException(400, "That person can't be given tasks (no Tasks desk access).")
    given = []
    with db._conn() as con:
        for pos, tid in enumerate(ids, 1):
            old = _task_row(con, tid, user)
            con.execute("UPDATE tasks SET queue_pos = ?, assignee_id = ?, updated_at = datetime('now') WHERE id = ?",
                        (pos, person, tid))
            if old["assignee_id"] != person:
                _task_log(con, tid, user, "edit", f"Queue: given to {names.get(person, 'nobody')}")
                if person and old["status"] not in tasks.HISTORY:
                    given.append(tid)
        for tid, p in pri.items():
            old = _task_row(con, tid, user)
            if old["priority"] != p:
                con.execute("UPDATE tasks SET priority = ? WHERE id = ?", (p, tid))
                _task_log(con, tid, user, "edit", f"Priority: {'High' if p == 'high' else 'Normal'}")
    for tid in given:
        try:
            await _task_wa_send(tid, "task", user)
        except Exception as e:
            print(f"Task WhatsApp failed: {e}", flush=True)
    return {"ok": True, "given": len(given)}


@app.get("/api/tasks/day")
def api_tasks_day(request: Request, day: str = "", person: int = 0):
    """End of day: what each person did on a Hong Kong day (from the task log), plus what's still open."""
    _tasks_db()
    _need_level(request, "tasks", "manage")
    try:
        d = date.fromisoformat(day) if day else datetime.now(CASH_TZ).date()
    except ValueError:
        raise HTTPException(400, "Pick a day.")
    utc = lambda x: datetime.combine(x, clock.min, CASH_TZ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    a, b = utc(d), utc(d + timedelta(days=1))
    people, stores = _task_lookups()
    by_email = {p["email"]: p for p in people.values()}
    with db._conn() as con:
        logs = [dict(r) for r in con.execute(
            "SELECT l.*, t.title, t.kind, t.store_id, t.status, t.assignee_id FROM task_log l JOIN tasks t ON t.id = l.task_id"
            " WHERE l.at >= ? AND l.at < ? ORDER BY l.at", (a, b))]
        open_rows = [dict(r) for r in con.execute(
            f"SELECT * FROM tasks WHERE status NOT IN ({','.join('?' * len(tasks.HISTORY))})", tasks.HISTORY)]
        files = [dict(r) for r in con.execute(
            "SELECT f.id, f.task_id, f.name, f.mime, f.by FROM task_files f WHERE f.at >= ? AND f.at < ?", (a, b))]
    report = {}
    for p in people.values():
        if (person and p["id"] != person) or not (p["level"] or p["id"] == person):
            continue
        report[p["email"]] = {"person": p, "events": [], "files": [], "open": [], "done": 0, "submitted": 0}
    for l in logs:
        r = report.get(l["by"])
        if r is None:
            continue
        r["events"].append({"at": l["at"], "task_id": l["task_id"], "title": l["title"], "store": stores.get(l["store_id"], ""),
                            "action": l["action"], "text": l["text"]})
        if l["action"] == "status" and (l["text"] or "").startswith("Submitted"):
            r["submitted"] += 1
    for f in files:
        if f["by"] in report:
            report[f["by"]]["files"].append(f)
    for t in open_rows:
        p = people.get(t["assignee_id"])
        if p and p["email"] in report:
            report[p["email"]]["open"].append({"id": t["id"], "title": t["title"], "status": t["status"],
                                               "status_name": tasks.STATUS_NAMES.get(t["status"]), "due": t["due"],
                                               "store": stores.get(t["store_id"], "")})
    out = sorted(report.values(), key=lambda r: (-len(r["events"]), r["person"]["name"].lower()))
    return {"day": d.isoformat(), "people": out, "known": list(by_email)}


def _launch_tasks(con, ids: list) -> list:
    """The picked tasks with what goes in the ads: landing page, copy, image / video creatives."""
    out = []
    for tid in ids:
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, f"Task #{tid} no longer exists.")
        t = dict(row)
        t["result_d"] = tasks.loads(t["result"])
        t["launch_d"] = tasks.loads(t["launch"])
        t["creatives"] = [dict(f) for f in con.execute(
            "SELECT * FROM task_files WHERE task_id = ? AND side = 'staff' AND kind = 'creative' ORDER BY id", (tid,))
            if f["name"].lower().endswith(_IMG_EXT + _VID_EXT)]
        out.append(t)
    return out


def _launch_problems(t: dict, resuming: bool) -> list:
    r = t["result_d"]
    probs = []
    if t["kind"] != "listing":
        probs.append("is a custom task")
    if t["status"] != "approved":
        probs.append(f"is “{tasks.STATUS_NAMES.get(t['status'], t['status'])}”, not approved")
    if not str(r.get("landing_url") or "").startswith("http"):
        probs.append("has no main landing page link")
    if not t["creatives"]:
        probs.append("has no image or video creatives")
    if not (r.get("primary_text") and r.get("headline")):
        probs.append("has no ad copy (primary text + headline)")
    if t["launch_d"].get("batch") and not t["launch_d"].get("campaign_id") and not resuming:
        probs.append(f"is already in launch #{t['launch_d']['batch']}, which hasn't finished (carry that one on)")
    return probs


def _launch_pending(con, ids: list):
    """An unfinished launch of exactly these tasks, to carry on instead of starting again."""
    for b in con.execute("SELECT * FROM task_launches WHERE status != 'done' ORDER BY id DESC"):
        if sorted(json.loads(b["task_ids"])) == sorted(ids):
            return dict(b)
    return None


def _launch_ids(raw) -> list:
    ids = [int(x) for x in re.findall(r"\d+", str(raw if not isinstance(raw, list) else ",".join(map(str, raw))))]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise HTTPException(400, "Pick at least one task to launch.")
    return ids


@app.get("/api/tasks/launch/options")
async def api_tasks_launch_options(request: Request, ids: str = ""):
    """The Launch form for one or several approved tasks: strategies, the ad account's Pages and pixels,
    and what each task brings (landing page, creatives, copy) or what stops it."""
    if not tasks.LAUNCH_READY:
        raise HTTPException(400, "Launching from Tasks is coming soon: the launch strategies are being set up.")
    _tasks_db()
    _need_level(request, "tasks", "manage")
    ids = _launch_ids(ids)
    with db._conn() as con:
        picked = _launch_tasks(con, ids)
        pending = _launch_pending(con, ids)
    stores = {t["store_id"] for t in picked}
    if len(stores) != 1 or None in stores:
        raise HTTPException(400, "Pick tasks from one store (one Meta ad account). "
                                 + ("Some have no store: edit their brief." if None in stores else ""))
    st, conn = _task_meta(stores.pop())
    opts = await meta.launch_options(conn["access_token"], st["meta_account_id"])
    saved = tasks.loads(db.get_setting(f"task_launch:{st['id']}") or "")
    first = tasks.loads(picked[0]["brief"])
    title = picked[0]["title"] if len(picked) == 1 else f"{len(picked)} landing pages"
    settings = tasks.loads(pending["settings"]) if pending else {}
    return {"account_id": st["meta_account_id"], "account": st.get("meta_account_name") or "", "store": st["name"].strip(),
            "currency": st.get("meta_currency") or "", **opts,
            "strategies": [{"key": k, **v} for k, v in tasks.STRATEGIES.items()],
            "tasks": [{"id": t["id"], "title": t["title"], "landing_url": t["result_d"].get("landing_url") or "",
                       "headline": (tasks.lines(t["result_d"].get("headline")) or [""])[0],
                       "creatives": [{"id": f["id"], "name": f["name"], "video": f["name"].lower().endswith(_VID_EXT)}
                                     for f in t["creatives"]],
                       "problems": _launch_problems(t, bool(pending))} for t in picked],
            "pending": {"id": pending["id"], "strategy": pending["strategy"], "status": pending["status"],
                        "error": pending["error"], "settings": settings} if pending else None,
            "defaults": {"strategy": settings.get("strategy") or saved.get("strategy") or "y",
                         "page_id": settings.get("page_id") or saved.get("page_id")
                         or (opts["pages"][0]["id"] if len(opts["pages"]) == 1 else ""),
                         "pixel_id": settings.get("pixel_id") or saved.get("pixel_id") or _px_cfg(st["id"]).get("meta_pixel") or "",
                         "daily_budget": settings.get("daily_budget") or re.sub(r"[^\d.]", "", str(first.get("budget") or ""))
                         or saved.get("daily_budget") or "",
                         "countries": settings.get("countries") or first.get("countries") or saved.get("countries") or "",
                         "campaign_name": settings.get("campaign_name") or f"{st['name'].strip()} · {title}"}}


@app.post("/api/tasks/launch")
async def api_tasks_launch(request: Request, payload: dict):
    """Put the picked approved tasks into one Meta campaign with a strategy, everything PAUSED. Each Meta id is
    saved as it's made, so pressing again after a problem (or while Meta processes videos) carries on, never doubles."""
    if not tasks.LAUNCH_READY:
        raise HTTPException(400, "Launching from Tasks is coming soon: the launch strategies are being set up.")
    _tasks_db()
    _need_level(request, "tasks", "manage")
    user = _task_user(request)
    ids = _launch_ids(payload.get("ids"))
    with db._conn() as con:
        picked = _launch_tasks(con, ids)
        batch = _launch_pending(con, ids)
    by_id = {t["id"]: t for t in picked}
    picked = [by_id[i] for i in ids]
    probs = [f"#{t['id']} {t['title']}: {', '.join(p)}" for t in picked if (p := _launch_problems(t, bool(batch)))]
    if probs:
        raise HTTPException(400, "Can't launch yet. " + " · ".join(probs))
    stores = {t["store_id"] for t in picked}
    if len(stores) != 1:
        raise HTTPException(400, "Pick tasks from one store (one Meta ad account).")
    st, conn = _task_meta(stores.pop())
    token, act = conn["access_token"], st["meta_account_id"]

    strategy = payload.get("strategy") if payload.get("strategy") in tasks.STRATEGIES else ""
    if batch:                                    # carrying on: the strategy can't change halfway
        strategy = batch["strategy"]
    if not strategy:
        raise HTTPException(400, "Pick a strategy.")
    strat = tasks.STRATEGIES[strategy]
    page_id = re.sub(r"\D", "", str(payload.get("page_id") or ""))
    pixel_id = re.sub(r"\D", "", str(payload.get("pixel_id") or ""))
    countries = [c for c in re.split(r"[\s,]+", str(payload.get("countries") or "").upper()) if re.fullmatch(r"[A-Z]{2}", c)]
    try:
        budget = float(payload.get("daily_budget") or 0)
    except ValueError:
        budget = 0
    name = (str(payload.get("campaign_name") or "").strip() or picked[0]["title"])[:200]
    missing = [n for n, v in (("Facebook Page", page_id), ("pixel", pixel_id), ("countries", countries),
                              ("daily budget", budget > 0)) if not v]
    if missing:
        raise HTTPException(400, "Fill in: " + ", ".join(missing) + ".")
    settings = {"strategy": strategy, "page_id": page_id, "pixel_id": pixel_id, "daily_budget": str(payload.get("daily_budget")),
                "countries": " ".join(countries), "campaign_name": name}
    db.set_setting(f"task_launch:{st['id']}", json.dumps(settings))
    with db._conn() as con:
        if not batch:
            bid = con.execute("INSERT INTO task_launches (store_id, strategy, task_ids, settings, progress, created_by)"
                              " VALUES (?, ?, ?, ?, '{}', ?)", (st["id"], strategy, json.dumps(ids), json.dumps(settings),
                                                                 user.get("email", ""))).lastrowid
            batch = dict(con.execute("SELECT * FROM task_launches WHERE id = ?", (bid,)).fetchone())
        else:
            con.execute("UPDATE task_launches SET settings = ? WHERE id = ?", (json.dumps(settings), batch["id"]))
        for t in picked:
            con.execute("UPDATE tasks SET launch = ? WHERE id = ?", (json.dumps({"batch": batch["id"]}), t["id"]))
    bid = batch["id"]
    prog = tasks.loads(batch["progress"])
    prog.setdefault("adsets", {})
    prog.setdefault("ads", {})
    prog.setdefault("videos", {})
    prog["account_id"] = act

    def save(status="running", error=None):
        with db._conn() as con:
            con.execute("UPDATE task_launches SET progress = ?, status = ?, error = ? WHERE id = ?",
                        (json.dumps(prog), status, error, bid))

    minor = meta.minor_units(budget, st.get("meta_currency"))
    waiting = []
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            if not prog.get("campaign_id"):
                prog["campaign_id"] = await meta.create_campaign(
                    client, token, act, name, daily_minor=minor if strat["budget"] == "campaign" else None)
                save()
            for t in picked:
                key = "all" if strat["split"] == "one" else str(t["id"])
                if key not in prog["adsets"]:
                    label = name if key == "all" else f"{name} · {t['title']}"
                    prog["adsets"][key] = await meta.create_adset(
                        client, token, act, prog["campaign_id"], label[:200],
                        minor if strat["budget"] == "adset" else None, pixel_id, countries)
                    save()
                r = t["result_d"]
                for f in t["creatives"]:
                    fk = str(f["id"])
                    if fk in prog["ads"]:
                        continue
                    path, low = _task_file_path(f), f["name"].lower()
                    ad_name = f"{t['title']} · {f['name']}"[:250]
                    copy = dict(page_id=page_id, link=r["landing_url"], message=r["primary_text"],
                                headline=(tasks.lines(r.get("headline")) or [""])[0],
                                description=r.get("ad_description"), cta=r.get("cta") or "SHOP_NOW")
                    if low.endswith(_VID_EXT):
                        if fk not in prog["videos"]:
                            prog["videos"][fk] = await meta.upload_video(client, token, act, path, f["name"])
                            save()
                        thumb = await meta.video_thumbnail(client, token, prog["videos"][fk])
                        if not thumb:
                            waiting.append(f["name"])
                            continue
                        made = await meta.create_ad(client, token, act, prog["adsets"][key], ad_name, **copy,
                                                    video_id=prog["videos"][fk], thumb_url=thumb)
                    else:
                        send = path
                        if not low.endswith((".jpg", ".jpeg", ".png", ".gif")):       # HEIC / WebP → JPEG for Meta
                            send = path + "_ad.jpg"
                            if not os.path.exists(send) and not await asyncio.to_thread(_att_image, path, send, 2400):
                                raise meta.MetaAdsError(f"{f['name']} isn't an image Meta can use.")
                        with open(send, "rb") as fh:
                            data = fh.read()
                        image_hash = await meta.upload_image(client, token, act, data, os.path.basename(send))
                        made = await meta.create_ad(client, token, act, prog["adsets"][key], ad_name, **copy,
                                                    image_hash=image_hash)
                    prog["ads"][fk] = {**made, "task_id": t["id"]}
                    save()
    except meta.MetaAdsError as e:
        save("failed", str(e))
        with db._conn() as con:
            for t in picked:
                _task_log(con, t["id"], user, "launch", f"Launch #{bid} stopped: {e}")
        return JSONResponse({"error": f"{e} What was already made is kept; press Launch again after fixing it.",
                             "batch_id": bid}, status_code=400)
    with db._conn() as con:
        if waiting:
            save("waiting")
            for t in picked:
                _task_log(con, t["id"], user, "launch", f"Launch #{bid}: campaign made (paused); Meta is still processing "
                                                        f"{len(waiting)} video{'s' if len(waiting) > 1 else ''}")
        else:
            prog["done_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            con.execute("UPDATE task_launches SET progress = ?, status = 'done', error = NULL, done_at = datetime('now')"
                        " WHERE id = ?", (json.dumps(prog), bid))
            for t in picked:
                mine = sum(1 for a in prog["ads"].values() if a.get("task_id") == t["id"])
                info = {"batch": bid, "campaign_id": prog["campaign_id"], "account_id": act, "strategy": strategy,
                        "strategy_name": strat["name"], "ads": mine, "with": [i for i in ids if i != t["id"]]}
                con.execute("UPDATE tasks SET launch = ?, status = 'launched', closed_at = datetime('now') WHERE id = ?",
                            (json.dumps(info), t["id"]))
                _task_log(con, t["id"], user, "launch",
                          f"Launched on Meta (paused) with {strat['name'].split(' · ')[0]}: {mine} ad{'s' if mine != 1 else ''}"
                          + (f", together with {len(ids) - 1} other task{'s' if len(ids) > 2 else ''}" if len(ids) > 1 else "")
                          + f", campaign {prog['campaign_id']}")
    return {"ok": True, "batch_id": bid, "waiting": waiting, "campaign_id": prog["campaign_id"], "ads": len(prog["ads"]),
            "account_id": act}


@app.get("/api/tasks/{task_id}")
def api_task(request: Request, task_id: int):
    _tasks_db()
    user = _task_user(request)
    people, stores = _task_lookups()
    with db._conn() as con:
        t = _task_row(con, task_id, user)
        files = [dict(r) for r in con.execute("SELECT * FROM task_files WHERE task_id = ? ORDER BY id", (task_id,))]
        log = [dict(r) for r in con.execute("SELECT * FROM task_log WHERE task_id = ? ORDER BY id", (task_id,))]
    names = {p["email"]: p["name"] for p in people.values()}
    for l in log:
        l["who"] = names.get(l["by"], (l["by"] or "").split("@")[0])
    for f in files:
        f["who"] = names.get(f["by"], (f["by"] or "").split("@")[0])
    out = _task_out(t, people, stores, files, full=True)
    out["log"] = log
    out["todo"] = tasks.TODO
    out["launch_ready"] = tasks.LAUNCH_READY
    out["manage"] = _task_manager(user)
    out["mine"] = t["assignee_id"] == user.get("id")
    p = people.get(t["assignee_id"])
    out["wa"] = {"ready": whatsapp.ready(), "number": bool(p and p.get("wa"))}
    return out


def _task_fields(payload: dict) -> dict:
    kind = payload.get("kind") if payload.get("kind") in tasks.KINDS else "listing"
    title = str(payload.get("title") or "").strip()[:200]
    brief = tasks.clean_brief(payload.get("brief") or {})
    if not title:
        title = (f"List {brief['collection']}" if brief.get("collection") else "")
    if not title:
        raise HTTPException(400, "Give the task a title.")
    due = str(payload.get("due") or "").strip() or None
    if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        raise HTTPException(400, "Pick a due date.")
    people = {p["id"] for p in _task_everyone()}
    assignee = int(payload.get("assignee_id") or 0) or None
    if assignee and assignee not in people:
        raise HTTPException(400, "That person is no longer in People.")
    if assignee:
        _task_give_access(assignee)
    return {"kind": kind, "title": title, "store_id": int(payload.get("store_id") or 0) or None,
            "assignee_id": assignee, "priority": "high" if payload.get("priority") == "high" else "normal",
            "due": due, "special_notes": str(payload.get("special_notes") or "").strip()[:8000],
            "brief": json.dumps(brief)}


@app.post("/api/tasks")
async def api_task_create(request: Request, payload: dict):
    _tasks_db()
    _need_level(request, "tasks", "manage")
    user = _task_user(request)
    f = _task_fields(payload)
    with db._conn() as con:
        tid = con.execute("INSERT INTO tasks (kind, title, store_id, assignee_id, priority, due, special_notes, brief,"
                          " created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (f["kind"], f["title"], f["store_id"], f["assignee_id"], f["priority"], f["due"],
                           f["special_notes"], f["brief"], user.get("email", ""))).lastrowid
        _task_log(con, tid, user, "created", "Created the task")
    wa = await _task_wa_send(tid, "task", user) if f["assignee_id"] and payload.get("whatsapp", True) else ""
    return {"ok": True, "id": tid, "whatsapp": wa}


@app.put("/api/tasks/{task_id}")
async def api_task_edit(request: Request, task_id: int, payload: dict):
    _tasks_db()
    _need_level(request, "tasks", "manage")
    user = _task_user(request)
    f = _task_fields(payload)
    with db._conn() as con:
        old = _task_row(con, task_id, user)
        con.execute("UPDATE tasks SET kind = ?, title = ?, store_id = ?, assignee_id = ?, priority = ?, due = ?,"
                    " special_notes = ?, brief = ? WHERE id = ?",
                    (f["kind"], f["title"], f["store_id"], f["assignee_id"], f["priority"], f["due"],
                     f["special_notes"], f["brief"], task_id))
        what = "Brief edited"
        if old["assignee_id"] != f["assignee_id"]:
            p = {x["id"]: x["name"] for x in _task_people()}.get(f["assignee_id"], "nobody")
            what += f", given to {p}"
        _task_log(con, task_id, user, "edit", what)
    given = f["assignee_id"] and old["assignee_id"] != f["assignee_id"] and old["status"] not in tasks.HISTORY
    wa = await _task_wa_send(task_id, "task", user) if given and payload.get("whatsapp", True) else ""
    return {"ok": True, "whatsapp": wa}


@app.delete("/api/tasks/{task_id}")
def api_task_delete(request: Request, task_id: int):
    _tasks_db()
    _need_level(request, "tasks", "manage")
    with db._conn() as con:
        _task_row(con, task_id, _task_user(request))
        for table in ("task_files", "task_log"):
            con.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
        con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    import shutil
    shutil.rmtree(os.path.join(TASK_DIR, str(task_id)), ignore_errors=True)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/result")
def api_task_result(request: Request, task_id: int, payload: dict):
    """Staff's hand-back: links, ad copy, notes. submit=true also sends it for review."""
    _tasks_db()
    user = _task_user(request)
    result = tasks.clean_result(payload.get("result") or {})
    with db._conn() as con:
        t = _task_row(con, task_id, user)
        if t["status"] in tasks.HISTORY or (t["status"] in ("submitted", "approved") and not _task_manager(user)):
            raise HTTPException(400, "This task is with the admins now. Press “Take back to edit” first (or ask an "
                                     "admin, once it's approved).")
        con.execute("UPDATE tasks SET result = ? WHERE id = ?", (json.dumps(result), task_id))
        if payload.get("submit"):
            n_cr = con.execute("SELECT COUNT(*) FROM task_files WHERE task_id = ? AND side = 'staff' AND kind = 'creative'",
                               (task_id,)).fetchone()[0]
            lacking = tasks.missing(t["kind"], tasks.loads(t["brief"]), result, n_cr)
            if lacking:
                raise HTTPException(400, "Still needed: " + ", ".join(lacking) + ". If you can't make one, press "
                                         "“Can't do this” under it and tell the admin why.")
            con.execute("UPDATE tasks SET status = 'submitted', submitted_at = datetime('now') WHERE id = ?", (task_id,))
            _task_log(con, task_id, user, "status", "Submitted for review")
        else:
            if t["status"] == "todo":
                con.execute("UPDATE tasks SET status = 'doing' WHERE id = ?", (task_id,))
                _task_log(con, task_id, user, "status", "Started")
            _task_log(con, task_id, user, "edit", "Saved their work")
    return {"ok": True}


# who may move a task where: staff (the assignee) / admins (Manage)
_TASK_STAFF_MOVES = {("todo", "doing"), ("changes", "doing"), ("submitted", "doing")}
_TASK_MOVE_TEXT = {"doing": "Started", "changes": "Sent back for changes", "approved": "Approved",
                   "launched": "Marked as launched", "done": "Marked done", "cancelled": "Cancelled",
                   "todo": "Moved back to To do", "submitted": "Submitted for review"}


@app.post("/api/tasks/{task_id}/status")
def api_task_status(request: Request, task_id: int, payload: dict):
    _tasks_db()
    user = _task_user(request)
    to = payload.get("status")
    comment = str(payload.get("comment") or "").strip()
    if to not in tasks.STATUSES:
        raise HTTPException(400, "Unknown step.")
    with db._conn() as con:
        t = _task_row(con, task_id, user)
        if not _task_manager(user) and (t["status"], to) not in _TASK_STAFF_MOVES:
            raise HTTPException(403, "Only an admin can do that.")
        if to == "changes" and not comment:
            raise HTTPException(400, "Say what needs changing.")
        sets = ["status = ?"]
        if to == "approved":
            sets += ["approved_by = ?", "approved_at = datetime('now')"]
        if to in tasks.HISTORY:
            sets.append("closed_at = datetime('now')")
        else:
            sets.append("closed_at = NULL")
        args = [to] + ([user.get("email", "")] if to == "approved" else []) + [task_id]
        con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args)
        _task_log(con, task_id, user, "status", _TASK_MOVE_TEXT.get(to, to) + (f": {comment}" if comment else ""))
    return {"ok": True}


@app.post("/api/tasks/{task_id}/comment")
def api_task_comment(request: Request, task_id: int, payload: dict):
    _tasks_db()
    user = _task_user(request)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write something first.")
    with db._conn() as con:
        _task_row(con, task_id, user)
        _task_log(con, task_id, user, "comment", text)
    return {"ok": True}


_IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif")
_VID_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi")


def _task_file_path(f: dict) -> str:
    return os.path.join(TASK_DIR, str(f["task_id"]), f"{f['id']}-{tasks.safe_name(f['name'])}")


@app.post("/api/tasks/{task_id}/files")
async def api_task_files(request: Request, task_id: int):
    """Creatives (images / videos that go in the ads) or any other file, kept on the volume."""
    _tasks_db()
    user = _task_user(request)
    form = await request.form()
    side = "admin" if form.get("side") == "admin" else "staff"
    kind = form.get("kind") if form.get("kind") in ("file", "instruction") else "creative"
    if kind == "instruction":
        side = "admin"
    with db._conn() as con:
        t = _task_row(con, task_id, user)
    if side == "admin" and not _task_manager(user):
        raise HTTPException(403, "Only an admin can add files to the brief.")
    if t["status"] in tasks.HISTORY:
        raise HTTPException(400, "This task is closed.")
    if t["status"] in ("submitted", "approved") and not _task_manager(user):
        raise HTTPException(400, "This task is with the admins now. Press “Take back to edit” first.")
    os.makedirs(os.path.join(TASK_DIR, str(task_id)), exist_ok=True)
    added = []
    for up in [f for f in form.getlist("files") if hasattr(f, "read")][:30]:
        name = (up.filename or "file")[-120:]
        with db._conn() as con:
            fid = con.execute("INSERT INTO task_files (task_id, side, kind, name, mime, by) VALUES (?, ?, ?, ?, ?, ?)",
                              (task_id, side, kind, name, up.content_type or "", user.get("email", ""))).lastrowid
        rec = {"id": fid, "task_id": task_id, "name": name}
        path, size = _task_file_path(rec), 0
        try:
            with open(path, "wb") as fh:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > TASK_FILE_MAX:
                        raise HTTPException(400, f"{name} is bigger than 500 MB.")
                    fh.write(chunk)
        except BaseException:
            with db._conn() as con:
                con.execute("DELETE FROM task_files WHERE id = ?", (fid,))
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        if name.lower().endswith(_IMG_EXT):
            await asyncio.to_thread(_att_image, path, path + "_t.jpg", 480)
        with db._conn() as con:
            con.execute("UPDATE task_files SET size = ? WHERE id = ?", (size, fid))
            added.append(name)
    if added:
        with db._conn() as con:
            _task_log(con, task_id, user, "file", f"Uploaded {len(added)} file{'s' if len(added) > 1 else ''}: "
                      + ", ".join(added[:6]) + ("…" if len(added) > 6 else ""))
            if t["status"] == "todo" and side == "staff":
                con.execute("UPDATE tasks SET status = 'doing' WHERE id = ?", (task_id,))
    return {"ok": True, "added": len(added)}


@app.get("/api/tasks/file/{file_id}")
def api_task_file(request: Request, file_id: int, thumb: int = 0, download: int = 0):
    _tasks_db()
    with db._conn() as con:
        row = con.execute("SELECT * FROM task_files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "File not found.")
        f = dict(row)
        _task_row(con, f["task_id"], _task_user(request))
    path = _task_file_path(f)
    if thumb and os.path.exists(path + "_t.jpg"):
        return FileResponse(path + "_t.jpg", media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
    if not os.path.exists(path):
        raise HTTPException(404, "That file is missing from storage.")
    return FileResponse(path, media_type=f["mime"] or None, filename=f["name"] if download else None,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/tasks/file/{file_id}")
def api_task_file_delete(request: Request, file_id: int):
    _tasks_db()
    user = _task_user(request)
    with db._conn() as con:
        row = con.execute("SELECT * FROM task_files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "File not found.")
        f = dict(row)
        t = _task_row(con, f["task_id"], user)
        if not _task_manager(user) and (f["by"] != user.get("email") or t["status"] in ("approved",) + tasks.HISTORY):
            raise HTTPException(403, "Only an admin can remove that file now.")
        con.execute("DELETE FROM task_files WHERE id = ?", (file_id,))
        _task_log(con, f["task_id"], user, "file", f"Removed {f['name']}")
    for p in (_task_file_path(f), _task_file_path(f) + "_t.jpg"):
        try:
            os.remove(p)
        except OSError:
            pass
    return {"ok": True}


def _task_meta(store_id):
    st = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not st:
        raise HTTPException(400, "Pick the task's store first (edit the brief).")
    conn = next((c for c in db.list_meta_connections() if c["id"] == st.get("meta_connection_id")), None)
    if not st.get("meta_account_id") or not conn:
        raise HTTPException(400, f"{st['name'].strip()} has no Meta ad account connected (Settings → Meta Ads).")
    return st, conn


class _Static(StaticFiles):
    """Scripts and styles: pages link them with ?v=<content hash>, so a browser may keep that exact
    copy for a year and never ask again; a changed file gets a new hash. Without ?v: always check."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if b"v=" in (scope.get("query_string") or b""):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


app.include_router(adcopy.router)          # Product Importer → Ad copy
app.include_router(paypal_track.router)    # Apps → PayPal Tracking


@app.get("/paypal-tracking", response_class=HTMLResponse)
def paypal_tracking_page():
    return _page("paypaltrack.html", ("paypaltrack.js", "paypaltrack.css", "nav.js", "styles.css", "skin.css", "chat.js"))


app.mount("/static", _Static(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    print(f"\n  EcomVital running at {BASE_URL}\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
