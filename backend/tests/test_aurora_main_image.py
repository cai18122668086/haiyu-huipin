from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.aurora_main_image_planner import (
    PAGE_ROLES,
    AuroraPlanningError,
    _bind_identity_evidence,
    _build_raw,
    _public_plan_payload,
)
from app.aurora_main_image_provider import (
    _VISUAL_CHECKS,
    AuroraMainImageProvider,
    _authorized_fact_pool_for_audit,
    _bounded_visual_audit_sources,
    _classify_transient_failure,
    _generation_sources,
    _is_shadow_composition_gate_failure,
    _merge_identity_coverage_judgment,
    _normalize_aurora_square_artifact,
    _normalize_parallel_set_audit_payload,
    _normalize_set_role_audit_payload,
    _normalize_style_calibration_payload,
    _normalize_visual_judgment_payload,
    _required_audit_check_names,
    _safe_planner_diagnostics,
    _v20_audit_failure_message,
    _v20_gateway_error,
    _visual_judgment_schema,
)
from app.aurora_main_image_runtime import (
    AuroraHarnessError,
    AuroraHarnessRunner,
    AuroraV20HarnessRunner,
    verify_aurora_v20_source,
)
from app.provider import ProviderArtifact, ProviderOutcome, ProviderRequest
from app.routes_v1 import _new_job_from_payload, runtime_status_response
from app.schemas_v1 import DomesticMainImageJobCreate
from app.services import canonical_hash
from app.xinghe_agent import (
    FakeXingheGateway,
    PlannerRequest,
    SourceImage,
    _planner_response_format,
)


def test_v20_identity_coverage_merges_only_identity_failure() -> None:
    primary = {
        "checks": [
            {"name": "product_identity_match", "passed": True, "evidence": "primary"},
            {"name": "page_role_fit", "passed": True, "evidence": "layout"},
        ],
        "deficiencies": [],
    }
    coverage = {
        "checks": [
            {
                "name": "product_identity_match",
                "passed": False,
                "evidence": "fourth source disagrees",
            },
            {
                "name": "page_role_fit",
                "passed": False,
                "evidence": "coverage-only layout observation",
            },
        ],
        "deficiencies": [
            {"check": "product_identity_match", "detail": "fourth source disagrees"},
        ],
    }

    _merge_identity_coverage_judgment(primary, coverage)

    checks = {item["name"]: item for item in primary["checks"]}
    assert checks["product_identity_match"]["passed"] is False
    assert checks["page_role_fit"]["passed"] is True
    assert any(
        item.get("check") == "product_identity_match"
        for item in primary["all_material_deficiencies"]
    )


def test_domestic_main_image_contract_is_exactly_six_page_input() -> None:
    payload = DomesticMainImageJobCreate.model_validate(
        {
            "type": "domestic_main_image",
            "input": {"product_asset_ids": ["asset_one", "asset_two"]},
            "configuration": {
                "product_name": "轻量随行杯",
                "category": "保温杯",
                "selling_points": ["杯盖防误开"],
            },
        }
    )
    job, roles = _new_job_from_payload("workspace_one", payload)
    assert job.expected_item_count == 6
    assert roles == [
        ("asset_one", "product_identity_authority", 0),
        ("asset_two", "product_identity_authority", 1),
    ]


def test_v20_audit_schema_binds_page_layout_tokens_exactly() -> None:
    request = PlannerRequest(
        workspace_id="workspace_one",
        job_id="job_layout_schema",
        group_key="job_layout_schema",
        sources=(),
        page_count=1,
        language="zh-CN",
        product_name="商品",
        product_information=(
            json.dumps(
                {
                    "layout_observation_contract": {
                        "composition_archetype_seen": "ASYMMETRIC_SEARCH_FRAME",
                        "product_zone_seen": "RIGHT_OR_CENTER_DOMINANT",
                        "copy_zone_seen": "LEFT_OR_INTERLOCKED",
                        "dominant_view_seen": "FULL_PRODUCT_HERO",
                        "evidence_topology_seen": "UNEVEN_MODULES_PLUS_REAL_ACTION",
                    }
                }
            ),
        ),
        platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
        size="1024x1024",
        idempotency_key="layout-schema",
        planning_profile="aurora_v20_visual_audit",
    )
    schema = _planner_response_format(request)["json_schema"]["schema"]
    properties = schema["properties"]["observations"]["properties"]
    assert properties["composition_archetype_seen"]["enum"] == [
        "ASYMMETRIC_SEARCH_FRAME"
    ]
    assert properties["product_zone_seen"]["enum"] == ["RIGHT_OR_CENTER_DOMINANT"]


def test_v20_visual_judgment_fails_closed_on_missing_native_fields() -> None:
    with pytest.raises(AuroraPlanningError, match="decision_summary"):
        AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
            {
                "page_index": 0,
                "image_sha256": "0" * 64,
                "reviewer": {
                    "mode": "AGENT_DIRECT_PIXEL_REVIEW",
                    "pixel_inspection_performed": True,
                },
                "observations": {},
                "checks": [],
            },
            {"page_index": 0},
        )


def test_v20_visual_judgment_binds_runtime_fields_after_semantic_validation() -> None:
    source = SourceImage(
        asset_id="candidate",
        group_key="job_runtime_binding",
        filename="candidate.png",
        content_type="image/png",
        sha256="a" * 64,
        payload=b"candidate-pixels",
        source_role="generated_candidate",
    )
    result = asyncio.run(
        FakeXingheGateway().plan(
            PlannerRequest(
                workspace_id="workspace_one",
                job_id="job_runtime_binding",
                group_key="audit",
                sources=(source,),
                page_count=1,
                language="zh-CN",
                product_name="Aurora v2 audit",
                product_information=(
                    json.dumps(
                        {"required_check_names": list(_VISUAL_CHECKS)},
                        ensure_ascii=False,
                    ),
                ),
                platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key="job_runtime_binding:audit",
                planning_profile="aurora_v20_visual_audit",
            )
        )
    )
    assert isinstance(result.payload, dict)
    sparse_payload = dict(result.payload)
    for field in ("page_index", "image_sha256", "reviewer", "audit_complete"):
        sparse_payload.pop(field, None)

    normalized = AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
        sparse_payload,
        {"page_index": 0},
        runtime_image_sha256=source.sha256,
    )

    assert normalized["page_index"] == 0
    assert normalized["audit_complete"] is True
    assert normalized["decision_summary"]
    assert normalized["observations"]
    assert normalized["checks"]
    assert normalized["all_material_deficiencies"] == []


def test_v20_visual_judgment_normalizes_fractional_scores_and_routes_baseline_to_repair(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "candidate.png"
    source_path.write_bytes(b"candidate-pixels")
    result = asyncio.run(
        FakeXingheGateway().plan(
            PlannerRequest(
                workspace_id="workspace_one",
                job_id="job_fractional_scores",
                group_key="audit",
                sources=(
                    SourceImage(
                        asset_id="candidate",
                        group_key="job_fractional_scores",
                        filename=str(source_path),
                        content_type="image/png",
                        sha256="a" * 64,
                        payload=b"candidate-pixels",
                    ),
                ),
                page_count=1,
                language="zh-CN",
                product_name="Aurora v2 audit",
                product_information=(
                    json.dumps({"required_check_names": list(_VISUAL_CHECKS)}),
                ),
                platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key="job_fractional_scores:audit",
                planning_profile="aurora_v20_visual_audit",
            )
        )
    )
    assert isinstance(result.payload, dict)
    payload = dict(result.payload)
    for check in payload["checks"]:
        check["score"] = 0.98
    payload["observations"].update(
        {
            "type_roles_seen": 3,
            "commercial_closure_visible": False,
        }
    )
    normalized = AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
        payload,
        {"page_index": 0},
    )

    assert normalized["checks"][0]["score"] == 98
    baseline = next(
        item
        for item in normalized["checks"]
        if item["name"] == "baseline_performance_match"
    )
    assert baseline["passed"] is False
    assert baseline["score"] == 69
    assert [
        item["check"] for item in normalized["all_material_deficiencies"]
    ] == ["baseline_performance_match"]


def test_v20_visual_judgment_routes_overlong_search_headline_to_repair() -> None:
    result = asyncio.run(
        FakeXingheGateway().plan(
            PlannerRequest(
                workspace_id="workspace_one",
                job_id="job_overlong_headline",
                group_key="audit",
                sources=(
                    SourceImage(
                        asset_id="candidate",
                        group_key="job_overlong_headline",
                        filename="candidate.png",
                        content_type="image/png",
                        sha256="a" * 64,
                        payload=b"candidate-pixels",
                    ),
                ),
                page_count=1,
                language="zh-CN",
                product_name="Aurora v2 audit",
                product_information=(
                    json.dumps({"required_check_names": list(_VISUAL_CHECKS)}),
                ),
                platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key="job_overlong_headline:audit",
                planning_profile="aurora_v20_visual_audit",
            )
        )
    )
    assert isinstance(result.payload, dict)
    payload = dict(result.payload)
    payload["observations"] = dict(payload["observations"])
    payload["observations"].update(
        {"headline_text": "一二三四五六七八九十 一二三四五六七八九十"}
    )
    generation_request = {
        "page_index": 0,
        "page_contract": {
            "display_copy_contract": {
                "headline_hard_max_chars": 12,
                "headline_max_lines": 2,
            }
        },
    }

    normalized = AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
        payload, generation_request
    )

    for name in ("headline_treatment_match", "thumbnail_success"):
        check = next(item for item in normalized["checks"] if item["name"] == name)
        assert check["passed"] is False
        assert check["score"] == 69


