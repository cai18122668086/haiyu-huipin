# ruff: noqa: E501
"""Independent domestic detail contract: shared planning, independent canvases.

The planner authors the creative decisions once. This module only serializes
the shared locks and each page's decisions; it never invents copy or repairs an
incomplete strategy. Original detail contracts remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from . import refined_detail
from .new_detail_skill_provider import DetailSkillRoute, NewDetailSkillProvider

if TYPE_CHECKING:
    from .provider import PersistedProviderArtifact, Provider, ProviderArtifact
    from .xinghe_agent import PlannerRequest

SKILL_ID = "xinghe-ecommerce-detail-domestic-4-fast"
PIPELINE_VERSION = "xinghe-domestic-detail-v4.0-fast"
PROFILE = "xinghe_domestic_detail_4_fast"
CONCURRENCY = 20
PROTOCOL_FILES = (
    "SKILL.md",
    "references/buyer-demand-strategy.md",
    "references/page-planning-strategy.md",
    "references/design-strength-system.md",
    "references/product-consistency-physics.md",
    "references/compliance.md",
)

_PREFIX = "vertical 3:4 e-commerce detail page image"
_BOUNDARIES = (
    "Render exactly ONE independent detail page, never multiple screens or a contact sheet. "
    "Product-reference photos and explicitly confirmed user facts establish product truth. "
    "Style-reference images establish visual grammar only; never borrow their brand, price, "
    "promotions, certifications, measurements or product claims. Select the declared primary "
    "product reference; other package versions must not be mixed into it. Preserve its "
    "silhouette, proportions, colors/material appearance, structural relationships, visible "
    "logo/label position and physically plausible contact, perspective, shadows and occlusion. "
    "Unreadable marks remain visual placement, never invented text. Do not add accessories "
    "or structures absent from the evidence. Source framing is not locked: create the "
    "page-specific designed composition. A white-background source is not a finished page. "
    "Only confirmed evidence may become visible copy. Unknown or inferred efficacy, "
    "materials, parameters, tests, certifications, sales, reviews, prices, gifts and before/after "
    "curves must not become claims. Never fabricate data charts. Workflow notes, source "
    "watermarks, skill/model names and private risk notes are not on-image copy. "
    "Render the exact authored title, label and approved copy clearly; no consultation CTA. "
    "No local collage, resizing or text overlay is performed after generation."
)


def is_fast(configuration: Mapping[str, Any]) -> bool:
    return configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION


def prompt_configuration(settings: Mapping[str, Any]) -> dict[str, Any]:
    return refined_detail.prompt_configuration(settings) | {
        "profile_id": PROFILE,
        "delivery": {"qa_mode": "not_reviewed", "qa_status_required": False},
    }


class FastDetailProvider(NewDetailSkillProvider):
    def __init__(self, delegate: Provider) -> None:
        super().__init__(
            delegate, DetailSkillRoute(SKILL_ID, "domestic", prompt_configuration)
        )

    def _native_delivery_receipt(
        self,
        artifact: ProviderArtifact | PersistedProviderArtifact,
        expected: list[int],
    ) -> dict[str, Any] | None:
        return refined_detail.native_delivery_receipt(artifact, expected)


def strategy_schema(page_count: int) -> dict[str, Any]:
    schema = refined_detail.strategy_schema(page_count)
    screen = schema["properties"]["plan"]["properties"]["screens"]["items"]
    del screen["properties"]["image_prompt"]
    screen["required"].remove("image_prompt")
    screen["properties"]["page_prompt"] = {"type": "string", "minLength": 1}
    screen["required"].append("page_prompt")
    lock = deepcopy(
        schema["properties"]["plan"]["properties"]["product_identity_physics_lock"]
    )
    schema["properties"]["plan"]["properties"]["product_identity_physics_lock"] = lock
    lock.update(
        properties={
            "primary_product_source": {"type": "integer", "minimum": 1},
            "identity": {"type": "string", "minLength": 1},
            "do_not_change": refined_detail._strings(),
            "physical_logic": {"type": "string", "minLength": 1},
        },
        required=[
            "primary_product_source",
            "identity",
            "do_not_change",
            "physical_logic",
        ],
    )
    return schema


def planner_prompt(request: PlannerRequest) -> str:
    settings = request.intake_settings or {}
    return (
        f"Author one complete domestic ecommerce detail strategy with exactly {request.page_count} "
        f"independent 3:4 pages, language {request.language}, canvas {request.size}. "
        f"Product: {request.product_name}. Authorized user information: "
        f"{json.dumps(list(request.product_information), ensure_ascii=False)}. "
        f"Category: {settings.get('category') or 'infer from product images'}. "
        f"Target buyer: {settings.get('target_buyer') or 'derive conservatively'}. "
        f"Platform: {request.platform_code}. Brand policy: "
        f"{settings.get('brandless_confirmation') or 'only authorized visible product marks'}. "
        "This is one combined planning call, not a revision or quality audit. "
        "Output shared identity, confirmed facts, buyer demand mapping, campaign/style/design "
        "locks and module plan ONCE in analysis/plan. Explicitly select the 1-based "
        "primary_product_source using its position among ALL input images (it must have "
        "product_reference role), retain its packaging identity "
        "and list do_not_change constraints. Do not mix packaging variants. "
        "For each screen, author page_prompt with only its unique visual direction, camera, "
        "composition, visual proof and copy treatment; do not repeat shared locks or the whole "
        "module table. The server combines shared locks and current page fields verbatim into "
        "a complete independent generation prompt. No mechanical design template replaces your "
        "creative decisions. Keep all requested pages, titles, labels, exact body copy, buyer "
        "question, evidence and scene layers. Meaningful layout/scale diversity is required. "
        "Ranks start at 1, module page/role/buyer_demand_id match corresponding screens. "
        "Only confirmed facts may appear in visible text; reasonable inference and missing "
        "evidence stay in private risk notes. Distinguish product truth from style inspiration. "
        + _BOUNDARIES
        + "\nReturn only the COMPLETE data INSTANCE matching this schema, with exactly "
        "analysis and plan at the ROOT. Do not return a JSON Schema or wrap the "
        "instance in type/properties. Do not output image_prompt.\n"
        + json.dumps(strategy_schema(request.page_count), ensure_ascii=False)
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compiled(structured_plan: Mapping[str, Any], screen: Mapping[str, Any]) -> str:
    analysis, plan = structured_plan["analysis"], structured_plan["plan"]
    shared = {
        "Confirmed Facts": analysis["information_confidence"]["confirmed"],
        "Product Consistency Anchors": analysis["product_consistency_anchors"],
        "Campaign Style Lock": plan["campaign_style_lock"],
        "Style System Lock": plan["style_system_lock"],
        "Design Strength Lock": plan["design_strength_lock"],
        "Product Identity & Physics Lock": plan["product_identity_physics_lock"],
        "Purchase Decision Type": plan["purchase_decision_type"],
        "Page Strategy": plan["page_strategy"],
    }
    # Private risk/inference fields are explicitly labeled, never buyer copy.
    page = {
        key: value
        for key, value in screen.items()
        if key
        not in {
            "image_prompt",
            "item_key",
            "group_key",
            "page",
            "quality",
        }
    }
    return "\n".join(
        (
            _PREFIX,
            _BOUNDARIES,
            "SHARED AUTHORED LOCKS: " + _json(shared),
            "CURRENT PAGE TASK (risk_reminder and required_proof are private constraints, "
            "not text to print): " + _json(page),
        )
    )


def _validate_raw(value: Mapping[str, Any], page_count: int) -> None:
    from .xinghe_agent import XingheAgentError

    if not 1 <= page_count <= 12:
        raise XingheAgentError(
            "DETAIL_FAST_PLAN_INVALID", "A complete 1–12 page strategy is required."
        )
    errors = list(Draft202012Validator(strategy_schema(page_count)).iter_errors(value))
    if errors:
        error = errors[0]
        raise XingheAgentError(
            "DETAIL_FAST_PLAN_INVALID",
            error.message,
            json_pointer="/" + "/".join(str(part) for part in error.absolute_path),
        )


def normalize_strategy(value: Mapping[str, Any]) -> dict[str, Any]:
    from .xinghe_agent import XingheAgentError

    # Some JSON-object relays wrap authored instance values in schema-shaped
    # keys. Unwrap only this exact unambiguous carrier; never infer fields or
    # accept a real schema/partial strategy as a usable plan.
    if "type" in value or "properties" in value:
        properties = value.get("properties")
        if (
            set(value) != {"type", "properties"}
            or value.get("type") not in ("object", "json_object")
            or not isinstance(properties, Mapping)
            or set(properties) != {"analysis", "plan"}
        ):
            raise XingheAgentError(
                "DETAIL_FAST_PLAN_INVALID",
                "The strategy instance envelope is ambiguous.",
            )
        value = properties
    result = refined_detail.normalize_strategy(value)
    plan = result.get("plan")
    screens = plan.get("screens") if isinstance(plan, Mapping) else None
    count = len(screens) if isinstance(screens, list) else 0
    _validate_raw(result, count)
    for screen in result["plan"]["screens"]:
        screen["image_prompt"] = _compiled(result, screen)
    return result


def validate_strategy(value: Mapping[str, Any], page_count: int) -> None:
    from .xinghe_agent import XingheAgentError

    _validate_raw(value, page_count)
    refined_detail.validate_strategy(value, page_count)
    for screen in value["plan"]["screens"]:
        if screen["image_prompt"] != _compiled(value, screen):
            raise XingheAgentError(
                "DETAIL_FAST_PROMPT_MISMATCH",
                "A frozen page prompt or shared lock was changed.",
            )


def validate_source_selection(
    structured_plan: Mapping[str, Any], source_roles: Any
) -> None:
    """Validate role bindings while the planner failure can still be recorded."""
    from .xinghe_agent import XingheAgentError

    primary = structured_plan["plan"]["product_identity_physics_lock"][
        "primary_product_source"
    ]
    if primary > len(source_roles) or source_roles[primary - 1] != "product_reference":
        raise XingheAgentError(
            "DETAIL_FAST_IDENTITY_SOURCE_INVALID",
            "Primary identity must refer to an available product-reference image.",
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
    from .xinghe_agent import XingheAgentError

    if source_roles is not None:
        validate_source_selection(structured_plan, source_roles)
    prompt = _compiled(structured_plan, screen)
    if screen.get("image_prompt") != prompt:
        raise XingheAgentError(
            "DETAIL_FAST_PROMPT_MISMATCH",
            "Frozen prompt does not match authored locks.",
        )
    return prompt


def approval_document(payload: Mapping[str, Any]) -> str:
    return refined_detail.approval_document(payload).replace(
        "# 精美详情方案", "# 国内详情图·快速方案", 1
    )


def approval_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(approval_document(payload).encode()).hexdigest()


def approval_matches(payload: Mapping[str, Any]) -> bool:
    approval = payload.get("refined_approval") or {}
    document = approval.get("document_markdown")
    return (
        isinstance(document, str)
        and hashlib.sha256(document.encode()).hexdigest()
        == approval.get("document_sha256")
        and refined_detail._document_sections(document)
        == refined_detail._document_sections(approval_document(payload))
    )


def validate_payload(payload: Mapping[str, Any], page_count: int) -> None:
    from .xinghe_agent import XingheAgentError

    groups, pages = payload.get("groups"), payload.get("pages")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(pages, list):
        raise XingheAgentError(
            "DETAIL_FAST_PLAN_INVALID", "One complete product strategy is required."
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


def fake_strategy(base: dict[str, Any], request: PlannerRequest) -> dict[str, Any]:
    from .refined_detail_mock import fake_strategy as original_fake

    result = original_fake(deepcopy(base), request)
    result["plan"]["product_identity_physics_lock"] = {
        "primary_product_source": 1,
        "identity": "仅使用合成测试输入",
        "do_not_change": ["合成主体与包装身份"],
        "physical_logic": "合成空间的接触、阴影和透视一致",
    }
    for screen in result["plan"]["screens"]:
        screen["page_prompt"] = screen.pop("image_prompt")
    return result
