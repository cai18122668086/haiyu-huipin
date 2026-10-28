"""Load exact native canvas/slice bytes; no alternate normalization or encoding."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..provider import ProviderArtifact
from .plans import canonical_hash


class DetailPixelError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "NATIVE_DETAIL_PIXEL_LINEAGE_INVALID"


@dataclass(frozen=True)
class NativePairPixels:
    lineage: dict[str, Any]
    canvas: ProviderArtifact
    screens: tuple[ProviderArtifact, ProviderArtifact]


def load_native_pair_pixels(
    value: Mapping[str, Any],
    workspaces: Sequence[Path],
    *,
    group_key: str,
) -> NativePairPixels:
    """Read the pinned Harness inspection and close the file/transport boundary."""
    try:
        lineage = dict(value)
        proof = {key: item for key, item in lineage.items() if key != "sha256"}
        if lineage.get("version") != "native-pixel-lineage-v1" or lineage.get(
            "sha256"
        ) != canonical_hash(proof):
            raise DetailPixelError("The native pixel lineage proof changed")
        roots = [path.resolve() for path in workspaces]

        def read(node: Mapping[str, Any]) -> bytes:
            path = Path(node["path"]).resolve()
            if not any(path.is_relative_to(root) for root in roots):
                raise DetailPixelError(
                    "A native raster escaped its approved job workspaces"
                )
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != node["sha256"]:
                raise DetailPixelError("A native raster changed after inspection")
            return payload

        source = lineage["source"]
        read(source)
        source_width, source_height = int(source["width_px"]), int(source["height_px"])
        mode = lineage["normalization"]["mode"]
        resampled = mode in {
            "CENTER_CROP_RESIZE",
            "BOUNDED_CENTER_CROP_RESIZE",
            "HYBRID_CROP_WITH_BLURRED_EDGE_EXTENSION",
        }
        formats = {
            "PNG": ("image/png", ".png"),
            "JPEG": ("image/jpeg", ".jpg"),
            "WEBP": ("image/webp", ".webp"),
        }

        def raster(
            node: Mapping[str, Any],
            filename: str,
            *,
            expected_pixels: bytes | None = None,
        ) -> ProviderArtifact:
            payload = read(node)
            with Image.open(BytesIO(payload)) as image:
                image.load()
                width, height = image.size
                if image.mode != "RGB" or image.format not in formats:
                    raise DetailPixelError(
                        "Native delivery raster format or RGB mode changed"
                    )
                if expected_pixels is not None and image.tobytes() != expected_pixels:
                    raise DetailPixelError(
                        "A native slice differs from its exact fixed canvas crop"
                    )
                kind, suffix = formats[str(image.format)]
                header = f"RGB:{width}x{height}".encode("ascii") + bytes([0])
                pixel_sha = hashlib.sha256(header + image.tobytes()).hexdigest()
            if (width, height) != (
                node["width_px"],
                node["height_px"],
            ) or pixel_sha != node["pixel_sha256"]:
                raise DetailPixelError(
                    "Native raster dimensions or decoded pixels changed"
                )
            return ProviderArtifact(
                payload=payload,
                filename=filename + suffix,
                content_type=kind,
                width=width,
                height=height,
                sha256=node["sha256"],
                locally_resampled=resampled,
                provider_width=source_width,
                provider_height=source_height,
            )

        pair_id = lineage["pair_id"]
        canvas_node = lineage["canvas"]
        canvas = raster(canvas_node, f"{group_key}-{pair_id}-canvas")
        rows = lineage["slices"]
        if (
            len(rows) != 2
            or [row["module_id"] for row in rows] != lineage["module_ids"]
        ):
            raise DetailPixelError("Native slice order changed")
        height = canvas.height // 2
        if canvas.height % 2:
            raise DetailPixelError("Native pair height is not two fixed windows")
        screens = []
        for index, row in enumerate(rows):
            box = [0, index * height, canvas.width, (index + 1) * height]
            if row["crop_box"] != box or row["canvas_sha256"] != canvas.sha256:
                raise DetailPixelError(
                    "Native slice no longer belongs to its fixed canvas window"
                )
            node = {**row, "width_px": canvas.width, "height_px": height}
            screens.append(
                raster(
                    node,
                    f"{group_key}-{pair_id}-screen-{index + 1:02d}",
                    expected_pixels=fixed_crop_pixels(canvas.payload, box),
                )
            )
        return NativePairPixels(
            lineage=lineage, canvas=canvas, screens=(screens[0], screens[1])
        )
    except (OSError, KeyError, TypeError, ValueError, UnidentifiedImageError) as exc:
        if isinstance(exc, DetailPixelError):
            raise
        raise DetailPixelError(
            "The exact native pixel files or proof are missing or unreadable"
        ) from exc


def fixed_crop_pixels(payload: bytes, box: list[int]) -> bytes:
    with Image.open(BytesIO(payload)) as image:
        return image.crop((box[0], box[1], box[2], box[3])).tobytes()


def pixel_review_context(lineage: Mapping[str, Any]) -> dict[str, Any]:
    """Expose raster facts to vision without private filesystem paths."""
    return {
        key: (
            {
                name: item
                for name, item in value.items()
                if name not in {"path", "source_path"}
            }
            if key in {"source", "canvas", "normalization"}
            else [
                {name: item for name, item in row.items() if name != "path"}
                for row in value
            ]
            if key == "slices"
            else value
        )
        for key, value in lineage.items()
        if key != "token"
    }
