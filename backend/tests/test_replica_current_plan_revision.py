"""Current-protocol recovery through the real planner, HTTP API and worker."""

import asyncio
import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app import replica
from app.models import ReplicaDocumentRecord
from app.replica_analysis_recovery import (
    configure_run,
    exact_revision_available,
)
from app.replica_exact_compact import CompactExactReplicaRun, protocol_binding
from app.replica_exact_contracts import canonical_hash
from app.replica_native import ReplicaNativeError, ReplicaPlanAuditRejected
from app.task_runner import MockTaskRunner
from tests.test_api import dispatch_pending
from tests.test_replica_compact import SyntheticGateway, setup_run


def failed_plan(tmp_path):
    gateway = SyntheticGateway()
    gateway.reject_always = True
    run, _, payload, records = setup_run(tmp_path, gateway)
    with pytest.raises(ReplicaPlanAuditRejected):
        asyncio.run(run.prepare(payload, records))
    snapshot = deepcopy(asyncio.run(run.snapshot()))
    values = {
        **payload,
        "replication_method": "exact",
        "_exact_model_protocol": protocol_binding()["version"],
        "_analysis_checkpoint": snapshot,
        "error_code": "REPLICA_PLAN_AUDIT_REJECTED",
    }
    values["_analysis_input_hash"] = replica._analysis_input_hash(values)
    return run, gateway, values, records


def authorize(values):
    return {
        **values,
        "_exact_plan_revision": {
            "id": uuid4().hex,
            "parent_snapshot_sha256": canonical_hash(values["_analysis_checkpoint"]),
        },
    }


def test_revision_window_persists_and_cannot_extend_on_transport_retry(tmp_path):
    run, gateway, values, records = failed_plan(tmp_path)
    original = deepcopy(values["_analysis_checkpoint"])
    values = authorize(values)
    configure_run(run, values, original)
    assert run.planning_revisions == 6
    with pytest.raises(ReplicaPlanAuditRejected):
        asyncio.run(run.prepare(values, records))
    assert run.checkpoints["plan_cursor"]["rounds"] == 6
    asyncio.run(run.checkpoint())
    snapshot = asyncio.run(run.snapshot())
    rmtree(run.root)
    resumed = CompactExactReplicaRun(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=run.root
    )
    asyncio.run(resumed.restore(snapshot))
    configure_run(resumed, values, snapshot)
    before = len(gateway.requests)
    with pytest.raises(ReplicaPlanAuditRejected):
        asyncio.run(resumed.prepare(values, records))
    assert resumed.planning_revisions == 6 and len(gateway.requests) == before
    assert values["_analysis_checkpoint"] == original


@pytest.mark.parametrize("defect", ["facts", "binding", "session", "cursor", "jobs"])
def test_changed_or_executing_checkpoint_is_not_offered_revision(tmp_path, defect):
    run, _, values, _ = failed_plan(tmp_path)
    if defect == "facts":
        values["facts"] += " changed"
    elif defect == "binding":
        values["_analysis_checkpoint"]["model_protocol"]["content_sha256"] = "0" * 64
    elif defect == "jobs":
        values["job_ids"] = ["already_started"]
    else:
        name = "session.json" if defect == "session" else "compact-checkpoints.json"
        values["_analysis_checkpoint"]["files"][name]["sha256"] = "0" * 64
    assert not exact_revision_available(
        SimpleNamespace(status="failed", payload=values),
        replica._analysis_input_hash(values),
    )
    assert run.binding == protocol_binding()


def test_revision_authority_must_match_original_parent(tmp_path):
    run, _, values, _ = failed_plan(tmp_path)
    values = authorize(values)
    values["_exact_plan_revision"]["parent_snapshot_sha256"] = "0" * 64
    with pytest.raises(ReplicaNativeError, match="授权"):
        configure_run(run, values, values["_analysis_checkpoint"])


