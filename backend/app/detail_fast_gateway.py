"""Durable, single-dispatch image calls for the domestic fast-detail route."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress
from dataclasses import replace
from io import BytesIO
from time import monotonic
from typing import Any
from urllib.parse import urljoin

from PIL import Image
from sqlalchemy import select

from .errors import ApiError
from .models import JobAttemptRecord, JobItemRecord, JobPlanRecord, JobRecord, utc_now
from .provider import ProviderArtifact, ProviderOutcome
from .storage import ALLOWED_IMAGE_FORMATS, ImageStorage, ValidatedImage
from .xinghe_agent import ImageCallResult, ImageEditRequest

PIPELINE = "xinghe-domestic-detail-v4.0-fast"
RECEIPTS = "_detail_fast_invocations"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class DurableFastDetailGateway:
    """Fence paid submissions and keep original bytes before common delivery.

    The caller remains responsible for validating the Skill/plan and for budget,
    technical delivery checks, credits and publishing. Only this fast pipeline
    can use this adapter; all other gateway methods/identity are forwarded.
    """

    def __init__(
        self,
        session_factory: Any,
        gateway: Any,
        storage: ImageStorage | None,
        *,
        skill_id: str,
        skill_content_hash: str,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.storage = storage
        self.skill_id = skill_id
        self.skill_content_hash = skill_content_hash

    def __getattr__(self, key: str) -> Any:
        return getattr(self.gateway, key)

    def _accepted_recovery(self, receipt: dict[str, Any]) -> dict[str, Any] | None:
        """Admit only a unique persisted Haiyu task, without a network call."""
        from .haiyu_transport import HaiyuTransport

        transport = getattr(self.gateway, "transport", None)
        if not isinstance(transport, HaiyuTransport):
            return None
        key = receipt.get("recovery_idempotency_key") or receipt.get("idempotency_key")
        if not isinstance(key, str) or not key:
            return None
        try:
            matches = [
                task
                for route in self.gateway._image_routes()
                if (
                    task := transport.accepted_task(
                        urljoin(route.image_base_url, "v1/images/edits"),
                        key,
                        route.image_model,
                    )
                )
            ]
        except Exception:
            return None
        if len(matches) != 1:
            return None
        return {
            "job_id": receipt["job_id"],
            "job_item_id": receipt["job_item_id"],
            "attempt_id": receipt["attempt_id"],
            "idempotency_key": key,
            "accepted_task_id": matches[0],
        }

    def _binding(self, request: ImageEditRequest) -> dict[str, Any]:
        sources = []
        for source in request.sources:
            sha = hashlib.sha256(source.payload).hexdigest()
            if sha != source.sha256:
                raise ApiError(
                    409, "DETAIL_FAST_SOURCE_CHANGED", "商品原图校验不一致。"
                )
            sources.append(
                {
                    "asset_id": source.asset_id,
                    "group_key": source.group_key,
                    "role": source.source_role,
                    "sha256": sha,
                    "content_type": source.content_type,
                }
            )
        return {
            "workspace_id": request.workspace_id,
            "pipeline": PIPELINE,
            "skill_id": self.skill_id,
            "skill_content_hash": self.skill_content_hash,
            "model": getattr(self.gateway, "image_model", None),
            "provider": getattr(self.gateway, "provider_name", None),
            "page_number": request.page_number,
            "group_key": request.group_key,
            "prompt": request.prompt,
            "prompt_sha256": hashlib.sha256(request.prompt.encode()).hexdigest(),
            "sources": sources,
            "size": request.size,
            "quality": request.quality,
            "output_format": request.output_format,
            "jpeg_quality": request.jpeg_quality,
            "input_fidelity": request.input_fidelity,
        }

    def _scope(
        self, session: Any, request: ImageEditRequest
    ) -> tuple[JobRecord, JobItemRecord]:
        job = session.get(JobRecord, request.job_id, with_for_update=True)
        item = session.get(JobItemRecord, request.job_item_id)
        attempt = session.get(JobAttemptRecord, request.attempt_id)
        if (
            job is None
            or item is None
            or attempt is None
            or job.type != "detail_pack"
            or job.status != "processing"
            or job.workspace_id != request.workspace_id
            or job.configuration.get("domestic_detail_pipeline") != PIPELINE
            or item.workspace_id != request.workspace_id
            or item.job_id != job.id
            or attempt.workspace_id != request.workspace_id
            or attempt.job_id != job.id
            or attempt.job_item_id != item.id
            or attempt.status != "running"
            or attempt.provider_idempotency_key != request.idempotency_key
        ):
            raise ApiError(
                409, "DETAIL_FAST_SCOPE_INVALID", "快速详情任务归属或执行状态无效。"
            )
        plan = session.scalar(
            select(JobPlanRecord)
            .where(
                JobPlanRecord.job_id == job.id,
                JobPlanRecord.workspace_id == job.workspace_id,
            )
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
            .with_for_update()
        )
        metadata = plan.payload.get("detail_fast") if plan is not None else None
        if (
            plan is None
            or plan.validation_status != "valid"
            or not isinstance(metadata, dict)
            or metadata.get("version") != PIPELINE
            or metadata.get("skill_id") != self.skill_id
            or metadata.get("skill_content_hash") != self.skill_content_hash
            or not item.configuration.get("resume_signature")
            or plan.payload.get("resume_signature")
            != item.configuration["resume_signature"]
        ):
            raise ApiError(
                409, "DETAIL_FAST_BINDING_CHANGED", "Skill 或冻结整套方案签名已变化。"
            )
        return job, item

    def _claim(
        self, request: ImageEditRequest, binding: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            job, item = self._scope(session, request)
            resume_signature = item.configuration.get("resume_signature")
            if not isinstance(resume_signature, str) or not resume_signature:
                raise ApiError(
                    409, "DETAIL_FAST_BINDING_CHANGED", "完整套图方案签名缺失。"
                )
            binding = {**binding, "resume_signature": resume_signature}
            current, source_item = job, item
            recovery: dict[str, Any] | None = None
            seen: set[str] = set()
            # Retry jobs keep frozen configuration. Match the prior page by its
            # stable ordinal, never by completion order or sparse array index.
            while True:
                if current.id in seen:
                    raise ApiError(409, "DETAIL_FAST_SCOPE_INVALID", "重试任务链无效。")
                seen.add(current.id)
                old = dict(current.configuration.get(RECEIPTS, {})).get(source_item.id)
                if old:
                    if old.get("binding_hash") != _hash(binding) or old.get(
                        "binding_hash"
                    ) != _hash(old.get("binding")):
                        raise ApiError(
                            409,
                            "DETAIL_FAST_BINDING_CHANGED",
                            "已有调用的方案、素材或 Skill 已变化。",
                        )
                    if (
                        old.get("workspace_id") != request.workspace_id
                        or old.get("job_id") != current.id
                        or old.get("job_item_id") != source_item.id
                        or old.get("item_ordinal") != item.ordinal
                    ):
                        raise ApiError(
                            409, "DETAIL_FAST_SCOPE_INVALID", "原图回执归属不一致。"
                        )
                    if old.get("state") == "returned":
                        return dict(old)
                    if (
                        old.get("state") in {"unknown", "invoked"}
                        and current.id != job.id
                        and job.retry_scope == "failed"
                    ):
                        recovery = self._accepted_recovery(old)
                        if recovery:
                            break
                    if old.get("state") != "failed" or current.id == job.id:
                        raise ApiError(
                            409,
                            "PROVIDER_OUTCOME_UNKNOWN",
                            "原调用结果尚未核实，已停止重复提交。",
                        )
                    # A known rejection may dispatch again only from an explicit
                    # new retry job. Do not skip past the latest failed call.
                    break
                if not current.retry_of_job_id:
                    break
                if current.id == job.id and current.retry_scope == "selected":
                    # A new selected-page revision explicitly authorizes a new
                    # image from changed copy/composition. Other pages are kept
                    # by the common retry path; same-job receipts still fence.
                    break
                if current.retry_scope != "failed":
                    raise ApiError(
                        409, "DETAIL_FAST_SCOPE_INVALID", "只有失败项重试可恢复原图。"
                    )
                previous = session.get(JobRecord, current.retry_of_job_id)
                if (
                    previous is None
                    or previous.workspace_id != job.workspace_id
                    or previous.type != job.type
                    or previous.configuration.get("domestic_detail_pipeline")
                    != PIPELINE
                ):
                    raise ApiError(409, "DETAIL_FAST_SCOPE_INVALID", "原任务归属无效。")
                prior_item = session.scalar(
                    select(JobItemRecord).where(
                        JobItemRecord.job_id == previous.id,
                        JobItemRecord.workspace_id == job.workspace_id,
                        JobItemRecord.ordinal == item.ordinal,
                    )
                )
                if prior_item is None or prior_item.item_key != item.item_key:
                    raise ApiError(
                        409, "DETAIL_FAST_SCOPE_INVALID", "原任务页码绑定无效。"
                    )
                current, source_item = previous, prior_item
            receipts = dict(job.configuration.get(RECEIPTS, {}))
            receipts[item.id] = {
                "state": "invoked",
                "workspace_id": request.workspace_id,
                "job_id": job.id,
                "job_item_id": item.id,
                "item_ordinal": item.ordinal,
                "attempt_id": request.attempt_id,
                "idempotency_key": request.idempotency_key,
                "binding": binding,
                "binding_hash": _hash(binding),
                "started_at": utc_now().isoformat(),
                "finished_at": None,
                "api_latency_seconds": None,
                "actual_cost_micros": None,
                **(
                    {
                        "recovery_source": recovery,
                        "recovery_idempotency_key": recovery["idempotency_key"],
                    }
                    if recovery
                    else {}
                ),
            }
            job.configuration = {**job.configuration, RECEIPTS: receipts}
            session.commit()
            return {"recovery": recovery} if recovery else {}

    def _finish(self, request: ImageEditRequest, values: dict[str, Any]) -> None:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            if job is None or job.workspace_id != request.workspace_id:
                raise ApiError(409, "DETAIL_FAST_SCOPE_INVALID", "任务回执归属无效。")
            receipts = dict(job.configuration.get(RECEIPTS, {}))
            old = receipts.get(request.job_item_id, {})
            if (
                old.get("attempt_id") != request.attempt_id
                or old.get("state") != "invoked"
            ):
                raise ApiError(409, "PROVIDER_OUTCOME_UNKNOWN", "调用回执已被接续。")
            receipts[request.job_item_id] = {
                **old,
                **values,
                "finished_at": utc_now().isoformat(),
            }
            job.configuration = {**job.configuration, RECEIPTS: receipts}
            session.commit()

    async def _reuse(
        self, request: ImageEditRequest, receipt: dict[str, Any]
    ) -> ImageCallResult:
        record = receipt["artifact"]
        prefix = f"{request.workspace_id}/detail-fast/{receipt['job_id']}/"
        page_number = receipt["binding"]["page_number"]
        suffix = next(
            (
                suffix
                for suffix, mime in ALLOWED_IMAGE_FORMATS.values()
                if mime == record["content_type"]
            ),
            None,
        )
        sha = record["sha256"]
        if (
            not isinstance(sha, str)
            or len(sha) != 64
            or any(char not in "0123456789abcdef" for char in sha)
            or type(page_number) is not int
            or page_number < 1
            or not suffix
            or record["object_key"] != f"{prefix}page-{page_number:03d}-{sha}{suffix}"
        ):
            raise ApiError(409, "DETAIL_FAST_SCOPE_INVALID", "原图存储归属无效。")
        if self.storage is None:
            raise ApiError(503, "DETAIL_FAST_STORAGE_REQUIRED", "原图存储服务不可用。")
        data = await self.storage.read_bytes(record["object_key"])
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ApiError(409, "DETAIL_FAST_RESULT_CHANGED", "保留原图校验不一致。")
        artifact = ProviderArtifact(
            payload=data,
            **{
                key: record[key]
                for key in ("filename", "content_type", "width", "height", "sha256")
            },
        )
        return ImageCallResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id=f"detail-fast-reused-{request.attempt_id}",
            raw_usage={"network_requests": 0, "reused": True},
            normalized_usage={
                "reported": True,
                "network_requests": 0,
                "reused": True,
                "input_tokens": 0,
                "output_tokens": 0,
                "review_status": "NOT_REVIEWED",
                "original_provider_request_id": receipt.get(
                    "original_provider_request_id"
                )
                or receipt.get("provider_request_id"),
            },
            api_latency_seconds=0,
            actual_cost_micros=0,
            estimated_cost_micros=0,
            artifact=artifact,
            http_status=200,
        )

    async def _save(
        self, request: ImageEditRequest, artifact: ProviderArtifact
    ) -> dict[str, Any]:
        with Image.open(BytesIO(artifact.payload)) as image:
            image.load()
            suffix, content_type = ALLOWED_IMAGE_FORMATS[image.format or ""]
            width, height = image.size
        sha = hashlib.sha256(artifact.payload).hexdigest()
        key = (
            f"{request.workspace_id}/detail-fast/"
            f"{request.job_id}/page-{request.page_number:03d}-{sha}{suffix}"
        )
        if self.storage is None:
            raise ApiError(503, "DETAIL_FAST_STORAGE_REQUIRED", "原图存储服务不可用。")
        stored = await self.storage.save_validated(
            ValidatedImage(
                payload=artifact.payload,
                filename="detail-fast-original" + suffix,
                content_type=content_type,
                width=width,
                height=height,
                object_key=key,
                sha256=sha,
            )
        )
        return {
            "object_key": stored.object_key,
            "object_version": stored.object_version,
            "filename": stored.filename,
            "content_type": content_type,
            "width": width,
            "height": height,
            "sha256": sha,
        }

    @staticmethod
    def _blocked(error: Exception) -> ImageCallResult:
        code = getattr(error, "code", "DETAIL_FAST_RECOVERY_FAILED")
        unknown = code == "PROVIDER_OUTCOME_UNKNOWN"
        return ImageCallResult(
            outcome=ProviderOutcome.OUTCOME_UNKNOWN
            if unknown
            else ProviderOutcome.FAILED,
            provider_request_id=None,
            raw_usage={"network_requests": 0},
            normalized_usage={"reported": True, "network_requests": 0},
            api_latency_seconds=0,
            actual_cost_micros=0,
            estimated_cost_micros=0,
            error_code=code,
            error_message=str(error),
        )

    async def edit(self, request: ImageEditRequest) -> ImageCallResult:
        request = replace(
            request, single_dispatch=True, preserve_provider_artifact=True
        )
        recovery: dict[str, Any] | None = None
        try:
            if self.storage is None:
                raise ApiError(
                    503, "DETAIL_FAST_STORAGE_REQUIRED", "原图存储服务不可用。"
                )
            binding = self._binding(request)
            cached = self._claim(request, binding)
            if cached:
                recovery = cached.get("recovery")
                if recovery is None:
                    return await self._reuse(request, cached)
        except Exception as error:
            return self._blocked(error)

        started = monotonic()
        result: ImageCallResult | None = None
        try:
            wire_request = (
                replace(
                    request,
                    idempotency_key=recovery["idempotency_key"],
                    reconcile_only=True,
                )
                if recovery
                else request
            )
            result = await self.gateway.edit(wire_request)
            if recovery:
                # A failed query/download does not establish that the original
                # paid generation failed. Keep its original recovery fence.
                if (
                    result.outcome != ProviderOutcome.SUCCEEDED
                    or result.artifact is None
                ):
                    result = replace(
                        result,
                        outcome=ProviderOutcome.OUTCOME_UNKNOWN,
                        error_code="PROVIDER_OUTCOME_UNKNOWN",
                        retryable=False,
                    )
                original_request_id = result.provider_request_id
                result = replace(
                    result,
                    provider_request_id=f"detail-fast-recovered-{request.attempt_id}",
                    actual_cost_micros=0,
                    estimated_cost_micros=0,
                    normalized_usage={
                        "reported": True,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "network_requests": 1,
                        "generation_requests": 0,
                        "recovery_requests": 1,
                        "recovery": True,
                        "original_provider_request_id": original_request_id,
                    },
                    raw_usage={
                        **(result.raw_usage or {}),
                        "generation_requests": 0,
                        "recovery_requests": 1,
                        "recovery": True,
                    },
                )
            unknown = (
                result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
                or result.error_code
                in {"PROVIDER_OUTCOME_UNKNOWN", "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"}
                or result.outcome == ProviderOutcome.SUCCEEDED
                and result.artifact is None
            )
            state = "unknown" if unknown else "failed"
            artifact = None
            if (
                result.outcome == ProviderOutcome.SUCCEEDED
                and result.artifact is not None
            ):
                artifact = await self._save(request, result.artifact)
                state = "returned"
            self._finish(
                request,
                {
                    "state": state,
                    "artifact": artifact,
                    "provider_request_id": result.provider_request_id,
                    "original_provider_request_id": result.normalized_usage.get(
                        "original_provider_request_id"
                    ),
                    "api_latency_seconds": result.api_latency_seconds,
                    "elapsed_seconds": monotonic() - started,
                    "raw_usage": result.raw_usage,
                    "usage": result.normalized_usage,
                    "actual_cost_micros": result.actual_cost_micros,
                    "estimated_cost_micros": result.estimated_cost_micros,
                    "error_code": result.error_code,
                },
            )
            if unknown:
                return replace(
                    result,
                    outcome=ProviderOutcome.OUTCOME_UNKNOWN,
                    error_code="PROVIDER_OUTCOME_UNKNOWN",
                    artifact=None,
                    retryable=False,
                )
            return replace(
                result,
                normalized_usage={
                    **result.normalized_usage,
                    "review_status": "NOT_REVIEWED",
                },
                retryable=False,
            )
        except BaseException as error:
            # A timeout, cancellation, image download/storage failure or failed
            # receipt write cannot prove that no paid generation took place.
            # The durable pre-dispatch receipt still fences retries if this
            # best-effort diagnostic update cannot be committed.
            with suppress(Exception):
                self._finish(
                    request,
                    {
                        "state": "unknown",
                        "elapsed_seconds": monotonic() - started,
                        "api_latency_seconds": getattr(
                            result, "api_latency_seconds", None
                        ),
                        "provider_request_id": getattr(
                            result, "provider_request_id", None
                        ),
                        "actual_cost_micros": 0
                        if recovery
                        else getattr(result, "actual_cost_micros", None),
                        "usage": getattr(result, "normalized_usage", None),
                        "error_code": "PROVIDER_OUTCOME_UNKNOWN",
                    },
                )
            if isinstance(
                error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise
            return ImageCallResult(
                outcome=ProviderOutcome.OUTCOME_UNKNOWN,
                provider_request_id=getattr(result, "provider_request_id", None),
                raw_usage={"network_requests": 1},
                normalized_usage={
                    **(getattr(result, "normalized_usage", None) or {}),
                    "network_requests": 1,
                    **(
                        {
                            "generation_requests": 0,
                            "recovery_requests": 1,
                            "recovery": True,
                        }
                        if recovery
                        else {}
                    ),
                },
                api_latency_seconds=monotonic() - started,
                actual_cost_micros=0
                if recovery
                else getattr(result, "actual_cost_micros", None),
                estimated_cost_micros=0
                if recovery
                else getattr(result, "estimated_cost_micros", None),
                error_code="PROVIDER_OUTCOME_UNKNOWN",
                error_message="原生图结果尚未核实，已停止重复提交。",
            )
