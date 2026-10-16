"""The ops agent: one worker for Slack (suppliers, supply chain, customer support), COG invoices, SCM and products.

Everything it does is a **job** started by something happening (a file in #gray-supplier-invoice, a supplier
message, a person asking), made of **steps** (what it read, decided, did, or waits for). People advise on any step;
advice becomes a **lesson** the agent reads before similar work. Where it's unsure it writes a **question** for
the team. It keeps its own copy of every Slack message it reads (free Slack forgets after 90 days).

Phase 1 (15 Oct 2026): Slack connection + message store, the 90-day supplier-invoice backfill and live invoice
filing through the normal invoice reader (estimates replaced by invoiced costs once matched; an AI disagreement
waits for a person), the job log with advice → lessons, and questions. Training (SOPs, 500 emails, quizzes),
supplier conversations and customer replies come next.
"""
import json
import re
from datetime import datetime, timezone

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_channels (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    private     INTEGER,
    role        TEXT,                 -- supplier_invoice | supply_chain | customer_support | eod | other
    supplier    TEXT,                 -- who's on the other side (dayone, gray …), owner can rename
    store_scope TEXT,                 -- free text: which stores it's about, if known
    read_to     TEXT,                 -- newest message ts read so far
    read_at     TEXT,
    members     INTEGER
);
CREATE TABLE IF NOT EXISTS slack_messages (
    channel   TEXT NOT NULL,
    ts        TEXT NOT NULL,
    thread_ts TEXT,
    user      TEXT,
    text      TEXT,
    files     TEXT,                   -- JSON [{id, name, mimetype, size, url}]
    subtype   TEXT,
    at        TEXT,                   -- UTC iso
    PRIMARY KEY (channel, ts)
);
CREATE INDEX IF NOT EXISTS slack_messages_at ON slack_messages (at);
CREATE TABLE IF NOT EXISTS slack_users (
    id        TEXT PRIMARY KEY,
    name      TEXT,
    real_name TEXT,
    is_bot    INTEGER,
    team      TEXT
);
CREATE TABLE IF NOT EXISTS agent_jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,         -- invoice_backfill | invoice | history | ask
    title      TEXT NOT NULL,
    area       TEXT,                  -- invoices | suppliers | supply_chain | customers | general
    status     TEXT NOT NULL DEFAULT 'running',   -- running | waiting | done | failed
    source     TEXT,                  -- JSON: what started it (channel, ts, person)
    summary    TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_jobs_at ON agent_jobs (updated_at);
