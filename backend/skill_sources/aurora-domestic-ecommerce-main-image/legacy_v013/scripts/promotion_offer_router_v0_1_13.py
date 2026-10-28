#!/usr/bin/env python3
"""Build one commercial search-listing cover from current-product evidence."""

from __future__ import annotations

import copy
import re
from typing import Any


SEARCH_MAIN_ROLE = "SEARCH_MAIN_IMAGE_LISTING_COVER"

FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("QUANTITY_BUNDLE", (r"买\s*[一二三四五六七八九十\d]+\s*送\s*[一二三四五六七八九十\d]+", r"第[二三四五六七八九十\d]+件", r"[一二三四五六七八九十\d]+件.{0,5}[一二三四五六七八九十\d]+")),
    ("THRESHOLD_DISCOUNT", (r"满\s*\d+(?:\.\d+)?\s*(?:元)?\s*(?:减|立减|省)\s*\d+", r"领券", r"优惠券")),
    ("PRICE", (r"(?:到手|活动|预售|狂欢|会员)?价", r"¥\s*\d+", r"￥\s*\d+", r"\d+(?:\.\d+)?\s*元")),
    ("GIFT", (r"赠(?:送)?", r"加赠", r"赠品", r"送\s*[一二三四五六七八九十\d]+(?:件|包|瓶|份|支|盒)")),
    ("SUBSIDY_REBATE", (r"补贴", r"返\s*\d+", r"直降", r"立减")),
    ("DEPOSIT_FINAL_PAYMENT", (r"定金", r"尾款", r"预售")),
    ("INSTALLMENT", (r"分期", r"免息")),
    ("TIME_LIMIT", (r"限时", r"前\s*\d+\s*(?:小时|分钟|名)", r"\d{1,2}月\d{1,2}日", r"\d{1,2}:\d{2}")),
    ("AUDIENCE_OR_CHANNEL", (r"会员", r"新人", r"直播间", r"学生", r"专享")),
    ("SHIPPING_SERVICE", (r"包邮", r"运费险", r"无理由", r"质保", r"正品")),
    ("RANKING_OR_SALES", (r"top\s*\d+", r"销量", r"热销", r"爆卖", r"累计销售")),
)

SPECIFIC_SIGNAL = re.compile(
    r"\d|[一二三四五六七八九十]+(?:件|包|瓶|份|支|盒|折)|包邮|免息|"
    r"运费险|无理由|正品|质保|买.+送|赠(?:送)?[^，。；]+"
)

CORE_TRANSACTION_FAMILIES = {
    "QUANTITY_BUNDLE",
    "THRESHOLD_DISCOUNT",
    "PRICE",
    "GIFT",
    "SUBSIDY_REBATE",
    "DEPOSIT_FINAL_PAYMENT",
    "INSTALLMENT",
    "SHIPPING_SERVICE",
}

VAGUE_ONLY = {
    "优惠",
    "限时优惠",
    "活动优惠",
    "赠品",
    "有好礼",
    "会员福利",
    "直播间福利",
}


def _fact_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("value") or "").strip()
    return str(value or "").strip()


def _families(text: str) -> list[str]:
    lowered = text.lower()
    found = [
        family
        for family, patterns in FAMILY_PATTERNS
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)
    ]
    return found or ["GENERAL_CONCRETE_OFFER"]


def _ranking_has_boundary(fact: Any) -> bool:
    if not isinstance(fact, dict):
        return False
    return bool(fact.get("source") and (fact.get("time_scope") or fact.get("period")))


def _normalise_verified_offers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    product = raw.get("product", {})
    offers = product.get("verified_offers", [])
    if not isinstance(offers, list):
        return []
    normalised: list[dict[str, Any]] = []
    for index, fact in enumerate(offers, 1):
        text = _fact_text(fact)
        if not text or text.replace(" ", "") in VAGUE_ONLY or not SPECIFIC_SIGNAL.search(text):
            continue
        families = _families(text)
        if "RANKING_OR_SALES" in families and not _ranking_has_boundary(fact):
            families.remove("RANKING_OR_SALES")
            if not families:
                continue
        normalised.append(
            {
                "id": str(fact.get("id") if isinstance(fact, dict) else f"offer-{index}"),
                "text_verbatim": text,
                "families": families,
                "source": "USER_FACT:product.verified_offers",
                "must_render_complete": True,
                "must_not_invent_missing_condition": True,
            }
        )
    return normalised


