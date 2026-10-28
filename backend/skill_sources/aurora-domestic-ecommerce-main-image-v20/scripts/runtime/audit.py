from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core import (
    CHECK_NAMES,
    HARD_CHECKS,
    MATERIAL_CHECKS,
    REBUILD_CHECKS,
    STYLE_CHECKS,
    require,
    validate_schema,
)


def evaluate(judgment: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    validate_schema(judgment, "visual-judgment.schema.json")
    require(judgment["page_index"] == version["page_index"], "judgment page mismatch")
    require(judgment["image_sha256"] == version["sha256"], "judgment image hash is stale")
    require(Path(judgment["reviewer"]["image_path"]).resolve() == Path(version["file"]).resolve(), "judgment image path mismatch")
    checks = judgment["checks"]
    names = [item["name"] for item in checks]
    require(names == CHECK_NAMES, "visual checks must contain the complete ordered v2 check set")
    checks_by_name = {item["name"]: item for item in checks}
    observations = judgment["observations"]
    layout = version.get("layout_contract") or {}
    layout_matches = all(
        observations.get(observation_key) == layout.get(contract_key)
        for observation_key, contract_key in (
            ("composition_archetype_seen", "composition_archetype"),
            ("product_zone_seen", "product_zone"),
            ("copy_zone_seen", "copy_zone"),
            ("dominant_view_seen", "dominant_view"),
            ("evidence_topology_seen", "evidence_topology"),
        )
    )
    require(not checks_by_name["layout_not_cloned"]["passed"] or layout_matches, "layout-not-cloned cannot pass when observed spatial structure misses the page-specific layout contract")
    require(not checks_by_name["approved_plan_fidelity_match"]["passed"] or layout_matches, "approved-plan fidelity cannot pass when the visible layout misses its contract")
    if version["page_index"] > 0:
        neighbor_difference = observations.get("neighbor_layout_difference", "").strip()
        require(not checks_by_name["layout_not_cloned"]["passed"] or len(neighbor_difference) >= 12, "layout-not-cloned needs a concrete visible difference from the previous page")
    require(not checks_by_name["selling_point_visual_dominance"]["passed"] or observations["evidence_is_primary_visual"] is True, "selling-point dominance cannot pass when evidence is not a primary visual")
    require(not checks_by_name["evidence_relation_visible"]["passed"] or bool(observations["physical_evidence"]), "evidence relation cannot pass without visible physical evidence")
    if version["page_index"] == 0:
        copy_contract = version.get("display_copy_contract") or {}
        headline_text = re.sub(r"[\s，,。；;：:·|/]+", "", observations["headline_text"])
        headline_concise = (
            len(headline_text) <= int(copy_contract.get("headline_hard_max_chars", 12))
            and observations["headline_line_count"] <= int(copy_contract.get("headline_max_lines", 2))
        )
        require(not checks_by_name["headline_treatment_match"]["passed"] or headline_concise, "search headline cannot pass when it exceeds the display-copy contract")
        require(not checks_by_name["thumbnail_success"]["passed"] or headline_concise, "thumbnail success cannot pass with an overlong search headline")
        baseline_visible = (
            observations["type_roles_seen"] >= 4
            and observations["headline_rank"] in {"FIRST", "JOINT_FIRST"}
            and observations["depth_layers"] >= 2
            and observations["commercial_closure_visible"] is True
            and bool(observations["physical_evidence"])
        )
        require(not checks_by_name["baseline_performance_match"]["passed"] or baseline_visible, "search baseline cannot pass without four type roles, strong headline, depth, physical evidence, and closure")
    normalized: list[dict[str, Any]] = []
    material_failed: list[str] = []
    advisory_failed: list[str] = []
    scores: list[float] = []
    for item in checks:
        name = item["name"]
        expected_material = name in MATERIAL_CHECKS
        require(item["material"] is expected_material, f"{name}.material must match v2 policy")
        require(not item["passed"] or item["score"] >= 70, f"{name} cannot pass below 70")
        scores.append(float(item["score"]))
        normalized.append(dict(item))
        if not item["passed"]:
            if name in HARD_CHECKS or (expected_material and item["score"] < 70):
                material_failed.append(name)
            else:
                advisory_failed.append(name)
    average = round(sum(scores) / len(scores), 2)
    deficiencies = judgment["all_material_deficiencies"]
    deficiency_names = [item["check"] for item in deficiencies]
    require(set(deficiency_names) == set(material_failed), "all material failed checks need exactly one complete deficiency")
    if average < 72 and not material_failed:
        material_failed.append("overall_design_quality")
        deficiencies = list(deficiencies) + [{
            "check": "overall_design_quality",
            "visible_problem": f"整体视觉均分仅为 {average}，多个层面共同削弱商业表现",
            "root_cause": "单项问题未达到阻断阈值，但累积后使商品、信息与证明缺少整体主导关系",
            "required_change": "在一次整体构图修复中提高商品、主标题、真实证据与商业闭合的共同清晰度",
        }]
    if not material_failed:
        scope = "NONE"
    elif set(material_failed) & REBUILD_CHECKS:
        scope = "REBUILD_FROM_PRODUCT_EVIDENCE"
    elif set(material_failed).issubset(STYLE_CHECKS):
        scope = "STYLE_LAYER_ONLY"
    else:
        scope = "LAYOUT_OR_COMPOSITION"
    return {
        "status": "PASS" if not material_failed else "BLOCKED",
        "design_score": average,
        "failed_checks": material_failed,
        "advisory_checks": advisory_failed,
        "repair_scope": scope,
        "decision_summary": judgment["decision_summary"],
        "observations": judgment["observations"],
        "checks": normalized,
        "all_material_deficiencies": deficiencies,
        "pixel_reviewed": True,
    }


def search_calibration(observation: dict[str, Any]) -> dict[str, Any]:
    require(observation.get("agent_pixel_reviewed") is True, "search calibration requires direct pixel review")
    axes = observation.get("axes")
    required_axes = {
        "product_dominance", "headline_performance", "physical_evidence",
        "spatial_depth", "commercial_closure", "thumbnail_competitiveness",
        "commercial_frame_structure", "type_hierarchy",
    }
    require(isinstance(axes, dict) and set(axes) == required_axes, "search calibration axes are incomplete")
    scores: list[float] = []
    for name, item in axes.items():
        require(isinstance(item, dict), f"search axis {name} must be an object")
        require(isinstance(item.get("score"), (int, float)) and 0 <= item["score"] <= 100, f"search axis {name} score is invalid")
        require(isinstance(item.get("observed_evidence"), str) and len(item["observed_evidence"].strip()) >= 8, f"search axis {name} needs pixel evidence")
        scores.append(float(item["score"]))
    average = round(sum(scores) / 8, 2)
    return {"status": "PASS" if average >= 80 and min(scores) >= 65 else "BLOCKED", "average": average, "axes": axes}


def validate_style_observation(observation: dict[str, Any]) -> dict[str, Any]:
    require(observation.get("agent_pixel_reviewed") is True, "style calibration requires direct pixel review")
    require(isinstance(observation.get("source_page_indexes"), list) and observation["source_page_indexes"], "style source pages are required")
    require(isinstance(observation.get("observed_evidence"), str) and len(observation["observed_evidence"].strip()) >= 12, "style calibration needs pixel evidence")
    tokens = observation.get("style_tokens")
    allowed = {"photography_finish", "light_direction", "color_temperature", "palette_roles", "palette_usage_ratio", "typography", "graphic_language", "information_surfaces", "density_band"}
    require(isinstance(tokens, dict) and tokens and set(tokens).issubset(allowed), "style tokens contain identity, layout, copy, or unknown fields")
    return tokens
