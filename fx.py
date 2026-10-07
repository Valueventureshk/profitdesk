"""Exchange rates, so stores in different currencies can be shown in one.

Rates are the latest daily reference rates: the European Central Bank's (via
Frankfurter) first, and open.er-api.com for currencies the ECB doesn't cover.
Both are free and need no key. They update once a day, which is plenty for
profit reporting.

Rates are held for an hour, and the last good set is saved so a dropped
connection doesn't blank the dashboard.
"""
import json
import time

import httpx

import db

FRANKFURTER = "https://api.frankfurter.dev/v1/latest"
ER_API = "https://open.er-api.com/v6/latest/{base}"
TTL = 3600  # seconds

# Offered in the currency picker, on top of whatever the stores use.
COMMON = ["AUD", "USD", "EUR", "GBP", "CAD", "NZD", "AED", "SGD", "INR", "JPY"]

_memo: dict[str, tuple[float, dict]] = {}


class FxError(RuntimeError):
    pass


async def _fetch(base: str) -> dict:
    """{"rates": {code: units of code per 1 base}, "date", "source"}"""
    async with httpx.AsyncClient(timeout=15) as client:
        rates, date, source = {}, None, []
        try:
            r = await client.get(FRANKFURTER, params={"base": base})
            if r.status_code == 200:
                body = r.json()
                rates.update(body.get("rates", {}))
                date = body.get("date")
                source.append("European Central Bank")
        except httpx.RequestError:
            pass
        try:
            r = await client.get(ER_API.format(base=base))
            if r.status_code == 200 and r.json().get("result") == "success":
                body = r.json()
                for code, rate in body.get("rates", {}).items():
                    if code not in rates:  # ECB first, this fills the gaps
                        rates[code] = rate
                if not date:
                    date = (body.get("time_last_update_utc") or "")[5:16].strip() or None
                source.append("open.er-api.com")
        except httpx.RequestError:
            pass
    if not rates:
        raise FxError("Could not reach either exchange-rate service.")
    rates[base] = 1.0
    return {"rates": rates, "date": date, "source": " + ".join(source)}


async def table(base: str) -> dict:
    """Latest rates against `base`, from memory, the network, or the last saved set."""
    hit = _memo.get(base)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        got = await _fetch(base)
        db.set_setting(f"fx:{base}", json.dumps(got))
        got["stale"] = False
    except FxError:
        saved = db.get_setting(f"fx:{base}")
        if not saved:
            raise
        got = {**json.loads(saved), "stale": True}
    _memo[base] = (time.time(), got)
    return got


def factor(fx: dict, from_code: str, base: str) -> float:
    """Multiply an amount in `from_code` by this to get `base`."""
    if not from_code or from_code == base:
        return 1.0
    rate = fx["rates"].get(from_code)
    if not rate:
        raise FxError(f"No exchange rate available for {from_code}.")
    return 1.0 / rate
