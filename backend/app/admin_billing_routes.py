"""Customer support operations on Huipin's own wallet and order tables."""

import csv
import io
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from . import haiyu_payments as payments
from .admin_auth import (
    AdminDependency,
    SessionDependency,
    SuperAdminDependency,
    is_super_admin,
)
from .credit_admin_actions import claim
from .credit_memberships import (
    add_months,
    aware,
    grant,
    membership_state,
    refresh,
    scheduling_end,
)
from .credit_payment_routes import _native_transaction, _response
from .credit_products import PRODUCTS
from .errors import ApiError
from .models import (
    AdminUserRecord,
    AuditEventRecord,
    CreditGrantRecord,
    CreditLedgerEntryRecord,
    CreditMembershipRecord,
    CreditPurchaseRecord,
    CreditRefundRecord,
    UserRecord,
    WorkspaceRecord,
    utc_now,
)
from .services import new_id

router = APIRouter(prefix="/api/v1/admin/billing", tags=["customer support"])


def _mask(phone: str | None) -> str | None:
    return phone[:3] + "****" + phone[-4:] if phone and len(phone) >= 7 else None


@router.get("/access")
def access(
    request: Request, session: SessionDependency, admin: AdminDependency
) -> dict[str, Any]:
    return {
        "can_adjust": is_super_admin(admin, session, request.app.state.settings),
        "user_id": admin.user_id,
    }


