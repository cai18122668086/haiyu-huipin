"""Independent OCR evidence from original-coordinate printed surfaces only."""

from __future__ import annotations

import hashlib
import math
from io import BytesIO
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from .provider import ProviderArtifact
from .source_pixels import artifact

REGIONS_SCHEMA = {
    "type": "array",
    "maxItems": 12,
    "items": {
        "type": "array", "minItems": 8, "maxItems": 8,
        "items": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "description": (
        "One source-photo quadrilateral per complete visible printed SURFACE "
        "(such as the entire colored hanging tag), containing ALL its columns, "
        "secondary marks and copyright. Do not split adjacent printed columns "
        "into competing crops. Each has 8 numbers "
        "x0,y0,x1,y1,x2,y2,x3,y3 in TL,TR,BR,BL order around the actual angled "
        "complete printed surface, including every edge glyph. No transcription. "
        "Convex polygons for distinct printed surfaces must be disjoint "
        "and entirely inside this product's box; their bounding boxes may overlap."
    ),
}


class PrintGeometryError(ValueError):
    def __init__(self, rule: str, index: int) -> None:
        super().__init__(rule)
        self.rule, self.index = rule, index


def validate(region: dict[str, Any]) -> None:
    x0, y0, x1, y1 = region["box"]
    polygons: list[list[tuple[float, float]]] = []
    for index, quad in enumerate(region.get("print_regions", [])):
        if len(quad) != 8 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in quad
        ):
            raise PrintGeometryError(
                "Physical print region needs four finite corners", index
            )
        points = list(zip(quad[::2], quad[1::2], strict=True))
        crosses = []
        for i, (x, y) in enumerate(points):
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                raise PrintGeometryError(
                    "Physical print region leaves its product", index
                )
            ax, ay = points[(i + 1) % 4]
            bx, by = points[(i + 2) % 4]
            crosses.append((ax - x) * (by - ay) - (ay - y) * (bx - ax))
        if not (
            all(v > 1e-10 for v in crosses) or all(v < -1e-10 for v in crosses)
        ):
            raise PrintGeometryError(
                "Physical print region must be a convex quad", index
            )
        for old in polygons:
            separated = False
            for polygon in (points, old):
                for i, (x, y) in enumerate(polygon):
                    nx, ny = polygon[(i + 1) % 4]
                    dx, dy = -(ny - y), nx - x
                    a = [px * dx + py * dy for px, py in points]
                    b = [px * dx + py * dy for px, py in old]
                    if max(a) <= min(b) + 1e-10 or max(b) <= min(a) + 1e-10:
                        separated = True
            if not separated:
                raise PrintGeometryError(
                    "Physical print regions must be disjoint without duplicate glyphs",
                    index,
                )
        polygons.append(points)


def crops(
    sources: tuple[Any, ...], preparation: dict[str, Any]
) -> tuple[ProviderArtifact, ...]:
    """One sheet per product, containing only real nearest-neighbor crops.

    No transcription, source counterpart, generated plan, labels or answer key
    is painted into the sheet. Source and candidate use identical geometry.
    This evidence never replaces the full candidate or delivered image.
    """
    by_id = {s.asset_id: s for s in sources}
    result = []
    for region in preparation["selected_regions"]:
        validate(region)
        boxes = region.get("print_regions", [])
        if not boxes:
            if region["visible_print"]:
                raise ValueError("Physical printing has no complete crop geometry")
            continue
        source = by_id[region["asset_id"]]
        if hashlib.sha256(source.payload).hexdigest() != source.sha256:
            raise ValueError("Physical print evidence source bytes changed")
        with Image.open(BytesIO(source.payload)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        pieces = []
        for quad in boxes:
            from PIL import ImageDraw

            # Evidence-only original context protects curved baselines and
            # copyright edge glyphs from approximate localization boundaries.
            # It is real source RGB, never synthesized lettering or delivery padding.
            padding = 8
            left = max(0, math.floor(min(quad[::2]) * image.width) - padding)
            top = max(0, math.floor(min(quad[1::2]) * image.height) - padding)
            right = min(image.width, math.ceil(max(quad[::2]) * image.width) + padding)
            bottom = min(
                image.height, math.ceil(max(quad[1::2]) * image.height) + padding
            )
            piece = image.crop((left, top, right, bottom))
            mask = Image.new("L", piece.size)
            ImageDraw.Draw(mask).polygon([
                (round(x * image.width) - left, round(y * image.height) - top)
                for x, y in zip(quad[::2], quad[1::2], strict=True)
            ], fill=255)
            mask = mask.filter(ImageFilter.MaxFilter(padding * 2 + 1))
            piece = Image.composite(
                piece, Image.new("RGB", piece.size, "#eeeeee"), mask
            )
            if min(piece.size) < 2:
                raise ValueError("Physical print evidence is too small to inspect")
            factor = min(8, max(1, 1024 // max(piece.size)))
            pieces.append(piece.resize(
                (piece.width * factor, piece.height * factor),
                Image.Resampling.NEAREST,
            ))
        columns = math.ceil(math.sqrt(len(pieces)))
        rows = math.ceil(len(pieces) / columns)
        width = max(p.width for p in pieces) + 16
        height = max(p.height for p in pieces) + 16
        if columns * width * rows * height > 4_000_000:
            raise ValueError("Physical print evidence exceeds its bounded pixel budget")
        sheet = Image.new("RGB", (columns * width, rows * height), "#eeeeee")
        for i, piece in enumerate(pieces):
            sheet.paste(piece, ((i % columns) * width + 8, (i // columns) * height + 8))
        result.append(artifact(sheet, "physical-print-evidence.png"))
    return tuple(result)
