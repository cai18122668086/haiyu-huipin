"""Real API/executor/transport recovery with local fault injection, no paid calls."""

import asyncio
import base64
import json
from datetime import timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest
from PIL import Image
from sqlalchemy import select

from app.haiyu_transport import HaiyuTransport
from app.models import (
    JobAttemptRecord,
    JobItemRecord,
    ReplicaDocumentRecord,
    UsageEventRecord,
    utc_now,
)
from app.provider import ProviderOutcome
from app.replica_native import read_json
from app.xinghe_agent import TengliOpenAICompatibleGateway, XingheAgentError
from tests.test_api import dispatch_pending
from tests.test_haiyu_transport import MODELS, Transport, png, request
from tests.test_replica_compact import SyntheticGateway
from tests.test_replica_partial_delivery import create_case


@pytest.fixture
def fast_sleep(monkeypatch):
    async def sleep(_seconds):
        return None

    monkeypatch.setattr("app.haiyu_transport.asyncio.sleep", sleep)


class LocalTransport(HaiyuTransport):
    def __init__(
        self, tmp_path, *, broken_queries=0, terminal=False, crash=False, fault_screen=1
    ):
        self.posts = []
        self.gets = []
        self.broken_queries = broken_queries
        self.terminal = terminal
        self.crash = crash
        self.tasks = {}
        self.fault_screen = fault_screen
        super().__init__(
            self, journal_dir=tmp_path / "journal", poll_interval=0, send_size=True
        )

    async def post_multipart(self, **kwargs):
        # The delegate seam is separate from the adapter entry point below.
        raise AssertionError("Use the adapter")

    async def send(self, **kwargs):
        self.posts.append(kwargs)
        task = f"task-{len(self.posts)}"
        canvas = tuple(map(int, dict(kwargs["fields"])["size"].split("x")))
        output = BytesIO()
        Image.new("RGB", canvas, "blue").save(output, "PNG")
        self.tasks[task] = output.getvalue()
        return 202, {"id": task, "status": "queued"}

    async def get_task(self, url, headers, timeout):
        self.gets.append(url)
        task = url.rsplit("/", 1)[1]
        if self.crash:
            self.crash = False
            raise asyncio.CancelledError("worker interrupted after supplier acceptance")
        if self.broken_queries and task == f"task-{self.fault_screen}":
            self.broken_queries -= 1
            raise ValueError("temporary incomplete status JSON")
        if self.terminal:
            return 200, {
                "id": task,
                "status": "failed",
                "error": {
                    "code": "agent3_failed",
                    "message": "Image result unavailable; "
                    "do not create a replacement automatically",
                },
            }
        return 200, {
            "id": task,
            "status": "succeeded",
            "result": {
                "data": [{"b64_json": base64.b64encode(self.tasks[task]).decode()}]
            },
        }

    async def adapter(self, **kwargs):
        return await HaiyuTransport.post_multipart(self, **kwargs)


