from __future__ import annotations

from dataclasses import dataclass
from html import escape
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from typing import Any, Protocol, runtime_checkable

from fastapi import FastAPI, Request

from .config import Settings

SUPPORTED_SUPERTOKENS_PYTHON_VERSION = "0.31.3"


@dataclass(frozen=True)
class ExternalIdentity:
    primary_user_id: str
    recipe_user_id: str
    email: str | None
    email_verified: bool
    phone_number: str | None = None
    phone_verified: bool = False
    credential_version: int = 0


class ExternalAuthenticationError(RuntimeError):
    """The request did not contain a valid SuperTokens session."""


class ExternalAuthenticationUnavailable(RuntimeError):
    """The configured authentication service could not complete verification."""


class ExternalEmailNotVerifiedError(RuntimeError):
    """The session is valid, but its email-verification claim is false."""


@runtime_checkable
class ExternalAuthBackend(Protocol):
    def initialize(self) -> None: ...

    def install_middleware(self, application: FastAPI) -> None: ...

    def cors_headers(self) -> list[str]: ...

    def is_ready(self) -> bool: ...

    async def authenticate(self, request: Request) -> ExternalIdentity: ...


_INITIALIZATION_LOCK = Lock()
_INITIALIZATION_FINGERPRINT: tuple[object, ...] | None = None


def _transactional_email_html(
    *,
    title_zh: str,
    title_en: str,
    message_zh: str,
    message_en: str,
    action_zh: str,
    action_en: str,
    action_url: str,
    recipient: str,
) -> str:
    safe_url = escape(action_url, quote=True)
    safe_recipient = escape(recipient)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title_zh)} | {escape(title_en)}</title>
</head>
<body style="margin:0;background:#f3f5f9;color:#172033;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
         style="background:#f3f5f9;padding:36px 16px;
                font-family:Arial,'Microsoft YaHei',sans-serif;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
             style="max-width:600px;background:#fff;border:1px solid #e5e7eb;
                    border-radius:16px;overflow:hidden;">
        <tr>
          <td style="padding:22px 32px;border-bottom:1px solid #eef0f4;
                     font-size:18px;line-height:26px;font-weight:700;
                     color:#111827;">
            Polaris Commerce Studio
          </td>
        </tr>
        <tr>
          <td style="padding:32px;">
            <h1 style="margin:0 0 8px;font-size:24px;line-height:34px;
                       color:#111827;">
              {escape(title_zh)}
            </h1>
            <p style="margin:0 0 26px;font-size:15px;line-height:24px;
                      color:#667085;">
              {escape(title_en)}
            </p>
            <p style="margin:0 0 6px;font-size:15px;line-height:24px;
                      color:#344054;">
              {escape(message_zh)}
            </p>
            <p style="margin:0 0 26px;font-size:15px;line-height:24px;
                      color:#667085;">
              {escape(message_en)}
            </p>
            <table role="presentation" cellspacing="0" cellpadding="0"
                   style="margin:0 0 28px;">
              <tr>
                <td bgcolor="#2563eb" style="border-radius:8px;">
                  <a href="{safe_url}"
                     style="display:inline-block;padding:12px 22px;
                            font-size:15px;line-height:24px;font-weight:700;
                            color:#fff;text-decoration:none;">
                    {escape(action_zh)} / {escape(action_en)}
                  </a>
                </td>
              </tr>
            </table>
            <table role="presentation" width="100%" cellspacing="0"
                   cellpadding="0" style="background:#f8fafc;
                   border-radius:10px;margin:0 0 26px;">
              <tr>
                <td style="padding:18px;font-size:15px;line-height:24px;
                           color:#475467;">
                  如果按钮无法打开，请复制以下链接到浏览器：<br>
                  If the button does not work, copy this link into your browser:
                  <div style="margin-top:8px;word-break:break-all;">
                    <a href="{safe_url}" style="font-size:15px;line-height:24px;
                       color:#2563eb;text-decoration:none;">{safe_url}</a>
                  </div>
                </td>
              </tr>
            </table>
            <p style="margin:0;font-size:15px;line-height:24px;color:#667085;">
              如果这不是您的操作，请忽略此邮件。<br>
              If you did not request this, you can safely ignore this email.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:20px 32px;background:#fafbfc;
                     border-top:1px solid #eef0f4;font-size:15px;
                     line-height:24px;color:#98a2b3;">
            此邮件发送至 {safe_recipient}<br>
            This email was sent to {safe_recipient}
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _override_password_email_service(service: Any, app_name: str) -> Any:
    async def get_content(template_vars: Any, _user_context: dict[str, Any]) -> Any:
        from supertokens_python.ingredients.emaildelivery.types import EmailContent

        return EmailContent(
            body=_transactional_email_html(
                title_zh="重置您的密码",
                title_en="Reset your password",
                message_zh=f"我们收到了重置您 {app_name} 账户密码的请求。",
                message_en=(
                    f"We received a request to reset the password for your "
                    f"{app_name} account."
                ),
                action_zh="重置密码",
                action_en="Reset password",
                action_url=template_vars.password_reset_link,
                recipient=template_vars.user.email,
            ),
            subject=f"重置 {app_name} 密码 | Password reset",
            to_email=template_vars.user.email,
            is_html=True,
        )

    service.get_content = get_content
    return service


