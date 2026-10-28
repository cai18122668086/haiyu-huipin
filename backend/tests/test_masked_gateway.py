import asyncio
import base64
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any
from urllib.error import URLError

import pytest
from PIL import Image

from app.masked_repair import (
    MaskedEditGatewayRequest,
    MaskedVisualReviewRequest,
)
from app.xinghe_agent import TengliOpenAICompatibleGateway


class CapturingTransport:
    def __init__(self) -> None:
        self.headers: Mapping[str, str] = {}
        self.fields: Sequence[tuple[str, str]] = ()
        self.files: Sequence[tuple[str, str, str, bytes]] = ()

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> tuple[int, dict[str, Any]]:
        raise AssertionError("Masked edit must not use the JSON endpoint.")

    async def post_multipart(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        fields: Sequence[tuple[str, str]],
        files: Sequence[tuple[str, str, str, bytes]],
        timeout_seconds: float,
    ) -> tuple[int, dict[str, Any]]:
        assert url.endswith("/v1/images/edits")
        assert timeout_seconds > 0
        self.headers = headers
        self.fields = fields
        self.files = files
        return 400, {"id": "request_bad_mask", "error": {"code": "invalid_mask"}}


def test_masked_content_refusal_is_terminal_even_when_relay_uses_503():
    class RefusalTransport(CapturingTransport):
        calls = 0

        async def post_multipart(self, **kwargs):
            self.calls += 1
            return 503, {"error": {"code": "content_policy_violation"}}

    transport = RefusalTransport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="primary-fixture", base_url="https://primary.example",
        planner_model="planner-fixture", image_model="image-fixture",
        image_failover_api_key="backup-fixture",
        image_failover_base_url="https://backup.example", transport=transport,
    )
    data = _png(Image.new("RGBA", (4, 4), (30, 40, 50, 255)))
    result = asyncio.run(gateway.edit_masked(MaskedEditGatewayRequest(
        source_png=data, api_mask_png=data, mask_png=data, prompt="background edit",
        quality="medium", width=4, height=4, idempotency_key="refusal-fixture",
    )))
    assert transport.calls == 1
    assert result.retryable is False
    assert result.error_code == "IMAGE_CONTENT_REJECTED"
    assert result.artifact is None


class ProviderCompatibilityTransport(CapturingTransport):
    def __init__(self, result_png: bytes) -> None:
        super().__init__()
        self.result_png = result_png

    async def post_multipart(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        fields: Sequence[tuple[str, str]],
        files: Sequence[tuple[str, str, str, bytes]],
        timeout_seconds: float,
    ) -> tuple[int, dict[str, Any]]:
        assert url.endswith("/v1/images/edits")
        assert timeout_seconds > 0
        self.headers = headers
        self.fields = fields
        self.files = files
        if [file[0] for file in files] != ["image", "image"]:
            return 400, {"error": {"code": "invalid_mask"}}
        return 200, {
            "id": "request_dual_image",
            "data": [
                {"b64_json": base64.b64encode(self.result_png).decode("ascii")}
            ],
        }


class ExplicitMaskTransport(ProviderCompatibilityTransport):
    async def post_multipart(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        fields: Sequence[tuple[str, str]],
        files: Sequence[tuple[str, str, str, bytes]],
        timeout_seconds: float,
    ) -> tuple[int, dict[str, Any]]:
        assert url.endswith("/v1/images/edits")
        assert timeout_seconds > 0
        self.headers = headers
        self.fields = fields
        self.files = files
        if [file[0] for file in files] != ["image", "mask"]:
            return 400, {"error": {"code": "invalid_mask"}}
        return 200, {
            "id": "request_explicit_mask",
            "data": [
                {"b64_json": base64.b64encode(self.result_png).decode("ascii")}
            ],
        }


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_masked_edit_uses_provider_compatible_identity_reference() -> None:
    transport = ProviderCompatibilityTransport(
        _png(Image.new("RGBA", (4, 4), (12, 34, 56, 255)))
    )
    gateway = TengliOpenAICompatibleGateway(
        api_key="test-placeholder-key",
        base_url="https://provider.invalid",
        planner_model="planner-test",
        image_model="configured-local-edit-model",
        transport=transport,
    )
    identity_pixel = (220, 15, 80, 255)
    source = Image.new("RGBA", (4, 4), (12, 34, 56, 255))
    source.putpixel((1, 1), identity_pixel)
    api_mask = Image.new("RGBA", source.size, (255, 255, 255, 255))
    for y in range(1, 3):
        for x in range(1, 3):
            api_mask.putpixel((x, y), (255, 255, 255, 0))
    request = MaskedEditGatewayRequest(
        source_png=_png(source),
        api_mask_png=_png(api_mask),
        prompt="master prompt with user instruction",
        quality="medium",
        width=4,
        height=4,
        idempotency_key="durable-attempt-key",
    )

    result = asyncio.run(gateway.edit_masked(request))

    fields = dict(transport.fields)
    # Commit 234ca70 made local edits use the configured image model instead of a
    # hardcoded literal; this assertion used to pin the old literal.
    assert fields["model"] == "configured-local-edit-model"
    assert fields["size"] == "4x4"
    assert fields["output_format"] == "png"
    assert fields["response_format"] == "b64_json"
    assert "input_fidelity" not in fields
    assert [file[0] for file in transport.files] == ["image", "image"]
    assert "transparent target" in fields["prompt"]
    assert fields["prompt"].endswith(request.prompt)
    assert [file[1] for file in transport.files] == [
        "editable.png",
        "source_reference.png",
    ]
    with Image.open(BytesIO(transport.files[0][3])) as editable_image:
        editable = editable_image.convert("RGBA")
    with Image.open(BytesIO(transport.files[1][3])) as reference_image:
        reference = reference_image.convert("RGBA")
    assert editable.getpixel((1, 1)) == (220, 15, 80, 0)
    assert reference.getpixel((1, 1)) == identity_pixel
    assert transport.headers["Idempotency-Key"] == "durable-attempt-key"
    assert result.http_status == 200
    assert result.retryable is False
    assert result.error_code is None
    assert result.artifact is not None


