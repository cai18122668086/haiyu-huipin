"""Render literal approved copy without changing native canvas or product pixels."""

from __future__ import annotations

import hashlib
import math
import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .source_pixels import artifact

VERSION = "approved-native-copy-v2-punctuation-safe"
FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
FONT_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/"
    "f8d157532fbfaeda587e826d4cd5b21a49186f7c/"
    "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
)
FONT_PATH = Path("/app/fonts/NotoSansCJKsc-Regular.otf")


def layout_schema(panels: int) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 32,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["copy_index", "panel", "panel_box", "role", "color", "align"],
            "properties": {
                "copy_index": {"type": "integer", "minimum": 0, "maximum": 31},
                "panel": {"type": "integer", "enum": list(range(panels))},
                "panel_box": {
                    "type": "array",
                    "description": (
                        "Panel-relative [left, top, right, bottom] corners. "
                        "Right must exceed left and bottom must exceed top. "
                        "For a box at (.52,.10) of size (.42,.16), send "
                        "[.52,.10,.94,.26]."
                    ),
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {"type": "number", "minimum": 0.04, "maximum": 0.96},
                },
                "role": {"enum": ["heading", "body", "caption"]},
                "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                "align": {"enum": ["left", "center", "right"]},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 6},
            },
        },
    }


def _overlap(one: list[float], two: list[float]) -> bool:
    return min(one[2], two[2]) > max(one[0], two[0]) and (
        min(one[3], two[3]) > max(one[1], two[1])
    )


