"""An explicit, free replacement for a failed trial with no delivered images."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    JobAssetRecord,
    JobRecord,
    ReplicaDocumentRecord,
    ReplicaWelcomeGrantRecord,
)


def available(
    session: Session, document: ReplicaDocumentRecord, job: JobRecord
) -> bool:
    if (
        document.kind != "session"
        or not document.payload.get("use_welcome_trial")
        or document.payload.get("replica_speed") != "fast"
        or job.type != "reference_remix"
        or job.status not in {"failed", "cancelled"}
        or job.workspace_id != document.workspace_id
        or job.configuration.get("replica_session_id") != document.id
        or job.id not in document.payload.get("job_ids", [])
        or job.succeeded_item_count
        or job.expected_item_count < 1
    ):
        return False
    grant = session.get(ReplicaWelcomeGrantRecord, document.created_by)
    if (
        grant is None
        or grant.workspace_id != job.workspace_id
        or grant.session_id != document.id
        or grant.source_job_id != (job.root_job_id or job.id)
    ):
        return False
    delivered = session.scalar(
        select(JobAssetRecord.id)
        .where(
            JobAssetRecord.job_id == job.id,
            JobAssetRecord.direction == "output",
            JobAssetRecord.is_deliverable.is_(True),
        )
        .limit(1)
    )
    attempts = (
        session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(JobRecord.root_job_id == grant.source_job_id)
        )
        or 0
    )
    return delivered is None and attempts < 3
