import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, cast, get_args
from uuid import uuid4

from fastapi import status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from .aurora_execution_modes import is_v20_aurora_mode
from .domain import (
    TERMINAL_JOB_STATUSES,
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    AttemptStatus,
    CreditReservationStatus,
    IdempotencyStatus,
    JobAssetDirection,
    JobItemStatus,
    JobStatus,
    JobType,
    OutboxStatus,
    RetryScope,
    UsageCompleteness,
    can_transition,
)
from .errors import ApiError
from .models import (
    AmazonSuiteReviewAttemptRecord,
    AssetRecord,
    CreditLedgerEntryRecord,
    CreditReservationRecord,
    IdempotencyRequestRecord,
    JobAssetRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    OutboxEventRecord,
    WorkspaceCreditAccountRecord,
    WorkspaceLimitRecord,
    utc_now,
)
from .schemas_v1 import (
    AssetResponse,
    CostSummary,
    JobAssetResponse,
    JobDetailResponse,
    JobItemResponse,
    JobListItemResponse,
    MoneyAmount,
    PlanResponse,
    ProductImageTaskType,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


_quota_enabled: bool = True
_credit_enforcement_enabled: bool = True
_credit_pilot_workspace_ids: frozenset[str] = frozenset()


def set_quota_enabled(enabled: bool) -> None:
    global _quota_enabled
    _quota_enabled = enabled


def quota_enabled() -> bool:
    return _quota_enabled


def set_credit_enforcement_enabled(enabled: bool) -> None:
    """Toggle membership-style workspace balance enforcement.

    Credit reservations and ledger entries remain active whenever provider quota
    tracking is enabled. Disabling enforcement only turns reservations into
    accounting-only records so internal beta usage is never rejected for an
    exhausted workspace balance.
    """

    global _credit_enforcement_enabled
    _credit_enforcement_enabled = enabled


def set_credit_pilot_workspaces(workspace_ids: Sequence[str]) -> None:
    """Server-only workspace allowlist; never populated from customer input."""
    global _credit_pilot_workspace_ids
    _credit_pilot_workspace_ids = frozenset(workspace_ids)


def credit_enforcement_enabled(workspace_id: str | None = None) -> bool:
    return _credit_enforcement_enabled or workspace_id in _credit_pilot_workspace_ids


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def require_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "IDEMPOTENCY_KEY_REQUIRED",
            "该操作必须提供 Idempotency-Key。",
        )
    key = value.strip()
    if len(key) > 128:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "VALIDATION_ERROR",
            "Idempotency-Key 不能超过 128 个字符。",
        )
    return key


def claim_idempotency(
    session: Session,
    *,
    workspace_id: str,
    actor_user_id: str,
    method: str,
    route_template: str,
    idempotency_key: str,
    request_hash: str,
) -> tuple[IdempotencyRequestRecord, bool]:
    existing = session.scalar(
        select(IdempotencyRequestRecord).where(
            IdempotencyRequestRecord.workspace_id == workspace_id,
            IdempotencyRequestRecord.method == method,
            IdempotencyRequestRecord.route_template == route_template,
            IdempotencyRequestRecord.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "IDEMPOTENCY_KEY_REUSED",
                "该幂等键已用于不同请求。",
            )
        if existing.status == IdempotencyStatus.COMPLETED.value:
            return existing, True
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "IDEMPOTENCY_IN_PROGRESS",
            "相同请求正在处理中，请稍后查询结果。",
        )

    record = IdempotencyRequestRecord(
        id=new_id("idem"),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        method=method,
        route_template=route_template,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status=IdempotencyStatus.IN_PROGRESS.value,
        expires_at=utc_now() + timedelta(days=7),
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        concurrent = session.scalar(
            select(IdempotencyRequestRecord).where(
                IdempotencyRequestRecord.workspace_id == workspace_id,
                IdempotencyRequestRecord.method == method,
                IdempotencyRequestRecord.route_template == route_template,
                IdempotencyRequestRecord.idempotency_key == idempotency_key,
            )
        )
        if concurrent is None:
            raise
        if concurrent.request_hash != request_hash:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "IDEMPOTENCY_KEY_REUSED",
                "该幂等键已用于不同请求。",
            ) from None
        if concurrent.status == IdempotencyStatus.COMPLETED.value:
            return concurrent, True
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "IDEMPOTENCY_IN_PROGRESS",
            "相同请求正在处理中，请稍后查询结果。",
        ) from None
    return record, False


