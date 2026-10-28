from uuid import uuid4

import pytest

from app.models import JobItemRecord, JobRecord, ReplicaDocumentRecord, utc_now
from app.replica_progress import native_progress, reconcile_document


def test_reviewed_work_is_distinct_from_accepted_and_delivered():
    progress = native_progress(
        {
            "state": "SET_COMPLETE",
            "module_index": 3,
            "modules": [
                {"id": "M001", "status": "COMPLETE", "attempts": []},
                {
                    "id": "M002",
                    "status": "REJECTED_BEST_EFFORT",
                    "attempts": [
                        {
                            "judgment": {
                                "major_mismatches": [
                                    "Visible product shape differs from the source"
                                ]
                            }
                        }
                    ],
                },
                {"id": "M003", "status": "COMPLETE", "attempts": []},
            ],
        }
    )
    assert progress["reviewed"] == 3 and progress["accepted"] == 2
    assert progress["rejected"] == 1 and progress["percentage"] < 100
    assert progress["modules"][1]["reasons"] == [
        "Visible product shape differs from the source"
    ]


def test_backend_persists_analysis_high_water_during_replanning(client, monkeypatch):
    from app.replica_native import NativeReplicaRun

    from .test_replica_auto_start import setup

    route, body = setup(client, monkeypatch)
    body["auto_start_after_plan"] = False
    prepared = NativeReplicaRun.prepare
    observed = []

    async def prepare(self, payload, records):
        for value in (12, 82, 48, 82):
            self.on_progress(value, "Observed planning/review step")
            with client.app.state.session_factory() as db:
                observed.append(
                    db.get(ReplicaDocumentRecord, self.document_id).payload["progress"]
                )
        return await prepared(self, payload, records)

    monkeypatch.setattr(NativeReplicaRun, "prepare", prepare)
    response = client.post(route + "/sessions", json=body)
    assert response.status_code == 202
    assert observed == [12, 82, 82, 82]


def test_active_attempt_exposes_repair_without_counting_it_as_passed():
    progress = native_progress(
        {
            "state": "REPAIR_READY",
            "module_index": 0,
            "modules": [
                {
                    "id": "M001",
                    "status": "PENDING",
                    "attempts": [
                        {
                            "status": "REPAIR_REQUIRED",
                            "judgment": {
                                "major_mismatches": [
                                    "Visible product shape differs from the source"
                                ]
                            },
                        }
                    ],
                }
            ],
        }
    )
    assert progress["modules"][0]["status"] == "REPAIR_REQUIRED"
    assert progress["accepted"] == progress["reviewed"] == 0
    assert progress["modules"][0]["reasons"]


@pytest.mark.parametrize("job_status", ["failed", "cancelled", "completed", "partial"])
@pytest.mark.parametrize("incomplete_native_submission", [False, True])
def test_document_history_uses_actual_job_terminal_state(
    client, job_status, incomplete_native_submission
):
    client.get("/api/v1/workspaces")
    with client.app.state.session_factory() as session:
        job_id, document_id = "job_" + uuid4().hex, "rep_" + uuid4().hex
        job = JobRecord(
            id=job_id,
            workspace_id="ws_test",
            type="reference_remix",
            status=job_status,
            expected_item_count=2,
            succeeded_item_count=2
            if job_status == "completed"
            else 1
            if job_status == "partial"
            else 0,
            failed_item_count=1 if job_status in {"failed", "partial"} else 0,
            finished_at=utc_now(),
        )
        row = ReplicaDocumentRecord(
            id=document_id,
            workspace_id="ws_test",
            created_by="usr_test",
            kind="session",
            fingerprint=uuid4().hex,
            status="generating",
            payload={
                "job_ids": [job_id],
                "submission_complete": True,
                "error": "Previous attempt failed",
                "error_code": "REPLICA_SKILL_BLOCKED",
            },
        )
        if incomplete_native_submission:
            job.configuration = {"replica_session_id": document_id}
            row.payload = {
                **row.payload,
                "submission_complete": False,
                "native_snapshot": {"schema": "replica-native-v1"},
                "confirmed_instructions_hash": "confirmed-fixture",
            }
        session.add_all([job, row])
        session.flush()
        if job_status == "failed":
            session.add(
                JobItemRecord(
                    id="item_" + uuid4().hex,
                    workspace_id="ws_test",
                    job_id=job_id,
                    item_key="image-001",
                    ordinal=0,
                    status="failed",
                    finished_at=utc_now(),
                    error_code="REPLICA_CANVAS_MISMATCH",
                    error_message="供应商返回的图片比例不符合当前屏要求。",
                )
            )
            session.flush()
        assert reconcile_document(session, row)
        session.commit()
    history = client.get("/api/v1/workspaces/ws_test/replica/sessions").json()
    assert (
        next(value for value in history if value["id"] == document_id)["status"]
        == job_status
    )
    if job_status == "failed":
        assert history[0]["payload"]["error_code"] == "REPLICA_CANVAS_MISMATCH"
    if job_status == "completed":
        assert not {"error", "error_code"}.intersection(history[0]["payload"])
