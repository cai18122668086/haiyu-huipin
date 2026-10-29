"""Paid-call fences use synthetic pixels and an isolated local database."""

import asyncio
import hashlib
import os
import shutil
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from app.detail_fast_gateway import PIPELINE, RECEIPTS, DurableFastDetailGateway
from app.haiyu_transport import HaiyuTransport
from app.models import (
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    WorkspaceRecord,
    utc_now,
)
from app.provider import ProviderArtifact, ProviderOutcome
from app.storage import ImageValidator, LocalImageStorage
from app.xinghe_agent import (
    ImageCallResult,
    ImageEditRequest,
    SourceImage,
    TengliOpenAICompatibleGateway,
)


def pixels(color="red"):
    output = BytesIO()
    Image.new("RGB", (24, 32), color).save(output, "PNG")
    return output.getvalue()


class Gateway:
    provider_name = "synthetic"
    image_model = "same-high-model"
    planner_model = "same-planner"
    pricing_version = "test"

    def __init__(self, outcome=ProviderOutcome.SUCCEEDED, error=None):
        self.requests = []
        self.outcome, self.error = outcome, error

    async def edit(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        data = pixels()
        return ImageCallResult(
            outcome=self.outcome,
            provider_request_id="original-request",
            raw_usage={"input_tokens": 10},
            normalized_usage={
                "reported": True,
                "input_tokens": 10,
                "output_tokens": 20,
            },
            api_latency_seconds=0.25,
            actual_cost_micros=None,
            artifact=ProviderArtifact(
                payload=data,
                filename="raw.png",
                content_type="image/png",
                width=24,
                height=32,
                sha256=hashlib.sha256(data).hexdigest(),
            )
            if self.outcome == ProviderOutcome.SUCCEEDED
            else None,
            error_code="KNOWN_REJECTION"
            if self.outcome == ProviderOutcome.FAILED
            else None,
        )


def make_request(client, parent=None, *, workspace_id="ws_test", long_ids=False):
    assert client.get("/api/v1/workspaces").status_code == 200
    job_id, item_id, attempt_id = (uuid4().hex for _ in range(3))
    if long_ids:
        job_id, item_id = "job_" + job_id, "item_" + item_id
    workspace_id = parent.workspace_id if parent else workspace_id
    idem = uuid4().hex
    with client.app.state.session_factory() as session:
        if session.get(WorkspaceRecord, workspace_id) is None:
            session.add(
                WorkspaceRecord(
                    id=workspace_id,
                    name="Isolated gateway test",
                    created_by_user_id="usr_test",
                )
            )
            session.flush()
        job = JobRecord(
            id=job_id,
            workspace_id=workspace_id,
            type="detail_pack",
            status="processing",
            configuration={
                "domestic_detail_pipeline": PIPELINE,
                "settings": {"detail_speed": "fast"},
            },
            retry_of_job_id=parent.job_id if parent else None,
            retry_scope="failed" if parent else None,
        )
        session.add(job)
        session.flush()
        session.add(
            JobPlanRecord(
                id=uuid4().hex,
                workspace_id=workspace_id,
                job_id=job_id,
                revision=1,
                validation_status="valid",
                payload_hash="not-used-for-recovery",
                payload={
                    "resume_signature": "frozen-whole-plan-signature",
                    "detail_fast": {
                        "version": PIPELINE,
                        "skill_id": "xinghe-ecommerce-detail-domestic-4-fast",
                        "skill_content_hash": "frozen-skill-hash",
                    },
                },
            )
        )
        session.add(
            JobItemRecord(
                id=item_id,
                workspace_id=workspace_id,
                job_id=job_id,
                item_key="primary:page:1",
                ordinal=0,
                status="running",
                configuration={"resume_signature": "frozen-whole-plan-signature"},
                source_job_item_id=parent.job_item_id if parent else None,
            )
        )
        session.flush()
        session.add(
            JobAttemptRecord(
                id=attempt_id,
                workspace_id=workspace_id,
                job_id=job_id,
                job_item_id=item_id,
                attempt_no=1,
                status="running",
                lease_token=uuid4().hex,
                leased_by="test",
                lease_expires_at=utc_now() + timedelta(minutes=10),
                provider="synthetic",
                operation="detail_pack",
                provider_idempotency_key=idem,
            )
        )
        session.commit()
    data = pixels("blue")
    return ImageEditRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        job_item_id=item_id,
        attempt_id=attempt_id,
        page_number=1,
        group_key="primary-product",
        sources=(
            SourceImage(
                asset_id="source-a",
                group_key="primary-product",
                filename="source.png",
                content_type="image/png",
                sha256=hashlib.sha256(data).hexdigest(),
                payload=data,
            ),
        ),
        prompt="Complete frozen prompt",
        quality="high",
        idempotency_key=idem,
        size="1440x1920",
    )


