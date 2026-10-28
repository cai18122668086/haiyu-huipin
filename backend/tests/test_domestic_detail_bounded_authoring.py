"""Real coordinator and native compilation, with an explicit synthetic supplier."""

from copy import deepcopy
from dataclasses import replace

import pytest

from app.domestic_detail import authoring
from app.xinghe_agent import (
    _complete_aurora_module_structure,
    _planner_response_format,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job, _gateway
from tests.test_domestic_detail_planner_contract import request


@pytest.mark.parametrize("count", [1, 3, 5])
def test_authored_payload_count_survives_adapter(count):
    raw = {"proof": {"minimum_visual_payload": count}}
    assert (
        _complete_aurora_module_structure(raw)["proof"]["minimum_visual_payload"]
        == count
    )
    assert raw == {"proof": {"minimum_visual_payload": count}}


def test_story_wire_locks_count_ids_and_excludes_detailed_modules():
    value = request(authoring_stage="story")
    schema = _planner_response_format(value)["json_schema"]["schema"]
    modules = schema["properties"]["modules"]
    assert modules["minItems"] == modules["maxItems"] == 8
    assert [m["properties"]["id"] for m in modules["prefixItems"]] == [
        {"const": f"M{i:02d}"} for i in range(1, 9)
    ]
    assert "proof" not in modules["prefixItems"][0]["properties"]
    assert schema["properties"]["operating_strategy"]["required"]


def test_actual_staged_coordinator_reaches_native_approval(client):
    source = upload_asset(client, filename="bounded-product.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
        planned.get("plan", {}).get("payload", {}).get("validation_errors"),
    )
    calls = _gateway(client).planner_calls
    assert [c.authoring_stage for c in calls] == [
        "",
        "story",
        "pair",
        "pair",
        "pair",
        "pair",
    ]
    assert [c.authoring_target_ids for c in calls[2:]] == [
        ("M01", "M02"),
        ("M03", "M04"),
        ("M05", "M06"),
        ("M07", "M08"),
    ]
    assert all(not c.sources for c in calls[2:])
    assert len(calls[1].system_prompt) < 40000
    assert planned["plan"]["payload"]["native_approval"]["document_sha256"]
    assert not _gateway(client).image_calls


def test_pair_does_not_change_story_or_accept_wrong_zone_count(client):
    source = upload_asset(client, filename="bounded-schema.png")
    _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    calls = _gateway(client).planner_calls
    pair = next(c for c in calls if c.authoring_stage == "pair")
    schema = _planner_response_format(pair)["json_schema"]["schema"]
    first = schema["properties"]["modules"]["prefixItems"][0]
    assert first["properties"]["lead_conclusion"] == {
        "const": pair.authoring_context["modules"][0]["lead_conclusion"]
    }
    assert schema["properties"]["modules"]["minItems"] == 2
    assert schema["properties"]["modules"]["maxItems"] == 2
    assert authoring.errors_for({"modules": []}, _planner_response_format(pair))
    from app.domestic_detail.mock_intake import mock_plan_with_intake
    from app.xinghe_agent import _fake_plan

    payload = authoring.fake_phase(
        mock_plan_with_intake(_fake_plan(pair), pair.detail_intake), pair
    )
    payload["modules"][0]["structure"] = {
        **payload["modules"][0]["structure"],
        "structure_family": "FULL_BLEED_HERO",
        "zone_briefs": ["one", "two", "three"],
    }
    assert any(
        "structure" in e["path"]
        for e in authoring.errors_for(payload, _planner_response_format(pair))
    )


def test_native_depth_is_not_overwritten():
    from app.domestic_detail.contracts import normalize_native_plan_for_harness

    depth = {
        "selection_policy": "authored",
        "primary_claim_ids": ["F01"],
        "claim_depth": [
            {
                "claim_id": "F01",
                "module_jobs": [{"module_id": "M03", "job": "DEDICATED_EXPRESSION"}],
            }
        ],
    }
    normalized = normalize_native_plan_for_harness(
        {"modules": [], "selling_point_depth_contract": deepcopy(depth)}
    )
    assert normalized["selling_point_depth_contract"] == depth


@pytest.mark.parametrize("failure", ["invalid", "unknown", "deadline"])
def test_bounded_failure_stops_without_new_task_or_images(client, monkeypatch, failure):
    gateway = _gateway(client)
    original = gateway.plan

    async def controlled(value):
        result = await original(value)
        if value.authoring_stage == "story":
            if failure == "unknown":
                return replace(
                    result,
                    payload=None,
                    error_code="PROVIDER_TRANSPORT_FAILED",
                    error_message="unknown",
                    http_status=None,
                )
            if failure == "invalid":
                payload = deepcopy(result.payload)
                payload["modules"].append(deepcopy(payload["modules"][-1]))
                return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", controlled)
    if failure == "deadline":
        monkeypatch.setattr(authoring, "PLANNING_SECONDS", 0.0)
    source = upload_asset(client, filename="bounded-stop.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "failed"
    assert not any(c.authoring_stage == "pair" for c in gateway.planner_calls)
    assert (
        len(gateway.planner_calls)
        == {"invalid": 3, "unknown": 2, "deadline": 1}[failure]
    )
    assert not gateway.image_calls


def test_resolved_conflict_wire_requires_identity_even_with_historical_conflict():
    from app.domestic_detail.intake import intake_prompt, intake_response_schema

    settings = {
        "product_name": "参考图女鞋",
        "selling_points": ["尖头轮廓"],
        "brandless_confirmation": "用户确认仅作来源水印，测试图不添加品牌",
    }
    resolution = {
        "resolutions": [
            {
                "conflict_id": "C01",
                "retained_fact_ids": ["F01"],
                "rejected_fact_ids": ["O02"],
            }
        ]
    }
    wire = request(
        planning_profile="aurora_domestic_intake",
        intake_settings=settings,
        intake_resolution=resolution,
    )
    schema = _planner_response_format(wire)["json_schema"]["schema"]
    assert {"identity", "identity_observations"}.issubset(schema["required"])
    assert schema["properties"]["identity"]["type"] == "object"
    assert "allOf" not in schema  # provider-compatible root stays intact
    unresolved = intake_response_schema(wire_settings=settings)
    assert "identity" not in unresolved["required"]
    prompt = intake_prompt(settings=settings, sources=[], resolution=resolution)
    assert "Do not stop at the old conflict again" in prompt
    assert "material conflict MUST stop" not in prompt
    before = intake_prompt(settings=settings, sources=[])
    assert "material conflict MUST stop" in before


@pytest.mark.parametrize("ambiguous", [False, True])
def test_selected_story_preserves_authored_selection_and_raw_reply(ambiguous):
    selected = {
        "candidate_mode": "DIRECT_DERIVATION",
        "count": 8,
        "decision": "SELECTED",
        "rationale": "authored evidence",
    }
    alternative = {
        "candidate_mode": "FULL_SALES",
        "count": 12,
        "decision": "SELECTED" if ambiguous else "REJECTED",
    }
    raw = {
        "output_strategy": {"module_count_candidates": [selected, alternative]},
        "modules": [{"duty": "unchanged"}],
    }
    original = deepcopy(raw)
    result = authoring.selected_story(raw, 8)
    assert raw == original
    assert result["output_strategy"]["module_count_candidates"] == (
        [selected, alternative] if ambiguous else [selected]
    )
    assert result["modules"] == raw["modules"]
    assert authoring.selected_story(raw, 10) == raw


@pytest.mark.parametrize(
    "fail_targets,remaining", [(("M01", "M02"), 4), (("M03", "M04"), 3)]
)
def test_failed_retry_reuses_intake_and_story_without_supplier_dispatch(
    client, monkeypatch, fail_targets, remaining
):
    from app.xinghe_agent import detail_workspace
    from tests.test_api import idem

    gateway = _gateway(client)
    original = gateway.plan
    failed = False

    async def fail_first_pair(value):
        nonlocal failed
        result = await original(value)
        if value.authoring_target_ids == fail_targets and not failed:
            failed = True
            return replace(
                result,
                payload=None,
                error_code="SYNTHETIC_REJECTED",
                error_message="explicit test rejection",
                external_request=False,
            )
        return result

    monkeypatch.setattr(gateway, "plan", fail_first_pair)
    source = upload_asset(client, filename="reusable-story.png")
    first = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    original_root = detail_workspace(first["id"])
    originals = {
        str(p): p.read_bytes()
        for folder in ("intake", "planner-calls")
        for p in (original_root / folder).glob("*")
        if p.is_file()
    }
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{first['id']}").json()[
            "status"
        ]
        == "failed"
    )
    before = len(gateway.planner_calls)
    retry = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{first['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert retry.status_code == 201, retry.text
    second = retry.json()
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{second['id']}").json()
    assert result["status"] == "awaiting_confirmation", result.get("error_message")
    assert [c.authoring_stage for c in gateway.planner_calls[before:]] == [
        "pair"
    ] * remaining
    assert (
        detail_workspace(second["id"]) / "planning" / "story-recovery.json"
    ).is_file()
    from pathlib import Path

    assert all(Path(p).read_bytes() == raw for p, raw in originals.items())
    assert not gateway.image_calls


def test_pair_correction_cannot_erase_already_valid_fields(client, monkeypatch):
    gateway = _gateway(client)
    original = gateway.plan

    async def corrected_with_regression(value):
        result = await original(value)
        if value.authoring_target_ids == ("M01", "M02"):
            payload = deepcopy(result.payload)
            if (value.authoring_context or {}).get("validation_errors"):
                assert value.authoring_context["prior_response"]["modules"]
                assert set(payload) == {"modules"}
                assert set(payload["modules"][0]) == {"visible_copy"}
                assert not authoring.errors_for(
                    payload, _planner_response_format(value)
                )
                payload["modules"][0]["variation_from_previous"] = []
            else:
                payload["modules"][0]["visible_copy"][0].pop("role")
            return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", corrected_with_regression)
    source = upload_asset(client, filename="correction-regression.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == "awaiting_confirmation", planned.get("error_message")
    assert len(gateway.planner_calls) == 7
    assert not gateway.image_calls


def test_error_merge_never_invents_missing_replacement():
    original = {"modules": [{"valid": "keep", "broken": []}]}
    corrected = {"modules": [{"valid": "unrelated change"}]}
    assert (
        authoring.merge_error_fields(
            original, corrected, [{"path": "/modules/0/broken"}]
        )
        == original
    )
    corrected["modules"][0]["broken"] = ["authored"]
    result = authoring.merge_error_fields(
        original, corrected, [{"path": "/modules/0/broken"}]
    )
    assert result == {"modules": [{"valid": "keep", "broken": ["authored"]}]}
    assert original["modules"][0]["broken"] == []


@pytest.mark.parametrize("problem", ["unknown_fact", "recall_drift", "dedicated_drift"])
def test_story_semantics_stop_before_any_paid_pair(client, monkeypatch, problem):
    gateway = _gateway(client)
    original = gateway.plan

    async def invalid_story(value):
        result = await original(value)
        if value.authoring_stage == "story":
            payload = deepcopy(result.payload)
            if problem == "unknown_fact":
                payload["selling_point_depth_contract"]["primary_claim_ids"][0] = "C99"
            elif problem == "recall_drift":
                payload["campaign_recall_contract"]["must_remember_claim_ids"] = []
            else:
                first = payload["selling_point_depth_contract"]["claim_depth"][0]
                first["dedicated_module_ids"] = [
                    m["id"]
                    for m in payload["modules"]
                    if m["lead_claim_id"] != first["claim_id"]
                ][:1]
            return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", invalid_story)
    source = upload_asset(client, filename="invalid-story-semantics.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "failed"
    assert not any(c.authoring_stage == "pair" for c in gateway.planner_calls)
    assert len(gateway.planner_calls) == 3
    assert not gateway.image_calls


def test_pair_omitted_frozen_fields_are_copied_from_one_story(client, monkeypatch):
    gateway = _gateway(client)
    original = gateway.plan

    async def omit_spine(value):
        result = await original(value)
        if value.authoring_stage == "pair":
            payload = deepcopy(result.payload)
            for module in payload["modules"]:
                for field in authoring.STORY_FIELDS:
                    if field != "id":
                        module.pop(field)
            return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", omit_spine)
    source = upload_asset(client, filename="frozen-spine.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == "awaiting_confirmation", result.get("error_message")
    assert len(gateway.planner_calls) == 6
    assert not gateway.image_calls


def test_prompt_contains_the_exact_nested_contract(client):
    import json

    from jsonschema import Draft202012Validator

    from app.xinghe_agent import _fake_plan, _planner_prompt

    source = upload_asset(client, filename="prompt-contract.png")
    _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    for value in _gateway(client).planner_calls:
        if not value.authoring_stage:
            continue
        prompt = _planner_prompt(value)
        contract = json.loads(
            prompt.split("RESPONSE_CONTRACT_JSON=", 1)[1].split("\nINPUT_JSON=", 1)[0]
        )
        Draft202012Validator.check_schema(contract)
        payload = authoring.fake_phase(_fake_plan(value), value)
        original = _planner_response_format(value)["json_schema"]["schema"]
        assert Draft202012Validator(contract).is_valid(payload)
        assert Draft202012Validator(original).is_valid(payload)
        assert len(json.dumps(contract)) <= len(json.dumps(original))
        if value.authoring_stage == "pair":
            payload["modules"][0]["visible_copy"][0].pop("role")
            assert not Draft202012Validator(contract).is_valid(payload)
            assert not Draft202012Validator(original).is_valid(payload)


def test_retry_corrects_saved_invalid_story_without_reauthoring(client, monkeypatch):
    import json
    from pathlib import Path

    from app.xinghe_agent import detail_workspace
    from tests.test_api import idem

    gateway = _gateway(client)
    original = gateway.plan
    inject_invalid = True

    async def invalid_until_retry(value):
        result = await original(value)
        if value.authoring_stage == "story" and inject_invalid:
            payload = deepcopy(result.payload)
            payload["selling_point_depth_contract"]["primary_claim_ids"][0] = "C99"
            return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", invalid_until_retry)
    source = upload_asset(client, filename="known-invalid-story.png")
    first = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{first['id']}").json()[
            "status"
        ]
        == "failed"
    )
    originals = {
        str(p): p.read_bytes()
        for p in (detail_workspace(first["id"]) / "planner-calls").glob("*")
        if p.is_file()
    }
    before = len(gateway.planner_calls)
    inject_invalid = False
    retry = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{first['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert retry.status_code == 201, retry.text
    dispatch_pending(client)
    second = retry.json()
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{second['id']}"
    ).json()
    assert planned["status"] == "awaiting_confirmation", planned.get("error_message")
    calls = gateway.planner_calls[before:]
    assert [c.authoring_stage for c in calls] == [
        "story",
        "pair",
        "pair",
        "pair",
        "pair",
    ]
    assert calls[0].authoring_context["prior_response"]
    assert any(
        e["constraint"] == "enum"
        for e in calls[0].authoring_context["validation_errors"]
    )
    assert any(
        e["constraint"] == "native_sales_depth"
        for e in calls[0].authoring_context["validation_errors"]
    )
    recovery = json.loads(
        (
            detail_workspace(second["id"]) / "planning" / "story-recovery.json"
        ).read_bytes()
    )
    assert recovery["stage_valid"] is False
    assert recovery["provider_request_count"] == 0
    assert all(Path(p).read_bytes() == raw for p, raw in originals.items())
    assert not gateway.image_calls


def test_sparse_second_screen_correction_preserves_first_screen(client, monkeypatch):
    gateway = _gateway(client)
    original = gateway.plan

    async def missing_second_copy_role(value):
        result = await original(value)
        if value.authoring_target_ids == ("M01", "M02"):
            payload = deepcopy(result.payload)
            if (value.authoring_context or {}).get("validation_errors"):
                assert payload["modules"][0] is None
                assert set(payload["modules"][1]) == {"visible_copy"}
                assert not authoring.errors_for(
                    payload, _planner_response_format(value)
                )
                assert "not the whole campaign or whole pair" in authoring.prompt(value)
            else:
                payload["modules"][1]["visible_copy"][0].pop("role")
            return replace(result, payload=payload)
        return result

    monkeypatch.setattr(gateway, "plan", missing_second_copy_role)
    source = upload_asset(client, filename="sparse-second-screen.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == "awaiting_confirmation", planned.get("error_message")
    assert planned["plan"]["payload"]["native_approval"]["document_sha256"]
    assert len(gateway.planner_calls) == 7
    assert not gateway.image_calls


@pytest.mark.parametrize("complete_correction", [False, True])
def test_unknown_fact_and_extra_depth_are_corrected_together(
    client, monkeypatch, complete_correction
):
    gateway = _gateway(client)
    original = gateway.plan
    observed = []

    async def mixed_story_errors(value):
        result = await original(value)
        if value.authoring_stage != "story":
            return result
        payload = deepcopy(result.payload)
        errors = (value.authoring_context or {}).get("validation_errors")
        if errors:
            observed.extend(errors)
            assert any(e["constraint"] == "enum" for e in errors)
            assert any(
                e["path"] == "/selling_point_depth_contract/claim_depth"
                and e["constraint"] == "native_sales_depth"
                for e in errors
            )
            assert any(e["path"].endswith("/depth_tier") for e in errors)
            assert not authoring.errors_for(payload, _planner_response_format(value))
            # The correction must be allowed to remove a row, not just rename IDs.
            assert len(payload["selling_point_depth_contract"]["claim_depth"]) < len(
                value.authoring_context["prior_response"]["selling_point_depth_contract"][
                    "claim_depth"
                ]
            )
            if not complete_correction:
                extra = deepcopy(
                    payload["selling_point_depth_contract"]["claim_depth"][0]
                )
                extra.update(claim_id="O01", depth_tier="SUPPORT")
                payload["selling_point_depth_contract"]["claim_depth"].append(extra)
        else:
            payload["modules"][0]["lead_claim_id"] = "C99"
            extra = deepcopy(payload["selling_point_depth_contract"]["claim_depth"][0])
            extra.update(claim_id="C04", depth_tier="SUPPORT")
            payload["selling_point_depth_contract"]["claim_depth"].append(extra)
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", mixed_story_errors)
    source = upload_asset(client, filename="mixed-depth-and-fact-errors.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert observed
    assert len([c for c in gateway.planner_calls if c.authoring_stage == "story"]) == 2
    if complete_correction:
        assert result["status"] == "awaiting_confirmation", result.get("error_message")
        assert result["plan"]["payload"]["native_approval"]["document_sha256"]
        assert len(gateway.planner_calls) == 7
    else:
        assert result["status"] == "failed"
        assert result["error_code"] == "AURORA_AUTHORING_INVALID"
        assert not any(c.authoring_stage == "pair" for c in gateway.planner_calls)
        assert len(gateway.planner_calls) == 3
    assert not gateway.image_calls


@pytest.mark.parametrize("complete_correction", [False, True])
def test_formula_and_recall_conflicts_share_one_native_correction(
    client, monkeypatch, complete_correction
):
    from app.xinghe_agent import _planner_prompt

    gateway = _gateway(client)
    original = gateway.plan
    observed = []

    async def invalid_campaign(value):
        result = await original(value)
        if value.authoring_stage != "story":
            return result
        payload = deepcopy(result.payload)
        errors = (value.authoring_context or {}).get("validation_errors")
        if errors:
            observed.extend(errors)
            assert any(e["constraint"] == "native_formula_split" for e in errors)
            assert any(
                e["path"] == "/campaign_recall_contract/proof_module_ids_by_claim"
                for e in errors
            )
            assert set(payload) == set(value.authoring_context["prior_response"])
            assert "root-level relational error" in _planner_prompt(value)
            assert "STRUCTURE_COMPONENT" in _planner_prompt(value)
            assert "not the whole campaign" not in _planner_prompt(value)
            assert not authoring.stage_errors(
                payload, replace(value, authoring_context=None)
            )
            if complete_correction:
                return result
        # The mock intake has no formula/structure facts. Extra evaluation is
        # therefore rejected by the pinned native validator, not a parallel rule.
        payload["formula_split_contract"]["evaluated_claims"] = [{"claim_id": "F01"}]
        recall = payload["campaign_recall_contract"]
        proof = recall["proof_module_ids_by_claim"]
        key = next(iter(proof))
        proof["C99"] = proof.pop(key)
        payload["modules"][0]["lead_claim_id"] = "C99"
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", invalid_campaign)
    source = upload_asset(client, filename="formula-recall-mixed-errors.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert observed
    if complete_correction:
        assert result["status"] == "awaiting_confirmation", result.get("error_message")
        assert result["plan"]["payload"]["native_approval"]["document_sha256"]
        assert len(gateway.planner_calls) == 7
    else:
        assert result["status"] == "failed"
        assert not any(c.authoring_stage == "pair" for c in gateway.planner_calls)
        assert len(gateway.planner_calls) == 3
    assert not gateway.image_calls


def test_source_structure_facts_are_given_to_native_preflight(client):
    from app.xinghe_agent import _fake_plan

    source = upload_asset(client, filename="structured-fact-preflight.png")
    _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    story = next(
        c for c in _gateway(client).planner_calls if c.authoring_stage == "story"
    )
    payload = authoring.fake_phase(_fake_plan(story), story)
    assert not authoring.stage_errors(payload, story)
    intake = deepcopy(story.detail_intake)
    intake["user_facts"][0]["claim_kind"] = "STRUCTURE_COMPONENT"
    issues = authoring.stage_errors(payload, replace(story, detail_intake=intake))
    assert any(
        e["constraint"] == "native_formula_split"
        and "every structured" in e["message"]
        for e in issues
    )
    assert payload["formula_split_contract"]["evaluated_claims"] == []


@pytest.mark.parametrize("complete_correction", [False, True])
def test_unmapped_decision_jobs_are_localized_with_other_errors(
    client, monkeypatch, complete_correction
):
    gateway = _gateway(client)
    original = gateway.plan
    observed = []

    async def missing_job_map(value):
        result = await original(value)
        if value.authoring_stage != "story":
            return result
        payload = deepcopy(result.payload)
        errors = (value.authoring_context or {}).get("validation_errors")
        if errors:
            observed.extend(errors)
            mapping = next(
                e for e in errors
                if e["path"] == "/selling_point_depth_contract/claim_depth/0"
            )
            assert mapping["missing_jobs"]
            assert any(e["constraint"] == "enum" for e in errors)
            assert not any(e["path"] == "/" for e in errors)
            assert "not the whole campaign" in authoring.prompt(value)
            assert "multiple job rows" in authoring.prompt(value)
            assert set(payload) == {"modules", "selling_point_depth_contract"}
            if complete_correction:
                return result
        else:
            payload["modules"][0]["lead_claim_id"] = "C99"
        payload["selling_point_depth_contract"]["claim_depth"][0]["module_jobs"].pop()
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", missing_job_map)
    source = upload_asset(client, filename="missing-decision-job-map.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert observed
    if complete_correction:
        assert result["status"] == "awaiting_confirmation", result.get("error_message")
        assert result["plan"]["payload"]["native_approval"]["document_sha256"]
        assert len(gateway.planner_calls) == 7
    else:
        assert result["status"] == "failed"
        assert not any(c.authoring_stage == "pair" for c in gateway.planner_calls)
        assert len(gateway.planner_calls) == 3
    assert not gateway.image_calls


@pytest.mark.parametrize("stage", ["story", "pair"])
def test_sparse_correction_only_receipt_restores_original_request_prior(
    client, monkeypatch, tmp_path, stage
):
    import json
    import shutil

    from app.aurora_detail_runtime import detail_workspace
    from app.domestic_detail.planning_calls import NativePlanningStore
    from app.domestic_detail.plans import canonical_hash

    gateway = _gateway(client)
    original = gateway.plan

    async def one_missing_field(value):
        result = await original(value)
        if value.authoring_stage != stage or (
            stage == "pair" and value.authoring_target_ids != ("M01", "M02")
        ):
            return result
        if (value.authoring_context or {}).get("validation_errors"):
            return result
        payload = deepcopy(result.payload)
        if stage == "story":
            payload.pop("campaign_thesis")
        else:
            payload["modules"][0]["visible_copy"][0].pop("role")
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", one_missing_field)
    source = upload_asset(client, filename="correction-only-parent.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == "awaiting_confirmation", planned.get("error_message")
    initial = next(
        c for c in gateway.planner_calls
        if c.authoring_stage == stage
        and not (c.authoring_context or {}).get("validation_errors")
    )
    root = detail_workspace(job["id"]) / "planner-calls"
    packets = []
    for path in root.glob("*.request.json"):
        binding = json.loads(path.read_bytes())["binding"]
        semantics = binding["semantic_request"]["semantics"]
        if semantics.get("authoring_stage") == stage and (
            semantics.get("authoring_context") or {}
        ).get("validation_errors"):
            packets.append(binding)
    assert len(packets) == 1
    binding = packets[0]
    saved_context = binding["semantic_request"]["semantics"]["authoring_context"]
    only_correction = tmp_path / "only-correction-receipt"
    only_correction.mkdir()
    store = NativePlanningStore(root, binding).store
    originals = {}
    for path in root.glob(store.stem.name + ".*"):
        originals[path.name] = path.read_bytes()
        shutil.copyfile(path, only_correction / path.name)
    assert len(list(only_correction.glob("*.request.json"))) == 1
    current_binding = deepcopy(binding)
    current_binding["semantic_request"]["semantics"]["authoring_context"] = (
        deepcopy(initial.authoring_context)
    )
    recovered = authoring.recover_story(initial, only_correction, current_binding)
    assert recovered is not None
    assert not authoring.stage_errors(recovered["payload"], initial)
    assert recovered["external_request"] is False
    assert recovered["normalized_usage"]["reported"] is False
    proof = recovered["provider_envelope"]["authored_input_recovery"]
    assert proof["stage_valid"] is True and proof["provider_request_count"] == 0
    assert proof["source_reconstruction"]["prior_payload_sha256"] == canonical_hash(
        saved_context["prior_response"]
    )
    prior_payload = saved_context["prior_response"]
    if stage == "story":
        assert recovered["payload"]["modules"] == prior_payload["modules"]
    else:
        assert recovered["payload"]["modules"][1] == prior_payload["modules"][1]
    assert all((root / name).read_bytes() == raw for name, raw in originals.items())
    assert all(
        (only_correction / name).read_bytes() == raw for name, raw in originals.items()
    )
    changed = replace(
        initial, product_information=(*initial.product_information, "changed")
    )
    changed_binding = deepcopy(current_binding)
    changed_semantics = changed_binding["semantic_request"]["semantics"]
    changed_semantics["product_information"] = changed.product_information
    assert authoring.recover_story(changed, only_correction, changed_binding) is None
    assert not gateway.image_calls


@pytest.mark.parametrize("scope", ["whole_array", "whole_objects"])
def test_complete_pair_correction_prompt_matches_replacement_contract(
    client, monkeypatch, scope
):
    gateway = _gateway(client)
    original = gateway.plan
    observed = []

    async def invalid_pair_container(value):
        result = await original(value)
        if value.authoring_stage != "pair" or value.authoring_target_ids != (
            "M01", "M02"
        ):
            return result
        payload = deepcopy(result.payload)
        errors = (value.authoring_context or {}).get("validation_errors")
        if errors:
            observed.extend(errors)
            instruction = authoring.prompt(value)
            assert "COMPLETE two-screen pair JSON" in instruction
            assert "not the whole campaign or whole pair" not in instruction
            assert "Complete-stage authoring rules" in instruction
            assert "COMPLETE replacement subtree" in instruction
            schema = _planner_response_format(value)["json_schema"]["schema"]
            modules = schema["properties"]["modules"]
            assert modules["minItems"] == modules["maxItems"] == 2
            assert all(
                "proof" in slot["required"] and "typography" in slot["required"]
                for slot in modules["prefixItems"]
            )
            assert not authoring.errors_for(payload, _planner_response_format(value))
        elif scope == "whole_array":
            payload["modules"].append(deepcopy(payload["modules"][0]))
        else:
            payload["modules"] = [{"id": module["id"]} for module in payload["modules"]]
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", invalid_pair_container)
    source = upload_asset(client, filename="whole-pair-container-correction.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert observed
    assert result["status"] == "awaiting_confirmation", result.get("error_message")
    assert result["plan"]["payload"]["native_approval"]["document_sha256"]
    assert len(gateway.planner_calls) == 7
    assert not gateway.image_calls
