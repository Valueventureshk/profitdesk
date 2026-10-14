# ProfitDesk (shown to users as EcomVital) — agent brief

Read this before changing anything.

## Who you're working with

The owner is a Shopify/DTC marketer, **not a developer**. Assume no coding
knowledge. This means:

- Explain what you changed in plain language. No jargon, no diffs pasted into chat.
- Never ask them to edit code by hand. Make the change yourself.
- Never ask them to "just run" a command without saying exactly what to type
  and what should happen.
- If something breaks, fix it. Don't hand back a stack trace and ask what to do.
- When you need a decision, offer 2–3 concrete options in business terms
  ("show profit per channel" vs "show profit per product"), not technical ones.

## What this app is

User-facing name is **EcomVital** since 13 Oct 2026 (logo static/brand/). Code, files and storage keys keep the ProfitDesk
name. Keep the Google conversion action names "ProfitDesk Purchase / Add to cart / Begin checkout" (they exist in the
ad accounts; gads_conv.ACTION_NAMES) and the WhatsApp template text as they are.


A profit dashboard. Pulls Shopify orders and Google Ads spend,
applies cost assumptions, shows true net profit and blended metrics. Answers
one question: *did I actually make money, and are my ads working?*

Runs at `http://127.0.0.1:8787` on the owner's Mac and is being moved to
Railway (deploys from GitHub `Valueventureshk/profitdesk`, branch `main`).
SQLite file (on a Railway volume via `DB_PATH`). Email + password login for the
owner and their partner (`auth.py`); every route except /login and /healthz
needs a session. On Railway, set `PUBLIC_URL`, `DB_PATH` and `SETUP_CODE`.
Settings → Backup downloads/restores the whole database (that's how data moves
between the Mac and Railway).

## Stack

Python 3 + FastAPI + httpx + SQLite (stdlib). Frontend is vanilla HTML/CSS/JS,
**no build step, no framework, no npm**. Charts are hand-rolled SVG.

Keep it that way. Do not introduce React, a bundler, Tailwind, or a package
manager. The zero-build setup is deliberate — the owner needs to double-click a
file and have it work.

## Files

