"""Only fast main-image planning extends the original quota settlement paths."""

from datetime import UTC

import pytest
from sqlalchemy import select

from app.models import (
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    UsageEventRecord,
    utc_now,
)
from app.provider_quota_ledger import (
    ATTEMPTS_ACTION,
    RELEASE_ACTION,
    SETTLEMENT_ACTION,
    reconcile_and_total,
)
from app.services import canonical_hash, new_id

from .test_replica import upload

PLANNER_RESERVE = 250_000


def seed(
    client, *, plan_usage=None, receipt=None, speed="native_fast", status="failed"
):
    upload(client, "red")
    now = utc_now()
    with client.app.state.session_factory() as session:
        job = JobRecord(
            id=new_id("job"),
            workspace_id="ws_test",
            type="domestic_main_image",
            status=status,
            configuration={
                "settings": {"main_image_speed": speed},
                **({"_main_image_fast_planning": receipt} if receipt else {}),
            },
            expected_item_count=6,
            succeeded_item_count=6 if status == "completed" else 0,
            finished_at=now,
        )
        session.add(job)
        session.flush()
        if plan_usage is not None:
            payload = {"usage": {"planner": plan_usage}}
            session.add(
                JobPlanRecord(
                    id=new_id("plan"),
                    workspace_id="ws_test",
                    job_id=job.id,
                    revision=1,
                    schema_version="aurora-main-image-native-fast-plan-v1",
                    payload=payload,
                    payload_hash=canonical_hash(payload),
                    validation_status="valid",
                )
            )
        session.add(
            AuditEventRecord(
                workspace_id="ws_test",
                action="provider.beta_job_reserved",
                resource_type="job",
                resource_id=job.id,
                request_id=job.id,
                details={
                    "ledger_version": 2,
                    "job_type": "domestic_main_image",
                    "estimated_cost_micros": 6 * 90_000 + PLANNER_RESERVE,
                    "provider_request_count": 7,
                    "group_count": 1,
                    "image_budget_micros": 6 * 90_000,
                    "planner_reserve_micros": PLANNER_RESERVE,
                },
            )
        )
        session.commit()
        return job.id


def reconcile(client, job_id):
    now = utc_now().astimezone(UTC)
    with client.app.state.session_factory() as session:
        totals = []
        for _ in range(2):
            totals.append(
                reconcile_and_total(
                    session,
                    workspace_id="ws_test",
                    day_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                    month_start=now.replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0
                    ),
                )
            )
            session.flush()
        assert totals[0] == totals[1]
        events = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action.in_(
                    [SETTLEMENT_ACTION, RELEASE_ACTION, ATTEMPTS_ACTION]
                ),
            )
        ).all()
        assert len(events) == 3
        return totals[0], {event.action: event.details for event in events}


@pytest.mark.parametrize(
    "actual,expected,basis",
    [
        (None, PLANNER_RESERVE, "estimated_fallback"),
        (1234, 1234, "actual"),
        (0, 0, "actual"),
    ],
)
def test_fast_main_image_successful_plan_is_charged_once(
    client, actual, expected, basis
):
    job_id = seed(client, plan_usage=[{"actual_cost_micros": actual}])
    totals, events = reconcile(client, job_id)
    assert totals == (expected, expected, 1)
    attempt = events[ATTEMPTS_ACTION]["attempts"][0]
    assert attempt["source"] == "planner" and attempt["cost_basis"] == basis
    assert events[RELEASE_ACTION]["released_request_count"] == 6
    with client.app.state.session_factory() as session:
        # A fallback budget is never written as the provider's actual cost.
        assert session.get(JobRecord, job_id).actual_cost_micros is None


