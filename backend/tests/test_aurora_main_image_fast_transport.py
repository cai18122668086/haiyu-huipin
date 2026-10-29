"""Fast planning output budgets and truncation fencing on the real transport."""

import asyncio
import json

import pytest

from app.aurora_main_image_fast import PROFILE, response_schema
from app.xinghe_agent import PlannerRequest, TengliOpenAICompatibleGateway


class Transport:
    def __init__(self, status=200, finish_reason="stop"):
        self.status, self.finish_reason = status, finish_reason
        self.calls = []

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.status, {
            "id": "offline-fast-plan",
            "choices": [
                {
                    "finish_reason": self.finish_reason,
                    "message": {"content": '{"parseable_but_incomplete":true}'},
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
        job_id="job",
        group_key="group",
        sources=(),
        page_count=6,
        language="zh-CN",
        product_name="商品",
        product_information=(json.dumps({"response_schema": response_schema()}),),
        platform_code="TAOBAO_DOMESTIC_MAIN_IMAGE",
        size="1024x1024",
        idempotency_key="offline-fast-plan",
        planning_profile=PROFILE,
    )


@pytest.mark.parametrize(
    "model,expected_limit", [("deepseek-v4-pro", 32768), ("gpt-5.6-sol", 16000)]
)
def test_fast_transport_reserves_complete_output_and_fences_length(
    model, expected_limit
):
    transport = Transport(finish_reason="length")
    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-placeholder",
        base_url="https://offline.invalid",
        planner_model=model,
        image_model="image-test",
        transport=transport,
    )
    result = asyncio.run(gateway.plan(request()))
    assert len(transport.calls) == 1
    payload = transport.calls[0]["payload"]
    assert payload["model"] == model
    assert payload["max_completion_tokens"] == expected_limit
    assert payload["response_format"] == {"type": "json_object"}
    assert result.payload is None
    assert result.error_code == "PLANNER_OUTPUT_TRUNCATED"
    assert result.normalized_usage["output_token_limit"] == expected_limit


def test_fast_transport_never_failsover_to_replan():
    transport = Transport(status=503)
    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-placeholder",
        base_url="https://offline.invalid",
        planner_model="original-planner",
        image_model="original-image",
        failover_enabled=True,
        failover_api_key="offline-fallback",
        failover_base_url="https://fallback.invalid",
        failover_model="other-model",
        transport=transport,
    )
    result = asyncio.run(gateway.plan(request()))
    assert len(transport.calls) == 1
    assert result.error_code == "PLANNER_REQUEST_FAILED"
    assert result.payload is None
