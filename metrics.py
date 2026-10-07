"""The profit engine.

Every number the dashboard shows is defined here and nowhere else.

    Total sales        what Shopify reports as total sales
  - Other costs        your cost % applied to total sales
  - Ad spend           Google Ads + Meta Ads, account level
  = Net profit

    ROAS               total sales / ad spend
    Net margin         net profit / total sales

That is the whole model. Costs are a single percentage you set per store,
so the number is only as honest as the percentage you put in.
"""

SUMMABLE = ("sales", "orders", "google_spend", "meta_spend", "ad_spend",
            "other_costs", "net_profit")


def _div(a, b):
    """Ratios are None rather than 0 when undefined, so the UI can show a dash."""
    return a / b if b else None


def day(date: str, sales: float, orders: int, google_spend: float,
        meta_spend: float, cost_pct: float):
    """One store, one day. Cost % is applied per store before anything is blended.

    Ad spend is the sum of every platform. Each platform is kept as well, so the
    dashboard can show the split without working anything out itself.
    """
    other_costs = sales * (cost_pct / 100.0)
    ad_spend = google_spend + meta_spend
    return {
        "date": date,
        "sales": sales,
        "orders": orders,
        "google_spend": google_spend,
        "meta_spend": meta_spend,
        "ad_spend": ad_spend,
        "other_costs": other_costs,
        "net_profit": sales - other_costs - ad_spend,
    }


def blend(series_per_store):
    """Merge several stores' daily rows into one series, matched up by date."""
    merged = {}
    for series in series_per_store:
        for row in series:
            acc = merged.setdefault(row["date"], {"date": row["date"], **{k: 0 for k in SUMMABLE}})
            for k in SUMMABLE:
                acc[k] += row[k]
    return [merged[d] for d in sorted(merged)]


def with_ratios(days):
    """Add the derived figures to each day, after any blending has happened.

    Ratios can never be summed, so they are only ever calculated on a finished
    row — never carried through blend().
    """
    for row in days:
        row["roas"] = _div(row["sales"], row["ad_spend"])
        row["net_margin"] = _div(row["net_profit"], row["sales"])
        row["aov"] = _div(row["sales"], row["orders"])
    return days


def summarize(days):
    totals = {k: 0 for k in SUMMABLE}
    for row in days:
        for k in SUMMABLE:
            totals[k] += row[k]
    totals["roas"] = _div(totals["sales"], totals["ad_spend"])
    totals["net_margin"] = _div(totals["net_profit"], totals["sales"])
    totals["aov"] = _div(totals["sales"], totals["orders"])
    return totals


def compare(current, previous):
    """Percentage change per metric. None when the previous period had nothing."""
    out = {}
    for key, now in current.items():
        was = previous.get(key)
        if now is None or was in (None, 0):
            out[key] = None
        else:
            out[key] = (now - was) / abs(was) * 100.0
    return out
