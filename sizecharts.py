"""Size Charts: one place for every store's size charts (replaces Kiwi / Panda).

A chart is a list of blocks shown in a pop-up on product pages:
    {"type": "title", "text"}
    {"type": "table", "label", "unit": "cm"|"in"|"", "header": [..], "rows": [[..]]}
    {"type": "text", "html"}                     (sanitised; may contain a simple table)
    {"type": "image", "url", "alt", "width"}
    {"type": "divider"}
    {"type": "tabs", "tabs": [{"label", "blocks": [...]}]}
Assignments ("applies") say which store's products get it: product, collection,
tag, product type, vendor, or all products. The storefront asks ProfitDesk through
the store's app proxy (store.com/apps/track/sizechart) and a small script draws
the button and pop-up (static/sizechart-widget.js).

AI (owner's Anthropic key): Claude reads a size-chart image into tables, and
Claude Haiku recommends a size from height / weight / fit using the chart.
"""
import base64
import html as _html
import json
import re
from html.parser import HTMLParser

import anthropic

READ_MODEL = "claude-sonnet-5-5"
ADVISE_MODEL = "claude-haiku-5-5"

# ---------------------------------------------------------------- templates

TEMPLATES = {
    "women_clothing": ("Women clothing", "cm", ["Size", "Bust", "Waist", "Hip"],
                       [["XS", "80", "62", "86"], ["S", "84", "66", "90"], ["M", "88", "70", "94"],
                        ["L", "94", "76", "100"], ["XL", "100", "82", "106"], ["2XL", "106", "88", "112"]]),
    "women_dress": ("Women dress", "cm", ["Size", "Bust", "Waist", "Hip", "Length"],
                    [["S", "84", "66", "90", "105"], ["M", "88", "70", "94", "106"], ["L", "94", "76", "100", "107"],
                     ["XL", "100", "82", "106", "108"], ["2XL", "106", "88", "112", "109"]]),
    "men_shirts": ("Men shirts", "cm", ["Size", "Chest", "Shoulder", "Sleeve", "Length"],
                   [["S", "96", "44", "62", "72"], ["M", "102", "46", "63", "74"], ["L", "108", "48", "64", "76"],
                    ["XL", "114", "50", "65", "78"], ["2XL", "120", "52", "66", "80"]]),
    "men_tshirt": ("Men T-shirt", "cm", ["Size", "Chest", "Length"],
                   [["S", "96", "68"], ["M", "102", "70"], ["L", "108", "72"], ["XL", "114", "74"], ["2XL", "120", "76"]]),
    "jacket": ("Jacket", "cm", ["Size", "Chest", "Shoulder", "Sleeve", "Length"],
               [["S", "104", "45", "62", "68"], ["M", "110", "47", "63", "70"], ["L", "116", "49", "64", "72"],
                ["XL", "122", "51", "65", "74"]]),
    "pants": ("Pants", "cm", ["Size", "Waist", "Hip", "Length"],
              [["S", "68", "94", "100"], ["M", "72", "98", "101"], ["L", "76", "102", "102"], ["XL", "80", "106", "103"],
               ["2XL", "84", "110", "104"]]),
    "jeans": ("Jeans", "cm", ["Size", "Waist", "Hip", "Inseam"],
              [["26", "66", "90", "76"], ["28", "71", "95", "77"], ["30", "76", "100", "78"], ["32", "81", "105", "79"],
               ["34", "86", "110", "80"]]),
    "skirt": ("Skirt", "cm", ["Size", "Waist", "Hip", "Length"],
              [["S", "66", "90", "60"], ["M", "70", "94", "61"], ["L", "76", "100", "62"], ["XL", "82", "106", "63"],
               ["2XL", "88", "112", "64"]]),
    "swimwear": ("Swimwear", "cm", ["Size", "Bust", "Waist", "Hip"],
                 [["S", "80-84", "62-66", "88-92"], ["M", "85-89", "67-71", "93-97"], ["L", "90-95", "72-77", "98-103"],
                  ["XL", "96-101", "78-83", "104-109"]]),
    "kids": ("Kids", "cm", ["Size", "Age", "Height", "Chest"],
             [["100", "3-4", "95-105", "56"], ["110", "4-5", "105-115", "58"], ["120", "6-7", "115-125", "62"],
              ["130", "8-9", "125-135", "66"], ["140", "10-11", "135-145", "70"]]),
    "bra": ("Bra", "cm", ["Size", "Underbust", "Bust"],
            [["70A", "68-72", "82-84"], ["75B", "73-77", "89-91"], ["80B", "78-82", "94-96"], ["85C", "83-87", "101-103"]]),
    "women_shoes": ("Women shoes", "", ["EU", "US", "UK", "AU", "Foot length (cm)"],
                    [["35", "5", "3", "4", "22.5"], ["36", "6", "4", "5", "23"], ["37", "6.5", "4.5", "5.5", "23.5"],
                     ["38", "7.5", "5.5", "6.5", "24"], ["39", "8", "6", "7", "24.5"], ["40", "9", "7", "8", "25"],
                     ["41", "9.5", "7.5", "8.5", "25.5"]]),
    "men_shoes": ("Men shoes", "", ["EU", "US", "UK", "AU", "Foot length (cm)"],
                  [["39", "6.5", "6", "6", "24.5"], ["40", "7", "6.5", "6.5", "25"], ["41", "8", "7.5", "7.5", "25.5"],
                   ["42", "8.5", "8", "8", "26"], ["43", "9.5", "9", "9", "27"], ["44", "10", "9.5", "9.5", "27.5"],
                   ["45", "11", "10.5", "10.5", "28.5"]]),
}


