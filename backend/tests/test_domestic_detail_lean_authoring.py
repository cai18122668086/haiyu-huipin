"""One creative authority, exact derived aliases and stage-scoped context."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.config import default_skill_root
from app.domestic_detail import authoring
from app.domestic_detail.plans import resolve_native_plan
from app.xinghe_agent import _fake_plan, _planner_response_format
from tests.test_api import WORKSPACE_ID, dispatch_pending, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job, _gateway
from tests.test_domestic_detail_planner_contract import request


def pair_fixture(role="SUPPORTING"):
    story_request = request(authoring_stage="story", detail_intake={})
    story = authoring.fake_phase(_fake_plan(story_request), story_request)
    story["modules"][0]["complete_product_role"] = role
    pair_request = replace(
        story_request,
        authoring_stage="pair",
        authoring_target_ids=("M01", "M02"),
        authoring_context=story,
    )
    pair = authoring.fake_phase(_fake_plan(pair_request), pair_request)
    # The fixture deliberately chooses each allowed frozen role independently
    # of its normal screen duty. These checks exercise the wire projection.
    policy, signature = authoring.frozen_rendering_fields(story["modules"][0])
    pair["modules"][0]["expression_signature"] = signature
    pair["modules"][0]["expression"]["product_presence_policy"] = policy
    pair["modules"][0]["product_presence_decision"]["policy"] = policy
    return pair_request, pair


@pytest.mark.parametrize("role", list(authoring.PRESENCE_BY_ROLE))
def test_omitted_aliases_inherit_exact_frozen_decisions_without_mutating_raw(role):
    value, raw = pair_fixture(role)
    first = raw["modules"][0]
    first.pop("expression_signature")
    first["expression"].pop("product_presence_policy")
    first["product_presence_decision"].pop("policy")
    before = deepcopy(raw)
    story_before = deepcopy(value.authoring_context)
    projected = authoring.project_stage(raw, value)
    assert raw == before
    assert value.authoring_context == story_before
    assert authoring.project_stage(projected, value) == projected
    assert not authoring.errors_for(projected, _planner_response_format(value))
    first = projected["modules"][0]
    expected = authoring.PRESENCE_BY_ROLE[role]
    assert first["expression"]["product_presence_policy"] == expected
    assert first["product_presence_decision"]["policy"] == expected
    assert first["expression_signature"].endswith("+" + role)


@pytest.mark.parametrize("field", ["expression", "decision", "signature"])
@pytest.mark.parametrize("invalid", [None, "WRONG"])
def test_explicit_conflicting_alias_is_preserved_and_rejected(field, invalid):
    value, raw = pair_fixture()
    first = raw["modules"][0]
    if field == "expression":
        target, key = first["expression"], "product_presence_policy"
    elif field == "decision":
        target, key = first["product_presence_decision"], "policy"
    else:
        target, key = first, "expression_signature"
    target[key] = invalid
    before = deepcopy(raw)
    projected = authoring.project_stage(raw, value)
    assert raw == before
    assert authoring.errors_for(projected, _planner_response_format(value))


@pytest.mark.parametrize("container", ["expression", "product_presence_decision"])
def test_projection_does_not_invent_missing_creative_container(container):
    value, raw = pair_fixture()
    raw["modules"][0].pop(container)
    projected = authoring.project_stage(raw, value)
    assert container not in projected["modules"][0]
    assert authoring.errors_for(projected, _planner_response_format(value))


def test_pair_prompt_removes_only_identical_duplicate_intake():
    value, _ = pair_fixture()
    intake = {"identity": {"product": "source-observed product"}}
    context = deepcopy(value.authoring_context)
    context["product_intake"] = deepcopy(intake)
    value = replace(value, detail_intake=intake, authoring_context=context)
    before = deepcopy(context)
    body = json.loads(authoring.prompt(value).split("\nINPUT_JSON=", 1)[1])
    assert body["locked_intake"] == intake
    assert "product_intake" not in body["campaign_and_story"]
    assert context == before
    context["product_intake"]["identity"]["product"] = "different product"
    with pytest.raises(ValueError, match="intake disagree"):
        authoring.prompt(value)


def test_real_coordinator_uses_lean_stages_and_compiles_omitted_aliases(
    client, monkeypatch
):
    gateway = _gateway(client)
    original = gateway.plan

    async def omit_aliases(value):
        result = await original(value)
        if value.authoring_stage == "pair":
            payload = deepcopy(result.payload)
            for module in payload["modules"]:
                module.pop("expression_signature")
                module["expression"].pop("product_presence_policy")
                module["product_presence_decision"].pop("policy")
            return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", omit_aliases)
    source = upload_asset(client, filename="lean-product.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "awaiting_confirmation", result.get("error_message")
    assert result["plan"]["payload"]["native_approval"]["document_sha256"]
    calls = gateway.planner_calls
    assert len(calls) == 6
    root = default_skill_root("aurora-domestic-ecommerce-detail-page")
    for call in calls[1:]:
        expected = authoring.system_prompt(root, stage=call.authoring_stage)
        assert call.system_prompt == expected
        assert len(call.system_prompt) < len(authoring.system_prompt(root))
    assert not gateway.image_calls


def test_sparse_creative_replacement_reprojects_aliases_after_merge(
    client, monkeypatch
):
    gateway = _gateway(client)
    original = gateway.plan

    async def incomplete_expression(value):
        result = await original(value)
        if value.authoring_stage != "pair":
            return result
        payload = deepcopy(result.payload)
        for module in payload["modules"]:
            if not isinstance(module, dict):
                continue
            module.pop("expression_signature", None)
            if isinstance(module.get("expression"), dict):
                module["expression"].pop("product_presence_policy", None)
        if value.authoring_target_ids == ("M01", "M02") and not (
            value.authoring_context or {}
        ).get("validation_errors"):
            payload["modules"][0]["expression"].pop("visual_scene_sentence")
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", incomplete_expression)
    source = upload_asset(client, filename="lean-sparse-expression.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "awaiting_confirmation", result.get("error_message")
    modules = resolve_native_plan(result["plan"]["payload"])["modules"]
    first = modules[0]
    assert first["expression"]["product_presence_policy"] == (
        authoring.PRESENCE_BY_ROLE[first["complete_product_role"]]
    )
    assert len(gateway.planner_calls) == 7
    assert not gateway.image_calls
