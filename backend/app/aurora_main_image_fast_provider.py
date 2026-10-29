"""Six independent main-image canvases with durable paid-call fences.

This provider validates identity bindings and files. It deliberately does not
claim to have performed a visual review or generate aesthetic repair requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import suppress
from dataclasses import replace
from io import BytesIO
from typing import Any

from PIL import Image
from sqlalchemy import select

from .aurora_main_image_planner import PAGE_ROLES
from .errors import ApiError
from .models import JobAttemptRecord, JobItemRecord, JobPlanRecord, JobRecord, utc_now
from .new_detail_skill_provider import BundledSkillManifestValidator
from .provider import ProviderArtifact, ProviderOutcome, ProviderRequest, ProviderResult
from .services import canonical_hash, get_ready_assets
from .storage import ValidatedImage
from .xinghe_agent import ImageEditRequest, SourceImage, XingheGateway

logger = logging.getLogger(__name__)

VERSION = "aurora-main-image-fast-v1"
SCHEMA_VERSION = "aurora-main-image-fast-plan-v1"
SKILL_ID = "aurora-domestic-ecommerce-main-image-fast"
NATIVE_VERSION = "aurora-main-image-native-fast-v1"
NATIVE_SCHEMA_VERSION = "aurora-main-image-native-fast-plan-v1"
NATIVE_SKILL_ID = "aurora-domestic-ecommerce-main-image-native-fast"
_VARIANTS = {
    "fast": (VERSION, SCHEMA_VERSION, SKILL_ID),
    "native_fast": (NATIVE_VERSION, NATIVE_SCHEMA_VERSION, NATIVE_SKILL_ID),
}
RECEIPTS_KEY = "_main_image_fast_invocations"
_GENERATION_KEYS = (
    "version",
    "skill_id",
    "skill_content_hash",
    "source_bindings",
    "product_asset_ids",
    "product_identity_lock",
    "confirmed_facts",
    "user_facts",
    "style_lock",
    "pages",
    "canvas",
)


def generation_hash(plan: dict[str, Any]) -> str:
    """Keep frozen generation independent from confirmation and retry metrics."""
    return canonical_hash({key: plan[key] for key in _GENERATION_KEYS})


def image_prompt(plan: dict[str, Any], page: dict[str, Any]) -> str:
    if plan.get("version") == NATIVE_VERSION:
        # The compiler's native prompt is frozen in the plan, including all
        # original spacing and copy. Do not wrap it in the first fast template.
        prompt = page.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ApiError(
                409, "FAST_MAIN_IMAGE_PLAN_CHANGED", "原生快速主图提示词不完整。"
            )
        return prompt
    return (
        "Create ONE complete, high-quality Chinese domestic e-commerce main image. "
        "Never combine multiple pages or output a contact sheet. Each attached "
        "product image is identity evidence; image 1 is the primary product. "
        "Keep its exact silhouette, proportions, packaging version, components, "
        "color and original label identity. Other photos are supplementary only; "
        "never mix different packaging. Original backgrounds are not identity. "
        "All visible marketing claims must be explicitly authorized by the original "
        "authorized_user_facts below; planned copy and model-inferred facts are "
        "never additional authority. Visual identity is not proof of efficacy, "
        "material, certification or sales. No invented prices, gifts, "
        "certifications, comparative data, "
        "before/after charts or extra product variants. When evidence is absent, "
        "show honest product detail instead. Use only the requested visible copy "
        "and preserve readable Chinese text. Follow the shared design direction "
        "while expressing this page's distinct duty and composition.\n"
        + json.dumps(
            {
                "product_identity_lock": plan["product_identity_lock"],
                "authorized_user_facts": plan["user_facts"],
                "shared_style": plan["style_lock"],
                "page": page,
                "canvas": plan["canvas"],
            },
            ensure_ascii=False,
        )
    )


class FastMainImageProvider:
    def __init__(
        self, session_factory: Any, gateway: XingheGateway, storage: Any
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.storage = storage
        self.name = getattr(gateway, "provider_name", "main-image-fast")
        self.model = getattr(gateway, "image_model", "none")
        self.pricing_version = getattr(gateway, "pricing_version", VERSION)

    def _load(
        self, request: ProviderRequest
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            item = session.get(JobItemRecord, request.job_item_id)
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            speed = (
                job.configuration.get("settings", {}).get("main_image_speed")
                if job is not None
                else None
            )
            if (
                request.operation != "domestic_main_image"
                or job is None
                or item is None
                or attempt is None
                or job.type != request.operation
                or job.workspace_id != request.workspace_id
                or item.workspace_id != request.workspace_id
                or item.job_id != job.id
                or attempt.workspace_id != request.workspace_id
                or attempt.job_id != job.id
                or attempt.job_item_id != item.id
                or attempt.status != "running"
                or attempt.provider_idempotency_key != request.idempotency_key
                or job.status != "processing"
                or speed not in _VARIANTS
            ):
                raise ApiError(
                    409,
                    "FAST_MAIN_IMAGE_SCOPE_INVALID",
                    "快速主图任务归属或执行状态无效。",
                )
            version, schema_version, skill_id = _VARIANTS[speed]
            row = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job.id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if (
                row is None
                or row.workspace_id != request.workspace_id
                or row.schema_version != schema_version
                or row.validation_status != "valid"
                or row.confirmed_at is None
                or row.payload_hash != canonical_hash(row.payload)
            ):
                raise ApiError(
                    409, "FAST_MAIN_IMAGE_PLAN_CHANGED", "快速主图方案未确认或已变化。"
                )
            plan = dict(row.payload)
            if (
                plan.get("version") != version
                or plan.get("skill_id") != skill_id
                or len(plan.get("pages", [])) != 6
                or [p.get("page_index") for p in plan["pages"]] != list(range(6))
                or [p.get("page_role") for p in plan["pages"]] != list(PAGE_ROLES)
                or not 0 <= item.ordinal < 6
                or plan.get("canvas") != [1024, 1024]
                or plan.get("product_asset_ids")
                != job.configuration.get("input", {}).get("product_asset_ids")
            ):
                raise ApiError(
                    409,
                    "FAST_MAIN_IMAGE_PLAN_CHANGED",
                    "快速主图六图职责或素材绑定已变化。",
                )
            if speed == "native_fast":
                if not (
                    isinstance(plan.get("product_identity_lock"), dict)
                    and plan["product_identity_lock"]
                    and isinstance(plan.get("style_lock"), dict)
                    and plan["style_lock"]
                ):
                    raise ApiError(
                        409, "FAST_MAIN_IMAGE_PLAN_CHANGED", "原生身份或风格锁不完整。"
                    )
                for page in plan["pages"]:
                    source_ids = page.get("source_asset_ids")
                    if (
                        not isinstance(page.get("prompt"), str)
                        or not page["prompt"].strip()
                        or not isinstance(source_ids, list)
                        or not source_ids
                        or any(not isinstance(key, str) for key in source_ids)
                        or len(set(source_ids)) != len(source_ids)
                        or not set(source_ids).issubset(plan["product_asset_ids"])
                    ):
                        raise ApiError(
                            409,
                            "FAST_MAIN_IMAGE_SOURCE_CHANGED",
                            "原生快速主图提示词或逐页来源绑定无效。",
                        )
            if (
                BundledSkillManifestValidator().validate(skill_id)
                != plan["skill_content_hash"]
            ):
                raise ApiError(
                    409,
                    "FAST_MAIN_IMAGE_SKILL_CHANGED",
                    "快速主图 Skill 已变化，请重新规划。",
                )
            assets = get_ready_assets(
                session, request.workspace_id, plan["product_asset_ids"]
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
            bindings = [
                {
                    "asset_id": key,
                    "sha256": records[key][2],
                    "width": records[key][4],
                    "height": records[key][5],
                }
                for key in plan["product_asset_ids"]
            ]
            if bindings != plan["source_bindings"]:
                raise ApiError(
                    409, "FAST_MAIN_IMAGE_SOURCE_CHANGED", "快速主图原始素材已变化。"
                )
            return plan, plan["pages"][item.ordinal], records

    @staticmethod
    def _ancestor_ids(session: Any, job: JobRecord) -> set[str]:
        ancestors = {job.id}
        current = job
        while current.retry_of_job_id:
            if current.retry_of_job_id in ancestors:
                raise ApiError(409, "FAST_MAIN_IMAGE_SCOPE_INVALID", "重试任务链无效。")
            parent = session.get(JobRecord, current.retry_of_job_id)
            if (
                parent is None
                or parent.workspace_id != job.workspace_id
                or parent.type != job.type
            ):
                raise ApiError(
                    409, "FAST_MAIN_IMAGE_SCOPE_INVALID", "重试任务归属无效。"
                )
            ancestors.add(parent.id)
            current = parent
        return ancestors

    def _claim(
        self,
        plan: dict[str, Any],
        page: dict[str, Any],
        request: ProviderRequest,
        *,
        skip_rejected_cache: bool = False,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if (
                job is None
                or job.status != "processing"
                or attempt is None
                or attempt.status != "running"
            ):
                raise ApiError(409, "FAST_MAIN_IMAGE_SCOPE_INVALID", "任务已中止。")
            row = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job.id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if (
                row is None
                or canonical_hash(row.payload) != row.payload_hash
                or generation_hash(row.payload) != generation_hash(plan)
            ):
                raise ApiError(
                    409, "FAST_MAIN_IMAGE_PLAN_CHANGED", "快速主图方案已变化。"
                )
            receipts = dict(job.configuration.get(RECEIPTS_KEY, {}))
            key = str(page["page_index"])
            old = receipts.get(key, {})
            if old and (
                old.get("plan_hash") != generation_hash(plan)
                or old.get("job_id") not in self._ancestor_ids(session, job)
            ):
                raise ApiError(
                    409, "FAST_MAIN_IMAGE_PLAN_CHANGED", "已有图片不属于当前冻结方案。"
                )
            if old.get("state") == "returned" or (
                old.get("state") == "rejected"
                and old.get("object_key")
                and not skip_rejected_cache
            ):
                prefix = (
                    f"{request.workspace_id}/main-image-fast/{old['job_id']}/{key}/"
                )
                if not old.get("object_key", "").startswith(prefix):
                    raise ApiError(
                        409, "FAST_MAIN_IMAGE_SCOPE_INVALID", "已有图片归属无效。"
                    )
                return old
            if old and (old.get("state") != "rejected" or old.get("job_id") == job.id):
                raise ApiError(
                    409,
                    "PROVIDER_OUTCOME_UNKNOWN",
                    "原生图结果尚未核实，已停止重复提交。",
                )
            receipts[key] = {
                "state": "invoked",
                "job_id": job.id,
                "attempt_id": request.attempt_id,
                "plan_hash": generation_hash(plan),
                "page_index": page["page_index"],
                "prompt_hash": hashlib.sha256(
                    image_prompt(plan, page).encode()
                ).hexdigest(),
                "started_at": utc_now().isoformat(),
            }
            job.configuration = {**job.configuration, RECEIPTS_KEY: receipts}
            session.commit()
            return {}

    def _finish(
        self, page: dict[str, Any], request: ProviderRequest, receipt: dict[str, Any]
    ) -> None:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            receipts = dict(job.configuration.get(RECEIPTS_KEY, {}))
            key = str(page["page_index"])
            old = receipts.get(key, {})
            if (
                old.get("attempt_id") != request.attempt_id
                or old.get("state") != "invoked"
            ):
                raise ApiError(
                    409, "FAST_MAIN_IMAGE_SCOPE_INVALID", "生图回执已被接续。"
                )
            receipts[key] = {**old, **receipt, "finished_at": utc_now().isoformat()}
            job.configuration = {**job.configuration, RECEIPTS_KEY: receipts}
            session.commit()

    @staticmethod
    def _artifact(
        data: bytes, canvas: list[int], *, strict_square: bool = False
    ) -> ProviderArtifact:
        with Image.open(BytesIO(data)) as image:
            image.load()
            w, h, fmt = *image.size, image.format
        # Main images are square; keep the native 1024 minimum rather than
        # applying the replica channel's alternative elongated-image threshold.
        if (
            fmt not in {"PNG", "JPEG", "WEBP"}
            or min(w, h) < 1024
            or (strict_square and w != h)
            or abs((w / h) / (canvas[0] / canvas[1]) - 1) > 0.01
        ):
            raise ApiError(
                502,
                "FAST_MAIN_IMAGE_CANVAS_INVALID",
                "主图原始画幅未达要求，已保留结果。",
            )
        suffix, content_type = {
            "PNG": ("png", "image/png"),
            "JPEG": ("jpg", "image/jpeg"),
            "WEBP": ("webp", "image/webp"),
        }[fmt]
        return ProviderArtifact(
            payload=data,
            filename="main-image-fast." + suffix,
            content_type=content_type,
            width=w,
            height=h,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        return replace(
            await self._execute(request),
            provider_name=self.name,
            model_name=self.model,
            pricing_version=self.pricing_version,
        )

    async def _execute(self, request: ProviderRequest) -> ProviderResult:
        invoked = False
        result = None
        artifact: ProviderArtifact | None = None
        try:
            plan, page, records = self._load(request)
            sources = []
            native = plan["version"] == NATIVE_VERSION
            source_ids = (
                page["source_asset_ids"] if native else plan["product_asset_ids"]
            )
            for key in source_ids:
                record = records[key]
                data = await self.storage.read_bytes(record[3])
                if hashlib.sha256(data).hexdigest() != record[2]:
                    raise ApiError(
                        409, "FAST_MAIN_IMAGE_SOURCE_CHANGED", "商品原图校验不一致。"
                    )
                sources.append(
                    SourceImage(
                        asset_id=key,
                        group_key=f"main-image-{page['page_index']}",
                        filename=record[0],
                        content_type=record[1],
                        sha256=record[2],
                        payload=data,
                        source_role="product_reference",
                    )
                )
            cached = self._claim(plan, page, request)
            while cached:
                data = await self.storage.read_bytes(cached["object_key"])
                if hashlib.sha256(data).hexdigest() != cached["sha256"]:
                    raise ApiError(
                        409, "FAST_MAIN_IMAGE_RESULT_CHANGED", "已生成图片校验不一致。"
                    )
                try:
                    artifact = self._artifact(
                        data, plan["canvas"], strict_square=native
                    )
                except ApiError:
                    if cached.get("state") != "rejected":
                        raise
                    cached = self._claim(plan, page, request, skip_rejected_cache=True)
                else:
                    return ProviderResult(
                        outcome=ProviderOutcome.SUCCEEDED,
                        artifact=artifact,
                        usage_reported=True,
                        actual_cost_micros=0,
                        estimated_cost_micros=0,
                        raw_usage={
                            "network_requests": 0,
                            "reused": True,
                            "review_status": "NOT_REVIEWED",
                            "technical_status": "STRUCTURALLY_VALIDATED",
                        },
                    )
            invoked = True
            result = await self.gateway.edit(
                ImageEditRequest(
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    job_item_id=request.job_item_id,
                    attempt_id=request.attempt_id,
                    page_number=page["page_index"] + 1,
                    group_key=f"main-image-{page['page_index']}",
                    sources=tuple(sources),
                    prompt=image_prompt(plan, page),
                    quality="high",
                    idempotency_key=request.idempotency_key,
                    size=f"{plan['canvas'][0]}x{plan['canvas'][1]}",
                    output_format="png",
                    preserve_provider_artifact=True,
                    single_dispatch=True,
                )
            )
            usage = {
                **(result.normalized_usage or {}),
                "network_requests": 1,
                "review_status": "NOT_REVIEWED",
                "main_image_speed": "native_fast" if native else "fast",
                "planning_rounds": 1,
                "metrics": {
                    "api_latency_seconds": result.api_latency_seconds,
                    "skill_id": plan["skill_id"],
                    "skill_content_hash": plan["skill_content_hash"],
                    "page_index": page["page_index"],
                },
            }
            if result.outcome != ProviderOutcome.SUCCEEDED or result.artifact is None:
                unknown = result.outcome in {
                    ProviderOutcome.OUTCOME_UNKNOWN,
                    ProviderOutcome.SUCCEEDED,
                } or result.error_code in {
                    "PROVIDER_OUTCOME_UNKNOWN",
                    "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED",
                }
                self._finish(
                    page,
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
                    else result.error_code or "FAST_MAIN_IMAGE_IMAGE_FAILED",
                    error_message="原生图结果尚未核实，请先核对原任务。"
                    if unknown
                    else "本张主图生成未完成，可重试。",
                    provider_request_id=result.provider_request_id,
                    raw_usage=usage,
                    usage_reported=bool(usage.get("reported")),
                    actual_cost_micros=result.actual_cost_micros,
                    estimated_cost_micros=result.estimated_cost_micros,
                    http_status=result.http_status,
                )
            data = result.artifact.payload
            with Image.open(BytesIO(data)) as image:
                image.load()
                w, h = image.size
                fmt = image.format or ""
                content_type = Image.MIME[fmt]
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
            sha = hashlib.sha256(data).hexdigest()
            key = (
                f"{request.workspace_id}/main-image-fast/{request.job_id}/"
                f"{page['page_index']}/{sha}{suffix}"
            )
            await self.storage.save_validated(
                ValidatedImage(
                    payload=data,
                    filename="generated-main-image" + suffix,
                    content_type=content_type,
                    width=w,
                    height=h,
                    object_key=key,
                    sha256=sha,
                )
            )
            artifact = None
            with suppress(ApiError):
                artifact = self._artifact(data, plan["canvas"], strict_square=native)
            self._finish(
                page,
                request,
                {
                    "state": "returned" if artifact else "rejected",
                    "object_key": key,
                    "sha256": sha,
                    "provider_request_id": result.provider_request_id,
                    "usage": usage,
                    "actual_cost_micros": result.actual_cost_micros,
                },
            )
            return ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED
                if artifact
                else ProviderOutcome.FAILED,
                artifact=artifact,
                error_code=None if artifact else "FAST_MAIN_IMAGE_CANVAS_INVALID",
                error_message=None
                if artifact
                else "本张主图原始画幅未达要求，结果已保留，可重试。",
                provider_request_id=result.provider_request_id,
                raw_usage={
                    **usage,
                    "technical_status": "STRUCTURALLY_VALIDATED"
                    if artifact
                    else "INVALID",
                },
                usage_reported=bool(usage.get("reported")),
                actual_cost_micros=result.actual_cost_micros,
                estimated_cost_micros=result.estimated_cost_micros,
                http_status=result.http_status,
            )
        except Exception as error:
            logger.warning(
                "Fast main image interrupted: job=%s type=%s code=%s",
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
                else getattr(error, "code", "FAST_MAIN_IMAGE_EXECUTION_FAILED"),
                error_message="原生图结果尚未核实，已停止重复提交。"
                if unknown
                else "快速主图未完成，请核对任务后重试。",
                actual_cost_micros=getattr(result, "actual_cost_micros", None)
                if invoked
                else 0,
                estimated_cost_micros=getattr(result, "estimated_cost_micros", None)
                if invoked
                else 0,
                raw_usage={
                    **(getattr(result, "normalized_usage", None) or {}),
                    "network_requests": 1 if invoked else 0,
                    "review_status": "NOT_REVIEWED",
                },
            )
