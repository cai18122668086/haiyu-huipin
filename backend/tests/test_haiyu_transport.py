import asyncio
import base64
import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.haiyu_transport import HaiyuTransport, _public_url, pack_references
from app.main import create_app
from app.masked_repair import MaskedEditGatewayRequest
from app.provider import ProviderOutcome
from app.xinghe_agent import (
    ImageEditRequest,
    SourceImage,
    TengliOpenAICompatibleGateway,
    UrllibOpenAITransport,
    XingheAgentError,
    build_tengli_gateway,
)

MODELS = (
    "image-2.5-flare",
    "image-2.5-sunburst",
    "image-2.5",
    "image-1k-2k-4k-th",
    "image-2",
)


def png(color="blue"):
    buffer = BytesIO()
    Image.new("RGB", (1024, 1024), color).save(buffer, "PNG")
    return buffer.getvalue()


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def post_multipart(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def image_response():
    return 200, {"data": [{"b64_json": base64.b64encode(png()).decode()}]}


def dispatch(transport, *, masked=False, single=False):
    gateway = TengliOpenAICompatibleGateway(
        api_key="test-server-secret",
        base_url="https://api.haiyushuke.com",
        planner_model="deepseek-v4.1-flash-1017",
        image_model=MODELS[0],
        image_fallback_models=(*MODELS[1:], MODELS[0], MODELS[-1]),
        transport=transport,
    )
    if masked:
        return asyncio.run(
            gateway.edit_masked(
                MaskedEditGatewayRequest(
                    source_png=png(),
                    api_mask_png=png("white"),
                    mask_png=png("black"),
                    prompt="Make the selected part white",
                    quality="high",
                    width=1024,
                    height=1024,
                    idempotency_key="masked-fixture",
                )
            )
        )
    return asyncio.run(
        gateway.edit(
            ImageEditRequest(
                workspace_id="ws",
                job_id="job",
                job_item_id="item",
                attempt_id="attempt",
                page_number=1,
                group_key="product",
                sources=(
                    SourceImage(
                        asset_id="source",
                        group_key="product",
                        filename="product.png",
                        content_type="image/png",
                        sha256="test",
                        payload=png(),
                    ),
                ),
                prompt="Make the background white",
                quality="high",
                idempotency_key="edit-fixture",
                size="1024x1024",
                single_dispatch=single,
            )
        )
    )


@pytest.mark.parametrize("masked", [False, True])
def test_five_models_are_tried_in_order_and_stop_on_success(masked):
    transport = Transport(
        [(503, {"error": {"code": "unavailable"}})] * 4 + [image_response()]
    )
    result = dispatch(transport, masked=masked)
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert [dict(call["fields"])["model"] for call in transport.calls] == list(MODELS)
    assert all(call["files"] == transport.calls[0]["files"] for call in transport.calls)


@pytest.mark.parametrize("error", ["invalid_image", "content_policy_violation"])
def test_invalid_input_and_policy_errors_do_not_switch_models(error):
    transport = Transport([(400, {"error": {"code": error}})])
    assert dispatch(transport).outcome == ProviderOutcome.FAILED
    assert len(transport.calls) == 1


def test_model_not_found_switches_but_single_dispatch_stays_single():
    transport = Transport(
        [(404, {"error": {"code": "model_not_found"}}), image_response()]
    )
    assert dispatch(transport).outcome == ProviderOutcome.SUCCEEDED
    transport = Transport([(503, {"error": {"code": "unavailable"}})])
    assert dispatch(transport, single=True).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert len(transport.calls) == 1


def test_all_channels_fail_without_looping():
    transport = Transport([(503, {"error": {"code": "unavailable"}})] * 5)
    result = dispatch(transport)
    assert result.outcome == ProviderOutcome.FAILED
    assert result.artifact is None
    assert len(transport.calls) == 5


def test_five_model_attempt_receipts_survive_successful_fallback():
    from app.provider_diagnostics import retained_metrics

    transport = Transport(
        [(503, {"error": {"code": "unavailable"}})] * 4 + [image_response()]
    )
    result = dispatch(transport)
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert result.raw_usage["network_requests"] == 5
    trace = retained_metrics(result.raw_usage)["provider_attempts"]
    assert [row["model"] for row in trace] == list(MODELS)
    assert [row["http_status"] for row in trace] == [503, 503, 503, 503, 200]


@pytest.mark.parametrize("masked", [False, True])
@pytest.mark.parametrize("status", [400, 503])
def test_wrapped_content_refusal_never_switches_to_another_haiyu_model(masked, status):
    transport = Transport(
        [
            (
                status,
                {
                    "error": {
                        "code": "upstream_text_reply",
                        "type": "invalid_request_error",
                        "message": (
                            "Request rejected due to similarity to third-party content"
                        ),
                    }
                },
            )
        ]
    )
    result = dispatch(transport, masked=masked)
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "IMAGE_CONTENT_REJECTED"
    assert result.retryable is False
    assert len(transport.calls) == 1


def test_default_browser_configuration_endpoints_are_closed(settings):
    settings.environment = "development"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/development/provider-settings").status_code == 404
        response = client.post(
            "/api/v1/development/provider-settings",
            json={
                "api_key": "test-provider-key",
                "base_url": "https://provider.example",
                "image_model": "image-test",
                "vision_model": "vision-test",
            },
        )
        assert response.status_code == 404
        assert client.app.state.settings.task_runner == "mock"


def test_server_settings_build_the_exact_chain_without_network_requests():
    settings = Settings(
        _env_file=None,
        tengli_api_key="test-key",
        image_base_url="https://api.haiyushuke.com",
        image_model=MODELS[0],
        image_fallback_models=MODELS[1:],
        analysis_model="deepseek-v4.1-flash-1017",
    )
    gateway = build_tengli_gateway(settings)
    assert isinstance(gateway.transport, HaiyuTransport)
    assert [route.image_model for route in gateway._image_routes()] == list(MODELS)
    assert gateway.planner_model == "deepseek-v4.1-flash-1017"
    assert gateway.external_network_request_count == 0


def test_reference_packing_preserves_every_reference_and_mask_guidance():
    files, note = pack_references(
        [
            ("image", "product.png", "image/png", png()),
            ("image", "style.png", "image/png", png("red")),
            ("mask", "mask.png", "image/png", png("black")),
        ]
    )
    assert len(files) == 1 and len(files[0][3]) < 10 * 1024 * 1024
    assert "Reference 1: product.png" in note and "Reference 2: style.png" in note
    assert "Reference 3: EDIT MAP" in note
    assert "never reproduce this grid" in note


@pytest.mark.parametrize("target_size", ["1464x600", "1086x1448", "800x800"])
def test_lossless_reference_sheet_tracks_target_aspect_without_changing_sources(
    target_size,
):
    source = png("#ca1693")
    files, _ = pack_references(
        [
            ("image", "product.png", "image/png", source),
            ("image", "style.png", "image/png", png("#8193ca")),
        ],
        target_size=target_size,
    )
    assert files[0][2] == "image/png"
    width, height = (int(v) for v in target_size.split("x"))
    with Image.open(BytesIO(files[0][3])) as sheet:
        assert sheet.format == "PNG"
        assert abs((sheet.width / sheet.height) / (width / height) - 1) < 0.001
        # Solid source colors survive exactly, without JPEG glyph-edge damage.
        colors = {color for _, color in sheet.getcolors(sheet.width * sheet.height)}
        assert (202, 22, 147) in colors and (129, 147, 202) in colors
    assert source == png("#ca1693")


class PollingTransport(HaiyuTransport):
    def __init__(self, delegate, states, *, journal_dir=None):
        super().__init__(
            delegate, poll_interval=0, task_timeout=1, journal_dir=journal_dir
        )
        self.states = iter(states)
        self.polls = []

    async def get_task(self, url, headers, timeout):
        self.polls.append(url)
        return next(self.states)

    async def image_data(self, payload, timeout):
        # URL conversion itself is checked separately with a mocked downloader.
        return payload


def request(
    transport, model=MODELS[0], prompt="Edit the product", *, reconcile_only=False
):
    return asyncio.run(
        transport.post_multipart(
            url="https://api.haiyushuke.com/v1/images/edits",
            headers={
                "Authorization": "Bearer test-key",
                "Idempotency-Key": "same-task",
            },
            fields=[
                ("model", model),
                ("prompt", prompt),
                ("response_format", "b64_json"),
            ],
            files=[("image", "product.png", "image/png", png())],
            timeout_seconds=30,
            reconcile_only=reconcile_only,
        )
    )


def test_202_polls_original_task_and_never_creates_duplicate():
    delegate = Transport([(202, {"id": "original-task", "status": "queued"})])
    transport = PollingTransport(
        delegate,
        [
            (200, {"status": "running"}),
            (200, {"status": "succeeded", "result": image_response()[1]}),
        ],
    )
    status, result = request(transport)
    assert status == 200 and result["data"]
    assert len(delegate.calls) == 1
    assert (
        transport.polls
        == ["https://api.haiyushuke.com/v1/images/tasks/original-task"] * 2
    )
    wire = dict(delegate.calls[0]["fields"])
    assert wire["async"] == "true" and wire["response_format"] == "url"


def test_unknown_task_outcomes_do_not_allow_automatic_repurchase():
    delegate = Transport([(202, {"id": "original-task", "status": "queued"})])
    transport = PollingTransport(
        delegate, [(200, {"status": "failed", "error": {"code": "settlement_pending"}})]
    )
    with pytest.raises(XingheAgentError) as caught:
        request(transport)
    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"
    assert caught.value.provider_error["code"] == "settlement_pending"
    assert len(delegate.calls) == 1


def test_uncertain_initial_response_does_not_switch_models():
    delegate = Transport([(503, {"error": {"code": "outcome_unknown"}})])
    with pytest.raises(XingheAgentError) as caught:
        request(PollingTransport(delegate, []))
    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"
    assert caught.value.provider_error["code"] == "outcome_unknown"
    assert len(delegate.calls) == 1


def test_async_policy_rejection_stays_a_terminal_bad_request():
    delegate = Transport([(202, {"id": "task", "status": "queued"})])
    transport = PollingTransport(
        delegate,
        [(200, {"status": "failed", "error": {"code": "content_policy_violation"}})],
    )
    status, _ = request(transport)
    assert status == 400
    assert len(delegate.calls) == 1


def test_each_fallback_model_has_a_stable_distinct_idempotency_key():
    delegate = Transport([image_response(), image_response(), image_response()])
    transport = PollingTransport(delegate, [])
    request(transport, MODELS[0])
    request(transport, MODELS[1])
    request(transport, MODELS[0])
    keys = [call["headers"]["Idempotency-Key"] for call in delegate.calls]
    assert keys[0] == keys[2] and keys[0] != keys[1]


@pytest.mark.parametrize("enabled", [False, True])
def test_size_forwarding_is_an_operator_switch(enabled):
    delegate = Transport([image_response()])
    transport = HaiyuTransport(delegate, send_size=enabled)
    asyncio.run(
        transport.post_multipart(
            url="https://api.haiyushuke.com/v1/images/edits",
            headers={"Idempotency-Key": "size-fixture"},
            fields=[
                ("model", MODELS[0]),
                ("prompt", "Keep the product"),
                ("size", "1024x1024"),
            ],
            files=[("image", "product.png", "image/png", png())],
            timeout_seconds=30,
        )
    )
    wire = dict(delegate.calls[0]["fields"])
    assert (wire.get("size") == "1024x1024") is enabled


def test_url_result_download_is_converted_to_inline_bytes(monkeypatch):
    monkeypatch.setattr(
        "app.haiyu_transport.download_result", lambda url, timeout, allowed_hosts: png()
    )
    transport = HaiyuTransport(None)
    result = asyncio.run(
        transport.image_data({"data": [{"url": "https://cdn.example/image.png"}]}, 30)
    )
    assert base64.b64decode(result["data"][0]["b64_json"]) == png()


@pytest.mark.parametrize("masked", [False, True])
def test_failed_artifact_download_retains_original_task_and_never_regenerates(
    monkeypatch, masked
):
    def reject_download(*args):
        raise XingheAgentError("PROVIDER_RESPONSE_INVALID", "Untrusted result address")

    monkeypatch.setattr("app.haiyu_transport.download_result", reject_download)
    delegate = Transport(
        [
            (
                200,
                {
                    "request_id": "original-task-id",
                    "data": [{"url": "https://cdn.example/result.png"}],
                },
            )
        ]
    )
    result = dispatch(HaiyuTransport(delegate), masked=masked)
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert result.error_code == "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"
    assert result.provider_request_id == "original-task-id"
    assert len(delegate.calls) == 1


def test_provider_response_header_request_id_is_retained(monkeypatch):
    response = BytesIO(json.dumps({"data": []}).encode())
    response.status = 200
    response.headers = {"X-Request-ID": "original-provider-trace-id"}
    monkeypatch.setattr(
        UrllibOpenAITransport, "_open", staticmethod(lambda request, timeout: response)
    )
    status, payload = UrllibOpenAITransport._send(
        "https://api.example/test", {}, b"{}", 5
    )
    assert status == 200
    assert payload["request_id"] == "original-provider-trace-id"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "192.168.0.1", "::1"])
