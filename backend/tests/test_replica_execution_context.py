"""A cold worker must receive every field from the last full native plan."""

import asyncio
import json
from copy import deepcopy

import pytest

from app.replica_completion import PLAN, STATE, CompletionRun, prepare
from app.replica_exact_contracts import canonical_hash
from app.replica_execution_context import FIELDS, ExecutionContextInvalid
from app.replica_native import ReplicaPlanAuditRejected, digest, read_json, write_json

from .test_replica_compact import setup_run


def prepared(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected) as failed:
        asyncio.run(run.prepare(payload, records))
    asyncio.run(prepare(run, payload, records, failed.value))
    return run, gateway


def cold_restore(run, gateway, snapshot):
    worker = CompletionRun(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=run.root
    )
    worker.binding = snapshot["model_protocol"]
    worker.source_metadata = snapshot
    asyncio.run(worker.restore(snapshot))
    return worker


def test_preparation_persists_all_bindings_before_the_snapshot(tmp_path):
    run, gateway = prepared(tmp_path)
    saved = read_json(run.root / "context.json")
    assert all(saved[key] == run.context[key] for key in FIELDS)
    plan = read_json(run.root / PLAN)
    assert plan["execution_context_binding"]["module_ids"] == ["M001", "M002"]
    snapshot = asyncio.run(run.snapshot())
    before = len(gateway.requests)
    worker = cold_restore(run, gateway, snapshot)
    assert all(worker.context[key] == saved[key] for key in FIELDS)
    assert worker.render_binding_version == 3
    assert len(gateway.requests) == before and not gateway.image_requests


def test_lost_derived_context_rehydrates_from_the_bound_native_plan(tmp_path):
    run, gateway = prepared(tmp_path)
    original = read_json(run.root / "context.json")
    native_bytes = (run.root / "session.json").read_bytes()
    plan_bytes = (run.root / PLAN).read_bytes()
    lost = deepcopy(original)
    for key in FIELDS:
        lost.pop(key)
    write_json(run.root / "context.json", lost)
    snapshot = asyncio.run(run.snapshot())
    before = len(gateway.requests)
    worker = cold_restore(run, gateway, snapshot)
    assert all(worker.context[key] == original[key] for key in FIELDS)
    assert all(
        read_json(run.root / "context.json")[key] == original[key] for key in FIELDS
    )
    assert (run.root / "session.json").read_bytes() == native_bytes
    assert (run.root / PLAN).read_bytes() == plan_bytes
    assert len(gateway.requests) == before and not gateway.image_requests


@pytest.mark.parametrize("tamper", ["hash", "missing-binding", "proposal", "prompt"])
def test_changed_execution_binding_stops_before_any_provider_call(tmp_path, tamper):
    run, gateway = prepared(tmp_path)
    plan = read_json(run.root / PLAN)
    if tamper == "hash":
        plan["execution_context_binding"]["context_sha256"] = "0" * 64
    elif tamper == "missing-binding":
        plan.pop("execution_context_binding")
    elif tamper == "proposal":
        plan["units"][0]["proposal"]["generation_brief"] = "A changed incomplete plan"
    else:
        plan["units"][0]["native_prompt"] = "A short generic completion prompt"
    write_json(run.root / PLAN, plan)
    state = read_json(run.root / STATE)
    state["plan_sha256"] = canonical_hash(plan)
    write_json(run.root / STATE, state)
    snapshot = asyncio.run(run.snapshot())
    before = len(gateway.requests)
    with pytest.raises(ExecutionContextInvalid) as failed:
        cold_restore(run, gateway, snapshot)
    assert failed.value.code == "REPLICA_EXECUTION_CONTEXT_INVALID"
    assert len(gateway.requests) == before and not gateway.image_requests


def test_real_http_worker_sends_all_retained_scope_to_the_image_boundary(
    client, monkeypatch, tmp_path
):
    from app.models import ReplicaDocumentRecord
    from tests.test_api import dispatch_pending
    from tests.test_replica_partial_delivery import create_case

    gateway, route, did, jid = create_case(client, monkeypatch, tmp_path, fallback=True)
    assert jid
    before = len(gateway.requests)
    dispatch_pending(client)
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "completed", job
    assert len(gateway.image_requests) == 2
    assert len(gateway.requests) == before  # No post-generation model review.
    with client.app.state.session_factory() as db:
        snapshot = db.get(ReplicaDocumentRecord, did).payload["native_executions"][jid][
            "snapshot"
        ]
    context = json.loads(snapshot["files"]["context.json"]["text"])
    plan = json.loads(snapshot["files"][PLAN]["text"])
    assert plan["completed_planning_rounds"] == 3
    assert any(
        row["decision"] == "FAIL" for row in plan["last_judgment"]["unit_audits"]
    )
    for unit, request in zip(plan["units"], gateway.image_requests, strict=True):
        assert request.prompt.startswith(unit["native_prompt"])
        evidence = json.loads(
            request.prompt.split("\nAPPROVED_MODULE_EVIDENCE=", 1)[1].splitlines()[0]
        )
        assert evidence == context["_completion_module_evidence"][unit["id"]]
        assert evidence["text_bindings"] and evidence["layout_anchors"]
    assert (
        digest(snapshot["files"]["context.json"]["text"].encode())
        == snapshot["files"]["context.json"]["sha256"]
    )
