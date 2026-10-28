"""Native rejection diagnostics preserve the cause and redact echoed credentials."""

import asyncio

import pytest

from app.domestic_detail.planner_dispatch import safe_native_provider_error
from app.xinghe_agent import TengliOpenAICompatibleGateway
from tests.test_domestic_detail_planning_calls import request


def test_native_error_redacts_every_field_and_bounds_untrusted_text():
    secret = "actual-provider-credential"
    payload = {
        "error": {
            "type": secret,
            "param": "response_format",
            "code": "invalid_json_schema",
            "message": "Invalid schema allOf "
            + secret
            + " sk-other_token Bearer private_token "
            + "x" * 1000,
        }
    }
    value = safe_native_provider_error(payload, [secret])
    assert (
        value["param"] == "response_format" and value["code"] == "invalid_json_schema"
    )
    assert "allOf" in value["message"] and len(value["message"]) <= 320
    assert (
        secret not in str(value)
        and "sk-other_token" not in str(value)
        and "private_token" not in str(value)
    )


@pytest.mark.parametrize(
    "payload",
    [{}, {"error": "unstructured"}, {"error": {"message": [], "code": False}}],
)
def test_unstructured_errors_are_not_reflected(payload):
    assert safe_native_provider_error(payload, []) == {}


@pytest.mark.parametrize("status", [400, 401, 403, 429, 503])
@pytest.mark.parametrize(
    "profile", ["aurora_domestic_intake", "xinghe_domestic_detail_4"]
)
def test_actual_gateway_keeps_native_http_cause_and_dispatch_provenance(
    status, profile
):
    class Transport:
        async def post_json(self, **kwargs):
            return status, {
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_json_schema",
                    "param": "response_format",
                    "message": "Unsupported allOf for credential private-actual",
                }
            }

    gateway = TengliOpenAICompatibleGateway(
        api_key="private-actual",
        base_url="https://offline.invalid/",
        planner_model="selected-model",
        image_model="synthetic",
        transport=Transport(),
    )
    result = asyncio.run(gateway.plan(request(profile)))
    assert result.http_status == status
    assert result.error_code == (
        "PLANNER_RATE_LIMITED" if status == 429 else "PLANNER_REQUEST_FAILED"
    )
    assert (
        "allOf" in result.error_message and "private-actual" not in result.error_message
    )
    metadata = result.provider_envelope
    assert metadata["native_provider_error"]["param"] == "response_format"
    if profile == "aurora_domestic_intake":
        assert metadata["native_planner_dispatch"]["outcome"] == (
            "outcome_unknown" if status == 503 else "rejected"
        )
        assert metadata["native_planner_dispatch"]["transport_attempts"] == 1
    else:
        assert "native_planner_dispatch" not in metadata


def test_provider_intake_root_is_plain_object_and_local_conditions_remain():
    from app.domestic_detail.facts import user_fact_ledger
    from app.domestic_detail.intake import intake_response_schema
    from app.xinghe_agent import _planner_prompt, _planner_response_format

    value = request("aurora_domestic_intake")
    original = intake_response_schema(
        [
            source.asset_id
            for source in value.sources
            if source.source_role == "product_reference"
        ],
        [fact["id"] for fact in user_fact_ledger(value.intake_settings or {})],
        wire_settings=value.intake_settings or {},
        style_asset_ids=[
            source.asset_id
            for source in value.sources
            if source.source_role == "style_reference"
        ],
    )
    wire = _planner_response_format(value)["json_schema"]["schema"]
    assert wire["type"] == "object"
    assert not ({"oneOf", "anyOf", "allOf", "enum", "const", "not"} & wire.keys())
    assert original.pop("allOf")
    assert wire == original
    assert '"allOf"' in _planner_prompt(value)
