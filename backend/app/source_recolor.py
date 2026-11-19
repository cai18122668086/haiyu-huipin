"""Source-only silver recoloring for explicitly selected gold metal hardware.

Geometry, printing and every pixel outside the bounded metal color mask remain
the photographed original. Other color/material requests use the normal editor.
"""

from __future__ import annotations

import hashlib
import math
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps

from .source_pixels import artifact

VERSION = "source-gold-metal-to-silver-v2-protected-print"
PROFILE = "image_fidelity_color_mask"


class MetalSelectionError(ValueError):
    def __init__(self, violations: list[dict[str, Any]]) -> None:
        super().__init__("Metal mask intersects protected printing or contains no gold")
        self.violations = violations


def enabled(context: dict[str, Any], sources: tuple[Any, ...]) -> bool:
    settings = context["settings"]
    target = str(settings.get("target_color", "")).lower()
    instruction = str(settings.get("instruction", "")).lower()
    return (
        context["job_type"] == "product_image"
        and settings.get("task_type") == "change_color"
        and ("银色" in target or "silver" in target)
        and any(
            w in instruction for w in ("金属", "五金", "metal", "hardware", "clasp")
        )
        and len([s for s in sources if s.source_role == "product_reference"]) == 1
    )


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["pixel_inspected", "regions", "unresolved"],
        "properties": {
            "pixel_inspected": {"type": "boolean"},
            "unresolved": {"type": "array", "items": {"type": "string"}},
            "regions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 48,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["part", "box"],
                    "properties": {
                        "part": {"enum": ["metal_clasp", "metal_ring", "metal_link"]},
                        "box": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                    },
                },
            },
        },
    }


def render(source: Any, recipe: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    from jsonschema import Draft202012Validator

    Draft202012Validator(schema()).validate(recipe["selection"])
    if (
        recipe["version"] != VERSION
        or recipe["source_sha256"] != source.sha256
        or hashlib.sha256(source.payload).hexdigest() != source.sha256
        or recipe["source_asset_id"] != source.asset_id
    ):
        raise ValueError("Original metal recolor authority changed")
    selection = recipe["selection"]
    if selection["pixel_inspected"] is not True or selection["unresolved"]:
        raise ValueError("Metal selection has unresolved evidence")
    with Image.open(BytesIO(source.payload)) as opened:
        original = ImageOps.exif_transpose(opened).convert("RGB")
    pixels: Any = original.load()
    hsv: Any = original.convert("HSV").load()
    output = original.copy()
    edited: Any = output.load()
    print_boxes = recipe.get("protected_print_boxes", [])
    if len(print_boxes) > 72:
        raise ValueError("Protected printing exceeds its bounded source inventory")
    protected = []
    for box in print_boxes:
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Protected printing has invalid source geometry")
        protected.append((
            math.floor(x0 * original.width), math.floor(y0 * original.height),
            math.ceil(x1 * original.width), math.ceil(y1 * original.height),
        ))
    changed: set[tuple[int, int]] = set()
    violations = []
    for index, region in enumerate(selection["regions"]):
        x0, y0, x1, y1 = region["box"]
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Invalid metal component geometry")
        left, top, right, bottom = (
            math.floor(x0 * original.width),
            math.floor(y0 * original.height),
            math.ceil(x1 * original.width),
            math.ceil(y1 * original.height),
        )
        if (right - left) * (bottom - top) > original.width * original.height * 0.04:
            raise ValueError("Metal component selection is too broad")
        found = False
        blocked = set()
        for y in range(top, bottom):
            for x in range(left, right):
                hue, saturation, value = hsv[x, y]
                # Warm gold only. Neutral highlights/background stay original.
                if not (10 <= hue <= 50 and saturation >= 55 and value >= 30):
                    continue
                print_hits = [
                    i
                    for i, (pl, pt, pr, pb) in enumerate(protected)
                    if pl <= x < pr and pt <= y < pb
                ]
                if print_hits:
                    blocked.update(print_hits)
                    continue
                red, green, blue = pixels[x, y]
                luminance = round(red * 0.2126 + green * 0.7152 + blue * 0.0722)
                edited[x, y] = (luminance, luminance, luminance)
                changed.add((x, y))
                found = True
        if blocked or not found:
            violations.append({
                "region_index": index,
                "part": region["part"],
                "box": region["box"],
                "reason": "protected_print" if blocked else "no_gold",
                "intersected_print_boxes": [print_boxes[i] for i in sorted(blocked)],
            })
    if violations:
        raise MetalSelectionError(violations)
    if not changed or len(changed) > original.width * original.height * 0.08:
        raise ValueError("Metal recoloring exceeds its source pixel envelope")
    for y in range(original.height):
        for x in range(original.width):
            if (x, y) not in changed and edited[x, y] != pixels[x, y]:
                raise ValueError("Recoloring changed protected source pixels")
    result = artifact(output, "source-metal-silver.png")
    return result, {
        "version": VERSION,
        "source_sha256": source.sha256,
        "result_sha256": result.sha256,
        "method": "source_luminance_silver_in_gold_mask",
        "changed_pixels": len(changed),
        "outside_mask_changed_pixels": 0,
        "dimensions": [original.width, original.height],
        "image_requests": 0,
    }
