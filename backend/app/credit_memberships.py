"""Calendar-month grants, expiring credit lots and reservation allocations.

Every mutation holds the wallet row lock. API reads, generation and maintenance
use the same transaction path, so delayed maintenance cannot extend validity or
make a yearly purchase issue twelve months of spendable credits at once.
"""

import calendar
from datetime import UTC, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .errors import ApiError
from .models import (
    CreditGrantRecord,
    CreditLedgerEntryRecord,
    CreditMembershipRecord,
    CreditRefundRecord,
    CreditReservationRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def add_months(anchor: datetime, count: int) -> datetime:
    """Anchor every period on the purchase day, including Jan 31 / leap years."""
    anchor = aware(anchor).astimezone(ZoneInfo("Asia/Shanghai"))
    index = anchor.year * 12 + anchor.month - 1 + count
    year, month = divmod(index, 12)
    month += 1
    return anchor.replace(
        year=year, month=month, day=min(anchor.day, calendar.monthrange(year, month)[1])
    ).astimezone(UTC)


def _id(prefix: str, source: str) -> str:
    return prefix + sha256(source.encode()).hexdigest()[:32]


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
        description=title,
    )
    session.add(entry)
    session.flush()
    return entry


def grant(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    source: str,
    amount: int,
    expires_at: datetime | None,
    title: str,
    *,
    kind: str = "membership",
    increment: bool = True,
) -> CreditGrantRecord:
    identifier = _id("lot_", account.workspace_id + ":" + source)
    existing = session.get(CreditGrantRecord, identifier)
    if existing is not None:
        return existing
    if increment:
        account.balance_credits += amount
        account.version += 1
    entry = _entry(session, account, kind, amount, title) if increment else None
    lot = CreditGrantRecord(
        id=identifier,
        workspace_id=account.workspace_id,
        source_key=source,
        kind=kind,
        amount=amount,
        remaining=amount,
        held=0,
        expires_at=expires_at,
        ledger_entry_id=entry.id if entry else None,
    )
    session.add(lot)
    session.flush()
    return lot


def _lots(session: Session, workspace_id: str) -> list[CreditGrantRecord]:
    # Expiring credits first; non-expiring historical balances last.
    return list(
        session.scalars(
            select(CreditGrantRecord)
            .where(CreditGrantRecord.workspace_id == workspace_id)
            .order_by(
                CreditGrantRecord.expires_at.is_(None),
                CreditGrantRecord.expires_at,
                CreditGrantRecord.created_at,
                CreditGrantRecord.id,
            )
        )
    )


def allocate(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    reservation: CreditReservationRecord,
    now: datetime,
) -> None:
    needed = reservation.reserved_credits
    allocations: dict[str, int] = {}
    for lot in _lots(session, account.workspace_id):
        if lot.expires_at is not None and aware(lot.expires_at) <= now:
            continue
        value = min(needed, lot.remaining - lot.held)
        if value:
            lot.held += value
            allocations[lot.id] = value
            needed -= value
        if not needed:
            break
    if needed:
        raise ApiError(409, "CREDIT_LOT_MISMATCH", "积分来源暂未核对完成，请稍后重试。")
    reservation.credit_allocations = allocations
    session.flush()


def initialize(
    session: Session, account: WorkspaceCreditAccountRecord, now: datetime
) -> None:
    if account.lots_initialized:
        return
    # An upgrade preserves every old balance and in-flight reservation, without
    # imposing a new expiry or tariff on previously purchased credits.
    if account.balance_credits:
        grant(
            session,
            account,
            "legacy",
            account.balance_credits,
            None,
            "历史积分余额",
            kind="legacy",
            increment=False,
        )
    for reservation in session.scalars(
        select(CreditReservationRecord)
        .where(
            CreditReservationRecord.workspace_id == account.workspace_id,
            CreditReservationRecord.status == "active",
        )
        .order_by(CreditReservationRecord.created_at, CreditReservationRecord.id)
    ):
        held_entry = session.scalar(
            select(CreditLedgerEntryRecord).where(
                CreditLedgerEntryRecord.reservation_id == reservation.id,
                CreditLedgerEntryRecord.entry_type == "reserve",
            )
        )
        if held_entry is None or held_entry.delta_reserved_credits > 0:
            allocate(session, account, reservation, now)
    account.lots_initialized = True
    session.flush()


