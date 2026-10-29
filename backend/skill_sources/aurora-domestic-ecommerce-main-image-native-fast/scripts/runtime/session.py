from __future__ import annotations

import copy
import secrets
from pathlib import Path
from typing import Any

from compiler import compile_prompt, normalize_plan, plan_summary
from core import (
    PAGE_ROLES,
    SESSION_SCHEMA_VERSION,
    HarnessError,
    digest,
    file_digest,
    image_dimensions,
    load_json,
    now,
    record,
    reference_paths,
    require,
    require_state,
    selected_version,
    transition,
    validate_schema,
    write_json,
)
from identity import validate_identity


ALLOWED_ACTIONS = {
    "AWAITING_IDENTITY": ["lock-identity", "inspect"],
    "IDENTITY_LOCKED": ["compile", "inspect"],
    "AWAITING_AUTHORIZATION": ["compile", "authorize", "inspect"],
    "AUTHORIZED": ["generate-next", "inspect"],
    "AWAITING_GENERATED_ASSET": ["record-generation", "recover-generation", "inspect"],
    "READY_FOR_DELIVERY": ["finalize", "inspect"],
    "FINALIZED": ["inspect"],
}

def _save(path: str | Path, session: dict[str, Any]) -> None:
    session["allowed_actions"] = ALLOWED_ACTIONS.get(session["state"], ["inspect"])
    validate_schema(session, "session.schema.json")
    write_json(path, session)


def _result(session: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"status": session["state"], "request_id": session["request_id"], "allowed_actions": ALLOWED_ACTIONS.get(session["state"], ["inspect"]), **extra}


def _modifications_used(session: dict[str, Any], page_index: int) -> int:
    return int(session["modifications_used_by_page"][str(page_index)])


def _consume_modification(session: dict[str, Any], page_index: int, kind: str) -> None:
    require(_modifications_used(session, page_index) < 1, f"page {page_index} modification budget is exhausted; each page may be modified only once per campaign")
    session["modifications_used_by_page"][str(page_index)] = 1
    session.setdefault("modification_history", []).append({"page_index": page_index, "kind": kind, "consumed_at": now()})


def _authorization_rebase(session: dict[str, Any], reason: str) -> None:
    authorization = session.get("authorization")
    require(isinstance(authorization, dict), "initial user authorization is missing")
    session.setdefault("authorization_history", []).append(copy.deepcopy(authorization))
    authorization["previous_plan_digest"] = authorization.get("plan_digest")
    authorization["plan_digest"] = session["plan_digest"]
    authorization["rebased_at"] = now()
    authorization["rebase_reason"] = reason


def _fact_constitution(session: dict[str, Any]) -> dict[str, Any]:
    identity = session["identity"]
    return {
        "product_identity": {
            "product_form": identity["product_form"],
            "identity_summary": identity["identity_summary"],
            "must_preserve": copy.deepcopy(identity["must_preserve"]),
            "must_not_change": copy.deepcopy(identity["must_not_change"]),
        },
        "campaign_style": copy.deepcopy(session["plan"]["campaign_style"]),
        "six_page_duties": [{"index": page["index"], "role": page["page_role"], "primary_claim": page["primary_claim"]} for page in session["plan"]["pages"]],
        "source_background_isolation": "Original scene is capture noise unless the user explicitly preserves a named element; rebuild the scene and keep this rule through repairs and local adjustments.",
    }


def start(input_path: str, session_path: str) -> dict[str, Any]:
    target = Path(session_path)
    require(not target.exists(), f"session already exists: {target}")
    raw = load_json(input_path)
    validate_schema(raw, "input.schema.json")
    require(bool(reference_paths(raw)), "at least one original product reference is required")
    from core import fact_records
    require(bool(fact_records(raw)), "at least one user-provided display fact is required")
    created = now()
    session = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "request_id": raw["request_id"],
        "state": "AWAITING_IDENTITY",
        "created_at": created,
        "updated_at": created,
        "raw_input": raw,
        "identity": None,
        "plan": None,
        "plan_digest": None,
        "campaign_constitution": None,
        "authorization": None,
        "pending_generation": None,
        "active_page_index": None,
        "accepted_pages": [],
        "generated_versions": [],
        "excluded_generated_assets": [],
        "modifications_used_by_page": {str(index): 0 for index in range(6)},
        "modification_history": [],
        "search_calibrated": False,
        "style_calibrated": False,
        "residual_issues": [],
        "history": [],
    }
    record(session, "start", "NEW")
    _save(target, session)
    return _result(session)


