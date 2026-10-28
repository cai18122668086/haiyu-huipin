"""A new zero-model queue task recovers an original paid result without rewriting it."""

import asyncio
import hashlib
import json

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import detail_workspace
from app.domain import JobStatus
from app.models import UsageEventRecord
from app.provider import ProviderOutcome
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_campaign_interruptions import (
    explicit_synthetic_delivery,  # noqa: F401
    running_review,
)
from tests.test_domestic_detail_campaign_jobs import request_review


def inventory(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "boundary", ["response_partial", "before_report", "source_changed"]
)
def test_public_saved_result_recovery_is_free_queued_idempotent_and_source_immutable(
    client, monkeypatch, boundary
):
    from app.domestic_detail import campaign_provider
    from app.domestic_detail.review import ReviewStore

    source, gateway, calls, before_images, coordinator, lease, request = running_review(
        client, monkeypatch
    )
    provider = coordinator.provider.routes["domestic_detail_review"]
    original_finish = ReviewStore.finish
    original_once = campaign_provider._once
    fired = False

    def finish(store, result):
        nonlocal fired
        original_finish(store, result)
        if boundary == "response_partial" and not fired:
            fired = True
            store.response_path.write_bytes(b"partial-original-response")
            raise OSError("Synthetic response was torn after full result was journaled")

    def once(path, value):
        nonlocal fired
        if (
            path.name == "campaign-report.json"
            and boundary in {"before_report", "source_changed"}
            and not fired
        ):
            fired = True
            raise OSError("Synthetic report write interrupted")
        original_once(path, value)

    monkeypatch.setattr(ReviewStore, "finish", finish)
    monkeypatch.setattr(campaign_provider, "_once", once)
    failed = asyncio.run(provider.execute(request))
    assert failed.outcome == ProviderOutcome.FAILED and fired
    assert coordinator.finish_attempt(lease, failed)
    assert coordinator.finalize_job(request.job_id) == JobStatus.FAILED
    parent_url = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{request.job_id}"
    parent_before = client.get(parent_url).json()
    source_url = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}"
    source_before = client.get(source_url).json()
    parent_files = inventory(detail_workspace(request.job_id))
    source_files = inventory(detail_workspace(source["id"]))
    assert len(calls) == 1
    premature = request_review(client, source)
    assert premature.status_code == 409, premature.text
    assert (
        premature.json()["error"]["code"]
        == "NATIVE_DETAIL_CAMPAIGN_SAVED_RESULT_AVAILABLE"
    )
    quote = client.get(parent_url + "/campaign-review/recovery-quote")
    assert quote.status_code == 200, quote.text
    value = quote.json()
    assert (
        value["model_requests"]
        == value["image_requests"]
        == value["reserve_micros"]
        == 0
    )
    body = {
        "original_result_journal_sha256": value["original_result_journal_sha256"],
        "quote_sha256": value["sha256"],
    }
    wrong = client.post(
        parent_url + "/campaign-review/recover",
        headers=idem(),
        json={**body, "original_result_journal_sha256": "a" * 64},
    )
    assert wrong.status_code == 409, wrong.text
    key = idem()
    queued = client.post(
        parent_url + "/campaign-review/recover", headers=key, json=body
    )
    assert queued.status_code == 202, queued.text
    recovery_id = queued.json()["id"]
    assert queued.json()["configuration"]["authorized_model_requests"] == 0
    assert queued.json()["configuration"]["original_review_job_id"] == request.job_id
    assert queued.json()["cost"]["estimated_max"]["micros"] == "0"
    assert (
        client.post(
            parent_url + "/campaign-review/recover", headers=key, json=body
        ).json()["id"]
        == recovery_id
    )
    active = client.post(
        parent_url + "/campaign-review/recover", headers=idem(), json=body
    )
    assert active.status_code == 409, active.text

    async def forbidden(*args, **kwargs):
        raise AssertionError("Saved-result recovery must never invoke a model")

    monkeypatch.setattr(gateway, "plan", forbidden)
    if boundary == "source_changed":
        path = detail_workspace(source["id"]) / "session.json"
        altered = json.loads(path.read_bytes())
        altered["plan"]["modules"][0]["intent_summary"] = (
            "Synthetic changed-after-recovery-request scope"
        )
        path.write_bytes(json.dumps(altered, ensure_ascii=False).encode("utf-8"))
        changed_source = inventory(detail_workspace(source["id"]))
        dispatch_pending(client)
        failed_child = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{recovery_id}"
        ).json()
        assert failed_child["status"] == "failed", failed_child
        assert failed_child["cost"]["actual"]["micros"] == "0"
        assert len(calls) == 1 and len(gateway.image_calls) == before_images
        assert inventory(detail_workspace(source["id"])) == changed_source
        assert inventory(detail_workspace(request.job_id)) == parent_files
        assert client.get(parent_url).json() == parent_before
        return
    dispatch_pending(client)
    recovered = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{recovery_id}"
    ).json()
    assert recovered["status"] == "completed", recovered
    assert (
        recovered["cost"]["actual"]["micros"] == "0"
        and recovered["api_request_count"] == 0
    )
    assert recovered["assets"] == [] and recovered["output_asset_count"] == 0
    report = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{recovery_id}/campaign-review/report"
    )
    assert report.status_code == 200, report.text
    assert report.json()["report"]["native_gate_accepted"] is True
    assert report.json()["report"]["original_review"]["job_id"] == request.job_id
    assert json.loads(report.json()["original_message"])["audit_complete"] is True
    assert len(calls) == 1 and len(gateway.image_calls) == before_images
    assert client.get(parent_url).json() == parent_before
    assert client.get(source_url).json() == source_before
    assert inventory(detail_workspace(request.job_id)) == parent_files
    assert inventory(detail_workspace(source["id"])) == source_files
    again = client.post(
        parent_url + "/campaign-review/recover", headers=idem(), json=body
    )
    assert again.status_code == 202 and again.json()["id"] == recovery_id, again.text
    with client.app.state.session_factory() as db:
        events = db.scalars(
            select(UsageEventRecord).where(
                UsageEventRecord.job_id.in_([request.job_id, recovery_id])
            )
        ).all()
        assert len(events) == 2 and sum(e.actual_cost_micros for e in events) == 17
