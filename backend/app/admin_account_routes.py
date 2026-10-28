from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, or_, select

from .admin_auth import AdminDependency, SessionDependency
from .admin_dashboard_routes import _account_label
from .admin_log_routes import _safe_error
from .domain import EcommerceAccountStatus, EcommerceLoginState
from .errors import ApiError
from .models import (
    CrawlProfileLeaseRecord,
    CrawlTaskRecord,
    EcommerceAccountRecord,
    WorkspaceRecord,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-accounts"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_DOMESTIC_PLATFORMS = {"taobao", "1688", "jd", "pinduoduo"}
_RISK_CODES = {"RISK_CONTROL", "RISK_CONTROL_PAGE", "CAPTCHA_REQUIRED"}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _normalize_platform(value: str | None) -> str | None:
    if value is None or value.strip().lower() in {"", "all"}:
        return None
    normalized = value.strip().lower()
    if normalized not in _DOMESTIC_PLATFORMS:
        raise ApiError(
            422, "INVALID_ACCOUNT_PLATFORM", "平台必须是淘宝、1688、京东或拼多多。"
        )
    return normalized


def _normalize_status(value: str | None) -> str | None:
    if value is None or value.strip().lower() in {"", "all"}:
        return None
    normalized = value.strip().lower()
    if normalized not in {status.value for status in EcommerceAccountStatus}:
        raise ApiError(422, "INVALID_ACCOUNT_STATUS", "账号状态不在支持范围内。")
    return normalized


def _normalize_login_state(value: str | None) -> str | None:
    if value is None or value.strip().lower() in {"", "all"}:
        return None
    normalized = value.strip().lower()
    if normalized not in {state.value for state in EcommerceLoginState}:
        raise ApiError(
            422, "INVALID_ACCOUNT_LOGIN_STATE", "账号登录状态不在支持范围内。"
        )
    return normalized


def _page(total: int, limit: int, offset: int) -> dict[str, int | bool]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def _recent_tasks(session, account_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not account_ids:
        return {}
    task_rank = func.row_number().over(
        partition_by=CrawlTaskRecord.account_id,
        order_by=(CrawlTaskRecord.created_at.desc(), CrawlTaskRecord.id.desc()),
    ).label("task_rank")
    ranked_tasks = (
        select(
            CrawlTaskRecord.account_id,
            CrawlTaskRecord.id,
            CrawlTaskRecord.platform,
            CrawlTaskRecord.status,
            CrawlTaskRecord.stage,
            CrawlTaskRecord.progress,
            CrawlTaskRecord.error_code,
            CrawlTaskRecord.error_message_safe,
            CrawlTaskRecord.created_at,
            CrawlTaskRecord.finished_at,
            task_rank,
        )
        .where(CrawlTaskRecord.account_id.in_(account_ids))
        .subquery()
    )
    rows = session.execute(
        select(ranked_tasks).where(ranked_tasks.c.task_rank <= 5)
    ).all()
    grouped: dict[str, list[dict[str, Any]]] = {
        account_id: [] for account_id in account_ids
    }
    for row in rows:
        account_id = str(row.account_id)
        grouped.setdefault(account_id, []).append(
            {
                "id": row.id,
                "platform": row.platform
                if row.platform in _DOMESTIC_PLATFORMS
                else "unknown",
                "status": row.status,
                "stage": row.stage,
                "progress": row.progress,
                "error_code": row.error_code,
                "error_message": _safe_error(
                    row.error_code, row.error_message_safe
                ),
                "created_at": _iso(row.created_at),
                "finished_at": _iso(row.finished_at),
            }
        )
    return grouped


def _account_item(
    account: EcommerceAccountRecord,
    workspace_name: str | None,
    lease: CrawlProfileLeaseRecord | None,
    stats: tuple[int, int, int],
    recent_tasks: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    total_tasks, failed_tasks, risk_tasks = stats
    lease_active = bool(lease and _utc(lease.expires_at) > now)
    lease_state = (
        "active"
        if lease_active
        else "expired"
        if lease is not None
        else "none"
    )
    lease_summary = (
        {
            "task_id": lease.task_id,
            "fencing_token": lease.fencing_token,
            "acquired_at": _iso(lease.acquired_at),
            "heartbeat_at": _iso(lease.heartbeat_at),
            "expires_at": _iso(lease.expires_at),
        }
        if lease is not None
        else None
    )
    return {
        "id": account.id,
        "workspace_id": account.workspace_id,
        "workspace_name": workspace_name,
        "platform": account.platform,
        "worker_id": account.worker_id,
        "label": _account_label(account),
        "status": account.status,
        "login_state": account.login_state,
        "needs_relogin": account.status == EcommerceAccountStatus.ACTIVE.value
        and account.login_state != EcommerceLoginState.READY.value,
        "last_login_at": _iso(account.last_login_at),
        "last_used_at": _iso(account.last_used_at),
        "task_count": total_tasks,
        "failed_task_count": failed_tasks,
        "risk_control_count": risk_tasks,
        "profile_busy": lease_active,
        "lease_state": lease_state,
        "active_lease_task_id": lease.task_id if lease_active and lease else None,
        "active_lease": lease_summary if lease_active else None,
        "last_lease": lease_summary,
        "recent_tasks": recent_tasks,
    }


@router.get("/accounts")
def list_admin_accounts(
    session: SessionDependency,
    _admin: AdminDependency,
    workspace_id: str | None = Query(None, max_length=40),
    platform: str | None = Query(None, max_length=24),
    status: str | None = Query(None, max_length=16),
    login_state: str | None = Query(None, max_length=24),
    needs_relogin: bool | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    normalized_status = _normalize_status(status)
    normalized_login_state = _normalize_login_state(login_state)
    filters: list[Any] = [EcommerceAccountRecord.platform.in_(_DOMESTIC_PLATFORMS)]
    if workspace_id and workspace_id.strip():
        filters.append(EcommerceAccountRecord.workspace_id == workspace_id.strip())
    if normalized_platform:
        filters.append(EcommerceAccountRecord.platform == normalized_platform)
    if normalized_status:
        filters.append(EcommerceAccountRecord.status == normalized_status)
    if normalized_login_state:
        filters.append(EcommerceAccountRecord.login_state == normalized_login_state)
    if needs_relogin is True:
        filters.extend(
            [
                EcommerceAccountRecord.status == EcommerceAccountStatus.ACTIVE.value,
                EcommerceAccountRecord.login_state != EcommerceLoginState.READY.value,
            ]
        )
    elif needs_relogin is False:
        filters.append(
            or_(
                EcommerceAccountRecord.status != EcommerceAccountStatus.ACTIVE.value,
                EcommerceAccountRecord.login_state == EcommerceLoginState.READY.value,
            )
        )
    total = int(
        session.scalar(
            select(func.count()).select_from(EcommerceAccountRecord).where(*filters)
        )
        or 0
    )
    bounded = min(max(limit, 1), _MAX_LIMIT)
    accounts = session.scalars(
        select(EcommerceAccountRecord)
        .where(*filters)
        .order_by(
            EcommerceAccountRecord.updated_at.desc(), EcommerceAccountRecord.id.desc()
        )
        .limit(bounded)
        .offset(offset)
    ).all()
    workspace_names = {
        workspace.id: workspace.name
        for workspace in session.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.id.in_({account.workspace_id for account in accounts})
            )
        ).all()
    }
    account_ids = [account.id for account in accounts]
    stats_rows = (
        session.execute(
            select(
                CrawlTaskRecord.account_id,
                func.count(CrawlTaskRecord.id),
                func.sum(case((CrawlTaskRecord.status == "failed", 1), else_=0)),
                func.sum(
                    case((CrawlTaskRecord.error_code.in_(_RISK_CODES), 1), else_=0)
                ),
            )
            .where(CrawlTaskRecord.account_id.in_(account_ids))
            .group_by(CrawlTaskRecord.account_id)
        ).all()
        if account_ids
        else []
    )
    stats = {
        str(account_id): (
            int(total_count),
            int(failed_count or 0),
            int(risk_count or 0),
        )
        for account_id, total_count, failed_count, risk_count in stats_rows
    }
    leases = (
        {
            lease.profile_id: lease
            for lease in session.scalars(
                select(CrawlProfileLeaseRecord).where(
                    CrawlProfileLeaseRecord.profile_id.in_(
                        [account.profile_id for account in accounts]
                    )
                )
            ).all()
        }
        if accounts
        else {}
    )
    now = datetime.now(UTC)
    recent_tasks = _recent_tasks(session, account_ids)
    items = [
        _account_item(
            account,
            workspace_names.get(account.workspace_id),
            leases.get(account.profile_id),
            stats.get(account.id, (0, 0, 0)),
            recent_tasks.get(account.id, []),
            now,
        )
        for account in accounts
    ]
    return {
        "success": True,
        "data": {"items": items, "pagination": _page(total, bounded, offset)},
    }
