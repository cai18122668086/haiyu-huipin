"""Keep bounded, credential-free image receipts at the replica boundary."""

from typing import Any

from .provider_diagnostics import retained_metrics, safe_text


def failure_fields(
    error: Exception, calls: list[dict[str, Any]], ordinal: int
) -> dict[str, Any]:
    code = getattr(error, "code", "REPLICA_DELIVERY_INVALID")
    failed = next((c for c in reversed(calls) if c.get("error_code")), {})
    if not failed and code == "REPLICA_CANVAS_MISMATCH":
        failed = next((c for c in reversed(calls) if c.get("kind") == "image"), {})
    if code == "REPLICA_SKILL_BLOCKED" and failed.get("error_code"):
        code = failed["error_code"]
    metrics = retained_metrics(failed)
    metrics.update(getattr(error, "provider_metrics", {}))
    metrics["failed_screen"] = ordinal + 1
    if code == "REPLICA_CANVAS_MISMATCH":
        metrics["canvas_validation"] = {
            "requested_size": getattr(error, "expected_size", None),
            "actual_size": getattr(error, "actual_size", None),
            "image_attempts": getattr(error, "attempts", 1),
        }
    message = safe_text(str(error), 800) or "本屏未返回可用图片。"
    provider_error = metrics.get("provider_error", {})
    if provider_error.get("message"):
        message += "（" + provider_error["message"] + "）"
    request_id = getattr(error, "provider_request_id", None) or failed.get(
        "provider_request_id"
    )
    if not request_id and code == "REPLICA_CANVAS_MISMATCH":
        attempts = metrics.get("provider_attempts", [])
        if attempts:
            request_id = attempts[-1].get("request_id")
    receipt = f" 错误码：{code}。"
    if provider_error.get("code"):
        receipt += f" 上游错误：{provider_error['code']}。"
    if request_id:
        receipt += f" 原任务编号：{safe_text(str(request_id), 255)}。"
    resumptions = metrics.get("image_recovery", {}).get("automatic_resumptions", 0)
    if resumptions:
        receipt += (
            f" 已自动续查原任务 {resumptions} 次（最多2次），"
            "未新建替代生图任务。"
        )
    return {
        "error_code": code,
        "error_message": f"第 {ordinal + 1} 屏：{message}{receipt} 已生成图片已保留。",
        "provider_request_id": request_id,
        "http_status": getattr(error, "http_status", None) or failed.get("http_status"),
        "raw_usage_metrics": metrics,
    }
