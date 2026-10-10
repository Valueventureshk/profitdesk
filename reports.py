"""Reports: downloadable CSV files (open in Excel / Google Sheets) made from the
same figures the desks show. Plain logic, no AI.

Each builder takes data already gathered by app.py and returns
(columns, rows). app.py stores the CSV so it can be downloaded again later.
"""
import csv
import io

TYPES = {
    "profit_daily": ("Profit by day", "profit"),
    "profit_stores": ("Profit by store", "profit"),
    "cog_orders": ("COG per order line", "cog"),
    "invoices": ("Supplier invoices", "cog"),
    "cash_statement": ("Cash statement", "cash"),
    "tickets": ("Support tickets", "inbox"),
    "ad_bills": ("Ads payable", "cash"),
    "cash_snapshots": ("Cash at end of day", "cash"),
}


def _r(v, d=2):
    return round(v, d) if isinstance(v, (int, float)) and v is not None else v


def profit_daily(dash: dict) -> tuple:
    cols = ["Date", "Sales", "Orders", "Ad spend", "Google", "Meta", "ROAS", "Payment fees",
            "Shopify fees", "COG", "COG estimated", "Net profit", "Net margin %",
            "Held in reserve", "Net available"]
    rows = []
    for r in dash["series"]:
        rows.append([r["date"], _r(r["sales"]), r["orders"], _r(r["ad_spend"]), _r(r["google_spend"]),
                     _r(r["meta_spend"]), _r(r.get("roas")), _r(r["payment_fee"]), _r(r["shopify_fee"]),
                     _r(r["cogs"]), _r(r["cogs_estimated"]), _r(r["net_profit"]),
                     _r((r.get("net_margin") or 0) * 100, 1) if r.get("net_margin") is not None else "",
                     _r(r.get("reserve_held", 0)), _r(r.get("net_available", 0))])
    t = dash["totals"]
    rows.append(["TOTAL", _r(t["sales"]), t["orders"], _r(t["ad_spend"]), _r(t["google_spend"]),
                 _r(t["meta_spend"]), _r(t.get("roas")), _r(t["payment_fee"]), _r(t["shopify_fee"]),
                 _r(t["cogs"]), _r(t["cogs_estimated"]), _r(t["net_profit"]),
                 _r((t.get("net_margin") or 0) * 100, 1) if t.get("net_margin") is not None else "",
                 _r(t.get("reserve_held", 0)), _r(t.get("net_available", 0))])
    return cols, rows


def profit_stores(dash: dict) -> tuple:
    cols = ["Store", "Sales", "Orders", "AOV", "Ad spend", "ROAS", "Fees", "COG", "COG %",
            "Net profit", "Net margin %"]
    rows = []
    for s in sorted(dash.get("stores") or [], key=lambda s: -(s["totals"]["sales"] or 0)):
        t = s["totals"]
        rows.append([s["name"].strip(), _r(t["sales"]), t["orders"], _r(t.get("aov")), _r(t["ad_spend"]),
                     _r(t.get("roas")), _r(t["processing_fee"]), _r(t["cogs"]),
                     _r((t.get("cogs_share") or 0) * 100, 1), _r(t["net_profit"]),
                     _r((t.get("net_margin") or 0) * 100, 1) if t.get("net_margin") is not None else ""])
    t = dash["totals"]
    rows.append(["TOTAL", _r(t["sales"]), t["orders"], _r(t.get("aov")), _r(t["ad_spend"]), _r(t.get("roas")),
                 _r(t["processing_fee"]), _r(t["cogs"]), _r((t.get("cogs_share") or 0) * 100, 1),
                 _r(t["net_profit"]), _r((t.get("net_margin") or 0) * 100, 1) if t.get("net_margin") is not None else ""])
    return cols, rows


def cog_orders(lines: list, stores: dict) -> tuple:
    cols = ["Day", "Store", "Order", "Product", "Qty", "Sales (store currency)", "COG (USD)",
            "Source", "Supplier", "Cancelled", "Note"]
    rows = [[l["day"], stores.get(l["store_id"], ""), l["order_name"], l["product"], l["quantity"],
             _r(l["subtotal"]), _r(l["cost_usd"]), l["source"], l["supplier"] or "",
             "yes" if l["cancelled"] else "", l["note"] or ""] for l in lines]
    return cols, rows


