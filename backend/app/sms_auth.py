"""电商生图独立账号的短信通道；验证码和会话由本项目 SuperTokens 管理。"""

from __future__ import annotations

import hashlib
import inspect
import logging
import re
from collections.abc import Callable
from typing import Any

from redis import Redis
from starlette.concurrency import run_in_threadpool
from supertokens_python.ingredients.smsdelivery.types import SMSDeliveryInterface
from supertokens_python.recipe.passwordless.types import SMSTemplateVars
from supertokens_python.types.response import GeneralErrorResponse

from .config import Settings

logger = logging.getLogger(__name__)


class SmsDeliveryError(RuntimeError):
    """可向用户展示的通道或限流错误，不包含云凭据与验证码。"""


def normalize_phone(phone: str) -> str:
    value = phone.strip()
    if value.startswith("+86"):
        value = value[3:]
    if not re.fullmatch(r"1[3-9]\d{9}", value):
        raise SmsDeliveryError("请输入有效的中国大陆手机号。")
    return "+86" + value


async def validate_phone(phone: str, _tenant_id: str) -> str | None:
    try:
        normalize_phone(phone)
    except SmsDeliveryError as exc:
        return str(exc)
    return None


# 所有进程共用 Redis 原子检查，跨 create/resend、跨验证码会话限制同一手机号。
_RATE_LIMIT_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 1 end
if tonumber(redis.call('GET', KEYS[2]) or '0') >= tonumber(ARGV[2]) then
  return 2
end
if tonumber(redis.call('GET', KEYS[3]) or '0') >= tonumber(ARGV[3]) then
  return 3
