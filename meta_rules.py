"""Apps → Meta Ads → Manage: standing rules, checked every hour for stores whose managing is on.
What a rule wants becomes a proposal that waits for a person's "Do it" (Ask me first).

Kinds (params; every rule also has level, days, basis and an optional campaign filter):
    no_sale       pause an ad / ad set that spent at least min_spend over the last N days with no purchase
    roas_floor    pause an ad / ad set / campaign whose ROAS over N days is under min_roas (once it spent min_spend)
    scale_budget  raise an ad set's / campaign's daily budget by step_pct % when its ROAS over N days is at least
                  min_roas (and it spent min_spend); never above max_budget or the store's cap; once a day
basis: "meta" = Meta's own purchase count / value, "pixel" = EcomVital's pixel (Tracking, matched by UTM names).
Only things that are running (effective status ACTIVE) are judged.
"""
KINDS = ("no_sale", "roas_floor", "scale_budget")
NAMES = {"no_sale": "Pause when no sale", "roas_floor": "Pause below a ROAS", "scale_budget": "Raise budget on winners"}
LEVEL_WORD = {"ad": "ad", "adset": "ad set", "campaign": "campaign"}
DEFAULTS = {
    "no_sale": {"level": "ad", "days": 3, "min_spend": 30, "basis": "meta", "campaign": ""},
    "roas_floor": {"level": "adset", "days": 3, "min_spend": 50, "min_roas": 1.5, "basis": "meta", "campaign": ""},
    "scale_budget": {"level": "adset", "days": 3, "min_spend": 50, "min_roas": 3, "step_pct": 20, "max_budget": 200,
                     "basis": "meta", "campaign": ""},
}


def clean(kind: str, params: dict) -> dict:
    if kind not in KINDS:
        raise ValueError("Unknown rule kind.")
    p = {**DEFAULTS[kind], **{k: v for k, v in (params or {}).items() if k in DEFAULTS[kind]}}
    allowed = {"no_sale": ("ad", "adset"), "roas_floor": ("ad", "adset", "campaign"), "scale_budget": ("adset", "campaign")}[kind]
    if p["level"] not in allowed:
        p["level"] = allowed[0]
    p["basis"] = "pixel" if p["basis"] == "pixel" else "meta"
    p["days"] = max(1, min(int(float(p["days"] or 1)), 30))
    for k in ("min_spend", "min_roas", "step_pct", "max_budget"):
        if k in p:
            p[k] = max(0.0, float(p[k] or 0))
    if kind == "scale_budget":
        p["step_pct"] = min(p["step_pct"] or 20, 100)
    p["campaign"] = str(p.get("campaign") or "").strip()[:200]
    return p


def describe(kind: str, p: dict, cur: str = "") -> str:
    lv = LEVEL_WORD[p["level"]]
    where = f" in campaigns named “{p['campaign']}”" if p.get("campaign") else ""
    by = "EcomVital pixel" if p["basis"] == "pixel" else "Meta's count"
    if kind == "no_sale":
        return f"Pause any {lv}{where} that spent {cur}{p['min_spend']:g}+ in the last {p['days']} days with no purchase ({by})."
    if kind == "roas_floor":
        return (f"Pause any {lv}{where} whose ROAS over the last {p['days']} days is under {p['min_roas']:g} "
                f"once it spent {cur}{p['min_spend']:g} ({by}).")
    return (f"Raise the daily budget of any {lv}{where} by {p['step_pct']:g}% when its ROAS over the last {p['days']} days "
            f"is {p['min_roas']:g}+ (spent {cur}{p['min_spend']:g}+), never above {cur}{p['max_budget']:g}, once a day ({by}).")


def result(row: dict, basis: str) -> tuple:
    """(purchases, value) on the rule's basis."""
    if basis == "pixel":
        return row.get("px_purchases") or 0, row.get("px_value") or 0.0
    return row.get("purchases") or 0, row.get("value") or 0.0


def evaluate(kind: str, p: dict, rows: list, cap: float = None) -> list:
    """rows: the level's objects with figures for the rule's window ({id, name, campaign, effective, daily_budget,
    spend, purchases, value, px_purchases, px_value}). Returns proposals {change, id, name, level, to, reason}."""
    out = []
    want = (p.get("campaign") or "").lower()
    for r in rows:
        if r.get("effective") != "ACTIVE":
            continue
        if want and want not in (r.get("campaign") or r.get("name") or "").lower():
            continue
        spend = r.get("spend") or 0.0
        if spend < (p.get("min_spend") or 0) or spend <= 0:
            continue
        n, value = result(r, p["basis"])
        roas = value / spend if spend else 0.0
        lv = LEVEL_WORD[p["level"]]
        if kind == "no_sale" and not n:
            out.append({"change": "status", "to": "PAUSED", "id": r["id"], "name": r["name"], "level": p["level"],
                        "reason": f"{lv} spent {spend:.2f} in {p['days']} days, no purchase"})
        elif kind == "roas_floor" and roas < p["min_roas"]:
            out.append({"change": "status", "to": "PAUSED", "id": r["id"], "name": r["name"], "level": p["level"],
                        "reason": f"ROAS {roas:.2f} over {p['days']} days (under {p['min_roas']:g}), spent {spend:.2f}"})
        elif kind == "scale_budget" and roas >= p["min_roas"] and r.get("daily_budget"):
            caps = [x for x in (p.get("max_budget"), cap) if x]
            limit = min(caps) if caps else None
            new = round(r["daily_budget"] * (1 + p["step_pct"] / 100), 2)
            if limit:
                new = min(new, limit)
            if new > r["daily_budget"] + 0.009:
                out.append({"change": "budget", "to": new, "from": r["daily_budget"], "id": r["id"], "name": r["name"],
                            "level": p["level"], "reason": f"ROAS {roas:.2f} over {p['days']} days, spent {spend:.2f}"})
    return out