def lock_identity(session_path: str, identity_path: str) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "AWAITING_IDENTITY")
    identity = load_json(identity_path)
    validate_identity(identity, session["raw_input"])
    session["identity"] = copy.deepcopy(identity)
    session["identity_digest"] = digest(identity)
    transition(session, "IDENTITY_LOCKED", "lock-identity")
    _save(session_path, session)
    return _result(session, identity_digest=session["identity_digest"])


def compile_campaign(session_path: str, plan_path: str | None) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "IDENTITY_LOCKED", "AWAITING_AUTHORIZATION")
    require(not session.get("accepted_pages"), "cannot replace the plan after a page is accepted; restart the campaign")
    supplied = load_json(plan_path) if plan_path else None
    plan = normalize_plan(session["raw_input"], session["identity"], supplied)
    session["plan"] = plan
    session["plan_digest"] = digest(plan)
    session["campaign_constitution"] = _fact_constitution(session)
    session["campaign_constitution_digest"] = digest(session["campaign_constitution"])
    session["authorization"] = None
    session["active_page_index"] = None
    transition(session, "AWAITING_AUTHORIZATION", "compile")
    _save(session_path, session)
    return _result(session, plan_digest=session["plan_digest"], plan_summary=plan_summary(plan), generation_performed=False)


def authorize(session_path: str, approval_text: str) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "AWAITING_AUTHORIZATION")
    text = approval_text.strip()
    require(len(text) >= 2, "approval text must preserve explicit user confirmation")
    session["authorization"] = {"approved_at": now(), "approval_text": text, "plan_digest": session["plan_digest"], "scope": "APPROVED_NATIVE_PLAN_AND_TECHNICAL_DELIVERY_WITHOUT_VISUAL_REVIEW"}
    transition(session, "AUTHORIZED", "authorize")
    _save(session_path, session)
    return _result(session, authorization_scope=session["plan_digest"])


def _next_page(session: dict[str, Any]) -> int | None:
    active = session.get("active_page_index")
    if isinstance(active, int):
        return active
    accepted = set(session.get("accepted_pages", []))
    return next((index for index in range(6) if index not in accepted), None)


