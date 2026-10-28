"""Bounded creative authoring; the native Harness remains the final authority."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from . import website_contract as website

STORY_FIELDS = (
    "id",
    "duty",
    "buyer_question",
    "lead_conclusion",
    "lead_claim_id",
    "selling_point_cluster_ids",
    "story_function",
    "pain_ids",
    "value_bridge_ids",
    "visual_proof_direction",
    "visual_archetype",
    "composition_family",
    "camera_scale",
    "primary_subject_class",
    "presentation_form",
    "complete_product_role",
)
# One campaign is serial story + pairs + at most one correction.
# A real supplier call completed in 203s, beyond the former 180s cap.
# Individual calls use the shared gateway timeout; the campaign stays bounded.
PLANNING_SECONDS = 1800.0
REFERENCES = (
    "detail-story-grammar.md",
    "visual-proof.md",
    "storyboard-matrix.md",
    "continuity-and-rhythm.md",
    "compliance-and-platform.md",
    "reference-intelligence.md",
)


STAGE_REFERENCES = {
    "story": (
        "detail-story-grammar.md",
        "storyboard-matrix.md",
        "compliance-and-platform.md",
        "reference-intelligence.md",
    ),
    "pair": ("visual-proof.md", "storyboard-matrix.md", "continuity-and-rhythm.md"),
}
PRESENCE_BY_ROLE = {
    "DOMINANT": "REQUIRED_DOMINANT",
    "SUPPORTING": "REQUIRED_SUPPORTING",
    "IDENTITY_TOKEN_ONLY": "IDENTITY_TOKEN_ONLY",
    "ABSENT": "ABSENT_PREFERRED",
}
SIGNATURE_FIELDS = (
    "primary_subject_class",
    "presentation_form",
    "camera_scale",
    "complete_product_role",
)


def frozen_rendering_fields(module: Mapping[str, Any]) -> tuple[str, str]:
    """Project only exact decisions already made in the campaign storyboard."""
    return (
        PRESENCE_BY_ROLE[module["complete_product_role"]],
        "+".join(module[key] for key in SIGNATURE_FIELDS),
    )


def system_prompt(root: Path, *, stage: str = "") -> str:
    policy = """Author a fact-grounded Aurora v4.3.1 campaign in bounded stages.
