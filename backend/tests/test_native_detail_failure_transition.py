"""Only complete negative pixel observations open the native two-attempt cycle."""

from __future__ import annotations

import hashlib
import json

import pytest
from PIL import Image

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)
from app.domestic_detail.atomic_review import synthetic_module_judgments
from app.domestic_detail.plans import canonical_hash
from tests.domestic_detail_atomic_fixtures import minimal_atomic_plan, review_context


def record_fixture(root, current, candidate, token):
    request = {
        "token": token,
        "pair_id": "P01",
        "module_ids": ["M01", "M02"],
        "output_surface": {
            "width_px": current["plan"]["output_strategy"]["canvas_width_px"],
            "height_px": 2 * current["plan"]["output_strategy"]["module_height_px"],
        },
    }
    request["request_fingerprint"] = canonical_hash(request)
    request_path = root / (token + ".json")
    request_path.write_text(json.dumps(request), encoding="utf-8")
    current.setdefault("generation_requests", []).append(
        {
            "token": token,
            "request": str(request_path),
            "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "request_fingerprint": request["request_fingerprint"],
        }
    )
    current["state"] = "AWAITING_GENERATED_MODULE"
    current["active_token"] = token
    session = root / "session.json"
    session.write_text(json.dumps(current), encoding="utf-8")
    AuroraDetailHarnessRunner().run(
        "record-generation",
        root,
        {"session": session, "token": token, "image": candidate},
    )
    return json.loads(session.read_text(encoding="utf-8"))


@pytest.fixture
def pair_audit(tmp_path):
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (3, 8), "#222222").save(candidate)
    pair = {"pair_id": "P01", "module_ids": ["M01", "M02"]}
    modules = [
        {
            "id": module_id,
            "order": index,
            "pair_id": "P01",
            "status": "AWAITING_AUDIT",
            "cycle_attempt_count": 0,
            "plan": minimal_atomic_plan(module_id, index, method),
            "attempts": [],
        }
        for index, (module_id, method) in enumerate(
            (("M01", "EDGE_BLEED"), ("M02", "GRAPHIC_OVERLAY")), start=1
        )
    ]
    current = {
        "version": "4.3.1",
        "state": "AWAITING_GENERATED_MODULE",
        "active_token": "fixture-token-1",
        "session_dir": str(tmp_path),
        "current_index": 0,
        "plan": {
            "information_contract_version": "4.1",
            "output_strategy": {
                "target_platform": "isolated fixture",
                "module_count": 2,
                "canvas_width_px": 3,
                "module_height_px": 4,
                "verification_status": "BACKEND_CONFIRMATION_REQUIRED",
                "continuous_pair_rendering": {
                    "enabled": True,
                    "pair_merge_type": "PAIR_PLAN_MERGE",
                    "pair_ids": [pair],
                    "pair_canvas_height_px": 8,
                },
            },
        },
        "modules": modules,
    }
    current = record_fixture(tmp_path, current, candidate, "fixture-token-1")
    checks = (
        "identity",
        "facts",
        "pair_duties",
        "visual_proof",
        "legibility",
        "copy_binding",
        "internal_seam",
        "spatial_integrity",
        "commercial_expression",
    )
    judgment = {
        **pair,
        "status": "MATERIAL_FAIL",
        "audit_complete": True,
        "artifact_binding": {
            "image_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "auditor": "synthetic-test-only",
            "audited_at": "2026-10-17T12:00:00+00:00",
            "ocr_text": [],
            "visual_inspection_output": {"fixture": "synthetic"},
        },
        "pair_canvas": {
            "aspect_ratio": "3:8",
            "width_px": 3,
            "height_px": 8,
            "decomposition": "TWO_FIXED_SINGLE_SCREEN_WINDOWS",
        },
        "checks": {
            name: {
                "pass": True,
                "visible_evidence": "Synthetic fixture observation for " + name,
            }
            for name in checks
        },
        "screen_coverage": [
            {
                "module_id": module_id,
                "duty_visible": True,
                "selling_points_visible": ["Synthetic visible unit"],
                "visible_evidence": "Synthetic fixture upper/lower screen observation "
                + module_id,
            }
            for module_id in pair["module_ids"]
        ],
        "internal_seam_evidence": {
            "method_id": "GRAPHIC_OVERLAY",
            "source_region": "upper bottom edge",
            "target_region": "lower top edge",
            "carrier": "amber light trail",
            "geometry": "light trail follows the same vertical direction",
            "hard_cut_absent": True,
            "method_specific_pass": True,
            "visible_evidence": (
                "Synthetic fixture light trail crosses the fixed boundary."
            ),
        },
        "material_failures": [
            {
                "check": "legibility",
                "visible_evidence": (
                    "Synthetic fixture: glyph strokes merge at the lower right."
                ),
            }
        ],
        "identity_silhouette": {
            "comparison_scope": "VISIBLE_COMPLETE_PRODUCT",
            "visible_features": ["Synthetic fixture visible original product feature"],
            "shape_consistent": True,
            "colorway_consistent": True,
            "texture_consistent": True,
            "visible_evidence": (
                "Synthetic fixture silhouette/color/texture compared with source."
            ),
        },
        "information_audit": {
            "upper_buyer_question": "first question",
            "lower_buyer_question": "second question",
            "new_information_visible": True,
            "new_information_evidence": (
                "Synthetic fixture different evidence units in each window."
            ),
            "repetition_without_progression": False,
            "hero_subject_clear": True,
            "co_equal_hero_detected": False,
            "purchase_judgment_enabled": True,
            "detail_page_not_posterized": True,
        },
    }
    judgment["module_judgments"] = synthetic_module_judgments(
        review_context(tmp_path),
        judgment["artifact_binding"]["image_sha256"],
        "2026-10-17T14:00:00+00:00",
    )
    return tmp_path, current, judgment


