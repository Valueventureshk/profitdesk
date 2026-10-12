"""Apps → Google Ads → Manage: read a store's campaign structure and make changes in Google Ads.

Reads: campaigns (type, status, budget, bidding), Performance Max asset groups and Standard Shopping
ad groups, and which products each one includes or excludes (listing group filters / listing groups,
by product item id).

Changes (all through googleAds:mutate, each one first tried with validateOnly so Google checks it
before anything is spent):
    set_budget          a campaign's daily budget
    set_status          ENABLED / PAUSED
    set_products        which item ids an asset group (Performance Max) or ad group (Shopping) runs
    create_campaign     a new feed-only Performance Max or Standard Shopping campaign with a budget

Every change is logged by the caller (app.py, table ads_actions) with what it was before, so it can
be undone.
"""
import json
import re

import httpx

import google_ads_client as gads

ITEM_OTHERS = None          # the "everything else" node of a product tree


async def _ctx(refresh_token, customer_id, login_cid):
    _, _, dev, version = gads._cfg()
    access = await gads._access_token(refresh_token)
    cid = str(customer_id).replace("-", "")
    return version, cid, gads._headers(access, dev, login_cid or customer_id)


async def _q(refresh_token, customer_id, login_cid, query):
    version, cid, headers = await _ctx(refresh_token, customer_id, login_cid)
    async with httpx.AsyncClient(timeout=90) as client:
        return await gads._search(client, version, cid, query, headers)


async def mutate(refresh_token, customer_id, login_cid, operations: list, validate_only=False) -> dict:
    """googleAds:mutate with a list of {"<resource>Operation": {...}} entries, all or nothing."""
    version, cid, headers = await _ctx(refresh_token, customer_id, login_cid)
    url = f"https://googleads.googleapis.com/{version}/customers/{cid}/googleAds:mutate"
    body = {"mutateOperations": operations, "partialFailure": False, "validateOnly": bool(validate_only)}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=headers, json=body)
    if r.status_code >= 400:
        raise gads.GoogleAdsError(f"Google Ads refused the change: {gads._why(r)}")
    return r.json()


def _micros(v) -> float:
    return int(v or 0) / 1_000_000


