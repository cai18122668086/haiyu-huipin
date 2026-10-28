"""Public lease fencing and durable response reuse for one optional review."""

import asyncio
import json
from datetime import timedelta

from app.aurora_detail_runtime import detail_workspace
from app.domain import JobStatus
from app.models import JobAttemptRecord, utc_now
from app.provider import ProviderOutcome, ProviderRequest, ProviderResult
from app.xinghe_agent import PlannerCallResult, normalize_usage
from tests.test_api import WORKSPACE_ID
from tests.test_domestic_detail_campaign_jobs import (
    explicit_synthetic_delivery,  # noqa: F401
    ready,
    request_review,
    synthetic_campaign_observation,
)
from tests.test_domestic_detail_execution import _gateway


def test_expired_review_lease_without_files_cannot_create_a_second_paid_intent(client):
    source = ready(client)
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    review_id = response.json()["id"]
    coordinator = client.app.state.task_runner.coordinator
    gateway = _gateway(client)
    before = len(gateway.planner_calls), len(gateway.image_calls)
    assert coordinator.prepare(review_id) == JobStatus.PROCESSING
    lease = coordinator.claim_next_item(review_id)
    assert lease is not None and coordinator.mark_provider_started(lease)
    with client.app.state.session_factory() as db:
        attempt = db.get(JobAttemptRecord, lease.attempt_id)
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert coordinator.claim_next_item(review_id) is None
    assert (
        coordinator.finish_attempt(
            lease,
            ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id="stale-review-result",
            ),
        )
        is False
    )
    assert coordinator.finalize_job(review_id) == JobStatus.FAILED
    assert not list((detail_workspace(review_id) / "reviews").glob("*.request.json"))
    repeated = request_review(client, source)
    assert repeated.status_code == 409, repeated.text
    assert repeated.json()["error"]["code"] == "NATIVE_DETAIL_CAMPAIGN_OUTCOME_UNKNOWN"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before


def test_completed_response_reentry_reuses_original_observations_without_new_call(
    client, monkeypatch
):
    source = ready(client)
    gateway = _gateway(client)
    original_plan = gateway.plan
    calls = []

    async def observed(request):
        if request.planning_profile != "aurora_detail_campaign_audit":
            return await original_plan(request)
        calls.append(request)
        raw = synthetic_campaign_observation(json.loads(request.product_information[0]))
        return PlannerCallResult(
            payload=raw,
            provider_request_id="synthetic-reused-campaign-response",
            raw_usage={"input_tokens": 9, "output_tokens": 8},
            normalized_usage=normalize_usage({"input_tokens": 9, "output_tokens": 8}),
            api_latency_seconds=0.01,
            actual_cost_micros=17,
            audit_raw_content=json.dumps(raw, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", observed)
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    review_id = response.json()["id"]
    coordinator = client.app.state.task_runner.coordinator
    assert coordinator.prepare(review_id) == JobStatus.PROCESSING
    lease = coordinator.claim_next_item(review_id)
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
    provider = coordinator.provider.routes["domestic_detail_review"]
    before_images = len(gateway.image_calls)
    first = asyncio.run(provider.execute(request))
    assert first.outcome == ProviderOutcome.SUCCEEDED, first.error_message
    root = detail_workspace(review_id)
    original_report = (root / "campaign-report.json").read_bytes()
    original_reply = next((root / "reviews").glob("*.reply.txt")).read_bytes()
    second = asyncio.run(provider.execute(request))
    assert second.outcome == ProviderOutcome.SUCCEEDED, second.error_message
    assert len(calls) == 1 and len(gateway.image_calls) == before_images
    assert second.actual_cost_micros == first.actual_cost_micros == 17
    assert second.raw_usage["campaign_review"]["report_available"] is True
    assert (root / "campaign-report.json").read_bytes() == original_report
    assert next((root / "reviews").glob("*.reply.txt")).read_bytes() == original_reply
    assert coordinator.finish_attempt(lease, second)
    assert not coordinator.finish_attempt(lease, first)
    assert coordinator.finalize_job(review_id) == JobStatus.COMPLETED
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}").json()
    assert current["cost"]["actual"]["micros"] == "17"


def test_later_campaign_review_keeps_completed_source_delivery_exact(
    client, monkeypatch
):
    from tests.test_domestic_detail_delivery import REVIEWS, finalize

    source = ready(client)
    completed = finalize(client, source["id"])
    assert completed.status_code == 200, completed.text
    source_url = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}"
    before_job = client.get(source_url).json()
    root = detail_workspace(source["id"])
    before_session = (root / "session.json").read_bytes()
    manifest_path = json.loads(before_session)["final"]["manifest"]
    from pathlib import Path

    before_manifest = Path(manifest_path).read_bytes()
    gateway = _gateway(client)
    before_images = len(gateway.image_calls)
    calls = []

    async def observed(request):
        assert request.planning_profile == "aurora_detail_campaign_audit"
        calls.append(request)
        raw = synthetic_campaign_observation(json.loads(request.product_information[0]))
        return PlannerCallResult(
            payload=raw,
            provider_request_id="synthetic-later-campaign-review",
            raw_usage={"input_tokens": 9, "output_tokens": 8},
            normalized_usage=normalize_usage({"input_tokens": 9, "output_tokens": 8}),
            api_latency_seconds=0.01,
            actual_cost_micros=17,
            audit_raw_content=json.dumps(raw, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", observed)
    assert REVIEWS[source["id"]]
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    from tests.test_api import dispatch_pending

    dispatch_pending(client)
    review_id = response.json()["id"]
    report = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/report"
    )
    assert report.status_code == 200, report.text
    assert report.json()["report"]["native_gate_accepted"] is True
    assert len(calls) == 1 and len(gateway.image_calls) == before_images
    assert client.get(source_url).json() == before_job
    assert (root / "session.json").read_bytes() == before_session
    assert Path(manifest_path).read_bytes() == before_manifest


def test_changed_native_preview_stops_before_any_paid_model_call(client, monkeypatch):
    from pathlib import Path

    from PIL import Image

    from app.aurora_detail_runtime import AuroraDetailHarnessRunner
    from tests.test_api import dispatch_pending

    source = ready(client)
    original_session = (detail_workspace(source["id"]) / "session.json").read_bytes()
    gateway = _gateway(client)
    before = len(gateway.planner_calls), len(gateway.image_calls)
    original_run = AuroraDetailHarnessRunner.run

    def changed_preview(self, command, workspace, arguments):
        result = original_run(self, command, workspace, arguments)
        if command == "assemble":
            path = Path(result["long_page"])
            with Image.open(path) as image:
                image.load()
                altered = image.copy()
            pixel = altered.getpixel((2, 4))
            altered.putpixel((2, 4), ((pixel[0] + 1) % 256, pixel[1], pixel[2]))
            altered.save(path, format="PNG")
        return result

    monkeypatch.setattr(AuroraDetailHarnessRunner, "run", changed_preview)
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    review_id = response.json()["id"]
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}").json()
    assert current["status"] == "failed", current
    assert "Preview pixels differ" in current["items"][0]["error_message"]
    assert current["cost"]["actual"]["micros"] == "0"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before
    assert not list((detail_workspace(review_id) / "reviews").glob("*.request.json"))
    assert (
        detail_workspace(source["id"]) / "session.json"
    ).read_bytes() == original_session