def _primary_family(offers: list[dict[str, Any]]) -> str:
    priority = (
        "QUANTITY_BUNDLE",
        "THRESHOLD_DISCOUNT",
        "PRICE",
        "GIFT",
        "SUBSIDY_REBATE",
        "DEPOSIT_FINAL_PAYMENT",
        "INSTALLMENT",
        "TIME_LIMIT",
        "AUDIENCE_OR_CHANNEL",
        "SHIPPING_SERVICE",
    )
    present = {family for offer in offers for family in offer["families"]}
    return next((family for family in priority if family in present), "GENERAL_CONCRETE_OFFER")


def _skeleton(family: str) -> str:
    return {
        "QUANTITY_BUNDLE": "PRODUCT_HERO_WITH_QUANTITY_LADDER_AND_BOTTOM_TRANSACTION_BAND",
        "THRESHOLD_DISCOUNT": "PRODUCT_HERO_WITH_COUPON_CONDITION_AND_BOTTOM_TRANSACTION_BAND",
        "PRICE": "PRODUCT_HERO_WITH_PRICE_ANCHOR_AND_CONDITION_BAND",
        "GIFT": "PRODUCT_HERO_WITH_BOUND_GIFT_MODULE_AND_TRANSACTION_BAND",
        "SUBSIDY_REBATE": "PRODUCT_HERO_WITH_REBATE_EQUATION_AND_CONDITION_BAND",
        "DEPOSIT_FINAL_PAYMENT": "PRODUCT_HERO_WITH_PAYMENT_STAGE_EQUATION",
        "INSTALLMENT": "PRODUCT_HERO_WITH_PAYMENT_TERM_AND_TRUST_ROW",
        "TIME_LIMIT": "PRODUCT_HERO_WITH_TIME_BADGE_AND_TRANSACTION_BAND",
        "AUDIENCE_OR_CHANNEL": "PRODUCT_HERO_WITH_AUDIENCE_BADGE_AND_TRANSACTION_BAND",
    }.get(family, "PRODUCT_HERO_WITH_ONE_OFFER_MODULE_AND_BOTTOM_TRANSACTION_BAND")


def build_promotion_offer_plan(raw: dict[str, Any]) -> dict[str, Any]:
    offers = _normalise_verified_offers(raw)
    core_present = any(
        family in CORE_TRANSACTION_FAMILIES
        for offer in offers
        for family in offer["families"]
    )
    if not offers or not core_present:
        return {
            "status": "INACTIVE_NO_CONCRETE_VERIFIED_OFFER",
            "promotion_page_allowed": False,
            "offer_source_policy": "USER_FACT_ONLY",
            "offers": offers,
            "page_role": SEARCH_MAIN_ROLE,
            "fallback_page_job": "SEARCH_CLICK_HOOK_WITHOUT_PROMOTION",
            "forbidden": [
                "invented price, discount, gift, ranking, deadline or audience condition",
                "a promotional skeleton activated by category convention or sample imagery",
            ],
        }
    primary = _primary_family(offers)
    return {
        "status": "ACTIVE_CONCRETE_VERIFIED_OFFER",
        "promotion_page_allowed": True,
        "offer_source_policy": "USER_FACT_ONLY",
        "page_role": SEARCH_MAIN_ROLE,
        "offers": offers,
        "primary_transaction_family": primary,
        "layout_skeleton": _skeleton(primary),
        "hierarchy": [
            "PRIMARY: complete current product plus one strongest verified transaction hook",
            "SECONDARY: eligibility, threshold, quantity, gift or time condition verbatim",
            "TERTIARY: only verified trust/service facts already present in USER_FACT",
        ],
        "zone_contract": {
            "brand_header": "small current-brand identity; no sample logo",
            "product_zone": "45%–62%; complete current product remains the primary object",
            "offer_zone": "one dominant module; secondary conditions are grouped, never equal-weight badge clutter",
            "transaction_band": "16%–25%; numbers may be strong only when present in USER_FACT",
            "condition_footnote": "retain every eligibility, quantity, time and scope condition without mechanical truncation",
        },
        "campaign_style_inheritance": {
            "fixed": ["current-product palette", "campaign typography families", "photography finish", "shared geometry"],
            "promotion_only_variation": ["higher numeric contrast", "one transaction band", "one matching offer device"],
            "never_default": ["festival red-gold", "livestream blue", "beauty pink", "sample category palette"],
        },
        "device_policy": (
            "A badge, price bubble, coupon card, gift card or time chip is allowed only "
            "when its exact fact type exists in offers; the live-room component board is "
            "a small-device grammar, never a full-page style source."
        ),
        "pre_generation_checks": [
            "price equations must be arithmetically correct and use only stackable mechanisms",
            "gift modules require gift name, quantity or specification, eligibility and any time/stock boundary",
            "preorder modules require the complete deposit, expansion, final-payment, stage-time and final-payable chain",
            "ranking, sales, cashback, mystery-box and free-order claims require source, time scope and applicable rule",
            "missing conditions block the affected module instead of being invented or hidden",
        ],
        "forbidden": [
            "copying sample brand, price, ranking, gift, deadline, color or campaign name",
            "promotion replacing or occluding current-product identity",
            "red/gold, blue or pink applied by template default rather than current-product Campaign Style",
            "equal-weight coupon, price, gift and badge clutter",
            "mechanically truncated Chinese conditions",
        ],
    }


