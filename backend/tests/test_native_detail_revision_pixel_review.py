"""Synthetic before/after audit protocol; no supplier or visual-quality acceptance."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)
from app.domestic_detail.plans import canonical_hash
from tests.domestic_detail_atomic_fixtures import review_context
from tests.test_native_detail_failure_transition import audit, record_fixture
from tests.test_native_detail_local_adjustment import adjustment, inventory

pytest_plugins = ("tests.test_native_detail_failure_transition",)


def project(root, command, **args):
    return AuroraDetailHarnessRunner().run(
        command, root, {"session": root / "session.json", **args}
    )


@pytest.fixture
def revision(pair_audit, request):
    original_root, original, judgment = pair_audit
    judgment["status"] = "PASS"
    judgment["material_failures"] = []
    _, original = audit((original_root, original, judgment))
    original_bytes = (original_root / "session.json").read_bytes()
    before = inventory(original_root)
    root = original_root.parent / (original_root.name + "_revision")
    root.mkdir()
    (root / "source-session.json").write_bytes(original_bytes)
    source_sha = hashlib.sha256(original_bytes).hexdigest()
    intent = {
        "version": "domestic-detail-local-revision-v1",
        "job_id": root.name,
        "source_job_id": original_root.name,
        "source_session_sha256": source_sha,
        "workspace_id": "synthetic-workspace",
        "actor_id": "synthetic-actor",
        "approval_text": "Synthetic explicit independent local cycle approval",
        "adjustment": adjustment(getattr(request, "param", "M01")),
    }
    intent["adjustment"]["change_only"].append("保留本屏副标题相对顺序")
    intent["sha256"] = canonical_hash(intent)
    (root / "local-revision-intent.json").write_text(
        json.dumps(intent), encoding="utf-8"
    )
    current = deepcopy(original)
    current.update(session_dir=str(root), current_index=0, active_token=None)
    current["revision_fork"] = {
        "version": "owned-local-revision-fork-v1",
        "maximum_new_image_requests": 2,
        "delivery_mode": "USER_FINAL_REVIEW",
        "intent_sha256": intent["sha256"],
        "source_session": str(root / "source-session.json"),
        "source_session_sha256": source_sha,
        "source_job_id": original_root.name,
        "job_id": root.name,
        "pair_id": "P01",
        "module_ids": ["M01", "M02"],
        "adjustment": intent["adjustment"],
    }
    for module in current["modules"]:
        module.update(
            cycle_id=module.get("cycle_id", 1) + 1,
            cycle_attempt_count=0,
            accepted_asset=None,
        )
    candidate = root / "candidate.png"
    Image.new("RGB", (3, 8), "#777777").save(candidate)
    current = record_fixture(root, current, candidate, "synthetic-local-first")
    judgment["artifact_binding"]["image_sha256"] = hashlib.sha256(
        candidate.read_bytes()
    ).hexdigest()
    context = review_context(root)
    from app.domestic_detail.atomic_review import synthetic_module_judgments

    judgment["module_judgments"] = synthetic_module_judgments(
        context,
        judgment["artifact_binding"]["image_sha256"],
        "2026-10-18T01:00:00+00:00",
    )
    contract = project(root, "inspect-pair-review-contracts", **{"pair-id": "P01"})[
        "local_revision_contract"
    ]
    return root, current, judgment, contract, original_root, before


def observation(contract, lineage):
    selected = contract["selected_module_id"]
    before = {row["module_id"]: row for row in contract["baseline"]["screens"]}
    after = {row["module_id"]: row for row in lineage["slices"]}
    sibling = next(key for key in contract["module_ids"] if key != selected)
    evidence = {
        "pass": True,
        "before_observation": "SYNTHETIC fixture original window observation",
        "after_observation": "SYNTHETIC fixture candidate window observation",
        "visible_evidence": "SYNTHETIC fixture comparison; not actual quality proof",
    }
    return {
        "version": "native-local-revision-pixel-v1",
        "contract_sha256": contract["sha256"],
        "intent_sha256": contract["intent_sha256"],
        "source_session_sha256": contract["source_session_sha256"],
        "pair_id": contract["pair_id"],
        "module_ids": contract["module_ids"],
        "selected_module_id": selected,
        "source_canvas_sha256": contract["baseline"]["canvas"]["sha256"],
        "candidate_canvas_sha256": lineage["canvas"]["sha256"],
        "target_before_slice_sha256": before[selected]["sha256"],
        "target_candidate_slice_sha256": after[selected]["sha256"],
        **{
            key: [
                {"index": index, "original_requirement": text, **evidence}
                for index, text in enumerate(contract["adjustment"][key])
            ]
            for key in ("change_only", "preserve", "must_not_regress")
        },
        "sibling": {
            "module_id": sibling,
            "before_slice_sha256": before[sibling]["sha256"],
            "candidate_slice_sha256": after[sibling]["sha256"],
            **evidence,
        },
    }


def attach(revision):
    root, current, judgment, contract, _, _ = revision
    lineage = project(root, "inspect-pair-artifacts", **{"pair-id": "P01"})
    judgment["local_revision_observation"] = observation(contract, lineage)
    return root, current, judgment


@pytest.mark.parametrize("revision", ["M01", "M02"], indirect=True)
def test_native_revision_pass_stores_exact_baseline_and_raw_observation(revision):
    root, current, judgment = attach(revision)
    original_root, before = revision[4:]
    expected = deepcopy(judgment["local_revision_observation"])
    # Whole-pair regeneration is a visual preservation contract, not pixel locking.
    assert (
        expected["sibling"]["before_slice_sha256"]
        != expected["sibling"]["candidate_slice_sha256"]
    )
    result, current = audit((root, current, judgment))
    assert result["state"] == "AWAITING_USER_FINAL_REVIEW"
    proof = project(root, "inspect-pair-review-evidence", **{"pair-id": "P01"})
    assert proof["audit_input"]["local_revision_observation"] == expected
    snapshot = json.loads(
        Path(proof["snapshot_reference"]["snapshot_file"]).read_bytes()
    )
    assert snapshot["revision_contract"] == revision[3]
    # A later unrelated fork must not reinterpret an earlier pair's authority.
    current["revision_fork"] = {"pair_id": "P99", "intent_sha256": "later-fork"}
    (root / "session.json").write_text(json.dumps(current), encoding="utf-8")
    assert project(root, "inspect-pair-review-evidence", **{"pair-id": "P01"}) == proof
    assert inventory(original_root) == before


@pytest.mark.parametrize(
    "group", ["change_only", "preserve", "must_not_regress", "sibling"]
)
@pytest.mark.parametrize("attempt", [1, 2])
def test_explicit_local_failure_uses_existing_two_attempt_endpoint(
    revision, group, attempt
):
    root, current, judgment = attach(revision)
    if attempt == 2:
        current = record_fixture(
            root, current, root / "candidate.png", "synthetic-local-second"
        )
    judgment["status"] = "MATERIAL_FAIL"
    row = judgment["local_revision_observation"][group]
    (row if group == "sibling" else row[0])["pass"] = False
    result, saved = audit((root, current, judgment))
    assert result["state"] == (
        "REPAIR_REQUIRED" if attempt == 1 else "AWAITING_USER_FINAL_REVIEW"
    )
    for module in saved["modules"]:
        stored = module["attempts"][-1]["judgment"]
        assert (
            stored["local_revision_observation"]
            == judgment["local_revision_observation"]
        )
        assert len(stored["material_failures"]) == 1
        assert stored["material_failures"][0]["check"].startswith("local_revision_")
        assert module["status"] == (
            "REPAIR_REQUIRED" if attempt == 1 else "COMPLETE_WITH_RESIDUALS"
        )
    assert inventory(revision[4]) == revision[5]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "null",
        "intent",
        "contract",
        "source_session",
        "source_canvas",
        "candidate_canvas",
        "target_before",
        "target_candidate",
        "sibling_before",
        "sibling_candidate",
        "sibling_id",
        "module_order",
        "selected",
        "missing_requirement",
        "reordered_requirement",
        "rewritten_requirement",
        "unknown_pass",
        "null_pass",
        "unknown_pixels",
        "blank_pixels",
        "extra",
        "contradictory_pass",
    ],
)
def test_unknown_or_misbound_revision_cannot_open_repair_or_mutate_source(
    revision, mutation
):
    root, current, judgment = attach(revision)
    row = judgment["local_revision_observation"]
    if mutation == "missing":
        judgment.pop("local_revision_observation")
    elif mutation == "null":
        judgment["local_revision_observation"] = None
    elif mutation in {
        "intent",
        "contract",
        "source_session",
        "source_canvas",
        "candidate_canvas",
    }:
        row[mutation + "_sha256"] = "0" * 64
    elif mutation in {"target_before", "target_candidate"}:
        row[mutation + "_slice_sha256"] = "0" * 64
    elif mutation in {"sibling_before", "sibling_candidate"}:
        row["sibling"][mutation.split("_")[1] + "_slice_sha256"] = "0" * 64
    elif mutation == "sibling_id":
        row["sibling"]["module_id"] = "M01"
    elif mutation == "module_order":
        row["module_ids"] = list(reversed(row["module_ids"]))
    elif mutation == "selected":
        row["selected_module_id"] = "M02"
    elif mutation == "missing_requirement":
        row["change_only"].pop()
    elif mutation == "reordered_requirement":
        row["change_only"].reverse()
    elif mutation == "rewritten_requirement":
        row["change_only"][0]["original_requirement"] = "Looser invented requirement"
    elif mutation == "unknown_pass":
        row["change_only"][0]["pass"] = "unknown"
    elif mutation == "null_pass":
        row["preserve"][0]["pass"] = None
    elif mutation == "unknown_pixels":
        row["sibling"]["visible_evidence"] = "unknown: cannot observe original pixels"
    elif mutation == "blank_pixels":
        row["preserve"][0]["before_observation"] = " " * 20
    elif mutation == "extra":
        row["unchecked_revision"] = True
    else:
        row["change_only"][0]["pass"] = False
    # Protocol failure is never converted to a known material failure/retry.
    before = (root / "session.json").read_bytes()
    path = root / "judgment.json"
    path.write_text(json.dumps(judgment), encoding="utf-8")
    with pytest.raises(AuroraDetailHarnessError):
        project(root, "audit-pair", judgment=path)
    assert (root / "session.json").read_bytes() == before
    assert inventory(revision[4]) == revision[5]


@pytest.mark.parametrize(
    "file", ["source-session.json", "local-revision-intent.json", "baseline"]
)
def test_native_projection_rejects_changed_originals(revision, file):
    root = revision[0]
    if file == "baseline":
        path = Path(revision[3]["baseline"]["canvas"]["path"])
    else:
        path = root / file
    if file == "local-revision-intent.json":
        intent = json.loads(path.read_bytes())
        intent["approval_text"] = "Altered human authority"
        path.write_text(json.dumps(intent), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(AuroraDetailHarnessError):
        project(root, "inspect-pair-review-contracts", **{"pair-id": "P01"})


def test_ordinary_pair_cannot_claim_local_revision_authority(pair_audit):
    root, current, judgment = pair_audit
    assert "local_revision_contract" not in project(
        root, "inspect-pair-review-contracts", **{"pair-id": "P01"}
    )
    judgment["local_revision_observation"] = {"invented": True}
    with pytest.raises(AuroraDetailHarnessError):
        audit((root, current, judgment))


@pytest.mark.parametrize(
    "mismatch", [None, "canvas", "slice", "order", "selected", "lineage", "dimensions"]
)
def test_quoted_revision_binds_original_actual_canvas_and_each_slice(
    revision, mismatch
):
    root, current, _, contract, _, _ = revision
    source = json.loads((root / "source-session.json").read_bytes())
    original = project(
        Path(source["session_dir"]),
        "inspect-pair-artifacts",
        **{"pair-id": "P01", "selection": "ACCEPTED"},
    )
    quote = {
        "pair_id": "P01",
        "module_ids": ["M01", "M02"],
        "selected_module_id": "M01",
        "source_screens": [
            {"module_id": row["module_id"], "sha256": row["sha256"]}
            for row in original["slices"]
        ],
        "source_pair_canvas": {
            "sha256": original["canvas"]["sha256"],
            "width": 3,
            "height": 8,
            "pixel_lineage_sha256": original["sha256"],
        },
    }
    if mismatch == "canvas":
        quote["source_pair_canvas"]["sha256"] = "0" * 64
    elif mismatch == "slice":
        quote["source_screens"][0]["sha256"] = "0" * 64
    elif mismatch == "order":
        quote["source_screens"].reverse()
    elif mismatch == "selected":
        quote["selected_module_id"] = "M02"
    elif mismatch == "lineage":
        quote["source_pair_canvas"]["pixel_lineage_sha256"] = "0" * 64
    elif mismatch == "dimensions":
        quote["source_pair_canvas"]["height"] = 16
    intent_path = root / "local-revision-intent.json"
    intent = json.loads(intent_path.read_bytes())
    intent["authority"] = {"quote": quote}
    intent["sha256"] = canonical_hash(
        {k: v for k, v in intent.items() if k != "sha256"}
    )
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    current["revision_fork"]["intent_sha256"] = intent["sha256"]
    (root / "session.json").write_text(json.dumps(current), encoding="utf-8")
    if mismatch:
        with pytest.raises(AuroraDetailHarnessError):
            project(root, "inspect-pair-review-contracts", **{"pair-id": "P01"})
    else:
        actual = project(root, "inspect-pair-review-contracts", **{"pair-id": "P01"})[
            "local_revision_contract"
        ]
        assert actual["baseline"] == contract["baseline"]
        assert actual["intent_sha256"] == intent["sha256"]


@pytest.mark.parametrize("file", ["source-session.json", "local-revision-intent.json"])
def test_accepted_revision_snapshot_verifies_its_own_original_authority(revision, file):
    root, current, judgment = attach(revision)
    audit((root, current, judgment))
    path = root / file
    if file == "source-session.json":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        intent = json.loads(path.read_bytes())
        intent["adjustment"]["change_only"][0] = "Another unapproved change"
        path.write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(AuroraDetailHarnessError):
        project(root, "inspect-pair-review-evidence", **{"pair-id": "P01"})
