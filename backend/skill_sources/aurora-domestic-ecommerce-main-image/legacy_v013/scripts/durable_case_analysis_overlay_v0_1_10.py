#!/usr/bin/env python3
"""Minimal durable-case visual overlay layered on the v0.1.7 compiler."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


PATTERN_ID = "DURABLE_CASE_ANALYSIS_OVERLAY_V0_1_10"

VISUAL_SOURCE_FILES = {
    "durable-goods-five-prototype-bundle.json",
    "durable-campaign-visual-dna.json",
    "durable-subcategory-overlays.json",
}

STABLE_TO_CASE_ROLE = {
    "CAROUSEL_SLOT_1_IDENTITY": "DURABLE_IDENTITY_HERO",
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT": "CORE_FUNCTION_EVIDENCE",
    "CAROUSEL_SLOT_3_SCENE_BENEFIT": "SPACE_USE_SCENE",
    "CAROUSEL_SLOT_4_DETAIL_PROOF": "STRUCTURE_DETAIL_PROOF",
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY":
        "CONFIGURATION_TRUST_SUMMARY",
}

PROMPT_EXECUTION_FIELDS = [
    "layout_archetype",
    "reading_path",
    "product_occupancy_range",
    "product_anchor",
    "product_view",
    "composition_axis",
    "title_region_pct",
    "supporting_copy_region_pct",
    "evidence_or_offer_region_pct",
    "semantic_line_break_plan",
    "typography_scale_ratios",
    "headline_structure",
    "keyword_emphasis_method",
    "graphic_device_family",
    "graphic_device_limit",
    "background_system",
    "background_material",
    "background_tone",
    "lighting_structure",
    "product_separation_method",
    "evidence_topology",
    "information_module_topology",
    "information_density",
    "difference_from_other_variants",
]

SUBCATEGORY_ROUTES = [
    (
        "ERGONOMIC_SEATING",
        ["人体工学椅", "工学椅", "办公椅", "电竞椅", "护腰椅"],
    ),
    (
        "STORAGE_CART_RACK",
        [
            "收纳推车",
            "收纳车",
            "小推车",
            "打印机置物架",
            "文件整理架",
            "移动置物架",
            "移动层架",
        ],
    ),
    (
        "SOFA_RECLINER",
        ["沙发", "躺椅", "休闲椅", "功能座椅", "功能椅", "recliner"],
    ),
    (
        "TABLE_CABINET_SHELF",
        ["桌柜架", "书架", "柜", "办公桌", "置物架", "层架", "货架"],
    ),
]

EXPRESSION_FAMILIES = {
    "ERGONOMIC_SEATING": "HUMAN_SUPPORT_AND_ADJUSTMENT",
    "STORAGE_CART_RACK": "SPATIAL_ORGANIZATION_AND_ACCESS",
    "SOFA_RECLINER": "BODY_COMFORT_AND_TRANSFORMATION",
    "TABLE_CABINET_SHELF": "STRUCTURE_CAPACITY_AND_ACCESS",
}

SHOT_REUSE_CONTRACT = {
    "exact_or_near_duplicate_complete_product_frame_reuse": 0,
    "same_complete_product_crop_reuse": 0,
    "same_camera_family_max_pages": 2,
    "same_camera_family_exception": (
        "only on non-adjacent pages when scale, product state, scene and "
        "layout family are all materially different"
    ),
    "style_reference_allowed_fields": [
        "typography family and treatment",
        "color roles",
        "information-surface material",
        "graphic geometry",
        "lighting and photography finish",
    ],
    "style_reference_forbidden_fields": [
        "product pose or camera angle",
        "product crop or scale",
        "loaded contents or operating state",
        "scene geometry",
        "subject anchor",
        "page skeleton",
    ],
    "conflict_action": (
        "reroute view, scale, product state, scene or layout before generation"
    ),
}

STORAGE_ROLE_EXECUTION = {
    "CAROUSEL_SLOT_1_IDENTITY": {
        "layout_archetype": "UNDER_DESK_SPATIAL_FIT_HERO",
        "reading_path": (
            "small identity → complete cart fitted under a desk → reclaimed "
            "desktop and floor space"
        ),
        "product_occupancy_range": [58, 68],
        "product_anchor": "LOWER_RIGHT_UNDER_DESK_FIT",
        "product_view": "NEAR_FRONTAL_OR_SLIGHT_SIDE_COMPLETE_PRODUCT",
        "view_selection_policy": (
            "score only current-product views that show all tiers, the open access "
            "direction, the complete rolling base and a credible under-desk fit; "
            "prefer the highest-scoring near-frontal or slight-side view rather "
            "than inheriting a previous product's camera"
        ),
        "hero_feature_visibility_targets": [
            "complete tier structure",
            "open access direction",
            "rolling base",
        ],
        "hero_feature_visibility_policy": (
            "show spatial fit and tier access through the complete product and "
            "real workstation relation; reserve component details for later pages"
        ),
        "product_salience_policy": (
            "the cart remains the largest coherent visual mass; the desk frames "
            "the fit relationship but stays lower in contrast and sharpness; "
            "all tier edges, openings and wheels must remain legible"
        ),
        "background_competition_limit": "LOW",
        "composition_axis": "DESK_EDGE_TO_CART_VERTICAL_AXIS",
        "title_region_pct": [5, 9, 34, 16],
        "supporting_copy_region_pct": [5, 27, 30, 10],
        "evidence_or_offer_region_pct": [0, 0, 0, 0],
        "headline_structure": (
            "small identity line + concise space-value headline; no feature "
            "list and no scene-card strip"
        ),
        "keyword_emphasis_method": (
            "one complete space-value phrase with restrained accent color"
        ),
        "graphic_device_family": [
            "architectural desk-edge frame",
            "restrained spatial clearance cue",
        ],
        "graphic_device_limit": 2,
        "background_system": "BRIGHT_ACTIVE_OFFICE_SPATIAL_FIT",
        "background_material": (
            "light neutral workstation, matte floor and low-contrast office "
            "storage outside the product silhouette"
        ),
        "background_tone": "clean light-neutral office with restrained cool accent",
        "lighting_structure": (
            "soft office daylight with local edge separation around every tier"
        ),
        "product_separation_method": (
            "background-value contrast, grounded contact shadow and clean tier edges"
        ),
        "evidence_topology": (
            "one complete current cart lightly loaded and credibly fitted under "
            "one workstation; no inset, no feature callout and no repeated scene grid"
        ),
        "information_module_topology": "identity and one space-value statement only",
        "information_density": "LOW",
        "difference_from_other_variants": (
            "opens with spatial fit and complete-product identity, not a generic "
            "centered hero or chair-style scene"
        ),
    },
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT": {
        "layout_archetype": "OPEN_SYNTHESIS_FEATURE_PROOF",
        "reading_path": "SYNTHESIZE_FROM_CLAIM_EVIDENCE_RELATION",
        "product_occupancy_range": [54, 76],
        "product_anchor": "SYNTHESIZE_FOR_VISIBLE_PROOF",
        "product_view": "AUTO_SCORE_FOR_VISIBLE_PROOF",
        "composition_axis": "SYNTHESIZE_FROM_PROOF_TOPOLOGY",
        "title_region_pct": [5, 5, 90, 18],
        "supporting_copy_region_pct": [5, 20, 90, 10],
        "evidence_or_offer_region_pct": [5, 28, 90, 67],
        "headline_structure": (
            "synthesize one role-specific hierarchy from the verified claim"
        ),
        "keyword_emphasis_method": (
            "synthesize one or two claim-relevant emphasis operations"
        ),
        "graphic_device_family": [
            "current-product photographic proof substrate",
            "optional evidence clarification device",
        ],
        "graphic_device_limit": 2,
        "background_system": "SYNTHESIZE_FOR_CATEGORY_AND_CLAIM",
        "background_material": "category-compatible photographic proof environment",
        "background_tone": "campaign-consistent with clear product separation",
        "lighting_structure": "synthesize light that reveals the claim-bearing relation",
        "product_separation_method": "background value, edge light and grounded shadow",
        "evidence_topology": (
            "derive a concrete photographic proof topology from the current verified "
            "claim, current-product evidence and buyer objection; do not choose a "
            "layout merely because it was previously used for this category"
        ),
        "information_module_topology": (
            "synthesize only after the photographic proof relation is defined"
        ),
        "information_density": "MEDIUM",
        "difference_from_other_variants": (
            "must differ from the preceding page in skeleton, view or crop, product "
            "state, scene geometry and reading path while keeping campaign style"
        ),
    },
    "CAROUSEL_SLOT_3_SCENE_BENEFIT": {
        "layout_archetype": "CONTINUOUS_SPATIAL_TRANSITION_STAGE",
        "reading_path": (
            "office-print zone → continuous spatial transition → home-study zone "
            "→ narrow-space storage zone"
        ),
        "product_occupancy_range": [30, 54],
        "product_anchor": "FLOWING_MULTI_POSITION_SPATIAL_PATH",
        "product_view": "FRONT_THREE_QUARTER_SIDE_SEQUENCE",
        "composition_axis": "CONTINUOUS_S_CURVE_SPATIAL_FLOW",
        "title_region_pct": [5, 5, 44, 14],
        "supporting_copy_region_pct": [5, 19, 40, 8],
        "evidence_or_offer_region_pct": [4, 30, 92, 66],
        "headline_structure": "light headline + two or three complete scene labels",
        "keyword_emphasis_method": "scene labels anchored directly to spatial zones",
        "graphic_device_family": [
            "continuous architectural transition",
            "restrained directional light path",
        ],
        "graphic_device_limit": 2,
        "background_system": "CONTINUOUS_VERIFIED_CONTEXT_ENVIRONMENT",
        "background_material": (
            "one uninterrupted photographic environment that naturally transitions "
            "from office print area to home study and narrow-space storage"
        ),
        "background_tone": "light neutral campaign family with scene-specific accents",
        "lighting_structure": "one coherent soft-daylight direction across all spatial zones",
        "product_separation_method": "scene-specific local contrast around the cart",
        "evidence_topology": (
            "three genuinely different photographic use contexts connected inside "
            "one continuous spatial story; each product appearance uses a different "
            "supported view and loading state; no duplicated hero frame and no "
            "separate scene windows"
        ),
        "information_module_topology": (
            "one headline plus up to three compact complete labels placed beside "
            "their corresponding spatial zones; no cards or framed scene modules"
        ),
        "information_density": "MEDIUM",
        "difference_from_other_variants": (
            "changes to a continuous panoramic context story with multiple product "
            "positions and no card-based scene segmentation"
        ),
    },
    "CAROUSEL_SLOT_4_DETAIL_PROOF": {
        "layout_archetype": "ACCESS_HANDLING_EVIDENCE_STAGE",
        "reading_path": "top-down access proof → side handling proof → low wheel proof",
        "product_occupancy_range": [42, 62],
        "product_anchor": "OFF_CENTER_DETAIL_CLUSTER",
        "product_view": "TOP_DOWN_SIDE_AND_LOW_DETAIL_SEQUENCE",
        "composition_axis": "STEPPED_DETAIL_AXIS",
        "title_region_pct": [6, 5, 42, 14],
        "supporting_copy_region_pct": [6, 18, 40, 8],
        "evidence_or_offer_region_pct": [5, 29, 90, 66],
        "headline_structure": "technical headline + graded photographic evidence labels",
        "keyword_emphasis_method": "numbered photographic evidence with one dominant detail",
        "graphic_device_family": [
            "graded photographic detail frames",
            "short source-bound labels",
        ],
        "graphic_device_limit": 2,
        "background_system": "CLEAN_COMPONENT_PROOF_STAGE",
        "background_material": "neutral matte proof surface with soft architectural depth",
        "background_tone": "light cool-neutral",
        "lighting_structure": "directional soft light revealing molded edges and openings",
        "product_separation_method": "micro-shadow and edge definition without recoloring",
        "evidence_topology": (
            "one dominant real access view plus smaller current-product handle, "
            "guardrail or wheel evidence only when bound; no repeated full-cart hero"
        ),
        "information_module_topology": "one large proof and two unequal subordinate proofs",
        "information_density": "MEDIUM_HIGH",
        "difference_from_other_variants": (
            "drops the complete-product hero and moves to access, handling and mobility evidence"
        ),
    },
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": {
        "layout_archetype": "SPACE_RECLAIMED_VALUE_CLOSE",
        "reading_path": "reclaimed workspace → mobile cart transition → verified trust or offer",
        "product_occupancy_range": [44, 58],
        "product_anchor": "LOWER_LEFT_MOTION_EXIT",
        "product_view": "SIDE_OR_REAR_THREE_QUARTER_IN_MOTION",
        "composition_axis": "OPEN_SPACE_TO_PRODUCT_MOTION",
        "title_region_pct": [7, 7, 48, 15],
        "supporting_copy_region_pct": [7, 23, 42, 10],
        "evidence_or_offer_region_pct": [57, 62, 35, 24],
        "headline_structure": "value-close headline + compact verified trust or offer module",
        "keyword_emphasis_method": "one value phrase plus one restrained verified badge",
        "graphic_device_family": [
            "open-space reveal",
            "compact verified trust panel",
        ],
        "graphic_device_limit": 2,
        "background_system": "OPEN_DESK_VALUE_CLOSE",
        "background_material": "cleared workstation, matte floor and restrained office depth",
        "background_tone": "bright neutral close with small campaign accent",
        "lighting_structure": "soft directional daylight with motion-side edge light",
        "product_separation_method": "open negative space and grounded rolling shadow",
        "evidence_topology": (
            "partially loaded current cart moving beside a cleared desk; trust or "
            "offer facts stay secondary and never reuse the opening hero frame"
        ),
        "information_module_topology": "large spatial-value close plus one compact factual module",
        "information_density": "MEDIUM",
        "difference_from_other_variants": (
            "closes on reclaimed space and movement rather than another static product portrait"
        ),
    },
}

VIEW_SCORE_WEIGHTS = {
    "product_identity_clarity": 30,
    "hero_feature_visible_area": 20,
    "complete_product_coverage": 15,
    "key_component_non_overlap": 10,
    "credible_scene_interaction": 10,
    "compositional_harmony": 15,
}


def _presentation_decision_core(
    subcategory: str,
    hero_feature_targets: list[str],
) -> dict[str, Any]:
    """Return a fact-neutral decision contract shared across categories."""
    adapter_candidates = {
        "ERGONOMIC_SEATING": [
            "upright front",
            "near-frontal 8–18 degree yaw",
            "evidence-supported three-quarter",
            "evidence-supported side",
        ],
        "STORAGE_CART_RACK": [
            "front showing all tiers",
            "three-quarter showing depth and access",
            "side only when mobility or narrow-depth evidence is primary",
        ],
        "SOFA_RECLINER": [
            "front identity view",
            "three-quarter volume view",
            "side only when recline geometry is the verified focus",
        ],
        "TABLE_CABINET_SHELF": [
            "front structure view",
            "three-quarter depth-and-access view",
            "side only when depth is a verified differentiator",
        ],
    }
    return {
        "decision_order": [
            "current_product_identity",
            "page_role_and_information_density",
            "visible_feature_targets",
            "evidence_supported_view_candidates",
            "view_score",
            "scene_compatibility",
            "local_background_separation",
            "typography_and_graphic_execution",
        ],
        "view_score_weights": copy.deepcopy(VIEW_SCORE_WEIGHTS),
        "category_adapter_candidates": adapter_candidates.get(
            subcategory, ["front", "three-quarter", "side", "top or detail"]
        ),
        "hero_feature_visibility_targets": copy.deepcopy(
            hero_feature_targets
        ),
        "first_page_boundary": (
            "establish product identity in one credible primary scene; "
            "show an identity-defining feature naturally when possible; "
            "reserve macro proof, parameter grids, multi-scene modules and "
            "component callouts for later pages"
        ),
        "local_separation_policy": (
            "check local luminance or chroma separation behind every "
            "identity-defining edge; move, soften or remove competing "
            "background props before changing the product"
        ),
        "reference_roles": {
            "original_same_product": "PRODUCT_IDENTITY_AUTHORITY",
            "user_approved_generated_page": "STYLE_REFERENCE_ONLY",
            "external_angle_example": "CAMERA_GRAMMAR_ONLY",
            "other_reference": "NON_BINDING_REFERENCE",
        },
    }


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _record_by_name(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        Path(record["path"]).name: record
        for record in manifest.get("sources", [])
        if Path(record["path"]).name in VISUAL_SOURCE_FILES
    }


def _only(record: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(record[key])
        for key in allowed
        if key in record
    }


def load_visual_analysis(manifest_path: str | Path) -> dict[str, Any]:
    """Load only the three visual source files and only manifest-listed fields."""
    manifest_path = Path(manifest_path).resolve()
    manifest = _load(manifest_path)
    records = _record_by_name(manifest)
    if set(records) != VISUAL_SOURCE_FILES:
        raise ValueError("Manifest does not list all required visual sources")

    prototype_record = records[
        "durable-goods-five-prototype-bundle.json"
    ]
    dna_record = records["durable-campaign-visual-dna.json"]
    subcategory_record = records["durable-subcategory-overlays.json"]

    def source_path(record: dict[str, Any]) -> Path:
        path = Path(record["path"])
        return path if path.is_absolute() else manifest_path.parent / path

    prototype_source = _load(source_path(prototype_record))
    dna_source = _load(source_path(dna_record))
    subcategory_source = _load(source_path(subcategory_record))

    prototype_fields = prototype_record["allowed_item_fields"]
    dna_fields = dna_record["allowed_fields"]
    subcategory_fields = subcategory_record["allowed_fields"]
    allowed_subcategories = set(
        subcategory_record["allowed_subcategories"]
    )
    allowed_case_dna = set(dna_record["case_scope_only"])

    return {
        "prototypes": [
            _only(item, prototype_fields)
            for item in prototype_source.get("items", [])
        ],
        "campaign_dna": {
            case_id: _only(dna_source[case_id], dna_fields)
            for case_id in allowed_case_dna
            if case_id in dna_source
        },
        "subcategories": {
            subcategory: _only(
                subcategory_source[subcategory],
                subcategory_fields,
            )
            for subcategory in allowed_subcategories
            if subcategory in subcategory_source
        },
    }


def raw_for_stable_baseline(raw: dict[str, Any]) -> dict[str, Any]:
    """Remove only candidate overlay controls before invoking v0.1.7."""
    result = copy.deepcopy(raw)
    preference = result.get("campaign_preference")
    if not isinstance(preference, dict):
        return result
    patterns = preference.get("experimental_patterns")
    if isinstance(patterns, list):
        preference["experimental_patterns"] = [
            value for value in patterns if value != PATTERN_ID
        ]
        if not preference["experimental_patterns"]:
            preference.pop("experimental_patterns", None)
    preference.pop("durable_case_overlay", None)
    if not preference:
        result.pop("campaign_preference", None)
    return result


def _enabled(raw: dict[str, Any]) -> bool:
    preference = raw.get("campaign_preference", {})
    patterns = preference.get("experimental_patterns", [])
    return isinstance(patterns, list) and PATTERN_ID in patterns


def route_subcategory(
    raw: dict[str, Any],
    analysis: dict[str, Any],
) -> str | None:
    """Route the current product name/category into a source-listed scope."""
    product = raw.get("product", {})
    text = " ".join(
        [
            str(product.get("name", "")),
            str(product.get("category", "")),
        ]
    ).lower()
    for subcategory, terms in SUBCATEGORY_ROUTES:
        if subcategory not in analysis["subcategories"]:
            continue
        if any(term.lower() in text for term in terms):
            return subcategory
    return None


def route_case_dna(
    subcategory: str,
    raw: dict[str, Any],
    identity: dict[str, Any],
    analysis: dict[str, Any],
) -> str | None:
    """Choose only the two case DNA records present in the source file."""
    if (
        subcategory == "ERGONOMIC_SEATING"
        and "ergonomic-chair" in analysis["campaign_dna"]
    ):
        return "ergonomic-chair"
    if subcategory != "STORAGE_CART_RACK":
        return None
    product = raw.get("product", {})
    color_evidence = json.dumps(
        {
            "verified_attributes": product.get(
                "verified_attributes", {}
            ),
            "identity": identity,
        },
        ensure_ascii=False,
    ).lower()
    if (
        any(
            token in color_evidence
            for token in ["白色", "纯白", "米白", "white"]
        )
        and "white-storage-cart" in analysis["campaign_dna"]
    ):
        return "white-storage-cart"
    return None


def _prototype_score(
    prototype: dict[str, Any],
    raw: dict[str, Any],
    identity: dict[str, Any],
) -> int:
    """Rank source prototypes by current-product and campaign compatibility."""
    product_context = json.dumps(
        {
            "product": raw.get("product", {}),
            "identity": identity,
        },
        ensure_ascii=False,
    ).lower()
    campaign_context = json.dumps(
        raw.get("campaign_preference", {}),
        ensure_ascii=False,
    ).lower()
    background = " ".join(
        str(prototype.get(key, ""))
        for key in [
            "background_system",
            "background_material",
            "background_tone",
        ]
    ).lower()
    score = 0

    dark_product = any(
        token in product_context
        for token in ["黑色", "深黑", "black", "dark"]
    )
    light_campaign = any(
        token in campaign_context
        for token in [
            "浅灰",
            "暖白",
            "浅木",
            "light gray",
            "warm white",
            "light wood",
        ]
    )
    if dark_product and any(
        token in background for token in ["graphite", "dark"]
    ):
        score -= 4
    if light_campaign and any(
        token in background
        for token in ["light", "warm", "room", "plaster", "wood"]
    ):
        score += 4
    if "不得改变颜色" in product_context and "high contrast" in background:
        score += 1
    return score


def _select_prototype(
    candidates: list[dict[str, Any]],
    raw: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        enumerate(candidates),
        key=lambda item: (
            _prototype_score(item[1], raw, identity),
            -item[0],
        ),
    )[1]


def _verified_items(raw: dict[str, Any], field: str) -> list[dict[str, str]]:
    items = raw.get("product", {}).get(field, [])
    result: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            item_id = str(item.get("id", f"{field}-{index + 1}"))
        else:
            text = str(item).strip()
            item_id = f"{field}-{index + 1}"
        if text:
            result.append({"id": item_id, "text": text})
    return result


def _semantic_terms(value: Any) -> set[str]:
    terms: set[str] = set()
    for chunk in re.findall(r"[\u3400-\u9fff]{2,}", str(value)):
        for size in (2, 3, 4):
            for start in range(max(0, len(chunk) - size + 1)):
                terms.add(chunk[start : start + size])
    return terms


def _component_bindings(
    claim: str,
    identity_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    generic_terms = {
        "支撑",
        "承托",
        "舒适",
        "结构",
        "商品",
        "人体",
        "工学",
        "人体工学",
    }
    claim_terms = _semantic_terms(claim) - generic_terms
    action_terms = {
        term
        for term in {
            "升降",
            "后仰",
            "旋转",
            "调节",
            "伸缩",
            "折叠",
            "开合",
            "锁定",
            "移动",
            "滑动",
        }
        if term in claim
    }
    identity = identity_contract.get("identity", {})
    references = identity_contract.get("reference_evidence", [])
    candidates: list[dict[str, Any]] = []
    for component in identity.get("components", []):
        candidates.append(
            {
                "target_component": component.get("name"),
                "evidence_kind": "COMPONENT",
                "description": " ".join(
                    str(component.get(key, ""))
                    for key in ("name", "shape", "color")
                ),
                "evidence_indices": component.get("evidence_indices", []),
            }
        )
    for surface in identity.get("materials_and_surfaces", []):
        candidates.append(
            {
                "target_component": surface.get("region"),
                "evidence_kind": "MATERIAL_SURFACE",
                "description": " ".join(
                    str(surface.get(key, ""))
                    for key in ("region", "appearance")
                ),
                "evidence_indices": surface.get("evidence_indices", []),
            }
        )

    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for candidate in candidates:
        if action_terms and not any(
            term in candidate["description"] for term in action_terms
        ):
            continue
        raw_overlap = sorted(
            claim_terms
            & (_semantic_terms(candidate["description"]) - generic_terms),
            key=lambda value: (-len(value), value),
        )
        overlap: list[str] = []
        for term in raw_overlap:
            if not any(term in existing for existing in overlap):
                overlap.append(term)
        if overlap:
            exact_name_bonus = (
                20
                if str(candidate["target_component"]) in claim
                else 0
            )
            scored.append(
                (
                    exact_name_bonus + sum(len(value) for value in overlap),
                    candidate,
                    overlap,
                )
            )
    scored.sort(key=lambda item: -item[0])

    bindings: list[dict[str, Any]] = []
    top_score = scored[0][0] if scored else 0
    for score, candidate, overlap in scored:
        if len(bindings) >= 2 or score < max(2, int(top_score * 0.6)):
            continue
        evidence_ids: list[str] = []
        view_types: list[str] = []
        for evidence_index in candidate["evidence_indices"]:
            if not isinstance(evidence_index, int):
                continue
            position = evidence_index - 1
            if position < 0 or position >= len(references):
                continue
            reference = references[position]
            evidence_id = reference.get("evidence_id")
            if evidence_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
            for view_tag in reference.get("view_tags", []):
                if view_tag not in view_types:
                    view_types.append(view_tag)
        if evidence_ids:
            bindings.append(
                {
                    "target_component": candidate["target_component"],
                    "evidence_kind": candidate["evidence_kind"],
                    "matched_terms": overlap[:4],
                    "evidence_ids": evidence_ids,
                    "view_types": view_types,
                }
            )
    return bindings


def _proof_success_contract(
    stable_role: str,
    selected_facts: list[dict[str, Any]],
    proof_bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile what the image must visibly prove, not just what to draw."""
    claim_text = " ".join(
        str(item.get("text", "")) for item in selected_facts
    )
    targets = [
        component.get("target_component")
        for binding in proof_bindings
        for component in binding.get("component_bindings", [])
        if component.get("target_component")
    ]
    if stable_role != "CAROUSEL_SLOT_2_CORE_SELLING_POINT":
        return {
            "status": "NOT_REQUIRED_FOR_THIS_PAGE_ROLE",
            "expression_family": "ROLE_DEFAULT",
        }
    if any(
        term in claim_text
        for term in ["贴合", "人体", "腰背", "承托", "支撑", "坐感"]
    ):
        return {
            "status": "READY",
            "expression_family": "REAL_USER_PRODUCT_CONTACT_PROOF",
            "required_subjects": [
                "current product from original same-product evidence",
                "one photoreal user in an anatomically plausible posture",
                *targets[:2],
            ],
            "required_visual_relation": (
                "the user's relevant body region must physically meet the "
                "bound current-product component at believable scale, "
                "perspective and posture; the contact component must remain "
                "readable and identity-consistent"
            ),
            "success_at_thumbnail": (
                "without reading the copy, a viewer can identify the user, "
                "the target component and the physical support/contact "
                "relationship in one glance"
            ),
            "accent_role": (
                "a restrained accent may clarify an already visible contact "
                "relationship but may never substitute for it"
            ),
            "production_method": (
                "PRODUCT_PRESERVING_COMPOSITE_OR_MASKED_EDIT_REQUIRED: keep "
                "the original current-product pixels/geometry authoritative; "
                "generate or place the user and scene around the preserved "
                "product. Do not freely resynthesize the product under a user"
            ),
            "identity_postcondition": (
                "the bound component shape, material, scale, attachment and "
                "relative position must match original same-product evidence; "
                "any changed component geometry blocks the page even when the "
                "human-contact relation is visually clear"
            ),
            "forbidden_substitutes": [
                "ghost silhouette without a complete believable posture",
                "decorative glow or arc as the only proof",
                "floating or misaligned body outline",
                "person standing behind rather than using the product",
                "text-only claim",
                "pointer or highlight not anchored to the bound component",
                "freely resynthesized product under the user",
                "changed target-component shape or attachment",
            ],
        }
    if any(term in claim_text for term in ["收纳", "容量", "分层", "空间"]):
        family = "REAL_CONTENT_LOADED_CAPACITY_PROOF"
        relation = (
            "show the current product loaded with fact-compatible real "
            "objects so tier, access and usable capacity are directly visible"
        )
    elif any(
        term in claim_text
        for term in ["升降", "后仰", "旋转", "折叠", "开合", "移动"]
    ):
        family = "MULTI_STATE_FUNCTION_PROOF"
        relation = (
            "show two or more identity-consistent states of the same current "
            "product with the changed component and motion path unambiguous"
        )
    elif any(term in claim_text for term in ["材质", "面料", "纹理", "透气"]):
        family = "CURRENT_PRODUCT_MATERIAL_MACRO_PROOF"
        relation = (
            "show a source-bound macro where the claimed material region and "
            "its surface evidence are photographically readable"
        )
    else:
        family = "CURRENT_PRODUCT_COMPONENT_PROOF"
        relation = (
            "show the bound current-product component large enough to verify "
            "the claim without relying on copy or decorative effects"
        )
    return {
        "status": "READY",
        "expression_family": family,
        "required_subjects": [
            "current product from original same-product evidence",
            *targets[:2],
        ],
        "required_visual_relation": relation,
        "success_at_thumbnail": (
            "the claim-defining subject, target and relation are identifiable "
            "without reading the copy"
        ),
        "accent_role": "accent is explanatory only, never primary evidence",
        "forbidden_substitutes": [
            "decorative glow as the only proof",
            "text-only claim",
            "unbound pointer",
            "reference-product component",
        ],
    }


