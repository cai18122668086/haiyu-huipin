from __future__ import annotations

from .state import HarnessError, require_fields


STORY_FUNCTIONS = {
    "HOOK", "PAIN_ARTICULATION", "PAIN_SOLUTION_COMBINED", "SOLUTION_PROOF",
    "MECHANISM_PROOF", "MATERIAL_PROOF", "USE_OR_RESULT", "TRUST_OR_RISK_REMOVAL",
    "SELECTION_OR_SPECIFICATION", "PURCHASE_CLOSURE", "OTHER",
}

VISUAL_CARRIERS = {
    "PRODUCT_HERO", "PAIN_SCENE", "ACTION_CONTACT", "STATE_CONTRAST", "PROCESS_PATH",
    "MATERIAL_MACRO", "RESULT_SCENE", "SCALE_RELATION", "DOCUMENT_EVIDENCE",
    "PRODUCT_DETAIL", "USE_SCENE", "SELECTION", "SPECIFICATION", "TRUST", "PURCHASE_CLOSURE",
}


def validate_story_contract(plan: dict, analysis: dict) -> None:
    require_fields(plan, ("campaign_pain_solution_contract", "campaign_visual_carrier_sequence"), "v0.9 route-aware plan")
    modules = plan["modules"]
    module_by_id = {item["id"]: item for item in modules}
    order = {item["id"]: item["order"] for item in modules}
    known_pains = {item["id"] for item in analysis["pain_hypotheses"]}
    known_bridges = {item["id"] for item in analysis["value_bridges"]}

    pain_contract = plan["campaign_pain_solution_contract"]
    require_fields(
        pain_contract,
        ("primary_pain_id", "coverage_mode", "pain_module_ids", "solution_module_ids", "value_bridge_ids",
         "visible_problem_boundary", "pain_to_solution_transition", "exemption_basis", "exemption_reason"),
        "campaign pain-solution contract",
    )
    selected_pain = analysis.get("primary_pain_id") or pain_contract["primary_pain_id"]
    if pain_contract["primary_pain_id"] != selected_pain or pain_contract["primary_pain_id"] not in known_pains:
        raise HarnessError("Pain-solution contract must preserve the selected primary pain")
    mode = pain_contract["coverage_mode"]
    if mode not in ("DEDICATED_PAIN_MODULE", "COMBINED_PAIN_SOLUTION_MODULE", "EXPLICITLY_NOT_APPLICABLE"):
        raise HarnessError("Unsupported pain-solution coverage mode")
    pain_modules = set(pain_contract["pain_module_ids"])
    solution_modules = set(pain_contract["solution_module_ids"])
    bridge_ids = set(pain_contract["value_bridge_ids"])
    if pain_modules - set(module_by_id) or solution_modules - set(module_by_id):
        raise HarnessError("Pain and solution module IDs must name planned modules")
    if bridge_ids - known_bridges:
        raise HarnessError("Pain-solution value bridges must come from operating analysis")
    if mode == "EXPLICITLY_NOT_APPLICABLE":
        if pain_contract["exemption_basis"] != "USER_REQUESTED_NON_SALES_INFORMATION_PAGE" or len(str(pain_contract["exemption_reason"]).strip()) < 20:
            raise HarnessError("A normal sales page cannot exempt the pain-to-solution story obligation")
    else:
        if not pain_modules or not solution_modules or not bridge_ids:
            raise HarnessError("A sales page requires visible pain modules, solution modules and verified value bridges")
        if mode == "DEDICATED_PAIN_MODULE":
            if pain_modules & solution_modules:
                raise HarnessError("Dedicated pain modules must be distinct from solution modules")
            if max(order[item] for item in pain_modules) >= min(order[item] for item in solution_modules):
                raise HarnessError("Dedicated pain must appear before its product solution")
            if any(module_by_id[item].get("story_function") != "PAIN_ARTICULATION" for item in pain_modules):
                raise HarnessError("Dedicated pain modules must use PAIN_ARTICULATION")
        else:
            if not pain_modules & solution_modules:
                raise HarnessError("Combined pain-solution coverage must overlap in at least one module")
            if any(module_by_id[item].get("story_function") != "PAIN_SOLUTION_COMBINED" for item in pain_modules & solution_modules):
                raise HarnessError("Combined pain-solution modules must declare PAIN_SOLUTION_COMBINED")
        if not any(bridge_ids & set(module_by_id[item].get("value_bridge_ids", [])) for item in solution_modules):
            raise HarnessError("A solution module must visibly carry the verified value bridge")

    sequence = plan["campaign_visual_carrier_sequence"]
    require_fields(sequence, ("sequence", "max_consecutive_static_packshots", "minimum_carrier_classes", "campaign_rationale"), "campaign visual carrier sequence")
    if [item.get("module_id") for item in sequence["sequence"]] != [item["id"] for item in modules]:
        raise HarnessError("Visual-carrier sequence must exactly match module order")
    if sequence["max_consecutive_static_packshots"] > 2:
        raise HarnessError("A sales page may not plan more than two consecutive static packshots")
    carriers = []
    static_run = max_static_run = 0
    for module, item in zip(modules, sequence["sequence"]):
        require_fields(item, ("module_id", "primary_visual_carrier", "expression_mode", "carrier_reason"), "visual-carrier sequence item")
        if "static_packshot_primary" not in item or not isinstance(item["static_packshot_primary"], bool):
            raise HarnessError("Visual-carrier sequence item requires a boolean static_packshot_primary")
        require_fields(module, ("story_function", "module_visual_expression_contract"), f"v0.9 module {module['id']}")
        for list_key in ("pain_ids", "value_bridge_ids"):
            if list_key not in module or not isinstance(module[list_key], list):
                raise HarnessError(f"Module {module['id']} requires list field {list_key}")
        if module["story_function"] not in STORY_FUNCTIONS:
            raise HarnessError(f"Module {module['id']} has an unsupported story function")
        if set(module["pain_ids"]) - known_pains or set(module["value_bridge_ids"]) - known_bridges:
            raise HarnessError(f"Module {module['id']} uses unknown pain or value-bridge IDs")
        expression = module["module_visual_expression_contract"]
        require_fields(expression, ("expression_mode", "claim_visualizability", "primary_visual_carrier", "visual_scene_sentence", "visual_semantic_payload", "required_visual_relations", "copy_responsibility", "forbidden_substitutes"), f"visual expression contract for {module['id']}")
        if "copy_only_justification" not in expression or not isinstance(expression["copy_only_justification"], str):
            raise HarnessError(f"Module {module['id']} requires copy_only_justification, using an empty string when not applicable")
        carrier = expression["primary_visual_carrier"]
        if carrier not in VISUAL_CARRIERS or carrier != item["primary_visual_carrier"] or expression["expression_mode"] != item["expression_mode"]:
            raise HarnessError(f"Module {module['id']} visual carrier sequence does not match its expression contract")
        if expression["expression_mode"] == "COPY_ONLY_JUSTIFIED":
            if expression["claim_visualizability"] != "TEXT_VERIFIABLE_ONLY" or len(str(expression["copy_only_justification"]).strip()) < 20:
                raise HarnessError(f"Module {module['id']} cannot use copy-only expression for a visualizable claim")
        elif expression["claim_visualizability"] not in ("VISUALIZABLE", "PARTIALLY_VISUALIZABLE", "TEXT_VERIFIABLE_ONLY"):
            raise HarnessError(f"Module {module['id']} has an unsupported claim-visualizability class")
        if not expression["required_visual_relations"] or len(set(expression["forbidden_substitutes"])) < 5:
            raise HarnessError(f"Module {module['id']} lacks semantic visual relations or anti-substitution rules")
        if module["story_function"] in ("PAIN_ARTICULATION", "PAIN_SOLUTION_COMBINED"):
            if not module["pain_ids"] or carrier not in ("PAIN_SCENE", "STATE_CONTRAST", "ACTION_CONTACT"):
                raise HarnessError(f"Module {module['id']} must make pain visible through a pain, contrast or action carrier")
        carriers.append(carrier)
        static_run = static_run + 1 if item["static_packshot_primary"] else 0
        max_static_run = max(max_static_run, static_run)
    minimum = 1 if len(modules) == 1 else 2 if len(modules) < 6 else 3 if len(modules) < 8 else 4
    if sequence["minimum_carrier_classes"] < minimum or len(set(carriers)) < sequence["minimum_carrier_classes"]:
        raise HarnessError("The page does not plan enough distinct visual carrier classes")
    if max_static_run > sequence["max_consecutive_static_packshots"]:
        raise HarnessError("The planned carrier sequence exceeds the static-packshot repetition limit")


