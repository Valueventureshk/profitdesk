"""Apps → Google Ads: every product's Google Shopping / Performance Max results next to what it
really sold on Shopify and what it cost, per 7 / 14 / 30 days.

Google reports per *item* (one per variant: "shopify_AU_<product id>_<variant id>"). Everything
is grouped back into one row per Shopify product, with its variants underneath.

    Google        cost, impressions, clicks, conversions + value (Google's own attribution)
    Shopify       units and sales of the product on the store (all channels: honest for stores
                  that only advertise on Google), COG from cog_lines (invoice / history), fees
                  at the store's own fee rate
    net profit  = sales - COG - fees - Google cost
    ROAS        = sales / Google cost          POAS = net profit / Google cost
    breakeven   = sales / (sales - COG - fees) the ROAS where the product stops losing money

Verdicts (per window): Learning (spend under the minimum), Scale (ROAS 30% over breakeven),
Keep (over breakeven), Watch (within 20% under), Cut (further under, or spend and no sales).
Google's days are the ad account's own; Shopify's are the store's. Windows end yesterday, like
Google's own "Last 7 / 14 / 30 days". Performance Max also spends on video, display and search ads
that belong to no product, and Search campaigns have no products: that spend is "not on a product",
counted in the totals (spend, ROAS, net profit) but in no product row.
"""
import re
from datetime import date, timedelta

WINDOWS = (7, 14, 30)
ITEM = re.compile(r"^shopify_[a-z]{2,3}_(\d+)_(\d+)$", re.I)
FIELDS = ("cost", "impressions", "clicks", "conversions", "value", "qty", "sales", "cog", "fees")


def parse_item(item_id: str) -> tuple:
    """(product id, variant id) from a Google item id; other feeds keep the item id."""
    m = ITEM.match((item_id or "").strip())
    if m:
        return m.group(1), m.group(2)
    return "item:" + (item_id or "?"), item_id or "?"


def _num(gid) -> str:
    return str(gid or "").rsplit("/", 1)[-1]


def _empty():
    return {k: 0.0 for k in FIELDS}


def _finish(w: dict, fee_rate: float, unit_margin, min_spend: float) -> dict:
    """Ratios and the verdict for one window's sums."""
    cost, sales = w["cost"], w["sales"]
    w["profit"] = sales - w["cog"] - w["fees"] - cost
    w["roas"] = sales / cost if cost else None
    w["groas"] = w["value"] / cost if cost else None
    w["poas"] = w["profit"] / cost if cost else None
    w["ctr"] = w["clicks"] / w["impressions"] if w["impressions"] else None
    w["cpc"] = cost / w["clicks"] if w["clicks"] else None
    margin = (sales - w["cog"] - w["fees"]) / sales if sales else unit_margin
    w["be_roas"] = 1 / margin if margin and margin > 0 else None
    if cost < min_spend:
        w["verdict"] = "learning"
    elif not sales or w["be_roas"] is None:
        w["verdict"] = "cut"
    else:
        r = w["roas"] / w["be_roas"]
        w["verdict"] = "scale" if r >= 1.3 else "keep" if r >= 1 else "watch" if r >= 0.8 else "cut"
    return w


def with_dashboard(out: dict, rows: list, today: str) -> dict:
    """Swap the summary's totals for the dashboard's own (metrics.day rows of the same store days), so
    the two pages agree to the cent: Shopify sales, refunds, COG, fees and Google spend (the whole
    account, store clock). What the product rows add up to stays as cost_products; the rest of the
    spend is "not on a product"."""
    end = date.fromisoformat(today)
    for w in WINDOWS:
        start = (end - timedelta(days=w - 1)).isoformat()
        days = [r for r in rows if start <= r["date"] <= today]
        s = out["summary"][w]
        cost = sum(r["google_spend"] for r in days)
        s["cost_products"] = s["cost"]
        s["cost"] = cost
        s["cost_other"] = cost - s["cost_products"]
        s["sales"] = sum(r["sales"] for r in days)
        s["refunds"] = sum(r["refunds"] + r["chargebacks"] for r in days)
        s["cog"] = sum(r["cogs"] for r in days)
        s["fees"] = sum(r["processing_fee"] for r in days)
        s["orders"] = sum(r["orders"] for r in days)
        kept = s["sales"] - s["refunds"] - s["cog"] - s["fees"]
        s["profit"] = kept - cost
        s["roas"] = s["sales"] / cost if cost else None
        s["poas"] = s["profit"] / cost if cost else None
        s["cpc"] = cost / s["clicks"] if s["clicks"] else None
        s["be_roas"] = s["sales"] / kept if kept > 0 else None
        s["start"], s["end"] = start, today
    return out


