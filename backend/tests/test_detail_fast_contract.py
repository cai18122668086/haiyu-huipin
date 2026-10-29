from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app import detail_fast, refined_detail
from app.skill_runtime import SkillManifestRegistry
from app.xinghe_agent import PlannerRequest, XingheAgentError, _fake_plan


def request(count=8):
    return PlannerRequest(
        workspace_id="ws_synthetic",
        job_id="job_synthetic",
        group_key="primary",
        sources=(),
        page_count=count,
        language="zh-CN",
        product_name="合成商品",
        product_information=("合成测试素材",),
        platform_code="tmall",
        size="1440x1920",
        idempotency_key="synthetic",
        planning_profile="detail_pack",
    )


def raw(count=8):
    req = request(count)
    return detail_fast.fake_strategy(_fake_plan(req), req)


def payload(strategy):
    plan = strategy["plan"]
    return {
        "groups": [
            {
                "analysis": strategy["analysis"],
                "plan_protocol_fields": {
                    key: value
                    for key, value in plan.items()
                    if key
                    not in {"screens", "campaign_style_lock", "design_strength_lock"}
                },
                "campaign_style_lock": plan["campaign_style_lock"],
                "design_strength_lock": plan["design_strength_lock"],
            }
        ],
        "pages": [
            page
            | {
                "page": index,
                "item_key": str(index),
                "group_key": "primary",
                "quality": "high",
            }
            for index, page in enumerate(plan["screens"], 1)
        ],
    }


@pytest.mark.parametrize("count", [1, 7, 8, 10, 12])
def test_complete_compact_strategy_preserves_all_pages_and_copy(count):
    value = raw(count)
    original = deepcopy(value)
    assert all("image_prompt" not in screen for screen in value["plan"]["screens"])
    normalized = detail_fast.normalize_strategy(value)
    detail_fast.validate_strategy(normalized, count)
    assert value == original
    for authored, compiled in zip(
        value["plan"]["screens"], normalized["plan"]["screens"], strict=True
    ):
        assert all(compiled[key] == content for key, content in authored.items())
        assert compiled["image_prompt"].startswith("vertical 3:4")
        assert compiled["image_prompt"].count("SHARED AUTHORED LOCKS:") == 1
        assert compiled["title"] in compiled["image_prompt"]
        serialized_page = compiled["image_prompt"].split("not text to print): ", 1)[1]
        assert json.loads(serialized_page)["page_prompt"] == compiled["page_prompt"]
    detail_fast.validate_payload(payload(normalized), count)


def test_truncated_page_count_and_incomplete_shared_locks_fail_closed():
    value = detail_fast.normalize_strategy(raw())
    value["plan"]["screens"].pop()
    with pytest.raises(XingheAgentError):
        detail_fast.validate_strategy(value, 8)
    value = raw()
    del value["plan"]["product_identity_physics_lock"]["primary_product_source"]
    with pytest.raises(XingheAgentError, match="required"):
        detail_fast.normalize_strategy(value)


@pytest.mark.parametrize("wrapper_type", ["object", "json_object"])
def test_exact_instance_envelope_preserves_every_authored_value_without_repair(
    wrapper_type,
):
    authored = raw(6)
    authored["plan"]["screens"][0]["page_prompt"] += '\n精确保留空白、引号"和数值0.75。'
    wrapped = {"type": wrapper_type, "properties": deepcopy(authored)}
    original = deepcopy(wrapped)
    normalized = detail_fast.normalize_strategy(wrapped)
    detail_fast.validate_strategy(normalized, 6)
    assert wrapped == original
    uncompiled = deepcopy(normalized)
    for screen in uncompiled["plan"]["screens"]:
        del screen["image_prompt"]
    assert uncompiled == authored
    assert normalized == detail_fast.normalize_strategy(authored)


@pytest.mark.parametrize(
    "change",
    [
        "root_analysis",
        "extra_outer",
        "extra_inner",
        "wrong_type",
        "missing_type",
        "missing_analysis",
        "real_schema",
        "minimal_real_schema",
        "missing_page",
    ],
)
def test_instance_envelope_cannot_hide_schema_ambiguity_or_incomplete_pages(change):
    wrapped = {"type": "object", "properties": raw(6)}
    if change == "root_analysis":
        wrapped["analysis"] = deepcopy(wrapped["properties"]["analysis"])
    elif change == "extra_outer":
        wrapped["metadata"] = {"ok": True}
    elif change == "extra_inner":
        wrapped["properties"]["extra"] = "unexpected"
    elif change == "wrong_type":
        wrapped["type"] = "array"
    elif change == "missing_type":
        del wrapped["type"]
    elif change == "missing_analysis":
        del wrapped["properties"]["analysis"]
    elif change == "real_schema":
        wrapped = detail_fast.strategy_schema(6)
    elif change == "minimal_real_schema":
        wrapped["properties"] = detail_fast.strategy_schema(6)["properties"]
    else:
        wrapped["properties"]["plan"]["screens"].pop()
    original = deepcopy(wrapped)
    with pytest.raises(XingheAgentError):
        detail_fast.validate_strategy(detail_fast.normalize_strategy(wrapped), 6)
    assert wrapped == original


