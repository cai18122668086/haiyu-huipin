"""Verify the photographed detail in a native page crop before image dispatch."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw

from . import source_pixels

PROFILE = "image_fidelity_source_window"
VERSION = "original-detail-window-v1"


def inspection(layer: Any, window: list[float]) -> Any:
    """The left grid uses the exact alpha-trimmed coordinates used by crop_windows."""
    reference = source_pixels.layer_reference(layer)
    cropped = source_pixels.crop_windows((layer,), [window])[0]
    with Image.open(BytesIO(reference.payload)) as opened:
        left = opened.convert("RGB")
    left.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    with Image.open(BytesIO(cropped.payload)) as opened:
        crop = opened.convert("RGBA")
    right = Image.new("RGBA", crop.size, "white")
    right.alpha_composite(crop)
    right.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    zoom = max(1, min(6, 720 // max(right.size)))
    right = right.resize(
        (right.width * zoom, right.height * zoom), Image.Resampling.NEAREST
    )
    view = Image.new(
        "RGB",
        (left.width + right.width + 64, max(left.height, right.height) + 52),
        "white",
    )
    view.paste(left, (32, 36))
    view.paste(right.convert("RGB"), (left.width + 56, 36))
    draw = ImageDraw.Draw(view)
    draw.text((32, 0), "FULL PRODUCT", fill="black")
    draw.text((left.width + 56, 0), "ACTUAL CROP", fill="black")
    for i in range(11):
        x = 32 + round(i * (left.width - 1) / 10)
        y = 36 + round(i * (left.height - 1) / 10)
        draw.line((x, 32, x, 35), fill="black")
        draw.line((28, y, 31, y), fill="black")
        if i % 2 == 0:
            draw.text((x - 6, 18), str(i * 10), fill="black")
            draw.text((4, y - 5), str(i * 10), fill="black")
    x0, y0, x1, y1 = window
    draw.rectangle(
        (
            32 + round(x0 * (left.width - 1)),
            36 + round(y0 * (left.height - 1)),
            32 + round(x1 * (left.width - 1)),
            36 + round(y1 * (left.height - 1)),
        ),
        outline="#cc3377",
        width=2,
    )
    return source_pixels.artifact(view, "original-and-selected-window.png")


async def check(
    gateway: Any,
    request: Any,
    layers: tuple[Any, ...],
    placements: list[dict[str, Any]],
    plans: list[Any],
    layout_attempt: int,
) -> list[dict[str, Any]]:
    from .image_fidelity import SourcePhotoUnusable
    from .provider_diagnostics import DETAIL_UNCLEAR
    from .xinghe_agent import SourceImage

    selected = [
        i
        for i, p in enumerate(placements)
        if p.get("source_window", [0, 0, 1, 1]) != [0, 0, 1, 1]
    ]
    if not selected:
        return []
    window = {
        "type": "array",
        "minItems": 4,
        "maxItems": 4,
        "items": {"type": "number", "minimum": 0, "maximum": 1},
    }
    row = {
        "type": "object",
        "additionalProperties": False,
        "required": ["placement", "verdict", "evidence", "window"],
        "properties": {
            "placement": {"type": "integer", "enum": selected},
            "verdict": {"enum": ["matches", "wrong_window", "unreadable_source"]},
            "evidence": {"type": "string", "minLength": 1},
            "window": window,
        },
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["crops"],
        "properties": {
            "crops": {
                "type": "array",
                "minItems": len(selected),
                "maxItems": len(selected),
                "items": row,
            }
        },
    }
    receipts = []
    for attempt in range(2):
        images = []
        native_layers: dict[int, Any] = {}
        for i in selected:
            p = placements[i]
            layer_index = p["layer_index"]
            layer = layers[layer_index]
            view = inspection(layer, p["source_window"])
            if max(layer.width, layer.height) > 1024:
                native_layers[layer_index] = layer
            images.append(
                SourceImage(
                    f"crop-placement-{i}",
                    request.group_key,
                    view.filename,
                    view.content_type,
                    view.sha256,
                    view.payload,
                    source_role="source_window_inspection",
                )
            )
        # A reduced diagram alone cannot establish that the original is unreadable.
        for index, layer in native_layers.items():
            images.append(
                SourceImage(
                    f"native-layer-{index}",
                    request.group_key,
                    layer.filename,
                    layer.content_type,
                    layer.sha256,
                    layer.payload,
                    source_role="native_source_window_reference",
                )
            )
        reply = await gateway._plan(
            request,
            PROFILE,
            tuple(images),
            {
                "schema": schema,
                "approved_final_image_contract": request.prompt,
                "placements_in_image_order": [
                    {"index": i, **placements[i]} for i in selected
                ],
                **(
                    {
                        "native_layer_inspection": {
                            "layer_indices_after_diagrams": list(native_layers),
                            "instruction": "Check legibility on these original pixels.",
                        }
                    }
                    if native_layers
                    else {}
                ),
                "instruction": (
                    "For each placement compare its panel's approved detail with "
                    "the actual RIGHT crop. The left is the full original product "
                    "with the current window outlined; its scale reads 0 to 100 "
                    "percent. Return matches only if the intended parts and their "
                    "requested extent fill the crop. A whole face is not a macro "
                    "of a smaller feature. A crop may include enough neighboring "
                    "material to locate a detail. If the part IS visible elsewhere "
                    "or at a tighter scale on the left, use wrong_window and give "
                    "a corrected normalized [x0,y0,x1,y1] on THAT left product, "
                    "using its 0-100 percent axis divided by 100. If the original "
                    "does not resolve the requested detail, use unreadable_source. "
                    "Never infer hidden shapes. For matches and unreadable_source "
                    "echo the current window. Ignore composition and copy outside "
                    "the crop."
                ),
            },
            suffix=f":layout-{layout_attempt}-window-{attempt}",
        )
        plans.append(reply)
        if reply.error_code or not Draft202012Validator(schema).is_valid(reply.payload):
            raise RuntimeError("Source detail inspection unavailable")
        rows = reply.payload["crops"]
        if sorted(r["placement"] for r in rows) != selected:
            raise RuntimeError("Source detail inspection does not bind every placement")
        receipts.append(
            {
                "version": VERSION,
                "evidence_sha256": [s.sha256 for s in images],
                "crops": rows,
            }
        )
        if any(r["verdict"] == "unreadable_source" for r in rows):
            raise SourcePhotoUnusable(DETAIL_UNCLEAR)
        corrections = [r for r in rows if r["verdict"] == "wrong_window"]
        if not corrections:
            return receipts
        if not attempt:
            for r in corrections:
                p = placements[r["placement"]]
                source_pixels.crop_windows((layers[p["layer_index"]],), [r["window"]])
                p["source_window"] = r["window"]
    raise RuntimeError("Corrected source crop still differs from the approved detail")
