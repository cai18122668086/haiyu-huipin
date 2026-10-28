from __future__ import annotations

from .state import HarnessError, require_fields


SUBJECT_CLASSES = {
    "COMPLETE_PRODUCT", "PRODUCT_DETAIL", "HUMAN", "BODY_OR_SKIN",
    "INGREDIENT_OR_MATERIAL", "MECHANISM", "ACTION", "RESULT_STATE",
    "DOCUMENT_EVIDENCE", "SPECIFICATION_INFORMATION", "ENVIRONMENT_SCENE",
}

PRESENTATION_FORMS = {
    "HERO_POSTER", "MULTI_PAIN_ARRAY", "WHOLE_PLUS_MACRO", "EXPLODED_STRUCTURE",
    "PROCESS_SEQUENCE", "SOURCE_ACTION_RESULT", "STATE_CONTRAST", "USE_SCOPE_MATRIX",
    "DOCUMENTARY_EVIDENCE", "SPECIFICATION_BOARD", "FAQ_BOARD", "ASYMMETRIC_EDITORIAL",
}

REQUIRED_ROUTE_MATCHERS = {
    "HUMAN_USE_ACTION": lambda subjects, forms: bool({"HUMAN", "BODY_OR_SKIN", "ACTION"} & subjects),
    "PRODUCT_DETAIL_OR_STRUCTURE": lambda subjects, forms: bool({"PRODUCT_DETAIL", "MECHANISM"} & subjects) or bool({"WHOLE_PLUS_MACRO", "EXPLODED_STRUCTURE"} & forms),
    "MATERIAL_OR_INGREDIENT": lambda subjects, forms: "INGREDIENT_OR_MATERIAL" in subjects,
    "MECHANISM_OR_PROCESS": lambda subjects, forms: "MECHANISM" in subjects or bool({"PROCESS_SEQUENCE", "SOURCE_ACTION_RESULT", "EXPLODED_STRUCTURE"} & forms),
    "DOCUMENT_OR_SPECIFICATION": lambda subjects, forms: bool({"DOCUMENT_EVIDENCE", "SPECIFICATION_INFORMATION"} & subjects) or bool({"DOCUMENTARY_EVIDENCE", "SPECIFICATION_BOARD", "FAQ_BOARD"} & forms),
}

COMPLETE_PRODUCT_ROLES = {"DOMINANT", "SUPPORTING", "IDENTITY_TOKEN_ONLY", "ABSENT"}
CONTENT_UNIT_TYPES = {
    "LEAD_CONCLUSION", "HOW_IT_CONNECTS", "BUYER_BENEFIT", "EXPERIENCE_RESULT",
    "OBJECT_LABEL", "PARAMETER", "USE_CONDITION", "QUALIFIER", "SELECTION_HELP", "TRUST_FACT",
}


def _thresholds(module_count: int) -> tuple[int, int, int]:
    if module_count >= 12:
        return 6, 7, 4
    if module_count >= 10:
        return 5, 6, 3
    if module_count >= 8:
        return 4, 5, 3
    # Test and compatibility plans below the production 8-screen floor.
    return min(3, module_count), min(3, module_count), max(1, module_count)


def _meaningful_cast_nodes(cast: dict) -> list[str]:
    return [
        str(cast.get(key, "")).strip()
        for key in ("hero_subject", "claim_object", "action_subject", "result_subject", "context_subject")
        if str(cast.get(key, "")).strip()
    ]


