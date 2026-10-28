import asyncio
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

import app.masked_repair as masked_module
from app.execution import ExecutionCoordinator
from app.masked_repair import MaskedEditGatewayRequest, MaskedVisualReviewCallResult
from app.models import AssetRecord, JobAssetRecord, JobItemRecord, JobRecord
from app.provider import FakeProvider, ProviderOutcome
from app.xinghe_agent import (
    ImageEditRequest,
    TengliOpenAICompatibleGateway,
    XingheAgentError,
    _planner_prompt,
)
from tests.test_api import WORKSPACE_ID, idem, upload_asset
from tests.test_masked_gateway import VisualReviewTransport, _visual_review_request
from tests.test_masked_repair import (
    OfflineMaskedGateway,
    _create_job,
    _dispatch,
    _install_runner,
    _usage,
    _valid_inputs,
)
from tests.test_xinghe_agent import _sample_planner_request


class ReviewOutageGateway(OfflineMaskedGateway):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    async def review_masked(self, request):
        if self.failures:
            self.failures -= 1
            self.review_calls.append(request)
            return MaskedVisualReviewCallResult(
                visual_success=None,
                provider_request_id=None,
                raw_usage={},
                normalized_usage=_usage(),
                actual_cost_micros=0,
                estimated_cost_micros=10_000,
                error_code="LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED",
                error_message="controlled 503",
                retryable=True,
                http_status=503,
            )
        return await super().review_masked(request)


def _failed_edit(client, monkeypatch):
    monkeypatch.setattr(masked_module, "_VISUAL_REVIEW_RETRY_DELAY_SECONDS", 0)
    gateway = ReviewOutageGateway(4)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    _, job = _create_job(client, source["id"], mask["id"])
    _dispatch(client)
    assert len(gateway.masked_calls) == 1
    assert len(gateway.review_calls) == 4
    return gateway, job


def _retry(client, job):
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_review_outage_resumes_same_candidate_and_preserves_outside_pixels(
    client, monkeypatch
):
    monkeypatch.setattr(masked_module, "_VISUAL_REVIEW_RETRY_DELAY_SECONDS", 0)
    gateway = ReviewOutageGateway(2)
    _install_runner(client, gateway)
    source, mask, source_bytes = _valid_inputs(client)
    _, job = _create_job(client, source["id"], mask["id"])
    _dispatch(client)
    detail = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert detail["status"] == "completed", detail
    assert len(gateway.masked_calls) == 1
    assert len(gateway.review_calls) == 3
    assert detail["api_request_count"] == 4
    with client.app.state.session_factory() as session:
        asset = session.scalar(
            select(AssetRecord)
            .join(JobAssetRecord)
            .where(
                JobAssetRecord.job_id == job["id"],
                JobAssetRecord.direction == "output",
            )
        )
        output_key = asset.object_key
    result = asyncio.run(client.app.state.storage.read_bytes(output_key))
    before = Image.open(BytesIO(source_bytes)).convert("RGBA")
    after = Image.open(BytesIO(result)).convert("RGBA")
    for y in range(8):
        for x in range(8):
            if not (2 <= x < 6 and 2 <= y < 6):
                assert before.getpixel((x, y)) == after.getpixel((x, y))


def test_explicit_retry_of_review_failure_does_not_generate_another_image(
    client, monkeypatch
):
    gateway, job = _failed_edit(client, monkeypatch)
    retried = _retry(client, job)
    _dispatch(client)
    result = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{retried['id']}"
    ).json()
    assert result["status"] == "completed", result
    assert len(gateway.masked_calls) == 1
    assert result["api_request_count"] == 1


def test_damaged_saved_candidate_fails_without_generating_again(client, monkeypatch):
    gateway, job = _failed_edit(client, monkeypatch)
    with client.app.state.session_factory() as session:
        asset = session.scalar(
            select(AssetRecord)
            .join(JobAssetRecord)
            .where(
                JobAssetRecord.job_id == job["id"],
                JobAssetRecord.role == "provider_raw",
            )
        )
        (Path(client.app.state.settings.storage_dir) / asset.object_key).write_bytes(
            b"damaged candidate"
        )
    retried = _retry(client, job)
    _dispatch(client)
    with client.app.state.session_factory() as session:
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == retried["id"])
        )
        assert item.error_code == "LOCAL_EDIT_RAW_INVALID"
    assert len(gateway.masked_calls) == 1


def test_changed_instruction_cannot_borrow_prior_candidate(client, monkeypatch):
    gateway, job = _failed_edit(client, monkeypatch)
    retried = _retry(client, job)
    with client.app.state.session_factory() as session:
        record = session.get(JobRecord, retried["id"])
        config = dict(record.configuration)
        config["settings"] = {**config["settings"], "instruction": "make it blue"}
        record.configuration = config
        session.commit()
    gateway.outcomes.append("success")
    _dispatch(client)
    assert len(gateway.masked_calls) == 2


def test_visual_review_uses_configured_vision_model_and_next_route():
    transport = VisualReviewTransport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="primary-test",
        base_url="https://primary.invalid",
        planner_model="planner",
        vision_model="vision",
        image_model="image",
        failover_enabled=True,
        failover_api_key="backup-test",
        failover_base_url="https://backup.invalid",
        failover_model="backup-vision",
        transport=transport,
    )
    request = _visual_review_request()
    assert asyncio.run(gateway.review_masked(request)).visual_success is True
    assert transport.call["payload"]["model"] == "vision"
    assert (
        asyncio.run(
            gateway.review_masked(replace(request, route_attempt=1))
        ).visual_success
        is True
    )
    assert transport.call["url"] == "https://backup.invalid/v1/chat/completions"
    assert transport.call["payload"]["model"] == "backup-vision"


