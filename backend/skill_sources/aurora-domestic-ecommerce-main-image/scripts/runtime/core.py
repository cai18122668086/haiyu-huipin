from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "domestic-ecommerce-image-harness-0.1.17"
PAGE_ROLES = [
    "SEARCH_MAIN_IMAGE_LISTING_COVER",
    "CAROUSEL_IDENTITY_HERO",
    "CAROUSEL_CORE_FEATURE_PROOF",
    "CAROUSEL_SCENE_OR_OPERATION",
    "CAROUSEL_DETAIL_OR_TRUST",
    "CAROUSEL_OFFER_OR_SPEC_CLOSURE",
]
REFERENCE_ROLES = {
    "PRODUCT_IDENTITY_AUTHORITY",
    "STYLE_REFERENCE_ONLY",
    "CAMERA_GRAMMAR_ONLY",
    "NON_BINDING_REFERENCE",
}
STYLE_FIELDS = {
    "photography_finish",
    "light_direction",
    "color_temperature",
    "palette_roles",
    "palette_usage_ratio",
    "typography",
    "graphic_language",
    "information_surface_geometry",
    "density_band",
    "prohibited_styles",
}
SHOT_FIELDS = {
    "view_crop",
    "product_scale",
    "product_state",
    "scene_geometry",
    "subject_anchor",
    "layout_family",
}
COMPOSITION_FIELDS = {
    "spatial_topology",
    "copy_topology",
    "background",
    "lighting",
}
DESIGN_INTELLIGENCE_FIELDS = {
    "product_form_adapter",
    "campaign_thesis",
    "product_derived_visual_cues",
    "evidence_topology_rationale",
    "prohibited_template_shortcuts",
}
COMMERCIAL_ORCHESTRATION_FIELDS = {
    "information_confidence",
    "buyer_demand_map",
    "demand_to_selling_point_match",
    "purchase_decision_type",
    "page_strategy",
    "page_task_table",
    "visual_rhythm_plan",
}
CONFIDENCE_LEVELS = {"CONFIRMED", "REASONABLE_INFERENCE", "NEEDS_CONFIRMATION"}
VIEW_SCORE_FIELDS = {
    "identity_clarity",
    "target_visibility",
    "complete_product_coverage",
    "component_separation",
    "credible_action_or_scene_relation",
    "compositional_harmony",
}
OBSERVABILITY_CLASSES = {
    "DIRECTLY_VISIBLE",
    "ACTION_DEMONSTRABLE",
    "TEXT_ONLY_VERIFIED",
    "NOT_RENDERABLE",
}
GENERATION_MODES = {"PRODUCT_PRESERVING_EDIT", "REFERENCE_GUIDED_GENERATION"}
IDENTITY_SENSITIVE_PAGE_INDEXES = {0, 1, 3, 5}
VISUAL_CHECKS = [
    "product_identity_match",
    "factual_copy_match",
    "typography_style_match",
    "typography_legibility",
    "page_role_expression",
    "evidence_relation_visible",
    "campaign_style_match",
    "headline_treatment_match",
    "palette_role_match",
    "graphic_language_match",
    "photography_finish_match",
    "information_surface_match",
    "layout_not_cloned",
    "set_diversity_match",
    "product_separation",
    "numeric_hierarchy_if_applicable",
    "design_intensity_controlled",
    "visual_performance_match",
    "baseline_performance_match",
    "approved_plan_fidelity_match",
    "campaign_constitution_match",
    "source_background_noise_absent",
    "selling_point_visible_without_copy",
    "selling_point_visual_dominance",
    "claim_to_evidence_relation_clear",
    "text_not_substituting_for_visual_proof",
    "thumbnail_success",
    "no_unsupported_claim",
    "no_hard_error",
]
STYLE_ONLY_CHECKS = {
    "typography_style_match",
    "campaign_style_match",
    "headline_treatment_match",
    "palette_role_match",
    "graphic_language_match",
    "photography_finish_match",
    "information_surface_match",
}
LAYOUT_ONLY_CHECKS = {
    "page_role_expression",
    "layout_not_cloned",
    "set_diversity_match",
    "product_separation",
    "numeric_hierarchy_if_applicable",
    "design_intensity_controlled",
    "visual_performance_match",
    "baseline_performance_match",
    "approved_plan_fidelity_match",
    "campaign_constitution_match",
    "source_background_noise_absent",
    "selling_point_visible_without_copy",
    "selling_point_visual_dominance",
    "claim_to_evidence_relation_clear",
    "text_not_substituting_for_visual_proof",
    "thumbnail_success",
}


