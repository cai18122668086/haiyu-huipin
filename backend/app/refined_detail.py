# ruff: noqa: E501
"""Independent website contract for the user-supplied Xinghe detail 4.0 skill."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from .new_detail_skill_provider import (
    AdapterValidationError,
    DetailSkillRoute,
    NewDetailSkillProvider,
    requested_page_count,
)

if TYPE_CHECKING:
    from .provider import Provider
    from .xinghe_agent import PlannerRequest

SKILL_ID = "xinghe-ecommerce-detail-domestic-4"
PIPELINE_VERSION = "xinghe-domestic-detail-v4.0"
PROFILE = "xinghe_domestic_detail_4"


def is_refined(configuration: Mapping[str, Any]) -> bool:
    return configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION


def prompt_configuration(settings: Mapping[str, Any]) -> dict[str, Any]:
    from .domestic_detail_provider import domestic_detail_prompt_configuration

    # Reuse platform/size validation, then replace the Aurora rendering policy.
    count = requested_page_count(settings)
    if count > 12:
        raise AdapterValidationError(
            "DETAIL_PAGE_COUNT_UNSUPPORTED", "请选择 1 至 12 页。"
        )
    base = domestic_detail_prompt_configuration({**settings, "page_count": 8})
    base["requested_page_count"] = count
    return {
        key: value
        for key, value in base.items()
        if key
        in {
            "platform_code",
            "language",
            "requested_page_count",
            "default_page_count",
            "target_size",
            "resolved_size",
        }
    } | {
        "profile_id": PROFILE,
        "screen_aspect_ratio": "3:4",
        "independent_page_generation": True,
        "output_format": "png",
        "approval_gate": {"required": True, "auto_confirm_after_validation": settings.get("auto_start_after_plan") is True},
        "delivery": {"qa_mode": "optional_manual_review", "qa_status_required": False},
    }


class RefinedDetailProvider(NewDetailSkillProvider):
    def __init__(self, delegate: Provider) -> None:
        super().__init__(
            delegate, DetailSkillRoute(SKILL_ID, "domestic", prompt_configuration)
        )


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties)}


def _strings() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}


def strategy_schema(page_count: int) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    confidence = {"enum": ["confirmed", "reasonable_inference", "needs_confirmation"]}
    demand = _object(
        {
            "id": text,
            "rank": {"type": "integer", "minimum": 1},
            "demand": text,
            "buyer_question": text,
            "reason": text,
        }
    )
    match = _object(
        {
            "demand_id": text,
            "selling_point": text,
            "confidence": confidence,
            "evidence": text,
        }
    )
    nonempty_object = {"type": "object", "minProperties": 1}
    screen = _object(
        {
            **{
                key: text
                for key in (
                    "role",
                    "title",
                    "label",
                    "composition",
                    "device",
                    "difference",
                    "structure",
                    "subject_scale",
                    "purchase_decision_type",
                    "buyer_demand_id",
                    "buyer_demand",
                    "buyer_question",
                    "core_information",
                    "matched_selling_point",
                    "copy_suggestion",
                    "visual_proof_method",
                    "required_proof",
                    "risk_reminder",
                    "image_prompt",
                )
            },
            "confidence": confidence,
            "required_elements": _strings(),
            "forbidden_elements": _strings(),
            "scene_layout": _object(
                {
                    key: text
                    for key in (
                        "foreground",
                        "midground",
                        "background",
                        "physical_relationship",
                    )
                }
            ),
        }
    )
    module = _object(
        {
            "page": {"type": "integer", "minimum": 1},
            "role": text,
            "buyer_demand_id": text,
        }
    )
    return _object(
        {
            "analysis": _object(
                {
                    "category_key": text,
                    "category_reason": text,
                    "product_image_analysis": nonempty_object,
                    "product_consistency_anchors": nonempty_object,
                    "buyer_objections": _strings(),
                    "information_confidence": _object(
                        {
                            key: {"type": "array", "items": text}
                            for key in (
                                "confirmed",
                                "reasonable_inference",
                                "needs_confirmation",
                            )
                        }
                    ),
                    "buyer_demand_map": {
                        "type": "array",
                        "items": demand,
                        "minItems": 1,
                    },
                    "demand_to_selling_point_match": {
                        "type": "array",
                        "items": match,
                        "minItems": 1,
                    },
                    "product_type_strategy": _object(
                        {"type": text, "rationale": text, "priority": text}
                    ),
                }
            ),
            "plan": _object(
                {
                    "purchase_decision_type": text,
                    "page_strategy": text,
                    "best_hero_direction": text,
                    "module_plan": {
                        "type": "array",
                        "items": module,
                        "minItems": page_count,
                        "maxItems": page_count,
                    },
                    "visual_rhythm_plan": {
                        "type": "array",
                        "items": text,
                        "minItems": page_count,
                        "maxItems": page_count,
                    },
                    "campaign_style_lock": _object(
                        {
                            key: text
                            for key in (
                                "palette",
                                "typography",
                                "background",
                                "lighting",
                                "icon_style",
                                "whitespace",
                                "product_scale",
                            )
                        }
                    ),
                    "design_strength_lock": _object(
                        {
                            **{
                                key: text
                                for key in (
                                    "category_visual_motif",
                                    "composition_contrast",
                                    "color_contrast",
                                    "template_ban",
                                )
                            },
                            "signature_devices": {
                                "type": "array",
                                "items": text,
                                "minItems": 2,
                            },
                            "rhythm_map": {
                                "type": "array",
                                "items": text,
                                "minItems": page_count,
                                "maxItems": page_count,
                            },
                        }
                    ),
                    "style_system_lock": nonempty_object,
                    "product_identity_physics_lock": nonempty_object,
                    "screens": {
                        "type": "array",
                        "items": screen,
                        "minItems": page_count,
                        "maxItems": page_count,
                    },
                }
            ),
        }
    )


def normalize_strategy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Relocate a model-authored field without changing its content."""
    from .xinghe_agent import XingheAgentError

    result = deepcopy(dict(value))
    analysis = result.get("analysis")
    if isinstance(analysis, dict) and "product_type_strategy" in result:
        authored = result["product_type_strategy"]
        if (
            "product_type_strategy" in analysis
            and analysis["product_type_strategy"] != authored
        ):
            raise XingheAgentError(
                "REFINED_DETAIL_PLAN_INVALID",
                "Conflicting product type strategies cannot be reconciled locally.",
            )
        analysis["product_type_strategy"] = result.pop("product_type_strategy")
    return result


