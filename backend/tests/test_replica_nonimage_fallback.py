"""Known text-only relay rejection may use the configured image backup once."""

import asyncio
import base64
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from app.provider import ProviderOutcome
from app.xinghe_agent import (
    ImageEditRequest,
    SourceImage,
    TengliOpenAICompatibleGateway,
)

TEXT_REPLY = {"error": {"code": "upstream_text_reply", "type": "invalid_request_error"}}


class Transport:
    def __init__(self, response, backup_rejects=False):
        self.response = response
        self.backup_rejects = backup_rejects
        self.calls = []

    async def post_multipart(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1 or self.backup_rejects:
            return 400, self.response
        output = BytesIO()
        Image.new("RGB", (1024, 1024), "blue").save(output, "PNG")
        return 200, {
            "data": [{"b64_json": base64.b64encode(output.getvalue()).decode()}]
        }


def dispatch(response=TEXT_REPLY, *, single=False, backup=True, backup_rejects=False):
    transport = Transport(response, backup_rejects)
    gateway = TengliOpenAICompatibleGateway(
        api_key="primary-fixture",
        base_url="https://primary.example",
        planner_model="planner-fixture",
        image_model="image-primary-fixture",
        image_failover_api_key="backup-fixture" if backup else None,
        image_failover_base_url="https://backup.example" if backup else None,
        transport=transport,
    )
    data = b"unchanged-product-fixture"
    source = SourceImage(
        asset_id="product-original",
        group_key="replica",
        filename="product.png",
        content_type="image/png",
        sha256=sha256(data).hexdigest(),
        payload=data,
        source_role="product_reference",
    )
    result = asyncio.run(
        gateway.edit(
            ImageEditRequest(
                workspace_id="ws",
                job_id="job",
                job_item_id="item",
                attempt_id="attempt",
                page_number=1,
                group_key="replica",
                sources=(source,),
                prompt="Native approved image edit instruction",
                quality="high",
                idempotency_key="native-token-fixture",
                size="1024x1024",
                preserve_provider_artifact=True,
                single_dispatch=single,
            )
        )
    )
    return result, transport.calls, gateway.external_network_request_count


def test_known_text_only_reply_uses_image_backup_with_identical_evidence():
    result, calls, count = dispatch()
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert result.artifact is not None
    assert (result.artifact.width, result.artifact.height) == (1024, 1024)
    assert count == len(calls) == 2
    assert [call["url"] for call in calls] == [
        "https://primary.example/v1/images/edits",
        "https://backup.example/v1/images/edits",
    ]
    assert calls[0]["files"] == calls[1]["files"]
    first, second = (dict(call["fields"]) for call in calls)
    assert (
        first["prompt"] == second["prompt"] == "Native approved image edit instruction"
    )
    assert first["size"] == second["size"]
    assert (
        calls[0]["headers"]["Idempotency-Key"] == calls[1]["headers"]["Idempotency-Key"]
    )


@pytest.mark.parametrize(
    "response",
    [
        {"error": {"code": "invalid_image", "type": "invalid_request_error"}},
        {
            "error": {
                "code": "content_policy_violation",
                "type": "invalid_request_error",
            }
        },
        {"error": {"code": "upstream_text_reply"}},
        {"error": "upstream_text_reply"},
        {
            **TEXT_REPLY,
            "data": [{"url": "https://images.example/already-returned.png"}],
        },
        {**TEXT_REPLY, "output": [{"type": "image_generation_call"}]},
    ],
)
def test_other_bad_requests_or_possible_image_outputs_do_not_fail_over(response):
    result, calls, count = dispatch(response)
    assert result.outcome == ProviderOutcome.FAILED
    assert count == len(calls) == 1


@pytest.mark.parametrize("options", [{"single": True}, {"backup": False}])
def test_text_only_reply_respects_single_dispatch_and_disabled_backup(options):
    result, calls, count = dispatch(**options)
    assert result.outcome == ProviderOutcome.FAILED
    assert count == len(calls) == 1


def test_both_routes_reject_without_looping_or_claiming_a_deliverable():
    result, calls, count = dispatch(backup_rejects=True)
    assert result.outcome == ProviderOutcome.FAILED
    assert result.artifact is None
    assert count == len(calls) == 2


@pytest.mark.parametrize("error", [
    {"code": "content_policy_violation", "type": "image_generation_user_error"},
    {"code": "upstream_text_reply", "type": "invalid_request_error",
     "message": ("The image we created may violate our guardrails concerning "
                 "similarity to third-party content.")},
])
def test_content_refusal_never_uses_another_route_or_requests_retry(error):
    result, calls, count = dispatch({"error": error})
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "IMAGE_CONTENT_REJECTED"
    assert result.retryable is False and result.artifact is None
    assert count == len(calls) == 1
    assert "内容限制" in result.error_message
    assert result.raw_usage["metrics"]["provider_error"]["code"] == error["code"]