@pytest.mark.parametrize("bad_observations", ["像素观察文本", [], None])
def test_v20_visual_judgment_rejects_non_object_observations(
    bad_observations: object,
) -> None:
    source = SourceImage(
        asset_id="candidate",
        group_key="job_bad_observations",
        filename="candidate.png",
        content_type="image/png",
        sha256="b" * 64,
        payload=b"candidate-pixels",
        source_role="generated_candidate",
    )
    result = asyncio.run(
        FakeXingheGateway().plan(
            PlannerRequest(
                workspace_id="workspace_one",
                job_id="job_bad_observations",
                group_key="audit",
                sources=(source,),
                page_count=1,
                language="zh-CN",
                product_name="Aurora v2 audit",
                product_information=(
                    json.dumps(
                        {"required_check_names": list(_VISUAL_CHECKS)},
                        ensure_ascii=False,
                    ),
                ),
                platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key="job_bad_observations:audit",
                planning_profile="aurora_v20_visual_audit",
            )
        )
    )
    assert isinstance(result.payload, dict)
    payload = dict(result.payload)
    payload["observations"] = bad_observations
    with pytest.raises(AuroraPlanningError, match="observations must be an object"):
        AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
            payload,
            {"page_index": 0},
            runtime_image_sha256=source.sha256,
        )


def test_v20_gateway_contract_is_strict_semantic_json_only() -> None:
    request = PlannerRequest(
        workspace_id="workspace_one",
        job_id="job_contract",
        group_key="audit",
        sources=(),
        page_count=1,
        language="zh-CN",
        product_name="Aurora v2 audit",
        product_information=(),
        platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
        size="1024x1024",
        idempotency_key="job_contract:audit",
        planning_profile="aurora_v20_visual_audit",
    )
    response_format = _planner_response_format(request)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "decision_summary",
        "observations",
        "checks",
        "all_material_deficiencies",
    }
    assert set(
        ("page_index", "image_sha256", "reviewer", "audit_complete")
    ).isdisjoint(schema["properties"])
    assert schema["properties"]["observations"]["type"] == "object"


def test_v20_gateway_schema_rejects_typography_material_policy_mismatch() -> None:
    request = PlannerRequest(
        workspace_id="workspace_one",
        job_id="job_material_policy",
        group_key="audit",
        sources=(
            SourceImage(
                asset_id="candidate",
                group_key="job_material_policy",
                filename="candidate.png",
                content_type="image/png",
                sha256="c" * 64,
                payload=b"candidate-pixels",
                source_role="generated_candidate",
            ),
        ),
        page_count=1,
        language="zh-CN",
        product_name="Aurora v2 audit",
        product_information=(
            json.dumps(
                {"required_check_names": list(_VISUAL_CHECKS)},
                ensure_ascii=False,
            ),
        ),
        platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
        size="1024x1024",
        idempotency_key="job_material_policy:audit",
        planning_profile="aurora_v20_visual_audit",
    )
    schema = _planner_response_format(request)["json_schema"]["schema"]
    variants = schema["properties"]["checks"]["items"]["anyOf"]
    typography = next(
        item
        for item in variants
        if item["properties"]["name"]["enum"] == ["typography_style_match"]
    )
    assert typography["properties"]["material"]["enum"] == [False]


def test_v20_visual_judgment_rejects_non_native_enum() -> None:
    payload = {
        "page_index": 0,
        "image_sha256": "0" * 64,
        "reviewer": {
            "mode": "AGENT_DIRECT_PIXEL_REVIEW",
            "pixel_inspection_performed": True,
        },
        "decision_summary": "The generated page was inspected directly.",
        "observations": {
            "product_prominence": 70,
            "headline_rank": "JOINT_FIRST",
            "headline_text": "核心卖点",
            "headline_line_count": 1,
            "physical_evidence": ["商品结构可见"],
            "evidence_is_primary_visual": True,
            "depth_layers": 2,
            "commercial_closure_visible": True,
            "background_competition": "LOW_TO_MEDIUM",
            "thumbnail_scan_order": ["商品", "标题"],
            "type_roles_seen": 4,
            "unequal_information_modules_seen": 2,
            "visual_tension_seen": "商品与标题形成明确张力",
            "composition_archetype_seen": "独立构图",
            "product_zone_seen": "主体区域",
            "copy_zone_seen": "标题区域",
            "dominant_view_seen": "完整商品",
            "evidence_topology_seen": "结构证据",
            "neighbor_layout_difference": "与相邻页采用不同构图骨架",
            "claim_to_evidence_relation": "商品结构承担主卖点证明",
            "claim_takeaway_without_copy": "去除文字后仍能识别利益方向",
            "source_background_leakage": [],
        },
        "checks": [],
        "all_material_deficiencies": [],
        "audit_complete": True,
    }
    with pytest.raises(AuroraPlanningError, match="background_competition"):
        AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
            payload, {"page_index": 0}
        )


def test_pasted_product_dossier_routes_service_away_from_product_page_leads() -> None:
    raw = _build_raw(
        "job_fact_routing",
        {
            "product_name": "蓝色赛车德训鞋",
            "category": "鞋子",
            "selling_points": [
                "一、基础商品信息",
                "商品全称：蓝色赛车德训鞋",
                "品牌：森果果",
                "发货与物流：免运费；平均16小时发货",
                "客服响应快（平均27秒回复）",
                "售后保障：7天无理由退货",
                "主打透气清爽",
                "主推配色：天蓝色+米白撞色",
                "翻毛皮浅蓝主体搭配米白流线型装饰",
                "日常休闲、通勤、出游、慢跑均可",
                "适配人群：学生党、小个子、宽胖脚/高脚背人群",
                "男女同款情侣板鞋",
            ],
        },
        ["product.jpg"],
    )
    product = raw["product"]
    assert product["verified_selling_points"][:5] == [
        "主打透气清爽",
        "适配人群：学生党、小个子、宽胖脚/高脚背人群",
        "主推配色：天蓝色+米白撞色",
        "翻毛皮浅蓝主体搭配米白流线型装饰",
        "男女同款情侣板鞋",
    ]
    assert "一、基础商品信息" not in product["verified_selling_points"]
    assert product["verified_scenes"] == ["日常休闲、通勤、出游、慢跑均可"]
    assert product["verified_service_or_trust_facts"] == [
        "发货与物流：免运费；平均16小时发货",
        "客服响应快（平均27秒回复）",
        "售后保障：7天无理由退货",
    ]


def test_electronics_dossier_preserves_display_facts_and_scene_source() -> None:
    raw = _build_raw(
        "job_electronics_fact_routing",
        {
            "product_name": "199档冷风手持制冷小风扇",
            "category": "便携式半导体制冷手持风扇",
            "user_provided_display_facts": ["199档", "20m/s", "体感直降16℃"],
            "selling_points": [
                "三、核心产品参数与功能",
                "1. 核心制冷风力",
                "不是单纯吹风，自带半导体制冷片，可冰敷降温",
                "静音不扰人，适合学生课堂、宿舍、图书馆",
                "可给手机辅助散热",
                "超长续航，室内外通用",
            ],
        },
        ["product.jpg"],
    )

    product = raw["product"]
    assert product["user_provided_display_facts"] == [
        "199档",
        "20m/s",
        "体感直降16℃",
    ]
    assert "三、核心产品参数与功能" not in product["verified_selling_points"]
    assert "1. 核心制冷风力" not in product["verified_selling_points"]
    assert product["verified_scenes"] == [
        "静音不扰人，适合学生课堂、宿舍、图书馆",
        "可给手机辅助散热",
    ]
    assert "超长续航，室内外通用" in product["verified_selling_points"]


def test_fake_gateway_returns_an_aurora_identity_contract() -> None:
    source = SourceImage(
        asset_id="asset_one",
        group_key="job_test",
        filename="product.png",
        content_type="image/png",
        sha256="0" * 64,
        payload=b"image-pixels",
    )
    result = asyncio.run(
        FakeXingheGateway().plan(
            PlannerRequest(
                workspace_id="workspace_one",
                job_id="job_test",
                group_key="job_test",
                sources=(source,),
                page_count=6,
                language="zh-CN",
                product_name="轻量随行杯",
                product_information=("杯盖防误开",),
                platform_code="TAOBAO_DOMESTIC_MAIN_IMAGE",
                size="1024x1024",
                idempotency_key="identity-test",
                planning_profile="aurora_identity",
            )
        )
    )
    assert result.error_code is None
    assert result.payload["request_id"] == "job_test"
    assert result.payload["evidence"][0]["evidence_id"] == "asset_one"
    assert result.payload["must_preserve"]


