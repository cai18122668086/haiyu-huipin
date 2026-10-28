#!/usr/bin/env python3
"""v0.1.11 full-corpus visual decisions layered on compiled v0.1.7 pages."""

from __future__ import annotations

import copy
from typing import Any


ROLE_BLUEPRINTS = {
    "CAROUSEL_SLOT_1_IDENTITY": {
        "page_job": "PRODUCT_IDENTITY_IN_ONE_CREDIBLE_PRIMARY_SCENE",
        "layout_family": "SINGLE_HERO_SCENE",
        "evidence_hierarchy": [
            "PRIMARY: complete current product and category identity",
            "SECONDARY: one identity-defining feature visible naturally",
            "TERTIARY: one compatible use context",
        ],
        "information_density": "LOW",
        "view_priority": ["FRONT", "THREE_QUARTER", "SIDE_OR_RECLINE"],
        "forbidden": [
            "macro inset or component callout on the opening page",
            "multi-scene grid",
            "copy that competes with the product",
        ],
    },
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT": {
        "page_job": "ONE_CORE_CLAIM_WITH_VISIBLE_PROOF",
        "layout_family": "PRIMARY_PROOF_RELATION_WITH_SECONDARY_MACRO",
        "evidence_hierarchy": [
            "PRIMARY: claim-defining subject, component and relation",
            "SECONDARY: one source-bound macro or alternate proof view",
            "TERTIARY: concise explanation",
        ],
        "information_density": "MEDIUM",
        "view_priority": ["DETAIL", "SIDE_OR_RECLINE", "THREE_QUARTER", "FRONT"],
        "forbidden": [
            "decorative glow as the only proof",
            "text-only selling point",
            "unbound body, ingredient, component or action evidence",
        ],
    },
    "CAROUSEL_SLOT_3_SCENE_BENEFIT": {
        "page_job": "VERIFIED_USE_CONTEXT_AND_BENEFIT",
        "layout_family": "ASYMMETRIC_SCENE_SEQUENCE",
        "evidence_hierarchy": [
            "PRIMARY: one dominant verified use scene",
            "SECONDARY: up to two smaller, genuinely different scenes",
            "TERTIARY: short complete scene labels",
        ],
        "information_density": "MEDIUM",
        "view_priority": ["THREE_QUARTER", "FRONT", "SIDE_OR_RECLINE"],
        "forbidden": [
            "equal-size three-card grid by default",
            "line icons replacing photographic scenes",
            "same scene duplicated with different labels",
        ],
    },
    "CAROUSEL_SLOT_4_DETAIL_PROOF": {
        "page_job": "STRUCTURE_OR_FUNCTION_EVIDENCE",
        "layout_family": "HERO_COMPONENT_WITH_GRADED_EVIDENCE",
        "evidence_hierarchy": [
            "PRIMARY: one dominant mechanism, structure or state proof",
            "SECONDARY: one or two bound component windows",
            "TERTIARY: small material, control or connection evidence",
        ],
        "information_density": "MEDIUM_HIGH",
        "view_priority": ["DETAIL", "SIDE_OR_RECLINE", "REAR", "THREE_QUARTER"],
        "forbidden": [
            "four or five equal cards by default",
            "multiple pointers converging on one unrelated component",
            "icons or arrows without current-product evidence",
        ],
    },
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": {
        "page_job": "VALUE_SUMMARY_AND_CONFIDENT_CLOSE",
        "layout_family": "QUIET_HERO_WITH_GRADED_EVIDENCE_STAGE",
        "evidence_hierarchy": [
            "PRIMARY: complete product and final value statement",
            "SECONDARY: one strongest remaining proof",
            "TERTIARY: grouped trust, material, specification or offer facts",
        ],
        "information_density": "MEDIUM",
        "view_priority": ["FRONT", "THREE_QUARTER", "DETAIL"],
        "forbidden": [
            "repeat of the opening-page skeleton",
            "equal-weight thumbnail strip",
            "unverified offer, parameter, certification or efficacy",
        ],
    },
}

UNIVERSAL_PROOF_FORBIDDEN = [
    (
        "line art, outline illustrations, pictograms or wireframes replacing "
        "photographic product, component, content, action or scene evidence"
    ),
    (
        "a selling point inferred from product appearance, category convention "
        "or the reference-photo background instead of verified user text"
    ),
    (
        "copying the original reference photo's wall, floor, furniture, clutter, "
        "lighting, color cast or camera perspective"
    ),
]


CATEGORY_ADAPTERS = [
    (
        "MOTHER_BABY",
        ["母婴", "婴儿", "婴童", "宝宝", "儿童护理", "婴儿床", "婴儿推车", "护栏"],
        "current product, verified age/use boundary, real caregiver-child relation or exact durable component evidence",
        "AGE_BOUNDARY_CONTACT_AND_CARE_TASK",
    ),
    (
        "MEDICAL_HEALTH",
        ["医疗", "健康", "保健", "营养补充", "护理器械", "康复", "药品", "医用"],
        "current package or device, dosage/form/use, verified source, test or certification evidence",
        "EVIDENCE_GRADE_USE_AND_RISK_BOUNDARY",
    ),
    (
        "PET_SUPPLIES",
        ["宠物", "猫粮", "狗粮", "冻干", "猫砂", "宠物清洁", "宠物用品"],
        "current product form, real material state and an animal action that directly participates in the claim",
        "PRODUCT_FORM_MATERIAL_AND_ANIMAL_USE",
    ),
    (
        "AUTOMOTIVE",
        ["汽车用品", "车载", "车用", "车内", "行车", "磁吸支架", "车载充电"],
        "current product in its verified installation position, exact component, contact, movement or compatibility relation",
        "INSTALLATION_COMPONENT_AND_TASK",
    ),
    (
        "FASHION_ACCESSORIES",
        ["服饰", "服装", "鞋履", "鞋", "上衣", "裤", "围巾", "背包", "箱包", "童装"],
        "complete current product or worn state, real fit/scale/action and one material or construction macro",
        "WEAR_FIT_MATERIAL_AND_ACTION",
    ),
    (
        "FOOD",
        ["食品", "零食", "饮料", "饼干", "面", "肉", "茶", "咖啡"],
        "real food, ingredient, serving or action photography",
        "APPETITE_AND_CONSUMPTION",
    ),
    (
        "BEAUTY_PERSONAL_CARE",
        [
            "美妆",
            "护肤",
            "个护",
            "洗护",
            "香水",
            "面膜",
            "洁面",
            "精华",
            "面霜",
            "防晒",
            "唇釉",
            "唇蜜",
            "眼影",
            "洗发",
            "护发",
            "沐浴",
            "身体清洁",
            "头皮护理",
        ],
        "current package, texture, dispensing or verified application evidence",
        "RITUAL_TEXTURE_AND_APPLICATION",
    ),
    (
        "HOME_DAILY_CLEANING",
        [
            "家清",
            "清洁剂",
            "洗衣",
            "洗洁",
            "洁厕",
            "地板清洁",
            "衣物护理",
        ],
        "current product, real cleaning target, contact, foam and rinse evidence",
        "CONTACT_ACTION_AND_RINSE",
    ),
    (
        "SMALL_APPLIANCE",
        [
            "小家电",
            "吸尘器",
            "洗地机",
            "风扇",
            "净水器",
            "剃须刀",
            "料理机",
            "电饭煲",
            "空气净化",
        ],
        "current appliance, real working interface, target contact, control, component and maintenance evidence",
        "WORKING_INTERFACE_COMPONENT_AND_TASK",
    ),
    (
        "DIGITAL_ELECTRONICS",
        ["数码", "电子", "手机", "电脑", "耳机", "鼠标", "键盘", "充电宝", "音箱"],
        "current device state, control, interface or component evidence",
        "INTERACTION_AND_PERFORMANCE",
    ),
    (
        "DURABLE_HOME_OFFICE",
        ["家具", "椅", "桌", "柜", "架", "推车", "收纳", "沙发", "家居"],
        "current structure, human use, spatial fit or multi-state evidence",
        "STRUCTURE_SPACE_AND_HUMAN_USE",
    ),
]


def _category_adapter(raw: dict[str, Any]) -> dict[str, str]:
    product = raw.get("product", {})
    text = f"{product.get('category', '')} {product.get('name', '')}".lower()
    for adapter_id, terms, evidence_priority, expression_family in CATEGORY_ADAPTERS:
        if any(term.lower() in text for term in terms):
            return {
                "adapter_id": adapter_id,
                "evidence_priority": evidence_priority,
                "expression_family": expression_family,
            }
    return {
        "adapter_id": "GENERAL_PRODUCT",
        "evidence_priority": (
            "current-product photography, visible component evidence and "
            "verified use context"
        ),
        "expression_family": "IDENTITY_USE_PROOF_AND_VALUE",
    }


def _supported_views(identity: dict[str, Any]) -> list[str]:
    views = identity.get("view_specific_evidence", {})
    return [
        str(view)
        for view, evidence_ids in views.items()
        if evidence_ids
    ]


def _ordered_views(
    supported: list[str],
    preferred: list[str],
) -> list[str]:
    ordered = [view for view in preferred if view in supported]
    ordered.extend(view for view in supported if view not in ordered)
    return ordered


def _campaign_motif(raw: dict[str, Any]) -> dict[str, Any]:
    preference = raw.get("campaign_preference", {})
    palette = preference.get("palette")
    materials = preference.get("materials")
    tone = preference.get("tone")
    return {
        "selection_inputs": [
            "current product silhouette and material",
            "verified core selling point",
            "verified use scene",
            "campaign preference",
        ],
        "typography_roles": [
            "one campaign display family",
            "one campaign information family",
            "one numeric style only when verified numbers exist",
        ],
        "palette_policy": (
            f"use user preference {palette}"
            if palette
            else "derive restrained text, neutral and accent roles from the "
            "current product and category; never recolor the product"
        ),
        "surface_policy": (
            f"prioritize user-provided materials {materials}"
            if materials
            else "select one category-compatible information-surface material"
        ),
        "tone_policy": (
            f"follow user tone {tone}"
            if tone
            else "choose a category-compatible commercial tone"
        ),
        "geometry_policy": (
            "derive one repeatable line, frame, cut or texture motif from the "
            "current product geometry or verified feature; do not copy a "
            "previous product's motif"
        ),
        "device_limit_per_page": [1, 3],
    }


