"""Source-grounded selection and fail-closed acceptance for real image edits.

This layer never retries generation. Unknown outcomes retain the original
reconciliation path. A rejected returned candidate is retained as a private,
non-deliverable asset, with image and review costs both accounted for.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import weakref
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from PIL import Image
from sqlalchemy import select

from .models import (
    AssetRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
)
from .provider import ProviderOutcome
from .services import canonical_hash, new_id
from .storage import ValidatedImage

VERSION = "image-fidelity-v1"
PREPARE = "image_fidelity_prepare"
REVIEW = "image_fidelity_review"
CHECKS = (
    "subject_selection",
    "physical_artwork_text",
    "protected_attributes",
    "requested_change",
    "reference_scope",
    "canvas",
)
logger = logging.getLogger(__name__)
AUTHORITY_ROLES = frozenset(
    {
        "product_reference",
        "product_original",
        "model_source",
        "white_background_product",
        "product_detail",
    }
)

POLICY = (
    "PRODUCT PRESERVATION — MANDATORY. The user's explicit selected subset, count "
    "and requested changes override generic 'keep all products' recipes. Every "
    "selected object must come from product-authority sources, never from a "
    "background/style/pose reference. Physical printing is part of the product: "
    "preserve the actual characters, language, spelling, logos, illustrations, "
    "linework and placement. Never replace tiny Japanese or other lettering with "
    "a familiar brand/character name or pseudo-glyphs. A ban on copying reference "
    "marketing text DOES NOT authorize erasing or rewriting product printing. "
    "For change_model, change the person only: retain Image 1's sleeve length, "
    "neckline, garment cut, buttons, fabric and colors; never import Image 2's "
    "clothes. For pose transfer, keep the original person, outfit, background "
    "and full canvas; the pose image supplies pose only. Return a full-bleed "
    "single image without contact-sheet panels, borders or transparent padding."
)


def operation_policy(context: dict[str, Any]) -> str:
    if context["settings"].get("task_type") == "change_model":
        return POLICY + (
            "\nTHIS REQUEST IS CHANGE_MODEL: actually replace the human face, "
            "hair and adult identity with a visibly different adult model. "
            "The original person's identity is NOT a protected product attribute. "
            "Here 'product identity' means the original GARMENTS ONLY. "
            "A near-identical face or unchanged person is a failed edit. "
            "Keep the original short sleeves, neckline, trousers, shoes, "
            "requested standing pose and room. The reference's sleeveless top "
            "and shorts are forbidden clothing substitutes."
        )
    return POLICY


def technical_contract(
    context: dict[str, Any], request: Any, artifact: Any
) -> dict[str, Any]:
    with Image.open(BytesIO(artifact.payload)) as image:
        image.load()
        width, height = image.size
        alpha_min = (
            image.getchannel("A").getextrema()[0] if "A" in image.getbands() else 255
        )
    valid = (width, height) == (artifact.width, artifact.height)
    valid = valid and hashlib.sha256(artifact.payload).hexdigest() == artifact.sha256
    target_width, target_height = (int(value) for value in request.size.split("x"))
    ratio_valid = abs((width / height) / (target_width / target_height) - 1) <= 0.01
    policy = (
        "Native original must match requested aspect within 1%; no artificial padding."
    )
    valid = valid and ratio_valid
    if context["job_type"] == "amazon_visual_suite":
        from .amazon_suite_fast import native_canvas_valid
        from .amazon_visual_suite_provider import AMAZON_ITEM_SPECS

        spec = next(
            spec for spec in AMAZON_ITEM_SPECS if spec.item_key == context["item_key"]
        )
        valid = valid and native_canvas_valid(width, height, spec)
        policy = (
            "EXISTING Amazon native-original validator: aspect within 1%; "
            "desktop banners width/height at least requested. Other images: "
            "short edge >=1024, or short edge >=768 and area >=1500000. "
            "The native artifact is delivered unchanged; requested target is "
            "not an exact-pixel resampled export. No cropping or size relaxation."
        )
    elif context["job_type"] == "domestic_main_image":
        valid = valid and width == height and min(width, height) >= 1024
        policy = (
            "Existing native main-image validator: exact square, short edge >=1024."
        )
    elif context["job_type"] == "model_pose_transfer":
        valid = valid and alpha_min == 255
        policy += " Opaque original background must cover the full pose canvas."
    return {
        "policy": policy,
        "measured_native_size": [width, height],
        "requested_target": [target_width, target_height],
        "server_technical_checks_passed": valid,
    }


def response_schema(profile: str, source_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    if profile == PREPARE:
        properties = {
            "selected_regions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["asset_id", "box", "identity", "visible_print"],
                    "properties": {
                        "asset_id": {
                            **text,
                            **({"enum": list(source_ids)} if source_ids else {}),
                        },
                        "box": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "identity": text,
                        "visible_print": {"type": "array", "items": text},
                    },
                },
            },
            "protected_attributes": {"type": "array", "minItems": 1, "items": text},
            "allowed_changes": {"type": "array", "minItems": 1, "items": text},
            "selection_summary": text,
        }
    else:
        assert profile == REVIEW
        properties = {
            "checks": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CHECKS),
                "properties": {
                    key: {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["passed", "evidence"],
                        "properties": {
                            "passed": {"type": "boolean"},
                            "evidence": {"type": "string", "minLength": 4},
                        },
                    }
                    for key in CHECKS
                },
            },
            "unresolved": {"type": "array", "items": text},
            "observable_defects": {"type": "array", "items": text},
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def validate_preparation(payload: object, ids: tuple[str, ...]) -> dict[str, Any]:
    Draft202012Validator(response_schema(PREPARE, ids)).validate(payload)
    assert isinstance(payload, dict)
    for region in payload["selected_regions"]:
        x0, y0, x1, y1 = region["box"]
        if not (x1 > x0 and y1 > y0 and (x1 - x0) * (y1 - y0) >= 0.0001):
            raise ValueError("Source selection has invalid geometry")
    return payload


def validate_review(payload: object) -> tuple[bool, list[str]]:
    Draft202012Validator(response_schema(REVIEW)).validate(payload)
    assert isinstance(payload, dict)
    failed = [name for name in CHECKS if payload["checks"][name]["passed"] is not True]
    if payload["unresolved"] or payload["observable_defects"]:
        failed.append("unresolved_evidence")
    return not failed, failed


def contract_diagnostic(error: ValidationError) -> dict[str, Any]:
    missing = []
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = sorted(set(error.validator_value) - set(error.instance))
    return {
        "validator": error.validator,
        "path": "/" + "/".join(str(part) for part in error.absolute_path),
        "missing_fields": missing,
    }


class FidelityGateway:
    def __init__(self, wrapped: Any, sessions: Any, storage: Any) -> None:
        self.wrapped, self.sessions, self.storage = wrapped, sessions, storage
        self._prepared: dict[str, dict[str, Any]] = {}
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    def _context(self, request: Any) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(JobRecord, request.job_id)
            if job is None or job.workspace_id != request.workspace_id:
                raise ValueError("Image fidelity scope invalid")
            item = session.get(JobItemRecord, request.job_item_id)
            if (
                item is None
                or item.job_id != job.id
                or item.workspace_id != job.workspace_id
            ):
                raise ValueError("Image fidelity item scope invalid")
            return {
                "job_type": job.type,
                "item_key": item.item_key,
                "settings": dict(job.configuration.get("settings", {})),
            }

    def _frozen(
        self,
        request: Any,
        value: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Freeze policy/crops before dispatch for identical reconciliation."""
        binding = canonical_hash(
            {
                "workspace": request.workspace_id,
                "job": request.job_id,
                "item": request.job_item_id,
                "prompt": request.prompt,
                "size": request.size,
                "quality": request.quality,
                "format": request.output_format,
                "fidelity": request.input_fidelity,
                "sources": [
                    (s.asset_id, s.sha256, s.source_role) for s in request.sources
                ],
            }
        )
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        with self.sessions() as session:
            job = session.get(JobRecord, request.job_id, with_for_update=True)
            if (
                job is None
                or job.workspace_id != request.workspace_id
                or job.status != "processing"
            ):
                raise ValueError("Image fidelity job is no longer active")
            item = session.scalar(
                select(JobItemRecord)
                .where(
                    JobItemRecord.id == request.job_item_id,
                    JobItemRecord.job_id == request.job_id,
                    JobItemRecord.workspace_id == request.workspace_id,
                )
                .with_for_update()
            )
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if (
                item is None
                or attempt is None
                or attempt.status != "running"
                or (
                    attempt.workspace_id,
                    attempt.job_id,
                    attempt.job_item_id,
                    attempt.provider_idempotency_key,
                )
                != (
                    request.workspace_id,
                    request.job_id,
                    request.job_item_id,
                    request.idempotency_key,
                )
            ):
                raise ValueError("Image fidelity attempt scope invalid")
            rows = dict(item.configuration.get("_image_fidelity_frozen", {}))
            prior = rows.get(key)
            if prior is not None:
                checksum = canonical_hash(
                    {k: v for k, v in prior.items() if k != "receipt_sha256"}
                )
                if (
                    prior.get("binding") != binding
                    or prior.get("receipt_sha256") != checksum
                ):
                    raise ValueError("Frozen image fidelity inputs changed")
                return prior
            if value is None:
                return None
            if len(rows) >= 64:
                raise ValueError("Image fidelity receipt limit reached")
            rows[key] = {**value, "binding": binding, "version": VERSION}
            rows[key]["receipt_sha256"] = canonical_hash(rows[key])
            item.configuration = {
                **item.configuration,
                "_image_fidelity_frozen": rows,
            }
            session.commit()
            return rows[key]

    async def _plan(
        self,
        request: Any,
        profile: str,
        sources: tuple[Any, ...],
        context: dict[str, Any],
        suffix: str = "",
    ) -> Any:
        from .xinghe_agent import PlannerRequest

        return await self.wrapped.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=request.job_item_id,
                sources=sources,
                page_count=1,
                language="zh-CN",
                product_name="Source-grounded image acceptance",
                product_information=(json.dumps(context, ensure_ascii=False),),
                platform_code="IMAGE_FIDELITY",
                size=request.size,
                idempotency_key=f"{request.idempotency_key}:{profile}{suffix}",
                planning_profile=profile,
                system_prompt=POLICY + "\nInspect attached pixels, not receipts. "
                "Return the exact JSON schema. "
                "Any missing or uncertain evidence fails. Never guess physical text. "
                "When reviewing pose edits, inspect protected "
                "identity/outfit/background, not pose scores.",
            )
        )

    async def edit(self, request: Any) -> Any:
        from .xinghe_agent import ImageCallResult, SourceImage

        original_sources = request.sources
        plans: list[Any] = []
        preparation = None
        try:
            context = self._context(request)
            if any(
                hashlib.sha256(source.payload).hexdigest() != source.sha256
                for source in original_sources
            ):
                raise ValueError("Image fidelity source hash changed")
            if context["job_type"] == "detail_pack" or (
                context["job_type"] == "domestic_main_image"
                and context["settings"].get("main_image_speed") != "native_fast"
            ):
                # Native Harness image requests are signed and have their own
                # source/pixel audits. Never modify a signed request downstream.
                return await self.wrapped.edit(request)
            frozen = self._frozen(request)
            crop_scale = (
                frozen.get("crop_scale", "native-v1") if frozen else "nearest-4-1024-v1"
            )
            if frozen is not None:
                preparation = frozen["preparation"]
            if context["job_type"] in {
                "product_image",
                "creative_image",
                "reference_remix",
                "amazon_visual_suite",
            } and (not request.reconcile_only or frozen is not None):
                authorities = tuple(
                    s for s in original_sources if s.source_role in AUTHORITY_ROLES
                )
                ids = tuple(s.asset_id for s in authorities)
                if not ids:
                    raise ValueError("Missing product identity authority")
                scope = canonical_hash(
                    {
                        "job": request.job_id,
                        "context": {
                            k: v for k, v in context.items() if k != "item_key"
                        },
                        "sources": [
                            (s.asset_id, s.sha256, s.source_role)
                            for s in original_sources
                            if s.source_role != "format_canvas"
                        ],
                    }
                )
                lock = self._locks.setdefault(scope, asyncio.Lock())
                async with lock:
                    preparation = preparation or self._prepared.get(scope)
                    if preparation is None:
                        plan = await self._plan(
                            request,
                            PREPARE,
                            original_sources,
                            {
                                **context,
                                "source_roles": [
                                    (s.asset_id, s.source_role)
                                    for s in original_sources
                                ],
                                "schema": response_schema(PREPARE, ids),
                                "instruction": "Select exactly the objects requested "
                                "by the user from product sources. Give tight "
                                "complete-object boxes in normalized source-image "
                                "coordinates. Read the original physical printing, "
                                "not the style image's text. If unreadable, state "
                                "UNREADABLE rather than inventing a transcription.",
                            },
                        )
                        plans.append(plan)
                        if plan.error_code:
                            raise ValueError("Preparation unavailable")
                        preparation = validate_preparation(plan.payload, ids)
                        if len(self._prepared) >= 128:
                            self._prepared.clear()
                        self._prepared[scope] = preparation
                crops = []
                preparation = validate_preparation(preparation, ids)
                for index, region in enumerate(preparation["selected_regions"]):
                    if context["settings"].get("task_type") == "change_model":
                        # A duplicated full-person crop reinforces the very face
                        # being replaced. The full garment source stays attached.
                        continue
                    source = next(
                        s for s in authorities if s.asset_id == region["asset_id"]
                    )
                    with Image.open(BytesIO(source.payload)) as im:
                        x0, y0, x1, y1 = region["box"]
                        box = (
                            math.floor(x0 * im.width),
                            math.floor(y0 * im.height),
                            math.ceil(x1 * im.width),
                            math.ceil(y1 * im.height),
                        )
                        buf = BytesIO()
                        detail = im.crop(box)
                        if crop_scale == "nearest-4-1024-v1":
                            factor = min(4, max(1, 1024 // max(detail.size)))
                            if factor > 1:
                                # Pixel replication enlarges reference evidence;
                                # it invents no glyph strokes. Delivered pixels
                                # are never enlarged by this reference-only step.
                                detail = detail.resize(
                                    (detail.width * factor, detail.height * factor),
                                    Image.Resampling.NEAREST,
                                )
                        detail.save(buf, "PNG")
                    data = buf.getvalue()
                    crops.append(
                        SourceImage(
                            f"fidelity-detail-{index}",
                            request.group_key,
                            f"selected-product-{index + 1}.png",
                            "image/png",
                            hashlib.sha256(data).hexdigest(),
                            data,
                            source_role="product_detail",
                        )
                    )
                edited_sources = original_sources + tuple(crops)
            else:
                edited_sources = original_sources
            if frozen is None and not request.reconcile_only:
                frozen = self._frozen(
                    request,
                    {
                        "preparation": preparation,
                        "crop_scale": crop_scale,
                        "prompt": request.prompt
                        + "\n"
                        + operation_policy(context)
                        + "\nSOURCE_SELECTION="
                        + json.dumps(preparation, ensure_ascii=False),
                    },
                )
            if frozen is not None:
                request = replace(
                    request,
                    sources=edited_sources,
                    prompt=frozen["prompt"],
                )
        except Exception as exc:
            if isinstance(exc, ValidationError):
                logger.warning(
                    "image_fidelity_contract_invalid job_id=%s "
                    "profile=%s diagnostic=%s",
                    request.job_id,
                    PREPARE,
                    contract_diagnostic(exc),
                )
            logger.warning(
                "image_fidelity_preparation_failed job_id=%s item_id=%s reason=%s",
                request.job_id,
                request.job_item_id,
                type(exc).__name__,
            )
            return self._account(
                ImageCallResult(
                    ProviderOutcome.FAILED,
                    None,
                    {"network_requests": 0, "fidelity_preparation_failed": True},
                    {
                        "reported": True,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "total_tokens": 0,
                    },
                    0.0,
                    0,
                    estimated_cost_micros=0,
                    error_code="IMAGE_FIDELITY_PREPARATION_FAILED",
                    error_message="未能完整确认需保留的商品与图案，尚未发送生图请求。",
                ),
                plans,
            )
        result = await self.wrapped.edit(request)
        if result.outcome != ProviderOutcome.SUCCEEDED or result.artifact is None:
            return self._account(result, plans)
        return await self._review(
            request, context, request.sources, preparation, plans, result
        )

    async def review_existing(
        self,
        request: Any,
        *,
        sources: tuple[Any, ...],
        prompt: str,
        size: str,
        artifact: Any,
    ) -> Any:
        """Review a hash-verified original without dispatching a replacement image."""
        from .provider import ProviderResult
        from .xinghe_agent import ImageCallResult, ImageEditRequest

        image_request = ImageEditRequest(
            request.workspace_id,
            request.job_id,
            request.job_item_id,
            request.attempt_id,
            1,
            request.job_item_id,
            sources,
            prompt,
            "high",
            request.idempotency_key,
            size,
        )
        result = await self._review(
            image_request,
            self._context(image_request),
            sources,
            None,
            [],
            ImageCallResult(
                ProviderOutcome.SUCCEEDED,
                None,
                {"network_requests": 0},
                {"reported": True},
                0.0,
                0,
                estimated_cost_micros=0,
                artifact=artifact,
            ),
        )
        return ProviderResult(
            outcome=result.outcome,
            artifact=result.artifact,
            error_code=result.error_code,
            error_message=result.error_message,
            retryable=False,
            usage_reported=result.normalized_usage["reported"],
            raw_usage={**result.raw_usage, "reused": True},
            actual_cost_micros=result.actual_cost_micros,
            estimated_cost_micros=result.estimated_cost_micros,
            provider_name=self.provider_name,
            model_name=self.image_model,
            pricing_version=self.pricing_version,
        )

    async def _review(
        self,
        request: Any,
        context: dict[str, Any],
        original_sources: tuple[Any, ...],
        preparation: Any,
        plans: list[Any],
        result: Any,
    ) -> Any:
        from .xinghe_agent import SourceImage

        candidate = result.artifact
        review_source = SourceImage(
            "candidate",
            request.group_key,
            candidate.filename,
            candidate.content_type,
            candidate.sha256,
            candidate.payload,
            source_role="candidate",
        )
        accepted = False
        failed = ["review_unavailable"]
        technical = technical_contract(context, request, candidate)
        feedback = None
        for attempt in range(2):
            review_context = {
                **context,
                "generation_contract": request.prompt,
                "source_selection": preparation,
                "candidate_dimensions": [candidate.width, candidate.height],
                "technical_contract": technical,
                "contract_feedback": feedback,
                "source_roles": [(s.asset_id, s.source_role) for s in original_sources],
                "schema": response_schema(REVIEW),
                "instruction": "Compare candidate against original authority, "
                "including each selected design and exact visible physical text. "
                "For change_model, short sleeves must remain short sleeves, "
                "never the reference's tank top. Do not declare pass if source "
                "printing changed or uncertainty/defects remain. "
                "observable_defects lists only violations of the request, "
                "not authorized changes. Decoded candidate dimensions are "
                "measured facts supplied by the server. For canvas, apply the "
                "technical_contract's EXISTING native-original acceptance policy, "
                "not an invented exact-pixel export requirement. A failed measured "
                "technical check must fail canvas. Subject and printing checks "
                "still require direct inspection of actual attached pixels.",
            }
            try:
                review = await self._plan(
                    request,
                    REVIEW,
                    original_sources + (review_source,),
                    review_context,
                    suffix=f":{attempt + 1}",
                )
            except Exception as exc:
                logger.warning(
                    "image_fidelity_review_unavailable job_id=%s item_id=%s reason=%s",
                    request.job_id,
                    request.job_item_id,
                    type(exc).__name__,
                )
                break
            plans.append(review)
            try:
                if review.error_code:
                    raise ValueError("Review unavailable")
                accepted, failed = validate_review(review.payload)
                if technical["server_technical_checks_passed"] is not True:
                    accepted = False
                    if "canvas" not in failed:
                        failed.append("canvas")
                break
            except ValidationError as exc:
                feedback = contract_diagnostic(exc)
                logger.warning(
                    "image_fidelity_contract_invalid job_id=%s profile=%s "
                    "attempt=%s diagnostic=%s",
                    request.job_id,
                    REVIEW,
                    attempt + 1,
                    feedback,
                )
                continue
            except (ValueError, TypeError, KeyError):
                continue
        result = self._account(result, plans)
        raw = dict(result.raw_usage or {})
        raw["metrics"] = {
            **raw.get("metrics", {}),
            "image_fidelity": {
                **raw.get("metrics", {}).get("image_fidelity", {}),
                "version": VERSION,
                "accepted": accepted,
                "failed_checks": failed,
                "candidate_sha256": candidate.sha256,
            },
        }
        if not accepted:
            try:
                asset_id = await self._retain(request, candidate)
            except Exception as exc:
                logger.warning(
                    "image_fidelity_retention_failed job_id=%s item_id=%s reason=%s",
                    request.job_id,
                    request.job_item_id,
                    type(exc).__name__,
                )
                return replace(
                    result,
                    outcome=ProviderOutcome.FAILED,
                    artifact=None,
                    error_code="IMAGE_FIDELITY_AUDIT_FAILED",
                    error_message="保真审核或候选留存未完成，未交付。",
                    retryable=False,
                    raw_usage=raw,
                )
            raw["metrics"]["image_fidelity"]["retained_asset_id"] = asset_id
            logger.warning(
                "image_fidelity_rejected job_id=%s item_id=%s failed_checks=%s",
                request.job_id,
                request.job_item_id,
                failed,
            )
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                artifact=None,
                error_code="IMAGE_FIDELITY_REJECTED",
                error_message="生成图未通过商品图案、文字或服装保真验收，候选原图已保留。",
                retryable=False,
                raw_usage=raw,
            )
        return replace(result, raw_usage=raw)

    def _account(self, result: Any, plans: list[Any]) -> Any:
        from .xinghe_agent import _planner_call_results

        plans = list(_planner_call_results(plans))

        def total(name: str) -> int | None:
            values = [
                getattr(result, name, None),
                *[getattr(p, name, None) for p in plans],
            ]
            if any(type(value) is not int for value in values):
                return None
            return sum(value for value in values if isinstance(value, int))

        raw = dict(result.raw_usage or {})
        image_requests = raw.get("network_requests", 1)
        raw["network_requests"] = image_requests + len(plans)
        raw["fidelity_planning_requests"] = len(plans)
        raw["metrics"] = {
            **raw.get("metrics", {}),
            "image_fidelity": {
                "version": VERSION,
                "costs_aggregated": True,
                "image_requests": image_requests,
                "planning_requests": len(plans),
            },
        }
        reported = result.normalized_usage.get("reported") is True and all(
            p.normalized_usage.get("reported") is True for p in plans
        )
        raw["_provider_usage_reported"] = reported
        normalized: dict[str, Any] = {"reported": reported}
        for field in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
            values = [
                result.normalized_usage.get(field),
                *[plan.normalized_usage.get(field) for plan in plans],
            ]
            normalized[field] = (
                sum(value for value in values if isinstance(value, int))
                if all(type(value) is int for value in values)
                else None
            )
            raw[field] = normalized[field]
        reserve = getattr(self.wrapped, "review_price_micros", None)
        estimate = result.estimated_cost_micros
        if plans:
            estimate = (
                estimate + len(plans) * reserve
                if type(estimate) is int and type(reserve) is int
                else None
            )
        return replace(
            result,
            raw_usage=raw,
            actual_cost_micros=total("actual_cost_micros"),
            estimated_cost_micros=estimate,
            normalized_usage=normalized,
            api_latency_seconds=result.api_latency_seconds
            + sum(p.api_latency_seconds for p in plans),
        )

    async def _retain(self, request: Any, artifact: Any) -> str:
        with self.sessions() as session:
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if attempt is None or (
                attempt.workspace_id,
                attempt.job_id,
                attempt.job_item_id,
                attempt.provider_idempotency_key,
            ) != (
                request.workspace_id,
                request.job_id,
                request.job_item_id,
                request.idempotency_key,
            ):
                raise ValueError("Candidate scope invalid")
        suffix = Path(artifact.filename).suffix.lower()
        key = (
            f"{request.workspace_id}/fidelity/{request.job_item_id}/"
            f"{request.attempt_id}/{artifact.sha256}{suffix}"
        )
        stored = await self.storage.save_validated(
            ValidatedImage(
                artifact.payload,
                artifact.filename,
                artifact.content_type,
                artifact.width,
                artifact.height,
                key,
                artifact.sha256,
            )
        )
        with self.sessions() as session:
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if attempt is None or (
                attempt.workspace_id,
                attempt.job_id,
                attempt.job_item_id,
            ) != (request.workspace_id, request.job_id, request.job_item_id):
                raise ValueError("Candidate scope invalid")
            prior = session.scalar(
                select(JobAssetRecord).where(
                    JobAssetRecord.job_item_id == request.job_item_id,
                    JobAssetRecord.role == "fidelity_candidate",
                    JobAssetRecord.ordinal == attempt.attempt_no,
                )
            )
            if prior is not None:
                asset = session.get(AssetRecord, prior.asset_id)
                if asset is None or asset.sha256 != artifact.sha256:
                    raise ValueError("Retained candidate binding changed")
                return prior.asset_id
            asset_id = new_id("asset")
            session.add(
                AssetRecord(
                    id=asset_id,
                    workspace_id=request.workspace_id,
                    media_kind="image",
                    origin_kind="generated",
                    lifecycle_status="ready",
                    original_filename=artifact.filename,
                    content_type=artifact.content_type,
                    byte_size=len(artifact.payload),
                    width=artifact.width,
                    height=artifact.height,
                    sha256=artifact.sha256,
                    storage_provider=stored.storage_provider,
                    storage_bucket=stored.storage_bucket,
                    object_key=stored.object_key,
                    asset_metadata={"fidelity_candidate": True, "policy": VERSION},
                )
            )
            session.flush()
            session.add(
                JobAssetRecord(
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    job_item_id=request.job_item_id,
                    asset_id=asset_id,
                    direction="intermediate",
                    role="fidelity_candidate",
                    ordinal=attempt.attempt_no,
                    is_deliverable=False,
                )
            )
            session.commit()
            return asset_id
