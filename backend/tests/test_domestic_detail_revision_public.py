"""The public submission and execution path, using explicit offline model callbacks."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from sqlalchemy import select

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail.revision_authorization import REVISION_APPROVAL_TEXT
from app.models import JobAssetRecord, JobPlanRecord, JobRecord
from app.xinghe_agent import (
    PlannerCallResult,
    XingheImageProvider,
    _artifact_from_bytes,
    normalize_usage,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_delivery import (  # noqa: F401
    ORIGINAL_REVIEW,
    REVIEWS,
    explicit_synthetic_delivery,
    ready,
)
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_revision_scope import model_result, observation
from tests.test_native_detail_local_adjustment import adjustment, inventory


def test_http_submission_runs_owned_revision_with_original_pixel_review(
    client, monkeypatch
):
    source = ready(client)
    source_id = source["id"]
    prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source_id}"
    factory = client.app.state.session_factory
    before_files = inventory(detail_workspace(source_id))
    with factory() as db:
        original_configuration = deepcopy(db.get(JobRecord, source_id).configuration)
        original_payload = deepcopy(
            db.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == source_id)
            ).payload
        )
    gateway = _gateway(client)
    monkeypatch.setattr(gateway, "provider_name", "offline-local-review")
    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", ORIGINAL_REVIEW
    )
    routed = client.app.state.task_runner.coordinator.provider
    provider = routed.domestic_detail_provider.delegate
    assert provider.revision_settings is client.app.state.settings
    prior_plan = gateway.plan
    calls = []
    images_before = len(gateway.image_calls)

    async def reviewed(call):
        if call.planning_profile == "aurora_detail_revision_scope":
            calls.append(call)
            return model_result(call, observation(call))
        if call.planning_profile != "aurora_detail_pair_audit":
            return await prior_plan(call)
        calls.append(call)
        context = json.loads(call.product_information[0])
        local = context["local_revision_context"]
        originals = [
            s
            for s in call.sources
            if s.source_role.startswith("local_revision_original_")
        ]
        assert len(originals) == 3
        assert originals[0].sha256 == quote["source_pair_canvas"]["sha256"]
        assert all(hashlib.sha256(s.payload).hexdigest() == s.sha256 for s in originals)
        artifact = _artifact_from_bytes(
            call.sources[-1].payload,
            filename="offline.png",
            expected_size="1440x3840",
            preserve_provider_artifact=True,
        )
        raw = provider._native_detail_judgment(context, artifact)
        evidence = {
            "before_observation": "SYNTHETIC_OFFLINE_FIXTURE original pixels inspected",
            "after_observation": "SYNTHETIC_OFFLINE_FIXTURE candidate pixels inspected",
            "visible_evidence": "SYNTHETIC_OFFLINE_FIXTURE before/after compared",
            "pass": True,
        }
        raw["local_revision_observation"] = {
            **local["expected_observation"],
            **{
                group: [
                    {"index": i, "original_requirement": text, **evidence}
                    for i, text in enumerate(local["requirements"][group])
                ]
                for group in ("change_only", "preserve", "must_not_regress")
            },
            "sibling": {**local["sibling_binding"], **evidence},
        }
        return PlannerCallResult(
            payload=raw,
            provider_request_id="offline-public-local-review",
            raw_usage={"total_tokens": 9},
            normalized_usage=normalize_usage({"total_tokens": 9}),
            api_latency_seconds=0.01,
            actual_cost_micros=1,
            audit_raw_content=json.dumps(raw, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", reviewed)
    body = {"final_review_sha256": REVIEWS[source_id], "adjustment": adjustment("M04")}
    result = client.post(prefix + "/local-revision/quote", json=body)
    assert result.status_code == 200, result.text
    quote = result.json()
    submit = {
        **body,
        "quote_sha256": quote["sha256"],
        "approval_text": REVISION_APPROVAL_TEXT,
    }
    key = idem()
    created = client.post(prefix + "/local-revision", headers=key, json=submit)
    assert created.status_code == 202, created.text
    child_id = created.json()["id"]
    assert child_id != source_id
    replay = client.post(prefix + "/local-revision", headers=key, json=submit)
    assert replay.status_code == 202 and replay.json()["id"] == child_id
    assert len(gateway.image_calls) == images_before
    dispatch_pending(client)
    child_prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{child_id}"
    current = client.get(child_prefix).json()
    assert current["status"] == "awaiting_final_review", current.get("error_message")
    assert len(gateway.image_calls) == images_before + 1
    assert [call.planning_profile for call in calls] == [
        "aurora_detail_revision_scope",
        "aurora_detail_pair_audit",
    ]
    review = client.post(child_prefix + "/detail-final-review")
    assert review.status_code == 200, review.text
    assert len(review.json()["screens"]) == 8
    with factory() as db:
        links = db.scalars(
            select(JobAssetRecord).where(
                JobAssetRecord.job_id == child_id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.role == "detail_page",
            )
        ).all()
        assert len(links) == 8
        plan = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == child_id))
        assert len(plan.payload["usage"]["planner"]) == 2
        assert db.get(JobRecord, source_id).configuration == original_configuration
        assert (
            db.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == source_id)
            ).payload
            == original_payload
        )
    finalized = client.post(
        child_prefix + "/finalize",
        headers=idem(),
        json={
            "approval_text": "我已审阅全部图片并接受本次合成修订的残差。",
            "final_review_sha256": review.json()["sha256"],
        },
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "completed"
    assert inventory(detail_workspace(source_id)) == before_files
    # A second revision targets another pair and must retain both older origins.
    first_child_files = inventory(detail_workspace(child_id))
    with factory() as db:
        first_child_payload = deepcopy(
            db.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == child_id)
            ).payload
        )
    refreshed = client.post(child_prefix + "/detail-final-review")
    assert refreshed.status_code == 200, refreshed.text
    second_body = {
        "final_review_sha256": refreshed.json()["sha256"],
        "adjustment": adjustment("M02"),
    }
    quoted = client.post(child_prefix + "/local-revision/quote", json=second_body)
    assert quoted.status_code == 200, quoted.text
    quote = quoted.json()
    second = client.post(
        child_prefix + "/local-revision",
        headers=idem(),
        json={
            **second_body,
            "quote_sha256": quote["sha256"],
            "approval_text": REVISION_APPROVAL_TEXT,
        },
    )
    assert second.status_code == 202, second.text
    dispatch_pending(client)
    second_prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{second.json()['id']}"
    second_current = client.get(second_prefix).json()
    assert second_current["status"] == "awaiting_final_review", second_current
    second_review = client.post(second_prefix + "/detail-final-review")
    assert second_review.status_code == 200, second_review.text
    assert len(second_review.json()["screens"]) == 8
    assert len(gateway.image_calls) == images_before + 2
    assert inventory(detail_workspace(source_id)) == before_files
    assert inventory(detail_workspace(child_id)) == first_child_files
    with factory() as db:
        assert (
            db.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == child_id)
            ).payload
            == first_child_payload
        )
