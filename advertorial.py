"""Advertorial + ad creative tasks: a "letter from a reader" advertorial page in a store, plus one image ad with its
primary text, headline and description.

The AI (Claude or ChatGPT through EcomVital's connector) writes the words and picks / makes the pictures; EcomVital
turns its content into the page (the same design on every store, copied from Perth Boutique's "A Letter From a
Reader", 18 Oct 2026), creates it in Shopify as a hidden (unpublished) page and publishes it when an admin approves
the task. The Advertorial Playbook (setting advertorial_playbook, edited in Tasks → Store kit) is how it writes;
PLAYBOOK below is the first version, written from the owner's 8 examples (Manchester Boutique + Perth Boutique).
"""
import asyncio
import html
import ipaddress
import json
import re
import secrets
import socket
from urllib.parse import urlparse

import httpx

import db
from shopify_client import API_VERSION, ShopifyClient

TEMPLATE = "advetorial"              # Perth's page template (spelt like this in its theme): hides the shop header / footer
ACCENT = "#1d7f8c"

CSS = """
#shopify-section-header,#shopify-section-footer,#shopify-section-footer-promotions,#shopify-section-newsletter-popup,
#shopify-section-announcement-bar,.shopify-section-group-header-group,.shopify-section-group-footer-group,
.announcement-bar,.announcement-slider,.page-content .section-header,.main-page-title,.breadcrumb,.breadcrumbs{display:none !important;}
.page-width--narrow{max-width:none !important;}
.mb-lp{font-family:Georgia,'Times New Roman',serif;font-size:19px;line-height:1.75;color:#2d2a26;max-width:700px;margin:0 auto;padding:28px 20px 70px;}
.mb-lp p{margin:0 0 22px;}
.mb-lp .lp-label{font-family:Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#8a8178;margin:0 0 14px;}
.mb-lp h1{font-size:35px;line-height:1.24;margin:0 0 22px;font-weight:700;color:#221f1c;}
.mb-lp h2{font-size:26px;line-height:1.3;margin:44px 0 18px;font-weight:700;color:#221f1c;}
.mb-lp .lp-byline{display:flex;align-items:center;gap:14px;border-top:1px solid #e5ded4;border-bottom:1px solid #e5ded4;padding:14px 0;margin:0 0 34px;}
.mb-lp .lp-byline img{width:58px;height:58px;border-radius:50%;object-fit:cover;flex:none;}
.mb-lp .lp-byline span{font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#6b635a;line-height:1.45;}
.mb-lp figure{margin:30px 0;}
.mb-lp figure img{width:100%;height:auto;display:block;border-radius:4px;}
.mb-lp figcaption{font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#8a8178;margin-top:9px;font-style:italic;}
.mb-lp .lp-pull{font-size:23px;line-height:1.5;font-style:italic;color:ACCENT;border-left:3px solid ACCENT;padding-left:20px;margin:30px 0;}
.mb-lp .lp-cta{text-align:center;margin:38px 0;}
.mb-lp .lp-cta a{display:inline-block;background:ACCENT;color:#fff !important;font-family:Helvetica,Arial,sans-serif;font-size:18px;font-weight:700;text-decoration:none;padding:17px 34px;border-radius:40px;line-height:1.3;}
.mb-lp .lp-card{background:#faf6ef;border-radius:8px;padding:28px;margin:44px 0;text-align:center;}
.mb-lp .lp-card img{width:100%;max-width:340px;height:auto;border-radius:6px;margin:0 auto 18px;display:block;}
.mb-lp .lp-card h3{font-size:23px;margin:0 0 8px;}
.mb-lp .lp-stars{font-family:Helvetica,Arial,sans-serif;font-size:16px;color:#6b635a;margin:0 0 14px;}
.mb-lp .lp-price{font-size:27px;margin:0 0 18px;}
.mb-lp .lp-price s{color:#9b938a;font-size:20px;margin-right:9px;}
.mb-lp .lp-price b{color:ACCENT;}
.mb-lp .lp-card ul{text-align:left;max-width:420px;margin:0 auto 22px;padding-left:22px;font-size:17px;line-height:1.65;}
.mb-lp .lp-note{font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#8a8178;margin-top:14px;}
.mb-lp .lp-foot{border-top:1px solid #e5ded4;margin-top:44px;padding-top:22px;font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#6b635a;line-height:1.6;}
.mb-lp .lp-foot p{margin:0 0 10px;}
.mb-lp .lp-disc{font-size:12px;color:#a39a90;letter-spacing:.02em;}
@media(max-width:600px){.mb-lp{font-size:18px;padding:20px 17px 56px;}.mb-lp h1{font-size:28px;}.mb-lp h2{font-size:22px;}.mb-lp .lp-pull{font-size:20px;}.mb-lp .lp-cta a{font-size:17px;padding:16px 26px;}}
"""