def _offer_prompt(plan: dict[str, Any]) -> str:
    if not plan["promotion_page_allowed"]:
        return (
            "当前商品没有具体且完整的 verified_offers，搜索主图不启用交易模块；"
            "不得生成价格、折扣、赠品、排名、限时、会员或直播权益。"
        )
    offers = "；".join(offer["text_verbatim"] for offer in plan["offers"])
    return "\n".join(
        [
            "【SEARCH MAIN OPTIONAL TRANSACTION MODULE — USER_FACT ONLY】",
            f"搜索主图可使用已核验交易事实；原文={offers}。所有条件逐字完整，不补缺失条件。",
            f"主交易族={plan['primary_transaction_family']}；骨架={plan['layout_skeleton']}。",
            "层级=" + "；".join(plan["hierarchy"]) + "。",
            "区域=" + "；".join(f"{k}:{v}" for k, v in plan["zone_contract"].items()) + "。",
            "套图风格锁：沿用当前商品冻结的色彩、字体、摄影、信息面和共享几何；"
            "搜索主图只提高一个数字/交易锚点的对比，不得自动改成红金、直播蓝或美妆粉。",
            plan["device_policy"],
            "生图前检查=" + "；".join(plan["pre_generation_checks"]) + "。",
            "禁止=" + "；".join(plan["forbidden"]) + "。",
        ]
    )


def build_search_main_image_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """Return an open commercial search-cover grammar, independent of offer presence."""
    offer_plan = build_promotion_offer_plan(raw)
    has_offer = offer_plan["promotion_page_allowed"]
    return {
        "status": "ACTIVE_SEARCH_MAIN_IMAGE",
        "page_role": SEARCH_MAIN_ROLE,
        "set_contract": "ONE_SEARCH_MAIN_IMAGE_PLUS_EXISTING_FIVE_CAROUSEL_IMAGES",
        "primary_objective": "WIN_QUALIFIED_CLICK_IN_SEARCH_OR_RECOMMENDATION_LIST",
        "role_boundary": {
            "search_main_image": "short, fast and precise; earn a qualified click at thumbnail scale",
            "carousel_five": "retain the existing five page jobs for deeper product understanding and purchase persuasion",
        },
        "required_hierarchy": [
            "BRAND: compact current-brand/store identity header",
            "PRIMARY: one complete current-product hero with unmistakable identity",
            "SECONDARY: one strongest verified current-product click hook",
            "EVIDENCE: zero to three short verified proof, specification, trust or transaction modules selected by relevance",
            "CLOSURE: one bottom commercial closure band; transaction when verified, otherwise specification/service/trust",
        ],
        "commercial_frame_contract": {
            "safe_frame": "2%–4%; a coherent outline or container edge, not an empty poster margin",
            "brand_header": "6%–10%; current brand/store only",
            "central_product_and_message_zone": "58%–72%",
            "bottom_closure_band": "16%–24% with a verified offer; 10%–16% without one",
            "product_external_bounds": "about 42%–58% of the full canvas and 50%–70% of canvas height when product form allows",
            "central_zone_balance": "product 50%–65%; click hook 20%–30%; auxiliary modules 8%–18%",
            "scan_path": "two or three deliberate stops: product/hook → evidence anchor → closure band",
            "not_a_fixed_template": "ratios are working bands; choose view, side, overlap, curve and module family from current-product evidence",
        },
        "density_route": (
            "TRANSACTION_FORWARD_SEARCH_COVER" if has_offer else "VALUE_AND_TRUST_SEARCH_COVER"
        ),
        "thumbnail_gate": [
            "the current product category and identity are recognizable before reading small copy",
            "one value hook remains readable in a fast list-page scan",
            "the product is not buried by labels, cards, badges or campaign decoration",
            "copy remains short enough for mobile search results while the page still reads as a commercial listing cover",
            "the brand header and bottom closure remain recognizable as one coherent e-commerce frame",
        ],
        "optional_modules": {
            "core_value": "one verified advantage selected from current-product purchase logic",
            "audience_or_scene": "only when supplied by USER_FACT and useful for filtering qualified buyers",
            "transaction": "activate only from concrete product.verified_offers",
            "trust": "only from verified ranking, sales, qualification, service or authenticity facts with required boundaries",
        },
        "composition_policy": (
            "synthesize one of several commercial search-cover families from current-product evidence: "
            "hero with side hook and bottom band; centered hero with price or specification anchor; "
            "hero with one bound gift/offer card; split hero and proof field; or hero with trust/service closure. "
            "Do not trace a sample layout and do not duplicate carousel page 1's shot, crop, pose, reading path or evidence topology"
        ),
        "campaign_style_policy": (
            "share the same frozen Campaign Style as the five carousel pages while allowing "
            "a search-specific hierarchy and composition"
        ),
        "promotion_offer_routing": offer_plan,
        "forbidden": [
            "reducing the search main image to an airy lifestyle poster or detail-page feature image",
            "treating the commercial frame as a mandatory festival or promotion theme",
            "inventing an offer, ranking, qualification, audience, service or trust label",
            "long-form detail-page explanation, equal-weight badge walls or more than three dominant visual anchors",
            "copying sample brand, facts, palette, typography, layout or product pose",
            "replacing product identity with decoration or text",
            "one undifferentiated plain-black type style across headline, number, evidence and closure",
        ],
    }


