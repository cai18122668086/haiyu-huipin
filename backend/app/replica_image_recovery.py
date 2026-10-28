"""Bounded GET-only reconciliation of a v14 replica's original invocation."""

from typing import Any

from .models import ReplicaDocumentRecord
from .replica_native import read_json

VERSION = "exact-compact-v14"
MAX_RESUMPTIONS = 2


def job_available(session: Any, job: Any) -> bool:
    if job.type != "reference_remix":
        return False
    document_id = job.configuration.get("replica_session_id")
    if not isinstance(document_id, str) or not document_id:
        return False
    document = session.get(ReplicaDocumentRecord, document_id)
    if document is None or document.workspace_id != job.workspace_id:
        return False
    values = document.payload
    approved = values.get("native_snapshot", {})
    execution = (
        values.get("native_executions", {}).get(job.id, {}).get("snapshot", approved)
    )
    return bool(
        job.id in values.get("job_ids", [])
        and (approved.get("model_protocol") or {}).get("version")
        in {VERSION, "exact-compact-v15"}
        and execution.get("model_protocol") == approved.get("model_protocol")
        and values.get("plan_hash")
        == job.configuration.get("settings", {}).get("replica_plan_hash")
        and values.get("confirmed_instructions_hash")
    )


def retryable(run: Any, error: Exception) -> bool:
    if (getattr(run, "binding", None) or {}).get("version") not in {
        VERSION, "exact-compact-v15"
    }:
        return False
    metrics = getattr(error, "provider_metrics", {})
    if metrics.get("image_recovery", {}).get("can_resume_original") is not True:
        return False
    path = run.root / "invocations.json"
    if not path.is_file():
        return False
    return any(
        row.get("state") == "invoked"
        and row.get("reconciliation_attempts", 0) < MAX_RESUMPTIONS
        for row in read_json(path).values()
    )
