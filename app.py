"""ProfitDesk — ROAS and net profit across every Shopify store you run.

Run:  python app.py     then open http://127.0.0.1:8787

Nothing is stored except your connections and your cost percentage. Sales and
ad spend are read live from Shopify, Google and Meta every time the dashboard loads.
"""
import asyncio
import json
import os
import re
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, time as clock, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import airwallex_client as awx
import paypal_client as paypal
import auth
import cashflow
import statement
import adbills
import cog
import invoices
import mail_client
import db
import demo
import fx
import google_ads_client as gads
import google_sheet as gsheet
import meta_ads_client as meta
import metrics
import shopify_client as shop
from shopify_client import ShopifyClient, ShopifyError

# On Railway (or any host that sets RAILWAY_ENVIRONMENT) listen on all
# interfaces; on a Mac stay on this computer only.
HOST = "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else os.getenv("HOST", "127.0.0.1")
# While ProfitDesk has no accounts yet, creating the owner or restoring a
# backup needs this code when it's set (always set it on a public server).
SETUP_CODE = os.getenv("SETUP_CODE", "")
PORT = int(os.getenv("PORT", "8787"))
# Where people reach ProfitDesk. On a server set PUBLIC_URL (e.g.
# https://app.example.com); every return address for Shopify and Google is
# built from it.
BASE_URL = (os.getenv("PUBLIC_URL") or f"http://{HOST}:{PORT}").rstrip("/")
GOOGLE_REDIRECT = f"{BASE_URL}/auth/google/callback"
SHOPIFY_REDIRECT = f"{BASE_URL}/auth/shopify/callback"

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CACHE_TTL = 60  # seconds; the Refresh button bypasses it


@asynccontextmanager
async def lifespan(app):
    db.init()
    saver = asyncio.create_task(_save_all_history())
    coster = asyncio.create_task(_cog_loop())
    reader = asyncio.create_task(_mail_loop())
    yield
    saver.cancel()
    coster.cancel()
    reader.cancel()


app = FastAPI(title="ProfitDesk", docs_url=None, redoc_url=None, lifespan=lifespan)

_google_states: set[str] = set()
_shopify_states: dict[str, str] = {}
_cache: dict[tuple, tuple[float, dict]] = {}


# ---------------------------------------------------------------- login wall

# Reachable without logging in: the login page itself and what it needs.
_OPEN = {"/login", "/api/login", "/api/logout", "/api/first-user", "/api/first-restore",
         "/healthz", "/about", "/privacy", "/terms"}


# Each desk's pages and data. Anything not listed (Settings, connections, people,
# backups, store setup) is for owners only.
_DESK_PATHS = {
    "profit": ("/", "/api/dashboard", "/api/history"),
    "cash": ("/cash", "/api/cash"),
    "cog": ("/cog", "/api/cog", "/api/invoices"),
    "inbox": ("/inbox", "/api/inbox"),
}
_DESK_HOME = {"profit": "/", "cash": "/cash", "cog": "/cog", "inbox": "/inbox"}
# Shared by every desk's pages: the user's own details and the dropdown lists.
_EVERYONE = {"/api/me", "/api/me/password", "/api/logout", "/change-password",
             "/api/setup"}
# What "read & write" people may change (owners may change anything).
_WRITE_OK = ("/api/invoices", "/api/cog/sync")


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
    desks = [d for d in (user.get("desks") or "").split(",") if d]
    desk = _desk_of(path)
    if desk is None or desk not in desks:
        if is_api:
            return JSONResponse({"error": "You don't have access to that."}, status_code=403)
        return RedirectResponse(_DESK_HOME.get(desks[0] if desks else "", "/change-password"),
                                status_code=303)
    if method not in ("GET", "HEAD") and not (
            user.get("role") == "write" and any(path.startswith(p) for p in _WRITE_OK)):
        return JSONResponse({"error": "Your access is view-only for this."}, status_code=403)
    return None


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    user = auth.user_for(request.cookies.get(auth.SESSION_COOKIE))
    request.state.user = user
    if path in _OPEN or path.startswith("/static/"):
        return await call_next(request)
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