def hybrid_gateway(tmp_path, **faults):
    from types import SimpleNamespace

    transport = LocalTransport(tmp_path, **faults)
    transport.delegate = SimpleNamespace(post_multipart=transport.send)
    # Override only the adapter's public method; its delegate stays local.
    transport.post_multipart = transport.adapter
    real = TengliOpenAICompatibleGateway(
        api_key="fixture",
        base_url="https://api.haiyushuke.com",
        planner_model="fixture-planner",
        image_model=MODELS[0],
        transport=transport,
    )
    fake = SyntheticGateway()

    async def edit(edit_request):
        fake.image_requests.append(edit_request)
        return await real.edit(edit_request)

    fake.edit = edit
    return fake, transport


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("fault_screen", [1, 2])
def test_api_automatically_resumes_original_after_query_failures_without_replanning(
    client, monkeypatch, tmp_path, fallback, fault_screen
):
    gateway, transport = hybrid_gateway(
        tmp_path, broken_queries=4, fault_screen=fault_screen
    )
    _, route, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=fallback, gateway_override=gateway
    )
    analysis_count = len(gateway.requests)
    dispatch_pending(client)
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "completed", job
    assert len(transport.posts) == 2
    assert len(gateway.image_requests) == 3
    initial, resumed = gateway.image_requests[fault_screen - 1 : fault_screen + 1]
    assert initial.reconcile_only is False and resumed.reconcile_only is True
    assert initial.idempotency_key == resumed.idempotency_key
    assert initial.sources == resumed.sources and initial.prompt == resumed.prompt
    assert len([r for r in gateway.image_requests if not r.reconcile_only]) == 2
    assert len(gateway.requests) == analysis_count
    assert (
        len([asset for asset in job["assets"] if asset["direction"] == "output"]) == 2
    )
    ledger = read_json(next((tmp_path / "native").rglob("invocations.json")))
    assert sorted(row.get("reconciliation_attempts", 0) for row in ledger.values()) == [
        0,
        1,
    ]
    with client.app.state.session_factory() as db:
        items = list(
            db.scalars(
                select(JobItemRecord)
                .where(JobItemRecord.job_id == jid)
                .order_by(JobItemRecord.ordinal)
            )
        )
        assert [item.attempt_count for item in items] == (
            [2, 1] if fault_screen == 1 else [1, 2]
        )
        doc = db.get(ReplicaDocumentRecord, did)
        assert (
            doc.payload["native_snapshot"]["model_protocol"]["version"]
            == "exact-compact-v16"
        )
        usages = list(
            db.scalars(select(UsageEventRecord).where(UsageEventRecord.job_id == jid))
        )
        assert sum(u.raw_usage["network_requests"] for u in usages) == 2
        resumed_usage = next(u for u in usages if u.raw_usage["network_requests"] == 0)
        assert (
            resumed_usage.actual_cost_micros == resumed_usage.estimated_cost_micros == 0
        )
    assert "APPROVED_MODULE_EVIDENCE=" in initial.prompt


@pytest.mark.parametrize("fallback", [False, True])
def test_expired_worker_lease_resumes_accepted_task_without_a_second_post(
    client, monkeypatch, tmp_path, fallback
):
    gateway, transport = hybrid_gateway(tmp_path, crash=True)
    _, _, _, jid = create_case(
        client, monkeypatch, tmp_path, fallback=fallback, gateway_override=gateway
    )
    coordinator = client.app.state.task_runner.coordinator
    coordinator.prepare(jid)
    lease = coordinator.claim_next_item(jid)
    assert lease
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coordinator.execute_lease(lease))
    assert len(transport.posts) == 1
    with client.app.state.session_factory() as db:
        attempt = db.get(JobAttemptRecord, lease.attempt_id)
        assert attempt.status == "running"
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    asyncio.run(coordinator.run(jid))
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "completed", job
    assert len(transport.posts) == 2  # One original request per each of two screens.
    assert gateway.image_requests[1].reconcile_only
    assert (
        gateway.image_requests[0].idempotency_key
        == gateway.image_requests[1].idempotency_key
    )


@pytest.mark.parametrize("fallback", [False, True])
def test_supplier_stop_instruction_is_terminal_and_preserves_reason(
    client, monkeypatch, tmp_path, fallback
):
    gateway, transport = hybrid_gateway(tmp_path, terminal=True)
    _, _, _, jid = create_case(
        client, monkeypatch, tmp_path, fallback=fallback, gateway_override=gateway
    )
    dispatch_pending(client)
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "failed"
    assert job["error_code"] == "IMAGE_ASYNC_TASK_FAILED"
    assert job["error_details"]["provider_request_id"] == "task-1"
    assert job["error_details"]["provider_error"]["code"] == "agent3_failed"
    assert len(transport.posts) == 1 and len(gateway.image_requests) == 1


def test_query_recovery_exhaustion_is_bounded_and_keeps_original_id(
    client, monkeypatch, tmp_path
):
    gateway, transport = hybrid_gateway(tmp_path, broken_queries=100)
    _, _, _, jid = create_case(client, monkeypatch, tmp_path, gateway_override=gateway)
    dispatch_pending(client)
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "failed", job
    assert len(transport.posts) == 1 and len(transport.gets) == 12
    assert len(gateway.image_requests) == 3
    assert all(request.reconcile_only for request in gateway.image_requests[1:])
    assert job["error_details"]["provider_request_id"] == "task-1"
    assert job["error_details"]["retryable"] is False
    assert job["error_details"]["image_recovery"]["automatic_resumptions"] == 2
    with client.app.state.session_factory() as db:
        attempts = list(
            db.scalars(
                select(JobAttemptRecord)
                .where(JobAttemptRecord.job_id == jid)
                .order_by(JobAttemptRecord.attempt_no)
            )
        )
        assert [a.provider_request_id for a in attempts] == ["task-1", None, None]
        assert attempts[-1].error_details["original_task_attempt_id"] == attempts[0].id


