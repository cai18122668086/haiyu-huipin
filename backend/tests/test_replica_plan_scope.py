"""Model revisions must cover the immutable source IDs, including omissions."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from app.replica_exact_compact import CompactResponseInvalid, response_schema
from app.replica_format_feedback import ReplicaFormatFeedback
from app.replica_plan_scope import bind_plan_schema, scope_errors

from .test_replica_compact import setup_run


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    run, gateway, payload, records = setup_run(tmp_path_factory.mktemp("scope"))
    asyncio.run(run.prepare(payload, records))
    request = next(
        r
        for r in gateway.requests
        if json.loads(r.product_information[0])["stage"] == "plan"
    )
    raw = deepcopy(
        next(
            r["raw"]
            for r in run.checkpoints["results"].values()
            if r["phase"] == "plan"
        )
    )
    module = raw["modules"][0]
    for field in ("text_bindings", "fact_adaptations"):
        row = deepcopy(module[field][0])
        row["region_id"] = "body"
        module[field].append(row)
    module["content_groups"][0]["region_ids"].append("body")
    data = {
        "_proposal_units": [{"id": "R001"}],
        "reference_layouts": {
            "R001": {
                "text_regions": [{"id": "headline"}, {"id": "body"}],
                "layout_anchors": [{"label": "Original source subject"}],
            }
        },
    }
    return raw, data, request


def test_schema_preserves_full_contract_and_valid_existing_regions(fixture):
    raw, data, _ = fixture
    base = response_schema("plan")
    original = deepcopy(base)
    schema = bind_plan_schema(base, data)
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(raw)
    assert base == original
    assert (
        schema["properties"]["modules"]["items"]["required"]
        == base["properties"]["modules"]["items"]["required"]
    )
    assert scope_errors(raw, data) == []


@pytest.mark.parametrize(
    "field", ["text_bindings", "fact_adaptations", "content_groups"]
)
@pytest.mark.parametrize("kind", ["rename", "duplicate", "missing"])
def test_any_renamed_missing_or_duplicate_region_is_rejected(fixture, field, kind):
    original, data, _ = fixture
    raw = deepcopy(original)
    rows = raw["modules"][0][field]
    if field == "content_groups":
        ids = rows[0]["region_ids"]
        if kind == "missing":
            ids.pop()
        else:
            ids[-1] = "invented-title" if kind == "rename" else "headline"
    elif kind == "missing":
        rows.pop()
    else:
        rows[-1]["region_id"] = "invented-title" if kind == "rename" else "headline"
    schema = bind_plan_schema(response_schema("plan"), data)
    assert not Draft202012Validator(schema).is_valid(raw)
    errors = scope_errors(raw, data)
    assert errors and "missing existing IDs=['body']" in errors[0]
    assert raw != original and len(original["modules"][0][field]) == len(
        fixture[0]["modules"][0][field]
    )


def test_full_prior_response_and_exact_id_schema_reach_format_feedback(fixture):
    raw, data, original_request = fixture
    raw = deepcopy(raw)
    raw["modules"][0]["fact_adaptations"][1]["region_id"] = "invented-title"
    schema = bind_plan_schema(response_schema("plan"), data)
    envelope = {
        "stage": "plan",
        "context": {},
        "response_schema": schema,
        "validation_error": "",
    }
    request = replace(original_request, product_information=(json.dumps(envelope),))
    feedback = ReplicaFormatFeedback()
    feedback.remember(request, raw)
    envelope["validation_error"] = "; ".join(scope_errors(raw, data))
    correction = feedback.prompt(
        replace(request, product_information=(json.dumps(envelope),))
    )
    assert correction and "previous_complete_response" in correction
    assert "invented-title" in correction and "body" in correction
    assert raw["modules"][0]["fact_adaptations"][1]["region_id"] == "invented-title"


def test_persistent_renamed_region_has_specific_failure_before_images(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    old = gateway.plan

    async def invalid(request):
        result = await old(request)
        if json.loads(request.product_information[0])["stage"] == "plan":
            result.payload["modules"][0]["fact_adaptations"][0]["region_id"] = (
                "invented-title"
            )
        return result

    gateway.plan = invalid
    with pytest.raises(
        CompactResponseInvalid,
        match="missing existing IDs=.*headline.*unexpected IDs=.*invented-title",
    ):
        asyncio.run(run.prepare(payload, records))
    plans = [
        r
        for r in gateway.requests
        if json.loads(r.product_information[0])["stage"] == "plan"
    ]
    assert len(plans) == 2 and not gateway.image_requests
    assert (
        "missing existing IDs"
        in json.loads(plans[1].product_information[0])["validation_error"]
    )