def complete_idempotency(
    record: IdempotencyRequestRecord,
    *,
    resource_type: str,
    resource_id: str,
    response_status: int,
) -> None:
    record.status = IdempotencyStatus.COMPLETED.value
    record.resource_type = resource_type
    record.resource_id = resource_id
    record.response_status = response_status
    record.response_body = {"resource_id": resource_id}


def asset_response(asset: AssetRecord) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        workspace_id=asset.workspace_id,
        media_kind=AssetMediaKind(asset.media_kind),
        origin_kind=AssetOriginKind(asset.origin_kind),
        lifecycle_status=AssetLifecycleStatus(asset.lifecycle_status),
        original_filename=asset.original_filename,
        content_type=asset.content_type,
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        sha256=asset.sha256,
        created_at=asset.created_at,
        expires_at=asset.expires_at,
    )


def asset_is_available() -> Any:
    now = utc_now()
    return and_(
        AssetRecord.deleted_at.is_(None),
        or_(
            AssetRecord.retention_class == "system_persistent",
            AssetRecord.expires_at.is_(None),
            AssetRecord.expires_at > now,
        ),
    )


def job_history_is_available() -> Any:
    now = utc_now()
    return and_(
        JobRecord.history_deleted_at.is_(None),
        or_(
            JobRecord.history_expires_at.is_(None),
            JobRecord.history_expires_at > now,
        ),
    )


def cost_summary(job: JobRecord) -> CostSummary:
    def money(value: int | None) -> MoneyAmount | None:
        if value is None or job.currency is None:
            return None
        return MoneyAmount(micros=str(value), currency=job.currency)

    return CostSummary(
        estimated_min=money(job.estimated_cost_min_micros),
        estimated_max=money(job.estimated_cost_max_micros),
        actual=money(job.actual_cost_micros),
        estimated_credits=job.estimated_credits,
        actual_credits=job.actual_credits,
        usage_completeness=UsageCompleteness(job.usage_completeness),
    )


_AMAZON_REVIEW_INFRASTRUCTURE_FAILURE = "review_infrastructure_failure"


def _is_amazon_review_infrastructure_blocker(item: JobItemRecord) -> bool:
    details = item.error_details
    return bool(
        item.status == JobItemStatus.FAILED.value
        and isinstance(details, dict)
        and details.get("failure_kind") == _AMAZON_REVIEW_INFRASTRUCTURE_FAILURE
        and details.get("image_quality_failed") is False
        and details.get("generated_asset_preserved") is True
    )


def _amazon_review_retry_state(
    job: JobRecord, items: Sequence[JobItemRecord]
) -> tuple[set[str], bool]:
    if job.type != JobType.AMAZON_VISUAL_SUITE.value:
        return set(), False
    blocker_ids = {
        item.id for item in items if _is_amazon_review_infrastructure_blocker(item)
    }
    if not blocker_ids:
        return set(), False
    retryable_statuses = {
        JobItemStatus.FAILED.value,
        JobItemStatus.CANCELLED.value,
    }
    has_retryable_non_blocker = any(
        item.status in retryable_statuses and item.id not in blocker_ids
        for item in items
    )
    return blocker_ids, has_retryable_non_blocker


def _amazon_review_retry_is_available(job: JobRecord) -> bool:
    if job.type != JobType.AMAZON_VISUAL_SUITE.value:
        return True
    session = object_session(job)
    if session is None:
        return True
    items = session.scalars(
        select(JobItemRecord).where(JobItemRecord.job_id == job.id)
    ).all()
    blocker_ids, has_retryable_non_blocker = _amazon_review_retry_state(job, items)
    return not blocker_ids or has_retryable_non_blocker


def _raise_amazon_review_retry_not_applicable(blocker_ids: set[str]) -> None:
    raise ApiError(
        status.HTTP_409_CONFLICT,
        "AMAZON_REVIEW_INFRASTRUCTURE_RETRY_NOT_APPLICABLE",
        (
            "Amazon 图片已成功生成并保留；失败仅来自自动视觉审核基础设施。"
            "为避免重复生成和收费，不能通过图片重试重新生成这些条目。"
        ),
        {
            "failure_kind": _AMAZON_REVIEW_INFRASTRUCTURE_FAILURE,
            "generated_asset_preserved": True,
            "blocked_item_ids": sorted(blocker_ids),
        },
    )


