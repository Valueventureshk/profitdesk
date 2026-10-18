"""Tasks desk: admins give staff work (mostly listing a collection / products + its ads), staff hand back
links, creatives and ad copy, admins approve, then launch the ads on Meta (created paused) or mark it done.

Kinds: "listing" (store, collection, pages to build, ad brief → staff return the links + creatives + copy)
and "custom" (title, description, files). Every task has a Special notes box staff always see first.

Flow: todo → doing → submitted → (changes → submitted …) → approved ("Waiting to launch")
      → launched (listing) or done (custom). launched / done / cancelled = History.
Every step, comment and upload is a row in task_log: the end-of-day view reads it.
Files sit on the volume in <DB dir>/task_files/<task id>/<file id>-<name>.
"""
import json
import re

STATUSES = ("todo", "doing", "submitted", "changes", "approved", "launched", "done", "cancelled")
HISTORY = ("launched", "done", "cancelled")
KINDS = ("listing", "custom", "store")

# what the admin fills in (brief) and what staff hand back (result), per kind
BRIEF_FIELDS = ("competitor_ads", "competitor_landing", "competitor_products", "todo", "description",
                # new store: what the admin fills in (see STORE_*)
                "shopify_domain", "brand_domain", "country", "language", "currency", "ads_platform", "competitor_url",
                "reviews", "payments", "support_email", "ad_account", "app_connected",
                # older listing tasks (before the to-do picks) — still shown, no longer asked for
                "collection", "products", "pages", "angle", "headlines", "budget", "countries")
RESULT_FIELDS = ("landing_url", "collection_url", "product_urls", "advertorial_url", "branded_url", "primary_text",
                 "headline", "ad_description", "cta", "other_links", "notes")

# What an admin can tick on a listing task, and what each one asks staff to hand back.
# Anything else typed in the to-do list is a custom item: staff answer it with a link or a note.
TODO = {
    "advertorial": "Create advertorial",
    "collection": "Create collection",
    "product_pages": "Create product pages",
    "branded_page": "Create branded page",
    "creatives": "Create creatives",
    "ad_copy": "Ad copy, headline and description",
}
# "Create new store" tasks: the steps Claude / staff work through, in order (following the Store Playbook,
# setting store_playbook). Each is answered with a link or a short note (result.custom) or "can't do" with why.
STORE_TODO = {
    "s_theme": "Install the Perth theme (from the store kit)",
    "s_pages": "Pages: about us, contact, track order, FAQ, shipping / returns / privacy / terms",
    "s_products": "Import ~1,000 products from the competitor (market price rules)",
    "s_collections": "Collections + collection template",
    "s_menus": "Menus: header, footer",
    "s_story": "Sale story + discount, consistent across the store",
    "s_product_template": "Product page like Perth (reviews, trust badges, size chart)",
    "s_reviews": "Place the real customer reviews",
    "s_payments": "Payment gateways checked (Airwallex, PayPal)",
    "s_tracking": "EcomVital app, pixel and tracking page live",
    "s_meta": "Meta ad account linked to Shopify and EcomVital",
    "s_launch": "Final check: test the order path, ready to launch",
}
# the admin's yes/no checks on a store task; "no" = a daily reminder until it's done
STORE_CHECKS = {"payments": "Payment gateways connected (Airwallex, PayPal)", "support_email": "Support email set up",
                "ad_account": "Meta ad account created", "app_connected": "Store connected to EcomVital (Shopify app)"}

TODO_NEEDS = {"advertorial": ("advertorial_url",), "collection": ("collection_url",), "product_pages": ("product_urls",),
              "branded_page": ("branded_url",), "ad_copy": ("primary_text", "headline")}

# Launching from the Tasks desk stays greyed out until the owner hands over their tested strategies
# (15 Oct 2026); the X/Y/Z below are placeholders. Switch to True once STRATEGIES holds the real ones.
LAUNCH_READY = False

