# ProfitDesk — agent brief

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
| `tracking.py` `static/tracking.*` `shopify-app/` | Apps → **Tracking** (like wetracked.io): Shopify web pixel extension (`shopify-app/extensions/profitdesk-pixel`, deployed with the Shopify CLI into each store's app) posts events to `/t/e`; last-click (7 days) UTM attribution; report of adds to cart / checkouts / purchases per campaign / ad set / ad. Watch-only per store until Meta CAPI sending is added. Hour picker (`hour=` 0-23, one day, store clock; Meta spend via the hourly breakdown on the ad account's clock). Column order per browser (`pdTrkCols2`) |
| `google_desk.py` `static/googleads.*` | Apps → **Google Ads**: Google product report (shopping_performance_view) grouped per Shopify product with variants, vs Shopify sales / COG / fees, 7/14/30 days, breakeven ROAS + verdicts |
| `recordings.py` `static/recordings.*` `static/recorder.js` | Apps → **Session Recordings**: rrweb recorder (ScriptTag, off by default per store), chunks via app proxy `/apps/track/rec` → `<DB dir>/recordings/`, replay with rrweb-player |
| `disputes.py` `static/disputes.*` | **Disputes desk**: PayPal cases + Airwallex chargebacks in one shape, order evidence pack (PDF/PNG, bundled Noto Sans in `assets/`), AI draft (Sonnet), actions |
| `suppliers.py` | **Supplier accounts**: opening + fulfilled orders' COG − Airwallex payments = owed; invoice expected vs invoiced (COG page → Suppliers) |
| `invoices.py` `invoice_ai.py` | Supplier invoices: read (PDF/Excel), match to orders, price + bundle check; AI second reading |
| `static/cog.html` `cog.js` | COG + Products Monitor page (Check COG, Products) |
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
| `static/cash.html` `cash.js` `nav.js` | Cash flow page, section menu |
| `static/index.html` `styles.css` `app.js` | The dashboard |
| `seed_demo.py` | Generates fake data for testing |
| `start.command` / `start.bat` | Double-click launchers |

## Current setup (keep this section up to date)

- Live at https://profitdesk-production.up.railway.app (Railway project
  "sparkling-beauty", service "profitdesk"). Every push to `main` deploys.
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
  Parcel Panel links at /apps/track. App proxy must be in each app version (Amber
  Ashton's app is under another Shopify login, not done yet).
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
  (dark-smile-511222-t3) owned by info@tendance-montreal.com: it has Google Ads API **Explorer**
  access (2,880 ops/day on real accounts; Basic needs brand verification + an owned domain).
  APIs on: Google Ads, Sheets, Gmail. Consent screen "ProfitDesk", published (unverified warning).
  Google login connected in ProfitDesk = shop.valueventures@gmail.com (sees the ad accounts).
  Per store `google_source`: NULL = direct API (hourly, shifted to the store clock), "sheet" = the
  script's Sheet. `/api/google/compare` checks both. ZAVA + Revionel stay on the Sheet until
  shop.valueventures@gmail.com gets access to those ad accounts.
- Speed: SQLite in WAL mode with a 10 s busy timeout (`db._conn`, `db._wal`; restore checkpoints and drops
  the side files). GZip on every response; /static files linked with ?v=<hash> are cached for a year.
  Heavy work (COG costing, re-costing, Google desk maths) runs in threads (`asyncio.to_thread`) so pages
  never queue behind it. A watchdog notes any stall over 0.6 s with the ProfitDesk code that caused it:
  owner route `/api/debug/stalls`, counts in `/healthz` (`stalls_10min`). New slow code: check there first.
- Dashboard: answers with its last figures and refreshes in the background (`_dash`, "refreshing");
  completed Shopify days and refunds come from `shopify_days` / `refund_days` (saved every 6 h and on
  read), only the last `LIVE_DAYS` live; outside-Shopify refunds + chargebacks come from `outside_days`
  (`_outside_loop`: last 30 days every 15 min, 190 days every 6 h). Step timings: `/api/debug/timing`.
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
