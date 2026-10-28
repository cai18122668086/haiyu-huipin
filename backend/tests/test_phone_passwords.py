from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import _external_principal
from app.errors import ApiError
from app.main import create_app
from app.models import UserRecord
from app.phone_accounts import (
    check_password,
    hash_password,
    persist_otp_account,
    validate_password,
    verify_password_account,
)
from app.supertokens_auth import ExternalIdentity
from tests.test_auth_external import FakeExternalAuthBackend, external_settings


def identity(version: int = 0) -> ExternalIdentity:
    return ExternalIdentity(
        primary_user_id="independent-phone-account",
        recipe_user_id="phone-recipe",
        email=None,
        email_verified=False,
        phone_number="+8613800138000",
        phone_verified=True,
        credential_version=version,
    )


def test_password_hash_is_salted_and_checks_without_plaintext() -> None:
    first = hash_password("TestPassword123")
    second = hash_password("TestPassword123")
    assert first != second
    assert "TestPassword123" not in first
    assert check_password("TestPassword123", first)
    assert not check_password("wrong-password", first)
    assert not check_password("TestPassword123", None)


@pytest.mark.parametrize("value", ["short1", "onlyletters", "123456789", "X1" * 51])
def test_password_policy_is_enforced_on_server(value: str) -> None:
    with pytest.raises(ApiError):
        validate_password(value)


def test_registration_and_reset_preserve_local_identity_and_invalidate_old_sessions(
    tmp_path,
) -> None:
    settings = external_settings(tmp_path)
    backend = FakeExternalAuthBackend(identity=identity())
    with (
        TestClient(create_app(settings, backend)) as client,
        client.app.state.session_factory() as session,
    ):
        account = persist_otp_account(
            session, identity(), "register", "FirstPassword123"
        )
        assert account.version == 1
        assert (
            verify_password_account(session, account.phone, "FirstPassword123").user_id
            == account.user_id
        )
        with pytest.raises(ApiError):
            verify_password_account(session, account.phone, "WrongPassword123")
        reset = persist_otp_account(session, identity(1), "reset", "SecondPassword123")
        assert reset.user_id == account.user_id
        assert reset.version == 2
        with pytest.raises(ApiError):
            verify_password_account(session, account.phone, "FirstPassword123")
        assert (
            verify_password_account(session, account.phone, "SecondPassword123").version
            == 2
        )
        with pytest.raises(ApiError, match="密码已更新"):
            _external_principal(session, identity(1))
        assert _external_principal(session, identity(2)).user_id == account.user_id


def test_registering_existing_phone_cannot_replace_password(tmp_path) -> None:
    with (
        TestClient(
            create_app(
                external_settings(tmp_path),
                FakeExternalAuthBackend(identity=identity()),
            )
        ) as client,
        client.app.state.session_factory() as session,
    ):
        persist_otp_account(session, identity(), "register", "FirstPassword123")
        with pytest.raises(ApiError, match="已注册"):
            persist_otp_account(session, identity(1), "register", "InjectedPassword123")
        session.rollback()
        user = session.scalar(select(UserRecord))
        assert check_password("FirstPassword123", user.password_hash)


def test_password_login_endpoints_fail_closed_when_sms_is_disabled(tmp_path) -> None:
    with TestClient(
        create_app(external_settings(tmp_path), FakeExternalAuthBackend())
    ) as client:
        response = client.post(
            "/api/v1/auth/phone/password/login",
            json={"phone": "13800138000", "password": "TestPassword123"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PHONE_AUTH_NOT_READY"
