"""Automatic suite submission using the original authenticated confirmation route.

The opt-in is bound by the server to the original submitter. Background recovery
rechecks that account and workspace membership, then reuses plan/source hashes,
quota reservation, idempotency and the original task dispatcher.
"""

from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks, Request

from .auth import _principal_from_user, require_workspace, require_workspace_write
from .models import ReplicaDocumentRecord, UserRecord, utc_now


async def submit_automatic(app: Any, document_id: str, attempt_id: str) -> None:
    from .replica import ConfirmRequest, confirm

    background = BackgroundTasks()
    try:
        with app.state.session_factory() as session:
            row = session.get(ReplicaDocumentRecord, document_id)
            if row is None:
                return
            values = dict(row.payload)
            authority = values.get("_automatic_submission") or {}
            if (
                row.status != "awaiting_confirmation"
                or values.get("auto_start_after_plan") is not True
                or values.get("analysis_attempt_id") != attempt_id
                or values.get("automatic_submission_error")
                or authority.get("version") != "replica-submission-v1"
            ):
                return
            user = session.get(UserRecord, authority.get("actor_user_id"))
            if user is None or user.status != "active":
                raise ValueError("The submitting account is no longer active")
            principal = _principal_from_user(
                user,
                email_verified=authority.get("email_verified") is True,
            )
            workspace_id = row.workspace_id
            # This rolls back the read transaction before the original handler
            # acquires its own row locks and reserves credits.
            workspace = require_workspace_write(
                require_workspace(workspace_id, principal, session)
            )
            request = Request(
                {
                    "type": "http",
                    "app": app,
                    "method": "POST",
                    "path": (
                        f"/api/v1/workspaces/{workspace_id}/replica/"
                        f"sessions/{document_id}/confirm"
                    ),
                    "headers": [],
                    "query_string": b"",
                    "scheme": "https",
                    "server": ("internal", 443),
                    "client": ("internal", 0),
                }
            )
            confirm(
                document_id=document_id,
                payload=ConfirmRequest(plan_hash=values["plan_hash"], instructions=[]),
                request=request,
                background_tasks=background,
                workspace=workspace,
                session=session,
            )
        await background()
    except Exception as error:
        # Keep the audited plan. A quota/auth/dispatch failure must never rerun
        # image analysis or silently bypass the original admission gates.
        with app.state.session_factory() as session:
            row = session.get(ReplicaDocumentRecord, document_id, with_for_update=True)
            if (
                row is not None
                and row.status == "awaiting_confirmation"
                and row.payload.get("analysis_attempt_id") == attempt_id
            ):
                row.payload = {
                    **row.payload,
                    "automatic_submission_error": getattr(
                        error,
                        "message",
                        "生成任务暂未提交成功，请稍后重试或检查账户额度。",
                    ),
                    "automatic_submission_error_code": getattr(
                        error, "code", "REPLICA_AUTOMATIC_SUBMISSION_FAILED"
                    ),
                }
                row.updated_at = utc_now()
                session.commit()
