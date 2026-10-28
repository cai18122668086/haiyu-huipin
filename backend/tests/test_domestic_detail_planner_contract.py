"""Exercise real authoring messages and reject silent decision replacement."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.xinghe_agent import (
    PlannerRequest,
    _complete_aurora_module_structure,
    _planner_prompt,
    _planner_response_format,
)


def request(**kwargs) -> PlannerRequest:
    base = PlannerRequest(
        workspace_id="ws_wire",
        job_id="job_wire",
        group_key="primary",
        sources=(),
        page_count=8,
        language="zh-CN",
        product_name="Synthetic jar",
        product_information=(),
        platform_code="tmall",
        size="1440x1920",
        idempotency_key="wire",
        planning_profile="aurora_domestic_detail",
    )
    return replace(base, **kwargs)


def test_single_module_completion_receives_all_saved_errors_and_exact_zone_bound():
    errors = "VALIDATION_ERRORS_JSON=" + json.dumps(
        [
            {
                "path": "/modules/2/structure/zone_briefs",
                "message": "three zones exceed MACRO_DIAGRAM two-zone bound",
            }
        ]
    )
    context = {
        "current_module": {
            "id": "M03",
            "structure": {
                "structure_family": "MACRO_DIAGRAM",
                "zone_briefs": ["whole", "macro", "label"],
            },
        }
    }
    value = request(
        planning_profile="aurora_domestic_modules_completion",
        page_count=1,
        product_information=(
            "CURRENT_AURORA_PLAN_JSON=" + json.dumps(context),
            errors,
            "TARGET_MODULE_ID=M03",
        ),
    )
    assert errors in _planner_prompt(value)
    schema = _planner_response_format(value)["json_schema"]["schema"]
    structure = schema["properties"]["modules"]["items"]["properties"]["structure"]
    assert structure["properties"]["structure_family"] == {"const": "MACRO_DIAGRAM"}
    assert structure["properties"]["zone_briefs"]["minItems"] == 2
    assert structure["properties"]["zone_briefs"]["maxItems"] == 2


@pytest.mark.parametrize(
    "profile",
    [
        "aurora_domestic_detail",
        "aurora_domestic_campaign_completion",
        "aurora_domestic_plan_repair",
    ],
)
def test_product_only_intake_does_not_create_adopted_style_references(profile):
    value = request(planning_profile=profile, detail_intake={"reference_materials": []})
    reference = _planner_response_format(value)["json_schema"]["schema"]["properties"][
        "reference_expression_system"
    ]["properties"]
    assert reference["coverage"]["maxItems"] == 0
    assert reference["adopted_patterns"]["maxItems"] == 0
    assert "ZERO supplied style_reference" in _planner_prompt(value)


def test_invalid_authored_roles_reach_validator_without_becoming_another_choice():
    authored = {
        "id": "M05",
        "complete_product_role": "REQUIRED_SUPPORTING",
        "expression_signature": "ACTION+MULTI_PAIN_ARRAY+medium+REQUIRED_SUPPORTING",
    }
    before = deepcopy(authored)
    normalized = _complete_aurora_module_structure(authored)
    assert normalized["complete_product_role"] == authored["complete_product_role"]
    assert normalized["expression_signature"] == authored["expression_signature"]
    assert authored == before