def allowed_actions(job: JobRecord) -> list[str]:
    if job.type == "product_replica":
        return []
    current = JobStatus(job.status)
    if job.type == JobType.DOMESTIC_DETAIL_REVIEW.value:
        return (
            ["cancel"]
            if current
            in {
                JobStatus.QUEUED,
                JobStatus.PROCESSING,
                JobStatus.CANCELLING,
            }
            else []
        )
    actions: list[str] = []
    if current == JobStatus.DRAFT:
        actions.extend(["edit", "submit", "cancel"])
    elif current in {
        JobStatus.QUEUED,
        JobStatus.PLANNING,
        JobStatus.AWAITING_CONFIRMATION,
        JobStatus.PROCESSING,
    }:
        actions.append("cancel")
    elif current == JobStatus.AWAITING_FINAL_REVIEW:
        actions.extend(["finalize", "cancel"])
    if current == JobStatus.AWAITING_CONFIRMATION:
        if (
            job.type == JobType.DETAIL_PACK.value
            and job.stage == "intake_confirmation_required"
        ):
            actions.append("resolve_intake")
        else:
            from .amazon_auto_start import eligible as automatic_amazon
            from .domestic_detail.auto_start import eligible

            if not eligible(job) and not automatic_amazon(job):
                actions.extend(["edit_plan", "confirm_plan"])
    if current in {
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    } and _amazon_review_retry_is_available(job):
        actions.append("retry")
    if current in TERMINAL_JOB_STATUSES:
        actions.append("clone")
    return actions


_PRODUCT_IMAGE_TASK_PRESETS = frozenset(get_args(ProductImageTaskType))


def job_task_preset(job: JobRecord) -> ProductImageTaskType | None:
    """Return only a supported product-image preset for public job responses."""
    if job.type != JobType.PRODUCT_IMAGE.value:
        return None
    settings = job.configuration.get("settings")
    task_preset = settings.get("task_type") if isinstance(settings, dict) else None
    if task_preset not in _PRODUCT_IMAGE_TASK_PRESETS:
        return None
    return cast(ProductImageTaskType, task_preset)


def _is_audit_only_generated_set(
    job: JobRecord,
    items: Sequence[JobItemRecord],
    output_asset_count: int,
) -> bool:
    """Treat the legacy synthetic set-audit marker as a QA warning only."""

    return bool(
        job.type == JobType.DOMESTIC_MAIN_IMAGE.value
        and job.status == JobStatus.PARTIAL.value
        and output_asset_count == job.expected_item_count == 6
        and any(item.error_code == "AURORA_SET_AUDIT_FAILED" for item in items)
        and all(
            item.status in {JobItemStatus.SUCCEEDED.value, JobItemStatus.REUSED.value}
            or item.error_code == "AURORA_SET_AUDIT_FAILED"
            for item in items
        )
    )


def job_list_response(
    job: JobRecord,
    *,
    output_asset_count: int = 0,
    primary_output_asset_id: str | None = None,
    generated_set_complete: bool = False,
) -> JobListItemResponse:
    status = JobStatus.COMPLETED if generated_set_complete else JobStatus(job.status)
    return JobListItemResponse(
        id=job.id,
        workspace_id=job.workspace_id,
        type="product_replica" if job.type == "product_replica" else JobType(job.type),
        status=status,
        stage="completed" if generated_set_complete else job.stage,
        display_name=job.display_name,
        task_preset=job_task_preset(job),
        progress=job.progress,
        expected_item_count=job.expected_item_count,
        succeeded_item_count=(
            job.expected_item_count
            if generated_set_complete
            else job.succeeded_item_count
        ),
        failed_item_count=0 if generated_set_complete else job.failed_item_count,
        output_asset_count=output_asset_count,
        primary_output_asset_id=primary_output_asset_id,
        allowed_actions=["clone"] if generated_set_complete else allowed_actions(job),
        cost=cost_summary(job),
        created_at=job.created_at,
        updated_at=job.updated_at,
        history_expires_at=getattr(job, "history_expires_at", None),
    )


