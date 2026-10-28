"""Use the real HTTP write path: connect retries never replay a paid body."""

from __future__ import annotations

import http.client
import io
import ssl
from collections import Counter

import pytest

from app.domestic_detail import transport as native
from app.xinghe_agent import (
    NativeUrllibOpenAITransport,
    UrllibOpenAITransport,
    XingheAgentError,
)


class InterruptedResponse(io.BytesIO):
    def readline(self, *args, **kwargs):
        raise ConnectionResetError("synthetic reset after POST body")


class Socket:
    def __init__(self, response, *, read_failure=False):
        self.writes = []
        self.response = response
        self.read_failure = read_failure
        self.timeouts = []

    def sendall(self, data):
        self.writes.append(bytes(data))

    def makefile(self, *_args, **_kwargs):
        if self.read_failure:
            return InterruptedResponse(self.response)
        return io.BytesIO(self.response)

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        pass


def fixture(
    monkeypatch,
    *,
    failed_connects=0,
    read_failure=False,
    status=200,
    certificate=False,
    rejected_proxy=False,
):
    calls = Counter()
    response = (
        f"HTTP/1.1 {status} Synthetic\r\nContent-Length: 2\r\n"
        "Location: https://different.example/redirect\r\n\r\n{}"
    ).encode()
    sock = Socket(response, read_failure=read_failure)

    def connect(connection):
        calls["connect"] += 1
        if isinstance(connection, native.PreconnectedHTTPSConnection):
            assert connection.timeout <= native.CONNECT_ATTEMPT_SECONDS
        if certificate:
            raise ssl.SSLCertVerificationError("synthetic invalid certificate")
        if calls["connect"] <= failed_connects:
            if rejected_proxy:
                # http.client._tunnel closes the connection on a rejected
                # CONNECT. Retrying must happen before provider HTTP state.
                connection.close()
                raise OSError("synthetic proxy CONNECT unavailable")
            raise ssl.SSLEOFError("synthetic handshake EOF; HTTP not sent")
        connection.sock = sock

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", connect)
    monkeypatch.setattr(native.time, "monotonic", lambda: 0.0)
    return calls, sock


def send(kind=NativeUrllibOpenAITransport):
    return kind._send(
        "https://api.example.test/v1/chat/completions",
        {
            "Authorization": "Bearer synthetic-test-key",
            "Idempotency-Key": "one-logical-provider-request",
        },
        b"the-exact-paid-request-body",
        600,
    )


def test_two_handshake_failures_still_send_exactly_one_http_body(monkeypatch):
    calls, sock = fixture(monkeypatch, failed_connects=2)
    status, value = send()
    assert status == 200 and value == {}
    assert calls["connect"] == 3
    assert sock.writes.count(b"the-exact-paid-request-body") == 1
    assert sum(chunk.startswith(b"POST ") for chunk in sock.writes) == 1
    assert b"Idempotency-Key: one-logical-provider-request" in sock.writes[0]
    assert sock.timeouts == [600.0]


def test_no_http_bytes_are_sent_when_setup_budget_is_exhausted(monkeypatch):
    calls, sock = fixture(monkeypatch, failed_connects=99)
    with pytest.raises(XingheAgentError):
        send()
    assert calls["connect"] == native.MAX_CONNECT_ATTEMPTS
    assert sock.writes == []


def test_connect_attempt_never_exceeds_remaining_total_budget(monkeypatch):
    calls, sock = fixture(monkeypatch, failed_connects=99)
    clock = iter([0.0, 27.0, 29.0, 29.5, 30.0])
    monkeypatch.setattr(native.time, "monotonic", lambda: next(clock))
    timeouts = []

    def connect(connection):
        timeouts.append(connection.timeout)
        calls["connect"] += 1
        raise TimeoutError("synthetic setup timeout")

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", connect)
    with pytest.raises(XingheAgentError):
        send()
    assert timeouts == [3.0, 0.5]
    assert not sock.writes


def test_certificate_failure_is_never_retried_or_bypassed(monkeypatch):
    calls, sock = fixture(monkeypatch, certificate=True)
    with pytest.raises(XingheAgentError):
        send()
    assert calls["connect"] == 1 and not sock.writes
    context = native.PreconnectedHTTPSHandler()._context
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_connection_loss_after_post_does_not_open_or_send_again(monkeypatch):
    calls, sock = fixture(monkeypatch, read_failure=True)
    with pytest.raises(XingheAgentError):
        send()
    assert calls["connect"] == 1
    assert sock.writes.count(b"the-exact-paid-request-body") == 1


