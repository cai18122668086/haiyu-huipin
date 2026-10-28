"""Failure correction preserves evidence, caches and actual durable objects."""

import asyncio
import json
from copy import deepcopy
from io import BytesIO
from shutil import rmtree
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.replica_analysis_recovery import (
    prepare_segmentation_retry,
    segmentation_diagnostics,
)
from app.replica_exact_compact import CompactExactReplicaRun, run_class
from app.replica_native import (
    ReplicaNativeError,
    ReplicaSegmentationInvalid,
    read_json,
    write_json,
)
from app.replica_style_analysis import DurableStyleAnalysisRun
from app.storage import ImageValidator, LocalImageStorage
from tests.test_replica_compact import SyntheticGateway, setup_run


def split_gateway(*, wrong_boundary=False):
    gateway = SyntheticGateway()
    gateway.correct_repair = False
    original = gateway.plan

    async def plan(request):
        result = await original(request)
        envelope = json.loads(request.product_information[0])
        phase, data = envelope["stage"], envelope["context"]
        if phase == "intake":
            result.payload["columns"] = [
                {
                    "reference_index": 0,
                    "column_index": i,
                    "rect": {"x": i * 128, "y": 0, "width": 128, "height": 640},
                    "column_boundary_reason": "Two separate synthetic columns",
                }
                for i in range(2)
            ]
        if phase in {"screen_split", "screen_inventory"}:
            for column in result.payload["columns"]:
                if column["column_id"] != "C002":
                    continue
                if wrong_boundary and phase == "screen_inventory":
                    column["boundary_ys"] = [120]
                elif phase == "screen_inventory" or (
                    gateway.correct_repair and data.get("boundary_mismatch")
                ):
                    column["screens"] = [
                        {
                            "headline": f"Physical panel {i}",
                            "buyer_question": f"Distinct supported question {i}",
                        }
                        for i in range(3)
                    ]
                    if "boundary_ys" in column:
                        column["boundary_ys"] = [200, 400]
        return result

    gateway.plan = plan
    return gateway


def test_equal_screen_counts_with_different_physical_boundaries_fail(tmp_path):
    gateway = split_gateway(wrong_boundary=True)
    run, _, payload, records = setup_run(tmp_path / "n", gateway)
    with pytest.raises(ReplicaSegmentationInvalid):
        asyncio.run(run.prepare(payload, records))
    proof = read_json(run.root / "screen-inventory.json")
    assert proof["diagnostics"]["columns"][0]["reason"] == "boundaries"
    assert proof["mismatched_columns"] == ["C002"]
    assert not gateway.image_requests


def test_explicit_boundary_correction_reuses_unaffected_columns_after_root_loss(
    tmp_path,
):
    gateway = split_gateway()
    run, _, payload, records = setup_run(tmp_path / "n", gateway)
    with pytest.raises(ReplicaSegmentationInvalid):
        asyncio.run(run.prepare(payload, records))
    snapshot = asyncio.run(run.snapshot())
    old_results = deepcopy(run.checkpoints["results"])
    before = len(gateway.requests)
    rmtree(run.root)  # Own fixture root; durable objects live outside this root.
    resumed = CompactExactReplicaRun(
        "exact", run.document_id, "ws_test", gateway, run.storage, root=run.root
    )
    asyncio.run(resumed.restore(snapshot))
    resumed.segmentation_revision = "explicit-correction-2"
    gateway.correct_repair = True
    plans = asyncio.run(resumed.prepare(payload, records))
    assert len(plans) == 5
    segmentation = [
        r for r in gateway.requests[before:] if r.group_key.startswith("identity")
    ]
    assert len(segmentation) == 2
    for request in segmentation:
        assert [
            c["column_id"]
            for c in json.loads(request.product_information[0])["context"]["columns"]
        ] == ["C002"]
    assert all(resumed.checkpoints["results"][k] == v for k, v in old_results.items())
    assert not gateway.image_requests