def test_missing_original_binding_and_reference_metadata_stop_before_paid_review(
    client, monkeypatch
):
    from app.models import AssetRecord, JobPlanRecord
    from tests.test_api import dispatch_pending
    from tests.test_domestic_detail_delivery import REVIEWS

    source = ready(client)
    root = detail_workspace(source["id"])
    binding = root / "native-contract-binding.json"
    original_binding = binding.read_bytes()
    backup = binding.with_suffix(".preserved")
    binding.rename(backup)
    try:
        quote = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}/campaign-review/quote",
            params={"final_review_sha256": REVIEWS[source["id"]]},
        )
        assert quote.status_code == 409, quote.text
        assert not binding.exists()
    finally:
        backup.rename(binding)
    assert binding.read_bytes() == original_binding
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    review_id = response.json()["id"]
    with client.app.state.session_factory() as db:
        plan = db.get(JobPlanRecord, response.json()["configuration"]["source_plan_id"])
        rows = plan.payload["source_asset_manifest"][0]["assets"]
        row = next(
            row for row in rows if row["role"] not in {"style", "style_reference"}
        )
        asset = db.get(AssetRecord, row["asset_id"])
        asset.sha256 = "b" * 64
        db.commit()
    gateway = _gateway(client)
    calls = []
    before_images = len(gateway.image_calls)

    async def observed(request):
        assert request.planning_profile == "aurora_detail_campaign_audit"
        calls.append(request)
        raw = synthetic_campaign_observation(json.loads(request.product_information[0]))
        return PlannerCallResult(
            payload=raw,
            provider_request_id="synthetic-reference-metadata-drift",
            raw_usage={"input_tokens": 9, "output_tokens": 8},
            normalized_usage=normalize_usage({"input_tokens": 9, "output_tokens": 8}),
            api_latency_seconds=0.01,
            actual_cost_micros=17,
            audit_raw_content=json.dumps(raw, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", observed)
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}").json()
    assert current["status"] == "failed", current
    assert calls == [] and len(gateway.image_calls) == before_images
    assert current["cost"]["actual"]["micros"] == "0"
    assert not list((detail_workspace(review_id) / "reviews").glob("*.request.json"))
