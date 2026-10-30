from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from .auth import SessionDependency, WorkspaceDependency
from .credit_billing import PRICING_VERSION, pricing_catalog
from .errors import ApiError
from .models import (
    CreditLedgerEntryRecord,
    CreditPurchaseRecord,
    CreditReservationRecord,
    JobRecord,
    WorkspaceCreditAccountRecord,
)
from .schemas_v1 import JobCreate
from .services import credit_enforcement_enabled

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/credits", tags=["credits"])


class CreditPricingRule(BaseModel):
    job_type: str
    task_type: str | None
    credits_per_image: int
    variant: str | None = None


class CreditAccountResponse(BaseModel):
    balance_credits: int
    reserved_credits: int
    available_credits: int
    enforcement_enabled: bool
    pricing_version: str
    pricing: list[CreditPricingRule]


class CreditLedgerEntry(BaseModel):
    id: int
    job_id: str | None
    title: str
    entry_type: str
    delta_balance_credits: int
    delta_reserved_credits: int
    released_credits: int
    available_after_credits: int
    created_at: str


class CreditLedgerResponse(BaseModel):
    items: list[CreditLedgerEntry]
    next_cursor: int | None


class CreditQuoteResponse(BaseModel):
    pricing_version: str
    image_count: int
    estimated_credits: int
    available_credits: int
    sufficient: bool


class CreditJobSettlementResponse(BaseModel):
    status: str
    reserved_credits: int
    settled_credits: int | None
    released_credits: int | None


@router.get("/jobs/{job_id}", response_model=CreditJobSettlementResponse)
def job_settlement(
    job_id: str, workspace: WorkspaceDependency, session: SessionDependency
) -> dict:
    reservation = session.scalar(
        select(CreditReservationRecord).where(
            CreditReservationRecord.workspace_id == workspace.workspace_id,
            CreditReservationRecord.job_id == job_id,
        )
    )
    if reservation is None:
        raise ApiError(404, "CREDIT_RESERVATION_NOT_FOUND", "该任务暂无积分预留记录。")
    final = reservation.status in {"settled", "released"}
    return {
        "status": reservation.status,
        "reserved_credits": reservation.reserved_credits,
        "settled_credits": reservation.settled_credits if final else None,
        "released_credits": max(
            0, reservation.reserved_credits - reservation.settled_credits
        )
        if final
        else None,
    }


def _account(session: Any, workspace_id: str) -> WorkspaceCreditAccountRecord:
    account = session.get(WorkspaceCreditAccountRecord, workspace_id)
    if account is None:
        raise ApiError(409, "CREDIT_ACCOUNT_NOT_CONFIGURED", "积分账户尚未配置。")
    return account


@router.get("", response_model=CreditAccountResponse)
def account(
    workspace: WorkspaceDependency, session: SessionDependency
) -> CreditAccountResponse:
    if credit_enforcement_enabled(workspace.workspace_id):
        from .credit_memberships import refresh

        refresh(session, workspace.workspace_id)
        session.commit()
    row = _account(session, workspace.workspace_id)
    return CreditAccountResponse(
        balance_credits=row.balance_credits,
        reserved_credits=row.reserved_credits,
        available_credits=row.balance_credits - row.reserved_credits,
        enforcement_enabled=credit_enforcement_enabled(workspace.workspace_id),
        pricing_version=PRICING_VERSION,
        pricing=[CreditPricingRule(**item) for item in pricing_catalog()],
    )


@router.get("/ledger", response_model=CreditLedgerResponse)
def ledger(
    workspace: WorkspaceDependency,
    session: SessionDependency,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    statement = (
        select(
            CreditLedgerEntryRecord,
            JobRecord.display_name,
            CreditPurchaseRecord.package_name,
        )
        .outerjoin(
            JobRecord,
            (JobRecord.id == CreditLedgerEntryRecord.job_id)
            & (JobRecord.workspace_id == CreditLedgerEntryRecord.workspace_id),
        )
        .outerjoin(
            CreditPurchaseRecord,
            CreditPurchaseRecord.ledger_entry_id == CreditLedgerEntryRecord.id,
        )
        .where(CreditLedgerEntryRecord.workspace_id == workspace.workspace_id)
    )
    if before_id is not None:
        statement = statement.where(CreditLedgerEntryRecord.id < before_id)
    rows = session.execute(
        statement.order_by(CreditLedgerEntryRecord.id.desc()).limit(limit + 1)
    ).all()
    items = []
    for entry, title, package_name in rows[:limit]:
        items.append(
            {
                "id": entry.id,
                "job_id": entry.job_id,
                "title": title or package_name or entry.description or "创作任务",
                "entry_type": entry.entry_type,
                "delta_balance_credits": entry.delta_balance_credits,
                "delta_reserved_credits": entry.delta_reserved_credits,
                "released_credits": (
                    max(0, -entry.delta_reserved_credits + entry.delta_balance_credits)
                    if entry.entry_type in {"settle", "release"}
                    else 0
                ),
                "available_after_credits": entry.balance_after_credits
                - entry.reserved_after_credits,
                "created_at": (
                    entry.created_at.replace(tzinfo=UTC)
                    if entry.created_at.tzinfo is None
                    else entry.created_at.astimezone(UTC)
                ).isoformat(),
            }
        )
    return {
        "items": items,
        "next_cursor": items[-1]["id"] if len(rows) > limit else None,
    }


@router.post("/quote", response_model=CreditQuoteResponse)
def quote(
    payload: JobCreate,
    request: Request,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> dict:
    from .credit_billing import image_unit_credits
    from .routes_v1 import _new_job_from_payload, _validate_inspiration_case_job

    inspiration = _validate_inspiration_case_job(session, payload)
    job, _ = _new_job_from_payload(
        workspace.workspace_id,
        payload,
        detail_medium_price_micros=request.app.state.settings.xinghe_medium_price_micros,
        detail_high_price_micros=request.app.state.settings.xinghe_high_price_micros,
    )
    if inspiration is not None:
        job.configuration["inspiration_case_id"] = inspiration.id
        job.configuration["inspiration_workflow"] = inspiration.workflow_type
    job.estimated_credits = job.expected_item_count * image_unit_credits(
        job.type, job.configuration
    )
    if credit_enforcement_enabled(workspace.workspace_id):
        from .credit_memberships import refresh

        refresh(session, workspace.workspace_id)
        session.commit()
    row = _account(session, workspace.workspace_id)
    available = row.balance_credits - row.reserved_credits
    return {
        "pricing_version": PRICING_VERSION,
        "image_count": job.expected_item_count,
        "estimated_credits": job.estimated_credits,
        "available_credits": available,
        "sufficient": not credit_enforcement_enabled(workspace.workspace_id)
        or available >= (job.estimated_credits or 0),
    }
