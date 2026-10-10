"""Gmail labels, kept in step with the Inbox desk (two-way), over the same IMAP
connection (app password) the mailboxes are read with.

Gmail shows labels per conversation, so ProfitDesk adds and removes a label on
every email in the Gmail thread (found with X-GM-THRID), like Gmail itself does.
Label colours aren't visible over IMAP; ProfitDesk keeps its own.
"""
import imaplib
import re
import ssl
from datetime import datetime, timedelta

SYSTEM = re.compile(r"^(\[gmail\]|\[google mail\]|inbox$|sent$|drafts?$|spam$|trash$|notes$)", re.I)


class LabelError(RuntimeError):
    pass


def _utf7_decode(s: str) -> str:
    """IMAP modified UTF-7 (labels with accents or emoji)."""
    import base64

    def dec(m):
        b = m.group(1)
        if not b:
            return "&"
        b = b.replace(",", "/")
        b += "=" * (-len(b) % 4)
        try:
            return base64.b64decode(b).decode("utf-16-be")
        except Exception:
            return m.group(0)
    return re.sub(r"&([A-Za-z0-9+,]*)-", dec, s)


def _utf7_encode(s: str) -> str:
    import base64
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            out.append("&" + base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=").replace("/", ",") + "-")
            buf = ""
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7e:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def _quote(name: str) -> str:
    return '"' + _utf7_encode(name).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _open(address: str, password: str, writable: bool = False):
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=60)
    m.login(address, password)
    box = '"[Gmail]/All Mail"'
    typ, rows = m.list()
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if "\\All" in line:
            name = line.rsplit(' "/" ', 1)[-1].strip()
            box = name if name.startswith('"') else f'"{name}"'
    m.select(box, readonly=not writable)
    return m, rows


def _names(rows) -> list:
    out = []
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if "\\Noselect" in line:
            continue
        name = line.rsplit(' "/" ', 1)[-1].strip().strip('"')
        name = _utf7_decode(name)
        if name and not SYSTEM.match(name):
            out.append(name)
    return sorted(set(out), key=str.lower)


def _parse_labels(blob: str) -> list:
    """The inside of X-GM-LABELS (...): quoted or bare words; system ones (\\Inbox...) dropped."""
    found = re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', blob)
    out = []
    for q, bare in found:
        v = (q.replace('\\"', '"').replace("\\\\", "\\") if q else bare).strip()
        if v and not v.startswith("\\"):
            out.append(_utf7_decode(v))
    return out


def sync(address: str, password: str, days: int = 45) -> tuple:
    """(label names in the mailbox, {Message-ID: [labels]} for emails of the last `days` days)."""
    try:
        m, rows = _open(address, password)
        with m:
            names = _names(rows)
            since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = m.uid("search", None, f"SINCE {since}")
            uids = (data[0] or b"").split()
            found = {}
            for i in range(0, len(uids), 400):
                chunk = b",".join(uids[i:i + 400]).decode()
                typ, got = m.uid("fetch", chunk, "(X-GM-LABELS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
                for part in got or []:
                    if not isinstance(part, tuple):
                        continue
                    head = part[0].decode("utf-8", "replace")
                    lab = re.search(r"X-GM-LABELS \((.*?)\)(?: UID| BODY|$)", head)
                    mid = re.search(r"message-id:\s*(<[^>]+>)", part[1].decode("utf-8", "replace"), re.I)
                    if mid:
                        found[mid.group(1).strip()] = _parse_labels(lab.group(1)) if lab else []
            return names, found
    except imaplib.IMAP4.error as e:
        raise LabelError(f"Couldn't read labels ({str(e)[:150]})")
    except OSError as e:
        raise LabelError(f"Couldn't reach Gmail ({type(e).__name__})")


def change(address: str, password: str, message_id: str, label: str, add: bool = True) -> int:
    """Add or remove a label on the whole Gmail conversation of this email. Returns emails changed."""
    mid = (message_id or "").strip().strip("<>")
    if not mid:
        raise LabelError("This email has no Message-ID, so Gmail can't find it.")
    try:
        m, rows = _open(address, password, writable=True)
        with m:
            if add and label not in _names(rows):
                m.create(_quote(label))                     # a new label, like Gmail's "Create new"
            typ, data = m.uid("search", None, "X-GM-RAW", f'"rfc822msgid:{mid}"')
            uids = (data[0] or b"").split()
            if not uids:
                return 0
            typ, got = m.uid("fetch", uids[-1], "(X-GM-THRID)")
            first = got[0] if isinstance(got[0], bytes) else got[0][0]
            thr = re.search(rb"X-GM-THRID (\d+)", first)
            if thr:
                typ, data = m.uid("search", None, "X-GM-THRID", thr.group(1).decode())
                uids = (data[0] or b"").split() or uids
            typ, _ = m.uid("store", b",".join(uids).decode(), "+X-GM-LABELS" if add else "-X-GM-LABELS",
                           f"({_quote(label)})")
            if typ != "OK":
                raise LabelError("Gmail didn't accept the label change.")
            return len(uids)
    except imaplib.IMAP4.error as e:
        raise LabelError(f"Gmail didn't accept the label change ({str(e)[:150]})")
    except OSError as e:
        raise LabelError(f"Couldn't reach Gmail ({type(e).__name__})")
