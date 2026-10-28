#!/usr/bin/env python3
"""Build and optionally calibrate a concrete Campaign Style token contract."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "campaign-style-token-contract-v0.1.6.schema.json"

ALLOWED_CONTROLS = [
    "TYPOGRAPHY_FAMILY",
    "HEADLINE_TREATMENT",
    "BODY_TREATMENT",
    "COLOR_ROLES",
    "GRAPHIC_DEVICE_FAMILY",
    "INFORMATION_SURFACES",
    "PHOTOGRAPHY_FINISH",
    "LIGHTING_LANGUAGE",
]
FORBIDDEN_CONTROLS = [
    "PRODUCT_IDENTITY",
    "PRODUCT_COLOR",
    "PRODUCT_STRUCTURE",
    "LAYOUT_ARCHETYPE",
    "PRODUCT_ANCHOR",
    "PRODUCT_SCALE",
    "COMPOSITION_AXIS",
    "TITLE_POSITION",
    "READING_PATH",
    "CARD_TOPOLOGY",
    "USER_FACT",
    "VISIBLE_COPY",
]
FORBIDDEN_OBSERVATION_KEYS = {
    "product_identity",
    "product_color",
    "product_structure",
    "layout_archetype",
    "product_anchor",
    "product_scale",
    "composition_axis",
    "title_position",
    "reading_path",
    "card_topology",
    "visible_copy",
    "user_fact",
}


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate(result: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(_load(SCHEMA)).validate(result)


def _deep_merge(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def build_planned_style_token_contract(
    raw: dict[str, Any],
    design: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    colors = design["color_system"]
    fonts = design["font_system"]
    devices = sorted(
        {
            device
            for page in design["pages"]
            for device in page.get("graphic_devices", [])
        }
    )
    role_variations = []
    design_by_index = {p["index"]: p for p in design["pages"]}
    for page in overlay["pages"]:
        design_page = design_by_index[page["page_index"]]
        role_variations.append(
            {
                "page_index": page["page_index"],
                "page_role": design_page["page_role"],
                "headline_treatment": design_page["treatment_id"],
                "keyword_emphasis": design_page["keyword_emphasis"][
                    "emphasis_method"
                ],
                "allowed_graphic_devices": design_page["graphic_devices"],
                "layout_must_remain_independent": True,
            }
        )
    tokens = {
        "typography": {
            "display_family_role": fonts["headline_personality"],
            "body_family_role": fonts["body_personality"],
            "numeric_family_role": fonts["numeric_personality"],
            "headline_fill_role": "PRIMARY_TEXT_OR_CAMPAIGN_ACCENT",
            "headline_outline_policy": "ONE_SHARED_TREATMENT_MAX",
            "headline_shadow_policy": "RESTRAINED_AND_SHARED",
            "body_treatment": "LEGIBLE_SOLID_NO_DECORATIVE_EFFECT",
            "label_treatment": "SHARED_STROKE_AND_WEIGHT_FAMILY",
            "invariant_features": [
                "same display/body role pairing across set",
                "same outline and shadow grammar across set",
                "same label stroke and corner language across set",
            ],
        },
        "palette": {
            "primary_text": colors["primary_text_color"],
            "secondary_text": colors["secondary_text_color"],
            "campaign_accent": colors["accent_color"],
            "secondary_accent": colors["secondary_accent_color"],
            "information_surface": colors["information_surface_color"],
            "divider": colors["divider_color"],
            "offer_color": colors["offer_color"],
            "usage_ratio": colors["usage_ratio"],
            "product_identity_colors_immutable": True,
        },
        "graphic_language": {
            "shared_device_family": devices,
            "geometry": "ONE_SHARED_CORNER_STROKE_AND_CONTOUR_LANGUAGE",
            "device_count_limit_per_page": 3,
            "adjacent_page_device_mix_must_vary": True,
        },
        "information_surfaces": {
            "fill_role": "INFORMATION_SURFACE_OR_CONTROLLED_TRANSPARENCY",
            "corner_radius_class": "ONE_SHARED_RADIUS_FAMILY",
            "border_policy": "ONE_SHARED_DIVIDER_WEIGHT",
            "repeated_identical_card_stack_forbidden": True,
        },
        "photography_finish": {
            "contrast": "CONSISTENT_PRODUCT_LED_COMMERCIAL_CONTRAST",
            "lighting": "CONSISTENT_KEY_DIRECTION_AND_EDGE_SEPARATION",
            "material_rendering": "REAL_TEXTURE_WITHOUT_PLASTIC_REDESIGN",
            "background_finish": "CATEGORY_COMPATIBLE_AND_SUBORDINATE",
        },
    }
    gates = {
        "style_tokens_are_concrete": all(tokens.values()),
        "style_controls_exclude_layout": not (
            set(ALLOWED_CONTROLS) & set(FORBIDDEN_CONTROLS)
        ),
        "product_identity_authority_is_original_only": True,
        "planned_contract_uses_no_generated_reference": True,
        "five_role_variations_present": len(role_variations) == 5,
        "profile01_semantics_read_false": True,
    }
    result = {
        "schema_version": "campaign-style-token-contract-0.1.6",
        "request_id": raw["request_id"],
        "status": "READY" if all(gates.values()) else "BLOCKED",
        "mode": "PLANNED",
        "source_type": "ENGINEERED_DEFAULT",
        "style_reference_policy": {
            "approved_generated_style_references": [],
            "user_approved": False,
            "scope": "STYLE_REFERENCE_ONLY",
            "product_identity_authority": (
                "ORIGINAL_CURRENT_PRODUCT_REFERENCES_ONLY"
            ),
            "allowed_controls": ALLOWED_CONTROLS,
            "forbidden_controls": FORBIDDEN_CONTROLS,
        },
        "tokens": tokens,
        "page_role_variations": role_variations,
        "set_gates": gates,
        "generation_performed": False,
        "profile01_semantics_read": False,
    }
    _validate(result)
    return result


def calibrate_from_user_approved_pages(
    planned: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    forbidden_seen = _walk_keys(observation) & FORBIDDEN_OBSERVATION_KEYS
    valid = (
        observation.get("user_approved") is True
        and observation.get("scope") == "STYLE_REFERENCE_ONLY"
        and bool(observation.get("approved_generated_style_references"))
        and not forbidden_seen
        and isinstance(observation.get("observed_tokens"), dict)
    )
    result = copy.deepcopy(planned)
    if valid:
        result["tokens"] = _deep_merge(
            result["tokens"], observation["observed_tokens"]
        )
        result["mode"] = "CALIBRATED_FROM_USER_APPROVED_PAGES"
        result["source_type"] = "USER_APPROVED_STYLE_REFERENCE"
        result["style_reference_policy"][
            "approved_generated_style_references"
        ] = observation["approved_generated_style_references"]
        result["style_reference_policy"]["user_approved"] = True
        result["set_gates"][
            "approved_style_reference_scope_is_style_only"
        ] = True
        result["set_gates"][
            "calibration_contains_no_product_or_layout_fields"
        ] = True
        result["status"] = "READY"
    else:
        result["set_gates"][
            "approved_style_reference_scope_is_style_only"
        ] = False
        result["set_gates"][
            "calibration_contains_no_product_or_layout_fields"
        ] = not bool(forbidden_seen)
        result["status"] = "BLOCKED"
    _validate(result)
    return result


def style_token_prompt_block(
    contract: dict[str, Any], page_index: int
) -> str:
    variation = next(
        p
        for p in contract["page_role_variations"]
        if p["page_index"] == page_index
    )
    tokens = contract["tokens"]
    return (
        "【CAMPAIGN STYLE TOKEN LOCK v0.1.6】\n"
        f"mode={contract['mode']}；scope=STYLE_REFERENCE_ONLY；"
        "原始当前商品参考图始终是唯一产品身份依据。\n"
        f"typography={json.dumps(tokens['typography'], ensure_ascii=False)}\n"
        f"palette={json.dumps(tokens['palette'], ensure_ascii=False)}\n"
        f"graphic_language={json.dumps(tokens['graphic_language'], ensure_ascii=False)}\n"
        f"information_surfaces={json.dumps(tokens['information_surfaces'], ensure_ascii=False)}\n"
        f"photography_finish={json.dumps(tokens['photography_finish'], ensure_ascii=False)}\n"
        f"本页角色变化={json.dumps(variation, ensure_ascii=False)}。\n"
        "保持上述字体角色、颜色角色、图形几何和摄影质感；"
        "同时严格执行本页独立版式，不复制其他页面骨架。"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--planned-contract", required=True)
    parser.add_argument("--approved-observation", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = calibrate_from_user_approved_pages(
        _load(args.planned_contract), _load(args.approved_observation)
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "mode": result["mode"]}))
    return 0 if result["status"] == "READY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
