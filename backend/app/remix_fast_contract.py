"""Typed planner wire contracts for the immutable fast remix Skill adapter."""

from typing import Any

PREPARE_PROFILE = "aurora_remix_fast_prepare"
REVIEW_PROFILE = "aurora_remix_fast_review"
CHECKS = ("aspect_ratio", "watermark", "source_leakage", "product_identity")
REFERENCE_FIELDS = (
    "subject_and_scale",
    "layout_and_reading_path",
    "carrier_morphology",
    "commercial_function",
    "camera_light_depth",
    "typography_structure",
    "brand_and_human_presence",
    "meaningful_absences",
    "cross_screen_role",
)
GEOMETRY_FIELDS = ("silhouette", "proportions", "structure", "component_relationships")


def _text() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 6000}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def response_format(profile: str) -> dict[str, Any]:
    if profile == PREPARE_PROFILE:
        schema = _object(
            {
                "geometry_lock": _object({key: _text() for key in GEOMETRY_FIELDS}),
                "identity_constraints": {
                    **_text(),
                    "description": "Identity constraints: one string, not an array.",
                },
                "reference_facts": _object({key: _text() for key in REFERENCE_FIELDS}),
                "page_duty": _text(),
                "buyer_conclusion": _text(),
                "generation_brief": {
                    **_text(),
                    "description": (
                        "Normalized layout and whitespace coordinates. "
                        "Use the requested "
                        "target canvas, never infer output size from source pixels. "
                        "Do not insert another Target canvas directive."
                    ),
                },
                "approved_visible_copy": {
                    "type": "array",
                    "maxItems": 30,
                    "items": {"type": "string", "maxLength": 300},
                },
                "critical_regions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": _object(
                        {
                            "box": {
                                "type": "array",
                                "minItems": 4,
                                "maxItems": 4,
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "feature": _text(),
                        }
                    ),
                },
                "product_present": {"type": "boolean"},
            }
        )
    elif profile == REVIEW_PROFILE:
        schema = _object(
            {
                "pixel_inspected": {"type": "boolean"},
                "checks": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": _object(
                        {
                            "name": {"type": "string", "enum": list(CHECKS)},
                            "passed": {"type": "boolean"},
                            "evidence": {**_text(), "minLength": 4},
                        }
                    ),
                },
                "differences": {"type": "array", "items": {"type": "string"}},
                "unresolved": {"type": "array", "items": {"type": "string"}},
            }
        )
    else:
        raise ValueError("Unsupported fast remix planner profile")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": profile,
            "strict": False,
            "schema": schema,
        },
    }
