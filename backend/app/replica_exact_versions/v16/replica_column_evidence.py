"""Unbiased column metadata and source-pixel coordinates for screen observers."""

import hashlib
import math
from pathlib import Path
from statistics import median
from typing import Any, cast

from PIL import Image, ImageDraw

POLICY = "source-column-coordinate-evidence-v1"
GUTTER = 80


def blank_bands(image: Image.Image) -> list[dict[str, Any]]:
    """Advisory whitespace coordinates, never a screen-count determination."""
    width, height = image.size
    border = round(width * 0.04)
    strip = image.convert("RGB").crop((border, 0, width - border, height))
    strip = strip.resize((min(256, strip.width), height), Image.Resampling.BOX)
    pixels = strip.load()
    assert pixels is not None
    minimum = max(8, math.ceil(height * 0.01))
    result, start = [], None
    for y in range(height + 1):
        flat = False
        if y < height:
            row = [cast(tuple[int, int, int], pixels[x, y]) for x in range(strip.width)]
            background = [median(value[c] for value in row) for c in range(3)]
            foreground = sum(
                max(abs(value[c] - background[c]) for c in range(3)) > 18
                for value in row
            )
            flat = min(background) >= 228 and foreground <= strip.width * 0.01
        if flat and start is None:
            start = y
        elif not flat and start is not None:
            if y - start >= minimum:
                result.append(
                    {
                        "top_y": start,
                        "bottom_y": y - 1,
                        "midpoint_y": round((start + y - 1) / 2),
                    }
                )
            start = None
    return result


def add_evidence(column: dict[str, Any], directory: Path) -> dict[str, Any]:
    source = Path(column["crop_path"])
    raw = source.read_bytes()
    with Image.open(source) as image:
        image.load()
        if image.size != (column["width"], column["height"]):
            raise ValueError("Source column dimensions do not match its frozen pixels")
        pixels = image.convert("RGBA")
        guide = Image.new("RGBA", (pixels.width + GUTTER, pixels.height), "white")
        guide.paste(pixels, (GUTTER, 0))
        draw = ImageDraw.Draw(guide)
        draw.line((GUTTER - 1, 0, GUTTER - 1, pixels.height - 1), fill="#263746")
        step = max(20, math.ceil(pixels.height / 20 / 10) * 10)
        for y in range(0, pixels.height, step):
            draw.line((GUTTER - 16, y, GUTTER - 1, y), fill="#263746")
            draw.text(
                (4, min(max(0, y - 6), pixels.height - 12)), str(y), fill="#263746"
            )
        # The guide changes no original source pixel or y coordinate.
        restored = guide.crop((GUTTER, 0, guide.width, guide.height))
        assert restored.tobytes() == pixels.tobytes()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"coordinates-{column['column_id']}.png"
        guide.save(path, "PNG")
        evidence = {
            "policy": POLICY,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_rgba_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
            "guide_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_content_box": [GUTTER, 0, guide.width, guide.height],
            "axis": "source_column_pixel_y; unchanged height; gutter is not content",
            "blank_bands": blank_bands(image),
            "blank_bands_are_not_screens_or_count_hints": True,
            "source_pixels_modified": False,
        }
    assert source.read_bytes() == raw
    return {
        **column,
        "coordinate_guide_path": str(path),
        "coordinate_evidence": evidence,
    }


def model_column(column: dict[str, Any]) -> dict[str, Any]:
    # Intake detects outer containers, not screen counts. Its free-text rationale
    # can contain a mistaken screen verdict and must not prime either observer.
    result = {
        key: column[key]
        for key in (
            "column_id",
            "reference_index",
            "column_index",
            "width",
            "height",
        )
    }
    if "coordinate_evidence" in column:
        result["coordinate_evidence"] = {
            key: column["coordinate_evidence"][key]
            for key in (
                "policy",
                "source_content_box",
                "axis",
                "blank_bands",
                "source_pixels_modified",
            )
        }
        result["coordinate_evidence"]["advisory_geometry_only"] = True
    return result


def visual_path(column: dict[str, Any]) -> Path:
    return Path(column.get("coordinate_guide_path", column["crop_path"]))