CREATE TABLE IF NOT EXISTS agent_steps (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  INTEGER NOT NULL,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,            -- read | think | act | wait | done | error | skip
    title   TEXT NOT NULL,
    detail  TEXT,
    data    TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS agent_steps_job ON agent_steps (job_id);
CREATE TABLE IF NOT EXISTS agent_advice (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    INTEGER NOT NULL,
    step_id   INTEGER,
    by        TEXT,
    at        TEXT NOT NULL,
    text      TEXT NOT NULL,
    lesson_id INTEGER
);
CREATE TABLE IF NOT EXISTS agent_lessons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    area       TEXT NOT NULL DEFAULT 'general',
    text       TEXT NOT NULL,
    source     TEXT,                  -- advice:<id> | answer:<id> | sop | manual
    active     INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    area        TEXT NOT NULL DEFAULT 'general',
    question    TEXT NOT NULL,
    context     TEXT,
    job_id      INTEGER,
    asked_at    TEXT NOT NULL,
    answer      TEXT,
    answered_by TEXT,
    answered_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_payments (           -- bank transfer notices / payment proofs posted in Slack
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    TEXT UNIQUE,
    channel    TEXT,
    ts         TEXT,
    name       TEXT,
    path       TEXT,                  -- kept copy on the volume (<DB dir>/agent_proofs/)
    supplier   TEXT,
    amount     REAL,
    currency   TEXT,
    paid_on    TEXT,
    reference  TEXT,
    payee      TEXT,
    note       TEXT,                  -- the AI's reading in words
    matched    TEXT,                  -- the bank payment it matches (Airwallex / PayPal id), or NULL
    match_note TEXT,
    at         TEXT
);
CREATE TABLE IF NOT EXISTS agent_chat (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    who     TEXT,
    at      TEXT
);
CREATE TABLE IF NOT EXISTS agent_files (              -- each Slack file the agent handled, once
    file_id    TEXT PRIMARY KEY,
    channel    TEXT,
    ts         TEXT,
    name       TEXT,
    job_id     INTEGER,
    invoice_id INTEGER,
    outcome    TEXT,
    at         TEXT
);
"""

CANDIDATE_EXT = (".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".webp")
AREAS = ("general", "invoices", "suppliers", "supply_chain", "customers", "products")
ROLE_AREA = {"supplier_invoice": "invoices", "supply_chain": "supply_chain", "customer_support": "customers",
             "eod": "general", "other": "general"}
ROLES = {"supplier_invoice": "Supplier invoices", "supply_chain": "Supply chain", "customer_support": "Customer support",
         "eod": "End-of-day reports", "other": "Other"}
INVOICE_EXT = (".pdf", ".xlsx", ".xls", ".csv")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guess(name: str) -> tuple:
    """(role, supplier) from a channel name like gray-supplier-invoice or dayone-supply-chain-management."""
    n = (name or "").lower()
    role = ("supplier_invoice" if "invoice" in n else "supply_chain" if re.search(r"supply.?chain", n) else
            "customer_support" if re.search(r"customer|support|service", n) else "eod" if "eod" in n else "other")
    supplier = n.split("-")[0] if "-" in n and role != "eod" else ""
    return role, supplier


def ts_iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return now()


# ---------------------------------------------------------------- job log

def start(kind: str, title: str, area: str = "general", source: dict = None) -> int:
    with db._conn() as con:
        return con.execute("INSERT INTO agent_jobs (kind, title, area, status, source, created_at, updated_at)"
                           " VALUES (?,?,?,?,?,?,?)", (kind, title[:300], area, "running", json.dumps(source or {}),
                                                       now(), now())).lastrowid


def step(job_id: int, kind: str, title: str, detail: str = "", data: dict = None) -> int:
    with db._conn() as con:
        sid = con.execute("INSERT INTO agent_steps (job_id, at, kind, title, detail, data) VALUES (?,?,?,?,?,?)",
                          (job_id, now(), kind, title[:500], (detail or "")[:4000],
                           json.dumps(data) if data else None)).lastrowid
        con.execute("UPDATE agent_jobs SET updated_at = ? WHERE id = ?", (now(), job_id))
    return sid


def finish(job_id: int, status: str, summary: str = ""):
    with db._conn() as con:
        con.execute("UPDATE agent_jobs SET status = ?, summary = ?, updated_at = ? WHERE id = ?",
                    (status, summary[:1000], now(), job_id))


def ask(area: str, question: str, context: str = "", job_id: int = None) -> int:
    """A question for the team (it doesn't ask the same open question twice)."""
    with db._conn() as con:
        old = con.execute("SELECT id FROM agent_questions WHERE question = ? AND answer IS NULL", (question,)).fetchone()
        if old:
            return old[0]
        return con.execute("INSERT INTO agent_questions (area, question, context, job_id, asked_at) VALUES (?,?,?,?,?)",
                           (area, question[:1000], (context or "")[:3000], job_id, now())).lastrowid


def lessons(area: str = None) -> list:
    """Active lessons, for the agent to read before work in an area (general ones always)."""
    with db._conn() as con:
        rows = con.execute("SELECT * FROM agent_lessons WHERE active = 1" + (" AND area IN (?, 'general')" if area else "")
                           + " ORDER BY id", (area,) if area else ()).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- what is this file? (before filing anything)

CLASSIFY_MODEL = "claude-haiku-5-5"
CLASSIFY_SYSTEM = """You sort files that suppliers and the team post in Slack for an e-commerce business (several Shopify stores;
suppliers such as DayOne and Graypop pack and ship the orders). Decide what ONE file is from its name and its content:
- invoice: a bill asking to be paid for goods / fulfilment: amounts per order or item, a total due, often an invoice number.
- payment: proof that money was sent: a bank transfer notice / confirmation, remittance advice, payment receipt or a
  screenshot of a transfer (amount, date, reference, payer / payee).
- tracking: a list of orders with tracking numbers / carriers / shipping status (even if it shows some prices).
- other: anything else (price lists, quotes, catalogues, labels, product photos, reports, statements).
Be strict: only "invoice" when it is clearly a bill. confidence 0-1.
store: the shop / brand the file is for, exactly as written on it or in the file name (e.g. "CUTEHOME", "Creston Perth"), or "".
supplier: who issued an invoice or received a payment (e.g. "Dayone", "Graypop"), or "".
For a payment also fill amount (number), currency (3 letters), paid_on (YYYY-MM-DD), reference, payee; otherwise null / "".
{lessons}"""
CLASSIFY_SCHEMA = {"type": "object", "additionalProperties": False,
                   "required": ["kind", "confidence", "reason", "store", "supplier", "amount", "currency", "paid_on", "reference", "payee"],
                   "properties": {"kind": {"type": "string", "enum": ["invoice", "payment", "tracking", "other"]},
                                  "confidence": {"type": "number"}, "reason": {"type": "string"},
                                  "store": {"type": "string"}, "supplier": {"type": "string"},
                                  "amount": {"type": ["number", "null"]}, "currency": {"type": "string"},
                                  "paid_on": {"type": "string"}, "reference": {"type": "string"}, "payee": {"type": "string"}}}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


async def classify(api_key: str, filename: str, text: str, image: bytes = None) -> dict:
    """What one file is (invoice / payment proof / tracking / other), whose store and supplier it is, and for a payment
    proof its amount, date and reference. Follows the team's invoice and supplier lessons."""
    import anthropic
    import base64
    taught = [l["text"] for l in lessons("invoices")][-30:] + [l["text"] for l in lessons("suppliers") if l["area"] == "suppliers"][-15:]
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=90)
    content = [{"type": "text", "text": f"File name: {filename}\n\n" + (f"Start of its text:\n{(text or '')[:6000]}" if not image else "The file is the image below.")}]
    if image:
        ext = "." + (filename or "").lower().rsplit(".", 1)[-1]
        content.append({"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES.get(ext, "image/png"),
                                                    "data": base64.b64encode(image).decode()}})
    resp = await client.messages.create(
        model=CLASSIFY_MODEL, max_tokens=600,
        system=CLASSIFY_SYSTEM.format(lessons=("What the team has taught you (follow it):\n- " + "\n- ".join(taught)) if taught else ""),
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
        messages=[{"role": "user", "content": content}])
    if resp.stop_reason == "refusal":
        return {"kind": "other", "confidence": 0, "reason": "The AI declined to read it.", "store": "", "supplier": ""}
    return json.loads(next((b.text for b in resp.content if b.type == "text"), "{}"))


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def store_from_name(filename: str) -> str:
    """DayOne names invoices "<Store>-Invoice <date> Dayone.pdf"."""
    m = re.match(r"\s*(.+?)\s*-\s*invoice\b", filename or "", re.I)
    return m.group(1).strip() if m else ""