def _search_prompt(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "【SEARCH MAIN IMAGE ROUTER v0.1.12 — SEARCH/RECOMMENDATION COVER】",
            "本张是新增搜索主图，不是详情页、第五张收口页或生活方式海报；套图结构=1张搜索主图+原有5张轮播图。",
            "目标=用户快速划过搜索/推荐列表时，在缩略图尺度识别当前商品、读懂一个最强已核验价值点，并产生精准点击。",
            "层级=" + "；".join(plan["required_hierarchy"]) + "。",
            "商业框架=" + "；".join(f"{k}:{v}" for k, v in plan["commercial_frame_contract"].items()) + "。",
            "密度路由=" + plan["density_route"] + "。",
            "缩略图门禁=" + "；".join(plan["thumbnail_gate"]) + "。",
            "构图=" + plan["composition_policy"] + "。",
            "Campaign=" + plan["campaign_style_policy"] + "。",
            _offer_prompt(plan["promotion_offer_routing"]),
            "禁止=" + "；".join(plan["forbidden"]) + "。",
        ]
    )


def prepend_search_main_image(compiled: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(compiled)
    pages = result.get("pages", [])
    if not pages:
        result["search_main_image_contract"] = copy.deepcopy(plan)
        return result
    search_page = copy.deepcopy(pages[0])
    search_page["source_carousel_page_role"] = search_page.get("page_role")
    search_page["index"] = 0
    search_page["page_role"] = SEARCH_MAIN_ROLE
    search_page["output_position"] = 0
    search_page["search_main_image_contract"] = copy.deepcopy(plan)
    search_page["generation_prompt"] = (
        search_page.get("generation_prompt", "").rstrip() + "\n\n" + _search_prompt(plan)
    )
    result["pages"] = [search_page, *pages]
    result["search_main_image_contract"] = copy.deepcopy(plan)
    result["output_set_contract"] = {
        "total_images": len(result["pages"]),
        "search_main_images": 1,
        "carousel_images": len(pages),
        "carousel_pages_preserved": True,
    }
    return result


def apply_promotion_offer_plan(compiled: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Compatibility helper: attach offer facts only to an existing search cover."""
    result = copy.deepcopy(compiled)
    result["promotion_offer_routing_contract"] = copy.deepcopy(plan)
    for page in result.get("pages", []):
        if page.get("page_role") != SEARCH_MAIN_ROLE:
            continue
        page["promotion_offer_routing_contract"] = copy.deepcopy(plan)
        page["generation_prompt"] = page.get("generation_prompt", "").rstrip() + "\n\n" + _offer_prompt(plan)
    return result
