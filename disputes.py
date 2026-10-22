"""The Disputes desk: PayPal cases (inquiries, claims, chargebacks) and Airwallex card chargebacks in
one list, with what each needs from us and by when.

Every case is put in one shape (`from_paypal`, `from_airwallex`):

    state    respond   waiting for us (reply, evidence, accept...)
             waiting   waiting for the customer
             review    PayPal / the card bank is deciding
             closed    finished (outcome: won / lost / refunded / cancelled)
    actions  only what the provider says is allowed right now (PayPal's links, Airwallex's status)

The evidence pack is built from what ProfitDesk already knows about the order: the Shopify order
(products, amounts, customer), its parcels and every tracking scan (SCM), the customer's emails and
our replies (Inbox), refunds. It is rendered as a PDF (PayPal) or PNG pages (Airwallex accepts
images only). The AI (Claude Sonnet 5.5) drafts the reply / defence from the same facts and never
invents any: it only sees what the pack holds.
"""
import io
import json
import re
import textwrap
from datetime import datetime, timezone

import anthropic

MODEL = "claude-sonnet-5-5"

REASONS = {
    # PayPal
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "Item not received",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "Not as described",
    "UNAUTHORISED": "Unauthorised payment",
    "CREDIT_NOT_PROCESSED": "Refund not processed",
    "DUPLICATE_TRANSACTION": "Charged twice",
    "INCORRECT_AMOUNT": "Wrong amount",
    "PAYMENT_BY_OTHER_MEANS": "Paid another way",
    "CANCELED_RECURRING_BILLING": "Cancelled subscription",
    "PROBLEM_WITH_REMITTANCE": "Problem with payment",
    "OTHER": "Other",
    # Airwallex
    "PRODUCT_NOT_RECEIVED": "Item not received",
    "PRODUCT_UNACCEPTABLE": "Not as described",
    "MISREPRESENTATION": "Not as described",
    "COUNTERFEIT_PRODUCT": "Counterfeit",
    "FRAUDULENT": "Fraud (card not authorised)",
    "UNRECOGNIZED_CHARGE": "Doesn't recognise the charge",
    "NOT_RECOGNIZED": "Doesn't recognise the charge",
    "DUPLICATE_CHARGE": "Charged twice",
    "CANCELLED_PRODUCT": "Cancelled order",
    "PROCESSING_ERRORS": "Processing error",
    "AUTHORIZATION": "Authorisation problem",
    "CONSUMER_DISPUTE": "Customer dispute",
    "UNKNOWN": "Unknown",
}
STAGES = {"INQUIRY": "Inquiry", "CHARGEBACK": "Chargeback", "PRE_ARBITRATION": "Pre-arbitration",
          "ARBITRATION": "Arbitration", "RFI": "Information request", "PRE_CHARGEBACK": "Pre-chargeback"}

# what each action is called on the desk, and the access it needs
ACTIONS = {
    "send-message": ("Send message", "work"),
    "provide-evidence": ("Send evidence", "work"),
    "provide-supporting-info": ("Send supporting info", "work"),
    "appeal": ("Appeal", "work"),
    "escalate": ("Escalate to PayPal", "work"),
    "acknowledge-return-item": ("Confirm return received", "work"),
    "challenge": ("Challenge with evidence", "work"),
    "make-offer": ("Make an offer", "manage"),
    "accept-claim": ("Accept and refund", "manage"),
    "accept-offer": ("Accept the customer's offer", "manage"),
    "deny-offer": ("Decline the customer's offer", "work"),
    "accept": ("Accept (customer keeps the money)", "manage"),
}


