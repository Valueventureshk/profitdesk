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
COUNTRY = {"AU": "Australia", "US": "United States", "CA": "Canada", "GB": "United Kingdom",
           "NZ": "New Zealand", "IE": "Ireland", "DE": "Germany", "FR": "France", "NL": "Netherlands",
           "BE": "Belgium", "AT": "Austria", "CH": "Switzerland", "IT": "Italy", "ES": "Spain", "PT": "Portugal",
           "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland", "SG": "Singapore",
           "MY": "Malaysia", "AE": "United Arab Emirates", "SA": "Saudi Arabia", "ZA": "South Africa",
           "JP": "Japan", "KR": "South Korea", "MX": "Mexico", "BR": "Brazil", "PH": "Philippines", "IN": "India"}


def destination(s: dict, lang: str = "en") -> str:
    """The customer's state or province ('South Australia', 'California'), else the country."""
    code = (s.get("country") or "").upper()
    return (s.get("province") or "").strip() or COUNTRY.get(code, code) or t("your country", lang)


# The page in the store's own language (setting track_lang:<store id>). English is the key.
LANG = {
    "es": {
        "Ordered": "Pedido", "Shipped": "Enviado", "In transit": "En tránsito", "Out for delivery": "En reparto",
        "Delivered": "Entregado",
        "We're preparing your order": "Estamos preparando tu pedido", "Your order has shipped": "Tu pedido ha sido enviado",
        "On its way": "En camino", "Ready for pickup": "Listo para recoger", "Delivery attempted": "Intento de entrega",
        "There's a delay with this parcel": "Hay un retraso con este paquete",
        "Order confirmed": "Pedido confirmado", "In transit to {}": "En tránsito hacia {}",
        "Parcel {} of {}": "Paquete {} de {}", "Order {}": "Pedido {}", "Estimated delivery": "Entrega estimada",
        "Track your order": "Sigue tu pedido",
        "Enter your order number and email to see where your parcel is.":
            "Introduce tu número de pedido y tu email para ver dónde está tu paquete.",
        "Order number": "Número de pedido", "Email": "Email", "e.g. #13109": "p. ej. #13109",
        "The email you ordered with": "El email con el que hiciste el pedido", "Track": "Seguir",
        "Or use your tracking number:": "O usa tu número de seguimiento:", "enter it here": "introdúcelo aquí",
        "Tracking number": "Número de seguimiento", "your country": "tu país",
        "Enter a tracking number.": "Introduce un número de seguimiento.",
        "We couldn't find that tracking number yet. It can take a day after shipping to show up.":
            "Aún no encontramos ese número de seguimiento. Puede tardar un día en aparecer tras el envío.",
        "We couldn't find an order with that number and email. Check both match your order confirmation.":
            "No encontramos un pedido con ese número y email. Comprueba que coinciden con tu confirmación de pedido.",
        "months": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"],
        "days": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
    },
    "fr": {
        "Ordered": "Commandé", "Shipped": "Expédié", "In transit": "En transit", "Out for delivery": "En livraison",
        "Delivered": "Livré",
        "We're preparing your order": "Nous préparons votre commande", "Your order has shipped": "Votre commande a été expédiée",
        "On its way": "En route", "Ready for pickup": "Prêt à être récupéré", "Delivery attempted": "Tentative de livraison",
        "There's a delay with this parcel": "Ce colis a du retard",
        "Order confirmed": "Commande confirmée", "In transit to {}": "En transit vers {}",
        "Parcel {} of {}": "Colis {} sur {}", "Order {}": "Commande {}", "Estimated delivery": "Livraison estimée",
        "Track your order": "Suivre votre commande",
        "Enter your order number and email to see where your parcel is.":
            "Entrez votre numéro de commande et votre courriel pour voir où se trouve votre colis.",
        "Order number": "Numéro de commande", "Email": "Courriel", "e.g. #13109": "ex. #13109",
        "The email you ordered with": "Le courriel utilisé pour la commande", "Track": "Suivre",
        "Or use your tracking number:": "Ou utilisez votre numéro de suivi :", "enter it here": "saisissez-le ici",
        "Tracking number": "Numéro de suivi", "your country": "votre pays",
        "Enter a tracking number.": "Saisissez un numéro de suivi.",
        "We couldn't find that tracking number yet. It can take a day after shipping to show up.":
            "Nous ne trouvons pas encore ce numéro de suivi. Il peut apparaître jusqu’à un jour après l’expédition.",
        "We couldn't find an order with that number and email. Check both match your order confirmation.":
            "Aucune commande ne correspond à ce numéro et ce courriel. Vérifiez-les dans votre confirmation de commande.",
        "months": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
        "days": ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
    },
}


