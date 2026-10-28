from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .admin_auth import AdminDependency, SessionDependency
from .models import (
    CrawlTaskRecord,
    EcommerceAccountRecord,
    JobRecord,
    UserRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-usage"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _utc(value)
    return normalized.isoformat() if normalized is not None else None


def _max_time(*values: datetime | None) -> datetime | None:
    normalized: list[datetime] = []
    for item in values:
        value = _utc(item)
        if value is not None:
            normalized.append(value)
    return max(normalized) if normalized else None


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.strip().split("@", 1)
    if not local or not domain:
        return None
    return f"{local[:1]}***@{domain}"


def _status_filter(value: str | None) -> str | None:
    normalized = value.strip().lower() if value else None
    return None if normalized in (None, "all") else normalized


def _bounded_limit(value: int) -> int:
    return min(max(value, 1), _MAX_LIMIT)


def _page_metadata(total: int, limit: int, offset: int) -> dict[str, int | bool]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def _group_counts(
    rows: Iterable[tuple[str, str, int]],
) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = defaultdict(dict)
    for owner_id, status, count in rows:
        grouped[owner_id][str(status)] = int(count)
    return grouped


def _workspace_ids_by_user(
    session,
    user_ids: list[str],
) -> tuple[dict[str, list[str]], dict[str, list[WorkspaceMemberRecord]]]:
    if not user_ids:
        return {}, {}
    memberships = session.scalars(
        select(WorkspaceMemberRecord).where(
            WorkspaceMemberRecord.user_id.in_(user_ids)
        )
    ).all()
    ids_by_user: dict[str, list[str]] = defaultdict(list)
    records_by_user: dict[str, list[WorkspaceMemberRecord]] = defaultdict(list)
    for membership in memberships:
        ids_by_user[membership.user_id].append(membership.workspace_id)
        records_by_user[membership.user_id].append(membership)
    return ids_by_user, records_by_user


def _workspace_job_counts(
    session,
    workspace_ids: list[str],
) -> dict[str, dict[str, int]]:
    if not workspace_ids:
        return {}
    rows = session.execute(
        select(JobRecord.workspace_id, JobRecord.status, func.count(JobRecord.id))
        .where(JobRecord.workspace_id.in_(workspace_ids))
        .group_by(JobRecord.workspace_id, JobRecord.status)
    ).all()
    return _group_counts(rows)


def _workspace_crawl_counts(
    session,
    workspace_ids: list[str],
) -> dict[str, dict[str, int]]:
    if not workspace_ids:
        return {}
    rows = session.execute(
        select(
            CrawlTaskRecord.workspace_id,
            CrawlTaskRecord.status,
            func.count(CrawlTaskRecord.id),
        )
        .where(CrawlTaskRecord.workspace_id.in_(workspace_ids))
        .group_by(CrawlTaskRecord.workspace_id, CrawlTaskRecord.status)
    ).all()
    return _group_counts(rows)


def _workspace_last_activity(
    session,
    workspace_ids: list[str],
) -> dict[str, datetime | None]:
    if not workspace_ids:
        return {}
    activity: dict[str, datetime | None] = {}
    for workspace in session.scalars(
        select(WorkspaceRecord).where(WorkspaceRecord.id.in_(workspace_ids))
    ).all():
        activity[workspace.id] = _max_time(workspace.created_at, workspace.updated_at)
    for model in (JobRecord, CrawlTaskRecord, EcommerceAccountRecord):
        rows = session.execute(
            select(model.workspace_id, func.max(model.updated_at))
            .where(model.workspace_id.in_(workspace_ids))
            .group_by(model.workspace_id)
        ).all()
        for workspace_id, latest in rows:
            activity[workspace_id] = _max_time(activity.get(workspace_id), latest)
    return activity


def _sum_status(
    counts: dict[str, int], statuses: tuple[str, ...],
) -> int:
    return sum(counts.get(status, 0) for status in statuses)


def _user_items(session, users: Sequence[UserRecord]) -> list[dict[str, Any]]:
    user_ids = [user.id for user in users]
    ids_by_user, memberships_by_user = _workspace_ids_by_user(session, user_ids)
    all_workspace_ids = sorted(
        {workspace_id for ids in ids_by_user.values() for workspace_id in ids}
    )
    job_counts = _workspace_job_counts(session, all_workspace_ids)
    workspace_activity = _workspace_last_activity(session, all_workspace_ids)

    direct_crawl_rows = session.execute(
        select(
            CrawlTaskRecord.created_by,
            CrawlTaskRecord.status,
            func.count(CrawlTaskRecord.id),
        )
        .where(CrawlTaskRecord.created_by.in_(user_ids))
        .group_by(CrawlTaskRecord.created_by, CrawlTaskRecord.status)
    ).all()
    direct_crawl_counts = _group_counts(direct_crawl_rows)
    direct_crawl_latest = dict(
        session.execute(
            select(CrawlTaskRecord.created_by, func.max(CrawlTaskRecord.updated_at))
            .where(CrawlTaskRecord.created_by.in_(user_ids))
            .group_by(CrawlTaskRecord.created_by)
        ).all()
    )

    items: list[dict[str, Any]] = []
    for user in users:
        workspace_ids = ids_by_user.get(user.id, [])
        member_records = memberships_by_user.get(user.id, [])
        job_statuses = {
            status
            for workspace_id in workspace_ids
            for status in job_counts.get(workspace_id, {})
        }
        jobs = {
            status: sum(
                job_counts.get(workspace_id, {}).get(status, 0)
                for workspace_id in workspace_ids
            )
            for status in job_statuses
        }
        crawls = direct_crawl_counts.get(user.id, {})
        member_activity = max(
            (
                timestamp
                for workspace_id in workspace_ids
                for timestamp in [workspace_activity.get(workspace_id)]
                if timestamp is not None
            ),
            default=None,
        )
        membership_timestamps = [
            value
            for member in member_records
            if (value := _utc(member.updated_at)) is not None
        ]
        membership_activity = max(membership_timestamps, default=None)
        last_activity = _max_time(
            user.updated_at,
            user.last_login_at,
            member_activity,
            membership_activity,
            direct_crawl_latest.get(user.id),
        )
        active_workspaces = sum(
            membership.status == "active" for membership in member_records
        )
        items.append(
            {
                "id": user.id,
                "display_name": user.display_name or "未命名用户",
                "email": _mask_email(user.email_normalized),
                "status": user.status,
                "last_login_at": _iso(user.last_login_at),
                "created_at": _iso(user.created_at),
                "last_activity_at": _iso(last_activity),
                "workspace_count": len(workspace_ids),
                "active_workspace_count": active_workspaces,
                "workspace_jobs_total": sum(jobs.values()),
                "workspace_jobs_running": _sum_status(
                    jobs,
                    (
                        "queued",
                        "planning",
                        "awaiting_confirmation",
                        "processing",
                        "cancelling",
                    ),
                ),
                "workspace_jobs_failed": jobs.get("failed", 0),
                "crawl_tasks_total": sum(crawls.values()),
                "crawl_tasks_running": _sum_status(
                    crawls,
                    (
                        "created",
                        "queued",
                        "running",
                        "waiting_session",
                        "cancelling",
                        "retry_scheduled",
                    ),
                ),
                "crawl_tasks_failed": crawls.get("failed", 0),
            }
        )
    return items


def _workspace_account_counts(
    session,
    workspace_ids: list[str],
) -> dict[str, dict[str, int]]:
    if not workspace_ids:
        return {}
    grouped: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "active": 0, "needs_relogin": 0}
    )
    accounts = session.scalars(
        select(EcommerceAccountRecord).where(
            EcommerceAccountRecord.workspace_id.in_(workspace_ids),
            EcommerceAccountRecord.platform.in_(("taobao", "1688", "jd", "pinduoduo")),
        )
    ).all()
    for account in accounts:
        counts = grouped[account.workspace_id]
        counts["total"] += 1
        counts["active"] += account.status == "active"
        counts["needs_relogin"] += (
            account.status == "active" and account.login_state != "ready"
        )
    return grouped


