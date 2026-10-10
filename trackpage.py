"""The customer tracking page, shown on each store's own domain (store.com/apps/track)
through a Shopify app proxy. Shopify wraps it in the store's theme.

Customers look up by order number + email, or by tracking number (the link in the
shipping email uses the tracking number, so it opens straight onto the parcel).

Dropship mode hides the journey before the parcel reaches the customer's country:
no Chinese carriers, cities or tracking numbers. Until it arrives, the customer sees
"Order confirmed", "Shipped" and "In transit to <country>".
"""
import hashlib
import hmac
import html
import json
from datetime import datetime

STEPS = [("ordered", "Ordered"), ("shipped", "Shipped"), ("transit", "In transit"),
         ("out", "Out for delivery"), ("delivered", "Delivered")]
STEP_OF = {"awaiting": 0, "pending": 1, "info_received": 1, "in_transit": 2, "pickup": 3,
           "out_for_delivery": 3, "failed_attempt": 3, "exception": 2, "expired": 2, "delivered": 4}
STATUS_TEXT = {
    "awaiting": "We're preparing your order",
    "pending": "Your order has shipped",
    "info_received": "Your order has shipped",
    "in_transit": "On its way",
    "pickup": "Ready for pickup",
    "out_for_delivery": "Out for delivery",
    "failed_attempt": "Delivery attempted",
    "exception": "There's a delay with this parcel",
    "expired": "On its way",
    "delivered": "Delivered",
}
ORIGIN_WORDS = ("china", "shenzhen", "guangzhou", "dongguan", "yiwu", "hangzhou", "shanghai", "beijing",
                "hong kong", "hongkong", "fujian", "guangdong", "zhejiang", "jiangsu", "putian", "xiamen",
                ", cn", " cn ", "(cn)", "sorting center of", "export")
ARRIVAL_SUBS = ("InTransit_CustomsProcessing", "InTransit_CustomsReleased", "InTransit_CustomsRequiringInformation",
                "OutForDelivery", "Delivered", "AvailableForPickup", "DeliveryFailure")
COUNTRY = {"AU": "Australia", "US": "the United States", "CA": "Canada", "GB": "the United Kingdom",
           "NZ": "New Zealand", "IE": "Ireland", "DE": "Germany", "FR": "France", "NL": "the Netherlands"}


