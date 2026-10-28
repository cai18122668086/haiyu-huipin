from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.amazon_visual_quality as quality_module
from app.amazon_visual_quality import (
    AmazonSuiteQualityService,
    AmazonSuiteReviewOutcome,
    _findings,
    _Output,
    _Sheet,
    _Snapshot,
)
from app.amazon_visual_suite_provider import AMAZON_ITEM_KEYS
from app.domain import (
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    AttemptStatus,
    JobAssetDirection,
    JobItemStatus,
    JobStatus,
    JobType,
    PlanValidationStatus,
)
from app.execution import ExecutionCoordinator
from app.models import (
    AmazonSuiteReviewAttemptRecord,
    AssetRecord,
    JobAssetRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from app.provider import FakeProvider
from app.services import canonical_hash, transition_job
from app.xinghe_agent import (
    AmazonVisualReviewCallResult,
    AmazonVisualReviewRequest,
)
from tests.test_api import WORKSPACE_ID


class _Protocol:
    def prompt_payload(self) -> dict[str, Any]:
        return {"protocol": "test"}


@dataclass
class _BlockingGateway:
    result: AmazonVisualReviewCallResult | None = None
    provider_name: str = "fake-amazon-review"
    calls: list[AmazonVisualReviewRequest] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def review_amazon(
        self, request: AmazonVisualReviewRequest
    ) -> AmazonVisualReviewCallResult:
        self.calls.append(request)
        self.started.set()
        await self.release.wait()
        return self.result or _passed_result(len(self.calls))


@dataclass
class _ImmediateGateway:
    result: AmazonVisualReviewCallResult | None = None
    provider_name: str = "fake-amazon-review"
    calls: list[AmazonVisualReviewRequest] = field(default_factory=list)

    async def review_amazon(
        self, request: AmazonVisualReviewRequest
    ) -> AmazonVisualReviewCallResult:
        self.calls.append(request)
        return self.result or _passed_result(len(self.calls))


@dataclass
class _UnavailableGateway:
    retryable: bool
    provider_name: str = "fake-amazon-review"
    calls: list[AmazonVisualReviewRequest] = field(default_factory=list)

    async def review_amazon(
        self, request: AmazonVisualReviewRequest
    ) -> AmazonVisualReviewCallResult:
        self.calls.append(request)
        return AmazonVisualReviewCallResult(
            visual_success=None,
            provider_request_id=f"review-unavailable-{len(self.calls)}",
            raw_usage={"input_tokens": 10},
            normalized_usage={"reported": True, "input_tokens": 10},
            actual_cost_micros=25,
            estimated_cost_micros=25,
            error_code="AMAZON_VISUAL_REVIEW_INVALID",
            error_message="review unavailable",
            retryable=self.retryable,
        )


class _InProgressReviewer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def review(self, job_id: str) -> AmazonSuiteReviewOutcome:
        self.calls.append(job_id)
        return AmazonSuiteReviewOutcome("in_progress", 1)


class _UnusedProvider:
    name = "unused"


def _passed_result(sequence: int = 1) -> AmazonVisualReviewCallResult:
    return AmazonVisualReviewCallResult(
        visual_success=True,
        provider_request_id=f"review-{sequence}",
        raw_usage={"input_tokens": 10},
        normalized_usage={"reported": True, "input_tokens": 10},
        actual_cost_micros=25,
        estimated_cost_micros=25,
        summary="passed",
    )


def _finding_result() -> AmazonVisualReviewCallResult:
    return AmazonVisualReviewCallResult(
        visual_success=False,
        provider_request_id="review-findings",
        raw_usage={"input_tokens": 10},
        normalized_usage={"reported": True, "input_tokens": 10},
        actual_cost_micros=25,
        estimated_cost_micros=25,
        summary="Review found a possible layout issue.",
        reason_codes=("POSSIBLE_LAYOUT_ISSUE",),
        failed_item_keys=(AMAZON_ITEM_KEYS[0],),
        retry_guidance="Replace the original Planner instruction.",
    )


def _unavailable_result() -> AmazonVisualReviewCallResult:
    return AmazonVisualReviewCallResult(
        visual_success=None,
        provider_request_id="review-unavailable-blocked",
        raw_usage={"input_tokens": 10},
        normalized_usage={"reported": True, "input_tokens": 10},
        actual_cost_micros=25,
        estimated_cost_micros=25,
        error_code="AMAZON_VISUAL_REVIEW_REQUEST_FAILED",
        error_message="temporary review failure",
        retryable=True,
    )


def _seed_snapshot(client: TestClient, suffix: str) -> _Snapshot:
    job_id = f"job_amazon_review_{suffix}"
    plan_id = f"plan_amazon_review_{suffix}"
    payload: dict[str, Any] = {"groups": [], "pages": []}
    with client.app.state.session_factory() as session:
        session.add(
            JobRecord(
                id=job_id,
                workspace_id=WORKSPACE_ID,
                type=JobType.AMAZON_VISUAL_SUITE.value,
                status=JobStatus.PROCESSING.value,
                stage="suite_review",
                configuration={},
                expected_item_count=len(AMAZON_ITEM_KEYS),
                succeeded_item_count=len(AMAZON_ITEM_KEYS),
            )
        )
        session.add(
            JobPlanRecord(
                id=plan_id,
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                payload=payload,
                payload_hash=canonical_hash(payload),
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
            )
        )
        outputs: list[_Output] = []
        for index, item_key in enumerate(AMAZON_ITEM_KEYS):
            item = JobItemRecord(
                id=f"item_{suffix}_{index}",
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                item_key=item_key,
                ordinal=index,
                status=JobItemStatus.SUCCEEDED.value,
                configuration={},
            )
            sha256 = f"{index + 1:064x}"
            asset = AssetRecord(
                id=f"asset_{suffix}_{index}",
                workspace_id=WORKSPACE_ID,
                media_kind=AssetMediaKind.IMAGE.value,
                origin_kind=AssetOriginKind.GENERATED.value,
                lifecycle_status=AssetLifecycleStatus.READY.value,
                original_filename=f"{item_key}.png",
                content_type="image/png",
                byte_size=1,
                width=1,
                height=1,
                sha256=sha256,
                storage_provider="memory",
                storage_bucket="test",
                object_key=f"unused/{suffix}/{item_key}.png",
                object_version="",
                asset_metadata={},
            )
            session.add_all([item, asset])
            session.flush()
            link = JobAssetRecord(
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                job_item_id=item.id,
                asset_id=asset.id,
                direction=JobAssetDirection.OUTPUT.value,
                role="generated_output",
                ordinal=0,
                is_deliverable=True,
                validation={},
            )
            session.add(link)
            session.flush()
            outputs.append(
                _Output(
                    job_asset_id=link.id,
                    item_id=item.id,
                    item_key=item_key,
                    ordinal=index,
                    filename=asset.original_filename,
                    content_type=asset.content_type,
                    sha256=sha256,
                    object_key=asset.object_key,
                    validation={},
                    asset_metadata={},
                )
            )
        session.commit()
    output_rows = tuple(outputs)
    return _Snapshot(
        workspace_id=WORKSPACE_ID,
        job_id=job_id,
        plan_id=plan_id,
        payload=payload,
        outputs=output_rows,
        inputs=(),
        review_round=0,
        output_hash=canonical_hash(
            [{"item_key": row.item_key, "sha256": row.sha256} for row in output_rows]
        ),
        input_hash=canonical_hash([]),
    )


def _set_review_state(
    client: TestClient,
    snapshot: _Snapshot,
    *,
    status: str,
    review_round: int,
    output_hash: str,
) -> None:
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert plan is not None
        payload = dict(plan.payload)
        payload["business_quality"] = {
            "status": status,
            "review_round": review_round,
            "output_asset_set_hash": output_hash,
        }
        plan.payload = payload
        plan.payload_hash = canonical_hash(payload)
        session.commit()


def _demote_output(client: TestClient, output: _Output) -> None:
    with client.app.state.session_factory() as session:
        item = session.get(JobItemRecord, output.item_id)
        link = session.get(JobAssetRecord, output.job_asset_id)
        assert item is not None and link is not None
        item.status = JobItemStatus.PENDING.value
        link.is_deliverable = False
        session.commit()


def _replace_output(
    client: TestClient,
    snapshot: _Snapshot,
    *,
    item_key: str,
    generation: int,
) -> _Snapshot:
    old = next(row for row in snapshot.outputs if row.item_key == item_key)
    with client.app.state.session_factory() as session:
        item = session.get(JobItemRecord, old.item_id)
        old_link = session.get(JobAssetRecord, old.job_asset_id)
        assert item is not None and old_link is not None
        old_link.is_deliverable = False
        sha256 = f"{generation + 1000:064x}"
        asset = AssetRecord(
            id=f"asset_{snapshot.job_id}_{item_key}_{generation}",
            workspace_id=WORKSPACE_ID,
            media_kind=AssetMediaKind.IMAGE.value,
            origin_kind=AssetOriginKind.GENERATED.value,
            lifecycle_status=AssetLifecycleStatus.READY.value,
            original_filename=f"{item_key}-{generation}.png",
            content_type="image/png",
            byte_size=1,
            width=1,
            height=1,
            sha256=sha256,
            storage_provider="memory",
            storage_bucket="test",
            object_key=f"unused/{snapshot.job_id}/{item_key}-{generation}.png",
            object_version="",
            asset_metadata={},
        )
        session.add(asset)
        session.flush()
        link = JobAssetRecord(
            workspace_id=WORKSPACE_ID,
            job_id=snapshot.job_id,
            job_item_id=item.id,
            asset_id=asset.id,
            direction=JobAssetDirection.OUTPUT.value,
            role="generated_output",
            ordinal=generation,
            is_deliverable=True,
            validation={},
        )
        session.add(link)
        item.status = JobItemStatus.SUCCEEDED.value
        session.flush()
        replacement = replace(
            old,
            job_asset_id=link.id,
            filename=asset.original_filename,
            sha256=sha256,
            object_key=asset.object_key,
        )
        session.commit()
    outputs = tuple(
        replacement if row.item_key == item_key else row for row in snapshot.outputs
    )
    return replace(
        snapshot,
        outputs=outputs,
        output_hash=canonical_hash(
            [{"item_key": row.item_key, "sha256": row.sha256} for row in outputs]
        ),
    )


def _service(
    client: TestClient,
    gateway: Any,
    snapshot: _Snapshot,
    *,
    worker_id: str,
    lease_seconds: float = 30,
    enforce_delivery: bool = False,
    max_review_rounds: int = 1,
) -> AmazonSuiteQualityService:
    service = AmazonSuiteQualityService(
        client.app.state.session_factory,
        gateway,
        object(),
        skill_root=Path("."),
        manifest_validator=lambda _skill_id: "a" * 64,
        max_review_rounds=max_review_rounds,
        enforce_delivery=enforce_delivery,
        worker_id=worker_id,
        review_lease_seconds=lease_seconds,
    )
    service._snapshot = lambda _job_id: snapshot  # type: ignore[method-assign]

    async def read_outputs(_rows: Any) -> tuple[bytes, ...]:
        return tuple(b"unused" for _ in AMAZON_ITEM_KEYS)

    async def persist_sheet(_snapshot: Any, _round: int, _sheet: Any) -> str:
        return f"contact-{snapshot.job_id}"

    async def read_inputs(_rows: Any) -> tuple[Any, ...]:
        return ()

    service._read_outputs = read_outputs  # type: ignore[method-assign]
    service._persist_sheet = persist_sheet  # type: ignore[method-assign]
    service._read_inputs = read_inputs  # type: ignore[method-assign]
    return service


@pytest.fixture(autouse=True)
def _stub_review_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        quality_module,
        "_contact_sheet",
        lambda _rows, _payloads: _Sheet(b"sheet", 1, 1, "a" * 64),
    )
    monkeypatch.setattr(
        quality_module,
        "load_skill_protocol",
        lambda *_args, **_kwargs: _Protocol(),
    )


