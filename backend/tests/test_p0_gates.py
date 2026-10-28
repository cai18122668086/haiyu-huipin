import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import MembershipRole, MembershipStatus, UserStatus
from app.main import create_app
from app.models import (
    AssetRecord,
    CreditLedgerEntryRecord,
    CreditReservationRecord,
    IdempotencyRequestRecord,
    JobPlanRecord,
    JobRecord,
    OutboxEventRecord,
    UserRecord,
    WorkspaceLimitRecord,
    WorkspaceMemberRecord,
)
from app.outbox import publish_pending_events
from app.services import lock_workspace_limits
from app.storage import ImageValidator, LocalImageStorage, StoredImage, ValidatedImage

WORKSPACE_ID = "ws_test"


def _image_payload(
    color: tuple[int, int, int] = (30, 80, 130),
) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def _upload(
    client: TestClient,
    *,
    media_kind: str = "image",
    payload: bytes | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers=_idem(),
        data={"origin_kind": "upload", "media_kind": media_kind},
        files={
            "file": (
                f"{media_kind}.png",
                payload or _image_payload(),
                "image/png",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _local_payload(
    source_id: object,
    mask_id: object,
    *,
    intent: str = "submit",
) -> dict[str, object]:
    return {
        "type": "local_edit",
        "intent": intent,
        "display_name": "P0 gate test",
        "input": {
            "source_asset_id": source_id,
            "selection_mask_asset_id": mask_id,
        },
        "configuration": {
            "instruction": "change color",
            "quality": "balanced",
            "output_format": "png",
        },
    }


def _create_local_job(
    client: TestClient,
    source_id: object,
    mask_id: object,
    *,
    intent: str = "submit",
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=_idem(),
        json=_local_payload(source_id, mask_id, intent=intent),
    )
    assert response.status_code == 202, response.text
    return response.json()


def _table_counts(client: TestClient) -> tuple[int, ...]:
    models = [
        AssetRecord,
        JobRecord,
        JobPlanRecord,
        IdempotencyRequestRecord,
        OutboxEventRecord,
        CreditReservationRecord,
        CreditLedgerEntryRecord,
    ]
    with client.app.state.session_factory() as session:
        return tuple(
            int(
                session.scalar(
                    select(func.count()).select_from(model)
                )
                or 0
            )
            for model in models
        )


def _stored_file_count(client: TestClient) -> int:
    root = Path(client.app.state.storage.root)
    return sum(1 for item in root.rglob("*") if item.is_file())


class _FailAfterWriteStorage(LocalImageStorage):
    async def save_validated(self, image: ValidatedImage) -> StoredImage:
        await super().save_validated(image)
        raise RuntimeError("simulated storage failure after write")


class _CancelAfterWriteStorage(LocalImageStorage):
    def __init__(self, root: Path, validator: ImageValidator) -> None:
        super().__init__(root, validator)
        self.object_written = asyncio.Event()
        self.release = asyncio.Event()

    async def save_validated(self, image: ValidatedImage) -> StoredImage:
        stored = await super().save_validated(image)
        self.object_written.set()
        await self.release.wait()
        return stored


def test_workspace_limit_gate_uses_database_row_lock() -> None:
    session = Mock(spec=Session)
    limits = WorkspaceLimitRecord(workspace_id=WORKSPACE_ID)
    session.get.return_value = limits

    assert lock_workspace_limits(session, WORKSPACE_ID) is limits
    session.get.assert_called_once_with(
        WorkspaceLimitRecord,
        WORKSPACE_ID,
        with_for_update=True,
    )


def test_storage_quota_rejects_second_upload_without_overage(
    client: TestClient,
) -> None:
    payload = _image_payload()
    with client.app.state.session_factory() as session:
        limits = session.get(WorkspaceLimitRecord, WORKSPACE_ID)
        assert limits is not None
        limits.storage_quota_bytes = len(payload)
        session.commit()

    first = _upload(client, payload=payload)
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers=_idem(),
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("second.png", payload, "image/png")},
    )

    assert first["byte_size"] == len(payload)
    assert second.status_code == 413
    assert second.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"
    with client.app.state.session_factory() as session:
        used = session.scalar(
            select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                AssetRecord.workspace_id == WORKSPACE_ID
            )
        )
    assert int(used or 0) == len(payload)


def test_storage_save_failure_cleans_database_and_partial_object(
    client: TestClient,
) -> None:
    before_counts = _table_counts(client)
    before_files = _stored_file_count(client)
    original_storage = client.app.state.storage
    client.app.state.storage = _FailAfterWriteStorage(
        Path(original_storage.root),
        client.app.state.image_validator,
    )

    with pytest.raises(RuntimeError, match="simulated storage failure after write"):
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
            headers=_idem(),
            data={"origin_kind": "upload", "media_kind": "image"},
            files={"file": ("failed.png", _image_payload(), "image/png")},
        )

    assert _table_counts(client) == before_counts
    assert _stored_file_count(client) == before_files


