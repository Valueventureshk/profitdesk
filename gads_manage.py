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


# ---------------------------------------------------------------- per campaign, per product, per day

async def product_days(refresh_token, customer_id, login_cid, start, end) -> list:
    """[{date, campaign, item, cost, impressions, clicks, conversions, value, atc}] (account days).
    atc = Google's add-to-cart conversions when the account tracks them, else 0."""
    rows = await _q(refresh_token, customer_id, login_cid,
                    "SELECT campaign.id, segments.date, segments.product_item_id, metrics.cost_micros, metrics.impressions, "
                    "metrics.clicks, metrics.conversions, metrics.conversions_value FROM shopping_performance_view "
                    f"WHERE segments.date BETWEEN '{start}' AND '{end}'")
    out = {}
    for r in rows:
        seg, m = r.get("segments") or {}, r.get("metrics") or {}
        k = (seg.get("date"), str((r.get("campaign") or {}).get("id")), seg.get("productItemId") or "")
        out[k] = {"date": k[0], "campaign": k[1], "item": k[2], "cost": _micros(m.get("costMicros")),
                  "impressions": int(m.get("impressions") or 0), "clicks": int(m.get("clicks") or 0),
                  "conversions": float(m.get("conversions") or 0), "value": float(m.get("conversionsValue") or 0), "atc": 0.0}
    try:
        atc = await _q(refresh_token, customer_id, login_cid,
                       "SELECT campaign.id, segments.date, segments.product_item_id, segments.conversion_action_category, "
                       "metrics.all_conversions FROM shopping_performance_view "
                       f"WHERE segments.date BETWEEN '{start}' AND '{end}' AND segments.conversion_action_category = 'ADD_TO_CART'")
        for r in atc:
            seg = r.get("segments") or {}
            k = (seg.get("date"), str((r.get("campaign") or {}).get("id")), seg.get("productItemId") or "")
            if k in out:
                out[k]["atc"] += float((r.get("metrics") or {}).get("allConversions") or 0)
    except gads.GoogleAdsError:
        pass                                   # the account doesn't segment add to carts: sales only
    return list(out.values())


# ---------------------------------------------------------------- changes

def _cid_of(resource: str) -> str:
    return resource.split("/")[1]


async def set_budget(rt, customer_id, login_cid, budget_resource: str, amount: float, validate_only=False):
    return await mutate(rt, customer_id, login_cid, [{"campaignBudgetOperation": {
        "update": {"resourceName": budget_resource, "amountMicros": str(int(round(amount * 1_000_000)))},
        "updateMask": "amount_micros"}}], validate_only)


async def set_status(rt, customer_id, login_cid, campaign_resource: str, status: str, validate_only=False):
    return await mutate(rt, customer_id, login_cid, [{"campaignOperation": {
        "update": {"resourceName": campaign_resource, "status": status}, "updateMask": "status"}}], validate_only)


async def set_target_roas(rt, customer_id, login_cid, campaign_resource: str, troas, validate_only=False):
    return await mutate(rt, customer_id, login_cid, [{"campaignOperation": {
        "update": {"resourceName": campaign_resource, "maximizeConversionValue": {"targetRoas": troas} if troas else {}},
        "updateMask": "maximize_conversion_value.target_roas"}}], validate_only)


def _depth(tree, node):
    d, by = 0, {n["resource"]: n for n in tree}
    while node.get("parent") and node["parent"] in by:
        node, d = by[node["parent"]], d + 1
    return d


def tree_ops(group: dict, mode: str, items: list, temp_start: int = 1) -> list:
    """Operations that replace a group's product tree with a flat one:
    mode "all" (every product), "only" (just items), "except" (every product but items)."""
    items = sorted({i.strip() for i in items if i and i.strip()}, key=str.lower)
    if mode in ("only", "except") and len(items) > 900:
        raise gads.GoogleAdsError(f"That's {len(items)} product variants in one product list; Google allows about 1,000. "
                                  "Use fewer products, or split them over campaigns.")
    tree = group.get("tree") or []
    ops = []
    if group["kind"] == "asset_group":
        for n in sorted(tree, key=lambda n: -_depth(tree, n)):            # children before parents
            ops.append({"assetGroupListingGroupFilterOperation": {"remove": n["resource"]}})
        cid, ag = _cid_of(group["resource"]), group["resource"].rsplit("/", 1)[1]
        name = lambda k: f"customers/{cid}/assetGroupListingGroupFilters/{ag}~-{k}"
        root = name(temp_start)
        if mode == "all" or (mode == "except" and not items):
            return ops + [{"assetGroupListingGroupFilterOperation": {"create": {
                "resourceName": root, "assetGroup": group["resource"], "type": "UNIT_INCLUDED", "listingSource": "SHOPPING"}}}]
        ops.append({"assetGroupListingGroupFilterOperation": {"create": {
            "resourceName": root, "assetGroup": group["resource"], "type": "SUBDIVISION", "listingSource": "SHOPPING"}}})
        inc, exc = ("UNIT_INCLUDED", "UNIT_EXCLUDED") if mode == "only" else ("UNIT_EXCLUDED", "UNIT_INCLUDED")
        for k, it in enumerate(items):
            ops.append({"assetGroupListingGroupFilterOperation": {"create": {
                "resourceName": name(temp_start + 1 + k), "assetGroup": group["resource"], "type": inc, "listingSource": "SHOPPING",
                "parentListingGroupFilter": root, "caseValue": {"productItemId": {"value": it}}}}})
        ops.append({"assetGroupListingGroupFilterOperation": {"create": {
            "resourceName": name(temp_start + 1 + len(items)), "assetGroup": group["resource"], "type": exc, "listingSource": "SHOPPING",
            "parentListingGroupFilter": root, "caseValue": {"productItemId": {}}}}})
        return ops
    # Standard Shopping ad group: removing the root removes its whole tree
    roots = [n for n in tree if not n.get("parent")]
    for n in roots:
        ops.append({"adGroupCriterionOperation": {"remove": n["resource"]}})
    cid, adg = _cid_of(group["resource"]), group["resource"].rsplit("/", 1)[1]
    name = lambda k: f"customers/{cid}/adGroupCriteria/{adg}~-{k}"
    root = name(temp_start)
    if mode == "all" or (mode == "except" and not items):
        return ops + [{"adGroupCriterionOperation": {"create": {
            "resourceName": root, "adGroup": group["resource"], "status": "ENABLED", "listingGroup": {"type": "UNIT"}}}}]
    ops.append({"adGroupCriterionOperation": {"create": {
        "resourceName": root, "adGroup": group["resource"], "status": "ENABLED", "listingGroup": {"type": "SUBDIVISION"}}}})
    for k, it in enumerate(items):
        ops.append({"adGroupCriterionOperation": {"create": {
            "resourceName": name(temp_start + 1 + k), "adGroup": group["resource"], "status": "ENABLED",
            "negative": mode == "except",
            "listingGroup": {"type": "UNIT", "parentAdGroupCriterion": root, "caseValue": {"productItemId": {"value": it}}}}}})
    ops.append({"adGroupCriterionOperation": {"create": {
        "resourceName": name(temp_start + 1 + len(items)), "adGroup": group["resource"], "status": "ENABLED",
        "negative": mode == "only",
        "listingGroup": {"type": "UNIT", "parentAdGroupCriterion": root, "caseValue": {"productItemId": {}}}}}})
    return ops