def place_text_without_collisions(
    layout: list[dict[str, Any]], cells: list[list[float]], panels: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate a text block locally; preserve size, order, panel and styling.

    This is bounded renderer layout, not a relaxation of collision or type
    checks. The caller still validates every resulting block and its copy.
    """
    from jsonschema import Draft202012Validator

    Draft202012Validator(layout_schema(panels)).validate(layout)
    original, placed = deepcopy(layout), deepcopy(layout)

    def box(row: dict[str, Any]) -> list[float]:
        x0, y0, x1, y1 = row["panel_box"]
        return [x0, (row["panel"] + y0) / panels, x1, (row["panel"] + y1) / panels]

    original_boxes = [box(row) for row in original]
    changes = []
    for index in sorted(range(len(placed)), key=lambda i: original_boxes[i][1]):
        row = placed[index]
        others = [box(other) for i, other in enumerate(placed) if i != index]
        obstacles = cells + others
        if not any(_overlap(box(row), other) for other in obstacles):
            continue
        x0, y0, x1, y1 = row["panel_box"]
        height = y1 - y0
        candidates = {y0}
        for obstacle in obstacles:
            candidates.add(obstacle[1] * panels - row["panel"] - height)
            candidates.add(obstacle[3] * panels - row["panel"])
        chosen = None
        for top in sorted(candidates, key=lambda y: (abs(y - y0), y)):
            top, bottom = round(top, 12), round(top + height, 12)
            if not (0.04 <= top < bottom <= 0.96) or abs(top - y0) > 0.08:
                continue
            trial = {**row, "panel_box": [x0, top, x1, bottom]}
            actual = box(trial)
            if any(_overlap(actual, obstacle) for obstacle in obstacles):
                continue
            ordered = True
            for other_index, other in enumerate(placed):
                if other_index == index or other["panel"] != row["panel"]:
                    continue
                old = original_boxes[other_index]
                if min(x1, old[2]) <= max(x0, old[0]):
                    continue
                before = sum(original_boxes[index][1::2]) - sum(old[1::2])
                after = sum(actual[1::2]) - sum(box(other)[1::2])
                if before * after <= 0:
                    ordered = False
                    break
            if ordered:
                chosen = trial
                break
        if chosen is None:
            return original, []
        placed[index] = chosen
        changes.append(
            {
                "layout_index": index,
                "copy_index": row["copy_index"],
                "panel": row["panel"],
                "before": row["panel_box"],
                "after": chosen["panel_box"],
            }
        )
    return placed, changes


def recipe(
    copy: list[str],
    layout: list[dict[str, Any]],
    cells: list[list[float]],
    panels: int,
    font_path: Path,
    size: tuple[int, int],
) -> dict[str, Any]:
    from jsonschema import Draft202012Validator

    if panels not in (1, 2) or not 0 <= len(copy) <= 32:
        raise ValueError("Invalid approved typography scope")
    Draft202012Validator(layout_schema(panels)).validate(layout)
    if sorted(row["copy_index"] for row in layout) != list(range(len(copy))):
        raise ValueError("Every approved copy string needs exactly one text block")
    if copy and hashlib.sha256(font_path.read_bytes()).hexdigest() != FONT_SHA256:
        raise ValueError("Approved source font integrity changed")
    blocks: list[dict[str, Any]] = []
    for layout_index, row in enumerate(layout):
        x0, y0, x1, y1 = row["panel_box"]
        if not (x0 < x1 and y0 < y1) or any(
            not math.isfinite(v) for v in row["panel_box"]
        ):
            raise ValueError(
                f"Invalid text placement at text_layout[{layout_index}]: "
                f"panel_box={row['panel_box']} must use increasing "
                "[left, top, right, bottom] corners."
            )
        box = [x0, (row["panel"] + y0) / panels, x1, (row["panel"] + y1) / panels]
        product_collisions = [
            index for index, other in enumerate(cells) if _overlap(box, other)
        ]
        text_collisions = [
            block["copy_index"] for block in blocks if _overlap(box, block["box"])
        ]
        if product_collisions or text_collisions:
            raise ValueError(
                f"text_layout[{layout_index}] copy_index={row['copy_index']} "
                f"panel={row['panel']} panel_box={row['panel_box']} overlaps "
                f"placements indices={product_collisions} or prior text "
                f"copy_indices={text_collisions}; move the colliding boxes apart"
            )
        text = copy[row["copy_index"]]
        if not isinstance(text, str) or not text.strip() or len(text) > 800:
            raise ValueError("Approved text is empty or exceeds its bounded block")
        block = {**row, "text": text, "box": box}
        _fit(block, size, panels, font_path)
        blocks.append(block)
    return {
        "version": VERSION,
        "font_sha256": FONT_SHA256,
        "panels": panels,
        "blocks": blocks,
    }


def _lines(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        # Preserve English words; Chinese/Japanese wrap at glyph boundaries.
        tokens = re.findall(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*|\s+|.", paragraph)
        grouped: list[str] = []
        for token in tokens:
            # A period on a line of its own looks like an added bullet. Keep
            # closing punctuation with its actual word/glyph; never delete it
            # or permit extra lines/smaller-than-approved type to make it fit.
            if (
                token in ".,!?;:%。、」』】〕〉》）］｝，！？；：…’”\"')]}"
                and grouped
                and not grouped[-1].isspace()
            ):
                grouped[-1] += token
            else:
                grouped.append(token)
        for token in grouped:
            proposed = line + token
            if font.getlength(proposed) > width and line.strip():
                lines.append(line.rstrip())
                line = token.lstrip()
            else:
                line = proposed
            if font.getlength(line) > width:
                raise ValueError("An approved word cannot fit the text block")
        lines.append(line.rstrip())
    if "".join("".join(lines).split()) != "".join(text.split()):
        raise ValueError("Text wrapping changed approved characters")
    return lines


def _fit(
    block: dict[str, Any],
    size: tuple[int, int],
    panels: int,
    font_path: Path,
) -> tuple[ImageFont.FreeTypeFont, list[str], int, int, tuple[int, int, int, int]]:
    x0, y0, x1, y1 = block["box"]
    left, top, right, bottom = (
        math.ceil(x0 * size[0]),
        math.ceil(y0 * size[1]),
        math.floor(x1 * size[0]),
        math.floor(y1 * size[1]),
    )
    width, height = right - left, bottom - top
    minimum = max(12, round(size[0] * 0.011))
    maximum = max(
        minimum,
        round(
            (size[1] / panels)
            * {"heading": 0.08, "body": 0.045, "caption": 0.03}[block["role"]]
        ),
    )
    for pixels in range(maximum, minimum - 1, -1):
        font = ImageFont.truetype(str(font_path), pixels)
        try:
            lines = _lines(block["text"], font, width)
        except ValueError:
            continue
        if len(lines) > block.get("max_lines", 1 if block["role"] == "heading" else 4):
            continue
        bounds = [font.getbbox(line or " ") for line in lines]
        line_height = math.ceil(max(b[3] - b[1] for b in bounds))
        spacing = max(3, round(pixels * 0.24))
        if line_height * len(lines) + spacing * (len(lines) - 1) <= height:
            missing = bytes(font.getmask(chr(0x10FFFF)))
            if any(
                bytes(font.getmask(c)) == missing
                for c in block["text"]
                if not c.isspace()
            ):
                raise ValueError(
                    "Approved text contains a glyph absent from the pinned font"
                )
            return font, lines, line_height, spacing, (left, top, right, bottom)
    raise ValueError("Approved copy cannot fit at a readable font size")


def render(
    canvas: Any,
    value: dict[str, Any],
    font_path: Path = FONT_PATH,
) -> tuple[Any, dict[str, Any]]:
    if value["version"] != VERSION or value["font_sha256"] != FONT_SHA256:
        raise ValueError("Approved text recipe changed")
    if hashlib.sha256(canvas.payload).hexdigest() != canvas.sha256:
        raise ValueError("Canvas changed before approved text rendering")
    if (
        value["blocks"]
        and hashlib.sha256(font_path.read_bytes()).hexdigest() != FONT_SHA256
    ):
        raise ValueError("Approved source font integrity changed")
    with Image.open(BytesIO(canvas.payload)) as opened:
        image = opened.convert("RGBA")
    if image.size != (canvas.width, canvas.height):
        raise ValueError("Typography cannot repair canvas dimensions")
    proof = []
    for block in value["blocks"]:
        font, lines, line_height, spacing, box = _fit(
            block, image.size, value["panels"], font_path
        )
        left, top, right, bottom = box
        tile = Image.new("RGBA", (right - left, bottom - top))
        draw = ImageDraw.Draw(tile)
        y = 0
        for line in lines:
            length = font.getlength(line)
            x = (
                0
                if block["align"] == "left"
                else right - left - length
                if block["align"] == "right"
                else (right - left - length) / 2
            )
            bounds = font.getbbox(line or " ")
            draw.text((x, y - bounds[1]), line, font=font, fill=block["color"])
            y += line_height + spacing
        image.alpha_composite(tile, (left, top))
        proof.append(
            {
                "copy_sha256": hashlib.sha256(block["text"].encode()).hexdigest(),
                "box": list(box),
                "font_size": font.size,
            }
        )
    result = artifact(image, "source-products-approved-copy.png")
    return result, {
        "version": VERSION,
        "font_sha256": FONT_SHA256,
        "input_sha256": canvas.sha256,
        "result_sha256": result.sha256,
        "blocks": proof,
        "dimensions": [result.width, result.height],
    }
