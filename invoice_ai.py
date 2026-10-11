"""Second reader for supplier invoices: Claude Sonnet 5.5 reads the original file (the PDF itself,
or every cell of an Excel sheet) on its own, and its reading is compared with invoices.parse.

    agreed     same orders, same amount per order, same total: saved as read
    review     they differ somewhere: the invoice's lines are left out of every cost until a
               person looks at the differences and picks the right reading
    ai_only    the normal reader found no lines at all; the AI's reading is used, but only
               when its orders add up to the invoice total it read

The key is the owner's Anthropic API key (Settings → AI).
"""
import base64
import io
import json
import re
from collections import defaultdict

import anthropic

import cog

MODEL = "claude-sonnet-5-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "supplier": {"type": "string"},
        "number": {"type": "string"},
        "date": {"type": "string"},
        "total": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "orders": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "order": {"type": "string"},
                "amount": {"type": "number"},
                "items": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "quantity": {"type": "integer"}},
                    "required": ["title", "quantity"], "additionalProperties": False}},
            },
            "required": ["order", "amount", "items"], "additionalProperties": False}},
    },
    "required": ["supplier", "number", "date", "total", "orders"],
    "additionalProperties": False,
}

SYSTEM = """You read supplier invoices for an online store (dropshipping suppliers in China bill the store for the orders they shipped).

Return every order on the invoice, in the order they appear:
- order: the store's order number exactly as printed (e.g. "AS13102", "16259CH", "#BR5728"). Never invent or fix one.
- amount: the total the supplier charges for that order, in the invoice currency (usually USD). If an order has several lines, add them up. If the table has an order-total column (e.g. "total_price" on the order's first row), use that figure once and don't add the line prices to it as well.
- items: each product in that order with its quantity (title as printed, variant included if shown).

Also return supplier (company name), number (the invoice number), date (the invoice date as YYYY-MM-DD, or "" if none) and total (the invoice's grand total as printed, or null if none is printed).

Copy every figure exactly; do not round or convert. Read every page and every row: missing one order is a serious error."""


class AIError(RuntimeError):
    pass


PRIVATE = re.compile(r"phone|street|address|zip|postal|e-?mail|city|^shipping name$|^name$|tracking", re.I)


def _xlsx_cells(data: bytes) -> str:
    """Every sheet's rows as tab-separated text, without empty columns (some exports carry hundreds)
    or the customer's personal details (name, phone, address), which the AI doesn't need."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head = rows[0]
        keep = [c for c in range(width) if any(r[c] for r in rows) and not PRIVATE.search(head[c] or "")]
        out.append(f"## Sheet {ws.title}")
        out += ["\t".join(r[c] for c in keep) for r in rows]
    return "\n".join(out)


async def read(api_key: str, data: bytes, filename: str = "") -> dict:
    """The AI's own reading of an invoice file: {supplier, number, date, total, orders, usage}."""
    if data[:4] == b"%PDF":
        content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                   "data": base64.b64encode(data).decode()}}]
    else:
        body = _xlsx_cells(data) if data[:2] == b"PK" else data.decode("utf-8", "ignore")
        if len(body) > 400000:                       # never let it read half a file
            raise AIError("The file is too big for one AI reading.")
        content = [{"type": "text", "text": (f"Excel invoice {filename}, every non-empty row (tab separated):\n\n"
                                             if data[:2] == b"PK" else f"Invoice {filename}:\n\n") + body}]
    content.append({"type": "text", "text": "Read this invoice."})
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=600)
    messages = [{"role": "user", "content": content}]
    usage = {"in": 0, "out": 0}
    for attempt in range(3):
        resp = await _ask(client, messages)
        usage["in"] += resp.usage.input_tokens
        usage["out"] += resp.usage.output_tokens
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            out = json.loads(text)
        except ValueError as e:
            raise AIError("The AI's reading couldn't be understood.") from e
        # its own check: the orders it read must add up to the total it read, or it skipped some
        got = round(sum(float(o["amount"] or 0) for o in out["orders"]), 2)
        if out.get("total") is None or abs(got - float(out["total"])) < 0.01 or attempt == 2:
            break
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"Your {len(out['orders'])} orders add up to {got:.2f}, but the invoice "
                                                 f"total is {float(out['total']):.2f}, so some orders are missing or "
                                                 "misread. Read the whole file again, every page and every row to the "
                                                 "end, and return the complete list."}]
    out["usage"] = usage
    out["attempts"] = attempt + 1
    return out


