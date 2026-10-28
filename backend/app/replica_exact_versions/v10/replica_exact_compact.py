"""Stage-scoped visual service; native metadata and transitions stay on the server."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from .replica_exact_adaptation import validate_render_adaptation
from .replica_exact_contracts import (
    adaptation_schema,
    canonical_hash,
    fact_resolution_schema,
    resolve_facts,
    user_statements,
    validate_adaptations,
)
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

VERSION = "exact-compact-v10"
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
    "path",
    "sha256",
    "source_hash",
    "references",
    "recommended_canvas",
    "output_canvas",
    "reference_aspect_ratio",
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
    dependencies = [
        Path(__file__),
        Path(__file__).with_name("replica_exact_contracts.py"),
        Path(__file__).with_name("replica_exact_adaptation.py"),
        Path(__file__).with_name("replica_exact_revision.py"),
        Path(__file__).with_name("replica_execution_resume.py"),
        Path(__file__).with_name("replica_direct_delivery.py"),
        Path(__file__).with_name("replica_screen_boundaries.py"),
        Path(__file__).with_name("replica_layout.py"),
        Path(__file__).with_name("replica_native.py"),
        Path(__file__).with_name("replica_native_provider.py"),
        Path(__file__).with_name("replica_canvas.py"),
        Path(__file__).with_name("replica_platform_delivery.py"),
        Path(__file__).with_name("replica_boundary_geometry.py"),
        *sorted(PROMPTS.glob("*.md")),
    ]
    for path in dependencies:
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
        ("exact-fact-resolution", "facts"),
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


def model_geometry(value: dict[str, Any], space: str) -> dict[str, Any]:
    """Describe canonical native edges without exposing an ambiguous model box."""

    def convert(item: Any) -> Any:
        if isinstance(item, list):
            return [convert(row) for row in item]
        if not isinstance(item, dict):
            return item
        result = {k: convert(v) for k, v in item.items() if k != "bbox"}
        if "bbox" in item:
            box = item["bbox"]
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(type(v) is not int for v in box)
                or not (0 <= box[0] < box[2] and 0 <= box[1] < box[3])
                or (space == "normalized_0_1000" and max(box) > 1000)
            ):
                raise CompactResponseInvalid("Server visual geometry has invalid edges")
            left, top, right, bottom = box
            result["rect"] = {
                "x": left,
                "y": top,
                "width": right - left,
                "height": bottom - top,
            }
        return result

    return {**convert(clean(value)), "coordinate_space": space}


def model_unit(unit: dict[str, Any]) -> dict[str, Any]:
    # The overview's absolute crop window is a server binding, not screen layout.
    return clean({k: v for k, v in unit.items() if k != "bbox"})


def model_product_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [model_geometry(row, "original_product_image_pixels") for row in rows]


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
    if phase == "facts":
        return fact_resolution_schema()
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
        if phase in {"screen_split", "screen_inventory"}:
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
                            "fact_adaptations": adaptation_schema(),
                            "render_anchors": arr(
                                obj(
                                    {
                                        "source_anchor_index": index,
                                        "label": text(2, 200),
                                        "rect": rectangle(),
                                        "basis": {
                                            "enum": [
                                                "retain_visual",
                                                "product_identity",
                                                "approved_facts",
                                                "source_pixel_correction",
                                            ]
                                        },
                                        "fact_ids": arr(text(1, 40), maxItems=40),
                                        "reason": text(8, 400),
                                    }
                                ),
                                minItems=1,
                                maxItems=24,
                            ),
                            "commercial_adaptation": obj(
                                {
                                    "mode": {
                                        "enum": ["preserve", "truthful_substitution"]
                                    },
                                    "source_role": text(8, 500),
                                    "supported_role": text(8, 500),
                                    "fact_ids": arr(text(1, 40), maxItems=40),
                                    "reason": text(8, 500),
                                    "preserved_mechanism": text(8, 500),
                                }
                            ),
                            "content_groups": arr(
                                obj(
                                    {
                                        "region_ids": arr(
                                            text(1, 40), minItems=1, maxItems=48
                                        ),
                                        "critical": {"type": "boolean"},
                                        "purpose": text(8, 400),
                                    }
                                ),
                                maxItems=48,
                            ),
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
            "fact_adaptations",
            "render_anchors",
            "commercial_adaptation",
            "content_groups",
        }
    }


def model_plan_proposal(plan: dict[str, Any], unit_id: str) -> dict[str, Any]:
    value = model_geometry(plan_proposal(plan, unit_id), "normalized_0_1000")
    value.pop("coordinate_space")
    return value


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
                        "fact_adaptations",
                        "commercial_adaptation",
                        "render_anchors",
                        "content_groups",
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
            "layout_anchors": [
                {"label": a["label"], "bbox": deepcopy(a["bbox"])}
                for a in proposed["render_anchors"]
            ],
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
    requires_independent_pixel_review = False
    render_binding_version = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.method != "exact":
            raise CompactResponseInvalid(
                "Compact protocol only supports exact replication"
            )
        self.binding = protocol_binding()
        self.context: dict[str, Any] = {}
        self.checkpoints: dict[str, Any] = {"results": {}, "planned": [], "reviews": {}}
        self.on_checkpoint: Any = None

    async def prepare(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if self.checkpoints.get("plan_cursor"):
            from .replica_exact_revision import resume_planning

            return await resume_planning(self)
        session = self.root / "session.json"
        if getattr(self, "_restored", False) and session.exists():
            # An explicit analysis retry starts a new native planning session,
            # preserving the previous one and reusing only hash-bound observations.
            # Image execution snapshots are resumed by the provider, never here.
            if self.state()["state"] not in {
                "STARTED",
                "PLAN_REJECTED",
                "PLAN_AUDIT_REQUIRED",
                "READY",
            }:
                raise CompactResponseInvalid(
                    "An image execution cannot restart analysis"
                )
            saved = self.root / "contracts" / f"previous-planning-{uuid4().hex}.json"
            saved.parent.mkdir(parents=True, exist_ok=True)
            session.rename(saved)
        return await super().prepare(*args, **kwargs)

    async def restore_for_revision(self, snapshot: dict[str, Any]) -> None:
        from .replica_exact_revision import import_revision

        await import_revision(self, snapshot)

    async def checkpoint(self) -> None:
        write_json(self.root / "compact-checkpoints.json", self.checkpoints)
        write_json(self.root / "context.json", self.context)
        if self.on_checkpoint is not None:
            await self.on_checkpoint()

    def _progress(self, phase: str, data: dict[str, Any], value: Any = None) -> None:
        total = len(data.get("physical_units", []))
        if total:
            self.checkpoints["total"] = total
        requested = data.get("_proposal_units") or data.get("reference_units", [])
        uid = requested[0]["id"] if len(requested) == 1 else None
        if value is not None and phase == "plan":
            planned = set(self.checkpoints["planned"])
            planned.update(m["reference_unit_id"] for m in value["modules"])
            self.checkpoints["planned"] = sorted(planned)
        if value is not None and phase == "review_plan":
            self.checkpoints["reviews"].update(
                {r["unit_id"]: r["decision"] for r in value["unit_audits"]}
            )
        reviews = self.checkpoints["reviews"]
        self.context["analysis_progress"] = {
            "stage": phase,
            "total": self.checkpoints.get("total", 0),
            "planned": len(self.checkpoints["planned"]),
            "reviewed": len(reviews),
            "accepted": sum(v == "PASS" for v in reviews.values()),
            "pending_corrections": sum(v == "FAIL" for v in reviews.values()),
            "current_unit": uid,
            "reused_reviews": len(self.checkpoints.get("reused_reviews", []))
            + len(self.checkpoints.get("inherited_reviews_used", [])),
            "external_calls": sum(u.get("network_requests", 1) for u in self.usage),
        }

    def _planner_identity(self) -> dict[str, Any]:
        return {
            "provider": self.gateway.provider_name,
            "model": self.gateway.planner_model,
            "pricing_version": getattr(self.gateway, "pricing_version", None),
            "routes": [
                {
                    "name": r.name,
                    "model": r.planner_model,
                    "endpoint": canonical_hash(r.planner_base_url),
                }
                for r in self.gateway._ordered_routes()
            ]
            if hasattr(self.gateway, "_ordered_routes")
            else [],
        }

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
        module = self.state()["plan"]["modules"][self.state()["module_index"]]
        return (
            prompt
            + "\n\n"
            + (PROMPTS / "generation.md").read_text(encoding="utf-8")
            + "\nRESOLVED_FACTS="
            + json.dumps(self.context["resolved_facts"], ensure_ascii=False)
            + "\nFACT_ADAPTATIONS="
            + json.dumps(module["fact_adaptations"], ensure_ascii=False)
            + "\nCOMMERCIAL_ADAPTATION="
            + json.dumps(module.get("commercial_adaptation"), ensure_ascii=False)
        )

    def state(self) -> dict[str, Any]:
        if getattr(self, "direct_execution", False):
            from .replica_native import read_json

            return read_json(self.root / "direct-session.json")
        return super().state()

    def generation_policy(self) -> str:
        return (PROMPTS / "visual_policy.md").read_text(encoding="utf-8")

    def model_module_evidence(self, value: dict[str, Any]) -> dict[str, Any]:
        return model_geometry(
            {k: v for k, v in value.items() if k != "source_layout_anchors"},
            "normalized_0_1000",
        )

    async def snapshot(self) -> dict[str, Any]:
        return {**await super().snapshot(), "model_protocol": self.binding}

    async def restore(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("model_protocol") != self.binding:
            raise CompactResponseInvalid(
                "Visual protocol changed; create a new analysis "
                "instead of reusing this plan"
            )
        await super().restore(snapshot)
        checkpoint_path = self.root / "compact-checkpoints.json"
        if checkpoint_path.exists():
            self.checkpoints = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        self._restored = True
        if self.context.get("model_protocol") != self.binding:
            raise CompactResponseInvalid(
                "Visual protocol context does not match its snapshot"
            )
        self.direct_execution = self.context.get("post_generation_review") == "disabled"
        if self.direct_execution:
            from .replica_direct_delivery import validate_direct_state

            validate_direct_state(self)

    def _inputs(
        self, phase: str, data: dict[str, Any], explicit: list[Path] | None
    ) -> list[Path]:
        if explicit is not None:
            return explicit
        units = (
            data.get("_proposal_units") or targets(data)
            if phase == "plan"
            else data.get("reference_units", [])
        )
        return [
            *[Path(p) for p in self.context["product_paths"]],
            *[Path(u["crop_path"]) for u in units],
        ]

    def _data(self, phase: str, data: dict[str, Any]) -> dict[str, Any]:
        if phase == "intake":
            return {
                **{k: data[k] for k in ("product_dimensions", "reference_dimensions")},
                **(
                    {
                        "column_correction": getattr(
                            self, "segmentation_revision", "initial"
                        )
                    }
                    if getattr(self, "segmentation_coverage_retry", False)
                    else {}
                ),
            }
        if phase in {"screen_split", "screen_inventory"}:
            return clean(
                {
                    "columns": [model_unit(c) for c in data["columns"]],
                    "reference_dimensions": data.get("reference_dimensions", []),
                    **(
                        {"coverage_scope": data["coverage_scope"]}
                        if "coverage_scope" in data
                        else {}
                    ),
                    **(
                        {"boundary_mismatch": data["boundary_mismatch"]}
                        if phase == "screen_split" and "boundary_mismatch" in data
                        else {}
                    ),
                }
            )
        if phase == "product":
            return {"products": clean(data["products"])}
        if phase == "facts":
            return {
                "user_statements": data["user_statements"],
                "product_evidence": model_product_evidence(data["product_evidence"]),
            }
        if phase == "layout":
            return {"physical_units": [model_unit(u) for u in data["physical_units"]]}
        if phase == "plan":
            units = data.get("_proposal_units") or targets(data)
            prior = data.get("previous_plan") or {}
            audit = data.get("prior_visual_audit") or {}
            requested_ids = {u["id"] for u in units}
            return clean(
                {
                    **{
                        k: data.get(k)
                        for k in (
                            "language",
                            "platform",
                        )
                    },
                    "resolved_facts": self.context["resolved_facts"],
                    "product_evidence": model_product_evidence(
                        data["product_evidence"]
                    ),
                    "requested_units": [model_unit(u) for u in units],
                    "reference_layouts": {
                        u["id"]: model_geometry(
                            data["reference_layouts"][u["id"]], "normalized_0_1000"
                        )
                        for u in units
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
                        for m in data.get("_sequence_summary", prior.get("modules", []))
                    ],
                    "sequence_roles": [
                        {
                            "reference_unit_id": u["id"],
                            "role": data["reference_layouts"][u["id"]][
                                "reference_observation"
                            ]["cross_screen_role"],
                        }
                        for u in data["physical_units"]
                    ],
                    "previous_targets": [
                        model_plan_proposal(prior, u["id"]) for u in units
                    ]
                    if prior
                    else [],
                    "prior_audit": {
                        "unit_audits": [
                            row
                            for row in audit.get("unit_audits", [])
                            if row["unit_id"] in requested_ids
                        ]
                    },
                    "other_failed_findings": [
                        {
                            "reference_unit_id": row["unit_id"],
                            "material_mismatches": row["material_mismatches"],
                        }
                        for row in audit.get("unit_audits", [])
                        if row["unit_id"] not in requested_ids
                        and row["decision"] == "FAIL"
                    ],
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
                    "resolved_facts": self.context["resolved_facts"],
                    "product_evidence": model_product_evidence(
                        data.get("product_evidence", [])
                    ),
                    "review_targets": [
                        {
                            "reference_unit_id": u["id"],
                            "reference_screen": model_unit(
                                {k: v for k, v in u.items() if k != "id"}
                            ),
                            "proposed_screen": model_geometry(
                                {
                                    k: v
                                    for k, v in modules[u["id"]].items()
                                    if k not in {"id", "reference_unit_id"}
                                },
                                "normalized_0_1000",
                            ),
                            "identity_scope": model_geometry(
                                {
                                    k: v
                                    for k, v in scopes.get(
                                        modules[u["id"]]["id"], {}
                                    ).items()
                                    if k != "module_id"
                                },
                                "normalized_0_1000",
                            ),
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
        value = clean(data)
        if phase in {"review_artwork", "repair"}:
            value.get("user_context", {}).pop("user_facts", None)
            value["resolved_facts"] = self.context["resolved_facts"]
            module = data.get("module", {})
            if module.get("id"):
                planned = next(
                    m
                    for m in self.state()["plan"]["modules"]
                    if m["id"] == module["id"]
                )
                value["fact_adaptations"] = planned["fact_adaptations"]
                value["commercial_adaptation"] = planned.get("commercial_adaptation")
                value["render_anchors"] = model_geometry(
                    {"anchors": planned.get("render_anchors", [])}, "normalized_0_1000"
                )
        if phase in {"review_artwork", "repair"} and value.get("module_constraints"):
            value["module_constraints"] = self.model_module_evidence(
                value["module_constraints"]
            )
        return value

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
            if phase in {"screen_split", "screen_inventory"}:
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
        elif phase == "facts":
            value = resolve_facts(
                value, data["user_statements"], data["product_evidence"]
            )
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
            for proposed in value["modules"]:
                layout = data["reference_layouts"][proposed["reference_unit_id"]]
                for i, anchor in enumerate(proposed["render_anchors"]):
                    anchor["bbox"] = rectangle_edges(
                        anchor.pop("rect"), 1000, 1000, f"Render anchor {i}"
                    )
                validate_adaptations(
                    proposed,
                    layout,
                    self.context["resolved_facts"],
                )
                validate_render_adaptation(
                    proposed, layout, self.context["resolved_facts"]
                )
            if data.get("_proposal_units"):
                units = data["_proposal_units"]
                if [m["reference_unit_id"] for m in value["modules"]] != [
                    u["id"] for u in units
                ]:
                    raise ValueError("Return only the requested screen proposal")
                for proposed in value["modules"]:
                    prior = data.get("previous_plan")
                    if prior and proposed == plan_proposal(
                        prior, proposed["reference_unit_id"]
                    ):
                        raise ValueError(
                            "A failed plan must actually change before another audit"
                        )
                # Validate the single proposal's native scope and text bindings,
                # without compiling or changing any native session state.
                materialize_plan(
                    value, {**data, "physical_units": units, "previous_plan": None}
                )
            else:
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
        # Bridge native orchestration; the versioned phase catalog owns all
        # visual instructions and schemas, rather than the old inline prompts.
        kwargs.pop("schemas", None)
        if (
            not stage.startswith("exact-module-constraints")
            and phase_for(stage) == "product"
        ):
            observation = await self._visual_stage(stage, context, **kwargs)
            ledger = await self._visual_stage(
                "exact-fact-resolution",
                {
                    "user_statements": user_statements(self.context["user_facts"]),
                    "product_evidence": observation["products"],
                },
                paths=[Path(p) for p in self.context["product_paths"]],
            )
            self.context["resolved_facts"] = ledger
            await self.checkpoint()
            return observation
        if (
            not stage.startswith("exact-module-constraints")
            and phase_for(stage) == "plan"
        ):
            data = context.get("data", context)
            cursor = self.checkpoints.setdefault(
                "plan_cursor",
                {
                    "rounds": 0,
                    "phase": "proposal",
                    "plan": None,
                    "audit": None,
                    "physical_units": deepcopy(data["physical_units"]),
                },
            )
            if cursor["phase"] in {"review", "accepted"}:
                return deepcopy(cursor["plan"])
            if cursor["rounds"] >= self.planning_revisions:
                from .replica_native import ReplicaPlanAuditRejected

                raise ReplicaPlanAuditRejected(cursor["audit"])
            if cursor["plan"]:
                data = {
                    **data,
                    "previous_plan": cursor["plan"],
                    "prior_visual_audit": cursor["audit"],
                }
            proposals = []
            summaries = {
                m["reference_unit_id"]: m
                for m in (data.get("previous_plan") or {}).get("modules", [])
            }
            for unit in targets(data):
                result = await self._visual_stage(
                    f"{stage}-{unit['id']}",
                    {
                        "data": {
                            **data,
                            "_proposal_units": [unit],
                            "_sequence_summary": [
                                summaries[u["id"]]
                                for u in data["physical_units"]
                                if u["id"] in summaries and u["id"] != unit["id"]
                            ],
                        },
                        "validation_error": context.get("validation_error", ""),
                    },
                    **{k: v for k, v in kwargs.items() if k != "paths"},
                )
                proposals.extend(result["modules"])
                summaries[unit["id"]] = result["modules"][0]
            plan = materialize_plan({"modules": proposals}, data)
            cursor.update(plan=deepcopy(plan), phase="review")
            await self.checkpoint()
            return plan
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
                from .replica_exact_revision import inherited_review

                inherited = inherited_review(self, scoped)
                if inherited is not None:
                    result = inherited
                    if options.get("bind_evidence"):
                        options["bind_evidence"](result)
                    self._progress("review_plan", scoped, result)
                    await self.checkpoint()
                else:
                    result = await self._visual_stage(
                        f"{stage}-{unit['id']}",
                        {
                            "data": scoped,
                            "validation_error": context.get("validation_error", ""),
                        },
                        **options,
                    )
                audits.extend(result["unit_audits"])
            value = {"method": "AI_FRESH_REFERENCE_AUDIT", "unit_audits": audits}
            cursor = self.checkpoints.get("plan_cursor")
            if (
                cursor
                and cursor.get("plan")
                and all(data.get(k) == v for k, v in cursor["plan"].items())
            ):
                if cursor["phase"] == "review":
                    cursor["rounds"] += 1
                cursor.update(
                    audit=deepcopy(value),
                    phase="accepted"
                    if all(r["decision"] == "PASS" for r in audits)
                    else "revise",
                )
                await self.checkpoint()
            return value
        if stage.startswith("exact-module-constraints") or phase_for(stage) != "intake":
            return await self._visual_stage(stage, context, **kwargs)
        from .replica_screen_boundaries import segment_references

        return await segment_references(self, stage, context, **kwargs)

    async def _visual_stage(
        self,
        stage: str,
        context: Any,
        *,
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
        if phase == "plan" and data.get("_proposal_units"):
            modules = schema["properties"]["modules"]
            ids = [u["id"] for u in data["_proposal_units"]]
            modules.update(minItems=len(ids), maxItems=len(ids))
            modules["items"]["properties"]["reference_unit_id"]["enum"] = ids
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
            + (PROMPTS / "visual_policy.md").read_text(encoding="utf-8")
            + "\n"
            + (PROMPTS / (phase + ".md")).read_text(encoding="utf-8")
        )
        model_data = self._data(phase, data)
        image_order = self._image_order(selected, data)
        identity = canonical_hash(
            {
                "protocol": self.binding,
                "phase": phase,
                "context": model_data,
                "observation_pass": stage if phase == "screen_inventory" else None,
                "schema": schema,
                "system": system,
                "planner": self._planner_identity(),
                "images": [s.sha256 for s in sources],
                "image_order": image_order,
            }
        )
        cached = self.checkpoints["results"].get(identity)
        if cached is None and phase == "intake":
            from .replica_screen_boundaries import inherited_intake

            cached = inherited_intake(self, data, sources, schema, system, image_order)
        if cached is not None:
            raw = deepcopy(cached["raw"])
            if canonical_hash(raw) != cached["response_sha256"]:
                raise CompactResponseInvalid("Cached visual evidence changed")
            if list(Draft202012Validator(schema).iter_errors(raw)):
                raise CompactResponseInvalid(
                    "Cached visual evidence violates its schema"
                )
            try:
                value = self._bind(phase, raw, data)
            except ValueError as exc:
                raise CompactResponseInvalid(str(exc)) from exc
            if bind_evidence is not None:
                bind_evidence(value)
            self._validate_model_paths(value)
            if phase == "review_plan":
                self.checkpoints["reused_reviews"] = sorted(
                    {
                        *self.checkpoints.get("reused_reviews", []),
                        identity,
                    }
                )
            self._progress(phase, data, value)
            await self.checkpoint()
            return value
        error = context.get("validation_error", "")
        for attempt in range(2):
            self._progress(phase, data)
            await self.checkpoint()
            started = time.monotonic()
            contract = {
                "stage": phase,
                "context": model_data,
                "response_schema": schema,
                "image_order": image_order,
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
                    idempotency_key=f"{self.document_id}:{identity}:format-{attempt}",
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
                    "protocol": self.binding["version"],
                    "request_sha256": identity,
                    "format_attempt": attempt + 1,
                    "system_characters": len(system),
                    "request_characters": len(serialized),
                    "images": len(sources),
                    "network_requests": requests,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "http_status": getattr(result, "http_status", None),
                    "error_category": (
                        "upstream_http_failure"
                        if getattr(result, "http_status", 0) in range(400, 600)
                        else "transport_or_response_failure"
                        if result.error_code
                        else None
                    ),
                }
            )
            write_json(metrics_path, metrics)
            await self.checkpoint()
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
            self.checkpoints["results"][identity] = {
                "raw": deepcopy(raw),
                "response_sha256": canonical_hash(raw),
                "phase": phase,
                "planner": self._planner_identity(),
            }
            self._progress(phase, data, value)
            write_json(
                self.root / "contracts" / f"raw-{stage}-{uuid4().hex[:8]}.json",
                value,
            )
            await self.checkpoint()
            return value
        raise CompactResponseInvalid(error)


def run_class(method: str, protocol: Any = None) -> type[NativeReplicaRun]:
    if protocol is None:
        return NativeReplicaRun
    version = protocol.get("version") if isinstance(protocol, dict) else protocol
    if method == "exact" and version in {
        "exact-compact-v1",
        "exact-compact-v2",
        "exact-compact-v3",
        "exact-compact-v4",
        "exact-compact-v5",
        "exact-compact-v6",
        "exact-compact-v7",
        "exact-compact-v8",
        "exact-compact-v9",
    }:
        archive = version.rsplit("-", 1)[-1]
        name = f"app._replica_exact_compact_{archive}"
        if name not in sys.modules:
            path = (
                Path(__file__).parent
                / f"replica_exact_versions/{archive}/replica_exact_compact.py"
            )
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise CompactResponseInvalid("Original compact protocol is unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        legacy_class: type[NativeReplicaRun] = sys.modules[name].CompactExactReplicaRun
        return legacy_class
    if method != "exact" or version != VERSION:
        raise CompactResponseInvalid(
            "Unsupported visual protocol; old runs are never silently migrated"
        )
    return CompactExactReplicaRun
