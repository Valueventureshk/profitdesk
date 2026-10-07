# ProfitDesk

Real ROAS and real net profit, across every Shopify store you run.

Connect a store, connect Google Ads, set one cost percentage. ProfitDesk reads
your sales and your ad spend live and shows what is actually left over. Connect
several stores and the blended view adds them all up.

Everything runs on your own machine. The only thing saved is your connections
and your cost percentage — no sales figures are stored anywhere.

---

## Quick start

Double-click **start.command** (Mac) or **start.bat** (Windows).

Or from a terminal:

```bash
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Then open <http://127.0.0.1:8787>.

### Look around first

```bash
python seed_demo.py
```

Five pretend stores with invented figures, so you can see the whole dashboard
before connecting anything real. Remove them with `python seed_demo.py --wipe`.

---

## What it shows

Six numbers, and nothing else.

| Card | What it is |
|---|---|
| Total Sales | What Shopify reports as total sales |
| Ad Spend | Google Ads + Meta Ads, whole account, with the split shown underneath |
| ROAS | Total sales ÷ ad spend |
| Net Profit | Total sales − other costs − ad spend |
| Net Margin | Net profit ÷ total sales |
| Orders | Order count |

Every card compares against the previous period of the same length, so a
30-day view is measured against the 30 days before it.

The maths, in full:

```
  Total sales
− Other costs      your cost % × total sales
− Ad spend         Google Ads + Meta Ads, account level
= Net profit
```

That is the entire model. There is no COGS table, no per-SKU costs, no payment
fee breakdown, no attribution. One percentage covers everything that is not
advertising.

---

## Adding more stores (one small app per store)

Shopify only lets a privately distributed app install on **one** store, so each
store gets its own ProfitDesk app. Settings → **Add a Shopify store** takes the
store address, that app's **Client ID**, **Client secret** and **Install link**.
Click **Connect store**, approve in Shopify, and ProfitDesk finishes by itself.
The steps for making the app are under **How do I get these?** on that screen.

Then pick the store's Meta and Google ad accounts under **Your stores**.

If the store's Meta ad account belongs to a **different Meta business**, make a
ProfitDesk system user and token in that business (same steps as below) and
paste it under Settings → Meta Ads → **Add another Meta business**. Each store's
dropdown lists ad accounts from every connected business.

## Connecting a Shopify store

Type your store address and click **Connect Shopify store**. Shopify shows its
own permission screen asking for view access to your orders. Approve it and the
store appears in the sidebar. Nothing in your store is changed — the app can
only read.

This needs a Shopify app to exist first, which is a one-time setup:

1. Go to [partners.shopify.com](https://partners.shopify.com) → **Apps** → **Create app**
2. In **App setup**, add this exact redirect URL:
   `http://127.0.0.1:8787/auth/shopify/callback`
3. Copy the **Client ID** and **Client secret** into `.env`
4. Restart ProfitDesk

To install on stores you do not own, the app needs distribution set up in the
Partner dashboard.

### Or connect with a token

If you would rather not make a Partner app, open **Connect with an access token
instead**. In the store's own admin, go to **Settings → Apps and sales channels
→ Develop apps**, create an app with the `read_orders` scope, install it, and
paste the token. This works per store and needs no Partner account.

> Shopify only returns 60 days of orders unless the app has `read_all_orders`.

---

## Connecting Google Ads

**Settings → Sign in with Google.** One sign-in covers every store. Then pick an
ad account for each store from the dropdown.

An ad account can only be attached to one store. Once it is taken, it is greyed
out for the others.

Only account-level spend is read — no clicks, no impressions, no conversions,
no campaign breakdown. Spend against sales is the honest comparison; anything
finer would be guessing which channel drove which order.

Setting up the Google side is two independent jobs:

