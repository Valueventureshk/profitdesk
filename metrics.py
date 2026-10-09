"""The profit engine.

Every number the dashboard shows is defined here and nowhere else.

    Total sales        what Shopify reports as total sales
  - Processing fees    payment provider fees + Shopify's third-party fee
  - COG                what the supplier charged for the goods (incl. shipping),
                       per order from the orders sheet / product costs (cog.py);
                       never-bought products use the store's average COG %
                       and that part is reported as "cogs_estimated"
  - Ad spend           Google Ads + Meta Ads, account level
  = Net profit

    Net available      net profit - reserve held back by the payment providers:
                       what's yours to use now from these sales (the held part
                       comes back weeks later). Shown on the dashboard only.

    ROAS               total sales / ad spend
    Net margin         net profit / total sales
    AOV                total sales / orders

Processing fees are estimated per order from how it was paid, using the rate
card below (editable in Settings) until exact fees are pulled from PayPal and
Airwallex.
"""

SUMMABLE = ("sales", "orders", "google_spend", "meta_spend", "ad_spend",
            "payment_fee", "shopify_fee", "processing_fee",
            "cogs", "cogs_estimated", "net_profit", "reserve_held", "net_available",
            "paypal_sales")


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
                    hkd_to_store: float, include_fx: bool = False):
    """Estimated (payment provider fee, Shopify third-party fee) for one store-day.

    payments     {gateway names: [orders, amount paid]} in the store's currency
    hkd_to_store how many units of the store's currency one HKD is worth, for
                 Airwallex's HK$ fixed fees
    include_fx   add the provider's currency conversion % (off by default: PayPal and
                 Airwallex charge conversion when money is converted, not on each sale,
                 and the actual per-sale fees they report don't include it)
    """
    converting = include_fx and currency != "HKD"
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


# Share of each sale the payment provider holds back as a rolling reserve, from
# the accounts' own data: PayPal holds 21% for 60 days on every sale; Airwallex
# holds 10% (cards and Afterpay alike, e.g. US$3.50 of US$34.95) for ~90 days.
RESERVE_PCT = {"paypal": 21.0, "airwallex_card": 10.0, "airwallex_afterpay": 10.0}


# PayPal also holds, for 21 days, everything above this much in PayPal sales per
# calendar month (all stores share one PayPal account). Set on the account by
# PayPal; "request an increase" in PayPal raises it.
PAYPAL_MONTHLY_LIMIT_AUD = 35160.0


def paypal_sales(payments: dict) -> float:
    return sum(amount for g, (orders, amount) in payments.items() if payment_method(g) == "paypal")


def over_limit_share(day_total: float, before: float, limit: float = PAYPAL_MONTHLY_LIMIT_AUD) -> float:
    """Share of one day's PayPal sales that falls above the monthly limit, given
    the month's PayPal sales before that day (both in AUD)."""
    if day_total <= 0:
        return 0.0
    over = max(0.0, before + day_total - limit) - max(0.0, before - limit)
    return min(1.0, over / day_total)


def with_monthly_hold(row: dict, share: float) -> dict:
    """Add PayPal's monthly-limit hold to a day: the part of its PayPal sales above
    the limit is held in full (the 21% reserve is already counted)."""
    extra = row["paypal_sales"] * share * (1 - RESERVE_PCT["paypal"] / 100.0)
    if extra:
        row = {**row, "reserve_held": row["reserve_held"] + extra,
               "net_available": row["net_available"] - extra}
    return row


def reserve_held(payments: dict) -> float:
    """Money held back as reserve from one store-day's payments
    ({gateway names: [orders, amount paid]}, store currency)."""
    return sum(amount * RESERVE_PCT.get(payment_method(g), 0.0) / 100.0
               for g, (orders, amount) in payments.items())


def provider_fee_day(known: float, covered: float, paid: float, estimate_all: float) -> float:
    """A store-day's payment provider fees: the fees of the orders already tracked
    (actual where PayPal / Airwallex have posted them, estimated otherwise), plus
    the rate-card estimate for any of the day's payments not tracked yet."""
    if paid <= 0:
        return known
    untracked = max(0.0, 1.0 - covered / paid)
    return known + estimate_all * untracked


def _div(a, b):
    """Ratios are None rather than 0 when undefined, so the UI can show a dash."""
    return a / b if b else None


def with_google_tax(spend: float, tax_pct) -> float:
    """What Google actually charges for `spend`: some accounts pay tax on top
    (e.g. 10% GST on an Australian account), set per store."""
    return spend * (1 + (tax_pct or 0) / 100.0)


def day(date: str, sales: float, orders: int, google_spend: float,
        meta_spend: float, cogs: float, payment_fee: float = 0.0,
        shopify_fee: float = 0.0, cogs_estimated: float = 0.0, reserve: float = 0.0,
        paypal: float = 0.0):
    """One store, one day, all in one currency.

    Ad spend is the sum of every platform, and processing fees the sum of the
    payment provider's fee and Shopify's. cogs_estimated is the part of cogs
    that came from the store's average rather than known product costs. Each
    part is kept as well, so the dashboard can show the split without working
    anything out itself.
    """
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
        "cogs": cogs,
        "cogs_estimated": cogs_estimated,
        "net_profit": sales - processing_fee - cogs - ad_spend,
        "reserve_held": reserve,
        "net_available": sales - processing_fee - cogs - ad_spend - reserve,
        "paypal_sales": paypal,
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
    # COG as a share of sales.
    totals["cogs_share"] = _div(totals["cogs"], totals["sales"])
    return totals


def platform_roas(stores):
    """ROAS per ad platform, by store: each store is counted under the one platform
    it advertises on (no guessing which ad drove which order). Stores on both
    platforms, or neither, are left out and counted in "mixed".

    stores: [{"platform": "google" | "meta" | "both" | None, "totals": summarize(...)}]
    """
    out = {}
    for key in ("google", "meta"):
        group = [s["totals"] for s in stores if s["platform"] == key]
        sales = sum(t["sales"] for t in group)
        spend = sum(t["ad_spend"] for t in group)
        out[key] = {"stores": len(group), "sales": sales, "ad_spend": spend,
                    "roas": _div(sales, spend)}
    out["mixed"] = sum(1 for s in stores if s["platform"] == "both")
    return out


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
