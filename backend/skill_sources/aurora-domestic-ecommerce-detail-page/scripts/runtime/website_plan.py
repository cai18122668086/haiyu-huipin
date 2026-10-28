"""Small website planning contract and deterministic native compilation.

This module is deliberately self-contained so the website adapter and portable
Skill use the same contract. It creates no product facts or pixel observations.
"""
from __future__ import annotations

import copy
import json

VERSION = "WEBSITE_DIRECT_V2"
AUDIT_PROFILE = "WEBSITE_LEAN_V1"
STYLE_FIELDS = (
    "visual_world", "color_and_light", "typography_and_graphics",
    "product_rendering", "rhythm",
)
ROLES = ("DOMINANT", "SUPPORTING", "IDENTITY_TOKEN_ONLY", "ABSENT")
CAMERAS = ("wide", "medium", "close", "macro", "mixed")


def validate_source_identity(raw: dict, identity: dict) -> None:
    """Use four source-bound observations instead of category keyword scoring."""
    from .state import HarnessError

    observations = raw.get("identity_observations")
    kinds = ["silhouette", "proportions", "parts", "color_material"]
    known = {image["id"] for image in raw["product_images"]}
    if not isinstance(observations, list) or len(observations) != len(kinds):
        raise HarnessError("Website identity requires four source observations")
    for kind, row in zip(kinds, observations, strict=True):
        if not isinstance(row, dict) or row.get("kind") != kind or row.get("source_asset_id") not in known:
            raise HarnessError("Website identity observation source or kind changed")
        if not isinstance(row.get("region"), str) or not row["region"].strip() or not isinstance(row.get("text"), str) or len(row["text"].strip()) < 4:
            raise HarnessError("Website identity needs concrete regions and descriptions")
    if identity["invariant_features"] != [row["text"] for row in observations]:
        raise HarnessError("Website identity must preserve the source observation texts")