def test_worker_with_missing_original_receipt_stops_without_regeneration(
    client, monkeypatch, tmp_path
):
    gateway, transport = hybrid_gateway(tmp_path, crash=True)
    _, _, _, jid = create_case(client, monkeypatch, tmp_path, gateway_override=gateway)
    coordinator = client.app.state.task_runner.coordinator
    coordinator.prepare(jid)
    lease = coordinator.claim_next_item(jid)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coordinator.execute_lease(lease))
    for receipt in (tmp_path / "journal").glob("*.json"):
        receipt.unlink()
    with client.app.state.session_factory() as db:
        db.get(JobAttemptRecord, lease.attempt_id).lease_expires_at = (
            utc_now() - timedelta(seconds=1)
        )
        db.commit()
    asyncio.run(coordinator.run(jid))
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "failed", job
    assert len(transport.posts) == 1
    assert gateway.image_requests[-1].reconcile_only
    assert "原任务回执" in job["error_message"]


def test_rate_limit_before_acceptance_retries_same_key_and_fields_only(
    tmp_path, fast_sleep
):
    rate_limit = (
        429,
        {"error": {"code": "rate_limit_exceeded"}, "_transport_retry_after": "0"},
    )
    delegate = Transport(
        [
            rate_limit,
            rate_limit,
            (200, {"data": [{"b64_json": base64.b64encode(png()).decode()}]}),
        ]
    )
    status, _ = request(HaiyuTransport(delegate, journal_dir=tmp_path))
    assert status == 200 and len(delegate.calls) == 3
    assert all(
        all(
            call[key] == delegate.calls[0][key]
            for key in ("url", "headers", "fields", "files")
        )
        for call in delegate.calls
    )
    assert (
        json.loads(next(tmp_path.glob("*.json")).read_text())["rate_limit_retries"] == 2
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"code": "rate_limit_exceeded"}, "id": "maybe-accepted"},
        {"error": {"code": "settlement_pending"}},
        {
            "error": {
                "code": "rate_limit_exceeded",
                "message": "do not create a replacement",
            }
        },
    ],
)
def test_uncertain_or_forbidden_rate_limits_never_replay(tmp_path, payload):
    delegate = Transport([(429, payload)])
    try:
        status, _ = request(HaiyuTransport(delegate, journal_dir=tmp_path))
        assert status == 429
    except XingheAgentError:
        pass
    assert len(delegate.calls) == 1


def test_rate_limit_retry_after_beyond_automatic_window_stops(tmp_path):
    delegate = Transport(
        [
            (
                429,
                {
                    "error": {"code": "rate_limit_exceeded"},
                    "_transport_retry_after": "120",
                },
            )
        ]
    )
    assert request(HaiyuTransport(delegate, journal_dir=tmp_path))[0] == 429
    assert len(delegate.calls) == 1


def test_rate_limit_retry_bound_survives_transport_recreation(tmp_path, fast_sleep):
    rejected = (
        429,
        {"error": {"code": "rate_limit_exceeded"}, "_transport_retry_after": "0"},
    )
    delegate = Transport([rejected] * 3)
    assert request(HaiyuTransport(delegate, journal_dir=tmp_path))[0] == 429
    assert len(delegate.calls) == 3
    later = Transport([])
    assert request(HaiyuTransport(later, journal_dir=tmp_path))[0] == 429
    assert not later.calls


def test_disconnect_during_rate_limit_retry_preserves_unknown_dispatch_fence(
    tmp_path, fast_sleep
):
    class LostConnection(Transport):
        async def post_multipart(self, **kwargs):
            if self.calls:
                self.calls.append(kwargs)
                raise XingheAgentError(
                    "PROVIDER_TRANSPORT_FAILED", "lost after sending"
                )
            return await super().post_multipart(**kwargs)

    delegate = LostConnection(
        [
            (
                429,
                {
                    "error": {"code": "rate_limit_exceeded"},
                    "_transport_retry_after": "0",
                },
            )
        ]
    )
    with pytest.raises(XingheAgentError):
        request(HaiyuTransport(delegate, journal_dir=tmp_path))
    assert len(delegate.calls) == 2
    later = Transport([])
    with pytest.raises(XingheAgentError, match="reconciliation"):
        request(HaiyuTransport(later, journal_dir=tmp_path))
    assert not later.calls


