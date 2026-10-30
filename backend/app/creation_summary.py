"""Workspace-scoped task counters, independent of recent-list pagination."""

from collections import Counter

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from .auth import SessionDependency, WorkspaceDependency
from .models import JobRecord
from .services import job_history_is_available

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["jobs"])


class CreationSummaryResponse(BaseModel):
    active: int
    needs_attention: int
    completed: int
    total: int


@router.get("/creation-summary", response_model=CreationSummaryResponse)
def creation_summary(
    workspace: WorkspaceDependency, session: SessionDependency
) -> dict:
    from .replica import creation_records

    replicas = creation_records(workspace, session)
    linked = {job_id for row in replicas for job_id in row["job_ids"]}
    query = select(JobRecord.status, func.count()).where(
        JobRecord.workspace_id == workspace.workspace_id, job_history_is_available()
    )
    if linked:
        query = query.where(JobRecord.id.not_in(linked))
    counts = Counter(dict(session.execute(query.group_by(JobRecord.status)).all()))
    for row in replicas:
        status = {"analysing": "planning", "generating": "processing"}.get(
            row["status"], row["status"]
        )
        counts[status] += 1
    return {
        "active": sum(
            counts[s]
            for s in (
                "queued",
                "planning",
                "processing",
                "awaiting_confirmation",
                "cancelling",
            )
        ),
        "needs_attention": sum(
            counts[s] for s in ("failed", "partial", "awaiting_final_review")
        ),
        "completed": counts["completed"],
        "total": sum(counts.values()),
    }
