"""Durable, non-blocking Amazon whole-suite QA reporting."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .amazon_visual_suite_provider import (
    AMAZON_EXPECTED_ITEM_COUNT,
    AMAZON_ITEM_KEYS,
    AMAZON_PLANNING_POLICY_VERSION,
    AMAZON_VISUAL_SUITE_SKILL_ID,
)
from .domain import (
    TERMINAL_JOB_STATUSES,
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    AttemptStatus,
    JobAssetDirection,
    JobItemStatus,
    JobStatus,
    JobType,
)
from .models import (
    AmazonSuiteReviewAttemptRecord,
    AssetRecord,
    JobAssetRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from .services import (
    canonical_hash,
    fence_amazon_suite_review_attempts,
    new_id,
    transition_job,
)
from .skill_protocol import SkillProtocolError, load_skill_protocol
from .storage import ImageStorage, ValidatedImage
from .xinghe_agent import (
    AmazonVisualReviewCallResult,
    AmazonVisualReviewImage,
    AmazonVisualReviewRequest,
    SourceImage,
)

AmazonSuiteReviewAction = Literal[
    "passed",
    "retry_items",
    "retry_review",
    "in_progress",
    "failed",
    "incomplete",
    "not_applicable",
]

_REVIEW_INFRASTRUCTURE_FAILURE = "review_infrastructure_failure"
_REVIEW_INFRASTRUCTURE_FAILED = "review_infrastructure_failed"


class AmazonReviewGateway(Protocol):
    provider_name: str

    async def review_amazon(
        self, request: AmazonVisualReviewRequest
    ) -> AmazonVisualReviewCallResult: ...


@dataclass(frozen=True)
class AmazonSuiteReviewOutcome:
    action: AmazonSuiteReviewAction
    review_round: int = 0
    failed_item_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Output:
    job_asset_id: int
    item_id: str
    item_key: str
    ordinal: int
    filename: str
    content_type: str
    sha256: str
    object_key: str
    validation: dict[str, Any]
    asset_metadata: dict[str, Any]


@dataclass(frozen=True)
class _Input:
    asset_id: str
    role: str
    ordinal: int
    filename: str
    content_type: str
    sha256: str
    object_key: str


@dataclass(frozen=True)
class _Snapshot:
    workspace_id: str
    job_id: str
    plan_id: str
    payload: dict[str, Any]
    outputs: tuple[_Output, ...]
    inputs: tuple[_Input, ...]
    review_round: int
    output_hash: str
    input_hash: str


@dataclass(frozen=True)
class _Sheet:
    payload: bytes
    width: int
    height: int
    sha256: str


_ReviewClaimAction = Literal[
    "claimed",
    "in_progress",
    "replay_passed",
    "replay_failed",
    "outcome_unknown",
    "fenced",
]


@dataclass(frozen=True)
class _ReviewClaim:
    action: _ReviewClaimAction
    review_round: int
    attempt_id: str | None = None
    lease_token: str | None = None
    idempotency_key: str | None = None


class AmazonSuiteQualityService:
    """Persist a fenced whole-suite QA report without mutating delivery state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: AmazonReviewGateway,
        storage: ImageStorage,
        *,
        skill_root: Path,
        manifest_validator: Callable[[str], str],
        max_review_rounds: int = 1,
        enforce_delivery: bool = False,
        worker_id: str | None = None,
        review_lease_seconds: float = 900,
    ) -> None:
        if max_review_rounds not in {1, 2}:
            raise ValueError("max_review_rounds must be 1 or 2")
        if review_lease_seconds <= 0:
            raise ValueError("review_lease_seconds must be positive")
        self.session_factory = session_factory
        self.gateway = gateway
        self.storage = storage
        self.skill_root = skill_root
        self.manifest_validator = manifest_validator
        self.max_review_rounds = max_review_rounds
        # The standalone diagnostic service remains read-only by default.  The
        # production Amazon execution path opts in so a failed first review
        # schedules only the flagged items for one bounded redraw round and a
        # failed final review blocks delivery.
        self.enforce_delivery = enforce_delivery
        self.worker_id = worker_id or f"amazon-review-{uuid4().hex}"
        self.review_lease_seconds = review_lease_seconds

    async def review(self, job_id: str) -> AmazonSuiteReviewOutcome:
        try:
            snapshot = self._snapshot(job_id)
        except Exception as exc:
            self._record_snapshot_failure(job_id, exc)
            return AmazonSuiteReviewOutcome("failed")
        if snapshot is None:
            return AmazonSuiteReviewOutcome("not_applicable")
        if tuple(row.item_key for row in snapshot.outputs) != AMAZON_ITEM_KEYS:
            self._record_incomplete(snapshot)
            return AmazonSuiteReviewOutcome("incomplete", snapshot.review_round)

        claim = self._claim_review(snapshot)
        if claim.action == "replay_passed":
            return AmazonSuiteReviewOutcome("passed", claim.review_round)
        if claim.action == "replay_failed":
            return AmazonSuiteReviewOutcome("failed", claim.review_round)
        if claim.action == "in_progress":
            return AmazonSuiteReviewOutcome("in_progress", claim.review_round)
        if claim.action == "fenced":
            return AmazonSuiteReviewOutcome("not_applicable", claim.review_round)
        if claim.action == "outcome_unknown":
            return self._unavailable(
                snapshot,
                claim.review_round,
                self._contact_sheet_asset_id(snapshot.job_id, claim.review_round),
                "AMAZON_SUITE_REVIEW_OUTCOME_UNKNOWN",
                (
                    "The prior Amazon suite review may have reached the provider; "
                    "it will not be submitted again."
                ),
                attempt_id=cast(str, claim.attempt_id),
                lease_token=cast(str, claim.lease_token),
                attempt_status=AttemptStatus.OUTCOME_UNKNOWN.value,
                force_exhausted=True,
            )

        review_round = claim.review_round
        attempt_id = cast(str, claim.attempt_id)
        lease_token = cast(str, claim.lease_token)
        contact_asset_id: str | None = None
        try:
            payloads = await self._read_outputs(snapshot.outputs)
            sheet = _contact_sheet(snapshot.outputs, payloads)
            contact_asset_id = await self._persist_sheet(snapshot, review_round, sheet)
            references = await self._read_inputs(snapshot.inputs)
            source_hash = self.manifest_validator(AMAZON_VISUAL_SUITE_SKILL_ID)
            protocol = load_skill_protocol(
                AMAZON_VISUAL_SUITE_SKILL_ID,
                source_content_hash=source_hash,
                source_root=self.skill_root,
            )
        except (OSError, ValueError, SkillProtocolError) as exc:
            return self._unavailable(
                snapshot,
                review_round,
                contact_asset_id,
                "AMAZON_SUITE_REVIEW_PREPARATION_FAILED",
                f"Amazon suite review preparation failed ({type(exc).__name__}).",
                attempt_id=attempt_id,
                lease_token=lease_token,
            )
        except Exception as exc:
            return self._unavailable(
                snapshot,
                review_round,
                contact_asset_id,
                "AMAZON_SUITE_REVIEW_PREPARATION_FAILED",
                f"Amazon suite review preparation failed ({type(exc).__name__}).",
                attempt_id=attempt_id,
                lease_token=lease_token,
            )

        lease_state = self._provider_call_allowed(snapshot, attempt_id, lease_token)
        if lease_state == "lost":
            return AmazonSuiteReviewOutcome("in_progress", review_round)
        if lease_state == "expired":
            return self._unavailable(
                snapshot,
                review_round,
                contact_asset_id,
                "AMAZON_SUITE_REVIEW_OUTCOME_UNKNOWN",
                "The Amazon suite review lease expired before a safe provider call.",
                attempt_id=attempt_id,
                lease_token=lease_token,
                attempt_status=AttemptStatus.OUTCOME_UNKNOWN.value,
                force_exhausted=True,
            )

        try:
            result = await self._review_with_recovery(
                AmazonVisualReviewRequest(
                    workspace_id=snapshot.workspace_id,
                    job_id=snapshot.job_id,
                    scope="suite",
                    item_key=None,
                    references=references,
                    candidates=(
                        AmazonVisualReviewImage(
                            item_key="amazon-contact-sheet",
                            filename="amazon-suite-contact-sheet.jpg",
                            content_type="image/jpeg",
                            payload=sheet.payload,
                        ),
                    ),
                    generation_contract=_generation_contract(snapshot.payload),
                    skill_protocol=protocol.prompt_payload(),
                    idempotency_key=cast(str, claim.idempotency_key),
                    review_item_keys=AMAZON_ITEM_KEYS,
                ),
                snapshot=snapshot,
                attempt_id=attempt_id,
                lease_token=lease_token,
            )
        except Exception as exc:
            return self._unavailable(
                snapshot,
                review_round,
                contact_asset_id,
                "AMAZON_SUITE_REVIEW_OUTCOME_UNKNOWN",
                (
                    "The Amazon suite review request ended without a durable outcome "
                    f"({type(exc).__name__}); it will not be submitted again."
                ),
                attempt_id=attempt_id,
                lease_token=lease_token,
                attempt_status=AttemptStatus.OUTCOME_UNKNOWN.value,
                force_exhausted=True,
            )
        if not isinstance(result, AmazonVisualReviewCallResult):
            return self._unavailable(
                snapshot,
                review_round,
                contact_asset_id,
                "AMAZON_SUITE_REVIEW_INVALID",
                "Amazon suite review returned invalid data.",
                attempt_id=attempt_id,
                lease_token=lease_token,
                force_exhausted=True,
            )
        if result.visual_success is None:
            return self._unavailable(
                snapshot,
                review_round,
                contact_asset_id,
                result.error_code or "AMAZON_SUITE_REVIEW_UNAVAILABLE",
                result.error_message or "Amazon suite review is unavailable.",
                result,
                attempt_id=attempt_id,
                lease_token=lease_token,
            )
        return self._apply(
            snapshot,
            review_round,
            contact_asset_id,
            result,
            attempt_id=attempt_id,
            lease_token=lease_token,
        )

    async def _review_with_recovery(
        self,
        request: AmazonVisualReviewRequest,
        *,
        snapshot: _Snapshot,
        attempt_id: str,
        lease_token: str,
    ) -> AmazonVisualReviewCallResult:
        attempts: list[AmazonVisualReviewCallResult] = []
        route_limit = min(
            4, max(2, int(getattr(self.gateway, "visual_review_route_count", 2)))
        )
        for index in range(route_limit):
            if index and self._provider_call_allowed(
                snapshot, attempt_id, lease_token
            ) in {"lost", "expired"}:
                break
            result = await self.gateway.review_amazon(
                replace(request, route_attempt=index)
            )
            attempts.append(result)
            if result.visual_success is not None or not result.retryable:
                break
        if len(attempts) == 1:
            return result
        normalized: dict[str, Any] = {
            "reported": all(a.normalized_usage.get("reported") for a in attempts),
            "network_requests": len(attempts),
        }
        for key in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
            values = [a.normalized_usage.get(key) for a in attempts]
            normalized[key] = (
                sum(values) if all(isinstance(v, int) for v in values) else None
            )
        actual = [a.actual_cost_micros for a in attempts]
        estimates = [a.estimated_cost_micros for a in attempts]
        return replace(
            result,
            normalized_usage=normalized,
            raw_usage={
                "network_requests": len(attempts),
                "attempts": [
                    {
                        "raw": a.raw_usage,
                        "error_code": a.error_code,
                        "http_status": a.http_status,
                        "provider_request_id": a.provider_request_id,
                    }
                    for a in attempts
                ],
            },
            actual_cost_micros=sum(v for v in actual if v is not None)
            if all(v is not None for v in actual)
            else None,
            estimated_cost_micros=sum(v for v in estimates if v is not None)
            if all(v is not None for v in estimates)
            else None,
        )

    def _claim_review(self, snapshot: _Snapshot) -> _ReviewClaim:
        now = utc_now()
        provider = self.gateway.provider_name
        try:
            with self.session_factory() as session:
                job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
                plan = session.get(
                    JobPlanRecord, snapshot.plan_id, with_for_update=True
                )
                if job is None or plan is None:
                    return _ReviewClaim("replay_failed", snapshot.review_round)
                if not _review_job_accepts_results(job):
                    fence_amazon_suite_review_attempts(session, job)
                    session.commit()
                    return _ReviewClaim("fenced", _payload_review_round(plan.payload))
                current_output_hash = _current_deliverable_output_hash(
                    session,
                    snapshot.job_id,
                    lock=True,
                )
                if current_output_hash != snapshot.output_hash:
                    return _ReviewClaim(
                        "in_progress", _payload_review_round(plan.payload)
                    )
                replay = self._replay_claim(plan.payload, current_output_hash)
                if replay is not None:
                    return replay
                review_round = _next_review_round(plan.payload, current_output_hash)
                if review_round is None or review_round > self.max_review_rounds:
                    return _ReviewClaim(
                        "in_progress", _payload_review_round(plan.payload)
                    )
                attempt = session.scalar(
                    select(AmazonSuiteReviewAttemptRecord)
                    .where(
                        AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id,
                        AmazonSuiteReviewAttemptRecord.review_round == review_round,
                        AmazonSuiteReviewAttemptRecord.output_asset_set_hash
                        == current_output_hash,
                    )
                    .with_for_update()
                    .limit(1)
                )
                if attempt is not None:
                    claim = self._claim_from_attempt(attempt, now)
                    if claim.action == "claimed":
                        session.commit()
                    return claim
                idempotency_key = (
                    f"{snapshot.job_id}:suite-review:{review_round}:"
                    f"{current_output_hash}"
                )
                attempt = AmazonSuiteReviewAttemptRecord(
                    id=new_id("asr"),
                    workspace_id=snapshot.workspace_id,
                    job_id=snapshot.job_id,
                    review_round=review_round,
                    output_asset_set_hash=current_output_hash,
                    provider=provider,
                    provider_idempotency_key=idempotency_key,
                    status=AttemptStatus.RUNNING.value,
                    lease_token=uuid4().hex,
                    leased_by=self.worker_id,
                    lease_expires_at=now + timedelta(seconds=self.review_lease_seconds),
                    started_at=now,
                )
                session.add(attempt)
                session.commit()
                return _ReviewClaim(
                    "claimed",
                    review_round,
                    attempt.id,
                    attempt.lease_token,
                    idempotency_key,
                )
        except IntegrityError:
            # PostgreSQL's unique constraints are the final arbiter if two workers
            # race before either can observe the other's row. Re-read the winner,
            # but only while the original snapshot still represents the live suite.
            with self.session_factory() as session:
                job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
                plan = session.get(
                    JobPlanRecord, snapshot.plan_id, with_for_update=True
                )
                if job is None or plan is None:
                    return _ReviewClaim("replay_failed", snapshot.review_round)
                if not _review_job_accepts_results(job):
                    fence_amazon_suite_review_attempts(session, job)
                    session.commit()
                    return _ReviewClaim("fenced", _payload_review_round(plan.payload))
                current_output_hash = _current_deliverable_output_hash(
                    session,
                    snapshot.job_id,
                    lock=True,
                )
                if current_output_hash != snapshot.output_hash:
                    return _ReviewClaim(
                        "in_progress", _payload_review_round(plan.payload)
                    )
                replay = self._replay_claim(plan.payload, current_output_hash)
                if replay is not None:
                    return replay
                review_round = _next_review_round(plan.payload, current_output_hash)
                if review_round is None:
                    return _ReviewClaim(
                        "in_progress", _payload_review_round(plan.payload)
                    )
                attempt = session.scalar(
                    select(AmazonSuiteReviewAttemptRecord)
                    .where(
                        AmazonSuiteReviewAttemptRecord.job_id == snapshot.job_id,
                        AmazonSuiteReviewAttemptRecord.review_round == review_round,
                        AmazonSuiteReviewAttemptRecord.output_asset_set_hash
                        == current_output_hash,
                    )
                    .with_for_update()
                    .limit(1)
                )
                if attempt is None:
                    raise
                claim = self._claim_from_attempt(attempt, utc_now())
                if claim.action == "claimed":
                    session.commit()
                return claim

    @staticmethod
    def _replay_claim(
        payload: Mapping[str, Any], output_hash: str
    ) -> _ReviewClaim | None:
        quality = payload.get("business_quality")
        if not isinstance(quality, Mapping):
            return None
        if quality.get("output_asset_set_hash") != output_hash:
            return None
        review_round = quality.get("review_round")
        round_value = (
            review_round
            if isinstance(review_round, int) and not isinstance(review_round, bool)
            else 0
        )
        status = quality.get("status")
        if status == "passed":
            return _ReviewClaim("replay_passed", round_value)
        if status in {"failed", "warning", _REVIEW_INFRASTRUCTURE_FAILED}:
            return _ReviewClaim("replay_failed", round_value)
        return None

    def _claim_from_attempt(
        self, attempt: AmazonSuiteReviewAttemptRecord, now: datetime
    ) -> _ReviewClaim:
        base = _ReviewClaim(
            "in_progress",
            attempt.review_round,
            attempt.id,
            attempt.lease_token,
            attempt.provider_idempotency_key,
        )
        if attempt.status == AttemptStatus.RUNNING.value:
            if _lease_expired(attempt.lease_expires_at, now):
                if attempt.provider_call_started_at is None:
                    attempt.lease_token = uuid4().hex
                    attempt.leased_by = self.worker_id
                    attempt.lease_expires_at = now + timedelta(
                        seconds=self.review_lease_seconds
                    )
                    attempt.started_at = attempt.started_at or now
                    attempt.finished_at = None
                    attempt.error_code = None
                    attempt.error_message = None
                    return _ReviewClaim(
                        "claimed",
                        attempt.review_round,
                        attempt.id,
                        attempt.lease_token,
                        attempt.provider_idempotency_key,
                    )
                return _ReviewClaim(
                    "outcome_unknown",
                    attempt.review_round,
                    attempt.id,
                    attempt.lease_token,
                    attempt.provider_idempotency_key,
                )
            return base
        if attempt.status == AttemptStatus.OUTCOME_UNKNOWN.value:
            return _ReviewClaim(
                "outcome_unknown",
                attempt.review_round,
                attempt.id,
                attempt.lease_token,
                attempt.provider_idempotency_key,
            )
        return base

    def _provider_call_allowed(
        self,
        snapshot: _Snapshot,
        attempt_id: str,
        lease_token: str,
    ) -> Literal["active", "expired", "lost"]:
        with self.session_factory() as session:
            job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
            plan = session.get(JobPlanRecord, snapshot.plan_id, with_for_update=True)
            attempt = session.get(
                AmazonSuiteReviewAttemptRecord, attempt_id, with_for_update=True
            )
            if job is None or plan is None or attempt is None:
                return "lost"
            if not _review_job_accepts_results(job):
                fence_amazon_suite_review_attempts(session, job)
                session.commit()
                return "lost"
            if (
                attempt.lease_token != lease_token
                or attempt.leased_by != self.worker_id
                or attempt.status != AttemptStatus.RUNNING.value
            ):
                return "lost"
            if not _review_attempt_matches_current_state(
                session,
                plan.payload,
                attempt,
                snapshot,
            ):
                _finish_review_attempt(
                    attempt,
                    status=AttemptStatus.FAILED.value,
                    review=None,
                    error_code="AMAZON_SUITE_REVIEW_STALE_OUTPUT",
                    error_message=(
                        "The Amazon suite outputs changed before the review request "
                        "was submitted."
                    ),
                )
                session.commit()
                return "lost"
            now = utc_now()
            if _lease_expired(attempt.lease_expires_at, now):
                if attempt.provider_call_started_at is not None:
                    return "expired"
                # Preparation is local and unbilled. The current owner can renew an
                # uncontested expired lease before durably marking the provider call.
                attempt.lease_expires_at = now + timedelta(
                    seconds=self.review_lease_seconds
                )
            if attempt.provider_call_started_at is None:
                attempt.provider_call_started_at = now
                session.commit()
            return "active"

    def _contact_sheet_asset_id(self, job_id: str, review_round: int) -> str | None:
        with self.session_factory() as session:
            return session.scalar(
                select(JobAssetRecord.asset_id)
                .where(
                    JobAssetRecord.job_id == job_id,
                    JobAssetRecord.job_item_id.is_(None),
                    JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                    JobAssetRecord.role == "amazon_contact_sheet",
                    JobAssetRecord.ordinal == review_round,
                )
                .limit(1)
            )

    def _snapshot(self, job_id: str) -> _Snapshot | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None or not _review_job_accepts_results(job):
                return None
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job.id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if plan is None or not isinstance(plan.payload, dict):
                return None
            out_rows = session.execute(
                select(
                    JobAssetRecord.id,
                    JobItemRecord.id,
                    JobItemRecord.item_key,
                    JobItemRecord.ordinal,
                    AssetRecord.original_filename,
                    AssetRecord.content_type,
                    AssetRecord.sha256,
                    AssetRecord.object_key,
                    JobAssetRecord.validation,
                    AssetRecord.asset_metadata,
                )
                .join(JobItemRecord, JobItemRecord.id == JobAssetRecord.job_item_id)
                .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
                .where(
                    JobAssetRecord.job_id == job.id,
                    JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                    JobAssetRecord.is_deliverable.is_(True),
                    JobItemRecord.status.in_(
                        [JobItemStatus.SUCCEEDED.value, JobItemStatus.REUSED.value]
                    ),
                )
                .order_by(JobItemRecord.ordinal)
            ).all()
            in_rows = session.execute(
                select(
                    AssetRecord.id,
                    JobAssetRecord.role,
                    JobAssetRecord.ordinal,
                    AssetRecord.original_filename,
                    AssetRecord.content_type,
                    AssetRecord.sha256,
                    AssetRecord.object_key,
                )
                .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
                .where(
                    JobAssetRecord.job_id == job.id,
                    JobAssetRecord.direction == JobAssetDirection.INPUT.value,
                    AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
                    AssetRecord.deleted_at.is_(None),
                )
                .order_by(JobAssetRecord.role, JobAssetRecord.ordinal)
            ).all()
            outputs = tuple(
                _Output(
                    job_asset_id=int(row[0]),
                    item_id=row[1],
                    item_key=row[2],
                    ordinal=int(row[3]),
                    filename=row[4],
                    content_type=row[5],
                    sha256=row[6],
                    object_key=row[7],
                    validation=dict(row[8]) if isinstance(row[8], dict) else {},
                    asset_metadata=dict(row[9]) if isinstance(row[9], dict) else {},
                )
                for row in out_rows
            )
            inputs = tuple(
                _Input(
                    asset_id=row[0],
                    role=row[1],
                    ordinal=int(row[2]),
                    filename=row[3],
                    content_type=row[4],
                    sha256=row[5],
                    object_key=row[6],
                )
                for row in in_rows
            )
            quality = plan.payload.get("business_quality")
            prior = quality.get("review_round", 0) if isinstance(quality, dict) else 0
            review_round = (
                prior if isinstance(prior, int) and not isinstance(prior, bool) else 0
            )
            return _Snapshot(
                workspace_id=job.workspace_id,
                job_id=job.id,
                plan_id=plan.id,
                payload=dict(plan.payload),
                outputs=outputs,
                inputs=inputs,
                review_round=max(0, review_round),
                output_hash=canonical_hash(
                    [
                        {"item_key": row.item_key, "sha256": row.sha256}
                        for row in outputs
                    ]
                ),
                input_hash=canonical_hash(
                    [
                        {"role": row.role, "ordinal": row.ordinal, "sha256": row.sha256}
                        for row in inputs
                    ]
                ),
            )

    async def _read_outputs(self, rows: tuple[_Output, ...]) -> tuple[bytes, ...]:
        result: list[bytes] = []
        for row in rows:
            payload = await self.storage.read_bytes(row.object_key)
            if not payload or hashlib.sha256(payload).hexdigest() != row.sha256:
                raise OSError("Amazon output integrity check failed")
            result.append(payload)
        return tuple(result)

    async def _read_inputs(self, rows: tuple[_Input, ...]) -> tuple[SourceImage, ...]:
        result: list[SourceImage] = []
        for row in rows:
            payload = await self.storage.read_bytes(row.object_key)
            if not payload or hashlib.sha256(payload).hexdigest() != row.sha256:
                raise OSError("Amazon source integrity check failed")
            result.append(
                SourceImage(
                    asset_id=row.asset_id,
                    group_key=row.role,
                    filename=row.filename,
                    content_type=row.content_type,
                    sha256=row.sha256,
                    payload=payload,
                    source_role=row.role,
                )
            )
        return tuple(result)

    async def _persist_sheet(
        self, snapshot: _Snapshot, review_round: int, sheet: _Sheet
    ) -> str:
        with self.session_factory() as session:
            existing = session.scalar(
                select(JobAssetRecord)
                .where(
                    JobAssetRecord.job_id == snapshot.job_id,
                    JobAssetRecord.job_item_id.is_(None),
                    JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                    JobAssetRecord.role == "amazon_contact_sheet",
                    JobAssetRecord.ordinal == review_round,
                )
                .limit(1)
            )
            if existing is not None:
                return existing.asset_id
        object_key = (
            f"{snapshot.workspace_id}/generated/amazon-contact-sheets/"
            f"{snapshot.job_id}-{review_round}-{snapshot.output_hash[:16]}.jpg"
        )
        stored = await self.storage.save_validated(
            ValidatedImage(
                payload=sheet.payload,
                filename="amazon-suite-contact-sheet.jpg",
                content_type="image/jpeg",
                width=sheet.width,
                height=sheet.height,
                object_key=object_key,
                sha256=sheet.sha256,
            )
        )
        with self.session_factory() as session:
            job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
            if job is None:
                raise OSError("Amazon job disappeared before contact sheet persistence")
            if not _review_job_accepts_results(job):
                fence_amazon_suite_review_attempts(session, job)
                session.commit()
                raise OSError("Amazon job stopped before contact sheet persistence")
            asset_id = new_id("asset")
            session.add(
                AssetRecord(
                    id=asset_id,
                    workspace_id=snapshot.workspace_id,
                    media_kind=AssetMediaKind.IMAGE.value,
                    origin_kind=AssetOriginKind.GENERATED.value,
                    lifecycle_status=AssetLifecycleStatus.READY.value,
                    original_filename=stored.filename,
                    content_type=stored.content_type,
                    byte_size=stored.byte_size,
                    width=stored.width,
                    height=stored.height,
                    sha256=stored.sha256,
                    storage_provider=stored.storage_provider,
                    storage_bucket=stored.storage_bucket,
                    object_key=stored.object_key,
                    object_version=stored.object_version,
                    etag=stored.etag,
                    asset_metadata={
                        "artifact_kind": "contact_sheet",
                        "review_round": review_round,
                        "output_asset_set_hash": snapshot.output_hash,
                    },
                )
            )
            session.add(
                JobAssetRecord(
                    workspace_id=snapshot.workspace_id,
                    job_id=snapshot.job_id,
                    job_item_id=None,
                    asset_id=asset_id,
                    direction=JobAssetDirection.OUTPUT.value,
                    role="amazon_contact_sheet",
                    ordinal=review_round,
                    is_deliverable=False,
                    validation={
                        "artifact_kind": "contact_sheet",
                        "source_image_count": AMAZON_EXPECTED_ITEM_COUNT,
                        "output_asset_set_hash": snapshot.output_hash,
                    },
                )
            )
            session.commit()
            return asset_id

    def _record_snapshot_failure(self, job_id: str, exc: Exception) -> None:
        """Record review unavailability without changing technical delivery state."""

        error_message = (
            "Amazon suite review could not load the confirmed job state "
            f"({type(exc).__name__})."
        )
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None or not _review_job_accepts_results(job):
                if job is not None:
                    fence_amazon_suite_review_attempts(session, job)
                    session.commit()
                return
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job.id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if plan is None or not isinstance(plan.payload, dict):
                return
            payload = dict(plan.payload)
            quality_value = payload.get("business_quality")
            quality = dict(quality_value) if isinstance(quality_value, Mapping) else {}
            quality.update(
                {
                    "status": _REVIEW_INFRASTRUCTURE_FAILED,
                    "failure_kind": _REVIEW_INFRASTRUCTURE_FAILURE,
                    "policy_version": AMAZON_PLANNING_POLICY_VERSION,
                    "technical_delivery_status": "unchanged",
                    "manual_review_required": False,
                    "automatic_redraw_enabled": False,
                    "qa_notes": [error_message],
                    "missing_evidence": [],
                    "failed_item_keys": [],
                    "visual_review": _review_infrastructure_payload(
                        "AMAZON_SUITE_REVIEW_STATE_UNAVAILABLE",
                        error_message,
                    ),
                }
            )
            if self.enforce_delivery:
                quality.update(
                    {
                        "manual_review_required": True,
                        "delivery_status": "manual_review_required",
                        "max_suite_review_rounds": self.max_review_rounds,
                    }
                )
            payload["business_quality"] = quality
            _enable_review(
                payload,
                max_review_rounds=self.max_review_rounds,
                automatic_redraw=False,
            )
            plan.payload = payload
            plan.payload_hash = canonical_hash(payload)
            session.commit()

    def _record_incomplete(self, snapshot: _Snapshot) -> None:
        existing = {row.item_key for row in snapshot.outputs}
        missing = [key for key in AMAZON_ITEM_KEYS if key not in existing]
        with self.session_factory() as session:
            job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
            plan = session.get(JobPlanRecord, snapshot.plan_id, with_for_update=True)
            if job is None or plan is None:
                return
            if not _review_job_accepts_results(job):
                fence_amazon_suite_review_attempts(session, job)
                session.commit()
                return
            payload = dict(plan.payload)
            payload["business_quality"] = _quality_payload(
                snapshot,
                status="incomplete",
                technical="incomplete",
                review_round=snapshot.review_round,
                failed=missing,
                notes=["The fixed 21-image delivery set is incomplete."],
                findings=[
                    {
                        "code": "AMAZON_SUITE_INCOMPLETE",
                        "severity": "failed",
                        "missing_item_keys": missing,
                    }
                ],
            )
            if self.enforce_delivery:
                payload["business_quality"].update(
                    {
                        "manual_review_required": True,
                        "automatic_redraw_enabled": False,
                        "max_suite_review_rounds": self.max_review_rounds,
                        "delivery_status": "manual_review_required",
                    }
                )
                _enable_review(
                    payload,
                    max_review_rounds=self.max_review_rounds,
                    automatic_redraw=False,
                )
            plan.payload, plan.payload_hash = payload, canonical_hash(payload)
            session.commit()

    def _apply(
        self,
        snapshot: _Snapshot,
        review_round: int,
        contact_asset_id: str,
        review: AmazonVisualReviewCallResult,
        *,
        attempt_id: str,
        lease_token: str,
    ) -> AmazonSuiteReviewOutcome:
        findings, _ = _findings(snapshot.outputs)
        failed = _ordered(review.failed_item_keys)
        passed = review.visual_success is True and not failed
        reasons = list(review.reason_codes)
        with self.session_factory() as session:
            job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
            plan = session.get(JobPlanRecord, snapshot.plan_id, with_for_update=True)
            attempt = session.get(
                AmazonSuiteReviewAttemptRecord, attempt_id, with_for_update=True
            )
            if job is None or plan is None:
                return AmazonSuiteReviewOutcome("failed", review_round, failed)
            if not _review_job_accepts_results(job):
                fence_amazon_suite_review_attempts(session, job)
                session.commit()
                return AmazonSuiteReviewOutcome("not_applicable", review_round)
            if not _owns_review_attempt(attempt, lease_token):
                return _fenced_review_outcome(plan.payload, snapshot, review_round)
            owned_attempt = cast(AmazonSuiteReviewAttemptRecord, attempt)
            if not _review_attempt_matches_current_state(
                session,
                plan.payload,
                owned_attempt,
                snapshot,
                lock=True,
            ):
                _finish_review_attempt(
                    owned_attempt,
                    status=AttemptStatus.FAILED.value,
                    review=review,
                    error_code="AMAZON_SUITE_REVIEW_STALE_OUTPUT",
                    error_message=(
                        "The Amazon suite review result no longer matches the "
                        "current deliverable outputs."
                    ),
                )
                session.commit()
                return _fenced_review_outcome(plan.payload, snapshot, review_round)
            _finish_review_attempt(
                owned_attempt,
                status=AttemptStatus.SUCCEEDED.value,
                review=review,
            )
            payload = dict(plan.payload)
            _append_usage(payload, review)
            payload["business_quality"] = _quality_payload(
                snapshot,
                status="passed" if passed else "warning",
                technical="completed",
                review_round=review_round,
                failed=list(failed),
                findings=findings,
                contact_asset_id=contact_asset_id,
                notes=[review.summary],
                missing=list(review.missing_evidence),
                visual_review=_review_payload(review, reasons),
            )
            if self.enforce_delivery:
                payload["business_quality"].update(
                    {
                        "automatic_redraw_enabled": True,
                        "manual_review_required": not passed,
                        "max_suite_review_rounds": self.max_review_rounds,
                        "delivery_status": "ready" if passed else "blocked",
                    }
                )
            if (
                self.enforce_delivery
                and not passed
                and review_round < self.max_review_rounds
            ):
                self._schedule_redraw_locked(
                    session,
                    snapshot,
                    payload,
                    failed,
                    review,
                    next_review_round=review_round + 1,
                )
                plan.payload, plan.payload_hash = payload, canonical_hash(payload)
                # The next image attempts require an active generation job,
                # including when this review started from a manual-review state.
                transition_job(job, JobStatus.PROCESSING)
                job.finished_at = None
                job.succeeded_item_count = (
                    session.scalar(
                        select(func.count())
                        .select_from(JobItemRecord)
                        .where(
                            JobItemRecord.job_id == job.id,
                            JobItemRecord.status.in_(("succeeded", "reused")),
                        )
                    )
                    or 0
                )
                job.failed_item_count = 0
                job.stage = "suite_quality_redraw_pending"
                job.state_version += 1
                session.commit()
                return AmazonSuiteReviewOutcome("retry_items", review_round, failed)
            if self.enforce_delivery and not passed:
                # A final visual rejection must have failed JobItems so the
                # durable partial-state constraint and retry scope can express
                # the blocked rows.  Fence their prior files before the job is
                # transitioned out of a completed delivery state.
                failed_set = set(failed) or set(AMAZON_ITEM_KEYS)
                items = session.scalars(
                    select(JobItemRecord)
                    .where(JobItemRecord.job_id == snapshot.job_id)
                    .with_for_update()
                ).all()
                for item in items:
                    if item.item_key not in failed_set:
                        continue
                    item.status = JobItemStatus.FAILED.value
                    item.stage = "qa_failed"
                    item.finished_at = utc_now()
                    item.error_code = "AMAZON_QA_QUALITY_FAILED"
                    item.error_message = review.retry_guidance or review.summary
                    item.error_details = {
                        "qa_review_round": review_round,
                        "qa_failed_item": True,
                        "reason_codes": list(review.reason_codes),
                        "retry_guidance": review.retry_guidance,
                    }
                    item.state_version += 1
                    session.query(JobAssetRecord).filter(
                        JobAssetRecord.job_id == snapshot.job_id,
                        JobAssetRecord.job_item_id == item.id,
                        JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                        JobAssetRecord.is_deliverable.is_(True),
                    ).update({"is_deliverable": False}, synchronize_session=False)
            _enable_review(
                payload,
                max_review_rounds=self.max_review_rounds,
                automatic_redraw=self.enforce_delivery,
            )
            plan.payload, plan.payload_hash = payload, canonical_hash(payload)
            job.stage = "suite_quality_passed" if passed else "suite_quality_reported"
            job.state_version += 1
            session.commit()
        return AmazonSuiteReviewOutcome(
            "passed" if passed else "failed", review_round, failed
        )

    def _schedule_redraw_locked(
        self,
        session: Session,
        snapshot: _Snapshot,
        payload: dict[str, Any],
        failed: Sequence[str],
        review: AmazonVisualReviewCallResult,
        next_review_round: int,
    ) -> None:
        """Fence old outputs and make only failed items claimable again."""

        failed_set = set(failed)
        items = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == snapshot.job_id)
            .with_for_update()
        ).all()
        for item in items:
            if item.item_key not in failed_set:
                continue
            item.status = JobItemStatus.PENDING.value
            item.stage = "qa_redraw"
            item.finished_at = None
            item.error_code = "AMAZON_QA_REDRAW"
            item.error_message = review.retry_guidance or review.summary
            item.error_details = {
                "qa_review_round": next_review_round,
                "qa_failed_item": True,
                "retry_guidance": review.retry_guidance,
            }
            current_configuration = (
                dict(item.configuration)
                if isinstance(item.configuration, Mapping)
                else {}
            )
            item.configuration = {
                **current_configuration,
                "qa_redraw": {
                    "review_round": next_review_round,
                    "reason_codes": list(review.reason_codes),
                    "retry_guidance": review.retry_guidance,
                    "summary": review.summary,
                },
            }
            item.state_version += 1
            session.query(JobAssetRecord).filter(
                JobAssetRecord.job_id == snapshot.job_id,
                JobAssetRecord.job_item_id == item.id,
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                JobAssetRecord.is_deliverable.is_(True),
            ).update({"is_deliverable": False}, synchronize_session=False)
        quality = payload.get("business_quality")
        quality_payload = dict(quality) if isinstance(quality, Mapping) else {}
        quality_payload.update(
            {
                "status": "redraw_pending",
                "technical_delivery_status": "redraw_pending",
                "manual_review_required": False,
                "automatic_redraw_enabled": True,
                "delivery_status": "redraw_pending",
                "failed_item_keys": list(failed),
                "qa_notes": [review.summary],
                "retry_guidance": review.retry_guidance,
                "max_suite_review_rounds": self.max_review_rounds,
            }
        )
        payload["business_quality"] = quality_payload
        _enable_review(
            payload,
            max_review_rounds=self.max_review_rounds,
            automatic_redraw=True,
        )
        payload["controls"] = {
            **(
                dict(payload.get("controls"))  # type: ignore[arg-type]
                if isinstance(payload.get("controls"), Mapping)
                else {}
            ),
            "max_suite_review_rounds": self.max_review_rounds,
            "automatic_review_enabled": True,
            "automatic_redraw_enabled": True,
        }

    def _unavailable(
        self,
        snapshot: _Snapshot,
        review_round: int,
        contact_asset_id: str | None,
        error_code: str,
        error_message: str,
        review: AmazonVisualReviewCallResult | None = None,
        *,
        attempt_id: str,
        lease_token: str,
        attempt_status: str = AttemptStatus.FAILED.value,
        force_exhausted: bool = False,
    ) -> AmazonSuiteReviewOutcome:
        del force_exhausted  # Review retries are intentionally disabled.
        with self.session_factory() as session:
            job = session.get(JobRecord, snapshot.job_id, with_for_update=True)
            plan = session.get(JobPlanRecord, snapshot.plan_id, with_for_update=True)
            attempt = session.get(
                AmazonSuiteReviewAttemptRecord, attempt_id, with_for_update=True
            )
            if job is None or plan is None:
                return AmazonSuiteReviewOutcome("failed", review_round)
            if not _review_job_accepts_results(job):
                fence_amazon_suite_review_attempts(session, job)
                session.commit()
                return AmazonSuiteReviewOutcome("not_applicable", review_round)
            if not _owns_review_attempt(attempt, lease_token):
                return _fenced_review_outcome(plan.payload, snapshot, review_round)
            owned_attempt = cast(AmazonSuiteReviewAttemptRecord, attempt)
            if not _review_attempt_matches_current_state(
                session,
                plan.payload,
                owned_attempt,
                snapshot,
                lock=True,
            ):
                _finish_review_attempt(
                    owned_attempt,
                    status=attempt_status,
                    review=review,
                    error_code="AMAZON_SUITE_REVIEW_STALE_OUTPUT",
                    error_message=(
                        "The Amazon suite review failure no longer matches the "
                        "current deliverable outputs."
                    ),
                )
                session.commit()
                return _fenced_review_outcome(plan.payload, snapshot, review_round)
            _finish_review_attempt(
                owned_attempt,
                status=attempt_status,
                review=review,
                error_code=error_code,
                error_message=error_message,
            )
            payload = dict(plan.payload)
            if review is not None:
                _append_usage(payload, review)
            payload["business_quality"] = _quality_payload(
                snapshot,
                status=_REVIEW_INFRASTRUCTURE_FAILED,
                technical="completed",
                review_round=review_round,
                failed=[],
                contact_asset_id=contact_asset_id,
                notes=[error_message],
                visual_review=_review_infrastructure_payload(
                    error_code,
                    error_message,
                    review,
                ),
                failure_kind=_REVIEW_INFRASTRUCTURE_FAILURE,
            )
            if self.enforce_delivery:
                payload["business_quality"].update(
                    {
                        "manual_review_required": True,
                        "delivery_status": "manual_review_required",
                        "automatic_redraw_enabled": False,
                        "max_suite_review_rounds": self.max_review_rounds,
                    }
                )
            _enable_review(payload)
            plan.payload, plan.payload_hash = payload, canonical_hash(payload)
            job.stage = "suite_review_reported"
            job.state_version += 1
            session.commit()
        return AmazonSuiteReviewOutcome("failed", review_round)


