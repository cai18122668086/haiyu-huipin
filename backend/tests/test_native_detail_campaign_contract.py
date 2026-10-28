"""Native campaign requirements inspection is not an actual pixel review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)

pytest_plugins = ("tests.test_native_detail_delivery_manifest",)


def inventory(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("mode", ["pair_only", "all_contracts", "residual"])
def test_campaign_inspection_reports_dynamic_native_and_schema_requirements_readonly(
    accepted_delivery,
    mode,
):
    root, path, state = accepted_delivery
    if mode == "all_contracts":
        state["plan"].update(
            information_contract_version="4.1",
            expression_contract_version="4.3",
            campaign_pain_solution_contract={"synthetic_present": True},
            campaign_product_presence_rhythm={"synthetic_present": True},
        )
    if mode == "residual":
        state["modules"][0].update(
            status="COMPLETE_WITH_RESIDUALS",
            residual_issues=["Synthetic unresolved geometry"],
        )
    path.write_text(json.dumps(state), encoding="utf-8")
    before = inventory(root)
    runner = AuroraDetailHarnessRunner()
    actual = runner.run("inspect-campaign-review-contract", root, {"session": path})
    assert actual == runner.run(
        "inspect-campaign-review-contract", root, {"session": path}
    )
    assert inventory(root) == before
    assert actual["scope"] == "REQUIREMENTS_AND_ASSET_INVENTORY_ONLY"
    assert actual["preview_optional"] is True
    assert actual["visual_quality_accepted"] is False
    assert actual["original_review_provenance_verified"] is False
    required = actual["required_checks"]
    assert len(required) == len(set(required))
    assert set(actual["native_dynamic_required_checks"]) <= set(required)
    assert set(actual["judgment_schema"]["properties"]["checks"]["required"]) <= set(
        required
    )
    assert {
        "pair_to_pair_seams",
        "actual_subject_diversity",
        "selling_point_recall",
    } <= set(required)
    assert len(required) == (45 if mode == "all_contracts" else 29)
    if mode == "all_contracts":
        assert {
            "product_identity_without_full_product",
            "pain_before_solution",
            "hero_uniqueness",
            "buyer_question_progression",
        } <= set(required)
    assert len(actual["modules"]) == 8 and len(actual["pairs"]) == 4
    for row in actual["modules"]:
        assert (
            hashlib.sha256(Path(row["image"]).read_bytes()).hexdigest()
            == row["image_sha256"]
        )
    assert not (root / "long-page.png").exists()
    assert state["state"] == "AWAITING_USER_FINAL_REVIEW"


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_session_version",
        "busy",
        "missing_asset",
        "bad_plan_order",
        "bad_pair_order",
        "duplicate_pairs",
        "non_integer_order",
    ],
)
def test_campaign_contract_refuses_unaccepted_or_inconsistent_inventory(
    accepted_delivery,
    fault,
):
    root, path, state = accepted_delivery
    if fault == "wrong_session_version":
        state["version"] = "historical-other-version"
    elif fault == "busy":
        state["state"] = "AWAITING_GENERATED_ASSET"
    elif fault == "missing_asset":
        Path(state["modules"][0]["accepted_asset"]).unlink()
    elif fault == "bad_plan_order":
        state["plan"]["modules"].reverse()
    elif fault == "bad_pair_order":
        state["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_ids"][0][
            "module_ids"
        ].reverse()
    elif fault == "duplicate_pairs":
        pairs = state["plan"]["output_strategy"]["continuous_pair_rendering"][
            "pair_ids"
        ]
        pairs[1]["pair_id"] = pairs[0]["pair_id"]
    else:
        state["modules"][0]["order"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    before = inventory(root)
    with pytest.raises(AuroraDetailHarnessError):
        AuroraDetailHarnessRunner().run(
            "inspect-campaign-review-contract", root, {"session": path}
        )
    assert inventory(root) == before