def _money(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def from_paypal(d: dict, conn: dict) -> dict:
    """A PayPal dispute (list row or full details) in the desk's shape."""
    status = d.get("status") or ""
    outcome_code = (d.get("dispute_outcome") or {}).get("outcome_code") or ""
    if status == "RESOLVED":
        state = "closed"
    elif status == "WAITING_FOR_SELLER_RESPONSE":
        state = "respond"
    elif status == "WAITING_FOR_BUYER_RESPONSE":
        state = "waiting"
    elif status == "UNDER_REVIEW":
        state = "review"
    else:
        state = "respond" if d.get("seller_response_due_date") else "waiting"
    outcome = None
    if state == "closed":
        outcome = ("won" if "SELLER_FAVOUR" in outcome_code or outcome_code == "DENIED" else "cancelled" if "CANCELED" in outcome_code
                   else "refunded" if outcome_code in ("ACCEPTED", "RESOLVED_WITH_PAYOUT") else "lost"
                   if "BUYER_FAVOUR" in outcome_code else (outcome_code.lower() or "closed"))
    amt = d.get("dispute_amount") or {}
    txn = (d.get("disputed_transactions") or [{}])[0]
    rels = {l.get("rel") for l in d.get("links") or []}
    actions = [a for a in ACTIONS if a.replace("-", "_") in rels]
    opts = d.get("allowed_response_options") or {}
    messages = [{"by": {"BUYER": "customer", "SELLER": "us", "ARBITER": "paypal"}.get(m.get("posted_by"), "paypal"),
                 "at": m.get("time_posted"), "text": m.get("content") or "",
                 "docs": [x.get("name") for x in m.get("documents") or []]} for m in d.get("messages") or []]
    requested = sorted({e.get("evidence_type") for e in d.get("evidences") or []
                        if e.get("source") == "REQUESTED_FROM_SELLER" and e.get("evidence_type")})
    return {
        "key": f"pp:{conn['id']}:{d.get('dispute_id')}", "provider": "paypal", "conn_id": conn["id"],
        "account": conn.get("label") or "PayPal", "id": d.get("dispute_id"),
        "stage": STAGES.get(d.get("dispute_life_cycle_stage") or "", (d.get("dispute_life_cycle_stage") or "").title()),
        "status": status, "state": state, "outcome": outcome,
        "reason": REASONS.get(d.get("reason") or "", (d.get("reason") or "").replace("_", " ").title()),
        "reason_code": d.get("reason"),
        "amount": _money(amt.get("value")), "currency": amt.get("currency_code") or "",
        "due": d.get("seller_response_due_date"), "created": d.get("create_time"), "updated": d.get("update_time"),
        "customer": (txn.get("buyer") or {}).get("name") or "", "email": "",
        "txn_id": txn.get("seller_transaction_id") or "", "invoice": txn.get("invoice_number") or "",
        "txn_time": txn.get("create_time"), "txn_amount": _money((txn.get("gross_amount") or {}).get("value")),
        "messages": messages, "actions": actions, "requested_evidence": requested,
        "offer_types": ((opts.get("make_offer") or {}).get("offer_types")) or [],
        "accept_types": ((opts.get("accept_claim") or {}).get("accept_claim_types")) or [],
        "return_types": ((opts.get("acknowledge_return_item") or {}).get("acknowledgement_types")) or [],
        "channel": d.get("dispute_channel") or "", "full": bool(d.get("messages") is not None or d.get("links")),
    }


def from_airwallex(d: dict, conn: dict) -> dict:
    status = d.get("status") or ""
    state = {"REQUIRES_RESPONSE": "respond", "CHALLENGED": "review", "PENDING_DECISION": "review",
             "PENDING_CLOSURE": "review"}.get(status, "closed")
    outcome = {"WON": "won", "REVERSED": "won", "LOST": "lost", "EXPIRED": "lost",
               "ACCEPTED": "refunded"}.get(status) if state == "closed" else None
    reason = d.get("reason") or {}
    msgs = []
    if d.get("issuer_comment"):
        msgs.append({"by": "bank", "at": d.get("created_at"), "text": d["issuer_comment"], "docs": []})
    for c in d.get("challenge_details") or []:
        msgs.append({"by": "us", "at": c.get("challenged_at"), "text": f"Challenged ({c.get('stage') or ''}): "
                     + (c.get("product_description") or c.get("reason") or ""), "docs": []})
    for a in d.get("accept_details") or []:
        msgs.append({"by": "us", "at": a.get("accepted_at"), "text": f"Accepted: {a.get('reason') or ''} "
                     f"{a.get('description') or ''}".strip(), "docs": []})
    return {
        "key": f"awx:{conn['id']}:{d.get('id')}", "provider": "airwallex", "conn_id": conn["id"],
        "account": conn.get("label") or "Airwallex", "id": d.get("id"),
        "stage": STAGES.get(d.get("stage") or "", (d.get("stage") or "").title()),
        "status": status, "state": state, "outcome": outcome,
        "reason": REASONS.get(reason.get("type") or "", (reason.get("type") or "").replace("_", " ").title()),
        "reason_code": reason.get("type"), "reason_detail": reason.get("description") or "",
        "amount": _money(d.get("amount")), "currency": d.get("currency") or "",
        "due": d.get("due_at"), "created": d.get("created_at"), "updated": d.get("updated_at"),
        "customer": d.get("customer_name") or "", "email": "",
        "txn_id": d.get("payment_intent_id") or "", "invoice": d.get("merchant_order_id") or "",
        "card": d.get("card_brand") or d.get("payment_method_type") or "",
        "messages": msgs, "actions": ["challenge", "accept"] if status == "REQUIRES_RESPONSE" else [],
        "requested_evidence": [], "offer_types": [], "accept_types": [], "return_types": [],
        "ai_hint": ((d.get("ai_dispute_automation") or {}).get("recommendation") or {}),
        "full": True,
    }


def days_left(due: str):
    if not due:
        return None
    try:
        t = datetime.fromisoformat(due.replace("Z", "+00:00"))
    except ValueError:
        return None
    if not t.tzinfo:
        t = t.replace(tzinfo=timezone.utc)
    return round((t - datetime.now(timezone.utc)).total_seconds() / 86400, 1)


# ---------------------------------------------------------------- evidence pack

def pack_lines(case: dict, ctx: dict) -> list:
    """The evidence pack as (style, text) lines: h = heading, b = bold, t = text."""
    o = ctx.get("order") or {}
    L = [("h", f"Order {o.get('name') or '(not found)'} · {o.get('store') or ''}")]
    if o:
        L += [("t", f"Ordered: {o.get('created', '')[:16].replace('T', ' ')} UTC"),
              ("t", f"Customer: {o.get('customer') or case.get('customer') or ''}  {o.get('email') or ''}"),
              ("t", f"Ship to: {', '.join(x for x in (o.get('city'), o.get('country')) if x)}"),
              ("t", f"Paid: {o.get('total', 0):.2f} {o.get('currency', '')} · payment {case.get('txn_id') or ''}")]
        L.append(("b", "Items"))
        L += [("t", f"  {i['qty']} × {i['product']}") for i in o.get("items") or []]
    for n, s in enumerate(ctx.get("shipments") or [], 1):
        L.append(("h", f"Parcel {n}: {s.get('company') or ''} {s.get('number') or ''}"))
        L.append(("t", f"Shipped: {(s.get('fulfilled_at') or '')[:16].replace('T', ' ')} UTC · status: {s.get('status') or ''}"
                       + (f" · delivered {(s.get('delivered_at') or '')[:16].replace('T', ' ')}" if s.get("delivered_at") else "")))
        if s.get("tracking_url"):
            L.append(("t", f"Track it: {s['tracking_url']}"))
        for e in (s.get("events") or [])[:40]:
            L.append(("t", f"  {(e.get('time') or '')[:16].replace('T', ' ')}  {e.get('location') or ''}  {e.get('desc') or ''}"))
    if ctx.get("refunds"):
        L.append(("h", "Refunds already given"))
        L += [("t", f"  {r.get('at', '')[:10]}  {r.get('amount', 0):.2f} {r.get('currency', '')}  {r.get('note') or ''}")
              for r in ctx["refunds"]]
    if ctx.get("emails"):
        L.append(("h", "Emails with the customer"))
        for m in ctx["emails"][-12:]:
            L.append(("b", f"{(m.get('at') or '')[:16].replace('T', ' ')} · {'Customer' if m.get('direction') == 'in' else 'Our reply'}"))
            L += [("t", "  " + x) for x in (m.get("text") or "").strip().splitlines()[:25] if x.strip()]
    return L


FONT = __import__("os").path.join(__import__("os").path.dirname(__file__), "assets", "NotoSans.ttf")


def _font(size: int, bold: bool = False):
    """Noto Sans (bundled, SIL OFL): accents, Cyrillic, Greek. Bold = its Bold instance."""
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(FONT, size)
        try:
            f.set_variation_by_name("Bold" if bold else "Regular")
        except (OSError, ValueError):
            pass
        return f
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def render_pages(title: str, lines: list) -> list:
    """A4-ish white pages (PIL images) with the pack's text."""
    from PIL import Image, ImageDraw
    W, H, M = 1240, 1754, 70
    fonts = {"h": _font(30, True), "b": _font(24, True), "t": _font(22), "title": _font(36, True)}
    pages, page, draw, y = [], None, None, 0

    def new_page():
        nonlocal page, draw, y
        page = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(page)
        draw.text((M, M), title, font=fonts["title"], fill="#14181F")
        y = M + 60
        pages.append(page)
    new_page()
    for style, text in lines:
        wrap = 70 if style == "t" else 60
        for i, part in enumerate(textwrap.wrap(text, wrap) or [""]):
            h = 44 if style == "h" else 32
            if y + h > H - M:
                new_page()
            if style == "h" and i == 0:
                y += 14
            draw.text((M + (0 if i == 0 else 24), y), part, font=fonts[style], fill="#14181F" if style != "t" else "#30343B")
            y += h
    return pages


def pack_pdf(title: str, lines: list) -> bytes:
    pages = render_pages(title, lines)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


def pack_pngs(title: str, lines: list) -> list:
    out = []
    for p in render_pages(title, lines)[:8]:
        buf = io.BytesIO()
        p.save(buf, "PNG", optimize=True)
        out.append(buf.getvalue())
    return out


# ---------------------------------------------------------------- AI draft

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string", "enum": ["fight", "refund", "offer", "wait"]},
        "why": {"type": "string"},
        "message": {"type": "string"},
        "evidence_notes": {"type": "string"},
        "evidence_type": {"type": "string"},
        "offer_amount": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recommendation", "why", "message", "evidence_notes", "evidence_type", "offer_amount", "missing"],
    "additionalProperties": False,
}

