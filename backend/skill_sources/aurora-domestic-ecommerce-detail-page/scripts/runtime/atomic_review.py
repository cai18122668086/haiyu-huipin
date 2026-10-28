"""Canonical atomic reports for the two fixed windows of a pair canvas."""
from __future__ import annotations

import hashlib
import json

from .artifact_binding import validate_artifact_binding
from .local_contracts import effective_module
from .performance_contract import resolve_performance_contract
from .spatial_contract import resolve_spatial_contract
from .state import HarnessError
from .visual_gate import validate_module_judgment
from .website_review import PROFILE, validate_module as validate_website_module


def contract_sha256(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def review_contracts(modules: list[dict], count: int) -> list[dict]:
    modules = [effective_module(module) for module in modules]
    return [{"module_id": module["id"], "contract": module["plan"],
             "contract_sha256": contract_sha256(module["plan"]),
             "spatial_contract": resolve_spatial_contract(module["plan"]),
             "performance_contract": resolve_performance_contract(module["plan"], module["order"], count)} for module in modules]


def validate_atomic_reports(judgment: dict, modules: list[dict], lineage: dict) -> list[dict]:
    modules = [effective_module(module) for module in modules]
    reports = judgment.get("module_judgments")
    if not isinstance(reports, list) or [row.get("module_id") for row in reports] != [row["id"] for row in modules]:
        raise HarnessError("module_judgments must contain both ordered atomic screen reports")
    findings = []
    for index, (report, module, sliced) in enumerate(zip(reports, modules, lineage["slices"], strict=True)):
        if report["approved_contract_sha256"] != contract_sha256(module["plan"]):
            raise HarnessError("Atomic report approved contract fingerprint differs")
        if report["slice_binding"] != {"canvas_sha256": lineage["canvas"]["sha256"], "crop_box": sliced["crop_box"]}:
            raise HarnessError("Atomic report fixed slice binding differs")
        validate_artifact_binding(report, sliced["path"], "atomic " + module["id"])
        observed = report["observations"]
        if report["artifact_binding"]["ocr_text"] != observed["ocr_text"]:
            raise HarnessError("Atomic raw OCR and artifact binding disagree")
        if module["plan"].get("website_contract_version") == PROFILE:
            derived = validate_website_module(report, module)
        else:
            if type(observed.get("headline_visible")) is not bool:
                raise HarnessError("Atomic headline visibility must be a known boolean")
            rank = observed.get("headline_rank")
            if (observed["headline_visible"] and type(rank) is not int) or (not observed["headline_visible"] and rank is not None):
                raise HarnessError("Atomic headline visibility and measured rank disagree or are unknown")
            if observed["independent_brand_logo_source_fidelity"] == "UNVERIFIABLE":
                raise HarnessError("Atomic brand fidelity is unknown; it cannot authorize an image repair")
            if module["plan"].get("expression_contract_version") == "4.3":
                signature = "+".join(observed[key] for key in ("observed_primary_subject_class", "observed_presentation_form", "observed_camera_scale", "observed_complete_product_role"))
                if observed["observed_expression_signature"] != signature or observed["planned_expression_signature_match"] is not (signature == module["plan"]["expression_signature"]):
                    raise HarnessError("Atomic expression observations are internally contradictory")
                if observed.get("product_presence_observed") != observed["observed_complete_product_role"]:
                    raise HarnessError("Atomic product role and presence observations disagree")
            actual_full_product = observed.get("product_presence_observed") in ("DOMINANT", "SUPPORTING")
            planned_role = module["plan"].get("complete_product_role")
            if actual_full_product and planned_role in ("ABSENT", "IDENTITY_TOKEN_ONLY") and observed.get("unplanned_full_product_seen") is not True:
                raise HarnessError("A full product in an absence/token-only contract must be explicitly observed as unplanned")
            derived = validate_module_judgment(report, module, pair_canvas_audit=True)
        if judgment["status"] == "PASS" and report["status"] != "PASS":
            raise HarnessError("PASS pair contradicts a failing atomic module")
        for finding in [*report["material_failures"], *derived]:
            findings.append({**finding, "module_id": module["id"], "atomic_report_index": index})
    return findings
