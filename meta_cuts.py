"""Meta Ads → Budget cut rules (22 Oct 2026). One store can have several rules; each is switched on/off by itself.

Two kinds of trigger:
  account   "Budget cut on a poor day" (the first rule, 20 Oct): today's spend across the rule's campaigns reaches
            `spend` (and again every `every` more), overall ROAS below `roas` → each campaign that spent today with
            ROAS below `roas` is cut. Each checkpoint once a day.
  campaign  Each campaign by itself (owner, 22 Oct): once it has spent `camp_pct`% of its starting budget for the day
            (or `camp_spend` in money) and its ROAS is below `roas` → cut. Once per campaign per day.

Options shared by both: which campaigns (all / picked with switches), ROAS from EcomVital's pixel or Meta, cut by a %
of the budget at the start of the day or of the budget now (or pause instead), a budget floor, active hours on the
store's clock, put the budget back at midnight, and Autopilot vs Ask me first (proposals wait on the Manage tab).

This file only decides; app.py reads Meta, saves state and makes the changes. Config lives in setting
`meta_cutrules:<store>` (written only by people), what happened in `meta_cutstate:<store>:<rule id>` (written only by
the checker), starting budgets in `meta_startbud:<store>`.
"""

import secrets

NUMBERS = {  # field: (lowest, highest)
    "spend": (1.0, 10_000_000.0), "every": (0.0, 10_000_000.0), "camp_pct": (1.0, 1000.0),
    "camp_spend": (0.0, 10_000_000.0), "roas": (0.1, 100.0), "pct": (1.0, 90.0), "floor": (0.0, 10_000_000.0),
    "hour_from": (0, 23), "hour_to": (1, 24),
}
CHOICES = {"trigger": ("account", "campaign"), "scope": ("all", "picked"), "camp_by": ("pct", "amount"),
           "basis": ("pixel", "meta"), "action": ("cut", "pause"), "base": ("start", "now"), "mode": ("auto", "ask")}
FLAGS = ("reset",)

BASE = {"id": "", "name": "", "on": False, "started_at": None, "stopped_at": None, "by": "",
        "trigger": "campaign", "scope": "picked", "campaigns": {},
        "spend": 1000.0, "every": 0.0, "camp_by": "pct", "camp_pct": 35.0, "camp_spend": 0.0,
        "roas": 2.2, "basis": "pixel", "action": "cut", "pct": 50.0, "base": "start", "floor": 0.0,
        "hour_from": 0, "hour_to": 24, "reset": False, "mode": "auto"}

TEMPLATES = {
    "campaign": {"name": "Cut a campaign early in the day", "trigger": "campaign", "scope": "picked",
                 "camp_by": "pct", "camp_pct": 35.0, "roas": 2.2, "pct": 50.0, "base": "start"},
    "poorday": {"name": "Budget cut on a poor day", "trigger": "account", "scope": "all", "spend": 1000.0,
                "roas": 2.0, "pct": 50.0, "base": "now", "every": 0.0},
}

DONE_LABEL = {"cut": "cut today", "pause": "paused today", "proposed": "proposed today (waiting on the Manage tab)",
              "skip": "already at or below the cut", "failed": "Meta refused today"}

STATE = {"checked_day": None, "checked": None, "fired_day": None, "runs": [], "last": None,
         "done": {"day": None, "items": {}}, "watch": None}


def new_rule(template: str = "campaign", rid: str = "") -> dict:
    return {**BASE, **TEMPLATES.get(template, TEMPLATES["campaign"]), "id": rid or secrets.token_hex(3), "campaigns": {}}


def full(rule: dict) -> dict:
    return {**BASE, **rule}


def full_state(st: dict | None) -> dict:
    out = {**STATE, **(st or {})}
    if not isinstance(out.get("done"), dict):
        out["done"] = {"day": None, "items": {}}
    return out


def migrate(old: dict) -> tuple[dict, dict]:
    """The 20 Oct "Budget cut on a poor day" settings → rule "poorday" (same behaviour) + its state."""
    rule = {**new_rule("poorday", "poorday")}
    for k in ("on", "name", "spend", "roas", "pct", "every", "started_at", "stopped_at", "by"):
        if old.get(k) is not None:
            rule[k] = old[k]
    state = {k: old.get(k) for k in ("checked_day", "checked", "fired_day", "runs", "last") if old.get(k) is not None}
    return rule, full_state(state)


def apply(rule: dict, payload: dict) -> dict:
    """A person's changes to a rule's settings, checked. Raises ValueError with a plain message."""
    rule = full(rule)
    if "name" in payload:
        rule["name"] = str(payload["name"] or "").strip()[:80] or rule["name"]
    for k, (lo, hi) in NUMBERS.items():
        if k in payload and payload[k] not in (None, ""):
            try:
                v = float(str(payload[k]).replace(",", ""))
            except (TypeError, ValueError):
                raise ValueError(f"{k.replace('_', ' ')} must be a number.")
            v = min(hi, max(lo, v))
            rule[k] = int(v) if k.startswith("hour") else v
    for k, ok in CHOICES.items():
        if k in payload:
            if payload[k] not in ok:
                raise ValueError(f"{k} must be one of {', '.join(ok)}.")
            rule[k] = payload[k]
    for k in FLAGS:
        if k in payload:
            rule[k] = bool(payload[k])
    if "campaigns" in payload and isinstance(payload["campaigns"], dict):
        rule["campaigns"] = {str(i): str(n or "")[:200] for i, n in payload["campaigns"].items() if i}
    if "campaign" in payload and isinstance(payload["campaign"], dict):      # one switch: {id, name, on}
        c = payload["campaign"]
        cs = dict(rule.get("campaigns") or {})
        if c.get("on"):
            cs[str(c["id"])] = str(c.get("name") or "")[:200]
        else:
            cs.pop(str(c["id"]), None)
        rule["campaigns"] = cs
    if rule["hour_to"] <= rule["hour_from"]:
        raise ValueError("Active hours: the end has to be after the start.")
    return rule