def store_decisions() -> dict:
    """{normalised store name: "include" | "exclude"}: the owner's answer for stores that aren't connected."""
    try:
        return json.loads(db.get_setting("agent_stores") or "{}")
    except ValueError:
        return {}


def known_store(name: str, stores: list):
    """The connected store a name means (exact or contained, ignoring case / spaces), else None."""
    n = norm(name)
    if not n:
        return None
    for st in stores:
        sn = norm(st["name"])
        if sn and (sn == n or (len(n) >= 5 and (n in sn or sn in n))):
            return st
    return None



# ---------------------------------------------------------------- the agent's brain (Ask the agent)

BRAIN_MODEL = "claude-sonnet-5-5"
BRAIN_BETAS = ["server-side-fallback-2026-07-01"]


class BrainError(RuntimeError):
    pass


def _t(name, description, props, required):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


BRAIN_TOOLS = [
    _t("supplier_positions", "Each supplier's account: opening balance (owner's figure and date), invoices oldest first with "
       "store, order range and paid / part paid / on credit (payments from Airwallex / PayPal applied oldest first after the "
       "opening), paid up to which invoice and order, invoiced up to which order per store, unpaid invoiced, shipped but not "
       "invoiced, total on credit, and the bank transfer notices kept from Slack with their bank match.",
       {"supplier": {"type": ["string", "null"], "description": "e.g. Dayone, Graypop; null for all"}}, ["supplier"]),
    _t("find_invoices", "Invoices in the app (COG → Invoices), filtered by supplier / store (in the file name) / date from.",
       {"supplier": {"type": ["string", "null"]}, "store": {"type": ["string", "null"]},
        "since": {"type": ["string", "null"], "description": "YYYY-MM-DD"}}, ["supplier", "store", "since"]),
    _t("search_slack", "Search EcomVital's copy of the Slack channels (suppliers, supply chain, customer support, EOD) for text "
       "or file names over the last N days (max 90).",
       {"text": {"type": "string"}, "channel": {"type": ["string", "null"], "description": "part of a channel name, or null"},
        "days": {"type": "integer"}}, ["text", "channel", "days"]),
    _t("check_transfer_notices", "Match the kept bank transfer notices against the actual payments out of Airwallex / PayPal.", {}, []),
    _t("ask_team", "Ask the team a question when you can't tell from the data (it appears on the Questions tab; the answer "
       "becomes a lesson). Use it instead of guessing.",
       {"question": {"type": "string"}, "area": {"type": "string", "enum": list(AREAS)}, "why": {"type": "string"}},
       ["question", "area", "why"]),
]

