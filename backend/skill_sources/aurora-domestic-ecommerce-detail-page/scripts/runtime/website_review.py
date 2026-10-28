"""Opt-in website v1 pixel contract; strict historical audits stay unchanged."""
from __future__ import annotations

from .state import HarnessError

PROFILE = "WEBSITE_LEAN_V1"
MARKER = "website_contract_version"
CHECKS = (
    "identity", "facts", "legibility", "approved_plan_fidelity",
    "composition_and_continuity", "proof_integrity",
)


def validate_plan_source(plan: dict) -> None:
    rows = plan.get("claim_coverage")
    if not isinstance(rows, list) or not rows:
        raise HarnessError("Website plan requires its authored claim/job source")
    source = {row["claim_id"]: row for row in rows}
    if len(source) != len(rows):
        raise HarnessError("Website primary claim IDs must be unique")
    depth = plan["selling_point_depth_contract"]
    entries = {row["claim_id"]: row for row in depth["claim_depth"]}
    recall = plan["campaign_recall_contract"]
    if set(source) != set(entries) or set(source) != set(depth["primary_claim_ids"]):
        raise HarnessError("Website claim source and derived depth disagree")
    for claim, row in source.items():
        entry = entries[claim]
        if entry["module_jobs"] != row["module_jobs"] or entry["evidence_ceiling"] != row["evidence_ceiling"]:
            raise HarnessError("Website job/evidence views differ from the authored source")
        dedicated = {job["module_id"] for job in row["module_jobs"] if job["job"] == "DEDICATED_EXPRESSION"}
        if set(entry["dedicated_module_ids"]) != dedicated or set(recall["proof_module_ids_by_claim"][claim]) != dedicated:
            raise HarnessError("Website dedicated proof/recall views differ from the source")


def schema_name(plan: dict) -> str:
    profile = plan.get(MARKER)
    if profile is None:
        return "pair-judgment.schema.json"
    if profile != PROFILE:
        raise HarnessError("Unknown website review contract version")
    return "pair-judgment-website-v1.schema.json"


def validate_module(value: dict, module: dict) -> list[dict]:
    from .visual_gate import (
        _matches_whitelist, _validate_concrete_check_evidence,
        _eligible_advisory_context_copy, _eligible_advisory_visual_marks,
        require_identity_silhouette, scope_for_presence,
    )
    if module["plan"].get(MARKER) != PROFILE or value.get(MARKER) != PROFILE:
        raise HarnessError("Website pixel report differs from the approved contract")
    if value.get("module_id") != module["id"] or value.get("audit_complete") is not True:
        raise HarnessError("Website report must identify the actual module and be complete")
    checks = value.get("checks") or {}
    if set(checks) != set(CHECKS):
        raise HarnessError("Website report requires the six core pixel checks")
    _validate_concrete_check_evidence(checks, "website module " + module["id"])
    observed = value["observations"]
    presence = observed["product_presence_observed"]
    require_identity_silhouette(
        value, "website module " + module["id"], allow_material_failure=True,
        expected_scope=scope_for_presence(presence),
    )
    findings = [
        {"check": name, "visible_evidence": row["visible_evidence"]}
        for name, row in checks.items() if row["pass"] is False
    ]
    plan = module["plan"]
    missing = observed["planned_elements_missing"]
    if missing:
        findings.append({"check": "approved_plan_fidelity", "visible_evidence": "; ".join(missing)})
    if not observed["content_units_observed"] or not observed["visible_proof_relations"]:
        findings.append({"check": "proof_integrity", "visible_evidence": "Missing observed content or visual proof relations"})
    texts = [item["text"] for item in plan.get("visible_copy", [])]
    unauthorized = list(observed["unauthorized_ocr_text"])
    advisory = _eligible_advisory_context_copy(observed, module) + _eligible_advisory_visual_marks(observed)
    unauthorized.extend(
        text for text in observed["ocr_text"]
        if not _matches_whitelist(text, texts + advisory)
    )
    if unauthorized or observed["forbidden_text_hits"]:
        findings.append({"check": "facts", "visible_evidence": "; ".join(unauthorized + observed["forbidden_text_hits"])})
    role = plan["complete_product_role"]
    if (presence in ("DOMINANT", "SUPPORTING") and role in ("ABSENT", "IDENTITY_TOKEN_ONLY")) or (
        role in ("DOMINANT", "SUPPORTING") and presence not in ("DOMINANT", "SUPPORTING")
    ):
        findings.append({"check": "approved_plan_fidelity", "visible_evidence": observed["composition_observation"]})
    required_logo = plan.get("independent_logo_policy") == "REQUIRED_IF_AUTHORIZED"
    count = observed["independent_brand_logo_count"]
    fidelity = observed["independent_brand_logo_source_fidelity"]
    if fidelity == "UNVERIFIABLE":
        raise HarnessError("Website brand observation is unknown")
    if count != int(required_logo) or (required_logo and fidelity != "MATCHES_AUTHORIZED_SOURCE"):
        findings.append({"check": "identity", "visible_evidence": observed["brand_observation"]})
    if value["status"] == "PASS" and (findings or value["material_failures"]):
        raise HarnessError("Website PASS contradicts material pixel observations")
    if value["status"] == "MATERIAL_FAIL" and not value["material_failures"]:
        raise HarnessError("Website material failure needs concrete visible evidence")
    return findings
