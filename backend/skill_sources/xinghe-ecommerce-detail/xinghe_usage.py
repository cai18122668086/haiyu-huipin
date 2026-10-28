"""Normalize and aggregate token usage from OpenAI-compatible relay responses."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_token_usage(raw_usage: Any) -> dict[str, Any]:
    if not isinstance(raw_usage, dict):
        return {"reported": False}

    input_tokens = _integer(raw_usage.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _integer(raw_usage.get("prompt_tokens"))
    output_tokens = _integer(raw_usage.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _integer(raw_usage.get("completion_tokens"))
    total_tokens = _integer(raw_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    normalized: dict[str, Any] = {
        "reported": True,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "raw": raw_usage,
    }
    return normalized


def aggregate_token_usage(usages: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    reported = 0
    unreported = 0
    input_total = 0
    output_total = 0
    total = 0
    input_complete = True
    output_complete = True
    total_complete = True

    for usage in usages:
        if not usage or not usage.get("reported"):
            unreported += 1
            continue
        reported += 1
        if usage.get("input_tokens") is None:
            input_complete = False
        else:
            input_total += int(usage["input_tokens"])
        if usage.get("output_tokens") is None:
            output_complete = False
        else:
            output_total += int(usage["output_tokens"])
        if usage.get("total_tokens") is None:
            total_complete = False
        else:
            total += int(usage["total_tokens"])

    return {
        "reported_requests": reported,
        "unreported_requests": unreported,
        "complete": unreported == 0 and input_complete and output_complete and total_complete,
        "input_tokens": input_total if input_complete and reported else None,
        "output_tokens": output_total if output_complete and reported else None,
        "total_tokens": total if total_complete and reported else None,
    }


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
