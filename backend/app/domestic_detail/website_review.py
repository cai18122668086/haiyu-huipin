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
        negative = deepcopy(negative)
        for finding in negative:
            evidence = finding.get("visible_evidence")
            if isinstance(evidence, str) and evidence.strip() and len(evidence) < 8:
                # Native computes literal short OCR tokens as negative evidence.
                # Describe the failing check without changing that token or any
                # decision. Otherwise the failure itself violates the pinned
                # eight-character diagnostic schema and crashes the handoff.
                finding["visible_evidence"] = (
                    f"Native {finding['check']} observation: {evidence}"
                )
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
        # One relay emits this empty annotation beside its actual boolean.
        # Keep any substantive or conflicting note for strict schema rejection.
        if identity.get("colorway_consistent_note") == "":
            del identity["colorway_consistent_note"]
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


def repair_findings(
    value: dict[str, Any], modules: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Carry each repair instruction once; immutable audits retain full prose."""
    result = []
    seen = set()
    observations = {
        row["module_id"]: row["observations"]
        for row in value.get("module_judgments") or []
    }
    roles = {row["id"]: row["plan"].get("complete_product_role") for row in modules}
    complete_roles = {"DOMINANT", "SUPPORTING"}
    known_roles = complete_roles | {"IDENTITY_TOKEN_ONLY", "ABSENT"}
    for finding in value.get("material_failures") or []:
        if not isinstance(finding, dict):
            finding = {"visible_evidence": str(finding)}
        evidence = str(finding.get("visible_evidence") or "").strip()
        change = str(finding.get("required_change") or evidence).strip()
        module_id = finding.get("module_id")
        observed = observations.get(module_id, {})
        role = roles.get(module_id)
        presence = observed.get("product_presence_observed")
        if (
            finding.get("check") == "approved_plan_fidelity"
            and evidence == observed.get("composition_observation")
            and role in known_roles
            and presence in known_roles
            and (role in complete_roles) != (presence in complete_roles)
        ):
            instruction = (
                "请按批准的主次布局加入完整原图商品，并保留要求的细节。"
                if role in complete_roles
                else "请按批准的范围展示，移除完整商品视图。"
            )
            change = (
                f"{module_id} 完整商品出镜要求为 {role}，"
                f"实际观察为 {presence}。{instruction}"
            )
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


def _owned_metadata_prompt(context: dict[str, Any], schema: dict[str, Any]) -> str:
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


# Never change a published prompt version: recovery verifies its exact bytes.
_SOURCE_COMPARISON_PROMPT_V1 = """SOURCE COMPARISON REVIEW V1
When source_candidate_comparison images are attached, LEFT SOURCE contains
original photographed product pixels and RIGHT CANDIDATE is the actual final
destination crop at the same scale. Use these actual pixel pairs to inspect
small printing and fittings; they are inspection evidence, never delivery
contact sheets or replacement authority. Inspect the full canvas and both
fixed slices independently for completeness, layout, leakage and approved copy.
Return every required root field and exactly two complete module_judgments in
the supplied module order, including when status is MATERIAL_FAIL.
Each module must contain all six checks and its actual observations. An aggregate
failure summary alone is incomplete; preserve negative findings in complete JSON.
"""


_SURFACE_PRINT_REVIEW = """SOURCE PRINT AND ADDED COPY REVIEW V2
Read the full canvas and each slice. For every visible inscription, determine
its physical location by direct comparison of the actual source and candidate.
In observations.ocr_text and joint ocr_text transcribe ALL independently laid
out marketing text, labels, watermarks, captions and marks outside the product.
In identity evidence describe and transcribe original printing on the photographed
product, identifying the specific surface and location on BOTH sides of each
comparison. Inspect every visible design. Only printing verified at its original
location on the same subject belongs in this identity evidence. Reject altered,
replaced, missing or newly introduced product printing under identity or facts.
If provenance cannot be determined, report uncertainty. Unapproved overlaid
copy, logos and off-product duplicates remain material even if their text also
appears on a genuine product. Never omit uncertain lettering to obtain a pass.
"""


def prompt(context: dict[str, Any], schema: dict[str, Any]) -> str:
    base = _owned_metadata_prompt(context, schema)
    version = context.get("review_prompt_version")
    if version is None:
        return base
    if version in {"SOURCE_COMPARISON_V3", "SOURCE_COMPARISON_V4"}:
        pair_scope = (
            "MIXED SCREEN SCOPE V4. Inspect each screen independently. A large crop "
            "is still only a detail if part of the photographed product is missing. "
            "Report DOMINANT or SUPPORTING only for an actually complete product. "
            "Then set the pair comparison_scope to the broadest scope actually seen "
            "on either screen: VISIBLE_COMPLETE_PRODUCT, then VISIBLE_IDENTITY_TOKEN, "
            "then NO_VISIBLE_PRODUCT. A pair with one complete view and one detail "
            "uses VISIBLE_COMPLETE_PRODUCT. Keep every negative finding.\n"
            if version == "SOURCE_COMPARISON_V4" else ""
        )
        return (
            pair_scope
            + "EXTERNAL COPY INSPECTION V3. For joint and per-screen ocr_text read "
            "ONLY "
            "the separately attached external-copy-inspection.png (screen 1 left, "
            "screen 2 right). Ignore its English inspection headers. It contains "
            "actual "
            "final pixels with verified opaque source subjects masked gray. Transcribe "
            "all visible editorial copy literally without annotations or commentary. "
            "Any unauthorized text outside the photographed product must still fail. "
            "Use the unmasked original photo, full canvas, fixed slices and pixel "
            "pairs "
            "for identity, all on-product text, layout, visual proof and continuity. "
            "Record photographed inscriptions in identity evidence.\n"
            + _SURFACE_PRINT_REVIEW
            + _SOURCE_COMPARISON_PROMPT_V1
            + base
        )
    if version == "SOURCE_COMPARISON_V2":
        return _SURFACE_PRINT_REVIEW + _SOURCE_COMPARISON_PROMPT_V1 + base
    if version != "SOURCE_COMPARISON_V1":
        raise ValueError("Unknown immutable native review prompt version")
    return _SOURCE_COMPARISON_PROMPT_V1 + base


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