def validate_strategy(value: Mapping[str, Any], page_count: int) -> None:
    from .xinghe_agent import XingheAgentError

    errors = list(Draft202012Validator(strategy_schema(page_count)).iter_errors(value))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise XingheAgentError(
            "REFINED_DETAIL_PLAN_INVALID", error.message, json_pointer=pointer
        )
    analysis, plan = value["analysis"], value["plan"]
    demands = analysis["buyer_demand_map"]
    ids = {demand["id"] for demand in demands}
    matched = {
        match["demand_id"] for match in analysis["demand_to_selling_point_match"]
    }
    valid = (
        len(ids) == len(demands)
        and [demand["rank"] for demand in demands] == list(range(1, len(demands) + 1))
        and matched == ids
    )
    for number, (module, screen) in enumerate(
        zip(plan["module_plan"], plan["screens"], strict=True), 1
    ):
        valid = valid and (
            module["page"] == number
            and module["role"] == screen["role"]
            and module["buyer_demand_id"] == screen["buyer_demand_id"]
            and screen["buyer_demand_id"] in ids
        )
    structures = Counter(
        screen["structure"].strip().casefold() for screen in plan["screens"]
    )
    valid = valid and len(structures) >= min(5, page_count)
    if page_count > 8:
        valid = valid and max(structures.values()) <= 2
    valid = valid and all(
        screen["image_prompt"].startswith("vertical 3:4 e-commerce detail page image")
        for screen in plan["screens"]
    )
    if not valid:
        raise XingheAgentError(
            "REFINED_DETAIL_PLAN_INVALID",
            "Demand ranking, module bindings or layout variety is incomplete.",
        )


def planner_prompt(request: PlannerRequest) -> str:
    settings = request.intake_settings or {}
    return (
        f"Create the full Xinghe 4.0 strategy and exactly {request.page_count} independent "
        f"3:4 screens in {request.language}. Product: {request.product_name}. "
        f"User facts: {json.dumps(list(request.product_information), ensure_ascii=False)}. "
        f"Category: {settings.get('category') or 'infer from product photos'}. "
        f"Target buyer: {settings.get('target_buyer') or 'derive conservatively'}. "
        f"Platform: {request.platform_code}. "
        f"Canvas: {request.size}. User brand policy: {settings.get('brandless_confirmation') or 'use only authorized visible product marks'}. "
        "Follow buyer demand ranking -> purchase decision -> page strategy -> module plan "
        "-> demand/selling-point match -> visual proof -> shared style and identity locks. "
        "Only product_reference images and user facts establish product truth. "
        "style_reference images supply visual inspiration only, never product facts or branding. "
        "Visible watermarks are source marks, not authorized branding. "
        "Operator notes about test runs, source-image watermarks, rendering and brand policies "
        "are private constraints, not buyer objections, selling points or on-image body copy. "
        "Do not build a module about these workflow rules. "
        "Unknown material, efficacy, parameters, certifications, tests, sales and reviews "
        "must not become on-image claims. "
        "Reasonable inference and needs_confirmation belong in planning/risk notes only; "
        "use confirmed evidence for visible copy. No consultation-style CTA. "
        "Follow the source's visual roles, structure and scale diversity rules. "
        "Every screen has one buyer question, one selling point, "
        "a short title and label, physical scene layers, evidence, required/forbidden elements "
        "and a risk reminder. A directly generated designed collage within ONE screen is allowed. "
        "Never put multiple detail screens on a canvas. Use the exact schema below; provide "
        "substantive decisions, no placeholder objects or generic repeated layout descriptions. "
        "Write each image_prompt in full following references/prompt-contract.md. It is passed "
        "verbatim to the image generator. Include all shared anchors, Module Plan, Demand-to-"
        "Selling-Point Match, Page Task Table, Style System Lock, Design Strength Lock and "
        "Product Identity & Physics Lock required by the source, plus the current screen's "
        "specific copy and scene. Start each image_prompt with exactly 'vertical 3:4 e-commerce "
        "detail page image'. The website performs no layout assembly or prompt rewriting. "
        "Ranks are consecutive from 1. module_plan page/role/buyer_demand_id must match screens.\n"
        "Never mention skill names, authors, model names or versions in buyer-facing copy or strategy prose.\n"
        + json.dumps(strategy_schema(request.page_count), ensure_ascii=False)
    )