@pytest.mark.parametrize("version", ["exact-compact-v5", "exact-compact-v9"])
def test_legacy_failure_reuses_intake_with_original_provenance(tmp_path, version):
    gateway = split_gateway()
    base, _, payload, records = setup_run(tmp_path / "old", gateway)
    legacy_type = run_class("exact", version)
    legacy = legacy_type(
        "exact", base.document_id, "ws_test", gateway, base.storage, root=base.root
    )
    with pytest.raises(ReplicaSegmentationInvalid):
        asyncio.run(legacy.prepare(payload, records))
    parent = asyncio.run(legacy.snapshot())
    immutable = deepcopy(parent)
    resumed = CompactExactReplicaRun(
        "exact",
        base.document_id,
        "ws_test",
        gateway,
        base.storage,
        root=tmp_path / "new",
    )
    resumed.segmentation_parent = parent
    resumed.segmentation_revision = "explicit-upgrade"
    gateway.correct_repair = True
    before = len(gateway.requests)
    assert len(asyncio.run(resumed.prepare(payload, records))) == 5
    assert not any(
        json.loads(r.product_information[0])["stage"] == "intake"
        for r in gateway.requests[before:]
    )
    assert (
        list(resumed.checkpoints["inherited_observations"].values())[0][
            "source_protocol"
        ]["version"]
        == version
    )
    assert parent == immutable
    values = {"_analysis_checkpoint": parent}
    assert segmentation_diagnostics(values)["columns"][0]["split_count"] == 2
    upgraded = prepare_segmentation_retry(values)
    assert upgraded["_analysis_lineage"][0] == parent
    assert "_analysis_checkpoint" not in upgraded
    assert upgraded["_exact_model_protocol"] == "exact-compact-v15"


def style_run(tmp_path):
    storage = LocalImageStorage(
        tmp_path / "objects", ImageValidator(1_000_000, 2_000_000)
    )
    calls = []

    async def plan(request):
        calls.append(request)
        return SimpleNamespace(
            payload={"observed": "Concrete synthetic observation"},
            error_code=None,
            normalized_usage={"reported": True, "input_tokens": 7, "output_tokens": 3},
            actual_cost_micros=1,
            estimated_cost_micros=1,
        )

    gateway = SimpleNamespace(
        plan=plan,
        provider_name="synthetic",
        planner_model="synthetic",
        review_price_micros=1,
    )
    run = DurableStyleAnalysisRun(
        "style", "rep_" + uuid4().hex, "ws_test", gateway, storage, root=tmp_path / "n"
    )
    run.context = {"user_facts": "Supported facts"}
    image = run.root / "inputs/product-00.img"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), "blue").save(image, "PNG")
    write_json(run.root / "context.json", run.context)
    return run, gateway, storage, calls, image


def test_style_returned_contract_is_reused_after_complete_temp_loss(tmp_path):
    run, gateway, storage, calls, image = style_run(tmp_path)
    first = asyncio.run(
        run.ask(
            "style-reference-analysis",
            "Synthetic instruction",
            {"source": "fixed"},
            paths=[image],
        )
    )
    snapshot = asyncio.run(run.snapshot())
    assert len(calls) == 1
    rmtree(run.root)
    resumed = DurableStyleAnalysisRun(
        "style", run.document_id, "ws_test", gateway, storage, root=run.root
    )
    asyncio.run(resumed.restore(snapshot))
    assert (
        asyncio.run(
            resumed.ask(
                "style-reference-analysis",
                "Synthetic instruction",
                {"source": "fixed"},
                paths=[image],
            )
        )
        == first
    )
    assert len(calls) == 1
    assert len(resumed.usage) == 1
    assert (
        asyncio.run(
            resumed.ask(
                "style-reference-analysis",
                "Synthetic instruction",
                {"source": "changed"},
                paths=[image],
            )
        )
        == first
    )
    assert len(calls) == 2
    gateway.planner_model = "different-authorized-model"
    asyncio.run(
        resumed.ask(
            "style-reference-analysis",
            "Synthetic instruction",
            {"source": "changed"},
            paths=[image],
        )
    )
    assert len(calls) == 3