def invoices(lines: list, stores: dict) -> tuple:
    cols = ["Invoice date", "Supplier", "Invoice", "Store", "Order", "Invoice title", "Matched product",
            "Amount (USD)", "Previous (USD)", "Check", "Approved by", "Note"]
    rows = [[l["date"], l["supplier"], l["number"], stores.get(l["store_id"], ""), l["order_name"] or l["order_key"],
             l["title"], l["product"] or "", _r(l["amount"]), _r(l["previous"]) if l["previous"] is not None else "",
             l["status"], l["approved_by"] or "", l["note"] or ""] for l in lines]
    return cols, rows


def cash_statement(st: dict) -> tuple:
    cols = ["Section", "Line", "Amount", "Count", "Via", "Detail"]
    rows = [["", "Opening balance", _r(st["opening"]), "", "", st["start"][:10]]]
    for s in st["sections"]:
        for l in s["lines"]:
            amount = l["amount"] if abs(l["amount"]) >= 0.005 or l.get("info") is None else l["info"]
            rows.append([s["name"], l["line"], _r(amount), l["count"], l["via"],
                         "; ".join(f"{d['name']}: {_r(d['amount'])}" for d in l.get("details") or [])])
    rows.append(["", "Closing balance", _r(st["closing"]), "", "", st["end"][:10]])
    rows.append(["", "Change", _r(st["change"]), "", "", ""])
    c = st.get("checks") or {}
    if c.get("shopify_sales") is not None:
        rows.append(["Checks", "Shopify sales", _r(c["shopify_sales"]), "", "", ""])
        rows.append(["Checks", "Sales received", _r(c["received"]), c.get("payments"), "", ""])
    return cols, rows


def tickets(rows_in: list, stores: dict) -> tuple:
    cols = ["Ticket", "Created", "Store", "Type", "Labels", "Customer", "Subject", "AI summary", "Orders",
            "Status", "Waiting on", "Escalated", "Closed", "Closed by", "Emails"]
    rows = [[t["id"], t["created_at"][:16], stores.get(t["store_id"], ""), t["kind"], t["labels"],
             t["customer_email"], t["subject"], t["summary"] or "", t["orders"], t["status"],
             "us" if t["waiting"] == "us" else "customer", "yes" if t["escalated"] else "",
             (t["closed_at"] or "")[:16], t["closed_by"] or "", t.get("emails", "")] for t in rows_in]
    return cols, rows


def ad_bills(ads: dict) -> tuple:
    cols = ["Store", "Platform", "Account", "Account ID", "Currency", "Owed (own currency)",
            "Owed (display currency)", "Last charge", "Card", "Declined charges"]
    rows = []
    for a in ads.get("accounts", []):
        lc = a.get("last_charge") or {}
        rows.append([a["store"], a["platform"], a["account"], a["account_id"], a["currency"],
                     _r(a["owed"]) if a["owed"] is not None else "", _r(a.get("owed_display")),
                     (lc.get("time") or "")[:16], lc.get("card", ""), len(a.get("failed") or [])])
    rows.append(["TOTAL", "", "", "", "", "", _r(ads.get("total")), "", "", ""])
    return cols, rows


HORIZONS = (("tomorrow", "By tomorrow"), ("d7", "Next 7 days"), ("d14", "Next 14 days"),
            ("d30", "Next 30 days"), ("d60", "Next 60 days"), ("d90", "Next 90 days"))


def cash_snapshots(snaps: list) -> tuple:
    """One row per day: the Cash flow cards as saved at 23:59 (Hong Kong), with the
    change in available + receivable from the day before."""
    cols = ["Date", "Saved at", "Currency", "Available now", "Held", "Receivable", "Available + receivable",
            "Change vs day before", "Ads payable", "Meta owed", "Google owed", "After ad bills"] + \
           [f"{label} (available by)" for _, label in HORIZONS] + ["Problems"]
    rows, prev = [], None
    for s in snaps:
        by = {h["key"]: h["available_by"] for h in s.get("horizons", [])}
        change = (s["position"] - prev["position"]) if prev and prev["currency"] == s["currency"] \
            and s.get("position") is not None and prev.get("position") is not None else ""
        rows.append([s["day"], s["taken_at"][11:19], s["currency"], _r(s.get("available")), _r(s.get("held")),
                     _r(s.get("receivable")), _r(s.get("position")), _r(change), _r(s.get("ads_payable")),
                     _r(s.get("ads_meta")), _r(s.get("ads_google")), _r(s.get("after_ads"))] +
                    [_r(by.get(k)) if by.get(k) is not None else "" for k, _ in HORIZONS] +
                    ["; ".join(s.get("problems") or [])])
        prev = s
    return cols, rows


def to_csv(cols: list, rows: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return "﻿" + buf.getvalue()      # BOM so Excel reads accents correctly
