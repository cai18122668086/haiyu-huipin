"""Synthetic evidence gaps, real recrops and durable stage recovery."""

import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from app.replica_native import NativeReplicaRun, ReplicaNativeError, digest, write_json
from app.replica_style_analysis import (
    CURSOR,
    FLAGS,
    PROVENANCE,
    StyleEvidenceInvalid,
    StyleReplicaRun,
    crop_errors,
    revision_checkpoint,
)
from app.storage import ImageValidator, LocalImageStorage


def setup_run(tmp_path):
    root = tmp_path / "run"
    run = StyleReplicaRun(
        "style",
        "rep_" + uuid4().hex,
        "ws_test",
        SimpleNamespace(
            provider_name="synthetic", planner_model="synthetic", pricing_version="test"
        ),
        LocalImageStorage(tmp_path / "objects", ImageValidator(1_000_000, 1_000_000)),
        root=root,
    )
    reference = root / "inputs/reference-00.img"
    reference.parent.mkdir()
    image = Image.new("RGB", (64, 128), "red")
    ImageDraw.Draw(image).rectangle((0, 64, 63, 127), fill="blue")
    image.save(reference, "PNG")
    product = root / "inputs/product-00.img"
    Image.new("RGB", (32, 32), "yellow").save(product, "PNG")
    refs = {
        "R01": {
            "id": "R01",
            "role": "benchmark_reference",
            "path": str(reference),
            "sha256": digest(reference.read_bytes()),
        }
    }
    run.run = root / "native"
    write_json(
        run.run / "artifacts/workflow.json",
        {
            "state": "ROUTE_CLASSIFIED",
            "inputs": list(refs.values()),
        },
    )
    run.context = {
        "user_facts": "Synthetic confirmed geometry",
        "references": [{"asset_id": "ref"}],
        "language": "zh-CN",
        "platform": "tmall",
    }
    write_json(root / "context.json", run.context)
    crop = run.crop(reference, [0, 0, 16, 16], root / "crops/style-C01.png")
    analysis = {
        "overview_detection": {
            "panels": [
                {
                    "panel_id": "B01",
                    "reference_input_id": "R01",
                    "bbox": [0, 0, 64, 64],
                },
                {
                    "panel_id": "B02",
                    "reference_input_id": "R01",
                    "bbox": [0, 64, 64, 128],
                },
            ]
        },
        "source_content_analysis": {
            "sequence": [
                {"id": "T01", "source_panel_ids": ["B01"]},
                {"id": "T02", "source_panel_ids": ["B02"]},
            ]
        },
        "units": [
            {
                "id": "C01",
                "reference_input_id": "R01",
                "source_crop_path": str(crop),
                "source_content_step_ids": ["T01"],
                "source_role": "style_mechanism_only",
                "source_semantics": ["Synthetic physical material"],
                "review_status": "visual_model",
                **dict.fromkeys(FLAGS, False),
            }
        ],
    }
    payload = {
        "product_asset_ids": ["product"],
        "references": [{"asset_id": "ref"}],
        "facts": run.context["user_facts"],
        "language": "zh-CN",
        "platform_code": "tmall",
    }
    records = {
        "product": (None, None, digest(product.read_bytes())),
        "ref": (None, None, digest(reference.read_bytes())),
    }
    return run, analysis, refs, payload, records


def corrected(analysis):
    value = deepcopy(analysis)
    value["units"].append(
        {
            **deepcopy(value["units"][0]),
            "id": "C02",
            "source_content_step_ids": ["T02"],
            "source_role": "style_mechanism_only",
            "source_semantics": ["Synthetic physical material"],
        }
    )
    value["crop_specs"] = [
        {"id": "C02", "reference_input_id": "R01", "bbox": [0, 64, 16, 80]}
    ]
    return value


def crop_response(analysis):
    return {
        key: deepcopy(analysis[key])
        for key in ("units", "crop_specs", "mechanisms")
        if key in analysis
    }


