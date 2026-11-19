"""Bounded layout compilation before the unchanged native rendering gates."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

VERSION = "bounded-product-layout-v1"


def place_products_without_collisions(
    placements: list[dict[str, Any]], text_layout: list[dict[str, Any]], panels: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate at most 0.10 and uniformly reduce at most 10%, or refuse.

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

    changes = []
    for index, row in enumerate(placed):
        products = [
            other["panel_box"]
            for i, other in enumerate(placed)
            if i != index and other["panel"] == row["panel"]
        ]
        obstacles = products + [
            text["panel_box"] for text in text_layout if text["panel"] == row["panel"]
        ]
        old = row["panel_box"]
        if not any(overlap(old, obstacle) for obstacle in products):
            continue
        cx, cy = (old[0] + old[2]) / 2, (old[1] + old[3]) / 2
        candidates = []
        for scale in (1.0, 0.95, 0.9):
            width, height = (old[2] - old[0]) * scale, (old[3] - old[1]) * scale
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