def expire(
    session: Session, account: WorkspaceCreditAccountRecord, now: datetime
) -> None:
    for lot in _lots(session, account.workspace_id):
        if lot.expires_at is None or aware(lot.expires_at) > now:
            continue
        amount = lot.remaining - lot.held
        if amount:
            lot.remaining -= amount
            account.balance_credits -= amount
            account.version += 1
            _entry(
                session,
                account,
                "expire",
                -amount,
                "积分到期 · "
                + {"membership": "会员积分", "purchase": "充值积分"}.get(
                    lot.kind, "创作积分"
                ),
            )
    session.flush()


def refresh(
    session: Session, workspace_id: str, *, now: datetime | None = None
) -> WorkspaceCreditAccountRecord:
    now = aware(now or utc_now())
    if session.get_bind().dialect.name == "sqlite":
        # SQLite ignores SELECT FOR UPDATE. Acquire its writer lock before
        # loading state, so concurrent local/API reads cannot seed twice.
        session.execute(
            update(WorkspaceCreditAccountRecord)
            .where(WorkspaceCreditAccountRecord.workspace_id == workspace_id)
            .values(version=WorkspaceCreditAccountRecord.version)
        )
    account = session.get(
        WorkspaceCreditAccountRecord,
        workspace_id,
        with_for_update=True,
        populate_existing=True,
    )
    if account is None:
        raise ApiError(409, "CREDIT_ACCOUNT_NOT_CONFIGURED", "积分账户尚未配置。")
    initialize(session, account, now)
    for member in session.scalars(
        select(CreditMembershipRecord)
        .where(
            CreditMembershipRecord.workspace_id == workspace_id,
            CreditMembershipRecord.starts_at <= now,
            CreditMembershipRecord.cancelled_at.is_(None),
        )
        .order_by(CreditMembershipRecord.starts_at, CreditMembershipRecord.id)
    ):
        for index in range(member.months):
            start = add_months(member.starts_at, index)
            if start > now:
                break
            grant(
                session,
                account,
                f"{member.purchase_id or member.id}:{index}",
                member.monthly_credits,
                add_months(member.starts_at, index + 1),
                f"{member.plan_name} · 第 {index + 1} 期积分",
            )
    expire(session, account, now)
    return account


def finish_allocation(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    reservation: CreditReservationRecord,
    charged: int,
) -> None:
    if reservation.credit_allocations is None:
        # Historical/unmanaged reservations keep their original financial mode.
        return
    remaining_charge = charged
    for identifier, value in reservation.credit_allocations.items():
        lot = session.get(CreditGrantRecord, identifier)
        if lot is None or lot.workspace_id != account.workspace_id or lot.held < value:
            raise ApiError(409, "CREDIT_LOT_MISMATCH", "积分冻结记录需要核对。")
        consumed = min(remaining_charge, value)
        lot.held -= value
        lot.remaining -= consumed
        remaining_charge -= consumed
    if remaining_charge:
        raise ApiError(409, "CREDIT_LOT_MISMATCH", "积分结算记录需要核对。")
    session.flush()
    expire(session, account, aware(utc_now()))


