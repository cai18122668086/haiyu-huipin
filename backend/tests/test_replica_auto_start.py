"""New automatic suite submissions preserve native authorization and manual history."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import JobRecord, ReplicaDocumentRecord, UserRecord
from app.replica_native import NativeReplicaRun
from tests.test_replica import upload


def setup(client, monkeypatch, method="exact"):
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.replica_gateway = SimpleNamespace()
    product, reference = upload(client, "red"), upload(client, "blue")

    async def prepare(self, payload, records):
        return [
            {
                "reference": payload["references"][0],
                "analysis": {"commercial_intent": "isolated routing fixture"},
                "prompt": "isolated native prompt",
                "unit_id": "M01",
            }
        ]

    async def snapshot(self):
        return {
            "schema": "replica-native-v1",
            "skill_id": self.skill_id,
            "source_hash": self.source_hash,
            "files": {},
        }

    monkeypatch.setattr(NativeReplicaRun, "prepare", prepare)
    monkeypatch.setattr(NativeReplicaRun, "snapshot", snapshot)
    body = {
        "request_id": str(uuid4()),
        "replication_method": method,
        "product_asset_ids": [product],
        "references": [{"asset_id": reference}],
        "auto_start_after_plan": True,
    }
    return "/api/v1/workspaces/ws_test/replica", body


@pytest.mark.parametrize("method", ["exact", "style"])
def test_new_native_suite_automatically_creates_one_authorized_job(
    client,
    monkeypatch,
    method,
):
    route, body = setup(client, monkeypatch, method)
    response = client.post(route + "/sessions", json=body)
    assert response.status_code == 202, response.text
    document = client.get(route + "/documents/" + response.json()["id"]).json()
    assert document["status"] == "generating", document
    assert len(document["payload"]["job_ids"]) == 1
    assert "_automatic_submission" not in document["payload"]
    assert document["payload"]["plans"][0]["prompt"] == ""
    receipt = document["payload"]["automatic_plan_confirmation"]
    assert receipt["confirmation_source"] == "task_submission_default"
    assert receipt["actor_user_id"] == "usr_test"
    for _ in range(3):
        client.get(route + "/documents/" + document["id"])
    with client.app.state.session_factory() as db:
        jobs = db.scalars(select(JobRecord)).all()
    assert len(jobs) == 1
    assert jobs[0].configuration["replica_session_id"] == document["id"]


def test_automatic_replica_recovers_after_plan_saved_before_submission(
    client,
    monkeypatch,
):
    import app.replica_auto_start as automatic

    route, body = setup(client, monkeypatch)
    original = automatic.submit_automatic

    async def interrupted(*args):
        return

    monkeypatch.setattr(automatic, "submit_automatic", interrupted)
    response = client.post(route + "/sessions", json=body)
    document_id = response.json()["id"]
    monkeypatch.setattr(automatic, "submit_automatic", original)
    first = client.get(route + "/documents/" + document_id).json()
    assert first["status"] == "awaiting_confirmation"
    current = client.get(route + "/documents/" + document_id).json()
    assert current["status"] == "generating"
    assert len(current["payload"]["job_ids"]) == 1


def test_revoked_submitter_stops_automatic_replica_without_rerunning_analysis(
    client,
    monkeypatch,
):
    import app.replica_auto_start as automatic

    route, body = setup(client, monkeypatch)
    original = automatic.submit_automatic

    async def interrupted(*args):
        return

    monkeypatch.setattr(automatic, "submit_automatic", interrupted)
    response = client.post(route + "/sessions", json=body)
    document_id = response.json()["id"]
    with client.app.state.session_factory() as db:
        user = db.get(UserRecord, "usr_test")
        user.status = "disabled"
        record = db.get(ReplicaDocumentRecord, document_id)
        attempt_id = record.payload["analysis_attempt_id"]
        db.commit()
    monkeypatch.setattr(automatic, "submit_automatic", original)
    asyncio.run(original(client.app, document_id, attempt_id))
    with client.app.state.session_factory() as db:
        record = db.get(ReplicaDocumentRecord, document_id)
        assert record.status == "awaiting_confirmation"
        assert (
            record.payload["automatic_submission_error_code"]
            == "REPLICA_AUTOMATIC_SUBMISSION_FAILED"
        )
        assert db.scalar(select(JobRecord.id)) is None
        assert len(record.payload["plans"]) == 1