@router.get("/users")
def users(
    session: SessionDependency,
    _admin: AdminDependency,
    search: str = Query("", max_length=80),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = select(UserRecord).order_by(UserRecord.created_at.desc(), UserRecord.id)
    if search.strip():
        pattern = "%" + search.strip().replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(
                *[
                    column.ilike(pattern, escape="\\")
                    for column in (
                        UserRecord.id,
                        UserRecord.phone_number,
                        UserRecord.email_normalized,
                        UserRecord.display_name,
                    )
                ]
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    items = []
    for user in session.scalars(query.limit(limit).offset(offset)):
        spaces = list(
            session.scalars(
                select(WorkspaceRecord.id).where(
                    WorkspaceRecord.created_by_user_id == user.id
                )
            )
        )
        items.append(
            {
                "id": user.id,
                "name": user.display_name or _mask(user.phone_number) or "用户",
                "phone_hint": _mask(user.phone_number),
                "status": user.status,
                "created_at": user.created_at.isoformat(),
                "workspace_ids": spaces,
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/workspaces/{workspace_id}")
def workspace_detail(
    workspace_id: str, session: SessionDependency, _admin: AdminDependency
) -> dict[str, Any]:
    space = session.get(WorkspaceRecord, workspace_id)
    if space is None:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "用户工作区不存在。")
    account = refresh(session, workspace_id)
    result = {
        "workspace_id": workspace_id,
        "name": space.name,
        "balance_credits": account.balance_credits,
        "reserved_credits": account.reserved_credits,
        "available_credits": account.balance_credits - account.reserved_credits,
        "membership": membership_state(session, workspace_id),
        "ledger": [
            {
                "id": row.id,
                "delta": row.delta_balance_credits,
                "reserved_delta": row.delta_reserved_credits,
                "title": row.description or row.entry_type,
                "created_at": row.created_at.isoformat(),
            }
            for row in session.scalars(
                select(CreditLedgerEntryRecord)
                .where(CreditLedgerEntryRecord.workspace_id == workspace_id)
                .order_by(CreditLedgerEntryRecord.id.desc())
                .limit(50)
            )
        ],
    }
    session.commit()
    return result


class MemberGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    reason: str = Field(min_length=5, max_length=160)


def _members(session: Any, workspace_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": m.id,
            "plan_name": m.plan_name,
            "monthly_credits": m.monthly_credits,
            "months": m.months,
            "starts_at": aware(m.starts_at).isoformat(),
            "ends_at": aware(m.ends_at).isoformat(),
            "purchase_id": m.purchase_id,
            "cancelled_at": aware(m.cancelled_at).isoformat()
            if m.cancelled_at
            else None,
            "cancellation_reason": m.cancellation_reason,
        }
        for m in session.scalars(
            select(CreditMembershipRecord)
            .where(CreditMembershipRecord.workspace_id == workspace_id)
            .order_by(CreditMembershipRecord.starts_at.desc())
        )
    ]


@router.get("/workspaces/{workspace_id}/memberships")
def members(
    workspace_id: str, session: SessionDependency, _admin: AdminDependency
) -> dict[str, Any]:
    return {
        "items": _members(session, workspace_id),
        "products": [p.snapshot() for p in PRODUCTS.values() if p.kind == "membership"],
    }


@router.post("/workspaces/{workspace_id}/memberships", status_code=201)
def member_grant(
    workspace_id: str,
    payload: MemberGrant,
    session: SessionDependency,
    admin: SuperAdminDependency,
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    product = PRODUCTS.get(payload.product_id)
    if not product or product.kind != "membership" or len(payload.reason.strip()) < 5:
        raise ApiError(422, "MEMBERSHIP_INVALID", "请选择会员方案并填写处理原因。")
    refresh(session, workspace_id)
    intent, reused = claim(
        session,
        workspace_id,
        admin.user_id,
        key or "",
        "membership_grant",
        payload.model_dump(),
    )
    if reused:
        session.rollback()
        return intent.response_body
    now = aware(utc_now())
    start = scheduling_end(session, workspace_id, now)
    member = CreditMembershipRecord(
        id=new_id("mem"),
        workspace_id=workspace_id,
        purchase_id=None,
        plan_id=product.plan_id,
        plan_name=product.name.split(" · ")[0],
        monthly_credits=product.credits,
        months=product.months,
        starts_at=start,
        ends_at=add_months(start, product.months),
    )
    session.add(member)
    session.flush()
    refresh(session, workspace_id)
    result = {
        "id": member.id,
        "starts_at": start.isoformat(),
        "ends_at": aware(member.ends_at).isoformat(),
        "source": "manual",
    }
    intent.response_body = result
    session.add(
        AuditEventRecord(
            workspace_id=workspace_id,
            actor_user_id=admin.user_id,
            action="membership.admin_granted",
            resource_type="credit_membership",
            resource_id=member.id,
            details={
                **result,
                "product_id": product.id,
                "reason": payload.reason.strip(),
            },
        )
    )
    session.commit()
    return result


class MemberCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=160)


@router.post("/workspaces/{workspace_id}/memberships/{member_id}/cancel")
def member_cancel(
    workspace_id: str,
    member_id: str,
    payload: MemberCancel,
    session: SessionDependency,
    admin: SuperAdminDependency,
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    account = refresh(session, workspace_id)
    intent, reused = claim(
        session,
        workspace_id,
        admin.user_id,
        key or "",
        "membership_cancel",
        {"id": member_id, **payload.model_dump()},
    )
    if reused:
        session.rollback()
        return intent.response_body
    member = session.get(CreditMembershipRecord, member_id)
    if member is None or member.workspace_id != workspace_id:
        raise ApiError(404, "MEMBERSHIP_NOT_FOUND", "会员记录不存在。")
    if member.purchase_id:
        raise ApiError(
            409, "PAID_MEMBERSHIP_REQUIRES_REFUND", "付费会员请从支付订单办理退款。"
        )
    if member.cancelled_at or len(payload.reason.strip()) < 5:
        raise ApiError(409, "MEMBERSHIP_CANCEL_INVALID", "该会员已停止或缺少处理原因。")
    lots = list(
        session.scalars(
            select(CreditGrantRecord).where(
                CreditGrantRecord.workspace_id == workspace_id,
                CreditGrantRecord.source_key.in_(
                    [f"{member.id}:{i}" for i in range(member.months)]
                ),
            )
        )
    )
    if any(lot.held for lot in lots):
        raise ApiError(
            409, "MEMBERSHIP_CREDITS_IN_USE", "积分正在生成中，请待任务结算后处理。"
        )
    revoked = sum(lot.remaining for lot in lots)
    for lot in lots:
        lot.remaining = 0
    account.balance_credits -= revoked
    account.version += 1
    member.cancelled_at = utc_now()
    member.cancellation_reason = payload.reason.strip()
    entry = CreditLedgerEntryRecord(
        workspace_id=workspace_id,
        entry_type="adjustment",
        delta_balance_credits=-revoked,
        delta_reserved_credits=0,
        balance_after_credits=account.balance_credits,
        reserved_after_credits=account.reserved_credits,
        description="停止赠送会员 · " + payload.reason.strip(),
    )
    session.add(entry)
    result = {"id": member.id, "credits_revoked": revoked, "status": "cancelled"}
    intent.response_body = result
    session.add(
        AuditEventRecord(
            workspace_id=workspace_id,
            actor_user_id=admin.user_id,
            action="membership.admin_cancelled",
            resource_type="credit_membership",
            resource_id=member.id,
            details={**result, "reason": payload.reason.strip()},
        )
    )
    session.commit()
    return result


@router.get("/overview")
def overview(
    session: SessionDependency, _admin: AdminDependency, request: Request
) -> dict[str, Any]:
    money = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(CreditPurchaseRecord.amount_fen), 0),
            func.coalesce(func.sum(CreditPurchaseRecord.refunded_amount_fen), 0),
        ).where(CreditPurchaseRecord.status != "pending")
    ).one()
    return {
        "paid_orders": money[0],
        "gross_amount_fen": money[1],
        "refunded_amount_fen": money[2],
        "net_amount_fen": money[1] - money[2],
        "pending_refunds": session.scalar(
            select(func.count())
            .select_from(CreditRefundRecord)
            .where(CreditRefundRecord.status.in_(("requested", "processing")))
        ),
        "channels": {
            "alipay": "ready"
            if "alipay" in payments.channels(request.app.state.settings)
            else "disabled",
            "wechat": "ready"
            if "wechat" in payments.channels(request.app.state.settings)
            else "configuration_required",
        },
    }


class Adjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delta: int = Field(strict=True, ge=-1000000, le=1000000)
    reason: str = Field(min_length=5, max_length=180)


@router.post("/workspaces/{workspace_id}/adjustments")
def adjust(
    workspace_id: str,
    payload: Adjustment,
    session: SessionDependency,
    admin: SuperAdminDependency,
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    if payload.delta == 0 or len(payload.reason.strip()) < 5:
        raise ApiError(
            422, "ADJUSTMENT_INVALID", "填写非零积分和至少五个字的处理原因。"
        )
    account = refresh(session, workspace_id)
    intent, reused = claim(
        session,
        workspace_id,
        admin.user_id,
        key or "",
        "adjustment",
        payload.model_dump(),
    )
    if reused:
        return intent.response_body or {}
    before = account.balance_credits
    if payload.delta > 0:
        entry_id = grant(
            session,
            account,
            intent.id,
            payload.delta,
            None,
            "人工补发 · " + payload.reason.strip(),
            kind="adjustment",
        ).ledger_entry_id
    else:
        needed = -payload.delta
        if needed > account.balance_credits - account.reserved_credits:
            raise ApiError(
                409,
                "INSUFFICIENT_AVAILABLE_CREDITS",
                "可用积分不足，不能扣回冻结或已消费的积分。",
            )
        lots = session.scalars(
            select(CreditGrantRecord)
            .where(CreditGrantRecord.workspace_id == workspace_id)
            .order_by(
                CreditGrantRecord.expires_at.is_(None),
                CreditGrantRecord.expires_at,
                CreditGrantRecord.created_at,
                CreditGrantRecord.id,
            )
        )
        for lot in lots:
            amount = min(needed, lot.remaining - lot.held)
            lot.remaining -= amount
            needed -= amount
            if not needed:
                break
        if needed:
            raise ApiError(409, "CREDIT_LOT_MISMATCH", "积分来源需要核对，本次未扣回。")
        account.balance_credits += payload.delta
        account.version += 1
        entry = CreditLedgerEntryRecord(
            workspace_id=workspace_id,
            entry_type="adjustment",
            delta_balance_credits=payload.delta,
            delta_reserved_credits=0,
            balance_after_credits=account.balance_credits,
            reserved_after_credits=account.reserved_credits,
            description="人工扣回 · " + payload.reason.strip(),
        )
        session.add(entry)
        session.flush()
        entry_id = entry.id
    session.add(
        AuditEventRecord(
            workspace_id=workspace_id,
            actor_user_id=admin.user_id,
            action="credits.admin_adjusted",
            resource_type="credit_ledger",
            resource_id=str(entry_id),
            details={
                "delta": payload.delta,
                "reason": payload.reason.strip(),
                "before": before,
                "after": account.balance_credits,
            },
        )
    )
    result = {
        "ledger_entry_id": entry_id,
        "balance_credits": account.balance_credits,
        "available_credits": account.balance_credits - account.reserved_credits,
    }
    intent.response_body = result
    session.commit()
    return result


class UserState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=5, max_length=180)


@router.patch("/users/{user_id}/status")
def user_status(
    user_id: str,
    payload: UserState,
    session: SessionDependency,
    admin: SuperAdminDependency,
) -> dict[str, str]:
    user = session.get(UserRecord, user_id, with_for_update=True)
    if user is None:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在。")
    if user_id == admin.user_id or session.get(AdminUserRecord, user_id) is not None:
        raise ApiError(
            409, "ADMIN_STATUS_PROTECTED", "自身与管理员账户请通过管理员权限管理处理。"
        )
    if len(payload.reason.strip()) < 5:
        raise ApiError(422, "REASON_REQUIRED", "请填写处理原因。")
    previous = user.status
    user.status = payload.status
    session.add(
        AuditEventRecord(
            actor_user_id=admin.user_id,
            action="user.support_status_changed",
            resource_type="user",
            resource_id=user_id,
            details={
                "before": previous,
                "after": payload.status,
                "reason": payload.reason.strip(),
            },
        )
    )
    session.commit()
    return {"id": user_id, "status": user.status}


