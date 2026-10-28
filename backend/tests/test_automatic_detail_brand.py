"""Brand omissions, safe logo defaults and immutable recovery through the website."""

from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from app.domestic_detail import automatic_brand
from app.domestic_detail.facts import DetailIntakeError, validate_intake
from app.domestic_detail.intake import intake_response_schema
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_intake import SETTINGS, SOURCES, intake
from tests.test_domestic_detail_website_v1 import (
    explicit_synthetic_storage,  # noqa: F401
)

WEB = {
    **SETTINGS,
    "market_region": "domestic",
    "website_contract_version": "WEBSITE_DIRECT_V2",
}


def branded(value=None, source="product-1"):
    value = deepcopy(value or intake())
    value["identity"]["brand_logo_source"] = {
        "mode": "BRANDED",
        "image_id": source,
        "authorization_basis": "USER_SUPPLIED_CURRENT_PRODUCT",
        "readability": "READABLE",
        "usage": "FIRST_SCREEN_INDEPENDENT_LOGO",
    }
    value["observed_facts"].append(
        {
            **(value["observed_facts"] or intake()["observed_facts"])[0],
            "id": "O99",
            "atomic_key": "brand.logo_text",
            "atomicity": "ONE_INDEPENDENT_PROPOSITION",
            "text": "包装正面可读品牌标识为“路斯 LUSCIOUS”。",
            "evidence_ref": source,
            "observed_region": "包装正面左上部深蓝色品牌区",
            "named_elements": ["路斯", "LUSCIOUS"],
            "claim_risk_contract": {
                "evidence_level": "PACKAGING_ONLY",
                "allowed_phrases": ["路斯 LUSCIOUS", "包装可见品牌为路斯 LUSCIOUS"],
                "forbidden_phrases": ["行业第一"],
                "evidence_sources": [{"type": "PACKAGE", "ref": source}],
                "effect_implication_allowed": False,
                "qualification": "依据当前产品包装正面可读区域",
            },
        }
    )
    return value


def test_model_brand_schema_requires_both_visible_fields_and_rejects_blank():
    schema = intake_response_schema()["properties"]["identity"]["anyOf"][0][
        "properties"
    ]["brand_logo_source"]
    brand = branded()["identity"]["brand_logo_source"]
    assert not Draft202012Validator(schema).is_valid(brand)
    brand.update(observed_logo_text="路斯", observed_region="正面")
    assert Draft202012Validator(schema).is_valid(brand)
    brand["observed_logo_text"] = "   "
    assert not Draft202012Validator(schema).is_valid(brand)


def test_original_cat_brand_fact_projects_missing_fields_without_mutating_receipt():
    raw = branded()
    before = deepcopy(raw)
    value, receipt, repair = automatic_brand.project(raw, WEB, SOURCES)
    assert not repair and raw == before
    assert (
        value["identity"]["brand_logo_source"]["observed_logo_text"] == "路斯 LUSCIOUS"
    )
    assert receipt["projected_fact_id"] == "O99"
    assert receipt["decision_source"] == "website_default"
    validate_intake(value, settings=WEB, sources=SOURCES)


@pytest.mark.parametrize("source", ["product-1", "style-1"])
def test_unverifiable_or_style_logo_uses_no_independent_logo_without_user_declaration(
    source,
):
    raw = branded(source=source)
    raw["observed_facts"].pop()
    value, receipt, repair = automatic_brand.project(raw, WEB, SOURCES)
    assert not repair and receipt["product_brandless_claim"] is False
    assert (
        value["identity"]["brand_logo_source"]["brandless_evidence"]
        == automatic_brand.NO_LOGO_BASIS
    )
    validate_intake(value, settings=WEB, sources=SOURCES)
    with pytest.raises(DetailIntakeError):
        validate_intake(value, settings=SETTINGS, sources=SOURCES)


def test_unknown_brand_never_synthesizes_geometry():
    raw = intake()
    del raw["identity"]
    del raw["identity_observations"]
    raw["brand_confirmation_required"] = True
    value, receipt, repair = automatic_brand.project(raw, WEB, SOURCES)
    assert repair and "identity" not in value
    assert value["brand_confirmation_required"] is False
    assert receipt["product_brandless_claim"] is False


def test_brand_conflict_omission_preserves_specification_conflicts():
    raw = branded()
    extra = {
        **raw["observed_facts"][-1],
        "id": "O98",
        "text": "背景另一个包装带有其他品牌字样",
    }
    raw["observed_facts"].append(extra)
    brand_row = {
        "id": "C01",
        "topic": "品牌与包装SKU身份",
        "alternative_fact_ids": ["O99", "O98"],
        "why_material": "不同标识",
        "status": "UNRESOLVED",
    }
    capacity = {
        "id": "C02",
        "topic": "净含量",
        "alternative_fact_ids": ["F01", "F02"],
        "why_material": "容量不同",
        "status": "UNRESOLVED",
    }
    raw["source_conflicts"] = [brand_row, capacity]
    raw["conflict_scan"].update(status="CONFLICTS_FOUND", conflict_ids=["C01", "C02"])
    value, receipt, _ = automatic_brand.project(raw, WEB, SOURCES)
    assert value["source_conflicts"] == [capacity]
    assert value["conflict_scan"]["conflict_ids"] == ["C02"]
    assert len(receipt["omitted_brand_observations"]) == 2
    assert {row["id"] for row in value["observed_facts"]} == {"O01"}
    assert not automatic_brand.brand_conflict(
        {**capacity, "topic": "品牌及净含量"}, {"F01": {"source_class": "USER_FACT"}}
    )