def _open_expression_composition_contract(
    stable_role: str,
    selected_facts: list[dict[str, Any]],
    proof_bindings: list[dict[str, Any]],
    proof_success: dict[str, Any],
) -> dict[str, Any]:
    """Expose evidence boundaries while requiring open visual synthesis."""
    facts = [
        str(item.get("text", "")).strip()
        for item in selected_facts
        if str(item.get("text", "")).strip()
    ]
    evidence_ids = sorted(
        {
            evidence_id
            for binding in proof_bindings
            for component in binding.get("component_bindings", [])
            for evidence_id in component.get("evidence_ids", [])
        }
    )
    return {
        "mode": "OPEN_SYNTHESIS_REQUIRED",
        "closed_method_enum": False,
        "role": stable_role,
        "verified_claims": facts,
        "bound_current_product_evidence_ids": evidence_ids,
        "visible_proof_goal": proof_success.get(
            "required_visual_relation",
            "make the page role and verified fact visually legible",
        ),
        "composition_dimensions": [
            "photographic proof substrate",
            "camera/view and crop",
            "product state, loading or action",
            "subject-to-product relation",
            "spatial and depth topology",
            "copy and information topology",
            "background and lighting",
            "optional evidence clarification devices",
        ],
        "candidate_prototypes_are_non_binding": True,
        "new_layout_may_be_invented": True,
        "auxiliary_graphics_policy": (
            "lines, bands, icons and labels may clarify an already visible "
            "current-product proof relation but may not replace it"
        ),
        "pre_generation_questions": [
            "What exact buyer objection must this page resolve?",
            "What relation must be visible without reading the copy?",
            "Which current-product evidence can truthfully support that relation?",
            "Which view, state and composition make that proof clearest?",
            "How does this skeleton materially differ from the preceding page?",
        ],
        "hard_success_condition": proof_success.get(
            "success_at_thumbnail",
            "the page role is understandable without relying on body copy",
        ),
        "compile_requirement": (
            "before image generation, replace OPEN/SYNTHESIZE fields with one "
            "concrete model-composed execution plan; do not pass placeholders "
            "or a menu of methods to the image model"
        ),
    }


