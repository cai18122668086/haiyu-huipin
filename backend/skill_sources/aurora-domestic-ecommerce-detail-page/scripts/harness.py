from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, UnidentifiedImageError

from runtime.audit_evidence import inspect_pair_audit, record_pair_audit
from runtime.local_revision_review import revision_review_contract, validate_revision_observation
from runtime.local_contracts import scope_contract_override, effective_plan, validate_request_contracts
from runtime.delivery_manifest import build_final_manifest, inspect_final_manifest
from runtime.assembly import assemble_vertical
from runtime.artifact_binding import file_sha256, validate_artifact_binding
from runtime.compliance_contract import validate_high_risk_cosmetic_claims, validate_high_risk_plan_copy
from runtime.identity_scope import pair_observed_scope
from runtime.image_format import ensure_format_canvas, image_dimensions, normalize_generated_image
from runtime.prompt_compiler import compile_pair_prompt, compile_prompt
from runtime.performance_contract import resolve_performance_contract
from runtime.atomic_review import review_contracts, validate_atomic_reports
from runtime.website_review import schema_name as review_schema_name
from runtime.pixel_lineage import create_fixed_slices, pair_artifacts, preserve_provider_original, preserve_signed_request
from runtime.plan_normalizer import (
    COMPACT_PLAN_FORMAT,
    normalize_plan,
    style_constitution_errors,
    validate_expression_signature,
)
from runtime.sales_depth_contract import validate_sales_depth_contract, validate_formula_split_contract
from runtime.sales_architecture import (
    validate_dynamic_count_formula,
    validate_user_selling_point_ledger,
    validate_visual_archetypes,
)
from runtime.story_contract import validate_story_contract
from runtime.visual_diversity_contract import (
    validate_campaign_expression_diversity,
    validate_observed_expression_diversity,
)
from runtime.spatial_contract import resolve_spatial_contract, validate_spatial_contract
from runtime.state import (
    HarnessError,
    allowed_actions,
    atomic_write_json,
    atomic_write_text,
    current_module,
    load_json,
    require_fields,
    require_state,
)
from runtime.visual_gate import require_identity_silhouette, required_long_page_checks, validate_long_page_judgment, validate_module_judgment
from runtime.information_contract import validate_information_contract
from runtime.schema_validation import validate_schema


VERSION = "4.3.1"
COMPATIBLE_SESSION_VERSIONS = {VERSION}

INTERNAL_REVIEW_PROFILE_NAME = "INTERNAL_REVIEW_1440_3_4"
INTERNAL_REVIEW_RATIO = {"width_units": 3, "height_units": 4, "label": "3:4"}
INTERNAL_REVIEW_WIDTH_PX = 1440
INTERNAL_REVIEW_HEIGHT_PX = 1920
UNVERIFIED_ADAPTIVE_RATIO_OPTIONS = {"3:4", "9:16"}

FORBIDDEN_PIXEL_FACING_PLAN_PHRASES = (
    "包装可见", "作为包装宣称", "包装宣称来源", "来源边界", "证据边界",
    "风险核对", "风险澄清", "核对提醒", "当前证据不足", "未提供检测",
    "以实际商品页为准", "以实际包装为准", "以包装标示为准", "以包装标识为准",
    "购买前以实际规格", "购买前请核对", "包装标示", "包装标识", "当前仅能确认",
    "缺少检测报告", "待验证", "待核验", "未确认", "来源说明", "风险提示",
)


COMPLETE_STATUSES = {"COMPLETE", "COMPLETE_WITH_RESIDUALS"}


# --- 产品几何形态锁（产品一致性 B 层，详见 skill `ecom-product-identity-consistency`）---
# 产品一致性出错的头号原因是：身份锁里只有「瓶身 / 圆形」这类空泛词，没有具体的
# 截面形状、宽高比、结构分段。这些词表用于在出图前强制 `invariant_features` 里
# 至少写出 3 类具体几何描述，否则 lock-identity 直接 BLOCKED。与 visual_gate.py
# 里已有的 `require_identity_silhouette`（出图后形态硬门禁）配套，补齐「出图前」这一环。
GEOMETRY_FACETS = (
    "截面", "横截面", "圆角矩形", "椭圆", "方柱", "圆柱", "跑道", "扁",
    "宽高比", "比例", "长宽比", "总高", "正面宽",
    "接缝", "分段", "一体式", "分体", "盖", "瓶盖", "瓶身", "瓶肩", "瓶颈",
    "螺纹", "颈环", "托环", "滚珠", "出液口", "泵头", "喷头", "把手", "底座",
    "圆角", "倒角", "直径", "外径", "厚度", "高度", "宽度",
)


def _is_geometry_clause(clause: str) -> bool:
    """一条 invariant_feature 是否算「具体几何描述」。

    命中任一几何 facet 词，或包含数字比例（如 `1:1.9`、`宽:高 1:2`），都算。
    """
    if not isinstance(clause, str) or not clause.strip():
        return False
    text = clause.strip()
    if any(facet in text for facet in GEOMETRY_FACETS):
        return True
    # 数字比例模式：含冒号且两侧有数字，或「X比Y」
    import re as _re
    if _re.search(r"\d+\s*[:：比]\s*\d+", text):
        return True
    return False


def _geometry_clause_count(identity: dict) -> int:
    """统计 identity 里「具体几何描述」的条款数（跨 invariant_features / allowed_states）。"""
    count = 0
    for key in ("invariant_features", "allowed_states"):
        for clause in identity.get(key, []):
            if _is_geometry_clause(clause):
                count += 1
    return count


def _require_geometry_clauses(identity: dict, *, minimum: int = 3) -> None:
    """强制身份锁写出 ≥3 条具体几何形态描述，否则拒绝锁定。"""
    count = _geometry_clause_count(identity)
    if count < minimum:
        raise HarnessError(
            f"Identity 未锁定产品几何形态：invariant_features / allowed_states 中只有 {count} 条具体几何描述，"
            f"至少需要 {minimum} 条。请按真实产品照片写出截面形状（如圆角矩形截面）、宽高比（如正面宽:总高≈1:1.9）、"
            f"结构分段（如一体式盖与瓶身同宽+水平接缝）等可量化形态，避免模型回退到品类通用款。"
        )


def _pair_rendering(session: dict) -> dict | None:
    strategy = (session.get("plan") or {}).get("output_strategy") or {}
    config = strategy.get("continuous_pair_rendering")
    return config if isinstance(config, dict) and config.get("enabled") else None


def _require_pair_generation_contract(session: dict) -> None:
    """Fail closed instead of silently continuing a legacy single-screen run."""
    strategy = (session.get("plan") or {}).get("output_strategy") or {}
    module_count = strategy.get("module_count")
    if not isinstance(module_count, int) or module_count < 2 or module_count > 24 or module_count % 2:
        return
    config = strategy.get("continuous_pair_rendering")
    if not isinstance(config, dict) or config.get("enabled") is not True:
        raise HarnessError(
            "This session was compiled with the legacy single-screen generation contract. "
            "It cannot continue under Skill v4.3; start a new session and recompile the approved product-driven screen plan "
            "so every ordered adjacent pair is merged into one 3:8 prompt before image generation."
        )
    if config.get("pair_merge_type") != "PAIR_PLAN_MERGE":
        raise HarnessError(
            "This session predates the v4.1 pair-merge lock; recompile the approved group→screen plan "
            "so every pair declares pair_merge_type=PAIR_PLAN_MERGE before image generation."
        )


def _pair_for_index(session: dict, index: int) -> dict | None:
    config = _pair_rendering(session)
    if not config:
        return None
    modules = session.get("modules", [])
    if index < 0 or index >= len(modules):
        return None
    module_id = modules[index]["id"]
    for pair in config.get("pair_ids", []):
        if module_id in pair.get("module_ids", []):
            return pair
    return None


def _pair_modules(session: dict, index: int) -> list[dict]:
    pair = _pair_for_index(session, index)
    if not pair:
        return [current_module(session)]
    by_id = {item["id"]: item for item in session.get("modules", [])}
    return [by_id[module_id] for module_id in pair["module_ids"] if module_id in by_id]


def _pair_canvas_slices(source: str | Path, session: dict, pair: dict, attempt: int) -> list[dict]:
    strategy = session["plan"]["output_strategy"]
    return create_fixed_slices(
        Path(source), Path(session["session_dir"]), pair["pair_id"], pair["module_ids"],
        int(strategy["canvas_width_px"]), int(strategy["module_height_px"]), attempt,
    )


def _pair_prompt(session: dict, modules: list[dict], *, repair: bool, format_canvas_lock: bool) -> tuple[str, list[str]]:
    # Keep this compatibility wrapper for callers that still pass the old
    # format-canvas flag. Pair generation now always compiles the two atomic
    # screen contracts first, then merges them into one pair-level prompt.
    del format_canvas_lock
    return compile_pair_prompt(session, modules, repair=repair)


def _unique_strings(values) -> list[str]:
    result = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def validate_operating_input(raw: dict, fact_ids: set[str]) -> None:
    """Validate only the new route-aware contract; legacy inputs remain readable."""
    if "operating_analysis" not in raw:
        return
    route = raw.get("intake_route")
    if route not in ("USER_SELLING_POINT_LED", "PHOTO_TO_PRODUCT_STRATEGY"):
        raise HarnessError("Route-aware input must select a supported intake_route")
    supplied = raw.get("user_supplied_selling_points")
    if not isinstance(supplied, list):
        raise HarnessError("user_supplied_selling_points must be a list")
    if route == "USER_SELLING_POINT_LED" and not supplied:
        raise HarnessError("USER_SELLING_POINT_LED requires at least one concrete user-supplied selling point")
    if route == "PHOTO_TO_PRODUCT_STRATEGY" and supplied:
        raise HarnessError("PHOTO_TO_PRODUCT_STRATEGY cannot hide user-supplied selling points")
    supplied_ids = []
    for point in supplied:
        require_fields(point, ("id", "text", "source_ref", "fact_ids"), "user-supplied selling point")
        supplied_ids.append(point["id"])
        unknown = set(point["fact_ids"]) - fact_ids
        if unknown:
            raise HarnessError(f"User selling point {point['id']} refers to unknown atomic facts: {sorted(unknown)}")
    if len(supplied_ids) != len(set(supplied_ids)):
        raise HarnessError("User-supplied selling-point IDs must be unique")

    atomic_keys = []
    for fact in raw["verified_facts"]:
        require_fields(fact, ("atomic_key", "atomicity"), f"route-aware atomic fact {fact.get('id', '<unknown>')}")
        if fact["atomicity"] != "ONE_INDEPENDENT_PROPOSITION":
            raise HarnessError("Route-aware facts must each contain one independently coverable proposition")
        atomic_keys.append(fact["atomic_key"])
    if len(atomic_keys) != len(set(atomic_keys)):
        raise HarnessError("Atomic fact keys must be unique; split or deduplicate compound meanings")

    analysis = raw["operating_analysis"]
    require_fields(
        analysis,
        (
            "route_reason", "confirmed_fact_ids", "buyer_hypotheses", "pain_hypotheses",
            "value_bridges", "positioning_candidates", "selected_positioning_id",
            "assumption_disclosure",
        ),
        "operating analysis",
    )
    if "evidence_gaps" not in analysis or not isinstance(analysis["evidence_gaps"], list):
        raise HarnessError("operating_analysis.evidence_gaps must be a list; it may be empty")
    if set(analysis["confirmed_fact_ids"]) != fact_ids:
        raise HarnessError("operating_analysis.confirmed_fact_ids must account for every atomic verified fact")
    buyers = analysis["buyer_hypotheses"]
    pains = analysis["pain_hypotheses"]
    bridges = analysis["value_bridges"]
    candidates = analysis["positioning_candidates"]
    if not buyers or not pains or not bridges or not candidates:
        raise HarnessError("Operating analysis requires buyer, pain, value-bridge and positioning reasoning")
    buyer_ids = [item.get("id") for item in buyers]
    pain_ids = [item.get("id") for item in pains]
    if len(buyer_ids) != len(set(buyer_ids)) or any(not item for item in buyer_ids):
        raise HarnessError("Buyer-hypothesis IDs must be unique and non-empty")
    if len(pain_ids) != len(set(pain_ids)) or any(not item for item in pain_ids):
        raise HarnessError("Pain-hypothesis IDs must be unique and non-empty")
    for bridge in bridges:
        require_fields(
            bridge,
            ("id", "fact_ids", "pain_id", "feature", "buyer_benefit", "purchase_reason", "visual_route", "claim_permission"),
            "evidence-to-value bridge",
        )
        if set(bridge["fact_ids"]) - fact_ids:
            raise HarnessError(f"Value bridge {bridge['id']} uses unknown facts")
        if bridge["pain_id"] not in set(pain_ids):
            raise HarnessError(f"Value bridge {bridge['id']} uses an unknown pain hypothesis")
    candidate_ids = [item.get("id") for item in candidates]
    selected = [item for item in candidates if item.get("decision") == "SELECTED"]
    if len(candidate_ids) != len(set(candidate_ids)) or any(not item for item in candidate_ids):
        raise HarnessError("Positioning-candidate IDs must be unique and non-empty")
    if len(selected) != 1 or selected[0]["id"] != analysis["selected_positioning_id"]:
        raise HarnessError("Exactly one positioning candidate must be selected and match selected_positioning_id")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generation_references(session: dict) -> Tuple[List[str], Optional[str]]:
    """Return the product evidence references for the current render.

    This function used to hand the first accepted screen to every later screen
    as a "series master" raster. The 2026-09-17 trash-bin campaign showed why
    that must never happen: the master carried the first screen's layout and
    its own drift into every following pair (M01's accepted attempt was
    slightly off-model, and M03/M05 inherited the drift — round body, wrong
    material), which is exactly the failure `cross-module-continuity.md`
    line "不得把前屏完整海报缩小后当作后屏参考" forbids.

    Style continuity now travels through two channels only: the written
    visual passport (palette roles, typography character, graphic language —
    compiled into the prompt) and the bottom edge strip produced by
    `continuity_edge_reference`. Product identity comes solely from the
    user's original product images below.
    """
    master = None

    # The previous implementation encoded one cat-food SKU's role names here.
    # Preserve the user's identity-lock order instead, then use generic evidence
    # hints that work for every category.
    identity_order = {
        image_id: index
        for index, image_id in enumerate(session["identity"]["reference_image_ids"])
    }
    generic_role_priority = (
        "front", "identity", "hero", "overall", "product",
        "side", "back", "label", "detail", "component", "package", "scale", "scene",
    )

    def candidate_key(item: dict) -> tuple:
        role = item.get("role", "").lower()
        role_rank = next(
            (index for index, token in enumerate(generic_role_priority) if token in role),
            len(generic_role_priority),
        )
        return (identity_order.get(item["id"], 999), role_rank, item["id"])
    allowed_ids = set(session["identity"]["reference_image_ids"])
    candidates = [
        item for item in session["input"]["product_images"]
        if item["id"] in allowed_ids
    ]
    candidates.sort(key=candidate_key)

    references: List[str] = []
    if master:
        references.append(master)
    for item in candidates:
        if item["path"] not in references:
            references.append(item["path"])
        if len(references) == 5:
            break
    return references, master


def continuity_edge_reference(session: dict, session_path: str | Path) -> Optional[str]:
    """Create a small, non-layout-locking handoff strip from the accepted prior screen."""
    current_index = session.get("current_index", 0)
    modules = session.get("modules", [])
    if current_index <= 0 or current_index >= len(modules):
        return None
    previous = modules[current_index - 1].get("accepted_asset")
    if not previous or not Path(previous).is_file():
        return None
    try:
        from PIL import Image
        with Image.open(previous) as image:
            image = image.convert("RGB")
            strip_height = max(48, int(round(image.height * 0.08)))
            strip = image.crop((0, image.height - strip_height, image.width, image.height))
            out = Path(session_path).resolve().parent / "edge-states" / f"M{current_index + 1:02d}-incoming.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            strip.save(out, format="PNG", optimize=True)
            strip.close()
        return str(out)
    except Exception:
        return None


def save(session_path: str, session: dict, event: str) -> None:
    session.setdefault("events", []).append({"at": now(), "event": event, "state": session["state"]})
    session["updated_at"] = now()
    atomic_write_json(session_path, session)


def _seconds_between_events(session: dict) -> list[dict]:
    events = session.get("events", [])
    result = []
    for previous, current in zip(events, events[1:]):
        try:
            start = datetime.fromisoformat(previous["at"])
            end = datetime.fromisoformat(current["at"])
        except (KeyError, TypeError, ValueError):
            continue
        result.append({
            "from": previous.get("event"),
            "to": current.get("event"),
            "seconds": round((end - start).total_seconds(), 3),
        })
    return result


def _planning_elapsed_seconds(session: dict) -> Optional[float]:
    locked = next((item for item in reversed(session.get("events", [])) if item.get("event") == "identity_locked"), None)
    if not locked:
        return None
    try:
        return round((datetime.now(timezone.utc) - datetime.fromisoformat(locked["at"])).total_seconds(), 3)
    except (KeyError, TypeError, ValueError):
        return None


def cmd_start(args: argparse.Namespace) -> dict:
    raw = load_json(args.input)
    validate_schema(raw, "input.schema.json", "product input")
    require_fields(raw, ("product_name", "category", "target_buyer", "product_images", "verified_facts", "platform_profile"), "input")
    for fact in raw["verified_facts"]:
        if fact.get("claim_kind") in {"INGREDIENT_FORMULA", "STRUCTURE_COMPONENT"}:
            if fact.get("display_intent") == "MUST_SHOW" and fact.get("user_prominence") == "CORE_SELLING_POINT" and not fact.get("named_elements"):
                raise HarnessError(
                    f"Core MUST_SHOW formula/ingredient/structure fact {fact.get('id')} needs at least one verified named_elements entry"
                )
    profile = raw["platform_profile"]
    require_fields(profile, ("target_platform", "profile_name", "selection_mode", "selection_rationale", "source", "aspect_ratio", "canvas_width_px", "module_height_px", "safe_margin_px", "slice_height_px", "verification_status"), "platform profile")
    if profile["selection_mode"] not in ("ADAPTIVE_PLANNED", "USER_SPECIFIED", "PLATFORM_VERIFIED"):
        raise HarnessError("Platform profile must be deliberately selected before session start")
    if len(profile["selection_rationale"].strip()) < 20:
        raise HarnessError("Platform-profile selection requires a product-specific ratio and dimension rationale")
    ratio = profile["aspect_ratio"]
    require_fields(ratio, ("width_units", "height_units", "label"), "platform aspect ratio")
    if ratio["label"] != f"{ratio['width_units']}:{ratio['height_units']}":
        raise HarnessError("Platform aspect-ratio label does not match its numeric units")
    if profile["canvas_width_px"] * ratio["height_units"] != profile["module_height_px"] * ratio["width_units"]:
        raise HarnessError("Platform-profile dimensions do not match the declared aspect ratio")
    if (
        profile["selection_mode"] == "ADAPTIVE_PLANNED"
        and profile["verification_status"] != "VERIFIED_CURRENT_BACKEND"
    ):
        expected = (
            profile["profile_name"] == INTERNAL_REVIEW_PROFILE_NAME
            and ratio == INTERNAL_REVIEW_RATIO
            and profile["canvas_width_px"] == INTERNAL_REVIEW_WIDTH_PX
            and profile["module_height_px"] == INTERNAL_REVIEW_HEIGHT_PX
        )
        if not expected:
            raise HarnessError(
                "Platform-unverified ADAPTIVE_PLANNED work must use the internal-review master "
                "INTERNAL_REVIEW_1440_3_4 at 1440x1920, 3:4. Use USER_SPECIFIED only for an "
                "explicit user-approved profile, or PLATFORM_VERIFIED for a current backend rule."
            )
    raw.setdefault("coverage_directive", "MAXIMIZE_USER_SUPPLIED_RELEVANT_FACTS")
    raw.setdefault("question_budget", 0)
    if raw["coverage_directive"] != "MAXIMIZE_USER_SUPPLIED_RELEVANT_FACTS":
        raise HarnessError("Detail-page runs must maximize relevant user-supplied fact coverage")
    if raw["question_budget"] != 0:
        raise HarnessError("Default clarification-question budget must be zero")
    raw.setdefault("conflict_policy", "BLOCK_PLAN_AND_BATCH_ASK")
    raw.setdefault("source_conflicts", [])
    raw.setdefault("reference_materials", [])
    raw.setdefault("conflict_scan", {
        "status": "CLEAR" if not raw["source_conflicts"] else "CONFLICTS_FOUND",
        "scanned_topics": ["identity", "specification", "formula", "performance", "audience", "compliance"],
        "conflict_ids": [item.get("id") for item in raw["source_conflicts"]],
    })
    if raw["conflict_policy"] != "BLOCK_PLAN_AND_BATCH_ASK":
        raise HarnessError("Conflict policy must block planning and batch the necessary user confirmation")
    scan = raw["conflict_scan"]
    require_fields(scan, ("status", "scanned_topics"), "conflict scan")
    declared_conflict_ids = [item.get("id") for item in raw["source_conflicts"]]
    if len(declared_conflict_ids) != len(set(declared_conflict_ids)) or any(not item for item in declared_conflict_ids):
        raise HarnessError("Source conflict IDs must be unique and non-empty")
    if set(scan.get("conflict_ids", [])) != set(declared_conflict_ids):
        raise HarnessError("conflict_scan.conflict_ids must exactly match source_conflicts")
    expected_scan_status = "CONFLICTS_FOUND" if raw["source_conflicts"] else "CLEAR"
    if scan["status"] != expected_scan_status:
        raise HarnessError("Conflict scan status does not match the declared source conflicts")
    known_fact_ids = {item["id"] for item in raw["verified_facts"]}
    validate_operating_input(raw, known_fact_ids)
    known_reference_unit_ids = set()
    for reference in raw["reference_materials"]:
        require_fields(reference, ("id", "path", "category", "analysis_status", "observed_expression_methods"), "reference material")
        if "analyzed_units" not in reference or not isinstance(reference["analyzed_units"], list) or not reference["analyzed_units"]:
            raise HarnessError(f"Reference {reference.get('id', '<unknown>')} must be decomposed into at least one individual analyzed unit")
        if reference["analysis_status"] != "ANALYZED":
            raise HarnessError(f"Reference {reference['id']} must be decomposed before session start")
        unit_methods = []
        for unit in reference["analyzed_units"]:
            require_fields(
                unit,
                ("id", "source_region", "page_duty", "visual_carrier", "composition_logic", "typography_logic", "observed_expression_method"),
                f"analyzed reference unit for {reference['id']}",
            )
            if unit["id"] in known_reference_unit_ids:
                raise HarnessError(f"Analyzed reference unit ID must be globally unique: {unit['id']}")
            known_reference_unit_ids.add(unit["id"])
            unit_methods.append(unit["observed_expression_method"])
        if set(unit_methods) != set(reference["observed_expression_methods"]):
            raise HarnessError(
                f"Reference {reference['id']} observed_expression_methods must be derived exactly from its individual analyzed units"
            )
    for conflict in raw["source_conflicts"]:
        require_fields(conflict, ("id", "topic", "alternative_fact_ids", "why_material", "status"), "source conflict")
        alternatives = conflict["alternative_fact_ids"]
        if len(alternatives) < 2 or len(alternatives) != len(set(alternatives)):
            raise HarnessError(f"Conflict {conflict['id']} must contain at least two distinct fact alternatives")
        unknown = set(alternatives) - known_fact_ids
        if unknown:
            raise HarnessError(f"Conflict {conflict['id']} refers to unknown facts: {sorted(unknown)}")
        if conflict["status"] not in ("UNRESOLVED", "RESOLVED"):
            raise HarnessError(f"Conflict {conflict['id']} has an invalid status")
    for fact in raw["verified_facts"]:
        if "risk_route" not in fact:
            fact["risk_route"] = (
                "USER_CONFIRMED_FACT" if fact["source_class"] == "USER_FACT"
                else "DOCUMENTED_FACT"
            )
        fact.setdefault("display_intent", "SHOW_IF_RELEVANT")
    validate_high_risk_cosmetic_claims(raw)
    unresolved = [item for item in raw["source_conflicts"] if item["status"] == "UNRESOLVED"]
    if any(item["status"] == "RESOLVED" and not item.get("resolution") for item in raw["source_conflicts"]):
        raise HarnessError("Resolved source conflicts must preserve their user-confirmed resolution")
    session = {
        "version": VERSION,
        "session_dir": str(Path(args.session).resolve().parent),
        "state": "AWAITING_CONFLICT_RESOLUTION" if unresolved else "AWAITING_IDENTITY",
        "created_at": now(),
        "updated_at": now(),
        "input": raw,
        "identity": None,
        "plan": None,
        "approval": None,
        "modules": [],
        "current_index": 0,
        "active_token": None,
        "active_render_container": None,
        "generation_requests": [],
        "candidate_generation_imports": [],
        "assembly": None,
        "long_page_audit": None,
        "final": None,
        "feedback_constraints": {
            "global": {"preserve": [], "must_not_regress": []},
            "by_module": {},
        },
        "planning_fact_ids": sorted(known_fact_ids),
        "conflict_checkpoint": {
            "required": bool(unresolved),
            "conflict_ids": [item["id"] for item in unresolved],
            "instruction": "Ask one batched question listing the exact conflicting values and wait for the user's decision before identity lock or full planning.",
        },
        "events": [],
    }
    save(args.session, session, "session_started")
    return summary(session)