end
redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
local daily = redis.call('INCR', KEYS[2])
if daily == 1 then redis.call('EXPIRE', KEYS[2], 86400) end
local hourly = redis.call('INCR', KEYS[3])
if hourly == 1 then redis.call('EXPIRE', KEYS[3], 3600) end
return 0
"""


class SmsRateLimiter:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.client = client or Redis.from_url(
            settings.auth_sms_redis_url or settings.celery_broker_url,
            socket_connect_timeout=3,
            socket_timeout=3,
        )

    def reserve(self, phone: str, ip: str) -> None:
        phone_hash = hashlib.sha256(phone.encode()).hexdigest()
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()
        prefix = "polaris:auth:sms:v1:"
        try:
            result = self.client.eval(
                _RATE_LIMIT_SCRIPT,
                3,
                prefix + "cooldown:" + phone_hash,
                prefix + "daily:" + phone_hash,
                prefix + "hourly:" + ip_hash,
                "60",
                "20",
                "40",
            )
        except Exception:
            raise SmsDeliveryError("验证码服务暂时不可用，请稍后重试。") from None
        messages = {
            1: "发送过于频繁，请在 60 秒后重试。",
            2: "该手机号今日验证码发送次数已达上限。",
            3: "当前网络请求过于频繁，请稍后重试。",
        }
        if result in messages:
            raise SmsDeliveryError(messages[result])
        if result != 0:
            raise SmsDeliveryError("验证码服务暂时不可用，请稍后重试。")

    def mark_attempt(self, pre_auth_session_id: str) -> None:
        try:
            self.client.set(self._attempt_key(pre_auth_session_id), "1", ex=300)
        except Exception:
            raise SmsDeliveryError("验证码服务暂时不可用，请稍后重试。") from None

    def reserve_password_attempt(self, phone: str, ip: str) -> None:
        phone_hash = hashlib.sha256(phone.encode()).hexdigest()
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()
        script = """
        if tonumber(redis.call('GET', KEYS[1]) or '0') >= 10 then return 1 end
        if tonumber(redis.call('GET', KEYS[2]) or '0') >= 50 then return 1 end
        for i=1,2 do
          local count = redis.call('INCR', KEYS[i])
          if count == 1 then redis.call('EXPIRE', KEYS[i], 900) end
        end
        return 0
        """
        try:
            result = self.client.eval(
                script,
                2,
                "polaris:auth:password:phone:" + phone_hash,
                "polaris:auth:password:ip:" + ip_hash,
            )
        except Exception:
            raise SmsDeliveryError("登录服务暂时不可用，请稍后重试。") from None
        if result != 0:
            raise SmsDeliveryError("登录尝试过于频繁，请在 15 分钟后重试。")

    def require_attempt(self, pre_auth_session_id: str) -> None:
        try:
            valid = self.client.exists(self._attempt_key(pre_auth_session_id))
        except Exception:
            raise SmsDeliveryError("验证码服务暂时不可用，请稍后重试。") from None
        if not valid:
            raise SmsDeliveryError("验证码已过期，请重新获取。")

    @staticmethod
    def _attempt_key(pre_auth_session_id: str) -> str:
        digest = hashlib.sha256(pre_auth_session_id.encode()).hexdigest()
        return "polaris:auth:sms:v1:attempt:" + digest


class TencentSmsSender:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.client = client

    def _client(self) -> Any:
        if self.client is None:
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.sms.v20210111.sms_client import SmsClient

            http = HttpProfile()
            http.endpoint = "sms.tencentcloudapi.com"
            http.reqTimeout = 10
            profile = ClientProfile()
            profile.httpProfile = http
            self.client = SmsClient(
                credential.Credential(
                    self.settings.auth_sms_secret_id.get_secret_value(),
                    self.settings.auth_sms_secret_key.get_secret_value(),
                ),
                self.settings.auth_sms_region,
                profile,
            )
        return self.client

    def send(self, phone: str, code: str, lifetime_ms: int) -> str:
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
            TencentCloudSDKException,
        )
        from tencentcloud.sms.v20210111.models import SendSmsRequest

        phone = normalize_phone(phone)
        if not re.fullmatch(r"\d{6}", code):
            raise SmsDeliveryError("验证码服务配置异常，请联系管理员。")
        minutes = str(max(1, (lifetime_ms + 59_999) // 60_000))
        params = [code, minutes]
        for attempt in range(2):
            request = SendSmsRequest()
            request.SmsSdkAppId = self.settings.auth_sms_sdk_app_id
            request.SignName = self.settings.auth_sms_sign_name
            request.TemplateId = self.settings.auth_sms_template_id
            request.PhoneNumberSet = [phone]
            request.TemplateParamSet = params
            try:
                response = self._client().SendSms(request)
            except TencentCloudSDKException as exc:
                logger.warning("SMS provider request rejected: %s", exc.get_code())
                raise SmsDeliveryError("短信发送失败，请稍后重试。") from None
            except Exception:
                raise SmsDeliveryError("短信服务连接失败，请稍后重试。") from None
            statuses = response.SendStatusSet or []
            if len(statuses) == 1 and statuses[0].Code == "Ok":
                logger.info(
                    "SMS accepted by provider; request_id=%s", response.RequestId
                )
                return str(response.RequestId)
            error_code = statuses[0].Code if statuses else "EMPTY_RESPONSE"
            if (
                attempt == 0
                and error_code
                == "FailedOperation.TemplateParamSetNotMatchApprovedTemplate"
            ):
                # 海宇的现有模板可能只有 code 一个变量；只在明确拒绝后重试。
                params = [code]
                continue
            logger.warning("SMS provider rejected message: %s", error_code)
            raise SmsDeliveryError("短信发送失败，请稍后重试。")
        raise SmsDeliveryError("短信发送失败，请稍后重试。")


class HaiyuSmsDelivery(SMSDeliveryInterface[SMSTemplateVars]):
    def __init__(
        self,
        settings: Settings,
        sender: TencentSmsSender | None = None,
        limiter: SmsRateLimiter | None = None,
    ):
        self.sender = sender or TencentSmsSender(settings)
        self.limiter = limiter or SmsRateLimiter(settings)

    async def send_sms(
        self, template_vars: SMSTemplateVars, user_context: dict[str, Any]
    ) -> None:
        phone = normalize_phone(template_vars.phone_number)
        wrapped_request = user_context.get("_default", {}).get("request")
        request = getattr(wrapped_request, "request", None)
        client = getattr(request, "client", None)
        ip = client.host if client is not None else "unknown"
        await run_in_threadpool(self.limiter.reserve, phone, ip)
        if template_vars.user_input_code is None:
            raise SmsDeliveryError("验证码服务配置异常，请联系管理员。")
        await run_in_threadpool(
            self.sender.send,
            phone,
            template_vars.user_input_code,
            min(template_vars.code_life_time, 300_000),
        )
        await run_in_threadpool(
            self.limiter.mark_attempt, template_vars.pre_auth_session_id
        )


def override_sms_apis(original: Any, limiter: SmsRateLimiter | None = None) -> Any:
    def wrap(method: Callable[..., Any], *, consent: bool = False) -> Any:
        signature = inspect.signature(method)

        async def call(*args: Any, **kwargs: Any) -> Any:
            arguments = signature.bind(*args, **kwargs).arguments
            if arguments.get("should_try_linking_with_session_user"):
                return GeneralErrorResponse("请退出当前账号后再使用手机号登录。")
            body = await arguments["api_options"].request.json()
            if not isinstance(body, dict) or body.get("acceptedAgreements") is not True:
                return GeneralErrorResponse("请先阅读并同意用户协议和隐私政策。")
            flow = body.get("flow", "login")
            if not isinstance(flow, str) or flow not in {
                "login",
                "register",
                "reset",
            }:
                return GeneralErrorResponse("无效的手机号验证用途。")
            if flow != "reset":
                from .errors import ApiError
                from .processing_consent import validate_choice

                try:
                    validate_choice(body.get("processingConsent"))
                except ApiError as exc:
                    return GeneralErrorResponse(exc.message)
            if consent and body.get("flow") in {"register", "reset"}:
                from .errors import ApiError
                from .phone_accounts import validate_password

                password = body.get("password")
                if not isinstance(password, str):
                    return GeneralErrorResponse("请设置登录密码。")
                try:
                    validate_password(password)
                except ApiError as exc:
                    return GeneralErrorResponse(exc.message)
            try:
                if consent and limiter is not None:
                    await run_in_threadpool(
                        limiter.require_attempt, arguments["pre_auth_session_id"]
                    )
                result = await method(*args, **kwargs)
                if consent and getattr(result, "status", None) == "OK":
                    from .phone_accounts import finish_otp_login

                    try:
                        await finish_otp_login(
                            result, body, arguments["api_options"].request.request
                        )
                    except Exception:
                        await result.session.revoke_session()
                        raise
                return result
            except SmsDeliveryError as exc:
                return GeneralErrorResponse(str(exc))

        return call

    original.create_code_post = wrap(original.create_code_post)
    original.resend_code_post = wrap(original.resend_code_post)
    original.consume_code_post = wrap(original.consume_code_post, consent=True)
    original.disable_phone_number_exists_get = True
    original.disable_email_exists_get = True
    return original
