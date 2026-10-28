#!/usr/bin/env python3
"""Route verified evidence to a category-appropriate visual representation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "evidence-representation-plan-v0.1.5.schema.json"

ROLE_MODES: dict[str, dict[str, str]] = {
    "食品": {
        "IDENTITY": "PRODUCT_HERO_PHOTOGRAPHY",
        "FEATURE": "REAL_FOOD_MACRO_WITH_PRODUCT_HERO",
        "SCENE": "REAL_ACTION_SEQUENCE_PHOTOGRAPHY",
        "PROOF": "REAL_COMPONENT_CLOSEUP_COLLAGE",
        "OFFER": "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY",
        "TRUST": "PRODUCT_PLUS_NUMERIC_SPEC_POSTER",
    },
    "家具": {
        "IDENTITY": "PRODUCT_HERO_PHOTOGRAPHY",
        "FEATURE": "VISIBLE_STRUCTURE_MACRO",
        "SCENE": "REAL_USE_SCENE_PHOTOGRAPHY",
        "PROOF": "SOURCE_BOUND_COMPONENT_CLOSEUP_GRID",
        "OFFER": "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY",
        "TRUST": "PRODUCT_PLUS_NUMERIC_SPEC_POSTER",
    },
    "数码电子": {
        "IDENTITY": "PRODUCT_HERO_PHOTOGRAPHY",
        "FEATURE": "VISIBLE_INTERFACE_OR_FUNCTION_MACRO",
        "SCENE": "REAL_USE_SCENE_PHOTOGRAPHY",
        "PROOF": "SOURCE_BOUND_COMPONENT_CLOSEUP_GRID",
        "OFFER": "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY",
        "TRUST": "PRODUCT_PLUS_NUMERIC_SPEC_POSTER",
    },
    "小家电": {
        "IDENTITY": "PRODUCT_HERO_PHOTOGRAPHY",
        "FEATURE": "VISIBLE_FUNCTION_MACRO",
        "SCENE": "REAL_OPERATION_SEQUENCE_PHOTOGRAPHY",
        "PROOF": "SOURCE_BOUND_COMPONENT_CLOSEUP_GRID",
        "OFFER": "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY",
        "TRUST": "PRODUCT_PLUS_NUMERIC_SPEC_POSTER",
    },
    "美妆个护": {
        "IDENTITY": "PRODUCT_HERO_PHOTOGRAPHY",
        "FEATURE": "REAL_TEXTURE_MACRO_WITH_PRODUCT_HERO",
        "SCENE": "REAL_USE_SEQUENCE_PHOTOGRAPHY",
        "PROOF": "PACKAGE_AND_APPLICATOR_DETAIL_GRID",
        "OFFER": "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY",
        "TRUST": "PRODUCT_PLUS_VERIFIED_LABEL_SUMMARY",
    },
}

GENERAL_MODES = {
    "IDENTITY": "PRODUCT_HERO_PHOTOGRAPHY",
    "FEATURE": "SOURCE_BOUND_PRODUCT_MACRO",
    "SCENE": "REAL_USE_SCENE_PHOTOGRAPHY",
    "PROOF": "SOURCE_BOUND_EVIDENCE_GRID",
    "OFFER": "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY",
    "TRUST": "PRODUCT_PLUS_VERIFIED_SPEC_SUMMARY",
}

MODE_EXECUTION = {
    "PRODUCT_HERO_PHOTOGRAPHY": "Use one clear full-product commercial photograph; no evidence cards.",
    "REAL_FOOD_MACRO_WITH_PRODUCT_HERO": "Keep the product visible and use restrained real-food macro photography bound to verified flavor or ingredient facts.",
    "REAL_ACTION_SEQUENCE_PHOTOGRAPHY": "Use real photographed actions for each verified step; do not replace them with generic line icons.",
    "REAL_COMPONENT_CLOSEUP_COLLAGE": "Use real food close-ups for fact-bound components in varied photo windows; do not invent packet appearance, quantity or ratio.",
    "PRODUCT_PLUS_NUMERIC_OFFER_HIERARCHY": "Keep product identity visible and make only verified offer numbers the primary information visual.",
    "PRODUCT_PLUS_NUMERIC_SPEC_POSTER": "Use a product-plus-large-number specification poster; no offer styling without verified offers.",
    "VISIBLE_STRUCTURE_MACRO": "Use only structure or material regions visible in the current-product references.",
    "REAL_USE_SCENE_PHOTOGRAPHY": "Show the current product in a verified use context with realistic photography.",
    "SOURCE_BOUND_COMPONENT_CLOSEUP_GRID": "Use independent close-ups, each bound to one visible current-product component; no ambiguous pointers.",
    "VISIBLE_INTERFACE_OR_FUNCTION_MACRO": "Use visible interfaces or function evidence from current-product references.",
    "VISIBLE_FUNCTION_MACRO": "Use visible operation or function evidence from current-product references.",
    "REAL_OPERATION_SEQUENCE_PHOTOGRAPHY": "Use real photographed operation steps derived from verified instructions.",
    "REAL_TEXTURE_MACRO_WITH_PRODUCT_HERO": "Use real texture photography only when the texture is visible or user-verified.",
    "REAL_USE_SEQUENCE_PHOTOGRAPHY": "Use real photographed application steps without inventing efficacy.",
    "PACKAGE_AND_APPLICATOR_DETAIL_GRID": "Use package, cap, pump or applicator details visible in current references.",
    "PRODUCT_PLUS_VERIFIED_LABEL_SUMMARY": "Use the product with a restrained grid of verified label facts.",
    "SOURCE_BOUND_PRODUCT_MACRO": "Use a close product view bound to current-product references.",
    "SOURCE_BOUND_EVIDENCE_GRID": "Use independent evidence cells bound to verified facts or visible current-product regions.",
    "PRODUCT_PLUS_VERIFIED_SPEC_SUMMARY": "Use the product with a concise verified specification summary.",
}


def build_evidence_representation_plan(
    raw: dict[str, Any],
    design_contract: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    category = str(raw["product"].get("category", "")).strip()
    policy = ROLE_MODES.get(category, GENERAL_MODES)
    design_by_index = {p["index"]: p for p in design_contract["pages"]}
    overlay_by_index = {p["page_index"]: p for p in overlay["pages"]}
    pages = []
    for index in range(1, 6):
        design_page = design_by_index[index]
        overlay_page = overlay_by_index[index]
        role = design_page["role_family"]
        mode = policy.get(role, GENERAL_MODES.get(role, "SOURCE_BOUND_EVIDENCE_GRID"))
        copy_plan = design_page["copy_plan"]
        labels = [
            x for x in copy_plan.get("evidence_labels", [])
            if isinstance(x, str) and x.strip()
        ]
        pages.append(
            {
                "page_index": index,
                "page_role_family": role,
                "category": category or "通用",
                "representation_mode": mode,
                "visual_execution": MODE_EXECUTION[mode],
                "fact_bound_visual_subjects": labels,
                "layout_archetype_binding": overlay_page["layout_archetype"],
                "card_topology": {
                    "IDENTITY": "NO_CARDS",
                    "FEATURE": "ROLE_SPECIFIC_MACRO_OR_EVIDENCE_PATH",
                    "SCENE": "REAL_PHOTO_SEQUENCE_OR_SCENE",
                    "PROOF": "INDEPENDENT_FACT_BOUND_EVIDENCE_CELLS",
                    "OFFER": "TIERED_VERIFIED_OFFER_MODULE",
                    "TRUST": "VERIFIED_SPEC_OR_LABEL_SUMMARY",
                }.get(role, "ROLE_SPECIFIC"),
                "icon_policy": (
                    "REAL_PHOTOGRAPHY_PRIMARY; ICONS_ONLY_AS_SECONDARY_NAVIGATION"
                    if category == "食品" and role in {"FEATURE", "SCENE", "PROOF"}
                    else "ICONS_ALLOWED_ONLY_WHEN_THEY_DO_NOT_REPLACE_REQUIRED_VISUAL_EVIDENCE"
                ),
                "hard_constraints": [
                    "CURRENT_PRODUCT_IDENTITY_FROM_ORIGINAL_REFERENCES_ONLY",
                    "EVERY_VISIBLE_CLAIM_MUST_BE_USER_FACT_OR_SAFE_COMPRESSION",
                    "REPRESENTATION_MODE_DOES_NOT_AUTHORIZE_NEW_FACTS",
                    "NO_AMBIGUOUS_POINTERS",
                    "NO_PREVIOUS_PRODUCT_IDENTITY_CARRYOVER",
                ],
                "field_sources": {
                    "representation_mode": "ENGINEERED_DEFAULT",
                    "fact_bound_visual_subjects": "USER_FACT",
                    "layout_archetype_binding": "ENGINEERED_DEFAULT",
                    "card_topology": "ENGINEERED_DEFAULT",
                },
            }
        )
    modes = {p["representation_mode"] for p in pages}
    gates = {
        "all_pages_have_representation_mode": all(
            p["representation_mode"] for p in pages
        ),
        "all_pages_bind_current_layout_archetype": all(
            p["layout_archetype_binding"] for p in pages
        ),
        "at_least_three_representation_modes": len(modes) >= 3,
        "food_evidence_pages_use_real_photography": (
            category != "食品"
            or all(
                "REAL_" in p["representation_mode"]
                for p in pages
                if p["page_role_family"] in {"FEATURE", "SCENE", "PROOF"}
            )
        ),
        "offers_require_verified_offer_facts": (
            bool(raw["product"].get("verified_offers"))
            or all(p["page_role_family"] != "OFFER" for p in pages)
        ),
        "no_generated_identity_reference": True,
        "profile01_semantics_read_false": True,
    }
    result = {
        "schema_version": "evidence-representation-plan-0.1.5",
        "request_id": raw["request_id"],
        "status": "READY" if all(gates.values()) else "BLOCKED",
        "category_route": category or "通用",
        "governance": {
            "source_type": "ENGINEERED_DEFAULT",
            "fact_authority": "USER_FACT_AND_CURRENT_PRODUCT_EVIDENCE_ONLY",
            "representation_does_not_create_facts": True,
            "profile01_semantics_read": False,
        },
        "pages": pages,
        "set_gates": gates,
        "generation_performed": False,
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    ).validate(result)
    return result


def evidence_prompt_block(page: dict[str, Any]) -> str:
    return (
        "【EVIDENCE REPRESENTATION ROUTER】\n"
        f"representation_mode={page['representation_mode']}；"
        f"visual_execution={page['visual_execution']}；"
        f"fact_bound_visual_subjects={json.dumps(page['fact_bound_visual_subjects'], ensure_ascii=False)}；"
        f"card_topology={page['card_topology']}；"
        f"icon_policy={page['icon_policy']}。\n"
        "本路由只决定证据如何可视化，不增加任何商品事实。"
        "实体摄影、操作摄影、微距、图标和数字模块必须服从当前商品原图、USER_FACT、页面角色与合规门禁。"
    )