class HarnessError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_file(path: str | Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def is_supported_image(path: str | Path) -> bool:
    with Path(path).open("rb") as stream:
        header = stream.read(16)
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def image_dimensions(path: str | Path) -> tuple[int, int]:
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8\xff"):
        offset = 2
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            length = int.from_bytes(data[offset:offset + 2], "big")
            require(length >= 2 and offset + length <= len(data), "invalid JPEG structure")
            if marker in sof:
                require(length >= 7, "invalid JPEG size segment")
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                return width, height
            offset += length
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if kind == b"VP8L" and data[20] == 0x2F:
            b1, b2, b3, b4 = data[21:25]
            return 1 + (b1 | ((b2 & 0x3F) << 8)), 1 + ((b2 >> 6) | (b3 << 2) | ((b4 & 0x0F) << 10))
        if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    raise HarnessError(f"cannot determine generated image dimensions: {path}")


def load_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"JSON root must be an object: {path}")
    return value


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessError(message)


def require_state(session: dict[str, Any], *allowed: str) -> None:
    require(
        session.get("state") in allowed,
        f"state {session.get('state')} does not allow this action; expected {allowed}",
    )


def record(session: dict[str, Any], action: str, old_state: str) -> None:
    session.setdefault("history", []).append(
        {
            "at": utc_now(),
            "action": action,
            "from": old_state,
            "to": session["state"],
        }
    )
    session["updated_at"] = utc_now()


