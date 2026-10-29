# ruff: noqa: E501
"""Amazon fast suite: frozen canvases, one dispatch, durable original reuse."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from io import BytesIO
from typing import Any

from PIL import Image
from sqlalchemy import select

from .amazon_suite_fast import POLICY_VERSION, SKILL_ID, native_canvas_valid, selected
from .amazon_visual_suite_provider import (
    AMAZON_ITEM_KEYS,
    AMAZON_PLAN_CONFIGURATION_KEY,
    AmazonVisualSuiteProvider,
    editable_amazon_structured_plan,
)
from .errors import ApiError
from .models import JobAttemptRecord, JobItemRecord, JobPlanRecord, JobRecord
from .new_detail_skill_provider import BundledSkillManifestValidator
from .provider import ProviderArtifact, ProviderOutcome, ProviderRequest, ProviderResult
from .services import canonical_hash, get_ready_assets
from .storage import ValidatedImage
from .xinghe_agent import ImageEditRequest, SourceImage

logger = logging.getLogger(__name__)


class AmazonFastPlannerRouter:
    def __init__(self, sessions: Any, standard: Any, fast: Any) -> None:
        self.sessions, self.standard, self.fast = sessions, standard, fast

    async def plan(self, job_id: str) -> Any:
        with self.sessions() as session:
            job = session.get(JobRecord, job_id)
            fast = job is not None and selected(job.configuration)
        return await (self.fast if fast else self.standard).plan(job_id)


class AmazonFastProviderRouter:
    def __init__(self, standard: Any, fast: Any) -> None:
        self.standard, self.fast = standard, fast
        self.name, self.model, self.pricing_version = (
            standard.name,
            standard.model,
            standard.pricing_version,
        )

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        configuration = request.configuration.get("job", {}).get("configuration", {})
        # ProviderRequest job is the durable configuration envelope.
        if not configuration:
            configuration = request.configuration.get("job", {})
        return await (self.fast if selected(configuration) else self.standard).execute(
            request
        )


class AmazonFastProvider:
    def __init__(self, sessions: Any, gateway: Any, storage: Any) -> None:
        self.sessions, self.gateway, self.storage = sessions, gateway, storage
        self.name = gateway.provider_name
        self.model = gateway.image_model
        self.pricing_version = gateway.pricing_version

    def _load(
        self, request: ProviderRequest
    ) -> tuple[Any, dict[str, Any], list[Any], str, str]:
        with self.sessions() as session:
            job = session.get(JobRecord, request.job_id)
            item = session.get(JobItemRecord, request.job_item_id)
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == request.job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if (
                job is None
                or item is None
                or attempt is None
                or plan is None
                or job.type != "amazon_visual_suite"
                or not selected(job.configuration)
                or job.workspace_id != request.workspace_id
                or item.workspace_id != request.workspace_id
                or item.job_id != job.id
                or attempt.job_item_id != item.id
                or attempt.job_id != job.id
                or attempt.workspace_id != request.workspace_id
                or attempt.status != "running"
                or attempt.provider_idempotency_key != request.idempotency_key
                or job.status != "processing"
                or plan.confirmed_at is None
                or plan.validation_status != "valid"
                or request.operation != job.type
            ):
                raise ApiError(
                    409, "AMAZON_FAST_SCOPE_INVALID", "快速套图任务归属或状态无效。"
                )
            content_hash = BundledSkillManifestValidator().validate(SKILL_ID)
            if (
                plan.payload.get("skill_id") != SKILL_ID
                or plan.payload.get("skill_content_hash") != content_hash
            ):
                raise ApiError(
                    409, "AMAZON_FAST_SKILL_CHANGED", "快速 Skill 已变化，请重新规划。"
                )
            context = item.configuration.get(AMAZON_PLAN_CONFIGURATION_KEY, {})
            structured = editable_amazon_structured_plan(plan.payload)
            signature = canonical_hash(
                {
                    "version": POLICY_VERSION,
                    "input_signature": plan.payload.get("input_signature"),
                    "structured_plan": structured,
                }
            )
            if (
                signature != plan.payload.get("resume_signature")
                or signature != context.get("resume_signature")
                or context.get("screen") != structured["plan"]["screens"][item.ordinal]
                or item.item_key != AMAZON_ITEM_KEYS[item.ordinal]
            ):
                raise ApiError(
                    409, "AMAZON_FAST_PLAN_CHANGED", "冻结方案已变化，请重新规划。"
                )
            expected_context = {
                "analysis": structured["analysis"],
                **{
                    k: structured["plan"][k]
                    for k in (
                        "campaign_style_lock",
                        "product_consistency_lock",
                        "layout_diversity_lock",
                    )
                },
            }
            if any(context.get(k) != v for k, v in expected_context.items()):
                raise ApiError(
                    409, "AMAZON_FAST_PLAN_CHANGED", "共享身份事实或风格锁已变化。"
                )
            # Reuse canonical source-role and complete task validation, never its resampling delegate.
            durable_request = replace(
                request,
                configuration={"job": job.configuration, "item": item.configuration},
            )
            adapter = AmazonVisualSuiteProvider(self)
            prepared, spec = adapter.prepare_request(
                durable_request, content_hash=content_hash
            )
            source_rows = prepared.configuration["new_skill_adapter"]["source_assets"]
            assets = get_ready_assets(
                session, request.workspace_id, [row["asset_id"] for row in source_rows]
            )
            records = [
                (
                    row["asset_id"],
                    row["role"],
                    assets[row["asset_id"]].original_filename,
                    assets[row["asset_id"]].content_type,
                    assets[row["asset_id"]].sha256,
                    assets[row["asset_id"]].object_key,
                )
                for row in source_rows
            ]
            primary = context["analysis"].get("primary_product_source_id")
            if not any(
                row[0] == primary and row[1] == "white_background_product"
                for row in records
            ):
                raise ApiError(
                    409,
                    "AMAZON_FAST_PRIMARY_SOURCE_INVALID",
                    "主商品来源未绑定真实商品。",
                )
            records.sort(key=lambda row: row[0] != primary)
            prompt = (
                "Create exactly one finished Amazon image on its own canvas. No contact sheet. "
                "Preserve the primary product's exact identity, label, parts and packaging version; "
                "other product photos only clarify the SAME SKU. Never blend packaging versions. "
                "Only authorized visible or supplied facts may appear in copy. No invented certification, "
                "numbers, reviews, price, gifts, comparison data or before/after evidence. "
                "Desktop and mobile are independently composed; never crop or resize a sibling image. "
                "For MAIN-01: pure white, real product only, authentic label stays, no added text or props.\n"
                f"CANVAS={spec.width}x{spec.height}; QUALITY=high; PRIMARY_SOURCE={primary}\n"
                + "SHARED_STRATEGY="
                + json.dumps(
                    {
                        k: context[k]
                        for k in (
                            "analysis",
                            "campaign_style_lock",
                            "product_consistency_lock",
                            "layout_diversity_lock",
                        )
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\nUSER_AUTHORIZED_FACTS_AND_HARD_CONSTRAINTS="
                + json.dumps(
                    {
                        k: job.configuration.get("settings", {}).get(k)
                        for k in (
                            "product_facts",
                            "parameters",
                            "certifications_or_evidence",
                            "forbidden_claims",
                            "instruction",
                        )
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\nITEM_DESIGN="
                + json.dumps(
                    context["screen"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\nIMAGE_BINDINGS="
                + json.dumps(
                    [
                        {"image": i + 1, "asset_id": row[0], "role": row[1]}
                        for i, row in enumerate(records)
                    ]
                )
            )
            root = item.configuration.get("amazon_fast_root_item_id", item.id)
            original = session.get(JobItemRecord, root)
            if (
                original is None
                or original.workspace_id != request.workspace_id
                or original.item_key != item.item_key
            ):
                raise ApiError(409, "AMAZON_FAST_SCOPE_INVALID", "原始调用归属无效。")
            receipt = original.configuration.get("amazon_fast_receipt", {})
            binding_scope = {
                "plan": signature,
                "skill": content_hash,
                "sources": [(r[0], r[1], r[4]) for r in records],
            }

            def prompt_binding(value: str) -> str:
                return canonical_hash({**binding_scope, "prompt": value})

            if receipt:
                frozen_prompt = receipt.get("prompt")
                if frozen_prompt is None:
                    # Bootstrap receipts from the first local v1 run were already
                    # bound before source constraints were directly appended.
                    # Accept only the exact reconstructed paid prompt hash.
                    prefix, remainder = prompt.split(
                        "\nUSER_AUTHORIZED_FACTS_AND_HARD_CONSTRAINTS=", 1
                    )
                    legacy_prompt = (
                        prefix
                        + "\nITEM_DESIGN="
                        + remainder.split("\nITEM_DESIGN=", 1)[1]
                    )
                    frozen_prompt = (
                        prompt
                        if prompt_binding(prompt) == receipt.get("binding")
                        else legacy_prompt
                    )
                if not isinstance(frozen_prompt, str) or prompt_binding(
                    frozen_prompt
                ) != receipt.get("binding"):
                    raise ApiError(
                        409, "AMAZON_FAST_PLAN_CHANGED", "已提交提示词或来源发生变化。"
                    )
                prompt = frozen_prompt
            binding = prompt_binding(prompt)
            return (
                spec,
                {"prompt": prompt, "binding": binding},
                records,
                root,
                item.item_key,
            )

    def _receipt(
        self,
        root: str,
        request: ProviderRequest,
        binding: str,
        *,
        finish: dict[str, Any] | None = None,
        skip_rejected_cache: bool = False,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(JobItemRecord, request.job_item_id, with_for_update=True)
            original = session.get(JobItemRecord, root, with_for_update=True)
            if (
                item is None
                or original is None
                or original.workspace_id != request.workspace_id
                or original.item_key != item.item_key
            ):
                raise ApiError(409, "AMAZON_FAST_SCOPE_INVALID", "原图归属不一致。")
            receipt = original.configuration.get("amazon_fast_receipt", {})
            if receipt and receipt.get("binding") != binding:
                raise ApiError(
                    409, "AMAZON_FAST_PLAN_CHANGED", "已有回执不属于冻结方案。"
                )
            if finish is not None:
                if (
                    receipt.get("attempt_id") != request.attempt_id
                    or receipt.get("state") != "invoked"
                ):
                    raise ApiError(
                        409, "AMAZON_FAST_RECEIPT_CHANGED", "调用回执已被接续。"
                    )
                receipt = {**receipt, **finish}
            elif receipt:
                if receipt.get("object_key") and (
                    receipt.get("state") == "returned"
                    or (receipt.get("state") == "rejected" and not skip_rejected_cache)
                ):
                    return dict(receipt)
                if (
                    receipt.get("state") != "rejected"
                    or receipt.get("job_id") == request.job_id
                ):
                    raise ApiError(
                        409,
                        "PROVIDER_OUTCOME_UNKNOWN",
                        "调用结果未核实，停止重复提交。",
                    )
                receipt = {}
            if not receipt:
                receipt = {
                    "state": "invoked",
                    "binding": binding,
                    "prompt": prompt,
                    "attempt_id": request.attempt_id,
                    "job_id": request.job_id,
                }
            original.configuration = {
                **original.configuration,
                "amazon_fast_receipt": receipt,
            }
            item.configuration = {
                **item.configuration,
                "amazon_fast_root_item_id": root,
            }
            # When original == item, keep both fields in one assignment.
            if original.id == item.id:
                item.configuration = {
                    **item.configuration,
                    "amazon_fast_receipt": receipt,
                }
            session.commit()
            return {} if finish is None else receipt

    @staticmethod
    def artifact(data: bytes, spec: Any) -> ProviderArtifact:
        with Image.open(BytesIO(data)) as image:
            image.load()
            w, h = image.size
            fmt = image.format
        # Keep native pixels and composition; no local crop, resize or upscale.
        if fmt not in {"PNG", "JPEG", "WEBP"} or not native_canvas_valid(w, h, spec):
            raise ApiError(
                502,
                "AMAZON_FAST_CANVAS_INVALID",
                "原始画幅未满足套图尺寸，已保留原图。",
            )
        suffix, content = {
            "PNG": ("png", "image/png"),
            "JPEG": ("jpg", "image/jpeg"),
            "WEBP": ("webp", "image/webp"),
        }[fmt]
        return ProviderArtifact(
            data,
            spec.item_key + "." + suffix,
            content,
            w,
            h,
            hashlib.sha256(data).hexdigest(),
        )

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        invoked, result = False, None
        artifact: ProviderArtifact | None = None
        try:
            spec, frozen, records, root, key = self._load(request)
            sources = []
            for asset_id, role, filename, content, sha, object_key in records:
                data = await self.storage.read_bytes(object_key)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ApiError(
                        409, "AMAZON_FAST_SOURCE_CHANGED", "素材哈希校验失败。"
                    )
                sources.append(
                    SourceImage(
                        asset_id, key, filename, content, sha, data, source_role=role
                    )
                )
            # An explicit empty canvas guides nonstandard aspect ratios without altering output pixels.
            buffer = BytesIO()
            Image.new("RGB", (spec.width, spec.height), "white").save(buffer, "PNG")
            data = buffer.getvalue()
            sources.append(
                SourceImage(
                    "format-canvas",
                    key,
                    "format-canvas.png",
                    "image/png",
                    hashlib.sha256(data).hexdigest(),
                    data,
                    source_role="format_canvas",
                )
            )
            cached = self._receipt(
                root, request, frozen["binding"], prompt=frozen["prompt"]
            )
            while cached:
                data = await self.storage.read_bytes(cached["object_key"])
                if (
                    not cached["object_key"].startswith(
                        f"{request.workspace_id}/amazon-fast/{root}/"
                    )
                    or hashlib.sha256(data).hexdigest() != cached["sha256"]
                ):
                    raise ApiError(
                        409, "AMAZON_FAST_RESULT_CHANGED", "已生成原图归属或哈希无效。"
                    )
                try:
                    artifact = self.artifact(data, spec)
                except ApiError:
                    if cached.get("state") != "rejected":
                        raise
                    cached = self._receipt(
                        root,
                        request,
                        frozen["binding"],
                        skip_rejected_cache=True,
                        prompt=frozen["prompt"],
                    )
                    continue
                output = ProviderResult(
                    ProviderOutcome.SUCCEEDED,
                    artifact=artifact,
                    usage_reported=True,
                    raw_usage={
                        "network_requests": 0,
                        "reused": True,
                        "review_status": "NOT_REVIEWED",
                    },
                )
                break
            if not cached:
                invoked = True
                result = await self.gateway.edit(
                    ImageEditRequest(
                        workspace_id=request.workspace_id,
                        job_id=request.job_id,
                        job_item_id=request.job_item_id,
                        attempt_id=request.attempt_id,
                        page_number=AMAZON_ITEM_KEYS.index(key) + 1,
                        group_key=key,
                        sources=tuple(sources),
                        prompt=frozen["prompt"],
                        quality="high",
                        idempotency_key=request.idempotency_key,
                        size=f"{spec.width}x{spec.height}",
                        output_format="jpeg",
                        preserve_provider_artifact=True,
                        single_dispatch=True,
                    )
                )
                usage = {
                    **(result.raw_usage or {}),
                    **(result.normalized_usage or {}),
                    "network_requests": 1,
                    "review_status": "NOT_REVIEWED",
                    "amazon_speed": "fast",
                    "metrics": {
                        "api_latency_seconds": result.api_latency_seconds,
                        "skill_id": SKILL_ID,
                    },
                }
                if (
                    result.outcome != ProviderOutcome.SUCCEEDED
                    or result.artifact is None
                ):
                    unknown = (
                        result.outcome != ProviderOutcome.FAILED
                        or result.error_code
                        in {
                            "PROVIDER_OUTCOME_UNKNOWN",
                            "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED",
                        }
                    )
                    self._receipt(
                        root,
                        request,
                        frozen["binding"],
                        finish={
                            "state": "unknown" if unknown else "rejected",
                            "usage": usage,
                            "provider_request_id": result.provider_request_id,
                        },
                    )
                    output = ProviderResult(
                        ProviderOutcome.OUTCOME_UNKNOWN
                        if unknown
                        else ProviderOutcome.FAILED,
                        error_code="PROVIDER_OUTCOME_UNKNOWN"
                        if unknown
                        else result.error_code or "AMAZON_FAST_IMAGE_FAILED",
                        error_message="原生图结果未核实。"
                        if unknown
                        else "该项生成失败，可明确重试。",
                        raw_usage=usage,
                        provider_request_id=result.provider_request_id,
                        actual_cost_micros=result.actual_cost_micros,
                        estimated_cost_micros=result.estimated_cost_micros,
                        usage_reported=bool(usage.get("reported")),
                    )
                else:
                    data = result.artifact.payload
                    sha = hashlib.sha256(data).hexdigest()
                    with Image.open(BytesIO(data)) as image:
                        image.load()
                        w, h = image.size
                        fmt = image.format
                        content = Image.MIME[fmt or ""]
                    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt or ""]
                    object_key = (
                        f"{request.workspace_id}/amazon-fast/{root}/{sha}{suffix}"
                    )
                    await self.storage.save_validated(
                        ValidatedImage(
                            payload=data,
                            filename=key + suffix,
                            content_type=content,
                            width=w,
                            height=h,
                            object_key=object_key,
                            sha256=sha,
                        )
                    )
                    error = None
                    try:
                        artifact = self.artifact(data, spec)
                    except ApiError as exc:
                        error, artifact = exc, None
                    self._receipt(
                        root,
                        request,
                        frozen["binding"],
                        finish={
                            "state": "rejected" if error else "returned",
                            "object_key": object_key,
                            "sha256": sha,
                            "usage": usage,
                            "provider_request_id": result.provider_request_id,
                        },
                    )
                    output = ProviderResult(
                        ProviderOutcome.FAILED if error else ProviderOutcome.SUCCEEDED,
                        artifact=artifact,
                        error_code=error.code if error else None,
                        error_message=str(error) if error else None,
                        raw_usage=usage,
                        provider_request_id=result.provider_request_id,
                        usage_reported=bool(usage.get("reported")),
                        actual_cost_micros=result.actual_cost_micros,
                        estimated_cost_micros=result.estimated_cost_micros,
                    )
        except Exception as exc:
            logger.warning(
                "Fast Amazon interrupted job=%s type=%s code=%s",
                request.job_id,
                type(exc).__name__,
                getattr(exc, "code", None),
                exc_info=True,
            )
            unknown = getattr(exc, "code", None) == "PROVIDER_OUTCOME_UNKNOWN" or (
                invoked and (result is None or result.outcome != ProviderOutcome.FAILED)
            )
            if invoked and result is not None:
                try:
                    self._receipt(
                        root,
                        request,
                        frozen["binding"],
                        finish={
                            "state": "unknown",
                            "provider_request_id": result.provider_request_id,
                            "usage": {
                                **(result.raw_usage or {}),
                                **(result.normalized_usage or {}),
                                "network_requests": 1,
                            },
                            "actual_cost_micros": result.actual_cost_micros,
                        },
                    )
                except Exception:
                    # A terminal receipt already persisted must never be overwritten.
                    logger.warning(
                        "Could not append Amazon fast failure receipt job=%s",
                        request.job_id,
                    )
            output = ProviderResult(
                ProviderOutcome.OUTCOME_UNKNOWN if unknown else ProviderOutcome.FAILED,
                error_code="PROVIDER_OUTCOME_UNKNOWN"
                if unknown
                else getattr(exc, "code", "AMAZON_FAST_EXECUTION_FAILED"),
                provider_request_id=getattr(result, "provider_request_id", None),
                error_message="快速套图调用中断，已停止重复提交。"
                if unknown
                else str(exc),
                actual_cost_micros=getattr(result, "actual_cost_micros", None)
                if invoked
                else 0,
                estimated_cost_micros=getattr(result, "estimated_cost_micros", None)
                if invoked
                else 0,
                raw_usage={"network_requests": int(invoked)},
            )
        return replace(
            output,
            provider_name=self.name,
            model_name=self.model,
            pricing_version=self.pricing_version,
        )