def test_identity_request_id_is_bound_to_the_current_job() -> None:
    source = SourceImage(
        asset_id="asset_one",
        group_key="job_current",
        filename="product.png",
        content_type="image/png",
        sha256="0" * 64,
        payload=b"image-pixels",
    )

    identity = _bind_identity_evidence(
        {
            "request_id": "job_stale_provider_value",
            "product_form": "shoe",
            "identity_summary": "same shoe",
            "evidence": [],
            "must_preserve": ["shape"],
            "must_not_change": ["color"],
        },
        (source,),
        ("product.png",),
    )

    assert identity["request_id"] == "job_current"


def test_visual_audit_supplies_the_complete_harness_judgment_schema() -> None:
    schema = _visual_judgment_schema()

    assert "decision_summary" in schema["required"]
    assert "performance_observation" in schema["required"]
    assert set(schema["properties"]["checks"]["required"])


def test_visual_audit_uses_provider_compatible_json_object_response() -> None:
    request = PlannerRequest(
        workspace_id="workspace_one",
        job_id="job_audit",
        group_key="job_audit:page:0",
        sources=(),
        page_count=1,
        language="zh-CN",
        product_name="当前页卖点",
        product_information=(),
        platform_code="TAOBAO_AURORA_PIXEL_AUDIT",
        size="1024x1024",
        idempotency_key="audit-structure",
        planning_profile="aurora_visual_audit",
    )

    response_format = _planner_response_format(request)

    assert response_format == {"type": "json_object"}


def test_visual_audit_keeps_candidate_and_bounds_identity_images() -> None:
    identity_sources = tuple(
        SourceImage(
            asset_id=f"identity-{index}",
            group_key="job_audit",
            filename=f"identity-{index}.jpg",
            content_type="image/jpeg",
            sha256=str(index) * 64,
            payload=f"identity-{index}".encode(),
        )
        for index in range(6)
    )
    candidate = SourceImage(
        asset_id="candidate-page-0",
        group_key="job_audit",
        filename="candidate.jpg",
        content_type="image/jpeg",
        sha256="f" * 64,
        payload=b"candidate",
        source_role="generated_candidate",
    )

    audit_sources = _bounded_visual_audit_sources(identity_sources, candidate)

    assert [source.asset_id for source in audit_sources] == [
        "identity-0",
        "identity-1",
        "identity-2",
        "candidate-page-0",
    ]
    assert audit_sources[-1].source_role == "generated_candidate"


def test_aurora_normalizes_provider_portrait_without_cropping() -> None:
    source = Image.new("RGB", (1113, 1413), "green")
    source.putpixel((0, 0), (255, 0, 0))
    buffer = BytesIO()
    source.save(buffer, format="JPEG", quality=95)
    payload = buffer.getvalue()
    artifact = ProviderArtifact(
        payload=payload,
        filename="portrait.jpg",
        content_type="image/jpeg",
        width=1113,
        height=1413,
        sha256=hashlib.sha256(payload).hexdigest(),
        provider_width=1113,
        provider_height=1413,
    )

    normalized = _normalize_aurora_square_artifact(artifact)

    assert (normalized.width, normalized.height) == (1024, 1024)
    assert (normalized.provider_width, normalized.provider_height) == (1113, 1413)
    assert normalized.locally_resampled is True
    with Image.open(BytesIO(normalized.payload)) as image:
        assert image.size == (1024, 1024)
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_v20_generation_uses_exact_skill_reference_paths_for_repair(
    tmp_path: Path,
) -> None:
    repair_path = tmp_path / "repair.jpg"
    product_path = tmp_path / "product.jpg"
    repair_path.write_bytes(b"repair-pixels")
    product_path.write_bytes(b"product-pixels")

    sources = _generation_sources(
        {
            "edit_target": {
                "path": str(repair_path),
                "role": "REPAIR_CANVAS_ONLY",
            },
            "reference_allowlist": [
                {"path": str(product_path), "role": "PRODUCT_IDENTITY_AUTHORITY"}
            ],
            "imagegen_reference_paths": [str(repair_path)],
        },
        "job-repair",
    )

    assert [source.filename for source in sources] == [repair_path.name]


def test_visual_audit_adapts_equivalent_direct_pixel_review_fields() -> None:
    judgment = _normalize_visual_judgment_payload(
        {
            "overall_verdict": "PASS",
            "approval_recommendation": "APPROVE",
            "material_failure_count": 0,
            "final_reasoning": (
                "Actual pixels show a complete product, clean isolation, and a "
                "clear approved page duty."
            ),
            "candidate_pixel_summary": {
                "composition": "A complete product pair dominates the square canvas.",
                "background": (
                    "A rebuilt clean studio background surrounds the product."
                ),
            },
            "checks": [
                {
                    "check_name": "product_identity",
                    "status": "PASS",
                    "actual_pixel_evidence": (
                        "Visible shape, color blocks, and components match the "
                        "source product."
                    ),
                }
            ],
            "selling_point_proof_observation": {},
            "all_observed_deficiencies": [],
        }
    )

    assert len(judgment["decision_summary"]) >= 20
    assert judgment["audit_completeness_confirmation"] is True
    assert set(judgment["checks"]) == set(
        _visual_judgment_schema()["properties"]["checks"]["required"]
    )
    assert judgment["checks"]["product_identity_match"]["passed"] is True
    assert judgment["checks"]["approved_plan_fidelity_match"]["passed"] is True
    assert judgment["all_observed_deficiencies"] == []


def test_visual_audit_accepts_tengli_material_pass_field_aliases() -> None:
    judgment = _normalize_visual_judgment_payload(
        {
            "overall_judgment": "PASS",
            "material_fail_count": 0,
            "summary": (
                "The candidate visibly fulfills the page duty with no material "
                "pixel-level deficiency."
            ),
            "checks": [],
            "selling_point_proof_observation": {},
        }
    )

    assert all(check["passed"] for check in judgment["checks"].values())
    assert judgment["selling_point_proof_observation"]["visible_without_copy"] is True
    assert (
        judgment["selling_point_proof_observation"]["evidence_is_primary_visual"]
        is True
    )
    assert judgment["all_observed_deficiencies"] == []


def test_visual_audit_keeps_missing_critical_checks_blocking_on_explicit_fail() -> None:
    judgment = _normalize_visual_judgment_payload(
        {
            "overall_verdict": "FAIL",
            "material_failed_check_count": 1,
            "final_reasoning": "The approved page duty is not visibly established.",
            "checks": [],
            "selling_point_proof_observation": {
                "visible_without_copy": False,
                "evidence_is_primary_visual": False,
            },
        }
    )

    assert judgment["checks"]["approved_plan_fidelity_match"]["passed"] is False
    assert judgment["checks"]["selling_point_visible_without_copy"]["passed"] is False
    assert judgment["all_observed_deficiencies"]


def test_balanced_audit_keeps_unrequested_soft_checks_advisory() -> None:
    judgment = _normalize_visual_judgment_payload(
        {
            "overall_verdict": "FAIL",
            "material_failed_check_count": 1,
            "final_reasoning": "The assigned page role is not visibly established.",
            "checks": [],
            "selling_point_proof_observation": {},
        },
        required_check_names=frozenset({"page_role_expression"}),
    )

    assert judgment["checks"]["page_role_expression"]["passed"] is False
    assert {
        item["check"] for item in judgment["all_observed_deficiencies"]
    } == {"page_role_expression", "selling_point_visible_without_copy"}


def test_balanced_audit_deficiencies_include_forced_core_proof_failure() -> None:
    judgment = _normalize_visual_judgment_payload(
        {
            "overall_verdict": "FAIL",
            "material_failed_check_count": 1,
            "final_reasoning": "The core benefit is not visible without copy.",
            "checks": [
                {
                    "check_name": "product_identity_match",
                    "status": "PASS",
                    "actual_pixel_evidence": (
                        "The product silhouette and components match the reference."
                    ),
                }
            ],
            "selling_point_proof_observation": {
                "visible_without_copy": False,
                "evidence_is_primary_visual": False,
            },
        },
        required_check_names=frozenset(
            {
                "product_identity_match",
                "selling_point_visible_without_copy",
            }
        ),
    )

    deficiency_checks = {
        item["check"] for item in judgment["all_observed_deficiencies"]
    }
    assert "selling_point_visible_without_copy" in deficiency_checks
    assert judgment["checks"]["selling_point_visible_without_copy"]["passed"] is False


def test_repair_audit_focuses_on_failed_checks_and_identity_safety() -> None:
    required = _required_audit_check_names(
        {
            "plan": {
                "pages": [
                    {
                        "repair_instruction": {
                            "failed_check_names": [
                                "page_role_expression",
                                "thumbnail_success",
                            ]
                        }
                    }
                ]
            }
        },
        {
            "page_index": 0,
            "generation_origin": "AUTOMATIC_REPAIR",
        },
    )

    assert "page_role_expression" in required
    assert "product_identity_match" in required
    assert "factual_copy_match" in required
    assert "thumbnail_success" not in required