| File | Role |
|---|---|
| `app.py` | Routes and server. Start here to trace a request. |
| `metrics.py` | **The P&L engine.** One definition per metric. |
| `db.py` | SQLite schema + queries. Schema is at the top. |
| `shopify_client.py` | Admin GraphQL, rolls orders into daily totals |
| `google_ads_client.py` | OAuth + GAQL spend reporting |
| `meta_ads_client.py` | Meta System User token + Insights spend reporting, ad account balances |
| `google_sheet.py` | Google spend from the Sheet the Google Ads scripts write hourly |
| `google-ads-script.js` | The script pasted into each Google Ads account (hourly schedule) |
| `auth.py` | Email + password login, sessions |
| `fx.py` | Daily exchange rates (ECB + open.er-api) |
| `airwallex_client.py` | Airwallex balances, financial transactions, card (Issuing) transactions |
| `paypal_client.py` | PayPal balances + transaction feed; hold release schedule |
| `cashflow.py` | **Cash flow engine**: available, receivable, arriving by day N |
| `statement.py` | The statement pop-up: opening → movements → closing, sales check |
| `adbills.py` | Ads payable: Meta balance, Google spend since its last card charge |
| `cog.py` | **COG engine**: orders sheet → per-store product costs → cost of every order line |
| `tracking.py` `static/tracking.*` `shopify-app/` | Apps → **Tracking** (like wetracked.io): Shopify web pixel extension (`shopify-app/extensions/profitdesk-pixel`, deployed with the Shopify CLI into each store's app) posts events to `/t/e`; last-click (7 days) UTM attribution; report of adds to cart / checkouts / purchases per campaign / ad set / ad. Watch-only per store until Meta CAPI sending is added. Pixel deployed (12 Oct 2026) into CUTEHOME + Dovaro, Vizo, Tendance, Beige Rue, Taylor Maria, Revionel apps: one `shopify-app/shopify.app.<store>.toml` per store app (made with `config pull` over a placeholder, then `shopify app deploy --config <store> --force`); then `/api/tracking/install/<id>`. Shopify's customerJourneySummary drops the gclid, so Google uploads need the pixel's click ids (`gads_conv.py`). Tracking → Google Ads panel: conversion actions per account, primary/secondary switch. ProfitDesk → Google (`_gconv_loop`, every 10 min): pixel events with a Google click (px_visitors.g_click, last Google click ≤30 days) → Google **Data Manager API** (`datamanager.googleapis.com/v1/events:ingest`, scope `auth/datamanager`, API enabled in the Cloud project; Google refuses uploadClickConversions for new integrations), one event per request, into its own actions "ProfitDesk Purchase / Add to cart / Begin checkout" (purchase value = order total − tax, orderId = order name, hashed email/phone unless the account hasn't accepted customer data terms → `ids` off); log `gconv_uploads`; per store `gconv:<id>` {enabled, mode watch|auto|primary, started_at, switched_at}; auto = switch ours primary after ≥8 days when ours ≥95% of the primary's 7-day purchases. Plan (`/api/tracking/google/plan`, Do it): TrackBee cancelled 12 Oct 2026 → Google Shopping App Purchase bridge primary (Tendance, Beige Rue, Dovaro; Revionel already) + ours auto; Vizo + Taylor Maria ours primary; ZAVA untouched. Hour picker (`hour=` 0-23, one day, store clock; Meta spend via the hourly breakdown on the ad account's clock). Column order per browser (`pdTrkCols2`) |
| `google_desk.py` `static/googleads.*` | Apps → **Google Ads**: Google product report (shopping_performance_view) grouped per Shopify product with variants, vs Shopify sales / COG / fees, one store at a time, everything in the Google Ads account's currency; period picker (`start`/`end`, window "sel") plus 7/14/30 store days incl. today (same days as the dashboard), breakeven ROAS + verdicts. Summary totals come from the dashboard engine (`_store_window` → `_in_currency` → `google_desk.with_dashboard`), so they match the dashboard to the cent; product rows are Google's product report; PMax spend on no product + Search = "not on a product". `/api/googleads/check?store=` compares Google / campaigns / products / dashboard. Laid out like pythago.io: filters (min/max ROAS, ad days, min cost, status, country, tags, verdict), bulk Draft/Archive/Active in Shopify (`/api/googleads/status`, Manage), tags (`gads_tags`, `gads_tag_products`), refunds per product (`ShopifyClient.refund_lines`), country (`cog_lines.country` + feed country in the item id), Period 1 vs 2 (`/api/googleads/compare`). Speed: product details kept 6 h (`_pinfo`), `_gdesk_loop` reads each store's 30-day view every 30 min (~336 product reports/day of Explorer's 2,880) |
| `recordings.py` `static/recordings.*` `static/recorder.js` | Apps → **Session Recordings**: rrweb recorder (ScriptTag, off by default per store), chunks via app proxy `/apps/track/rec` → `<DB dir>/recordings/`, replay with rrweb-player |
| `disputes.py` `static/disputes.*` | **Disputes desk**: PayPal cases + Airwallex chargebacks in one shape, order evidence pack (PDF/PNG, bundled Noto Sans in `assets/`), AI draft (Sonnet), actions |
| `suppliers.py` | **Supplier accounts**: opening + fulfilled orders' COG − Airwallex payments = owed; invoice expected vs invoiced (COG page → Suppliers) |
| `invoices.py` `invoice_ai.py` | Supplier invoices: read (PDF/Excel), match to orders, price + bundle check; AI second reading |
| `static/cog.html` `cog.js` | COG + Products Monitor page (Check COG, Products) |
| `products_desk.py` | COG → **Products** table (`/api/cog/pdesk`): one row per Shopify product per store (variants together; lines without product_id join by title): image / listed date / status from Shopify (`PD_PRODUCTS`, 6 h cache), units + orders + last order from cog_lines (BOGO ×2), latest confirmed single cost + price history (`cost_history`, Catalog tier-1 entries), size chart yes/no (`_sc_match` with `_sc_rules`), CS tickets (support/legal) matched by ticket order numbers else the customer's latest order (shipments email), title words pick the product in multi-product orders. Team edits: `pd_info` (supplier, material), `pd_notes` (complaint / note), `pd_qc` photos in `<DB dir>/qc/` (JPEG + _t thumb). Work level can edit. |
| `mail_client.py` | Store support mailboxes over IMAP (app password): Inbox + Sent, read-only |
| `ai_brain.py` | Claude Haiku 5.5 sorts each email (customer/inquiry/legal/other, SCM/CS, thread) |
| `tickets.py` | Turns sorted emails into tickets; our Gmail replies set "we replied last" |
| `static/inbox.html` `inbox.js` | Inbox desk: mail views, SCM/CS tickets, records, AI training |
| `ai_coach.py` | Learns a playbook from past replies + SOPs; drafts replies (Claude Sonnet 5.5) |
| `sizecharts.py` `static/sizecharts.*` | Size Charts desk: Panda/Kiwi import, block editor, AI chart from image (Sonnet), "Find my size" advisor (Haiku) |
| `static/sizechart-widget.js` | Storefront size chart pop-up, added to each store as a ScriptTag; data via the app proxy `/apps/track/sizechart` |
| `reports.py` `static/reports.*` | Reports desk: CSV downloads built from the desks' own figures (no AI) |
| `ai_chat.py` `static/chat.js` | Chat button on every desk: Sonnet 5.5 calls the app's tools, never invents figures |
| `expenses.py` `static/expenses.*` | Expenses desk: actual payments out of Airwallex + PayPal by category; owner's picks in `expense_rules`, AI suggestions (Haiku) |
| `track17.py` `static/scm.*` | SCM desk: every order's parcels (Shopify) + where they are (17TRACK API, webhook `/api/scm/webhook`) |
| `importer.py` `static/importer.*` | Product Importer desk (like Poky): load any Shopify store's products.json, apply price/currency/tag rules, create in our stores with productSet (Draft by default). `static/product-edit.js`: Edit an imported product in-app (title/description + AI rewrite, images via staged upload, prices, size chart, tags, activate) |
| `gads_manage.py` `ads_engine.py` `ads_brain.py` `static/googleads-manage.js` | Google Ads → **Manage** tab: per-store ads AI (Sonnet) that reads campaigns/products and proposes plans (Do it) and rules (Turn on); never changes Google itself. Changes via googleAds:mutate, each validated (validateOnly) first, logged in `ads_actions` with `before` for Undo. Product moves = item-id product lists (asset group listing group filters / Shopping listing groups); campaigns picked by labels ("custom") are read-only. Rules (`ads_rules`: roas_floor, graduate, new_products, scale_budget) run hourly in `_ads_loop` when the store's managing is on; Ask-me-first = proposals wait, Autopilot = carried out. Store settings `ads_manage:<id>` (enabled, mode, budget_cap, new_budget_cap) |
| `checkout.py` `static/pay.*` `static/pay-manage.html` `static/checkout.*` | Apps → **Checkout** (Lasso-style own payment page, desk `checkout`): offers (`co_offers`: one Shopify variant, one-time and/or subscription price every N days, shipping, countries) at secret links `/pay/<slug>` (public, `/pay/` is open in the login middleware). Airwallex Drop-in (`static.airwallex.com/components/sdk/v1`) takes the card on our page; subscriptions: Airwallex customer + intent, Drop-in `payment_consent {next_triggered_by: merchant, merchant_trigger_reason: scheduled}` saves the card. `/pay/api/done` (and webhook `/pay/webhook`, x-timestamp + x-signature HMAC) reads the intent back, checks SUCCEEDED + amount, creates `co_subs` and the paid Shopify order (`orderCreate`, gateway Airwallex, tags profitdesk-checkout / subscription / subscription-renewal). `_co_loop` (10 min) renews due subs: new intent + confirm `triggered_by merchant` with the saved consent/card; failures retried daily 3×, then status failed. Customer manage page `/pay/manage/<token>` (skip / pause / cancel). Keys: setting `co_awx` (own restricted key, Payment Acceptance; money lands in Cutehome Australia), `co_test` (default on: max 5.00 per payment, Shopify test orders, which every figure skips). First store: Amber Ashton (refill $30/month, $50 one-time). Not yet: cart → checkout redirect, Apple Pay domain file, discount codes, page editor, renewal reminder emails |
| `whatsapp.py` | WhatsApp (Cloud API): webhook `/api/whatsapp/webhook` (signed with the Meta app secret), allowed numbers → ProfitDesk users (`wa_people`), replies via the Ask assistant (`ai_chat.reply` + `_chat_tool`), YES/NO confirms proposed actions, files saved to `<DB dir>/wa_files` (PDF/Excel or caption 'invoice' → COG invoice upload), reports/alerts pushed (text inside 24 h, else template `profitdesk_update`). Settings → WhatsApp (owner) |
| `static/cash.html` `cash.js` `nav.js` | Cash flow page, section menu |
| `static/index.html` `styles.css` `app.js` | The dashboard |
| `seed_demo.py` | Generates fake data for testing |
| `start.command` / `start.bat` | Double-click launchers |

