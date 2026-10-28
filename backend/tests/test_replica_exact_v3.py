"""Synthetic protocol/recovery tests; real visual verdicts remain model evidence."""

import asyncio
import json
from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.replica_exact_adaptation import blank_parallel_branch
from app.replica_exact_compact import (
    CompactExactReplicaRun,
    CompactResponseInvalid,
    run_class,
)
from app.replica_exact_contracts import canonical_hash
from app.replica_exact_revision import V2_BINDING, inherited_review, review_scope
from app.replica_native import (
    ReplicaAnalysisUnavailable,
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
)
from tests.test_replica_compact import SyntheticGateway, setup_run


def legacy_failure(tmp_path):
    prepared, gateway, payload, records = setup_run(tmp_path, SyntheticGateway())
    legacy_type = run_class("exact", V2_BINDING)
    old = legacy_type(
        "exact",
        prepared.document_id,
        prepared.workspace_id,
        gateway,
        prepared.storage,
        root=tmp_path,
    )
    assert old.binding == V2_BINDING
    gateway.reject_always = True
    with pytest.raises(ReplicaPlanAuditRejected):
        asyncio.run(old.prepare(payload, records))
    return old, gateway, payload, records


def revised_run(old, gateway, tmp_path):
    run = CompactExactReplicaRun(
        "exact", old.document_id, old.workspace_id, gateway, old.storage, root=tmp_path
    )
    asyncio.run(run.restore_for_revision(asyncio.run(old.snapshot())))
    return run


def test_explicit_revision_keeps_actual_pass_and_only_repairs_latest_failure(tmp_path):
    old, gateway, payload, records = legacy_failure(tmp_path)
    original = deepcopy(old.state()["plan"])
    ledger = deepcopy(old.context["resolved_facts"])
    calls = len(gateway.requests)
    run = revised_run(old, gateway, tmp_path)
    assert len(gateway.requests) == calls  # No API calls to import real evidence.
    gateway.reject_always = False
    asyncio.run(run.prepare(payload, records))
    new = gateway.requests[calls:]
    assert [json.loads(r.product_information[0])["stage"] for r in new] == [
        "plan",
        "review_plan",
    ]
    assert all("R002" in r.group_key for r in new)
    assert run.state()["state"] == "READY"
    assert run.state()["plan"]["modules"][0] == original["modules"][0]
    assert (
        run.state()["plan"]["_compact_scopes"]["modules"][0]
        == original["_compact_scopes"]["modules"][0]
    )
    assert run.context["resolved_facts"] == ledger
    assert run.checkpoints["inherited_reviews_used"] == ["R001"]
    receipt = run.checkpoints["inherited_reviews"]["R001"]
    assert receipt["parent_protocol"] == V2_BINDING
    assert receipt["response_sha256"] == canonical_hash(receipt["raw"])
    assert (
        len(
            [
                r
                for r in run.checkpoints["results"].values()
                if r["phase"] == "review_plan"
            ]
        )
        == 1
    )


def test_inherited_verdict_cannot_be_changed_even_with_a_new_response_hash(tmp_path):
    old, gateway, _, _ = legacy_failure(tmp_path)
    run = revised_run(old, gateway, tmp_path)
    receipt = run.checkpoints["inherited_reviews"]["R001"]
    receipt["raw"]["unit_audits"][0]["fresh_pixel_observation"][
        "subject_and_scale"
    ] += " altered"
    receipt["response_sha256"] = canonical_hash(receipt["raw"])
    with pytest.raises(ReplicaNativeError, match="原始审查"):
        inherited_review(
            run,
            review_scope(
                run.checkpoints["plan_cursor"]["plan"],
                "R001",
                run.context["product_evidence"],
            ),
        )


def test_changed_legacy_subject_or_model_requires_a_real_new_review(tmp_path):
    old, gateway, _, _ = legacy_failure(tmp_path)
    run = revised_run(old, gateway, tmp_path)
    scope = review_scope(
        run.checkpoints["plan_cursor"]["plan"], "R001", run.context["product_evidence"]
    )
    assert inherited_review(run, scope) is not None
    scope["modules"][0]["generation_brief"] += " Move the scene subject."
    assert inherited_review(run, scope) is None
    scope = review_scope(
        run.checkpoints["plan_cursor"]["plan"], "R001", run.context["product_evidence"]
    )
    gateway.planner_model = "different-synthetic-model"
    assert inherited_review(run, scope) is None


