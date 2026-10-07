"""The profit engine.

Every number the dashboard shows is defined here and nowhere else.

    Total sales        what Shopify reports as total sales
  - Processing fees    payment provider fees + Shopify's third-party fee
  - Other costs        your cost % applied to total sales
  - Ad spend           Google Ads + Meta Ads, account level
  = Net profit

    ROAS               total sales / ad spend
    Net margin         net profit / total sales
    AOV                total sales / orders

Processing fees are estimated per order from how it was paid, using the rate
card below (editable in Settings) until exact fees are pulled from PayPal and
Airwallex. Other costs are a single percentage you set per store, so they are
only as honest as the percentage you put in.
"""

SUMMABLE = ("sales", "orders", "google_spend", "meta_spend", "ad_spend",
            "payment_fee", "shopify_fee", "processing_fee",
            "other_costs", "net_profit")


# ---------------------------------------------------------------- processing fees

# Standard rates for a Hong Kong business account selling to customers abroad.
#   PayPal HK (effective 7 Aug 2025): 3.90% + fixed fee, +0.50% cross-border,
#     3.00% to convert a foreign-currency payment into HKD.
#   Airwallex HK: international cards 3.60% + HK$2.35 (Apple Pay / Google Pay
#     are priced as the card behind them).
#   Afterpay via Airwallex: 5.90% payment method fee + HK$2.00 gateway fee,
#     confirmed against three real CUTEHOME settlements (Sep-Oct 2026).
#   Airwallex settles in the payment's own currency (AUD stays AUD), so no
#     conversion is charged at payment time; FX is 0 unless that changes.
DEFAULT_FEE_RATES = {
    "paypal_pct": 4.40,
    "paypal_fixed": {"AUD": 0.30, "USD": 0.30, "CAD": 0.30, "EUR": 0.35,
                     "GBP": 0.20, "NZD": 0.45, "HKD": 2.35},
    "paypal_fx_pct": 3.00,
    "awx_card_pct": 3.60,
    "awx_card_fixed_hkd": 2.35,
    "awx_afterpay_pct": 5.90,
    "awx_afterpay_fixed_hkd": 2.00,
    "awx_fx_pct": 0.00,
}

# Shopify's own fee on orders paid through anything other than Shopify Payments.
SHOPIFY_THIRD_PARTY_PCT = {"Basic": 2.0, "Shopify": 1.0, "Grow": 1.0, "Advanced": 0.6}

# Ways of paying that carry no provider fee and no Shopify fee.
_FREE = ("manual", "cash", "bank", "gift_card", "cod", "money order")


def payment_method(gateways: str) -> str:
    """Map Shopify's gateway names for an order to a fee bucket."""
    g = gateways.lower()
    if not g:
        return "none"
    if "afterpay" in g:
        return "airwallex_afterpay"
    if "airwallex" in g:
        return "airwallex_card"
    if "paypal" in g:
        return "paypal"
    if "shopify_payments" in g:
        return "shopify_payments"
    if any(word in g for word in _FREE):
        return "free"
    return "other"


def processing_fees(payments: dict, rates: dict, plan: str, currency: str,
                    hkd_to_store: float):
    """Estimated (payment provider fee, Shopify third-party fee) for one store-day.

    payments     {gateway names: [orders, amount paid]} in the store's currency
    hkd_to_store how many units of the store's currency one HKD is worth, for
                 Airwallex's HK$ fixed fees
    Currency conversion is charged when the store sells in anything but HKD.
    """
    converting = currency != "HKD"
    shopify_pct = SHOPIFY_THIRD_PARTY_PCT.get(plan, 0.0)
    provider = shopify = 0.0
    for gateways, (orders, amount) in payments.items():
        method = payment_method(gateways)
        if method == "paypal":
            fixed = rates["paypal_fixed"].get(currency)
            if fixed is None:
                fixed = rates["paypal_fixed"].get("HKD", 0.0) * hkd_to_store
            pct = rates["paypal_pct"] + (rates["paypal_fx_pct"] if converting else 0.0)
            provider += amount * pct / 100.0 + orders * fixed
        elif method in ("airwallex_card", "airwallex_afterpay"):
            kind = "card" if method == "airwallex_card" else "afterpay"
            pct = rates[f"awx_{kind}_pct"] + (rates["awx_fx_pct"] if converting else 0.0)
            provider += (amount * pct / 100.0
                         + orders * rates[f"awx_{kind}_fixed_hkd"] * hkd_to_store)
        if method in ("paypal", "airwallex_card", "airwallex_afterpay", "other"):
            shopify += amount * shopify_pct / 100.0
    return provider, shopify


def _div(a, b):
    """Ratios are None rather than 0 when undefined, so the UI can show a dash."""
    return a / b if b else None


def day(date: str, sales: float, orders: int, google_spend: float,
        meta_spend: float, cost_pct: float, payment_fee: float = 0.0,
        shopify_fee: float = 0.0):
    """One store, one day. Cost % is applied per store before anything is blended.

    Ad spend is the sum of every platform, and processing fees the sum of the
    payment provider's fee and Shopify's. Each part is kept as well, so the
    dashboard can show the split without working anything out itself.
    """
    other_costs = sales * (cost_pct / 100.0)
    ad_spend = google_spend + meta_spend
    processing_fee = payment_fee + shopify_fee
    return {
        "date": date,
        "sales": sales,
        "orders": orders,
        "google_spend": google_spend,
        "meta_spend": meta_spend,
        "ad_spend": ad_spend,
        "payment_fee": payment_fee,
        "shopify_fee": shopify_fee,
        "processing_fee": processing_fee,
        "other_costs": other_costs,
        "net_profit": sales - processing_fee - other_costs - ad_spend,
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
    # Other costs as a share of sales: each store's own % blended by its sales.
    totals["cost_share"] = _div(totals["other_costs"], totals["sales"])
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