def test_artifact_download_rejects_private_addresses_even_for_allowed_host(
    monkeypatch, address
):
    monkeypatch.setattr(
        "app.haiyu_transport.socket.getaddrinfo",
        lambda *args: [(0, 0, 0, "", (address, 443))],
    )
    monkeypatch.setattr(
        "app.haiyu_transport.getproxies", lambda: {"https": "http://127.0.0.1:7897"}
    )
    with pytest.raises(XingheAgentError):
        _public_url("https://cdn.example/image.png", ("cdn.example",))


def test_fake_dns_requires_exact_host_and_configured_proxy(monkeypatch):
    monkeypatch.setattr(
        "app.haiyu_transport.socket.getaddrinfo",
        lambda *args: [(0, 0, 0, "", ("198.18.0.4", 443))],
    )
    monkeypatch.setattr(
        "app.haiyu_transport.getproxies", lambda: {"https": "http://127.0.0.1:7897"}
    )
    with pytest.raises(XingheAgentError):
        _public_url("https://untrusted.example/image.png", ("cdn.example",))
    _public_url("https://cdn.example/image.png", ("cdn.example",))
    monkeypatch.setattr("app.haiyu_transport.getproxies", lambda: {})
    with pytest.raises(XingheAgentError):
        _public_url("https://cdn.example/image.png", ("cdn.example",))