def test_same_job_round_is_claimed_once_under_concurrent_delivery(
    client: TestClient,
) -> None:
    asyncio.run(_same_round_concurrency_scenario(client))


async def _same_round_concurrency_scenario(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "concurrent")
    gateway = _BlockingGateway()
    first_service = _service(client, gateway, snapshot, worker_id="review-worker-first")
    second_service = _service(
        client, gateway, snapshot, worker_id="review-worker-second"
    )

    first_task = asyncio.create_task(first_service.review(snapshot.job_id))
    await asyncio.wait_for(gateway.started.wait(), timeout=2)
    second = await second_service.review(snapshot.job_id)

    assert second.action == "in_progress"
    assert len(gateway.calls) == 1
    with client.app.state.session_factory() as session:
        assert (
            session.scalar(
                select(func.count(AmazonSuiteReviewAttemptRecord.id)).where(
                    AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
                )
            )
            == 1
        )
        attempt = session.scalar(
            select(AmazonSuiteReviewAttemptRecord).where(
                AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
            )
        )
        assert attempt is not None
        assert attempt.status == AttemptStatus.RUNNING.value
        assert attempt.provider_call_started_at is not None

    gateway.release.set()
    first = await asyncio.wait_for(first_task, timeout=2)
    assert first.action == "passed"
    assert gateway.calls[0].idempotency_key == (
        f"{snapshot.job_id}:suite-review:1:{snapshot.output_hash}"
    )