def test_masked_edit_uses_explicit_source_and_mask_parts_when_supplied() -> None:
    transport = ExplicitMaskTransport(
        _png(Image.new("RGBA", (4, 4), (12, 34, 56, 255)))
    )
    gateway = TengliOpenAICompatibleGateway(
        api_key="test-placeholder-key",
        base_url="https://provider.invalid",
        planner_model="planner-test",
        image_model="configured-local-edit-model",
        transport=transport,
    )
    source = _png(Image.new("RGBA", (4, 4), (12, 34, 56, 255)))
    mask = _png(Image.new("RGBA", (4, 4), (255, 255, 255, 255)))
    result = asyncio.run(
        gateway.edit_masked(
            MaskedEditGatewayRequest(
                source_png=source,
                api_mask_png=mask,
                mask_png=mask,
                prompt="master prompt",
                quality="medium",
                width=4,
                height=4,
                idempotency_key="explicit-mask-idem",
                execution_path="cropped",
            )
        )
    )
    assert result.outcome.value == "succeeded"
    assert [file[0] for file in transport.files] == ["image", "mask"]
    assert [file[1] for file in transport.files] == ["source.png", "mask_api.png"]
    assert transport.headers["Idempotency-Key"] == "explicit-mask-idem"


class VisualReviewTransport:
    def __init__(self) -> None:
        self.call: dict[str, Any] | None = None

    async def post_json(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.call = kwargs
        return (
            200,
            {
                "id": "review_request",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"visual_success":true,'
                                '"instruction_satisfied":true,'
                                '"identity_inside_selection_preserved":true,'
                                '"boundary_continuity":true,'
                                '"artifacts_absent":true,'
                                '"reason_codes":["passed"],'
                                '"summary":"The candidate passes review."}'
                            )
                        }
                    }
                ],
            },
        )

    async def post_multipart(self, **_: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("Visual review must use the JSON endpoint.")


def test_tengli_visual_review_sends_three_ordered_images_once() -> None:
    transport = VisualReviewTransport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="test-placeholder-key",
        base_url="https://provider.invalid",
        planner_model="planner-test",
        image_model="gpt-image-2",
        transport=transport,
    )
    result = asyncio.run(
        gateway.review_masked(
            MaskedVisualReviewRequest(
                source_png=b"source",
                authority_png=b"authority",
                candidate_png=b"candidate",
                instruction="replace the selected label",
                idempotency_key="visual-review-idem",
            )
        )
    )

    assert result.visual_success is True
    assert result.reason_codes == ("passed",)
    assert transport.call is not None
    payload = transport.call["payload"]
    assert payload["model"] == "planner-test"
    assert payload["response_format"] == {"type": "json_object"}
    assert transport.call["headers"]["Idempotency-Key"] == "visual-review-idem"
    content = payload["messages"][1]["content"]
    prompt = next(item["text"] for item in content if item["type"] == "text")
    assert "only when the user did not explicitly request those exact changes" in prompt
    assert "require all other identity evidence" in prompt
    images = [item for item in content if item["type"] == "image_url"]
    assert len(images) == 3
    assert images[0]["image_url"]["url"].endswith("c291cmNl")
    assert images[1]["image_url"]["url"].endswith("YXV0aG9yaXR5")
    assert images[2]["image_url"]["url"].endswith("Y2FuZGlkYXRl")


