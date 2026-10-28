#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


_configure_utf8_stdio()

VENDOR = Path(__file__).resolve().parent.parent / "vendor"
if VENDOR.is_dir():
    # The supplied fallback vendor contains a CPython 3.12 Windows extension.
    # Prefer the application's hash-locked environment (Linux/Python 3.13 in
    # production), while preserving the fallback for the original standalone
    # Windows package when jsonschema is not installed globally.
    sys.path.append(str(VENDOR))

RUNTIME = Path(__file__).resolve().parent / "runtime"
sys.path.insert(0, str(RUNTIME))

from core import (  # noqa: E402
    PAGE_ROLES,
    STYLE_FIELDS,
    VERSION,
    HarnessError,
    allowed_actions,
    background_style_lock,
    compile_prompt,
    compile_session,
    compiled_contract_hash,
    digest_file,
    digest_value,
    image_dimensions,
    load_json,
    is_supported_image,
    next_page_index,
    record,
    require,
    require_state,
    transition,
    utc_now,
    validate_identity,
    validate_campaign_style,
    validate_plan,
    validate_raw,
    write_json,
)
from visual_gate import evaluate  # noqa: E402
from quality_engine import concise_execution_prompt, enhance_v013_plan, validate_quality_plan  # noqa: E402
from v013_bridge import (  # noqa: E402
    V013BridgeError,
    adapt_v014_identity,
    adapt_v014_raw,
    build_v014_plan,
    compile_v013,
)


def save(session_path: str | Path, session: dict[str, Any]) -> None:
    session["allowed_actions"] = allowed_actions(session["state"])
    write_json(session_path, session)


def result(session: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "status": session["state"],
        "request_id": session["request_id"],
        "allowed_actions": allowed_actions(session["state"]),
        **extra,
    }


def rebase_authorization(session: dict[str, Any], reason: str) -> None:
    authorization = session.get("authorization")
    require(isinstance(authorization, dict), "initial user authorization is missing")
    session.setdefault("authorization_history", []).append(copy.deepcopy(authorization))
    authorization["previous_compiled_hash"] = authorization.get("compiled_hash")
    authorization["compiled_hash"] = session["compiled_hash"]
    authorization["rebased_at"] = utc_now()
    authorization["rebase_reason"] = reason


def cmd_start(args: argparse.Namespace) -> dict[str, Any]:
    target = Path(args.session)
    require(not target.exists(), f"session already exists: {target}")
    raw = load_json(args.input)
    validate_raw(raw)
    now = utc_now()
    session = {
        "schema_version": VERSION,
        "request_id": raw["request_id"],
        "state": "AWAITING_IDENTITY",
        "created_at": now,
        "updated_at": now,
        "profile01_semantics_read": False,
        "raw_input": raw,
        "raw_input_sha256": digest_value(raw),
        "identity_contract": None,
        "plan": None,
        "compiled_hash": None,
        "campaign_style_id": None,
        "authorization": None,
        "pending_generation": None,
        "current_page_index": None,
        "accepted_page_indexes": [],
        "style_calibrated": False,
        "search_baseline_calibrated": False,
        "max_automatic_repairs_per_page": 1,
        "automatic_repairs_used_by_page": {str(index): 0 for index in range(6)},
        "user_adjustment_history": [],
        "generated_versions": [],
        "excluded_generated_assets": [],
        "history": [],
    }
    record(session, "start", "NEW")
    save(target, session)
    return result(session)


