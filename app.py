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
from contextlib import asynccontextmanager
from datetime import date, datetime, time as clock, timedelta
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
    yield


app = FastAPI(title="ProfitDesk", docs_url=None, redoc_url=None, lifespan=lifespan)

_google_states: set[str] = set()
_shopify_states: dict[str, str] = {}
_cache: dict[tuple, tuple[float, dict]] = {}


# ---------------------------------------------------------------- login wall

# Reachable without logging in: the login page itself and what it needs.
_OPEN = {"/login", "/api/login", "/api/logout", "/api/first-user", "/api/first-restore",
         "/healthz", "/about", "/privacy", "/terms"}


@app.middleware("http")
async def _require_login(request: Request, call_next):
    path = request.url.path
    user = auth.user_for(request.cookies.get(auth.SESSION_COOKIE))
    request.state.user = user
    if user or path in _OPEN or path.startswith("/static/"):
        return await call_next(request)
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


@app.get("/api/users")
def api_users(request: Request):
    return {"me": request.state.user, "users": db.list_users()}


@app.post("/api/users")
def api_add_user(payload: dict):
    try:
        uid = auth.create_user(payload.get("email"), payload.get("name"), payload.get("password"))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return {"id": uid}


@app.delete("/api/users/{user_id}")
def api_remove_user(user_id: int, request: Request):
    if user_id == request.state.user["id"]:
        raise HTTPException(400, "You can't remove your own account while logged in with it.")
    db.delete_user(user_id)
    return {"ok": True}


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


@app.get("/api/cash")
async def api_cash(currency: str = None):
    conns = db.list_cash_connections()
    base = (currency or _display_currency()).upper()
    if not conns:
        return {"currency": base, "connected": [], "summary": None, "problems": []}

    async def one(c):
        if c["provider"] == "airwallex":
            return await awx.snapshot(c["client_id"], c["secret"], account_id=c["account_id"])
        if c["provider"] == "paypal":
            return await paypal.snapshot(c["client_id"], c["secret"])
        raise RuntimeError(f"Unknown provider {c['provider']}")

    got = await asyncio.gather(*[one(c) for c in conns], return_exceptions=True)
    accounts, problems = [], []
    for c, g in zip(conns, got):
        if isinstance(g, Exception):
            problems.append(f"{c['label']}: {g}")
            continue
        accounts.append({"id": c["id"], "label": c["label"], "provider": c["provider"], **g})

    codes = {b["currency"] for a in accounts for b in a["balances"]} | \
            {p["currency"] for a in accounts for p in a["pending"]}
    fx_table = await fx.table(base) if codes - {base} else None
    factor = (lambda code: fx.factor(fx_table, code, base)) if fx_table else (lambda code: 1.0)
    today = datetime.now(CASH_TZ).date()
    return {
        "currency": base,
        "today": today.isoformat(),
        "connected": [{"id": c["id"], "label": c["label"], "provider": c["provider"]} for c in conns],
        "summary": cashflow.summarize(accounts, factor, today, CASH_TZ),
        "problems": problems,
        "fx": None if not fx_table else {"date": fx_table.get("date"),
                                         "source": fx_table.get("source")},
    }


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
                    store["cost_pct"], r["payment_fee"] * f_sales, r["shopify_fee"] * f_sales)
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

    warnings, current, previous, per_store = [], [], [], []
    for store, (rows, notes), cut in zip(stores, results, earlier):
        warnings.extend(notes)
        rows = _in_currency(rows, store, fx_table, base)
        cur = [r for r in rows if r["date"] >= start]
        if cut:
            pre, cut_notes = cut
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
        "delta": metrics.compare(totals, prev_totals),
        "series": series,
        "stores": per_store if scope == "all" or group else [],
        "warnings": sorted(set(warnings)),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


async def _store_window(store, start, end, auth, meta_auth):
    """Live sales and spend for one store, as daily rows. Never raises."""
    key = (store["id"], start, end, store["cost_pct"])
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    notes = []

    if demo.is_demo(store):
        got_sales, got_spend = demo.series(store, start, end)
        rows = [
            metrics.day(d, got_sales[d]["sales"], got_sales[d]["orders"],
                        got_spend[d]["google"], got_spend[d]["meta"], store["cost_pct"])
            for d in _dates(start, end)
        ]
        return rows, ["These are demo stores with invented figures. "
                      "Connect a real Shopify store to replace them."]

    # Days that haven't begun on this store's clock have nothing to fetch.
    store_today = _store_today(store).isoformat()
    if start > store_today:
        rows = [metrics.day(d, 0.0, 0, 0.0, 0.0, store["cost_pct"]) for d in _dates(start, end)]
        _cache[key] = (time.time(), (rows, notes))
        return rows, notes
    end_for_api = min(end, store_today)

    async def sales():
        client = ShopifyClient(store["shop_domain"], store["access_token"])
        return await client.daily_sales(start, end_for_api, store["timezone"])

    async def spend():
        if not store["google_customer_id"]:
            return {}
        if not auth:
            notes.append(f"{store['name']}: Google is not connected, so ad spend shows as zero.")
            return {}
        if store["google_source"] == "sheet":
            return await gsheet.daily_spend(auth["refresh_token"], store["google_customer_id"],
                                            start, end_for_api, store["timezone"])
        return await gads.daily_spend(
            auth["refresh_token"], store["google_customer_id"],
            store["google_login_cid"], start, end_for_api,
        )

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
    rows = []
    for d in _dates(start, end):
        s = got_sales.get(d, {"sales": 0.0, "orders": 0, "payments": {}})
        pay_fee, shop_fee = _fees_for(store, s["payments"], ctx, notes)
        rows.append(metrics.day(d, s["sales"], s["orders"],
                                got_spend.get(d, 0.0), got_meta.get(d, 0.0),
                                store["cost_pct"], pay_fee, shop_fee))

    _cache[key] = (time.time(), (rows, notes))
    return rows, notes


def _zone(store):
    try:
        return ZoneInfo(store["timezone"] or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _now_in(store) -> datetime:
    return datetime.now(_zone(store))


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
        return [metrics.day(day, 0.0, 0, 0.0, 0.0, store["cost_pct"])], notes
    cut = datetime.combine(date.fromisoformat(day), clock.min, tz) + min(
        elapsed, timedelta(hours=24) - timedelta(seconds=1))
    until = cut.time()
    now = cut  # its .hour is the hour the ad platforms are cut at

    if demo.is_demo(store):
        seconds = until.hour * 3600 + until.minute * 60 + until.second
        s, p = demo.day_share(store, day, seconds / 86400)
        return [metrics.day(day, s["sales"], s["orders"], p["google"], p["meta"],
                            store["cost_pct"])], notes

    async def sales():
        client = ShopifyClient(store["shop_domain"], store["access_token"])
        got = await client.daily_sales(day, day, store["timezone"], until=until)
        return got.get(day, {"sales": 0.0, "orders": 0, "payments": {}})

    async def google():
        if not (store["google_customer_id"] and auth):
            return 0.0
        if store["google_source"] == "sheet":
            return await gsheet.spend_until_hour(auth["refresh_token"], store["google_customer_id"],
                                                 day, now.hour, store["timezone"])
        return await gads.spend_until_hour(auth["refresh_token"], store["google_customer_id"],
                                           store["google_login_cid"], day, now.hour)

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
    return [metrics.day(day, got_sales["sales"], got_sales["orders"],
                        got_google, got_meta, store["cost_pct"], pay_fee, shop_fee)], notes


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