def test_self_consistent_partial_instance_still_fails_requested_page_count():
    wrapped = {"type": "object", "properties": raw(5)}
    normalized = detail_fast.normalize_strategy(wrapped)
    with pytest.raises(XingheAgentError):
        detail_fast.validate_strategy(normalized, 6)


@pytest.mark.parametrize(
    "wrapper_type", ["array", "json_schema", "OBJECT", "", None, 0, False]
)
def test_other_instance_envelope_types_are_rejected(wrapper_type):
    wrapped = {"type": wrapper_type, "properties": raw(6)}
    with pytest.raises(XingheAgentError, match="ambiguous"):
        detail_fast.normalize_strategy(wrapped)


@pytest.mark.parametrize("tamper", ["copy", "lock", "prompt", "binding"])
def test_frozen_strategy_rejects_prompt_lock_copy_and_binding_tampering(tamper):
    value = detail_fast.normalize_strategy(raw())
    if tamper == "copy":
        value["plan"]["screens"][0]["title"] += " changed"
    elif tamper == "lock":
        value["plan"]["campaign_style_lock"]["palette"] += " changed"
    elif tamper == "prompt":
        value["plan"]["screens"][0]["image_prompt"] += " changed"
    else:
        value["plan"]["module_plan"][0]["buyer_demand_id"] = "missing"
    with pytest.raises(XingheAgentError):
        detail_fast.validate_strategy(value, 8)


def test_primary_identity_cannot_come_from_style_reference():
    value = detail_fast.normalize_strategy(raw())
    kwargs = dict(
        structured_plan=value,
        screen=value["plan"]["screens"][0],
        page_number=1,
        page_count=8,
        language="zh-CN",
        product_name="合成商品",
        product_information=(),
        source_roles=("style_reference",),
        size="1440x1920",
    )
    with pytest.raises(XingheAgentError, match="product-reference"):
        detail_fast.compile_prompt(**kwargs)
    assert detail_fast.compile_prompt(
        **(kwargs | {"source_roles": ("product_reference",)})
    )


def test_approval_covers_complete_shared_locks_and_exact_execution():
    value = payload(detail_fast.normalize_strategy(raw()))
    approval = {
        "document_markdown": detail_fast.approval_document(value),
        "document_sha256": detail_fast.approval_hash(value),
    }
    value["refined_approval"] = approval
    assert detail_fast.approval_matches(value)
    value["pages"][0]["image_prompt"] += " changed execution"
    assert not detail_fast.approval_matches(value)


def test_fast_source_is_independent_complete_copy_and_necessary_guidance_is_bounded():
    backend = Path(__file__).resolve().parents[1]
    source = backend / "skill_sources" / refined_detail.SKILL_ID
    fast = source.with_name(detail_fast.SKILL_ID)
    original_files = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    fast_files = {
        path.relative_to(fast).as_posix() for path in fast.rglob("*") if path.is_file()
    }
    assert original_files == fast_files
    for relative in original_files - {"SKILL.md"}:
        assert (source / relative).read_bytes() == (fast / relative).read_bytes()
    assert (source / "SKILL.md").read_bytes() != (fast / "SKILL.md").read_bytes()
    old_bytes = sum(
        path.stat().st_size
        for path in [source / "SKILL.md", *(source / "references").glob("*.md")]
    )
    new_bytes = sum(
        (fast / relative).stat().st_size for relative in detail_fast.PROTOCOL_FILES
    )
    assert new_bytes < old_bytes / 2
    assert "references/quality-control.md" not in detail_fast.PROTOCOL_FILES
    assert "references/generation-tools.md" not in detail_fast.PROTOCOL_FILES
    registry = SkillManifestRegistry.bundled()
    registry.verify_source(detail_fast.SKILL_ID, fast)
    registry.verify_source(refined_detail.SKILL_ID, source)


def test_planner_output_schema_does_not_request_repeated_full_prompts():
    schema = detail_fast.strategy_schema(8)
    screen = schema["properties"]["plan"]["properties"]["screens"]["items"]
    assert "image_prompt" not in screen["properties"]
    assert "page_prompt" in screen["required"]
    assert "exactly 8" in detail_fast.planner_prompt(
        replace(request(), planning_profile=detail_fast.PROFILE)
    )
    assert json.loads(json.dumps(schema)) == schema
