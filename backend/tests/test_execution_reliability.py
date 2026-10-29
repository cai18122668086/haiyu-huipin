import asyncio
import hashlib
from collections import defaultdict
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

import app.execution as execution_module
from app.aurora_main_image_planner import AuroraPlanningError
from app.domain import (
    AttemptStatus,
    JobItemStatus,
    JobStatus,
    JobType,
    OutboxStatus,
    PlanValidationStatus,
    UsageCompleteness,
)
from app.execution import ExecutionCoordinator
from app.models import (
    AmazonSuiteReviewAttemptRecord,
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    OutboxEventRecord,
    UsageEventRecord,
    utc_now,
)
from app.outbox import publish_pending_events
from app.provider import FakeProvider, ProviderOutcome, ProviderResult

WORKSPACE_ID = "ws_test"


def test_provider_failure_receipt_survives_attempt_item_and_usage(client):
    job_id = _create_job(client)
    coordinator = _coordinator(client, FakeProvider(), "receipt-worker")
    assert coordinator.prepare(job_id) == JobStatus.PROCESSING
    lease = coordinator.claim_next_item(job_id)
    assert lease is not None and coordinator.mark_provider_started(lease)
    error = {
        "code": "invalid_image",
        "param": "image",
        "message": "Unsupported encoding",
    }
    assert coordinator.finish_attempt(
        lease,
        ProviderResult(
            outcome=ProviderOutcome.FAILED,
            error_code="IMAGE_REQUEST_FAILED",
            http_status=400,
            provider_request_id="req-fixture",
            retryable=False,
            raw_usage={
                "metrics": {
                    "provider_error": error,
                    "provider_attempts": [
                        {
                            "route": "primary",
                            "host": "fixture.example",
                            "model": "fixture",
                            "http_status": 400,
                            "request_id": "req-fixture",
                            "error": error,
                        }
                    ],
                }
            },
        ),
    )
    with client.app.state.session_factory() as session:
        attempt = session.get(JobAttemptRecord, lease.attempt_id)
        item = session.get(JobItemRecord, attempt.job_item_id)
        usage = session.scalar(
            select(UsageEventRecord).where(
                UsageEventRecord.attempt_id == lease.attempt_id
            )
        )
        assert attempt.error_details["provider_error"] == error
        assert item.error_details["provider_attempts"][0]["http_status"] == 400
        assert usage.raw_usage["metrics"]["provider_error"] == error
    assert coordinator.finalize_job(job_id) == JobStatus.FAILED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job.error_code == "IMAGE_REQUEST_FAILED"
        assert job.error_details["provider_error"] == error
    detail = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["error_code"] == "IMAGE_REQUEST_FAILED"
    assert detail.json()["error_details"]["provider_error"] == error


def _image_payload(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _create_job(client: TestClient) -> str:
    asset_ids: list[str] = []
    for index, media_kind in enumerate(["image", "mask"]):
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": media_kind},
            files={
                "file": (
                    f"input-{index}.png",
                    _image_payload((20 + index, 40, 60)),
                    "image/png",
                )
            },
        )
        assert response.status_code == 201
        asset_ids.append(response.json()["id"])
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "type": "local_edit",
            "intent": "submit",
            "input": {
                "source_asset_id": asset_ids[0],
                "selection_mask_asset_id": asset_ids[1],
            },
            "configuration": {
                "instruction": "reliability test",
                "quality": "balanced",
                "output_format": "png",
            },
        },
    )
    assert created.status_code == 202
    return str(created.json()["id"])


def _coordinator(
    client: TestClient,
    provider: FakeProvider,
    worker_id: str,
) -> ExecutionCoordinator:
    return ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        worker_id=worker_id,
        lease_seconds=2,
        heartbeat_seconds=0.2,
    )


@pytest.mark.parametrize("blocking_phase", ["provider", "storage"])
def test_heartbeat_survives_blocking_provider_and_slow_artifact_save(
    client, blocking_phase, monkeypatch,
):
    import threading

    from app.provider import ProviderArtifact

    phase_active = threading.Event()
    renewed_during_phase = threading.Event()

    class BlockingProvider(FakeProvider):
        name = "real-test"

        async def execute(self, request):
            if blocking_phase == "provider":
                phase_active.set()
                try:
                    # The event loop is blocked here: only the keeper thread
                    # can renew. Wait for positive evidence, not a subsecond
                    # expiry that depends on full-suite CPU/GC scheduling.
                    assert renewed_during_phase.wait(timeout=3)
                finally:
                    phase_active.clear()
            payload = _image_payload((10, 20, 30))
            return ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_name=self.name,
                artifact=ProviderArtifact(
                    payload=payload, filename="saved.png", content_type="image/png",
                    width=32, height=32, sha256=hashlib.sha256(payload).hexdigest(),
                ),
            )

    class SlowStorage:
        async def save_validated(self, image):
            if blocking_phase == "storage":
                phase_active.set()
                try:
                    assert await asyncio.to_thread(
                        renewed_during_phase.wait, timeout=3
                    )
                finally:
                    phase_active.clear()
            return SimpleNamespace(
                **image.__dict__, byte_size=len(image.payload), storage_provider="test",
                storage_bucket="test", object_version="", etag=None,
            )

        async def delete_object(self, *args):
            pass

    job_id = _create_job(client)
    coordinator = ExecutionCoordinator(client.app.state.session_factory,
        BlockingProvider(), worker_id="blocking-test", lease_seconds=5,
        heartbeat_seconds=0.05, artifact_storage=SlowStorage())
    original_renew = coordinator.renew_lease

    def observe_renew(lease):
        renewed = original_renew(lease)
        if renewed and phase_active.is_set():
            renewed_during_phase.set()
        return renewed

    monkeypatch.setattr(coordinator, "renew_lease", observe_renew)
    coordinator.prepare(job_id)
    lease = coordinator.claim_next_item(job_id)
    assert lease is not None
    assert asyncio.run(coordinator.execute_lease(lease)) is True
    assert renewed_during_phase.is_set()
    with client.app.state.session_factory() as session:
        assert session.get(JobAttemptRecord, lease.attempt_id).status == "succeeded"


