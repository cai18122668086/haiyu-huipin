from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .admin_auth import AdminDependency
from .auth import Principal, get_principal
from .database import get_session
from .domain import (
    EcommerceAccountStatus,
    EcommerceLoginState,
    MembershipStatus,
    WorkspaceStatus,
)
from .errors import ApiError
from .models import (
    CrawlProfileLeaseRecord,
    EcommerceAccountRecord,
    EcommerceWorkerRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
    utc_now,
)

router = APIRouter(prefix="/api/ecommerce", tags=["ecommerce-accounts"])
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]

_PLATFORM_ALIASES = {
    "taobao": "taobao",
    "tmall": "taobao",
    "1688": "1688",
    "alibaba1688": "1688",
    "jd": "jd",
    "pinduoduo": "pinduoduo",
}


def _new_id() -> str:
    return f"eac_{uuid4().hex}"


def _canonical_platform(value: str) -> str:
    try:
        return _PLATFORM_ALIASES[value.strip().casefold()]
    except KeyError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_PLATFORM",
            "当前账号平台不受支持。",
        ) from exc


def _ensure_workspace(
    session: Session, principal: Principal, workspace_id: str, *, write: bool = False
) -> None:
    workspace = session.get(WorkspaceRecord, workspace_id)
    membership = session.scalar(
        select(WorkspaceMemberRecord).where(
            WorkspaceMemberRecord.workspace_id == workspace_id,
            WorkspaceMemberRecord.user_id == principal.user_id,
            WorkspaceMemberRecord.status == MembershipStatus.ACTIVE.value,
        )
    )
    if (
        workspace is None
        or workspace.status != WorkspaceStatus.ACTIVE.value
        or membership is None
    ):
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "WORKSPACE_NOT_FOUND", "未找到指定工作区。"
        )
    if write and membership.role == "viewer":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "WORKSPACE_ACCESS_DENIED",
            "当前成员角色没有账号写入权限。",
        )


def _platform_root(request: Any, platform: str) -> Path:
    settings = request.app.state.settings
    roots = {
        "taobao": settings.ecommerce_session_dir,
        "1688": settings.ecommerce_1688_session_dir,
        "jd": settings.ecommerce_jd_session_dir,
        "pinduoduo": settings.ecommerce_pinduoduo_session_dir,
    }
    return Path(roots[platform])


def _account_response(
    account: EcommerceAccountRecord, *, lease: CrawlProfileLeaseRecord | None = None
) -> dict[str, Any]:
    now = utc_now()
    lease_active = bool(lease and lease.expires_at > now)
    return {
        "id": account.id,
        "workspace_id": account.workspace_id,
        "created_by": account.created_by,
        "platform": account.platform,
        "account_key": account.account_key,
        "display_name": account.display_name,
        "profile_id": account.profile_id,
        "worker_id": account.worker_id,
        "status": account.status,
        "login_state": account.login_state,
        "profile_busy": lease_active,
        "profile_lease_task_id": lease.task_id if lease_active and lease else None,
        "last_used_at": account.last_used_at.astimezone(UTC).isoformat()
        if account.last_used_at
        else None,
        "last_login_at": account.last_login_at.astimezone(UTC).isoformat()
        if account.last_login_at
        else None,
        "paused_at": account.paused_at.astimezone(UTC).isoformat()
        if account.paused_at
        else None,
        "disabled_at": account.disabled_at.astimezone(UTC).isoformat()
        if account.disabled_at
        else None,
        "created_at": account.created_at.astimezone(UTC).isoformat(),
        "updated_at": account.updated_at.astimezone(UTC).isoformat(),
    }


class EcommerceAccountCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=40)
    platform: str = Field(min_length=1, max_length=24)
    account_key: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)
    worker_id: str | None = Field(default=None, max_length=40)


class EcommerceAccountUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    status: EcommerceAccountStatus | None = None
    login_state: EcommerceLoginState | None = None
    worker_id: str | None = Field(default=None, max_length=40)


def _get_account(
    session: Session, principal: Principal, account_id: str, *, write: bool = False
) -> EcommerceAccountRecord:
    account = session.get(EcommerceAccountRecord, account_id)
    if account is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "ECOMMERCE_ACCOUNT_NOT_FOUND",
            "未找到指定电商账号。",
        )
    _ensure_workspace(session, principal, account.workspace_id, write=write)
    return account


