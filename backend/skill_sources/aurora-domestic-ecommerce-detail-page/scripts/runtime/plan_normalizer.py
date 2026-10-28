from __future__ import annotations

import copy

from .state import HarnessError, require_fields


COMPACT_PLAN_FORMAT = "COMPACT_V1_3"
COMPATIBLE_COMPACT_PLAN_FORMATS = {COMPACT_PLAN_FORMAT}

# These fields are consumed directly by the pixel-facing prompt compiler. Keep
# the contract in one runtime module so preflight, strict validation and prompt
# compilation fail with the same migration guidance instead of discovering one
# missing key at generation time.
STYLE_CONSTITUTION_FIELDS = (
    "visual_world",
    "color_and_light",
    "typography_and_graphics",
    "product_rendering",
    "rhythm",
)
LEGACY_STYLE_CONSTITUTION_FIELDS = (
    "palette",
    "typography",
    "graphics",
    "photography",
    "product_finish",
)


def style_constitution_errors(value: object) -> list[str]:
    """Return deterministic v4.1 visual-DNA contract errors.

    The compact schema historically accepted any object here, while the prompt
    compiler required five named strings. This helper lets every stage report
    the same actionable error before an approval packet can be created.
    """
    if not isinstance(value, dict):
        return [
            "style_constitution must be an object containing v4.1 fields: "
            + ", ".join(STYLE_CONSTITUTION_FIELDS)
        ]
    missing_or_blank = [
        field for field in STYLE_CONSTITUTION_FIELDS
        if not isinstance(value.get(field), str) or not value[field].strip()
    ]
    errors: list[str] = []
    if missing_or_blank:
        errors.append(
            "style_constitution missing or blank v4.1 fields: "
            + ", ".join(missing_or_blank)
        )
    legacy_present = [field for field in LEGACY_STYLE_CONSTITUTION_FIELDS if field in value]
    if legacy_present:
        errors.append(
            "style_constitution contains legacy fields ("
            + ", ".join(legacy_present)
            + "); rewrite them explicitly as the v4.1 fields and recompile before approval"
        )
    return errors

DEFAULT_FORBIDDEN_SUBSTITUTES = [
    "纯文字解释", "纯图标解释", "装饰替代证据", "通用商品摆拍", "等大卡片平铺",
]

READING_DIRECTION_BY_MODE = {
    "centered_hero_headline": "top_to_bottom",
    "offset_editorial_headline": "diagonal",
    "object_label": "object_to_result",
    "directional_annotation": "object_to_result",
    "comparison_caption": "comparison_scan",
    "step_sequence": "step_flow",
    "data_hierarchy": "top_to_bottom",
    "scene_caption": "scene_to_caption",
    "body_explainer": "top_to_bottom",
    "minimal_label": "object_to_result",
    "closing_summary": "object_to_result",
}


PRODUCT_PRESENCE_POLICIES = {
    "REQUIRED_DOMINANT", "REQUIRED_SUPPORTING", "IDENTITY_TOKEN_ONLY",
    "OPTIONAL", "ABSENT_ALLOWED", "ABSENT_PREFERRED",
}

CONTINUITY_METHODS = {
    "EDGE_BLEED", "PRODUCT_OVERFLOW", "BACKGROUND_STRINGING", "PERSPECTIVE_TURN",
    "DECORATIVE_HANDOFF", "GRAPHIC_OVERLAY", "SUBJECT_STATE_HANDOFF", "CHAPTER_BAND",
}

HARD_CONTINUITY_METHODS = {
    "EDGE_BLEED", "PRODUCT_OVERFLOW", "PERSPECTIVE_TURN", "SUBJECT_STATE_HANDOFF",
}
GENERIC_TRANSITION_PLACEHOLDERS = {
    "上一屏靠近分界的可见元素",
    "本屏靠近分界的对应区域",
    "位置、方向、尺度或运动关系在分界处可追踪",
    "一项可见视觉线索",
}


def _validate_authored_transition(raw: dict, authored: dict, method: str, order: int) -> None:
    """Require a real seam plan instead of allowing generic placeholder text."""
    if not authored:
        raise HarnessError(
            f"compact module {raw['id']} must explicitly declare transition_contract seam fields"
        )
    required = ["outgoing_anchor", "source_region_previous", "target_region_current", "handoff_geometry", "seam_carrier"]
    if order > 1:
        required.append("incoming_anchor")
    missing = [field for field in required if not str(authored.get(field, "")).strip()]
    if missing:
        raise HarnessError(
            f"compact module {raw['id']}.transition_contract missing concrete fields: {', '.join(missing)}"
        )
    for field in required:
        value = str(authored[field]).strip()
        if value in GENERIC_TRANSITION_PLACEHOLDERS:
            raise HarnessError(
                f"compact module {raw['id']}.transition_contract.{field} cannot be a generic placeholder"
            )
    if method in HARD_CONTINUITY_METHODS and authored.get("visible_overlap_required") is False:
        raise HarnessError(
            f"compact module {raw['id']} method {method} requires visible_overlap_required=true"
        )
    if method in HARD_CONTINUITY_METHODS and authored.get("continuity_strength") not in (None, "HARD"):
        raise HarnessError(
            f"compact module {raw['id']} method {method} requires continuity_strength=HARD"
        )