# Ways to put several approved listing tasks into one Meta campaign (all created PAUSED).
# Starter set: the owner will replace / add their own tested strategies.
STRATEGIES = {
    "x": {"name": "Strategy X · one ad set, every ad",
          "about": "One campaign, one ad set. Every creative from every picked task becomes an ad, each going to "
                   "its own task's landing page with that task's copy. Meta spends where it works best.",
          "budget": "adset", "split": "one"},
    "y": {"name": "Strategy Y · one ad set per landing page (ABO)",
          "about": "One campaign, one ad set per task (per landing page), each with the daily budget below. "
                   "Every page gets a fair test.",
          "budget": "adset", "split": "task"},
    "z": {"name": "Strategy Z · campaign budget, ad set per landing page (CBO)",
          "about": "One campaign holding the daily budget, one ad set per task. Meta moves the budget to the "
                   "landing pages that sell.",
          "budget": "campaign", "split": "task"},
}
CTAS = ("SHOP_NOW", "LEARN_MORE", "ORDER_NOW", "BUY_NOW", "GET_OFFER", "SIGN_UP")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL DEFAULT 'listing',     -- listing | custom
    title         TEXT    NOT NULL,
    store_id      INTEGER,
    assignee_id   INTEGER,                                -- users.id
    status        TEXT    NOT NULL DEFAULT 'todo',
    priority      TEXT    NOT NULL DEFAULT 'normal',      -- normal | high
    due           TEXT,                                   -- YYYY-MM-DD
    special_notes TEXT,                                   -- staff always see this first
    brief         TEXT,                                   -- JSON, admin's input
    result        TEXT,                                   -- JSON, staff's hand-back
    launch        TEXT,                                   -- JSON, Meta ids made by Launch (kept to resume)
    created_by    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    submitted_at  TEXT,
    approved_by   TEXT,
    approved_at   TEXT,
    closed_at     TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS tasks_assignee ON tasks (assignee_id);
