"""Product Importer → Ad copy: paste a Meta ad link (or a screenshot of the ad) and get its primary text, headline and
description back, each improvable with AI.

From a link: Meta's Ad Library API with each connected Meta token (the archived ad by its id), then the ad's rendered
preview read by the AI. Meta often refuses both (Ad Library access needs an identity-confirmed account, and the public page
blocks servers), so a screenshot of the ad always works: the AI reads the text off the picture.
"""
import base64
import json
import re
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import APIRouter, HTTPException, Request

import db
import meta_ads_client as meta

router = APIRouter()
READ_MODEL = "claude-haiku-5-5"
WRITE_MODEL = "claude-sonnet-5-5"
FIELDS = ("primary_text", "headline", "description")

SCHEMA = """
CREATE TABLE IF NOT EXISTS adcopy_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT,
    ad_id        TEXT,
    source       TEXT,               -- ad library | preview | screenshot | typed
    page_name    TEXT,
    primary_text TEXT,
    headline     TEXT,
    description  TEXT,
    by           TEXT,
    at           TEXT
);
"""
_ready = {"ok": False}

COPY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["found", "page_name", "primary_text", "headline", "description"],
               "properties": {"found": {"type": "boolean"}, "page_name": {"type": "string"}, "primary_text": {"type": "string"},
                              "headline": {"type": "string"}, "description": {"type": "string"}}}
READ_SYSTEM = """You read a Facebook / Instagram ad and return its text exactly as written (keep line breaks and emojis):
- primary_text: the main text above the image / video.
- headline: the bold line under the image, next to the button.
- description: the smaller line under the headline (often empty).
- page_name: the advertiser's page name.
found = false if this isn't an ad or you can't read it. Never invent text."""
IMPROVE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["primary_text", "headline", "description", "notes"],
                  "properties": {"primary_text": {"type": "string"}, "headline": {"type": "string"}, "description": {"type": "string"},
                                 "notes": {"type": "string"}}}
IMPROVE_SYSTEM = """You are a senior direct-response copywriter for Shopify stores selling on Meta (Facebook / Instagram).
Rewrite the ad copy you're given so it sells better for OUR store: same product idea and offer, stronger hook in the first line,
clear benefit, simple words, short lines, a reason to act now, and a fitting call to action. Never copy the competitor's brand or
page name, never invent claims (medical, guarantees, discounts) that aren't in the original, and keep Meta's ad rules in mind
(no "you" + personal attributes like health conditions in a way Meta rejects).
Limits: headline ≤ 40 characters, description ≤ 30 characters, primary text ≤ 125 characters before the "See more" cut
(can continue after a line break). Only rewrite the fields you're asked to; return the others unchanged.
notes: one short line on what you changed."""


def _db():
    if not _ready["ok"]:
        with db._conn() as con:
            con.executescript(SCHEMA)
        _ready["ok"] = True


def _key() -> str:
    k = db.get_setting("anthropic_api_key")
    if not k:
        raise HTTPException(400, "Add the AI key first (Settings → AI).")
    return k


def _ad_id(url: str) -> str:
    m = re.search(r"[?&]id=(\d{6,})", url or "") or re.search(r"/ads/library/[^\d]*(\d{8,})", url or "") or re.fullmatch(r"\s*(\d{8,})\s*", url or "")
    return m.group(1) if m else ""


async def _ask(messages: list, system: str, schema: dict, model: str, max_tokens=1500) -> dict:
    client = anthropic.AsyncAnthropic(api_key=_key(), max_retries=2, timeout=120)
    try:
        resp = await client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                                            messages=messages)
    except anthropic.APIError as e:
        raise HTTPException(400, f"The AI couldn't answer ({type(e).__name__}). Try again in a minute.")
    if resp.stop_reason == "refusal":
        raise HTTPException(400, "The AI declined this one.")
    return json.loads(next((b.text for b in resp.content if b.type == "text"), "{}"))


