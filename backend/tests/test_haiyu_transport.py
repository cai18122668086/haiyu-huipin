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


class PollingTransport(HaiyuTransport):
    def __init__(self, delegate, states):
        super().__init__(delegate, poll_interval=0, task_timeout=1)
        self.states = iter(states)
        self.polls = []

    async def get_task(self, url, headers, timeout):
        self.polls.append(url)
        return next(self.states)

    async def image_data(self, payload, timeout):
        # URL conversion itself is checked separately with a mocked downloader.
        return payload


def request(transport, model=MODELS[0], prompt="Edit the product"):
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
    with pytest.raises(XingheAgentError, match="reconciliation"):
        request(transport)
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
def test_async_terminal_failure_does_not_purchase_a_fallback_image(masked):
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
    result = dispatch(transport, masked=masked)
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "IMAGE_ASYNC_TASK_FAILED"
    assert result.provider_request_id == "failed-task"
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


def test_receipt_disk_failure_preserves_accepted_task_and_prevents_resubmit(tmp_path):
    class BrokenJournal(HaiyuTransport):
        @staticmethod
        def _save_journal(path, record):
            raise OSError("disk unavailable")

        async def get_task(self, url, headers, timeout):
            raise XingheAgentError(
                "PROVIDER_RESPONSE_INVALID", "Original task query unavailable",
                provider_request_id="accepted-task",
            )

    delegate = Transport([(202, {"id": "accepted-task", "status": "queued"})])
    with pytest.raises(XingheAgentError) as caught:
        request(BrokenJournal(delegate, journal_dir=tmp_path, poll_interval=0))
    assert caught.value.provider_request_id == "accepted-task"
    with pytest.raises(XingheAgentError, match="reconciliation"):
        request(HaiyuTransport(delegate, journal_dir=tmp_path))
    assert len(delegate.calls) == 1
