from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import (
    AttemptStatus,
    JobItemStatus,
    JobStatus,
    JobType,
    UsageCompleteness,
)
from app.models import (
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    UsageEventRecord,
    utc_now,
)
from app.provider_quota_ledger import (
    ProviderQuotaLedgerError,
    reconcile_and_total,
)
from app.services import new_id

WORKSPACE_ID = "ws_test"
IMAGE_REQUEST_COUNT = 21
IMAGE_UNIT_COST_MICROS = 90_000
IMAGE_BUDGET_MICROS = IMAGE_REQUEST_COUNT * IMAGE_UNIT_COST_MICROS
PLANNER_RESERVE_MICROS = 250_000
LEGACY_RESERVATION_OCCURRED_AT = datetime(2026, 8, 4, 7, 11, tzinfo=UTC)
MODERN_RESERVATION_OCCURRED_AT = datetime(2026, 8, 4, 7, 13, tzinfo=UTC)


@pytest.mark.parametrize("charge,requests", [(545000, 3), (680001, 3), (545000, 5)])
def test_masked_usage_above_estimates_settles_truthfully(
    client, charge, requests,
):
    now = utc_now()
    with client.app.state.session_factory() as session:
        job = JobRecord(
            id=new_id("job"), workspace_id=WORKSPACE_ID, type="local_edit",
            status="failed", stage="failed", expected_item_count=1,
            succeeded_item_count=0, failed_item_count=1, finished_at=now,
        )
        session.add(job)
        session.flush()
        item = JobItemRecord(
            id=new_id("item"), workspace_id=WORKSPACE_ID, job_id=job.id,
            item_key="edit", ordinal=0, status="failed", stage="failed",
        )
        session.add(item)
        session.flush()
        attempt = JobAttemptRecord(
            id=new_id("attempt"), workspace_id=WORKSPACE_ID, job_id=job.id,
            job_item_id=item.id, attempt_no=1, status="failed", lease_token="t",
            leased_by="test", lease_expires_at=now, provider="tengli",
            operation="local_edit", provider_idempotency_key=job.id,
        )
        session.add(attempt)
        session.flush()
        session.add(UsageEventRecord(
            workspace_id=WORKSPACE_ID, job_id=job.id, job_item_id=item.id,
            attempt_id=attempt.id, provider="tengli", model="gpt-image-2",
            operation="local_edit", input_image_count=1, output_image_count=1,
            quality="high", width=1440, height=1920, usage_reported=False,
            raw_usage={"network_requests": requests}, pricing_version="test",
            currency="USD", estimated_cost_micros=charge, occurred_at=now,
        ))
        reservation = AuditEventRecord(
            workspace_id=WORKSPACE_ID, action="provider.beta_job_reserved",
            resource_type="job", resource_id=job.id, request_id=job.id,
            details={"ledger_version": 2, "job_type": "local_edit",
                "estimated_cost_micros": 430000, "provider_request_count": 4,
                "image_budget_micros": 90000, "planner_reserve_micros": 250000},
        )
        session.add(reservation)
        session.commit()
        day, month = _period_starts(now)
        for _ in range(2):
            assert reconcile_and_total(session, workspace_id=WORKSPACE_ID,
                day_start=day, month_start=month) == (charge, charge, requests)
            session.flush()
        settlement = session.scalar(select(AuditEventRecord).where(
            AuditEventRecord.resource_id == job.id,
            AuditEventRecord.action == "provider.beta_job_settled"))
        assert settlement.details["charged_cost_micros"] == charge
        assert settlement.details[
            "observed_cost_over_estimate_micros"
        ] == charge - 430000
        assert reservation.details["estimated_cost_micros"] == 430000


