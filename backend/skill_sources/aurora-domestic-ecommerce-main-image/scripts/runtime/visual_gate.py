from __future__ import annotations

from pathlib import Path
from typing import Any

from core import LAYOUT_ONLY_CHECKS, STYLE_ONLY_CHECKS, VISUAL_CHECKS, require


ERROR_CODES = {
    "product_identity_match": "PRODUCT_IDENTITY_DRIFT",
    "factual_copy_match": "TEXT_FACT_DRIFT",
    "typography_style_match": "TYPOGRAPHY_STYLE_DRIFT",
    "typography_legibility": "TYPOGRAPHY_OR_LEGIBILITY_FAILURE",
    "page_role_expression": "PAGE_ROLE_FAILURE",
    "evidence_relation_visible": "EVIDENCE_RELATION_NOT_VISIBLE",
    "campaign_style_match": "CAMPAIGN_STYLE_DRIFT",
    "headline_treatment_match": "TYPOGRAPHY_STYLE_DRIFT",
    "palette_role_match": "PALETTE_ROLE_DRIFT",
    "graphic_language_match": "GRAPHIC_LANGUAGE_DRIFT",
    "photography_finish_match": "PHOTOGRAPHY_FINISH_DRIFT",
    "information_surface_match": "INFORMATION_SURFACE_DRIFT",
    "layout_not_cloned": "LAYOUT_OVERCLONED",
    "set_diversity_match": "SET_DIVERSITY_FAILURE",
    "product_separation": "PRODUCT_SEPARATION_FAILURE",
    "numeric_hierarchy_if_applicable": "NUMERIC_HIERARCHY_FAILURE",
    "design_intensity_controlled": "DESIGN_INTENSITY_FAILURE",
    "visual_performance_match": "VISUAL_PERFORMANCE_FAILURE",
    "baseline_performance_match": "VALIDATED_BASELINE_GAP",
    "approved_plan_fidelity_match": "APPROVED_PLAN_DRIFT",
    "campaign_constitution_match": "CAMPAIGN_CONSTITUTION_DRIFT",
    "source_background_noise_absent": "SOURCE_BACKGROUND_NOISE_LEAKED",
    "selling_point_visible_without_copy": "SELLING_POINT_NOT_VISUALLY_PROVEN",
    "selling_point_visual_dominance": "SELLING_POINT_EVIDENCE_NOT_DOMINANT",
    "claim_to_evidence_relation_clear": "CLAIM_EVIDENCE_RELATION_WEAK",
    "text_not_substituting_for_visual_proof": "TEXT_REPLACED_VISUAL_PROOF",
    "thumbnail_success": "THUMBNAIL_FAILURE",
    "no_unsupported_claim": "UNSUPPORTED_VISUAL_CLAIM",
    "no_hard_error": "HARD_VISUAL_ERROR",
}

REPAIR_POLICIES = {
    "product_identity_match": "废弃当前版本；回到原始同商品编辑底图，重新锁定轮廓、比例、颜色分区、部件与连接关系。",
    "factual_copy_match": "删除或改回已核验原文；不得用设计修饰掩盖事实或数字错误。",
    "typography_legibility": "保留已通过商品与构图层，只修正中文字符、断行、字号、对比度与数字单位层级。",
    "page_role_expression": "重建当前页阅读路径和商业职责；搜索首图必须恢复商品主导、点击钩子与闭合区。",
    "evidence_relation_visible": "让真实部件、动作或状态承担证明；若落点不确定，删除引线或示意，不得猜测。",
    "layout_not_cloned": "更换镜头、主体锚点、阅读路径和证据拓扑，不得只移动标题。",
    "product_separation": "提高商品与背景的明度、边缘、尺度和接触阴影分离，不得重绘商品。",
    "thumbnail_success": "压缩为两至三个扫描停顿，放大完整商品与主结论，删除小字和均权模块。",
    "visual_performance_match": "提高商品与主标题主导性，加入当前商品真实材质、动作或结构证据，并建立前中后景或双尺度张力。",
    "baseline_performance_match": "按用户验证的十套商业基准逐轴修复商品体量、标题表现、物理证据、空间纵深和商业闭合，不复制具体版式。",
    "approved_plan_fidelity_match": "恢复本页职责、主卖点意义、商品身份和必要商业条件；保留更有表现力的镜头与构图，不因其不同于规划草图而回退。",
    "campaign_constitution_match": "恢复商品身份、整套美术方向、当前页职责和背景隔离；保留当前更强的镜头、构图与表现手法。",
    "source_background_noise_absent": "删除原图桌面、墙面、缝隙、杂物、键盘和拍摄边角；保留商品身份并按批准方案重建干净场景。",
    "selling_point_visible_without_copy": "保留商品身份，加入与卖点对应的真实部件、材质、动作、界面、工作状态或结果；遮住文案后仍应看懂证明对象。",
    "selling_point_visual_dominance": "放大真实证明对象并用镜头、尺度、光线和空间关系使其成为第一或并列第一视觉；压低背景、装饰和无关主体。",
    "claim_to_evidence_relation_clear": "重组画面，使观者无需先读标题即可从真实证据直接得出本页卖点；删除含混或无关暗示。",
    "text_not_substituting_for_visual_proof": "删除文字替代证明的做法；可视/可演示卖点必须由像素证据承担，参数文字只作限定说明。",
    "no_unsupported_claim": "删除无证据的视觉暗示、认证、配件、优惠或效果，只保留USER_FACT。",
    "no_hard_error": "废弃当前版本并从原始商品证据重新生成，禁止局部美容式修补。",
}

