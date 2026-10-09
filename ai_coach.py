"""Teaching the AI how this business handles customers, and drafting replies.

No model is retrained. Instead the AI is given what a new agent would be given:

    SOPs        written by the owner (Inbox → AI training)
    playbook    written by Claude Sonnet 5.5 after reading up to 500 past
                conversations (a customer's email and our reply): for each kind of
                situation, how we handle it, plus tone and sign-off
    answers     the AI's questions about anything unclear, answered by the owner
    examples    the past replies most similar to the ticket being answered

Drafting puts all of that, the ticket's thread and the customer's Shopify orders
in front of Claude Sonnet 5.5, which writes the reply in the customer's language.
"""
import json
import re

import anthropic

MODEL = "claude-sonnet-5-5"
BETAS = ["server-side-fallback-2026-07-01"]      # retry a declined request on another model


class CoachError(RuntimeError):
    pass


async def _ask(api_key: str, system: str, user: str, schema: dict = None, effort: str = "medium",
               max_tokens: int = 16000) -> str:
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=300)
    config = {"effort": effort}
    if schema:
        config["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = await client.beta.messages.create(
            model=MODEL, max_tokens=max_tokens, betas=BETAS, fallbacks="default",
            system=system, output_config=config,
            messages=[{"role": "user", "content": user}])
    except anthropic.AuthenticationError as e:
        raise CoachError("Anthropic didn't accept the API key. Check it in Settings → AI.") from e
    except anthropic.RateLimitError as e:
        raise CoachError("Anthropic is busy (rate limit). Try again in a minute.") from e
    except anthropic.APIStatusError as e:
        if "credit" in str(e).lower() or "billing" in str(e).lower():
            raise CoachError("The Anthropic account is out of credit.") from e
        raise CoachError(f"Anthropic error {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise CoachError("Couldn't reach Anthropic.") from e
    if resp.stop_reason == "refusal":
        raise CoachError("The AI declined this one. Write it by hand.")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------- past conversations

_RE = re.compile(r"^\s*((re|fw|fwd|aw|sv|tr|rv)\s*:\s*)+", re.I)


def clean_text(text: str, limit: int = 1800) -> str:
    body = (text or "").strip()
    for marker in ("\nOn ", "\n-----Original Message", "\nLe ", "\nEl ", "\nAm ", "\nOp ", "\n> "):
        cut = body.find(marker)
        if cut > 80:
            body = body[:cut]
            break
    body = re.sub(r"https?://\S{60,}", "[link]", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()[:limit]


def threads(messages: list, limit: int = 500) -> list:
    """Group copied emails into conversations (customer + store + subject) and keep
    those where we replied. Newest first. Each: {customer, store_id, subject,
    turns: [(direction, text)], last}"""
    convo = {}
    for m in sorted(messages, key=lambda m: m["date"]):
        if m["direction"] == "in":
            who = (m["from_addr"] or "").lower()
        else:
            who = (m["to_addr"] or "").split(",")[0].strip().lower()
        if not who or (m["direction"] == "in" and m.get("category") == "other"):
            continue
        subj = _RE.sub("", m["subject"] or "").strip().lower()[:80]
        key = (m["account_id"], who, subj)
        c = convo.setdefault(key, {"customer": who, "account_id": m["account_id"], "subject": m["subject"],
                                   "turns": [], "last": m["date"]})
        text = clean_text(m["text"])
        if text:
            c["turns"].append((m["direction"], text))
            c["last"] = m["date"]
    keep = [c for c in convo.values()
            if any(d == "in" for d, _ in c["turns"]) and any(d == "out" for d, _ in c["turns"])
            and c["turns"][0][0] == "in"]
    keep.sort(key=lambda c: c["last"], reverse=True)
    return keep[:limit]


def _render(c: dict, n: int = None) -> str:
    head = f"### Conversation {n}" if n is not None else "### Conversation"
    lines = [f"{head} (subject: {c['subject'] or '-'})"]
    for d, text in c["turns"][:6]:
        lines.append(("CUSTOMER: " if d == "in" else "US: ") + text)
    return "\n".join(lines)


LESSONS_SCHEMA = {
    "type": "object",
    "properties": {
        "lessons": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "how_we_handle": {"type": "string"},
                "example_reply": {"type": "string"},
            },
            "required": ["situation", "how_we_handle", "example_reply"],
            "additionalProperties": False}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lessons", "questions"],
    "additionalProperties": False,
}

LEARN_SYSTEM = """You are learning how an online store's support team handles customers, so you can later write replies exactly the way they do.
The store sells online (Shopify); orders ship from suppliers abroad, so delivery takes a while.

Read the conversations and write down lessons: for each kind of situation (where is my order, tracking not moving, change address, cancel before/after shipping, refund, return, damaged item, wrong size or item, exchange, quality complaint, chargeback/dispute, product questions...), what the team actually does and says: offers, conditions, time frames, what they ask the customer for (photos, order number), what they never promise, tone, greeting and sign-off.
Only record what the conversations show. Where the team was inconsistent, or a decision clearly depends on a rule you can't see (e.g. refund vs replacement, return address, who pays return shipping, compensation amounts), add a short question for the owner instead of guessing."""


async def learn_batch(api_key: str, convos: list, sops: str) -> dict:
    user = (f"The owner's SOPs so far (may be empty):\n{sops or '(none yet)'}\n\n"
            + "\n\n".join(_render(c, i + 1) for i, c in enumerate(convos)))
    text = await _ask(api_key, LEARN_SYSTEM, user, LESSONS_SCHEMA, effort="medium")
    try:
        return json.loads(text)
    except ValueError as e:
        raise CoachError("Couldn't read the AI's lessons.") from e


PLAYBOOK_SYSTEM = """You write the support playbook for an online store, from lessons collected from its past conversations and the owner's SOPs and answers.
The SOPs and the owner's answers always win over lessons from conversations.

Write it in Markdown, organised by situation (one section each: when it applies, what we do, what we say, what we ask for, what we never do, a short example reply). Start with a short "Voice and tone" section (greeting, sign-off, length, formality) and end with "Things to always check" (e.g. order status and tracking before replying).
Be specific (numbers, time frames, conditions). Leave out anything that is only a one-off.

After the playbook, on a line that says exactly ===QUESTIONS===, list up to 12 short questions for the owner (one per line) about rules that are still unclear or inconsistent. Skip questions the SOPs or answers already settle."""


async def write_playbook(api_key: str, lessons: list, sops: str, answers: str) -> tuple:
    user = (f"SOPs:\n{sops or '(none)'}\n\nOwner's answers to earlier questions:\n{answers or '(none)'}\n\n"
            "Lessons from past conversations (JSON):\n" + json.dumps(lessons, ensure_ascii=False)[:150000])
    text = await _ask(api_key, PLAYBOOK_SYSTEM, user, effort="high", max_tokens=32000)
    book, _, qs = text.partition("===QUESTIONS===")
    questions = [q.strip(" -*0123456789.)\t") for q in qs.splitlines() if q.strip(" -*")]
    return book.strip(), [q for q in questions if len(q) > 8][:12]


# ---------------------------------------------------------------- drafting

def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-zà-ÿ]{4,}", (text or "").lower())}


