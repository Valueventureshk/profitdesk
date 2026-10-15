"""Slack (the NF Ecom workspace): the ops agent's eyes and voice.

A Slack app made by the owner in their workspace ("EcomVital Agent", from the manifest the Agent desk shows) gives a
bot token (xoxb-...) and a signing secret, pasted on the Agent desk. The bot reads the channels it's invited to
(suppliers, supply chain, customer support, EOD), downloads files (supplier invoices), and posts its work log.
Events arrive at /api/slack/events, signed with the signing secret.

Free Slack keeps only the last 90 days, so EcomVital keeps its own copy of every message it reads (agent.py).
"""
import asyncio
import hashlib
import hmac
import time

import httpx

API = "https://slack.com/api"

# what the app needs (also in the manifest the desk shows)
BOT_SCOPES = ["channels:history", "channels:read", "groups:history", "groups:read", "files:read", "users:read",
              "chat:write", "im:history", "im:read", "im:write", "app_mentions:read", "reactions:write"]
BOT_EVENTS = ["message.channels", "message.groups", "message.im", "app_mention"]


class SlackError(RuntimeError):
    pass


def verify(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Slack signs every request: v0=HMAC-SHA256(secret, "v0:<timestamp>:<body>"); older than 5 minutes = refused."""
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (TypeError, ValueError):
        return False
    base = b"v0:" + str(timestamp).encode() + b":" + body
    mine = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mine, signature or "")


_EXPLAIN = {
    "invalid_auth": "Slack didn't accept the bot token. Copy the whole Bot User OAuth Token (xoxb-…) again.",
    "not_authed": "No Slack bot token yet.",
    "account_inactive": "The Slack app was removed or its token revoked. Reinstall it and paste the new token.",
    "not_in_channel": "The agent isn't in that channel: type /invite @EcomVital Agent in it.",
    "channel_not_found": "The agent can't see that channel: type /invite @EcomVital Agent in it.",
    "missing_scope": "The Slack app is missing a permission. Update it from the manifest on the Agent desk and reinstall.",
}


async def call(token: str, method: str, params: dict = None, post: bool = False) -> dict:
    """One Slack Web API call; waits and retries when Slack says slow down."""
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(5):
            try:
                if post:
                    r = await client.post(f"{API}/{method}", json=params or {}, headers={"Authorization": f"Bearer {token}"})
                else:
                    r = await client.get(f"{API}/{method}", params=params or {}, headers={"Authorization": f"Bearer {token}"})
            except httpx.RequestError as e:
                raise SlackError(f"Couldn't reach Slack ({type(e).__name__}).") from e
            if r.status_code == 429:
                await asyncio.sleep(min(int(r.headers.get("Retry-After", "5")), 60))
                continue
            body = r.json()
            if not body.get("ok"):
                err = body.get("error", "unknown")
                if err == "ratelimited":
                    await asyncio.sleep(5)
                    continue
                raise SlackError(_EXPLAIN.get(err, f"Slack said: {err}") + (f" (needs {body['needed']})" if body.get("needed") else ""))
            return body
    raise SlackError("Slack kept asking to slow down; try again in a minute.")


async def pages(token: str, method: str, params: dict, key: str, limit: int = 20000) -> list:
    """Follow Slack's cursors and collect one list from every page."""
    out, cursor = [], None
    while True:
        p = {**params, **({"cursor": cursor} if cursor else {})}
        body = await call(token, method, p)
        out += body.get(key) or []
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor or len(out) >= limit:
            return out


async def whoami(token: str) -> dict:
    b = await call(token, "auth.test")
    return {"team": b.get("team"), "team_id": b.get("team_id"), "bot_user": b.get("user_id"), "bot_name": b.get("user"),
            "url": b.get("url")}


async def channels(token: str) -> list:
    """Channels the bot is a member of (public and private)."""
    return await pages(token, "users.conversations", {"types": "public_channel,private_channel", "limit": 200,
                                                       "exclude_archived": "true"}, "channels")


async def users(token: str) -> list:
    return await pages(token, "users.list", {"limit": 200}, "members")


async def history(token: str, channel: str, oldest: str) -> list:
    """Every top-level message in a channel since `oldest` (unix seconds as text), oldest first."""
    rows = await pages(token, "conversations.history", {"channel": channel, "oldest": oldest, "limit": 200}, "messages")
    return sorted(rows, key=lambda m: float(m.get("ts") or 0))


async def replies(token: str, channel: str, ts: str) -> list:
    rows = await pages(token, "conversations.replies", {"channel": channel, "ts": ts, "limit": 200}, "messages")
    return [m for m in rows if m.get("ts") != ts]


async def download(token: str, url: str, max_bytes: int = 25 * 1024 * 1024) -> bytes:
    """A file's bytes (url_private needs the bot token)."""
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200 or r.headers.get("content-type", "").startswith("text/html"):
        raise SlackError("Slack didn't hand over the file (the app needs files:read, and to be in the channel).")
    if len(r.content) > max_bytes:
        raise SlackError("That file is too big.")
    return r.content


async def post(token: str, channel: str, text: str, thread_ts: str = None) -> str:
    b = await call(token, "chat.postMessage", {"channel": channel, "text": text[:3900],
                                               **({"thread_ts": thread_ts} if thread_ts else {}), "unfurl_links": False},
                   post=True)
    return b.get("ts")


def manifest(base_url: str) -> dict:
    """The Slack app to create (api.slack.com/apps → Create New App → From a manifest)."""
    return {
        "display_information": {"name": "EcomVital Agent", "description": "EcomVital's ops agent: reads supplier, "
                                "supply-chain and support channels, files invoices, reports its work.",
                                "background_color": "#14181f"},
        "features": {"bot_user": {"display_name": "EcomVital Agent", "always_online": True},
                     "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}},
        "oauth_config": {"scopes": {"bot": BOT_SCOPES}},
        "settings": {"event_subscriptions": {"request_url": f"{base_url}/api/slack/events", "bot_events": BOT_EVENTS},
                     "org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False},
    }
