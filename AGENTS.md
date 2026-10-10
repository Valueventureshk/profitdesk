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
| `static/cog.html` `cog.js` | COG + Products Monitor page (Check COG, Products) |
| `mail_client.py` | Store support mailboxes over IMAP (app password): Inbox + Sent, read-only |
| `ai_brain.py` | Claude Haiku 5.5 sorts each email (customer/inquiry/legal/other, SCM/CS, thread) |
| `tickets.py` | Turns sorted emails into tickets; our Gmail replies set "we replied last" |
| `static/inbox.html` `inbox.js` | Inbox desk: mail views, SCM/CS tickets, records, AI training |
| `ai_coach.py` | Learns a playbook from past replies + SOPs; drafts replies (Claude Sonnet 5.5) |
| `reports.py` `static/reports.*` | Reports desk: CSV downloads built from the desks' own figures (no AI) |
| `ai_chat.py` `static/chat.js` | Chat button on every desk: Sonnet 5.5 calls the app's tools, never invents figures |
| `expenses.py` `static/expenses.*` | Expenses desk: actual payments out of Airwallex + PayPal by category; owner's picks in `expense_rules`, AI suggestions (Haiku) |
| `track17.py` `static/scm.*` | SCM desk: every order's parcels (Shopify) + where they are (17TRACK API, webhook `/api/scm/webhook`) |
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
- People and access (`users.role`, `users.desks`): owner (everything, incl. Settings,
  connections, people, backups), write (work in their desks: upload invoices,
  approve prices, refresh COG), read (view only). Desks: profit, cash, cog. Enforced in
  the login middleware (`_denied`, `_DESK_PATHS`, `_WRITE_OK`); pages hide
  `data-owner-only` / `data-write-only` elements to match. New people get a temporary
  password and must choose their own at first login (`/change-password`).
- Inbox: mailboxes are read every 2 min (Railway Hobby blocks outgoing SMTP, so replies
  are made in Gmail for now and picked up from the Sent folder). AI key = setting
  `anthropic_api_key` (owner pastes it in Settings → AI). Tickets only for emails from
  setting `tickets_from` (default 8 Oct 2026 HK). Team can override: close, reopen,
  snooze, escalate, relabel, "not a customer email".
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
  Store permissions: shopify_client.SCOPES (27, see SHOPIFY-APP-RELEASE.md), check
  /api/shopify/permissions; opening the app from Shopify admin asks for missing ones.
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
- Next steps planned: invoice upload (AI reading) with price-change flags and
  approval, supplier payments ledger from Airwallex payouts, write cost to Shopify
  "Cost per item" (needs read_products, read_inventory, write_inventory).

## Invariants — do not break these

`metrics.py` is the single source of truth for every number. If a metric appears
in the UI, it is computed there and nowhere else. Never calculate a figure in
`app.js`.

These identities must hold exactly. Verify after any change to `metrics.py`:

```
sales - processing_fee - cogs - ad_spend == net_profit      (metrics.day)
ad_spend == google_spend + meta_spend
processing_fee == payment_fee + shopify_fee
```

Other rules:

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