def test_client_cancellation_after_object_write_cleans_and_propagates(
    settings: Settings,
) -> None:
    application = create_app(settings)
    storage = _CancelAfterWriteStorage(
        settings.storage_dir,
        application.state.image_validator,
    )

    async def cancel_upload() -> None:
        async with application.router.lifespan_context(application):
            application.state.storage = storage
            transport = ASGITransport(app=application, raise_app_exceptions=True)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as async_client:
                task = asyncio.create_task(
                    async_client.post(
                        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
                        headers=_idem(),
                        data={"origin_kind": "upload", "media_kind": "image"},
                        files={
                            "file": (
                                "cancelled.png",
                                _image_payload(),
                                "image/png",
                            )
                        },
                    )
                )
                await asyncio.wait_for(storage.object_written.wait(), timeout=5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
                assert task.cancelled()

            with application.state.session_factory() as session:
                asset_count = session.scalar(
                    select(func.count()).select_from(AssetRecord)
                )
                idempotency_count = session.scalar(
                    select(func.count()).select_from(IdempotencyRequestRecord)
                )
                limits = session.get(WorkspaceLimitRecord, WORKSPACE_ID)
                assert limits is not None
                assert limits.storage_quota_bytes == 10 * 1024 * 1024 * 1024
            assert asset_count == 0
            assert idempotency_count == 0
            assert not [
                path for path in settings.storage_dir.rglob("*") if path.is_file()
            ]

    asyncio.run(cancel_upload())


def test_database_commit_failure_cleans_database_and_staged_object(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_counts = _table_counts(client)
    before_files = _stored_file_count(client)
    session_class = client.app.state.session_factory.class_

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("simulated database commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(session_class, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated database commit failure"):
            client.post(
                f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
                headers=_idem(),
                data={"origin_kind": "upload", "media_kind": "image"},
                files={"file": ("failed.png", _image_payload(), "image/png")},
            )

    assert _table_counts(client) == before_counts
    assert _stored_file_count(client) == before_files


def test_quota_rejection_has_zero_database_and_storage_side_effects(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        limits = session.get(WorkspaceLimitRecord, WORKSPACE_ID)
        assert limits is not None
        limits.storage_quota_bytes = 0
        session.commit()
    before_counts = _table_counts(client)
    before_files = _stored_file_count(client)

    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers=_idem(),
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("rejected.png", _image_payload(), "image/png")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"
    assert _table_counts(client) == before_counts
    assert _stored_file_count(client) == before_files


def test_viewer_write_gate_has_zero_database_and_storage_side_effects(
    client: TestClient,
) -> None:
    source = _upload(client)
    mask = _upload(client, media_kind="mask")
    draft = _create_local_job(
        client, source["id"], mask["id"], intent="draft"
    )
    terminal = _create_local_job(client, source["id"], mask["id"])
    cancelled = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{terminal['id']}/cancel",
        headers=_idem(),
        json={"reason": "prepare terminal source"},
    )
    assert cancelled.status_code == 202

    detail = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=_idem(),
        json={
            "type": "detail_pack",
            "intent": "submit",
            "input": {
                "product_groups": [
                    {
                        "group_key": "product",
                        "asset_ids": [source["id"]],
                    }
                ]
            },
            "configuration": {
                "product_name": "viewer gate",
                "platform_code": "amazon",
                "page_count": 1,
            },
        },
    )
    assert detail.status_code == 202
    publish_pending_events(
        client.app.state.settings,
        client.app.state.session_factory,
        client.app.state.task_runner,
    )
    queued = _create_local_job(client, source["id"], mask["id"])

    viewer_id = "usr_viewer"
    with client.app.state.session_factory() as session:
        session.add(
            UserRecord(
                id=viewer_id,
                auth_provider="development",
                auth_subject=viewer_id,
                status=UserStatus.ACTIVE.value,
            )
        )
        session.add(
            WorkspaceMemberRecord(
                workspace_id=WORKSPACE_ID,
                user_id=viewer_id,
                role=MembershipRole.VIEWER.value,
                status=MembershipStatus.ACTIVE.value,
            )
        )
        session.commit()

    viewer = {"X-User-Id": viewer_id}
    before_counts = _table_counts(client)
    before_files = _stored_file_count(client)
    with client.app.state.session_factory() as session:
        before_jobs = {
            job_id: (
                session.get(JobRecord, job_id).status,
                session.get(JobRecord, job_id).state_version,
            )
            for job_id in [
                str(draft["id"]),
                str(terminal["id"]),
                str(detail.json()["id"]),
                str(queued["id"]),
            ]
        }

    requests = [
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
            headers={**viewer, **_idem()},
            files={
                "file": (
                    "viewer.png",
                    _image_payload((200, 20, 20)),
                    "image/png",
                )
            },
        ),
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
            headers={**viewer, **_idem()},
            json=_local_payload(source["id"], mask["id"]),
        ),
        client.patch(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{draft['id']}",
            headers=viewer,
            json={"display_name": "forbidden"},
        ),
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{draft['id']}/submit",
            headers={**viewer, **_idem()},
        ),
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{queued['id']}/cancel",
            headers={**viewer, **_idem()},
            json={"reason": "forbidden"},
        ),
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{terminal['id']}/retry",
            headers={**viewer, **_idem()},
            json={"scope": "full", "item_ids": []},
        ),
        client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{terminal['id']}/clone",
            headers={**viewer, **_idem()},
        ),
        client.put(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{detail.json()['id']}/plan",
            headers=viewer,
            json={
                "expected_revision": 1,
                "payload": {
                    "pages": [
                        {
                            "item_key": "page-001",
                            "title": "forbidden",
                        }
                    ]
                },
            },
        ),
        client.post(
            (
                f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/"
                f"{detail.json()['id']}/plan/confirm"
            ),
            headers={**viewer, **_idem()},
            json={"expected_revision": 1},
        ),
    ]

    assert len(requests) == 9
    for response in requests:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "WORKSPACE_ACCESS_DENIED"
    assert _table_counts(client) == before_counts
    assert _stored_file_count(client) == before_files
    with client.app.state.session_factory() as session:
        after_jobs = {
            job_id: (
                session.get(JobRecord, job_id).status,
                session.get(JobRecord, job_id).state_version,
            )
            for job_id in before_jobs
        }
    assert after_jobs == before_jobs


