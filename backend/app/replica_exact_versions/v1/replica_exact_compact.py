"""Stage-scoped visual service; native metadata and transitions stay on the server."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from .replica_layout import (
    bind_module_text,
    validate_product_evidence,
    validate_reference_layouts,
)
from .replica_native import (
    NativeReplicaRun,
    ReplicaAnalysisUnavailable,
    ReplicaNativeError,
    ReplicaSegmentationInvalid,
    segmentation_errors,
    validate_module_constraints,
    write_json,
)

VERSION = "exact-compact-v1"
PROMPTS = Path(__file__).with_name("replica_exact_prompts")
OBSERVATIONS = (
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
PLAN_DIMENSIONS = (
    "layout_geometry",
    "carrier_morphology",
    "commercial_function",
    "expression",
    "brand_human_presence",
    "typography_structure",
    "meaningful_absences",
    "aspect_ratio",
)
ARTWORK_DIMENSIONS = (
    "product_identity",
    "subject_and_scale",
    "layout_and_reading_path",
    "carrier_morphology",
    "commercial_function",
    "camera_light_depth",
    "typography_structure",
    "brand_and_human_presence",
    "meaningful_absences",
    "copy_and_claim_safety",
)
SERVER_FIELDS = {
    "sha256",
    "source_hash",
    "references",
    "recommended_canvas",
    "output_canvas",
    "plan_contract_version",
    "fidelity_mode",
    "sequence_contract",
    "model_protocol",
    "artwork",
    "ai_reference_observation",
}


class CompactResponseInvalid(ReplicaAnalysisUnavailable):
    code = "REPLICA_MODEL_RESPONSE_INVALID"
    message = "图片分析结果未通过校验，本次已停止；原始素材和原因已保留。"

    def __init__(self, detail: str) -> None:
        ReplicaNativeError.__init__(self, detail)


class CompactScreenBoundaryMismatch(ReplicaSegmentationInvalid):
    message = "参考图屏数与逐屏边界未能一致核对，本次已停止；原始素材已保留。"

    def __init__(self, detail: str) -> None:
        ReplicaNativeError.__init__(self, detail)


def protocol_binding() -> dict[str, str]:
    digest = hashlib.sha256()
    for path in [Path(__file__), *sorted(PROMPTS.glob("*.md"))]:
        digest.update(path.name.encode())
        digest.update(
            path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode()
        )
    return {"version": VERSION, "content_sha256": digest.hexdigest()}


def phase_for(stage: str) -> str:
    for prefix, phase in (
        ("identity-screen-split", "screen_split"),
        ("identity-screen-inventory", "screen_inventory"),
        ("identity", "intake"),
        ("exact-product-evidence", "product"),
        ("exact-reference-layout", "layout"),
        ("exact-plan-audit", "review_plan"),
        ("exact-plan", "plan"),
        ("exact-pixel-audit", "review_artwork"),
        ("exact-repair", "repair"),
    ):
        if stage.startswith(prefix):
            return phase
    raise CompactResponseInvalid("Unsupported compact visual stage")


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: clean(v)
            for k, v in value.items()
            if k not in SERVER_FIELDS
            and not k.startswith("_")
            and (k in OBSERVATIONS or not k.endswith(("_path", "_paths", "_sha256")))
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def arr(item: dict[str, Any], **limits: Any) -> dict[str, Any]:
    return {"type": "array", "items": item, **limits}


def text(minimum: int = 8, maximum: int = 1200) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def rectangle(normalized: bool = True) -> dict[str, Any]:
    limit = {"maximum": 1000} if normalized else {}
    return obj(
        {
            "x": {"type": "integer", "minimum": 0, **limit},
            "y": {"type": "integer", "minimum": 0, **limit},
            "width": {"type": "integer", "minimum": 1, **limit},
            "height": {"type": "integer", "minimum": 1, **limit},
        }
    )


def rectangle_edges(
    rect: dict[str, int], width: int, height: int, field: str
) -> list[int]:
    """Translate an explicitly declared XYWH rectangle; never infer or clip it."""
    if (
        not isinstance(rect, dict)
        or set(rect) != {"x", "y", "width", "height"}
        or any(type(v) is not int for v in rect.values())
    ):
        raise ValueError(f"{field}: rect requires integer x, y, width and height")
    x, y, w, h = (rect[k] for k in ("x", "y", "width", "height"))
    if not (0 <= x < x + w <= width and 0 <= y < y + h <= height):
        raise ValueError(
            f"{field}: rect width/height must be positive and x+width/y+height "
            f"must fit {width}x{height}; received {rect}"
        )
    return [x, y, x + w, y + h]


def observation() -> dict[str, Any]:
    return obj({key: text(8, 600) for key in OBSERVATIONS})


def comparisons(dimensions: tuple[str, ...]) -> dict[str, Any]:
    return arr(
        obj(
            {
                "dimension": {"enum": list(dimensions)},
                "match": {"type": "boolean"},
                "evidence": text(12, 800),
            }
        ),
        minItems=len(dimensions),
        maxItems=len(dimensions),
    )


def targets(data: dict[str, Any]) -> list[dict[str, Any]]:
    units = data["physical_units"]
    if not data.get("previous_plan"):
        return units
    failed = {
        row["unit_id"]
        for row in data["prior_visual_audit"]["unit_audits"]
        if row["decision"] == "FAIL"
    }
    if not failed or not failed <= {u["id"] for u in units}:
        raise CompactResponseInvalid("Plan repair requires actual failed source units")
    return [u for u in units if u["id"] in failed]


def response_schema(phase: str) -> dict[str, Any]:
    index = {"type": "integer", "minimum": 0}
    anchors = arr(
        obj({"label": text(2, 200), "rect": rectangle()}), minItems=1, maxItems=24
    )
    if phase == "intake":
        return obj(
            {
                "product_geometry": arr(text(), minItems=3),
                "product_crops": arr(
                    obj({"product_index": index, "rect": rectangle(False)}), minItems=1
                ),
                "columns": arr(
                    obj(
                        {
                            "reference_index": index,
                            "column_index": index,
                            "rect": rectangle(False),
                            "column_boundary_reason": text(12),
                        }
                    ),
                    minItems=1,
                    maxItems=60,
                ),
                "route": {
                    "enum": [
                        "SEARCH_COVER",
                        "PROMO_POSTER",
                        "BANNER",
                        "SOCIAL_COMMERCE_CARD",
                        "CAROUSEL_SET",
                        "DETAIL_PAGE",
                        "MARKETING_VISUAL_SET",
                    ]
                },
            }
        )
    if phase in {"screen_split", "screen_inventory"}:
        column: dict[str, Any] = {
            "column_id": text(1, 40),
            "screens": arr(
                obj({"headline": text(1, 400), "buyer_question": text(8, 800)}),
                minItems=1,
                maxItems=60,
            ),
        }
        if phase == "screen_split":
            column["boundary_ys"] = arr({"type": "integer", "minimum": 1}, maxItems=59)
        properties: dict[str, Any] = {
            "columns": arr(obj(column), minItems=1, maxItems=60)
        }
        if phase == "screen_inventory":
            properties["all_columns_covered"] = {"type": "boolean"}
        return obj(properties)
    if phase == "product":
        return obj(
            {
                "products": arr(
                    obj(
                        {
                            "product_index": index,
                            "readable_marks": arr(
                                obj({"text": text(1, 2000), "rect": rectangle(False)})
                            ),
                            "uncertainty_notes": text(8, 4000),
                        }
                    ),
                    minItems=1,
                )
            }
        )
    if phase == "layout":
        region = obj(
            {
                "id": text(1, 40),
                "role": {
                    "enum": [
                        "brand",
                        "headline",
                        "connector",
                        "subtitle",
                        "badge",
                        "body",
                        "footnote",
                    ]
                },
                "font_tier": {"enum": ["dominant", "secondary", "small"]},
                "line_group": text(1, 100),
                "observed_text": text(0, 2000),
                "rect": rectangle(),
            }
        )
        return obj(
            {
                "units": arr(
                    obj(
                        {
                            "reference_unit_id": text(1, 40),
                            "reference_observation": observation(),
                            "reference_product_present": {"type": "boolean"},
                            "layout_anchors": anchors,
                            "has_poster_text": {"type": "boolean"},
                            "text_regions": arr(region, maxItems=48),
                            "typography_notes": text(8, 4000),
                        }
                    ),
                    minItems=1,
                )
            }
        )
    if phase == "plan":
        binding = {
            "oneOf": [
                obj(
                    {
                        "region_id": text(1, 40),
                        "action": {"const": "render"},
                        "copy_unit_index": index,
                        "fragment": text(1, 2000),
                    }
                ),
                obj(
                    {
                        "region_id": text(1, 40),
                        "action": {"const": "omit_unsupported_copy"},
                        "reason": text(),
                        "retain_carrier": {"const": True},
                    }
                ),
            ]
        }
        return obj(
            {
                "modules": arr(
                    obj(
                        {
                            "reference_unit_id": text(1, 40),
                            "visual_contract": observation(),
                            "page_duty": text(),
                            "buyer_conclusion": text(),
                            "generation_brief": text(8, 4000),
                            "copy_units": arr(text(1, 2000)),
                            "product_visibility": {
                                "enum": ["visible_product", "context_only"]
                            },
                            "required_identity_checks": arr(text()),
                            "not_applicable_checks": arr(text()),
                            "text_bindings": arr(binding),
                        }
                    ),
                    minItems=1,
                )
            }
        )
    if phase == "review_plan":
        return obj(
            {
                "unit_audits": arr(
                    obj(
                        {
                            "unit_id": {**text(4, 40), "pattern": r"^R[0-9]{3,}$"},
                            "fresh_pixel_observation": observation(),
                            "comparisons": comparisons(PLAN_DIMENSIONS),
                            "material_mismatches": arr(text()),
                            "decision": {"enum": ["PASS", "FAIL"]},
                        }
                    ),
                    minItems=1,
                )
            }
        )
    if phase == "review_artwork":
        return obj(
            {
                "fresh_reference_observation": observation(),
                "fresh_artwork_observation": observation(),
                "comparisons": comparisons(ARTWORK_DIMENSIONS),
                "major_mismatches": arr(text()),
                "decision": {"enum": ["PASS", "FAIL"]},
                "product_geometry_match": {"type": "boolean"},
            }
        )
    if phase == "repair":
        return obj({"single_edit_instruction": text(8, 6000)})
    raise CompactResponseInvalid("Missing response schema")


def validate_review(
    row: dict[str, Any], dimensions: tuple[str, ...], mismatch_key: str
) -> None:
    checks = row["comparisons"]
    if [r["dimension"] for r in checks] != list(dimensions):
        raise ValueError("Return all comparison dimensions once in the requested order")
    if len({r["evidence"] for r in checks}) != len(checks):
        raise ValueError("Each dimension needs its own actual pixel evidence")
    expected = (
        "PASS" if all(r["match"] for r in checks) and not row[mismatch_key] else "FAIL"
    )
    if row["decision"] != expected:
        raise ValueError(
            "Decision conflicts with observations; reassess and return "
            "a consistent actual verdict"
        )


def plan_proposal(plan: dict[str, Any], unit_id: str) -> dict[str, Any]:
    module = next(m for m in plan["modules"] if m["reference_unit_id"] == unit_id)
    scope = next(
        s for s in plan["_compact_scopes"]["modules"] if s["module_id"] == module["id"]
    )
    return {
        k: deepcopy(v)
        for source in (module, scope)
        for k, v in source.items()
        if k
        in {
            "reference_unit_id",
            "visual_contract",
            "page_duty",
            "buyer_conclusion",
            "generation_brief",
            "copy_units",
            "product_visibility",
            "required_identity_checks",
            "not_applicable_checks",
            "text_bindings",
        }
    }


def validate_copy_coverage(proposed: dict[str, Any]) -> None:
    """Surplus approved text must not silently disappear from the render bindings."""
    for index, copy in enumerate(proposed["copy_units"]):
        covered: set[int] = set()
        for binding in proposed["text_bindings"]:
            if (
                binding.get("action") != "render"
                or binding.get("copy_unit_index") != index
            ):
                continue
            fragment = binding["fragment"]
            start = copy.find(fragment)
            while start >= 0:
                covered.update(range(start, start + len(fragment)))
                start = copy.find(fragment, start + 1)
        if any(not char.isspace() and i not in covered for i, char in enumerate(copy)):
            raise ValueError(
                f"{proposed['reference_unit_id']}: "
                f"copy_units[{index}] has unbound text; "
                "bind the complete copy within existing reference regions or remove "
                "surplus copy. Do not add a carrier or another headline."
            )


def materialize_plan(value: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    requested = targets(data)
    if [m["reference_unit_id"] for m in value["modules"]] != [
        u["id"] for u in requested
    ]:
        raise ValueError("Return exactly the requested reference units in order")
    replacements = {m["reference_unit_id"]: m for m in value["modules"]}
    prior = data.get("previous_plan") or {}
    old = {m["reference_unit_id"]: m for m in prior.get("modules", [])}
    scopes = {
        r["module_id"]: r for r in prior.get("_compact_scopes", {}).get("modules", [])
    }
    modules, references = [], []
    for index, unit in enumerate(data["physical_units"]):
        uid, mid = unit["id"], f"M{index + 1:03}"
        layout = data["reference_layouts"][uid]
        references.append(
            {
                **{
                    k: deepcopy(v)
                    for k, v in unit.items()
                    if k not in {"recommended_canvas", "source_reference_index"}
                },
                "ai_reference_observation": deepcopy(layout["reference_observation"]),
            }
        )
        if uid not in replacements:
            if uid not in old or old[uid]["id"] != mid or mid not in scopes:
                raise ValueError(
                    "Unchanged modules require a complete validated previous plan"
                )
            modules.append(deepcopy(old[uid]))
            continue
        proposed = replacements[uid]
        validate_copy_coverage(proposed)
        if uid in old and proposed == plan_proposal(prior, uid):
            raise ValueError(
                f"{uid}: a failed plan must actually change before another audit; "
                "repair the proposal or bindings, never resample an unchanged FAIL"
            )
        modules.append(
            {
                "id": mid,
                "reference_unit_id": uid,
                "reference_path": unit["crop_path"],
                "output_canvas": deepcopy(unit["recommended_canvas"]),
                **{
                    k: deepcopy(proposed[k])
                    for k in (
                        "visual_contract",
                        "page_duty",
                        "buyer_conclusion",
                        "generation_brief",
                        "copy_units",
                    )
                },
            }
        )
        scopes[mid] = {
            "module_id": mid,
            "reference_sha256": unit["crop_sha256"],
            **{
                k: deepcopy(proposed[k])
                for k in (
                    "product_visibility",
                    "required_identity_checks",
                    "not_applicable_checks",
                    "text_bindings",
                )
            },
            "layout_anchors": deepcopy(layout["layout_anchors"]),
            "composition_notes": proposed["visual_contract"]["layout_and_reading_path"],
        }
    plan: dict[str, Any] = {
        "plan_contract_version": "6",
        "fidelity_mode": "REMIX_STRICT",
        "reference_units": references,
        "sequence_contract": {
            "segmentation_basis": "SCREEN_NOT_COLUMN",
            "reading_order": "COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM",
            "declared_screen_count": len(references),
            "source_unit_order": [u["id"] for u in references],
        },
        "modules": modules,
        "_compact_scopes": {"modules": [scopes[m["id"]] for m in modules]},
    }
    try:
        scoped = validate_module_constraints(
            plan["_compact_scopes"],
            {
                m["id"]: u["crop_sha256"]
                for m, u in zip(modules, references, strict=True)
            },
        )
    except ReplicaNativeError as exc:
        raise ValueError(str(exc)) from exc
    bind_module_text(scoped, modules, data["reference_layouts"])
    return plan


class CompactExactReplicaRun(NativeReplicaRun):
    """Frozen visual protocol over the existing deterministic, bounded native guard."""

    observation_attempts = 1
    contract_attempts = 1
    planning_revisions = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.method != "exact":
            raise CompactResponseInvalid(
                "Compact protocol only supports exact replication"
            )
        self.binding = protocol_binding()
        self.context: dict[str, Any] = {}

    def prepared_module_constraints(self, plan: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(plan["_compact_scopes"])

    def image_prompt(self, prompt: str) -> str:
        header = (
            "Create ONE complete Chinese ecommerce visual for the current token only."
        )
        if prompt.count(header) != 1:
            raise CompactResponseInvalid(
                "Native image request has an unknown visual header"
            )
        target = json.dumps(self.context["language"], ensure_ascii=False)
        prompt = prompt.replace(
            header,
            "Create ONE complete ecommerce visual for the current token. "
            f"The target language for new poster copy is {target}.",
            1,
        )
        return prompt + "\n\n" + (PROMPTS / "generation.md").read_text(encoding="utf-8")

    async def snapshot(self) -> dict[str, Any]:
        return {**await super().snapshot(), "model_protocol": self.binding}

    async def restore(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("model_protocol") != self.binding:
            raise CompactResponseInvalid(
                "Visual protocol changed; create a new analysis "
                "instead of reusing this plan"
            )
        await super().restore(snapshot)
        if self.context.get("model_protocol") != self.binding:
            raise CompactResponseInvalid(
                "Visual protocol context does not match its snapshot"
            )

    def _inputs(
        self, phase: str, data: dict[str, Any], explicit: list[Path] | None
    ) -> list[Path]:
        if explicit is not None:
            return explicit
        units = targets(data) if phase == "plan" else data.get("reference_units", [])
        return [
            *[Path(p) for p in self.context["product_paths"]],
            *[Path(u["crop_path"]) for u in units],
        ]

    def _data(self, phase: str, data: dict[str, Any]) -> dict[str, Any]:
        if phase == "intake":
            return {k: data[k] for k in ("product_dimensions", "reference_dimensions")}
        if phase in {"screen_split", "screen_inventory"}:
            return clean(
                {
                    "columns": data["columns"],
                    "reference_dimensions": data.get("reference_dimensions", []),
                    **(
                        {"boundary_mismatch": data["boundary_mismatch"]}
                        if phase == "screen_split" and "boundary_mismatch" in data
                        else {}
                    ),
                }
            )
        if phase == "product":
            return {"products": clean(data["products"])}
        if phase == "layout":
            return {"physical_units": clean(data["physical_units"])}
        if phase == "plan":
            units = targets(data)
            prior = data.get("previous_plan") or {}
            return clean(
                {
                    **{
                        k: data.get(k)
                        for k in (
                            "user_facts",
                            "language",
                            "platform",
                            "product_evidence",
                        )
                    },
                    "requested_units": units,
                    "reference_layouts": {
                        u["id"]: data["reference_layouts"][u["id"]] for u in units
                    },
                    "sequence_summary": [
                        {
                            k: m[k]
                            for k in (
                                "reference_unit_id",
                                "page_duty",
                                "buyer_conclusion",
                                "copy_units",
                            )
                        }
                        for m in prior.get("modules", [])
                    ],
                    "previous_targets": [plan_proposal(prior, u["id"]) for u in units]
                    if prior
                    else [],
                    "prior_audit": data.get("prior_visual_audit"),
                }
            )
        if phase == "review_plan":
            modules = {m["reference_unit_id"]: m for m in data["modules"]}
            scopes = {
                s["module_id"]: s
                for s in data.get("_compact_scopes", {}).get("modules", [])
            }
            return clean(
                {
                    "audit_subject": "written_plan_before_rendering",
                    "user_facts": data.get("user_facts", ""),
                    "product_evidence": data.get("product_evidence", []),
                    "review_targets": [
                        {
                            "reference_unit_id": u["id"],
                            "reference_screen": {
                                k: v for k, v in u.items() if k != "id"
                            },
                            "proposed_screen": {
                                k: v
                                for k, v in modules[u["id"]].items()
                                if k not in {"id", "reference_unit_id"}
                            },
                            "identity_scope": {
                                k: v
                                for k, v in scopes.get(
                                    modules[u["id"]]["id"], {}
                                ).items()
                                if k != "module_id"
                            },
                            "canvas_binding": {
                                "reference_pixels": [
                                    u["pixel_width"],
                                    u["pixel_height"],
                                ],
                                "planned_pixels": [
                                    modules[u["id"]]["output_canvas"]["width"],
                                    modules[u["id"]]["output_canvas"]["height"],
                                ],
                            },
                        }
                        for u in data["reference_units"]
                    ],
                }
            )
        return clean(data)

    def _image_order(
        self, paths: list[Path], data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        products = self.context.get("product_paths", [])
        references = self.context.get("reference_paths", [])
        column_paths = {
            column["crop_path"]: column
            for column in self.context.get("reference_columns", [])
        }
        units = data.get("physical_units", data.get("reference_units", []))
        unit_paths = {u["crop_path"]: u["id"] for u in units}
        module = data.get("module", {})
        if module.get("reference_path"):
            unit_paths[module["reference_path"]] = module["reference_unit_id"]
        result = []
        for index, path in enumerate(paths):
            name = str(path)
            row: dict[str, Any] = {"image": index + 1}
            if name in products:
                row.update(role="product_original", product_index=products.index(name))
            elif name in unit_paths:
                row.update(role="reference_screen", reference_unit_id=unit_paths[name])
            elif name in references:
                row.update(
                    role="reference_original", reference_index=references.index(name)
                )
            elif name in column_paths:
                column = column_paths[name]
                row.update(role="reference_column", column_id=column["column_id"])
            elif name == data.get("artwork"):
                row.update(role="current_artwork")
            else:
                raise CompactResponseInvalid(
                    "Unexpected visual input outside declared sources"
                )
            result.append(row)
        return result

    def _bind(
        self, phase: str, value: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        value = deepcopy(value)
        if phase == "intake":
            for key, index_key, dimensions in (
                ("product_crops", "product_index", data["product_dimensions"]),
                ("columns", "reference_index", data["reference_dimensions"]),
            ):
                for i, row in enumerate(value[key]):
                    index = row[index_key]
                    if not 0 <= index < len(dimensions):
                        raise ValueError(
                            f"{key}[{i}] must name a supplied source index"
                        )
                    source = dimensions[index]
                    row["bbox"] = rectangle_edges(
                        row.pop("rect"),
                        source["width"],
                        source["height"],
                        f"{key}[{i}]",
                    )
            # Validate column bounds with the existing source-coordinate guard.
            # These provisional rows are never written as physical screen units.
            columns = value["columns"]
            provisional = {
                **value,
                "units": [
                    {
                        **column,
                        "screen_index": 0,
                        "screen_boundary_reason": column["column_boundary_reason"],
                    }
                    for column in columns
                ],
            }
            errors = segmentation_errors(
                provisional,
                [(r["width"], r["height"]) for r in data["product_dimensions"]],
                [(r["width"], r["height"]) for r in data["reference_dimensions"]],
            )
            if errors:
                raise ValueError("; ".join(errors))
            order = [(c["reference_index"], c["column_index"]) for c in columns]
            if order != sorted(order):
                raise ValueError("List columns in reference-major, left-to-right order")
            for reference in range(len(data["reference_dimensions"])):
                group = [c for c in columns if c["reference_index"] == reference]
                if [c["column_index"] for c in group] != list(range(len(group))):
                    raise ValueError("Column indices must start at zero without gaps")
                if any(
                    left["bbox"][2] > right["bbox"][0]
                    for left, right in zip(group, group[1:], strict=False)
                ):
                    raise ValueError("Reference columns must not overlap horizontally")
        elif phase in {"screen_split", "screen_inventory"}:
            expected = data["columns"]
            if [c["column_id"] for c in value["columns"]] != [
                c["column_id"] for c in expected
            ]:
                raise ValueError("Observe every declared column once in source order")
            if phase == "screen_split":
                for column, source in zip(value["columns"], expected, strict=True):
                    cuts = column["boundary_ys"]
                    if cuts != sorted(set(cuts)) or any(
                        not 0 < y < source["height"] for y in cuts
                    ):
                        raise ValueError(
                            "Screen boundaries must increase inside column pixels"
                        )
                    if len(column["screens"]) != len(cuts) + 1:
                        raise ValueError(
                            "Describe one screen per resulting physical interval"
                        )
        elif phase == "product":
            sources = {p["product_index"]: p for p in data["products"]}
            for row in value["products"]:
                source = sources.get(row["product_index"])
                if source is None:
                    raise ValueError(
                        "Product evidence must name a supplied source index"
                    )
                row["source_sha256"] = source["sha256"]
                for i, mark in enumerate(row["readable_marks"]):
                    mark["bbox"] = rectangle_edges(
                        mark.pop("rect"),
                        source["width"],
                        source["height"],
                        f"Product {row['product_index']} mark {i}",
                    )
            validate_product_evidence(value, data["products"])
        elif phase == "layout":
            hashes = {u["id"]: u["crop_sha256"] for u in data["physical_units"]}
            for row in value["units"]:
                row["reference_sha256"] = hashes.get(row["reference_unit_id"])
                for key in ("layout_anchors", "text_regions"):
                    for i, region in enumerate(row[key]):
                        region["bbox"] = rectangle_edges(
                            region.pop("rect"),
                            1000,
                            1000,
                            f"Reference {row['reference_unit_id']} {key}[{i}]",
                        )
            validate_reference_layouts(value, hashes)
        elif phase == "plan":
            value = materialize_plan(value, data)
        elif phase == "review_plan":
            if [r["unit_id"] for r in value["unit_audits"]] != [
                u["id"] for u in data["reference_units"]
            ]:
                raise ValueError(
                    "Review every reference unit once in its original order"
                )
            for row in value["unit_audits"]:
                validate_review(row, PLAN_DIMENSIONS, "material_mismatches")
                unit = next(
                    u for u in data["reference_units"] if u["id"] == row["unit_id"]
                )
                if row["fresh_pixel_observation"] == unit["ai_reference_observation"]:
                    raise ValueError(
                        "Fresh reference observation must not copy "
                        "the earlier planning observation"
                    )
            value["method"] = "AI_FRESH_REFERENCE_AUDIT"
        elif phase == "review_artwork":
            validate_review(value, ARTWORK_DIMENSIONS, "major_mismatches")
            if not value["product_geometry_match"] and not value["major_mismatches"]:
                raise ValueError(
                    "A geometry mismatch requires an explicit material failure"
                )
            planned = next(
                m["visual_contract"]
                for m in self.state()["plan"]["modules"]
                if m["id"] == data["module"]["id"]
            )
            if value["fresh_reference_observation"] in (
                planned,
                value["fresh_artwork_observation"],
            ):
                raise ValueError(
                    "Fresh observations must describe each actual image independently"
                )
            value["method"] = "AI_FRESH_SIDE_BY_SIDE_PIXEL_AUDIT"
        elif phase == "repair":
            value["material_failures"] = deepcopy(
                data["failed_judgment"]["major_mismatches"]
            )
        return value

    async def ask(
        self,
        stage: str,
        instruction: str,
        context: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if (
            not stage.startswith("exact-module-constraints")
            and phase_for(stage) == "review_plan"
        ):
            data = context.get("data", context)
            audits = []
            # Keep the same native full-set gate, but inspect one physical pair
            # at a time instead of mixing every screen in one visual request.
            for unit in data["reference_units"]:
                module = next(
                    m for m in data["modules"] if m["reference_unit_id"] == unit["id"]
                )
                scoped = {
                    **data,
                    "reference_units": [unit],
                    "modules": [module],
                    "_compact_scopes": {
                        "modules": [
                            s
                            for s in data["_compact_scopes"]["modules"]
                            if s["module_id"] == module["id"]
                        ]
                    },
                }
                options = {k: v for k, v in kwargs.items() if k != "paths"}
                result = await self._ask_stage(
                    f"{stage}-{unit['id']}",
                    instruction,
                    {
                        "data": scoped,
                        "validation_error": context.get("validation_error", ""),
                    },
                    **options,
                )
                audits.extend(result["unit_audits"])
            return {"method": "AI_FRESH_REFERENCE_AUDIT", "unit_audits": audits}
        if stage.startswith("exact-module-constraints") or phase_for(stage) != "intake":
            return await self._ask_stage(stage, instruction, context, **kwargs)
        columns_observation = await self._ask_stage(
            stage, instruction, context, **kwargs
        )
        data = context.get("data", context)
        columns = []
        for index, column in enumerate(columns_observation["columns"]):
            path = self.root / "crops" / f"reference-column-{index:03}.png"
            source = Path(self.context["reference_paths"][column["reference_index"]])
            self.crop(source, column["bbox"], path)
            left, top, right, bottom = column["bbox"]
            columns.append(
                {
                    **column,
                    "column_id": f"C{index + 1:03}",
                    "width": right - left,
                    "height": bottom - top,
                    "crop_path": str(path),
                }
            )
        self.context["reference_columns"] = columns
        write_json(self.root / "context.json", self.context)
        inputs = {
            "columns": columns,
            "reference_dimensions": data["reference_dimensions"],
        }
        column_paths = [Path(c["crop_path"]) for c in columns]
        split = await self._ask_stage(
            "identity-screen-split-0", "", inputs, paths=column_paths
        )
        # The counter sees actual pixels, never the proposed cuts, titles or counts.
        inventory_paths = [
            *map(Path, self.context["reference_paths"]),
            *column_paths,
        ]
        inventory = await self._ask_stage(
            "identity-screen-inventory-0", "", inputs, paths=inventory_paths
        )

        def mismatches() -> list[str]:
            return [
                a["column_id"]
                for a, b in zip(split["columns"], inventory["columns"], strict=True)
                if len(a["screens"]) != len(b["screens"])
            ]

        if not inventory["all_columns_covered"]:
            raise CompactScreenBoundaryMismatch(
                "Reference columns omit or overlap visible content"
            )
        failed = mismatches()
        if failed:
            targeted = {
                **inputs,
                "columns": [c for c in columns if c["column_id"] in failed],
                "boundary_mismatch": {
                    "previous_split": [
                        c for c in split["columns"] if c["column_id"] in failed
                    ],
                    "independent_inventory": [
                        c for c in inventory["columns"] if c["column_id"] in failed
                    ],
                },
            }
            repaired = await self._ask_stage(
                "identity-screen-split-repair",
                "",
                targeted,
                paths=[Path(c["crop_path"]) for c in targeted["columns"]],
            )
            replacements = {c["column_id"]: c for c in repaired["columns"]}
            split = {
                "columns": [
                    replacements.get(c["column_id"], c) for c in split["columns"]
                ]
            }
            inventory = await self._ask_stage(
                "identity-screen-inventory-1", "", inputs, paths=inventory_paths
            )
        write_json(
            self.root / "screen-inventory.json",
            {
                "split": split,
                "independent_inventory": inventory,
                "directed_repair_columns": failed,
                "mismatched_columns": mismatches(),
            },
        )
        if not inventory["all_columns_covered"] or mismatches():
            raise CompactScreenBoundaryMismatch(
                "Physical screen count remains inconsistent after one correction"
            )
        units = []
        for column, observed in zip(columns, split["columns"], strict=True):
            left, top, right, _ = column["bbox"]
            edges = [0, *observed["boundary_ys"], column["height"]]
            for index, screen in enumerate(observed["screens"]):
                units.append(
                    {
                        "reference_index": column["reference_index"],
                        "column_index": column["column_index"],
                        "screen_index": index,
                        "bbox": [
                            left,
                            top + edges[index],
                            right,
                            top + edges[index + 1],
                        ],
                        "screen_boundary_reason": (
                            f"Distinct screen: {screen['headline']}; "
                            f"buyer question: {screen['buyer_question']}"
                        ),
                    }
                )
        result = {
            k: columns_observation[k]
            for k in ("product_geometry", "product_crops", "route")
        }
        result["units"] = units
        errors = segmentation_errors(
            result,
            [(p["width"], p["height"]) for p in data["product_dimensions"]],
            [(r["width"], r["height"]) for r in data["reference_dimensions"]],
        )
        if errors:
            raise CompactScreenBoundaryMismatch("; ".join(errors))
        return result

    async def _ask_stage(
        self,
        stage: str,
        instruction: str,
        context: Any,
        *,
        schemas: tuple[str, ...] = (),
        paths: list[Path] | None = None,
        bind_evidence: Any = None,
    ) -> dict[str, Any]:
        from .xinghe_agent import PlannerRequest

        self.report_stage_progress(stage)
        if stage.startswith("exact-module-constraints"):
            return self.prepared_module_constraints(context["plan"])
        phase = phase_for(stage)
        data = context.get("data", context)
        selected = self._inputs(phase, data, paths)
        sources = self.sources(selected)
        schema = response_schema(phase)
        if phase == "review_plan":
            audits = schema["properties"]["unit_audits"]
            expected_ids = [u["id"] for u in data["reference_units"]]
            audits["items"]["properties"]["unit_id"]["enum"] = expected_ids
            audits.update(minItems=len(expected_ids), maxItems=len(expected_ids))
        if self.context.get("model_protocol", self.binding) != self.binding:
            raise CompactResponseInvalid("Frozen visual protocol context changed")
        self.context["model_protocol"] = self.binding
        write_json(self.root / "context.json", self.context)
        system = (
            (PROMPTS / "common.md").read_text(encoding="utf-8")
            + "\n"
            + (PROMPTS / (phase + ".md")).read_text(encoding="utf-8")
        )
        error = context.get("validation_error", "")
        for attempt in range(2):
            started = time.monotonic()
            contract = {
                "stage": phase,
                "context": self._data(phase, data),
                "response_schema": schema,
                "image_order": self._image_order(selected, data),
                "validation_error": error,
            }
            serialized = json.dumps(contract, ensure_ascii=False)
            before = getattr(self.gateway, "external_network_request_count", None)
            result = await self.gateway.plan(
                PlannerRequest(
                    workspace_id=self.workspace_id,
                    job_id=self.document_id,
                    group_key=stage,
                    sources=sources,
                    page_count=1,
                    language="zh-CN",
                    product_name="用户商品",
                    product_information=(serialized,),
                    platform_code="tmall",
                    size="1024x1024",
                    idempotency_key=f"{self.document_id}:{stage}:{uuid4().hex}",
                    planning_profile="replica_exact_compact",
                    system_prompt=system,
                )
            )
            after = getattr(self.gateway, "external_network_request_count", None)
            requests = (
                max(1, after - before)
                if type(before) is int and type(after) is int
                else 1
            )
            self.record_usage(result, "analysis", requests)
            metrics_path = self.root / "compact-protocol-metrics.json"
            metrics: dict[str, Any] = (
                json.loads(metrics_path.read_text())
                if metrics_path.exists()
                else {"version": VERSION, "calls": []}
            )
            metrics["calls"].append(
                {
                    "stage": stage,
                    "phase": phase,
                    "format_attempt": attempt + 1,
                    "system_characters": len(system),
                    "request_characters": len(serialized),
                    "images": len(sources),
                    "network_requests": requests,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            write_json(metrics_path, metrics)
            if result.error_code or not isinstance(result.payload, dict):
                raise ReplicaAnalysisUnavailable()
            raw = dict(result.payload)
            write_json(
                self.root
                / "contracts"
                / f"model-raw-{stage}-{attempt}-{uuid4().hex[:8]}.json",
                raw,
            )
            try:
                errors = list(Draft202012Validator(schema).iter_errors(raw))
                if errors:
                    first = errors[0]
                    if phase == "review_plan" and list(first.absolute_path)[-1:] == [
                        "unit_id"
                    ]:
                        raise ValueError(
                            "unit_id must use a supplied reference_unit_id, "
                            f"in this order: {expected_ids}; module IDs are not "
                            "reference unit IDs"
                        )
                    raise ValueError(
                        f"Response field {list(first.absolute_path)} "
                        f"violates {first.validator}"
                    )
                value = self._bind(phase, raw, data)
            except ValueError as exc:
                error = str(exc)
                continue
            # Missing server keys and failed file bindings are program errors;
            # another model call cannot repair them.
            if bind_evidence is not None:
                bind_evidence(value)
            self._validate_model_paths(value)
            write_json(
                self.root / "contracts" / f"raw-{stage}-{uuid4().hex[:8]}.json",
                value,
            )
            return value
        raise CompactResponseInvalid(error)


def run_class(method: str, protocol: Any = None) -> type[NativeReplicaRun]:
    if protocol is None:
        return NativeReplicaRun
    version = protocol.get("version") if isinstance(protocol, dict) else protocol
    if method != "exact" or version != VERSION:
        raise CompactResponseInvalid(
            "Unsupported visual protocol; old runs are never silently migrated"
        )
    return CompactExactReplicaRun
