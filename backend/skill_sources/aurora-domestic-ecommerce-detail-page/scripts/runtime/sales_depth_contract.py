from __future__ import annotations

from .state import HarnessError, require_fields


CANDIDATE_MODES = ("DIRECT_DERIVATION", "COMPACT_COVERAGE", "BALANCED_EXPRESSION", "FULL_SALES")
DECISION_JOBS = {
    "PROBLEM_CONTEXT", "VALUE_OVERVIEW", "DEDICATED_EXPRESSION", "MECHANISM_OR_REASON",
    "USE_SCOPE", "ACTION_OR_OPERATION", "RESULT_OR_EXPERIENCE", "RISK_QUALIFICATION",
    "SELECTION", "TRUST", "RECALL",
}
EVIDENCE_CEILINGS = {"DIRECT_PROOF_AVAILABLE", "QUALIFIED_CLAIM_ONLY", "PACKAGING_CLAIM_ONLY", "CONTEXT_ONLY"}


def _formula_facts(facts: list[dict] | None) -> dict[str, dict]:
    return {
        item["id"]: item
        for item in (facts or [])
        if item.get("claim_kind") in {"INGREDIENT_FORMULA", "STRUCTURE_COMPONENT"}
    }


def validate_formula_split_contract(plan: dict, fact_ids: set[str], facts: list[dict] | None = None) -> None:
    """Require independently meaningful formula/ingredient claims to be evaluated and visibly expressed."""
    contract = plan.get("formula_split_contract")
    formula_facts = _formula_facts(facts)
    if not contract:
        if formula_facts:
            raise HarnessError("Formula, ingredient and structure facts require formula_split_contract evaluation")
        return
    require_fields(contract, ("enabled", "trigger_basis", "evaluated_claims", "entries"), "formula split contract")
    evaluated = contract["evaluated_claims"]
    if not isinstance(evaluated, list):
        raise HarnessError("formula_split_contract.evaluated_claims must be a list")
    evaluated_ids = [item.get("claim_id") for item in evaluated]
    if set(evaluated_ids) != set(formula_facts) or len(evaluated_ids) != len(set(evaluated_ids)):
        raise HarnessError("Formula evaluation must account for every structured formula/ingredient/structure fact exactly once")
    evaluation_by_id = {}
    for item in evaluated:
        require_fields(
            item,
            (
                "claim_id", "formula_or_element", "named_ingredient", "user_prominence", "display_intent",
                "supported_benefit", "buyer_question", "trigger_decision", "reason",
            ),
            "formula evaluation entry",
        )
        if "merge_target_claim_id" not in item:
            raise HarnessError("formula evaluation entry missing required field: merge_target_claim_id")
        fact = formula_facts[item["claim_id"]]
        if item["display_intent"] != fact.get("display_intent") or item["user_prominence"] != fact.get("user_prominence"):
            raise HarnessError("Formula evaluation must preserve fact display intent and prominence")
        names = fact.get("named_elements") or []
        if bool(names) != bool(item["named_ingredient"]):
            raise HarnessError("Formula evaluation named_ingredient must match structured named_elements")
        if names and item["formula_or_element"] not in names:
            raise HarnessError("Formula evaluation must use a verified named formula/ingredient/structure element")
        required = (
            fact.get("display_intent") == "MUST_SHOW"
            and fact.get("user_prominence") == "CORE_SELLING_POINT"
            and bool(names)
        )
        if required and item["trigger_decision"] not in {"DEDICATED_EXPRESSION_REQUIRED", "MERGE_ALLOWED"}:
            raise HarnessError("A core named MUST_SHOW formula/ingredient/structure claim requires dedicated or controlled merged expression")
        if item["trigger_decision"] == "MERGE_ALLOWED":
            if not item["merge_target_claim_id"] or item["merge_target_claim_id"] == item["claim_id"]:
                raise HarnessError("MERGE_ALLOWED requires another verified formula claim as merge_target_claim_id")
            if len(str(item["reason"]).strip()) < 10:
                raise HarnessError("Merged formula expression requires a concrete same-problem/evidence/visual-route reason")
        elif item["merge_target_claim_id"] is not None:
            raise HarnessError("Only MERGE_ALLOWED may set merge_target_claim_id")
        evaluation_by_id[item["claim_id"]] = item

    expression_required = {
        claim_id for claim_id, item in evaluation_by_id.items()
        if item["trigger_decision"] in {"DEDICATED_EXPRESSION_REQUIRED", "MERGE_ALLOWED"}
    }
    if not contract["enabled"]:
        if contract["entries"] or expression_required:
            raise HarnessError("A disabled formula split contract must not contain entries")
        return
    if not contract["entries"]:
        raise HarnessError("Enabled formula split contract must list each independent formula/element claim")
    modules = {item["id"]: item for item in plan["modules"]}
    entry_claim_ids = [item.get("claim_id") for item in contract["entries"]]
    if set(entry_claim_ids) != expression_required or len(entry_claim_ids) != len(set(entry_claim_ids)):
        raise HarnessError("Formula split entries must exactly match claims requiring visible expression")
    modules_to_entries: dict[str, list[dict]] = {}
    for entry in contract["entries"]:
        require_fields(entry, ("claim_id", "formula_or_element", "independent_split_reason", "buyer_problem_or_question", "supported_benefit", "evidence_ceiling", "dedicated_module_id", "visual_expression_route"), "formula split entry")
        if entry["claim_id"] not in fact_ids or entry["evidence_ceiling"] not in EVIDENCE_CEILINGS:
            raise HarnessError("Formula split entries must use active verified claims and approved evidence ceilings")
        module_id = entry["dedicated_module_id"]
        if module_id not in modules:
            raise HarnessError("Each formula/element claim needs a valid expression module")
        modules_to_entries.setdefault(module_id, []).append(entry)
        module = modules[module_id]
        module_claims = {module.get("lead_claim_id"), *module.get("supporting_claim_ids", [])}
        if entry["claim_id"] not in module_claims:
            raise HarnessError("A formula dedicated module must carry its verified claim")
        if evaluation_by_id[entry["claim_id"]]["trigger_decision"] == "DEDICATED_EXPRESSION_REQUIRED" and module.get("lead_claim_id") != entry["claim_id"]:
            raise HarnessError("A formula dedicated module must lead with that formula/element claim")
    for module_id, grouped in modules_to_entries.items():
        if len(grouped) == 1:
            continue
        if any(evaluation_by_id[item["claim_id"]]["trigger_decision"] != "MERGE_ALLOWED" for item in grouped):
            raise HarnessError("Only controlled MERGE_ALLOWED formula claims may share one module")
        comparison_keys = ("buyer_problem_or_question", "supported_benefit", "evidence_ceiling", "visual_expression_route")
        if any(len({item[key] for item in grouped}) != 1 for key in comparison_keys):
            raise HarnessError("Merged formula claims must share buyer question, benefit, evidence ceiling and visual route")