def _seed_terminal_amazon_job_with_image_usage(
    session: Session,
    *,
    modern_reservation: bool,
    usage_event_count: int = IMAGE_REQUEST_COUNT,
    doubled_network_request_ordinal: int | None = None,
    plan_reuse_retry: bool = False,
) -> JobRecord:
    now = utc_now()
    reservation_occurred_at = (
        MODERN_RESERVATION_OCCURRED_AT
        if modern_reservation
        else LEGACY_RESERVATION_OCCURRED_AT
    )
    planner_required = int(modern_reservation and not plan_reuse_retry)
    retry_source_id: str | None = None
    if plan_reuse_retry:
        retry_source = JobRecord(
            id=new_id("job"),
            workspace_id=WORKSPACE_ID,
            type=JobType.AMAZON_VISUAL_SUITE.value,
            status=JobStatus.COMPLETED.value,
            stage="completed",
            progress=100,
            expected_item_count=IMAGE_REQUEST_COUNT,
            succeeded_item_count=IMAGE_REQUEST_COUNT,
            failed_item_count=0,
            usage_completeness=UsageCompleteness.COMPLETE.value,
            api_request_count=IMAGE_REQUEST_COUNT,
            started_at=now,
            finished_at=now,
        )
        session.add(retry_source)
        session.flush()
        retry_source_id = retry_source.id

    job = JobRecord(
        id=new_id("job"),
        workspace_id=WORKSPACE_ID,
        type=JobType.AMAZON_VISUAL_SUITE.value,
        status=JobStatus.COMPLETED.value,
        stage="completed",
        progress=100,
        root_job_id=retry_source_id,
        retry_of_job_id=retry_source_id,
        retry_scope="all" if plan_reuse_retry else None,
        expected_item_count=IMAGE_REQUEST_COUNT,
        succeeded_item_count=IMAGE_REQUEST_COUNT,
        failed_item_count=0,
        estimated_cost_min_micros=IMAGE_BUDGET_MICROS,
        estimated_cost_max_micros=IMAGE_BUDGET_MICROS,
        actual_cost_micros=None,
        currency="USD",
        usage_completeness=UsageCompleteness.COMPLETE.value,
        api_request_count=IMAGE_REQUEST_COUNT,
        started_at=now,
        finished_at=now,
    )
    session.add(job)
    session.flush()

    for ordinal in range(IMAGE_REQUEST_COUNT):
        item = JobItemRecord(
            id=new_id("item"),
            workspace_id=WORKSPACE_ID,
            job_id=job.id,
            item_key=f"amazon-{ordinal + 1:02d}",
            ordinal=ordinal,
            status=JobItemStatus.SUCCEEDED.value,
            stage="completed",
            attempt_count=1,
            started_at=now,
            finished_at=now,
        )
        session.add(item)
        session.flush()

        attempt = JobAttemptRecord(
            id=new_id("attempt"),
            workspace_id=WORKSPACE_ID,
            job_id=job.id,
            job_item_id=item.id,
            attempt_no=1,
            status=AttemptStatus.SUCCEEDED.value,
            lease_token=f"lease-{ordinal}",
            leased_by="legacy-worker",
            lease_expires_at=now + timedelta(minutes=5),
            provider="tengli",
            operation=JobType.AMAZON_VISUAL_SUITE.value,
            provider_request_id=f"provider-request-{job.id}-{ordinal}",
            provider_idempotency_key=f"provider-idempotency-{job.id}-{ordinal}",
            started_at=now,
            finished_at=now,
        )
        session.add(attempt)
        session.flush()

        if ordinal < usage_event_count:
            raw_usage = (
                {"network_requests": 2}
                if ordinal == doubled_network_request_ordinal
                else {}
            )
            session.add(
                UsageEventRecord(
                    workspace_id=WORKSPACE_ID,
                    job_id=job.id,
                    job_item_id=item.id,
                    attempt_id=attempt.id,
                    provider="tengli",
                    model="gpt-image-2",
                    operation=JobType.AMAZON_VISUAL_SUITE.value,
                    provider_request_id=attempt.provider_request_id,
                    input_image_count=1,
                    output_image_count=1,
                    quality="high",
                    width=1600,
                    height=1600,
                    usage_reported=False,
                    raw_usage=raw_usage,
                    pricing_version="test-image-v1",
                    currency="USD",
                    estimated_cost_micros=IMAGE_UNIT_COST_MICROS,
                    actual_cost_micros=None,
                    occurred_at=now,
                )
            )

    session.add(
        AuditEventRecord(
            workspace_id=WORKSPACE_ID,
            action="provider.beta_job_reserved",
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details={
                "ledger_version": 2,
                "job_type": JobType.AMAZON_VISUAL_SUITE.value,
                "planner_reserve_micros": PLANNER_RESERVE_MICROS,
                "image_budget_micros": IMAGE_BUDGET_MICROS,
                "group_count": planner_required,
                "estimated_cost_micros": (
                    IMAGE_BUDGET_MICROS
                    + planner_required * PLANNER_RESERVE_MICROS
                ),
                "provider_request_count": IMAGE_REQUEST_COUNT + planner_required,
                "daily_limit_micros": 5_000_000,
                "monthly_limit_micros": 20_000_000,
                "daily_request_limit": 100,
            },
            occurred_at=reservation_occurred_at,
        )
    )
    session.commit()
    return job


