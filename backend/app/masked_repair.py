"""Strict, workspace-scoped provider for durable local image edits."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from io import BytesIO
from math import ceil, sqrt
from pathlib import Path
from typing import Any, Protocol, cast

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .domain import (
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    AttemptStatus,
    JobAssetDirection,
    JobItemStatus,
    JobStatus,
)
from .models import (
    AssetRecord,
    AuditEventRecord,
    CreditReservationRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    WorkspaceLimitRecord,
)
from .provider import (
    ProviderArtifact,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from .provider_quota import require_provider_job_quota
from .services import new_id, quota_enabled
from .skill_runtime import SkillManifestRegistry, SkillRuntimeError
from .storage import ImageStorage, ValidatedImage

MASKED_REPAIR_WORKFLOW_VERSION = "masked-repair-v3"
MASKED_REPAIR_PROMPT_VERSION = "masked-repair-master-v2"
MASKED_REPAIR_REVIEW_VERSION = "masked-repair-visual-review-v1"
MASKED_REPAIR_MODEL = "gpt-image-2"
_RAW_ROLE = "provider_raw"
_PROVIDER_EDGE_MULTIPLE = 16
_PROVIDER_MIN_PIXELS = 655_360
_PROVIDER_MAX_PIXELS = 8_294_400
_PROVIDER_MAX_EDGE = 3_840
_PROVIDER_MAX_RATIO = 3.0
_MAX_RELAY_CROP_FRACTION = 0.15
_VISUAL_REVIEW_MAX_ATTEMPTS = 2
_VISUAL_REVIEW_RETRY_DELAY_SECONDS = 1.0


class MaskedRepairError(RuntimeError):
    """A controlled local-edit failure safe to expose through job status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        needs_reselection: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.needs_reselection = needs_reselection


@dataclass(frozen=True)
class MaskedEditGatewayRequest:
    source_png: bytes
    api_mask_png: bytes
    prompt: str
    quality: str
    width: int
    height: int
    idempotency_key: str
    # ``mask_png`` is the explicit OpenAI-compatible mask part.  It is
    # optional only for backwards-compatible third-party gateways; the real
    # provider path always supplies it and sends ``image`` + ``mask``.
    mask_png: bytes | None = None
    execution_path: str = "full"


@dataclass(frozen=True)
class MaskedEditCallResult:
    outcome: ProviderOutcome
    provider_request_id: str | None
    raw_usage: dict[str, Any] | None
    normalized_usage: dict[str, Any]
    actual_cost_micros: int | None
    estimated_cost_micros: int | None
    artifact: ProviderArtifact | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    http_status: int | None = None


@dataclass(frozen=True)
class MaskedVisualReviewRequest:
    source_png: bytes
    authority_png: bytes
    candidate_png: bytes
    instruction: str
    idempotency_key: str
    route_attempt: int = 0


@dataclass(frozen=True)
class MaskedVisualReviewCallResult:
    visual_success: bool | None
    provider_request_id: str | None
    raw_usage: dict[str, Any] | None
    normalized_usage: dict[str, Any]
    actual_cost_micros: int | None
    estimated_cost_micros: int | None
    summary: str | None = None
    reason_codes: tuple[str, ...] = ()
    retry_guidance: str | None = None
    observable_defect: str | None = None
    corrective_action: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    http_status: int | None = None


class MaskedEditGateway(Protocol):
    provider_name: str
    image_model: str
    pricing_version: str

    async def edit_masked(
        self,
        request: MaskedEditGatewayRequest,
    ) -> MaskedEditCallResult: ...

    async def review_masked(
        self,
        request: MaskedVisualReviewRequest,
    ) -> MaskedVisualReviewCallResult: ...


@dataclass(frozen=True)
class _AuthorizedInputs:
    source: AssetRecord
    mask: AssetRecord
    instruction: str
    quality: str
    execution_path: str
    attempt_no: int = 1


@dataclass(frozen=True)
class _ProviderCanvas:
    source_png: bytes
    api_mask_png: bytes
    width: int
    height: int
    source_box: tuple[int, int, int, int]


@dataclass(frozen=True)
class _NormalizedInputs:
    source_png: bytes
    authority_png: bytes
    source_image: Image.Image
    authority: Image.Image
    width: int
    height: int
    normalized_source_sha256: str
    authority_sha256: str

    provider_canvas: _ProviderCanvas


