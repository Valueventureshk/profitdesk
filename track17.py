"""17TRACK Tracking API (v2.4): follows each parcel across every carrier it
passes through (e.g. YunExpress in China, then Australia Post).

    register      one quota per tracking number, once. 17TRACK then checks the
                  carrier every 6-12 hours by itself, at no extra cost.
    webhook       17TRACK pushes every update to /api/scm/webhook (set the URL in
                  the 17TRACK dashboard). Signed: sha256(body + "/" + key).
    gettrackinfo  reads what 17TRACK already has for registered numbers.

The key is pasted in ProfitDesk (SCM desk → Tracking connection), never in code.
"""
import hashlib
import hmac
import json
from datetime import datetime

import httpx

API = "https://api.17track.net/track/v2.4"

# 17TRACK's main statuses -> the SCM desk's.
STATUS = {
    "NotFound": "pending",
    "InfoReceived": "info_received",
    "InTransit": "in_transit",
    "Expired": "expired",
    "AvailableForPickup": "pickup",
    "OutForDelivery": "out_for_delivery",
    "DeliveryFailure": "failed_attempt",
    "Delivered": "delivered",
    "Exception": "exception",
}


class TrackError(RuntimeError):
    pass


ERRORS = {
    -18010001: "17TRACK refused the request: this server's address isn't on the key's allowed list. "
               "In 17TRACK → Settings, turn off the IP whitelist.",
    -18010002: "17TRACK didn't accept the key. Copy it again from the 17TRACK dashboard.",
    -18019907: "17TRACK's daily limit is reached; more parcels will be added tomorrow.",
    -18019908: "The 17TRACK quota is used up. Buy a top-up in the 17TRACK dashboard.",
}


async def _post(key: str, path: str, body) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(f"{API}/{path}", json=body,
                                  headers={"17token": key, "Content-Type": "application/json"})
        except httpx.RequestError as e:
            raise TrackError(f"Couldn't reach 17TRACK ({type(e).__name__}).") from e
    if r.status_code == 429:
        raise TrackError("17TRACK is busy (too many requests). It'll retry shortly.")
    if r.status_code >= 400:
        raise TrackError(f"17TRACK error {r.status_code}.")
    out = r.json()
    code = out.get("code", 0)
    if code:
        raise TrackError(ERRORS.get(code) or f"17TRACK error {code}: {(out.get('data') or {}).get('errors') or ''}")
    return out.get("data") or {}


async def quota(key: str) -> dict:
    """{quota_total, quota_used, quota_remain, today_used, ...}"""
    return await _post(key, "getquota", [])


async def register(key: str, items: list) -> tuple:
    """items: [{number, order_no, order_time, destination_country, tag}] (max 40).
    Returns (accepted numbers, {number: error message}). "Already registered"
    counts as accepted."""
    body = []
    for it in items[:40]:
        b = {"number": it["number"], "auto_detection": True}
        for k in ("order_no", "order_time", "destination_country", "tag"):
            if it.get(k):
                b[k] = it[k]
        body.append(b)
    data = await _post(key, "register", body)
    ok = [a["number"] for a in data.get("accepted", [])]
    bad = {}
    for r in data.get("rejected", []):
        err = r.get("error") or {}
        if err.get("code") == -18019901:          # already registered
            ok.append(r["number"])
        elif err.get("code") in (-18019907, -18019908):
            raise TrackError(ERRORS[err["code"]])
        else:
            bad[r["number"]] = err.get("message") or str(err.get("code"))
    return ok, bad


async def track_info(key: str, numbers: list) -> list:
    """What 17TRACK has for registered numbers (max 40). [{number, carrier, track_info}]"""
    data = await _post(key, "gettrackinfo", [{"number": n} for n in numbers[:40]])
    return data.get("accepted", [])


def verify(body: bytes, sign: str, key: str) -> bool:
    want = hashlib.sha256(body + b"/" + key.encode()).hexdigest()
    return bool(sign) and hmac.compare_digest(want.lower(), sign.strip().lower())


def _when(e: dict):
    t = e.get("time_utc") or e.get("time_iso")
    if not t:
        return None
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def summarize(item: dict) -> dict:
    """One 17TRACK record -> the fields the SCM desk keeps."""
    ti = item.get("track_info") or {}
    latest = ti.get("latest_status") or {}
    ev = ti.get("latest_event") or {}
    tm = ti.get("time_metrics") or {}
    ship = ti.get("shipping_info") or {}
    providers = (ti.get("tracking") or {}).get("providers") or []
    events = []
    for p in providers:
        prov = p.get("provider") or {}
        for e in p.get("events") or []:
            events.append({"time": _when(e), "text": e.get("description") or "",
                           "location": e.get("location") or "", "stage": e.get("stage") or "",
                           "sub": e.get("sub_status") or "", "carrier": prov.get("name") or "",
                           "country": prov.get("country") or ""})
    events.sort(key=lambda e: e["time"] or "", reverse=True)
    delivered = next((e["time"] for e in events if (e["sub"] or "").startswith("Delivered")), None)
    first = providers[0].get("provider") if providers else {}
    last = providers[-1].get("provider") if providers else {}
    # 17TRACK lists the providers last-mile first when a parcel is handed over.
    last_mile = (providers[0].get("provider") or {}) if len(providers) > 1 else {}
    eta = (tm.get("estimated_delivery_date") or {})
    return {
        "status": STATUS.get(latest.get("status"), "pending"),
        "sub_status": latest.get("sub_status") or "",
        "last_event": ev.get("description") or "",
        "last_event_at": _when(ev),
        "last_location": ev.get("location") or "",
        "carrier_name": (last or first or {}).get("name") or "",
        "last_mile": last_mile.get("name") or "",
        "last_mile_number": (ti.get("misc_info") or {}).get("local_number") or "",
        "origin": (ship.get("shipper_address") or {}).get("country") or "",
        "destination": (ship.get("recipient_address") or {}).get("country") or "",
        "transit_days": tm.get("days_of_transit"),
        "eta_from": eta.get("from"), "eta_to": eta.get("to"),
        "delivered_at": delivered,
        "events": json.dumps(events[:80]),
    }
