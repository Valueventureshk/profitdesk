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
START_RE = re.compile(r"^\s*#?[A-Z&]{0,5}\d{3,8}[A-Z&]{0,4}\s+\S")
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
    # Long product names wrap: "TM13241 Elune™ - Baskets ... mesh" / "respirant" / "$13.70".
    # Join a line that starts with an order number but has no price yet with the lines after it.
    rows, pending = [], None
    for raw in text.splitlines():
        if pending is not None:
            if START_RE.match(raw) or re.search(r"total", raw, re.I):
                rows.append(pending)                 # never found its price: leave it as it was
                pending = None
            else:
                pending = f"{pending.rstrip()} {raw.strip()}"
                if LINE_RE.match(pending):
                    rows.append(pending)
                    pending = None
                continue
        if START_RE.match(raw) and not LINE_RE.match(raw):
            pending = raw
            continue
        rows.append(raw)
    if pending is not None:
        rows.append(pending)
    for raw in rows:
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


def _status(amount: float, units: float, known, any_known=None, tier: int = 1) -> dict:
    """ok / changed / new for an invoice line, by price per item, against earlier prices for the
    same bundle size (tier = how many of the product were in the order)."""
    unit = amount / (units or 1)
    each = f" each ({units:g} items, {amount:.2f} USD)" if units > 1 else ""
    if known is None and any_known is not None:
        return {"status": "new", "previous": None,
                "note": f"First single-item price: {unit:.2f} USD (earlier prices were from bundle orders: {any_known[1]:.2f})"}
    if known is None:
        return {"status": "new", "previous": None, "note": "First invoice for this product" + (each and f" ·{each}")}
    if abs(known[1] - unit) > 0.05:
        return {"status": "changed", "previous": known[1], "note": f"Was {known[1]:.2f} USD, now {unit:.2f} USD{each}"}
    return {"status": "ok", "previous": known[1], "note": each.strip()}


def judge(rows: list, catalog) -> None:
    """Price check for matched invoice lines, by order (changes rows in place).

    Each row: store_id, order_name, product, amount, units (items the line is for), tier (items in
    the whole Shopify order).

    1 item in the order    the price per item against the product's last single-item price:
                           ok / changed / new.
    2+ items (any products) suppliers discount these, so single prices don't apply line by line.
                           The order's items at single prices are added up ("singles") and the
                           difference to what was charged is the bundle discount. Never flagged
                           when cheaper; flagged (changed) when the bundle costs more than the
                           items would on their own.
    """
    groups = {}
    for r in rows:
        if r.get("status") in ("unmatched", "duplicate", "superseded") or not r.get("product"):
            continue
        groups.setdefault((r.get("invoice_id"), r["store_id"], r.get("order_name") or r.get("order")), []).append(r)
    for g in groups.values():
        items = max(int(g[0].get("tier") or 0), int(round(sum(r.get("units") or 1 for r in g))))
        for r in g:
            r["tier"] = items
            r["each"] = None
        if items <= 1:
            for r in g:
                known = catalog.single(r["store_id"], r["product"]) if catalog else None
                any_known = catalog.latest(r["store_id"], r["product"]) if catalog else None
                r.update(_status(r["amount"], r.get("units") or 1, known, any_known, 1))
                r["singles"] = known[1] * (r.get("units") or 1) if known else None
            continue
        charged = sum(r["amount"] for r in g)
        missing = []
        for r in g:
            one = catalog.single(r["store_id"], r["product"]) if catalog else None
            r["previous"] = one[1] if one else None
            r["singles"] = one[1] * (r.get("units") or 1) if one else None
            if not one:
                missing.append(r["product"])
        if missing:
            names = "; ".join(sorted({m.split(" - ")[0][:40] for m in missing}))
            for r in g:
                r.update(status="new" if r["singles"] is None else "ok",
                         note=f"Bundle of {items}: charged {charged:.2f} USD. No single price yet for {names}")
            continue
        full = sum(r["singles"] for r in g)
        off = full - charged
        if off < -0.05:
            for r in g:
                r.update(status="changed", note=f"Bundle of {items} charged {charged:.2f} USD, but on their own "
                                                f"these items cost {full:.2f} USD ({-off:.2f} more)")
        else:
            for r in g:
                r.update(status="ok", note=f"Bundle of {items}: singles {full:.2f} USD, charged {charged:.2f} USD, "
                                           f"bundle discount {max(off, 0):.2f} USD"
                                           + (f" ({off / full * 100:.0f}%)" if full and off > 0.005 else ""))


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
        items = sum((c["quantity"] or 1) for c in candidates if c.get("source") != "cancelled") or 1
        row.update(status="pending", _line=(best["order_name"], best["line_no"]), _qty=best["quantity"] or 1,
                   tier=items)
        out.append(row)

    # One invoice line can be for several items (2 pairs = one $26 line). Prices are compared
    # per item: an order line of 2 billed on 1 invoice line means that line covers 2 items.
    per_line = {}
    for r in out:
        if r.get("status") == "pending":
            per_line[r["_line"]] = per_line.get(r["_line"], 0) + 1
    for r in out:
        if r.get("status") != "pending":
            r.setdefault("units", 1.0)
            continue
        r["units"] = max(1.0, r.pop("_qty") / per_line[r.pop("_line")])
    judge(out, catalog)

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