def test_claim_is_fenced_after_job_enters_cancelling(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "claim-cancelled")
    service = _service(
        client,
        _ImmediateGateway(),
        snapshot,
        worker_id="review-claim-cancelled",
    )
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert job is not None and plan is not None
        payload_before = dict(plan.payload)
        transition_job(job, JobStatus.CANCELLING, progress=job.progress)
        session.commit()

    claim = service._claim_review(snapshot)

    assert claim.action == "fenced"
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert job is not None and job.status == JobStatus.CANCELLING.value
        assert plan is not None and plan.payload == payload_before
        assert (
            session.scalar(
                select(func.count(AmazonSuiteReviewAttemptRecord.id)).where(
                    AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
                )
            )
            == 0
        )


@pytest.mark.parametrize("result_kind", ["passed", "unavailable"])
def test_cancel_during_provider_call_fences_late_review_result(
    client: TestClient, result_kind: str
) -> None:
    asyncio.run(_cancel_during_provider_call_scenario(client, result_kind))


async def _cancel_during_provider_call_scenario(
    client: TestClient, result_kind: str
) -> None:
    snapshot = _seed_snapshot(client, f"cancel-provider-{result_kind}")
    gateway = _BlockingGateway(
        result=_unavailable_result() if result_kind == "unavailable" else None
    )
    service = _service(
        client,
        gateway,
        snapshot,
        worker_id=f"review-cancel-provider-{result_kind}",
    )

    review_task = asyncio.create_task(service.review(snapshot.job_id))
    await asyncio.wait_for(gateway.started.wait(), timeout=2)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        attempt = session.scalar(
            select(AmazonSuiteReviewAttemptRecord).where(
                AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
            )
        )
        assert job is not None and plan is not None and attempt is not None
        payload_before = dict(plan.payload)
        original_lease_token = attempt.lease_token
        assert attempt.provider_call_started_at is not None
        transition_job(job, JobStatus.CANCELLING, progress=job.progress)
        session.commit()

    gateway.release.set()
    outcome = await asyncio.wait_for(review_task, timeout=2)

    assert outcome.action == "not_applicable"
    assert len(gateway.calls) == 1
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        attempt = session.scalar(
            select(AmazonSuiteReviewAttemptRecord).where(
                AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
            )
        )
        assert job is not None and job.status == JobStatus.CANCELLING.value
        assert plan is not None and plan.payload == payload_before
        assert attempt is not None
        assert attempt.status == AttemptStatus.CANCELLED.value
        assert attempt.lease_token != original_lease_token
        assert attempt.error_code == "AMAZON_SUITE_REVIEW_CANCELLED"
        assert attempt.provider_request_id is None
        assert all(
            item.status == JobItemStatus.SUCCEEDED.value
            for item in session.scalars(
                select(JobItemRecord).where(JobItemRecord.job_id == snapshot.job_id)
            ).all()
        )


