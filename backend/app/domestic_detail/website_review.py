"""Small actual-pixel review prompt and explicitly synthetic test projection."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from typing import Any

from .website_contract import MARKER, PROFILE

CHECKS = (
    "identity",
    "facts",
    "legibility",
    "approved_plan_fidelity",
    "composition_and_continuity",
    "proof_integrity",
)
OBSERVATIONS = (
    "ocr_text",
    "unauthorized_ocr_text",
    "forbidden_text_hits",
    "product_presence_observed",
    "planned_elements_visible",
    "planned_elements_missing",
    "visible_proof_relations",
    "content_units_observed",
    "independent_brand_logo_count",
    "independent_brand_logo_source_fidelity",
)


def project_native_negative_decisions(
    value: dict[str, Any], contracts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Native findings may deny a model PASS, never manufacture an acceptance.

    The original model message stays immutable. The unchanged bundled validator
    supplies every finding; unknown/missing observations still fail closed.
    """
    from ..config import default_skill_root
    from ..skill_runtime import SkillManifestRegistry
    from .review import DetailReviewError

    skill = "aurora-domestic-ecommerce-detail-page"
    root = default_skill_root(skill)
    SkillManifestRegistry.bundled().verify_source(skill, root)
    package = "_polaris_native_website_review"
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package,
            root / "scripts/runtime/__init__.py",
            submodule_search_locations=[str(root / "scripts/runtime")],
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    from importlib import import_module

    native = import_module(package + ".website_review")
    result = deepcopy(value)
    findings: list[dict[str, Any]] = []
    for report, contract in zip(result["module_judgments"], contracts, strict=True):
        if report["status"] != "PASS":
            continue
        # Explicit contradictory negatives remain invalid under the ordinary
        # adapter checks. This handles additional findings computed by native.
        if (
            any(row["pass"] is False for row in report["checks"].values())
            or report["material_failures"]
        ):
            continue
        diagnostic = deepcopy(report)
        diagnostic["status"] = "DIAGNOSTIC_ONLY_NEVER_ACCEPTED"
        try:
            negative = native.validate_module(
                diagnostic, {"id": contract["module_id"], "plan": contract["contract"]}
            )
        except Exception as exc:
            raise DetailReviewError("AURORA_NATIVE_AUDIT_INVALID", str(exc)) from exc
        if not negative:
            continue
        report["status"] = "MATERIAL_FAIL"
        report["material_failures"] = negative
        failed = {row["check"] for row in negative}
        report["passed_layers"] = [
            key for key in report["passed_layers"] if key not in failed
        ]
        findings.extend({"module_id": report["module_id"], **row} for row in negative)
    if findings:
        result["status"] = "MATERIAL_FAIL"
        result["material_failures"] = [*result["material_failures"], *findings]
        result["passed_layers"] = []
    return result


def project_failed_scope(value: dict[str, Any]) -> dict[str, Any]:
    """Derive duplicate scope metadata from the actual reported presence.

    Only an already failed, evidence-backed report is eligible. Preserve every
    check, comparison boolean, feature, OCR item and material failure verbatim;
    unknown observations and contradictory PASS reports remain invalid.
    """
    from .identity_review import identity_schema

    result = deepcopy(value)
    scopes = identity_schema()["x-presence-to-scope"]
    for report in result.get("module_judgments") or []:
        if (
            not isinstance(report, dict)
            or report.get("status") != "MATERIAL_FAIL"
            or not report.get("material_failures")
        ):
            continue
        presence = (report.get("observations") or {}).get("product_presence_observed")
        identity = report.get("identity_silhouette")
        if (
            isinstance(presence, str)
            and presence in scopes
            and isinstance(identity, dict)
            and identity.get("comparison_scope") in scopes.values()
        ):
            identity["comparison_scope"] = scopes[presence]
    # A failed pair's duplicate aggregate scope follows its two explicit
    # per-screen presence reports too. Preserve all comparisons and evidence.
    reports = result.get("module_judgments") or []
    aggregate = result.get("identity_silhouette")
    if (
        result.get("status") == "MATERIAL_FAIL"
        and result.get("material_failures")
        and isinstance(aggregate, dict)
        and aggregate.get("comparison_scope") in scopes.values()
        and len(reports) == 2
        and all(
            isinstance(row, dict)
            and row.get("status") == "MATERIAL_FAIL"
            and row.get("material_failures")
            and (row.get("observations") or {}).get("product_presence_observed")
            in scopes
            for row in reports
        )
    ):
        priority = identity_schema()["x-scope-priority"]
        aggregate["comparison_scope"] = max(
            (
                scopes[row["observations"]["product_presence_observed"]]
                for row in reports
            ),
            key=priority.index,
        )
    return result