def _workspace_items(
    session, workspaces: Sequence[WorkspaceRecord]
) -> list[dict[str, Any]]:
    workspace_ids = [workspace.id for workspace in workspaces]
    job_counts = _workspace_job_counts(session, workspace_ids)
    crawl_counts = _workspace_crawl_counts(session, workspace_ids)
    account_counts = _workspace_account_counts(session, workspace_ids)
    activity = _workspace_last_activity(session, workspace_ids)
    member_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "active": 0}
    )
    for member in session.scalars(
        select(WorkspaceMemberRecord).where(
            WorkspaceMemberRecord.workspace_id.in_(workspace_ids)
        )
    ).all():
        counts = member_counts[member.workspace_id]
        counts["total"] += 1
        counts["active"] += member.status == "active"

    items: list[dict[str, Any]] = []
    for workspace in workspaces:
        jobs = job_counts.get(workspace.id, {})
        crawls = crawl_counts.get(workspace.id, {})
        accounts = account_counts.get(workspace.id, {})
        items.append(
            {
                "id": workspace.id,
                "name": workspace.name,
                "status": workspace.status,
                "created_at": _iso(workspace.created_at),
                "updated_at": _iso(workspace.updated_at),
                "last_activity_at": _iso(activity.get(workspace.id)),
                "member_count": member_counts[workspace.id]["total"],
                "active_member_count": member_counts[workspace.id]["active"],
                "jobs_total": sum(jobs.values()),
                "jobs_running": _sum_status(
                    jobs,
                    (
                        "queued",
                        "planning",
                        "awaiting_confirmation",
                        "processing",
                        "cancelling",
                    ),
                ),
                "jobs_failed": jobs.get("failed", 0),
                "crawl_tasks_total": sum(crawls.values()),
                "crawl_tasks_running": _sum_status(
                    crawls,
                    (
                        "created",
                        "queued",
                        "running",
                        "waiting_session",
                        "cancelling",
                        "retry_scheduled",
                    ),
                ),
                "crawl_tasks_failed": crawls.get("failed", 0),
                "account_count": accounts.get("total", 0),
                "active_account_count": accounts.get("active", 0),
                "needs_relogin_count": accounts.get("needs_relogin", 0),
            }
        )
    return items