# Spend the single automatic repair only on material defects. Small aesthetic
# gaps remain visible as advisory warnings and do not discard a good image.
HARD_BLOCKING_CHECKS = {
    "product_identity_match",
    "factual_copy_match",
    "source_background_noise_absent",
    "no_unsupported_claim",
    "no_hard_error",
}
MATERIAL_BLOCKING_CHECKS = {
    "typography_legibility",
    "page_role_expression",
    "evidence_relation_visible",
    "selling_point_visible_without_copy",
}


def evaluate(
    judgment: dict[str, Any],
    expected_image_hash: str,
    page_index: int,
    expected_image_path: str | None = None,
    approved_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require(judgment.get("page_index") == page_index, "judgment page_index mismatch")
    require(judgment.get("image_sha256") == expected_image_hash, "judgment image hash is stale or incorrect")
    reviewer = judgment.get("reviewer")
    require(isinstance(reviewer, dict), "reviewer provenance is required")
    require(reviewer.get("mode") == "AGENT_DIRECT_PIXEL_REVIEW", "visual judgment must be made by Agent direct pixel review")
    require(reviewer.get("pixel_inspection_performed") is True, "Agent must inspect actual image pixels")
    require(isinstance(reviewer.get("image_path"), str) and reviewer["image_path"].strip(), "reviewer.image_path is required")
    if expected_image_path is not None:
        require(Path(reviewer["image_path"]).resolve() == Path(expected_image_path).resolve(), "reviewer.image_path does not match the current generated asset")
    decision_summary = judgment.get("decision_summary")
    require(isinstance(decision_summary, str) and len(decision_summary.strip()) >= 20, "decision_summary must contain a concrete visual conclusion")
    performance = judgment.get("performance_observation")
    require(isinstance(performance, dict), "performance_observation is required")
    for name in (
        "estimated_product_occupancy_percent",
        "headline_visual_rank",
        "physical_evidence_seen",
        "depth_layers_seen",
        "commercial_closure_seen",
        "background_competition",
        "thumbnail_scan_order",
    ):
        require(name in performance, f"performance_observation.{name} is required")
    occupancy = performance["estimated_product_occupancy_percent"]
    require(isinstance(occupancy, (int, float)) and 0 <= occupancy <= 100, "estimated product occupancy must be 0..100")
    require(performance["headline_visual_rank"] in {"FIRST", "JOINT_FIRST", "SECOND", "WEAK"}, "headline_visual_rank is invalid")
    require(isinstance(performance["physical_evidence_seen"], list), "physical_evidence_seen must be a list")
    require(isinstance(performance["depth_layers_seen"], int) and 0 <= performance["depth_layers_seen"] <= 5, "depth_layers_seen must be 0..5")
    require(isinstance(performance["commercial_closure_seen"], bool), "commercial_closure_seen must be boolean")
    require(performance["background_competition"] in {"LOW", "MEDIUM", "HIGH"}, "background_competition is invalid")
    require(isinstance(performance["thumbnail_scan_order"], list) and 2 <= len(performance["thumbnail_scan_order"]) <= 4, "thumbnail_scan_order must contain 2..4 stops")
    fidelity = judgment.get("plan_fidelity_observation")
    require(isinstance(fidelity, dict), "plan_fidelity_observation is required")
    for name in ("observed_subject_and_view", "observed_scene_geometry", "observed_composition", "observed_reading_path", "missing_required_elements", "unexpected_plan_changes"):
        require(name in fidelity, f"plan_fidelity_observation.{name} is required")
    require(all(isinstance(fidelity[name], str) and len(fidelity[name].strip()) >= 8 for name in ("observed_subject_and_view", "observed_scene_geometry", "observed_composition")), "plan fidelity observations must describe visible pixels")
    require(isinstance(fidelity["observed_reading_path"], list) and 2 <= len(fidelity["observed_reading_path"]) <= 4, "observed_reading_path must contain 2..4 stops")
    require(isinstance(fidelity["missing_required_elements"], list), "missing_required_elements must be a list")
    require(isinstance(fidelity["unexpected_plan_changes"], list), "unexpected_plan_changes must be a list")
    proof_observation = judgment.get("selling_point_proof_observation")
    require(isinstance(proof_observation, dict), "selling_point_proof_observation is required")
    for name in ("source_text", "observability_class", "visible_without_copy", "pixel_evidence", "text_only_dependency", "evidence_is_primary_visual", "estimated_evidence_occupancy_percent", "claim_takeaway_without_copy", "expression_mode"):
        require(name in proof_observation, f"selling_point_proof_observation.{name} is required")
    require(isinstance(proof_observation["visible_without_copy"], bool), "visible_without_copy must be boolean")
    require(isinstance(proof_observation["pixel_evidence"], list), "pixel_evidence must be a list")
    require(isinstance(proof_observation["text_only_dependency"], bool), "text_only_dependency must be boolean")
    require(isinstance(proof_observation["evidence_is_primary_visual"], bool), "evidence_is_primary_visual must be boolean")
    evidence_occupancy = proof_observation["estimated_evidence_occupancy_percent"]
    require(isinstance(evidence_occupancy, (int, float)) and 0 <= evidence_occupancy <= 100, "estimated evidence occupancy must be 0..100")
    require(isinstance(proof_observation["claim_takeaway_without_copy"], str) and len(proof_observation["claim_takeaway_without_copy"].strip()) >= 8, "claim_takeaway_without_copy must state the visible takeaway")
    require(proof_observation["expression_mode"] in {"DIRECT", "INDIRECT", "COMPARATIVE", "SEQUENCE", "CONTEXTUAL", "TEXT_ONLY"}, "expression_mode is invalid")
    background = judgment.get("source_background_observation")
    require(isinstance(background, dict), "source_background_observation is required")
    require(isinstance(background.get("leaked_source_elements"), list), "leaked_source_elements must be a list")
    require(isinstance(background.get("rebuilt_scene_evidence"), str) and len(background["rebuilt_scene_evidence"].strip()) >= 8, "rebuilt_scene_evidence must describe visible pixels")
    if approved_contract:
        proof_contract = approved_contract["selling_point_visual_proof_contract"]
        require(proof_observation["source_text"] == proof_contract["source_text"], "selling-point observation source does not match approved plan")
        require(proof_observation["observability_class"] == proof_contract["observability_class"], "selling-point observability class does not match approved plan")
    checks = judgment.get("checks")
    require(isinstance(checks, dict), "judgment checks object is required")
    if fidelity["missing_required_elements"] or fidelity["unexpected_plan_changes"]:
        require(checks.get("approved_plan_fidelity_match", {}).get("passed") is False, "visible plan drift must fail approved_plan_fidelity_match")
    renderable = bool(approved_contract and approved_contract["selling_point_visual_proof_contract"].get("renderable_claim_must_survive_copy_removal"))
    if renderable and not proof_observation["visible_without_copy"]:
        require(checks.get("selling_point_visible_without_copy", {}).get("passed") is False, "a renderable claim without pixel proof must fail selling_point_visible_without_copy")
    if renderable and proof_observation["text_only_dependency"]:
        require(checks.get("text_not_substituting_for_visual_proof", {}).get("passed") is False, "text substituting for visual proof must fail its check")
    if renderable and not proof_observation["evidence_is_primary_visual"]:
        require(checks.get("selling_point_visual_dominance", {}).get("passed") is False, "non-dominant evidence must fail selling_point_visual_dominance")
    if background["leaked_source_elements"]:
        require(checks.get("source_background_noise_absent", {}).get("passed") is False, "source background leakage must fail source_background_noise_absent")
    reported_failed: list[str] = []
    blocking_failed: list[str] = []
    normalized: dict[str, Any] = {}
    scores: list[float] = []
    for name in VISUAL_CHECKS:
        item = checks.get(name)
        require(isinstance(item, dict), f"missing visual check {name}")
        require(isinstance(item.get("passed"), bool), f"{name}.passed must be boolean")
        evidence = item.get("observed_evidence")
        require(isinstance(evidence, str) and len(evidence.strip()) >= 12, f"{name}.observed_evidence must describe visible pixels")
        score = item.get("score")
        require(isinstance(score, (int, float)) and 0 <= score <= 100, f"{name}.score must be 0..100")
        if item["passed"]:
            require(score >= 70, f"{name}.passed requires a score of at least 70")
        normalized[name] = {
            "passed": item["passed"],
            "observed_evidence": evidence.strip(),
            "score": float(score),
        }
        scores.append(float(score))
        if not item["passed"]:
            reported_failed.append(name)
            if name in HARD_BLOCKING_CHECKS or (name in MATERIAL_BLOCKING_CHECKS and score < 70):
                blocking_failed.append(name)
    score = round(sum(scores) / len(scores), 2)
    # Do not spend an image call because many minor styling, density, layout,
    # thumbnail-polish or optional-proof scores lower the mean. Only a named
    # material defect above can trigger the one automatic repair.
    passed = not blocking_failed
    if passed:
        scope = "NONE"
    elif set(blocking_failed).issubset(STYLE_ONLY_CHECKS):
        scope = "STYLE_LAYER_ONLY"
    elif set(blocking_failed).issubset(LAYOUT_ONLY_CHECKS):
        scope = "LAYOUT_OR_COMPOSITION_REPAIR_REQUIRED"
    else:
        scope = "PRODUCT_OR_FACT_REPAIR_REQUIRED"
    errors = [ERROR_CODES.get(name, "DESIGN_SCORE_BELOW_THRESHOLD") for name in blocking_failed]
    failed_checks = [name for name in blocking_failed if name in normalized]
    advisory_checks = [name for name in reported_failed if name not in failed_checks]
    completeness = judgment.get("audit_completeness_confirmation")
    require(completeness is True, "audit_completeness_confirmation must be true")
    deficiencies = judgment.get("all_observed_deficiencies")
    require(isinstance(deficiencies, list), "all_observed_deficiencies must be a list")
    deficiency_checks = []
    for item in deficiencies:
        require(isinstance(item, dict), "each deficiency must be an object")
        require(item.get("check") in failed_checks, "deficiency check must be a failed visual check")
        for field in ("visible_problem", "root_cause", "required_change"):
            require(isinstance(item.get(field), str) and len(item[field].strip()) >= 8, f"deficiency.{field} must be concrete")
        deficiency_checks.append(item["check"])
    require(set(deficiency_checks) == set(failed_checks), "all_observed_deficiencies must cover every failed check exactly as a complete diagnosis")
    repair_actions = [
        {
            "check": name,
            "error_code": ERROR_CODES.get(name, "DESIGN_SCORE_BELOW_THRESHOLD"),
            "observed_evidence": normalized[name]["observed_evidence"],
            "policy": REPAIR_POLICIES.get(name, "保留通过层，只修复该失败检查所对应的可见问题。"),
        }
        for name in failed_checks
    ]
    return {
        "status": "PASS" if passed else "BLOCKED",
        "page_index": page_index,
        "image_sha256": expected_image_hash,
        "checks": normalized,
        "design_score": score,
        "errors": sorted(set(errors)),
        "failed_checks": failed_checks,
        "advisory_checks": advisory_checks,
        "audit_policy": "MATERIAL_DEFECTS_ONLY; multiple selling points or useful information modules are never failures by count alone",
        "repair_actions": repair_actions,
        "repair_scope": scope,
        "visual_judgment_not_inferred_from_receipt": True,
        "reviewer": reviewer,
        "decision_summary": decision_summary.strip(),
        "performance_observation": performance,
        "plan_fidelity_observation": fidelity,
        "selling_point_proof_observation": proof_observation,
        "source_background_observation": background,
        "all_observed_deficiencies": deficiencies,
        "audit_completeness_confirmation": True,
    }