def validate_campaign_expression_diversity(plan: dict, fact_ids: set[str]) -> None:
    """Reject campaigns that are structurally different on paper but visually repeat one packshot."""
    if plan.get("expression_contract_version") != "4.3":
        raise HarnessError("Current plans must declare expression_contract_version=4.3")
    contract = plan.get("campaign_expression_diversity_contract")
    if not isinstance(contract, dict):
        raise HarnessError("v4.3 requires campaign_expression_diversity_contract")
    require_fields(
        contract,
        (
            "minimum_subject_classes", "minimum_presentation_forms",
            "maximum_complete_product_dominant_screens", "maximum_consecutive_same_signature",
            "required_when_evidence_available",
        ),
        "campaign expression diversity contract",
    )
    modules = plan.get("modules") or []
    expected_subjects, expected_forms, expected_product_max = _thresholds(len(modules))
    if contract["minimum_subject_classes"] < expected_subjects:
        raise HarnessError("Campaign plans too few primary visual-subject classes for its screen count")
    if contract["minimum_presentation_forms"] < expected_forms:
        raise HarnessError("Campaign plans too few presentation forms for its screen count")
    if contract["maximum_complete_product_dominant_screens"] > expected_product_max:
        raise HarnessError("Complete-product dominance budget is too high for a detail-page campaign")
    if contract["maximum_consecutive_same_signature"] != 1:
        raise HarnessError("Adjacent modules may not repeat the same subject/form/camera/product-role signature")

    subject_classes: list[str] = []
    presentation_forms: list[str] = []
    signatures: list[str] = []
    dominant_product_modules: list[str] = []
    known_modules = {item.get("id") for item in modules}
    formula_entries: dict[str, list[dict]] = {}
    for item in (plan.get("formula_split_contract") or {}).get("entries", []):
        formula_entries.setdefault(item.get("dedicated_module_id"), []).append(item)

    for module in modules:
        if module.get("expression_contract_version") != "4.3":
            raise HarnessError(f"Module {module.get('id', '?')} must inherit expression_contract_version=4.3")
        require_fields(
            module,
            ("primary_subject_class", "secondary_subject_classes", "presentation_form",
             "complete_product_role", "expression_signature", "visual_cast", "content_unit_contract"),
            f"v4.3 expression fields for {module.get('id', '?')}",
        )
        subject_class = module["primary_subject_class"]
        presentation_form = module["presentation_form"]
        product_role = module["complete_product_role"]
        if subject_class not in SUBJECT_CLASSES:
            raise HarnessError(f"Module {module['id']} has an unsupported primary_subject_class")
        if presentation_form not in PRESENTATION_FORMS:
            raise HarnessError(f"Module {module['id']} has an unsupported presentation_form")
        if product_role not in COMPLETE_PRODUCT_ROLES:
            raise HarnessError(f"Module {module['id']} has an unsupported complete_product_role")
        if any(value not in SUBJECT_CLASSES for value in module["secondary_subject_classes"]):
            raise HarnessError(f"Module {module['id']} has an unsupported secondary subject class")

        expected_signature = f"{subject_class}+{presentation_form}+{module['camera_scale']}+{product_role}"
        if module["expression_signature"] != expected_signature:
            raise HarnessError(f"Module {module['id']} expression_signature does not match its authored expression fields")
        if signatures and signatures[-1] == expected_signature:
            raise HarnessError("Adjacent modules repeat the same visual-expression signature")

        presence = (module.get("module_visual_expression_contract") or {}).get("product_presence_policy")
        role_to_policy = {
            "DOMINANT": {"REQUIRED_DOMINANT"},
            "SUPPORTING": {"REQUIRED_SUPPORTING", "OPTIONAL"},
            "IDENTITY_TOKEN_ONLY": {"IDENTITY_TOKEN_ONLY"},
            "ABSENT": {"ABSENT_ALLOWED", "ABSENT_PREFERRED"},
        }
        if presence not in role_to_policy[product_role]:
            raise HarnessError(f"Module {module['id']} complete-product role conflicts with product-presence policy")
        if product_role == "DOMINANT":
            dominant_product_modules.append(module["id"])
        if module["order"] not in (1, len(modules)) and subject_class == "COMPLETE_PRODUCT" and presentation_form == "HERO_POSTER":
            raise HarnessError(f"Module {module['id']} repeats a complete-product hero poster outside the opening or closure")

        cast = module["visual_cast"]
        cast_fields = ("hero_subject", "claim_object", "action_subject", "result_subject", "context_subject", "product_identity_anchor")
        missing_cast_fields = [name for name in cast_fields if name not in cast]
        if missing_cast_fields:
            raise HarnessError(f"visual cast for {module['id']} missing required fields: {', '.join(missing_cast_fields)}")
        if not str(cast["hero_subject"]).strip() or not str(cast["product_identity_anchor"]).strip():
            raise HarnessError(f"Module {module['id']} visual cast needs a hero subject and product identity anchor")
        nodes = _meaningful_cast_nodes(cast)
        if not module.get("breathing_screen") and len(set(nodes)) < 3:
            raise HarnessError(f"Module {module['id']} needs at least three distinct semantic visual-cast nodes")
        if product_role == "ABSENT" and any("完整商品" in node or "完整产品" in node or "完整包装" in node for node in nodes):
            raise HarnessError(f"Module {module['id']} forbids the complete product but lists it in the visual cast")

        unit_contract = module["content_unit_contract"]
        require_fields(unit_contract, ("units", "density_class", "unit_count"), f"content-unit contract for {module['id']}")
        units = unit_contract["units"]
        if unit_contract["unit_count"] != len(units):
            raise HarnessError(f"Module {module['id']} content-unit count is inconsistent")
        if module.get("breathing_screen"):
            minimum, maximum = 0, 2
        elif module.get("story_function") in {"HOOK", "USE_OR_RESULT", "PURCHASE_CLOSURE"}:
            minimum, maximum = 2, 4
        else:
            minimum, maximum = 3, 5
        if not minimum <= len(units) <= maximum:
            raise HarnessError(f"Module {module['id']} requires {minimum}-{maximum} meaningful content units for its role")
        expected_density = (
            "BREATHING" if module.get("breathing_screen") else
            "COMPACT" if len(units) <= 2 else
            "DETAIL" if len(units) <= 4 else
            "DENSE_INFORMATION"
        )
        if unit_contract["density_class"] != expected_density:
            raise HarnessError(f"Module {module['id']} content-unit density class does not match its visible unit count")
        seen_text = set()
        visible_copy_texts = {str(item.get("text", "")).strip() for item in module.get("visible_copy", [])}
        for unit in units:
            require_fields(unit, ("type", "text", "fact_ids", "visual_anchor"), f"content unit for {module['id']}")
            if unit["type"] not in CONTENT_UNIT_TYPES or not str(unit["text"]).strip() or not str(unit["visual_anchor"]).strip():
                raise HarnessError(f"Module {module['id']} has an invalid content unit")
            if set(unit["fact_ids"]) - fact_ids:
                raise HarnessError(f"Module {module['id']} content unit uses unknown facts")
            normalized = "".join(str(unit["text"]).split())
            if normalized in seen_text:
                raise HarnessError(f"Module {module['id']} repeats one content unit in different styling")
            if str(unit["text"]).strip() not in visible_copy_texts:
                raise HarnessError(f"Module {module['id']} content units must come from the approved visible-copy whitelist")
            seen_text.add(normalized)
        if {str(unit["text"]).strip() for unit in units} != visible_copy_texts:
            raise HarnessError(f"Module {module['id']} visible copy and content-unit contract must account for each other exactly")

        module_formula_entries = formula_entries.get(module["id"], [])
        if module_formula_entries:
            if subject_class != "INGREDIENT_OR_MATERIAL":
                raise HarnessError(f"Formula module {module['id']} must make ingredient/material the primary subject class")
            if presentation_form not in {"WHOLE_PLUS_MACRO", "SOURCE_ACTION_RESULT", "PROCESS_SEQUENCE"}:
                raise HarnessError(f"Formula module {module['id']} must use a macro, pathway or process presentation")
            if product_role == "DOMINANT":
                raise HarnessError(f"Formula module {module['id']} may not let the complete packshot dominate the ingredient")
            for formula_entry in module_formula_entries:
                ingredient_name = str(formula_entry.get("formula_or_element", "")).strip()
                if ingredient_name and not any(ingredient_name in str(unit["text"]) for unit in units):
                    raise HarnessError(f"Formula module {module['id']} must visibly name {ingredient_name}")

        subject_classes.append(subject_class)
        presentation_forms.append(presentation_form)
        signatures.append(expected_signature)

    if len(set(subject_classes)) < contract["minimum_subject_classes"]:
        raise HarnessError("Campaign primary-subject diversity is below the declared minimum")
    if len(set(presentation_forms)) < contract["minimum_presentation_forms"]:
        raise HarnessError("Campaign presentation-form diversity is below the declared minimum")
    if len(dominant_product_modules) > contract["maximum_complete_product_dominant_screens"]:
        raise HarnessError("Too many modules make the complete product the first visual subject")

    required_routes = contract["required_when_evidence_available"]
    if not isinstance(required_routes, list) or len(set(required_routes)) != len(required_routes):
        raise HarnessError("required_when_evidence_available must be a unique list")
    unsupported_routes = set(required_routes) - set(REQUIRED_ROUTE_MATCHERS)
    if unsupported_routes:
        raise HarnessError(f"Campaign expression contract uses unsupported required routes: {sorted(unsupported_routes)}")
    actual_subjects = set(subject_classes)
    actual_forms = set(presentation_forms)
    missing_routes = [
        route for route in required_routes
        if not REQUIRED_ROUTE_MATCHERS[route](actual_subjects, actual_forms)
    ]
    if missing_routes:
        raise HarnessError(f"Evidence-available expression routes are not scheduled: {missing_routes}")
    if set(formula_entries) - known_modules:
        raise HarnessError("Formula expression routes refer to unknown modules")


