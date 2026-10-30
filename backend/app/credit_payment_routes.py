"""Official Haiyu payment channels and atomic membership fulfillment."""

import re
from datetime import UTC
from typing import Annotated, Any, Literal, cast
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import haiyu_payments as payments
from .auth import SessionDependency, WorkspaceDependency, WorkspaceWriteDependency
from .config import Settings
from .credit_memberships import (
    add_months,
    aware,
    grant,
    membership_state,
    refresh,
    scheduling_end,
)
from .credit_products import LEGACY_PACK_ID, PRODUCTS
from .errors import ApiError
from .models import (
    AuditEventRecord,
    CreditLedgerEntryRecord,
    CreditMembershipRecord,
    CreditPurchaseRecord,
    utc_now,
)
from .services import credit_enforcement_enabled, new_id, require_idempotency_key


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["credit payments"], dependencies=[Depends(_no_store)])
PACKAGE_ID = "credits_1500"
PACKAGE_NAME = "创作积分包 · 1,500 积分"
AMOUNT_FEN = 9900
PACKAGE_CREDITS = 1500


def parse_payment_form(payload: bytes) -> dict[str, str]:
    if len(payload) > 8192:
        raise ApiError(413, "PAYMENT_CALLBACK_TOO_LARGE", "支付通知过长。")
    try:
        raw = payload.decode("utf-8", errors="strict")
        if re.search(r"%(?![0-9A-Fa-f]{2})", raw):
            raise ValueError("Invalid percent encoding")
        pairs = parse_qsl(
            raw, keep_blank_values=True, errors="strict", max_num_fields=128
        )
    except (ValueError, UnicodeError) as error:
        raise ApiError(400, "INVALID_PAYMENT_CALLBACK", "支付通知格式错误。") from error
    result: dict[str, str] = {}
    for name, value in pairs:
        if (
            not re.fullmatch(r"[A-Za-z0-9_]{1,128}", name)
            or name in result
            or len(value.encode("utf-8")) > 2048
        ):
            raise ApiError(400, "INVALID_PAYMENT_CALLBACK", "支付通知字段无效。")
        result[name] = value
    return result


def money_fen(value: str) -> int:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?", value):
        raise ApiError(400, "INVALID_PAYMENT_AMOUNT", "支付金额格式错误。")
    whole, _, fraction = value.partition(".")
    return int(whole) * 100 + int(fraction.ljust(2, "0") or "0")


class CreditPackage(BaseModel):
    id: str
    name: str
    amount_fen: int
    credits: int
    kind: str
    plan_id: str | None
    months: int
    validity_months: int


class CreditPackageCatalog(BaseModel):
    enabled: bool
    packages: list[CreditPackage]
    channels: list[Literal["alipay", "wechat"]]


class CreditPurchaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    channel: Literal["alipay", "wechat"] = "alipay"


class CreditPurchaseResponse(BaseModel):
    id: str
    package_name: str
    amount_fen: int
    credits: int
    status: str
    checkout_url: str | None
    code_url: str | None
    channel: str
    confirmation_mode: str
    refunded_amount_fen: int
    created_at: str
    paid_at: str | None
    product_kind: str
    membership_starts_at: str | None
    membership_ends_at: str | None
    months: int


def _response(order: CreditPurchaseRecord) -> CreditPurchaseResponse:
    return CreditPurchaseResponse(
        id=order.id,
        package_name=order.package_name,
        amount_fen=order.amount_fen,
        credits=order.credits,
        status=order.status,
        created_at=aware(order.created_at).isoformat(),
        paid_at=aware(order.paid_at).isoformat() if order.paid_at else None,
        product_kind=(order.product_snapshot or {}).get("kind", "pack"),
        membership_starts_at=(order.product_snapshot or {}).get("starts_at"),
        membership_ends_at=(order.product_snapshot or {}).get("ends_at"),
        months=(order.product_snapshot or {}).get("months", 0),
        checkout_url=order.checkout_url
        if order.status == "pending" and order.signing_key_id == "alipay"
        else None,
        code_url=order.checkout_url
        if order.status == "pending" and order.signing_key_id == "wechat"
        else None,
        channel=order.signing_key_id,
        confirmation_mode=(order.product_snapshot or {}).get(
            "confirmation_mode", "callback"
        ),
        refunded_amount_fen=order.refunded_amount_fen,
    )


