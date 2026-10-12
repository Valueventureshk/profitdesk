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
FIELDS = ("cost", "impressions", "clicks", "conversions", "value", "qty", "sales", "cog", "fees", "refunds", "ref_units")
ITEM_COUNTRY = re.compile(r"^shopify_([a-z]{2,3})_", re.I)


def item_country(item_id: str) -> str:
    """The feed country in a Shopify item id ("shopify_AU_…" -> "AU"), else ""."""
    m = ITEM_COUNTRY.match((item_id or "").strip())
    return m.group(1).upper() if m else ""


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
    """Ratios and the verdict for one window's sums. Net revenue = sales - refunds."""
    cost, sales = w["cost"], w["sales"]
    w["net_sales"] = sales - w["refunds"]
    w["net_qty"] = w["qty"] - w["ref_units"]
    w["profit"] = w["net_sales"] - w["cog"] - w["fees"] - cost
    w["roas"] = sales / cost if cost else None
    w["groas"] = w["value"] / cost if cost else None
    w["poas"] = w["profit"] / cost if cost else None
    w["ctr"] = w["clicks"] / w["impressions"] if w["impressions"] else None
    w["cpc"] = cost / w["clicks"] if w["clicks"] else None
    margin = (w["net_sales"] - w["cog"] - w["fees"]) / sales if sales else unit_margin
    w["be_roas"] = 1 / margin if margin and margin > 0 else None
    if cost < min_spend:
        w["verdict"] = "learning"
    elif not sales or w["be_roas"] is None:
        w["verdict"] = "cut"
    else:
        r = w["roas"] / w["be_roas"]
        w["verdict"] = "scale" if r >= 1.3 else "keep" if r >= 1 else "watch" if r >= 0.8 else "cut"
    return w


def windows_for(today: date, start: date, end: date) -> dict:
    """The periods every product is measured over: the picked one ("sel") and the last 7 / 14 / 30 store
    days to today (today so far included, like the dashboard): {key: (first day, last day)} as ISO dates."""
    out = {str(w): ((today - timedelta(days=w - 1)).isoformat(), today.isoformat()) for w in WINDOWS}
    out["sel"] = (start.isoformat(), end.isoformat())
    return out


def with_dashboard(out: dict, rows: list, windows: dict) -> dict:
    """Swap the summary's totals for the dashboard's own (metrics.day rows of the same store days), so
    the two pages agree to the cent: Shopify sales, refunds, COG, fees and Google spend (the whole
    account, store clock). What the product rows add up to stays as cost_products; the rest of the
    spend is "not on a product"."""
    for w, (start, end) in windows.items():
        days = [r for r in rows if start <= r["date"] <= end]
        s = out["summary"][w]
        cost = sum(r["google_spend"] for r in days)
        s["cost_products"] = s["cost"]
        s["cost"] = cost
        s["cost_other"] = cost - s["cost_products"]
        s["sales"] = sum(r["sales"] for r in days)
        s["refunds"] = sum(r["refunds"] + r["chargebacks"] for r in days)
        s["net_sales"] = s["sales"] - s["refunds"]
        s["cog"] = sum(r["cogs"] for r in days)
        s["fees"] = sum(r["processing_fee"] for r in days)
        s["orders"] = sum(r["orders"] for r in days)
        kept = s["sales"] - s["refunds"] - s["cog"] - s["fees"]
        s["profit"] = kept - cost
        s["roas"] = s["sales"] / cost if cost else None
        s["poas"] = s["profit"] / cost if cost else None
        s["cpc"] = cost / s["clicks"] if s["clicks"] else None
        s["be_roas"] = s["sales"] / kept if kept > 0 else None
        s["start"], s["end"] = start, end
    return out