PLAYBOOK = """ADVERTORIAL PLAYBOOK v1 (EcomVital, from the owner's 8 examples, 18-19 Oct 2026)

WHAT YOU DELIVER for an "Advertorial + ad creative" task
1. The advertorial page (sent to EcomVital with advertorial_save; EcomVital builds it in the store as a hidden page).
2. One image ad (square, 1:1).
3. Primary text, headline and description for that ad.

TWO WAYS TO WORK
- Competitor advertorial given (brief.competitor_advertorial; its text is in competitor_page): COPYCAT. Keep their story,
  structure, section order, angle and length; swap in our store, our product, our country's spelling and places,
  our prices and our real reviews. Change names, friends, towns and small details so it is not word for word.
- No competitor advertorial: write our own in exactly the style below, from the product facts.

THE VOICE (every example)
- A woman of about 65-70 writing plainly, warmly and a little drily, in the store's country (British spelling for UK,
  Australian for AU: "till", "high street"/"shops", "mum"). No marketing words, no exclamation marks, no emojis.
- Short sentences. Small concrete moments (the checkout queue, a 70th birthday lunch, walking the labrador, a coach
  trip, the caravan site). A friend or sister with a backhanded compliment ("Ann told me I looked like I had made an
  effort for once, which I have decided to take as a compliment").
- The product is named with one vivid physical detail (colour, stitching, print, the bit you touch).
- Store described as: "<Store>, a small <British/Australian> boutique that has been quietly dressing women our age for
  a few years now" (homeware/accessories: "quietly making the ordinary things a bit lovelier").
- Signed with a first name (Maggie, Pat, Patricia...).

STORY FRAMES (pick the one that suits the product, or the competitor's)
A. "I almost did not post this." (dresses, outfits, bags)
   I am 67 and not one for Facebook -> N women asked me about it at <event> -> it felt mean keeping it to myself ->
   store + product with one detail -> "I will tell you what I told them" -> the real benefits as life moments -> the
   compliment -> "That is the rarest sort of <thing>. The one you can <grand> or <everyday> and feel right either way."
   -> reviews -> why today (sale on, middle sizes / first stock going) -> "If you have ..., this is where I would start."
B. "I saw a woman with it / asked her plainly." (accessories, shoes, jewellery, homeware)
   I noticed it on someone (friend's landing, golden wedding, promenade, caravan site) -> assumed it was expensive/old
   -> asked her plainly -> store + product -> "I have wasted more money on <category> than I care to admit" -> ordered
   expecting to send it back -> wrote the boutique a letter, "the six things this one gets right" -> "They asked if they
   could put it up for other women to read" -> "The short version." six benefits in one paragraph -> what happened since
   -> reviews -> sale -> "If your <thing> is <problem>, my full letter is below, with the six things laid out plainly."

PRIMARY TEXT (the ad): 330-450 words, frame A or B, plain paragraphs, signed "— Name". Uses the real review rating,
count and one real quote from the brief. Ends pointing to the letter / the store.
HEADLINE: under 60 characters. Patterns: "The <product> four women asked me about", "The <product> she wore to the
<event>", "Six things this <product> gets right", "The one <product> she won't stop carrying".
DESCRIPTION: under 45 characters. Patterns: "What I told them at the table", "So I asked her where it was from",
"Read Pat's letter in full".

THE PAGE (advertorial_save -> page). 1,100-1,700 words, the long version of the same story:
- label: "A letter from a reader"
- headline: one long specific sentence with age + a moment, e.g. "At 67, my sister watched me tip my whole handbag out
  on the kitchen table, just to find my glasses".
- author: first name, "<City> · <Month Year>", photo = an older woman's portrait (use a file made for the task, or
  leave it out).
- blocks in this order (types: p, pull, h2, image, cta, list):
  1. 3-5 p: writing this at the kitchen table, why (people kept asking), the moment it started; one "pull" quote (what
     the friend said).
  2. image of the product as it arrived (caption in her voice).
  3. h2 "How it happens": why women end up with the wrong <thing> (the shops give you two choices...).
  4. h2 "What I had wrong": the real problem was never X, it was Y.  Then a cta.
  5. h2 "The <thing> is called the <Name>": store + product details from the product facts; image with caption.
  6. h2 "What happened at <event>": the outing, the compliments, the friend; one or two images with captions.
  7. h2 "Three things I have learned since": a "list" block, items {lead: "One. ...", text: "..."}.
  8. h2 "About the price": "The <Name> is <price>. It was <compare-at price>." (from the product facts), why no catch
     (small boutique selling directly, sale on to make room for the new season), the reviews (rating, count, one real
     quote, "Not the suspiciously perfect five you see on some websites, which made me trust it rather more"). Then a cta.
  9. h2 "What I would tell you if we were friends": who it is for, "Not because a <thing> will change your life...",
     two practical things (sale on, which colour/size goes first), a closing "pull" line, then her name as a p.
- card: product name (as on the store, e.g. "ALINA | Exclusive Design Bag"), its best image, 5-7 bullet benefits from
  the product facts (plus the store's delivery line), button text "Shop The <Collection/Product>", note "<Sale> on now.
  The <colour> is selling first."  EcomVital adds the stars line (from the brief's reviews) and the prices.
- footer: about = "<Full name> is <age> and lives in <city> with <a detail>. She is not a fashion writer.";
  store_line = "<Store> · <delivery line> · <returns line>". EcomVital adds "This page is an advertisement for <Store>."
- cta buttons: "See The <Collection>" (they all go to the brief's product / collection link).
- Frame B page (the "six things" letter): label + long headline ("Fifty years of handbags and I never once wrote to a
  shop about one. This is the exception"), a one-line standfirst p ("Six things the <Name> gets right that everyday
  <things> get wrong, from a woman who does not review <things>"), "Let me be plain about who is talking" intro, where she
  first saw it, then six h2 sections ("One. ..." to "Six. ...") each with 2-3 p and an image, then price, reviews, close.

REVIEWS: use only the rating, count and quotes the staff put in the brief (review_rating, review_count, reviews).
If none were given, leave reviews out of the copy and say so in task notes.
PRICES: from the product facts (price, compare-at price), in the store's currency.

IMAGES
- Ad image: square phone-camera photo, no text, no logo, natural light, a little lived-in. Two scenes that work:
  (a) "just arrived": the product laid on a made bed (beige / cream linen), torn grey mailer bag and tissue paper beside
  it, a brown swing tag still on; (b) "out and about": the product on a weathered park bench with autumn leaves, a
  bistro table on a cobbled street with a cup of tea, a sunny windowsill. The product must look exactly like the real
  one (use the product photo as the reference: same colour, shape, print, hardware).
- Page images (3-4): the same product in home scenes matching the story (bed the morning it arrived, side table,
  armchair by the door, kitchen table), plus an older woman's portrait for the byline.
- Making them: ChatGPT makes them with its image tool from the product photo. Then add each one to the task with
  task_add_image (image_url), or ask the person to download it and drop it on the task; each file gets an id you use as
  "file:<id>" in the page. Product photos from the product facts can be used as they are (their https link).

FINISH: advertorial_save with page + primary_text + headline + description (+ submit=true when the ad image is on the
task). Then tell the person the preview link so they can check it before the admin approves.
"""


