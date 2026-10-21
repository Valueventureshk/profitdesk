"""The chat button: ask EcomVital anything.

Claude Sonnet 5.5 doesn't hold any figures itself. It calls the app's own tools
(the same logic the desks use) and explains the answer; documents are made by
the Reports desk and handed back as a download link. app.py runs the tools and
checks the person's desk access for each one.
"""
import json

import anthropic

MODEL = "claude-sonnet-5-5"
BETAS = ["server-side-fallback-2026-07-01"]

_RANGE = {
    "start": {"type": "string", "description": "First day, YYYY-MM-DD"},
    "end": {"type": "string", "description": "Last day, YYYY-MM-DD"},
}


def _tool(name, description, props, required):
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required,
                             "additionalProperties": False}}


TOOLS = [
    _tool("list_stores", "The stores and store groups, with their ids (use an id as scope).", {}, []),
    _tool("get_profit", "Profit dashboard figures for a period: sales (full order value on the order day), orders, ad spend "
          "(Google/Meta), ROAS, fees (PayPal / Airwallex / Shopify), refunds (on the day refunded) and chargebacks, COG, net profit, margin, reserve held, net available; plus each store's totals when scope is "
          "all or a group.",
          {**_RANGE, "scope": {"type": "string", "description": "all, a store id, or g<group id>"}},
          ["start", "end", "scope"]),
    _tool("get_cash", "Cash flow right now: available now, receivable, held, ads payable, what arrives by "
          "tomorrow / 7 / 14 / 30 / 60 / 90 days, per account.", {}, []),
    _tool("get_cash_history", "Cash flow cards as they stood at 23:59 Hong Kong time on past days (saved every "
          "night): available now, held, receivable, available + receivable, ads payable, after ad bills, "
          "available by tomorrow / 7 / 14 / 30 / 60 / 90 days, per account. Use for 'end of day', 'last night', "
          "'how is cash trending'. Days before saving started have no snapshot: use get_cash_statement.",
          _RANGE, ["start", "end"]),
    _tool("get_cash_statement", "Cash statement between two dates (Hong Kong days, up to 92 days back): opening "
          "and closing 'available + receivable' (closing = at the end of the last day), money in and out, fees, "
          "moves between accounts. Works for any past day, but only gives available + receivable, not "
          "the other cards.", _RANGE, ["start", "end"]),
    _tool("get_expenses", "Actual payments out of Airwallex and PayPal for a period, by category (supplier "
          "payments, Meta ads, Google Ads, subscriptions, processing fees, refunds, chargebacks, salaries, "
          "other), with the biggest payees and what is still unsorted.", _RANGE, ["start", "end"]),
    _tool("get_cog", "COG for a period: sales, COG, COG %, how much is from invoices vs history vs estimates, "
          "and the products with the highest cost share.",
          {**_RANGE, "store": {"type": "string", "description": "all or a store id"}}, ["start", "end", "store"]),
    _tool("get_inbox", "Support inbox: tickets waiting on us, SCM / CS / inquiry counts, escalated, and the "
          "oldest tickets waiting with their AI summaries.", {}, []),
    _tool("get_price_changes", "Invoice price changes still to decide in the COG desk: product, store, old and "
          "new cost (USD), cheaper or dearer, 'to decide' or 'checking' with the supplier, how many orders.",
          {"direction": {"type": "string", "enum": ["all", "down", "up"], "description": "down = cheaper"},
           "store": {"type": "string", "description": "all or a store id"},
           "product_contains": {"type": "string", "description": "part of a product name, or empty"}},
          ["direction", "store", "product_contains"]),
    _tool("propose_price_decision", "Owners only. Propose a decision on invoice price changes: use_new (new price "
          "becomes the product's cost and re-costs its orders), checking (asked the supplier), keep_old (supplier "
          "corrected it). This does NOT change anything: it shows the person a Confirm button. Say clearly that "
          "they need to click Confirm. Use direction down for 'cheaper than history'.",
          {"action": {"type": "string", "enum": ["use_new", "checking", "keep_old"]},
           "direction": {"type": "string", "enum": ["all", "down", "up"]},
           "store": {"type": "string", "description": "all or a store id"},
           "product_contains": {"type": "string", "description": "part of a product name, or empty for all"}},
          ["action", "direction", "store", "product_contains"]),
    _tool("create_report", "Make a downloadable spreadsheet (CSV) in the Reports desk and return its link. "
          "Types: profit_daily, profit_stores, cog_orders, invoices, cash_statement, tickets, ad_bills, "
          "cash_snapshots (cash at end of each day).",
          {"type": {"type": "string", "enum": ["profit_daily", "profit_stores", "cog_orders", "invoices",
                                               "cash_statement", "tickets", "ad_bills", "cash_snapshots"]},
           **_RANGE, "scope": {"type": "string", "description": "all, a store id, or g<group id>"}},
          ["type", "start", "end", "scope"]),
]