def test_revision_rejects_a_different_protocol_without_silent_migration(tmp_path):
    old, gateway, _, _ = legacy_failure(tmp_path)
    snapshot = asyncio.run(old.snapshot())
    snapshot["model_protocol"]["content_sha256"] = "0" * 64
    run = CompactExactReplicaRun(
        "exact", old.document_id, old.workspace_id, gateway, old.storage, root=tmp_path
    )
    before = len(gateway.requests)
    with pytest.raises(ReplicaNativeError, match="原协议"):
        asyncio.run(run.restore_for_revision(snapshot))
    assert len(gateway.requests) == before


@pytest.mark.parametrize("interrupt_at", ["plan", "review_plan"])
def test_interruption_resumes_last_round_without_old_round_replay(
    tmp_path, interrupt_at
):
    gateway = SyntheticGateway(reject_first=True)
    original = gateway.plan
    interrupted = False

    async def interrupt(request):
        nonlocal interrupted
        envelope = json.loads(request.product_information[0])
        if (
            envelope["stage"] == interrupt_at
            and "-r1-" in request.group_key
            and not interrupted
        ):
            interrupted = True
            raise ReplicaAnalysisUnavailable()
        return await original(request)

    gateway.plan = interrupt
    run, _, payload, records = setup_run(tmp_path, gateway)
    with pytest.raises(ReplicaAnalysisUnavailable):
        asyncio.run(run.prepare(payload, records))
    assert run.checkpoints["reviews"] == {"R001": "PASS", "R002": "FAIL"}
    before = len(gateway.requests)
    snapshot = asyncio.run(run.snapshot())
    restored = CompactExactReplicaRun(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=tmp_path
    )
    asyncio.run(restored.restore(snapshot))
    progress = []

    async def checkpoint():
        progress.append(deepcopy(restored.context["analysis_progress"]))

    restored.on_checkpoint = checkpoint
    asyncio.run(restored.prepare(payload, records))
    added = gateway.requests[before:]
    assert len(added) == (2 if interrupt_at == "plan" else 1)
    assert all("-r1-" in r.group_key and "R002" in r.group_key for r in added)
    assert restored.state()["state"] == "READY"
    assert min(p["accepted"] for p in progress) >= 1


