"""Native final delivery projection and read-only verification."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runtime.artifact_binding import file_sha256
from runtime.state import HarnessError


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def accepted_attempt(module: dict) -> dict:
    accepted = [row for row in module.get("attempts", [])
                if row.get("image") == module.get("accepted_asset")]
    if not accepted:
        raise HarnessError("Final delivery needs the original accepted attempt")
    return accepted[-1]


def build_final_manifest(session: dict, version: str, approval: dict) -> dict:
    if session.get("version") != version:
        raise HarnessError("Final delivery requires its pinned native runtime")
    modules = session.get("modules")
    if not isinstance(modules, list) or not modules:
        raise HarnessError("Final delivery needs accepted native modules")
    if [m.get("id") for m in modules] != [m.get("id") for m in session["plan"]["modules"]]:
        raise HarnessError("Final delivery must preserve the approved module order")
    for module in modules:
        if module.get("status") not in {"COMPLETE", "COMPLETE_WITH_RESIDUALS"}:
            raise HarnessError("An unfinished module cannot be delivered")
        if not isinstance(module.get("residual_issues"), list):
            raise HarnessError("Original residual issues must remain explicit")
        attempt = accepted_attempt(module)
        if not isinstance((attempt.get("judgment") or {}).get("artifact_binding"), dict):
            raise HarnessError("Final delivery needs the original pixel audit binding")
    if (
        not isinstance(approval, dict)
        or set(approval) != {"text", "at", "mode"}
        or not isinstance(approval.get("text"), str)
        or not approval["text"].strip()
        or not isinstance(approval.get("at"), str)
        or not approval["at"].strip()
        or approval.get("mode") not in {"USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED"}
    ):
        raise HarnessError("Final approval text, time and explicit mode are required")
    if approval["mode"] == "AUTO_FINALIZE_AUTHORIZED":
        authorized = session.get("approval") or {}
        if (authorized.get("delivery_mode") != "AUTO_FINALIZE_AUTHORIZED"
                or authorized.get("text") != approval["text"]):
            raise HarnessError("Automatic delivery needs the original explicit authorization")
    residuals = [{"module_id": module["id"], "issues": module["residual_issues"]} for module in session["modules"] if module["residual_issues"]]
    render_containers = []
    pair_config = (session.get("plan", {}).get("output_strategy", {}).get("continuous_pair_rendering") or {})
    if isinstance(pair_config, dict) and pair_config.get("enabled"):
        for pair in pair_config.get("pair_ids", []):
            first = next((item for item in session["modules"] if item["id"] == pair["module_ids"][0]), None)
            accepted = next((attempt for attempt in reversed(first.get("attempts", [])) if attempt.get("image") == first.get("accepted_asset")), None) if first else None
            if accepted is None or not accepted.get("pair_canvas"):
                raise HarnessError("An accepted pair canvas is required for final delivery")
            render_containers.append({
                "mode": "CONTINUOUS_PAIR",
                "pair_id": pair["pair_id"],
                "module_ids": pair["module_ids"],
                "pair_canvas": accepted["pair_canvas"],
                "pair_canvas_sha256": file_sha256(accepted["pair_canvas"]),
                "artifact_lineage_sha256": accepted.get("artifact_lineage_sha256"),
                "slice_height_px": session["plan"]["output_strategy"]["module_height_px"],
            })
    manifest = {
        "skill": "aurora-domestic-ecommerce-detail-page",
        "version": version,
        "product_name": session["input"]["product_name"],
        "platform_profile": session["input"]["platform_profile"],
        "approval": dict(approval),
        "long_page": (session.get("assembly") or {}).get("long_page"),
        "modules": [{
            "id": module["id"], "order": module["order"], "asset": module["accepted_asset"],
            "status": module["status"], "residual_issues": module["residual_issues"],
            "review_evidence": accepted_attempt(module).get("review_evidence"),
            "asset_sha256": file_sha256(module["accepted_asset"]) if module.get("accepted_asset") else None,
            "audit_binding": next((
                attempt.get("judgment", {}).get("artifact_binding")
                for attempt in reversed(module.get("attempts", []))
                if attempt.get("judgment") and attempt.get("image") == module.get("accepted_asset")
            ), None),
        } for module in session["modules"]],
        "slices": (session.get("assembly") or {}).get("slices", []),
        "render_containers": render_containers,
        "seam_audit": {
            "pair_audits": (session.get("long_page_audit") or {}).get("pair_audits", []),
            "pair_transition_audits": (session.get("long_page_audit") or {}).get("pair_transition_audits", []),
        },
        "residual_issues": residuals,
        "long_page_audit": session.get("long_page_audit"),
        "backend_confirmation_required": session["input"]["platform_profile"]["verification_status"] != "VERIFIED_CURRENT_BACKEND",
    }
    return manifest


def inspect_final_manifest(session: dict, manifest_path: str, version: str) -> dict:
    if session.get("state") != "FINALIZED":
        raise HarnessError("The native session has not been finalized")
    final = session.get("final") or {}
    path = Path(manifest_path)
    if path.is_symlink() or Path(final.get("manifest", "")).resolve() != path.resolve():
        raise HarnessError("The exact original final manifest path is required")
    payload = path.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    if final.get("manifest_sha256") != sha:
        raise HarnessError("The original final manifest bytes are missing or changed")
    manifest = json.loads(payload.decode("utf-8"))
    expected = build_final_manifest(session, version, final.get("approval"))
    if fingerprint(manifest) != fingerprint(expected):
        raise HarnessError("The final manifest differs from its original accepted native truth")
    return {"version": version, "manifest_file": str(path.resolve()),
            "manifest_file_sha256": sha, "manifest": manifest}
