"""Native pixel-budget admission for direct delivery, without resizing artwork.

Exact planning derives a 1024px short edge from each source's aspect. Relays
also return native canvases around 1024*1536 pixels; their short edge varies
with that aspect. A complete native canvas must not be treated as a preview
solely because a portrait is taller than 3:2. The original planning canvas and
strict invocation verdict remain evidence; delivery records the actual size.
"""

from typing import Any

POLICY = "native-aspect-pixel-budget-v1"
NATIVE_PIXEL_BUDGET = 1024 * 1536


def native_budget_matches(expected: list[int], actual: tuple[int, int]) -> bool:
    """Only admit server-derived 1024px plans at full native pixel density.

    The existing strict contract still handles all smaller canvases. This
    boundary does not authorize replacing an explicitly larger resolution,
    a changed aspect, or a low resolution preview with a smaller image.
    """
    return (
        min(expected) == 1024
        and expected[0] * expected[1] > NATIVE_PIXEL_BUDGET
        and min(actual) > 0
        and actual[0] * actual[1] >= NATIVE_PIXEL_BUDGET * 0.99
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