def get_ecommerce_account_for_principal(
    session: Session,
    principal: Principal,
    account_id: str,
    *,
    write: bool = False,
) -> EcommerceAccountRecord:
    """Resolve an account only after enforcing its workspace boundary."""

    return _get_account(session, principal, account_id, write=write)


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_ecommerce_account(
    request: Request,
    payload: EcommerceAccountCreateRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    _ensure_workspace(session, principal, payload.workspace_id, write=True)
    if payload.worker_id:
        worker = session.get(EcommerceWorkerRecord, payload.worker_id)
        if (
            worker is None
            or worker.workspace_id != payload.workspace_id
            or worker.user_id != principal.user_id
        ):
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "WORKER_NOT_AVAILABLE",
                "目标本机执行设备不可用或不属于当前工作区。",
            )
    platform = _canonical_platform(payload.platform)
    account_key = payload.account_key.strip()
    if not account_key:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_ACCOUNT_KEY",
            "账号标识不能为空。",
        )
    duplicate = session.scalar(
        select(EcommerceAccountRecord).where(
            EcommerceAccountRecord.workspace_id == payload.workspace_id,
            EcommerceAccountRecord.platform == platform,
            EcommerceAccountRecord.account_key == account_key,
        )
    )
    if duplicate is not None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "ECOMMERCE_ACCOUNT_EXISTS",
            "该工作区已存在相同平台账号。",
        )
    account_id = _new_id()
    profile_id = f"account-profile-{account_id}"
    profile_path = _platform_root(request, platform) / "accounts" / account_id
    account = EcommerceAccountRecord(
        id=account_id,
        workspace_id=payload.workspace_id,
        created_by=principal.user_id,
        platform=platform,
        account_key=account_key,
        display_name=payload.display_name.strip() if payload.display_name else None,
        profile_id=profile_id,
        profile_path=str(profile_path),
        worker_id=payload.worker_id,
        status=EcommerceAccountStatus.ACTIVE.value,
        login_state=EcommerceLoginState.UNKNOWN.value,
    )
    session.add(account)
    session.commit()
    return {"success": True, "data": _account_response(account)}


@router.delete("/accounts/{account_id}")
def delete_ecommerce_account(
    account_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    """Remove an account from the operational pool while preserving history."""
    account = _get_account(session, principal, account_id, write=True)
    account.status = EcommerceAccountStatus.DISABLED.value
    account.worker_id = None
    account.disabled_at = utc_now()
    session.commit()
    return {"success": True, "data": {"id": account.id, "status": account.status}}


@router.get("/accounts")
def list_ecommerce_accounts(
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
    workspace_id: str = Query(min_length=1, max_length=40),
) -> dict[str, Any]:
    _ensure_workspace(session, principal, workspace_id)
    accounts = session.scalars(
        select(EcommerceAccountRecord)
        .where(EcommerceAccountRecord.workspace_id == workspace_id)
        .order_by(EcommerceAccountRecord.created_at, EcommerceAccountRecord.id)
    ).all()
    leases = {
        lease.profile_id: lease
        for lease in session.scalars(
            select(CrawlProfileLeaseRecord).where(
                CrawlProfileLeaseRecord.profile_id.in_([a.profile_id for a in accounts])
            )
        ).all()
    }
    return {
        "success": True,
        "data": [
            _account_response(account, lease=leases.get(account.profile_id))
            for account in accounts
        ],
    }


@router.get("/accounts/{account_id}")
def get_ecommerce_account(
    account_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    account = _get_account(session, principal, account_id)
    lease = session.scalar(
        select(CrawlProfileLeaseRecord).where(
            CrawlProfileLeaseRecord.profile_id == account.profile_id
        )
    )
    return {"success": True, "data": _account_response(account, lease=lease)}


@router.patch("/accounts/{account_id}")
def update_ecommerce_account(
    account_id: str,
    payload: EcommerceAccountUpdateRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    account = _get_account(session, principal, account_id, write=True)
    if "worker_id" in payload.model_fields_set:
        if payload.worker_id is None:
            account.worker_id = None
        else:
            worker = session.get(EcommerceWorkerRecord, payload.worker_id)
            if (
                worker is None
                or worker.workspace_id != account.workspace_id
                or worker.user_id != principal.user_id
            ):
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "WORKER_NOT_AVAILABLE",
                    "目标本机执行设备不可用或不属于当前工作区。",
                )
            account.worker_id = payload.worker_id
    if payload.display_name is not None:
        account.display_name = payload.display_name.strip() or None
    if payload.status is not None:
        account.status = payload.status.value
        now = utc_now()
        account.paused_at = (
            now if payload.status is EcommerceAccountStatus.PAUSED else None
        )
        account.disabled_at = (
            now if payload.status is EcommerceAccountStatus.DISABLED else None
        )
    if payload.login_state is not None:
        account.login_state = payload.login_state.value
        if payload.login_state is EcommerceLoginState.READY:
            account.last_login_at = utc_now()
    session.commit()
    return {"success": True, "data": _account_response(account)}
