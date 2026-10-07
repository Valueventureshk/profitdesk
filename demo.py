"""Made-up numbers for demo stores, so the dashboard can be explored before any
real store is connected.

A store is a demo store when its access token is the word "demo". Nothing here
ever touches Shopify, Google or Meta.
"""
import hashlib
from datetime import date, timedelta

# name -> (average daily sales, average order value, target ROAS)
PROFILES = {
    "Madison Braids":   (2400, 68, 3.3),
    "Northwind Supply": (1650, 92, 2.6),
    "Harbour & Co":     (980, 145, 4.1),
    "Petal Street":     (610, 41, 2.2),
    "Ridgeline Gear":   (3100, 210, 3.8),
}


def is_demo(store) -> bool:
    return store["access_token"] == "demo"


def _noise(*parts) -> float:
    """Deterministic 0–1 value, so the same day always shows the same figure."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def day_share(store, day: str, fraction: float):
    """A demo day cut short at `fraction` of the way through. Invented figures,
    so an even spread across the day is good enough."""
    sales, spend = series(store, day, day)
    s, p = sales[day], spend[day]
    return (
        {"sales": round(s["sales"] * fraction, 2), "orders": round(s["orders"] * fraction)},
        {"google": round(p["google"] * fraction, 2), "meta": round(p["meta"] * fraction, 2)},
    )


def series(store, start: str, end: str):
    base, aov, target_roas = PROFILES.get(store["name"], (1200, 75, 3.0))
    name = store["name"]

    sales, spend = {}, {}
    day = date.fromisoformat(start)
    last = date.fromisoformat(end)
    index = 0

    while day <= last:
        iso = day.isoformat()

        weekend = 1.22 if day.weekday() >= 5 else 0.96
        drift = 1 + index * 0.0015
        swing = 0.72 + _noise(name, iso, "sales") * 0.62

        revenue = base * weekend * drift * swing
        orders = max(1, round(revenue / (aov * (0.85 + _noise(name, iso, "aov") * 0.3))))

        roas = target_roas * (0.7 + _noise(name, iso, "roas") * 0.7)
        sales[iso] = {"sales": round(revenue, 2), "orders": orders}
        total = revenue / roas
        meta_share = 0.45 + _noise(name, iso, "meta") * 0.25
        spend[iso] = {"google": round(total * (1 - meta_share), 2),
                      "meta": round(total * meta_share, 2)}

        day += timedelta(days=1)
        index += 1

    return sales, spend