def test_missing_source_step_is_detected_before_any_page_planning(tmp_path):
    run, analysis, refs, _, _ = setup_run(tmp_path)
    provenance = json.loads((run.root / PROVENANCE).read_text())
    errors = crop_errors(analysis, provenance, refs)
    assert len(errors) == 1 and errors[0].startswith("T02:")
    # Reusing a first-panel swatch for the second panel cannot fix coverage.
    analysis["units"][0]["source_content_step_ids"].append("T02")
    assert crop_errors(analysis, provenance, refs) == errors
    analysis["units"][0]["reference_input_id"] = "R_OTHER"
    assert any(
        "registered reference" in e for e in crop_errors(analysis, provenance, refs)
    )


def test_repair_physically_crops_then_freshly_reviews_returned_pixels(
    tmp_path, monkeypatch
):
    run, analysis, refs, _, _ = setup_run(tmp_path)
    calls = []

    async def ask(self, stage, instruction, context, **kwargs):
        calls.append(stage)
        if stage == "style-crop-repair-0":
            assert any(e.startswith("T02:") for e in context["validation_errors"])
            return crop_response(corrected(context["analysis"]))
        if stage == "style-crop-review-1":
            actual = Path(context["analysis"]["units"][1]["source_crop_path"])
            with Image.open(actual) as image:
                assert image.getpixel((0, 0)) == (0, 0, 255)
            assert (
                context["analysis"]["source_content_analysis"]
                == analysis["source_content_analysis"]
            )
        return crop_response(context["analysis"])

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    result = asyncio.run(run._review_crops(analysis))
    assert calls == [
        "style-crop-review-0",
        "style-crop-repair-0",
        "style-crop-review-1",
    ]
    provenance = json.loads((run.root / PROVENANCE).read_text())
    assert not crop_errors(result, provenance, refs)
    assert len(result["units"]) == 2


@pytest.mark.parametrize(
    "defect", ["flags_only", "delete_source_step", "contamination"]
)
def test_relabeling_or_changing_source_observations_never_repairs_crops(
    tmp_path, monkeypatch, defect
):
    run, analysis, _, _, _ = setup_run(tmp_path)
    original_source = deepcopy(analysis["source_content_analysis"])

    async def ask(self, stage, instruction, context, **kwargs):
        value = deepcopy(context["analysis"])
        if defect == "flags_only":
            value["units"][0]["source_content_step_ids"] = ["T01", "T02"]
        elif defect == "delete_source_step":
            value["source_content_analysis"]["sequence"] = value[
                "source_content_analysis"
            ]["sequence"][:1]
        else:
            value["units"][0]["source_product_presence"] = True
        return value if defect == "delete_source_step" else crop_response(value)

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    with pytest.raises(StyleEvidenceInvalid):
        asyncio.run(run._review_crops(analysis))
    assert (
        run.journal["cursor"]["evidence"]["source_analysis"]["source_content_analysis"]
        == original_source
    )
    assert not (run.root / "crops/style-C02.png").exists()


def test_failed_crop_stage_restores_after_temp_removal_without_new_intake(
    tmp_path, monkeypatch
):
    run, analysis, _, payload, records = setup_run(tmp_path)
    run.journal["cursor"].update(
        phase="review",
        observation={},
        units=[],
        analysis=analysis,
        evidence={"source_analysis": analysis, "candidate": analysis},
    )
    asyncio.run(run.checkpoint())
    snapshot = asyncio.run(run.snapshot())
    original_root = run.root
    rmtree(original_root)
    restored = StyleReplicaRun(
        "style",
        run.document_id,
        "ws_test",
        run.gateway,
        run.storage,
        root=original_root,
    )
    asyncio.run(restored.restore(snapshot))
    calls = []

    async def review(self, current):
        assert current == analysis
        calls.append("crop_stage_only")
        raise StyleEvidenceInvalid("Synthetic stop after restored crop stage")

    async def forbidden(*args, **kwargs):
        raise AssertionError("Intake/physical analysis must not restart")

    monkeypatch.setattr(StyleReplicaRun, "_review_crops", review)
    monkeypatch.setattr(NativeReplicaRun, "prepare", forbidden)
    with pytest.raises(StyleEvidenceInvalid):
        asyncio.run(restored.prepare(payload, records))
    assert calls == ["crop_stage_only"]
    assert (original_root / "crops/style-C01.png").is_file()
    records["ref"] = (None, None, "0" * 64)
    with pytest.raises(ReplicaNativeError, match="哈希"):
        asyncio.run(restored.prepare(payload, records))
    assert calls == ["crop_stage_only"]


