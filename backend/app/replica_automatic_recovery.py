"""Bounded checkpoint recovery, separate from the three visual plan rounds."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sqlalchemy import update

from .auth import _principal_from_user, require_workspace, require_workspace_write
from .models import ReplicaDocumentRecord, UserRecord, utc_now

RECOVERABLE = {
    "REPLICA_SEGMENTATION_INVALID",
    "REPLICA_MODEL_RESPONSE_INVALID",
    "REPLICA_PLANNER_OUTPUT_TRUNCATED",
}
MAX_RECOVERIES = 2


def claim_analysis_recovery(app: Any, document_id: str, attempt_id: str) -> str | None:
    from .replica import _analysis_input_hash
    from .replica_analysis_recovery import (
        document_file,
        prepare_segmentation_retry,
        segmentation_repair_available,
    )
    from .replica_exact_compact import protocol_binding
    from .replica_exact_contracts import canonical_hash

    with app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, document_id, with_for_update=True)
        if row is None or row.status != "failed":
            return None
        values = deepcopy(row.payload)
        authority = values.get("_automatic_submission") or {}
        history = values.get("_automatic_analysis_recoveries", [])
        code = values.get("error_code")
        checkpoint = values.get("_analysis_checkpoint")
        cursor = document_file(checkpoint or {}, "compact-checkpoints.json").get(
            "plan_cursor", {}
        )
        if (
            values.get("analysis_attempt_id") != attempt_id
            or values.get("auto_start_after_plan") is not True
            or values.get("replication_method") != "exact"
            or values.get("job_ids")
            or code not in RECOVERABLE
            or not checkpoint
            or cursor.get("rounds", 0) >= 3
            or checkpoint.get("model_protocol") != protocol_binding()
            or len(history) >= MAX_RECOVERIES
            or any(event["error_code"] == code for event in history)
            or values.get("_analysis_input_hash") != _analysis_input_hash(values)
            or authority.get("version") != "replica-submission-v1"
        ):
            return None
        user = session.get(UserRecord, authority.get("actor_user_id"))
        if user is None or user.status != "active":
            return None
        principal = _principal_from_user(
            user, email_verified=authority.get("email_verified") is True
        )
        require_workspace_write(require_workspace(row.workspace_id, principal, session))
        if code == "REPLICA_SEGMENTATION_INVALID":
            if not segmentation_repair_available(row, _analysis_input_hash(values)):
                return None
            values = prepare_segmentation_retry(values)
        new_attempt = uuid4().hex
        values["_automatic_analysis_recoveries"] = [
            *history,
            {
                "error_code": code,
                "error_message": values.get("error"),
                "error_details": values.get("analysis_error_details"),
                "from_attempt": attempt_id,
                "to_attempt": new_attempt,
                "at": utc_now().isoformat(),
                "checkpoint_sha256": canonical_hash(checkpoint),
                "planning_rounds_reset": False,
                "planning_rounds_before": cursor.get("rounds", 0),
            },
        ]
        lineage = values.get("_analysis_lineage", [])
        if checkpoint not in lineage:
            values["_analysis_lineage"] = [*lineage, checkpoint]
        for key in ("error", "error_code", "analysis_error_details"):
            values.pop(key, None)
        values.update(
            analysis_attempt_id=new_attempt,
            analysis_stage="正在自动重新核对未完成的分析",
        )
        claimed = session.scalar(
            update(ReplicaDocumentRecord)
            .where(
                ReplicaDocumentRecord.id == document_id,
                ReplicaDocumentRecord.status == "failed",
                ReplicaDocumentRecord.payload["analysis_attempt_id"].as_string()
                == attempt_id,
            )
            .values(status="analysing", payload=values, updated_at=utc_now())
            .returning(ReplicaDocumentRecord.id)
        )
        if claimed is None:
            session.rollback()
            return None
        session.commit()
        return new_attempt
