"""Durable provider usage estimates. Estimates never limit generation."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEventRecord, JobRecord, utc_now
from .provider_quota_ledger import (
    LEDGER_VERSION,
    ProviderQuotaLedgerError,
    reconcile_and_total,
)
from .services import lock_workspace_limits, quota_enabled

PROVIDER_JOB_QUOTA_ACTION = "provider.beta_job_reserved"
_LOGGER = logging.getLogger(__name__)


def _estimate(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def reserve_provider_job_quota(
    session: Session,
    *,
    job: JobRecord,
    actor_user_id: str,
    estimated_cost_micros: int,
    provider_request_count: int,
    daily_limit_micros: int,
    monthly_limit_micros: int,
    daily_request_limit: int,
    planner_reserve_micros: int | None = None,
    image_budget_micros: int | None = None,
    group_count: int | None = None,
) -> None:
    """Record an advisory estimate; preserve the legacy caller signature.

    Daily/monthly/request limits are retired. A prior ledger inconsistency is
    recorded for accounting review in its own savepoint, never surfaced as a
    submission/retry failure. Idempotent dispatch and provider usage remain
    independently recorded by the existing job/attempt execution protocol.
    """
    if not quota_enabled():
        return
    lock_workspace_limits(session, job.workspace_id)
    now = utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    try:
        with session.begin_nested():
            reconcile_and_total(
                session,
                workspace_id=job.workspace_id,
                day_start=datetime(now.year, now.month, now.day, tzinfo=UTC),
                month_start=datetime(now.year, now.month, 1, tzinfo=UTC),
            )
    except ProviderQuotaLedgerError as exc:
        _LOGGER.warning(
            "Provider usage reconciliation deferred for job %s: %s", job.id, exc
        )
        session.add(
            AuditEventRecord(
                workspace_id=job.workspace_id,
                actor_user_id=actor_user_id,
                action="provider.beta_ledger_reconciliation_pending",
                resource_type="job",
                resource_id=job.id,
                request_id=job.id,
                details={"enforcement_mode": "observe_only", "reason": str(exc)},
            )
        )
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            actor_user_id=actor_user_id,
            action=PROVIDER_JOB_QUOTA_ACTION,
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details={
                "ledger_version": LEDGER_VERSION,
                "enforcement_mode": "observe_only",
                "job_type": job.type,
                "planner_reserve_micros": planner_reserve_micros,
                "image_budget_micros": image_budget_micros,
                "group_count": group_count,
                "estimated_cost_micros": _estimate(estimated_cost_micros),
                "provider_request_count": _estimate(provider_request_count),
                "estimate_available": type(estimated_cost_micros) is int
                and estimated_cost_micros >= 0,
                "daily_limit_micros": 0,
                "monthly_limit_micros": 0,
                "daily_request_limit": 0,
            },
        )
    )


def require_provider_job_quota(session: Session, job: JobRecord) -> None:
    """Compatibility hook: a missing estimate is recorded, never an execution gate."""
    if not quota_enabled():
        return
    reservation = session.scalar(
        select(AuditEventRecord.id)
        .where(
            AuditEventRecord.workspace_id == job.workspace_id,
            AuditEventRecord.action == PROVIDER_JOB_QUOTA_ACTION,
            AuditEventRecord.resource_id == job.id,
            AuditEventRecord.request_id == job.id,
        )
        .limit(1)
    )
    if reservation is None:
        session.add(
            AuditEventRecord(
                workspace_id=job.workspace_id,
                action="provider.beta_job_estimate_missing",
                resource_type="job",
                resource_id=job.id,
                request_id=job.id,
                details={"enforcement_mode": "observe_only"},
            )
        )
