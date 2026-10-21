"""Meta Ads → Launcher: put approved advertorial / listing tasks into Meta as one campaign + one ad set, one ad per task.

Every switch is the person's (CBO / ABO, budget, bid strategy, Advantage+ audience / placements, every Advantage+
creative enhancement, countries, age, gender, pixel event, attribution, start time). A task with several creatives
becomes ONE flexible ad (Meta mixes its images / videos); one creative = a normal image or video ad.

Safety: everything is created PAUSED and only switched on once the campaign, ad set and every ad exist, so a launch
that stops halfway never spends. Each Meta id is saved as it's made (task_launches.progress), so pressing Launch again
carries on instead of doubling. A scheduled start ("tomorrow 00:01") is the ad set's start_time on the ad account's
clock. Settings are kept per store as named presets + the last used (settings launcher:<store>).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db

DEFAULTS = {
    "campaign": {"mode": "new", "existing_id": "", "name": "{store} | {date} | {preset}", "budget_mode": "abo",
                 "daily_budget": "30", "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "bid_amount": "", "roas_floor": ""},
    "adset": {"name": "{tasks} | {countries}", "countries": "", "age_min": 18, "age_max": 65, "genders": "all",
              "advantage_audience": True, "placements": "advantage",
              "platforms": ["facebook", "instagram"], "fb_positions": ["feed", "story", "facebook_reels"],
              "ig_positions": ["stream", "story", "reels", "explore"],
              "pixel_id": "", "event": "PURCHASE", "attribution": "7c1v",
              "start": "now", "start_at": "", "end_at": ""},
    "ad": {"name": "{task}", "page_id": "", "instagram_id": "", "cta": "SHOP_NOW", "utm": True,
           "multi": "flexible", "enhancements": {}},
}
BID_STRATEGIES = {"LOWEST_COST_WITHOUT_CAP": "Highest volume (no cap)", "COST_CAP": "Cost per result goal (cost cap)",
                  "LOWEST_COST_WITH_BID_CAP": "Bid cap", "LOWEST_COST_WITH_MIN_ROAS": "ROAS goal"}
EVENTS = {"PURCHASE": "Purchase", "ADD_TO_CART": "Add to cart", "INITIATED_CHECKOUT": "Initiate checkout"}
ATTRIBUTION = {"7c1v": "7-day click or 1-day view", "7c": "7-day click", "1c": "1-day click", "1c1v": "1-day click or 1-day view"}
CTAS = {"SHOP_NOW": "Shop now", "LEARN_MORE": "Learn more", "SEE_MORE": "See more", "GET_OFFER": "Get offer",
        "ORDER_NOW": "Order now", "BUY_NOW": "Buy now"}
# Advantage+ creative enhancements, each its own switch (Meta's creative_features_spec names). Off unless switched on.
ENHANCEMENTS = {
    "text_optimizations": "Text improvements (Meta rewrites / swaps your text)",
    "image_touchups": "Visual touch-ups (crops / adjusts images)",
    "image_brightness_and_contrast": "Brightness and contrast",
    "image_uncrop": "Expand image (AI fills the edges)",
    "adapt_to_placement": "Adapt to placement (9:16 versions)",
    "enhance_cta": "Enhance call to action",
    "add_text_overlay": "Add text overlays",
    "image_templates": "Add image templates / frames",
    "image_animation": "Image animation",
    "music": "Add music",
    "3d_animation": "3D animation",
    "inline_comment": "Show a relevant comment",
    "description_automation": "Add descriptions automatically",
    "site_extensions": "Site links",
    "video_auto_crop": "Video auto-crop",
}
UTM = ("utm_source=facebook&utm_medium=paid&utm_campaign={{campaign.name}}&utm_term={{adset.name}}"
       "&utm_content={{ad.name}}&utm_id={{campaign.id}}")


def merged(settings: dict | None) -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    for part in ("campaign", "adset", "ad"):
        s[part].update({k: v for k, v in ((settings or {}).get(part) or {}).items() if k in DEFAULTS[part]})
    return s


def store_cfg(store_id: int) -> dict:
    try:
        got = json.loads(db.get_setting(f"launcher:{store_id}") or "{}")
    except ValueError:
        got = {}
    return {"presets": got.get("presets") or [], "last": got.get("last") or {}, "drafts": got.get("drafts") or []}


def save_cfg(store_id: int, cfg: dict):
    db.set_setting(f"launcher:{store_id}", json.dumps(cfg))


def countries(raw) -> list:
    if isinstance(raw, list):
        raw = " ".join(raw)
    return [c for c in re.split(r"[\s,]+", str(raw or "").upper()) if re.fullmatch(r"[A-Z]{2}", c)]


def fill(tpl: str, **kw) -> str:
    out = str(tpl or "")
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return re.sub(r"\s+\|\s+\|", " |", out).strip(" |")[:250]


def start_time(s: dict, tz_name: str) -> str | None:
    """The ad set's start on the ad account's clock, as Meta wants it (ISO with offset), or None = now."""
    a = s["adset"]
    if a.get("start") != "at" or not a.get("start_at"):
        return None
    try:
        tz = ZoneInfo(tz_name or "UTC")
        when = datetime.fromisoformat(str(a["start_at"])).replace(tzinfo=tz)
    except (ValueError, KeyError):
        raise ValueError("The start time isn't a date and time.")
    if when < datetime.now(timezone.utc) - timedelta(minutes=5):
        raise ValueError("The start time is in the past.")
    return when.isoformat()


def end_time(s: dict, tz_name: str) -> str | None:
    a = s["adset"]
    if not a.get("end_at"):
        return None
    try:
        return datetime.fromisoformat(str(a["end_at"])).replace(tzinfo=ZoneInfo(tz_name or "UTC")).isoformat()
    except (ValueError, KeyError):
        raise ValueError("The end time isn't a date and time.")


def campaign_body(s: dict, name: str, minor: int | None) -> dict:
    c = s["campaign"]
    body = {"name": name, "objective": "OUTCOME_SALES", "status": "PAUSED", "special_ad_categories": []}
    if c["budget_mode"] == "cbo":
        body.update({"daily_budget": minor, "bid_strategy": c["bid_strategy"]})
    else:
        body["is_adset_budget_sharing_enabled"] = "false"
    return body


def adset_body(s: dict, name: str, campaign_id: str, minor: int | None, tz_name: str, bid_minor: int | None) -> dict:
    a, c = s["adset"], s["campaign"]
    targeting = {"geo_locations": {"countries": countries(a["countries"])},
                 "age_min": int(a.get("age_min") or 18), "age_max": int(a.get("age_max") or 65),
                 "targeting_automation": {"advantage_audience": 1 if a.get("advantage_audience") else 0}}
    if a.get("genders") in ("women", "men"):
        targeting["genders"] = [2 if a["genders"] == "women" else 1]
    if a.get("placements") == "manual":
        plats = [p for p in a.get("platforms") or [] if p in ("facebook", "instagram", "audience_network", "messenger")]
        targeting["publisher_platforms"] = plats
        if "facebook" in plats:
            targeting["facebook_positions"] = a.get("fb_positions") or ["feed"]
        if "instagram" in plats:
            targeting["instagram_positions"] = a.get("ig_positions") or ["stream"]
    attr = {"7c1v": [("CLICK_THROUGH", 7), ("VIEW_THROUGH", 1)], "7c": [("CLICK_THROUGH", 7)],
            "1c": [("CLICK_THROUGH", 1)], "1c1v": [("CLICK_THROUGH", 1), ("VIEW_THROUGH", 1)]}.get(a.get("attribution"), [])
    body = {"name": name, "campaign_id": campaign_id, "status": "PAUSED", "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "promoted_object": {"pixel_id": a["pixel_id"], "custom_event_type": a.get("event") or "PURCHASE"},
            "targeting": targeting,
            "attribution_spec": [{"event_type": e, "window_days": d} for e, d in attr]}
    if c["budget_mode"] != "cbo":
        body.update({"daily_budget": minor, "bid_strategy": c["bid_strategy"]})
    if c["bid_strategy"] in ("COST_CAP", "LOWEST_COST_WITH_BID_CAP") and bid_minor:
        body["bid_amount"] = bid_minor
    if c["bid_strategy"] == "LOWEST_COST_WITH_MIN_ROAS" and c.get("roas_floor"):
        body["bid_constraints"] = {"roas_average_floor": int(round(float(c["roas_floor"]) * 10000))}
    st, en = start_time(s, tz_name), end_time(s, tz_name)
    if st:
        body["start_time"] = st
    if en:
        body["end_time"] = en
    return body


def enhancements_spec(s: dict) -> dict:
    on = (s["ad"].get("enhancements") or {})
    return {"creative_features_spec": {k: {"enroll_status": "OPT_IN" if on.get(k) else "OPT_OUT"} for k in ENHANCEMENTS}}


def creative_body(s: dict, name: str, page_id: str, ig_id: str, link: str, copy: dict, media: list) -> dict:
    """media = [{"image_hash"} | {"video_id", "thumb"}]. Several = one flexible ad (Meta mixes them)."""
    cta = s["ad"].get("cta") or "SHOP_NOW"
    body = {"name": name, "degrees_of_freedom_spec": enhancements_spec(s)}
    if s["ad"].get("utm", True):
        body["url_tags"] = UTM
    story = {"page_id": page_id}
    if ig_id:
        story["instagram_user_id"] = ig_id
    if len(media) > 1 and s["ad"].get("multi", "flexible") == "flexible":
        feed = {"bodies": [{"text": copy["message"]}], "titles": [{"text": copy["headline"]}],
                "link_urls": [{"website_url": link}], "call_to_action_types": [cta],
                "ad_formats": ["AUTOMATIC_FORMAT"], "optimization_type": "DEGREES_OF_FREEDOM"}
        if copy.get("description"):
            feed["descriptions"] = [{"text": copy["description"]}]
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
    if m.get("video_id"):
        story["video_data"] = {"video_id": m["video_id"], "image_url": m["thumb"], "message": copy["message"],
                               "title": copy["headline"], "link_description": copy.get("description") or "",
                               "call_to_action": call}
    else:
        story["link_data"] = {"image_hash": m["image_hash"], "link": link, "message": copy["message"],
                              "name": copy["headline"], "description": copy.get("description") or "",
                              "call_to_action": call}
    body["object_story_spec"] = story
    return body


def problems(s: dict, n_tasks: int) -> list:
    """What's missing before Meta is even asked."""
    out = []
    c, a, ad = s["campaign"], s["adset"], s["ad"]
    if not n_tasks:
        out.append("pick at least one task")
    if c["mode"] == "existing" and not c.get("existing_id"):
        out.append("pick the existing campaign")
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
    if not countries(a["countries"]):
        out.append("countries (e.g. AU)")
    if not a.get("pixel_id"):
        out.append("the pixel")
    if a.get("placements") == "manual" and not a.get("platforms"):
        out.append("at least one placement platform")
    if a.get("start") == "at" and not a.get("start_at"):
        out.append("the start date and time")
    if not ad.get("page_id"):
        out.append("the Facebook Page")
    try:
        lo, hi = int(a.get("age_min") or 18), int(a.get("age_max") or 65)
        if not (13 <= lo <= hi <= 65):
            out.append("an age range between 18 and 65")
    except ValueError:
        out.append("ages as numbers")
    return out
