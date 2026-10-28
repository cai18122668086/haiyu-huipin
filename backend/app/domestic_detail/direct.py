"""Two-call website path: observed intake, one creative brief, native compilation."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "WEBSITE_DIRECT_V2"
SYSTEM = """Create a coherent Chinese ecommerce detail page from the locked product
identity and supplied facts. Return the small requested JSON brief. Design a
strong product opening, distinct evidence/detail/use screens and a useful close.
Preserve every user selling point somewhere, but combine related facts naturally;
observations are product evidence, not extra independent selling-point campaigns.
Use short, truthful Chinese copy and specific visual direction. Choose varied
commercial compositions, camera views and text placement within one visual world.
No pain ledger, formula ledger, sales-depth map, aesthetic scores or runtime JSON.
Do not invent material, performance, measurements, claims or evidence. Background
watermarks are not the product brand. Adjacent screens share a continuous 3:8
canvas, with a concrete background/material/light connection at the middle.
Only product references define identity; style references only guide art direction.
""".strip()


@lru_cache(maxsize=1)
def contract() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "skill_sources"
        / "aurora-domestic-ecommerce-detail-page/scripts/runtime/website_plan.py"
    )
    spec = importlib.util.spec_from_file_location("_polaris_website_detail_v2", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Website detail compiler is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enabled(request: Any) -> bool:
    return (request.intake_settings or {}).get("website_contract_version") == VERSION


def facts_for(request: Any) -> tuple[list[dict[str, Any]], list[str]]:
    from .intake import active_fact_ids

    intake = request.detail_intake or {}
    active = set(active_fact_ids(intake))
    facts = [
        deepcopy(row)
        for kind in ("user_facts", "observed_facts")
        for row in intake.get(kind, [])
        if row["id"] in active
    ]
    required = [
        row["id"] for row in intake.get("user_facts", []) if row["id"] in active
    ]
    return facts, required


def response_format(request: Any) -> dict[str, Any]:
    facts, _ = facts_for(request)
    schema = contract().schema(request.page_count, [row["id"] for row in facts])
    transitions = schema["properties"]["pair_transitions"]
    transitions.pop("minItems")
    transitions.pop("maxItems")
    transitions["anyOf"] = [
        {"minItems": length, "maxItems": length}
        for length in (request.page_count // 2, request.page_count - 1)
    ]
    transitions["description"] = (
        "Ordered transitions: one per two-screen pair, or one per adjacent "
        "screen boundary across the whole page. Never mix these orders."
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "website_detail_brief_v2",
            "strict": False,
            "schema": schema,
        },
    }


def prompt(request: Any) -> str:
    facts, required = facts_for(request)
    value = {
        "screen_count": request.page_count,
        "language": request.language,
        "product_name": request.product_name,
        "target_buyer": (request.intake_settings or {}).get("target_buyer"),
        "identity": (request.detail_intake or {}).get("identity"),
        "facts": [
            {key: row.get(key) for key in ("id", "text", "conditions")} for row in facts
        ],
        "user_fact_ids_to_cover": required,
        "style_reference_analysis": (request.detail_intake or {}).get(
            "reference_materials", []
        ),
        "brand_mode": ((request.detail_intake or {}).get("identity") or {}).get(
            "brand_logo_source"
        ),
    }
    if request.authoring_context:
        value["correction"] = request.authoring_context
    return (
        "Author exactly the requested number of screen briefs. "
        "First screen has a dominant product hero. "
        "Do not repeat the same view, composition and conclusion on adjacent screens. "
        "A detail crop may use IDENTITY_TOKEN_ONLY. "
        "Preserve all real user facts without forcing separate screens "
        "for duplicate observations.\n"
        + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def pair_brief(payload: dict[str, Any], count: int) -> dict[str, Any]:
    """Project authored adjacent edges onto the four/five/six render seams.

    For eight screens, render pairs 01+02, 03+04, 05+06, 07+08 use edges
    0, 2, 4, 6. The original seven-edge reply stays in the immutable receipt.
    No transition text is invented or repeated to fill missing entries.
    """
    value = deepcopy(payload)
    transitions = value.get("pair_transitions")
    if isinstance(transitions, list) and len(transitions) == count - 1:
        value["pair_transitions"] = transitions[::2]
    return value


def errors(payload: object, request: Any) -> list[str]:
    from jsonschema import Draft202012Validator

    facts, required = facts_for(request)
    schema = response_format(request)["json_schema"]["schema"]
    structural = [
        "/" + "/".join(map(str, issue.absolute_path)) + ": " + issue.message
        for issue in Draft202012Validator(schema).iter_errors(payload)
    ]
    if structural:
        return structural
    assert isinstance(payload, dict)
    return list(
        contract().check_brief(
            pair_brief(payload, request.page_count), facts, required, request.page_count
        )
    )


def build(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    facts, required = facts_for(request)
    width, height = map(int, request.size.split("x"))
    canonical = pair_brief(payload, request.page_count)
    canonical = brand_rendering_brief(canonical, request.detail_intake or {})
    value: dict[str, Any] = contract().authored_plan(
        canonical, facts, required, request.page_count, width, height
    )
    if canonical != payload:
        from .plans import canonical_hash

        value["website_brief_projection"] = {
            "version": "adjacent-screen-edges-to-pair-seams-v1",
            "source_brief_sha256": canonical_hash(payload),
            "canonical_brief_sha256": canonical_hash(canonical),
            "source_transition_indices": list(range(0, request.page_count - 1, 2)),
        }
    value["product_intake"] = deepcopy(request.detail_intake)
    return value


def brand_rendering_brief(
    brief: dict[str, Any], intake: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep the source compiler intact while preserving physical package marks."""
    if (intake.get("automatic_brand_handling") or {}).get(
        "decision"
    ) != "omit_independent_logo":
        return brief
    value = deepcopy(brief)
    instruction = (
        "保留商品本体已有包装文字和标识，只禁止额外添加独立品牌标志；"
        "不得擦除、替换商品上的真实标识，不宣称商品无品牌。"
    )
    rendering = value["style"]["product_rendering"]
    if instruction not in rendering:
        value["style"]["product_rendering"] = rendering + "；" + instruction
    return value


