"""Aspect-based native delivery, retaining the supplier's original pixels."""

from typing import Any

POLICY = "native-aspect-minimum-edge-v2"
LEGACY_POLICY = "native-aspect-pixel-budget-v1"
NATIVE_PIXEL_BUDGET = 1024 * 1536
MIN_NATIVE_EDGE = 512


def native_budget_matches(expected: list[int], actual: tuple[int, int]) -> bool:
    """Requested pixels are a target; aspect and a usable native edge govern.

    A 1% relative ratio tolerance accommodates integer rounding. The 512px
    floor rejects small previews, independently of a larger planned canvas.
    Small synthetic/legacy canvases retain their existing smaller floor.
    """
    return (
        len(expected) == len(actual) == 2
        and min(expected) > 0
        and min(actual) >= min(MIN_NATIVE_EDGE, min(expected))
        and abs((actual[0] / actual[1]) / (expected[0] / expected[1]) - 1) <= 0.01
    )


def delivery_receipt(
    expected: list[int], actual: tuple[int, int], sha256: str
) -> dict[str, Any]:
    return {
        "policy": POLICY,
        "requested_size": list(expected),
        "actual_size": list(actual),
        "sha256": sha256,
        "provider_bytes_preserved": True,
        "locally_resampled": False,
    }


def receipt_matches(
    receipt: Any, expected: list[int], actual: tuple[int, int], sha256: str
) -> bool:
    """Validate old receipts without rewriting their historical policy claim."""
    if not isinstance(receipt, dict) or receipt.get("policy") not in {
        POLICY,
        LEGACY_POLICY,
    }:
        return False
    return {**receipt, "policy": POLICY} == delivery_receipt(expected, actual, sha256)
