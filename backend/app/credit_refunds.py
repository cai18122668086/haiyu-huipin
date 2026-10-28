"""Persist refund intent and withdraw source-bound rights before external I/O."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import haiyu_payments as payments
from .config import Settings
from .credit_memberships import add_months, aware, refresh
from .errors import ApiError
from .models import (
    AuditEventRecord,
    CreditGrantRecord,
    CreditLedgerEntryRecord,
    CreditMembershipRecord,
    CreditPurchaseRecord,
    CreditRefundRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from .services import new_id, require_idempotency_key

ACTIVE = ("requested", "processing")
Policy = Literal["unused", "goodwill"]


def _order(session: Session, order_id: str) -> CreditPurchaseRecord:
    order = session.get(CreditPurchaseRecord, order_id)
    if order is None:
        raise ApiError(404, "CREDIT_PURCHASE_NOT_FOUND", "订单不存在。")
    return order


def _sources(
    session: Session, order: CreditPurchaseRecord
) -> tuple[list[CreditGrantRecord], CreditMembershipRecord | None]:
    member = session.scalar(
        select(CreditMembershipRecord).where(
            CreditMembershipRecord.purchase_id == order.id
        )
    )
    keys = (
        [order.id]
        if member is None
        else [f"{order.id}:{i}" for i in range(member.months)]
    )
    lots = list(
        session.scalars(
            select(CreditGrantRecord)
            .where(
                CreditGrantRecord.workspace_id == order.workspace_id,
                CreditGrantRecord.source_key.in_(keys),
            )
            .order_by(CreditGrantRecord.id)
        )
    )
    return lots, member


def _quote(
    session: Session, order: CreditPurchaseRecord, policy: Policy
) -> dict[str, Any]:
    if order.status not in {"paid", "partially_refunded", "refunded"}:
        raise ApiError(
            409, "REFUND_REQUIRES_PAID_ORDER", "只能为已确认到账的订单退款。"
        )
    if order.signing_key_id not in {"alipay", "wechat"}:
        raise ApiError(
            409,
            "UNSUPPORTED_REFUND_CHANNEL",
            "历史支付渠道需人工核对，不能从本接口退款。",
        )
    if session.scalar(
        select(CreditRefundRecord.id)
        .where(
            CreditRefundRecord.purchase_id == order.id,
            CreditRefundRecord.status.in_(ACTIVE),
        )
        .limit(1)
    ):
        raise ApiError(
            409, "REFUND_IN_PROGRESS", "这笔订单已有退款处理中，请核对原退款单。"
        )
    lots, member = _sources(session, order)
    now = aware(utc_now())
    future = (
        sum(
            member.monthly_credits
            for i in range(member.months)
            if add_months(member.starts_at, i) > now
        )
        if member and member.cancelled_at is None
        else 0
    )
    remaining = sum(lot.remaining for lot in lots)
    held = sum(lot.held for lot in lots)
    total_units = order.credits * (member.months if member else 1)
    unpaid_refund = order.amount_fen - order.refunded_amount_fen
    cap = (
        unpaid_refund
        if policy == "goodwill"
        else min(unpaid_refund, order.amount_fen * (remaining + future) // total_units)
    )
    data = {
        "purchase_id": order.id,
        "policy": policy,
        "original_amount_fen": order.amount_fen,
        "already_refunded_fen": order.refunded_amount_fen,
        "max_refundable_amount_fen": cap,
        "credits_to_revoke": remaining,
        "future_credits_to_cancel": future,
        "frozen_credits": held,
        "membership_will_cancel": bool(member and member.cancelled_at is None),
        "membership_ends_at": aware(member.ends_at).isoformat() if member else None,
    }
    fingerprint = {
        **data,
        "lots": [(lot.id, lot.remaining, lot.held) for lot in lots],
        "cancelled_at": aware(member.cancelled_at).isoformat()
        if member and member.cancelled_at
        else None,
    }
    data["quote_token"] = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True).encode()
    ).hexdigest()
    return data


def quote(session: Session, order_id: str, policy: Policy = "unused") -> dict[str, Any]:
    order = _order(session, order_id)
    refresh(session, order.workspace_id)
    session.refresh(order)
    result = _quote(session, order, policy)
    session.commit()
    return result


def response(refund: CreditRefundRecord) -> dict[str, Any]:
    return {
        "id": refund.id,
        "purchase_id": refund.purchase_id,
        "workspace_id": refund.workspace_id,
        "amount_fen": refund.amount_fen,
        "policy": refund.policy,
        "reason": refund.reason,
        "status": refund.status,
        "provider_refund_id": refund.provider_refund_id,
        "created_at": aware(refund.created_at).isoformat(),
        "completed_at": aware(refund.completed_at).isoformat()
        if refund.completed_at
        else None,
        "credits_revoked": refund.entitlement_snapshot.get("credits_revoked", 0),
        "future_credits_cancelled": refund.entitlement_snapshot.get(
            "future_credits_cancelled", 0
        ),
        "last_error_code": refund.last_error_code,
        "attempts": refund.attempts,
    }


def _entry(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    kind: str,
    delta: int,
    title: str,
) -> CreditLedgerEntryRecord:
    entry = CreditLedgerEntryRecord(
        workspace_id=account.workspace_id,
        entry_type=kind,
        delta_balance_credits=delta,
        delta_reserved_credits=0,
        balance_after_credits=account.balance_credits,
        reserved_after_credits=account.reserved_credits,
        description=title[:200],
    )
    session.add(entry)
    session.flush()
    return entry


def create(
    session: Session,
    order_id: str,
    actor_id: str,
    *,
    key: str,
    reason: str,
    policy: Policy,
    quote_token: str,
    amount_fen: int | None = None,
) -> CreditRefundRecord:
    key = require_idempotency_key(key)
    if len(reason.strip()) < 5 or len(reason) > 160:
        raise ApiError(422, "REFUND_REASON_REQUIRED", "请填写 5 至 160 字的退款原因。")
    order = _order(session, order_id)
    if order.status == "pending":
        raise ApiError(409, "REFUND_REQUIRES_PAID_ORDER", "只能为已到账订单退款。")
    payload_hash = hashlib.sha256(
        json.dumps(
            {
                "order": order_id,
                "actor": actor_id,
                "reason": reason,
                "policy": policy,
                "quote": quote_token,
                "amount": amount_fen,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    # Wallet lock serializes different refund requests and all grant/consumption paths.
    account = refresh(session, order.workspace_id)
    locked_order = session.get(
        CreditPurchaseRecord, order_id, with_for_update=True, populate_existing=True
    )
    assert locked_order is not None
    order = locked_order
    existing = session.scalar(
        select(CreditRefundRecord).where(
            CreditRefundRecord.workspace_id == order.workspace_id,
            CreditRefundRecord.created_by_user_id == actor_id,
            CreditRefundRecord.idempotency_key == key,
        )
    )
    if existing:
        if existing.request_hash != payload_hash:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "该退款请求已用于其他内容。")
        session.rollback()
        return existing
    terms = _quote(session, order, policy)
    if terms["quote_token"] != quote_token:
        raise ApiError(
            409, "REFUND_QUOTE_STALE", "积分或订单状态已变化，请重新核对退款。"
        )
    if terms["frozen_credits"]:
        raise ApiError(
            409, "REFUND_CREDITS_IN_USE", "该订单的积分正在生成中，请待任务结算后退款。"
        )
    amount = terms["max_refundable_amount_fen"] if amount_fen is None else amount_fen
    if (
        type(amount) is not int
        or amount <= 0
        or amount > terms["max_refundable_amount_fen"]
    ):
        raise ApiError(422, "REFUND_AMOUNT_INVALID", "退款金额超过当前核对的可退额度。")
    lots, member = _sources(session, order)
    snapshot: dict[str, Any] = {
        "lots": [
            {"id": lot.id, "amount": lot.remaining} for lot in lots if lot.remaining
        ],
        "credits_revoked": terms["credits_to_revoke"],
        "future_credits_cancelled": terms["future_credits_to_cancel"],
        "member_id": member.id if member else None,
        "member_cancelled_at": aware(member.cancelled_at).isoformat()
        if member and member.cancelled_at
        else None,
        "member_cancellation_reason": member.cancellation_reason if member else None,
    }
    for lot in lots:
        lot.remaining = 0
    account.balance_credits -= terms["credits_to_revoke"]
    account.version += 1
    if member and member.cancelled_at is None:
        member.cancelled_at = utc_now()
        member.cancellation_reason = "退款处理中 · " + reason.strip()
    refund = CreditRefundRecord(
        id=new_id("ref")[:32],
        workspace_id=order.workspace_id,
        purchase_id=order.id,
        created_by_user_id=actor_id,
        idempotency_key=key,
        request_hash=payload_hash,
        amount_fen=amount,
        reason=reason.strip(),
        policy=policy,
        status="requested",
        entitlement_snapshot=snapshot,
        next_retry_at=utc_now(),
    )
    refund.ledger_entry_id = _entry(
        session,
        account,
        "refund_reserve",
        -terms["credits_to_revoke"],
        "退款权益预留 · " + order.package_name,
    ).id
    session.add(refund)
    session.add(
        AuditEventRecord(
            workspace_id=order.workspace_id,
            actor_user_id=actor_id,
            action="credits.refund_requested",
            resource_type="credit_refund",
            resource_id=refund.id,
            details={
                "purchase_id": order.id,
                "amount_fen": amount,
                "reason": reason.strip(),
                "policy": policy,
                "credits_revoked": terms["credits_to_revoke"],
            },
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ApiError(
            409, "REFUND_CONCURRENT_REQUEST", "退款请求已更新，请核对原记录。"
        ) from None
    return refund


def _restore(
    session: Session, account: WorkspaceCreditAccountRecord, refund: CreditRefundRecord
) -> None:
    snapshot = refund.entitlement_snapshot
    now = aware(utc_now())
    restored = 0
    for item in snapshot.get("lots", []):
        lot = session.get(CreditGrantRecord, item["id"])
        if (
            lot is None
            or lot.workspace_id != refund.workspace_id
            or lot.remaining
            or lot.held
        ):
            raise ApiError(409, "REFUND_RIGHTS_MISMATCH", "退款权益来源需要核对。")
        if lot.expires_at is None or aware(lot.expires_at) > now:
            lot.remaining = item["amount"]
            restored += item["amount"]
    account.balance_credits += restored
    account.version += 1
    if snapshot.get("member_id"):
        member = session.get(CreditMembershipRecord, snapshot["member_id"])
        assert member is not None
        member.cancelled_at = (
            datetime.fromisoformat(snapshot["member_cancelled_at"])
            if snapshot["member_cancelled_at"]
            else None
        )
        member.cancellation_reason = snapshot["member_cancellation_reason"]
    _entry(session, account, "refund_release", restored, "退款未成功，恢复有效权益")


def settle(session: Session, refund_id: str, result: dict[str, Any]) -> None:
    initial = session.get(CreditRefundRecord, refund_id)
    if initial is None:
        raise ApiError(404, "REFUND_NOT_FOUND", "退款单不存在。")
    account = refresh(session, initial.workspace_id)
    refund = session.get(
        CreditRefundRecord, refund_id, with_for_update=True, populate_existing=True
    )
    assert refund is not None
    if refund.status in {"succeeded", "failed"}:
        if (
            result["status"] in {"succeeded", "failed"}
            and result["status"] != refund.status
        ):
            raise ApiError(
                409, "REFUND_STATE_CONFLICT", "渠道退款状态发生冲突，需人工核对。"
            )
        session.rollback()
        return
    status = result["status"]
    if status not in {"succeeded", "failed"}:
        refund.status = "processing"
        refund.lease_until = None
        refund.next_retry_at = utc_now() + timedelta(
            seconds=min(3600, 30 * max(1, refund.attempts))
        )
        refund.last_error_code = result.get("error_code")
        session.commit()
        return
    order = session.get(
        CreditPurchaseRecord,
        refund.purchase_id,
        with_for_update=True,
        populate_existing=True,
    )
    assert order is not None
    if status == "succeeded":
        if order.refunded_amount_fen + refund.amount_fen > order.amount_fen:
            raise ApiError(409, "REFUND_AMOUNT_CONFLICT", "累计退款金额需要核对。")
        order.refunded_amount_fen += refund.amount_fen
        order.status = (
            "refunded"
            if order.refunded_amount_fen == order.amount_fen
            else "partially_refunded"
        )
        _entry(session, account, "refund", 0, "渠道退款已成功 · " + order.package_name)
    else:
        _restore(session, account, refund)
    refund.status = status
    refund.provider_refund_id = result.get("provider_refund_id")
    refund.last_error_code = result.get("error_code")
    refund.completed_at = utc_now()
    refund.lease_until = None
    refund.next_retry_at = None
    session.add(
        AuditEventRecord(
            workspace_id=refund.workspace_id,
            actor_user_id=refund.created_by_user_id,
            action="credits.refund_" + status,
            resource_type="credit_refund",
            resource_id=refund.id,
            details={
                "purchase_id": order.id,
                "amount_fen": refund.amount_fen,
                "provider_refund_id": refund.provider_refund_id,
            },
        )
    )
    session.commit()


def _configured_identity(settings: Settings, order: CreditPurchaseRecord) -> None:
    expected = (order.product_snapshot or {}).get("payment_identity", {})
    current = (
        {
            "app_id": settings.credit_alipay_app_id,
            "merchant_id": settings.credit_alipay_seller_id,
        }
        if order.signing_key_id == "alipay"
        else {
            "app_id": settings.credit_wechat_app_id,
            "merchant_id": settings.credit_wechat_mch_id,
        }
    )
    if not expected or expected != current:
        raise ApiError(
            503, "REFUND_MERCHANT_UNAVAILABLE", "原收款商户配置需要核对，暂不发起退款。"
        )


def process(session: Session, settings: Settings, refund_id: str) -> CreditRefundRecord:
    now = aware(utc_now())
    if session.get_bind().dialect.name == "sqlite":
        session.execute(
            update(CreditRefundRecord)
            .where(CreditRefundRecord.id == refund_id)
            .values(attempts=CreditRefundRecord.attempts)
        )
    refund = session.get(
        CreditRefundRecord, refund_id, with_for_update=True, populate_existing=True
    )
    if refund is None:
        raise ApiError(404, "REFUND_NOT_FOUND", "退款单不存在。")
    if refund.status not in ACTIVE or (
        refund.lease_until and aware(refund.lease_until) > now
    ):
        session.rollback()
        return refund
    order = _order(session, refund.purchase_id)
    _configured_identity(settings, order)
    first_attempt = refund.attempts == 0
    channel, order_id, amount, total, reason = (
        order.signing_key_id,
        order.id,
        refund.amount_fen,
        order.amount_fen,
        refund.reason,
    )
    refund.lease_until = utc_now() + timedelta(seconds=60)
    refund.attempted_at = utc_now()
    refund.attempts += 1
    session.commit()
    # No database locks or connections are held during payment-provider I/O.
    session.expunge_all()
    session.rollback()
    try:
        result = (
            {"status": "not_found"}
            if first_attempt
            else payments.query_refund(
                settings, channel, order_id, refund_id, amount, total
            )
        )
        if result["status"] == "not_found":
            result = payments.create_refund(
                settings,
                channel,
                order_id,
                refund_id,
                amount,
                total,
                reason.encode("utf-8")[:80].decode("utf-8", errors="ignore"),
            )
    except payments.PaymentProviderError as error:
        result = {
            "status": "failed" if first_attempt and error.definitive else "processing",
            "error_code": error.provider_code,
        }
    except Exception:
        # A timeout or bad response never proves that money was not refunded.
        result = {"status": "processing", "error_code": "PROVIDER_RESULT_UNKNOWN"}
    settle(session, refund_id, result)
    current = session.get(CreditRefundRecord, refund_id, populate_existing=True)
    assert current is not None
    return current


def maintenance(session_factory: sessionmaker[Session], settings: Settings) -> None:
    with session_factory() as session:
        ids = list(
            session.scalars(
                select(CreditRefundRecord.id)
                .where(
                    CreditRefundRecord.status.in_(ACTIVE),
                    CreditRefundRecord.next_retry_at <= utc_now(),
                )
                .order_by(CreditRefundRecord.created_at)
                .limit(10)
            )
        )
    for refund_id in ids:
        with session_factory() as session:
            try:
                process(session, settings, refund_id)
            except Exception:
                session.rollback()
