"""Lossless pixel observations and immutable, single-dispatch vision receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

from ..config import default_skill_root
from .plans import canonical_hash

PAIR_CHECKS = (
    "identity",
    "facts",
    "pair_duties",
    "visual_proof",
    "legibility",
    "copy_binding",
    "internal_seam",
    "spatial_integrity",
    "commercial_expression",
)
IDENTITY_KEYS = ("shape_consistent", "colorway_consistent", "texture_consistent")
AUDIT_PROFILES = frozenset({"aurora_detail_pair_audit", "aurora_detail_identity_audit"})


class DetailReviewError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _invalid(path: str, reason: str) -> NoReturn:
    raise DetailReviewError("AURORA_NATIVE_AUDIT_INVALID", f"{path}: {reason}")


def _authority_invalid(path: str) -> NoReturn:
    raise DetailReviewError(
        "AURORA_NATIVE_REVIEW_AUTHORITY_INVALID",
        path + ": the exact compiled and approved review authority is required",
    )


def approved_review_authority(session: Mapping[str, Any]) -> dict[str, Any]:
    """Project approved native truth; raw settings never supply review facts."""
    expected_version = (
        (default_skill_root("aurora-domestic-ecommerce-detail-page") / "VERSION")
        .read_text(encoding="utf-8")
        .strip()
    )
    if session.get("version") != expected_version:
        _authority_invalid("/version")
    for key in ("input", "identity", "plan", "approval_document"):
        if not isinstance(session.get(key), Mapping):
            _authority_invalid("/" + key)
    native_input, identity, plan = (
        session["input"],
        session["identity"],
        session["plan"],
    )
    facts = native_input.get("verified_facts")
    active = session.get("planning_fact_ids")
    if not isinstance(facts, list) or not isinstance(active, list):
        _authority_invalid("/verified_facts or /planning_fact_ids")
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not isinstance(row.get("text"), str)
        or not row["text"].strip()
        for row in facts
    ):
        _authority_invalid("/verified_facts")
    fact_ids = [row["id"] for row in facts]
    if (
        any(not isinstance(item, str) for item in active)
        or len(fact_ids) != len(set(fact_ids))
        or len(active) != len(set(active))
        or not set(active) <= set(fact_ids)
    ):
        _authority_invalid("/planning_fact_ids")
    conflicts = native_input.get("source_conflicts", [])
    if not isinstance(conflicts, list):
        _authority_invalid("/source_conflicts")
    rejected: set[str] = set()
    retained: set[str] = set()
    for conflict in conflicts:
        if not isinstance(conflict, dict) or conflict.get("status") != "RESOLVED":
            _authority_invalid("/source_conflicts/status")
        resolution = conflict.get("resolution")
        if (
            not isinstance(resolution, dict)
            or not isinstance(resolution.get("rejected_fact_ids"), list)
            or not isinstance(resolution.get("retained_fact_ids"), list)
            or not resolution.get("confirmation_text")
        ):
            _authority_invalid("/source_conflicts/resolution")
        rejected.update(resolution["rejected_fact_ids"])
        retained.update(resolution["retained_fact_ids"])
    if rejected & retained or set(fact_ids) - set(active) != rejected:
        _authority_invalid("/planning_fact_ids/resolution")
    coverage = (plan.get("output_strategy") or {}).get("claim_coverage")
    if not isinstance(coverage, list) or any(
        not isinstance(row, dict)
        or row.get("disposition") not in {"LEAD", "SUPPORT", "DEFERRED"}
        for row in coverage
    ):
        _authority_invalid("/claim_coverage")
    coverage_ids = [row.get("claim_id") for row in coverage]
    if len(coverage_ids) != len(set(coverage_ids)) or set(coverage_ids) != set(active):
        _authority_invalid("/claim_coverage/claim_id")
    by_id = {row["claim_id"]: row for row in coverage}
    ledger = [
        {
            "fact": deepcopy(fact),
            "approved_disposition": by_id[fact["id"]]["disposition"]
            if fact["id"] in by_id
            else "EXCLUDED",
            "claim_coverage": deepcopy(by_id.get(fact["id"])),
        }
        for fact in facts
    ]
    expressed = [
        row["fact"]
        for row in ledger
        if row["approved_disposition"] in {"LEAD", "SUPPORT"}
    ]
    value = {
        "version": "native-review-authority-v1",
        "native_version": session["version"],
        "locked_identity": deepcopy(identity),
        "original_fact_ledger": ledger,
        "active_fact_ids": list(active),
        "expressible_fact_ids": [row["id"] for row in expressed],
        "product_name": identity.get("canonical_name"),
        "product_information": [row["text"] for row in expressed],
        "source_conflicts": deepcopy(conflicts),
        "compiled_input_sha256": canonical_hash(native_input),
        "compiled_identity_sha256": canonical_hash(identity),
        "compiled_plan_sha256": canonical_hash(plan),
        "native_approval_document_sha256": session["approval_document"].get("sha256"),
    }
    return require_review_authority({**value, "sha256": canonical_hash(value)})


def require_review_authority(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("version") != "native-review-authority-v1"
    ):
        _authority_invalid("/review_authority")
    value = deepcopy(dict(value))
    if value.get("sha256") != canonical_hash(
        {key: item for key, item in value.items() if key != "sha256"}
    ):
        _authority_invalid("/review_authority/sha256")
    for key in (
        "compiled_input_sha256",
        "compiled_identity_sha256",
        "compiled_plan_sha256",
        "native_approval_document_sha256",
    ):
        if not isinstance(value.get(key), str) or not re.fullmatch(
            r"[0-9a-f]{64}", value[key]
        ):
            _authority_invalid("/review_authority/" + key)
    identity = value.get("locked_identity")
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("canonical_name"), str)
        or not identity["canonical_name"].strip()
        or value.get("product_name") != identity["canonical_name"]
    ):
        _authority_invalid("/review_authority/locked_identity")
    ledger = value.get("original_fact_ledger")
    if not isinstance(ledger, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("fact"), dict)
        or not isinstance(row["fact"].get("id"), str)
        or not isinstance(row["fact"].get("text"), str)
        or row.get("approved_disposition")
        not in {"LEAD", "SUPPORT", "DEFERRED", "EXCLUDED"}
        for row in ledger
    ):
        _authority_invalid("/review_authority/original_fact_ledger")
    expressed = [
        row["fact"]
        for row in ledger
        if row["approved_disposition"] in {"LEAD", "SUPPORT"}
    ]
    if value.get("expressible_fact_ids") != [
        row["id"] for row in expressed
    ] or value.get("product_information") != [row["text"] for row in expressed]:
        _authority_invalid("/review_authority/product_information")
    return value


def _pair_observation(
    raw: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    image_sha256: str,
    width: int,
    height: int,
    identity_pending: bool = False,
) -> dict[str, Any]:
    """Keep explicit observations; never invent a missing identity comparison."""
    value = deepcopy(dict(raw))
    if request.get("review_profile") == "WEBSITE_LEAN_V1":
        from .website_review import bind_pixel_metadata

        value = bind_pixel_metadata(value, request, image_sha256, width, height)
    from .revision_review import validate_local_observation

    local_findings = validate_local_observation(
        value,
        request,
        image_sha256=image_sha256,
        width=width,
        height=height,
    )
    failures = value.get("material_failures")
    if isinstance(failures, list):
        value["material_failures"] = [
            {"visible_evidence": item} if isinstance(item, str) else item
            for item in failures
        ]
    schema_path = default_skill_root("aurora-domestic-ecommerce-detail-page") / (
        "schemas/pair-judgment-website-v1.schema.json"
        if request.get("review_profile") == "WEBSITE_LEAN_V1"
        else "schemas/pair-judgment.schema.json"
    )
    schema = json.loads(schema_path.read_bytes())
    if identity_pending:
        # This phase validates all actual module evidence and bindings, but is
        # never returned as an accepted judgment. Only aggregate identity may
        # still be asked once about the same pixels.
        schema["required"].remove("identity_silhouette")
        schema["properties"].pop("identity_silhouette")
    if request.get("review_profile") == "WEBSITE_LEAN_V1":
        from .website_review import OBSERVATIONS

        allowed = {*OBSERVATIONS, "composition_observation", "brand_observation"}
        for index, report in enumerate(value.get("module_judgments") or []):
            observed = report.get("observations") if isinstance(report, dict) else None
            if not isinstance(observed, dict):
                continue
            alias = observed.get("observed_complete_product_role")
            if alias is not None and alias != observed.get("product_presence_observed"):
                _invalid(
                    f"/module_judgments/{index}/observations", "product roles disagree"
                )
            # Auxiliary legacy metadata is retained in the immutable original
            # reply. It does not expand the six-check website rendering contract.
            report["observations"] = {k: v for k, v in observed.items() if k in allowed}
    resolver = RefResolver(schema_path.as_uri(), schema)
    errors = sorted(
        Draft202012Validator(
            schema, resolver=resolver, format_checker=FormatChecker()
        ).iter_errors(value),
        key=lambda error: str(list(error.absolute_path)),
    )
    if errors:
        error = errors[0]
        parts = [str(part) for part in error.absolute_path]
        if error.validator == "required" and isinstance(error.instance, dict):
            parts.extend(
                [name for name in error.validator_value if name not in error.instance][
                    :1
                ]
            )
        path = "/" + "/".join(parts)
        _invalid(path, "missing, unknown or incorrectly typed pixel observation")
    if value["pair_id"] != request.get("pair_id"):
        _invalid("/pair_id", "does not identify the attached candidate request")
    if value["module_ids"] != request.get("module_ids"):
        _invalid("/module_ids", "does not preserve the approved upper/lower order")
    coverage = value["screen_coverage"]
    if [item["module_id"] for item in coverage] != request.get("module_ids"):
        _invalid("/screen_coverage", "must report both actual windows in order")
    canvas = value["pair_canvas"]
    if (canvas["width_px"], canvas["height_px"]) != (width, height):
        _invalid("/pair_canvas", "dimensions differ from the attached raster")
    binding = value["artifact_binding"]
    if binding["image_sha256"].lower() != image_sha256.lower():
        _invalid("/artifact_binding/image_sha256", "audited raster hash differs")
    for key in ("ocr_text", "unauthorized_ocr_text", "forbidden_text_hits"):
        if not isinstance(value.get(key), list) or any(
            not isinstance(item, str) for item in value[key]
        ):
            _invalid("/" + key, "must explicitly report the observed text list")
    if value["ocr_text"] != binding["ocr_text"]:
        _invalid("/artifact_binding/ocr_text", "disagrees with the raw OCR list")
    if binding["visual_inspection_output"] in (None, "", {}, []):
        _invalid("/artifact_binding/visual_inspection_output", "inspection is absent")
    checks = value["checks"]
    website = request.get("review_profile") == "WEBSITE_LEAN_V1"
    required_checks: tuple[str, ...] = PAIR_CHECKS
    if website:
        from .website_review import CHECKS

        required_checks = CHECKS
    if any(name not in checks for name in required_checks):
        _invalid("/checks", "all explicit pair observations are required")
    seam = value["internal_seam_evidence"]
    expected_method = (request.get("pair_render") or {}).get("internal_seam_method_id")
    if not expected_method or seam["method_id"] != expected_method:
        _invalid(
            "/internal_seam_evidence/method_id", "differs from the approved method"
        )
    for key in ("source_region", "target_region", "carrier", "geometry"):
        if len(seam[key].strip()) < 4:
            _invalid(
                "/internal_seam_evidence/" + key, "needs an actual pixel description"
            )
    from .atomic_review import atomic_context

    contracts, slices = atomic_context(request, image_sha256)
    if website:
        from .website_review import project_native_negative_decisions

        value = project_native_negative_decisions(value, contracts)
    reports = value["module_judgments"]
    if [row["module_id"] for row in reports] != request["module_ids"]:
        _invalid("/module_judgments", "must preserve both actual slice IDs in order")
    for index, (report, contract, sliced) in enumerate(
        zip(reports, contracts, slices, strict=True)
    ):
        prefix = f"/module_judgments/{index}"
        if report["approved_contract_sha256"] != contract["contract_sha256"]:
            _invalid(
                prefix + "/approved_contract_sha256", "differs from the approved module"
            )
        if report["artifact_binding"]["image_sha256"] != sliced["sha256"] or report[
            "slice_binding"
        ] != {"canvas_sha256": image_sha256, "crop_box": sliced["crop_box"]}:
            _invalid(
                prefix + "/slice_binding", "differs from its actual fixed native window"
            )
        if report["artifact_binding"]["ocr_text"] != report["observations"]["ocr_text"]:
            _invalid(
                prefix + "/artifact_binding/ocr_text",
                "disagrees with the raw atomic OCR",
            )
        if report["artifact_binding"]["visual_inspection_output"] in (None, "", {}, []):
            _invalid(
                prefix + "/artifact_binding/visual_inspection_output",
                "inspection is absent",
            )
        observed_negative = any(
            row["pass"] is False for row in report["checks"].values()
        ) or any(report["identity_silhouette"][key] is False for key in IDENTITY_KEYS)
        if report["status"] == "PASS" and (
            observed_negative or report["material_failures"]
        ):
            _invalid(
                prefix + "/status", "PASS contradicts atomic negative observations"
            )
        if report["status"] == "MATERIAL_FAIL" and not report["material_failures"]:
            _invalid(
                prefix + "/material_failures", "needs explicit visible failure evidence"
            )
    if value["status"] == "PASS" and any(
        report["status"] != "PASS" for report in reports
    ):
        _invalid("/status", "PASS contradicts a failing atomic module")
    information = value.get("information_audit")
    if not isinstance(information, dict):
        if not website:
            _invalid(
                "/information_audit", "explicit information observations are required"
            )
        information = {}
    negative = (
        any(checks[name]["pass"] is False for name in required_checks)
        or any(
            (value.get("identity_silhouette") or {}).get(name) is False
            for name in IDENTITY_KEYS
        )
        or seam["hard_cut_absent"] is False
        or seam["method_specific_pass"] is False
        or any(
            not row["duty_visible"] or not row["selling_points_visible"]
            for row in coverage
        )
        or (
            not website
            and (
                any(
                    information[name] is False
                    for name in (
                        "new_information_visible",
                        "hero_subject_clear",
                        "purchase_judgment_enabled",
                        "detail_page_not_posterized",
                    )
                )
                or information["repetition_without_progression"] is True
                or information["co_equal_hero_detected"] is True
            )
        )
        or bool(value["unauthorized_ocr_text"] or value["forbidden_text_hits"])
    )
    if value["status"] == "PASS" and (negative or value["material_failures"]):
        _invalid("/status", "PASS contradicts explicit negative observations")
    if (
        value["status"] == "MATERIAL_FAIL"
        and not value["material_failures"]
        and all(report["status"] == "PASS" for report in reports)
        and not local_findings
    ):
        _invalid(
            "/material_failures", "a paid image repair needs a visible material finding"
        )
    return value


def normalize_pair_observation(
    raw: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    image_sha256: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    return _pair_observation(
        raw, request, image_sha256=image_sha256, width=width, height=height
    )


def validate_pair_before_identity(
    raw: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    image_sha256: str,
    width: int,
    height: int,
) -> None:
    """Validate the non-aggregate evidence; this grants no acceptance or repair."""
    _pair_observation(
        raw,
        request,
        image_sha256=image_sha256,
        width=width,
        height=height,
        identity_pending=True,
    )


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class ReviewStore:
    root: Path
    binding: Mapping[str, Any]

    @property
    def sha256(self) -> str:
        return canonical_hash(self.binding)

    @property
    def stem(self) -> Path:
        key = base64.urlsafe_b64encode(bytes.fromhex(self.sha256)).decode().rstrip("=")
        return self.root / key

    @property
    def request_path(self) -> Path:
        return self.stem.with_suffix(".request.json")

    @property
    def response_path(self) -> Path:
        return self.stem.with_suffix(".response.json")

    def completed(self) -> dict[str, Any] | None:
        if not self.request_path.exists():
            for path in self.root.glob("*.request.json"):
                old = json.loads(path.read_bytes()).get("binding")
                if not isinstance(old, dict):
                    raise DetailReviewError(
                        "AURORA_NATIVE_REVIEW_CHANGED",
                        "Existing review request is invalid",
                    )
                if all(
                    old.get(key) == self.binding.get(key)
                    for key in (
                        "phase",
                        "render_token",
                        "native_request_sha256",
                        "candidate_sha256",
                    )
                ):
                    raise DetailReviewError(
                        "AURORA_NATIVE_REVIEW_BINDING_CHANGED",
                        (
                            "Reconcile the existing review before changing "
                            "code, model or sources"
                        ),
                    )
            return None
        if (
            canonical_hash(json.loads(self.request_path.read_bytes()).get("binding"))
            != self.sha256
        ):
            raise DetailReviewError(
                "AURORA_NATIVE_REVIEW_CHANGED", "Review request changed"
            )
        if not self.response_path.exists():
            raise DetailReviewError(
                "AURORA_NATIVE_REVIEW_OUTCOME_UNKNOWN",
                "Reconcile the original vision request before another paid review",
            )
        response = json.loads(self.response_path.read_bytes())
        result = response.get("result")
        if (
            not isinstance(result, dict)
            or response.get("binding_sha256") != self.sha256
            or response.get("result_sha256") != canonical_hash(result)
            or canonical_hash(response.get("response"))
            != canonical_hash(result.get("payload"))
        ):
            raise DetailReviewError(
                "AURORA_NATIVE_REVIEW_CHANGED", "Raw review receipt changed"
            )
        raw = response.get("raw_reply")
        original = result.get("audit_raw_content")
        if isinstance(original, str) != isinstance(raw, dict):
            raise DetailReviewError(
                "AURORA_NATIVE_REVIEW_CHANGED", "Raw reply receipt is inconsistent"
            )
        if isinstance(raw, dict):
            content = self.stem.with_suffix(".reply.txt").read_bytes()
            if (
                hashlib.sha256(content).hexdigest() != raw.get("sha256")
                or content != str(original).encode("utf-8")
                or len(content) != raw.get("bytes")
            ):
                raise DetailReviewError(
                    "AURORA_NATIVE_REVIEW_CHANGED", "Raw reply bytes changed"
                )
        return result

    def begin(self) -> None:
        try:
            _write_once(self.request_path, {"binding": dict(self.binding)})
        except FileExistsError as exc:
            raise DetailReviewError(
                "AURORA_NATIVE_REVIEW_ALREADY_DISPATCHED",
                "This exact vision request was already dispatched",
            ) from exc

    def finish(self, result: Mapping[str, Any]) -> None:
        raw = result.get("audit_raw_content")
        raw_receipt = None
        if isinstance(raw, str):
            content = raw.encode("utf-8")
            with self.stem.with_suffix(".reply.txt").open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            raw_receipt = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        _write_once(
            self.response_path,
            {
                "binding_sha256": self.sha256,
                "result": dict(result),
                "result_sha256": canonical_hash(result),
                "response": result.get("payload"),
                "raw_reply": raw_receipt,
                "raw_reply_state": "original_message_utf8"
                if raw_receipt
                else "predecoded_only",
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
