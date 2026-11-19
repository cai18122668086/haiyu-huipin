import hashlib
import math
from io import BytesIO

import pytest
from jsonschema import ValidationError
from PIL import Image, ImageFont

from app import source_pixels, source_typography


@pytest.fixture
def font(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-font-fixture"
    path.write_bytes(b"unit-layout-only")
    monkeypatch.setattr(
        source_typography, "FONT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    raster_font = ImageFont.load_default(size=24)
    monkeypatch.setattr(
        source_typography.ImageFont, "truetype", lambda *args: raster_font
    )
    return path


def block(index=0):
    return {
        "copy_index": index,
        "panel": 0,
        "panel_box": [0.1, 0.05, 0.9, 0.25],
        "role": "heading",
        "color": "#334155",
        "align": "center",
    }


def test_approved_copy_renders_on_same_canvas_without_touching_product_pixels(font):
    base = Image.new("RGB", (400, 400), "white")
    for y in range(160, 360):
        for x in range(80, 320):
            base.putpixel((x, y), (180, 20, 60))
    original = source_pixels.artifact(base, "native.png")
    recipe = source_typography.recipe(
        ["APPROVED COPY"], [block()], [[0.2, 0.4, 0.8, 0.9]], 1, font, (400, 400)
    )
    result, proof = source_typography.render(original, recipe, font)
    assert result.width == result.height == 400
    assert (
        proof["blocks"][0]["copy_sha256"]
        == hashlib.sha256(b"APPROVED COPY").hexdigest()
    )
    with Image.open(BytesIO(result.payload)) as actual:
        assert (
            actual.convert("RGB").crop((0, 120, 400, 400)).tobytes()
            == base.crop((0, 120, 400, 400)).tobytes()
        )
        assert (
            actual.convert("RGB").crop((40, 20, 360, 100)).tobytes()
            != base.crop((40, 20, 360, 100)).tobytes()
        )


@pytest.mark.parametrize(
    "kind", ["missing", "duplicate", "overlap", "product", "outside"]
)
def test_invalid_copy_layout_cannot_reach_paid_background_generation(font, kind):
    layout = [block()]
    copy = ["APPROVED COPY"]
    if kind == "missing":
        layout = []
    elif kind == "duplicate":
        layout.append(block())
    elif kind == "overlap":
        layout.append(block(1))
        copy.append("SECOND COPY")
    elif kind == "product":
        layout[0]["panel_box"] = [0.2, 0.45, 0.8, 0.8]
    else:
        layout[0]["panel_box"] = [0, 0.1, 1, 0.3]
    with pytest.raises((ValidationError, ValueError)):
        source_typography.recipe(
            copy, layout, [[0.2, 0.4, 0.8, 0.9]], 1, font, (400, 400)
        )


def test_empty_approved_copy_needs_no_font_or_text_blocks(tmp_path):
    result = source_typography.recipe(
        [], [], [[0.1, 0.1, 0.9, 0.9]], 1, tmp_path / "absent", (400, 400)
    )
    assert result["blocks"] == []


@pytest.mark.parametrize("copy", ["Spot your choice faster.", "Select the original!"])
def test_wrapping_cannot_turn_approved_terminal_punctuation_into_a_bullet(copy):
    raster = ImageFont.load_default(size=24)
    width = math.ceil(raster.getlength(copy[:-1]))
    lines = source_typography._lines(copy, raster, width)
    assert len(lines) >= 2
    assert all(line.strip() not in {".", "!"} for line in lines)
    assert lines[-1].endswith(copy.split()[-1])
    assert "".join("".join(lines).split()) == "".join(copy.split())


def test_pair_layout_feedback_identifies_collision_without_copy_values(font):
    heading = {**block(), "panel": 1, "panel_box": [0.07, 0.16, 0.61, 0.24]}
    cells = [[0.07, 0.145, 0.31, 0.42], [0.53, 0.61, 0.92, 0.98]]
    with pytest.raises(ValueError) as error:
        source_typography.recipe(
            ["PRIVATE APPROVED COPY"], [heading], cells, 2, font, (1440, 3840)
        )
    diagnostic = str(error.value)
    assert "text_layout[0] copy_index=0 panel=1" in diagnostic
    assert "placements indices=[1]" in diagnostic
    assert "PRIVATE APPROVED COPY" not in diagnostic
    assert len(diagnostic) <= 240


@pytest.mark.parametrize("panels,panel", [(1, 0), (2, 1)])
def test_small_collision_translation_preserves_copy_order_and_exact_type_boxes(
    font, panels, panel
):
    from copy import deepcopy

    layout = [
        {**block(0), "panel": panel, "panel_box": [0.06, 0.06, 0.43, 0.2]},
        {**block(1), "panel": panel, "panel_box": [0.06, 0.22, 0.43, 0.29]},
    ]
    cells = [[0.39, (panel + 0.27) / panels, 0.61, (panel + 0.95) / panels]]
    before = deepcopy(layout)
    corrected, changes = source_typography.place_text_without_collisions(
        layout, cells, panels
    )
    assert layout == before
    assert corrected[0] == before[0]
    assert corrected[1]["panel_box"] == [0.06, 0.2, 0.43, 0.27]
    assert len(changes) == 1 and changes[0]["copy_index"] == 1
    for key, value in before[1].items():
        if key != "panel_box":
            assert corrected[1][key] == value
    source_typography.recipe(
        ["TITLE", "BODY"], corrected, cells, panels, font, (1440, 3840)
    )


def test_unrepairable_layout_remains_rejected_without_shrinking_or_reordering(font):
    layout = [block()]
    cells = [[0.04, 0.04, 0.96, 0.96]]
    corrected, changes = source_typography.place_text_without_collisions(
        layout, cells, 1
    )
    assert corrected == layout and changes == []
    with pytest.raises(ValueError, match="overlaps"):
        source_typography.recipe(["TITLE"], corrected, cells, 1, font, (400, 400))
