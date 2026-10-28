"""Native fast Skill contract, real source tools and isolated metered gateway."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import UsageEventRecord
from app.new_detail_skill_provider import BundledSkillManifestValidator
from app.provider import ProviderOutcome
from app.remix_fast_provider import (
    CHECKS,
    GEOMETRY_FIELDS,
    PREPARE_PROFILE,
    REFERENCE_FIELDS,
    RemixFastProvider,
    _compile,
)
from app.remix_fast_runtime import SKILL_ID, RemixFastRuntime
from app.xinghe_agent import PlannerCallResult, normalize_usage
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_xinghe_single_image_provider import (
    _image_bytes,  # noqa: F401
    _reference_follow_request,
)
from tests.test_xinghe_single_image_provider import harness as harness


def brief():
    return {
        "geometry_lock": {
            key: "宽高比 1:2，轮廓和部件位置依原图" for key in GEOMETRY_FIELDS
        },
        "identity_constraints": "保留原图横向纹样，不是竖向纹样；不改变结构",
        "reference_facts": {
            key: "从对标图观察到的结构与留白" for key in REFERENCE_FIELDS
        },
        "page_duty": "单张商品主视觉",
        "buyer_conclusion": "看清商品真实细节",
        "generation_brief": "主体 x=0.2..0.8 y=0.15..0.9，顶部留白 0.1",
        "approved_visible_copy": ["轻量随行杯"],
        "critical_regions": [
            {"box": [0.2, 0.2, 0.8, 0.8], "feature": "产品接口和纹样方向"}
        ],
        "product_present": True,
    }


def install(harness, tmp_path, *, failed_check=None):
    _, request = _reference_follow_request(
        harness,
        reference_payload=_image_bytes(),
        exact_skill=True,
    )
    envelope = request.configuration["new_skill_adapter"]
    envelope["skill_id"] = SKILL_ID
    envelope["skill_content_hash"] = BundledSkillManifestValidator().validate(SKILL_ID)
    gateway = harness.gateway

    async def plan(value):
        gateway.plan_requests.append(value)
        if value.planning_profile == PREPARE_PROFILE:
            payload = brief()
        else:
            payload = {
                "pixel_inspected": True,
                "checks": [
                    {
                        "name": key,
                        "passed": key != failed_check,
                        "evidence": "真实像素与原图逐处对照",
                    }
                    for key in CHECKS
                ],
                "differences": ["本品已替换对标中的商品"],
                "unresolved": [],
            }
        return PlannerCallResult(
            payload=payload,
            provider_request_id="offline_plan",
            raw_usage={"input_tokens": 10, "output_tokens": 5},
            normalized_usage=normalize_usage({"input_tokens": 10, "output_tokens": 5}),
            api_latency_seconds=0.001,
            actual_cost_micros=123,
        )

    async def save(image):
        harness.storage.objects[image.object_key] = image.payload
        return SimpleNamespace(object_key=image.object_key)

    gateway.plan = plan
    harness.storage.save_validated = save
    provider = RemixFastProvider(
        harness.session_factory,
        gateway,
        storage=harness.storage,
        operation="reference_remix",
        skill_id=SKILL_ID,
        manifest_validator=BundledSkillManifestValidator().validate,
        work_root=tmp_path / "fast",
    )
    return provider, request


def test_fast_skill_uses_full_source_native_tools_and_preserves_bytes(
    harness, tmp_path
):
    provider, request = install(harness, tmp_path)
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED, result
    assert result.artifact.payload == harness.gateway.result.artifact.payload
    assert len(harness.gateway.requests) == 1
    call = harness.gateway.requests[0]
    assert call.single_dispatch is True
    assert [s.source_role for s in call.sources] == [
        "product_reference",
        "style_reference",
    ]
    assert "PRODUCT GEOMETRY LOCK" in call.prompt
    assert "Target canvas " + call.size.replace("x", "×") in call.prompt
    assert "<>" not in call.prompt
    assert len(harness.gateway.plan_requests) == 2
    assert "对标二创" in harness.gateway.plan_requests[0].system_prompt
    assert result.raw_usage["network_requests"] == 3
    assert result.actual_cost_micros == 321246
    assert result.raw_usage["metrics"]["quick_check"]["pixel_inspected"]
    assert (tmp_path / "fast/job_test/item_test/watermark.png").exists()
    assert (tmp_path / "fast/job_test/item_test/identity-0.png").exists()
    again = asyncio.run(provider.execute(request))
    assert again.artifact.sha256 == result.artifact.sha256
    assert len(harness.gateway.requests) == 1
    assert len(harness.gateway.plan_requests) == 2


@pytest.mark.parametrize("check", CHECKS)
def test_fast_quick_check_failure_never_delivers_or_automatically_redraws(
    harness,
    tmp_path,
    check,
):
    provider, request = install(harness, tmp_path, failed_check=check)
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.FAILED, result
    assert result.error_code == "REMIX_FAST_QUICK_CHECK_FAILED"
    assert result.artifact is None
    assert not result.retryable
    asyncio.run(provider.execute(request))
    assert len(harness.gateway.requests) == 1


def test_fast_unknown_image_outcome_stops_all_resubmissions(harness, tmp_path):
    provider, request = install(harness, tmp_path)
    harness.gateway.error = TimeoutError("offline timeout")
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN, result
    assert not result.retryable
    again = asyncio.run(provider.execute(request))
    assert again.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert len(harness.gateway.requests) == 1


def test_fast_wrong_aspect_is_preserved_but_not_delivered(harness, tmp_path):
    provider, request = install(harness, tmp_path)
    from tests.test_xinghe_single_image_provider import _artifact

    harness.gateway.result = replace(
        harness.gateway.result, artifact=_artifact((160, 160))
    )
    result = asyncio.run(provider.execute(request))
    assert result.error_code == "REMIX_FAST_ASPECT_RATIO_FAILED", result
    assert result.artifact is None
    assert len(harness.gateway.requests) == 1
    assert len(harness.gateway.plan_requests) == 1


def test_fast_native_tool_rejects_outside_workspace(tmp_path):
    runtime = RemixFastRuntime()
    workspace = tmp_path / "job"
    workspace.mkdir()
    with pytest.raises(ValueError, match="escaped"):
        runtime.run("ratio", workspace, tmp_path / "outside.png")


def test_native_prompt_is_filled_from_original_source_template():
    runtime = RemixFastRuntime()
    assert len(runtime.verify()) == 64
    prompt = _compile(runtime.prompt_template(), brief(), 1152, 1536)
    assert "Target canvas 1152×1536" in prompt
    assert "APPROVED VISIBLE COPY ONLY: [" in prompt
    assert "轻量随行杯" in prompt
    assert not any(mark in prompt for mark in ("<>", "<W>", "<H>"))


def _website_fast(client, tmp_path):
    from app.task_runner import MockTaskRunner
    from tests.test_p0_amazon_fail_closed import _CountingGateway

    gateway = _CountingGateway()
    gateway.review_fails = False
    original = gateway.plan

    async def plan(request):
        if request.planning_profile not in {
            PREPARE_PROFILE,
            "aurora_remix_fast_review",
        }:
            return await original(request)
        gateway.planner_calls.append(request)
        payload = (
            brief()
            if request.planning_profile == PREPARE_PROFILE
            else {
                "pixel_inspected": True,
                "checks": [
                    {
                        "name": name,
                        "passed": not gateway.review_fails,
                        "evidence": "逐处对照真实像素",
                    }
                    for name in CHECKS
                ],
                "differences": ["替换为本品"],
                "unresolved": [],
            }
        )
        return PlannerCallResult(
            payload=payload,
            provider_request_id=f"offline_{len(gateway.planner_calls)}",
            raw_usage={"input_tokens": 10, "output_tokens": 5},
            normalized_usage=normalize_usage({"input_tokens": 10, "output_tokens": 5}),
            api_latency_seconds=0.001,
            actual_cost_micros=123,
        )

    gateway.plan = plan
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        reference_remix_provider_enabled=True,
        aurora_work_root=tmp_path / "fast-web",
    )
    product = upload_asset(client)
    reference = upload_asset(client, filename="benchmark.png")
    payload = {
        "type": "reference_remix",
        "intent": "submit",
        "display_name": "Fast native website test",
        "input": {
            "product_asset_ids": [product["id"]],
            "reference_asset_ids": [reference["id"]],
        },
        "configuration": {
            "reference_skill": SKILL_ID,
            "aspect_ratio": "follow-reference",
            "image_count": 1,
            "execution_mode": "planner",
            "selling_points": ["真实商品结构保持不变"],
        },
    }
    return gateway, payload


def test_fast_website_submission_routes_to_new_skill_and_accounts_once(
    client, tmp_path
):
    gateway, payload = _website_fast(client, tmp_path)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=payload
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
    assert result["status"] == "completed", json.dumps(
        result, ensure_ascii=False, indent=2
    )
    assert result["succeeded_item_count"] == 1
    assert len(gateway.image_calls) == 1
    assert len(gateway.planner_calls) == 2
    asyncio.run(client.app.state.task_runner.run(job_id))
    assert len(gateway.image_calls) == 1
    with client.app.state.session_factory() as db:
        usage = db.scalars(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job_id)
        ).all()
        assert len(usage) == 1
    payload["configuration"]["selling_points"] = []
    rejected = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=payload
    )
    assert rejected.status_code == 422


def test_fast_explicit_retry_of_known_bad_image_starts_one_new_candidate(
    client, tmp_path
):
    gateway, payload = _website_fast(client, tmp_path)
    gateway.review_fails = True
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=payload
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
    assert result["status"] == "failed"
    assert result["items"][0]["error_code"] == "REMIX_FAST_QUICK_CHECK_FAILED"
    assert len(gateway.image_calls) == 1
    gateway.review_fails = False
    retry = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/retry",
        headers=idem(),
        json={"scope": "failed", "item_ids": []},
    )
    assert retry.status_code == 201, retry.text
    dispatch_pending(client)
    retried = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{retry.json()['id']}"
    ).json()
    assert retried["status"] == "completed", json.dumps(retried, ensure_ascii=False)
    assert len(gateway.image_calls) == 2
    assert len(gateway.planner_calls) == 4


def test_fast_explicit_retry_cannot_resubmit_unknown_paid_outcome(client, tmp_path):
    gateway, payload = _website_fast(client, tmp_path)
    original = gateway.edit

    async def unknown(request):
        result = await original(request)
        return replace(
            result,
            outcome=ProviderOutcome.OUTCOME_UNKNOWN,
            artifact=None,
            error_code="OFFLINE_UNKNOWN",
            retryable=False,
        )

    gateway.edit = unknown
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=payload
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch_pending(client)
    assert len(gateway.image_calls) == 1
    retry = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/retry",
        headers=idem(),
        json={"scope": "failed", "item_ids": []},
    )
    assert retry.status_code in {201, 409}, retry.text
    if retry.status_code == 201:
        dispatch_pending(client)
    assert len(gateway.image_calls) == 1


def test_fast_live_observed_array_is_excluded_by_the_wire_contract():
    from jsonschema import Draft202012Validator

    from app.remix_fast_contract import response_format
    from app.remix_fast_provider import _validate_brief

    schema = response_format(PREPARE_PROFILE)["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    observed = {
        **brief(),
        "identity_constraints": ["保留透明方形瓶盖", "保留横向蝴蝶结"],
    }
    errors = list(validator.iter_errors(observed))
    assert any(list(error.path) == ["identity_constraints"] for error in errors)
    with pytest.raises(ValueError):
        _validate_brief(observed)
    observed["identity_constraints"] = "；".join(observed["identity_constraints"])
    validator.validate(observed)
    assert _validate_brief(observed) == observed


def test_fast_planner_wire_contract_has_typed_stages_and_computed_canvas(
    harness, tmp_path
):
    from jsonschema import Draft202012Validator

    from app.xinghe_agent import _planner_response_format

    provider, request = install(harness, tmp_path)
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    prepare, review = harness.gateway.plan_requests
    canvas = json.loads(prepare.product_information[0])["target_canvas"]
    image_request = harness.gateway.requests[0]
    assert image_request.size == f"{canvas['width']}x{canvas['height']}"
    assert f"Target canvas {canvas['width']}×{canvas['height']}" in image_request.prompt
    for stage in (prepare, review):
        response = _planner_response_format(stage)
        assert response["type"] == "json_schema"
        schema = response["json_schema"]["schema"]
        Draft202012Validator.check_schema(schema)
        if stage is prepare:
            Draft202012Validator(schema).validate(brief())
        else:
            Draft202012Validator(schema).validate(
                result.raw_usage["metrics"]["quick_check"]
            )


def test_fast_analysis_failure_retains_upstream_diagnostics_without_an_image(
    harness, tmp_path
):
    provider, request = install(harness, tmp_path)

    async def unavailable(_):
        return PlannerCallResult(
            payload=None,
            provider_request_id="analysis_down",
            raw_usage=None,
            normalized_usage=normalize_usage(None),
            api_latency_seconds=0,
            actual_cost_micros=None,
            error_code="PLANNER_RATE_LIMITED",
            http_status=429,
        )

    harness.gateway.plan = unavailable
    result = asyncio.run(provider.execute(request))
    assert result.error_code == "REMIX_FAST_PREPARATION_FAILED"
    assert "尚未开始生图" in result.error_message
    assert harness.gateway.requests == []
    call = result.raw_usage["calls"][0]
    assert call["error_code"] == "PLANNER_RATE_LIMITED"
    assert call["http_status"] == 429
    assert call["provider_request_id"] == "analysis_down"