def job_detail_response(session: Session, job: JobRecord) -> JobDetailResponse:
    items = session.scalars(
        select(JobItemRecord)
        .where(
            JobItemRecord.workspace_id == job.workspace_id,
            JobItemRecord.job_id == job.id,
        )
        .order_by(JobItemRecord.ordinal)
    ).all()
    assets = session.scalars(
        select(JobAssetRecord)
        .join(
            AssetRecord,
            and_(
                AssetRecord.workspace_id == JobAssetRecord.workspace_id,
                AssetRecord.id == JobAssetRecord.asset_id,
            ),
        )
        .where(
            JobAssetRecord.workspace_id == job.workspace_id,
            JobAssetRecord.job_id == job.id,
            asset_is_available(),
        )
        .order_by(JobAssetRecord.ordinal)
    ).all()
    deliverable_outputs = [
        relation
        for relation in assets
        if relation.direction == JobAssetDirection.OUTPUT.value
        and relation.is_deliverable
    ]
    generated_set_complete = _is_audit_only_generated_set(
        job, items, len(deliverable_outputs)
    )
    base = job_list_response(
        job,
        output_asset_count=len(deliverable_outputs),
        primary_output_asset_id=(
            deliverable_outputs[0].asset_id if deliverable_outputs else None
        ),
        generated_set_complete=generated_set_complete,
    ).model_dump()
    plan = session.scalar(
        select(JobPlanRecord)
        .where(
            JobPlanRecord.workspace_id == job.workspace_id,
            JobPlanRecord.job_id == job.id,
        )
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    plan_payload = plan.payload if plan is not None else None
    if (
        plan is not None
        and plan.schema_version == "aurora-main-image-native-fast-plan-v1"
    ):
        from .aurora_main_image_native_fast import public_payload

        plan_payload = public_payload(plan.payload)
    if plan is not None and plan.schema_version == "aurora-main-image-plan-v2":
        from .aurora_main_image_planner import public_aurora_plan_payload

        plan_payload = public_aurora_plan_payload(plan.payload)
    return JobDetailResponse(
        **base,
        configuration={
            key: value for key, value in job.configuration.items()
            if key != "_detail_fast_invocations" and not key.startswith("_main_image_fast")
        },
        configuration_schema=job.configuration_schema,
        prompt_version=job.prompt_version,
        strategy_version=job.strategy_version,
        release_version=job.release_version,
        root_job_id=job.root_job_id,
        retry_of_job_id=job.retry_of_job_id,
        retry_scope=job.retry_scope,
        state_version=job.state_version,
        error_code=job.error_code,
        error_message=job.error_message,
        error_details=job.error_details,
        api_request_count=job.api_request_count,
        cancel_requested_at=job.cancel_requested_at,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        items=[
            JobItemResponse(
                id=item.id,
                item_key=item.item_key,
                ordinal=item.ordinal,
                status=JobItemStatus(item.status),
                stage=item.stage,
                quality=item.quality,
                result_source=item.result_source,
                error_code=item.error_code,
                error_message=item.error_message,
                error_details=item.error_details,
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item in items
        ],
        assets=[
            JobAssetResponse(
                asset_id=relation.asset_id,
                job_item_id=relation.job_item_id,
                direction=relation.direction,
                role=relation.role,
                ordinal=relation.ordinal,
                is_deliverable=relation.is_deliverable,
                validation=relation.validation,
            )
            for relation in assets
        ],
        plan=(
            PlanResponse(
                id=plan.id,
                revision=plan.revision,
                schema_version=plan.schema_version,
                payload=plan_payload or {},
                validation_status=plan.validation_status,
                validation_errors=plan.validation_errors,
                confirmed_at=plan.confirmed_at,
            )
            if plan is not None
            else None
        ),
    )


def get_workspace_asset(
    session: Session, workspace_id: str, asset_id: str
) -> AssetRecord:
    asset = session.scalar(
        select(AssetRecord).where(
            AssetRecord.workspace_id == workspace_id,
            AssetRecord.id == asset_id,
            asset_is_available(),
        )
    )
    if asset is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "ASSET_NOT_FOUND",
            "未找到指定素材。",
        )
    return asset


def get_ready_assets(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> dict[str, AssetRecord]:
    assets = {
        item.id: item
        for item in session.scalars(
            select(AssetRecord).where(
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.id.in_(asset_ids),
                AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
                asset_is_available(),
            )
        ).all()
    }
    missing = [asset_id for asset_id in asset_ids if asset_id not in assets]
    if missing:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "INPUT_ASSET_NOT_FOUND",
            "部分输入素材不存在、不属于当前工作区或尚未就绪。",
            {"asset_ids": missing},
        )
    return assets


def get_workspace_job(
    session: Session,
    workspace_id: str,
    job_id: str,
    *,
    for_update: bool = False,
) -> JobRecord:
    statement = select(JobRecord).where(
        JobRecord.workspace_id == workspace_id,
        JobRecord.id == job_id,
        job_history_is_available(),
    )
    if for_update:
        statement = statement.with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "JOB_NOT_FOUND",
            "未找到指定任务。",
        )
    if for_update and job.type == "product_replica":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "LEGACY_JOB_READ_ONLY",
            "旧版商品复刻任务支持查看与下载；如需重新创作，请使用一键复刻工作台。",
        )
    return job


