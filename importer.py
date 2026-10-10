"""Product Importer: copy products from any Shopify store into our stores (like Poky).

Every Shopify store publishes its catalogue as JSON:
    /products/<handle>.json                    one product
    /collections/<handle>/products.json        a collection (250 per page)
    /products.json                             the whole store (250 per page)
Prices there are in the source store's currency (read from /cart.js).

Each product is rebuilt with the owner's rules (price, rounding, currency, tags,
status...) and created in the chosen stores with Shopify's productSet. Images are
given to Shopify as web addresses; Shopify downloads and keeps its own copy, so
nothing depends on the source store afterwards.
"""
import html as _html
import math
import random
import re
import string
from urllib.parse import urlsplit

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept": "application/json,text/html;q=0.8"}

DEFAULTS = {
    "active": False,              # import as Active (else Draft)
    "vendor": False,              # keep the source vendor name
    "tags": True,                 # keep the source tags
    "taxable": True,
    "sku": True, "random_sku": False,
    "barcode": True, "random_barcode": False,
    "strip_links": False, "strip_alt": False,
    "convert_currency": True,     # into each target store's own currency
    "source_metafield": False,    # save the source URL in a metafield
    "single_variant": False,      # import without variants
    "unique": True,               # skip products whose handle already exists
    "track_inventory": False,     # dropshipping: don't track stock, keep selling
    "price_enabled": True,
    "price_mode": "increase", "price_pct": 0.0, "price_fixed": 0.0,
    "price_whole_end": "", "price_cents": "",
    "compare_mode": "source",     # source | multiply | none
    "compare_pct": 0.0, "compare_multiplier": 1.5,
    "compare_whole_end": "", "compare_cents": "",
    "custom_tags": [], "product_type": "",
    "size_chart_mode": "none",    # none | chart (assign a chosen chart) | ai (read the source's size chart image)
    "size_chart_id": 0,
}


def size_chart_images(p: dict, limit: int = 4) -> list:
    """Images likely to be a size chart: the ones in the description first, then product
    images whose name or alt mentions size / chart (incl. Chinese 尺码 / 尺寸)."""
    out = []
    for src in re.findall(r'<img[^>]+src=["\']([^"\']+)', p.get("body_html") or "", re.I):
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http") and src not in out:
            out.append(src)
    hint = re.compile(r"size|chart|guide|measure|taille|talla|尺码|尺寸", re.I)
    for i in p.get("images") or []:
        if hint.search((i.get("src") or "") + " " + (i.get("alt") or "")) and i["src"] not in out:
            out.append(i["src"])
    return out[:limit]


class ImportError_(RuntimeError):
    pass


