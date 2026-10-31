from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Any

from fastapi import Depends, Security, status
from fastapi.security import APIKeyCookie
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from .database import get_session
from .domain import (
    MembershipRole,
    MembershipStatus,
    UserStatus,
    WorkspaceStatus,
)
from .errors import ApiError
from .models import (
    UserRecord,
    WorkspaceCreditAccountRecord,
    WorkspaceLimitRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)
from .onboarding import ensure_personal_workspace
from .supertokens_auth import (
    ExternalAuthenticationError,
    ExternalAuthenticationUnavailable,
    ExternalEmailNotVerifiedError,
    ExternalIdentity,
)

_EXTERNAL_AUTH_PROVIDER = "supertokens"
_MAX_JIT_ATTEMPTS = 3


class _HTTPConnectionAPIKeyCookie(APIKeyCookie):
    """Cookie security scheme compatible with HTTP and WebSocket scopes.

    FastAPI's built-in ``APIKeyCookie`` annotates its dependency with
    ``Request``.  A WebSocket handshake exposes a Starlette ``WebSocket``
    (an ``HTTPConnection``), so the generated dependency can raise a 500
    before our authentication adapter gets a chance to inspect the session.
    Keep the OpenAPI security scheme while accepting the common connection
    interface at runtime.
    """

    async def __call__(self, request: HTTPConnection) -> str | None:
        api_key = request.cookies.get(self.model.name)
        return self.check_api_key(api_key)


_SESSION_COOKIE = _HTTPConnectionAPIKeyCookie(
    name="sAccessToken",
    scheme_name="SuperTokensSession",
    description=(
        "SuperTokens HttpOnly session cookie managed by the authentication SDK."
    ),
    auto_error=False,
)


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str | None = None
    display_name: str | None = None
    status: UserStatus = UserStatus.ACTIVE
    email_verified: bool = True


@dataclass(frozen=True)
class WorkspaceContext:
    principal: Principal
    workspace_id: str
    role: MembershipRole


SessionDependency = Annotated[Session, Depends(get_session)]


def ensure_development_identity(request: Any) -> None:
    application = getattr(request, "app", request)
    settings = application.state.settings
    if settings.auth_mode != "development":
        return
    session_factory = application.state.session_factory
    with session_factory() as session:
        user = session.get(UserRecord, settings.development_user_id)
        if user is None:
            user = UserRecord(
                id=settings.development_user_id,
                auth_provider="development",
                auth_subject=settings.development_user_id,
                display_name="Development User",
                email_normalized=settings.development_user_email.strip().casefold(),
                status=UserStatus.ACTIVE.value,
            )
            session.add(user)
        elif not user.email_normalized:
            user.email_normalized = settings.development_user_email.strip().casefold()
        workspace = session.get(WorkspaceRecord, settings.development_workspace_id)
        if workspace is None:
            workspace = WorkspaceRecord(
                id=settings.development_workspace_id,
                name=settings.development_workspace_name,
                status=WorkspaceStatus.ACTIVE.value,
                created_by_user_id=user.id,
            )
            session.add(workspace)
            session.flush()
        if (
            session.get(
                WorkspaceMemberRecord,
                (settings.development_workspace_id, settings.development_user_id),
            )
            is None
        ):
            session.add(
                WorkspaceMemberRecord(
                    workspace_id=settings.development_workspace_id,
                    user_id=settings.development_user_id,
                    role=MembershipRole.OWNER.value,
                    status=MembershipStatus.ACTIVE.value,
                )
            )
        if session.get(WorkspaceLimitRecord, settings.development_workspace_id) is None:
            session.add(
                WorkspaceLimitRecord(workspace_id=settings.development_workspace_id)
            )
        if (
            session.get(WorkspaceCreditAccountRecord, settings.development_workspace_id)
            is None
        ):
            session.add(
                WorkspaceCreditAccountRecord(
                    workspace_id=settings.development_workspace_id
                )
            )
        session.commit()


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _local_user_id(primary_user_id: str) -> str:
    digest = sha256(primary_user_id.encode("utf-8")).hexdigest()[:32]
    return f"usr_st_{digest}"


def _load_external_user(session: Session, primary_user_id: str) -> UserRecord | None:
    return session.scalar(
        select(UserRecord).where(
            UserRecord.auth_provider == _EXTERNAL_AUTH_PROVIDER,
            UserRecord.auth_subject == primary_user_id,
        )
    )