def generate_next(session_path: str, output_path: str | None) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "AUTHORIZED")
    require(session.get("authorization", {}).get("plan_digest") == session.get("plan_digest"), "authorization is stale")
    page_index = _next_page(session)
    require(page_index is not None, "no remaining page")
    require(page_index not in session["accepted_pages"], "native-fast cannot regenerate an already selected page")
    page = session["plan"]["pages"][page_index]
    evidence_by_id = {item["evidence_id"]: item for item in session["identity"]["evidence"]}
    references = [{"evidence_id": evidence_id, "path": evidence_by_id[evidence_id]["path"], "role": "PRODUCT_IDENTITY_AUTHORITY"} for evidence_id in page["evidence_ids"]]
    origin = page.get("generation_origin", "INITIAL")
    require(origin == "INITIAL", "native-fast does not authorize repairs or local adjustments")
    edit_target: dict[str, Any] | None = None
    if origin == "AUTOMATIC_REPAIR" and page.get("repair", {}).get("scope") != "REBUILD_FROM_PRODUCT_EVIDENCE":
        blocked = [item for item in session["generated_versions"] if item["page_index"] == page_index and item.get("status") == "BLOCKED"]
        require(bool(blocked), "automatic repair requires a failed commercial draft")
        failed = blocked[-1]
        edit_target = {"path": failed["file"], "sha256": failed["sha256"], "role": "REPAIR_CANVAS_ONLY", "identity_authority": False}
    elif origin == "USER_ADJUSTMENT" and page.get("adjustment_canvas"):
        edit_target = copy.deepcopy(page["adjustment_canvas"])
    elif page["generation_mode"] == "PRODUCT_PRESERVING_EDIT":
        source = evidence_by_id[page["edit_target_evidence_id"]]
        edit_target = {"path": source["path"], "evidence_id": source["evidence_id"], "role": "PRODUCT_IDENTITY_AUTHORITY", "identity_authority": True}
    if edit_target and edit_target.get("role") in {"REPAIR_CANVAS_ONLY", "USER_ADJUSTMENT_CANVAS"}:
        imagegen_reference_paths = [edit_target["path"]]
    else:
        ordered_paths = ([edit_target["path"]] if edit_target else []) + [item["path"] for item in references]
        imagegen_reference_paths = list(dict.fromkeys(ordered_paths))
    require(bool(imagegen_reference_paths), "generation request has no executable image reference")
    token = digest({"request_id": session["request_id"], "plan_digest": session["plan_digest"], "page": page_index, "revision": page.get("revision", 0), "nonce": secrets.token_hex(16)})
    request = {
        "schema_version": "aurora-generation-request-2.0.0",
        "request_id": session["request_id"],
        "page_index": page_index,
        "page_role": page["page_role"],
        "revision": page.get("revision", 0),
        "generation_origin": origin,
        "generation_token": token,
        "plan_digest": session["plan_digest"],
        "campaign_constitution": copy.deepcopy(session["campaign_constitution"]),
        "page_contract": {key: copy.deepcopy(value) for key, value in page.items() if key != "prompt"},
        "prompt": page["prompt"],
        "reference_allowlist": references,
        "edit_target": edit_target,
        "imagegen_reference_paths": imagegen_reference_paths,
        "image_call_policy": {
            "maximum_calls_for_token": 1,
            "first_completed_asset_is_authoritative": True,
            "running_or_delayed_result_action": "WAIT_SAME_CALL",
            "completed_but_unbound_action": "RECOVER_AND_RECORD_FIRST_ASSET",
            "retry_for_missing_path_or_output_shape": "FORBIDDEN",
            "recovery_command": "recover-generation",
            "campaign_restart_while_pending": "FORBIDDEN",
        },
        "source_capture_policy": {
            "source_pixels_authorize": "PRODUCT_IDENTITY_ONLY",
            "must_rebuild_environment": True,
            "forbid_recognizable_transfer_of": [
                "source-specific branding",
                "distinctive background objects",
                "source arrangement or room geometry",
                "capture artifacts and incidental accessories",
            ],
            "generic_category_props_are_allowed_when": "unbranded, commercially credible, and not implying an included accessory",
        },
        "modification_budget": {"maximum": 0, "used": 0, "remaining": 0},
        "output_surface": {"aspect_ratio": "1:1", "minimum_width": 1024, "minimum_height": 1024},
    }
    session["pending_generation"] = {
        "token_digest": digest(token),
        "page_index": page_index,
        "revision": page.get("revision", 0),
        "issued_at": now(),
        "maximum_image_tool_calls": 1,
        "first_completed_asset_is_authoritative": True,
        "recovery_action": "WAIT_OR_BIND_FIRST_COMPLETED_ASSET_NEVER_REGENERATE",
    }
    session["active_page_index"] = page_index
    transition(session, "AWAITING_GENERATED_ASSET", "generate-next")
    _save(session_path, session)
    if output_path:
        write_json(output_path, request)
    return _result(session, generation_request=request)


def record_generation(session_path: str, token: str, image_path: str) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "AWAITING_GENERATED_ASSET")
    pending = session.get("pending_generation") or {}
    require(digest(token) == pending.get("token_digest"), "generation token is stale")
    image = Path(image_path).resolve()
    require(image.is_file(), f"generated image is missing: {image}")
    width, height = image_dimensions(image)
    require(width == height and width >= 1024, f"generated image must be square and at least 1024×1024, got {width}×{height}")
    page_index = pending["page_index"]
    page = session["plan"]["pages"][page_index]
    version = {"page_index": page_index, "page_role": PAGE_ROLES[page_index], "revision": pending["revision"], "file": str(image), "sha256": file_digest(image), "width": width, "height": height, "status": "GENERATED_NOT_REVIEWED", "visual_review_status": "NOT_REVIEWED", "recorded_at": now(), "origin": page.get("generation_origin", "INITIAL"), "layout_contract": copy.deepcopy(page["layout_contract"]), "display_copy_contract": copy.deepcopy(page["display_copy_contract"])}
    session["generated_versions"].append(version)
    session["pending_generation"] = None
    session["accepted_pages"] = sorted(set(session["accepted_pages"]) | {page_index})
    session["visual_review_status"] = "NOT_REVIEWED"
    _advance(session, page_index, "record-generation-technically-selected")
    _save(session_path, session)
    return _result(session, page_index=page_index, image_sha256=version["sha256"], width=width, height=height)