def parse_url(url: str) -> dict:
    """{'host', 'kind': product|collection|store, 'handle'}"""
    url = (url or "").strip()
    if not url:
        raise ImportError_("Paste a product, collection or store URL.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if not host or "." not in host:
        raise ImportError_("That doesn't look like a web address.")
    path = parts.path.rstrip("/")
    m = re.search(r"/products/([^/?#]+)", path)
    if m:
        return {"host": host, "kind": "product", "handle": m.group(1).removesuffix(".json")}
    m = re.search(r"/collections/([^/?#]+)", path)
    if m:
        return {"host": host, "kind": "collection", "handle": m.group(1)}
    return {"host": host, "kind": "store", "handle": None}


async def _get(client, url):
    try:
        r = await client.get(url, headers=UA, follow_redirects=True)
    except httpx.RequestError as e:
        raise ImportError_(f"Couldn't reach that store ({type(e).__name__}).") from e
    if r.status_code == 404:
        raise ImportError_("Nothing found at that address. Check the URL, or the product may have been removed.")
    if r.status_code == 401 or "password" in str(r.url):
        raise ImportError_("That store is password-protected.")
    if r.status_code == 429:
        raise ImportError_("That store is limiting requests. Wait a minute and try again.")
    if r.status_code >= 400:
        raise ImportError_(f"That store answered with an error ({r.status_code}).")
    try:
        return r.json(), str(r.url)
    except ValueError as e:
        raise ImportError_("That address isn't a Shopify store (or it hides its product data).") from e


async def source_currency(client, host: str) -> str:
    try:
        data, _ = await _get(client, f"https://{host}/cart.js")
        return (data.get("currency") or "").upper() or None
    except ImportError_:
        return None


async def load(url: str, max_products: int = 2000) -> dict:
    """The products at a URL, in the source store's currency."""
    u = parse_url(url)
    async with httpx.AsyncClient(timeout=30) as client:
        currency = await source_currency(client, u["host"])
        if u["kind"] == "product":
            data, final = await _get(client, f"https://{u['host']}/products/{u['handle']}.json")
            products = [data.get("product") or {}]
        else:
            base = (f"https://{u['host']}/collections/{u['handle']}/products.json" if u["kind"] == "collection"
                    else f"https://{u['host']}/products.json")
            products, page = [], 1
            while len(products) < max_products:
                data, final = await _get(client, f"{base}?limit=250&page={page}")
                batch = data.get("products") or []
                products += batch
                if len(batch) < 250:
                    break
                page += 1
    products = [p for p in products if p.get("id") and p.get("title")]
    if not products:
        raise ImportError_("No products found there.")
    return {"host": u["host"], "kind": u["kind"], "currency": currency,
            "products": [summary(p, u["host"]) for p in products[:max_products]], "raw": products[:max_products]}


def summary(p: dict, host: str) -> dict:
    prices = [float(v.get("price") or 0) for v in p.get("variants") or []]
    imgs = p.get("images") or []
    return {"id": p["id"], "handle": p.get("handle"), "title": p.get("title"), "vendor": p.get("vendor"),
            "image": (imgs[0].get("src") if imgs else None), "images": len(imgs),
            "variants": len(p.get("variants") or []), "price_min": min(prices) if prices else 0,
            "price_max": max(prices) if prices else 0, "url": f"https://{host}/products/{p.get('handle')}"}


# ---------------------------------------------------------------- rules

def _end_whole(x: float, digits: str) -> float:
    """Make the whole part end with these digits, rounding up: 37.40 with '9' -> 39.40."""
    if not digits or not digits.isdigit():
        return x
    whole, frac = int(x), x - int(x)
    step = 10 ** len(digits)
    target = (whole // step) * step + int(digits)
    if target < whole:
        target += step
    return target + frac


def _end_cents(x: float, cents: str) -> float:
    if cents == "" or cents is None or not str(cents).isdigit():
        return round(x, 2)
    return int(x) + int(str(cents).ljust(2, "0")[:2]) / 100


def adjust(x: float, mode: str, pct: float, fixed: float = 0.0) -> float:
    sign = -1 if mode == "decrease" else 1
    return max(0.0, x * (1 + sign * (pct or 0) / 100) + sign * (fixed or 0))


def price_for(src: float, cfg: dict, factor: float) -> float:
    x = src * factor
    if cfg.get("price_enabled"):
        x = adjust(x, cfg.get("price_mode"), cfg.get("price_pct"), cfg.get("price_fixed"))
        x = _end_whole(x, str(cfg.get("price_whole_end") or ""))
        x = _end_cents(x, str(cfg.get("price_cents") or ""))
    return round(x, 2)


def compare_for(src_compare, new_price: float, cfg: dict, factor: float):
    mode = cfg.get("compare_mode") or "source"
    if mode == "none":
        return None
    if mode == "multiply":
        x = new_price * float(cfg.get("compare_multiplier") or 1)
    else:
        if not src_compare:
            return None
        x = adjust(float(src_compare) * factor, "increase", cfg.get("compare_pct") or 0)
    x = _end_whole(x, str(cfg.get("compare_whole_end") or ""))
    x = _end_cents(x, str(cfg.get("compare_cents") or ""))
    return round(x, 2) if x > new_price else None


def _clean_html(body: str, cfg: dict) -> str:
    body = body or ""
    if cfg.get("strip_links"):
        body = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", body, flags=re.I | re.S)
    if cfg.get("strip_alt"):
        body = re.sub(r'(<img\b[^>]*?)\salt\s*=\s*("[^"]*"|\'[^\']*\')', r"\1", body, flags=re.I)
    return body


def _rand(n: int, chars=string.ascii_uppercase + string.digits) -> str:
    return "".join(random.choice(chars) for _ in range(n))


def build_input(p: dict, cfg: dict, factor: float, source_url: str) -> dict:
    """Shopify productSet input for one source product."""
    options = [o for o in (p.get("options") or []) if o.get("name")]
    variants = p.get("variants") or []
    has_variants = not (len(variants) == 1 and (variants[0].get("title") == "Default Title")) and not cfg.get("single_variant")
    imgs = p.get("images") or []
    files = [{"originalSource": i["src"], "contentType": "IMAGE", "alt": (i.get("alt") or "")[:500]}
             for i in imgs if i.get("src")]
    img_by_id = {i.get("id"): i.get("src") for i in imgs}

    def variant(v, with_options: bool):
        price = price_for(float(v.get("price") or 0), cfg, factor)
        out = {"price": f"{price:.2f}",
               "taxable": bool(cfg.get("taxable")),
               "inventoryPolicy": "DENY" if cfg.get("track_inventory") else "CONTINUE",
               "inventoryItem": {"tracked": bool(cfg.get("track_inventory"))}}
        comp = compare_for(v.get("compare_at_price"), price, cfg, factor)
        if comp:
            out["compareAtPrice"] = f"{comp:.2f}"
        sku = (v.get("sku") or "") if cfg.get("sku") else ""
        if not sku and cfg.get("random_sku"):
            sku = "PD-" + _rand(8)
        if sku:
            out["inventoryItem"]["sku"] = sku[:255]
        bc = (v.get("barcode") or "") if cfg.get("barcode") else ""
        if not bc and cfg.get("random_barcode"):
            bc = _rand(12, string.digits)
        if bc:
            out["barcode"] = bc
        if with_options:
            out["optionValues"] = [{"optionName": o["name"], "name": v.get(f"option{i + 1}") or "Default"}
                                   for i, o in enumerate(options[:3])]
            src = img_by_id.get(v.get("image_id")) or ((v.get("featured_image") or {}).get("src"))
            if src:
                out["file"] = {"originalSource": src, "contentType": "IMAGE"}
        else:
            out["optionValues"] = [{"optionName": "Title", "name": "Default Title"}]
        return out

    if has_variants and options:
        product_options = []
        for i, o in enumerate(options[:3]):
            seen, values = set(), []
            for v in variants:
                val = v.get(f"option{i + 1}") or "Default"
                if val not in seen:
                    seen.add(val)
                    values.append({"name": val})
            product_options.append({"name": o["name"], "position": i + 1, "values": values})
        var_inputs = [variant(v, True) for v in variants[:2048]]
    else:
        product_options = [{"name": "Title", "position": 1, "values": [{"name": "Default Title"}]}]
        var_inputs = [variant(variants[0] if variants else {"price": 0}, False)]

    tags = []
    if cfg.get("tags"):
        src_tags = p.get("tags") or []
        tags += src_tags if isinstance(src_tags, list) else [t.strip() for t in str(src_tags).split(",")]
    tags += [t for t in (cfg.get("custom_tags") or []) if t]
    inp = {
        "title": _html.unescape(p.get("title") or "")[:255],
        "handle": p.get("handle"),
        "descriptionHtml": _clean_html(p.get("body_html") or "", cfg),
        "status": "ACTIVE" if cfg.get("active") else "DRAFT",
        "productOptions": product_options,
        "variants": var_inputs,
        "files": files[:250],
        "tags": sorted({t.strip() for t in tags if t and t.strip()})[:250],
    }
    if cfg.get("vendor") and p.get("vendor"):
        inp["vendor"] = p["vendor"][:255]
    if cfg.get("product_type") or p.get("product_type"):
        inp["productType"] = (cfg.get("product_type") or p.get("product_type"))[:255]
    if cfg.get("source_metafield"):
        inp["metafields"] = [{"namespace": "profitdesk", "key": "source_url", "type": "url", "value": source_url}]
    return inp


PRODUCT_SET = """
mutation Set($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product { id handle title }
    userErrors { field message code }
  }
}"""

HANDLE_EXISTS = """
query Exists($q: String!) { products(first: 1, query: $q) { nodes { id handle } } }"""

COLLECTIONS = """
query Collections($q: String) { collections(first: 250, query: $q, sortKey: TITLE) { nodes { id title } } }"""

COLLECTION_CREATE = """
mutation C($input: CollectionInput!) {
  collectionCreate(input: $input) { collection { id title } userErrors { field message } }
}"""

COLLECTION_ADD = """
mutation A($id: ID!, $productIds: [ID!]!) {
  collectionAddProducts(id: $id, productIds: $productIds) { userErrors { field message } }
}"""

PUBLICATIONS = """
query { publications(first: 25) { nodes { id name } } }"""

PUBLISH = """
mutation P($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) { userErrors { field message } }
}"""


# ---------------------------------------------------------------- edit an imported product (in ProfitDesk)

PRODUCT_GET = """
query P($id: ID!) { product(id: $id) { id title handle status descriptionHtml productType vendor tags
  onlineStorePreviewUrl onlineStoreUrl options { name values }
  media(first: 50) { nodes { id mediaContentType alt preview { image { url } } } }
  variants(first: 100) { nodes { id title price compareAtPrice sku selectedOptions { name value } } } } }"""
PRODUCT_UPDATE = """
mutation U($product: ProductUpdateInput!, $media: [CreateMediaInput!]) {
  productUpdate(product: $product, media: $media) { product { id status } userErrors { field message } } }"""
VARIANTS_UPDATE = """
mutation V($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) { userErrors { field message } } }"""
MEDIA_REORDER = """
mutation R($id: ID!, $moves: [MoveInput!]!) {
  productReorderMedia(id: $id, moves: $moves) { job { id } mediaUserErrors { field message } } }"""
MEDIA_REMOVE = """
mutation F($files: [FileUpdateInput!]!) { fileUpdate(files: $files) { files { id } userErrors { field message } } }"""
STAGED_UPLOAD = """
mutation S($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) { stagedTargets { url resourceUrl parameters { name value } } userErrors { field message } } }"""


def errors(block: dict, key: str = "userErrors") -> str:
    errs = (block or {}).get(key) or []
    return "; ".join(e.get("message", "") for e in errs)


REWRITE_SYSTEM = """You write product copy for a fashion and lifestyle Shopify store.
Rewrite the product's title and description in {language}.

Rules:
- Use only facts found in the given title, description, options and product type. Never invent
  materials, certifications, measurements, origins, reviews or guarantees.
- Keep every size, measurement and care instruction that is in the original.
- Title: short and appealing, at most 70 characters. {title_rule}
- Description: clean HTML using only <p>, <h3>, <ul>, <li>, <strong> and <br>. No links, no images,
  no inline styles, no emojis. A short opening paragraph, then a bullet list of key features.
- Remove supplier language (factory, wholesale, "dropshipping", Chinese size notes, shop names).
- Tone: {tone}."""

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "description_html": {"type": "string"}},
    "required": ["title", "description_html"],
    "additionalProperties": False,
}