def test_transient_result_download_retries_the_same_url_only(monkeypatch):
    reads = []

    def read(url, *_args):
        reads.append(url)
        if len(reads) < 3:
            raise TimeoutError("temporary CDN read timeout")
        return png()

    monkeypatch.setattr("app.haiyu_transport.download_result", read)
    value = asyncio.run(
        HaiyuTransport(None).image_data(
            {
                "data": [{"url": "https://cdn.example/original.png"}],
                "request_id": "original-task",
            },
            1,
        )
    )
    assert base64.b64decode(value["data"][0]["b64_json"]) == png()
    assert reads == ["https://cdn.example/original.png"] * 3


class JournalTransport(HaiyuTransport):
    async def get_task(self, url, headers, timeout):
        return 200, {
            "status": "succeeded",
            "result": {
                "data": [{"url": "https://cdn.example/original.png"}],
            },
        }


def test_native_references_preserve_order_bytes_and_explicit_controls():
    delegate = Transport([image_response()])
    files = [
        ("image", "product.png", "image/png", png()),
        ("image", "background.png", "image/png", png("pink")),
    ]
    fields = [
        ("model", MODELS[0]),
        ("prompt", "Use image 2 as background"),
        ("quality", "high"),
        ("input_fidelity", "high"),
        ("size", "1464x600"),
        ("n", "1"),
    ]
    transport = HaiyuTransport(delegate, send_size=True, native_references=True)
    status, _ = asyncio.run(
        transport.post_multipart(
            url="https://api.haiyushuke.com/v1/images/edits",
            headers={"Idempotency-Key": "native-test"},
            fields=fields,
            files=files,
            timeout_seconds=30,
        )
    )
    assert status == 200 and len(delegate.calls) == 1
    sent = delegate.calls[0]
    assert sent["files"] == files
    values = dict(sent["fields"])
    assert values["quality"] == values["input_fidelity"] == "high"
    assert values["size"] == "1464x600"
    assert "REFERENCE SHEET" not in values["prompt"]


