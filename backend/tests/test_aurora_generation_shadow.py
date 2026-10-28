from __future__ import annotations

from app.aurora_generation_shadow import (
    CONTRACT_FIRST_REBUILD_V1,
    EXECUTION_SEMANTICS_V1,
    build_contract_first_prompt,
    build_execution_semantics_prompt,
    prepare_shadow_generation_request,
)


def _request(*, origin: str = "INITIAL", scope: str = "") -> dict[str, object]:
    return {
        "page_index": 3,
        "page_role": "SCENE_OR_OPERATION",
        "generation_origin": origin,
        "prompt": "the production prompt",
        "imagegen_reference_paths": ["/failed.png", "/product.jpg"],
        "reference_allowlist": [
            {"path": "/product.jpg", "role": "PRODUCT_IDENTITY_AUTHORITY"}
        ],
        "campaign_constitution": {
            "product_identity": {
                "identity_summary": "黑色双座儿童电动车",
                "must_preserve": ["车身比例", "四个车轮"],
                "must_not_change": ["不得新增结构"],
            },
            "campaign_style": {"photography_finish": "高锐度商业广告质感"},
        },
        "authorized_facts": {
            "selling_points": ["12V 电瓶"],
            "attributes": ["双座"],
            "scenes": ["户外亲子出行"],
            "offers": ["赠送遥控器"],
            "service_or_trust": ["支持七天无理由退货"],
        },
        "page_contract": {
            "index": 3,
            "page_role": "SCENE_OR_OPERATION",
            "buyer_question": "孩子如何真实使用",
            "primary_claim": "亲子同乘与遥控互动",
            "visual_proof": "儿童驾驶，家长手持遥控器",
            "composition": "动作斜线和真实接触关系",
            "layout_contract": {
                "composition_archetype": "ACTION_DIAGONAL_SCENE",
                "product_zone": "CENTER_IN_USE_RELATION",
                "copy_zone": "CORNER_OR_ACTION_BOUND",
                "dominant_view": "HUMAN_OR_OBJECT_ACTION",
                "evidence_topology": "CONTACT_ACTION_PATH",
            },
            "reading_path": ["人物关系", "商品", "控制证据"],
            "visual_performance_contract": {"product_dominance": "center in use"},
            "selling_point_visual_proof_contract": {
                "primary_physical_evidence": "真实驾驶"
            },
            "commercial_closure": "安全与陪伴",
        },
    } | (
        {"repair": {"scope": scope}}
        if scope
        else {}
    )


def test_contract_first_prompt_puts_page_duty_before_shared_style() -> None:
    prompt = build_contract_first_prompt(_request())

    assert prompt.startswith("【AURORA SHADOW contract-first rebuild v1】")
    assert prompt.index("【主构图职责】") < prompt.index("【共享视觉 DNA")
    assert "ACTION_DIAGONAL_SCENE" in prompt
    assert "HUMAN_OR_OBJECT_ACTION" in prompt
    assert "必须出现儿童驾驶或家长与遥控器的真实接触关系" in prompt
    assert "真实证据优先" in prompt
    assert "动作证据必须大而清楚" in prompt
    assert "the production prompt" not in prompt


def test_shadow_request_rebuilds_composition_from_identity_references() -> None:
    original = _request(origin="AUTOMATIC_REPAIR", scope="LAYOUT_OR_COMPOSITION")
    prepared = prepare_shadow_generation_request(
        original,
        mode=CONTRACT_FIRST_REBUILD_V1,
    )

    assert original["prompt"] == "the production prompt"
    assert original["imagegen_reference_paths"] == ["/failed.png", "/product.jpg"]
    assert prepared["aurora_shadow_mode"] == CONTRACT_FIRST_REBUILD_V1
    assert prepared["aurora_shadow_repair_scope"] == "REBUILD_FROM_PRODUCT_EVIDENCE"
    assert prepared["edit_target"] is None
    assert prepared["repair_canvas"] is None
    assert prepared["imagegen_reference_paths"] == ["/product.jpg"]


