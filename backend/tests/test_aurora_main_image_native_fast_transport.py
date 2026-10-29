"""No-network transport acceptance for native-fast identity planning."""

import asyncio
import json

import pytest
from jsonschema import Draft202012Validator

from app.aurora_main_image_native_fast import PROFILE
from app.aurora_main_image_planner import _IDENTITY_SYSTEM_PROMPT
from app.xinghe_agent import (
    PlannerRequest,
    TengliOpenAICompatibleGateway,
    _aurora_identity_json_schema,
)


def identity():
    return {
        "request_id": "native-transport-job",
        "product_form": "Exact original cup",
        "identity_summary": "Original product shape and label from visible evidence.",
        "evidence": [{"evidence_id": "original-asset", "view_tags": ["front"]}],
        "must_preserve": ["Exact silhouette and label layout"],
        "must_not_change": ["Do not mix packaging or invent product structures"],
    }


class Transport:
    def __init__(self, *, status=200, finish_reason="stop"):
        self.status, self.finish_reason = status, finish_reason
        self.calls = []

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.status, {
            "id": "offline-native-identity-receipt",
            "choices": [
                {
                    "finish_reason": self.finish_reason,
                    "message": {"content": json.dumps(identity())},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "completion_tokens_details": {"reasoning_tokens": 150},
                "total_tokens": 300,
            },
        }


def request():
    return PlannerRequest(
        workspace_id="workspace",
        job_id="native-transport-job",
        group_key="group",
        sources=(),
        page_count=6,
        language="zh-CN",
        product_name="商品",
        product_information=("Only original user facts authorize visible claims.",),
        platform_code="TAOBAO_DOMESTIC_MAIN_IMAGE",
        size="1024x1024",
        idempotency_key="offline-native-identity",
        planning_profile=PROFILE,
        system_prompt=_IDENTITY_SYSTEM_PROMPT,
    )


def gateway(transport, model="original-configured-model"):
    return TengliOpenAICompatibleGateway(
        api_key="offline-placeholder",
        base_url="https://primary.invalid",
        planner_model=model,
        image_model="original-image-model",
        transport=transport,
        failover_enabled=True,
        failover_api_key="offline-fallback-placeholder",
        failover_base_url="https://fallback.invalid",
        failover_model="other-model",
    )


@pytest.mark.parametrize(
    "model,limit", [("deepseek-v4-pro", 32768), ("original-configured-model", 16000)]
)
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_native_identity_complete_json_respects_transport_termination(
    model, limit, finish_reason
):
    assert Draft202012Validator(_aurora_identity_json_schema()).is_valid(identity())
    transport = Transport(finish_reason=finish_reason)
    provider = gateway(transport, model)
    result = asyncio.run(provider.plan(request()))
    assert len(transport.calls) == provider.external_network_request_count == 1
    call = transport.calls[0]
    assert call["url"].startswith("https://primary.invalid/")
    payload = call["payload"]
    assert payload["model"] == model
    assert payload["max_completion_tokens"] == limit
    assert payload["messages"][0]["content"] == _IDENTITY_SYSTEM_PROMPT
    texts = [
        part["text"]
        for part in payload["messages"][1]["content"]
        if part["type"] == "text"
    ]
    marker = "Return the complete identity response_schema:\n"
    original_schema = json.loads(
        next(text.split(marker, 1)[1] for text in texts if marker in text)
    )
    assert original_schema == _aurora_identity_json_schema()
    if model.startswith("deepseek"):
        assert payload["response_format"] == {"type": "json_object"}
    else:
        assert (
            payload["response_format"]["json_schema"]["schema"]
            == _aurora_identity_json_schema()
        )
    if finish_reason == "length":
        assert result.payload is None
        assert result.error_code == "PLANNER_OUTPUT_TRUNCATED"
        assert result.normalized_usage["finish_reason"] == "length"
        assert result.normalized_usage["output_token_limit"] == limit
        assert result.provider_request_id == "offline-native-identity-receipt"
    else:
        assert result.error_code is None
        assert result.payload == identity()
    assert result.actual_cost_micros is None


@pytest.mark.parametrize("status", [429, 503])
def test_native_identity_has_one_route_even_with_failover_enabled(status):
    transport = Transport(status=status)
    provider = gateway(transport)
    result = asyncio.run(provider.plan(request()))
    assert len(transport.calls) == provider.external_network_request_count == 1
    assert transport.calls[0]["url"].startswith("https://primary.invalid/")
    assert transport.calls[0]["payload"]["model"] == "original-configured-model"
    assert result.payload is None
    assert result.error_code == (
        "PLANNER_RATE_LIMITED" if status == 429 else "PLANNER_REQUEST_FAILED"
    )