def test_balanced_gate_does_not_repair_for_low_soft_score_mean() -> None:
    runtime_dir = (
        Path(__file__).resolve().parents[1]
        / "skill_sources"
        / "aurora-domestic-ecommerce-main-image"
        / "scripts"
        / "runtime"
    )
    sys.path.insert(0, str(runtime_dir))
    try:
        visual_gate = importlib.import_module("visual_gate")
    finally:
        sys.path.pop(0)
    blocking = (
        visual_gate.HARD_BLOCKING_CHECKS
        | visual_gate.MATERIAL_BLOCKING_CHECKS
    )
    checks = {
        name: {
            "passed": name in blocking,
            "score": 90 if name in blocking else 10,
            "observed_evidence": "Direct pixels support this audit conclusion.",
        }
        for name in _visual_judgment_schema()["properties"]["checks"]["required"]
    }
    image_path = str(runtime_dir / "candidate.jpg")
    result = visual_gate.evaluate(
        {
            "page_index": 0,
            "image_sha256": "a" * 64,
            "reviewer": {
                "mode": "AGENT_DIRECT_PIXEL_REVIEW",
                "pixel_inspection_performed": True,
                "image_path": image_path,
            },
            "decision_summary": (
                "The product identity, facts, page role and core proof are intact."
            ),
            "performance_observation": {
                "estimated_product_occupancy_percent": 70,
                "headline_visual_rank": "JOINT_FIRST",
                "physical_evidence_seen": ["product", "benefit"],
                "depth_layers_seen": 2,
                "commercial_closure_seen": True,
                "background_competition": "LOW",
                "thumbnail_scan_order": ["product", "benefit"],
            },
            "plan_fidelity_observation": {
                "observed_subject_and_view": "Complete product hero view.",
                "observed_scene_geometry": "Rebuilt commercial studio scene.",
                "observed_composition": "Product-led asymmetric composition.",
                "observed_reading_path": ["product", "benefit"],
                "missing_required_elements": [],
                "unexpected_plan_changes": [],
            },
            "selling_point_proof_observation": {
                "source_text": "core benefit",
                "observability_class": "DIRECTLY_VISIBLE",
                "visible_without_copy": True,
                "pixel_evidence": ["Visible product benefit relation."],
                "text_only_dependency": False,
                "evidence_is_primary_visual": True,
                "estimated_evidence_occupancy_percent": 60,
                "claim_takeaway_without_copy": "The benefit is directly visible.",
                "expression_mode": "DIRECT",
            },
            "source_background_observation": {
                "leaked_source_elements": [],
                "rebuilt_scene_evidence": "A clean rebuilt studio is visible.",
            },
            "checks": checks,
            "all_observed_deficiencies": [],
            "audit_completeness_confirmation": True,
        },
        "a" * 64,
        0,
        image_path,
    )

    assert result["status"] == "PASS"
    assert result["design_score"] < 72
    assert "thumbnail_success" in result["advisory_checks"]


def test_style_calibration_joins_concrete_pixel_evidence() -> None:
    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "review_mode": "DIRECT_PIXEL_VISUAL_COMPARISON",
            "concrete_observed_evidence": [
                "Both accepted pages use the same clean white studio field.",
                "Both pages preserve matching blue product color and crisp finish.",
            ],
            "source_page_indexes": [0, 1],
            "style_tokens": {"density_band": "spacious commercial catalog"},
        }
    )

    assert len(observation["observed_evidence"]) >= 20
    assert "concrete_observed_evidence" not in observation


def test_style_calibration_flattens_spaced_nested_evidence_field() -> None:
    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "style_tokens": {
                "palette_usage_ratio": {
                    "blue": "25-40%",
                    "white": "45-60%",
                }
            },
            "concrete observed_evidence": [
                {
                    "source_page_index": 0,
                    "observations": [
                        "The page uses a cool blue and white commercial system.",
                        "Upper-left light reveals the suede and stitching.",
                    ],
                },
                {
                    "shared_comparison": [
                        "Both pages share restrained retro-sport typography."
                    ]
                },
            ],
        }
    )

    assert "cool blue and white" in observation["observed_evidence"]
    assert "retro-sport typography" in observation["observed_evidence"]
    assert "concrete observed_evidence" not in observation
    assert "palette_usage_ratio" not in observation["style_tokens"]


def test_style_calibration_drops_empty_palette_ratio_override() -> None:
    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "style_tokens": {
                "palette_usage_ratio": {},
                "density_band": "spacious commercial catalog",
            },
        }
    )

    assert observation["style_tokens"] == {
        "density_band": "spacious commercial catalog"
    }


def test_style_calibration_maps_geometry_alias_to_harness_field() -> None:
    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "style_tokens": {
                "information_surface_geometry": "asymmetric evidence-led surfaces",
                "prohibited_styles": ["equal-card wall"],
            },
        }
    )

    assert observation["style_tokens"] == {
        "information_surfaces": "asymmetric evidence-led surfaces"
    }


def test_style_calibration_coalesces_list_string_tokens() -> None:
    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "style_tokens": {
                "information_surfaces": ["完整结构为主", "局部采用辅助面"],
                "density_band": ["MEDIUM", "HIGH"],
            },
        }
    )

    assert observation["style_tokens"] == {
        "information_surfaces": "完整结构为主；局部采用辅助面",
        "density_band": "MEDIUM；HIGH",
    }


@pytest.mark.parametrize(
    "prohibited_styles",
    [["equal-card wall"], "equal-card wall", {"name": "equal-card wall"}],
)
def test_style_calibration_drops_prohibited_styles_for_any_type(
    prohibited_styles: object,
) -> None:
    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "style_tokens": {
                "prohibited_styles": prohibited_styles,
                "density_band": "medium",
            },
        }
    )

    assert observation["style_tokens"] == {"density_band": "medium"}


def test_style_calibration_preserves_native_tokens_and_valid_palette_ratio() -> None:
    tokens = {
        "photography_finish": "crisp studio",
        "light_direction": "upper left",
        "color_temperature": "cool neutral",
        "palette_roles": {"background": "white", "accent": "blue"},
        "palette_usage_ratio": {"background": 60, "product": 30, "accent": 10},
        "typography": {"headline": "bold sans"},
        "graphic_language": "evidence-led lines",
        "information_surfaces": "asymmetric evidence-led surfaces",
        "density_band": "medium",
    }

    observation = _normalize_style_calibration_payload(
        {"agent_pixel_reviewed": True, "style_tokens": tokens}
    )

    assert observation["style_tokens"] == tokens


@pytest.mark.parametrize("field", ["identity", "layout", "copy", "unknown"])
def test_style_calibration_keeps_unknown_fields_fail_closed(field: str) -> None:
    runtime_path = (
        Path(__file__).resolve().parents[1]
        / "skill_sources"
        / "aurora-domestic-ecommerce-main-image-v20"
        / "scripts"
        / "runtime"
    )
    existing_core = sys.modules.pop("core", None)
    existing_audit = sys.modules.pop("audit", None)
    sys.path.insert(0, str(runtime_path))
    try:
        native_validator = importlib.import_module("audit").validate_style_observation
    finally:
        sys.path.remove(str(runtime_path))
        sys.modules.pop("audit", None)
        sys.modules.pop("core", None)
        if existing_audit is not None:
            sys.modules["audit"] = existing_audit
        if existing_core is not None:
            sys.modules["core"] = existing_core

    observation = _normalize_style_calibration_payload(
        {
            "agent_pixel_reviewed": True,
            "source_page_indexes": [0],
            "observed_evidence": "Visible pixel evidence is present.",
            "style_tokens": {"density_band": "medium", field: "must block"},
        }
    )

    with pytest.raises(Exception, match="style tokens contain identity, layout, copy"):
        native_validator(observation)


def test_set_role_audit_adapts_page_evidence_to_harness_distinctions() -> None:
    observation = _normalize_set_role_audit_payload(
        {
            "pages": [
                {
                    "page_index": index,
                    "concrete_pixel_evidence_not_search_cover": [
                        f"Page {index} visibly performs a unique carousel duty."
                    ],
                }
                for index in range(1, 6)
            ],
            "search_listing_cover_assessment": {
                "search_listing_cover_page_index": 0,
                "page_0_alone_is_visibly_search_listing_cover": True,
                "other_search_listing_cover_page_indices": [],
                "pixel_evidence": [
                    "Page zero alone uses the search-cover hero hierarchy."
                ],
            },
        }
    )

    assert [
        item["page_index"] for item in observation["carousel_distinctions"]
    ] == list(range(1, 6))
    assert observation["identified_search_page_index"] == 0
    assert observation["search_role_is_unique"] is True
    assert observation["confusable_page_indexes"] == []


def test_parallel_set_audit_binds_provider_output_to_harness_contract() -> None:
    sources = [SimpleNamespace(sha256=f"hash-{index}") for index in range(6)]
    observation = _normalize_parallel_set_audit_payload(
        {
            "pages": [
                {
                    "page_index": index,
                    "concrete_pixel_evidence_not_search_cover": [
                        f"Page {index} visibly performs a unique carousel duty."
                    ],
                }
                for index in range(1, 6)
            ],
            "search_listing_cover_assessment": {
                "search_listing_cover_page_index": 0,
                "page_0_alone_is_visibly_search_listing_cover": True,
                "other_search_listing_cover_page_indices": [],
                "pixel_evidence": ["Page zero is the unique listing cover."],
            },
        },
        sources,
    )

    assert observation["agent_pixel_reviewed"] is True
    assert observation["reviewed_page_indexes"] == list(range(6))
    assert observation["image_sha256_by_page"] == {
        str(index): f"hash-{index}" for index in range(6)
    }


