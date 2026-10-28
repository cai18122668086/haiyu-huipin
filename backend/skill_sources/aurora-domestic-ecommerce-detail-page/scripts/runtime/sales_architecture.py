from __future__ import annotations

from .state import HarnessError, require_fields


COVERAGE_STATUSES = {"ACTIVE", "MERGED", "QUALIFIED", "DEFERRED", "CONFLICT_BLOCKED"}
PRIORITIES = {"PRIMARY", "SECONDARY", "TERTIARY"}
VISUAL_ARCHETYPES = {
    "PAIN_SCENE", "PAIN_TO_SOLUTION_BRIDGE", "BENEFIT_OVERVIEW",
    "DEDICATED_BENEFIT_PROOF", "SOURCE_ACTION_RESULT", "WHOLE_PLUS_MACRO",
    "ACTION_SEQUENCE", "USE_SCOPE_MATRIX", "STATE_CONTRAST", "SENSORY_WORLD",
    "SELECTION_COMPARISON", "TRUST_EVIDENCE", "PURCHASE_CLOSURE",
}


def validate_user_selling_point_ledger(plan: dict, supplied_points: list[dict], fact_ids: set[str]) -> None:
    ledger = plan.get("user_selling_point_coverage_ledger")
    if ledger is None:
        raise HarnessError("COMPACT_V1_3 requires user_selling_point_coverage_ledger")
    if not isinstance(ledger, list):
        raise HarnessError("user_selling_point_coverage_ledger must be a list")
    supplied_ids = {item["id"] for item in supplied_points}
    ledger_ids = [item.get("selling_point_id") for item in ledger]
    if set(ledger_ids) != supplied_ids or len(ledger_ids) != len(set(ledger_ids)):
        raise HarnessError("The selling-point ledger must account for every user-supplied point exactly once")
    known_modules = {item["id"] for item in plan["modules"]}
    for item in ledger:
        require_fields(
            item,
            ("selling_point_id", "status", "atomic_claim_ids", "planned_module_ids", "reason"),
            "user selling-point coverage ledger entry",
        )
        if item["status"] not in COVERAGE_STATUSES:
            raise HarnessError("Unsupported user selling-point coverage status")
        claims = set(item["atomic_claim_ids"])
        modules = set(item["planned_module_ids"])
        if claims - fact_ids or modules - known_modules:
            raise HarnessError("Selling-point ledger refers to unknown claims or modules")
        if item["status"] in {"ACTIVE", "MERGED", "QUALIFIED"} and (not claims or not modules):
            raise HarnessError("An active, merged or qualified user selling point needs claims and planned modules")
        if item["status"] in {"DEFERRED", "CONFLICT_BLOCKED"} and len(str(item["reason"]).strip()) < 10:
            raise HarnessError("A deferred or blocked user selling point needs a concrete reason")


def validate_dynamic_count_formula(plan: dict) -> None:
    strategy = plan["output_strategy"]
    formula = strategy.get("module_count_formula")
    if not isinstance(formula, dict):
        raise HarnessError("COMPACT_V1_3 requires module_count_formula")
    require_fields(
        formula,
        (
            "base_narrative_jobs", "primary_depth_expansion", "supporting_jobs",
            "shared_proof_savings", "computed_balanced_minimum", "shared_proof_rationale",
        ),
        "dynamic module-count formula",
    )
    numeric = ("base_narrative_jobs", "primary_depth_expansion", "supporting_jobs", "shared_proof_savings", "computed_balanced_minimum")
    if any(not isinstance(formula[key], int) or isinstance(formula[key], bool) for key in numeric):
        raise HarnessError("Module-count formula values must be integers")
    if formula["base_narrative_jobs"] < 3:
        raise HarnessError("A normal sales page needs hook, problem/bridge and purchase closure narrative jobs")
    primary_count = len(plan["selling_point_depth_contract"]["primary_claim_ids"])
    if formula["primary_depth_expansion"] < primary_count * 2:
        raise HarnessError("Primary-depth expansion must budget overview/dedicated/use-or-result depth for every primary claim")
    if not 0 <= formula["shared_proof_savings"] <= max(0, primary_count - 1):
        raise HarnessError("Shared-proof savings exceed the number of genuinely shareable primary claims")
    expected = (
        formula["base_narrative_jobs"] + formula["primary_depth_expansion"]
        + formula["supporting_jobs"] - formula["shared_proof_savings"]
    )
    if formula["computed_balanced_minimum"] != expected:
        raise HarnessError("computed_balanced_minimum does not match the declared module-count formula")
    selected = next(item for item in strategy["module_count_candidates"] if item["decision"] == "SELECTED")
    if selected["candidate_mode"] == "DIRECT_DERIVATION" and selected["count"] < expected:
        raise HarnessError("Selected screen count is below the primary-selling-point depth floor")


def validate_visual_archetypes(plan: dict) -> None:
    modules = plan["modules"]
    archetypes = []
    for module in modules:
        archetype = module.get("visual_archetype")
        if archetype not in VISUAL_ARCHETYPES:
            raise HarnessError(f"Module {module['id']} requires a supported cross-category visual archetype")
        archetypes.append(archetype)
        if module.get("story_function") == "PAIN_ARTICULATION" and archetype not in {"PAIN_SCENE", "STATE_CONTRAST"}:
            raise HarnessError("A pain module must use a visible pain or state-contrast archetype")
        if module.get("story_function") == "PURCHASE_CLOSURE" and archetype != "PURCHASE_CLOSURE":
            raise HarnessError("The final sales closure must use PURCHASE_CLOSURE")
        expression = module.get("module_visual_expression_contract") or module.get("expression") or {}
        if archetype in {"DEDICATED_BENEFIT_PROOF", "SOURCE_ACTION_RESULT", "STATE_CONTRAST"}:
            if expression.get("claim_visualizability") in {"VISUALIZABLE", "PARTIALLY_VISUALIZABLE"}:
                if expression.get("product_presence_policy") == "REQUIRED_DOMINANT":
                    raise HarnessError("A visualizable proof screen may not force the product to dominate its claim object or transformation")
                if expression.get("evidence_subject_policy") == "PRODUCT_DOMINANT":
                    raise HarnessError("A dedicated proof screen must make the claim object, action or result—not the packshot—the evidence subject")
        if expression.get("visualization_mode") == "CONCEPTUAL_NON_DIAGNOSTIC":
            if not expression.get("transformation_required") or len(expression.get("transformation_stages") or []) < 2:
                raise HarnessError("Conceptual proof must show at least two visible transformation stages instead of a labeled icon")
    if len(modules) >= 6 and len(set(archetypes)) < 4:
        raise HarnessError("The page needs at least four cross-category visual archetypes")
    for index in range(max(0, len(archetypes) - 2)):
        if len(set(archetypes[index:index + 3])) == 1:
            raise HarnessError("Three consecutive modules may not repeat one visual archetype")


