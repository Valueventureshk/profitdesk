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
    _tool("get_profit", "Profit dashboard figures for a period: sales (full order value on the order day), orders, ad spend "
          "(Google/Meta), ROAS, fees (PayPal / Airwallex / Shopify), refunds (on the day refunded) and chargebacks, COG, net profit, margin, reserve held, net available; plus each store's totals when scope is "
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
    _tool("get_price_changes", "Invoice price changes still to decide in the COG desk: product, store, old and "
          "new cost (USD), cheaper or dearer, 'to decide' or 'checking' with the supplier, how many orders.",
          {"direction": {"type": "string", "enum": ["all", "down", "up"], "description": "down = cheaper"},
           "store": {"type": "string", "description": "all or a store id"},
           "product_contains": {"type": "string", "description": "part of a product name, or empty"}},
          ["direction", "store", "product_contains"]),
    _tool("propose_price_decision", "Owners only. Propose a decision on invoice price changes: use_new (new price "
          "becomes the product's cost and re-costs its orders), checking (asked the supplier), keep_old (supplier "
          "corrected it). This does NOT change anything: it shows the person a Confirm button. Say clearly that "
          "they need to click Confirm. Use direction down for 'cheaper than history'.",
          {"action": {"type": "string", "enum": ["use_new", "checking", "keep_old"]},
           "direction": {"type": "string", "enum": ["all", "down", "up"]},
           "store": {"type": "string", "description": "all or a store id"},
           "product_contains": {"type": "string", "description": "part of a product name, or empty for all"}},
          ["action", "direction", "store", "product_contains"]),
    _tool("create_report", "Make a downloadable spreadsheet (CSV) in the Reports desk and return its link. "
          "Types: profit_daily, profit_stores, cog_orders, invoices, cash_statement, tickets, ad_bills, "
          "cash_snapshots (cash at end of each day).",
          {"type": {"type": "string", "enum": ["profit_daily", "profit_stores", "cog_orders", "invoices",
                                               "cash_statement", "tickets", "ad_bills", "cash_snapshots"]},
           **_RANGE, "scope": {"type": "string", "description": "all, a store id, or g<group id>"}},
          ["type", "start", "end", "scope"]),
]

_STORE = {"store": {"type": "string", "description": "a store id (see list_stores)"}}
_NUMQ = {"type": ["number", "null"]}
TOOLS += [
    _tool("store_health", "Health check of one store or all: Shopify connection + permissions, product problems (active products "
          "without image / price / stock), tracking pixel (events, last event), Google conversion tracking (which purchase "
          "action Google bids on, what it recorded, ProfitDesk uploads, how many Shopify orders Google sees), Meta link, "
          "COG / invoices (how much of the last 14 days is costed from invoices vs estimates, last invoice uploaded), inbox "
          "waiting. Returns problems first.", {"store": {"type": "string", "description": "a store id, or all"}}, ["store"]),
    _tool("tracking_live", "Live pixel events from the ProfitDesk tracking pixel, per hour, split by where the shopper came "
          "from (meta, google, tiktok, other): adds to cart, checkouts, purchases. For 'how many adds to cart from Meta in "
          "the last hour'.", {"store": {"type": "string", "description": "a store id, or all"},
                              "hours": {"type": "integer", "description": "1-48"}}, ["store", "hours"]),
    _tool("google_campaigns", "A store's Google Ads campaigns: status, daily budget, target ROAS, products, and today's and the "
          "last 7 days' spend, Shopify sales, ROAS, clicks, conversions, add to carts; plus the store's breakeven ROAS.",
          _STORE, ["store"]),
    _tool("google_products", "A store's products in Google Ads (all variants combined) over the last N days: spend, Shopify "
          "sales and items sold, ROAS, add to carts, clicks, impressions; optionally one campaign and filters.",
          {**_STORE, "days": {"type": "integer"}, "campaign": {"type": ["string", "null"]},
           "min_spend": _NUMQ, "max_roas": _NUMQ, "min_roas": _NUMQ, "limit": {"type": ["integer", "null"]}},
          ["store", "days", "campaign", "min_spend", "max_roas", "min_roas", "limit"]),
    _tool("propose_google_change", "Propose changes to a store's Google Ads (needs Manage on Google Ads). Nothing changes until "
          "the person confirms. Steps (in order), each {kind, ...}: set_budget {campaign, amount}, set_status {campaign, "
          "status ENABLED|PAUSED}, move_products {products, from_campaign, to_campaign}, add_products {products, campaign}, "
          "remove_products {products, campaign}, create_campaign {name, budget, products, status ENABLED|PAUSED, countries [codes "
          "like AU], target_roas (optional)}. A new campaign is always Performance Max, feed only (ads come only from the "
          "product feed, no assets), bidding to maximise conversion value (plus target ROAS if given), starting now with no "
          "end date, targeting people in the given countries. Campaigns by id or exact name; to use a campaign made earlier "
          "in the same plan write \"new:<its name>\". Products are Shopify product ids (all their variants are used). "
          "Campaigns that pick products by labels / categories (Adon labels) CAN have products taken out (they're excluded "
          "inside the label) and put back; adding brand-new products into a label campaign needs the label. Put "
          "create_campaign first, then move/remove. Google checks the plan in test mode before it's shown. 'Scale by 20%' = "
          "set_budget to budget x 1.2.",
          {**_STORE, "summary": {"type": "string"}, "steps": {"type": "array", "items": {"type": "object"}}},
          ["store", "summary", "steps"], ),
    _tool("google_product_issues", "Products Google won't show (disapproved / limited / not eligible) for a store's Google Ads "
          "account, with Google's reasons, and links to each product in Shopify admin and on the store. Read only.",
          {**_STORE}, ["store"]),
    _tool("find_shopify_products", "Find products in a store's Shopify catalogue by title, status or tag, with their Shopify "
          "admin link and store link (use for 'send me the link to …').",
          {**_STORE, "query": {"type": "string", "description": "words in the title, or a Shopify search like status:draft"},
           "limit": {"type": ["integer", "null"]}}, ["store", "query", "limit"]),
    _tool("create_schedule", "Save a scheduled message: at a time of day (and days of the week) the assistant runs the "
          "instruction and sends the answer to this person (e.g. 'performance report for all stores today' at 17:00).",
          {"name": {"type": "string"}, "instruction": {"type": "string", "description": "what to do / report each time"},
           "time": {"type": "string", "description": "HH:MM 24h"},
           "days": {"type": "string", "description": "daily, weekdays, or comma list like mon,wed,fri"},
           "timezone": {"type": "string", "description": "IANA zone, default Asia/Hong_Kong"}},
          ["name", "instruction", "time", "days", "timezone"]),
    _tool("create_alert", "Save an alert checked every few minutes; the person gets a message when it triggers (once per hour "
          "slot). Kinds: meta_atc_hour (adds to cart from Meta ads in one clock hour >= threshold), atc_hour (all sources), "
          "purchases_hour, google_tracking_stopped (no Google conversion upload for a store in 24 h while it had Google "
          "visitors), no_events (a store's pixel silent for threshold hours).",
          {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["meta_atc_hour", "atc_hour", "purchases_hour",
                                                                          "google_tracking_stopped", "no_events"]},
           "store": {"type": "string", "description": "a store id, or all"}, "threshold": {"type": "number"}},
          ["name", "kind", "store", "threshold"]),
    _tool("list_schedules_alerts", "This person's scheduled messages and alerts.", {}, []),
    _tool("delete_schedule_alert", "Delete one of this person's schedules or alerts by id.", {"id": {"type": "integer"}}, ["id"]),
]
for _t in TOOLS:
    if _t["name"] == "propose_google_change":
        _t.pop("strict", None)