class MaskedRepairProvider:
    """Generate a raw edit, then deliver only an exact-authority composite."""

    name = "masked-image-repair"
    model = MASKED_REPAIR_MODEL
    pricing_version = "masked-repair-configured-v1"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: MaskedEditGateway,
        storage: ImageStorage,
        *,
        budget_limit_micros: int,
        skill_root: Path,
        feather_radius: int = 2,
    ) -> None:
        if budget_limit_micros <= 0:
            raise ValueError("Masked-repair budget must be positive.")
        if not 0 <= feather_radius <= 8:
            raise ValueError("Masked-repair feather radius must be from 0 through 8.")
        self.session_factory = session_factory
        self.gateway = gateway
        self.storage = storage
        self.budget_limit_micros = budget_limit_micros
        self.skill_root = skill_root
        self.feather_radius = feather_radius

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            authorized = self._authorize(request)
            normalized = await self._load_and_normalize(authorized)
            self._reserve_call_budget(request, authorized.quality)
            reusable = await self._load_reusable_raw(request, normalized)
        except MaskedRepairError as exc:
            return self._rejected(exc)

        gateway_request = MaskedEditGatewayRequest(
            source_png=normalized.provider_canvas.source_png,
            api_mask_png=normalized.provider_canvas.api_mask_png,
            prompt=_master_prompt(authorized.instruction),
            quality=_provider_quality(authorized.quality),
            width=normalized.provider_canvas.width,
            height=normalized.provider_canvas.height,
            idempotency_key=request.idempotency_key,
            # Keep the authority/API mask as a distinct multipart part.  The
            # gateway may still retain its legacy transparent-hole adapter for
            # old callers, but production local-edit calls use this field.
            mask_png=normalized.provider_canvas.api_mask_png,
            execution_path=_effective_execution_path(
                authorized.execution_path,
                instruction=authorized.instruction,
                authority=normalized.authority,
            ),
        )
        execution_path = gateway_request.execution_path
        call_count = 0
        result: MaskedEditCallResult
        if reusable is not None:
            result = reusable
        elif execution_path == "deterministic":
            deterministic = _deterministic_repair(
                normalized.source_image,
                normalized.authority,
                authorized.instruction,
            )
            if deterministic is None:
                return self._rejected(
                    MaskedRepairError(
                        "LOCAL_EDIT_DETERMINISTIC_UNSUPPORTED",
                        "The deterministic path only accepts an explicit "
                        "flat-colour fill.",
                    )
                )
            payload = _png_bytes(deterministic)
            result = MaskedEditCallResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id="local-deterministic",
                raw_usage={"path": "deterministic"},
                normalized_usage={"reported": True},
                actual_cost_micros=0,
                estimated_cost_micros=0,
                artifact=ProviderArtifact(
                    payload=payload,
                    filename="local-edit-deterministic.png",
                    content_type="image/png",
                    width=deterministic.width,
                    height=deterministic.height,
                    sha256=hashlib.sha256(payload).hexdigest(),
                ),
            )
        else:
            while True:
                call_count += 1
                result = await self.gateway.edit_masked(gateway_request)
                if not result.retryable or call_count >= 2:
                    break

        usage = _usage_record(
            result,
            review_attempts=(),
            call_count=call_count,
            source_sha256=authorized.source.sha256,
            mask_sha256=authorized.mask.sha256,
            normalized_source_sha256=normalized.normalized_source_sha256,
            authority_sha256=normalized.authority_sha256,
            execution_path=execution_path,
        )
        if result.outcome != ProviderOutcome.SUCCEEDED or result.artifact is None:
            return ProviderResult(
                outcome=result.outcome,
                provider_request_id=result.provider_request_id,
                error_code=result.error_code,
                error_message=result.error_message,
                usage_reported=bool(result.normalized_usage.get("reported")),
                raw_usage=usage,
                estimated_cost_micros=result.estimated_cost_micros,
                actual_cost_micros=result.actual_cost_micros,
                retryable=result.retryable,
                http_status=result.http_status,
                provider_name=self.gateway.provider_name,
                model_name=MASKED_REPAIR_MODEL,
                pricing_version=self.gateway.pricing_version,
            )

        generation_id = _generation_id(
            request.attempt_id,
            result.provider_request_id,
            result.artifact.sha256,
        )
        if reusable is not None:
            generation_id = str(
                (result.raw_usage or {}).get("generation_id") or generation_id
            )
        try:
            if reusable is None:
                await self._persist_raw_artifact(
                    request,
                    result.artifact,
                    generation_id=generation_id,
                    provider_request_id=result.provider_request_id,
                    source_sha256=authorized.source.sha256,
                    mask_sha256=authorized.mask.sha256,
                    normalized_source_sha256=normalized.normalized_source_sha256,
                    authority_sha256=normalized.authority_sha256,
                )
        except Exception as error:
            failure = (
                error
                if isinstance(error, MaskedRepairError)
                else MaskedRepairError(
                    "LOCAL_EDIT_POSTPROCESS_FAILED",
                    f"Local-edit postprocessing failed ({type(error).__name__}).",
                    retryable=True,
                )
            )
            metrics = dict(usage["metrics"])
            metrics.update(
                {
                    "generation_id": generation_id,
                    "raw_artifact_sha256": result.artifact.sha256,
                    "strict_lock_success": False,
                    "delivery_status": (
                        "needs_reselection_for_strict_lock"
                        if failure.needs_reselection
                        else "retryable_processing_failure"
                        if failure.retryable
                        else "strict_lock_failed"
                    ),
                }
            )
            usage["metrics"] = metrics
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id=result.provider_request_id,
                error_code=failure.code,
                error_message=str(failure),
                usage_reported=bool(result.normalized_usage.get("reported")),
                raw_usage=usage,
                estimated_cost_micros=result.estimated_cost_micros,
                actual_cost_micros=result.actual_cost_micros,
                retryable=failure.retryable,
                http_status=result.http_status,
                provider_name=self.gateway.provider_name,
                model_name=MASKED_REPAIR_MODEL,
                pricing_version=self.gateway.pricing_version,
            )

        try:
            materialized_candidate = _materialize_candidate(normalized, result.artifact)
        except Exception as error:
            failure = (
                error
                if isinstance(error, MaskedRepairError)
                else MaskedRepairError(
                    "LOCAL_EDIT_POSTPROCESS_FAILED",
                    f"Local-edit postprocessing failed ({type(error).__name__}).",
                    retryable=True,
                )
            )
            metrics = dict(usage["metrics"])
            metrics.update(
                {
                    "generation_id": generation_id,
                    "raw_artifact_sha256": result.artifact.sha256,
                    "raw_visual_success": None,
                    "strict_lock_success": False,
                    "visual_review_requests": 0,
                    "provider_raw_width": result.artifact.width,
                    "provider_raw_height": result.artifact.height,
                    "delivery_status": (
                        "needs_reselection_for_strict_lock"
                        if failure.needs_reselection
                        else "retryable_processing_failure"
                        if failure.retryable
                        else "strict_lock_failed"
                    ),
                }
            )
            usage["metrics"] = metrics
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id=result.provider_request_id,
                error_code=failure.code,
                error_message=str(failure),
                usage_reported=bool(result.normalized_usage.get("reported")),
                raw_usage=usage,
                estimated_cost_micros=result.estimated_cost_micros,
                actual_cost_micros=result.actual_cost_micros,
                retryable=failure.retryable,
                http_status=result.http_status,
                provider_name=self.gateway.provider_name,
                model_name=MASKED_REPAIR_MODEL,
                pricing_version=self.gateway.pricing_version,
            )

        review_request = MaskedVisualReviewRequest(
            source_png=normalized.source_png,
            authority_png=normalized.authority_png,
            candidate_png=_png_bytes(materialized_candidate),
            instruction=authorized.instruction,
            idempotency_key=f"{request.idempotency_key}:visual-review",
        )
        review_attempts: list[MaskedVisualReviewCallResult] = []
        while len(review_attempts) < _VISUAL_REVIEW_MAX_ATTEMPTS:
            try:
                review = await self.gateway.review_masked(
                    replace(
                        review_request,
                        route_attempt=(authorized.attempt_no - 1)
                        * _VISUAL_REVIEW_MAX_ATTEMPTS
                        + len(review_attempts),
                    )
                )
            except Exception as exc:
                review = MaskedVisualReviewCallResult(
                    visual_success=None,
                    provider_request_id=None,
                    raw_usage=None,
                    normalized_usage={"reported": False},
                    actual_cost_micros=None,
                    estimated_cost_micros=None,
                    error_code="LOCAL_EDIT_VISUAL_REVIEW_FAILED",
                    error_message=(
                        f"Local-edit visual review failed ({type(exc).__name__})."
                    ),
                    retryable=False,
                )
            review_attempts.append(review)
            if (
                not review.retryable
                or len(review_attempts) >= _VISUAL_REVIEW_MAX_ATTEMPTS
            ):
                break
            await asyncio.sleep(_VISUAL_REVIEW_RETRY_DELAY_SECONDS)
        usage = _usage_record(
            result,
            review_attempts=review_attempts,
            call_count=call_count,
            source_sha256=authorized.source.sha256,
            mask_sha256=authorized.mask.sha256,
            normalized_source_sha256=normalized.normalized_source_sha256,
            authority_sha256=normalized.authority_sha256,
            execution_path=execution_path,
        )
        try:
            self._record_raw_visual_review(request, review)
        except Exception as exc:
            review = MaskedVisualReviewCallResult(
                visual_success=None,
                provider_request_id=review.provider_request_id,
                raw_usage=review.raw_usage,
                normalized_usage=review.normalized_usage,
                actual_cost_micros=review.actual_cost_micros,
                estimated_cost_micros=review.estimated_cost_micros,
                error_code="LOCAL_EDIT_VISUAL_REVIEW_AUDIT_FAILED",
                error_message=(
                    f"Local-edit visual review audit failed ({type(exc).__name__})."
                ),
                retryable=False,
                http_status=review.http_status,
            )
        review_estimated_cost = (
            sum(cast(int, attempt.estimated_cost_micros) for attempt in review_attempts)
            if all(
                attempt.estimated_cost_micros is not None for attempt in review_attempts
            )
            else None
        )
        review_actual_cost = (
            sum(cast(int, attempt.actual_cost_micros) for attempt in review_attempts)
            if all(
                attempt.actual_cost_micros is not None for attempt in review_attempts
            )
            else None
        )
        estimated_cost = _combined_cost(
            result.estimated_cost_micros,
            review_estimated_cost,
        )
        actual_cost = _combined_cost(
            result.actual_cost_micros,
            review_actual_cost,
            require_complete=True,
        )
        usage_reported = bool(result.normalized_usage.get("reported")) and all(
            bool(attempt.normalized_usage.get("reported"))
            for attempt in review_attempts
        )
        metrics = dict(usage["metrics"])
        metrics.update(
            {
                "generation_id": generation_id,
                "raw_artifact_sha256": result.artifact.sha256,
                "raw_visual_success": review.visual_success,
                "visual_review_request_id": review.provider_request_id,
                "visual_review_summary": review.summary,
                "visual_review_reason_codes": list(review.reason_codes),
                "visual_review_retry_guidance": review.retry_guidance,
                "visual_review_observable_defect": review.observable_defect,
                "visual_review_corrective_action": review.corrective_action,
                "visual_review_version": MASKED_REPAIR_REVIEW_VERSION,
                "strict_lock_success": None,
                "workflow_version": MASKED_REPAIR_WORKFLOW_VERSION,
                "prompt_version": MASKED_REPAIR_PROMPT_VERSION,
            }
        )
        usage["metrics"] = metrics
        if review.error_code is not None or review.visual_success is None:
            metrics["delivery_status"] = "visual_review_unavailable"
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id=result.provider_request_id,
                error_code=(review.error_code or "LOCAL_EDIT_VISUAL_REVIEW_FAILED"),
                error_message=(
                    review.error_message
                    or "Local-edit visual review did not return a verdict."
                ),
                usage_reported=usage_reported,
                raw_usage=usage,
                estimated_cost_micros=estimated_cost,
                actual_cost_micros=actual_cost,
                retryable=review.retryable,
                http_status=review.http_status,
                provider_name=self.gateway.provider_name,
                model_name=MASKED_REPAIR_MODEL,
                pricing_version=self.gateway.pricing_version,
            )
        if not review.visual_success:
            metrics["delivery_status"] = "visual_review_rejected"
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id=result.provider_request_id,
                error_code="LOCAL_EDIT_VISUAL_REVIEW_REJECTED",
                error_message=(
                    review.summary or "The generated edit did not pass visual review."
                ),
                usage_reported=usage_reported,
                raw_usage=usage,
                estimated_cost_micros=estimated_cost,
                actual_cost_micros=actual_cost,
                retryable=False,
                http_status=review.http_status,
                provider_name=self.gateway.provider_name,
                model_name=MASKED_REPAIR_MODEL,
                pricing_version=self.gateway.pricing_version,
            )

        metrics["delivery_status"] = "strict_result_delivered"
        try:
            final_artifact, outside_changed_pixels = _strict_composite(
                normalized,
                materialized_candidate,
                feather_radius=self.feather_radius,
            )
        except Exception as error:
            failure = (
                error
                if isinstance(error, MaskedRepairError)
                else MaskedRepairError(
                    "LOCAL_EDIT_POSTPROCESS_FAILED",
                    f"Local-edit postprocessing failed ({type(error).__name__}).",
                )
            )
            metrics.update(
                {
                    "strict_lock_success": False,
                    "delivery_status": (
                        "needs_reselection_for_strict_lock"
                        if failure.needs_reselection
                        else "raw_visual_success_locked_failed"
                    ),
                    "provider_request_width": normalized.provider_canvas.width,
                    "provider_request_height": normalized.provider_canvas.height,
                    "provider_raw_width": result.artifact.width,
                    "provider_raw_height": result.artifact.height,
                    "geometry_mapping": "uniform_cover_center_crop_v1",
                }
            )
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id=result.provider_request_id,
                error_code=failure.code,
                error_message=str(failure),
                usage_reported=usage_reported,
                raw_usage=usage,
                estimated_cost_micros=estimated_cost,
                actual_cost_micros=actual_cost,
                retryable=False,
                http_status=result.http_status,
                provider_name=self.gateway.provider_name,
                model_name=MASKED_REPAIR_MODEL,
                pricing_version=self.gateway.pricing_version,
            )
        metrics.update(
            {
                "strict_lock_success": True,
                "outside_changed_pixels": outside_changed_pixels,
                "final_sha256": final_artifact.sha256,
                "final_width": final_artifact.width,
                "final_height": final_artifact.height,
                "provider_request_width": normalized.provider_canvas.width,
                "provider_request_height": normalized.provider_canvas.height,
                "provider_raw_width": result.artifact.width,
                "provider_raw_height": result.artifact.height,
                "geometry_mapping": "uniform_cover_center_crop_v1",
            }
        )

        return ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id=result.provider_request_id,
            usage_reported=usage_reported,
            raw_usage=usage,
            estimated_cost_micros=estimated_cost,
            actual_cost_micros=actual_cost,
            retryable=False,
            http_status=review.http_status,
            artifact=final_artifact,
            provider_name=self.gateway.provider_name,
            model_name=MASKED_REPAIR_MODEL,
            pricing_version=self.gateway.pricing_version,
        )

    def _authorize(self, request: ProviderRequest) -> _AuthorizedInputs:
        try:
            SkillManifestRegistry.bundled().verify_source(
                "masked-image-repair",
                self.skill_root,
            )
        except SkillRuntimeError as exc:
            raise MaskedRepairError(exc.code, str(exc)) from exc
        if request.operation != "local_edit":
            raise MaskedRepairError(
                "LOCAL_EDIT_OPERATION_INVALID",
                "Masked repair only accepts local_edit attempts.",
            )
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            item = session.get(JobItemRecord, request.job_item_id, with_for_update=True)
            attempt = session.get(
                JobAttemptRecord,
                request.attempt_id,
                with_for_update=True,
            )
            if (
                job is None
                or item is None
                or attempt is None
                or job.workspace_id != request.workspace_id
                or item.workspace_id != request.workspace_id
                or attempt.workspace_id != request.workspace_id
                or item.job_id != job.id
                or attempt.job_item_id != item.id
            ):
                raise MaskedRepairError(
                    "WORKSPACE_SCOPE_MISMATCH",
                    "The local edit is outside its durable workspace scope.",
                )
            if (
                JobStatus(job.status) != JobStatus.PROCESSING
                or JobItemStatus(item.status) != JobItemStatus.RUNNING
                or AttemptStatus(attempt.status) != AttemptStatus.RUNNING
            ):
                raise MaskedRepairError(
                    "LOCAL_EDIT_CALL_NOT_AUTHORIZED",
                    "The durable attempt is not authorized for a provider call.",
                )
            inputs = session.scalars(
                select(JobAssetRecord).where(
                    JobAssetRecord.workspace_id == request.workspace_id,
                    JobAssetRecord.job_id == job.id,
                    JobAssetRecord.direction == JobAssetDirection.INPUT.value,
                    JobAssetRecord.role.in_(["source", "selection_mask"]),
                )
            ).all()
            by_role = {relation.role: relation.asset_id for relation in inputs}
            source_id = by_role.get("source")
            mask_id = by_role.get("selection_mask")
            if source_id is None or mask_id is None or source_id == mask_id:
                raise MaskedRepairError(
                    "LOCAL_EDIT_INPUT_INVALID",
                    "Source and selection mask must be distinct input assets.",
                )
            assets = {
                asset.id: asset
                for asset in session.scalars(
                    select(AssetRecord).where(
                        AssetRecord.workspace_id == request.workspace_id,
                        AssetRecord.id.in_([source_id, mask_id]),
                        AssetRecord.lifecycle_status
                        == AssetLifecycleStatus.READY.value,
                        AssetRecord.deleted_at.is_(None),
                    )
                ).all()
            }
            source = assets.get(source_id)
            mask = assets.get(mask_id)
            if source is None or mask is None:
                raise MaskedRepairError(
                    "LOCAL_EDIT_INPUT_NOT_READY",
                    "Source and mask must be ready assets in the same workspace.",
                )
            if source.media_kind != AssetMediaKind.IMAGE.value:
                raise MaskedRepairError(
                    "LOCAL_EDIT_SOURCE_KIND_INVALID",
                    "The source asset must be an image.",
                )
            if mask.media_kind != AssetMediaKind.MASK.value:
                raise MaskedRepairError(
                    "LOCAL_EDIT_MASK_KIND_INVALID",
                    "The selection asset must have mask media kind.",
                    needs_reselection=True,
                )
            if (
                source.width is None
                or source.height is None
                or mask.width != source.width
                or mask.height != source.height
            ):
                raise MaskedRepairError(
                    "LOCAL_EDIT_DIMENSIONS_MISMATCH",
                    "Source and selection mask dimensions must match.",
                    needs_reselection=True,
                )
            settings_value = request.configuration.get("job")
            settings_root = settings_value if isinstance(settings_value, dict) else {}
            edit_settings = settings_root.get("settings")
            configuration = edit_settings if isinstance(edit_settings, dict) else {}
            instruction = configuration.get("instruction")
            quality = configuration.get("quality", "balanced")
            execution_path = configuration.get("execution_path", "auto")
            if not isinstance(instruction, str) or not instruction.strip():
                raise MaskedRepairError(
                    "LOCAL_EDIT_INSTRUCTION_INVALID",
                    "A concrete local-edit instruction is required.",
                )
            if _instruction_has_encoding_damage(instruction):
                raise MaskedRepairError(
                    "LOCAL_EDIT_INSTRUCTION_ENCODING_INVALID",
                    "The local-edit instruction appears to contain encoding damage.",
                )
            if quality not in {"preview", "balanced", "final", "auto"}:
                raise MaskedRepairError(
                    "LOCAL_EDIT_QUALITY_INVALID",
                    "Local-edit quality is invalid.",
                )
            if execution_path not in {
                "auto",
                "deterministic",
                "hybrid",
                "cropped",
                "full",
            }:
                raise MaskedRepairError(
                    "LOCAL_EDIT_EXECUTION_PATH_INVALID",
                    "Local-edit execution path is invalid.",
                )
            return _AuthorizedInputs(
                source=source,
                mask=mask,
                instruction=instruction,
                quality=str(quality),
                execution_path=str(execution_path),
                attempt_no=attempt.attempt_no,
            )

    async def _load_and_normalize(
        self,
        authorized: _AuthorizedInputs,
    ) -> _NormalizedInputs:
        try:
            source_bytes = await self.storage.read_bytes(authorized.source.object_key)
            mask_bytes = await self.storage.read_bytes(authorized.mask.object_key)
        except Exception as exc:
            raise MaskedRepairError(
                "LOCAL_EDIT_INPUT_READ_FAILED",
                f"Local-edit input could not be read ({type(exc).__name__}).",
                retryable=True,
            ) from exc
        if hashlib.sha256(source_bytes).hexdigest() != authorized.source.sha256:
            raise MaskedRepairError(
                "LOCAL_EDIT_SOURCE_HASH_MISMATCH",
                "Source bytes no longer match the durable asset hash.",
            )
        if hashlib.sha256(mask_bytes).hexdigest() != authorized.mask.sha256:
            raise MaskedRepairError(
                "LOCAL_EDIT_MASK_HASH_MISMATCH",
                "Mask bytes no longer match the durable asset hash.",
            )
        try:
            source = _normalized_rgba(source_bytes)
            mask_rgba = _normalized_rgba(mask_bytes)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MaskedRepairError(
                "LOCAL_EDIT_IMAGE_INVALID",
                "Source or selection mask is not a valid raster image.",
                needs_reselection=True,
            ) from exc
        if source.size != mask_rgba.size:
            raise MaskedRepairError(
                "LOCAL_EDIT_DIMENSIONS_MISMATCH",
                "Source and selection mask dimensions must match after EXIF handling.",
                needs_reselection=True,
            )
        authority = _binary_authority(mask_rgba)
        extrema = authority.getextrema()
        if extrema == (0, 0):
            raise MaskedRepairError(
                "LOCAL_EDIT_MASK_EMPTY",
                "The selection mask does not authorize any pixels.",
                needs_reselection=True,
            )
        if extrema == (255, 255):
            raise MaskedRepairError(
                "LOCAL_EDIT_MASK_FULL_FRAME",
                "The selection mask may not authorize the full canvas.",
                needs_reselection=True,
            )
        source_png = _png_bytes(source)
        authority_png = _png_bytes(authority)
        provider_canvas = _build_provider_canvas(source, authority)
        width, height = source.size
        return _NormalizedInputs(
            source_png=source_png,
            authority_png=authority_png,
            source_image=source,
            authority=authority,
            width=width,
            height=height,
            normalized_source_sha256=hashlib.sha256(source_png).hexdigest(),
            authority_sha256=hashlib.sha256(authority_png).hexdigest(),
            provider_canvas=provider_canvas,
        )

    async def _load_reusable_raw(
        self, request: ProviderRequest, normalized: _NormalizedInputs
    ) -> MaskedEditCallResult | None:
        """Resume review from a hash-bound candidate in this retry's lineage."""
        with self.session_factory() as session:
            current_job = session.get(JobRecord, request.job_id)
            item = session.get(JobItemRecord, request.job_item_id)
            if current_job is None or item is None:
                return None
            found: dict[str, Any] | None = None
            seen: set[str] = set()
            for _ in range(16):
                if item.id in seen:
                    break
                seen.add(item.id)
                source_job = session.get(JobRecord, item.job_id)
                if (
                    source_job is None
                    or item.workspace_id != request.workspace_id
                    or source_job.workspace_id != request.workspace_id
                    or source_job.type != "local_edit"
                    or source_job.configuration != current_job.configuration
                ):
                    break
                relation = session.scalar(
                    select(JobAssetRecord).where(
                        JobAssetRecord.job_id == item.job_id,
                        JobAssetRecord.job_item_id == item.id,
                        JobAssetRecord.direction == "intermediate",
                        JobAssetRecord.role == _RAW_ROLE,
                    )
                )
                if relation is not None:
                    asset = session.get(AssetRecord, relation.asset_id)
                    if asset is None or asset.workspace_id != request.workspace_id:
                        break
                    metadata = asset.asset_metadata or {}
                    if relation.validation.get("raw_visual_success") is False:
                        return None
                    # A rejected image must be regenerated on an explicit retry.
                    if item.error_code == "LOCAL_EDIT_VISUAL_REVIEW_REJECTED":
                        return None
                    if (
                        asset.lifecycle_status != AssetLifecycleStatus.READY.value
                        or asset.deleted_at is not None
                        or metadata.get("immutable_provider_raw") is not True
                        or metadata.get("workflow_version")
                        != MASKED_REPAIR_WORKFLOW_VERSION
                        or metadata.get("normalized_source_sha256")
                        != normalized.normalized_source_sha256
                        or metadata.get("authority_sha256")
                        != normalized.authority_sha256
                    ):
                        raise MaskedRepairError(
                            "LOCAL_EDIT_RAW_BINDING_INVALID",
                            "The saved candidate no longer matches this edit's "
                            "authority.",
                        )
                    found = {
                        "id": asset.id,
                        "key": asset.object_key,
                        "sha256": asset.sha256,
                        "bytes": asset.byte_size,
                        "width": asset.width,
                        "height": asset.height,
                        "validation": dict(relation.validation),
                    }
                    break
                if not item.source_job_item_id:
                    break
                ancestor = session.get(JobItemRecord, item.source_job_item_id)
                if ancestor is None:
                    break
                item = ancestor
        if found is None:
            return None
        try:
            payload = await self.storage.read_bytes(str(found["key"]))
            if (
                hashlib.sha256(payload).hexdigest() != found["sha256"]
                or len(payload) != found["bytes"]
            ):
                raise ValueError("candidate hash mismatch")
            with Image.open(BytesIO(payload)) as image:
                if image.size != (found["width"], found["height"]):
                    raise ValueError("candidate dimensions mismatch")
                image.verify()
        except Exception as exc:
            raise MaskedRepairError(
                "LOCAL_EDIT_RAW_INVALID",
                "The saved candidate could not be verified; "
                "upload again or start a new edit.",
            ) from exc
        with self.session_factory() as session:
            existing = session.scalar(
                select(JobAssetRecord.id).where(
                    JobAssetRecord.job_id == request.job_id,
                    JobAssetRecord.job_item_id == request.job_item_id,
                    JobAssetRecord.direction == "intermediate",
                    JobAssetRecord.role == _RAW_ROLE,
                )
            )
            if existing is None:
                session.add(
                    JobAssetRecord(
                        workspace_id=request.workspace_id,
                        job_id=request.job_id,
                        job_item_id=request.job_item_id,
                        asset_id=str(found["id"]),
                        direction="intermediate",
                        role=_RAW_ROLE,
                        ordinal=0,
                        is_deliverable=False,
                        validation={**found["validation"], "candidate_reused": True},
                    )
                )
                session.commit()
        return MaskedEditCallResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id=None,
            raw_usage={
                "candidate_reused": True,
                "generation_id": found["validation"].get("generation_id"),
            },
            normalized_usage={
                "reported": True,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
            },
            actual_cost_micros=0,
            estimated_cost_micros=0,
            http_status=200,
            artifact=ProviderArtifact(
                payload=payload,
                filename="resumed-candidate.png",
                content_type="image/png",
                width=int(found["width"]),
                height=int(found["height"]),
                sha256=str(found["sha256"]),
            ),
        )

    def _reserve_call_budget(
        self,
        request: ProviderRequest,
        quality: str,
    ) -> None:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            item = session.get(JobItemRecord, request.job_item_id, with_for_update=True)
            attempt = session.get(
                JobAttemptRecord,
                request.attempt_id,
                with_for_update=True,
            )
            if (
                job is None
                or item is None
                or attempt is None
                or job.workspace_id != request.workspace_id
                or item.workspace_id != request.workspace_id
                or attempt.workspace_id != request.workspace_id
                or attempt.job_item_id != item.id
                or JobStatus(job.status) != JobStatus.PROCESSING
                or JobItemStatus(item.status) != JobItemStatus.RUNNING
                or AttemptStatus(attempt.status) != AttemptStatus.RUNNING
            ):
                raise MaskedRepairError(
                    "LOCAL_EDIT_CALL_NOT_AUTHORIZED",
                    "The durable attempt is not authorized for a provider call.",
                )
            if quota_enabled():
                reservation = session.scalar(
                    select(CreditReservationRecord).where(
                        CreditReservationRecord.workspace_id == request.workspace_id,
                        CreditReservationRecord.job_id == job.id,
                        CreditReservationRecord.status == "active",
                    )
                )
                if reservation is None:
                    raise MaskedRepairError(
                        "BUDGET_RESERVATION_MISSING",
                        "An active budget reservation is required before local edit.",
                    )
            if self.gateway.provider_name == "tengli":
                try:
                    require_provider_job_quota(session, job)
                except RuntimeError as exc:
                    raise MaskedRepairError(
                        "PROVIDER_QUOTA_RESERVATION_MISSING",
                        str(exc),
                    ) from exc
            existing = session.scalar(
                select(AuditEventRecord.id)
                .where(
                    AuditEventRecord.workspace_id == request.workspace_id,
                    AuditEventRecord.action == "masked_repair.image_reserved",
                    AuditEventRecord.request_id == attempt.id,
                )
                .limit(1)
            )
            if existing is not None:
                raise MaskedRepairError(
                    "LOCAL_EDIT_CALL_ALREADY_RESERVED",
                    "This durable attempt already reserved its provider call.",
                )
            review_exposure = int(getattr(self.gateway, "review_price_micros", 0) or 0)
            saved_candidate = session.scalar(
                select(JobAssetRecord.id).where(
                    JobAssetRecord.job_id == job.id,
                    JobAssetRecord.job_item_id == item.id,
                    JobAssetRecord.direction == "intermediate",
                    JobAssetRecord.role == _RAW_ROLE,
                )
            )
            image_exposure = (
                0
                if saved_candidate is not None
                else int(job.estimated_cost_max_micros or 0) * 2
            )
            exposure = image_exposure + review_exposure * _VISUAL_REVIEW_MAX_ATTEMPTS
            if (
                exposure <= 0 and saved_candidate is None
            ) or exposure > self.budget_limit_micros:
                raise MaskedRepairError(
                    "BUDGET_LIMIT_EXCEEDED",
                    "The local-edit maximum cost is outside the runner budget.",
                )
            prior: Sequence[dict[str, Any]] = session.scalars(
                select(AuditEventRecord.details)
                .select_from(AuditEventRecord)
                .join(
                    JobAttemptRecord,
                    AuditEventRecord.request_id == JobAttemptRecord.id,
                )
                .where(
                    JobAttemptRecord.job_id == job.id,
                    AuditEventRecord.workspace_id == request.workspace_id,
                    AuditEventRecord.action == "masked_repair.image_reserved",
                )
            ).all()
            prior_exposure = sum(
                int(value.get("estimated_cost_micros") or 0) for value in prior
            )
            if prior_exposure + exposure > self.budget_limit_micros:
                raise MaskedRepairError(
                    "BUDGET_LIMIT_EXCEEDED",
                    "Cumulative local-edit exposure exceeds the runner budget.",
                )
            session.add(
                AuditEventRecord(
                    workspace_id=request.workspace_id,
                    action="masked_repair.image_reserved",
                    resource_type="job_item",
                    resource_id=item.id,
                    request_id=attempt.id,
                    details={
                        "attempt_no": attempt.attempt_no,
                        "quality": quality,
                        "estimated_cost_micros": exposure,
                        "budget_limit_micros": self.budget_limit_micros,
                        "idempotency_key_sha256": hashlib.sha256(
                            request.idempotency_key.encode()
                        ).hexdigest(),
                    },
                )
            )
            session.commit()

    async def _persist_raw_artifact(
        self,
        request: ProviderRequest,
        artifact: ProviderArtifact,
        *,
        generation_id: str,
        provider_request_id: str | None,
        source_sha256: str,
        mask_sha256: str,
        normalized_source_sha256: str,
        authority_sha256: str,
    ) -> None:
        if artifact.content_type != "image/png":
            raise MaskedRepairError(
                "LOCAL_EDIT_RAW_FORMAT_INVALID",
                "The provider raw artifact must be a PNG image.",
            )
        object_key = (
            f"{request.workspace_id}/generated/local-edit/"
            f"{hashlib.sha256(request.idempotency_key.encode()).hexdigest()}-raw.png"
        )
        stored = await self.storage.save_validated(
            ValidatedImage(
                payload=artifact.payload,
                filename=f"{generation_id}-raw.png",
                content_type=artifact.content_type,
                width=artifact.width,
                height=artifact.height,
                object_key=object_key,
                sha256=artifact.sha256,
            )
        )
        try:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(AssetRecord).where(
                        AssetRecord.workspace_id == request.workspace_id,
                        AssetRecord.object_key == stored.object_key,
                    )
                )
                if existing is not None:
                    return
                limits = session.get(
                    WorkspaceLimitRecord,
                    request.workspace_id,
                    with_for_update=True,
                )
                used_bytes = session.scalar(
                    select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                        AssetRecord.workspace_id == request.workspace_id,
                        AssetRecord.lifecycle_status
                        == AssetLifecycleStatus.READY.value,
                        AssetRecord.deleted_at.is_(None),
                    )
                )
                if (
                    limits is None
                    or int(used_bytes or 0) + stored.byte_size
                    > limits.storage_quota_bytes
                ):
                    raise MaskedRepairError(
                        "STORAGE_QUOTA_EXCEEDED",
                        "The immutable raw result exceeds workspace storage quota.",
                    )
                asset_id = new_id("asset")
                session.add(
                    AssetRecord(
                        id=asset_id,
                        workspace_id=request.workspace_id,
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
                            "workflow_version": MASKED_REPAIR_WORKFLOW_VERSION,
                            "generation_id": generation_id,
                            "attempt_id": request.attempt_id,
                            "provider_request_id": provider_request_id,
                            "model": MASKED_REPAIR_MODEL,
                            "source_sha256": source_sha256,
                            "mask_sha256": mask_sha256,
                            "normalized_source_sha256": normalized_source_sha256,
                            "authority_sha256": authority_sha256,
                            "immutable_provider_raw": True,
                            "raw_visual_success": None,
                        },
                    )
                )
                session.add(
                    JobAssetRecord(
                        workspace_id=request.workspace_id,
                        job_id=request.job_id,
                        job_item_id=request.job_item_id,
                        asset_id=asset_id,
                        direction=JobAssetDirection.INTERMEDIATE.value,
                        role=_RAW_ROLE,
                        ordinal=0,
                        is_deliverable=False,
                        validation={
                            "generation_id": generation_id,
                            "sha256": stored.sha256,
                            "width": stored.width,
                            "height": stored.height,
                            "model": MASKED_REPAIR_MODEL,
                            "provider_request_id": provider_request_id,
                            "raw_visual_success": None,
                        },
                    )
                )
                session.commit()
        except Exception as exc:
            await self.storage.delete_object(
                stored.object_key,
                stored.object_version,
            )
            if isinstance(exc, MaskedRepairError):
                raise
            raise MaskedRepairError(
                "LOCAL_EDIT_RAW_PERSIST_FAILED",
                (
                    "The immutable provider result could not be persisted "
                    f"({type(exc).__name__})."
                ),
                retryable=True,
            ) from exc

    def _record_raw_visual_review(
        self,
        request: ProviderRequest,
        review: MaskedVisualReviewCallResult,
    ) -> None:
        with self.session_factory() as session:
            relation = session.scalar(
                select(JobAssetRecord).where(
                    JobAssetRecord.workspace_id == request.workspace_id,
                    JobAssetRecord.job_id == request.job_id,
                    JobAssetRecord.job_item_id == request.job_item_id,
                    JobAssetRecord.direction == JobAssetDirection.INTERMEDIATE.value,
                    JobAssetRecord.role == _RAW_ROLE,
                )
            )
            if relation is None:
                raise MaskedRepairError(
                    "LOCAL_EDIT_RAW_AUDIT_MISSING",
                    "The immutable raw result is missing its job relation.",
                )
            validation = dict(relation.validation or {})
            validation.update(
                {
                    "raw_visual_success": review.visual_success,
                    "visual_review_request_id": review.provider_request_id,
                    "visual_review_summary": review.summary,
                    "visual_review_reason_codes": list(review.reason_codes),
                    "visual_review_retry_guidance": review.retry_guidance,
                    "visual_review_observable_defect": review.observable_defect,
                    "visual_review_corrective_action": review.corrective_action,
                    "visual_review_version": MASKED_REPAIR_REVIEW_VERSION,
                    "visual_review_error_code": review.error_code,
                }
            )
            relation.validation = validation
            asset = session.get(AssetRecord, relation.asset_id)
            if asset is None or asset.workspace_id != request.workspace_id:
                raise MaskedRepairError(
                    "LOCAL_EDIT_RAW_AUDIT_MISSING",
                    "The immutable raw asset is missing.",
                )
            metadata = dict(asset.asset_metadata or {})
            metadata.update(
                {
                    "raw_visual_success": review.visual_success,
                    "visual_review_request_id": review.provider_request_id,
                    "visual_review_summary": review.summary,
                    "visual_review_reason_codes": list(review.reason_codes),
                    "visual_review_retry_guidance": review.retry_guidance,
                    "visual_review_observable_defect": review.observable_defect,
                    "visual_review_corrective_action": review.corrective_action,
                    "visual_review_version": MASKED_REPAIR_REVIEW_VERSION,
                    "visual_review_error_code": review.error_code,
                }
            )
            asset.asset_metadata = metadata
            session.add(
                AuditEventRecord(
                    workspace_id=request.workspace_id,
                    action="masked_repair.visual_review_completed",
                    resource_type="job_item",
                    resource_id=request.job_item_id,
                    request_id=request.attempt_id,
                    details={
                        "visual_success": review.visual_success,
                        "reason_codes": list(review.reason_codes),
                        "review_request_id": review.provider_request_id,
                        "retry_guidance": review.retry_guidance,
                        "observable_defect": review.observable_defect,
                        "corrective_action": review.corrective_action,
                        "review_version": MASKED_REPAIR_REVIEW_VERSION,
                        "error_code": review.error_code,
                    },
                )
            )
            session.commit()

    def _rejected(self, error: MaskedRepairError) -> ProviderResult:
        return ProviderResult(
            outcome=ProviderOutcome.FAILED,
            error_code=error.code,
            error_message=str(error),
            usage_reported=False,
            raw_usage={
                "provider_type": self.gateway.provider_name,
                "input_images": 0,
                "output_images": 0,
                "network_requests": 0,
                "metrics": {
                    "needs_reselection": error.needs_reselection,
                    "workflow_version": MASKED_REPAIR_WORKFLOW_VERSION,
                },
            },
            estimated_cost_micros=0,
            actual_cost_micros=0,
            retryable=error.retryable,
            provider_name=self.gateway.provider_name,
            model_name=MASKED_REPAIR_MODEL,
            pricing_version=self.gateway.pricing_version,
        )


