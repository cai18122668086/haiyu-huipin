# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from app.aurora_detail_runtime import normalize_native_plan_for_harness
from app.domestic_detail_provider import (
    DOMESTIC_DETAIL_SKILL_ID,
    domestic_detail_prompt_configuration,
)
from app.provider import ProviderArtifact
from app.xinghe_agent import (
    PlannerRequest,
    XingheImageProvider,
    _complete_aurora_module_structure,
    _normalize_aurora_style_constitution,
    _planner_response_format,
    _split_aurora_pair_artifact,
    _validated_image_size,
)


def test_aurora_domestic_contract_is_pair_first_and_serial() -> None:
    contract = domestic_detail_prompt_configuration(
        {"page_count": 8, "platform_code": "tmall"}
    )
    assert DOMESTIC_DETAIL_SKILL_ID == "aurora-domestic-ecommerce-detail-page"
    assert contract["pair_count"] == 4
    assert contract["pair_aspect_ratio"] == "3:8"
    assert contract["screen_aspect_ratio"] == "3:4"
    assert contract["pair_generation"] == "one_3_8_canvas_then_deterministic_two_3_4_slices"
    assert contract["reference_edit_concurrency"] == 1
    assert contract["approval_gate"]["auto_confirm_after_validation"] is False
    assert contract["pixel_audit"]["max_automatic_repairs"] == 1


@pytest.mark.parametrize("count", [1, 6, 9, 11, 13])
def test_aurora_rejects_non_canonical_module_count(count: int) -> None:
    with pytest.raises(ValueError, match="8, 10, or 12"):
        domestic_detail_prompt_configuration(
            {"page_count": count, "platform_code": "tmall"}
        )


