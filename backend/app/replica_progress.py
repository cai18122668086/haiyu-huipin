"""Replica progress is processed work; accepted images and delivery stay distinct."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import JobItemRecord, JobRecord, ReplicaDocumentRecord

TERMINAL = frozenset({"completed", "partial", "failed", "cancelled"})


def native_progress(state: dict[str, Any]) -> dict[str, Any]:
    modules = state.get("modules", [])
    if state.get("review_status") == "NOT_REVIEWED":
        generated = sum(m.get("status") == "GENERATED" for m in modules)
        total = len(modules)
        return {
            "phase": state["state"],
            "post_generation_review": "disabled",
            "total": total,
            "generated": generated,
            "reviewed": 0,
            "accepted": 0,
            "rejected": 0,
            "current_module": min(total, int(state.get("module_index", 0)) + 1),
            "percentage": min(99, 25 + round(70 * generated / max(1, total))),
            "modules": [
                {
                    "id": m["id"],
                    "status": m["status"],
                    "attempts": len(m.get("attempts", [])),
                    "reasons": [],
                }
                for m in modules
            ],
        }
    rows = []
    for module in modules:
        attempts = module.get("attempts", [])
        attempt = attempts[-1] if attempts else {}
        judgment = attempt.get("judgment") or {}
        failures = judgment.get("major_mismatches", [])
        rows.append(
            {
                "id": module.get("id"),
                "status": (
                    module["status"]
                    if module.get("status") in {"COMPLETE", "REJECTED_BEST_EFFORT"}
                    else attempt.get("status", module.get("status", "PENDING"))
                ),
                "attempts": len(attempts),
                "reasons": [str(value)[:600] for value in failures[:4]],
            }
        )
    accepted = sum(row["status"] == "COMPLETE" for row in rows)
    rejected = sum(row["status"] == "REJECTED_BEST_EFFORT" for row in rows)
    reviewed = accepted + rejected
    total = len(rows)
    phase = state.get("state", "READY")
    return {
        "phase": phase,
        "total": total,
        "reviewed": reviewed,
        "accepted": accepted,
        "rejected": rejected,
        "current_module": min(total, int(state.get("module_index", 0)) + 1),
        "percentage": min(99, 25 + round(70 * reviewed / max(1, total))),
        "modules": rows,
    }


def reconcile_document(session: Session, row: ReplicaDocumentRecord) -> bool:
    """Reconcile only current jobs; a failed run is never labelled completed."""
    ids = row.payload.get("job_ids", [])
    if not ids:
        return False
    jobs = session.scalars(
        select(JobRecord).where(
            JobRecord.workspace_id == row.workspace_id, JobRecord.id.in_(ids)
        )
    ).all()
    if row.payload.get("submission_complete") is False and not (
        row.payload.get("native_snapshot")
        and row.payload.get("confirmed_instructions_hash")
        and len(ids) == len(jobs) == 1
        and jobs[0].configuration.get("replica_session_id") == row.id
    ):
        return False
    if len(jobs) != len(ids) or any(job.status not in TERMINAL for job in jobs):
        return False
    statuses = {job.status for job in jobs}
    target = (
        "completed"
        if statuses == {"completed"}
        else "cancelled"
        if statuses == {"cancelled"}
        else "partial"
        if statuses.intersection({"completed", "partial"})
        else "failed"
    )
    changed = row.status != target
    row.status = target
    payload = dict(row.payload)
    if target == "completed":
        payload["progress"] = 100
        payload.pop("error", None)
        payload.pop("error_code", None)
    else:
        item = session.scalar(
            select(JobItemRecord)
            .where(
                JobItemRecord.job_id.in_(ids),
                JobItemRecord.status.in_({"failed", "outcome_unknown"}),
            )
            .order_by(JobItemRecord.ordinal)
            .limit(1)
        )
        message = next((job.error_message for job in jobs if job.error_message), None)
        payload["error"] = (
            message
            or (item.error_message if item else None)
            or (
                "任务已取消。"
                if target == "cancelled"
                else "部分图片未通过验收，请查看逐屏原因。"
            )
        )
        if item and item.error_code:
            payload["error_code"] = item.error_code
    if payload != row.payload:
        row.payload = payload
        changed = True
    return changed


def reconcile_job_document(session: Session, job: JobRecord) -> None:
    document_id = job.configuration.get("replica_session_id")
    if not document_id:
        return
    row = session.get(ReplicaDocumentRecord, document_id, with_for_update=True)
    if row is not None and row.workspace_id == job.workspace_id:
        reconcile_document(session, row)
