"""Research → Find competitors: the VAs' competitor / winning-product routine, automated.

    1 Discover   (Apify)  one keyword × the ticked countries in Meta's Ad Library: active, image ads,
                          most-shown first, capped per country, all countries in ONE Apify run.
    2 Qualify    (free)   per new store: Shopify? (products.json / page source), store age (oldest
                          product), dropshipper score from the VAs' signs (place / boutique names,
                          discount wording, image ads, Shop now buttons, several Facebook pages).
    3 Bestsellers(free)   the store's own best-selling order (collections/all?sort_by=best-selling,
                          what PPSpy shows) matched with each product's created date.
    4 Deep check (Apify)  the store's domain searched in the Ad Library (= the VAs pasting the URL):
                          every Facebook page of the store, its top ads, creatives per product.
    5 Label      (free)   golden nugget / compete in the same market / proven, try another market.

Every day (free) the bestseller order of every qualified store is read again (`watch`), so a product
jumping into the top 25 shows up the day it happens; rank history builds "top for months".
Optional auto-hunt rotates the saved keywords (off by default). Apify use is capped everywhere.
"""
import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Request

import apify_client as apify
import db
import research as rs

router = APIRouter()
HK = ZoneInfo("Asia/Hong_Kong")

COUNTRIES = [("US", "United States"), ("GB", "United Kingdom"), ("CA", "Canada"), ("AU", "Australia"),
             ("NZ", "New Zealand")]
DEFAULT_KEYWORDS = [
    # the VAs' keywords
    "50% off", "free shipping", "anniversary sale", "closure sale", "closing sale", "75% off today", "boutique",
    # more of the same kind
    "70% off", "60% off", "80% off", "clearance sale", "closing down sale", "store closing", "final sale",
    "last day", "everything must go", "going out of business", "today only", "limited stock", "sitewide sale",
]
PRICE_PER_AD = 0.00075            # Apify scraper, $0.75 / 1,000 ads (the panel shows the real cost)
DEEP_CAP = 50                     # ads read per store in the deep check
QUALIFY_DAYS = 30                 # store must be at least this old
MAX_STORE_DAYS = 730              # … and at most 2 years old (owner: no stores from 2019 etc.)
COMPARE_SHARE = 0.7               # most products must show a compare-at ("was") price
QUALIFY_SCORE = 50                # dropshipper score to pass
RECHECK_DAYS = 14                 # a store seen before isn't re-qualified sooner than this
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

SKIP_DOMAINS = ("facebook.com", "fb.com", "fb.me", "instagram.com", "m.me", "wa.me", "whatsapp.com", "messenger.com",
                "apple.com", "google.com", "youtube.com", "tiktok.com", "amazon.", "ebay.", "etsy.com", "temu.com",
                "shein.com", "aliexpress.", "linktr.ee", "bit.ly", "walmart.com", "target.com")
PLACES = """london manchester liverpool birmingham leeds glasgow edinburgh bristol oxford cambridge brighton york
chelsea kensington soho mayfair notting cotswold devon cornwall yorkshire surrey kent essex belfast cardiff
newyork nyc brooklyn manhattan boston chicago miami austin dallas houston denver seattle portland atlanta nashville
vegas losangeles hollywood malibu beverly sanfrancisco sandiego texas california florida carolina georgia ohio
virginia montana colorado arizona hamptons savannah charleston aspen napa sonoma hawaii
toronto vancouver montreal ottawa calgary quebec halifax victoria
sydney melbourne brisbane perth adelaide byron bondi hobart noosa canberra
auckland wellington christchurch queenstown
paris milan milano rome roma florence firenze venice venezia monaco madrid barcelona berlin munich vienna
amsterdam copenhagen stockholm oslo zurich geneva dublin lisbon athens
italia italy france french english british scottish irish nordic riviera amalfi capri positano tuscany
provence sorrento como portofino cannes nice marbella ibiza mykonos santorini""".split()
BOUTIQUE = ("boutique", "atelier", "maison", "studio", "house of", "collective", "& co", " co.", "lane", "avenue",
            "street", "label", "apparel", "attire", "closet", "wardrobe")
DISCOUNT = re.compile(r"\d+\s*%\s*off|\bsale\b|free (shipping|delivery)|clearance|clos(ing|ure)|anniversary|"
                      r"today only|last day|final|limited|everything must go|out of business|sitewide", re.I)