def cmd_resolve_conflicts(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_CONFLICT_RESOLUTION")
    resolution = load_json(args.resolution)
    validate_schema(resolution, "conflict-resolution.schema.json", "conflict resolution")
    require_fields(resolution, ("confirmation_text", "resolutions"), "conflict resolution")
    if not str(resolution["confirmation_text"]).strip():
        raise HarnessError("Conflict resolution must preserve the user's confirmation words")
    conflicts = {item["id"]: item for item in session["input"].get("source_conflicts", []) if item["status"] == "UNRESOLVED"}
    supplied = {item.get("conflict_id"): item for item in resolution["resolutions"]}
    if len(supplied) != len(resolution["resolutions"]) or set(supplied) != set(conflicts):
        raise HarnessError("One batched resolution must cover every unresolved conflict exactly once")
    rejected_fact_ids = set()
    retained_fact_ids = set()
    for conflict_id, item in supplied.items():
        require_fields(item, ("conflict_id", "retained_fact_ids", "rejected_fact_ids"), "conflict resolution item")
        conflict = conflicts[conflict_id]
        retained = set(item["retained_fact_ids"])
        rejected = set(item["rejected_fact_ids"])
        alternatives = set(conflict["alternative_fact_ids"])
        if not retained or retained & rejected or retained | rejected != alternatives:
            raise HarnessError(f"Resolution for {conflict_id} must retain at least one alternative and account for all others")
        conflict["status"] = "RESOLVED"
        conflict["resolution"] = {
            "confirmation_text": resolution["confirmation_text"],
            "retained_fact_ids": sorted(retained),
            "rejected_fact_ids": sorted(rejected),
        }
        rejected_fact_ids.update(rejected)
        retained_fact_ids.update(retained)
    if retained_fact_ids & rejected_fact_ids:
        raise HarnessError("A fact cannot be both retained and rejected across conflicts")
    all_fact_ids = {item["id"] for item in session["input"]["verified_facts"]}
    session["planning_fact_ids"] = sorted(all_fact_ids - rejected_fact_ids)
    session["conflict_checkpoint"] = {"required": False, "conflict_ids": [], "resolved_at": now()}
    session["state"] = "AWAITING_IDENTITY"
    save(args.session, session, "conflicts_resolved")
    return summary(session)


def cmd_lock_identity(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_IDENTITY")
    if any(item.get("status") == "UNRESOLVED" for item in session["input"].get("source_conflicts", [])):
        raise HarnessError("Identity lock is forbidden until every material source conflict is user-resolved")
    identity = load_json(args.identity)
    validate_schema(identity, "identity.schema.json", "identity contract")
    require_fields(identity, ("canonical_name", "category", "reference_image_ids", "invariant_features", "allowed_states", "forbidden_mutations"), "identity")
    if session["input"].get("website_contract_version") == "WEBSITE_DIRECT_V2":
        from runtime.website_plan import validate_source_identity
        validate_source_identity(session["input"], identity)
    else:
        _require_geometry_clauses(identity)
    if "uncertain_features" not in identity or not isinstance(identity["uncertain_features"], list):
        raise HarnessError("identity uncertain_features must be present as an array; it may be empty when no uncertainty remains")
    known_ids = {item["id"] for item in session["input"]["product_images"]}
    unknown = set(identity["reference_image_ids"]) - known_ids
    if unknown:
        raise HarnessError(f"Identity refers to unknown product images: {sorted(unknown)}")
    logo_source = identity.get("brand_logo_source")
    if not isinstance(logo_source, dict):
        raise HarnessError(
            "Identity requires brand_logo_source; use mode BRANDLESS with brandless_evidence when the product carries no brand mark"
        )
    require_fields(
        logo_source,
        ("image_id", "authorization_basis", "readability", "usage"),
        "brand logo source",
    )
    brand_mode = logo_source.get("mode", "BRANDED")
    if brand_mode not in ("BRANDED", "BRANDLESS"):
        raise HarnessError("Brand logo source mode must be BRANDED or BRANDLESS")
    if brand_mode == "BRANDLESS":
        if not str(logo_source.get("brandless_evidence", "")).strip():
            raise HarnessError(
                "BRANDLESS mode requires brandless_evidence recording the user-confirmed basis for the missing brand mark"
            )
        if logo_source["image_id"] != "NONE":
            raise HarnessError("BRANDLESS mode must set brand_logo_source.image_id to NONE")
        if logo_source["authorization_basis"] != "NOT_APPLICABLE":
            raise HarnessError("BRANDLESS mode must set brand_logo_source.authorization_basis to NOT_APPLICABLE")
        if logo_source["readability"] != "NOT_APPLICABLE":
            raise HarnessError("BRANDLESS mode must set brand_logo_source.readability to NOT_APPLICABLE")
        if logo_source["usage"] != "FORBIDDEN_ALL_SCREENS":
            raise HarnessError("BRANDLESS mode must set brand_logo_source.usage to FORBIDDEN_ALL_SCREENS")
    else:
        if logo_source["image_id"] not in known_ids:
            raise HarnessError("Brand logo source must name a supplied current-product image")
        if logo_source["authorization_basis"] != "USER_SUPPLIED_CURRENT_PRODUCT":
            raise HarnessError("Brand logo source must be authorized by the user-supplied current-product evidence")
        if logo_source["readability"] != "READABLE":
            raise HarnessError("The first-screen logo hard gate requires a readable source brand mark")
        if logo_source["usage"] != "FIRST_SCREEN_INDEPENDENT_LOGO":
            raise HarnessError("Brand logo source usage must be FIRST_SCREEN_INDEPENDENT_LOGO")
    session["identity"] = identity
    session["brand_mode"] = brand_mode
    session["state"] = "IDENTITY_LOCKED"
    save(args.session, session, "identity_locked")
    return summary(session)


def _collect_plan_mapping_errors(session: dict, plan: dict) -> list[str]:
    """Report common cross-ledger mapping faults in one pass instead of one retry at a time."""
    errors: list[str] = []
    modules = [item for item in plan.get("modules", []) if isinstance(item, dict)]
    module_by_id = {item.get("id"): item for item in modules if item.get("id")}
    known_module_ids = set(module_by_id)
    strategy = plan.get("output_strategy", {}) if isinstance(plan.get("output_strategy"), dict) else {}
    clusters = strategy.get("selling_point_clusters", [])
    cluster_by_id = {
        item.get("id"): item for item in clusters if isinstance(item, dict) and item.get("id")
    }
    for cluster_id, cluster in cluster_by_id.items():
        assigned = cluster.get("assigned_module_ids")
        if not isinstance(assigned, list):
            errors.append(f"selling-point cluster {cluster_id} needs assigned_module_ids")
            continue
        unknown = set(assigned) - known_module_ids
        if unknown:
            errors.append(f"selling-point cluster {cluster_id} cites unknown modules {sorted(unknown)}")
        for module_id in set(assigned) & known_module_ids:
            if cluster_id not in module_by_id[module_id].get("selling_point_cluster_ids", []):
                errors.append(f"cluster {cluster_id} assigns {module_id}, but that module does not declare it")

    for item in strategy.get("claim_coverage", []):
        if not isinstance(item, dict):
            errors.append("claim_coverage contains a non-object item")
            continue
        claim_id = item.get("claim_id", "?")
        module_ids = item.get("module_ids")
        if not isinstance(module_ids, list):
            errors.append(f"claim {claim_id} needs a module_ids list")
            continue
        unknown = set(module_ids) - known_module_ids
        if unknown:
            errors.append(f"claim {claim_id} cites unknown modules {sorted(unknown)}")
        disposition = item.get("disposition")
        defer_code = item.get("defer_reason_code")
        if disposition == "DEFERRED":
            if module_ids:
                errors.append(f"deferred claim {claim_id} cannot be assigned to modules")
            if defer_code not in {
                "CONFLICTING_FACT", "HIGH_RISK_UNDOCUMENTED", "UNSUPPORTED_INVENTION", "IMMATERIAL_DUPLICATE"
            }:
                errors.append(f"deferred claim {claim_id} needs an allowed defer reason")
            continue
        if defer_code != "NOT_DEFERRED":
            errors.append(f"selected claim {claim_id} must use NOT_DEFERRED")
        if not module_ids:
            errors.append(f"selected claim {claim_id} needs at least one assigned module")
        for module_id in set(module_ids) & known_module_ids:
            module = module_by_id[module_id]
            used = {module.get("lead_claim_id"), *module.get("supporting_claim_ids", [])}
            if claim_id not in used:
                errors.append(f"claim {claim_id} is assigned to {module_id}, but that module does not use it")
        if disposition == "LEAD" and module_ids and not any(
            module_by_id[module_id].get("lead_claim_id") == claim_id
            for module_id in set(module_ids) & known_module_ids
        ):
            errors.append(f"lead claim {claim_id} is not the lead claim of an assigned module")
    return _unique_strings(errors)


def _collect_plan_preflight_errors(session: dict, plan: dict) -> list[str]:
    """Collect cheap, deterministic plan faults before the strict validator.

    This prevents the authoring loop from discovering one schema/mapping fault per
    compile attempt. It is intentionally shallow; validate_plan remains the final
    authority after this batch report is clean.
    """
    errors: list[str] = []
    errors.extend(style_constitution_errors(plan.get("style_constitution")))
    strategy = plan.get("output_strategy") if isinstance(plan.get("output_strategy"), dict) else {}
    modules = plan.get("modules") if isinstance(plan.get("modules"), list) else []
    if len(modules) not in {8, 10, 12}:
        errors.append("modules must contain exactly 8, 10, or 12 items")
    if strategy.get("module_count") != len(modules):
        errors.append("output_strategy.module_count must equal the number of modules")
    count_records = strategy.get("module_count_candidates")
    if not isinstance(count_records, list) or len(count_records) != 1:
        errors.append("module_count_candidates must contain one DIRECT_DERIVATION record")
    elif count_records[0].get("candidate_mode") != "DIRECT_DERIVATION":
        errors.append("the single module-count record must use DIRECT_DERIVATION")
    pair_config = strategy.get("continuous_pair_rendering")
    if not isinstance(pair_config, dict) or pair_config.get("pair_merge_type") != "PAIR_PLAN_MERGE":
        errors.append("continuous_pair_rendering must lock pair_merge_type=PAIR_PLAN_MERGE")
    ids = [item.get("id") for item in modules if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("module IDs must be unique")
    if ids != [f"M{index:02d}" for index in range(1, len(modules) + 1)]:
        errors.append("module IDs must be ordered M01...Mnn without gaps")
    for index, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            errors.append(f"module {index} must be an object")
            continue
        missing = [field for field in (
            "id", "order", "duty", "buyer_question", "lead_conclusion", "lead_claim_id",
            "selling_point_cluster_ids", "visual_proof_direction", "visual_evidence",
            "visual_proof_contract", "module_spatial_structure_contract", "copy_intent",
            "composition_family", "camera_scale", "text_system", "reading_path", "copy_density",
            "visual_payload_density", "proof_relation_strength", "commercial_energy", "breathing_screen",
            "brand_anchor", "independent_logo_policy", "independent_logo_source_id", "inherited_dna_tokens",
            "variation_from_previous", "output", "reference_pattern_ids", "visible_copy",
        ) if field not in module]
        if missing:
            errors.append(f"{module.get('id', f'M{index:02d}')} missing: {', '.join(missing)}")
        evidence = module.get("visual_evidence")
        if isinstance(evidence, dict):
            missing_evidence = [field for field in (
                "evidence_class", "proof_device", "visible_content", "forbidden_inventions",
            ) if field not in evidence]
            if missing_evidence:
                errors.append(
                    f"{module.get('id', f'M{index:02d}')} visual_evidence missing: "
                    + ", ".join(missing_evidence)
                )
        else:
            errors.append(f"{module.get('id', f'M{index:02d}')} visual_evidence must be an object")
        try:
            resolve_spatial_contract(module)
        except (HarnessError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{module.get('id', f'M{index:02d}')} spatial contract: {exc}")
    errors.extend(_collect_plan_mapping_errors(session, plan))
    if not errors:
        probe_session = dict(session)
        probe_session["plan"] = plan
        probe_modules = [
            {"id": item["id"], "order": item["order"], "plan": item, "status": "PENDING", "attempts": []}
            for item in modules
        ]
        probe_session["modules"] = probe_modules
        try:
            for module in probe_modules:
                compile_prompt(probe_session, module, repair=False, format_canvas_lock=True)
            pair_config = strategy.get("continuous_pair_rendering") or {}
            by_id = {item["id"]: item for item in probe_modules}
            for pair in pair_config.get("pair_ids", []):
                pair_modules = [by_id[module_id] for module_id in pair.get("module_ids", [])]
                if len(pair_modules) == 2:
                    compile_pair_prompt(probe_session, pair_modules, repair=False)
        except (HarnessError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"prompt compiler readiness: {exc}")
    return _unique_strings(errors)


def _preflight_authoring_plan(session: dict, raw_plan: dict) -> dict:
    """Report independent schema and semantic faults in one bounded repair request.

    Diagnostic normalization never authorizes an invalid plan. Work on a copy:
    authoring normalization may add runtime fields, while original replies remain
    immutable. Strict validate_plan still runs before approval is persisted.
    """
    if raw_plan.get("plan_format") == "WEBSITE_DIRECT_V2":
        from runtime.website_plan import compile_native

        return compile_native(session, raw_plan)
    errors: list[str] = []
    try:
        validate_schema(raw_plan, "compact-detail-plan.schema.json", "compact plan")
    except HarnessError as exc:
        errors.append(str(exc))
    plan = None
    try:
        plan = normalize_plan(session, copy.deepcopy(raw_plan))
        try:
            validate_schema(plan["output_strategy"]["continuous_pair_rendering"], "continuous-pair-render.schema.json", "continuous pair rendering")
        except (HarnessError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"continuous pair schema: {exc}")
        errors.extend(_collect_plan_preflight_errors(session, plan))
    except (HarnessError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"diagnostic normalization: {exc}")
    errors = _unique_strings(errors)
    if errors:
        raise HarnessError(f"Plan preflight found {len(errors)} issue(s):\n- " + "\n- ".join(errors))
    if plan is None:
        raise HarnessError("Plan preflight could not normalize the authored contract")
    return plan


def cmd_preflight_plan(args: argparse.Namespace) -> dict:
    """Run the batch preflight without mutating the session or creating approval files."""
    session = load_json(args.session)
    require_state(session, "IDENTITY_LOCKED")
    raw_plan = load_json(args.plan)
    plan = _preflight_authoring_plan(session, raw_plan)
    return {"status": "PREFLIGHT_PASS", "module_count": len(plan["modules"]), "pair_count": len(plan["modules"]) // 2}


def _require_module_logo_policy(session: dict, module: dict) -> None:
    """Enforce the locked campaign brand-anchor mode on one planned module.

    A BRANDED campaign binds the first screen to the authorized logo source.
    A BRANDLESS (white-label) campaign forbids independent logos on every screen
    including the first, which is anchored by product identity and visual DNA.
    """
    brandless = session.get("brand_mode") == "BRANDLESS"
    branded_first_screen = module["order"] == 1 and not brandless
    if branded_first_screen:
        expected_logo_policy = "REQUIRED_IF_AUTHORIZED"
        expected_logo_source = session["identity"]["brand_logo_source"]["image_id"]
    else:
        expected_logo_policy = "FORBIDDEN"
        expected_logo_source = "NONE"
    expected_logo_hint = (
        "this campaign was locked as BRANDLESS, so no module may show an independent brand logo"
        if brandless
        else "independent brand logos are allowed only on the first screen of a branded campaign"
    )
    if module["independent_logo_policy"] != expected_logo_policy:
        raise HarnessError(
            f"Module {module['id']} independent_logo_policy must be {expected_logo_policy}; {expected_logo_hint}"
        )
    if module["independent_logo_source_id"] != expected_logo_source:
        raise HarnessError(
            f"Module {module['id']} independent_logo_source_id must be {expected_logo_source}; "
            "a branded campaign binds M01 to the authorized logo source and every other screen uses NONE"
        )


def validate_reference_expression_system(session: dict, plan: dict, fact_ids: set[str]) -> None:
    """Validate actual analyzed references and their exact screen schedule."""
    known_module_ids = {item["id"] for item in plan["modules"]}
    reference_system = plan["reference_expression_system"]
    require_fields(reference_system, ("anti_copy_rule",), "reference expression system")
    for optional_empty_key in ("coverage", "adopted_patterns"):
        if optional_empty_key not in reference_system or not isinstance(reference_system[optional_empty_key], list):
            raise HarnessError(f"reference expression system missing required list field: {optional_empty_key}")
    if reference_system["anti_copy_rule"] != "ADAPT_VISUAL_GRAMMAR_ONLY_NEVER_COPY_BRAND_COPY_OR_ASSET":
        raise HarnessError("Reference use must adapt expression grammar without copying brand, copy or assets")
    supplied_reference_items = {
        item["id"]: item for item in session["input"].get("reference_materials", [])
    }
    supplied_reference_units = {
        unit["id"]: {**unit, "reference_id": item["id"]}
        for item in supplied_reference_items.values()
        for unit in item.get("analyzed_units", [])
    }
    supplied_refs = set(supplied_reference_items)
    coverage_refs = {item.get("reference_id") for item in reference_system["coverage"]}
    if coverage_refs != supplied_refs:
        raise HarnessError("Every supplied reference material must be explicitly adopted or rejected")
    patterns = {item.get("id"): item for item in reference_system["adopted_patterns"]}
    for pattern_id, pattern in patterns.items():
        pattern_fields = ["id", "source_reference_ids", "source_unit_ids", "observed_method", "adaptation_for_product", "scheduled_module_ids", "do_not_copy"]
        if plan.get("expression_contract_version") == "4.3":
            pattern_fields.extend(["fact_dependencies", "copy_or_claims_not_inherited"])
        require_fields(pattern, tuple(pattern_fields), "reference expression pattern")
        if set(pattern["source_reference_ids"]) - supplied_refs:
            raise HarnessError(f"Reference pattern {pattern_id} cites unknown materials")
        cited_units = set(pattern["source_unit_ids"])
        if cited_units - set(supplied_reference_units):
            raise HarnessError(f"Reference pattern {pattern_id} cites unknown analyzed screen units")
        if {supplied_reference_units[unit_id]["reference_id"] for unit_id in cited_units} - set(pattern["source_reference_ids"]):
            raise HarnessError(f"Reference pattern {pattern_id} source units do not belong to its source references")
        observed_methods = {
            supplied_reference_units[unit_id]["observed_expression_method"]
            for unit_id in cited_units
        }
        if pattern["observed_method"] not in observed_methods:
            raise HarnessError(
                f"Reference pattern {pattern_id} must use an expression method actually recorded in its analyzed source material"
            )
        if set(pattern["scheduled_module_ids"]) - known_module_ids:
            raise HarnessError(f"Reference pattern {pattern_id} is scheduled to unknown modules")
        if not pattern["scheduled_module_ids"]:
            raise HarnessError(f"Reference pattern {pattern_id} must be scheduled to at least one module")
        if plan.get("expression_contract_version") == "4.3":
            if not pattern["fact_dependencies"] or set(pattern["fact_dependencies"]) - fact_ids:
                raise HarnessError(f"Reference pattern {pattern_id} must bind to active verified facts")
            if not pattern["copy_or_claims_not_inherited"]:
                raise HarnessError(f"Reference pattern {pattern_id} must name reference claims/copy that are not inherited")
    for item in reference_system["coverage"]:
        require_fields(item, ("reference_id", "disposition", "pattern_ids", "reason"), "reference coverage item")
        cited_patterns = set(item["pattern_ids"])
        if cited_patterns - set(patterns):
            raise HarnessError(f"Reference {item['reference_id']} cites unknown expression patterns")
        if item["disposition"] == "ADOPTED_GRAMMAR" and not cited_patterns:
            raise HarnessError(f"Adopted reference {item['reference_id']} must feed at least one expression pattern")
        if item["disposition"] == "REJECTED_IRRELEVANT" and cited_patterns:
            raise HarnessError(f"Rejected reference {item['reference_id']} cannot feed an expression pattern")
    known_pattern_ids = set(patterns)
    for module in plan["modules"]:
        scheduled_patterns = {pattern_id for pattern_id, pattern in patterns.items()
                              if module["id"] in pattern["scheduled_module_ids"]}
        if scheduled_patterns - set(module["reference_pattern_ids"]):
            raise HarnessError(f"Module {module['id']} must inherit every scheduled analyzed reference-expression pattern")
        if set(module["reference_pattern_ids"]) - known_pattern_ids:
            raise HarnessError(f"Module {module['id']} cites unknown reference-expression patterns")
        for pattern_id in module["reference_pattern_ids"]:
            if module["id"] not in patterns[pattern_id]["scheduled_module_ids"]:
                raise HarnessError(f"Reference pattern {pattern_id} is not scheduled for module {module['id']}")


def validate_plan(session: dict, plan: dict) -> None:
    if plan.get("planning_mode") == "WEBSITE_DIRECT_V2":
        from runtime.website_plan import compile_native

        if session.get("conflict_checkpoint", {}).get("required"):
            raise HarnessError("Resolve product source conflicts before planning")
        if plan != compile_native(session, plan["website_source_plan"]):
            raise HarnessError("Compiled website plan differs from its facts or approved brief")
        for module in plan["modules"]:
            _require_module_logo_policy(session, module)
        return
    if any(item.get("status") == "UNRESOLVED" for item in session["input"].get("source_conflicts", [])):
        raise HarnessError("Full planning is forbidden while product-source conflicts remain unresolved")
    require_fields(plan, ("campaign_thesis", "dominant_decision_type", "output_strategy", "reference_expression_system", "style_constitution", "series_visual_dna", "typography_rhythm", "modules"), "plan")
    visual_dna_errors = style_constitution_errors(plan.get("style_constitution"))
    if visual_dna_errors:
        raise HarnessError(
            "Visual DNA contract is incomplete; recompile the plan before requesting user approval:\n- "
            + "\n- ".join(visual_dna_errors)
        )
    legacy_ledgers = [
        name for name in ("visual_performance_rhythm", "spatial_structure_rhythm")
        if name in plan
    ]
    if legacy_ledgers:
        raise HarnessError(
            "v4.1 lightweight planning derives performance and spatial rhythm at runtime; "
            f"remove duplicated legacy ledgers: {', '.join(legacy_ledgers)}"
        )
    lightweight = plan.get("planning_mode") in {"LIGHTWEIGHT_V41", "LIGHTWEIGHT_V41"}
    mapping_errors = [] if lightweight else _collect_plan_mapping_errors(session, plan)
    if mapping_errors:
        raise HarnessError(
            f"Plan mapping preflight found {len(mapping_errors)} issue(s):\n- "
            + "\n- ".join(mapping_errors)
        )
    route_aware = "operating_analysis" in session["input"] and not lightweight
    if route_aware:
        require_fields(plan, ("operating_strategy", "campaign_recall_contract", "campaign_pain_solution_contract", "campaign_visual_carrier_sequence", "selling_point_depth_contract"), "route-aware plan")
    strategy = plan["output_strategy"]
    # v4.1 makes the series-master inheritance boundary a runtime invariant.
    # A plan may document it, but cannot weaken it or repeat a large manual ledger.
    require_fields(
        strategy,
        (
            "target_platform", "profile_name", "selection_mode", "module_count",
            "module_count_rationale", "module_count_candidates", "selling_point_clusters", "merge_decisions", "claim_coverage",
            "aspect_ratio", "canvas_width_px", "module_height_px", "safe_margin_px",
            "slice_height_px", "dimension_rationale",
            "uniform_dimensions", "verification_status",
        ),
        "output strategy",
    )
    if strategy["selection_mode"] not in ("ADAPTIVE_PLANNED", "USER_SPECIFIED", "PLATFORM_VERIFIED"):
        raise HarnessError("Output strategy must be deliberately planned; implicit engineered defaults are forbidden")
    if len(strategy["module_count_rationale"].strip()) < 20:
        raise HarnessError("Module-count rationale must explain the product-specific grouping decision")
    if len(strategy["module_count_candidates"]) != 1:
        raise HarnessError("Module count must be one direct derivation record; do not compare alternative count options")
    for candidate in strategy["module_count_candidates"]:
        required_candidate_fields = ("count", "coverage_tradeoff", "selling_point_fit", "decision", "story_obligation_fit") if route_aware else ("count", "coverage_tradeoff", "selling_point_fit", "decision")
        require_fields(candidate, required_candidate_fields, "direct screen-count decision")
    candidate = strategy["module_count_candidates"][0]
    if candidate.get("candidate_mode") != "DIRECT_DERIVATION":
        raise HarnessError("Module-count record must use DIRECT_DERIVATION; do not expose alternative counts")
    if strategy["module_count"] not in {8, 10, 12}:
        raise HarnessError("Module count must be exactly 8, 10, or 12 screens")
    if candidate.get("count") != strategy["module_count"]:
        raise HarnessError("Directly derived module_count must match its single decision record")
    selected_candidates = [item for item in strategy["module_count_candidates"] if item["decision"] == "SELECTED"]
    if len(selected_candidates) != 1 or selected_candidates[0]["count"] != strategy["module_count"]:
        raise HarnessError("Exactly one direct screen-count decision must be selected and match module_count")
    if len(strategy["dimension_rationale"].strip()) < 20:
        raise HarnessError("Dimension rationale must explain the working-master and delivery relationship")
    if not strategy["merge_decisions"] or not all(str(item).strip() for item in strategy["merge_decisions"]):
        raise HarnessError("Output strategy must record at least one explicit merge or separation decision")
    if strategy["uniform_dimensions"] is not True:
        raise HarnessError("All generated detail modules must use one approved width, height and aspect ratio")

    pair_config = strategy.get("continuous_pair_rendering")
    if not isinstance(pair_config, dict):
        raise HarnessError("v4.1 requires continuous_pair_rendering for every campaign")
    if pair_config.get("enabled") is not True:
        raise HarnessError("v4.1 requires continuous_pair_rendering.enabled=true")
    if pair_config.get("enabled"):
        if pair_config.get("pair_merge_type") != "PAIR_PLAN_MERGE":
            raise HarnessError("Every continuous pair must use pair_merge_type=PAIR_PLAN_MERGE")
        if strategy["module_count"] not in {8, 10, 12}:
            raise HarnessError("Continuous-pair campaigns must use exactly 8, 10, or 12 screens")
        if strategy["aspect_ratio"].get("label") != "3:4":
            raise HarnessError("v4.1 uses the configured single-screen window only as internal decomposition geometry")
        if pair_config.get("pair_canvas_aspect_ratio") != "3:8" or pair_config.get("decomposition_window_aspect_ratio") != "3:4":
            raise HarnessError("v4.1 pair canvas must declare 3:8 and the configured decomposition window")
        if pair_config.get("pair_canvas_width_px") != strategy["canvas_width_px"]:
            raise HarnessError("Pair canvas width must equal the approved single-screen width")
        if pair_config.get("pair_canvas_width_px", 0) * 8 != pair_config.get("pair_canvas_height_px", 0) * 3:
            raise HarnessError("Pair canvas pixel dimensions must resolve to the declared 3:8 ratio")
        if len(plan.get("modules", [])) % 2:
            raise HarnessError("Continuous pair rendering requires an even module count")
        if pair_config.get("grouping_mode") != "TWO_SCREEN_PAIRS" or pair_config.get("pair_size") != 2:
            raise HarnessError("continuous_pair_rendering must use TWO_SCREEN_PAIRS with pair_size=2")
        pairs = pair_config.get("pair_ids")
        if not isinstance(pairs, list) or len(pairs) != len(plan["modules"]) // 2:
            raise HarnessError("continuous_pair_rendering.pair_ids must cover every module exactly once")
        expected_ids = [item["id"] for item in plan["modules"]]
        plan_module_by_id = {item["id"]: item for item in plan["modules"]}
        seen = []
        for index, pair in enumerate(pairs, start=1):
            require_fields(
                pair,
                (
                    "pair_id", "module_ids", "pair_merge_type", "internal_seam_method_id", "internal_seam_carrier",
                    "internal_seam_source_region", "internal_seam_target_region", "internal_seam_geometry",
                ),
                "continuous render pair",
            )
            if pair["pair_id"] != f"P{index:02d}" or len(pair["module_ids"]) != 2:
                raise HarnessError("Continuous render pairs must be ordered P01... and contain exactly two modules")
            if pair.get("pair_merge_type") != "PAIR_PLAN_MERGE":
                raise HarnessError(f"Continuous render pair {pair['pair_id']} must use pair_merge_type=PAIR_PLAN_MERGE")
            if pair["module_ids"] != expected_ids[(index - 1) * 2:index * 2]:
                raise HarnessError("Continuous render pair module_ids must match storyboard order")
            lower_transition = plan_module_by_id[pair["module_ids"][1]].get("transition_contract") or {}
            expected_seam_fields = {
                "internal_seam_method_id": lower_transition.get("method_id"),
                "internal_seam_carrier": lower_transition.get("seam_carrier"),
                "internal_seam_source_region": lower_transition.get("source_region_previous"),
                "internal_seam_target_region": lower_transition.get("target_region_current"),
                "internal_seam_geometry": lower_transition.get("handoff_geometry"),
            }
            for field, expected in expected_seam_fields.items():
                if pair.get(field) != expected:
                    raise HarnessError(f"Continuous render pair {pair['pair_id']} {field} must mirror its lower-screen incoming transition contract")
            for field, expected_module in (
                ("internal_seam_source_region", pair["module_ids"][0]),
                ("internal_seam_target_region", pair["module_ids"][1]),
            ):
                explicit = set(re.findall(r"(?<![A-Za-z0-9])M\d{2}(?![A-Za-z0-9])", str(pair.get(field) or "")))
                if explicit - {expected_module}:
                    raise HarnessError(f"Pair {pair['pair_id']} {field} must reference only {expected_module}")
            seen.extend(pair["module_ids"])
            seam = pair.get("internal_seam_zone", {})
            require_fields(seam, ("start_y", "end_y"), "continuous pair internal seam zone")
            if not 0 <= seam["start_y"] < seam["end_y"] <= strategy["module_height_px"] * 2:
                raise HarnessError("Continuous pair internal seam zone must lie inside the pair canvas")
        if seen != expected_ids or pair_config.get("pair_count") != len(pairs):
            raise HarnessError("Continuous render pair coverage is incomplete or duplicated")
        if pair_config.get("pair_canvas_height_px") != strategy["module_height_px"] * 2:
            raise HarnessError("Continuous pair canvas height must equal two module heights")
        if pair_config.get("pair_slice_height_px") != strategy["module_height_px"]:
            raise HarnessError("Continuous pair slice height must equal one module height")
        bridges = pair_config.get("pair_to_pair_transition_tokens")
        if not isinstance(bridges, list) or len(bridges) != max(0, len(pairs) - 1) or any(not str(token).strip() for token in bridges):
            raise HarnessError("Continuous pair rendering must declare one non-empty token for each pair-to-pair seam")

    profile = session["input"]["platform_profile"]
    profile_pairs = (
        ("profile_name", "profile_name"),
        ("canvas_width_px", "canvas_width_px"),
        ("module_height_px", "module_height_px"),
        ("safe_margin_px", "safe_margin_px"),
        ("slice_height_px", "slice_height_px"),
        ("verification_status", "verification_status"),
    )
    for strategy_key, profile_key in profile_pairs:
        if strategy[strategy_key] != profile[profile_key]:
            raise HarnessError(f"Output strategy {strategy_key} does not match the selected platform profile")
    if strategy["target_platform"] != profile["target_platform"]:
        raise HarnessError("Output strategy target_platform does not match the selected platform profile")
    if strategy["selection_mode"] != profile["selection_mode"]:
        raise HarnessError("Output strategy selection_mode does not match the selected platform profile")
    ratio = strategy["aspect_ratio"]
    require_fields(ratio, ("width_units", "height_units", "label"), "aspect ratio")
    expected_label = f"{ratio['width_units']}:{ratio['height_units']}"
    if ratio["label"] != expected_label:
        raise HarnessError("Aspect-ratio label does not match its numeric units")
    if ratio != profile["aspect_ratio"]:
        raise HarnessError("Output strategy aspect ratio does not match the selected platform profile")
    if strategy["canvas_width_px"] * ratio["height_units"] != strategy["module_height_px"] * ratio["width_units"]:
        raise HarnessError("Approved pixel dimensions do not exactly match the declared aspect ratio")
    dna = plan["series_visual_dna"]
    require_fields(
        dna,
        ("brand_anchor_system", "palette_tokens", "typography_tokens", "graphic_tokens", "photography_tokens", "product_tokens", "spatial_tokens", "invariants"),
        "series visual DNA",
    )
    if len(dna["invariants"]) < 2:
        raise HarnessError("Series visual DNA requires at least two campaign invariants")
    typography_rhythm = plan["typography_rhythm"]
    require_fields(
        typography_rhythm,
        (
            "campaign_rule", "primary_mode_sequence", "anchor_zone_sequence",
            "centered_hero_max", "consecutive_anchor_repeat_limit",
        ),
        "typography rhythm",
    )
    if "exceptions" not in typography_rhythm or not isinstance(typography_rhythm["exceptions"], list):
        raise HarnessError("Typography rhythm exceptions must be a list, using an empty list when none")
    if not isinstance(typography_rhythm["centered_hero_max"], int) or typography_rhythm["centered_hero_max"] < 0:
        raise HarnessError("Typography rhythm centered_hero_max must be a non-negative integer")
    if not plan["modules"]:
        raise HarnessError("A detail page requires at least one purposeful module")
    if strategy["module_count"] not in {8, 10, 12}:
            raise HarnessError("Continuous-pair campaigns must use exactly 8, 10, or 12 screens")
    if strategy["module_count"] != len(plan["modules"]):
        raise HarnessError("Planned module_count must exactly equal the storyboard module count")
    fact_ids = set(session.get("planning_fact_ids") or [item["id"] for item in session["input"]["verified_facts"]])
    facts_by_id = {item["id"]: item for item in session["input"]["verified_facts"]}
    performance_overrides = [
        module for module in plan["modules"]
        if module.get("module_visual_performance_contract")
    ]
    override_budget = max(1, len(plan["modules"]) // 6)
    if len(performance_overrides) > override_budget:
        raise HarnessError(
            f"v4.1 lightweight planning allows at most {override_budget} deliberate visual-performance "
            "override(s) for this page; ordinary modules must use runtime-derived contracts"
        )
    for module in performance_overrides:
        reason = str(module.get("performance_override_reason", "")).strip()
        if len(reason) < 20:
            raise HarnessError(
                f"Module {module.get('id', '?')} uses a visual-performance override without a concrete "
                "performance_override_reason; remove the override or explain the exceptional need"
            )
    if route_aware:
        operating = plan["operating_strategy"]
        require_fields(
            operating,
            (
                "intake_route", "target_buyer", "primary_pain_id", "selected_positioning_id",
                "core_promise", "priority_claim_ids", "assumption_boundary",
            ),
            "operating strategy",
        )
        if "supporting_claim_ids" not in operating or not isinstance(operating["supporting_claim_ids"], list):
            raise HarnessError("operating_strategy.supporting_claim_ids must be a list; it may be empty")
        analysis = session["input"]["operating_analysis"]
        if operating["intake_route"] != session["input"]["intake_route"]:
            raise HarnessError("Plan operating route must match the route selected at intake")
        if operating["selected_positioning_id"] != analysis["selected_positioning_id"]:
            raise HarnessError("Plan must preserve the selected product positioning")
        pain_ids = {item["id"] for item in analysis["pain_hypotheses"]}
        if operating["primary_pain_id"] not in pain_ids:
            raise HarnessError("Plan primary pain must come from the operating analysis")
        priority_claim_ids = set(operating["priority_claim_ids"])
        supporting_claim_ids = set(operating["supporting_claim_ids"])
        if not priority_claim_ids or priority_claim_ids - fact_ids or supporting_claim_ids - fact_ids:
            raise HarnessError("Operating strategy claim priorities must use active verified facts")
        if priority_claim_ids & supporting_claim_ids:
            raise HarnessError("A claim cannot be both priority and supporting in the operating strategy")
    clusters = strategy["selling_point_clusters"]
    if not clusters:
        raise HarnessError("Selling-point clusters are required before module count can be chosen")
    cluster_ids = [item.get("id") for item in clusters]
    if len(cluster_ids) != len(set(cluster_ids)) or any(not item for item in cluster_ids):
        raise HarnessError("Selling-point cluster IDs must be unique and non-empty")
    clustered_fact_ids = []
    for cluster in clusters:
        require_fields(cluster, ("id", "buyer_question", "fact_ids", "purchase_role", "separation_policy", "assigned_module_ids"), "selling-point cluster")
        clustered_fact_ids.extend(cluster["fact_ids"])
    if len(clustered_fact_ids) != len(set(clustered_fact_ids)) or set(clustered_fact_ids) != fact_ids:
        raise HarnessError("Selling-point clusters must account for every active fact exactly once before screen-count planning")
    coverage = strategy["claim_coverage"]
    coverage_ids = [item.get("claim_id") for item in coverage]
    if len(coverage_ids) != len(set(coverage_ids)):
        raise HarnessError("Each verified fact must appear exactly once in claim_coverage")
    if set(coverage_ids) != fact_ids:
        missing = sorted(fact_ids - set(coverage_ids))
        unknown = sorted(set(coverage_ids) - fact_ids)
        raise HarnessError(f"claim_coverage must account for every verified fact; missing={missing}, unknown={unknown}")
    module_ids = []
    expected_order = 1
    previous_module = None
    for module in plan["modules"]:
        if module.get("expression_contract_version") == "4.3":
            validate_expression_signature(module)
        require_fields(
            module,
            ("id", "order", "duty", "buyer_question", "lead_conclusion", "lead_claim_id", "selling_point_cluster_ids", "visual_proof_direction", "visual_evidence", "visual_proof_contract", "module_spatial_structure_contract", "copy_intent", "composition_family", "camera_scale", "text_system", "reading_path", "copy_density", "visual_payload_density", "proof_relation_strength", "commercial_energy", "breathing_screen", "brand_anchor", "independent_logo_policy", "independent_logo_source_id", "inherited_dna_tokens", "variation_from_previous", "output"),
            "plan module",
        )
        for optional_empty_key in ("reference_pattern_ids", "visible_copy"):
            if optional_empty_key not in module or not isinstance(module[optional_empty_key], list):
                raise HarnessError(f"plan module missing required list field: {optional_empty_key}")
        for field_name in ("duty", "buyer_question", "lead_conclusion", "visual_proof_direction"):
            field_value = str(module.get(field_name, ""))
            leaked = [phrase for phrase in FORBIDDEN_PIXEL_FACING_PLAN_PHRASES if phrase in field_value]
            if leaked:
                raise HarnessError(
                    f"Module {module['id']} leaks internal evidence/compliance provenance into {field_name}: "
                    f"{', '.join(leaked)}. Keep provenance machine-side and write a buyer-facing sales conclusion."
                )
        if "形成清晰的产品绑定画面" in str(module["visual_proof_direction"]):
            raise HarnessError(
                f"Module {module['id']} uses a generic visual-proof placeholder; name the concrete objects, "
                "action/state relation and visible buyer takeaway"
            )
        require_fields(module["visual_evidence"], ("evidence_class", "proof_device", "visible_content", "forbidden_inventions"), f"visual evidence for {module['id']}")
        contract = module["visual_proof_contract"]
        require_fields(contract, ("buyer_takeaway", "visual_verb", "primary_subject", "supporting_subjects", "required_relations", "minimum_visual_payload", "visual_specificity_anchor", "allowed_metaphor", "hidden_copy_test"), f"visual proof contract for {module['id']}")
        expression_contract = module.get("module_visual_expression_contract", {})
        for label, value in (
            ("visual_proof_contract.buyer_takeaway", contract.get("buyer_takeaway")),
            ("visual_proof_contract.visual_verb", contract.get("visual_verb")),
            ("module_visual_expression_contract.visual_scene_sentence", expression_contract.get("visual_scene_sentence")),
            ("module_visual_expression_contract.visual_semantic_payload", expression_contract.get("visual_semantic_payload")),
        ):
            leaked = [phrase for phrase in FORBIDDEN_PIXEL_FACING_PLAN_PHRASES if phrase in str(value or "")]
            if leaked:
                raise HarnessError(
                    f"Module {module['id']} leaks internal provenance into prompt-facing {label}: "
                    f"{', '.join(leaked)}"
                )
        if contract["minimum_visual_payload"] < 3 and not module["breathing_screen"]:
            raise HarnessError(f"Module {module['id']} must require at least three effective visual payloads unless it is an explicit breathing screen")
        performance = resolve_performance_contract(module, order=module["order"], module_count=len(plan["modules"]))
        require_fields(
            performance,
            (
                "strategy_type", "target_energy", "product_dominance", "product_prominence_min_percent",
                "headline_dominance", "headline_must_enter_top_three", "headline_rank_max",
                "primary_evidence_anchor", "evidence_must_be_primary_visual", "depth_plan", "depth_layers_min",
                "visual_tension", "visual_tension_required", "background_competition_max", "information_density",
                "type_role_target", "unequal_information_groups_min", "mobile_scan_stops_min", "mobile_scan_order",
                "commercial_closure", "commercial_closure_required", "creative_permissions", "forbidden_regression",
            ),
            f"visual performance contract for {module['id']}",
        )
        allowed_strategies = {
            "FIRST_SCREEN_CORE_BENEFIT", "PAIN", "PAIN_SOLUTION_COMBINED", "EFFICACY_PROOF", "MECHANISM", "MATERIAL_INGREDIENT",
            "USE_SCENE", "PARAMETER_SPECIFICATION", "TRUST", "PURCHASE_CLOSURE", "OTHER_PRODUCT_PROOF",
        }
        if performance["strategy_type"] not in allowed_strategies:
            raise HarnessError(f"Module {module['id']} has an unsupported visual-performance strategy")
        if performance["target_energy"] not in ("MEDIUM", "MEDIUM_HIGH", "HIGH"):
            raise HarnessError(f"Module {module['id']} has an unsupported performance-energy target")
        if not 0 <= performance["product_prominence_min_percent"] <= 90:
            raise HarnessError(f"Module {module['id']} product-prominence threshold must be between 0 and 90 percent")
        if performance["headline_must_enter_top_three"] and performance["headline_rank_max"] > 3:
            raise HarnessError(f"Module {module['id']} headline must enter the top three visual ranks")
        if performance["headline_must_enter_top_three"] and not module["visible_copy"]:
            raise HarnessError(f"Module {module['id']} requires a dominant headline but has no approved visible copy")
        if performance["depth_layers_min"] < (2 if module["breathing_screen"] else 3):
            raise HarnessError(f"Module {module['id']} lacks the required visual depth")
        if performance["information_density"] == "LOW" and not module["breathing_screen"]:
            raise HarnessError(f"Module {module['id']} may use low information density only as an approved breathing screen")
        if performance["mobile_scan_stops_min"] < (2 if module["breathing_screen"] else 3):
            raise HarnessError(f"Module {module['id']} lacks enough mobile scan stops for its role")
        if len(performance["mobile_scan_order"]) < performance["mobile_scan_stops_min"]:
            raise HarnessError(f"Module {module['id']} scan order is shorter than its approved scan-stop minimum")
        if performance["background_competition_max"] not in ("LOW", "LOW_TO_MEDIUM", "MEDIUM"):
            raise HarnessError(f"Module {module['id']} has an unsupported background-competition ceiling")
        if len(performance["creative_permissions"]) < 2:
            raise HarnessError(f"Module {module['id']} needs at least two evidence-grounded creative permissions")
        if len(set(performance["forbidden_regression"])) < 5:
            raise HarnessError(f"Module {module['id']} must preserve the five core visual-performance anti-regressions")
        if module["copy_density"] in ("none", "low") and module["visual_payload_density"] == "low" and not module["breathing_screen"]:
            raise HarnessError(f"Module {module['id']} confuses low copy with low visual payload")
        if module["proof_relation_strength"] == "weak" and not module["breathing_screen"]:
            raise HarnessError(f"Module {module['id']} requires a visible proof relationship, not a weak decorative association")
        if module["commercial_energy"] == "restrained" and module["visual_payload_density"] == "low" and not module["breathing_screen"]:
            raise HarnessError(f"Module {module['id']} would become an underpowered concept poster")
        copy_limits = {"none": (0, 0), "low": (1, 3), "medium": (2, 5), "high": (3, 8)}
        minimum_copy, maximum_copy = copy_limits[module["copy_density"]]
        if not module["breathing_screen"] and module["copy_density"] == "none":
            raise HarnessError(f"Module {module['id']} may not interpret visual-first as text-free")
        if not minimum_copy <= len(module["visible_copy"]) <= maximum_copy:
            raise HarnessError(f"Module {module['id']} visible-copy whitelist exceeds its copy-density limit")
        copy_intent = module["copy_intent"]
        require_fields(copy_intent, ("policy", "why_text_is_needed", "text_to_visual_bindings", "hidden_copy_takeaway", "copy_adds"), f"copy intent for {module['id']}")
        if copy_intent["policy"] != "VISUAL_FIRST_WITH_AUXILIARY_COPY":
            raise HarnessError(f"Module {module['id']} must use imagery as primary proof and copy as auxiliary guidance")
        whitelist = [item["text"] for item in module["visible_copy"]]
        for item in module["visible_copy"]:
            copy_text = str(item.get("text", ""))
            leaked = [phrase for phrase in FORBIDDEN_PIXEL_FACING_PLAN_PHRASES if phrase in copy_text]
            role = str(item.get("role", "")).lower()
            if leaked:
                raise HarnessError(
                    f"Module {module['id']} visible copy leaks machine-side provenance or workflow language: "
                    f"{', '.join(leaked)}"
                )
        bound_copy = [item.get("text") for item in copy_intent["text_to_visual_bindings"]]
        if sorted(whitelist) != sorted(bound_copy):
            raise HarnessError(f"Every visible copy unit in {module['id']} must be bound to a visible object, relation or result")
        require_fields(
            module["text_system"],
            (
                "primary_mode", "anchor_target", "anchor_zone", "reading_direction",
                "placement_logic", "hierarchy", "max_lines", "container_style",
                "negative_space_reservation", "image_text_relationship",
                "composition_flexibility", "neighbor_difference",
            ),
            f"text system for {module['id']}",
        )
        if "supporting_mode" not in module["text_system"]:
            raise HarnessError(f"Text system for {module['id']} must declare supporting_mode, using null when absent")
        if not isinstance(module["text_system"]["max_lines"], int) or module["text_system"]["max_lines"] < 1:
            raise HarnessError(f"Text system for {module['id']} max_lines must be a positive integer")
        if module["text_system"]["composition_flexibility"] not in ("LOCKED", "NEGATIVE_SPACE_ONLY", "CONTROLLED_REFLOW"):
            raise HarnessError(f"Text system for {module['id']} has an unsupported composition_flexibility")
        if len(module["inherited_dna_tokens"]) < 2:
            raise HarnessError(f"Module {module['id']} must inherit at least two series visual-DNA tokens")
        _require_module_logo_policy(session, module)
        if previous_module:
            same_composition = module["composition_family"] == previous_module["composition_family"]
            same_text = module["text_system"]["primary_mode"] == previous_module["text_system"]["primary_mode"]
            same_anchor = module["text_system"]["anchor_zone"] == previous_module["text_system"]["anchor_zone"]
            if (same_composition and same_text or same_text and same_anchor) and not module.get("repeat_justification", "").strip():
                raise HarnessError(
                    f"Module {module['id']} repeats a composition/typography placement pair from {previous_module['id']} without a transition justification"
                )
            if not module["variation_from_previous"]:
                raise HarnessError(f"Module {module['id']} must declare visible variation from the previous module")
        if module["order"] != expected_order:
            raise HarnessError("Module order must be continuous and start at 1")
        expected_order += 1
        module_ids.append(module["id"])
        unknown_clusters = set(module["selling_point_cluster_ids"]) - set(cluster_ids)
        if unknown_clusters or not module["selling_point_cluster_ids"]:
            raise HarnessError(f"Module {module['id']} must serve known selling-point clusters")
        used = {module["lead_claim_id"], *module.get("supporting_claim_ids", [])}
        unknown = used - fact_ids - {"NO_FACT_CLOSURE"}
        if unknown:
            raise HarnessError(f"Module {module['id']} uses unknown fact IDs: {sorted(unknown)}")
        if module.get("story_function") == "TRUST_OR_RISK_REMOVAL":
            documented = [
                fact_id for fact_id in used
                if fact_id in facts_by_id and facts_by_id[fact_id].get("source_class") == "DOCUMENTED_EVIDENCE"
            ]
            if not documented:
                raise HarnessError(
                    f"Module {module['id']} is a trust/risk-only screen without independent documented evidence; "
                    "defer the unsupported point or attach its qualifier to the relevant selling screen"
                )
        output = module["output"]
        if output["width_px"] != strategy["canvas_width_px"] or output["height_px"] != strategy["module_height_px"] or output["safe_margin_px"] != strategy["safe_margin_px"]:
            raise HarnessError(f"Module {module['id']} dimensions do not match the approved output strategy")
        previous_module = module
    if len(module_ids) != len(set(module_ids)):
        raise HarnessError("Module IDs must be unique")
    # Performance and spatial rhythm are resolved from compact per-module blueprints.
    first_performance = resolve_performance_contract(plan["modules"][0], 1, len(plan["modules"]))
    if first_performance["strategy_type"] != "FIRST_SCREEN_CORE_BENEFIT":
        raise HarnessError("The first module must use the core-benefit performance strategy")
    if first_performance["target_energy"] != "HIGH":
        raise HarnessError("The first module must remain a HIGH visual-performance peak")
    if len(plan["modules"]) > 1:
        last_performance = resolve_performance_contract(
            plan["modules"][-1], len(plan["modules"]), len(plan["modules"])
        )
        if last_performance["strategy_type"] != "PURCHASE_CLOSURE" or last_performance["target_energy"] != "HIGH":
            raise HarnessError("The final module must close the purchase story at HIGH visual performance")
    support_only = [
        module for module in plan["modules"]
        if module.get("story_function") == "TRUST_OR_RISK_REMOVAL"
    ]
    support_budget = max(1, len(plan["modules"]) // 5)
    if len(support_only) > support_budget:
        raise HarnessError(
            "Trust, qualification and risk-only screens exceed the sales-support budget; "
            "attach qualifiers to the claim screen or combine them with product/use information"
        )
    known_module_ids = set(module_ids)
    module_by_id = {item["id"]: item for item in plan["modules"]}
    validate_spatial_contract(plan)
    validate_information_contract(plan)
    validate_high_risk_plan_copy(session["input"], plan)
    if route_aware:
        validate_sales_depth_contract(plan, fact_ids)
        validate_formula_split_contract(plan, fact_ids, session["input"].get("verified_facts", []))
        validate_story_contract(plan, session["input"]["operating_analysis"])
        if plan.get("plan_format") == "COMPACT_V1_3":
            validate_user_selling_point_ledger(
                plan,
                session["input"].get("user_supplied_selling_points", []),
                fact_ids,
            )
            validate_dynamic_count_formula(plan)
            validate_visual_archetypes(plan)
            validate_campaign_expression_diversity(plan, fact_ids)
        recall = plan["campaign_recall_contract"]
        require_fields(
            recall,
            (
                "must_remember_claim_ids", "first_screen_value_claim_ids",
                "proof_module_ids_by_claim", "closure_reinforcement_claim_ids", "buyer_recall_test",
            ),
            "campaign recall contract",
        )
        must_remember = set(recall["must_remember_claim_ids"])
        first_claims = set(recall["first_screen_value_claim_ids"])
        closure_claims = set(recall["closure_reinforcement_claim_ids"])
        if not must_remember or not first_claims or not closure_claims:
            raise HarnessError("Recall contract requires must-remember, first-screen and closing claim sets")
        if must_remember - priority_claim_ids or first_claims - must_remember or closure_claims - must_remember:
            raise HarnessError("Recall contract claim sets must remain within the priority verified claims")
        first_module_claims = {plan["modules"][0]["lead_claim_id"], *plan["modules"][0].get("supporting_claim_ids", [])}
        last_module_claims = {plan["modules"][-1]["lead_claim_id"], *plan["modules"][-1].get("supporting_claim_ids", [])}
        if first_claims - first_module_claims:
            raise HarnessError("First-screen value claims must be carried by the first module")
        if closure_claims - last_module_claims:
            raise HarnessError("Closing reinforcement claims must be carried by the final module")
        proof_map = recall["proof_module_ids_by_claim"]
        if set(proof_map) != must_remember:
            raise HarnessError("Recall proof map must account for every must-remember claim exactly once")
        claim_usage = {claim_id: set() for claim_id in must_remember}
        for module in plan["modules"]:
            used_claims = {module["lead_claim_id"], *module.get("supporting_claim_ids", [])}
            for claim_id in must_remember & used_claims:
                claim_usage[claim_id].add(module["id"])
        for claim_id, proof_modules in proof_map.items():
            declared = set(proof_modules)
            if not declared or declared - known_module_ids:
                raise HarnessError(f"Recall proof modules for {claim_id} must be valid planned modules")
            if declared - claim_usage[claim_id]:
                raise HarnessError(f"Recall proof modules for {claim_id} must actually carry that verified claim")
            minimum_recurrence = 1 if len(plan["modules"]) == 1 else 2
            if len(claim_usage[claim_id]) < minimum_recurrence:
                raise HarnessError(f"Must-remember claim {claim_id} is not reinforced across the campaign")
    centered_count = sum(item["text_system"]["primary_mode"] == "centered_hero_headline" for item in plan["modules"])
    natural_centered_limit = max(1, (len(plan["modules"]) + 2) // 3)
    if typography_rhythm["centered_hero_max"] > natural_centered_limit:
        raise HarnessError("Typography rhythm centered_hero_max is too permissive to guarantee campaign-level variation")
    if centered_count > typography_rhythm["centered_hero_max"]:
        raise HarnessError("Centered hero headline count exceeds the approved campaign typography rhythm")
    primary_sequence = [item["text_system"]["primary_mode"] for item in plan["modules"]]
    anchor_sequence = [item["text_system"]["anchor_zone"] for item in plan["modules"]]
    if primary_sequence != typography_rhythm["primary_mode_sequence"]:
        raise HarnessError("Typography rhythm primary_mode_sequence must exactly match module order")
    if anchor_sequence != typography_rhythm["anchor_zone_sequence"]:
        raise HarnessError("Typography rhythm anchor_zone_sequence must exactly match module order")
    repeat_limit = typography_rhythm["consecutive_anchor_repeat_limit"]
    if not isinstance(repeat_limit, int) or repeat_limit < 1:
        raise HarnessError("Typography rhythm consecutive_anchor_repeat_limit must be a positive integer")
    run = 1
    for index in range(1, len(anchor_sequence)):
        run = run + 1 if anchor_sequence[index] == anchor_sequence[index - 1] else 1
        if run > repeat_limit:
            raise HarnessError("Typography anchor zone repeats for too many consecutive modules")
    for cluster in clusters:
        assigned = set(cluster["assigned_module_ids"])
        if not assigned or assigned - known_module_ids:
            raise HarnessError(f"Selling-point cluster {cluster['id']} must be assigned to valid modules")
        for module_id in assigned:
            if cluster["id"] not in module_by_id[module_id]["selling_point_cluster_ids"]:
                raise HarnessError(f"Selling-point cluster {cluster['id']} is not declared by assigned module {module_id}")
    must_separate = [item for item in clusters if item["separation_policy"] == "MUST_SEPARATE"]
    for cluster in must_separate:
        other_assignments = set().union(*(
            set(item["assigned_module_ids"])
            for item in must_separate if item["id"] != cluster["id"]
        )) if len(must_separate) > 1 else set()
        if not (set(cluster["assigned_module_ids"]) - other_assignments):
            raise HarnessError(f"Selling-point cluster {cluster['id']} is marked MUST_SEPARATE but has no dedicated module")

    validate_reference_expression_system(session, plan, fact_ids)
    for item in coverage:
        require_fields(item, ("claim_id", "disposition", "rationale", "defer_reason_code"), "claim coverage item")
        if "module_ids" not in item or not isinstance(item["module_ids"], list):
            raise HarnessError("claim coverage item missing required fields: module_ids")
        if not str(item["rationale"]).strip():
            raise HarnessError(f"Claim {item['claim_id']} requires a disposition rationale")
        referenced = set(item["module_ids"])
        if referenced - known_module_ids:
            raise HarnessError(f"Claim {item['claim_id']} refers to unknown modules: {sorted(referenced - known_module_ids)}")
        if item["disposition"] == "DEFERRED":
            if referenced:
                raise HarnessError(f"Deferred claim {item['claim_id']} cannot be assigned to a module")
            if item["defer_reason_code"] not in (
                "CONFLICTING_FACT", "HIGH_RISK_UNDOCUMENTED", "UNSUPPORTED_INVENTION", "IMMATERIAL_DUPLICATE"
            ):
                raise HarnessError(f"Deferred claim {item['claim_id']} needs an allowed defer reason")
            continue
        if item["defer_reason_code"] != "NOT_DEFERRED":
            raise HarnessError(f"Selected claim {item['claim_id']} must use NOT_DEFERRED")
        if not referenced:
            raise HarnessError(f"Selected claim {item['claim_id']} must be assigned to at least one module")
        for module_id in referenced:
            module = module_by_id[module_id]
            used = {module["lead_claim_id"], *module.get("supporting_claim_ids", [])}
            if item["claim_id"] not in used:
                raise HarnessError(f"Coverage assigns {item['claim_id']} to {module_id}, but that module does not use the claim")
        if item["disposition"] == "LEAD" and not any(module_by_id[module_id]["lead_claim_id"] == item["claim_id"] for module_id in referenced):
            raise HarnessError(f"Lead claim {item['claim_id']} is not the lead claim of any assigned module")


def cmd_compile_plan(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "IDENTITY_LOCKED")
    if session.get("conflict_checkpoint", {}).get("required"):
        raise HarnessError("Full planning is blocked until the user resolves the batched conflict checkpoint")
    compile_started = time.perf_counter()
    raw_plan = load_json(args.plan)
    plan = _preflight_authoring_plan(session, raw_plan)
    validate_plan(session, plan)
    session["plan"] = plan
    session["modules"] = [
        {
            "id": item["id"], "order": item["order"], "plan": item,
            "status": "PENDING", "attempts": [], "accepted_asset": None,
            "repair_plan": None, "user_adjustment": None, "residual_issues": [],
            "cycle_id": 1, "cycle_attempt_count": 0, "repair_budget": 1,
            "cycle_base_asset": None, "revision_history": [],
            "pair_id": next((pair["pair_id"] for pair in (plan["output_strategy"].get("continuous_pair_rendering", {}).get("pair_ids", []) if isinstance(plan["output_strategy"].get("continuous_pair_rendering"), dict) else []) if item["id"] in pair.get("module_ids", [])), None),
        }
        for item in plan["modules"]
    ]
    session["current_index"] = 0
    session["state"] = "AWAITING_PLAN_APPROVAL"
    approval_markdown = _approval_markdown(session)
    if plan.get("planning_mode") != "WEBSITE_DIRECT_V2":
        grouping_markdown = _pair_grouping_markdown(session)
        approval_markdown = approval_markdown.rstrip() + "\n\n" + grouping_markdown.lstrip()
    _persist_approval_document(session, args.session, approval_markdown)
    raw_bytes = len(json.dumps(raw_plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    compiled_bytes = len(json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    session["planning_metrics"] = {
        "authoring_format": raw_plan.get("plan_format", "FULL_V1_0"),
        "authoring_plan_bytes": raw_bytes,
        "compiled_plan_bytes": compiled_bytes,
        "runtime_derived_bytes": max(0, compiled_bytes - raw_bytes),
        "planning_elapsed_seconds": _planning_elapsed_seconds(session),
        "compiler_elapsed_seconds": round(time.perf_counter() - compile_started, 3),
    }
    save(args.session, session, "plan_compiled")
    return summary(session)


def _persist_approval_document(session: dict, session_path: str | Path, markdown: str) -> None:
    # atomic_write_text owns exactly one terminal LF. Hash those same bytes.
    markdown = markdown.rstrip() + "\n"
    approval_path = Path(session_path).with_name("campaign-approval.md")
    atomic_write_text(approval_path, markdown)
    session["approval_document"] = {
        "path": str(approval_path.resolve()),
        "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "format": "MANDATORY_VERBATIM_MARKDOWN",
    }
    session["pair_grouping_document"] = {
        "path": str(approval_path.resolve()),
        "sha256": session["approval_document"]["sha256"],
        "format": "MERGED_IN_CAMPAIGN_APPROVAL",
    }


def cmd_authorize(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_PLAN_APPROVAL")
    if not args.approval_text.strip():
        raise HarnessError("Approval text must preserve the user's explicit words")
    approval_document = session.get("approval_document") or {}
    if args.approval_document_sha256 != approval_document.get("sha256"):
        raise HarnessError(
            "Authorization must cite the exact compiler-generated campaign-approval.md fingerprint; "
            "do not authorize from a handwritten or abbreviated plan summary"
        )
    try:
        actual_approval_sha = hashlib.sha256(Path(approval_document["path"]).read_bytes()).hexdigest()
    except (KeyError, OSError, TypeError) as exc:
        raise HarnessError("The compiler-generated approval file is missing") from exc
    if actual_approval_sha != approval_document.get("sha256"):
        raise HarnessError("The compiler-generated approval file bytes changed; recompile and review again")
    strategy = session["plan"]["output_strategy"]
    if args.approved_module_count != strategy["module_count"]:
        raise HarnessError("Approved module count does not match the compiled plan")
    pair_config = strategy.get("continuous_pair_rendering") or {}
    approved_pair_ratio = getattr(args, "approved_pair_canvas_aspect_ratio", None)
    if session.get("version") == VERSION and getattr(args, "approved_aspect_ratio", None):
        raise HarnessError("v4.1 no longer accepts a user-approved single-screen ratio; approve only the group 3:8 canvas")
    if approved_pair_ratio is not None:
        if approved_pair_ratio != pair_config.get("pair_canvas_aspect_ratio", "3:8"):
            raise HarnessError("Approved pair-canvas aspect ratio does not match the compiled plan")
    elif pair_config.get("enabled"):
        raise HarnessError("Pair campaigns require approval of the 3:8 pair-canvas ratio")
    session["approval"] = {
        "text": args.approval_text,
        "module_count": args.approved_module_count,
        "pair_canvas_aspect_ratio": approved_pair_ratio or pair_config.get("pair_canvas_aspect_ratio"),
        "pair_merge_type": pair_config.get("pair_merge_type") if pair_config.get("enabled") else None,
        "at": now(),
        "delivery_mode": getattr(args, "delivery_mode", "USER_FINAL_REVIEW"),
    }
    session["state"] = "AUTHORIZED"
    save(args.session, session, "plan_authorized")
    return summary(session)


def _local_revision_request_count(session: dict) -> int | None:
    fork = session.get("revision_fork")
    if fork is None:
        return None
    if fork.get("version") != "owned-local-revision-fork-v1":
        raise HarnessError("Unknown local revision authority")
    source = Path(fork["source_session"])
    if source.resolve().parent != Path(session["session_dir"]).resolve() or source.name != "source-session.json":
        raise HarnessError("Local revision source snapshot escaped its owner")
    if file_sha256(source) != fork["source_session_sha256"]:
        raise HarnessError("Original local revision snapshot changed")
    original = load_json(source).get("generation_requests", [])
    requests = session.get("generation_requests", [])
    if (not isinstance(original, list) or not isinstance(requests, list)
            or any(not isinstance(r, dict) for r in requests)
            or requests[:len(original)] != original):
        raise HarnessError("Local revision changed its original signed request history")
    fresh = requests[len(original):]
    if (len(fresh) > 2 or len({r.get("token") for r in fresh}) != len(fresh)
            or any(not r.get("token") or r.get("pair_id") != fork["pair_id"]
                   or r.get("module_ids") != fork["module_ids"]
                   or not Path(r["request"]).resolve().is_relative_to(Path(session["session_dir"]).resolve()) for r in fresh)):
        raise HarnessError("Local revision request history violates its two-request budget")
    signed = Path(session["session_dir"]) / "signed-requests"
    if signed.is_symlink() or signed.is_junction():
        raise HarnessError("Local revision signed requests cannot traverse links")
    actual_files = {p.resolve() for p in signed.glob("*.json")}
    expected_files = {Path(r["request"]).resolve() for r in fresh}
    if actual_files != expected_files:
        raise HarnessError("Local revision signed request history differs from its session; reconcile original requests")
    for row in fresh:
        path = Path(row["request"])
        if path.is_symlink() or file_sha256(path) != row.get("request_sha256"):
            raise HarnessError("Local revision original signed request bytes changed")
        value = load_json(path)
        if (value.get("token") != row["token"] or value.get("pair_id") != fork["pair_id"]
                or value.get("module_ids") != fork["module_ids"]
                or value.get("request_fingerprint") != fingerprint({k: v for k, v in value.items() if k != "request_fingerprint"})):
            raise HarnessError("Local revision original signed request scope changed")
    return len(fresh)


def cmd_generate_next(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    if session.get("version") not in COMPATIBLE_SESSION_VERSIONS:
        raise HarnessError(
            f"Session version {session.get('version')} is not compatible with harness {VERSION}; restart and recompile the approved storyboard matrix"
        )
    require_state(session, "AUTHORIZED")
    revision_requests = _local_revision_request_count(session)
    if revision_requests is not None and revision_requests >= 2:
        raise HarnessError("Local revision request budget exhausted; reconcile the original two requests without reopening the cycle")
    _require_pair_generation_contract(session)
    module = current_module(session)
    pair = _pair_for_index(session, session.get("current_index", 0))
    pair_modules = _pair_modules(session, session.get("current_index", 0)) if pair else [module]
    if pair and module["id"] != pair["module_ids"][0]:
        raise HarnessError("A continuous pair can only be generated from its upper screen")
    allowed = {"PENDING", "REPAIR_PREPARED", "USER_ADJUSTMENT"}
    if any(item["status"] not in allowed for item in pair_modules):
        statuses = ", ".join(f"{item['id']}={item['status']}" for item in pair_modules)
        raise HarnessError(f"Current render container is not ready to generate: {statuses}")
    if any(item.get("cycle_attempt_count", 0) >= 2 for item in pair_modules):
        raise HarnessError("Generation budget exhausted: one initial attempt plus one repair is the hard limit for this revision cycle")
    is_repair = any(item["status"] == "REPAIR_PREPARED" for item in pair_modules)
    token = str(uuid.uuid4())
    references, series_master = generation_references(session)
    edge_reference = continuity_edge_reference(session, args.session)
    edit_target = (
        next(
            item.get("pair_canvas", item["image"]) for item in pair_modules[0]["attempts"]
            if item.get("cycle_id", 1) == pair_modules[0].get("cycle_id", 1)
        )
        if is_repair else pair_modules[0].get("cycle_base_asset")
    )
    output = module["plan"]["output"]
    render_width = int(output["width_px"])
    render_height = int(output["height_px"]) * (2 if pair else 1)
    format_canvas = None
    request_references = list(references)
    if not edit_target:
        format_canvas = ensure_format_canvas(
            Path(args.session).resolve().parent / "format-canvases" /
            f"{render_width}x{render_height}.png",
            render_width,
            render_height,
        )
        request_references.insert(0, format_canvas)
    if edge_reference:
        insert_at = 1 if format_canvas else 0
        request_references.insert(insert_at, edge_reference)
    if pair:
        prompt, screen_prompts = _pair_prompt(session, pair_modules, repair=is_repair, format_canvas_lock=bool(format_canvas))
    else:
        prompt = compile_prompt(session, module, repair=is_repair, format_canvas_lock=bool(format_canvas))
        screen_prompts = [prompt]
    ratio_divisor = math.gcd(render_width, render_height)
    render_mode = "continuous_pair" if pair else ("repair" if is_repair else ("user_adjustment" if module["status"] == "USER_ADJUSTMENT" else "initial"))
    request = {
        "token": token,
        "module_id": module["id"],
        "module_ids": [item["id"] for item in pair_modules],
        "pair_id": pair.get("pair_id") if pair else None,
        "attempt": len(pair_modules[0]["attempts"]) + 1,
        "cycle_id": pair_modules[0].get("cycle_id", 1),
        "cycle_attempt": pair_modules[0].get("cycle_attempt_count", 0) + 1,
        "mode": render_mode,
        "prompt": prompt,
        "prompt_merge_mode": "PAIR_PLAN_MERGE" if pair else "SINGLE_SCREEN_COMPILE",
        "screen_prompts": screen_prompts,
        "reference_images": request_references,
        "product_reference_images": references,
        "continuity_edge_reference": edge_reference,
        "format_canvas": format_canvas,
        "output_surface": {
            "width_px": render_width,
            "height_px": render_height,
            "aspect_ratio": (
                f"{int(output['width_px']) // ratio_divisor}:"
                f"{render_height // ratio_divisor}"
            ),
            "format_canvas_locked": bool(format_canvas),
            "screen_width_px": render_width,
            "screen_height_px": int(output["height_px"]),
            "render_container": "PAIR_CANVAS" if pair else "SINGLE_SCREEN",
        },
        "edit_target": edit_target,
        "context_contract": {
            "canonical_product": session["identity"]["canonical_name"],
            "module_id": module["id"],
            "module_ids": [item["id"] for item in pair_modules],
            "module_duties": [item["plan"]["duty"] for item in pair_modules],
            "single_conclusions": [item["plan"].get("lead_conclusion", item["plan"].get("conclusion")) for item in pair_modules],
            "identity_fingerprint": fingerprint(session["identity"]),
            "approved_plan_fingerprint": fingerprint(session["plan"]),
            "prompt_fingerprint": fingerprint(prompt),
            "series_master": series_master,
            "reference_count": len(request_references),
            "product_reference_count": len(references),
            "continuity_edge_reference": edge_reference,
            "format_canvas_locked": bool(format_canvas),
            "compiled_prompt_locked": True,
            "render_container": "PAIR_CANVAS" if pair else "SINGLE_SCREEN",
            "pair_prompt_merge_mode": "PAIR_PLAN_MERGE" if pair else None,
            "pair_internal_seam": ({
                "start_y": int(output["height_px"]) - 96,
                "end_y": int(output["height_px"]) + 96,
            } if pair else None),
        },
    }
    if session["plan"].get("website_contract_version") is not None:
        request["review_profile"] = session["plan"]["website_contract_version"]
    if pair:
        pair_config = _pair_rendering(session) or {}
        pair_index = next((index for index, item in enumerate(pair_config.get("pair_ids", [])) if item.get("pair_id") == pair["pair_id"]), 0)
        bridges = pair_config.get("pair_to_pair_transition_tokens", [])
        pair_transition = pair_modules[1]["plan"].get("transition_contract") or {}
        request["pair_render"] = {
            "mode": "CONTINUOUS_PAIR",
            "pair_merge_type": "PAIR_PLAN_MERGE",
            "pair_id": pair["pair_id"],
            "module_ids": [item["id"] for item in pair_modules],
            "canvas_width_px": render_width,
            "canvas_height_px": render_height,
            "module_height_px": int(output["height_px"]),
            "slice_coordinates": [
                {"module_id": item["id"], "start_y": offset * int(output["height_px"]), "end_y": (offset + 1) * int(output["height_px"])}
                for offset, item in enumerate(pair_modules)
            ],
            "internal_seam_zone": {"start_y": int(output["height_px"]) - 96, "end_y": int(output["height_px"]) + 96},
            "internal_seam_method_id": pair_transition.get("method_id"),
            "internal_seam_carrier": pair_transition.get("seam_carrier"),
            "internal_seam_source_region": pair_transition.get("source_region_previous"),
            "internal_seam_target_region": pair_transition.get("target_region_current"),
            "internal_seam_geometry": pair_transition.get("handoff_geometry"),
            "pair_transition_token": pair.get("pair_transition_token", pair.get("transition_token", pair["pair_id"])),
            "incoming_transition_token": bridges[pair_index - 1] if pair_index > 0 and pair_index - 1 < len(bridges) else None,
            "outgoing_transition_token": bridges[pair_index] if pair_index < len(bridges) else None,
        "prompt_merge_mode": "PAIR_PLAN_MERGE",
            "merged_prompt_fingerprint": fingerprint(prompt),
            "screen_contracts": [{"module_id": item["id"], "prompt_fingerprint": fingerprint(screen_prompts[offset])} for offset, item in enumerate(pair_modules)],
        }
    if any(item.get("local_contract_override") is not None for item in pair_modules):
        request["local_contract_bindings"] = [{"module_id": item["id"], "effective_plan_sha256": fingerprint(effective_plan(item)),
            "override_sha256": (item.get("local_contract_override") or {}).get("sha256")} for item in pair_modules]
    request["request_fingerprint"] = fingerprint(request)
    atomic_write_json(args.out, request)
    signed_request = preserve_signed_request(Path(args.out), Path(args.session).resolve().parent, token)
    session.setdefault("generation_requests", []).append({
        "token": token,
        "pair_id": request.get("pair_id"),
        "module_ids": request["module_ids"],
        "request": str(signed_request),
        "request_sha256": file_sha256(signed_request),
        "request_fingerprint": request["request_fingerprint"],
        "issued_at": now(),
    })
    session["active_token"] = token
    session["active_render_container"] = {"mode": "CONTINUOUS_PAIR" if pair else "SINGLE_SCREEN", "pair_id": pair.get("pair_id") if pair else None, "module_ids": [item["id"] for item in pair_modules]}
    session["state"] = "AWAITING_GENERATED_MODULE"
    for item in pair_modules:
        item["status"] = "AWAITING_GENERATION"
    save(args.session, session, f"generation_requested:{pair['pair_id'] if pair else module['id']}")
    return {"request": str(Path(args.out).resolve()), **request, "approved_module_contracts": review_contracts(pair_modules, len(session["modules"]))}


def cmd_record_generation(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    _require_pair_generation_contract(session)
    workspace = Path(args.session).resolve().parent
    if Path(session.get("session_dir", workspace)).resolve() != workspace:
        raise HarnessError("Generation session directory changed")
    session["session_dir"] = str(workspace)
    require_state(session, "AWAITING_GENERATED_MODULE")
    if args.token != session.get("active_token"):
        raise HarnessError("Stale or incorrect generation token")
    image = Path(args.image)
    if not image.is_file():
        raise HarnessError(f"Generated image does not exist: {image}")
    module = current_module(session)
    pair = _pair_for_index(session, session.get("current_index", 0))
    pair_modules = _pair_modules(session, session.get("current_index", 0)) if pair else [module]
    output = module["plan"]["output"]
    target_height = int(output["height_px"]) * (2 if pair else 1)
    original = preserve_provider_original(image, workspace, args.token)
    normalized = normalize_generated_image(
        original,
        int(output["width_px"]),
        target_height,
    )
    attempt_number = len(pair_modules[0]["attempts"]) + 1
    slices = _pair_canvas_slices(normalized["path"], session, pair, attempt_number) if pair else []
    slice_by_id = {item["module_id"]: item for item in slices}
    for item in pair_modules:
        item["cycle_attempt_count"] = item.get("cycle_attempt_count", 0) + 1
        asset = slice_by_id[item["id"]]["path"] if pair else normalized["path"]
        item["attempts"].append({
            "number": len(item["attempts"]) + 1,
            "cycle_id": item.get("cycle_id", 1),
            "cycle_attempt": item["cycle_attempt_count"],
            "image": asset, "pair_canvas": normalized["path"] if pair else None,
            "pair_id": pair.get("pair_id") if pair else None,
            "pair_slice": slice_by_id.get(item["id"]),
            "slice_integrity": {
                "checked": bool(pair),
                "status": "PASS" if pair else "NOT_APPLICABLE",
                "aspect_ratio": "3:4" if pair else None,
                "width_px": int(output["width_px"]),
                "height_px": int(output["height_px"]),
            },
            "token": args.token,
            "source_image": normalized["source_path"],
            "provider_output_path": str(image.resolve()),
            "normalization": {key: value for key, value in normalized.items() if key != "path"},
            "source_image_sha256": file_sha256(normalized["source_path"]),
            "normalized_image_sha256": file_sha256(normalized["path"]),
            "source_width_px": normalized["source_width_px"],
            "source_height_px": normalized["source_height_px"],
            "width_px": int(output["width_px"]),
            "height_px": int(output["height_px"]),
            "render_canvas_width_px": normalized["width_px"],
            "render_canvas_height_px": normalized["height_px"],
            "format_normalization": normalized["mode"],
            "relative_ratio_error": normalized["relative_ratio_error"],
            "bounded_crop_fraction": normalized.get("bounded_crop_fraction", 0.0),
            "recorded_at": now(), "judgment": None,
        })
        item["status"] = "AWAITING_AUDIT"
    session["active_token"] = None
    session["active_render_container"] = None
    session["state"] = "AWAITING_PIXEL_AUDIT"
    save(args.session, session, f"generation_recorded:{pair['pair_id'] if pair else module['id']}")
    return summary(session)


def cmd_record_generation_batch(args: argparse.Namespace) -> dict:
    """Recover one issued render request from one or more candidate rasters.

    All candidates must belong to the current signed request. Exactly one candidate
    is selected and enters the ordinary normalization, slicing and audit path; the
    remaining candidates are retained as a hash-bound recovery ledger only.
    """
    session = load_json(args.session)
    require_state(session, "AWAITING_GENERATED_MODULE")
    manifest = load_json(args.manifest)
    validate_schema(manifest, "generation-import.schema.json", "generation import manifest")
    request_path = Path(manifest["request"])
    request = load_json(request_path)
    embedded_fingerprint = request.pop("request_fingerprint", None)
    calculated_fingerprint = fingerprint(request)
    request["request_fingerprint"] = embedded_fingerprint
    if embedded_fingerprint != calculated_fingerprint or manifest["request_fingerprint"] != calculated_fingerprint:
        raise HarnessError("Generation import request fingerprint is stale or invalid")
    ledger_entry = next(
        (
            item for item in session.get("generation_requests", [])
            if item.get("token") == request.get("token")
            and item.get("request_fingerprint") == calculated_fingerprint
        ),
        None,
    )
    if ledger_entry is None:
        raise HarnessError("Generation import request was not issued by this session")
    if Path(ledger_entry["request"]).resolve() != request_path.resolve():
        raise HarnessError("Generation import request path does not match the session ledger")
    candidates = manifest["candidates"]
    selected_index = manifest["selected_candidate"]
    if selected_index >= len(candidates):
        raise HarnessError("selected_candidate is outside the candidate list")
    expected_ids = request.get("module_ids")
    for candidate in candidates:
        if candidate["token"] != request.get("token") or candidate["token"] != session.get("active_token"):
            raise HarnessError("Generation import token does not match the active request")
        if candidate["pair_id"] != request.get("pair_id") or candidate["module_ids"] != expected_ids:
            raise HarnessError("Generation import pair/module order does not match the signed request")
        image_path = Path(candidate["image"])
        if not image_path.is_file() or file_sha256(image_path) != candidate["image_sha256"].lower():
            raise HarnessError(f"Generation candidate hash does not match: {image_path}")
    session.setdefault("candidate_generation_imports", []).append({
        "request": str(request_path.resolve()),
        "request_fingerprint": calculated_fingerprint,
        "selected_candidate": selected_index,
        "candidates": candidates,
        "recorded_at": now(),
    })
    save(args.session, session, f"candidate_batch_bound:{request.get('pair_id') or request.get('module_id')}")
    selected = candidates[selected_index]
    return cmd_record_generation(argparse.Namespace(
        session=args.session,
        token=selected["token"],
        image=selected["image"],
    ))


def advance_after_acceptance(session: dict) -> None:
    if all(module["status"] in COMPLETE_STATUSES for module in session["modules"]):
        pair_config = (session.get("plan", {}).get("output_strategy", {}).get("continuous_pair_rendering") or {})
        if pair_config.get("enabled"):
            session["state"] = (
                "AUTO_FINALIZE_AUTHORIZED"
                if (session.get("approval") or {}).get("delivery_mode") == "AUTO_FINALIZE_AUTHORIZED"
                else "AWAITING_USER_FINAL_REVIEW"
            )
        else:
            session["state"] = "AWAITING_ASSEMBLY"
        return
    next_index = next(i for i, module in enumerate(session["modules"]) if module["status"] not in COMPLETE_STATUSES)
    session["current_index"] = next_index
    session["state"] = "AWAITING_PIXEL_AUDIT" if session["modules"][next_index]["status"] == "AWAITING_AUDIT" else "AUTHORIZED"


def cmd_audit_current(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_PIXEL_AUDIT")
    pair_config = (session.get("plan", {}).get("output_strategy", {}).get("continuous_pair_rendering") or {})
    if isinstance(pair_config, dict) and pair_config.get("enabled"):
        raise HarnessError("v4.1 pair campaigns must use one group-level audit-pair judgment; audit-current is legacy-only")
    module = current_module(session)
    attempt = module["attempts"][-1]
    expected = module["plan"]["output"]
    actual_width, actual_height = image_dimensions(attempt["image"])
    if (actual_width, actual_height) != (expected["width_px"], expected["height_px"]):
        raise HarnessError(
            "Recorded module dimensions changed after generation binding: "
            f"expected {expected['width_px']}x{expected['height_px']}, "
            f"found {actual_width}x{actual_height}"
        )
    judgment = load_json(args.judgment)
    validate_schema(judgment, "module-judgment.schema.json", "module judgment")
    validate_artifact_binding(judgment, attempt["image"], f"module {module['id']} judgment")
    validate_module_judgment(judgment, module)
    attempt["judgment"] = judgment
    pair = _pair_for_index(session, session.get("current_index", 0))
    pair_modules = _pair_modules(session, session.get("current_index", 0)) if pair else [module]
    if judgment["status"] == "PASS":
        module["accepted_asset"] = attempt["image"]
        module["status"] = "COMPLETE"
        module["residual_issues"] = judgment.get("residual_issues", [])
        advance_after_acceptance(session)
    elif module.get("cycle_attempt_count", 0) == 1:
        for item in pair_modules:
            item["status"] = "REPAIR_REQUIRED"
            item["accepted_asset"] = None
        session["state"] = "REPAIR_REQUIRED"
    else:
        module["accepted_asset"] = attempt["image"]
        module["status"] = "COMPLETE_WITH_RESIDUALS"
        module["residual_issues"] = [item.get("visible_evidence", str(item)) for item in judgment["material_failures"]]
        advance_after_acceptance(session)
    save(args.session, session, f"pixel_audited:{module['id']}:{judgment['status']}")
    return summary(session)


def cmd_audit_pair(args: argparse.Namespace) -> dict:
    """Audit one generated two-screen canvas as a single commercial unit."""
    session = load_json(args.session)
    require_state(session, "AWAITING_PIXEL_AUDIT")
    upper = current_module(session)
    pair = _pair_for_index(session, session.get("current_index", 0))
    if not pair or pair.get("module_ids", [None])[0] != upper["id"]:
        raise HarnessError("The current render container is not an ordered two-screen pair")
    pair_modules = _pair_modules(session, session.get("current_index", 0))
    lineage = pair_artifacts(session, pair["pair_id"])
    validate_request_contracts(session, lineage, pair_modules)
    judgment = load_json(args.judgment)
    validate_schema(judgment, review_schema_name(session["plan"]), "pair judgment")
    require_fields(
        judgment,
        ("pair_id", "module_ids", "status", "audit_complete", "pair_canvas", "checks", "screen_coverage", "internal_seam_evidence", "material_failures"),
        "pair judgment",
    )
    if judgment["pair_id"] != pair["pair_id"] or judgment["module_ids"] != pair["module_ids"]:
        raise HarnessError("Pair judgment must match the current adjacent pair in storyboard order")
    latest_pair_canvas = pair_modules[0]["attempts"][-1].get("pair_canvas")
    if not latest_pair_canvas:
        raise HarnessError("Pair audit requires the recorded pair canvas")
    validate_artifact_binding(judgment, latest_pair_canvas, f"pair {pair['pair_id']} judgment")
    if judgment["status"] not in ("PASS", "MATERIAL_FAIL") or judgment["audit_complete"] is not True:
        raise HarnessError("Pair judgment must be complete and use PASS or MATERIAL_FAIL")
    if len(judgment["screen_coverage"]) != len(pair_modules):
        raise HarnessError("Pair judgment must cover both atomic screen duties")
    pair_canvas = judgment["pair_canvas"]
    require_fields(pair_canvas, ("aspect_ratio", "width_px", "height_px", "decomposition"), "pair canvas audit")
    if pair_canvas["aspect_ratio"] != pair.get("pair_canvas_aspect_ratio", "3:8"):
        raise HarnessError("Pair canvas audit must confirm the approved 3:8 group canvas")
    expected_width = session["plan"]["output_strategy"]["canvas_width_px"]
    expected_height = session["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_canvas_height_px"]
    if (pair_canvas["width_px"], pair_canvas["height_px"]) != (expected_width, expected_height):
        raise HarnessError("Pair canvas audit dimensions must match the approved group canvas")
    if pair_canvas["decomposition"] != "TWO_FIXED_SINGLE_SCREEN_WINDOWS":
        raise HarnessError("Pair canvas audit must confirm decomposition into two fixed single-screen windows")
    coverage_ids = []
    for coverage in judgment["screen_coverage"]:
        require_fields(coverage, ("module_id", "duty_visible", "selling_points_visible", "visible_evidence"), "pair screen coverage")
        if coverage["module_id"] not in pair["module_ids"] or coverage["module_id"] in coverage_ids:
            raise HarnessError("Pair screen coverage must contain each adjacent module exactly once")
        if judgment["status"] == "PASS" and (coverage["duty_visible"] is not True or not coverage["selling_points_visible"]):
            raise HarnessError("PASS pair screen coverage must visibly confirm each screen duty and selling-point payload")
        coverage_ids.append(coverage["module_id"])
    if coverage_ids != pair["module_ids"]:
        raise HarnessError("Pair screen coverage must preserve upper-to-lower screen order")
    seam = judgment["internal_seam_evidence"]
    require_fields(
        seam,
        ("method_id", "source_region", "target_region", "carrier", "geometry", "hard_cut_absent", "method_specific_pass", "visible_evidence"),
        "pair internal seam evidence",
    )
    planned = pair_modules[1]["plan"].get("transition_contract") or {}
    if seam["method_id"] != planned.get("method_id"):
        raise HarnessError("Pair seam evidence method does not match the approved lower-screen incoming transition contract")
    if judgment["status"] == "PASS" and (seam["hard_cut_absent"] is not True or seam["method_specific_pass"] is not True):
        raise HarnessError("PASS pair seam evidence must pass the declared method and contain no hard cut")
    checks = judgment["checks"]
    required_checks = ("identity", "facts", "pair_duties", "visual_proof", "legibility", "copy_binding", "internal_seam", "spatial_integrity", "commercial_expression")
    if session["plan"].get("website_contract_version") == "WEBSITE_LEAN_V1":
        from runtime.website_review import CHECKS as required_checks
    missing = [name for name in required_checks if name not in checks]
    if missing:
        raise HarnessError("Pair judgment missing checks: " + ", ".join(missing))
    require_identity_silhouette(judgment, f"pair {judgment['pair_id']} judgment", allow_material_failure=True, expected_scope=pair_observed_scope(judgment["module_judgments"]))
    failures = list(judgment["material_failures"])
    failures.extend(validate_atomic_reports(judgment, pair_modules, lineage))
    revision_contract = revision_review_contract(session, pair["pair_id"])
    failures.extend(validate_revision_observation(revision_contract, judgment, lineage))
    if judgment["status"] == "MATERIAL_FAIL" and not failures:
        raise HarnessError("MATERIAL_FAIL pair judgment requires visible material failures")
    # These findings come only from explicit negative pixel observations. Missing
    # fields remain schema errors and never grant an automatic image repair.
    observed_failures = [
        {"check": name, "visible_evidence": checks[name]["visible_evidence"]}
        for name in required_checks if checks[name]["pass"] is False
    ]
    if seam["hard_cut_absent"] is False or seam["method_specific_pass"] is False:
        observed_failures.append({"check": "internal_seam", "visible_evidence": seam["visible_evidence"]})
    silhouette = judgment["identity_silhouette"]
    if any(silhouette[name] is False for name in ("shape_consistent", "colorway_consistent", "texture_consistent")):
        observed_failures.append({"check": "identity_silhouette", "visible_evidence": silhouette["visible_evidence"]})
    for coverage in judgment["screen_coverage"]:
        if coverage["duty_visible"] is False or not coverage["selling_points_visible"]:
            observed_failures.append({"check": "screen_coverage", "module_id": coverage["module_id"], "visible_evidence": coverage["visible_evidence"]})
    for finding in observed_failures:
        if finding not in failures:
            failures.append(finding)
    if session.get("plan", {}).get("information_contract_version") == "4.1" and session["plan"].get("website_contract_version") != "WEBSITE_LEAN_V1":
        info = judgment.get("information_audit")
        require_fields(info or {}, ("upper_buyer_question", "lower_buyer_question", "new_information_visible", "new_information_evidence", "repetition_without_progression", "hero_subject_clear", "co_equal_hero_detected", "purchase_judgment_enabled", "detail_page_not_posterized"), "pair information audit")
        if info["new_information_visible"] is not True or info["repetition_without_progression"] is True or info["purchase_judgment_enabled"] is not True or info["detail_page_not_posterized"] is not True:
            failures.append({"check": "information_audit", "visible_evidence": info.get("new_information_evidence", "Pair lacks visible information progression")})
        if info["hero_subject_clear"] is not True or info["co_equal_hero_detected"] is True:
            failures.append({"check": "hero_uniqueness", "visible_evidence": "首屏主英雄不唯一或存在等量主视觉实体"})
    failed_checks = [name for name in required_checks if not checks[name].get("pass", False)]
    unauthorized = judgment.get("unauthorized_ocr_text", [])
    forbidden = judgment.get("forbidden_text_hits", [])
    hard_failures = []
    if unauthorized:
        hard_failures.append("Pair canvas contains unauthorized promotional text")
    if forbidden:
        hard_failures.append("Pair canvas contains workflow metadata")
    if judgment["status"] == "PASS" and (failures or failed_checks or hard_failures):
        raise HarnessError("PASS pair judgment violates group audit gates: " + "; ".join(hard_failures or failed_checks))
    if judgment["status"] == "MATERIAL_FAIL" and not failures:
        raise HarnessError("MATERIAL_FAIL pair judgment requires visible material failures")
    cycle_counts = [item.get("cycle_attempt_count") for item in pair_modules]
    if any(type(count) is not int for count in cycle_counts) or cycle_counts[0] not in (1, 2) or cycle_counts != [cycle_counts[0]] * len(pair_modules):
        raise HarnessError("Pair audit requires the same first or second cycle attempt on both screens")
    # Preserve raw reports unchanged. Whole-pair preservation claims require
    # both complete atomic reports; never invent an identity pass on failure.
    effective_passed = [name for name in judgment.get("passed_layers", []) if name in checks and checks[name]["pass"] is True]
    if any(report["status"] != "PASS" for report in judgment["module_judgments"]):
        effective_passed = []
    if silhouette["comparison_scope"] != "VISIBLE_COMPLETE_PRODUCT" or any(silhouette[name] is False for name in ("shape_consistent", "colorway_consistent", "texture_consistent")):
        effective_passed = [name for name in effective_passed if name != "identity"]
    if seam["hard_cut_absent"] is False or seam["method_specific_pass"] is False:
        effective_passed = [name for name in effective_passed if name != "internal_seam"]
    recorded_judgment = {**judgment, "material_failures": failures, "effective_passed_layers": effective_passed}
    review_evidence = record_pair_audit(args.judgment, judgment, recorded_judgment, lineage, revision_contract=revision_contract)
    for item in pair_modules:
        attempt = item["attempts"][-1]
        attempt["judgment"] = recorded_judgment
        attempt["review_evidence"] = review_evidence
        attempt["artifact_lineage_sha256"] = lineage["sha256"]
        if judgment["status"] == "PASS":
            item["accepted_asset"] = attempt["image"]
            item["status"] = "COMPLETE"
            item["residual_issues"] = judgment.get("residual_issues", [])
        elif item.get("cycle_attempt_count", 0) == 1:
            item["status"] = "REPAIR_REQUIRED"
            item["accepted_asset"] = None
        else:
            item["accepted_asset"] = attempt["image"]
            item["status"] = "COMPLETE_WITH_RESIDUALS"
            item["residual_issues"] = [failure.get("visible_evidence", str(failure)) for failure in failures]
    if judgment["status"] == "PASS":
        advance_after_acceptance(session)
    elif all(item.get("status") in COMPLETE_STATUSES for item in pair_modules):
        # The second failed attempt is the deterministic cycle endpoint. Keep
        # the residuals and move directly to the next ordered pair.
        advance_after_acceptance(session)
    else:
        session["state"] = "REPAIR_REQUIRED"
    save(args.session, session, f"pair_audited:{pair['pair_id']}:{judgment['status']}")
    return summary(session)


def cmd_prepare_repair(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "REPAIR_REQUIRED")
    repair = load_json(args.repair_plan)
    validate_schema(repair, "repair-plan.schema.json", "repair plan")
    require_fields(repair, ("module_id", "material_failures", "passed_layers", "root_cause", "unified_strategy", "expected_visible_change"), "repair plan")
    module = current_module(session)
    if module.get("cycle_attempt_count", 0) != 1:
        raise HarnessError("Automatic repair can be prepared only after the first failed attempt in the current cycle")
    if repair["module_id"] != module["id"]:
        raise HarnessError("Repair plan does not target current module")
    pair = _pair_for_index(session, session.get("current_index", 0))
    pair_modules = _pair_modules(session, session.get("current_index", 0)) if pair else [module]
    for item in pair_modules:
        if item.get("cycle_attempt_count", 0) != 1:
            raise HarnessError("Continuous pair repair requires both screens to be on their first attempt")
        item["repair_plan"] = repair
        item["status"] = "REPAIR_PREPARED"
        item["accepted_asset"] = None
    if pair:
        session["current_index"] = min(i for i, item in enumerate(session["modules"]) if item["id"] in {m["id"] for m in pair_modules})
    session["state"] = "AUTHORIZED"
    save(args.session, session, f"repair_prepared:{pair['pair_id'] if pair else module['id']}")
    return summary(session)


def _sibling_preservation_adjustment(module_id: str) -> dict:
    return {
        "module_id": module_id,
        "verbatim_instruction": "Preserve sibling screen while regenerating the approved continuous pair.",
        "change_scope": "local", "change_kind": "OTHER_LOCAL",
        "change_only": ["Preserve this sibling screen; only reconnect its approved continuous-pair seam."],
        "preserve": ["Keep this sibling's accepted composition, product geometry, wording and visual proof."],
        "must_not_regress": ["Keep this sibling's identity, verified facts, readability and approved seam."],
        "changes_identity": False, "changes_facts": False, "changes_target_buyer": False,
        "changes_campaign_style": False, "changes_module_sequence": False,
    }


def cmd_apply_user_adjustment(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AUTHORIZED", "REPAIR_EXHAUSTED", "AWAITING_ASSEMBLY", "AWAITING_USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED", "FINALIZED")
    adjustment = load_json(args.adjustment)
    validate_schema(adjustment, "user-adjustment.schema.json", "user adjustment")
    require_fields(adjustment, ("module_id", "verbatim_instruction", "change_scope", "change_kind", "change_only", "preserve", "must_not_regress"), "user adjustment")
    global_flags = ("changes_identity", "changes_facts", "changes_target_buyer", "changes_campaign_style", "changes_module_sequence")
    if adjustment.get("change_scope") != "local" or any(adjustment.get(flag) for flag in global_flags):
        raise HarnessError("This is a global change; replan or restart instead of reopening one module")
    try:
        index = next(i for i, module in enumerate(session["modules"]) if module["id"] == adjustment["module_id"])
    except StopIteration as exc:
        raise HarnessError("Unknown module in user adjustment") from exc
    module = session["modules"][index]
    base_asset = module.get("accepted_asset") or (module.get("attempts") or [{}])[-1].get("image")
    if not base_asset:
        raise HarnessError("A local adjustment requires an existing generated module")
    module["prior_accepted_asset"] = module.get("accepted_asset")
    module["accepted_asset"] = None
    module.setdefault("revision_history", []).append({
        "cycle_id": module.get("cycle_id", 1),
        "instruction": adjustment["verbatim_instruction"],
        "attempt_count": module.get("cycle_attempt_count", 0),
        "closed_at": now(),
    })
    module["cycle_id"] = module.get("cycle_id", 1) + 1
    module["cycle_attempt_count"] = 0
    module["cycle_base_asset"] = ((module.get("attempts") or [{}])[-1].get("pair_canvas") or base_asset)
    module["repair_plan"] = None
    module["user_adjustment"] = adjustment
    module["status"] = "USER_ADJUSTMENT"
    pair = _pair_for_index(session, index)
    if pair:
        pair_members = _pair_modules(session, index)
        pair_base = (module.get("attempts") or [{}])[-1].get("pair_canvas") or base_asset
        for sibling in pair_members:
            if sibling["id"] == module["id"]:
                continue
            sibling["prior_accepted_asset"] = sibling.get("accepted_asset")
            sibling["accepted_asset"] = None
            sibling.setdefault("revision_history", []).append({
                "cycle_id": sibling.get("cycle_id", 1),
                "instruction": "Preserve sibling screen while regenerating the approved continuous pair.",
                "source_module_id": module["id"],
                "verbatim_user_instruction": adjustment["verbatim_instruction"],
                "attempt_count": sibling.get("cycle_attempt_count", 0),
                "closed_at": now(),
            })
            sibling["cycle_id"] = sibling.get("cycle_id", 1) + 1
            sibling["cycle_attempt_count"] = 0
            sibling["cycle_base_asset"] = pair_base
            sibling["repair_plan"] = None
            sibling["user_adjustment"] = _sibling_preservation_adjustment(sibling["id"])
            sibling["status"] = "USER_ADJUSTMENT"
    ledger = session.setdefault("feedback_constraints", {"global": {"preserve": [], "must_not_regress": []}, "by_module": {}})
    module_ledger = ledger.setdefault("by_module", {}).setdefault(module["id"], {"preserve": [], "must_not_regress": []})
    for key in ("preserve", "must_not_regress"):
        module_ledger[key] = _unique_strings(module_ledger.get(key, []) + adjustment[key])
    session["current_index"] = (
        min(i for i, item in enumerate(session["modules"]) if item["id"] in pair["module_ids"])
        if pair else index
    )
    session["assembly"] = None
    session["long_page_audit"] = None
    session["final"] = None
    session["state"] = "AUTHORIZED"
    save(args.session, session, f"user_adjustment_applied:{module['id']}")
    return summary(session)


def cmd_fork_local_revision(args: argparse.Namespace) -> dict:
    """Open one owned local cycle; never reopen or mutate the source session."""
    import os

    source_path, target = Path(args.source_session), Path(args.session)
    for path in (source_path, target, Path(args.revision_intent)):
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise HarnessError("Local revision cannot traverse links")
    root = target.resolve().parent
    if (target.name != "session.json" or source_path.name != "source-session.json"
            or source_path.resolve().parent != root
            or Path(args.revision_intent).resolve().parent != root):
        raise HarnessError("Local revision inputs must belong to the new owned workspace")
    source_bytes = source_path.read_bytes()
    if file_sha256(source_path) != args.source_session_sha256:
        raise HarnessError("Original local revision source bytes changed")
    source = load_json(source_path)
    original_root = Path(source.get("session_dir") or "")
    if (not original_root.is_absolute() or not original_root.is_dir()
            or root.is_relative_to(original_root.resolve())
            or original_root.resolve().is_relative_to(root)):
        raise HarnessError("Local revision requires a separate source and target workspace")
    cmd_inspect_campaign_review_contract(argparse.Namespace(session=str(source_path)))
    if source.get("pending") or source.get("active_token"):
        raise HarnessError("Reconcile the source's unfinished generation before revising it")
    intent = load_json(args.revision_intent)
    def fingerprint(value: dict) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if (intent.get("version") != "domestic-detail-local-revision-v1"
            or intent.get("sha256") != fingerprint({k: v for k, v in intent.items() if k != "sha256"})
            or intent.get("job_id") != root.name
            or intent.get("source_job_id") != original_root.name
            or intent.get("job_id") == intent.get("source_job_id")
            or intent.get("source_session_sha256") != args.source_session_sha256
            or not all(isinstance(intent.get(k), str) and len(intent[k].strip()) >= 2
                       for k in ("workspace_id", "actor_id", "approval_text"))):
        raise HarnessError("A complete original local revision authority is required")
    adjustment = intent.get("adjustment")
    if not isinstance(adjustment, dict):
        raise HarnessError("Explicit original local change instructions are required")
    validate_schema(adjustment, "user-adjustment.schema.json", "user adjustment")
    flags = ("changes_identity", "changes_facts", "changes_target_buyer", "changes_campaign_style", "changes_module_sequence")
    if (adjustment.get("change_scope") != "local" or any(adjustment.get(k) is not False for k in flags)
            or not str(adjustment.get("verbatim_instruction") or "").strip()
            or str(adjustment.get("verbatim_instruction")).strip().lower() in {"continue", "retry", "继续", "重试", "重新生成"}
            or any(not values or any(not isinstance(v, str) or not v.strip() for v in values)
                   for values in (adjustment.get("change_only"), adjustment.get("preserve"), adjustment.get("must_not_regress")))):
        raise HarnessError("Global or unspecified changes require replanning and new approval")
    modules = source["modules"]
    index = next((i for i, m in enumerate(modules) if m["id"] == adjustment["module_id"]), None)
    if index is None:
        raise HarnessError("Unknown module in local revision")
    pair = _pair_for_index(source, index)
    if pair is None or len(pair["module_ids"]) != 2:
        raise HarnessError("Local revision requires its exact approved continuous pair")
    contract_override = scope_contract_override(root, intent, source, pair)
    fork = {"version": "owned-local-revision-fork-v1", "intent_sha256": intent["sha256"],
            "source_session": str(source_path.resolve()), "source_session_sha256": args.source_session_sha256,
            "source_job_id": intent["source_job_id"], "job_id": intent["job_id"],
            "source_approval": source["approval"], "source_revision_fork": source.get("revision_fork"),
            "adjustment": adjustment, "pair_id": pair["pair_id"], "module_ids": pair["module_ids"],
            "maximum_new_image_requests": 2, "delivery_mode": "USER_FINAL_REVIEW"}
    approval = {**source["approval"], "delivery_mode": "USER_FINAL_REVIEW",
                "text": intent["approval_text"], "local_revision_intent_sha256": intent["sha256"]}
    if contract_override is not None:
        fork["local_contract_override_sha256"] = contract_override["sha256"]
        approval["local_contract_override_sha256"] = contract_override["sha256"]
    feedback = json.loads(json.dumps(source.get("feedback_constraints") or {"global": {"preserve": [], "must_not_regress": []}, "by_module": {}}))
    local_feedback = feedback.setdefault("by_module", {}).setdefault(adjustment["module_id"], {"preserve": [], "must_not_regress": []})
    for key in ("preserve", "must_not_regress"):
        local_feedback[key] = _unique_strings(local_feedback.get(key, []) + adjustment[key])
    def verify_existing() -> dict:
        existing = load_json(target)
        if (existing.get("revision_fork") != fork or existing.get("approval") != approval
                or existing.get("session_dir") != str(root) or len(existing.get("modules", [])) != len(modules)):
            raise HarnessError("Existing local revision differs from its original authority")
        controls = {"state", "events", "updated_at", "final", "assembly", "long_page_audit", "session_dir", "revision_fork", "modules", "approval", "pending", "active_token", "active_render_container", "current_index", "feedback_constraints", "generation_requests"}
        _local_revision_request_count(existing)
        if existing.get("feedback_constraints") != feedback:
            raise HarnessError("Existing local revision changed its feedback scope")
        if {k: v for k, v in existing.items() if k not in controls} != {k: v for k, v in source.items() if k not in controls}:
            raise HarnessError("Existing local revision changed its approved source contract")
        for prior, current in zip(modules, existing["modules"]):
            if prior["id"] not in pair["module_ids"]:
                if current != prior:
                    raise HarnessError("A local revision changed another accepted pair")
                continue
            old_attempts, attempts = prior.get("attempts", []), current.get("attempts", [])
            if (current.get("id") != prior["id"] or current.get("cycle_id") != prior.get("cycle_id", 1) + 1
                    or type(current.get("cycle_attempt_count")) is not int or not 0 <= current["cycle_attempt_count"] <= 2
                    or attempts[:len(old_attempts)] != old_attempts or not 0 <= len(attempts) - len(old_attempts) <= 2
                    or current["cycle_attempt_count"] != len(attempts) - len(old_attempts)
                    or any(a.get("cycle_id") != current["cycle_id"] or a.get("cycle_attempt") != i
                           for i, a in enumerate(attempts[len(old_attempts):], 1))
                    or current.get("user_adjustment") != (adjustment if prior["id"] == adjustment["module_id"] else _sibling_preservation_adjustment(prior["id"]))
                    or current.get("plan") != prior.get("plan")
                    or current.get("local_contract_override") != (contract_override if prior["id"] == adjustment["module_id"] and contract_override is not None else prior.get("local_contract_override"))):
                raise HarnessError("Existing local revision changed its original cycle or image ceiling")
        return {"session": str(target.resolve()), "revision_fork": fork, "reused": True}
    if target.exists():
        return verify_existing()
    clone = json.loads(source_bytes)
    clone["session_dir"] = str(root)
    clone["approval"] = approval
    clone["revision_fork"] = fork
    if contract_override is not None:
        next(m for m in clone["modules"] if m["id"] == adjustment["module_id"])["local_contract_override"] = contract_override
    staging = root / (".local-revision-" + uuid.uuid4().hex + ".json")
    adjustment_path = root / (".local-adjustment-" + uuid.uuid4().hex + ".json")
    atomic_write_json(staging, clone)
    atomic_write_json(adjustment_path, adjustment)
    cmd_apply_user_adjustment(argparse.Namespace(session=str(staging), adjustment=str(adjustment_path)))
    # Publish the complete new session exclusively. A retry cannot overwrite a
    # session that already advanced, reopen its cycle or touch source artifacts.
    with staging.open("rb+") as handle:
        os.fsync(handle.fileno())
    try:
        os.link(staging, target)
    except FileExistsError:
        result = verify_existing()
    else:
        result = {"session": str(target.resolve()), "revision_fork": fork, "reused": False}
    if source_path.read_bytes() != source_bytes:
        raise HarnessError("Original source snapshot changed while creating a revision")
    staging.unlink()
    adjustment_path.unlink()
    return result


def cmd_fork_campaign_review(args: argparse.Namespace) -> dict:
    """Create an owned review snapshot of the original accepted campaign."""
    source_path, target = Path(args.source_session), Path(args.session)
    expected = args.source_session_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or file_sha256(source_path) != expected:
        raise HarnessError("Original campaign-review session bytes changed")
    if source_path.resolve().parent != target.resolve().parent or source_path.resolve() == target.resolve():
        raise HarnessError("Campaign-review source snapshot must belong to the new review workspace")
    if source_path.is_symlink() or target.is_symlink() or target.parent.is_symlink() or target.parent.is_junction():
        raise HarnessError("Campaign-review snapshots cannot traverse links")
    source = load_json(source_path)
    inspection = cmd_inspect_campaign_review_contract(argparse.Namespace(session=str(source_path)))
    if source.get("pending") is not None:
        raise HarnessError("Reconcile the source's unfinished generation before reviewing it")
    def authority_sha(value: dict) -> str:
        controls = {"state", "events", "updated_at", "final", "assembly", "long_page_audit", "session_dir", "review_fork"}
        return hashlib.sha256(json.dumps(
            {key: item for key, item in value.items() if key not in controls},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
    original_authority_sha = authority_sha(source)
    accepted_sha = hashlib.sha256(json.dumps(
        source["modules"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    fork = {"source_session": str(source_path.resolve()), "source_session_sha256": expected,
            "accepted_modules_sha256": accepted_sha, "source_authority_sha256": original_authority_sha,
            "inspection_scope": inspection["scope"]}
    if target.exists():
        existing = load_json(target)
        actual_sha = hashlib.sha256(json.dumps(
            existing.get("modules"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        if (existing.get("review_fork") != fork or actual_sha != accepted_sha or existing.get("version") != VERSION
                or authority_sha(existing) != original_authority_sha
                or existing.get("session_dir") != str(target.resolve().parent)
                or existing.get("state") not in {"AWAITING_USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED", "FINALIZED"}):
            raise HarnessError("Existing campaign-review snapshot differs from its original accepted source")
        return {"session": str(target.resolve()), "review_fork": fork, "reused": True}
    # Existing images, identities, facts, approval and audit originals stay in
    # their original workspace. Only the new review snapshot is changed.
    source["session_dir"] = str(target.resolve().parent)
    source["review_fork"] = fork
    source["assembly"] = None
    source["long_page_audit"] = None
    source["final"] = None
    source["state"] = (
        "AUTO_FINALIZE_AUTHORIZED"
        if (source.get("approval") or {}).get("delivery_mode") == "AUTO_FINALIZE_AUTHORIZED"
        else "AWAITING_USER_FINAL_REVIEW"
    )
    save(str(target), source, "campaign_review_forked")
    return {"session": str(target.resolve()), "review_fork": fork, "reused": False}


def cmd_inspect_campaign_review_contract(args: argparse.Namespace) -> dict:
    """Requirements and byte inventory only; never a new visual acceptance."""
    session = load_json(args.session)
    if session.get("version") != VERSION:
        raise HarnessError("Campaign review requires its exact pinned native session version")
    require_state(session, "AWAITING_USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED", "FINALIZED")
    modules = session.get("modules") or []
    plan = session.get("plan") or {}
    if len(modules) not in {8, 10, 12} or [m.get("id") for m in modules] != [m.get("id") for m in plan.get("modules", [])]:
        raise HarnessError("Campaign review inventory differs from the approved screen sequence")
    inventory = []
    for order, module in enumerate(modules, 1):
        if type(module.get("order")) is not int or module["order"] != order or module.get("status") not in COMPLETE_STATUSES:
            raise HarnessError("Campaign review requires accepted screens in exact native order")
        image = module.get("accepted_asset")
        if not isinstance(image, str) or not Path(image).is_file():
            raise HarnessError("Campaign review is missing an original accepted screen")
        inventory.append({"module_id": module["id"], "image": image, "image_sha256": file_sha256(image), "status": module["status"]})
    pair_config = (plan.get("output_strategy") or {}).get("continuous_pair_rendering") or {}
    pairs = pair_config.get("pair_ids") or []
    if not pair_config.get("enabled") or len(pairs) != len(modules) // 2 or len({p.get("pair_id") for p in pairs}) != len(pairs):
        raise HarnessError("Campaign review requires exact adjacent pair coverage")
    for index, pair in enumerate(pairs):
        if pair.get("module_ids") != [m["module_id"] for m in inventory[index * 2:index * 2 + 2]]:
            raise HarnessError("Campaign review pair inventory differs from approved adjacent windows")
    schema = load_json(Path(__file__).resolve().parents[1] / "schemas" / "long-page-judgment.schema.json")
    sections = list(schema["required"])
    if pair_config.get("enabled"):
        sections.extend(("pair_audits", "pair_transition_audits"))
        for name, count in (("pair_audits", len(pairs)), ("pair_transition_audits", len(pairs) - 1)):
            schema["properties"][name]["minItems"] = count
            schema["properties"][name]["maxItems"] = count
    if plan.get("expression_contract_version") == "4.3":
        sections.append("expression_diversity_audit")
    if plan.get("plan_format") == "COMPACT_V1_3":
        sections.append("claim_fulfillment")
        claim_ids = (plan.get("selling_point_depth_contract") or {}).get("primary_claim_ids") or []
        module_ids = [m["id"] for m in modules]
        schema["properties"]["claim_fulfillment"] = {
            "type": "array", "minItems": len(claim_ids), "maxItems": len(claim_ids),
            "items": {"type": "object",
                "required": ["claim_id", "overview_visible", "dedicated_visible", "use_or_result_visible", "recall_visible", "observed_module_ids", "visible_evidence"],
                "properties": {
                    "claim_id": {"enum": claim_ids},
                    **{name: {"type": "boolean"} for name in ("overview_visible", "dedicated_visible", "use_or_result_visible", "recall_visible")},
                    "observed_module_ids": {"type": "array", "uniqueItems": True, "minItems": 3, "items": {"enum": module_ids}},
                    "visible_evidence": {"type": "string", "minLength": 20},
                }},
        }
    schema["required"] = list(dict.fromkeys(sections))
    dynamic_checks = list(required_long_page_checks(plan))
    all_checks = list(dict.fromkeys(dynamic_checks + schema["properties"]["checks"]["required"]))
    return {
        "version": VERSION,
        "scope": "REQUIREMENTS_AND_ASSET_INVENTORY_ONLY",
        "visual_quality_accepted": False,
        "original_review_provenance_verified": False,
        "preview_optional": True,
        "required_checks": all_checks,
        "native_dynamic_required_checks": dynamic_checks,
        "judgment_schema": schema,
        "approved_expression_diversity_contract": plan.get("campaign_expression_diversity_contract"),
        "modules": inventory,
        "pairs": pairs,
    }


def cmd_assemble(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_ASSEMBLY", "AWAITING_USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED")
    paths = [module["accepted_asset"] for module in session["modules"]]
    if not all(paths):
        raise HarnessError("All modules must have accepted assets")
    session["assembly"] = assemble_vertical(paths, args.output, session["input"]["platform_profile"])
    # v4.1 assembly is an optional preview and never inserts a mandatory
    # long-page-audit gate into the single-screen delivery path. Preserve the
    # old transition only for legacy non-pair sessions.
    pair_config = (session.get("plan", {}).get("output_strategy", {}).get("continuous_pair_rendering") or {})
    if session.get("state") == "AWAITING_ASSEMBLY" and not pair_config.get("enabled"):
        session["state"] = "AWAITING_LONG_PAGE_AUDIT"
    save(args.session, session, "long_page_assembled")
    return session["assembly"]


def cmd_audit_long_page(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_LONG_PAGE_AUDIT", "AWAITING_USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED")
    judgment = load_json(args.judgment)
    validate_schema(judgment, "long-page-judgment.schema.json", "long-page judgment")
    long_page = (session.get("assembly") or {}).get("long_page")
    if not long_page:
        raise HarnessError("Long-page audit requires an assembled long-page raster")
    validate_artifact_binding(judgment, long_page, "long-page judgment")
    validate_long_page_judgment(judgment, session.get("plan"))
    validate_observed_expression_diversity(judgment, session.get("plan") or {})
    session["long_page_audit"] = judgment
    session["state"] = (
        "AUTO_FINALIZE_AUTHORIZED"
        if (session.get("approval") or {}).get("delivery_mode") == "AUTO_FINALIZE_AUTHORIZED"
        else "AWAITING_USER_FINAL_REVIEW"
    )
    save(args.session, session, f"long_page_audited:{judgment['status']}")
    return summary(session)


def _write_final_manifest(session: dict, args: argparse.Namespace, approval_text: str, approval_mode: str) -> dict:
    approval = {"text": approval_text, "at": now(), "mode": approval_mode}
    manifest = build_final_manifest(session, VERSION, approval)
    atomic_write_json(args.output, manifest)
    session["final"] = {
        "manifest": str(Path(args.output).resolve()),
        "manifest_sha256": file_sha256(args.output),
        "approval": manifest["approval"],
    }
    session["state"] = "FINALIZED"
    save(args.session, session, "finalized")
    return manifest


def cmd_finalize(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AWAITING_USER_FINAL_REVIEW")
    if not args.approval_text.strip():
        raise HarnessError("Final approval text is required")
    return _write_final_manifest(session, args, args.approval_text, "USER_FINAL_REVIEW")


def cmd_auto_finalize(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    require_state(session, "AUTO_FINALIZE_AUTHORIZED")
    approval = session.get("approval") or {}
    if approval.get("delivery_mode") != "AUTO_FINALIZE_AUTHORIZED" or not approval.get("text"):
        raise HarnessError("Auto-finalize requires explicit authorization captured at plan approval")
    return _write_final_manifest(session, args, approval["text"], "AUTO_FINALIZE_AUTHORIZED")


STRUCTURE_FAMILY_LABELS = {
    "FULL_BLEED_HERO": "全幅英雄主视觉",
    "SPLIT_PANEL": "不等宽左右分区",
    "STACKED_BANDS": "上下分层结构",
    "NESTED_INFO_BOARD": "主视觉嵌套信息板",
    "PROCESS_STRIP_PLUS_RESULT": "步骤条＋结果主场景",
    "CENTRAL_OBJECT_ORBIT": "中心商品＋环绕证据",
    "FRAMED_LIFESTYLE_PLUS_CARDS": "大场景＋辅助情境面板",
    "PRODUCT_PAIR_PLUS_TABLE": "产品并置＋规格区",
    "MACRO_DIAGRAM": "局部微距＋关系示意",
    "PORTRAIT_STRIP_PLUS_PAIN_ARRAY": "人群条带＋痛点阵列",
    "ASYMMETRIC_EDITORIAL": "不对称编辑式分区",
    "COMPARISON_SPLIT": "同条件对比双区",
    "SELECTION_MATRIX": "需求到选项矩阵",
    "SPECIFICATION_BOARD": "商品绑定规格板",
}


def _as_compact_text(value, limit: int = 4) -> str:
    if value in (None, "", []):
        return "—"
    if isinstance(value, dict):
        values = [_as_compact_text(item, limit=limit) for item in value.values()]
    elif isinstance(value, (list, tuple, set)):
        values = [_as_compact_text(item, limit=limit) for item in value]
    else:
        return str(value).replace("\n", " ").strip()
    values = [item for item in values if item and item != "—"]
    if len(values) > limit:
        return "；".join(values[:limit]) + f"；等{len(values)}项"
    return "；".join(values) or "—"


def _md_cell(value) -> str:
    return _as_compact_text(value).replace("|", "／")


def _spatial_structure_summary(module: dict) -> str:
    contract = resolve_spatial_contract(module)
    zone_text = "；".join(
        str(zone.get("content") or zone.get("role") or "").strip()
        for zone in contract["zones"]
        if str(zone.get("content") or zone.get("role") or "").strip()
    )
    family = STRUCTURE_FAMILY_LABELS.get(contract["structure_family"], contract["structure_family"])
    return f"{family}：{zone_text}"


def _approval_packet(plan: dict) -> dict:
    strategy = plan["output_strategy"]
    modules = []
    for module in plan["modules"]:
        text_system = module.get("text_system", {})
        expression = module.get("module_visual_expression_contract", {})
        visual_proof = module["visual_proof_direction"]
        presence = expression.get("product_presence_policy")
        subject = expression.get("evidence_subject_policy")
        mode = expression.get("visualization_mode")
        stages = expression.get("transformation_stages") or []
        proof_details = [visual_proof]
        if presence in ("OPTIONAL", "ABSENT_ALLOWED"):
            proof_details.append("商品可缺席")
        if subject in ("CLAIM_OBJECT_DOMINANT", "ACTION_RESULT_DOMINANT"):
            proof_details.append("功效对象/变化为主视觉")
        if mode == "CONCEPTUAL_NON_DIAGNOSTIC":
            proof_details.append("概念性非实验可视化")
        if stages:
            proof_details.append("变化：" + "→".join(str(item) for item in stages))
        presence_decision = module.get("product_presence_decision") or {}
        presence_labels = {
            "REQUIRED_DOMINANT": "完整商品主导",
            "REQUIRED_SUPPORTING": "完整商品辅助",
            "IDENTITY_TOKEN_ONLY": "仅商品身份线索",
            "OPTIONAL": "商品可选",
            "ABSENT_ALLOWED": "允许商品缺席",
            "ABSENT_PREFERRED": "优先商品缺席",
        }
        presence_summary = presence_labels.get(presence, presence or "—")
        if presence in ("IDENTITY_TOKEN_ONLY", "ABSENT_ALLOWED", "ABSENT_PREFERRED"):
            presence_summary += "；主角：" + str(presence_decision.get("primary_subject_without_product") or "卖点证据")
        modules.append({
            "module": module["id"],
            "page_duty": module["duty"],
            "main_conclusion": module["lead_conclusion"],
            "first_visual_subject": (module.get("visual_cast") or {}).get("hero_subject", module.get("visual_proof_contract", {}).get("primary_subject", "—")),
            "presentation_form": module.get("presentation_form", "—"),
            "camera_scale": module.get("camera_scale", "—"),
            "content_units": [item.get("text", "") for item in (module.get("content_unit_contract") or {}).get("units", [])],
            "visual_proof": "；".join(proof_details),
            "商品出镜策略": presence_summary,
            "空间结构": _spatial_structure_summary(module),
            "copy_method": " / ".join(filter(None, [
                str(text_system.get("primary_mode", "")).strip(),
                str(text_system.get("anchor_zone", "")).strip(),
            ])),
            "sequence_relation": _as_compact_text(
                "方法=" + str((module.get("transition_contract") or {}).get("method_id", "")) + "；载体=" + str((module.get("transition_contract") or {}).get("seam_carrier", "")) + "；接缝=" + str((module.get("transition_contract") or {}).get("visual_handoff_type", "")) + "；来源→目标=" + str((module.get("transition_contract") or {}).get("source_region_previous", "")) + "→" + str((module.get("transition_contract") or {}).get("target_region_current", "")) + "；几何=" + str((module.get("transition_contract") or {}).get("handoff_geometry", ""))
                if module.get("transition_contract") else
                (module.get("transition_contract") or {}).get("outgoing_carrier")
                or module["variation_from_previous"]
            ),
        })
    return {
        "required_storyboard_columns": [
            "模块", "页面职责", "主结论", "第一视觉主角", "展示形式/镜头", "信息单元", "画面如何证明", "商品出镜策略", "空间结构", "文字如何进入画面", "与前后屏关系"
        ],
        "decision": {
            "target_platform": strategy["target_platform"],
            "module_count": strategy["module_count"],
            "pair_canvas_aspect_ratio": strategy.get("continuous_pair_rendering", {}).get("pair_canvas_aspect_ratio", "3:8"),
            "pair_merge_type": strategy.get("continuous_pair_rendering", {}).get("pair_merge_type", "PAIR_PLAN_MERGE"),
            "design_dimensions_px": f"{strategy['canvas_width_px']}x{strategy['module_height_px']}",
            "safe_margin_px": strategy["safe_margin_px"],
            "slice_height_px": strategy["slice_height_px"],
            "verification_status": strategy["verification_status"],
            "render_mode": "CONTINUOUS_PAIR" if isinstance(strategy.get("continuous_pair_rendering"), dict) and strategy["continuous_pair_rendering"].get("enabled") else "SINGLE_SCREEN",
            "pair_grouping": [item["module_ids"] for item in strategy.get("continuous_pair_rendering", {}).get("pair_ids", [])] if isinstance(strategy.get("continuous_pair_rendering"), dict) and strategy["continuous_pair_rendering"].get("enabled") else [],
        },
        "module_count_candidates": strategy["module_count_candidates"],
        "pair_canvas_aspect_ratio": strategy.get("continuous_pair_rendering", {}).get("pair_canvas_aspect_ratio", "3:8"),
        "storyboard": modules,
    }


def _approval_markdown(session: dict) -> str:
    if session["plan"].get("planning_mode") == "WEBSITE_DIRECT_V2":
        from runtime.website_plan import approval_markdown

        return approval_markdown(session)
    plan = session["plan"]
    input_data = session["input"]
    strategy = plan["output_strategy"]
    packet = _approval_packet(plan)
    analysis = input_data.get("operating_analysis", {})
    facts_by_id = {item["id"]: item for item in input_data.get("verified_facts", [])}
    active_ids = set(session.get("planning_fact_ids", facts_by_id))
    coverage_by_id = {item["claim_id"]: item for item in strategy.get("claim_coverage", [])}
    confirmed_ids = analysis.get("confirmed_fact_ids") or list(facts_by_id)
    confirmed_facts = [facts_by_id[item]["text"] for item in confirmed_ids
                       if item in facts_by_id and item in active_ids
                       and coverage_by_id.get(item, {}).get("disposition") != "DEFERRED"]
    identity = session.get("identity", {})
    brand = identity.get("brand_logo_source", {})
    if brand.get("mode", "BRANDED") == "BRANDLESS":
        brand_summary = "无品牌；原始确认：" + str(brand.get("brandless_evidence") or "未记录")
    else:
        brand_summary = "品牌原文：" + str(brand.get("observed_logo_text") or "未记录") + "；来源图编号：" + str(brand.get("image_id") or "未记录") + "；可见区域：" + str(brand.get("observed_region") or "未记录")
    identity_lines = [
        "## 锁定的商品身份", "",
        "| 商品与品类 | 来源图编号 | 必须保持的可见特征 | 允许状态 | 禁止变化 | 待核对特征 | 品牌依据 |",
        "|---|---|---|---|---|---|---|",
        "| " + " | ".join(_md_cell(value) for value in (
            [identity.get("canonical_name", "未记录"), identity.get("category", "未记录")],
            identity.get("reference_image_ids", []), identity.get("invariant_features", []),
            identity.get("allowed_states", []), identity.get("forbidden_mutations", []),
            identity.get("uncertain_features", []), brand_summary,
        )) + " |", "",
    ]
    fact_lines = ["## 事实来源与本次处置", "",
                  "| 事实编号 | 原始内容 | 来源及引用 | 本次处置 |",
                  "|---|---|---|---|"]
    source_names = {"USER_FACT": "用户原文", "OBSERVED_PRODUCT_EVIDENCE": "商品图观察", "DOCUMENTED_EVIDENCE": "材料证据"}
    for fact_id, fact in facts_by_id.items():
        coverage = coverage_by_id.get(fact_id, {})
        if fact_id not in active_ids:
            disposition = "已按用户确认排除，不进入本次表达"
        elif coverage.get("disposition") == "DEFERRED":
            disposition = "暂缓表达：" + str(coverage.get("rationale") or coverage.get("defer_reason_code") or "原因未记录")
        elif coverage.get("disposition") in {"LEAD", "SUPPORT"}:
            disposition = "主责表达" if coverage["disposition"] == "LEAD" else "辅助事实"
            disposition += "；对应屏：" + _as_compact_text(coverage.get("module_ids", []))
        else:
            disposition = "待核对：未记录明确处置"
        reference = str(fact.get("evidence_ref") or "未记录")
        for prefix, label in (("settings/selling_points/", "用户卖点第"), ("settings/verified_facts/", "用户补充事实第")):
            if reference.startswith(prefix) and reference[len(prefix):].isdigit():
                reference = label + str(int(reference[len(prefix):]) + 1) + "条"
                break
        source = source_names.get(fact.get("source_class"), "来源未确认") + "：" + reference
        if fact.get("observed_region"):
            source += "；可见区域：" + str(fact["observed_region"])
        fact_lines.append("| " + " | ".join(_md_cell(value) for value in (fact_id, fact["text"], source, disposition)) + " |")
    fact_lines.append("")
    supplied_points = [item.get("text", "") for item in input_data.get("user_supplied_selling_points", [])]
    buyer_pain = [
        f"{item.get('buyer', '')}：{item.get('scenario', '')}"
        for item in analysis.get("buyer_hypotheses", [])
    ] + [item.get("pain", "") for item in analysis.get("pain_hypotheses", [])]
    selected_positioning_id = analysis.get("selected_positioning_id")
    selected_positioning = next((
        item.get("thesis", "") for item in analysis.get("positioning_candidates", [])
        if item.get("id") == selected_positioning_id
    ), plan.get("campaign_thesis", ""))

    lines = [
        f"方案已通过 Skill v{VERSION} 编译，当前停在生图前审批节点。",
        *( ["本次采用精简检查：每屏核对商品、事实、文案、卖点、构图和证据，不做重复审美评分。"] if plan.get("website_contract_version") == "WEBSITE_LEAN_V1" else [] ),
        "",
        *identity_lines,
        *fact_lines,
        "| 采用路由 | 确认的产品事实 | 用户指定卖点 | 推演人群与痛点 | 推荐定位 | 待验证假设 |",
        "|---|---|---|---|---|---|",
        "| " + " | ".join([
            _md_cell(input_data.get("intake_route", "—")),
            _md_cell(confirmed_facts),
            _md_cell(supplied_points),
            _md_cell(buyer_pain),
            _md_cell(selected_positioning),
            _md_cell(analysis.get("evidence_gaps", [])),
        ]) + " |",
        "",
        "| 屏数决策 | 屏数 | 一级卖点深度 | 证据边界 | 结论 |",
        "|---|---:|---|---|---|",
    ]
    mode_labels = {
        "DIRECT_DERIVATION": "直接推导",
        "COMPACT_COVERAGE": "紧凑覆盖",
        "BALANCED_EXPRESSION": "平衡表达",
        "FULL_SALES": "完整销售",
    }
    for candidate in strategy["module_count_candidates"]:
        lines.append("| " + " | ".join([
            mode_labels.get(candidate.get("candidate_mode"), candidate.get("candidate_mode", "—")),
            str(candidate["count"]),
            _md_cell(candidate.get("primary_selling_point_depth_fit") or candidate.get("selling_point_fit")),
            _md_cell(candidate.get("evidence_boundary_fit") or candidate.get("coverage_tradeoff")),
            "选定" if candidate["decision"] == "SELECTED" else "不选",
        ]) + " |")

    lines.extend([
        "",
        "| 目标平台 | 总屏数 | Pair 数量 | Pair 画布比例 | 设计尺寸 | 安全边距 | 固定拆解 | 平台核验状态 | 屏数依据 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
        "| " + " | ".join([
            _md_cell(strategy["target_platform"]), str(strategy["module_count"]),
            str(strategy.get("continuous_pair_rendering", {}).get("pair_count", 0)),
            str(strategy.get("continuous_pair_rendering", {}).get("pair_canvas_aspect_ratio", "3:8")),
            f"{strategy['canvas_width_px']}×{strategy['module_height_px']} px",
            f"{strategy['safe_margin_px']} px", "按组固定位置拆解",
            _md_cell(strategy["verification_status"]), _md_cell(strategy["module_count_rationale"]),
        ]) + " |",
        "",
        "连续渲染容器：" + (
            "启用，两屏一张 3:8 AI 长画布；分界按单屏高度固定切片，屏间不重复主体；长页必须逐条提交方法专属接缝证据。分组：" +
            "、".join("+".join(pair["module_ids"]) for pair in strategy.get("continuous_pair_rendering", {}).get("pair_ids", []))
            if isinstance(strategy.get("continuous_pair_rendering"), dict) and strategy["continuous_pair_rendering"].get("enabled")
            else "未启用，按单屏生成。"
        ),
        "",
        "## 第一层：单屏原子规划",
        "",
        "以下是先完成的单屏规划底稿；最终审批以文档后半部分的‘组—单屏’层级为准。",
        "",
        "| 模块 | 页面职责 | 主结论 | 第一视觉主角 | 展示形式/镜头 | 信息单元 | 画面如何证明 | 商品出镜策略 | 空间结构 | 文字如何进入画面 | 与前后屏关系 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    for item in packet["storyboard"]:
        lines.append("| " + " | ".join([
            _md_cell(item["module"]), _md_cell(item["page_duty"]), _md_cell(item["main_conclusion"]),
            _md_cell(item["first_visual_subject"]), _md_cell(str(item["presentation_form"]) + " / " + str(item["camera_scale"])),
            _md_cell(item["content_units"]),
            _md_cell(item["visual_proof"]), _md_cell(item["商品出镜策略"]), _md_cell(item["空间结构"]),
            _md_cell(item["copy_method"]), _md_cell(item["sequence_relation"]),
        ]) + " |")

    if plan.get("plan_format") == "COMPACT_V1_3":
        lines.extend([
            "",
            "| 一级卖点 | 优先级 | 证据等级 | 总览 | 独立表达 | 使用/结果 | 收尾回忆 | 最终状态 |",
            "|---|---|---|---|---|---|---|---|",
        ])
        outcome_jobs = {"MECHANISM_OR_REASON", "USE_SCOPE", "ACTION_OR_OPERATION", "RESULT_OR_EXPERIENCE", "SELECTION", "TRUST"}
        for depth in plan["selling_point_depth_contract"]["claim_depth"]:
            fact = facts_by_id.get(depth["claim_id"], {})
            job_map = depth.get("module_jobs", [])
            def modules_for(jobs):
                return [entry["module_id"] for entry in job_map if entry.get("job") in jobs]
            overview = modules_for({"VALUE_OVERVIEW", "PROBLEM_CONTEXT"})
            dedicated = modules_for({"DEDICATED_EXPRESSION"})
            outcome = modules_for(outcome_jobs)
            recall_ids = modules_for({"RECALL"})
            complete = bool(overview and dedicated and outcome and recall_ids)
            lines.append("| " + " | ".join([
                _md_cell(fact.get("text", depth["claim_id"])),
                _md_cell(depth.get("priority", "PRIMARY")),
                _md_cell(depth.get("evidence_ceiling")),
                _md_cell(overview), _md_cell(dedicated), _md_cell(outcome), _md_cell(recall_ids),
                "完整" if complete else "规划缺口",
            ]) + " |")

        formula_contract = plan.get("formula_split_contract", {})
        if formula_contract.get("enabled"):
            lines.extend([
                "",
                "| 配方/成分/结构卖点 | 对应痛点或问题 | 对应用户利益 | 独立模块 | 视觉表达路线 | 证据边界 |",
                "|---|---|---|---|---|---|",
            ])
            for entry in formula_contract.get("entries", []):
                lines.append("| " + " | ".join([
                    _md_cell(entry.get("formula_or_element", entry.get("claim_id", ""))),
                    _md_cell(entry.get("buyer_problem_or_question", "")),
                    _md_cell(entry.get("supported_benefit", "")),
                    _md_cell(entry.get("dedicated_module_id", "")),
                    _md_cell(entry.get("visual_expression_route", "")),
                    _md_cell(entry.get("evidence_ceiling", "")),
                ]) + " |")

        if input_data.get("user_supplied_selling_points"):
            supplied_by_id = {item["id"]: item for item in input_data["user_supplied_selling_points"]}
            lines.extend([
                "",
                "| 用户原始卖点 | 处理状态 | 原子事实 | 计划屏幕 | 处理说明 |",
                "|---|---|---|---|---|",
            ])
            for entry in plan.get("user_selling_point_coverage_ledger", []):
                lines.append("| " + " | ".join([
                    _md_cell(supplied_by_id.get(entry["selling_point_id"], {}).get("text", entry["selling_point_id"])),
                    _md_cell(entry["status"]), _md_cell(entry["atomic_claim_ids"]),
                    _md_cell(entry["planned_module_ids"]), _md_cell(entry["reason"]),
                ]) + " |")

    dna = plan["series_visual_dna"]
    lines.extend([
        "",
        "视觉 DNA：" + "；".join([
            "品牌锚点 " + _as_compact_text(dna.get("brand_anchor_system")),
            "色彩 " + _as_compact_text(dna.get("palette_tokens")),
            "字体 " + _as_compact_text(dna.get("typography_tokens")),
            "图形 " + _as_compact_text(dna.get("graphic_tokens")),
            "摄影 " + _as_compact_text(dna.get("photography_tokens")),
            "商品质感 " + _as_compact_text(dna.get("product_tokens")),
        ]) + "。",
        "",
        "合并处理：" + _as_compact_text(strategy.get("merge_decisions", [])),
    ])
    defer_labels = {
        "CONFLICTING_FACT": "事实冲突",
        "HIGH_RISK_UNDOCUMENTED": "高风险且缺少独立证据",
        "UNSUPPORTED_INVENTION": "当前证据不支持",
        "IMMATERIAL_DUPLICATE": "与已保留事实重复",
    }
    deferred = []
    for item in strategy["claim_coverage"]:
        if item.get("disposition") == "DEFERRED":
            fact = facts_by_id.get(item.get("claim_id"), {})
            deferred.append(
                f"{fact.get('text', item.get('claim_id'))}（{defer_labels.get(item.get('defer_reason_code'), item.get('defer_reason_code'))}）"
            )
    lines.extend([
        "延后表达：" + (_as_compact_text(deferred, limit=20) if deferred else "无。"),
        "",
        f"如同意，请回复：**批准 {strategy['module_count']} 屏、{strategy.get('continuous_pair_rendering', {}).get('pair_count', 0)} 组、每组 3:8 画布及以上组—单屏方案，按此生成**。",
    ])
    return "\n".join(lines)


def _pair_grouping_markdown(session: dict) -> str:
    strategy = session["plan"]["output_strategy"]
    pair_config = strategy.get("continuous_pair_rendering") or {}
    lines = [
        f"## 第二层：{session['input']['product_name']}｜组—单屏分级方案",
        "",
        "以下内容是整体审批方案的一部分：先展示每组的整体任务，再展开组内两个单屏的具体展示方式，最后说明两屏之间的视觉衔接。组与组之间的连续性延后到可选的长页预览与审查阶段。",
        "",
        f"- 分组模式：{pair_config.get('grouping_mode', 'TWO_SCREEN_PAIRS')}",
        f"- 合并类型：{pair_config.get('pair_merge_type', 'PAIR_PLAN_MERGE')}",
        f"- 组数：{pair_config.get('pair_count', 0)}",
        f"- 每组画布：{strategy.get('canvas_width_px')}×{pair_config.get('pair_canvas_height_px')} px（3:8）",
        "- 生成后处理：每组 3:8 画布按固定窗口拆解为组内两个单屏交付文件",
        "",
    ]
    modules = {item["id"]: item for item in session["plan"].get("modules", [])}
    for pair in pair_config.get("pair_ids", []):
        upper_id, lower_id = pair["module_ids"]
        upper = modules[upper_id]
        lower = modules[lower_id]
        transition = lower.get("transition_contract") or {}
        lines.extend([
            "",
            f"## {pair['pair_id']}｜{upper_id} + {lower_id}",
            "",
            "组级规划：",
            f"- 组级画布：{strategy.get('canvas_width_px')}×{pair_config.get('pair_canvas_height_px')} px，比例 3:8",
            f"- 组整体目的：{upper.get('duty', '')} → {lower.get('duty', '')}",
            f"- 组级衔接：{transition.get('method_id', pair.get('internal_seam_method_id', ''))}；{transition.get('handoff_geometry', pair.get('internal_seam_geometry', ''))}",
            "",
            f"### {upper_id}｜单屏规划",
            f"- 页面职责：{upper.get('duty', '')}",
            f"- 主结论：{upper.get('lead_conclusion', '')}",
            f"- 第一视觉主角：{(upper.get('visual_cast') or {}).get('hero_subject', upper.get('primary_subject_class', ''))}",
            f"- 展示形式 / 镜头：{upper.get('presentation_form', '')} / {upper.get('camera_scale', '')}",
            f"- 完整商品角色：{upper.get('complete_product_role', '')}",
            f"- 信息单元：{_as_compact_text([item.get('text', '') for item in (upper.get('content_unit_contract') or {}).get('units', [])])}",
            f"- 画面证明：{upper.get('visual_proof_direction', '')}",
            f"- 空间结构：{_spatial_structure_summary(upper)}",
            f"- 禁止退化：{_as_compact_text((upper.get('module_spatial_structure_contract') or {}).get('forbidden_fallbacks', []))}",
            "",
            f"### {lower_id}｜单屏规划",
            f"- 页面职责：{lower.get('duty', '')}",
            f"- 主结论：{lower.get('lead_conclusion', '')}",
            f"- 第一视觉主角：{(lower.get('visual_cast') or {}).get('hero_subject', lower.get('primary_subject_class', ''))}",
            f"- 展示形式 / 镜头：{lower.get('presentation_form', '')} / {lower.get('camera_scale', '')}",
            f"- 完整商品角色：{lower.get('complete_product_role', '')}",
            f"- 信息单元：{_as_compact_text([item.get('text', '') for item in (lower.get('content_unit_contract') or {}).get('units', [])])}",
            f"- 画面证明：{lower.get('visual_proof_direction', '')}",
            f"- 空间结构：{_spatial_structure_summary(lower)}",
            f"- 禁止退化：{_as_compact_text((lower.get('module_spatial_structure_contract') or {}).get('forbidden_fallbacks', []))}",
            "",
            "### 组内两屏衔接",
            f"- 方法：{transition.get('method_id', pair.get('internal_seam_method_id', ''))}",
            f"- 上屏来源区域：{transition.get('source_region_previous', pair.get('internal_seam_source_region', ''))}",
            f"- 下屏目标区域：{transition.get('target_region_current', pair.get('internal_seam_target_region', ''))}",
            f"- 接缝载体：{transition.get('seam_carrier', pair.get('internal_seam_carrier', ''))}",
            f"- 接缝几何：{transition.get('handoff_geometry', pair.get('internal_seam_geometry', ''))}",
            f"- 允许变化：{transition.get('allowed_change', '保持动线、光线、尺度或材质连续，并允许两屏承担不同职责')}",
        ])
    lines.extend(["", "每组审核时按‘组级关系 → 上屏 → 下屏 → 内部接缝’展开，但最终只产生一份组级审核结果。"])
    return "\n".join(lines)


def summary(session: dict) -> dict:
    modules = session.get("modules", [])
    state = session.get("state")
    current = None
    if modules and 0 <= session.get("current_index", 0) < len(modules):
        current = modules[session["current_index"]]["id"]
    result = {
        "state": state,
        "allowed_actions": allowed_actions(state),
        "current_module": current,
        "completed_modules": sum(module.get("status") in COMPLETE_STATUSES for module in modules),
        "total_modules": len(modules),
        "assembly": session.get("assembly"),
        "stage_timings": _seconds_between_events(session),
    }
    if session.get("planning_metrics"):
        result["planning_metrics"] = session["planning_metrics"]
    if current:
        module = modules[session["current_index"]]
        result["current_module_runtime"] = {
            "status": module.get("status"),
            "cycle_id": module.get("cycle_id", 1),
            "cycle_attempt_count": module.get("cycle_attempt_count", 0),
            "automatic_repair_remaining": max(
                0,
                module.get("repair_budget", 1) - max(0, module.get("cycle_attempt_count", 0) - 1),
            ),
            "lifetime_attempt_count": len(module.get("attempts", [])),
            "residual_issues": module.get("residual_issues", []),
        }
        pair = _pair_for_index(session, session.get("current_index", 0))
        if pair:
            result["current_render_container"] = {
                "mode": "CONTINUOUS_PAIR",
                "pair_id": pair["pair_id"],
                "module_ids": pair["module_ids"],
                "module_height_px": session["plan"]["output_strategy"]["module_height_px"] if session.get("plan") else None,
            }
    if session.get("conflict_checkpoint"):
        result["conflict_checkpoint"] = session["conflict_checkpoint"]
        result["planning_fact_ids"] = session.get("planning_fact_ids", [])
    if session.get("plan"):
        strategy = session["plan"]["output_strategy"]
        result["approved_output_strategy"] = {
            "target_platform": strategy["target_platform"],
            "module_count": strategy["module_count"],
            "pair_canvas_aspect_ratio": (strategy.get("continuous_pair_rendering") or {}).get("pair_canvas_aspect_ratio"),
            "pair_merge_type": (strategy.get("continuous_pair_rendering") or {}).get("pair_merge_type"),
            "dimensions_px": f"{strategy['canvas_width_px']}x{strategy['module_height_px']}",
            "verification_status": strategy["verification_status"],
        }
        if isinstance(strategy.get("continuous_pair_rendering"), dict) and strategy["continuous_pair_rendering"].get("enabled"):
            result["approved_output_strategy"]["render_mode"] = "CONTINUOUS_PAIR"
            result["approved_output_strategy"]["pair_count"] = strategy["continuous_pair_rendering"].get("pair_count")
            result["approved_output_strategy"]["pair_canvas_dimensions_px"] = (
                f"{strategy['canvas_width_px']}x{strategy['continuous_pair_rendering'].get('pair_canvas_height_px')}"
            )
        if state == "AWAITING_PLAN_APPROVAL":
            result["approval_packet"] = _approval_packet(session["plan"])
            result["approval_document"] = session.get("approval_document")
            document = session.get("approval_document") or {}
            try:
                raw_document = Path(document["path"]).read_bytes()
                markdown = raw_document.decode("utf-8")
            except (KeyError, OSError, TypeError, UnicodeError) as exc:
                raise HarnessError("The compiled approval document is missing or unreadable") from exc
            if hashlib.sha256(raw_document).hexdigest() != document.get("sha256"):
                raise HarnessError("The compiled approval document bytes changed")
            result["approval_markdown"] = markdown
            result["approval_output_instruction"] = (
                "Show approval_markdown verbatim as the complete user-facing response; "
                "do not abbreviate, paraphrase, replace it with a page-order list, or add a second plan."
            )
    return result


def cmd_inspect_pair_review_contracts(args: argparse.Namespace) -> dict:
    session = load_json(args.session)
    pair = next((pair for pair in session["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_ids"] if pair["pair_id"] == args.pair_id), None)
    if pair is None:
        raise HarnessError("Unknown pair review contract")
    by_id = {module["id"]: module for module in session["modules"]}
    result = {"module_contracts": review_contracts([by_id[key] for key in pair["module_ids"]], len(session["modules"]))}
    revision_contract = revision_review_contract(session, args.pair_id)
    if revision_contract is not None:
        result["local_revision_contract"] = revision_contract
    return result


def cmd_inspect_pair_review_evidence(args: argparse.Namespace) -> dict:
    return inspect_pair_audit(load_json(args.session), args.pair_id)


def cmd_inspect_pair_artifacts(args: argparse.Namespace) -> dict:
    return pair_artifacts(load_json(args.session), args.pair_id, args.selection)


def cmd_inspect_final_manifest(args: argparse.Namespace) -> dict:
    return inspect_final_manifest(load_json(args.session), args.manifest, VERSION)


def cmd_inspect(args: argparse.Namespace) -> dict:
    return summary(load_json(args.session))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Aurora domestic e-commerce detail-page state harness")
    sub = root.add_subparsers(dest="command", required=True)
    commands = {}
    for name in ("start", "resolve-conflicts", "lock-identity", "preflight-plan", "compile-plan", "authorize", "generate-next", "record-generation", "record-generation-batch", "audit-current", "audit-pair", "prepare-repair", "apply-user-adjustment", "assemble", "audit-long-page", "finalize", "auto-finalize", "inspect", "inspect-pair-artifacts", "inspect-pair-review-contracts", "inspect-pair-review-evidence", "inspect-final-manifest", "inspect-campaign-review-contract", "fork-campaign-review", "fork-local-revision"):
        commands[name] = sub.add_parser(name)
    commands["start"].add_argument("--input", required=True); commands["start"].add_argument("--session", required=True)
    for name in commands:
        if name != "start": commands[name].add_argument("--session", required=True)
    commands["lock-identity"].add_argument("--identity", required=True)
    commands["resolve-conflicts"].add_argument("--resolution", required=True)
    commands["preflight-plan"].add_argument("--plan", required=True)
    commands["compile-plan"].add_argument("--plan", required=True)
    commands["authorize"].add_argument("--approval-text", required=True)
    commands["authorize"].add_argument("--approved-module-count", required=True, type=int)
    commands["authorize"].add_argument("--approved-pair-canvas-aspect-ratio", required=False)
    commands["authorize"].add_argument("--approved-aspect-ratio", required=False)
    commands["authorize"].add_argument("--approval-document-sha256", required=True)
    commands["authorize"].add_argument("--delivery-mode", choices=("USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED"), default="USER_FINAL_REVIEW")
    commands["generate-next"].add_argument("--out", required=True)
    commands["record-generation"].add_argument("--token", required=True); commands["record-generation"].add_argument("--image", required=True)
    commands["record-generation-batch"].add_argument("--manifest", required=True)
    commands["audit-current"].add_argument("--judgment", required=True)
    commands["audit-pair"].add_argument("--judgment", required=True)
    commands["inspect-pair-artifacts"].add_argument("--pair-id", required=True)
    commands["inspect-pair-review-contracts"].add_argument("--pair-id", required=True)
    commands["inspect-pair-review-evidence"].add_argument("--pair-id", required=True)
    commands["inspect-final-manifest"].add_argument("--manifest", required=True)
    commands["fork-local-revision"].add_argument("--source-session", required=True)
    commands["fork-local-revision"].add_argument("--source-session-sha256", required=True)
    commands["fork-local-revision"].add_argument("--revision-intent", required=True)
    commands["fork-campaign-review"].add_argument("--source-session", required=True)
    commands["fork-campaign-review"].add_argument("--source-session-sha256", required=True)
    commands["inspect-pair-artifacts"].add_argument("--selection", choices=("LATEST_RECORDED", "ACCEPTED"), default="LATEST_RECORDED")
    commands["prepare-repair"].add_argument("--repair-plan", required=True)
    commands["apply-user-adjustment"].add_argument("--adjustment", required=True)
    commands["assemble"].add_argument("--output", required=True)
    commands["audit-long-page"].add_argument("--judgment", required=True)
    commands["finalize"].add_argument("--output", required=True); commands["finalize"].add_argument("--approval-text", required=True)
    commands["auto-finalize"].add_argument("--output", required=True)
    return root


HANDLERS = {
    "start": cmd_start, "resolve-conflicts": cmd_resolve_conflicts,
    "lock-identity": cmd_lock_identity, "preflight-plan": cmd_preflight_plan, "compile-plan": cmd_compile_plan,
    "authorize": cmd_authorize, "generate-next": cmd_generate_next, "record-generation": cmd_record_generation,
    "record-generation-batch": cmd_record_generation_batch,
    "audit-current": cmd_audit_current, "prepare-repair": cmd_prepare_repair,
    "audit-pair": cmd_audit_pair, "inspect-pair-artifacts": cmd_inspect_pair_artifacts, "inspect-pair-review-contracts": cmd_inspect_pair_review_contracts, "inspect-pair-review-evidence": cmd_inspect_pair_review_evidence, "apply-user-adjustment": cmd_apply_user_adjustment, "assemble": cmd_assemble,
    "inspect-final-manifest": cmd_inspect_final_manifest,
    "inspect-campaign-review-contract": cmd_inspect_campaign_review_contract,
    "fork-campaign-review": cmd_fork_campaign_review,
    "fork-local-revision": cmd_fork_local_revision,
    "audit-long-page": cmd_audit_long_page, "finalize": cmd_finalize, "auto-finalize": cmd_auto_finalize, "inspect": cmd_inspect,
}


def main() -> int:
    try:
        args = parser().parse_args()
        print(json.dumps(HANDLERS[args.command](args), ensure_ascii=False, indent=2))
        return 0
    except (HarnessError, KeyError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__, "failure_kind": "LOCAL_IO" if isinstance(exc, OSError) else "CONTRACT_REJECTION"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