def test_shadow_request_preserves_non_composition_repairs() -> None:
    original = _request(origin="AUTOMATIC_REPAIR", scope="COPY")
    prepared = prepare_shadow_generation_request(
        original,
        mode=CONTRACT_FIRST_REBUILD_V1,
    )

    assert "aurora_shadow_repair_scope" not in prepared
    assert prepared["imagegen_reference_paths"] == ["/failed.png", "/product.jpg"]


def test_shadow_request_reads_harness_repair_scope_from_page_contract() -> None:
    original = _request(origin="AUTOMATIC_REPAIR")
    page_contract = dict(original["page_contract"])
    page_contract["repair"] = {"scope": "LAYOUT_OR_COMPOSITION"}
    original["page_contract"] = page_contract

    prepared = prepare_shadow_generation_request(
        original,
        mode=CONTRACT_FIRST_REBUILD_V1,
    )

    assert prepared["aurora_shadow_repair_scope"] == "REBUILD_FROM_PRODUCT_EVIDENCE"
    assert prepared["imagegen_reference_paths"] == ["/product.jpg"]


def test_identity_failure_rebuilds_from_original_product_references() -> None:
    original = _request(origin="AUTOMATIC_REPAIR", scope="PRODUCT_IDENTITY_MISMATCH")

    prepared = prepare_shadow_generation_request(
        original,
        mode=EXECUTION_SEMANTICS_V1,
    )

    assert prepared["aurora_shadow_repair_scope"] == "REBUILD_FROM_PRODUCT_EVIDENCE"
    assert prepared["edit_target"] is None
    assert prepared["repair_canvas"] is None
    assert prepared["imagegen_reference_paths"] == ["/product.jpg"]



def test_execution_semantics_prompt_makes_geometry_and_repetition_observable() -> None:
    prompt = build_execution_semantics_prompt(_request())

    assert prompt.startswith("【AURORA SHADOW execution semantics v1】")
    assert "【空间构图硬契约】" in prompt
    assert "手、遥控器、儿童姿态和车辆之间的真实接触关系必须足够大而清楚" in prompt
    assert "禁止静态孤立英雄车" in prompt
    assert "不得继承上一页的镜头" in prompt
    assert "PRODUCT CONSISTENCY GATE" in prompt
    assert "variant fact may remain copy-only" in prompt
    assert "unequal verified modules" in prompt
    assert "Equal-weight catalog grids" in prompt
    assert "12V 电瓶" in prompt
    assert "赠送遥控器" in prompt
    assert "支持七天无理由退货" in prompt
    assert "the production prompt" not in prompt


def test_execution_semantics_preserves_search_cover_unequal_module_contract() -> None:
    request = _request()
    page_contract = dict(request["page_contract"])
    layout_contract = dict(page_contract["layout_contract"])
    layout_contract["composition_archetype"] = "ASYMMETRIC_SEARCH_FRAME"
    page_contract["layout_contract"] = layout_contract
    request["page_contract"] = page_contract

    prompt = build_execution_semantics_prompt(request)

    assert "allow 0-5 unequal verified modules" in prompt
    assert "Reject only a catalog-like equal-weight card wall" in prompt
    assert "不是所有信息模块" not in prompt


def test_execution_semantics_shadow_uses_same_repair_rebuild_boundary() -> None:
    original = _request(origin="AUTOMATIC_REPAIR", scope="LAYOUT_OR_COMPOSITION")
    prepared = prepare_shadow_generation_request(
        original,
        mode=EXECUTION_SEMANTICS_V1,
    )

    assert prepared["aurora_shadow_mode"] == EXECUTION_SEMANTICS_V1
    assert prepared["aurora_shadow_repair_scope"] == "REBUILD_FROM_PRODUCT_EVIDENCE"
    assert prepared["edit_target"] is None
    assert prepared["repair_canvas"] is None
    assert prepared["imagegen_reference_paths"] == ["/product.jpg"]
