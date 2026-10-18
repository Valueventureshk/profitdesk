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
| `tracking.py` `static/tracking.*` `shopify-app/` | Apps → **Tracking** (like wetracked.io): Shopify web pixel extension (`shopify-app/extensions/profitdesk-pixel`, deployed with the Shopify CLI into each store's app) posts events to `/t/e`; last-click (7 days) UTM attribution; report of adds to cart / checkouts / purchases per campaign / ad set / ad. Watch-only per store until Meta CAPI sending is added. Pixel deployed (12 Oct 2026) into CUTEHOME + Dovaro, Vizo, Tendance, Beige Rue, Taylor Maria, Revionel apps: one `shopify-app/shopify.app.<store>.toml` per store app (made with `config pull` over a placeholder, then `shopify app deploy --config <store> --force`); then `/api/tracking/install/<id>`. Shopify's customerJourneySummary drops the gclid, so Google uploads need the pixel's click ids (`gads_conv.py`). Tracking → Google Ads panel: conversion actions per account, primary/secondary switch. ProfitDesk → Google (`_gconv_loop`, every 10 min): pixel events with a Google click (px_visitors.g_click, last Google click ≤30 days) → Google **Data Manager API** (`datamanager.googleapis.com/v1/events:ingest`, scope `auth/datamanager`, API enabled in the Cloud project; Google refuses uploadClickConversions for new integrations), one event per request, into its own actions "ProfitDesk Purchase / Add to cart / Begin checkout" (purchase value = order total − tax, orderId = order name, hashed email/phone unless the account hasn't accepted customer data terms → `ids` off); log `gconv_uploads`; per store `gconv:<id>` {enabled, mode watch|auto|primary, started_at, switched_at}; auto = switch ours primary after ≥8 days when ours ≥95% of the primary's 7-day purchases. Plan (`/api/tracking/google/plan`, Do it): TrackBee cancelled 12 Oct 2026 → Google Shopping App Purchase bridge primary (Tendance, Beige Rue, Dovaro; Revionel already) + ours auto; Vizo + Taylor Maria ours primary; ZAVA untouched. Purchases also come from Shopify's orders (18 Oct 2026, `_px_order_loop` every 3 min, `tracking.add_orders`): orders of the last 2 days, 10+ min old; any order without a pixel purchase is added as px_events id `order:<id>` (ua = how credited), credited via the same shopper's pixel events (em_hash, 7 days) → the order's custom attributes utm_*/click ids → customerJourneySummary; a late pixel purchase replaces it. EcomVital lines ("EcomVital campaign / ad set / ad / source / medium / click / matched by") are written on each Shopify order's Additional details (orderUpdate customAttributes, merged with other apps' lines; setting `px_orders:<id>` {write:false} stops it). Owner check `/api/tracking/orders-sync?store=&run=1`. CUTEHOME 18 Oct: 35/35 orders, value to the cent, 34 credited = WeTracked. Hour picker (`hour=` 0-23, one day, store clock; Meta spend via the hourly breakdown on the ad account's clock). Column order per browser (`pdTrkCols2`) |
| `google_desk.py` `static/googleads.*` | Apps → **Google Ads**: Google product report (shopping_performance_view) grouped per Shopify product with variants, vs Shopify sales / COG / fees, one store at a time, everything in the Google Ads account's currency; period picker (`start`/`end`, window "sel") plus 7/14/30 store days incl. today (same days as the dashboard), breakeven ROAS + verdicts. Summary totals come from the dashboard engine (`_store_window` → `_in_currency` → `google_desk.with_dashboard`), so they match the dashboard to the cent; product rows are Google's product report; PMax spend on no product + Search = "not on a product". `/api/googleads/check?store=` compares Google / campaigns / products / dashboard. Laid out like pythago.io: filters (min/max ROAS, ad days, min cost, status, country, tags, verdict), bulk Draft/Archive/Active in Shopify (`/api/googleads/status`, Manage), tags (`gads_tags`, `gads_tag_products`), refunds per product (`ShopifyClient.refund_lines`), country (`cog_lines.country` + feed country in the item id), Period 1 vs 2 (`/api/googleads/compare`). Speed: product details kept 6 h (`_pinfo`), `_gdesk_loop` reads each store's 30-day view every 30 min (~336 product reports/day of Explorer's 2,880) |
| `recordings.py` `static/recordings.*` `static/recorder.js` | Apps → **Session Recordings**: rrweb recorder (ScriptTag, off by default per store), chunks via app proxy `/apps/track/rec` → `<DB dir>/recordings/`, replay with rrweb-player |
| `disputes.py` `static/disputes.*` | **Disputes desk**: PayPal cases + Airwallex chargebacks in one shape, order evidence pack (PDF/PNG, bundled Noto Sans in `assets/`), AI draft (Sonnet), actions |
| `suppliers.py` | **Supplier accounts**: opening + fulfilled orders' COG − Airwallex payments = owed; invoice expected vs invoiced (COG page → Suppliers) |
| `invoices.py` `invoice_ai.py` | Supplier invoices: read (PDF/Excel), match to orders, price + bundle check; AI second reading |
| `static/cog.html` `cog.js` | COG + Products Monitor page (Check orders, Invoices, Products, Suppliers) |
| `cog_desk.py` `static/cogdesk.*` | COG → **Check orders** (`/api/cog/orders`, USD): one row per order, items each with its price (single prices + “bundle discount” line), sale, COG, COG % (orange ≥40%), Paid/Unpaid (its invoices' paid marks), Invoiced / Not invoiced yet / Not fulfilled / Cancelled, and where the cost comes from (`cog_desk.order_kind`: up/down = price changed & undecided, review, real, kept, bundle, note, new, same, inv, est). Click an invoiced order → compare (`/api/cog/compare`: this order's invoice vs the last earlier invoice with the product, same store) and answer (`/api/invoices/orders/answer`): bundle (invoice_lines.check_state 'bundle' = decided, not a price change), real (`_decide_prices use_new`), review (checking + optional post to the supplier's Slack invoice channel, `_review_channel`), note; answers + order notes in `cog_order_marks`, each kept as an `agent_lessons` row (sources answer:/ordernote:). ✨ AI check (`/api/invoices/orders/ai`, Sonnet, ≤40 orders): verdict, why, fix; changes nothing until applied. **Invoices by date** (`/api/suppliers/by-day`): invoices (replaced ones left out) per date, X/Y paid, flags, Send to supplier (`/api/suppliers/invoice-review`), notes per day (`invoice_day_notes`, lesson daynote:), paid marks `invoices.paid/paid_by/paid_at` saved with `/api/suppliers/marks` (Manage); cut-off setting `invoice_paid_cutoff` (default 2026-09-01: invoices dated before it count as paid). On credit = unpaid invoices; Estimated = orders not invoiced since the cut-off (shipped ones, plus — since 17 Oct 2026 — orders placed in the last `PLACED_DAYS` (30) not shipped yet, counted from the day placed so a new order's COG shows at once; COG job every 5 min clears `_payable_cache`, which otherwise keeps 60 s) and newer than the supplier's newest invoiced order for that store (`cog_desk.billed_up_to` / `covered_by`: suppliers bill in order sequence, so older orders on no uploaded invoice were billed on a file that never reached the app, e.g. Graypop CUTEHOME 12 Aug–13 Sep, 16–28 Sep 2026; Check orders shows them “Billed, no invoice file”, paid as the covering invoice); Cross-fulfilment (`cog_desk.supplier_shares` / `set_suppliers`, also in COG → Suppliers): an order on an invoice belongs to the supplier who invoiced it (Graypop ships a Tendance order Dayone can't source); a supplier billing under 10% of a store's invoiced orders is “minor” there, so that store's not-yet-invoiced orders go to its main supplier. Invoices tab shows each invoice's order range (an invoice dated 16 Oct bills orders placed up to the 15th). total = Cash flow's “Suppliers payable” (`_supplier_payable`, 5-min cache, `/api/cash` → `suppliers_payable`). |
| `products_desk.py` | COG → **Products** table (`/api/cog/pdesk`): one row per Shopify product per store (variants together; lines without product_id join by title): image / listed date / status from Shopify (`PD_PRODUCTS`, 6 h cache), units + orders + last order from cog_lines (BOGO ×2), latest confirmed single cost + price history (`cost_history`, Catalog tier-1 entries), size chart yes/no (`_sc_match` with `_sc_rules`), CS tickets (support/legal) matched by ticket order numbers else the customer's latest order (shipments email), title words pick the product in multi-product orders. Team edits: `pd_info` (supplier, material), `pd_notes` (complaint / note), `pd_qc` photos in `<DB dir>/qc/` (JPEG + _t thumb). Work level can edit. Period picker (`start`/`end` store days, default all time): only products sold then, units/orders for that period; the open table reloads quietly every minute. |
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
| `tasks.py` `static/tasks.*` | **Tasks** desk (desk `tasks`, `/tasks`): Trello-like board. Admins (Manage level / owners) create tasks: "listing" (store, competitor Meta ad links, their landing page / advertorial links, their product / collection links, a To do pick list `tasks.TODO` (advertorial, collection, product pages, branded page, creatives, ad copy) plus custom to-do lines) or "custom"; Special instructions (text + voice notes / screen recordings made in the browser, files kind "instruction"; while recording, Stop/Pause sit in a Document Picture-in-Picture window that stays on top of every tab (Chrome/Edge; page bar elsewhere) and the tab title shows the timer; each recording is previewed before it is used) always shown first to staff. Staff (Work level) see only their own: Start, hand back the main landing page link (required; the ads go there) and one answer per to-do item, or a "Can't do this" note (`result.cant`); custom items answer in `result.custom`; `tasks.missing` is the one submit check (page + WhatsApp Done), upload creatives (images/videos ≤500 MB) + other files to `<DB dir>/task_files/<task>/`, Submit; locked while submitted/approved ("Take back to edit"). Flow todo → doing → submitted → changes / approved ("Waiting to launch") → launched / done; launched/done/cancelled = History. Launch (greyed out since 15 Oct 2026 while `tasks.LAUNCH_READY` is False: the owner will send their real tested strategies to replace X/Y/Z; until then admins use “Mark launched (set up by hand)”): admins tick approved listing cards (same store) → "Launch selected", or one task's Launch button; pick a strategy (`tasks.STRATEGIES`, starter set X one ad set / Y ad set per landing page ABO / Z CBO; owner to supply their tested ones). `/api/tasks/launch` makes one campaign, ad sets per strategy, one ad per creative pointing at its task's landing page with that task's copy, all PAUSED; ids saved in `task_launches` as made, so pressing again carries on (videos wait for Meta's thumbnail). Needs ads_management on the System User token and the Page assigned; last choices per store in setting `task_launch:<id>`. Not yet tested against a live ad account. **Queue** page (admins only, `data-manage-only`): one column per person with their open tasks still with them (to do / in progress / changes) in order (`tasks.queue_pos`, the board's order too), load bar, late count, ▲▼ / drag to reorder, drag or “Give to” to move (logged, WhatsApp like a new task), High on/off: `POST /api/tasks/queue` {person, order, priority}. Every step/comment/upload is a `task_log` row: End of day (`/api/tasks/day`, HK day) per person. WhatsApp (phase 2, built 15 Oct 2026, not yet tried on the live number): staff numbers = Settings → WhatsApp people (`wa_people`, optional `tasks_only`: no assistant). Giving a task (create, or edit to a new person) sends it (`_task_wa_send`): inside 24 h a text with Doing/Done reply buttons, else template `ecomvital_task_new` (falls back to `profitdesk_update` heads-up until approved). Admin **Follow up on WhatsApp** → `ecomvital_task_followup`. Sent ids in `task_wa`; replies (`_task_wa_reply`, before the assistant in `_wa_handle`): button payload `task:<id>:doing|done`, or words doing/done (+ `#id`), task = replied-to message, else only/last-messaged/latest open; done on a listing without links asks for them; free text replying to a task message (or the first message within 24 h after a Follow up) → task comment `(WhatsApp) …` + `_assist_deliver` to whoever asked. Templates: Settings → WhatsApp → Task message templates (`/api/whatsapp/task-templates`). |
| `meta_manage.py` `meta_rules.py` `meta_brain.py` `static/metaads.*` | Apps → **Meta Ads** (desk `metaads`, `/metaads`, built 15 Oct 2026, first store CUTEHOME = store 6): **Overview** per store: campaigns / ad sets / ads (status, daily budget, spend, impressions, link clicks, CTR, CPC, ATC, Meta purchases / value / ROAS) next to EcomVital's pixel purchases / value / ROAS (Tracking, matched by UTM names), last 1/3/7/14/30 days, account currency, 10-min cache. Manage-level people can Pause / Switch on and edit daily budgets right there (logged, Undo). **Manage**: per store `meta_manage:<id>` {enabled, budget_cap}; mode is **Ask me first only** (Autopilot not offered yet). Rules (`mads_rules`: no_sale, roas_floor, scale_budget; basis meta|pixel; saved OFF with starter numbers; owner to send their real numbers) run hourly in `_mads_loop` when managing is on → proposals (`mads_actions`, one batch per rule run; the same change isn't proposed again while waiting or within 24 h) wait for **Do it** / Reject. Every change is validate-only first, then real, `before` saved for Undo; budgets never above the cap; budget changes refused for ad sets whose budget is on the campaign. Ask Claude (`meta_brain`, Sonnet 5.5, `mads_chat`): reads results, proposes plans (status/budget steps) and rules, never changes Meta itself. Owner check of a store's token: `/api/meta/check-manage?store=`. Strategy **“$10 up every purchase”** (Autopilot, its own switch, setting `meta_up:<id>` {on, step, cap, started_at}): `_up_loop` every minute reads the pixel's `checkout_completed` events since it was switched on, each order once (`mads_boosts`), matches `a_campaign` (utm_campaign) to a Meta campaign by name or id, and raises that campaign's daily budget by `step` (validate-only first, logged in `mads_actions` source `strategy:up`, Undo), never above `cap`; skips campaigns whose budget is on the ad sets. Red “Turn off autopilot” card on top of the Meta Ads page. |
| `agent.py` `slack_client.py` `static/agent.*` | Apps → **Agent** (desk `agent`, `/agent`, phase 1 built 15 Oct 2026): the ops agent. Works through the owner's **NF Ecom Slack** (suppliers, supply chain, customer support and EOD all happen there; free plan = only 90 days kept, so it saves its own copy in `slack_messages`). Slack app "EcomVital Agent" made from the manifest the Slack tab shows (`slack_client.manifest`), bot token + signing secret in settings `slack_bot_token` / `slack_signing_secret`; events signed at `/api/slack/events` (open path). Channels (`slack_channels`) get a role (supplier_invoice / supply_chain / customer_support / eod / other) and a supplier, guessed from names like gray-supplier-invoice. Jobs → steps (`agent_jobs`, `agent_steps`) are the work log; Advise on any step (`agent_advice`) becomes a lesson (`agent_lessons`, per area); when unsure it asks (`agent_questions`), answers become lessons. Supplier-invoice files (90-day backfill + live) go through `_ingest_invoice` (the same as COG → Invoices upload: match, re-cost so estimates become invoiced costs, AI double-check holds disagreements), once per file (`agent_files`). 16 Oct 2026: the AI (`agent.classify`, Haiku, reads text or images, follows invoice/supplier lessons) sorts each file: invoice / payment (bank transfer notice → kept in `<DB dir>/agent_proofs/`, row in `agent_payments` with amount/date/ref, matched to Airwallex/PayPal supplier payments ±1%/±5 days by `_agent_match_proofs`) / tracking / other; DayOne's "<store> <date> Tracking.xlsx" skipped by name. Invoices for a store that isn't connected wait (not recorded) and ask a Yes/No store question (`agent_questions.kind='store'`, data JSON); owner's answer in setting `agent_stores` {norm name: include|exclude}, include re-reads the channels, exclude can remove already-filed ones (`_agent_undo`). Suppliers tab (`/api/agent/suppliers`, `_agent_positions`): COG → Suppliers ledger + invoices oldest first, payments applied after the opening (FIFO) → paid up to invoice/order, part paid, invoiced up to per store, on credit = unpaid invoiced + shipped not invoiced, transfer notices with bank match. Ask the agent (`agent.brain`, Sonnet 5.5, tools supplier_positions / find_invoices / search_slack / check_transfer_notices / ask_team, all lessons in its prompt, `agent_chat`). Pause (`agent_paused`), owner Undo of wrong filings. Next phases: SOPs + 500 emails + questions/quizzes (owner writes quizzes later), supplier conversations, customer replies, Slack thread advice / approve buttons. |
| `timetrack.py` `desktop/time_app.py` `static/time.*` `.github/workflows/time-app.yml` | Apps → **Time** (desk `time`, `/time`, built 16 Oct 2026, like Hubstaff): desktop app **EcomVital Time** (Python + tkinter, mss screenshots, pynput input counts, front app/window via Win32+psutil / AppKit+Quartz) for Windows + Mac (Apple, Intel), built by GitHub Actions (PyInstaller) into `static/downloads/` (`EcomVital-Time-windows.exe`, `-mac-apple.zip`, `-mac-intel.zip`, `version.txt`; the workflow commits them). Staff sign in with their EcomVital login → device token (`time_devices`, hashed, revocable on Computers tab). Client calls `/api/time/client/*` (open path, Bearer `evt_…`): start/stop (`time_sessions`), minutes every minute (`time_minutes`: active + key/click/move counts, app, title, task), one screenshot at a random moment per 10-min block (`time_shots`, `<DB dir>/time_shots/<user>/<day>/`, JPEG ≤2400 px, ~100 KB), discard idle (person answers "remove" after ≥ idle_minutes away). Offline queue on the computer. Desk: Team week (hours, activity %), Day (10-min blocks coloured by activity with screenshots, apps & sites via `timetrack.app_group`), Get the app (downloads + Mac permission steps), Computers + owner settings (`time_settings`: keep_days 30, idle_minutes 5, staff_delete_minutes 10). Staff (Work) see only their own; Manage sees all. `_time_loop` deletes screenshots after keep_days. Mac builds are unsigned (right-click → Open; Screen Recording + Input Monitoring permissions). |
| `paypal_track.py` `static/paypaltrack.*` | Apps → **PayPal Tracking** (desk `paypaltrack`, `/paypal-tracking`, built 16 Oct 2026, **off by default**): sends SCM tracking numbers to the PayPal payment of the same order (`POST /v1/shipping/trackers-batch`, one tracker per request, status SHIPPED, notify_buyer false). Only parcels whose SCM status is `in_transit` (setting `later` also allows pickup / out_for_delivery / delivered; never info received / pending / no tracking), fulfilled in the last 60 days, of orders paid through PayPal (Shopify transaction gateway contains "paypal"). Exact match only: Shopify payment id = PayPal invoice_id, or Shopify authorizationCode = PayPal transaction_id, in a connected PayPal account's feed (`order_fees` first, else Shopify `nodes` query, kept in `pp_track_orders`, re-asked after 6 h); no exact id = "Not matched", never sent. Carrier mapped from the SCM carrier (`paypal_track.CARRIERS`), else OTHER + name; a refused carrier is resent as OTHER. Every parcel's row in `pp_track` (waiting / unmatched / ready / sent / error / failed); each (transaction, tracking number) sent once; errors retried 3× then "gave up" with Try again. `paypal_track.loop` every 15 min keeps rows current (Ready to send = the dry run while off) and sends only while setting `pp_track` {on, stores, later} is on, for the ticked stores. Preview (dry) and Run now buttons (Manage). Last run in setting `pp_track_last`. |
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
  setting `tickets_from` (default 8 Oct 2026 HK). Store picker for staff: `/api/inbox/mailboxes` (Inbox desk, id/store/address only). Team can override: close, reopen,
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
  card transaction, skips moves between own accounts (incl. PayPal withdrawals in transit, PayPal ↔ PayPal).
  Airwallex transfers to an account not connected here are asked, not guessed: "Is this your own account?"
  panel (`to_confirm`, not sent to the AI): **Ours, internal move** saves rule `ignore` for that payee;
  **Not ours, real payment** saves the category (default supplier) and, for supplier, the supplier
  (`/api/suppliers/payee`), so it counts in COG → Suppliers payments. Until answered they count as money out.
  Airwallex gives no payee name for these (restricted keys can't read transfers; 404 / not allowed), only the
  reference typed when sending ("own" on the owner's own moves). With a reference the answer is kept per
  reference (`awx-transfer:<reference>`); with none (`expenses.NO_REF`, e.g. DayOne payments) it is asked per
  payment (`tx:<id>`), except payments up to the owner's earlier answer for all no-reference transfers
  (`expense_rules.set_at` of NO_REF, `expenses.rule_map`). Unknown payees go to "To sort";
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
  Panda import (`/api/sizecharts/import`): Panda's export has no product links, so each chart is linked to every product with its title (exact, then `sizecharts.loose_title`); `dry` previews; importing again updates charts by name. ZAVA: 723 charts imported 18 Oct 2026, 693 linked to 719 products, 30 drafts (templates like "Code 96", renamed products). Storefront handles keep ™ etc. (`_sc_handle`). ZAVA's app can't add script tags: the size chart is the app embed, switched on in the theme editor.
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

- Arrived today (18 Oct 2026, `cashflow.track_arrivals`, setting `cash_arrivals`): pending items due by tomorrow are
  remembered; once one leaves the pending list of an account that answered (and the transit check worked), it has
  settled into the available balance → "Arrived" (green) on today's row and "Incl. … arrived today" on Available now.
  `_arrivals_loop` looks every 30 min so it shows even when nobody had the page open.
- Airwallex card authorisation holds are already off the available balance; don't
  count them as pending. Some sub-accounts answer in camelCase (`_f()` reads both).
- Airwallex returns at most ~a month per search, oldest first: read in windows.
- PayPal holds: T2103 reserve (released 60 days later, T2104), T2101 monthly-limit
  hold (21 days, T2102). The schedule is fitted to PayPal's withheld balance.
- Statement: opening = position now − movements since; internal moves (reserves,
  PayPal → Airwallex, PayPal → PayPal, sub-account transfers) don't change the total.
- Own-account moves (16 Oct 2026, `statement.pair`): a PayPal withdrawal (T04) stays **available while in
  transit**: no Airwallex deposit yet and ≤ `statement.TRANSIT_DAYS` (7) old, or its deposit still PENDING
  (`statement.in_transit` → `cashflow.summarize(transit=)`, `summary.in_transit`, note on the Available card;
  `app._in_transit` reads Airwallex deposits since the oldest recent withdrawal and drops the paired pending
  deposit). Paired withdrawal + deposit both count 0; only PayPal's fee (and an Airwallex deposit fee) is money
  out. Not in Airwallex after 7 days = withdrawn to a bank not connected here (money out, Expenses "To sort").
  A withdrawal the bank sent back (T1101/T1104) pairs with it, both 0. PayPal → PayPal between connected
  accounts (T00 out in one, same amount in within a day in another) = "Between your PayPal accounts", 0, not
  a sale, not an expense. Airwallex transfers with no partner in a connected account = **money out** (Money
  out "Sent to an Airwallex account not connected here", e.g. DayOne's own Airwallex), unless the owner
  answered "ours" on the Expenses desk (rule `ignore` on its `awx-transfer:` key → `_own_payees()` → Moved
  "To your own Airwallex account"). Sales check
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
- Background job: last 2 days every 5 min, full window every 6 h. Dashboard COG
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

- **BOGO billing (asked 14 Oct 2026, owner checking with the team):** does Graypop bill one Orthora BOGO sale (2 pairs) as 2 rows (one per pair, e.g. #16261CH 2 × US$13) or 1 row for both (8 Oct invoice: one row US$16.30)? The answer decides whether a one-row BOGO invoice is half-invoiced (cog.pack, products desk 'per BOGO sale'). Until then the Products desk shows the latest costed BOGO sale as it is.

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
refunds_total == refunds + chargebacks          (chargebacks = lost disputes, see below)
payment_fee == paypal_fee + airwallex_fee + other_payment_fee
ad_spend == google_spend + meta_spend
processing_fee == payment_fee + shopify_fee
```

Other rules:

- **Disputes count only once lost** (owner, 16 Oct 2026; `_lost_disputes`, field `chargebacks`, shown as “Lost disputes” inside Refunds): Airwallex cases closed lost or accepted, on the day they closed (from the `disputes` table, refreshed by `_outside_loop`), less what that order already refunded in Shopify, skipped when an outside refund of the same amount within 5 days already counts; PayPal reversals (T1106, `PP_LOST`); PayPal chargebacks only from PayPal's Disputes API (feature not yet ticked). Open cases, holds, won cases and dispute debits on open count nothing. Partial refunds count for what was refunded.
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