def in_scope(rule: dict, row: dict) -> bool:
    return rule["scope"] == "all" or str(row["id"]) in (rule.get("campaigns") or {})


def roas_of(rule: dict, row: dict):
    s = row.get("spend") or 0
    if not s:
        return None
    return ((row.get("px_value") if rule["basis"] == "pixel" else row.get("value")) or 0) / s


def target(rule: dict, start: float, now: float) -> float:
    """The budget a cut leaves: pct off the starting budget or off the budget now, never under the floor."""
    base = start if rule["base"] == "start" and start else now
    return round(max(rule["floor"] or 0.0, base * (1 - rule["pct"] / 100)), 2)


def decide(rule: dict, state: dict, rows: list, starts: dict, today: str, hour: int) -> dict:
    """What the rule wants right now. rows = today's campaign rows (_mads_rows), starts = {campaign id: budget at the
    start of today}. Returns {"cuts": [...], "watch": [...], "note": str, "run": {...} | None, "state": {...}}:
    the caller acts on cuts and saves state (the checkpoint is saved before acting, so it never runs twice)."""
    rule, state = full(rule), full_state(state)
    rows = [r for r in rows if in_scope(rule, r)]
    done = state["done"]["items"] if state["done"].get("day") == today else {}
    out = {"cuts": [], "watch": [], "note": "", "run": None, "state": None}
    open_now = rule["hour_from"] <= hour < rule["hour_to"]

    if rule["trigger"] == "campaign":
        for r in rows:
            cid = str(r["id"])
            start = starts.get(cid) or r.get("daily_budget") or 0.0
            line = start * rule["camp_pct"] / 100 if rule["camp_by"] == "pct" else rule["camp_spend"]
            ro = roas_of(rule, r)
            w = {"id": cid, "name": r["name"], "status": r.get("effective"), "start": start or None,
                 "budget": r.get("daily_budget") or None, "spend": r.get("spend") or 0.0,
                 "spent_pct": (r.get("spend") or 0) / start if start else None, "trigger_at": line or None, "roas": ro}
            if cid in done:
                w["state"] = DONE_LABEL.get(done[cid].get("action"), "handled today")
            elif not r.get("daily_budget"):
                w["state"] = "budget on the ad sets (or lifetime): can't cut here"
            elif r.get("effective") != "ACTIVE":
                w["state"] = "not running"
            elif not line or (r.get("spend") or 0) < line:
                w["state"] = "waiting"
            elif ro is not None and ro >= rule["roas"]:
                w["state"] = "ROAS fine"
            elif not open_now:
                w["state"] = "would cut (outside active hours)"
            else:
                w["state"] = "cut now"
                out["cuts"].append({"id": cid, "name": r["name"], "start": start, "spend": r.get("spend") or 0.0,
                                    "roas": ro or 0.0,
                                    "why": f"spent {r.get('spend') or 0:,.2f} of its {start:,.2f} starting budget "
                                           f"(trigger {line:,.2f}), ROAS {ro or 0:.2f} below {rule['roas']:g}"})
            out["watch"].append(w)
        out["note"] = (f"{len(out['cuts'])} to cut" if out["cuts"] else "nothing to cut") if open_now else "outside active hours"
        return out

    # account trigger: checkpoints on today's total spend of the rule's campaigns
    spend = sum(r.get("spend") or 0 for r in rows)
    value = sum((r.get("px_value") if rule["basis"] == "pixel" else r.get("value")) or 0 for r in rows)
    roas = value / spend if spend else 0.0
    every = float(rule.get("every") or 0)
    checked = state["checked"] if state.get("checked_day") == today else None
    if checked is None and state.get("fired_day") == today:         # fired before checkpoints existed
        checked = rule["spend"]
    out["note"] = f"today's spend {spend:,.2f}, ROAS {roas:.2f}"
    if not open_now:
        out["note"] += " (outside active hours)"
        return out
    if (checked is not None and every <= 0) or spend < rule["spend"]:
        return out
    point = rule["spend"] + (int((spend - rule["spend"]) // every) * every if every > 0 else 0)
    if checked is not None and point <= checked:
        return out
    new = {**state, "checked_day": today, "checked": point}
    if new.get("checked_day") != state.get("checked_day"):
        new["runs"] = []
    out["run"] = {"point": point, "spend": round(spend, 2), "roas": round(roas, 2)}
    if roas < rule["roas"]:
        new["fired_day"] = today
        for r in rows:
            ro = roas_of(rule, r)
            if not r.get("spend") or (ro or 0) >= rule["roas"]:
                continue
            cid = str(r["id"])
            out["cuts"].append({"id": cid, "name": r["name"], "start": starts.get(cid) or r.get("daily_budget") or 0.0,
                                "spend": r["spend"], "roas": ro or 0.0,
                                "why": f"today's spend {spend:,.2f}, overall ROAS {roas:.2f} (below {rule['roas']:g}); "
                                       f"this campaign {ro or 0:.2f}"})
    out["state"] = new
    return out