def audit(fixture):
    root, current, judgment = fixture
    session_path = root / "session.json"
    judgment_path = root / "judgment.json"
    session_path.write_text(json.dumps(current), encoding="utf-8")
    judgment_path.write_text(json.dumps(judgment), encoding="utf-8")
    before_judgment = judgment_path.read_bytes()
    summary = AuroraDetailHarnessRunner().run(
        "audit-pair", root, {"session": session_path, "judgment": judgment_path}
    )
    assert judgment_path.read_bytes() == before_judgment
    return summary, json.loads(session_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "finding", ["seam", "shape", "colorway", "texture", "duty", "points"]
)
@pytest.mark.parametrize("attempt", [1, 2])
def test_native_negative_findings_use_bounded_canonical_transition(
    pair_audit, finding, attempt
):
    root, current, judgment = pair_audit
    if attempt == 2:
        current = record_fixture(
            root, current, root / "candidate.png", "fixture-token-2"
        )
        pair_audit = (root, current, judgment)
    if finding == "seam":
        judgment["internal_seam_evidence"]["hard_cut_absent"] = False
        judgment["internal_seam_evidence"]["method_specific_pass"] = False
    elif finding in {"shape", "colorway", "texture"}:
        judgment["identity_silhouette"][finding + "_consistent"] = False
    elif finding == "duty":
        judgment["screen_coverage"][1]["duty_visible"] = False
    else:
        judgment["screen_coverage"][1]["selling_points_visible"] = []
    summary, saved = audit(pair_audit)
    assert summary["state"] == (
        "REPAIR_REQUIRED" if attempt == 1 else "AWAITING_USER_FINAL_REVIEW"
    )
    assert [module["status"] for module in saved["modules"]] == [
        "REPAIR_REQUIRED" if attempt == 1 else "COMPLETE_WITH_RESIDUALS"
    ] * 2
    for module in saved["modules"]:
        persisted = module["attempts"][-1]["judgment"]
        assert persisted["internal_seam_evidence"] == judgment["internal_seam_evidence"]
        assert persisted["identity_silhouette"] == judgment["identity_silhouette"]
        assert persisted["screen_coverage"] == judgment["screen_coverage"]
        assert len(persisted["material_failures"]) == 2
        if attempt == 2:
            assert len(module["residual_issues"]) == 2
    assert saved["events"][-1]["event"] == "pair_audited:P01:MATERIAL_FAIL"
    assert "bridge" not in saved["events"][-1]


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_check",
        "identity_unknown",
        "stale_pixels",
        "wrong_method",
        "missing_failure",
        "third_attempt",
        "different_attempts",
        "boolean_attempt",
        "pass_with_failure",
    ],
)
def test_malformed_or_contradictory_audit_never_mutates_native_session(
    pair_audit, invalid
):
    root, current, judgment = pair_audit
    if invalid == "missing_check":
        del judgment["checks"]["commercial_expression"]
    elif invalid == "identity_unknown":
        judgment["identity_silhouette"]["shape_consistent"] = None
    elif invalid == "stale_pixels":
        judgment["artifact_binding"]["image_sha256"] = "0" * 64
    elif invalid == "wrong_method":
        judgment["internal_seam_evidence"]["method_id"] = "EDGE_BLEED"
    elif invalid == "missing_failure":
        judgment["material_failures"] = []
    elif invalid == "third_attempt":
        for module in current["modules"]:
            module["cycle_attempt_count"] = 3
    elif invalid == "different_attempts":
        current["modules"][1]["cycle_attempt_count"] = 2
    elif invalid == "boolean_attempt":
        for module in current["modules"]:
            module["cycle_attempt_count"] = True
    else:
        judgment["status"] = "PASS"
    session_path = root / "session.json"
    session_path.write_text(json.dumps(current), encoding="utf-8")
    before = session_path.read_bytes()
    with pytest.raises(AuroraDetailHarnessError):
        audit(pair_audit)
    assert session_path.read_bytes() == before