def transition(session: dict[str, Any], state: str, action: str) -> None:
    old_state = session["state"]
    session["state"] = state
    record(session, action, old_state)


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(flatten_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(flatten_strings(item))
        return result
    if value is None:
        return []
    return [str(value)]


def raw_reference_paths(raw: dict[str, Any]) -> set[str]:
    return set(flatten_strings(raw.get("product", {}).get("original_references", [])))


def verified_fact_texts(raw: dict[str, Any]) -> set[str]:
    product = raw.get("product", {})
    values: set[str] = set()
    for name in (
        "verified_attributes",
        "verified_selling_points",
        "verified_scenes",
        "verified_offers",
        "verified_service_or_trust_facts",
        "user_provided_display_facts",
    ):
        values.update(flatten_strings(product.get(name)))
    return values


def validate_raw(raw: dict[str, Any]) -> None:
    require(isinstance(raw.get("request_id"), str) and raw["request_id"].strip(), "request_id is required")
    product = raw.get("product")
    require(isinstance(product, dict), "product object is required")
    for field in ("name", "category"):
        require(isinstance(product.get(field), str) and product[field].strip(), f"product.{field} is required")
    require(bool(raw_reference_paths(raw)), "product.original_references requires at least one path")
    require(bool(verified_fact_texts(raw)), "at least one user-provided display fact is required")
    require(raw.get("profile01_semantics_read", False) is False, "profile01_semantics_read must be false")


def validate_identity(identity: dict[str, Any], session: dict[str, Any]) -> None:
    require(identity.get("request_id") == session["request_id"], "identity request_id mismatch")
    require(identity.get("profile01_semantics_read", False) is False, "identity must keep profile01 unread")
    require(identity.get("previous_generated_images_used", False) is False, "generated images cannot be identity evidence")
    for field in ("product_form", "identity_summary"):
        require(isinstance(identity.get(field), str) and identity[field].strip(), f"identity.{field} is required")
    for field in ("must_preserve", "must_not_change"):
        require(isinstance(identity.get(field), list) and identity[field], f"identity.{field} must be nonempty")
        require(all(isinstance(x, str) and x.strip() for x in identity[field]), f"identity.{field} must contain text")
    evidence = identity.get("evidence")
    require(isinstance(evidence, list) and evidence, "identity.evidence must be nonempty")
    raw_paths = raw_reference_paths(session["raw_input"])
    ids: set[str] = set()
    for item in evidence:
        require(isinstance(item, dict), "identity evidence item must be an object")
        evidence_id = item.get("evidence_id")
        path = item.get("path")
        require(isinstance(evidence_id, str) and evidence_id.strip(), "evidence_id is required")
        require(evidence_id not in ids, f"duplicate evidence_id {evidence_id}")
        ids.add(evidence_id)
        require(path in raw_paths, f"identity evidence path is not in raw original_references: {path}")
        require(isinstance(item.get("view_tags"), list) and item["view_tags"], f"view_tags required for {evidence_id}")


def validate_campaign_style(style: Any) -> None:
    require(isinstance(style, dict), "campaign_style must be an object")
    missing = STYLE_FIELDS - set(style)
    require(not missing, f"campaign_style missing fields: {sorted(missing)}")
    require(isinstance(style["palette_roles"], dict) and style["palette_roles"], "palette_roles must be nonempty")
    require(isinstance(style["palette_usage_ratio"], dict) and style["palette_usage_ratio"], "palette_usage_ratio must be nonempty")
    ratios = list(style["palette_usage_ratio"].values())
    require(all(isinstance(x, (int, float)) and x >= 0 for x in ratios), "palette ratios must be nonnegative numbers")
    require(abs(sum(ratios) - 100) <= 0.01, "palette usage ratios must sum to 100")
    require(isinstance(style["typography"], dict) and style["typography"], "typography must be nonempty")
    require(isinstance(style["prohibited_styles"], list), "prohibited_styles must be a list")


def validate_commercial_orchestration(value: Any) -> None:
    require(isinstance(value, dict), "commercial_orchestration must be an object")
    missing = COMMERCIAL_ORCHESTRATION_FIELDS - set(value)
    require(not missing, f"commercial_orchestration missing fields: {sorted(missing)}")
    decision = value["purchase_decision_type"]
    require(isinstance(decision, str) and decision.strip(), "purchase_decision_type is required")
    strategy = value["page_strategy"]
    require(isinstance(strategy, dict), "page_strategy must be an object")
    for field in ("main_purchase_resistance", "conversion_angle", "proof_style"):
        require(isinstance(strategy.get(field), str) and strategy[field].strip(), f"page_strategy.{field} is required")
    confidence = value["information_confidence"]
    require(isinstance(confidence, list) and confidence, "information_confidence must be nonempty")
    for item in confidence:
        require(isinstance(item, dict), "information_confidence items must be objects")
        require(isinstance(item.get("source_text"), str) and item["source_text"].strip(), "information confidence source_text is required")
        require(item.get("confidence_level") in CONFIDENCE_LEVELS, "information confidence level is invalid")
    tasks = value["page_task_table"]
    require(isinstance(tasks, list) and len(tasks) == 6, "page_task_table must contain exactly six rows")
    task_fields = {
        "page_index", "page_role", "buyer_demand", "buyer_question",
        "matched_selling_point", "confidence_level", "visual_proof_method",
        "required_evidence", "subject_scale", "layout_structure",
        "copy_direction", "risk_boundary", "difference_from_previous",
        "primary_selling_point_source_text", "selling_point_display_label",
        "supporting_selling_points", "selling_point_bundle_id",
        "selling_point_bundle_source_texts", "claim_visual_evidence_map",
        "non_repetition_reason",
    }
    for index, task in enumerate(tasks):
        require(isinstance(task, dict), f"page task {index} must be an object")
        require(not (task_fields - set(task)), f"page task {index} is incomplete")
        require(task["page_index"] == index, f"page task {index} index mismatch")
        require(task["page_role"] == PAGE_ROLES[index], f"page task {index} role mismatch")
        require(task["confidence_level"] in CONFIDENCE_LEVELS, f"page task {index} confidence is invalid")
        for field in task_fields - {"page_index", "supporting_selling_points", "selling_point_bundle_source_texts", "claim_visual_evidence_map"}:
            require(isinstance(task[field], str) and task[field].strip(), f"page task {index}.{field} is required")
        require(task["matched_selling_point"] == task["primary_selling_point_source_text"], f"page task {index} primary selling point mismatch")
        require(isinstance(task["supporting_selling_points"], list), f"page task {index}.supporting_selling_points must be a list")
        require(all(isinstance(item, str) and item.strip() for item in task["supporting_selling_points"]), f"page task {index} supporting selling points must contain text")
        require(task["primary_selling_point_source_text"] not in task["supporting_selling_points"], f"page task {index} cannot repeat its primary selling point as support")
        require(isinstance(task["selling_point_bundle_source_texts"], list) and task["selling_point_bundle_source_texts"], f"page task {index} selling point bundle must be nonempty")
        require(task["primary_selling_point_source_text"] in task["selling_point_bundle_source_texts"], f"page task {index} lead selling point must belong to its bundle")
        require(len(task["selling_point_bundle_source_texts"]) == len(set(task["selling_point_bundle_source_texts"])), f"page task {index} selling point bundle contains duplicates")
        require(isinstance(task["claim_visual_evidence_map"], list) and len(task["claim_visual_evidence_map"]) == len(task["selling_point_bundle_source_texts"]), f"page task {index} must map every bundled selling point to visual evidence")
        mapped_claims = [item.get("source_text") for item in task["claim_visual_evidence_map"] if isinstance(item, dict)]
        require(mapped_claims == task["selling_point_bundle_source_texts"], f"page task {index} visual evidence map must preserve bundle order")
        require(all(item.get("role") in {"LEAD", "SUPPORT"} and isinstance(item.get("visual_proof_method"), str) and item["visual_proof_method"].strip() for item in task["claim_visual_evidence_map"]), f"page task {index} visual evidence map is incomplete")
    bundle_ids = [task["selling_point_bundle_id"] for task in tasks]
    require(len(set(bundle_ids)) == 6, "all six pages must have distinct selling-point bundle duties")
    require(tasks[0]["layout_structure"] != tasks[1]["layout_structure"], "search cover and carousel hero must not share the same execution layout")
    require(tasks[0]["difference_from_previous"] != tasks[1]["difference_from_previous"], "search cover and carousel hero need distinct commercial duties")
    for field in ("buyer_demand_map", "demand_to_selling_point_match", "visual_rhythm_plan"):
        rows = value[field]
        require(isinstance(rows, list) and len(rows) == 6, f"{field} must contain exactly six rows")


def validate_plan(plan: dict[str, Any], session: dict[str, Any]) -> None:
    identity = session["identity_contract"]
    evidence_ids = {x["evidence_id"] for x in identity["evidence"]}
    intelligence = plan.get("design_intelligence")
    require(isinstance(intelligence, dict), "design_intelligence must be an object")
    missing_intelligence = DESIGN_INTELLIGENCE_FIELDS - set(intelligence)
    require(not missing_intelligence, f"design_intelligence missing fields: {sorted(missing_intelligence)}")
    for field in ("product_form_adapter", "campaign_thesis", "evidence_topology_rationale"):
        require(isinstance(intelligence[field], str) and intelligence[field].strip(), f"design_intelligence.{field} is required")
    for field in ("product_derived_visual_cues", "prohibited_template_shortcuts"):
        require(isinstance(intelligence[field], list) and intelligence[field], f"design_intelligence.{field} must be nonempty")
        require(all(isinstance(x, str) and x.strip() for x in intelligence[field]), f"design_intelligence.{field} must contain text")
    validate_campaign_style(plan.get("campaign_style"))
    validate_commercial_orchestration(plan.get("commercial_orchestration"))
    bindings = plan.get("reference_bindings")
    require(isinstance(bindings, list) and bindings, "reference_bindings must be nonempty")
    bound_identity_ids: set[str] = set()
    for binding in bindings:
        require(binding.get("evidence_id") in evidence_ids, "reference binding uses unknown evidence_id")
        require(binding.get("role") in REFERENCE_ROLES, "reference binding has invalid role")
        pages = binding.get("page_indexes")
        require(isinstance(pages, list) and pages, "reference binding page_indexes must be nonempty")
        require(all(isinstance(x, int) and 0 <= x < 6 for x in pages), "reference page index must be 0..5")
        if binding["role"] == "PRODUCT_IDENTITY_AUTHORITY":
            bound_identity_ids.add(binding["evidence_id"])
    require(bound_identity_ids == evidence_ids, "every identity evidence item must be bound as PRODUCT_IDENTITY_AUTHORITY")
    pages = plan.get("pages")
    require(isinstance(pages, list) and len(pages) == 6, "plan must contain exactly six pages")
    facts = verified_fact_texts(session["raw_input"])
    shot_keys: set[str] = set()
    for index, page in enumerate(pages):
        require(page.get("index") == index, f"page {index} index mismatch")
        require(page.get("page_role") == PAGE_ROLES[index], f"page {index} role must be {PAGE_ROLES[index]}")
        for field in ("buyer_objection", "visible_proof", "thumbnail_success"):
            require(isinstance(page.get(field), str) and page[field].strip(), f"page {index}.{field} is required")
        if index:
            require(isinstance(page.get("differs_from_previous"), str) and page["differs_from_previous"].strip(), f"page {index} needs differs_from_previous")
        claims = page.get("claim_source_texts")
        require(isinstance(claims, list) and claims, f"page {index} requires claim_source_texts")
        require(all(x in facts for x in claims), f"page {index} contains a claim not present verbatim in verified facts")
        page_evidence = page.get("evidence_ids")
        require(isinstance(page_evidence, list) and page_evidence, f"page {index} requires evidence_ids")
        require(set(page_evidence).issubset(evidence_ids), f"page {index} uses unknown evidence")
        bound_for_page = {
            x["evidence_id"]
            for x in bindings
            if x["role"] == "PRODUCT_IDENTITY_AUTHORITY" and index in x["page_indexes"]
        }
        require(set(page_evidence).issubset(bound_for_page), f"page {index} evidence is not role-bound to that page")
        shot = page.get("shot_key")
        require(isinstance(shot, dict) and not (SHOT_FIELDS - set(shot)), f"page {index} shot_key is incomplete")
        shot_hash = digest_value({key: shot[key] for key in sorted(SHOT_FIELDS)})
        require(shot_hash not in shot_keys, f"page {index} duplicates a complete shot key")
        shot_keys.add(shot_hash)
        composition = page.get("composition")
        require(isinstance(composition, dict) and not (COMPOSITION_FIELDS - set(composition)), f"page {index} composition is incomplete")
        observability = page.get("claim_observability")
        require(isinstance(observability, list) and observability, f"page {index} requires claim_observability")
        observed_claims: set[str] = set()
        for item in observability:
            require(isinstance(item, dict), f"page {index} claim_observability item must be an object")
            source = item.get("source_text")
            require(source in claims, f"page {index} observability source_text must match a page claim verbatim")
            require(item.get("class") in OBSERVABILITY_CLASSES, f"page {index} observability class is invalid")
            require(isinstance(item.get("evidence_relation"), str) and item["evidence_relation"].strip(), f"page {index} observability needs evidence_relation")
            observed_claims.add(source)
        require(observed_claims == set(claims), f"page {index} must classify every claim's observability")
        require(isinstance(page.get("proof_success_contract"), str) and page["proof_success_contract"].strip(), f"page {index} requires proof_success_contract")
        score = page.get("view_score")
        require(isinstance(score, dict) and not (VIEW_SCORE_FIELDS - set(score)), f"page {index} view_score is incomplete")
        require(all(isinstance(score[x], (int, float)) and 0 <= score[x] <= 100 for x in VIEW_SCORE_FIELDS), f"page {index} view_score values must be 0..100")
        reading_path = page.get("reading_path")
        require(isinstance(reading_path, list) and 2 <= len(reading_path) <= 4, f"page {index} reading_path must contain 2..4 scan stops")
        require(all(isinstance(x, str) and x.strip() for x in reading_path), f"page {index} reading_path must contain text")
        hierarchy = page.get("type_hierarchy")
        require(isinstance(hierarchy, dict), f"page {index} type_hierarchy must be an object")
        require(all(isinstance(hierarchy.get(x), str) and hierarchy[x].strip() for x in ("primary", "secondary", "support")), f"page {index} type_hierarchy requires primary, secondary and support")
        require(page.get("design_intensity") in {"LOW", "MEDIUM", "HIGH"}, f"page {index} design_intensity is invalid")
        anti = page.get("anti_template")
        require(isinstance(anti, dict), f"page {index} anti_template must be an object")
        require(isinstance(anti.get("background_rationale"), str) and anti["background_rationale"].strip(), f"page {index} needs product-derived background_rationale")
        require(isinstance(anti.get("module_strategy"), str) and anti["module_strategy"].strip(), f"page {index} needs module_strategy")
        require(isinstance(anti.get("avoided_defaults"), list) and anti["avoided_defaults"], f"page {index} needs avoided_defaults")
        require(anti.get("uses_repeated_equal_cards") is False, f"page {index} repeated equal-card templates are forbidden")
        require(isinstance(anti.get("max_information_modules"), int) and 0 <= anti["max_information_modules"] <= 3, f"page {index} max_information_modules must be 0..3")
        generic_background = anti["background_rationale"].strip().lower()
        require(generic_background not in {"蓝色渐变", "科技蓝", "默认蓝色科技背景", "generic blue gradient"}, f"page {index} background rationale is a generic template shortcut")
        generation = page.get("generation_contract")
        require(isinstance(generation, dict), f"page {index} generation_contract must be an object")
        require(generation.get("mode") in GENERATION_MODES, f"page {index} generation mode is invalid")
        target = generation.get("edit_target_evidence_id")
        support = generation.get("supporting_evidence_ids")
        require(isinstance(support, list) and set(support).issubset(set(page_evidence)), f"page {index} supporting evidence must be page evidence")
        for field in ("locked_product_invariants", "allowed_changes", "forbidden_changes"):
            require(isinstance(generation.get(field), list) and generation[field], f"page {index} generation_contract.{field} must be nonempty")
        if generation["mode"] == "PRODUCT_PRESERVING_EDIT":
            require(target in page_evidence, f"page {index} edit target must be one current-page evidence item")
            require(target not in support, f"page {index} edit target cannot also be supporting evidence")
        else:
            require(target is None, f"page {index} reference-guided generation cannot declare an edit target")
        if index in IDENTITY_SENSITIVE_PAGE_INDEXES:
            require(generation["mode"] == "PRODUCT_PRESERVING_EDIT", f"page {index} is identity-sensitive and must use PRODUCT_PRESERVING_EDIT")
    require(len({p["shot_key"]["layout_family"] for p in pages}) == 6, "all six layout families must be unique")
    require(pages[0]["shot_key"] != pages[1]["shot_key"], "search cover and carousel hero must use different shot keys")


def compile_prompt(session: dict[str, Any], page: dict[str, Any]) -> str:
    identity = session["identity_contract"]
    style = session["plan"]["campaign_style"]
    repair = page.get("repair_instruction")
    adjustment = page.get("active_user_adjustment")
    continuity = [
        "【V0.1.17 SESSION CONTINUITY】",
        "保持同一商品身份、同一视觉世界、本页职责、主卖点意义与原图背景隔离；内部合同保存在请求字段中，不要把整套JSON或其他五页施工细节写进画面提示词。",
        "campaign_constitution_sha256=" + str(session.get("campaign_constitution_sha256")) + "。",
        "用户修改是批准方案上的局部差量，不是新提示词；未被修改的硬锁继续生效，视觉执行仍保留镜头、构图与表现手法的专业判断空间。",
    ]
    if adjustment:
        continuity.extend(["【ACTIVE USER ADJUSTMENT DELTA】", canonical(adjustment)])
    if repair:
        continuity.extend([
            "【AUTOMATIC REPAIR CANVAS HARD LOCK】",
            "必须编辑失败的第一稿成片作为REPAIR_CANVAS_ONLY，保留其已重建的干净商业背景与已通过层；原始商品照片只核对身份，禁止再次使用完整原图作为编辑底板，禁止原图背景回流。",
        ])
    if page.get("v015_execution_prompt"):
        lines = [
            page["v015_execution_prompt"],
            f"【ACTIVE CONTRACT】request_id={session['request_id']}；campaign_style_id={session['campaign_style_id']}；revision={page.get('revision', 0)}。",
            "approved_page_contract_sha256=" + digest_value(page["approved_page_execution_contract"]) + "。",
            "reference_evidence_ids=" + "；".join(page["evidence_ids"]) + "。",
        ]
        lines.extend(continuity)
        if repair:
            lines.extend(["【TARGETED REPAIR — PRESERVE PASSED LAYERS】", canonical(repair)])
        return "\n".join(lines)
    if page.get("legacy_v013_generation_prompt"):
        legacy_execution_prompt = page.get("legacy_v013_execution_prompt", page["legacy_v013_generation_prompt"])
        commercial = session["plan"]["commercial_orchestration"]
        page_task = page["commercial_page_task"]
        lines = [
            "【V0.1.15 QUALITY EXECUTOR ENVELOPE AROUND V0.1.13 FACT AND IDENTITY COMPILATION】",
            f"request_id={session['request_id']}；page={page['index']}；role={page['page_role']}；revision={page.get('revision', 0)}。",
            "【V0.1.15 PRODUCT IDENTITY AND GENERATION METHOD LOCK】",
            identity["identity_summary"],
            "必须保留=" + "；".join(identity["must_preserve"]) + "。",
            "绝不改变=" + "；".join(identity["must_not_change"]) + "。",
            "generation_contract=" + canonical(page["generation_contract"]) + "。",
            f"active_campaign_style_id={session['campaign_style_id']}；active_campaign_style=" + canonical(style) + "。",
            "PRODUCT_PRESERVING_EDIT 必须使用指定 edit_target 原图作为编辑底图；支持图只用于核对，不得融合成新商品。",
            "【OUTPUT SURFACE HARD LOCK】canvas=1:1 square；不得因竖版原图改成长图、海报图或详情页比例；输出至少1024×1024。",
            "【V0.1.15 COMMERCIAL ORCHESTRATION CONTRACT】",
            f"purchase_decision_type={commercial['purchase_decision_type']}；page_strategy=" + canonical(commercial["page_strategy"]) + "。",
            "six_page_visual_rhythm=" + canonical(commercial["visual_rhythm_plan"]) + "。",
            "current_page_task=" + canonical(page_task) + "。",
            "execution_overlay=" + canonical(page["execution_overlay"]) + "。",
            "v0.1.13 的全部结构化合同和原始编译稿保存在会话中；当前执行稿必须先消解搜索首图与轮播身份页的职责冲突。",
            "【BEGIN V0.1.13 RESOLVED EXECUTION PROMPT】",
            legacy_execution_prompt,
            "【END V0.1.13 RESOLVED EXECUTION PROMPT】",
            "【V0.1.15 FINAL PRECEDENCE】",
            "当前商品身份 > USER_PROVIDED_DISPLAY_FACT > v0.1.13证据与设计契约 > 页面职责 > 可读性 > Campaign一致性 > 装饰。用户主动提供的内容直接使用，不再要求核验；仅删除模型自行添加的内容。",
            "任何与原图商品结构冲突的 v0.1.13 非事实视觉默认都必须简化或删除，不得重绘商品。",
        ]
        lines.extend(continuity)
        if repair:
            lines.extend(["【TARGETED REPAIR — PRESERVE PASSED LAYERS】", canonical(repair)])
        return "\n".join(lines)
    lines = [
        "【DOMESTIC E-COMMERCE QUALITY EXECUTOR v0.1.15】",
        f"request_id={session['request_id']}；page={page['index']}；role={page['page_role']}；revision={page.get('revision', 0)}。",
        "【PRODUCT IDENTITY LOCK】",
        identity["identity_summary"],
        "必须保留=" + "；".join(identity["must_preserve"]) + "。",
        "绝不改变=" + "；".join(identity["must_not_change"]) + "。",
        "仅使用当前页证据=" + "；".join(page["evidence_ids"]) + "；previous_generated_images_used_for_product_identity=false。",
        "【OUTPUT SURFACE HARD LOCK】canvas=1:1 square；不得输出竖版海报或详情长图；输出至少1024×1024。",
        "【USER-PROVIDED DISPLAY FACTS — VERBATIM AUTHORITY】",
        "；".join(page["claim_source_texts"]),
        "用户主动提供的商品参数、卖点、价格、促销、服务和场景默认就是本任务的展示事实，不得再次要求用户核验；只有模型自行添加且用户从未提供的内容必须自动删除。",
        "【FROZEN CAMPAIGN STYLE】",
        f"campaign_style_id={session['campaign_style_id']}；摄影={style['photography_finish']}；主光={style['light_direction']}；色温={style['color_temperature']}。",
        "色彩角色=" + canonical(style["palette_roles"]) + "；比例=" + canonical(style["palette_usage_ratio"]) + "。",
        "字体角色=" + canonical(style["typography"]) + "；共享图形=" + str(style["graphic_language"]) + "。",
        f"信息面={style['information_surface_geometry']}；密度={style['density_band']}；禁用=" + "；".join(style["prohibited_styles"]) + "。",
        "【PAGE DUTY AND CONCRETE EXECUTION】",
        f"买家疑虑={page['buyer_objection']}；无文案也必须可见的证明={page['visible_proof']}。",
        "shot_key=" + canonical(page["shot_key"]) + "。",
        "composition=" + canonical(page["composition"]) + "。",
        f"缩略图成功条件={page['thumbnail_success']}。",
        f"与前页差异={page.get('differs_from_previous', '本页为搜索入口')}。",
        "商业页面任务=" + canonical(session["plan"]["commercial_orchestration"]["page_task_table"][page["index"]]) + "。",
        "【DESIGN INTELLIGENCE — NOT A TEMPLATE】",
        "整套策略=" + canonical(session["plan"]["design_intelligence"]) + "。",
        "可观察性=" + canonical(page["claim_observability"]) + "。",
        f"无文案证明成功契约={page['proof_success_contract']}；视角评分=" + canonical(page["view_score"]) + "。",
        "阅读路径=" + " → ".join(page["reading_path"]) + "；字级=" + canonical(page["type_hierarchy"]) + f"；设计强度={page['design_intensity']}。",
        "反模板决策=" + canonical(page["anti_template"]) + "。",
        "【GENERATION METHOD CONTRACT】",
        canonical(page["generation_contract"]),
        "若 mode=PRODUCT_PRESERVING_EDIT，必须把 edit_target_evidence_id 对应原图作为编辑底图，保持商品像素级结构、比例、部件与颜色；只允许改 allowed_changes。其余证据只能辅助核对，不得融合出新商品。",
        "原图背景通常是拍摄噪声；除非用户明确要求保留，否则只取商品身份与视角证据并重建背景。",
        "每个标签、指针、宏观特写、光效和人物动作必须落到当前商品可见证据；证据不足时删除，不得虚构。",
        "商品身份 > 事实合规 > 页面职责 > 可读性 > Campaign一致性 > 版式差异 > 装饰。",
    ]
    lines.extend(continuity)
    if repair:
        lines.extend(["【TARGETED REPAIR — PRESERVE PASSED LAYERS】", canonical(repair)])
    return "\n".join(lines)


def background_style_lock(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "photography_finish": copy.deepcopy(style["photography_finish"]),
        "light_direction": copy.deepcopy(style["light_direction"]),
        "color_temperature": copy.deepcopy(style["color_temperature"]),
        "palette_roles": copy.deepcopy(style["palette_roles"]),
        "graphic_language": copy.deepcopy(style["graphic_language"]),
        "information_surface_geometry": copy.deepcopy(style["information_surface_geometry"]),
        "rule": "六页共享同一美术方向：保持色彩角色、材质/图形语言、字体气质与摄影完成度可识别；允许按页面职责改变场景、光向、景深、明暗重心和构图。",
    }


def compile_session(session: dict[str, Any], plan: dict[str, Any]) -> None:
    validate_commercial_orchestration(plan.get("commercial_orchestration"))
    session["plan"] = copy.deepcopy(plan)
    session["commercial_contract_hash"] = digest_value(session["plan"]["commercial_orchestration"])
    session["campaign_style_id"] = "CAMPAIGN-" + digest_value(
        {"request_id": session["request_id"], "style": plan["campaign_style"]}
    )[:12].upper()
    session["campaign_constitution"] = {
        "identity_contract_sha256": session.get("identity_contract_sha256"),
        "product_identity_lock": {
            "product_form": session["identity_contract"]["product_form"],
            "identity_summary": session["identity_contract"]["identity_summary"],
            "must_preserve": copy.deepcopy(session["identity_contract"]["must_preserve"]),
            "must_not_change": copy.deepcopy(session["identity_contract"]["must_not_change"]),
            "rule": "六页与所有返修必须保持同一商品轮廓、比例、颜色分区、包装版式、部件数量、连接关系、开合结构和关键标签；卖点表现不得重设计商品。",
        },
        "campaign_style_id": session["campaign_style_id"],
        "campaign_style": copy.deepcopy(plan["campaign_style"]),
        "background_style_lock": background_style_lock(plan["campaign_style"]),
        "commercial_orchestration_sha256": digest_value(plan["commercial_orchestration"]),
        "six_page_duties": [
            {
                "page_index": page["index"],
                "page_role": page["page_role"],
                "lead_claim": page["approved_page_execution_contract"]["selling_point_visual_proof_contract"]["source_text"],
            }
            for page in plan["pages"]
        ],
        "source_scene_isolation_contract": {
            "default": "SOURCE_BACKGROUND_IS_CAPTURE_NOISE",
            "rule": "只提取商品身份、视角和真实结构；除非用户明确批准保留某一背景元素，否则必须重建场景，返修与用户中途修改均不得恢复原图桌面、墙面、缝隙、杂物、键盘或拍摄环境。",
            "survives_repairs_and_user_adjustments": True,
            "automatic_repair_canvas_rule": "自动返修必须编辑第一稿成片作为REPAIR_CANVAS_ONLY；原图只作商品身份核对，不得再次以整张原始照片作为修图底板。",
        },
        "continuity_rule": "每次生图重载商品身份、背景视觉世界、六页职责摘要、当前页战略合同和局部差量；内部合同用于约束事实与连续性，不作为逐像素施工图。",
    }
    session["campaign_constitution_sha256"] = digest_value(session["campaign_constitution"])
    for page in session["plan"]["pages"]:
        page.setdefault("revision", 0)
        page["generation_prompt"] = compile_prompt(session, page)
    session["compiled_hash"] = compiled_contract_hash(session)
    session["authorization"] = None
    session["pending_generation"] = None


def compiled_contract_hash(session: dict[str, Any]) -> str:
    return digest_value(
        {
            "identity": session["identity_contract"],
            "bindings": session["plan"]["reference_bindings"],
            "style_id": session["campaign_style_id"],
            "commercial_orchestration": session["plan"]["commercial_orchestration"],
            "pages": session["plan"]["pages"],
            "campaign_constitution_sha256": session.get("campaign_constitution_sha256"),
        }
    )


def next_page_index(session: dict[str, Any]) -> int | None:
    accepted = set(session.get("accepted_page_indexes", []))
    for index in range(6):
        if index not in accepted:
            return index
    return None


def allowed_actions(state: str) -> list[str]:
    return {
        "AWAITING_IDENTITY": ["lock-identity", "inspect"],
        "IDENTITY_LOCKED": ["compile-v013", "compile", "inspect"],
        "AWAITING_AUTHORIZATION": ["authorize", "replan", "restart-campaign", "inspect"],
        "AUTHORIZED": ["generate-next", "replan", "restart-campaign", "inspect"],
        "AWAITING_GENERATED_ASSET": ["record-generation", "restart-campaign", "inspect"],
        "AWAITING_VISUAL_AUDIT": ["audit-current", "restart-campaign", "inspect"],
        "REPAIR_REQUIRED": ["prepare-repair", "apply-user-adjustment", "replan", "restart-campaign", "inspect"],
        "AWAITING_USER_INTERVENTION": ["apply-user-adjustment", "restart-campaign", "inspect"],
        "AWAITING_SEARCH_BASELINE_CALIBRATION": ["calibrate-search-baseline", "restart-campaign", "inspect"],
        "AWAITING_AGENT_STYLE_CALIBRATION": ["calibrate-style", "restart-campaign", "inspect"],
        "AWAITING_SET_ROLE_AUDIT": ["audit-set-role-distinction", "restart-campaign", "inspect"],
        "SIX_PAGES_ACCEPTED": ["inspect"],
        "AWAITING_USER_FINAL_REVIEW": ["finalize", "apply-user-adjustment", "inspect"],
        "FINALIZED": ["apply-user-adjustment", "inspect"],
    }.get(state, ["inspect"])
