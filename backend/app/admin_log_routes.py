from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .admin_auth import AdminDependency, SessionDependency
from .database import read_snapshot
from .domain import AssetLifecycleStatus, CrawlTaskStatus, JobAssetDirection, JobStatus
from .errors import ApiError
from .models import (
    AssetRecord,
    AuditEventRecord,
    CrawlEventRecord,
    CrawlTaskRecord,
    EcommerceAccountRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-logs"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_LOG_KINDS = {"all", "generation", "crawl"}
_DOMESTIC_PLATFORMS = {"taobao", "1688", "jd", "pinduoduo"}
_GENERATION_STATUSES = {status.value for status in JobStatus}
_CRAWL_STATUSES = {status.value for status in CrawlTaskStatus}

_SAFE_ERROR_MESSAGES = {
    "LOGIN_REQUIRED": "账号需要重新登录。",
    "RISK_CONTROLLED": "平台触发风控，需要人工处理。",
    "RISK_CONTROL": "平台触发风控，需要人工处理。",
    "RISK_CONTROL_PAGE": "平台触发风控，需要人工处理。",
    "CAPTCHA_REQUIRED": "需要人工完成平台验证。",
    "PLATFORM_UNAVAILABLE": "平台暂时不可用，请稍后重试。",
    "FETCH_TIMEOUT": "平台页面加载超时。",
    "PARSE_FAILED": "平台页面解析失败。",
    "PROFILE_BUSY": "账号浏览器 Profile 正在被其他任务使用。",
    "LEASE_LOST": "任务执行租约已失效，结果未提交。",
    "TASK_CANCELLED": "任务已取消。",
}

_SAFE_AUDIT_LABELS = {
    "job.dispatch_requested": "生成任务已提交。",
    "job.cancel_requested": "已请求取消生成任务。",
    "xinghe.planning_rejected": "生成计划未通过校验。",
    "xinghe.image_budget_rejected": "生成任务超过预算限制。",
    "xinghe.image_reserved": "已预留生成资源。",
    "xinghe.image_provider_completed": "生成服务已返回结果。",
    "provider.beta_job_settled": "生成资源用量已结算。",
    "provider.beta_job_attempts_recorded": "生成尝试记录已保存。",
    "provider.beta_job_released": "生成资源预留已释放。",
    "domestic_detail.auto_confirm": "已自动确认国内详情页计划。",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _utc(value)
    return normalized.isoformat() if normalized is not None else None


def _bounded_limit(value: int) -> int:
    return min(max(value, 1), _MAX_LIMIT)


def _normalize_kind(value: str) -> Literal["all", "generation", "crawl"]:
    normalized = value.strip().lower()
    if normalized not in _LOG_KINDS:
        raise ApiError(
            422, "INVALID_LOG_KIND", "日志类型必须是 all、generation 或 crawl。"
        )
    return normalized  # type: ignore[return-value]


def _normalize_platform(value: str | None) -> str | None:
    if value is None or value.strip().lower() in {"", "all"}:
        return None
    normalized = value.strip().lower()
    if normalized not in _DOMESTIC_PLATFORMS:
        raise ApiError(
            422,
            "INVALID_LOG_PLATFORM",
            "平台必须是淘宝、1688、京东或拼多多。",
        )
    return normalized


def _normalize_status(
    value: str | None, kind: Literal["all", "generation", "crawl"]
) -> str | None:
    if value is None or value.strip().lower() in {"", "all"}:
        return None
    normalized = value.strip().lower()
    allowed = (
        _GENERATION_STATUSES
        if kind == "generation"
        else _CRAWL_STATUSES
        if kind == "crawl"
        else _GENERATION_STATUSES | _CRAWL_STATUSES
    )
    if normalized not in allowed:
        raise ApiError(422, "INVALID_LOG_STATUS", "任务状态不在管理员日志支持范围内。")
    return normalized


def _normalize_search(value: str | None, *, max_length: int = 80) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized[:max_length] or None


def _like_pattern(value: str) -> str:
    # Escape wildcard characters so an admin search cannot accidentally become
    # an unbounded SQL LIKE query.
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _validate_range(created_from: datetime | None, created_to: datetime | None) -> None:
    normalized_from = _utc(created_from) if created_from is not None else None
    normalized_to = _utc(created_to) if created_to is not None else None
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_from > normalized_to
    ):
        raise ApiError(422, "INVALID_TIME_RANGE", "开始时间不能晚于结束时间。")


def _safe_error(code: str | None, message: str | None = None) -> str | None:
    if not code:
        return None
    if code in _SAFE_ERROR_MESSAGES:
        return _SAFE_ERROR_MESSAGES[code]
    # Never return the persisted provider/stack message from an admin list.
    return "任务执行失败，请根据错误码定位。"


def _safe_event_message(event_type: str, message: str | None) -> str:
    if event_type in _SAFE_AUDIT_LABELS:
        return _SAFE_AUDIT_LABELS[event_type]
    if event_type in {"created", "status_changed", "retry_scheduled"} and message:
        # Crawl event messages are authored by the adapter, but keep the output
        # bounded and reject URL-like content so this endpoint remains safe.
        text = " ".join(str(message).split())[:160]
        if "http://" not in text and "https://" not in text:
            return text
    return "任务状态发生变化。"


def _page(total: int, limit: int, offset: int) -> dict[str, int | bool]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def _base_filters(
    model: Any,
    *,
    user_id: str | None,
    workspace_id: str | None,
    status: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list[Any]:
    filters: list[Any] = []
    if workspace_id:
        filters.append(model.workspace_id == workspace_id)
    if status and status != "all":
        filters.append(model.status == status.strip().lower())
    if created_from:
        filters.append(model.created_at >= _utc(created_from))
    if created_to:
        filters.append(model.created_at <= _utc(created_to))
    if user_id:
        filters.append(
            exists(
                select(WorkspaceMemberRecord.workspace_id).where(
                    WorkspaceMemberRecord.workspace_id == model.workspace_id,
                    WorkspaceMemberRecord.user_id == user_id,
                )
            )
        )
    return filters


def _task_id_filter(model: Any, value: str | None) -> Any | None:
    return model.id == value if value else None


def _account_filter(value: str | None) -> Any | None:
    return CrawlTaskRecord.account_id == value if value else None


def _search_filter(model: Any, query: str | None) -> Any | None:
    if not query:
        return None
    pattern = _like_pattern(query)
    task_id_match = model.id.ilike(pattern, escape="\\")
    if model is not CrawlTaskRecord:
        return task_id_match
    # Account identifiers are never returned by this endpoint. Search only
    # the admin-visible display name or the masked suffix of account_key; the
    # raw account key is deliberately not exposed as a searchable field.
    account_match = exists(
        select(EcommerceAccountRecord.id).where(
            EcommerceAccountRecord.id == CrawlTaskRecord.account_id,
            or_(
                EcommerceAccountRecord.display_name.ilike(pattern, escape="\\"),
                func.lower(func.substr(EcommerceAccountRecord.account_key, -4))
                == query,
            ),
        )
    )
    return or_(task_id_match, account_match)


def _job_item(
    job: JobRecord,
    workspace_name: str | None,
    event_count: int,
    attempt_count: int,
) -> dict[str, Any]:
    return {
        "kind": "generation",
        "id": job.id,
        "workspace_id": job.workspace_id,
        "workspace_name": workspace_name,
        "type": job.type,
        "platform": None,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error_code": job.error_code,
        "error_message": _safe_error(job.error_code, job.error_message),
        "attempt_count": attempt_count,
        "created_at": _iso(job.created_at),
        "queued_at": _iso(job.queued_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "event_count": event_count,
    }


def _crawl_item(
    task: CrawlTaskRecord, workspace_name: str | None, event_count: int
) -> dict[str, Any]:
    return {
        "kind": "crawl",
        "id": task.id,
        "workspace_id": task.workspace_id,
        "workspace_name": workspace_name,
        "type": "ecommerce_crawl",
        "platform": task.platform
        if task.platform in _DOMESTIC_PLATFORMS
        else "unknown",
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "error_code": task.error_code,
        "error_message": _safe_error(task.error_code, task.error_message_safe),
        "attempt_count": task.attempt_count,
        "created_at": _iso(task.created_at),
        "queued_at": _iso(task.queued_at),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "event_count": event_count,
    }


def _event_counts(session, task_ids: list[str], *, crawl: bool) -> dict[str, int]:
    if not task_ids:
        return {}
    if crawl:
        rows = session.execute(
            select(CrawlEventRecord.task_id, func.count())
            .where(CrawlEventRecord.task_id.in_(task_ids))
            .group_by(CrawlEventRecord.task_id)
        ).all()
    else:
        rows = session.execute(
            select(AuditEventRecord.resource_id, func.count())
            .where(AuditEventRecord.resource_id.in_(task_ids))
            .group_by(AuditEventRecord.resource_id)
        ).all()
    return {str(resource_id): int(count) for resource_id, count in rows}


def _job_attempt_counts(session, job_ids: list[str]) -> dict[str, int]:
    if not job_ids:
        return {}
    rows = session.execute(
        select(
            JobItemRecord.job_id,
            func.coalesce(func.sum(JobItemRecord.attempt_count), 0),
        )
        .where(JobItemRecord.job_id.in_(job_ids))
        .group_by(JobItemRecord.job_id)
    ).all()
    return {str(job_id): int(count) for job_id, count in rows}


def _workspace_names(session, workspace_ids: set[str]) -> dict[str, str]:
    if not workspace_ids:
        return {}
    return {
        workspace.id: workspace.name
        for workspace in session.scalars(
            select(WorkspaceRecord).where(WorkspaceRecord.id.in_(workspace_ids))
        ).all()
    }


def _list_rows(
    session,
    *,
    kind: Literal["all", "generation", "crawl"],
    user_id: str | None,
    workspace_id: str | None,
    platform: str | None,
    status: str | None,
    query: str | None,
    job_id: str | None,
    crawl_task_id: str | None,
    account_id: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    total = 0
    window = limit + offset
    include_generation = (
        kind in {"all", "generation"}
        and not platform
        and not crawl_task_id
        and not account_id
    )
    include_crawl = kind in {"all", "crawl"} and not job_id
    if include_generation:
        filters = _base_filters(
            JobRecord,
            user_id=user_id,
            workspace_id=workspace_id,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )
        filters.append(JobRecord.history_deleted_at.is_(None))
        for extra_filter in (
            _task_id_filter(JobRecord, job_id),
            _search_filter(JobRecord, query),
        ):
            if extra_filter is not None:
                filters.append(extra_filter)
        total += int(
            session.scalar(select(func.count()).select_from(JobRecord).where(*filters))
            or 0
        )
        jobs = session.scalars(
            select(JobRecord)
            .where(*filters)
            .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            .limit(window)
        ).all()
        counts = _event_counts(session, [job.id for job in jobs], crawl=False)
        attempts = _job_attempt_counts(session, [job.id for job in jobs])
        names = _workspace_names(session, {job.workspace_id for job in jobs})
        rows.extend(
            _job_item(
                job,
                names.get(job.workspace_id),
                counts.get(job.id, 0),
                attempts.get(job.id, 0),
            )
            for job in jobs
        )
    if include_crawl:
        normalized_platform = _normalize_platform(platform)
        filters = _base_filters(
            CrawlTaskRecord,
            user_id=user_id,
            workspace_id=workspace_id,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )
        if normalized_platform:
            filters.append(CrawlTaskRecord.platform == normalized_platform)
        for extra_filter in (
            _task_id_filter(CrawlTaskRecord, crawl_task_id),
            _account_filter(account_id),
            _search_filter(CrawlTaskRecord, query),
        ):
            if extra_filter is not None:
                filters.append(extra_filter)
        total += int(
            session.scalar(
                select(func.count()).select_from(CrawlTaskRecord).where(*filters)
            )
            or 0
        )
        tasks = session.scalars(
            select(CrawlTaskRecord)
            .where(*filters)
            .order_by(CrawlTaskRecord.created_at.desc(), CrawlTaskRecord.id.desc())
            .limit(window)
        ).all()
        counts = _event_counts(session, [task.id for task in tasks], crawl=True)
        names = _workspace_names(session, {task.workspace_id for task in tasks})
        rows.extend(
            _crawl_item(task, names.get(task.workspace_id), counts.get(task.id, 0))
            for task in tasks
        )
    rows.sort(key=lambda row: (row["created_at"] or "", row["id"]), reverse=True)
    return rows[offset : offset + limit], total


def _generation_events(session, job: JobRecord) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for label, occurred_at in (
        ("created", job.created_at),
        ("queued", job.queued_at),
        ("started", job.started_at),
        ("finished", job.finished_at),
    ):
        if occurred_at is not None:
            events.append(
                {
                    "id": f"{job.id}:{label}",
                    "event_type": label,
                    "from_status": None,
                    "to_status": job.status if label == "finished" else None,
                    "message": _safe_event_message(label, None),
                    "occurred_at": _iso(occurred_at),
                }
            )
    audit_rows = session.scalars(
        select(AuditEventRecord)
        .where(
            AuditEventRecord.workspace_id == job.workspace_id,
            or_(
                and_(
                    AuditEventRecord.resource_type == "job",
                    AuditEventRecord.resource_id == job.id,
                ),
                AuditEventRecord.request_id == job.id,
            ),
        )
        .order_by(AuditEventRecord.occurred_at, AuditEventRecord.id)
    ).all()
    for event in audit_rows:
        if event.action not in _SAFE_AUDIT_LABELS:
            continue
        events.append(
            {
                "id": str(event.id),
                "event_type": event.action,
                "from_status": None,
                "to_status": None,
                "message": _safe_event_message(event.action, None),
                "occurred_at": _iso(event.occurred_at),
            }
        )
    events.sort(key=lambda item: (item["occurred_at"] or "", item["id"]))
    return events


async def _asset_access_url(
    request: Request, asset: AssetRecord, disposition: Literal["inline", "attachment"]
) -> tuple[str, str] | None:
    """Create a protected asset URL without exposing storage implementation details."""

    if (
        asset.lifecycle_status != AssetLifecycleStatus.READY.value
        or asset.deleted_at is not None
        or asset.purged_at is not None
    ):
        return None
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=10)
    if asset.expires_at is not None:
        asset_expires_at = _utc(asset.expires_at)
        if asset_expires_at is not None:
            expires_at = min(expires_at, asset_expires_at)
    expires_seconds = max(1, int((expires_at - now).total_seconds()))
    url = None
    if request.app.state.settings.asset_access_url_mode == "auto":
        url = await request.app.state.storage.create_access_url(
            asset.object_key,
            asset.original_filename,
            disposition,
            expires_seconds,
        )
    if url is None:
        expires = int(expires_at.timestamp())
        message = f"{asset.workspace_id}:{asset.id}:{expires}:{disposition}".encode()
        signature = hmac.new(
            request.app.state.settings.access_url_secret.encode(),
            message,
            hashlib.sha256,
        ).hexdigest()
        url = str(
            request.url_for(
                "get_signed_asset_content",
                workspace_id=asset.workspace_id,
                asset_id=asset.id,
            ).include_query_params(
                expires=expires, disposition=disposition, signature=signature
            )
        )
    return str(url), _iso(expires_at) or ""


def _asset_metadata(asset: AssetRecord, relation: JobAssetRecord) -> dict[str, Any]:
    return {
        "id": asset.id,
        "relation_id": relation.id,
        "job_item_id": relation.job_item_id,
        "direction": relation.direction,
        "role": relation.role,
        "ordinal": relation.ordinal,
        "is_deliverable": relation.is_deliverable,
        "validation": relation.validation,
        "original_filename": asset.original_filename,
        "content_type": asset.content_type,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
        "origin_kind": asset.origin_kind,
        "lifecycle_status": asset.lifecycle_status,
        "created_at": _iso(asset.created_at),
        "access_url": None,
        "download_url": None,
        "access_expires_at": None,
    }


def _provider_stage(attempt: JobAttemptRecord) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "provider": attempt.provider,
        "operation": attempt.operation,
        "status": attempt.status,
        "error_code": attempt.error_code,
        "error_message": _safe_error(attempt.error_code, attempt.error_message),
        "started_at": _iso(attempt.started_at),
        "finished_at": _iso(attempt.finished_at),
    }


def _generation_item_detail(
    item: JobItemRecord, attempts: list[JobAttemptRecord]
) -> dict[str, Any]:
    return {
        "id": item.id,
        "item_key": item.item_key,
        "ordinal": item.ordinal,
        "status": item.status,
        "stage": item.stage,
        "quality": item.quality,
        "result_source": item.result_source,
        "attempt_count": item.attempt_count,
        "error_code": item.error_code,
        "error_message": _safe_error(item.error_code, item.error_message),
        "started_at": _iso(item.started_at),
        "finished_at": _iso(item.finished_at),
        "provider_stages": [_provider_stage(attempt) for attempt in attempts],
    }


def _generation_detail_assets(
    session: Session, job: JobRecord
) -> list[tuple[dict[str, Any], AssetRecord]]:
    rows = session.execute(
        select(JobAssetRecord, AssetRecord)
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
        )
        .order_by(
            JobAssetRecord.direction,
            JobAssetRecord.ordinal,
            JobAssetRecord.id,
        )
    ).all()
    return [(_asset_metadata(asset, relation), asset) for relation, asset in rows]


def _crawl_events(session, task: CrawlTaskRecord) -> list[dict[str, Any]]:
    events = session.scalars(
        select(CrawlEventRecord)
        .where(CrawlEventRecord.task_id == task.id)
        .order_by(CrawlEventRecord.created_at, CrawlEventRecord.id)
    ).all()
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "message": _safe_event_message(event.event_type, event.message),
            "occurred_at": _iso(event.created_at),
        }
        for event in events
    ]


