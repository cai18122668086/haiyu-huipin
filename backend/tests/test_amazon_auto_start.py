"""Default Amazon automatic planning and compatibility with manual history."""

import asyncio
import sys
from dataclasses import replace
from types import SimpleNamespace

from sqlalchemy import select

from app.amazon_auto_start import SUBMISSION_KEY
from app.amazon_visual_suite_provider import AMAZON_PLAN_CONFIGURATION_KEY
from app.models import AuditEventRecord, JobItemRecord, JobPlanRecord
from app.outbox import _dispatch
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_p0_amazon_fail_closed import (
    _create_amazon_job,
    _install_counting_runner,
)


def test_default_amazon_plan_is_confirmed_atomically_and_generates_21_once(client):
    gateway = _install_counting_runner(client)
    source = upload_asset(client)
    job = _create_amazon_job(client, source["id"], auto_start=None)
    assert job["configuration"]["settings"]["auto_start_after_plan"] is True
    assert job["configuration"][SUBMISSION_KEY]["actor_user_id"] == "usr_test"
    coordinator = client.app.state.task_runner.coordinator
    assert coordinator.prepare(job["id"]) == "planning"
    assert asyncio.run(coordinator.detail_planner.plan(job["id"])) == "processing"
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["plan"]["confirmed_at"]
    assert (
        current["plan"]["payload"]["controls"]["manual_confirmation_required"] is False
    )
    assert "confirm_plan" not in current["allowed_actions"]
    assert not gateway.image_calls
    with client.app.state.session_factory() as db:
        plan = db.get(JobPlanRecord, current["plan"]["id"])
        items = db.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == job["id"])
        ).all()
        assert len(items) == 21
        assert all(
            item.configuration[AMAZON_PLAN_CONFIGURATION_KEY]["resume_signature"]
            == plan.payload["resume_signature"]
            for item in items
        )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "completed", result
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == 21
    assert len(gateway.amazon_review_calls) == 1
    assert result["plan"]["payload"]["business_quality"]["status"] == "passed"
    asyncio.run(client.app.state.task_runner.run(job["id"]))
    assert len(gateway.image_calls) == 21
    with client.app.state.session_factory() as db:
        audits = db.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "amazon_visual_suite.plan_auto_confirmed",
                AuditEventRecord.resource_id == job["id"],
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].details["confirmation_source"] == "task_submission_default"


def test_manual_amazon_history_still_waits_and_uses_original_worker(
    client, monkeypatch
):
    gateway = _install_counting_runner(client)
    source = upload_asset(client)
    manual = _create_amazon_job(client, source["id"])
    automatic = _create_amazon_job(client, source["id"], auto_start=True)
    observed = []
    task = SimpleNamespace(apply_async=lambda **kwargs: observed.append(kwargs))
    monkeypatch.setitem(
        sys.modules,
        "app.tasks",
        SimpleNamespace(
            process_job=task,
            cancel_job_task=task,
        ),
    )
    settings = client.app.state.settings.model_copy(
        update={"task_dispatcher": "celery"}
    )
    runner = client.app.state.task_runner
    for job, event in [
        (manual, "job.dispatch_requested"),
        (automatic, "job.dispatch_requested"),
        (automatic, "job.cancel_requested"),
    ]:
        _dispatch(
            settings,
            runner,
            event_type=event,
            dedupe_key="offline",
            payload={"job_id": job["id"]},
        )
    assert "queue" not in observed[0]
    assert all(call["queue"] == "domestic_detail_v431" for call in observed[1:])
    coordinator = runner.coordinator
    coordinator.prepare(manual["id"])
    assert (
        asyncio.run(coordinator.detail_planner.plan(manual["id"]))
        == "awaiting_confirmation"
    )
    assert not gateway.image_calls


def test_invalid_automatic_amazon_plan_cannot_reach_image_provider(client, monkeypatch):
    gateway = _install_counting_runner(client)
    original = gateway.plan

    async def invalid(request):
        result = await original(request)
        result.payload["plan"]["screens"].pop()
        return replace(result, payload=result.payload)

    monkeypatch.setattr(gateway, "plan", invalid)
    source = upload_asset(client)
    job = _create_amazon_job(client, source["id"], auto_start=True)
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "failed"
    assert not result.get("plan") or result["plan"]["confirmed_at"] is None
    assert not gateway.image_calls


def test_cancelled_automatic_amazon_job_never_starts_generation(client):
    gateway = _install_counting_runner(client)
    source = upload_asset(client)
    job = _create_amazon_job(client, source["id"], auto_start=True)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/cancel",
        headers=idem(),
        json={"reason": "cancel before planning"},
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    assert not gateway.image_calls