def build(google_rows: list, g_factor: float, lines: list, usd_factor: float, products: dict,
          fee_rate: float, windows: dict, g_windows: dict, min_spend: float = 25.0, s_factor: float = 1.0,
          refunds: list = (), country: str = "") -> dict:
    """google_rows: gads.product_report rows (account currency x g_factor = store currency);
    lines: cog_lines rows of the last 30 store days (cost_usd x usd_factor = store currency);
    products: {product id: {title, image, status, handle, variants: {variant id: {title, price, image}}}};
    windows / g_windows: {key: (first, last day)} on the store's / the ad account's clock (windows_for);
    every money figure comes out in one currency: Google x g_factor, store currency x s_factor, USD x usd_factor."""
    W = list(windows)
    variant_of = {}                                   # variant id -> product id
    for pid, p in products.items():
        for vid in (p.get("variants") or {}):
            variant_of[vid] = pid
    prods = {}

    def slot(pid, vid, title=""):
        p = prods.setdefault(pid, {"pid": pid, "gtitle": title, "variants": {}})
        if title and not p["gtitle"]:
            p["gtitle"] = title
        return p["variants"].setdefault(vid, {"vid": vid, "w": {w: _empty() for w in W}, "units_cost": [0.0, 0],
                                              "days": {w: set() for w in W}})

    countries = set()
    for r in google_rows:
        c = item_country(r["item_id"])
        countries.add(c)
        if country and c and c != country:
            continue
        pid, vid = parse_item(r["item_id"])
        variant_of.setdefault(vid, pid)
        v = slot(pid, vid, r["title"])
        for w in W:
            if g_windows[w][0] <= (r["date"] or "") <= g_windows[w][1]:
                t = v["w"][w]
                t["cost"] += r["cost"] * g_factor
                t["value"] += r["value"] * g_factor
                t["impressions"] += r["impressions"]
                t["clicks"] += r["clicks"]
                t["conversions"] += r["conversions"]
                if r["cost"]:
                    v["days"][w].add(r["date"])
    for l in lines:
        if l.get("cancelled"):
            continue
        countries.add(l.get("country") or "")
        if country and (l.get("country") or "") != country:
            continue
        vid = _num(l.get("variant_id"))
        pid = _num(l.get("product_id")) or variant_of.get(vid)
        if not pid or pid not in prods and pid not in products:
            continue                                  # never advertised and unknown: not a Google product
        v = slot(pid, vid)
        qty, sales = int(l.get("quantity") or 0), float(l.get("subtotal") or 0) * s_factor
        cog = (l.get("cost_usd") or 0.0) * usd_factor
        if qty and l.get("cost_usd"):
            v["units_cost"][0] += cog
            v["units_cost"][1] += qty
        for w in W:
            if windows[w][0] <= (l.get("day") or "") <= windows[w][1]:
                t = v["w"][w]
                t["qty"] += qty
                t["sales"] += sales
                t["cog"] += cog
                t["fees"] += sales * fee_rate
    for x in refunds:                                 # refunds count on the day they're made
        vid = _num(x.get("variant_id"))
        pid = _num(x.get("product_id")) or variant_of.get(vid)
        if not pid or pid not in prods:
            continue
        v = slot(pid, vid)
        for w in W:
            if windows[w][0] <= x["day"] <= windows[w][1]:
                v["w"][w]["refunds"] += x["amount"] * s_factor
                v["w"][w]["ref_units"] += x["quantity"]

    out = []
    for pid, p in prods.items():
        info = products.get(pid) or {}
        vinfo = info.get("variants") or {}
        variants, totals = [], {w: _empty() for w in W}
        for vid, v in p["variants"].items():
            vi = vinfo.get(vid) or {}
            price = vi.get("price")
            unit_cog = v["units_cost"][0] / v["units_cost"][1] if v["units_cost"][1] else None
            unit_margin = (1 - unit_cog / price - fee_rate) if price and unit_cog is not None else None
            for w in W:
                for k in FIELDS:
                    totals[w][k] += v["w"][w][k]
                v["w"][w]["ad_days"] = len(v["days"][w])
                _finish(v["w"][w], fee_rate, unit_margin, min_spend / 2)
            variants.append({"vid": vid, "title": vi.get("title") or ("Variant " + vid if vid else "?"),
                             "price": price, "unit_cog": unit_cog, "image": vi.get("image"), "w": v["w"]})
        known = [x for x in variants if x["price"] and x["unit_cog"] is not None]
        unit_margin = (sum(1 - x["unit_cog"] / x["price"] for x in known) / len(known) - fee_rate) if known else None
        for w in W:
            totals[w]["ad_days"] = len(set().union(*(p["variants"][x["vid"]]["days"][w] for x in variants))) if variants else 0
            _finish(totals[w], fee_rate, unit_margin, min_spend)
        variants.sort(key=lambda x: -x["w"]["sel"]["cost"])
        out.append({"pid": pid, "title": info.get("title") or p["gtitle"] or pid, "image": info.get("image"),
                    "status": info.get("status") or ("" if not pid.startswith("item:") else "Not Shopify"),
                    "handle": info.get("handle"), "variants": variants, "w": totals})
    out.sort(key=lambda x: -x["w"]["sel"]["cost"])
    summary = {}
    for w in W:
        s = _empty()
        for p in out:
            for k in FIELDS:
                s[k] += p["w"][w][k]
        s["cost_products"] = s["cost"]
        s["cost_other"] = 0.0
        summary[w] = _finish(s, fee_rate, None, 0)
        summary[w]["products"] = sum(1 for p in out if p["w"][w]["cost"] or p["w"][w]["sales"])
    return {"products": out, "summary": summary, "windows": W, "countries": sorted(c for c in countries if c),
            "ranges": {w: {"start": windows[w][0], "end": windows[w][1], "g_start": g_windows[w][0], "g_end": g_windows[w][1]} for w in W}}
