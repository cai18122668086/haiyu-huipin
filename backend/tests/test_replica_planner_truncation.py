"""Real gateway envelopes must never turn a truncated response into a plan."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.xinghe_agent import (
    PlannerCallResult,
    PlannerRequest,
    TengliOpenAICompatibleGateway,
)
from tests.test_replica_compact import SyntheticGateway
from tests.test_replica_partial_delivery import create_case


def request():
    return PlannerRequest(
        workspace_id="ws_test",
        job_id="job_truncation",
        group_key="product",
        sources=(),
        page_count=1,
        language="zh-CN",
        product_name="test",
        product_information=(
            json.dumps(
                {
                    "stage": "product",
                    "context": {},
                    "response_schema": {
                        "type": "object",
                        "properties": {"products": {"type": "array"}},
                    },
                }
            ),
        ),
        platform_code="tmall",
        size="1024x1024",
        idempotency_key="original-key",
        planning_profile="replica_exact_compact",
        system_prompt="Keep the full original contract.",
    )


@pytest.mark.parametrize(
    "model,limit", [("deepseek-v4.1-flash-1017", 32768), ("configured-planner", 16000)]
)
def test_budget_and_effort_are_explicit_without_changing_the_complete_contract(
    model, limit
):
    sent = []

    async def post_json(**kwargs):
        sent.append(kwargs)
        return 200, {
            "id": "receipt",
            "choices": [
                {"finish_reason": "stop", "message": {"content": '{"products": []}'}}
            ],
        }

    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-test",
        base_url="https://offline.invalid",
        planner_model=model,
        image_model="offline-image",
        transport=SimpleNamespace(post_json=post_json),
    )
    req = request()
    result = asyncio.run(gateway.plan(req))
    assert not result.error_code and result.payload == {"products": []}
    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert payload["max_completion_tokens"] == limit
    assert payload["messages"][0]["content"] == req.system_prompt
    assert "thinking" not in payload
    if model.startswith("deepseek"):
        assert payload["response_format"] == {"type": "json_object"}
        text = payload["messages"][1]["content"][0]["text"]
        original = json.loads(text[text.index('{"stage":') :])
        assert original == json.loads(req.product_information[0])
        assert "ENTIRE response_schema" in payload["messages"][1]["content"][-1]["text"]
        assert payload["reasoning_effort"] == "low"
        assert sent[0]["headers"]["Idempotency-Key"] != req.idempotency_key
    else:
        assert (
            payload["response_format"]["json_schema"]["schema"]
            == json.loads(req.product_information[0])["response_schema"]
        )
        assert "reasoning_effort" not in payload


@pytest.mark.parametrize(
    "content", ["", '{"products": []}', '{"products"' + "\n\t" * 100]
)
def test_length_is_rejected_even_if_partial_body_looks_like_valid_json(content):
    async def post_json(**kwargs):
        return 200, {
            "id": "truncated-receipt",
            "choices": [{"finish_reason": "length", "message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 32768},
        }

    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-test",
        base_url="https://offline.invalid",
        planner_model="deepseek-v4.1-flash-1017",
        image_model="offline-image",
        transport=SimpleNamespace(post_json=post_json),
    )
    result = asyncio.run(gateway.plan(request()))
    assert result.payload is None
    assert result.error_code == "PLANNER_OUTPUT_TRUNCATED"
    assert result.provider_request_id == "truncated-receipt"
    assert result.normalized_usage["finish_reason"] == "length"
    assert result.normalized_usage["output_token_limit"] == 32768
    assert result.normalized_usage["output_characters"] == len(content)
    assert result.normalized_usage["trailing_whitespace_characters"] == len(
        content
    ) - len(content.rstrip())
    assert result.normalized_usage["response_format_type"] == "json_object"
    assert gateway.external_network_request_count == 1


def test_http_explains_truncation_preserves_checkpoint_and_never_generates(
    client, monkeypatch, tmp_path
):
    gateway = SyntheticGateway()
    original = gateway.plan

    async def truncate_product(req):
        envelope = json.loads(req.product_information[0])
        if envelope["stage"] != "product":
            return await original(req)
        return PlannerCallResult(
            payload=None,
            provider_request_id="real-truncated-request",
            raw_usage=None,
            normalized_usage={
                "reported": True,
                "input_tokens": 100,
                "output_tokens": 16000,
                "error_code": "PLANNER_OUTPUT_TRUNCATED",
                "finish_reason": "length",
                "output_token_limit": 16000,
                "provider_request_id": "real-truncated-request",
                "http_status": 200,
            },
            api_latency_seconds=0,
            actual_cost_micros=None,
            error_code="PLANNER_OUTPUT_TRUNCATED",
            http_status=200,
        )

    gateway.plan = truncate_product
    _, route, did, jid = create_case(
        client, monkeypatch, tmp_path, gateway_override=gateway
    )
    doc = client.get(route + "/documents/" + did).json()
    assert jid is None and doc["status"] == "failed"
    assert doc["payload"]["error_code"] == "REPLICA_PLANNER_OUTPUT_TRUNCATED"
    assert "16000" in doc["payload"]["error"] and "length" in doc["payload"]["error"]
    assert (
        doc["payload"]["analysis_error_details"]["provider_request_id"]
        == "real-truncated-request"
    )
    assert not gateway.image_requests


@pytest.mark.parametrize("content", ["", "\n\t" * 100, "[]", '{"products"'])
def test_invalid_json_content_preserves_receipt_and_names_schema_failure(content):
    async def post_json(**kwargs):
        return 200, {
            "id": "invalid-json-receipt",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        }

    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-test",
        base_url="https://offline.invalid",
        planner_model="deepseek-v4.1-flash-1017",
        image_model="offline-image",
        transport=SimpleNamespace(post_json=post_json),
    )
    result = asyncio.run(gateway.plan(request()))
    assert result.payload is None and result.error_code == "PLANNER_SCHEMA_INVALID"
    assert result.provider_request_id == "invalid-json-receipt"
    assert result.normalized_usage["provider_request_id"] == "invalid-json-receipt"
    assert result.normalized_usage["output_characters"] == len(content)
    assert result.normalized_usage["parse_trace"]


def test_second_format_attempt_gets_all_errors_and_complete_response():
    from dataclasses import replace

    sent = []
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "units": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"enum": ["headline", "subtitle"]},
                        "observation": {"type": "string"},
                    },
                    "required": ["role", "observation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["units"],
    }
    original = {
        "units": [
            {"role": "secondary", "observation": "Full first screen observation"},
            {"role": "secondary", "observation": "Full second screen observation"},
        ]
    }

    async def post_json(**kwargs):
        sent.append(kwargs)
        body = (
            original
            if len(sent) == 1
            else {"units": [{**row, "role": "subtitle"} for row in original["units"]]}
        )
        return 200, {
            "id": "format-receipt",
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(body)}}
            ],
        }

    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-test",
        base_url="https://offline.invalid",
        planner_model="deepseek-v4.1-flash-1017",
        image_model="offline-image",
        transport=SimpleNamespace(post_json=post_json),
    )
    envelope = {
        "stage": "layout",
        "context": {},
        "response_schema": schema,
        "validation_error": "",
    }
    first = replace(request(), product_information=(json.dumps(envelope),))
    first_result = asyncio.run(gateway.plan(first))
    assert first_result.payload == original
    envelope["validation_error"] = "Response field role violates enum"
    second = replace(
        first, product_information=(json.dumps(envelope),), idempotency_key="format-1"
    )
    result = asyncio.run(gateway.plan(second))
    assert len(sent) == gateway.external_network_request_count == 2
    assert len(result.payload["units"]) == 2
    correction = sent[1]["payload"]["messages"][-1]["content"]
    feedback = json.loads(correction[correction.index('{"schema_errors"') :])
    assert len(feedback["schema_errors"]) == 2
    assert feedback["previous_complete_response"] == original
    assert "'secondary' is not one of" in correction
    assert (
        sent[1]["payload"]["response_format"] == sent[0]["payload"]["response_format"]
    )
    assert sent[1]["payload"]["messages"][0] == sent[0]["payload"]["messages"][0]
    text = sent[1]["payload"]["messages"][1]["content"][0]["text"]
    retried_envelope = json.loads(text[text.index('{"stage"') :])
    assert retried_envelope["response_schema"] == schema
    assert retried_envelope["context"] == envelope["context"]
    assert original["units"][0]["role"] == "secondary"


def test_format_feedback_never_crosses_job_or_contract_bindings():
    from dataclasses import replace

    from app.replica_format_feedback import ReplicaFormatFeedback

    helper = ReplicaFormatFeedback()
    req = request()
    envelope = json.loads(req.product_information[0])
    envelope["response_schema"]["additionalProperties"] = False
    req = replace(req, product_information=(json.dumps(envelope),))
    helper.remember(req, {"products": [], "unexpected": True})
    envelope["validation_error"] = "additionalProperties"
    retry = replace(req, product_information=(json.dumps(envelope),))
    assert helper.prompt(retry)
    assert helper.prompt(replace(retry, job_id="unrelated-job")) is None
    assert helper.prompt(replace(retry, system_prompt="Different instruction")) is None
    assert helper.prompt(req) is None


def test_http_keeps_actual_schema_error_instead_of_only_a_generic_message(
    client, monkeypatch, tmp_path
):
    gateway = SyntheticGateway()
    original = gateway.plan

    async def invalid_role(req):
        envelope = json.loads(req.product_information[0])
        result = await original(req)
        if envelope["stage"] == "layout":
            result.payload["units"][0]["text_regions"][0]["role"] = "secondary"
        return result

    gateway.plan = invalid_role
    _, route, did, jid = create_case(
        client, monkeypatch, tmp_path, gateway_override=gateway
    )
    doc = client.get(route + "/documents/" + did).json()
    assert jid is None and doc["status"] == "failed"
    assert doc["payload"]["error_code"] == "REPLICA_MODEL_RESPONSE_INVALID"
    assert "role" in doc["payload"]["error"] and "enum" in doc["payload"]["error"]
    assert doc["payload"]["analysis_error_details"]["message"]
    assert not gateway.image_requests