def test_native_reference_limit_rejects_before_purchase():
    delegate = Transport([])
    transport = HaiyuTransport(delegate, native_references=True)
    status, _ = asyncio.run(
        transport.post_multipart(
            url="https://api.haiyushuke.com/v1/images/edits",
            headers={},
            fields=[("model", MODELS[0]), ("prompt", "Preserve")],
            files=[("image", f"source-{i}.png", "image/png", png()) for i in range(17)],
            timeout_seconds=30,
        )
    )
    assert status == 400 and delegate.calls == []


def test_native_mask_guide_keeps_the_original_canvas_and_exact_authority():
    original = png("white")
    mask = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    mask.putpixel((10, 10), (255, 255, 255, 0))
    buffer = BytesIO()
    mask.save(buffer, "PNG")
    delegate = Transport([image_response()])
    transport = HaiyuTransport(delegate, native_references=True)
    status, _ = asyncio.run(
        transport.post_multipart(
            url="https://api.haiyushuke.com/v1/images/edits",
            headers={},
            fields=[("model", MODELS[0]), ("prompt", "Edit only selected wall")],
            files=[
                ("image", "original.png", "image/png", original),
                ("mask", "authority.png", "image/png", buffer.getvalue()),
            ],
            timeout_seconds=30,
        )
    )
    assert status == 200
    sent = delegate.calls[0]
    assert len(sent["files"]) == 2 and sent["files"][0][3] == original
    with Image.open(BytesIO(sent["files"][1][3])) as guide:
        assert guide.size == (1024, 1024)
        assert guide.getpixel((20, 20)) == (255, 255, 255)
        assert guide.getpixel((10, 10)) != (255, 255, 255)
    assert "EDIT MAP ONLY" in dict(sent["fields"])["prompt"]