def fence_amazon_suite_review_attempts(
    session: Session,
    job: JobRecord,
) -> None:
    """Invalidate live suite-review leases once a job stops accepting QA."""

    if job.type != JobType.AMAZON_VISUAL_SUITE.value:
        return
    current = JobStatus(job.status)
    if current != JobStatus.CANCELLING and current not in TERMINAL_JOB_STATUSES:
        return

    with session.no_autoflush:
        attempts = session.scalars(
            select(AmazonSuiteReviewAttemptRecord)
            .where(
                AmazonSuiteReviewAttemptRecord.workspace_id == job.workspace_id,
                AmazonSuiteReviewAttemptRecord.job_id == job.id,
                AmazonSuiteReviewAttemptRecord.status == AttemptStatus.RUNNING.value,
            )
            .with_for_update()
        ).all()
    if not attempts:
        return

    cancelled = current in {JobStatus.CANCELLING, JobStatus.CANCELLED}
    now = utc_now()
    for attempt in attempts:
        attempt.status = (
            AttemptStatus.CANCELLED.value if cancelled else AttemptStatus.FAILED.value
        )
        # Rotate the token as well as closing the row so a response already in
        # flight cannot retain ownership through a stale in-memory lease.
        attempt.lease_token = uuid4().hex
        attempt.leased_by = "job-status-fence"
        attempt.lease_expires_at = now
        attempt.error_code = (
            "AMAZON_SUITE_REVIEW_CANCELLED"
            if cancelled
            else "AMAZON_SUITE_REVIEW_JOB_TERMINAL"
        )
        attempt.error_message = (
            "The Amazon suite review was fenced because the job was cancelled."
            if cancelled
            else "The Amazon suite review was fenced after the job became terminal."
        )
        attempt.finished_at = now


def transition_job(
    job: JobRecord,
    target: JobStatus,
    *,
    stage: str | None = None,
    progress: int | None = None,
) -> None:
    current = JobStatus(job.status)
    if current == target:
        return
    if not can_transition(current, target):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INVALID_STATE_TRANSITION",
            "任务状态不能执行该转换。",
            {"from": current.value, "to": target.value},
        )
    job.status = target.value
    job.stage = stage
    if progress is not None:
        job.progress = progress
    job.state_version += 1
    now = utc_now()
    if target == JobStatus.QUEUED:
        job.queued_at = now
        job.history_expires_at = None
    if target in {JobStatus.PLANNING, JobStatus.PROCESSING} and job.started_at is None:
        job.started_at = now
    if target in TERMINAL_JOB_STATUSES:
        job.finished_at = now
        job.history_expires_at = now + timedelta(days=30)
    if target == JobStatus.CANCELLING or target in TERMINAL_JOB_STATUSES:
        session = object_session(job)
        if session is not None:
            fence_amazon_suite_review_attempts(session, job)


def estimate_job(
    job_type: JobType,
    item_count: int,
    configuration: Mapping[str, Any] | None = None,
    *,
    workspace_id: str | None = None,
) -> tuple[int, int, int]:
    from .credit_billing import image_unit_credits

    credits = item_count * image_unit_credits(job_type.value, configuration)
    if workspace_id is not None and not credit_enforcement_enabled(workspace_id):
        credits = 2 if job_type == JobType.LOCAL_EDIT else item_count * 2
    if job_type == JobType.LOCAL_EDIT:
        return 40_000, 80_000, credits
    return item_count * 45_000, item_count * 90_000, credits


def lock_workspace_limits(
    session: Session,
    workspace_id: str,
) -> WorkspaceLimitRecord:
    """Acquire the workspace-wide transaction lock used by quota gates."""

    limits = session.get(
        WorkspaceLimitRecord,
        workspace_id,
        with_for_update=True,
    )
    if limits is None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "WORKSPACE_LIMITS_NOT_CONFIGURED",
            "工作区限制尚未配置。",
        )
    return limits


def enforce_active_job_limit(session: Session, workspace_id: str) -> None:
    limits = lock_workspace_limits(session, workspace_id)
    active = session.scalar(
        select(func.count())
        .select_from(JobRecord)
        .where(
            JobRecord.workspace_id == workspace_id,
            JobRecord.status.not_in([item.value for item in TERMINAL_JOB_STATUSES]),
        )
    )
    if (active or 0) >= limits.max_active_jobs:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "ACTIVE_JOB_LIMIT_REACHED",
            "当前工作区活动任务数量已达到上限。",
        )