def _ensure_external_user(
    session: Session,
    identity: ExternalIdentity,
) -> UserRecord:
    normalized_email = _normalize_email(identity.email) if identity.email else None
    verified_phone = identity.phone_number if identity.phone_verified else None
    for attempt in range(_MAX_JIT_ATTEMPTS):
        user = _load_external_user(session, identity.primary_user_id)
        if user is not None:
            if user.email_normalized != normalized_email:
                user.email_normalized = normalized_email
            if verified_phone is not None:
                user.phone_number = verified_phone
            return user
        try:
            with session.begin_nested():
                user = UserRecord(
                    id=_local_user_id(identity.primary_user_id),
                    auth_provider=_EXTERNAL_AUTH_PROVIDER,
                    auth_subject=identity.primary_user_id,
                    email_normalized=normalized_email,
                    phone_number=verified_phone,
                    status=UserStatus.ACTIVE.value,
                    last_login_at=datetime.now(UTC),
                )
                session.add(user)
                session.flush()
                return user
        except IntegrityError:
            session.expire_all()
            if attempt + 1 == _MAX_JIT_ATTEMPTS:
                raise

    raise RuntimeError("external user provisioning attempts exhausted")


def _principal_from_user(user: UserRecord, *, email_verified: bool) -> Principal:
    return Principal(
        user_id=user.id,
        email=user.email_normalized,
        display_name=user.display_name,
        status=UserStatus(user.status),
        email_verified=email_verified,
    )


async def get_principal(
    request: HTTPConnection,
    session: SessionDependency,
    _session_cookie: Annotated[str | None, Security(_SESSION_COOKIE)] = None,
) -> Principal:
    settings = request.app.state.settings
    if settings.auth_mode == "development":
        user_id = request.headers.get("X-User-Id") or settings.development_user_id
        return await run_in_threadpool(_development_principal, session, user_id)

    auth_backend = request.app.state.external_auth_backend
    try:
        identity = await auth_backend.authenticate(request)
    except ExternalEmailNotVerifiedError as exc:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "EMAIL_NOT_VERIFIED",
            "请先完成邮箱验证。",
        ) from exc
    except ExternalAuthenticationError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "UNAUTHENTICATED",
            "请求未通过身份认证。",
        ) from exc
    except ExternalAuthenticationUnavailable as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AUTHENTICATION_UNAVAILABLE",
            "身份认证服务暂时不可用。",
        ) from exc

    if not (
        (identity.email and identity.email_verified)
        or (identity.phone_number and identity.phone_verified)
    ):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "EMAIL_NOT_VERIFIED",
            "请先完成邮箱验证。",
        )

    return await run_in_threadpool(_external_principal, session, identity)


def _development_principal(session: Session, user_id: str) -> Principal:
    user = session.get(UserRecord, user_id)
    principal = (
        Principal(user_id=user_id)
        if user is None
        else _principal_from_user(user, email_verified=True)
    )
    session.rollback()
    return principal


def _external_principal(session: Session, identity: ExternalIdentity) -> Principal:
    # Pool checkout and provisioning use the synchronous SQLAlchemy driver.
    # Keep them off the ASGI event loop so occupied connections cannot freeze
    # session refresh, health checks, or completion of other requests.

    try:
        user = _ensure_external_user(session, identity)
        if (
            identity.phone_verified
            and identity.credential_version != user.password_version
        ):
            session.rollback()
            raise ApiError(401, "SESSION_REVOKED", "密码已更新，请重新登录。")
        if user.status != UserStatus.ACTIVE.value:
            session.rollback()
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "ACCOUNT_DISABLED",
                "当前账户已被停用。",
            )
        ensure_personal_workspace(session, user)
        session.commit()
    except ApiError:
        raise
    except SQLAlchemyError as exc:
        session.rollback()
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "IDENTITY_PROVISIONING_FAILED",
            "用户初始化暂时失败，请稍后重试。",
        ) from exc

    return _principal_from_user(user, email_verified=True)


def require_workspace(
    workspace_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDependency,
) -> WorkspaceContext:
    membership = session.scalar(
        select(WorkspaceMemberRecord).where(
            WorkspaceMemberRecord.workspace_id == workspace_id,
            WorkspaceMemberRecord.user_id == principal.user_id,
            WorkspaceMemberRecord.status == MembershipStatus.ACTIVE.value,
        )
    )
    workspace = session.get(WorkspaceRecord, workspace_id)
    if (
        workspace is None
        or workspace.status != WorkspaceStatus.ACTIVE.value
        or membership is None
    ):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "WORKSPACE_NOT_FOUND",
            "未找到指定工作区。",
        )
    context = WorkspaceContext(
        principal=principal,
        workspace_id=workspace_id,
        role=MembershipRole(membership.role),
    )
    # The authorization snapshot contains no ORM objects. Release this read
    # transaction before upload/download handlers wait on external storage.
    session.rollback()
    return context


WorkspaceDependency = Annotated[WorkspaceContext, Depends(require_workspace)]


def require_workspace_write(
    workspace: WorkspaceDependency,
) -> WorkspaceContext:
    if workspace.role == MembershipRole.VIEWER:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "WORKSPACE_ACCESS_DENIED",
            "当前成员角色没有写入工作区的权限。",
        )
    return workspace


WorkspaceWriteDependency = Annotated[
    WorkspaceContext,
    Depends(require_workspace_write),
]