def xlsx_text(data: bytes, filename: str = "") -> str:
    """Graypop's Excel invoice (one row per item; the order's cost, after discount, in `total_price`
    on its first row) turned into the same one-line-per-item text as a Dayone PDF. An order's cost
    is shared across its items by quantity."""
    try:
        import openpyxl
    except ImportError as e:
        raise InvoiceError("Excel reading isn't installed on the server yet.") from e
    try:
        ws = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True).worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    except Exception as e:
        raise InvoiceError(f"Couldn't open that Excel file ({type(e).__name__}).") from e
    if not rows:
        raise InvoiceError("That Excel file is empty.")
    head = [str(h or "").strip().lower() for h in rows[0]]

    def col(*names):
        return next((head.index(n) for n in names if n in head), None)
    c_order, c_name, c_var = col("order number", "order"), col("lineitem name", "product"), col("variants", "variant")
    c_qty, c_total = col("lineitem quantity", "quantity"), col("total_price", "total price", "amount")
    c_when = col("payment dated", "date")
    if c_order is None or c_name is None or c_total is None:
        raise InvoiceError("This Excel file doesn't have Order number, Lineitem name and total_price columns.")
    orders, order_list, when = {}, [], ""
    for r in rows[1:]:
        r = r + [None] * (len(head) - len(r))
        oid = str(r[c_order] or "").strip()
        if not oid or not re.match(r"^#?[A-Z&]{0,5}\d{3,8}[A-Z&]{0,4}$", oid):
            continue
        if oid not in orders:
            orders[oid] = {"total": None, "items": []}
            order_list.append(oid)
        if r[c_total] not in (None, ""):
            try:
                orders[oid]["total"] = (orders[oid]["total"] or 0) + float(r[c_total])
            except (TypeError, ValueError):
                pass
        name = str(r[c_name] or "").strip()
        var = str(r[c_var] or "").strip() if c_var is not None else ""
        if var.startswith("__"):                     # bundle-app codes, not a real variant
            var = ""
        try:
            qty = max(1, int(float(r[c_qty] or 1))) if c_qty is not None else 1
        except (TypeError, ValueError):
            qty = 1
        orders[oid]["items"].append((f"{name} - {var}" if var else name, qty))
        if c_when is not None and r[c_when]:
            when = max(when, str(r[c_when])[:10])
    lines, total = [], 0.0
    for oid in order_list:
        o = orders[oid]
        if o["total"] is None or not o["items"]:
            continue
        total += o["total"]
        units = sum(q for _, q in o["items"])
        left = round(o["total"], 2)
        for i, (name, q) in enumerate(o["items"]):
            amt = left if i == len(o["items"]) - 1 else round(o["total"] * q / units, 2)
            left = round(left - amt, 2)
            lines.append(f"{oid} {name.replace('$', '')} ${amt:.2f}")
    if not lines:
        raise InvoiceError("No orders with a cost found in that Excel file.")
    stem = re.sub(r"\.xlsx?$", "", re.sub(r"^\$[\d.,]+-", "", filename.rsplit("/", 1)[-1]), flags=re.I).strip()
    date_line = f"Invoice date: {when.replace('-', '/')}\n" if re.match(r"\d{4}-\d{2}-\d{2}", when) else ""
    return (f"Graypop\nInvoice # {re.sub(r'[^A-Za-z0-9-]', '-', stem)[:60] or 'excel'}\n{date_line}"
            + "\n".join(lines) + f"\nTotal amount ${total:.2f}\n")