The local native Harness owns compilation, approval, generation and pixel audit.
This call only authors the requested planning stage. Do not reproduce execution
instructions or audit reports. Never invent product facts, branding, specifications,
materials, performance or evidence. Source photo watermarks/backgrounds are not
product identity. Keep hypotheses separate from supplied facts. Use original fact IDs.
Every PRIMARY claim needs DEEP coverage: overview, dedicated proof, use/result and
closing recall across at least three screens. claim_depth contains exactly the
primary_claim_ids, with no supporting-only entries. Preserve one story with
distinct jobs.
8/10/12 screens require at least 4/5/6 subject classes and 5/6/7 presentation forms,
with at most 3/3/4 dominant complete-product screens. Middle proof screens may omit
the full product. Every normal screen needs useful visible content units bound to
fact IDs, copy and visual anchors; no slogan-only posters. Plan actual unequal zones,
not extra zones for labels. Never use two consecutive full-bleed single scenes.
BRANDLESS means zero independent logos. BRANDED means one authorized independent
logo on M01 only. User facts and observed facts retain separate provenance.
Write concise Chinese copy and observable visual relations. Keep internal evidence,
risk and approval workflow out of sales copy. Only schema JSON is returned.
"""
    return policy + "\n".join(
        f"\n--- {name} ---\n" + (root / "references" / name).read_text(encoding="utf-8")
        for name in STAGE_REFERENCES.get(stage, REFERENCES)
    )


def phase_schema(
    full: Mapping[str, Any], request: Any, *, wire: bool = True
) -> dict[str, Any]:
    schema = deepcopy(full["json_schema"]["schema"])
    native = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "skill_sources"
            / "aurora-domestic-ecommerce-detail-page/schemas/detail-plan.schema.json"
        ).read_text(encoding="utf-8")
    )
    for key, prop in list(schema["properties"].items()):
        expanded = native["properties"].get(key, {})
        if (
            prop.get("type") == "object"
            and not prop.get("properties")
            and expanded.get("properties")
        ):
            schema["properties"][key] = deepcopy(expanded)
    depth = schema["properties"]["selling_point_depth_contract"]["properties"][
        "claim_depth"
    ]["items"]
    depth["required"].extend(["priority", "module_jobs"])
    depth["properties"].update(
        {
            "priority": {"const": "PRIMARY"},
            "depth_tier": {"const": "DEEP"},
            "module_jobs": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["module_id", "job"],
                    "properties": {
                        "module_id": {"type": "string", "pattern": "^M[0-9]{2,}$"},
                        "job": deepcopy(
                            depth["properties"]["required_decision_jobs"]["items"]
                        ),
                    },
                },
            },
        }
    )
    from .intake import active_fact_ids

    ids = active_fact_ids(request.detail_intake or {})
    fact_arrays = {
        "fact_ids",
        "primary_claim_ids",
        "priority_claim_ids",
        "must_remember_claim_ids",
        "supporting_claim_ids",
        "first_screen_value_claim_ids",
        "closure_reinforcement_claim_ids",
        "atomic_claim_ids",
        "fact_dependencies",
    }

    def bind_fact_ids(node: Any) -> None:
        if isinstance(node, dict):
            for name, prop in node.get("properties", {}).items():
                if (
                    ids
                    and name in {"claim_id", "lead_claim_id"}
                    and isinstance(prop, dict)
                ):
                    prop["enum"] = ids
                if ids and name in fact_arrays and isinstance(prop, dict):
                    prop["items"] = {"type": "string", "enum": ids}
                if (
                    ids
                    and name == "proof_module_ids_by_claim"
                    and isinstance(prop, dict)
                ):
                    prop["propertyNames"] = {"enum": ids}
            for value in node.values():
                bind_fact_ids(value)
        elif isinstance(node, list):
            for value in node:
                bind_fact_ids(value)

    bind_fact_ids(schema)
    module = schema["properties"]["modules"]["items"]
    if request.authoring_stage == "story":
        module = {
            "type": "object",
            "additionalProperties": False,
            "required": list(STORY_FIELDS),
            "properties": {
                key: deepcopy(module["properties"][key]) for key in STORY_FIELDS
            },
        }
        ids = [f"M{i:02d}" for i in range(1, request.page_count + 1)]
        # A storyboard contains creative duties, not every nested rendering contract.
    else:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["modules"],
            "properties": {},
        }
        ids = list(request.authoring_target_ids)
        if len(ids) != 2:
            raise ValueError(
                "Atomic detail completion requires exactly two target screens"
            )
    slots = []
    context = request.authoring_context or {}
    locked = {m["id"]: m for m in context.get("modules", [])}
    for module_id in ids:
        item = deepcopy(module)
        item["properties"]["id"] = {"const": module_id}
        if request.authoring_stage == "pair":
            item["required"] = [
                key
                for key in item["required"]
                if key not in STORY_FIELDS or key == "id"
            ]
            for key in STORY_FIELDS:
                item["properties"][key] = {"const": locked[module_id][key]}
            policy, signature = frozen_rendering_fields(locked[module_id])
            item["required"] = [
                key for key in item["required"] if key != "expression_signature"
            ]
            item["properties"]["expression_signature"] = {"const": signature}
            item["required"].append("product_presence_decision")
            decision = item["properties"]["product_presence_decision"]
            decision["required"] = [
                key for key in decision["required"] if key != "policy"
            ]
            decision["properties"]["policy"] = {"const": policy}
            expression = item["properties"]["expression"]
            expression["required"] = [
                key
                for key in expression["required"]
                if key != "product_presence_policy"
            ]
            expression["properties"]["product_presence_policy"] = {"const": policy}
        slots.append(item)
    schema["properties"]["modules"] = {
        "type": "array",
        "minItems": len(ids),
        "maxItems": len(ids),
        "prefixItems": slots,
        "items": False,
    }
    if request.authoring_stage == "story" and website.enabled(request):
        schema = website.schema_view(schema, wire=wire)
    errors = (request.authoring_context or {}).get("validation_errors")
    if errors:
        schema = correction_contract(schema, errors)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "aurora_bounded_" + request.authoring_stage,
            "strict": False,
            "schema": schema,
        },
    }


def correction_contract(
    schema: Mapping[str, Any], errors: list[dict[str, Any]]
) -> dict[str, Any]:
    """Request replacements only at invalid paths; full merged validation follows."""
    paths = [
        tuple(e["path"].strip("/").split("/")) if e["path"] != "/" else ()
        for e in errors
    ]

    def scope(
        node: Mapping[str, Any], selected: list[tuple[str, ...]]
    ) -> dict[str, Any]:
        if () in selected:
            return deepcopy(dict(node))
        children = {
            path[0]: [p[1:] for p in selected if p[0] == path[0]] for path in selected
        }
        if node.get("type") == "array":
            slots = []
            for index in range(max(map(int, children)) + 1):
                if str(index) not in children:
                    slots.append({"const": None})
                    continue
                prefix = node.get("prefixItems", [])
                child = prefix[index] if index < len(prefix) else node.get("items", {})
                slots.append(
                    scope(
                        child if isinstance(child, Mapping) else {},
                        children[str(index)],
                    )
                )
            return {
                "type": "array",
                "minItems": len(slots),
                "maxItems": len(slots),
                "prefixItems": slots,
                "items": False,
            }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(children),
            "properties": {
                key: scope(node.get("properties", {}).get(key, {}), tails)
                for key, tails in children.items()
            },
        }

    return scope(schema, paths)


def correction_fixture(payload: Any, errors: list[dict[str, Any]]) -> Any:
    """Synthetic supplier output in the same sparse format as the wire contract."""
    paths = [
        tuple(e["path"].strip("/").split("/")) if e["path"] != "/" else ()
        for e in errors
    ]

    def scope(value: Any, selected: list[tuple[str, ...]]) -> Any:
        if () in selected:
            return deepcopy(value)
        children = {
            path[0]: [p[1:] for p in selected if p[0] == path[0]] for path in selected
        }
        if isinstance(value, list):
            return [
                scope(value[i], children[str(i)]) if str(i) in children else None
                for i in range(max(map(int, children)) + 1)
            ]
        return {key: scope(value[key], tails) for key, tails in children.items()}

    return scope(payload, paths)


def prompt_contract(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Share repeated module property schemas without changing their constraints."""
    value = deepcopy(dict(schema))
    slots = value.get("properties", {}).get("modules", {}).get("prefixItems", [])
    if len(slots) < 2:
        return value
    definitions = value.setdefault("$defs", {})
    for key, prop in slots[0].get("properties", {}).items():
        if len(json.dumps(prop)) < 150 or not all(
            slot.get("properties", {}).get(key) == prop for slot in slots[1:]
        ):
            continue
        name = "module_" + key
        definitions[name] = deepcopy(prop)
        for slot in slots:
            slot["properties"][key] = {"$ref": "#/$defs/" + name}
    if not definitions:
        value.pop("$defs")
    return value


