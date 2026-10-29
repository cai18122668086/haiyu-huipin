"""Fast detail must inherit full-output transport safeguards, using no network."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from app import detail_fast
from app.xinghe_agent import TengliOpenAICompatibleGateway, _fake_plan
from tests.test_api import dispatch_pending, idem, upload_asset
from tests.test_detail_fast_contract import request
from tests.test_detail_fast_integration import BASE, request_payload
from tests.test_domestic_detail_execution import _gateway


class EnvelopeTransport:
    """Records the actual production adapter payload, without a socket call."""

    def __init__(
        self, content: str = '{"accepted":true}', *, finish="stop", status=200
    ):
        self.content = content
        self.finish = finish
        self.status = status
        self.calls: list[dict[str, Any]] = []

    async def post_json(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.status >= 400:
            return self.status, {"error": {"message": "Synthetic route refusal"}}
        return 200, {
            "id": "detail-fast-transport-receipt",
            "choices": [
                {"finish_reason": self.finish, "message": {"content": self.content}}
            ],
            "usage": {
                "prompt_tokens": 1600,
                "completion_tokens": 32000 if self.finish == "length" else 4000,
                "total_tokens": 33600 if self.finish == "length" else 5600,
                "completion_tokens_details": {"reasoning_tokens": 1000},
            },
        }


def gateway(transport, model="deepseek-v4.1-flash-1017"):
    return TengliOpenAICompatibleGateway(
        api_key="offline-placeholder",
        base_url="https://offline.invalid",
        planner_model=model,
        image_model="gpt-image-2",
        failover_enabled=True,
        failover_api_key="offline-fallback",
        failover_base_url="https://fallback.offline.invalid",
        failover_model=model,
        transport=transport,
    )


def fast_request():
    return replace(
        request(), planning_profile=detail_fast.PROFILE, domestic_protocol=True
    )


@pytest.mark.parametrize("model", ["deepseek-v4.1-flash-1017", "configured-planner"])
def test_fast_budget_includes_reasoning_without_lowering_model_or_full_schema(model):
    transport = EnvelopeTransport()
    adapter = gateway(transport, model)
    req = fast_request()
    result = asyncio.run(adapter.plan(req))
    assert result.error_code is None and result.payload == {"accepted": True}
    assert len(transport.calls) == adapter.external_network_request_count == 1
    sent = transport.calls[0]["payload"]
    assert sent["model"] == model
    assert sent["messages"][0]["content"] == req.system_prompt
    assert "thinking" not in sent
    schema = detail_fast.strategy_schema(req.page_count)
    body = sent["messages"][1]["content"][0]["text"]
    assert json.dumps(schema, ensure_ascii=False) in body
    if model.startswith("deepseek"):
        assert sent["max_completion_tokens"] >= 32768
        assert sent["response_format"] == {"type": "json_object"}
        assert sent["reasoning_effort"] == "low"
    else:
        assert sent["max_completion_tokens"] == 24000
        assert "reasoning_effort" not in sent
        assert sent["response_format"]["json_schema"]["schema"] == schema


def test_fast_refused_primary_route_never_starts_a_second_planner_call():
    transport = EnvelopeTransport(status=429)
    adapter = gateway(transport)
    result = asyncio.run(adapter.plan(fast_request()))
    assert result.error_code == "PLANNER_RATE_LIMITED"
    assert len(transport.calls) == adapter.external_network_request_count == 1
    assert transport.calls[0]["url"].startswith("https://offline.invalid/")


@pytest.mark.parametrize(
    "content", ["", '{"accepted":true}', '{"plan":' + "\n\t" * 100]
)
def test_fast_length_termination_blocks_even_complete_json_and_preserves_receipt(
    content,
):
    transport = EnvelopeTransport(content, finish="length")
    adapter = gateway(transport)
    result = asyncio.run(adapter.plan(fast_request()))
    assert result.payload is None
    assert result.error_code == "PLANNER_OUTPUT_TRUNCATED"
    assert result.provider_request_id == "detail-fast-transport-receipt"
    assert result.normalized_usage["finish_reason"] == "length"
    assert result.normalized_usage["output_token_limit"] >= 32000
    assert result.normalized_usage["response_format_type"] == "json_object"
    assert result.normalized_usage["output_characters"] == len(content)
    assert result.raw_usage["completion_tokens_details"]["reasoning_tokens"] == 1000
    assert len(transport.calls) == adapter.external_network_request_count == 1


def test_generic_detail_default_transport_contract_remains_unchanged():
    transport = EnvelopeTransport()
    adapter = gateway(transport, "configured-planner")
    result = asyncio.run(adapter.plan(request()))
    assert result.error_code is None
    sent = transport.calls[0]["payload"]
    assert sent["response_format"] == {"type": "json_object"}
    assert "max_completion_tokens" not in sent
    assert "reasoning_effort" not in sent
    assert "thinking" not in sent


@pytest.mark.parametrize("failure", ["invalid_primary", "truncated_complete_json"])
def test_site_records_one_failed_planning_receipt_and_zero_images(
    client, monkeypatch, failure
):
    fake_gateway = _gateway(client)
    captured_requests = []
    transport = EnvelopeTransport()
    adapter = gateway(transport)

    async def plan(req):
        captured_requests.append(req)
        authored = _fake_plan(req)
        if failure == "invalid_primary":
            # A formally complete strategy must not authorize a missing source.
            authored["plan"]["product_identity_physics_lock"][
                "primary_product_source"
            ] = 99
        transport.content = json.dumps(authored, ensure_ascii=False)
        transport.finish = "length" if failure == "truncated_complete_json" else "stop"
        return await adapter.plan(req)

    monkeypatch.setattr(fake_gateway, "plan", plan)
    source = upload_asset(client)
    response = client.post(
        BASE + "/jobs", headers=idem(), json=request_payload(source["id"], 6)
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{job_id}").json()
    assert final["status"] == "failed", final
    assert final["stage"] == "planning_failed"
    expected = (
        "DETAIL_FAST_IDENTITY_SOURCE_INVALID"
        if failure == "invalid_primary"
        else "PLANNER_OUTPUT_TRUNCATED"
    )
    assert final["error_code"] == expected
    assert len(captured_requests) == len(transport.calls) == 1
    assert captured_requests[0].planning_profile == detail_fast.PROFILE
    assert fake_gateway.image_calls == []
    payload = final["plan"]["payload"]
    assert payload["state"] == "failed"
    assert payload["planner_request_count"] == 1
    assert payload["image_request_count"] == 0
    assert len(payload["usage"]["planner"]) == 1
    usage = payload["usage"]["planner"][0]
    assert usage["provider_request_id_presence"] == "present"
    assert usage["raw"]["completion_tokens_details"]["reasoning_tokens"] == 1000
    assert usage["reported"] is True
    assert final["api_request_count"] == 1
    assert final["plan"]["validation_status"] == "invalid"
    assert all(item["status"] == "failed" for item in final["items"])
    # An unchanged second dispatch neither replans nor starts image generation.
    dispatch_pending(client)
    assert len(transport.calls) == 1 and fake_gateway.image_calls == []