DRAFT_SYSTEM = """You help an online store (Shopify, dropshipped from China, long delivery times) answer PayPal disputes and card chargebacks.

You get the case and the store's own records for the order (products, parcels with every tracking scan, emails with the customer, refunds). Use only these facts. Never invent tracking events, dates, amounts, promises or policies. If something important is missing, list it in "missing".

Return:
- recommendation: fight (we have proof, e.g. delivered / customer withdrew), refund (we can't win or it's fair), offer (partial refund or replacement makes sense), wait (in transit and still within the delivery estimate: reply and ask for patience).
- why: 1-3 short sentences for the team, in English.
- message: a reply to the customer (PayPal inquiry messages are seen by the customer), polite and short, in the customer's language (judge from their messages; English if none). Empty string for card chargebacks (no customer chat).
- evidence_notes: the defence for PayPal / the card bank, in English, factual, max 1800 characters: order date, what was bought, when it shipped, carrier + tracking number, the delivery scan (date, place) if any, what the customer said, refunds. Reference the attached evidence pack.
- evidence_type: the best PayPal evidence type: PROOF_OF_FULFILLMENT (shipped/delivered with tracking), PROOF_OF_REFUND, PROOF_OF_RETURN, ITEM_DESCRIPTION, OTHER.
- offer_amount: for "offer", a suggested partial refund in the dispute currency; otherwise null.
- missing: what would make the case stronger and the team should get (e.g. "delivery photo from the carrier")."""


