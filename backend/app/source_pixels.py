"""Preserve photographed product pixels; generate backgrounds separately.

The matte predicts alpha only. It never synthesizes RGB, letters or artwork.
The original background canvas is retained separately for audit. Compositing
keeps its measured native dimensions; it never repairs an invalid canvas.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .provider import ProviderArtifact

VERSION = "source-pixel-layers-v1"
MODEL_SHA256 = "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
_lock = threading.Lock()
_net: Any = None
_loaded_path: Path | None = None


@dataclass(frozen=True)
class PixelLayer:
    artifact: ProviderArtifact
    source_asset_id: str
    source_sha256: str
    source_box: tuple[int, int, int, int]


def enabled_for(context: dict[str, Any]) -> bool:
    settings = context["settings"]
    if context["job_type"] == "product_image":
        return settings.get("task_type") in {
            "white_background",
            "change_background",
            "change_scene",
            "scene_main",
        }
    if context["job_type"] == "amazon_visual_suite":
        return True
    if context["job_type"] == "domestic_main_image":
        return settings.get("main_image_speed") == "native_fast"
    instruction = str(settings.get("instruction", "")).lower()
    return (
        context["job_type"] in {"creative_image", "reference_remix"}
        and ("背景" in instruction or "background" in instruction)
        and any(
            word in instruction
            for word in (
                "不改变",
                "保留",
                "保持",
                "preserve",
                "unchanged",
                "retain",
            )
        )
    )


def _alpha(crop: Image.Image, model_path: Path) -> Image.Image:
    global _net, _loaded_path
    cv = importlib.import_module("cv2")
    np = importlib.import_module("numpy")
    # One model/inference at a time per worker, including OpenCV's global RNG.
    with _lock:
        if _net is None or _loaded_path != model_path:
            if hashlib.sha256(model_path.read_bytes()).hexdigest() != MODEL_SHA256:
                raise ValueError("Source matte model integrity check failed")
            cv.setNumThreads(1)
            _net = cv.dnn.readNetFromONNX(str(model_path))
            _loaded_path = model_path
        rgb = np.array(crop.convert("RGB"))
        small = cv.resize(rgb, (320, 320)).astype(np.float32) / 255
        tensor = (
            (small - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
        ).transpose(2, 0, 1)[None]
        _net.setInput(tensor.astype(np.float32))
        prediction = _net.forward()[0, 0]
        minimum, maximum = float(prediction.min()), float(prediction.max())
        if not math.isfinite(maximum) or maximum - minimum < 0.01:
            raise ValueError("Source matte has no reliable foreground")
        confidence = cv.resize(
            ((prediction - minimum) * 255 / (maximum - minimum)).astype(np.uint8),
            crop.size,
        )
        labels = np.where(
            confidence < 128,
            cv.GC_BGD,
            np.where(confidence >= 230, cv.GC_FGD, cv.GC_PR_FGD),
        ).astype(np.uint8)
        if not np.any(labels == cv.GC_FGD) or not np.any(labels == cv.GC_BGD):
            raise ValueError("Source matte lacks foreground/background evidence")
        cv.setRNGSeed(0)
        cv.grabCut(
            cv.cvtColor(rgb, cv.COLOR_RGB2BGR),
            labels,
            None,
            np.zeros((1, 65), np.float64),
            np.zeros((1, 65), np.float64),
            3,
            cv.GC_INIT_WITH_MASK,
        )
        alpha = np.where(
            (labels == cv.GC_FGD) | (labels == cv.GC_PR_FGD),
            np.minimum(255, np.maximum(0, (confidence.astype(float) - 128) * 255 / 80)),
            0,
        ).astype(np.uint8)
        return Image.fromarray(alpha, "L")


def artifact(image: Image.Image, filename: str) -> ProviderArtifact:
    output = BytesIO()
    image.save(output, "PNG")
    payload = output.getvalue()
    return ProviderArtifact(
        payload=payload,
        filename=filename,
        content_type="image/png",
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def extract_layers(
    sources: tuple[Any, ...],
    preparation: dict[str, Any],
    model_path: Path,
) -> tuple[PixelLayer, ...]:
    regions = preparation["selected_regions"]
    if not 1 <= len(regions) <= 6:
        raise ValueError("Source layer count exceeds the selection contract")
    by_id = {source.asset_id: source for source in sources}
    layers = []
    for index, region in enumerate(regions):
        source = by_id[region["asset_id"]]
        if hashlib.sha256(source.payload).hexdigest() != source.sha256:
            raise ValueError("Source pixel authority changed")
        with Image.open(BytesIO(source.payload)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        x0, y0, x1, y1 = region["box"]
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Invalid source layer geometry")
        pad = max(
            12,
            round(
                max(
                    (x1 - x0) * image.width * 0.3,
                    (y1 - y0) * image.height * 0.1,
                )
            ),
        )
        left, top = (
            max(0, math.floor(x0 * image.width) - pad),
            max(0, math.floor(y0 * image.height) - pad),
        )
        right, bottom = (
            min(image.width, math.ceil(x1 * image.width) + pad),
            min(image.height, math.ceil(y1 * image.height) + pad),
        )
        crop = image.crop((left, top, right, bottom))
        if crop.width * crop.height > 8_000_000:
            raise ValueError("Source selection exceeds the bounded matte pixel budget")
        alpha = _alpha(crop, model_path)
        bounds = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
        if bounds is None or (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) < 64:
            raise ValueError("Source layer is empty or incomplete")
        # RGB is copied unchanged. Only alpha comes from segmentation.
        rgba = crop.convert("RGBA")
        rgba.putalpha(alpha)
        if rgba.convert("RGB").tobytes() != crop.tobytes():
            raise ValueError("Source layer unexpectedly changed product RGB")
        rgba = rgba.crop(bounds)
        layers.append(
            PixelLayer(
                artifact(rgba, f"source-product-{index + 1}.png"),
                source.asset_id,
                source.sha256,
                (left + bounds[0], top + bounds[1], left + bounds[2], top + bounds[3]),
            )
        )
    return tuple(layers)


def preview(cutout: ProviderArtifact) -> ProviderArtifact:
    with Image.open(BytesIO(cutout.payload)) as opened:
        rgba = opened.convert("RGBA")
    checker = Image.new("RGBA", rgba.size, "#dddddd")
    from PIL import ImageDraw

    draw = ImageDraw.Draw(checker)
    for y in range(0, rgba.height, 16):
        for x in range(0, rgba.width, 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle((x, y, x + 15, y + 15), fill="#aaaaaa")
    checker.alpha_composite(rgba)
    return artifact(checker, "source-matte-preview.png")


def positions(context: dict[str, Any], count: int) -> list[list[float]]:
    """Reserve product cells without hiding contract text or changing canvas."""
    if not 1 <= count <= 6:
        raise ValueError("Invalid source layer count")
    key = str(context.get("item_key", ""))
    if key.startswith("A-DESKTOP"):
        x0, y0, x1, y1 = 0.49, 0.04, 0.98, 0.96
    elif context["job_type"] == "amazon_visual_suite" and key != "MAIN-01":
        x0, y0, x1, y1 = 0.04, 0.26, 0.96, 0.94
    else:
        x0, y0, x1, y1 = 0.04, 0.04, 0.96, 0.96
    columns = min(count, 3)
    rows = math.ceil(count / columns)
    return [
        [
            x0 + (x1 - x0) * (i % columns) / columns,
            y0 + (y1 - y0) * (i // columns) / rows,
            x0 + (x1 - x0) * (i % columns + 1) / columns,
            y0 + (y1 - y0) * (i // columns + 1) / rows,
        ]
        for i in range(count)
    ]


def background_prompt(
    description: str, cells: list[list[float]], copy: tuple[str, ...] = ()
) -> str:
    return (
        "BACKGROUND PLATE ONLY. The server will add ORIGINAL PHOTOGRAPHED "
        "PRODUCT PIXELS afterwards. Do not draw products, characters, charms, "
        "packaging, labels-on-products, silhouettes or placeholders. Preserve "
        "the described background/style, placing approved marketing copy "
        "outside the reserved product cells. "
        "Leave each cell as quiet continuous background: no box, border, "
        "grid, watermark, text, extra prop or object. Any products in style "
        "references are EXCLUDED; only their background is authoritative. "
        "Return one opaque full-bleed native canvas at the requested size. "
        "Reserved cells (normalized x0,y0,x1,y1): "
        + json.dumps(cells)
        + "\nEMPTY BACKGROUND DESCRIPTION:\n"
        + description
        + "\nAPPROVED MARKETING COPY (empty means NO TEXT): "
        + json.dumps(copy, ensure_ascii=False)
        + "\nFINAL REQUIREMENT: empty background plate only, with absolutely "
        "no product, character, keychain, charm, metal clasp, tag, rack, "
        "silhouette, placeholder or lettering on a product."
    )


def compose(
    background: ProviderArtifact,
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
) -> tuple[ProviderArtifact, dict[str, Any]]:
    if len(cutouts) != len(cells):
        raise ValueError("Source layer placement count changed")
    if hashlib.sha256(background.payload).hexdigest() != background.sha256:
        raise ValueError("Native background hash changed")
    with Image.open(BytesIO(background.payload)) as opened:
        opened.load()
        if opened.size != (background.width, background.height):
            raise ValueError("Native background dimensions changed")
        canvas = opened.convert("RGBA")
        if canvas.getchannel("A").getextrema()[0] != 255:
            raise ValueError(
                "Native background must be opaque; no artificial fill is allowed"
            )
    placements = []
    for cutout, cell in zip(cutouts, cells, strict=True):
        if hashlib.sha256(cutout.payload).hexdigest() != cutout.sha256:
            raise ValueError("Saved source product pixels changed")
        with Image.open(BytesIO(cutout.payload)) as opened:
            rgba = opened.convert("RGBA")
        x0, y0, x1, y1 = cell
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Invalid source product placement")
        width, height = (
            math.floor((x1 - x0) * canvas.width),
            math.floor((y1 - y0) * canvas.height),
        )
        factor = min(width / rgba.width, height / rgba.height)
        resized = rgba.resize(
            (max(1, round(rgba.width * factor)), max(1, round(rgba.height * factor))),
            Image.Resampling.LANCZOS,
        )
        left = round(x0 * canvas.width + (width - resized.width) / 2)
        top = round(y0 * canvas.height + (height - resized.height) / 2)
        canvas.alpha_composite(resized, (left, top))
        placements.append(
            {
                "source_layer_sha256": cutout.sha256,
                "box": [left, top, left + resized.width, top + resized.height],
            }
        )
    result = artifact(canvas, "source-pixel-composite.png")
    if (result.width, result.height) != (background.width, background.height):
        raise ValueError("Compositor cannot change the native canvas")
    return result, {
        "version": VERSION,
        "method": "source_rgb_copy_alpha_only_uniform_scale",
        "background_sha256": background.sha256,
        "composite_sha256": result.sha256,
        "placements": placements,
    }
