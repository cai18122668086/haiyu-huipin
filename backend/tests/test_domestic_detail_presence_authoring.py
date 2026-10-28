"""Native role completion preserves the authored presence decision."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from app.config import default_skill_root
from app.xinghe_agent import _complete_aurora_module_structure


@pytest.mark.parametrize(
    "role,policy",
    [
        ("DOMINANT", "REQUIRED_DOMINANT"),
        ("SUPPORTING", "REQUIRED_SUPPORTING"),
        ("IDENTITY_TOKEN_ONLY", "IDENTITY_TOKEN_ONLY"),
        ("ABSENT", "ABSENT_PREFERRED"),
    ],
)
@pytest.mark.parametrize("source", ["role", "expression", "decision"])
def test_native_completion_preserves_explicit_policy_or_exact_role(
    role, policy, source
):
    raw = {"complete_product_role": role}
    if source == "expression":
        raw["expression"] = {"product_presence_policy": policy}
    elif source == "decision":
        raw["product_presence_decision"] = {"policy": policy}
    before = deepcopy(raw)
    first = _complete_aurora_module_structure(raw)
    second = _complete_aurora_module_structure(first)
    assert first["product_presence_decision"]["policy"] == policy
    assert second["product_presence_decision"] == first["product_presence_decision"]
    assert raw == before


@pytest.mark.parametrize("value", ["UNKNOWN", "", None])
@pytest.mark.parametrize("source", ["expression", "decision"])
def test_native_completion_does_not_convert_unknown_presence_to_a_valid_policy(
    value, source
):
    raw = {"complete_product_role": "SUPPORTING"}
    if source == "expression":
        raw["expression"] = {"product_presence_policy": value}
    else:
        raw["product_presence_decision"] = {"policy": value}
    result = _complete_aurora_module_structure(raw)
    assert result["product_presence_decision"]["policy"] == value
    schema = json.loads(
        (
            default_skill_root("aurora-domestic-ecommerce-detail-page")
            / "schemas/compact-detail-plan.schema.json"
        ).read_bytes()
    )["properties"]["modules"]["items"]["properties"]["product_presence_decision"]
    assert not Draft202012Validator(schema).is_valid(
        result["product_presence_decision"]
    )