def prompt(request: Any) -> str:
    if request.authoring_stage == "direct":
        from .direct import prompt as direct_prompt

        return direct_prompt(request)
    context = deepcopy(request.authoring_context or {})
    errors = context.pop("validation_errors", None)
    if request.authoring_stage == "story":
        instruction = (
            "Create ONE complete campaign-level plan and a concise ordered atomic "
            "storyboard for exactly " + str(request.page_count) + " screens. "
            "The modules contain ONLY the requested spine fields; detailed proof, "
            "copy, zones and rendering contracts are authored in later calls. "
            "Use plan_format COMPACT_V1_3, information_contract_version 4.1 and "
            "expression_contract_version 4.3. Campaign contracts must be complete, "
            "including claim depth, coverage, pain/value bridges, visual DNA, hero "
            "and information progression. hero_screen_contract requires "
            "hero_module_id, "
            "hero_entity_id, hero_job, hero_uniqueness, supporting_entities_allowed, "
            "co_equal_hero_forbidden and hero_identity_evidence. "
            "detail_information_contract "
            "requires buyer_question_sequence, non_repeating_overview_limit, "
            "required_progression_jobs and every_pair_requires_information_delta. "
            "output_strategy.module_count_candidates contains exactly ONE object: "
            "candidate_mode=DIRECT_DERIVATION, decision=SELECTED, count equal to "
            "screen_count. Never emit rejected alternatives. All claim references "
            "must use active Fxx/Oxx fact IDs from locked_intake, never invented Cxx "
            "aliases. primary_claim_ids and must_remember_claim_ids must be identical; "
            "claim_depth accounts for that exact set, all DEEP. For every claim_depth "
            "entry, set(module_jobs[].module_id) must equal set(scheduled_module_ids), "
            "and set(module_jobs[].job) must equal set(required_decision_jobs). Map "
            "every listed decision job explicitly; one module may have multiple "
            "job rows. Do not list unassigned decision jobs. Every dedicated module "
            "must lead with that same fact ID. Recall proof-map keys must use those "
            "same verified IDs. formula_split_contract.evaluated_claims must account "
            "exactly once for each INGREDIENT_FORMULA or STRUCTURE_COMPONENT fact "
            "in locked_intake, excluding other claim kinds. Preserve each fact's "
            "display_intent and user_prominence; named_ingredient equals whether "
            "named_elements is nonempty, and formula_or_element must be one of those "
            "verified names. A named core MUST_SHOW fact requires dedicated or "
            "controlled merged expression; enabled and entries must reflect those "
            "decisions and their actual module duties. Keep all required "
            "fit explanations and the six module_count_formula fields. "
            "Keep output_strategy.module_count exact. Pairs are adjacent M01/M02, "
            "M03/M04 etc with 3:8 canvases, two fixed 3:4 windows. Author their "
            "seam intent consistently with the atomic duties. Only the lower screen's "
            "incoming transition defines its pair's internal seam. Do not invent "
            "independent trust/ingredient/performance claims just to fill the count."
        )
        body = {
            "product_name": request.product_name,
            "user_information": request.product_information,
            "locked_intake": request.detail_intake,
            "platform": request.platform_code,
            "screen_size": request.size,
            "screen_count": request.page_count,
        }
        if context:
            body["prior_invalid_stage"] = context
    else:
        instruction = (
            "Complete ONLY the two specified atomic screens of this already authored "
            "story. Return {modules:[upper,lower]} with all compact fields. Every "
            "spine field constrained by const is frozen. Keep each screen's own "
            "duty, claims, copy, evidence and product presence. Omit frozen spine "
            "fields except id; the server copies those exact fields from the story. "
            "Every visible_copy item requires text, role and anchor; fact_ids is not "
            "a substitute. A pair is a raster "
            "container. Use the campaign's pair seam intent for the lower incoming "
            "transition. Also omit expression_signature, "
            "expression.product_presence_policy and product_presence_decision.policy: "
            "the server derives these exact aliases from the frozen storyboard. "
            "Still author both expression and product_presence_decision with their "
            "creative explanations. Explicit contradictions or null values are "
            "invalid, never replaced. "
            "content units must be distinct and bind actual fact IDs and visible copy. "
            "Do not repeat campaign objects. Do not guess facts from missing images: "
            "locked intake contains the original source observations and source IDs. "
            "STRUCTURE_ZONE_COUNTS: FULL_BLEED_HERO=1; SPLIT_PANEL,COMPARISON_SPLIT,"
            "MACRO_DIAGRAM,ASYMMETRIC_EDITORIAL,SPECIFICATION_BOARD,STACKED_BANDS=2; "
            "CENTRAL_OBJECT_ORBIT,FRAMED_LIFESTYLE_PLUS_CARDS,NESTED_INFO_BOARD,"
            "PORTRAIT_STRIP_PLUS_PAIN_ARRAY,PROCESS_STRIP_PLUS_RESULT,PRODUCT_PAIR_PLUS_TABLE,"
            "SELECTION_MATRIX=3. Labels occupy an existing spatial zone. "
            "No watermark, invented logo, internal risk or evidence workflow copy."
        )
        # complete() adds this exact copy for native compilation. It is not a
        # second factual authority and need not be sent alongside locked_intake.
        if "product_intake" in context:
            if context["product_intake"] != request.detail_intake:
                raise ValueError("Campaign and locked product intake disagree")
            context.pop("product_intake")
        body = {
            "target_module_ids": request.authoring_target_ids,
            "campaign_and_story": context,
            "locked_intake": request.detail_intake,
        }
    if website.enabled(request):
        if request.authoring_stage == "story":
            instruction = website.STORY_INSTRUCTION
        else:
            body["campaign_and_story"] = website.prompt_context(
                context, request.authoring_target_ids
            )
    if errors:
        stage_instruction = instruction
        paths = {error["path"] for error in errors}
        root_replacement = "/" in paths
        whole_pair_replacement = request.authoring_stage == "pair" and (
            "/modules" in paths
            or (
                len(request.authoring_target_ids) == 2
                and all(f"/modules/{i}" in paths for i in range(2))
            )
        )
        full_stage_replacement = root_replacement or whole_pair_replacement
        if root_replacement:
            scope_instruction = (
                "For this root-level relational error, return the COMPLETE stage JSON "
                "required by the contract so dependent decisions can change together. "
            )
        elif whole_pair_replacement:
            scope_instruction = (
                "Return the COMPLETE two-screen pair JSON because the selected paths "
                "replace the modules array or both complete module objects. Return "
                "exactly the two target modules with all required rendering fields, "
                "including already-valid proof, copy, typography and expression. "
            )
        else:
            scope_instruction = (
                "Return ONLY the sparse replacement JSON required by the contract, "
                "not the whole campaign or whole pair. Omit unaffected properties. "
            )
        instruction = (
            "Repair the supplied prior_response using the listed validation errors. "
            + scope_instruction
            + "Every selected object or array is a COMPLETE replacement subtree, "
            "not a missing-field patch. Include all its required members and copy "
            "unchanged authorized values from prior_response. "
            "A null array placeholder means leave that original element unchanged; "
            "it is not a replacement or deletion. The server merges only the listed "
            "invalid paths and then validates the complete result. Use active Fxx/Oxx "
            "fact IDs from locked_intake; preserve all valid creative decisions. "
            "The campaign must-remember facts must equal its primary facts after "
            "correcting references. Never invent source-fact mappings from ID suffixes."
        )
        if full_stage_replacement:
            instruction += "\nComplete-stage authoring rules: " + stage_instruction
    from ..xinghe_agent import _planner_response_format

    contract = prompt_contract(
        _planner_response_format(request)["json_schema"]["schema"]
    )
    return (
        instruction
        + "\nThe following JSON Schema is the complete output contract, including "
        "required nested fields and allowed enum values. Resolve $ref against $defs. "
        "The reference prose explains design intent; this contract defines field names."
        + "\nRESPONSE_CONTRACT_JSON="
        + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
        + "\nINPUT_JSON="
        + json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        + (
            "\nCorrect every listed error without changing frozen duties: "
            + json.dumps(errors, ensure_ascii=False)
            if errors
            else ""
        )
    )


