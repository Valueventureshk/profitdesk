"""The Expenses desk: every payment that actually left Airwallex and PayPal, sorted
into business categories. Actual figures only, nothing estimated.

Movements come from statement.py (the same reading the cash statement uses).
Each one gets a category from, in order:

    1. the owner's pick for that payee (or that one transaction), saved in
       expense_rules; picks apply to every past and future payment to the payee
    2. what the movement plainly is (Meta / Google card charges, Shopify,
       refunds, chargebacks, processing fees)
    3. nothing: it goes to "To sort" at the top of the desk, with the AI's
       suggestion pre-selected (Claude Haiku 5.5, learning from earlier picks)

Money that only moved between the owner's own accounts (reserves, PayPal ->
Airwallex, connected sub-accounts) is not an expense and is left out.

Card holds: a hold, its release and its capture are one card payment. They are
netted per card transaction and dated at the first charge, so a hold that was
released without being charged adds nothing.
"""
import json
import re
from datetime import timedelta

import anthropic

import statement

CATEGORIES = [
    ("supplier", "Supplier payments"),
    ("meta", "Meta ads"),
    ("google", "Google Ads"),
    ("other_ads", "Other ads"),
    ("subscriptions", "Subscriptions + apps"),
    ("fees", "Processing fees"),
    ("refunds", "Refunds"),
    ("chargebacks", "Chargebacks + disputes"),
    ("staff", "Staff salaries"),
    ("directors", "Directors' salaries"),
    ("contractors", "Freelancers + agencies"),
    ("tax", "Tax + government"),
    ("other", "Other business expenses"),
    ("ignore", "Not an expense (own money moved)"),
]
NAMES = dict(CATEGORIES)
_KEYS = [k for k, _ in CATEGORIES]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _item(m: dict, amount: float, key: str, name: str, category, line: str = None) -> dict:
    """One expense: amount is positive for money out, in the movement's own currency."""
    return {"id": m.get("id") or "", "time": m["time"], "currency": m["currency"], "amount": amount,
            "source": m["source"], "key": key, "name": name, "category": category,
            "line": line or name, "detail": (m.get("desc") or "")[:120]}


def _cards(moves: list) -> list:
    """Card charges netted per card transaction (hold + release + capture = one payment)."""
    groups = {}
    for m in moves:
        groups.setdefault(m.get("sid") or m.get("id") or id(m), []).append(m)
    out = []
    for g in groups.values():
        net = sum(x["amount"] for x in g)
        named = next((x for x in g if x["line"] not in ("Card spend", "Card refunds and released holds")), g[0])
        first = min(g, key=lambda x: x["time"])
        out.append({**named, "time": first["time"], "amount": net})
    # Holds whose release came under another id: an unnamed debit and an unnamed
    # credit of the same amount within 10 days cancel out.
    loose = [m for m in out if m["line"] in ("Card spend", "Card refunds and released holds")]
    for a in loose:
        if a.get("gone") or a["amount"] >= 0:
            continue
        for b in loose:
            if (not b.get("gone") and b["amount"] > 0 and b["currency"] == a["currency"]
                    and abs(a["amount"] + b["amount"]) < 0.01
                    and abs((b["time"] - a["time"]).total_seconds()) < 10 * 86400):
                a["gone"] = b["gone"] = True
                break
    return [m for m in out if not m.get("gone") and abs(m["amount"]) >= 0.005]