@app.get("/healthz")
def healthz():
    """Up check for Railway, plus where the database lives (paths only) so a
    missing volume is easy to spot."""
    vol = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
    path = os.path.abspath(db.DB_PATH)
    return {"ok": True, "db_path": path, "volume_mount": vol or None,
            "db_on_volume": bool(vol) and path.startswith(vol.rstrip("/") + "/"),
            "has_accounts": auth.has_users()}


@app.get("/login", response_class=HTMLResponse)
def login_page():
    with open(os.path.join(STATIC_DIR, "login.html")) as f:
        html = f.read()
    stamp = int(os.path.getmtime(os.path.join(STATIC_DIR, "styles.css")))
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
        raise HTTPException(400, "ProfitDesk already has an owner. Log in instead.")
    _check_setup_code(payload.get("setup_code"))
    try:
        auth.create_user(payload.get("email"), payload.get("name"), payload.get("password"))
        token = auth.log_in(payload.get("email"), payload.get("password"), _client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"ok": True})
    _set_session(resp, request, token)
    return resp


_MAX_BACKUP = 50 * 1024 * 1024


@app.post("/api/first-restore")
async def api_first_restore(request: Request):
    """Fill a brand-new ProfitDesk from a backup, before any account exists.
    Needs the setup code; afterwards you log in with the accounts in the backup."""
    if auth.has_users():
        raise HTTPException(400, "ProfitDesk already has accounts. Log in, then restore "
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
        raise HTTPException(400, "Choose a ProfitDesk backup file (.db) to upload.")
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
    me["desks"] = [d for d in (me.get("desks") or "").split(",") if d] \
        if me.get("role") != "owner" else list(auth.DESKS)
    return me


@app.get("/api/users")
def api_users(request: Request):
    users = db.list_users()
    for u in users:
        u["desks"] = [d for d in (u.get("desks") or "").split(",") if d]
    return {"me": request.state.user, "users": users}


@app.post("/api/users")
def api_add_user(payload: dict):
    """Add a person with a temporary password they must change at first login."""
    try:
        uid = auth.create_user(payload.get("email"), payload.get("name"), payload.get("password"),
                               payload.get("role"), payload.get("desks"), temporary=True)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return {"id": uid}


@app.put("/api/users/{user_id}")
def api_update_user(user_id: int, payload: dict, request: Request):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(404, "That person no longer exists.")
    try:
        role, desks = auth.clean_access(payload.get("role", user["role"]),
                                        payload.get("desks", (user["desks"] or "").split(",")))
        if user_id == request.state.user["id"] and role != "owner":
            raise auth.AuthError("You can't take owner access away from yourself.")
        if user["role"] == "owner" and role != "owner" and \
                sum(1 for u in db.list_users() if u["role"] == "owner") <= 1:
            raise auth.AuthError("ProfitDesk needs at least one owner.")
        db.set_user_access(user_id, role, desks, (payload.get("name") or "").strip() or None)
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

    return _page("index.html", ("app.js", "nav.js", "styles.css"))


def _page(filename: str, assets) -> HTMLResponse:
    """Serve a page with each script and stylesheet stamped by its edit time,
    so a browser never keeps running an old copy after an update."""
    with open(os.path.join(STATIC_DIR, filename)) as f:
        html = f.read()
    for name in assets:
        stamp = int(os.path.getmtime(os.path.join(STATIC_DIR, name)))
        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- setup state

@app.get("/cog", response_class=HTMLResponse)
def cog_page():
    return _page("cog.html", ("cog.js", "nav.js", "styles.css"))


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
            "sales": 0.0, "cog": 0.0, "estimated": False})
        cost = (r["cost_usd"] or 0.0) * f("USD")
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