def _credit_reservation_is_enforced(
    session: Session,
    reservation: CreditReservationRecord,
) -> bool:
    """Recover the reservation's original enforcement mode from its ledger."""

    reserve_entry = session.scalar(
        select(CreditLedgerEntryRecord).where(
            CreditLedgerEntryRecord.reservation_id == reservation.id,
            CreditLedgerEntryRecord.entry_type == "reserve",
        )
    )
    if reserve_entry is None:
        # Legacy reservations predate the tracking-only mode and were enforced.
        return True
    return reserve_entry.delta_reserved_credits > 0


def reserve_image_credits(
    session: Session, workspace_id: str, job: JobRecord, _estimate: int = 0
) -> CreditReservationRecord | None:
    """All generation constructors share this server-authoritative gate."""
    from .credit_billing import bind_image_reservation

    existing = session.scalar(
        select(CreditReservationRecord).where(CreditReservationRecord.job_id == job.id)
    )
    if existing is not None:
        return reserve_credits(session, workspace_id, job, existing.reserved_credits)
    if not credit_enforcement_enabled(workspace_id):
        return reserve_credits(session, workspace_id, job, _estimate)
    credits, pricing = bind_image_reservation(session, job)
    reservation = reserve_credits(session, workspace_id, job, credits)
    if reservation is not None:
        reservation.pricing_snapshot = pricing
    return reservation


def reserve_credits(
    session: Session, workspace_id: str, job: JobRecord, credits: int
) -> CreditReservationRecord | None:
    if not (_quota_enabled or credit_enforcement_enabled(workspace_id)):
        return None

    existing = session.scalar(
        select(CreditReservationRecord).where(CreditReservationRecord.job_id == job.id)
    )
    if existing is not None:
        if existing.status == CreditReservationStatus.ACTIVE.value:
            return existing
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "CREDIT_RESERVATION_EXISTS",
            "该任务已有结算或释放的配额预留，无法再次预留。",
        )

    account = session.get(
        WorkspaceCreditAccountRecord, workspace_id, with_for_update=True
    )
    if account is None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "CREDIT_ACCOUNT_NOT_CONFIGURED",
            "工作区额度账户尚未配置。",
        )
    enforced = credit_enforcement_enabled(workspace_id)
    if enforced:
        from .credit_memberships import refresh

        account = refresh(session, workspace_id)
    available = account.balance_credits - account.reserved_credits
    if enforced and available < credits:
        raise ApiError(
            status.HTTP_402_PAYMENT_REQUIRED,
            "BUDGET_LIMIT_EXCEEDED",
            "当前工作区可用额度不足。",
            {"required_credits": credits, "available_credits": available},
        )
    reservation = CreditReservationRecord(
        id=new_id("hold"),
        workspace_id=workspace_id,
        job_id=job.id,
        reserved_credits=credits,
        settled_credits=0,
        status=CreditReservationStatus.ACTIVE.value,
    )
    if enforced:
        from .credit_memberships import allocate

        allocate(session, account, reservation, utc_now())
    reserved_delta = credits if enforced else 0
    if enforced:
        changed = session.execute(
            update(WorkspaceCreditAccountRecord)
            .where(
                WorkspaceCreditAccountRecord.workspace_id == workspace_id,
                WorkspaceCreditAccountRecord.balance_credits
                - WorkspaceCreditAccountRecord.reserved_credits
                >= credits,
            )
            .values(
                reserved_credits=WorkspaceCreditAccountRecord.reserved_credits
                + credits,
                version=WorkspaceCreditAccountRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], changed).rowcount != 1:
            raise ApiError(
                402, "BUDGET_LIMIT_EXCEEDED", "当前可用积分不足，请充值后再试。"
            )
        session.refresh(account)
    session.add(reservation)
    session.flush()
    session.add(
        CreditLedgerEntryRecord(
            workspace_id=workspace_id,
            reservation_id=reservation.id,
            job_id=job.id,
            entry_type="reserve",
            delta_balance_credits=0,
            delta_reserved_credits=reserved_delta,
            balance_after_credits=account.balance_credits,
            reserved_after_credits=account.reserved_credits,
        )
    )
    return reservation