# ---------------------------------------------------------------- page HTML from the AI's content

def _inline(text: str) -> str:
    """Plain text -> safe HTML, with **bold** kept."""
    t = html.escape(str(text or "").strip())
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)


def _img(src: str, alt: str = "") -> str:
    return f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt or "", quote=True)}">'


def money(v, currency: str) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return ""
    sym = {"AUD": "$", "USD": "$", "CAD": "$", "NZD": "$", "GBP": "£", "EUR": "€"}.get((currency or "").upper(), "")
    return f"{sym}{n:,.2f}" if sym else f"{n:,.2f} {currency}"


def stars(rating) -> str:
    try:
        r = float(rating)
    except (TypeError, ValueError):
        return ""
    full = int(round(r))
    return "★" * full + "☆" * (5 - full)


def render(page: dict, *, store: str, link: str, images: dict, price: str = "", was: str = "",
           rating: str = "", count: str = "", accent: str = ACCENT) -> str:
    """The page body for Shopify (style + content). images maps each image reference to its final https link."""
    def src(ref):
        ref = str(ref or "").strip()
        return images.get(ref) or (ref if ref.startswith("https://") else "")

    out = [f'<p class="lp-label">{_inline(page.get("label") or "A letter from a reader")}</p>',
           f'<h1>{_inline(page.get("headline"))}</h1>']
    a = page.get("author") or {}
    if a.get("name"):
        photo = src(a.get("photo"))
        out.append(f'<div class="lp-byline">{_img(photo, a["name"]) if photo else ""}'
                   f'<span>{_inline(a["name"])}<br>{_inline(a.get("line"))}</span></div>')
    cta = lambda text: (f'<div class="lp-cta"><a href="{html.escape(link, quote=True)}">'
                        f'{_inline(text or "See The Collection")}</a></div>')
    for b in page.get("blocks") or []:
        kind = b.get("type")
        if kind == "p":
            out.append(f"<p>{_inline(b.get('text'))}</p>")
        elif kind == "pull":
            out.append(f'<p class="lp-pull">{_inline(b.get("text"))}</p>')
        elif kind == "h2":
            out.append(f"<h2>{_inline(b.get('text'))}</h2>")
        elif kind == "image":
            s = src(b.get("src"))
            if s:
                cap = f"<figcaption>{_inline(b['caption'])}</figcaption>" if b.get("caption") else ""
                out.append(f"<figure>{_img(s, b.get('alt') or b.get('caption') or '')}{cap}</figure>")
        elif kind == "cta":
            out.append(cta(b.get("text")))
        elif kind == "list":
            for it in b.get("items") or []:
                lead = f"<strong>{_inline(it.get('lead'))}</strong> " if it.get("lead") else ""
                out.append(f"<p>{lead}{_inline(it.get('text'))}</p>")
    c = page.get("card") or {}
    if c.get("title"):
        s = src(c.get("image"))
        line = f"{stars(rating)} {rating} out of 5 from {count} reviews" if rating and count else ""
        out.append('<div class="lp-card">' + (_img(s, c["title"]) if s else "") + f"<h3>{_inline(c['title'])}</h3>"
                   + (f'<p class="lp-stars">{html.escape(line)}</p>' if line else "")
                   + (f'<p class="lp-price">{f"<s>{html.escape(was)}</s> " if was else ""}<b>{html.escape(price)}</b></p>' if price else "")
                   + ("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in c.get("bullets") or []) + "</ul>" if c.get("bullets") else "")
                   + cta(c.get("button") or "Shop Now").replace('class="lp-cta"', 'class="lp-cta" style="margin:0;"')
                   + (f'<p class="lp-note">{_inline(c["note"])}</p>' if c.get("note") else "") + "</div>")
    f = page.get("footer") or {}
    out.append('<div class="lp-foot">' + (f"<p>{_inline(f['about'])}</p>" if f.get("about") else "")
               + (f"<p>{_inline(f['store_line'])}</p>" if f.get("store_line") else "")
               + f'<p class="lp-disc">This page is an advertisement for {html.escape(store)}.</p></div>')
    return f"<style>{CSS.replace('ACCENT', accent)}</style>\n<div class=\"mb-lp\">\n" + "\n".join(out) + "\n</div>"


def image_refs(page: dict) -> list:
    """Every image the page uses (https links and file:<id> references)."""
    refs = [(page.get("author") or {}).get("photo"), (page.get("card") or {}).get("image")]
    refs += [b.get("src") for b in page.get("blocks") or [] if b.get("type") == "image"]
    return [str(r).strip() for r in refs if r]


def check(page: dict) -> list:
    """What's missing for a page worth showing."""
    out = []
    if not str(page.get("headline") or "").strip():
        out.append("headline")
    blocks = page.get("blocks") or []
    if sum(1 for b in blocks if b.get("type") == "p") < 8:
        out.append("the story (at least 8 paragraphs)")
    if not any(b.get("type") == "cta" for b in blocks):
        out.append("a button (cta block)")
    return out


def handle_of(title: str) -> str:
    h = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:70]
    return h or "a-letter-from-a-reader"