def membership_state(
    session: Session, workspace_id: str, now: datetime | None = None
) -> dict:
    now = aware(now or utc_now())
    members = list(
        session.scalars(
            select(CreditMembershipRecord)
            .where(
                CreditMembershipRecord.workspace_id == workspace_id,
                CreditMembershipRecord.ends_at > now,
                CreditMembershipRecord.cancelled_at.is_(None),
            )
            .order_by(CreditMembershipRecord.starts_at)
        )
    )
    current = next((row for row in members if aware(row.starts_at) <= now), None)
    upcoming = next((row for row in members if aware(row.starts_at) > now), None)
    previous = (
        session.scalar(
            select(CreditMembershipRecord)
            .where(
                CreditMembershipRecord.workspace_id == workspace_id,
                CreditMembershipRecord.ends_at <= now,
                CreditMembershipRecord.cancelled_at.is_(None),
            )
            .order_by(CreditMembershipRecord.ends_at.desc())
            .limit(1)
        )
        if current is None
        else None
    )
    lots = _lots(session, workspace_id)
    account = session.get(WorkspaceCreditAccountRecord, workspace_id)
    legacy_balance = (
        account.balance_credits if account and not account.lots_initialized else None
    )
    expiring = [
        lot
        for lot in lots
        if lot.expires_at is not None
        and lot.remaining > lot.held
        and aware(lot.expires_at) > now
    ]
    next_expiry = min(
        (aware(lot.expires_at) for lot in expiring if lot.expires_at is not None),
        default=None,
    )
    next_grant = None
    if current:
        next_grant = next(
            (
                add_months(current.starts_at, i)
                for i in range(1, current.months)
                if add_months(current.starts_at, i) > now
            ),
            None,
        )
    return {
        "plan_id": current.plan_id if current else None,
        "plan_name": current.plan_name if current else None,
        "monthly_credits": current.monthly_credits if current else 0,
        "starts_at": aware(current.starts_at).isoformat() if current else None,
        "ends_at": aware(current.ends_at).isoformat() if current else None,
        "membership_status": "active"
        if current
        else "scheduled"
        if upcoming
        else "expired"
        if previous
        else "none",
        "previous_plan_name": previous.plan_name if previous else None,
        "next_plan_name": upcoming.plan_name if upcoming else None,
        "next_plan_starts_at": aware(upcoming.starts_at).isoformat()
        if upcoming
        else None,
        "next_grant_at": next_grant.isoformat() if next_grant else None,
        "next_expiry_at": next_expiry.isoformat() if next_expiry else None,
        "expiring_credits": sum(
            lot.remaining - lot.held
            for lot in expiring
            if lot.expires_at is not None and aware(lot.expires_at) == next_expiry
        ),
        "member_credits": sum(
            lot.remaining for lot in lots if lot.kind == "membership"
        ),
        "pack_credits": legacy_balance
        if legacy_balance is not None
        else sum(lot.remaining for lot in lots if lot.kind != "membership"),
    }


def scheduling_end(session: Session, workspace_id: str, now: datetime) -> datetime:
    """Reserve refund-pending periods until the original refund is definitive."""
    end = aware(now)
    for member in session.scalars(
        select(CreditMembershipRecord).where(
            CreditMembershipRecord.workspace_id == workspace_id,
            CreditMembershipRecord.ends_at > now,
        )
    ):
        pending = member.purchase_id and session.scalar(
            select(CreditRefundRecord.id)
            .where(
                CreditRefundRecord.purchase_id == member.purchase_id,
                CreditRefundRecord.status.in_(("requested", "processing")),
            )
            .limit(1)
        )
        if member.cancelled_at is None or pending:
            end = max(end, aware(member.ends_at))
    return end


def maintenance(session_factory: sessionmaker[Session]) -> None:
    """Also process purchased memberships when their owner is offline."""
    with session_factory() as session:
        workspaces = list(
            session.scalars(
                select(CreditGrantRecord.workspace_id).union(
                    select(CreditMembershipRecord.workspace_id)
                )
            )
        )
    for workspace_id in workspaces:
        with session_factory() as session:
            refresh(session, workspace_id)
            session.commit()


async def maintenance_loop(
    session_factory: sessionmaker[Session], settings: Settings | None = None
) -> None:
    import asyncio
    import logging

    from starlette.concurrency import run_in_threadpool

    while True:
        await asyncio.sleep(60)
        try:
            await run_in_threadpool(maintenance, session_factory)
            if settings is not None:
                from .credit_refunds import maintenance as refund_maintenance

                await run_in_threadpool(refund_maintenance, session_factory, settings)
        except Exception:
            # Reads and job submission also reconcile due grants before use.
            logging.getLogger(__name__).exception("Membership maintenance failed")
