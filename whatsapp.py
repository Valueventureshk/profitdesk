"""WhatsApp (Meta's WhatsApp Business Platform, Cloud API): the owner and team chat with ProfitDesk's assistant.

Settings (pasted by the owner in Settings → WhatsApp, never in chat):
    wa_token        System user access token (whatsapp_business_messaging + whatsapp_business_management)
    wa_phone_id     Phone number ID of ProfitDesk's WhatsApp number
    wa_waba_id      WhatsApp Business Account ID
    wa_app_secret   The Meta app's secret, to check that webhook calls really come from Meta
    wa_verify       Verify token for the webhook (ProfitDesk makes it)
    wa_people       JSON [{"phone": "85291234567", "email": "user@..."}]: who may use it, as which ProfitDesk login

Replies to a person's message are free text for 24 hours after their last message; outside that window Meta
only allows approved templates, so reports and alerts then go as the "profitdesk_update" template (a short
heads-up) and the full text follows when they reply.
"""
import hashlib
import hmac
import json
import re

import httpx

import db
from meta_ads_client import GRAPH, _version

TEMPLATE = "profitdesk_update"


class WAError(RuntimeError):
    pass


def cfg() -> dict:
    try:
        people = json.loads(db.get_setting("wa_people") or "[]")
    except ValueError:
        people = []
    return {"token": db.get_setting("wa_token") or "", "phone_id": db.get_setting("wa_phone_id") or "",
            "waba_id": db.get_setting("wa_waba_id") or "", "app_secret": db.get_setting("wa_app_secret") or "",
            "verify": db.get_setting("wa_verify") or "", "people": people}


def ready() -> bool:
    c = cfg()
    return bool(c["token"] and c["phone_id"])


def norm_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


def person(phone: str):
    """The ProfitDesk login a WhatsApp number belongs to, or None (then the message is ignored)."""
    p = norm_phone(phone)
    return next((x for x in cfg()["people"] if norm_phone(x.get("phone")) == p), None)


def phone_of(email: str):
    return next((norm_phone(x["phone"]) for x in cfg()["people"] if x.get("email") == email), None)


def signature_ok(body: bytes, header: str) -> bool:
    secret = cfg()["app_secret"]
    if not secret:
        return False
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header or "")


async def _post(path: str, payload: dict) -> dict:
    c = cfg()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{GRAPH}/{_version()}/{path}", json=payload, headers={"Authorization": f"Bearer {c['token']}"})
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        err = body.get("error") or {}
        raise WAError(f"{err.get('code', r.status_code)}: {err.get('message', r.text[:200])}")
    return body


def _chunks(text: str, size: int = 3800) -> list:
    out, cur = [], ""
    for para in (text or "").split("\n"):
        if len(cur) + len(para) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        out.append(cur)
    return out or ["(empty)"]


def wa_text(md: str) -> str:
    """The assistant's Markdown, made readable in WhatsApp (bold *x*, tables as lines)."""
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", md or "")
    lines = []
    for l in t.split("\n"):
        if re.match(r"^\s*\|?\s*:?-{2,}", l):
            continue
        if l.strip().startswith("|"):
            l = " · ".join(c.strip() for c in l.strip().strip("|").split("|"))
        lines.append(l)
    return "\n".join(lines)


async def send_text(to: str, text: str) -> list:
    c = cfg()
    ids = []
    for part in _chunks(wa_text(text)):
        got = await _post(f"{c['phone_id']}/messages", {"messaging_product": "whatsapp", "to": norm_phone(to), "type": "text",
                                                        "text": {"body": part, "preview_url": False}})
        ids += [m.get("id") for m in got.get("messages") or []]
    return ids


async def send_template(to: str, title: str, lang: str = "en") -> list:
    c = cfg()
    got = await _post(f"{c['phone_id']}/messages", {
        "messaging_product": "whatsapp", "to": norm_phone(to), "type": "template",
        "template": {"name": TEMPLATE, "language": {"code": lang},
                     "components": [{"type": "body", "parameters": [{"type": "text", "text": title[:200]}]}]}})
    return [m.get("id") for m in got.get("messages") or []]