CREATE TABLE IF NOT EXISTS task_files (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL,
    side     TEXT    NOT NULL DEFAULT 'staff',            -- admin (brief) | staff (hand-back)
    kind     TEXT    NOT NULL DEFAULT 'creative',         -- creative (goes in the ads) | file
    name     TEXT    NOT NULL,
    mime     TEXT,
    size     INTEGER,
    by       TEXT,
    at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS task_files_task ON task_files (task_id);
CREATE TABLE IF NOT EXISTS task_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL,
    by       TEXT,                                        -- email
    at       TEXT    NOT NULL DEFAULT (datetime('now')),  -- UTC
    action   TEXT    NOT NULL,                            -- created, status, comment, file, edit, launch
    text     TEXT
);
CREATE INDEX IF NOT EXISTS task_log_task ON task_log (task_id);
CREATE INDEX IF NOT EXISTS task_log_at ON task_log (at);
CREATE TABLE IF NOT EXISTS task_wa (                      -- WhatsApp messages sent about a task (to read replies)
    wa_id       TEXT    PRIMARY KEY,                      -- Meta's message id (a reply carries it as its context)
    task_id     INTEGER NOT NULL,
    phone       TEXT    NOT NULL,
    kind        TEXT    NOT NULL,                         -- task (new / given) | followup
    asked_by    TEXT,                                     -- email: who gave the task / pressed Follow up
    at          TEXT    NOT NULL DEFAULT (datetime('now')),
    answered_at TEXT
);
CREATE INDEX IF NOT EXISTS task_wa_phone ON task_wa (phone, at);
CREATE TABLE IF NOT EXISTS task_launches (                -- several approved tasks launched together on Meta
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    INTEGER NOT NULL,
    strategy    TEXT    NOT NULL,
    task_ids    TEXT    NOT NULL,                         -- JSON list
    settings    TEXT,                                     -- JSON: page, pixel, budget, countries, name
    progress    TEXT,                                     -- JSON: Meta ids as they're made (to carry on)
    status      TEXT    NOT NULL DEFAULT 'running',       -- running | waiting (videos) | failed | done
    error       TEXT,
    created_by  TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    done_at     TEXT
);
"""

# WhatsApp replies: "doing" / "done" (optionally with the task number, e.g. "done #12")
_DOING = ("doing", "doing it", "start", "started", "starting", "on it", "in progress", "working on it")
_DONE = ("done", "finished", "complete", "completed", "submitted", "all done", "ready")


def wa_verb(text: str):
    """('doing' | 'done' | None, task number or None) from a short WhatsApp reply."""
    t = re.sub(r"[^\w#\s]", " ", (text or "").lower()).strip()
    m = re.fullmatch(r"(.+?)\s*(?:task\s*)?#?\s*(\d+)?", t)
    if not m:
        return None, None
    word = re.sub(r"\s+", " ", m.group(1)).strip()
    num = int(m.group(2)) if m.group(2) else None
    if word in _DOING:
        return "doing", num
    if word in _DONE:
        return "done", num
    return None, None

STATUS_NAMES = {"todo": "To do", "doing": "In progress", "submitted": "Submitted for review",
                "changes": "Changes asked", "approved": "Waiting to launch", "launched": "Launched",
                "done": "Done", "cancelled": "Cancelled"}


def _clean(d: dict, fields) -> dict:
    out = {}
    for k in fields:
        v = (d or {}).get(k)
        if isinstance(v, dict):
            v = {str(a).strip()[:200]: str(b).strip()[:4000] for a, b in v.items() if str(b or "").strip()}
        elif isinstance(v, list):
            v = [str(x).strip()[:2000] for x in v if str(x).strip()][:50]
        elif v is not None:
            v = str(v).strip()[:8000]
        if v:
            out[k] = v
    return out


def clean_brief(d: dict) -> dict:
    out = _clean(d, BRIEF_FIELDS)
    if "pages" in out and not isinstance(out["pages"], list):
        out["pages"] = [p.strip() for p in out["pages"].split(",") if p.strip()]
    return out


def clean_result(d: dict) -> dict:
    out = _clean(d, RESULT_FIELDS + ("custom", "cant"))
    for k in ("custom", "cant"):
        if k in out and not isinstance(out[k], dict):
            out.pop(k)
    if out.get("cta") and out["cta"] not in CTAS:
        out.pop("cta")
    return out


def todo_label(key: str) -> str:
    return TODO.get(key) or STORE_TODO.get(key) or key


def store_pending(brief: dict) -> list:
    """The admin's store checks still answered "no" (or not answered)."""
    return [label for k, label in STORE_CHECKS.items() if str(brief.get(k) or "no").strip().lower() in ("no", "", "false")]


def missing(kind: str, brief: dict, result: dict, creatives: int) -> list:
    """What still stops a listing task being submitted: the main landing page link, and every to-do item
    that has neither its output nor a "couldn't do it" note for the admin."""
    if kind not in ("listing", "store"):
        return []
    out = [] if result.get("landing_url") else ["the main landing page link" if kind == "listing" else "the store link"]
    cant = result.get("cant") or {}
    for key in brief.get("todo") or []:
        if cant.get(key):
            continue
        if key == "creatives":
            done = creatives > 0
        elif key in TODO_NEEDS:
            done = all(result.get(f) for f in TODO_NEEDS[key])
        else:
            done = bool((result.get("custom") or {}).get(key))
        if not done:
            label = todo_label(key)
            out.append(label[:1].lower() + label[1:])
    return out


def loads(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except ValueError:
        return default if default is not None else {}


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "file").strip())[-80:].strip("-.")
    return name or "file"


def lines(v) -> list:
    """A text box with one item per line (several primary texts / headlines / URLs)."""
    if isinstance(v, list):
        return [x for x in v if x]
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]