async def create_pmax(rt, customer_id, login_cid, name: str, budget: float, merchant_id: str, final_url: str,
                      items: list, target_roas=None, status="ENABLED", feed_label=None, validate_only=False) -> dict:
    """A feed-only Performance Max campaign (no text / image assets: ads come from the product feed) running
    just `items`, with its own daily budget."""
    cid = str(customer_id).replace("-", "")
    budget_rn, camp_rn, ag_rn = (f"customers/{cid}/campaignBudgets/-1", f"customers/{cid}/campaigns/-2",
                                 f"customers/{cid}/assetGroups/-3")
    shopping = {"merchantId": str(merchant_id)}
    if feed_label:
        shopping["feedLabel"] = feed_label
    ops = [
        {"campaignBudgetOperation": {"create": {"resourceName": budget_rn, "name": f"{name} · ProfitDesk",
                                                "amountMicros": str(int(round(budget * 1_000_000))),
                                                "deliveryMethod": "STANDARD", "explicitlyShared": False}}},
        {"campaignOperation": {"create": {
            "resourceName": camp_rn, "name": name, "status": status, "advertisingChannelType": "PERFORMANCE_MAX",
            "campaignBudget": budget_rn, "maximizeConversionValue": {"targetRoas": target_roas} if target_roas else {},
            "shoppingSetting": shopping,
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"}}},
        {"assetGroupOperation": {"create": {"resourceName": ag_rn, "campaign": camp_rn, "name": "Products",
                                            "finalUrls": [final_url], "status": "ENABLED"}}},
    ]
    ops += tree_ops({"kind": "asset_group", "resource": ag_rn, "tree": []}, "only", items, temp_start=10)
    got = await mutate(rt, customer_id, login_cid, ops, validate_only)
    if validate_only:
        return {"ok": True}
    res = got.get("mutateOperationResponses") or []
    camp = next((r["campaignResult"]["resourceName"] for r in res if "campaignResult" in r), None)
    return {"campaign": camp, "campaign_id": camp.rsplit("/", 1)[1] if camp else None}


async def campaign_days(refresh_token, customer_id, login_cid, start, end) -> dict:
    """{campaign id: {date: cost}} (account days), every campaign type."""
    rows = await _q(refresh_token, customer_id, login_cid,
                    "SELECT campaign.id, segments.date, metrics.cost_micros, metrics.conversions_value FROM campaign "
                    f"WHERE segments.date BETWEEN '{start}' AND '{end}'")
    out = {}
    for r in rows:
        c = str((r.get("campaign") or {}).get("id"))
        d = (r.get("segments") or {}).get("date")
        out.setdefault(c, {})[d] = _micros((r.get("metrics") or {}).get("costMicros"))
    return out


def new_tree(products: dict, add=(), remove=()) -> tuple:
    """A group's (mode, items) after adding / removing item ids, from products_of(). Raises on trees we don't edit."""
    mode, items = products.get("mode"), set(products.get("items") or [])
    add, remove = {i for i in add}, {i for i in remove}
    if mode == "custom":
        raise gads.GoogleAdsError("This campaign picks its products by labels or categories (set in Google Ads or by a feed app), "
                                  "so ProfitDesk won't rewrite it. Use a campaign that lists products by ID.")
    low = lambda s: {x.lower() for x in s}
    if mode in ("all",):
        return ("all", []) if not remove else ("except", sorted(remove))
    if mode == "none":
        return ("only", sorted(add - remove))
    if mode == "only":
        keep = {i for i in items | add if i.lower() not in low(remove)}
        return "only", sorted(keep)
    if mode == "except":
        out = {i for i in items | remove if i.lower() not in low(add)}
        return ("except", sorted(out)) if out else ("all", [])
    raise gads.GoogleAdsError(f"Unknown product list ({mode}).")