def errors_for(
    payload: object, response_format: Mapping[str, Any]
) -> list[dict[str, Any]]:
    validator = Draft202012Validator(response_format["json_schema"]["schema"])
    return [
        {
            "path": "/" + "/".join(map(str, e.absolute_path)),
            "message": e.message,
            "constraint": e.validator,
            "expected": e.validator_value,
        }
        for e in sorted(
            validator.iter_errors(payload), key=lambda e: str(list(e.absolute_path))
        )
    ]


def selected_story(payload: object, page_count: int) -> object:
    """Discard only explicitly rejected alternatives, never author a decision."""
    value = deepcopy(payload)
    if not isinstance(value, dict):
        return value
    strategy = value.get("output_strategy")
    if not isinstance(strategy, dict):
        return value
    candidates = strategy.get("module_count_candidates")
    if not isinstance(candidates, list) or not candidates:
        return value
    if not all(isinstance(row, dict) for row in candidates):
        return value
    selected = [row for row in candidates if row.get("decision") == "SELECTED"]
    if (
        len(selected) == 1
        and selected[0].get("candidate_mode") == "DIRECT_DERIVATION"
        and selected[0].get("count") == page_count
        and all(row.get("decision") in {"SELECTED", "REJECTED"} for row in candidates)
    ):
        strategy["module_count_candidates"] = selected
    return value


def project_stage(payload: object, request: Any) -> object:
    if request.authoring_stage == "story":
        value = website.project(payload) if website.enabled(request) else payload
        return selected_story(value, request.page_count)
    value = deepcopy(payload)
    locked = {m["id"]: m for m in (request.authoring_context or {}).get("modules", [])}
    if isinstance(value, dict) and isinstance(value.get("modules"), list):
        for module in value["modules"]:
            if isinstance(module, dict) and module.get("id") in locked:
                for key in STORY_FIELDS:
                    module.setdefault(key, deepcopy(locked[module["id"]][key]))
                policy, signature = frozen_rendering_fields(locked[module["id"]])
                module.setdefault("expression_signature", signature)
                # Missing creative containers remain invalid. Only mechanical
                # aliases inherit; explicit wrong/null values remain untouched.
                expression = module.get("expression")
                if isinstance(expression, dict):
                    expression.setdefault("product_presence_policy", policy)
                decision = module.get("product_presence_decision")
                if isinstance(decision, dict):
                    decision.setdefault("policy", policy)
    return value