def _shot_plan(
    expression_family: str,
    stable_role: str,
    execution_fields: dict[str, Any],
) -> dict[str, Any]:
    state_by_role = {
        "CAROUSEL_SLOT_1_IDENTITY": "LIGHTLY_LOADED_IDENTITY_STATE",
        "CAROUSEL_SLOT_2_CORE_SELLING_POINT": "FULLY_LOADED_PROOF_STATE",
        "CAROUSEL_SLOT_3_SCENE_BENEFIT": "SCENE_SPECIFIC_LOADING_STATES",
        "CAROUSEL_SLOT_4_DETAIL_PROOF": "EMPTY_OR_SINGLE_OBJECT_DETAIL_STATE",
        "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": "PARTIALLY_LOADED_MOTION_STATE",
    }
    view = str(execution_fields.get("product_view", "EVIDENCE_SUPPORTED"))
    layout = str(execution_fields.get("layout_archetype", "ROLE_SPECIFIC"))
    product_state = state_by_role.get(stable_role, "ROLE_SPECIFIC_STATE")
    return {
        "shot_key": "|".join(
            [
                expression_family,
                stable_role,
                view,
                product_state,
                layout,
            ]
        ),
        "camera_family": view,
        "product_state": product_state,
        "layout_family": layout,
        "reuse_policy": "UNIQUE_COMPLETE_PRODUCT_FRAME",
        "must_differ_from_previous": [
            "camera or crop",
            "product scale",
            "loaded contents or operating state",
            "scene geometry",
            "subject anchor",
            "page skeleton",
        ],
    }