def test_explicit_plan_revision_keeps_failed_audit_and_skips_completed_stages(
    tmp_path, monkeypatch
):
    run, _, _, payload, records = setup_run(tmp_path)
    write_json(run.run / "artifacts/workflow.json", {"state": "PLAN_AUDIT_REQUIRED"})
    failed = {
        "pass": False,
        "review_kind": "visual_model",
        "evidence": ["Synthetic rejected layout"],
    }
    write_json(run.root / "contracts/prior-audit.json", failed)
    run.journal["cursor"].update(
        phase="rejected",
        revision=3,
        stop_after=3,
        planning_phase="plan",
        prior_audit=failed,
        observation={},
        units=[],
    )
    asyncio.run(run.checkpoint())
    snapshot = asyncio.run(run.snapshot())
    assert revision_checkpoint({"_analysis_checkpoint": snapshot})
    rmtree(run.root)
    restored = StyleReplicaRun(
        "style", run.document_id, "ws_test", run.gateway, run.storage, root=run.root
    )
    asyncio.run(restored.restore(snapshot))

    restored.authorized_revision = "explicit-revision"

    async def planning(self, previous_audit):
        assert previous_audit == failed
        assert self.state()["state"] == "PLAN_AUDIT_REQUIRED"
        return [{"unit_id": "synthetic"}]

    async def forbidden(*args, **kwargs):
        raise AssertionError("Completed intake/facts/crops must not repeat")

    monkeypatch.setattr(NativeReplicaRun, "prepare", forbidden)
    monkeypatch.setattr(StyleReplicaRun, "finish_style_planning", planning)
    result = asyncio.run(restored.prepare(payload, records))
    assert result == [{"unit_id": "synthetic"}]
    assert (restored.root / "contracts/prior-audit.json").read_bytes() == snapshot[
        "files"
    ]["contracts/prior-audit.json"]["text"].encode()
    bad = deepcopy(snapshot)
    bad["files"][CURSOR]["sha256"] = "0" * 64
    assert not revision_checkpoint({"_analysis_checkpoint": bad})
    assert restored.journal["cursor"]["prior_audit"] == failed
    assert restored.journal["cursor"]["applied_revision"] == "explicit-revision"


@pytest.mark.parametrize(
    "invalid", [None, "facts_changed", "checkpoint_corrupt", "has_job"]
)
def test_normal_style_revision_route_is_bound_and_idempotent(
    client, monkeypatch, tmp_path, invalid
):
    from app import replica
    from app.models import ReplicaDocumentRecord

    run, _, _, payload, _ = setup_run(tmp_path)
    run.journal["cursor"].update(
        phase="rejected",
        revision=3,
        stop_after=3,
        planning_phase="plan",
        prior_audit={"pass": False},
        observation={},
        units=[],
    )
    asyncio.run(run.checkpoint())
    snapshot = asyncio.run(run.snapshot())
    payload.update(
        replication_method="style",
        _analysis_checkpoint=snapshot,
        error_code="REPLICA_PLAN_AUDIT_REJECTED",
        job_ids=[],
        analysis_attempt_id="previous",
    )
    payload["_analysis_input_hash"] = replica._analysis_input_hash(payload)
    if invalid == "facts_changed":
        payload["facts"] += " changed"
    elif invalid == "checkpoint_corrupt":
        payload["_analysis_checkpoint"]["files"][CURSOR]["sha256"] = "0" * 64
    elif invalid == "has_job":
        payload["job_ids"] = ["already_started"]
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
    base = "/api/v1/workspaces/ws_test/replica"
    wire = client.get(base + "/documents/" + run.document_id).json()
    assert wire["payload"]["analysis_revision_available"] is (invalid is None)
    response = client.post(base + "/sessions/" + run.document_id + "/revise", json={})
    if invalid:
        assert response.status_code == 409 and not calls
        with client.app.state.session_factory() as session:
            assert (
                session.get(ReplicaDocumentRecord, run.document_id).payload == original
            )
        return
    assert response.status_code == 202, response.text
    assert "_style_analysis_revision" not in response.json()["payload"]
    assert "_exact_model_protocol" not in response.json()["payload"]
    again = client.post(base + "/sessions/" + run.document_id + "/revise", json={})
    assert again.status_code == 202 and len(calls) == 1
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, run.document_id).payload
        assert saved["_analysis_checkpoint"] == original["_analysis_checkpoint"]
        assert saved["_style_analysis_revision"][
            "parent_snapshot_sha256"
        ] == replica._hash(snapshot)
        assert "_exact_model_protocol" not in saved


