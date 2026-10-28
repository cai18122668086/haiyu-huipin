"""Truthful identity scope is tied to actual visibility, not a planned pass."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.domestic_detail.atomic_review import (
    synthetic_identity,
    synthetic_module_judgments,
)
from app.domestic_detail.identity_review import (
    identity_complete,
    merge_identity_followup,
    observed_pair_scope,
)
from app.domestic_detail.review import DetailReviewError
from tests.domestic_detail_atomic_fixtures import review_context
from tests.test_domestic_detail_atomic_review import reject_unchanged
from tests.test_native_detail_failure_transition import audit, record_fixture
from tests.test_native_detail_failure_transition import pair_audit as pair_audit

SCOPE_PRESENCE = {
    "VISIBLE_COMPLETE_PRODUCT": "DOMINANT",
    "VISIBLE_IDENTITY_TOKEN": "IDENTITY_TOKEN_ONLY",
    "NO_VISIBLE_PRODUCT": "ABSENT",
}


def authored_role(plan, role):
    subject = (
        "INGREDIENT_OR_MATERIAL"
        if role == "ABSENT"
        else "PRODUCT_DETAIL"
        if role == "IDENTITY_TOKEN_ONLY"
        else "COMPLETE_PRODUCT"
    )
    form = (
        "ASYMMETRIC_EDITORIAL"
        if role in {"ABSENT", "IDENTITY_TOKEN_ONLY"}
        else "HERO_POSTER"
    )
    camera = "macro" if role in {"ABSENT", "IDENTITY_TOKEN_ONLY"} else "close"
    plan.update(
        primary_subject_class=subject,
        presentation_form=form,
        camera_scale=camera,
        complete_product_role=role,
        expression_signature="+".join((subject, form, camera, role)),
    )
    plan["module_visual_expression_contract"].update(
        primary_visual_carrier="MATERIAL_MACRO"
        if role == "ABSENT"
        else "PRODUCT_DETAIL"
        if role == "IDENTITY_TOKEN_ONLY"
        else "PRODUCT_HERO",
        product_presence_policy={
            "ABSENT": "ABSENT_PREFERRED",
            "IDENTITY_TOKEN_ONLY": "IDENTITY_TOKEN_ONLY",
            "DOMINANT": "REQUIRED_DOMINANT",
            "SUPPORTING": "REQUIRED_SUPPORTING",
        }[role],
    )


def actual_role(report, plan, role):
    observed_plan = deepcopy(plan)
    authored_role(observed_plan, role)
    observed = report["observations"]
    observed.update(
        product_presence_observed=role,
        observed_complete_product_role=role,
        observed_primary_subject_class=observed_plan["primary_subject_class"],
        observed_presentation_form=observed_plan["presentation_form"],
        observed_camera_scale=observed_plan["camera_scale"],
        observed_expression_signature=observed_plan["expression_signature"],
        planned_expression_signature_match=observed_plan["expression_signature"]
        == plan["expression_signature"],
        unplanned_full_product_seen=role in {"DOMINANT", "SUPPORTING"}
        and plan["complete_product_role"] in {"ABSENT", "IDENTITY_TOKEN_ONLY"},
        ingredient_visual_present=role == "ABSENT",
        visual_carrier_observed=observed_plan["module_visual_expression_contract"][
            "primary_visual_carrier"
        ],
        product_prominence_percent=0
        if role in {"ABSENT", "IDENTITY_TOKEN_ONLY"}
        else 40,
    )
    report["identity_silhouette"] = synthetic_identity(
        role, "SYNTHETIC observed original identity feature; no real visual acceptance"
    )
    if role != plan["complete_product_role"]:
        report.update(
            status="MATERIAL_FAIL",
            material_failures=[
                {
                    "check": "complete_product_role_fidelity",
                    "visible_evidence": "SYNTHETIC presence violates planned role",
                    "root_cause": "SYNTHETIC extra or missing product",
                    "required_change": "SYNTHETIC restore the planned role",
                }
            ],
        )


def scoped_fixture(fixture, planned=("DOMINANT", "ABSENT"), observed=None):
    root, current, judgment = fixture
    for module, role in zip(current["modules"], planned, strict=True):
        authored_role(module["plan"], role)
    (root / "session.json").write_text(json.dumps(current), encoding="utf-8")
    judgment["module_judgments"] = synthetic_module_judgments(
        review_context(root),
        judgment["artifact_binding"]["image_sha256"],
        "2026-10-17T15:00:00+00:00",
    )
    for report, module, role in zip(
        judgment["module_judgments"],
        current["modules"],
        observed or planned,
        strict=True,
    ):
        actual_role(report, module["plan"], role)
    judgment.update(
        status="MATERIAL_FAIL"
        if any(row["status"] == "MATERIAL_FAIL" for row in judgment["module_judgments"])
        else "PASS",
        material_failures=[],
    )
    scope = observed_pair_scope(judgment["module_judgments"])
    judgment["identity_silhouette"] = synthetic_identity(
        SCOPE_PRESENCE[scope], "SYNTHETIC aggregate of actual module identity features"
    )
    return root, current, judgment


@pytest.mark.parametrize(
    "roles,scope",
    [
        (("DOMINANT", "SUPPORTING"), "VISIBLE_COMPLETE_PRODUCT"),
        (("DOMINANT", "ABSENT"), "VISIBLE_COMPLETE_PRODUCT"),
        (("IDENTITY_TOKEN_ONLY", "ABSENT"), "VISIBLE_IDENTITY_TOKEN"),
        (("ABSENT", "ABSENT"), "NO_VISIBLE_PRODUCT"),
        (("IDENTITY_TOKEN_ONLY", "IDENTITY_TOKEN_ONLY"), "VISIBLE_IDENTITY_TOKEN"),
        (("ABSENT", "SUPPORTING"), "VISIBLE_COMPLETE_PRODUCT"),
    ],
)
def test_scope_follows_actual_full_token_or_absent_product_without_invented_comparisons(
    pair_audit, roles, scope
):
    fixture = scoped_fixture(pair_audit, roles)
    raw = deepcopy(fixture[2])
    result, state = audit(fixture)
    assert result["state"] == "AWAITING_USER_FINAL_REVIEW"
    assert raw["identity_silhouette"]["comparison_scope"] == scope
    for module in state["modules"]:
        assert (
            module["attempts"][-1]["judgment"]["module_judgments"]
            == raw["module_judgments"]
        )
    for role, row in zip(roles, raw["module_judgments"], strict=True):
        if role == "ABSENT":
            assert all(
                row["identity_silhouette"][key] == "NOT_APPLICABLE"
                for key in (
                    "shape_consistent",
                    "colorway_consistent",
                    "texture_consistent",
                )
            )
        elif role == "IDENTITY_TOKEN_ONLY":
            assert (
                row["identity_silhouette"]["shape_consistent"] == "NOT_APPLICABLE"
                and row["identity_silhouette"]["visible_features"]
            )


@pytest.mark.parametrize(
    "planned,actual",
    [
        (("DOMINANT", "SUPPORTING"), ("ABSENT", "ABSENT")),
        (("ABSENT", "IDENTITY_TOKEN_ONLY"), ("DOMINANT", "IDENTITY_TOKEN_ONLY")),
        (("ABSENT", "IDENTITY_TOKEN_ONLY"), ("ABSENT", "SUPPORTING")),
    ],
)
@pytest.mark.parametrize("attempt", [1, 2])
def test_known_unexpected_presence_or_absence_uses_one_pair_repair_and_keeps_na(
    pair_audit, planned, actual, attempt
):
    root, current, judgment = scoped_fixture(pair_audit, planned, actual)
    if attempt == 2:
        current = record_fixture(
            root, current, root / "candidate.png", "scope-second-token"
        )
    raw = deepcopy(judgment["module_judgments"])
    result, state = audit((root, current, judgment))
    assert result["state"] == (
        "REPAIR_REQUIRED" if attempt == 1 else "AWAITING_USER_FINAL_REVIEW"
    )
    for module in state["modules"]:
        assert module["attempts"][-1]["judgment"]["module_judgments"] == raw
        assert module["status"] == (
            "REPAIR_REQUIRED" if attempt == 1 else "COMPLETE_WITH_RESIDUALS"
        )


@pytest.mark.parametrize(
    "bad",
    [
        "none_unknown",
        "none_imputed_true",
        "none_features",
        "full_na",
        "token_features_missing",
        "wrong_module_scope",
        "aggregate_downgrade",
        "contradictory_full_seen",
    ],
)
def test_not_applicable_cannot_hide_unknown_or_visible_full_product(pair_audit, bad):
    fixture = scoped_fixture(pair_audit, ("DOMINANT", "ABSENT"))
    judgment = fixture[2]
    absent = judgment["module_judgments"][1]
    full = judgment["module_judgments"][0]
    if bad == "none_unknown":
        absent["identity_silhouette"]["shape_consistent"] = None
    elif bad == "none_imputed_true":
        absent["identity_silhouette"]["shape_consistent"] = True
    elif bad == "none_features":
        absent["identity_silhouette"]["visible_features"] = [
            "Unobservable full product cannot be claimed as visible"
        ]
    elif bad == "full_na":
        full["identity_silhouette"]["shape_consistent"] = "NOT_APPLICABLE"
    elif bad == "token_features_missing":
        fixture = scoped_fixture(pair_audit, ("IDENTITY_TOKEN_ONLY", "ABSENT"))
        fixture[2]["module_judgments"][0]["identity_silhouette"][
            "visible_features"
        ] = []
    elif bad == "wrong_module_scope":
        absent["identity_silhouette"] = synthetic_identity(
            "DOMINANT", "Wrong complete product comparison in an observed absence"
        )
    elif bad == "aggregate_downgrade":
        judgment["identity_silhouette"] = synthetic_identity(
            "ABSENT", "Wrong aggregate downgrade despite a visible full product"
        )
    else:
        absent["observations"]["unplanned_full_product_seen"] = True
    reject_unchanged(fixture)


def test_token_followup_preserves_false_and_applicability_without_inventing_passes():
    initial = synthetic_identity(
        "IDENTITY_TOKEN_ONLY", "Original observed token color differs from the product"
    )
    initial["colorway_consistent"] = False
    observed = {
        **synthetic_identity(
            "IDENTITY_TOKEN_ONLY", "Follow-up observes the same localized token feature"
        ),
        "audit_complete": True,
    }
    before = deepcopy(initial)
    result = merge_identity_followup(initial, observed, "VISIBLE_IDENTITY_TOKEN")
    assert result["colorway_consistent"] is False
    assert result["shape_consistent"] == "NOT_APPLICABLE"
    assert initial == before and identity_complete(result, "VISIBLE_IDENTITY_TOKEN")


@pytest.mark.parametrize("bad", ["uncertain", "scope_change", "applicability_change"])
def test_followup_cannot_turn_uncertainty_or_changed_applicability_into_a_pass(bad):
    initial = synthetic_identity(
        "IDENTITY_TOKEN_ONLY",
        "Original observed token identity with scoped comparisons",
    )
    observed = {
        **synthetic_identity(
            "IDENTITY_TOKEN_ONLY",
            "Follow-up direct token comparison of the same pixels",
        ),
        "audit_complete": True,
    }
    if bad == "uncertain":
        observed.update(audit_complete=False, shape_consistent=None)
    elif bad == "scope_change":
        initial = synthetic_identity(
            "DOMINANT", "Original claims a complete product rather than a token"
        )
    else:
        observed["shape_consistent"] = True
    with pytest.raises(DetailReviewError):
        merge_identity_followup(initial, observed, "VISIBLE_IDENTITY_TOKEN")
