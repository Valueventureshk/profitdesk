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
import os
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
@import url('https://fonts.googleapis.com/css2?family=Caveat:wght@500;700&display=swap');
#shopify-section-header,#shopify-section-footer,#shopify-section-footer-promotions,#shopify-section-newsletter-popup,
#shopify-section-announcement-bar,.shopify-section-group-header-group,.shopify-section-group-footer-group,
.announcement-bar,.announcement-slider,.page-content .section-header,.main-page-title,.breadcrumb,.breadcrumbs{display:none !important;}
.page-width--narrow{max-width:none !important;}
.mb-lp{--ac:ACCENT;font-family:Georgia,'Times New Roman',serif;font-size:19px;line-height:1.75;color:#2d2a26;max-width:700px;margin:0 auto;padding:28px 20px 70px;}
.mb-lp *{box-sizing:border-box;}
.mb-lp p{margin:0 0 22px;}
.mb-lp mark{background:linear-gradient(transparent 55%,#f7e08a 55%);color:inherit;padding:0 2px;}
.mb-lp .lp-label{font-family:Helvetica,Arial,sans-serif;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#8a8178;margin:0 0 14px;}
.mb-lp h1{font-family:Georgia,'Times New Roman',serif;font-size:35px;line-height:1.24;margin:0 0 22px;font-weight:700;color:#221f1c;}
.mb-lp h2{font-family:Georgia,'Times New Roman',serif;font-size:26px;line-height:1.3;margin:44px 0 18px;font-weight:700;color:#221f1c;}
.mb-lp .lp-stand{font-size:21px;line-height:1.55;color:#4a443d;font-style:italic;}
.mb-lp .lp-byline{display:flex;align-items:center;gap:14px;border-top:1px solid #e5ded4;border-bottom:1px solid #e5ded4;padding:14px 0;margin:0 0 34px;}
.mb-lp .lp-byline img{width:58px;height:58px;border-radius:50%;object-fit:cover;flex:none;}
.mb-lp .lp-byline span{font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#6b635a;line-height:1.45;}
.mb-lp figure{margin:30px 0;}
.mb-lp figure img{width:100%;height:auto;display:block;border-radius:4px;}
.mb-lp figcaption{font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#8a8178;margin-top:9px;font-style:italic;}
.mb-lp figure.lp-framed{border:1px solid #e0d8cc;padding:10px;background:#fff;box-shadow:0 2px 0 #efe8dc;}
.mb-lp figure.lp-polaroid{background:#fff;padding:12px 12px 16px;box-shadow:0 6px 18px rgba(40,30,20,.14);transform:rotate(-1.2deg);max-width:560px;margin:34px auto;position:relative;}
.mb-lp figure.lp-polaroid:nth-of-type(even){transform:rotate(1.3deg);}
.mb-lp figure.lp-polaroid img{border-radius:0;}
.mb-lp figure.lp-polaroid figcaption{font-family:'Caveat',cursive;font-style:normal;font-size:22px;color:#4a443d;text-align:center;margin-top:10px;}
.mb-lp figure.lp-polaroid:before{content:"";position:absolute;top:-12px;left:50%;width:110px;height:26px;margin-left:-55px;background:rgba(240,226,180,.75);transform:rotate(-3deg);}
.mb-lp .lp-pull{font-size:23px;line-height:1.5;font-style:italic;color:var(--ac);border-left:3px solid var(--ac);padding-left:20px;margin:30px 0;}
.mb-lp .lp-cta{text-align:center;margin:38px 0;}
.mb-lp .lp-cta a{display:inline-block;background:var(--ac);color:#fff !important;font-family:Helvetica,Arial,sans-serif;font-size:18px;font-weight:700;text-decoration:none;padding:17px 34px;border-radius:40px;line-height:1.3;box-shadow:0 3px 0 rgba(0,0,0,.12);}
.mb-lp .lp-box{border:2px solid var(--ac);border-radius:10px;padding:22px 24px 6px;margin:34px 0;background:#fffdf8;position:relative;}
.mb-lp .lp-box h4{font-family:Helvetica,Arial,sans-serif;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--ac);margin:0 0 10px;}
.mb-lp .lp-review{background:#faf6ef;border-radius:10px;padding:22px 24px;margin:30px 0;border:1px solid #ece3d4;}
.mb-lp .lp-review .lp-rs{color:#e2a522;font-size:18px;letter-spacing:2px;margin:0 0 6px;font-family:Helvetica,Arial,sans-serif;}
.mb-lp .lp-review p{margin:0 0 10px;font-style:italic;}
.mb-lp .lp-review .lp-who{font-family:Helvetica,Arial,sans-serif;font-style:normal;font-size:14px;color:#8a8178;margin:0;}
.mb-lp .lp-review .lp-who b{color:#4a443d;}
.mb-lp .lp-review .lp-ok{color:#2e7d4f;font-weight:700;}
.mb-lp .lp-step{display:flex;align-items:center;gap:14px;margin:44px 0 16px;}
.mb-lp .lp-step span{flex:none;width:42px;height:42px;border-radius:50%;background:var(--ac);color:#fff;font-family:Helvetica,Arial,sans-serif;font-weight:700;font-size:19px;display:flex;align-items:center;justify-content:center;}
.mb-lp .lp-step h2{margin:0;}
.mb-lp .lp-div{text-align:center;color:#c9bfb1;letter-spacing:.6em;margin:34px 0;font-size:18px;}
.mb-lp ul.lp-check{list-style:none;padding:0;margin:0 0 24px;}
.mb-lp ul.lp-check li{padding-left:34px;position:relative;margin:0 0 10px;}
.mb-lp ul.lp-check li:before{content:"✓";position:absolute;left:0;top:1px;width:24px;height:24px;border-radius:50%;background:var(--ac);color:#fff;font-size:14px;line-height:24px;text-align:center;font-family:Helvetica,Arial,sans-serif;}
.mb-lp .lp-note{background:#fff7c2;padding:16px 20px;margin:30px 8px;font-family:'Caveat',cursive;font-size:25px;line-height:1.3;color:#3d3a32;transform:rotate(-1deg);box-shadow:0 4px 10px rgba(40,30,20,.12);}
.mb-lp .lp-sign{font-family:'Caveat',cursive;font-size:36px;color:#2d2a26;margin:6px 0 30px;}
.mb-lp .lp-card{background:#faf6ef;border-radius:12px;padding:30px 28px;margin:44px 0;text-align:center;border:2px solid #efe5d3;position:relative;}
.mb-lp .lp-badge{position:absolute;top:-14px;left:50%;transform:translateX(-50%);background:#b3402a;color:#fff;font-family:Helvetica,Arial,sans-serif;font-size:13px;font-weight:700;letter-spacing:.08em;padding:6px 14px;border-radius:20px;white-space:nowrap;}
.mb-lp .lp-card img{width:100%;max-width:340px;height:auto;border-radius:6px;margin:0 auto 18px;display:block;}
.mb-lp .lp-card h3{font-family:Georgia,'Times New Roman',serif;font-size:23px;margin:0 0 8px;}
.mb-lp .lp-stars{font-family:Helvetica,Arial,sans-serif;font-size:16px;color:#6b635a;margin:0 0 14px;}
.mb-lp .lp-price{font-size:27px;margin:0 0 18px;}
.mb-lp .lp-price s{color:#9b938a;font-size:20px;margin-right:9px;}
.mb-lp .lp-price b{color:var(--ac);}
.mb-lp .lp-card ul{text-align:left;max-width:420px;margin:0 auto 22px;padding-left:22px;font-size:17px;line-height:1.65;}
.mb-lp .lp-cnote{font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#8a8178;margin-top:14px;}
.mb-lp .lp-foot{border-top:1px solid #e5ded4;margin-top:44px;padding-top:22px;font-family:Helvetica,Arial,sans-serif;font-size:15px;color:#6b635a;line-height:1.6;}
.mb-lp .lp-foot p{margin:0 0 10px;}
.mb-lp .lp-disc{font-size:12px;color:#a39a90;letter-spacing:.02em;}
/* journal: a letter on cream writing paper */
.mb-lp.th-journal{background:#fdfaf2;border:1px solid #e8dfcc;box-shadow:0 10px 30px rgba(60,45,20,.08);border-radius:4px;margin:24px auto;padding:44px 40px 60px;
  outline:1px solid #eadfc8;outline-offset:-12px;}
.mb-lp.th-journal .lp-label:before{content:"✉  ";color:var(--ac);}
.mb-lp.th-journal .lp-byline{border-color:#e3d7c0;}
.mb-lp.th-journal h2{border-bottom:2px dotted #d9ccb3;padding-bottom:8px;}
/* editorial: a magazine feature */
.mb-lp.th-editorial{border-top:6px double #221f1c;padding-top:22px;}
.mb-lp.th-editorial .lp-label{color:#b3402a;font-weight:700;border-bottom:1px solid #221f1c;padding-bottom:10px;}
.mb-lp.th-editorial h1{font-size:40px;letter-spacing:-.01em;}
.mb-lp.th-editorial .lp-first:first-letter{float:left;font-size:74px;line-height:.82;padding:6px 10px 0 0;color:var(--ac);font-weight:700;}
.mb-lp.th-editorial h2{border-top:1px solid #221f1c;padding-top:16px;text-transform:none;}
.mb-lp.th-editorial .lp-step h2{border-top:0;padding-top:0;}
.mb-lp.th-editorial .lp-step{border-top:1px solid #221f1c;padding-top:16px;}
.mb-lp.th-editorial .lp-pull{border:0;border-top:2px solid var(--ac);border-bottom:2px solid var(--ac);padding:18px 6px;text-align:center;font-size:25px;}
.mb-lp.th-editorial .lp-pull:before{content:"“";display:block;font-size:60px;line-height:.6;color:var(--ac);}
.mb-lp.th-editorial figure img{border-radius:0;}
.mb-lp.th-editorial figcaption{font-style:normal;text-transform:uppercase;letter-spacing:.08em;font-size:12px;}
/* scrapbook: photos taped in, notes in the margin */
.mb-lp.th-scrapbook h2{display:inline-block;background:linear-gradient(transparent 60%,#f6dca0 60%);padding:0 4px;}
.mb-lp.th-scrapbook .lp-box{border-style:dashed;border-radius:4px;background:#fffef9;}
.mb-lp.th-scrapbook .lp-review{border:2px dashed #e0c98f;background:#fffdf3;}
.mb-lp.th-scrapbook .lp-pull{font-family:'Caveat',cursive;font-style:normal;font-size:31px;border-left:0;padding-left:0;text-align:center;}
@media(max-width:600px){.mb-lp{font-size:18px;padding:20px 17px 56px;}.mb-lp h1{font-size:28px;}.mb-lp.th-editorial h1{font-size:31px;}
  .mb-lp h2{font-size:22px;}.mb-lp .lp-pull{font-size:20px;}.mb-lp .lp-cta a{font-size:17px;padding:16px 26px;}
  .mb-lp.th-journal{padding:30px 20px 46px;margin:10px 6px;}.mb-lp .lp-note{font-size:22px;margin:26px 2px;}
  .mb-lp.th-editorial .lp-first:first-letter{font-size:60px;}}
"""
STICKY_CSS = """
.mb-lp .lp-sticky{display:none;}
@media(max-width:749px){.mb-lp{padding-bottom:96px !important;}
  .mb-lp .lp-sticky{display:block;position:fixed;left:12px;right:12px;bottom:max(12px,env(safe-area-inset-bottom));z-index:2147483000;
    text-align:center;background:ACCENT;color:#fff !important;text-decoration:none !important;font:700 17px/1.2 Helvetica,Arial,sans-serif;
    letter-spacing:.02em;padding:16px 18px;border-radius:12px;box-shadow:0 6px 22px rgba(0,0,0,.28);}}
"""
THEMES = ("letter", "journal", "editorial", "scrapbook")
ACCENTS = {"teal": "#1d7f8c", "terracotta": "#b5532f", "forest": "#2f6b4f", "navy": "#2b4a7a", "plum": "#7a3e65",
           "caramel": "#a8642a"}

# The owner's house sample primary text (T-8, 20 Oct 2026): every primary text follows its beats and length.
HOUSE_SAMPLE = """I almost did not post this.

I am 67 and I am not one for putting things on Facebook. But three different women asked me about this bag during one Thursday morning shop, two of them stood behind me in the checkout queue, and it started to feel a bit mean keeping it to myself.

It is from Perth Boutique, a small Australian boutique that has been quietly dressing women our age for a few years now. They have a sale on at the moment. The bag is called the Alina. A caramel-coloured crossbody with a gently curved top, neat squared stitching across the front, and a little front pocket that sits just where your hand falls.

I will tell you what I told them in the queue.

It does the one thing I had stopped believing a handbag could do. It looks like a proper, put-together bag, and it behaves like an organised one. My phone lives in the front pocket, my purse has its own place inside, and my keys go where my keys always go, so at the till I am not the woman holding everybody up while she digs. It sits crossbody so my hands are free, and it is light enough that I forget it is there. It does not look like a bag made for somebody half my age, and it does not look like I have given up either.

I had my purse out at the till before the lady in front had finished packing, and Pauline, who I used to work with and who has never once commented on anything I carry, said it looked far too smart for a Thursday shop. I have decided to take that as a compliment. I had it on again the next morning to take the labrador across the field, and it stayed put and out of the way the whole time.

That is the rarest sort of bag. The one you can take to a lunch, or sling on to walk the dog first thing, and feel right either way.

I did go and read the reviews before I ordered. Just over three hundred of them, sitting at 4.2 stars. Not the suspiciously perfect five you see on some websites, which if I am honest made me trust it rather more. The same things kept coming up. One woman from Sydney had written, "I have stopped losing my keys. My phone finally has its own pocket and the colour goes with everything I own." That was the one that decided it for me.

The reason I am posting today rather than next week is that the sale is on now and the first autumn stock is already going. They do it in the caramel and in a black, and it is the caramel that seems to be going first.

If your own bag has spent years swallowing your glasses and your keys the moment you actually need them, this is where I would start.

— Maggie"""
SAMPLE_WORDS = 500                  # the sample's length; a primary text may be 475-525 words
SAMPLE_PARAS = 11
WRITER_NAME, WRITER_AGE = "Maggie Whitefield", "67"
# page styles staff can pick on the task (the AI picks when left on auto)
STYLES = {"letter": "Classic letter (clean, like Perth's first page)", "editorial": "Magazine feature (drop cap, boxed quotes)",
          "journal": "Cream writing paper (framed, warm)", "scrapbook": "Scrapbook (taped photos, sticky notes)"}

# The style library (setting advertorial_styles): page designs made from real pages (ours or competitors'), each a
# base theme + its own CSS (scoped to .mb-lp.st-<id>) + a guide telling the AI how pages in that style are built.
# Only the look is kept: every word is a placeholder and images are grey boxes until the AI writes the real page.
def custom_styles() -> list:
    try:
        got = json.loads(db.get_setting("advertorial_styles") or "[]")
    except ValueError:
        got = []
    return [s for s in got if isinstance(s, dict) and s.get("id")]


def all_styles() -> dict:
    """id -> name: the built-in themes, then the library's styles."""
    return {**STYLES, **{s["id"]: s.get("name") or s["id"] for s in custom_styles()}}


def style_of(key) -> dict | None:
    """The library style with this id (with its CSS), or None for a built-in / unknown one."""
    return next((s for s in custom_styles() if s["id"] == key), None)


def known_style(key) -> bool:
    return bool(key) and key in all_styles()


_PH = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='800' height='{h}'>"
       "<rect width='100%' height='100%' fill='%23d9d6cf'/><text x='50%' y='50%' font-family='Arial' font-size='34' "
       "fill='%23827d72' text-anchor='middle' dominant-baseline='middle'>{t}</text></svg>")
PLACEHOLDER_PAGE = {
    "label": "Label line (e.g. A letter from a reader)", "headline": "Headline goes here: the hook that makes her read on",
    "standfirst": "Standfirst: one or two sentences under the headline that set up the story.",
    "author": {"name": "Writer name", "line": "Writer, age · town", "photo": "ph:author"},
    "blocks": [
        {"type": "p", "text": "Opening paragraph. The writer's own voice: who she is and why she is writing this."},
        {"type": "p", "text": "Second paragraph. The problem she lived with, in everyday detail."},
        {"type": "image", "src": "ph:photo1", "caption": "Photo caption", "style": "framed"},
        {"type": "pull", "text": "A pull quote: the line people remember."},
        {"type": "h2", "text": "Sub-heading: the turning point"},
        {"type": "p", "text": "Paragraph about finding the product and trying it."},
        {"type": "list", "items": [{"lead": "Point one.", "text": "Why it matters."}, {"lead": "Point two.", "text": "Why it matters."}]},
        {"type": "box", "title": "Box title", "text": "A boxed note or summary.", "items": ["Tick one", "Tick two", "Tick three"]},
        {"type": "review", "stars": 5, "text": "A real customer review goes here.", "name": "Name", "place": "Town", "verified": True},
        {"type": "p", "text": "Closing paragraph: where to get it and why now."},
        {"type": "cta", "text": "Button text"},
        {"type": "signature", "name": "Writer's sign-off"}],
    "card": {"title": "Product name", "image": "ph:product", "bullets": ["Benefit one", "Benefit two", "Benefit three"],
             "button": "Shop button text", "note": "Small note under the button"},
    "footer": {"about": "About line", "store_line": "Store line"}}


def preview(key: str, store: str = "Your store") -> str:
    """A style with placeholder text and grey image boxes, as staff see it before picking."""
    s = style_of(key)
    page = dict((s or {}).get("sample") or PLACEHOLDER_PAGE)
    if not s:
        page["theme"] = key if key in THEMES else "letter"
    imgs = {"ph:author": _PH.format(h=800, t="Writer photo"), "ph:photo1": _PH.format(h=560, t="Photo"),
            "ph:photo2": _PH.format(h=560, t="Photo"), "ph:product": _PH.format(h=800, t="Product photo")}
    for b in page.get("blocks") or []:
        if b.get("type") == "image" and str(b.get("src", "")).startswith("ph:") and b["src"] not in imgs:
            imgs[b["src"]] = _PH.format(h=560, t="Photo")
    return render(page, store=store, link="#", images=imgs, price="$59.99", was="$119.99", rating="4.7", count="312",
                  style=s)


# never on the page or in the ad (owner, T-8): it is presented as a reader's real letter
FORBIDDEN = re.compile(r"\b(fictional|fiction|fictitious|dramati[sz]ed|dramati[sz]ation|made[- ]up|invented|"
                       r"(?:is|as) a character|composite|ai[- ]generated|ai[- ]made|ai image|generated (?:image|photo)|"
                       r"image generator|illustrative|for illustration|stock (?:photo|image)|rendered|mock-?up)\b", re.I)


def words(text: str) -> int:
    return len([w for w in str(text or "").split() if re.search(r"\w", w)])


def copy_issues(page: dict, primary_text: str, brief: dict) -> list:
    """What would send the work back (the owner's standing edits), checked before anything is saved."""
    out = []
    texts = [primary_text or ""] + [str(v) for v in _all_text(page)]
    hits = sorted({m.group(0).lower() for t in texts for m in FORBIDDEN.finditer(t)})
    if hits:
        out.append("remove these words, the page must read as a real reader's letter and never mention AI or that it "
                   "is made up: " + ", ".join(hits))
    if primary_text:
        n = words(primary_text)
        if not (SAMPLE_WORDS - 25 <= n <= SAMPLE_WORDS + 25):
            out.append(f"the primary text is {n} words; make it {SAMPLE_WORDS - 25}-{SAMPLE_WORDS + 25} (the house sample is "
                       f"{SAMPLE_WORDS}), keeping the sample's paragraph-by-paragraph beats")
        name = (brief.get("writer_name") or WRITER_NAME).split()[0]
        if name.lower() not in primary_text.lower():
            out.append(f"sign the primary text with the writer's name ({name})")
        age = str(brief.get("writer_age") or WRITER_AGE)
        if age not in primary_text:
            out.append(f"say the writer's age ({age}) in the primary text")
    return out


def _all_text(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in ("src", "photo", "image", "product_handle", "theme", "accent", "type"):
                yield from _all_text(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_text(v)
    elif isinstance(node, str):
        yield node


PLAYBOOK = """ADVERTORIAL PLAYBOOK v3 (EcomVital, from the owner's 8 examples + the T-8 edits, 18-20 Oct 2026)

WHAT YOU DELIVER for an "Advertorial + ad creative" task
1. The advertorial page (saved with advertorial_save; EcomVital builds it in the store as a hidden page).
2. One image ad (square, 1:1), made with your image tool from the product (the image_prompts in task_get).
3. Primary text, headline and description for that ad.
Never stop at a plan or a draft in the chat: the task is done only when advertorial_save (or task_save with
result.page) has returned the preview link, and the ad image is on the task.

THE OWNER'S STANDING RULES (from the edits on T-8: follow them first time, no back-and-forth)
- The writer is the task's writer (writer.name, default Maggie Whitefield) and her age (writer.age, default 67) is
  said in both the primary text and the page ("I am 67..."). Byline and signature use her full name.
- It is presented as a real reader's letter: NEVER say or hint that it is fictional, dramatised, invented, a character
  or a composite, anywhere (page, captions, footer, copy).
- Never mention that images are made by AI (no "AI image", "illustrative", "generated", "mock-up" in captions or text).
  Captions are in her voice ("The morning it arrived").
- The primary text follows the HOUSE SAMPLE (house_sample in task_get) beat by beat and is the same length: 475-525
  words, about 11 short paragraphs. It tells the SAME story as the page: same event, same friend and compliment, same
  product details, same reviews. Write the page first, then the primary text as its short version.
- EcomVital checks these before saving and refuses with the reason, so fix and save again.
- Page style: if the task has page_style, use it (EcomVital applies it anyway). A style from the style library comes with how_pages_in_this_style_are_built and structure_to_follow: write the page in exactly that structure (same block types, order and amount), replacing every placeholder with the real story.

THE INPUTS (task_get)
- product: the product the whole advertorial and ad are about (product_facts from its link, or the product photo +
  description staff uploaded). Every claim comes from here.
- landing page (send_to): where every button and link in the advertorial sends readers (often the collection).
- competitor advertorial (optional): replicate it. Admins tweak afterwards.
- reviews: the rating, count and review texts staff collected. Use these and only these.

TWO WAYS TO WORK
- Competitor advertorial given (competitor_page): COPYCAT. Keep their story, structure, section order, angle and length;
  swap in our store, our product, our country's spelling and places, our prices and our reviews. Change names,
  friends, towns and small details so it is not word for word.
- None given: write our own in exactly the style below, from the product.

THE VOICE (every example)
- A woman of about 65-70 writing plainly, warmly and a little drily, in the store's country (British spelling for UK,
  Australian for AU: "till", "shops", "mum"). No marketing words, no exclamation marks, no emojis.
- Short sentences. Small concrete moments (the checkout queue, a 70th birthday lunch, walking the labrador, a coach
  trip, the caravan site, the bowls club). A friend or sister with a backhanded compliment ("Ann told me I looked like I
  had made an effort for once, which I have decided to take as a compliment").
- The product is named with one vivid physical detail (colour, stitching, print, the bit you touch).
- Store described as: "<Store>, a small <British/Australian> boutique that has been quietly dressing women our age for
  a few years now" (homeware/accessories: "quietly making the ordinary things a bit lovelier").
- Signed with the writer's name (default Maggie Whitefield, 67). Use a different town, event and friend from the
  store's other advertorials for the same product.

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

PRIMARY TEXT (the ad): mirror the house sample: 475-525 words in about 11 short paragraphs, beat by beat:
  1 "I almost did not post this." 2 "I am 67 and I am not one for putting things on Facebook. But <N> different women
  asked me about <it> <at the event>... it started to feel a bit mean keeping it to myself." 3 store + "They have a sale
  on at the moment." + "The <thing> is called the <Name>." + one vivid physical description. 4 "I will tell you what I
  told them <where>." 5 what it does, as everyday life (3-5 real benefits, "It does not look like <it> was made for
  somebody half my age, and it does not look like I have given up either"). 6 the moment + a named friend's backhanded
  compliment ("I have decided to take that as a compliment") + wearing/using it again next day. 7 "That is the rarest
  sort of <thing>. The one you can <grand> or <everyday>, and feel right either way." 8 reviews: count, rating, "Not the
  suspiciously perfect five you see on some websites, which if I am honest made me trust it rather more", one real
  quote from the brief. 9 "The reason I am posting today rather than next week is that the sale is on now and <stock /
  sizes / colour> is already going." 10 "If your own <thing> has <problem>, this is where I would start." 11 "— <first
  name>". Frame B tasks keep the same length and the same sale / reviews / close beats.
HEADLINE: under 60 characters. Patterns: "The <product> four women asked me about", "The <product> she wore to the
<event>", "Six things this <product> gets right", "The one <product> she won't stop carrying".
DESCRIPTION: under 45 characters. Patterns: "What I told them at the table", "So I asked her where it was from",
"Read Pat's letter in full".

THE PAGE (page JSON). 1,100-1,700 words, the long version of the same story.
- theme: pick one and vary it between tasks: "letter" (clean, like Perth's first page), "journal" (a letter on cream
  writing paper with a framed border: warm, personal), "editorial" (magazine feature: double rule, drop cap, boxed quotes),
  "scrapbook" (taped-in polaroid photos, sticky notes, dashed boxes: playful). accent: teal, terracotta, forest, navy,
  plum, caramel (or a #hex) that suits the product's colours.
- label: "A letter from a reader"; headline: one long specific sentence with age + a moment.
- standfirst (optional): one italic line under the headline ("Six things the <Name> gets right that everyday <things>
  get wrong, from a woman who does not review <things>").
- author: first name, "<City> · <Month Year>", photo = an older woman's portrait image (or leave it out).
- blocks (use the styled ones; a page with only p and h2 looks flat):
  p {text} (**bold**, ==highlight==) · pull {text} quote in her voice · h2 {text} · step {n, title} numbered heading
  (for the "six things": n 1-6) · image {src, caption, alt, style: plain | framed | polaroid} · cta {text} button ·
  list {items:[{lead, text}]} · checklist {items:[text]} ticked list · box {title, text, items?} bordered callout
  ("What I wish I had known", "The short version") · review {text, name, place, stars, verified} a real review card
  (only reviews from the brief) · note {text} handwritten sticky note · divider · signature {name} handwritten sign-off.
- Order for frame A: 3-5 p and a pull (why she is writing) -> image (as it arrived) -> h2 "How it happens" -> h2 "What I
  had wrong" -> cta -> h2 "The <thing> is called the <Name>" (product details) + image -> h2 "What happened at <event>" +
  images -> h2 "Three things I have learned since" (list or checklist) -> h2 "About the price" ("The <Name> is <price>.
  It was <compare-at price>.", no catch, reviews: count, rating, a review card) -> cta -> h2 "What I would tell you if we
  were friends" -> closing pull -> signature.
- Order for frame B: standfirst, "Let me be plain about who is talking", where she first saw it, image, then six step
  sections (n 1-6) each with 2-3 p (images between some), a "The short version" box, about the price, a review card,
  cta, close, signature.
- 2-3 cta buttons ("See The <Collection>"); they all go to the landing page. 3-5 images.
- card: product name as on the store, its best image, 5-7 bullet benefits from the product (plus the delivery line),
  button "Shop The <Collection/Product>", note "<Sale> on now. The <colour> is selling first." EcomVital adds the sale
  badge, the stars line (from the reviews) and the prices.
- footer: about = "<Full name> is <age> and lives in <city> with <a detail>. She is not a fashion writer.";
  store_line = "<Store> · <delivery line> · <returns line>". EcomVital adds "This page is an advertisement for <Store>."

REVIEWS: use only the rating, count and review texts in the brief. A line that isn't a customer review (no name,
reads like story text) is not used: say so in the task notes.
PRICES: from the product facts (price, compare-at price), in the store's currency.

IMAGES (image_prompts in task_get are ready to use)
- Ad image: square phone-camera photo, no text, no logo, natural light, a little lived-in. Scenes that work: (a) "just
  arrived": the product on a made bed (beige / cream linen), torn grey mailer bag and tissue paper beside it, a brown
  swing tag still on; (b) "out and about": on a weathered park bench with autumn leaves, a bistro table on a cobbled
  street with a cup of tea, a sunny windowsill. The product must look exactly like the real one (same colour, shape,
  stitching, hardware): attach / look at the product photo first.
- Page images (3-4): the same product in home scenes matching the story, plus the narrator's portrait.
- ChatGPT: make each image with your image tool, then add it with task_add_image if you have a link to it; otherwise
  ask the person to download it and drop it on the task (Ad image / page pictures), then read the task again
  (task_get lists every file as file:<id>) and save the page with those ids. Product photos from product_facts can be
  used as they are (their https link).

FINISH: advertorial_save (or task_save with result.page) with page + primary_text + headline + description, submit=true
once the ad image is on the task. Then give the person the preview link.
"""


# ---------------------------------------------------------------- page HTML from the AI's content

def _inline(text: str) -> str:
    """Plain text -> safe HTML, with **bold** and ==highlight== kept."""
    t = html.escape(str(text or "").strip())
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"==(.+?)==", r"<mark>\1</mark>", t)


def _img(src: str, alt: str = "") -> str:
    return f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt or "", quote=True)}" loading="lazy">'


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
    full = max(0, min(5, int(round(r))))
    return "★" * full + "☆" * (5 - full)


def _num(s) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", str(s or "")) or 0)
    except ValueError:
        return 0.0


def accent_of(v) -> str:
    v = str(v or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", v):
        return v
    return ACCENTS.get(v, ACCENT)


def render(page: dict, *, store: str, link: str, images: dict, price: str = "", was: str = "",
           rating: str = "", count: str = "", accent: str = None, style: dict = None) -> str:
    """The page body for Shopify (style + content). images maps each image reference to its final https link.
    style = a custom page style from the style library (its base theme + its own CSS), else page["theme"]."""
    def src(ref):
        ref = str(ref or "").strip()
        return images.get(ref) or (ref if ref.startswith("https://") else "")

    lib = style if isinstance(style, dict) and style.get("css") is not None else None
    theme = (lib or {}).get("base") if (lib or {}).get("base") in THEMES else (page.get("theme") if page.get("theme") in THEMES else "letter")
    out = [f'<p class="lp-label">{_inline(page.get("label") or "A letter from a reader")}</p>',
           f'<h1>{_inline(page.get("headline"))}</h1>']
    if page.get("standfirst"):
        out.append(f'<p class="lp-stand">{_inline(page["standfirst"])}</p>')
    a = page.get("author") or {}
    if a.get("name"):
        photo = src(a.get("photo"))
        out.append(f'<div class="lp-byline">{_img(photo, a["name"]) if photo else ""}'
                   f'<span>{_inline(a["name"])}<br>{_inline(a.get("line"))}</span></div>')
    cta = lambda text: (f'<div class="lp-cta"><a href="{html.escape(link, quote=True)}">'
                        f'{_inline(text or "See The Collection")}</a></div>')
    first_p = True
    for b in page.get("blocks") or []:
        kind = b.get("type")
        if kind == "p":
            cls = ' class="lp-first"' if first_p else ""
            out.append(f"<p{cls}>{_inline(b.get('text'))}</p>")
            first_p = False
        elif kind == "pull":
            out.append(f'<p class="lp-pull">{_inline(b.get("text"))}</p>')
        elif kind == "h2":
            out.append(f"<h2>{_inline(b.get('text'))}</h2>")
        elif kind == "step":
            out.append(f'<div class="lp-step"><span>{html.escape(str(b.get("n") or ""))[:3]}</span>'
                       f"<h2>{_inline(b.get('title') or b.get('text'))}</h2></div>")
        elif kind == "image":
            s = src(b.get("src"))
            if s:
                style = b.get("style") if b.get("style") in ("framed", "polaroid") else ""
                cap = f"<figcaption>{_inline(b['caption'])}</figcaption>" if b.get("caption") else ""
                cls = f' class="lp-{style}"' if style else ""
                out.append(f"<figure{cls}>{_img(s, b.get('alt') or b.get('caption') or '')}{cap}</figure>")
        elif kind == "cta":
            out.append(cta(b.get("text")))
        elif kind == "list":
            for it in b.get("items") or []:
                lead = f"<strong>{_inline(it.get('lead'))}</strong> " if it.get("lead") else ""
                out.append(f"<p>{lead}{_inline(it.get('text'))}</p>")
        elif kind == "checklist":
            out.append('<ul class="lp-check">' + "".join(f"<li>{_inline(x)}</li>" for x in b.get("items") or []) + "</ul>")
        elif kind == "box":
            inner = (f"<h4>{_inline(b['title'])}</h4>" if b.get("title") else "") + (f"<p>{_inline(b['text'])}</p>" if b.get("text") else "")
            if b.get("items"):
                inner += '<ul class="lp-check">' + "".join(f"<li>{_inline(x)}</li>" for x in b["items"]) + "</ul>"
            out.append(f'<div class="lp-box">{inner}</div>')
        elif kind == "review":
            st = stars(b.get("stars")) if b.get("stars") else ""
            who = " · ".join(x for x in (f"<b>{_inline(b['name'])}</b>" if b.get("name") else "", _inline(b.get("place"))) if x)
            ok = ' · <span class="lp-ok">✓ Verified buyer</span>' if b.get("verified") else ""
            rs = f'<p class="lp-rs">{st}</p>' if st else ""
            out.append(f'<div class="lp-review">{rs}<p>“{_inline(b.get("text"))}”</p>'
                       f'<p class="lp-who">{who}{ok}</p></div>')
        elif kind == "note":
            out.append(f'<div class="lp-note">{_inline(b.get("text"))}</div>')
        elif kind == "divider":
            out.append('<div class="lp-div">• • •</div>')
        elif kind == "signature":
            out.append(f'<p class="lp-sign">{_inline(b.get("name") or b.get("text"))}</p>')
    c = page.get("card") or {}
    if c.get("title"):
        s = src(c.get("image"))
        line = f"{stars(rating)} {rating} out of 5 from {count} reviews" if rating and count else (f"From {count} reviews" if count else "")
        off = round(100 * (1 - _num(price) / _num(was))) if _num(was) > _num(price) > 0 else 0
        badge = f'<span class="lp-badge">SALE · SAVE {off}%</span>' if off >= 10 else ""
        out.append('<div class="lp-card">' + badge + (_img(s, c["title"]) if s else "") + f"<h3>{_inline(c['title'])}</h3>"
                   + (f'<p class="lp-stars">{html.escape(line)}</p>' if line else "")
                   + (f'<p class="lp-price">{f"<s>{html.escape(was)}</s> " if was else ""}<b>{html.escape(price)}</b></p>' if price else "")
                   + ("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in c.get("bullets") or []) + "</ul>" if c.get("bullets") else "")
                   + cta(c.get("button") or "Shop Now").replace('class="lp-cta"', 'class="lp-cta" style="margin:0;"')
                   + (f'<p class="lp-cnote">{_inline(c["note"])}</p>' if c.get("note") else "") + "</div>")
    f = page.get("footer") or {}
    out.append('<div class="lp-foot">' + (f"<p>{_inline(f['about'])}</p>" if f.get("about") else "")
               + (f"<p>{_inline(f['store_line'])}</p>" if f.get("store_line") else "")
               + f'<p class="lp-disc">This page is an advertisement for {html.escape(store.strip())}.</p></div>')
    ac = accent or (lib or {}).get("accent") or accent_of(page.get("accent"))
    # mobile: a small "shop" bar fixed at the bottom of the screen, to the same landing page as every button
    btn = (c.get("button") or next((b_.get("text") for b_ in page.get("blocks") or [] if b_.get("type") == "cta" and b_.get("text")), "")
           or "Shop Now")
    out.append(f'<a class="lp-sticky" href="{html.escape(link, quote=True)}">{_inline(btn)} &rarr;</a>')
    extra = (lib or {}).get("css") or ""
    cls = f"mb-lp th-{theme}" + (f" st-{re.sub(r'[^a-z0-9-]', '', str(lib.get('id')))}" if lib else "")
    return (f"<style>{CSS.replace('ACCENT', ac)}{STICKY_CSS.replace('ACCENT', ac)}{extra}</style>\n<div class=\"{cls}\">\n"
            + "\n".join(out) + "\n</div>")


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


def send_to(brief: dict) -> str:
    """Where the advertorial's buttons go: the landing page given, else the collection in the product link, else the
    product link itself."""
    if str(brief.get("send_to_url") or "").startswith("http"):
        return brief["send_to_url"].strip()
    u = str(brief.get("product_url") or "").strip()
    m = re.match(r"(https?://[^/]+/collections/[^/?#]+)/products/", u)
    return m.group(1) if m else u


# ---------------------------------------------------------------- ready-made prompts (task box + task_get)

def _product_line(fx: dict, brief: dict) -> tuple:
    """(name, short physical description, photo link) of the task's product."""
    p = (fx or {}).get("product")
    if not p and (fx or {}).get("collection"):
        prods = fx["collection"].get("products") or []
        p = prods[0] if prods else None
    kind, handle = link_handle(brief.get("product_url"))
    name = (p or {}).get("title") or (handle.replace("-", " ").title() if kind == "product" and handle else "") \
        or (brief.get("product_description") or "").split(",")[0].split(".")[0][:60] or "the product"
    opts = (p or {}).get("options") or {}
    colours = ", ".join(next((v for k, v in opts.items() if k.lower() in ("colour", "color", "colours", "colors")), [])[:5])
    desc = (brief.get("product_description") or (p or {}).get("description") or "")[:260].rsplit(" ", 1)[0]
    photo = ((p or {}).get("images") or [""])[0]
    return name, (f"{desc}... Colours: {colours}." if colours else f"{desc}..."), photo


def prompts(task: dict, brief: dict, fx: dict, store: str, country: str = "", base_url: str = "",
            writer_photo: str = "") -> dict:
    """What a person pastes into ChatGPT (with or without the EcomVital connector), and the image prompts."""
    tid = task["id"]
    wname, wage = brief.get("writer_name") or WRITER_NAME, str(brief.get("writer_age") or WRITER_AGE)
    style = brief.get("page_style") if known_style(brief.get("page_style")) else ""
    name, desc, photo = _product_line(fx, brief)
    plain = re.sub(r"\s*\|.*$", "", name).strip().title() or "the product"
    where = country or "the store's country"
    look = (f"The product is {name}: {desc} It must look exactly like the real product in the attached product photo "
            f"({photo or 'drag the product photo into the chat'}): same colour, shape, stitching, hardware and size. ")
    base = ("Realistic phone-camera photo, natural daylight, slightly imperfect framing, a little lived-in, no text, no "
            "logos, no watermark, no people's faces unless asked. ")
    return {
        "do_the_task_with_connector": (
            f"Use EcomVital: do task T-{tid}. Read it with task_get and follow its advertorial playbook, the owner's standing "
            f"rules and ai_steps exactly. The writer is {wname}, {wage}; the primary text follows the house sample beat by beat "
            f"at {SAMPLE_WORDS - 25}-{SAMPLE_WORDS + 25} words and tells the same story as the page"
            + (f"; page style: {style}" if style else "") + ". "
            f"Write the primary text, headline, description and the full advertorial page, make the square ad image and 3 page "
            f"images with your image tool from the product photo, add them to the task, save everything with advertorial_save "
            f"(or task_save with result.page) and submit it. Don't stop until you can give me the preview link."),
        "write_without_connector": (
            f"You are writing a Facebook advertorial as {wname}, {wage}, in {where}, for {store}, a small "
            f"boutique. Product: {name}. {desc} Readers who click go to {send_to(brief)}. Never say it is fictional and never "
            f"mention AI images. Write: 1) primary text, {SAMPLE_WORDS} words (±25) in 11 short paragraphs following this "
            f"sample beat by beat, for this product: <<<{HOUSE_SAMPLE}>>> signed '— {wname.split()[0]}'; 2) a headline under 60 characters like 'The {plain.lower()} four women asked me about'; 3) a "
            f"description under 45 characters like 'What I told them at the table'; 4) a 1,200-1,600 word 'letter from a "
            f"reader' page telling the long version with section headings. Use only these reviews: "
            f"{brief.get('review_rating') or '-'} stars, {brief.get('review_count') or '-'} reviews, {brief.get('reviews') or 'no quotes'}."),
        "ad_image": (f"Square 1:1 photo. {look}{base}Scene: the {plain.lower()} has just arrived, laid on a made bed with "
                     "beige linen, a torn grey mailer bag and white tissue paper beside it, a brown swing tag still attached, "
                     "soft window light from the left."),
        "ad_image_alternative": (f"Square 1:1 photo. {look}{base}Scene: the {plain.lower()} on a weathered wooden park "
                                 "bench with a few autumn leaves, green hedge behind, overcast soft light."),
        "page_images": [
            f"Landscape 4:3 photo. {look}{base}Scene: on a kitchen table beside a mug of tea, an older woman's hands "
            f"(about 65, a plain ring, cardigan sleeve) using it.",
            f"Landscape 4:3 photo. {look}{base}Scene: on an armchair by the front door at home, a folded blanket beside it.",
            f"Landscape 4:3 photo. {look}{base}Scene: out on the day the story is about (a café table, the shops, a "
            f"garden), the {plain.lower()} in use, nobody's face in frame.",
            (f"No portrait needed: the writer photo on the task ({writer_photo}) is used at the top of the letter."
             if writer_photo else
             f"Square portrait photo of a friendly woman of {wage} from {where}, natural selfie at home, warm light, "
             f"no make-up look, casual top. For the letter's byline."),
        ],
        "after_making_images": ("Add each image to the task (task_add_image with its link, or download it and drop it on "
                                f"the task: {base_url}/tasks?task={tid}), then save the page using the file:<id> "
                                "of each image."),
    }


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


EXAMPLES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "advertorial_examples.json")


def examples() -> list:
    """Example advertorials the AI learns from: the owner's list (setting advertorial_examples, edited in Tasks → Store
    kit), else the 8 examples it started with."""
    try:
        got = json.loads(db.get_setting("advertorial_examples") or "null")
        if isinstance(got, list):
            return got
    except ValueError:
        pass
    try:
        with open(EXAMPLES_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def playbook() -> str:
    return db.get_setting("advertorial_playbook") or PLAYBOOK
