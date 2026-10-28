from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "environment": "production",
        "auth_mode": "external",
        "supertokens_api_key": "production-core-api-key",
        "supertokens_api_domain": "https://www.tengliapi.xyz",
        "supertokens_website_domain": "https://www.tengliapi.xyz",
        "supertokens_cookie_secure": True,
        "auth_smtp_host": "smtp.example.com",
        "auth_smtp_port": 587,
        "auth_smtp_username": "smtp-user",
        "auth_smtp_password": "smtp-password",
        "auth_email_from_name": "Polaris Commerce Studio",
        "auth_email_from_address": "no-reply@mail.tengliapi.xyz",
        "announcement_admin_emails": ["admin@example.com"],
        "s3_secret_key": "production-s3-secret",
        "s3_auto_create_bucket": False,
        "access_url_secret": "production-access-url-secret",
    }
    values.update(overrides)
    return Settings(**values)


def test_complete_production_auth_and_smtp_configuration_is_accepted() -> None:
    production_settings().validate_runtime_safety()


def test_production_requires_transaction_email_smtp() -> None:
    settings = production_settings(
        auth_smtp_host="",
        auth_smtp_username="",
        auth_smtp_password="",
        auth_email_from_address="",
    )

    with pytest.raises(RuntimeError, match="SMTP is required"):
        settings.validate_runtime_safety()


def test_partial_smtp_configuration_is_rejected_in_development() -> None:
    settings = Settings(auth_smtp_host="smtp.example.com")

    with pytest.raises(RuntimeError, match="configuration is incomplete"):
        settings.validate_runtime_safety()


def test_production_requires_supertokens_core_api_key() -> None:
    settings = production_settings(supertokens_api_key="")

    with pytest.raises(RuntimeError, match="Core API key is required"):
        settings.validate_runtime_safety()


def test_announcement_admin_emails_are_normalized_and_deduplicated() -> None:
    settings = Settings(
        announcement_admin_emails=[
            " Admin@Example.com ",
            "admin@example.com",
            "second@example.com",
        ]
    )

    assert settings.announcement_admin_emails == [
        "admin@example.com",
        "second@example.com",
    ]


def test_production_requires_announcement_admin_allowlist() -> None:
    settings = production_settings(announcement_admin_emails=[])

    with pytest.raises(RuntimeError, match="admin email allowlist is required"):
        settings.validate_runtime_safety()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_dispatcher", "inline", "independent worker"),
        ("auto_create_schema", True, "auto-create"),
        ("storage_backend", "local", "durable object storage"),
        ("ecommerce_proxy_enabled", True, "direct-only"),
    ],
)
def test_production_fails_closed_for_ecommerce_runtime_safety(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        production_settings(**{field: value}).validate_runtime_safety()
def test_production_provisions_object_storage_bucket_ahead_of_time() -> None:
    with pytest.raises(RuntimeError, match="provisioned ahead of time"):
        production_settings(s3_auto_create_bucket=True).validate_runtime_safety()