class ConfigurableVisualReviewTransport:
    def __init__(
        self,
        *,
        status: int = 200,
        response: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.response = response or {}
        self.error = error
        self.call_count = 0

    async def post_json(self, **_: Any) -> tuple[int, dict[str, Any]]:
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return self.status, self.response

    async def post_multipart(self, **_: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("Visual review must use the JSON endpoint.")


def _visual_review_gateway(
    transport: ConfigurableVisualReviewTransport,
) -> TengliOpenAICompatibleGateway:
    return TengliOpenAICompatibleGateway(
        api_key="test-placeholder-key",
        base_url="https://provider.invalid",
        planner_model="planner-test",
        image_model="gpt-image-2",
        transport=transport,
    )


def _visual_review_request() -> MaskedVisualReviewRequest:
    return MaskedVisualReviewRequest(
        source_png=b"source",
        authority_png=b"authority",
        candidate_png=b"candidate",
        instruction="replace the selected label",
        idempotency_key="visual-review-idem",
    )


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (429, "LOCAL_EDIT_VISUAL_REVIEW_RATE_LIMITED"),
        (503, "LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED"),
    ],
)
def test_tengli_visual_review_retries_transient_http_failures(
    status: int,
    error_code: str,
) -> None:
    transport = ConfigurableVisualReviewTransport(
        status=status,
        response={"id": f"review_http_{status}", "error": {"code": "temporary"}},
    )

    result = asyncio.run(
        _visual_review_gateway(transport).review_masked(_visual_review_request())
    )

    assert transport.call_count == 1
    assert result.visual_success is None
    assert result.provider_request_id == f"review_http_{status}"
    assert result.http_status == status
    assert result.error_code == error_code
    assert result.retryable is True


@pytest.mark.parametrize("status", [400, 401, 422])
def test_tengli_visual_review_does_not_retry_client_http_failures(
    status: int,
) -> None:
    transport = ConfigurableVisualReviewTransport(
        status=status,
        response={"id": f"review_http_{status}", "error": {"code": "invalid"}},
    )

    result = asyncio.run(
        _visual_review_gateway(transport).review_masked(_visual_review_request())
    )

    assert transport.call_count == 1
    assert result.visual_success is None
    assert result.provider_request_id == f"review_http_{status}"
    assert result.http_status == status
    assert result.error_code == "LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED"
    assert result.retryable is False


@pytest.mark.parametrize(
    "transport_error",
    [
        pytest.param(TimeoutError("timed out"), id="timeout"),
        pytest.param(OSError("connection reset"), id="os-error"),
        pytest.param(URLError("temporary DNS failure"), id="url-error"),
    ],
)
def test_tengli_visual_review_classifies_direct_transport_failures_as_retryable(
    transport_error: BaseException,
) -> None:
    transport = ConfigurableVisualReviewTransport(error=transport_error)

    result = asyncio.run(
        _visual_review_gateway(transport).review_masked(_visual_review_request())
    )

    assert transport.call_count == 1
    assert result.visual_success is None
    assert result.provider_request_id is None
    assert result.http_status is None
    assert result.error_code == "LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED"
    assert result.retryable is True


def test_tengli_visual_review_rejects_malformed_success_payload_without_retry() -> None:
    transport = ConfigurableVisualReviewTransport(
        status=200,
        response={"id": "review_malformed", "choices": []},
    )

    result = asyncio.run(
        _visual_review_gateway(transport).review_masked(_visual_review_request())
    )

    assert transport.call_count == 1
    assert result.visual_success is None
    assert result.provider_request_id == "review_malformed"
    assert result.error_code == "LOCAL_EDIT_VISUAL_REVIEW_INVALID"
    assert result.retryable is False


def test_tengli_visual_review_returns_valid_rejection_without_retry() -> None:
    transport = ConfigurableVisualReviewTransport(
        status=200,
        response={
            "id": "review_rejected",
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"visual_success":false,'
                            '"instruction_satisfied":false,'
                            '"identity_inside_selection_preserved":true,'
                            '"boundary_continuity":true,'
                            '"artifacts_absent":true,'
                            '"reason_codes":["instruction_not_satisfied"],'
                            '"summary":"The requested change is not visible."}'
                        )
                    }
                }
            ],
        },
    )

    result = asyncio.run(
        _visual_review_gateway(transport).review_masked(_visual_review_request())
    )

    assert transport.call_count == 1
    assert result.visual_success is False
    assert result.provider_request_id == "review_rejected"
    assert result.http_status == 200
    assert result.error_code is None
    assert result.retryable is False
    assert result.reason_codes == ("instruction_not_satisfied",)
