"""Compile colliding proposals; never change source authority or native gates."""

from copy import deepcopy

import pytest

from app import source_layout, source_pixels


def proposal(panel=0):
    return [
        {"layer_index": 0, "panel": panel, "panel_box": [0.07, 0.255, 0.29, 0.79]},
        {"layer_index": 1, "panel": panel, "panel_box": [0.345, 0.255, 0.565, 0.79]},
        {"layer_index": 2, "panel": panel, "panel_box": [0.62, 0.255, 0.86, 0.79]},
        {
            "layer_index": 0,
            "panel": panel,
            "panel_box": [0.72, 0.675, 0.94, 0.94],
            "source_window": [0.29, 0.0, 0.72, 0.24],
        },
    ]


@pytest.mark.parametrize("panels,panel", [(1, 0), (2, 1)])
def test_repeated_closeup_can_be_disjoint_with_bounded_uniform_product_layout(
    panels,
    panel,
):
    rows = proposal(panel)
    if panels == 2:
        rows.insert(
            0, {"layer_index": 1, "panel": 0, "panel_box": [0.2, 0.3, 0.7, 0.9]}
        )
    text = [
        {"panel": panel, "panel_box": [0.07, 0.055, 0.72, 0.145]},
        {"panel": panel, "panel_box": [0.07, 0.155, 0.55, 0.205]},
    ]
    before, text_before = deepcopy(rows), deepcopy(text)
    with pytest.raises(ValueError, match="overlaps"):
        source_pixels.native_placements(rows, 3, panels)
    fixed, changes = source_layout.place_products_without_collisions(rows, text, panels)
    indices, _ = source_pixels.native_placements(fixed, 3, panels)
    assert indices == [r["layer_index"] for r in before]
    assert rows == before and text == text_before and changes
    assert fixed[-1] == before[-1]  # Original authorized close-up is preserved.
    for old, new in zip(before, fixed, strict=True):
        assert {k: v for k, v in old.items() if k != "panel_box"} == {
            k: v for k, v in new.items() if k != "panel_box"
        }
        a, b = old["panel_box"], new["panel_box"]
        sx, sy = (b[2] - b[0]) / (a[2] - a[0]), (b[3] - b[1]) / (a[3] - a[1])
        assert 0.9 - 1e-10 <= sx <= 1 + 1e-10 and abs(sx - sy) < 1e-10
        assert (
            max(abs((a[i] + a[i + 2] - b[i] - b[i + 2]) / 2) for i in (0, 1))
            <= 0.1 + 1e-10
        )
        for block in text:
            c = block["panel_box"]
            if block["panel"] == new["panel"]:
                assert min(b[2], c[2]) <= max(b[0], c[0]) or min(b[3], c[3]) <= max(
                    b[1], c[1]
                )
    assert source_layout.place_products_without_collisions(rows, text, panels) == (
        fixed,
        changes,
    )


def test_already_valid_layout_and_text_only_collisions_are_not_reinterpreted():
    rows = proposal()[:3]
    text = [{"panel": 0, "panel_box": [0.1, 0.3, 0.8, 0.8]}]
    assert source_layout.place_products_without_collisions(rows, text, 1) == (rows, [])


@pytest.mark.parametrize("invalid", ["full_overlap", "seam", "nan", "missing_panel"])
def test_unsolvable_or_invalid_proposal_is_still_rejected(invalid):
    rows = [{"layer_index": 0, "panel": 0, "panel_box": [0.04, 0.04, 0.96, 0.96]}] * 2
    rows = deepcopy(rows)
    panels = 1
    if invalid == "seam":
        rows[0]["panel_box"][0] = 0
    elif invalid == "nan":
        rows[0]["panel_box"][0] = float("nan")
    elif invalid == "missing_panel":
        panels = 2
    fixed, changes = source_layout.place_products_without_collisions(rows, [], panels)
    assert not changes
    with pytest.raises(ValueError):
        source_pixels.native_placements(fixed, 1, panels)