def bind_pixel_metadata(
    value: dict[str, Any], request: Any, canvas_sha256: str, width: int, height: int
) -> dict[str, Any]:
    """Bind runtime metadata from the exact attachments, never from model echoes."""
    from .atomic_review import atomic_context

    result = project_failed_scope(value)
    identities = [result.get("identity_silhouette")]
    identities += [
        report.get("identity_silhouette")
        for report in result.get("module_judgments") or []
        if isinstance(report, dict)
    ]
    for identity in identities:
        if not isinstance(identity, dict):
            continue
        features = identity.get("visible_features")
        if (
            isinstance(features, list)
            and features
            and all(isinstance(item, str) and item.strip() for item in features)
            and any(len(item) < 8 for item in features)
        ):
            # Chinese labels may be concrete in fewer than eight characters.
            # Join exact reported phrases without padding, deletion or inference.
            identity["visible_features"] = ["；".join(features)]
    contracts, slices = atomic_context(request, canvas_sha256)
    result["pair_id"] = request["pair_id"]
    result["module_ids"] = request["module_ids"]
    result["pair_canvas"] = {
        "aspect_ratio": "3:8",
        "width_px": width,
        "height_px": height,
        "decomposition": "TWO_FIXED_SINGLE_SCREEN_WINDOWS",
    }
    binding = result.get("artifact_binding")
    if isinstance(binding, dict):
        binding["image_sha256"] = canvas_sha256
        if isinstance(result.get("ocr_text"), list):
            binding["ocr_text"] = deepcopy(result["ocr_text"])
    reports = result.get("module_judgments")
    if isinstance(reports, list) and len(reports) == 2:
        for report, contract, sliced in zip(reports, contracts, slices, strict=True):
            if (
                not isinstance(report, dict)
                or report.get("module_id") != contract["module_id"]
            ):
                continue
            report["approved_contract_sha256"] = contract["contract_sha256"]
            report["slice_binding"] = {
                "canvas_sha256": canvas_sha256,
                "crop_box": sliced["crop_box"],
            }
            binding = report.get("artifact_binding")
            if isinstance(binding, dict):
                binding["image_sha256"] = sliced["sha256"]
                observed = report.get("observations") or {}
                if isinstance(observed.get("ocr_text"), list):
                    binding["ocr_text"] = deepcopy(observed["ocr_text"])
    return result


def transport_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Do not ask vision to echo server-owned hashes, crop boxes or dimensions."""
    result = deepcopy(schema)
    for key in ("pair_id", "module_ids", "pair_canvas"):
        result.get("properties", {}).pop(key, None)
        if key in result.get("required", []):
            result["required"].remove(key)
    module = result["properties"]["module_judgments"]["items"]
    for key in ("approved_contract_sha256", "slice_binding"):
        module["properties"].pop(key, None)
        module["required"].remove(key)
    for node in (result, module):
        artifact = node["properties"]["artifact_binding"]
        for key in ("image_sha256", "ocr_text"):
            artifact["properties"].pop(key, None)
            if key in artifact.get("required", []):
                artifact["required"].remove(key)
    return result


def repair_findings(value: dict[str, Any]) -> list[dict[str, str]]:
    """Carry each repair instruction once; immutable audits retain full prose."""
    result = []
    seen = set()
    for finding in value.get("material_failures") or []:
        if not isinstance(finding, dict):
            finding = {"visible_evidence": str(finding)}
        evidence = str(finding.get("visible_evidence") or "").strip()
        change = str(finding.get("required_change") or evidence).strip()
        if change and change not in seen:
            result.append(
                {"visible_evidence": evidence or change, "required_change": change}
            )
            seen.add(change)
    return result


def request_context(information: tuple[str, ...]) -> dict[str, Any]:
    """Ordinary legacy product prose is not a website review contract."""
    try:
        value = json.loads(information[0]) if information else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def legacy_prompt(context: dict[str, Any], schema: dict[str, Any]) -> str:
    return (
        """Inspect the attached real product references, both generated fixed