def _copy_plan(page: dict[str, Any]) -> dict[str, Any]:
    return page.get("audit_contract", {}).get("copy_planner", {})


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "").strip()


def _page_claim_context(page: dict[str, Any]) -> str:
    copy_plan = _copy_plan(page)
    fields = (
        "hero_headline",
        "supporting_line",
        "emphasized_keyword",
        "identity_line",
        "feature_lines",
        "callouts",
        "badges",
    )
    return " ".join(
        _flatten_text(copy_plan.get(field))
        for field in fields
        if copy_plan.get(field)
    ).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _product_form_prior(raw: dict[str, Any]) -> str:
    product = raw.get("product", {})
    text = _flatten_text(
        {
            "name": product.get("name"),
            "category": product.get("category"),
            "attributes": product.get("verified_attributes"),
        }
    ).lower()
    routes = (
        ("VEHICLE_FRAGRANCE", ("车载香薰", "车用香薰", "香薰挂件")),
        (
            "MECHANICAL_MOUNT_OR_CHARGER",
            ("车载支架", "车载手机支架", "手机支架", "磁吸支架", "充电支架"),
        ),
        ("FOOTWEAR", ("鞋履", "跑鞋", "运动鞋", "皮鞋", "拖鞋", "鞋")),
        ("BAG_OR_CARRY_SYSTEM", ("背包", "双肩包", "单肩包", "箱包")),
        ("SCARF_OR_SOFT_ACCESSORY", ("围巾", "披肩", "丝巾")),
        ("APPAREL", ("上衣", "裤", "童装", "服装", "服饰", "外套", "内衣")),
        ("INFANT_DURABLE_GEAR", ("婴儿床", "婴儿推车", "安全座椅", "护栏")),
        ("PET_LITTER", ("猫砂", "宠物砂")),
        ("PET_FOOD", ("猫粮", "狗粮", "宠物粮", "冻干", "宠物罐头")),
        ("PET_CARE_CLEANSING", ("宠物清洁", "宠物洗护", "宠物沐浴")),
        (
            "PET_FEEDING_DEVICE",
            ("宠物智能喂食器", "智能喂食器", "自动喂食器", "喂食器"),
        ),
        ("SUPPLEMENT_OR_HEALTH_CONSUMABLE", ("营养补充", "保健品", "胶囊", "片剂", "口服液")),
        ("APPLIANCE_WITH_WORKING_INTERFACE", ("吸尘器", "洗地机", "风扇", "净水器", "剃须刀", "料理机", "电饭煲")),
        ("DEVICE_WITH_CONTROLS", ("鼠标", "键盘", "手机", "电脑", "充电宝", "智能设备")),
        ("WEARABLE_OR_AUDIO_DEVICE", ("耳机", "耳麦", "智能手表", "手环")),
        ("TRIGGER_OR_ATOMIZING_SPRAY", ("喷雾", "喷瓶", "喷剂", "喷头")),
        ("PUMP_OR_DISPENSER_BOTTLE", ("泵瓶", "泵头", "按压泵", "按压瓶")),
        ("DROPPER_BOTTLE", ("滴管", "安瓶")),
        ("SQUEEZE_TUBE", ("软管", "挤压管")),
        ("JAR_OR_POT", ("罐装", "面霜罐", "膏霜罐")),
        ("POD_OR_CAPSULE", ("凝珠", "洗衣珠")),
        (
            "LIP_COLOR_APPLICATOR",
            ("唇泥", "口红", "唇彩", "唇釉", "唇蜜", "唇颊"),
        ),
        ("COMPACT_OR_APPLICATOR", ("眼影盘", "粉盘", "刷头", "唇釉")),
        ("POUCH_BOX_OR_SHEET", ("袋装", "盒装", "湿巾", "纸品", "面膜")),
    )
    for form, terms in routes:
        if _contains_any(text, terms):
            return form
    if _contains_any(text, ("瓶", "液", "凝露", "啫喱", "乳")):
        return "GENERAL_BOTTLED_OR_FLUID_PRODUCT"
    return "GENERAL_CURRENT_PRODUCT_FORM"


def _validated_category_expression_priors(product_form: str) -> dict[str, Any]:
    """Return optional evidence grammars, never a page template or fact source."""
    common = {
        "authority": "USER_VALIDATED_NON_BINDING_REFERENCE",
        "mandatory_template": False,
        "fixed_page_sequence": False,
        "fixed_layout": False,
        "fixed_palette": False,
        "fixed_typography": False,
        "may_create_new_expression": True,
        "selection_rule": (
            "select, combine, reorder or reject candidates only after resolving "
            "the current verified claim, buyer objection, same-product evidence, "
            "supported view and page-specific proof duty"
        ),
        "transfer_boundary": (
            "never transfer sample brand, SKU, copy, number, color, product pose, "
            "camera, page order or complete skeleton"
        ),
    }
    if product_form == "PET_LITTER":
        return {
            **common,
            "prior_id": "PET_LITTER_OPEN_EVIDENCE_GRAMMAR_V012",
            "identity_invariants": [
                "current package silhouette, print zones and variant markings",
                "current litter granule shape, color and size distribution",
            ],
            "candidate_evidence_relations": [
                "complete package plus animal-to-litter scale or toileting relation",
                "real granules plus a bound pour, scoop or low-dust observation",
                "real clump plus scoop, lift, release or basin-contact relation",
                "verified material or odor claim plus real animal-use scene and text boundary",
                "verified bundle offer plus exact package count or text-only quantity hierarchy",
            ],
            "typography_prior": (
                "derive headline force and accent from the current package; keep facts "
                "and offer arithmetic highly legible without imposing pet-cartoon type"
            ),
            "forbidden_shortcuts": [
                "decorative cat used as proof without a litter-use or scale relation",
                "dust, odor or clumping result invented from category convention",
                "sample package count or bundle copied into another product",
            ],
        }
    if product_form == "APPAREL":
        return {
            **common,
            "prior_id": "APPAREL_WEAR_MATERIAL_STRUCTURE_GRAMMAR_V012",
            "identity_invariants": [
                "complete current silhouette, length, fit and closure relation",
                "current hood, brim, face cover, cuff, pocket, pleat and seam geometry",
            ],
            "candidate_evidence_relations": [
                "complete worn identity in one verified primary scene",
                "real protective or coverage action bound to the exact garment parts",
                "material macro paired with the same garment and a bounded sensory claim",
                "rear, pocket, pleat, seam or ventilation detail bound to the real location",
                "several verified scenes with genuinely different actions and views",
            ],
            "typography_prior": (
                "derive type color, weight and rhythm from the current garment, audience "
                "and scene; allow restrained product-color emphasis and reject default "
                "pure-black heavy headlines when they conflict with the material character"
            ),
            "forbidden_shortcuts": [
                "outdoor-hard typography imposed on every apparel product",
                "water, wind, ice or sunlight metaphor used as sole functional proof",
                "repeating one model pose while only changing copy",
            ],
        }
    if product_form == "LIP_COLOR_APPLICATOR":
        return {
            **common,
            "prior_id": "LIP_COLOR_IDENTITY_TEXTURE_APPLICATION_GRAMMAR_V012",
            "identity_invariants": [
                "same-product container length-to-diameter proportion",
                "cap/body partition, equal or unequal diameter, band and label placement",
                "applicator, neck and opening relation when supported by source views",
            ],
            "candidate_evidence_relations": [
                "one complete current container as identity hero",
                "real applicator and product smear for texture and finish",
                "verified shade-to-swatch binding with one label per swatch",
                "real lip or cheek application action without unsupported before-after result",
                "verified audience or scene guidance paired with bound shade samples",
            ],
            "typography_prior": (
                "derive editorial tone and text color from the current package and shades; "
                "keep shade codes and names readable and subordinate decoration to texture"
            ),
            "forbidden_shortcuts": [
                "beautifying the container by stretching, compressing or changing cap ratio",
                "reusing sample shade codes, names or colors for another product",
                "turning a lip-cheek action into an efficacy before-after claim",
            ],
        }
    return {
        **common,
        "authority": "NONE",
        "prior_id": "NONE",
        "identity_invariants": [],
        "candidate_evidence_relations": [],
        "typography_prior": "derive from the current product and page claim",
        "forbidden_shortcuts": [],
    }