def test_second_submit_with_different_key_is_stable_conflict(
    client: TestClient,
) -> None:
    source = _upload(client)
    mask = _upload(client, media_kind="mask")
    draft = _create_local_job(
        client, source["id"], mask["id"], intent="draft"
    )
    route = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{draft['id']}/submit"

    first = client.post(route, headers=_idem())
    second = client.post(route, headers=_idem())

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "JOB_NOT_SUBMITTABLE"
    with client.app.state.session_factory() as session:
        reservations = session.scalar(
            select(func.count())
            .select_from(CreditReservationRecord)
            .where(CreditReservationRecord.job_id == draft["id"])
        )
        outbox = session.scalar(
            select(func.count())
            .select_from(OutboxEventRecord)
            .where(OutboxEventRecord.aggregate_id == draft["id"])
        )
    assert reservations == 1
    assert outbox == 1


def test_active_job_limit_rejects_second_job_without_overage(
    client: TestClient,
) -> None:
    source = _upload(client)
    mask = _upload(client, media_kind="mask")
    with client.app.state.session_factory() as session:
        limits = session.get(WorkspaceLimitRecord, WORKSPACE_ID)
        assert limits is not None
        limits.max_active_jobs = 1
        session.commit()

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=_idem(),
        json=_local_payload(source["id"], mask["id"]),
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=_idem(),
        json=_local_payload(source["id"], mask["id"]),
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "ACTIVE_JOB_LIMIT_REACHED"
    with client.app.state.session_factory() as session:
        active = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.workspace_id == WORKSPACE_ID,
                JobRecord.status.not_in(
                    ["partial", "completed", "failed", "cancelled"]
                ),
            )
        )
    assert active == 1


def test_external_mode_returns_401_and_readiness_reports_configuration(
    settings: Settings,
    tmp_path: Path,
) -> None:
    from app.supertokens_auth import ExternalAuthenticationError, ExternalIdentity

    class UnauthenticatedBackend:
        def initialize(self) -> None:
            return None

        def install_middleware(self, application: FastAPI) -> None:
            return None

        def cors_headers(self) -> list[str]:
            return []

        def is_ready(self) -> bool:
            return True

        async def authenticate(self, request: Request) -> ExternalIdentity:
            raise ExternalAuthenticationError("missing session")

    external_settings = settings.model_copy(
        update={
            "auth_mode": "external",
            "database_url": f"sqlite:///{tmp_path / 'external.db'}",
            "storage_dir": tmp_path / "external-storage",
        }
    )
    with TestClient(
        create_app(external_settings, UnauthenticatedBackend())
    ) as external:
        unauthenticated = external.get("/api/v1/me")
        ready = external.get("/health/ready")

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "UNAUTHENTICATED"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ok"
    assert ready.json()["checks"] == {
        "database": "ok",
        "authentication": "ok",
    }