@pytest.mark.parametrize("profile", ["aurora_set_audit", "creative_image"])
@pytest.mark.parametrize("envelope_error", [False, True])
def test_invalid_planning_response_uses_next_route(profile, envelope_error):
    creative = {
        "product_identity_lock": "same product",
        "hard_composition_skeleton": "left",
        "normalized_anchors": [],
        "product_role_map": {},
        "typography_zones": [],
        "commercial_intent": "show product",
        "forbidden_transfer": [],
        "post_generation_review": "check identity",
        "original_execution_prompt": "make it pink",
    }
    final = creative if profile == "creative_image" else {"set_audit_passed": True}

    class Transport:
        calls = 0

        async def post_json(self, **kwargs):
            self.calls += 1
            if self.calls == 1 and envelope_error:
                raise XingheAgentError(
                    "PROVIDER_RESPONSE_INVALID", "invalid JSON response body"
                )
            content = json.dumps(final) if self.calls > 1 else "temporarily unavailable"
            return 200, {"choices": [{"message": {"content": content}}]}

    transport = Transport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="primary-test",
        base_url="https://primary.invalid",
        planner_model="planner",
        image_model="image",
        failover_enabled=True,
        failover_api_key="backup-test",
        failover_base_url="https://backup.invalid",
        transport=transport,
    )
    request = replace(_sample_planner_request(), planning_profile=profile)
    result = asyncio.run(gateway.plan(request))
    assert result.payload == final
    assert transport.calls == 2
    if profile == "creative_image":
        assert "ONE creative ecommerce image" in _planner_prompt(request)


@pytest.mark.parametrize(
    "operation",
    [
        "product_image",
        "reference_remix",
        "model_pose_transfer",
        "creative_image",
    ],
)
@pytest.mark.parametrize("failure_kind", ["transient", "permanent", "unknown"])
def test_independent_workflows_recover_only_known_transient_failures(
    client, operation, failure_kind
):
    first = upload_asset(client, filename="first.png")["id"]
    second = upload_asset(client, filename="second.png")["id"]
    inputs = {"product_asset_ids": [first]}
    configuration = {}
    if operation == "reference_remix":
        inputs["reference_asset_ids"] = [second]
    elif operation == "model_pose_transfer":
        inputs = {"model_source_asset_id": first, "pose_reference_asset_id": second}
    elif operation == "product_image":
        configuration = {"task_type": "change_color", "target_color": "pink"}
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": operation,
            "intent": "submit",
            "input": inputs,
            "configuration": configuration,
        },
    )
    assert created.status_code == 202, created.text

    class Provider(FakeProvider):
        async def execute(self, request):
            result = await super().execute(request)
            if self.call_count == 1:
                return replace(
                    result,
                    artifact=None,
                    outcome=ProviderOutcome.OUTCOME_UNKNOWN
                    if failure_kind == "unknown"
                    else ProviderOutcome.FAILED,
                    error_code="IMAGE_REQUEST_FAILED",
                    error_message="controlled failure",
                    retryable=failure_kind != "permanent",
                    http_status=400 if failure_kind == "permanent" else 503,
                )
            return result

    provider = Provider()
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        artifact_storage=client.app.state.storage,
    )
    asyncio.run(coordinator.run(created.json()["id"]))
    detail = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created.json()['id']}"
    ).json()
    assert provider.call_count == (2 if failure_kind == "transient" else 1)
    assert detail["status"] == (
        "completed" if failure_kind == "transient" else "failed"
    )
    if failure_kind == "unknown":
        assert detail["items"][0]["error_code"] == "PROVIDER_OUTCOME_UNKNOWN"


@pytest.mark.parametrize("masked", [False, True])
@pytest.mark.parametrize(
    "code", ["PROVIDER_TRANSPORT_FAILED", "PROVIDER_RESPONSE_INVALID"]
)
def test_image_gateway_keeps_unconfirmed_responses_unknown(masked, code):
    class Transport:
        calls = 0

        async def post_multipart(self, **kwargs):
            self.calls += 1
            raise XingheAgentError(code, "response lost", retryable=True)

    transport = Transport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="primary-test",
        base_url="https://primary.invalid",
        planner_model="planner",
        image_model="image",
        transport=transport,
    )
    if masked:
        result = asyncio.run(
            gateway.edit_masked(
                MaskedEditGatewayRequest(
                    source_png=b"source",
                    api_mask_png=b"mask",
                    mask_png=b"mask",
                    prompt="make it pink",
                    quality="medium",
                    width=4,
                    height=4,
                    idempotency_key="unknown-image",
                )
            )
        )
    else:
        result = asyncio.run(
            gateway.edit(
                ImageEditRequest(
                    workspace_id="workspace",
                    job_id="job",
                    job_item_id="item",
                    attempt_id="attempt",
                    page_number=1,
                    group_key="group",
                    sources=(),
                    prompt="make it pink",
                    quality="medium",
                    idempotency_key="unknown-image",
                    size="1024x1024",
                )
            )
        )
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert result.retryable is False
    assert transport.calls == 1