@router.get("/logs")
def list_admin_logs(
    session: SessionDependency,
    _admin: AdminDependency,
    kind: str = Query("all"),
    user_id: str | None = Query(None, max_length=40),
    workspace_id: str | None = Query(None, max_length=40),
    platform: str | None = Query(None, max_length=24),
    status: str | None = Query(None, max_length=32),
    query: str | None = Query(None, max_length=80),
    job_id: str | None = Query(None, max_length=40),
    crawl_task_id: str | None = Query(None, max_length=40),
    account_id: str | None = Query(None, max_length=40),
    created_from: datetime | None = Query(None),  # noqa: B008
    created_to: datetime | None = Query(None),  # noqa: B008
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    normalized_kind = _normalize_kind(kind)
    _validate_range(created_from, created_to)
    bounded = _bounded_limit(limit)
    normalized_platform = _normalize_platform(platform)
    normalized_status = _normalize_status(status, normalized_kind)
    normalized_query = _normalize_search(query)
    normalized_job_id = job_id.strip() if job_id and job_id.strip() else None
    normalized_crawl_task_id = (
        crawl_task_id.strip() if crawl_task_id and crawl_task_id.strip() else None
    )
    normalized_account_id = (
        account_id.strip() if account_id and account_id.strip() else None
    )
    items, total = _list_rows(
        session,
        kind=normalized_kind,
        user_id=user_id.strip() if user_id else None,
        workspace_id=workspace_id.strip() if workspace_id else None,
        platform=normalized_platform,
        status=normalized_status,
        query=normalized_query,
        job_id=normalized_job_id,
        crawl_task_id=normalized_crawl_task_id,
        account_id=normalized_account_id,
        created_from=created_from,
        created_to=created_to,
        limit=bounded,
        offset=offset,
    )
    return {
        "success": True,
        "data": {"items": items, "pagination": _page(total, bounded, offset)},
    }


def _get_detail(session, kind: str, log_id: str) -> tuple[str, Any]:
    normalized = _normalize_kind(kind)
    if normalized == "generation":
        record = session.get(JobRecord, log_id)
        if record is None or record.history_deleted_at is not None:
            raise ApiError(404, "ADMIN_LOG_NOT_FOUND", "未找到指定任务日志。")
        return normalized, record
    if normalized == "crawl":
        record = session.get(CrawlTaskRecord, log_id)
        if record is None:
            raise ApiError(404, "ADMIN_LOG_NOT_FOUND", "未找到指定任务日志。")
        return normalized, record
    raise ApiError(422, "INVALID_LOG_KIND", "详情必须指定 generation 或 crawl。")


def _admin_log_snapshot(
    session: Session, kind: str, log_id: str
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], AssetRecord]]]:
    normalized, record = _get_detail(session, kind, log_id)
    names = _workspace_names(session, {record.workspace_id})
    asset_rows: list[tuple[dict[str, Any], AssetRecord]] = []
    if normalized == "generation":
        attempts = _job_attempt_counts(session, [record.id])
        data = _job_item(
            record,
            names.get(record.workspace_id),
            0,
            attempts.get(record.id, 0),
        )
        items = session.scalars(
            select(JobItemRecord)
            .where(
                JobItemRecord.workspace_id == record.workspace_id,
                JobItemRecord.job_id == record.id,
            )
            .order_by(JobItemRecord.ordinal)
        ).all()
        attempt_rows = session.scalars(
            select(JobAttemptRecord)
            .where(
                JobAttemptRecord.workspace_id == record.workspace_id,
                JobAttemptRecord.job_id == record.id,
            )
            .order_by(JobAttemptRecord.job_item_id, JobAttemptRecord.attempt_no)
        ).all()
        attempts_by_item: dict[str, list[JobAttemptRecord]] = {}
        for attempt in attempt_rows:
            attempts_by_item.setdefault(attempt.job_item_id, []).append(attempt)
        asset_rows = _generation_detail_assets(session, record)
        assets = [view for view, _ in asset_rows]
        data["items"] = [
            _generation_item_detail(item, attempts_by_item.get(item.id, []))
            for item in items
        ]
        data["assets"] = assets
        data["input_assets"] = [
            asset
            for asset in assets
            if asset["direction"] == JobAssetDirection.INPUT.value
        ]
        data["output_assets"] = [
            asset
            for asset in assets
            if asset["direction"] == JobAssetDirection.OUTPUT.value
        ]
        data["intermediate_assets"] = [
            asset
            for asset in assets
            if asset["direction"] == JobAssetDirection.INTERMEDIATE.value
        ]
        events = _generation_events(session, record)
    else:
        data = _crawl_item(record, names.get(record.workspace_id), 0)
        data["items"] = []
        data["assets"] = []
        data["input_assets"] = []
        data["output_assets"] = []
        data["intermediate_assets"] = []
        events = _crawl_events(session, record)
    data["events"] = events
    data["event_count"] = len(events)
    return data, asset_rows