def validate_observed_expression_diversity(audit: dict, plan: dict) -> None:
    """Bind the long-page diversity decision to observed pixels rather than the approved plan."""
    if plan.get("expression_contract_version") != "4.3":
        return
    observed = audit.get("expression_diversity_audit")
    if not isinstance(observed, dict):
        raise HarnessError("v4.3 long-page audit requires expression_diversity_audit")
    require_fields(
        observed,
        ("module_observations", "distinct_subject_classes", "distinct_presentation_forms",
         "complete_product_dominant_module_ids", "maximum_consecutive_same_signature", "actual_pixels_pass"),
        "observed expression diversity audit",
    )
    modules = plan.get("modules") or []
    rows = observed["module_observations"]
    if [item.get("module_id") for item in rows] != [item.get("id") for item in modules]:
        raise HarnessError("Expression-diversity audit must follow the approved module order")
    subjects = []
    forms = []
    signatures = []
    dominant = []
    for row in rows:
        require_fields(
            row,
            ("module_id", "primary_subject_class", "presentation_form", "camera_scale",
             "complete_product_role", "expression_signature", "visible_evidence"),
            "observed module expression",
        )
        signature = f"{row['primary_subject_class']}+{row['presentation_form']}+{row['camera_scale']}+{row['complete_product_role']}"
        if row["expression_signature"] != signature or not str(row["visible_evidence"]).strip():
            raise HarnessError("Observed expression signature or evidence is incomplete")
        subjects.append(row["primary_subject_class"])
        forms.append(row["presentation_form"])
        signatures.append(signature)
        if row["complete_product_role"] == "DOMINANT":
            dominant.append(row["module_id"])
    actual_run = 1
    maximum_run = 1
    for left, right in zip(signatures, signatures[1:]):
        actual_run = actual_run + 1 if left == right else 1
        maximum_run = max(maximum_run, actual_run)
    if observed["distinct_subject_classes"] != len(set(subjects)) or observed["distinct_presentation_forms"] != len(set(forms)):
        raise HarnessError("Observed expression-diversity counts do not match module observations")
    if observed["complete_product_dominant_module_ids"] != dominant or observed["maximum_consecutive_same_signature"] != maximum_run:
        raise HarnessError("Observed product dominance or repetition count is inconsistent")
    contract = plan["campaign_expression_diversity_contract"]
    passes = (
        len(set(subjects)) >= contract["minimum_subject_classes"]
        and len(set(forms)) >= contract["minimum_presentation_forms"]
        and len(dominant) <= contract["maximum_complete_product_dominant_screens"]
        and maximum_run <= contract["maximum_consecutive_same_signature"]
    )
    if observed["actual_pixels_pass"] is not passes:
        raise HarnessError("actual_pixels_pass does not match observed expression diversity")
    if not passes:
        raise HarnessError("The assembled page regresses to repeated subjects or presentation forms")
