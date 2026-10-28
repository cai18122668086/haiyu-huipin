"""Crop deltas retain observations and never inherit an omitted pixel review."""

import asyncio
from copy import deepcopy
from shutil import rmtree
from uuid import uuid4

import pytest
from PIL import Image

from app import replica
from app.models import ReplicaDocumentRecord
from app.replica_exact_contracts import canonical_hash
from app.replica_native import NativeReplicaRun, ReplicaNativeError, write_json
from app.replica_style_analysis import (
    JOURNAL,
    LEGACY_REVIEW_POLICY,
    StyleEvidenceInvalid,
    StyleReplicaRun,
    crop_policy_upgrade,
    policy,
)
from app.replica_style_evidence import FLAGS, FROZEN_ANALYSIS, crop_patch
from tests.test_replica_style_analysis import corrected, crop_response, setup_run


def complete_analysis(tmp_path):
    run, analysis, refs, payload, records = setup_run(tmp_path)
    analysis = corrected(analysis)
    run._apply_specs(analysis)
    analysis.update(
        narrative_logic={"order": ["T01", "T02"]},
        source_semantics={"kind": "synthetic"},
        benchmark_sequence=["B01", "B02"],
    )
    return run, analysis, refs, payload, records


def test_crop_only_final_review_preserves_all_frozen_observations(
    tmp_path, monkeypatch
):
    run, analysis, _, _, _ = complete_analysis(tmp_path)
    frozen = {key: deepcopy(analysis[key]) for key in FROZEN_ANALYSIS}

    async def ask(self, stage, instruction, context, **kwargs):
        assert set(context["current_unit_ids"]) == {"C01", "C02"}
        return crop_response(context["analysis"])

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    reviewed = asyncio.run(run._review_crops(analysis))
    assert {key: reviewed[key] for key in FROZEN_ANALYSIS} == frozen
    assert len(run.journal["requests"]) == 1


def test_specs_only_proposal_materializes_before_explicit_complete_review(
    tmp_path, monkeypatch
):
    run, analysis, _, _, _ = setup_run(tmp_path)
    calls = []

    async def ask(self, stage, instruction, context, **kwargs):
        calls.append(stage)
        if len(calls) == 1:
            proposal = crop_response(corrected(context["analysis"]))
            proposal["units"] = proposal["units"][1:]
            for key in (*FLAGS, "review_status"):
                proposal["units"][0].pop(key, None)
            return proposal
        assert "fresh visual review" in context["validation_errors"][0]
        with Image.open(context["analysis"]["units"][1]["source_crop_path"]) as image:
            assert image.getpixel((0, 0)) == (0, 0, 255)
        response = crop_response(context["analysis"])
        for row in response["units"]:
            row.update(review_status="visual_model", **dict.fromkeys(FLAGS, False))
        return response

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    result = asyncio.run(run._review_crops(analysis))
    assert calls == ["style-crop-review-0", "style-crop-review-1"]
    assert len(result["units"]) == 2


@pytest.mark.parametrize("defect", ["subset", "missing_flag", "unsafe"])
def test_partial_or_unsafe_review_never_inherits_old_safe_flags(
    tmp_path, monkeypatch, defect
):
    run, analysis, _, _, _ = complete_analysis(tmp_path)

    async def ask(self, stage, instruction, context, **kwargs):
        response = crop_response(context["analysis"])
        if defect == "subset":
            response["units"] = response["units"][:1]
        elif defect == "missing_flag":
            response["units"][0].pop(FLAGS[0], None)
        else:
            response["units"][0][FLAGS[0]] = True
        return response

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    with pytest.raises(StyleEvidenceInvalid):
        asyncio.run(run._review_crops(analysis))
    assert all(
        not row.get("invalid_contract") for row in run.journal["requests"].values()
    )  # A genuine unsafe verdict is retained as a verdict.


@pytest.mark.parametrize(
    "defect", ["frozen", "duplicate", "new_without_bindings", "new_without_spec"]
)
def test_crop_patch_rejects_source_edits_and_unbound_new_units(tmp_path, defect):
    _, analysis, _, _, _ = setup_run(tmp_path)
    response = crop_response(analysis)
    if defect == "frozen":
        response["source_content_analysis"] = analysis["source_content_analysis"]
    elif defect == "duplicate":
        response["units"] *= 2
    else:
        response["units"][0]["id"] = "NEW"
        if defect == "new_without_bindings":
            response["units"][0].pop("source_content_step_ids")
            response["crop_specs"] = [
                {"id": "NEW", "reference_input_id": "R01", "bbox": [0, 0, 8, 8]}
            ]
    original = deepcopy(analysis)
    with pytest.raises(StyleEvidenceInvalid):
        crop_patch(analysis, response)
    assert analysis == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_input_id", []),
        ("source_content_step_ids", [{}]),
        ("source_content_step_ids", "T01"),
        ("source_role", {}),
        ("source_semantics", []),
        ("source_semantics", "invalid scalar"),
    ],
)
def test_malformed_crop_bindings_are_repairable_contract_errors(tmp_path, field, value):
    _, analysis, _, _, _ = setup_run(tmp_path)
    response = crop_response(analysis)
    response["units"][0][field] = value
    with pytest.raises(StyleEvidenceInvalid):
        crop_patch(analysis, response)


def test_rejected_crop_format_is_hash_preserved_and_not_reused_as_valid(
    tmp_path, monkeypatch
):
    run, analysis, _, _, _ = setup_run(tmp_path)

    async def ask(self, stage, instruction, context, **kwargs):
        return deepcopy(context["analysis"])

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    with pytest.raises(StyleEvidenceInvalid):
        asyncio.run(run._review_crops(analysis))
    assert len(run.journal["requests"]) == run.contract_attempts
    for record in run.journal["requests"].values():
        assert record["invalid_contract"] is True
        assert record["sha256"] == canonical_hash(record["response"])