def validate_local_edit_asset_records(
    source: AssetRecord,
    mask: AssetRecord,
) -> None:
    """Cheap API preflight; byte-level mask validation remains provider-side."""

    if source.id == mask.id:
        raise MaskedRepairError(
            "LOCAL_EDIT_INPUT_INVALID",
            "Source and selection mask must be different assets.",
        )
    if source.workspace_id != mask.workspace_id:
        raise MaskedRepairError(
            "WORKSPACE_SCOPE_MISMATCH",
            "Source and selection mask must belong to one workspace.",
        )
    if source.media_kind != AssetMediaKind.IMAGE.value:
        raise MaskedRepairError(
            "LOCAL_EDIT_SOURCE_KIND_INVALID",
            "The source asset must be an image.",
        )
    if mask.media_kind != AssetMediaKind.MASK.value:
        raise MaskedRepairError(
            "LOCAL_EDIT_MASK_KIND_INVALID",
            "The selection asset must have mask media kind.",
            needs_reselection=True,
        )
    if (
        source.width is None
        or source.height is None
        or source.width != mask.width
        or source.height != mask.height
    ):
        raise MaskedRepairError(
            "LOCAL_EDIT_DIMENSIONS_MISMATCH",
            "Source and selection mask dimensions must match.",
            needs_reselection=True,
        )