@pytest.mark.parametrize("result_kind", ["apply", "unavailable"])
def test_terminal_job_rejects_review_mutations(
    client: TestClient, result_kind: str
) -> None:
    snapshot = _seed_snapshot(client, f"terminal-{result_kind}")
    service = _service(
        client,
        _ImmediateGateway(),
        snapshot,
        worker_id=f"review-terminal-{result_kind}",
    )
    claim = service._claim_review(snapshot)
    assert claim.action == "claimed"

    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert job is not None and plan is not None
        payload_before = dict(plan.payload)
        transition_job(job, JobStatus.COMPLETED, progress=100)
        session.commit()

    if result_kind == "apply":
        outcome = service._apply(
            snapshot,
            claim.review_round,
            "contact-terminal",
            _passed_result(),
            attempt_id=str(claim.attempt_id),
            lease_token=str(claim.lease_token),
        )
    else:
        outcome = service._unavailable(
            snapshot,
            claim.review_round,
            "contact-terminal",
            "AMAZON_VISUAL_REVIEW_REQUEST_FAILED",
            "temporary review failure",
            _unavailable_result(),
            attempt_id=str(claim.attempt_id),
            lease_token=str(claim.lease_token),
        )

    assert outcome.action == "not_applicable"
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        attempt = session.get(AmazonSuiteReviewAttemptRecord, claim.attempt_id)
        assert job is not None and job.status == JobStatus.COMPLETED.value
        assert plan is not None and plan.payload == payload_before
        assert attempt is not None
        assert attempt.status == AttemptStatus.FAILED.value
        assert attempt.error_code == "AMAZON_SUITE_REVIEW_JOB_TERMINAL"
        assert attempt.provider_request_id is None


