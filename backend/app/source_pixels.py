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
import re
import threading
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps

from .provider import ProviderArtifact

VERSION = "source-pixel-layers-v11-white-exterior-edge"
MAX_DETAIL_UPSCALE = 4.0
MAX_MATTE_WORK_PIXELS = 8_000_000
PLANNING_CONSTRAINTS = (
    "SOURCE PHOTOGRAPH RENDER CAPABILITIES: Plan from the actual supplied "
    "product photographs. The final renderer preserves their photographed RGB, "
    "printing, connected parts and visible mechanical state. It can uniformly "
    "scale/translate a complete object, repeat an original view, and crop a "
    "specific visible detail. It cannot invent another angle, unfold/open "
    "hardware, separate connected parts, rotate individual parts, replace "
    "letters, or demonstrate an unseen action. Author every module's visual "
    "proof and layout within those capabilities; explain an unseen operation "
    "with approved text rather than promising an unphotographed action image. "
    "The deterministic renderer draws photographed source views and exact "
    "approved text only. Do not author leader lines, arrows, numbered circles, "
    "color dots, rules, badges, diagrams or other precisely positioned graphic "
    "annotations: these are not supported rendering primitives. Answer each "
    "module's buyer question using visible original views, bounded detail "
    "insets and their exact approved captions. Do not promise a different "
    "mechanical state or attachment interaction than the source photo shows. "
    "Source-only macro crops must specify the actual visible detail. "
    "A photographed detail crop may be enlarged at most 4x linearly. Tiny "
    "original fittings cannot become sharp full-frame macro photos. Plan a "
    "bounded original-detail inset with supporting original product views and "
    "approved readable copy when source resolution is limited. Never promise "
    "a macro framing that would require invented or enhanced source pixels. "
    "Scene actors/supporting props may be generated behind the intact photographed "
    "product, with explicit achievable contact/attachment positions. Do not "
    "claim a measured size, thickness, weight, material, licensing or package "
    "contents that source pixels or confirmed user facts do not establish. "
    "Do not identify a cartoon character, animal species, brand or licensed "
    "series from visual resemblance. Unless the user explicitly supplies its "
    "name, describe each pictured design by visible color, tag color, position "
    "and geometry in source facts AND marketing copy. Do not translate product "
    "printing into a guessed identity label. A neutral label must remain "
    "consistent with the actual photographed shape. "
    "Keep every canonical module and its buyer question; choose a truthful "
    "visible demonstration rather than inventing product evidence. All source "
    "fidelity, copy, layout, native-canvas and final visual checks still apply."
)
MODEL_SHA256 = "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
_lock = threading.Lock()
_net: Any = None
_loaded_path: Path | None = None


@dataclass(frozen=True)
class PixelLayer:
    artifact: ProviderArtifact
    source_asset_id: str
    source_sha256: str
    source_box: tuple[int, int, int, int]


def background_only(instruction: str) -> bool:
    # A background-only edit implicitly preserves the subject. Match the
    # entire instruction so an additional product-edit clause stays editable.
    description = r"(?:(?!同时|并|以及|连同|再把|再将|和|与)[^、，,。.!！；;\n])"
    return re.fullmatch(
        r"(?:请)?(?:只|仅)?"
        rf"(?:(?:增加|添加|更换|替换|换成|换){description}{{0,64}}背景"
        rf"|(?:把|将)?背景(?:更换为|替换为|改成|换成|改为){description}{{1,64}})"
        r"(?:[，, ]*(?:不额外增加(?:其他)?内容|不(?:再)?添加其他内容))?[。.!！]?",
        instruction.strip(),
    ) is not None


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
    if context["job_type"] == "creative_image" and background_only(instruction):
        return True
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


class SourceForegroundUnclear(ValueError):
    """The original pixels do not provide a usable product boundary."""


class MatteFragmentError(SourceForegroundUnclear):
    """The foreground is ambiguous; no image may be dispatched from this matte."""


