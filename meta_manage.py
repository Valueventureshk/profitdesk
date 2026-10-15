"""Apps → Meta Ads: read a store's campaigns / ad sets / ads with their results, and make the few changes the
desk allows (pause / switch on, daily budget). Every change is first sent in Meta's validate-only mode, then for
real; app.py logs what it was before so it can be undone.

Results come from Meta's Insights (spend, impressions, link clicks, and Meta's own count of purchases / adds to
cart with their value) over ad-account days. The desk puts EcomVital's own pixel counts next to them (Tracking,
matched by the UTM names), so decisions can rest on either.
"""
import json

import httpx

import meta_ads_client as meta

LEVELS = ("campaign", "adset", "ad")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mads_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    kind       TEXT    NOT NULL,                  -- meta_rules.KINDS
    params     TEXT    NOT NULL,                  -- JSON
    enabled    INTEGER NOT NULL DEFAULT 0,
    last_run   TEXT,
    last_notes TEXT,                              -- JSON list: what it found last time
    created_at TEXT,
    created_by TEXT
);
CREATE TABLE IF NOT EXISTS mads_actions (         -- every proposal / change, with what it was before (Undo)
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    batch      TEXT    NOT NULL,                  -- one proposal / plan = one batch, done together
    source     TEXT    NOT NULL,                  -- rule:<id> | ai | manual | undo:<id>
    change     TEXT    NOT NULL,                  -- status | budget
    level      TEXT    NOT NULL,                  -- campaign | adset | ad
    obj_id     TEXT    NOT NULL,
    name       TEXT,
    to_value   TEXT,
    reason     TEXT,
    summary    TEXT,
    status     TEXT    NOT NULL DEFAULT 'proposed',   -- proposed | done | failed | rejected | undone
    before     TEXT,
    error      TEXT,
    created_at TEXT,
    decided_by TEXT,
    done_at    TEXT
);
CREATE INDEX IF NOT EXISTS mads_actions_store ON mads_actions (store_id, status);
CREATE TABLE IF NOT EXISTS mads_chat (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id   INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    who        TEXT,
    created_at TEXT
);
"""
_PURCHASE = ("offsite_conversion.fb_pixel_purchase", "omni_purchase", "purchase")
_ATC = ("offsite_conversion.fb_pixel_add_to_cart", "omni_add_to_cart", "add_to_cart")


def _money(minor, currency: str):
    """Meta budgets come in the account currency's smallest unit."""
    if minor in (None, "", "0", 0):
        return None
    zero = (currency or "").upper() in meta._ZERO_DECIMAL
    return float(minor) / (1 if zero else 100)


_FIELDS = {
    "campaign": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,created_time",
    "adset": "id,name,campaign_id,campaign{name},status,effective_status,daily_budget,lifetime_budget,optimization_goal,created_time",
    "ad": "id,name,adset_id,adset{name},campaign_id,campaign{name},status,effective_status,created_time,creative{thumbnail_url}",
}


def _shape(level: str, o: dict, currency: str) -> dict:
    out = {"id": o["id"], "name": o.get("name") or o["id"], "status": o.get("status"), "effective": o.get("effective_status"),
           "created": o.get("created_time")}
    if level != "ad":
        out["daily_budget"] = _money(o.get("daily_budget"), currency)
        out["lifetime_budget"] = _money(o.get("lifetime_budget"), currency)
    if level == "campaign":
        out["objective"] = o.get("objective")
    else:
        out["campaign_id"] = o.get("campaign_id")
        out["campaign"] = (o.get("campaign") or {}).get("name", "")
    if level == "adset":
        out["goal"] = o.get("optimization_goal")
    if level == "ad":
        out["adset_id"] = o.get("adset_id")
        out["adset"] = (o.get("adset") or {}).get("name", "")
        out["thumb"] = (o.get("creative") or {}).get("thumbnail_url")
    return out


async def objects(token: str, account_id: str, level: str, currency: str, also_ids=()) -> list:
    """One level's campaigns / ad sets / ads: everything running now, plus the given ids (what spent in the
    window, even if it's paused now). Big accounts have thousands of old ads, so never the whole list."""
    level = level if level in LEVELS else "campaign"
    act = f"act_{str(account_id).removeprefix('act_')}"
    running = json.dumps([{"field": "effective_status", "operator": "IN", "value": ["ACTIVE", "IN_PROCESS", "WITH_ISSUES", "PENDING_REVIEW"]}])
    async with httpx.AsyncClient(timeout=90) as client:
        rows = await meta._get_all(client, f"{act}/{level}s", token, {"fields": _FIELDS[level], "filtering": running, "limit": 100})
        have = {r["id"] for r in rows}
        rest = [i for i in dict.fromkeys(str(x) for x in also_ids if x) if i not in have]
        for k in range(0, len(rest), 50):                 # Meta reads up to 50 ids in one call
            got = await meta._get(client, "", token, {"ids": ",".join(rest[k:k + 50]), "fields": _FIELDS[level]})
            rows += [v for v in got.values() if isinstance(v, dict) and v.get("id")]
    return [_shape(level, r, currency) for r in rows]


def _count(items, names) -> float:
    for n in names:                                  # Meta repeats one event under several names: take the first
        for x in items or []:
            if x.get("action_type") == n:
                return float(x.get("value") or 0)
    return 0.0


async def insights(token: str, account_id: str, level: str, since: str, until: str) -> dict:
    """{id: {spend, impressions, clicks, purchases, value, atc}} per campaign / ad set / ad, ad-account days."""
    level = level if level in LEVELS else "campaign"
    act = f"act_{str(account_id).removeprefix('act_')}"
    async with httpx.AsyncClient(timeout=90) as client:
        rows = await meta._get_all(client, f"{act}/insights", token, {
            "level": level, "limit": 200, "time_range": json.dumps({"since": since, "until": until}),
            "fields": f"{level}_id,spend,impressions,inline_link_clicks,actions,action_values"})
    out = {}
    for r in rows:
        out[r.get(f"{level}_id")] = {
            "spend": float(r.get("spend") or 0), "impressions": int(r.get("impressions") or 0),
            "clicks": int(r.get("inline_link_clicks") or 0),
            "purchases": _count(r.get("actions"), _PURCHASE), "value": _count(r.get("action_values"), _PURCHASE),
            "atc": _count(r.get("actions"), _ATC)}
    return out


async def read(token: str, obj_id: str) -> dict:
    """An object's status and budget right now (for 'before', so Undo knows what to put back)."""
    async with httpx.AsyncClient(timeout=30) as client:
        return await meta._get(client, str(obj_id), token, {"fields": "id,name,status,daily_budget,lifetime_budget"})


async def _write(token: str, obj_id: str, data: dict, validate: bool):
    body = dict(data)
    if validate:
        body["execution_options"] = ["validate_only"]
    async with httpx.AsyncClient(timeout=30) as client:
        return await meta._post(client, str(obj_id), token, body)


async def set_status(token: str, obj_id: str, status: str, validate: bool = False):
    if status not in ("ACTIVE", "PAUSED"):
        raise meta.MetaAdsError("Status must be ACTIVE or PAUSED.")
    return await _write(token, obj_id, {"status": status}, validate)


async def set_budget(token: str, obj_id: str, amount: float, currency: str, validate: bool = False):
    if not amount or amount <= 0:
        raise meta.MetaAdsError("A daily budget must be more than 0.")
    return await _write(token, obj_id, {"daily_budget": meta.minor_units(amount, currency)}, validate)
