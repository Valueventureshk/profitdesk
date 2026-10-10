# Releasing the ProfitDesk app version in every store

Do this once for each of the 12 "Profitdesk x <store>" apps. About 3 minutes per store.

## Every box in the new version

| Box | What to put |
|---|---|
| App name | Leave it ("Profitdesk x <store>") |
| App URL | `https://profitdesk-production.up.railway.app` |
| Embed app in Shopify admin | **Off** (unticked) |
| Preferences URL | Leave empty |
| Webhooks API version | `2026-07` |
| Access scopes | The line below |
| Optional scopes | Leave empty |
| Use legacy install flow | Leave it as it was |
| Redirect URLs | `https://profitdesk-production.up.railway.app/auth/shopify/callback` |
| App proxy: Subpath prefix | `apps` |
| App proxy: Subpath | `track` |
| App proxy: Proxy URL | `https://profitdesk-production.up.railway.app/proxy/track` |
| POS / anything else | Leave it as it was |

## Permissions (scopes): paste this whole line

```
read_orders,read_all_orders,write_orders,write_fulfillments,write_merchant_managed_fulfillment_orders,write_third_party_fulfillment_orders,write_assigned_fulfillment_orders,write_products,write_inventory,read_locations,write_publications,write_online_store_pages,write_online_store_navigation,write_content,write_themes,write_files,write_metaobjects,write_metaobject_definitions,write_discounts,write_pixels,read_customer_events,write_script_tags,read_customers,write_marketing_events,write_translations,read_analytics,read_shipping
```

| Group | Scopes | What ProfitDesk can do with them |
|---|---|---|
| Orders | read_orders, read_all_orders, write_orders | Full order history (not only 60 days), order tags and notes |
| Shipping and tracking | write_fulfillments, write_*_fulfillment_orders, read_shipping | Tell Shopify each tracking update, so Shopify sends the "Out for delivery" and "Delivered" emails. Point the order status page at your tracking page |
| Products | write_products, write_inventory, read_locations, write_publications | List products and variants, prices, stock, Shopify "Cost per item", publish to Online Store |
| Collections | write_products, write_publications | Create and fill collections (collections are part of the products permission) |
| Pages and advertorials | write_online_store_pages, write_content, write_themes, write_files | Create pages and advertorials, blog posts, page templates, upload images |
| Navigation | write_online_store_navigation | Menus |
| Custom content | write_metaobjects, write_metaobject_definitions | Reusable blocks (reviews, FAQs, advertorial sections) |
| Discounts | write_discounts | Discount codes for offers and advertorials |
| Pixels and tracking | write_pixels, read_customer_events, write_script_tags, write_marketing_events | Install tracking pixels (Meta, Google, TikTok) as a Shopify web pixel |
| Customers | read_customers | Customer names and history in the Inbox and SCM |
| Other | write_translations, read_analytics | Translated pages, Shopify analytics |

## App proxy (the customer tracking page)

- Subpath prefix: `apps`
- Subpath: `track`
- Proxy URL: `https://profitdesk-production.up.railway.app/proxy/track`

## Steps for each store

1. **Read all orders (skip for CUTEHOME, already approved).** Partner Dashboard (partners.shopify.com) → Apps → "Profitdesk x <store>" → **API access requests** → "Read all orders scope" → **Request access**. Approval has been instant.
2. **Dev Dashboard** (dev.shopify.com) → Apps → "Profitdesk x <store>" → **Versions** → **New version** (it opens pre-filled from the last one).
3. **Scopes:** replace what's there with the line above.
4. **URLs:** leave them as they are. They must be the Railway address (`https://profitdesk-production.up.railway.app`, redirect `.../auth/shopify/callback`), never 127.0.0.1.
5. **App proxy:** fill in the three boxes above. If Shopify says `track` is taken, use `pd-track` and tell Claude.
6. **Protected customer data** (if the version asks): select Name, Email, Phone and Address, with reason "Customer service / order tracking".
7. **Release.**
8. **Store admin** → Apps → "Profitdesk x <store>" → **approve the updated permissions** if Shopify asks.

When you've done them, tell Claude. ProfitDesk can check each store's permissions itself.
