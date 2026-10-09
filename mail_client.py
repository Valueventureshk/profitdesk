"""Store support mailboxes, read over IMAP (Google Workspace / Gmail with an app
password, or any provider that offers IMAP).

Read-only so far: emails are copied into ProfitDesk; nothing in the mailbox is
marked read, moved or deleted (messages are fetched with BODY.PEEK).

    first run    the last DAYS_BACK days of INBOX
    after that   only messages with a UID above the last one seen
"""
import email
import email.policy
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

DAYS_BACK = 30
PROVIDERS = {
    "google": ("imap.gmail.com", 993, "smtp.gmail.com", 587),
}


class MailError(RuntimeError):
    pass


def _explain(e: Exception, what: str) -> MailError:
    msg = str(e)
    if "AUTHENTICATIONFAILED" in msg or "Invalid credentials" in msg or "535" in msg \
            or "Application-specific password required" in msg:
        return MailError("The mailbox didn't accept that email and app password. Check it's the "
                         "16-letter App password (not the normal password), copied in full.")
    if "IMAP access is disabled" in msg or "IMAP" in msg and "disabled" in msg:
        return MailError("IMAP is turned off for this mailbox. Turn it on in Gmail settings → "
                         "Forwarding and POP/IMAP (or ask the Workspace admin to allow it).")
    return MailError(f"Couldn't {what} ({type(e).__name__}: {msg[:160]}).")


def check(address: str, password: str, provider: str = "google"):
    """Log in to IMAP once, to prove the details work.

    Sending isn't checked: the server (Railway Hobby plan) blocks outgoing SMTP,
    so replies will go out another way when the Inbox can reply."""
    imap_host, imap_port, _, _ = PROVIDERS[provider]
    try:
        with imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ssl.create_default_context(),
                               timeout=30) as m:
            m.login(address, password)
            m.select("INBOX", readonly=True)
    except Exception as e:
        raise _explain(e, "read the mailbox")


def _text(msg) -> tuple:
    """(plain text, html) of a message; plain text is made from the html if needed."""
    plain, html = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            body = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if part.get_content_type() == "text/plain" and plain is None:
            plain = body
        elif part.get_content_type() == "text/html" and html is None:
            html = body
    if plain is None and html:
        t = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        plain = re.sub(r"\n{3,}", "\n\n", t).strip()
    return (plain or "").strip(), html


def _attachments(msg) -> list:
    out = []
    for part in msg.walk():
        name = part.get_filename()
        if name:
            out.append({"name": name, "type": part.get_content_type(),
                        "size": len(part.get_payload(decode=True) or b"")})
    return out


def parse(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
    except Exception:
        when = datetime.now(timezone.utc)
    text, html = _text(msg)
    return {
        "message_id": str(msg.get("Message-ID", "")).strip(),
        "in_reply_to": str(msg.get("In-Reply-To", "")).strip(),
        "references": str(msg.get("References", "")).strip(),
        "from_name": name, "from_addr": addr.lower(),
        "to_addr": ", ".join(a for _, a in getaddresses([str(msg.get("To", ""))])),
        "subject": str(msg.get("Subject", "")).strip(),
        "date": when.isoformat(),
        "text": text[:200000], "html": (html or "")[:400000],
        "attachments": _attachments(msg),
    }


def fetch_new(address: str, password: str, last_uid: int = 0, provider: str = "google") -> tuple:
    """New INBOX messages since last_uid (or the last DAYS_BACK days on the first run).
    Returns (messages with their uid, highest uid seen). Nothing is marked read."""
    imap_host, imap_port, _, _ = PROVIDERS[provider]
    out = []
    try:
        with imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ssl.create_default_context(),
                               timeout=60) as m:
            m.login(address, password)
            m.select("INBOX", readonly=True)
            if last_uid:
                typ, data = m.uid("search", None, f"UID {last_uid + 1}:*")
            else:
                since = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%d-%b-%Y")
                typ, data = m.uid("search", None, f"SINCE {since}")
            uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid]
            for uid in uids[-500:]:
                typ, got = m.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((g[1] for g in got if isinstance(g, tuple)), None)
                if raw:
                    out.append({"uid": uid, **parse(raw)})
    except Exception as e:
        raise _explain(e, "read new emails")
    return out, max([last_uid] + [m["uid"] for m in out])