def _feature_expression(
    stable_role: str,
    raw: dict[str, Any],
    identity_contract: dict[str, Any],
    subcategory: str | None = None,
) -> dict[str, Any]:
    selling_points = _verified_items(raw, "verified_selling_points")
    scenes = _verified_items(raw, "verified_scenes")
    product_name = str(raw.get("product", {}).get("name", "")).strip()
    if stable_role == "CAROUSEL_SLOT_1_IDENTITY":
        selected = [
            {
                "id": "product-identity",
                "text": product_name,
                "source_type": "USER_FACT",
            },
            *[
                {**item, "source_type": "USER_FACT"}
                for item in scenes[:1]
            ],
        ]
        expression_mode = (
            "SPATIAL_FIT_IDENTITY_HERO"
            if subcategory == "STORAGE_CART_RACK"
            else "SCENE_LED_IDENTITY_HERO"
        )
    elif stable_role == "CAROUSEL_SLOT_2_CORE_SELLING_POINT":
        selected = [
            {**item, "source_type": "USER_FACT"}
            for item in selling_points[:1]
        ]
        expression_mode = "COMPONENT_BOUND_FEATURE_PROOF"
    elif stable_role == "CAROUSEL_SLOT_3_SCENE_BENEFIT":
        selected = [
            {**item, "source_type": "USER_FACT"}
            for item in scenes[:3]
        ]
        expression_mode = "VERIFIED_USE_SCENE_WITH_MINIMAL_LABELS"
    elif stable_role == "CAROUSEL_SLOT_4_DETAIL_PROOF":
        selected = [
            {**item, "source_type": "USER_FACT"}
            for item in selling_points[1:4]
        ]
        expression_mode = "COMPONENT_BOUND_EVIDENCE_GRID"
    else:
        selected = []
        expression_mode = "VERIFIED_SUMMARY_NO_UNBOUND_POINTER"

    proof_bindings: list[dict[str, Any]] = []
    for item in selected:
        no_component_pointer = (
            stable_role == "CAROUSEL_SLOT_3_SCENE_BENEFIT"
            or stable_role == "CAROUSEL_SLOT_1_IDENTITY"
        )
        bindings = (
            []
            if no_component_pointer
            else _component_bindings(item["text"], identity_contract)
        )
        if stable_role == "CAROUSEL_SLOT_1_IDENTITY":
            proof_method = (
                "FULL_PRODUCT_IDENTITY_NO_COMPONENT_POINTER"
                if item["id"] == "product-identity"
                else "PHOTOGRAPHIC_USE_SCENE_NO_COMPONENT_POINTER"
            )
        elif stable_role == "CAROUSEL_SLOT_3_SCENE_BENEFIT":
            proof_method = "SCENE_CONTEXT_NO_POINTER"
        elif bindings:
            proof_method = "CURRENT_PRODUCT_MACRO_OR_CALLOUT_TO_BOUND_COMPONENT"
        else:
            proof_method = "TEXT_ONLY_VERIFIED_FACT_NO_POINTER"
        proof_bindings.append(
            {
                "fact_id": item["id"],
                "verified_fact": item["text"],
                "source_type": item["source_type"],
                "proof_method": proof_method,
                "component_bindings": bindings,
                "pointer_policy": (
                    "POINTERS_ONLY_TO_LISTED_COMPONENTS"
                    if bindings
                    else "NO_POINTERS"
                ),
            }
        )
    proof_success = _proof_success_contract(
        stable_role,
        selected,
        proof_bindings,
    )
    open_composition = _open_expression_composition_contract(
        stable_role,
        selected,
        proof_bindings,
        proof_success,
    )
    return {
        "expression_mode": expression_mode,
        "buyer_objection": "如何从当前商品证据确认该卖点",
        "selected_verified_facts": selected,
        "proof_bindings": proof_bindings,
        "proof_success_contract": proof_success,
        "open_expression_composition": open_composition,
        "visible_evidence_status": (
            "BOUND_TO_CURRENT_PRODUCT_EVIDENCE"
            if any(item["component_bindings"] for item in proof_bindings)
            else "NO_COMPONENT_POINTER_REQUIRED"
        ),
        "reference_product_facts_forbidden": True,
    }


