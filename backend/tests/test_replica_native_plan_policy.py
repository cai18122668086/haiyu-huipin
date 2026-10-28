"""Three real native rounds retain their plan; pre-plan failures never fake one."""

import asyncio
import json
from copy import deepcopy

import pytest

from app.replica_completion import PLAN, prepare, prompt
from app.replica_native import ReplicaPlanAuditRejected, read_json
from app.replica_native_plan import ReplicaPlanIncomplete, snapshot_has_full_plan
from tests.test_replica_analysis_recovery import split_gateway
from tests.test_replica_compact import setup_run
from tests.test_replica_partial_delivery import create_case


def test_three_rejected_native_rounds_keep_geometry_copy_bindings_and_original_fail(
    tmp_path,
):
    run, gateway, payload, records = setup_run(tmp_path)
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected) as failed:
        asyncio.run(run.prepare(payload, records))
    original = deepcopy(run.state())
    assert run.checkpoints["plan_cursor"]["rounds"] == 3
    assert original["state"] == "PLAN_REJECTED"
    before = len(gateway.requests)
    asyncio.run(prepare(run, payload, records, failed.value))
    plan = read_json(run.root / PLAN)
    assert plan["completed_planning_rounds"] == 3
    assert plan["native_plan_retained"] is True
    assert run.state() == original
    assert len(gateway.requests) == before and not gateway.image_requests
    for index, unit in enumerate(plan["units"]):
        assert unit["proposal"] == original["plan"]["modules"][index]
        compiled = prompt(plan, unit, index)
        assert "PRODUCT GEOMETRY LOCK" in compiled
        assert "AI-observed reference facts:" in compiled
        assert "Approved visible copy only:" in compiled
        assert "RESOLVED_FACTS=" in compiled
        assert "FACT_ADAPTATIONS=" in compiled
        assert "best-effort plan" not in compiled
    scopes = run.context["module_constraints"]
    assert set(scopes) == {m["id"] for m in original["plan"]["modules"]}
    assert all(scope["text_bindings"] for scope in scopes.values())


def test_segmentation_failure_without_any_native_plan_cannot_generate_generic_images(
    tmp_path,
):
    from app.replica_native import ReplicaSegmentationInvalid

    run, gateway, payload, records = setup_run(tmp_path, split_gateway())
    with pytest.raises(ReplicaSegmentationInvalid) as failed:
        asyncio.run(run.prepare(payload, records))
    assert not run.checkpoints.get("plan_cursor")
    assert not snapshot_has_full_plan(asyncio.run(run.snapshot()))
    before = len(gateway.requests)
    with pytest.raises(ReplicaPlanIncomplete):
        asyncio.run(prepare(run, payload, records, failed.value))
    assert not (run.root / PLAN).exists()
    assert not gateway.image_requests and len(gateway.requests) == before


def test_http_reopening_preplan_failure_does_not_schedule_simplified_generation(
    client, monkeypatch, tmp_path
):
    gateway, route, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=False, gateway_override=split_gateway()
    )
    assert jid is None
    before = len(gateway.requests)
    for _ in range(3):
        doc = client.get(route + "/documents/" + did).json()
        assert doc["status"] == "failed"
        assert doc["payload"]["error_code"] == "REPLICA_SEGMENTATION_INVALID"
        assert not doc["payload"].get("job_ids")
    phases = [json.loads(r.product_information[0])["stage"] for r in gateway.requests]
    assert "plan" not in phases and "review_plan" not in phases
    assert len(gateway.requests) == before and not gateway.image_requests


def test_legacy_empty_proposal_cannot_be_used_for_new_image_generation():
    with pytest.raises(ReplicaPlanIncomplete):
        prompt({"method": "exact", "units": [{}]}, {"proposal": {}}, 0)


def test_legacy_full_plan_restores_native_prompt_without_replanning_or_changing_plan(
    tmp_path,
):
    from app.replica_completion import STATE, CompletionRun
    from app.replica_direct_delivery import V12_BINDING
    from app.replica_exact_compact import run_class
    from app.replica_exact_contracts import canonical_hash
    from app.replica_native import digest, write_json

    run, gateway, payload, records = setup_run(tmp_path)
    run = run_class("exact", V12_BINDING)(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=run.root
    )
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected) as failed:
        asyncio.run(run.prepare(payload, records))
    asyncio.run(prepare(run, payload, records, failed.value))
    plan = read_json(run.root / PLAN)
    for unit in plan["units"]:
        unit.pop("native_prompt")
    write_json(run.root / PLAN, plan)
    state = read_json(run.root / STATE)
    state["plan_sha256"] = canonical_hash(plan)
    write_json(run.root / STATE, state)
    snapshot = asyncio.run(run.snapshot())
    original_plan_bytes = (run.root / PLAN).read_bytes()
    original_session = digest((run.root / "session.json").read_bytes())
    restored = CompletionRun(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=run.root
    )
    restored.source_metadata = snapshot
    restored.binding = snapshot["model_protocol"]
    before = len(gateway.requests)
    asyncio.run(restored.restore(snapshot))
    assert len(restored.legacy_native_prompts) == len(plan["units"])
    assert all(
        "PRODUCT GEOMETRY LOCK" in p for p in restored.legacy_native_prompts.values()
    )
    assert (run.root / PLAN).read_bytes() == original_plan_bytes
    assert digest((run.root / "session.json").read_bytes()) == original_session
    assert len(gateway.requests) == before and not gateway.image_requests


def test_unknown_provider_receipt_survives_restore_without_another_dispatch(tmp_path):
    from app.provider import ProviderOutcome
    from app.replica_native_provider import ReplicaImageOutcomeUnknown
    from tests.test_replica_resolution import renderer, result

    response = result((64, 80), ProviderOutcome.OUTCOME_UNKNOWN)
    response.provider_request_id = "accepted-original-task"
    run, provider, render, calls = renderer(tmp_path, [response])
    for _ in range(2):
        with pytest.raises(ReplicaImageOutcomeUnknown) as failure:
            asyncio.run(render())
        assert failure.value.provider_request_id == "accepted-original-task"
    assert len(calls) == 1
    ledger = read_json(run.root / "invocations.json")
    assert ledger["resolution-test"]["state"] == "invoked"