def test_passed_output_hash_is_replayed_without_starting_round_two(
    client: TestClient,
) -> None:
    asyncio.run(_passed_replay_scenario(client))


async def _passed_replay_scenario(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "replay")
    gateway = _ImmediateGateway()
    first_service = _service(client, gateway, snapshot, worker_id="review-worker-one")
    second_service = _service(client, gateway, snapshot, worker_id="review-worker-two")

    first = await first_service.review(snapshot.job_id)
    second = await second_service.review(snapshot.job_id)

    assert first.action == "passed"
    assert second.action == "passed"
    assert second.review_round == 1
    assert len(gateway.calls) == 1
    with client.app.state.session_factory() as session:
        attempts = session.scalars(
            select(AmazonSuiteReviewAttemptRecord).where(
                AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
            )
        ).all()
        assert len(attempts) == 1
        assert attempts[0].review_round == 1
        assert attempts[0].status == AttemptStatus.SUCCEEDED.value


def test_review_findings_are_read_only_and_do_not_demote_outputs(
    client: TestClient,
) -> None:
    snapshot = _seed_snapshot(client, "read-only-findings")
    with client.app.state.session_factory() as session:
        item = session.get(JobItemRecord, snapshot.outputs[0].item_id)
        assert item is not None
        item.configuration = {
            "new_skill_adapter": {
                "prompt_input": {"reference_guidance": "original planner guidance"}
            }
        }
        session.commit()

    duplicate_outputs = (
        snapshot.outputs[0],
        replace(snapshot.outputs[1], sha256=snapshot.outputs[0].sha256),
        *snapshot.outputs[2:],
    )
    findings, deterministic_failed = _findings(duplicate_outputs)
    assert deterministic_failed == ()
    assert findings[0]["severity"] == "warning"

    gateway = _ImmediateGateway(result=_finding_result())
    service = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-read-only-findings",
    )
    outcome = asyncio.run(service.review(snapshot.job_id))

    assert outcome.action == "failed"
    assert outcome.failed_item_keys == (AMAZON_ITEM_KEYS[0],)
    assert len(gateway.calls) == 1
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        item = session.get(JobItemRecord, snapshot.outputs[0].item_id)
        links = session.scalars(
            select(JobAssetRecord).where(
                JobAssetRecord.job_id == snapshot.job_id,
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
            )
        ).all()
        assert plan is not None and item is not None
        quality = plan.payload["business_quality"]
        assert quality["status"] == "warning"
        assert quality["technical_delivery_status"] == "completed"
        assert quality["automatic_redraw_enabled"] is False
        assert item.status == JobItemStatus.SUCCEEDED.value
        assert item.configuration["new_skill_adapter"]["prompt_input"] == {
            "reference_guidance": "original planner guidance"
        }
        assert all(link.is_deliverable for link in links)