def test_set_role_audit_adapts_tengli_page_reason_and_cover_pass() -> None:
    observation = _normalize_set_role_audit_payload(
        {
            "carousel_distinctions": [
                {"page_index": index, "why_not_search_cover": ""}
                for index in range(1, 6)
            ],
            "pages": [
                {
                    "page_index": index,
                    "not_search_cover_reason": (
                        f"Page {index} visibly performs a focused carousel duty."
                    ),
                }
                for index in range(1, 6)
            ],
            "search_identification_evidence": "",
            "search_role_is_unique": False,
            "search_listing_cover_assessment": {
                "search_listing_cover_page_index": 0,
                "cover_uniqueness_status": "PASS",
                "page_0_pixel_evidence": [
                    "Page zero alone has a listing-cover commercial hierarchy."
                ],
            },
            "set_level_conclusion": {
                "page_0_is_the_only_visible_search_cover": True,
            },
        }
    )

    assert all(
        item["why_not_search_cover"]
        for item in observation["carousel_distinctions"]
    )
    assert observation["identified_search_page_index"] == 0
    assert observation["search_role_is_unique"] is True
    assert "Page zero alone" in observation["search_identification_evidence"]


def test_set_role_audit_adapts_tengli_explaining_evidence_and_set_verdict() -> None:
    observation = _normalize_set_role_audit_payload(
        {
            "carousel_distinctions": [
                {"page_index": index, "why_not_search_cover": ""}
                for index in range(1, 6)
            ],
            "pages": [
                {
                    "page_index": 0,
                    "concrete_pixel_evidence": [
                        "Page zero visibly combines the product hero and "
                        "listing headline."
                    ],
                },
                *[
                    {
                        "page_index": index,
                        "concrete_pixel_evidence_explaining_not_search_cover": [
                            f"Page {index} visibly performs one focused carousel duty."
                        ],
                    }
                    for index in range(1, 6)
                ],
            ],
            "set_verdict": {
                "page_0_alone_is_visibly_the_search_listing_cover": True,
                "search_listing_cover_page_index": 0,
                "set_roles_are_visibly_distinct": True,
            },
        }
    )

    assert all(
        item["why_not_search_cover"]
        for item in observation["carousel_distinctions"]
    )
    assert observation["identified_search_page_index"] == 0
    assert observation["search_role_is_unique"] is True
    assert "product hero" in observation["search_identification_evidence"]


def test_visual_audit_accepts_material_pass_with_audit_limitations() -> None:
    normalized = _normalize_visual_judgment_payload(
        {
            "overall_status": "PASS_WITH_AUDIT_LIMITATIONS",
            "material_pass": True,
            "decision_summary": "Direct pixels establish the planned page duty.",
        }
    )

    assert normalized["checks"]["page_role_expression"]["passed"] is True
    assert normalized["checks"]["approved_plan_fidelity_match"]["passed"] is True
    assert normalized["all_observed_deficiencies"] == []


def test_visual_audit_normalizes_incomplete_evidence_in_complete_shape() -> None:
    schema = _visual_judgment_schema()
    checks = {
        name: {
            "passed": True,
            "score": 90,
            "observed_evidence": (
                "The reviewer supplied direct pixel evidence."
                if name != "page_role_expression"
                else ""
            ),
        }
        for name in schema["properties"]["checks"]["required"]
    }
    normalized = _normalize_visual_judgment_payload(
        {
            "decision_summary": "Direct pixels establish the planned page duty.",
            "performance_observation": {},
            "plan_fidelity_observation": {},
            "source_background_observation": {},
            "selling_point_proof_observation": {},
            "checks": checks,
            "audit_completeness_confirmation": True,
        }
    )

    assert len(
        normalized["checks"]["page_role_expression"]["observed_evidence"].strip()
    ) >= 12


def test_visual_audit_proof_observation_forces_consistent_material_failures() -> None:
    schema = _visual_judgment_schema()
    checks = {
        name: {
            "passed": True,
            "score": 90,
            "observed_evidence": "The reviewer supplied direct pixel evidence.",
        }
        for name in schema["properties"]["checks"]["required"]
    }
    judgment = {
        "decision_summary": "The page needs a stronger physical proof treatment.",
        "performance_observation": {},
        "plan_fidelity_observation": {},
        "source_background_observation": {},
        "selling_point_proof_observation": {
            "visible_without_copy": False,
            "evidence_is_primary_visual": False,
        },
        "checks": checks,
        "audit_completeness_confirmation": True,
        "all_observed_deficiencies": [],
    }

    normalized = _normalize_visual_judgment_payload(judgment)

    assert normalized["checks"]["selling_point_visual_dominance"]["passed"] is False
    assert normalized["checks"]["approved_plan_fidelity_match"]["passed"] is False


def test_visual_audit_keeps_explicit_overall_pass_failures_advisory() -> None:
    schema = _visual_judgment_schema()
    checks = {
        name: {
            "passed": name != "headline_treatment_match",
            "score": 55 if name == "headline_treatment_match" else 90,
            "observed_evidence": "Direct pixel evidence supports this conclusion.",
        }
        for name in schema["properties"]["checks"]["required"]
    }
    judgment = {
        "overall_verdict": "PASS",
        "material_failure_count": 0,
        "decision_summary": "The page materially satisfies its approved contract.",
        "performance_observation": {},
        "plan_fidelity_observation": {},
        "source_background_observation": {},
        "selling_point_proof_observation": {
            "visible_without_copy": True,
            "evidence_is_primary_visual": True,
        },
        "checks": checks,
        "audit_completeness_confirmation": True,
        "all_observed_deficiencies": [],
    }

    normalized = _normalize_visual_judgment_payload(judgment)

    assert normalized["checks"]["headline_treatment_match"]["passed"] is False
    assert normalized["checks"]["headline_treatment_match"]["score"] == 70
    assert normalized["all_observed_deficiencies"] == []


def test_compile_v013_builds_campaign_style_and_six_execution_contracts(
    tmp_path: Path,
) -> None:
    source_path = Path(__file__).resolve().parents[2] / "public" / "og.png"
    evidence_path = tmp_path / "product.png"
    evidence_path.write_bytes(source_path.read_bytes())
    raw = {
        "request_id": "job_compile",
        "profile01_semantics_read": False,
        "style_preferences": ["冷灰背景", "运动杂志感"],
        "product": {
            "name": "蓝色赛车德训鞋",
            "category": "男士运动鞋",
            "original_references": [str(evidence_path)],
            "verified_selling_points": ["复古赛车外观", "鞋底抓地纹路"],
            "verified_attributes": ["天蓝色与米白撞色"],
            "verified_scenes": ["城市通勤"],
            "verified_offers": [],
            "verified_service_or_trust_facts": ["48小时内发货"],
        },
    }
    identity = {
        "request_id": "job_compile",
        "product_form": "低帮系带运动鞋",
        "identity_summary": "天蓝色与米白撞色的低帮系带鞋，以原始商品图锁定身份。",
        "evidence": [
            {
                "evidence_id": "asset_one",
                "path": str(evidence_path),
                "view_tags": ["商品主视图"],
            }
        ],
        "must_preserve": ["鞋体轮廓", "蓝白配色", "鞋底比例"],
        "must_not_change": ["不得改变配色", "不得增加或删除部件"],
        "previous_generated_images_used": False,
        "profile01_semantics_read": False,
    }
    input_path = tmp_path / "input.json"
    identity_path = tmp_path / "identity.json"
    session_path = tmp_path / "session.json"
    output_path = tmp_path / "compiled.json"
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    identity_path.write_text(json.dumps(identity, ensure_ascii=False), encoding="utf-8")
    runner = AuroraHarnessRunner()
    runner.run("start", tmp_path, {"input": input_path, "session": session_path})
    runner.run(
        "lock-identity",
        tmp_path,
        {"session": session_path, "identity": identity_path},
    )
    result = runner.run(
        "compile-v013",
        tmp_path,
        {"session": session_path, "output": output_path},
    )
    session = json.loads(session_path.read_text(encoding="utf-8"))
    public = _public_plan_payload(raw["product"], identity, session)

    assert result["status"] == "AWAITING_AUTHORIZATION"
    assert session["campaign_constitution"]["background_style_lock"]
    assert len(public["pages"]) == 6
    assert [page["page_role"] for page in public["pages"]] == list(PAGE_ROLES)
    assert all(page["main_selling_point"] for page in public["pages"])
    assert all(page["visual_proof"] for page in public["pages"])
    assert all(page["composition_and_subject"] for page in public["pages"])
    assert all(page["reading_path_and_type_hierarchy"] for page in public["pages"])
    assert all(page["non_repetition_reason"] for page in public["pages"])
    assert all(page["commercial_closure"] for page in public["pages"])
    assert all("发货" not in page["main_selling_point"] for page in public["pages"])
    prompts = [page["v015_execution_prompt"] for page in session["plan"]["pages"]]
    assert all(len(prompt) < 3_500 for prompt in prompts)
    assert "【最高优先级：只执行当前页已批准合同】" in prompts[0]
    assert "【必须逐项可见】" in prompts[0]
    assert "禁止退化成白底商品海报" in prompts[0]


