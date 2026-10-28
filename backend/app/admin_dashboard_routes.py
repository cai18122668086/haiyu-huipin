from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .admin_auth import (
    AdminDependency,
    PrincipalDependency,
    SessionDependency,
    is_admin,
)
from .models import (
    CrawlProfileLeaseRecord,
    CrawlTaskRecord,
    EcommerceAccountRecord,
    JobRecord,
    UserRecord,
    WorkspaceRecord,
)

router = APIRouter(prefix="/api/v1", tags=["admin-dashboard"])

_DOMESTIC_PLATFORMS = ("taobao", "1688", "jd", "pinduoduo")
_ACTIVE_JOB_STATUSES = (
    "queued",
    "planning",
    "awaiting_confirmation",
    "processing",
    "cancelling",
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _account_label(account: EcommerceAccountRecord) -> str:
    if account.display_name and account.display_name.strip():
        return account.display_name.strip()
    key = account.account_key.strip()
    if "@" in key:
        local, domain = key.split("@", 1)
        return f"{local[:1]}***@{domain}"
    return f"账号***{key[-4:]}" if key else "未命名账号"


def _status_counts(session: Session, model: Any, id_column: Any) -> dict[str, int]:
    rows = session.execute(
        select(model.status, func.count(id_column)).group_by(model.status)
    ).all()
    return {str(value): int(cast(int, count)) for value, count in rows}


def _platform_sessions(
    session: Session, now: datetime
) -> dict[str, dict[str, Any]]:
    accounts = session.scalars(
        select(EcommerceAccountRecord)
        .where(EcommerceAccountRecord.platform.in_(_DOMESTIC_PLATFORMS))
        .order_by(
            EcommerceAccountRecord.platform,
            EcommerceAccountRecord.created_at,
            EcommerceAccountRecord.id,
        )
    ).all()
    leases = {
        lease.profile_id: lease
        for lease in session.scalars(
            select(CrawlProfileLeaseRecord).where(
                CrawlProfileLeaseRecord.profile_id.in_([a.profile_id for a in accounts])
            )
        ).all()
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account in accounts:
        lease = leases.get(account.profile_id)
        lease_active = bool(lease and _utc(lease.expires_at) > now)
        needs_relogin = (
            account.status == "active" and account.login_state != "ready"
        )
        grouped[account.platform].append(
            {
                "id": account.id,
                "workspace_id": account.workspace_id,
                "label": _account_label(account),
                "status": account.status,
                "login_state": account.login_state,
                "last_login_at": _iso(account.last_login_at),
                "last_used_at": _iso(account.last_used_at),
                "profile_busy": lease_active,
                "active_lease_task_id": (
                    lease.task_id if lease_active and lease else None
                ),
                "needs_relogin": needs_relogin,
            }
        )

    result: dict[str, dict[str, Any]] = {}
    for platform in _DOMESTIC_PLATFORMS:
        platform_accounts = grouped[platform]
        result[platform] = {
            "account_count": len(platform_accounts),
            "active_count": sum(
                item["status"] == "active" for item in platform_accounts
            ),
            "ready_count": sum(
                item["login_state"] == "ready" for item in platform_accounts
            ),
            "needs_relogin_count": sum(
                item["needs_relogin"] for item in platform_accounts
            ),
            "busy_count": sum(item["profile_busy"] for item in platform_accounts),
            "accounts": platform_accounts,
        }
    return result


@router.get("/admin/overview/access")
def get_admin_overview_access(
    request: Request,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, bool]:
    return {"can_manage": is_admin(principal, session, request.app.state.settings)}


@router.get("/admin/overview")
def get_admin_overview(
    session: SessionDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    """Return a safe, read-only first slice of the administrator workbench."""

    now = datetime.now(UTC)
    recent_signup_cutoff = now - timedelta(days=7)
    user_counts = _status_counts(session, UserRecord, UserRecord.id)
    workspace_counts = _status_counts(session, WorkspaceRecord, WorkspaceRecord.id)
    job_counts = _status_counts(session, JobRecord, JobRecord.id)
    crawl_counts = _status_counts(session, CrawlTaskRecord, CrawlTaskRecord.id)

    recent_signups = int(
        session.scalar(
            select(func.count(UserRecord.id)).where(
                UserRecord.created_at >= recent_signup_cutoff
            )
        )
        or 0
    )
    job_total = sum(job_counts.values())
    crawl_total = sum(crawl_counts.values())
    active_job_count = sum(job_counts.get(status, 0) for status in _ACTIVE_JOB_STATUSES)
    active_crawl_count = sum(
        crawl_counts.get(status, 0)
        for status in (
            "created",
            "queued",
            "running",
            "waiting_session",
            "cancelling",
            "retry_scheduled",
        )
    )

    return {
        "success": True,
        "data": {
            "users": {
                "total": sum(user_counts.values()),
                "active": user_counts.get("active", 0),
                "disabled": sum(
                    count for status, count in user_counts.items() if status != "active"
                ),
                "recent_signups": recent_signups,
            },
            "workspaces": {
                "total": sum(workspace_counts.values()),
                "active": workspace_counts.get("active", 0),
            },
            "usage": {
                "jobs_total": job_total,
                "jobs_running": active_job_count,
                "jobs_failed": job_counts.get("failed", 0),
                "jobs_completed": job_counts.get("completed", 0),
                "crawl_tasks_total": crawl_total,
                "crawl_tasks_running": active_crawl_count,
                "crawl_tasks_failed": crawl_counts.get("failed", 0),
                "crawl_tasks_succeeded": crawl_counts.get("succeeded", 0),
            },
            "crawler_sessions": _platform_sessions(session, now),
            "generated_at": now.isoformat(),
        },
    }