# ---------------------------------------------------------------- Shopify: product facts, the page, images

FACTS_Q = """
query F($q: String!, $c: String!) {
  shop { name currencyCode myshopifyDomain primaryDomain { url } billingAddress { country countryCodeV2 } }
  products(first: 1, query: $q) { nodes { ...P } }
  collections(first: 1, query: $c) { nodes { id title handle description
    products(first: 12) { nodes { ...P } } } }
}
fragment P on Product { id title handle productType vendor description onlineStoreUrl
  options { name optionValues { name } }
  priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount } }
  compareAtPriceRange { maxVariantCompareAtPrice { amount } }
  media(first: 6) { nodes { ... on MediaImage { image { url } } } } }"""


def _product(p: dict, cur: str) -> dict:
    lo = (p.get("priceRangeV2") or {}).get("minVariantPrice") or {}
    cmp_ = ((p.get("compareAtPriceRange") or {}).get("maxVariantCompareAtPrice") or {}).get("amount")
    return {"title": p["title"], "handle": p["handle"], "type": p.get("productType"), "vendor": p.get("vendor"),
            "description": (p.get("description") or "")[:2500],
            "options": {o["name"]: [v["name"] for v in o.get("optionValues") or []][:30]
                        for o in p.get("options") or [] if o["name"] != "Title"},
            "price": money(lo.get("amount"), cur), "compare_at_price": money(cmp_, cur) if cmp_ and float(cmp_) > 0 else "",
            "price_number": lo.get("amount"),
            "images": [(m.get("image") or {}).get("url") for m in (p.get("media") or {}).get("nodes") or [] if (m.get("image") or {}).get("url")]}