def test_style_cached_response_and_policy_are_hash_bound(tmp_path):
    run, gateway, storage, calls, image = style_run(tmp_path)
    asyncio.run(run.ask("style-facts", "Synthetic instruction", {}, paths=[image]))
    snapshot = asyncio.run(run.snapshot())
    changed = deepcopy(snapshot)
    changed["analysis_policy"]["sha256"] = "0" * 64
    other = DurableStyleAnalysisRun(
        "style", run.document_id, "ws_test", gateway, storage, root=run.root
    )
    with pytest.raises(ReplicaNativeError, match="规则已变化"):
        asyncio.run(other.restore(changed))
    next(iter(run.journal["requests"].values()))["response"]["observed"] = "tampered"
    with pytest.raises(ReplicaNativeError, match="哈希"):
        asyncio.run(run.ask("style-facts", "Synthetic instruction", {}, paths=[image]))
    assert len(calls) == 1


def test_http_failure_exposes_reference_differences_and_explicit_correction(
    client, monkeypatch, tmp_path
):
    from app.models import ReplicaDocumentRecord

    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.settings.replica_exact_compact_workspaces = ("ws_test",)
    gateway = split_gateway()
    client.app.state.replica_gateway = gateway
    original_init = CompactExactReplicaRun.__init__

    def init(self, *args, **kwargs):
        kwargs.setdefault("root", tmp_path / "n")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(CompactExactReplicaRun, "__init__", init)
    ids = []
    for size in [(64, 96), (256, 640)]:
        out = BytesIO()
        Image.new("RGB", size, "blue").save(out, "PNG")
        result = client.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": ("source.png", out.getvalue(), "image/png")},
        )
        assert result.status_code == 201
        ids.append(result.json()["id"])
    route = "/api/v1/workspaces/ws_test/replica"
    result = client.post(
        route + "/sessions",
        json={
            "request_id": str(uuid4()),
            "replication_method": "exact",
            "product_asset_ids": ids[:1],
            "references": [{"asset_id": ids[1]}],
            "facts": "Supported synthetic product",
        },
    )
    assert result.status_code == 202
    doc_id = result.json()["id"]
    doc = client.get(route + "/documents/" + doc_id).json()
    assert doc["status"] == "failed", doc
    assert doc["payload"]["segmentation_repair_available"] is True
    assert doc["payload"]["analysis_resume_available"] is False
    assert (
        doc["payload"]["segmentation_diagnostics"]["columns"][0]["reference_index"] == 0
    )
    with client.app.state.session_factory() as session:
        original_snapshot = deepcopy(
            session.get(ReplicaDocumentRecord, doc_id).payload["_analysis_checkpoint"]
        )
    gateway.correct_repair = True
    response = client.post(route + "/sessions/" + doc_id + "/retry")
    assert response.status_code == 202, response.text
    doc = client.get(route + "/documents/" + doc_id).json()
    assert doc["status"] == "awaiting_confirmation", doc
    assert len(doc["payload"]["plans"]) == 5
    assert "_segmentation_revision" not in doc["payload"]
    assert (
        original_snapshot["files"]["screen-inventory.json"]["text"].count('"C002"') > 0
    )


def planning_style_run(tmp_path):
    from tests.test_replica_style_delivery import fixture

    native, _, _, _ = fixture(tmp_path)
    run = DurableStyleAnalysisRun(
        "style",
        native.document_id,
        "ws_test",
        native.gateway,
        native.storage,
        root=native.root,
    )
    run.run, run.context = (
        native.run,
        {**native.context, "references": [{"asset_id": "reference"}]},
    )
    run.journal["cursor"] = {
        "phase": "planning",
        "revision": 0,
        "stop_after": 3,
        "planning_phase": "plan",
        "prior_audit": None,
        "observation": {},
        "units": [],
    }
    return run