@router.get("/orders")
def orders(
    session: SessionDependency,
    _admin: AdminDependency,
    search: str = Query("", max_length=80),
    workspace_id: str | None = None,
    status: Literal["pending", "paid", "partially_refunded", "refunded"] | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    query = select(CreditPurchaseRecord).order_by(
        CreditPurchaseRecord.created_at.desc(), CreditPurchaseRecord.id
    )
    if workspace_id:
        query = query.where(CreditPurchaseRecord.workspace_id == workspace_id)
    if status:
        query = query.where(CreditPurchaseRecord.status == status)
    if search.strip():
        query = query.where(
            or_(
                CreditPurchaseRecord.id == search.strip(),
                CreditPurchaseRecord.gateway_trade_no == search.strip(),
                CreditPurchaseRecord.created_by_user_id == search.strip(),
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            {
                **_response(row).model_dump(),
                "workspace_id": row.workspace_id,
                "user_id": row.created_by_user_id,
                "gateway_trade_no": row.gateway_trade_no,
                "checkout_url": None,
                "code_url": None,
            }
            for row in session.scalars(query.limit(limit).offset(offset))
        ],
        "total": total,
    }


@router.post("/orders/{order_id}/reconcile")
def reconcile(
    order_id: str,
    request: Request,
    session: SessionDependency,
    admin: SuperAdminDependency,
) -> dict[str, Any]:
    order = session.get(CreditPurchaseRecord, order_id)
    if order is None:
        raise ApiError(404, "CREDIT_PURCHASE_NOT_FOUND", "订单不存在。")
    if order.status == "pending":
        channel, workspace_id = order.signing_key_id, order.workspace_id
        session.rollback()
        data = payments.query_payment(request.app.state.settings, channel, order_id)
        if data.get("out_trade_no") != order_id:
            raise ApiError(400, "PAYMENT_ORDER_MISMATCH", "支付查询返回了其他订单。")
        if channel == "alipay":
            data.setdefault(
                "seller_id", request.app.state.settings.credit_alipay_seller_id
            )
        _native_transaction(session, channel, data)
        session.add(
            AuditEventRecord(
                workspace_id=workspace_id,
                actor_user_id=admin.user_id,
                action="credits.admin_reconciled",
                resource_type="credit_purchase",
                resource_id=order_id,
            )
        )
        session.commit()
        session.expire_all()
    current = session.get(CreditPurchaseRecord, order_id)
    assert current is not None
    return _response(current).model_dump()


@router.get("/orders-export")
def export_orders(
    session: SessionDependency, admin: AdminDependency, workspace_id: str | None = None
) -> Response:
    query = select(CreditPurchaseRecord).order_by(
        CreditPurchaseRecord.created_at, CreditPurchaseRecord.id
    )
    if workspace_id:
        query = query.where(CreditPurchaseRecord.workspace_id == workspace_id)
    rows = list(session.scalars(query.limit(10001)))
    if len(rows) > 10000:
        raise ApiError(
            422, "EXPORT_TOO_LARGE", "请按用户工作区分批导出，每次最多一万笔。"
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "订单号",
            "用户ID",
            "工作区",
            "产品",
            "渠道",
            "状态",
            "原金额(分)",
            "已退金额(分)",
            "创建时间",
            "支付时间",
        ]
    )

    def safe(value: Any) -> str:
        raw = "" if value is None else str(value)
        return "'" + raw if raw.lstrip().startswith(("=", "+", "-", "@")) else raw

    for row in rows:
        writer.writerow(
            [
                safe(value)
                for value in [
                    row.id,
                    row.created_by_user_id,
                    row.workspace_id,
                    row.package_name,
                    row.signing_key_id,
                    row.status,
                    row.amount_fen,
                    row.refunded_amount_fen,
                    aware(row.created_at).isoformat(),
                    aware(row.paid_at).isoformat() if row.paid_at else "",
                ]
            ]
        )
    session.add(
        AuditEventRecord(
            actor_user_id=admin.user_id,
            workspace_id=workspace_id,
            action="credits.orders_exported",
            resource_type="credit_purchase",
            details={"rows": len(rows)},
        )
    )
    session.commit()
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="huipin-orders.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/audit")
def audit(
    session: SessionDependency,
    _admin: AdminDependency,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    workspace_id: str | None = None,
) -> dict[str, Any]:
    query = select(AuditEventRecord).where(
        AuditEventRecord.action.in_(
            [
                "credits.admin_adjusted",
                "credits.admin_reconciled",
                "user.support_status_changed",
                "credits.purchase_settled",
                "credits.orders_exported",
                "credits.refund_requested",
                "credits.refund_succeeded",
                "credits.refund_failed",
                "membership.admin_granted",
                "membership.admin_cancelled",
            ]
        )
    )
    if workspace_id:
        query = query.where(AuditEventRecord.workspace_id == workspace_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "workspace_id": row.workspace_id,
                "action": row.action,
                "resource_id": row.resource_id,
                "details": row.details,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in session.scalars(
                query.order_by(AuditEventRecord.id.desc()).limit(limit).offset(offset)
            )
        ],
        "total": total,
    }