def stage_errors(payload: object, request: Any) -> list[dict[str, Any]]:
    """Run the same native sales validator before spending on detailed pairs."""
    from ..xinghe_agent import _planner_response_format

    if request.authoring_stage == "direct":
        from .direct import errors

        return [{"path": "/", "message": issue} for issue in errors(payload, request)]
    response_format = _planner_response_format(request)
    if request.authoring_stage == "story" and website.enabled(request):
        full = _planner_response_format(replace(
            request, authoring_stage="", planning_profile="aurora_domestic_detail"
        ))
        response_format = phase_schema(full, request, wire=False)
        if isinstance(payload, Mapping):
            views = website.derived_views(payload)
            if views is not None:
                for key, value in views.items():
                    response_format["json_schema"]["schema"]["properties"][key] = {
                        "const": value
                    }
    issues = errors_for(payload, response_format)
    if request.authoring_stage == "story" and isinstance(payload, Mapping):
        depth = payload.get("selling_point_depth_contract")
        recall = payload.get("campaign_recall_contract")
        if isinstance(depth, Mapping):
            primary = depth.get("primary_claim_ids")
            entries = depth.get("claim_depth")
            # This native set rule is independent of fact-ID validity. Report it
            # with enum errors so the sole correction can replace the whole list,
            # including removing supporting entries instead of only renaming IDs.
            if (
                isinstance(primary, list)
                and all(isinstance(item, str) for item in primary)
                and isinstance(entries, list)
                and all(
                    isinstance(item, Mapping)
                    and isinstance(item.get("claim_id"), str)
                    for item in entries
                )
                and {item["claim_id"] for item in entries} != set(primary)
            ):
                issues.append(
                    {
                        "path": "/selling_point_depth_contract/claim_depth",
                        "constraint": "native_sales_depth",
                        "message": (
                            "Selling-point depth entries must account for every "
                            "primary claim exactly once; exclude supporting-only "
                            "claims and give every primary claim DEEP treatment"
                        ),
                    }
                )
            # These native cross-field equalities remain meaningful when an
            # unrelated schema error exists. Report every affected claim at once
            # and replace its authored entry, never invent module duties locally.
            for index, item in enumerate(entries if isinstance(entries, list) else []):
                if not isinstance(item, Mapping):
                    continue
                mapped = item.get("module_jobs")
                jobs = item.get("required_decision_jobs")
                scheduled = item.get("scheduled_module_ids")
                if not (
                    isinstance(mapped, list)
                    and all(
                        isinstance(row, Mapping)
                        and isinstance(row.get("module_id"), str)
                        and isinstance(row.get("job"), str)
                        for row in mapped
                    )
                    and isinstance(jobs, list)
                    and isinstance(scheduled, list)
                    and all(isinstance(value, str) for value in jobs + scheduled)
                ):
                    continue
                mapped_jobs = {row["job"] for row in mapped}
                mapped_modules = {row["module_id"] for row in mapped}
                if mapped_jobs != set(jobs) or mapped_modules != set(scheduled):
                    issues.append({
                        "path": f"/selling_point_depth_contract/claim_depth/{index}",
                        "constraint": "native_sales_depth",
                        "message": (
                            "module_jobs must exactly explain scheduled modules and "
                            "required decision jobs; map every listed job explicitly. "
                            "One module may have multiple job rows."
                        ),
                        "missing_jobs": sorted(set(jobs) - mapped_jobs),
                        "unexpected_jobs": sorted(mapped_jobs - set(jobs)),
                        "missing_modules": sorted(set(scheduled) - mapped_modules),
                        "unexpected_modules": sorted(mapped_modules - set(scheduled)),
                    })
        if isinstance(depth, Mapping) and isinstance(recall, Mapping):
            primary = depth.get("primary_claim_ids")
            remembered = recall.get("must_remember_claim_ids")
            proof_map = recall.get("proof_module_ids_by_claim")
            if (
                isinstance(remembered, list)
                and all(isinstance(item, str) for item in remembered)
                and isinstance(proof_map, Mapping)
                and set(proof_map) != set(remembered)
            ):
                issues.append(
                    {
                        "path": "/campaign_recall_contract/proof_module_ids_by_claim",
                        "constraint": "native_recall",
                        "message": (
                            "Recall proof map must account for every "
                            "must-remember claim exactly once"
                        ),
                    }
                )
            if (
                isinstance(primary, list)
                and isinstance(remembered, list)
                and all(isinstance(item, str) for item in primary + remembered)
                and set(primary) != set(remembered)
            ):
                issues.append(
                    {
                        "path": "/campaign_recall_contract/must_remember_claim_ids",
                        "constraint": "native_sales_depth",
                        "message": (
                            "Selling-point depth primary claims must equal "
                            "the campaign must-remember claims"
                        ),
                    }
                )
    if not issues and request.authoring_stage == "pair":
        # Match the native pair rule before paying to author any later pair.
        assert isinstance(payload, Mapping)
        lower = payload["modules"][1]["transition_contract"]
        for field, expected in zip(
            ("source_region_previous", "target_region_current"),
            request.authoring_target_ids,
            strict=True,
        ):
            explicit = set(re.findall(
                r"(?<![A-Za-z0-9])M\d{2}(?![A-Za-z0-9])", lower[field]
            ))
            if explicit - {expected}:
                issues.append({
                    "path": "/modules/1/transition_contract/" + field,
                    "constraint": "native_pair_seam",
                    "message": f"Incoming pair {field} must reference only {expected}",
                })
    if request.authoring_stage != "story" or not isinstance(payload, Mapping):
        return issues
    from ..skill_runtime import SkillManifestRegistry
    from .contracts import normalize_native_plan_for_harness
    from .intake import active_fact_ids

    root = (
        Path(__file__).resolve().parents[2]
        / "skill_sources/aurora-domestic-ecommerce-detail-page"
    )
    SkillManifestRegistry.bundled().verify_source(
        "aurora-domestic-ecommerce-detail-page", root
    )
    code = """import json,sys
sys.path.insert(0,sys.argv[1])
from runtime.sales_depth_contract import (
 validate_sales_depth_contract, validate_formula_split_contract,
)
from runtime.state import HarnessError
value=json.load(sys.stdin)
checks=[('native_formula_split',lambda:validate_formula_split_contract(
 value['plan'],set(value['fact_ids']),value['facts']))]
if value['check_sales_depth']:
 checks.append(('native_sales_depth',lambda:validate_sales_depth_contract(
  value['plan'],set(value['fact_ids']))))
issues=[]
for constraint,check in checks:
 try:
  check()
 except (HarnessError,KeyError,TypeError,ValueError) as error:
  issues.append({'path':'/','message':str(error),'constraint':constraint})
print(json.dumps(issues))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(root / "scripts")],
        input=json.dumps(
            {
                "plan": normalize_native_plan_for_harness(payload),
                "fact_ids": active_fact_ids(request.detail_intake or {}),
                # Use the same source facts as the final native input, not a
                # guessed claim alias or a rewritten fact classification.
                "facts": [
                    *(request.detail_intake or {}).get("user_facts", []),
                    *(request.detail_intake or {}).get("observed_facts", []),
                ],
                "check_sales_depth": not issues,
            }
        ).encode(),
        capture_output=True,
        check=True,
        timeout=15,
    )
    issues = [*issues, *json.loads(result.stdout)]
    if website.enabled(request):
        for issue in issues:
            if any(issue["path"].startswith("/" + key) for key in website.DERIVED):
                issue["path"] = "/" + website.SOURCE
    return issues


def merge_error_fields(
    original: object, correction: object, errors: list[dict[str, Any]]
) -> object:
    """Apply only authored replacements at invalid subtrees, retaining valid fields."""
    merged: Any = deepcopy(original)
    paths = sorted(
        {tuple(e["path"].lstrip("/").split("/")) for e in errors},
        key=lambda p: (len(p), p),
    )
    applied: list[tuple[str, ...]] = []
    for path in paths:
        if any(path[: len(parent)] == parent for parent in applied):
            continue
        if path == ("",):
            return deepcopy(correction)
        try:
            source: Any = correction
            target = merged
            for token in path[:-1]:
                source = (
                    source[int(token)] if isinstance(source, list) else source[token]
                )
                target = (
                    target[int(token)] if isinstance(target, list) else target[token]
                )
            key = path[-1]
            value = source[int(key)] if isinstance(source, list) else source[key]
            if isinstance(target, list):
                target[int(key)] = deepcopy(value)
            else:
                target[key] = deepcopy(value)
            applied.append(path)
        except (KeyError, IndexError, TypeError, ValueError):
            # A missing authored replacement remains invalid; never synthesize it.
            continue
    return merged


def recover_story(
    request: Any, source_root: Path, binding: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Recover a story or pair from immutable replies on an explicit failed retry."""
    if request.authoring_stage not in {"story", "pair", "direct"} or (
        request.authoring_context or {}
    ).get("validation_errors"):
        return None
    from ..xinghe_agent import (
        normalize_usage,
        validate_aurora_domestic_plan,
    )
    from .planning_calls import NativePlanningStore
    from .plans import canonical_hash

    def context(value: Any) -> dict[str, Any]:
        return {
            k: v
            for k, v in dict(value or {}).items()
            if k not in {"validation_errors", "prior_response"}
        }

    def project(value: object) -> object:
        return project_stage(value, request)

    truth_fields = (
        "product_name",
        "product_information",
        "page_count",
        "language",
        "platform_code",
        "size",
        "domestic_protocol",
        "source_inventory",
        "detail_intake",
        "intake_settings",
        "intake_resolution",
        "authoring_stage",
        "authoring_target_ids",
    )
    current = binding["semantic_request"]["semantics"]
    candidates = []
    base = None
    for path in sorted(
        source_root.glob("*.request.json"), key=lambda p: p.stat().st_mtime_ns
    ):
        raw = json.loads(path.read_bytes())["binding"]
        old = NativePlanningStore(source_root, raw)
        if old.store.request_path != path:
            continue
        semantics = raw.get("semantic_request", {}).get("semantics", {})
        if (
            raw.get("phase") != binding["phase"]
            or raw.get("group_key") != binding["group_key"]
            or (
                request.authoring_stage != "direct"
                and raw.get("skill_content_hash") != binding["skill_content_hash"]
            )
            or raw.get("dispatch_route") != binding["dispatch_route"]
            or any(
                canonical_hash(semantics.get(k)) != canonical_hash(current.get(k))
                for k in truth_fields
            )
            or canonical_hash(context(semantics.get("authoring_context")))
            != canonical_hash(context(current.get("authoring_context")))
        ):
            continue
        result = old._original()
        if result.get("error_code") or not isinstance(result.get("payload"), Mapping):
            continue
        projected = project(result["payload"])
        source_hashes = [canonical_hash(result)]
        correction = bool(
            (semantics.get("authoring_context") or {}).get("validation_errors")
        )
        reconstruction = None
        original_context = semantics.get("authoring_context") or {}
        original_prior = original_context.get("prior_response")
        original_errors = original_context.get("validation_errors")
        if (
            correction
            and isinstance(original_prior, Mapping)
            and isinstance(original_errors, list)
            and original_errors
            and all(
                isinstance(error, Mapping) and isinstance(error.get("path"), str)
                for error in original_errors
            )
        ):
            # A failed-only continuation can contain just a sparse correction
            # receipt. Its immutable request already carries the complete prior
            # and exact authorized paths; no earlier call in this folder is needed.
            projected = project(
                merge_error_fields(project(original_prior), projected, original_errors)
            )
            reconstruction = {
                "rule": "merge-original-request-prior-and-original-error-paths-v1",
                "prior_payload_sha256": canonical_hash(original_prior),
                "validation_errors_sha256": canonical_hash(original_errors),
            }
        elif correction and base is not None and stage_errors(projected, request):
            # Retain recovery of older complete-response receipts without a
            # request-bound prior. Current validation still decides acceptance.
            prior = project(base["payload"])
            projected = project(
                merge_error_fields(prior, projected, stage_errors(prior, request))
            )
            source_hashes.insert(0, canonical_hash(base))
        elif not correction:
            base = result
        issues = stage_errors(projected, request)
        assert isinstance(projected, dict)
        if request.authoring_stage == "story" and not issues:
            validate_aurora_domestic_plan(
                projected, request.page_count, campaign_only=True
            )
        candidates.append(
            (
                not issues,
                path.stat().st_mtime_ns,
                raw,
                result,
                projected,
                source_hashes,
                reconstruction,
                issues,
            )
        )
    if not candidates:
        return None
    _, _, original, result, projected, source_hashes, reconstruction, issues = max(
        candidates, key=lambda row: (row[0], row[1])
    )
    return {
        **result,
        "payload": projected,
        "audit_raw_content": None,
        "provider_request_id": None,
        "raw_usage": None,
        "normalized_usage": normalize_usage(None),
        "api_latency_seconds": 0,
        "actual_cost_micros": 0,
        "external_request": False,
        "phase": "aurora_recovered_" + request.authoring_stage,
        "supplemental_results": (),
        "provider_envelope": {
            "authored_input_recovery": {
                "source_binding_sha256": canonical_hash(original),
                "source_result_sha256": canonical_hash(result),
                "source_result_hashes": source_hashes,
                "source_reconstruction": reconstruction,
                "projected_payload_sha256": canonical_hash(projected),
                "source_request_file": str(
                    NativePlanningStore(source_root, original).store.request_path
                ),
                "provider_request_count": 0,
                "stage_valid": not issues,
                "validation_errors": issues,
            }
        },
    }