@router.get("/logs/{kind}/{log_id}")
async def get_admin_log_detail(
    kind: str,
    log_id: str,
    request: Request,
    session: SessionDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    data, asset_rows = await run_in_threadpool(
        read_snapshot, session, _admin_log_snapshot, session, kind, log_id
    )
    for view, asset in asset_rows:
        inline = await _asset_access_url(request, asset, "inline")
        download = await _asset_access_url(request, asset, "attachment")
        if inline is not None:
            view["access_url"], view["access_expires_at"] = inline
        if download is not None:
            view["download_url"] = download[0]
    return {"success": True, "data": data}


# Explicit aliases make the read-only slices easy to consume without exposing
# the raw source-specific models through the admin API.
@router.get("/generation-jobs")
def list_admin_generation_jobs(
    session: SessionDependency,
    _admin: AdminDependency,
    user_id: str | None = Query(None, max_length=40),
    workspace_id: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=32),
    query: str | None = Query(None, max_length=80),
    job_id: str | None = Query(None, max_length=40),
    crawl_task_id: str | None = Query(None, max_length=40),
    account_id: str | None = Query(None, max_length=40),
    created_from: datetime | None = Query(None),  # noqa: B008
    created_to: datetime | None = Query(None),  # noqa: B008
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return list_admin_logs(
        session,
        _admin,
        kind="generation",
        user_id=user_id,
        workspace_id=workspace_id,
        status=status,
        query=query,
        job_id=job_id,
        crawl_task_id=crawl_task_id,
        account_id=account_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )


@router.get("/crawl-tasks")
def list_admin_crawl_tasks(
    session: SessionDependency,
    _admin: AdminDependency,
    user_id: str | None = Query(None, max_length=40),
    workspace_id: str | None = Query(None, max_length=40),
    platform: str | None = Query(None, max_length=24),
    status: str | None = Query(None, max_length=32),
    query: str | None = Query(None, max_length=80),
    job_id: str | None = Query(None, max_length=40),
    crawl_task_id: str | None = Query(None, max_length=40),
    account_id: str | None = Query(None, max_length=40),
    created_from: datetime | None = Query(None),  # noqa: B008
    created_to: datetime | None = Query(None),  # noqa: B008
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return list_admin_logs(
        session,
        _admin,
        kind="crawl",
        user_id=user_id,
        workspace_id=workspace_id,
        platform=platform,
        status=status,
        query=query,
        job_id=job_id,
        crawl_task_id=crawl_task_id,
        account_id=account_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )


@router.get("/generation-jobs/{job_id}")
async def get_admin_generation_job(
    job_id: str,
    request: Request,
    session: SessionDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    return await get_admin_log_detail("generation", job_id, request, session, _admin)


@router.get("/crawl-tasks/{task_id}")
async def get_admin_crawl_task(
    task_id: str,
    request: Request,
    session: SessionDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    return await get_admin_log_detail("crawl", task_id, request, session, _admin)