def test_pair_canvas_is_deterministically_split_and_hash_bound() -> None:
    source = BytesIO()
    image = Image.new("RGB", (1200, 3200), (30, 90, 180))
    image.save(source, format="PNG")
    payload = source.getvalue()
    pair = ProviderArtifact(
        payload=payload,
        filename="pair.png",
        content_type="image/png",
        width=1200,
        height=3200,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    first, second = _split_aurora_pair_artifact(
        pair,
        screen_size="1200x1600",
        pair_id="P01",
        group_key="primary-product",
    )
    assert (first.width, first.height) == (1200, 1600)
    assert (second.width, second.height) == (1200, 1600)
    assert first.content_type == second.content_type == "image/png"
    assert first.sha256 == hashlib.sha256(first.payload).hexdigest()
    assert second.sha256 == hashlib.sha256(second.payload).hexdigest()


def test_pair_canvas_storage_key_is_stable_and_workspace_scoped() -> None:
    key = XingheImageProvider._aurora_pair_canvas_object_key(
        "ws_demo", "job_demo", "primary-product", "P01"
    )
    assert key.startswith("ws_demo/generated/aurora-pairs/")
    assert key.endswith(".png")
    assert key == XingheImageProvider._aurora_pair_canvas_object_key(
        "ws_demo", "job_demo", "primary-product", "P01"
    )
    assert key != XingheImageProvider._aurora_pair_canvas_object_key(
        "ws_other", "job_demo", "primary-product", "P01"
    )


def test_aurora_1440_screen_and_pair_sizes_are_supported() -> None:
    """The domestic UI's 1440x1920 slice must pass the shared request gate."""

    assert _validated_image_size("1440x1920") == "1440x1920"
    assert _validated_image_size("1440x3840") == "1440x3840"


def test_empty_commercial_comfort_forbidden_details_get_safe_default() -> None:
    repaired = _complete_aurora_module_structure(
        {
            "lead_conclusion": "商品结构清晰易于理解",
            "expression": {
                "commercial_comfort": {
                    "aversion_risk": "LOW",
                    "discomfort_target": "LOW",
                    "sanitization_strategy": "DIRECT_BUT_CLEAN",
                    "detail_dominance_policy": "SUPPORTING_ONLY",
                    "forbidden_visual_details": [],
                    "replacement_expression": "用干净的使用结果表达购买结论。",
                }
            },
        }
    )

    assert repaired["expression"]["commercial_comfort"][
        "forbidden_visual_details"
    ] == ["不展示不适或刺激性特写"]


def test_malformed_commercial_comfort_details_are_filtered_without_rewriting_valid_items() -> None:
    repaired = _complete_aurora_module_structure(
        {
            "lead_conclusion": "商品结构清晰易于理解",
            "expression": {
                "commercial_comfort": {
                    "forbidden_visual_details": ["", "保留的安全边界", 3],
                }
            },
        }
    )

    assert repaired["expression"]["commercial_comfort"][
        "forbidden_visual_details"
    ] == ["保留的安全边界"]


def test_null_optional_pain_expression_is_removed_without_inventing_a_pain() -> None:
    repaired = _complete_aurora_module_structure(
        {
            "lead_conclusion": "商品结构清晰易于理解",
            "expression": {"pain_expression": None},
        }
    )

    assert "pain_expression" not in repaired["expression"]


def test_missing_style_constitution_gets_neutral_structural_defaults() -> None:
    root: dict[str, object] = {}
    _normalize_aurora_style_constitution(root)

    style = root["style_constitution"]
    assert isinstance(style, dict)
    assert style["visual_world"] == "基于已批准商品素材的清晰电商展示空间"
    assert style["product_rendering"] == "保持商品外观、结构和比例以已批准素材为准"


def test_provider_schemas_are_self_contained_and_match_each_repair_phase() -> None:
    request = PlannerRequest(
        workspace_id="ws_schema", job_id="job_schema", group_key="primary",
        sources=(), page_count=8, language="zh-CN", product_name="商品",
        product_information=(), platform_code="tmall", size="1440x1920",
        idempotency_key="schema", planning_profile="aurora_domestic_detail",
    )
    schema = _planner_response_format(request)["json_schema"]["schema"]
    assert '"$ref"' not in json.dumps(schema)
    module_fields = schema["properties"]["modules"]["items"]
    assert set(module_fields["required"]).issubset(module_fields["properties"])
    for field in ("proof", "expression", "structure", "typography"):
        nested = module_fields["properties"][field]
        assert set(nested["required"]).issubset(nested["properties"])
    assert module_fields["properties"]["lead_claim_id"]["type"] == "string"
    assert module_fields["properties"]["selling_point_cluster_ids"]["type"] == "array"
    campaign = _planner_response_format(replace(request, planning_profile="aurora_domestic_campaign_completion"))["json_schema"]["schema"]
    assert "modules" not in campaign["required"]
    assert "selling_point_depth_contract" in campaign["required"]
    module = _planner_response_format(replace(request, planning_profile="aurora_domestic_modules_completion"))["json_schema"]["schema"]
    assert module["required"] == ["modules"]
    assert module["properties"]["modules"]["maxItems"] == 1
    evidence = module["properties"]["modules"]["items"]["properties"]["visual_evidence"]
    assert evidence["type"] == "object"
    assert evidence["required"] == ["evidence_class", "proof_device", "visible_content", "forbidden_inventions"]
    clusters = campaign["properties"]["output_strategy"]["properties"]["selling_point_clusters"]
    assert "fact_ids" in clusters["items"]["required"]
    assert "assigned_module_ids" in clusters["items"]["required"]
    patch = _planner_response_format(replace(request, planning_profile="aurora_domestic_plan_repair"))["json_schema"]["schema"]
    assert not patch.get("required")
    assert patch["properties"]["modules"]["items"]["required"] == ["id"]
    assert not patch["properties"]["modules"]["items"]["properties"]["proof"].get("required")


def test_claim_aliases_follow_explicit_sources_and_preserve_grouped_facts() -> None:
    plan = {
        "selling_point_depth_contract": {"primary_claims": [
            {"claim_id": "C01", "fact_ids": ["F01", "F02", "F03"]},
            {"claim_id": "C02", "fact_ids": ["F04"]},
            {"claim_id": "C03", "source_fact_ids": ["F05"]},
        ]},
        "output_strategy": {"claim_coverage": [
            {"claim_id": "C01", "disposition": "LEAD", "module_ids": ["M01"]},
            {"claim_id": "C02", "disposition": "LEAD", "module_ids": ["M02"]},
            {"claim_id": "C03", "disposition": "LEAD", "module_ids": ["M03"]},
        ]},
        "modules": [
            {"id": "M01", "lead_claim_id": "C01"},
            {"id": "M02", "lead_claim_id": "C02"},
            {"id": "M03", "lead_claim_id": "C03"},
            {"id": "M04", "lead_claim_id": "C99"},
        ],
    }
    normalized = normalize_native_plan_for_harness(plan)
    assert [m["lead_claim_id"] for m in normalized["modules"]] == ["F01", "F04", "F05", "C99"]
    assert normalized["modules"][0]["supporting_claim_ids"] == ["F02", "F03"]
    assert {c["claim_id"] for c in normalized["output_strategy"]["claim_coverage"]} == {"F01", "F02", "F03", "F04", "F05"}
    assert normalized["selling_point_depth_contract"]["primary_claim_ids"] == ["F01", "F04", "F05"]
    assert plan["modules"][1]["lead_claim_id"] == "C02"

