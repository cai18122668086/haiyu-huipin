"""Actual native six-image protocol under submission-authorized website execution."""

import asyncio

from sqlalchemy import select

from app.models import AuditEventRecord
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset


def create_main(client, tmp_path, *, auto=True):
    gateway = FakeXingheGateway()
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        domestic_main_image_provider_enabled=True,
        aurora_work_root=tmp_path / "main",
    )
    asset = upload_asset(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "domestic_main_image",
            "intent": "submit",
            "input": {"product_asset_ids": [asset["id"]]},
            "configuration": {
                "product_name": "轻量随行杯",
                "category": "保温杯",
                "selling_points": ["杯盖防误开"],
                "auto_start_after_plan": auto,
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json(), gateway


def test_native_main_submission_delivers_six_without_manual_confirmation(
    client, tmp_path
):
    job, gateway = create_main(client, tmp_path)
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "completed", current
    assert current["plan"]["confirmed_at"]
    assert (
        current["plan"]["payload"]["automatic_final_confirmation"][
            "confirmation_source"
        ]
        == "task_submission_default"
    )
    assert len(gateway.image_calls) == 6
    before = len(gateway.image_calls)
    asyncio.run(client.app.state.task_runner.run(job["id"]))
    assert len(gateway.image_calls) == before
    with client.app.state.session_factory() as db:
        actions = db.scalars(
            select(AuditEventRecord.action).where(
                AuditEventRecord.resource_id == job["id"],
            )
        ).all()
    assert actions.count("domestic_main_image.plan_auto_confirmed") == 1
    assert actions.count("domestic_main_image.set_auto_finalized") == 1


def test_manual_history_main_keeps_original_checkpoint(client, tmp_path):
    job, gateway = create_main(client, tmp_path, auto=False)
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "awaiting_confirmation"
    assert not gateway.image_calls


def test_cancelled_main_submission_cannot_generate(client, tmp_path):
    job, gateway = create_main(client, tmp_path)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/cancel",
        headers=idem(),
        json={},
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    assert not gateway.image_calls
