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


def image_content_refusal(payload: Mapping[str, Any]) -> bool:
    """Recognize an explicit refusal even when a relay wraps it as text output."""
    error = payload.get("error")
    if not isinstance(error, Mapping) or any(
        payload.get(key) for key in (
            "data", "images", "output", "url", "b64_json", "generation_id", "task_id"
        )
    ):
        return False
    code = str(error.get("code") or "").casefold()
    if code in {
        "content_policy_violation", "content_policy_rejected", "safety_violation",
        "moderation_blocked", CONTENT_REJECTION_CODE.casefold(),
    }:
        return True
    if code != "upstream_text_reply":
        return False
    return bool(re.search(
        r"(?:violat\w*.{0,80}(?:guardrails?|content polic)|"
        r"similarity to third.party content|content.policy.violation|"
        r"内容(?:政策|审核).{0,12}(?:拒绝|限制|不允许)|违反.{0,12}安全)",
        str(error.get("message") or "")[:2400], re.I | re.S,
    ))


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
        payload.get(key) for key in (
            "data", "images", "output", "url", "b64_json", "generation_id", "task_id"
        )
        )
    ):
        return False
    code = error.get("code")
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
    recovery = metrics.get("image_recovery")
    if isinstance(recovery, Mapping):
        result["image_recovery"] = {
            key: safe_text(value, 255) if isinstance(value, str) else value
            for key, value in recovery.items()
            if (
                key in {"can_resume_original", "reconcile_only"}
                and type(value) is bool
            ) or (
                key in {"rate_limit_retries", "url_refreshes", "automatic_resumptions"}
                and type(value) is int and 0 <= value <= 10
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
