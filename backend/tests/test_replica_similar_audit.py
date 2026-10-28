"""Read-only production audit: synthetic fixtures, no remote model calls."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import JobItemRecord, JobRecord, ReplicaDocumentRecord, utc_now
from app.replica_completion import PLAN, STATE, CompletionRun, prepare
from app.replica_native import digest, read_json, write_json
from tests.test_replica_partial_delivery import create_case


def frozen_case(
    client, monkeypatch, tmp_path, *, all_generated=False, unknown=False, screens=2
):
    gateway, route, did, jid = create_case(
        client, monkeypatch, tmp_path, screens=screens
    )
    with client.app.state.session_factory() as db:
        values = deepcopy(db.get(ReplicaDocumentRecord, did).payload)
    snapshot = values["native_snapshot"]
    run = CompletionRun(
        "exact",
        did,
        "ws_test",
        gateway,
        client.app.state.storage,
        root=Path(snapshot["root"]),
    )
    run.source_metadata = snapshot
    run.binding = snapshot.get("model_protocol")
    asyncio.run(run.restore(snapshot))
    plan, state = read_json(run.root / PLAN), run.state()
    for index in range(2 if all_generated else 1):
        output = run.root / "artwork" / f"audit-{index}.png"
        output.parent.mkdir(exist_ok=True)
        Image.new("RGB", tuple(plan["units"][index]["canvas"]), "blue").save(output)
        state["modules"][index].update(
            status="GENERATED", artwork=str(output), sha256=digest(output.read_bytes())
        )
    write_json(run.root / STATE, state)
    run.usage.append(
        {
            "kind": "image",
            "network_requests": 1,
            "error_code": "PROVIDER_RESPONSE_INVALID"
            if unknown
            else "IMAGE_ASYNC_TASK_FAILED",
            "provider_request_id": "ambiguous-receipt"
            if unknown
            else "old-supplier-receipt",
        }
    )
    frozen = asyncio.run(run.snapshot())
    with client.app.state.session_factory() as db:
        db.get(ReplicaDocumentRecord, did).payload = {
            **values,
            "native_executions": {jid: {"snapshot": frozen, "delivery": []}},
        }
        job = db.get(JobRecord, jid)
        job.status, job.finished_at = "failed", utc_now()
        job.error_code = (
            "PROVIDER_OUTCOME_UNKNOWN" if unknown else "REPLICA_CANVAS_MISMATCH"
        )
        job.error_message = "Current actual failure"
        job.error_details = {
            "provider_outcome": "outcome_unknown" if unknown else "failed",
            "provider_request_id": "current-receipt",
        }
        for item in db.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == jid)
        ):
            item.status = "succeeded" if item.ordinal == 0 else "failed"
            if item.ordinal == 1:
                item.error_code, item.error_message = job.error_code, job.error_message
                item.error_details = deepcopy(job.error_details)
        db.commit()
    return route, did, jid, gateway


def test_recovery_must_not_overwrite_current_failure_with_older_usage(
    client, monkeypatch, tmp_path
):
    route, did, jid, gateway = frozen_case(client, monkeypatch, tmp_path)
    response = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
    assert response.status_code == 200, response.text
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert not gateway.image_requests
    assert job["items"][1]["error_code"] == "REPLICA_CANVAS_MISMATCH", job["items"][1]


def test_recovery_must_preserve_unknown_outcome_and_receipt(
    client, monkeypatch, tmp_path
):
    route, did, jid, gateway = frozen_case(client, monkeypatch, tmp_path, unknown=True)
    response = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
    assert response.status_code == 200, response.text
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert not gateway.image_requests
    assert job["error_code"] == "PROVIDER_OUTCOME_UNKNOWN", job["error_details"]
    assert job["error_details"]["provider_outcome"] == "outcome_unknown"


def test_all_recovered_outputs_must_clear_obsolete_job_failure(
    client, monkeypatch, tmp_path
):
    route, did, jid, gateway = frozen_case(
        client, monkeypatch, tmp_path, all_generated=True
    )
    response = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
    assert response.status_code == 200, response.text
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert not gateway.image_requests
    assert job["status"] == "completed"
    assert job["error_code"] is None, (
        job["status"],
        job["error_code"],
        job["error_message"],
    )


def test_style_preplan_failure_must_not_create_simplified_completion(tmp_path):
    from app.replica_native_plan import ReplicaPlanIncomplete
    from app.replica_style_evidence import StyleEvidenceInvalid
    from tests.test_replica_style_analysis import setup_run

    run, analysis, refs, payload, records = setup_run(tmp_path)
    run.context.update(
        product_paths=[str(run.root / "inputs/product-00.img")],
        reference_paths=[refs["R01"]["path"]],
    )
    run.journal["cursor"] = {
        "phase": "review",
        "units": [
            {"reference_index": 0, "bbox": [0, 0, 64, 64]},
            {"reference_index": 0, "bbox": [0, 64, 64, 128]},
        ],
        "observation": {"route": "DETAIL_PAGE"},
        "analysis": analysis,
    }
    with pytest.raises(ReplicaPlanIncomplete):
        asyncio.run(
            prepare(run, payload, records, StyleEvidenceInvalid("correction limit"))
        )


def test_style_completed_detailed_rounds_remain_eligible_without_fake_pass(tmp_path):
    from app.replica_native_plan import style_completion_ready
    from tests.test_replica_style_delivery import fixture

    run, _, _, calls = fixture(tmp_path)
    state = deepcopy(run.state())
    state["state"] = "PLAN_AUDIT_REQUIRED"
    state["plan_audit"]["pass"] = False
    before = deepcopy(state)
    assert not style_completion_ready(state, {"phase": "rejected", "revision": 2})
    assert style_completion_ready(state, {"phase": "rejected", "revision": 3})
    assert state == before and not calls


def test_reopening_style_preplan_failure_does_not_start_completion(
    client, monkeypatch, tmp_path
):
    gateway, route, did, _ = create_case(client, monkeypatch, tmp_path)
    with client.app.state.session_factory() as db:
        row = db.get(ReplicaDocumentRecord, did)
        values = deepcopy(row.payload)
        snapshot = values["native_snapshot"]
        values.update(
            replication_method="style",
            job_ids=[],
            error_code="REPLICA_STYLE_EVIDENCE_INVALID",
            _analysis_checkpoint={
                "method": "style",
                "root": snapshot["root"],
                "run": snapshot["run"],
                "files": {},
            },
        )
        row.status, row.payload = "failed", values
        db.commit()
    before = len(gateway.requests)
    for _ in range(3):
        doc = client.get(route + "/documents/" + did).json()
        assert doc["status"] == "failed"
        assert not doc["payload"].get("job_ids")
    assert len(gateway.requests) == before and not gateway.image_requests


def test_legacy_unknown_receipt_relocation_keeps_retry_blocked(
    client, monkeypatch, tmp_path
):
    from uuid import uuid4

    from tests.test_replica_retry import json_record

    route, did, jid, gateway = frozen_case(client, monkeypatch, tmp_path, unknown=True)
    with client.app.state.session_factory() as db:
        row = db.get(ReplicaDocumentRecord, did)
        values = deepcopy(row.payload)
        snapshot = values["native_executions"][jid]["snapshot"]
        snapshot["files"]["invocations.json"] = json_record(
            {
                "legacy-token": {
                    "state": "invoked",
                    "provider_request_id": "ambiguous-receipt",
                }
            }
        )
        row.payload = values
        job = db.get(JobRecord, jid)
        job.error_code = "REPLICA_SKILL_BLOCKED"
        for item in db.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == jid)
        ):
            item.status = "failed" if item.ordinal == 0 else "skipped"
            item.error_code = "REPLICA_SKILL_BLOCKED"
        db.commit()
    response = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
    assert response.status_code == 200, response.text
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["items"][1]["error_code"] == "PROVIDER_OUTCOME_UNKNOWN"
    assert (
        job["items"][1]["error_details"]["provider_request_id"] == "ambiguous-receipt"
    )
    retry = client.post(
        route + f"/sessions/{did}/jobs/{jid}/retry", json={"request_id": uuid4().hex}
    )
    assert retry.status_code == 409, retry.text
    assert not gateway.image_requests


def test_recovered_unknown_screen_clears_its_stale_error_while_missing_screens_remain(
    client, monkeypatch, tmp_path
):
    route, did, jid, gateway = frozen_case(
        client, monkeypatch, tmp_path, all_generated=True, unknown=True, screens=3
    )
    with client.app.state.session_factory() as db:
        last = db.scalar(
            select(JobItemRecord).where(
                JobItemRecord.job_id == jid, JobItemRecord.ordinal == 2
            )
        )
        last.status, last.error_code = "skipped", "REPLICA_SEQUENCE_HALTED"
        db.commit()
    response = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
    assert response.status_code == 200, response.text
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "partial" and job["succeeded_item_count"] == 2
    assert job["failed_item_count"] == 0 and job["error_code"] is None
    assert job["items"][2]["status"] == "skipped"
    assert not gateway.image_requests


def test_receipt_file_lock_retries_local_save_without_another_paid_post(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from tests.test_haiyu_transport import JournalTransport, Transport, png, request

    original = Path.replace
    attempts = []

    def transient_lock(self, target):
        attempts.append(str(target))
        if len(attempts) < 3:
            raise PermissionError("temporary sharing violation")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", transient_lock)
    monkeypatch.setattr("app.haiyu_transport.time.sleep", lambda _: None)
    monkeypatch.setattr("app.haiyu_transport.download_result", lambda *_: png())
    delegate = Transport([(202, {"id": "original-accepted-task", "status": "queued"})])
    status, payload = request(JournalTransport(delegate, journal_dir=tmp_path))
    assert status == 200 and payload["haiyu_task_id"] == "original-accepted-task"
    assert len(delegate.calls) == 1 and len(attempts) == 3
    assert not list(tmp_path.glob("*.tmp"))