BRAIN_SYSTEM = """You are EcomVital's ops agent for a group of Shopify stores, working with the owner and their team.
Today is {today} (Hong Kong). Suppliers (DayOne, Graypop …) pack and ship orders and bill them; talk with them happens
in Slack. You keep the supplier invoices, the bank transfer notices and the supplier accounts straight.

How to work:
- Use your tools; never make up a figure, an order number or a date. Say where a number comes from.
- Supplier money: opening balance (the owner sets it in COG → Suppliers) + invoices − payments = what's owed. "Paid up to"
  = the last invoice the payments fully cover, oldest first. "On credit" = invoiced but unpaid + shipped but not invoiced.
- Bank transfer notices posted in Slack are claims; the bank (Airwallex / PayPal) payment is the proof. Say when a notice has
  no matching bank payment.
- When something doesn't add up or you can't tell, say so plainly and use ask_team rather than guessing.
- Short answers for a busy owner who isn't a developer: the answer first, then a small table if useful.
{lessons}"""


async def brain(api_key: str, history: list, run_tool, today: str) -> dict:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=240)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-14:]
                if m.get("content") and m.get("role") in ("user", "assistant")]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    taught = [f"[{l['area']}] {l['text']}" for l in lessons()][-60:]
    system = BRAIN_SYSTEM.format(today=today, lessons=("\nWhat the team has taught you (follow it):\n- " + "\n- ".join(taught)) if taught else "")
    for _ in range(10):
        try:
            resp = await client.beta.messages.create(
                model=BRAIN_MODEL, max_tokens=12000, betas=BRAIN_BETAS, fallbacks="default", system=system, tools=BRAIN_TOOLS,
                thinking={"type": "adaptive"}, output_config={"effort": "medium"}, messages=messages)
        except anthropic.AuthenticationError as e:
            raise BrainError("Anthropic didn't accept the API key. Check it in Settings → AI.") from e
        except anthropic.RateLimitError as e:
            raise BrainError("The AI is busy right now. Try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise BrainError(f"Anthropic error {e.status_code}: {str(e)[:200]}") from e
        except anthropic.APIConnectionError as e:
            raise BrainError("Couldn't reach Anthropic.") from e
        if resp.stop_reason == "refusal":
            return {"text": "I can't help with that one."}
        if resp.stop_reason != "tool_use":
            return {"text": "".join(b.text for b in resp.content if b.type == "text").strip() or "(no answer)"}
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            try:
                out = await run_tool(b.name, b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str)[:60000]})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": str(getattr(e, "detail", None) or e)[:600]})
        messages.append({"role": "user", "content": results})
    return {"text": "That took too many steps. Try a narrower question."}