@router.get(
    "/api/v1/workspaces/{workspace_id}/credits/packages",
    response_model=CreditPackageCatalog,
)
def packages(
    request: Request, workspace: WorkspaceDependency, include_memberships: bool = False
) -> CreditPackageCatalog:
    settings: Settings = request.app.state.settings
    available = payments.channels(settings)
    enabled = credit_enforcement_enabled(workspace.workspace_id) and bool(available)
    return CreditPackageCatalog(
        enabled=enabled,
        channels=available if enabled else [],
        packages=[
            CreditPackage(**product.snapshot())
            for product in PRODUCTS.values()
            if product.id != LEGACY_PACK_ID
            and (include_memberships or product.kind == "pack")
        ],
    )


@router.post(
    "/api/v1/workspaces/{workspace_id}/credits/purchases",
    response_model=CreditPurchaseResponse,
    status_code=201,
)
def create_purchase(
    payload: CreditPurchaseCreate,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreditPurchaseResponse:
    if not credit_enforcement_enabled(workspace.workspace_id):
        raise ApiError(403, "CREDIT_PILOT_REQUIRED", "当前账号暂未开放积分充值。")
    settings: Settings = request.app.state.settings
    identity = payments.identity(settings, payload.channel)
    product = PRODUCTS.get(payload.package_id)
    if product is None:
        raise ApiError(422, "INVALID_CREDIT_PACKAGE", "充值套餐不存在。")
    request_key = require_idempotency_key(idempotency_header)
    statement = select(CreditPurchaseRecord).where(
        CreditPurchaseRecord.workspace_id == workspace.workspace_id,
        CreditPurchaseRecord.created_by_user_id == workspace.principal.user_id,
        CreditPurchaseRecord.idempotency_key == request_key,
    )
    existing = session.scalar(statement)
    if existing is not None:
        if (
            existing.package_id != payload.package_id
            or existing.signing_key_id != payload.channel
        ):
            raise ApiError(
                409, "IDEMPOTENCY_CONFLICT", "这次购买内容已改变，请重新确认。"
            )
        if not existing.checkout_url and existing.status == "pending":
            existing.checkout_url = payments.create_checkout(
                settings,
                payload.channel,
                existing.id,
                existing.package_name,
                existing.amount_fen,
            )
            session.commit()
        return _response(existing)
    order_id = new_id("pay")[:32]
    order = CreditPurchaseRecord(
        id=order_id,
        workspace_id=workspace.workspace_id,
        created_by_user_id=workspace.principal.user_id,
        idempotency_key=request_key,
        package_id=product.id,
        package_name=product.name,
        amount_fen=product.amount_fen,
        credits=product.credits,
        product_snapshot={
            **product.snapshot(),
            "payment_identity": identity,
            "confirmation_mode": settings.credit_wechat_confirmation_mode
            if payload.channel == "wechat"
            else "callback",
        },
        merchant_pid=identity["merchant_id"],
        signing_key_id=payload.channel,
        checkout_url="",
        status="pending",
    )
    session.add(order)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(statement)
        if existing is None:
            raise
        if (
            existing.package_id != payload.package_id
            or existing.signing_key_id != payload.channel
        ):
            raise ApiError(
                409, "IDEMPOTENCY_CONFLICT", "这次购买内容已改变，请重新确认。"
            ) from None
        return _response(existing)
    order.checkout_url = payments.create_checkout(
        settings, payload.channel, order.id, order.package_name, order.amount_fen
    )
    session.commit()
    session.refresh(order)
    return _response(order)


class CreditPurchaseList(BaseModel):
    items: list[CreditPurchaseResponse]
    next_cursor: str | None


class CreditMembershipState(BaseModel):
    membership_status: str = "none"
    previous_plan_name: str | None = None
    plan_id: str | None
    plan_name: str | None
    monthly_credits: int
    starts_at: str | None
    ends_at: str | None
    next_plan_name: str | None
    next_plan_starts_at: str | None
    next_grant_at: str | None
    next_expiry_at: str | None
    expiring_credits: int
    member_credits: int
    pack_credits: int


@router.get(
    "/api/v1/workspaces/{workspace_id}/credits/membership",
    response_model=CreditMembershipState,
)
def membership(workspace: WorkspaceDependency, session: SessionDependency) -> dict:
    if credit_enforcement_enabled(workspace.workspace_id):
        refresh(session, workspace.workspace_id)
        session.commit()
    return membership_state(session, workspace.workspace_id)


@router.get(
    "/api/v1/workspaces/{workspace_id}/credits/purchases",
    response_model=CreditPurchaseList,
)
def purchase_list(
    workspace: WorkspaceDependency,
    session: SessionDependency,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    status: Literal["pending", "paid", "partially_refunded", "refunded", "closed"]
    | None = None,
) -> CreditPurchaseList:
    statement = select(CreditPurchaseRecord).where(
        CreditPurchaseRecord.workspace_id == workspace.workspace_id
    )
    if status:
        statement = statement.where(CreditPurchaseRecord.status == status)
    if cursor:
        anchor = session.scalar(
            select(CreditPurchaseRecord).where(
                CreditPurchaseRecord.id == cursor,
                CreditPurchaseRecord.workspace_id == workspace.workspace_id,
            )
        )
        if anchor is None:
            raise ApiError(422, "INVALID_ORDER_CURSOR", "订单分页位置无效。")
        statement = statement.where(
            or_(
                CreditPurchaseRecord.created_at < anchor.created_at,
                and_(
                    CreditPurchaseRecord.created_at == anchor.created_at,
                    CreditPurchaseRecord.id < anchor.id,
                ),
            )
        )
    rows = list(
        session.scalars(
            statement.order_by(
                CreditPurchaseRecord.created_at.desc(), CreditPurchaseRecord.id.desc()
            ).limit(limit + 1)
        )
    )
    return CreditPurchaseList(
        items=[_response(row) for row in rows[:limit]],
        next_cursor=rows[limit - 1].id if len(rows) > limit else None,
    )


@router.get(
    "/api/v1/workspaces/{workspace_id}/credits/purchases/{purchase_id}",
    response_model=CreditPurchaseResponse,
)
def purchase_detail(
    purchase_id: str, workspace: WorkspaceDependency, session: SessionDependency
) -> CreditPurchaseResponse:
    order = session.scalar(
        select(CreditPurchaseRecord).where(
            CreditPurchaseRecord.id == purchase_id,
            CreditPurchaseRecord.workspace_id == workspace.workspace_id,
        )
    )
    if order is None:
        raise ApiError(404, "CREDIT_PURCHASE_NOT_FOUND", "充值订单不存在。")
    return _response(order)


@router.post(
    "/api/v1/workspaces/{workspace_id}/credits/purchases/{purchase_id}/reconcile",
    response_model=CreditPurchaseResponse,
)
def reconcile_purchase(
    purchase_id: str,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> CreditPurchaseResponse:
    order = session.scalar(
        select(CreditPurchaseRecord).where(
            CreditPurchaseRecord.id == purchase_id,
            CreditPurchaseRecord.workspace_id == workspace.workspace_id,
        )
    )
    if order is None:
        raise ApiError(404, "CREDIT_PURCHASE_NOT_FOUND", "充值订单不存在。")
    if order.status in {"paid", "partially_refunded", "refunded"}:
        return _response(order)
    order_id = order.id
    session.rollback()
    from .credit_payment_reconciliation import process

    current = process(
        session,
        request.app.state.settings,
        order_id,
        manual=True,
        initiator="customer",
        raise_errors=True,
    )
    assert current is not None
    return _response(current)


def _settle_native(session: Session, channel: str, data: dict) -> None:
    order = session.scalar(
        select(CreditPurchaseRecord).where(
            CreditPurchaseRecord.id == data.get("out_trade_no", "")
        )
    )
    if order is None or order.signing_key_id != channel:
        raise ApiError(400, "PAYMENT_ORDER_MISMATCH", "支付订单无法匹配。")
    terms = (order.product_snapshot or {}).get("payment_identity", {})
    if channel == "alipay":
        valid = data.get("app_id") == terms.get("app_id") and data.get(
            "seller_id"
        ) == terms.get("merchant_id")
        paid = data.get("trade_status") in {"TRADE_SUCCESS", "TRADE_FINISHED"}
        amount = data.get("total_amount", "")
        trade = data.get("trade_no", "")
    else:
        valid = data.get("appid") == terms.get("app_id") and data.get(
            "mchid"
        ) == terms.get("merchant_id")
        paid = data.get("trade_state") == "SUCCESS"
        total = data.get("amount", {}).get("total")
        valid = valid and (
            not paid
            or (type(total) is int and data.get("amount", {}).get("currency") == "CNY")
        )
        amount = f"{total // 100}.{total % 100:02d}" if type(total) is int else ""
        trade = data.get("transaction_id", "")
    if not valid or (paid and money_fen(amount) != order.amount_fen):
        raise ApiError(400, "PAYMENT_ORDER_MISMATCH", "支付商户或金额不一致。")
    if not paid:
        return
    _settle_verified_purchase(
        session,
        {
            "pid": order.merchant_pid,
            "out_trade_no": order.id,
            "trade_no": trade,
            "type": channel,
            "name": order.package_name,
            "param": order.id,
            "money": amount,
            "trade_status": "TRADE_SUCCESS",
        },
    )


def _settle_verified_purchase(session: Session, parameters: dict[str, str]) -> None:
    """Only verified callbacks or authenticated server queries may call this."""
    order = session.scalar(
        select(CreditPurchaseRecord)
        .where(CreditPurchaseRecord.id == parameters.get("out_trade_no", ""))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise ApiError(400, "INVALID_PAYMENT_CALLBACK", "支付通知无法确认。")
    trade_no = parameters.get("trade_no", "")
    if (
        parameters.get("pid") != order.merchant_pid
        or parameters.get("trade_status") != "TRADE_SUCCESS"
        or parameters.get("name") != order.package_name
        or parameters.get("param") != order.id
        or money_fen(parameters.get("money", "")) != order.amount_fen
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", trade_no)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", parameters.get("type", ""))
    ):
        raise ApiError(400, "PAYMENT_ORDER_MISMATCH", "支付通知与充值订单不一致。")
    if order.status in {"paid", "partially_refunded", "refunded"}:
        if order.gateway_trade_no != trade_no or order.ledger_entry_id is None:
            raise ApiError(409, "PAYMENT_ORDER_MISMATCH", "支付订单状态需要核对。")
        session.rollback()
        return
    claimed = session.execute(
        update(CreditPurchaseRecord)
        .where(
            CreditPurchaseRecord.id == order.id,
            CreditPurchaseRecord.status.in_(("pending", "closed")),
        )
        .values(
            status="paid",
            gateway_trade_no=trade_no,
            paid_at=utc_now(),
            reconcile_next_at=None,
            reconcile_lease_until=None,
            reconcile_lease_token=None,
            reconcile_last_error=None,
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], claimed).rowcount != 1:
        session.rollback()
        # Reload and validate the committed winner; never ACK a missing ledger.
        _settle_verified_purchase(session, parameters)
        return
    account = refresh(session, order.workspace_id)
    terms = order.product_snapshot
    if terms and terms.get("kind") == "membership":
        # A paid renewal starts after all already purchased periods. Even two
        # different plans paid concurrently cannot overlap or lose paid time.
        now = aware(utc_now())
        begins = scheduling_end(session, order.workspace_id, now)
        member = CreditMembershipRecord(
            id=new_id("mem"),
            workspace_id=order.workspace_id,
            purchase_id=order.id,
            plan_id=terms["plan_id"],
            plan_name=order.package_name.split(" · ")[0],
            monthly_credits=order.credits,
            months=terms["months"],
            starts_at=begins,
            ends_at=add_months(begins, terms["months"]),
        )
        session.add(member)
        order.product_snapshot = {
            **terms,
            "starts_at": begins.isoformat(),
            "ends_at": aware(member.ends_at).isoformat(),
        }
        session.flush()
        if begins <= now:
            lot = grant(
                session,
                account,
                f"{order.id}:0",
                order.credits,
                add_months(begins, 1),
                f"{member.plan_name} · 第 1 期积分",
            )
            order.ledger_entry_id = lot.ledger_entry_id
        else:
            entry = CreditLedgerEntryRecord(
                workspace_id=order.workspace_id,
                entry_type="renewal",
                delta_balance_credits=0,
                delta_reserved_credits=0,
                balance_after_credits=account.balance_credits,
                reserved_after_credits=account.reserved_credits,
                description=(
                    f"{order.package_name} · {begins.astimezone(UTC).date()} 生效"
                ),
            )
            session.add(entry)
            session.flush()
            order.ledger_entry_id = entry.id
    else:
        # Orders created before this release retain their original non-expiring
        # pack contract, including pending orders paid after the upgrade.
        expiry = (
            add_months(utc_now(), terms["validity_months"])
            if terms and terms.get("validity_months")
            else None
        )
        lot = grant(
            session,
            account,
            order.id,
            order.credits,
            expiry,
            order.package_name,
            kind="purchase",
        )
        order.ledger_entry_id = lot.ledger_entry_id
    session.add(
        AuditEventRecord(
            workspace_id=order.workspace_id,
            actor_user_id=order.created_by_user_id,
            action="credits.purchase_settled",
            resource_type="credit_purchase",
            resource_id=order.id,
            details={
                "amount_fen": order.amount_fen,
                "credits": order.credits,
                "provider": order.signing_key_id,
            },
        )
    )
    session.commit()


async def _callback_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise ApiError(413, "PAYMENT_CALLBACK_TOO_LARGE", "支付通知过长。")
    return bytes(body)


def _native_transaction(session: Session, channel: str, data: dict) -> None:
    try:
        _settle_native(session, channel, data)
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "PAYMENT_RECEIPT_CONFLICT", "支付流水需要核对。") from None
    except Exception:
        session.rollback()
        raise


@router.post("/api/v1/payments/alipay/notify", response_class=PlainTextResponse)
async def alipay_notify(
    request: Request, session: SessionDependency
) -> PlainTextResponse:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/x-www-form-urlencoded"
    ):
        raise ApiError(415, "INVALID_PAYMENT_CALLBACK", "支付通知必须使用表单。")
    fields = parse_payment_form(await _callback_body(request))
    payments.verify_alipay(request.app.state.settings, fields)
    await run_in_threadpool(_native_transaction, session, "alipay", fields)
    return PlainTextResponse("success")


@router.post("/api/v1/payments/wechat/notify", status_code=204)
async def wechat_notify(request: Request, session: SessionDependency) -> Response:
    settings = request.app.state.settings
    body = await _callback_body(request)
    if settings.credit_wechat_confirmation_mode == "query":
        payments.verify_wechat_query_notice(
            settings, request.headers, body, {"TRANSACTION.SUCCESS"}
        )
        # Authenticated hint only. The durable server query owns fulfillment.
        return Response(status_code=204)
    data = payments.decrypt_wechat(settings, request.headers, body)
    await run_in_threadpool(_native_transaction, session, "wechat", data)
    return Response(status_code=204)