def test_legacy_manual_revision_restores_only_failed_plan_then_delivers_without_review(
    client, tmp_path, monkeypatch
):
    settings = client.app.state.settings
    settings.reference_remix_provider_enabled = True
    settings.replica_exact_compact_workspaces = ("ws_test",)
    gateway = SyntheticGateway()
    gateway.reject_always = True
    client.app.state.replica_gateway = gateway
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        reference_remix_provider_enabled=True,
        aurora_work_root=tmp_path / "worker",
    )
    original_init = CompactExactReplicaRun.__init__

    def isolated_init(self, *args, **kwargs):
        kwargs.setdefault("root", tmp_path / "native")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(CompactExactReplicaRun, "__init__", isolated_init)
    ids = []
    for i, size in enumerate([(64, 96), (256, 640)]):
        buffer = BytesIO()
        Image.new("RGB", size, "blue" if i == 0 else "yellow").save(buffer, "PNG")
        response = client.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": (f"synthetic-{i}.png", buffer.getvalue(), "image/png")},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    route = "/api/v1/workspaces/ws_test/replica"
    response = client.post(
        route + "/sessions",
        json={
            "request_id": str(uuid4()),
            "replication_method": "exact",
            "product_asset_ids": ids[:1],
            "references": [{"asset_id": ids[1]}],
            "facts": "Supported synthetic product",
            "auto_start_after_plan": False,
        },
    )
    assert response.status_code == 202
    document_id = response.json()["id"]
    current = route + "/documents/" + document_id
    failed = client.get(current).json()
    assert failed["status"] == "failed"
    assert failed["payload"]["analysis_revision_available"]
    assert failed["payload"]["analysis_progress"]["accepted"] == 1
    with client.app.state.session_factory() as session:
        original = deepcopy(session.get(ReplicaDocumentRecord, document_id).payload)
    root = Path(original["_analysis_checkpoint"]["root"]).resolve()
    assert root.is_relative_to(tmp_path.resolve())
    rmtree(root)
    before = len(gateway.requests)
    pending = []
    analyse = replica._analyse

    async def defer(*args):
        pending.append(args)

    monkeypatch.setattr(replica, "_analyse", defer)
    revise = route + "/sessions/" + document_id + "/revise"
    assert client.post(revise).status_code == 202
    assert client.post(revise).status_code == 202
    assert len(pending) == 1
    assert "_exact_plan_revision" not in client.get(current).json()["payload"]
    gateway.reject_always = False
    asyncio.run(analyse(*pending[0]))
    new = gateway.requests[before:]
    assert [json.loads(r.product_information[0])["stage"] for r in new] == [
        "plan",
        "review_plan",
    ]
    assert all("R002" in r.group_key for r in new)
    ready = client.get(current).json()
    assert ready["status"] == "awaiting_confirmation", ready
    confirmed = client.post(
        route + "/sessions/" + document_id + "/confirm",
        json={"plan_hash": ready["payload"]["plan_hash"], "instructions": []},
    )
    assert confirmed.status_code == 202, confirmed.text
    ready = client.get(current).json()
    assert len(ready["payload"]["job_ids"]) == 1
    assert client.post(revise).status_code == 409
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, document_id).payload
        old = original["_analysis_checkpoint"]
        assert saved["_analysis_lineage"][-1] == old
        approved = json.loads(saved["native_snapshot"]["files"]["session.json"]["text"])
        previous = json.loads(old["files"]["session.json"]["text"])
        assert approved["plan"]["modules"][0] == previous["plan"]["modules"][0]
        assert saved["native_snapshot"]["model_protocol"] == old["model_protocol"]
    calls_before_delivery = len(gateway.requests)
    dispatch_pending(client)
    job = client.get(
        "/api/v1/workspaces/ws_test/jobs/" + ready["payload"]["job_ids"][0]
    ).json()
    assert job["status"] == "completed" and job["succeeded_item_count"] == 2
    assert len(gateway.image_requests) == 2
    assert len(gateway.requests) == calls_before_delivery
    assert client.get(current).json()["status"] == "completed"
