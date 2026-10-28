#!/usr/bin/env python3
"""Compile a set-level contract that unifies style without cloning layouts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "style-layout-separation-v0.1.5.schema.json"

STYLE_FIELDS = [
    "TYPOGRAPHY_FAMILY",
    "TYPE_TREATMENT",
    "COLOR_ROLES",
    "MATERIALS",
    "LIGHTING_LANGUAGE",
    "SHARED_GRAPHIC_MOTIFS",
]
LAYOUT_FIELDS = [
    "LAYOUT_ARCHETYPE",
    "PRODUCT_ANCHOR",
    "PRODUCT_SCALE",
    "COMPOSITION_AXIS",
    "TITLE_POSITION",
    "READING_PATH",
    "CARD_TOPOLOGY",
]


def build_style_layout_contract(
    raw: dict[str, Any],
    overlay: dict[str, Any],
    evidence_plan: dict[str, Any],
) -> dict[str, Any]:
    evidence_by_index = {p["page_index"]: p for p in evidence_plan["pages"]}
    pages = []
    for page in overlay["pages"]:
        index = page["page_index"]
        pages.append(
            {
                "page_index": index,
                "layout_archetype": page["layout_archetype"],
                "product_anchor": page["product_anchor"],
                "product_occupancy": page["product_occupancy"],
                "composition_axis": page["composition_axis"],
                "title_region_pct": page["title_region_pct"],
                "reading_path": page["reading_path"],
                "card_topology": evidence_by_index[index]["card_topology"],
                "representation_mode": evidence_by_index[index][
                    "representation_mode"
                ],
                "difference_from_previous_page": page[
                    "difference_from_previous_page"
                ],
            }
        )
    layouts = [p["layout_archetype"] for p in pages]
    anchors = [p["product_anchor"] for p in pages]
    axes = [p["composition_axis"] for p in pages]
    title_positions = [
        (p["title_region_pct"]["x"], p["title_region_pct"]["y"])
        for p in pages
    ]
    topologies = [p["card_topology"] for p in pages]
    gates = {
        "five_distinct_layout_archetypes": len(set(layouts)) == 5,
        "adjacent_layout_skeleton_reuse_forbidden": all(
            layouts[i] != layouts[i - 1] for i in range(1, 5)
        ),
        "at_least_three_product_anchors": len(set(anchors)) >= 3,
        "at_least_three_composition_axes": len(set(axes)) >= 3,
        "at_least_three_title_positions": len(set(title_positions)) >= 3,
        "at_least_three_card_topologies": len(set(topologies)) >= 3,
        "style_and_layout_control_sets_are_disjoint": not (
            set(STYLE_FIELDS) & set(LAYOUT_FIELDS)
        ),
        "all_pages_share_campaign_style_lock": len(
            {p["campaign_style_lock_id"] for p in overlay["pages"]}
        ) == 1,
        "product_identity_priority_preserved": all(
            p["product_identity_priority"] == "HARD_LOCK_BEFORE_OVERLAY"
            for p in overlay["pages"]
        ),
        "profile01_semantics_read_false": True,
    }
    result = {
        "schema_version": "style-layout-separation-0.1.5",
        "request_id": raw["request_id"],
        "status": "READY" if all(gates.values()) else "BLOCKED",
        "style_lock": {
            "controls": STYLE_FIELDS,
            "must_not_control": LAYOUT_FIELDS,
            "campaign_style_lock_id": overlay["campaign_style_lock"]["lock_id"],
            "source_type": "ENGINEERED_DEFAULT",
        },
        "pages": pages,
        "set_gates": gates,
        "generation_performed": False,
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    ).validate(result)
    return result


def style_layout_prompt_block(contract: dict[str, Any], page: dict[str, Any]) -> str:
    return (
        "【STYLE / LAYOUT SEPARATION CONTRACT】\n"
        f"style_lock_controls={json.dumps(contract['style_lock']['controls'], ensure_ascii=False)}；"
        f"style_lock_must_not_control={json.dumps(contract['style_lock']['must_not_control'], ensure_ascii=False)}。\n"
        f"本页必须执行 layout_archetype={page['layout_archetype']}、"
        f"product_anchor={page['product_anchor']}、"
        f"composition_axis={page['composition_axis']}、"
        f"card_topology={page['card_topology']}。"
        "统一字体、配色、材质、灯光和共享母题，但不得复制其他页面的版式骨架。"
    )