def test_real_runtime_exposes_registered_aurora_provider_when_enabled() -> None:
    runtime = runtime_status_response(
        environment="test",
        authentication_mode="development",
        task_runner="tengli",
        domestic_main_image_provider_enabled=True,
    )
    capability = runtime.capabilities.features["domestic_main_image"]
    assert capability.availability == "available"
    assert capability.execution == "provider"


def test_provider_completes_the_harness_controlled_six_page_loop(
    tmp_path: Path,
) -> None:
    job_id = "job_provider"
    workspace = tmp_path / job_id
    workspace.mkdir()
    evidence_path = workspace / "product.png"
    source_path = Path(__file__).resolve().parents[2] / "public" / "og.png"
    evidence_path.write_bytes(source_path.read_bytes())
    raw = {
        "request_id": job_id,
        "profile01_semantics_read": False,
        "product": {
            "name": "轻量随行杯",
            "category": "保温杯",
            "original_references": [str(evidence_path)],
            "verified_selling_points": ["杯盖防误开", "轻量便携"],
            "verified_attributes": ["可见杯盖结构"],
            "verified_scenes": ["城市通勤"],
            "verified_offers": [],
            "verified_service_or_trust_facts": ["48小时内发货"],
        },
    }
    identity = {
        "request_id": job_id,
        "product_form": "带盖随行杯",
        "identity_summary": "由原始图片锁定同一款带盖随行杯。",
        "evidence": [
            {
                "evidence_id": "asset_one",
                "path": str(evidence_path),
                "view_tags": ["商品主视图"],
            }
        ],
        "must_preserve": ["杯体轮廓", "杯盖结构", "颜色分区"],
        "must_not_change": ["不得增加部件", "不得改变杯体比例"],
        "previous_generated_images_used": False,
        "profile01_semantics_read": False,
    }
    input_path = workspace / "input.json"
    identity_path = workspace / "identity.json"
    session_path = workspace / "session.json"
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    identity_path.write_text(json.dumps(identity, ensure_ascii=False), encoding="utf-8")
    runner = AuroraHarnessRunner()
    runner.run("start", workspace, {"input": input_path, "session": session_path})
    runner.run(
        "lock-identity",
        workspace,
        {"session": session_path, "identity": identity_path},
    )
    runner.run(
        "compile-v013",
        workspace,
        {"session": session_path, "output": workspace / "compiled.json"},
    )
    runner.run(
        "authorize",
        workspace,
        {"session": session_path, "approval-text": "确认六图规划并授权生成"},
    )

    class MemoryProvider(AuroraMainImageProvider):
        state = json.loads(session_path.read_text(encoding="utf-8"))

        def _load_authorized_context(self, request):  # type: ignore[no-untyped-def]
            del request
            return SimpleNamespace(
                ordinal=len(self.state["accepted_page_indexes"])
            ), dict(self.state)

        def _persist_session(self, job_id, harness_session):  # type: ignore[no-untyped-def]
            assert job_id == "job_provider"
            self.state = dict(harness_session)

    provider = MemoryProvider(
        session_factory=None,  # type: ignore[arg-type]
        gateway=FakeXingheGateway(),
        storage=None,  # type: ignore[arg-type]
        work_root=tmp_path,
        # This fixture drives the v0.1.17 six-page Harness contract below;
        # the provider default is the native Aurora v2.0 mode.
        execution_mode="sequential_v013",
    )
    for index in range(6):
        result = asyncio.run(
            provider.execute(
                ProviderRequest(
                    workspace_id="workspace_one",
                    job_id=job_id,
                    job_item_id=f"item_{index}",
                    attempt_id=f"attempt_{index}",
                    operation="domestic_main_image",
                    idempotency_key=f"provider-{index}",
                    configuration={},
                )
            )
        )
        assert result.outcome == ProviderOutcome.SUCCEEDED, result.error_message
        assert result.artifact is not None

    assert provider.state["accepted_page_indexes"] == [0, 1, 2, 3, 4, 5]
    assert provider.state["style_calibrated"] is True
    assert provider.state["search_baseline_calibrated"] is True
    assert provider.state["state"] == "AWAITING_USER_FINAL_REVIEW"

    incomplete_session = dict(provider.state)
    incomplete_session["state"] = "AWAITING_SET_ROLE_AUDIT"
    incomplete_session_path = workspace / "incomplete-set-session.json"
    incomplete_observation_path = workspace / "incomplete-set-observation.json"
    incomplete_session_path.write_text(
        json.dumps(incomplete_session, ensure_ascii=False), encoding="utf-8"
    )
    expected_hashes = {
        str(index): next(
            version["sha256"]
            for version in reversed(incomplete_session["generated_versions"])
            if version["page_index"] == index
            and version["status"] in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}
        )
        for index in range(6)
    }
    incomplete_observation_path.write_text(
        json.dumps(
            {
                "agent_pixel_reviewed": True,
                "review_mode": "BLIND_SIX_PAGE_ROLE_COMPARISON",
                "reviewed_page_indexes": list(range(6)),
                "image_sha256_by_page": expected_hashes,
                "carousel_distinctions": [],
                "search_identification_evidence": "",
                "identified_search_page_index": 0,
                "search_role_is_unique": True,
                "confusable_page_indexes": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runner.run(
        "audit-set-role-distinction",
        workspace,
        {
            "session": incomplete_session_path,
            "observation": incomplete_observation_path,
        },
    )
    recovered = json.loads(incomplete_session_path.read_text(encoding="utf-8"))
    assert recovered["state"] == "AWAITING_USER_FINAL_REVIEW"
    assert recovered["set_role_distinction_passed"] is False
    assert recovered["residual_issues_for_user_final_review"][-1]["errors"] == [
        "SET_ROLE_AUDIT_EVIDENCE_INCOMPLETE"
    ]


def test_harness_runner_rejects_unknown_commands(tmp_path: Path) -> None:
    with pytest.raises(AuroraHarnessError) as captured:
        AuroraHarnessRunner().run("python", tmp_path, {})
    assert captured.value.code == "HARNESS_COMMAND_NOT_ALLOWED"


def test_aurora_v20_source_matches_the_original_zip_manifest() -> None:
    manifest = verify_aurora_v20_source()

    assert manifest["source_version"] == "2.0.0"
    assert manifest["source_file_count"] == 24
    assert manifest["archive_sha256"] == (
        "0c5d3daa3889d0ff5964114996a49c428dce9b200b3591d9b690991170869d3c"
    )
    assert manifest["skill_md_sha256"] == (
        "a6ce9864e5a6522339db5e69cca90242dfb700320633f802f783f951eed9cb6c"
    )


def test_sequential_v20_provider_keeps_native_harness_order(
    tmp_path: Path, client,
) -> None:
    job_id = "job_v20_sequential"
    evidence_path = tmp_path / "product.png"
    evidence_path.write_bytes(
        (Path(__file__).resolve().parents[2] / "public" / "og.png").read_bytes()
    )
    raw = {
        "request_id": job_id,
        "product": {
            "name": "轻量随行杯",
            "category": "保温杯",
            "original_references": [str(evidence_path)],
            "verified_selling_points": ["杯盖防误开", "轻量便携"],
            "verified_attributes": ["可见杯盖结构"],
            "verified_scenes": ["城市通勤"],
            "verified_offers": [],
            "verified_service_or_trust_facts": ["48小时内发货"],
        },
    }
    identity = {
        "request_id": job_id,
        "product_form": "带盖随行杯",
        "identity_summary": "由原始图片锁定同一款带盖随行杯。",
        "evidence": [
            {
                "evidence_id": "asset_one",
                "path": str(evidence_path),
                "view_tags": ["商品主视图"],
            }
        ],
        "must_preserve": ["杯体轮廓", "杯盖结构", "颜色分区"],
        "must_not_change": ["不得增加部件", "不得改变杯体比例"],
        "previous_generated_images_used": False,
    }
    input_path = tmp_path / "input.json"
    identity_path = tmp_path / "identity.json"
    session_path = tmp_path / "session.json"
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    identity_path.write_text(
        json.dumps(identity, ensure_ascii=False), encoding="utf-8"
    )
    runner = AuroraV20HarnessRunner()
    runner.run("start", tmp_path, {"input": input_path, "session": session_path})
    runner.run(
        "lock-identity",
        tmp_path,
        {"session": session_path, "identity": identity_path},
    )
    runner.run("compile", tmp_path, {"session": session_path})
    runner.run(
        "authorize",
        tmp_path,
        {"session": session_path, "approval-text": "确认六图规划并授权生成"},
    )

    class MemoryProvider(AuroraMainImageProvider):
        state = json.loads(session_path.read_text(encoding="utf-8"))

        def _load_authorized_context(self, request):  # type: ignore[no-untyped-def]
            del request
            return SimpleNamespace(ordinal=len(self.state["accepted_pages"])), dict(
                self.state
            )

        def _persist_session(self, job_id, harness_session):  # type: ignore[no-untyped-def]
            assert job_id == "job_v20_sequential"
            self.state = dict(harness_session)

    provider = MemoryProvider(
        session_factory=None,  # type: ignore[arg-type]
        gateway=FakeXingheGateway(),
        storage=None,  # type: ignore[arg-type]
        work_root=tmp_path,
        execution_mode="sequential_v20",
    )
    results = []
    for index in range(6):
        result = asyncio.run(
            provider.execute(
                ProviderRequest(
                    workspace_id="workspace_one",
                    job_id=job_id,
                    job_item_id=f"item_{index}",
                    attempt_id=f"attempt_{index}",
                    operation="domestic_main_image",
                    idempotency_key=f"provider-v20-{index}",
                    configuration={},
                )
            )
        )
        results.append(result)

    assert all(result.outcome == ProviderOutcome.SUCCEEDED for result in results)
    assert provider.state["accepted_pages"] == [0, 1, 2, 3, 4, 5]
    assert provider.state["state"] == "AWAITING_SET_AUDIT"

    # Exercise the production set-audit persistence and final approval API with
    # the actual native Harness and offline gateway, including stale pixels.
    from uuid import uuid4

    from sqlalchemy import select

    from app.domain import JobItemStatus, JobStatus, JobType, PlanValidationStatus
    from app.models import JobItemRecord, JobPlanRecord, JobRecord

    with client.app.state.session_factory() as db:
        db.add(JobRecord(
            id=job_id, workspace_id="ws_test", type=JobType.DOMESTIC_MAIN_IMAGE.value,
            status=JobStatus.PROCESSING.value, stage="generating", progress=90,
            configuration={}, expected_item_count=6, currency="USD",
        ))
        db.flush()
        for index in range(6):
            db.add(JobItemRecord(
                id=f"item_{uuid4().hex}", workspace_id="ws_test", job_id=job_id,
                item_key=f"PAGE-{index}", ordinal=index,
                status=JobItemStatus.SUCCEEDED.value, configuration={},
            ))
        payload = {
            "execution_policy": {"execution_mode": "sequential_v20"},
            "_harness_session": provider.state,
        }
        db.add(JobPlanRecord(
            id=f"plan_{uuid4().hex}", workspace_id="ws_test", job_id=job_id,
            revision=1, schema_version="aurora-main-image-plan-v2", payload=payload,
            payload_hash=canonical_hash(payload),
            validation_status=PlanValidationStatus.VALID.value,
        ))
        db.commit()
    provider.session_factory = client.app.state.session_factory
    assert asyncio.run(provider.audit_sequential_v20_set(job_id)) is True
    coordinator = client.app.state.task_runner.coordinator
    assert (
        coordinator._mark_aurora_final_review_pending(job_id)
        == JobStatus.AWAITING_FINAL_REVIEW
    )
    # Redelivery must not re-audit or complete a job awaiting human approval.
    assert asyncio.run(coordinator.run(job_id)) == JobStatus.AWAITING_FINAL_REVIEW
    client.app.state.settings.aurora_work_root = tmp_path
    endpoint = f"/api/v1/workspaces/ws_test/jobs/{job_id}/finalize"
    approval = {"approval_text": "我确认六张图片并批准交付"}
    headers = {"Idempotency-Key": str(uuid4())}
    candidate_path = Path(provider.state["generated_versions"][0]["file"])
    original = candidate_path.read_bytes()
    candidate_path.write_bytes(b"changed after audit")
    rejected = client.post(endpoint, json=approval, headers=headers)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "AURORA_FINALIZE_FAILED"
    candidate_path.write_bytes(original)
    approved = client.post(endpoint, json=approval, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert approved.json()["plan"]["payload"]["harness_state"] == "FINALIZED"
    replay = client.post(endpoint, json=approval, headers=headers)
    assert replay.status_code == 200, replay.text
    changed = client.post(
        endpoint, json={"approval_text": "不同批准内容"}, headers=headers
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    with client.app.state.session_factory() as db:
        stored = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job_id))
        manifest = json.loads(
            Path(stored.payload["final_manifest"]).read_text(encoding="utf-8")
        )
    assert manifest["final_user_approval"]["text"] == approval["approval_text"]
    assert len(manifest["pages"]) == 6


def test_parallel_v20_provider_uses_native_snapshot_and_page_local_audit(
    tmp_path: Path,
) -> None:
    class SparseNativeGateway(FakeXingheGateway):
        async def plan(self, request: PlannerRequest):
            result = await super().plan(request)
            if (
                request.planning_profile == "aurora_v20_visual_audit"
                and isinstance(result.payload, dict)
            ):
                payload = dict(result.payload)
                for field in (
                    "page_index",
                    "image_sha256",
                    "reviewer",
                    "audit_complete",
                ):
                    payload.pop(field, None)
                return replace(result, payload=payload)
            return result

    job_id = "job_v20_snapshot"
    evidence_path = tmp_path / "product.png"
    evidence_path.write_bytes(
        (Path(__file__).resolve().parents[2] / "public" / "og.png").read_bytes()
    )
    raw = {
        "request_id": job_id,
        "product": {
            "name": "轻量随行杯",
            "category": "保温杯",
            "original_references": [str(evidence_path)],
            "verified_selling_points": ["杯盖防误开", "轻量便携"],
            "verified_attributes": ["可见杯盖结构"],
            "verified_scenes": ["城市通勤"],
            "verified_offers": [],
            "verified_service_or_trust_facts": ["48小时内发货"],
        },
    }
    identity = {
        "request_id": job_id,
        "product_form": "带盖随行杯",
        "identity_summary": "由原始图片锁定同一款带盖随行杯。",
        "evidence": [
            {
                "evidence_id": "asset_one",
                "path": str(evidence_path),
                "view_tags": ["商品主视图"],
            }
        ],
        "must_preserve": ["杯体轮廓", "杯盖结构", "颜色分区"],
        "must_not_change": ["不得增加部件", "不得改变杯体比例"],
        "previous_generated_images_used": False,
    }
    input_path = tmp_path / "input.json"
    identity_path = tmp_path / "identity.json"
    session_path = tmp_path / "session.json"
    request_path = tmp_path / "request.json"
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    identity_path.write_text(
        json.dumps(identity, ensure_ascii=False), encoding="utf-8"
    )
    runner = AuroraV20HarnessRunner()
    runner.run("start", tmp_path, {"input": input_path, "session": session_path})
    runner.run(
        "lock-identity",
        tmp_path,
        {"session": session_path, "identity": identity_path},
    )
    runner.run("compile", tmp_path, {"session": session_path})
    runner.run(
        "authorize",
        tmp_path,
        {"session": session_path, "approval-text": "确认六图规划并授权生成"},
    )
    result = runner.run(
        "generate-next",
        tmp_path,
        {"session": session_path, "out": request_path},
    )
    generation_request = dict(result["generation_request"])
    snapshot = {
        "schema_version": "aurora-parallel-page-snapshot-v1",
        "execution_mode": "parallel_v20",
        "page_index": 0,
        "group_key": job_id,
        "prompt": generation_request["prompt"],
        "imagegen_reference_paths": generation_request[
            "imagegen_reference_paths"
        ],
        "edit_target": generation_request.get("edit_target"),
        "generation_token": generation_request["generation_token"],
        "page_revision": 0,
        "plan_revision": 1,
        "plan_hash": generation_request["plan_digest"],
        "identity_hash": "identity",
        "style_lock": generation_request["campaign_constitution"][
            "campaign_style"
        ],
        "source_asset_hashes": {
            "asset_one": hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        },
        "logical_idempotency_key": f"{job_id}:item_0:plan-1:page-0",
        "request": generation_request,
        "session": json.loads(session_path.read_text(encoding="utf-8")),
    }
    snapshot["snapshot_hash"] = canonical_hash(snapshot)
    provider = AuroraMainImageProvider(
        session_factory=None,  # type: ignore[arg-type]
        gateway=SparseNativeGateway(),
        storage=None,  # type: ignore[arg-type]
        work_root=tmp_path / "work",
        execution_mode="parallel_v20",
    )

    provider_result = asyncio.run(
        provider.execute(
            ProviderRequest(
                workspace_id="workspace_one",
                job_id=job_id,
                job_item_id="item_0",
                attempt_id="attempt_0",
                operation="domestic_main_image",
                idempotency_key=snapshot["logical_idempotency_key"],
                configuration={
                    "item": {
                        "aurora_execution_mode": "parallel_v20",
                        "aurora_snapshot": snapshot,
                    }
                },
            )
        )
    )

    assert provider_result.outcome == ProviderOutcome.SUCCEEDED
    metrics = provider_result.raw_usage["metrics"]
    assert metrics["aurora_execution_mode"] == "parallel_v20"
    assert metrics["page_index"] == 0
    assert metrics["harness_state"] == "AUTHORIZED"
    assert (
        tmp_path
        / "work"
        / job_id
        / "parallel-v20"
        / "page-01"
        / "attempt-attempt_0"
        / "search-baseline-observation.json"
    ).is_file()
    attempt_workspace = (
        tmp_path
        / "work"
        / job_id
        / "parallel-v20"
        / "page-01"
        / "attempt-attempt_0"
    )
    judgment = json.loads(
        (attempt_workspace / "judgment-1.json").read_text(encoding="utf-8")
    )
    generated_path = (
        attempt_workspace / "generated" / "page-01-attempt-1.jpg"
    )
    assert judgment["page_index"] == 0
    assert judgment["image_sha256"] == hashlib.sha256(
        generated_path.read_bytes()
    ).hexdigest()
    assert judgment["reviewer"] == {
        "mode": "AGENT_DIRECT_PIXEL_REVIEW",
        "pixel_inspection_performed": True,
        "image_path": str(generated_path),
    }
    assert judgment["audit_complete"] is True


def _fake_v20_judgment_payload(job_id: str) -> dict[str, Any]:
    """Produce a valid native v2.0 audit payload via the deterministic gateway."""

    source = SourceImage(
        asset_id="candidate",
        group_key=job_id,
        filename="candidate.png",
        content_type="image/png",
        sha256="a" * 64,
        payload=b"candidate-pixels",
        source_role="generated_candidate",
    )
    result = asyncio.run(
        FakeXingheGateway().plan(
            PlannerRequest(
                workspace_id="workspace_one",
                job_id=job_id,
                group_key="audit",
                sources=(source,),
                page_count=1,
                language="zh-CN",
                product_name="Aurora v2 audit",
                product_information=(
                    json.dumps(
                        {"required_check_names": list(_VISUAL_CHECKS)},
                        ensure_ascii=False,
                    ),
                ),
                platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key=f"{job_id}:audit",
                planning_profile="aurora_v20_visual_audit",
            )
        )
    )
    assert isinstance(result.payload, dict)
    payload = dict(result.payload)
    for field in ("page_index", "image_sha256", "reviewer", "audit_complete"):
        payload.pop(field, None)
    return payload


def test_v20_audit_schema_pins_the_complete_check_cardinality() -> None:
    """RC-3: the schema must not accept a response the validator rejects.

    The old ``minItems: 1`` plus ``anyOf`` only constrained each element, so a
    fully schema-valid response could carry a single check while the validator
    and the immutable Harness runtime both demanded all 29.
    """

    request = PlannerRequest(
        workspace_id="workspace_one",
        job_id="job_audit_schema",
        group_key="audit",
        sources=(),
        page_count=1,
        language="zh-CN",
        product_name="Aurora v2 audit",
        product_information=(),
        platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
        size="1024x1024",
        idempotency_key="audit-schema",
        planning_profile="aurora_v20_visual_audit",
    )

    schema = _planner_response_format(request)["json_schema"]["schema"]
    checks = schema["properties"]["checks"]

    assert checks["minItems"] == len(_VISUAL_CHECKS)
    assert checks["maxItems"] == len(_VISUAL_CHECKS)


def test_v20_visual_judgment_accepts_checks_in_any_order() -> None:
    """A complete check set must not fail the page for arriving out of order."""

    payload = _fake_v20_judgment_payload("job_check_order")
    payload["checks"] = list(reversed(payload["checks"]))

    normalized = AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
        payload,
        {"page_index": 0},
        runtime_image_sha256="a" * 64,
    )

    assert [item["name"] for item in normalized["checks"]] == list(_VISUAL_CHECKS)


def test_v20_visual_judgment_names_the_missing_checks() -> None:
    payload = _fake_v20_judgment_payload("job_check_missing")
    dropped = payload["checks"][0]["name"]
    payload["checks"] = payload["checks"][1:]

    with pytest.raises(AuroraPlanningError) as captured:
        AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
            payload,
            {"page_index": 0},
            runtime_image_sha256="a" * 64,
        )

    assert "complete native check set" in str(captured.value)
    assert dropped in str(captured.value)


def test_v20_visual_judgment_coalesces_duplicate_checks_conservatively() -> None:
    payload = _fake_v20_judgment_payload("job_check_duplicate")
    duplicate = dict(payload["checks"][0])
    duplicate["passed"] = False
    duplicate["score"] = 12
    duplicate["observed_evidence"] = "重复项明确显示该层存在可见材料性问题"
    payload["checks"] = [*payload["checks"], duplicate]

    normalized = AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
        payload,
        {"page_index": 0},
        runtime_image_sha256="a" * 64,
    )

    product_identity = next(
        item
        for item in normalized["checks"]
        if item["name"] == "product_identity_match"
    )
    assert product_identity["passed"] is False
    assert product_identity["score"] == 12