def schema(count: int, fact_ids: list[str]) -> dict:
    text = {"type": "string", "minLength": 1}
    screen = {
        "type": "object", "additionalProperties": False,
        "required": ["duty", "title", "supporting_copy", "fact_ids", "visual_direction", "composition", "camera", "product_role"],
        "properties": {
            "duty": text, "title": {**text, "maxLength": 36},
            "supporting_copy": {"type": "array", "maxItems": 2, "items": text},
            "fact_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "enum": fact_ids}},
            "visual_direction": text, "composition": text,
            "camera": {"enum": list(CAMERAS)}, "product_role": {"enum": list(ROLES)},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["campaign_thesis", "style", "screens", "pair_transitions"],
        "properties": {
            "campaign_thesis": text,
            "style": {"type": "object", "additionalProperties": False,
                      "required": list(STYLE_FIELDS), "properties": {key: text for key in STYLE_FIELDS}},
            "screens": {"type": "array", "minItems": count, "maxItems": count, "items": screen},
            "pair_transitions": {"type": "array", "minItems": count // 2, "maxItems": count // 2, "items": text},
        },
    }


def check_brief(brief: object, facts: list[dict], required_ids: list[str], count: int) -> list[str]:
    errors = []
    if count not in (8, 10, 12) or not isinstance(brief, dict):
        return ["A website brief must be an object with 8, 10 or 12 screens"]
    if not isinstance(brief.get("campaign_thesis"), str) or not brief["campaign_thesis"].strip():
        errors.append("campaign_thesis must describe the page")
    style = brief.get("style")
    if not isinstance(style, dict) or any(not isinstance(style.get(k), str) or not style[k].strip() for k in STYLE_FIELDS):
        errors.append("style needs the five short visual passport strings")
    screens = brief.get("screens")
    if not isinstance(screens, list) or len(screens) != count:
        return errors + [f"screens must contain exactly {count} ordered screen briefs"]
    allowed = {item["id"] for item in facts}
    used = set()
    for index, screen in enumerate(screens, 1):
        path = f"screens/{index - 1}"
        if not isinstance(screen, dict):
            errors.append(path + " must be an object")
            continue
        for field in ("duty", "title", "visual_direction", "composition"):
            if not isinstance(screen.get(field), str) or not screen[field].strip():
                errors.append(path + "/" + field + " must be a nonempty string")
        copies = screen.get("supporting_copy")
        if not isinstance(copies, list) or len(copies) > 2 or any(not isinstance(v, str) or not v.strip() for v in copies):
            errors.append(path + "/supporting_copy must contain zero to two concise copy strings")
        ids = screen.get("fact_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or v not in allowed for v in ids):
            errors.append(path + "/fact_ids must use original active fact IDs")
        else:
            used.update(ids)
        if screen.get("camera") not in CAMERAS or screen.get("product_role") not in ROLES:
            errors.append(path + " needs an approved camera and product_role")
    if isinstance(screens[0], dict) and screens[0].get("product_role") != "DOMINANT":
        errors.append("The opening screen must establish the dominant product identity")
    missing = sorted(set(required_ids) - used)
    if missing:
        errors.append("User selling points need visible coverage: " + ", ".join(missing))
    transitions = brief.get("pair_transitions")
    if not isinstance(transitions, list) or len(transitions) != count // 2 or any(not isinstance(v, str) or not v.strip() for v in transitions):
        errors.append("pair_transitions needs one concrete background/material/light handoff per pair")
    return errors


def authored_plan(brief: dict, facts: list[dict], required_ids: list[str], count: int, width: int, height: int) -> dict:
    errors = check_brief(brief, facts, required_ids, count)
    if errors:
        raise ValueError("; ".join(errors))
    modules = []
    for index, screen in enumerate(brief["screens"], 1):
        transition = brief["pair_transitions"][(index - 1) // 2]
        modules.append({
            "id": f"M{index:02d}", "duty": screen["duty"],
            "buyer_question": screen["duty"], "lead_conclusion": screen["title"],
            "lead_claim_id": screen["fact_ids"][0], "supporting_claim_ids": screen["fact_ids"][1:],
            "story_function": "HOOK" if index == 1 else "PURCHASE_CLOSURE" if index == count else "SOLUTION_PROOF",
            "visual_proof_direction": screen["visual_direction"],
            "visual_archetype": "PRODUCT_DETAIL" if screen["product_role"] == "IDENTITY_TOKEN_ONLY" else "PRODUCT_HERO",
            "expression": {"visual_scene_sentence": screen["visual_direction"]},
            "structure": {"structure_family": screen["composition"]},
            "camera_scale": screen["camera"], "complete_product_role": screen["product_role"],
            "transition_contract": {
                "method_id": "BACKGROUND_STRINGING", "seam_carrier": transition, "incoming_anchor": transition, "outgoing_anchor": transition,
                "source_region_previous": f"M{index - 1 if index > 1 else 1:02d} 底部共享背景",
                "target_region_current": f"M{index:02d} 顶部共享背景",
                "handoff_geometry": transition,
            },
            "website_screen": copy.deepcopy(screen),
        })
    pairs = []
    for index in range(count // 2):
        lower = modules[index * 2 + 1]["transition_contract"]
        pairs.append({
            "pair_id": f"P{index + 1:02d}", "module_ids": [f"M{index * 2 + 1:02d}", f"M{index * 2 + 2:02d}"],
            "pair_merge_type": "PAIR_PLAN_MERGE", "internal_seam_method_id": lower["method_id"],
            "internal_seam_carrier": lower["seam_carrier"],
            "internal_seam_source_region": lower["source_region_previous"],
            "internal_seam_target_region": lower["target_region_current"],
            "internal_seam_geometry": lower["handoff_geometry"],
            "internal_seam_zone": {"start_y": height - 96, "end_y": height + 96},
        })
    coverage = []
    for fact in facts:
        containing = [m["id"] for m in modules if fact["id"] in m["website_screen"]["fact_ids"]]
        coverage.append({"claim_id": fact["id"], "disposition": "LEAD" if fact["id"] in required_ids else "SUPPORT" if containing else "DEFERRED",
                         "module_ids": containing, "rationale": "使用已创作屏幕中的事实关联" if containing else "保留为商品身份观察，本次不授权额外宣传文案",
                         "defer_reason_code": "NOT_DEFERRED" if containing else "IDENTITY_EVIDENCE_ONLY"})
    return {
        "plan_format": VERSION, "planning_mode": VERSION, "website_contract_version": AUDIT_PROFILE,
        "website_brief": copy.deepcopy(brief),
        "fact_ledger": [{"id": f["id"], "text": f["text"]} for f in facts],
        "required_fact_ids": list(required_ids), "campaign_thesis": brief["campaign_thesis"],
        "dominant_decision_type": "identity", "style_constitution": copy.deepcopy(brief["style"]),
        "series_visual_dna": copy.deepcopy(brief["style"]), "modules": modules,
        "reference_expression_system": {"mode": VERSION, "visual_grammar": copy.deepcopy(brief["style"])},
        "output_strategy": {
            "module_count": count, "canvas_width_px": width, "module_height_px": height,
            "claim_coverage": coverage,
            "continuous_pair_rendering": {
                "enabled": True, "grouping_mode": "TWO_SCREEN_PAIRS", "pair_size": 2,
                "pair_merge_type": "PAIR_PLAN_MERGE", "pair_count": count // 2,
                "pair_canvas_width_px": width, "pair_canvas_height_px": height * 2,
                "pair_canvas_aspect_ratio": "3:8", "decomposition_window_aspect_ratio": "3:4",
                "pair_slice_height_px": height, "pair_ids": pairs,
            },
        },
    }


def compile_native(session: dict, raw: dict) -> dict:
    """Expand screen briefs into the existing native generation envelope."""
    from .plan_normalizer import _derive_module
    from .state import HarnessError

    facts = [f for f in session["input"]["verified_facts"] if f["id"] in session.get("planning_fact_ids", [v["id"] for v in session["input"]["verified_facts"]])]
    required = [f for point in session["input"].get("user_supplied_selling_points", []) for f in point.get("fact_ids", []) if f in {row["id"] for row in facts}]
    output = session["input"]["platform_profile"]
    try:
        expected = authored_plan(raw["website_brief"], facts, required, len(raw["modules"]), output["canvas_width_px"], output["module_height_px"])
        comparable = {key: raw[key] for key in expected}
        if comparable != expected:
            raise ValueError("Website brief, facts, dimensions and displayed plan disagree")
    except (KeyError, TypeError, ValueError) as exc:
        raise HarnessError(str(exc)) from exc
    plan = copy.deepcopy(expected)
    plan["output_strategy"].update({**output, "module_count_candidates": [{"candidate_mode": "DIRECT_DERIVATION", "count": len(raw["modules"]), "decision": "SELECTED"}]})
    expanded = []
    for index, source in enumerate(expected["modules"], 1):
        screen = source["website_screen"]
        copies = [{"text": screen["title"], "role": "HEADLINE", "anchor": screen["composition"]}]
        copies += [{"text": text, "role": "SUPPORT", "anchor": screen["composition"]} for text in screen["supporting_copy"]]
        role = screen["product_role"]
        raw_module = {
            **source, "selling_point_cluster_ids": [source["lead_claim_id"]],
            "pain_ids": [], "value_bridge_ids": screen["fact_ids"],
            "visual_evidence": {"evidence_class": "DIRECT_OBSERVATION", "proof_device": "PRODUCT_EVIDENCE", "visible_content": [screen["visual_direction"]], "forbidden_inventions": ["不得新增来源未提供的产品事实"]},
            "proof": {"buyer_takeaway": screen["title"], "visual_verb": "展示", "primary_subject": screen["visual_direction"], "supporting_subjects": ["真实商品局部"], "required_relations": [screen["visual_direction"]], "minimum_visual_payload": 2, "visual_specificity_anchor": [screen["visual_direction"]], "hidden_copy_test": screen["visual_direction"]},
            "expression": {**source["expression"], "expression_mode": "DIRECT_VISUAL", "claim_visualizability": "DIRECT", "primary_visual_carrier": "PRODUCT_DETAIL" if role == "IDENTITY_TOKEN_ONLY" else "PRODUCT_HERO", "copy_responsibility": "用批准的短文案命名可见特征", "product_presence_policy": {"DOMINANT": "REQUIRED_DOMINANT", "SUPPORTING": "REQUIRED_SUPPORTING", "IDENTITY_TOKEN_ONLY": "IDENTITY_TOKEN_ONLY", "ABSENT": "ABSENT_PREFERRED"}[role]},
            "structure": {"structure_family": "ASYMMETRIC_EDITORIAL", "structural_relationship": screen["composition"], "zone_briefs": [screen["visual_direction"], screen["composition"]], "forbidden_fallbacks": ["不复制相邻屏幕版式"]},
            "visible_copy": copies, "typography": {"primary_mode": "object_anchored", "anchor_target": screen["visual_direction"], "anchor_zone": screen["composition"]},
            "composition_family": screen["composition"], "variation_from_previous": [screen["composition"]],
            "primary_subject_class": "PRODUCT_DETAIL_OR_STRUCTURE" if role == "IDENTITY_TOKEN_ONLY" else "PRODUCT_HERO",
            "secondary_subject_classes": [], "presentation_form": "PRODUCT_DETAIL" if role == "IDENTITY_TOKEN_ONLY" else "EDITORIAL_PRODUCT",
            "visual_cast": {"hero_subject": screen["visual_direction"], "claim_object": screen["visual_direction"], "action_subject": None, "result_subject": None, "context_subject": None, "product_identity_anchor": "原图商品形态及局部结构"},
            "content_unit_contract": {"units": [{"text": item["text"], "fact_ids": screen["fact_ids"], "visual_anchor": screen["visual_direction"]} for item in copies], "density_class": "STANDARD", "unit_count": len(copies)},
        }
        raw_module["expression_signature"] = "+".join(str(raw_module[key]) for key in ("primary_subject_class", "presentation_form", "camera_scale", "complete_product_role"))
        value = _derive_module(session, raw_module, index, len(expected["modules"]), VERSION)
        value.update(planning_mode=VERSION, website_contract_version=AUDIT_PROFILE, website_screen=copy.deepcopy(screen))
        expanded.append(value)
    plan["modules"] = expanded
    plan["website_source_plan"] = copy.deepcopy(raw)
    return plan


def approval_markdown(session: dict) -> str:
    plan = session["plan"]
    lines = ["# 商品详情图方案", "", plan["campaign_thesis"], "", "相邻两屏合成一张 3:8 连续画布，固定切为两张 3:4 图片。", "", "## 商品事实", ""]
    lines += ["- " + row["text"] for row in plan["fact_ledger"]]
    lines += ["", "## 系列视觉", ""] + ["- " + value for value in plan["style_constitution"].values()]
    lines += ["", "## 逐屏方案", "", "| 屏幕 | 职责与批准文案 | 画面与构图 |", "|---|---|---|"]
    for module in plan["modules"]:
        screen = module["website_screen"]
        clean = lambda text: str(text).replace("|", "／").replace("\n", " ")
        lines.append("| " + module["id"] + " | " + clean(screen["duty"] + "；" + " / ".join([screen["title"], *screen["supporting_copy"]])) + " | " + clean(screen["visual_direction"] + "；" + screen["composition"]) + " |")
    lines += ["", "每屏检查商品、事实、文字、卖点、构图及证据；仅实质问题触发一次自动修复。", ""]
    return "\n".join(lines)


def screen_prompt(session: dict, module: dict, window: dict | None = None, repair: bool = False) -> str:
    plan = module["plan"]
    screen = plan["website_screen"]
    facts = {row["id"]: row["text"] for row in session["input"]["verified_facts"]}
    text = ("画面职责：" + screen["duty"] + "\n画面：" + screen["visual_direction"]
            + "\n构图：" + screen["composition"] + "\n镜头：" + screen["camera"]
            + "\n商品出镜：" + screen["product_role"]
            + "\n允许表达的事实：" + "；".join(facts[key] for key in screen["fact_ids"])
            + "\n只允许出现这些宣传文字：" + json.dumps([screen["title"], *screen["supporting_copy"]], ensure_ascii=False)
            + "\n文字清晰、简短、有层级，融入当前画面留白；不画入任何指令或字段名。")
    if window:
        text = f"画布{window['position']}固定窗口：\n" + text
    if repair:
        text += "\n根据已观察到的实质问题集中修复：" + json.dumps(module.get("repair_plan") or {}, ensure_ascii=False)
    return text


def pair_prompt(session: dict, modules: list[dict], repair: bool = False) -> tuple[str, list[str]]:
    output = modules[0]["plan"]["output"]
    width, height = output["width_px"], output["height_px"]
    prompts = [screen_prompt(session, item, {"position": "上方" if i == 0 else "下方"}, repair) for i, item in enumerate(modules)]
    style = session["plan"]["style_constitution"]
    transition = modules[1]["plan"]["transition_contract"]["seam_carrier"]
    prompt = (f"创作一张 {width}×{height * 2} 像素、3:8 的完整电商详情连续画布。"
              f"上半部 y=0–{height}、下半部 y={height}–{height * 2} 各承担下述职责；不得改变切片位置。"
              "先建立贯穿整图的空间、背景、光线和材质，再让两部分形成不同的构图。不要上下拼贴两张海报，不画硬横线或白色分隔带。"
              "\n系列视觉：" + "；".join(style.values())
              + "\n跨中部边界的实际视觉连接：" + transition
              + "\n全部原始商品图是同一商品的不同视角；保持形态、颜色、结构和部件关系。重建商业背景，不复制来源水印。"
              + "\n系列参考只继承色彩、光线、字体气质和完成度，改变主体位置、镜头与文字布局。"
              + ("\n无品牌商品，不添加任何独立品牌标志。" if session.get("brand_mode") == "BRANDLESS" else "\n独立品牌标志仅在首屏使用已授权来源，后续屏不增加独立标志。")
              + "\n\n" + "\n\n".join(prompts)
              + "\n完整覆盖四边，保持 3:8。只使用上述批准文案，不新增参数、功效、材质、认证或承诺。")
    return prompt, prompts