def _normalized_rgba(payload: bytes) -> Image.Image:
    with Image.open(BytesIO(payload)) as image:
        normalized = ImageOps.exif_transpose(image)
        normalized.load()
        return normalized.convert("RGBA")


def _binary_authority(mask: Image.Image) -> Image.Image:
    alpha = mask.getchannel("A")
    alpha_extrema = alpha.getextrema()
    basis = alpha if alpha_extrema != (255, 255) else ImageOps.grayscale(mask)
    return basis.point(lambda value: 255 if value > 127 else 0, mode="1").convert("L")


def _api_mask_png(authority: Image.Image) -> bytes:
    locked_alpha = ImageOps.invert(authority)
    mask = Image.new("RGBA", authority.size, (255, 255, 255, 255))
    mask.putalpha(locked_alpha)
    return _png_bytes(mask)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _materialize_candidate(
    normalized: _NormalizedInputs,
    raw: ProviderArtifact,
) -> Image.Image:
    try:
        generated = _normalized_rgba(raw.payload)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MaskedRepairError(
            "LOCAL_EDIT_RAW_IMAGE_INVALID",
            "The immutable provider result is not a valid image.",
        ) from exc
    mapped = _map_raw_to_source(normalized, generated)
    return Image.alpha_composite(
        normalized.source_image.convert("RGBA"),
        mapped.convert("RGBA"),
    )