def _review_job_accepts_results(job: JobRecord) -> bool:
    if job.type != JobType.AMAZON_VISUAL_SUITE.value:
        return False
    current = JobStatus(job.status)
    return current != JobStatus.CANCELLING and current not in TERMINAL_JOB_STATUSES


def _generation_contract(payload: Mapping[str, Any]) -> str:
    groups = payload.get("groups")
    group = groups[0] if isinstance(groups, list) and groups else {}
    group = group if isinstance(group, Mapping) else {}
    return json.dumps(
        {
            "analysis": group.get("analysis"),
            "campaign_style_lock": group.get("campaign_style_lock"),
            "product_consistency_lock": group.get("product_consistency_lock"),
            "layout_diversity_lock": group.get("layout_diversity_lock"),
            "pages": payload.get("pages"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _contact_sheet(rows: tuple[_Output, ...], payloads: tuple[bytes, ...]) -> _Sheet:
    columns, thumb, label_h, gap, margin = 4, (360, 270), 42, 18, 24
    count_rows = math.ceil(len(rows) / columns)
    width = margin * 2 + columns * thumb[0] + (columns - 1) * gap
    height = margin * 2 + count_rows * (thumb[1] + label_h) + (count_rows - 1) * gap
    sheet = Image.new("RGB", (width, height), "#f3f4f6")
    draw, font = ImageDraw.Draw(sheet), ImageFont.load_default()
    for index, (row, payload) in enumerate(zip(rows, payloads, strict=True)):
        try:
            with Image.open(BytesIO(payload)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail(thumb, Image.Resampling.LANCZOS)
        except OSError as exc:
            raise ValueError("Amazon output is not a readable image") from exc
        canvas = Image.new("RGB", thumb, "white")
        canvas.paste(
            image, ((thumb[0] - image.width) // 2, (thumb[1] - image.height) // 2)
        )
        x = margin + (index % columns) * (thumb[0] + gap)
        y = margin + (index // columns) * (thumb[1] + label_h + gap)
        sheet.paste(canvas, (x, y))
        draw.text((x, y + thumb[1] + 10), row.item_key, fill="#111827", font=font)
    buffer = BytesIO()
    sheet.save(buffer, format="JPEG", quality=92, optimize=True)
    payload = buffer.getvalue()
    return _Sheet(payload, width, height, hashlib.sha256(payload).hexdigest())


def _findings(
    rows: tuple[_Output, ...],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_hash[row.sha256].append(row.item_key)
    groups = [keys for keys in by_hash.values() if len(keys) > 1]
    findings: list[dict[str, Any]] = []
    if groups:
        findings.append(
            {
                "code": "AMAZON_EXACT_DUPLICATE_OUTPUT",
                "severity": "warning",
                "groups": groups,
            }
        )
    perceptual = [
        (row.item_key, row.sha256, row.validation.get("perceptual_dhash"))
        for row in rows
        if isinstance(row.validation.get("perceptual_dhash"), str)
        and len(cast(str, row.validation["perceptual_dhash"])) == 16
    ]
    pairs: list[list[str]] = []
    for index, (left_key, left_sha, left_hash) in enumerate(perceptual):
        for right_key, right_sha, right_hash in perceptual[index + 1 :]:
            if left_sha == right_sha:
                continue
            try:
                distance = (
                    int(cast(str, left_hash), 16) ^ int(cast(str, right_hash), 16)
                ).bit_count()
            except ValueError:
                continue
            if distance <= 4:
                pairs.append([left_key, right_key])
    if pairs:
        findings.append(
            {
                "code": "AMAZON_PERCEPTUAL_NEAR_DUPLICATE_OUTPUT",
                "severity": "reviewed_by_visual_qa",
                "pairs": pairs[:20],
                "threshold": 4,
            }
        )
    return findings, ()


def _ordered(values: Any) -> tuple[str, ...]:
    values = set(values)
    return tuple(key for key in AMAZON_ITEM_KEYS if key in values)


def _quality_payload(
    snapshot: _Snapshot,
    *,
    status: str,
    technical: str,
    review_round: int,
    failed: list[str],
    findings: list[dict[str, Any]] | None = None,
    contact_asset_id: str | None = None,
    notes: list[str] | None = None,
    missing: list[str] | None = None,
    visual_review: dict[str, Any] | None = None,
    failure_kind: str | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "policy_version": AMAZON_PLANNING_POLICY_VERSION,
        "technical_delivery_status": technical,
        "manual_review_required": False,
        "automatic_redraw_enabled": False,
        "input_asset_set_hash": snapshot.input_hash,
        "output_asset_set_hash": snapshot.output_hash,
        "deterministic_findings": findings or [],
        "visual_review": visual_review,
        "qa_notes": notes or [],
        "missing_evidence": missing or [],
        "failed_item_keys": failed,
        "review_round": review_round,
        "contact_sheet_asset_id": contact_asset_id,
        "generation_routes": _routes(snapshot.outputs),
    }
    if failure_kind is not None:
        result["failure_kind"] = failure_kind
    return result


def _review_infrastructure_payload(
    error_code: str,
    error_message: str,
    review: AmazonVisualReviewCallResult | None = None,
) -> dict[str, Any]:
    return {
        "visual_success": None,
        "summary": error_message,
        "reason_codes": [error_code],
        "missing_evidence": [],
        "retry_guidance": "",
        "failed_item_keys": [],
        "provider_request_id": review.provider_request_id if review else None,
        "usage_reported": bool(review and review.normalized_usage.get("reported")),
    }


def _review_payload(
    result: AmazonVisualReviewCallResult, reasons: list[str]
) -> dict[str, Any]:
    return {
        "visual_success": result.visual_success,
        "summary": result.summary,
        "reason_codes": reasons,
        "missing_evidence": list(result.missing_evidence),
        "retry_guidance": result.retry_guidance,
        "failed_item_keys": list(result.failed_item_keys),
        "provider_request_id": result.provider_request_id,
        "usage_reported": bool(result.normalized_usage.get("reported")),
    }


def _current_deliverable_output_hash(
    session: Session,
    job_id: str,
    *,
    lock: bool = False,
) -> str | None:
    statement = (
        select(
            JobItemRecord.item_key,
            JobItemRecord.status,
            AssetRecord.sha256,
        )
        .join(JobAssetRecord, JobAssetRecord.job_item_id == JobItemRecord.id)
        .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
        .where(
            JobItemRecord.job_id == job_id,
            JobAssetRecord.job_id == job_id,
            JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
            JobAssetRecord.is_deliverable.is_(True),
        )
        .order_by(JobItemRecord.ordinal)
    )
    if lock:
        statement = statement.with_for_update()
    rows = session.execute(statement).all()
    if len(rows) != AMAZON_EXPECTED_ITEM_COUNT:
        return None
    if tuple(row[0] for row in rows) != AMAZON_ITEM_KEYS:
        return None
    successful_statuses = {
        JobItemStatus.SUCCEEDED.value,
        JobItemStatus.REUSED.value,
    }
    if any(row[1] not in successful_statuses for row in rows):
        return None
    return canonical_hash([{"item_key": row[0], "sha256": row[2]} for row in rows])


def _next_review_round(
    payload: Mapping[str, Any], current_output_hash: str
) -> int | None:
    quality = payload.get("business_quality")
    if not isinstance(quality, Mapping):
        return 1
    current_round = _payload_review_round(payload)
    status = quality.get("status")
    if current_round == 0 and status in {None, "pending_generation", "needs_review"}:
        return 1
    controls = payload.get("controls")
    max_rounds = (
        controls.get("max_suite_review_rounds", 1)
        if isinstance(controls, Mapping)
        else 1
    )
    if status == "redraw_pending" and current_round > 0 and current_round < max_rounds:
        # A deterministic/mock generator may return the same bytes after a
        # redraw.  Completion of the fenced pending item set, rather than a
        # hash change alone, is the durable round transition.
        return current_round + 1
    return None


def _review_attempt_matches_current_state(
    session: Session,
    payload: Mapping[str, Any],
    attempt: AmazonSuiteReviewAttemptRecord,
    snapshot: _Snapshot,
    *,
    lock: bool = False,
) -> bool:
    if (
        attempt.job_id != snapshot.job_id
        or attempt.review_round <= 0
        or attempt.output_asset_set_hash != snapshot.output_hash
    ):
        return False
    current_output_hash = _current_deliverable_output_hash(
        session,
        snapshot.job_id,
        lock=lock,
    )
    if current_output_hash != attempt.output_asset_set_hash:
        return False
    return _next_review_round(payload, current_output_hash) == attempt.review_round


def _payload_review_round(payload: Mapping[str, Any]) -> int:
    quality = payload.get("business_quality")
    if not isinstance(quality, Mapping):
        return 0
    value = quality.get("review_round")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _lease_expired(value: datetime, now: datetime) -> bool:
    if value.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif value.tzinfo is not None and now.tzinfo is None:
        value = value.replace(tzinfo=None)
    return value <= now


def _owns_review_attempt(
    attempt: AmazonSuiteReviewAttemptRecord | None, lease_token: str
) -> bool:
    return bool(
        attempt is not None
        and attempt.lease_token == lease_token
        and attempt.status == AttemptStatus.RUNNING.value
    )


def _finish_review_attempt(
    attempt: AmazonSuiteReviewAttemptRecord,
    *,
    status: str,
    review: AmazonVisualReviewCallResult | None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    attempt.status = status
    attempt.provider_request_id = review.provider_request_id if review else None
    attempt.raw_usage = (
        dict(review.raw_usage) if review is not None and review.raw_usage else {}
    )
    attempt.normalized_usage = (
        dict(review.normalized_usage) if review is not None else {}
    )
    attempt.actual_cost_micros = review.actual_cost_micros if review else None
    attempt.estimated_cost_micros = review.estimated_cost_micros if review else None
    attempt.error_code = error_code
    attempt.error_message = error_message
    attempt.finished_at = utc_now()


def _fenced_review_outcome(
    payload: Mapping[str, Any], snapshot: _Snapshot, review_round: int
) -> AmazonSuiteReviewOutcome:
    quality = payload.get("business_quality")
    if isinstance(quality, Mapping) and (
        quality.get("output_asset_set_hash") == snapshot.output_hash
    ):
        current_round = _payload_review_round(payload)
        if quality.get("status") == "passed":
            return AmazonSuiteReviewOutcome("passed", current_round)
        if quality.get("status") in {
            "failed",
            "warning",
            _REVIEW_INFRASTRUCTURE_FAILED,
        }:
            return AmazonSuiteReviewOutcome("failed", current_round)
    return AmazonSuiteReviewOutcome("in_progress", review_round)


def _append_usage(
    payload: dict[str, Any], result: AmazonVisualReviewCallResult
) -> None:
    usage_value = payload.get("usage")
    usage = dict(usage_value) if isinstance(usage_value, dict) else {}
    records_value = usage.get("amazon_suite_review")
    records = list(records_value) if isinstance(records_value, list) else []
    records.append(
        {
            "raw": result.raw_usage,
            "normalized": result.normalized_usage,
            "reported": bool(result.normalized_usage.get("reported")),
            "provider_request_id": result.provider_request_id,
            "actual_cost_micros": result.actual_cost_micros,
            "estimated_cost_micros": result.estimated_cost_micros,
        }
    )
    usage["amazon_suite_review"] = records
    payload["usage"] = usage


def _enable_review(
    payload: dict[str, Any],
    *,
    max_review_rounds: int = 1,
    automatic_redraw: bool = False,
) -> None:
    controls_value = payload.get("controls")
    controls = dict(controls_value) if isinstance(controls_value, dict) else {}
    controls.update(
        {
            "automatic_review_enabled": True,
            "automatic_redraw_enabled": automatic_redraw,
            "successful_page_auto_redraw_enabled": False,
            "max_suite_review_rounds": max_review_rounds,
        }
    )
    payload["controls"] = controls


def _routes(rows: tuple[_Output, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        metrics = row.asset_metadata.get("provider_metrics")
        route = (
            metrics.get("generation_route") if isinstance(metrics, Mapping) else None
        )
        result.append(
            {
                "item_key": row.item_key,
                "route": dict(route) if isinstance(route, Mapping) else None,
            }
        )
    return result


def _refresh_counts(session: Session, job: JobRecord) -> None:
    rows = session.execute(
        select(JobItemRecord.status, func.count())
        .where(JobItemRecord.job_id == job.id)
        .group_by(JobItemRecord.status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    job.succeeded_item_count = sum(
        counts.get(status, 0)
        for status in (JobItemStatus.SUCCEEDED.value, JobItemStatus.REUSED.value)
    )
    job.failed_item_count = counts.get(JobItemStatus.FAILED.value, 0)
    job.progress = 99
