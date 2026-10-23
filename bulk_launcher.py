"""Meta Ads → Launcher → Bulk (23 Oct 2026): many creatives uploaded straight to EcomVital (no task needed), several
primary texts / headlines / descriptions, one landing page, split into ad sets and ads the way the person picks, into a
new campaign, an existing campaign or an existing ad set. Settings are the Launcher's (ads_launcher.py), and can be
copied from a live campaign ("Copy setup from a campaign": `blueprint`).

This file only plans and translates; app.py talks to Meta (same safety as the Launcher: everything PAUSED until it all
exists, ids saved as made so pressing Launch again carries on).
"""
import copy as _copy
import os
import re

MAX_TEXTS = 5                  # Meta's limit per text kind in one ad
MAX_FLEX = 10                  # images (and separately videos) in one flexible ad
ADSETS = {"one": "All in one ad set", "each": "One ad set per creative", "groups": "One ad set per group of creatives"}
ADS = {"each": "One ad per creative", "flexible": "One flexible ad (Meta mixes the creatives)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS lx_media (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id  INTEGER NOT NULL,
    name      TEXT    NOT NULL,
    kind      TEXT    NOT NULL,              -- image | video
    size      INTEGER,
    by        TEXT,
    at        TEXT    NOT NULL DEFAULT (datetime('now')),
    hidden    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS lx_media_store ON lx_media (store_id, id);
"""


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "file")[-100:]


def media_path(root: str, m: dict) -> str:
    return os.path.join(root, str(m["store_id"]), f"{m['id']}-{safe_name(m['name'])}")


def stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name or ""))[0][:80]


def texts(raw, limit=MAX_TEXTS) -> list:
    """Up to 5 non-empty, distinct texts from a list (or one string)."""
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for t in items:
        t = str(t or "").strip()
        if t and t not in out:
            out.append(t)
    return out[:limit]


def clean_job(job: dict) -> dict:
    job = job or {}
    adsets = job.get("adsets") if job.get("adsets") in ADSETS else "one"
    ads = job.get("ads") if job.get("ads") in ADS else "each"
    try:
        group = max(2, min(20, int(job.get("group_size") or 3)))
    except (TypeError, ValueError):
        group = 3
    ids = []
    for x in job.get("media_ids") or []:
        try:
            if int(x) not in ids:
                ids.append(int(x))
        except (TypeError, ValueError):
            pass
    return {"media_ids": ids[:200], "bodies": texts(job.get("bodies")), "titles": texts(job.get("titles")),
            "descriptions": texts(job.get("descriptions")), "link": str(job.get("link") or "").strip()[:1000],
            "adsets": adsets, "ads": ads, "group_size": group}


def plan(media: list, job: dict, existing_adset: bool) -> list:
    """[[ad, ...] per ad set], each ad = list of media. Flexible ads hold at most 10 images + 10 videos."""
    if existing_adset or job["adsets"] == "one":
        sets = [media]
    elif job["adsets"] == "each":
        sets = [[m] for m in media]
    else:
        n = job["group_size"]
        sets = [media[i:i + n] for i in range(0, len(media), n)]
    out = []
    for group in sets:
        if job["ads"] == "each" or len(group) == 1:
            out.append([[m] for m in group])
            continue
        ads, cur = [], []
        for m in group:
            kind = m.get("kind")
            if sum(1 for x in cur if x.get("kind") == kind) >= MAX_FLEX:
                ads.append(cur)
                cur = []
            cur.append(m)
        if cur:
            ads.append(cur)
        out.append(ads)
    return [s for s in out if s]


def problems(s: dict, job: dict, n_media: int) -> list:
    """What's missing before Meta is asked (on top of the Launcher's own settings checks)."""
    out = []
    a, c, ad = s["adset"], s["campaign"], s["ad"]
    into_adset = a.get("mode") == "existing"
    if not n_media:
        out.append("pick at least one creative")
    if not job["link"].startswith("http"):
        out.append("the landing page link (https://…)")
    if not job["bodies"]:
        out.append("at least one primary text")
    if not job["titles"]:
        out.append("at least one headline")
    if not ad.get("page_id"):
        out.append("the Facebook Page")
    if into_adset:
        if not a.get("existing_id"):
            out.append("the existing ad set")
        return out
    if c["mode"] == "existing" and not c.get("existing_id"):
        out.append("the existing campaign")
    if c["mode"] == "new" or c["budget_mode"] != "cbo":
        try:
            if float(c.get("daily_budget") or 0) <= 0:
                out.append("a daily budget")
        except ValueError:
            out.append("a daily budget (a number)")
    if c["bid_strategy"] in ("COST_CAP", "LOWEST_COST_WITH_BID_CAP") and not str(c.get("bid_amount") or "").strip():
        out.append("the cost / bid cap amount")
    if c["bid_strategy"] == "LOWEST_COST_WITH_MIN_ROAS" and not str(c.get("roas_floor") or "").strip():
        out.append("the ROAS goal")
    if a.get("audience") != "copied":
        from ads_launcher import countries
        if not countries(a["countries"]):
            out.append("countries (e.g. AU)")
    if not a.get("pixel_id"):
        out.append("the pixel")
    if a.get("start") == "at" and not a.get("start_at"):
        out.append("the start date and time")
    return out


def creative(s: dict, name: str, link: str, job: dict, media: list) -> dict:
    """One ad's creative. Several creatives (flexible) or several texts = Meta's asset feed (it mixes them);
    one creative with one text each = a normal image / video ad."""
    from ads_launcher import enhancements_spec, UTM
    ad = s["ad"]
    cta = ad.get("cta") or "SHOP_NOW"
    body = {"name": name, "degrees_of_freedom_spec": enhancements_spec(s)}
    if ad.get("utm", True):
        body["url_tags"] = ad.get("url_tags") or UTM
    story = {"page_id": ad["page_id"]}
    if ad.get("instagram_id"):
        story["instagram_user_id"] = ad["instagram_id"]
    many_texts = len(job["bodies"]) > 1 or len(job["titles"]) > 1 or len(job["descriptions"]) > 1
    if len(media) > 1 or many_texts:
        feed = {"bodies": [{"text": t} for t in job["bodies"]], "titles": [{"text": t} for t in job["titles"]],
                "link_urls": [{"website_url": link, **({"display_url": ad["display_link"]} if ad.get("display_link") else {})}],
                "call_to_action_types": [cta], "ad_formats": ["AUTOMATIC_FORMAT"], "optimization_type": "DEGREES_OF_FREEDOM"}
        if job["descriptions"]:
            feed["descriptions"] = [{"text": t} for t in job["descriptions"]]
        imgs = [{"hash": m["image_hash"]} for m in media if m.get("image_hash")]
        vids = [{"video_id": m["video_id"], "thumbnail_url": m["thumb"]} for m in media if m.get("video_id")]
        if imgs:
            feed["images"] = imgs
        if vids:
            feed["videos"] = vids
        body["asset_feed_spec"] = feed
        body["object_story_spec"] = story
        return body
    m = media[0]
    call = {"type": cta, "value": {"link": link}}
    text, title = job["bodies"][0], job["titles"][0]
    desc = job["descriptions"][0] if job["descriptions"] else ""
    if m.get("video_id"):
        story["video_data"] = {"video_id": m["video_id"], "image_url": m["thumb"], "message": text, "title": title,
                               "link_description": desc, "call_to_action": call}
    else:
        story["link_data"] = {"image_hash": m["image_hash"], "link": link, "message": text, "name": title,
                              "description": desc, "call_to_action": call}
        if ad.get("display_link"):
            story["link_data"]["caption"] = ad["display_link"]
    body["object_story_spec"] = story
    return body


# ---- Copy setup from a campaign: a live campaign + one of its ad sets + one of its ads → Launcher settings,
# plus a plain list of every feature found (and what can't be copied).

_BASIC_TARGETING = {"geo_locations", "age_min", "age_max", "genders", "targeting_automation", "publisher_platforms",
                    "facebook_positions", "instagram_positions", "messenger_positions", "audience_network_positions",
                    "device_platforms", "brand_safety_content_filter_levels", "targeting_relaxation_types"}
_POS = {"feed": "Feed", "story": "Stories", "facebook_reels": "Reels", "reels": "Reels", "stream": "Feed",
        "explore": "Explore", "explore_home": "Explore home", "marketplace": "Marketplace", "video_feeds": "Video feeds",
        "search": "Search", "right_hand_column": "Right column", "instream_video": "In-stream video",
        "profile_feed": "Profile feed", "ig_search": "Search", "messenger_home": "Inbox", "classic": "Native / banner",
        "rewarded_video": "Rewarded video", "notification": "Notifications", "facebook_reels_overlay": "Reels overlay",
        "biz_disco_feed": "Business discovery", "profile_reels": "Profile reels"}
_ATTR = {((("CLICK_THROUGH", 7), ("VIEW_THROUGH", 1))): "7c1v", (("CLICK_THROUGH", 7),): "7c",
         (("CLICK_THROUGH", 1),): "1c", (("CLICK_THROUGH", 1), ("VIEW_THROUGH", 1)): "1c1v"}
_BID = {"LOWEST_COST_WITHOUT_CAP": "Highest volume", "COST_CAP": "Cost per result goal",
        "LOWEST_COST_WITH_BID_CAP": "Bid cap", "LOWEST_COST_WITH_MIN_ROAS": "ROAS goal"}


def _money(minor, factor: int):
    try:
        return round(int(minor) / factor, 2) if minor not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        return None


def blueprint(camp: dict, adset: dict | None, cr: dict | None, base: dict, cur: str, factor: int = 100) -> dict:
    """camp / adset / cr = Meta objects as read; base = the Launcher's current settings (kept where nothing is found).
    Returns {settings, features: [..], warnings: [..], copy: {bodies, titles, descriptions, link}}."""
    s = _copy.deepcopy(base)
    feats, warns = [], []
    c, a, ad = s["campaign"], s["adset"], s["ad"]
    c["mode"] = "new"
    obj = camp.get("objective") or ""
    feats.append(f"Objective: {obj.replace('OUTCOME_', '').title() or 'not given'}")
    if obj and obj != "OUTCOME_SALES":
        warns.append(f"This campaign's objective is {obj}; the launcher makes Sales campaigns.")
    cb = _money(camp.get("daily_budget"), factor)
    if cb:
        c.update(budget_mode="cbo", daily_budget=str(cb))
        feats.append(f"Budget on the campaign (Advantage+ campaign budget): {cur} {cb:,.2f} a day")
    elif camp.get("lifetime_budget") not in (None, "", "0"):
        warns.append("The campaign has a lifetime budget; the launcher uses daily budgets, so set one here.")
    bid = (camp.get("bid_strategy") if cb else (adset or {}).get("bid_strategy")) or c["bid_strategy"]
    if bid in _BID:
        c["bid_strategy"] = bid
        feats.append(f"Bid strategy: {_BID[bid]}")
    if adset:
        ab = _money(adset.get("daily_budget"), factor)
        if not cb:
            c["budget_mode"] = "abo"
            if ab:
                c["daily_budget"] = str(ab)
                feats.append(f"Budget on each ad set: {cur} {ab:,.2f} a day")
            elif adset.get("lifetime_budget") not in (None, "", "0"):
                warns.append("The ad set has a lifetime budget; the launcher uses daily budgets, so set one here.")
        bm = _money(adset.get("bid_amount"), factor)
        if bm and bid in ("COST_CAP", "LOWEST_COST_WITH_BID_CAP"):
            c["bid_amount"] = str(bm)
            feats.append(f"{'Cost per result goal' if bid == 'COST_CAP' else 'Bid cap'}: {cur} {bm:,.2f}")
        floor = ((adset.get("bid_constraints") or {}).get("roas_average_floor"))
        if floor and bid == "LOWEST_COST_WITH_MIN_ROAS":
            c["roas_floor"] = str(round(int(floor) / 10000, 2))
            feats.append(f"ROAS goal: {c['roas_floor']}")
        po = adset.get("promoted_object") or {}
        if po.get("pixel_id"):
            a["pixel_id"] = str(po["pixel_id"])
        if po.get("custom_event_type"):
            a["event"] = po["custom_event_type"]
        feats.append(f"Optimised for: {(po.get('custom_event_type') or adset.get('optimization_goal') or '').replace('_', ' ').title()}"
                     + (f" on pixel {po['pixel_id']}" if po.get("pixel_id") else ""))
        if adset.get("optimization_goal") not in (None, "OFFSITE_CONVERSIONS"):
            warns.append(f"The ad set optimises for {adset['optimization_goal']}; the launcher optimises for conversions.")
        spec = tuple(sorted((x.get("event_type"), int(x.get("window_days") or 0)) for x in adset.get("attribution_spec") or []))
        key = _ATTR.get(spec)
        if key:
            a["attribution"] = key
        if spec and not key:
            warns.append("The attribution window isn't one of the launcher's four; it uses the one picked here.")
        if spec:
            feats.append("Attribution: " + ", ".join(f"{d}-day {'click' if e == 'CLICK_THROUGH' else 'view' if e == 'VIEW_THROUGH' else 'engaged view'}" for e, d in spec))
        t = adset.get("targeting") or {}
        geo = t.get("geo_locations") or {}
        if geo.get("countries"):
            a["countries"] = " ".join(geo["countries"])
        a["age_min"], a["age_max"] = int(t.get("age_min") or 18), int(t.get("age_max") or 65)
        g = t.get("genders") or []
        a["genders"] = "women" if g == [2] else "men" if g == [1] else "all"
        adv = (t.get("targeting_automation") or {}).get("advantage_audience")
        a["advantage_audience"] = bool(adv)
        feats.append(f"Advantage+ audience: {'on' if adv else 'off'}")
        feats.append(f"Who: {', '.join(geo.get('countries') or []) or 'see locations'} · ages {a['age_min']}-{a['age_max']}"
                     f"{'+' if a['age_max'] == 65 else ''} · {'women' if g == [2] else 'men' if g == [1] else 'everyone'}")
        if t.get("publisher_platforms"):
            a["placements"] = "manual"
            a["platforms"] = list(t["publisher_platforms"])
            a["fb_positions"] = list(t.get("facebook_positions") or a["fb_positions"])
            a["ig_positions"] = list(t.get("instagram_positions") or a["ig_positions"])
            pos = []
            for p in t["publisher_platforms"]:
                ps = t.get(f"{p}_positions") or []
                pos.append(f"{p.replace('_', ' ').title()}" + (f" ({', '.join(_POS.get(x, x) for x in ps)})" if ps else ""))
            feats.append("Placements: manual — " + "; ".join(pos))
        else:
            a["placements"] = "advantage"
            feats.append("Placements: Advantage+ (all)")
        extra = []
        for k, v in t.items():
            if k in _BASIC_TARGETING or not v:
                continue
            if k == "custom_audiences":
                extra.append(f"{len(v)} custom audience{'s' if len(v) != 1 else ''} included")
            elif k == "excluded_custom_audiences":
                extra.append(f"{len(v)} custom audience{'s' if len(v) != 1 else ''} excluded")
            elif k == "flexible_spec":
                extra.append("detailed targeting (interests / behaviours)")
            elif k == "exclusions":
                extra.append("detailed targeting exclusions")
            elif k == "excluded_geo_locations":
                extra.append("excluded locations")
            elif k == "locales":
                extra.append(f"{len(v)} language{'s' if len(v) != 1 else ''}")
            else:
                extra.append(k.replace("_", " "))
        for k in ("regions", "cities", "zips", "places", "custom_locations"):
            if geo.get(k):
                extra.append(f"locations: {k}")
        if extra or (t.get("device_platforms") and set(t["device_platforms"]) != {"mobile", "desktop"}):
            if t.get("device_platforms") and set(t["device_platforms"]) != {"mobile", "desktop"}:
                extra.append("devices: " + ", ".join(t["device_platforms"]))
            a["audience"] = "copied"
            a["targeting_raw"] = t
            a["targeting_from"] = adset.get("name") or adset.get("id") or ""
            feats.append("Audience extras (copied exactly): " + ", ".join(extra))
        else:
            a["audience"] = "form"
            a["targeting_raw"] = None
            a["targeting_from"] = ""
        if adset.get("is_dynamic_creative"):
            feats.append("Dynamic creative ad set")
            warns.append("This ad set uses dynamic creative; bulk ads use flexible ads instead, which Meta now recommends.")
        if adset.get("frequency_control_specs"):
            warns.append("A frequency cap is set on the ad set; it isn't copied (set it in Ads Manager).")
        if adset.get("adset_schedule"):
            warns.append("The ad set runs on a schedule (dayparting); it isn't copied (set it in Ads Manager).")
        if adset.get("destination_type") not in (None, "", "WEBSITE", "UNDEFINED"):
            warns.append(f"Destination is {adset['destination_type']}; bulk ads send people to your website.")
    copy_out = {"bodies": [], "titles": [], "descriptions": [], "link": ""}
    if cr:
        st = cr.get("object_story_spec") or {}
        if st.get("page_id"):
            ad["page_id"] = str(st["page_id"])
        ig = st.get("instagram_user_id") or st.get("instagram_actor_id") or cr.get("instagram_user_id") or cr.get("instagram_actor_id")
        if ig:
            ad["instagram_id"] = str(ig)
        feats.append("Facebook Page and Instagram account" if ig else "Facebook Page (Instagram uses the Page)")
        feed = cr.get("asset_feed_spec") or {}
        ld = st.get("link_data") or st.get("video_data") or {}
        cta = ((ld.get("call_to_action") or {}).get("type") or (feed.get("call_to_action_types") or [None])[0]
               or cr.get("call_to_action_type"))
        if cta:
            ad["cta"] = cta
            feats.append(f"Button: {cta.replace('_', ' ').title()}")
        if feed:
            ad["multi"] = "flexible"
            n = len(feed.get("images") or []) + len(feed.get("videos") or [])
            feats.append(f"Flexible / multi-text ad ({n} creative{'s' if n != 1 else ''}, "
                         f"{len(feed.get('bodies') or [])} primary text{'s' if len(feed.get('bodies') or []) != 1 else ''}, "
                         f"{len(feed.get('titles') or [])} headline{'s' if len(feed.get('titles') or []) != 1 else ''})")
            copy_out["bodies"] = [x.get("text") for x in feed.get("bodies") or [] if x.get("text")][:MAX_TEXTS]
            copy_out["titles"] = [x.get("text") for x in feed.get("titles") or [] if x.get("text")][:MAX_TEXTS]
            copy_out["descriptions"] = [x.get("text") for x in feed.get("descriptions") or [] if x.get("text")][:MAX_TEXTS]
            copy_out["link"] = ((feed.get("link_urls") or [{}])[0]).get("website_url") or ""
            disp = ((feed.get("link_urls") or [{}])[0]).get("display_url")
        else:
            copy_out["bodies"] = [ld["message"]] if ld.get("message") else []
            copy_out["titles"] = [ld.get("name") or ld.get("title")] if (ld.get("name") or ld.get("title")) else []
            d = ld.get("description") or ld.get("link_description")
            copy_out["descriptions"] = [d] if d else []
            copy_out["link"] = ld.get("link") or ((ld.get("call_to_action") or {}).get("value") or {}).get("link") or ""
            disp = ld.get("caption")
        if disp:
            ad["display_link"] = disp
            feats.append(f"Display link: {disp}")
        tags = cr.get("url_tags") or ""
        ad["utm"] = bool(tags)
        ad["url_tags"] = tags
        feats.append("Tracking tags on the link (copied as they are)" if tags else "No tracking tags on the link")
        fs = ((cr.get("degrees_of_freedom_spec") or {}).get("creative_features_spec") or {})
        on = {k: True for k, v in fs.items() if (v or {}).get("enroll_status") == "OPT_IN"}
        ad["enhancements"] = on
        feats.append("Advantage+ creative enhancements on: " + (", ".join(k.replace("_", " ") for k in on) or "none"))
    return {"settings": s, "features": feats, "warnings": warns, "copy": copy_out}