TONES = {"warm": "warm, elegant and reassuring", "short": "brief and punchy",
         "premium": "premium and refined", "playful": "light and playful"}


async def rewrite(api_key: str, title: str, description_html: str, extra: dict) -> dict:
    """AI rewrite of title + description. Nothing is saved; the editor shows it for review."""
    import json
    import anthropic
    text = re.sub(r"<[^>]+>", " ", description_html or "")
    text = re.sub(r"\s+", " ", _html.unescape(text)).strip()[:6000]
    keep = (extra.get("title_style") or "").strip()
    title_rule = (f"Follow this naming style: {keep}." if keep else
                  "If the original starts with a model name followed by ™, keep that name and the ™.")
    system = REWRITE_SYSTEM.format(language=extra.get("language") or "English", title_rule=title_rule,
                                   tone=TONES.get(extra.get("tone"), TONES["warm"]))
    notes = (extra.get("instructions") or "").strip()
    user = (f"Title: {title}\nProduct type: {extra.get('product_type') or ''}\n"
            f"Options: {extra.get('options') or ''}\nDescription: {text}"
            + (f"\n\nExtra instructions from the store owner: {notes}" if notes else ""))
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=90)
    try:
        resp = await client.messages.create(
            model="claude-sonnet-5-5", max_tokens=3000, system=system,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": REWRITE_SCHEMA}},
            messages=[{"role": "user", "content": user}])
    except anthropic.AuthenticationError as e:
        raise ValueError("Anthropic didn't accept the API key (Settings → AI).") from e
    except anthropic.APIStatusError as e:
        raise ValueError(f"The AI couldn't rewrite this right now ({e.status_code}).") from e
    except anthropic.APIConnectionError as e:
        raise ValueError("Couldn't reach the AI.") from e
    if resp.stop_reason == "refusal":
        raise ValueError("The AI declined to rewrite this product.")
    out = json.loads(next((b.text for b in resp.content if b.type == "text"), "{}"))
    return {"title": (out.get("title") or "").strip(), "description_html": out.get("description_html") or ""}
