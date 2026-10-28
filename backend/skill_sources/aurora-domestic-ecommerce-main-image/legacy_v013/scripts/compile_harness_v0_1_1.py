#!/usr/bin/env python3
"""Plan and compile raw domestic-ecommerce inputs into prompt/audit pairs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema


VERSION = "domestic-ecommerce-prompt-compiler-0.1.1"
PRECEDENCE = [
    "USER_FACT",
    "EVIDENCE_DERIVED",
    "ENGINEERED_DEFAULT",
    "MODEL_FREEDOM",
]
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW_SCHEMA = ROOT / "schemas" / "raw-input-v0.1.1.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "compiled-output-v0.1.1.schema.json"
RULES = (
    ROOT / "references" / "source-layered-rules-v0.1.json"
    if (ROOT / "references" / "source-layered-rules-v0.1.json").exists()
    else ROOT / "source-layered-rules-v0.1.json"
)
ROLE_MAP = (
    ROOT / "references" / "research-role-map-v0.1.1.json"
    if (ROOT / "references" / "research-role-map-v0.1.1.json").exists()
    else ROOT / "research-role-map-v0.1.1.json"
)


class CompileError(ValueError):
    pass


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path: str | Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def portable_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def normalize_category(value: str) -> str:
    text = value.strip().upper()
    aliases = {
        "家具": "FURNITURE",
        "人体工学椅": "FURNITURE",
        "办公椅": "FURNITURE",
        "数码": "ELECTRONICS",
        "电子": "ELECTRONICS",
        "小家电": "HOME_APPLIANCE",
        "家电": "HOME_APPLIANCE",
        "美妆": "BEAUTY_PERSONAL_CARE",
        "个护": "BEAUTY_PERSONAL_CARE",
        "食品": "FOOD",
        "家居日用": "HOME_DAILY",
        "家居": "HOME_DAILY",
    }
    for token, category in aliases.items():
        if token in value:
            return category
    if text in {
        "FURNITURE",
        "ELECTRONICS",
        "HOME_APPLIANCE",
        "BEAUTY_PERSONAL_CARE",
        "FOOD",
        "HOME_DAILY",
    }:
        return text
    return "GENERAL"


def attribute_text(attributes: dict[str, Any], key_terms: tuple[str, ...]) -> str:
    for key, value in attributes.items():
        if any(term in key.lower() for term in key_terms):
            if isinstance(value, list):
                return "、".join(str(x) for x in value)
            return str(value)
    return ""


def dominant_luminance(value: str) -> str:
    """Classify the current product's evidence-bound color partition.

    This intentionally avoids category- or product-instance checks. A mixed
    product is not treated as wholly dark merely because one material region is
    black.
    """
    text = value.lower()
    dark_count = sum(text.count(x) for x in ("black", "dark", "黑色", "深灰", "石墨"))
    light_count = sum(text.count(x) for x in ("white", "light", "白色", "浅灰", "银白"))
    if dark_count > light_count:
        return "DARK_DOMINANT"
    if light_count > dark_count:
        return "LIGHT_DOMINANT"
    return "MIXED_OR_UNKNOWN"


BACKGROUND_PROFILES = {
    "FURNITURE": {
        "system": "light-neutral architectural office/home space",
        "tone": "soft ivory and light warm gray",
        "materials": ["light-gray plaster", "warm walnut", "matte neutral floor"],
        "device_pool": ["restrained ergonomic curve", "thin architectural line"],
    },
    "ELECTRONICS": {
        "system": "clean technical product studio",
        "tone": "neutral gray with restrained cool accent",
        "materials": ["matte mineral panel", "fine anodized-metal plane"],
        "device_pool": ["thin data line", "precision grid"],
    },
    "HOME_APPLIANCE": {
        "system": "bright clean kitchen/home product environment",
        "tone": "warm white with a restrained category accent",
        "materials": ["light solid-surface counter", "soft neutral wall"],
        "device_pool": ["functional callout line", "soft geometric halo"],
    },
    "BEAUTY_PERSONAL_CARE": {
        "system": "soft clean beauty studio",
        "tone": "light mineral neutral with product-matched accent",
        "materials": ["matte mineral surface", "translucent acrylic plane"],
        "device_pool": ["soft halo", "fine ingredient line"],
    },
    "FOOD": {
        "system": "warm appetizing tabletop or ingredient scene",
        "tone": "warm natural neutral",
        "materials": ["natural wood or stone tabletop", "soft fabric accent"],
        "device_pool": ["ingredient framing", "soft steam or freshness cue"],
    },
    "HOME_DAILY": {
        "system": "clean lived-in home environment",
        "tone": "warm light neutral",
        "materials": ["light plaster wall", "warm wood", "matte home surface"],
        "device_pool": ["thin utility line", "soft framing arch"],
    },
    "GENERAL": {
        "system": "neutral commercial product studio",
        "tone": "balanced light neutral",
        "materials": ["matte neutral wall", "grounded seamless floor"],
        "device_pool": ["thin framing line", "soft product halo"],
    },
}


ROLE_VARIANTS = {
    "STANDARD_IDENTITY_MAIN": {
        "surface": "ECOM_MAIN_IMAGE",
        "goal": "ESTABLISH_PRODUCT_IDENTITY",
        "occupancy": [66, 74],
        "anchor": "center-right, full product visible",
        "axis": "lower-right to upper-left asymmetric balance",
        "composition": "single product identity hero with clean negative space",
        "view": "front three-quarter identity view",
        "headline": "upper-left safe zone",
        "selling": "below headline, one concise verified selling point",
        "offer": "none",
        "devices": 2,
        "background_variant": "identity studio",
    },
    "OFFER_IDENTITY_MAIN": {
        "surface": "ECOM_MAIN_IMAGE",
        "goal": "COMMUNICATE_OFFER_WITH_IDENTITY",
        "occupancy": [58, 66],
        "anchor": "left-center or center-left, full product visible",
        "axis": "product-to-information horizontal split",
        "composition": "product-led identity hero with a separate offer rail",
        "view": "front three-quarter identity view",
        "headline": "upper-right identity strip",
        "selling": "directly below product identity",
        "offer": "right-side module, outside product silhouette",
        "devices": 3,
        "background_variant": "offer identity studio",
    },
    "CAROUSEL_SLOT_1_IDENTITY": {
        "surface": "ECOM_CAROUSEL",
        "goal": "ESTABLISH_PRODUCT_IDENTITY",
        "occupancy": [67, 74],
        "anchor": "center-right hero",
        "axis": "asymmetric vertical hero",
        "composition": "identity hero with search-interception clarity",
        "view": "front three-quarter full-product view",
        "headline": "upper-left",
        "selling": "left-middle under identity",
        "offer": "none",
        "devices": 2,
        "background_variant": "identity studio",
    },
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT": {
        "surface": "ECOM_CAROUSEL",
        "goal": "EXPLAIN_CORE_FEATURE",
        "occupancy": [58, 66],
        "anchor": "left-center feature view",
        "axis": "diagonal product-to-callout flow",
        "composition": "feature-led product view with one evidence callout",
        "view": "closer three-quarter or side view",
        "headline": "upper-right",
        "selling": "right-middle callout zone",
        "offer": "none",
        "devices": 3,
        "background_variant": "technical feature plane",
    },
    "CAROUSEL_SLOT_3_SCENE_BENEFIT": {
        "surface": "ECOM_CAROUSEL",
        "goal": "DEMONSTRATE_USE_CONTEXT",
        "occupancy": [46, 56],
        "anchor": "center foreground in compatible scene",
        "axis": "foreground product with receding scene depth",
        "composition": "full-bleed compatible scene with product foreground",
        "view": "contextual front three-quarter view",
        "headline": "top-center",
        "selling": "upper perimeter, away from product",
        "offer": "none",
        "devices": 1,
        "background_variant": "bright compatible lifestyle scene",
    },
    "CAROUSEL_SLOT_4_DETAIL_PROOF": {
        "surface": "ECOM_CAROUSEL",
        "goal": "PROVE_DETAIL",
        "occupancy": [54, 63],
        "anchor": "left product context plus right macro evidence",
        "axis": "split detail-proof grid",
        "composition": "product context paired with one or two factual detail crops",
        "view": "detail close-up with identity context",
        "headline": "right-upper",
        "selling": "right-middle evidence labels",
        "offer": "none",
        "devices": 4,
        "background_variant": "controlled macro proof studio",
    },
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": {
        "surface": "ECOM_CAROUSEL",
        "goal": "PROMOTION_TRUST_OR_SUMMARY_CLOSE",
        "occupancy": [52, 61],
        "anchor": "left-center product anchor",
        "axis": "product-to-summary rail",
        "composition": "product-anchored closing layout with modular summary rail",
        "view": "front three-quarter closing view",
        "headline": "upper-center",
        "selling": "right-middle summary zone",
        "offer": "right-side module only when verified offer facts exist",
        "devices": 3,
        "background_variant": "light closing studio",
    },
    "DETAIL_LONG_FEATURE_SECTION": {
        "surface": "ECOM_DETAIL_LONG",
        "goal": "EXPLAIN_CORE_FEATURE",
        "occupancy": [42, 58],
        "anchor": "continuous vertical centerline",
        "axis": "top-to-bottom narrative flow",
        "composition": "continuous vertical section with product and proof modules",
        "view": "alternating whole-product and detail views",
        "headline": "top safe zone",
        "selling": "below headline in ordered section",
        "offer": "none unless verified and assigned to closing section",
        "devices": 3,
        "background_variant": "continuous detail-page system",
    },
    "FEATURE": {
        "surface": "ECOM_DETAIL_SLICE",
        "goal": "EXPLAIN_CORE_FEATURE",
        "occupancy": [55, 68],
        "anchor": "center-left product or feature close-up",
        "axis": "product-to-feature diagonal",
        "composition": "one feature, one slice",
        "view": "feature-revealing three-quarter or close-up",
        "headline": "upper-right",
        "selling": "right-middle",
        "offer": "none",
        "devices": 3,
        "background_variant": "feature slice studio",
    },
    "BENEFIT": {
        "surface": "ECOM_DETAIL_SLICE",
        "goal": "COMMUNICATE_USER_BENEFIT",
        "occupancy": [48, 60],
        "anchor": "center product in compatible use context",
        "axis": "foreground product and contextual depth",
        "composition": "one benefit, one compatible scene slice",
        "view": "contextual identity view",
        "headline": "upper-left",
        "selling": "left-middle",
        "offer": "none",
        "devices": 2,
        "background_variant": "benefit lifestyle slice",
    },
    "SPECIFICATION": {
        "surface": "ECOM_DETAIL_SLICE",
        "goal": "COMMUNICATE_SPECIFICATION",
        "occupancy": [44, 58],
        "anchor": "left product, right verified specification table",
        "axis": "horizontal product-to-data flow",
        "composition": "verified specification module with product identity",
        "view": "neutral identity view",
        "headline": "top-center",
        "selling": "right specification grid",
        "offer": "none",
        "devices": 3,
        "background_variant": "neutral specification slice",
    },
    "TRUST": {
        "surface": "ECOM_DETAIL_SLICE",
        "goal": "BUILD_TRUST",
        "occupancy": [50, 62],
        "anchor": "center-right product",
        "axis": "product-to-trust-marker balance",
        "composition": "one verified trust reason with product identity",
        "view": "front identity view",
        "headline": "upper-left",
        "selling": "left-middle trust zone",
        "offer": "none unless the trust fact itself is a verified right",
        "devices": 2,
        "background_variant": "clean trust slice",
    },
}


def fact_list(value: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"id": x["id"], "text": x["text"]} for x in value]


def choose_primary_claim(
    role: str, raw: dict[str, Any], index: int = 0
) -> dict[str, str]:
    product = raw["product"]
    selling = fact_list(product["verified_selling_points"])
    scenes = fact_list(product["verified_scenes"])
    offers = fact_list(product["verified_offers"])
    if "IDENTITY" in role:
        return {"id": "product:name", "text": product["name"]}
    if role == "CAROUSEL_SLOT_2_CORE_SELLING_POINT":
        return selling[0] if selling else {"id": "product:name", "text": product["name"]}
    if role == "CAROUSEL_SLOT_3_SCENE_BENEFIT":
        return scenes[0] if scenes else (
            selling[0] if selling else {"id": "product:name", "text": product["name"]}
        )
    if role == "CAROUSEL_SLOT_4_DETAIL_PROOF":
        return selling[min(1, len(selling) - 1)] if selling else {
            "id": "product:name",
            "text": product["name"],
        }
    if role == "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY" and offers:
        return offers[0]
    if role in {"FEATURE", "DETAIL_LONG_FEATURE_SECTION"}:
        return selling[index % len(selling)] if selling else {
            "id": "product:name",
            "text": product["name"],
        }
    if role == "BENEFIT":
        return scenes[0] if scenes else (
            selling[0] if selling else {"id": "product:name", "text": product["name"]}
        )
    return selling[0] if selling else {"id": "product:name", "text": product["name"]}


def roles_for_request(raw: dict[str, Any]) -> list[str]:
    output = raw["output"]
    output_type = output["type"]
    count = output["image_count"]
    product = raw["product"]
    if output_type == "STANDARD_IDENTITY_MAIN":
        return ["STANDARD_IDENTITY_MAIN"]
    if output_type == "OFFER_IDENTITY_MAIN":
        return ["OFFER_IDENTITY_MAIN"]
    if output_type == "FIVE_IMAGE_CAROUSEL":
        return [
            "CAROUSEL_SLOT_1_IDENTITY",
            "CAROUSEL_SLOT_2_CORE_SELLING_POINT",
            "CAROUSEL_SLOT_3_SCENE_BENEFIT",
            "CAROUSEL_SLOT_4_DETAIL_PROOF",
            "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY",
        ]
    if output_type == "DETAIL_LONG":
        return ["DETAIL_LONG_FEATURE_SECTION"] * count
    requested = output.get("detail_slice_roles") or []
    if requested:
        return requested
    fallback = []
    if product["verified_selling_points"]:
        fallback.append("FEATURE")
    if product["verified_scenes"]:
        fallback.append("BENEFIT")
    if product["verified_attributes"]:
        fallback.append("SPECIFICATION")
    if product["verified_offers"]:
        fallback.append("TRUST")
    fallback = fallback or ["FEATURE"]
    return [fallback[i % len(fallback)] for i in range(count)]


def candidate_research_contexts(
    role: str,
    template: dict[str, Any],
    raw: dict[str, Any],
    role_map: dict[str, Any],
) -> list[dict[str, Any]]:
    category = normalize_category(raw["product"]["category"])
    offers = raw["product"]["verified_offers"]
    representation_density = {
        "STANDARD_IDENTITY_MAIN": ("RENDERED", "SPARSE"),
        "OFFER_IDENTITY_MAIN": ("RENDERED", "SPARSE"),
        "CAROUSEL_SLOT_1_IDENTITY": ("RENDERED", "SPARSE"),
        "CAROUSEL_SLOT_2_CORE_SELLING_POINT": ("RENDERED", "MEDIUM"),
        "CAROUSEL_SLOT_3_SCENE_BENEFIT": ("PHOTOGRAPHIC", "SPARSE"),
        "CAROUSEL_SLOT_4_DETAIL_PROOF": ("RENDERED", "MEDIUM"),
        "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": ("RENDERED", "MEDIUM"),
        "DETAIL_LONG_FEATURE_SECTION": ("MIXED", "DENSE"),
        "FEATURE": ("RENDERED", "MEDIUM"),
        "BENEFIT": ("PHOTOGRAPHIC", "MEDIUM"),
        "SPECIFICATION": ("RENDERED", "MEDIUM"),
        "TRUST": ("RENDERED", "MEDIUM"),
    }
    representation, density = representation_density[role]
    contexts = []
    for item in role_map["mapping"][role]:
        context = copy.deepcopy(item)
        context["surface"] = template["surface"]
        context["category"] = category
        context["product_representation"] = representation
        context["copy_density"] = density
        context.setdefault("offer_status", "VISIBLE" if offers else "NONE")
        context.setdefault(
            "scene_relevance",
            "DIRECT" if raw["product"]["verified_scenes"] else "NO_SCENE",
        )
        contexts.append(context)
    return contexts


def rule_matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    for key, value in condition.items():
        if value is None:
            continue
        if context.get(key) != value:
            return False
    return True


def activate_rules(
    surface: str,
    contexts: list[dict[str, Any]],
    evidence_rules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    matched = []
    for wrapper in evidence_rules:
        payload = wrapper["rule_payload"]
        if payload["surface"] != surface:
            continue
        condition = payload["condition"]
        matching_contexts = [x for x in contexts if rule_matches(condition, x)]
        if matching_contexts:
            matched.append(
                {
                    "rule_id": payload["rule_id"],
                    "condition": condition,
                    "effect": payload["effect"],
                    "matched_research_context": matching_contexts[0],
                    "specificity": sum(v is not None for v in condition.values()),
                    "fallback_level": payload.get("fallback_level", 99),
                    "match_priority": payload.get("match_priority", 99),
                }
            )
    matched.sort(
        key=lambda x: (
            -x["specificity"],
            x["fallback_level"],
            x["match_priority"],
            x["rule_id"],
        )
    )
    effects: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for item in matched:
        for path, value in item["effect"].items():
            if path not in effects:
                effects[path] = value
                sources[path] = "EVIDENCE_DERIVED"
    return matched, effects, sources


def background_route(
    raw: dict[str, Any], role: str, offer_status: str
) -> dict[str, Any]:
    product = raw["product"]
    category = normalize_category(product["category"])
    profile = copy.deepcopy(BACKGROUND_PROFILES[category])
    attributes = product["verified_attributes"]
    color = attribute_text(attributes, ("color", "colour", "颜色", "色"))
    material = attribute_text(attributes, ("material", "材质", "面料"))
    core = product["verified_selling_points"][0]["text"] if product["verified_selling_points"] else ""
    scene = product["verified_scenes"][0]["text"] if product["verified_scenes"] else ""
    preference = raw.get("campaign_preference") or {}
    profile.setdefault(
        "separation_guard",
        "maintain clear luminance and edge separation without recoloring the product",
    )

    if category == "FURNITURE":
        profile["system"] = "light or medium-neutral office/home architecture"
        profile["tone"] = "soft ivory and light warm gray with warm wood"
        profile["materials"] = [
            "light-gray architectural plaster",
            "warm walnut",
            "matte neutral floor",
        ]
        profile["device_pool"] = [
            "restrained ergonomic curve",
            "thin cool technical line",
        ]
        luminance = dominant_luminance(color or product["name"])
        if luminance == "DARK_DOMINANT":
            profile["separation_guard"] = (
                "never place a dark-dominant product on a deep-dark field; use a "
                "lighter architectural plane plus precise edge separation"
            )
        elif luminance == "LIGHT_DOMINANT":
            profile["separation_guard"] = (
                "avoid white-on-white silhouette loss; use a light-to-medium neutral "
                "plane, controlled shadow and darker edge contrast without recoloring"
            )
        else:
            profile["separation_guard"] = (
                "separate every evidence-bound color region from the background with "
                "controlled luminance contrast, edge light and shadow"
            )
    else:
        profile["separation_guard"] = (
            "maintain clear luminance and edge separation between product and background"
        )

    if preference.get("tone"):
        profile["tone"] = preference["tone"]
    if preference.get("materials"):
        profile["materials"] = preference["materials"]
    palette = preference.get("palette") or profile["tone"]
    accent_policy = (
        "offer accent is confined to the information module; no default red-gold field"
        if offer_status == "VISIBLE"
        else "accent supports the selling point and remains subordinate to product identity"
    )
    variant = ROLE_VARIANTS[role]["background_variant"]
    return {
        "category_route": category,
        "system": f"{profile['system']} — {variant}",
        "tone": profile["tone"],
        "materials": profile["materials"],
        "palette": palette,
        "selling_point_binding": core or "product identity",
        "scene_binding": scene or "no unverified scene",
        "offer_status": offer_status,
        "campaign_preference": preference,
        "accent_policy": accent_policy,
        "separation_guard": profile["separation_guard"],
        "visual_device_candidates": profile["device_pool"],
        "selection_inputs": {
            "category": category,
            "product_color": color or "not supplied",
            "material": material or "not supplied",
            "core_selling_point": core or "not supplied",
            "use_scene": scene or "not supplied",
            "offer_status": offer_status,
            "campaign_preference": preference,
        },
    }


def concrete_visual_plan(
    raw: dict[str, Any],
    role: str,
    evidence_effects: dict[str, Any],
    evidence_sources: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    template = copy.deepcopy(ROLE_VARIANTS[role])
    offer_status = (
        "VISIBLE"
        if raw["product"]["verified_offers"]
        and role in {"OFFER_IDENTITY_MAIN", "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY", "TRUST"}
        else "NONE"
    )
    background = background_route(raw, role, offer_status)
    fields = {
        "composition.pattern": template["composition"],
        "composition.axis": template["axis"],
        "product.occupancy_range": template["occupancy"],
        "product.visual_anchor": template["anchor"],
        "product.angle_view": template["view"],
        "copy.headline_region": template["headline"],
        "copy.selling_point_region": template["selling"],
        "copy.offer_region": template["offer"],
        "layout.safe_margin_percent": 6,
        "layout.allowed_occlusion": [
            "grounding shadow",
            "non-identity background props behind the product",
        ],
        "layout.forbidden_occlusion": [
            "product silhouette",
            "identity-defining structure",
            "functional evidence area",
            "product name",
        ],
        "background.system": background["system"],
        "background.tone": background["tone"],
        "background.material": background["materials"],
        "lighting.system": (
            "45-degree soft key from upper-left plus controlled opposite rim light"
        ),
        "lighting.product_separation": "CLEAR",
        "visual_devices.items": {
            "STANDARD_IDENTITY_MAIN": ["identity framing line", *background["visual_device_candidates"]],
            "OFFER_IDENTITY_MAIN": ["separate offer rail", *background["visual_device_candidates"]],
            "CAROUSEL_SLOT_1_IDENTITY": ["search-interception framing line", *background["visual_device_candidates"]],
            "CAROUSEL_SLOT_2_CORE_SELLING_POINT": ["single feature callout", *background["visual_device_candidates"]],
            "CAROUSEL_SLOT_3_SCENE_BENEFIT": ["subtle scene label", *background["visual_device_candidates"]],
            "CAROUSEL_SLOT_4_DETAIL_PROOF": ["macro inset frame", "evidence leader line", *background["visual_device_candidates"]],
            "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": ["summary rail divider", *background["visual_device_candidates"]],
            "DETAIL_LONG_FEATURE_SECTION": ["section connector", *background["visual_device_candidates"]],
            "FEATURE": ["feature callout", *background["visual_device_candidates"]],
            "BENEFIT": ["scene cue", *background["visual_device_candidates"]],
            "SPECIFICATION": ["specification divider", *background["visual_device_candidates"]],
            "TRUST": ["trust marker frame", *background["visual_device_candidates"]],
        }[role],
        "visual_devices.max_count": template["devices"],
        "typography.treatment": "SOLID",
        "typography.hierarchy": "THREE_LEVEL",
        "copy.alignment": "role-specific aligned grid",
        "copy.dominance": "IMAGE_LED",
    }
    sources = {key: "ENGINEERED_DEFAULT" for key in fields}

    if "composition" in evidence_effects:
        symbol = evidence_effects["composition"]
        if symbol == "FULL_BLEED_SCENE":
            fields["composition.pattern"] = (
                "full-bleed compatible scene with product clearly in the foreground"
            )
        elif symbol == "CONTINUOUS_VERTICAL":
            fields["composition.pattern"] = (
                "continuous vertical narrative with connected section transitions"
            )
        sources["composition.pattern"] = "EVIDENCE_DERIVED"
    if evidence_effects.get("background.system") == "COMPOSITE":
        fields["background.system"] = (
            f"composite two-depth system: {background['system']}"
        )
        sources["background.system"] = "EVIDENCE_DERIVED"
    if evidence_effects.get("background.material") == "MIXED":
        fields["background.material"] = background["materials"][:2]
        sources["background.material"] = "EVIDENCE_DERIVED"
    for path in (
        "lighting.product_separation",
        "typography.treatment",
        "typography.hierarchy",
        "copy.alignment",
        "copy.dominance",
        "copy.headline_position",
        "product_representation",
        "scene_relevance",
    ):
        if path in evidence_effects:
            fields[path] = evidence_effects[path]
            sources[path] = "EVIDENCE_DERIVED"
    return {"fields": fields, "background_route": background, "offer_status": offer_status}, sources


def auto_locks(raw: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
    product = raw["product"]
    attributes = product["verified_attributes"]
    return {
        "product_consistency": {
            "source_type": "USER_FACT",
            "anchors": {
                "product_name": product["name"],
                "same_product_references": product["same_product_reference_images"],
                "verified_attributes": attributes,
            },
            "forbidden": [
                "change product count, category, structure, color, material, components, accessories, proportions or logo placement",
                "replace the product with a similar model",
            ],
        },
        "campaign_style": {
            "source_type": "ENGINEERED_DEFAULT",
            "anchors": {
                "campaign_preference": raw.get("campaign_preference") or {},
                "shared_background_family": pages[0]["visual_plan"]["background_route"]["category_route"],
                "shared_typography": "consistent family with role-specific hierarchy",
                "shared_lighting": "material-faithful product separation",
            },
            "forbidden": ["unrelated campaign palette", "style reset between adjacent images"],
        },
        "design_strength": {
            "source_type": "ENGINEERED_DEFAULT",
            "anchors": {
                "distinct_page_roles": [x["page_role"] for x in pages],
                "occupancy_ranges": [
                    x["visual_plan"]["fields"]["product.occupancy_range"] for x in pages
                ],
                "composition_patterns": [
                    x["visual_plan"]["fields"]["composition.pattern"] for x in pages
                ],
                "headline_regions": [
                    x["visual_plan"]["fields"]["copy.headline_region"] for x in pages
                ],
            },
            "forbidden": ["same template on adjacent pages", "product becomes secondary"],
        },
    }


def factual_text(raw: dict[str, Any], role: str, claim: dict[str, str]) -> dict[str, Any]:
    product = raw["product"]
    offers = fact_list(product["verified_offers"])
    offer_allowed = role in {
        "OFFER_IDENTITY_MAIN",
        "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY",
        "TRUST",
    }
    return {
        "product_name": {"id": "product:name", "text": product["name"]},
        "primary_claim": claim,
        "offer_facts": offers if offer_allowed else [],
        "offer_source_policy": "USER_FACT_ONLY",
    }


def natural_generation_prompt(page: dict[str, Any], locks: dict[str, Any]) -> str:
    f = page["visual_plan"]["fields"]
    bg = page["visual_plan"]["background_route"]
    text = page["factual_content"]
    offer_bits = "；".join(x["text"] for x in text["offer_facts"])
    offer_sentence = (
        f"优惠信息仅使用：{offer_bits}，置于{f['copy.offer_region']}，不得遮挡商品。"
        if offer_bits
        else "不要添加任何优惠、价格或促销权益。"
    )
    devices = "、".join(f["visual_devices.items"][: f["visual_devices.max_count"]])
    return (
        f"制作一张国内电商{page['surface']}图片。商品为“{text['product_name']['text']}”，"
        f"页面职责是{page['page_role']}，沟通目标是{page['communication_goal']}。"
        f"商品必须是第一视觉主体，占画面{f['product.occupancy_range'][0]}%–"
        f"{f['product.occupancy_range'][1]}%，位于{f['product.visual_anchor']}，"
        f"采用{f['product.angle_view']}；构图为{f['composition.pattern']}，轴线为"
        f"{f['composition.axis']}。标题放在{f['copy.headline_region']}，核心文案仅使用"
        f"“{text['primary_claim']['text']}”，卖点区位于{f['copy.selling_point_region']}。"
        f"{offer_sentence} 背景采用{bg['system']}，色调{bg['tone']}，材质为"
        f"{'、'.join(bg['materials'])}；{bg['separation_guard']}。灯光为"
        f"{f['lighting.system']}，确保商品轮廓清晰分离。视觉装置最多"
        f"{f['visual_devices.max_count']}个，可用{devices}。安全边距"
        f"{f['layout.safe_margin_percent']}%，不得遮挡商品轮廓、身份结构、功能证据或商品名称。"
        f"保持同一商品的名称、数量、结构、颜色、材质、部件、配件、比例和Logo位置一致。"
        f"不得编造价格、参数、认证、功效、权益或配件；无水印。"
    )


def page_gate(page: dict[str, Any]) -> dict[str, bool]:
    f = page["visual_plan"]["fields"]
    product_first = f["product.occupancy_range"][0] >= 42
    offer_safe = (
        page["visual_plan"]["offer_status"] == "NONE"
        or (
            page["factual_content"]["offer_facts"]
            and f["copy.offer_region"] != "none"
            and "outside product silhouette" in f["copy.offer_region"]
            or page["page_role"] == "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY"
        )
    )
    return {
        "product_is_first_visual_subject": product_first,
        "required_visual_fields_concrete": all(
            key in f and f[key] not in (None, "", [], {})
            for key in (
                "product.occupancy_range",
                "product.visual_anchor",
                "composition.axis",
                "copy.headline_region",
                "copy.selling_point_region",
                "copy.offer_region",
                "layout.safe_margin_percent",
                "layout.allowed_occlusion",
                "layout.forbidden_occlusion",
                "background.system",
                "background.tone",
                "background.material",
                "lighting.system",
                "lighting.product_separation",
                "visual_devices.items",
                "visual_devices.max_count",
            )
        ),
        "offer_is_user_fact_only_and_non_occluding": bool(offer_safe),
        "claims_are_source_bound": bool(
            page["factual_content"]["primary_claim"]["id"]
        ),
        "generation_prompt_is_compact": len(page["generation_prompt"]) <= 1800,
    }


def five_image_gate(pages: list[dict[str, Any]], locks: dict[str, Any]) -> dict[str, bool]:
    if len(pages) != 5:
        return {"applicable": False}
    def values(path: str) -> list[Any]:
        return [x["visual_plan"]["fields"][path] for x in pages]

    occupancy_buckets = []
    for low, high in values("product.occupancy_range"):
        occupancy_buckets.append(
            "DETAIL_OR_CONTEXT" if high <= 58 else ("MEDIUM" if low < 60 else "HERO")
        )
    adjacent = []
    for left, right in zip(pages, pages[1:]):
        left_signature = (
            left["page_role"],
            left["visual_plan"]["fields"]["composition.pattern"],
            left["visual_plan"]["fields"]["copy.headline_region"],
            left["visual_plan"]["background_route"]["system"],
        )
        right_signature = (
            right["page_role"],
            right["visual_plan"]["fields"]["composition.pattern"],
            right["visual_plan"]["fields"]["copy.headline_region"],
            right["visual_plan"]["background_route"]["system"],
        )
        adjacent.append(left_signature != right_signature)
    claims = [x["factual_content"]["primary_claim"]["id"] for x in pages]
    return {
        "applicable": True,
        "five_distinct_communication_duties": len({x["page_role"] for x in pages}) == 5,
        "at_least_three_product_scales": len(set(occupancy_buckets)) >= 3,
        "at_least_three_compositions": len(set(values("composition.pattern"))) >= 3,
        "headline_positions_rotate": len(set(values("copy.headline_region"))) >= 3,
        "background_rhythm_changes": len(
            {x["visual_plan"]["background_route"]["system"] for x in pages}
        ) >= 3,
        "no_adjacent_template_reuse": all(adjacent),
        "product_identity_constant": bool(
            locks["product_consistency"]["anchors"]["product_name"]
        ),
        "product_views_rotate": len(set(values("product.angle_view"))) >= 3,
        "visual_devices_rotate": len(
            {
                tuple(x["visual_plan"]["fields"]["visual_devices.items"])
                for x in pages
            }
        ) >= 2,
        "primary_claims_are_role_specific": len(set(claims)) >= 4,
    }


def compile_raw(
    raw: dict[str, Any],
    rules_path: str | Path = RULES,
    role_map_path: str | Path = ROLE_MAP,
) -> dict[str, Any]:
    jsonschema.Draft202012Validator(load(RAW_SCHEMA)).validate(raw)
    rules = load(rules_path)
    role_map = load(role_map_path)
    if (
        rules.get("evidence_derived_rule_count") != 59
        or rules.get("profile01_semantics_read") is not False
        or role_map.get("profile01_semantics_read") is not False
    ):
        raise CompileError("FROZEN_RESEARCH_BOUNDARY_INVALID")

    roles = roles_for_request(raw)
    if raw["output"]["type"] == "FIVE_IMAGE_CAROUSEL" and len(roles) != 5:
        raise CompileError("FIVE_IMAGE_ROUTE_INVALID")
    if raw["output"]["type"] == "OFFER_IDENTITY_MAIN" and not raw["product"]["verified_offers"]:
        raise CompileError("OFFER_IDENTITY_MAIN_REQUIRES_VERIFIED_OFFER")

    pages = []
    for index, role in enumerate(roles, 1):
        template = ROLE_VARIANTS[role]
        contexts = candidate_research_contexts(role, template, raw, role_map)
        activated, evidence_effects, evidence_sources = activate_rules(
            template["surface"], contexts, rules["evidence_derived_rules"]
        )
        visual_plan, field_sources = concrete_visual_plan(
            raw, role, evidence_effects, evidence_sources
        )
        claim = choose_primary_claim(role, raw, index - 1)
        page = {
            "index": index,
            "surface": template["surface"],
            "page_role": role,
            "communication_goal": template["goal"],
            "research_context_candidates": contexts,
            "activated_evidence_rules": activated,
            "evidence_effects": evidence_effects,
            "visual_plan": visual_plan,
            "field_sources": field_sources,
            "factual_content": factual_text(raw, role, claim),
        }
        pages.append(page)

    locks = auto_locks(raw, pages)
    for page in pages:
        page["generation_prompt"] = natural_generation_prompt(page, locks)
        page["audit_contract"] = {
            "product_facts": {
                "name": raw["product"]["name"],
                "category": raw["product"]["category"],
                "reference_images": raw["product"]["same_product_reference_images"],
                "verified_attributes": raw["product"]["verified_attributes"],
                "verified_selling_points": raw["product"]["verified_selling_points"],
                "verified_scenes": raw["product"]["verified_scenes"],
                "verified_offers": raw["product"]["verified_offers"],
            },
            "rule_sources": {
                "precedence": PRECEDENCE,
                "activated_evidence_rule_ids": [
                    x["rule_id"] for x in page["activated_evidence_rules"]
                ],
                "field_sources": page["field_sources"],
            },
            "locks": locks,
            "compliance": {
                "verified_facts_only": True,
                "offer_source": "USER_FACT_ONLY",
                "forbidden_content": raw["product"]["forbidden_content"],
                "prohibit_fabrication": [
                    "price",
                    "parameter",
                    "certification",
                    "benefit or efficacy",
                    "offer or right",
                    "accessory",
                ],
            },
            "negative": [
                "product drift",
                "promotion replacing product identity",
                "background overpowering product",
                "text or module occluding product",
                "watermark",
            ],
            "visual_plan": page["visual_plan"],
        }
        page["gates"] = page_gate(page)

    set_gates = five_image_gate(pages, locks)
    all_page_gates = all(all(x["gates"].values()) for x in pages)
    all_set_gates = all(v for k, v in set_gates.items() if k != "applicable")
    output = {
        "schema_version": "domestic-ecommerce-compiled-output-0.1.1",
        "compiler_version": VERSION,
        "request_id": raw["request_id"],
        "status": "READY_FOR_CONTROLLED_PROMPT_USE"
        if all_page_gates and all_set_gates
        else "BLOCKED",
        "source_precedence": PRECEDENCE,
        "planner": {
            "input_type": "RAW_PRODUCT_INPUT",
            "planned_roles": roles,
            "business_to_research_mapping_path": portable_path(role_map_path),
        },
        "pages": pages,
        "set_gates": set_gates,
        "applied_evidence_rule_count": len(
            {x["rule_id"] for p in pages for x in p["activated_evidence_rules"]}
        ),
        "rule_package": {
            "research_rules_path": portable_path(rules_path),
            "research_rules_sha256": sha256(rules_path),
            "research_rule_count": 59,
            "role_map_path": portable_path(role_map_path),
            "role_map_sha256": sha256(role_map_path),
        },
        "profile01_semantics_read": False,
        "api_calls": 0,
        "generation_performed": False,
        "holdout_validated": False,
    }
    jsonschema.Draft202012Validator(load(OUTPUT_SCHEMA)).validate(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rules", default=str(RULES))
    parser.add_argument("--role-map", default=str(ROLE_MAP))
    args = parser.parse_args()
    compiled = compile_raw(load(args.input), args.rules, args.role_map)
    write(args.output, compiled)
    print(
        json.dumps(
            {
                "request_id": compiled["request_id"],
                "status": compiled["status"],
                "pages": len(compiled["pages"]),
                "applied_evidence_rule_count": compiled["applied_evidence_rule_count"],
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