@app.post("/api/invoices")
async def api_invoice_upload(request: Request, filename: str = "invoice"):
    """Upload one supplier invoice (PDF, or text/CSV with one line per item)."""
    data = await request.body()
    if not data:
        raise HTTPException(400, "That file is empty.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "That file is too big (15 MB max).")
    try:
        text = invoices.pdf_text(data) if data[:4] == b"%PDF" else data.decode("utf-8", "ignore")
        inv = invoices.parse(text)
    except invoices.InvoiceError as e:
        raise HTTPException(400, str(e))
    for old in db.list_invoices():
        if old["number"] and old["number"] == inv["number"] and old["supplier"] == inv["supplier"] \
                and abs((old["total"] or 0) - inv["total"]) < 0.01:
            raise HTTPException(400, f"Invoice {inv['number']} from {inv['supplier']} is already uploaded.")
    catalog = await _catalog()
    tags = _cog.get("tags") or {}
    store_of = lambda key: tags.get(cog.tag_of(key)) if cog.tag_of(key) else None
    sids = {store_of(l["order"]) for l in inv["lines"]} - {None}
    order_lines = {}
    if sids:
        start = (date.today() - timedelta(days=150)).isoformat()
        for r in db.cog_lines(list(sids), start, date.today().isoformat()):
            order_lines.setdefault((r["store_id"], cog.order_key(r["order_name"])), []).append(r)
    invoiced = {(l["store_id"], l["order_key"], cog.norm(l["product"] or "")): l["number"]
                for l in db.invoice_lines() if l["status"] not in ("unmatched", "duplicate")}
    checked = invoices.check(inv, store_of, order_lines, catalog, invoiced)
    user = (getattr(request.state, "user", None) or {}).get("email", "")
    iid = db.save_invoice(inv, checked, filename[:120], user)
    for sid in sids:
        names = {l["order_name"] for l in checked["lines"] if l.get("order_name") and l["store_id"] == sid}
        await _recost(sid, names)
    return {"ok": True, "id": iid, "supplier": inv["supplier"], "number": inv["number"],
            "lines": len(inv["lines"]), "total": inv["total"], "sum": checked["sum"],
            "counts": checked["counts"], "missing": len(checked["missing"])}


@app.get("/api/invoices")
def api_invoices():
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    out = []
    for i in db.list_invoices():
        i["store"] = stores.get(i["store_id"], "Several / unknown")
        i["missing"] = len(json.loads(i["missing"] or "[]"))
        out.append(i)
    return {"invoices": out, "to_check": sum(i["to_check"] or 0 for i in out)}


@app.get("/api/invoices/{invoice_id}")
def api_invoice(invoice_id: int):
    inv = db.get_invoice(invoice_id)
    if not inv:
        raise HTTPException(404, "That invoice no longer exists.")
    inv["missing"] = json.loads(inv["missing"] or "[]")
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    inv["store"] = stores.get(inv["store_id"], "Several / unknown")
    return {"invoice": inv, "lines": db.invoice_lines(invoice_id)}


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


@app.delete("/api/invoices/{invoice_id}")
async def api_invoice_delete(invoice_id: int):
    inv = db.get_invoice(invoice_id)
    names = {l["order_name"] for l in db.invoice_lines(invoice_id) if l["order_name"]}
    db.delete_invoice(invoice_id)
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


# ---------------------------------------------------------------- inbox (see mail_client.py)

_reading: set = set()


async def _read_mailbox(acct: dict) -> int:
    """Copy a mailbox's new emails in, saving after every batch of 50."""
    if acct["id"] in _reading:          # already being read (e.g. right after connecting)
        return 0
    _reading.add(acct["id"])

    def save(batch, top):
        db.save_mail(acct["id"], batch)
        db.mail_checked(acct["id"], last_uid=top)
    try:
        count, last = await asyncio.to_thread(mail_client.fetch_new, acct["address"], acct["password"],
                                              acct["last_uid"], acct["provider"], save)
        db.mail_checked(acct["id"], last_uid=last)
        return count
    except Exception as e:
        db.mail_checked(acct["id"], error=str(e)[:300])
        return 0
    finally:
        _reading.discard(acct["id"])


async def _mail_loop():
    """Copy new emails from every connected mailbox every 2 minutes."""
    await asyncio.sleep(20)
    while True:
        for acct in db.list_mail_accounts():
            try:
                await _read_mailbox(acct)   # fresh row each time: last_uid moves on
            except Exception as e:
                print(f"Reading {acct['address']} failed: {e}", flush=True)
        await asyncio.sleep(120)


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page():
    return _page("inbox.html", ("inbox.js", "nav.js", "styles.css"))


@app.get("/api/mail-accounts")
def api_mail_accounts():
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    with db._conn() as con:
        counts = {r["account_id"]: r["n"] for r in con.execute(
            "SELECT account_id, COUNT(*) n FROM mail_messages GROUP BY account_id")}
    return {"accounts": [{"id": a["id"], "address": a["address"], "store": stores.get(a["store_id"], ""),
                          "store_id": a["store_id"], "last_checked": a["last_checked"],
                          "error": a["last_error"], "emails": counts.get(a["id"], 0)}
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


@app.get("/api/inbox/messages")
def api_inbox_messages(account: int = None):
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    rows = db.mail_list([account] if account else None)
    for r in rows:
        r["store"] = stores.get(r["store_id"], "")
        r["attachments"] = len(json.loads(r["attachments"] or "[]"))
    return {"messages": rows}


@app.get("/api/inbox/messages/{message_id}")
def api_inbox_message(message_id: int):
    m = db.mail_get(message_id)
    if not m:
        raise HTTPException(404, "That email no longer exists.")
    stores = {x["id"]: x["name"].strip() for x in db.list_stores()}
    m["store"] = stores.get(m["store_id"], "")
    m["attachments"] = json.loads(m["attachments"] or "[]")
    m.pop("html", None)          # shown as plain text for now (safe)
    return m


@app.get("/api/history")
async def api_history(fresh: int = 0):
    """How far back each store's sales go: Shopify's window, whether the store has
    approved full order history, and the days ProfitDesk has saved."""
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
    install screen; ProfitDesk picks the connection up when Shopify comes back.
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
    return _page("cash.html", ("cash.js", "nav.js", "styles.css"))


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
        for x in google_stores:
            cid = _digits_only(x["google_customer_id"])
            rows.append(adbills.google_row(x, tabs.get(cid), charges.get(cid, []), now))

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
    summary = cashflow.summarize(accounts, factor, today, CASH_TZ)
    if ad_rows is not None:
        ads = adbills.summarize(ad_rows, factor)
        summary["ads"] = ads
        summary["ads_payable"] = ads["total"]
        summary["after_ads"] = summary["position"] - ads["total"]
    return {"base": base, "conns": conns, "problems": problems, "factor": factor, "today": today,
            "fx_table": fx_table, "summary": summary}


@app.get("/api/cash")
async def api_cash(currency: str = None):
    st = await _cash_state(currency, with_ads=True)
    if not st["conns"]:
        return {"currency": st["base"], "connected": [], "summary": None, "problems": []}
    fx_table = st["fx_table"]
    return {
        "currency": st["base"],
        "today": st["today"].isoformat(),
        "connected": [{"id": c["id"], "label": c["label"], "provider": c["provider"]}
                      for c in st["conns"]],
        "summary": st["summary"],
        "problems": st["problems"],
        "fx": None if not fx_table else {"date": fx_table.get("date"),
                                         "source": fx_table.get("source")},
    }


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
                          shopify=by_gateway if shop_ok else None, ad_spend=ad_spend)
    return {"currency": st["base"], "problems": problems, **out}


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
                    r["paypal_sales"] * f_sales)
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

@app.get("/api/dashboard")
async def api_dashboard(scope: str = "all", start: str = None, end: str = None,
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

    base = (currency or _display_currency()).upper()
    used = {c for s in stores
            for c in (s["currency"], s["meta_currency"], s["google_currency"]) if c}
    fx_table = await fx.table(base) if used - {base} else None

    over = await _paypal_over_limit(min(prev_start, start), end)
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

    series = metrics.with_ratios(metrics.blend(current))
    totals = metrics.summarize(series)
    prev_totals = metrics.summarize(metrics.blend(previous))

    short = await _short_history(stores, min(start, prev_start))
    if short:
        names = ", ".join(x["name"].strip() for x in short)
        warnings.append(
            f"Shopify only shares the last 60 days of orders with ProfitDesk for {names}, so "
            f"sales before {short[0]['_from']} show as zero (ad spend still counts). ProfitDesk "
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

    async def sales():
        # Shopify only shares the last 60 days; older days come from the totals
        # ProfitDesk saved while they were still inside that window.
        cutoff = _shopify_cutoff(store)
        if start < cutoff and "read_all_orders" in await _granted(store):
            cutoff = start            # approved for full history: ask Shopify for all of it
        got = {}
        live_from = max(start, cutoff)
        if live_from <= end_for_api:
            client = ShopifyClient(store["shop_domain"], store["access_token"])
            got = await client.daily_sales(live_from, end_for_api, store["timezone"])
            _save_sales(store, got, live_from, end_for_api)
        if start < cutoff:
            older = db.saved_shopify_days(store["id"], start,
                                          (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat())
            got = {**older, **got}
        return got

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
            got = await gads.daily_spend(
                auth["refresh_token"], store["google_customer_id"],
                store["google_login_cid"], start, end_for_api,
            )
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

    got_sales, got_spend, got_meta = await asyncio.gather(
        sales(), spend(), meta_spend(), return_exceptions=True
    )

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

    ctx = await _fee_context(store, notes)
    cogs = await _cog_for(store, start, end,
                          {d: got_sales.get(d, {}).get("sales", 0.0) for d in _dates(start, end)})
    tracked = _tracked_fees(store, start, end)
    rows = []
    for d in _dates(start, end):
        s = got_sales.get(d, {"sales": 0.0, "orders": 0, "payments": {}})
        pay_fee, shop_fee = _fees_for(store, s["payments"], ctx, notes)
        known, covered = tracked.get(d, (0.0, 0.0))
        pay_fee = metrics.provider_fee_day(known, covered,
                                           sum(v[1] for v in s["payments"].values()), pay_fee)
        c, est = cogs.get(d, (0.0, 0.0))
        rows.append(metrics.day(d, s["sales"], s["orders"],
                                got_spend.get(d, 0.0), got_meta.get(d, 0.0),
                                c, pay_fee, shop_fee, est, metrics.reserve_held(s["payments"]),
                                metrics.paypal_sales(s["payments"])))

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
                got = await ShopifyClient(x["shop_domain"], x["access_token"]).daily_sales(
                    start, end, x["timezone"])
                _save_sales(x, got, start, end)
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
        # Each store's tag (AS…, …CH) from its own order names, then its cost history.
        known = {sid: [o["name"] for o in v[2]] for sid, v in fetched.items()}
        saved = _cog.get("tags") or {}
        tags = {**saved, **cog.store_tags(known)}
        _cog["tags"] = tags
        catalog = cog.Catalog(rows, tags)
        catalog.add_invoices(db.invoice_lines())
        _cog["catalog"] = catalog
        for x in stores:
            if x["id"] not in fetched:
                continue
            start, end, orders = fetched[x["id"]]
            rate = await _usd_to(x["currency"])
            out = []
            for o in orders:
                for n, line in enumerate(cog.cost_order(o, x["id"], catalog, rate)):
                    out.append({**line, "order": o["name"], "line_no": n, "day": o["day"],
                                "created": o["created"], "cancelled": o["cancelled"],
                                "fulfillment": o["fulfillment"]})
            db.replace_cog_lines(x["id"], start, end, out)
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
    out = {}
    for c, g in zip(conns, got):
        if isinstance(g, Exception):
            print(f"Fees from {c['label']} failed: {g}", flush=True)
        else:
            out.update(g)
    return out


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
    """Keep COG current: recent days every 20 minutes, the whole window every 6 hours."""
    await asyncio.sleep(60)
    n = 0
    while True:
        try:
            if db.get_setting("cog_sheet_id"):
                if n % 18 == 0:
                    await _cog_rows(force=True)
                    await _cog_sync()
                else:
                    await _cog_sync(days_back=2)
        except Exception as e:
            print(f"COG sync failed: {e}", flush=True)
        n += 1
        await asyncio.sleep(20 * 60)


async def _catalog() -> "cog.Catalog":
    """The current product cost history (sheet + approved invoices), rebuilt if needed."""
    if not _cog.get("tags"):
        _cog["tags"] = cog.store_tags(db.store_order_names())
    if not _cog.get("catalog"):
        catalog = cog.Catalog(await _cog_rows(), _cog["tags"])
        catalog.add_invoices(db.invoice_lines())
        _cog["catalog"] = catalog
    return _cog["catalog"]


async def _recost(store_id: int, order_names: set):
    """Re-cost saved order lines (no Shopify call), e.g. after an invoice upload."""
    store = next((x for x in db.list_stores() if x["id"] == store_id), None)
    if not store or not order_names:
        return
    catalog = cog.Catalog(await _cog_rows(), _cog.get("tags") or cog.store_tags(db.store_order_names()))
    catalog.add_invoices(db.invoice_lines())
    _cog["catalog"] = catalog
    rate = await _usd_to(store["currency"])
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
                           "subtotal": r["subtotal"], "sku": r["sku"]})
        o["nos"].append(r["line_no"])
    updates = []
    for o in orders.values():
        for no, line in zip(o["nos"], cog.cost_order(o, store_id, catalog, rate)):
            updates.append({"order_name": o["name"], "line_no": no, "cost": line["cost"],
                            "source": line["source"], "supplier": line["supplier"], "note": line["note"]})
    db.update_cog_costs(store_id, updates)
    _cache.clear()


def _tracked_fees(store, start: str, end: str, until: datetime = None) -> dict:
    """{day: (provider fees of tracked orders, amount those payments cover)}:
    actual fee where posted, estimate otherwise."""
    out = {}
    if demo.is_demo(store):
        return out
    for r in db.order_fees(store["id"], start, end):
        if until is not None and datetime.fromisoformat(r["created"]) > until:
            continue
        fee = r["fee_actual"] if r["fee_actual"] is not None else (r["fee_est"] or 0.0)
        k, c = out.get(r["day"], (0.0, 0.0))
        out[r["day"]] = (k + fee, c + r["amount"])
    return out


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
                                              store["google_login_cid"], day, now.hour)
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

    pay_fee, shop_fee = _fees_for(store, got_sales["payments"],
                                  await _fee_context(store, notes), notes)
    known, covered = _tracked_fees(store, day, day, until=cut).get(day, (0.0, 0.0))
    pay_fee = metrics.provider_fee_day(known, covered,
                                       sum(v[1] for v in got_sales["payments"].values()), pay_fee)
    c, est = (await _cog_for(store, day, day, {day: got_sales["sales"]}, until=cut)).get(day, (0.0, 0.0))
    return [metrics.day(day, got_sales["sales"], got_sales["orders"],
                        got_google, got_meta, c, pay_fee, shop_fee, est,
                        metrics.reserve_held(got_sales["payments"]),
                        metrics.paypal_sales(got_sales["payments"]))], notes


def _dates(start: str, end: str):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        yield d.isoformat()
        d += timedelta(days=1)


def _closer(message: str, ok: bool = False) -> str:
    tone = "#0F9D74" if ok else "#B3402A"
    return f"""<!doctype html><meta charset="utf-8"><title>ProfitDesk</title>
<style>body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
background:#F7F8FA;color:#14181F;display:grid;place-items:center;height:100vh;
margin:0;text-align:center;padding:24px}}
strong{{color:{tone}}}</style>
<div><p><strong>{message}</strong></p></div>
<script>if(window.opener){{window.opener.postMessage('profitdesk:connected','*');
setTimeout(()=>window.close(),1500);}}</script>"""


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    print(f"\n  ProfitDesk running at {BASE_URL}\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