async def _from_library(ad_id: str) -> dict:
    """The archived ad through Meta's Ad Library API, with each connected Meta token."""
    tried = []
    for conn in db.list_meta_connections():
        token = conn.get("access_token")
        if not token:
            continue
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                got = await meta._get(client, ad_id, token, {"fields": "page_name,ad_creative_bodies,ad_creative_link_titles,"
                                                                       "ad_creative_link_descriptions,ad_snapshot_url"})
                bodies = got.get("ad_creative_bodies") or []
                if bodies or got.get("ad_creative_link_titles"):
                    return {"source": "ad library", "page_name": got.get("page_name") or "", "primary_text": bodies[0] if bodies else "",
                            "headline": (got.get("ad_creative_link_titles") or [""])[0],
                            "description": (got.get("ad_creative_link_descriptions") or [""])[0]}
            except meta.MetaAdsError as e:
                tried.append(str(e)[:160])
            try:                                       # the ad's rendered preview, read by the AI
                r = await client.get("https://www.facebook.com/ads/archive/render_ad/", params={"id": ad_id, "access_token": token},
                                     headers={"User-Agent": "Mozilla/5.0"})
                login_wall = re.search(r"must log in|log in to facebook|log into facebook", r.text[:20000], re.I)
                if r.status_code == 200 and len(r.text) > 500 and not login_wall:
                    text = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", r.text, flags=re.S))[:12000]
                    got = await _ask([{"role": "user", "content": f"The ad's preview page as text:\n{text}"}], READ_SYSTEM, COPY_SCHEMA, READ_MODEL)
                    if got.get("found"):
                        return {"source": "preview", **{k: got.get(k) or "" for k in ("page_name",) + FIELDS}}
                tried.append("preview: Meta asked to log in" if login_wall else f"preview {r.status_code}")
            except httpx.HTTPError as e:
                tried.append(f"preview {type(e).__name__}")
    raise HTTPException(400, "Meta didn't share this ad's text with the app. Take a screenshot of the ad (the whole ad: text, "
                             "picture and the headline under it) and paste or drop it below; the AI reads it.")


def _log(row: dict, by: str) -> int:
    _db()
    with db._conn() as con:
        return con.execute("INSERT INTO adcopy_log (url, ad_id, source, page_name, primary_text, headline, description, by, at)"
                           " VALUES (?,?,?,?,?,?,?,?,?)",
                           (row.get("url"), row.get("ad_id"), row.get("source"), row.get("page_name"), row.get("primary_text"),
                            row.get("headline"), row.get("description"), by,
                            datetime.now(timezone.utc).isoformat(timespec="seconds"))).lastrowid


def _who(request: Request) -> str:
    return ((getattr(request.state, "user", None) or {}).get("email") or "")


@router.post("/api/importer/adcopy/read")
async def read_link(payload: dict, request: Request):
    url = str(payload.get("url") or "").strip()
    ad_id = _ad_id(url)
    if not ad_id:
        raise HTTPException(400, "Paste a Meta Ad Library link, like https://www.facebook.com/ads/library/?id=123456789. "
                                 "For any other ad, paste a screenshot of it instead.")
    got = await _from_library(ad_id)
    row = {"url": url, "ad_id": ad_id, **got}
    row["id"] = _log(row, _who(request))
    return row


@router.post("/api/importer/adcopy/image")
async def read_image(request: Request):
    """A screenshot of the ad: the AI reads its text."""
    form = await request.form()
    up = form.get("file")
    if not up or not hasattr(up, "read"):
        raise HTTPException(400, "Add the screenshot.")
    data = await up.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "That picture is too big (5 MB max): crop it to just the ad.")
    kind = (up.content_type or "image/png").split(";")[0]
    if kind not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise HTTPException(400, "Use a PNG or JPG screenshot.")
    got = await _ask([{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": kind, "data": base64.b64encode(data).decode()}},
        {"type": "text", "text": "Read this ad."}]}], READ_SYSTEM, COPY_SCHEMA, WRITE_MODEL)
    if not got.get("found"):
        raise HTTPException(400, "The AI couldn't find an ad in that picture. Screenshot the whole ad, including the text above it.")
    row = {"url": str(form.get("url") or ""), "ad_id": _ad_id(str(form.get("url") or "")), "source": "screenshot",
           **{k: got.get(k) or "" for k in ("page_name",) + FIELDS}}
    row["id"] = _log(row, _who(request))
    return row


@router.post("/api/importer/adcopy/improve")
async def improve(payload: dict):
    """Rewrite one field or all three with AI, for our store (an optional note steers it)."""
    which = payload.get("which") if payload.get("which") in FIELDS else "all"
    copy = {k: str(payload.get(k) or "")[:3000] for k in FIELDS}
    if not any(copy.values()):
        raise HTTPException(400, "There's no copy to improve yet.")
    ask = (f"Rewrite only the {which.replace('_', ' ')}." if which != "all" else "Rewrite all three.")
    note = str(payload.get("note") or "").strip()[:500]
    product = str(payload.get("product") or "").strip()[:300]
    got = await _ask([{"role": "user", "content": json.dumps({"task": ask, "our_note": note, "our_product": product, "copy": copy}, ensure_ascii=False)}],
                     IMPROVE_SYSTEM, IMPROVE_SCHEMA, WRITE_MODEL, 2000)
    if which != "all":
        got = {**copy, which: got.get(which) or copy[which], "notes": got.get("notes", "")}
    return got


@router.get("/api/importer/adcopy/recent")
def recent():
    _db()
    with db._conn() as con:
        return {"items": [dict(r) for r in con.execute("SELECT * FROM adcopy_log ORDER BY id DESC LIMIT 15")]}
