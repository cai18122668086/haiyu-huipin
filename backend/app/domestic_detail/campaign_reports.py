"""Read an original campaign report and exact model-seen bytes without new calls."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..aurora_detail_runtime import detail_workspace
from ..models import JobRecord
from .campaign_durability import JOURNAL, load_result
from .campaign_materialization import validate_campaign_preview
from .delivery import _json, _reject, _stable_session
from .planner_dispatch import validate_dispatch_result
from .plans import canonical_hash
from .review import ReviewStore


def _owned_bytes(root: Path, relative: str) -> bytes:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        _reject("Original campaign report evidence escaped its workspace")
    return path.read_bytes()


def load_campaign_report(job: JobRecord) -> tuple[dict[str, Any], bytes]:
    if job.type != "domestic_detail_review":
        _reject("This task is not an optional campaign review")
    root = detail_workspace(job.id)
    report = _json(root / "campaign-report.json")
    intent = job.configuration
    if (
        report.get("sha256")
        != canonical_hash({k: v for k, v in report.items() if k != "sha256"})
        or report.get("review_job_id") != job.id
        or report.get("source_job_id") != intent["source_job_id"]
        or report.get("intent_sha256") != intent["intent_sha256"]
        or report.get("source_context_sha256") != intent["source_context_sha256"]
    ):
        _reject("Original campaign report binding changed")
    context = _json(root / "campaign-request-context.json")
    original = _json(root / "source-session.json")
    if (
        canonical_hash(context) != report["request_context_sha256"]
        or context.get("source_context_sha256") != intent["source_context_sha256"]
        or _stable_session(original) != intent["source_native_truth_sha256"]
        or canonical_hash(context.get("approved_authority"))
        != intent["approved_authority_sha256"]
        or canonical_hash(context.get("approved_plan"))
        != canonical_hash(original["plan"])
    ):
        _reject("Original campaign source or model request changed")
    for frame in context["source_inventory"]:
        payload = _owned_bytes(root, frame["file"])
        if (
            len(payload) != frame["bytes"]
            or hashlib.sha256(payload).hexdigest() != frame["sha256"]
        ):
            _reject("An original model-seen raster changed")
    preview = _owned_bytes(root, "long-page.png")
    if hashlib.sha256(preview).hexdigest() != report["preview_sha256"]:
        _reject("Original assembled preview changed")
    validate_campaign_preview(
        preview,
        [
            _owned_bytes(root, row["file"])
            for row in context["source_inventory"]
            if row["role"] == "accepted_detail_screen"
        ],
    )
    requests = list((root / "reviews").glob("*.request.json"))
    if len(requests) != 1:
        _reject("A campaign-review intent requires one original model request")
    path = requests[0]
    binding = _json(path)["binding"]
    store = ReviewStore(path.parent, binding)
    if (
        store.request_path != path
        or hashlib.sha256(path.read_bytes()).hexdigest()
        != report["request_file_sha256"]
        or binding.get("render_token") != intent.get("original_review_job_id", job.id)
        or binding.get("native_request_sha256")
        != intent.get("original_review_intent_sha256", intent["intent_sha256"])
        or binding.get("candidate_sha256") != report["preview_sha256"]
        or binding.get("request_context_sha256") != canonical_hash(context)
        or binding["dispatch_route"]["sha256"] != intent["dispatch_route_sha256"]
    ):
        _reject("Original campaign request or selected dispatch changed")
    journal_sha = report.get("model_result_journal_sha256")
    if intent.get("original_review_job_id"):
        expected_origin = {
            "job_id": intent["original_review_job_id"],
            "intent_sha256": intent["original_review_intent_sha256"],
            "journal_file_sha256": intent["original_result_journal_sha256"],
        }
        if (
            report.get("original_review") != expected_origin
            or journal_sha != intent["original_result_journal_sha256"]
        ):
            _reject("Recovered report lost its exact original paid-request provenance")
    if journal_sha is not None:
        if hashlib.sha256((root / JOURNAL).read_bytes()).hexdigest() != journal_sha:
            _reject("Original model result journal changed")
        load_result(store)
    result = None
    if store.response_path.exists():
        if (
            hashlib.sha256(store.response_path.read_bytes()).hexdigest()
            != report["response_file_sha256"]
        ):
            _reject("Original campaign model response changed")
        result = store.completed()
        if result is not None:
            validate_dispatch_result(result, binding["dispatch_route"])
    elif (
        report.get("response_file_sha256") is not None
        or report.get("native_gate_accepted") is True
    ):
        _reject("The original campaign response is missing")
    if report.get("native_gate_accepted") is True:
        from .campaign_protocol import validate_campaign_observation

        if result is None:
            _reject("Native campaign acceptance needs its original response")
        judgment = validate_campaign_observation(
            result["payload"], context, original_text=result.get("audit_raw_content")
        )
        saved = _json(root / "audited-session.json")
        current = _json(root / "session.json")
        if (
            hashlib.sha256((root / "audited-session.json").read_bytes()).hexdigest()
            != report["native_session_sha256"]
            or canonical_hash(saved.get("long_page_audit")) != canonical_hash(judgment)
            or canonical_hash(report.get("judgment")) != canonical_hash(judgment)
        ):
            _reject("Native campaign acceptance differs from original observations")
        controls = {"state", "events", "updated_at", "final"}
        if canonical_hash(
            {k: v for k, v in saved.items() if k not in controls}
        ) != canonical_hash({k: v for k, v in current.items() if k not in controls}):
            _reject("The native reviewed campaign changed after acceptance")
    return {
        "report": report,
        "request_context": context,
        "original_message": result.get("audit_raw_content") if result else None,
        "original_message_state": "PRESERVED_UTF8"
        if result and isinstance(result.get("audit_raw_content"), str)
        else "UNAVAILABLE",
        "model": intent["planner_model"],
        "source_job_id": intent["source_job_id"],
        "scope": "OPTIONAL_CAMPAIGN_REVIEW_ONLY",
    }, preview