def build_overlay_plan(
    raw: dict[str, Any],
    identity: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Select only source-recorded visual values; do not infer product facts."""
    if not _enabled(raw):
        return {"status": "INACTIVE", "page_overlays": []}

    subcategory = route_subcategory(raw, analysis)
    if subcategory is None:
        return {"status": "INACTIVE", "page_overlays": []}

    case_dna_id = route_case_dna(
        subcategory,
        raw,
        identity,
        analysis,
    )
    campaign_dna = analysis["campaign_dna"].get(case_dna_id)
    expression_family = EXPRESSION_FAMILIES.get(
        subcategory,
        "DURABLE_STRUCTURE_AND_USE",
    )
    page_overlays: list[dict[str, Any]] = []

    for stable_role, case_role in STABLE_TO_CASE_ROLE.items():
        candidates = [
            prototype
            for prototype in analysis["prototypes"]
            if prototype.get("page_role") == case_role
            and subcategory
            in prototype.get("applicable_subcategories", [])
        ]
        selected = _select_prototype(
            candidates,
            raw,
            identity,
        )
        if selected is None:
            continue
        execution_fields = _only(selected, PROMPT_EXECUTION_FIELDS)
        execution_source_type = "EXPERIMENTAL_PATTERN"
        if stable_role == "CAROUSEL_SLOT_1_IDENTITY":
            brand_only_scene_hero = subcategory == "ERGONOMIC_SEATING"
            verified_selling_points = _verified_items(
                raw, "verified_selling_points"
            )
            hero_feature_bindings = (
                _component_bindings(
                    verified_selling_points[0]["text"],
                    identity,
                )
                if verified_selling_points
                else []
            )
            hero_feature_targets = [
                item["target_component"]
                for item in hero_feature_bindings[:2]
            ]
            presentation_core = _presentation_decision_core(
                subcategory,
                hero_feature_targets,
            )
            execution_fields.update(
                {
                    "presentation_decision_core": presentation_core,
                    "layout_archetype": (
                        "BRAND_ONLY_SCENE_HERO"
                        if brand_only_scene_hero
                        else "SCENE_LED_IDENTITY_HERO"
                    ),
                    "canvas_aspect_ratio": "1:1",
                    "reading_path": (
                        "small brand identity → dominant product inside one "
                        "primary use scene"
                        if brand_only_scene_hero
                        else "small brand identity → complete category → "
                        "dominant product inside one primary use scene"
                    ),
                    "product_occupancy_range": (
                        [70, 78] if brand_only_scene_hero else [62, 70]
                    ),
                    "product_anchor": (
                        "CENTER_LOWER_ARCHITECTURAL_DEPTH"
                        if brand_only_scene_hero
                        else "RIGHT_CENTER_IN_WORKSPACE"
                    ),
                    "product_view": (
                        "AUTO_SCORED_NEAR_FRONTAL_HERO_VIEW"
                        if brand_only_scene_hero
                        else "PRIMARY_HERO_FRONT_THREE_QUARTER"
                    ),
                    "view_selection_policy": (
                        "score only views supported by current-product "
                        "evidence. For a symmetric chair identity hero, "
                        "prefer a near-frontal upright view with about "
                        "8–18 degrees of horizontal yaw and a level-to-"
                        "slightly-elevated camera when it keeps the complete "
                        "silhouette, armrests, seat, base and wheels readable "
                        "while exposing the bound hero-feature targets. "
                        "Choose a stronger three-quarter or side view only "
                        "when it produces a higher verified component-"
                        "visibility score. Rank views by: product identity "
                        "clarity, hero-feature visible area, complete-product "
                        "coverage, non-overlap of key components, scene "
                        "interaction and overall harmony. Never copy the "
                        "reference product, color or promotional modules; "
                        "transfer only the evidence-supported camera grammar"
                    ),
                    "hero_feature_visibility_targets": hero_feature_targets,
                    "hero_feature_visibility_policy": (
                        "show the bound identity-defining components "
                        "naturally inside the complete product hero; use "
                        "camera angle and elevation rather than text, macro "
                        "insets, callout lines or synthetic glow"
                    ),
                    "product_salience_policy": (
                        "the product must remain the largest coherent visual "
                        "mass and the first fixation; background props must "
                        "have lower contrast, lower sharpness or smaller "
                        "area. Enforce local luminance separation around "
                        "every identity-defining edge: headrest, backrest, "
                        "lumbar support, armrests, seat, base and wheels "
                        "must not merge into a similar-value background. "
                        "Move any dark monitor, desk edge or cabinet away "
                        "from the product silhouette rather than recoloring "
                        "the product"
                    ),
                    "background_competition_limit": "LOW_TO_MEDIUM",
                    "composition_axis": (
                        "CENTERED_ARCHITECTURAL_DEPTH"
                        if brand_only_scene_hero
                        else "WORKSPACE_DEPTH_DIAGONAL"
                    ),
                    "title_region_pct": (
                        [4, 4, 24, 10]
                        if brand_only_scene_hero
                        else [6, 7, 42, 15]
                    ),
                    "supporting_copy_region_pct": (
                        [0, 0, 0, 0]
                        if brand_only_scene_hero
                        else [6, 23, 38, 8]
                    ),
                    "evidence_or_offer_region_pct": [0, 0, 0, 0],
                    "headline_structure": (
                        "small brand mark only; no category headline and no "
                        "scene label"
                        if brand_only_scene_hero
                        else "small brand line + large complete category + "
                        "one concise use-positioning line"
                    ),
                    "keyword_emphasis_method": (
                        "none; the product and scene carry the hierarchy"
                        if brand_only_scene_hero
                        else "contrasting weight on the complete category "
                        "plus one restrained accent tab for the use "
                        "positioning"
                    ),
                    "graphic_device_family": [
                        "editorial identity tab",
                        "restrained workspace depth frame",
                    ],
                    "graphic_device_limit": 2,
                    "background_system": (
                        "COOL_NEUTRAL_ERGONOMIC_OFFICE"
                        if brand_only_scene_hero
                        else "MODERN_ERGONOMIC_WORKSPACE_STAGE"
                    ),
                    "background_material": (
                        "pearl-gray or cool-white architectural planes "
                        "directly behind the product silhouette, frosted "
                        "glass, matte aluminum, cool light-ash or light-gray "
                        "worktop receding as a low horizontal band; compact "
                        "dark monitors and cabinets must sit outside the "
                        "product silhouette; restrained active-work objects"
                        if brand_only_scene_hero
                        else "light-gray architectural office planes, pale "
                        "wood workstation edge, matte neutral floor and "
                        "restrained technical lines"
                    ),
                    "background_tone": (
                        "cool professional light-neutral with graphite "
                        "structure and a very small low-saturation blue-gray "
                        "accent; avoid dominant warm wood"
                        if brand_only_scene_hero
                        else "professional light-neutral with graphite "
                        "support and a small low-saturation blue accent"
                    ),
                    "lighting_structure": (
                        "soft architectural daylight plus restrained cool "
                        "edge separation on the black mesh silhouette and "
                        "a grounded neutral shadow"
                        if brand_only_scene_hero
                        else "soft daylight office key plus restrained cool "
                        "rim separation on the product silhouette"
                    ),
                    "product_separation_method": (
                        "verified local background-value contrast around all "
                        "key silhouette zones, precise restrained mesh edge "
                        "light, brighter neutral floor and grounded contact "
                        "shadow; never recolor the product for separation"
                    ),
                    "evidence_topology": (
                        "one dominant current-product chair in one real "
                        "active office workstation use context; the chair "
                        "must have a believable usable distance, height and "
                        "orientation relative to a desk that recedes behind "
                        "it as a low horizontal band; keep the monitor small "
                        "and behind the product; choose a clear main-image "
                        "angle while preserving current-product identity; no "
                        "left-product/right-desk split, empty showroom "
                        "staging, secondary scene windows, line icons, "
                        "component callouts or macro proof"
                        if brand_only_scene_hero
                        else "one dominant current-product chair in one real "
                        "modern office use scene; choose a clear main-image "
                        "front three-quarter angle while preserving the "
                        "current product identity; no secondary scene "
                        "windows, no line icons, no component callouts and "
                        "no macro proof"
                    ),
                    "information_module_topology": (
                        "brand identity only; no category headline, scene "
                        "label or feature copy"
                        if brand_only_scene_hero
                        else "brand identity, category and concise scene "
                        "positioning only; component benefits are reserved "
                        "for later feature and detail-proof pages"
                    ),
                    "copy_visibility_mode": (
                        "BRAND_ONLY"
                        if brand_only_scene_hero
                        else "BRAND_CATEGORY_SCENE"
                    ),
                    "information_density": (
                        "MEDIUM_SCENE_CONTEXT_LOW_COPY"
                        if brand_only_scene_hero
                        else "LOW_MEDIUM"
                    ),
                    "difference_from_other_variants": (
                        "opens with product identity inside a credible use "
                        "environment; later pages own additional use scenes, "
                        "lumbar support, adjustment and structural proof"
                    ),
                }
            )
            execution_source_type = "ENGINEERED_DEFAULT"
        if subcategory == "STORAGE_CART_RACK":
            execution_fields.update(
                copy.deepcopy(STORAGE_ROLE_EXECUTION[stable_role])
            )
            execution_source_type = "ENGINEERED_DEFAULT"
        execution_fields["expression_family"] = expression_family
        execution_fields["shot_plan"] = _shot_plan(
            expression_family,
            stable_role,
            execution_fields,
        )
        page_overlays.append(
            {
                "stable_page_role": stable_role,
                "selected_prototype_id": selected.get("prototype_id"),
                "execution_fields": execution_fields,
                "execution_source_type": execution_source_type,
                "forbidden_conditions": copy.deepcopy(
                    selected.get("forbidden_conditions", [])
                )
                + (
                    [
                        "decorative home-staging props dominate the chair",
                        "background reads as a living-room decor advertisement",
                        "dark background collapses the black mesh silhouette",
                        "empty showroom or sparse studio without active "
                        "workstation evidence",
                    ]
                    if (
                        stable_role == "CAROUSEL_SLOT_1_IDENTITY"
                        and subcategory == "ERGONOMIC_SEATING"
                    )
                    else []
                ),
                "feature_expression": _feature_expression(
                    stable_role,
                    raw,
                    identity,
                    subcategory,
                ),
            }
        )

    return {
        "status": "ACTIVE",
        "subcategory": subcategory,
        "expression_family": expression_family,
        "shot_reuse_contract": copy.deepcopy(SHOT_REUSE_CONTRACT),
        "case_dna_id": (
            case_dna_id if campaign_dna is not None else None
        ),
        "subcategory_visuals": copy.deepcopy(
            analysis["subcategories"][subcategory]
        ),
        "campaign_dna": copy.deepcopy(campaign_dna),
        "page_overlays": page_overlays,
    }


def _region(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 4:
        return {
            "x": value[0],
            "y": value[1],
            "width": value[2],
            "height": value[3],
        }
    return copy.deepcopy(value)


def _region_text(value: Any) -> str:
    region = _region(value)
    if not isinstance(region, dict):
        return str(region)
    return (
        f"x={region['x']}%, y={region['y']}%, "
        f"width={region['width']}%, height={region['height']}%"
    )


def _execution_prompt_block(
    execution_fields: dict[str, Any],
    forbidden_conditions: list[Any],
    subcategory_visuals: dict[str, Any],
    campaign_dna: dict[str, Any] | None,
    feature_expression: dict[str, Any],
) -> str:
    occupancy = execution_fields.get("product_occupancy_range", [])
    occupancy_text = (
        f"{occupancy[0]}%–{occupancy[1]}%"
        if isinstance(occupancy, list) and len(occupancy) == 2
        else str(occupancy)
    )
    devices = "、".join(
        str(value)
        for value in execution_fields.get("graphic_device_family", [])
    )
    lines = [
        "【DURABLE CASE DESIGN EXECUTION — EXPERIMENTAL_PATTERN】",
        "本块替换本页原有的非事实版式默认值；若前文出现冲突的固定模板，"
        "以本块为准。不得改变 Product Identity、用户事实、可见文案或合规门禁。",
        f"版式原型：{execution_fields.get('layout_archetype')}；"
        f"阅读路径：{execution_fields.get('reading_path')}。",
        f"商品占画面 {occupancy_text}，锚点 "
        f"{execution_fields.get('product_anchor')}，视角 "
        f"{execution_fields.get('product_view')}，构图轴 "
        f"{execution_fields.get('composition_axis')}。",
        f"标题区：{_region_text(execution_fields.get('title_region_pct'))}；"
        f"辅助文案区：{_region_text(execution_fields.get('supporting_copy_region_pct'))}；"
        f"证据/权益区：{_region_text(execution_fields.get('evidence_or_offer_region_pct'))}。",
        f"标题结构：{execution_fields.get('headline_structure')}；"
        f"关键词强调：{execution_fields.get('keyword_emphasis_method')}；"
        f"图形装置：{devices}，最多 "
        f"{execution_fields.get('graphic_device_limit')} 个。",
        f"卖点/痛点展示拓扑："
        f"{execution_fields.get('evidence_topology')}；"
        f"信息模块拓扑："
        f"{execution_fields.get('information_module_topology')}。"
        "只把已核验卖点绑定到当前商品可见部件；局部高亮、引线、"
        "方向标记和放大框必须指向正确结构，不得借用参考商品的部件、"
        "参数、认证、赠品或优惠。",
        f"背景：{execution_fields.get('background_system')} / "
        f"{execution_fields.get('background_material')} / "
        f"{execution_fields.get('background_tone')}；"
        f"灯光：{execution_fields.get('lighting_structure')}；"
        f"商品分离：{execution_fields.get('product_separation_method')}。",
        f"语义断行：{execution_fields.get('semantic_line_break_plan')}。",
        f"本页卖点表达模式：{feature_expression.get('expression_mode')}。",
    ]
    open_composition = feature_expression.get(
        "open_expression_composition", {}
    )
    if open_composition.get("mode") == "OPEN_SYNTHESIS_REQUIRED":
        lines.append(
            "开放式视觉编排合同：现有原型仅为非约束候选，不得把卖点表达"
            "处理成固定模板或有限选项。生图前必须围绕买家疑虑、当前商品"
            "证据和缩略图可读性，具体决定机位、裁切、商品状态、主体关系、"
            "空间拓扑、信息拓扑、背景与灯光；现有方法不合适时允许创造新的"
            "页面骨架。线条、色带、图标和标签只能澄清已经可见的实体关系，"
            "不得代替实体证明。"
        )
        lines.append(
            "开放式编排预判："
            + "；".join(
                str(value)
                for value in open_composition.get(
                    "pre_generation_questions", []
                )
            )
            + "。硬成功条件："
            + str(open_composition.get("hard_success_condition"))
            + "。"
        )
        lines.append(
            str(open_composition.get("compile_requirement"))
            + "。"
        )
    decision_core = execution_fields.get("presentation_decision_core")
    if isinstance(decision_core, dict):
        lines.append(
            "通用商品表现决策：候选视角必须按 "
            + "、".join(
                f"{key}={value}%"
                for key, value in decision_core.get(
                    "view_score_weights", {}
                ).items()
            )
            + " 评分；首图边界="
            + str(decision_core.get("first_page_boundary"))
            + "；局部分离="
            + str(decision_core.get("local_separation_policy"))
            + "。"
        )
    for binding in feature_expression.get("proof_bindings", []):
        components = binding.get("component_bindings", [])
        if components:
            component_text = "；".join(
                f"{item.get('target_component')}（证据 "
                f"{'/'.join(item.get('evidence_ids', []))}，允许视角 "
                f"{'/'.join(item.get('view_types', []))}）"
                for item in components
            )
        else:
            component_text = "无可见部件绑定，不得画引线或伪造局部证据"
        lines.append(
            f"已核验事实「{binding.get('verified_fact')}」："
            f"采用 {binding.get('proof_method')}；{component_text}；"
            f"指示策略 {binding.get('pointer_policy')}。"
        )
    lines.append(
        "痛点/卖点表达必须由以上当前商品事实与证据绑定驱动；"
        "场景页用真实使用语境，功能页用正确局部特写或引线，"
        "证据不足时只保留完整文字，不得把引线指向相似但错误的部件。"
    )
    success_contract = feature_expression.get("proof_success_contract", {})
    if success_contract.get("status") == "READY":
        lines.append(
            "卖点视觉成功合同：表达族="
            + str(success_contract.get("expression_family"))
            + "；必要关系="
            + str(success_contract.get("required_visual_relation"))
            + "；缩略图判定="
            + str(success_contract.get("success_at_thumbnail"))
            + "；禁止替代="
            + "、".join(
                str(value)
                for value in success_contract.get(
                    "forbidden_substitutes", []
                )
            )
            + "。"
        )
    grammar = subcategory_visuals.get("required_visual_grammar", [])
    if grammar:
        lines.append(
            "子类视觉语法：" + "；".join(str(value) for value in grammar)
        )
    forbidden_inference = subcategory_visuals.get(
        "forbidden_inference", []
    )
    if forbidden_inference:
        lines.append(
            "禁止推断："
            + "；".join(str(value) for value in forbidden_inference)
        )
    if campaign_dna:
        shared_tokens = campaign_dna.get("shared_visual_tokens")
        if shared_tokens:
            lines.append(
                "套图共享视觉语汇："
                + "；".join(str(value) for value in shared_tokens)
            )
    lines.append(
        "禁用条件：" + "；".join(str(value) for value in forbidden_conditions)
        + "。触发时忽略该页实验原型并沿用稳定版。"
    )
    return "\n".join(lines)


def _replace_section(prompt: str, replacement: str) -> str:
    pattern = re.compile(
        r"【DESIGN PROTOTYPE OVERLAY — NON-FACTUAL VISUAL EXECUTION】"
        r".*?(?=【EVIDENCE REPRESENTATION ROUTER】)",
        re.S,
    )
    if pattern.search(prompt):
        return pattern.sub(replacement + "\n\n", prompt, count=1)
    return prompt


def _replace_baseline_literals(
    prompt: str,
    baseline_overlay: dict[str, Any],
    baseline_fields: dict[str, Any],
    execution_fields: dict[str, Any],
) -> str:
    occupancy = execution_fields.get("product_occupancy_range")
    replacements = {
        str(baseline_overlay.get("layout_archetype")):
            str(execution_fields.get("layout_archetype")),
        str(baseline_overlay.get("composition_axis")):
            str(execution_fields.get("composition_axis")),
        str(baseline_overlay.get("product_anchor")):
            str(execution_fields.get("product_anchor")),
        str(baseline_fields.get("composition.pattern")):
            str(execution_fields.get("layout_archetype")),
        str(baseline_fields.get("composition.axis")):
            str(execution_fields.get("composition_axis")),
        str(baseline_fields.get("product.visual_anchor")):
            str(execution_fields.get("product_anchor")),
        str(baseline_fields.get("product.angle_view")):
            str(execution_fields.get("product_view")),
        str(baseline_fields.get("copy.headline_region")):
            _region_text(execution_fields.get("title_region_pct")),
        str(baseline_fields.get("copy.selling_point_region")):
            _region_text(
                execution_fields.get("supporting_copy_region_pct")
            ),
    }
    old_occupancy = baseline_fields.get("product.occupancy_range")
    if (
        isinstance(old_occupancy, list)
        and len(old_occupancy) == 2
        and isinstance(occupancy, list)
        and len(occupancy) == 2
    ):
        replacements[
            f"{old_occupancy[0]}%–{old_occupancy[1]}%"
        ] = f"{occupancy[0]}%–{occupancy[1]}%"
    for old, new in replacements.items():
        if old and old != "None" and new and new != "None":
            prompt = prompt.replace(old, new)
    return prompt


def _apply_execution_fields(
    page: dict[str, Any],
    execution_fields: dict[str, Any],
    feature_expression: dict[str, Any],
    execution_source_type: str,
) -> None:
    overlay = page.setdefault("design_prototype_overlay", {})
    fields = page.setdefault("visual_plan", {}).setdefault("fields", {})
    typography = page.setdefault("typography_plan", {})
    style_layout = page.setdefault("style_layout_separation", {})

    overlay_updates = {
        "canvas_aspect_ratio": execution_fields.get("canvas_aspect_ratio"),
        "layout_archetype": execution_fields.get("layout_archetype"),
        "reading_path": execution_fields.get("reading_path"),
        "product_occupancy": execution_fields.get(
            "product_occupancy_range"
        ),
        "product_anchor": execution_fields.get("product_anchor"),
        "product_view": execution_fields.get("product_view"),
        "view_selection_policy": execution_fields.get(
            "view_selection_policy"
        ),
        "hero_feature_visibility_targets": execution_fields.get(
            "hero_feature_visibility_targets"
        ),
        "hero_feature_visibility_policy": execution_fields.get(
            "hero_feature_visibility_policy"
        ),
        "product_salience_policy": execution_fields.get(
            "product_salience_policy"
        ),
        "background_competition_limit": execution_fields.get(
            "background_competition_limit"
        ),
        "presentation_decision_core": execution_fields.get(
            "presentation_decision_core"
        ),
        "composition_axis": execution_fields.get("composition_axis"),
        "title_region_pct": _region(
            execution_fields.get("title_region_pct")
        ),
        "supporting_copy_region_pct": _region(
            execution_fields.get("supporting_copy_region_pct")
        ),
        "evidence_or_offer_region_pct": _region(
            execution_fields.get("evidence_or_offer_region_pct")
        ),
        "semantic_line_break_plan": execution_fields.get(
            "semantic_line_break_plan"
        ),
        "typography_scale_ratios": execution_fields.get(
            "typography_scale_ratios"
        ),
        "headline_structure": execution_fields.get("headline_structure"),
        "keyword_emphasis_method": execution_fields.get(
            "keyword_emphasis_method"
        ),
        "graphic_device_family": execution_fields.get(
            "graphic_device_family"
        ),
        "graphic_device_limit": execution_fields.get(
            "graphic_device_limit"
        ),
        "background_system": execution_fields.get("background_system"),
        "background_material": execution_fields.get("background_material"),
        "background_tone": execution_fields.get("background_tone"),
        "lighting_structure": execution_fields.get("lighting_structure"),
        "information_density": execution_fields.get("information_density"),
        "evidence_topology": execution_fields.get("evidence_topology"),
        "information_module_topology": execution_fields.get(
            "information_module_topology"
        ),
        "copy_visibility_mode": execution_fields.get(
            "copy_visibility_mode"
        ),
        "difference_from_previous_page": execution_fields.get(
            "difference_from_other_variants"
        ),
        "expression_family": execution_fields.get("expression_family"),
        "shot_plan": execution_fields.get("shot_plan"),
    }
    overlay.update(
        {
            key: copy.deepcopy(value)
            for key, value in overlay_updates.items()
            if value is not None
        }
    )
    overlay_sources = overlay.setdefault("field_sources", {})
    for key in overlay_updates:
        if overlay_updates[key] is not None:
            overlay_sources[key] = execution_source_type

    field_updates = {
        "composition.axis": execution_fields.get("composition_axis"),
        "composition.pattern": execution_fields.get("layout_archetype"),
        "copy.headline_region": _region(
            execution_fields.get("title_region_pct")
        ),
        "copy.selling_point_region": _region(
            execution_fields.get("supporting_copy_region_pct")
        ),
        "product.angle_view": execution_fields.get("product_view"),
        "product.occupancy_range": execution_fields.get(
            "product_occupancy_range"
        ),
        "product.visual_anchor": execution_fields.get("product_anchor"),
        "background.system": execution_fields.get("background_system"),
        "background.material": execution_fields.get("background_material"),
        "background.tone": execution_fields.get("background_tone"),
        "lighting.system": execution_fields.get("lighting_structure"),
        "typography.graphic_devices": execution_fields.get(
            "graphic_device_family"
        ),
        "typography.keyword_emphasis": execution_fields.get(
            "keyword_emphasis_method"
        ),
        "visual_devices.items": execution_fields.get(
            "graphic_device_family"
        ),
        "visual_devices.max_count": execution_fields.get(
            "graphic_device_limit"
        ),
    }
    fields.update(
        {
            key: copy.deepcopy(value)
            for key, value in field_updates.items()
            if value is not None
        }
    )

    typography["headline_structure"] = execution_fields.get(
        "headline_structure"
    )
    typography["graphic_devices"] = copy.deepcopy(
        execution_fields.get("graphic_device_family", [])
    )
    typography.setdefault("keyword_emphasis", {})[
        "emphasis_method"
    ] = execution_fields.get("keyword_emphasis_method")
    typography["keyword_emphasis"]["source_type"] = execution_source_type

    style_layout.update(
        {
            "layout_archetype": execution_fields.get("layout_archetype"),
            "reading_path": execution_fields.get("reading_path"),
            "product_occupancy": copy.deepcopy(
                execution_fields.get("product_occupancy_range")
            ),
            "product_anchor": execution_fields.get("product_anchor"),
            "composition_axis": execution_fields.get("composition_axis"),
            "title_region_pct": _region(
                execution_fields.get("title_region_pct")
            ),
            "difference_from_previous_page": execution_fields.get(
                "difference_from_other_variants"
            ),
        }
    )
    page.setdefault("evidence_representation", {})[
        "layout_archetype_binding"
    ] = execution_fields.get("layout_archetype")
    page["evidence_representation"]["visual_execution"] = (
        execution_fields.get("evidence_topology")
    )
    page["evidence_representation"]["information_module_topology"] = (
        execution_fields.get("information_module_topology")
    )
    page["feature_expression"] = copy.deepcopy(feature_expression)
    page["evidence_representation"]["feature_expression"] = copy.deepcopy(
        feature_expression
    )

    page_sources = page.setdefault("field_sources", {})
    for key in field_updates:
        if field_updates[key] is not None:
            page_sources[key] = execution_source_type
    for key in overlay_updates:
        if overlay_updates[key] is not None:
            page_sources[
                f"design_prototype_overlay.{key}"
            ] = execution_source_type

    variation = page.setdefault("campaign_style_tokens", {}).setdefault(
        "page_role_variation", {}
    )
    variation["allowed_graphic_devices"] = copy.deepcopy(
        execution_fields.get("graphic_device_family", [])
    )
    variation["keyword_emphasis"] = execution_fields.get(
        "keyword_emphasis_method"
    )


def _compact_values(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, dict):
        return "；".join(
            f"{key}={value}"
            for key, value in values.items()
            if value not in (None, "", [], {})
        )
    if isinstance(values, list):
        return "；".join(
            str(value) for value in values if value not in (None, "")
        )
    return str(values)


def _compact_generation_prompt(page: dict[str, Any]) -> str:
    audit = page.get("audit_contract", {})
    product_facts = audit.get("product_facts", {})
    locks = audit.get("locks", {})
    consistency = locks.get("product_consistency", {})
    compliance = audit.get("compliance", {})
    copy_plan = audit.get("copy_planner", {})
    fields = page.get("visual_plan", {}).get("fields", {})
    overlay = page.get("design_prototype_overlay", {})
    typography = page.get("typography_plan", {})
    feature = page.get("feature_expression", {})
    evidence = page.get("evidence_representation", {})
    references = product_facts.get("reference_images", [])
    reference_text = "；".join(
        f"ref-{index:02d}={path}"
        for index, path in enumerate(references, 1)
    )
    if (
        page.get("page_role") == "CAROUSEL_SLOT_1_IDENTITY"
        and overlay.get("copy_visibility_mode") == "BRAND_ONLY"
    ):
        copy_rows = [
            ("品牌", copy_plan.get("identity_line")),
        ]
    elif (
        page.get("page_role") == "CAROUSEL_SLOT_1_IDENTITY"
        and feature.get("expression_mode")
        in {"SCENE_LED_IDENTITY_HERO", "SPATIAL_FIT_IDENTITY_HERO"}
    ):
        scene_copy = "｜".join(
            str(item.get("text", "")).strip()
            for item in feature.get("selected_verified_facts", [])
            if item.get("id") != "product-identity"
            and str(item.get("text", "")).strip()
        )
        copy_rows = [
            ("身份行", copy_plan.get("identity_line")),
            ("主标题", copy_plan.get("hero_headline")),
            ("场景定位", scene_copy),
        ]
    else:
        copy_rows = [
            ("身份行", copy_plan.get("identity_line")),
            ("主标题", copy_plan.get("hero_headline")),
            ("强调词", copy_plan.get("emphasized_keyword")),
            ("副标题", copy_plan.get("supporting_line")),
            ("促销数字", copy_plan.get("numeric_offer")),
            ("底注", copy_plan.get("disclaimer")),
        ]
    if page.get("page_role") != "CAROUSEL_SLOT_1_IDENTITY":
        copy_rows.insert(
            4,
            (
                "证据/场景标签",
                "｜".join(copy_plan.get("evidence_labels", [])),
            ),
        )
    visible_copy = "；".join(
        f"{label}=「{value}」"
        for label, value in copy_rows
        if value
    )
    proof_lines: list[str] = []
    for binding in feature.get("proof_bindings", []):
        components = binding.get("component_bindings", [])
        targets = "、".join(
            f"{item.get('target_component')}["
            f"{'/'.join(item.get('evidence_ids', []))};"
            f"{'/'.join(item.get('view_types', []))}]"
            for item in components
        )
        proof_lines.append(
            f"事实「{binding.get('verified_fact')}」→"
            f"{binding.get('proof_method')}→"
            f"{targets or '无部件指针'}；"
            f"{binding.get('pointer_policy')}"
        )
    colors = typography.get("colors", fields.get("typography.colors", {}))
    devices = fields.get(
        "visual_devices.items",
        overlay.get("graphic_device_family", []),
    )
    forbidden_occlusion = fields.get("layout.forbidden_occlusion", [])
    allowed_occlusion = fields.get("layout.allowed_occlusion", [])
    view_binding = page.get("view_evidence_binding", {})
    negatives = list(audit.get("negative", [])) + list(
        compliance.get("forbidden_content", [])
    )
    lines = [
        "【PRODUCT IDENTITY — HIGHEST PRIORITY】",
        f"只编辑当前商品「{product_facts.get('name')}」"
        f"（{product_facts.get('category')}）；数量、轮廓、比例、颜色分区、"
        "材质、部件、连接关系、Logo与附件必须与当前商品原图一致。",
        f"原始同商品证据：{reference_text}。",
        f"本页视角绑定："
        f"{json.dumps(view_binding, ensure_ascii=False, separators=(',', ':'))}。",
        "previous_generated_images_used_for_product_identity=false；"
        "用户明确批准的既往页面只允许作为STYLE_REFERENCE_ONLY，"
        "不得覆盖原始同商品证据，也不得复用其商品姿态、机位、裁切、"
        "装载状态、场景几何、主体锚点或页面骨架。优先保留原商品，只替换背景并增加版式；"
        "冲突时降低装饰复杂度。",
        "必须保持：" + _compact_values(consistency.get("must_preserve")) + "。",
        "禁止改变：" + _compact_values(consistency.get("must_not_change")) + "。",
        "",
        "【PAGE TASK & VERIFIED COPY】",
        f"Surface={page.get('surface')}；页面职责={page.get('page_role')}；"
        f"沟通目标={page.get('communication_goal')}。商品始终是第一视觉主体。",
        "当前商品已核验属性："
        + _compact_values(product_facts.get("verified_attributes"))
        + "。",
        "画面只允许出现以下完整文案：" + visible_copy + "。"
        "不得固定字符截断、扩写卖点或改变数字与单位。",
        "",
        "【FEATURE EXPRESSION & EVIDENCE BINDING】",
        f"表达模式={feature.get('expression_mode', 'STABLE_PAGE_EXPRESSION')}。",
        *proof_lines,
        "卖点视觉成功合同="
        + _compact_values(feature.get("proof_success_contract"))
        + "。",
        f"证据表现={evidence.get('representation_mode')}；"
        f"{evidence.get('visual_execution')}；"
        f"卡片拓扑={evidence.get('card_topology')}。",
        "引线、局部高亮和放大框只能指向上面明确绑定的当前商品部件；"
        "没有可见证据时只保留事实文字，不得伪造证明。",
        "",
        "【CONCRETE DESIGN EXECUTION】",
        f"商品表达族={overlay.get('expression_family')}；"
        f"本页唯一镜头={_compact_values(overlay.get('shot_plan'))}。"
        "跨页禁止复用相同或近似的完整商品摄影、裁切、装载/工作状态和"
        "场景几何；若冲突，生图前先改视角、尺度、状态、场景或骨架。",
        f"canvas={overlay.get('canvas_aspect_ratio', 'FOLLOW_OUTPUT_SPEC')}；"
        f"layout={overlay.get('layout_archetype', fields.get('composition.pattern'))}；"
        f"reading_path={overlay.get('reading_path')}；"
        f"composition_axis={fields.get('composition.axis')}；"
        f"product_anchor={fields.get('product.visual_anchor')}；"
        f"product_view={fields.get('product.angle_view')}；"
        f"product_occupancy={fields.get('product.occupancy_range')}%。",
        f"视角选择={overlay.get('view_selection_policy')}；"
        f"首页结构可见目标={_compact_values(overlay.get('hero_feature_visibility_targets'))}；"
        f"自然展示规则={overlay.get('hero_feature_visibility_policy')}；"
        f"商品突出={overlay.get('product_salience_policy')}；"
        f"背景竞争上限={overlay.get('background_competition_limit')}。",
        "通用商品表现决策="
        + _compact_values(overlay.get("presentation_decision_core"))
        + "。",
        f"title_region={_region_text(fields.get('copy.headline_region'))}；"
        f"supporting_region={_region_text(fields.get('copy.selling_point_region'))}；"
        f"evidence_or_offer_region={_region_text(overlay.get('evidence_or_offer_region_pct'))}；"
        f"safe_margin={fields.get('layout.safe_margin_percent')}%。",
        f"背景={fields.get('background.system')} / "
        f"{fields.get('background.material')} / {fields.get('background.tone')}；"
        f"灯光={fields.get('lighting.system')}；"
        f"商品分离={fields.get('lighting.product_separation')}。",
        f"标题结构={typography.get('headline_structure')}；"
        f"关键词强调={typography.get('keyword_emphasis', {}).get('emphasis_method')}；"
        f"图形装置={_compact_values(devices)}，最多"
        f"{fields.get('visual_devices.max_count', overlay.get('graphic_device_limit'))}种。",
        f"颜色角色={_compact_values(colors)}。整套保持同一字体家族、颜色角色、"
        "线条粗细与摄影质感；本页版式骨架必须独立。",
        f"允许遮挡={_compact_values(allowed_occlusion) or '仅背景关系'}；"
        f"禁止遮挡={_compact_values(forbidden_occlusion)}。",
        "",
        "【COMPLIANCE & NEGATIVE】",
        "只使用USER_FACT或已验证安全压缩文案；优惠仅可来自USER_FACT。"
        "不得编造价格、参数、认证、功效、权益、附件或品牌字形。",
        "禁止：" + _compact_values(negatives) + "。",
        "最终优先级：商品身份与事实准确 > 证据指向正确 > 页面职责 > "
        "Campaign一致性 > 装饰复杂度。",
    ]
    return "\n".join(line for line in lines if line is not None)


def apply_overlay(
    compiled: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Replace matching non-factual defaults; preserve facts, contracts and gates."""
    if plan["status"] != "ACTIVE":
        return compiled
    result = copy.deepcopy(compiled)
    by_role = {
        item["stable_page_role"]: item
        for item in plan["page_overlays"]
    }
    for page in result.get("pages", []):
        overlay = by_role.get(page.get("page_role"))
        if overlay is None:
            continue
        stable_audit_contract = copy.deepcopy(page.get("audit_contract"))
        stable_gates = copy.deepcopy(page.get("gates"))
        execution_fields = overlay["execution_fields"]
        baseline_overlay = copy.deepcopy(
            page.get("design_prototype_overlay", {})
        )
        baseline_fields = copy.deepcopy(
            page.get("visual_plan", {}).get("fields", {})
        )
        prompt_block = _execution_prompt_block(
            execution_fields,
            overlay["forbidden_conditions"],
            plan["subcategory_visuals"],
            plan["campaign_dna"],
            overlay["feature_expression"],
        )
        page["generation_prompt"] = _replace_baseline_literals(
            page["generation_prompt"],
            baseline_overlay,
            baseline_fields,
            execution_fields,
        )
        page["generation_prompt"] = _replace_section(
            page["generation_prompt"],
            prompt_block,
        )
        _apply_execution_fields(
            page,
            execution_fields,
            overlay["feature_expression"],
            overlay["execution_source_type"],
        )
        page["audit_contract"] = stable_audit_contract
        page["gates"] = stable_gates
    for page in result.get("pages", []):
        page["generation_prompt"] = _compact_generation_prompt(page)
    return result