def template(key: str) -> list:
    name, unit, header, rows = TEMPLATES[key]
    return [{"type": "title", "text": name}, {"type": "table", "label": "", "unit": unit, "header": header, "rows": rows}]


# ---------------------------------------------------------------- sanitising

_ALLOWED = {"table", "thead", "tbody", "tr", "td", "th", "p", "br", "b", "strong", "i", "em", "u", "ul", "ol",
            "li", "span", "div", "h1", "h2", "h3", "h4", "img", "a", "small", "sup", "sub"}
_ATTRS = {"colspan", "rowspan", "src", "alt", "href"}
_VOID = {"br", "img"}


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.skip += 1
            return
        if self.skip or tag not in _ALLOWED:
            return
        keep = []
        for k, v in attrs:
            if k in _ATTRS and v is not None:
                if k in ("src", "href") and not re.match(r"^https?://", v.strip(), re.I):
                    continue
                keep.append(f' {k}="{_html.escape(v, quote=True)}"')
        self.out.append(f"<{tag}{''.join(keep)}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in _ALLOWED and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(_html.escape(data, quote=False))


def clean_html(s: str) -> str:
    p = _Clean()
    p.feed(s or "")
    return "".join(p.out)


def _txt(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()[:300]


def normalise(blocks) -> list:
    """Trusted shape for whatever the editor or an import sent."""
    out = []
    for b in blocks or []:
        t = (b or {}).get("type")
        if t == "title" and _txt(b.get("text")):
            out.append({"type": "title", "text": _txt(b["text"])})
        elif t == "table":
            header = [_txt(c) for c in (b.get("header") or [])][:30]
            rows = [[_txt(c) for c in r][:30] for r in (b.get("rows") or []) if any(_txt(c) for c in r)][:200]
            if header or rows:
                out.append({"type": "table", "label": _txt(b.get("label")), "unit": b.get("unit") if b.get("unit") in ("cm", "in") else "",
                            "header": header, "rows": rows})
        elif t == "text" and (b.get("html") or "").strip():
            out.append({"type": "text", "html": clean_html(b["html"])[:60000]})
        elif t == "image" and re.match(r"^https?://", b.get("url") or ""):
            out.append({"type": "image", "url": b["url"][:1000], "alt": _txt(b.get("alt")),
                        "width": max(10, min(100, int(b.get("width") or 100)))})
        elif t == "divider":
            out.append({"type": "divider"})
        elif t == "tabs":
            tabs = [{"label": _txt(x.get("label")) or f"Tab {i + 1}", "blocks": normalise(x.get("blocks"))}
                    for i, x in enumerate(b.get("tabs") or []) if x]
            if tabs:
                out.append({"type": "tabs", "tabs": tabs[:8]})
    return out[:60]


# ---------------------------------------------------------------- rendering

_NUM = re.compile(r"^\s*(\d+(?:[.,]\d+)?)(\s*[-–/]\s*(\d+(?:[.,]\d+)?))?\s*$")


def _convert(cell: str, to_in: bool) -> str:
    m = _NUM.match(cell or "")
    if not m or not to_in:
        return cell
    def f(x):
        v = float(x.replace(",", ".")) / 2.54
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f(m.group(1)) + (f"-{f(m.group(3))}" if m.group(3) else "")


def _is_measure(label: str) -> bool:
    return bool(_MEASURE.search(label or "")) and not _SIZE_SYSTEM.search(label or "")


def _table_html(b: dict, inches: bool) -> str:
    """cm -> inches only where a column (or, for sideways tables, a row) is a body measurement,
    never size numbers like EU 38."""
    conv = inches and b.get("unit") == "cm"
    header, rows = b.get("header") or [], b.get("rows") or []
    sideways = bool(rows) and sum(_is_measure(r[0]) for r in rows if r) >= max(1, len(rows) // 2)
    col_ok = [_is_measure(h) for h in header]
    head = "".join(f"<th>{_html.escape(c)}</th>" for c in header)
    body = "".join("<tr>" + "".join(
        (f"<th>{_html.escape(c)}</th>" if i == 0 else
         f"<td>{_html.escape(_convert(c, conv and (_is_measure(r[0]) if sideways else (i < len(col_ok) and col_ok[i]))))}</td>")
        for i, c in enumerate(r)) + "</tr>" for r in rows)
    label = f'<div class="pdsc-label">{_html.escape(b["label"])}</div>' if b.get("label") else ""
    return f'{label}<div class="pdsc-scroll"><table class="pdsc-table">' + \
        (f"<thead><tr>{head}</tr></thead>" if head else "") + f"<tbody>{body}</tbody></table></div>"


def render(blocks: list, inches: bool = False) -> str:
    out = []
    for i, b in enumerate(blocks or []):
        t = b.get("type")
        if t == "title":
            out.append(f'<h3 class="pdsc-title">{_html.escape(b["text"])}</h3>')
        elif t == "table":
            out.append(_table_html(b, inches))
        elif t == "text":
            out.append(f'<div class="pdsc-text">{b["html"]}</div>')
        elif t == "image":
            out.append(f'<div class="pdsc-img"><img src="{_html.escape(b["url"])}" alt="{_html.escape(b.get("alt") or "")}"'
                       f' style="width:{int(b.get("width") or 100)}%" loading="lazy"></div>')
        elif t == "divider":
            out.append('<hr class="pdsc-hr">')
        elif t == "tabs":
            heads = "".join(f'<button type="button" class="pdsc-tab{" on" if j == 0 else ""}" data-tab="{i}-{j}">'
                            f'{_html.escape(x["label"])}</button>' for j, x in enumerate(b["tabs"]))
            panes = "".join(f'<div class="pdsc-pane" data-pane="{i}-{j}"{"" if j == 0 else " hidden"}>'
                            f'{render(x["blocks"], inches)}</div>' for j, x in enumerate(b["tabs"]))
            out.append(f'<div class="pdsc-tabs">{heads}</div>{panes}')
    return "".join(out)


def has_cm(blocks: list) -> bool:
    for b in blocks or []:
        if b.get("type") == "table" and b.get("unit") == "cm":
            return True
        if b.get("type") == "tabs" and any(has_cm(x["blocks"]) for x in b["tabs"]):
            return True
    return False


def tables_text(blocks: list) -> str:
    """The chart's tables as plain text, for the size advisor."""
    out = []
    for b in blocks or []:
        if b.get("type") == "table":
            out.append(f"Table {b.get('label') or ''} (unit: {b.get('unit') or 'as written'})")
            out.append(" | ".join(b.get("header") or []))
            out += [" | ".join(r) for r in b.get("rows") or []]
        elif b.get("type") == "text":
            out.append(re.sub(r"<[^>]+>", " ", b["html"])[:3000])
        elif b.get("type") == "tabs":
            for x in b["tabs"]:
                out.append(f"[{x['label']}]")
                out.append(tables_text(x["blocks"]))
    return "\n".join(out)[:12000]


# ---------------------------------------------------------------- Panda / Kiwi import

class _Tables(HTMLParser):
    """Rows of the tables in a piece of HTML: [[[cell, ...], ...], ...]"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.row, self.cell, self.other = [], None, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(_txt("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.tables:
            if any(self.row):
                self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        elif data.strip():
            self.other.append(data.strip())


_MEASURE = re.compile(r"bust|chest|waist|hip|length|shoulder|sleeve|inseam|thigh|poitrine|tour|hanche|longueur|"
                      r"[ée]paule|manche|cuisse|pecho|busto|cintura|cadera|largo|hombro|manga|muslo|brust|foot|pied|pie\\b", re.I)
_SIZE_SYSTEM = re.compile(r"\b(eu|us|uk|au|jp|cn|kr|br)\b|pointure|talla de|shoe|chaussure|zapato", re.I)


def guess_unit(header: list) -> str:
    h = " ".join(header or [])
    if re.search(r"\(\s*in(ch(es)?)?\s*\)|pouce|inch", h, re.I):
        return "in"
    if re.search(r"\bcm\b", h, re.I) or (_MEASURE.search(h) and not _SIZE_SYSTEM.search(h)):
        return "cm"
    return ""


def html_to_blocks(html: str) -> list:
    """A text block that is really a size table becomes a proper table (so cm/in works)."""
    p = _Tables()
    p.feed(html or "")
    tables = [t for t in p.tables if len(t) >= 2]
    if not tables or len(" ".join(p.other)) > 200:
        return [{"type": "text", "html": html}]
    blocks = []
    for t in tables:
        header, rows = t[0], t[1:]
        blocks.append({"type": "table", "label": "", "unit": guess_unit(header), "header": header, "rows": rows})
    if p.other:
        blocks.append({"type": "text", "html": "<p>" + _html.escape(" ".join(p.other)) + "</p>"})
    return blocks


def _cell(c) -> str:
    return _txt(c.get("data") if isinstance(c, dict) else c)


def from_panda(chart: dict) -> dict:
    """One chart from a Panda Size Chart export."""
    blocks = []
    for f in chart.get("data") or []:
        ft = f.get("fieldType")
        if ft == "title":
            blocks.append({"type": "title", "text": f.get("titleText")})
        elif ft == "table":
            header = [_cell(c) for c in f.get("title") or []]
            rows = [[_cell(c) for c in r] for r in f.get("data") or []]
            if (f.get("appearance") or {}).get("tableDirection") == "horizontal" and header:
                # Sizes across the top: keep as is (first column holds the measure names).
                pass
            unit = (f.get("appearance") or {}).get("conversion") or ""
            unit = "cm" if "cm" in unit.lower() else "in" if "in" in unit.lower() else guess_unit(header)
            blocks.append({"type": "table", "label": "", "unit": unit, "header": header, "rows": rows})
        elif ft == "text":
            blocks += html_to_blocks(f.get("text") or "")
        elif ft == "image":
            blocks.append({"type": "image", "url": ((f.get("file") or {}).get("url") or ""),
                           "alt": ((f.get("file") or {}).get("alt") or ""),
                           "width": re.sub(r"\D", "", str(((f.get("appearance") or {}).get("style") or {}).get("width") or "100")) or 100})
        elif ft == "International Chart":
            cs = f.get("chartSize") or []
            if cs:
                blocks.append({"type": "table", "label": "International sizes", "unit": "",
                               "header": [str(x) for x in cs[0]], "rows": [[str(x) for x in r] for r in cs[1:]]})
        elif ft == "divider":
            blocks.append({"type": "divider"})
    return {"name": _txt(chart.get("title")) or "Size chart", "blocks": normalise(blocks)}


def parse_export(data) -> list:
    """Panda's export (a list of charts). Kiwi exports are recognised by shape as best we can."""
    if isinstance(data, dict):
        data = data.get("data") or data.get("charts") or data.get("sizeCharts") or data.get("templates") or [data]
    out = []
    for c in data or []:
        if not isinstance(c, dict):
            continue
        if "data" in c and isinstance(c.get("data"), list) and any(isinstance(f, dict) and "fieldType" in f for f in c["data"]):
            out.append(from_panda(c))
        else:   # generic: {name/title, header, rows} or {name, html}
            name = _txt(c.get("name") or c.get("title")) or "Size chart"
            blocks = []
            if c.get("html") or c.get("content"):
                blocks.append({"type": "text", "html": c.get("html") or c.get("content")})
            if c.get("rows"):
                blocks.append({"type": "table", "header": c.get("header") or [], "rows": c["rows"], "unit": c.get("unit") or ""})
            out.append({"name": name, "blocks": normalise(blocks)})
    return [c for c in out if c["blocks"]]


def norm_title(s: str) -> str:
    s = _html.unescape(s or "").lower()
    s = re.sub(r"[\s ]+", " ", s)
    return re.sub(r"[^\w| ]", "", s).strip()


# ---------------------------------------------------------------- AI

class AIError(RuntimeError):
    pass


EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "title": {"type": "string"},
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "unit": {"type": "string", "enum": ["cm", "in", ""]},
                           "header": {"type": "array", "items": {"type": "string"}},
                           "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
            "required": ["label", "unit", "header", "rows"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["found", "title", "tables", "notes"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """You turn size chart images (often from Chinese suppliers on 1688, AliExpress or Taobao) into clean size tables for an online fashion store.
Read every size chart in the images. Output one table per chart (e.g. tops and bottoms separately). First column = size (S, M, L... or numbers); the other columns = measurements.
Translate headings into {language}. Use clear names (e.g. Bust, Waist, Hip, Length, Shoulder, Sleeve, Foot length) in that language.
Units: {unit_rule}
Keep numbers exactly as in the image except for unit conversion (1 in = 2.54 cm, round to 1 decimal). Ranges stay ranges (e.g. 84-88).
If an image is not a size chart, ignore it. If no image has a size chart, return found=false and empty tables.
"title": a short chart title in {language} (e.g. "Size guide"). "notes": short fitting advice from the image in {language}, or empty."""


async def read_images(api_key: str, images: list, language: str = "English", unit: str = "cm") -> dict:
    """images: [(media_type, bytes) | ("url", url)]. Returns {found, title, tables, notes}."""
    content = []
    for kind, data in images[:6]:
        if kind == "url":
            content.append({"type": "image", "source": {"type": "url", "url": data}})
        else:
            content.append({"type": "image", "source": {"type": "base64", "media_type": kind,
                                                         "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": "Extract the size chart(s)."})
    unit_rule = ("give measurements in centimetres (convert inches to cm)" if unit == "cm" else
                 "give measurements in inches (convert cm to inches)" if unit == "in" else
                 "keep the units used in the image and say which in 'unit'")
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=120)
    try:
        resp = await client.messages.create(
            model=READ_MODEL, max_tokens=8000,
            system=EXTRACT_SYSTEM.format(language=language, unit_rule=unit_rule),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
            messages=[{"role": "user", "content": content}])
    except anthropic.AuthenticationError as e:
        raise AIError("Anthropic didn't accept the API key (Settings → AI).") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"The AI couldn't read the image ({e.status_code}).") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Couldn't reach the AI.") from e
    if resp.stop_reason == "refusal":
        raise AIError("The AI declined this image.")
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        return json.loads(text)
    except ValueError as e:
        raise AIError("Couldn't read the AI's answer.") from e


def blocks_from_ai(res: dict) -> list:
    blocks = []
    if res.get("title"):
        blocks.append({"type": "title", "text": res["title"]})
    tables = res.get("tables") or []
    plain = re.compile(r"^\s*(size\s*chart|size\s*guide|sizes?|guide des tailles|tableau des tailles|gu[ií]a de tallas|tabla de tallas)\s*$", re.I)
    for t in tables:
        label = (t.get("label") or "").strip()
        if len(tables) == 1 and (plain.match(label) or label.lower() == (res.get("title") or "").strip().lower()):
            label = ""          # one table: its label would only repeat the heading
        blocks.append({"type": "table", "label": label, "unit": t.get("unit") or "",
                       "header": t.get("header") or [], "rows": t.get("rows") or []})
    if (res.get("notes") or "").strip():
        blocks.append({"type": "text", "html": "<p>" + _html.escape(res["notes"]) + "</p>"})
    return normalise(blocks)


ADVISE_SCHEMA = {
    "type": "object",
    "properties": {"size": {"type": "string"}, "range": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["size", "range", "reason"],
    "additionalProperties": False,
}

ADVISE_SYSTEM = """You are a store's size advisor. From the customer's height, weight, fit preference and body type, estimate their body measurements (bust/chest, waist, hip, foot length as relevant) and pick the best size from THIS product's size chart.
Use only sizes that appear in the chart. If between sizes: snug fit -> smaller, loose fit -> bigger.
Answer in {language}: "size" = the recommended size exactly as written in the chart; "range" = e.g. "M - L" when two could work, else the same size; "reason" = one short friendly sentence (max 20 words)."""


async def advise(api_key: str, chart_text: str, answers: dict, language: str = "English") -> dict:
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=30)
    user = (f"Size chart:\n{chart_text}\n\nCustomer: height {answers.get('height')} {answers.get('height_unit', 'cm')}, "
            f"weight {answers.get('weight')} {answers.get('weight_unit', 'kg')}, fit preference: {answers.get('fit', 'regular')}, "
            f"body type: {answers.get('body', 'average')}" + (f", usual size: {answers['usual']}" if answers.get("usual") else ""))
    try:
        resp = await client.messages.create(
            model=ADVISE_MODEL, max_tokens=800, system=ADVISE_SYSTEM.format(language=language),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ADVISE_SCHEMA}},
            messages=[{"role": "user", "content": user}])
    except anthropic.APIError as e:
        raise AIError("The size advisor is unavailable right now.") from e
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        return json.loads(text)
    except ValueError as e:
        raise AIError("The size advisor is unavailable right now.") from e