def _category_evidence_contract(
    adapter_id: str,
    product_form: str,
) -> dict[str, Any]:
    """Nominate evidence relations without creating a fact or fixed layout."""
    contract: dict[str, Any] = {
        "proof_relation": (
            "current complete product → one claim-defining component, material, "
            "action or verified scene relation → bounded result or complete text duty"
        ),
        "action_sequence": (
            "show one current-product state and the real component, subject or "
            "target relation that best resolves the buyer objection"
        ),
        "subject_evidence_role": (
            "a person, animal or scene is allowed only when it proves action, "
            "scale, fit, contact, target or verified use context"
        ),
        "target_object": (
            "the exact current-product component, material, content, control or "
            "verified use target named by this atomic claim"
        ),
        "risk_boundary": (
            "do not strengthen a claim beyond USER_FACT or current-product evidence"
        ),
        "topology": (
            "CURRENT_PRODUCT → CLAIM_DEFINING_VISIBLE_RELATION → "
            "BOUND_RESULT_OR_COMPLETE_TEXT_DUTY"
        ),
        "forbidden": [],
    }
    if adapter_id in ("DIGITAL_ELECTRONICS", "SMALL_APPLIANCE"):
        contract.update(
            {
                "proof_relation": (
                    "current device/appliance → real working interface, control, "
                    "target contact or exact component → verified operating state"
                ),
                "action_sequence": (
                    "show the current product before/during one real operation; "
                    "bind the visible control or component to only the verified state"
                ),
                "topology": (
                    "CURRENT_DEVICE → REAL_CONTROL_OR_WORKING_INTERFACE → "
                    "VISIBLE_STATE_OR_TASK → VERIFIED_PARAMETER_BOUNDARY"
                ),
                "target_object": (
                    "the exact current device control, interface, working component, "
                    "contact target or maintenance part"
                ),
                "forbidden": [
                    "generic blue energy, airflow or glow used as the only performance proof",
                    "a parameter detached from its real control, component or task",
                ],
            }
        )
    elif adapter_id == "AUTOMOTIVE":
        contract.update(
            {
                "proof_relation": (
                    "current accessory → verified in-vehicle installation and scale → "
                    "exact attachment, axis, contact, compatibility or use action"
                ),
                "action_sequence": (
                    "establish where it installs, then show one exact component/action "
                    "that resolves stability, reach, angle, compatibility or use"
                ),
                "topology": (
                    "CURRENT_ACCESSORY → VERIFIED_INSTALLATION_POSITION → "
                    "EXACT_COMPONENT_OR_CONTACT → BOUNDED_TASK_RESULT"
                ),
                "target_object": (
                    "the verified installation surface, mount, joint, charging contact, "
                    "phone/device or in-vehicle use position"
                ),
                "forbidden": [
                    "magnetic field or energy ring replacing installation and component proof",
                    "compatibility inferred from a decorative device array",
                ],
            }
        )
    elif adapter_id == "FASHION_ACCESSORIES":
        contract.update(
            {
                "proof_relation": (
                    "complete current product or worn state → real fit, scale, wear/action "
                    "or loading relation → one current material/construction macro"
                ),
                "action_sequence": (
                    "show the complete silhouette or product first, then one real wear, "
                    "movement, opening, loading or material relation"
                ),
                "topology": (
                    "COMPLETE_PRODUCT_OR_WORN_STATE → FIT_OR_REAL_ACTION → "
                    "MATERIAL_OR_CONSTRUCTION_MACRO → TEST_OR_TEXT_BOUNDARY"
                ),
                "target_object": (
                    "the current garment/shoe/bag silhouette, fit zone, opening, pocket, "
                    "sole, fabric, seam, closure or loading relation"
                ),
                "forbidden": [
                    "water waves, snowflakes, rocks or landscapes used as functional proof",
                    "invented colors, sizes, materials, sole layers or garment construction",
                ],
            }
        )
    elif adapter_id == "MEDICAL_HEALTH":
        contract.update(
            {
                "proof_relation": (
                    "current package, dosage/form or device → verified use and audience → "
                    "source, test, certification or complete claim boundary"
                ),
                "action_sequence": (
                    "show who uses what form and how, then attach only verified source, "
                    "test or certification evidence; never stage a guaranteed outcome"
                ),
                "topology": (
                    "CURRENT_PRODUCT_FORM → VERIFIED_USE_AND_AUDIENCE → "
                    "EVIDENCE_GRADE → COMPLETE_RISK_AND_RESULT_BOUNDARY"
                ),
                "target_object": (
                    "the current dosage/form, package, device contact point, verified "
                    "use step, source, test or certification object"
                ),
                "risk_boundary": (
                    "clinical, diagnostic, treatment and result language remains exactly "
                    "within USER_FACT, compliance and verified evidence level"
                ),
                "forbidden": [
                    "clinical blue-white styling or a body diagram used as authority",
                    "unverified doctor, certification, test, ranking or guaranteed result",
                ],
            }
        )
    elif adapter_id == "MOTHER_BABY":
        contract.update(
            {
                "proof_relation": (
                    "current product → verified age/contact/use boundary → real caregiver-child "
                    "action, scale or exact durable component → bounded experience/function"
                ),
                "action_sequence": (
                    "show the correct-age subject or caregiver only when the action proves "
                    "contact, scale, fit, movement or the exact structure"
                ),
                "topology": (
                    "CURRENT_PRODUCT → AGE_AND_CONTACT_BOUNDARY → REAL_CARE_TASK_OR_"
                    "EXACT_COMPONENT → EXPERIENCE_FUNCTION_TRUST_SEPARATION"
                ),
                "target_object": (
                    "the current child-care contact area, age/scale relation, wheel, joint, "
                    "guard, support, lock, package or verified material"
                ),
                "risk_boundary": (
                    "safety, age, sensitivity, formula, certification and efficacy must remain "
                    "separate verified facts; cute atmosphere proves none of them"
                ),
                "forbidden": [
                    "bubbles, clouds, cartoons or pastel color used as safety proof",
                    "a child or caregiver who does not prove age, scale, action or contact",
                ],
            }
        )
    elif adapter_id == "PET_SUPPLIES":
        contract.update(
            {
                "proof_relation": (
                    "current product form → real food, litter, foam, component or material state → "
                    "animal use/action or cleaning task → verified result boundary"
                ),
                "action_sequence": (
                    "route by pet food, litter, cleansing, supplement or device form; use the "
                    "animal only when feeding, toileting, contact or scale proves the claim"
                ),
                "topology": (
                    "CURRENT_PET_PRODUCT_FORM → REAL_MATERIAL_STATE → "
                    "ANIMAL_USE_OR_CARE_TASK → VERIFIED_NUTRITION_HYGIENE_BOUNDARY"
                ),
                "target_object": (
                    "the current food piece, kibble, litter granule/clump, foam, care target, "
                    "device component or animal-use relation"
                ),
                "forbidden": [
                    "an animal used as decoration without participating in the claim",
                    "invented breed, nutrition number, low-dust, deodorizing or safety result",
                ],
            }
        )
    if product_form == "VEHICLE_FRAGRANCE":
        contract["proof_relation"] = (
            "current fragrance product → verified wick/wood-stop/contact and hanging position → "
            "subordinate scent-source or context cue → complete text duty"
        )
        contract["topology"] = (
            "CURRENT_VEHICLE_FRAGRANCE → REAL_WICK_OR_CONTACT → "
            "VERIFIED_USE_POSITION → SUBORDINATE_SCENT_CUE"
        )
    elif product_form == "PET_FEEDING_DEVICE":
        contract.update(
            {
                "proof_relation": (
                    "current feeder → exact food window, control, outlet or bowl → "
                    "real dispense, cat-use or maintenance relation → text boundary "
                    "for timing, portion and suitability"
                ),
                "action_sequence": (
                    "show one exact device relation: food level is visible through the "
                    "current window, real food dispenses to the current bowl, a cat uses "
                    "the bowl, or a hand removes/cleans the current bowl"
                ),
                "subject_evidence_role": (
                    "a cat may prove feeding action and scale but cannot alone prove full "
                    "suitability; a human hand may prove removal or cleaning only"
                ),
                "target_object": (
                    "the current food window, control evidence, outlet, bowl, bowl connection, "
                    "dispense relation, cat-use relation or maintenance action"
                ),
                "risk_boundary": (
                    "timing, portion and suitability remain complete USER_FACT unless a "
                    "matching current control/interface or audience boundary is visible"
                ),
                "topology": (
                    "CURRENT_PET_FEEDING_DEVICE → EXACT_WINDOW_CONTROL_OUTLET_OR_BOWL → "
                    "REAL_DISPENSE_CAT_USE_OR_MAINTENANCE → TEXT_ONLY_SETTING_AND_"
                    "SUITABILITY_BOUNDARY"
                ),
                "forbidden": list(
                    dict.fromkeys(
                        contract["forbidden"]
                        + [
                            "invented app, screen, button state, timer, portion number or capacity",
                            "food, nutrition or palatability claims inferred from the device",
                        ]
                    )
                ),
            }
        )
    return contract


def _claim_families_for_text(
    text: str,
    *,
    include_identity: bool = False,
) -> list[str]:
    text = text.lower()
    routes = (
        (
            "OFFER_SPEC_GIFT",
            ("价格", "优惠", "折扣", "到手", "赠", "规格", "容量", "件装"),
        ),
        (
            "TRUST_CERTIFICATION_DATA",
            ("认证", "检测", "报告", "专利", "排名", "%", "倍", "小时"),
        ),
        (
            "SAFETY_AUDIENCE_SUITABILITY",
            ("温和", "敏感", "儿童", "婴儿", "孕", "适龄", "适用", "人群", "无刺激", "安全"),
        ),
        (
            "FIT_COMPATIBILITY_SIZE",
            ("兼容", "适配", "尺寸", "尺码", "大小", "版型", "合身", "安装位置", "机型"),
        ),
        (
            "CONTROL_INTERACTION_STATE",
            (
                "按键",
                "控制",
                "切换",
                "遥控",
                "界面",
                "显示",
                "调节",
                "设置",
                "定时",
                "份量",
                "换向",
                "折叠",
                "开合",
            ),
        ),
        (
            "STRUCTURE_COMPONENT_MECHANISM",
            (
                "结构",
                "部件",
                "关节",
                "转轴",
                "鞋底",
                "轮组",
                "刷头",
                "刀头",
                "接口",
                "隔层",
                "余粮窗",
                "视窗",
                "出粮口",
                "食盆",
                "支撑",
                "余粮窗",
                "视窗",
                "出粮口",
                "食盆",
            ),
        ),
        (
            "MATERIAL_CRAFT_CONSTRUCTION",
            ("面料", "织物", "材质", "做工", "工艺", "缝线", "皮革", "金属", "木塞"),
        ),
        (
            "MAINTENANCE_HYGIENE",
            ("拆洗", "清洗", "清理", "维护", "倒尘", "卫生", "可水洗", "易清洁"),
        ),
        (
            "NUTRITION_SOURCE_PROCESS",
            ("营养", "蛋白", "含肉", "原料", "产地", "冻干", "低温工艺", "配比"),
        ),
        (
            "PORTABILITY_SPACE_TASK",
            ("便携", "收纳", "搬运", "空间", "容量", "装载", "背负", "车内", "桌面"),
        ),
        (
            "TECHNOLOGY_MECHANISM",
            ("技术", "微胶囊", "配方", "机理", "科技", "technology"),
        ),
        (
            "INGREDIENT_MATERIAL",
            (
                "成分",
                "果酸",
                "烟酰胺",
                "精华",
                "提取",
                "材质",
                "配方",
                "ingredient",
            ),
        ),
        (
            "SENSORY_EXPERIENCE",
            (
                "香",
                "气味",
                "清新",
                "清爽",
                "不假滑",
                "不紧绷",
                "柔软",
                "舒适",
                "凉感",
                "fragrance",
                "scent",
            ),
        ),
        (
            "TEXTURE_SURFACE_AFTERFEEL",
            (
                "透明",
                "啫喱",
                "凝露",
                "泡沫",
                "质地",
                "流动",
                "水感",
                "乳霜",
                "粉质",
                "珠光",
                "肤感",
                "表面",
            ),
        ),
        (
            "USE_ACTION_APPLICATION",
            (
                "按压",
                "喷",
                "挤",
                "涂",
                "擦",
                "揉",
                "冲洗",
                "起泡",
                "使用",
                "上妆",
                "出粮",
                "喂食",
                "swatch",
            ),
        ),
        (
            "PERFORMANCE_OR_BOUNDED_RESULT",
            (
                "清洁",
                "洗净",
                "去污",
                "去油",
                "油污",
                "去除",
                "防晒",
                "保湿",
                "控油",
                "改善",
                "修复",
                "提亮",
                "持久",
            ),
        ),
    )
    families = [
        family
        for family, terms in routes
        if _contains_any(text, terms)
    ]
    if include_identity:
        families.insert(0, "IDENTITY_POSITIONING")
    return families or ["IDENTITY_OR_GENERAL_VALUE"]