@pytest.mark.parametrize(
    "initial_status", [JobStatus.PROCESSING, JobStatus.AWAITING_FINAL_REVIEW]
)
def test_enforced_review_schedules_only_failed_items_for_bounded_redraw(
    client: TestClient,
    initial_status: JobStatus,
) -> None:
    snapshot = _seed_snapshot(client, "enforced-redraw")
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id)
        job.status = initial_status.value
        session.commit()
    gateway = _ImmediateGateway(result=_finding_result())
    service = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-enforced-redraw",
        enforce_delivery=True,
        max_review_rounds=2,
    )

    outcome = asyncio.run(service.review(snapshot.job_id))

    assert outcome.action == "retry_items"
    assert outcome.review_round == 1
    assert outcome.failed_item_keys == (AMAZON_ITEM_KEYS[0],)
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        items = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == snapshot.job_id)
            .order_by(JobItemRecord.ordinal)
        ).all()
        links = session.scalars(
            select(JobAssetRecord).where(
                JobAssetRecord.job_id == snapshot.job_id,
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
            )
        ).all()
        assert plan is not None
        quality = plan.payload["business_quality"]
        assert quality["status"] == "redraw_pending"
        job = session.get(JobRecord, snapshot.job_id)
        assert job.status == JobStatus.PROCESSING.value
        assert job.succeeded_item_count == len(AMAZON_ITEM_KEYS) - 1
        assert quality["delivery_status"] == "redraw_pending"
        assert quality["max_suite_review_rounds"] == 2
        assert plan.payload["controls"]["automatic_redraw_enabled"] is True
        assert items[0].status == JobItemStatus.PENDING.value
        assert items[0].stage == "qa_redraw"
        assert items[0].configuration["qa_redraw"]["review_round"] == 2
        assert all(item.status == JobItemStatus.SUCCEEDED.value for item in items[1:])
        item_by_id = {item.id: item for item in items}
        assert all(
            (not link.is_deliverable)
            if item_by_id[link.job_item_id].item_key == AMAZON_ITEM_KEYS[0]
            else link.is_deliverable
            for link in links
        )


@pytest.mark.parametrize("result_kind", ["apply", "unavailable"])
def test_review_result_is_fenced_after_deliverable_output_changes(
    client: TestClient,
    result_kind: str,
) -> None:
    snapshot = _seed_snapshot(client, f"stale-result-{result_kind}")
    _set_review_state(
        client,
        snapshot,
        status="needs_review",
        review_round=0,
        output_hash=snapshot.output_hash,
    )
    review_snapshot = _replace_output(
        client,
        snapshot,
        item_key=snapshot.outputs[0].item_key,
        generation=1,
    )
    service = _service(
        client,
        _ImmediateGateway(),
        review_snapshot,
        worker_id=f"review-stale-result-{result_kind}",
    )
    claim = service._claim_review(review_snapshot)
    assert claim.action == "claimed"
    assert claim.review_round == 1

    _replace_output(
        client,
        review_snapshot,
        item_key=review_snapshot.outputs[0].item_key,
        generation=2,
    )
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert plan is not None
        payload_before = dict(plan.payload)

    if result_kind == "apply":
        outcome = service._apply(
            review_snapshot,
            claim.review_round,
            "contact-stale-output",
            _passed_result(),
            attempt_id=str(claim.attempt_id),
            lease_token=str(claim.lease_token),
        )
    else:
        outcome = service._unavailable(
            review_snapshot,
            claim.review_round,
            "contact-stale-output",
            "AMAZON_VISUAL_REVIEW_REQUEST_FAILED",
            "temporary review failure",
            AmazonVisualReviewCallResult(
                visual_success=None,
                provider_request_id="review-stale-unavailable",
                raw_usage={"input_tokens": 10},
                normalized_usage={"reported": True, "input_tokens": 10},
                actual_cost_micros=25,
                estimated_cost_micros=25,
                error_code="AMAZON_VISUAL_REVIEW_REQUEST_FAILED",
                error_message="temporary review failure",
                retryable=True,
            ),
            attempt_id=str(claim.attempt_id),
            lease_token=str(claim.lease_token),
        )

    assert outcome.action == "in_progress"
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        attempt = session.get(AmazonSuiteReviewAttemptRecord, claim.attempt_id)
        assert plan is not None
        assert plan.payload == payload_before
        assert "usage" not in plan.payload
        assert attempt is not None
        assert attempt.status == AttemptStatus.FAILED.value
        assert attempt.error_code == "AMAZON_SUITE_REVIEW_STALE_OUTPUT"


def test_review_infrastructure_failure_is_reported_once_without_blocking_items(
    client: TestClient,
) -> None:
    asyncio.run(_review_infrastructure_failure_scenario(client))


