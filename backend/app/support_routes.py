"""Task-linked customer assistance on Huipin's existing account system."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .admin_auth import AdminDependency
from .auth import SessionDependency, WorkspaceDependency
from .errors import ApiError
from .models import AuditEventRecord, JobRecord, SupportCaseRecord, WorkspaceRecord
from .services import new_id, require_idempotency_key

router = APIRouter(tags=["support"])


class SupportCreate(BaseModel):
    issue: str = Field(min_length=3, max_length=4000)
    job_id: str | None = Field(default=None, max_length=40)


class SupportUpdate(BaseModel):
    status: Literal["open", "in_progress", "resolved"]
    resolution: str = Field(default="", max_length=4000)


class SupportResponse(BaseModel):
    id: str
    workspace_id: str
    job_id: str | None
    issue: str
    status: str
    resolution: str

    model_config = {"from_attributes": True}


@router.get(
    "/api/v1/workspaces/{workspace_id}/support-cases",
    response_model=list[SupportResponse],
)
def list_cases(workspace: WorkspaceDependency, session: SessionDependency) -> list:
    return list(
        session.scalars(
            select(SupportCaseRecord)
            .where(SupportCaseRecord.workspace_id == workspace.workspace_id)
            .order_by(SupportCaseRecord.created_at.desc())
            .limit(100)
        )
    )


@router.post(
    "/api/v1/workspaces/{workspace_id}/support-cases",
    response_model=SupportResponse,
    status_code=201,
)
def create_case(
    body: SupportCreate,
    workspace: WorkspaceDependency,
    session: SessionDependency,
    key: Annotated[str, Header(alias="Idempotency-Key")],
) -> SupportCaseRecord:
    request_key = require_idempotency_key(key)
    issue = body.issue.strip()
    if len(issue) < 3:
        raise ApiError(422, "SUPPORT_ISSUE_REQUIRED", "请描述遇到的问题。")
    if body.job_id:
        job = session.scalar(
            select(JobRecord).where(
                JobRecord.id == body.job_id,
                JobRecord.workspace_id == workspace.workspace_id,
            )
        )
        if job is None:
            raise ApiError(404, "SUPPORT_JOB_NOT_FOUND", "未找到当前工作区的关联任务。")
    session.execute(
        select(WorkspaceRecord.id)
        .where(WorkspaceRecord.id == workspace.workspace_id)
        .with_for_update()
    ).scalar_one()
    existing = session.scalar(
        select(SupportCaseRecord).where(
            SupportCaseRecord.workspace_id == workspace.workspace_id,
            SupportCaseRecord.request_key == request_key,
        )
    )
    if existing:
        if existing.issue != issue or existing.job_id != body.job_id:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "此请求编号已用于其他问题。")
        return existing
    row = SupportCaseRecord(
        id=new_id("sup"),
        workspace_id=workspace.workspace_id,
        created_by=workspace.principal.user_id,
        job_id=body.job_id,
        request_key=request_key,
        issue=issue,
        status="open",
        resolution="",
    )
    session.add(row)
    session.commit()
    return row


@router.get("/api/v1/admin/billing/support-cases", response_model=list[SupportResponse])
def admin_cases(
    session: SessionDependency,
    _admin: AdminDependency,
    status: Literal["open", "in_progress", "resolved"] | None = None,
    offset: int = Query(0, ge=0),
) -> list:
    query = select(SupportCaseRecord)
    if status:
        query = query.where(SupportCaseRecord.status == status)
    return list(
        session.scalars(
            query.order_by(SupportCaseRecord.created_at.desc()).offset(offset).limit(30)
        )
    )


@router.patch(
    "/api/v1/admin/billing/support-cases/{case_id}", response_model=SupportResponse
)
def update_case(
    case_id: str,
    body: SupportUpdate,
    request: Request,
    session: SessionDependency,
    admin: AdminDependency,
) -> SupportCaseRecord:
    row = session.scalar(
        select(SupportCaseRecord)
        .where(SupportCaseRecord.id == case_id)
        .with_for_update()
    )
    if row is None:
        raise ApiError(404, "SUPPORT_CASE_NOT_FOUND", "未找到售后记录。")
    if body.status == "resolved" and not body.resolution.strip():
        raise ApiError(
            422, "SUPPORT_RESOLUTION_REQUIRED", "请填写处理结果后再标记已解决。"
        )
    row.status = body.status
    row.resolution = body.resolution.strip()
    session.add(
        AuditEventRecord(
            workspace_id=row.workspace_id,
            actor_user_id=admin.user_id,
            action="support.updated",
            resource_type="support_case",
            resource_id=row.id,
            request_id=getattr(request.state, "request_id", None),
            details={"status": row.status, "job_id": row.job_id},
        )
    )
    session.commit()
    return row
