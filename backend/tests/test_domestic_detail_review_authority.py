"""Review vocabulary follows approved dispositions, including user exclusions."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.review import (
    DetailReviewError,
    approved_review_authority,
    require_review_authority,
)


def compiled_review_session():
    return {
        "version": "4.3.1",
        "input": {
            "verified_facts": [
                {
                    "id": "F01",
                    "text": "Capacity 500 ml",
                    "source_class": "USER_FACT",
                    "evidence_ref": "brief/0",
                },
                {
                    "id": "F02",
                    "text": "Capacity 300 ml",
                    "source_class": "USER_FACT",
                    "evidence_ref": "brief/1",
                },
                {
                    "id": "F03",
                    "text": "777-hour performance claim",
                    "source_class": "DOCUMENTED_EVIDENCE",
                    "evidence_ref": "package",
                },
                {
                    "id": "F04",
                    "text": "Two original side openings",
                    "source_class": "OBSERVED_PRODUCT_EVIDENCE",
                    "evidence_ref": "I1",
                    "observed_region": "right edge",
                },
            ],
            "source_conflicts": [
                {
                    "id": "C01",
                    "status": "RESOLVED",
                    "resolution": {
                        "confirmation_text": (
                            "I choose the 500 ml SKU and reject 300 ml"
                        ),
                        "retained_fact_ids": ["F01"],
                        "rejected_fact_ids": ["F02"],
                    },
                }
            ],
        },
        "identity": {
            "canonical_name": "Approved original bottle",
            "category": "fixture",
            "reference_image_ids": ["I1"],
            "invariant_features": [
                "Flat rounded-rectangle section",
                "Width/height 1:2",
                "Two right-edge openings",
            ],
            "brand_logo_source": {
                "mode": "BRANDLESS",
                "brandless_evidence": "I confirm no brand mark",
            },
        },
        "planning_fact_ids": ["F01", "F03", "F04"],
        "plan": {
            "modules": [{"id": "M01"}, {"id": "M02"}],
            "output_strategy": {
                "claim_coverage": [
                    {
                        "claim_id": "F01",
                        "disposition": "LEAD",
                        "module_ids": ["M01"],
                        "defer_reason_code": "NOT_DEFERRED",
                        "rationale": "Approved specification",
                    },
                    {
                        "claim_id": "F03",
                        "disposition": "DEFERRED",
                        "module_ids": [],
                        "defer_reason_code": "HIGH_RISK_UNDOCUMENTED",
                        "rationale": "Not approved for expression",
                    },
                    {
                        "claim_id": "F04",
                        "disposition": "SUPPORT",
                        "module_ids": ["M02"],
                        "defer_reason_code": "NOT_DEFERRED",
                        "rationale": "Visible original geometry",
                    },
                ]
            },
        },
        "approval_document": {"sha256": "a" * 64},
        "settings": {
            "product_name": "Wrong raw setting",
            "selling_points": ["RAW UNAPPROVED CLAIM"],
        },
    }


def test_original_facts_and_identity_remain_exact_while_expression_is_filtered():
    session = compiled_review_session()
    before = deepcopy(session)
    authority = approved_review_authority(session)
    assert authority["product_name"] == "Approved original bottle"
    assert authority["product_information"] == [
        "Capacity 500 ml",
        "Two original side openings",
    ]
    assert authority["expressible_fact_ids"] == ["F01", "F04"]
    assert authority["locked_identity"] == session["identity"]
    ledger = authority["original_fact_ledger"]
    assert [row["fact"] for row in ledger] == session["input"]["verified_facts"]
    assert [row["approved_disposition"] for row in ledger] == [
        "LEAD",
        "EXCLUDED",
        "DEFERRED",
        "SUPPORT",
    ]
    assert (
        ledger[2]["claim_coverage"]
        == session["plan"]["output_strategy"]["claim_coverage"][1]
    )
    assert authority["source_conflicts"] == session["input"]["source_conflicts"]
    for key in ("input", "identity", "plan"):
        assert authority[f"compiled_{key}_sha256"] == canonical_hash(session[key])
    assert authority["native_approval_document_sha256"] == "a" * 64
    assert session == before
    authority["locked_identity"]["invariant_features"].append("mutated")
    assert session == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_active",
        "unknown_active",
        "duplicate_active",
        "unconfirmed_exclusion",
        "retained_and_rejected",
        "unresolved",
        "missing_coverage",
        "duplicate_coverage",
        "unknown_disposition",
        "missing_approval",
        "wrong_version",
    ],
)
def test_incomplete_authority_cannot_fall_back_to_settings(mutation):
    session = compiled_review_session()
    if mutation == "missing_active":
        del session["planning_fact_ids"]
    elif mutation == "unknown_active":
        session["planning_fact_ids"].append("unknown")
    elif mutation == "duplicate_active":
        session["planning_fact_ids"].append("F01")
    elif mutation == "unconfirmed_exclusion":
        session["input"]["source_conflicts"] = []
    elif mutation == "retained_and_rejected":
        session["input"]["source_conflicts"][0]["resolution"][
            "retained_fact_ids"
        ].append("F02")
    elif mutation == "unresolved":
        session["input"]["source_conflicts"][0]["status"] = "UNRESOLVED"
    elif mutation == "missing_coverage":
        session["plan"]["output_strategy"]["claim_coverage"].pop()
    elif mutation == "duplicate_coverage":
        session["plan"]["output_strategy"]["claim_coverage"].append(
            session["plan"]["output_strategy"]["claim_coverage"][0]
        )
    elif mutation == "unknown_disposition":
        session["plan"]["output_strategy"]["claim_coverage"][0]["disposition"] = (
            "UNKNOWN"
        )
    elif mutation == "missing_approval":
        session["approval_document"] = {}
    else:
        session["version"] = "4.3.0"
    with pytest.raises(DetailReviewError) as error:
        approved_review_authority(session)
    assert error.value.code == "AURORA_NATIVE_REVIEW_AUTHORITY_INVALID"


@pytest.mark.parametrize("mutation", ["hash", "identity", "facts", "ledger"])
def test_changed_audit_context_is_rejected(mutation):
    authority = approved_review_authority(compiled_review_session())
    if mutation == "hash":
        authority["sha256"] = "0" * 64
    elif mutation == "identity":
        authority["product_name"] = "Wrong raw setting"
    elif mutation == "facts":
        authority["product_information"].append("Capacity 300 ml")
    else:
        authority["original_fact_ledger"][0]["approved_disposition"] = "UNKNOWN"
    if mutation != "hash":
        authority["sha256"] = canonical_hash(
            {key: value for key, value in authority.items() if key != "sha256"}
        )
    with pytest.raises(DetailReviewError):
        require_review_authority(authority)