def create(client, auto=False):
    source = upload_asset(client, filename="brand-product.png")
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "detail_pack",
            "intent": "submit",
            "display_name": "automatic brand fixture",
            "input": {
                "product_groups": [
                    {"group_key": "primary", "asset_ids": [source["id"]]}
                ]
            },
            "configuration": {
                "product_name": "Synthetic product",
                "selling_points": ["合成商品外观"],
                "platform_code": "tmall",
                "market_region": "domestic",
                "page_count": 8,
                "website_contract_version": "WEBSITE_DIRECT_V2",
                "auto_start_after_plan": auto,
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def current(client, job):
    return client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()


def test_unknown_brand_website_generates_without_brand_confirmation(
    client,
):
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    job = create(client, auto=True)
    dispatch_pending(client)
    result = current(client, job)
    assert result["status"] == "completed", result
    receipt = result["plan"]["payload"]["product_intake"]["automatic_brand_handling"]
    assert receipt["decision"] == "omit_independent_logo"
    assert receipt["product_brandless_claim"] is False
    assert len(_gateway(client).planner_calls) == 2
    assert len(_gateway(client).image_calls) == 4
    assert all("不得擦除" in request.prompt for request in _gateway(client).image_calls)


def test_failed_retry_recovers_original_brand_omission_without_repeating_intake(
    client, monkeypatch
):
    gateway = _gateway(client)
    original = gateway.plan
    project = automatic_brand.project

    async def missing_fields(request):
        result = await original(request)
        if request.planning_profile == "aurora_domestic_intake":
            result = replace(
                result, payload=branded(result.payload, request.sources[0].asset_id)
            )
        return result

    monkeypatch.setattr(gateway, "plan", missing_fields)
    monkeypatch.setattr(
        automatic_brand,
        "project",
        lambda payload, *args, **kwargs: (deepcopy(payload), None, False),
    )
    job = create(client)
    dispatch_pending(client)
    assert current(client, job)["status"] == "failed"
    monkeypatch.setattr(automatic_brand, "project", project)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert response.status_code == 201, response.text
    retry = response.json()
    dispatch_pending(client)
    result = current(client, retry)
    assert result["status"] == "awaiting_confirmation", result
    assert (
        sum(
            call.planning_profile == "aurora_domestic_intake"
            for call in gateway.planner_calls
        )
        == 1
    )
    assert (
        result["plan"]["payload"]["product_intake"]["identity"]["brand_logo_source"][
            "observed_logo_text"
        ]
        == "路斯 LUSCIOUS"
    )


def test_old_successful_brand_question_gets_one_separately_receipted_reanalysis(
    client, monkeypatch
):
    from app.aurora_detail_runtime import detail_workspace

    gateway = _gateway(client)
    original = gateway.plan

    async def old_question(request):
        result = await original(request)
        if (
            request.planning_profile == "aurora_domestic_intake"
            and request.intake_brand_repair is None
        ):
            value = deepcopy(result.payload)
            value.pop("identity", None)
            value.pop("identity_observations", None)
            value.update(
                brand_confirmation_required=True,
                brand_observation={
                    "source_asset_id": request.sources[0].asset_id,
                    "text": "原识别无法确认独立品牌标志",
                },
            )
            result = replace(result, payload=value)
        return result

    monkeypatch.setattr(gateway, "plan", old_question)
    job = create(client)
    dispatch_pending(client)
    result = current(client, job)
    assert result["stage"] == "awaiting_confirmation", result
    assert (
        len(list((detail_workspace(job["id"]) / "intake").glob("*.response.json"))) == 2
    )
    assert len(result["plan"]["payload"]["usage"]["planner"]) == 3
    assert len(gateway.planner_calls) == 3


@pytest.mark.parametrize("suffix", ["/intake/brand/continue", ""])
def test_legacy_brand_question_continuation_is_idempotent_without_fake_human_resolution(
    client, monkeypatch, suffix
):
    from app.models import JobRecord

    enabled = automatic_brand.enabled
    monkeypatch.setattr(automatic_brand, "enabled", lambda settings: False)
    job = create(client)
    dispatch_pending(client)
    pending = current(client, job)
    assert pending["stage"] == "intake_confirmation_required", pending
    monkeypatch.setattr(automatic_brand, "enabled", enabled)
    key = idem()
    body = {"expected_revision": pending["plan"]["revision"]}
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}{suffix}"
    assert client.post(url, headers=key, json=body).status_code == 202
    assert client.post(url, headers=key, json=body).status_code == 202
    with client.app.state.session_factory() as db:
        configuration = db.get(JobRecord, job["id"]).configuration
        assert "domestic_detail_intake_resolution" not in configuration
        assert (
            configuration["automatic_brand_continuation"]["decision_source"]
            == "website_default"
        )
    dispatch_pending(client)
    result = current(client, job)
    assert result["stage"] == "awaiting_confirmation", result
    assert len(_gateway(client).planner_calls) == 3


def test_unknown_intake_outcome_is_never_replayed_as_brand_repair(client, monkeypatch):
    gateway = _gateway(client)

    async def uncertain(request):
        gateway.planner_calls.append(request)
        raise TimeoutError("unknown request outcome")

    monkeypatch.setattr(gateway, "plan", uncertain)
    job = create(client)
    dispatch_pending(client)
    result = current(client, job)
    assert result["error_code"] == "INTAKE_OUTCOME_UNKNOWN", result
    assert len(gateway.planner_calls) == 1