def similar(examples: list, text: str, k: int = 4) -> list:
    """The past conversations most like this ticket (by shared words)."""
    want = _words(text)
    if not want:
        return []
    scored = []
    for c in examples:
        have = _words(" ".join(t for d, t in c["turns"] if d == "in"))
        if have:
            scored.append((len(want & have) / (len(want | have) ** 0.5), c))
    scored.sort(key=lambda s: -s[0])
    return [c for s, c in scored[:k] if s > 0]


DRAFT_SYSTEM = """You write customer support replies for an online store, as its support team.
Follow the store's SOPs, the owner's answers and the playbook. Where they say nothing, follow the past replies shown.
Use the order details given (status, tracking, items, dates) instead of guessing; never invent tracking numbers, dates, refunds or policies. If something needs a decision you can't make from the rules (e.g. a refund outside policy), write the reply so it doesn't promise it, and add a line starting with "NOTE FOR AGENT:" at the very end explaining what to check.
Reply in the customer's language. Write only the email body: greeting, message, sign-off as the playbook says. No subject line."""


async def draft(api_key: str, knowledge: str, thread: str, orders: str, store: str, examples: list) -> str:
    ex = "\n\n".join(_render(c) for c in examples) or "(none)"
    user = (f"Store: {store}\n\n=== Knowledge ===\n{knowledge}\n\n"
            f"=== Similar past conversations ===\n{ex}\n\n"
            f"=== Customer's Shopify orders ===\n{orders or '(not found)'}\n\n"
            f"=== This ticket (oldest first) ===\n{thread}\n\n"
            "Write the next reply to the customer.")
    return await _ask(api_key, DRAFT_SYSTEM, user, effort="medium", max_tokens=8000)