_STORE = {"store": {"type": "string", "description": "a store id (see list_stores)"}}
_NUMQ = {"type": ["number", "null"]}
TOOLS += [
    _tool("store_health", "Health check of one store or all: Shopify connection + permissions, product problems (active products "
          "without image / price / stock), tracking pixel (events, last event), Google conversion tracking (which purchase "
          "action Google bids on, what it recorded, EcomVital uploads, how many Shopify orders Google sees), Meta link, "
          "COG / invoices (how much of the last 14 days is costed from invoices vs estimates, last invoice uploaded), inbox "
          "waiting. Returns problems first.", {"store": {"type": "string", "description": "a store id, or all"}}, ["store"]),
    _tool("tracking_live", "Live pixel events from the EcomVital tracking pixel, per hour, split by where the shopper came "
          "from (meta, google, tiktok, other): adds to cart, checkouts, purchases. For 'how many adds to cart from Meta in "
          "the last hour'.", {"store": {"type": "string", "description": "a store id, or all"},
                              "hours": {"type": "integer", "description": "1-48"}}, ["store", "hours"]),
    _tool("google_campaigns", "A store's Google Ads campaigns: status, daily budget, target ROAS, products, and today's and the "
          "last 7 days' spend, Shopify sales, ROAS, clicks, conversions, add to carts; plus the store's breakeven ROAS.",
          _STORE, ["store"]),
    _tool("google_products", "A store's products in Google Ads (all variants combined) over the last N days: spend, Shopify "
          "sales and items sold, ROAS, add to carts, clicks, impressions; optionally one campaign and filters.",
          {**_STORE, "days": {"type": "integer"}, "campaign": {"type": ["string", "null"]},
           "min_spend": _NUMQ, "max_roas": _NUMQ, "min_roas": _NUMQ, "limit": {"type": ["integer", "null"]}},
          ["store", "days", "campaign", "min_spend", "max_roas", "min_roas", "limit"]),
    _tool("propose_google_change", "Propose changes to a store's Google Ads (needs Manage on Google Ads). Nothing changes until "
          "the person confirms. Steps (in order), each {kind, ...}: set_budget {campaign, amount}, set_status {campaign, "
          "status ENABLED|PAUSED}, move_products {products, from_campaign, to_campaign}, add_products {products, campaign}, "
          "remove_products {products, campaign}, create_campaign {name, budget, products, status ENABLED|PAUSED, countries [codes "
          "like AU], target_roas (optional)}. A new campaign is always Performance Max, feed only (ads come only from the "
          "product feed, no assets), bidding to maximise conversion value (plus target ROAS if given), starting now with no "
          "end date, targeting people in the given countries. Campaigns by id or exact name; to use a campaign made earlier "
          "in the same plan write \"new:<its name>\". Products are Shopify product ids (all their variants are used). "
          "Campaigns that pick products by labels / categories (Adon labels) CAN have products taken out (they're excluded "
          "inside the label) and put back; adding brand-new products into a label campaign needs the label. Put "
          "create_campaign first, then move/remove. Google checks the plan in test mode before it's shown. 'Scale by 20%' = "
          "set_budget to budget x 1.2.",
          {**_STORE, "summary": {"type": "string"}, "steps": {"type": "array", "items": {"type": "object"}}},
          ["store", "summary", "steps"], ),
    _tool("google_product_issues", "Products Google won't show (disapproved / limited / not eligible) for a store's Google Ads "
          "account, with Google's reasons, and links to each product in Shopify admin and on the store. Read only.",
          {**_STORE}, ["store"]),
    _tool("find_shopify_products", "Find products in a store's Shopify catalogue by title, status or tag, with their Shopify "
          "admin link and store link (use for 'send me the link to …').",
          {**_STORE, "query": {"type": "string", "description": "words in the title, or a Shopify search like status:draft"},
           "limit": {"type": ["integer", "null"]}}, ["store", "query", "limit"]),
    _tool("create_schedule", "Save a scheduled message: at a time of day (and days of the week) the assistant runs the "
          "instruction and sends the answer to this person (e.g. 'performance report for all stores today' at 17:00).",
          {"name": {"type": "string"}, "instruction": {"type": "string", "description": "what to do / report each time"},
           "time": {"type": "string", "description": "HH:MM 24h"},
           "days": {"type": "string", "description": "daily, weekdays, or comma list like mon,wed,fri"},
           "timezone": {"type": "string", "description": "IANA zone, default Asia/Hong_Kong"}},
          ["name", "instruction", "time", "days", "timezone"]),
    _tool("create_alert", "Save an alert checked every few minutes; the person gets a message when it triggers (once per hour "
          "slot). Kinds: meta_atc_hour (adds to cart from Meta ads in one clock hour >= threshold), atc_hour (all sources), "
          "purchases_hour, google_tracking_stopped (no Google conversion upload for a store in 24 h while it had Google "
          "visitors), no_events (a store's pixel silent for threshold hours).",
          {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["meta_atc_hour", "atc_hour", "purchases_hour",
                                                                          "google_tracking_stopped", "no_events"]},
           "store": {"type": "string", "description": "a store id, or all"}, "threshold": {"type": "number"}},
          ["name", "kind", "store", "threshold"]),
    _tool("list_schedules_alerts", "This person's scheduled messages and alerts.", {}, []),
    _tool("delete_schedule_alert", "Delete one of this person's schedules or alerts by id.", {"id": {"type": "integer"}}, ["id"]),
    _tool("remember", "Save a lasting note you must follow in every future chat (WhatsApp and the app): a rule, definition, "
          "preference or correction the owner gives ('remember …', 'always …', 'from now on …', or 'no, it's like this'). "
          "One clear sentence in your own words.", {"note": {"type": "string"}}, ["note"]),
    _tool("forget", "Delete a saved note by its id (from the notes in your instructions).", {"id": {"type": "integer"}}, ["id"]),
    _tool("product_sales", "Which products sold in a store (or all stores) over a period, from the real Shopify order lines: "
          "orders, units, sales and product cost per product (variants combined; set by_variant for each variant). "
          "Use for 'which products sold', 'best sellers', 'how many orders of X'.",
          {"store": {"type": "string", "description": "a store id, or all"}, **_RANGE,
           "by_variant": {"type": ["boolean", "null"]}, "limit": {"type": ["integer", "null"]}},
          ["store", "start", "end", "by_variant", "limit"]),
    _tool("get_disputes", "The Disputes desk: PayPal cases and Airwallex chargebacks with state (respond = we must answer, "
          "waiting, review, closed), deadline, amount, reason, customer, store and order.",
          {"state": {"type": ["string", "null"], "description": "open (default), respond, waiting, review, closed or all"}},
          ["state"]),
    _tool("facebook_comments", "Inbox → Comments & Messages (Facebook Pages): comments needing a reply, open Messenger chats, and "
          "the comments hidden in the last N days with who hid them (auto = the hide-words list, with the word) and when.",
          {"days": {"type": ["integer", "null"], "description": "hidden in the last N days (default 7)"},
           "store": {"type": ["integer", "null"], "description": "a store id, or null for all"}},
          ["days", "store"]),
    _tool("find_parcels", "Where an order's parcels are (the SCM desk / 17TRACK): status, carrier, tracking number and link, "
          "last event and when. Search by order number (e.g. #AS13184) or the customer's email.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("request_change", "Save a request to change EcomVital: a fix, an improvement to a desk, or a new desk / tool. Use it when "
          "someone asks for something the app can't do yet or wants it to work differently. Write it so a developer can build "
          "it: what they want, why (the business reason), where (desk), and an example. Ask one short question first if the "
          "request is unclear. The owner reviews requests and has them built with Claude Code; nothing changes in the app now.",
          {"title": {"type": "string", "description": "short name, e.g. 'SCM: filter parcels stuck 20+ days'"},
           "details": {"type": "string", "description": "what, why, where, an example; the person's own words where useful"},
           "desk": {"type": ["string", "null"], "description": "desk it's about (dashboard, cash, cog, inbox, scm, disputes, "
                                                                  "expenses, reports, tracking, googleads, metaads, importer, sizecharts, tasks, new desk…)"},
           "kind": {"type": "string", "enum": ["fix", "improvement", "new_desk", "new_tool"]},
           "priority": {"type": "string", "enum": ["low", "normal", "urgent"]}},
          ["title", "details", "desk", "kind", "priority"]),
    _tool("list_change_requests", "Change requests saved with request_change: the person's own (the owner sees everyone's), "
          "newest first, with status (new, planned, done, declined) and the owner's note.",
          {"status": {"type": ["string", "null"], "description": "new, planned, done, declined or all (default: open = new + planned)"}},
          ["status"]),
    _tool("tasks_list", "Tasks desk: the person's tasks (admins see everyone's), each with its id (T-12), title, store, who it's "
          "given to, status (todo, doing, submitted, changes = sent back with changes needed, approved, launched, done) and due date.",
          {"status": {"type": ["string", "null"], "description": "open (default), history, or one status"},
           "assignee": {"type": ["string", "null"], "description": "admins: a person's name or email"}},
          ["status", "assignee"]),
    _tool("task_get", "One task in full by its id (T-12 or 12): the admin's special instructions (always follow them first), "
          "the brief (to-do items and what each must hand back, competitor links, description), what's been handed back so "
          "far, what is still missing before it can be submitted, the comments / change requests from the admin, and files.",
          {"task": {"type": "string"}}, ["task"]),
    _tool("task_start", "Mark a task as started (In progress). For the person it's given to: from To do, Changes needed, or "
          "to take a submitted task back to edit.", {"task": {"type": "string"}}, ["task"]),
    _tool("task_save", "Save work on a task (merged with what's already saved). result fields: landing_url (the main page the "
          "ads go to; required to submit a listing task), collection_url, product_urls (list), advertorial_url, branded_url, "
          "primary_text, headline, ad_description, cta, other_links, notes, custom {to-do item: answer} for custom to-do lines, "
          "cant {to-do item: why it can't be done}. Advertorial tasks: result.page (same as advertorial_save's page) builds the "
          "hidden Shopify page, result.ad_image_url adds the ad image. submit=true also hands it to the admin for review (EcomVital refuses "
          "while something required is missing). Creatives (images/videos) are uploaded on the Tasks desk itself.",
          {"task": {"type": "string"}, "result": {"type": "object"}, "submit": {"type": "boolean"}},
          ["task", "result", "submit"]),
    _tool("task_comment", "Add a comment to a task's history (questions for the admin, progress notes).",
          {"task": {"type": "string"}, "text": {"type": "string"}}, ["task", "text"]),
    _tool("task_review", "Admins only: approve a submitted task, send it back with the changes needed (comment required), "
          "mark it done / launched (set up by hand), or cancel it.",
          {"task": {"type": "string"}, "decision": {"type": "string", "enum": ["approve", "changes", "done", "launched", "cancel"]},
           "comment": {"type": ["string", "null"]}}, ["task", "decision", "comment"]),
    _tool("task_create", "Create a task and give it to someone (they get it on the Tasks desk and WhatsApp). Admins: any kind. "
          "Staff: only kind advertorial, for themselves. kind listing = product / store listing with ads (to-do picks: "
          "advertorial, collection, product_pages, branded_page, creatives, ad_copy, plus any custom lines); advertorial = "
          "an advertorial page + one image ad with its copy (needs store, product_url = our collection or product link, "
          "optional competitor_advertorial link, and the real reviews: review_rating, review_count, reviews); custom = anything "
          "else. Returns its id (T-…).",
          {"title": {"type": "string"}, "kind": {"type": "string", "enum": ["listing", "custom", "advertorial"]},
           "assignee": {"type": ["string", "null"], "description": "name or email"},
           "store": {"type": ["string", "null"], "description": "store name or id"},
           "due": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
           "priority": {"type": ["string", "null"], "enum": ["normal", "high", None]},
           "special_notes": {"type": ["string", "null"], "description": "instructions staff must read first"},
           "todo": {"type": ["array", "null"], "items": {"type": "string"}},
           "description": {"type": ["string", "null"]},
           "competitor_ads": {"type": ["string", "null"]}, "competitor_landing": {"type": ["string", "null"]},
           "competitor_products": {"type": ["string", "null"]},
           "product_url": {"type": ["string", "null"], "description": "advertorial: our collection or product page link"},
           "competitor_advertorial": {"type": ["string", "null"], "description": "advertorial: the competitor's advertorial to copy"},
           "review_rating": {"type": ["string", "null"], "description": "advertorial: e.g. 4.2"},
           "review_count": {"type": ["string", "null"], "description": "advertorial: e.g. 300+"},
           "reviews": {"type": ["string", "null"], "description": "advertorial: real customer reviews, one per line"}},
          ["title", "kind", "assignee", "store", "due", "priority", "special_notes", "todo", "description",
           "competitor_ads", "competitor_landing", "competitor_products"]),
    _tool("advertorial_save", "Advertorial + ad creative tasks: send the advertorial page and the ad copy. EcomVital builds the "
          "page in the store's design and creates it in Shopify as a HIDDEN page (published only when an admin approves the "
          "task); calling it again updates the same page. page = {title (Shopify page title), theme (letter | journal | "
          "editorial | scrapbook), accent (teal, terracotta, forest, navy, plum, caramel or #hex), label, headline, standfirst, "
          "author {name, line ('Sydney · September 2026'), photo}, blocks [ p {text} | pull {text} | h2 {text} | step {n, title} | "
          "image {src, caption, alt, style: plain|framed|polaroid} | cta {text} | list {items:[{lead, text}]} | checklist "
          "{items[]} | box {title, text, items?} | review {text, name, place, stars, verified} | note {text} | divider | "
          "signature {name} ] (each block has its type), card {title, product_handle (for a collection: which product), image, "
          "bullets[], button, note}, footer {about, store_line}}. Text may use **bold** and ==highlight==. Image src/photo/image = file:<id> (a file on the task) or an https "
          "image link (copied into the store's Files). Prices, stars and the 'advertisement' line are added by EcomVital. "
          "Refused (with the reason) if it breaks the owner's standing rules: never fictional / never mentions AI images, "
          "primary text 475-525 words following the house sample with the writer's name and age. "
          "primary_text / headline / description = the ad copy. submit=true also sends the task for review (needs the ad "
          "image on the task). Returns the preview link.",
          {"task": {"type": "string"}, "page": {"type": "object"}, "primary_text": {"type": ["string", "null"]},
           "headline": {"type": ["string", "null"]}, "description": {"type": ["string", "null"]},
           "submit": {"type": ["boolean", "null"]}}, ["task", "page"]),
    _tool("task_add_image", "Save an image you made or found on a task, from its https link: use 'ad' = the ad image (the "
          "creative), 'page' = a picture for the advertorial page. Returns file:<id> to use in advertorial_save. If the link "
          "can't be read, ask the person to download the image and drop it on the task.",
          {"task": {"type": "string"}, "image_url": {"type": "string"}, "use": {"type": "string", "enum": ["ad", "page"]},
           "name": {"type": ["string", "null"]}}, ["task", "image_url", "use"]),
    _tool("data_schema", "Owner only: the business tables you can read with data_query (orders and product costs, parcels, "
          "disputes, tickets, pixel events, Google uploads, invoices, payments, saved daily totals…), with their columns and "
          "what they mean. Call this before writing a data_query.", {}, []),
    _tool("data_query", "Owner only: run ONE read-only SQLite SELECT over the tables from data_schema, for questions no other "
          "tool answers (averages, counts, breakdowns, e.g. average days from fulfilled to delivered per store, orders per "
          "country, products by refunds). Aggregate in SQL (GROUP BY, AVG, COUNT, julianday() for day differences); at most "
          "200 rows come back. Join stores for names. Dates are text: ISO UTC timestamps or YYYY-MM-DD store days.",
          {"sql": {"type": "string"}, "purpose": {"type": "string", "description": "what this answers, in a few words"}},
          ["sql", "purpose"]),
]
# Anthropic allows at most 20 strict tools: the simple ones (few or no inputs) don't need it.
_LOOSE = {"propose_google_change", "list_stores", "get_cash", "get_inbox", "list_schedules_alerts", "delete_schedule_alert",
          "store_health", "tracking_live", "google_campaigns", "google_product_issues", "find_shopify_products",
          "remember", "forget", "product_sales", "get_disputes", "find_parcels", "data_schema", "data_query", "request_change", "list_change_requests", "tasks_list", "task_get", "task_start", "task_save", "task_comment", "task_review", "task_create", "advertorial_save", "task_add_image"}
