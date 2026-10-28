"""Operator original-route refunds and customer-owned refund history."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import credit_refunds as refunds
from . import haiyu_payments as payments
from .admin_auth import AdminDependency, SessionDependency, SuperAdminDependency
from .auth import WorkspaceDependency
from .credit_payment_routes import _callback_body
from .errors import ApiError
from .models import CreditPurchaseRecord, CreditRefundRecord

router = APIRouter(tags=["credit refunds"])


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["unused", "goodwill"] = "unused"
    reason: str = Field(min_length=5, max_length=160)
    quote_token: str = Field(min_length=64, max_length=64)
    amount_fen: int | None = Field(default=None, strict=True, gt=0)


@router.post("/api/v1/admin/billing/orders/{order_id}/refund-quote")
def refund_quote(
    order_id: str,
    session: SessionDependency,
    _admin: SuperAdminDependency,
    policy: Literal["unused", "goodwill"] = "unused",
) -> dict[str, Any]:
    return refunds.quote(session, order_id, policy)


@router.post("/api/v1/admin/billing/orders/{order_id}/refunds", status_code=201)
def refund_create(
    order_id: str,
    payload: RefundCreate,
    request: Request,
    session: SessionDependency,
    admin: SuperAdminDependency,
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    refund = refunds.create(
        session,
        order_id,
        admin.user_id,
        key=key or "",
        reason=payload.reason,
        policy=payload.policy,
        quote_token=payload.quote_token,
        amount_fen=payload.amount_fen,
    )
    refund_id = refund.id
    try:
        return refunds.response(
            refunds.process(session, request.app.state.settings, refund_id)
        )
    except ApiError:
        # The persisted intent remains recoverable during merchant outages.
        session.rollback()
        current = session.get(CreditRefundRecord, refund_id)
        assert current is not None
        return refunds.response(current)


@router.get("/api/v1/admin/billing/refunds")
def refund_list(
    session: SessionDependency,
    _admin: AdminDependency,
    purchase_id: str | None = None,
    workspace_id: str | None = None,
    status: Literal["requested", "processing", "succeeded", "failed"] | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    from sqlalchemy import func

    query = select(CreditRefundRecord)
    if purchase_id:
        query = query.where(CreditRefundRecord.purchase_id == purchase_id)
    if workspace_id:
        query = query.where(CreditRefundRecord.workspace_id == workspace_id)
    if status:
        query = query.where(CreditRefundRecord.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            refunds.response(row)
            for row in session.scalars(
                query.order_by(
                    CreditRefundRecord.created_at.desc(), CreditRefundRecord.id
                )
                .offset(offset)
                .limit(limit)
            )
        ],
        "total": total,
    }


@router.post("/api/v1/admin/billing/refunds/{refund_id}/reconcile")
def refund_reconcile(
    refund_id: str,
    request: Request,
    session: SessionDependency,
    _admin: SuperAdminDependency,
) -> dict[str, Any]:
    return refunds.response(
        refunds.process(session, request.app.state.settings, refund_id)
    )


@router.get("/api/v1/workspaces/{workspace_id}/credits/purchases/{purchase_id}/refunds")
def customer_refunds(
    purchase_id: str, workspace: WorkspaceDependency, session: SessionDependency
) -> dict[str, Any]:
    order = session.get(CreditPurchaseRecord, purchase_id)
    if order is None or order.workspace_id != workspace.workspace_id:
        raise ApiError(404, "CREDIT_PURCHASE_NOT_FOUND", "订单不存在。")
    return {
        "items": [
            refunds.response(row)
            for row in session.scalars(
                select(CreditRefundRecord)
                .where(CreditRefundRecord.purchase_id == purchase_id)
                .order_by(CreditRefundRecord.created_at.desc())
            )
        ]
    }


def _settle_callback(session: Session, data: dict) -> None:
    refund_id = data.get("out_refund_no", "")
    refund = session.get(CreditRefundRecord, refund_id)
    if refund is None:
        raise ApiError(400, "REFUND_NOT_FOUND", "退款通知无法匹配。")
    order = session.get(CreditPurchaseRecord, refund.purchase_id)
    assert order is not None
    terms = (order.product_snapshot or {}).get("payment_identity", {})
    if order.signing_key_id != "wechat" or data.get("mchid") != terms.get(
        "merchant_id"
    ):
        raise ApiError(400, "REFUND_MERCHANT_MISMATCH", "退款商户无法匹配。")
    normalized = {**data, "status": data.get("refund_status")}
    try:
        result = payments._refund_receipt(
            "wechat",
            normalized,
            order.id,
            refund.id,
            refund.amount_fen,
            order.amount_fen,
        )
    except ValueError:
        raise ApiError(
            400, "REFUND_RECEIPT_MISMATCH", "退款金额或订单不一致。"
        ) from None
    refunds.settle(session, refund_id, result)


@router.post("/api/v1/payments/wechat/refund-notify", status_code=204)
async def wechat_refund_notify(
    request: Request, session: SessionDependency
) -> Response:
    data = payments.decrypt_wechat_event(
        request.app.state.settings,
        request.headers,
        await _callback_body(request),
        {"REFUND.SUCCESS", "REFUND.ABNORMAL", "REFUND.CLOSED"},
    )
    await run_in_threadpool(_settle_callback, session, data)
    return Response(status_code=204)