## Current setup (keep this section up to date)

- Live at **https://hq.ecomvital.com** (main address since 13 Oct 2026, `PUBLIC_URL`); the old
  https://profitdesk-production.up.railway.app still serves the same app. Railway project "sparkling-beauty",
  service "profitdesk". Every push to `main` deploys. Public brand **EcomVital**: https://www.ecomvital.com (same app,
  host-based: `SITE_HOSTS` serve static/legal home/privacy/terms + `/api/interest` beta sign-ups, list at `/interest`;
  ecomvital.com forwards to www at GoDaddy). DNS at GoDaddy (hq + www CNAMEs to Railway). `SHOPIFY_REDIRECT_URL` keeps
  Shopify OAuth returning to the old address for store apps not yet listing hq (the 7 CLI apps list both). Google
  OAuth client lists both callbacks; consent screen branding "EcomVital" verified + published 13 Oct (logo
  static/brand/, domain verified in Search Console under info@tendance-montreal.com). All 12 store apps (CLI tomls in shopify-app/, one per store,
  incl. zava/perth/creston/sageleaf/amber pulled 13 Oct), the Airwallex checkout webhook, Apple Pay domain
  (assets/applepay served at /.well-known/), 17TRACK webhook and WhatsApp webhook point at hq.ecomvital.com.
  After a deploy, open `/healthz`: `db_on_volume` must be `true`.
