from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.domain import MembershipRole, MembershipStatus, UserStatus
from app.main import create_app
from app.models import (
    UserRecord,
    WorkspaceCreditAccountRecord,
    WorkspaceLimitRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)
from app.onboarding import PERSONAL_WORKSPACE_INITIAL_CREDITS, personal_workspace_id
from app.supertokens_auth import (
    ExternalAuthenticationError,
    ExternalEmailNotVerifiedError,
    ExternalIdentity,
)


@dataclass
class FakeExternalAuthBackend:
    identity: ExternalIdentity | None = None
    authentication_error: Exception | None = None
    ready: bool = True
    initialized: bool = False
    middleware_installed: bool = False

    def initialize(self) -> None:
        self.initialized = True

    def install_middleware(self, application: FastAPI) -> None:
        self.middleware_installed = True

    def cors_headers(self) -> list[str]:
        return ["anti-csrf", "rid", "st-auth-mode"]

    def is_ready(self) -> bool:
        return self.ready and self.initialized

    async def authenticate(self, request: Request) -> ExternalIdentity:
        if self.authentication_error is not None:
            raise self.authentication_error
        if self.identity is None:
            raise ExternalAuthenticationError("missing session")
        return self.identity


def external_settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="test",
        auth_mode="external",
        access_url_secret="test-access-url-secret",
        database_url=f"sqlite:///{tmp_path / 'external-auth.db'}",
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=tmp_path / "storage",
        auto_process_jobs=False,
        task_dispatcher="inline",
        task_runner="mock",
        mock_job_delay_seconds=0,
        workspace_credit_enforcement_enabled=True,
        cors_origins=["https://www.tengliapi.xyz"],
    )


def verified_identity(
    *,
    primary_user_id: str = "st_primary_user_1",
    email: str = "  User@Example.COM  ",
) -> ExternalIdentity:
    return ExternalIdentity(
        primary_user_id=primary_user_id,
        recipe_user_id="st_recipe_user_1",
        email=email,
        email_verified=True,
    )


def test_external_mode_ignores_x_user_id_and_reports_ready(tmp_path: Path) -> None:
    backend = FakeExternalAuthBackend()
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        response = client.get(
            "/api/v1/me",
            headers={"X-User-Id": "usr_spoofed"},
        )
        ready = client.get("/health/ready")
        preflight = client.options(
            "/api/v1/me",
            headers={
                "Origin": "https://www.tengliapi.xyz",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "anti-csrf",
            },
        )

    assert backend.initialized is True
    assert backend.middleware_installed is True
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert ready.status_code == 200
    assert ready.json()["checks"]["authentication"] == "ok"
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-credentials"] == "true"


def test_development_mode_allows_credentialed_browser_requests(tmp_path: Path) -> None:
    settings = external_settings(tmp_path).model_copy(
        update={"auth_mode": "development"}
    )
    with TestClient(create_app(settings)) as client:
        preflight = client.options(
            "/api/v1/workspaces",
            headers={
                "Origin": "https://www.tengliapi.xyz",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Request-Id",
            },
        )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-credentials"] == "true"


def test_email_verification_claim_error_maps_to_403(tmp_path: Path) -> None:
    backend = FakeExternalAuthBackend(
        authentication_error=ExternalEmailNotVerifiedError(
            "email verification is required"
        )
    )
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        response = client.get("/api/v1/me")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_unverified_email_is_rejected_before_local_provisioning(tmp_path: Path) -> None:
    backend = FakeExternalAuthBackend(
        identity=ExternalIdentity(
            primary_user_id="st_unverified",
            recipe_user_id="st_unverified",
            email="pending@example.com",
            email_verified=False,
        )
    )
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        response = client.get("/api/v1/me")
        with cast(FastAPI, client.app).state.session_factory() as session:
            user_count = session.scalar(select(func.count()).select_from(UserRecord))
            workspace_count = session.scalar(
                select(func.count()).select_from(WorkspaceRecord)
            )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert user_count == 0
    assert workspace_count == 0


def test_verified_user_is_jit_mapped_and_personal_workspace_is_idempotent(
    tmp_path: Path,
) -> None:
    backend = FakeExternalAuthBackend(identity=verified_identity())
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        first = client.get("/api/v1/me")
        second = client.get("/api/v1/me")
        workspaces = client.get("/api/v1/workspaces")

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        body = first.json()
        assert body == {
            "id": body["id"],
            "email": "user@example.com",
            "display_name": None,
            "status": "active",
            "email_verified": True,
        }

        workspace_id = personal_workspace_id(body["id"])
        assert workspaces.status_code == 200
        assert workspaces.json() == [
            {
                "id": workspace_id,
                "name": "user\u7684\u5de5\u4f5c\u533a",
                "role": "owner",
            }
        ]

        with cast(FastAPI, client.app).state.session_factory() as session:
            user = session.get(UserRecord, body["id"])
            workspace = session.get(WorkspaceRecord, workspace_id)
            membership = session.get(
                WorkspaceMemberRecord,
                (workspace_id, body["id"]),
            )
            limits = session.get(WorkspaceLimitRecord, workspace_id)
            credits = session.get(WorkspaceCreditAccountRecord, workspace_id)
            user_count = session.scalar(select(func.count()).select_from(UserRecord))
            workspace_count = session.scalar(
                select(func.count()).select_from(WorkspaceRecord)
            )

    assert user is not None
    assert user.auth_provider == "supertokens"
    assert user.auth_subject == "st_primary_user_1"
    assert user.email_normalized == "user@example.com"
    assert workspace is not None
    assert workspace.created_by_user_id == user.id
    assert membership is not None
    assert membership.role == MembershipRole.OWNER.value
    assert membership.status == MembershipStatus.ACTIVE.value
    assert limits is not None
    assert credits is not None
    assert credits.balance_credits == PERSONAL_WORKSPACE_INITIAL_CREDITS == 0
    assert credits.reserved_credits == 0
    assert user_count == 1
    assert workspace_count == 1


def test_external_email_sync_and_disabled_user_rejection(tmp_path: Path) -> None:
    backend = FakeExternalAuthBackend(identity=verified_identity())
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        created = client.get("/api/v1/me")
        user_id = created.json()["id"]

        backend.identity = verified_identity(email="renamed@example.com")
        updated = client.get("/api/v1/me")
        assert updated.status_code == 200
        assert updated.json()["email"] == "renamed@example.com"

        with cast(FastAPI, client.app).state.session_factory() as session:
            user = session.get(UserRecord, user_id)
            assert user is not None
            user.status = UserStatus.SUSPENDED.value
            session.commit()

        disabled = client.get(
            "/api/v1/me",
            headers={"X-User-Id": "usr_spoofed"},
        )
        with cast(FastAPI, client.app).state.session_factory() as session:
            user_count = session.scalar(select(func.count()).select_from(UserRecord))

    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "ACCOUNT_DISABLED"
    assert user_count == 1
