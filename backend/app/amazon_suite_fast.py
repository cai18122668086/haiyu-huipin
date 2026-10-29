"""One-pass Amazon planning, isolated from the original suite's QA policy.

The compact wire shape omits only runtime-owned size/type repetitions. All
creative decisions and the full 21-item inventory are mandatory. Expansion is
deterministic and never invents a missing design or repairs truncated output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from .xinghe_agent import PlannerRequest

PROFILE = "amazon_visual_suite_fast"
SKILL_ID = "xinghe-amazon-visual-suite-fast"
POLICY_VERSION = "amazon-visual-suite-fast-v1"
CONCURRENCY = 20
PROTOCOL_FILES = ("SKILL.md", "references/fast-planning.md")


def native_canvas_valid(width: int, height: int, spec: Any) -> bool:
    """Validate provider pixels, never fabricate the requested export size.

    Desktop banners retain their minimum module canvas. Other canvases use
    the established high-resolution original threshold, with their own aspect
    ratios; requested dimensions and high quality remain unchanged.
    """
    if min(width, height) <= 0 or max(width, height) > 10_000:
        return False
    if abs((width / height) / (spec.width / spec.height) - 1) > 0.01:
        return False
    if spec.group == "a_desktop":
        return width >= spec.width and height >= spec.height
    return min(width, height) >= 1024 or (
        min(width, height) >= 768 and width * height >= 1_500_000
    )


def selected(settings: Mapping[str, Any]) -> bool:
    nested = settings.get("settings")
    if isinstance(nested, Mapping):
        settings = nested
    return settings.get("amazon_speed") == "fast"


def planner_json_schema() -> dict[str, Any]:
    from .xinghe_agent import _amazon_planner_json_schema

    schema = deepcopy(_amazon_planner_json_schema())
    analysis = schema["properties"]["analysis"]
    analysis["properties"]["primary_product_source_id"] = {
        "type": "string",
        "minLength": 1,
    }
    analysis["required"].append("primary_product_source_id")
    screen = schema["properties"]["plan"]["properties"]["screens"]["items"]
    for name in ("asset_type", "final_size"):
        del screen["properties"][name]
        screen["required"].remove(name)
    return schema


def planner_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "amazon_visual_suite_fast_plan",
            "strict": True,
            "schema": planner_json_schema(),
        },
    }


def planner_prompt(request: PlannerRequest) -> str:
    from .amazon_visual_suite_provider import (
        AMAZON_ITEM_ROLES,
        AMAZON_ITEM_SPECS,
        AMAZON_SKILL_ROW_ID_BY_ITEM_KEY,
    )

    inventory = [
        {
            "asset_id": source.asset_id,
            "role": source.source_role,
            "filename": source.filename,
        }
        for source in request.sources
    ]
    table = [
        {
            "id": AMAZON_SKILL_ROW_ID_BY_ITEM_KEY[spec.item_key],
            "role": role,
            "asset_type": spec.asset_type,
            "final_size": f"{spec.width}x{spec.height}",
            "buyer_question": spec.buyer_question,
            "visual_proof": spec.visual_proof,
        }
        for spec, role in zip(AMAZON_ITEM_SPECS, AMAZON_ITEM_ROLES, strict=True)
    ]
    envelope = {
        "product": request.product_name,
        "marketplace": request.platform_code,
        "language": request.language,
        "authorized_information": list(request.product_information),
        "source_inventory": inventory,
        "canonical_task_table": table,
    }
    return (
        "Author one complete 21-image Amazon suite in one JSON response. "
        "Choose one white_background_product asset as primary_product_source_id; "
        "its exact SKU, packaging version, labels and colors govern the suite. "
        "Other product images only supply compatible visible detail; references "
        "and competitors never establish product facts. Return shared product "
        "analysis, confidence, Product Master, campaign style, consistency and "
        "layout strategy once. Each canonical screen must contain its own buyer "
        "question, one selling point, confidence, visual proof, exact headline "
        "and subcopy, specific layout_direction, source_material role labels and "
        "item-local risk_note. Do not repeat shared locks in individual rows. "
        "Do not output asset_type or final_size: the server binds their canonical "
        "values; every other creative field must be fully authored. Return all "
        "21 ids in canonical order; never abbreviate the array. Main image copy "
        "must be empty, with a real product on white, authentic existing branding "
        "preserved and no added text/props/badges. Desktop and mobile A+ share "
        "selling logic but each needs a separately designed composition for its "
        "own aspect ratio; never describe the mobile version as a crop, stretch "
        "or resized desktop. For each layout specify subject/copy positions, "
        "visual scale and evidence hierarchy. Write concise design decisions, "
        "not generation/audit instructions. Unknown claims and user-prohibited "
        "claims stay off-image; no invented before/after data, certification, "
        "ratings, pricing, gifts or test results. Use the exact task table below "
        "as the inventory and role contract. JSON only. Input="
        + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    )


def validate_planner_payload(
    payload: object,
    *,
    sources: Sequence[object] = (),
) -> dict[str, Any]:
    from .amazon_visual_suite_provider import (
        AMAZON_ITEM_SPECS,
        validate_amazon_planner_payload,
    )
    from .xinghe_agent import XingheAgentError

    error = next(Draft202012Validator(planner_json_schema()).iter_errors(payload), None)
    if error is not None:
        path = "/" + "/".join(str(value) for value in error.absolute_path)
        raise XingheAgentError(
            "AMAZON_FAST_PLAN_INVALID",
            "The complete fast suite schema is required: " + error.message,
            json_pointer=path,
        )
    assert isinstance(payload, Mapping)
    structured = deepcopy(dict(payload))
    primary_id = structured["analysis"]["primary_product_source_id"]
    if sources and not any(
        getattr(source, "asset_id", None) == primary_id
        and getattr(source, "source_role", None) == "white_background_product"
        for source in sources
    ):
        raise XingheAgentError(
            "AMAZON_FAST_PRIMARY_SOURCE_INVALID",
            "The primary identity source must be a supplied real product image.",
            json_pointer="/analysis/primary_product_source_id",
        )
    rows = structured["plan"]["screens"]
    for row, spec in zip(rows, AMAZON_ITEM_SPECS, strict=True):
        row["asset_type"] = spec.asset_type
        row["final_size"] = f"{spec.width}x{spec.height}"
    if rows[0]["headline"].strip() or rows[0]["subcopy"].strip():
        raise XingheAgentError(
            "AMAZON_FAST_MAIN_COPY_INVALID",
            "The main image cannot add copy.",
            json_pointer="/plan/screens/0/headline",
        )
    # This is a structural check, never a claim that visual quality was reviewed.
    for offset in range(7):
        desktop = " ".join(rows[7 + offset]["layout_direction"].casefold().split())
        mobile = " ".join(rows[14 + offset]["layout_direction"].casefold().split())
        if desktop == mobile:
            raise XingheAgentError(
                "AMAZON_FAST_RESPONSIVE_LAYOUT_INVALID",
                "Desktop and mobile require independently authored compositions.",
                json_pointer=f"/plan/screens/{14 + offset}/layout_direction",
            )
    return validate_amazon_planner_payload(structured, sources=sources)


def fake_planner_payload(request: PlannerRequest) -> dict[str, Any]:
    """No-network fixture using the same compact contract as the real gateway."""
    from .xinghe_agent import _fake_amazon_plan

    payload = _fake_amazon_plan(request)
    primary = next(
        (
            source.asset_id
            for source in request.sources
            if source.source_role == "white_background_product"
        ),
        "missing-primary-product",
    )
    payload["analysis"]["primary_product_source_id"] = primary
    for row in payload["plan"]["screens"]:
        del row["asset_type"]
        del row["final_size"]
    payload["plan"]["screens"][0].update(headline="", subcopy="")
    return payload


def validate_expanded_payload(
    payload: object, *, sources: Sequence[object] = ()
) -> dict[str, Any]:
    """Validate edited/restored plans with exactly the original fast contract.

    Only metadata previously bound by the server is removed. The complete
    authored contract remains required, including the primary identity source.
    Metadata itself is validated before removal so an edited size/type cannot
    silently be reset to its canonical value and treated as accepted.
    """
    from .amazon_visual_suite_provider import (
        AMAZON_ITEM_SPECS,
        validate_amazon_planner_payload,
    )
    from .xinghe_agent import XingheAgentError

    validated = validate_amazon_planner_payload(payload, sources=sources)
    compact = deepcopy(validated)
    for index, (row, spec) in enumerate(
        zip(compact["plan"]["screens"], AMAZON_ITEM_SPECS, strict=True)
    ):
        if row["asset_type"] != spec.asset_type:
            raise XingheAgentError(
                "AMAZON_TARGET_TYPE_MISMATCH",
                "The item must retain its canonical Amazon asset type.",
                json_pointer=f"/plan/screens/{index}/asset_type",
            )
        for name in ("asset_type", "final_size", "item_key", "role"):
            row.pop(name, None)
    return validate_planner_payload(compact, sources=sources)