def test_render_anchor_substitution_never_mutates_source_observation(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    asyncio.run(run.prepare(payload, records))
    layout = run.context["reference_layouts"]["R001"]
    assert layout["layout_anchors"][0]["label"] != "Actual blue synthetic product"
    scope = run.state()["plan"]["_compact_scopes"]["modules"][0]
    assert scope["layout_anchors"][0]["label"] == "Actual blue synthetic product"
    model = run.model_module_evidence(run.context["module_constraints"]["M001"])
    assert "source_layout_anchors" not in model
    for request in gateway.requests:
        envelope = json.loads(request.product_information[0])
        if envelope["stage"] == "review_plan":
            target = envelope["context"]["review_targets"][0]
            assert (
                target["proposed_screen"]["commercial_adaptation"]["mode"] == "preserve"
            )
            assert (
                target["identity_scope"]["layout_anchors"][0]["label"]
                == "Actual blue synthetic product"
            )


def test_empty_parallel_column_is_blocked_but_isolated_unsupported_badges_can_omit():
    regions = [
        {
            "id": "left-label",
            "role": "badge",
            "font_tier": "secondary",
            "bbox": [100, 300, 400, 370],
        },
        {
            "id": "right-label",
            "role": "badge",
            "font_tier": "secondary",
            "bbox": [600, 300, 900, 370],
        },
        {
            "id": "left-body",
            "role": "body",
            "font_tier": "small",
            "bbox": [100, 700, 400, 850],
        },
        {
            "id": "right-body",
            "role": "body",
            "font_tier": "small",
            "bbox": [600, 700, 900, 850],
        },
    ]
    proposal = {
        "text_bindings": [
            {
                "region_id": r["id"],
                "action": "omit_unsupported_copy"
                if r["id"].startswith("right")
                else "render",
            }
            for r in regions
        ]
    }
    assert blank_parallel_branch(proposal, {"text_regions": regions})
    proposal["text_bindings"][3]["action"] = "render"
    assert not blank_parallel_branch(proposal, {"text_regions": regions})


def test_manual_revision_restores_durable_v2_evidence_and_submits_only_once(
    client, tmp_path, monkeypatch
):
    from app import replica_exact_compact
    from app.models import ReplicaDocumentRecord
    from app.task_runner import MockTaskRunner
    from tests.test_api import dispatch_pending

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
    legacy = run_class("exact", V2_BINDING)
    legacy_init = legacy.__init__

    def isolated_init(self, *args, **kwargs):
        kwargs.setdefault("root", tmp_path.parent / "http-revision")
        legacy_init(self, *args, **kwargs)

    monkeypatch.setattr(legacy, "__init__", isolated_init)
    ids = []
    for i, size in enumerate([(64, 96), (256, 640)]):
        buffer = BytesIO()
        Image.new("RGB", size, "blue" if i == 0 else "yellow").save(buffer, "PNG")
        upload = client.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": (f"synthetic-{i}.png", buffer.getvalue(), "image/png")},
        )
        assert upload.status_code == 201
        ids.append(upload.json()["id"])
    route = "/api/v1/workspaces/ws_test/replica"
    monkeypatch.setattr(replica_exact_compact, "VERSION", "exact-compact-v2")
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
    failed = client.get(route + "/documents/" + document_id).json()
    assert (
        failed["status"] == "failed"
        and failed["payload"]["analysis_revision_available"]
    )
    assert failed["payload"]["analysis_progress"]["accepted"] == 1
    with client.app.state.session_factory() as session:
        old = deepcopy(
            session.get(ReplicaDocumentRecord, document_id).payload[
                "_analysis_checkpoint"
            ]
        )
    # Simulate the previous API container having gone away: the service must
    # restore from the saved checkpoint/object store, not existing temp files.
    from pathlib import Path
    from shutil import rmtree

    root = Path(old["root"]).resolve()
    assert root.is_relative_to(tmp_path.parent.resolve())
    rmtree(root)
    before = len(gateway.requests)
    monkeypatch.setattr(replica_exact_compact, "VERSION", "exact-compact-v5")
    gateway.reject_always = False
    revised = client.post(route + "/sessions/" + document_id + "/revise", json={})
    assert revised.status_code == 202, revised.text
    current = client.get(route + "/documents/" + document_id).json()
    assert current["status"] == "awaiting_confirmation", current
    confirmed = client.post(
        route + "/sessions/" + document_id + "/confirm",
        json={"plan_hash": current["payload"]["plan_hash"], "instructions": []},
    )
    assert confirmed.status_code == 202, confirmed.text
    current = client.get(route + "/documents/" + document_id).json()
    assert current["status"] == "generating", current
    assert len(gateway.requests) == before + 2
    assert len(current["payload"]["job_ids"]) == 1
    assert (
        client.post(route + "/sessions/" + document_id + "/revise", json={}).status_code
        == 409
    )
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, document_id).payload
        assert (
            saved["native_snapshot"]["model_protocol"]["version"] == "exact-compact-v5"
        )
        assert (
            json.loads(saved["native_snapshot"]["files"]["context.json"]["text"])[
                "revision_lineage"
            ]["ledger_version"]
            == json.loads(old["files"]["context.json"]["text"])["resolved_facts"][
                "version"
            ]
        )
    dispatch_pending(client)
    job = client.get(
        f"/api/v1/workspaces/ws_test/jobs/{current['payload']['job_ids'][0]}"
    ).json()
    assert job["status"] == "completed" and job["succeeded_item_count"] == 2
    assert len(gateway.image_requests) == 2


@pytest.mark.parametrize(
    "violation", ["empty_critical", "unsupported_substitution", "lost_anchor"]
)
def test_invalid_adaptation_stops_before_any_plan_verdict(tmp_path, violation):
    gateway = SyntheticGateway()
    original = gateway.plan

    async def invalid(request):
        response = await original(request)
        envelope = json.loads(request.product_information[0])
        if envelope["stage"] != "plan":
            return response
        proposal = response.payload["modules"][0]
        if violation == "empty_critical":
            proposal["copy_units"] = []
            proposal["text_bindings"] = [
                {
                    "region_id": "headline",
                    "action": "omit_unsupported_copy",
                    "reason": "Missing facts must not blank the whole branch",
                    "retain_carrier": True,
                }
            ]
            proposal["fact_adaptations"][0].update(mode="omit_unsupported", fact_ids=[])
        elif violation == "unsupported_substitution":
            proposal["commercial_adaptation"].update(
                mode="truthful_substitution", fact_ids=["F999"]
            )
        else:
            proposal["render_anchors"][0]["source_anchor_index"] = 7
        return response

    gateway.plan = invalid
    run, _, payload, records = setup_run(tmp_path, gateway)
    with pytest.raises(CompactResponseInvalid):
        asyncio.run(run.prepare(payload, records))
    assert not any("exact-plan-audit" in r.group_key for r in gateway.requests)
    assert not gateway.image_requests