def link_handle(url: str):
    """('product' | 'collection', handle) from a store link."""
    path = urlparse((url or "").strip()).path
    m = re.search(r"/products/([^/?#]+)", path)
    if m:
        return "product", m.group(1)
    m = re.search(r"/collections/([^/?#]+)", path)
    if m:
        return "collection", m.group(1)
    return None, None


async def facts(store: dict, url: str) -> dict:
    """The real product / collection behind the brief's link: names, options, prices, compare-at, photos."""
    kind, handle = link_handle(url)
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    q = f"handle:{handle}" if kind == "product" else "handle:__none__"
    c = f"handle:{handle}" if kind == "collection" else "handle:__none__"
    data = await client.gql(FACTS_Q, {"q": q, "c": c})
    shop = data["shop"]
    cur = shop["currencyCode"]
    out = {"store": {"name": shop["name"], "currency": cur, "domain": (shop.get("primaryDomain") or {}).get("url"),
                     "country": (shop.get("billingAddress") or {}).get("country")},
           "link": url, "link_is": kind}
    prods = data["products"]["nodes"]
    cols = data["collections"]["nodes"]
    if kind == "product" and prods:
        out["product"] = _product(prods[0], cur)
    elif kind == "collection" and cols:
        col = cols[0]
        out["collection"] = {"title": col["title"], "handle": col["handle"], "description": (col.get("description") or "")[:1500],
                             "products": [_product(p, cur) for p in col["products"]["nodes"]]}
    else:
        out["error"] = "That link isn't a product or collection of this store (or it's not found)."
    return out