for _t in TOOLS:
    if _t["name"] in _LOOSE:
        _t.pop("strict", None)
assert sum(1 for _t in TOOLS if _t.get("strict")) <= 20, "too many strict tools"

SYSTEM = """You are EcomVital's assistant for the owner and staff of a group of Shopify stores.

About EcomVital (the app you live in, formerly ProfitDesk, at hq.ecomvital.com; same assistant in the app's Ask button and on WhatsApp):
The owner, Nafras, runs about 12 dropshipping Shopify stores (Australia, Canada, Europe…) through Value Ventures Limited, Hong Kong. He built EcomVital with Claude Code: when you lack a tool or data, say exactly what's missing and suggest he ask Claude Code to add it. Never send him to an "admin" or "support"; he is the admin.
Desks (pages) and what they hold:
- Dashboard: profit per store and day (sales, refunds, chargebacks, fees, COG, Google + Meta spend, net profit).
- Cash flow: Airwallex + PayPal balances, money on hold, arriving soon; statement; end-of-day snapshots.
- COG: product cost of every order line from the supplier orders sheet and invoices; price changes; suppliers (owed, invoices).
- Expenses: payments out of Airwallex/PayPal by category.
- Inbox: the stores' support mailboxes turned into tickets (SCM = shipping, CS = customer service), AI drafts.
- SCM: every order's parcels with 17TRACK tracking (status, carrier, fulfilled/delivered dates, transit days), notes, flags; customer tracking page.
- Disputes: PayPal cases and Airwallex chargebacks, deadlines, evidence, AI replies.
- Apps → Tracking: our own pixel in each store (visits, adds to cart, checkouts, purchases per ad); sends purchases to Google Ads (Data Manager).
- Apps → Google Ads: product report vs Shopify sales/COG, verdicts, and a Manage tab (AI plans + rules, every change confirmed, undo).
- Apps → Checkout: our own Airwallex payment page with subscriptions (Amber Ashton refill first).
- Also: Reports (CSV exports), Size Charts, Product Importer, Session Recordings, Settings (connections, people, WhatsApp, backups).
- Time (Apps): staff hours, activity %, apps/sites and screenshots from the EcomVital Time desktop app (Mac + Windows), like Hubstaff.
- Agent (Apps): the ops agent working through Slack: its work log (jobs and steps), advice that becomes lessons, its questions; it files supplier invoices posted in Slack into COG, keeps bank transfer notices and checks them against the bank, shows each supplier's position (paid up to which invoice/order, on credit) and has its own Ask chat.
- Meta Ads (Apps): campaigns / ad sets / ads with Meta's and the pixel's results; Manage tab where Claude proposes changes and rules for Meta ads (Ask me first: every change waits for Do it, with Undo).
- Tasks: a board where admins give staff work: listing + ads (competitor ad / landing / product links and a To do list: advertorial, collection, product pages, branded page, creatives, ad copy, custom items) or a custom task, with special instructions (text, voice notes, screen recordings); staff hand back one main landing page link plus each item, or a note saying why they couldn't; admins approve, then tick one or several approved cards and launch them on Meta with a strategy (X/Y/Z), everything PAUSED (switched on in Ads Manager); launched tasks go to History. End of day shows what each person did.
WhatsApp: you answer only in this one-to-one chat (no group chats). Voice notes need the OpenAI key in Settings → WhatsApp. Changes you propose are confirmed by replying YES.

How to work: understand what the person really wants, then get the real figures. Prefer the ready-made tools; when none fits, the owner can use data_schema + data_query to compute it from the raw tables (e.g. average delivery time = AVG(julianday(delivered_at) - julianday(fulfilled_at)) on shipments with status delivered). Use several tools if needed. Don't substitute a weaker proxy (like ticket counts) when the real data exists, and don't stop at "I can't" without trying data_query.
Answer questions about their business using the tools: profit, cash flow, COG, the support inbox and reports. Never make up a figure: if a tool can't give it, say so.
Today is {today} (Hong Kong time). "Today", "yesterday", "last week" (the 7 days ending yesterday), "this month" and similar mean dates on that clock.
Money is in {currency} unless a tool says otherwise; write amounts with their currency (e.g. A$1,234 for AUD, US$ for USD). Keep answers short and plain: lead with the answer, then the key numbers. Use a small table when comparing several stores or days.
For cash on a past day, prefer get_cash_history; if that day has no snapshot, use get_cash_statement and say only "available + receivable" can be rebuilt for it.
When someone asks for a document, file, export, spreadsheet or report, use create_report and give the download link as a Markdown link.
If a tool says the person has no access, tell them which desk they'd need.
For "check everything / store health / are things working" use store_health. For live events ("adds to cart in the last hour") use tracking_live.
Scheduled reports and alerts: create them with create_schedule / create_alert and confirm the time, days and threshold in one line.
When the owner gives a rule, preference, definition or correction meant to last, save it with remember and say "Noted." Follow the saved notes below; if a note conflicts with what you're asked now, follow the request and ask whether to update the note.
Advertorial tasks (kind advertorial, "Advertorial + ad creative"): task_get gives the Advertorial Playbook, the real product facts, the competitor's advertorial text (copy it when given) and the staff's real reviews; write the page and the ad copy in that playbook's voice, add images with task_add_image (or ask the person to drop them on the task), finish with advertorial_save, then give the preview link.
Tasks: people refer to tasks by id (T-12). Before working on one, read it with task_get and follow its special instructions first, then the to-do items; keep the person informed of what's still missing; save progress with task_save and submit only when they say it's ready. Admins review with task_review (send back with clear change notes).
When someone wants the app to do something new or differently (a fix, a better desk, a new desk or tool), save it with request_change after making sure you understand it; tell them the owner reviews requests. For product-level sales use product_sales; for disputes use get_disputes; for "where is order …" use find_parcels.
You can propose changes with the propose_ tools (owners only). They never change anything themselves: the person sees the action with a Confirm button. Before proposing, check the list (e.g. get_price_changes) and say what will change. Never claim something was done until they confirm."""