class DetailResolutionError(ValueError):
    def __init__(self, message: str, diagnostic: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


def _alpha(
    crop: Image.Image,
    model_path: Path,
    selection_box: tuple[int, int, int, int] | None = None,
) -> Image.Image:
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
        try:
            first = _segment(crop, cv, np, _net)
        except MatteFragmentError:
            if selection_box is None:
                raise
            # Narrow only the surrounding source context, retaining the entire
            # selected rectangle plus a margin. The same ambiguity criterion
            # still applies. This is bounded to one additional local inference,
            # before any supplier request, and never synthesizes product RGB.
            core_left, core_top, core_right, core_bottom = selection_box
            alternate = (
                max(0, core_left - 12),
                max(0, core_top - 12),
                min(crop.width, core_right + 12),
                min(crop.height, core_bottom + 12),
            )
            if alternate == (0, 0, crop.width, crop.height):
                raise
            recovered = _segment(crop.crop(alternate), cv, np, _net)
            first = Image.new("L", crop.size)
            first.paste(recovered, (alternate[0], alternate[1]))
        bounds = first.getbbox()
        if bounds is None:
            raise SourceForegroundUnclear("Source matte is empty")
        # Recenter on the actual foreground before a second bounded inference.
        # Small metal fittings can be missed in a coarse, asymmetric context.
        # Union copies source alpha only; RGB is never generated or repaired.
        left, top, right, bottom = bounds
        pad = max(12, round(max(right - left, bottom - top) * 0.1))
        box = (
            max(0, left - pad),
            max(0, top - pad),
            min(crop.width, right + pad),
            min(crop.height, bottom + pad),
        )
        refined = _segment(crop.crop(box), cv, np, _net)
        second = Image.new("L", crop.size)
        second.paste(refined, (box[0], box[1]))
        coarse = ImageChops.lighter(first, second)
        refined = _recover_fine_edges(crop, coarse, cv, np)
        return _single_product(refined, cv, np)


def _recover_fine_edges(
    crop: Image.Image, coarse: Image.Image, cv: Any, np: Any
) -> Image.Image:
    """Resolve a small source-color band without erasing confirmed foreground.

    The saliency model can miss fine reflective metal edges. Only pixels near
    its confirmed product contour are candidates; distant display hardware is
    definite background. Final completeness/context review remains mandatory.
    """
    radius = min(8, max(2, round(min(crop.size) * 0.025)))
    foreground = np.array(coarse) >= 128
    vicinity = np.array(coarse.filter(ImageFilter.MaxFilter(2 * radius + 1))) > 0
    labels = np.where(
        foreground, cv.GC_FGD, np.where(vicinity, cv.GC_PR_FGD, cv.GC_BGD)
    ).astype(np.uint8)
    if not np.any(labels == cv.GC_BGD):
        raise ValueError("Fine-edge refinement lacks definite background")
    cv.setRNGSeed(0)
    cv.grabCut(
        cv.cvtColor(np.array(crop.convert("RGB")), cv.COLOR_RGB2BGR),
        labels,
        None,
        np.zeros((1, 65), np.float64),
        np.zeros((1, 65), np.float64),
        5,
        cv.GC_INIT_WITH_MASK,
    )
    refined = np.where((labels == cv.GC_FGD) | (labels == cv.GC_PR_FGD), 255, 0).astype(
        np.uint8
    )
    if np.any(refined[foreground] != 255) or np.any(refined[~vicinity] != 0):
        raise ValueError("Fine-edge refinement violated its source contour bounds")
    return Image.fromarray(refined, "L")


def _segment(crop: Image.Image, cv: Any, np: Any, net: Any) -> Image.Image:
    rgb = np.array(crop.convert("RGB"))
    small = cv.resize(rgb, (320, 320)).astype(np.float32) / 255
    tensor = (
        (small - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    ).transpose(2, 0, 1)[None]
    net.setInput(tensor.astype(np.float32))
    prediction = net.forward()[0, 0]
    minimum, maximum = float(prediction.min()), float(prediction.max())
    if not math.isfinite(maximum) or maximum - minimum < 0.01:
        raise SourceForegroundUnclear("Source matte has no reliable foreground")
    confidence = cv.resize(
        ((prediction - minimum) * 255 / (maximum - minimum)).astype(np.uint8),
        crop.size,
    )
    labels = np.where(
        confidence < 96,
        cv.GC_BGD,
        np.where(confidence >= 230, cv.GC_FGD, cv.GC_PR_FGD),
    ).astype(np.uint8)
    if not np.any(labels == cv.GC_FGD) or not np.any(labels == cv.GC_BGD):
        raise SourceForegroundUnclear(
            "Source matte lacks foreground/background evidence"
        )
    cv.setRNGSeed(0)
    cv.grabCut(
        cv.cvtColor(rgb, cv.COLOR_RGB2BGR),
        labels,
        None,
        np.zeros((1, 65), np.float64),
        np.zeros((1, 65), np.float64),
        5,
        cv.GC_INIT_WITH_MASK,
    )
    alpha = np.where(
        (labels == cv.GC_FGD) | (labels == cv.GC_PR_FGD),
        255,
        0,
    ).astype(np.uint8)
    return _single_product(Image.fromarray(alpha, "L"), cv, np)


def _single_product(mask: Image.Image, cv: Any, np: Any) -> Image.Image:
    # The same disconnected-fragment guard applies AFTER mask unions and
    # edge refinement too. They must not reintroduce a neighboring product.
    alpha = np.array(mask)
    count, components, stats, _ = cv.connectedComponentsWithStats(alpha, 8)
    if count < 2:
        raise SourceForegroundUnclear("Source matte has no connected product")
    largest = int(stats[1:, cv.CC_STAT_AREA].argmax()) + 1
    total = int(stats[1:, cv.CC_STAT_AREA].sum())
    if int(stats[largest, cv.CC_STAT_AREA]) < total * 0.95:
        raise MatteFragmentError("Source matte contains disconnected product fragments")
    alpha[components != largest] = 0
    return Image.fromarray(alpha, "L")


def _trim_white_matte_edge(crop: Image.Image, mask: Image.Image) -> Image.Image:
    """Remove a narrow white-background rim, never repaint product RGB.

    Re-extracting a flattened white product photo can classify its pure white
    exterior as foreground. Only near-white pixels in the outer two-pixel band
    beside colored foreground are eligible. Interior printing and wholly white
    thin details remain untouched. The resulting alpha still needs matte review.
    """
    alpha = mask.convert("L")
    eroded = alpha.filter(ImageFilter.MinFilter(5))
    rgb = crop.convert("RGB")
    colors, opacity = list(rgb.getdata()), list(alpha.getdata())
    colored = Image.new("L", alpha.size)
    colored.putdata([
        255 if a > 0 and min(pixel) < 235 else 0
        for pixel, a in zip(colors, opacity, strict=True)
    ])
    nearby = colored.filter(ImageFilter.MaxFilter(5))
    coverage = alpha.point(lambda value: 255 if value else 0).filter(
        ImageFilter.BoxBlur(1)
    )
    values = [
        0 if a > 0 and edge == 0 and neighbor > 0 and density >= 160
        and min(pixel) >= 248 and max(pixel) - min(pixel) <= 3 else a
        for pixel, a, edge, neighbor, density in zip(
            colors, opacity, eroded.getdata(), nearby.getdata(), coverage.getdata(),
            strict=True,
        )
    ]
    changed = sum(a != b for a, b in zip(opacity, values, strict=True))
    if changed > sum(a > 0 for a in opacity) * 0.03:
        raise ValueError("White exterior cleanup exceeds its narrow source-edge bound")
    cleaned = Image.new("L", alpha.size)
    cleaned.putdata(values)
    return cleaned


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
    *,
    tight_context: bool = False,
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
        if tight_context:
            # Re-segment the complete original selection with less surrounding
            # scenery after visual rejection. Never trim the selected box.
            short_side = min((x1 - x0) * image.width, (y1 - y0) * image.height)
            pad = max(4, round(short_side * 0.05))
        left, top = (
            max(0, math.floor(x0 * image.width) - pad),
            max(0, math.floor(y0 * image.height) - pad),
        )
        right, bottom = (
            min(image.width, math.ceil(x1 * image.width) + pad),
            min(image.height, math.ceil(y1 * image.height) + pad),
        )
        # Context may include another complete product, especially on an
        # already isolated white-background intermediate. Limit inference to
        # the source object's spatial cell; retain the original photographed
        # pixels, not a white fill or an invented mask. Completeness review
        # still rejects any fitting clipped by an ambiguous inventory.
        left, top, right, bottom = owned_context(
            region,
            preparation.get("source_inventory", []),
            image.size,
            (left, top, right, bottom),
        )
        width, height = right - left, bottom - top
        work_size = (width, height)
        if width * height > MAX_MATTE_WORK_PIXELS:
            scale = math.sqrt(MAX_MATTE_WORK_PIXELS / (width * height))
            work_width = min(MAX_MATTE_WORK_PIXELS, max(1, int(width * scale)))
            work_height = min(
                MAX_MATTE_WORK_PIXELS // work_width, max(1, int(height * scale))
            )
            work_size = (work_width, work_height)
        crop = (
            image.crop((left, top, right, bottom))
            if work_size == (width, height)
            else image.resize(
                work_size, Image.Resampling.LANCZOS, box=(left, top, right, bottom)
            )
        )
        scale_x, scale_y = work_size[0] / width, work_size[1] / height
        alpha = _alpha(
            crop,
            model_path,
            (
                math.floor((math.floor(x0 * image.width) - left) * scale_x),
                math.floor((math.floor(y0 * image.height) - top) * scale_y),
                math.ceil((math.ceil(x1 * image.width) - left) * scale_x),
                math.ceil((math.ceil(y1 * image.height) - top) * scale_y),
            ),
        )
        if getattr(source, "source_role", "") == "white_background_product":
            alpha = _trim_white_matte_edge(crop, alpha)
            alpha = _single_product(
                alpha, importlib.import_module("cv2"), importlib.import_module("numpy")
            )
        if alpha.size != (width, height):
            alpha = alpha.resize((width, height), Image.Resampling.NEAREST)
        bounds = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
        if bounds is None or (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) < 64:
            raise SourceForegroundUnclear("Source layer is empty or incomplete")
        bounds = (
            max(0, bounds[0] - 8),
            max(0, bounds[1] - 8),
            min(width, bounds[2] + 8),
            min(height, bounds[3] + 8),
        )
        source_box = (
            left + bounds[0], top + bounds[1], left + bounds[2], top + bounds[3]
        )
        # RGB is copied unchanged. Only alpha comes from segmentation.
        original = image.crop(source_box)
        rgba = original.convert("RGBA")
        rgba.putalpha(alpha.crop(bounds))
        if rgba.convert("RGB").tobytes() != original.tobytes():
            raise ValueError("Source layer unexpectedly changed product RGB")
        layers.append(
            PixelLayer(
                artifact(rgba, f"source-product-{index + 1}.png"),
                source.asset_id,
                source.sha256,
                source_box,
            )
        )
    return tuple(layers)


def owned_context(
    selected: dict[str, Any],
    inventory: list[dict[str, Any]],
    size: tuple[int, int],
    crop: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = selected["box"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top, right, bottom = crop
    for other in inventory:
        if other["asset_id"] != selected["asset_id"] or other["box"] == selected["box"]:
            continue
        a0, b0, a1, b1 = other["box"]
        ox, oy = (a0 + a1) / 2, (b0 + b1) / 2
        aligned_y = min(y1, b1) - max(y0, b0) > min(y1 - y0, b1 - b0) * 0.5
        aligned_x = min(x1, a1) - max(x0, a0) > min(x1 - x0, a1 - a0) * 0.5
        if aligned_y and abs(ox - cx) > min(x1 - x0, a1 - a0) * 0.5:
            boundary = round((ox + cx) * size[0] / 2)
            if ox < cx:
                left = max(left, boundary)
            else:
                right = min(right, boundary)
        if aligned_x and abs(oy - cy) > min(y1 - y0, b1 - b0) * 0.5:
            boundary = round((oy + cy) * size[1] / 2)
            if oy < cy:
                top = max(top, boundary)
            else:
                bottom = min(bottom, boundary)
    if not (
        0 <= left < cx * size[0] < right <= size[0]
        and 0 <= top < cy * size[1] < bottom <= size[1]
    ):
        raise ValueError("Source inventory cannot isolate the selected product context")
    return left, top, right, bottom


def preview(cutout: ProviderArtifact) -> ProviderArtifact:
    with Image.open(BytesIO(cutout.payload)) as opened:
        rgba = opened.convert("RGBA")
    # Evidence-only breathing room makes the complete outer contour visible.
    # This checkerboard is never a delivered or supplier canvas.
    padded = Image.new("RGBA", (rgba.width + 32, rgba.height + 32))
    padded.alpha_composite(rgba, (16, 16))
    rgba = padded
    checker = Image.new("RGBA", rgba.size, "#dddddd")
    from PIL import ImageDraw

    draw = ImageDraw.Draw(checker)
    for y in range(0, rgba.height, 16):
        for x in range(0, rgba.width, 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle((x, y, x + 15, y + 15), fill="#aaaaaa")
    checker.alpha_composite(rgba)
    factor = min(4, max(1, 1024 // max(checker.size)))
    checker = checker.resize(
        (checker.width * factor, checker.height * factor), Image.Resampling.NEAREST
    )
    return artifact(checker, "source-matte-preview.png")


def matte_pair(
    original: ProviderArtifact, cutout_preview: ProviderArtifact
) -> ProviderArtifact:
    """Put real original/cutout evidence at the same scale in one view."""
    views = []
    for view in (original, cutout_preview):
        if hashlib.sha256(view.payload).hexdigest() != view.sha256:
            raise ValueError("Matte comparison evidence bytes changed")
        with Image.open(BytesIO(view.payload)) as opened:
            views.append(opened.convert("RGB"))
    left, right = views
    if left.size != right.size:
        raise ValueError("Matte comparison evidence has mismatched geometry")
    result = Image.new("RGB", (left.width * 2 + 16, left.height + 20), "#eeeeee")
    result.paste(left, (0, 20))
    result.paste(right, (left.width + 16, 20))
    from PIL import ImageDraw

    draw = ImageDraw.Draw(result)
    draw.text((4, 4), "ORIGINAL", fill="black")
    draw.text((left.width + 20, 4), "CUTOUT", fill="black")
    return artifact(result, "original-cutout-pair.png")


def repair_layers(
    layers: tuple[PixelLayer, ...], repairs: list[dict[str, Any]], feather: int
) -> tuple[PixelLayer, ...]:
    """Apply one bounded, source-only alpha correction, then require re-audit.

    Normalized boxes refer to the paired audit images including their 16px
    margin. Background erasure is restricted to the unprinted upper fitting
    zone; it cannot erase the character faces, tag text or copyright below.
    """
    if type(feather) is not int or feather not in (0, 1, 2) or len(repairs) > 12:
        raise ValueError("Source mask correction exceeds its bounded contract")
    result = []
    for index, layer in enumerate(layers):
        if hashlib.sha256(layer.artifact.payload).hexdigest() != layer.artifact.sha256:
            raise ValueError("Source layer changed before mask correction")
        with Image.open(BytesIO(layer.artifact.payload)) as opened:
            rgba = opened.convert("RGBA")
        rgb = rgba.convert("RGB").tobytes()
        alpha = rgba.getchannel("A")
        before = alpha.tobytes()
        rows = [
            sum(v >= 128 for v in before[y * rgba.width : (y + 1) * rgba.width])
            for y in range(rgba.height)
        ]
        broad = next(
            (y for y, width in enumerate(rows) if width >= max(rows) * 0.35), 0
        )
        hardware_end = min(broad, round(rgba.height * 0.2))
        for repair in repairs:
            if type(repair["layer_index"]) is not int or not 0 <= repair[
                "layer_index"
            ] < len(layers):
                raise ValueError("Mask correction selects a foreign layer")
            if repair["layer_index"] != index:
                continue
            box = repair["box"]
            if len(box) != 4 or any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                for v in box
            ):
                raise ValueError("Mask correction coordinates are invalid")
            left, top, right, bottom = (
                round(box[0] * (rgba.width + 32)) - 16,
                round(box[1] * (rgba.height + 32)) - 16,
                round(box[2] * (rgba.width + 32)) - 16,
                round(box[3] * (rgba.height + 32)) - 16,
            )
            if not (
                0 <= left < right <= rgba.width and 0 <= top < bottom <= rgba.height
            ):
                raise ValueError("Mask correction leaves its immutable source crop")
            action = repair["action"]
            colors = rgba.convert("RGB").convert("HSV").tobytes()
            opacity = alpha.tobytes()
            gold = {
                (x, y)
                for y in range(top, bottom)
                for x in range(left, right)
                if 11 <= colors[(y * rgba.width + x) * 3] <= 78
                and colors[(y * rgba.width + x) * 3 + 1] >= 65
                and colors[(y * rgba.width + x) * 3 + 2] >= 30
            }

            if action == "remove_background":
                if bottom > hardware_end:
                    raise ValueError(
                        "Mask correction may not erase protected artwork or printing"
                    )
                if gold:
                    raise ValueError("Mask correction may not erase photographed metal")
                alpha.paste(0, (left, top, right, bottom))
            elif action == "restore_foreground":
                if (right - left) * (bottom - top) > max(
                    64, sum(v > 0 for v in before) * 0.12
                ):
                    raise ValueError(
                        "Source mask correction changes too much of the product"
                    )
                if bottom > hardware_end:
                    raise ValueError(
                        "Mask correction may not redraw protected artwork or printing"
                    )
                # A rectangle is evidence localization, NEVER a foreground
                # mask. Restore only actual source-colored metal connected to
                # the existing fitting. Background corners and isolated rack
                # fragments cannot become opaque merely because QA boxed them.
                candidates = {
                    (x, y)
                    for x, y in gold
                    if opacity[y * rgba.width + x] < 255
                }
                queue = [
                    (x, y)
                    for x, y in candidates
                    if any(
                        0 <= x + dx < rgba.width
                        and 0 <= y + dy < rgba.height
                        and opacity[(y + dy) * rgba.width + x + dx] >= 128
                        for dx, dy in (
                            (-1, -1), (0, -1), (1, -1), (-1, 0),
                            (1, 0), (-1, 1), (0, 1), (1, 1),
                        )
                    )
                ]
                while queue:
                    x, y = queue.pop()
                    if (x, y) not in candidates:
                        continue
                    candidates.remove((x, y))
                    alpha.putpixel((x, y), 255)
                    queue.extend(
                        (x + dx, y + dy)
                        for dx, dy in (
                            (-1, -1), (0, -1), (1, -1), (-1, 0),
                            (1, 0), (-1, 1), (0, 1), (1, 1),
                        )
                        if (x + dx, y + dy) in candidates
                    )
            else:
                raise ValueError("Unknown source mask correction")
        if feather:
            softened = ImageChops.darker(
                alpha, alpha.filter(ImageFilter.GaussianBlur(feather))
            )
            # Keep the fine fitting sharp; soften only the broad product body.
            alpha.paste(softened.crop((0, broad, rgba.width, rgba.height)), (0, broad))
        changed = sum(a != b for a, b in zip(before, alpha.tobytes(), strict=True))
        if changed > max(64, sum(v > 0 for v in before) * 0.12):
            raise ValueError("Source mask correction changes too much of the product")
        rgba.putalpha(alpha)
        if rgba.convert("RGB").tobytes() != rgb:
            raise ValueError("Mask correction changed original product RGB")
        result.append(replace(layer, artifact=artifact(rgba, layer.artifact.filename)))
    if all(
        a.artifact.sha256 == b.artifact.sha256
        for a, b in zip(layers, result, strict=True)
    ):
        raise ValueError("Mask correction made no verifiable pixel change")
    return tuple(result)


def original_positions(
    layers: tuple[PixelLayer, ...], source: Any, target_size: str,
) -> list[list[float]]:
    """Fit the entire source frame, keeping each subject at its original position."""
    with Image.open(BytesIO(source.payload)) as image:
        width, height = ImageOps.exif_transpose(image).size
    target_w, target_h = (int(v) for v in target_size.split("x"))
    scale = min(target_w / width, target_h / height)
    dx, dy = (target_w - width * scale) / 2, (target_h - height * scale) / 2
    cells = []
    for layer in layers:
        if (layer.source_asset_id, layer.source_sha256) != (
            source.asset_id, source.sha256,
        ):
            raise ValueError("Original placement requires one verified source frame")
        with Image.open(BytesIO(layer.artifact.payload)) as image:
            visible = image.getchannel("A").getbbox()
        if visible is None:
            raise ValueError("Original placement has no visible product")
        left, top, _, _ = layer.source_box
        x0, y0, x1, y1 = visible
        cells.append([
            (dx + (left + x0) * scale) / target_w,
            (dy + (top + y0) * scale) / target_h,
            min(1.0, math.nextafter((dx + (left + x1) * scale) / target_w, 1.0)),
            min(1.0, math.nextafter((dy + (top + y1) * scale) / target_h, 1.0)),
        ])
    return cells


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
    description: str,
    cells: list[list[float]],
    copy: tuple[str, ...] = (),
    product_geometry: list[dict[str, Any]] | None = None,
    *,
    continuous_pair: bool = False,
) -> str:
    return (
        "BACKGROUND PLATE ONLY. The server will add ORIGINAL PHOTOGRAPHED "
        "PRODUCT PIXELS afterwards. Do not draw products, "
        "characters on those products, charms, "
        "packaging, labels-on-products, silhouettes or placeholders. Preserve "
        "the described background/style, placing approved marketing copy "
        "outside the reserved product cells. "
        "Do not draw a box, border, grid, watermark, text or substitute product "
        "inside a reserved cell. Preserve all scene actors and supporting props "
        "explicitly required in the description, including hands, bags and "
        "attachment loops: those are part of the scene, not products. A reserved "
        "cell may contain the required supporting surface behind the product. "
        "For an approved hanging scene, the visible support must extend through "
        "each measured upper contact band. For an approved standing scene, the "
        "surface must meet each measured lower contact band. Bands mark actual "
        "visible source contours in full-canvas coordinates. Keep required support "
        "visible beside and between the silhouettes even when it is transparent; "
        "a distant horizon, soft shadow or empty upper margin cannot attach them. "
        "Never replace a required interaction with an empty floating space. "
        "Do not add unrequested props. Any products in style "
        "references are EXCLUDED; only their background is authoritative. "
        "Return one opaque full-bleed native canvas at the requested size. "
        + (
            "TWO-SCREEN CONTINUITY: paint one uninterrupted scene through the "
            "horizontal midpoint (y=0.5). Continue the same background field, "
            "lighting, texture and relevant scene geometry across that line. "
            "No hard color band, horizontal divider, frame, collage, separate "
            "upper/lower backdrops or abrupt horizon at the midpoint. "
            "Reserved placement cells guide composition, not background panels. "
            "This continuity rule overrides conflicting layout prose. "
            if continuous_pair
            else ""
        )
        + "Reserved cells (normalized x0,y0,x1,y1): "
        + json.dumps(cells)
        + "\nMEASURED LATER PRODUCT PLACEMENT (normalized full-canvas geometry; "
        "the source products themselves must NOT be generated): "
        + json.dumps(product_geometry or [])
        + "\nEMPTY BACKGROUND DESCRIPTION:\n"
        + description
        + "\nAPPROVED MARKETING COPY (empty means NO TEXT): "
        + json.dumps(copy, ensure_ascii=False)
        + "\nFINAL REQUIREMENT: empty background plate only, with absolutely "
        "no duplicate product, freestanding character, keychain, charm, "
        "metal clasp, tag, "
        "silhouette, placeholder or lettering on a product. Exclude any source "
        "display rack unless the approved scene explicitly requires that support. "
        "If the approved background is a printed surface, preserve its explicitly "
        "requested flat motif on that surface without adding standalone objects."
        + (
            "\nREQUIRED VISIBLE MARKETING TEXT: Render EVERY approved copy "
            "string above exactly, using the approved panel layout and hierarchy "
            "outside product cells. Empty background means no products; it does "
            "not cancel these required headlines and supporting text. Any "
            "generic 'no text' in the background description applies only to "
            "the reserved product cells. Missing approved copy is a failure."
            if copy
            else "\nNO TEXT anywhere: no marketing copy was approved."
        )
    )


def native_placements(
    value: Any, count: int, panels: int = 2, *, check_overlap: bool = True
) -> tuple[list[int], list[list[float]]]:
    """Validate the approved pair's source-layer placement, including its seam."""
    if (
        panels not in (1, 2)
        or not isinstance(value, list)
        or not panels <= len(value) <= 12
    ):
        raise ValueError("Native pair requires bounded product placement")
    indices: list[int] = []
    cells: list[list[float]] = []
    seen_panels = set()
    for entry in value:
        index, box, panel = entry["layer_index"], entry["panel_box"], entry["panel"]
        if (
            type(index) is not int
            or not 0 <= index < count
            or type(panel) is not int
            or panel not in range(panels)
        ):
            raise ValueError("Native placement changed the source authority")
        if len(box) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in box
        ):
            raise ValueError("Native placement geometry is invalid")
        x0, y0, x1, y1 = box
        if not (0.04 <= x0 < x1 <= 0.96 and 0.04 <= y0 < y1 <= 0.96):
            raise ValueError("Native product placement crosses the pair seam")
        # The pair is two vertically stacked equal panels. This transforms
        # authored destination geometry, never source or supplier image pixels.
        y0, y1 = (panel + y0) / panels, (panel + y1) / panels
        box = [x0, y0, x1, y1]
        for old_index, old in enumerate(cells):
            if (
                check_overlap
                and min(x1, old[2]) > max(x0, old[0])
                and min(y1, old[3]) > max(y0, old[1])
            ):
                raise ValueError(
                    f"placements[{len(cells)}].panel_box overlaps "
                    f"placements[{old_index}].panel_box: "
                    f"{entry['panel_box']} versus {value[old_index]['panel_box']}. "
                    "Each source layer is one product; use disjoint cells."
                )
        indices.append(index)
        cells.append(box)
        seen_panels.add(panel)
    if seen_panels != set(range(panels)):
        raise ValueError("Native product placement omits an approved panel")
    return indices, cells


def crop_windows(
    cutouts: tuple[ProviderArtifact, ...],
    windows: list[list[float]] | None,
) -> tuple[ProviderArtifact, ...]:
    """Approved close-ups use original pixels, never a generated replacement."""
    if windows is None:
        return cutouts
    if len(windows) != len(cutouts):
        raise ValueError("Close-up placement count changed")
    result = []
    for cutout, window in zip(cutouts, windows, strict=True):
        if len(window) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in window
        ):
            raise ValueError("Invalid original-pixel close-up")
        x0, y0, x1, y1 = window
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("Original-pixel close-up leaves its source")
        if list(window) == [0, 0, 1, 1]:
            result.append(cutout)
            continue
        if hashlib.sha256(cutout.payload).hexdigest() != cutout.sha256:
            raise ValueError("Source pixels changed before close-up extraction")
        with Image.open(BytesIO(cutout.payload)) as opened:
            rgba = opened.convert("RGBA")
        visible = rgba.getchannel("A").getbbox()
        if visible is None:
            raise ValueError("Close-up has no photographed product")
        rgba = rgba.crop(visible)
        crop = rgba.crop(
            (
                math.floor(x0 * rgba.width),
                math.floor(y0 * rgba.height),
                math.ceil(x1 * rgba.width),
                math.ceil(y1 * rgba.height),
            )
        )
        if min(crop.size) < 4 or crop.getchannel("A").getbbox() is None:
            raise ValueError("Close-up contains no usable original product pixels")
        result.append(artifact(crop, "original-product-closeup.png"))
    return tuple(result)


def detail_inset_box_limits(
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
    windows: list[list[float]],
    canvas: tuple[int, int],
    *,
    panels: int,
) -> dict[int, tuple[float, float]]:
    try:
        validate_detail_resolution(cutouts, cells, windows, canvas, panels=panels)
    except DetailResolutionError as error:
        return {
            problem["placement_index"]: tuple(problem["maximum_panel_box_extent"])
            for problem in error.diagnostic["violations"]
        }
    return {}


def validate_detail_resolution(
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
    windows: list[list[float]],
    canvas: tuple[int, int],
    *,
    panels: int = 1,
) -> None:
    if panels not in (1, 2):
        raise ValueError("Invalid resolution panel count")
    if len(cutouts) != len(cells) or len(windows) != len(cells):
        raise ValueError("Detail resolution cannot change its placement count")
    violations: list[dict[str, Any]] = []
    for index, (cutout, cell, window) in enumerate(
        zip(cutouts, cells, windows, strict=True)
    ):
        if window == [0, 0, 1, 1]:
            continue
        with Image.open(BytesIO(cutout.payload)) as opened:
            visible = opened.getchannel("A").getbbox()
        if visible is None:
            raise ValueError("Detail crop has no original pixels")
        width, height = visible[2] - visible[0], visible[3] - visible[1]
        factor = min(
            math.floor((cell[2] - cell[0]) * canvas[0]) / width,
            math.floor((cell[3] - cell[1]) * canvas[1]) / height,
        )
        if factor > MAX_DETAIL_UPSCALE:
            maximum = [math.floor(width * MAX_DETAIL_UPSCALE),
                       math.floor(height * MAX_DETAIL_UPSCALE)]
            violations.append(
                {
                    "placement_index": index,
                    "original_detail_pixels": [width, height],
                    "observed_upscale": round(factor, 4),
                    "maximum_upscale": MAX_DETAIL_UPSCALE,
                    "maximum_inset_pixels": maximum,
                    "maximum_panel_box_extent": [
                        maximum[0] / canvas[0], maximum[1] * panels / canvas[1]
                    ],
                }
            )
    if violations:
        first = violations[0]
        width, height = first["original_detail_pixels"]
        raise DetailResolutionError(
            f"placement[{first['placement_index']}] enlarges an original "
            f"{width}x{height} detail by {first['observed_upscale']:.2f}x; "
            f"maximum is {MAX_DETAIL_UPSCALE:g}x. All {len(violations)} oversized "
            "insets must respect their original source resolution.",
            {
                "constraint": "original_detail_resolution",
                "violations": violations,
                "instruction": "Correct ALL listed placements together. Make "
                "each inset no larger than its given panel extents; use "
                "surrounding space for approved copy and original full-product "
                "supporting views. Never invent detail pixels or omit copy.",
            },
        )


def layer_reference(cutout: ProviderArtifact) -> ProviderArtifact:
    """Coordinate reference for a source window; never sent as a background."""
    if hashlib.sha256(cutout.payload).hexdigest() != cutout.sha256:
        raise ValueError("Original reference layer changed")
    with Image.open(BytesIO(cutout.payload)) as opened:
        rgba = opened.convert("RGBA")
    visible = rgba.getchannel("A").getbbox()
    if visible is None:
        raise ValueError("Source reference is empty")
    rgba = rgba.crop(visible)
    canvas = Image.new("RGBA", rgba.size, "white")
    canvas.alpha_composite(rgba)
    factor = min(4, max(1, 1024 // max(canvas.size)))
    return artifact(
        canvas.resize(
            (canvas.width * factor, canvas.height * factor), Image.Resampling.NEAREST
        ),
        "source-window-reference.png",
    )


def placed_layer(
    cutout: ProviderArtifact, size: tuple[int, int], cell: list[float]
) -> tuple[Image.Image, tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Use identical geometry for rendering and source/candidate comparisons."""
    if hashlib.sha256(cutout.payload).hexdigest() != cutout.sha256:
        raise ValueError("Saved source product pixels changed")
    with Image.open(BytesIO(cutout.payload)) as opened:
        rgba = opened.convert("RGBA")
    visible_box = rgba.getchannel("A").getbbox()
    if visible_box is None:
        raise ValueError("Source product has no visible pixels")
    rgba = rgba.crop(visible_box)
    x0, y0, x1, y1 = cell
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("Invalid source product placement")
    width, height = math.floor((x1 - x0) * size[0]), math.floor((y1 - y0) * size[1])
    factor = min(width / rgba.width, height / rgba.height)
    resized = rgba.resize(
        (max(1, round(rgba.width * factor)), max(1, round(rgba.height * factor))),
        Image.Resampling.LANCZOS,
    )
    left = round(x0 * size[0] + (width - resized.width) / 2)
    top = round(y0 * size[1] + (height - resized.height) / 2)
    return resized, (left, top, left + resized.width, top + resized.height), visible_box


def support_guide(
    size: tuple[int, int],
    geometry: list[dict[str, Any]],
    mode: str,
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
) -> ProviderArtifact:
    """Map actual composited bounds to a guide that is never used as output."""
    from PIL import ImageDraw

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    radius = max(3, round(min(size) * 0.012))
    for item, cutout, cell in zip(geometry, cutouts, cells, strict=True):
        layer, (left, top, _, _), _ = placed_layer(cutout, size, cell)
        image.paste(
            "#bdbdbd",
            (left, top, left + layer.width, top + layer.height),
            layer.getchannel("A"),
        )
        band = item[
            "upper_contact_band" if mode == "upper_attachment" else "lower_contact_band"
        ]
        x = round((band[0] + band[2]) * size[0] / 2)
        y = round((band[1] + band[3]) * size[1] / 2)
        draw.line((x - radius, y, x + radius, y), fill="#007d88", width=3)
        draw.line((x, y - radius, x, y + radius), fill="#007d88", width=3)
    return artifact(image, "support-registration.png")


def placement_geometry(
    cutout: ProviderArtifact, size: tuple[int, int], cell: list[float]
) -> dict[str, list[float]]:
    layer, (left, top, _, _), _ = placed_layer(cutout, size, cell)
    alpha = layer.getchannel("A")
    foreground = alpha.point(lambda value: 255 if value >= 128 else 0)
    bounds = foreground.getbbox()
    if bounds is None:
        foreground = alpha
        bounds = foreground.getbbox()
    if bounds is None:
        raise ValueError("Placed product has no visible contact contour")
    x0, y0, x1, y1 = bounds
    band_height = max(1, round((y1 - y0) * 0.06))

    def normalize(box: tuple[int, int, int, int]) -> list[float]:
        return [
            round((left + box[0]) / size[0], 5),
            round((top + box[1]) / size[1], 5),
            round((left + box[2]) / size[0], 5),
            round((top + box[3]) / size[1], 5),
        ]

    def contact_band(start: int) -> list[float]:
        band = foreground.crop((x0, start, x1, start + band_height))
        contact = band.getbbox()
        assert contact is not None  # Each band includes an occupied edge row.
        return normalize(
            (x0 + contact[0], start + contact[1], x0 + contact[2], start + contact[3])
        )

    return {
        "visible_product_bounds": normalize(bounds),
        "upper_contact_band": contact_band(y0),
        "lower_contact_band": contact_band(y1 - band_height),
    }


def comparison_panels(
    candidate: ProviderArtifact,
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
) -> tuple[ProviderArtifact, ...]:
    """Paired inspection evidence; never a delivery or substituted candidate.

    LEFT is the original photographed layer, uniformly scaled by the frozen
    render geometry on neutral grey. RIGHT is the actual complete destination
    crop, with its actual background. Neither side synthesizes artwork/text.
    The full unmasked candidate remains mandatory in the visual review.
    """
    from PIL import ImageDraw

    if hashlib.sha256(candidate.payload).hexdigest() != candidate.sha256:
        raise ValueError("Candidate comparison pixels changed")
    with Image.open(BytesIO(candidate.payload)) as opened:
        canvas = opened.convert("RGBA")
    if canvas.size != (candidate.width, candidate.height):
        raise ValueError("Candidate comparison dimensions changed")
    if len(cutouts) != len(cells):
        raise ValueError("Source comparison placement count changed")
    comparisons = []
    for i, (cutout, cell) in enumerate(zip(cutouts, cells, strict=True)):
        source, box, _ = placed_layer(cutout, canvas.size, cell)
        actual = canvas.crop(box)
        # Fully opaque source pixels must match the real final candidate.
        # This is an additional integrity check, not a visual approval flag.
        for expected, observed in zip(source.getdata(), actual.getdata(), strict=True):
            if expected[3] == 255 and expected != observed:
                raise ValueError(
                    "Candidate differs from original opaque product pixels"
                )
        pair = Image.new("RGBA", (source.width * 2 + 24, source.height + 32), "#dddddd")
        pair.alpha_composite(source, (8, 24))
        pair.alpha_composite(actual, (source.width + 16, 24))
        draw = ImageDraw.Draw(pair)
        draw.text((8, 5), "SOURCE", fill="black")
        draw.text((source.width + 16, 5), "CANDIDATE", fill="black")
        factor = min(4, max(1, 1024 // max(pair.size)))
        pair = pair.resize(
            (pair.width * factor, pair.height * factor), Image.Resampling.NEAREST
        )
        comparisons.append(artifact(pair, f"source-candidate-pair-{i}.png"))
    return tuple(comparisons)


def register_hanging_cells(
    background: ProviderArtifact,
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
) -> list[list[float]]:
    """Register clasps to nearby visible enclosed loops in a product-free plate."""
    cv, np = importlib.import_module("cv2"), importlib.import_module("numpy")
    with Image.open(BytesIO(background.payload)) as opened:
        view = opened.convert("L")
    view.thumbnail((900, 900))
    gray = np.asarray(view)
    edges = cv.Canny(cv.GaussianBlur(gray, (3, 3), 0), 14, 38)
    edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, hierarchy = cv.findContours(edges, cv.RETR_TREE, cv.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return cells
    anchors = []
    for contour, relatives in zip(contours, hierarchy[0], strict=True):
        if relatives[3] < 0 or relatives[2] >= 0:
            continue
        x, y, w, h = cv.boundingRect(contour)
        area = cv.contourArea(contour)
        if (
            not 0.006 < w / view.width < 0.07
            or not 0.008 < h / view.height < 0.07
            or not 0.18 < w / h < 2
            or area < w * h * 0.32
        ):
            continue
        anchors.append(((x + w * 0.5) / view.width, (y + h * 0.5) / view.height))
    aligned = []
    for cutout, cell in zip(cutouts, cells, strict=True):
        band = placement_geometry(cutout, (background.width, background.height), cell)[
            "upper_contact_band"
        ]
        px, py = (band[0] + band[2]) / 2, (band[1] + band[3]) / 2
        near = [
            (x - px, y - py)
            for x, y in anchors
            if abs(x - px) < 0.055 and abs(y - py) < 0.105
        ]
        if not near:
            aligned.append(cell)
            continue
        dx, dy = min(near, key=lambda delta: delta[0] ** 2 + delta[1] ** 2)
        if any(abs(dx - x) > 0.025 or abs(dy - y) > 0.025 for x, y in near):
            aligned.append(cell)
            continue
        moved = [cell[0] + dx, cell[1] + dy, cell[2] + dx, cell[3] + dy]
        aligned.append(moved if all(0.015 < v < 0.985 for v in moved) else cell)
    if any(
        min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
        for i, a in enumerate(aligned)
        for b in aligned[i + 1 :]
    ):
        return cells
    return aligned


def marketing_text_view(
    candidate: ProviderArtifact,
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
) -> ProviderArtifact:
    """Expose actual external copy, obscuring only verified opaque source RGB."""
    from PIL import ImageDraw

    with Image.open(BytesIO(candidate.payload)) as opened:
        frame = opened.convert("RGB")
    for cutout, cell in zip(cutouts, cells, strict=True):
        layer, box, _ = placed_layer(cutout, frame.size, cell)
        mask = layer.getchannel("A").point(lambda v: 255 if v == 255 else 0)
        if ImageChops.multiply(
            ImageChops.difference(frame.crop(box), layer.convert("RGB")),
            mask.convert("RGB"),
        ).getbbox():
            raise ValueError(
                "External copy may overlap protected original product pixels"
            )
        frame.paste("#bebebe", box, mask)
    if frame.height % 2:
        raise ValueError("Native marketing inspection requires an even pair")
    half = frame.height // 2
    sheet = Image.new("RGB", (2 * frame.width, half + 44), "white")
    for index in range(2):
        sheet.paste(
            frame.crop((0, index * half, frame.width, (index + 1) * half)),
            (index * frame.width, 44),
        )
        ImageDraw.Draw(sheet).text(
            (index * frame.width + 10, 12),
            f"SCREEN {index + 1} - EXTERNAL COPY ONLY",
            fill="black",
        )
    return artifact(sheet, "external-copy-inspection.png")


def compose(
    background: ProviderArtifact,
    cutouts: tuple[ProviderArtifact, ...],
    cells: list[list[float]],
    *, resting_shadow: bool = False,
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
        resized, box, visible_box = placed_layer(cutout, canvas.size, cell)
        left, top = box[:2]
        if resting_shadow:
            # A small occlusion shadow follows the actual source silhouette.
            # The generated photograph still supplies the visible surface.
            radius = max(1, round(min(canvas.size) * 0.003))
            pad = radius * 4
            shadow = Image.new("L", (resized.width + pad * 2, resized.height + pad * 2))
            shadow.paste(resized.getchannel("A"), (pad, pad))
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius)).point(
                lambda v: v * 0.23
            )
            tint = Image.new("RGBA", shadow.size, (46, 38, 32, 0))
            tint.putalpha(shadow)
            canvas.alpha_composite(tint, (left - pad + radius, top - pad + radius))
        canvas.alpha_composite(resized, (left, top))
        placements.append(
            {
                "source_layer_sha256": cutout.sha256,
                "source_visible_box": list(visible_box),
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
        **({"contact_shadow": "source-alpha-v1"} if resting_shadow else {}),
    }
