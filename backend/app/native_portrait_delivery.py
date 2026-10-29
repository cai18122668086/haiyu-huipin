"""Original portrait admission outside the content-addressed exact protocols."""

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