@pytest.mark.parametrize("old_encoder", ["jpeg", "png"])
@pytest.mark.parametrize("old_size", [False, True])
def test_legacy_packing_receipt_resumes_without_changing_or_resubmitting(
    tmp_path,
    monkeypatch,
    old_encoder,
    old_size,
):
    from app import haiyu_transport

    monkeypatch.setattr("app.haiyu_transport.download_result", lambda *_args: png())
    original_packer = haiyu_transport.pack_references
    files = [
        ("image", "product.png", "image/png", png()),
        ("image", "style.png", "image/png", png("red")),
    ]
    delegate = Transport([(202, {"id": "historical-task", "status": "queued"})])
    args = dict(
        url="https://api.haiyushuke.com/v1/images/edits",
        headers={"Idempotency-Key": "historical-key"},
        fields=[
            ("model", MODELS[0]),
            ("prompt", "Original instruction"),
            ("size", "1464x600"),
            ("quality", "high"),
            ("input_fidelity", "high"),
        ],
        files=files,
        timeout_seconds=30,
    )
    monkeypatch.setattr(
        haiyu_transport,
        "pack_references",
        (lambda value, **_: haiyu_transport._legacy_pack_references(value))
        if old_encoder == "jpeg"
        else original_packer,
    )
    first = JournalTransport(
        delegate, journal_dir=tmp_path, poll_interval=0, send_size=old_size
    )
    asyncio.run(first.post_multipart(**args))
    monkeypatch.setattr(haiyu_transport, "pack_references", original_packer)
    resumed_delegate = Transport([])
    resumed = JournalTransport(
        resumed_delegate,
        journal_dir=tmp_path,
        poll_interval=0,
        send_size=True,
        native_references=True,
    )
    status, result = asyncio.run(resumed.post_multipart(**args, reconcile_only=True))
    assert status == 200 and result["haiyu_task_id"] == "historical-task"
    assert len(delegate.calls) == 1 and resumed_delegate.calls == []
    with pytest.raises(XingheAgentError, match="parameters changed"):
        asyncio.run(
            resumed.post_multipart(
                **{**args, "fields": [("model", MODELS[0]), ("prompt", "Changed")]},
                reconcile_only=True,
            )
        )
    assert resumed_delegate.calls == []