@router.get("/users")
def list_admin_users(
    session: SessionDependency,
    _admin: AdminDependency,
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    search: str | None = Query(None, max_length=80),
) -> dict[str, Any]:
    normalized_status = _status_filter(status)
    normalized_search = search.strip() if search else None
    filters = []
    if normalized_status:
        filters.append(UserRecord.status == normalized_status)
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            (UserRecord.id.ilike(pattern))
            | (UserRecord.display_name.ilike(pattern))
            | (UserRecord.email_normalized.ilike(pattern))
        )
    total = int(session.scalar(select(func.count(UserRecord.id)).where(*filters)) or 0)
    users = session.scalars(
        select(UserRecord)
        .where(*filters)
        .order_by(UserRecord.created_at.desc(), UserRecord.id.desc())
        .offset(offset)
        .limit(_bounded_limit(limit))
    ).all()
    return {
        "success": True,
        "data": {
            "items": _user_items(session, users),
            "pagination": _page_metadata(total, _bounded_limit(limit), offset),
        },
    }


@router.get("/workspaces")
def list_admin_workspaces(
    session: SessionDependency,
    _admin: AdminDependency,
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    search: str | None = Query(None, max_length=80),
) -> dict[str, Any]:
    normalized_status = _status_filter(status)
    normalized_search = search.strip() if search else None
    filters = []
    if normalized_status:
        filters.append(WorkspaceRecord.status == normalized_status)
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            (WorkspaceRecord.id.ilike(pattern)) | WorkspaceRecord.name.ilike(pattern)
        )
    total = int(
        session.scalar(select(func.count(WorkspaceRecord.id)).where(*filters)) or 0
    )
    workspaces = session.scalars(
        select(WorkspaceRecord)
        .where(*filters)
        .order_by(WorkspaceRecord.updated_at.desc(), WorkspaceRecord.id.desc())
        .offset(offset)
        .limit(_bounded_limit(limit))
    ).all()
    return {
        "success": True,
        "data": {
            "items": _workspace_items(session, workspaces),
            "pagination": _page_metadata(total, _bounded_limit(limit), offset),
        },
    }

