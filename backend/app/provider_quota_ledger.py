"""Append-only reconciliation for durable real-provider quota reservations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypeGuard

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import (
    TERMINAL_JOB_STATUSES,
    JobStatus,
    JobType,
    UsageCompleteness,
)
from .models import (
    AuditEventRecord,
    JobPlanRecord,
    JobRecord,
    UsageEventRecord,
)

RESERVATION_ACTION = "provider.beta_job_reserved"
ATTEMPTS_ACTION = "provider.beta_job_attempts_recorded"
SETTLEMENT_ACTION = "provider.beta_job_settled"
RELEASE_ACTION = "provider.beta_job_released"
LEDGER_VERSION = 2
LEGACY_AMAZON_PREPLANNER_CUTOFF = datetime(2026, 8, 4, 7, 12, tzinfo=UTC)


class ProviderQuotaLedgerError(RuntimeError):
    """Raised when durable quota history cannot be reconciled safely."""


def reconcile_and_total(
    session: Session,
    *,
    workspace_id: str,
    day_start: datetime,
    month_start: datetime,
) -> tuple[int, int, int]:
    """Settle terminal reservations and return daily/monthly effective exposure."""

    reservations = session.scalars(
        select(AuditEventRecord)
        .where(
            AuditEventRecord.workspace_id == workspace_id,
            AuditEventRecord.action == RESERVATION_ACTION,
            AuditEventRecord.occurred_at >= month_start,
        )
        .order_by(AuditEventRecord.id)
    ).all()
    seen_job_ids: set[str] = set()
    settlements_by_job: dict[str, AuditEventRecord | None] = {}
    for reservation in reservations:
        job_id = reservation.resource_id
        if not isinstance(job_id, str) or not job_id:
            raise ProviderQuotaLedgerError("A quota reservation has no job id.")
        if reservation.request_id != job_id:
            raise ProviderQuotaLedgerError(
                "A quota reservation has an inconsistent request id."
            )
        if job_id in seen_job_ids:
            raise ProviderQuotaLedgerError("A job has duplicate quota reservations.")
        seen_job_ids.add(job_id)
        job = session.get(JobRecord, job_id)
        if job is None:
            raise ProviderQuotaLedgerError(
                "A quota reservation references a missing job."
            )
        settlement = _settlement_for_reservation(session, reservation)
        settlements_by_job[job_id] = settlement
        if settlement is not None:
            if JobStatus(job.status) not in TERMINAL_JOB_STATUSES:
                raise ProviderQuotaLedgerError(
                    "A non-terminal job has a quota settlement."
                )
            continue
        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
            _append_settlement(session, job, reservation)
            session.flush()
            settlements_by_job[job_id] = _settlement_for_reservation(
                session,
                reservation,
            )

    daily_cost = 0
    monthly_cost = 0
    daily_requests = 0
    for reservation in reservations:
        cost, requests = _reservation_values(reservation)
        settled = settlements_by_job[reservation.resource_id or ""]
        if settled is not None:
            details = _details(settled)
            cost = _required_int(details, "charged_cost_micros")
            requests = _required_int(details, "actual_provider_request_count")
        monthly_cost += cost
        if _aware_utc(reservation.occurred_at) >= day_start:
            daily_cost += cost
            daily_requests += requests
    return daily_cost, monthly_cost, daily_requests


def _append_settlement(
    session: Session,
    job: JobRecord,
    reservation: AuditEventRecord,
) -> None:
    reserved_cost, reserved_requests = _reservation_values(reservation)
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
    for usage in usage_events:
        event_requests = usage_request_count(usage)
        event_charge, basis = _usage_charge(job, usage, event_requests)
        requests += event_requests
        charge += event_charge
        actual_cost_count += int(basis == "actual")
        fallback_count += int(basis != "actual")
        sources.append(
            {
                "source": "usage_event",
                "usage_event_id": usage.id,
                "attempt_id": usage.attempt_id,
                "provider_request_count": event_requests,
                "charged_cost_micros": event_charge,
                "cost_basis": basis,
                "actual_cost_reported": usage.actual_cost_micros is not None,
            }
        )

    planner_sources, planner_requests, planner_charge, planner_actual = (
        _planner_charges(
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
        "ledger_version": LEDGER_VERSION,
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
    if charge > reserved_cost:
        common["observed_cost_over_estimate_micros"] = charge - reserved_cost
    if requests > reserved_requests:
        common["observed_requests_over_estimate"] = requests - reserved_requests
    _add_event(session, job, ATTEMPTS_ACTION, {**common, "attempts": sources})
    _add_event(session, job, SETTLEMENT_ACTION, common)
    _add_event(
        session,
        job,
        RELEASE_ACTION,
        {
            **common,
            "released_cost_micros": max(0, reserved_cost - charge),
            "released_request_count": max(0, reserved_requests - requests),
            "release_reason": f"terminal_{job.status}",
        },
    )


def _planner_charges(
    session: Session,
    job: JobRecord,
    reservation: AuditEventRecord,
    reserved_cost: int,
    reserved_requests: int,
    observed_usage_requests: int,
    usage_events: Sequence[UsageEventRecord],
) -> tuple[list[dict[str, Any]], int, int, int]:
    if job.type not in {
        JobType.DETAIL_PACK.value,
        JobType.AMAZON_VISUAL_SUITE.value,
    }:
        return [], 0, 0, 0
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if plan is None:
        if _was_rejected_before_provider(session, job):
            return [], 0, 0, 0
        if _is_legacy_preplanner_amazon_reservation(
            job,
            reservation,
            reserved_cost,
            reserved_requests,
            usage_events,
        ):
            if observed_usage_requests != reserved_requests:
                raise ProviderQuotaLedgerError(
                    "Legacy pre-planner Amazon usage is incomplete."
                )
            return [], 0, 0, 0
        raise ProviderQuotaLedgerError(
            "A terminal detail job has no provider-attempt evidence."
        )
    payload = plan.payload if isinstance(plan.payload, dict) else {}
    usage = payload.get("usage")
    planner = usage.get("planner") if isinstance(usage, dict) else None
    if planner is None:
        # A cancellation can race the planner lease before the provider call
        # writes its attempt metadata.  That terminal state is auditable from
        # the durable plan snapshot (cancelled + no image requests) and must
        # settle at zero instead of poisoning every later quota check.  Keep a
        # zero-value source in the settlement so the repair remains visible.
        image_usage = usage.get("image") if isinstance(usage, dict) else None
        image_requests = (
            image_usage.get("requests") if isinstance(image_usage, dict) else None
        )
        if (
            job.status == JobStatus.CANCELLED.value
            and payload.get("state") == "cancelled"
            and not usage_events
            and (image_requests is None or image_requests == [])
            and observed_usage_requests == 0
        ):
            return (
                [
                    {
                        "source": "planner_unattempted",
                        "provider_request_count": 0,
                        "charged_cost_micros": 0,
                        "cost_basis": "cancelled_before_planner",
                        "actual_cost_reported": False,
                    }
                ],
                0,
                0,
                0,
            )
        if (
            payload.get("state") == "planner_reserved"
            and job.error_code == "PLANNER_OUTCOME_UNKNOWN"
        ):
            groups = _reservation_group_count(reservation, job)
            unit_reserve = _planner_unit_reserve(reservation, job, reserved_cost)
            return (
                [
                    {
                        "source": "planner_unknown",
                        "planner_ordinal": ordinal,
                        "provider_request_count": 1,
                        "charged_cost_micros": unit_reserve,
                        "cost_basis": "conservative_unknown",
                        "actual_cost_reported": False,
                    }
                    for ordinal in range(groups)
                ],
                groups,
                groups * unit_reserve,
                0,
            )
        raise ProviderQuotaLedgerError("Planner attempt metadata is missing.")
    if not isinstance(planner, list):
        raise ProviderQuotaLedgerError("Planner usage metadata is invalid.")
    recorded_count = payload.get("planner_request_count")
    if recorded_count is not None and (
        not _is_non_negative_int(recorded_count) or int(recorded_count) != len(planner)
    ):
        raise ProviderQuotaLedgerError("Planner attempt count is inconsistent.")
    unit_reserve = _planner_unit_reserve(reservation, job, reserved_cost)
    sources: list[dict[str, Any]] = []
    total = 0
    actual_count = 0
    for ordinal, value in enumerate(planner):
        if not isinstance(value, dict):
            raise ProviderQuotaLedgerError("Planner usage metadata is invalid.")
        actual = value.get("actual_cost_micros")
        if _is_non_negative_int(actual):
            item_charge = int(actual)
            item_basis = "actual"
            actual_count += 1
        else:
            item_charge = unit_reserve
            item_basis = "estimated_fallback"
        total += item_charge
        sources.append(
            {
                "source": "planner",
                "planner_ordinal": ordinal,
                "provider_request_count": 1,
                "charged_cost_micros": item_charge,
                "cost_basis": item_basis,
                "actual_cost_reported": item_basis == "actual",
            }
        )
    return sources, len(planner), total, actual_count


def _is_legacy_preplanner_amazon_reservation(
    job: JobRecord,
    reservation: AuditEventRecord,
    reserved_cost: int,
    reserved_requests: int,
    usage_events: Sequence[UsageEventRecord],
) -> bool:
    """Recognize complete image-only Amazon jobs from before planner gating."""

    if (
        job.type != JobType.AMAZON_VISUAL_SUITE.value
        or _aware_utc(reservation.occurred_at) >= LEGACY_AMAZON_PREPLANNER_CUTOFF
        or job.retry_of_job_id is not None
        or job.root_job_id is not None
        or job.status != JobStatus.COMPLETED.value
        or job.usage_completeness != UsageCompleteness.COMPLETE.value
    ):
        return False
    expected = int(job.expected_item_count or 0)
    if (
        expected <= 0
        or int(job.succeeded_item_count or 0) != expected
        or int(job.failed_item_count or 0) != 0
        or int(job.api_request_count or 0) != reserved_requests
        or reserved_requests != expected
        or len(usage_events) != reserved_requests
    ):
        return False
    details = _details(reservation)
    ledger_version = details.get("ledger_version")
    group_count = details.get("group_count")
    image_budget = details.get("image_budget_micros")
    if not (
        _is_non_negative_int(ledger_version)
        and int(ledger_version) == LEDGER_VERSION
        and details.get("job_type") == JobType.AMAZON_VISUAL_SUITE.value
        and _is_non_negative_int(group_count)
        and int(group_count) == 0
        and _is_positive_int(details.get("planner_reserve_micros"))
        and _is_non_negative_int(image_budget)
        and int(image_budget) == reserved_cost
    ):
        return False
    item_ids = [usage.job_item_id for usage in usage_events]
    attempt_ids = [usage.attempt_id for usage in usage_events]
    return (
        all(
            usage.operation == JobType.AMAZON_VISUAL_SUITE.value
            and usage_request_count(usage) == 1
            for usage in usage_events
        )
        and all(isinstance(value, str) and value for value in item_ids)
        and all(isinstance(value, str) and value for value in attempt_ids)
        and len(set(item_ids)) == reserved_requests
        and len(set(attempt_ids)) == reserved_requests
    )


def _was_rejected_before_provider(session: Session, job: JobRecord) -> bool:
    event = session.scalar(
        select(AuditEventRecord)
        .where(
            AuditEventRecord.workspace_id == job.workspace_id,
            AuditEventRecord.resource_id == job.id,
            AuditEventRecord.action == "xinghe.planning_rejected",
        )
        .order_by(AuditEventRecord.id.desc())
        .limit(1)
    )
    if event is None:
        return False
    details = _details(event)
    value = details.get("provider_call_count")
    if not _is_non_negative_int(value):
        raise ProviderQuotaLedgerError("Planning rejection audit is invalid.")
    return int(value) == 0


def _reservation_group_count(
    reservation: AuditEventRecord,
    job: JobRecord,
) -> int:
    details = _details(reservation)
    groups = details.get("group_count")
    if _is_positive_int(groups):
        return int(groups)
    configuration = job.configuration if isinstance(job.configuration, dict) else {}
    input_value = configuration.get("input")
    product_groups = (
        input_value.get("product_groups") if isinstance(input_value, dict) else None
    )
    if not isinstance(product_groups, list) or not product_groups:
        raise ProviderQuotaLedgerError("Planner group count cannot be derived safely.")
    return len(product_groups)


def _planner_unit_reserve(
    reservation: AuditEventRecord,
    job: JobRecord,
    reserved_cost: int,
) -> int:
    details = _details(reservation)
    configured = details.get("planner_reserve_micros")
    if _is_non_negative_int(configured):
        return int(configured)
    groups = details.get("group_count")
    if not _is_positive_int(groups):
        configuration = job.configuration if isinstance(job.configuration, dict) else {}
        input_value = configuration.get("input")
        product_groups = (
            input_value.get("product_groups") if isinstance(input_value, dict) else None
        )
        groups = len(product_groups) if isinstance(product_groups, list) else 0
    image_budget = details.get("image_budget_micros")
    if not _is_non_negative_int(image_budget):
        image_budget = int(job.estimated_cost_max_micros or 0)
    remainder = reserved_cost - 2 * int(image_budget)
    if not _is_positive_int(groups) or remainder < 0 or remainder % int(groups):
        raise ProviderQuotaLedgerError(
            "Legacy planner reserve cannot be derived safely."
        )
    return remainder // int(groups)


def usage_request_count(usage: UsageEventRecord) -> int:
    raw = usage.raw_usage if isinstance(usage.raw_usage, dict) else {}
    value = raw.get("network_requests")
    return int(value) if _is_non_negative_int(value) else 1


def _usage_charge(
    job: JobRecord,
    usage: UsageEventRecord,
    request_count: int,
) -> tuple[int, str]:
    if _is_non_negative_int(usage.actual_cost_micros):
        return int(usage.actual_cost_micros), "actual"
    if not _is_non_negative_int(usage.estimated_cost_micros):
        # Older failed provider attempts (and some relays that reject a
        # request before returning usage) may have neither actual nor unit
        # estimated cost.  The job reservation is already fail-closed, so use
        # its conservative envelope to settle the attempt and allow retries;
        # do not strand the whole job behind a historical missing field.
        fallback = int(job.estimated_cost_max_micros or 0)
        if _is_positive_int(fallback):
            return fallback, "conservative_unknown"
        raise ProviderQuotaLedgerError(
            "Observed provider usage has no conservative cost."
        )
    estimated = int(usage.estimated_cost_micros)
    if job.type == JobType.LOCAL_EDIT.value:
        raw = usage.raw_usage if isinstance(usage.raw_usage, dict) else {}
        metrics_value = raw.get("metrics")
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        transport_attempts = metrics.get("transport_attempts")
        if _is_positive_int(transport_attempts) and int(transport_attempts) > 1:
            estimated += (int(transport_attempts) - 1) * int(
                job.estimated_cost_max_micros or 0
            )
    elif request_count > 1:
        estimated *= request_count
    return estimated, "estimated_fallback"


def _reservation_values(event: AuditEventRecord) -> tuple[int, int]:
    details = _details(event)
    return (
        _required_int(details, "estimated_cost_micros"),
        _required_int(details, "provider_request_count"),
    )


def _settlement_for_reservation(
    session: Session,
    reservation: AuditEventRecord,
) -> AuditEventRecord | None:
    job_id = reservation.resource_id
    if not isinstance(job_id, str) or not job_id:
        raise ProviderQuotaLedgerError("A quota reservation has no job id.")
    settlements = session.scalars(
        select(AuditEventRecord)
        .where(
            AuditEventRecord.workspace_id == reservation.workspace_id,
            AuditEventRecord.action == SETTLEMENT_ACTION,
            AuditEventRecord.resource_id == job_id,
        )
        .order_by(AuditEventRecord.id)
    ).all()
    if len(settlements) > 1:
        raise ProviderQuotaLedgerError("A job has duplicate quota settlements.")
    if not settlements:
        return None
    settlement = settlements[0]
    _validate_settlement(reservation, settlement)
    return settlement


def _validate_settlement(
    reservation: AuditEventRecord,
    settlement: AuditEventRecord,
) -> None:
    reserved_cost, reserved_requests = _reservation_values(reservation)
    details = _details(settlement)
    if settlement.request_id != reservation.request_id:
        raise ProviderQuotaLedgerError(
            "A quota settlement has an inconsistent request id."
        )
    reservation_event_id = details.get("reservation_event_id")
    if (
        not _is_positive_int(reservation_event_id)
        or int(reservation_event_id) != reservation.id
    ):
        raise ProviderQuotaLedgerError(
            "A quota settlement references the wrong reservation."
        )
    if _required_int(details, "reserved_cost_micros") != reserved_cost:
        raise ProviderQuotaLedgerError("A quota settlement changed the reserved cost.")
    if _required_int(details, "reserved_request_count") != reserved_requests:
        raise ProviderQuotaLedgerError(
            "A quota settlement changed the reserved request count."
        )
    # Validate recorded values, without treating estimates as spending authority.
    _required_int(details, "charged_cost_micros")
    _required_int(details, "actual_provider_request_count")


def _add_event(
    session: Session,
    job: JobRecord,
    action: str,
    details: dict[str, Any],
) -> None:
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            action=action,
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details=details,
        )
    )


def _details(event: AuditEventRecord) -> Mapping[str, Any]:
    if not isinstance(event.details, dict):
        raise ProviderQuotaLedgerError("A provider quota event is invalid.")
    return event.details


def _required_int(details: Mapping[str, Any], key: str) -> int:
    value = details.get(key)
    if not _is_non_negative_int(value):
        raise ProviderQuotaLedgerError(f"Provider quota field {key} is invalid.")
    return int(value)


def _is_non_negative_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: object) -> TypeGuard[int]:
    return _is_non_negative_int(value) and int(value) > 0


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