def _override_verification_email_service(service: Any, app_name: str) -> Any:
    async def get_content(template_vars: Any, _user_context: dict[str, Any]) -> Any:
        from supertokens_python.ingredients.emaildelivery.types import EmailContent

        return EmailContent(
            body=_transactional_email_html(
                title_zh="验证您的邮箱",
                title_en="Verify your email address",
                message_zh=f"请验证此邮箱地址，以完成 {app_name} 账户设置。",
                message_en=(
                    f"Please verify this email address to finish setting up your "
                    f"{app_name} account."
                ),
                action_zh="验证邮箱",
                action_en="Verify email",
                action_url=template_vars.email_verify_link,
                recipient=template_vars.user.email,
            ),
            subject=f"验证 {app_name} 邮箱 | Email verification",
            to_email=template_vars.user.email,
            is_html=True,
        )

    service.get_content = get_content
    return service


class SuperTokensAuthAdapter:
    """Small, test-replaceable adapter around supertokens-python 0.31.3."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._initialized = False
        self._verify_session: Any = None
        self._get_user: Any = None
        self._middleware: Any = None
        self._cors_headers: list[str] = []

    def _fingerprint(self) -> tuple[object, ...]:
        return (
            self.settings.supertokens_connection_uri,
            self.settings.supertokens_api_key.get_secret_value(),
            self.settings.supertokens_api_domain,
            self.settings.supertokens_website_domain,
            self.settings.supertokens_api_base_path,
            self.settings.supertokens_website_base_path,
            self.settings.supertokens_cookie_secure,
            self.settings.supertokens_cookie_same_site,
            self.settings.auth_smtp_host,
            self.settings.auth_smtp_port,
            self.settings.auth_smtp_username,
            self.settings.auth_smtp_password.get_secret_value(),
            self.settings.auth_smtp_secure,
            self.settings.auth_email_from_name,
            self.settings.auth_email_from_address,
            self.settings.auth_sms_enabled,
            self.settings.auth_sms_secret_id.get_secret_value(),
            self.settings.auth_sms_secret_key.get_secret_value(),
            self.settings.auth_sms_region,
            self.settings.auth_sms_sdk_app_id,
            self.settings.auth_sms_sign_name,
            self.settings.auth_sms_template_id,
            self.settings.auth_sms_redis_url or self.settings.celery_broker_url,
        )

    def initialize(self) -> None:
        global _INITIALIZATION_FINGERPRINT

        if self._initialized:
            return
        try:
            installed_version = version("supertokens-python")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "external authentication requires supertokens-python==0.31.3"
            ) from exc
        if installed_version != SUPPORTED_SUPERTOKENS_PYTHON_VERSION:
            raise RuntimeError(
                "unsupported supertokens-python version: "
                f"expected {SUPPORTED_SUPERTOKENS_PYTHON_VERSION}, "
                f"found {installed_version}"
            )

        from supertokens_python import (
            InputAppInfo,
            SupertokensConfig,
            get_all_cors_headers,
        )
        from supertokens_python import (
            init as supertokens_init,
        )
        from supertokens_python.asyncio import get_user
        from supertokens_python.framework.fastapi import get_middleware
        from supertokens_python.recipe import emailpassword, emailverification, session
        from supertokens_python.recipe.session.framework.fastapi import verify_session

        password_email_delivery = None
        verification_email_delivery = None
        sms_recipes = []
        if self.settings.auth_sms_enabled:
            from supertokens_python.ingredients.smsdelivery.types import (
                SMSDeliveryConfig,
            )
            from supertokens_python.recipe import passwordless

            from .sms_auth import HaiyuSmsDelivery, override_sms_apis, validate_phone

            sms_delivery = HaiyuSmsDelivery(self.settings)
            sms_recipes.append(
                passwordless.init(
                    contact_config=passwordless.ContactPhoneOnlyConfig(
                        validate_phone_number=validate_phone
                    ),
                    flow_type="USER_INPUT_CODE",
                    sms_delivery=SMSDeliveryConfig(service=sms_delivery),
                    override=passwordless.PasswordlessOverrideConfig(
                        apis=lambda original: override_sms_apis(
                            original, sms_delivery.limiter
                        )
                    ),
                )
            )
        if self.settings.auth_smtp_host:
            from supertokens_python.ingredients.emaildelivery.types import (
                SMTPSettings,
                SMTPSettingsFrom,
            )

            emailpassword_smtp = import_module(
                "supertokens_python.recipe.emailpassword.emaildelivery.services.smtp"
            )
            emailverification_smtp = import_module(
                "supertokens_python.recipe.emailverification.emaildelivery.services.smtp"
            )

            smtp_settings = SMTPSettings(
                host=self.settings.auth_smtp_host,
                port=self.settings.auth_smtp_port,
                username=self.settings.auth_smtp_username,
                password=self.settings.auth_smtp_password.get_secret_value(),
                secure=self.settings.auth_smtp_secure,
                from_=SMTPSettingsFrom(
                    name=self.settings.auth_email_from_name,
                    email=self.settings.auth_email_from_address,
                ),
            )
            password_email_delivery = emailpassword.EmailDeliveryConfig(
                service=emailpassword_smtp.SMTPService(
                    smtp_settings,
                    override=lambda service: _override_password_email_service(
                        service, self.settings.app_name
                    ),
                )
            )
            verification_email_delivery = emailverification.EmailDeliveryConfig(
                service=emailverification_smtp.SMTPService(
                    smtp_settings,
                    override=lambda service: _override_verification_email_service(
                        service, self.settings.app_name
                    ),
                )
            )

        fingerprint = self._fingerprint()
        with _INITIALIZATION_LOCK:
            if _INITIALIZATION_FINGERPRINT is None:
                api_key = self.settings.supertokens_api_key.get_secret_value() or None
                supertokens_init(
                    app_info=InputAppInfo(
                        app_name=self.settings.app_name,
                        api_domain=self.settings.supertokens_api_domain,
                        website_domain=self.settings.supertokens_website_domain,
                        api_base_path=self.settings.supertokens_api_base_path,
                        website_base_path=self.settings.supertokens_website_base_path,
                    ),
                    framework="fastapi",
                    supertokens_config=SupertokensConfig(
                        connection_uri=self.settings.supertokens_connection_uri,
                        api_key=api_key,
                    ),
                    recipe_list=[
                        *(
                            []
                            if self.settings.auth_sms_enabled
                            else [
                                emailpassword.init(
                                    email_delivery=password_email_delivery
                                )
                            ]
                        ),
                        *sms_recipes,
                        emailverification.init(
                            "REQUIRED", email_delivery=verification_email_delivery
                        ),
                        session.init(
                            cookie_secure=self.settings.supertokens_cookie_secure,
                            cookie_same_site=self.settings.supertokens_cookie_same_site,
                            anti_csrf="VIA_TOKEN",
                            get_token_transfer_method=lambda *_args: "cookie",
                        ),
                    ],
                    telemetry=False,
                )
                _INITIALIZATION_FINGERPRINT = fingerprint
            elif fingerprint != _INITIALIZATION_FINGERPRINT:
                raise RuntimeError(
                    "SuperTokens was already initialized with different settings"
                )

        self._verify_session = verify_session(session_required=True)
        self._get_user = get_user
        self._middleware = get_middleware()
        self._cors_headers = list(get_all_cors_headers())
        self._initialized = True

    def install_middleware(self, application: FastAPI) -> None:
        if not self._initialized or self._middleware is None:
            raise RuntimeError("SuperTokens adapter must be initialized first")
        application.add_middleware(self._middleware)

    def cors_headers(self) -> list[str]:
        return list(self._cors_headers)

    def is_ready(self) -> bool:
        return self._initialized

    async def authenticate(self, request: Request) -> ExternalIdentity:
        if (
            not self._initialized
            or self._verify_session is None
            or self._get_user is None
        ):
            raise ExternalAuthenticationUnavailable(
                "SuperTokens adapter is not initialized"
            )

        from supertokens_python.exceptions import GeneralError
        from supertokens_python.recipe.session.exceptions import (
            InvalidClaimsError,
            SuperTokensSessionError,
        )

        try:
            session_container = await self._verify_session(request)
            if session_container is None:
                raise ExternalAuthenticationError("session is required")
            primary_user_id = session_container.get_user_id()
            recipe_user_id = session_container.get_recipe_user_id().get_as_string()
            supertokens_user = await self._get_user(primary_user_id)
        except InvalidClaimsError as exc:
            if any(error.id_ == "st-ev" for error in exc.payload):
                raise ExternalEmailNotVerifiedError(
                    "email verification is required"
                ) from exc
            raise ExternalAuthenticationError("session claims are invalid") from exc
        except SuperTokensSessionError as exc:
            raise ExternalAuthenticationError("invalid or expired session") from exc
        except GeneralError as exc:
            raise ExternalAuthenticationUnavailable(
                "SuperTokens Core is unavailable"
            ) from exc

        if supertokens_user is None:
            raise ExternalAuthenticationError("session user no longer exists")

        login_method = next(
            (
                method
                for method in supertokens_user.login_methods
                if method.recipe_user_id.get_as_string() == recipe_user_id
            ),
            None,
        )
        if login_method is None:
            raise ExternalAuthenticationError(
                "the authenticated login method is missing"
            )
        phone_number = getattr(login_method, "phone_number", None)
        phone_verified = bool(
            self.settings.auth_sms_enabled
            and login_method.recipe_id == "passwordless"
            and phone_number
            and login_method.verified
        )
        if not login_method.email and not phone_verified:
            raise ExternalAuthenticationError(
                "the authenticated login method has no email address"
            )

        return ExternalIdentity(
            primary_user_id=primary_user_id,
            recipe_user_id=recipe_user_id,
            email=login_method.email,
            email_verified=bool(login_method.verified),
            phone_number=phone_number if phone_verified else None,
            phone_verified=phone_verified,
            credential_version=session_container.get_access_token_payload().get(
                "phoneCredentialVersion", 0
            )
            if phone_verified
            else 0,
        )