def wrapper(client, gateway, skill_hash="frozen-skill-hash"):
    return DurableFastDetailGateway(
        client.app.state.session_factory,
        gateway,
        client.app.state.storage,
        skill_id="xinghe-ecommerce-detail-domestic-4-fast",
        skill_content_hash=skill_hash,
    )


def receipt(client, request):
    with client.app.state.session_factory() as session:
        return session.get(JobRecord, request.job_id).configuration[RECEIPTS][
            request.job_item_id
        ]


def test_gateway_preserves_identity_flags_raw_bytes_and_nullable_cost(client):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    assert durable.planner_model == gateway.planner_model
    result = asyncio.run(durable.edit(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert gateway.requests[0].single_dispatch
    assert gateway.requests[0].preserve_provider_artifact
    assert gateway.requests[0].quality == request.quality
    record = receipt(client, request)
    assert record["state"] == "returned"
    assert record["actual_cost_micros"] is None
    assert record["started_at"] and record["finished_at"]
    assert record["api_latency_seconds"] == 0.25
    assert record["usage"]["output_tokens"] == 20
    assert record["binding"]["prompt"] == request.prompt
    assert record["binding"]["skill_content_hash"] == "frozen-skill-hash"
    assert record["artifact"]["object_key"] == (
        f"{request.workspace_id}/detail-fast/{request.job_id}/"
        f"page-001-{record['artifact']['sha256']}.png"
    )
    assert (
        asyncio.run(
            client.app.state.storage.read_bytes(record["artifact"]["object_key"])
        )
        == result.artifact.payload
    )
    assert result.normalized_usage["review_status"] == "NOT_REVIEWED"


def test_raw_image_reused_same_job_and_explicit_retry_with_zero_calls(client):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    first = asyncio.run(durable.edit(request))
    for next_request in [request, make_request(client, request)]:
        result = asyncio.run(durable.edit(next_request))
        assert result.outcome == ProviderOutcome.SUCCEEDED
        assert result.artifact.payload == first.artifact.payload
        assert result.normalized_usage["network_requests"] == 0
        assert result.normalized_usage["reused"]
        assert result.actual_cost_micros == 0
        assert result.provider_request_id != first.provider_request_id
    assert len(gateway.requests) == 1


@pytest.mark.parametrize(
    "change", ["prompt", "quality", "size", "skill", "source", "plan"]
)
def test_frozen_binding_changes_never_reuse_or_redispatch(client, change):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    asyncio.run(durable.edit(request))
    retry = make_request(client, request)
    if change == "skill":
        durable = wrapper(client, gateway, skill_hash="other-version")
    elif change == "plan":
        with client.app.state.session_factory() as session:
            item = session.get(JobItemRecord, retry.job_item_id)
            item.configuration = {"resume_signature": "changed-whole-plan"}
            plan = session.query(JobPlanRecord).filter_by(job_id=retry.job_id).one()
            plan.payload = {**plan.payload, "resume_signature": "changed-whole-plan"}
            session.commit()
    elif change == "source":
        data = pixels("green")
        retry = replace(
            retry,
            sources=(
                replace(
                    retry.sources[0],
                    payload=data,
                    sha256=hashlib.sha256(data).hexdigest(),
                ),
            ),
        )
    else:
        retry = replace(
            retry,
            **{
                change: {
                    "prompt": "Different plan",
                    "quality": "medium",
                    "size": "1024x1536",
                }[change]
            },
        )
    result = asyncio.run(durable.edit(retry))
    assert result.error_code == "DETAIL_FAST_BINDING_CHANGED"
    assert len(gateway.requests) == 1


@pytest.mark.parametrize("mode", ["timeout", "unknown", "download", "empty"])
def test_unknown_outcomes_fence_original_and_retry_jobs(client, mode):
    gateway = Gateway(
        outcome=ProviderOutcome.OUTCOME_UNKNOWN
        if mode == "unknown"
        else ProviderOutcome.SUCCEEDED,
        error=TimeoutError("unknown submission") if mode == "timeout" else None,
    )
    if mode in {"download", "empty"}:
        edit = gateway.edit

        async def incomplete(request):
            result = await edit(request)
            return replace(
                result,
                artifact=None,
                error_code="IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"
                if mode == "download"
                else None,
            )

        gateway.edit = incomplete
    durable = wrapper(client, gateway)
    request = make_request(client)
    assert asyncio.run(durable.edit(request)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert receipt(client, request)["state"] == "unknown"
    for next_request in [request, make_request(client, request)]:
        assert (
            asyncio.run(durable.edit(next_request)).outcome
            == ProviderOutcome.OUTCOME_UNKNOWN
        )
    assert len(gateway.requests) == 1


def test_known_rejection_requires_new_retry_job(client):
    gateway = Gateway(outcome=ProviderOutcome.FAILED)
    durable = wrapper(client, gateway)
    request = make_request(client)
    assert asyncio.run(durable.edit(request)).outcome == ProviderOutcome.FAILED
    assert receipt(client, request)["state"] == "failed"
    assert asyncio.run(durable.edit(request)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert len(gateway.requests) == 1
    gateway.outcome = ProviderOutcome.SUCCEEDED
    retry = make_request(client, request)
    assert asyncio.run(durable.edit(retry)).outcome == ProviderOutcome.SUCCEEDED
    assert len(gateway.requests) == 2


def test_cancelled_call_records_unknown_and_does_not_dispatch_again(client):
    gateway = Gateway(error=asyncio.CancelledError())
    durable = wrapper(client, gateway)
    request = make_request(client)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(durable.edit(request))
    assert receipt(client, request)["state"] == "unknown"
    assert asyncio.run(durable.edit(request)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert len(gateway.requests) == 1


def test_raw_storage_failure_is_unknown_and_not_paid_again(client, monkeypatch):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)

    async def unavailable(image):
        raise OSError("storage unavailable")

    monkeypatch.setattr(client.app.state.storage, "save_validated", unavailable)
    assert asyncio.run(durable.edit(request)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert receipt(client, request)["state"] == "unknown"
    assert (
        asyncio.run(durable.edit(make_request(client, request))).outcome
        == ProviderOutcome.OUTCOME_UNKNOWN
    )
    assert len(gateway.requests) == 1


def test_active_call_is_fenced_before_awaiting_gateway(client):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        edit = gateway.edit

        async def slow(request):
            entered.set()
            await release.wait()
            return await edit(request)

        gateway.edit = slow
        first = asyncio.create_task(durable.edit(request))
        await entered.wait()
        assert receipt(client, request)["state"] == "invoked"
        second = await durable.edit(request)
        assert second.outcome == ProviderOutcome.OUTCOME_UNKNOWN
        release.set()
        assert (await first).outcome == ProviderOutcome.SUCCEEDED

    asyncio.run(run())
    assert len(gateway.requests) == 1


def test_source_corruption_and_wrong_scope_block_before_dispatch(client):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    bad_source = replace(request.sources[0], sha256="wrong")
    assert (
        asyncio.run(durable.edit(replace(request, sources=(bad_source,)))).error_code
        == "DETAIL_FAST_SOURCE_CHANGED"
    )
    assert (
        asyncio.run(
            durable.edit(replace(request, workspace_id="other-workspace"))
        ).error_code
        == "DETAIL_FAST_SCOPE_INVALID"
    )
    assert not gateway.requests


def test_selected_revision_authorizes_new_call_but_same_job_still_fenced(client):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    original = make_request(client)
    assert asyncio.run(durable.edit(original)).outcome == ProviderOutcome.SUCCEEDED
    selected = make_request(client, original)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, selected.job_id)
        job.retry_scope = "selected"
        item = session.get(JobItemRecord, selected.job_item_id)
        item.configuration = {"resume_signature": "selected-revised-whole-plan"}
        plan = session.query(JobPlanRecord).filter_by(job_id=selected.job_id).one()
        plan.payload = {
            **plan.payload,
            "resume_signature": "selected-revised-whole-plan",
        }
        session.commit()
    selected = replace(selected, prompt="User authorized revised layout and copy")
    assert asyncio.run(durable.edit(selected)).outcome == ProviderOutcome.SUCCEEDED
    assert len(gateway.requests) == 2
    assert asyncio.run(durable.edit(selected)).normalized_usage["reused"]
    assert len(gateway.requests) == 2


def test_missing_storage_blocks_before_any_image_call(client):
    gateway = Gateway()
    durable = DurableFastDetailGateway(
        client.app.state.session_factory,
        gateway,
        None,
        skill_id="xinghe-ecommerce-detail-domestic-4-fast",
        skill_content_hash="frozen",
    )
    result = asyncio.run(durable.edit(make_request(client)))
    assert result.error_code == "DETAIL_FAST_STORAGE_REQUIRED"
    assert not gateway.requests


def test_cached_artifact_checksum_mismatch_never_regenerates(client, monkeypatch):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    asyncio.run(durable.edit(request))

    async def corrupted(key):
        return pixels("green")

    monkeypatch.setattr(client.app.state.storage, "read_bytes", corrupted)
    result = asyncio.run(durable.edit(make_request(client, request)))
    assert result.error_code == "DETAIL_FAST_RESULT_CHANGED"
    assert len(gateway.requests) == 1


@pytest.mark.parametrize(
    "field", ["version", "skill_id", "skill_content_hash", "resume_signature"]
)
def test_latest_plan_skill_and_frozen_signature_are_verified_before_call(client, field):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    with client.app.state.session_factory() as session:
        plan = session.query(JobPlanRecord).filter_by(job_id=request.job_id).one()
        if field == "resume_signature":
            plan.payload = {**plan.payload, field: "changed"}
        else:
            plan.payload = {
                **plan.payload,
                "detail_fast": {**plan.payload["detail_fast"], field: "changed"},
            }
        session.commit()
    assert (
        asyncio.run(durable.edit(request)).error_code == "DETAIL_FAST_BINDING_CHANGED"
    )
    assert not gateway.requests


def test_mutable_plan_metrics_hash_does_not_break_frozen_image_recovery(client):
    gateway = Gateway()
    durable = wrapper(client, gateway)
    request = make_request(client)
    asyncio.run(durable.edit(request))
    with client.app.state.session_factory() as session:
        plan = session.query(JobPlanRecord).filter_by(job_id=request.job_id).one()
        plan.payload = {**plan.payload, "metrics": {"completed": 1}}
        plan.payload_hash = "updated-after-generation"
        session.commit()
    assert asyncio.run(durable.edit(request)).normalized_usage["reused"]
    assert len(gateway.requests) == 1


def test_compact_private_key_saves_and_reuses_under_real_workspace_length(client):
    # Use this checkout's resolved path, not pytest's unrelated temporary root.
    # The previous job/item/SHA layout exceeds MAX_PATH for these real IDs.
    base = Path(__file__).resolve().parents[2] / "backend" / ".data"
    root = (base / ("df-gateway-longpath-" + uuid4().hex[:8])).resolve()
    storage = LocalImageStorage(root, ImageValidator(2 * 1024 * 1024, 2_000_000))
    gateway = Gateway()
    durable = DurableFastDetailGateway(
        client.app.state.session_factory,
        gateway,
        storage,
        skill_id="xinghe-ecommerce-detail-domestic-4-fast",
        skill_content_hash="frozen-skill-hash",
    )
    request = make_request(client, workspace_id="ws_" + "a" * 33, long_ids=True)
    try:
        generated = asyncio.run(durable.edit(request))
        assert generated.outcome == ProviderOutcome.SUCCEEDED
        record = receipt(client, request)
        key = record["artifact"]["object_key"]
        actual = root / key
        legacy = root / (
            f"{request.workspace_id}/detail-fast/{request.job_id}/"
            f"{request.job_item_id}/{record['artifact']['sha256']}.png"
        )
        assert len(str(legacy)) - len(str(actual)) == len(request.job_item_id) - 8
        if os.name == "nt":
            assert len(str(actual)) < 260 <= len(str(legacy))
        assert actual.is_file() and actual.read_bytes() == generated.artifact.payload
        cached = asyncio.run(durable.edit(make_request(client, request)))
        assert cached.artifact.payload == generated.artifact.payload
        assert cached.normalized_usage["reused"]
        assert cached.normalized_usage["network_requests"] == 0
        assert len(gateway.requests) == 1
    finally:
        # Delete only this test's unique ignored directory, never runtime data.
        assert root.parent == base.resolve() and root.name.startswith(
            "df-gateway-longpath-"
        )
        if root.exists():
            shutil.rmtree(root)


@pytest.mark.parametrize(
    "admission", ["unique", "missing", "ambiguous", "failed_query"]
)
def test_failed_retry_recovers_only_unique_haiyu_accepted_task_with_gets(
    client, monkeypatch, tmp_path, admission
):
    class NoSecondPost:
        def __init__(self):
            self.posts = 0

        async def post_multipart(self, **kwargs):
            self.posts += 1
            assert self.posts == 1, "A recovery must never purchase another image"
            return 202, {"id": "already-paid-task", "status": "queued"}

    class JournalTransport(HaiyuTransport):
        def __init__(self, delegate):
            super().__init__(
                delegate, journal_dir=tmp_path / "journal", poll_interval=0
            )
            self.gets = []

        async def get_task(self, url, headers, timeout):
            self.gets.append(url)
            if admission == "failed_query" and len(self.gets) == 2:
                # The real transport turns this GET response into HTTP 400;
                # Tengli consequently returns FAILED. It still does not prove
                # that another paid submission is authorized.
                return 200, {
                    "status": "failed",
                    "error": {
                        "code": "invalid_image",
                        "message": "Synthetic GET failure",
                    },
                }
            return 200, {
                "status": "succeeded",
                "result": {"data": [{"url": "https://cdn.example/original.png"}]},
            }

    delegate = NoSecondPost()
    transport = JournalTransport(delegate)
    gateway = TengliOpenAICompatibleGateway(
        api_key="synthetic-test-key",
        base_url="https://api.haiyushuke.com",
        planner_model="same-planner",
        image_model="same-high-model",
        transport=transport,
    )
    monkeypatch.setattr("app.haiyu_transport.download_result", lambda *_: pixels())
    wires = []
    edit = gateway.edit

    async def recorded(request):
        wires.append(request)
        return await edit(request)

    monkeypatch.setattr(gateway, "edit", recorded)
    storage = client.app.state.storage
    save = storage.save_validated

    async def failed_save(image):
        raise OSError("Synthetic original-path storage failure")

    monkeypatch.setattr(storage, "save_validated", failed_save)
    durable = wrapper(client, gateway)
    original = make_request(client)
    assert (
        asyncio.run(durable.edit(original)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    )
    assert receipt(client, original)["state"] == "unknown"
    assert delegate.posts == 1 and len(transport.gets) == 1
    # Even accepted evidence never makes the same job automatically repeat.
    assert (
        asyncio.run(durable.edit(original)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    )
    assert len(wires) == 1
    monkeypatch.setattr(storage, "save_validated", save)
    if admission == "missing":
        transport.journal_dir = tmp_path / "no-accepted-journal"
    elif admission == "ambiguous":
        route = gateway._image_routes()[0]
        monkeypatch.setattr(gateway, "_image_routes", lambda: (route, route))
    retry = make_request(client, original)
    recovered = asyncio.run(durable.edit(retry))
    assert delegate.posts == 1
    if admission in {"missing", "ambiguous"}:
        assert recovered.outcome == ProviderOutcome.OUTCOME_UNKNOWN
        assert len(wires) == 1 and len(transport.gets) == 1
        return
    if admission == "failed_query":
        assert recovered.outcome == ProviderOutcome.OUTCOME_UNKNOWN
        assert recovered.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert recovered.actual_cost_micros == recovered.estimated_cost_micros == 0
        assert receipt(client, retry)["state"] == "unknown"
        assert (
            receipt(client, retry)["recovery_idempotency_key"]
            == original.idempotency_key
        )
        retry = make_request(client, retry)
        recovered = asyncio.run(durable.edit(retry))
        assert len(wires) == 3 and len(transport.gets) == 3
    else:
        assert len(wires) == 2 and len(transport.gets) == 2
    assert recovered.outcome == ProviderOutcome.SUCCEEDED
    assert delegate.posts == 1
    assert all(wire.reconcile_only for wire in wires[1:])
    assert all(wire.idempotency_key == original.idempotency_key for wire in wires[1:])
    assert wires[-1].reconcile_only and wires[-1].single_dispatch
    assert wires[-1].idempotency_key == original.idempotency_key
    assert recovered.artifact.payload == pixels()
    assert recovered.actual_cost_micros == recovered.estimated_cost_micros == 0
    assert recovered.provider_request_id == f"detail-fast-recovered-{retry.attempt_id}"
    assert recovered.normalized_usage["generation_requests"] == 0
    assert recovered.normalized_usage["recovery"] is True
    assert recovered.normalized_usage["original_provider_request_id"]
    record = receipt(client, retry)
    if admission != "failed_query":
        assert record["recovery_source"]["job_id"] == original.job_id
        assert record["recovery_source"]["attempt_id"] == original.attempt_id
    assert record["recovery_idempotency_key"] == original.idempotency_key
    assert record["state"] == "returned"