def test_style_planning_transport_resume_does_not_repeat_completed_proposal(tmp_path):
    from app.replica_native import ReplicaAnalysisUnavailable

    run = planning_style_run(tmp_path)
    calls = []

    async def interrupted(stage, *args, **kwargs):
        calls.append(stage)
        if "audit" in stage:
            raise ReplicaAnalysisUnavailable()
        return {"units": ["actual proposal"]}

    run.contract = interrupted
    with pytest.raises(ReplicaAnalysisUnavailable):
        asyncio.run(run.finish_style_planning())
    assert run.journal["cursor"]["planning_phase"] == "audit"
    snapshot = asyncio.run(run.snapshot())
    rmtree(run.root)
    resumed = DurableStyleAnalysisRun(
        "style", run.document_id, "ws_test", run.gateway, run.storage, root=run.root
    )
    asyncio.run(resumed.restore(snapshot))

    async def succeed(stage, *args, **kwargs):
        calls.append(stage)
        return {"pass": True}

    resumed.contract = succeed
    assert len(asyncio.run(resumed.finish_style_planning())) == 3
    assert calls == ["style-plan-r0", "style-plan-audit-r0", "style-plan-audit-r0"]
    assert asyncio.run(resumed.finish_style_planning())
    assert len(calls) == 3


def test_style_failed_plan_can_be_explicitly_revised_without_changing_old_failures(
    tmp_path,
):
    from app.replica import _analysis_input_hash
    from app.replica_analysis_recovery import style_revision_available
    from app.replica_native import ReplicaPlanAuditRejected, digest

    run = planning_style_run(tmp_path)
    calls = []

    async def reject(stage, *args, **kwargs):
        calls.append(stage)
        if "audit" in stage:
            raise ReplicaPlanAuditRejected(
                {"pass": False, "evidence": ["Actual synthetic plan mismatch"]}
            )
        write_json(run.root / f"proposal-{stage}.json", {"revision": stage})
        return {"units": ["changed proposal"]}

    run.contract = reject
    with pytest.raises(ReplicaPlanAuditRejected):
        asyncio.run(run.finish_style_planning())
    assert len(calls) == 6
    assert run.journal["cursor"]["phase"] == "rejected"
    payload = {
        "replication_method": "style",
        "product_asset_ids": ["p"],
        "references": [{"asset_id": "r"}],
        "facts": "Supported facts",
        "language": "zh-CN",
        "platform_code": "tmall",
    }
    records = {}
    (run.root / "inputs").mkdir(exist_ok=True)
    for role, aid, source in [
        ("product", "p", "product.png"),
        ("reference", "r", "reference.png"),
    ]:
        raw = (run.root / source).read_bytes()
        (run.root / f"inputs/{role}-00.img").write_bytes(raw)
        records[aid] = (source, "image/png", digest(raw), "unused", 1, 1)
    run.context["references"] = payload["references"]
    awaitable = run.checkpoint()
    asyncio.run(awaitable)
    snapshot = asyncio.run(run.snapshot())
    values = {
        **payload,
        "_analysis_checkpoint": snapshot,
        "_analysis_input_hash": _analysis_input_hash(payload),
        "error_code": "REPLICA_PLAN_AUDIT_REJECTED",
        "job_ids": [],
    }
    row = SimpleNamespace(status="failed", payload=values)
    assert style_revision_available(row, _analysis_input_hash(values))
    previous = deepcopy(snapshot)
    resumed = DurableStyleAnalysisRun(
        "style", run.document_id, "ws_test", run.gateway, run.storage, root=run.root
    )
    asyncio.run(resumed.restore(snapshot))
    resumed.authorized_revision = "explicit-revision"

    async def corrected(stage, *args, **kwargs):
        calls.append(stage)
        return {"pass": True} if "audit" in stage else {"units": ["corrected proposal"]}

    resumed.contract = corrected
    assert len(asyncio.run(resumed.prepare(payload, records))) == 3
    assert calls[-2:] == ["style-plan-r3", "style-plan-audit-r3"]
    assert snapshot == previous
    assert resumed.journal["cursor"]["prior_audit"]["pass"] is False
    # Duplicate delivery of the same explicit request cannot start another round.
    assert asyncio.run(resumed.prepare(payload, records))
    assert len(calls) == 8