async def structure(refresh_token, customer_id, login_cid) -> dict:
    """Every campaign (not removed) with budget and bidding, its asset groups / ad groups and their
    product trees: {"campaigns": [...], "currency", "timezone", "merchant_ids"}."""
    camps = await _q(refresh_token, customer_id, login_cid,
                     "SELECT customer.currency_code, customer.time_zone, campaign.id, campaign.name, campaign.status, "
                     "campaign.advertising_channel_type, campaign.bidding_strategy_type, "
                     "campaign.maximize_conversion_value.target_roas, campaign.target_roas.target_roas, "
                     "campaign.shopping_setting.merchant_id, campaign.shopping_setting.feed_label, "
                     "campaign_budget.resource_name, campaign_budget.amount_micros, campaign_budget.explicitly_shared "
                     "FROM campaign WHERE campaign.status != 'REMOVED'")
    out, cur, tz, merchants = {}, "", "", set()
    for r in camps:
        c, b, cu = r.get("campaign") or {}, r.get("campaignBudget") or {}, r.get("customer") or {}
        cur, tz = cu.get("currencyCode") or cur, cu.get("timeZone") or tz
        ss = c.get("shoppingSetting") or {}
        if ss.get("merchantId"):
            merchants.add(str(ss["merchantId"]))
        troas = (c.get("maximizeConversionValue") or {}).get("targetRoas") or (c.get("targetRoas") or {}).get("targetRoas")
        out[str(c["id"])] = {
            "id": str(c["id"]), "resource": c.get("resourceName"), "name": c.get("name"), "status": c.get("status"),
            "type": c.get("advertisingChannelType"), "bidding": c.get("biddingStrategyType"),
            "target_roas": float(troas) if troas else None, "merchant_id": ss.get("merchantId"),
            "feed_label": ss.get("feedLabel"), "budget": _micros(b.get("amountMicros")),
            "budget_resource": b.get("resourceName"), "budget_shared": bool(b.get("explicitlyShared")),
            "groups": []}
    groups = {}
    ags = await _q(refresh_token, customer_id, login_cid,
                   "SELECT campaign.id, asset_group.id, asset_group.name, asset_group.status, asset_group.resource_name "
                   "FROM asset_group WHERE asset_group.status != 'REMOVED'")
    for r in ags:
        a, cid = r.get("assetGroup") or {}, str((r.get("campaign") or {}).get("id"))
        g = {"kind": "asset_group", "id": str(a.get("id")), "resource": a.get("resourceName"), "name": a.get("name"),
             "status": a.get("status"), "campaign": cid, "tree": []}
        groups[g["resource"]] = g
        if cid in out:
            out[cid]["groups"].append(g)
    flt = await _q(refresh_token, customer_id, login_cid,
                   "SELECT asset_group_listing_group_filter.resource_name, asset_group_listing_group_filter.asset_group, "
                   "asset_group_listing_group_filter.type, asset_group_listing_group_filter.parent_listing_group_filter, "
                   "asset_group_listing_group_filter.case_value.product_item_id.value, "
                   "asset_group_listing_group_filter.listing_source FROM asset_group_listing_group_filter")
    for r in flt:
        f = r.get("assetGroupListingGroupFilter") or {}
        g = groups.get(f.get("assetGroup"))
        if g is not None:
            cv = f.get("caseValue") or {}
            g["tree"].append({"resource": f.get("resourceName"), "type": f.get("type"), "parent": f.get("parentListingGroupFilter"),
                              "dimension": next(iter(cv), None),
                              "item": ((cv.get("productItemId") or {}).get("value")) if "productItemId" in cv else None,
                              "source": f.get("listingSource")})
    adg = await _q(refresh_token, customer_id, login_cid,
                   "SELECT campaign.id, ad_group.id, ad_group.name, ad_group.status, ad_group.resource_name, ad_group.type "
                   "FROM ad_group WHERE ad_group.status != 'REMOVED' AND campaign.advertising_channel_type = 'SHOPPING'")
    for r in adg:
        a, cid = r.get("adGroup") or {}, str((r.get("campaign") or {}).get("id"))
        g = {"kind": "ad_group", "id": str(a.get("id")), "resource": a.get("resourceName"), "name": a.get("name"),
             "status": a.get("status"), "campaign": cid, "tree": []}
        groups[g["resource"]] = g
        if cid in out:
            out[cid]["groups"].append(g)
    if any(c["type"] == "SHOPPING" for c in out.values()):
        lg = await _q(refresh_token, customer_id, login_cid,
                      "SELECT ad_group_criterion.resource_name, ad_group_criterion.ad_group, ad_group_criterion.negative, "
                      "ad_group_criterion.listing_group.type, ad_group_criterion.listing_group.parent_ad_group_criterion, "
                      "ad_group_criterion.listing_group.case_value.product_item_id.value, ad_group_criterion.cpc_bid_micros "
                      "FROM ad_group_criterion WHERE ad_group_criterion.type = 'LISTING_GROUP' AND ad_group_criterion.status != 'REMOVED'")
        for r in lg:
            c = r.get("adGroupCriterion") or {}
            g = groups.get(c.get("adGroup"))
            if g is not None:
                lgp = c.get("listingGroup") or {}
                cv = lgp.get("caseValue") or {}
                g["tree"].append({"resource": c.get("resourceName"), "type": lgp.get("type"), "parent": lgp.get("parentAdGroupCriterion"),
                                  "dimension": next(iter(cv), None),
                                  "item": ((cv.get("productItemId") or {}).get("value")) if "productItemId" in cv else None,
                                  "negative": bool(c.get("negative")), "bid": _micros(c.get("cpcBidMicros"))})
    for g in groups.values():
        g["products"] = products_of(g)
    return {"campaigns": sorted(out.values(), key=lambda c: (c["status"] != "ENABLED", c["name"] or "")),
            "currency": cur, "timezone": tz, "merchant_ids": sorted(merchants)}


def products_of(g: dict) -> dict:
    """What a group's product tree runs: {"mode": "all" | "only" | "except" | "custom",
    "items": [item ids listed]}. Our own trees are flat: root -> one node per item id + "everything else"."""
    tree = g.get("tree") or []
    if not tree:
        return {"mode": "none", "items": []}
    root = [n for n in tree if not n.get("parent")]
    if len(root) == 1 and root[0]["type"] in ("UNIT_INCLUDED", "UNIT"):
        return {"mode": "all", "items": []}
    kids = [n for n in tree if n.get("parent") and n.get("dimension") == "productItemId"]
    if len(kids) + 1 != len(tree):
        return {"mode": "custom", "items": []}     # split by brand / category / labels: left as Google has it
    others = next((n for n in kids if n["item"] is None), None)
    listed = [n for n in kids if n["item"] is not None]

    def included(n):
        return n["type"] == "UNIT_INCLUDED" or (n["type"] == "UNIT" and not n.get("negative"))
    if others and included(others):
        return {"mode": "except", "items": sorted(n["item"] for n in listed if not included(n))}
    return {"mode": "only", "items": sorted(n["item"] for n in listed if included(n))}
