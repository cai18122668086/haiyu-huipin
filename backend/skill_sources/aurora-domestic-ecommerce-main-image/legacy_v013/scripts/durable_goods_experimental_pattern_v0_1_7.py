#!/usr/bin/env python3
"""Compile user-opted durable-goods visual grammar without importing facts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_PATH = (
    ROOT / "references" / "durable-goods-experimental-patterns-v0.1.7.json"
)
SCHEMA_PATH = (
    ROOT / "schemas" / "experimental-pattern-contract-v0.1.7.schema.json"
)
PATTERN_BUNDLE_ID = "V3_DURABLE_GOODS_STYLE_GRAMMAR"


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _surface_mode(raw: dict[str, Any]) -> str:
    output_type = raw["output"]["type"]
    if output_type in {
        "STANDARD_IDENTITY_MAIN",
        "OFFER_IDENTITY_MAIN",
        "FIVE_IMAGE_CAROUSEL",
    }:
        return "STYLE_GRAMMAR_ONLY"
    if output_type in {"DETAIL_LONG", "DETAIL_SLICE"}:
        return "DETAIL_LAYOUT_AND_STYLE"
    return "NOT_APPLIED"


def _category_applicable(raw: dict[str, Any], bundle: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(raw.get("product", {}).get("category", "")),
            str(raw.get("product", {}).get("name", "")),
        ]
    ).lower()
    return any(
        str(term).lower() in haystack
        for term in bundle["applicability"]["category_terms"]
    )


def _explicit_opt_in(raw: dict[str, Any]) -> bool:
    requested = (
        raw.get("campaign_preference", {}).get("experimental_patterns", [])
    )
    return PATTERN_BUNDLE_ID in requested


def build_experimental_pattern_contract(
    raw: dict[str, Any],
) -> dict[str, Any]:
    bundle = _load(BUNDLE_PATH)
    opt_in = _explicit_opt_in(raw)
    applicable = _category_applicable(raw, bundle)
    mode = _surface_mode(raw)
    active = opt_in and applicable and mode != "NOT_APPLIED"
    blocked = opt_in and not active
    parameters = {
        "typography": {
            "headline_max_lines": 2,
            "subtitle_to_headline_ratio": [0.26, 0.32],
            "eyebrow_to_headline_ratio": [0.18, 0.24],
            "emphasis_mechanism_limit": 2,
            "semantic_line_break_required": True,
            "headline_color_roles": [
                "DEEP_BROWN_OR_GRAPHITE_ON_LIGHT",
                "WHITE_ON_DARK_SCENE_NEGATIVE_SPACE",
            ],
        },
        "background_and_photography": {
            "material_family": [
                "CREAM_OR_LIGHT_BEIGE_WALL",
                "LIGHT_WOOD_FLOOR_OR_TABLE",
                "SOFT_WINDOW_LIGHT",
            ],
            "significant_prop_limit": 5,
            "light_product_separation": (
                "CONTACT_SHADOW_AND_CONTENT_COLOR_CONTRAST"
            ),
            "dark_product_separation": (
                "WINDOW_RIM_AND_WALL_LUMINANCE_CONTRAST"
            ),
        },
        "graphic_language": {
            "device_limit": 3,
            "detail_devices": [
                "MAGNIFIER",
                "LEADER_LINE",
                "COMPARE_CARD",
                "PARAMETER_GRID",
            ],
            "proof_requires_current_product_closeup": True,
            "omit_uncertain_connector": True,
        },
        "anti_template_rules": bundle[
            "cross_surface_style_grammar"
        ]["anti_template_rules"],
        "allowed_controls": bundle[
            "cross_surface_style_grammar"
        ]["allowed_controls"],
        "forbidden_controls": bundle[
            "cross_surface_style_grammar"
        ]["forbidden_controls"],
    }
    active_ids = (
        [pattern["pattern_id"] for pattern in bundle["patterns"]]
        if active
        else []
    )
    gates = {
        "explicit_opt_in_required_and_respected": (active == opt_in)
        if applicable
        else not active,
        "category_scope_respected": not active or applicable,
        "note_content_not_promoted_to_evidence": True,
        "cross_surface_ceiling_respected": (
            mode != "STYLE_GRAMMAR_ONLY"
            or "LAYOUT_ARCHETYPE"
            in parameters["forbidden_controls"]
        ),
        "product_identity_not_controlled": (
            "PRODUCT_IDENTITY" in parameters["forbidden_controls"]
        ),
        "visible_copy_not_controlled": (
            "VISIBLE_COPY" in parameters["forbidden_controls"]
        ),
        "reference_product_facts_not_imported": (
            "REFERENCE_PRODUCT_FACTS" in parameters["forbidden_controls"]
        ),
        "profile01_semantics_read_false": True,
    }
    result = {
        "schema_version": "experimental-pattern-contract-0.1.7",
        "request_id": raw["request_id"],
        "pattern_bundle_id": PATTERN_BUNDLE_ID,
        "status": (
            "ACTIVE" if active else "BLOCKED" if blocked else "INACTIVE"
        ),
        "source_type": "EXPERIMENTAL_PATTERN",
        "source_context": "NOTE_CONTENT",
        "evidence_ceiling": "PARTIAL_EVIDENCE",
        "explicit_opt_in": opt_in,
        "category_applicable": applicable,
        "surface_usage_mode": mode if active else "NOT_APPLIED",
        "active_pattern_ids": active_ids,
        "compiled_parameters": parameters if active else {},
        "gates": gates,
        "generation_performed": False,
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(_load(SCHEMA_PATH)).validate(result)
    return result


def experimental_pattern_prompt_block(
    contract: dict[str, Any],
    page_index: int,
) -> str:
    if contract["status"] != "ACTIVE":
        return ""
    parameters = contract["compiled_parameters"]
    detail_note = (
        "本页是详情Surface，可使用当前商品原图绑定的场景模块或结构近景；"
        if contract["surface_usage_mode"] == "DETAIL_LAYOUT_AND_STYLE"
        else "本页是主图或轮播Surface，只吸收字体、色彩、摄影和信息面语言；"
        "不得复制参考作品集的长页、三栏或卡片骨架；"
    )
    return (
        "【DURABLE GOODS EXPERIMENTAL PATTERN v0.1.7】\n"
        f"page_index={page_index}；source_type=EXPERIMENTAL_PATTERN；"
        "source_context=NOTE_CONTENT；evidence_ceiling=PARTIAL_EVIDENCE。\n"
        f"{detail_note}\n"
        f"typography={json.dumps(parameters['typography'], ensure_ascii=False)}\n"
        "background_and_photography="
        f"{json.dumps(parameters['background_and_photography'], ensure_ascii=False)}\n"
        f"graphic_language={json.dumps(parameters['graphic_language'], ensure_ascii=False)}\n"
        f"anti_template_rules={json.dumps(parameters['anti_template_rules'], ensure_ascii=False)}\n"
        "本模块不提供商品事实、可见文案、优惠、产品身份或具体版式。"
        "冲突时按 Product Identity、USER_FACT、合规、页面职责、可读性顺序降级。"
    )
