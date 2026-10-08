"""Supplier invoices: read them, match every line to an order, flag what's off.

An invoice is one store's fulfilled items from one supplier, one line per item:

    AS13102 BohoSands | Orthopedic Slippers for Women $11.60

Dayone's PDFs are plain text, so no AI is needed to read them. Each line is
checked against what ProfitDesk knows:

    ok         matches an order line and the product's usual cost
    changed    the product's cost differs from its last known cost: someone checks
               with the supplier and approves it, and only then does it become the
               product's cost for future orders (this order uses it either way,
               because it's what was charged)
    new        first time this product is invoiced in this store
    duplicate  this order line was already on an earlier invoice
    unmatched  no such order / product in the store

and orders that were fulfilled in the invoice's order range but are not on it
are listed as missing.
"""
import io
import re
from datetime import datetime

import cog

LINE_RE = re.compile(r"^\s*#?([A-Z&]{0,5}\d{3,8}[A-Z&]{0,4})\s+(.+?)\s+(?:US)?\$\s*([\d,]+\.?\d*)\s*$")
SUPPLIERS = {"dayone": "Dayone", "graypop": "Graypop", "winwin": "WinWin"}


class InvoiceError(RuntimeError):
    pass


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise InvoiceError("PDF reading isn't installed on the server yet.") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        raise InvoiceError(f"Couldn't read that PDF ({type(e).__name__}).") from e


def parse(text: str) -> dict:
    """{supplier, number, date, total, lines: [{order, title, amount}]}"""
    low = text.lower()
    supplier = next((name for key, name in SUPPLIERS.items() if key in low), "")
    number = re.search(r"invoice\s*(?:#|no\.?|number)\s*[:：]?\s*([A-Za-z0-9-]{3,})", text, re.I)
    when = re.search(r"invoice date\s*[:：]?\s*(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", text, re.I)
    total = re.search(r"total amount\s*\$?\s*([\d,]+\.?\d*)", text, re.I) or \
        re.search(r"total\s*[:：]\s*\$?\s*([\d,]+\.?\d*)", text, re.I)
    lines = []
    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if m:
            lines.append({"order": cog.order_key(m.group(1)), "title": m.group(2).strip(),
                          "amount": float(m.group(3).replace(",", ""))})
    if not lines:
        raise InvoiceError("No invoice lines found. Each line should start with the order "
                           "number and end with the amount, e.g. \"AS13102 Slippers $11.60\".")
    return {
        "supplier": supplier,
        "number": number.group(1) if number else "",
        "date": (f"{when.group(1)}-{int(when.group(2)):02d}-{int(when.group(3)):02d}"
                 if when else datetime.now().date().isoformat()),
        "total": float(total.group(1).replace(",", "")) if total else sum(l["amount"] for l in lines),
        "lines": lines,
    }


def check(inv: dict, store_of, order_lines: dict, catalog, invoiced: dict) -> dict:
    """Match and flag every line.

    store_of(order key) -> store id or None
    order_lines: {(store, order key): [cog_lines rows]}
    catalog: cog.Catalog (product cost history)
    invoiced: {(store, order key, product): invoice number} already on earlier invoices
    """
    out, stores = [], set()
    used = {}
    for n, l in enumerate(inv["lines"]):
        sid = store_of(l["order"])
        row = {**l, "line_no": n, "store_id": sid, "status": "unmatched", "product": None,
               "previous": None, "note": ""}
        if sid is None:
            row["note"] = "Order number isn't from a connected store"
            out.append(row)
            continue
        stores.add(sid)
        candidates = order_lines.get((sid, l["order"]), [])
        if not candidates:
            row["note"] = "No such order in this store (yet)"
            out.append(row)
            continue
        # best product in the order, allowing for each item of a quantity >1
        def free(c):
            return used.get((c["order_name"], c["line_no"]), 0) < c["quantity"]
        ranked = sorted((c for c in candidates if free(c)),
                        key=lambda c: -cog.title_match(l["title"], c["product"]))
        best = ranked[0] if ranked else None
        if not best or cog.title_match(l["title"], best["product"]) < 0.6:
            row["note"] = "Product not found in this order"
            out.append(row)
            continue
        used[(best["order_name"], best["line_no"])] = used.get((best["order_name"], best["line_no"]), 0) + 1
        row["product"] = best["product"]
        row["order_name"] = best["order_name"]
        key = (sid, l["order"], cog.norm(best["product"]))
        if key in invoiced:
            row.update(status="duplicate", note=f"Already on invoice {invoiced[key]}")
            out.append(row)
            continue
        known = catalog.latest(sid, best["product"]) if catalog else None
        if known is None:
            row.update(status="new", note="First invoice for this product")
        elif abs(known[1] - l["amount"]) > 0.05:
            row.update(status="changed", previous=known[1],
                       note=f"Was {known[1]:.2f} USD, now {l['amount']:.2f} USD")
        else:
            row.update(status="ok", previous=known[1])
        out.append(row)

    # Fulfilled orders inside the invoice's order range that it doesn't include.
    missing = []
    nums = [int(re.search(r"\d+", l["order"]).group()) for l in inv["lines"]]
    tags = {cog.tag_of(l["order"]) for l in inv["lines"]}
    if nums and len(stores) == 1:
        sid = next(iter(stores))
        lo, hi = min(nums), max(nums)
        on_invoice = {l["order"] for l in inv["lines"]}
        for (s, okey), rows in order_lines.items():
            if s != sid or okey in on_invoice or cog.tag_of(okey) not in tags:
                continue
            num = int(re.search(r"\d+", okey).group())
            if lo <= num <= hi and any((r["fulfillment"] or "").upper() == "FULFILLED"
                                       and not r["cancelled"] for r in rows):
                missing.append({"order": rows[0]["order_name"],
                                "products": [r["product"] for r in rows]})
    missing.sort(key=lambda m: m["order"])
    counts = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"lines": out, "missing": missing, "counts": counts,
            "store_id": next(iter(stores)) if len(stores) == 1 else None,
            "sum": round(sum(l["amount"] for l in inv["lines"]), 2)}
