"""Bounded layout compilation before the unchanged native rendering gates."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

VERSION = "bounded-product-layout-v3"


def place_products_without_collisions(
    placements: list[dict[str, Any]],
    text_layout: list[dict[str, Any]],
    panels: int,
    *,
    detail_box_limits: dict[int, tuple[float, float]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate at most 0.10; oversize crops may shrink to original-pixel limits.

    Source identity, crop, panel, order and text stay unchanged. This only
    compiles an overlapping proposal; the caller must still apply all native
    placement, detail-resolution, typography and final pixel-review checks.
    """
    original, placed = deepcopy(placements), deepcopy(placements)
    if panels not in (1, 2) or not panels <= len(placed) <= 12:
        return original, []
    for row in placed:
        box = row.get("panel_box")
        if (
            type(row.get("panel")) is not int
            or row["panel"] not in range(panels)
            or not isinstance(box, list)
            or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or not (0.04 <= box[0] < box[2] <= 0.96)
            or not (0.04 <= box[1] < box[3] <= 0.96)
        ):
            return original, []

    def overlap(one: list[float], two: list[float]) -> bool:
        return min(one[2], two[2]) > max(one[0], two[0]) and min(one[3], two[3]) > max(
            one[1], two[1]
        )

    limits = detail_box_limits or {}
    if any(
        index not in range(len(placed))
        or not all(math.isfinite(extent) and extent > 0 for extent in extents)
        or not placed[index].get("source_window")
        or placed[index]["source_window"] == [0, 0, 1, 1]
        for index, extents in limits.items()
    ):
        return original, []
    changes = []
    for index in sorted(range(len(placed)), key=lambda index: index not in limits):
        row = placed[index]
        products = [
            other["panel_box"]
            for i, other in enumerate(placed)
            if i != index and other["panel"] == row["panel"]
        ]
        obstacles = products + [
            text["panel_box"] for text in text_layout if text["panel"] == row["panel"]
        ]
        old = row["panel_box"]
        is_detail = row.get("source_window") not in (None, [0, 0, 1, 1])
        max_width, max_height = limits.get(index, (1.0, 1.0))
        collision_targets = obstacles if is_detail else products
        if index not in limits and not any(
            overlap(old, obstacle) for obstacle in collision_targets
        ):
            continue
        cx, cy = (old[0] + old[2]) / 2, (old[1] + old[3]) / 2
        candidates = []
        # The renderer contains the crop and centers it inside this box. Trim
        # empty space per axis so the limiting side keeps its full pixel budget.
        base_width = min(old[2] - old[0], max_width)
        base_height = min(old[3] - old[1], max_height)
        for scale in (1.0, 0.95, 0.9):
            width, height = base_width * scale, base_height * scale
            xs, ys = {cx - width / 2}, {cy - height / 2}
            for box in obstacles:
                xs.update((box[0] - width, box[2]))
                ys.update((box[1] - height, box[3]))
            for x0 in xs:
                for y0 in ys:
                    box = [round(v, 12) for v in (x0, y0, x0 + width, y0 + height)]
                    dx, dy = (box[0] + box[2]) / 2 - cx, (box[1] + box[3]) / 2 - cy
                    if (
                        max(abs(dx), abs(dy)) > 0.100000000001
                        or not (0.04 <= box[0] < box[2] <= 0.96)
                        or not (0.04 <= box[1] < box[3] <= 0.96)
                        or any(overlap(box, obstacle) for obstacle in obstacles)
                    ):
                        continue
                    # Keep horizontal product ordering from the proposal.
                    if any(
                        (cx - (other["panel_box"][0] + other["panel_box"][2]) / 2)
                        * (
                            (box[0] + box[2]) / 2
                            - (other["panel_box"][0] + other["panel_box"][2]) / 2
                        )
                        < 0
                        for i, other in enumerate(placed)
                        if i != index and other["panel"] == row["panel"]
                    ):
                        continue
                    candidates.append(((1 - scale, dx * dx + dy * dy, box), box))
        if not candidates:
            return original, []
        chosen = min(candidates, key=lambda candidate: candidate[0])[1]
        row["panel_box"] = chosen
        changes.append({"placement_index": index, "before": old, "after": chosen})
    return placed, changes