def fake_phase(payload: Mapping[str, Any], request: Any) -> dict[str, Any]:
    """Project the explicit synthetic fixture through the real stage schemas."""
    if request.authoring_stage == "direct":
        from .direct import mock_brief

        return mock_brief(request)
    from .mock_intake import bounded_mock_campaign

    value = bounded_mock_campaign(payload, request.detail_intake)
    if request.authoring_stage == "story":
        value["modules"] = [{k: m[k] for k in STORY_FIELDS} for m in value["modules"]]
        if website.enabled(request):
            value[website.SOURCE] = [{
                "claim_id": row["claim_id"],
                "evidence_ceiling": row["evidence_ceiling"],
                "module_jobs": deepcopy(row["module_jobs"]),
                "rationale": row["non_duplicate_rationale"],
            } for row in value["selling_point_depth_contract"]["claim_depth"]]
            for key in website.DERIVED:
                value.pop(key)
    elif request.authoring_stage == "pair":
        context = request.authoring_context
        locked = {m["id"]: m for m in context["modules"]}
        patterns = context["reference_expression_system"]["adopted_patterns"]
        for module in value["modules"]:
            for key in STORY_FIELDS:
                module[key] = deepcopy(locked[module["id"]][key])
            module["reference_pattern_ids"] = [
                p["id"] for p in patterns if module["id"] in p["scheduled_module_ids"]
            ]
        value = {
            "modules": [
                m for m in value["modules"] if m["id"] in request.authoring_target_ids
            ]
        }
    errors = (request.authoring_context or {}).get("validation_errors")
    return correction_fixture(value, errors) if errors else value