def _select_continuity_method(raw: dict, order: int, module_count: int) -> tuple[str, str]:
    """Choose a visual handoff from the module's evidence, not from a category template."""
    if order == 1:
        return "CHAPTER_BAND", "首屏建立系列入口，并向下一屏交付主视觉材质或动线"
    expression = raw.get("expression") or {}
    carrier = expression.get("primary_visual_carrier", "")
    archetype = raw.get("visual_archetype", "")
    story = raw.get("story_function", "")
    structure = raw.get("structure") or {}
    text = " ".join(str(x) for x in [carrier, archetype, story, structure.get("structural_relationship", "")]).lower()
    if any(k in text for k in ("边缘", "曲线", "材质延续", "屏边", "纹理")):
        return "EDGE_BLEED", "本屏存在可从屏边或材质纹理继续展开的明确视觉边缘"
    if carrier in ("STATE_CONTRAST", "PROCESS_PATH", "ACTION_CONTACT", "RESULT_SCENE") or any(k in text for k in ("结果", "过程", "状态", "作用")):
        return "SUBJECT_STATE_HANDOFF", "上一屏的问题对象或动线在本屏继续发生可见状态变化"
    if carrier in ("MATERIAL_MACRO", "SCALE_RELATION", "PRODUCT_DETAIL") or any(k in text for k in ("结构", "部件", "尺度", "俯视", "侧面")):
        return "PERSPECTIVE_TURN", "保留空间方向或尺度锚点，用换视角解释本屏对象关系"
    if carrier in ("PRODUCT_HERO", "PRODUCT_DETAIL", "SELECTION", "PURCHASE_CLOSURE") and order < module_count:
        return "PRODUCT_OVERFLOW", "商品轮廓适合自然跨越分界，向下一屏交付真实产品线索"
    if carrier in ("PAIN_SCENE", "USE_SCENE", "SENSORY_WORLD"):
        return "BACKGROUND_STRINGING", "沿用同一环境色温或材质世界，同时更换主体与区域结构"
    if carrier in ("DOCUMENT_EVIDENCE", "SPECIFICATION", "TRUST") or any(k in text for k in ("参数", "机制", "数据", "证据")):
        return "GRAPHIC_OVERLAY", "以共享图形系统承接信息关系，图形只做组织不冒充检测"
    if story in ("PAIN", "PAIN_TO_SOLUTION", "CHAPTER_TRANSITION") or order == module_count:
        return "CHAPTER_BAND", "用章节材质或色带完成叙事转场并保留系列DNA"
    return "DECORATIVE_HANDOFF", "无安全实体线索时以轻量装饰动线接力，不承担核心卖点"


def _derive_product_presence_policy(raw: dict, expression: dict, order: int, module_count: int) -> str:
    """Route product presence by the screen's buyer job, not a blanket packshot default."""
    decision = raw.get("product_presence_decision") or {}
    explicit = decision.get("policy") or expression.get("product_presence_policy")
    if explicit:
        if explicit not in PRODUCT_PRESENCE_POLICIES:
            raise HarnessError(f"Unsupported product presence policy for {raw.get('id')}: {explicit}")
        return explicit
    if order in (1, module_count):
        return "REQUIRED_DOMINANT"
    archetype = raw.get("visual_archetype", "BENEFIT_OVERVIEW")
    carrier = expression.get("primary_visual_carrier")
    if archetype in ("SELECTION_COMPARISON", "PURCHASE_CLOSURE") or carrier in (
        "PRODUCT_HERO", "PRODUCT_DETAIL", "SELECTION", "SPECIFICATION", "PURCHASE_CLOSURE",
    ):
        return "REQUIRED_DOMINANT"
    if archetype in ("WHOLE_PLUS_MACRO", "ACTION_SEQUENCE", "PAIN_TO_SOLUTION_BRIDGE"):
        return "REQUIRED_SUPPORTING"
    if archetype == "USE_SCOPE_MATRIX":
        return "IDENTITY_TOKEN_ONLY"
    if archetype in (
        "PAIN_SCENE", "DEDICATED_BENEFIT_PROOF", "SOURCE_ACTION_RESULT",
        "STATE_CONTRAST", "SENSORY_WORLD", "TRUST_EVIDENCE",
    ):
        return "ABSENT_PREFERRED"
    if raw.get("story_function") in ("MECHANISM_PROOF", "MATERIAL_PROOF", "TRUST_OR_RISK_REMOVAL"):
        return "ABSENT_PREFERRED"
    return "REQUIRED_SUPPORTING"


def _derive_product_presence_decision(raw: dict, proof: dict, policy: str) -> dict:
    authored = copy.deepcopy(raw.get("product_presence_decision") or {})
    anchors = copy.deepcopy(authored.get("identity_binding_without_product") or proof["visual_specificity_anchor"])
    if not isinstance(anchors, list):
        anchors = [str(anchors)]
    absence_favored = policy in ("IDENTITY_TOKEN_ONLY", "ABSENT_ALLOWED", "ABSENT_PREFERRED")
    return {
        "policy": policy,
        "unique_job_if_present": authored.get(
            "unique_job_if_present",
            "建立或完成商品识别与购买判断" if policy == "REQUIRED_DOMINANT"
            else "连接使用动作与当前商品" if policy == "REQUIRED_SUPPORTING"
            else "NONE",
        ),
        "primary_subject_without_product": authored.get("primary_subject_without_product", proof["primary_subject"]),
        "identity_binding_without_product": anchors,
        "absence_reason": authored.get(
            "absence_reason",
            "让卖点对象、来源、过程、结果或场景承担主要视觉重量，避免重复包装露出" if absence_favored else "",
        ),
    }