async def draft(api_key: str, case: dict, lines: list) -> dict:
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=180)
    facts = "\n".join(text for _, text in lines)[:30000]
    convo = "\n".join(f"[{m.get('at', '')[:16]}] {m['by']}: {m['text']}" for m in case.get("messages") or [])[-8000:]
    user = (f"Case: {case['provider']} {case['stage']} · reason: {case['reason']} ({case.get('reason_code')})"
            f" {case.get('reason_detail') or ''}\nAmount: {case['amount']} {case['currency']} · status {case['status']}"
            f" · respond by {case.get('due') or 'n/a'}\nEvidence PayPal asked for: {', '.join(case.get('requested_evidence') or []) or 'none listed'}"
            f"\n\nMessages in the case:\n{convo or '(none)'}\n\nThe store's records:\n{facts}")
    try:
        resp = await client.messages.create(
            model=MODEL, max_tokens=4000, system=DRAFT_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
            messages=[{"role": "user", "content": user}])
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Anthropic didn't accept the API key (Settings → AI).") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic error {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Couldn't reach Anthropic.") from e
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError as e:
        raise RuntimeError("The AI's draft couldn't be read.") from e


def safe_name(name: str, ext: str) -> str:
    """PayPal accepts names of letters, digits, - _ , and spaces, one dot."""
    stem = re.sub(r"[^A-Za-z0-9 _,-]+", " ", name.rsplit(".", 1)[0]).strip()[:60] or "evidence"
    return f"{stem}.{ext}"
