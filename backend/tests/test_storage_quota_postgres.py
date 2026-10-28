from __future__ import annotations

import os
import threading
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.main import create_app
from app.models import (
    AssetRecord,
    IdempotencyRequestRecord,
    WorkspaceLimitRecord,
)

POSTGRES_ENV = "POLARIS_TEST_DATABASE_URL"
WORKSPACE_ID = "ws_quota_v2"


@dataclass(frozen=True)
class PostgresUploadContext:
    client: TestClient
    session_factory: sessionmaker[Session]
    storage_root: Path


def _image_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (220, 20, 60)).save(output, format="PNG")
    payload = output.getvalue()
    assert len(payload) == 117
    return payload


@pytest.fixture
def postgres_upload_context(tmp_path: Path) -> Iterator[PostgresUploadContext]:
    database_url = os.environ.get(POSTGRES_ENV)
    if not database_url:
        pytest.skip(f"set {POSTGRES_ENV} to run real PostgreSQL concurrency tests")

    base_url = make_url(database_url)
    if base_url.get_backend_name() != "postgresql":
        raise pytest.UsageError(f"{POSTGRES_ENV} must use PostgreSQL")

    schema_name = f"quota_v2_{uuid4().hex}"
    admin_engine = create_engine(base_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    existing_options = str(base_url.query.get("options", "")).strip()
    connection_options = (
        f"{existing_options} -csearch_path={schema_name} -clock_timeout=3000"
    ).strip()
    scoped_url = base_url.update_query_dict({"options": connection_options})
    storage_root = tmp_path / "storage"
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="development",
        development_user_id="usr_quota_v2",
        development_workspace_id=WORKSPACE_ID,
        development_workspace_name="Quota concurrency v2",
        access_url_secret="test-access-url-secret",
        database_url=scoped_url.render_as_string(hide_password=False),
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=storage_root,
        auto_process_jobs=False,
        task_dispatcher="inline",
        mock_job_delay_seconds=0,
        max_upload_bytes=2 * 1024 * 1024,
        max_image_pixels=2_000_000,
    )

    try:
        with TestClient(
            create_app(settings),
            raise_server_exceptions=False,
        ) as client:
            yield PostgresUploadContext(
                client=client,
                session_factory=client.app.state.session_factory,
                storage_root=storage_root,
            )
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _set_exact_quota(context: PostgresUploadContext, quota_bytes: int) -> None:
    with context.session_factory() as session:
        limits = session.get(WorkspaceLimitRecord, WORKSPACE_ID)
        assert limits is not None
        limits.storage_quota_bytes = quota_bytes
        session.commit()


def _concurrent_uploads(
    context: PostgresUploadContext,
    payload: bytes,
) -> list[Response]:
    barrier = threading.Barrier(2)

    def upload(_: int) -> Response:
        barrier.wait(timeout=5)
        return context.client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": "image"},
            files={"file": ("image.png", payload, "image/png")},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(upload, index) for index in range(2)]
        return [future.result(timeout=10) for future in futures]


def _assert_atomic_round(
    context: PostgresUploadContext,
    payload: bytes,
) -> None:
    responses = _concurrent_uploads(context, payload)
    counts = Counter(response.status_code for response in responses)
    assert counts == Counter({201: 1, 413: 1}), [
        (response.status_code, response.text) for response in responses
    ]
    rejection = next(response for response in responses if response.status_code == 413)
    assert rejection.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"

    with context.session_factory() as session:
        used_bytes = session.scalar(
            select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                AssetRecord.workspace_id == WORKSPACE_ID
            )
        )
        asset_count = session.scalar(
            select(func.count())
            .select_from(AssetRecord)
            .where(AssetRecord.workspace_id == WORKSPACE_ID)
        )
        idempotency_count = session.scalar(
            select(func.count())
            .select_from(IdempotencyRequestRecord)
            .where(IdempotencyRequestRecord.workspace_id == WORKSPACE_ID)
        )
    stored_files = [path for path in context.storage_root.rglob("*") if path.is_file()]
    assert (int(used_bytes or 0), asset_count, idempotency_count) == (
        len(payload),
        1,
        1,
    )
    assert len(stored_files) == 1


def _reset_round(context: PostgresUploadContext) -> None:
    with context.session_factory() as session:
        session.execute(
            delete(IdempotencyRequestRecord).where(
                IdempotencyRequestRecord.workspace_id == WORKSPACE_ID
            )
        )
        session.execute(
            delete(AssetRecord).where(AssetRecord.workspace_id == WORKSPACE_ID)
        )
        session.commit()
    for path in context.storage_root.rglob("*"):
        if path.is_file():
            path.unlink()


def test_postgres_dual_request_quota_is_atomic(
    postgres_upload_context: PostgresUploadContext,
) -> None:
    payload = _image_payload()
    _set_exact_quota(postgres_upload_context, len(payload))

    _assert_atomic_round(postgres_upload_context, payload)


def test_postgres_quota_twenty_rounds_without_deadlock(
    postgres_upload_context: PostgresUploadContext,
) -> None:
    payload = _image_payload()
    _set_exact_quota(postgres_upload_context, len(payload))

    for _ in range(20):
        _reset_round(postgres_upload_context)
        _assert_atomic_round(postgres_upload_context, payload)