async def complete(
    coordinator: Any, request: Any, result: Any, results: list[Any]
) -> dict[str, Any]:
    from ..xinghe_agent import (
        AuroraDetailHarnessError,
        XingheAgentError,
        validate_aurora_domestic_plan,
    )

    original = result
    extra = []
    problems = errors(result.payload, request)
    try:
        if problems:
            correction = replace(
                request,
                authoring_context={
                    "prior_response": result.payload,
                    "validation_errors": problems,
                },
                idempotency_key=request.idempotency_key + ":correction-1",
            )
            result = await coordinator._native_planner_call(correction)
            extra.append(result)
            if result.error_code:
                raise XingheAgentError(
                    result.error_code,
                    result.error_message or "Website brief result unavailable",
                )
            problems = errors(result.payload, request)
        if problems:
            raise XingheAgentError(
                "WEBSITE_DETAIL_BRIEF_INVALID",
                "; ".join(problems),
                validation_errors=[{"path": "/", "message": item} for item in problems],
            )
        value = build(result.payload, request)
        structured = validate_aurora_domestic_plan(
            value, request.page_count, skill_root=coordinator.skill_root
        )
        structured["native_approval"] = await asyncio.to_thread(
            coordinator._preflight_aurora_plan, request, value
        )
        return structured
    except AuroraDetailHarnessError as exc:
        raise XingheAgentError("WEBSITE_DETAIL_COMPILE_INVALID", str(exc)) from exc
    finally:
        results[-1] = replace(
            original,
            phase="aurora_domestic_detail",
            supplemental_results=(*original.supplemental_results, *extra),
        )


def validate_plan(value: Mapping[str, Any], page_count: int) -> None:
    intake = value.get("product_intake") or {}
    from .intake import active_fact_ids

    active = set(active_fact_ids(intake))
    facts = [
        row
        for kind in ("user_facts", "observed_facts")
        for row in intake.get(kind, [])
        if row["id"] in active
    ]
    required = [
        row["id"] for row in intake.get("user_facts", []) if row["id"] in active
    ]
    strategy = value["output_strategy"]
    expected = contract().authored_plan(
        brand_rendering_brief(value["website_brief"], intake),
        facts,
        required,
        page_count,
        strategy["canvas_width_px"],
        strategy["module_height_px"],
    )
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError("Website source brief, source facts and screen views disagree")


def mock_brief(request: Any) -> dict[str, Any]:
    """Explicit mock provider fixture, never a real-planner fallback."""
    facts, required = facts_for(request)
    ids = required or [row["id"] for row in facts]
    screens: list[dict[str, Any]] = [
        {
            "duty": f"SYNTHETIC screen {i + 1}",
            "title": f"合成商品细节{i + 1}",
            "supporting_copy": [],
            "fact_ids": [ids[i % len(ids)]],
            "visual_direction": f"SYNTHETIC product detail view {i + 1}",
            "composition": f"SYNTHETIC distinct layout {i + 1}",
            "camera": "medium" if i % 2 == 0 else "close",
            "product_role": "DOMINANT",
        }
        for i in range(request.page_count)
    ]
    # Many supplied facts may share a screen; don't lose them in a mock fixture.
    for index, fact in enumerate(ids):
        assigned = screens[index % len(screens)]["fact_ids"]
        if fact not in assigned:
            assigned.append(fact)
    return {
        "campaign_thesis": "SYNTHETIC_OFFLINE_FIXTURE",
        "style": {
            key: "SYNTHETIC visual passport " + key for key in contract().STYLE_FIELDS
        },
        "screens": screens,
        "pair_transitions": [
            f"SYNTHETIC continuous background {i + 1}"
            for i in range(request.page_count // 2)
        ],
    }