def test_restart_recovers_accepted_task_without_another_paid_submit(
    tmp_path, monkeypatch
):
    def blocked(*_args):
        raise XingheAgentError("PROVIDER_RESPONSE_INVALID", "Non-public CDN address")

    monkeypatch.setattr("app.haiyu_transport.download_result", blocked)
    delegate = Transport([(202, {"id": "accepted-task", "status": "queued"})])
    first = JournalTransport(delegate, journal_dir=tmp_path, poll_interval=0)
    with pytest.raises(XingheAgentError) as caught:
        request(first)
    assert caught.value.provider_request_id == "accepted-task"
    assert len(delegate.calls) == 1
    checkpoint = next(tmp_path.glob("*.json")).read_text()
    assert "accepted-task" in checkpoint
    assert "test-key" not in checkpoint and "Edit the product" not in checkpoint
    monkeypatch.setattr("app.haiyu_transport.download_result", lambda *_args: png())
    resumed_delegate = Transport([])
    resumed = JournalTransport(resumed_delegate, journal_dir=tmp_path, poll_interval=0)
    status, payload = request(resumed)
    assert status == 200
    assert payload["haiyu_task_id"] == "accepted-task"
    assert base64.b64decode(payload["data"][0]["b64_json"]) == png()
    assert resumed_delegate.calls == []


def test_unknown_dispatch_journal_never_resubmits_after_restart(tmp_path):
    class Interrupted:
        async def post_multipart(self, **kwargs):
            raise XingheAgentError("PROVIDER_TRANSPORT_FAILED", "Connection lost")

    with pytest.raises(XingheAgentError):
        request(HaiyuTransport(Interrupted(), journal_dir=tmp_path))
    delegate = Transport([])
    with pytest.raises(XingheAgentError, match="reconciliation"):
        request(HaiyuTransport(delegate, journal_dir=tmp_path))
    assert delegate.calls == []


def test_journal_refuses_changed_inputs_under_the_same_key(tmp_path, monkeypatch):
    monkeypatch.setattr("app.haiyu_transport.download_result", lambda *_args: png())
    delegate = Transport([(202, {"id": "original-task", "status": "queued"})])
    transport = JournalTransport(delegate, journal_dir=tmp_path, poll_interval=0)
    request(transport)
    with pytest.raises(XingheAgentError, match="parameters changed"):
        request(transport, prompt="Different authorized instructions")
    assert len(delegate.calls) == 1


@pytest.mark.parametrize("masked", [False, True])
@pytest.mark.parametrize("single", [False, True])
def test_async_terminal_failure_does_not_purchase_a_fallback_image(masked, single):
    delegate = Transport([(202, {"id": "failed-task", "status": "queued"})])
    transport = PollingTransport(
        delegate,
        [
            (
                200,
                {
                    "status": "failed",
                    "error": {"code": "render_failed", "message": "No output"},
                },
            )
        ],
    )
    result = dispatch(transport, masked=masked, single=single)
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "IMAGE_ASYNC_TASK_FAILED"
    assert result.provider_request_id == "failed-task"
    assert result.retryable is False
    assert result.raw_usage["metrics"]["provider_error"]["code"] == "render_failed"
    assert len(delegate.calls) == 1


