from fastapi import APIRouter, Response

from .auth import SessionDependency, WorkspaceDependency
from .credit_memberships import aware
from .models import JobRecord, ReplicaDocumentRecord, UserRecord, utc_now
from .replica_welcome import campaign_enabled, ensure_grant, newcomer_eligible

router = APIRouter(tags=["promotions"])


@router.get("/api/v1/public/promotions/replica-welcome")
def public_offer(session: SessionDependency, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": campaign_enabled(session)}


@router.get("/api/v1/workspaces/{workspace_id}/promotions/replica-welcome")
def welcome_state(
    workspace: WorkspaceDependency, session: SessionDependency, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    user = session.get(UserRecord, workspace.principal.user_id)
    grant = ensure_grant(session, user) if user else None
    session.commit()
    state = "unavailable"
    if grant and grant.workspace_id == workspace.workspace_id:
        if grant.session_id:
            document = session.get(ReplicaDocumentRecord, grant.session_id)
            jobs = [
                session.get(JobRecord, job_id)
                for job_id in (document.payload.get("job_ids", []) if document else [])
            ]
            state = (
                "used"
                if jobs and all(job and job.status == "completed" for job in jobs)
                else "in_progress"
            )
        else:
            state = (
                "available"
                if campaign_enabled(session)
                and aware(grant.expires_at) > aware(utc_now())
                else "expired"
            )
    else:
        grant = None
    return {
        "enabled": campaign_enabled(session),
        "status": state,
        "newcomer_available": newcomer_eligible(
            session, workspace.principal.user_id, workspace.workspace_id
        ),
        "session_id": grant.session_id if grant else None,
        "expires_at": aware(grant.expires_at).isoformat() if grant else None,
    }
