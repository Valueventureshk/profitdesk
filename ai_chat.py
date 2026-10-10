"""The chat button: ask ProfitDesk anything.

Claude Sonnet 5.5 doesn't hold any figures itself. It calls the app's own tools
(the same logic the desks use) and explains the answer; documents are made by
the Reports desk and handed back as a download link. app.py runs the tools and
checks the person's desk access for each one.
"""
import json

import anthropic

MODEL = "claude-sonnet-5-5"
BETAS = ["server-side-fallback-2026-07-01"]

_RANGE = {
    "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
    "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
}


def _tool(name, description, props, required):
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required,
                             "additionalProperties": False}}


TOOLS = [
    _tool("list_stores", "The stores and store groups, with their ids (use an id as scope).", {}, []),
    _tool("get_profit", "Profit dashboard figures for a period: sales, orders, ad spend (Google/Meta), ROAS, "
          "fees, COG, net profit, margin, reserve held, net available; plus each store's totals when scope is "
          "all or a group.",
          {**_RANGE, "scope": {"type": "string", "description": "all, a store id, or g<group id>"}},
          ["start", "end", "scope"]),
    _tool("get_cash", "Cash flow right now: available now, receivable, held, ads payable, what arrives by "
          "tomorrow / 7 / 14 / 30 / 60 / 90 days, per account.", {}, []),
    _tool("get_cash_history", "Cash flow cards as they stood at 23:59 Hong Kong time on past days (saved every "
          "night): available now, held, receivable, available + receivable, ads payable, after ad bills, "
          "available by tomorrow / 7 / 14 / 30 / 60 / 90 days, per account. Use for 'end of day', 'last night', "
          "'how is cash trending'. Days before saving started have no snapshot: use get_cash_statement.",
          _RANGE, ["start", "end"]),
    _tool("get_cash_statement", "Cash statement between two dates (Hong Kong days, up to 92 days back): opening "
          "and closing 'available + receivable' (closing = at the end of the last day), money in and out, fees, "
          "moves between accounts. Works for any past day, but only gives available + receivable, not "
          "the other cards.", _RANGE, ["start", "end"]),
    _tool("get_expenses", "Actual payments out of Airwallex and PayPal for a period, by category (supplier "
          "payments, Meta ads, Google Ads, subscriptions, processing fees, refunds, chargebacks, salaries, "
          "other), with the biggest payees and what is still unsorted.", _RANGE, ["start", "end"]),
    _tool("get_cog", "COG for a period: sales, COG, COG %, how much is from invoices vs history vs estimates, "
          "and the products with the highest cost share.",
          {**_RANGE, "store": {"type": "string", "description": "all or a store id"}}, ["start", "end", "store"]),
    _tool("get_inbox", "Support inbox: tickets waiting on us, SCM / CS / inquiry counts, escalated, and the "
          "oldest tickets waiting with their AI summaries.", {}, []),
    _tool("create_report", "Make a downloadable spreadsheet (CSV) in the Reports desk and return its link. "
          "Types: profit_daily, profit_stores, cog_orders, invoices, cash_statement, tickets, ad_bills, "
          "cash_snapshots (cash at end of each day).",
          {"type": {"type": "string", "enum": ["profit_daily", "profit_stores", "cog_orders", "invoices",
                                               "cash_statement", "tickets", "ad_bills", "cash_snapshots"]},
           **_RANGE, "scope": {"type": "string", "description": "all, a store id, or g<group id>"}},
          ["type", "start", "end", "scope"]),
]

SYSTEM = """You are ProfitDesk's assistant for the owner and staff of a group of Shopify stores.
Answer questions about their business using the tools: profit, cash flow, COG, the support inbox and reports. Never make up a figure: if a tool can't give it, say so.
Today is {today} (Hong Kong time). "Today", "yesterday", "last week" (the 7 days ending yesterday), "this month" and similar mean dates on that clock.
Money is in {currency} unless a tool says otherwise; write amounts with their currency (e.g. A$1,234 for AUD, US$ for USD). Keep answers short and plain: lead with the answer, then the key numbers. Use a small table when comparing several stores or days.
For cash on a past day, prefer get_cash_history; if that day has no snapshot, use get_cash_statement and say only "available + receivable" can be rebuilt for it.
When someone asks for a document, file, export, spreadsheet or report, use create_report and give the download link as a Markdown link.
If a tool says the person has no access, tell them which desk they'd need."""


class ChatError(RuntimeError):
    pass


async def reply(api_key: str, history: list, run_tool, today: str, currency: str) -> dict:
    """history: [{"role": "user"|"assistant", "content": text}]. run_tool(name, input) -> dict.
    Returns {"text", "links": [...]}"""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=180)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-12:]
                if m.get("content") and m.get("role") in ("user", "assistant")]
    links = []
    for _ in range(8):
        try:
            resp = await client.beta.messages.create(
                model=MODEL, max_tokens=8000, betas=BETAS, fallbacks="default",
                system=SYSTEM.format(today=today, currency=currency),
                tools=TOOLS, output_config={"effort": "low"}, messages=messages)
        except anthropic.AuthenticationError as e:
            raise ChatError("Anthropic didn't accept the API key. Check it in Settings → AI.") from e
        except anthropic.RateLimitError as e:
            raise ChatError("The AI is busy right now. Try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise ChatError(f"Anthropic error {e.status_code}.") from e
        except anthropic.APIConnectionError as e:
            raise ChatError("Couldn't reach Anthropic.") from e
        if resp.stop_reason == "refusal":
            return {"text": "I can't help with that one.", "links": links}
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return {"text": text or "(no answer)", "links": links}
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            try:
                out = await run_tool(b.name, b.input)
                if b.name == "create_report" and out.get("download"):
                    links.append({"title": out.get("title"), "url": out["download"]})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                 "content": json.dumps(out, default=str)[:30000]})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": str(getattr(e, "detail", None) or e)[:500]})
        messages.append({"role": "user", "content": results})
    return {"text": "That took too many steps. Try a more specific question.", "links": links}
