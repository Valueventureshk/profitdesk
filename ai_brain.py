"""The AI brain: Claude Haiku 5.5 reads each incoming support email and decides
what it is and where it belongs.

One request per email, answered in a fixed JSON shape (structured output):

    category      customer | inquiry | legal | other
                  customer  someone who bought (or tried to): orders, delivery,
                            returns, refunds, product problems
                  inquiry   a genuine shopper question before buying (size, stock,
                            material, shipping to my country...)
                  legal     lawyers, trademark / copyright, chargeback or dispute
                            notices, consumer-authority letters, data requests
                  other     marketing, sales pitches, partnership offers, platform
                            notifications, newsletters, spam, receipts
    labels        for customer emails: scm (the order: where is it, tracking,
                  address, delivery, cancel) and/or cs (the product: quality,
                  damaged, wrong item or size, not as described, returns)
    ticket_id     the customer's existing ticket this continues, or null
    resolves      true when the email only closes things off ("thanks", "got it",
                  "received it") and needs no reply
    summary       one line about the ticket's issue (English)
    orders        order numbers mentioned

The key is the owner's Anthropic API key, pasted in Settings → AI.
"""
import json

import anthropic

MODEL = "claude-haiku-5-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["customer", "inquiry", "legal", "other"]},
        "labels": {"type": "array", "items": {"type": "string", "enum": ["scm", "cs"]}},
        "ticket_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "resolves": {"type": "boolean"},
        "summary": {"type": "string"},
        "orders": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string"},
    },
    "required": ["category", "labels", "ticket_id", "resolves", "summary", "orders", "language"],
    "additionalProperties": False,
}

SYSTEM = """You sort the support inbox of an online store (Shopify dropshipping, orders shipped by suppliers from China).

For each email decide:

category
- customer: a buyer writing about their order or product (where is my order, tracking, change address, cancel, refund, return, damaged, wrong size, quality, not as described). Includes replies to our own emails.
- inquiry: a genuine shopper question about the store's products before or without an order (sizes, stock, materials, shipping times or countries, discounts for a product they want).
- legal: lawyers, trademark/copyright/IP complaints, chargeback or payment dispute notices, consumer protection or government letters, GDPR/data requests, threats of legal action.
- other: everything else: marketing, sales pitches, agencies, partnership/influencer offers, app or platform notifications (Shopify, Google, Meta, PayPal receipts), newsletters, spam, automatic replies.

labels (only for customer; may be both, empty otherwise)
- scm: about the order itself: delivery status, tracking, shipping delay, address change, cancellation, missing parcel.
- cs: about the product: quality, damaged, wrong item/size/colour, not as described, return or exchange because of the product.

ticket_id: the customer's existing tickets are listed. Return the id of the one this email continues (same problem or same order, including "thanks" replies to it). Return null when it's a new problem, or about a different order (e.g. a second purchase). Never invent an id.

resolves: true only if this email needs no reply and closes the issue (e.g. "thanks, received it", "ok great", "got it, thank you"). False if it asks anything or reports a problem.

summary: one short English line describing the ticket's issue as it stands now (e.g. "Order BR4722 not received after 12 days, asks for tracking").
orders: order numbers mentioned (e.g. "#BR4722", "AS13102"), as written.
language: the email's language as an English word (e.g. "English", "French")."""


class AIError(RuntimeError):
    pass


def _email_text(msg: dict, limit: int = 6000) -> str:
    body = (msg.get("text") or "").strip()
    # Quoted older replies add cost without helping: keep the new part first.
    for marker in ("\nOn ", "\n-----Original Message", "\nLe ", "\nEl ", "\nAm ", "\nOp "):
        cut = body.find(marker)
        if cut > 200:
            body = body[:cut] + "\n[earlier messages quoted below, trimmed]"
            break
    return body[:limit]


async def triage(api_key: str, msg: dict, store: str, candidates: list) -> dict:
    """Ask Claude Haiku 5.5 about one incoming email. Returns the JSON answer."""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=60)
    tickets = "\n".join(
        f"- ticket {t['id']} ({t['status']}, {t['kind']}{', ' + t['labels'] if t['labels'] else ''}): "
        f"{t['summary'] or t['subject']} [orders: {t['orders'] or 'none'}]"
        for t in candidates) or "(none)"
    hint = ""
    if msg.get("reply_to_ticket"):
        hint = f"\nThe email's headers say it replies to a message in ticket {msg['reply_to_ticket']}."
    user = (f"Store: {store}\n"
            f"From: {msg.get('from_name') or ''} <{msg.get('from_addr')}>\n"
            f"To: {msg.get('to_addr')}\n"
            f"Subject: {msg.get('subject') or '(none)'}\n"
            f"Attachments: {', '.join(a.get('name', '') for a in msg.get('attachments_list', [])) or 'none'}\n"
            f"This customer's existing tickets:\n{tickets}{hint}\n\n"
            f"Email:\n{_email_text(msg)}")
    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Anthropic didn't accept the API key. Check it in Settings → AI.") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError("The Anthropic key isn't allowed to use Claude Haiku 5.5.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Anthropic is rate limiting; emails will be sorted on the next run.") from e
    except anthropic.APIStatusError as e:
        if "credit" in str(e).lower() or "billing" in str(e).lower():
            raise AIError("The Anthropic account is out of credit. Add credit at console.anthropic.com.") from e
        raise AIError(f"Anthropic error {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Couldn't reach Anthropic.") from e
    if resp.stop_reason == "refusal":
        return {"category": "customer", "labels": [], "ticket_id": None, "resolves": False,
                "summary": (msg.get("subject") or "")[:120], "orders": [], "language": "",
                "note": "AI declined to sort this email; check it by hand"}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except ValueError as e:
        raise AIError("The AI's answer couldn't be read.") from e
    out["usage"] = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
    return out