def test_v20_visual_judgment_still_rejects_duplicate_that_hides_missing_check() -> None:
    payload = _fake_v20_judgment_payload("job_check_duplicate_missing")
    dropped = payload["checks"].pop()
    payload["checks"].append(dict(payload["checks"][0]))

    with pytest.raises(AuroraPlanningError) as captured:
        AuroraMainImageProvider._normalize_v20_visual_judgment_payload(
            payload,
            {"page_index": 0},
            runtime_image_sha256="a" * 64,
        )

    assert "complete native check set" in str(captured.value)
    assert dropped["name"] in str(captured.value)


def test_harness_runner_rejects_paths_outside_job_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    with pytest.raises(AuroraHarnessError) as captured:
        AuroraHarnessRunner().run(
            "start",
            tmp_path,
            {"input": outside, "session": tmp_path / "session.json"},
        )
    assert captured.value.code == "HARNESS_PATH_OUTSIDE_WORKSPACE"


def _planner_result(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "http_status": None,
        "provider_request_id": None,
        "parse_trace": (),
        "structure_fingerprint": None,
        "structure_profile": None,
        "provider_envelope": None,
        "content_utf8_bytes": None,
        "content_sha256": None,
        "error_code": None,
        "error_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_safe_planner_diagnostics_bounds_an_opted_in_content_sample() -> None:
    """RC-4: the excerpt is bounded, and absent unless the caller opted in."""

    quiet = _safe_planner_diagnostics(_planner_result())

    assert "content_sample_head" not in quiet
    assert "content_sample_tail" not in quiet

    sampled = _safe_planner_diagnostics(_planner_result(content_sample="x" * 2000))

    assert sampled["content_sample_head"] == "x" * 512
    assert sampled["content_sample_tail"] == "x" * 256


def test_v20_audit_failure_message_names_the_real_cause() -> None:
    """RC-5: a decode failure must not read as an image-quality verdict."""

    decode_failure = _planner_result(
        http_status=200,
        parse_trace=("whole_json:decode_failed", "fence:not_matched"),
    )

    message = _v20_audit_failure_message(decode_failure)

    assert "no JSON object" in message
    assert "whole_json:decode_failed" in message

    assert _v20_audit_failure_message(_planner_result(http_status=502)) == (
        "Aurora v2.0 pixel audit failed with HTTP 502."
    )
    assert (
        _v20_audit_failure_message(
            _planner_result(
                http_status=502,
                error_message="Planner request failed with HTTP 502.",
            )
        )
        == "Planner request failed with HTTP 502."
    )


def test_gateway_error_carries_upstream_status_for_retry_classification() -> None:
    """RC-6: a 502 must reach the transient classifier as retryable.

    The classifier reads ``http_status`` off the exception.  Previously the
    status lived only inside diagnostics, so every upstream 429/502/503 was
    classified as permanent and the configured retry policy never engaged.
    """

    error = _v20_gateway_error(
        _planner_result(http_status=502),
        "Planner request failed with HTTP 502.",
    )

    assert isinstance(error, AuroraPlanningError)
    assert error.http_status == 502
    assert _classify_transient_failure(error) == (True, 502)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_classifier_recovers_status_from_the_message(status: int) -> None:
    """A lost attribute must not silently disable retries again."""

    error = AuroraPlanningError(f"Planner request failed with HTTP {status}.")

    retryable, recovered = _classify_transient_failure(error)

    assert retryable is True
    assert recovered == status


def test_transient_classifier_keeps_contract_failures_permanent() -> None:
    error = AuroraPlanningError("Aurora v2 observations must be an object")

    assert _classify_transient_failure(error) == (False, None)


def test_audit_fact_pool_is_lossless_and_does_not_use_page_prose() -> None:
    pool = _authorized_fact_pool_for_audit(
        {
            "authorized_facts": {
                "selling_points": ["12V 电瓶"],
                "attributes": ["双座"],
                "scenes": ["户外亲子出行"],
            },
            "page_contract": {"primary_claim": "模型生成的页面主卖点"},
        }
    )

    assert pool["selling_points"] == ["12V 电瓶"]
    assert pool["attributes"] == ["双座"]
    assert pool["scenes"] == ["户外亲子出行"]
    assert pool["all_exact_fact_seeds"] == ["12V 电瓶", "双座", "户外亲子出行"]
    assert "模型生成的页面主卖点" not in pool["all_exact_fact_seeds"]


def test_transient_classifier_ignores_a_non_transient_status() -> None:
    error = AuroraPlanningError("Planner request failed with HTTP 400.")
    error.http_status = 400

    assert _classify_transient_failure(error) == (False, 400)


@pytest.mark.parametrize(
    "message, expected",
    [
        (
            "selling-point dominance cannot pass when evidence is not a primary visual",
            True,
        ),
        ("layout-not-cloned needs a concrete visible difference", True),
        ("product identity mismatch requires rebuilding from product evidence", True),
        ("generated image is missing", False),
    ],
)
def test_shadow_visual_gate_is_the_only_harness_quality_retry(
    message: str, expected: bool
) -> None:
    assert _is_shadow_composition_gate_failure(message) is expected