def t(text: str, lang: str, *args) -> str:
    out = (LANG.get(lang) or {}).get(text, text)
    return out.format(*args) if args else out


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


def _when(iso, with_time=True, lang="en") -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    names = LANG.get(lang)
    if not names:
        return d.strftime("%d %b %Y, %H:%M") if with_time else d.strftime("%a %d %b")
    month = names["months"][d.month - 1]
    if with_time:
        return f"{d.day} {month} {d.year}, {d:%H:%M}"
    return f"{names['days'][d.weekday()]} {d.day} {month}"


def _origin(e: dict, dest: str) -> bool:
    """An event that gives away where the parcel came from."""
    text = f" {e.get('location', '')} {e.get('text', '')} ".lower()
    return any(w in text for w in ORIGIN_WORDS)


def timeline(s: dict, dropship: bool, lang: str = "en") -> list:
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
            out.append((transit, t("In transit to {}", lang, destination(s, lang)), ""))
    else:
        out = [(e["time"], e["text"], e.get("location", "")) for e in events]
    if s.get("fulfilled_at"):
        out.append((s["fulfilled_at"], t("Your order has shipped", lang), ""))
    if s.get("order_at"):
        out.append((s["order_at"], t("Order confirmed", lang), ""))
    def key(ev):
        try:
            return datetime.fromisoformat(str(ev[0]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0
    return sorted(out, key=key, reverse=True)


def _parcel(s: dict, dropship: bool, n: int, of: int, lang: str = "en") -> str:
    status = s["status"] if s.get("number") else "awaiting"
    step = STEP_OF.get(status, 1)
    bar = "".join(f'<li class="{"done" if i <= step else ""}{" now" if i == step else ""}"><span></span>{_e(t(label, lang))}</li>'
                  for i, (_, label) in enumerate(STEPS))
    if dropship:
        carrier, number = (s.get("last_mile") or ""), (s.get("last_mile_number") or "")
    else:
        carrier, number = (s.get("carrier_name") or s.get("company") or ""), s.get("number") or ""
    eta = ""
    if status != "delivered" and (s.get("eta_from") or s.get("eta_to")):
        a, b = _when(s.get("eta_from"), False, lang), _when(s.get("eta_to"), False, lang)
        eta = f'<p class="pdt-eta">{_e(t("Estimated delivery", lang))} <strong>{_e(a)}{" – " + _e(b) if b and b != a else ""}</strong></p>'
    rows = "".join(f'<li><time>{_e(_when(when, True, lang))}</time><div>{_e(text)}{f"<small>{_e(place)}</small>" if place else ""}</div></li>'
                   for when, text, place in timeline(s, dropship, lang))
    head = t("Parcel {} of {}", lang, n, of) if of > 1 else _e(t("Order {}", lang, s.get("order_name") or ""))
    return f'''<section class="pdt-card">
  <p class="pdt-kicker">{head}</p>
  <h2>{_e(t(STATUS_TEXT.get(status, "On its way"), lang))}</h2>
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


def page(store_name: str, parcels: list, ask: dict, message: str, dropship: bool, action: str,
         lang: str = "en") -> str:
    form = f'''<form method="get" action="{_e(action)}">
  <label>{_e(t("Order number", lang))}<input name="order" value="{_e(ask.get("order"))}" placeholder="{_e(t("e.g. #13109", lang))}" autocomplete="off"></label>
  <label>{_e(t("Email", lang))}<input name="email" type="email" value="{_e(ask.get("email"))}" placeholder="{_e(t("The email you ordered with", lang))}"></label>
  <button type="submit">{_e(t("Track", lang))}</button>
</form>
<p class="pdt-or">{_e(t("Or use your tracking number:", lang))} <a href="#" onclick="var n=prompt('{_e(t("Tracking number", lang)).replace("&#x27;", "")}');if(n)location.href='{_e(action)}?nums='+encodeURIComponent(n);return false">{_e(t("enter it here", lang))}</a></p>'''
    body = "".join(_parcel(p, dropship, i + 1, len(parcels), lang) for i, p in enumerate(parcels))
    msg = f'<p class="pdt-msg">{_e(t(message, lang))}</p>' if message else ""
    return f'''{STYLE}
<div class="pdt" lang="{lang}">
  <h1>{_e(t("Track your order", lang))}</h1>
  <p class="pdt-sub">{_e(t("Enter your order number and email to see where your parcel is.", lang))}</p>
  {form}{msg}{body}
</div>'''
