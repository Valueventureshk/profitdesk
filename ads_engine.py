"""Apps → Google Ads → Manage: the numbers the ads AI and the rules work from, and the rules themselves.

Per product (all its variants together), per campaign, over the last N store days:
    Google      cost, impressions, clicks, conversions, conversion value, add to carts (if Google tracks them)
    Shopify     items sold and sales of the product (all channels, the store's own days)
    ROAS        Shopify sales / Google cost (basis "shopify", the desk's), or Google's value / cost ("google")

Rules (each a plain dict saved in ads_rules, run every hour; what they want becomes actions):
    roas_floor    products in a campaign whose ROAS over N days is under X (with at least min_spend spent):
                  take them out, optionally into another campaign ("zombie")
    graduate      products in a testing campaign that got a sale / add to cart while spending at most Y:
                  move them to the scaling campaign
    new_products  products added to Shopify in the last N days: are they in the testing campaign, getting
                  impressions? Optionally add the missing ones
    scale_budget  a campaign whose ROAS over N days is at least X: raise its daily budget by Z, once a day,
                  never above the cap
The app decides whether an action waits for Approve or runs straight away (the store's Autopilot setting).
"""
from datetime import date, timedelta

import google_desk

KINDS = ("roas_floor", "graduate", "new_products", "scale_budget")


def window(today: date, days: int):
    days = max(1, min(int(days or 1), 90))
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def product_stats(days_rows: list, lines: list, start: str, end: str, campaign: str = None) -> dict:
    """{product id: {cost, impressions, clicks, conversions, value, atc, qty, sales, items: set, campaigns: set,
    first_day}} for start..end. Google rows (gads_manage.product_days) by account day; Shopify lines by store day."""
    out = {}

    def slot(pid):
        return out.setdefault(pid, {"cost": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "value": 0.0,
                                    "atc": 0.0, "qty": 0, "sales": 0.0, "items": set(), "campaigns": set(), "first_day": None})
    for r in days_rows:
        if not r["item"] or not (start <= r["date"] <= end) or (campaign and r["campaign"] != str(campaign)):
            continue
        pid = google_desk.parse_item(r["item"])[0]
        s = slot(pid)
        for k in ("cost", "impressions", "clicks", "conversions", "value", "atc"):
            s[k] += r[k]
        s["items"].add(r["item"])
        if r["cost"] or r["impressions"]:
            s["campaigns"].add(r["campaign"])
            s["first_day"] = min(s["first_day"] or r["date"], r["date"])
    for l in lines:
        if l.get("cancelled") or not (start <= (l.get("day") or "") <= end):
            continue
        pid = str(l.get("product_id") or "").rsplit("/", 1)[-1]
        if not pid or (campaign and pid not in out):
            continue
        s = slot(pid)
        s["qty"] += int(l.get("quantity") or 0)
        s["sales"] += float(l.get("subtotal") or 0)
    return out


def roas(s: dict, basis: str = "shopify"):
    if not s["cost"]:
        return None
    return (s["value"] if basis == "google" else s["sales"]) / s["cost"]


def campaign_stats(days_rows: list, lines: list, start: str, end: str, campaign: str, campaign_cost: float = None,
                   basis: str = "shopify") -> dict:
    """A campaign's cost (its whole spend when given, else its products'), and the Shopify sales of the products
    it spent on, with ROAS on the chosen basis."""
    ps = product_stats(days_rows, lines, start, end, campaign)
    cost = campaign_cost if campaign_cost is not None else sum(s["cost"] for s in ps.values())
    sales = sum(s["sales"] for s in ps.values() if s["cost"] or s["impressions"])
    value = sum(s["value"] for s in ps.values())
    got = value if basis == "google" else sales
    return {"cost": cost, "sales": sales, "value": value, "roas": got / cost if cost else None, "products": len(ps)}


def describe(rule: dict, names: dict) -> str:
    """A rule in plain words, for the list. names: {campaign id: name}."""
    p, k = rule["params"], rule["kind"]
    n = lambda c: f"“{names.get(str(c), c)}”" if c else "—"
    if k == "roas_floor":
        to = f" and move them to {n(p.get('move_to'))}" if p.get("move_to") else ""
        return (f"In {n(p['campaign'])}: any product with ROAS under {p['min_roas']} over {p['days']} days "
                f"(at least {p.get('min_spend', 0)} spent) — take it out{to}.")
    if k == "graduate":
        need = {"sale": "a sale", "atc": "an add to cart", "either": "a sale or an add to cart"}[p.get("need", "either")]
        return (f"In {n(p['from_campaign'])}: a product that gets {need} within {p.get('max_spend')} spend "
                f"(last {p.get('days', 14)} days) — move it to {n(p['to_campaign'])}.")
    if k == "new_products":
        add = " Add any that are missing." if p.get("add_missing") else ""
        return (f"Products added to Shopify in the last {p.get('days_new', 7)} days: check they're in {n(p['campaign'])} "
                f"and getting impressions.{add}")
    if k == "scale_budget":
        return (f"{n(p['campaign'])}: when ROAS over {p['days']} days is {p['min_roas']} or more, raise the daily budget "
                f"by {p['step']} (once a day, never above {p.get('max_budget') or 'the store cap'}).")
    return k


