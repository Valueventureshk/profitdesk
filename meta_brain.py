"""Apps → Meta Ads → Manage: the ads AI (Claude Sonnet 5.5), one conversation per store.

It reads the store's campaigns / ad sets / ads with their results through the app's tools, and turns what the
person asks into a plan (pause / switch on, change a daily budget) or a standing rule. It never changes Meta
itself: a plan waits for the person's "Do it", a rule for "Turn on". app.py checks every step with Meta first
(validate-only), logs what it was before, and can undo it.
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


TOOLS = [
    _tool("get_results", "Campaigns, ad sets or ads of this store's Meta ad account with status, daily budget, and over the "
          "last N days (ad account days, today included): spend, impressions, link clicks, CTR, CPC, Meta's purchases / "
          "purchase value / ROAS / adds to cart, and EcomVital's own pixel purchases / value / ROAS (matched by the UTM names; "
          "missing when the ad's UTMs don't carry its names).",
          {"level": {"type": "string", "enum": ["campaign", "adset", "ad"]},
           "days": {"type": "integer", "description": "1-30"},
           "only_active": {"type": "boolean", "description": "true = only things running now"}},
          ["level", "days", "only_active"]),
    _tool("propose_plan", "Propose changes as ordered steps. Nothing changes until the person clicks Do it. Each step: "
          "change (\"status\" or \"budget\"), level (campaign / adset / ad), id (from get_results), to (ACTIVE / PAUSED for "
          "status; the new daily budget in the account currency for budget), reason (one short line with the figures).",
          {"summary": {"type": "string"},
           "steps": {"type": "array", "items": {"type": "object", "properties": {
               "change": {"type": "string", "enum": ["status", "budget"]},
               "level": {"type": "string", "enum": ["campaign", "adset", "ad"]},
               "id": {"type": "string"}, "to": {"type": ["string", "number"]}, "reason": {"type": "string"}},
               "required": ["change", "level", "id", "to", "reason"], "additionalProperties": False}}},
          ["summary", "steps"], strict=False),
    _tool("save_rule", "Save a standing rule the app checks every hour (saved switched OFF; the person turns it on). Kinds:\n"
          "- no_sale: level (ad|adset), days, min_spend → pause what spent min_spend with no purchase.\n"
          "- roas_floor: level (ad|adset|campaign), days, min_spend, min_roas → pause what is under min_roas.\n"
          "- scale_budget: level (adset|campaign), days, min_spend, min_roas, step_pct, max_budget → raise the daily budget "
          "by step_pct % (never above max_budget), once a day.\n"
          "Every kind also takes basis (meta = Meta's purchase count, pixel = EcomVital's pixel) and campaign (only campaigns "
          "whose name contains this text, or empty for all).",
          {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["no_sale", "roas_floor", "scale_budget"]},
           "params": {"type": "object"}}, ["name", "kind", "params"], strict=False),
    _tool("list_rules", "The store's rules: what each does, on or off, last run and what it found.", {}, []),
    _tool("recent_changes", "The last proposals and changes: waiting, done, failed, undone, rejected.", {}, []),
]

SYSTEM = """You are the Meta ads manager AI inside EcomVital for the Shopify store "{store}". You help run its Meta (Facebook / Instagram) ads.
Today is {today} on the store's clock. Money is in {currency} (the Meta ad account's currency).
Store settings: managing {enabled}, mode "Ask me first" (every change waits for the person's Do it), daily budget cap {budget_cap}.

How to work:
- Look before you act: get_results at the level you need. Never make up a figure or an id.
- ROAS = purchase value ÷ spend. Say which count you use: Meta's own (it credits views and clicks up to 7 days) or EcomVital's
  pixel (last click). When they disagree a lot, show both.
- To change anything, use propose_plan, spell out exactly what will happen, and say the person must click "Do it". Never say
  something is done.
- For "always / whenever / keep an eye on" instructions, use save_rule (one rule per behaviour) and say they must click
  "Turn on". Explain the rule in one line.
- Don't judge on tiny spend: wait until something spent at least about the store's average order value, and say so.
- Budgets never above the cap; if the person wants more, tell them to raise the cap in Manage settings.
- Meta's learning phase: changing a budget by more than ~20% at a time, or pausing and restarting often, resets learning.
  Warn when a plan does that.
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
                           enabled="on" if s.get("enabled") else "off", budget_cap=s.get("budget_cap"))
    for _ in range(10):
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
