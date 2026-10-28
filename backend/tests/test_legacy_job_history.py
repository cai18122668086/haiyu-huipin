from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models import JobAssetRecord, JobRecord, OutboxEventRecord, utc_now


@pytest.fixture
def legacy_job(client):
    client.get("/api/v1/workspaces")
    with client.app.state.session_factory() as session:
        session.add_all(
            [
                JobRecord(
                    id="job_legacy_replica",
                    workspace_id="ws_test",
                    type="product_replica",
                    status="completed",
                    progress=100,
                    expected_item_count=1,
                    succeeded_item_count=1,
                    finished_at=utc_now(),
                ),
                JobRecord(
                    id="job_current_image",
                    workspace_id="ws_test",
                    type="product_image",
                    status="failed",
                    failed_item_count=1,
                    finished_at=utc_now(),
                ),
            ]
        )
        session.commit()
    return "job_legacy_replica"


def test_legacy_record_does_not_break_current_history_or_detail(client, legacy_job):
    response = client.get(
        "/api/v1/workspaces/ws_test/jobs?limit=100&include_total=true"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    records = {item["id"]: item for item in response.json()["items"]}
    assert records[legacy_job]["type"] == "product_replica"
    assert records[legacy_job]["allowed_actions"] == []
    assert records["job_current_image"]["type"] == "product_image"
    detail = client.get(f"/api/v1/workspaces/ws_test/jobs/{legacy_job}")
    assert detail.status_code == 200
    assert detail.json()["type"] == "product_replica"
    assert detail.json()["allowed_actions"] == []
    assert (
        client.get(f"/api/v1/workspaces/ws_other/jobs/{legacy_job}").status_code == 404
    )


@pytest.mark.parametrize("action", ["retry", "clone", "cancel"])
def test_retired_executor_cannot_be_restarted(client, legacy_job, action):
    body = {"scope": "failed", "item_ids": []} if action == "retry" else {}
    response = client.post(
        f"/api/v1/workspaces/ws_test/jobs/{legacy_job}/{action}",
        json=body,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LEGACY_JOB_READ_ONLY"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEventRecord)) == 0


def test_legacy_type_remains_invalid_for_creation(client, legacy_job):
    response = client.post(
        "/api/v1/workspaces/ws_test/jobs",
        json={
            "type": "product_replica",
            "intent": "submit",
            "input": {},
            "configuration": {},
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422


def test_legacy_output_remains_available_as_an_individual_download(client, legacy_job):
    content = BytesIO()
    Image.new("RGB", (16, 16), "purple").save(content, format="PNG")
    uploaded = client.post(
        "/api/v1/workspaces/ws_test/assets",
        files={
            "file": ("preserved.png", content.getvalue(), "image/png"),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert uploaded.status_code == 201
    asset_id = uploaded.json()["id"]
    with client.app.state.session_factory() as session:
        session.add(
            JobAssetRecord(
                workspace_id="ws_test",
                job_id=legacy_job,
                asset_id=asset_id,
                direction="output",
                role="result",
                ordinal=0,
                is_deliverable=True,
            )
        )
        session.commit()
    detail = client.get(f"/api/v1/workspaces/ws_test/jobs/{legacy_job}")
    assert detail.json()["output_asset_count"] == 1
    access = client.post(
        f"/api/v1/workspaces/ws_test/assets/{asset_id}/access-url",
        json={"disposition": "attachment", "variant": "original"},
    )
    assert access.status_code == 200
    download = client.get(access.json()["url"])
    assert download.status_code == 200
    assert download.content == content.getvalue()