def verify_proxy(query: dict, secret: str) -> bool:
    """Shopify app proxy signature: sorted key=value pairs (lists joined by ','),
    concatenated, HMAC-SHA256 with the app's client secret."""
    sig = query.get("signature")
    if not sig or not secret:
        return False
    parts = []
    for k in sorted(k for k in query if k != "signature"):
        v = query[k]
        parts.append(f"{k}={','.join(v) if isinstance(v, list) else v}")
    want = hmac.new(secret.encode(), "".join(parts).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


def _e(s) -> str:
    """Escape for HTML, and break Liquid tags: Shopify runs the page through Liquid."""
    return html.escape(str(s or "")).replace("{", "&#123;").replace("}", "&#125;")


def _when(iso, with_time=True) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return d.strftime("%d %b %Y, %H:%M") if with_time else d.strftime("%a %d %b")


def _origin(e: dict, dest: str) -> bool:
    """An event that gives away where the parcel came from."""
    text = f" {e.get('location', '')} {e.get('text', '')} ".lower()
    return any(w in text for w in ORIGIN_WORDS)


def timeline(s: dict, dropship: bool) -> list:
    """Events to show the customer, newest first: [(time, text, place)]."""
    events = json.loads(s.get("events") or "[]") if isinstance(s.get("events"), str) else (s.get("events") or [])
    dest = s.get("country") or ""
    out = []
    if dropship:
        # From the first sign it reached the customer's country (customs, last-mile carrier).
        ordered = sorted(events, key=lambda e: e.get("time") or "")
        start = None
        for i, e in enumerate(ordered):
            if (e.get("sub") or "").startswith(ARRIVAL_SUBS) or (
                    e.get("country") and dest and e["country"].upper() == dest.upper()):
                start = i
                break
        local = ordered[start:] if start is not None else []
        out = [(e["time"], e["text"], e.get("location", "")) for e in local if not _origin(e, dest)]
        transit = next((e.get("time") for e in ordered if (e.get("sub") or "").startswith("InTransit")), None)
        if transit and s.get("status") not in ("pending", "info_received"):
            out.append((transit, f"In transit to {COUNTRY.get(dest.upper(), dest) or 'your country'}", ""))
    else:
        out = [(e["time"], e["text"], e.get("location", "")) for e in events]
    if s.get("fulfilled_at"):
        out.append((s["fulfilled_at"], "Your order has shipped", ""))
    if s.get("order_at"):
        out.append((s["order_at"], "Order confirmed", ""))
    def key(ev):
        try:
            return datetime.fromisoformat(str(ev[0]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0
    return sorted(out, key=key, reverse=True)


def _parcel(s: dict, dropship: bool, n: int, of: int) -> str:
    status = s["status"] if s.get("number") else "awaiting"
    step = STEP_OF.get(status, 1)
    bar = "".join(f'<li class="{"done" if i <= step else ""}{" now" if i == step else ""}"><span></span>{_e(t)}</li>'
                  for i, (_, t) in enumerate(STEPS))
    if dropship:
        carrier, number = (s.get("last_mile") or ""), (s.get("last_mile_number") or "")
    else:
        carrier, number = (s.get("carrier_name") or s.get("company") or ""), s.get("number") or ""
    eta = ""
    if status != "delivered" and (s.get("eta_from") or s.get("eta_to")):
        a, b = _when(s.get("eta_from"), False), _when(s.get("eta_to"), False)
        eta = f'<p class="pdt-eta">Estimated delivery <strong>{_e(a)}{" – " + _e(b) if b and b != a else ""}</strong></p>'
    rows = "".join(f'<li><time>{_e(_when(t))}</time><div>{_e(text)}{f"<small>{_e(place)}</small>" if place else ""}</div></li>'
                   for t, text, place in timeline(s, dropship))
    head = f"Parcel {n} of {of}" if of > 1 else f"Order {_e(s.get('order_name'))}"
    return f'''<section class="pdt-card">
  <p class="pdt-kicker">{head}</p>
  <h2>{_e(STATUS_TEXT.get(status, "On its way"))}</h2>
  {eta}
  <ol class="pdt-steps">{bar}</ol>
  {f'<p class="pdt-num">{_e(carrier)} <span>{_e(number)}</span></p>' if number else ""}
  <ol class="pdt-events">{rows}</ol>
</section>'''


STYLE = """<style>
.pdt{max-width:720px;margin:32px auto 56px;padding:0 16px;font:inherit;color:inherit}
.pdt h1{font-size:1.8em;margin:0 0 .3em}.pdt .pdt-sub{opacity:.7;margin:0 0 1.2em}
.pdt form{display:grid;gap:10px;grid-template-columns:1fr 1fr auto;align-items:end;margin-bottom:8px}
.pdt label{display:grid;gap:4px;font-size:.9em}.pdt input{padding:11px 12px;border:1px solid rgba(0,0,0,.2);border-radius:6px;font:inherit;width:100%;box-sizing:border-box}
.pdt button{padding:12px 20px;border:0;border-radius:6px;background:#111;color:#fff;font:inherit;cursor:pointer}
.pdt .pdt-or{font-size:.85em;opacity:.65;margin:4px 0 20px}.pdt .pdt-or a{color:inherit}
.pdt .pdt-msg{padding:12px 14px;border-radius:6px;background:rgba(0,0,0,.05);margin:12px 0}
.pdt-card{border:1px solid rgba(0,0,0,.12);border-radius:10px;padding:20px;margin:18px 0}
.pdt-kicker{font-size:.8em;text-transform:uppercase;letter-spacing:.06em;opacity:.6;margin:0}
.pdt-card h2{margin:.2em 0 .4em;font-size:1.4em}.pdt-eta{margin:0 0 12px}
.pdt-steps{list-style:none;display:flex;padding:0;margin:18px 0;counter-reset:s}
.pdt-steps li{flex:1;position:relative;text-align:center;font-size:.78em;opacity:.45;padding-top:22px}
.pdt-steps li span{position:absolute;top:0;left:50%;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:rgba(0,0,0,.2)}
.pdt-steps li::before{content:"";position:absolute;top:6px;right:50%;width:100%;height:2px;background:rgba(0,0,0,.15)}
.pdt-steps li:first-child::before{display:none}
.pdt-steps li.done{opacity:1}.pdt-steps li.done span,.pdt-steps li.done::before{background:#111}
.pdt-steps li.now span{box-shadow:0 0 0 4px rgba(0,0,0,.12)}
.pdt-num{font-size:.9em;margin:0 0 10px}.pdt-num span{font-family:ui-monospace,monospace}
.pdt-events{list-style:none;padding:0 0 0 14px;margin:0;border-left:2px solid rgba(0,0,0,.12)}
.pdt-events li{position:relative;padding:0 0 14px 12px}
.pdt-events li::before{content:"";position:absolute;left:-20px;top:5px;width:10px;height:10px;border-radius:50%;background:#fff;border:2px solid rgba(0,0,0,.35)}
.pdt-events li:first-child::before{background:#111;border-color:#111}
.pdt-events time{display:block;font-size:.8em;opacity:.6}.pdt-events small{display:block;opacity:.65}
@media(max-width:600px){.pdt form{grid-template-columns:1fr}.pdt-steps li{font-size:.68em}}
</style>"""


def page(store_name: str, parcels: list, ask: dict, message: str, dropship: bool, action: str) -> str:
    form = f'''<form method="get" action="{_e(action)}">
  <label>Order number<input name="order" value="{_e(ask.get("order"))}" placeholder="e.g. #13109" autocomplete="off"></label>
  <label>Email<input name="email" type="email" value="{_e(ask.get("email"))}" placeholder="The email you ordered with"></label>
  <button type="submit">Track</button>
</form>
<p class="pdt-or">Or use your tracking number: <a href="#" onclick="var n=prompt('Tracking number');if(n)location.href='{_e(action)}?nums='+encodeURIComponent(n);return false">enter it here</a></p>'''
    body = "".join(_parcel(p, dropship, i + 1, len(parcels)) for i, p in enumerate(parcels))
    msg = f'<p class="pdt-msg">{_e(message)}</p>' if message else ""
    return f'''{STYLE}
<div class="pdt">
  <h1>Track your order</h1>
  <p class="pdt-sub">Enter your order number and email to see where your parcel is.</p>
  {form}{msg}{body}
</div>'''