def classify(moves: list, factor) -> list:
    """Movements (after statement pairing) -> expense items, before the owner's rules."""
    items, cards = [], []
    for m in moves:
        sec, line = m["section"], m["line"]
        if m.get("card"):
            cards.append(m)
            continue
        if "legs" in m:                                     # currency conversion: the cost of it
            cost = -sum(l["amount"] * factor(l["currency"]) for l in m["legs"])
            if abs(cost) >= 0.005:
                items.append({**_item({**m, "currency": "_base"}, cost, "fees:conversion",
                                      "Currency conversions", "fees")})
            continue
        if sec == statement.FEES:
            items.append(_item(m, -m["amount"], "fees:" + _norm(line), line.split(" (")[0], "fees", line))
        elif sec == statement.MOVED:
            if m.get("withdrawal") and not m.get("partner"):
                items.append(_item(m, -m["amount"], "pp-withdrawal", "Withdrawn from PayPal to a bank",
                                   None))
            elif m.get("own") and not m.get("matched") and m["amount"] < 0:
                who = m.get("desc") or "an Airwallex account not connected here"
                items.append(_item(m, -m["amount"], "awx-transfer:" + _norm(re.sub(r"\d{3,}", "#", who)),
                                   f"Sent to {who}", None))
        elif sec == statement.OUT:
            if line == "Refunds to customers":
                items.append(_item(m, -m["amount"], "refunds", line, "refunds"))
            elif line == "Disputes and chargebacks":
                items.append(_item(m, -m["amount"], "chargebacks", line, "chargebacks"))
            elif m.get("payout"):
                who = line.removeprefix("Paid to ").strip()
                items.append(_item(m, -m["amount"], "payee:" + _norm(who), who, None))
            elif line.startswith("Other · "):
                items.append(_item(m, -m["amount"], "tx:" + str(m.get("id")), line, None))
            else:                                           # PayPal payment to someone
                items.append(_item(m, -m["amount"], *_payee_key(line)))
        elif sec == statement.IN and line.startswith("Other · ") and m["amount"] < 0:
            items.append(_item(m, -m["amount"], "tx:" + str(m.get("id")), line, None))

    for m in _cards(cards):
        line = m["line"]
        if line in ("Card spend", "Card refunds and released holds"):
            items.append(_item(m, -m["amount"], "tx:" + str(m.get("id")), "Card payment (merchant unknown)", None))
        else:
            items.append(_item(m, -m["amount"], *_payee_key(line)))
    return items


def _payee_key(name: str) -> tuple:
    """key, display name, built-in category for a merchant / payee name."""
    n = _norm(name)
    if name == "Meta ads":
        return "merchant:meta", "Meta ads", "meta"
    if name == "Google Ads":
        return "merchant:google", "Google Ads", "google"
    if name in ("TikTok ads", "Pinterest ads"):
        return "merchant:" + n, name, "other_ads"
    if name == "Shopify":
        return "merchant:shopify", "Shopify", "subscriptions"
    return "merchant:" + n, name, None


def apply_rules(items: list, rules: dict) -> list:
    """rules: key -> category (an owner's pick). A pick for one transaction ("tx:<id>")
    beats a pick for its payee, which beats the built-in category."""
    for it in items:
        pick = rules.get("tx:" + str(it["id"])) or rules.get(it["key"])
        if pick:
            it["category"] = pick
            it["picked"] = True
    return items