**OAuth client (5 minutes).** [Google Cloud Console](https://console.cloud.google.com)
→ enable the **Google Ads API** → **Credentials → Create credentials → OAuth
client ID → Web application**. Add the redirect URI
`http://127.0.0.1:8787/auth/google/callback`, then copy the client ID and secret
into `.env`.

**Developer token (1–3 business days).** From a **Manager (MCC)** account →
**Tools → API Center**. New tokens only reach test accounts; apply for **Basic
access** to read live ones. Add it to `.env` as `GOOGLE_DEVELOPER_TOKEN`.

Until the developer token is approved, stores still show sales — ad spend just
sits at zero.

---

## Connecting Meta Ads (Facebook + Instagram)

**Settings → Meta Ads → paste a System User access token → Connect Meta Ads.**
One token covers every store. Then pick a Meta ad account for each store.

Only account-level spend is read. Meta's own "purchases" and "ROAS" are
ignored — those are Meta's attribution guesses. ProfitDesk compares spend
against real Shopify sales instead.

Getting the token (about 15 minutes, no waiting for approval):

1. **Create a Meta app.** [developers.facebook.com](https://developers.facebook.com)
   → **My Apps → Create app**. Pick the use case **Create & manage ads with
   Marketing API**, and connect it to the business portfolio that owns your ad
   accounts.
2. **Make a System User.** [business.facebook.com/settings](https://business.facebook.com/settings)
   → **Users → System users → Add**. Name it `ProfitDesk`, role **Employee**.
3. **Give it your ad accounts.** On that system user → **Assign assets →
   Ad accounts** → tick each account → permission **View performance**.
4. **Give it the app.** Same screen → **Assign assets → Apps** → your new app →
   **Develop app** (or **Test app**).
5. **Generate the token.** **Generate token** → pick your app → expiry
   **Never** → tick **ads_read** → **Generate**. Copy it straight away; Meta
   only shows it once.
6. Paste it into ProfitDesk.

For your own business's ad accounts, the app does not need Meta's App Review.

---

## Cost percentage

**Settings → your store → Other costs.** One number per store, applied to total
sales. Put everything that is not advertising into it: product cost, shipping,
payment fees, packaging, overhead.

If you are unsure, take last month's profit and loss, add up every cost except
advertising, and divide by total sales.

The figures are only as honest as this number.

---

## Blended view

Connect two or more stores and **All stores** appears at the top of the sidebar.
It adds the sales, adds the spend, and works out ROAS and profit across the lot,
with a store-by-store table underneath.

Each store's own cost percentage is applied before anything is added together,
so stores with different margins stay correct.

Stores in different currencies are added up as if they were the same. A warning
appears when that happens.

---

## Store groups

Settings → **Store groups** lets you make named sets of stores, e.g. *Meta
stores*, *Google stores*, *Multiple channel stores*. Each group appears under
**All stores** (sidebar on a computer, the green dropdown on a phone) and shows
its stores blended together, with the store-by-store table. Quick buttons tick
every store with Meta, with Google, or with both linked. A store can be in
several groups; deleting a group never touches the stores.

## One currency for everything

The currency picker at the top of the dashboard (next to the date range) sets
the currency every figure is shown in, for the blended view and each store.
ProfitDesk remembers your choice.

Each store's sales convert from the store's currency, and each ad account's
spend from that ad account's own currency, at the latest **daily** reference
rate: European Central Bank, with open.er-api.com for currencies the ECB doesn't
cover. The rate used is shown under the warnings. If neither service can be
reached, the last saved rates are used and a note says so.

## Whose "today"

Dates always follow the **stores' clocks**, never your computer's. Picking a
date shows each store's own version of that date, as its Shopify reports would.
"Today" is the date of whichever connected store is furthest ahead, so with an
Australian store connected, "Today" turns over when Australia's day does. Stores
that haven't reached that date yet show nothing until their day begins.
"Today vs yesterday" compares each store with the same share of its own
previous day. On a single store's page, "Today" is that store's own today.

Ad accounts on a different clock from their store (e.g. a Berlin-time ad
account for a Melbourne store) are read hour by hour and moved onto the
store's days, so spend and sales cover the same 24 hours.

## Live, not synced

There is no sync button and no CSV. Every time the dashboard loads it asks
Shopify and Google for the figures directly. Answers are held for 60 seconds so
that clicking around does not hammer the APIs; **Refresh** ignores that and
fetches again.

Long date ranges across many stores take a few seconds, because Shopify pages
through orders.

---

## Files

| File | Role |
|---|---|
| `app.py` | Server, routes, live fetching |
| `metrics.py` | The profit engine — one definition per number |
| `db.py` | SQLite: stores, tokens, cost %, ad account links |
| `shopify_client.py` | App install flow and daily sales |
| `google_ads_client.py` | Google sign-in and account-level spend |
| `meta_ads_client.py` | Meta token check, ad account list and account-level spend |
| `demo.py` / `seed_demo.py` | Pretend stores for looking around |
| `static/` | The dashboard |
| `profitdesk.db` | Your connections. **Back this up.** |

---

## Troubleshooting

**"Shopify rejected the credentials"** — the token was truncated, or the app
lacks `read_orders`.

**"Google Ads refused the request"** — the developer token is still limited to
test accounts, or that Google login has no access to the ad account.

**Ad spend shows zero** — no ad account is linked, or no developer token yet.
The warning strip at the top of the dashboard says which.

**"Meta did not accept that access token"** — part of the token was missed
when copying, or it was revoked or the system user was removed. Generate a new one (step 5 above).

**"Meta refused access"** — the system user has not been given that ad
account (step 3), or the token is missing `ads_read`.

**Meta version errors** — bump `META_API_VERSION` in `.env`.

**Google Ads version errors** — Google sunsets versions yearly. Bump
`GOOGLE_ADS_API_VERSION` in `.env`.

**Shopify 404** — bump `SHOPIFY_API_VERSION`. Shopify ships quarterly.

**Port already in use** — change `PORT` in `.env` and update both redirect URLs
to match.

`.env` and `profitdesk.db` hold live credentials. Keep both off shared drives
and out of version control.