def settle_credits(session: Session, job: JobRecord, actual_credits: int) -> None:

    reservation = session.scalar(
        select(CreditReservationRecord).where(CreditReservationRecord.job_id == job.id)
    )
    if reservation is None or reservation.status != CreditReservationStatus.ACTIVE:
        return
    account = session.get(
        WorkspaceCreditAccountRecord, job.workspace_id, with_for_update=True
    )
    if account is None:
        return
    from .credit_billing import successful_image_credits

    computed = successful_image_credits(session, job)
    if computed is not None:
        actual_credits = computed
    charged = min(max(0, actual_credits), reservation.reserved_credits)
    claimed = session.execute(
        update(CreditReservationRecord)
        .where(
            CreditReservationRecord.id == reservation.id,
            CreditReservationRecord.status == CreditReservationStatus.ACTIVE.value,
        )
        .values(status=CreditReservationStatus.SETTLED.value, settled_credits=charged)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], claimed).rowcount != 1:
        session.expire_all()
        return
    enforced = _credit_reservation_is_enforced(session, reservation)
    if enforced:
        session.execute(
            update(WorkspaceCreditAccountRecord)
            .where(WorkspaceCreditAccountRecord.workspace_id == job.workspace_id)
            .values(
                reserved_credits=WorkspaceCreditAccountRecord.reserved_credits
                - reservation.reserved_credits,
                balance_credits=WorkspaceCreditAccountRecord.balance_credits - charged,
                version=WorkspaceCreditAccountRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        session.refresh(account)
    reservation.settled_credits = charged
    reservation.status = CreditReservationStatus.SETTLED.value
    session.add(
        CreditLedgerEntryRecord(
            workspace_id=job.workspace_id,
            reservation_id=reservation.id,
            job_id=job.id,
            entry_type="settle",
            delta_balance_credits=-charged if enforced else 0,
            delta_reserved_credits=(-reservation.reserved_credits if enforced else 0),
            balance_after_credits=account.balance_credits,
            reserved_after_credits=account.reserved_credits,
        )
    )
    if enforced:
        from .credit_memberships import finish_allocation

        finish_allocation(session, account, reservation, charged)
    job.actual_credits = charged


def release_credits(session: Session, job: JobRecord) -> None:
    """Release an active reservation without deducting workspace balance."""

    reservation = session.scalar(
        select(CreditReservationRecord).where(CreditReservationRecord.job_id == job.id)
    )
    if (
        reservation is None
        or reservation.status != CreditReservationStatus.ACTIVE.value
    ):
        return

    account = session.get(
        WorkspaceCreditAccountRecord,
        job.workspace_id,
        with_for_update=True,
    )
    if account is None:
        return

    claimed = session.execute(
        update(CreditReservationRecord)
        .where(
            CreditReservationRecord.id == reservation.id,
            CreditReservationRecord.status == CreditReservationStatus.ACTIVE.value,
        )
        .values(status=CreditReservationStatus.RELEASED.value)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], claimed).rowcount != 1:
        session.expire_all()
        return
    released = reservation.reserved_credits
    enforced = _credit_reservation_is_enforced(session, reservation)
    if enforced:
        session.execute(
            update(WorkspaceCreditAccountRecord)
            .where(WorkspaceCreditAccountRecord.workspace_id == job.workspace_id)
            .values(
                reserved_credits=WorkspaceCreditAccountRecord.reserved_credits
                - released,
                version=WorkspaceCreditAccountRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        session.refresh(account)
    reservation.status = CreditReservationStatus.RELEASED.value
    session.add(
        CreditLedgerEntryRecord(
            workspace_id=job.workspace_id,
            reservation_id=reservation.id,
            job_id=job.id,
            entry_type="release",
            delta_balance_credits=0,
            delta_reserved_credits=-released if enforced else 0,
            balance_after_credits=account.balance_credits,
            reserved_after_credits=account.reserved_credits,
        )
    )
    session.flush()
    if enforced:
        from .credit_memberships import finish_allocation

        finish_allocation(session, account, reservation, 0)


def add_outbox_event(
    session: Session,
    *,
    workspace_id: str,
    job_id: str,
    event_type: str,
    suffix: str,
) -> OutboxEventRecord:
    event = OutboxEventRecord(
        id=new_id("evt"),
        workspace_id=workspace_id,
        aggregate_type="job",
        aggregate_id=job_id,
        event_type=event_type,
        dedupe_key=f"{job_id}:{event_type}:{suffix}",
        payload={"job_id": job_id},
        status=OutboxStatus.PENDING.value,
    )
    session.add(event)
    return event


def encode_cursor(created_at: Any, resource_id: str, scope: str) -> str:
    raw = json.dumps(
        {"created_at": created_at.isoformat(), "id": resource_id, "scope": scope},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, scope: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload["scope"] != scope:
            raise ValueError
        return str(payload["created_at"]), str(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_CURSOR",
            "分页游标无效或与当前筛选条件不匹配。",
        ) from exc


def retry_item_ids(
    session: Session,
    source: JobRecord,
    scope: RetryScope,
    selected_item_ids: list[str],
) -> list[str]:
    items = session.scalars(
        select(JobItemRecord).where(JobItemRecord.job_id == source.id)
    ).all()
    if scope == RetryScope.SELECTED:
        known = {item.id for item in items}
        unknown = [item_id for item_id in selected_item_ids if item_id not in known]
        if unknown:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "VALIDATION_ERROR",
                "所选任务条目不属于源任务。",
                {"item_ids": unknown},
            )

    blocker_ids, has_retryable_non_blocker = _amazon_review_retry_state(source, items)
    if blocker_ids and not has_retryable_non_blocker:
        _raise_amazon_review_retry_not_applicable(blocker_ids)

    from .amazon_suite_fast import selected as fast_amazon_selected

    if source.type == JobType.AMAZON_VISUAL_SUITE.value and fast_amazon_selected(
        source.configuration
    ):
        # Fast retries retain successful originals and never bill a cached
        # successful image a second time, even under FULL/SELECTED scope.
        candidates = {item.id for item in items if item.status not in {
            JobItemStatus.SUCCEEDED.value, JobItemStatus.REUSED.value
        }}
        if scope == RetryScope.FULL:
            return [item.id for item in items if item.id in candidates]
        if scope == RetryScope.SELECTED:
            return [item_id for item_id in selected_item_ids if item_id in candidates]

    if scope == RetryScope.FULL:
        return [item.id for item in items if item.id not in blocker_ids]
    if scope == RetryScope.SELECTED:
        selected = [
            item_id for item_id in selected_item_ids if item_id not in blocker_ids
        ]
        if selected_item_ids and not selected and blocker_ids:
            _raise_amazon_review_retry_not_applicable(blocker_ids)
        return selected
    statuses = {JobItemStatus.FAILED.value}
    if scope == RetryScope.MISSING:
        statuses.add(JobItemStatus.CANCELLED.value)
    preserved_set_audit_ids = {
        item_id
        for item_id in session.scalars(
            select(JobAssetRecord.job_item_id).where(
                JobAssetRecord.job_id == source.id,
                JobAssetRecord.is_deliverable.is_(True),
                JobAssetRecord.job_item_id.is_not(None),
            )
        ).all()
    }
    selected = [
        item.id
        for item in items
        if item.status in statuses
        and item.id not in blocker_ids
        and not (
            item.error_code == "AURORA_SET_AUDIT_FAILED"
            and item.id in preserved_set_audit_ids
        )
    ]
    # An Aurora v2 retry must recover pages that an earlier/style
    # gate marked as synthetic SKIPPED rows.  They never produced an artifact,
    # so treating the retry scope as FAILED-only would permanently reduce a
    # six-page retry to the first two failed pages.  Aurora has no user-visible
    # skip state; cancellation is represented by CANCELLED, so every SKIPPED
    # Aurora page is safe to regenerate from the immutable plan.
    if source.type == JobType.REFERENCE_REMIX.value and source.configuration.get(
        "replica_session_id"
    ):
        selected.extend(
            item.id
            for item in items
            if item.status
            in {JobItemStatus.SKIPPED.value, JobItemStatus.CANCELLED.value}
            and item.id not in selected
        )
    elif source.type == JobType.DOMESTIC_MAIN_IMAGE.value:
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == source.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        policy = plan.payload.get("execution_policy") if plan is not None else None
        if isinstance(policy, Mapping) and is_v20_aurora_mode(
            policy.get("execution_mode")
        ):
            selected.extend(
                item.id
                for item in items
                if item.status == JobItemStatus.SKIPPED.value
                and item.id not in blocker_ids
                and item.id not in selected
            )
    elif (
        source.type == JobType.DETAIL_PACK.value
        and isinstance(source.configuration, Mapping)
        and isinstance(source.configuration.get("settings"), Mapping)
        and source.configuration["settings"].get("market_region") == "domestic"
    ):
        # A failed pair closes the remaining sequence without generating it.
        # Recover those dependent screens together with the failed page;
        # unrelated skips and user cancellations retain their own semantics.
        selected.extend(
            item.id
            for item in items
            if item.status == JobItemStatus.SKIPPED.value
            and item.error_code == "AURORA_DETAIL_SEQUENCE_HALTED"
            and item.id not in selected
        )
    return selected
