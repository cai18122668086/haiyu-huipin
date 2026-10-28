"""Atomic reports cover both actual slices; summaries cannot bypass them."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessError
from app.domestic_detail.atomic_review import atomic_check_names
from tests.test_native_detail_failure_transition import audit, record_fixture
from tests.test_native_detail_failure_transition import pair_audit as pair_audit


def reject_unchanged(fixture):
    root, current, _ = fixture
    path = root / "session.json"
    path.write_text(json.dumps(current), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(AuroraDetailHarnessError):
        audit(fixture)
    assert path.read_bytes() == before


def test_pair_summary_without_atomic_reports_cannot_open_a_paid_repair(pair_audit):
    del pair_audit[2]["module_judgments"]
    reject_unchanged(pair_audit)


@pytest.mark.parametrize("check", atomic_check_names())
def test_every_one_of_the_44_atomic_checks_is_required(pair_audit, check):
    assert len(atomic_check_names()) == 44
    del pair_audit[2]["module_judgments"][1]["checks"][check]
    reject_unchanged(pair_audit)


@pytest.mark.parametrize(
    "field",
    [
        "effective_visual_payloads",
        "content_units_observed",
        "structure_family_observed",
        "structural_zone_count",
        "observed_expression_signature",
        "independent_brand_logo_count",
        "headline_visible",
        "headline_rank",
        "visual_carrier_observed",
        "typography_primary_mode_observed",
        "ocr_text",
        "planned_expression_signature_match",
    ],
)
def test_missing_atomic_observation_is_not_a_known_image_failure(pair_audit, field):
    del pair_audit[2]["module_judgments"][0]["observations"][field]
    reject_unchanged(pair_audit)


@pytest.mark.parametrize(
    "bad",
    [
        "order",
        "duplicate",
        "contract",
        "slice_hash",
        "canvas_hash",
        "window",
        "unknown_bool",
        "bool_as_number",
        "unverifiable_brand",
        "unknown_headline",
        "contradictory_signature",
        "pass_with_failed_check",
    ],
)
def test_wrong_binding_unknown_or_contradictory_atomic_report_stops(pair_audit, bad):
    judgment = pair_audit[2]
    report = judgment["module_judgments"][0]
    observation = report["observations"]
    if bad == "order":
        judgment["module_judgments"].reverse()
    elif bad == "duplicate":
        judgment["module_judgments"][1] = deepcopy(report)
    elif bad == "contract":
        report["approved_contract_sha256"] = "0" * 64
    elif bad == "slice_hash":
        report["artifact_binding"]["image_sha256"] = "0" * 64
    elif bad == "canvas_hash":
        report["slice_binding"]["canvas_sha256"] = "0" * 64
    elif bad == "window":
        report["slice_binding"]["crop_box"][1] = 1
    elif bad == "unknown_bool":
        observation["planned_expression_signature_match"] = None
    elif bad == "bool_as_number":
        observation["structural_zone_count"] = True
    elif bad == "unverifiable_brand":
        observation["independent_brand_logo_source_fidelity"] = "UNVERIFIABLE"
    elif bad == "unknown_headline":
        observation.update(headline_visible=True, headline_rank=None)
    elif bad == "contradictory_signature":
        observation["observed_expression_signature"] = "different signature"
    else:
        report["checks"]["facts"]["pass"] = False
    reject_unchanged(pair_audit)


def visible_failure(report, name):
    report["status"] = "MATERIAL_FAIL"
    report["material_failures"] = [
        {
            "check": {
                "geometry": "spatial_structure_match",
                "brand": "independent_brand_logo",
                "typography": "typography_contract_match",
                "content": "content_unit_visibility",
                "payload": "visual_payload_sufficiency",
                "identity": "identity",
                "expression": "semantic_visual_expression",
                "depth": "depth_performance",
            }[name],
            "visible_evidence": "Synthetic observed lower screen problem: " + name,
            "root_cause": "Synthetic known pixel mismatch",
            "required_change": "Synthetic restore approved visual duty",
        }
    ]
    observed = report["observations"]
    if name == "geometry":
        observed["structural_zone_count"] = 0
    elif name == "brand":
        observed["independent_brand_logo_count"] = 1
    elif name == "typography":
        observed["typography_anchor_zone_observed"] = "wrong-observed-zone"
    elif name == "content":
        observed["content_units_observed"] = []
    elif name == "payload":
        observed["observed_visual_payload_nodes"] = []
    elif name == "identity":
        report["identity_silhouette"]["shape_consistent"] = False
    elif name == "expression":
        observed["static_packshot_only"] = True
    elif name == "depth":
        observed["depth_layers"] = 0
    else:
        raise AssertionError(name)


@pytest.mark.parametrize(
    "name",
    [
        "geometry",
        "brand",
        "typography",
        "content",
        "payload",
        "identity",
        "expression",
        "depth",
    ],
)
@pytest.mark.parametrize("attempt", [1, 2])
def test_known_atomic_failures_use_pair_budget_and_preserve_exact_raw_observations(
    pair_audit, name, attempt
):
    root, current, judgment = pair_audit
    if attempt == 2:
        current = record_fixture(
            root, current, root / "candidate.png", "second-atomic-token"
        )
    judgment["material_failures"] = []
    visible_failure(judgment["module_judgments"][1], name)
    original = deepcopy(judgment["module_judgments"])
    summary, saved = audit((root, current, judgment))
    assert summary["state"] == (
        "REPAIR_REQUIRED" if attempt == 1 else "AWAITING_USER_FINAL_REVIEW"
    )
    for module in saved["modules"]:
        accepted = module["attempts"][-1]["judgment"]
        assert accepted["module_judgments"] == original
        assert any(
            row.get("module_id") == "M02" for row in accepted["material_failures"]
        )
        assert module["status"] == (
            "REPAIR_REQUIRED" if attempt == 1 else "COMPLETE_WITH_RESIDUALS"
        )
        if attempt == 2:
            assert module["residual_issues"]


@pytest.mark.parametrize(
    "name",
    [
        "geometry",
        "brand",
        "typography",
        "content",
        "payload",
        "identity",
        "expression",
        "depth",
    ],
)
def test_atomic_pass_cannot_hide_measured_gate_failure(pair_audit, name):
    report = pair_audit[2]["module_judgments"][1]
    visible_failure(report, name)
    report.update(status="PASS", material_failures=[])
    reject_unchanged(pair_audit)


def test_material_failure_text_cannot_contradict_all_positive_atomic_observations(
    pair_audit,
):
    report = pair_audit[2]["module_judgments"][0]
    report.update(
        status="MATERIAL_FAIL",
        material_failures=[
            {
                "check": "legibility",
                "visible_evidence": (
                    "Synthetic alleged glyph issue without negative evidence"
                ),
                "root_cause": "Synthetic alleged cause",
                "required_change": "Synthetic alleged change",
            }
        ],
    )
    reject_unchanged(pair_audit)


def test_empty_material_failure_evidence_never_authorizes_an_image_repair(pair_audit):
    report = pair_audit[2]["module_judgments"][0]
    visible_failure(report, "geometry")
    report["material_failures"][0]["visible_evidence"] = ""
    reject_unchanged(pair_audit)


def test_missing_expression_choices_and_signature_are_not_fabricated():
    from app.xinghe_agent import _complete_aurora_module_structure

    value = _complete_aurora_module_structure(
        {"id": "M01", "lead_conclusion": "Synthetic actual product benefit"}
    )
    assert value["expression_signature"] == ""
    assert all(
        value["expression"][key] == ""
        for key in (
            "expression_mode",
            "claim_visualizability",
            "primary_visual_carrier",
        )
    )


def test_unbound_expression_signature_is_rejected_before_approval_without_mutation(
    client, tmp_path
):
    from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
    from app.domestic_detail.native_approval import native_approval_directory
    from tests.test_domestic_detail_plans import planned_job

    job = planned_job(client)
    source = native_approval_directory(
        detail_workspace(job["id"]), job["plan"]["payload"]["native_approval"]
    )
    plan = json.loads((source / "normalized_plan.json").read_text(encoding="utf-8"))
    plan["modules"][0]["expression_signature"] = (
        "plausible but unbound display signature"
    )
    root = tmp_path / "preflight"
    root.mkdir()
    session_path, plan_path = root / "session.json", root / "plan.json"
    for name in ("input.json", "identity.json"):
        (root / name).write_bytes((source / name).read_bytes())
    runner = AuroraDetailHarnessRunner()
    runner.run("start", root, {"session": session_path, "input": root / "input.json"})
    runner.run(
        "lock-identity",
        root,
        {"session": session_path, "identity": root / "identity.json"},
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    before = session_path.read_bytes(), plan_path.read_bytes()
    with pytest.raises(AuroraDetailHarnessError, match="expression_signature"):
        AuroraDetailHarnessRunner().run(
            "preflight-plan", root, {"session": session_path, "plan": plan_path}
        )
    assert (session_path.read_bytes(), plan_path.read_bytes()) == before
    assert not (root / "campaign-approval.md").exists()
