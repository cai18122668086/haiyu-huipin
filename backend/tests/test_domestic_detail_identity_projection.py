"""One original observation list feeds the native identity."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from app.domestic_detail.facts import DetailIntakeError, validate_intake
from app.domestic_detail.intake import (
    IDENTITY_PROJECTION,
    intake_response_schema,
    project_identity_observations,
)
from app.domestic_detail.plans import canonical_hash
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_intake import CONFIRMATION, SETTINGS, SOURCES, intake
from tests.test_domestic_detail_plans import planned_job


def wire():
    value = intake()
    value["brand_confirmation_required"] = False
    value["identity_features_source"] = IDENTITY_PROJECTION
    value["identity"].pop("invariant_features")
    for fact in value["observed_facts"]:
        fact.setdefault("atomic_key", "synthetic-" + fact["id"])
        fact.setdefault("atomicity", "ONE_INDEPENDENT_PROPOSITION")
    return value


def test_wire_projection_preserves_original_bytes_and_actual_observations():
    original = wire()
    before = deepcopy(original)
    refs = original["conflict_scan"]["scanned_source_refs"]
    schema = intake_response_schema(
        ["product-1"], [r for r in refs if r != "product-1"], wire_settings=SETTINGS
    )
    errors = list(Draft202012Validator(schema).iter_errors(original))
    assert not errors, [(str(error.path), error.message) for error in errors]
    native, receipt = project_identity_observations(original)
    result = validate_intake(
        native, settings=SETTINGS, sources=SOURCES, brandless_confirmation=CONFIRMATION
    )
    assert original == before
    assert result["identity"]["invariant_features"] == [
        row["text"].strip() for row in before["identity_observations"]
    ]
    assert receipt["source_payload_sha256"] == canonical_hash(before)
    assert receipt["observations_sha256"] == canonical_hash(
        before["identity_observations"]
    )


def test_legacy_disagreement_is_still_rejected_instead_of_silently_repaired():
    original = intake()
    original["identity"]["invariant_features"][0] = (
        "Different legacy product silhouette"
    )
    native, receipt = project_identity_observations(original)
    assert native == original and receipt is None
    with pytest.raises(DetailIntakeError) as error:
        validate_intake(
            native,
            settings=SETTINGS,
            sources=SOURCES,
            brandless_confirmation=CONFIRMATION,
        )
    assert error.value.code == "INTAKE_IDENTITY_OBSERVATION_MISMATCH"


@pytest.mark.parametrize(
    "tamper",
    ["source", "generic", "kind", "missing", "duplicated_identity", "unknown_protocol"],
)
def test_projection_never_supplies_missing_product_geometry_or_sources(tamper):
    original = wire()
    if tamper == "source":
        original["identity_observations"][0]["source_asset_id"] = "another-product"
    elif tamper == "generic":
        original["identity_observations"][0]["text"] = (
            "所有结构严格以用户商品参考图为准"
        )
    elif tamper == "kind":
        original["identity_observations"][0]["kind"] = "parts"
    elif tamper == "missing":
        original["identity_observations"].pop()
    elif tamper == "duplicated_identity":
        original["identity"]["invariant_features"] = ["guessed product"]
    else:
        original["identity_features_source"] = "UNKNOWN"
    with pytest.raises(DetailIntakeError):
        native, _ = project_identity_observations(original)
        validate_intake(
            native,
            settings=SETTINGS,
            sources=SOURCES,
            brandless_confirmation=CONFIRMATION,
        )


def test_public_planning_binds_the_original_wire_reply_to_native_identity(
    client, monkeypatch
):
    gateway = _gateway(client)
    original = gateway.plan
    replies = []

    async def reply(request):
        result = await original(request)
        if request.planning_profile == "aurora_domestic_intake":
            payload = deepcopy(result.payload)
            payload["identity_features_source"] = IDENTITY_PROJECTION
            payload["identity"].pop("invariant_features")
            replies.append(deepcopy(payload))
            return replace(
                result,
                payload=payload,
                audit_raw_content=json.dumps(payload, ensure_ascii=False),
            )
        return result

    monkeypatch.setattr(gateway, "plan", reply)
    planned = planned_job(client)
    assert planned["status"] == "awaiting_confirmation"
    value = planned["plan"]["payload"]["product_intake"]
    assert len(replies) == 1 and "invariant_features" not in replies[0]["identity"]
    assert value["identity_projection_receipt"][
        "source_payload_sha256"
    ] == canonical_hash(replies[0])
    assert value["identity"]["invariant_features"] == [
        row["text"].strip() for row in replies[0]["identity_observations"]
    ]
    assert gateway.image_calls == []


@pytest.mark.parametrize("state", ["absent", "null", "empty"])
def test_zero_reference_default_requires_no_uploaded_style_sources(state):
    from app.domestic_detail.intake import canonical_intake_wire

    value = wire()
    if state == "absent":
        value.pop("reference_materials")
    else:
        value["reference_materials"] = None if state == "null" else []
    before = deepcopy(value)
    canonical = canonical_intake_wire(value, has_style_sources=False)
    assert canonical["reference_materials"] == [] and value == before
    native, _ = project_identity_observations(canonical)
    assert (
        validate_intake(
            native,
            settings=SETTINGS,
            sources=SOURCES[:1],
            brandless_confirmation=CONFIRMATION,
        )["reference_materials"]
        == []
    )
    untouched = canonical_intake_wire(value, has_style_sources=True)
    assert untouched == value
    native, _ = project_identity_observations(untouched)
    with pytest.raises(DetailIntakeError):
        validate_intake(
            native,
            settings=SETTINGS,
            sources=SOURCES,
            brandless_confirmation=CONFIRMATION,
        )


def test_zero_reference_default_does_not_remove_phantom_or_legacy_reference_data():
    from app.domestic_detail.intake import canonical_intake_wire

    value = wire()
    assert canonical_intake_wire(value, has_style_sources=False) == value
    value = intake()
    value.pop("reference_materials")
    assert canonical_intake_wire(value, has_style_sources=False) == value


@pytest.mark.parametrize("tamper", [None, "missing", "empty", "null"])
def test_wire_declares_the_required_observed_fact_region(tamper):
    value = wire()
    if tamper == "missing":
        value["observed_facts"][0].pop("observed_region")
    elif tamper:
        value["observed_facts"][0]["observed_region"] = (
            "" if tamper == "empty" else None
        )
    schema = intake_response_schema(
        ["product-1"], ["F01", "F02"], wire_settings=SETTINGS
    )
    assert Draft202012Validator(schema).is_valid(value) is (tamper is None)


def test_wire_photo_only_route_requires_authored_operating_analysis():
    schema = intake_response_schema(["product-1"], [], wire_settings={})
    assert "operating_analysis" in schema["allOf"][0]["then"]["else"]["required"]
    schema = intake_response_schema(["product-1"], ["F01"], wire_settings=SETTINGS)
    assert "operating_analysis" not in schema["allOf"][0]["then"]["else"]["required"]


def test_wire_conflict_question_precedes_identity_and_cannot_lock_it_early():
    from tests.test_domestic_detail_intake import conflict_intake

    value = conflict_intake()
    value["brand_confirmation_required"] = False
    value["identity_features_source"] = IDENTITY_PROJECTION
    for fact in value["observed_facts"]:
        fact.setdefault("atomic_key", "synthetic-" + fact["id"])
        fact.setdefault("atomicity", "ONE_INDEPENDENT_PROPOSITION")
    before = deepcopy(value)
    schema = intake_response_schema(
        ["product-1"], ["F01", "F02"], wire_settings=SETTINGS
    )
    Draft202012Validator(schema).validate(value)
    native, receipt = project_identity_observations(value)
    assert receipt is None and "identity" not in native
    validated = validate_intake(native, settings=SETTINGS, sources=SOURCES)
    assert validated["conflict_scan"]["status"] == "CONFLICTS_FOUND"
    assert "identity" not in validated and value == before
    value["conflict_scan"].update(status="CLEAR", conflict_ids=[])
    value["source_conflicts"] = []
    assert not Draft202012Validator(schema).is_valid(value)