@pytest.mark.parametrize("masked", [False, True])
def test_failed_label_cannot_hide_upstream_reconciliation_instruction(masked):
    delegate = Transport([(202, {"id": "original-task", "status": "queued"})])
    transport = PollingTransport(
        delegate,
        [
            (
                200,
                {
                    "status": "failed",
                    "error": {
                        "code": "agent3_failed",
                        "message": "Image result unavailable; "
                        "do not create a replacement automatically",
                    },
                },
            )
        ],
    )
    result = dispatch(transport, masked=masked, single=False)
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert result.provider_request_id == "original-task"
    assert result.retryable is False
    assert len(delegate.calls) == 1


@pytest.mark.parametrize("masked", [False, True])
def test_download_failure_keeps_original_task_and_exact_cdn_diagnostics(
    monkeypatch, masked
):
    def blocked(*_args):
        raise XingheAgentError("PROVIDER_RESPONSE_INVALID", "Non-public CDN address")

    monkeypatch.setattr("app.haiyu_transport.download_result", blocked)
    delegate = Transport(
        [
            (
                200,
                {
                    "request_id": "original-task",
                    "data": [{"url": "https://cdn.example/result.png"}],
                },
            )
        ]
    )
    result = dispatch(HaiyuTransport(delegate), masked=masked)
    assert result.provider_request_id == "original-task"
    error = result.raw_usage["metrics"]["provider_error"]
    assert error["code"] == "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"
    assert error["param"] == "cdn.example"
    assert "Non-public" in error["message"]
    assert result.raw_usage["network_requests"] == 1
    assert len(delegate.calls) == 1


def test_masked_five_model_fallback_keeps_all_actual_route_receipts():
    delegate = Transport(
        [(503, {"error": {"code": "unavailable"}})] * 4 + [image_response()]
    )
    result = dispatch(delegate, masked=True)
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert result.raw_usage["network_requests"] == 5
    assert [
        x["model"] for x in result.raw_usage["metrics"]["provider_attempts"]
    ] == list(MODELS)


def test_download_error_retains_queryable_task_id_instead_of_http_trace(monkeypatch):
    def blocked(*_args):
        raise XingheAgentError("PROVIDER_RESPONSE_INVALID", "Untrusted CDN address")

    monkeypatch.setattr("app.haiyu_transport.download_result", blocked)
    with pytest.raises(XingheAgentError) as caught:
        asyncio.run(
            HaiyuTransport(None).image_data(
                {
                    "request_id": "http-trace-not-a-task",
                    "haiyu_task_id": "queryable-original-task",
                    "data": [{"url": "https://cdn.example/result.png"}],
                },
                1,
            )
        )
    assert caught.value.provider_request_id == "queryable-original-task"


def test_error_response_with_a_task_receipt_does_not_switch_models():
    delegate = Transport(
        [
            (
                503,
                {
                    "id": "possibly-accepted-task",
                    "error": {"code": "upstream_timeout"},
                },
            )
        ]
    )
    result = dispatch(HaiyuTransport(delegate))
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert result.provider_request_id == "possibly-accepted-task"
    assert len(delegate.calls) == 1


