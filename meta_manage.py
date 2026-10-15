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


async def structure(token: str, account_id: str, currency: str) -> dict:
    """{campaigns: [...], adsets: [...], ads: [...]} with status, budgets (account currency) and parents.
    Deleted / archived things are left out."""
    act = f"act_{str(account_id).removeprefix('act_')}"
    keep = json.dumps([{"field": "effective_status", "operator": "NOT_IN", "value": ["DELETED", "ARCHIVED"]}])
    async with httpx.AsyncClient(timeout=60) as client:
        camps = await meta._get_all(client, f"{act}/campaigns", token, {
            "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,bid_strategy,created_time",
            "filtering": keep, "limit": 200})
        sets = await meta._get_all(client, f"{act}/adsets", token, {
            "fields": "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,optimization_goal,created_time",
            "filtering": keep, "limit": 300})
        ads = await meta._get_all(client, f"{act}/ads", token, {
            "fields": "id,name,adset_id,campaign_id,status,effective_status,created_time,creative{thumbnail_url}",
            "filtering": keep, "limit": 500})
    c_name = {c["id"]: c["name"] for c in camps}
    s_name = {s["id"]: s["name"] for s in sets}
    return {
        "campaigns": [{"id": c["id"], "name": c["name"], "status": c.get("status"), "effective": c.get("effective_status"),
                       "objective": c.get("objective"), "daily_budget": _money(c.get("daily_budget"), currency),
                       "lifetime_budget": _money(c.get("lifetime_budget"), currency), "created": c.get("created_time")}
                      for c in camps],
        "adsets": [{"id": s["id"], "name": s["name"], "campaign_id": s.get("campaign_id"), "campaign": c_name.get(s.get("campaign_id"), ""),
                    "status": s.get("status"), "effective": s.get("effective_status"),
                    "daily_budget": _money(s.get("daily_budget"), currency),
                    "lifetime_budget": _money(s.get("lifetime_budget"), currency),
                    "goal": s.get("optimization_goal"), "created": s.get("created_time")} for s in sets],
        "ads": [{"id": a["id"], "name": a["name"], "adset_id": a.get("adset_id"), "adset": s_name.get(a.get("adset_id"), ""),
                 "campaign_id": a.get("campaign_id"), "campaign": c_name.get(a.get("campaign_id"), ""),
                 "status": a.get("status"), "effective": a.get("effective_status"), "created": a.get("created_time"),
                 "thumb": ((a.get("creative") or {}).get("thumbnail_url"))} for a in ads],
    }


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
            "level": level, "limit": 500, "time_range": json.dumps({"since": since, "until": until}),
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