def legacy_review_checkpoint(tmp_path):
    run, analysis, _, payload, records = complete_analysis(tmp_path)
    response = crop_response(analysis)
    run.journal["requests"]["legacy-review"] = {
        "stage": "style-crop-review-0",
        "response": response,
        "sha256": canonical_hash(response),
    }
    run.journal["cursor"].update(
        phase="review",
        observation={},
        units=[],
        analysis=analysis,
        evidence={"source_analysis": analysis, "candidate": analysis},
    )
    run.usage = [{"stage": "style-reference-analysis", "cost_micros": 7}]
    run.cost = 7
    asyncio.run(run.checkpoint())
    snapshot = asyncio.run(run.snapshot())
    snapshot["analysis_policy"] = deepcopy(LEGACY_REVIEW_POLICY)
    payload.update(
        replication_method="style",
        _analysis_checkpoint=snapshot,
        error_code=StyleEvidenceInvalid.code,
        job_ids=[],
        analysis_attempt_id="previous",
    )
    payload["_analysis_input_hash"] = replica._analysis_input_hash(payload)
    return run, analysis, payload, records


def test_authorized_legacy_upgrade_keeps_old_bytes_usage_and_resumes_only_review(
    tmp_path, monkeypatch
):
    run, analysis, payload, records = legacy_review_checkpoint(tmp_path)
    original = deepcopy(payload["_analysis_checkpoint"])
    authority = crop_policy_upgrade(payload, replica._analysis_input_hash(payload))
    assert authority["parent_snapshot_sha256"] == canonical_hash(original)
    rmtree(run.root)
    restored = StyleReplicaRun(
        "style", run.document_id, "ws_test", run.gateway, run.storage, root=run.root
    )
    with pytest.raises(ReplicaNativeError, match="规则已变化"):
        asyncio.run(restored.restore(original))
    restored.crop_policy_adoption = authority
    asyncio.run(restored.restore(original))
    assert restored.usage == original["usage"] and restored.cost == 7
    old = restored.journal["requests"]["legacy-review"]
    assert old["response"] == crop_response(analysis)
    assert old["sha256"] == canonical_hash(old["response"])
    assert old["invalid_contract"] is True
    assert restored.journal["policy_adoptions"] == [authority]
    assert asyncio.run(restored.snapshot())["analysis_policy"] == policy()
    assert payload["_analysis_checkpoint"] == original

    async def forbidden(*args, **kwargs):
        raise AssertionError("Completed intake must not run again")

    async def review(self, current):
        assert current == analysis
        raise StyleEvidenceInvalid("Restored crop review reached")

    monkeypatch.setattr(NativeReplicaRun, "prepare", forbidden)
    monkeypatch.setattr(StyleReplicaRun, "_review_crops", review)
    with pytest.raises(StyleEvidenceInvalid, match="Restored crop review reached"):
        asyncio.run(restored.prepare(payload, records))


@pytest.mark.parametrize(
    "defect",
    [None, "policy", "source", "facts", "job", "plan", "phase", "pass", "corrupt"],
)
def test_normal_retry_upgrades_only_known_failed_pre_generation_crop_checkpoint(
    client, monkeypatch, tmp_path, defect
):
    run, _, payload, _ = legacy_review_checkpoint(tmp_path)
    snapshot = payload["_analysis_checkpoint"]
    if defect == "policy":
        snapshot["analysis_policy"]["sha256"] = "0" * 64
    elif defect == "source":
        snapshot["source_hash"] = "0" * 64
    elif defect == "facts":
        payload["facts"] += " changed"
    elif defect == "job":
        payload["job_ids"] = ["started"]
    elif defect == "plan":
        payload["plans"] = [{"id": "approved"}]
    elif defect in {"phase", "pass"}:
        if defect == "phase":
            run.journal["cursor"]["phase"] = "ready"
            write_json(run.root / JOURNAL, run.journal)
        else:
            workflow = run.state()
            workflow["plan_audit"] = {"pass": True}
            write_json(run.run / "artifacts/workflow.json", workflow)
        snapshot = asyncio.run(run.snapshot())
        snapshot["analysis_policy"] = deepcopy(LEGACY_REVIEW_POLICY)
        payload["_analysis_checkpoint"] = snapshot
    elif defect == "corrupt":
        snapshot["files"][JOURNAL]["sha256"] = "0" * 64
    original = deepcopy(payload)
    calls = []

    async def analyse(app, document_id, attempt_id):
        calls.append((document_id, attempt_id))

    monkeypatch.setattr(replica, "_analyse", analyse)
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
    route = "/api/v1/workspaces/ws_test/replica/sessions/" + run.document_id + "/retry"
    result = client.post(route)
    if defect:
        assert result.status_code == 409 and not calls
    else:
        assert result.status_code == 202
        assert "_style_crop_policy_adoption" not in result.json()["payload"]
        again = client.post(route)
        assert again.status_code == 202 and len(calls) == 1
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, run.document_id).payload
        if defect:
            assert saved == original
        else:
            assert saved["_analysis_checkpoint"] == original["_analysis_checkpoint"]
            assert saved["_analysis_lineage"] == [original["_analysis_checkpoint"]]
            assert saved["_style_crop_policy_adoption"][
                "parent_snapshot_sha256"
            ] == canonical_hash(original["_analysis_checkpoint"])
