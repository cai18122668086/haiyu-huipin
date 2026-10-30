from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import UserRecord, WorkspaceRecord
from app.sms_auth import (
    HaiyuSmsDelivery,
    SmsDeliveryError,
    SmsRateLimiter,
    TencentSmsSender,
    normalize_phone,
    override_sms_apis,
)
from app.supertokens_auth import (
    ExternalAuthenticationError,
    ExternalIdentity,
    SuperTokensAuthAdapter,
)
from tests.test_auth_external import FakeExternalAuthBackend, external_settings


def sms_settings(**kwargs: Any) -> Settings:
    return Settings(
        _env_file=None,
        auth_sms_enabled=True,
        auth_sms_secret_id="synthetic-id",
        auth_sms_secret_key="synthetic-key",
        auth_sms_sdk_app_id="synthetic-app",
        auth_sms_sign_name="synthetic-sign",
        auth_sms_template_id="synthetic-template",
        **kwargs,
    )


@pytest.mark.parametrize("phone", ["13800138000", "+8613800138000"])
def test_phone_is_canonicalized(phone: str) -> None:
    assert normalize_phone(phone) == "+8613800138000"


@pytest.mark.parametrize("phone", ["+12025550101", "1380013800", "abc", "12800138000"])
def test_non_domestic_or_invalid_phone_is_rejected(phone: str) -> None:
    with pytest.raises(SmsDeliveryError):
        normalize_phone(phone)


def test_sms_requires_complete_server_configuration() -> None:
    with pytest.raises(RuntimeError, match="SMS authentication configuration"):
        Settings(_env_file=None, auth_sms_enabled=True).validate_runtime_safety()
    assert "synthetic-key" not in repr(sms_settings())


class FakeSmsClient:
    def __init__(self, codes: list[str]):
        self.codes = codes
        self.requests: list[Any] = []

    def SendSms(self, request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            RequestId="synthetic-request",
            SendStatusSet=[SimpleNamespace(Code=self.codes.pop(0))],
        )


def test_existing_haiyu_template_accepts_single_parameter_fallback() -> None:
    client = FakeSmsClient(
        ["FailedOperation.TemplateParamSetNotMatchApprovedTemplate", "Ok"]
    )
    assert (
        TencentSmsSender(sms_settings(), client).send("13800138000", "123456", 300_000)
        == "synthetic-request"
    )
    assert [r.TemplateParamSet for r in client.requests] == [
        ["123456", "5"],
        ["123456"],
    ]
    assert client.requests[0].PhoneNumberSet == ["+8613800138000"]


def test_rejected_sms_never_reports_success_or_blindly_retries(caplog: Any) -> None:
    client = FakeSmsClient(["LimitExceeded.PhoneNumberDailyLimit"])
    with pytest.raises(SmsDeliveryError, match="短信发送失败"):
        TencentSmsSender(sms_settings(), client).send("13800138000", "123456", 300_000)
    assert len(client.requests) == 1
    assert "123456" not in caplog.text
    assert "13800138000" not in caplog.text
    assert "synthetic-key" not in caplog.text


@pytest.fixture
def isolated_redis(tmp_path: Path) -> Any:
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server is required for the atomic rate-limit test")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            binary,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    client = Redis(host="127.0.0.1", port=port, socket_timeout=1)
    try:
        for _ in range(80):
            try:
                client.ping()
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise AssertionError("isolated Redis did not start")
        yield client
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=5)


def test_rate_limits_are_atomic_across_parallel_sends(isolated_redis: Any) -> None:
    limiter = SmsRateLimiter(sms_settings(), isolated_redis)

    def attempt(_: int) -> bool:
        try:
            limiter.reserve("+8613800138000", "127.0.0.1")
            return True
        except SmsDeliveryError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 1
    assert not list(isolated_redis.scan_iter("sendcode:*"))
    assert not list(isolated_redis.scan_iter("verify:*"))


def test_sms_attempt_expires_independently_of_core_code_lifetime(
    isolated_redis: Any,
) -> None:
    limiter = SmsRateLimiter(sms_settings(), isolated_redis)
    limiter.mark_attempt("synthetic-attempt")
    limiter.require_attempt("synthetic-attempt")
    key = next(isolated_redis.scan_iter("polaris:auth:sms:v1:attempt:*"))
    assert 295 <= isolated_redis.ttl(key) <= 300
    isolated_redis.delete(key)
    with pytest.raises(SmsDeliveryError, match="已过期"):
        limiter.require_attempt("synthetic-attempt")