def compile_prompt(
    *,
    structured_plan: Mapping[str, Any],
    screen: Mapping[str, Any],
    page_number: int,
    page_count: int,
    language: str,
    product_name: str,
    product_information: Any,
    source_roles: Any,
    size: str,
) -> str:
    # Like the main-image source boundary, the adapter executes the authored
    # prompt. It does not replace the skill's creative rules with a local recipe.
    return str(screen["image_prompt"])


def approval_document(payload: Mapping[str, Any]) -> str:
    labels = {
        "analysis": "商品分析与买家需求",
        "plan_protocol_fields": "整套策划与视觉规范",
        "information_confidence": "信息可信度",
        "buyer_demand_map": "买家需求排序",
        "demand_to_selling_point_match": "需求与卖点匹配",
        "product_type_strategy": "商品类型策略",
        "purchase_decision_type": "购买决策类型",
        "page_strategy": "页面策略",
        "best_hero_direction": "首屏方向",
        "module_plan": "模块分工",
        "visual_rhythm_plan": "视觉节奏",
        "campaign_style_lock": "系列风格",
        "style_system_lock": "统一视觉规范",
        "design_strength_lock": "设计强度",
        "product_identity_physics_lock": "商品身份与物理关系",
        "buyer_question": "买家问题",
        "matched_selling_point": "对应卖点",
        "visual_proof_method": "视觉证明",
        "risk_reminder": "待确认与风险",
        "scene_layout": "场景与空间层次",
        "title": "主标题",
        "label": "卖点短句",
        "composition": "构图",
        "difference": "与前屏的区别",
    }
    lines = ["# 精美详情方案", "每屏独立生成；成功保存后交付，不因画质观察自动重绘。"]
    for group in payload.get("groups", []):
        for section in (
            "analysis",
            "plan_protocol_fields",
            "campaign_style_lock",
            "design_strength_lock",
        ):
            lines.append("## " + labels[section])
            for key, value in sorted(group.get(section, {}).items()):
                if key != "screens":
                    lines.append(
                        f"{labels.get(key, key)}：{json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                    )
    for index, page in enumerate(payload.get("pages", []), 1):
        lines.append(f"## 第 {index} 屏 · {page.get('title', '')}")
        lines.append(
            "执行内容指纹："
            + hashlib.sha256(
                str(page.get("image_prompt", "")).encode("utf-8")
            ).hexdigest()
        )
        for key, value in sorted(page.items()):
            if key not in {"item_key", "group_key", "page", "quality", "image_prompt"}:
                lines.append(
                    f"{labels.get(key, key)}：{json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                )
    return "\n\n".join(lines)


def approval_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(approval_document(payload).encode("utf-8")).hexdigest()


def _document_sections(document: str) -> list[tuple[str, list[str]]]:
    """Compare old documents without depending on PostgreSQL JSONB key order."""
    sections: list[tuple[str, list[str]]] = []
    for paragraph in document.split("\n\n"):
        if paragraph.startswith("#") or not sections:
            sections.append((paragraph, []))
            continue
        label, separator, value = paragraph.partition("：")
        if separator:
            with suppress(ValueError, TypeError):
                value = json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True)
            paragraph = label + separator + value
        sections[-1][1].append(paragraph)
    return [(header, sorted(lines)) for header, lines in sections]


def approval_matches(payload: Mapping[str, Any]) -> bool:
    approval = payload.get("refined_approval") or {}
    document = approval.get("document_markdown")
    return (
        isinstance(document, str)
        and hashlib.sha256(document.encode("utf-8")).hexdigest()
        == approval.get("document_sha256")
        and _document_sections(document) == _document_sections(approval_document(payload))
    )


def validate_payload(payload: Mapping[str, Any], page_count: int) -> None:
    from .xinghe_agent import XingheAgentError

    groups, pages = payload.get("groups"), payload.get("pages")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(pages, list):
        raise XingheAgentError(
            "REFINED_DETAIL_PLAN_INVALID", "One complete product strategy is required."
        )
    group = groups[0]
    validate_strategy(
        {
            "analysis": group.get("analysis"),
            "plan": {
                **group.get("plan_protocol_fields", {}),
                "campaign_style_lock": group.get("campaign_style_lock"),
                "design_strength_lock": group.get("design_strength_lock"),
                "screens": pages,
            },
        },
        page_count,
    )
