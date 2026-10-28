"""Exhausted real native analysis completes automatically, without fake PASSes."""

import asyncio
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import JobRecord, ReplicaDocumentRecord
from app.replica_analysis_recovery import document_file
from app.replica_completion import PLAN, STATE, prepare
from app.replica_exact_compact import CompactExactReplicaRun
from app.replica_native import (
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    read_json,
    write_json,
)
from app.task_runner import MockTaskRunner
from tests.test_api import dispatch_pending
from tests.test_replica_compact import SyntheticGateway, setup_run


def test_style_unsafe_crops_stop_before_generation(tmp_path):
    from app.replica_native_plan import ReplicaPlanIncomplete
    from app.replica_style_evidence import StyleEvidenceInvalid
    from tests.test_replica_style_analysis import setup_run as style_setup

    run, analysis, refs, payload, records = style_setup(tmp_path)
    run.context.update(
        product_paths=[str(run.root / "inputs/product-00.img")],
        reference_paths=[refs["R01"]["path"]],
    )
    run.journal["cursor"] = {
        "phase": "review",
        "units": [{"reference_index": 0, "bbox": [0, 0, 64, 128]}],
        "observation": {"route": "DETAIL_PAGE"},
        "analysis": analysis,
    }
    original = deepcopy(run.state())
    with pytest.raises(ReplicaPlanIncomplete):
        asyncio.run(
            prepare(run, payload, records, StyleEvidenceInvalid("correction limit"))
        )
    assert run.state() == original
    assert not (run.root / PLAN).exists()


def test_failed_plan_remains_failed_evidence_but_has_executable_completion(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected) as failed:
        asyncio.run(run.prepare(payload, records))
    original = deepcopy(run.state())
    before = len(gateway.requests)
    plans = asyncio.run(prepare(run, payload, records, failed.value))
    assert len(plans) == 2 and len(gateway.requests) == before
    assert run.state() == original and original["state"] == "PLAN_REJECTED"
    completion = read_json(run.root / PLAN)
    assert completion["planning_status"] == "BEST_EFFORT"
    assert any(
        a["decision"] == "FAIL" for a in completion["last_judgment"]["unit_audits"]
    )
    assert read_json(run.root / STATE)["review_status"] == "NOT_REVIEWED"


def test_changed_original_cannot_be_hidden_by_best_effort(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected) as failed:
        asyncio.run(run.prepare(payload, records))
    product = Path(run.context["product_paths"][0])
    product.write_bytes(b"changed original")
    with pytest.raises(ReplicaNativeError, match="哈希"):
        asyncio.run(prepare(run, payload, records, failed.value))
    assert not (run.root / PLAN).exists()


def test_issued_image_cannot_be_replaced_by_analysis_completion(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected) as failed:
        asyncio.run(run.prepare(payload, records))
    write_json(run.root / "invocations.json", {"issued": {"state": "invoked"}})
    with pytest.raises(ReplicaNativeError, match="已有生图请求"):
        asyncio.run(prepare(run, payload, records, failed.value))


def test_completion_rejects_cross_workspace_object_before_restore(tmp_path):
    from app.replica_completion import CompletionRun

    run, gateway, _, _ = setup_run(tmp_path)
    snapshot = asyncio.run(run.snapshot())
    snapshot["files"]["forged.img"] = {
        "object_key": "other-workspace/replica/other-document/pixels.img",
        "sha256": "0" * 64,
    }
    completion = CompletionRun(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=run.root
    )
    completion.binding = snapshot.get("model_protocol")
    with pytest.raises(ReplicaNativeError, match="当前工作区"):
        asyncio.run(completion.restore(snapshot))


def test_http_bounded_failure_creates_one_job_and_delivers_without_extra_click(
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
        out = BytesIO()
        Image.new("RGB", size, "blue" if i == 0 else "yellow").save(out, "PNG")
        response = client.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": uuid4().hex},
            files={"file": ("test.png", out.getvalue(), "image/png")},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    route = "/api/v1/workspaces/ws_test/replica"
    response = client.post(
        route + "/sessions",
        json={
            "request_id": uuid4().hex,
            "replication_method": "exact",
            "product_asset_ids": ids[:1],
            "references": [{"asset_id": ids[1]}],
            "facts": "Supported synthetic product",
            "auto_start_after_plan": True,
        },
    )
    assert response.status_code == 202
    did = response.json()["id"]
    doc = client.get(route + "/documents/" + did).json()
    assert doc["status"] == "generating", doc
    assert doc["payload"]["planning_status"] == "BEST_EFFORT"
    assert len(doc["payload"]["job_ids"]) == 1
    with client.app.state.session_factory() as db:
        stored = db.get(ReplicaDocumentRecord, did).payload
        assert len(stored["_analysis_lineage"]) == 1
        assert (
            document_file(stored["_analysis_lineage"][0], "session.json")["state"]
            == "PLAN_REJECTED"
        )
        root = Path(stored["native_snapshot"]["root"])
    # A process restart must recover the best-effort plan, not start analysis.
    rmtree(root)
    before = len(gateway.requests)
    dispatch_pending(client)
    jid = doc["payload"]["job_ids"][0]
    job = client.get("/api/v1/workspaces/ws_test/jobs/" + jid).json()
    assert job["status"] == "completed", job
    assert job["succeeded_item_count"] == 2 and len(gateway.image_requests) == 2
    assert len(gateway.requests) == before
    assert client.get(route + "/documents/" + did).json()["status"] == "completed"
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(JobRecord)).all()) == 1
    asyncio.run(client.app.state.task_runner.run(jid))
    assert len(gateway.image_requests) == 2
    record = next(x for x in client.get(route + "/records").json() if x["id"] == did)
    assert record["status"] == "completed" and record["job_ids"] == [jid]