async def _review_infrastructure_failure_scenario(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "review-unavailable")
    gateway = _UnavailableGateway(retryable=True)
    service = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-unavailable",
    )

    first = await service.review(snapshot.job_id)
    second = await service.review(snapshot.job_id)

    assert first.action == "failed"
    assert second.action == "failed"
    assert first.review_round == second.review_round == 1
    assert len(gateway.calls) == 2
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        items = session.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == snapshot.job_id)
        ).all()
        links = session.scalars(
            select(JobAssetRecord).where(
                JobAssetRecord.job_id == snapshot.job_id,
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
            )
        ).all()
        assert plan is not None
        quality = plan.payload["business_quality"]
        assert quality["status"] == "review_infrastructure_failed"
        assert quality["technical_delivery_status"] == "completed"
        assert quality["automatic_redraw_enabled"] is False
        assert all(item.status == JobItemStatus.SUCCEEDED.value for item in items)
        assert all(link.is_deliverable for link in links)


def test_active_review_does_not_block_coordinator_finalize(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_active_review_scenario(client, monkeypatch))


async def _active_review_scenario(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = _InProgressReviewer()
    coordinator = ExecutionCoordinator(
        client.app.state.session_factory,
        _UnusedProvider(),  # type: ignore[arg-type]
        amazon_suite_reviewer=reviewer,
    )
    monkeypatch.setattr(coordinator, "prepare", lambda _job_id: JobStatus.PROCESSING)
    monkeypatch.setattr(coordinator, "claim_next_item", lambda _job_id: None)
    monkeypatch.setattr(coordinator, "_is_amazon_job", lambda _job_id: True)

    finalized: list[str] = []

    def finalize(_job_id: str) -> JobStatus:
        finalized.append(_job_id)
        return JobStatus.COMPLETED

    monkeypatch.setattr(coordinator, "finalize_job", finalize)

    result = await coordinator.run("job-active-review")

    assert result == JobStatus.COMPLETED
    assert finalized == ["job-active-review"]
    assert reviewer.calls == []


def test_stale_lease_token_cannot_apply_review_result(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "fencing")
    gateway = _ImmediateGateway()
    service = _service(client, gateway, snapshot, worker_id="review-owner")
    claim = service._claim_review(snapshot)

    stale = service._apply(
        snapshot,
        claim.review_round,
        "contact-stale",
        _passed_result(),
        attempt_id=str(claim.attempt_id),
        lease_token="stale-token",
    )

    assert stale.action == "in_progress"
    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        attempt = session.get(AmazonSuiteReviewAttemptRecord, claim.attempt_id)
        assert plan is not None
        assert "business_quality" not in plan.payload
        assert attempt is not None
        assert attempt.status == AttemptStatus.RUNNING.value
        assert attempt.provider_request_id is None

    applied = service._apply(
        snapshot,
        claim.review_round,
        "contact-owner",
        _passed_result(),
        attempt_id=str(claim.attempt_id),
        lease_token=str(claim.lease_token),
    )
    assert applied.action == "passed"


def test_expired_pre_call_attempt_is_safely_reclaimed(
    client: TestClient,
) -> None:
    asyncio.run(_expired_pre_call_reclaim_scenario(client))


async def _expired_pre_call_reclaim_scenario(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "expired-pre-call")
    gateway = _ImmediateGateway()
    owner = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-expired-owner",
        lease_seconds=0.01,
    )
    claim = owner._claim_review(snapshot)
    assert claim.action == "claimed"
    await asyncio.sleep(0.03)
    observer = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-expired-observer",
        lease_seconds=30,
    )

    outcome = await observer.review(snapshot.job_id)

    assert outcome.action == "passed"
    assert len(gateway.calls) == 1
    with client.app.state.session_factory() as session:
        attempt = session.get(AmazonSuiteReviewAttemptRecord, claim.attempt_id)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.SUCCEEDED.value
        assert attempt.lease_token != claim.lease_token
        assert attempt.leased_by == "review-expired-observer"
        assert attempt.provider_call_started_at is not None
        assert attempt.error_code is None
        assert plan is not None
        assert plan.payload["business_quality"]["status"] == "passed"


def test_expired_attempt_after_provider_start_becomes_outcome_unknown(
    client: TestClient,
) -> None:
    asyncio.run(_expired_started_attempt_scenario(client))