SYSTEM = """You are ProfitDesk's assistant for the owner and staff of a group of Shopify stores.
Answer questions about their business using the tools: profit, cash flow, COG, the support inbox and reports. Never make up a figure: if a tool can't give it, say so.
Today is {today} (Hong Kong time). "Today", "yesterday", "last week" (the 7 days ending yesterday), "this month" and similar mean dates on that clock.
Money is in {currency} unless a tool says otherwise; write amounts with their currency (e.g. A$1,234 for AUD, US$ for USD). Keep answers short and plain: lead with the answer, then the key numbers. Use a small table when comparing several stores or days.
For cash on a past day, prefer get_cash_history; if that day has no snapshot, use get_cash_statement and say only "available + receivable" can be rebuilt for it.
When someone asks for a document, file, export, spreadsheet or report, use create_report and give the download link as a Markdown link.
If a tool says the person has no access, tell them which desk they'd need.
For "check everything / store health / are things working" use store_health. For live events ("adds to cart in the last hour") use tracking_live.
Scheduled reports and alerts: create them with create_schedule / create_alert and confirm the time, days and threshold in one line.
You can propose changes with the propose_ tools (owners only). They never change anything themselves: the person sees the action with a Confirm button. Before proposing, check the list (e.g. get_price_changes) and say what will change. Never claim something was done until they confirm."""


class ChatError(RuntimeError):
    pass


async def reply(api_key: str, history: list, run_tool, today: str, currency: str) -> dict:
    """history: [{"role": "user"|"assistant", "content": text}]. run_tool(name, input) -> dict.
    Returns {"text", "links": [...]}"""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=180)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-12:]
                if m.get("content") and m.get("role") in ("user", "assistant")]
    links, actions = [], []
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
            return {"text": "I can't help with that one.", "links": links, "actions": actions}
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return {"text": text or "(no answer)", "links": links, "actions": actions}
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            try:
                out = await run_tool(b.name, b.input)
                if b.name == "create_report" and out.get("download"):
                    links.append({"title": out.get("title"), "url": out["download"]})
                if out.get("proposed_action"):
                    actions.append(out["proposed_action"])
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                 "content": json.dumps(out, default=str)[:30000]})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": str(getattr(e, "detail", None) or e)[:500]})
        messages.append({"role": "user", "content": results})
    return {"text": "That took too many steps. Try a more specific question.", "links": links, "actions": actions}
