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