async def _expired_started_attempt_scenario(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "expired-started")
    gateway = _ImmediateGateway()
    owner = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-started-owner",
    )
    claim = owner._claim_review(snapshot)
    assert claim.action == "claimed"
    with client.app.state.session_factory() as session:
        attempt = session.get(AmazonSuiteReviewAttemptRecord, claim.attempt_id)
        assert attempt is not None
        attempt.provider_call_started_at = utc_now()
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()

    observer = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-started-observer",
        lease_seconds=30,
    )
    outcome = await observer.review(snapshot.job_id)

    assert outcome.action == "failed"
    assert gateway.calls == []
    with client.app.state.session_factory() as session:
        attempt = session.get(AmazonSuiteReviewAttemptRecord, claim.attempt_id)
        plan = session.get(JobPlanRecord, snapshot.plan_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.OUTCOME_UNKNOWN.value
        assert attempt.provider_call_started_at is not None
        assert attempt.error_code == "AMAZON_SUITE_REVIEW_OUTCOME_UNKNOWN"
        assert plan is not None
        quality = plan.payload["business_quality"]
        assert quality["status"] == "review_infrastructure_failed"
        assert quality["failure_kind"] == "review_infrastructure_failure"


def test_slow_local_preparation_renews_pre_call_lease(
    client: TestClient,
) -> None:
    asyncio.run(_slow_local_preparation_scenario(client))


async def _slow_local_preparation_scenario(client: TestClient) -> None:
    snapshot = _seed_snapshot(client, "slow-preparation")
    gateway = _ImmediateGateway()
    service = _service(
        client,
        gateway,
        snapshot,
        worker_id="review-slow-preparation",
        lease_seconds=0.01,
    )

    async def delayed_inputs(_rows: Any) -> tuple[Any, ...]:
        await asyncio.sleep(0.03)
        return ()

    service._read_inputs = delayed_inputs  # type: ignore[method-assign]

    outcome = await service.review(snapshot.job_id)

    assert outcome.action == "passed"
    assert len(gateway.calls) == 1
    with client.app.state.session_factory() as session:
        attempt = session.scalar(
            select(AmazonSuiteReviewAttemptRecord).where(
                AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
            )
        )
        assert attempt is not None
        assert attempt.status == AttemptStatus.SUCCEEDED.value
        assert attempt.provider_call_started_at is not None


def test_transient_suite_review_recovers_without_redrawing_images(
    client: TestClient,
) -> None:
    snapshot = _seed_snapshot(client, "transient-review-recovery")

    class Gateway(_ImmediateGateway):
        async def review_amazon(
            self, request: AmazonVisualReviewRequest
        ) -> AmazonVisualReviewCallResult:
            self.calls.append(request)
            if len(self.calls) == 1:
                return replace(_unavailable_result(), retryable=True, http_status=503)
            return _passed_result()

    gateway = Gateway()
    service = _service(client, gateway, snapshot, worker_id="review-recovery")
    result = asyncio.run(service.review(snapshot.job_id))
    assert result.action == "passed"
    assert [call.route_attempt for call in gateway.calls] == [0, 1]
    assert len({call.idempotency_key for call in gateway.calls}) == 1
    with client.app.state.session_factory() as session:
        attempt = session.scalar(
            select(AmazonSuiteReviewAttemptRecord).where(
                AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id
            )
        )
        assert attempt.status == AttemptStatus.SUCCEEDED.value
        assert attempt.normalized_usage["network_requests"] == 2
        assert len(attempt.raw_usage["attempts"]) == 2
        items = session.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == snapshot.job_id)
        ).all()
        assert all(item.status == JobItemStatus.SUCCEEDED.value for item in items)


@pytest.mark.parametrize("failed_count", [1, len(AMAZON_ITEM_KEYS)])
def test_legacy_review_with_failed_items_becomes_retryable_without_generation(
    client: TestClient,
    failed_count: int,
) -> None:
    snapshot = _seed_snapshot(client, "legacy-failed-review")
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id)
        job.status = JobStatus.AWAITING_FINAL_REVIEW.value
        items = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job.id)
            .limit(failed_count)
        ).all()
        for item in items:
            item.status = JobItemStatus.FAILED.value
            item.error_code = "AMAZON_QA_REDRAW"
        session.commit()
    provider = FakeProvider()
    coordinator = ExecutionCoordinator(client.app.state.session_factory, provider)
    expected = (
        JobStatus.PARTIAL if failed_count < len(AMAZON_ITEM_KEYS) else JobStatus.FAILED
    )
    assert asyncio.run(coordinator.run(snapshot.job_id)) == expected
    assert provider.call_count == 0
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, snapshot.job_id)
        assert job.succeeded_item_count == len(AMAZON_ITEM_KEYS) - failed_count
        assert job.failed_item_count == failed_count
        assert job.finished_at is not None