class _FailingDomesticPlanner:
    async def plan(self, job_id: str) -> JobStatus:
        raise OSError(f"workspace unavailable for {job_id}")


class _SetAuditTrackingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.set_audit_calls = 0

    async def audit_parallel_set(self, job_id: str) -> bool:
        self.set_audit_calls += 1
        return True


class _ExplodingSetAuditProvider(FakeProvider):
    async def audit_parallel_set(self, job_id: str) -> bool:
        error = AuroraPlanningError(
            "Aurora v2.0 set audit gateway returned HTTP 502."
        )
        error.http_status = 502
        raise error


class _TransientSetAuditProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.set_audit_calls = 0

    async def audit_parallel_set(self, job_id: str) -> bool:
        del job_id
        self.set_audit_calls += 1
        if self.set_audit_calls < 3:
            raise AuroraPlanningError("temporary set-audit response failure")
        return True


class _BlockedSetAuditProvider(FakeProvider):
    async def audit_parallel_set(self, job_id: str) -> bool:
        del job_id
        return False


def test_parallel_aurora_skips_set_audit_when_any_page_failed(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert job is not None and first is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first.status = JobItemStatus.SUCCEEDED.value
        for ordinal in range(1, 6):
            session.add(
                JobItemRecord(
                    id=f"item_{uuid4().hex}",
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    item_key=f"page-{ordinal + 1:03d}",
                    ordinal=ordinal,
                    status=(
                        JobItemStatus.FAILED.value
                        if ordinal == 1
                        else JobItemStatus.SUCCEEDED.value
                    ),
                    configuration={},
                )
            )
        session.commit()

    provider = _SetAuditTrackingProvider()
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        worker_id="parallel-audit-skip",
    )

    assert asyncio.run(coordinator._run_parallel_aurora_set_audit(job_id)) == (
        None,
        None,
    )
    assert provider.set_audit_calls == 0


def _six_succeeded_aurora_pages(client: TestClient) -> str:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert job is not None and first is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first.status = JobItemStatus.SUCCEEDED.value
        for ordinal in range(1, 6):
            session.add(
                JobItemRecord(
                    id=f"item_{uuid4().hex}",
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    item_key=f"page-{ordinal + 1:03d}",
                    ordinal=ordinal,
                    status=JobItemStatus.SUCCEEDED.value,
                    configuration={},
                )
            )
        session.commit()
    return job_id