def summarize(items: list, factor, start, end, tz, base: str) -> dict:
    """Items inside [start, end), in the display currency. Cards per category,
    day-by-day totals and the "To sort" list."""
    inside = []
    for it in items:
        if not (start <= it["time"] < end):
            continue
        f = 1.0 if it["currency"] == "_base" else factor(it["currency"])
        inside.append({**it, "value": it["amount"] * f, "day": it["time"].astimezone(tz).date().isoformat()})

    cats = {k: {"key": k, "name": n, "total": 0.0, "count": 0, "lines": {}} for k, n in CATEGORIES}
    days, sort = {}, {}
    for it in inside:
        c = it["category"]
        if c is None:
            s = sort.setdefault(it["key"], {"key": it["key"], "name": it["name"], "total": 0.0, "count": 0,
                                            "sources": set(), "examples": []})
            s["total"] += it["value"]
            s["count"] += 1
            s["sources"].add(it["source"].split(" · ")[0])
            if len(s["examples"]) < 5:
                s["examples"].append({"id": it["id"], "day": it["day"], "amount": it["value"],
                                      "original": None if it["currency"] == "_base"
                                      else f"{it['currency']} {it['amount']:,.2f}",
                                      "detail": it["detail"], "via": it["source"]})
            c = "unsorted"
        else:
            cat = cats[c]
            cat["total"] += it["value"]
            cat["count"] += 1
            ln = cat["lines"].setdefault(it["line"], {"name": it["line"], "total": 0.0, "count": 0, "key": it["key"]})
            ln["total"] += it["value"]
            ln["count"] += 1
        if c != "ignore":
            d = days.setdefault(it["day"], {"day": it["day"], "total": 0.0, "by": {}})
            d["total"] += it["value"]
            d["by"][c] = d["by"].get(c, 0.0) + it["value"]

    unsorted = sum(s["total"] for s in sort.values())
    categories = []
    for c in cats.values():
        c["lines"] = sorted(c["lines"].values(), key=lambda l: -abs(l["total"]))
        categories.append(c)
    return {
        "currency": base,
        "total": sum(c["total"] for c in categories if c["key"] != "ignore") + unsorted,
        "unsorted": unsorted,
        "categories": categories,
        "days": sorted(days.values(), key=lambda d: d["day"]),
        "to_sort": sorted(({**s, "sources": " + ".join(sorted(s["sources"]))} for s in sort.values()),
                          key=lambda s: -abs(s["total"])),
    }


# ---------------------------------------------------------------- AI suggestions

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {"key": {"type": "string"},
                       "category": {"type": "string", "enum": _KEYS},
                       "reason": {"type": "string"}},
        "required": ["key", "category", "reason"],
        "additionalProperties": False}}},
    "required": ["picks"],
    "additionalProperties": False,
}

SUGGEST_SYSTEM = """You sort the outgoing payments of a small group of Shopify stores (dropshipping; products come from suppliers in China) into categories, for the owner's own expense view.
Categories: {cats}.
Use the owner's earlier picks as the strongest guide: the same or a similar payee gets the same category. Clues: company names that sound like manufacturers, trading or tech/logistics companies in China or Hong Kong are usually suppliers; a person's name paid a round amount each month is usually a salary (staff, or directors if the owner's earlier picks say so); software and SaaS names are subscriptions; "Sent to" an Airwallex account or a PayPal withdrawal to a bank is often the owner's own money moving (ignore), unless it names someone else.
Give one pick per payee key, with a short reason (under 12 words)."""


async def suggest(api_key: str, unsorted: list, rules: list) -> list:
    """unsorted: [{key, name, total, count, examples}], rules: [{key, name, category}].
    Returns [{key, category, reason}]."""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=90)
    learned = "\n".join(f"- {r['name'] or r['key']}: {NAMES.get(r['category'], r['category'])}"
                        for r in rules[-150:]) or "(none yet)"
    todo = "\n".join(
        f"- key={u['key']} | {u['name']} | {u['count']} payment(s), total {u['total']:,.2f} | "
        + "; ".join(f"{e['day']} {e.get('original') or ''} {e['detail']}".strip() for e in u["examples"][:3])
        for u in unsorted[:60])
    cats = ", ".join(f"{k} ({n})" for k, n in CATEGORIES)
    resp = await client.messages.create(
        model="claude-haiku-5-5", max_tokens=6000,
        system=SUGGEST_SYSTEM.format(cats=cats),
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUGGEST_SCHEMA}},
        messages=[{"role": "user", "content": f"Owner's earlier picks:\n{learned}\n\nPayments to sort:\n{todo}"}])
    if resp.stop_reason == "refusal":
        return []
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        picks = json.loads(text).get("picks", [])
    except ValueError:
        return []
    keys = {u["key"] for u in unsorted}
    return [p for p in picks if p.get("key") in keys and p.get("category") in NAMES]


def window(start, end, now):
    """What to read so holds, releases and transfers around the edges can be paired."""
    return start - timedelta(days=7), min(now, end + timedelta(days=7))