def evaluate(rule: dict, ctx: dict) -> dict:
    """What one rule wants now. ctx: today (date), days_rows, lines, structure, campaign_cost {campaign id: (start,
    end) -> cost} as a function, new_products [{id, title, created}], items_of(product ids) -> item ids.
    Returns {"actions": [{kind, params, summary}], "notes": [text]}."""
    p, k = rule["params"], rule["kind"]
    camps = {c["id"]: c for c in ctx["structure"]["campaigns"]}
    acts, notes = [], []
    if k == "roas_floor":
        c = camps.get(str(p["campaign"]))
        if not c:
            return {"actions": [], "notes": ["The campaign isn't in the account any more."]}
        a, b = window(ctx["today"], p.get("days", 3))
        ps = product_stats(ctx["days_rows"], ctx["lines"], a, b, c["id"])
        bad = []
        for pid, s in ps.items():
            if pid == google_desk.UNLINKED or s["cost"] < float(p.get("min_spend") or 0):
                continue
            r = roas(s, p.get("basis", "shopify"))
            if r is not None and r < float(p["min_roas"]):
                bad.append((pid, s, r))
        if bad:
            bad.sort(key=lambda x: x[2])
            pids = [x[0] for x in bad]
            to = p.get("move_to")
            acts.append({"kind": "move_products" if to else "remove_products",
                         "params": {"products": pids, "from_campaign": c["id"], "to_campaign": to} if to
                         else {"products": pids, "campaign": c["id"]},
                         "summary": f"{len(pids)} product(s) under ROAS {p['min_roas']} over {p.get('days', 3)} days in “{c['name']}”"
                                    + (f" → “{camps.get(str(to), {}).get('name', to)}”" if to else " → taken out"),
                         "detail": [{"product": pid, "cost": round(s["cost"], 2), "sales": round(s["sales"], 2),
                                     "roas": round(r, 2)} for pid, s, r in bad]})
        notes.append(f"Checked {len(ps)} products: {len(bad)} under {p['min_roas']}.")
    elif k == "graduate":
        c, to = camps.get(str(p["from_campaign"])), camps.get(str(p["to_campaign"]))
        if not c or not to:
            return {"actions": [], "notes": ["One of the campaigns isn't in the account any more."]}
        a, b = window(ctx["today"], p.get("days", 14))
        ps = product_stats(ctx["days_rows"], ctx["lines"], a, b, c["id"])
        need = p.get("need", "either")
        good = []
        for pid, s in ps.items():
            if pid == google_desk.UNLINKED or s["cost"] > float(p.get("max_spend") or 1e12):
                continue
            hit = {"sale": s["qty"] > 0, "atc": s["atc"] > 0, "either": s["qty"] > 0 or s["atc"] > 0}[need]
            if hit:
                good.append((pid, s))
        if good:
            acts.append({"kind": "move_products", "params": {"products": [x[0] for x in good], "from_campaign": c["id"],
                                                               "to_campaign": to["id"]},
                         "summary": f"{len(good)} winner(s) from “{c['name']}” → “{to['name']}”",
                         "detail": [{"product": pid, "cost": round(s["cost"], 2), "qty": s["qty"], "atc": round(s["atc"], 1)}
                                    for pid, s in good]})
        notes.append(f"Checked {len(ps)} products in “{c['name']}”: {len(good)} ready to move.")
    elif k == "new_products":
        c = camps.get(str(p["campaign"]))
        if not c:
            return {"actions": [], "notes": ["The campaign isn't in the account any more."]}
        a, b = window(ctx["today"], p.get("days_new", 7))
        ps = product_stats(ctx["days_rows"], ctx["lines"], a, b, c["id"])
        listed = set()
        for g in c["groups"]:
            pr = g.get("products") or {}
            if pr.get("mode") in ("all", "except"):
                listed = None
                break
            listed |= {google_desk.parse_item(i)[0] for i in pr.get("items", [])}
        missing, quiet, live = [], [], []
        for np in ctx.get("new_products") or []:
            inside = listed is None or np["id"] in listed
            if not inside:
                missing.append(np)
            elif not ps.get(np["id"], {}).get("impressions"):
                quiet.append(np)
            else:
                live.append(np)
        notes.append(f"{len(ctx.get('new_products') or [])} new products: {len(live)} getting impressions, {len(quiet)} in the "
                     f"campaign with no impressions yet, {len(missing)} not in the campaign.")
        if quiet:
            notes.append("No impressions yet: " + ", ".join(x["title"] for x in quiet[:15]))
        if missing:
            notes.append("Not in the campaign: " + ", ".join(x["title"] for x in missing[:15]))
            if p.get("add_missing"):
                acts.append({"kind": "add_products", "params": {"products": [x["id"] for x in missing], "campaign": c["id"]},
                             "summary": f"Add {len(missing)} new product(s) to “{c['name']}”",
                             "detail": [{"product": x["id"], "title": x["title"]} for x in missing]})
    elif k == "scale_budget":
        c = camps.get(str(p["campaign"]))
        if not c:
            return {"actions": [], "notes": ["The campaign isn't in the account any more."]}
        a, b = window(ctx["today"], p.get("days", 3))
        cs = campaign_stats(ctx["days_rows"], ctx["lines"], a, b, c["id"], ctx["campaign_cost"](c["id"], a, b),
                            p.get("basis", "shopify"))
        cap = float(p.get("max_budget") or ctx.get("budget_cap") or 0) or None
        if cs["roas"] is not None and cs["roas"] >= float(p["min_roas"]):
            new = c["budget"] + float(p["step"])
            if cap:
                new = min(new, cap)
            if new > c["budget"] + 0.005:
                acts.append({"kind": "set_budget", "params": {"campaign": c["id"], "amount": round(new, 2)},
                             "summary": f"“{c['name']}” ROAS {cs['roas']:.2f} over {p['days']} days → budget "
                                        f"{c['budget']:.2f} → {new:.2f} a day", "cooldown_hours": 24})
            else:
                notes.append(f"ROAS {cs['roas']:.2f} qualifies, but the budget is already at the cap ({cap}).")
        else:
            notes.append(f"ROAS over {p['days']} days: {cs['roas']:.2f}" if cs["roas"] is not None else "No spend in the window.")
    return {"actions": acts, "notes": notes}