def test_repaired_step_bindings_pass_the_unchanged_native_request_compiler(tmp_path):
    run, analysis, _, _, _ = setup_run(tmp_path)
    for i, step in enumerate(analysis["source_content_analysis"]["sequence"], 1):
        step.update(source_reference_ids=["R01"], source_order=i)
    script = """
import json,sys
sys.path.insert(0,sys.argv[1])
from workflow import target_reference_bindings
value=json.loads(sys.argv[2]);units=value['units']
for unit in units:
 unit.update(path=unit['source_crop_path'],sha256='a'*64)
try:
 refs=target_reference_bindings({'reference_content_analysis':value['source_content_analysis']},
       {'units':units},[{'source_content_step_ids':['T01','T02']}])
 print(json.dumps({'passed':True,'count':len(refs)}))
except ValueError:
 print(json.dumps({'passed':False}))
"""
    skill = (
        Path(__file__).parents[1]
        / "skill_sources/all-category-style-benchmark-still/scripts"
    )

    def native(value):
        proc = subprocess.run(
            [sys.executable, "-c", script, str(skill), json.dumps(value)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    assert native(analysis) == {"passed": False}
    repaired = corrected(analysis)
    run._apply_specs(repaired)
    assert native(repaired) == {"passed": True, "count": 2}


def test_new_physical_crop_bytes_never_reuse_old_review_cache(tmp_path, monkeypatch):
    run, analysis, refs, _, _ = setup_run(tmp_path)
    seen = []

    async def inspect(self, stage, instruction, context, **kwargs):
        path = Path(context["analysis"]["units"][0]["source_crop_path"])
        with Image.open(path) as image:
            seen.append(image.getpixel((0, 0)))
        return crop_response(context["analysis"])

    monkeypatch.setattr(NativeReplicaRun, "ask", inspect)
    context = {"analysis": analysis}
    asyncio.run(run._ask_model("style-crop-review-0", "Inspect pixels", context))
    asyncio.run(run._ask_model("style-crop-review-0", "Inspect pixels", context))
    assert seen == [(255, 0, 0)]
    run.crop(
        Path(refs["R01"]["path"]),
        [0, 64, 16, 80],
        Path(analysis["units"][0]["source_crop_path"]),
    )
    asyncio.run(run._ask_model("style-crop-review-0", "Inspect pixels", context))
    assert seen == [(255, 0, 0), (0, 0, 255)]


def test_twelve_steps_with_four_bindings_stop_before_page_planning(
    tmp_path, monkeypatch
):
    run, analysis, refs, _, _ = setup_run(tmp_path)
    reference = Path(refs["R01"]["path"])
    Image.new("RGB", (64, 768), "red").save(reference, "PNG")
    refs["R01"]["sha256"] = digest(reference.read_bytes())
    write_json(
        run.run / "artifacts/workflow.json",
        {"state": "ROUTE_CLASSIFIED", "inputs": list(refs.values())},
    )
    analysis["overview_detection"]["panels"] = [
        {
            "panel_id": f"B{i + 1:02}",
            "reference_input_id": "R01",
            "bbox": [0, i * 64, 64, (i + 1) * 64],
        }
        for i in range(12)
    ]
    analysis["source_content_analysis"]["sequence"] = [
        {"id": f"T{i + 1:02}", "source_panel_ids": [f"B{i + 1:02}"]} for i in range(12)
    ]
    seed = deepcopy(analysis["units"][0])
    analysis["units"] = []
    for i in range(4):
        uid = f"C{i + 1:02}"
        crop = run.crop(
            reference,
            [0, i * 64, 16, i * 64 + 16],
            run.root / "crops" / f"style-{uid}.png",
        )
        analysis["units"].append(
            {
                **seed,
                "id": uid,
                "source_crop_path": str(crop),
                "source_content_step_ids": [f"T{i + 1:02}"],
            }
        )
    provenance = json.loads((run.root / PROVENANCE).read_text())
    assert len(crop_errors(analysis, provenance, refs)) == 8
    run.journal["cursor"].update(
        phase="review", observation={}, units=[], analysis=analysis
    )
    calls = []

    async def unchanged(self, stage, instruction, context, **kwargs):
        calls.append(stage)
        assert stage.startswith(("style-crop-review", "style-crop-repair"))
        return crop_response(context["analysis"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("Page planning must not run before physical crop coverage")

    monkeypatch.setattr(NativeReplicaRun, "ask", unchanged)
    monkeypatch.setattr(StyleReplicaRun, "finish_style_planning", forbidden)
    with pytest.raises(StyleEvidenceInvalid):
        asyncio.run(run._prepare_style({}, []))
    assert len(calls) == 6
    assert (
        len(
            run.journal["cursor"]["evidence"]["source_analysis"][
                "source_content_analysis"
            ]["sequence"]
        )
        == 12
    )


def test_changed_original_reference_cannot_pass_with_old_crop_provenance(tmp_path):
    run, analysis, refs, _, _ = setup_run(tmp_path)
    path = Path(refs["R01"]["path"])
    Image.new("RGB", (64, 128), "green").save(path, "PNG")
    provenance = json.loads((run.root / PROVENANCE).read_text())
    assert any(
        "registered reference" in error
        for error in crop_errors(analysis, provenance, refs)
    )
    with pytest.raises(StyleEvidenceInvalid, match="Original reference bytes changed"):
        run._apply_specs(corrected(analysis))


def test_style_restore_rejects_cross_task_private_object_keys(tmp_path):
    run, _, _, _, _ = setup_run(tmp_path)
    asyncio.run(run.checkpoint())
    snapshot = asyncio.run(run.snapshot())
    poisoned = deepcopy(snapshot)
    image = next(row for row in poisoned["files"].values() if "object_key" in row)
    image["object_key"] = "ws_other/replica/rep_other/private.png"
    with pytest.raises(ReplicaNativeError, match="another task"):
        asyncio.run(run.restore(poisoned))


def test_invalid_native_contract_is_preserved_but_not_replayed_as_valid(
    tmp_path, monkeypatch
):
    run, _, _, _, _ = setup_run(tmp_path)
    calls = []
    corrected = False

    async def ask(self, stage, instruction, context, **kwargs):
        calls.append(stage)
        return {"facts": "corrected" if corrected else "invalid"}

    def cli(self, command, **arguments):
        if json.loads(arguments["facts"].read_text())["facts"] == "invalid":
            raise ReplicaNativeError("Missing concrete native fact evidence")
        return {"accepted": True}

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    monkeypatch.setattr(NativeReplicaRun, "cli", cli)
    with pytest.raises(ReplicaNativeError):
        asyncio.run(
            run.contract(
                "style-facts", "Return facts", {}, "register-product-facts", "facts"
            )
        )
    original = deepcopy(run.journal["requests"])
    assert len(calls) == 3
    assert all(row["invalid_contract"] for row in original.values())
    corrected = True
    run.evidence_revision = "explicit-facts-correction"
    asyncio.run(
        run.contract(
            "style-facts", "Return facts", {}, "register-product-facts", "facts"
        )
    )
    assert len(calls) == 4
    assert all(run.journal["requests"][key] == row for key, row in original.items())


def test_real_plan_fail_is_not_misclassified_as_invalid_format(tmp_path, monkeypatch):
    from app.replica_native import ReplicaPlanAuditRejected

    run, _, _, _, _ = setup_run(tmp_path)
    calls = []

    async def ask(self, stage, instruction, context, **kwargs):
        calls.append(stage)
        return {"pass": False, "evidence": ["A real independent plan failure"]}

    def cli(self, *args, **kwargs):
        raise ReplicaNativeError("Plan audit honestly failed")

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    monkeypatch.setattr(NativeReplicaRun, "cli", cli)
    for _ in range(2):
        with pytest.raises(ReplicaPlanAuditRejected):
            asyncio.run(
                run.contract(
                    "style-plan-audit-r0", "Inspect plan", {}, "audit-plan", "audit"
                )
            )
    assert len(calls) == 1
    assert all(
        not row.get("invalid_contract") for row in run.journal["requests"].values()
    )


def test_shared_crop_is_scoped_to_actual_job_and_passes_native_dedup(tmp_path):
    from app.replica_style_evidence import job_crop_manifest

    run, analysis, refs, _, _ = setup_run(tmp_path)
    analysis["source_content_analysis"]["sequence"][1]["source_panel_ids"] = ["B01"]
    for i, step in enumerate(analysis["source_content_analysis"]["sequence"], 1):
        step.update(source_reference_ids=["R01"], source_order=i)
    analysis["units"][0]["source_content_step_ids"] = ["T01", "T02"]
    for unit in analysis["units"]:
        unit.update(
            path=unit["source_crop_path"],
            sha256=digest(Path(unit["source_crop_path"]).read_bytes()),
        )
    manifest = {"units": deepcopy(analysis["units"]), "record_count": 1}
    workflow = {
        "inputs": list(refs.values()),
        "overview_detection": analysis["overview_detection"],
        "reference_content_analysis": analysis["source_content_analysis"],
        "units": {
            "U01": {"source_content_step_ids": ["T01"]},
            "U02": {"source_content_step_ids": ["T02"]},
        },
    }
    original = deepcopy(manifest)
    assert (
        job_crop_manifest(run, workflow, manifest, {"id": "S01", "unit_ids": ["U01"]})
        == manifest
    )
    assert (
        job_crop_manifest(run, workflow, manifest, {"id": "S02", "unit_ids": ["U02"]})
        == manifest
    )
    assert not (run.root / "style-binding-aliases.json").exists()
    paired = job_crop_manifest(
        run, workflow, manifest, {"id": "G01", "unit_ids": ["U01", "U02"]}
    )
    assert manifest == original
    assert len(paired["units"]) == 2
    alias = paired["units"][-1]
    assert (
        Path(alias["path"]).read_bytes()
        == Path(manifest["units"][0]["path"]).read_bytes()
    )
    assert alias["sha256"] == manifest["units"][0]["sha256"]
    assert alias["binding_alias"]["pixel_review"] == "inherited_identical_bytes"
    from app.replica_style_delivery import generation_workspace

    job = {"id": "G01", "unit_ids": ["U01", "U02"], "canvas": [768, 2048]}
    source = {
        **deepcopy(workflow),
        "route": "DETAIL_PAGE",
        "reference_crops": deepcopy(manifest),
        "layout": {"jobs": [job]},
        "groups": {"G01": {"unit_ids": job["unit_ids"], "status": "pending"}},
    }
    original_workflow = run.run / "artifacts/workflow.json"
    original_manifest = run.run / "artifacts/reference-crops/manifest.json"
    write_json(original_workflow, source)
    write_json(original_manifest, manifest)
    original_bytes = (original_workflow.read_bytes(), original_manifest.read_bytes())
    gate = generation_workspace(
        run,
        {"route": "DETAIL_PAGE", "jobs": [job], "targets": [{"status": "PENDING"}]},
        0,
    )
    generated_workflow = json.loads((gate.run / "artifacts/workflow.json").read_text())
    generated_manifest = json.loads(
        (gate.run / "artifacts/reference-crops/manifest.json").read_text()
    )
    assert generated_workflow["reference_crops"] == generated_manifest == paired
    assert (
        original_workflow.read_bytes(),
        original_manifest.read_bytes(),
    ) == original_bytes
    assert not list(gate.run.glob("artifacts/generation-request-*"))
    script = """import json,sys
sys.path.insert(0,sys.argv[1])
from workflow import target_reference_bindings
v=json.loads(sys.argv[2])
rows=target_reference_bindings(v['workflow'],v['manifest'],[{'source_content_step_ids':['T01','T02']}])
print(json.dumps(rows))
"""
    skill = (
        Path(__file__).parents[1]
        / "skill_sources/all-category-style-benchmark-still/scripts"
    )
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(skill),
            json.dumps({"workflow": workflow, "manifest": paired}),
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    assert [r["source_content_step_id"] for r in rows] == ["T01", "T02"]
    assert (
        job_crop_manifest(
            run, workflow, manifest, {"id": "G01", "unit_ids": ["U01", "U02"]}
        )
        == paired
    )


def test_shared_crop_alias_cannot_authorize_another_physical_panel(tmp_path):
    from app.replica_style_evidence import job_crop_manifest

    run, analysis, refs, _, _ = setup_run(tmp_path)
    for i, step in enumerate(analysis["source_content_analysis"]["sequence"], 1):
        step.update(source_reference_ids=["R01"], source_order=i)
    analysis["units"][0]["source_content_step_ids"] = ["T01", "T02"]
    unit = analysis["units"][0]
    unit.update(
        path=unit["source_crop_path"],
        sha256=digest(Path(unit["source_crop_path"]).read_bytes()),
    )
    manifest = {"units": [unit], "record_count": 1}
    original = deepcopy(manifest)
    workflow = {
        "inputs": list(refs.values()),
        "overview_detection": analysis["overview_detection"],
        "reference_content_analysis": analysis["source_content_analysis"],
        "units": {"U01": {"source_content_step_ids": ["T01", "T02"]}},
    }
    with pytest.raises(StyleEvidenceInvalid, match="physical evidence"):
        job_crop_manifest(run, workflow, manifest, {"id": "G01", "unit_ids": ["U01"]})
    assert manifest == original
    assert not (run.root / "style-binding-aliases.json").exists()


def test_rejected_format_correction_cannot_recurse_or_silently_resample_same_attempt(
    tmp_path, monkeypatch
):
    run, _, _, _, _ = setup_run(tmp_path)
    calls = []

    async def ask(self, stage, instruction, context, **kwargs):
        calls.append(stage)
        return {"facts": "invalid"}

    def cli(self, *args, **kwargs):
        raise ReplicaNativeError("Native factual evidence is missing")

    monkeypatch.setattr(NativeReplicaRun, "ask", ask)
    monkeypatch.setattr(NativeReplicaRun, "cli", cli)
    with pytest.raises(ReplicaNativeError):
        asyncio.run(
            run.contract(
                "style-facts", "Return facts", {}, "register-product-facts", "facts"
            )
        )
    run.evidence_revision = "explicit-attempt-2"
    with pytest.raises(ReplicaNativeError):
        asyncio.run(
            run.contract(
                "style-facts", "Return facts", {}, "register-product-facts", "facts"
            )
        )
    before = len(calls)
    with pytest.raises(ReplicaNativeError, match="本轮合同修正"):
        asyncio.run(
            run.contract(
                "style-facts", "Return facts", {}, "register-product-facts", "facts"
            )
        )
    assert len(calls) == before == 6