def cmd_lock_identity(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_IDENTITY")
    identity = load_json(args.identity)
    validate_identity(identity, session)
    session["identity_contract"] = copy.deepcopy(identity)
    session["identity_contract_sha256"] = digest_value(identity)
    transition(session, "IDENTITY_LOCKED", "lock-identity")
    save(args.session, session)
    return result(session, identity_contract_sha256=session["identity_contract_sha256"])


def cmd_compile(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "IDENTITY_LOCKED")
    plan = load_json(args.plan)
    validate_plan(plan, session)
    try:
        validate_quality_plan(plan)
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc
    compile_session(session, plan)
    transition(session, "AWAITING_AUTHORIZATION", "compile")
    save(args.session, session)
    return result(
        session,
        compiled_hash=session["compiled_hash"],
        commercial_contract_hash=session["commercial_contract_hash"],
        pages=len(session["plan"]["pages"]),
        generation_performed=False,
    )


def cmd_compile_v013(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "IDENTITY_LOCKED")
    require(bool(args.input) == bool(args.identity), "provide both --input and --identity, or omit both to adapt the locked v0.1.15 session")
    raw = load_json(args.input) if args.input else adapt_v014_raw(session["raw_input"])
    identity = load_json(args.identity) if args.identity else adapt_v014_identity(raw, session["identity_contract"])
    require(raw.get("request_id") == session["request_id"], "v0.1.13 raw request_id mismatch")
    require(identity.get("contract_id", "").split(":", 1)[0] == session["request_id"], "v0.1.13 identity request_id mismatch")
    try:
        compiled = compile_v013(raw, identity, args.source_manifest)
        plan = build_v014_plan(compiled, identity, session["identity_contract"])
        plan = enhance_v013_plan(plan, session["raw_input"], session["identity_contract"])
        validate_quality_plan(plan)
    except (V013BridgeError, ValueError) as exc:
        raise HarnessError(str(exc)) from exc
    compile_session(session, plan)
    session["legacy_v013_compiled_output"] = copy.deepcopy(compiled)
    session["legacy_v013_compiled_sha256"] = digest_value(compiled)
    session["compiler_lineage"] = plan["compiler_lineage"]
    transition(session, "AWAITING_AUTHORIZATION", "compile-v013")
    save(args.session, session)
    if args.output:
        write_json(args.output, compiled)
    return result(
        session,
        compiler_lineage=session["compiler_lineage"],
        legacy_v013_compiled_sha256=session["legacy_v013_compiled_sha256"],
        compiled_hash=session["compiled_hash"],
        commercial_contract_hash=session["commercial_contract_hash"],
        pages=6,
        generation_performed=False,
    )


def cmd_replan(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_AUTHORIZATION", "AUTHORIZED", "REPAIR_REQUIRED")
    require(not session.get("accepted_page_indexes"), "full-set replan is allowed only before any page is accepted")
    plan = load_json(args.plan)
    validate_plan(plan, session)
    try:
        validate_quality_plan(plan)
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc
    previous = {
        "compiled_hash": session.get("compiled_hash"),
        "campaign_style_id": session.get("campaign_style_id"),
        "superseded_at": utc_now(),
        "reason": args.reason.strip(),
    }
    require(previous["reason"], "replan reason is required")
    session.setdefault("superseded_compilations", []).append(previous)
    compile_session(session, plan)
    session["current_page_index"] = None
    transition(session, "AWAITING_AUTHORIZATION", "replan")
    save(args.session, session)
    return result(
        session,
        compiled_hash=session["compiled_hash"],
        superseded_compiled_hash=previous["compiled_hash"],
        authorization_invalidated=True,
        generation_performed=False,
    )


def cmd_authorize(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_AUTHORIZATION")
    text = args.approval_text.strip()
    require(len(text) >= 2, "approval_text must preserve the user's explicit confirmation")
    session["authorization"] = {
        "approved_at": utc_now(),
        "approval_text": text,
        "approval_text_sha256": digest_value(text),
        "compiled_hash": session["compiled_hash"],
        "scope": "ONE_PLAN_AUTHORIZATION_FOR_UNINTERRUPTED_SIX_PAGE_GENERATION_AUDIT_ONE_REPAIR_PER_PAGE_AND_FINAL_USER_REVIEW",
        "execution_scope": "NO_INTERMEDIATE_USER_CHECKPOINTS_UNTIL_COMPLETE_SIX_IMAGE_SET",
    }
    transition(session, "AUTHORIZED", "authorize")
    save(args.session, session)
    return result(session, authorization_scope=session["compiled_hash"])


def cmd_generate_next(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AUTHORIZED")
    authorization = session.get("authorization") or {}
    require(authorization.get("compiled_hash") == session.get("compiled_hash"), "authorization is stale")
    page_index = next_page_index(session)
    require(page_index is not None, "no remaining page")
    if page_index >= 2:
        require(session.get("style_calibrated") is True, "style calibration is required after the first two pages")
    page = session["plan"]["pages"][page_index]
    generation_origin = page.get("generation_origin", "INITIAL")
    evidence_by_id = {x["evidence_id"]: x for x in session["identity_contract"]["evidence"]}
    evidence = [
        {
            "evidence_id": evidence_id,
            "path": evidence_by_id[evidence_id]["path"],
            "role": "PRODUCT_IDENTITY_AUTHORITY",
        }
        for evidence_id in page["evidence_ids"]
    ]
    repair_canvas = None
    if generation_origin == "AUTOMATIC_REPAIR":
        blocked_versions = [
            item for item in session.get("generated_versions", [])
            if item["page_index"] == page_index and item.get("status") == "BLOCKED"
        ]
        require(bool(blocked_versions), "automatic repair requires the failed first draft as repair canvas")
        failed = blocked_versions[-1]
        repair_canvas = {
            "path": failed["file"],
            "sha256": failed["sha256"],
            "role": "REPAIR_CANVAS_ONLY",
            "identity_authority": False,
            "instruction": "Edit this composed first draft in place. Preserve its clean rebuilt scene and passed style/layout layers; fix only the complete repair plan. Never reconstruct from the original source-photo background.",
        }
    token_payload = {
        "request_id": session["request_id"],
        "compiled_hash": session["compiled_hash"],
        "page_index": page_index,
        "revision": page.get("revision", 0),
        "nonce": __import__("secrets").token_hex(16),
    }
    token = digest_value(token_payload)
    generation_request = {
        "schema_version": "controlled-generation-request-0.1.17",
        "request_id": session["request_id"],
        "page_index": page_index,
        "page_role": page["page_role"],
        "revision": page.get("revision", 0),
        "generation_token": token,
        "compiled_hash": session["compiled_hash"],
        "commercial_contract_hash": session["commercial_contract_hash"],
        "campaign_constitution": copy.deepcopy(session["campaign_constitution"]),
        "campaign_constitution_sha256": session["campaign_constitution_sha256"],
        "product_identity_lock": copy.deepcopy(session["campaign_constitution"]["product_identity_lock"]),
        "background_style_lock": copy.deepcopy(session["campaign_constitution"]["background_style_lock"]),
        "commercial_page_task": copy.deepcopy(page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index]),
        "selling_point_assignment": {
            "primary_user_source_text": (page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index])["primary_selling_point_source_text"],
            "supporting_user_source_texts": copy.deepcopy((page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index])["supporting_selling_points"]),
            "bundle_id": (page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index])["selling_point_bundle_id"],
            "bundle_user_source_texts": copy.deepcopy((page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index])["selling_point_bundle_source_texts"]),
            "claim_visual_evidence_map": copy.deepcopy((page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index])["claim_visual_evidence_map"]),
            "non_repetition_reason": (page["commercial_page_task"] if "commercial_page_task" in page else session["plan"]["commercial_orchestration"]["page_task_table"][page_index])["non_repetition_reason"],
            "hard_rule": "One page may carry multiple coordinated selling points. Preserve useful verified information modules, maintain a clear lead hierarchy, visually prove each renderable claim, and avoid repeating the same selling-point bundle across adjacent pages. Never fail or delete content merely because the page has multiple claims.",
        },
        "approved_page_execution_contract": copy.deepcopy(page["approved_page_execution_contract"]),
        "approved_page_contract_sha256": digest_value(page["approved_page_execution_contract"]),
        "selling_point_visual_proof_contract": copy.deepcopy(page["approved_page_execution_contract"]["selling_point_visual_proof_contract"]),
        "source_scene_isolation_contract": copy.deepcopy(page["approved_page_execution_contract"]["source_scene_isolation_contract"]),
        "active_user_adjustment_delta": copy.deepcopy(page.get("active_user_adjustment")),
        "search_cover_blueprint": copy.deepcopy(page["approved_page_execution_contract"].get("search_cover_blueprint")),
        "generation_adapter_instruction": "Use prompt as the sole concise image-model brief; do not append raw JSON, hashes, the complete campaign_constitution, or other page contracts. Keep the structured fields as execution/audit memory. Preserve product identity, factual meaning, current page duty, campaign visual world, and source-background isolation; allow expert freedom over camera, composition, scale, lighting, copy phrasing, supporting-point selection, and credible visual dramatization. On AUTOMATIC_REPAIR, edit repair_canvas in place; original references are identity checks only and their full backgrounds must not enter the output. Never pause for user approval after plan authorization; complete the six-page run with at most one automatic repair per page.",
        "prompt": page["generation_prompt"],
        "reference_allowlist": evidence,
        "generation_contract": copy.deepcopy(page["generation_contract"]),
        "edit_target": repair_canvas or next((x for x in evidence if x["evidence_id"] == page["generation_contract"].get("edit_target_evidence_id")), None),
        "repair_canvas": repair_canvas,
        "supporting_references": [x for x in evidence if x["evidence_id"] in page["generation_contract"].get("supporting_evidence_ids", [])],
        "previous_generated_images_used_for_product_identity": False,
        "must_record_asset_before_audit": True,
        "generation_origin": generation_origin,
        "source_background_firewall": {
            "initial_generation": "extract product identity, discard the entire source scene, rebuild the approved campaign scene",
            "automatic_repair": "edit the failed first draft canvas; do not use a full original photo as the canvas",
            "forbidden": ["source desk", "source wall", "source seam", "source clutter", "source keyboard", "source capture shadow or edge"],
        },
        "output_surface_contract": {
            "aspect_ratio": "1:1",
            "minimum_width": 1024,
            "minimum_height": 1024,
            "surface": "TAOBAO_SEARCH_AND_CAROUSEL_SQUARE",
        },
    }
    session["pending_generation"] = {
        "token_sha256": digest_value(token),
        "page_index": page_index,
        "revision": page.get("revision", 0),
        "request_sha256": digest_value(generation_request),
        "issued_at": utc_now(),
    }
    session["current_page_index"] = page_index
    transition(session, "AWAITING_GENERATED_ASSET", "generate-next")
    save(args.session, session)
    if args.out:
        write_json(args.out, generation_request)
    return result(session, generation_request=generation_request)


