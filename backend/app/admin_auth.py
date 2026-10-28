from __future__ import annotations

from typing import Annotated

from fastapi import Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from .auth import Principal, get_principal
from .database import get_session
from .domain import AdminRole, AdminStatus, MembershipRole, MembershipStatus, UserStatus
from .errors import ApiError
from .models import AdminUserRecord, UserRecord, WorkspaceMemberRecord

_ADMIN_ROLES = {MembershipRole.OWNER.value, MembershipRole.ADMIN.value}
DEFAULT_ADMIN_PERMISSIONS = (
    "users:read",
    "workspaces:read",
    "accounts:read",
    "logs:read",
)
SUPER_ADMIN_PERMISSIONS = (*DEFAULT_ADMIN_PERMISSIONS, "admins:manage")

SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


def _normalized_allowlist(settings: object) -> set[str]:
    values = getattr(settings, "announcement_admin_emails", ()) or ()
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _legacy_admin_identity(
    principal: Principal, session: Session, settings: object
) -> bool:
    email = principal.email.strip().casefold() if principal.email else None
    if (
        not principal.email_verified
        or email is None
        or email not in _normalized_allowlist(settings)
    ):
        return False
    return (
        session.scalar(
            select(WorkspaceMemberRecord.user_id)
            .where(
                WorkspaceMemberRecord.user_id == principal.user_id,
                WorkspaceMemberRecord.status == MembershipStatus.ACTIVE.value,
                WorkspaceMemberRecord.role.in_(_ADMIN_ROLES),
            )
            .limit(1)
        )
        is not None
    )


def has_managed_admins(session: Session) -> bool:
    return bool(session.scalar(select(func.count(AdminUserRecord.user_id))))


def get_admin_record(session: Session, user_id: str) -> AdminUserRecord | None:
    return session.get(AdminUserRecord, user_id)


def is_admin(principal: Principal, session: Session, settings: object) -> bool:
    """Return whether the principal has an active managed or bootstrap admin role.

    The deployment email allowlist remains a one-time bootstrap path. The first
    administrator-management mutation materializes every active allowlisted
    workspace administrator into ``admin_users``; after that, access is owned by
    the durable table and can be revoked from the UI.
    """

    if not principal.email_verified or principal.status != UserStatus.ACTIVE:
        return False
    record = get_admin_record(session, principal.user_id)
    if record is not None:
        return record.status == AdminStatus.ACTIVE.value
    return not has_managed_admins(session) and _legacy_admin_identity(
        principal, session, settings
    )


def is_super_admin(principal: Principal, session: Session, settings: object) -> bool:
    if not principal.email_verified or principal.status != UserStatus.ACTIVE:
        return False
    record = get_admin_record(session, principal.user_id)
    if record is not None:
        return (
            record.status == AdminStatus.ACTIVE.value
            and record.role == AdminRole.SUPER_ADMIN.value
        )
    return not has_managed_admins(session) and _legacy_admin_identity(
        principal, session, settings
    )


def bootstrap_legacy_admins(
    session: Session, principal: Principal, settings: object
) -> list[AdminUserRecord]:
    """Materialize the existing deployment allowlist before its first mutation."""

    if has_managed_admins(session):
        return []
    if not _legacy_admin_identity(principal, session, settings):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ADMIN_MANAGE_REQUIRED",
            "只有超级管理员可以管理管理员。",
        )

    allowlist = _normalized_allowlist(settings)
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
                WorkspaceMemberRecord.role.in_(_ADMIN_ROLES),
            )
        ).all()
    )
    if principal.user_id not in eligible_ids:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ADMIN_MANAGE_REQUIRED",
            "只有具备有效工作区管理员身份的超级管理员可以管理管理员。",
        )

    records: list[AdminUserRecord] = []
    for user in users:
        if user.id not in eligible_ids:
            continue
        record = AdminUserRecord(
            user_id=user.id,
            role=AdminRole.SUPER_ADMIN.value,
            status=AdminStatus.ACTIVE.value,
            permissions=list(SUPER_ADMIN_PERMISSIONS),
            created_by_user_id=principal.user_id,
            updated_by_user_id=principal.user_id,
        )
        session.add(record)
        records.append(record)
    session.flush()
    return records


def require_admin(
    request: HTTPConnection,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Principal:
    if not is_admin(principal, session, request.app.state.settings):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ADMIN_REQUIRED",
            "An active workspace owner or admin role is required.",
        )
    return principal


def require_super_admin(
    request: HTTPConnection,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Principal:
    if not is_super_admin(principal, session, request.app.state.settings):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ADMIN_MANAGE_REQUIRED",
            "只有超级管理员可以管理管理员。",
        )
    return principal


AdminDependency = Annotated[Principal, Depends(require_admin)]
SuperAdminDependency = Annotated[Principal, Depends(require_super_admin)]
