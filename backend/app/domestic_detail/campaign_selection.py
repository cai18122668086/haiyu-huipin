"""Explicit supplementary review snapshots; never alter original native audit truth."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..aurora_detail_runtime import detail_workspace
from ..models import JobPlanRecord, JobRecord
from .campaign_durability import bytes_once
from .campaign_reports import load_campaign_report
from .delivery import _reject
from .plans import canonical_hash
from .review import approved_review_authority


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _access(path: Path) -> Path:
    if os.name != "nt":
        return path
    prefix = chr(92) * 2 + "?" + chr(92)
    absolute = os.path.abspath(path)
    return Path(absolute if absolute.startswith(prefix) else prefix + absolute)


def _read(root: Path, relative: str) -> bytes:
    path = _access(root / relative)
    root = _access(root)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        _reject("Supplementary evidence escaped its owned workspace")
    return path.read_bytes()


def _summary(
    review_id: str, model: str, report: dict[str, Any], files: Mapping[str, bytes]
) -> dict[str, Any]:
    return {
        "review_job_id": review_id,
        "report_sha256": report["sha256"],
        "model": model,
        "report": report,
        "original_message_state": "PRESERVED_UTF8"
        if "original-message.txt" in files
        else "UNAVAILABLE",
        "files": {
            name: {"sha256": _sha(data), "bytes": len(data)}
            for name, data in sorted(files.items())
        },
    }


def select_campaign_review(
    db: Session,
    source: JobRecord,
    plan: JobPlanRecord,
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    review = db.get(JobRecord, selection.get("review_job_id"))
    if (
        review is None
        or review.workspace_id != source.workspace_id
        or review.type != "domestic_detail_review"
        or review.configuration.get("source_job_id") != source.id
        or review.status not in {"completed", "failed", "cancelled"}
    ):
        _reject("Select a terminal review of this exact detail source")
    intent = review.configuration
    if (
        intent.get("source_plan_id") != plan.id
        or intent.get("source_plan_revision") != plan.revision
        or intent.get("source_context_sha256") != canonical_hash(context)
        or intent.get("approved_authority_sha256")
        != canonical_hash(approved_review_authority(state))
        or intent.get("generation_binding_sha256")
        != context["generation_binding_sha256"]
    ):
        _reject("The selected report belongs to another source or approved revision")
    payload, preview = load_campaign_report(review)
    report = payload["report"]
    if report["sha256"] != selection.get("report_sha256"):
        _reject("The explicitly selected report fingerprint changed")
    frames = [
        (r["asset_id"], r["sha256"])
        for r in payload["request_context"]["source_inventory"]
        if r["role"] == "accepted_detail_screen"
    ]
    if frames != [(r["asset_id"], r["sha256"]) for r in context["screens"]]:
        _reject("The selected report observed another accepted screen order")
    root = detail_workspace(review.id)
    files = {
        "campaign-report.json": _read(root, "campaign-report.json"),
        "request-context.json": _read(root, "campaign-request-context.json"),
        "preview.png": preview,
    }
    message = payload["original_message"]
    if isinstance(message, str):
        files["original-message.txt"] = message.encode("utf-8")
    for name in (
        "campaign-model-result.json",
        "audited-session.json",
        "source-session.json",
    ):
        if (root / name).exists():
            files[name] = _read(root, name)
    for path in sorted((root / "reviews").glob("*")):
        if path.suffix in {".json", ".txt"} and path.is_file():
            files["reviews/" + path.name] = _read(
                root, path.relative_to(root).as_posix()
            )
    for i, row in enumerate(payload["request_context"]["source_inventory"]):
        files[f"inputs/{i:03d}" + Path(row["file"]).suffix] = _read(root, row["file"])
    return _summary(review.id, payload["model"], report, files), files


def preserve_selection(directory: Path, files: Mapping[str, bytes]) -> None:
    for name, data in files.items():
        destination = directory / "supplementary-review" / name
        # Extended paths cover both atomic publication and later frozen reads.
        bytes_once(_access(destination), data)


def frozen_selection(
    directory: Path,
    summary: Mapping[str, Any],
    source_id: str,
    context: Mapping[str, Any],
) -> dict[str, bytes]:
    root = directory / "supplementary-review"
    files = {}
    for name, proof in summary["files"].items():
        data = _read(root, name)
        if len(data) != proof["bytes"] or _sha(data) != proof["sha256"]:
            _reject("Accepted supplementary evidence changed")
        files[name] = data
    report = json.loads(files["campaign-report.json"])
    if (
        report.get("source_job_id") != source_id
        or report.get("source_context_sha256") != canonical_hash(context)
        or report.get("review_job_id") != summary["review_job_id"]
        or report.get("sha256")
        != canonical_hash({k: v for k, v in report.items() if k != "sha256"})
        or report.get("sha256") != summary["report_sha256"]
        or canonical_hash(report) != canonical_hash(summary["report"])
        or canonical_hash(
            _summary(summary["review_job_id"], summary["model"], report, files)
        )
        != canonical_hash(summary)
    ):
        _reject("Accepted supplementary snapshot differs from its original packet")
    return files


def verify_accepted_review_anchor(
    root: Path,
    job: JobRecord,
    plan: JobPlanRecord,
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    fingerprint: str,
) -> None:
    """A mutable SQL pointer cannot replace the original completed human intent."""
    from .delivery import DELIVERY_VERSION

    manifest = _access(Path(state["final"]["manifest"]))
    directory = manifest.parent
    if directory.is_symlink() or not directory.resolve().is_relative_to(
        _access(root / "delivery").resolve()
    ):
        _reject("Accepted delivery receipt escaped its source workspace")
    request_bytes = _read(directory, "request.json")
    request = json.loads(request_bytes)
    response = json.loads(_read(directory, "response.json"))
    binding = request["binding"]
    public = plan.payload["final_manifest"]
    if (
        request.get("version") != DELIVERY_VERSION
        or response.get("version") != DELIVERY_VERSION
        or directory.name != canonical_hash(binding)
        or response.get("sha256")
        != canonical_hash({k: v for k, v in response.items() if k != "sha256"})
        or response.get("sha256") != public.get("native_delivery_receipt_sha256")
        or response.get("binding_sha256") != canonical_hash(binding)
        or response.get("request_sha256") != canonical_hash(request)
        or response.get("request_file_sha256") != _sha(request_bytes)
        or binding.get("job_id") != job.id
        or binding.get("workspace_id") != job.workspace_id
        or binding.get("plan_id") != plan.id
        or binding.get("plan_revision") != plan.revision
        or binding.get("final_review_sha256") != fingerprint
        or canonical_hash({key: binding.get(key) for key in context})
        != canonical_hash(context)
        or response.get("session_after_sha256") != _sha(_read(root, "session.json"))
        or response["native_inspection"]["manifest_file_sha256"]
        != _sha(_read(directory, manifest.name))
        or response["native_inspection"]["manifest_file_sha256"]
        != public.get("native_manifest_sha256")
    ):
        _reject(
            "The selected final packet differs from the originally accepted delivery",
            "NATIVE_DETAIL_FINAL_REVIEW_STALE",
        )