def test_parallel_aurora_set_audit_failure_records_its_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-4: a swallowed set-audit exception made 32 failures undiagnosable."""

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(execution_module.asyncio, "sleep", no_sleep)
    job_id = _six_succeeded_aurora_pages(client)
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        _ExplodingSetAuditProvider(),
        worker_id="parallel-audit-error",
    )

    applicable, error = asyncio.run(
        coordinator._run_parallel_aurora_set_audit(job_id)
    )

    assert applicable is False
    assert isinstance(error, AuroraPlanningError)

    coordinator._mark_parallel_aurora_set_audit_failed(job_id, error)

    with client.app.state.session_factory() as session:
        item = session.scalar(
            select(JobItemRecord).where(
                JobItemRecord.job_id == job_id,
                JobItemRecord.error_code == "AURORA_SET_AUDIT_FAILED",
            )
        )

    assert item is not None
    assert item.error_details["scope"] == "set_audit"
    assert item.error_details["reason"] == "AuroraPlanningError"
    assert item.error_details["http_status"] == 502
    assert "HTTP 502" in item.error_details["message"]


def test_parallel_aurora_set_audit_retries_transient_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(execution_module.asyncio, "sleep", no_sleep)
    job_id = _six_succeeded_aurora_pages(client)
    provider = _TransientSetAuditProvider()
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        worker_id="parallel-audit-retry",
    )

    assert asyncio.run(coordinator._run_parallel_aurora_set_audit(job_id)) == (
        True,
        None,
    )
    assert provider.set_audit_calls == 3


def test_parallel_aurora_set_audit_is_idle_after_final_review(
    client: TestClient,
) -> None:
    job_id = _six_succeeded_aurora_pages(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        job.status = JobStatus.AWAITING_FINAL_REVIEW.value
        job.stage = "awaiting_final_review"
        session.commit()

    provider = _SetAuditTrackingProvider()
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        worker_id="parallel-audit-final-review-idle",
    )

    assert asyncio.run(coordinator._run_parallel_aurora_set_audit(job_id)) == (
        None,
        None,
    )
    assert provider.set_audit_calls == 0


def test_v20_blocked_set_audit_waits_for_user_final_review(
    client: TestClient,
) -> None:
    job_id = _six_succeeded_aurora_pages(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        job.status = JobStatus.PROCESSING.value
        job.stage = "generating"
        session.add(
            JobPlanRecord(
                id=f"plan_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                payload={"execution_policy": {"execution_mode": "parallel_v20"}},
                payload_hash="0" * 64,
                validation_status=PlanValidationStatus.VALID.value,
            )
        )
        session.commit()

    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        _BlockedSetAuditProvider(),
        worker_id="parallel-audit-blocked",
    )

    assert asyncio.run(coordinator.run(job_id)) == JobStatus.AWAITING_FINAL_REVIEW
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        assert job.status == JobStatus.AWAITING_FINAL_REVIEW.value
        assert job.stage == "awaiting_final_review"


def test_domestic_planning_error_reaches_terminal_failure(client: TestClient) -> None:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        session.commit()

    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        campaign_planner=_FailingDomesticPlanner(),
    )

    assert asyncio.run(coordinator.run(job_id)) == JobStatus.FAILED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        items = session.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        ).all()
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.error_code == "AURORA_PLANNING_FAILED"
        assert job.history_expires_at is not None
        assert {item.status for item in items} == {JobItemStatus.FAILED.value}
        # RC-4: the class name alone left all 54 planning failures identical.
        assert job.error_details["reason"] == "OSError"
        assert "workspace unavailable" in job.error_details["message"]
        assert all(
            item.error_details["message"] == job.error_details["message"]
            for item in items
        )


class _ParallelAuroraFakeProvider:
    name = "aurora-fake"
    model = "none"
    pricing_version = "test"

    def __init__(self, permanently_failed_pages: set[int] | None = None) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: defaultdict[int, int] = defaultdict(int)
        self.idempotency_keys: defaultdict[int, list[str]] = defaultdict(list)
        self.permanently_failed_pages = permanently_failed_pages or set()

    async def execute(self, request):  # type: ignore[no-untyped-def]
        page = int(request.configuration["item"]["aurora_snapshot"]["page_index"])
        self.calls[page] += 1
        self.idempotency_keys[page].append(request.idempotency_key)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.03)
        self.active -= 1
        if page in self.permanently_failed_pages:
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                error_code="PERMANENT_PAGE_FAILURE",
                error_message="controlled page failure",
                retryable=False,
                usage_reported=False,
                raw_usage={"network_requests": 1},
            )
        if page == 2 and self.calls[page] == 1:
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                error_code="UPSTREAM_502",
                error_message="temporary unavailable",
                retryable=True,
                http_status=502,
                usage_reported=True,
                raw_usage={"network_requests": 1},
            )
        return ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id=f"fake:{page}:{self.calls[page]}",
            usage_reported=True,
            raw_usage={"network_requests": 1},
        )


def test_parallel_aurora_overlaps_six_pages_and_retries_only_failure(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id, with_for_update=True)
        assert job is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert first is not None
        first.item_key = "page-001"
        items = [first]
        for ordinal in range(1, 6):
            item = JobItemRecord(
                id=f"item_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                item_key=f"page-{ordinal + 1:03d}",
                ordinal=ordinal,
                status=JobItemStatus.PENDING.value,
                configuration={},
            )
            session.add(item)
            items.append(item)
        for item in items:
            item.configuration = {
                "aurora_execution_mode": "parallel_v20",
                "aurora_snapshot": {
                    "page_index": item.ordinal,
                    "plan_revision": 1,
                    "page_revision": 0,
                },
            }
        now = utc_now()
        session.add(
            JobPlanRecord(
                id=f"plan_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                schema_version="aurora-main-image-plan-v2",
                payload={
                    "execution_policy": {"execution_mode": "parallel_v20"},
                },
                payload_hash="0" * 64,
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version="aurora-harness-2.0.0",
                validated_at=now,
                confirmed_at=now,
            )
        )
        session.commit()

    provider = _ParallelAuroraFakeProvider()
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        worker_id="parallel-aurora",
        max_concurrency=6,
        max_aurora_retries=2,
        aurora_retry_backoff_base_seconds=0.01,
        aurora_retry_backoff_max_seconds=0.01,
    )

    assert asyncio.run(coordinator.run(job_id)) == JobStatus.COMPLETED
    assert provider.max_active == 6
    assert provider.calls == {0: 1, 1: 1, 2: 2, 3: 1, 4: 1, 5: 1}
    assert len(set(provider.idempotency_keys[2])) == 1


def test_parallel_page_zero_failure_does_not_block_other_pages(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id, with_for_update=True)
        assert job is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert first is not None
        first.item_key = "page-001"
        items = [first]
        for ordinal in range(1, 6):
            item = JobItemRecord(
                id=f"item_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                item_key=f"page-{ordinal + 1:03d}",
                ordinal=ordinal,
                status=JobItemStatus.PENDING.value,
                configuration={},
            )
            session.add(item)
            items.append(item)
        for item in items:
            item.configuration = {
                "aurora_execution_mode": "parallel_v20",
                "aurora_snapshot": {"page_index": item.ordinal},
            }
        now = utc_now()
        session.add(
            JobPlanRecord(
                id=f"plan_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                schema_version="aurora-main-image-plan-v2",
                payload={"execution_policy": {"execution_mode": "parallel_v20"}},
                payload_hash="0" * 64,
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version="aurora-harness-2.0.0",
                validated_at=now,
                confirmed_at=now,
            )
        )
        session.commit()

    provider = _ParallelAuroraFakeProvider(permanently_failed_pages={0})
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        provider,
        worker_id="parallel-page-zero-failure",
        max_concurrency=6,
    )

    assert asyncio.run(coordinator.run(job_id)) == JobStatus.PARTIAL
    assert set(provider.calls) == set(range(6))
    with client.app.state.session_factory() as session:
        later = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job_id, JobItemRecord.ordinal >= 1)
            .order_by(JobItemRecord.ordinal)
        ).all()
        assert {item.status for item in later} == {
            JobItemStatus.SUCCEEDED.value
        }


def test_parallel_aurora_claims_all_pages_before_style_calibration(
    client: TestClient,
) -> None:
    """Parallel mode dispatches all frozen pages without a two-page gate."""

    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert job is not None and first is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first.ordinal = 0
        first.configuration = {
            "aurora_execution_mode": "parallel_v20",
            "aurora_snapshot": {"page_index": 0},
        }
        for ordinal in range(1, 6):
            session.add(
                JobItemRecord(
                    id=f"item_{uuid4().hex}",
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    item_key=f"page-{ordinal + 1:03d}",
                    ordinal=ordinal,
                    status=JobItemStatus.PENDING.value,
                    configuration={
                        "aurora_execution_mode": "parallel_v20",
                        "aurora_snapshot": {"page_index": ordinal},
                    },
                )
            )
        now = utc_now()
        session.add(
            JobPlanRecord(
                id=f"plan_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                schema_version="aurora-main-image-plan-v2",
                payload={"execution_policy": {"execution_mode": "parallel_v20"}},
                payload_hash="0" * 64,
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version="aurora-harness-2.0.0",
                validated_at=now,
                confirmed_at=now,
            )
        )
        session.commit()

    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="parallel-style-gate-claim",
        max_concurrency=6,
    )

    claimed = [
        lease
        for lease in (coordinator.claim_next_item(job_id) for _ in range(6))
        if lease is not None
    ]

    assert [
        lease.configuration["item"]["aurora_snapshot"]["page_index"]
        for lease in claimed
    ] == list(range(6))


def test_calibrated_parallel_aurora_claims_only_released_snapshots(
    client: TestClient,
) -> None:
    """The shadow mode cannot dispatch page 1 before page 0 calibration."""

    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert job is not None and first is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first.ordinal = 0
        first.configuration = {
            "aurora_execution_mode": "parallel_v20_calibrated",
            "aurora_snapshot": {"page_index": 0},
        }
        for ordinal in range(1, 6):
            session.add(
                JobItemRecord(
                    id=f"item_{uuid4().hex}",
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    item_key=f"page-{ordinal + 1:03d}",
                    ordinal=ordinal,
                    status=JobItemStatus.PENDING.value,
                    configuration={
                        "aurora_execution_mode": "parallel_v20_calibrated",
                    },
                )
            )
        now = utc_now()
        session.add(
            JobPlanRecord(
                id=f"plan_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                schema_version="aurora-main-image-plan-v2",
                payload={
                    "execution_policy": {
                        "execution_mode": "parallel_v20_calibrated"
                    }
                },
                payload_hash="0" * 64,
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version="aurora-harness-2.0.0",
                validated_at=now,
                confirmed_at=now,
            )
        )
        session.commit()

    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="parallel-calibrated-gate",
        max_concurrency=6,
    )
    first_lease = coordinator.claim_next_item(job_id)
    assert first_lease is not None
    assert first_lease.configuration["item"]["aurora_snapshot"]["page_index"] == 0
    assert coordinator.claim_next_item(job_id) is None


def _expand_job_to_three_items(client: TestClient, job_id: str) -> None:
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        job.expected_item_count = 3
        for ordinal in (1, 2):
            session.add(
                JobItemRecord(
                    id=f"item_{uuid4().hex}",
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    item_key=f"result-{ordinal + 1}",
                    ordinal=ordinal,
                    status=JobItemStatus.PENDING.value,
                    configuration={},
                )
            )
        session.commit()


def _counts(client: TestClient, job_id: str) -> tuple[int, int]:
    with client.app.state.session_factory() as session:
        attempts = session.scalar(
            select(func.count())
            .select_from(JobAttemptRecord)
            .where(JobAttemptRecord.job_id == job_id)
        )
        usage = session.scalar(
            select(func.count())
            .select_from(UsageEventRecord)
            .where(UsageEventRecord.job_id == job_id)
        )
    return int(attempts or 0), int(usage or 0)


def _prepare_amazon_accounting_job(
    client: TestClient,
    *,
    suffix: str,
    payload_review_usage: list[dict[str, object]],
) -> tuple[str, ExecutionCoordinator]:
    job_id = _create_job(client)
    coordinator = _coordinator(client, FakeProvider(), f"accounting-{suffix}")
    assert coordinator.prepare(job_id) == JobStatus.PROCESSING
    payload = {
        "business_quality": {"status": "passed"},
        "planner_request_count": 0,
        "usage": {
            "planner": [],
            "amazon_suite_review": payload_review_usage,
        },
    }
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert job is not None and item is not None
        job.type = JobType.AMAZON_VISUAL_SUITE.value
        item.status = JobItemStatus.SUCCEEDED.value
        item.stage = "completed"
        item.finished_at = utc_now()
        session.add(
            JobPlanRecord(
                id=f"plan_{suffix}_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                payload=payload,
                payload_hash="a" * 64,
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
            )
        )
        session.commit()
    return job_id, coordinator


def _add_amazon_review_attempt(
    client: TestClient,
    job_id: str,
    *,
    review_round: int,
    status: str,
    reported: bool,
    actual_cost_micros: int | None,
    estimated_cost_micros: int | None,
    provider: str = "xinghe",
    provider_call_started: bool = True,
    error_code: str | None = None,
) -> None:
    now = utc_now()
    with client.app.state.session_factory() as session:
        session.add(
            AmazonSuiteReviewAttemptRecord(
                id=f"asr_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                review_round=review_round,
                output_asset_set_hash=f"{review_round:064x}",
                provider=provider,
                provider_idempotency_key=(
                    f"{job_id}:suite-review:{review_round}:{uuid4().hex}"
                ),
                status=status,
                lease_token=uuid4().hex,
                leased_by="accounting-test",
                lease_expires_at=now + timedelta(minutes=15),
                provider_request_id=(
                    f"review-request-{review_round}" if provider != "mock" else None
                ),
                raw_usage={"input_tokens": 10} if reported else {},
                normalized_usage={"reported": reported},
                actual_cost_micros=actual_cost_micros,
                estimated_cost_micros=estimated_cost_micros,
                error_code=error_code,
                started_at=now,
                provider_call_started_at=now if provider_call_started else None,
                finished_at=now if status != AttemptStatus.RUNNING.value else None,
            )
        )
        session.commit()


def _add_provider_reservation(
    client: TestClient,
    job_id: str,
    *,
    reserved_cost_micros: int = 4_530_000,
    reserved_request_count: int = 45,
) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            AuditEventRecord(
                workspace_id=WORKSPACE_ID,
                action="provider.beta_job_reserved",
                resource_type="job",
                resource_id=job_id,
                request_id=job_id,
                details={
                    "ledger_version": 2,
                    "job_type": JobType.AMAZON_VISUAL_SUITE.value,
                    "planner_reserve_micros": 250_000,
                    "image_budget_micros": 1_890_000,
                    "group_count": 0,
                    "estimated_cost_micros": reserved_cost_micros,
                    "provider_request_count": reserved_request_count,
                    "daily_limit_micros": 10_000_000,
                    "monthly_limit_micros": 20_000_000,
                    "daily_request_limit": 100,
                },
            )
        )
        session.commit()


def test_ten_concurrent_deliveries_execute_provider_once(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    providers = [FakeProvider(delay_seconds=0.05) for _ in range(10)]
    coordinators = [
        _coordinator(client, provider, f"worker-{index}")
        for index, provider in enumerate(providers)
    ]

    async def deliver() -> list[JobStatus | None]:
        return await asyncio.gather(
            *(coordinator.run(job_id) for coordinator in coordinators)
        )

    asyncio.run(deliver())
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED.value
    assert sum(provider.call_count for provider in providers) == 1
    assert _counts(client, job_id) == (1, 1)


def test_non_domestic_jobs_keep_configured_parallel_claims(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    _expand_job_to_three_items(client, job_id)
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="parallel-delivery",
        lease_seconds=2,
        heartbeat_seconds=0.2,
        max_concurrency=6,
    )
    assert coordinator.prepare(job_id) == JobStatus.PROCESSING

    first = coordinator.claim_next_item(job_id)
    second = coordinator.claim_next_item(job_id)
    assert first is not None
    assert second is not None
    with client.app.state.session_factory() as session:
        ordinals = {
            session.get(JobItemRecord, lease.item_id).ordinal
            for lease in (first, second)
        }
    assert ordinals == {0, 1}


def test_domestic_pages_wait_for_predecessor_across_duplicate_deliveries(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    _expand_job_to_three_items(client, job_id)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.status = JobStatus.PROCESSING.value
        job.stage = "processing"
        session.commit()

    first = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="aurora-first-delivery",
        lease_seconds=2,
        heartbeat_seconds=0.2,
        max_concurrency=6,
    )
    duplicate = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="aurora-duplicate-delivery",
        lease_seconds=2,
        heartbeat_seconds=0.2,
        max_concurrency=6,
    )
    first_page = first.claim_next_item(job_id)
    assert first_page is not None
    with client.app.state.session_factory() as session:
        claimed = session.get(JobItemRecord, first_page.item_id)
        assert claimed is not None
        assert claimed.ordinal == 0

    # A redelivered process_job must not claim page 2 while page 1 is active,
    # even though the normal site concurrency remains six.
    assert duplicate.claim_next_item(job_id) is None

    assert asyncio.run(first.execute_lease(first_page))
    second_page = duplicate.claim_next_item(job_id)
    assert second_page is not None
    with client.app.state.session_factory() as session:
        claimed = session.get(JobItemRecord, second_page.item_id)
        assert claimed is not None
        assert claimed.ordinal == 1


def test_domestic_retry_with_reused_prefix_claims_next_pending_page(
    client: TestClient,
) -> None:
    """REUSED pages count as completed for sequential retry ordering."""

    job_id = _create_job(client)
    _expand_job_to_three_items(client, job_id)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        items = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job_id)
            .order_by(JobItemRecord.ordinal)
        ).all()
        assert job is not None and len(items) == 3
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.status = JobStatus.PROCESSING.value
        job.stage = "processing"
        items[0].status = JobItemStatus.REUSED.value
        items[1].status = JobItemStatus.PENDING.value
        items[2].status = JobItemStatus.PENDING.value
        session.commit()

    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="aurora-retry-order",
        lease_seconds=2,
        heartbeat_seconds=0.2,
        max_concurrency=6,
    )
    lease = coordinator.claim_next_item(job_id)
    assert lease is not None
    with client.app.state.session_factory() as session:
        claimed = session.get(JobItemRecord, lease.item_id)
        assert claimed is not None
        assert claimed.ordinal == 1


def test_parallel_page_failure_does_not_skip_other_pages(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        first = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert job is not None and first is not None
        job.type = JobType.DOMESTIC_MAIN_IMAGE.value
        job.expected_item_count = 6
        first.status = JobItemStatus.SUCCEEDED.value
        first.ordinal = 0
        for ordinal in range(1, 6):
            session.add(
                JobItemRecord(
                    id=f"item_{uuid4().hex}",
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    item_key=f"page-{ordinal + 1:03d}",
                    ordinal=ordinal,
                    status=(
                        JobItemStatus.FAILED.value
                        if ordinal == 1
                        else JobItemStatus.PENDING.value
                    ),
                    configuration={},
                )
            )
        now = utc_now()
        session.add(
            JobPlanRecord(
                id=f"plan_{uuid4().hex}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                schema_version="aurora-main-image-plan-v2",
                payload={
                    "execution_policy": {"execution_mode": "parallel_v20"}
                },
                payload_hash="0" * 64,
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version="aurora-harness-2.0.0",
                validated_at=now,
                confirmed_at=now,
            )
        )
        session.commit()

    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="parallel-style-gate",
    )
    coordinator._skip_parallel_pages_after_style_failure(job_id)
    with client.app.state.session_factory() as session:
        later = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job_id, JobItemRecord.ordinal >= 2)
            .order_by(JobItemRecord.ordinal)
        ).all()
        assert len(later) == 4
        assert {item.status for item in later} == {JobItemStatus.PENDING.value}
        assert {item.error_code for item in later} == {None}


def test_running_job_aggregates_follow_terminal_item_statuses(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    _expand_job_to_three_items(client, job_id)
    coordinator = _coordinator(client, FakeProvider(), "aggregate-worker")
    assert coordinator.prepare(job_id) == JobStatus.PROCESSING

    first = coordinator.claim_next_item(job_id)
    assert first is not None
    assert asyncio.run(coordinator.execute_lease(first))
    after_first = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
    assert after_first["status"] == JobStatus.PROCESSING.value
    assert after_first["succeeded_item_count"] == 1
    assert after_first["failed_item_count"] == 0
    assert after_first["progress"] == 46

    second = coordinator.claim_next_item(job_id)
    assert second is not None
    assert coordinator.mark_provider_started(second)
    assert coordinator.finish_attempt(
        second,
        ProviderResult(
            outcome=ProviderOutcome.FAILED,
            error_code="TEST_FAILURE",
            error_message="Controlled test failure.",
            retryable=False,
        ),
    )
    after_second = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
    assert after_second["succeeded_item_count"] == 1
    assert after_second["failed_item_count"] == 1
    assert after_second["progress"] == 73

    third = coordinator.claim_next_item(job_id)
    assert third is not None
    assert asyncio.run(coordinator.execute_lease(third))
    before_finalize = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}"
    ).json()
    assert before_finalize["status"] == JobStatus.PROCESSING.value
    assert before_finalize["succeeded_item_count"] == 2
    assert before_finalize["failed_item_count"] == 1
    assert before_finalize["progress"] == 99

    assert coordinator.finalize_job(job_id) == JobStatus.PARTIAL
    finalized = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
    assert finalized["progress"] == 100


def test_expired_claim_is_released_to_one_new_worker_without_new_attempt(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    old_provider = FakeProvider()
    new_provider = FakeProvider()
    old = _coordinator(client, old_provider, "old-worker")
    new = _coordinator(client, new_provider, "new-worker")
    assert old.prepare(job_id) == JobStatus.PROCESSING
    old_lease = old.claim_next_item(job_id)
    assert old_lease is not None

    with client.app.state.session_factory() as session:
        attempt = session.get(JobAttemptRecord, old_lease.attempt_id)
        assert attempt is not None
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()

    new_lease = new.claim_next_item(job_id)
    assert new_lease is not None
    assert new_lease.attempt_id == old_lease.attempt_id
    assert new_lease.lease_token != old_lease.lease_token
    assert old.mark_provider_started(old_lease) is False
    assert asyncio.run(new.execute_lease(new_lease)) is True
    assert new.finalize_job(job_id) == JobStatus.COMPLETED
    assert old_provider.call_count == 0
    assert new_provider.call_count == 1
    assert _counts(client, job_id) == (1, 1)


def test_expired_running_attempt_becomes_unknown_and_stale_finish_is_fenced(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    old = _coordinator(client, FakeProvider(), "old-worker")
    recovery = _coordinator(client, FakeProvider(), "recovery-worker")
    assert old.prepare(job_id) == JobStatus.PROCESSING
    lease = old.claim_next_item(job_id)
    assert lease is not None
    assert old.mark_provider_started(lease) is True

    with client.app.state.session_factory() as session:
        attempt = session.get(JobAttemptRecord, lease.attempt_id)
        assert attempt is not None
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()

    assert recovery.claim_next_item(job_id) is None
    assert (
        old.finish_attempt(
            lease,
            ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id="stale-result",
            ),
        )
        is False
    )
    assert recovery.finalize_job(job_id) == JobStatus.FAILED
    with client.app.state.session_factory() as session:
        attempt = session.get(JobAttemptRecord, lease.attempt_id)
        item = session.get(JobItemRecord, lease.item_id)
        job = session.get(JobRecord, job_id)
        assert attempt is not None and item is not None and job is not None
        assert attempt.status == AttemptStatus.OUTCOME_UNKNOWN.value
        assert item.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert job.actual_cost_micros is None
    assert _counts(client, job_id) == (1, 0)


@pytest.mark.parametrize(
    "item_status",
    [
        JobItemStatus.PENDING.value,
        JobItemStatus.QUEUED.value,
        JobItemStatus.RUNNING.value,
    ],
)
def test_nonterminal_item_prevents_job_terminalization(
    client: TestClient,
    item_status: str,
) -> None:
    job_id = _create_job(client)
    coordinator = _coordinator(client, FakeProvider(), "worker")
    assert coordinator.prepare(job_id) == JobStatus.PROCESSING
    with client.app.state.session_factory() as session:
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert item is not None
        item.status = item_status
        session.commit()
    assert coordinator.finalize_job(job_id) == JobStatus.PROCESSING


def test_cancel_and_finish_have_serializable_results(client: TestClient) -> None:
    cancelled_job_id = _create_job(client)
    cancelled = _coordinator(client, FakeProvider(), "cancel-first")
    assert cancelled.prepare(cancelled_job_id) == JobStatus.PROCESSING
    cancelled_lease = cancelled.claim_next_item(cancelled_job_id)
    assert cancelled_lease is not None
    assert cancelled.mark_provider_started(cancelled_lease)
    assert cancelled.cancel(cancelled_job_id) == JobStatus.CANCELLED
    assert not cancelled.finish_attempt(
        cancelled_lease,
        ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id="too-late",
        ),
    )

    finished_job_id = _create_job(client)
    finished = _coordinator(client, FakeProvider(), "finish-first")
    assert finished.prepare(finished_job_id) == JobStatus.PROCESSING
    finished_lease = finished.claim_next_item(finished_job_id)
    assert finished_lease is not None
    assert asyncio.run(finished.execute_lease(finished_lease))
    assert finished.finalize_job(finished_job_id) == JobStatus.COMPLETED
    assert finished.cancel(finished_job_id) == JobStatus.COMPLETED


def test_published_outbox_delivery_is_recovered_from_postgres(
    client: TestClient,
) -> None:
    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        event = session.scalar(
            select(OutboxEventRecord).where(OutboxEventRecord.aggregate_id == job_id)
        )
        assert event is not None
        event.status = OutboxStatus.PUBLISHED.value
        event.published_at = utc_now() - timedelta(seconds=31)
        session.commit()

    published = publish_pending_events(
        client.app.state.settings,
        client.app.state.session_factory,
        client.app.state.task_runner,
    )
    assert published == 1
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        event = session.scalar(
            select(OutboxEventRecord).where(OutboxEventRecord.aggregate_id == job_id)
        )
        assert job is not None and event is not None
        assert job.status == JobStatus.COMPLETED.value
        assert event.status == OutboxStatus.PUBLISHED.value
    assert _counts(client, job_id) == (1, 1)


@pytest.mark.parametrize("state", ["active", "reserved", "unknown", "lost"])
def test_celery_delivery_recovery_does_not_redispatch_live_provider_calls(
    client, monkeypatch, state,
):
    from app.celery_app import celery_app

    job_id = _create_job(client)
    with client.app.state.session_factory() as session:
        event = session.scalar(select(OutboxEventRecord).where(
            OutboxEventRecord.aggregate_id == job_id,
        ))
        event.status = OutboxStatus.PUBLISHED.value
        event.published_at = utc_now() - timedelta(minutes=10)
        session.commit()
    task = {"name": "polaris.process_job", "args": [job_id]}
    inspector = SimpleNamespace(
        active=lambda: None if state == "unknown" else {
            "worker": [task] if state == "active" else [],
        },
        reserved=lambda: {"worker": [task] if state == "reserved" else []},
    )
    monkeypatch.setattr(celery_app.control, "inspect", lambda **kwargs: inspector)
    dispatched = []
    monkeypatch.setattr(
        "app.outbox._dispatch", lambda *args, **kwargs: dispatched.append(kwargs),
    )
    settings = client.app.state.settings.model_copy(
        update={"task_dispatcher": "celery"},
    )
    published = publish_pending_events(
        settings, client.app.state.session_factory, client.app.state.task_runner,
    )
    assert published == (1 if state == "lost" else 0)
    assert len(dispatched) == published


def test_finalize_amazon_review_uses_terminal_attempts_without_payload_double_count(
    client: TestClient,
) -> None:
    job_id, coordinator = _prepare_amazon_accounting_job(
        client,
        suffix="durable",
        payload_review_usage=[
            {
                "reported": True,
                "provider_request_id": "duplicated-plan-review",
                "actual_cost_micros": 9_999,
                "estimated_cost_micros": 9_999,
            }
        ],
    )
    for review_round, status, reported, actual, estimated in (
        (1, AttemptStatus.SUCCEEDED.value, True, 11, 101),
        (2, AttemptStatus.FAILED.value, True, 13, 103),
        (3, AttemptStatus.OUTCOME_UNKNOWN.value, False, 17, 107),
        (4, AttemptStatus.RUNNING.value, True, 1_000, 1_000),
    ):
        _add_amazon_review_attempt(
            client,
            job_id,
            review_round=review_round,
            status=status,
            reported=reported,
            actual_cost_micros=actual,
            estimated_cost_micros=estimated,
        )

    assert coordinator.finalize_job(job_id) == JobStatus.COMPLETED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        assert job.actual_cost_micros == 41
        assert job.api_request_count == 3
        assert job.usage_completeness == UsageCompleteness.PARTIAL.value


def test_finalize_amazon_review_excludes_preparation_failure_from_request_accounting(
    client: TestClient,
) -> None:
    job_id, coordinator = _prepare_amazon_accounting_job(
        client,
        suffix="preparation-failed",
        payload_review_usage=[
            {
                "reported": True,
                "provider_request_id": "must-not-fall-back",
                "actual_cost_micros": 9_999,
            }
        ],
    )
    _add_amazon_review_attempt(
        client,
        job_id,
        review_round=1,
        status=AttemptStatus.FAILED.value,
        reported=False,
        actual_cost_micros=None,
        estimated_cost_micros=None,
        provider_call_started=False,
        error_code="AMAZON_SUITE_REVIEW_PREPARATION_FAILED",
    )

    assert coordinator.finalize_job(job_id) == JobStatus.COMPLETED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        assert job.actual_cost_micros is None
        assert job.api_request_count == 0
        assert job.usage_completeness == UsageCompleteness.NONE.value


def test_finalize_amazon_review_keeps_estimated_only_unknown_cost_incomplete(
    client: TestClient,
) -> None:
    job_id, coordinator = _prepare_amazon_accounting_job(
        client,
        suffix="estimated-only",
        payload_review_usage=[],
    )
    _add_amazon_review_attempt(
        client,
        job_id,
        review_round=1,
        status=AttemptStatus.OUTCOME_UNKNOWN.value,
        reported=False,
        actual_cost_micros=None,
        estimated_cost_micros=29,
    )

    assert coordinator.finalize_job(job_id) == JobStatus.COMPLETED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        assert job.actual_cost_micros is None
        assert job.api_request_count == 1
        assert job.usage_completeness == UsageCompleteness.NONE.value


def test_finalize_amazon_review_falls_back_to_legacy_plan_usage(
    client: TestClient,
) -> None:
    job_id, coordinator = _prepare_amazon_accounting_job(
        client,
        suffix="legacy",
        payload_review_usage=[
            {
                "reported": True,
                "provider_request_id": "legacy-review-request",
                "actual_cost_micros": 7,
                "estimated_cost_micros": 9,
            }
        ],
    )

    assert coordinator.finalize_job(job_id) == JobStatus.COMPLETED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        assert job is not None
        assert job.actual_cost_micros == 7
        assert job.api_request_count == 1
        assert job.usage_completeness == UsageCompleteness.COMPLETE.value


def test_finalize_amazon_review_merges_mixed_evidence_into_quota_settlement(
    client: TestClient,
) -> None:
    job_id, coordinator = _prepare_amazon_accounting_job(
        client,
        suffix="mixed-settlement",
        payload_review_usage=[
            {
                "review_round": 1,
                "reported": True,
                "provider_request_id": "legacy-review-1",
                "actual_cost_micros": 7,
                "estimated_cost_micros": 70,
            },
            {
                "review_round": 2,
                "reported": True,
                "provider_request_id": "review-request-2",
                "actual_cost_micros": 9_999,
                "estimated_cost_micros": 9_999,
            },
            {
                "review_round": 3,
                "reported": True,
                "provider_request_id": "legacy-review-3",
                "actual_cost_micros": 13,
                "estimated_cost_micros": 130,
            },
            {
                "review_round": 5,
                "reported": True,
                "provider_request_id": "must-not-fall-back",
                "actual_cost_micros": 9_999,
                "estimated_cost_micros": 9_999,
            },
        ],
    )
    _add_amazon_review_attempt(
        client,
        job_id,
        review_round=2,
        status=AttemptStatus.SUCCEEDED.value,
        reported=True,
        actual_cost_micros=11,
        estimated_cost_micros=110,
    )
    _add_amazon_review_attempt(
        client,
        job_id,
        review_round=4,
        status=AttemptStatus.SUCCEEDED.value,
        reported=True,
        actual_cost_micros=17,
        estimated_cost_micros=170,
        provider="mock",
    )
    _add_amazon_review_attempt(
        client,
        job_id,
        review_round=5,
        status=AttemptStatus.FAILED.value,
        reported=False,
        actual_cost_micros=None,
        estimated_cost_micros=None,
        provider_call_started=False,
        error_code="AMAZON_SUITE_REVIEW_PREPARATION_FAILED",
    )
    _add_provider_reservation(client, job_id)

    assert coordinator.finalize_job(job_id) == JobStatus.COMPLETED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        attempts_event = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action == "provider.beta_job_attempts_recorded",
            )
        )
        release = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action == "provider.beta_job_released",
            )
        )
        assert job is not None
        assert settlement is not None
        assert attempts_event is not None
        assert release is not None
        assert job.actual_cost_micros == 48
        assert job.api_request_count == 3
        assert job.usage_completeness == UsageCompleteness.COMPLETE.value
        assert settlement.details["actual_provider_request_count"] == 3
        assert settlement.details["charged_cost_micros"] == 31
        assert settlement.details["actual_cost_reported_count"] == 3
        assert settlement.details["estimated_fallback_count"] == 0
        assert settlement.details["conservative_unknown_count"] == 0
        review_sources = [
            source
            for source in attempts_event.details["attempts"]
            if source["source"].startswith("amazon_suite_review_")
        ]
        assert [source["review_round"] for source in review_sources] == [1, 2, 3]
        assert [source["source"] for source in review_sources] == [
            "amazon_suite_review_legacy",
            "amazon_suite_review_durable",
            "amazon_suite_review_legacy",
        ]
        assert release.details["released_request_count"] == 42
        assert release.details["released_cost_micros"] == 4_529_969


@pytest.mark.parametrize(
    ("reserved_cost_micros", "reserved_request_count"),
    [(4_530_000, 1), (1, 45)],
)
def test_finalize_amazon_usage_above_estimates_is_recorded(
    client: TestClient,
    reserved_cost_micros: int,
    reserved_request_count: int,
) -> None:
    job_id, coordinator = _prepare_amazon_accounting_job(
        client,
        suffix=f"overflow-{reserved_cost_micros}-{reserved_request_count}",
        payload_review_usage=[
            {
                "review_round": 1,
                "reported": True,
                "provider_request_id": "legacy-overflow-1",
                "actual_cost_micros": 2,
            },
            {
                "review_round": 2,
                "reported": True,
                "provider_request_id": "legacy-overflow-2",
                "actual_cost_micros": 3,
            },
        ],
    )
    _add_provider_reservation(
        client,
        job_id,
        reserved_cost_micros=reserved_cost_micros,
        reserved_request_count=reserved_request_count,
    )

    assert coordinator.finalize_job(job_id) == JobStatus.COMPLETED

    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        assert job is not None
        assert job.status == JobStatus.COMPLETED.value
        assert settlement is not None
        assert settlement.details["enforcement_mode"] == "observe_only"
