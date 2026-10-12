"""Apps → Google Ads → Manage: the ads AI (Claude Sonnet 5.5), one conversation per store.

It reads the store's campaigns and product figures through the app's tools, turns what the person asks into
a plan (create a campaign, move products, change a budget ...) or a standing rule, and explains it. It never
changes Google Ads itself: a plan waits for the person's "Do it" click, a rule for "Turn on". app.py carries
out the plan step by step inside the store's caps, logs every change with what it was before, and can undo it.
"""
import json

import anthropic

MODEL = "claude-sonnet-5-5"
BETAS = ["server-side-fallback-2026-07-01"]


def _tool(name, description, props, required, strict=True):
    t = {"name": name, "description": description,
         "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}
    if strict:
        t["strict"] = True
    return t


_NUM = {"type": ["number", "null"]}
TOOLS = [
    _tool("get_campaigns", "Every campaign in this store's Google Ads account: id, name, status, type, daily budget, bidding, "
          "target ROAS, which products each asset group / ad group runs, and spend / Shopify sales / ROAS for the last 7 and "
          "30 days.", {}, []),
    _tool("find_products", "Products (all variants combined) with their Google spend, impressions, clicks, add to carts, Google "
          "conversion value, Shopify items sold and sales, and ROAS over the last N store days, optionally only inside one "
          "campaign, filtered by spend / ROAS / title. Use it to pick products for a plan or to answer questions.",
          {"days": {"type": "integer", "description": "1-60, today included"},
           "campaign": {"type": ["string", "null"], "description": "campaign id or exact name, or null for the whole account"},
           "min_spend": _NUM, "max_spend": _NUM, "min_roas": _NUM, "max_roas": _NUM,
           "basis": {"type": "string", "enum": ["shopify", "google"], "description": "ROAS from Shopify sales (default) or Google's conversion value"},
           "title_contains": {"type": ["string", "null"]},
           "limit": {"type": ["integer", "null"]}},
          ["days", "campaign", "min_spend", "max_spend", "min_roas", "max_roas", "basis", "title_contains", "limit"]),
    _tool("propose_plan", "Propose changes to Google Ads as ordered steps. Nothing changes until the person clicks Do it. Step "
          "kinds and their fields:\n"
          "- create_campaign: name, budget (per day, account currency), products (Shopify product ids), target_roas (optional, e.g. 2.5), status (ENABLED or PAUSED, default ENABLED). Makes a feed-only Performance Max campaign running just those products (all their variants).\n"
          "- move_products: products, from_campaign, to_campaign. Takes them out of one campaign and puts them in another. To use a campaign made earlier in the same plan, write to_campaign as \"new:<its name>\".\n"
          "- add_products: products, campaign.  - remove_products: products, campaign.\n"
          "- set_budget: campaign, amount (per day).  - set_status: campaign, status (ENABLED / PAUSED).  - set_target_roas: campaign, target_roas (or null for none).\n"
          "Campaigns by id or exact name. Every step also gets a short summary. Products must be Shopify product ids from find_products.",
          {"summary": {"type": "string", "description": "One line: what the plan does"},
           "steps": {"type": "array", "items": {"type": "object"}}},
          ["summary", "steps"], strict=False),
    _tool("save_rule", "Save a standing rule the app checks every hour. It's saved switched OFF; the person turns it on. Kinds and params:\n"
          "- roas_floor: campaign, days, min_roas, min_spend (only judge products that spent at least this), basis (shopify|google), move_to (campaign to send them to, e.g. a zombie campaign, or null to just take them out).\n"
          "- graduate: from_campaign (testing), to_campaign (scaling), max_spend (a product must get its result within this spend), need (sale|atc|either), days (look-back, default 14).\n"
          "- new_products: campaign (testing), days_new (products added to Shopify in the last N days), add_missing (true to add new products that aren't in the campaign).\n"
          "- scale_budget: campaign, days, min_roas, step (amount added to the daily budget), max_budget (never above), basis.\n"
          "Campaigns by id or exact name (a campaign the plan is about to create can be named).",
          {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["roas_floor", "graduate", "new_products", "scale_budget"]},
           "params": {"type": "object"}},
          ["name", "kind", "params"], strict=False),
    _tool("list_rules", "The store's rules: what each does, on or off, when it last ran and what it found.", {}, []),
    _tool("recent_changes", "The last changes: planned, waiting for approval, done, failed, undone.", {}, []),
]

SYSTEM = """You are the ads manager AI inside ProfitDesk for the Shopify store "{store}". You help run its Google Ads.
Today is {today} on the store's clock. Money is in {currency} (the Google Ads account's currency).
Store settings: managing {enabled}, mode {mode} ("approve" = rules wait for Approve; "auto" = rules act by themselves), daily budget cap per campaign {budget_cap}, cap for a new campaign's budget {new_budget_cap}.

How to work:
- Look before you act: get_campaigns for the structure, find_products for the products and figures. Never make up a figure or a product id.
- ROAS means Shopify sales ÷ Google spend unless the person says Google's own conversions. "Including all variants" is how find_products already counts.
- To change anything, use propose_plan. Spell out exactly what will happen (which campaign, how many products, budget) and say the person must click "Do it". Never say something is done.
- For "always / whenever / every day / keep an eye on" instructions, use save_rule (one rule per behaviour) and say they must click "Turn on". Explain the rule in one line.
- If a step needs a campaign that will be created in the same plan, create it first in the steps and refer to it as "new:<name>".
- Campaigns whose product list is "custom" (picked by labels / categories) can't be edited by ProfitDesk; say so and suggest an alternative.
- Budgets must stay within the caps above; if the person wants more, tell them to raise the cap in Manage settings.
- Never judge a product on tiny spend. For roas_floor always set min_spend (if the person didn't say, use roughly the store's
  average order value, e.g. 20-40, and tell them); for graduate set max_spend to what they call "small" (ask if unclear, default 15).
- Scaling a budget needs a cap: always set max_budget for scale_budget (ask, or use the store's cap) and say it.
- Keep answers short: what you found (a small table if useful), then the plan / rule."""


class BrainError(RuntimeError):
    pass


async def reply(api_key: str, history: list, run_tool, ctx: dict) -> dict:
    """history: [{"role", "content"}]; run_tool(name, input) -> dict. Returns {"text"}."""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=240)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-14:]
                if m.get("content") and m.get("role") in ("user", "assistant")]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    s = ctx["settings"]
    system = SYSTEM.format(store=ctx["store"], today=ctx["today"], currency=ctx["currency"],
                           enabled="on" if s.get("enabled") else "off", mode=s.get("mode"),
                           budget_cap=s.get("budget_cap"), new_budget_cap=s.get("new_budget_cap"))
    for _ in range(12):
        try:
            resp = await client.beta.messages.create(
                model=MODEL, max_tokens=12000, betas=BETAS, fallbacks="default", system=system, tools=TOOLS,
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
    return {"text": "That took too many steps. Try a narrower request."}
