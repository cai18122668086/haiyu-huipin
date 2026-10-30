"""手机号密码由电商生图保存，复用现有 SuperTokens 安全会话。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from supertokens_python.asyncio import get_user
from supertokens_python.recipe.session.asyncio import (
    create_new_session,
    revoke_all_sessions_for_user,
)

from .auth import Principal, _ensure_external_user, get_principal
from .errors import ApiError
from .models import UserRecord
from .onboarding import ensure_personal_workspace
from .sms_auth import SmsDeliveryError, SmsRateLimiter, normalize_phone
from .supertokens_auth import ExternalIdentity

router = APIRouter(prefix="/api/v1/auth/phone", tags=["phone authentication"])


def validate_password(password: str) -> None:
    if (
        not (8 <= len(password) <= 99)
        or not any(c.isascii() and c.isalpha() for c in password)
        or not any(c.isascii() and c.isdigit() for c in password)
    ):
        raise ApiError(400, "INVALID_PASSWORD", "密码须为 8 至 99 位，包含字母和数字。")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=16384,
        r=8,
        p=1,
        dklen=64,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return f"scrypt$16384$8$1${salt}${digest}"


def check_password(password: str, encoded: str | None) -> bool:
    if encoded is None:
        return False
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (n, r, p) != ("16384", "8", "1"):
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=16384,
            r=8,
            p=1,
            dklen=64,
            maxmem=64 * 1024 * 1024,
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("synthetic-password-135")


@dataclass(frozen=True)
class PhoneAccount:
    user_id: str
    subject: str
    phone: str
    version: int


def account_snapshot(user: UserRecord) -> PhoneAccount:
    if not user.phone_number or user.status != "active":
        raise ApiError(401, "INVALID_CREDENTIALS", "手机号或密码不正确。")
    return PhoneAccount(
        user.id, user.auth_subject, user.phone_number, user.password_version
    )


def verify_password_account(
    session: Session, phone: str, password: str
) -> PhoneAccount:
    user = session.scalar(select(UserRecord).where(UserRecord.phone_number == phone))
    valid = check_password(
        password, user.password_hash if user and user.password_hash else _DUMMY_HASH
    )
    if user is None or not user.password_hash or not valid:
        raise ApiError(401, "INVALID_CREDENTIALS", "手机号或密码不正确。")
    return account_snapshot(user)


def persist_otp_account(
    session: Session,
    identity: ExternalIdentity,
    flow: str,
    password: str | None,
    processing_consent: dict[str, Any] | None = None,
) -> PhoneAccount:
    existing_id = session.scalar(
        select(UserRecord.id).where(
            UserRecord.auth_provider == "supertokens",
            UserRecord.auth_subject == identity.primary_user_id,
        )
    )
    if flow == "reset" and existing_id is None:
        raise ApiError(404, "PHONE_NOT_REGISTERED", "该手机号尚未注册，请先注册。")
    confirmed = False
    if flow != "reset":
        from .processing_consent import validate_choice

        try:
            validate_choice(processing_consent)
            confirmed = True
        except ApiError:
            # Verified existing users can sign in to read/download without
            # granting AI training. A new application account cannot be
            # provisioned through SMS auto-registration without its choices.
            if flow == "register" or existing_id is None:
                raise
    user = _ensure_external_user(session, identity)
    session.flush()
    locked_user = session.get(
        UserRecord, user.id, with_for_update=True, populate_existing=True
    )
    assert locked_user is not None
    user = locked_user
    account_snapshot(user)
    if flow == "register" and user.password_hash:
        raise ApiError(
            409, "PHONE_ALREADY_REGISTERED", "该手机号已注册，请直接登录或找回密码。"
        )
    if flow in {"register", "reset"}:
        if password is None:
            raise ApiError(400, "INVALID_PASSWORD", "请设置登录密码。")
        user.password_hash = hash_password(password)
        user.password_version += 1
    if not user.display_name:
        user.display_name = "用户" + (user.phone_number or "")[-4:]
    ensure_personal_workspace(session, user)
    if confirmed:
        from .processing_consent import record_consent

        record_consent(session, user.id, processing_consent, source="phone_" + flow)
    session.commit()
    return account_snapshot(user)


async def finish_otp_login(result: Any, body: dict[str, Any], request: Request) -> None:
    if getattr(result, "status", None) != "OK":
        return
    method = next(
        (
            m
            for m in result.user.login_methods
            if m.recipe_user_id.get_as_string()
            == result.session.get_recipe_user_id().get_as_string()
        ),
        None,
    )
    if method is None or method.recipe_id != "passwordless" or not method.verified:
        raise ApiError(401, "INVALID_CREDENTIALS", "手机号验证未通过。")
    identity = ExternalIdentity(
        primary_user_id=result.user.id,
        recipe_user_id=method.recipe_user_id.get_as_string(),
        email=None,
        email_verified=False,
        phone_number=normalize_phone(method.phone_number),
        phone_verified=True,
    )
    flow = body.get("flow", "login")

    def persist() -> PhoneAccount:
        with request.app.state.session_factory() as session:
            return persist_otp_account(
                session,
                identity,
                flow,
                body.get("password"),
                body.get("processingConsent"),
            )

    account = await run_in_threadpool(persist)
    if flow in {"register", "reset"}:
        await revoke_all_sessions_for_user(account.subject)
        result.session = await create_new_session(
            request,
            "public",
            method.recipe_user_id,
            access_token_payload={"phoneCredentialVersion": account.version},
        )
    else:
        await result.session.merge_into_access_token_payload(
            {"phoneCredentialVersion": account.version}
        )


class PasswordLoginBody(BaseModel):
    phone: str = Field(max_length=24)
    password: SecretStr = Field(max_length=99)


class PasswordChangeBody(BaseModel):
    current_password: SecretStr = Field(max_length=99)
    new_password: SecretStr = Field(max_length=99)


def require_phone_auth(request: Request) -> None:
    settings = request.app.state.settings
    if settings.auth_mode != "external" or not settings.auth_sms_enabled:
        raise ApiError(503, "PHONE_AUTH_NOT_READY", "手机号登录服务尚未启用。")


@router.post("/password/login")
async def password_login(request: Request, body: PasswordLoginBody) -> dict[str, str]:
    require_phone_auth(request)
    expected_origin = request.app.state.settings.supertokens_website_domain.rstrip("/")
    if request.headers.get("origin", "").rstrip("/") != expected_origin:
        raise ApiError(403, "INVALID_ORIGIN", "请从本站登录页面发起请求。")
    try:
        phone = normalize_phone(body.phone)
    except SmsDeliveryError as exc:
        raise ApiError(400, "INVALID_PHONE", str(exc)) from None
    try:
        limiter = SmsRateLimiter(request.app.state.settings)
        await run_in_threadpool(
            limiter.reserve_password_attempt,
            phone,
            request.client.host if request.client else "unknown",
        )
    except SmsDeliveryError as exc:
        raise ApiError(429, "LOGIN_RATE_LIMITED", str(exc)) from None

    def verify() -> PhoneAccount:
        with request.app.state.session_factory() as session:
            return verify_password_account(
                session, phone, body.password.get_secret_value()
            )

    account = await run_in_threadpool(verify)
    user = await get_user(account.subject)
    method = (
        next(
            (
                m
                for m in user.login_methods
                if m.recipe_id == "passwordless"
                and m.phone_number == phone
                and m.verified
            ),
            None,
        )
        if user
        else None
    )
    if method is None:
        raise ApiError(
            401, "INVALID_CREDENTIALS", "账号状态已变化，请使用短信验证码登录。"
        )
    await create_new_session(
        request,
        "public",
        method.recipe_user_id,
        access_token_payload={"phoneCredentialVersion": account.version},
    )
    return {"status": "OK"}


@router.get("/profile")
def phone_profile(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> dict[str, Any]:
    require_phone_auth(request)
    with request.app.state.session_factory() as session:
        user = session.get(UserRecord, principal.user_id)
        if user is None or not user.phone_number:
            raise ApiError(409, "PHONE_NOT_CONFIGURED", "当前账号尚未绑定手机号。")
        phone = user.phone_number.removeprefix("+86")
        return {
            "phone_hint": phone[:3] + "****" + phone[-4:],
            "has_password": bool(user.password_hash),
        }


@router.post("/password/change")
async def change_password(
    request: Request,
    body: PasswordChangeBody,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, str]:
    require_phone_auth(request)
    validate_password(body.new_password.get_secret_value())

    def update() -> PhoneAccount:
        with request.app.state.session_factory() as session:
            user = session.get(UserRecord, principal.user_id, with_for_update=True)
            if user is None or not check_password(
                body.current_password.get_secret_value(), user.password_hash
            ):
                raise ApiError(400, "INVALID_PASSWORD", "当前密码不正确。")
            account_snapshot(user)
            user.password_hash = hash_password(body.new_password.get_secret_value())
            user.password_version += 1
            session.commit()
            return account_snapshot(user)

    account = await run_in_threadpool(update)
    # 数据库版本先变更；即使 Core 暂时无法撤销会话，旧版本也无法再访问业务 API。
    await revoke_all_sessions_for_user(account.subject)
    return {"status": "OK"}