def test_v13_protocol_hash_stays_loadable_from_its_frozen_archive():
    import sys

    from app.replica_direct_delivery import V13_BINDING
    from app.replica_exact_compact import run_class

    legacy = run_class("exact", V13_BINDING)
    assert sys.modules[legacy.__module__].protocol_binding() == V13_BINDING


def test_old_v13_unknown_token_is_not_silently_given_new_recovery_behavior(tmp_path):
    from app.replica_native_provider import ReplicaImageOutcomeUnknown
    from tests.test_replica_canvas_recovery import correction_renderer
    from tests.test_replica_resolution import result

    run, render, calls = correction_renderer(
        tmp_path, [result((1024, 1447), ProviderOutcome.OUTCOME_UNKNOWN)]
    )
    run.binding = {"version": "exact-compact-v13"}
    for _ in range(2):
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
    assert len(calls) == 1


def test_expired_result_url_refreshes_original_task_only(
    tmp_path, monkeypatch, fast_sleep
):
    from tests.test_haiyu_transport import JournalTransport

    reads = []

    def download(url, *args):
        reads.append(url)
        if len(reads) == 1:
            raise HTTPError(url, 403, "expired signature", {}, None)
        return png()

    monkeypatch.setattr("app.haiyu_transport.download_result", download)
    delegate = Transport(
        [
            (
                202,
                {
                    "id": "original-task",
                    "status": "succeeded",
                    "result": {"data": [{"url": "https://cdn.example/expired"}]},
                },
            )
        ]
    )
    transport = JournalTransport(delegate, journal_dir=tmp_path, poll_interval=0)
    status, payload = request(transport)
    assert status == 200 and len(delegate.calls) == 1
    assert reads == ["https://cdn.example/expired", "https://cdn.example/original.png"]
    assert payload["image_recovery"]["url_refreshes"] == 1


def test_reconcile_only_without_receipt_never_posts_or_creates_dispatch_fence(tmp_path):
    async def run():
        delegate = Transport([])
        transport = HaiyuTransport(delegate, journal_dir=tmp_path)
        with pytest.raises(XingheAgentError, match="receipt"):
            await transport.post_multipart(
                url="https://api.haiyushuke.com/v1/images/edits",
                headers={"Idempotency-Key": "missing"},
                fields=[("model", MODELS[0])],
                files=[("image", "source.png", "image/png", png())],
                timeout_seconds=1,
                reconcile_only=True,
            )
        assert not delegate.calls and not list(tmp_path.glob("*.json"))

    asyncio.run(run())


@pytest.mark.parametrize(
    "status, payload",
    [
        (503, {"error": {"code": "unavailable"}}),
        (502, {"error": {"code": "upstream_error"}}),
        (408, {"error": {"code": "timeout"}}),
        (429, {"error": {"code": "unclassified_limit"}}),
        (
            503,
            {
                "error": {
                    "code": "channel_unavailable",
                    "message": "do not create a replacement automatically",
                }
            },
        ),
        (
            503,
            {
                "url": "https://cdn.example/possibly-delivered.png",
                "error": {"code": "unavailable"},
            },
        ),
    ],
)
def test_uncertain_initial_response_never_buys_another_model(status, payload):
    from tests.test_haiyu_transport import dispatch

    delegate = Transport([(status, payload)])
    result = dispatch(HaiyuTransport(delegate))
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert not result.retryable and len(delegate.calls) == 1


def test_confirmed_unaccepted_channel_rejection_can_use_configured_fallback():
    from tests.test_haiyu_transport import dispatch, image_response

    delegate = Transport(
        [(503, {"error": {"code": "no_available_channel"}}), image_response()]
    )
    assert dispatch(HaiyuTransport(delegate)).outcome == ProviderOutcome.SUCCEEDED
    assert len(delegate.calls) == 2
