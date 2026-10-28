from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .admin_auth import (
    DEFAULT_ADMIN_PERMISSIONS,
    SUPER_ADMIN_PERMISSIONS,
    SessionDependency,
    SuperAdminDependency,
    bootstrap_legacy_admins,
    has_managed_admins,
)
from .domain import AdminRole, AdminStatus, MembershipRole, MembershipStatus, UserStatus
from .errors import ApiError
from .models import AdminUserRecord, AuditEventRecord, UserRecord, WorkspaceMemberRecord

router = APIRouter(prefix="/api/v1/admin", tags=["admin-management"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_ALLOWED_PERMISSIONS = frozenset(SUPER_ADMIN_PERMISSIONS)


class AdminCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class AdminUpdateRequest(BaseModel):
    status: Literal["active", "disabled"] | None = None
    role: Literal["admin", "super_admin"] | None = None
    permissions: list[str] | None = Field(default=None, max_length=20)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.strip().split("@", 1)
    return f"{local[:1]}***@{domain}" if local and domain else None


def _page(total: int, limit: int, offset: int) -> dict[str, int | bool]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def _permissions_for(
    role: str, permissions: list[str] | tuple[str, ...] | None
) -> list[str]:
    allowed = set(permissions or ()) & _ALLOWED_PERMISSIONS
    if role == AdminRole.SUPER_ADMIN.value:
        allowed.update(SUPER_ADMIN_PERMISSIONS)
    else:
        allowed.discard("admins:manage")
        if not allowed:
            allowed.update(DEFAULT_ADMIN_PERMISSIONS)
    return sorted(allowed)


def _admin_item(
    user: UserRecord,
    record: AdminUserRecord | None,
    *,
    source: str,
) -> dict[str, Any]:
    role = record.role if record is not None else AdminRole.SUPER_ADMIN.value
    status_value = record.status if record is not None else AdminStatus.ACTIVE.value
    permissions = _permissions_for(
        role, record.permissions if record is not None else None
    )
    return {
        "user_id": user.id,
        "display_name": user.display_name or "未设置姓名",
        "email": _mask_email(user.email_normalized),
        "status": status_value,
        "role": role,
        "permissions": permissions,
        "source": source,
        "user_status": user.status,
        "created_at": _iso(
            record.created_at if record is not None else user.created_at
        ),
        "updated_at": _iso(
            record.updated_at if record is not None else user.updated_at
        ),
        "last_login_at": _iso(user.last_login_at),
    }


def _eligible_bootstrap_users(session: Session, settings: object) -> list[UserRecord]:
    allowlist = {
        str(value).strip().casefold()
        for value in (getattr(settings, "announcement_admin_emails", ()) or ())
        if str(value).strip()
    }
    if not allowlist:
        return []
    users = session.scalars(
        select(UserRecord).where(
            UserRecord.email_normalized.in_(allowlist),
            UserRecord.status == UserStatus.ACTIVE.value,
        )
    ).all()
    eligible_ids = set(
        session.scalars(
            select(WorkspaceMemberRecord.user_id).where(
                WorkspaceMemberRecord.user_id.in_([user.id for user in users]),
                WorkspaceMemberRecord.status == MembershipStatus.ACTIVE.value,
                WorkspaceMemberRecord.role.in_(
                    (
                        MembershipRole.OWNER.value,
                        MembershipRole.ADMIN.value,
                    )
                ),
            )
        ).all()
    )
    return [user for user in users if user.id in eligible_ids]


def _record_audit(
    session: Session,
    request: Request,
    actor_user_id: str,
    action: str,
    target_user_id: str,
    details: dict[str, Any],
) -> None:
    session.add(
        AuditEventRecord(
            actor_user_id=actor_user_id,
            action=action,
            resource_type="admin_user",
            resource_id=target_user_id,
            request_id=getattr(request.state, "request_id", None),
            details=details,
        )
    )


def _validate_permissions(permissions: list[str]) -> list[str]:
    normalized = sorted({item.strip() for item in permissions if item.strip()})
    unknown = [item for item in normalized if item not in _ALLOWED_PERMISSIONS]
    if unknown:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_ADMIN_PERMISSION",
            "管理员权限包含不支持的项目。",
            {"permissions": unknown},
        )
    return normalized


def _materialize_if_needed(
    session: Session,
    request: Request,
    principal: SuperAdminDependency,
) -> None:
    if has_managed_admins(session):
        return
    records = bootstrap_legacy_admins(session, principal, request.app.state.settings)
    for record in records:
        _record_audit(
            session,
            request,
            principal.user_id,
            "admin.bootstrap_materialized",
            record.user_id,
            {"role": record.role, "status": record.status},
        )


@router.get("/administrators")
def list_administrators(
    request: Request,
    session: SessionDependency,
    _admin: SuperAdminDependency,
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    role: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None, max_length=80),
) -> dict[str, Any]:
    normalized_role = role.strip().lower() if role else None
    if normalized_role and normalized_role not in {item.value for item in AdminRole}:
        raise ApiError(422, "INVALID_ADMIN_ROLE", "管理员角色不在支持范围内。")
    normalized_status = status_filter.strip().lower() if status_filter else None
    if normalized_status and normalized_status not in {
        item.value for item in AdminStatus
    }:
        raise ApiError(422, "INVALID_ADMIN_STATUS", "管理员状态不在支持范围内。")
    normalized_search = search.strip().casefold() if search else None

    if not has_managed_admins(session):
        users = _eligible_bootstrap_users(session, request.app.state.settings)
        items = [
            _admin_item(user, None, source="bootstrap")
            for user in users
            if (not normalized_role or normalized_role == AdminRole.SUPER_ADMIN.value)
            and (not normalized_status or normalized_status == AdminStatus.ACTIVE.value)
            and (
                not normalized_search
                or normalized_search in (user.email_normalized or "").casefold()
                or normalized_search in (user.display_name or "").casefold()
                or normalized_search in user.id.casefold()
            )
        ]
        items.sort(
            key=lambda item: (item["created_at"] or "", item["user_id"]), reverse=True
        )
        total = len(items)
        return {
            "success": True,
            "data": {
                "items": items[offset : offset + limit],
                "pagination": _page(total, limit, offset),
                "can_manage": True,
            },
        }

    filters: list[Any] = []
    if normalized_role:
        filters.append(AdminUserRecord.role == normalized_role)
    if normalized_status:
        filters.append(AdminUserRecord.status == normalized_status)
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            (UserRecord.id.ilike(pattern))
            | (UserRecord.display_name.ilike(pattern))
            | (UserRecord.email_normalized.ilike(pattern))
        )
    total = int(
        session.scalar(
            select(func.count(AdminUserRecord.user_id))
            .join(UserRecord, UserRecord.id == AdminUserRecord.user_id)
            .where(*filters)
        )
        or 0
    )
    rows = session.execute(
        select(AdminUserRecord, UserRecord)
        .join(UserRecord, UserRecord.id == AdminUserRecord.user_id)
        .where(*filters)
        .order_by(AdminUserRecord.created_at.desc(), AdminUserRecord.user_id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "success": True,
        "data": {
            "items": [
                _admin_item(user, record, source="managed") for record, user in rows
            ],
            "pagination": _page(total, limit, offset),
            "can_manage": True,
        },
    }


@router.post("/administrators", status_code=status.HTTP_201_CREATED)
def create_administrator(
    payload: AdminCreateRequest,
    request: Request,
    session: SessionDependency,
    principal: SuperAdminDependency,
) -> dict[str, Any]:
    _materialize_if_needed(session, request, principal)
    normalized_email = payload.email.strip().casefold()
    user = session.scalar(
        select(UserRecord).where(UserRecord.email_normalized == normalized_email)
    )
    if user is None:
        raise ApiError(
            404, "ADMIN_USER_NOT_FOUND", "请先让该用户完成注册后再添加管理员。"
        )
    if user.status != UserStatus.ACTIVE.value:
        raise ApiError(422, "ADMIN_USER_INACTIVE", "停用或删除的用户不能添加为管理员。")
    if session.get(AdminUserRecord, user.id) is not None:
        raise ApiError(409, "ADMIN_ALREADY_EXISTS", "该用户已经是管理员。")

    record = AdminUserRecord(
        user_id=user.id,
        role=AdminRole.ADMIN.value,
        status=AdminStatus.ACTIVE.value,
        permissions=list(DEFAULT_ADMIN_PERMISSIONS),
        created_by_user_id=principal.user_id,
        updated_by_user_id=principal.user_id,
    )
    session.add(record)
    _record_audit(
        session,
        request,
        principal.user_id,
        "admin.created",
        user.id,
        {
            "role": record.role,
            "status": record.status,
            "permissions": record.permissions,
        },
    )
    session.commit()
    session.refresh(record)
    return {"success": True, "data": _admin_item(user, record, source="managed")}


@router.patch("/administrators/{user_id}")
def update_administrator(
    user_id: str,
    payload: AdminUpdateRequest,
    request: Request,
    session: SessionDependency,
    principal: SuperAdminDependency,
) -> dict[str, Any]:
    _materialize_if_needed(session, request, principal)
    record = session.get(AdminUserRecord, user_id)
    user = session.get(UserRecord, user_id)
    if record is None or user is None:
        raise ApiError(404, "ADMIN_NOT_FOUND", "管理员不存在。")
    if payload.status is None and payload.role is None and payload.permissions is None:
        raise ApiError(422, "ADMIN_UPDATE_EMPTY", "至少提供一项需要修改的管理员属性。")

    previous = {
        "role": record.role,
        "status": record.status,
        "permissions": list(record.permissions or []),
    }
    next_role = payload.role or record.role
    next_status = payload.status or record.status
    next_permissions = (
        _validate_permissions(payload.permissions)
        if payload.permissions is not None
        else list(record.permissions or [])
    )
    next_permissions = _permissions_for(next_role, next_permissions)

    active_super_count = int(
        session.scalar(
            select(func.count(AdminUserRecord.user_id)).where(
                AdminUserRecord.role == AdminRole.SUPER_ADMIN.value,
                AdminUserRecord.status == AdminStatus.ACTIVE.value,
            )
        )
        or 0
    )
    was_active_super = (
        record.role == AdminRole.SUPER_ADMIN.value
        and record.status == AdminStatus.ACTIVE.value
    )
    will_be_active_super = (
        next_role == AdminRole.SUPER_ADMIN.value
        and next_status == AdminStatus.ACTIVE.value
    )
    if was_active_super and not will_be_active_super and active_super_count <= 1:
        raise ApiError(
            409,
            "LAST_SUPER_ADMIN",
            "不能禁用或降权最后一个超级管理员。",
        )

    record.role = next_role
    record.status = next_status
    record.permissions = next_permissions
    record.updated_by_user_id = principal.user_id
    _record_audit(
        session,
        request,
        principal.user_id,
        "admin.updated",
        user_id,
        {
            "previous": previous,
            "next": {
                "role": next_role,
                "status": next_status,
                "permissions": next_permissions,
            },
        },
    )
    session.commit()
    session.refresh(record)
    return {"success": True, "data": _admin_item(user, record, source="managed")}