@pytest.mark.parametrize("task_state", ["failed", "reconciliation"])
def test_terminal_gateway_receipt_persists_and_only_requeries_original(
    tmp_path, task_state
):
    task = {
        "id": "original-task",
        "object": "image.task",
        "status": task_state,
        "error": {
            "code": "agent3_failed",
            "message": (
                "Image result unavailable; do not create a replacement automatically"
                if task_state == "failed"
                else "Result unavailable"
            ),
        },
    }
    delegate = Transport([(502, task)])
    transport = PollingTransport(delegate, [], journal_dir=tmp_path)
    with pytest.raises(XingheAgentError) as original:
        request(transport)
    assert original.value.provider_request_id == task["id"]
    assert original.value.provider_error["code"] == "agent3_failed"
    assert transport.polls == []
    assert (
        transport.accepted_task(
            "https://api.haiyushuke.com/v1/images/edits", "same-task", MODELS[0]
        )
        == task["id"]
    )

    resumed = PollingTransport(delegate, [(200, task)], journal_dir=tmp_path)
    with pytest.raises(XingheAgentError) as rechecked:
        request(resumed, reconcile_only=True)
    assert rechecked.value.code == original.value.code == "PROVIDER_RESPONSE_INVALID"
    assert rechecked.value.provider_request_id == task["id"]
    assert rechecked.value.provider_error == original.value.provider_error
    assert resumed.polls == ["https://api.haiyushuke.com/v1/images/tasks/original-task"]
    _, image = image_response()
    resolved = PollingTransport(
        delegate,
        [(200, {"id": task["id"], "status": "succeeded", "result": image})],
        journal_dir=tmp_path,
    )
    status, response = request(resolved, reconcile_only=True)
    assert status == 200 and response["haiyu_task_id"] == task["id"]
    assert response["data"] == image["data"]
    assert resolved.polls == resumed.polls
    assert len(delegate.calls) == 1


@pytest.mark.parametrize(
    "evidence", [{"id": "unverified"}, {"task_id": "unverified"}, {"data": [{}]}]
)
def test_ambiguous_error_response_cannot_poll_or_resubmit_after_restart(
    tmp_path, evidence
):
    delegate = Transport([(502, {**evidence, "error": {"code": "model_not_found"}})])
    for _attempt in range(2):
        transport = PollingTransport(delegate, [], journal_dir=tmp_path)
        with pytest.raises(XingheAgentError) as error:
            request(transport)
        assert error.value.code == "PROVIDER_RESPONSE_INVALID"
        assert error.value.provider_request_id == (
            evidence.get("id") or evidence.get("task_id")
        )
        assert error.value.provider_error["code"] == "model_not_found"
        assert transport.polls == []
    assert len(delegate.calls) == 1


def test_legacy_rate_limit_journal_with_task_id_never_resubmits(tmp_path):
    delegate = Transport(
        [
            (429, {"id": "unverified", "error": {"code": "rate_limit_exceeded"}}),
        ]
    )
    transport = PollingTransport(delegate, [], journal_dir=tmp_path)
    with pytest.raises(XingheAgentError):
        request(transport)
    journal = next(tmp_path.glob("*.json"))
    record = json.loads(journal.read_text(encoding="utf8"))
    record.update(state="rejected", retry_after="0")
    journal.write_text(json.dumps(record), encoding="utf8")
    resumed = PollingTransport(delegate, [], journal_dir=tmp_path)
    with pytest.raises(XingheAgentError) as caught:
        request(resumed)
    assert caught.value.provider_request_id == "unverified"
    assert resumed.polls == []
    assert len(delegate.calls) == 1


def test_receipt_disk_failure_preserves_accepted_task_and_prevents_resubmit(tmp_path):
    class BrokenJournal(HaiyuTransport):
        @staticmethod
        def _save_journal(path, record):
            raise OSError("disk unavailable")

        async def get_task(self, url, headers, timeout):
            raise XingheAgentError(
                "PROVIDER_RESPONSE_INVALID",
                "Original task query unavailable",
                provider_request_id="accepted-task",
            )

    delegate = Transport([(202, {"id": "accepted-task", "status": "queued"})])
    with pytest.raises(XingheAgentError) as caught:
        request(BrokenJournal(delegate, journal_dir=tmp_path, poll_interval=0))
    assert caught.value.provider_request_id == "accepted-task"
    with pytest.raises(XingheAgentError, match="reconciliation"):
        request(HaiyuTransport(delegate, journal_dir=tmp_path))
    assert len(delegate.calls) == 1