def _period_starts(reference: datetime | None = None) -> tuple[datetime, datetime]:
    now = reference or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (
        datetime(now.year, now.month, now.day, tzinfo=UTC),
        datetime(now.year, now.month, 1, tzinfo=UTC),
    )


def test_legacy_pre_planner_amazon_usage_settles_without_plan(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        job = _seed_terminal_amazon_job_with_image_usage(
            session,
            modern_reservation=False,
        )
        day_start, month_start = _period_starts(LEGACY_RESERVATION_OCCURRED_AT)

        totals = reconcile_and_total(
            session,
            workspace_id=WORKSPACE_ID,
            day_start=day_start,
            month_start=month_start,
        )

        assert totals == (
            IMAGE_BUDGET_MICROS,
            IMAGE_BUDGET_MICROS,
            IMAGE_REQUEST_COUNT,
        )
        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        assert settlement is not None
        assert settlement.details["charged_cost_micros"] == IMAGE_BUDGET_MICROS
        assert (
            settlement.details["actual_provider_request_count"]
            == IMAGE_REQUEST_COUNT
        )
        assert settlement.details["estimated_fallback_count"] == IMAGE_REQUEST_COUNT


def test_modern_amazon_reservation_without_plan_fails_closed(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        _seed_terminal_amazon_job_with_image_usage(
            session,
            modern_reservation=True,
        )
        day_start, month_start = _period_starts(MODERN_RESERVATION_OCCURRED_AT)

        with pytest.raises(
            ProviderQuotaLedgerError,
            match="no provider-attempt evidence",
        ):
            reconcile_and_total(
                session,
                workspace_id=WORKSPACE_ID,
                day_start=day_start,
                month_start=month_start,
            )


def test_legacy_pre_planner_amazon_incomplete_usage_fails_without_settlement(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        job = _seed_terminal_amazon_job_with_image_usage(
            session,
            modern_reservation=False,
            usage_event_count=IMAGE_REQUEST_COUNT - 1,
        )
        day_start, month_start = _period_starts(LEGACY_RESERVATION_OCCURRED_AT)

        with pytest.raises(ProviderQuotaLedgerError):
            reconcile_and_total(
                session,
                workspace_id=WORKSPACE_ID,
                day_start=day_start,
                month_start=month_start,
            )

        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        assert settlement is None


def test_legacy_pre_planner_amazon_excess_usage_fails_without_settlement(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        job = _seed_terminal_amazon_job_with_image_usage(
            session,
            modern_reservation=False,
            doubled_network_request_ordinal=0,
        )
        day_start, month_start = _period_starts(LEGACY_RESERVATION_OCCURRED_AT)

        with pytest.raises(ProviderQuotaLedgerError):
            reconcile_and_total(
                session,
                workspace_id=WORKSPACE_ID,
                day_start=day_start,
                month_start=month_start,
            )

        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        assert settlement is None


def test_modern_plan_reuse_retry_without_plan_fails_closed(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        job = _seed_terminal_amazon_job_with_image_usage(
            session,
            modern_reservation=True,
            plan_reuse_retry=True,
        )
        assert job.root_job_id is not None
        assert job.retry_of_job_id is not None
        day_start, month_start = _period_starts(MODERN_RESERVATION_OCCURRED_AT)

        with pytest.raises(
            ProviderQuotaLedgerError,
            match="no provider-attempt evidence",
        ):
            reconcile_and_total(
                session,
                workspace_id=WORKSPACE_ID,
                day_start=day_start,
                month_start=month_start,
            )

        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        assert settlement is None
