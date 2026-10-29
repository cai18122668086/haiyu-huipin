"""Persisted payment queries, leases and retries; independent of browser lifetime."""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from . import haiyu_payments as payments
from .config import Settings
from .credit_memberships import aware
from .errors import ApiError
from .models import (
    AuditEventRecord,
    CreditPaymentReconciliationRecord,
    CreditPurchaseRecord,
    utc_now,
)

PAID = {"paid", "partially_refunded", "refunded"}
TERMINAL = {"CLOSED", "REVOKED", "PAYERROR", "TRADE_CLOSED"}
LEASE_SECONDS = 60
BATCH_SIZE = 20


@dataclass(frozen=True)
class Claim:
    id: str
    channel: str
    identity: dict[str, str]
    merchant: str
    token: str


def _current_identity(settings: Settings, channel: str) -> dict[str, str]:
    if channel == "alipay":
        return {
            "app_id": settings.credit_alipay_app_id,
            "merchant_id": settings.credit_alipay_seller_id,
        }
    if channel == "wechat":
        return {
            "app_id": settings.credit_wechat_app_id,
            "merchant_id": settings.credit_wechat_mch_id,
        }
    return {}


def _claim(session: Session, order_id: str, manual: bool) -> Claim | None:
    now = aware(utc_now())
    if session.get_bind().dialect.name == "sqlite":
        session.execute(
            update(CreditPurchaseRecord)
            .where(CreditPurchaseRecord.id == order_id)
            .values(reconcile_attempts=CreditPurchaseRecord.reconcile_attempts)
        )
    order = session.scalar(
        select(CreditPurchaseRecord)
        .where(CreditPurchaseRecord.id == order_id)
        .with_for_update(skip_locked=not manual)
        .execution_options(populate_existing=True)
    )
    if order is None:
        session.rollback()
        return None
    busy = order.reconcile_lease_until and aware(order.reconcile_lease_until) > now
    cooling = (
        manual
        and order.reconcile_last_at
        and (aware(order.reconcile_last_at) > now - timedelta(seconds=5))
    )
    not_due = (
        not manual
        and order.reconcile_next_at
        and (aware(order.reconcile_next_at) > now)
    )
    if order.status != "pending" or busy or cooling or not_due:
        session.rollback()
        return None
    token = uuid4().hex
    order.reconcile_lease_token = token
    order.reconcile_lease_until = now + timedelta(seconds=LEASE_SECONDS)
    order.reconcile_attempts += 1
    identity = (order.product_snapshot or {}).get("payment_identity", {})
    claim = Claim(
        order.id, order.signing_key_id, dict(identity), order.merchant_pid, token
    )
    session.commit()
    session.expunge_all()
    session.rollback()
    return claim


def _delay(order: CreditPurchaseRecord, now: datetime, error: str | None) -> int:
    age = (now - aware(order.created_at)).total_seconds()
    if error == "PAYMENT_MERCHANT_UNAVAILABLE":
        return 300
    if error:
        return 3600 if age >= 86400 else min(600, 30 * max(1, order.reconcile_attempts))
    if age < 120:
        return 10
    if age < 1800:
        return 30
    if age < 86400:
        return 300
    return 3600


def _finish(
    session: Session,
    claim: Claim,
    provider_status: str | None,
    error: str | None,
    initiator: str,
) -> CreditPurchaseRecord:
    now = aware(utc_now())
    order = session.get(
        CreditPurchaseRecord, claim.id, with_for_update=True, populate_existing=True
    )
    assert order is not None
    if order.status in PAID:
        order.reconcile_next_at = None
        order.reconcile_lease_until = None
        order.reconcile_lease_token = None
        order.reconcile_last_error = None
        order.reconcile_last_status = "SUCCESS"
        order.reconcile_last_at = now
    elif order.reconcile_lease_token == claim.token:
        order.reconcile_last_at = now
        order.reconcile_last_error = error
        order.reconcile_last_status = provider_status
        order.reconcile_lease_until = None
        order.reconcile_lease_token = None
        if not error and provider_status in TERMINAL:
            order.status = "closed"
            order.reconcile_next_at = None
            session.add(
                AuditEventRecord(
                    workspace_id=order.workspace_id,
                    action="credits.purchase_closed",
                    resource_type="credit_purchase",
                    resource_id=order.id,
                    details={
                        "provider_status": provider_status,
                        "initiator": initiator,
                    },
                )
            )
        else:
            order.reconcile_next_at = now + timedelta(seconds=_delay(order, now, error))
    session.commit()
    return order