def _claim_families(page: dict[str, Any]) -> list[str]:
    return _claim_families_for_text(
        _page_claim_context(page),
        include_identity=(
            page.get("page_role") == "CAROUSEL_SLOT_1_IDENTITY"
        ),
    )


def _atomic_page_claims(page: dict[str, Any]) -> list[dict[str, str]]:
    copy_plan = _copy_plan(page)
    fields = (
        "hero_headline",
        "supporting_line",
        "emphasized_keyword",
        "identity_line",
        "feature_lines",
        "callouts",
        "badges",
    )
    units: list[dict[str, str]] = []
    seen: set[str] = set()
    for field in fields:
        value = copy_plan.get(field)
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            text = _flatten_text(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            units.append({"field": field, "text": text})
    return units


def _primary_claim_family(families: list[str]) -> str:
    priority = (
        "OFFER_SPEC_GIFT",
        "TRUST_CERTIFICATION_DATA",
        "SAFETY_AUDIENCE_SUITABILITY",
        "FIT_COMPATIBILITY_SIZE",
        "CONTROL_INTERACTION_STATE",
        "STRUCTURE_COMPONENT_MECHANISM",
        "MATERIAL_CRAFT_CONSTRUCTION",
        "MAINTENANCE_HYGIENE",
        "NUTRITION_SOURCE_PROCESS",
        "PORTABILITY_SPACE_TASK",
        "INGREDIENT_MATERIAL",
        "TECHNOLOGY_MECHANISM",
        "SENSORY_EXPERIENCE",
        "TEXTURE_SURFACE_AFTERFEEL",
        "USE_ACTION_APPLICATION",
        "PERFORMANCE_OR_BOUNDED_RESULT",
        "IDENTITY_POSITIONING",
        "IDENTITY_OR_GENERAL_VALUE",
    )
    return next(
        (family for family in priority if family in families),
        families[0],
    )


def _claim_observability(
    page: dict[str, Any],
    feature: dict[str, Any],
) -> list[str]:
    text = _page_claim_context(page)
    modes: list[str] = []
    families = (
        (
            "DIRECTLY_VISIBLE",
            (
                "透明",
                "啫喱",
                "凝露",
                "泡沫",
                "质地",
                "材质",
                "结构",
                "部件",
                "颜色",
                "纹理",
                "按键",
                "接口",
                "分区",
                "容量",
                "版型",
                "长度",
                "颜色",
                "口袋",
                "开合",
                "轮组",
                "关节",
                "颗粒",
                "肉块",
                "隔层",
                "texture",
                "material",
                "structure",
            ),
        ),
        (
            "ACTION_DEMONSTRABLE",
            (
                "按压",
                "泵出",
                "释放",
                "喷",
                "挤",
                "涂",
                "擦",
                "揉",
                "清洁",
                "洗净",
                "冲洗",
                "防晒",
                "起泡",
                "泡沫",
                "调节",
                "切换",
                "移动",
                "装载",
                "承托",
                "操作",
                "使用",
                "穿脱",
                "背负",
                "喂食",
                "结团",
                "清理",
                "折叠",
                "换向",
                "安装",
                "出粮",
                "喂食",
                "拆洗",
                "dispense",
                "wash",
                "switch",
                "adjust",
            ),
        ),
        (
            "SENSORY_PROXY_ONLY",
            (
                "香",
                "香氛",
                "气味",
                "酸甜",
                "爆汁",
                "清爽",
                "不假滑",
                "不紧绷",
                "舒适",
                "柔软",
                "口感",
                "鲜活",
                "fragrance",
                "scent",
                "aroma",
                "comfort",
            ),
        ),
        (
            "TEXT_ONLY_VERIFIED",
            (
                "微胶囊",
                "技术",
                "配方",
                "成分",
                "果酸",
                "烟酰胺",
                "认证",
                "留香",
                "续航",
                "兼容",
                "适龄",
                "尺码",
                "认证",
                "检测",
                "营养",
                "蛋白",
                "防晒指数",
                "防水等级",
                "速干时间",
                "定时",
                "份量",
                "technology",
                "formula",
                "ingredient",
                "certified",
            ),
        ),
        (
            "RESULT_NOT_VISUALLY_PROVABLE_WITH_CURRENT_EVIDENCE",
            (
                "改善",
                "淡化",
                "美白",
                "暗沉",
                "粗糙",
                "小疙瘩",
                "抗氧",
                "修复",
                "紧致",
                "焕白",
                "更持久",
                "long-lasting",
                "improve",
                "repair",
                "治疗",
                "治愈",
                "保证",
                "抗菌",
                "除臭",
                "低尘",
            ),
        ),
    )
    for mode, terms in families:
        if _contains_any(text, terms):
            modes.append(mode)
    if not modes:
        modes.append(
            "DIRECTLY_VISIBLE"
            if feature.get("proof_bindings")
            else "TEXT_ONLY_VERIFIED"
        )
    return modes


def _claim_visualization_contract(
    page: dict[str, Any],
    raw: dict[str, Any],
    blueprint: dict[str, Any],
    feature: dict[str, Any],
    buyer_objection: str,
) -> dict[str, Any]:
    claim = _verified_page_claim(page)
    text = _page_claim_context(page)
    modes = _claim_observability(page, feature)
    adapter = _category_adapter(raw)
    product_form = _product_form_prior(raw)
    category_evidence = _category_evidence_contract(
        adapter["adapter_id"],
        product_form,
    )
    claim_families = _claim_families(page)
    primary_claim_family = _primary_claim_family(claim_families)
    is_fragrance = _contains_any(
        text,
        ("香", "香氛", "气味", "酸甜", "爆汁", "fragrance", "scent"),
    )
    is_cleansing = _contains_any(
        text,
        (
            "清洁",
            "洗净",
            "冲洗",
            "泡沫",
            "洗掉",
            "卸防晒",
            "去污",
            "去油",
            "油污",
            "擦净",
            "wash",
        ),
    )
    is_operation = "ACTION_DEMONSTRABLE" in modes
    is_sensory = "SENSORY_PROXY_ONLY" in modes
    is_text_only = "TEXT_ONLY_VERIFIED" in modes
    result_unproven = (
        "RESULT_NOT_VISUALLY_PROVABLE_WITH_CURRENT_EVIDENCE" in modes
    )
    is_personal_care = adapter["adapter_id"] == "BEAUTY_PERSONAL_CARE"
    is_home_cleaning = adapter["adapter_id"] == "HOME_DAILY_CLEANING"
    is_task_specific_category = adapter["adapter_id"] in {
        "DIGITAL_ELECTRONICS",
        "SMALL_APPLIANCE",
        "AUTOMOTIVE",
        "FASHION_ACCESSORIES",
        "MEDICAL_HEALTH",
        "MOTHER_BABY",
        "PET_SUPPLIES",
    }

    if is_fragrance:
        primary_evidence = (
            "当前商品身份、当前可见质地与核验使用接触点形成连续摄影关系；"
            "香味本身不冒充可见物理证据，包装喷口也不冒充香气释放起点"
        )
        action_sequence = (
            "当前商品正常取用 → 当前质地接触手、皮肤、织物、清洁对象或"
            "核验介质 → 经揉搓、起泡、喷洒或使用动作后，从真实接触点"
            "出现克制的感官扩散；仅在喷雾本身是核验功能时允许喷口起雾"
        )
        product_subject_relation = (
            "商品、取用动作、真实质地和接触介质必须处于同一可信空间；"
            "感官扩散必须绑定使用接触点，不得画成泵嘴液柱或水果爆炸"
        )
    elif is_cleansing:
        primary_evidence = (
            "当前商品、真实清洁对象、产品接触/绵密泡沫与水流冲洗或"
            "核验残留被带走的连续摄影关系"
        )
        action_sequence = (
            "当前商品接触真实清洁对象 → 可见泡沫或清洁动作 → 水流冲洗"
            "并带走已核验残留；同一主体、同一光线，不改变肤色或材质"
        )
        product_subject_relation = (
            "商品作为事实锚点，清洁对象与泡沫/水流作为主证明；不得用"
            "图标、箭头或虚假前后效果替代真实接触和冲洗"
        )
    elif is_task_specific_category:
        primary_evidence = category_evidence["proof_relation"]
        action_sequence = category_evidence["action_sequence"]
        product_subject_relation = (
            category_evidence["subject_evidence_role"]
            + "；当前商品身份、结构、颜色与相对几何保持权威"
        )
    elif is_operation:
        primary_evidence = (
            "当前商品、真实操作者或使用对象、可见控制/部件及动作结果"
            "处于同一连续摄影关系"
        )
        action_sequence = (
            "动作前的当前商品状态 → 对真实控制或部件的操作 → 核验后的"
            "可见状态；只呈现当前证据支持的变化"
        )
        product_subject_relation = (
            "操作者、当前商品和结果状态形成一个明确接触或空间关系，"
            "商品几何与部件位置保持权威"
        )
    else:
        primary_evidence = (
            f"{adapter['evidence_priority']}；选择一个与本页主张直接对应的"
            "当前商品、部件、内容物或核验场景摄影关系"
        )
        action_sequence = (
            "不强行动作化；使用一个证据支持的当前商品状态和一个最清楚"
            "的真实部件、内容物或场景关系"
        )
        product_subject_relation = (
            "商品始终为事实锚点；任何主体、道具或环境只服务于本页已核验"
            "主张，不引入新事实"
        )

    if is_fragrance:
        secondary_sensory_cue = (
            "从真实使用接触点向外，用与当前材质和核验香型相容的低比例"
            "半透明雾、水汽、柔软流带、液滴、泡沫、光泽或色彩联想辅助"
            "感受；原料线索低比例、景深融合且必须匹配当前核验香型，"
            "不承担事实证明"
        )
    elif is_sensory:
        secondary_sensory_cue = (
            "只用与当前可见材质相容的流动、湿润、柔软、冷暖、泡沫、"
            "折射或空气感作为克制代理；不得把肤感、舒适或凉感画成"
            "未经证实的物理装置、成分或结果"
        )
    else:
        secondary_sensory_cue = (
            "NONE_REQUIRED；除非它能强化当前真实材质且不替代主证据"
        )
    text_only_fact = (
        "不可直接看见的技术、配方、成分、持续时间或认证只以完整的"
        "USER_FACT文案出现；不得画成伪显微结构、分子、渗透路径或计时数据"
        if is_text_only
        else "NONE_REQUIRED；文字只解释已经可见的真实关系"
    )
    result_boundary = (
        "当前证据没有用户提供的真实前后对照时，不生成肤色、粗糙、暗沉、"
        "疙瘩、修复或持久性的可见结果变化；保留为克制的事实文案或"
        "显式证据缺口"
        if result_unproven
        else "不得把视觉表现扩写为用户未核验的结果或功效"
    )
    emphasized = (
        _copy_plan(page).get("emphasized_keyword")
        or claim.get("text")
        or "本页核心关键词"
    )
    if primary_claim_family == "OFFER_SPEC_GIFT":
        headline_tone = (
            "特大核验数字或规格为唯一强调点，配紧凑商业无衬线；"
            "仅用于成交/规格页，不把促销字形带入体验与功效页"
        )
    elif primary_claim_family in (
        "TRUST_CERTIFICATION_DATA",
        "TECHNOLOGY_MECHANISM",
        "INGREDIENT_MATERIAL",
    ) and not is_fragrance:
        headline_tone = (
            "清晰几何无衬线承担事实与数字，可用克制栅格；"
            "不得用伪科学字形、徽章或视觉机制替代证据"
        )
    elif is_fragrance or primary_claim_family == "SENSORY_EXPERIENCE":
        headline_tone = (
            "从当前包装字形、材质和感受选择有表现力的展示宋体、"
            "流动感无衬线或柔和宽体，配清晰信息无衬线；"
            "避免体育斜体、工业硬字和幼态圆体默认"
        )
    elif primary_claim_family == "SAFETY_AUDIENCE_SUITABILITY":
        headline_tone = (
            "圆角友好无衬线或克制中黑体，扩大行距与留白；"
            "安全与人群事实必须完整，不用可爱字形暗示未核验安全结论"
        )
    elif primary_claim_family in (
        "PERFORMANCE_OR_BOUNDED_RESULT",
        "USE_ACTION_APPLICATION",
    ):
        headline_tone = (
            "按当前包装与主张强度选择重黑、窄体或几何无衬线，"
            "仅在动作方向确实支持时使用受控倾斜；禁止跨品类硬套"
        )
    elif is_personal_care:
        headline_tone = (
            "从当前包装、材质与页面职责选择编辑感宋黑组合、"
            "流动感无衬线或精致轻字辅层，不设个护统一字体"
        )
    elif is_home_cleaning:
        headline_tone = (
            "按清洁强度与包装材质选择清晰粗黑、受控斜切或高对比字；"
            "香氛/护理型家清可转圆润，禁止一律硬核化"
        )
    elif adapter["adapter_id"] in ("DIGITAL_ELECTRONICS", "SMALL_APPLIANCE"):
        headline_tone = (
            "按当前商品材质、任务和性能主张选择几何无衬线、窄体或清晰中黑；"
            "数字靠近真实接口、控制或工作界面，禁止默认科技蓝和空洞电竞字"
        )
    elif adapter["adapter_id"] == "AUTOMOTIVE":
        headline_tone = (
            "安装、兼容与机械页使用清晰紧凑无衬线；香薰体验页可用克制编辑字；"
            "两种product form不得共享蓝黑科技或暖金香氛默认字体"
        )
    elif adapter["adapter_id"] == "FASHION_ACCESSORIES":
        headline_tone = (
            "从当前服装/鞋包轮廓、面料、受众和claim job选择编辑型宋黑组合、"
            "运动窄体或克制商业无衬线；不得把户外硬字套给所有服饰"
        )
    elif adapter["adapter_id"] == "MEDICAL_HEALTH":
        headline_tone = (
            "使用高可读信息无衬线与严格数字层级；情绪字只服务当前品牌定位，"
            "不得用临床蓝白、医生字样或徽章制造权威"
        )
    elif adapter["adapter_id"] == "MOTHER_BABY":
        headline_tone = (
            "按当前年龄、包材、材质与任务选择友好圆角无衬线或结构清晰中黑；"
            "可爱字形不得承担安全、适龄、认证或功效事实"
        )
    elif adapter["adapter_id"] == "PET_SUPPLIES":
        headline_tone = (
            "食品、猫砂、清洁和设备分别按真实物性与任务选择字体；"
            "活泼字可作情绪层，但营养、检测、规格与用法必须清晰完整"
        )
    else:
        headline_tone = (
            "字体性格由当前商品材质、主感受与受众推导，不使用跨品类"
            "万能高点击字体"
        )
    physical_action_origin = (
        "当前商品内容物与真实手、皮肤、织物、污物、清洁对象或核验介质"
        "的接触点；包装出口只负责正常取用"
        if is_operation
        else "当前可见商品、部件、内容物或核验场景关系"
    )
    sensory_release_origin = (
        (
            "经核验的雾化喷口与真实目标接触区；只展示该剂型真实喷雾，"
            "不得扩大为无依据气味或效果机制"
            if product_form == "TRIGGER_OR_ATOMIZING_SPRAY"
            else "真实使用接触点；非喷雾商品禁止从泵嘴、瓶口或包装表面"
            "凭空喷出香气、长液柱或效果"
        )
        if is_sensory
        else "NONE_REQUIRED"
    )
    claim_visual_family = (
        "SCENT_RELEASE_FROM_USE_CONTACT"
        if is_fragrance
        else "CONTACT_ACTION_MATERIAL_RESULT"
        if is_cleansing
        else "OFFER_OR_VERIFIED_NUMERIC_HERO"
        if primary_claim_family == "OFFER_SPEC_GIFT"
        else "TRUST_OR_VERIFIED_DATA"
        if primary_claim_family == "TRUST_CERTIFICATION_DATA"
        else "TEXT_ONLY_TECH_OR_INGREDIENT_WITH_MATERIAL_ANCHOR"
        if (
            "TECHNOLOGY_MECHANISM" in claim_families
            or "INGREDIENT_MATERIAL" in claim_families
        )
        else "REAL_USE_ACTION_OR_SCENE"
        if primary_claim_family == "USE_ACTION_APPLICATION"
        else "TEXTURE_OR_SURFACE_MACRO"
        if primary_claim_family == "TEXTURE_SURFACE_AFTERFEEL"
        else "REAL_ACTION_OR_CURRENT_STATE"
    )
    role = page.get("page_role")
    if role == "CAROUSEL_SLOT_1_IDENTITY":
        product_scale_range = "45–70% image height; one complete identity anchor"
    elif (
        primary_claim_family == "TEXTURE_SURFACE_AFTERFEEL"
        or "INGREDIENT_MATERIAL" in claim_families
        or "TECHNOLOGY_MECHANISM" in claim_families
    ):
        product_scale_range = (
            "20–40% when a true macro/material field carries the claim; "
            "product remains identifiable"
        )
    elif primary_claim_family in (
        "USE_ACTION_APPLICATION",
        "PERFORMANCE_OR_BOUNDED_RESULT",
        "SENSORY_EXPERIENCE",
    ):
        product_scale_range = (
            "30–55%; real action, target or material transition carries "
            "the primary evidence"
        )
    elif primary_claim_family == "OFFER_SPEC_GIFT":
        product_scale_range = (
            "30–50%; product and one verified number/offer are the two anchors"
        )
    else:
        product_scale_range = "30–55%; one primary proof and one clarifier"
    if is_fragrance:
        evidence_topology = (
            "CURRENT_PRODUCT → VERIFIED_USE_CONTACT → VISIBLE_MATERIAL_MOTION "
            "→ SUBORDINATE_SENSORY_ATMOSPHERE → TEXT_ONLY_TECH_DUTY"
        )
    elif is_cleansing:
        evidence_topology = (
            "CURRENT_PRODUCT → REAL_TARGET_CONTACT → VISIBLE_FOAM_OR_REMOVAL "
            "→ RINSE → BOUNDED_RESULT"
        )
    elif (
        "TECHNOLOGY_MECHANISM" in claim_families
        or "INGREDIENT_MATERIAL" in claim_families
    ):
        evidence_topology = (
            "CURRENT_PRODUCT → VISIBLE_CURRENT_MATERIAL_OR_VERIFIED_SOURCE_CUE "
            "→ COMPLETE_TEXT_FACT → NO_PSEUDO_MECHANISM"
        )
    elif primary_claim_family == "OFFER_SPEC_GIFT":
        evidence_topology = (
            "CURRENT_PRODUCT → VERIFIED_NUMBER_OR_OFFER → COMPACT_LEGAL_OR_"
            "BOUNDARY_COPY"
        )
    elif is_task_specific_category:
        evidence_topology = category_evidence["topology"]
    else:
        evidence_topology = (
            "CURRENT_PRODUCT → REAL_USE_CONTACT_OR_STATE → VISIBLE_ACTION_OR_"
            "MATERIAL_EVIDENCE → BOUNDED_RESULT_OR_TEXT_DUTY"
        )
    cue_budget = {
        "sensory_device_families_max": 1 if is_sensory else 0,
        "ingredient_or_scent_cue_share": (
            "LOW; subordinate to current product and real action"
            if is_sensory
            else "NONE_REQUIRED"
        ),
        "small_fact_points_max": 3,
        "rule": (
            "one large conclusion + one physical/action relation + one to three "
            "short fact points; never scatter equal-weight labels around product"
        ),
    }
    forbidden_substitutes = [
        "伪显微结构、伪分子图、伪渗透路径或无依据时钟数据",
        "用图标、线稿、空洞示意人、装饰光弧代替真实证据",
        "无当前证据的前后效果、肤色或材质变化",
        "多个同权重卡片机械罗列卖点",
    ]
    forbidden_substitutes.extend(category_evidence["forbidden"])
    if is_fragrance:
        forbidden_substitutes.extend(
            [
                "非喷雾商品从泵嘴或瓶口射出长液柱、喷泉或爆炸火花",
                "把香气、香型原料或微胶囊画成肉眼可见实体颗粒",
                "满屏水果/花材拼贴，或使用与当前核验香型不一致的原料",
            ]
        )
    atomic_claims = _atomic_page_claims(page) or [
        {"field": claim.get("field", ""), "text": claim.get("text", "")}
    ]
    claim_units: list[dict[str, Any]] = []
    for atomic in atomic_claims:
        unit_text = atomic["text"]
        unit_families = _claim_families_for_text(
            unit_text,
            include_identity=(
                page.get("page_role") == "CAROUSEL_SLOT_1_IDENTITY"
                and atomic["field"] in ("hero_headline", "identity_line")
            ),
        )
        unit_page = {
            "page_role": page.get("page_role"),
            "audit_contract": {
                "copy_planner": {"hero_headline": unit_text}
            },
        }
        unit_modes = _claim_observability(unit_page, feature)
        if "OFFER_SPEC_GIFT" in unit_families:
            unit_topology = (
                "CURRENT_PRODUCT → VERIFIED_OFFER_OR_SPEC → BOUNDARY_COPY"
            )
            unit_grammar = "OFFER_OR_VERIFIED_NUMERIC_HERO"
        elif "TRUST_CERTIFICATION_DATA" in unit_families:
            unit_topology = (
                "CURRENT_PRODUCT → VERIFIED_DATA_OR_SEAL → SUPPORTING_TEXT"
            )
            unit_grammar = "TRUST_OR_VERIFIED_DATA"
        elif (
            "TECHNOLOGY_MECHANISM" in unit_families
            or "INGREDIENT_MATERIAL" in unit_families
        ):
            unit_topology = (
                "CURRENT_PRODUCT → VISIBLE_CURRENT_MATERIAL_OR_VERIFIED_"
                "SOURCE_CUE → COMPLETE_TEXT_FACT → NO_PSEUDO_MECHANISM"
            )
            unit_grammar = (
                "TEXT_ONLY_TECH_OR_INGREDIENT_WITH_MATERIAL_ANCHOR"
            )
        elif "SENSORY_EXPERIENCE" in unit_families:
            unit_topology = (
                "CURRENT_PRODUCT → REAL_USE_OR_MATERIAL_STATE → "
                "SUBORDINATE_SENSORY_PROXY → COMPLETE_TEXT_FACT"
            )
            unit_grammar = "SENSORY_FROM_REAL_USE_OR_MATERIAL"
        elif (
            "USE_ACTION_APPLICATION" in unit_families
            or "PERFORMANCE_OR_BOUNDED_RESULT" in unit_families
        ):
            unit_topology = (
                "CURRENT_PRODUCT → REAL_TARGET_ACTION → VISIBLE_MATERIAL_"
                "STATE → BOUNDED_RESULT"
            )
            unit_grammar = "ACTION_MATERIAL_BOUNDED_RESULT"
        elif "TEXTURE_SURFACE_AFTERFEEL" in unit_families:
            unit_topology = (
                "CURRENT_PRODUCT → VISIBLE_TEXTURE_OR_SURFACE_MACRO → "
                "VERIFIED_APPLICATION_RELATION"
            )
            unit_grammar = "TEXTURE_OR_APPLICATION_MACRO"
        elif any(
            family in unit_families
            for family in (
                "FIT_COMPATIBILITY_SIZE",
                "CONTROL_INTERACTION_STATE",
                "STRUCTURE_COMPONENT_MECHANISM",
                "MATERIAL_CRAFT_CONSTRUCTION",
                "MAINTENANCE_HYGIENE",
                "NUTRITION_SOURCE_PROCESS",
                "PORTABILITY_SPACE_TASK",
                "SAFETY_AUDIENCE_SUITABILITY",
            )
        ):
            unit_topology = category_evidence["topology"]
            unit_grammar = adapter["expression_family"]
        else:
            unit_topology = (
                "CURRENT_PRODUCT → ONE_VISIBLE_DEFINING_CUE → TEXT_DUTY"
            )
            unit_grammar = "IDENTITY_OR_GENERAL_VALUE"
        claim_units.append(
            {
                "fact": unit_text,
                "field": atomic["field"],
                "fact_status": str(
                    _copy_plan(page).get(
                        "copy_source",
                        "USER_FACT_OR_SAFE_COMPRESSION_OF_USER_FACT",
                    )
                ),
                "claim_families": unit_families,
                "evidence_classes": unit_modes,
                "observable_stage": (
                    "real use/action/material state"
                    if (
                        "ACTION_DEMONSTRABLE" in unit_modes
                        or "DIRECTLY_VISIBLE" in unit_modes
                    )
                    else "text or bounded sensory proxy only"
                ),
                "physical_origin": physical_action_origin,
                "target_object": category_evidence["target_object"],
                "best_view_duty": (
                    "select the evidence-supported view that makes this atomic "
                    "claim readable without unrelated copy"
                ),
                "evidence_topology": unit_topology,
                "visual_grammar": unit_grammar,
                "text_duty": (
                    "complete USER_FACT for invisible technology, ingredient, "
                    "duration, certification or result boundary"
                ),
                "result_boundary": result_boundary,
                "forbidden_substitutes": copy.deepcopy(
                    forbidden_substitutes
                ),
                "transfer_confidence": (
                    "ENGINEERED_DEFAULT_FROM_FULL_CATEGORY_ARCHIVE; current-"
                    "product evidence must authorize the concrete execution"
                ),
            }
        )
    return {
        "verified_claim": copy.deepcopy(claim),
        "buyer_objection": buyer_objection,
        "observability_modes": modes,
        "what_must_be_visible_without_copy": primary_evidence,
        "primary_physical_evidence": primary_evidence,
        "secondary_sensory_cue": secondary_sensory_cue,
        "text_only_fact": text_only_fact,
        "result_boundary": result_boundary,
        "concrete_action_sequence": action_sequence,
        "product_subject_relation": product_subject_relation,
        "category_prior": adapter["adapter_id"],
        "category_evidence_contract": copy.deepcopy(category_evidence),
        "product_form": product_form,
        "claim_families": claim_families,
        "primary_claim_family": primary_claim_family,
        "claim_visual_family": claim_visual_family,
        "evidence_topology": evidence_topology,
        "physical_action_origin": physical_action_origin,
        "sensory_release_origin": sensory_release_origin,
        "subject_evidence_role": category_evidence["subject_evidence_role"],
        "risk_boundary": category_evidence["risk_boundary"],
        "product_scale_range": product_scale_range,
        "cue_budget": cue_budget,
        "claim_text_duty": (
            "一个短主标题承担记忆；一个副句限定事实；最多1–3个短证据点。"
            "技术、成分、持续时间和不可见结果由完整USER_FACT文字承担"
        ),
        "claim_units": claim_units,
        "copy_duty": (
            "标题只承担本页一个主张；副文案解释不可见事实或限定边界，"
            "不得用文字替代主证据"
        ),
        "typography_expression": {
            "emphasized_keyword": emphasized,
            "headline_tone": headline_tone,
            "scale_ratio": "主关键词:主标题:说明≈1.35–1.55:1:0.42–0.55",
            "weight_roles": "900/700/400三档，禁止所有文字同字重",
            "width_and_rhythm": (
                "主关键词允许宽体或紧凑体与主标题形成宽窄反差；技术或"
                "成分事实使用短促高密度标签，不使用空洞细线胶囊框"
            ),
            "line_break_rule": "按完整语义断行，禁止机械截断中文",
        },
        "thumbnail_success_condition": (
            "缩略图下无需阅读说明文字，也能从当前商品与真实动作、材质、"
            "部件或空间关系识别本页主张；感官线索和装饰不得成为唯一证据"
        ),
        "forbidden_symbolic_substitutes": forbidden_substitutes,
        "page_job": blueprint.get("page_job"),
    }


def _verified_page_claim(page: dict[str, Any]) -> dict[str, str]:
    copy_plan = _copy_plan(page)
    role = page.get("page_role")
    if role == "CAROUSEL_SLOT_1_IDENTITY":
        fields = ("emphasized_keyword", "hero_headline", "identity_line")
    elif role == "CAROUSEL_SLOT_3_SCENE_BENEFIT":
        fields = ("hero_headline", "supporting_line", "emphasized_keyword")
    else:
        fields = ("emphasized_keyword", "hero_headline", "supporting_line")
    for field in fields:
        value = copy_plan.get(field)
        if isinstance(value, str) and value.strip():
            return {
                "text": value.strip(),
                "field": field,
                "source": str(
                    copy_plan.get(
                        "copy_source",
                        "USER_FACT_OR_SAFE_COMPRESSION_OF_USER_FACT",
                    )
                ),
            }
    return {
        "text": "",
        "field": "",
        "source": "USER_FACT_OR_SAFE_COMPRESSION_OF_USER_FACT",
    }


def _claim_expression_reasoning(
    page: dict[str, Any],
    raw: dict[str, Any],
    blueprint: dict[str, Any],
    feature: dict[str, Any],
) -> dict[str, Any]:
    claim = _verified_page_claim(page)
    role = page.get("page_role")
    is_hero = role == "CAROUSEL_SLOT_1_IDENTITY"
    buyer_objection = feature.get(
        "buyer_objection",
        (
            "what makes this current product immediately worth considering"
            if is_hero
            else "how can the current product visibly prove this page claim"
        ),
    )
    visualization = _claim_visualization_contract(
        page,
        raw,
        blueprint,
        feature,
        buyer_objection,
    )
    return {
        "verified_primary_claim": claim,
        "buyer_objection": buyer_objection,
        "reasoning_order": [
            "verified claim",
            "buyer objection",
            "visible proof relation",
            "evidence-bound best view",
            "product, subject or state relation",
            "spatial topology",
            "copy topology",
            "rebuilt background and lighting",
            "difference from adjacent pages",
        ],
        "open_synthesis": {
            "closed_method_menu": False,
            "prototypes_are_non_binding_priors": True,
            "may_create_new_concrete_expression": True,
            "layout_is_an_output_not_an_input": True,
            "rule": (
                "reason from the current claim, objection and evidence into "
                "one concrete execution; do not ask the image model to choose "
                "from methods or inherit another product's page skeleton"
            ),
        },
        "required_concrete_outputs_before_generation": [
            "one evidence-supported view or crop",
            "one visible product, subject or state relation",
            "one spatial topology",
            "one copy topology",
            "one background and lighting execution",
            "one explicit difference from adjacent pages",
        ],
        "thumbnail_success_condition": (
            "at small thumbnail size, the current product remains identifiable "
            "and the page claim is understandable from the visible relation, "
            "not from copy or decoration alone"
        ),
        "hero_information_budget": {
            "applies": is_hero,
            "scene_count": 1 if is_hero else None,
            "proof_modules_max": 0 if is_hero else None,
            "copy_groups_max": 2 if is_hero else None,
            "rule": (
                "show one complete product in one credible scene; let one "
                "identity-defining feature read naturally and move detached "
                "macros, pointers and multi-scene proof to later pages"
                if is_hero
                else "use only the evidence modules needed to prove this page"
            ),
        },
        "label_binding_rule": (
            "every label, pointer, macro and highlight must land on the exact "
            "visible current-product component, state or relation it names; "
            "remove it when that target is not supported or visible"
        ),
        "proof_success_contract": copy.deepcopy(
            feature.get("proof_success_contract", {})
        ),
        "claim_visualization_contract": visualization,
        "failure_conditions": [
            "claim is understandable only after reading copy",
            "symbolic decoration substitutes for physical or spatial evidence",
            "pointer or label lands on the wrong component",
            "page copies a prior page skeleton without evidence-driven reason",
            "product identity or key silhouette is weakened by the expression",
        ],
        "page_job": blueprint.get("page_job"),
    }


def _page_intelligence(
    page: dict[str, Any],
    raw: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    role = page.get("page_role")
    blueprint = copy.deepcopy(ROLE_BLUEPRINTS.get(role, {}))
    supported = _supported_views(identity)
    preferred = blueprint.get(
        "view_priority",
        ["FRONT", "THREE_QUARTER", "DETAIL"],
    )
    feature = page.get("feature_expression", {})
    bindings = feature.get("proof_bindings", [])
    bound_views = []
    for binding in bindings:
        for component in binding.get("component_bindings", []):
            for view in component.get("view_types", []):
                if view in supported and view not in bound_views:
                    bound_views.append(view)
    candidates = bound_views + [
        view
        for view in _ordered_views(supported, preferred)
        if view not in bound_views
    ]
    adapter = _category_adapter(raw)
    product_form = _product_form_prior(raw)
    current_overlay = page.get("design_prototype_overlay", {})
    expression_family = current_overlay.get(
        "expression_family",
        adapter["expression_family"],
    )
    if current_overlay.get("layout_archetype"):
        blueprint["layout_family"] = current_overlay["layout_archetype"]
    shot_plan = copy.deepcopy(current_overlay.get("shot_plan", {}))
    if not shot_plan:
        shot_plan = {
            "shot_key": "|".join(
                [
                    expression_family,
                    str(role),
                    str(blueprint.get("layout_family")),
                ]
            ),
            "reuse_policy": "UNIQUE_COMPLETE_PRODUCT_FRAME",
            "must_differ_from_previous": [
                "camera or crop",
                "product scale",
                "product state",
                "scene geometry",
                "subject anchor",
                "page skeleton",
            ],
        }
    blueprint["forbidden"] = list(
        dict.fromkeys(
            blueprint.get("forbidden", []) + UNIVERSAL_PROOF_FORBIDDEN
        )
    )
    expression_reasoning = _claim_expression_reasoning(
        page,
        raw,
        blueprint,
        feature,
    )
    return {
        **blueprint,
        "category_adapter": adapter,
        "user_validated_category_expression_priors": (
            _validated_category_expression_priors(product_form)
        ),
        "expression_family": expression_family,
        "shot_plan": shot_plan,
        "claim_expression_reasoning": expression_reasoning,
        "claim_visualization_contract": copy.deepcopy(
            expression_reasoning["claim_visualization_contract"]
        ),
        "verified_claim_authority": {
            "allowed_source": "USER_FACT_ONLY",
            "rule": (
                "page claims and selling points must be selected only from the "
                "user-provided verified text; visual evidence may prove a claim "
                "but may not create or expand one"
            ),
        },
        "photographic_evidence_policy": {
            "default": "PHOTOGRAPHIC_EVIDENCE_FIRST",
            "proof_bearing_modules": (
                "use the current product, current real components, real contents "
                "or actions, or realistic verified use scenes"
            ),
            "line_art_policy": "FORBIDDEN_AS_PRIMARY_OR_SECONDARY_EVIDENCE",
            "navigation_exception": (
                "allow only a few small non-proof wayfinding symbols"
            ),
            "fallback": (
                "simplify the layout or report an evidence gap; never replace "
                "missing photographic proof with a line diagram"
            ),
        },
        "reference_background_isolation": {
            "default": "REBUILD_BACKGROUND_FROM_ROUTER",
            "reference_authority": "PRODUCT_IDENTITY_AND_VIEW_EVIDENCE_ONLY",
            "discard": [
                "original wall, floor, desk and furniture",
                "original clutter and incidental objects",
                "original lighting, color cast and camera perspective",
            ],
            "selection_inputs": [
                "category",
                "current product color and material",
                "verified core selling point",
                "verified use scene",
                "offer status",
                "campaign preference",
            ],
            "exception": (
                "preserve the source environment only when the user explicitly "
                "requests it"
            ),
        },
        "best_view_selector": {
            "evidence_supported_candidates": candidates,
            "score_weights": {
                "product_identity_clarity": 30,
                "claim_defining_target_visibility": 20,
                "complete_product_coverage": 15,
                "key_component_non_overlap": 10,
                "credible_scene_or_action_relation": 10,
                "compositional_harmony": 15,
            },
            "selection_rule": (
                "choose the highest-scoring evidence-supported view; do not "
                "force front, side or three-quarter; preserve the complete "
                "current-product identity and the page's primary proof"
            ),
        },
        "proof_before_layout": {
            "buyer_objection": expression_reasoning["buyer_objection"],
            "expression_mode": feature.get("expression_mode"),
            "proof_success_contract": copy.deepcopy(
                feature.get("proof_success_contract", {})
            ),
            "rule": (
                "reason from verified claim and buyer objection into one "
                "visible proof relation, then derive view, spatial topology "
                "and layout; decoration, copy and pointers cannot substitute "
                "for evidence"
            ),
        },
        "design_intensity": {
            "level": "CONTROLLED_MEDIUM_HIGH",
            "target": (
                "increase hierarchy, motion and commercial finish without "
                "reducing product salience or evidence accuracy"
            ),
            "allowed_methods": [
                "asymmetry",
                "scale contrast",
                "layered information surfaces",
                "one repeatable campaign motif",
                "controlled depth, light or motion path",
            ],
            "stop_conditions": [
                "product is no longer the first fixation",
                "identity-defining edges lose local separation",
                "decoration becomes the only proof",
                "more than three graphic-device families compete",
            ],
        },
    }


def build_design_intelligence_plan(
    compiled: dict[str, Any],
    raw: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    pages = [
        {
            "page_index": page.get("index"),
            "page_role": page.get("page_role"),
            **_page_intelligence(page, raw, identity),
        }
        for page in compiled.get("pages", [])
    ]
    return {
        "status": "ACTIVE",
        "source_type": "ENGINEERED_DEFAULT",
        "scope": "NON_FACTUAL_VISUAL_DECISIONS_ONLY",
        "shared_campaign_motif": _campaign_motif(raw),
        "set_rhythm_contract": {
            "five_distinct_page_jobs": True,
            "minimum_title_structures": 3,
            "minimum_product_scales": 3,
            "minimum_composition_axes": 3,
            "minimum_keyword_emphasis_methods": 2,
            "minimum_graphic_device_families": 3,
            "adjacent_layout_clone_forbidden": True,
            "evidence_modules_must_be_hierarchical_not_equal_by_default": True,
            "background_value_and_information_density_must_form_rhythm": True,
            "product_identity_priority": True,
            "selling_points_must_come_from_verified_user_text": True,
            "photographic_evidence_preferred_across_categories": True,
            "line_art_cannot_replace_proof": True,
            "reference_background_isolation_required": True,
            "same_or_near_duplicate_complete_product_frame_reuse": 0,
            "style_reference_cannot_supply_pose_crop_state_scene_or_layout": True,
            "shared_campaign_dna_does_not_mean_shared_page_skeleton": True,
            "middle_page_order_may_follow_purchase_logic": True,
            "verified_core_claims_require_explicit_page_coverage": True,
            "shot_key_dimensions": [
                "view or crop",
                "product scale",
                "product state",
                "scene geometry",
                "subject anchor",
                "layout family",
            ],
        },
        "pages": pages,
    }


def _prompt_block(
    page_plan: dict[str, Any],
    motif: dict[str, Any],
    rhythm: dict[str, Any],
) -> str:
    hierarchy = "；".join(page_plan.get("evidence_hierarchy", []))
    reasoning = page_plan.get("claim_expression_reasoning", {})
    claim = reasoning.get("verified_primary_claim", {}).get("text") or "本页已核验主张"
    buyer_objection = reasoning.get("buyer_objection", "")
    visualization = reasoning.get("claim_visualization_contract", {})
    observability = "、".join(
        visualization.get("observability_modes", [])
    ) or "TEXT_ONLY_VERIFIED"
    typography = visualization.get("typography_expression", {})
    sensory_execution = (
        "感官线索仅用低比例真实材质运动辅助，不作物理证明。"
        if "SENSORY_PROXY_ONLY" in visualization.get(
            "observability_modes", []
        )
        else ""
    )
    text_execution = (
        "不可见技术/成分/时长仅作完整USER_FACT文案，禁画伪结构。"
        if "TEXT_ONLY_VERIFIED" in visualization.get(
            "observability_modes", []
        )
        else ""
    )
    result_execution = (
        "无用户真实前后证据，不画结果变化。"
        if "RESULT_NOT_VISUALLY_PROVABLE_WITH_CURRENT_EVIDENCE"
        in visualization.get("observability_modes", [])
        else ""
    )
    candidates = "、".join(
        page_plan.get("best_view_selector", {}).get(
            "evidence_supported_candidates", []
        )
    ) or "only views supported by current-product references"
    forbidden = "；".join(page_plan.get("forbidden", []))
    universal_evidence_lock = (
        "实物证据优先：承担证明责任的主证据和次证据必须使用当前商品、"
        "真实部件、真实内容物/动作或核验场景的摄影式画面；线稿、轮廓图、"
        "图标、线框示意不得替代证据，仅允许少量不承担证明的导航符号。"
    )
    background_isolation_lock = (
        "参考背景隔离：原图只提供商品身份与视角证据；默认丢弃原拍墙面、"
        "地面、桌具、杂物、光色和透视，按品类、商品色材质、核验卖点、"
        "核验场景与Campaign重新构建背景；用户明确要求保留时除外。"
    )
    verified_claim_lock = (
        "卖点事实锁：本页卖点只可从用户已提供并核验的文字中选择；不得从"
        "商品外观、类目常识、原拍背景或模型联想中新增、扩写或改强结论。"
    )
    if page_plan.get("page_role") == "CAROUSEL_SLOT_1_IDENTITY":
        return "\n".join(
            [
                "【CROSS-CATEGORY DESIGN INTELLIGENCE — ENGINEERED_DEFAULT】",
                f"本页已核验主张={claim}；要解决的购买疑虑={buyer_objection}。",
                "先推理卖点的可见表达；版式是结果，不是模板。",
                f"卖点合同：可观察性={observability}；主证据="
                f"{visualization.get('primary_physical_evidence')}；"
                "缩略图须由可见关系而非说明文字读懂。",
                f"大类prior={visualization.get('category_prior')}；"
                f"当前剂型={visualization.get('product_form')}；"
                f"卖点族={'、'.join(visualization.get('claim_families', []))}；"
                f"证据拓扑={visualization.get('evidence_topology')}。",
                f"主体职责={visualization.get('subject_evidence_role')}；"
                f"风险边界={visualization.get('risk_boundary')}。",
                f"{sensory_execution}{text_execution}{result_execution}",
                "字体表现=主词/标题/说明="
                f"{typography.get('scale_ratio')}；"
                f"{typography.get('weight_roles')}；"
                f"字体性格={typography.get('headline_tone')}。",
                f"商品尺度={visualization.get('product_scale_range')}；"
                f"文案职责={visualization.get('claim_text_duty')}。",
                "首图=完整商品+一个可信主场景；微距、引线、多场景和参数后置。",
                "首图预算：1场景、0独立证明模块、≤2组文案；核心特征在"
                "完整商品中自然显现。",
                f"候选视角={candidates}；选身份、重点和构图综合最高分者。",
                "受控中高强度；商品第一视觉，1–2种装置，下一页换骨架。",
                "本页镜头必须区别于后页；禁近似镜头复用；风格参考不得"
                "提供姿态、机位、状态或骨架。",
                "证据只用当前实物/核验场景；指示必须落对部件；原拍环境"
                "隔离重建，线稿图标不得替代证明。",
            ]
        )
    return "\n".join(
        [
            "【CROSS-CATEGORY DESIGN INTELLIGENCE — ENGINEERED_DEFAULT】",
            f"本页已核验主张={claim}；要解决的购买疑虑={buyer_objection}。",
            "先完成卖点表达推理，把主张、疑虑、当前商品证据编译成一个"
            "缩略图也能读懂的可见关系；再定视角、空间与文案拓扑。"
            "版式是推理结果，不是预设模板；禁用固定左右模板和默认等大卡片。",
            f"卖点可观察性={observability}。",
            "主物理证据="
            f"{visualization.get('primary_physical_evidence')}。",
            "具体动作链="
            f"{visualization.get('concrete_action_sequence')}。",
            f"大类prior={visualization.get('category_prior')}；"
            f"当前剂型={visualization.get('product_form')}；"
            f"卖点族={'、'.join(visualization.get('claim_families', []))}；"
            f"证据拓扑={visualization.get('evidence_topology')}。",
            f"表达族={visualization.get('claim_visual_family')}；"
            f"动作起点={visualization.get('physical_action_origin')}；"
            f"感官释放起点={visualization.get('sensory_release_origin')}。",
            f"主体职责={visualization.get('subject_evidence_role')}；"
            f"风险边界={visualization.get('risk_boundary')}。",
            f"线索预算={visualization.get('cue_budget')}；"
            f"文案职责={visualization.get('claim_text_duty')}。",
            f"{sensory_execution}{text_execution}{result_execution}",
            "缩略图条件=由真实关系而非说明文字读懂；字体表现=主词/标题/说明="
            f"{typography.get('scale_ratio')}；"
            f"{typography.get('weight_roles')}；"
            f"字体性格={typography.get('headline_tone')}；中文完整断行。",
            "禁止替代="
            f"{'；'.join(visualization.get('forbidden_symbolic_substitutes', []))}。",
            f"职责={page_plan.get('page_job')}；"
            f"骨架={page_plan.get('layout_family')}；"
            f"密度={page_plan.get('information_density')}。",
            f"商品表达族={page_plan.get('expression_family')}；"
            f"本页镜头={page_plan.get('shot_plan', {}).get('shot_key')}。"
            "禁止跨页复用相同或近似的完整商品摄影、裁切、装载/工作状态"
            "和场景几何；STYLE_REFERENCE_ONLY不得提供商品姿态、机位、"
            "裁切、状态、场景布局、主体锚点或页面骨架。",
            f"层级：{hierarchy}。",
            f"候选视角：{candidates}；按身份30/卖点20/完整15/不重叠10/"
            "真实交互10/构图15选择最高分。",
            "证据优先："
            f"{page_plan.get('category_adapter', {}).get('evidence_priority')}。",
            verified_claim_lock,
            universal_evidence_lock,
            "标签、引线、放大框和高亮必须准确落到其命名的当前商品部件、"
            "状态或关系；目标不可见、无证据或落点不准确时直接删除。",
            background_isolation_lock,
            f"禁止：{forbidden}。",
            "优先级：身份事实>可见证据>页面职责>套图统一>设计强度。",
        ]
    )


def apply_design_intelligence(
    compiled: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(compiled)
    by_role = {
        page["page_role"]: page
        for page in plan.get("pages", [])
    }
    motif = plan.get("shared_campaign_motif", {})
    rhythm = plan.get("set_rhythm_contract", {})
    for page in result.get("pages", []):
        page_plan = by_role.get(page.get("page_role"))
        if page_plan is None:
            continue
        page["design_intelligence_overlay"] = copy.deepcopy(page_plan)
        page["claim_visualization_contract"] = copy.deepcopy(
            page_plan.get("claim_visualization_contract", {})
        )
        page.setdefault("design_prototype_overlay", {}).update(
            {
                "evidence_hierarchy": copy.deepcopy(
                    page_plan.get("evidence_hierarchy", [])
                ),
                "best_view_selector": copy.deepcopy(
                    page_plan.get("best_view_selector", {})
                ),
                "design_intensity": copy.deepcopy(
                    page_plan.get("design_intensity", {})
                ),
            }
        )
        page["generation_prompt"] = (
            page["generation_prompt"].rstrip()
            + "\n\n"
            + _prompt_block(page_plan, motif, rhythm)
        )
    result["design_intelligence_overlay"] = copy.deepcopy(plan)
    return result
