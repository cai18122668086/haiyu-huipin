import asyncio
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from app.domain import AssetLifecycleStatus, JobStatus, JobType
from app.models import AssetRecord, JobRecord, utc_now
from app.retention import run_retention_cycle

WORKSPACE_ID = "ws_test"


def _image_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), (34, 120, 210)).save(output, format="PNG")
    return output.getvalue()


def _upload(client: TestClient) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers={"Idempotency-Key": str(uuid4())},
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("retention.png", _image_payload(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_expired_asset_is_hidden_and_physically_purged(client: TestClient) -> None:
    created = _upload(client)
    asset_id = str(created["id"])
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, asset_id)
        assert asset is not None
        asset.expires_at = utc_now() - timedelta(seconds=1)
        stored_path = client.app.state.storage.root / asset.object_key
        session.commit()

    assert stored_path.exists()
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/assets/{asset_id}").status_code
        == 404
    )

    result = asyncio.run(
        run_retention_cycle(
            client.app.state.session_factory,
            client.app.state.storage,
            batch_size=10,
        )
    )

    assert result.purged_assets == 1
    assert result.purged_bytes > 0
    assert not stored_path.exists()
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, asset_id)
        assert asset is not None
        assert asset.lifecycle_status == AssetLifecycleStatus.DELETED.value
        assert asset.deleted_at is not None
        assert asset.purged_at is not None


def test_system_asset_is_not_purged(client: TestClient) -> None:
    created = _upload(client)
    asset_id = str(created["id"])
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, asset_id)
        assert asset is not None
        asset.retention_class = "system_persistent"
        asset.expires_at = None
        stored_path = client.app.state.storage.root / asset.object_key
        session.commit()

    result = asyncio.run(
        run_retention_cycle(
            client.app.state.session_factory,
            client.app.state.storage,
            batch_size=10,
        )
    )

    assert result.claimed_assets == 0
    assert stored_path.exists()


def test_expired_job_history_is_hidden(client: TestClient) -> None:
    job = JobRecord(
        id="job_expired_history",
        workspace_id=WORKSPACE_ID,
        type=JobType.LOCAL_EDIT.value,
        status=JobStatus.DRAFT.value,
        stage="draft",
        display_name="sensitive name",
        configuration={"instruction": "sensitive prompt"},
        history_expires_at=utc_now() - timedelta(seconds=1),
    )
    with client.app.state.session_factory() as session:
        session.add(job)
        session.commit()

    response = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job.id}")
    assert response.status_code == 404

    result = asyncio.run(
        run_retention_cycle(
            client.app.state.session_factory,
            client.app.state.storage,
            batch_size=10,
        )
    )
    assert result.expired_jobs == 1
    with client.app.state.session_factory() as session:
        expired = session.get(JobRecord, job.id)
        assert expired is not None
        assert expired.history_deleted_at is not None
        assert expired.display_name is None
        assert expired.configuration == {}