def process(
    session: Session,
    settings: Settings,
    order_id: str,
    *,
    manual: bool = False,
    initiator: str = "background",
    raise_errors: bool = False,
) -> CreditPurchaseRecord | None:
    claim = _claim(session, order_id, manual)
    if claim is None:
        return session.get(CreditPurchaseRecord, order_id, populate_existing=True)
    error: ApiError | None = None
    provider_status = None
    try:
        current = _current_identity(settings, claim.channel)
        if (
            not current
            or not claim.identity
            or current != claim.identity
            or current.get("merchant_id") != claim.merchant
        ):
            raise ApiError(
                503, "PAYMENT_MERCHANT_UNAVAILABLE", "原收款商户配置需要核对。"
            )
        # The signed query and credit settlement deliberately hold no DB lease
        # connection/row locks across the remote request.
        data = payments.query_payment(settings, claim.channel, claim.id)
        if data.get("out_trade_no") != claim.id:
            raise ApiError(400, "PAYMENT_ORDER_MISMATCH", "支付查询返回了其他订单。")
        if claim.channel == "alipay":
            data.setdefault("seller_id", current["merchant_id"])
        from .credit_payment_routes import _native_transaction

        _native_transaction(session, claim.channel, data)
        value = data.get("trade_state" if claim.channel == "wechat" else "trade_status")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z_]{1,40}", value):
            raise ApiError(502, "PAYMENT_QUERY_INVALID_STATE", "渠道支付状态需要核对。")
        provider_status = value
        if value == "REFUND":
            raise ApiError(
                409, "PAYMENT_REFUND_REVIEW_REQUIRED", "渠道订单已转入退款，请核对。"
            )
    except ApiError as exc:
        error = exc
        session.rollback()
    except Exception:
        error = ApiError(
            502, "PAYMENT_QUERY_UNAVAILABLE", "支付渠道暂时无法查询，请稍后重试。"
        )
        session.rollback()
    code = (
        error.provider_code
        if isinstance(error, payments.PaymentProviderError)
        else error.code
        if error
        else None
    )
    order = _finish(session, claim, provider_status, code, initiator)
    if error and raise_errors and order.status not in PAID:
        raise error
    return order


def maintenance(factory: sessionmaker[Session], settings: Settings) -> None:
    if not settings.credit_payment_reconciliation_enabled:
        return
    now = utc_now()
    with factory() as session:
        ids = list(
            session.scalars(
                select(CreditPurchaseRecord.id)
                .where(
                    CreditPurchaseRecord.status == "pending",
                    or_(
                        CreditPurchaseRecord.reconcile_next_at.is_(None),
                        CreditPurchaseRecord.reconcile_next_at <= now,
                    ),
                    or_(
                        CreditPurchaseRecord.reconcile_lease_until.is_(None),
                        CreditPurchaseRecord.reconcile_lease_until <= now,
                    ),
                )
                .order_by(
                    CreditPurchaseRecord.reconcile_next_at.asc().nullsfirst(),
                    CreditPurchaseRecord.created_at,
                    CreditPurchaseRecord.id,
                )
                .limit(BATCH_SIZE)
            )
        )

    def reconcile(identifier: str) -> tuple[bool, bool]:
        with factory() as session:
            row = process(session, settings, identifier)
            return bool(row and row.status in PAID), bool(
                row and row.reconcile_last_error
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(reconcile, ids))
    with factory() as session:
        session.merge(
            CreditPaymentReconciliationRecord(
                id="payments",
                last_completed_at=utc_now(),
                checked_count=len(results),
                confirmed_count=sum(paid for paid, _ in results),
                error_count=sum(error for _, error in results),
                last_error=None,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # Another API replica may have inserted the heartbeat first.
            session.rollback()
            session.execute(
                update(CreditPaymentReconciliationRecord)
                .where(CreditPaymentReconciliationRecord.id == "payments")
                .values(last_completed_at=utc_now())
            )
            session.commit()


async def maintenance_loop(factory: sessionmaker[Session], settings: Settings) -> None:
    while True:
        try:
            await run_in_threadpool(maintenance, factory, settings)
        except Exception:
            logging.getLogger(__name__).exception("Payment reconciliation cycle failed")
            with factory() as session:
                row = session.get(CreditPaymentReconciliationRecord, "payments")
                if row:
                    row.last_error = "RECONCILIATION_CYCLE_FAILED"
                    session.commit()
        await asyncio.sleep(5)


def status(session: Session, settings: Settings) -> dict[str, Any]:
    now = aware(utc_now())
    row = session.get(CreditPaymentReconciliationRecord, "payments")
    return {
        "enabled": settings.credit_payment_reconciliation_enabled,
        "wechat_confirmation_mode": settings.credit_wechat_confirmation_mode,
        "healthy": bool(
            settings.credit_payment_reconciliation_enabled
            and row
            and not row.last_error
            and (now - aware(row.last_completed_at)).total_seconds() < 120
        ),
        "last_completed_at": aware(row.last_completed_at).isoformat() if row else None,
        "checked_count": row.checked_count if row else 0,
        "confirmed_count": row.confirmed_count if row else 0,
        "last_error": row.last_error if row else None,
        "pending_orders": session.scalar(
            select(func.count())
            .select_from(CreditPurchaseRecord)
            .where(CreditPurchaseRecord.status == "pending")
        ),
        "review_orders": session.scalar(
            select(func.count())
            .select_from(CreditPurchaseRecord)
            .where(
                CreditPurchaseRecord.status == "pending",
                CreditPurchaseRecord.reconcile_last_error.is_not(None),
            )
        ),
    }