PAGE_CREATE = """
mutation C($page: PageCreateInput!) { pageCreate(page: $page) { page { id handle } userErrors { field message } } }"""
PAGE_UPDATE = """
mutation U($id: ID!, $page: PageUpdateInput!) { pageUpdate(id: $id, page: $page) { page { id handle isPublished } userErrors { field message } } }"""
THEME_Q = """
query { themes(first: 1, roles: [MAIN]) { nodes { files(filenames: ["templates/page.advetorial.json", "templates/page.advetorial.liquid"]) {
  nodes { filename } } } } }"""
STAGED = """
mutation S($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) { stagedTargets { url resourceUrl parameters { name value } } userErrors { field message } } }"""
FILE_CREATE = """
mutation F($files: [FileCreateInput!]!) { fileCreate(files: $files) { files { id fileStatus ... on MediaImage { image { url } } }
  userErrors { field message } } }"""
FILE_Q = """query N($id: ID!) { node(id: $id) { ... on MediaImage { fileStatus image { url } } } }"""


def _errs(block: dict) -> str:
    return "; ".join(e.get("message", "") for e in (block or {}).get("userErrors") or [])


async def has_template(client: ShopifyClient) -> bool:
    try:
        d = await client.gql(THEME_Q)
        nodes = d["themes"]["nodes"]
        return bool(nodes and nodes[0]["files"]["nodes"])
    except Exception:
        return False


async def save_page(store: dict, page_id: str, title: str, handle: str, body: str) -> dict:
    """Create (hidden) or update the advertorial page. Returns {id, handle}."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    data = {"title": title, "body": body}
    if await has_template(client):
        data["templateSuffix"] = TEMPLATE
    if page_id:
        d = await client.gql(PAGE_UPDATE, {"id": page_id, "page": data})
        if _errs(d["pageUpdate"]) or not d["pageUpdate"]["page"]:
            if "not exist" not in _errs(d["pageUpdate"]).lower():
                raise ValueError(f"Shopify refused the page: {_errs(d['pageUpdate'])}")
        else:
            return d["pageUpdate"]["page"]
    d = await client.gql(PAGE_CREATE, {"page": {**data, "handle": handle, "isPublished": False}})
    if _errs(d["pageCreate"]):
        if "handle" in _errs(d["pageCreate"]).lower():             # taken: add a number
            d = await client.gql(PAGE_CREATE, {"page": {**data, "handle": f"{handle}-{secrets.token_hex(2)}",
                                                         "isPublished": False}})
        if _errs(d["pageCreate"]):
            raise ValueError(f"Shopify refused the page: {_errs(d['pageCreate'])}")
    return d["pageCreate"]["page"]


def publish_sync(store: dict, page_id: str) -> None:
    """Make the page live (on approve). Synchronous: the status route isn't async."""
    r = httpx.post(f"https://{store['shop_domain']}/admin/api/{API_VERSION}/graphql.json", timeout=30,
                   headers={"X-Shopify-Access-Token": store["access_token"], "Content-Type": "application/json"},
                   json={"query": PAGE_UPDATE, "variables": {"id": page_id, "page": {"isPublished": True}}})
    if r.status_code >= 400:
        raise ValueError(f"Shopify answered {r.status_code}")
    body = r.json()
    if body.get("errors"):
        raise ValueError(str(body["errors"][0].get("message")))
    e = _errs(body["data"]["pageUpdate"])
    if e:
        raise ValueError(e)