def test_legacy_coverage_failure_without_inventory_file_still_has_safe_correction(
    tmp_path,
):
    from app.replica import _analysis_input_hash
    from app.replica_analysis_recovery import segmentation_repair_available

    gateway = SyntheticGateway()
    original = gateway.plan

    async def missing(request):
        result = await original(request)
        if json.loads(request.product_information[0])["stage"] == "screen_inventory":
            result.payload["all_columns_covered"] = False
        return result

    gateway.plan = missing
    base, _, payload, records = setup_run(tmp_path / "n", gateway)
    legacy_type = run_class("exact", "exact-compact-v5")
    legacy = legacy_type(
        "exact", base.document_id, "ws_test", gateway, base.storage, root=base.root
    )
    with pytest.raises(ReplicaSegmentationInvalid):
        asyncio.run(legacy.prepare(payload, records))
    snapshot = asyncio.run(legacy.snapshot())
    assert "screen-inventory.json" not in snapshot["files"]
    values = {
        **payload,
        "replication_method": "exact",
        "_exact_model_protocol": "exact-compact-v5",
        "_analysis_checkpoint": snapshot,
        "error_code": "REPLICA_SEGMENTATION_INVALID",
        "job_ids": [],
        "plans": [],
    }
    values["_analysis_input_hash"] = _analysis_input_hash(values)
    diagnostic = segmentation_diagnostics(values)
    assert diagnostic["coverage_valid"] is False
    assert segmentation_repair_available(
        SimpleNamespace(status="failed", payload=values), _analysis_input_hash(values)
    )
    assert (
        prepare_segmentation_retry(values)["_segmentation_revision"]["coverage_retry"]
        is True
    )


def test_style_http_revision_is_idempotent_and_keeps_original_checkpoint(
    client, monkeypatch, tmp_path
):
    from app import replica
    from app.models import ReplicaDocumentRecord

    run = planning_style_run(tmp_path)
    run.journal["cursor"]["phase"] = "rejected"
    run.journal["cursor"]["revision"] = 3
    run.journal["cursor"]["prior_audit"] = {
        "pass": False,
        "evidence": ["Actual mismatch"],
    }
    asyncio.run(run.checkpoint())
    checkpoint = asyncio.run(run.snapshot())
    payload = {
        "replication_method": "style",
        "product_asset_ids": ["original-product"],
        "references": [{"asset_id": "original-reference"}],
        "facts": "Supported facts",
        "language": "zh-CN",
        "platform_code": "tmall",
        "_analysis_checkpoint": checkpoint,
        "error_code": "REPLICA_PLAN_AUDIT_REJECTED",
        "job_ids": [],
        "plans": [],
    }
    payload["_analysis_input_hash"] = replica._analysis_input_hash(payload)
    with client.app.state.session_factory() as session:
        session.add(
            ReplicaDocumentRecord(
                id=run.document_id,
                workspace_id="ws_test",
                created_by="usr_test",
                kind="session",
                fingerprint=uuid4().hex * 2,
                status="failed",
                payload=payload,
            )
        )
        session.commit()
    calls = []

    async def record(app, document_id, attempt_id):
        calls.append((document_id, attempt_id))

    monkeypatch.setattr(replica, "_analyse", record)
    route = f"/api/v1/workspaces/ws_test/replica/sessions/{run.document_id}/revise"
    first = client.post(route)
    assert first.status_code == 202, first.text
    assert "_style_revision_requested" not in first.json()["payload"]
    assert client.post(route).status_code == 202
    assert len(calls) == 1
    with client.app.state.session_factory() as session:
        assert (
            session.get(ReplicaDocumentRecord, run.document_id).payload[
                "_analysis_checkpoint"
            ]
            == checkpoint
        )