def _strict_composite(
    normalized: _NormalizedInputs,
    materialized: Image.Image,
    *,
    feather_radius: int,
) -> tuple[ProviderArtifact, int]:
    generated = materialized.convert("RGBA")
    if generated.size != normalized.source_image.size:
        raise MaskedRepairError(
            "LOCAL_EDIT_RAW_GEOMETRY_INVALID",
            "The materialized provider result does not match the source canvas.",
        )
    inward = normalized.authority
    if feather_radius:
        blurred = normalized.authority.filter(
            ImageFilter.GaussianBlur(radius=feather_radius)
        )
        inward = ImageChops.multiply(blurred, normalized.authority)
    candidate = Image.composite(generated, normalized.source_image, inward)
    locked = Image.composite(
        candidate,
        normalized.source_image,
        normalized.authority,
    )
    outside_changed = _outside_changed_pixels(
        normalized.source_image,
        locked,
        normalized.authority,
    )
    if locked.size != normalized.source_image.size or outside_changed != 0:
        raise MaskedRepairError(
            "LOCAL_EDIT_PIXEL_LOCK_FAILED",
            "Strict local-edit compositing changed pixels outside authority.",
            needs_reselection=True,
        )
    payload = _png_bytes(locked)
    return (
        ProviderArtifact(
            payload=payload,
            filename="local-edit-result.png",
            content_type="image/png",
            width=locked.width,
            height=locked.height,
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
        outside_changed,
    )


def _outside_changed_pixels(
    source: Image.Image,
    result: Image.Image,
    authority: Image.Image,
) -> int:
    source_bytes = source.convert("RGBA").tobytes()
    result_bytes = result.convert("RGBA").tobytes()
    mask_bytes = authority.tobytes()
    changed = 0
    for offset, allowed in enumerate(mask_bytes):
        if allowed:
            continue
        start = offset * 4
        if source_bytes[start : start + 4] != result_bytes[start : start + 4]:
            changed += 1
    return changed


def _instruction_has_encoding_damage(instruction: str) -> bool:
    value = instruction.strip()
    if "\ufffd" in value:
        return True
    question_count = value.count("?")
    return question_count >= 3 and question_count * 4 >= len(value)


def _provider_quality(quality: str) -> str:
    return {
        "preview": "low",
        "balanced": "medium",
        "final": "high",
        "auto": "auto",
    }[quality]


_HEX_COLOUR_RE = re.compile(r"(?<![0-9a-f])#?(?P<hex>[0-9a-f]{6})(?![0-9a-f])", re.I)
_NAMED_COLOURS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (220, 32, 32),
    "green": (34, 150, 80),
    "blue": (32, 96, 220),
    "yellow": (245, 196, 32),
    "黑色": (0, 0, 0),
    "白色": (255, 255, 255),
    "红色": (220, 32, 32),
    "绿色": (34, 150, 80),
    "蓝色": (32, 96, 220),
    "黄色": (245, 196, 32),
}