async def ensure_template() -> str:
    """Ask Meta to approve ProfitDesk's one template (for reports / alerts sent outside the 24-hour window)."""
    c = cfg()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{GRAPH}/{_version()}/{c['waba_id']}/message_templates", params={"name": TEMPLATE},
                             headers={"Authorization": f"Bearer {c['token']}"})
    have = (r.json().get("data") or []) if r.status_code < 400 else []
    if have:
        return have[0].get("status", "?")
    got = await _post(f"{c['waba_id']}/message_templates", {
        "name": TEMPLATE, "language": "en", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "You have a new ProfitDesk update: {{1}}. Reply to this message to see it in full.",
                        "example": {"body_text": [["Daily performance report"]]}}]})
    return got.get("status", "PENDING")


async def mark_read(message_id: str):
    c = cfg()
    try:
        await _post(f"{c['phone_id']}/messages", {"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except WAError:
        pass


async def media(media_id: str) -> tuple:
    """(bytes, mime type) of a photo / document someone sent."""
    c = cfg()
    h = {"Authorization": f"Bearer {c['token']}"}
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(f"{GRAPH}/{_version()}/{media_id}", headers=h)
        if r.status_code >= 400:
            raise WAError(f"Couldn't find the file ({r.status_code}).")
        info = r.json()
        f = await client.get(info["url"], headers=h)
        if f.status_code >= 400:
            raise WAError(f"Couldn't download the file ({f.status_code}).")
    return f.content, info.get("mime_type") or "application/octet-stream"


async def setup(app_id: str) -> list:
    """Finish Meta's side by API once the token + app secret are saved: register the number for Cloud API (with a
    two-step PIN ProfitDesk keeps), subscribe the app to the WhatsApp account, and point the app's webhook at
    ProfitDesk. Returns what happened, step by step."""
    import secrets
    c = cfg()
    out = []
    pin = db.get_setting("wa_pin")
    if not pin:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
        db.set_setting("wa_pin", pin)
    try:
        await _post(f"{c['phone_id']}/register", {"messaging_product": "whatsapp", "pin": pin})
        out.append("Phone number registered for the API")
    except WAError as e:
        out.append(f"Phone number: {e}" if "already" not in str(e).lower() else "Phone number already registered")
    try:
        await _post(f"{c['waba_id']}/subscribed_apps", {})
        out.append("App subscribed to the WhatsApp account")
    except WAError as e:
        out.append(f"Subscribe app: {e}")
    if app_id and c["app_secret"] and c["verify"]:
        import os
        base = os.getenv("PUBLIC_URL", "").rstrip("/") or "https://profitdesk-production.up.railway.app"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{GRAPH}/{_version()}/{app_id}/subscriptions", data={
                "object": "whatsapp_business_account", "callback_url": f"{base}/api/whatsapp/webhook",
                "verify_token": c["verify"], "fields": "messages", "access_token": f"{app_id}|{c['app_secret']}"})
        out.append("Webhook set on the Meta app" if r.status_code < 300 else f"Webhook: {r.text[:200]}")
    return out


async def diagnose(app_id: str) -> dict:
    """What Meta says about the setup: the number (registered? quality), apps subscribed to the WhatsApp account,
    the app's webhook subscription, and the message template."""
    c = cfg()
    h = {"Authorization": f"Bearer {c['token']}"}
    out = {}
    async with httpx.AsyncClient(timeout=30) as client:
        async def get(path, params=None, auth=h):
            r = await client.get(f"{GRAPH}/{_version()}/{path}", params=params or {}, headers=auth or {})
            try:
                return r.json()
            except ValueError:
                return {"raw": r.text[:300]}
        out["number"] = await get(c["phone_id"], {"fields": "display_phone_number,verified_name,code_verification_status,platform_type,status,quality_rating,name_status"})
        out["subscribed_apps"] = await get(f"{c['waba_id']}/subscribed_apps")
        if app_id and c["app_secret"]:
            out["app_webhook"] = await get(f"{app_id}/subscriptions", {"access_token": f"{app_id}|{c['app_secret']}"}, auth=None)
        out["templates"] = await get(f"{c['waba_id']}/message_templates", {"fields": "name,status,language"})
    return out
