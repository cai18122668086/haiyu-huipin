"""Original replies survive local interruption without another model request."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.domain import JobStatus
from app.provider import ProviderOutcome, ProviderRequest
from app.xinghe_agent import PlannerCallResult, normalize_usage
from tests.test_api import WORKSPACE_ID
from tests.test_domestic_detail_campaign_jobs import (
    explicit_synthetic_delivery,  # noqa: F401
    ready,
    request_review,
    synthetic_campaign_observation,
)
from tests.test_domestic_detail_execution import _gateway


def running_review(client, monkeypatch):
    source = ready(client)
    gateway = _gateway(client)
    calls = []
    before_images = len(gateway.image_calls)

    async def observed(request):
        assert request.planning_profile == "aurora_detail_campaign_audit"
        calls.append(request)
        raw = synthetic_campaign_observation(json.loads(request.product_information[0]))
        return PlannerCallResult(
            payload=raw,
            provider_request_id="synthetic-interrupted-campaign",
            raw_usage={"input_tokens": 9, "output_tokens": 8},
            normalized_usage=normalize_usage({"input_tokens": 9, "output_tokens": 8}),
            api_latency_seconds=0.01,
            actual_cost_micros=17,
            audit_raw_content=json.dumps(raw, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", observed)
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    coordinator = client.app.state.task_runner.coordinator
    assert coordinator.prepare(response.json()["id"]) == JobStatus.PROCESSING
    lease = coordinator.claim_next_item(response.json()["id"])
    assert lease is not None and coordinator.mark_provider_started(lease)
    request = ProviderRequest(
        workspace_id=lease.workspace_id,
        job_id=lease.job_id,
        job_item_id=lease.item_id,
        attempt_id=lease.attempt_id,
        operation=lease.operation,
        idempotency_key=lease.provider_idempotency_key,
        configuration=lease.configuration,
    )
    return source, gateway, calls, before_images, coordinator, lease, request


@pytest.mark.parametrize(
    "boundary", ["after_response", "before_report", "after_report"]
)
def test_local_interruption_is_not_a_permanent_negative_quality_report(
    client, monkeypatch, boundary
):
    from app.domestic_detail import campaign_provider
    from app.domestic_detail.review import ReviewStore

    source, gateway, calls, before_images, coordinator, lease, request = running_review(
        client, monkeypatch
    )
    source_before = (detail_workspace(source["id"]) / "session.json").read_bytes()
    fired = False
    original_finish = ReviewStore.finish
    original_once = campaign_provider._once

    def finish(store, result):
        nonlocal fired
        original_finish(store, result)
        if boundary == "after_response" and not fired:
            fired = True
            raise OSError("Synthetic interruption after original response was saved")

    def once(path, value):
        nonlocal fired
        if (
            path.name == "campaign-report.json"
            and not fired
            and boundary == "before_report"
        ):
            fired = True
            raise OSError("Synthetic interruption before report write")
        original_once(path, value)
        if (
            path.name == "campaign-report.json"
            and not fired
            and boundary == "after_report"
        ):
            fired = True
            raise OSError("Synthetic interruption after report publish")

    monkeypatch.setattr(ReviewStore, "finish", finish)
    monkeypatch.setattr(campaign_provider, "_once", once)
    provider = coordinator.provider.routes["domestic_detail_review"]
    first = asyncio.run(provider.execute(request))
    assert first.outcome == ProviderOutcome.FAILED and fired
    assert first.actual_cost_micros == 17
    root = detail_workspace(request.job_id)
    response_path = next((root / "reviews").glob("*.response.json"))
    original_response = response_path.read_bytes()
    original_reply = next((root / "reviews").glob("*.reply.txt")).read_bytes()
    if boundary != "after_report":
        assert not (root / "campaign-report.json").exists(), (
            "A local write error must not become a model quality verdict"
        )
    second = asyncio.run(provider.execute(request))
    assert second.outcome == ProviderOutcome.SUCCEEDED, second.error_message
    assert len(calls) == 1 and len(gateway.image_calls) == before_images
    assert response_path.read_bytes() == original_response
    assert next((root / "reviews").glob("*.reply.txt")).read_bytes() == original_reply
    assert coordinator.finish_attempt(lease, second)
    assert not coordinator.finish_attempt(lease, first)
    assert coordinator.finalize_job(request.job_id) == JobStatus.COMPLETED
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{request.job_id}"
    ).json()
    assert current["cost"]["actual"]["micros"] == "17"
    assert (
        detail_workspace(source["id"]) / "session.json"
    ).read_bytes() == source_before