def _deterministic_repair(
    source: Image.Image,
    authority: Image.Image,
    instruction: str,
) -> Image.Image | None:
    """Apply a measured flat-colour fill without a model call.

    This intentionally accepts only an explicit colour token.  Ambiguous
    instructions stay on a model path instead of silently changing pixels.
    """

    lowered = instruction.casefold()
    if not any(token in lowered for token in ("填充", "改为", "换成", "solid", "fill")):
        return None
    match = _HEX_COLOUR_RE.search(lowered)
    colour: tuple[int, int, int] | None = None
    if match:
        token = match.group("hex")
        colour = tuple(int(token[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[assignment]
    else:
        for name, value in _NAMED_COLOURS.items():
            if name.casefold() in lowered:
                colour = value
                break
    if colour is None:
        return None
    result = source.copy().convert("RGBA")
    pixels = result.load()
    for y in range(result.height):
        for x in range(result.width):
            if authority.getpixel((x, y)) > 0:  # type: ignore[operator]
                pixels[x, y] = (*colour, pixels[x, y][3])  # type: ignore[index]
    return result


def _effective_execution_path(
    requested: str,
    *,
    instruction: str,
    authority: Image.Image,
) -> str:
    """Resolve the Skill's fast-path choice without changing user authority.

    The deterministic/hybrid implementations remain provider-specific.  We
    therefore record the requested path and use a conservative model path for
    instructions that need synthesis.  ``auto`` chooses cropped context for a
    small selection and full canvas otherwise; this is observable in usage
    metrics and keeps retries on the normalized source.
    """

    if requested != "auto":
        return requested
    selected = sum(1 for value in authority.getdata() if value > 0)  # type: ignore[attr-defined,misc]
    total = authority.width * authority.height
    fraction = selected / total if total else 1.0
    lowered = instruction.casefold()
    # Flat colour/fill requests are eligible for a deterministic adapter in a
    # future provider release.  Until that adapter is available, keep the
    # model call explicit rather than silently claiming a local repair.
    if any(token in lowered for token in ("纯色", "填充", "solid", "flat color")):
        return "hybrid"
    return "cropped" if fraction <= 0.35 else "full"


def _master_prompt(user_request: str) -> str:
    return (
        "任务类型：严格局部图片编辑 / masked image edit。\n"
        "第一张输入图是唯一原始画布。透明蒙版区域仅用于模型编辑上下文；"
        "最终允许修改的范围以任务中确认的 authority selection 为准。\n"
        f"用户要求：{user_request}\n\n"
        "规则：1. 只完成用户要求的局部修改。"
        "2. 延续原图的机位、透视、光向、曝光、白平衡、阴影、景深、锐度、"
        "颗粒、压缩质感和风格。"
        "3. 不重新拍摄、重构、美化或顺手修改整张图。"
        "4. 保持边缘遮挡、纹理、结构和真实光影连续。"
        "5. 除非用户明确要求修改，否则保留选区内原有产品身份、Logo、标签、"
        "文字、图案和其他标记。"
        "6. 不添加说明、对比图、边框、标记、水印或额外文字。"
        "7. 输出一张与原画布尺寸和构图一致的完成图。"
    )


def _generation_id(
    attempt_id: str,
    provider_request_id: str | None,
    raw_sha256: str,
) -> str:
    token = hashlib.sha256(
        f"{attempt_id}:{provider_request_id or 'missing'}:{raw_sha256}".encode()
    ).hexdigest()[:24]
    return f"gen_{token}"


def _combined_cost(
    left: int | None,
    right: int | None,
    *,
    require_complete: bool = False,
) -> int | None:
    if require_complete and (left is None or right is None):
        return None
    values = [value for value in (left, right) if value is not None]
    return sum(values) if values else None


def _combined_usage_value(
    image_value: object,
    review_value: object,
    *,
    review_performed: bool,
) -> int | None:
    if isinstance(image_value, bool) or not isinstance(image_value, int):
        return None
    if not review_performed:
        return image_value
    if isinstance(review_value, bool) or not isinstance(review_value, int):
        return None
    return image_value + review_value


def _aggregate_review_usage_value(
    attempts: Sequence[MaskedVisualReviewCallResult],
    key: str,
) -> int | None:
    values = [attempt.normalized_usage.get(key) for attempt in attempts]
    if not values or any(
        isinstance(value, bool) or not isinstance(value, int) for value in values
    ):
        return None
    return sum(cast(int, value) for value in values)


def _usage_record(
    result: MaskedEditCallResult,
    *,
    review_attempts: Sequence[MaskedVisualReviewCallResult],
    call_count: int,
    source_sha256: str,
    mask_sha256: str,
    normalized_source_sha256: str,
    authority_sha256: str,
    execution_path: str,
) -> dict[str, Any]:
    normalized = dict(result.normalized_usage)
    from .provider_diagnostics import retained_metrics

    attempts = tuple(review_attempts)
    review_performed = bool(attempts)
    reviews_reported = all(
        bool(attempt.normalized_usage.get("reported")) for attempt in attempts
    )
    reported = bool(normalized.get("reported")) and (
        not review_performed or reviews_reported
    )
    review_input_tokens = _aggregate_review_usage_value(attempts, "input_tokens")
    review_output_tokens = _aggregate_review_usage_value(attempts, "output_tokens")
    review_cached_tokens = _aggregate_review_usage_value(attempts, "cached_tokens")
    return {
        "provider_type": "masked_image_edit_with_visual_review",
        "reported": reported,
        "input_tokens": _combined_usage_value(
            normalized.get("input_tokens"),
            review_input_tokens,
            review_performed=review_performed,
        ),
        "output_tokens": _combined_usage_value(
            normalized.get("output_tokens"),
            review_output_tokens,
            review_performed=review_performed,
        ),
        "cached_tokens": _combined_usage_value(
            normalized.get("cached_tokens"),
            review_cached_tokens,
            review_performed=review_performed,
        ),
        "input_images": (2 if call_count else 0) + (3 * len(attempts)),
        "output_images": 1 if result.artifact is not None and call_count else 0,
        "network_requests": call_count + len(attempts),
        "raw": {
            "image_edit": dict(result.raw_usage or {}),
            "visual_review": (
                [dict(attempt.raw_usage or {}) for attempt in attempts]
                if review_performed
                else None
            ),
        },
        "normalized": {
            "image_edit": normalized,
            "visual_review": (
                [dict(attempt.normalized_usage) for attempt in attempts]
                if review_performed
                else None
            ),
        },
        "metrics": {
            **retained_metrics(result.raw_usage),
            "source_sha256": source_sha256,
            "mask_sha256": mask_sha256,
            "normalized_source_sha256": normalized_source_sha256,
            "authority_sha256": authority_sha256,
            "transport_attempts": call_count,
            "candidate_reused": bool((result.raw_usage or {}).get("candidate_reused")),
            "visual_review_requests": len(attempts),
            "visual_review_attempts": [
                {
                    "attempt": index,
                    "provider_request_id": attempt.provider_request_id,
                    "http_status": attempt.http_status,
                    "error_code": attempt.error_code,
                    "retryable": attempt.retryable,
                    "visual_success": attempt.visual_success,
                    "estimated_cost_micros": attempt.estimated_cost_micros,
                    "actual_cost_micros": attempt.actual_cost_micros,
                }
                for index, attempt in enumerate(attempts, start=1)
            ],
            "durable_idempotency_reused": call_count > 1 or len(attempts) > 1,
            "execution_path": execution_path,
            "mask_protocol": "source_plus_explicit_mask_v1",
            "workflow_version": MASKED_REPAIR_WORKFLOW_VERSION,
        },
    }


def _build_provider_canvas(
    source: Image.Image,
    authority: Image.Image,
) -> _ProviderCanvas:
    target_width, target_height, scaled_width, scaled_height = _provider_dimensions(
        source.width,
        source.height,
    )
    scaled_source = source
    scaled_authority = authority
    if (scaled_width, scaled_height) != source.size:
        scaled_source = source.resize(
            (scaled_width, scaled_height),
            Image.Resampling.LANCZOS,
        )
        scaled_authority = authority.resize(
            (scaled_width, scaled_height),
            Image.Resampling.NEAREST,
        )
    offset_x = (target_width - scaled_width) // 2
    offset_y = (target_height - scaled_height) // 2
    source_box = (
        offset_x,
        offset_y,
        offset_x + scaled_width,
        offset_y + scaled_height,
    )
    edge_color = scaled_source.getpixel((0, 0))
    provider_source = Image.new("RGBA", (target_width, target_height), edge_color)
    provider_source.paste(scaled_source, (offset_x, offset_y))
    provider_authority = Image.new("L", (target_width, target_height), 0)
    provider_authority.paste(scaled_authority, (offset_x, offset_y))
    return _ProviderCanvas(
        source_png=_png_bytes(provider_source),
        api_mask_png=_api_mask_png(provider_authority),
        width=target_width,
        height=target_height,
        source_box=source_box,
    )


def _provider_dimensions(
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise MaskedRepairError(
            "LOCAL_EDIT_DIMENSIONS_INVALID",
            "The source canvas dimensions are invalid.",
        )
    area = width * height
    scale = 1.0
    if area < _PROVIDER_MIN_PIXELS:
        scale = sqrt(_PROVIDER_MIN_PIXELS / area)
    scale = min(
        scale,
        _PROVIDER_MAX_EDGE / max(width, height),
        sqrt(_PROVIDER_MAX_PIXELS / area),
    )
    scaled_width = max(1, round(width * scale))
    scaled_height = max(1, round(height * scale))
    for _ in range(8):
        target_width = ceil(scaled_width / _PROVIDER_EDGE_MULTIPLE) * 16
        target_height = ceil(scaled_height / _PROVIDER_EDGE_MULTIPLE) * 16
        if target_width / target_height > _PROVIDER_MAX_RATIO:
            target_height = (
                ceil(target_width / _PROVIDER_MAX_RATIO / _PROVIDER_EDGE_MULTIPLE)
                * _PROVIDER_EDGE_MULTIPLE
            )
        elif target_height / target_width > _PROVIDER_MAX_RATIO:
            target_width = (
                ceil(target_height / _PROVIDER_MAX_RATIO / _PROVIDER_EDGE_MULTIPLE)
                * _PROVIDER_EDGE_MULTIPLE
            )
        target_area = target_width * target_height
        if target_area < _PROVIDER_MIN_PIXELS:
            factor = sqrt(_PROVIDER_MIN_PIXELS / target_area) * 1.01
            scaled_width = max(1, round(scaled_width * factor))
            scaled_height = max(1, round(scaled_height * factor))
            continue
        if (
            target_area > _PROVIDER_MAX_PIXELS
            or max(target_width, target_height) > _PROVIDER_MAX_EDGE
        ):
            factor = (
                min(
                    sqrt(_PROVIDER_MAX_PIXELS / target_area),
                    _PROVIDER_MAX_EDGE / max(target_width, target_height),
                )
                * 0.99
            )
            scaled_width = max(1, round(scaled_width * factor))
            scaled_height = max(1, round(scaled_height * factor))
            continue
        return target_width, target_height, scaled_width, scaled_height
    raise MaskedRepairError(
        "LOCAL_EDIT_PROVIDER_DIMENSIONS_UNAVAILABLE",
        "A legal provider canvas could not be derived without distortion.",
    )


def _map_raw_to_source(
    normalized: _NormalizedInputs,
    generated: Image.Image,
) -> Image.Image:
    target = normalized.provider_canvas
    scale = max(target.width / generated.width, target.height / generated.height)
    mapped_width = max(target.width, ceil(generated.width * scale))
    mapped_height = max(target.height, ceil(generated.height * scale))
    crop_fraction = max(
        (mapped_width - target.width) / mapped_width,
        (mapped_height - target.height) / mapped_height,
    )
    if crop_fraction > _MAX_RELAY_CROP_FRACTION:
        raise MaskedRepairError(
            "LOCAL_EDIT_RAW_GEOMETRY_UNMAPPABLE",
            "The provider result drifted too far for a non-distorting map.",
        )
    mapped = generated.resize(
        (mapped_width, mapped_height),
        Image.Resampling.LANCZOS,
    )
    left = (mapped_width - target.width) // 2
    top = (mapped_height - target.height) // 2
    provider_canvas = mapped.crop((left, top, left + target.width, top + target.height))
    source_region = provider_canvas.crop(target.source_box)
    if source_region.size != normalized.source_image.size:
        source_region = source_region.resize(
            normalized.source_image.size,
            Image.Resampling.LANCZOS,
        )
    return source_region