def validate_sales_depth_contract(plan: dict, fact_ids: set[str]) -> None:
    strategy = plan["output_strategy"]
    candidates = strategy["module_count_candidates"]
    if len(candidates) != 1:
        raise HarnessError("Screen-count planning must contain exactly one direct derivation record; do not compare candidates")
    by_mode = {}
    for candidate in candidates:
        require_fields(
            candidate,
            ("candidate_mode", "count", "coverage_tradeoff", "selling_point_fit", "story_obligation_fit",
             "primary_selling_point_depth_fit", "evidence_boundary_fit", "decision"),
            "direct screen-count decision",
        )
        mode = candidate["candidate_mode"]
        if mode not in CANDIDATE_MODES or mode in by_mode:
            raise HarnessError("Screen-count derivation must use the DIRECT_DERIVATION mode exactly once")
        by_mode[mode] = candidate
    if set(by_mode) != {"DIRECT_DERIVATION"}:
        raise HarnessError("Screen-count derivation must use DIRECT_DERIVATION and must not expose alternatives")

    selected = [item for item in candidates if item["decision"] == "SELECTED"]
    if len(selected) != 1:
        raise HarnessError("Exactly one direct screen-count decision must be selected")

    contract = plan.get("selling_point_depth_contract")
    if not contract:
        raise HarnessError("Route-aware sales planning requires selling_point_depth_contract")
    require_fields(contract, ("selection_policy", "primary_claim_ids", "claim_depth"), "selling-point depth contract")
    if "compact_selection_exception" in contract and not isinstance(contract["compact_selection_exception"], str):
        raise HarnessError("compact_selection_exception, when present, must be a string")
    primary = set(contract["primary_claim_ids"])
    if not primary or primary - fact_ids:
        raise HarnessError("Primary selling points must use active verified fact IDs")
    recall_primary = set(plan.get("campaign_recall_contract", {}).get("must_remember_claim_ids", []))
    if primary != recall_primary:
        raise HarnessError("Selling-point depth primary claims must equal the campaign must-remember claims")
    depth_items = contract["claim_depth"]
    if {item.get("claim_id") for item in depth_items} != primary:
        raise HarnessError("Selling-point depth entries must account for every primary claim exactly once")
    modules = {item["id"]: item for item in plan["modules"]}
    deep_count = 0
    for item in depth_items:
        require_fields(
            item,
            ("claim_id", "depth_tier", "evidence_ceiling", "required_decision_jobs", "scheduled_module_ids",
             "dedicated_module_ids", "non_duplicate_rationale", "forbidden_expansion"),
            "selling-point depth entry",
        )
        if item["depth_tier"] not in ("DEEP", "STANDARD", "SUPPORT") or item["evidence_ceiling"] not in EVIDENCE_CEILINGS:
            raise HarnessError("Selling-point depth entry has an unsupported tier or evidence ceiling")
        if plan.get("plan_format") == "COMPACT_V1_3":
            require_fields(item, ("priority", "module_jobs"), "v1.3 selling-point depth entry")
            if item["priority"] != "PRIMARY":
                raise HarnessError("Every must-remember claim must be explicitly prioritized PRIMARY")
            if not isinstance(item["module_jobs"], list) or not item["module_jobs"]:
                raise HarnessError("Every primary claim needs an explicit module-to-decision-job map")
        jobs = set(item["required_decision_jobs"])
        scheduled = set(item["scheduled_module_ids"])
        dedicated = set(item["dedicated_module_ids"])
        if not jobs or jobs - DECISION_JOBS or not scheduled or scheduled - set(modules) or dedicated - scheduled:
            raise HarnessError("Selling-point depth jobs and modules must use approved values")
        if not dedicated:
            raise HarnessError("Every primary selling point needs at least one dedicated or visually dominant module")
        for module_id in dedicated:
            if plan.get("plan_format") == "COMPACT_V1_3":
                if modules[module_id]["lead_claim_id"] != item["claim_id"]:
                    raise HarnessError("A dedicated selling-point module must lead with that claim; supporting presence is not depth")
            else:
                module_claims = {modules[module_id]["lead_claim_id"], *modules[module_id].get("supporting_claim_ids", [])}
                if item["claim_id"] not in module_claims:
                    raise HarnessError("A dedicated selling-point module must actually carry that verified claim")
        if item["depth_tier"] == "DEEP":
            deep_count += 1
            if plan.get("plan_format") == "COMPACT_V1_3":
                required_core = {"VALUE_OVERVIEW", "DEDICATED_EXPRESSION", "RECALL"}
                outcome_jobs = {"MECHANISM_OR_REASON", "USE_SCOPE", "ACTION_OR_OPERATION", "RESULT_OR_EXPERIENCE", "SELECTION", "TRUST"}
                if len(jobs) < 4 or len(scheduled) < 3 or not required_core.issubset(jobs) or not jobs & outcome_jobs:
                    raise HarnessError("A DEEP selling point needs overview, dedicated proof, a use/result decision job and recall")
                mapped_modules = {entry.get("module_id") for entry in item["module_jobs"]}
                mapped_jobs = {entry.get("job") for entry in item["module_jobs"]}
                if mapped_modules != scheduled or mapped_jobs != jobs:
                    raise HarnessError("module_jobs must exactly explain scheduled modules and required decision jobs")
            elif len(jobs) < 2 or len(scheduled) < 2:
                raise HarnessError("A DEEP selling point needs at least two decision jobs and two modules")
        if item["evidence_ceiling"] in ("PACKAGING_CLAIM_ONLY", "CONTEXT_ONLY") and "MECHANISM_OR_REASON" in jobs:
            raise HarnessError("Packaging-only or context-only evidence cannot be expanded into a mechanism claim")
    if plan.get("plan_format") == "COMPACT_V1_3":
        if deep_count != len(primary):
            raise HarnessError("Every must-remember primary selling point must receive DEEP sales treatment")
    else:
        minimum_deep = 2 if len(primary) >= 3 else 1
        if deep_count < minimum_deep:
            raise HarnessError("The must-remember selling-point set is underdeveloped for a sales detail page")

    # Direct derivation has no compact-selection exception or candidate trade-off table.