async def upload_image(store: dict, data: bytes, name: str, mime: str) -> str:
    """One image into the store's Files; returns its https link (Shopify's CDN)."""
    client = ShopifyClient(store["shop_domain"], store["access_token"])
    t = await client.gql(STAGED, {"input": [{"filename": name, "mimeType": mime, "resource": "FILE", "httpMethod": "POST",
                                             "fileSize": str(len(data))}]})
    if _errs(t["stagedUploadsCreate"]):
        raise ValueError(_errs(t["stagedUploadsCreate"]))
    target = t["stagedUploadsCreate"]["stagedTargets"][0]
    async with httpx.AsyncClient(timeout=120) as http:
        up = await http.post(target["url"], data={x["name"]: x["value"] for x in target["parameters"]},
                             files={"file": (name, data, mime)})
    if up.status_code >= 300:
        raise ValueError(f"Shopify didn't take {name} ({up.status_code}).")
    f = await client.gql(FILE_CREATE, {"files": [{"originalSource": target["resourceUrl"], "contentType": "IMAGE", "alt": name}]})
    if _errs(f["fileCreate"]):
        raise ValueError(_errs(f["fileCreate"]))
    fid = f["fileCreate"]["files"][0]["id"]
    for _ in range(20):                                  # Shopify processes it for a few seconds
        n = (await client.gql(FILE_Q, {"id": fid}))["node"] or {}
        if (n.get("image") or {}).get("url"):
            return n["image"]["url"]
        if n.get("fileStatus") == "FAILED":
            raise ValueError(f"Shopify couldn't process {name}.")
        await asyncio.sleep(1.5)
    raise ValueError(f"Shopify is still processing {name}; save again in a minute.")


# ---------------------------------------------------------------- reading a web page / image the AI points at

def _public(url: str) -> bool:
    u = urlparse(url or "")
    if u.scheme != "https" or not u.hostname:
        return False
    try:
        for info in socket.getaddrinfo(u.hostname, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


async def read_page(url: str) -> dict:
    """A competitor advertorial as text: headline, sections in order (headings, paragraphs, image captions)."""
    if not _public(url):
        return {"error": "Not a public https link."}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (EcomVital)"}) as c:
        r = await c.get(url)
    if r.status_code >= 400:
        return {"error": f"The page answered {r.status_code}."}
    doc = r.text
    m = re.search(r"<main[\s\S]*?</main>", doc, re.I)
    doc = m.group(0) if m else doc
    doc = re.sub(r"<(script|style|noscript|svg)[\s\S]*?</\1>", " ", doc, flags=re.I)
    parts = []
    for tag, attrs, inner in re.findall(r"<(h1|h2|h3|p|li|figcaption|blockquote|img)\b([^>]*)>(?:([\s\S]*?)</\1>)?", doc, re.I):
        tag = tag.lower()
        if tag == "img":
            alt = re.search(r'alt="([^"]*)"', attrs or "")
            parts.append(f"[image: {html.unescape(alt.group(1)) if alt else ''}]")
            continue
        text = html.unescape(re.sub(r"<[^>]+>", " ", inner or "")).strip()
        text = re.sub(r"\s+", " ", text)
        if text and not (parts and parts[-1].endswith(text)):
            parts.append({"h1": "# ", "h2": "## ", "h3": "### ", "li": "- ", "figcaption": "(caption) ",
                          "blockquote": "> "}.get(tag, "") + text)
    text = "\n".join(parts)
    return {"url": url, "text": text[:30000], "words": len(text.split())}


async def fetch_image(url: str) -> tuple:
    """(bytes, mime) of an image the AI made / found, at most 15 MB."""
    if not _public(url):
        raise ValueError("Give a public https link to the image.")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
    mime = (r.headers.get("content-type") or "").split(";")[0].strip()
    if r.status_code >= 400 or not mime.startswith("image/"):
        raise ValueError(f"That link didn't give an image ({r.status_code}, {mime or 'no type'}).")
    if len(r.content) > 15 * 1024 * 1024:
        raise ValueError("The image is bigger than 15 MB.")
    return r.content, mime


def playbook() -> str:
    return db.get_setting("advertorial_playbook") or PLAYBOOK