def cmd_record_generation(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_GENERATED_ASSET")
    pending = session.get("pending_generation") or {}
    require(digest_value(args.token) == pending.get("token_sha256"), "generation token is stale or invalid")
    image = Path(args.image).resolve()
    require(image.is_file(), f"generated image does not exist: {image}")
    require(is_supported_image(image), "generated asset must be PNG, JPEG, or WebP")
    width, height = image_dimensions(image)
    require(width == height, f"generated asset must be square 1:1, got {width}x{height}")
    require(width >= 1024, f"generated asset must be at least 1024x1024, got {width}x{height}")
    page_index = pending["page_index"]
    image_hash = digest_file(image)
    version = {
        "page_index": page_index,
        "page_role": PAGE_ROLES[page_index],
        "revision": pending["revision"],
        "file": str(image),
        "sha256": image_hash,
        "width": width,
        "height": height,
        "status": "AWAITING_VISUAL_AUDIT",
        "recorded_at": utc_now(),
    }
    session["generated_versions"].append(version)
    session["pending_generation"] = None
    transition(session, "AWAITING_VISUAL_AUDIT", "record-generation")
    save(args.session, session)
    return result(session, page_index=page_index, image_sha256=image_hash, width=width, height=height)


def current_version(session: dict[str, Any]) -> dict[str, Any]:
    page_index = session.get("current_page_index")
    matches = [x for x in session.get("generated_versions", []) if x["page_index"] == page_index]
    require(bool(matches), "no generated version exists for current page")
    return matches[-1]


def advance_after_page_selection(session: dict[str, Any], page_index: int, action: str) -> None:
    if session["accepted_page_indexes"] == list(range(6)):
        transition(session, "AWAITING_SET_ROLE_AUDIT", action)
    elif page_index == 0 and not session.get("search_baseline_calibrated"):
        transition(session, "AWAITING_SEARCH_BASELINE_CALIBRATION", action)
    elif page_index == 1 and not session.get("style_calibrated"):
        transition(session, "AWAITING_AGENT_STYLE_CALIBRATION", action)
    else:
        transition(session, "AUTHORIZED", action)


def cmd_audit_current(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_VISUAL_AUDIT")
    version = current_version(session)
    judgment = load_json(args.judgment)
    audit = evaluate(
        judgment,
        version["sha256"],
        version["page_index"],
        version["file"],
        session["plan"]["pages"][version["page_index"]]["approved_page_execution_contract"],
    )
    version["visual_audit"] = audit
    if audit["status"] == "PASS":
        version["status"] = "ACCEPTED"
        session["accepted_page_indexes"] = sorted(
            set(session.get("accepted_page_indexes", [])) | {version["page_index"]}
        )
        advance_after_page_selection(session, version["page_index"], "audit-current-pass")
    else:
        version["status"] = "BLOCKED"
        session["excluded_generated_assets"].append(
            {
                "page_index": version["page_index"],
                "file": version["file"],
                "sha256": version["sha256"],
                "errors": audit["errors"],
                "repair_scope": audit["repair_scope"],
            }
        )
        session["required_repair_scope"] = audit["repair_scope"]
        origin = session["plan"]["pages"][version["page_index"]].get("generation_origin", "INITIAL")
        if origin == "INITIAL" and session["automatic_repairs_used_by_page"].get(str(version["page_index"]), 0) < 1:
            transition(session, "REPAIR_REQUIRED", "audit-current-block-first-attempt")
        else:
            version["status"] = "ACCEPTED_WITH_RESIDUALS"
            session["accepted_page_indexes"] = sorted(set(session.get("accepted_page_indexes", [])) | {version["page_index"]})
            session.setdefault("residual_issues_for_user_final_review", []).append({
                "page_index": version["page_index"],
                "revision": version["revision"],
                "image_sha256": version["sha256"],
                "errors": audit["errors"],
                "failed_checks": audit["failed_checks"],
                "decision_summary": audit["decision_summary"],
            })
            advance_after_page_selection(session, version["page_index"], "audit-current-second-attempt-selected-for-final-user-review")
    save(args.session, session)
    return result(session, visual_audit=audit)


def cmd_calibrate_search_baseline(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_SEARCH_BASELINE_CALIBRATION")
    observation = load_json(args.observation)
    require(observation.get("agent_pixel_reviewed") is True, "search baseline calibration requires direct Agent pixel review")
    require(observation.get("review_mode") == "DIRECT_PIXEL_VALIDATED_BASELINE_COMPARISON", "search baseline review_mode is invalid")
    axes = observation.get("axes")
    required_axes = {
        "product_dominance",
        "headline_performance",
        "physical_evidence",
        "spatial_depth",
        "commercial_closure",
        "thumbnail_competitiveness",
        "commercial_frame_structure",
        "type_hierarchy",
    }
    require(isinstance(axes, dict) and set(axes) == required_axes, "search baseline axes are incomplete")
    scores = []
    for name, item in axes.items():
        require(isinstance(item, dict), f"baseline axis {name} must be an object")
        require(isinstance(item.get("score"), (int, float)) and 0 <= item["score"] <= 100, f"baseline axis {name} score must be 0..100")
        require(isinstance(item.get("observed_evidence"), str) and len(item["observed_evidence"].strip()) >= 12, f"baseline axis {name} needs observed pixel evidence")
        scores.append(float(item["score"]))
    average = round(sum(scores) / len(scores), 2)
    # The baseline protects recognizability, not aesthetic perfection. Keep a
    # commercially strong cover and reserve repair for a material baseline gap.
    passed = average >= 80 and min(scores) >= 65
    version = current_version(session)
    version["search_baseline_comparison"] = {
        "status": "PASS" if passed else "BLOCKED",
        "average_score": average,
        "axes": copy.deepcopy(axes),
        "baseline": "USER_VALIDATED_TEN_SET_TRANSFERABLE_COMMERCIAL_BASELINE",
        "reviewed_at": utc_now(),
    }
    if passed:
        session["search_baseline_calibrated"] = True
        if session.get("accepted_page_indexes") == list(range(6)):
            transition(session, "AWAITING_SET_ROLE_AUDIT", "calibrate-search-baseline-pass")
        else:
            transition(session, "AUTHORIZED", "calibrate-search-baseline-pass")
    else:
        audit = version.get("visual_audit", {})
        audit["status"] = "BLOCKED"
        audit["failed_checks"] = ["baseline_performance_match"]
        audit["errors"] = ["VALIDATED_BASELINE_GAP"]
        audit["repair_scope"] = "LAYOUT_OR_COMPOSITION_REPAIR_REQUIRED"
        if session["automatic_repairs_used_by_page"].get("0", 0) < 1 and session["plan"]["pages"][0].get("generation_origin", "INITIAL") == "INITIAL":
            version["status"] = "BLOCKED"
            session["accepted_page_indexes"] = [index for index in session.get("accepted_page_indexes", []) if index != 0]
            session["required_repair_scope"] = "LAYOUT_OR_COMPOSITION_REPAIR_REQUIRED"
            session["excluded_generated_assets"].append({
                "page_index": 0,
                "file": version["file"],
                "sha256": version["sha256"],
                "errors": ["VALIDATED_BASELINE_GAP"],
                "repair_scope": "LAYOUT_OR_COMPOSITION_REPAIR_REQUIRED",
            })
            transition(session, "REPAIR_REQUIRED", "calibrate-search-baseline-block")
        else:
            version["status"] = "ACCEPTED_WITH_RESIDUALS"
            session["search_baseline_calibrated"] = True
            session.setdefault("residual_issues_for_user_final_review", []).append({
                "page_index": 0,
                "revision": version["revision"],
                "image_sha256": version["sha256"],
                "errors": ["VALIDATED_BASELINE_GAP"],
                "failed_checks": ["baseline_performance_match"],
                "decision_summary": "唯一自动返修后的搜索基线仍有差距；保留该稿继续完成六图，最终交由用户整套验收。",
            })
            transition(session, "AUTHORIZED", "calibrate-search-baseline-second-attempt-recorded-for-final-review")
    save(args.session, session)
    return result(session, baseline_score=average, baseline_status="PASS" if passed else "BLOCKED")


def cmd_audit_set_role_distinction(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_SET_ROLE_AUDIT")
    observation = load_json(args.observation)
    require(observation.get("agent_pixel_reviewed") is True, "set-role audit requires direct Agent pixel review")
    require(observation.get("review_mode") == "BLIND_SIX_PAGE_ROLE_COMPARISON", "set-role review_mode is invalid")
    require(observation.get("reviewed_page_indexes") == list(range(6)), "set-role audit must compare all six pages")
    expected_hashes = {}
    for index in range(6):
        versions = [item for item in session["generated_versions"] if item["page_index"] == index and item["status"] in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}]
        require(bool(versions), f"accepted page {index} is missing")
        expected_hashes[str(index)] = versions[-1]["sha256"]
    require(observation.get("image_sha256_by_page") == expected_hashes, "set-role audit image hashes are stale or incomplete")
    distinctions = observation.get("carousel_distinctions")
    distinction_indexes = (
        sorted(
            item.get("page_index")
            for item in distinctions
            if isinstance(item, dict) and isinstance(item.get("page_index"), int)
        )
        if isinstance(distinctions, list)
        else []
    )
    distinctions_complete = (
        isinstance(distinctions, list)
        and len(distinctions) == 5
        and distinction_indexes == list(range(1, 6))
        and all(
            isinstance(item, dict)
            and isinstance(item.get("why_not_search_cover"), str)
            and len(item["why_not_search_cover"].strip()) >= 12
            for item in distinctions
        )
    )
    evidence = observation.get("search_identification_evidence")
    evidence_complete = isinstance(evidence, str) and len(evidence.strip()) >= 20
    passed = (
        distinctions_complete
        and evidence_complete
        and observation.get("identified_search_page_index") == 0
        and observation.get("search_role_is_unique") is True
        and observation.get("confusable_page_indexes") == []
    )
    session["set_role_distinction_audit"] = copy.deepcopy(observation)
    session["set_role_distinction_passed"] = passed
    if not passed:
        error = (
            "SEARCH_ROLE_NOT_UNIQUE_IN_SIX_PAGE_BLIND_TEST"
            if distinctions_complete and evidence_complete
            else "SET_ROLE_AUDIT_EVIDENCE_INCOMPLETE"
        )
        session.setdefault("residual_issues_for_user_final_review", []).append({
            "page_index": 0,
            "errors": [error],
            "failed_checks": ["approved_plan_fidelity_match"],
            "decision_summary": (
                evidence.strip()
                if evidence_complete
                else "The final set review returned incomplete role evidence; all six generated assets remain available for user final review."
            ),
        })
    transition(session, "AWAITING_USER_FINAL_REVIEW", "audit-set-role-distinction-complete-ready-for-user-final-review")
    save(args.session, session)
    return result(session, set_role_distinction_status="PASS" if passed else "BLOCKED")


def cmd_restart_campaign(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require(session.get("state") not in {"AWAITING_IDENTITY", "FINALIZED"}, "campaign restart is unavailable in the current state")
    reason = args.reason.strip()
    require(reason, "restart reason is required")
    previous = {
        "compiled_hash": session.get("compiled_hash"),
        "campaign_style_id": session.get("campaign_style_id"),
        "accepted_page_indexes": copy.deepcopy(session.get("accepted_page_indexes", [])),
        "reason": reason,
        "superseded_at": utc_now(),
    }
    session.setdefault("superseded_campaigns", []).append(previous)
    for version in session.get("generated_versions", []):
        if version.get("status") == "ACCEPTED":
            version["status"] = "SUPERSEDED_BY_CAMPAIGN_RESTART"
    if session.get("authorization"):
        session.setdefault("authorization_history", []).append(copy.deepcopy(session["authorization"]))
    session["plan"] = None
    session["compiled_hash"] = None
    session["commercial_contract_hash"] = None
    session["campaign_style_id"] = None
    session["campaign_constitution"] = None
    session["campaign_constitution_sha256"] = None
    session["authorization"] = None
    session["pending_generation"] = None
    session["current_page_index"] = None
    session["accepted_page_indexes"] = []
    session["style_calibrated"] = False
    session["search_baseline_calibrated"] = False
    session["automatic_repairs_used_by_page"] = {str(index): 0 for index in range(6)}
    session["required_repair_scope"] = None
    transition(session, "IDENTITY_LOCKED", "restart-campaign")
    save(args.session, session)
    return result(session, authorization_invalidated=True, previous_compiled_hash=previous["compiled_hash"])


def cmd_prepare_repair(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "REPAIR_REQUIRED")
    repair = load_json(args.repair_plan)
    required_scope = session.get("required_repair_scope")
    require(repair.get("scope") == required_scope, f"repair scope must be {required_scope}")
    instruction = repair.get("instruction")
    require(isinstance(instruction, str) and instruction.strip(), "repair instruction is required")
    require(isinstance(repair.get("preserve"), list) and repair["preserve"], "repair preserve list is required")
    require(isinstance(repair.get("expected_visible_change"), str) and len(repair["expected_visible_change"].strip()) >= 12, "expected_visible_change is required")
    source_checks = repair.get("failed_check_names")
    require(isinstance(source_checks, list) and source_checks, "failed_check_names is required")
    required_checks = current_version(session)["visual_audit"]["failed_checks"]
    require(set(source_checks) == set(required_checks), "repair must address exactly the failed visual checks")
    findings = repair.get("all_failed_findings")
    require(isinstance(findings, list) and findings, "all_failed_findings is required")
    finding_checks = []
    for item in findings:
        require(isinstance(item, dict), "each failed finding must be an object")
        require(item.get("check") in required_checks, "failed finding must reference a failed check")
        for field in ("visible_problem", "root_cause", "required_change"):
            require(isinstance(item.get(field), str) and len(item[field].strip()) >= 8, f"failed finding {field} must be concrete")
        finding_checks.append(item["check"])
    require(set(finding_checks) == set(required_checks), "repair diagnosis must cover all failed checks before the only automatic repair")
    require(isinstance(repair.get("root_cause_reflection"), str) and len(repair["root_cause_reflection"].strip()) >= 20, "root_cause_reflection is required")
    require(isinstance(repair.get("single_second_attempt_strategy"), str) and len(repair["single_second_attempt_strategy"].strip()) >= 20, "single_second_attempt_strategy is required")
    page_index = session["current_page_index"]
    page = session["plan"]["pages"][page_index]
    used = int(session.get("automatic_repairs_used_by_page", {}).get(str(page_index), 0))
    require(used < 1, "the single automatic repair has already been used; retain the second attempt for final-set user review")
    page["revision"] = int(page.get("revision", 0)) + 1
    page["generation_origin"] = "AUTOMATIC_REPAIR"
    page["repair_instruction"] = {
        "scope": required_scope,
        "instruction": instruction.strip(),
        "preserve": repair["preserve"],
        "expected_visible_change": repair["expected_visible_change"].strip(),
        "failed_check_names": source_checks,
        "source_errors": current_version(session)["visual_audit"]["errors"],
        "all_failed_findings": findings,
        "root_cause_reflection": repair["root_cause_reflection"].strip(),
        "single_second_attempt_strategy": repair["single_second_attempt_strategy"].strip(),
    }
    session["automatic_repairs_used_by_page"][str(page_index)] = used + 1
    page["generation_prompt"] = compile_prompt(session, page)
    session["compiled_hash"] = compiled_contract_hash(session)
    rebase_authorization(session, "TARGETED_REPAIR_WITHIN_USER_APPROVED_SIX_PAGE_TASK")
    session["required_repair_scope"] = None
    transition(session, "AUTHORIZED", "prepare-repair")
    save(args.session, session)
    return result(session, page_index=page_index, revision=page["revision"], authorization_rebased=True)


def cmd_apply_user_adjustment(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "FINALIZED", "AWAITING_USER_FINAL_REVIEW", "AWAITING_USER_INTERVENTION", "REPAIR_REQUIRED", "AUTHORIZED")
    adjustment = load_json(args.adjustment)
    require(adjustment.get("scope") == "LOCAL_PAGE_DELTA", "only LOCAL_PAGE_DELTA is allowed; global identity/style/plan changes require replan or restart")
    page_index = adjustment.get("page_index")
    require(isinstance(page_index, int) and 0 <= page_index < 6, "adjustment.page_index must be 0..5")
    text = adjustment.get("user_instruction_verbatim")
    require(isinstance(text, str) and len(text.strip()) >= 2, "user_instruction_verbatim is required")
    changes = adjustment.get("requested_visible_changes")
    preserve = adjustment.get("preserve")
    require(isinstance(changes, list) and changes and all(isinstance(x, str) and x.strip() for x in changes), "requested_visible_changes must be a nonempty text list")
    require(isinstance(preserve, list) and preserve and all(isinstance(x, str) and x.strip() for x in preserve), "preserve must be a nonempty text list")
    require(adjustment.get("changes_identity") is False, "identity changes require campaign restart")
    require(adjustment.get("changes_verified_facts") is False, "fact changes require recompile/replan")
    require(adjustment.get("changes_campaign_style") is False, "campaign-wide style changes require campaign restart")
    require(adjustment.get("changes_page_role") is False, "page-role changes require replan/restart")
    page = session["plan"]["pages"][page_index]
    previous_accepted = [x for x in session.get("generated_versions", []) if x["page_index"] == page_index and x.get("status") in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}]
    for version in previous_accepted:
        version["status"] = "SUPERSEDED_BY_USER_ADJUSTMENT"
    session["accepted_page_indexes"] = [x for x in session.get("accepted_page_indexes", []) if x != page_index]
    if session.get("final_manifest"):
        session.setdefault("superseded_final_manifests", []).append({"file": session["final_manifest"], "superseded_at": utc_now(), "page_index": page_index})
        session["final_manifest"] = None
    delta = {
        "scope": "LOCAL_PAGE_DELTA",
        "page_index": page_index,
        "user_instruction_verbatim": text.strip(),
        "requested_visible_changes": changes,
        "preserve": preserve,
        "immutable_contracts": ["product identity", "verified facts", "campaign style", "page role", "approved plan except named delta", "source background isolation", "selling-point visual storyboard"],
        "applied_at": utc_now(),
    }
    session.setdefault("user_adjustment_history", []).append(copy.deepcopy(delta))
    page["active_user_adjustment"] = delta
    page["repair_instruction"] = None
    page["revision"] = int(page.get("revision", 0)) + 1
    page["generation_origin"] = "USER_ADJUSTMENT"
    page["generation_prompt"] = compile_prompt(session, page)
    session["current_page_index"] = page_index
    session["pending_generation"] = None
    session["required_repair_scope"] = None
    session["set_role_distinction_passed"] = False
    if page_index == 0:
        session["search_baseline_calibrated"] = False
    session["compiled_hash"] = compiled_contract_hash(session)
    rebase_authorization(session, "USER_AUTHORIZED_LOCAL_PAGE_DELTA_WITH_CAMPAIGN_CONSTITUTION_PRESERVED")
    transition(session, "AUTHORIZED", "apply-user-adjustment")
    save(args.session, session)
    return result(session, page_index=page_index, revision=page["revision"], campaign_constitution_sha256=session["campaign_constitution_sha256"], next_action="generate-next")


def cmd_calibrate_style(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_AGENT_STYLE_CALIBRATION")
    observation = load_json(args.observation)
    require(observation.get("agent_pixel_reviewed") is True, "style calibration requires direct Agent pixel review")
    require(observation.get("review_mode") == "DIRECT_PIXEL_VISUAL_COMPARISON", "style calibration review_mode is invalid")
    evidence = observation.get("observed_evidence")
    require(isinstance(evidence, str) and len(evidence.strip()) >= 20, "style calibration needs concrete observed pixel evidence")
    approved = observation.get("source_page_indexes")
    require(isinstance(approved, list) and approved, "source_page_indexes is required")
    require(set(approved).issubset(set(session["accepted_page_indexes"])), "style source pages must already be Agent-accepted")
    tokens = observation.get("style_tokens")
    require(isinstance(tokens, dict) and tokens, "style_tokens are required")
    require(set(tokens).issubset(STYLE_FIELDS), "style observation contains product, copy, layout, or unknown fields")
    for key, value in tokens.items():
        session["plan"]["campaign_style"][key] = copy.deepcopy(value)
    validate_campaign_style(session["plan"]["campaign_style"])
    session["style_calibrated"] = True
    session["style_calibration"] = {
        "source_page_indexes": approved,
        "agent_pixel_reviewed": True,
        "review_mode": "DIRECT_PIXEL_VISUAL_COMPARISON",
        "observed_evidence": evidence.strip(),
        "style_tokens": copy.deepcopy(tokens),
        "authority": "STYLE_REFERENCE_ONLY",
        "calibrated_at": utc_now(),
    }
    session["campaign_style_id"] = "CAMPAIGN-CALIBRATED-" + digest_value(
        {"request_id": session["request_id"], "style": session["plan"]["campaign_style"]}
    )[:12].upper()
    session["campaign_constitution"]["campaign_style_id"] = session["campaign_style_id"]
    session["campaign_constitution"]["campaign_style"] = copy.deepcopy(session["plan"]["campaign_style"])
    session["campaign_constitution"]["background_style_lock"] = background_style_lock(session["plan"]["campaign_style"])
    session["campaign_constitution_sha256"] = digest_value(session["campaign_constitution"])
    for page in session["plan"]["pages"]:
        if page["index"] not in session["accepted_page_indexes"]:
            if page.get("v015_execution_prompt"):
                task = page.get("commercial_page_task") or session["plan"]["commercial_orchestration"]["page_task_table"][page["index"]]
                page["v015_execution_prompt"] = concise_execution_prompt(
                    session["raw_input"],
                    session["identity_contract"],
                    session["plan"]["campaign_style"],
                    session["plan"]["style_evidence_contract"],
                    task,
                    page,
                )
            page["generation_prompt"] = compile_prompt(session, page)
    session["compiled_hash"] = compiled_contract_hash(session)
    rebase_authorization(session, "AGENT_PIXEL_REVIEWED_STYLE_CALIBRATION_WITHIN_USER_APPROVED_TASK")
    transition(session, "AUTHORIZED", "calibrate-style")
    save(args.session, session)
    return result(session, campaign_style_id=session["campaign_style_id"], authorization_rebased=True)


def cmd_finalize(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    require_state(session, "AWAITING_USER_FINAL_REVIEW")
    require(session.get("accepted_page_indexes") == list(range(6)), "all six pages must be accepted")
    approval_text = args.approval_text.strip()
    require(len(approval_text) >= 2, "final user approval text is required")
    require(isinstance(session.get("set_role_distinction_audit"), dict), "internal six-page role audit must complete before final user review")
    session["final_user_approval"] = {
        "approval_text": approval_text,
        "approval_text_sha256": digest_value(approval_text),
        "approved_at": utc_now(),
        "authority": "USER_FINAL_SIX_IMAGE_ACCEPTANCE",
    }
    final_pages = []
    for index, role in enumerate(PAGE_ROLES):
        versions = [
            x
            for x in session["generated_versions"]
            if x["page_index"] == index and x["status"] in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}
        ]
        require(bool(versions), f"accepted asset missing for page {index}")
        version = versions[-1]
        path = Path(version["file"])
        require(path.is_file(), f"accepted asset missing on disk: {path}")
        require(digest_file(path) == version["sha256"], f"accepted asset changed after audit: {path}")
        final_pages.append(
            {
                "page_index": index,
                "page_role": role,
                "file": str(path),
                "sha256": version["sha256"],
                "revision": version["revision"],
                "visual_audit_status": "PASS" if version["status"] == "ACCEPTED" else "ACCEPTED_WITH_RESIDUALS_BY_FINAL_USER_REVIEW",
            }
        )
    manifest = {
        "schema_version": "controlled-generation-set-manifest-0.1.17",
        "request_id": session["request_id"],
        "status": "FINALIZED",
        "compiled_hash": session["compiled_hash"],
        "commercial_contract_hash": session["commercial_contract_hash"],
        "campaign_style_id": session["campaign_style_id"],
        "campaign_constitution_sha256": session["campaign_constitution_sha256"],
        "final_pages": final_pages,
        "excluded_generated_assets": session["excluded_generated_assets"],
        "residual_issues_disclosed_to_user": copy.deepcopy(session.get("residual_issues_for_user_final_review", [])),
        "set_role_distinction_passed": session.get("set_role_distinction_passed", False),
        "final_user_approval": copy.deepcopy(session["final_user_approval"]),
        "generation_receipt_is_not_quality_acceptance": True,
        "profile01_semantics_read": False,
    }
    write_json(args.output, manifest)
    transition(session, "FINALIZED", "finalize")
    session["final_manifest"] = str(Path(args.output).resolve())
    save(args.session, session)
    return result(session, manifest=args.output, pages=6)


def cmd_inspect(args: argparse.Namespace) -> dict[str, Any]:
    session = load_json(args.session)
    return result(
        session,
        current_page_index=session.get("current_page_index"),
        next_page_index=next_page_index(session),
        accepted_page_indexes=session.get("accepted_page_indexes", []),
        compiled_hash=session.get("compiled_hash"),
        commercial_contract_hash=session.get("commercial_contract_hash"),
        campaign_style_id=session.get("campaign_style_id"),
        search_baseline_calibrated=session.get("search_baseline_calibrated", False),
        style_calibrated=session.get("style_calibrated", False),
        residual_issues_for_user_final_review=session.get("residual_issues_for_user_final_review", []),
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Domestic e-commerce six-image continuity executor v0.1.17")
    sub = value.add_subparsers(dest="command", required=True)
    command = sub.add_parser("start")
    command.add_argument("--input", required=True)
    command.add_argument("--session", required=True)
    command.set_defaults(func=cmd_start)
    command = sub.add_parser("lock-identity")
    command.add_argument("--session", required=True)
    command.add_argument("--identity", required=True)
    command.set_defaults(func=cmd_lock_identity)
    command = sub.add_parser("compile")
    command.add_argument("--session", required=True)
    command.add_argument("--plan", required=True)
    command.set_defaults(func=cmd_compile)
    command = sub.add_parser("compile-v013")
    command.add_argument("--session", required=True)
    command.add_argument("--input", help="optional native v0.1.13 raw-input contract")
    command.add_argument("--identity", help="optional native v0.1.13 product-identity contract")
    command.add_argument("--source-manifest", help="optional override; bundled manifest is the default")
    command.add_argument("--output", help="optional full v0.1.13 compiled artifact")
    command.set_defaults(func=cmd_compile_v013)
    command = sub.add_parser("replan")
    command.add_argument("--session", required=True)
    command.add_argument("--plan", required=True)
    command.add_argument("--reason", required=True)
    command.set_defaults(func=cmd_replan)
    command = sub.add_parser("restart-campaign")
    command.add_argument("--session", required=True)
    command.add_argument("--reason", required=True)
    command.set_defaults(func=cmd_restart_campaign)
    command = sub.add_parser("authorize")
    command.add_argument("--session", required=True)
    command.add_argument("--approval-text", required=True)
    command.set_defaults(func=cmd_authorize)
    command = sub.add_parser("generate-next")
    command.add_argument("--session", required=True)
    command.add_argument("--out")
    command.set_defaults(func=cmd_generate_next)
    command = sub.add_parser("record-generation")
    command.add_argument("--session", required=True)
    command.add_argument("--token", required=True)
    command.add_argument("--image", required=True)
    command.set_defaults(func=cmd_record_generation)
    command = sub.add_parser("audit-current")
    command.add_argument("--session", required=True)
    command.add_argument("--judgment", required=True)
    command.set_defaults(func=cmd_audit_current)
    command = sub.add_parser("calibrate-search-baseline")
    command.add_argument("--session", required=True)
    command.add_argument("--observation", required=True)
    command.set_defaults(func=cmd_calibrate_search_baseline)
    command = sub.add_parser("audit-set-role-distinction")
    command.add_argument("--session", required=True)
    command.add_argument("--observation", required=True)
    command.set_defaults(func=cmd_audit_set_role_distinction)
    command = sub.add_parser("prepare-repair")
    command.add_argument("--session", required=True)
    command.add_argument("--repair-plan", required=True)
    command.set_defaults(func=cmd_prepare_repair)
    command = sub.add_parser("apply-user-adjustment")
    command.add_argument("--session", required=True)
    command.add_argument("--adjustment", required=True)
    command.set_defaults(func=cmd_apply_user_adjustment)
    command = sub.add_parser("calibrate-style")
    command.add_argument("--session", required=True)
    command.add_argument("--observation", required=True)
    command.set_defaults(func=cmd_calibrate_style)
    command = sub.add_parser("finalize")
    command.add_argument("--session", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--approval-text", required=True)
    command.set_defaults(func=cmd_finalize)
    command = sub.add_parser("inspect")
    command.add_argument("--session", required=True)
    command.set_defaults(func=cmd_inspect)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        output = args.func(args)
    except HarnessError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
