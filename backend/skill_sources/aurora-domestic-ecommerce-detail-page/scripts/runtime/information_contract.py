from __future__ import annotations

from .state import HarnessError, require_fields


PROGRESSION_ROLES = {
    "IDENTITY", "PAIN", "BENEFIT", "DEDICATED_PROOF", "MECHANISM_OR_STRUCTURE",
    "SELECTION_OR_USE", "RESULT_OR_EXPERIENCE", "TRUST", "CLOSURE", "OTHER",
}


def is_v41(plan: dict) -> bool:
    return plan.get("information_contract_version") == "4.1" or plan.get("planning_mode") == "LIGHTWEIGHT_V41"


def validate_hero_screen_contract(plan: dict) -> None:
    contract = plan.get("hero_screen_contract")
    if not isinstance(contract, dict):
        raise HarnessError("v4.1 requires hero_screen_contract; recompile the plan with a unique M01 hero")
    require_fields(contract, ("hero_module_id", "hero_entity_id", "hero_job", "hero_uniqueness", "supporting_entities_allowed", "co_equal_hero_forbidden", "hero_identity_evidence"), "hero_screen_contract")
    if contract["hero_module_id"] != "M01":
        raise HarnessError("hero_screen_contract.hero_module_id must be M01")
    if contract["hero_uniqueness"] != "ONE_PRIMARY_HERO":
        raise HarnessError("M01 must declare hero_uniqueness=ONE_PRIMARY_HERO")
    if contract["co_equal_hero_forbidden"] is not True:
        raise HarnessError("M01 must forbid co-equal heroes")
    modules = plan.get("modules") or []
    if not modules or modules[0].get("id") != "M01":
        raise HarnessError("The unique hero contract requires the first module to be M01")
    first = modules[0]
    if first.get("information_role") not in ("IDENTITY", "BENEFIT"):
        raise HarnessError("M01 information_role must establish identity or core benefit")
    if first.get("hero_entity_id") != contract["hero_entity_id"]:
        raise HarnessError("M01 hero_entity_id does not match hero_screen_contract")
    if first.get("co_equal_hero_entity_ids"):
        raise HarnessError("M01 cannot declare co-equal hero entities")


def validate_detail_information_contract(plan: dict) -> None:
    contract = plan.get("detail_information_contract")
    if not isinstance(contract, dict):
        raise HarnessError("v4.1 requires detail_information_contract; recompile the information progression")
    require_fields(contract, ("buyer_question_sequence", "non_repeating_overview_limit", "required_progression_jobs", "every_pair_requires_information_delta"), "detail_information_contract")
    questions = contract["buyer_question_sequence"]
    if not isinstance(questions, list) or len(questions) < 4 or any(not str(item).strip() for item in questions):
        raise HarnessError("detail_information_contract.buyer_question_sequence needs at least four concrete buyer questions")
    if contract["non_repeating_overview_limit"] != 1:
        raise HarnessError("Only one overview role may be used without a new decision job")
    if contract["every_pair_requires_information_delta"] is not True:
        raise HarnessError("Every Pair must require a visible information delta")
    roles = contract["required_progression_jobs"]
    if not isinstance(roles, list) or not {"IDENTITY", "PAIN", "BENEFIT", "DEDICATED_PROOF", "CLOSURE"}.issubset(set(roles)):
        raise HarnessError("Information progression must include identity, pain, benefit, dedicated proof and closure")
    if any(role not in PROGRESSION_ROLES for role in roles):
        raise HarnessError("Unsupported information progression role")


def validate_information_progression(plan: dict) -> None:
    modules = plan.get("modules") or []
    if not modules:
        raise HarnessError("Information progression requires modules")
    seen_overviews = 0
    known_ids = {item.get("id") for item in modules}
    for index, module in enumerate(modules):
        require_fields(module, ("information_role", "buyer_question_answered", "new_information_added", "depends_on_module_ids", "repeats_module_ids", "decision_enabled"), f"information contract for {module.get('id', '?')}")
        if module["information_role"] in ("BENEFIT", "BENEFIT_OVERVIEW"):
            seen_overviews += 1
        if not isinstance(module["new_information_added"], list) or not module["new_information_added"]:
            raise HarnessError(f"Module {module.get('id')} must declare concrete new_information_added")
        if set(module["depends_on_module_ids"]) - known_ids or set(module["repeats_module_ids"]) - known_ids:
            raise HarnessError(f"Module {module.get('id')} references unknown information modules")
        if module["repeats_module_ids"] and not module["new_information_added"]:
            raise HarnessError(f"Module {module.get('id')} repeats information without adding anything")
        if not str(module["decision_enabled"]).strip() or module["decision_enabled"].strip() in {"进一步了解产品", "了解更多"}:
            raise HarnessError(f"Module {module.get('id')} must enable a concrete purchase judgment")
        if index > 0 and not module["buyer_question_answered"].strip():
            raise HarnessError(f"Module {module.get('id')} needs a buyer question")
    if seen_overviews > 1:
        raise HarnessError("The page repeats overview information without a new decision role")
    pair_ids = [item.get("pair_id") for item in ((plan.get("output_strategy") or {}).get("continuous_pair_rendering") or {}).get("pair_ids", [])]
    for pair in pair_ids:
        if not pair:
            raise HarnessError("Every continuous pair needs a stable pair_id for information progression")


def validate_information_contract(plan: dict) -> None:
    if not is_v41(plan):
        return
    validate_hero_screen_contract(plan)
    validate_detail_information_contract(plan)
    validate_information_progression(plan)


