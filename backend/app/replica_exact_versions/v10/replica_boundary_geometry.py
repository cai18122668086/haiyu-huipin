"""Resolve approximate equal-count cuts only against unique safe source gaps."""

import hashlib
import math
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from statistics import median
from typing import Any, cast

from PIL import Image

POLICY = "source-pixel-safe-boundary-v1"


def _safe_cut(
    image: Image.Image, left: int, right: int
) -> tuple[int, list[int]] | None:
    width, height = image.size
    radius = max(12, min(96, round(height * 0.06)))
    common_low = max(1, left - radius, right - radius)
    common_high = min(height - 2, left + radius, right + radius)
    if common_low > common_high:
        return None
    low = max(1, min(left, right) - radius)
    high = min(height - 2, max(left, right) + radius)
    border = round(width * 0.04)
    strip = image.convert("RGB").crop((border, 0, width - border, height))
    # Analysis only: keep source y coordinates and never write modified pixels.
    strip = strip.resize((min(256, strip.width), height), Image.Resampling.BOX)
    pixels = strip.load()
    assert pixels is not None
    bands: list[tuple[int, int]] = []
    start = None
    for y in range(low, high + 2):
        flat = False
        if y <= high:
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
            bands.append((start, y - 1))
            start = None
    minimum_band = max(8, math.ceil(height * 0.01))
    candidates = [
        band
        for band in bands
        if band[1] - band[0] + 1 >= minimum_band
        and min(band[1], common_high) - max(band[0], common_low) + 1
        >= max(4, minimum_band // 2)
    ]
    candidates.sort(key=lambda band: band[1] - band[0], reverse=True)
    if not candidates:
        return None
    best = candidates[0]
    if len(candidates) > 1 and (
        candidates[1][1] - candidates[1][0] + 1 >= (best[1] - best[0] + 1) / 2
    ):
        return None  # Ambiguous gaps cannot authorize a guessed cut.
    selected = round((max(best[0], common_low) + min(best[1], common_high)) / 2)
    return selected, list(best)


def resolve_boundaries(
    columns: list[dict[str, Any]],
    split: dict[str, Any],
    inventory: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    split, inventory = deepcopy(split), deepcopy(inventory)
    receipts = []
    for source, proposed, observed in zip(
        columns, split["columns"], inventory["columns"], strict=True
    ):
        left, right = proposed["boundary_ys"], observed["boundary_ys"]
        count = len(proposed["screens"])
        if (
            count != len(observed["screens"])
            or len(left) != count - 1
            or len(right) != count - 1
        ):
            continue  # Never infer or change a model's physical screen count.
        tolerance = max(3, min(24, round(source["height"] * 0.02)))
        disputed = [
            i
            for i, (a, b) in enumerate(zip(left, right, strict=True))
            if abs(a - b) > tolerance
        ]
        if not disputed:
            continue
        payload = Path(source["crop_path"]).read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        with Image.open(BytesIO(payload)) as image:
            if image.size != (source["width"], source["height"]):
                continue
            cuts, observations = list(left), []
            for index in disputed:
                resolved = _safe_cut(image, left[index], right[index])
                if resolved is None:
                    break
                selected, band = resolved
                cuts[index] = selected
                observations.append(
                    {
                        "policy": POLICY,
                        "column_id": source["column_id"],
                        "boundary_index": index,
                        "original_split_y": left[index],
                        "original_inventory_y": right[index],
                        "selected_y": selected,
                        "safe_blank_band": band,
                        "source_sha256": sha,
                        "source_size": [source["width"], source["height"]],
                        "screen_counts_unchanged": True,
                        "source_bytes_modified": False,
                    }
                )
            else:
                if cuts == sorted(set(cuts)) and all(
                    0 < y < image.height for y in cuts
                ):
                    proposed["boundary_ys"] = cuts
                    observed["boundary_ys"] = list(cuts)
                    receipts.extend(observations)
    return split, inventory, receipts