GOOD_CTA = {"SHOP_NOW", "ORDER_NOW", "LEARN_MORE", "BUY_NOW", "SHOP NOW", "ORDER NOW", "LEARN MORE", "BUY NOW",
            "GET_OFFER", "GET OFFER", "SEE_MORE", "SEE MORE"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_hunts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    countries   TEXT NOT NULL,
    cap         INTEGER NOT NULL,
    match       TEXT NOT NULL DEFAULT 'phrase',
    deep_max    INTEGER NOT NULL DEFAULT 15,
    status      TEXT NOT NULL,          -- running | done | error
    stage       TEXT,
    ads         INTEGER, stores INTEGER, shopify INTEGER, qualified INTEGER, new_stores INTEGER,
    deep        INTEGER, nuggets INTEGER,
    cost_usd    REAL,
    error       TEXT,
    by_name     TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS research_seen (
    domain      TEXT PRIMARY KEY,
    shopify     INTEGER,
    item_id     INTEGER,
    checked_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_products (
    item_id     INTEGER NOT NULL,
    handle      TEXT NOT NULL,
    title       TEXT,
    image       TEXT,
    price       TEXT,
    compare_at  TEXT,                   -- the "was" price
    url         TEXT,
    created_at  TEXT,
    rank        INTEGER,                -- bestseller position (1 = best), NULL = not in the top 25
    prev_rank   INTEGER,
    best_since  TEXT,                   -- first day seen in the top 25
    meta_ads    INTEGER NOT NULL DEFAULT 0,
    meta_first  TEXT,                   -- earliest start date of its Meta ads
    label       TEXT,                   -- nugget | compete | proven | top | ads
    why         TEXT,
    history     TEXT NOT NULL DEFAULT '{}',   -- {date: rank}
    first_seen  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (item_id, handle)
);
"""
_ready = {"ok": False}
_hunting: set = set()
_analysing: set = set()


def ensure():
    rs.ensure()
    if not _ready["ok"]:
        with db._conn() as con:
            con.executescript(SCHEMA)
            if "compare_at" not in {r[1] for r in con.execute("PRAGMA table_info(research_products)")}:
                con.execute("ALTER TABLE research_products ADD COLUMN compare_at TEXT")
            con.execute("UPDATE research_hunts SET status = 'error', error = 'Stopped by an app restart; run it again.',"
                        " finished_at = ? WHERE status = 'running'", (rs._now(),))
        _ready["ok"] = True


def config() -> dict:
    try:
        c = json.loads(db.get_setting("research_hunt") or "{}")
    except ValueError:
        c = {}
    return {"keywords": c.get("keywords") or list(DEFAULT_KEYWORDS),
            "countries": c.get("countries") or [k for k, _ in COUNTRIES],
            "cap": int(c.get("cap") or 40), "match": c.get("match") or "phrase",
            "deep_max": int(c.get("deep_max") if c.get("deep_max") is not None else 15),
            "auto": bool(c.get("auto")), "auto_hour": int(c.get("auto_hour") if c.get("auto_hour") is not None else 8),
            "auto_next": int(c.get("auto_next") or 0), "auto_last": c.get("auto_last") or "",
            "watch_last": c.get("watch_last") or ""}


def save_config(c: dict):
    db.set_setting("research_hunt", json.dumps(c))


def estimate(n_countries: int, cap: int, deep_max: int) -> float:
    return round((n_countries * cap + deep_max * DEEP_CAP) * PRICE_PER_AD, 2)


# ---------------------------------------------------------------- reading ads

def library_url(q: str, country: str = "ALL", media: str = "image", match: str = "phrase") -> str:
    st = "keyword_exact_phrase" if match == "phrase" else "keyword_unordered"
    qq = f'"{q}"' if match == "phrase" else q
    return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country={country}&is_targeted_country=false&media_type={media}&q={quote(qq)}&search_type={st}")


def unwrap(url: str) -> str:
    if not url:
        return ""
    try:
        u = urlparse(url)
        if u.hostname and u.hostname.endswith("facebook.com") and u.path.startswith("/l.php"):
            return parse_qs(u.query).get("u", [""])[0]
    except ValueError:
        return ""
    return url


def store_domain(url: str) -> str:
    d = rs.domain_of(unwrap(url))
    if not d or any(s in d for s in SKIP_DOMAINS):
        return ""
    return d


def ad_info(it: dict) -> dict:
    """parse_ad plus what the hunt needs: page id, button, format, the store link, the search it came from."""
    a = rs.parse_ad(it)
    snap = rs._first(it, "snapshot", "adSnapshot") or {}
    cards = snap.get("cards") or []
    link = a["link"] or ((cards[0] or {}).get("link_url") if cards else "") or ""
    src = str(rs._first(it, "url", "inputUrl", "input_url", "sourceUrl", "ad_library_url") or "")
    country = (parse_qs(urlparse(src).query).get("country", [""])[0] if "ads/library" in src else "")
    return {**a, "link": link, "domain": store_domain(link),
            "page_id": str(rs._first(it, "page_id", "pageId") or snap.get("page_id") or ""),
            "cta": str(rs._first(snap, "cta_type", "ctaType", "cta_text", "ctaText") or "").upper(),
            "format": str(rs._first(snap, "display_format", "displayFormat") or "").upper(),
            "country": country, "source": src}


def product_handle(link: str) -> str:
    m = re.search(r"/products/([A-Za-z0-9\-_%.]+)", unwrap(link) or "")
    return m.group(1).lower().split("?")[0] if m else ""


# ---------------------------------------------------------------- reading a store (free)

BLOCKED = {403, 429, 430, 503}          # Shopify's "too many requests" / bot refusals (430 = security rejection)
_store_sem = asyncio.Semaphore(3)       # stores read at once, all callers together


async def _get(client, url, log: list = None):
    """GET with a few patient retries when the store says slow down."""
    for wait in (0, 3, 8):
        if wait:
            await asyncio.sleep(wait)
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            if log is not None:
                log.append(f"{urlparse(url).path}: {type(e).__name__}")
            continue
        if log is not None:
            log.append(f"{urlparse(url).path}: {r.status_code}")
        if r.status_code not in (429, 430, 503):
            return r
    return None


async def read_store(domain: str) -> dict:
    """{shopify, final_domain, products, best, since, unreliable, error, blocked, via, log}. Reads the store
    directly; when Shopify refuses the server's address, again through Apify Proxy."""
    out = await _read_store(domain, None)
    if out["blocked"]:
        token = db.get_setting("apify_token")
        proxy = await apify.proxy_url(token) if token else None
        if proxy:
            again = await _read_store(domain, proxy)
            again["log"] = out["log"] + ["(again through Apify Proxy)"] + again["log"]
            if again["products"] or not again["blocked"]:
                again["via"] = "apify proxy"
                return again
            out["log"] = again["log"]
    return out


async def _read_store(domain: str, proxy) -> dict:
    out = {"shopify": False, "final_domain": domain, "products": {}, "best": [], "since": None,
           "unreliable": False, "error": "", "blocked": False, "via": "direct", "log": []}
    log = out["log"]
    async with _store_sem, httpx.AsyncClient(timeout=25, follow_redirects=True, proxy=proxy, headers={
            "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}) as c:
        base = f"https://{domain}"
        for page in range(1, 9):
            r = await _get(c, f"{base}/products.json?limit=250&page={page}", log)
            if r is None or r.status_code in BLOCKED:
                if page == 1:
                    out["blocked"] = True
                break
            if r.status_code >= 400:
                break
            if page == 1:
                out["final_domain"] = rs.domain_of(str(r.url)) or domain
                base = f"https://{out['final_domain']}"
            try:
                prods = r.json().get("products")
            except ValueError:
                break
            if prods is None:
                break
            out["shopify"] = True
            for p in prods:
                h = (p.get("handle") or "").lower()
                if not h:
                    continue
                img = ((p.get("images") or [{}])[0] or {}).get("src") or ""
                var = (p.get("variants") or [{}])[0] or {}
                out["products"][h] = {"title": p.get("title") or h, "image": img, "price": var.get("price") or "",
                                      "compare_at": var.get("compare_at_price") or "",
                                      "created_at": (p.get("created_at") or p.get("published_at") or "")[:10],
                                      "url": f"{base}/products/{h}"}
            if len(prods) < 250:
                break
        if not out["shopify"]:
            r = await _get(c, base, log)
            if r is None or r.status_code in BLOCKED:
                out["blocked"] = True
            elif r.status_code < 400 and ("cdn.shopify.com" in r.text or "Shopify.shop" in r.text):
                out["shopify"] = True
                out["error"] = "Shopify store, but it hides its product list."
            return out
        def _was(p):
            try:
                return float(p["compare_at"] or 0) > float(p["price"] or 0) > 0
            except ValueError:
                return False
        out["compare_share"] = round(sum(1 for p in out["products"].values() if _was(p)) / len(out["products"]), 2) \
            if out["products"] else None
        dates = [p["created_at"] for p in out["products"].values() if p["created_at"]]
        out["since"] = min(dates) if dates else None
        seen = []
        for page in (1, 2):
            r = await _get(c, f"{base}/collections/all?sort_by=best-selling&page={page}", log)
            if r is None or r.status_code >= 400:
                break
            for h in re.findall(r"/products/([A-Za-z0-9\-_%.]+)", r.text):
                h = h.lower()
                if h in out["products"] and h not in seen:
                    seen.append(h)
            if len(seen) >= 25:
                break
        out["best"] = seen[:25]
        newest = [h for h, _ in sorted(out["products"].items(), key=lambda kv: kv[1]["created_at"], reverse=True)][:8]
        if len(out["best"]) >= 8 and out["best"][:8] == newest:
            out["unreliable"] = True      # the "best selling" page is just newest first
    return out


def score(names: list, domain: str, texts: list, ctas: list, formats: list, n_pages: int, n_products: int) -> tuple:
    """Dropshipper score 0-100 and the reasons, from the VAs' signs."""
    pts, why = 0, []
    blob = " ".join(names + [domain]).lower()
    squash = re.sub(r"[^a-z]", "", blob)
    place = next((p for p in PLACES if p in squash), None)
    if place:
        pts += 25; why.append(f"place in the name ({place})")
    bq = next((b for b in BOUTIQUE if b in blob), None)
    if bq:
        pts += 15; why.append(f"“{bq.strip()}” name")
    disc = sum(1 for t in texts if t and DISCOUNT.search(t))
    if texts and disc / len(texts) >= 0.3:
        pts += 20; why.append("discount / sale wording")
    if formats and sum(1 for f in formats if f in ("IMAGE", "")) / len(formats) >= 0.8:
        pts += 10; why.append("image ads")
    if ctas and sum(1 for c in ctas if c in GOOD_CTA) / len(ctas) >= 0.5:
        pts += 10; why.append("Shop now / Order now buttons")
    if n_pages >= 2:
        pts += 10; why.append(f"{n_pages} Facebook pages")
    pts += 10; why.append("Shopify")
    if n_products >= 50:
        pts += 5; why.append(f"{n_products} products")
    return min(pts, 100), why


def _days(d: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(d[:10])).days
    except (TypeError, ValueError):
        return -1


def label(p: dict, store_age: int, today: date) -> tuple:
    rank, age = p.get("rank"), _days(p.get("created_at") or "", today)
    in_top = rank is not None and rank <= 25
    top_days = _days(p.get("best_since") or "", today)
    ads = p.get("meta_ads") or 0
    a = f"created {age} days ago" if age >= 0 else "created date unknown"
    if in_top and 0 <= age <= 21 and store_age >= QUALIFY_DAYS:
        return "nugget", f"#{rank} bestseller, {a}, store {store_age} days old: a new winner coming up"
    if rank is not None and rank <= 5 and 0 <= age <= 56 and ads >= 2:
        return "compete", f"#{rank} bestseller, {a}, {ads} ads on Meta: compete in the same market"
    if rank is not None and rank <= 5 and (age >= 90 or top_days >= 60):
        return "proven", f"#{rank} bestseller, {a}: proven, try it in another market"
    if in_top:
        return "top", f"#{rank} bestseller, {a}" + (f", {ads} ads on Meta" if ads else "")
    if ads:
        return "ads", f"Not in the top 25, {ads} ads on Meta, {a}"
    return None, ""


def relabel(item_id: int):
    """Labels, nugget count and top product of one store from its saved products."""
    today = datetime.now(HK).date()
    item = rs.get_item(item_id)
    store_age = _days(item.get("store_since") or "", today)
    with db._conn() as con:
        prods = [dict(r) for r in con.execute("SELECT * FROM research_products WHERE item_id = ?", (item_id,))]
        nug = 0
        for p in prods:
            lab, why = label(p, store_age, today)
            nug += lab == "nugget"
            con.execute("UPDATE research_products SET label = ?, why = ? WHERE item_id = ? AND handle = ?",
                        (lab, why, item_id, p["handle"]))
        top = min((p for p in prods if p["rank"]), key=lambda p: p["rank"], default=None)
        con.execute("UPDATE research_items SET nuggets = ?, top_product = ? WHERE id = ?",
                    (nug, (top or {}).get("title") or "", item_id))


def save_products(item_id: int, store: dict):
    """Top 25 bestsellers (rank, rank history) plus products already known from Meta ads."""
    today = datetime.now(HK).date().isoformat()
    now = rs._now()
    best = {h: i + 1 for i, h in enumerate(store["best"])}
    with db._conn() as con:
        old = {r["handle"]: dict(r) for r in con.execute("SELECT * FROM research_products WHERE item_id = ?", (item_id,))}
        for h, p in store["products"].items():
            rank = best.get(h)
            o = old.get(h)
            if rank is None and not o:
                continue
            hist = json.loads((o or {}).get("history") or "{}")
            if rank:
                hist[today] = rank
            hist = dict(sorted(hist.items())[-120:])
            best_since = (o or {}).get("best_since") or (today if rank else None)
            if not rank:
                best_since = None
            con.execute(
                "INSERT INTO research_products (item_id, handle, title, image, price, compare_at, url, created_at, rank, prev_rank,"
                " best_since, history, first_seen, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(item_id, handle) DO UPDATE SET title = excluded.title, image = excluded.image,"
                " price = excluded.price, compare_at = excluded.compare_at, url = excluded.url, created_at = excluded.created_at,"
                " prev_rank = research_products.rank, rank = excluded.rank, best_since = excluded.best_since,"
                " history = excluded.history, updated_at = excluded.updated_at",
                (item_id, h, p["title"], p["image"], p["price"], p.get("compare_at") or "", p["url"], p["created_at"], rank,
                 (o or {}).get("rank"), best_since, json.dumps(hist), now, now))


async def analyse(item_id: int, extra: dict = None, store: dict = None) -> dict:
    """Stages 2, 3, 5 for one row (free): Shopify, store age, score, bestsellers, labels."""
    ensure()
    item = rs.get_item(item_id)
    dom = item.get("domain") or ""
    if not dom:
        return {"ok": False, "error": "Add the website first."}
    sig = merge_signals(item, extra or {})
    store = store or await read_store(dom)
    today = datetime.now(HK).date()
    kept = False
    if store["blocked"] and not store["products"] and item.get("is_shopify"):
        # the store couldn't be read this time: keep what was read before instead of blanking it
        kept = True
        store.update({"shopify": True, "since": item.get("store_since"), "unreliable": bool(item.get("best_unreliable")),
                      "compare_share": item.get("compare_share")})
    n_products = len(store["products"]) or (item.get("products_count") or 0 if kept else 0)
    pages = json.loads(item.get("fb_pages") or "[]")
    names = [item.get("name") or ""] + [p.get("name", "") for p in pages] + sig["names"]
    pts, why = score(names, store["final_domain"], sig["texts"], sig["ctas"], sig["formats"],
                     max(len(pages), sig["n_pages"]), n_products)
    if not store["shopify"]:
        pts, why = 0, ["not a Shopify store" if not store["blocked"] else "the store refused to be read"]
    age = _days(store["since"] or "", today)
    share = store.get("compare_share")
    if share is not None:
        why.append(f"{round(share * 100)}% of products show a was-price")
    qualified = 1 if (store["shopify"] and QUALIFY_DAYS <= age <= MAX_STORE_DAYS and pts >= QUALIFY_SCORE
                      and share is not None and share >= COMPARE_SHARE) else 0
    if store["shopify"] and age > MAX_STORE_DAYS:
        why.append("store older than 2 years: left out")
    elif store["shopify"] and 0 <= age < QUALIFY_DAYS:
        why.append(f"store only {age} days old: too new")
    if store["shopify"] and share is not None and share < COMPARE_SHARE:
        why.append("most products have no was-price: left out")
    if store["unreliable"]:
        why.append("its ‘best selling’ page looks like newest-first, ranks may not be sales")
    if store["error"]:
        why.append(store["error"])
    if kept:
        why.append("couldn't read the store this time, kept the earlier figures")
    with db._conn() as con:
        con.execute("UPDATE research_items SET is_shopify = ?, store_since = ?, products_count = ?, ds_score = ?,"
                    " ds_reasons = ?, qualified = ?, best_unreliable = ?, analysed_at = ?, ad_signals = ?, compare_share = ?"
                    " WHERE id = ?",
                    (1 if store["shopify"] else 0, store["since"], n_products, pts, "; ".join(why),
                     qualified, 1 if store["unreliable"] else 0, rs._now(), json.dumps(sig), share, item_id))
        con.execute("INSERT OR REPLACE INTO research_seen (domain, shopify, item_id, checked_at) VALUES (?,?,?,?)",
                    (dom, 1 if store["shopify"] else 0, item_id, rs._now()))
    if store["products"]:
        save_products(item_id, store)
    relabel(item_id)
    return {"ok": True, "shopify": store["shopify"], "score": pts, "qualified": bool(qualified),
            "bestsellers": len(store["best"]), "since": store["since"], "kept": kept, "via": store["via"],
            "blocked": store["blocked"]}


def merge_signals(item: dict, extra: dict) -> dict:
    """The ad signs a store has shown so far (saved on the row), plus the new ones; capped."""
    try:
        old = json.loads(item.get("ad_signals") or "{}")
    except ValueError:
        old = {}
    out = {}
    for k in ("names", "texts", "ctas", "formats"):
        vals = list(old.get(k) or []) + list(extra.get(k) or [])
        if k == "names":
            vals = list(dict.fromkeys(v for v in vals if v))
        out[k] = vals[-40:]
    out["n_pages"] = max(int(old.get("n_pages") or 0), int(extra.get("n_pages") or 0))
    return out


# ---------------------------------------------------------------- deep check (Apify)

async def deep_check(item_ids: list, by_name: str) -> dict:
    """One Apify run for several stores: each store's domain searched in the Ad Library (all its pages),
    or its Facebook page when there's no website. Fills ads, new today, Facebook pages, ads per product."""
    ensure()
    token = db.get_setting("apify_token")
    if not token:
        return {"ok": False, "error": "Paste the Apify key first."}
    cfg = rs.apify_config()
    items = [rs.get_item(i) for i in item_ids]
    items = [i for i in items if i.get("domain") or i.get("fb_page")]
    if not items:
        return {"ok": False, "error": "Nothing to check."}
    urls = []
    for it in items:
        if it.get("domain"):
            urls.append({"url": library_url(it["domain"], cfg["country"], "all", "any")})
        else:
            urls.append({"url": it["fb_page"]})
    now = rs._now()
    with db._conn() as con:
        run_ids = {it["id"]: con.execute("INSERT INTO research_runs (item_id, status, by_name, started_at)"
                                         " VALUES (?, 'running', ?, ?)", (it["id"], by_name, now)).lastrowid
                   for it in items}
    for it in items:
        rs._running.add(it["id"])
    try:
        payload = {**apify.page_input(urls[0]["url"], cfg["country"], DEEP_CAP), "urls": urls,
                   "count": DEEP_CAP * len(urls), "limitPerSource": DEEP_CAP}
        async with rs._sem:
            run, raw = await apify.run_actor(token, payload)
        if raw:
            db.set_setting("research_apify_sample", json.dumps(raw[0])[:6000])
        ads = [ad_info(r) for r in raw if not r.get("error")]
        cost = apify.run_cost(run)
        share = round(cost / len(items), 4) if cost else None
        today = rs.meta_today()
        for it in items:
            if it.get("domain"):
                mine = [a for a in ads if a["domain"] == it["domain"]]
                pids = {a["page_id"] for a in mine if a["page_id"]}
                mine += [a for a in ads if a["page_id"] in pids and a not in mine and not a["domain"]]
            else:
                mine = ads if len(items) == 1 else [a for a in ads if a["source"] and it["fb_page"].rstrip("/") in a["source"]]
            _save_ads(it, mine, today, by_name, run.get("id"), share, run_ids[it["id"]], len(raw) >= DEEP_CAP * len(urls))
        return {"ok": True, "ads": len(ads), "cost_usd": cost}
    except Exception as e:
        msg = str(e) if isinstance(e, apify.ApifyError) else f"Something went wrong: {e}"
        with db._conn() as con:
            for rid in run_ids.values():
                con.execute("UPDATE research_runs SET status = 'error', error = ?, finished_at = ? WHERE id = ?"
                            " AND status = 'running'", (msg[:500], rs._now(), rid))
        return {"ok": False, "error": msg}
    finally:
        for it in items:
            rs._running.discard(it["id"])


def _save_ads(it: dict, ads: list, today: str, by_name: str, apify_run, cost, run_id: int, capped: bool):
    item_id, now = it["id"], rs._now()
    uniq = {a["ad_id"]: a for a in ads if a["ad_id"]}
    live = [a for a in uniq.values() if a["active"]]
    new_today = sum(1 for a in live if a["start_date"] == today)
    pages = {}
    for a in uniq.values():
        if a["page_id"] or a["page_name"]:
            k = a["page_id"] or a["page_name"]
            pages.setdefault(k, {"id": a["page_id"], "name": a["page_name"], "ads": 0})["ads"] += 1
    per_product = {}
    for a in live:
        h = product_handle(a["link"])
        if h:
            pp = per_product.setdefault(h, {"ads": 0, "first": None})
            pp["ads"] += 1
            if a["start_date"] and (not pp["first"] or a["start_date"] < pp["first"]):
                pp["first"] = a["start_date"]
    with db._conn() as con:
        for a in uniq.values():
            con.execute(
                "INSERT INTO research_ads (item_id, ad_id, page_name, start_date, active, copies, text, link, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id, ad_id) DO UPDATE SET page_name = excluded.page_name,"
                " start_date = excluded.start_date, active = excluded.active, copies = excluded.copies, text = excluded.text,"
                " link = excluded.link, last_seen = excluded.last_seen",
                (item_id, a["ad_id"], a["page_name"], a["start_date"], a["active"], a["copies"], a["text"], a["link"], now, now))
        if not capped and uniq:
            ids = list(uniq)
            con.execute(f"UPDATE research_ads SET active = 0 WHERE item_id = ? AND ad_id NOT IN ({','.join('?' * len(ids))})",
                        (item_id, *ids))
        old = con.execute("SELECT ads_count, ads_new_today FROM research_items WHERE id = ?", (item_id,)).fetchone()
        sets = ["ads_count = ?", "ads_new_today = ?", "ads_capped = ?", "ads_by = ?", "ads_at = ?", "updated_at = ?"]
        vals = [len(live), new_today, 1 if capped else 0, rs.APP_NAME, now, now]
        if pages:
            sets.append("fb_pages = ?"); vals.append(json.dumps(sorted(pages.values(), key=lambda p: -p["ads"])))
            if not it.get("fb_page"):
                top = max(pages.values(), key=lambda p: p["ads"])
                if top["id"]:
                    sets.append("fb_page = ?"); vals.append(f"https://www.facebook.com/{top['id']}")
        con.execute(f"UPDATE research_items SET {', '.join(sets)} WHERE id = ?", (*vals, item_id))
        if old["ads_count"] != len(live):
            rs._log(con, item_id, "ads_count", old["ads_count"], len(live), rs.APP_NAME)
        if old["ads_new_today"] != new_today:
            rs._log(con, item_id, "ads_new_today", old["ads_new_today"], new_today, rs.APP_NAME)
        con.execute("UPDATE research_products SET meta_ads = 0 WHERE item_id = ?", (item_id,))
        for h, pp in per_product.items():
            con.execute("UPDATE research_products SET meta_ads = ?, meta_first = ? WHERE item_id = ? AND handle = ?",
                        (pp["ads"], pp["first"], item_id, h))
        con.execute("UPDATE research_runs SET status = 'done', apify_run = ?, ads = ?, capped = ?, cost_usd = ?,"
                    " finished_at = ? WHERE id = ?", (apify_run, len(live), 1 if capped else 0, cost, now, run_id))
        sig = merge_signals(it, {"names": [p["name"] for p in pages.values() if p["name"]],
                                 "texts": [a["text"] for a in live][:20], "ctas": [a["cta"] for a in live][:20],
                                 "formats": [a["format"] for a in live][:20], "n_pages": len(pages)})
        con.execute("UPDATE research_items SET ad_signals = ? WHERE id = ?", (json.dumps(sig), item_id))
    try:
        relabel(item_id)
    except Exception:
        pass


# ---------------------------------------------------------------- the hunt

def _stage(hid: int, text: str, **fields):
    sets, vals = ["stage = ?"], [text]
    for k, v in fields.items():
        sets.append(f"{k} = ?"); vals.append(v)
    with db._conn() as con:
        con.execute(f"UPDATE research_hunts SET {', '.join(sets)} WHERE id = ?", (*vals, hid))


async def hunt(hid: int):
    ensure()
    with db._conn() as con:
        h = dict(con.execute("SELECT * FROM research_hunts WHERE id = ?", (hid,)).fetchone())
    countries = json.loads(h["countries"])
    token = db.get_setting("apify_token")
    total_cost = 0.0
    try:
        _hunting.add(hid)
        if not token:
            raise apify.ApifyError("Paste the Apify key first (Apify button).")
        # 1 discover
        _stage(hid, f"1/5 Searching Meta's Ad Library for “{h['keyword']}” in {', '.join(countries)}…")
        urls = [{"url": library_url(h["keyword"], c, "image", h["match"])} for c in countries]
        payload = {**apify.page_input(urls[0]["url"], "ALL", h["cap"]), "urls": urls,
                   "count": h["cap"] * len(urls), "limitPerSource": h["cap"]}
        async with rs._sem:
            run, raw = await apify.run_actor(token, payload)
        total_cost += apify.run_cost(run) or 0
        ads = [ad_info(r) for r in raw if not r.get("error")]
        stores = {}
        for a in ads:
            if not a["domain"]:
                continue
            s = stores.setdefault(a["domain"], {"names": set(), "pages": set(), "texts": [], "ctas": [], "formats": [],
                                                "countries": set(), "ads": 0})
            s["ads"] += 1
            if a["page_name"]:
                s["names"].add(a["page_name"])
            if a["page_id"]:
                s["pages"].add(a["page_id"])
            s["texts"].append(a["text"]); s["ctas"].append(a["cta"]); s["formats"].append(a["format"])
            if a["country"]:
                s["countries"].add(a["country"])
        _stage(hid, f"2/5 Found {len(ads)} ads from {len(stores)} stores. Checking which are Shopify dropshippers…",
               ads=len(ads), stores=len(stores), cost_usd=round(total_cost, 4))
        # 2-3 qualify + bestsellers (free)
        with db._conn() as con:
            seen = {r[0]: dict(r) for r in con.execute("SELECT * FROM research_seen")}
            known = {r["domain"]: r["id"] for r in con.execute("SELECT id, domain FROM research_items WHERE domain != ''")}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).isoformat()
        sem = asyncio.Semaphore(6)
        found_by = f"“{h['keyword']}”"
        results = {"shopify": 0, "qualified": 0, "new": 0}
        new_ids = []

        async def one(dom, s):
            sn = seen.get(dom)
            if sn and sn["checked_at"] > cutoff and not sn["shopify"]:
                return                                  # not Shopify, checked recently
            async with sem:
                item_id = known.get(dom)
                is_new = item_id is None
                store = None
                if is_new:
                    store = await read_store(dom)
                    with db._conn() as con:
                        con.execute("INSERT OR REPLACE INTO research_seen (domain, shopify, item_id, checked_at) VALUES (?,?,?,?)",
                                    (dom, 1 if store["shopify"] else 0, None, rs._now()))
                    if not store["shopify"]:
                        return
                    name = sorted(s["names"], key=len)[0] if s["names"] else dom
                    item = rs.add_item({"site_url": f"https://{dom}", "name": name, "niche": "Fashion"}, rs.APP_NAME, "app")
                    item_id = item["id"]
                    known[dom] = item_id
                    results["new"] += 1
                    new_ids.append(item_id)
                with db._conn() as con:
                    con.execute("UPDATE research_items SET found_by = ? WHERE id = ?",
                                (f"{found_by} · {', '.join(sorted(s['countries'])) or ', '.join(countries)}", item_id))
                if not is_new and sn and sn["checked_at"] > cutoff:
                    results["shopify"] += 1
                    results["qualified"] += rs.get_item(item_id).get("qualified") or 0
                    return
                res = await analyse(item_id, {"names": list(s["names"]), "texts": s["texts"], "ctas": s["ctas"],
                                              "formats": s["formats"], "n_pages": len(s["pages"])}, store)
                results["shopify"] += 1 if res.get("shopify") else 0
                results["qualified"] += 1 if res.get("qualified") else 0

        await asyncio.gather(*(one(d, s) for d, s in stores.items()))
        _stage(hid, f"3/5 {results['shopify']} Shopify stores, {results['qualified']} look like dropshippers "
                    f"({results['new']} new). Reading their Meta ads…",
               shopify=results["shopify"], qualified=results["qualified"], new_stores=results["new"])
        # 4 deep check the best new qualified stores
        with db._conn() as con:
            deep = [r[0] for r in con.execute(
                f"SELECT id FROM research_items WHERE qualified = 1 AND id IN ({','.join('?' * len(new_ids)) or 'NULL'})"
                " ORDER BY ds_score DESC LIMIT ?", (*new_ids, h["deep_max"]))]
        if deep:
            for i in range(0, len(deep), 10):
                res = await deep_check(deep[i:i + 10], rs.APP_NAME)
                total_cost += res.get("cost_usd") or 0
        # 5 labels
        mine = [known[d] for d in stores if d in known]
        with db._conn() as con:
            nug = con.execute(f"SELECT COUNT(*) FROM research_products WHERE label = 'nugget' AND item_id IN "
                              f"({','.join('?' * len(mine)) or 'NULL'})", tuple(mine)).fetchone()[0]
        _stage(hid, "Done", status="done", deep=len(deep), nuggets=nug, cost_usd=round(total_cost, 4),
               finished_at=rs._now())
    except Exception as e:
        msg = str(e) if isinstance(e, apify.ApifyError) else f"Something went wrong: {e}"
        _stage(hid, "Stopped", status="error", error=msg[:500], cost_usd=round(total_cost, 4), finished_at=rs._now())
    finally:
        _hunting.discard(hid)


def start_hunt(keyword: str, countries: list, cap: int, match: str, deep_max: int, by_name: str) -> int:
    ensure()
    with db._conn() as con:
        hid = con.execute("INSERT INTO research_hunts (keyword, countries, cap, match, deep_max, status, stage, by_name,"
                          " started_at) VALUES (?,?,?,?,?,'running','Starting…',?,?)",
                          (keyword, json.dumps(countries), cap, match, deep_max, by_name, rs._now())).lastrowid
    asyncio.create_task(hunt(hid))
    return hid


async def watch():
    """Free: read every qualified store's bestsellers again, keep rank history, relabel."""
    ensure()
    with db._conn() as con:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM research_items WHERE archived = 0 AND is_shopify = 1 AND domain != ''"
            " AND (qualified = 1 OR by_kind = 'person') ORDER BY id LIMIT 300")]
    sem = asyncio.Semaphore(4)

    async def one(i):
        async with sem:
            try:
                await analyse(i)
            except Exception as e:
                print(f"Research watch {i}: {e}")
    await asyncio.gather(*(one(i) for i in ids))
    return len(ids)


async def loop():
    """Every 10 min: the daily free bestseller watch (HK morning), and the optional keyword rotation."""
    await asyncio.sleep(300)
    while True:
        try:
            c = config()
            now = datetime.now(HK)
            today = now.date().isoformat()
            if now.hour >= 6 and c["watch_last"] != today:
                c["watch_last"] = today
                save_config(c)
                await watch()
                c = config()
            if c["auto"] and db.get_setting("apify_token") and now.hour >= c["auto_hour"] and c["auto_last"] != today \
                    and c["keywords"]:
                kw = c["keywords"][c["auto_next"] % len(c["keywords"])]
                c["auto_last"], c["auto_next"] = today, c["auto_next"] + 1
                save_config(c)
                start_hunt(kw, c["countries"], c["cap"], c["match"], c["deep_max"], rs.APP_NAME)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Research loop failed: {e}")
        await asyncio.sleep(600)


# ---------------------------------------------------------------- routes

@router.get("/api/research/hunt")
def api_hunt_info():
    ensure()
    c = config()
    with db._conn() as con:
        hunts = [dict(r) for r in con.execute("SELECT * FROM research_hunts ORDER BY id DESC LIMIT 20")]
    for h in hunts:
        h["countries"] = json.loads(h["countries"] or "[]")
    return {"config": c, "countries": [{"code": k, "name": n} for k, n in COUNTRIES], "hunts": hunts,
            "running": any(h["status"] == "running" for h in hunts), "price_per_ad": PRICE_PER_AD, "deep_cap": DEEP_CAP,
            "apify": bool(db.get_setting("apify_token")),
            "rules": {"qualify_days": QUALIFY_DAYS, "max_store_days": MAX_STORE_DAYS, "qualify_score": QUALIFY_SCORE,
                      "compare_share": COMPARE_SHARE}}


@router.post("/api/research/hunt")
async def api_hunt(payload: dict, request: Request):
    rs._level(request, "manage")
    kw = (payload.get("keyword") or "").strip()
    if not kw:
        raise HTTPException(400, "Type or pick a keyword.")
    codes = {k for k, _ in COUNTRIES}
    countries = [c for c in payload.get("countries") or [] if c in codes]
    if not countries:
        raise HTTPException(400, "Tick at least one country.")
    cap = max(10, min(int(payload.get("cap") or 40), 200))
    deep_max = max(0, min(int(payload.get("deep_max") if payload.get("deep_max") is not None else 15), 40))
    match = "any" if payload.get("match") == "any" else "phrase"
    if not db.get_setting("apify_token"):
        raise HTTPException(400, "Connect Apify first (Apify button).")
    if _hunting:
        raise HTTPException(400, "A search is already running; wait for it to finish.")
    c = config()
    if kw.lower() not in [k.lower() for k in c["keywords"]]:
        c["keywords"].append(kw)
    c.update({"countries": countries, "cap": cap, "match": match, "deep_max": deep_max})
    save_config(c)
    hid = start_hunt(kw, countries, cap, match, deep_max, rs._who(request))
    return {"ok": True, "id": hid, "estimate_usd": estimate(len(countries), cap, deep_max)}


@router.put("/api/research/hunt/settings")
def api_hunt_settings(payload: dict, request: Request):
    rs._level(request, "manage")
    c = config()
    if "keywords" in payload:
        kws, out = [str(k).strip() for k in payload["keywords"] or [] if str(k).strip()], []
        for k in kws:
            if k.lower() not in [x.lower() for x in out]:
                out.append(k[:60])
        c["keywords"] = out
    if "auto" in payload:
        c["auto"] = bool(payload["auto"])
    if "auto_hour" in payload:
        c["auto_hour"] = max(0, min(int(payload["auto_hour"]), 23))
    save_config(c)
    return {"ok": True, "config": c}


@router.post("/api/research/items/{item_id}/analyse")
async def api_analyse(item_id: int, request: Request):
    """Free: Shopify check, store age, dropshipper score, bestsellers, labels."""
    rs._level(request, "work")
    if item_id in _analysing:
        return {"ok": True, "running": True}
    _analysing.add(item_id)
    try:
        return await analyse(item_id)
    finally:
        _analysing.discard(item_id)


@router.get("/api/research/debug-store")
async def api_debug_store(domain: str, request: Request):
    """Owner check: what the server gets back from a store (direct, then Apify Proxy)."""
    rs._owner(request)
    s = await read_store(rs.domain_of(domain))
    return {"shopify": s["shopify"], "blocked": s["blocked"], "via": s["via"], "products": len(s["products"]),
            "since": s["since"], "bestsellers": s["best"][:10], "log": s["log"]}


@router.get("/api/research/products")
def api_products(label: str = "", item: int = 0, q: str = ""):
    ensure()
    where, args = ["i.archived = 0"], []
    if label:
        labs = label.split(",")
        where.append(f"p.label IN ({','.join('?' * len(labs))})"); args += labs
    else:
        where.append("p.label IS NOT NULL")
    if item:
        where.append("p.item_id = ?"); args.append(item)
    if q.strip():
        where.append("(lower(p.title) LIKE ? OR lower(i.name) LIKE ? OR lower(i.domain) LIKE ?)")
        args += [f"%{q.strip().lower()}%"] * 3
    order = "CASE p.label WHEN 'nugget' THEN 0 WHEN 'compete' THEN 1 WHEN 'proven' THEN 2 WHEN 'top' THEN 3 ELSE 4 END, p.rank"
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT p.*, i.name AS store, i.domain, i.store_since, i.found_by, i.best_unreliable FROM research_products p"
            f" JOIN research_items i ON i.id = p.item_id WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 1000", args)]
        counts = {r[0]: r[1] for r in con.execute(
            "SELECT p.label, COUNT(*) FROM research_products p JOIN research_items i ON i.id = p.item_id"
            " WHERE i.archived = 0 AND p.label IS NOT NULL GROUP BY p.label")}
    today = datetime.now(HK).date()
    for r in rows:
        r["age_days"] = _days(r["created_at"] or "", today)
        r.pop("history", None)
    return {"products": rows, "counts": counts}
