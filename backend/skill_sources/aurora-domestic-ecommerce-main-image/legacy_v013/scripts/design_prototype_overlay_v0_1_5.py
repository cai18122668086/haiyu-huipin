#!/usr/bin/env python3
"""Compile non-factual visual design parameters between planning and prompting.

The overlay deliberately does not own product identity, claims, offers, copy
facts, or compliance.  The five carousel prototypes are engineered defaults:
the v2.1 research bundle explicitly blocks promotion of real furniture and
carousel rhythm patterns.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "design-prototype-overlay-v0.1.5.schema.json"
SOURCE_PRECEDENCE = [
    "USER_FACT",
    "EVIDENCE_DERIVED",
    "EXPERIMENTAL_PATTERN",
    "ENGINEERED_DEFAULT",
    "MODEL_FREEDOM",
]

BASE_CAMPAIGN_STYLE_LOCK = {
    "lock_id": "WARM_NEUTRAL_BLUE_LINE_V015",
    "typography_base": "SAME_MODERN_CHINESE_DISPLAY_AND_INFORMATION_FAMILY",
    "primary_text": "DEEP_GRAPHITE_NOT_PURE_BLACK",
    "secondary_text": "NEUTRAL_GRAY",
    "common_accent": "RESTRAINED_BLUE",
    "offer_accent_exception": "WARM_ORANGE_ONLY_ON_PAGE_5_INFORMATION_ZONE",
    "background_base": [
        "WARM_WHITE",
        "LIGHT_GRAY_ARCHITECTURAL_SURFACE",
        "RESTRAINED_LIGHT_WOOD",
    ],
    "information_surface": "TRANSLUCENT_WARM_WHITE",
    "card_corner_system": "CONSISTENT_MEDIUM_RADIUS",
    "border_system": "CONSISTENT_THIN_BLUE_OR_NEUTRAL_HAIRLINE",
    "icon_system": "CONSISTENT_MONOLINE_COMMERCIAL_ICON",
    "shadow_system": "SOFT_LOW_OPACITY_GROUNDED_SHADOW",
    "shared_device_motif": "THIN_BLUE_STRUCTURAL_LINE",
    "page_difference_policy": "CHANGE_LAYOUT_AND_DENSITY_WITHOUT_CHANGING_THE_CAMPAIGN_VISUAL_LANGUAGE",
    "style_lock_controls": [
        "TYPOGRAPHY_FAMILY",
        "TYPE_TREATMENT",
        "COLOR_ROLES",
        "MATERIALS",
        "LIGHTING_LANGUAGE",
        "SHARED_GRAPHIC_MOTIFS",
    ],
    "style_lock_must_not_control": [
        "LAYOUT_SKELETON",
        "PRODUCT_ANCHOR",
        "PRODUCT_SCALE",
        "TITLE_POSITION",
        "READING_PATH",
        "CARD_TOPOLOGY",
    ],
    "source_type": "ENGINEERED_DEFAULT",
}

FOOD_PAGE_BACKGROUNDS = {
    1: {
        "background_material": [
            "WARM_IVORY_FOOD_STAGE",
            "DARK_STONE_ACCENT",
            "PACKAGING_DERIVED_COLOR_PLANE",
        ],
        "lighting_structure": "UPPER_LEFT_WARM_SOFT_KEY + CLEAN_PACKAGE_RIM + GROUNDED_CONTACT_SHADOW",
    },
    2: {
        "background_material": [
            "WARM_IVORY_FOOD_STAGE",
            "SUBTLE_STEAM_GRADIENT",
            "DARK_STONE_ACCENT",
        ],
        "lighting_structure": "SOFT WARM FRONTAL_KEY + SIDE_PACKAGE_SEPARATION + CONTROLLED_APPETITE_HIGHLIGHT",
    },
    3: {
        "background_material": [
            "WARM_TABLETOP",
            "SOFT_KITCHEN_DEPTH",
            "RESTRAINED_STEAM_ATMOSPHERE",
        ],
        "lighting_structure": "BROAD WARM WINDOW_KEY + SOFT_AMBIENT_FILL + NATURAL_CONTACT_SHADOW",
    },
    4: {
        "background_material": [
            "WARM_IVORY_INFORMATION_SURFACE",
            "DARK_STONE_PRODUCT_STAGE",
            "PACKAGING_DERIVED_ACCENT_GRID",
        ],
        "lighting_structure": "CONTROLLED WARM SIDE_KEY + CRISP PACKAGE_EDGE + MATCHED_EVIDENCE_CARD_LIGHT",
    },
    5: {
        "background_material": [
            "WARM_IVORY_COMMERCIAL_SURFACE",
            "DARK_STONE_PRODUCT_STAGE",
            "PACKAGING_DERIVED_COLOR_INFO_ZONE",
        ],
        "lighting_structure": "SOFT WARM PRODUCT_KEY + CLEAN RIM_SEPARATION + GROUNDED_CONTACT_SHADOW",
    },
}

FOOD_LAYOUT_OVERRIDES = {
    1: {
        "layout_archetype": "CENTER_STAGE_IDENTITY_POSTER",
        "reading_path": [
            "top_brand_identity",
            "top_center_category_headline",
            "center_stage_product",
            "bottom_selling_point",
        ],
        "product_occupancy": [64, 72],
        "product_anchor": "CENTER_LOWER_STAGE",
        "composition_axis": "CENTERED_VERTICAL_POSTER",
        "title_region_pct": {"x": 13, "y": 5, "width": 74, "height": 24},
        "supporting_copy_region_pct": {"x": 28, "y": 29, "width": 44, "height": 9},
        "evidence_or_offer_region_pct": {"x": 28, "y": 88, "width": 44, "height": 8},
        "title_structure": "TOP_CENTER_IDENTITY + LARGE_CENTER_HEADLINE + ARC_KEYWORD",
        "graphic_device_family": [
            "CENTER_STAGE_HALO",
            "ARC_KEYWORD_BADGE",
            "ORANGE_WHITE_CORNER_STRIPE",
        ],
        "difference_from_previous_page": "Opening center-stage identity poster with no evidence cards.",
    },
    3: {
        "layout_archetype": "PANORAMIC_STEP_FILMSTRIP",
        "reading_path": [
            "top_horizontal_title_band",
            "left_product",
            "right_staggered_real_photo_steps",
        ],
        "product_occupancy": [44, 52],
        "product_anchor": "LEFT_LOWER_PRODUCT",
        "composition_axis": "TOP_BAND_THEN_RIGHT_VERTICAL_Z_PATH",
        "title_region_pct": {"x": 5, "y": 5, "width": 90, "height": 21},
        "supporting_copy_region_pct": {"x": 63, "y": 22, "width": 31, "height": 8},
        "evidence_or_offer_region_pct": {"x": 53, "y": 28, "width": 43, "height": 66},
        "title_structure": "WIDE_HORIZONTAL_TITLE_BAND + INLINE_KEYWORD",
        "graphic_device_family": [
            "STAGGERED_REAL_PHOTO_FILMSTRIP",
            "TORN_EDGE_PHOTO_FRAME",
            "ORANGE_WHITE_CORNER_STRIPE",
        ],
        "difference_from_previous_page": "Move product to the left and replace feature split with a right-side real-action filmstrip.",
    },
    4: {
        "layout_archetype": "TOP_DOWN_INGREDIENT_ORBIT",
        "reading_path": [
            "left_vertical_title",
            "center_top_view_product",
            "clockwise_real_food_orbit",
        ],
        "product_occupancy": [40, 48],
        "product_anchor": "CENTER_TOP_VIEW",
        "composition_axis": "RADIAL_CLOCKWISE_ORBIT",
        "title_region_pct": {"x": 2, "y": 20, "width": 20, "height": 60},
        "supporting_copy_region_pct": {"x": 25, "y": 79, "width": 50, "height": 10},
        "evidence_or_offer_region_pct": {"x": 0, "y": 0, "width": 100, "height": 100},
        "title_structure": "LEFT_VERTICAL_EDITORIAL_TITLE + RADIAL_LABELS",
        "graphic_device_family": [
            "IRREGULAR_REAL_PHOTO_ISLAND",
            "TORN_EDGE_COLLAGE",
            "ORANGE_WHITE_CORNER_STRIPE",
        ],
        "evidence_pointer_policy": "NO_CONNECTORS; REAL_PHOTO_ISLANDS_MUST_BE FACT_BOUND",
        "difference_from_previous_page": "Switch to top-down radial composition with irregular fact-bound food photography.",
    },
    5: {
        "reading_path": [
            "top_identity_line",
            "left_product",
            "right_numeric_information",
            "bottom_full_width_spec_rail",
        ],
        "product_occupancy": [45, 53],
        "product_anchor": "LEFT_CENTER_PRODUCT",
        "composition_axis": "LEFT_PRODUCT_RIGHT_NUMERIC_POSTER",
        "title_region_pct": {"x": 52, "y": 18, "width": 43, "height": 22},
        "supporting_copy_region_pct": {"x": 53, "y": 66, "width": 40, "height": 10},
        "evidence_or_offer_region_pct": {"x": 6, "y": 81, "width": 88, "height": 14},
        "title_structure": "RIGHT_NUMERIC_HERO + BOTTOM_FULL_WIDTH_INFORMATION_RAIL",
        "graphic_device_family": [
            "LARGE_NUMERIC_POSTER",
            "FULL_WIDTH_INFORMATION_RAIL",
            "ORANGE_WHITE_CORNER_STRIPE",
        ],
        "difference_from_previous_page": "Replace radial orbit with left product, right numeric hierarchy and a bottom information rail.",
    },
}


PROTOTYPES: dict[int, dict[str, Any]] = {
    1: {
        "layout_archetype": "IDENTITY_EDITORIAL",
        "reading_path": [
            "small_brand_identity",
            "large_category_headline",
            "emphasized_selling_point",
            "product_identity",
            "short_use_labels",
        ],
        "product_occupancy": [67, 74],
        "product_anchor": "CENTER_RIGHT_HERO",
        "composition_axis": "ASYMMETRIC_VERTICAL_LEFT_COPY_RIGHT_PRODUCT",
        "title_region_pct": {"x": 6, "y": 8, "width": 36, "height": 25},
        "supporting_copy_region_pct": {"x": 7, "y": 35, "width": 31, "height": 12},
        "evidence_or_offer_region_pct": {"x": 7, "y": 72, "width": 34, "height": 12},
        "keyword_emphasis_method": ["SCALE_UP", "ACCENT_COLOR_BLOCK"],
        "graphic_device_family": [
            "OFFSET_EDITORIAL_RULE",
            "SMALL_IDENTITY_TAB",
            "STRUCTURAL_CURVE",
        ],
        "graphic_device_limit": 2,
        "background_material": [
            "WARM_WHITE_ARCHITECTURAL_SURFACE",
            "LIGHT_GRAY_MINERAL_PLANE",
            "RESTRAINED_LIGHT_WOOD_ACCENT",
        ],
        "background_luminance": "LIGHT",
        "information_density": "MEDIUM",
        "lighting_structure": "UPPER_LEFT_SOFT_KEY + OPPOSITE_NARROW_RIM + GROUNDED_CONTACT_SHADOW",
        "title_structure": "SMALL_IDENTITY_LINE + LARGE_CATEGORY + OFFSET_KEYWORD",
        "difference_from_previous_page": "OPENING_IDENTITY_PAGE; establish the product with asymmetric editorial hierarchy.",
        "evidence_pointer_policy": "NO_EVIDENCE_POINTERS_ON_IDENTITY_PAGE",
    },
    2: {
        "layout_archetype": "FEATURE_MACRO",
        "reading_path": [
            "large_feature_keyword",
            "product_function_area",
            "callout_or_macro_evidence",
            "short_explanation",
        ],
        "product_occupancy": [58, 66],
        "product_anchor": "RIGHT_CENTER_WITH_LEFT_EVIDENCE_PATH",
        "composition_axis": "CURVED_DIAGONAL_FEATURE_CONNECTION",
        "title_region_pct": {"x": 6, "y": 9, "width": 39, "height": 23},
        "supporting_copy_region_pct": {"x": 7, "y": 34, "width": 34, "height": 12},
        "evidence_or_offer_region_pct": {"x": 5, "y": 49, "width": 40, "height": 28},
        "keyword_emphasis_method": ["SCALE_UP", "OUTLINE_OR_COLOR"],
        "graphic_device_family": [
            "FUNCTION_CURVE",
            "CALLOUT_LINE",
            "LOCAL_MACRO_FRAME",
        ],
        "graphic_device_limit": 3,
        "background_material": [
            "LIGHT_GRAY_ARCHITECTURAL_SURFACE",
            "SUBTLE_TRANSLUCENT_EVIDENCE_PANEL",
        ],
        "background_luminance": "LIGHT_MEDIUM",
        "information_density": "MEDIUM",
        "lighting_structure": "SOFT_FRONTAL_KEY + SIDE_EDGE_SEPARATION + LOCAL_EVIDENCE_HIGHLIGHT",
        "title_structure": "LARGE_FEATURE_KEYWORD + SHORT_SUPPORTING_EXPLANATION",
        "difference_from_previous_page": "Switch from editorial identity split to a curved feature-evidence path and closer product scale.",
        "evidence_pointer_policy": "CONNECT_ONLY_TO_VISIBLE_LUMBAR_OR_MESH_TARGET; OMIT_IF_AMBIGUOUS",
    },
    3: {
        "layout_archetype": "SCENE_AIRY",
        "reading_path": [
            "light_scene_headline",
            "product_in_use_context",
            "two_or_three_scene_labels",
            "supporting_line",
        ],
        "product_occupancy": [46, 56],
        "product_anchor": "LOWER_RIGHT_IN_OPEN_SCENE",
        "composition_axis": "OPEN_DIAGONAL_DEPTH",
        "title_region_pct": {"x": 7, "y": 8, "width": 46, "height": 18},
        "supporting_copy_region_pct": {"x": 8, "y": 28, "width": 40, "height": 10},
        "evidence_or_offer_region_pct": {"x": 7, "y": 72, "width": 48, "height": 14},
        "keyword_emphasis_method": ["SPACED_EDITORIAL_TYPE", "LABELIZATION"],
        "graphic_device_family": [
            "SCENE_LABEL_CLUSTER",
            "AIRY_ZONE_MARKER",
        ],
        "graphic_device_limit": 2,
        "background_material": [
            "WARM_NEUTRAL_OFFICE_OR_HOME_SPACE",
            "LIGHT_WOOD_FURNITURE_ACCENT",
            "SOFT_ARCHITECTURAL_DEPTH",
        ],
        "background_luminance": "LIGHT",
        "information_density": "LOW",
        "lighting_structure": "BROAD_WINDOW_KEY + SOFT_AMBIENT_FILL + NATURAL_CONTACT_SHADOW",
        "title_structure": "LIGHT_SINGLE_HEADLINE + SPACED_SCENE_LABELS",
        "difference_from_previous_page": "Reduce text weight and product scale; open the background into an airy use-scene depth.",
        "evidence_pointer_policy": "NO_TECHNICAL_POINTERS_ON_SCENE_PAGE",
    },
    4: {
        "layout_archetype": "DETAIL_TECH_GRID",
        "reading_path": [
            "restrained_proof_headline",
            "numbered_evidence_grid",
            "local_macro_frames",
            "short_fact_labels",
        ],
        "product_occupancy": [54, 63],
        "product_anchor": "CENTER_LEFT_WITH_RIGHT_TECH_GRID",
        "composition_axis": "MODULAR_ORTHOGONAL_TECH_GRID",
        "title_region_pct": {"x": 6, "y": 7, "width": 42, "height": 16},
        "supporting_copy_region_pct": {"x": 7, "y": 24, "width": 37, "height": 9},
        "evidence_or_offer_region_pct": {"x": 52, "y": 22, "width": 42, "height": 58},
        "keyword_emphasis_method": ["NUMBERED_LABEL", "ICON_LABEL"],
        "graphic_device_family": [
            "NUMBERED_TECH_GRID",
            "INDEPENDENT_EVIDENCE_CARD",
            "LOCAL_MACRO_FRAME",
        ],
        "graphic_device_limit": 3,
        "background_material": [
            "COOL_LIGHT_GRAY_TECHNICAL_PLANE",
            "SEMI_TRANSPARENT_GRID_PANEL",
        ],
        "background_luminance": "MEDIUM_LIGHT",
        "information_density": "HIGH",
        "lighting_structure": "CONTROLLED SIDE_KEY + CRISP EDGE_LIGHT + MATCHED_LOCAL_MACRO_LIGHT",
        "title_structure": "RESTRAINED_PROOF_TITLE + NUMBERED_EVIDENCE_GRID",
        "difference_from_previous_page": "Replace airy scene depth with a warm-neutral orthogonal evidence grid and independently labeled proof frames.",
        "evidence_pointer_policy": "NO_CONNECTORS; EACH_CARD_MUST_SHOW_THE_EXACT_LABELED_COMPONENT",
    },
    5: {
        "layout_archetype": "OFFER_NUMERIC_HERO",
        "reading_path": [
            "product_identity_line",
            "numeric_offer",
            "offer_explanation",
            "product_identity",
            "tiered_verified_benefits",
            "disclaimer",
        ],
        "product_occupancy": [52, 61],
        "product_anchor": "RIGHT_CENTER_IDENTITY_HERO",
        "composition_axis": "LEFT_NUMERIC_STACK_RIGHT_PRODUCT",
        "title_region_pct": {"x": 6, "y": 7, "width": 41, "height": 17},
        "supporting_copy_region_pct": {"x": 7, "y": 39, "width": 37, "height": 10},
        "evidence_or_offer_region_pct": {"x": 5, "y": 20, "width": 42, "height": 55},
        "keyword_emphasis_method": ["NUMERIC_SPLIT_SCALE", "TIERED_COLOR_BLOCK"],
        "graphic_device_family": [
            "NUMERIC_BADGE",
            "TIERED_OFFER_MODULE",
            "ASYMMETRIC_TICKET_CUT",
        ],
        "graphic_device_limit": 3,
        "background_material": [
            "WARM_OFF_WHITE_COMMERCIAL_SURFACE",
            "RESTRAINED_OFFER_COLOR_INFORMATION_ZONE",
            "LIGHT_GRAY_PRODUCT_STAGE",
        ],
        "background_luminance": "LIGHT_MEDIUM",
        "information_density": "MEDIUM_HIGH",
        "lighting_structure": "SOFT PRODUCT_KEY + CLEAN RIM_SEPARATION + CONTROLLED_OFFER_ZONE_GLOW",
        "title_structure": "IDENTITY_LINE + LARGE_NUMERIC_HERO + TIERED_BENEFITS",
        "difference_from_previous_page": "Switch from technical grid to a numeric offer hierarchy while keeping the product identity visible.",
        "evidence_pointer_policy": "NO_PRODUCT_POINTERS; OFFER_MODULES_MUST_REMAIN_OUTSIDE_PRODUCT",
    },
}

SPEC_TRUST_SUMMARY_PROTOTYPE: dict[str, Any] = {
    "layout_archetype": "SPEC_TRUST_SUMMARY",
    "reading_path": [
        "product_identity_line",
        "summary_headline",
        "product_identity",
        "verified_specification_grid",
        "verified_use_or_storage_note",
    ],
    "product_occupancy": [52, 61],
    "product_anchor": "RIGHT_CENTER_IDENTITY_HERO",
    "composition_axis": "LEFT_SPEC_GRID_RIGHT_PRODUCT",
    "title_region_pct": {"x": 6, "y": 7, "width": 41, "height": 17},
    "supporting_copy_region_pct": {"x": 7, "y": 27, "width": 37, "height": 10},
    "evidence_or_offer_region_pct": {"x": 5, "y": 40, "width": 42, "height": 37},
    "keyword_emphasis_method": ["SPEC_NUMBER_HIERARCHY", "LABELIZATION"],
    "graphic_device_family": [
        "VERIFIED_SPEC_GRID",
        "MONOLINE_INFO_ICON",
        "SUMMARY_DIVIDER",
    ],
    "graphic_device_limit": 3,
    "background_material": [
        "WARM_OFF_WHITE_COMMERCIAL_SURFACE",
        "LIGHT_GRAY_PRODUCT_STAGE",
        "RESTRAINED_CATEGORY_MATERIAL_ACCENT",
    ],
    "background_luminance": "LIGHT_MEDIUM",
    "information_density": "MEDIUM_HIGH",
    "lighting_structure": "SOFT PRODUCT_KEY + CLEAN RIM_SEPARATION + GROUNDED_CONTACT_SHADOW",
    "title_structure": "IDENTITY_LINE + SUMMARY_HEADLINE + VERIFIED_SPEC_GRID",
    "difference_from_previous_page": "Switch from technical proof to a concise verified specification and trust summary without inventing an offer.",
    "evidence_pointer_policy": "NO_PRODUCT_POINTERS; SPECIFICATIONS_MUST_BE VISIBLE_FACT_BOUND",
}


TYPE_RATIOS = {
    "body_text": 1.0,
    "main_title_to_body": 3.6,
    "subtitle_to_main_title": 0.40,
    "label_to_main_title": 0.26,
    "disclaimer_to_main_title": 0.16,
    "promotion_number_to_main_title": 1.55,
    "promotion_percent_or_unit_to_number": 0.50,
}


def _visible_lines(copy_plan: dict[str, Any]) -> dict[str, list[str]]:
    """Return complete phrases only; never slice strings.

    `emphasized_keyword` is a typography slot, not a selling-point-count gate.
    The complete claims remain in factual content and later v0.1.17 routing.
    """
    result: dict[str, list[str]] = {}
    for key in (
        "identity_line",
        "hero_headline",
        "emphasized_keyword",
        "supporting_line",
        "numeric_offer",
        "disclaimer",
    ):
        value = copy_plan.get(key)
        result[key] = [value] if isinstance(value, str) and value.strip() else []
    result["evidence_labels"] = [
        x for x in copy_plan.get("evidence_labels", []) if isinstance(x, str) and x.strip()
    ]
    if not result["emphasized_keyword"]:
        fallback = next(
            (
                lines[0]
                for key in ("hero_headline", "identity_line", "supporting_line", "evidence_labels")
                if (lines := result.get(key))
            ),
            "本页核心信息",
        )
        result["emphasized_keyword"] = [fallback]
    return result


def _region_valid(region: dict[str, int]) -> bool:
    return (
        all(isinstance(region.get(k), int) for k in ("x", "y", "width", "height"))
        and 0 <= region["x"] <= 100
        and 0 <= region["y"] <= 100
        and 1 <= region["width"] <= 100
        and 1 <= region["height"] <= 100
        and region["x"] + region["width"] <= 100
        and region["y"] + region["height"] <= 100
    )


def _set_gates(pages: list[dict[str, Any]]) -> dict[str, bool]:
    title_structures = [x["title_structure"] for x in pages]
    occupancy_buckets = [
        "LARGE" if x["product_occupancy"][0] >= 64 else
        "SMALL" if x["product_occupancy"][1] <= 56 else "MEDIUM"
        for x in pages
    ]
    axes = [x["composition_axis"] for x in pages]
    emphasis = {m for x in pages for m in x["keyword_emphasis_method"]}
    devices = {d for x in pages for d in x["graphic_device_family"]}
    backgrounds = [
        (
            tuple(x["background_material"]),
            x["background_luminance"],
            x["information_density"],
        )
        for x in pages
    ]
    adjacent_unique = all(
        pages[i]["layout_archetype"] != pages[i - 1]["layout_archetype"]
        and title_structures[i] != title_structures[i - 1]
        for i in range(1, len(pages))
    )
    return {
        "at_least_three_title_structures": len(set(title_structures)) >= min(3, len(pages)),
        "at_least_three_product_scales": len(set(occupancy_buckets)) >= min(3, len(pages)),
        "at_least_three_composition_axes": len(set(axes)) >= min(3, len(pages)),
        "at_least_two_keyword_emphasis_methods": len(emphasis) >= min(2, len(pages)),
        "at_least_three_graphic_devices": len(devices) >= min(3, len(pages)),
        "adjacent_pages_use_different_layout_and_title_skeletons": adjacent_unique,
        "background_material_luminance_density_have_rhythm": len(set(backgrounds)) >= min(3, len(pages)),
        "all_regions_are_bounded_percent_boxes": all(
            _region_valid(x[name])
            for x in pages
            for name in (
                "title_region_pct",
                "supporting_copy_region_pct",
                "evidence_or_offer_region_pct",
            )
        ),
        "all_pages_limit_keywords_to_one_or_two": all(
            1 <= len(x["semantic_line_break_plan"].get("emphasized_keyword", [])) <= 2
            for x in pages
        ),
        "semantic_line_breaks_use_complete_copy_units": all(
            all(isinstance(line, str) and line.strip() for lines in x["semantic_line_break_plan"].values() for line in lines)
            for x in pages
        ),
        "product_identity_contract_has_priority": all(
            x["product_identity_priority"] == "HARD_LOCK_BEFORE_OVERLAY" for x in pages
        ),
        "all_pages_share_one_campaign_style_lock": len(
            {x["campaign_style_lock_id"] for x in pages}
        ) == 1,
        "detail_proof_uses_unambiguous_evidence_binding": (
            pages[3]["evidence_pointer_policy"] in {
                "NO_CONNECTORS; EACH_CARD_MUST_SHOW_THE_EXACT_LABELED_COMPONENT",
                "NO_CONNECTORS; REAL_PHOTO_ISLANDS_MUST_BE FACT_BOUND",
            }
            and bool(pages[3].get("evidence_bindings"))
            and [x["label"] for x in pages[3]["evidence_bindings"]]
            == pages[3]["semantic_line_break_plan"].get("evidence_labels", [])
            and "CALLOUT_LINE" not in pages[3]["graphic_device_family"]
        ),
    }


def _campaign_style_lock(
    raw: dict[str, Any],
    design_contract: dict[str, Any],
) -> dict[str, Any]:
    """Bind one set-level style to the current product, never a prior fixture."""
    lock = copy.deepcopy(BASE_CAMPAIGN_STYLE_LOCK)
    category = str(raw["product"].get("category", "")).strip()
    color_system = design_contract.get("color_system", {})
    accent = color_system.get("accent_color", "PRODUCT_DERIVED_ACCENT")
    offer = color_system.get("offer_color", "PRODUCT_DERIVED_OFFER_ACCENT")
    category_tag = {
        "食品": "FOOD",
        "美妆个护": "BEAUTY",
        "小家电": "SMALL_APPLIANCE",
        "数码电子": "ELECTRONICS",
        "家具": "FURNITURE",
        "家居日用": "HOME_DAILY",
    }.get(category, "GENERAL")
    lock["lock_id"] = f"{category_tag}_PRODUCT_DERIVED_CAMPAIGN_V015"
    lock["common_accent"] = f"CURRENT_PRODUCT_ACCENT:{accent}"
    lock["offer_accent_exception"] = f"CURRENT_PRODUCT_OFFER_ACCENT:{offer}; VERIFIED_OFFERS_ONLY"
    lock["border_system"] = "CONSISTENT_THIN_PRODUCT_ACCENT_OR_NEUTRAL_HAIRLINE"
    lock["shared_device_motif"] = "CURRENT_PRODUCT_PACKAGING_OR_FORM_DERIVED_MOTIF"
    lock["product_identity_palette_is_immutable"] = True
    if category == "食品":
        lock["background_base"] = [
            "WARM_IVORY",
            "DARK_STONE_OR_WARM_TABLETOP",
            "PRODUCT_PACKAGING_DERIVED_COLOR_PLANE",
        ]
        lock["information_surface"] = "WARM_IVORY_OR_TRANSLUCENT_CREAM"
        lock["icon_system"] = "CONSISTENT_MONOLINE_FOOD_INFORMATION_ICON"
    return lock


def build_design_prototype_overlay(
    raw: dict[str, Any],
    identity_contract: dict[str, Any],
    design_contract: dict[str, Any],
    base_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(base_pages) != 5:
        raise ValueError("v0.1.5 five-carousel overlay requires exactly five pages")
    design_by_index = {p["index"]: p for p in design_contract["pages"]}
    pages: list[dict[str, Any]] = []
    has_verified_offer = bool(raw["product"].get("verified_offers"))
    campaign_style_lock = _campaign_style_lock(raw, design_contract)
    is_food = str(raw["product"].get("category", "")).strip() == "食品"
    for base in base_pages:
        index = base["index"]
        page = copy.deepcopy(
            SPEC_TRUST_SUMMARY_PROTOTYPE
            if index == 5 and not has_verified_offer
            else PROTOTYPES[index]
        )
        copy_plan = design_by_index[index]["copy_plan"]
        if is_food:
            page.update(copy.deepcopy(FOOD_PAGE_BACKGROUNDS[index]))
            page.update(copy.deepcopy(FOOD_LAYOUT_OVERRIDES.get(index, {})))
            if index == 5:
                page["layout_archetype"] = (
                    "LEFT_PRODUCT_RIGHT_NUMERIC_OFFER"
                    if has_verified_offer
                    else "LEFT_PRODUCT_RIGHT_NUMERIC_SPEC"
                )
        if index == 4:
            labels = _visible_lines(copy_plan)["evidence_labels"]
            page["evidence_bindings"] = [
                {
                    "label": label,
                    "required_visual_target": f"CURRENT_PRODUCT_SOURCE_BOUND_EVIDENCE_FOR:{label}",
                }
                for label in labels
            ]
        page.update(
            {
                "page_index": index,
                "page_role": base["page_role"],
                "semantic_line_break_plan": _visible_lines(copy_plan),
                "typography_scale_ratios": copy.deepcopy(TYPE_RATIOS),
                "product_identity_priority": "HARD_LOCK_BEFORE_OVERLAY",
                "campaign_style_lock_id": campaign_style_lock["lock_id"],
                "non_factual_visual_fields_only": True,
                "field_sources": {
                    "layout_archetype": "ENGINEERED_DEFAULT",
                    "reading_path": "ENGINEERED_DEFAULT",
                    "product_occupancy": "ENGINEERED_DEFAULT",
                    "product_anchor": "ENGINEERED_DEFAULT",
                    "composition_axis": "ENGINEERED_DEFAULT",
                    "title_region_pct": "ENGINEERED_DEFAULT",
                    "supporting_copy_region_pct": "ENGINEERED_DEFAULT",
                    "evidence_or_offer_region_pct": "ENGINEERED_DEFAULT",
                    "semantic_line_break_plan": "SAFE_COMPRESSION_OF_USER_FACT",
                    "typography_scale_ratios": "ENGINEERED_DEFAULT",
                    "keyword_emphasis_method": "ENGINEERED_DEFAULT",
                    "graphic_device_family": "ENGINEERED_DEFAULT",
                    "graphic_device_limit": "ENGINEERED_DEFAULT",
                    "background_material": "ENGINEERED_DEFAULT",
                    "lighting_structure": "ENGINEERED_DEFAULT",
                    "difference_from_previous_page": "ENGINEERED_DEFAULT",
                },
            }
        )
        pages.append(page)
    gates = _set_gates(pages)
    overlay = {
        "schema_version": "design-prototype-overlay-0.1.5",
        "request_id": raw["request_id"],
        "status": "READY" if all(gates.values()) else "BLOCKED",
        "source_precedence": SOURCE_PRECEDENCE,
        "governance": {
            "overlay_scope": "NON_FACTUAL_VISUAL_FIELDS_ONLY",
            "v1_usage": "EXPERIMENTAL_PATTERN_OR_NON_BINDING_REFERENCE_ONLY",
            "v2_1_furniture_and_carousel_status": "ENGINEERED_DEFAULT",
            "blocked_research_capabilities": [
                "FREEZE_REAL_CAROUSEL_RHYTHM",
                "FREEZE_FURNITURE",
            ],
            "experimental_patterns_enabled": False,
            "category_color_copy_fact_carryover": False,
            "previous_generated_images_as_visual_target": False,
            "profile01_semantics_read": False,
        },
        "identity_contract_binding": {
            "contract_id": identity_contract["contract_id"],
            "priority": "HARD_LOCK_BEFORE_OVERLAY",
            "overlay_may_change_product_identity": False,
        },
        "campaign_style_lock": campaign_style_lock,
        "pages": pages,
        "set_gates": gates,
        "generation_performed": False,
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    ).validate(overlay)
    return overlay


def overlay_prompt_block(page: dict[str, Any]) -> str:
    """Compact executable suffix; all factual copy stays in the copy contract."""
    return (
        "【DESIGN PROTOTYPE OVERLAY — NON-FACTUAL VISUAL EXECUTION】\n"
        f"layout_archetype={page['layout_archetype']}；"
        f"reading_path={json.dumps(page['reading_path'], ensure_ascii=False)}；"
        f"product_occupancy={page['product_occupancy']}%；"
        f"product_anchor={page['product_anchor']}；"
        f"composition_axis={page['composition_axis']}。\n"
        f"title_region_pct={json.dumps(page['title_region_pct'], ensure_ascii=False)}；"
        f"supporting_copy_region_pct={json.dumps(page['supporting_copy_region_pct'], ensure_ascii=False)}；"
        f"evidence_or_offer_region_pct={json.dumps(page['evidence_or_offer_region_pct'], ensure_ascii=False)}。\n"
        f"semantic_line_break_plan={json.dumps(page['semantic_line_break_plan'], ensure_ascii=False)}；"
        f"typography_scale_ratios={json.dumps(page['typography_scale_ratios'], ensure_ascii=False)}；"
        f"keyword_emphasis_method={json.dumps(page['keyword_emphasis_method'], ensure_ascii=False)}。\n"
        f"graphic_device_family={json.dumps(page['graphic_device_family'], ensure_ascii=False)}；"
        f"graphic_device_limit={page['graphic_device_limit']}；"
        f"background_material={json.dumps(page['background_material'], ensure_ascii=False)}；"
        f"lighting_structure={page['lighting_structure']}；"
        f"difference_from_previous_page={page['difference_from_previous_page']}。\n"
        f"campaign_style_lock_id={page['campaign_style_lock_id']}；"
        f"evidence_pointer_policy={page['evidence_pointer_policy']}。\n"
        "Campaign Style 只统一字体家族、字形处理、颜色角色、材质、灯光和共享图形母题；"
        "严禁把风格参考图的版式骨架、商品位置、商品尺度、标题位置、阅读路径或卡片拓扑复制到本页。"
        "必须执行本页 layout_archetype、product_anchor、composition_axis 和 region 百分比。\n"
        "本模块只控制非事实视觉字段；不得覆盖 Product Identity、USER_FACT、合规或文案完整性。"
        "商品颜色、结构和部件身份优先；版式冲突时降低装饰复杂度。"
        "source_type=ENGINEERED_DEFAULT"
    )