async def complete(
    coordinator: Any, request: Any, result: Any, results: list[Any]
) -> dict[str, Any]:
    if request.authoring_stage == "direct":
        from .direct import complete as direct_complete

        return await direct_complete(coordinator, request, result, results)
    # Local import avoids a module-load cycle with the shared gateway.
    from ..xinghe_agent import (
        AuroraDetailHarnessError,
        XingheAgentError,
        detail_workspace,
        validate_aurora_domestic_plan,
        write_detail_contract,
    )

    index = len(results) - 1
    extra: list[Any] = []
    corrections = 0
    payload: dict[str, Any] = {}

    async def accept(stage_request: Any, response: Any) -> dict[str, Any]:
        nonlocal corrections

        def candidate(value: Any) -> Any:
            return project_stage(value, stage_request)

        value = candidate(response.payload)
        errors = stage_errors(value, stage_request)
        if errors:
            if corrections:
                raise XingheAgentError(
                    "AURORA_AUTHORING_INVALID",
                    "Bounded authoring correction exhausted",
                    validation_errors=errors,
                )
            corrections += 1
            context = deepcopy(stage_request.authoring_context or {})
            context["prior_response"] = response.payload
            prior_candidate = value
            prior_errors = errors
            context["validation_errors"] = errors
            correction = replace(
                stage_request,
                authoring_context=context,
                idempotency_key=stage_request.idempotency_key + ":correction-1",
            )
            response = await coordinator._native_planner_call(correction)
            extra.append(response)
            if response.error_code:
                raise XingheAgentError(
                    response.error_code,
                    response.error_message or "Authoring result unknown",
                )
            value = candidate(
                merge_error_fields(
                    prior_candidate, candidate(response.payload), prior_errors
                )
            )
            from .plans import canonical_hash

            write_detail_contract(
                detail_workspace(request.job_id)
                / "planning"
                / (
                    "correction-projection-"
                    + stage_request.authoring_stage
                    + "-"
                    + ("+".join(stage_request.authoring_target_ids) or "campaign")
                    + ".json"
                ),
                {
                    "rule": "only-replace-invalid-authored-subtrees-v1",
                    "original_payload_sha256": canonical_hash(prior_candidate),
                    "correction_payload_sha256": canonical_hash(response.payload),
                    "merged_payload_sha256": canonical_hash(value),
                    "validation_errors": prior_errors,
                },
            )
            errors = stage_errors(value, stage_request)
            if errors:
                raise XingheAgentError(
                    "AURORA_AUTHORING_INVALID",
                    "Corrected stage remains invalid",
                    validation_errors=errors,
                )
        if (
            stage_request.authoring_stage == "story"
            and selected_story(response.payload, request.page_count) != response.payload
        ):
            from .plans import canonical_hash

            write_detail_contract(
                detail_workspace(request.job_id)
                / "planning"
                / "selected-story-projection.json",
                {
                    "rule": "retain-the-single-authored-direct-selection-v1",
                    "source_payload_sha256": canonical_hash(response.payload),
                    "projected_payload_sha256": canonical_hash(value),
                    "discarded": [
                        row
                        for row in response.payload["output_strategy"][
                            "module_count_candidates"
                        ]
                        if row.get("decision") == "REJECTED"
                    ],
                },
            )
        return deepcopy(dict(value))

    try:
        payload = await accept(request, result)
        payload["product_intake"] = deepcopy(request.detail_intake)
        # Structural campaign validation occurs before any screen-completion spend.
        validate_aurora_domestic_plan(
            payload,
            request.page_count,
            skill_root=coordinator.skill_root,
            campaign_only=True,
        )
        story = deepcopy(payload)
        completed: list[dict[str, Any]] = []
        for offset in range(0, request.page_count, 2):
            targets = tuple(m["id"] for m in story["modules"][offset : offset + 2])
            context = deepcopy(story)
            # The compact full storyboard preserves global rhythm; completed detail
            # is only needed for the immediately preceding handoff, not all screens.
            if completed:
                context["previous_completed_screen"] = completed[-1]
            pair_request = replace(
                request,
                planning_profile="aurora_domestic_modules_completion",
                authoring_stage="pair",
                authoring_target_ids=targets,
                authoring_context=context,
                system_prompt=system_prompt(coordinator.skill_root, stage="pair"),
                sources=(),
                idempotency_key=request.idempotency_key + ":" + "+".join(targets),
            )
            response = await coordinator._native_planner_call(pair_request)
            extra.append(response)
            if response.error_code:
                raise XingheAgentError(
                    response.error_code,
                    response.error_message or "Authoring result unknown",
                )
            pair = await accept(pair_request, response)
            completed.extend(pair["modules"])
            write_detail_contract(
                detail_workspace(request.job_id)
                / "planning"
                / f"authored-{offset // 2 + 1}.json",
                pair,
            )
        payload["modules"] = completed
        structured = validate_aurora_domestic_plan(
            payload, request.page_count, skill_root=coordinator.skill_root
        )
        structured["native_approval"] = await asyncio.to_thread(
            coordinator._preflight_aurora_plan, request, structured["aurora_plan"]
        )
        return structured
    except AuroraDetailHarnessError as exc:
        raise XingheAgentError("AURORA_PLAN_PREFLIGHT_INVALID", str(exc)) from exc
    finally:
        results[index] = replace(
            result,
            payload=payload or result.payload,
            phase="aurora_domestic_detail",
            supplemental_results=(*result.supplemental_results, *extra),
        )
