"""Original portrait admission outside the content-addressed exact protocols."""

import re
from typing import Any

from .replica_canvas import NATIVE_PIXEL_BUDGET

STYLE_NATIVE_POLICY = "style-native-aspect-pixel-budget-v1"


def style_native_budget_matches(expected: list[int], actual: tuple[int, int]) -> bool:
    """Admit complete native files for the style Skill's standard portrait canvases.

    These are fixed style presets, not arbitrary explicit resolution requests.
    Keep other sizes strict, and never admit previews or a changed aspect.
    """
    return (
        expected in ([1080, 1440], [1440, 1920])
        and min(actual) > 0
        and actual[0] * actual[1] >= NATIVE_PIXEL_BUDGET * 0.99
        and abs((actual[0] / actual[1]) / (expected[0] / expected[1]) - 1) <= 0.01
    )


def follows_native_square(context: dict[str, Any], expected: list[int]) -> bool:
    references = context.get("references") or []
    return (
        expected == [1200, 1200]
        and isinstance(references, list)
        and bool(references)
        and all(
            isinstance(ref, dict) and ref.get("aspect_ratio") in ("follow-reference", "1:1")
            for ref in references
        )
        and not re.search(
            r"(?<!\d)1200(?!\d)|\d{3,5}\s*(?:[x×*]\s*\d{3,5}|px|像素)",
            str(context.get("user_facts", "")), re.IGNORECASE,
        )
    )