@pytest.mark.parametrize("status", [302, 429, 500])
def test_http_response_never_causes_connection_or_body_replay(monkeypatch, status):
    calls, sock = fixture(monkeypatch, status=status)
    actual, _ = send()
    assert actual == status and calls["connect"] == 1
    assert sock.writes.count(b"the-exact-paid-request-body") == 1


def test_standard_transport_keeps_original_single_connect_behavior(monkeypatch):
    calls, sock = fixture(monkeypatch, failed_connects=2)
    with pytest.raises(XingheAgentError):
        send(UrllibOpenAITransport)
    assert calls["connect"] == 1 and sock.writes == []


@pytest.mark.parametrize(
    "profile",
    ["aurora_domestic_intake", "aurora_v20_visual_audit", "xinghe_domestic_detail_4"],
)
def test_native_setup_path_does_not_change_domestic_main_planner(monkeypatch, profile):
    import asyncio

    from app.xinghe_agent import PlannerRequest, TengliOpenAICompatibleGateway

    calls = []

    async def native_call(self, **kwargs):
        calls.append("native")
        return 400, {"error": {"message": "synthetic known rejection"}}

    async def standard_call(self, **kwargs):
        calls.append("standard")
        return 400, {"error": {"message": "synthetic known rejection"}}

    monkeypatch.setattr(NativeUrllibOpenAITransport, "post_json", native_call)
    monkeypatch.setattr(UrllibOpenAITransport, "post_json", standard_call)
    gateway = TengliOpenAICompatibleGateway(
        api_key="synthetic",
        base_url="https://api.example.test",
        planner_model="synthetic",
        image_model="synthetic",
    )
    request = PlannerRequest(
        workspace_id="ws_test",
        job_id="job_test",
        group_key="primary",
        sources=(),
        page_count=8,
        language="zh-CN",
        product_name="synthetic",
        product_information=(),
        platform_code="tmall",
        size="1440x1920",
        idempotency_key="one",
        planning_profile=profile,
    )
    asyncio.run(gateway.plan(request))
    assert calls == [
        "native"
        if profile in {"aurora_domestic_intake", "xinghe_domestic_detail_4"}
        else "standard"
    ]


@pytest.mark.parametrize("single_dispatch", [False, True])
@pytest.mark.parametrize("preserve_provider_artifact", [False, True])
def test_only_domestic_detail_image_path_uses_native_setup(
    monkeypatch, single_dispatch, preserve_provider_artifact
):
    import asyncio

    from app.xinghe_agent import ImageEditRequest, TengliOpenAICompatibleGateway

    calls = []

    async def native_call(self, **kwargs):
        calls.append("native")
        return 400, {"error": {"message": "synthetic known rejection"}}

    async def standard_call(self, **kwargs):
        calls.append("standard")
        return 400, {"error": {"message": "synthetic known rejection"}}

    monkeypatch.setattr(NativeUrllibOpenAITransport, "post_multipart", native_call)
    monkeypatch.setattr(UrllibOpenAITransport, "post_multipart", standard_call)
    gateway = TengliOpenAICompatibleGateway(
        api_key="synthetic",
        base_url="https://api.example.test",
        planner_model="synthetic",
        image_model="synthetic",
    )
    request = ImageEditRequest(
        workspace_id="ws_test",
        job_id="job_test",
        job_item_id="item",
        attempt_id="attempt",
        page_number=1,
        group_key="primary",
        sources=(),
        prompt="synthetic",
        quality="high",
        idempotency_key="one",
        size="1440x1920",
        single_dispatch=single_dispatch,
        preserve_provider_artifact=preserve_provider_artifact,
    )
    asyncio.run(gateway.edit(request))
    assert calls == [
        "native" if single_dispatch or preserve_provider_artifact else "standard"
    ]


def test_proxy_setup_failure_cannot_reset_a_started_provider_post(monkeypatch):
    calls, sock = fixture(monkeypatch, failed_connects=2, rejected_proxy=True)
    status, value = send()
    assert status == 200 and value == {}
    assert calls["connect"] == 3
    assert sock.writes.count(b"the-exact-paid-request-body") == 1
    assert sum(chunk.startswith(b"POST ") for chunk in sock.writes) == 1