async def _ask(client, messages):
    try:
        async with client.messages.stream(
            model=MODEL, max_tokens=64000, system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=messages,
        ) as stream:
            resp = await stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise AIError("Anthropic didn't accept the API key (Settings → AI).") from e
    except anthropic.RateLimitError as e:
        raise AIError("Anthropic is busy (rate limit); try the double-check again in a minute.") from e
    except anthropic.APIStatusError as e:
        if "credit" in str(e).lower() or "billing" in str(e).lower():
            raise AIError("The Anthropic account is out of credit.") from e
        raise AIError(f"Anthropic error {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Couldn't reach Anthropic.") from e
    if resp.stop_reason == "max_tokens":
        raise AIError("The invoice is too long for one AI reading.")
    if resp.stop_reason == "refusal":
        raise AIError("The AI declined to read this file.")
    return resp


def _key(order: str) -> str:
    return cog.order_key(str(order or "").strip())


def compare(lines: list, total: float, ai: dict) -> list:
    """Differences between the normal reading (lines with order + amount, the invoice total) and
    the AI's. Empty = they agree to the cent on every order and on the total."""
    mine, theirs = defaultdict(float), defaultdict(float)
    for l in lines:
        mine[_key(l.get("order") or l.get("order_key"))] += l["amount"]
    for o in ai.get("orders") or []:
        theirs[_key(o["order"])] += float(o["amount"] or 0)
    diffs = []
    for k in sorted(set(mine) | set(theirs)):
        a, b = mine.get(k), theirs.get(k)
        if a is None:
            diffs.append({"order": k, "file": None, "ai": round(b, 2), "what": "Only the AI found this order"})
        elif b is None:
            diffs.append({"order": k, "file": round(a, 2), "ai": None, "what": "The AI didn't find this order"})
        elif abs(a - b) >= 0.01:
            diffs.append({"order": k, "file": round(a, 2), "ai": round(b, 2), "what": "Different amount"})
    printed = ai.get("total")
    if printed is not None and total is not None and abs(float(printed) - float(total)) >= 0.01:
        diffs.append({"order": "Invoice total", "file": round(total, 2), "ai": round(float(printed), 2),
                      "what": "Different invoice total"})
    lines_sum = sum(mine.values())
    if total is not None and abs(lines_sum - total) >= 0.01 and not any(d["order"] == "Invoice total" for d in diffs):
        diffs.append({"order": "Lines vs total", "file": round(lines_sum, 2), "ai": round(sum(theirs.values()), 2),
                      "what": f"The lines add up to {lines_sum:.2f}, not the invoice total {total:.2f}"})
    return diffs


def as_invoice(ai: dict) -> dict:
    """The AI's reading in the shape invoices.parse returns: one line per item, an order's amount
    shared across its items by quantity (cent-exact on the last one)."""
    lines = []
    for o in ai.get("orders") or []:
        items = [i for i in o.get("items") or [] if (i.get("title") or "").strip()] or [{"title": "?", "quantity": 1}]
        units = sum(max(1, int(i.get("quantity") or 1)) for i in items)
        left = round(float(o["amount"]), 2)
        for n, i in enumerate(items):
            q = max(1, int(i.get("quantity") or 1))
            amt = left if n == len(items) - 1 else round(float(o["amount"]) * q / units, 2)
            left = round(left - amt, 2)
            for k in range(q):                       # one line per item, like the suppliers' own invoices
                share = round(amt / q, 2) if k < q - 1 else round(amt - round(amt / q, 2) * (q - 1), 2)
                lines.append({"order": _key(o["order"]), "title": i["title"].strip(), "amount": share})
    date = ai.get("date") or ""
    total = ai.get("total")
    return {"supplier": (ai.get("supplier") or "").strip(), "number": (ai.get("number") or "").strip(),
            "date": date if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else "",
            "total": float(total) if total is not None else round(sum(l["amount"] for l in lines), 2),
            "lines": lines}