screen slices, and the final full 3:8 candidate canvas. Only product_reference
images establish product truth. Return WEBSITE_LEAN_V1 JSON matching the schema.
For each actual slice report six core checks: identity, facts, legibility,
approved_plan_fidelity, composition_and_continuity, proof_integrity. Each boolean
needs a distinct concrete visible observation. Inspect shape/parts/color against
the source, transcribe actual OCR, check truthful copy, visible approved selling
points, useful visual proof, composition and the intended seam. Report no numerical
aesthetic scores, headline ranks or depth-layer counts. Minor spacing or decorative
preferences are advisory. Missing product, unsupported claim, essential illegibility,
lost screen duty, misleading proof, broken geometry or a content-destroying seam
is material. Never fill an observation from the approved plan.
Preserve exact module order, canvas/slice hashes, crop boxes and contract hashes
from the context. Record artifact_binding and its same raw OCR. \
Both the joint pair checks and each screen use these same six core checks. \
A PASS cannot hide a false check or
material failure. Compare identity only within actual visible scope: full product,
identity token or no visible product, with NOT_APPLICABLE for unseen comparisons.
If an observation is unknown, audit_complete=false and describe uncertainty; it
does not authorize image repair. Brandless means zero independent logos. Only
approved copy may carry product claims; do not reproduce source watermarks.
"""
        + "\nRESPONSE_CONTRACT_JSON="
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        + "\nINPUT_JSON="
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def prompt(context: dict[str, Any], schema: dict[str, Any]) -> str:
    if context.get("review_transport_version") != "WEBSITE_OWNED_METADATA_V2":
        return legacy_prompt(context, schema)
    return (
        """Inspect the attached real product references, both generated fixed
screen slices, and the final full 3:8 candidate canvas. Only product_reference
images establish product truth. Return WEBSITE_LEAN_V1 JSON matching the schema.
For each actual slice report six core checks: identity, facts, legibility,
approved_plan_fidelity, composition_and_continuity, proof_integrity. Each boolean
needs a distinct concrete visible observation. Inspect shape/parts/color against
the source, transcribe actual OCR, check truthful copy, visible approved selling
points, useful visual proof, composition and the intended seam. Report no numerical
aesthetic scores, headline ranks or depth-layer counts. Minor spacing or decorative
preferences are advisory. Missing product, unsupported claim, essential illegibility,
lost screen duty, misleading proof, broken geometry or a content-destroying seam
is material. Never fill an observation from the approved plan.
Preserve exact module order and module IDs. The server binds canvas/slice hashes,
crop boxes, dimensions and contract hashes from the exact attachments; do not
repeat those runtime fields. Transcribe actual OCR once in each observations
object and once for the joint canvas. Record auditor, time and actual visual
inspection evidence in artifact_binding. \
Both the joint pair checks and each screen use these same six core checks. \
A PASS cannot hide a false check or
material failure. Compare identity only within actual visible scope: full product,
identity token or no visible product, with NOT_APPLICABLE for unseen comparisons.
If an observation is unknown, audit_complete=false and describe uncertainty; it
does not authorize image repair. Brandless means zero independent logos. Only
approved copy may carry product claims; do not reproduce source watermarks.
"""
        + "\nRESPONSE_CONTRACT_JSON="
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        + "\nINPUT_JSON="
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def synthetic_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mock-only projection; never called for a real provider response."""
    result = deepcopy(reports)
    aliases = {
        "approved_plan_fidelity": "approved_plan_fidelity",
        "composition_and_continuity": "spatial_structure_match",
    }
    for report in result:
        report[MARKER] = PROFILE
        report["checks"] = {
            key: report["checks"][aliases.get(key, key)] for key in CHECKS
        }
        report["passed_layers"] = list(CHECKS)
        observed = report["observations"]
        report["observations"] = {key: observed[key] for key in OBSERVATIONS}
        report["observations"]["composition_observation"] = (
            "SYNTHETIC_OFFLINE_FIXTURE; no actual composition acceptance"
        )
        report["observations"]["brand_observation"] = (
            "SYNTHETIC_OFFLINE_FIXTURE; no actual brand acceptance"
        )
    return result