def _copy_density(visible_copy: list, breathing: bool) -> str:
    count = len(visible_copy)
    if count == 0:
        return "none" if breathing else "low"
    return "low" if count <= 3 else "medium" if count <= 5 else "high"


def _require_list(value: object, label: str, *, allow_empty: bool = False) -> list:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise HarnessError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}list")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessError(f"{label} must be a non-empty string")
    return value.strip()


def _derive_output_strategy(session: dict, raw: dict, module_count: int) -> dict:
    profile = session["input"]["platform_profile"]
    required = (
        "module_count_rationale", "module_count_candidates", "selling_point_clusters",
        "merge_decisions", "claim_coverage", "dimension_rationale",
    )
    require_fields(raw, required, "compact output strategy")
    pair_rendering = copy.deepcopy(raw.get("continuous_pair_rendering", {}))
    if not isinstance(pair_rendering, dict):
        raise HarnessError("compact output strategy continuous_pair_rendering must be an object")
    pair_rendering.setdefault("enabled", True)
    pair_rendering.setdefault("grouping_mode", "TWO_SCREEN_PAIRS")
    pair_rendering.setdefault("pair_size", 2)
    pair_rendering.setdefault("pair_canvas_width_px", profile["canvas_width_px"])
    pair_rendering.setdefault("pair_canvas_height_px", profile["module_height_px"] * 2)
    pair_rendering.setdefault("pair_canvas_aspect_ratio", "3:8")
    pair_rendering.setdefault("pair_merge_type", "PAIR_PLAN_MERGE")
    pair_rendering.setdefault("decomposition_window_aspect_ratio", "3:4")
    pair_rendering.setdefault("pair_slice_height_px", profile["module_height_px"])
    pair_rendering.setdefault("pair_count", (module_count + 1) // 2)
    pair_rendering.setdefault("pair_to_pair_transition_tokens", [
        f"PAIR_BRIDGE_{index:02d}" for index in range(1, (module_count + 1) // 2)
    ])
    pair_rendering.setdefault("pair_ids", [
        {
            "pair_id": f"P{index:02d}",
            "module_ids": [f"M{index * 2 - 1:02d}", f"M{index * 2:02d}"],
            "internal_seam_zone": {"start_y": profile["module_height_px"] - 96, "end_y": profile["module_height_px"] + 96},
        }
        for index in range(1, (module_count + 1) // 2 + 1)
    ])
    for pair in pair_rendering.get("pair_ids", []):
        if isinstance(pair, dict):
            pair.setdefault("pair_merge_type", "PAIR_PLAN_MERGE")
    return {
        "target_platform": profile["target_platform"],
        "profile_name": profile["profile_name"],
        "selection_mode": profile["selection_mode"],
        "module_count": module_count,
        "module_count_rationale": raw["module_count_rationale"],
        "module_count_candidates": copy.deepcopy(raw["module_count_candidates"]),
        "selling_point_clusters": copy.deepcopy(raw["selling_point_clusters"]),
        "merge_decisions": copy.deepcopy(raw["merge_decisions"]),
        "claim_coverage": copy.deepcopy(raw["claim_coverage"]),
        **({"module_count_formula": copy.deepcopy(raw["module_count_formula"])} if "module_count_formula" in raw else {}),
        "aspect_ratio": copy.deepcopy(profile["aspect_ratio"]),
        "canvas_width_px": profile["canvas_width_px"],
        "module_height_px": profile["module_height_px"],
        "safe_margin_px": profile["safe_margin_px"],
        "slice_height_px": profile["slice_height_px"],
        "dimension_rationale": raw["dimension_rationale"],
        "uniform_dimensions": True,
        "verification_status": profile["verification_status"],
        "continuous_pair_rendering": pair_rendering,
    }


def validate_expression_signature(module: dict) -> None:
    expected = "+".join(str(module.get(key, "")) for key in ("primary_subject_class", "presentation_form", "camera_scale", "complete_product_role"))
    if module.get("expression_signature") != expected:
        raise HarnessError("Module expression_signature must be the exact authored subject+presentation+camera+product-role tuple")


def _derive_module(session: dict, raw: dict, order: int, module_count: int, plan_format: str) -> dict:
    require_fields(
        raw,
        (
            "id", "duty", "buyer_question", "lead_conclusion", "lead_claim_id",
            "selling_point_cluster_ids", "story_function",
            "visual_proof_direction", "visual_evidence", "proof", "expression", "structure",
            "visible_copy", "typography", "composition_family", "camera_scale",
            "variation_from_previous", "primary_subject_class", "secondary_subject_classes",
            "presentation_form", "complete_product_role", "expression_signature",
            "visual_cast", "content_unit_contract",
        ),
        "compact plan module",
    )
    validate_expression_signature(raw)
    for list_key in ("pain_ids", "value_bridge_ids"):
        _require_list(raw.get(list_key), f"compact plan module {list_key}", allow_empty=True)
    proof = raw["proof"]
    require_fields(
        proof,
        (
            "buyer_takeaway", "visual_verb", "primary_subject", "supporting_subjects",
            "required_relations", "minimum_visual_payload", "visual_specificity_anchor",
            "hidden_copy_test",
        ),
        f"compact proof for {raw['id']}",
    )
    expression = raw["expression"]
    require_fields(
        expression,
        (
            "expression_mode", "claim_visualizability", "primary_visual_carrier",
            "visual_scene_sentence", "copy_responsibility",
        ),
        f"compact expression for {raw['id']}",
    )
    typography = raw["typography"]
    require_fields(typography, ("primary_mode", "anchor_target", "anchor_zone"), f"compact typography for {raw['id']}")

    for key in ("supporting_subjects", "required_relations", "visual_specificity_anchor"):
        _require_list(proof.get(key), f"compact proof {raw['id']}.{key}")
    _require_list(raw.get("selling_point_cluster_ids"), f"compact module {raw['id']}.selling_point_cluster_ids")
    _require_list(raw.get("variation_from_previous"), f"compact module {raw['id']}.variation_from_previous")
    _require_list(raw.get("supporting_claim_ids", []), f"compact module {raw['id']}.supporting_claim_ids", allow_empty=True)
    _require_list(raw.get("inherited_dna_tokens", ["PALETTE", "PRODUCT_RENDERING"]), f"compact module {raw['id']}.inherited_dna_tokens")
    structure = raw["structure"]
    if not isinstance(structure, dict):
        raise HarnessError(f"compact module {raw['id']}.structure must be an object")
    _require_list(structure.get("zone_briefs"), f"compact module {raw['id']}.structure.zone_briefs")
    _require_list(structure.get("forbidden_fallbacks"), f"compact module {raw['id']}.structure.forbidden_fallbacks")
    for index, brief in enumerate(structure["zone_briefs"], start=1):
        _require_string(brief, f"compact module {raw['id']} zone brief {index}")
    breathing = bool(raw.get("breathing_screen", False))
    visible_copy = copy.deepcopy(_require_list(raw["visible_copy"], f"compact module {raw['id']}.visible_copy", allow_empty=True))
    for item in visible_copy:
        require_fields(item, ("text", "role", "anchor"), f"visible copy for {raw['id']}")
    visual_cast = raw["visual_cast"]
    cast_fields = ("hero_subject", "claim_object", "action_subject", "result_subject", "context_subject", "product_identity_anchor")
    missing_cast_fields = [name for name in cast_fields if name not in visual_cast]
    if missing_cast_fields:
        raise HarnessError(f"visual cast for {raw['id']} missing required fields: {', '.join(missing_cast_fields)}")
    _require_string(visual_cast["product_identity_anchor"], f"visual cast for {raw['id']}.product_identity_anchor")
    content_unit_contract = raw["content_unit_contract"]
    require_fields(content_unit_contract, ("units", "density_class", "unit_count"), f"content unit contract for {raw['id']}")
    _require_list(content_unit_contract.get("units"), f"compact module {raw['id']}.content_unit_contract.units", allow_empty=True)
    text_mode = typography["primary_mode"]
    anchor_zone = typography["anchor_zone"]
    reading_direction = typography.get("reading_direction") or READING_DIRECTION_BY_MODE.get(text_mode, "top_to_bottom")
    supporting_claim_ids = copy.deepcopy(raw.get("supporting_claim_ids", []))
    inherited_tokens = copy.deepcopy(raw.get("inherited_dna_tokens") or ["PALETTE", "PRODUCT_RENDERING"])
    product_output = session["input"]["platform_profile"]
    logo_source = (
        "NONE"
        if session.get("brand_mode") == "BRANDLESS"
        else session["identity"]["brand_logo_source"]["image_id"]
    )

    product_presence_policy = _derive_product_presence_policy(raw, expression, order, module_count)
    product_presence_decision = _derive_product_presence_decision(raw, proof, product_presence_policy)
    expression_contract = {
        "expression_mode": expression["expression_mode"],
        "claim_visualizability": expression["claim_visualizability"],
        "primary_visual_carrier": expression["primary_visual_carrier"],
        "visual_scene_sentence": expression["visual_scene_sentence"],
        "visual_semantic_payload": expression.get("visual_semantic_payload", proof["buyer_takeaway"]),
        "required_visual_relations": copy.deepcopy(expression.get("required_visual_relations") or proof["required_relations"]),
        "copy_responsibility": expression["copy_responsibility"],
        "copy_only_justification": expression.get("copy_only_justification", ""),
        "forbidden_substitutes": copy.deepcopy(expression.get("forbidden_substitutes") or DEFAULT_FORBIDDEN_SUBSTITUTES),
        "product_presence_policy": product_presence_policy,
        "evidence_subject_policy": expression.get("evidence_subject_policy", "PRODUCT_DOMINANT" if expression["primary_visual_carrier"] in ("PRODUCT_HERO", "PRODUCT_DETAIL", "SELECTION", "PURCHASE_CLOSURE") else "ACTION_RESULT_DOMINANT"),
        "visualization_mode": expression.get("visualization_mode", "DIRECT_OBSERVATION"),
        "transformation_required": bool(expression.get("transformation_required", expression["primary_visual_carrier"] in ("ACTION_CONTACT", "STATE_CONTRAST", "PROCESS_PATH", "RESULT_SCENE"))),
        "transformation_stages": copy.deepcopy(expression.get("transformation_stages") or []),
        "truth_boundary": expression.get("truth_boundary", "只呈现当前事实允许的对象、动作与结果，不伪装成实验、检测或真实显微观察"),
        "pain_expression": copy.deepcopy(expression.get("pain_expression")),
        "commercial_comfort": copy.deepcopy(expression.get("commercial_comfort")),
    }
    authored_transition = raw.get("transition_contract") or {}
    if not isinstance(authored_transition, dict):
        raise HarnessError(f"compact module {raw['id']}.transition_contract must be an object")
    selected_method, selection_basis = _select_continuity_method(raw, order, module_count)
    method = authored_transition.get("method_id", selected_method)
    if method not in CONTINUITY_METHODS:
        raise HarnessError(f"Unsupported transition_contract.method_id for {raw['id']}: {method}")
    _validate_authored_transition(raw, authored_transition, method, order)
    transition_contract = {
        "method_id": method,
        "selection_basis": authored_transition.get("selection_basis", selection_basis),
        "incoming_anchor": authored_transition.get("incoming_anchor", "建立本屏独有的视觉载体" if order == 1 else "沿用前屏的一项可见材质、动线或环境线索"),
        "outgoing_anchor": authored_transition.get("outgoing_anchor", "交付下一屏可继续变化的材质、动线或问题线索"),
        "mode": authored_transition.get("mode", "DECLARED_CHAPTER" if order == 1 else "CARRIER_HANDOFF"),
        "incoming_carrier": authored_transition.get("incoming_carrier", "建立本屏独有的视觉载体" if order == 1 else "沿用前屏的一项可见材质、动线或环境线索"),
        "outgoing_carrier": authored_transition.get("outgoing_carrier", "交付下一屏可继续变化的材质、动线或问题线索"),
        "seam_carrier": authored_transition["seam_carrier"],
        "spatial_direction": authored_transition.get("spatial_direction", "由本屏主视觉关系自然决定"),
        "shared_invariants": copy.deepcopy(authored_transition.get("shared_invariants") or ["campaign palette", "lighting/material atmosphere"]),
        "allowed_change": authored_transition.get("allowed_change", "改变主体状态、景别或空间结构，不复制前屏版式"),
        "visual_handoff_type": authored_transition.get("visual_handoff_type", {
            "EDGE_BLEED": "EDGE_CONTINUATION", "PRODUCT_OVERFLOW": "OBJECT_CROSSING",
            "BACKGROUND_STRINGING": "CONTINUOUS_CANVAS", "PERSPECTIVE_TURN": "PERSPECTIVE_CONTINUATION",
            "DECORATIVE_HANDOFF": "GRAPHIC_CONTINUATION", "GRAPHIC_OVERLAY": "GRAPHIC_CONTINUATION",
            "SUBJECT_STATE_HANDOFF": "STATE_CONTINUATION", "CHAPTER_BAND": "CHAPTER_BRIDGE",
        }.get(method, "VISIBLE_ELEMENT_HANDOFF")),
        "source_region_previous": authored_transition["source_region_previous"],
        "target_region_current": authored_transition["target_region_current"],
        "handoff_geometry": authored_transition["handoff_geometry"],
        "visible_overlap_required": bool(authored_transition.get("visible_overlap_required", method not in ("CHAPTER_BAND",))),
        "continuity_strength": authored_transition.get("continuity_strength", "HARD" if method in ("EDGE_BLEED", "PRODUCT_OVERFLOW", "PERSPECTIVE_TURN", "SUBJECT_STATE_HANDOFF") else "SOFT"),
    }
    visual_proof_contract = {
        "buyer_takeaway": proof["buyer_takeaway"],
        "visual_verb": proof["visual_verb"],
        "primary_subject": proof["primary_subject"],
        "supporting_subjects": copy.deepcopy(proof["supporting_subjects"]),
        "required_relations": copy.deepcopy(proof["required_relations"]),
        "minimum_visual_payload": proof["minimum_visual_payload"],
        "visual_specificity_anchor": proof["visual_specificity_anchor"],
        "allowed_metaphor": proof.get("allowed_metaphor", "NONE"),
        "hidden_copy_test": proof["hidden_copy_test"],
    }
    copy_bindings = [
        {"text": item["text"], "bound_to_visual": item["anchor"]}
        for item in visible_copy
    ]
    text_system = {
        "primary_mode": text_mode,
        "supporting_mode": typography.get("supporting_mode"),
        "anchor_target": typography["anchor_target"],
        "anchor_zone": anchor_zone,
        "reading_direction": reading_direction,
        "placement_logic": typography.get("placement_logic", f"文字绑定到{typography['anchor_target']}并顺应画面动线"),
        "hierarchy": typography.get("hierarchy", "商品证据优先，主文案次之，限定信息最低"),
        "max_lines": typography.get("max_lines", min(6, max(1, len(visible_copy) * 2))),
        "container_style": typography.get("container_style", "随场景留白或对象局部排布"),
        "negative_space_reservation": typography.get("negative_space_reservation", f"在{anchor_zone}预留可读空间"),
        "image_text_relationship": typography.get("image_text_relationship", "文案只命名、限定或组织画面已显示的意义"),
        "composition_flexibility": typography.get("composition_flexibility", "CONTROLLED_REFLOW"),
        "neighbor_difference": typography.get("neighbor_difference", raw["variation_from_previous"] or "首屏建立文字基线"),
    }
    return {
        "id": raw["id"],
        "authoring_plan_format": plan_format,
        "expression_contract_version": "4.3",
        "order": order,
        "duty": raw["duty"],
        "buyer_question": raw["buyer_question"],
        "lead_conclusion": raw["lead_conclusion"],
        "lead_claim_id": raw["lead_claim_id"],
        "information_role": raw.get("information_role", "OTHER"),
        "buyer_question_answered": raw.get("buyer_question_answered", raw["buyer_question"]),
        "new_information_added": copy.deepcopy(raw.get("new_information_added", [raw["lead_conclusion"]])),
        "depends_on_module_ids": copy.deepcopy(raw.get("depends_on_module_ids", [])),
        "repeats_module_ids": copy.deepcopy(raw.get("repeats_module_ids", [])),
        "decision_enabled": raw.get("decision_enabled", raw["buyer_question"]),
        "hero_entity_id": raw.get("hero_entity_id"),
        "co_equal_hero_entity_ids": copy.deepcopy(raw.get("co_equal_hero_entity_ids", [])),
        "supporting_claim_ids": supporting_claim_ids,
        "selling_point_cluster_ids": copy.deepcopy(raw["selling_point_cluster_ids"]),
        "reference_pattern_ids": copy.deepcopy(raw.get("reference_pattern_ids", [])),
        "story_function": raw["story_function"],
        "visual_archetype": raw.get("visual_archetype", "BENEFIT_OVERVIEW"),
        "pain_ids": copy.deepcopy(raw["pain_ids"]),
        "value_bridge_ids": copy.deepcopy(raw["value_bridge_ids"]),
        "visual_proof_direction": raw["visual_proof_direction"],
        "visual_evidence": copy.deepcopy(raw["visual_evidence"]),
        "visual_proof_contract": visual_proof_contract,
        "module_visual_expression_contract": expression_contract,
        "transition_contract": transition_contract,
        "product_presence_decision": product_presence_decision,
        "module_spatial_structure_contract": copy.deepcopy(raw["structure"]),
        "visible_copy": visible_copy,
        "copy_intent": {
            "policy": "VISUAL_FIRST_WITH_AUXILIARY_COPY",
            "why_text_is_needed": raw.get("why_text_is_needed", expression["copy_responsibility"]),
            "text_to_visual_bindings": copy_bindings,
            "hidden_copy_takeaway": proof["hidden_copy_test"],
            "copy_adds": expression["copy_responsibility"],
        },
        "composition_family": raw["composition_family"],
        "camera_scale": raw["camera_scale"],
        "primary_subject_class": raw["primary_subject_class"],
        "secondary_subject_classes": copy.deepcopy(raw["secondary_subject_classes"]),
        "presentation_form": raw["presentation_form"],
        "complete_product_role": raw["complete_product_role"],
        "expression_signature": raw["expression_signature"],
        "visual_cast": copy.deepcopy(visual_cast),
        "content_unit_contract": copy.deepcopy(content_unit_contract),
        "text_system": text_system,
        "reading_path": raw.get("reading_path", reading_direction),
        "copy_density": _copy_density(visible_copy, breathing),
        "visual_payload_density": raw.get("visual_payload_density", "medium" if breathing else "high"),
        "proof_relation_strength": raw.get("proof_relation_strength", "medium" if breathing else "strong"),
        "commercial_energy": raw.get("commercial_energy", "high" if order in (1, module_count) else "standard"),
        "breathing_screen": breathing,
        "brand_anchor": raw.get(
            "brand_anchor",
            (
                "白牌产品识别锚点：商品主体+真实品类字样+视觉DNA，非Logo"
                if order == 1
                else "白牌产品本体自然保留，无品牌标识"
            )
            if logo_source == "NONE"
            else ("首屏独立品牌锚点" if order == 1 else "包装印刷品牌自然保留"),
        ),
        "independent_logo_policy": "FORBIDDEN" if (order != 1 or logo_source == "NONE") else "REQUIRED_IF_AUTHORIZED",
        "independent_logo_source_id": logo_source if order == 1 else "NONE",
        "inherited_dna_tokens": inherited_tokens,
        "variation_from_previous": raw["variation_from_previous"],
        "static_packshot_primary": bool(raw.get("static_packshot_primary", False)),
        "output": {
            "width_px": product_output["canvas_width_px"],
            "height_px": product_output["module_height_px"],
            "safe_margin_px": product_output["safe_margin_px"],
        },
    }


def normalize_plan(session: dict, raw_plan: dict) -> dict:
    """Expand a v4.3 compact authoring plan into the runtime contract."""
    if raw_plan.get("plan_format") == "WEBSITE_DIRECT_V2":
        from .website_plan import compile_native

        return compile_native(session, raw_plan)
    if raw_plan.get("plan_format") not in COMPATIBLE_COMPACT_PLAN_FORMATS:
        if str(raw_plan.get("plan_format", "")).startswith("COMPACT_"):
            raise HarnessError("Only COMPACT_V1_3 is supported by Skill v4.3; recompile the plan")
        # Full plans are already expanded runtime contracts; their v4.1 fields
        # are validated by harness.validate_plan when present.
        return copy.deepcopy(raw_plan)

    # Missing planning_mode is treated as the ordinary lightweight path. The
    # route-aware ledgers are opt-in, so an omitted flag cannot accidentally
    # expand a normal detail-page plan into a large strategy dossier.
    planning_mode = raw_plan.get("planning_mode", "LIGHTWEIGHT_V41")
    # v4.1 plans opt into information-architecture enforcement explicitly.
    # v4.1 compact plans are normalized into the single current runtime contract.
    information_v41 = True
    require_fields(raw_plan, ("information_contract_version", "expression_contract_version", "hero_screen_contract", "detail_information_contract", "formula_split_contract", "campaign_expression_diversity_contract"), "v4.3 information and expression architecture contract")
    if raw_plan.get("information_contract_version") != "4.1":
        raise HarnessError("v4.1 plans must declare information_contract_version=4.1")
    if raw_plan.get("expression_contract_version") != "4.3":
        raise HarnessError("v4.3 plans must declare expression_contract_version=4.3")
    lightweight = planning_mode == "LIGHTWEIGHT_V41"
    if lightweight:
        require_fields(raw_plan, ("campaign_thesis", "dominant_decision_type", "output_strategy", "reference_expression_system", "style_constitution", "series_visual_dna", "modules"), "lightweight compact plan")
        facts = session.get("input", {}).get("verified_facts", [])
        fact_ids = [item.get("id") for item in facts if item.get("id")]
        module_ids = [item.get("id") for item in raw_plan.get("modules", []) if item.get("id")]
        if len(module_ids) not in {8, 10, 12}:
            raise HarnessError("v4.3 compact plans must contain exactly 8, 10, or 12 modules")
        strategy = raw_plan["output_strategy"]
        strategy.setdefault("module_count_rationale", "按核心卖点、使用场景与购买收口组织屏幕，保持信息密度可读。")
        strategy.setdefault("module_count_candidates", [{
            "candidate_mode": "DIRECT_DERIVATION", "count": len(module_ids),
            "coverage_tradeoff": "已按卖点深度直接确定",
            "selling_point_fit": "覆盖主要购买问题与独立证明职责",
            "story_obligation_fit": "保留开场、证明、场景与购买收口",
            "primary_selling_point_depth_fit": "每个主卖点均有独立视觉证明或明确收口",
            "evidence_boundary_fit": "不超出已提供事实与证据边界",
            "decision": "SELECTED",
        }])
        strategy.setdefault("selling_point_clusters", [{"id": f"SP{index:02d}", "buyer_question": "核心购买判断", "fact_ids": [fact_id], "purchase_role": "BENEFIT", "separation_policy": "MERGEABLE", "assigned_module_ids": module_ids[:1] or ["M01"]} for index, fact_id in enumerate(fact_ids, start=1)])
        strategy.setdefault("merge_decisions", ["将可由同一画面证明的事实合并表达"])
        strategy.setdefault("claim_coverage", [{"claim_id": fact_id, "disposition": "LEAD", "module_ids": module_ids[:1] or ["M01"], "rationale": "核心事实直接进入屏幕", "defer_reason_code": "NOT_DEFERRED"} for fact_id in fact_ids])
        strategy.setdefault("dimension_rationale", "沿用平台工作母版尺寸，便于连续双屏生成、固定切片和最终装配。")
        raw_plan.setdefault("operating_strategy", {})
        raw_plan.setdefault("campaign_recall_contract", {})
        raw_plan.setdefault("selling_point_depth_contract", {})
        raw_plan.setdefault("campaign_pain_solution_contract", {})
    else:
        require_fields(
            raw_plan,
            (
                "campaign_thesis", "dominant_decision_type", "operating_strategy",
                "campaign_recall_contract", "selling_point_depth_contract",
                "campaign_pain_solution_contract", "output_strategy",
                "reference_expression_system", "style_constitution", "series_visual_dna", "modules",
            ),
            "compact route-aware plan",
        )
    if raw_plan["plan_format"] == COMPACT_PLAN_FORMAT:
        if "user_selling_point_coverage_ledger" not in raw_plan or not isinstance(raw_plan["user_selling_point_coverage_ledger"], list):
            raise HarnessError("compact v1.3 plan requires a user_selling_point_coverage_ledger list; it may be empty for photo-only routing")
        require_fields(raw_plan["output_strategy"], ("module_count_formula",), "compact v1.3 output strategy")
    if not raw_plan["modules"]:
        raise HarnessError("A compact detail plan requires at least one module")
    if len(raw_plan["modules"]) not in {8, 10, 12}:
        raise HarnessError("v4.3 compact plans must contain exactly 8, 10, or 12 modules")
    output_strategy = _derive_output_strategy(session, raw_plan["output_strategy"], len(raw_plan["modules"]))
    modules = [
        _derive_module(session, item, order=index, module_count=len(raw_plan["modules"]), plan_format=raw_plan["plan_format"])
        for index, item in enumerate(raw_plan["modules"], start=1)
    ]
    carriers = [item["module_visual_expression_contract"]["primary_visual_carrier"] for item in modules]
    minimum_carriers = 1 if len(modules) == 1 else 2 if len(modules) < 6 else 3 if len(modules) < 8 else 4
    presence_sequence = [
        {
            "module_id": item["id"],
            "policy": item["product_presence_decision"]["policy"],
            "unique_job_if_present": item["product_presence_decision"]["unique_job_if_present"],
            "primary_subject_without_product": item["product_presence_decision"]["primary_subject_without_product"],
            "identity_binding_without_product": copy.deepcopy(item["product_presence_decision"]["identity_binding_without_product"]),
            "absence_reason": item["product_presence_decision"]["absence_reason"],
        }
        for item in modules
    ]
    website = raw_plan.get("website_contract_version")
    if website is not None:
        if website != "WEBSITE_LEAN_V1":
            raise HarnessError("Unknown website contract version")
        from .website_review import validate_plan_source
        validate_plan_source(raw_plan)
        for item in modules:
            item["website_contract_version"] = website
    return {
        **({"website_contract_version": website, "claim_coverage": copy.deepcopy(raw_plan["claim_coverage"])} if website is not None else {}),
        "plan_format": raw_plan["plan_format"],
        "planning_mode": "LIGHTWEIGHT_V41" if information_v41 else ("LIGHTWEIGHT_V41" if lightweight else raw_plan.get("planning_mode", "STRICT_ROUTE_AWARE")),
        "information_contract_version": raw_plan.get("information_contract_version", "4.1" if information_v41 else None),
        "expression_contract_version": raw_plan["expression_contract_version"],
        **({"hero_screen_contract": copy.deepcopy(raw_plan["hero_screen_contract"]), "detail_information_contract": copy.deepcopy(raw_plan["detail_information_contract"])} if information_v41 and "hero_screen_contract" in raw_plan and "detail_information_contract" in raw_plan else {}),
        "detail_page_information_sufficiency": copy.deepcopy(raw_plan.get("detail_page_information_sufficiency", {})),
        "campaign_thesis": raw_plan["campaign_thesis"],
        "operating_strategy": copy.deepcopy(raw_plan["operating_strategy"]),
        "campaign_recall_contract": copy.deepcopy(raw_plan["campaign_recall_contract"]),
        "dominant_decision_type": raw_plan["dominant_decision_type"],
        "supporting_decision_types": copy.deepcopy(raw_plan.get("supporting_decision_types", [])),
        "buyer_doubts": copy.deepcopy(raw_plan.get("buyer_doubts", [])),
        "output_strategy": output_strategy,
        "selling_point_depth_contract": copy.deepcopy(raw_plan["selling_point_depth_contract"]),
        "formula_split_contract": copy.deepcopy(raw_plan["formula_split_contract"]),
        "campaign_expression_diversity_contract": copy.deepcopy(raw_plan["campaign_expression_diversity_contract"]),
        "user_selling_point_coverage_ledger": copy.deepcopy(raw_plan.get("user_selling_point_coverage_ledger", [])),
        "campaign_pain_solution_contract": copy.deepcopy(raw_plan["campaign_pain_solution_contract"]),
        "campaign_visual_carrier_sequence": {
            "sequence": [
                {
                    "module_id": item["id"],
                    "primary_visual_carrier": item["module_visual_expression_contract"]["primary_visual_carrier"],
                    "static_packshot_primary": item["static_packshot_primary"],
                    "expression_mode": item["module_visual_expression_contract"]["expression_mode"],
                    "carrier_reason": f"{item['duty']}使用{item['module_visual_expression_contract']['primary_visual_carrier']}承载本屏主结论并避免海报化退化。",
                }
                for item in modules
            ],
            "max_consecutive_static_packshots": 2,
            "minimum_carrier_classes": minimum_carriers,
            "campaign_rationale": "视觉载体由逐屏职责派生，保证痛点、动作、证据、材质、选择和收口之间存在可见变化。",
        },
        "campaign_product_presence_rhythm": {
            "sequence": presence_sequence,
            "full_product_return_module_ids": [
                item["module_id"] for item in presence_sequence
                if item["policy"] in ("REQUIRED_DOMINANT", "REQUIRED_SUPPORTING")
            ],
            "absence_favored_module_ids": [
                item["module_id"] for item in presence_sequence
                if item["policy"] in ("IDENTITY_TOKEN_ONLY", "ABSENT_ALLOWED", "ABSENT_PREFERRED")
            ],
            "campaign_rationale": "商品在建立识别、参与动作、选择判断和购买收口时回归；其余页面优先让来源、材质、机制、结果、场景或信任证据成为主角。",
        },
        "reference_expression_system": copy.deepcopy(raw_plan["reference_expression_system"]),
        "style_constitution": copy.deepcopy(raw_plan["style_constitution"]),
        "series_visual_dna": copy.deepcopy(raw_plan["series_visual_dna"]),
        "typography_rhythm": {
            "campaign_rule": raw_plan.get("typography_campaign_rule", "文字绑定当前屏的证据对象与阅读路径，不使用连续默认标题位。"),
            "primary_mode_sequence": [item["text_system"]["primary_mode"] for item in modules],
            "anchor_zone_sequence": [item["text_system"]["anchor_zone"] for item in modules],
            "centered_hero_max": max(1, sum(item["text_system"]["primary_mode"] == "centered_hero_headline" for item in modules)),
            "consecutive_anchor_repeat_limit": 1,
            "exceptions": [],
        },
        "modules": modules,
    }


