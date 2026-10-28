"""Usage estimates and historical accounting must never gate new work."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    UsageEventRecord,
    utc_now,
)
from app.provider_quota import require_provider_job_quota, reserve_provider_job_quota
from app.provider_quota_ledger import reconcile_and_total


def job_record(session, *, terminal=False):
    job = JobRecord(
        id="job_" + uuid4().hex,
        workspace_id="ws_test",
        type="reference_remix",
        status="completed" if terminal else "draft",
        expected_item_count=1,
        succeeded_item_count=int(terminal),
        failed_item_count=0,
        finished_at=utc_now() if terminal else None,
    )
    session.add(job)
    session.flush()
    return job


def record_estimate(session, job, *, cost=1, requests=1):
    reserve_provider_job_quota(
        session,
        job=job,
        actor_user_id="usr_test",
        estimated_cost_micros=cost,
        provider_request_count=requests,
        daily_limit_micros=1,
        monthly_limit_micros=1,
        daily_request_limit=1,
    )
    session.flush()


def seed_usage(session, job, requests, cost):
    now = utc_now()
    item = JobItemRecord(
        id="item_" + uuid4().hex,
        workspace_id="ws_test",
        job_id=job.id,
        item_key="first",
        ordinal=0,
        status="succeeded",
    )
    session.add(item)
    session.flush()
    attempt = JobAttemptRecord(
        id="attempt_" + uuid4().hex,
        workspace_id="ws_test",
        job_id=job.id,
        job_item_id=item.id,
        attempt_no=1,
        status="succeeded",
        lease_token="test",
        leased_by="test",
        lease_expires_at=now,
        provider="tengli",
        operation="reference_remix",
        provider_idempotency_key=job.id,
    )
    session.add(attempt)
    session.flush()
    session.add(
        UsageEventRecord(
            workspace_id="ws_test",
            job_id=job.id,
            job_item_id=item.id,
            attempt_id=attempt.id,
            provider="tengli",
            model="test",
            operation="reference_remix",
            usage_reported=False,
            raw_usage={"network_requests": requests},
            pricing_version="test",
            currency="USD",
            estimated_cost_micros=cost,
            actual_cost_micros=None,
        )
    )
    session.commit()


def reconcile(session):
    now = utc_now()
    return reconcile_and_total(
        session,
        workspace_id="ws_test",
        day_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        month_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    )


def test_recurring_limits_are_ignored_but_both_estimates_are_recorded(client):
    with client.app.state.session_factory() as session:
        for _ in range(2):
            record_estimate(session, job_record(session), cost=9_000_000, requests=99)
        events = session.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.action == "provider.beta_job_reserved"
            )
        ).all()
        assert len(events) == 2
        assert all(e.details["enforcement_mode"] == "observe_only" for e in events)
        assert all(e.details["daily_request_limit"] == 0 for e in events)
        assert all(e.details["daily_limit_micros"] == 0 for e in events)
        assert all(e.details["monthly_limit_micros"] == 0 for e in events)


@pytest.mark.parametrize(
    "requests,unit_cost", [(13, 176154), (66, 900000), (1, 13050001)]
)
def test_usage_above_estimates_settles_truthfully_and_does_not_block_next_job(
    client, requests, unit_cost
):
    with client.app.state.session_factory() as session:
        job = job_record(session, terminal=True)
        record_estimate(session, job, cost=1_400_000, requests=12)
        seed_usage(session, job, requests, unit_cost)
        expected = requests * unit_cost
        for _ in range(2):
            assert reconcile(session) == (expected, expected, requests)
            session.flush()
        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        assert settlement.details["charged_cost_micros"] == expected
        assert settlement.details["actual_provider_request_count"] == requests
        assert settlement.details["enforcement_mode"] == "observe_only"
        release = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_released",
            )
        )
        assert release.details["released_request_count"] >= 0
        assert release.details["released_cost_micros"] >= 0
        record_estimate(session, job_record(session))


def test_corrupt_historical_ledger_is_deferred_without_rolling_back_new_job(client):
    with client.app.state.session_factory() as session:
        old = job_record(session)
        record_estimate(session, old)
        event = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == old.id,
                AuditEventRecord.action == "provider.beta_job_reserved",
            )
        )
        session.add(
            AuditEventRecord(
                workspace_id="ws_test",
                action=event.action,
                resource_type="job",
                resource_id=old.id,
                request_id=old.id,
                details=dict(event.details),
            )
        )
        session.commit()
        new = job_record(session)
        record_estimate(session, new)
        session.commit()
        assert session.get(JobRecord, new.id) is not None
        pending = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == new.id,
                AuditEventRecord.action
                == "provider.beta_ledger_reconciliation_pending",
            )
        )
        assert pending is not None
        assert "duplicate" in pending.details["reason"]


def test_missing_estimate_is_a_record_not_an_execution_gate(client):
    with client.app.state.session_factory() as session:
        job = job_record(session)
        require_provider_job_quota(session, job)
        session.flush()
        assert (
            session.scalar(
                select(AuditEventRecord.id).where(
                    AuditEventRecord.resource_id == job.id,
                    AuditEventRecord.action == "provider.beta_job_estimate_missing",
                )
            )
            is not None
        )
