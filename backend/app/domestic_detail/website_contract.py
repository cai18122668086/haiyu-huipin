"""Versioned website authoring views of one explicit creative claim/job map."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

PROFILE = "WEBSITE_LEAN_V1"
MARKER = "website_contract_version"
SOURCE = "claim_coverage"
DERIVED = ("selling_point_depth_contract", "campaign_recall_contract")


def enabled(request: Any) -> bool:
    return (request.intake_settings or {}).get(MARKER) == PROFILE


def source_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    depth = schema["properties"][DERIVED[0]]["properties"]["claim_depth"]["items"]
    props = depth["properties"]
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["claim_id", "evidence_ceiling", "module_jobs", "rationale"],
            "properties": {
                "claim_id": deepcopy(props["claim_id"]),
                "evidence_ceiling": deepcopy(props["evidence_ceiling"]),
                "module_jobs": deepcopy(props["module_jobs"]),
                "rationale": {"type": "string", "minLength": 8},
            },
        },
    }


def schema_view(schema: dict[str, Any], *, wire: bool) -> dict[str, Any]:
    schema["properties"][SOURCE] = source_schema(schema)
    schema["properties"][MARKER] = {"const": PROFILE}
    if wire:
        schema["required"] = [k for k in schema["required"] if k not in DERIVED]
        for key in DERIVED:
            schema["properties"].pop(key)
    schema["required"].append(SOURCE)
    return schema


def derived_views(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Every association comes from an authored job row; never guess coverage."""
    rows = value.get(SOURCE)
    if not isinstance(rows, list) or not rows:
        return None
    try:
        primary, entries, proof, overview, closure = [], [], {}, [], []
        for row in rows:
            claim = row["claim_id"]
            jobs = row["module_jobs"]
            if not isinstance(claim, str) or not isinstance(jobs, list) or not jobs:
                return None
            scheduled = list(dict.fromkeys(job["module_id"] for job in jobs))
            duties = list(dict.fromkeys(job["job"] for job in jobs))
            dedicated = list(
                dict.fromkeys(
                    job["module_id"]
                    for job in jobs
                    if job["job"] == "DEDICATED_EXPRESSION"
                )
            )
            primary.append(claim)
            if "VALUE_OVERVIEW" in duties:
                overview.append(claim)
            if "RECALL" in duties:
                closure.append(claim)
            proof[claim] = dedicated
            entries.append(
                {
                    "claim_id": claim,
                    "priority": "PRIMARY",
                    "depth_tier": "DEEP",
                    "evidence_ceiling": row["evidence_ceiling"],
                    "required_decision_jobs": duties,
                    "scheduled_module_ids": scheduled,
                    "dedicated_module_ids": dedicated,
                    "module_jobs": deepcopy(jobs),
                    "non_duplicate_rationale": row["rationale"],
                    "forbidden_expansion": ["不得超出锁定事实和当前证据边界"],
                }
            )
        if len(set(primary)) != len(primary):
            return None
        return {
            DERIVED[0]: {
                "selection_policy": (
                    "SMALLEST_CANDIDATE_THAT_SATISFIES_SALES_DEPTH_"
                    "NOT_MINIMUM_FACT_LISTING"
                ),
                "primary_claim_ids": primary,
                "claim_depth": entries,
                "compact_selection_exception": "",
            },
            DERIVED[1]: {
                "must_remember_claim_ids": primary,
                "first_screen_value_claim_ids": overview,
                "proof_module_ids_by_claim": proof,
                "closure_reinforcement_claim_ids": closure,
                "buyer_recall_test": (
                    "核对每项主卖点是否具有已创作的总览、独立证明、"
                    "使用判断和收尾回顾任务。"
                ),
            },
        }
    except (KeyError, TypeError):
        return None


def project(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return payload
    result = deepcopy(dict(payload))
    result.setdefault(MARKER, PROFILE)
    views = derived_views(result)
    if views is not None:
        for key, value in views.items():
            result.setdefault(key, value)
    return result


def prompt_context(
    context: dict[str, Any], target_ids: tuple[str, ...]
) -> dict[str, Any]:
    context = deepcopy(context)
    for key in (*DERIVED, SOURCE, "user_selling_point_coverage_ledger"):
        context.pop(key, None)
    strategy = context.get("output_strategy")
    if isinstance(strategy, Mapping):
        pair = strategy.get("continuous_pair_rendering") or {}
        context["output_strategy"] = {
            "module_count": strategy.get("module_count"),
            "continuous_pair_rendering": {
                "pair_canvas_aspect_ratio": "3:8",
                "pair_ids": [
                    row
                    for row in pair.get("pair_ids", [])
                    if row.get("module_ids") == list(target_ids)
                ],
            },
        }
    return context


STORY_INSTRUCTION = """Author one complete campaign and its ordered atomic storyboard.
Use the supplied schema. Choose one direct 8/10/12-screen count as requested, not
alternatives. Keep original active fact IDs, truthful evidence ceilings, useful
screen duties, concise copy, distinct visual subjects/forms and campaign DNA.
claim_coverage is the ONLY primary-claim/job source. For every primary claim,
write its actual module_jobs once: VALUE_OVERVIEW, DEDICATED_EXPRESSION, at least
one USE_SCOPE/RESULT_OR_EXPERIENCE/MECHANISM_OR_REASON/ACTION_OR_OPERATION/SELECTION/
TRUST job, and RECALL, across at least three screens. A dedicated job must use a
screen whose lead_claim_id is that claim. One screen may have several job rows.
Never invent a job merely to fill the map: make its purpose visible in the spine.
The server derives depth and recall views; do not output those parallel ledgers.
Formula evaluation still accounts exactly once for each actual INGREDIENT_FORMULA
or STRUCTURE_COMPONENT fact. No material, performance, size or brand invention.
Only source style references may supply reference patterns. Adjacent screen pairs
are fixed 3:8 canvases; the lower incoming transition defines each internal seam.
Return only schema JSON, with the compact spine fields, not detailed rendering
contracts. Later requests author the two-screen rendering details.
"""