class ChatError(RuntimeError):
    pass


async def reply(api_key: str, history: list, run_tool, today: str, currency: str, notes: list = None) -> dict:
    """history: [{"role": "user"|"assistant", "content": text}]. run_tool(name, input) -> dict.
    Returns {"text", "links": [...]}"""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=180)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-20:]
                if m.get("content") and m.get("role") in ("user", "assistant")]
    tidy = []   # the API wants user/assistant turns alternating, starting with the user
    for m in messages:
        if not tidy and m["role"] != "user":
            continue
        if tidy and tidy[-1]["role"] == m["role"]:
            tidy[-1]["content"] += "\n\n" + m["content"]
        else:
            tidy.append(dict(m))
    messages = tidy
    links, actions = [], []
    for _ in range(12):
        try:
            resp = await client.beta.messages.create(
                model=MODEL, max_tokens=8000, betas=BETAS, fallbacks="default",
                system=SYSTEM.format(today=today, currency=currency) + (
                    "\n\nSaved notes from the owner (follow these):\n" + "\n".join(f"[{n['id']}] {n['note']}" for n in notes)
                    if notes else ""),
                tools=TOOLS, output_config={"effort": "medium"}, messages=messages)
        except anthropic.AuthenticationError as e:
            raise ChatError("Anthropic didn't accept the API key. Check it in Settings → AI.") from e
        except anthropic.RateLimitError as e:
            raise ChatError("The AI is busy right now. Try again in a minute.") from e
        except anthropic.APIStatusError as e:
            detail = ""
            try:
                detail = (e.body or {}).get("error", {}).get("message", "") if isinstance(e.body, dict) else str(e)[:300]
            except Exception:
                detail = str(e)[:300]
            print(f"Anthropic {e.status_code}: {detail}", flush=True)
            raise ChatError(f"Anthropic error {e.status_code}: {detail[:300]}") from e
        except anthropic.APIConnectionError as e:
            raise ChatError("Couldn't reach Anthropic.") from e
        if resp.stop_reason == "refusal":
            return {"text": "I can't help with that one.", "links": links, "actions": actions}
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return {"text": text or "(no answer)", "links": links, "actions": actions}
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            try:
                out = await run_tool(b.name, b.input)
                if b.name == "create_report" and out.get("download"):
                    links.append({"title": out.get("title"), "url": out["download"]})
                if out.get("proposed_action"):
                    actions.append(out["proposed_action"])
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                 "content": json.dumps(out, default=str)[:30000]})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": str(getattr(e, "detail", None) or e)[:500]})
        messages.append({"role": "user", "content": results})
    return {"text": "That took too many steps. Try a more specific question.", "links": links, "actions": actions}