@pytest.mark.parametrize(
    "receipt",
    [
        {"state": "invoked"},
        {
            "state": "returned",
            "actual_cost_micros": None,
            "error_code": "PLANNER_OUTPUT_TRUNCATED",
        },
        {"state": "unknown", "actual_cost_micros": None},
    ],
)
def test_missing_plan_counts_persisted_planner_receipt_conservatively(client, receipt):
    job_id = seed(client, receipt=receipt)
    totals, events = reconcile(client, job_id)
    assert totals == (PLANNER_RESERVE, PLANNER_RESERVE, 1)
    attempt = events[ATTEMPTS_ACTION]["attempts"][0]
    assert attempt["cost_basis"] == "conservative_unknown"
    assert attempt["actual_cost_reported"] is False
    assert events[SETTLEMENT_ACTION]["conservative_unknown_count"] == 1


def test_failed_plan_preserves_reported_cost_without_fake_estimate(client):
    job_id = seed(client, receipt={"state": "returned", "actual_cost_micros": 876})
    totals, events = reconcile(client, job_id)
    assert totals == (876, 876, 1)
    assert events[ATTEMPTS_ACTION]["attempts"][0]["cost_basis"] == "actual"


def test_fast_preflight_never_submitted_is_zero_calls(client):
    job_id = seed(client, receipt={"state": "preparing"})
    totals, events = reconcile(client, job_id)
    assert totals == (0, 0, 0)
    assert events[RELEASE_ACTION]["released_request_count"] == 7
    assert not events[ATTEMPTS_ACTION]["attempts"]


def test_retry_reuses_plan_without_charging_the_original_planner_again(client):
    job_id = seed(
        client, plan_usage=[], receipt={"state": "returned", "actual_cost_micros": 876}
    )
    totals, events = reconcile(client, job_id)
    assert totals == (0, 0, 0)
    assert not events[ATTEMPTS_ACTION]["attempts"]


def test_six_images_plus_one_planner_settle_seven_real_calls(client):
    job_id = seed(client, plan_usage=[{"actual_cost_micros": 1234}], status="completed")
    with client.app.state.session_factory() as session:
        for ordinal in range(6):
            item = JobItemRecord(
                id=new_id("item"),
                workspace_id="ws_test",
                job_id=job_id,
                item_key=f"main-{ordinal}",
                ordinal=ordinal,
                status="succeeded",
            )
            session.add(item)
            session.flush()
            attempt = JobAttemptRecord(
                id=new_id("attempt"),
                workspace_id="ws_test",
                job_id=job_id,
                job_item_id=item.id,
                attempt_no=1,
                status="succeeded",
                lease_token="t",
                leased_by="test",
                lease_expires_at=utc_now(),
                provider="tengli",
                operation="domestic_main_image",
                provider_idempotency_key=item.id,
            )
            session.add(attempt)
            session.flush()
            session.add(
                UsageEventRecord(
                    workspace_id="ws_test",
                    job_id=job_id,
                    job_item_id=item.id,
                    attempt_id=attempt.id,
                    provider="tengli",
                    model="gpt-image-2",
                    operation="domestic_main_image",
                    input_image_count=1,
                    output_image_count=1,
                    quality="high",
                    width=1024,
                    height=1024,
                    usage_reported=True,
                    raw_usage={"network_requests": 1},
                    pricing_version="test",
                    currency="USD",
                    actual_cost_micros=90_000,
                )
            )
        session.commit()
    totals, events = reconcile(client, job_id)
    assert totals == (541_234, 541_234, 7)
    assert len(events[ATTEMPTS_ACTION]["attempts"]) == 7
    assert events[RELEASE_ACTION]["released_request_count"] == 0


def test_cancel_before_planner_and_standard_main_image_ledger_stay_unchanged(client):
    job_id = seed(client, status="cancelled")
    totals, _ = reconcile(client, job_id)
    assert totals == (0, 0, 0)
    job_id = seed(client, speed="standard", receipt={"state": "invoked"})
    totals, events = reconcile(client, job_id)
    assert totals == (0, 0, 0)
    assert not events[ATTEMPTS_ACTION]["attempts"]
