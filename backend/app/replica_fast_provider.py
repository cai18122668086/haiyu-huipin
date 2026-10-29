"""Bounded parallel fast-suite images with durable pre-dispatch receipts."""

from __future__ import annotations

import hashlib
import json
import logging
from io import BytesIO
from typing import Any

from PIL import Image

from .errors import ApiError
from .models import (
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    ReplicaDocumentRecord,
    utc_now,
)
from .new_detail_skill_provider import BundledSkillManifestValidator
from .provider import ProviderArtifact, ProviderOutcome, ProviderRequest, ProviderResult
from .replica_exact_contracts import canonical_hash
from .replica_fast import (
    SKILLS,
    VERSION,
    crop_source,
    image_prompt,
    selected,
    source_binding,
)
from .services import get_ready_assets
from .storage import ValidatedImage
from .xinghe_agent import ImageEditRequest, SourceImage, XingheGateway

logger = logging.getLogger(__name__)


class FastReplicaProvider:
    def __init__(
        self, session_factory: Any, gateway: XingheGateway, storage: Any
    ) -> None:
        self.session_factory, self.gateway, self.storage = (
            session_factory,
            gateway,
            storage,
        )
        self.name = getattr(gateway, "provider_name", "fast-replica")
        self.model = getattr(gateway, "image_model", "none")
        self.pricing_version = getattr(gateway, "pricing_version", VERSION)

    def _load(
        self, request: ProviderRequest
    ) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            item = session.get(JobItemRecord, request.job_item_id)
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if (
                job is None
                or item is None
                or attempt is None
                or request.operation != "reference_remix"
                or job.workspace_id != request.workspace_id
                or item.workspace_id != request.workspace_id
                or item.job_id != job.id
                or attempt.job_id != job.id
                or attempt.job_item_id != item.id
                or attempt.workspace_id != request.workspace_id
                or attempt.status != "running"
                or attempt.provider_idempotency_key != request.idempotency_key
                or job.status != "processing"
            ):
                raise ApiError(
                    409,
                    "FAST_REPLICA_SCOPE_INVALID",
                    "快速复刻任务归属或执行状态无效。",
                )
            document_id = job.configuration.get("replica_session_id")
            row = (
                session.get(ReplicaDocumentRecord, document_id) if document_id else None
            )
            settings = job.configuration.get("settings", {})
            if (
                row is None
                or row.workspace_id != request.workspace_id
                or job.id not in row.payload.get("job_ids", [])
                or not selected(row.payload)
                or settings.get("reference_skill") not in SKILLS.values()
            ):
                raise ApiError(
                    409, "FAST_REPLICA_SCOPE_INVALID", "快速方案不属于当前任务。"
                )
            plan = row.payload.get("_fast_plan", {})
            if (
                plan.get("version") != VERSION
                or plan.get("document_id") != row.id
                or plan.get("workspace_id") != request.workspace_id
                or canonical_hash(plan) != row.payload.get("plan_hash")
                or settings.get("replica_plan_hash") != row.payload.get("plan_hash")
                or settings.get("reference_skill") != plan.get("skill_id")
                or settings.get("replication_method") != plan.get("replication_method")
                or SKILLS.get(plan.get("replication_method")) != plan.get("skill_id")
                or settings.get("image_count") != len(plan.get("units", []))
            ):
                raise ApiError(
                    409,
                    "FAST_REPLICA_PLAN_CHANGED",
                    "快速方案绑定发生变化，请新建复刻。",
                )
            if (
                BundledSkillManifestValidator().validate(plan["skill_id"])
                != plan["skill_content_hash"]
            ):
                raise ApiError(
                    409, "REPLICA_SKILL_CHANGED", "快速 Skill 版本已变化，请重新分析。"
                )
            if not 0 <= item.ordinal < len(plan["units"]):
                raise ApiError(409, "FAST_REPLICA_SCOPE_INVALID", "快速方案屏号无效。")
            ids = list(plan["product_asset_ids"]) + [
                u["reference"]["asset_id"] for u in plan["units"]
            ]
            assets = get_ready_assets(
                session, request.workspace_id, list(dict.fromkeys(ids))
            )
            records = {
                key: (
                    a.original_filename,
                    a.content_type,
                    a.sha256,
                    a.object_key,
                    a.width,
                    a.height,
                )
                for key, a in assets.items()
            }
            values = {
                "product_asset_ids": plan["product_asset_ids"],
                "references": row.payload["references"],
            }
            if source_binding(values, records) != plan["source_bindings"]:
                raise ApiError(
                    409, "FAST_REPLICA_SOURCE_CHANGED", "快速方案的原始素材已变化。"
                )
            return row.id, plan, plan["units"][item.ordinal], records

    def _claim(
        self,
        document_id: str,
        plan: dict[str, Any],
        unit: dict[str, Any],
        request: ProviderRequest,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if (
                job is None
                or job.status != "processing"
                or attempt is None
                or attempt.status != "running"
                or attempt.job_id != job.id
                or job.workspace_id != request.workspace_id
            ):
                raise ApiError(409, "FAST_REPLICA_SCOPE_INVALID", "任务已中止。")
            row = session.get(ReplicaDocumentRecord, document_id, with_for_update=True)
            if row.workspace_id != request.workspace_id or row.payload.get(
                "plan_hash"
            ) != canonical_hash(plan):
                raise ApiError(409, "FAST_REPLICA_PLAN_CHANGED", "快速方案已变化。")
            receipts = dict(row.payload.get("_fast_invocations", {}))
            old = receipts.get(unit["id"], {})
            if old and old.get("plan_hash") != canonical_hash(plan):
                raise ApiError(
                    409, "FAST_REPLICA_PLAN_CHANGED", "已有图片不属于当前方案。"
                )
            if old.get("state") == "returned":
                prefix = (
                    f"{request.workspace_id}/replica-fast/{document_id}/{unit['id']}/"
                )
                if not old.get("object_key", "").startswith(prefix):
                    raise ApiError(
                        409, "FAST_REPLICA_SCOPE_INVALID", "已有图片归属无效。"
                    )
                return old
            if old and (
                old.get("state") != "rejected" or old.get("job_id") == request.job_id
            ):
                raise ApiError(
                    409,
                    "PROVIDER_OUTCOME_UNKNOWN",
                    "原生图结果尚未核实，已停止重复提交。",
                )
            receipts[unit["id"]] = {
                "state": "invoked",
                "job_id": request.job_id,
                "attempt_id": request.attempt_id,
                "plan_hash": canonical_hash(plan),
            }
            row.payload = {**row.payload, "_fast_invocations": receipts}
            row.updated_at = utc_now()
            session.commit()
            return {}

    def _finish(
        self,
        document_id: str,
        unit: dict[str, Any],
        request: ProviderRequest,
        receipt: dict[str, Any],
    ) -> None:
        with self.session_factory() as session:
            row = session.get(ReplicaDocumentRecord, document_id, with_for_update=True)
            receipts = dict(row.payload.get("_fast_invocations", {}))
            old = receipts.get(unit["id"], {})
            if (
                old.get("attempt_id") != request.attempt_id
                or old.get("state") != "invoked"
            ):
                raise ApiError(409, "FAST_REPLICA_SCOPE_INVALID", "生图回执已被接续。")
            receipts[unit["id"]] = {**old, **receipt}
            row.payload = {**row.payload, "_fast_invocations": receipts}
            row.updated_at = utc_now()
            session.commit()

    @staticmethod
    def _artifact(data: bytes, canvas: list[int]) -> ProviderArtifact:
        with Image.open(BytesIO(data)) as image:
            image.load()
            w, h = image.size
            fmt = image.format
        if (
            fmt not in {"PNG", "JPEG", "WEBP"}
            or min(w, h) < 1024
            or abs((w / h) / (canvas[0] / canvas[1]) - 1) > 0.01
        ):
            raise ApiError(
                502,
                "FAST_REPLICA_CANVAS_INVALID",
                "原始图片画幅未达要求，已保留结果，可重试该屏。",
            )
        suffix, content_type = {
            "PNG": ("png", "image/png"),
            "JPEG": ("jpg", "image/jpeg"),
            "WEBP": ("webp", "image/webp"),
        }[fmt]
        return ProviderArtifact(
            payload=data,
            filename="replica-fast." + suffix,
            content_type=content_type,
            width=w,
            height=h,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        invoked = False
        result = None
        try:
            document_id, plan, unit, records = self._load(request)
            # Read and hash source bytes before fencing a potentially paid dispatch.
            sources: list[SourceImage] = []
            for key in [unit["reference"]["asset_id"], *plan["product_asset_ids"]]:
                record = records[key]
                data = await self.storage.read_bytes(record[3])
                if hashlib.sha256(data).hexdigest() != record[2]:
                    raise ApiError(
                        409, "FAST_REPLICA_SOURCE_CHANGED", "快速复刻原图校验不一致。"
                    )
                source = SourceImage(
                    asset_id=key,
                    group_key=unit["id"],
                    filename=record[0],
                    content_type=record[1],
                    sha256=record[2],
                    payload=data,
                    source_role="product_reference"
                    if key in plan["product_asset_ids"]
                    else "style_reference",
                )
                sources.append(
                    crop_source(source, unit["bbox"]) if not sources else source
                )
            canvas = unit["canvas"]
            if all(
                abs((canvas[0] / canvas[1]) / ratio - 1) > 0.01
                for ratio in (1, 2 / 3, 3 / 2)
            ):
                output = BytesIO()
                Image.new("RGB", tuple(canvas), "white").save(output, "PNG")
                data = output.getvalue()
                sources.append(
                    SourceImage(
                        asset_id="format-canvas",
                        group_key=unit["id"],
                        filename="format-canvas.png",
                        content_type="image/png",
                        sha256=hashlib.sha256(data).hexdigest(),
                        payload=data,
                        source_role="format_canvas",
                    )
                )
            cached = self._claim(document_id, plan, unit, request)
            if cached:
                data = await self.storage.read_bytes(cached["object_key"])
                if hashlib.sha256(data).hexdigest() != cached["sha256"]:
                    raise ApiError(
                        409, "FAST_REPLICA_RESULT_CHANGED", "已生成图片校验不一致。"
                    )
                return ProviderResult(
                    outcome=ProviderOutcome.SUCCEEDED,
                    artifact=self._artifact(data, unit["canvas"]),
                    usage_reported=True,
                    actual_cost_micros=0,
                    estimated_cost_micros=0,
                    raw_usage={
                        "network_requests": 0,
                        "reused": True,
                        "review_status": "NOT_REVIEWED",
                    },
                )
            invoked = True
            result = await self.gateway.edit(
                ImageEditRequest(
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    job_item_id=request.job_item_id,
                    attempt_id=request.attempt_id,
                    page_number=unit["screen_index"] + 1,
                    group_key=unit["id"],
                    sources=tuple(sources),
                    prompt=image_prompt(plan, unit)
                    + "\nIMAGE_BINDINGS="
                    + json.dumps(
                        [
                            {"image": i + 1, "role": s.source_role}
                            for i, s in enumerate(sources)
                        ]
                    ),
                    quality="high",
                    idempotency_key=request.idempotency_key,
                    size=f"{unit['canvas'][0]}x{unit['canvas'][1]}",
                    output_format="png",
                    preserve_provider_artifact=True,
                    single_dispatch=True,
                )
            )
            usage = {
                **(result.normalized_usage or {}),
                "network_requests": 1,
                "review_status": "NOT_REVIEWED",
                "replica_speed": "fast",
                "planning_rounds": 1,
                "metrics": {
                    "api_latency_seconds": result.api_latency_seconds,
                    "skill_id": plan["skill_id"],
                    "skill_content_hash": plan["skill_content_hash"],
                },
            }
            if result.outcome != ProviderOutcome.SUCCEEDED or result.artifact is None:
                unknown = (
                    result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
                    or result.outcome == ProviderOutcome.SUCCEEDED
                    or result.error_code
                    in {"PROVIDER_OUTCOME_UNKNOWN", "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"}
                )
                self._finish(
                    document_id,
                    unit,
                    request,
                    {
                        "state": "unknown" if unknown else "rejected",
                        "provider_request_id": result.provider_request_id,
                        "usage": usage,
                        "actual_cost_micros": result.actual_cost_micros,
                    },
                )
                return ProviderResult(
                    outcome=ProviderOutcome.OUTCOME_UNKNOWN
                    if unknown
                    else ProviderOutcome.FAILED,
                    error_code="PROVIDER_OUTCOME_UNKNOWN"
                    if unknown
                    else result.error_code or "FAST_REPLICA_IMAGE_FAILED",
                    error_message="原生图结果尚未核实，请先核查原任务。"
                    if unknown
                    else "本屏生成未完成，可重试。",
                    provider_request_id=result.provider_request_id,
                    raw_usage=usage,
                    usage_reported=bool(usage.get("reported")),
                    actual_cost_micros=result.actual_cost_micros,
                    estimated_cost_micros=result.estimated_cost_micros,
                    http_status=result.http_status,
                )
            data = result.artifact.payload
            # Preserve raw bytes even when the supplier returned a wrong canvas.
            with Image.open(BytesIO(data)) as image:
                image.load()
                w, h = image.size
                fmt = image.format or ""
                content_type = Image.MIME[fmt]
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
            sha = hashlib.sha256(data).hexdigest()
            key = (
                f"{request.workspace_id}/replica-fast/{document_id}/"
                f"{unit['id']}/{sha}{suffix}"
            )
            await self.storage.save_validated(
                ValidatedImage(
                    payload=data,
                    filename="generated-image" + suffix,
                    content_type=content_type,
                    width=w,
                    height=h,
                    object_key=key,
                    sha256=sha,
                )
            )
            valid = True
            try:
                artifact = self._artifact(data, unit["canvas"])
            except ApiError:
                valid = False
            self._finish(
                document_id,
                unit,
                request,
                {
                    "state": "returned" if valid else "rejected",
                    "object_key": key,
                    "sha256": sha,
                    "provider_request_id": result.provider_request_id,
                    "usage": usage,
                    "actual_cost_micros": result.actual_cost_micros,
                },
            )
            return ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED if valid else ProviderOutcome.FAILED,
                artifact=artifact if valid else None,
                error_code=None if valid else "FAST_REPLICA_CANVAS_INVALID",
                error_message=None
                if valid
                else "本屏原始图片画幅未达要求，结果已保留，可重试。",
                provider_request_id=result.provider_request_id,
                raw_usage=usage,
                usage_reported=bool(usage.get("reported")),
                actual_cost_micros=result.actual_cost_micros,
                estimated_cost_micros=result.estimated_cost_micros,
                http_status=result.http_status,
            )
        except Exception as error:
            logger.warning(
                "Fast replica interrupted: job=%s type=%s code=%s",
                request.job_id,
                type(error).__name__,
                getattr(error, "code", None),
                exc_info=True,
            )
            unknown = invoked and (
                result is None or result.outcome != ProviderOutcome.FAILED
            )
            return ProviderResult(
                outcome=ProviderOutcome.OUTCOME_UNKNOWN
                if unknown
                else ProviderOutcome.FAILED,
                error_code="PROVIDER_OUTCOME_UNKNOWN"
                if unknown
                else getattr(error, "code", "FAST_REPLICA_EXECUTION_FAILED"),
                error_message="原生图结果尚未核实，已停止重复提交。"
                if unknown
                else "快速复刻未完成，请核对任务后重试。",
                actual_cost_micros=getattr(result, "actual_cost_micros", None)
                if invoked
                else 0,
                estimated_cost_micros=getattr(result, "estimated_cost_micros", None)
                if invoked
                else 0,
                raw_usage={
                    "network_requests": 1 if invoked else 0,
                    **(getattr(result, "normalized_usage", None) or {}),
                },
            )