def recover_generation(session_path: str, token: str, generated_dir: str) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "AWAITING_GENERATED_ASSET")
    pending = session.get("pending_generation") or {}
    require(digest(token) == pending.get("token_digest"), "generation token is stale")
    directory = Path(generated_dir).resolve()
    require(directory.is_dir(), f"generated image directory is missing: {directory}")
    from datetime import datetime
    issued_at = datetime.fromisoformat(pending["issued_at"]).timestamp()
    candidates = sorted(
        (
            item for item in directory.iterdir()
            if item.is_file()
            and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and item.stat().st_mtime >= issued_at - 1
        ),
        key=lambda item: item.stat().st_mtime,
    )
    require(len(candidates) == 1, f"recovery requires exactly one image created for the pending call, found {len(candidates)}")
    return record_generation(session_path, token, str(candidates[0]))


def _current_version(session: dict[str, Any]) -> dict[str, Any]:
    page_index = session.get("active_page_index")
    versions = [item for item in session["generated_versions"] if item["page_index"] == page_index]
    require(bool(versions), "current page has no generated version")
    return versions[-1]


def _advance(session: dict[str, Any], page_index: int, action: str) -> None:
    # accepted_pages records selected technical files, not an AI visual verdict.
    session["active_page_index"] = None
    session["visual_review_status"] = "NOT_REVIEWED"
    session["set_audit_performed"] = False
    if session["accepted_pages"] == list(range(6)):
        transition(session, "READY_FOR_DELIVERY", action)
    else:
        transition(session, "AUTHORIZED", action)


def _disabled(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise HarnessError("This command is disabled in native-fast: no visual audit, calibration, automatic repair, local adjustment or campaign restart")


audit_current = _disabled
prepare_repair = _disabled
calibrate_search = _disabled
calibrate_style = _disabled
apply_user_adjustment = _disabled
audit_set = _disabled


def finalize(session_path: str, output_path: str, approval_text: str) -> dict[str, Any]:
    session = load_json(session_path)
    require_state(session, "READY_FOR_DELIVERY")
    text = approval_text.strip()
    require(len(text) >= 2, "submission authorization text is required")
    require(session["accepted_pages"] == list(range(6)), "all six real pages must be selected")
    require(session.get("authorization", {}).get("plan_digest") == session.get("plan_digest"), "authorization is stale")
    pages = []
    for index in range(6):
        version = selected_version(session, index)
        require(version is not None, f"page {index} is missing")
        path = Path(version["file"])
        require(path.is_file() and file_digest(path) == version["sha256"], f"page {index} changed after recording")
        width, height = image_dimensions(path)
        require(width == height and width >= 1024, f"page {index} canvas is invalid")
        pages.append({"page_index": index, "page_role": PAGE_ROLES[index], "file": str(path), "sha256": version["sha256"], "revision": version["revision"], "status": "GENERATED_NOT_REVIEWED", "visual_review_status": "NOT_REVIEWED"})
    manifest = {
        "schema_version": "aurora-native-fast-final-set-v1",
        "request_id": session["request_id"], "status": "FINALIZED",
        "plan_digest": session["plan_digest"],
        "campaign_constitution_digest": session["campaign_constitution_digest"],
        "pages": pages, "visual_review_status": "NOT_REVIEWED",
        "set_audit_performed": False, "automatic_repairs_used": 0,
        "delivery_authorization": {"scope": "INITIAL_SUBMISSION_AUTHORIZATION", "text": text, "recorded_at": now()},
    }
    write_json(output_path, manifest)
    session["final_manifest"] = str(Path(output_path).resolve())
    session["visual_review_status"] = "NOT_REVIEWED"
    session["set_audit_performed"] = False
    transition(session, "FINALIZED", "finalize-technical-delivery")
    _save(session_path, session)
    return _result(session, manifest=output_path, pages=6, visual_review_status="NOT_REVIEWED")


restart_campaign = _disabled


def inspect(session_path: str) -> dict[str, Any]:
    session = load_json(session_path)
    return _result(session, active_page_index=session.get("active_page_index"), next_page_index=_next_page(session), accepted_pages=session.get("accepted_pages", []), modifications_used_by_page=session.get("modifications_used_by_page", {}), pending_generation=copy.deepcopy(session.get("pending_generation")), plan_digest=session.get("plan_digest"), search_calibrated=session.get("search_calibrated", False), style_calibrated=session.get("style_calibrated", False), residual_issues=session.get("residual_issues", []))
