"""Database-backed job execution, leases, heartbeats, and fencing."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import socket
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import provider_quota_ledger as quota_ledger
from . import refined_detail
from .aurora_execution_modes import (
    is_calibrated_parallel_aurora_mode,
    is_parallel_aurora_mode,
    is_v20_aurora_mode,
)
from .config import (
    Settings,
    default_masked_skill_root,
    default_skill_root,
    default_xinghe_skill_root,
)
from .domain import (
    TERMINAL_JOB_STATUSES,
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    AttemptStatus,
    JobAssetDirection,
    JobItemStatus,
    JobStatus,
    JobType,
    PlanValidationStatus,
    UsageCompleteness,
)
from .models import (
    AmazonSuiteReviewAttemptRecord,
    AssetRecord,
    AuditEventRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    UsageEventRecord,
    WorkspaceLimitRecord,
    utc_now,
)
from .provider import (
    PersistedProviderArtifact,
    Provider,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from .provider_quota_ledger import ProviderQuotaLedgerError, usage_request_count
from .services import new_id, settle_credits, transition_job
from .skill_runtime import build_fake_skill_runtime_provider
from .storage import ImageStorage, ValidatedImage

logger = logging.getLogger(__name__)


class DetailPlanner(Protocol):
    async def plan(self, job_id: str) -> JobStatus | None: ...


class AmazonSuiteReviewer(Protocol):
    async def review(self, job_id: str) -> Any: ...


ACTIVE_ATTEMPT_STATUSES = {
    AttemptStatus.CLAIMED.value,
    AttemptStatus.RUNNING.value,
}
TERMINAL_ITEM_STATUSES = {
    JobItemStatus.SUCCEEDED.value,
    JobItemStatus.FAILED.value,
    JobItemStatus.CANCELLED.value,
    JobItemStatus.SKIPPED.value,
    JobItemStatus.REUSED.value,
}
SUCCESS_ITEM_STATUSES = {
    JobItemStatus.SUCCEEDED.value,
    JobItemStatus.REUSED.value,
}
AMAZON_REVIEW_ACCOUNTING_STATUSES = {
    AttemptStatus.SUCCEEDED.value,
    AttemptStatus.FAILED.value,
    AttemptStatus.OUTCOME_UNKNOWN.value,
}


@dataclass(frozen=True)
class _AmazonReviewCall:
    review_round: int
    attempt_identity: str
    provider: str
    provider_request_id: str | None
    status: str
    reported: bool
    actual_cost_micros: int | None
    estimated_cost_micros: int | None
    provider_call_started: bool
    source: str
    durable_attempt_id: str | None = None
    network_requests: int = 1


@dataclass(frozen=True)
class _AmazonReviewAccounting:
    request_count: int = 0
    reported_count: int = 0
    known_actual_cost_count: int = 0
    actual_cost_micros: int = 0
    real_request_count: int = 0


def _valid_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_int(value: object) -> int | None:
    normalized = _valid_nonnegative_int(value)
    return normalized if normalized is not None and normalized > 0 else None


def _nonempty_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _legacy_review_round(record: Mapping[str, Any], ordinal: int) -> int:
    return _positive_int(record.get("review_round")) or ordinal


def _legacy_review_identities(record: Mapping[str, Any]) -> set[str]:
    identities: set[str] = set()
    for key in ("attempt_id", "provider_idempotency_key", "provider_request_id"):
        value = _nonempty_string(record.get(key))
        if value is not None:
            identities.add(value)
    return identities


def _sorted_amazon_review_calls(
    calls: list[_AmazonReviewCall],
) -> tuple[_AmazonReviewCall, ...]:
    return tuple(
        sorted(
            calls,
            key=lambda call: (
                call.review_round,
                0 if call.source == "durable" else 1,
                call.attempt_identity,
            ),
        )
    )


def _amazon_review_calls(
    session: Session,
    job: JobRecord,
    plan_payload: Mapping[str, Any] | None,
) -> tuple[_AmazonReviewCall, ...]:
    """Merge durable and legacy suite-review evidence without double counting."""

    if job.type != JobType.AMAZON_VISUAL_SUITE.value:
        return ()

    attempts = session.scalars(
        select(AmazonSuiteReviewAttemptRecord)
        .where(AmazonSuiteReviewAttemptRecord.job_id == job.id)
        .order_by(
            AmazonSuiteReviewAttemptRecord.review_round,
            AmazonSuiteReviewAttemptRecord.id,
        )
    ).all()
    durable_rounds = {attempt.review_round for attempt in attempts}
    durable_identities: set[str] = set()
    calls: list[_AmazonReviewCall] = []
    for attempt in attempts:
        identities = {attempt.id, attempt.provider_idempotency_key}
        request_id = _nonempty_string(attempt.provider_request_id)
        if request_id is not None:
            identities.add(request_id)
        durable_identities.update(identities)
        if attempt.status not in AMAZON_REVIEW_ACCOUNTING_STATUSES:
            continue
        normalized = (
            attempt.normalized_usage
            if isinstance(attempt.normalized_usage, Mapping)
            else {}
        )
        calls.append(
            _AmazonReviewCall(
                review_round=attempt.review_round,
                attempt_identity=attempt.provider_idempotency_key,
                provider=attempt.provider,
                provider_request_id=request_id,
                status=attempt.status,
                reported=normalized.get("reported") is True,
                actual_cost_micros=_valid_nonnegative_int(attempt.actual_cost_micros),
                estimated_cost_micros=_valid_nonnegative_int(
                    attempt.estimated_cost_micros
                ),
                provider_call_started=attempt.provider_call_started_at is not None,
                source="durable",
                durable_attempt_id=attempt.id,
                network_requests=_positive_int(normalized.get("network_requests")) or 1,
            )
        )

    # Pre-migration jobs stored review usage only in the plan payload. Durable
    # evidence wins by review round or provider-attempt identity, while legacy
    # rounds with no durable counterpart remain billable and observable.
    usage = plan_payload.get("usage") if isinstance(plan_payload, Mapping) else None
    records = usage.get("amazon_suite_review") if isinstance(usage, Mapping) else None
    if not isinstance(records, list):
        return _sorted_amazon_review_calls(calls)
    for ordinal, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            continue
        review_round = _legacy_review_round(record, ordinal)
        identities = _legacy_review_identities(record)
        if review_round in durable_rounds or identities & durable_identities:
            continue
        provider_request_id = _nonempty_string(record.get("provider_request_id"))
        provider = _nonempty_string(record.get("provider")) or (
            "legacy" if provider_request_id is not None else "mock"
        )
        explicitly_started = record.get("provider_call_started") is True or (
            record.get("provider_call_started_at") is not None
        )
        provider_call_started = explicitly_started or provider_request_id is not None
        identity = next(iter(sorted(identities)), f"legacy-round-{review_round}")
        calls.append(
            _AmazonReviewCall(
                review_round=review_round,
                attempt_identity=identity,
                provider=provider,
                provider_request_id=provider_request_id,
                status=str(record.get("status") or AttemptStatus.SUCCEEDED.value),
                reported=record.get("reported") is True,
                actual_cost_micros=_valid_nonnegative_int(
                    record.get("actual_cost_micros")
                ),
                estimated_cost_micros=_valid_nonnegative_int(
                    record.get("estimated_cost_micros")
                ),
                provider_call_started=provider_call_started,
                source="legacy",
            )
        )
    return _sorted_amazon_review_calls(calls)


def _amazon_review_accounting(
    calls: tuple[_AmazonReviewCall, ...],
) -> _AmazonReviewAccounting:
    called = [call for call in calls if call.provider_call_started]
    reported_count = sum(call.network_requests for call in called if call.reported)
    known_costs = [
        call.actual_cost_micros
        for call in called
        if call.actual_cost_micros is not None
    ]
    return _AmazonReviewAccounting(
        request_count=sum(call.network_requests for call in called),
        reported_count=reported_count,
        known_actual_cost_count=sum(
            call.network_requests for call in called
            if call.actual_cost_micros is not None
        ),
        actual_cost_micros=sum(known_costs),
        real_request_count=sum(
            call.network_requests for call in called if call.provider != "mock"
        ),
    )


def _review_charge(
    call: _AmazonReviewCall,
    fallback_reserve_micros: int,
) -> tuple[int, str]:
    actual_cost = call.actual_cost_micros
    if actual_cost is not None:
        return actual_cost, "actual"
    estimated_cost = call.estimated_cost_micros
    if estimated_cost is not None:
        basis = (
            "conservative_unknown"
            if call.status == AttemptStatus.OUTCOME_UNKNOWN.value
            else "estimated_fallback"
        )
        return estimated_cost, basis
    return fallback_reserve_micros, "conservative_unknown"


def _settle_amazon_provider_quota(
    session: Session,
    job: JobRecord,
    review_calls: tuple[_AmazonReviewCall, ...],
) -> None:
    """Settle Amazon reservations with durable/legacy suite-review attempts."""

    if job.type != JobType.AMAZON_VISUAL_SUITE.value:
        return
    reservations = session.scalars(
        select(AuditEventRecord)
        .where(
            AuditEventRecord.workspace_id == job.workspace_id,
            AuditEventRecord.action == quota_ledger.RESERVATION_ACTION,
            AuditEventRecord.resource_id == job.id,
            AuditEventRecord.request_id == job.id,
        )
        .order_by(AuditEventRecord.id)
        .with_for_update()
    ).all()
    if not reservations:
        return
    if len(reservations) != 1:
        raise ProviderQuotaLedgerError("A job has duplicate quota reservations.")
    reservation = reservations[0]
    if quota_ledger._settlement_for_reservation(session, reservation) is not None:
        return

    reserved_cost, reserved_requests = quota_ledger._reservation_values(reservation)
    usage_events = session.scalars(
        select(UsageEventRecord)
        .where(
            UsageEventRecord.workspace_id == job.workspace_id,
            UsageEventRecord.job_id == job.id,
            UsageEventRecord.provider != "mock",
        )
        .order_by(UsageEventRecord.id)
    ).all()
    requests = 0
    charge = 0
    actual_cost_count = 0
    fallback_count = 0
    sources: list[dict[str, Any]] = []
    for usage_event in usage_events:
        event_requests = usage_request_count(usage_event)
        event_charge, basis = quota_ledger._usage_charge(
            job,
            usage_event,
            event_requests,
        )
        requests += event_requests
        charge += event_charge
        actual_cost_count += int(basis == "actual")
        fallback_count += int(basis != "actual")
        sources.append(
            {
                "source": "usage_event",
                "usage_event_id": usage_event.id,
                "attempt_id": usage_event.attempt_id,
                "provider_request_count": event_requests,
                "charged_cost_micros": event_charge,
                "cost_basis": basis,
                "actual_cost_reported": usage_event.actual_cost_micros is not None,
            }
        )

    planner_sources, planner_requests, planner_charge, planner_actual = (
        quota_ledger._planner_charges(
            session,
            job,
            reservation,
            reserved_cost,
            reserved_requests,
            requests,
            usage_events,
        )
    )
    sources.extend(planner_sources)
    requests += planner_requests
    charge += planner_charge
    actual_cost_count += planner_actual
    fallback_count += planner_requests - planner_actual

    reservation_details = (
        reservation.details if isinstance(reservation.details, Mapping) else {}
    )
    review_reserve = _positive_int(reservation_details.get("planner_reserve_micros"))
    if review_reserve is None:
        raise ProviderQuotaLedgerError(
            "Amazon suite review reserve cannot be derived safely."
        )
    for call in review_calls:
        if not call.provider_call_started or call.provider == "mock":
            continue
        review_charge, basis = _review_charge(call, review_reserve)
        requests += call.network_requests
        charge += review_charge
        actual_cost_count += int(basis == "actual")
        fallback_count += int(basis != "actual")
        sources.append(
            {
                "source": f"amazon_suite_review_{call.source}",
                "review_round": call.review_round,
                "attempt_identity": call.attempt_identity,
                "durable_attempt_id": call.durable_attempt_id,
                "provider_request_id": call.provider_request_id,
                "provider_request_count": call.network_requests,
                "charged_cost_micros": review_charge,
                "cost_basis": basis,
                "actual_cost_reported": basis == "actual",
            }
        )

    conservative_unknown_count = sum(
        source["cost_basis"] == "conservative_unknown" for source in sources
    )
    basis = (
        "mixed_conservative_unknown"
        if conservative_unknown_count and actual_cost_count
        else "conservative_unknown"
        if conservative_unknown_count
        else "actual"
        if requests and fallback_count == 0
        else "mixed"
        if actual_cost_count
        else "estimated_fallback"
    )
    common = {
        "enforcement_mode": "observe_only",
        "ledger_version": quota_ledger.LEDGER_VERSION,
        "reservation_event_id": reservation.id,
        "reserved_cost_micros": reserved_cost,
        "reserved_request_count": reserved_requests,
        "charged_cost_micros": charge,
        "actual_provider_request_count": requests,
        "cost_basis": basis,
        "actual_cost_reported_count": actual_cost_count,
        "estimated_fallback_count": fallback_count,
        "conservative_unknown_count": conservative_unknown_count,
    }
    quota_ledger._add_event(
        session,
        job,
        quota_ledger.ATTEMPTS_ACTION,
        {**common, "attempts": sources},
    )
    quota_ledger._add_event(session, job, quota_ledger.SETTLEMENT_ACTION, common)
    quota_ledger._add_event(
        session,
        job,
        quota_ledger.RELEASE_ACTION,
        {
            **common,
            "released_cost_micros": max(0, reserved_cost - charge),
            "released_request_count": max(0, reserved_requests - requests),
            "release_reason": f"terminal_{job.status}",
        },
    )


def _synchronize_job_item_aggregates(
    session: Session,
    job: JobRecord,
) -> None:
    """Keep user-visible counters current without terminalizing the job early."""

    count_rows = session.execute(
        select(JobItemRecord.status, func.count())
        .where(JobItemRecord.job_id == job.id)
        .group_by(JobItemRecord.status)
    ).all()
    counts = {item_status: int(count) for item_status, count in count_rows}
    succeeded = sum(counts.get(value, 0) for value in SUCCESS_ITEM_STATUSES)
    failed = counts.get(JobItemStatus.FAILED.value, 0)
    terminal = sum(
        count
        for item_status, count in counts.items()
        if item_status in TERMINAL_ITEM_STATUSES
    )
    progress = min(
        99,
        max(job.progress, 20 + (80 * terminal // job.expected_item_count)),
    )
    if (
        job.succeeded_item_count,
        job.failed_item_count,
        job.progress,
    ) != (succeeded, failed, progress):
        job.succeeded_item_count = succeeded
        job.failed_item_count = failed
        job.progress = progress
        job.state_version += 1


@dataclass(frozen=True)
class AttemptLease:
    workspace_id: str
    job_id: str
    item_id: str
    attempt_id: str
    lease_token: str
    provider_idempotency_key: str
    operation: str
    configuration: dict[str, object]


class UsageEventWriter(Protocol):
    def add(self, session: Session, event: UsageEventRecord) -> None: ...


class DatabaseUsageEventWriter:
    def add(self, session: Session, event: UsageEventRecord) -> None:
        session.add(event)


class ExecutionCoordinator:
    """Coordinates execution exclusively through PostgreSQL-visible state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: Provider,
        *,
        worker_id: str | None = None,
        lease_seconds: float = 30,
        heartbeat_seconds: float = 10,
        usage_writer: UsageEventWriter | None = None,
        detail_planner: DetailPlanner | None = None,
        campaign_planner: DetailPlanner | None = None,
        max_concurrency: int = 1,
        max_detail_retries: int = 0,
        max_amazon_retries: int = 0,
        max_product_image_retries: int = 1,
        max_aurora_retries: int = 2,
        aurora_retry_backoff_base_seconds: float = 2,
        aurora_retry_backoff_max_seconds: float = 30,
        artifact_storage: ImageStorage | None = None,
        amazon_suite_reviewer: AmazonSuiteReviewer | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be positive and less than lease")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 3000:
            raise ValueError("max_concurrency must be from 1 through 3000")
        if max_detail_retries < 0 or max_detail_retries > 2:
            raise ValueError("max_detail_retries must be from 0 through 2")
        if max_amazon_retries < 0 or max_amazon_retries > 2:
            raise ValueError("max_amazon_retries must be from 0 through 2")
        if max_product_image_retries < 0 or max_product_image_retries > 1:
            raise ValueError("max_product_image_retries must be from 0 through 1")
        if max_aurora_retries < 0 or max_aurora_retries > 2:
            raise ValueError("max_aurora_retries must be from 0 through 2")
        self.session_factory = session_factory
        self.provider = provider
        self.worker_id = worker_id or f"worker-{socket.gethostname()}-{uuid4().hex}"
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.usage_writer = usage_writer or DatabaseUsageEventWriter()
        self.detail_planner = detail_planner
        self.campaign_planner = campaign_planner
        self.max_concurrency = max_concurrency
        self.max_detail_retries = max_detail_retries
        self.max_amazon_retries = max_amazon_retries
        self.max_product_image_retries = max_product_image_retries
        self.max_aurora_retries = max_aurora_retries
        self.aurora_retry_backoff_base_seconds = max(
            0.0, aurora_retry_backoff_base_seconds
        )
        self.aurora_retry_backoff_max_seconds = max(
            self.aurora_retry_backoff_base_seconds,
            aurora_retry_backoff_max_seconds,
        )
        self.artifact_storage = artifact_storage
        self.amazon_suite_reviewer = amazon_suite_reviewer

    async def run(self, job_id: str) -> JobStatus | None:
        current = self.prepare(job_id)
        if current is None or current in TERMINAL_JOB_STATUSES:
            return current
        if current == JobStatus.AWAITING_FINAL_REVIEW:
            if self._is_amazon_job(job_id) and not (
                self._amazon_generation_outstanding(job_id)
            ):
                with self.session_factory() as session:
                    failed = session.scalar(select(JobItemRecord.id).where(
                        JobItemRecord.job_id == job_id,
                        JobItemRecord.status == JobItemStatus.FAILED.value,
                    ).limit(1))
                if failed is not None:
                    return self._mark_amazon_quality_blocked(job_id)
            return await self._maybe_finalize_domestic_automatic_delivery(
                job_id, current
            )
        if current == JobStatus.CANCELLING:
            return self.cancel(job_id)
        if current == JobStatus.PLANNING:
            planner = (
                self.campaign_planner
                if self._is_domestic_main_image_job(job_id)
                else self.detail_planner
            )
            if planner is None:
                return self.fail_missing_detail_planner(job_id)
            try:
                planned = await planner.plan(job_id)
            except Exception as exc:
                if self._is_domestic_main_image_job(job_id):
                    logger.exception(
                        "domestic main image planning failed: job_id=%s", job_id
                    )
                    return self.fail_domestic_main_image_planning(job_id, exc)
                raise
            current = self.auto_confirm_detail_plan(job_id, planned)
            if current != JobStatus.PROCESSING:
                return current
        if current == JobStatus.AWAITING_CONFIRMATION:
            current = self.auto_confirm_detail_plan(job_id, current)
            if current != JobStatus.PROCESSING:
                return current

        is_parallel_aurora = self._is_parallel_aurora_job(job_id)
        is_v20_aurora = self._is_v20_aurora_job(job_id)
        parallel_mode = (
            self._parallel_aurora_mode(job_id) if is_parallel_aurora else None
        )
        claim_width = (
            1
            if (
                self._is_domestic_main_image_job(job_id)
                or self._is_aurora_domestic_detail_job(job_id)
                or self._is_native_replica_job(job_id)
            )
            and not is_parallel_aurora
            else self.max_concurrency
        )
        is_amazon = self._is_amazon_job(job_id)
        with self.session_factory() as session:
            fast_job = session.get(JobRecord, job_id)
            is_fast_replica = (
                fast_job is not None and self._fast_replica_record(fast_job)
            )
        if is_fast_replica:
            await self._run_fast_replica(job_id)
            return self.finalize_job(job_id)
        while True:
            leases: list[AttemptLease] = []
            for _ in range(claim_width):
                lease = self.claim_next_item(job_id)
                if lease is None:
                    break
                leases.append(lease)
            if not leases:
                self._close_failed_native_replica(job_id)
                if self._domestic_detail_has_blocking_failure(job_id):
                    self._skip_domestic_detail_pages_after_execution_failure(job_id)
                    break
                if (
                    is_amazon
                    and self.amazon_suite_reviewer is not None
                    and getattr(self.amazon_suite_reviewer, "enforce_delivery", False)
                ):
                    # A duplicate dispatch can arrive while the first worker
                    # still holds every concurrency slot.  An unfinished suite
                    # is not a review.  Closing it here leaves the remaining
                    # items permanently unclaimable.
                    if self._amazon_generation_outstanding(job_id):
                        return JobStatus.PROCESSING
                    review = await self.amazon_suite_reviewer.review(job_id)
                    action = getattr(review, "action", None)
                    enforce_delivery = True
                    if action == "retry_items":
                        # The quality service has fenced old deliverables and
                        # returned only failed items to PENDING.  Claim those
                        # items through the normal idempotent execution path.
                        continue
                    if action == "failed" and enforce_delivery:
                        # A reviewer transport/protocol failure is a manual
                        # review handoff, not a visual-quality rejection. The
                        # generated files remain deliverable after explicit
                        # user approval; only a completed visual failure is
                        # fenced into partial/blocked.
                        if self._amazon_review_needs_manual_review(job_id):
                            return self._mark_amazon_review_pending(job_id)
                        return self._mark_amazon_quality_blocked(job_id)
                    if (
                        enforce_delivery
                        and action in {"in_progress", "incomplete", "not_applicable"}
                    ):
                        return self._mark_amazon_review_pending(job_id)
                retry_delay = (
                    self._seconds_until_aurora_retry(job_id)
                    if is_v20_aurora
                    else None
                )
                if retry_delay is not None:
                    await asyncio.sleep(retry_delay)
                    continue
                break

            async def execute_staggered(
                index: int, lease: AttemptLease, staggered: bool
            ) -> bool:
                if staggered and index:
                    await asyncio.sleep(index * 3)
                return await self.execute_lease(lease)

            with self.session_factory() as session:
                current_job = session.get(JobRecord, job_id)
                refined_job = current_job is not None and refined_detail.is_refined(
                    current_job.configuration
                )
            await asyncio.gather(
                *(
                    execute_staggered(index, lease, refined_job)
                    for index, lease in enumerate(leases)
                )
            )
            if not is_parallel_aurora and self._domestic_batch_has_failure(
                job_id, leases
            ):
                self._skip_domestic_pages_after_execution_failure(job_id)
                break
            if self._domestic_detail_batch_has_failure(job_id, leases):
                self._skip_domestic_detail_pages_after_execution_failure(job_id)
                break
            if (
                is_calibrated_parallel_aurora_mode(parallel_mode)
                and self._domestic_batch_has_failure(job_id, leases)
            ):
                self._skip_calibrated_parallel_dependents(job_id, leases)

        if is_v20_aurora:
            set_audit, set_audit_error = await self._run_parallel_aurora_set_audit(
                job_id
            )
            if set_audit is False and set_audit_error is not None:
                self._mark_parallel_aurora_set_audit_failed(job_id, set_audit_error)
            elif set_audit is False and set_audit_error is None:
                from .main_image_auto_start import eligible as automatic_main_image

                with self.session_factory() as session:
                    job = session.get(JobRecord, job_id)
                    automatic = job is not None and automatic_main_image(job)
                if automatic:
                    self._mark_parallel_aurora_set_audit_failed(
                        job_id,
                        ValueError("Automatic delivery needs a passing set audit"),
                    )
                    return self.finalize_job(job_id)
                return self._mark_aurora_final_review_pending(job_id)
            elif set_audit is True and set_audit_error is None:
                return await self._maybe_finalize_domestic_automatic_delivery(
                    job_id, self._mark_aurora_final_review_pending(job_id)
                )
        return await self._maybe_finalize_domestic_automatic_delivery(
            job_id, self.finalize_job(job_id)
        )

    async def _run_fast_replica(self, job_id: str) -> None:
        """Refill free slots immediately; database claims fence duplicate workers."""
        from .replica_fast import CONCURRENCY

        pending: set[asyncio.Task[bool]] = set()
        try:
            while True:
                while len(pending) < min(self.max_concurrency, CONCURRENCY):
                    lease = self.claim_next_item(job_id)
                    if lease is None:
                        break
                    pending.add(asyncio.create_task(self.execute_lease(lease)))
                if not pending:
                    return
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
        finally:
            # Do not orphan provider calls when a worker is interrupted.
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _maybe_finalize_domestic_automatic_delivery(
        self, job_id: str, current: JobStatus | None
    ) -> JobStatus | None:
        if (
            current == JobStatus.AWAITING_FINAL_REVIEW
            and self._is_domestic_main_image_job(job_id)
        ):
            from .main_image_auto_start import finalize_validated_set

            with self.session_factory() as session:
                job = session.get(JobRecord, job_id, with_for_update=True)
                plan = session.scalar(
                    select(JobPlanRecord)
                    .where(
                        JobPlanRecord.job_id == job_id,
                    )
                    .order_by(JobPlanRecord.revision.desc())
                    .limit(1)
                    .with_for_update()
                )
                if job is None or plan is None:
                    return current
                planner_root = getattr(self.campaign_planner, "work_root", None)
                if planner_root is None or not finalize_validated_set(
                    session,
                    job,
                    plan,
                    work_root=planner_root,
                ):
                    return current
                session.commit()
            return self.finalize_job(job_id)
        if current == JobStatus.AWAITING_FINAL_REVIEW:
            from .crossborder_auto_start import complete_delivery, eligible

            with self.session_factory() as session:
                job = session.get(JobRecord, job_id, with_for_update=True)
                if job is not None and eligible(job):
                    plan = session.scalar(select(JobPlanRecord).where(
                        JobPlanRecord.job_id == job.id,
                    ).order_by(JobPlanRecord.revision.desc()).limit(1).with_for_update())
                    if plan is not None and complete_delivery(session, job, plan):
                        session.commit()
                        return JobStatus.COMPLETED
        if current != JobStatus.AWAITING_FINAL_REVIEW or not (
            self._is_aurora_domestic_detail_job(job_id)
        ):
            return current
        from .domestic_detail.automatic_delivery import complete_authorized_delivery

        return await complete_authorized_delivery(
            self.session_factory, self.artifact_storage, job_id
        )

    def _mark_amazon_quality_blocked(self, job_id: str) -> JobStatus | None:
        """Fence rejected outputs and finalize through normal usage settlement."""
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                return JobStatus(job.status)
            _synchronize_job_item_aggregates(session, job)
            failed_ids = select(JobItemRecord.id).where(
                JobItemRecord.job_id == job.id,
                JobItemRecord.status == JobItemStatus.FAILED.value,
            )
            session.query(JobAssetRecord).filter(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.job_item_id.in_(failed_ids),
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
            ).update({"is_deliverable": False}, synchronize_session=False)
            if job.status == JobStatus.AWAITING_FINAL_REVIEW.value:
                session.add(AuditEventRecord(
                    workspace_id=job.workspace_id,
                    action="job.review_state_recovered", resource_type="job",
                    resource_id=job.id,
                    details={"from": "awaiting_final_review",
                             "to": "partial" if job.succeeded_item_count else "failed",
                             "failed_item_count": job.failed_item_count},
                ))
            session.commit()
        result = self.finalize_job(job_id)
        if result in {JobStatus.PARTIAL, JobStatus.FAILED}:
            with self.session_factory() as session:
                job = session.get(JobRecord, job_id)
                if job is not None:
                    job.stage = "suite_quality_blocked"
                    session.commit()
        return result

    def _amazon_review_needs_manual_review(self, job_id: str) -> bool:
        with self.session_factory() as session:
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if plan is None or not isinstance(plan.payload, Mapping):
                return False
            quality = plan.payload.get("business_quality")
            return bool(
                isinstance(quality, Mapping)
                and quality.get("status") == "review_infrastructure_failed"
            )

    def _amazon_generation_outstanding(self, job_id: str) -> bool:
        with self.session_factory() as session:
            outstanding = session.scalar(
                select(func.count())
                .select_from(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.status.in_(
                        (
                            JobItemStatus.PENDING.value,
                            JobItemStatus.QUEUED.value,
                            JobItemStatus.RUNNING.value,
                            JobItemStatus.RETRY_WAIT.value,
                        )
                    ),
                )
            )
        return int(outstanding or 0) > 0

    def _mark_amazon_review_pending(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                return JobStatus(job.status)
            if session.scalar(
                select(JobItemRecord.id).where(
                    JobItemRecord.job_id == job.id,
                    JobItemRecord.status.in_(
                        ("pending", "queued", "running", "retry_wait")
                    ),
                ).limit(1)
            ) is not None:
                return JobStatus.PROCESSING
            transition_job(
                job,
                JobStatus.AWAITING_FINAL_REVIEW,
                stage="awaiting_suite_review",
                progress=min(99, job.progress),
            )
            session.commit()
            return JobStatus.AWAITING_FINAL_REVIEW

    def _skip_parallel_pages_after_style_failure(self, job_id: str) -> None:
        """Retained as a compatibility hook; parallel pages are never gated.

        All six immutable snapshots are claimable before provider execution.
        A failed page is handled by its own item retry policy and must not turn
        unrelated pending pages into synthetic ``SKIPPED`` rows.
        """

        return

    async def _run_parallel_aurora_set_audit(
        self, job_id: str
    ) -> tuple[bool | None, Exception | None]:
        """Run the six-page set audit, reporting why it failed.

        Returns ``(None, None)`` when the audit does not apply, ``(passed, None)``
        for a verdict, and ``(False, exc)`` when the audit raised.  The exception
        used to be swallowed, which made every ``AURORA_SET_AUDIT_FAILED`` row
        unrecoverable after the fact.
        """

        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return None, None
            current_status = JobStatus(job.status)
            if current_status in TERMINAL_JOB_STATUSES or current_status == (
                JobStatus.AWAITING_FINAL_REVIEW
            ):
                # A heartbeat can revisit a job after the set audit has already
                # handed it to the user. Do not spend another provider call on a
                # verdict that is waiting for explicit final approval.
                return None, None
            statuses = session.scalars(
                select(JobItemRecord.status).where(JobItemRecord.job_id == job_id)
            ).all()
        if len(statuses) != 6 or any(
            status not in SUCCESS_ITEM_STATUSES for status in statuses
        ):
            return None, None
        provider: object = self.provider
        routes = getattr(provider, "routes", None)
        if isinstance(routes, Mapping):
            provider = routes.get(JobType.DOMESTIC_MAIN_IMAGE.value, provider)
        with self.session_factory() as session:
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            policy = plan.payload.get("execution_policy") if plan else None
            mode = policy.get("execution_mode") if isinstance(policy, Mapping) else None
        audit = getattr(
            provider,
            "audit_parallel_set"
            if is_parallel_aurora_mode(mode) or not mode
            else "audit_sequential_v20_set",
            None,
        )
        if not callable(audit):
            return None, None
        attempts = 3
        retry_delay_seconds = 5.0
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return bool(await audit(job_id)), None
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    logger.exception(
                        "aurora set audit failed after %d attempt(s): job_id=%s",
                        attempt,
                        job_id,
                    )
                    break
                logger.warning(
                    "aurora set audit attempt %d/%d failed, retrying in %.1fs:"
                    " job_id=%s error=%s",
                    attempt,
                    attempts,
                    retry_delay_seconds,
                    job_id,
                    exc,
                )
                await asyncio.sleep(retry_delay_seconds)
        return False, last_error

    def _mark_aurora_final_review_pending(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if JobStatus(job.status) == JobStatus.AWAITING_FINAL_REVIEW:
                return JobStatus.AWAITING_FINAL_REVIEW
            transition_job(
                job,
                JobStatus.AWAITING_FINAL_REVIEW,
                stage="awaiting_final_review",
                progress=95,
            )
            session.commit()
            return JobStatus.AWAITING_FINAL_REVIEW

    def _mark_parallel_aurora_set_audit_failed(
        self, job_id: str, error: Exception | None = None
    ) -> None:
        with self.session_factory() as session:
            item = session.scalar(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.status == JobItemStatus.SUCCEEDED.value,
                )
                .order_by(JobItemRecord.ordinal.desc())
                .limit(1)
                .with_for_update()
            )
            if item is None:
                return
            item.status = JobItemStatus.FAILED.value
            item.stage = "set_audit_failed"
            item.error_code = "AURORA_SET_AUDIT_FAILED"
            item.error_message = "Aurora v2.0 six-page set audit did not pass."
            item.error_details = {
                "retryable": False,
                "scope": "set_audit",
                "reason": type(error).__name__ if error is not None else None,
                "message": str(error)[:512] if error is not None else None,
                "http_status": getattr(error, "http_status", None),
                "aurora_gateway_diagnostics": getattr(
                    error, "aurora_gateway_diagnostics", None
                ),
            }
            item.finished_at = utc_now()
            item.state_version += 1
            session.commit()

    def _seconds_until_aurora_retry(self, job_id: str) -> float | None:
        with self.session_factory() as session:
            due = session.scalar(
                select(func.min(JobItemRecord.next_attempt_at)).where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.status == JobItemStatus.RETRY_WAIT.value,
                )
            )
        if due is None:
            return None
        now = utc_now()
        if due.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        return max(0.01, min(30.0, (due - now).total_seconds()))

    def _is_amazon_job(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job_type = session.scalar(
                select(JobRecord.type).where(JobRecord.id == job_id).limit(1)
            )
            return job_type == JobType.AMAZON_VISUAL_SUITE.value

    def _is_domestic_main_image_job(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job_type = session.scalar(
                select(JobRecord.type).where(JobRecord.id == job_id).limit(1)
            )
            return job_type == JobType.DOMESTIC_MAIN_IMAGE.value

    def _is_aurora_domestic_detail_job(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None or job.type != JobType.DETAIL_PACK.value:
                return False
            settings = (
                job.configuration.get("settings")
                if isinstance(job.configuration, Mapping)
                else None
            )
            return (
                isinstance(settings, Mapping)
                and settings.get("market_region") == "domestic"
                and not refined_detail.is_refined(job.configuration)
            )

    def _is_parallel_aurora_job(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None or job.type != JobType.DOMESTIC_MAIN_IMAGE.value:
                return False
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if plan is None or not isinstance(plan.payload, Mapping):
                return False
            policy = plan.payload.get("execution_policy")
            return isinstance(policy, Mapping) and is_parallel_aurora_mode(
                policy.get("execution_mode")
            )

    def _is_v20_aurora_job(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None or job.type != JobType.DOMESTIC_MAIN_IMAGE.value:
                return False
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            policy = plan.payload.get("execution_policy") if plan else None
            return isinstance(policy, Mapping) and is_v20_aurora_mode(
                policy.get("execution_mode")
            )

    def _parallel_aurora_mode(self, job_id: str) -> str | None:
        with self.session_factory() as session:
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            policy = plan.payload.get("execution_policy") if plan else None
            mode = policy.get("execution_mode") if isinstance(policy, Mapping) else None
            return str(mode) if is_parallel_aurora_mode(mode) else None

    def _skip_calibrated_parallel_dependents(
        self, job_id: str, leases: Sequence[AttemptLease]
    ) -> None:
        """Close pages that cannot be planned after a calibration gate fails."""

        failed_ordinals: list[int] = []
        with self.session_factory() as session:
            for lease in leases:
                item = session.get(JobItemRecord, lease.item_id)
                if item is not None and item.status == JobItemStatus.FAILED.value:
                    failed_ordinals.append(int(item.ordinal))
        if not failed_ordinals:
            return
        first_failed = min(failed_ordinals)
        with self.session_factory() as session:
            items = session.scalars(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.ordinal > first_failed,
                    JobItemRecord.status.in_(
                        [
                            JobItemStatus.PENDING.value,
                            JobItemStatus.QUEUED.value,
                            JobItemStatus.RETRY_WAIT.value,
                        ]
                    ),
                )
                .with_for_update()
            ).all()
            now = utc_now()
            for item in items:
                item.status = JobItemStatus.SKIPPED.value
                item.stage = "blocked_by_calibration"
                item.error_code = "AURORA_CALIBRATION_BLOCKED"
                item.error_message = "未完成前置页面校准，后续页面未启动。"
                item.finished_at = now
                item.state_version += 1
            session.commit()

    def _skip_domestic_pages_after_execution_failure(self, job_id: str) -> None:
        """Stop a strictly sequential Aurora campaign after an infrastructure failure.

        Later pages have not failed generation and must not be submitted after the
        Harness rejects the active page. Marking them skipped preserves that fact
        while allowing the durable job to reach a terminal state.
        """

        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None or job.type != JobType.DOMESTIC_MAIN_IMAGE.value:
                return
            pending = session.scalars(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.status.in_(
                        [JobItemStatus.PENDING.value, JobItemStatus.QUEUED.value]
                    ),
                )
                .with_for_update()
            ).all()
            now = utc_now()
            for item in pending:
                item.status = JobItemStatus.SKIPPED.value
                item.stage = "blocked_by_previous_page"
                item.error_code = "AURORA_SEQUENCE_HALTED"
                item.error_message = (
                    "Not generated because an earlier sequential page failed."
                )
                item.finished_at = now
                item.state_version += 1
            session.commit()

    def _skip_domestic_detail_pages_after_execution_failure(self, job_id: str) -> None:
        """Close a domestic detail pair sequence after an unrecoverable page failure."""

        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None or not self._is_domestic_detail_job_record(job):
                return
            pending = session.scalars(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.status.in_(
                        [
                            JobItemStatus.PENDING.value,
                            JobItemStatus.QUEUED.value,
                            JobItemStatus.RETRY_WAIT.value,
                        ]
                    ),
                )
                .with_for_update()
            ).all()
            now = utc_now()
            for item in pending:
                item.status = JobItemStatus.SKIPPED.value
                item.stage = "blocked_by_previous_pair"
                item.error_code = "AURORA_DETAIL_SEQUENCE_HALTED"
                item.error_message = (
                    "因前置 pair 页面失败，后续详情页面未启动。"
                )
                item.finished_at = now
                item.next_attempt_at = None
                item.state_version += 1
            session.commit()

    def _domestic_detail_has_blocking_failure(self, job_id: str) -> bool:
        """Detect a failed pair that would otherwise leave later pages pending."""

        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None or not self._is_domestic_detail_job_record(job):
                return False
            active = session.scalar(
                select(func.count())
                .select_from(JobAttemptRecord)
                .where(
                    JobAttemptRecord.job_id == job_id,
                    JobAttemptRecord.status.in_(ACTIVE_ATTEMPT_STATUSES),
                    JobAttemptRecord.lease_expires_at > utc_now(),
                )
            )
            if int(active or 0) > 0:
                return False
            failed_count = session.scalar(
                select(func.count())
                .select_from(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job_id,
                    JobItemRecord.status == JobItemStatus.FAILED.value,
                )
            )
            return int(failed_count or 0) > 0

    def _domestic_detail_batch_has_failure(
        self, job_id: str, leases: Sequence[AttemptLease]
    ) -> bool:
        if not leases:
            return False
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None or not self._is_domestic_detail_job_record(job):
                return False
            statuses = session.scalars(
                select(JobItemRecord.status).where(
                    JobItemRecord.id.in_([lease.item_id for lease in leases])
                )
            ).all()
            return any(status == JobItemStatus.FAILED.value for status in statuses)

    def _domestic_batch_has_failure(
        self, job_id: str, leases: Sequence[AttemptLease]
    ) -> bool:
        with self.session_factory() as session:
            job_type = session.scalar(
                select(JobRecord.type).where(JobRecord.id == job_id).limit(1)
            )
            if job_type != JobType.DOMESTIC_MAIN_IMAGE.value:
                return False
            statuses = session.scalars(
                select(JobItemRecord.status).where(
                    JobItemRecord.id.in_([lease.item_id for lease in leases])
                )
            ).all()
            return any(status == JobItemStatus.FAILED.value for status in statuses)

    def prepare(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            current = JobStatus(job.status)
            if current in TERMINAL_JOB_STATUSES or current in {
                JobStatus.CANCELLING,
                JobStatus.AWAITING_CONFIRMATION,
            }:
                return current
            if current == JobStatus.QUEUED and job.type in {
                JobType.DETAIL_PACK.value,
                JobType.AMAZON_VISUAL_SUITE.value,
                JobType.DOMESTIC_MAIN_IMAGE.value,
            }:
                confirmed_plan = session.scalar(
                    select(JobPlanRecord.id)
                    .where(
                        JobPlanRecord.job_id == job.id,
                        JobPlanRecord.validation_status
                        == PlanValidationStatus.VALID.value,
                        JobPlanRecord.confirmed_at.is_not(None),
                    )
                    .limit(1)
                )
                if confirmed_plan is not None:
                    transition_job(
                        job,
                        JobStatus.PROCESSING,
                        stage="generating",
                        progress=20,
                    )
                    session.commit()
                    return JobStatus.PROCESSING
                transition_job(job, JobStatus.PLANNING, stage="planning", progress=10)
                session.commit()
                return JobStatus.PLANNING
            if current == JobStatus.QUEUED:
                transition_job(
                    job, JobStatus.PROCESSING, stage="processing", progress=20
                )
            session.commit()
            return JobStatus(job.status)

    def auto_confirm_detail_plan(
        self,
        job_id: str,
        planned_status: JobStatus | None,
    ) -> JobStatus | None:
        """Auto-confirm only the explicitly opted-in domestic detail flow."""

        if planned_status != JobStatus.AWAITING_CONFIRMATION:
            return planned_status
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if job.type == JobType.DOMESTIC_MAIN_IMAGE.value:
                from .main_image_auto_start import (
                    confirm_validated_plan as confirm_main,
                )

                plan = session.scalar(
                    select(JobPlanRecord)
                    .where(
                        JobPlanRecord.job_id == job.id,
                    )
                    .order_by(JobPlanRecord.revision.desc())
                    .limit(1)
                    .with_for_update()
                )
                if plan is not None:
                    confirm_main(
                        session,
                        job,
                        plan,
                        work_root=getattr(self.campaign_planner, "work_root", None),
                    )
                    session.commit()
                return JobStatus(job.status)
            if job.type == JobType.AMAZON_VISUAL_SUITE.value:
                from .amazon_auto_start import confirm_validated_plan

                plan = session.scalar(
                    select(JobPlanRecord)
                    .where(JobPlanRecord.job_id == job.id)
                    .order_by(JobPlanRecord.revision.desc())
                    .limit(1)
                    .with_for_update()
                )
                if plan is not None:
                    confirm_validated_plan(session, job, plan)
                    session.commit()
                return JobStatus(job.status)
            from .crossborder_auto_start import (
                confirm_validated_plan as confirm_crossborder,
            )
            from .crossborder_auto_start import (
                eligible as crossborder_eligible,
            )

            if crossborder_eligible(job):
                plan = session.scalar(select(JobPlanRecord).where(
                    JobPlanRecord.job_id == job.id,
                ).order_by(JobPlanRecord.revision.desc()).limit(1).with_for_update())
                if plan is not None:
                    confirm_crossborder(session, job, plan)
                    session.commit()
                return JobStatus(job.status)
            settings = job.configuration.get("settings")
            if not isinstance(settings, dict):
                return JobStatus(job.status)
            if (
                job.type != JobType.DETAIL_PACK.value
                or settings.get("market_region") != "domestic"
                or JobStatus(job.status) != JobStatus.AWAITING_CONFIRMATION
            ):
                return JobStatus(job.status)
            from .domestic_detail.auto_start import confirm_validated_plan, eligible

            if not eligible(job) or job.stage == "intake_confirmation_required":
                return JobStatus(job.status)
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job.id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if plan is not None:
                confirm_validated_plan(session, job, plan)
                session.commit()
            return JobStatus(job.status)

    def fail_missing_detail_planner(self, job_id: str) -> JobStatus | None:
        """Fail closed; static/template planning fallback is forbidden."""

        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if JobStatus(job.status) != JobStatus.PLANNING:
                return JobStatus(job.status)
            now = utc_now()
            items = session.scalars(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job.id,
                    JobItemRecord.status.in_(
                        [
                            JobItemStatus.PENDING.value,
                            JobItemStatus.QUEUED.value,
                        ]
                    ),
                )
                .with_for_update()
            ).all()
            for item in items:
                item.status = JobItemStatus.FAILED.value
                item.stage = "planning_failed"
                item.error_code = "DETAIL_PLANNER_UNAVAILABLE"
                item.error_message = "The mandatory detail planner is unavailable."
                item.finished_at = now
                item.state_version += 1
            job.failed_item_count = len(items)
            job.error_code = "DETAIL_PLANNER_UNAVAILABLE"
            job.error_message = "The mandatory detail planner is unavailable."
            transition_job(
                job,
                JobStatus.FAILED,
                stage="planning_failed",
                progress=100,
            )
            settle_credits(session, job, 0)
            session.commit()
            return JobStatus.FAILED

    def fail_domestic_main_image_planning(
        self, job_id: str, error: Exception
    ) -> JobStatus | None:
        """Persist Aurora planning failures instead of redelivering forever."""

        # Only the exception class name used to be stored, so all 54 recorded
        # planning failures were indistinguishable from each other.  Keep the
        # bounded message and the gateway diagnostics too: they are what makes a
        # failure diagnosable without a production re-run.
        failure_details: dict[str, Any] = {
            "reason": type(error).__name__,
            "message": str(error)[:512],
        }
        gateway_diagnostics = getattr(error, "aurora_gateway_diagnostics", None)
        if isinstance(gateway_diagnostics, Mapping):
            failure_details["aurora_gateway_diagnostics"] = dict(gateway_diagnostics)
        http_status = getattr(error, "http_status", None)
        if isinstance(http_status, int):
            failure_details["http_status"] = http_status
        error_path = getattr(error, "error_path", None)
        if isinstance(error_path, str):
            failure_details["error_path"] = error_path

        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if JobStatus(job.status) != JobStatus.PLANNING:
                return JobStatus(job.status)
            items = session.scalars(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job.id,
                    JobItemRecord.status.in_(
                        [JobItemStatus.PENDING.value, JobItemStatus.QUEUED.value]
                    ),
                )
                .with_for_update()
            ).all()
            now = utc_now()
            for item in items:
                item.status = JobItemStatus.FAILED.value
                item.stage = "planning_failed"
                item.error_code = "AURORA_PLANNING_FAILED"
                item.error_message = "国内电商主图规划失败，请重新提交任务。"
                item.error_details = dict(failure_details)
                item.finished_at = now
                item.state_version += 1
            job.failed_item_count = len(items)
            job.error_code = "AURORA_PLANNING_FAILED"
            job.error_message = "国内电商主图规划失败，请重新提交任务。"
            job.error_details = dict(failure_details)
            transition_job(
                job,
                JobStatus.FAILED,
                stage="planning_failed",
                progress=100,
            )
            settle_credits(session, job, 0)
            session.commit()
            return JobStatus.FAILED

    def claim_next_item(self, job_id: str) -> AttemptLease | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            current = JobStatus(job.status)
            if current in TERMINAL_JOB_STATUSES or current in {
                JobStatus.CANCELLING,
                JobStatus.AWAITING_CONFIRMATION,
            }:
                return None

            recovered, expired_handled = self._recover_expired_attempt(session, job)
            if expired_handled:
                session.commit()
            if recovered is not None:
                return recovered

            active = session.scalar(
                select(func.count())
                .select_from(JobAttemptRecord)
                .where(
                    JobAttemptRecord.job_id == job.id,
                    JobAttemptRecord.status.in_(ACTIVE_ATTEMPT_STATUSES),
                    JobAttemptRecord.lease_expires_at > utc_now(),
                )
            )
            from .replica_fast import CONCURRENCY as fast_replica_concurrency

            limit = (
                min(self.max_concurrency, fast_replica_concurrency)
                if self._fast_replica_record(job)
                else self.max_concurrency
            )
            if int(active or 0) >= limit:
                return None

            item_query = select(JobItemRecord).where(
                JobItemRecord.job_id == job.id,
                JobItemRecord.status.in_(
                    [
                        JobItemStatus.PENDING.value,
                        JobItemStatus.QUEUED.value,
                        JobItemStatus.RETRY_WAIT.value,
                    ]
                ),
                (JobItemRecord.status != JobItemStatus.RETRY_WAIT.value)
                | (JobItemRecord.next_attempt_at.is_(None))
                | (JobItemRecord.next_attempt_at <= utc_now()),
            )
            calibrated_parallel = False
            if job.type == JobType.DOMESTIC_MAIN_IMAGE.value:
                plan = session.scalar(
                    select(JobPlanRecord)
                    .where(JobPlanRecord.job_id == job.id)
                    .order_by(JobPlanRecord.revision.desc())
                    .limit(1)
                )
                policy = plan.payload.get("execution_policy") if plan else None
                calibrated_parallel = is_calibrated_parallel_aurora_mode(
                    policy.get("execution_mode")
                    if isinstance(policy, Mapping)
                    else None
                )
            if self._native_replica_record(job):
                succeeded_pages = session.scalar(
                    select(func.count())
                    .select_from(JobItemRecord)
                    .where(
                        JobItemRecord.job_id == job.id,
                        JobItemRecord.status.in_(SUCCESS_ITEM_STATUSES),
                    )
                )
                item_query = item_query.where(
                    JobItemRecord.ordinal == int(succeeded_pages or 0)
                )
            elif (
                job.type == JobType.DOMESTIC_MAIN_IMAGE.value
                and not self._job_uses_parallel_aurora(session, job.id)
            ):
                # Aurora owns a strict page-by-page protocol. Outbox delivery is
                # at-least-once, so a duplicate process_job invocation may enter
                # while the preceding page is still running. Keep the configured
                # site concurrency unchanged and make only the next contiguous
                # page claimable at the database boundary.
                succeeded_pages = session.scalar(
                    select(func.count())
                    .select_from(JobItemRecord)
                    .where(
                        JobItemRecord.job_id == job.id,
                        JobItemRecord.status.in_(SUCCESS_ITEM_STATUSES),
                    )
                )
                item_query = item_query.where(
                    JobItemRecord.ordinal == int(succeeded_pages or 0)
                )
            elif self._is_domestic_detail_job_record(job):
                # Aurora detail pages are rendered as one pair canvas followed
                # by its deterministic second slice.  Enforce this dependency
                # while claiming rows so separate Celery workers cannot race
                # the second screen ahead of the persisted pair canvas.
                active_pair_row = session.execute(
                    select(
                        JobItemRecord.configuration["pair_id"].as_string(),
                        JobItemRecord.configuration["pair_position"].as_string(),
                    )
                    .where(
                        JobItemRecord.job_id == job.id,
                        JobItemRecord.status == JobItemStatus.RUNNING.value,
                    )
                    .limit(1)
                ).first()
                active_pair = active_pair_row[0] if active_pair_row else None
                active_pair_position = active_pair_row[1] if active_pair_row else None
                if active_pair is not None and str(active_pair_position) == "1":
                    # The pair canvas must finish and be persisted before its
                    # second slice is eligible for a claim.
                    return None
                if active_pair is not None:
                    next_pair = active_pair
                else:
                    # Pick the earliest pair that is not fully successful. A
                    # later pair must never leap over a failed or unknown
                    # predecessor; bounded pair retries are requeued below.
                    pair_rows = session.scalars(
                        select(JobItemRecord)
                        .where(JobItemRecord.job_id == job.id)
                        .order_by(JobItemRecord.ordinal)
                    ).all()
                    next_pair = None
                    recoverable_pair_errors = {
                        "AURORA_PAIR_CANVAS_REHYDRATION_FAILED",
                        "AURORA_PAIR_CACHE_MISSING",
                        "IMAGE_RESPONSE_SIZE_INVALID",
                        "USAGE_RECORD_FAILED",
                    }
                    for candidate in pair_rows:
                        candidate_pair = (
                            candidate.configuration.get("pair_id")
                            if isinstance(candidate.configuration, Mapping)
                            else None
                        )
                        if not candidate_pair:
                            continue
                        if candidate.status in {
                            JobItemStatus.SUCCEEDED.value,
                            JobItemStatus.REUSED.value,
                            JobItemStatus.SKIPPED.value,
                            JobItemStatus.CANCELLED.value,
                        }:
                            continue
                        next_pair = str(candidate_pair)
                        if (
                            candidate.status == JobItemStatus.FAILED.value
                            and candidate.attempt_count < 2
                            and candidate.error_code in recoverable_pair_errors
                        ):
                            candidate.status = JobItemStatus.PENDING.value
                            candidate.stage = "retrying"
                            candidate.finished_at = None
                            candidate.next_attempt_at = None
                            candidate.error_message = None
                            candidate.error_details = None
                            candidate.state_version += 1
                        elif candidate.status == JobItemStatus.FAILED.value:
                            return None
                        break
                if next_pair is not None:
                    item_query = item_query.where(
                        JobItemRecord.configuration["pair_id"].as_string() == next_pair
                    )
            elif job.type == JobType.AMAZON_VISUAL_SUITE.value:
                active_ordinal = session.scalar(
                    select(func.min(JobItemRecord.ordinal)).where(
                        JobItemRecord.job_id == job.id,
                        JobItemRecord.status.in_(
                            [
                                JobItemStatus.PENDING.value,
                                JobItemStatus.QUEUED.value,
                                JobItemStatus.RUNNING.value,
                            ]
                        ),
                    )
                )
                if active_ordinal is None:
                    return None
                lower, upper = _amazon_group_ordinal_bounds(int(active_ordinal))
                item_query = item_query.where(
                    JobItemRecord.ordinal >= lower,
                    JobItemRecord.ordinal <= upper,
                )
            if calibrated_parallel:
                candidates = session.scalars(
                    item_query.order_by(JobItemRecord.ordinal).with_for_update(
                        skip_locked=True
                    )
                ).all()
                item = next(
                    (
                        candidate
                        for candidate in candidates
                        if isinstance(candidate.configuration, Mapping)
                        and isinstance(
                            candidate.configuration.get("aurora_snapshot"), Mapping
                        )
                    ),
                    None,
                )
            else:
                item = session.scalar(
                    item_query.order_by(JobItemRecord.ordinal)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            if item is None:
                return None

            previous_version = item.state_version
            claimed_id = session.scalar(
                update(JobItemRecord)
                .where(
                    JobItemRecord.id == item.id,
                    JobItemRecord.job_id == job.id,
                    JobItemRecord.state_version == previous_version,
                    JobItemRecord.status.in_(
                        [
                            JobItemStatus.PENDING.value,
                            JobItemStatus.QUEUED.value,
                            JobItemStatus.RETRY_WAIT.value,
                        ]
                    ),
                )
                .values(
                    status=JobItemStatus.RUNNING.value,
                    next_attempt_at=None,
                    started_at=func.coalesce(JobItemRecord.started_at, utc_now()),
                    attempt_count=JobItemRecord.attempt_count + 1,
                    state_version=JobItemRecord.state_version + 1,
                )
                .returning(JobItemRecord.id)
            )
            if claimed_id is None:
                session.rollback()
                return None

            session.refresh(item)
            lease = self._new_attempt(job, item)
            session.add(lease)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return None
            return self._lease_from_records(job, item, lease)

    @staticmethod
    def _job_uses_parallel_aurora(session: Session, job_id: str) -> bool:
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == job_id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        if plan is None or not isinstance(plan.payload, Mapping):
            return False
        policy = plan.payload.get("execution_policy")
        return isinstance(policy, Mapping) and is_parallel_aurora_mode(
            policy.get("execution_mode")
        )

    @staticmethod
    def _is_domestic_detail_job_record(job: JobRecord) -> bool:
        settings = (
            job.configuration.get("settings")
            if isinstance(job.configuration, Mapping)
            else None
        )
        return (
            job.type == JobType.DETAIL_PACK.value
            and isinstance(settings, Mapping)
            and settings.get("market_region") == "domestic"
            and not refined_detail.is_refined(job.configuration)
        )

    @staticmethod
    def _fast_replica_record(job: JobRecord) -> bool:
        return bool(
            job.type == JobType.REFERENCE_REMIX.value
            and job.configuration.get("settings", {}).get("reference_skill")
            in {
                "aurora-ecommerce-reference-remix-fast",
                "all-category-style-benchmark-still-fast",
            }
        )

    @staticmethod
    def _native_replica_record(job: JobRecord) -> bool:
        return bool(
            job.type == JobType.REFERENCE_REMIX.value
            and job.configuration.get("replica_session_id")
            and job.configuration.get("settings", {}).get("reference_skill")
            in {
                "aurora-ecommerce-reference-remix",
                "all-category-style-benchmark-still",
            }
        )

    def _is_native_replica_job(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            return job is not None and self._native_replica_record(job)

    def _close_failed_native_replica(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None or not self._native_replica_record(job):
                return
            items = session.scalars(
                select(JobItemRecord).where(JobItemRecord.job_id == job_id)
            ).all()
            if not any(item.status in {"failed", "outcome_unknown"} for item in items):
                return
            for item in items:
                if item.status in {"pending", "queued", "retry_wait"}:
                    item.status, item.stage = "skipped", "blocked_by_previous_screen"
                    item.error_code = "REPLICA_SEQUENCE_HALTED"
                    item.error_message = (
                        "前面的图片生成中断，本屏尚未生成；"
                        "重试会保留成功图片并补齐剩余部分。"
                    )
                    item.finished_at, item.next_attempt_at = utc_now(), None
                    item.state_version += 1
            session.commit()

    @staticmethod
    def _job_uses_v20_aurora(session: Session, job_id: str) -> bool:
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == job_id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        if plan is None or not isinstance(plan.payload, Mapping):
            return False
        policy = plan.payload.get("execution_policy")
        return isinstance(policy, Mapping) and is_v20_aurora_mode(
            policy.get("execution_mode")
        )

    def _recover_expired_attempt(
        self, session: Session, job: JobRecord
    ) -> tuple[AttemptLease | None, bool]:
        now = utc_now()
        attempt = session.scalar(
            select(JobAttemptRecord)
            .where(
                JobAttemptRecord.job_id == job.id,
                JobAttemptRecord.status.in_(ACTIVE_ATTEMPT_STATUSES),
                JobAttemptRecord.lease_expires_at <= now,
            )
            .order_by(JobAttemptRecord.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if attempt is None:
            return None, False
        item = session.get(JobItemRecord, attempt.job_item_id, with_for_update=True)
        if item is None:
            attempt.status = AttemptStatus.FAILED.value
            attempt.error_code = "JOB_ITEM_MISSING"
            attempt.finished_at = now
            return None, True
        if attempt.status == AttemptStatus.CLAIMED.value:
            attempt.lease_token = uuid4().hex
            attempt.leased_by = self.worker_id
            attempt.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            return self._lease_from_records(job, item, attempt), True

        # RUNNING is the durable provider-call boundary.  Once its lease expires
        # the result is unknown and the request must never be issued blindly.
        attempt.status = AttemptStatus.OUTCOME_UNKNOWN.value
        attempt.error_code = "PROVIDER_OUTCOME_UNKNOWN"
        attempt.error_message = "Worker lease expired after provider invocation."
        attempt.finished_at = now
        from .replica_image_recovery import MAX_RESUMPTIONS, job_available

        if attempt.attempt_no <= MAX_RESUMPTIONS and job_available(session, job):
            item.status = JobItemStatus.PENDING.value
            item.stage = "retrying_original_image_task"
            item.error_code = "PROVIDER_OUTCOME_UNKNOWN"
            item.error_message = "工作进程中断，正在查询原生图任务；不会重新生成。"
            item.error_details = {"retryable": True, "reconcile_only": True}
            item.next_attempt_at = now
            item.finished_at = None
            item.state_version += 1
            return None, True
        pair_reconcile = (
            job.type == JobType.DETAIL_PACK.value
            and not self._is_domestic_detail_job_record(job)
            and isinstance(item.configuration, Mapping)
            and bool(item.configuration.get("pair_id"))
            and attempt.attempt_no <= 2
        )
        if pair_reconcile:
            # Pair requests use a stable idempotency key, so a worker restart
            # can safely reconcile the same provider call once more.
            item.status = JobItemStatus.PENDING.value
            item.stage = "retrying_outcome_reconciliation"
            item.error_code = "PROVIDER_OUTCOME_UNKNOWN"
            item.error_message = (
                "Worker lease expired; reconciling the same pair request."
            )
            item.error_details = {
                "retryable": True,
                "outcome_unknown": True,
                "provider_idempotency_key": attempt.provider_idempotency_key,
            }
            item.next_attempt_at = now
            item.finished_at = None
            item.state_version += 1
            return None, True
        if (
            job.type == JobType.DOMESTIC_MAIN_IMAGE.value
            and self._job_uses_v20_aurora(session, job.id)
            and attempt.attempt_no <= self.max_aurora_retries
        ):
            # The v2.0 page snapshot and Provider idempotency key are immutable.
            # Re-entering through RETRY_WAIT therefore asks the upstream to
            # reconcile the same logical request rather than submitting a new
            # page revision after the worker disappears.
            item.status = JobItemStatus.RETRY_WAIT.value
            item.stage = "retry_wait_outcome_reconciliation"
            item.error_code = "PROVIDER_OUTCOME_UNKNOWN"
            item.error_message = (
                "Worker lease expired; reconciling the same idempotent page request."
            )
            item.error_details = {
                "retryable": True,
                "outcome_unknown": True,
                "provider_idempotency_key": attempt.provider_idempotency_key,
            }
            item.next_attempt_at = now
            item.finished_at = None
            item.state_version += 1
            return None, True
        item.status = JobItemStatus.FAILED.value
        item.error_code = "PROVIDER_OUTCOME_UNKNOWN"
        item.error_message = (
            "Provider outcome is unknown; manual reconciliation required."
        )
        if self._is_domestic_detail_job_record(job):
            item.error_details = {
                "retryable": False,
                "outcome_unknown": True,
                "manual_reconciliation_required": True,
                "provider_idempotency_key": attempt.provider_idempotency_key,
            }
        item.finished_at = now
        item.state_version += 1
        return None, True

    def _new_attempt(self, job: JobRecord, item: JobItemRecord) -> JobAttemptRecord:
        attempt_no = item.attempt_count
        provider_idempotency_key = f"{job.id}:{item.id}:{attempt_no}"
        snapshot = (
            item.configuration.get("aurora_snapshot")
            if isinstance(item.configuration, Mapping)
            else None
        )
        if job.type == JobType.DOMESTIC_MAIN_IMAGE.value and isinstance(
            snapshot, Mapping
        ):
            provider_idempotency_key = (
                f"{job.id}:{item.id}:plan-{snapshot.get('plan_revision', 1)}:"
                f"page-{snapshot.get('page_revision', 0)}"
            )
        elif (
            job.type == JobType.DETAIL_PACK.value
            and isinstance(item.configuration, Mapping)
            and item.configuration.get("pair_id")
        ):
            # A pair canvas is one logical provider request. If a worker dies
            # after the upstream call, retry with the same key for reconciliation.
            provider_idempotency_key = (
                f"{job.id}:{item.id}:pair-{item.configuration['pair_id']}"
            )
        return JobAttemptRecord(
            id=new_id("attempt"),
            workspace_id=job.workspace_id,
            job_id=job.id,
            job_item_id=item.id,
            attempt_no=attempt_no,
            status=AttemptStatus.CLAIMED.value,
            lease_token=uuid4().hex,
            leased_by=self.worker_id,
            lease_expires_at=utc_now() + timedelta(seconds=self.lease_seconds),
            provider=self.provider.name,
            operation=job.type,
            provider_idempotency_key=provider_idempotency_key,
        )

    @staticmethod
    def _lease_from_records(
        job: JobRecord,
        item: JobItemRecord,
        attempt: JobAttemptRecord,
    ) -> AttemptLease:
        return AttemptLease(
            workspace_id=job.workspace_id,
            job_id=job.id,
            item_id=item.id,
            attempt_id=attempt.id,
            lease_token=attempt.lease_token,
            provider_idempotency_key=attempt.provider_idempotency_key,
            operation=attempt.operation,
            configuration={
                "job": dict(job.configuration),
                "item": dict(item.configuration),
            },
        )

    async def execute_lease(self, lease: AttemptLease) -> bool:
        if not self.mark_provider_started(lease):
            return False
        request = ProviderRequest(
            workspace_id=lease.workspace_id,
            job_id=lease.job_id,
            job_item_id=lease.item_id,
            attempt_id=lease.attempt_id,
            operation=lease.operation,
            idempotency_key=lease.provider_idempotency_key,
            configuration=lease.configuration,
        )
        stopped = threading.Event()
        lease_lost = threading.Event()

        def heartbeat() -> None:
            # Native Harness subprocesses and PNG compression can block the
            # provider's event loop longer than the lease. Keep ownership alive
            # independently, including while the final artifact is being saved.
            while not stopped.wait(self.heartbeat_seconds):
                try:
                    renewed = self.renew_lease(lease)
                except Exception:
                    logger.exception("Attempt heartbeat failed: %s", lease.attempt_id)
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        keeper = threading.Thread(target=heartbeat, daemon=True)
        keeper.start()
        provider_task = asyncio.create_task(self.provider.execute(request))
        try:
            while not provider_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(provider_task),
                        timeout=self.heartbeat_seconds,
                    )
                except TimeoutError:
                    if lease_lost.is_set():
                        provider_task.cancel()
                        return False
            result = await provider_task
            if lease_lost.is_set():
                return False
            effective_provider = self._effective_provider_name(result)
            if effective_provider == "mock":
                if result.persisted_artifact is not None:
                    await self._delete_persisted_artifact(result.persisted_artifact)
                result = self._without_mock_artifacts(result)
            elif result.artifact is not None:
                result = await self._persist_provider_artifact(lease, result)
            finished = self.finish_attempt(lease, result)
            if result.persisted_artifact is not None and not self._artifact_linked(
                lease,
                result.persisted_artifact,
            ):
                await self._delete_persisted_artifact(result.persisted_artifact)
            return finished
        finally:
            stopped.set()
            keeper.join(timeout=1)

    def _effective_provider_name(self, result: ProviderResult) -> str:
        return result.provider_name or self.provider.name

    @staticmethod
    def _without_mock_artifacts(result: ProviderResult) -> ProviderResult:
        raw_usage = dict(result.raw_usage)
        raw_usage["output_images"] = 0
        for key in ("raw", "normalized"):
            value = raw_usage.get(key)
            if isinstance(value, dict):
                sanitized = dict(value)
                sanitized["output_images"] = 0
                raw_usage[key] = sanitized
        metrics_value = raw_usage.get("metrics")
        metrics = dict(metrics_value) if isinstance(metrics_value, dict) else {}
        metrics["mock_artifact_discarded"] = bool(
            metrics.get("mock_artifact_discarded")
        ) or (result.artifact is not None or result.persisted_artifact is not None)
        raw_usage["metrics"] = metrics
        return replace(
            result,
            raw_usage=raw_usage,
            artifact=None,
            persisted_artifact=None,
        )

    async def _persist_provider_artifact(
        self,
        lease: AttemptLease,
        result: ProviderResult,
    ) -> ProviderResult:
        artifact = result.artifact
        if artifact is None:
            return result
        if self.artifact_storage is None:
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                error_code="OUTPUT_STORAGE_UNAVAILABLE",
                error_message="Generated output storage is unavailable.",
                retryable=False,
                artifact=None,
            )
        suffix = (
            ".png"
            if artifact.content_type == "image/png"
            else Path(artifact.filename).suffix or ".bin"
        )
        object_key = (
            f"{lease.workspace_id}/generated/"
            f"{hashlib.sha256(lease.provider_idempotency_key.encode()).hexdigest()}"
            f"{suffix}"
        )
        try:
            stored = await self.artifact_storage.save_validated(
                ValidatedImage(
                    payload=artifact.payload,
                    filename=artifact.filename,
                    content_type=artifact.content_type,
                    width=artifact.width,
                    height=artifact.height,
                    object_key=object_key,
                    sha256=artifact.sha256,
                )
            )
        except Exception as exc:
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                error_code="OUTPUT_STORAGE_FAILED",
                error_message=(
                    f"Generated output could not be stored ({type(exc).__name__})."
                ),
                retryable=False,
                artifact=None,
            )
        raw_usage = dict(result.raw_usage)
        metrics_value = raw_usage.get("metrics")
        metrics = dict(metrics_value) if isinstance(metrics_value, dict) else {}
        metrics["saved_at"] = utc_now().isoformat()
        metrics["locally_resampled"] = artifact.locally_resampled
        metrics["provider_width"] = artifact.provider_width
        metrics["provider_height"] = artifact.provider_height
        raw_usage["metrics"] = metrics
        return replace(
            result,
            raw_usage=raw_usage,
            persisted_artifact=PersistedProviderArtifact(
                filename=stored.filename,
                content_type=stored.content_type,
                byte_size=stored.byte_size,
                width=stored.width,
                height=stored.height,
                sha256=stored.sha256,
                storage_provider=stored.storage_provider,
                storage_bucket=stored.storage_bucket,
                object_key=stored.object_key,
                object_version=stored.object_version,
                etag=stored.etag,
            ),
            artifact=None,
        )

    def _artifact_linked(
        self,
        lease: AttemptLease,
        artifact: PersistedProviderArtifact,
    ) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(JobAssetRecord.id)
                    .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
                    .where(
                        JobAssetRecord.workspace_id == lease.workspace_id,
                        JobAssetRecord.job_id == lease.job_id,
                        JobAssetRecord.job_item_id == lease.item_id,
                        AssetRecord.object_key == artifact.object_key,
                    )
                    .limit(1)
                )
                is not None
            )

    async def _delete_persisted_artifact(
        self,
        artifact: PersistedProviderArtifact,
    ) -> None:
        if self.artifact_storage is None:
            return
        try:
            await self.artifact_storage.delete_object(
                artifact.object_key,
                artifact.object_version,
            )
        except Exception:
            return

    def mark_provider_started(self, lease: AttemptLease) -> bool:
        now = utc_now()
        with self.session_factory() as session:
            changed_id = session.scalar(
                update(JobAttemptRecord)
                .where(
                    JobAttemptRecord.id == lease.attempt_id,
                    JobAttemptRecord.job_id == lease.job_id,
                    JobAttemptRecord.job_item_id == lease.item_id,
                    JobAttemptRecord.lease_token == lease.lease_token,
                    JobAttemptRecord.leased_by == self.worker_id,
                    JobAttemptRecord.status == AttemptStatus.CLAIMED.value,
                    JobAttemptRecord.lease_expires_at > now,
                )
                .values(status=AttemptStatus.RUNNING.value, started_at=now)
                .returning(JobAttemptRecord.id)
            )
            session.commit()
            return changed_id is not None

    def renew_lease(self, lease: AttemptLease) -> bool:
        now = utc_now()
        with self.session_factory() as session:
            changed_id = session.scalar(
                update(JobAttemptRecord)
                .where(
                    JobAttemptRecord.id == lease.attempt_id,
                    JobAttemptRecord.lease_token == lease.lease_token,
                    JobAttemptRecord.leased_by == self.worker_id,
                    JobAttemptRecord.status.in_(ACTIVE_ATTEMPT_STATUSES),
                    JobAttemptRecord.lease_expires_at > now,
                )
                .values(lease_expires_at=now + timedelta(seconds=self.lease_seconds))
                .returning(JobAttemptRecord.id)
            )
            session.commit()
            return changed_id is not None

    def finish_attempt(
        self,
        lease: AttemptLease,
        result: ProviderResult,
    ) -> bool:
        effective_provider = self._effective_provider_name(result)
        if effective_provider == "mock":
            result = self._without_mock_artifacts(result)
        now = utc_now()
        with self.session_factory() as session:
            job = session.get(JobRecord, lease.job_id, with_for_update=True)
            item = session.get(JobItemRecord, lease.item_id, with_for_update=True)
            attempt = session.get(
                JobAttemptRecord, lease.attempt_id, with_for_update=True
            )
            if job is None or item is None or attempt is None:
                return False
            if (
                JobStatus(job.status) in TERMINAL_JOB_STATUSES
                or JobStatus(job.status) == JobStatus.CANCELLING
            ):
                return False
            if (
                attempt.lease_token != lease.lease_token
                or attempt.leased_by != self.worker_id
                or attempt.status != AttemptStatus.RUNNING.value
                or _lease_expired(attempt.lease_expires_at, now)
                or item.status != JobItemStatus.RUNNING.value
            ):
                return False

            expected_dimensions = _expected_delivery_dimensions(item, result, job)
            if (
                result.outcome == ProviderOutcome.SUCCEEDED
                and result.persisted_artifact is not None
                and expected_dimensions is not None
                and (
                    result.persisted_artifact.width,
                    result.persisted_artifact.height,
                )
                != expected_dimensions
            ):
                result = replace(
                    result,
                    outcome=ProviderOutcome.FAILED,
                    error_code="OUTPUT_DIMENSIONS_INVALID",
                    error_message=(
                        "Generated output does not match the canonical "
                        "delivery dimensions."
                    ),
                    retryable=False,
                )

            if result.persisted_artifact is not None:
                limits = session.get(
                    WorkspaceLimitRecord,
                    job.workspace_id,
                    with_for_update=True,
                )
                used_bytes = session.scalar(
                    select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                        AssetRecord.workspace_id == job.workspace_id,
                        AssetRecord.lifecycle_status
                        == AssetLifecycleStatus.READY.value,
                        AssetRecord.deleted_at.is_(None),
                    )
                )
                if (
                    limits is None
                    or int(used_bytes or 0) + result.persisted_artifact.byte_size
                    > limits.storage_quota_bytes
                ):
                    result = replace(
                        result,
                        outcome=ProviderOutcome.FAILED,
                        error_code="STORAGE_QUOTA_EXCEEDED",
                        error_message=(
                            "Generated output exceeds the workspace storage quota."
                        ),
                        retryable=False,
                    )

            original_task_attempt_id = None
            if result.provider_request_id:
                from .replica_image_recovery import job_available

                original = session.scalar(select(JobAttemptRecord).where(
                    JobAttemptRecord.provider_request_id == result.provider_request_id,
                    JobAttemptRecord.id != attempt.id,
                ))
                if (
                    original is not None and original.workspace_id == job.workspace_id
                    and original.job_id == job.id and original.job_item_id == item.id
                    and job_available(session, job)
                ):
                    original_task_attempt_id = original.id
            # A GET-only reconciliation is another local attempt, not another
            # provider request. Keep the unique original receipt on its owner.
            attempt.provider_request_id = (
                None if original_task_attempt_id else result.provider_request_id
            )
            if result.provider_name:
                attempt.provider = result.provider_name
            attempt.finished_at = now
            attempt.error_code = result.error_code
            attempt.error_message = result.error_message
            if result.outcome == ProviderOutcome.SUCCEEDED:
                attempt.status = AttemptStatus.SUCCEEDED.value
                item.status = JobItemStatus.SUCCEEDED.value
                item.result_source = (
                    "mock" if effective_provider == "mock" else "generated"
                )
                item.error_code = None
                item.error_message = None
                item.error_details = None
            elif result.outcome == ProviderOutcome.FAILED:
                attempt.status = AttemptStatus.FAILED.value
                # Aurora domestic detail pairs are the one detail workflow
                # whose adjacent screens may be leased by different workers.
                # Give transient provider errors and pair-canvas rehydration a
                # bounded two-attempt recovery window without changing the
                # legacy explicit-retry contract for ordinary detail jobs.
                pair_retry = (
                    job.type == JobType.DETAIL_PACK.value
                    and isinstance(item.configuration, Mapping)
                    and bool(item.configuration.get("pair_id"))
                    and attempt.attempt_no <= 2
                    and (
                        bool(result.retryable)
                        or result.error_code
                        in {
                            "AURORA_PAIR_CANVAS_REHYDRATION_FAILED",
                            "AURORA_PAIR_CACHE_MISSING",
                            "IMAGE_RESPONSE_SIZE_INVALID",
                            "USAGE_RECORD_FAILED",
                        }
                    )
                )
                should_retry = bool(result.retryable) and (
                    (
                        job.type in {
                            JobType.REFERENCE_REMIX.value,
                            JobType.MODEL_POSE_TRANSFER.value,
                            JobType.CREATIVE_IMAGE.value,
                        }
                        and attempt.attempt_no <= 1
                    )
                    or (
                        job.type == JobType.LOCAL_EDIT.value
                        and (result.error_code or "").startswith(
                            "LOCAL_EDIT_VISUAL_REVIEW"
                        )
                        and attempt.attempt_no <= 1
                    )
                    or (
                        job.type == JobType.DETAIL_PACK.value
                        and attempt.attempt_no <= self.max_detail_retries
                    )
                    or pair_retry
                    or (
                        job.type == JobType.AMAZON_VISUAL_SUITE.value
                        and attempt.attempt_no <= self.max_amazon_retries
                    )
                    or (
                        job.type == JobType.PRODUCT_IMAGE.value
                        and attempt.attempt_no <= self.max_product_image_retries
                    )
                    or (
                        job.type == JobType.DOMESTIC_MAIN_IMAGE.value
                        and self._job_uses_v20_aurora(session, job.id)
                        and attempt.attempt_no <= self.max_aurora_retries
                    )
                )
                aurora_retry = (
                    should_retry
                    and job.type == JobType.DOMESTIC_MAIN_IMAGE.value
                    and self._job_uses_v20_aurora(session, job.id)
                )
                item.status = (
                    JobItemStatus.RETRY_WAIT.value
                    if aurora_retry
                    else JobItemStatus.PENDING.value
                    if should_retry
                    else JobItemStatus.FAILED.value
                )
                item.stage = (
                    "retry_wait"
                    if aurora_retry
                    else "retrying"
                    if should_retry
                    else "failed"
                )
                if aurora_retry:
                    schedule = (1.0, 2.5, 7.5)
                    multiplier = schedule[
                        min(attempt.attempt_no - 1, len(schedule) - 1)
                    ]
                    jitter_seed = hashlib.sha256(
                        f"{item.id}:{attempt.attempt_no}".encode()
                    ).digest()[0]
                    jitter = 1.0 + (jitter_seed / 2550.0)
                    retry_delay = min(
                        self.aurora_retry_backoff_max_seconds,
                        self.aurora_retry_backoff_base_seconds * multiplier * jitter,
                    )
                    item.next_attempt_at = now + timedelta(seconds=retry_delay)
                else:
                    item.next_attempt_at = None
            else:
                attempt.status = AttemptStatus.OUTCOME_UNKNOWN.value
                from .replica_image_recovery import MAX_RESUMPTIONS, job_available

                replica_reconcile = (
                    bool(result.retryable) and attempt.attempt_no <= MAX_RESUMPTIONS
                    and job_available(session, job)
                )
                pair_reconcile = (
                    job.type == JobType.DETAIL_PACK.value
                    and not self._is_domestic_detail_job_record(job)
                    and isinstance(item.configuration, Mapping)
                    and bool(item.configuration.get("pair_id"))
                    and attempt.attempt_no <= 2
                )
                item.status = (
                    JobItemStatus.PENDING.value
                    if pair_reconcile or replica_reconcile
                    else JobItemStatus.FAILED.value
                )
                item.stage = (
                    "retrying_original_image_task" if replica_reconcile
                    else "retrying" if pair_reconcile else "failed"
                )
                item.error_code = "PROVIDER_OUTCOME_UNKNOWN"
                item.error_message = (
                    "Provider outcome is being reconciled with the same pair request."
                    if pair_reconcile
                    else None
                )
                item.next_attempt_at = None
            if result.outcome != ProviderOutcome.SUCCEEDED:
                item.error_code = (
                    "PROVIDER_OUTCOME_UNKNOWN"
                    if result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
                    else result.error_code or item.error_code
                )
                item.error_message = result.error_message
                metrics_value = result.raw_usage.get("metrics")
                failure_metrics = (
                    dict(metrics_value) if isinstance(metrics_value, dict) else {}
                )
                failure_details: dict[str, Any] = {
                    "retryable": bool(result.retryable),
                    "http_status": result.http_status,
                    "provider_request_id": result.provider_request_id,
                    "provider_outcome": result.outcome.value,
                }
                if original_task_attempt_id:
                    failure_details["original_task_attempt_id"] = (
                        original_task_attempt_id
                    )
                from .provider_diagnostics import retained_metrics

                failure_details.update(retained_metrics(result.raw_usage))
                gateway_diagnostics = failure_metrics.get("aurora_gateway_diagnostics")
                if isinstance(gateway_diagnostics, Mapping):
                    failure_details["aurora_gateway_diagnostics"] = dict(
                        gateway_diagnostics
                    )
                attempt.error_details = failure_details
                delivery_status = failure_metrics.get("delivery_status")
                item.error_details = {
                    **failure_details,
                    "next_attempt_at": (
                        item.next_attempt_at.isoformat()
                        if item.next_attempt_at is not None
                        else None
                    ),
                    "needs_reselection": bool(failure_metrics.get("needs_reselection"))
                    or delivery_status == "needs_reselection_for_strict_lock",
                }
            item.finished_at = (
                None
                if item.status
                in {JobItemStatus.PENDING.value, JobItemStatus.RETRY_WAIT.value}
                else now
            )
            item.state_version += 1

            if (
                result.outcome == ProviderOutcome.SUCCEEDED
                and result.persisted_artifact is not None
            ):
                stored = result.persisted_artifact
                asset_id = new_id("asset")
                metrics_value = result.raw_usage.get("metrics")
                result_metrics = (
                    dict(metrics_value) if isinstance(metrics_value, dict) else {}
                )
                canonical_dimensions = _expected_delivery_dimensions(item, result, job)
                session.add(
                    AssetRecord(
                        id=asset_id,
                        workspace_id=job.workspace_id,
                        media_kind=AssetMediaKind.IMAGE.value,
                        origin_kind=AssetOriginKind.GENERATED.value,
                        lifecycle_status=AssetLifecycleStatus.READY.value,
                        original_filename=stored.filename,
                        content_type=stored.content_type,
                        byte_size=stored.byte_size,
                        width=stored.width,
                        height=stored.height,
                        sha256=stored.sha256,
                        storage_provider=stored.storage_provider,
                        storage_bucket=stored.storage_bucket,
                        object_key=stored.object_key,
                        object_version=stored.object_version,
                        etag=stored.etag,
                        asset_metadata={
                            "workflow_version": (
                                item.configuration.get("workflow_version")
                            ),
                            "resume_signature": (
                                item.configuration.get("resume_signature")
                            ),
                            "actual_width": stored.width,
                            "actual_height": stored.height,
                            "provider_metrics": result_metrics,
                        },
                    )
                )
                output_role = _output_asset_role(job.type)
                output_validation = {
                    "sha256": stored.sha256,
                    "actual_width": stored.width,
                    "actual_height": stored.height,
                    "requested_size": item.configuration.get("size"),
                    "canonical_width": (
                        canonical_dimensions[0]
                        if canonical_dimensions is not None
                        else None
                    ),
                    "canonical_height": (
                        canonical_dimensions[1]
                        if canonical_dimensions is not None
                        else None
                    ),
                    "locally_resampled": bool(
                        result_metrics.get("locally_resampled", False)
                    ),
                    "provider_width": result_metrics.get("provider_width"),
                    "provider_height": result_metrics.get("provider_height"),
                    "outside_changed_pixels": result_metrics.get(
                        "outside_changed_pixels"
                    ),
                    "raw_visual_success": result_metrics.get("raw_visual_success"),
                    "visual_review_summary": result_metrics.get(
                        "visual_review_summary"
                    ),
                    "visual_review_reason_codes": result_metrics.get(
                        "visual_review_reason_codes"
                    ),
                    "visual_review_version": result_metrics.get(
                        "visual_review_version"
                    ),
                    "delivery_status": result_metrics.get("delivery_status"),
                    "perceptual_dhash": result.raw_usage.get("perceptual_dhash"),
                    "aurora_pair_id": result_metrics.get("aurora_pair_id"),
                    "aurora_pair_canvas_dimensions": result_metrics.get(
                        "aurora_pair_canvas_dimensions"
                    ),
                    "aurora_pair_canvas_object_key": result_metrics.get(
                        "aurora_pair_canvas_object_key"
                    ),
                    "aurora_pair_canvas_sha256": result_metrics.get(
                        "aurora_pair_canvas_sha256"
                    ),
                    "aurora_pair_canvas_storage": result_metrics.get(
                        "aurora_pair_canvas_storage"
                    ),
                    "aurora_pair_slice_dimensions": result_metrics.get(
                        "aurora_pair_slice_dimensions"
                    ),
                    "audit_entrypoint": result_metrics.get("audit_entrypoint"),
                    "pair_audit": result_metrics.get("pair_audit"),
                    "semantic_audit_required": bool(
                        result_metrics.get("pair_audit", {}).get(
                            "semantic_audit_required", False
                        )
                        if isinstance(result_metrics.get("pair_audit"), Mapping)
                        else False
                    ),
                }
                # A bounded QA redraw reuses the same durable JobItem.  The
                # output-link uniqueness key is (job, item, direction, role,
                # ordinal), so replace the fenced link in place rather than
                # inserting a second row that can never be delivered.
                output_link = session.scalar(
                    select(JobAssetRecord)
                    .where(
                        JobAssetRecord.job_id == job.id,
                        JobAssetRecord.job_item_id == item.id,
                        JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                        JobAssetRecord.role == output_role,
                        JobAssetRecord.ordinal == item.ordinal,
                    )
                    .with_for_update()
                )
                if output_link is None:
                    session.add(
                        JobAssetRecord(
                            workspace_id=job.workspace_id,
                            job_id=job.id,
                            job_item_id=item.id,
                            asset_id=asset_id,
                            direction=JobAssetDirection.OUTPUT.value,
                            role=output_role,
                            ordinal=item.ordinal,
                            is_deliverable=True,
                            validation=output_validation,
                        )
                    )
                else:
                    output_link.asset_id = asset_id
                    output_link.is_deliverable = True
                    output_link.validation = output_validation

            usage_provider = effective_provider
            usage_model = result.model_name or self.provider.model
            usage_pricing = result.pricing_version or self.provider.pricing_version
            usage_event = UsageEventRecord(
                workspace_id=job.workspace_id,
                job_id=job.id,
                job_item_id=item.id,
                attempt_id=attempt.id,
                provider=usage_provider,
                model=usage_model,
                operation=job.type,
                provider_request_id=result.provider_request_id,
                input_tokens=_usage_count(result.raw_usage, "input_tokens"),
                output_tokens=_usage_count(result.raw_usage, "output_tokens"),
                cached_tokens=_usage_count(result.raw_usage, "cached_tokens"),
                input_image_count=_usage_count(result.raw_usage, "input_images"),
                output_image_count=_usage_count(result.raw_usage, "output_images"),
                quality=item.quality,
                width=(
                    result.persisted_artifact.width
                    if result.persisted_artifact is not None
                    else None
                ),
                height=(
                    result.persisted_artifact.height
                    if result.persisted_artifact is not None
                    else None
                ),
                usage_reported=result.usage_reported,
                raw_usage=result.raw_usage,
                pricing_version=usage_pricing,
                currency=job.currency or "USD",
                estimated_cost_micros=result.estimated_cost_micros,
                actual_cost_micros=result.actual_cost_micros,
            )
            try:
                self.usage_writer.add(session, usage_event)
                if job.type in {
                    JobType.DETAIL_PACK.value,
                    JobType.AMAZON_VISUAL_SUITE.value,
                }:
                    from .xinghe_agent import update_job_metrics

                    update_job_metrics(session, job)
                _synchronize_job_item_aggregates(session, job)
                session.commit()
                return True
            except Exception as exc:
                session.rollback()
                usage_error_type = type(exc).__name__
        return self._fail_closed_on_usage_error(
            lease,
            result,
            usage_error_type,
        )

    def _fail_closed_on_usage_error(
        self,
        lease: AttemptLease,
        result: ProviderResult,
        usage_error_type: str,
    ) -> bool:
        """Persist a controlled failure when usage cannot be recorded."""

        now = utc_now()
        with self.session_factory() as session:
            job = session.get(JobRecord, lease.job_id, with_for_update=True)
            item = session.get(JobItemRecord, lease.item_id, with_for_update=True)
            attempt = session.get(
                JobAttemptRecord, lease.attempt_id, with_for_update=True
            )
            if job is None or item is None or attempt is None:
                return False
            if (
                job.workspace_id != lease.workspace_id
                or item.workspace_id != lease.workspace_id
                or attempt.workspace_id != lease.workspace_id
                or attempt.lease_token != lease.lease_token
                or attempt.leased_by != self.worker_id
                or attempt.status != AttemptStatus.RUNNING.value
                or item.status != JobItemStatus.RUNNING.value
            ):
                return False
            attempt.status = AttemptStatus.FAILED.value
            attempt.provider_request_id = result.provider_request_id
            attempt.error_code = "USAGE_RECORD_FAILED"
            attempt.error_message = "Usage could not be recorded; execution closed."
            attempt.finished_at = now
            item.status = JobItemStatus.FAILED.value
            item.error_code = "USAGE_RECORD_FAILED"
            item.error_message = "Usage could not be recorded; execution closed."
            item.finished_at = now
            item.state_version += 1
            session.add(
                AuditEventRecord(
                    workspace_id=lease.workspace_id,
                    action="skill_runtime.usage_record_failed",
                    resource_type="job",
                    resource_id=lease.job_id,
                    request_id=lease.attempt_id,
                    details={
                        "attempt_id": lease.attempt_id,
                        "job_item_id": lease.item_id,
                        "provider_type": self.provider.name,
                        "error_code": "USAGE_RECORD_FAILED",
                        "usage_error_type": usage_error_type,
                    },
                )
            )
            _synchronize_job_item_aggregates(session, job)
            session.commit()
            return True

    def finalize_job(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            current = JobStatus(job.status)
            if current in TERMINAL_JOB_STATUSES:
                return current

            count_rows = session.execute(
                select(JobItemRecord.status, func.count())
                .where(JobItemRecord.job_id == job.id)
                .group_by(JobItemRecord.status)
            ).all()
            counts: dict[str, int] = {
                item_status: int(count) for item_status, count in count_rows
            }
            nonterminal = sum(
                count
                for item_status, count in counts.items()
                if item_status not in TERMINAL_ITEM_STATUSES
            )
            if nonterminal:
                return current

            succeeded = sum(counts.get(value, 0) for value in SUCCESS_ITEM_STATUSES)
            failed = counts.get(JobItemStatus.FAILED.value, 0)
            cancelled = counts.get(JobItemStatus.CANCELLED.value, 0)
            job.succeeded_item_count = succeeded
            job.failed_item_count = failed

            costs = session.execute(
                select(
                    func.count(UsageEventRecord.id),
                    func.count(UsageEventRecord.actual_cost_micros),
                    func.sum(UsageEventRecord.actual_cost_micros),
                ).where(UsageEventRecord.job_id == job.id)
            ).one()
            reported_count = (
                session.scalar(
                    select(func.count())
                    .select_from(UsageEventRecord)
                    .where(
                        UsageEventRecord.job_id == job.id,
                        UsageEventRecord.usage_reported.is_(True),
                    )
                )
                or 0
            )
            usage_count = int(costs[0] or 0)
            known_cost_count = int(costs[1] or 0)
            usage_cost = int(costs[2] or 0)
            planner_count = 0
            planner_reported = 0
            planner_known_cost = 0
            planner_cost = 0
            planner_real_calls = 0
            latest_plan: JobPlanRecord | None = None
            if job.type in {
                JobType.DETAIL_PACK.value,
                JobType.AMAZON_VISUAL_SUITE.value,
            }:
                from .xinghe_agent import planner_accounting

                latest_plan = session.scalar(
                    select(JobPlanRecord)
                    .where(JobPlanRecord.job_id == job.id)
                    .order_by(JobPlanRecord.revision.desc())
                    .limit(1)
                )
                if latest_plan is not None:
                    (
                        planner_count,
                        planner_reported,
                        planner_known_cost,
                        planner_cost,
                        planner_real_calls,
                    ) = planner_accounting(latest_plan.payload)
            review_calls = _amazon_review_calls(
                session,
                job,
                latest_plan.payload if latest_plan is not None else None,
            )
            review_accounting = _amazon_review_accounting(review_calls)
            total_usage_count = (
                usage_count + planner_count + review_accounting.request_count
            )
            total_reported_count = (
                int(reported_count)
                + planner_reported
                + review_accounting.reported_count
            )
            total_known_cost = (
                known_cost_count
                + planner_known_cost
                + review_accounting.known_actual_cost_count
            )
            job.actual_cost_micros = (
                usage_cost + planner_cost + review_accounting.actual_cost_micros
                if total_usage_count and total_known_cost == total_usage_count
                else None
            )
            from .credit_billing import successful_image_credits

            customer_credits = successful_image_credits(session, job)
            job.actual_credits = (
                customer_credits
                if customer_credits is not None
                else (0 if usage_count else None)
            )
            real_usage_events = session.scalars(
                select(UsageEventRecord).where(
                    UsageEventRecord.job_id == job.id,
                    UsageEventRecord.provider != "mock",
                )
            ).all()
            job.api_request_count = (
                sum(usage_request_count(event) for event in real_usage_events)
                + planner_real_calls
                + review_accounting.real_request_count
            )
            job.usage_completeness = (
                UsageCompleteness.COMPLETE.value
                if total_usage_count and total_reported_count == total_usage_count
                else (
                    UsageCompleteness.PARTIAL.value
                    if total_reported_count
                    else UsageCompleteness.NONE.value
                )
            )

            if current == JobStatus.CANCELLING:
                target = JobStatus.CANCELLED
            elif succeeded == job.expected_item_count:
                if (
                    job.type == JobType.DETAIL_PACK.value
                    and not refined_detail.is_refined(job.configuration)
                ):
                    # Domestic Aurora and cross-border Skill detail pages both
                    # require an explicit human delivery decision after all
                    # modules are generated. Cross-border QA is disclosure and
                    # selection only; it never triggers automatic redraw.
                    target = JobStatus.AWAITING_FINAL_REVIEW
                else:
                    target = JobStatus.COMPLETED
            elif succeeded:
                target = JobStatus.PARTIAL
            elif cancelled == job.expected_item_count:
                target = JobStatus.CANCELLED
            else:
                target = JobStatus.FAILED
            if target in {JobStatus.FAILED, JobStatus.PARTIAL} and not job.error_code:
                failure = session.scalar(
                    select(JobItemRecord).where(
                        JobItemRecord.job_id == job.id,
                        JobItemRecord.status == JobItemStatus.FAILED.value,
                        JobItemRecord.error_code.is_not(None),
                    ).order_by(JobItemRecord.ordinal).limit(1)
                )
                if failure is not None:
                    job.error_code = failure.error_code
                    job.error_message = failure.error_message
                    job.error_details = dict(failure.error_details or {})
            terminal_stage = (
                "awaiting_user_final_review"
                if target == JobStatus.AWAITING_FINAL_REVIEW
                else target.value
            )
            transition_job(job, target, stage=terminal_stage, progress=100)
            from .replica_progress import reconcile_job_document

            reconcile_job_document(session, job)
            if job.type in {
                JobType.DETAIL_PACK.value,
                JobType.AMAZON_VISUAL_SUITE.value,
            }:
                from .xinghe_agent import update_job_metrics

                update_job_metrics(session, job)
            _settle_amazon_provider_quota(session, job, review_calls)
            settle_credits(session, job, job.actual_credits or 0)
            session.commit()
            return target

    def cancel(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            current = JobStatus(job.status)
            if current in TERMINAL_JOB_STATUSES:
                return current
            if current != JobStatus.CANCELLING:
                transition_job(job, JobStatus.CANCELLING, progress=job.progress)
            now = utc_now()
            attempts = session.scalars(
                select(JobAttemptRecord)
                .where(
                    JobAttemptRecord.job_id == job.id,
                    JobAttemptRecord.status.in_(ACTIVE_ATTEMPT_STATUSES),
                )
                .with_for_update()
            ).all()
            for attempt in attempts:
                attempt.status = (
                    AttemptStatus.CANCELLED.value
                    if attempt.status == AttemptStatus.CLAIMED.value
                    else AttemptStatus.OUTCOME_UNKNOWN.value
                )
                if attempt.status == AttemptStatus.OUTCOME_UNKNOWN.value:
                    attempt.error_code = "PROVIDER_OUTCOME_UNKNOWN"
                attempt.finished_at = now
            items = session.scalars(
                select(JobItemRecord)
                .where(
                    JobItemRecord.job_id == job.id,
                    JobItemRecord.status.in_(
                        [
                            JobItemStatus.PENDING.value,
                            JobItemStatus.QUEUED.value,
                            JobItemStatus.RETRY_WAIT.value,
                            JobItemStatus.RUNNING.value,
                        ]
                    ),
                )
                .with_for_update()
            ).all()
            for item in items:
                item.status = JobItemStatus.CANCELLED.value
                item.finished_at = now
                item.state_version += 1
            session.commit()
        return self.finalize_job(job_id)


def _output_asset_role(job_type: str) -> str:
    return {
        JobType.LOCAL_EDIT.value: "local_edit_result",
        JobType.PRODUCT_IMAGE.value: "product_image_result",
        JobType.REFERENCE_REMIX.value: "reference_remix_result",
        JobType.MODEL_POSE_TRANSFER.value: "model_pose_transfer_result",
        JobType.CREATIVE_IMAGE.value: "creative_image_result",
        JobType.DETAIL_PACK.value: "detail_page",
        JobType.AMAZON_VISUAL_SUITE.value: "amazon_visual",
        JobType.DOMESTIC_MAIN_IMAGE.value: "domestic_main_image",
    }.get(job_type, "generated_image")


def _expected_delivery_dimensions(
    item: JobItemRecord,
    result: ProviderResult,
    job: JobRecord | None = None,
) -> tuple[int, int] | None:
    """Resolve item dimensions and verify native-original delivery receipts."""

    configuration = item.configuration if isinstance(item.configuration, dict) else {}
    size = configuration.get("size")
    if isinstance(size, str) and re.fullmatch(r"[1-9]\d*x[1-9]\d*", size):
        width_text, height_text = size.split("x", maxsplit=1)
        expected = (int(width_text), int(height_text))
        if (
            job is not None
            and job.type == JobType.DETAIL_PACK.value
            and refined_detail.is_refined(job.configuration)
            and result.persisted_artifact is not None
        ):
            artifact = result.persisted_artifact
            metrics = result.raw_usage.get("metrics")
            receipt = refined_detail.native_delivery_receipt(artifact, list(expected))
            if (
                receipt is not None
                and isinstance(metrics, Mapping)
                and metrics.get("locally_resampled") is False
                and metrics.get("delivery_canvas") == receipt
            ):
                return artifact.width, artifact.height
        return expected
    width = configuration.get("target_width")
    height = configuration.get("target_height")
    if (
        isinstance(width, int)
        and not isinstance(width, bool)
        and width > 0
        and isinstance(height, int)
        and not isinstance(height, bool)
        and height > 0
    ):
        return width, height
    usage_width = result.raw_usage.get("target_width")
    usage_height = result.raw_usage.get("target_height")
    if (
        isinstance(usage_width, int)
        and not isinstance(usage_width, bool)
        and usage_width > 0
        and isinstance(usage_height, int)
        and not isinstance(usage_height, bool)
        and usage_height > 0
    ):
        return usage_width, usage_height
    return None


def build_fake_coordinator(
    session_factory: sessionmaker[Session],
    delay_seconds: float,
    *,
    storage: ImageStorage | None = None,
    xinghe_gateway: object | None = None,
    xinghe_concurrency: int = 6,
    budget_limit_micros: int = 200_000_000,
    medium_price_micros: int = 45_000,
    high_price_micros: int = 90_000,
    xinghe_skill_root: Path | None = None,
    product_image_provider_enabled: bool = False,
    reference_remix_provider_enabled: bool = False,
    model_pose_transfer_provider_enabled: bool = False,
    creative_image_provider_enabled: bool = False,
    amazon_visual_suite_provider_enabled: bool = False,
    crossborder_detail_provider_enabled: bool = False,
    domestic_main_image_provider_enabled: bool = False,
    aurora_work_root: Path | None = None,
    aurora_execution_mode: str = "sequential_v20",
    aurora_max_transient_retries: int = 2,
    aurora_retry_backoff_base_seconds: float = 2,
    aurora_retry_backoff_max_seconds: float = 30,
    domestic_revision_settings: Settings | None = None,
) -> ExecutionCoordinator:
    from .amazon_visual_suite_provider import (
        AMAZON_VISUAL_SUITE_SKILL_ID,
        AmazonVisualSuiteProvider,
    )
    from .aurora_main_image_planner import AuroraMainImagePlanner
    from .aurora_main_image_provider import AuroraMainImageProvider
    from .creative_image_provider import CreativeImageProvider
    from .domestic_detail_provider import DomesticDetailProvider
    from .model_pose_transfer_provider import ModelPoseTransferProvider
    from .new_detail_skill_provider import BundledSkillManifestValidator
    from .product_image_provider import ProductImageProvider
    from .provider_registry import (
        DetailMarketRoutingPlanner,
        RegisteredOperationRoutingProvider,
        UnavailableProvider,
    )
    from .reference_remix_provider import (
        AuroraRemixFastAdapter,
        ReferenceRemixProvider,
        ReferenceRemixRouterProvider,
        ReferenceVisualReverseRemixProvider,
    )
    from .replica_fast_provider import FastReplicaProvider
    from .replica_style_delivery import ReplicaDeliveryProvider
    from .xinghe_agent import (
        FakeXingheGateway,
        XingheGateway,
        XingheImageProvider,
        XinghePlanningCoordinator,
    )

    gateway: XingheGateway
    if xinghe_gateway is None:
        gateway = FakeXingheGateway(delay_seconds=delay_seconds)
    else:
        gateway = cast("XingheGateway", xinghe_gateway)
    skill_root = xinghe_skill_root or default_xinghe_skill_root()
    base_url_signature = str(getattr(gateway, "base_url", "mock://local"))
    planner = XinghePlanningCoordinator(
        session_factory,
        gateway,
        storage=storage,
        skill_root=skill_root,
        base_url_signature=base_url_signature,
        medium_price_micros=medium_price_micros,
        high_price_micros=high_price_micros,
        budget_limit_micros=budget_limit_micros,
    )
    detail_provider: Provider = XingheImageProvider(
        session_factory,
        gateway,
        storage=storage,
        skill_root=skill_root,
        max_concurrency=xinghe_concurrency,
        budget_limit_micros=budget_limit_micros,
    )
    international_planner = planner
    if xinghe_gateway is not None and crossborder_detail_provider_enabled:
        from .crossborder_detail_provider import (
            CROSSBORDER_DETAIL_SKILL_ID,
            CrossborderDetailProvider,
        )

        crossborder_skill_root = default_skill_root(CROSSBORDER_DETAIL_SKILL_ID)
        international_planner = XinghePlanningCoordinator(
            session_factory,
            gateway,
            storage=storage,
            skill_root=crossborder_skill_root,
            base_url_signature=base_url_signature,
            medium_price_micros=medium_price_micros,
            high_price_micros=high_price_micros,
            budget_limit_micros=budget_limit_micros,
            skill_id=CROSSBORDER_DETAIL_SKILL_ID,
        )
        crossborder_detail_delegate = XingheImageProvider(
            session_factory,
            gateway,
            storage=storage,
            skill_root=crossborder_skill_root,
            max_concurrency=xinghe_concurrency,
            budget_limit_micros=budget_limit_micros,
            skill_id=CROSSBORDER_DETAIL_SKILL_ID,
        )
        detail_provider = CrossborderDetailProvider(crossborder_detail_delegate)
    # Aurora v4.3 is the installed domestic detail runtime.  Its pair workflow
    # is intentionally serialized because reference-bearing edits are measured
    # to fail above one concurrent request (HTTP 413).
    from .domestic_detail_provider import DOMESTIC_DETAIL_SKILL_ID

    domestic_skill_id = DOMESTIC_DETAIL_SKILL_ID
    domestic_skill_root = default_skill_root(domestic_skill_id)
    domestic_planner = XinghePlanningCoordinator(
        session_factory,
        gateway,
        storage=storage,
        skill_root=domestic_skill_root,
        base_url_signature=base_url_signature,
        medium_price_micros=medium_price_micros,
        high_price_micros=high_price_micros,
        budget_limit_micros=budget_limit_micros,
        skill_id=domestic_skill_id,
    )
    domestic_detail_delegate = XingheImageProvider(
        session_factory,
        gateway,
        storage=storage,
        skill_root=domestic_skill_root,
        max_concurrency=1,
        budget_limit_micros=budget_limit_micros,
        skill_id=domestic_skill_id,
        revision_settings=domestic_revision_settings,
    )
    domestic_detail_provider = DomesticDetailProvider(domestic_detail_delegate)
    from .refined_detail import SKILL_ID as REFINED_DETAIL_SKILL_ID
    from .refined_detail import RefinedDetailProvider

    refined_skill_root = default_skill_root(REFINED_DETAIL_SKILL_ID)
    refined_planner = XinghePlanningCoordinator(
        session_factory,
        gateway,
        storage=storage,
        skill_root=refined_skill_root,
        base_url_signature=base_url_signature,
        medium_price_micros=medium_price_micros,
        high_price_micros=high_price_micros,
        budget_limit_micros=budget_limit_micros,
        skill_id=REFINED_DETAIL_SKILL_ID,
    )
    refined_detail_provider = RefinedDetailProvider(
        XingheImageProvider(
            session_factory,
            gateway,
            storage=storage,
            skill_root=refined_skill_root,
            max_concurrency=xinghe_concurrency,
            budget_limit_micros=budget_limit_micros,
            skill_id=REFINED_DETAIL_SKILL_ID,
        )
    )
    amazon_skill_root = default_skill_root(AMAZON_VISUAL_SUITE_SKILL_ID)
    amazon_planner = XinghePlanningCoordinator(
        session_factory,
        gateway,
        storage=storage,
        skill_root=amazon_skill_root,
        base_url_signature=base_url_signature,
        medium_price_micros=medium_price_micros,
        high_price_micros=high_price_micros,
        budget_limit_micros=budget_limit_micros,
        skill_id=AMAZON_VISUAL_SUITE_SKILL_ID,
    )
    routed_planner = DetailMarketRoutingPlanner(
        session_factory,
        international_planner,
        domestic_planner,
        amazon_planner,
        refined=refined_planner,
    )
    default_provider = build_fake_skill_runtime_provider(
        session_factory,
        delay_seconds,
    )
    local_provider: Provider = default_provider
    if xinghe_gateway is not None:
        if storage is None:
            raise ValueError("Real local-edit provider requires image storage.")
        from .masked_repair import MaskedEditGateway, MaskedRepairProvider

        local_provider = MaskedRepairProvider(
            session_factory,
            cast("MaskedEditGateway", gateway),
            storage,
            budget_limit_micros=budget_limit_micros,
            skill_root=default_masked_skill_root(),
        )
    from .remix_fast_provider import RemixFastProvider

    manifest_validator = BundledSkillManifestValidator()
    if storage is None:
        raise ValueError("Aurora main-image workflow requires image storage.")
    aurora_provider: Provider = AuroraMainImageProvider(
        session_factory,
        gateway,
        storage,
        work_root=aurora_work_root,
        execution_mode=aurora_execution_mode,
    )
    if xinghe_gateway is None:
        new_routes: dict[str, Provider] = {
            JobType.PRODUCT_IMAGE.value: default_provider,
            JobType.REFERENCE_REMIX.value: ReferenceRemixRouterProvider(
                default_provider, default_provider, UnavailableProvider()
            ),
            JobType.MODEL_POSE_TRANSFER.value: default_provider,
            JobType.CREATIVE_IMAGE.value: default_provider,
            JobType.AMAZON_VISUAL_SUITE.value: AmazonVisualSuiteProvider(
                default_provider,
                manifest_validator=manifest_validator,
            ),
            JobType.DOMESTIC_MAIN_IMAGE.value: aurora_provider,
        }
    else:

        def single_image_delegate(
            enabled: bool,
            operation: str,
            skill_id: str,
        ) -> Provider:
            if not enabled:
                return UnavailableProvider()
            if storage is None:
                raise ValueError("Real single-image provider requires image storage.")
            from .xinghe_single_image_provider import XingheSingleImageProvider

            skill_root = default_skill_root(skill_id)
            delegate_manifest_validator = BundledSkillManifestValidator(
                source_roots={skill_id: skill_root}
            )
            return XingheSingleImageProvider(
                session_factory,
                gateway,
                storage=storage,
                operation=operation,
                skill_id=skill_id,
                manifest_validator=delegate_manifest_validator.validate,
                max_concurrency=xinghe_concurrency,
            )

        new_routes = {
            JobType.PRODUCT_IMAGE.value: ProductImageProvider(
                single_image_delegate(
                    product_image_provider_enabled,
                    JobType.PRODUCT_IMAGE.value,
                    ProductImageProvider.skill_id,
                ),
                manifest_validator=manifest_validator.validate,
            ),
            JobType.REFERENCE_REMIX.value: ReferenceRemixRouterProvider(
                ReferenceRemixProvider(
                    single_image_delegate(
                        reference_remix_provider_enabled,
                        JobType.REFERENCE_REMIX.value,
                        ReferenceRemixProvider.skill_id,
                    ),
                    manifest_validator=manifest_validator.validate,
                ),
                ReferenceVisualReverseRemixProvider(
                    single_image_delegate(
                        reference_remix_provider_enabled,
                        JobType.REFERENCE_REMIX.value,
                        ReferenceVisualReverseRemixProvider.skill_id,
                    ),
                    manifest_validator=manifest_validator.validate,
                ),
                ReplicaDeliveryProvider(session_factory, gateway, storage),
                suite_fast=FastReplicaProvider(
                    session_factory, gateway, storage
                ) if reference_remix_provider_enabled else UnavailableProvider(),
                fast=AuroraRemixFastAdapter(
                    RemixFastProvider(
                        session_factory, gateway, storage=storage,
                        operation=JobType.REFERENCE_REMIX.value,
                        skill_id="aurora-remix-fast",
                        manifest_validator=manifest_validator.validate,
                        work_root=(aurora_work_root / "remix-fast"
                                   if aurora_work_root else None),
                    ) if reference_remix_provider_enabled else UnavailableProvider(),
                    manifest_validator=manifest_validator.validate,
                ),
            ),
            JobType.MODEL_POSE_TRANSFER.value: ModelPoseTransferProvider(
                single_image_delegate(
                    model_pose_transfer_provider_enabled,
                    JobType.MODEL_POSE_TRANSFER.value,
                    ModelPoseTransferProvider.skill_id,
                ),
                manifest_validator=manifest_validator.validate,
            ),
            JobType.CREATIVE_IMAGE.value: CreativeImageProvider(
                single_image_delegate(
                    creative_image_provider_enabled,
                    JobType.CREATIVE_IMAGE.value,
                    CreativeImageProvider.skill_id,
                ),
                manifest_validator=manifest_validator.validate,
            ),
            JobType.AMAZON_VISUAL_SUITE.value: AmazonVisualSuiteProvider(
                single_image_delegate(
                    amazon_visual_suite_provider_enabled,
                    JobType.AMAZON_VISUAL_SUITE.value,
                    AMAZON_VISUAL_SUITE_SKILL_ID,
                ),
                manifest_validator=manifest_validator,
            ),
            JobType.DOMESTIC_MAIN_IMAGE.value: (
                aurora_provider
                if domestic_main_image_provider_enabled
                else UnavailableProvider()
            ),
        }
    from .domestic_detail.campaign_provider import DomesticCampaignReviewProvider

    new_routes[JobType.DOMESTIC_DETAIL_REVIEW.value] = DomesticCampaignReviewProvider(
        session_factory, gateway, storage,
    )
    provider = RegisteredOperationRoutingProvider(
        default_provider,
        detail_provider,
        local_provider,
        new_routes,
        domestic_detail_provider=domestic_detail_provider,
        refined_detail_provider=refined_detail_provider,
    )
    amazon_suite_reviewer = None
    if storage is not None:
        from .amazon_visual_quality import AmazonSuiteQualityService

        amazon_suite_reviewer = AmazonSuiteQualityService(
            session_factory,
            gateway,
            storage,
            skill_root=amazon_skill_root,
            manifest_validator=manifest_validator.validate,
            max_review_rounds=2,
            enforce_delivery=True,
        )
    return ExecutionCoordinator(
        session_factory,
        provider,
        detail_planner=routed_planner,
        campaign_planner=AuroraMainImagePlanner(
            session_factory,
            storage=storage,
            gateway=gateway,
            work_root=aurora_work_root,
            execution_mode=aurora_execution_mode,
        ),
        max_concurrency=xinghe_concurrency,
        # Detail-pack behavior remains unchanged. Amazon follows its own Skill
        # incomplete-execution and regenerate-on-invalid-output contract.
        max_detail_retries=0,
        max_amazon_retries=2,
        # Universal product images use one conservative retry for transient
        # gateway/output failures; semantic QA remains visible for review.
        max_product_image_retries=1,
        max_aurora_retries=aurora_max_transient_retries,
        aurora_retry_backoff_base_seconds=aurora_retry_backoff_base_seconds,
        aurora_retry_backoff_max_seconds=aurora_retry_backoff_max_seconds,
        artifact_storage=storage,
        amazon_suite_reviewer=amazon_suite_reviewer,
    )


def _amazon_group_ordinal_bounds(ordinal: int) -> tuple[int, int]:
    if ordinal <= 0:
        return 0, 0
    if ordinal <= 6:
        return 1, 6
    if ordinal <= 13:
        return 7, 13
    return 14, 20


def _lease_expired(expires_at: datetime, now: datetime) -> bool:
    """Compare PostgreSQL-aware and SQLite-naive timestamps consistently."""

    if expires_at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return expires_at <= now


def _usage_count(raw_usage: dict[str, object], key: str) -> int | None:
    value = raw_usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