def test_redis_outage_fails_closed_before_sms_delivery() -> None:
    class BrokenRedis:
        def eval(self, *_: Any) -> None:
            raise ConnectionError("synthetic")

    client = FakeSmsClient(["Ok"])
    service = HaiyuSmsDelivery(
        sms_settings(),
        sender=TencentSmsSender(sms_settings(), client),
        limiter=SmsRateLimiter(sms_settings(), BrokenRedis()),
    )
    with pytest.raises(SmsDeliveryError, match="验证码服务暂时不可用"):
        asyncio.run(
            service.send_sms(
                SimpleNamespace(
                    phone_number="+8613800138000",
                    user_input_code="123456",
                    code_life_time=300_000,
                ),
                {},
            )
        )
    assert client.requests == []


def test_phone_login_creates_only_local_account_and_workspace(tmp_path: Path) -> None:
    backend = FakeExternalAuthBackend(
        identity=ExternalIdentity(
            primary_user_id="ecommerce-phone-identity",
            recipe_user_id="ecommerce-phone-recipe",
            email=None,
            email_verified=False,
            phone_number="+8613800138000",
            phone_verified=True,
        )
    )
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        first = client.get("/api/v1/me")
        second = client.get("/api/v1/me")
        assert first.status_code == 200, first.text
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["email"] is None
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(UserRecord)) == 1
            assert (
                session.scalar(select(func.count()).select_from(WorkspaceRecord)) == 1
            )
            user = session.scalar(select(UserRecord))
            assert user.phone_number == "+8613800138000"
            assert user.auth_provider == "supertokens"


def test_unverified_phone_cannot_provision_local_user(tmp_path: Path) -> None:
    backend = FakeExternalAuthBackend(
        identity=ExternalIdentity(
            primary_user_id="ecommerce-phone-identity",
            recipe_user_id="ecommerce-phone-recipe",
            email=None,
            email_verified=False,
            phone_number="+8613800138000",
            phone_verified=False,
        )
    )
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        assert client.get("/api/v1/me").status_code == 403
        with client.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(UserRecord)) == 0


def test_adapter_accepts_verified_passwordless_phone_without_email() -> None:
    adapter = SuperTokensAuthAdapter(sms_settings())
    adapter._initialized = True

    async def verify(_: Any) -> Any:
        return SimpleNamespace(
            get_user_id=lambda: "ecommerce-phone-identity",
            get_access_token_payload=lambda: {},
            get_recipe_user_id=lambda: SimpleNamespace(
                get_as_string=lambda: "recipe-1"
            ),
        )

    async def get_user(_: str) -> Any:
        return SimpleNamespace(
            login_methods=[
                SimpleNamespace(
                    recipe_user_id=SimpleNamespace(get_as_string=lambda: "recipe-1"),
                    recipe_id="passwordless",
                    phone_number="+8613800138000",
                    email=None,
                    verified=True,
                )
            ]
        )

    adapter._verify_session = verify
    adapter._get_user = get_user
    identity = asyncio.run(adapter.authenticate(SimpleNamespace()))
    assert identity.phone_verified
    assert identity.email is None
    adapter.settings = Settings(_env_file=None)
    with pytest.raises(ExternalAuthenticationError):
        asyncio.run(adapter.authenticate(SimpleNamespace()))


def test_passwordless_consent_linking_and_delivery_errors() -> None:
    called: list[bool] = []

    async def method(
        api_options: Any, should_try_linking_with_session_user: bool = False
    ) -> Any:
        called.append(True)
        raise SmsDeliveryError("短信发送失败，请稍后重试。")

    implementation = SimpleNamespace(
        create_code_post=method, resend_code_post=method, consume_code_post=method
    )
    api = override_sms_apis(implementation)

    payload: dict[str, Any] = {}

    async def body() -> Any:
        return payload

    options = SimpleNamespace(request=SimpleNamespace(json=body))
    result = asyncio.run(api.consume_code_post(options))
    assert "用户协议" in result.to_json()["message"]
    assert not called
    result = asyncio.run(api.create_code_post(options, True))
    assert "退出当前账号" in result.to_json()["message"]
    assert not called
    from app.processing_consent import POLICY_VERSION

    payload.update(
        acceptedAgreements=True,
        flow="login",
        processingConsent={
            "policyVersion": POLICY_VERSION,
            "hongKongProcessing": True,
            "aiTraining": True,
        },
    )
    result = asyncio.run(api.create_code_post(options))
    assert result.to_json()["status"] == "GENERAL_ERROR"
    assert len(called) == 1
    assert api.disable_phone_number_exists_get
