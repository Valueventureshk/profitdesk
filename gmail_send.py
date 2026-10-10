"""Send replies from the Inbox through each store mailbox's own Gmail.

Each support mailbox signs in with Google once, allowing one thing only: sending
email (gmail.send). Replies go out from the store's address, land in that
mailbox's Sent folder and sit in the same Gmail conversation as the customer's
email, exactly as if typed in Gmail. ProfitDesk then reads them back from Sent
like any other reply. (Railway's Hobby plan blocks normal SMTP sending.)
"""
import base64
import imaplib
import re
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import urlencode

import httpx

import google_ads_client as gads

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/userinfo.email"]
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class SendError(RuntimeError):
    pass


def auth_url(redirect_uri: str, state: str, address: str) -> str:
    cid, _, _, _ = gads._cfg()
    if not cid:
        raise SendError("The Google sign-in keys aren't set up yet (Settings → Google Ads).")
    return gads.AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "login_hint": address, "state": state,
    })


async def _token(refresh_token: str) -> str:
    cid, secret, _, _ = gads._cfg()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(gads.TOKEN_URL, data={"refresh_token": refresh_token, "client_id": cid,
                                                    "client_secret": secret, "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise SendError("This mailbox's Google sign-in has expired. Sign it in again in Settings → Support mailboxes.")
    return r.json()["access_token"]


def thread_id(address: str, password: str, message_id: str) -> str:
    """Gmail's conversation id for an email (found over IMAP), so the reply joins it."""
    if not message_id:
        return ""
    mid = message_id.strip().strip("<>")
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=30) as m:
            m.login(address, password)
            box = '"[Gmail]/All Mail"'
            typ, rows = m.list()
            for raw in rows or []:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                if "\\All" in line:
                    name = line.rsplit(' "/" ', 1)[-1].strip()
                    box = name if name.startswith('"') else f'"{name}"'
            m.select(box, readonly=True)
            typ, data = m.uid("search", None, "X-GM-RAW", f'"rfc822msgid:{mid}"')
            uids = (data[0] or b"").split()
            if not uids:
                return ""
            typ, got = m.uid("fetch", uids[-1], "(X-GM-THRID)")
            hit = re.search(rb"X-GM-THRID (\d+)", got[0] if isinstance(got[0], bytes) else got[0][0])
            return format(int(hit.group(1)), "x") if hit else ""
    except Exception:
        return ""                 # the reply still threads for the customer through its headers


UPLOAD_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send?uploadType=multipart"
MAX_FILES_BYTES = 20 * 1024 * 1024          # Gmail allows 25 MB per email, files grow ~1/3 when encoded


async def send(refresh_token: str, sender: str, sender_name: str, to: str, subject: str, text: str,
               in_reply_to: str = "", references: str = "", thread: str = "", files: list = None) -> dict:
    """files: [(file name, content type, bytes)] attached to the reply."""
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender)) if sender_name else sender
    msg["To"] = to
    msg["Subject"] = subject if re.match(r"^\s*re\s*:", subject or "", re.I) else f"Re: {subject or ''}".strip()
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = " ".join(x for x in ((references or "").strip(), in_reply_to) if x)
    msg.set_content(text)
    files = files or []
    if sum(len(d) for _, _, d in files) > MAX_FILES_BYTES:
        raise SendError("The attachments are bigger than 20 MB together. Send fewer or smaller files.")
    for name, ctype, data in files:
        main, _, sub = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=main or "application", subtype=sub or "octet-stream", filename=name)
    token = await _token(refresh_token)

    async def post(with_thread: bool):
        if not files:                                # small text reply: the simple way
            body = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
            if with_thread:
                body["threadId"] = thread
            return await client.post(SEND_URL, json=body, headers={"Authorization": f"Bearer {token}"})
        # with files: Gmail's upload address takes bigger emails
        boundary = "pd" + base64.urlsafe_b64encode(make_msgid().encode()).decode().strip("=")[:24]
        meta = ('{"threadId": "%s"}' % thread) if with_thread else "{}"
        payload = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                   f"--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n").encode() + msg.as_bytes() + \
            f"\r\n--{boundary}--".encode()
        return await client.post(UPLOAD_URL, content=payload, headers={
            "Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"})
    async with httpx.AsyncClient(timeout=180) as client:
        r = await post(bool(thread))
        if r.status_code == 404 and thread:          # unknown conversation: send without it
            r = await post(False)
    if r.status_code == 403 and ("accessNotConfigured" in r.text or "has not been used in project" in r.text):
        raise SendError("The Gmail API isn't switched on in the Google Cloud project yet (see Settings → Support mailboxes).")
    if r.status_code in (401, 403):
        raise SendError("Gmail refused to send from this mailbox. Sign it in again in Settings → Support mailboxes.")
    if r.status_code >= 300:
        raise SendError(f"Gmail couldn't send it ({r.status_code}): {r.text[:200]}")
    return {**r.json(), "message_id": msg["Message-ID"]}


async def check(refresh_token: str) -> str:
    """Prove sending will work without sending anything: Gmail is handed an empty message,
    which it refuses as invalid only if the sign-in and the Gmail API are both fine."""
    token = await _token(refresh_token)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(SEND_URL, json={"raw": ""}, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 400:
        return "ok"
    if "accessNotConfigured" in r.text or "has not been used in project" in r.text or "disabled" in r.text:
        try:
            detail = r.json()["error"]["message"]
        except Exception:
            detail = r.text[:300]
        raise SendError("The Gmail API isn't switched on yet: open the Gmail API link in Settings → Support mailboxes "
                        f"and click Enable. (Google says: {detail[:300]})")
    if r.status_code in (401, 403):
        raise SendError(f"Gmail refused this mailbox's sign-in. Click Sign in to send again. ({r.text[:200]})")
    raise SendError(f"Unexpected answer from Gmail ({r.status_code}): {r.text[:200]}")
