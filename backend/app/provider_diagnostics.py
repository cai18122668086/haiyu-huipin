"""Bounded provider receipts, excluding credentials and image/request bodies."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CONTENT_REJECTION_CODE = "IMAGE_CONTENT_REJECTED"
MAX_PROVIDER_ATTEMPTS = 32
CONTENT_REJECTION_MESSAGE = (
    "生成服务因内容限制拒绝了本次请求，未返回图片。原素材和要求已保留。"
)
PREPARATION_FAILED = "IMAGE_FIDELITY_PREPARATION_FAILED"
PREPARATION_FAILED_MESSAGE = (
    "生图前检查未能完成，本次未开始生成。请稍后重试。"
)
SUBJECT_UNCLEAR = "SOURCE_PRODUCT_UNCLEAR"
PRINT_UNREADABLE = "SOURCE_PRINT_UNREADABLE"
DETAIL_UNCLEAR = "SOURCE_DETAIL_UNCLEAR"
SOURCE_UPLOAD_MESSAGES = {
    DETAIL_UNCLEAR: "原图里看不清这处商品细节，这一项已停止生成。请上传商品全图，"
    "并附上一张对焦清楚的细节近照。",
    SUBJECT_UNCLEAR: (
        "照片里看不清商品的完整轮廓，这一项已停止生成。"
        "请把商品放在纯色背景前，移开遮挡物，拍清主体和配件后重新上传。"
    ),
    PRINT_UNREADABLE: (
        "原图上的商品印字看不清，这一项已停止生成。"
        "请对焦印字，避开反光，重新上传能看清文字的原图。"
    ),
}
FIDELITY_REJECTED = "IMAGE_FIDELITY_REJECTED"
FIDELITY_REJECTED_MESSAGE = (
    "生成图未通过商品保真审核，候选图已保留。请核对商品外观、图案与文字要求。"
)
_FIDELITY_PREPARATION_STAGES = frozenset({
    "source_binding", "source_selection", "source_matte", "source_readback",
    "source_recolor", "background_layout", "freeze_receipt",
})


def preparation_message(stage: object) -> str:
    if stage == "source_matte":
        return (
            "商品轮廓未能与背景完整分开，本次未发送生图请求。"
            "请换用主体完整、周围没有遮挡物的商品实拍图。"
        )
    if stage == "source_readback":
        return (
            "商品原图上的文字未能核对，本次未发送生图请求。"
            "请上传能看清印字的原图。"
        )
    return PREPARATION_FAILED_MESSAGE


def public_failure_message(
    code: str | None, stored: str | None, details: object = None,
) -> str | None:
    if code in SOURCE_UPLOAD_MESSAGES:
        return SOURCE_UPLOAD_MESSAGES[code]
    if code == PREPARATION_FAILED and isinstance(details, Mapping):
        fidelity = details.get("image_fidelity")
        if (
            isinstance(fidelity, Mapping)
            and fidelity.get("preparation_stage") == "source_selection"
            and fidelity.get("planner_http_status") == 400
        ):
            return (
                "商品识别服务拒绝了本次检查请求，尚未开始生图。"
                "请联系客服并提供任务编号。"
            )
    # Read historical generic adapter errors without rewriting task history.
    if code == "IMAGE_REQUEST_FAILED" and isinstance(details, Mapping):
        error = details.get("provider_error")
        if (
            details.get("http_status") == 400 and isinstance(error, Mapping)
            and error.get("code") == "unsupported_size"
        ):
            return "生成服务不支持所选尺寸。请更换图片尺寸后重新提交。"
    if stored in {
        None,
        "Image provider returned a controlled failure.",
        "本张主图生成未完成，可重试。",
    }:
        if code == PREPARATION_FAILED:
            if isinstance(details, Mapping):
                fidelity = details.get("image_fidelity")
                if isinstance(fidelity, Mapping):
                    return preparation_message(fidelity.get("preparation_stage"))
            return PREPARATION_FAILED_MESSAGE
        if code == FIDELITY_REJECTED:
            return FIDELITY_REJECTED_MESSAGE
    return stored


def image_content_refusal(payload: Mapping[str, Any]) -> bool:
    """Recognize an explicit refusal even when a relay wraps it as text output."""
    error = payload.get("error")
    if not isinstance(error, Mapping) or any(
        payload.get(key)
        for key in (
            "data",
            "images",
            "output",
            "url",
            "b64_json",
            "generation_id",
            "task_id",
        )
    ):
        return False
    code = str(error.get("code") or "").casefold()
    if code in {
        "content_policy_violation",
        "content_policy_rejected",
        "safety_violation",
        "moderation_blocked",
        CONTENT_REJECTION_CODE.casefold(),
    }:
        return True
    if code != "upstream_text_reply":
        return False
    return bool(
        re.search(
            r"(?:violat\w*.{0,80}(?:guardrails?|content polic)|"
            r"similarity to third.party content|content.policy.violation|"
            r"内容(?:政策|审核).{0,12}(?:拒绝|限制|不允许)|违反.{0,12}安全)",
            str(error.get("message") or "")[:2400],
            re.I | re.S,
        )
    )


def safe_text(value: str, limit: int = 1200) -> str:
    value = re.sub(r"data:[^\s\"']+", "[image omitted]", value)
    value = re.sub(r"https?://[^\s\"']+", "[url omitted]", value)
    value = re.sub(r"\b(?:sk|rk)-[\w-]+", "[credential omitted]", value)
    value = re.sub(r"(?i)bearer\s+[^\s,;\"']+", "Bearer [omitted]", value)
    value = re.sub(
        r"(?i)((?:api[_-]?key|authorization|password|secret|access[_-]?token)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+",
        r"\1[omitted]",
        value,
    )
    return value[:limit]


def error_summary(payload: Mapping[str, Any]) -> dict[str, str]:
    error = payload.get("error")
    if isinstance(error, Mapping):
        result = {
            key: safe_text(str(error[key]), 1200 if key == "message" else 255)
            for key in ("code", "type", "message", "param")
            if isinstance(error.get(key), (str, int)) and str(error[key])
        }
        return result or {"code": "provider_error"}
    if isinstance(error, str):
        return {"code": "provider_error", "message": safe_text(error)}
    message = payload.get("message")
    return {
        "code": "provider_error",
        **({"message": safe_text(message)} if isinstance(message, str) else {}),
    }


def known_image_route_rejection(status: int, payload: Mapping[str, Any]) -> bool:
    error = payload.get("error")
    if (
        status != 400
        or image_content_refusal(payload)
        or not isinstance(error, Mapping)
        or any(
            payload.get(key)
            for key in (
                "data",
                "images",
                "output",
                "url",
                "b64_json",
                "generation_id",
                "task_id",
            )
        )
    ):
        return False
    code = error.get("code")
    if code == "unsupported_size":
        # This model rejected the unchanged dimensions at admission.
        # An accepted task must be reconciled instead of being redispatched.
        return error.get("type") == "invalid_request_error" and not payload.get("id")
    if code == "upstream_text_reply":
        return error.get("type") == "invalid_request_error"
    return code in {
        "model_not_found",
        "unsupported_model",
        "model_not_supported",
        "channel_unavailable",
        "no_available_channel",
    }


def retained_metrics(raw_usage: object) -> dict[str, Any]:
    """Defend at the adapter/persistence boundary as well as the gateway."""
    metrics = raw_usage.get("metrics") if isinstance(raw_usage, Mapping) else None
    if not isinstance(metrics, Mapping):
        return {}
    result: dict[str, Any] = {}
    rendering = metrics.get("rendering")
    if isinstance(rendering, Mapping) and rendering.get("kind") in {
        "native_background_source_products",
        "source_original_recolor",
    }:
        result["rendering"] = {
            key: value
            for key, value in rendering.items()
            if (
                key in {"kind", "method", "version"}
                and isinstance(value, str)
                and re.fullmatch(r"[a-zA-Z0-9_-]{1,96}", value)
            )
            or (
                key
                in {
                    "background_sha256",
                    "source_sha256",
                    "result_sha256",
                    "font_sha256",
                }
                and isinstance(value, str)
                and re.fullmatch(r"[0-9a-f]{64}", value)
            )
            or (
                key in {"changed_pixels", "outside_mask_changed_pixels"}
                and type(value) is int
                and 0 <= value <= 50_000_000
            )
        }
    fidelity = metrics.get("image_fidelity")
    if isinstance(fidelity, Mapping):
        result["image_fidelity"] = {
            key: value
            for key, value in fidelity.items()
            if (
                key in {"accepted", "costs_aggregated", "independent_ocr_matched"}
                and type(value) is bool
            )
            or (
                key in {"image_requests", "planning_requests"}
                and type(value) is int
                and 0 <= value <= MAX_PROVIDER_ATTEMPTS
            )
            or (
                key
                in {
                    "version",
                    "candidate_sha256",
                    "retained_asset_id",
                    "detail_review_version",
                }
                and isinstance(value, str)
                and len(value) <= 255
            )
            or (key == "print_verification" and value == "original_rgb_exact")
            or (
                key == "planner_http_status" and type(value) is int
                and 100 <= value <= 599
            )
            or (
                key == "preparation_stage"
                and isinstance(value, str)
                and value in _FIDELITY_PREPARATION_STAGES
            )
            or (
                key == "failed_checks"
                and isinstance(value, list)
                and len(value) <= 8
                and all(isinstance(v, str) and len(v) <= 80 for v in value)
            )
        }
    # This server-created receipt links a delivered white image to its owned
    # original alpha layers. Dropping it here forces downstream re-extraction
    # from flattened white pixels. Retain only the complete bounded binding,
    # consistent with the accepted compositor receipt; never arbitrary metadata.
    lineage = metrics.get("white_product_lineage")
    hashes = {"receipt_key", "recipe_sha256", "output_sha256", "background_sha256"}
    owners = {"workspace_id", "job_id", "item_id"}
    if (
        isinstance(lineage, Mapping)
        and set(lineage) == hashes | owners | {"version"}
        and lineage.get("version") == "owned-white-product-lineage-v1"
        and all(
            isinstance(lineage[key], str)
            and re.fullmatch(r"[0-9a-f]{64}", lineage[key])
            for key in hashes
        )
        and all(
            isinstance(lineage[key], str)
            and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", lineage[key])
            for key in owners
        )
        and result.get("image_fidelity", {}).get("accepted") is True
        and result.get("rendering", {}).get("kind")
        == "native_background_source_products"
        and result["rendering"].get("result_sha256") == lineage["output_sha256"]
        and result["rendering"].get("background_sha256")
        == lineage["background_sha256"]
    ):
        result["white_product_lineage"] = dict(lineage)
    recovery = metrics.get("image_recovery")
    if isinstance(recovery, Mapping):
        result["image_recovery"] = {
            key: safe_text(value, 255) if isinstance(value, str) else value
            for key, value in recovery.items()
            if (
                key in {"can_resume_original", "reconcile_only"} and type(value) is bool
            )
            or (
                key in {"rate_limit_retries", "url_refreshes", "automatic_resumptions"}
                and type(value) is int
                and 0 <= value <= 10
            )
            or (key == "original_task_id" and isinstance(value, str))
        }
    error = metrics.get("provider_error")
    if isinstance(error, Mapping):
        result["provider_error"] = error_summary({"error": error})
    attempts = metrics.get("provider_attempts")
    if isinstance(attempts, list):
        result["provider_attempts"] = [
            {
                key: safe_text(str(value), 255) if isinstance(value, str) else value
                for key, value in row.items()
                if key in {"route", "host", "model", "http_status", "request_id"}
                and (value is None or type(value) in {str, int})
            }
            | (
                {"error": error_summary({"error": row["error"]})}
                if isinstance(row.get("error"), Mapping)
                else {}
            )
            for row in attempts[:MAX_PROVIDER_ATTEMPTS]
            if isinstance(row, Mapping)
        ]
    return result


def retained_request_count(raw_usage: object) -> int:
    """Count only gateway transport receipts and our bounded review receipts."""
    metrics = retained_metrics(raw_usage)
    count = (
        raw_usage.get("network_requests") if isinstance(raw_usage, Mapping) else None
    )
    trace = metrics.get("provider_attempts")
    image_count = len(trace) if isinstance(trace, list) else None
    fidelity = metrics.get("image_fidelity", {})
    if fidelity.get("costs_aggregated") is True:
        images, plans = (
            fidelity.get("image_requests"),
            fidelity.get("planning_requests"),
        )
        if (
            type(images) is int
            and type(plans) is int
            and type(count) is int
            and images in {0, image_count}
            and count == images + plans
        ):
            return count
    if type(count) is int and count == image_count:
        return count
    return 1
