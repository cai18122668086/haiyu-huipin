from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.aurora_detail_runtime import normalize_native_plan_for_harness

CASES = json.loads(
    (Path(__file__).parent / "fixtures/domestic_detail/deferred_claim_cases.json")
    .read_text(encoding="utf-8")
)["cases"]


def claim_plan(claim: dict) -> dict:
    return {
        "modules": [
            {"id": "M01", "lead_claim_id": "F01", "supporting_claim_ids": ["F02"]}
        ],
        "output_strategy": {"claim_coverage": [deepcopy(claim)]},
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case"])
def test_source_deferred_reasons_survive_normalization(case: dict) -> None:
    """Neither the original response nor its partial repair may lose a reason."""
    plan = claim_plan(case["claim"])
    before = deepcopy(plan)
    normalized = normalize_native_plan_for_harness(plan)
    claim = normalized["output_strategy"]["claim_coverage"][0]
    assert claim["disposition"] == "DEFERRED"
    assert claim["defer_reason_code"] == case["claim"]["defer_reason_code"]
    assert claim["module_ids"] == []
    assert plan == before
    assert normalize_native_plan_for_harness(normalized) == normalized


@pytest.mark.parametrize("disposition,claim_id", [
    ("LEAD", "F01"), ("SUPPORT", "F02"), ("CONTEXT_ONLY", "F02"),
])
def test_explicit_selected_claims_preserve_their_contract(
    disposition: str, claim_id: str,
) -> None:
    claim = {
        "claim_id": claim_id, "disposition": disposition,
        "module_ids": ["M01"], "rationale": "A verified fact supports this screen.",
        "defer_reason_code": "NOT_DEFERRED",
    }
    normalized = normalize_native_plan_for_harness(claim_plan(claim))
    assert normalized["output_strategy"]["claim_coverage"] == [claim]


def test_public_runtime_path_reexports_detail_contract() -> None:
    from app.domestic_detail.contracts import (
        normalize_native_plan_for_harness as detail_adapter,
    )

    assert normalize_native_plan_for_harness is detail_adapter


@pytest.fixture(scope="module")
def detail_harness():
    """Use the locked validator itself and restore its generic runtime imports."""
    scripts = (
        Path(__file__).resolve().parents[1]
        / "skill_sources/aurora-domestic-ecommerce-detail-page/scripts"
    )
    previous = {
        name: module for name, module in sys.modules.items()
        if name == "runtime" or name.startswith("runtime.")
    }
    for name in previous:
        sys.modules.pop(name)
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(
            "_detail_contract_test_harness", scripts / "harness.py"
        )
        assert spec is not None and spec.loader is not None
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        yield harness
    finally:
        sys.path.remove(str(scripts))
        for name in list(sys.modules):
            if name == "runtime" or name.startswith("runtime."):
                sys.modules.pop(name)
        sys.modules.update(previous)


def coverage_schema_errors(claim: dict) -> list:
    path = (
        Path(__file__).resolve().parents[1]
        / "skill_sources/aurora-domestic-ecommerce-detail-page"
        / "schemas/detail-plan.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    item = schema["properties"]["output_strategy"]["properties"]
    validator = Draft202012Validator(item["claim_coverage"]["items"])
    return list(validator.iter_errors(claim))


@pytest.mark.parametrize("reason", [
    "CONFLICTING_FACT", "HIGH_RISK_UNDOCUMENTED",
    "UNSUPPORTED_INVENTION", "IMMATERIAL_DUPLICATE",
])
def test_every_allowed_deferred_reason_is_preserved_and_accepted(
    detail_harness, reason: str,
) -> None:
    claim = {**CASES[0]["claim"], "defer_reason_code": reason}
    normalized = normalize_native_plan_for_harness(claim_plan(claim))
    actual = normalized["output_strategy"]["claim_coverage"][0]
    assert actual == claim
    assert coverage_schema_errors(actual) == []
    assert detail_harness._collect_plan_mapping_errors({}, normalized) == []


@pytest.mark.parametrize("disposition", ["LEAD", "SUPPORT", "CONTEXT_ONLY", "DEFERRED"])
@pytest.mark.parametrize("reason", ["MISSING", None, "", "UNKNOWN_FUTURE_REASON", 7])
def test_missing_and_unknown_reasons_are_not_invented_or_promoted(
    detail_harness, disposition: str, reason: object,
) -> None:
    claim = {
        "claim_id": "F03" if disposition == "DEFERRED" else "F01",
        "disposition": disposition,
        "module_ids": [] if disposition == "DEFERRED" else ["M01"],
        "rationale": "No reason may be silently substituted.",
    }
    if reason != "MISSING":
        claim["defer_reason_code"] = reason
    plan = claim_plan(claim)
    before = deepcopy(plan)
    normalized = normalize_native_plan_for_harness(plan)
    actual = normalized["output_strategy"]["claim_coverage"][0]
    assert actual == claim
    assert plan == before
    assert coverage_schema_errors(actual)
    assert detail_harness._collect_plan_mapping_errors({}, normalized)
    assert normalize_native_plan_for_harness(normalized) == normalized


@pytest.mark.parametrize("disposition,reason", [
    ("DEFERRED", "NOT_DEFERRED"),
    ("LEAD", "UNSUPPORTED_INVENTION"),
    ("SUPPORT", "HIGH_RISK_UNDOCUMENTED"),
    ("CONTEXT_ONLY", "CONFLICTING_FACT"),
])
def test_contradictory_known_reasons_remain_rejected(
    detail_harness, disposition: str, reason: str,
) -> None:
    claim = {
        "claim_id": "F03" if disposition == "DEFERRED" else "F01",
        "disposition": disposition,
        "module_ids": [] if disposition == "DEFERRED" else ["M01"],
        "rationale": "Enum membership does not imply semantic compatibility.",
        "defer_reason_code": reason,
    }
    normalized = normalize_native_plan_for_harness(claim_plan(claim))
    actual = normalized["output_strategy"]["claim_coverage"][0]
    assert actual == claim
    assert coverage_schema_errors(actual) == []
    assert detail_harness._collect_plan_mapping_errors({}, normalized)


@pytest.mark.parametrize("disposition", ["UNKNOWN_FUTURE_DISPOSITION", "", None])
def test_unknown_dispositions_are_not_replaced(disposition: object) -> None:
    claim = {**CASES[0]["claim"], "disposition": disposition}
    normalized = normalize_native_plan_for_harness(claim_plan(claim))
    actual = normalized["output_strategy"]["claim_coverage"][0]
    assert actual == claim
    assert coverage_schema_errors(actual)


@pytest.mark.parametrize("module_count", [8, 10, 12])
def test_claim_reason_fix_keeps_screen_order_and_count(module_count: int) -> None:
    plan = claim_plan(CASES[0]["claim"])
    plan["modules"] = [
        {"id": f"M{index:02d}", "lead_claim_id": "F01"}
        for index in range(1, module_count + 1)
    ]
    normalized = normalize_native_plan_for_harness(plan)
    assert [m["id"] for m in normalized["modules"]] == [
        f"M{index:02d}" for index in range(1, module_count + 1)
    ]
    assert normalized["output_strategy"]["module_count"] == module_count
    assert normalized["output_strategy"]["claim_coverage"] == [CASES[0]["claim"]]
    assert normalize_native_plan_for_harness(normalized) == normalized