def build(google_rows: list, g_factor: float, lines: list, usd_factor: float, products: dict,
          fee_rate: float, today: str, min_spend: float = 25.0, account_days: dict = None, g_today: str = None) -> dict:
    """google_rows: gads.product_report rows (account currency x g_factor = store currency);
    lines: cog_lines rows of the last 30 store days (cost_usd x usd_factor = store currency);
    products: {product id: {title, image, status, handle, variants: {variant id: {title, price, image}}}};
    today / g_today: the last day counted (store / ad account clock); account_days: {account day: cost}."""
    end = date.fromisoformat(today)
    g_end = date.fromisoformat(g_today or today)
    starts = {w: (end - timedelta(days=w - 1)).isoformat() for w in WINDOWS}
    g_starts = {w: (g_end - timedelta(days=w - 1)).isoformat() for w in WINDOWS}
    end_s, g_end_s = end.isoformat(), g_end.isoformat()
    variant_of = {}                                   # variant id -> product id
    for pid, p in products.items():
        for vid in (p.get("variants") or {}):
            variant_of[vid] = pid
    prods = {}

    def slot(pid, vid, title=""):
        p = prods.setdefault(pid, {"pid": pid, "gtitle": title, "variants": {}})
        if title and not p["gtitle"]:
            p["gtitle"] = title
        return p["variants"].setdefault(vid, {"vid": vid, "w": {w: _empty() for w in WINDOWS}, "units_cost": [0.0, 0]})

    for r in google_rows:
        pid, vid = parse_item(r["item_id"])
        variant_of.setdefault(vid, pid)
        v = slot(pid, vid, r["title"])
        for w in WINDOWS:
            if g_starts[w] <= (r["date"] or "") <= g_end_s:
                t = v["w"][w]
                t["cost"] += r["cost"] * g_factor
                t["value"] += r["value"] * g_factor
                t["impressions"] += r["impressions"]
                t["clicks"] += r["clicks"]
                t["conversions"] += r["conversions"]
    for l in lines:
        if l.get("cancelled"):
            continue
        vid = _num(l.get("variant_id"))
        pid = _num(l.get("product_id")) or variant_of.get(vid)
        if not pid or pid not in prods and pid not in products:
            continue                                  # never advertised and unknown: not a Google product
        v = slot(pid, vid)
        qty, sales = int(l.get("quantity") or 0), float(l.get("subtotal") or 0)
        cog = (l.get("cost_usd") or 0.0) * usd_factor
        if qty and l.get("cost_usd"):
            v["units_cost"][0] += cog
            v["units_cost"][1] += qty
        for w in WINDOWS:
            if starts[w] <= (l.get("day") or "") <= end_s:
                t = v["w"][w]
                t["qty"] += qty
                t["sales"] += sales
                t["cog"] += cog
                t["fees"] += sales * fee_rate

    out = []
    for pid, p in prods.items():
        info = products.get(pid) or {}
        vinfo = info.get("variants") or {}
        variants, totals = [], {w: _empty() for w in WINDOWS}
        for vid, v in p["variants"].items():
            vi = vinfo.get(vid) or {}
            price = vi.get("price")
            unit_cog = v["units_cost"][0] / v["units_cost"][1] if v["units_cost"][1] else None
            unit_margin = (1 - unit_cog / price - fee_rate) if price and unit_cog is not None else None
            for w in WINDOWS:
                for k in FIELDS:
                    totals[w][k] += v["w"][w][k]
                _finish(v["w"][w], fee_rate, unit_margin, min_spend / 2)
            variants.append({"vid": vid, "title": vi.get("title") or ("Variant " + vid if vid else "?"),
                             "price": price, "unit_cog": unit_cog, "image": vi.get("image"), "w": v["w"]})
        known = [x for x in variants if x["price"] and x["unit_cog"] is not None]
        unit_margin = (sum(1 - x["unit_cog"] / x["price"] for x in known) / len(known) - fee_rate) if known else None
        for w in WINDOWS:
            _finish(totals[w], fee_rate, unit_margin, min_spend)
        variants.sort(key=lambda x: -x["w"][30]["cost"])
        out.append({"pid": pid, "title": info.get("title") or p["gtitle"] or pid, "image": info.get("image"),
                    "status": info.get("status") or ("" if not pid.startswith("item:") else "Not Shopify"),
                    "handle": info.get("handle"), "variants": variants, "w": totals})
    out.sort(key=lambda x: -x["w"][30]["cost"])
    summary = {}
    for w in WINDOWS:
        s = _empty()
        for p in out:
            for k in FIELDS:
                s[k] += p["w"][w][k]
        s["cost_products"] = s["cost"]
        if account_days is not None:            # the whole account, so totals match Google
            s["cost"] = max(s["cost"], sum(c for d, c in account_days.items() if g_starts[w] <= d <= g_end_s) * g_factor)
        s["cost_other"] = s["cost"] - s["cost_products"]
        summary[w] = _finish(s, fee_rate, None, 0)
        summary[w]["products"] = sum(1 for p in out if p["w"][w]["cost"] or p["w"][w]["sales"])
    return {"products": out, "summary": summary, "windows": list(WINDOWS),
            "ranges": {w: {"start": starts[w], "end": end_s, "g_start": g_starts[w], "g_end": g_end_s} for w in WINDOWS}}