- Database lives on the Railway volume (`/data/profitdesk.db`). Credentials for
  Shopify apps, Meta, Google, Airwallex and PayPal are stored in that database,
  pasted in by the owner through the app. Never ask for them in chat.
- Railway variables: `PUBLIC_URL`, `DB_PATH`, `SETUP_CODE`, plus the names in
  `.env` (Shopify/Google client ids and secrets, API versions). Values never in chat.
- To check the live app, use a browser where the owner is logged in and call the
  app's own `/api/...` routes. Don't use the owner's personal Chrome without asking.
- People and access: owner (everything, incl. Settings, connections, people, backups) or staff
  with a level per desk in `users.access` JSON (`auth.access_of`; people from before keep
  write→Work, read→View). View = GET only; Work = what `_WRITE_OK` lists; Manage = every other
  change on that desk, plus `_need_level(request, desk, "manage")` inside routes (e.g. dispute
  refunds/accepts/offers). Presets in People: Manager, VA (Inbox+SCM), Customer service, Viewer.
  Pages hide `data-write-only` (View), `data-manage-only` (below Manage, for the page's desk),
  `data-owner-only` (connections/settings). New people get a temporary
  password and must choose their own at first login (`/change-password`).
- Inbox: mailboxes are read every 2 min. Replying (Railway Hobby blocks SMTP): each mailbox
  signs in once with Google for gmail.send only (`gmail_send.py`, Settings → Support mailboxes →
  Sign in to send; token in `mail_accounts.gmail_token`; needs the Gmail API enabled in the Google
  Cloud project). Replies go through the Gmail API into the same Gmail thread (thread id found over
  IMAP via X-GM-THRID), then are read back from Sent like any Gmail reply. Translate: `ai_brain.translate`
  (Haiku), saved in `mail_translations`; only each email's new part (`ai_brain.new_part`).
  Labels: Gmail labels two-way over IMAP (`mail_labels.py`, X-GM-LABELS, whole thread), synced in
  `_mail_loop`; tables `mail_tags`, `mail_message_tags`, `tag_colors`; AI suggests (`ai_brain.suggest_labels`,
  `mail_messages.tag_suggest`), staff confirm. Attachments: fetched from Gmail when first opened (whole email in
  one trip, max 3 at once), kept on the volume in `<DB dir>/attachments/<message id>/` for 60 days unopened;
  previews are JPEG (Pillow + pillow-heif for iPhone HEIC); only images/PDF open inline, the rest download. AI key = setting
  `anthropic_api_key` (owner pastes it in Settings → AI). Tickets only for emails from
  setting `tickets_from` (default 8 Oct 2026 HK). Team can override: close, reopen,
  snooze, escalate, relabel, "not a customer email".
  A mailbox with no store ("All stores", the company email) gets each ticket's store from the order
  numbers in the email (store order tags) or the customer's email in SCM parcels (`_customer_orders`).
  Per mailbox `auto_ask`: a new customer ticket (email < 2 days old) with no order found gets one AI
  reply (Haiku, customer's language) asking for order number, name and checkout email (`tickets.auto_asked`).
- Cash at end of day: `_snapshot_loop` saves the Cash flow cards at 23:59 HK into
  `cash_snapshots` (one row per day). Shown in Reports, report type `cash_snapshots`,
  chat tool `get_cash_history`. Days before 10 Oct 2026 have none; the chat falls back
  to the statement (`get_cash_statement`), which only rebuilds available + receivable.
- Expenses desk (desk `expenses`): reads movements via statement.py, nets card holds per
  card transaction, skips moves between own accounts. Unknown payees go to "To sort";
  the owner's pick is saved per payee key and applies to past and future payments.
  Conversion cost is the only computed line (legs at today's rate).
- SCM desk (desk `scm`, replacing Parcel Panel): `_scm_loop` every 10 min reads each store's
  orders changed since the last run (first run: 60 days) into `shipments` (one row per
  tracking number; number '' = waiting for tracking). Parcels from the stores ticked in
  SCM → Tracking connection, fulfilled on/after `track_from`, are registered with 17TRACK
  (1 quota each, once). Updates arrive by webhook (signed sha256(body/key)); a daily
  `gettrackinfo` catches up. Key = setting `track17_key` (owner pastes it on the SCM page).
  Customer tracking page: Shopify app proxy apps/track → /proxy/track (signed; owners can
  preview unsigned), dropship mode per store, "In transit to <state>". "Tell Shopify" per
  store (setting scm_push:<id>, off by default) sends fulfillment events (IN_TRANSIT,
  OUT_FOR_DELIVERY, DELIVERED...) once each; turning it on marks current statuses as sent.
  SCM page has Orders (tiles, status tabs, More views: stuck/flagged/with notes/not
  tracked/cancelled, export CSV, bulk flag) and Settings (17TRACK). Team notes and
  flags are per order (`scm_notes`, `scm_flags`), so they survive parcel changes.
  Store menus: owner tool POST /api/storefront/tracking {apply|revert, stores} points
  Parcel Panel links at /apps/track. App proxy must be in each app version (all 12 have it since 13 Oct 2026).
  Store permissions: shopify_client.SCOPES (27, see SHOPIFY-APP-RELEASE.md), check
  /api/shopify/permissions; opening the app from Shopify admin asks for missing ones.
- Size Charts desk (desk `sizecharts`): charts in `size_charts` (blocks JSON), who sees them in
  `size_chart_rules` (product > collection > tag > type > vendor > all, per store, optional countries).
  Turning a store on in Display settings installs the ScriptTag; the widget asks the app proxy
  (`/proxy/track/sizechart`, so stores need the App proxy). cm→in converts measurement columns only.
  Product Importer setting `size_chart_mode`: none / chart (assign one) / ai (read the source's
  size-chart image, one chart per source product shared by all target stores).
- Disputes desk (desk `disputes`, `/disputes`): reads each PayPal / Airwallex connection's disputes
  (5-min cache, open PayPal cases read in full) into `disputes`; team actions in `dispute_log`.
  Order found by payment id (order_fees), order number, or amount within 3 h; else link by hand.
  Actions only as PayPal's links / Airwallex's status allow (`disputes.ACTIONS`, with the level each
  needs). PayPal needs the app's "Disputes" feature; Airwallex key needs Payment disputes + File upload.
  PayPal multipart: part "input" (JSON) + file parts named per the spec (evidence-file, message_document...).
- Google sign-in keys (OAuth client, setting `google_client_id/secret`, changeable in Settings → Google
  Ads → Change Google sign-in keys) come from Google Cloud project "My First Project"
  (dark-smile-511222-t3) owned by info@tendance-montreal.com: it has Google Ads API **Basic** access since 13 Oct 2026
  (15,000 ops/day, approved minutes after brand verification of EcomVital; Standard = unlimited, apply in Cloud → Google Ads API → Access levels, ~10–15 working days).
  APIs on: Google Ads, Sheets, Gmail. Consent screen "ProfitDesk", published (unverified warning).
  Google login connected in ProfitDesk = shop.valueventures@gmail.com (sees the ad accounts).
  Per store `google_source`: NULL = direct API (hourly, shifted to the store clock), "sheet" = the
  script's Sheet. `/api/google/compare` checks both. All 7 Google stores (incl. ZAVA + Revionel) read the API
  directly (checked 13 Oct 2026); today/yesterday re-read at most every 10 min, last figures / Sheet if the
  daily allowance runs out.
- Speed: SQLite in WAL mode with a 10 s busy timeout (`db._conn`, `db._wal`; restore checkpoints and drops
  the side files). GZip on every response; /static files linked with ?v=<hash> are cached for a year.
  Heavy work (COG costing, re-costing, Google desk maths) runs in threads (`asyncio.to_thread`) so pages
  never queue behind it. A watchdog notes any stall over 0.6 s with the ProfitDesk code that caused it:
  owner route `/api/debug/stalls`, counts in `/healthz` (`stalls_10min`). New slow code: check there first.
- Dashboard: answers with its last figures and refreshes in the background (`_dash`, "refreshing");
  completed Shopify days and refunds come from `shopify_days` / `refund_days` (saved every 6 h and on
  read), only the last `LIVE_DAYS` live; outside-Shopify refunds + chargebacks come from `outside_days`
  (`_outside_loop`: last 30 days every 15 min, 190 days every 6 h). Step timings: `/api/debug/timing`.
- Assistant knowledge: ai_chat.SYSTEM describes the owner, every desk and WhatsApp limits (update it when desks change). Owner-only `data_schema` / `data_query` (`ai_data.py`): one read-only SELECT over whitelisted business tables (ai_data.TABLES with meanings), DB opened mode=ro, SQLite authorizer denies other tables, secret columns read as NULL, 200 rows, step limit. Effort medium, 20 messages of history, 12 tool rounds; history is merged into alternating turns.
- Assistant notes: owner-only `remember`/`forget` tools save lasting rules in `assistant_notes` (`_ai_notes()`, appended to the system prompt in every chat incl. WhatsApp). Tools also: `product_sales` (cog_lines by product), `get_disputes` (disputes table), `find_parcels` (shipments). WhatsApp profile (logo static/brand/ecomvital-640.jpg, about, website, email) via POST /api/whatsapp/profile (resumable upload handle).
- Assistant (Ask chat, WhatsApp later): `_assist_tool` adds store_health, tracking_live, google_campaigns / google_products / propose_google_change (confirm runs `_ads_run_batch`), schedules + alerts (`assistant_jobs`, `_assist_loop`: schedules every minute, alerts every 5) delivered to `assistant_inbox` (badge on the Ask button).
- Ads payable (Google): stores read directly use `gads.hourly_tab` (hourly spend, account clock) instead of
  the Sheet; the Sheet is the fallback.
- Business clock is Hong Kong (`CASH_TIMEZONE`). Each store has its own clock;
  "today" is the furthest-ahead store's date.

Cash flow rules learned the hard way:

- Airwallex card authorisation holds are already off the available balance; don't
  count them as pending. Some sub-accounts answer in camelCase (`_f()` reads both).
- Airwallex returns at most ~a month per search, oldest first: read in windows.
- PayPal holds: T2103 reserve (released 60 days later, T2104), T2101 monthly-limit
  hold (21 days, T2102). The schedule is fitted to PayPal's withheld balance.
- Statement: opening = position now − movements since; internal moves (reserves,
  PayPal → Airwallex, sub-account transfers) don't change the total. Sales check
  uses Shopify on the Hong Kong clock, split by gateway; completed days match to the cent.
- Google bills: match card charges by the account number in the merchant name
  (`GOOGLE*ADS5460462568`). A monthly bill on the 1st covers last month only.
  A retried charge covers spend up to its first attempt. Per-store "Google tax"
  (ZAVA = 10% GST; Google's balance includes it).

COG rules (replaced the old per-store "Other costs %", which is no longer used):

- Source of truth is the owner's orders sheet (setting `cog_sheet_id`; weekly tabs +
  APRIL; one row per item; COG in USD incl. shipping). Read via the Google sign-in.
- Every Shopify order line is costed and saved in `cog_lines`: `invoice` (its own
  sheet row), `catalog` (product's latest cost in that store, then same product
  other variant), `estimate` (store's average COG %, shown orange), `cancelled` (0).
- Store ↔ order tag (AS…, …CH) is learned from each store's Shopify order names.
- Background job: last 2 days every 20 min, full window every 6 h. Dashboard COG
  per day = saved lines + store average for any sales not yet covered.
- Bundles: suppliers discount orders with 2+ items (same or different products), so prices are
  kept with the order's item count and only 1-item orders give a product's "single" price.
  Invoices (`invoices.judge`): 1-item orders compare per item against the single price; 2+ item
  orders compare the order's charge against its items at single prices (singles − charged = bundle
  discount, never flagged; dearer than singles = flagged, one Price-changes row per order).
  Not yet invoiced 2+ item orders cost single prices less the store's usual discount for that many
  items (`Catalog.bundle_rate`, median of the last 60 such orders). `cog_lines.single_usd` lets Check
  COG show "Singles … · bundle discount …" per order.
- Item counts come from the Shopify order (`_order_items`, passed to `cog.Catalog`), not the number of sheet rows (a row can cover two). `cog.pack`: a "BOGO" / "Buy 1 Get 1" product or variant is 2 items (costing, bundle tier, invoice matching). Price changes: gap ≤ $2.50/item (`PRICE_GAP`) = likely real supplier change, bigger = likely bundle/quantity (`likely` on /api/invoices/changes). Recheck (`/api/invoices/recheck`) also re-matches "Product not found" rows.
- AI double-check (`invoice_ai.py`, Sonnet 5.5): every uploaded invoice file is read again by the AI
  in the background and compared per order to the cent plus the total. Agree → `ai_check` agreed;
  differ → `review` and its lines are left out of all costs until a person picks a reading on the
  invoice (first reading / AI's reading / ask again). Files the normal reader can't read are read by
  the AI alone (`ai_only`) when its orders add up to the printed total.
- Supplier accounts (`suppliers.py`, `/api/suppliers`, COG → Suppliers): per supplier, all stores.
  Owner sets the opening (date + USD owed that morning, `supplier_accounts`). Orders count when
  fulfilled (first `shipments.fulfilled_at`, HK clock) at their cog_lines cost; lines without a
  supplier go to the store's usual one. Payments = Expenses items in category "supplier", assigned
  to a supplier per payee or per payment (`supplier_payees`, '' = not a supplier). Invoice check:
  expected = single prices (bundles: singles less the usual discount); "matches" = no price to decide,
  nothing unmatched/billed twice/missing, AI agrees. Orders fulfilled 14+ days ago on no invoice are listed.
- Next steps planned: invoice upload (AI reading) with price-change flags and
  approval, supplier payments ledger from Airwallex payouts, write cost to Shopify
  "Cost per item" (needs read_products, read_inventory, write_inventory).

## To do later (owner decided to postpone)

- **Move Google Cloud project "My First Project" (dark-smile-511222-t3) out of the tendance-montreal.com organisation
  into EcomVital's own (ecomvital.com Workspace, admin@ecomvital.com).** Found 13 Oct 2026: the project sits under
  Tendance Montreal's Google Workspace org, whose "Domain-restricted sharing" policy
  (iam.allowedPolicyMemberDomains) blocks adding admin@ecomvital.com (or any Gmail) as owner, so EcomVital's API
  access (Basic, Standard application), OAuth keys and branding depend on the Tendance org / info@tendance-montreal.com.
  The plan:
  1. admin@ecomvital.com sets up its Cloud organisation.
  2. Migrate the project (needs project-move permissions in both orgs). The access level, branding and keys move with it.
  3. Add admin@ecomvital.com as Owner, plus Search Console owner.

  Quick alternative: a project-level exception to the Tendance org policy. Claude may not change IAM or org policy
  itself; the owner clicks.

## Invariants — do not break these

`metrics.py` is the single source of truth for every number. If a metric appears
in the UI, it is computed there and nowhere else. Never calculate a figure in
`app.js`.

These identities must hold exactly. Verify after any change to `metrics.py`:

```
sales - refunds - chargebacks - processing_fee - cogs - ad_spend == net_profit   (metrics.day)
refunds_total == refunds + chargebacks
payment_fee == paypal_fee + airwallex_fee + other_payment_fee
ad_spend == google_spend + meta_spend
processing_fee == payment_fee + shopify_fee
```

Other rules:

- **Sales = full order value on the order day; refunds count on the day they're made** (Shopify
  refunds by refund date, plus PayPal / Airwallex refunds that match no Shopify refund and all
  chargebacks, the latter two in the all-stores view as "PayPal / Airwallex (not in Shopify)").
- **Tax is excluded from revenue.** Tax was never the merchant's money. It only
  appears in the payment-fee base, because processors charge on it.
- **API spend supersedes manual spend** for the same day (`db.fetch_range`).
  This prevents double-counting when a Google developer token is approved
  mid-stream. Don't "simplify" it by summing them.
- **No attribution modelling.** This tool deliberately does not guess which
  channel drove which order. Account-level spend against total revenue is the
  honest view. If asked for per-channel ROAS, explain the tradeoff first.
- Money is stored and computed as floats in shop currency, formatted only at
  render time via `money()` in `app.js`.

## Design system

Do not restyle wholesale. The look is a cool paper ledger, deliberately not a
dark SaaS dashboard. Tokens live at the top of `styles.css`:

```
--paper #F2F1ED   --ink #14181F     --rule #D6D3CB
--profit #1F6F5C  --loss #B3402A    --spend #3A4E7A   --revenue #8A8578
```

Type: Instrument Sans for UI, IBM Plex Mono for **all numerals**. Numbers are
tabular-nums so columns align. Keep it.

The signature element is the **P&L waterfall** — bars shrink to scale as costs
are deducted, with the removed slice hatched on the end. It's the thing that
makes this app worth using. Don't replace it with a generic bar chart.

Colour semantics: cost metrics are **neutral**, never red/green. Spending less
is not automatically good. Only profit-direction metrics get colour.

## Testing your work

There is no test suite. Verify like this:

```bash
python seed_demo.py          # 120 days of fake data
python app.py                # then open 127.0.0.1:8787
```

After changing `metrics.py`, assert the four identities above against seeded
data before saying you're done.

Antigravity can drive Chrome — **use it**. Load the dashboard, check the console
for errors, and screenshot at 1280px and 390px wide. The mobile chart uses a
taller viewBox (`narrow` flag in `renderChart`); confirm it still reads.

## Gotchas

- Shopify returns only 60 days of orders unless the app has `read_all_orders`.
  ProfitDesk saves each day's totals (`shopify_days`, every 6 hours and on every
  read), so days older than 60 come from the saved copy. `/api/history` shows coverage.
- Shopify API version is quarterly (`2026-07`); Google Ads is yearly (`v25`).
  Both are in `.env`. A 404 or version error usually means bumping one.
- "New customers" derives from Shopify's lifetime order count — a proxy, not
  exact. Don't present new-customer CAC as precise.
- Orders bucket by **UTC** date, which can shift late-night orders vs Shopify's
  own reports.
- `.env` and `profitdesk.db` hold live credentials and real business data.
  Never commit them, never paste their contents into chat, never send them
  anywhere. Both are gitignored.

## Good first requests

If the owner is unsure what to ask for, these are natural next steps:

- Per-SKU COGS instead of a flat percentage
- Pull Meta ad spend automatically instead of manual entry
- A date-range picker for arbitrary custom ranges
- Export the current view to CSV
- Compare two periods side by side
