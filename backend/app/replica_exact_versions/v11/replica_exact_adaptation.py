"""Truthful render bindings, distinct from immutable reference observations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def blank_parallel_branch(proposal: dict[str, Any], layout: dict[str, Any]) -> bool:
    """Catch an empty co-primary label/body column, not isolated legal badges.

    This geometric guard supplements the visual review. It does not infer a
    commercial purpose or turn an omitted certification badge into required copy.
    """
    bindings = {b["region_id"]: b for b in proposal["text_bindings"]}
    regions = layout["text_regions"]
    omitted = [r for r in regions if bindings[r["id"]]["action"] != "render"]
    rendered = [r for r in regions if bindings[r["id"]]["action"] == "render"]

    def peers(left: dict[str, Any], right: dict[str, Any]) -> bool:
        a, b = left["bbox"], right["bbox"]
        return (
            left["role"] == right["role"]
            and left["font_tier"] == right["font_tier"]
            and (a[2] <= b[0] or b[2] <= a[0])
            and abs(a[1] + a[3] - b[1] - b[3]) <= max(a[3] - a[1], b[3] - b[1])
        )

    paired = [r for r in omitted if any(peers(r, s) for s in rendered)]
    for body in paired:
        if body["role"] != "body":
            continue
        a = body["bbox"]
        for label in paired:
            b = label["bbox"]
            if (
                label["role"] in {"badge", "headline", "subtitle"}
                and b[3] <= a[1]
                and max(a[0], b[0]) < min(a[2], b[2])
            ):
                return True
    return False


def validate_render_adaptation(
    proposal: dict[str, Any], layout: dict[str, Any], ledger: dict[str, Any]
) -> list[dict[str, Any]]:
    allowed = {f["id"] for f in ledger["facts"] if f["disposition"] == "approved"}
    anchors = proposal["render_anchors"]
    if [a["source_anchor_index"] for a in anchors] != list(
        range(len(layout["layout_anchors"]))
    ):
        raise ValueError("Bind every source anchor once in source order")
    for anchor in anchors:
        if not set(anchor["fact_ids"]) <= allowed:
            raise ValueError("Render adaptation references unapproved facts")
        if anchor["basis"] == "approved_facts" and not anchor["fact_ids"]:
            raise ValueError("A factual prop substitution needs approved fact IDs")
    commercial = proposal["commercial_adaptation"]
    if not set(commercial["fact_ids"]) <= allowed:
        raise ValueError("Commercial adaptation references unapproved facts")
    if commercial["mode"] == "truthful_substitution" and not commercial["fact_ids"]:
        raise ValueError("A commercial substitution needs approved supporting facts")
    groups = proposal["content_groups"]
    covered = [rid for group in groups for rid in group["region_ids"]]
    if len(covered) != len(set(covered)) or set(covered) != {
        r["id"] for r in layout["text_regions"]
    }:
        raise ValueError("Assign every text region to exactly one content group")
    bindings = {b["region_id"]: b for b in proposal["text_bindings"]}
    for group in groups:
        if group["critical"] and not any(
            bindings[rid]["action"] == "render" for rid in group["region_ids"]
        ):
            raise ValueError(
                "A critical content branch cannot be blank; use grounded replacement "
                "copy and the closest supported buyer question"
            )
    if blank_parallel_branch(proposal, layout):
        raise ValueError(
            "A parallel label/body branch cannot be entirely omitted; "
            "fill it with a grounded alternative without inventing instructions"
        )
    # Coordinate conversion is performed by the compact boundary. No source
    # labels or source product attributes are copied into execution implicitly.
    return [{"label": a["label"], "bbox": deepcopy(a["bbox"])} for a in anchors]
