"""Daily promotional credits and one-level rewards on verified paid credit lots.

Wallet locks precede reward/adjustment locks. Attribution only points from a new
signup to an older account, so paid issuance follows that same acyclic order.
"""

import re
import secrets
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .credit_memberships import aware, grant, refresh
from .errors import ApiError
from .models import (
    AuditEventRecord,
    CreditGrantRecord,
    CreditLedgerEntryRecord,
    CreditPurchaseRecord,
    CreditRefundRecord,
    DailyCheckinRecord,
    GrowthDebtRecord,
    InvitationCodeRecord,
    InvitationRecord,
    PromotionCampaignRecord,
    ReferralReversalRecord,
    ReferralRewardRecord,
    UserRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from .onboarding import personal_workspace_id

CHECKIN = "daily-checkin-v1"
REFERRAL = "referral-credits-v1"
DAILY_CREDITS = 10
REWARD_BPS = 1000
BONUS_VALIDITY_DAYS = 30
INVITE_PATTERN = re.compile(r"HY[A-F0-9]{10}")


def active(session: Session, campaign_id: str) -> bool:
    campaign = session.get(PromotionCampaignRecord, campaign_id)
    return bool(
        campaign and campaign.enabled and aware(campaign.starts_at) <= aware(utc_now())
    )


def eligible(user: UserRecord | None) -> bool:
    return bool(user and user.status == "active" and user.phone_number)


def phone_key(user: UserRecord) -> str:
    return sha256((user.phone_number or "").encode()).hexdigest()


def bind_signup_invitation(session: Session, user: UserRecord, code: object) -> None:
    """Called only while completing a first verified phone registration."""
    if not isinstance(code, str) or not eligible(user) or not active(session, REFERRAL):
        return
    normalized = code.strip().upper()
    if not INVITE_PATTERN.fullmatch(normalized):
        return
    campaign = session.get(PromotionCampaignRecord, REFERRAL)
    if not campaign or aware(user.created_at) < aware(campaign.starts_at):
        return
    owner = session.scalar(
        select(InvitationCodeRecord).where(InvitationCodeRecord.code == normalized)
    )
    inviter = session.get(UserRecord, owner.user_id) if owner else None
    if (
        not eligible(inviter)
        or not inviter
        or inviter.id == user.id
        or inviter.phone_number == user.phone_number
        or aware(inviter.created_at) >= aware(user.created_at)
        or session.get(InvitationRecord, user.id) is not None
        or session.scalar(
            select(InvitationRecord.invitee_user_id).where(
                InvitationRecord.phone_key == phone_key(user)
            )
        )
    ):
        return
    try:
        with session.begin_nested():
            session.add(
                InvitationRecord(
                    invitee_user_id=user.id,
                    inviter_user_id=inviter.id,
                    phone_key=phone_key(user),
                    code=normalized,
                )
            )
            session.flush()
            session.add(
                AuditEventRecord(
                    workspace_id=personal_workspace_id(user.id),
                    actor_user_id=user.id,
                    action="growth.invitation_bound",
                    resource_type="invitation",
                    resource_id=user.id,
                    details={"inviter_user_id": inviter.id},
                )
            )
    except IntegrityError:
        # Attribution is optional; a competing valid registration must never
        # turn a successfully verified signup into a login failure.
        return


def referral_terms(session: Session, buyer_user_id: str, workspace_id: str) -> dict:
    if workspace_id != personal_workspace_id(buyer_user_id) or not active(
        session, REFERRAL
    ):
        return {}
    relationship = session.get(InvitationRecord, buyer_user_id)
    if not relationship or relationship.inviter_user_id == buyer_user_id:
        return {}
    return {
        "referral_reward": {
            "inviter_user_id": relationship.inviter_user_id,
            "reward_bps": REWARD_BPS,
            "validity_days": BONUS_VALIDITY_DAYS,
        }
    }


def _entry(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    kind: str,
    delta: int,
    title: str,
) -> None:
    session.add(
        CreditLedgerEntryRecord(
            workspace_id=account.workspace_id,
            entry_type=kind,
            delta_balance_credits=delta,
            delta_reserved_credits=0,
            balance_after_credits=account.balance_credits,
            reserved_after_credits=account.reserved_credits,
            description=title,
        )
    )


def _adjustment(session: Session, user_id: str) -> GrowthDebtRecord:
    row = session.get(
        GrowthDebtRecord, user_id, with_for_update=True, populate_existing=True
    )
    if row is None:
        row = GrowthDebtRecord(user_id=user_id, pending_credits=0)
        session.add(row)
        session.flush()
    return row


def _bonus(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    user_id: str,
    source: str,
    amount: int,
    days: int,
    title: str,
    kind: str,
) -> tuple[CreditGrantRecord, int]:
    lot = grant(
        session,
        account,
        source,
        amount,
        aware(utc_now()) + timedelta(days=days),
        title,
        kind=kind,
    )
    adjustment = session.get(
        GrowthDebtRecord, user_id, with_for_update=True, populate_existing=True
    )
    offset = min(amount, adjustment.pending_credits) if adjustment else 0
    if offset and adjustment is not None:
        adjustment.pending_credits -= offset
        account.balance_credits -= offset
        account.version += 1
        lot.remaining -= offset
        _entry(
            session, account, "reward_adjustment", -offset, "赠送积分抵扣退款奖励调整"
        )
    return lot, offset


def award_paid_grant(session: Session, source: CreditGrantRecord) -> None:
    if source.kind not in {
        "purchase",
        "membership",
    } or not source.source_key.startswith("pay_"):
        return
    purchase_id = source.source_key.split(":", 1)[0]
    order = session.get(CreditPurchaseRecord, purchase_id, populate_existing=True)
    terms = (order.product_snapshot or {}).get("referral_reward") if order else None
    if (
        not order
        or not terms
        or order.status not in {"paid", "partially_refunded"}
        or source.workspace_id != order.workspace_id
        or order.workspace_id != personal_workspace_id(order.created_by_user_id)
    ):
        return
    relationship = session.get(InvitationRecord, order.created_by_user_id)
    inviter = terms.get("inviter_user_id")
    if (
        not relationship
        or relationship.inviter_user_id != inviter
        or inviter == order.created_by_user_id
    ):
        return
    amount = source.amount * int(terms["reward_bps"]) // 10000
    if amount <= 0 or session.get(ReferralRewardRecord, source.id) is not None:
        return
    account = refresh(session, personal_workspace_id(inviter))
    reward = ReferralRewardRecord(
        source_grant_id=source.id,
        purchase_id=order.id,
        inviter_user_id=inviter,
        invitee_user_id=order.created_by_user_id,
        amount=amount,
        revoked=0,
        consumed=0,
        debt_created=0,
        source_revoked=0,
    )
    session.add(reward)
    session.flush()
    lot, offset = _bonus(
        session,
        account,
        inviter,
        "referral:" + source.id,
        amount,
        int(terms["validity_days"]),
        "邀请奖励 · " + order.package_name,
        "referral_bonus",
    )
    reward.bonus_grant_id = lot.id
    reward.consumed = offset
    session.add(
        AuditEventRecord(
            workspace_id=account.workspace_id,
            actor_user_id=inviter,
            action="growth.referral_reward_granted",
            resource_type="referral_reward",
            resource_id=source.id,
            details={"purchase_id": order.id, "credits": amount},
        )
    )
    session.flush()


def _reconcile(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    reward: ReferralRewardRecord,
) -> None:
    lot = (
        session.get(CreditGrantRecord, reward.bonus_grant_id)
        if reward.bonus_grant_id
        else None
    )
    if not lot or lot.workspace_id != account.workspace_id:
        raise ApiError(409, "REFERRAL_REWARD_MISMATCH", "邀请奖励记录需要核对。")
    entitlement = reward.amount - reward.revoked
    permitted_unheld = max(0, entitlement - reward.consumed - lot.held)
    revoke = max(0, lot.remaining - lot.held - permitted_unheld)
    if revoke:
        lot.remaining -= revoke
        account.balance_credits -= revoke
        account.version += 1
        _entry(
            session,
            account,
            "referral_reversal",
            -revoke,
            "邀请充值退款 · 奖励积分调整",
        )
    # Only benefits already consumed can create an adjustment. Unused expired
    # credits must never become a debt, and active customer tasks keep running.
    extra = max(0, reward.consumed - entitlement) - reward.debt_created
    if extra > 0:
        adjustment = _adjustment(session, reward.inviter_user_id)
        adjustment.pending_credits += extra
        reward.debt_created += extra


def record_bonus_consumption(
    session: Session,
    account: WorkspaceCreditAccountRecord,
    lot: CreditGrantRecord,
    consumed: int,
) -> None:
    if lot.kind != "referral_bonus":
        return
    reward = session.scalar(
        select(ReferralRewardRecord)
        .where(ReferralRewardRecord.bonus_grant_id == lot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reward:
        reward.consumed += consumed
        _reconcile(session, account, reward)


def reverse_refund_rewards(
    session: Session, order: CreditPurchaseRecord, refund: CreditRefundRecord
) -> None:
    if not (order.product_snapshot or {}).get("referral_reward"):
        return
    rows = list(
        session.scalars(
            select(ReferralRewardRecord).where(
                ReferralRewardRecord.purchase_id == order.id
            )
        )
    )
    if not rows:
        return
    accounts = {
        user_id: refresh(session, personal_workspace_id(user_id))
        for user_id in sorted({row.inviter_user_id for row in rows})
    }
    source_units = {
        item["id"]: int(item["amount"])
        for item in refund.entitlement_snapshot.get("lots", [])
    }
    full = order.refunded_amount_fen == order.amount_fen
    for original in rows:
        reward = session.get(
            ReferralRewardRecord,
            original.source_grant_id,
            with_for_update=True,
            populate_existing=True,
        )
        assert reward is not None
        if session.get(ReferralReversalRecord, (refund.id, reward.source_grant_id)):
            continue
        source = session.get(CreditGrantRecord, reward.source_grant_id)
        assert source is not None
        units = (
            source.amount - reward.source_revoked
            if full
            else min(
                source_units.get(source.id, 0), source.amount - reward.source_revoked
            )
        )
        if not units:
            continue
        session.add(
            ReferralReversalRecord(
                refund_id=refund.id, source_grant_id=source.id, source_credits=units
            )
        )
        reward.source_revoked += units
        terms = (order.product_snapshot or {})["referral_reward"]
        remaining_reward = (
            (source.amount - reward.source_revoked) * int(terms["reward_bps"]) // 10000
        )
        reward.revoked = reward.amount - remaining_reward
        _reconcile(session, accounts[reward.inviter_user_id], reward)
    session.flush()


def checkin(
    session: Session, user: UserRecord
) -> tuple[DailyCheckinRecord | None, bool]:
    if not eligible(user) or not active(session, CHECKIN):
        raise ApiError(403, "CHECKIN_UNAVAILABLE", "当前账号暂不能领取签到积分。")
    account = refresh(session, personal_workspace_id(user.id))
    today = aware(utc_now()).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    existing = session.scalar(
        select(DailyCheckinRecord).where(
            DailyCheckinRecord.phone_key == phone_key(user),
            DailyCheckinRecord.day == today,
        )
    )
    if existing:
        return (existing if existing.user_id == user.id else None), False
    lot, offset = _bonus(
        session,
        account,
        user.id,
        f"checkin:{user.id}:{today}",
        DAILY_CREDITS,
        BONUS_VALIDITY_DAYS,
        "每日签到 +10 积分",
        "daily_checkin",
    )
    receipt = DailyCheckinRecord(
        user_id=user.id,
        day=today,
        phone_key=phone_key(user),
        grant_id=lot.id,
        net_credits=DAILY_CREDITS - offset,
    )
    session.add(receipt)
    session.add(
        AuditEventRecord(
            workspace_id=account.workspace_id,
            actor_user_id=user.id,
            action="growth.daily_checkin",
            resource_type="daily_checkin",
            resource_id=today,
            details={"credits": DAILY_CREDITS, "net_credits": receipt.net_credits},
        )
    )
    session.flush()
    return receipt, True


def _code(session: Session, user_id: str) -> InvitationCodeRecord:
    current = session.get(InvitationCodeRecord, user_id)
    if current:
        return current
    for _ in range(8):
        try:
            with session.begin_nested():
                current = InvitationCodeRecord(
                    user_id=user_id, code="HY" + secrets.token_hex(5).upper()
                )
                session.add(current)
                session.flush()
            return current
        except IntegrityError:
            existing = session.get(InvitationCodeRecord, user_id)
            if existing:
                return existing
    raise ApiError(503, "INVITATION_CODE_BUSY", "邀请链接暂时未准备好，请重试。")


def state(session: Session, user: UserRecord, workspace_id: str) -> dict:
    allowed = eligible(user) and workspace_id == personal_workspace_id(user.id)
    daily = allowed and active(session, CHECKIN)
    referral = allowed and active(session, REFERRAL)
    now = aware(utc_now()).astimezone(ZoneInfo("Asia/Shanghai"))
    today = now.date().isoformat()
    if daily or referral:
        refresh(session, workspace_id)
    claimed = (
        bool(
            session.scalar(
                select(DailyCheckinRecord.user_id).where(
                    DailyCheckinRecord.phone_key == phone_key(user),
                    DailyCheckinRecord.day == today,
                )
            )
        )
        if daily
        else False
    )
    invite_code = _code(session, user.id).code if referral else None
    invited = (
        session.scalar(
            select(func.count())
            .select_from(InvitationRecord)
            .where(InvitationRecord.inviter_user_id == user.id)
        )
        if referral
        else 0
    )
    earned = (
        session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        ReferralRewardRecord.amount - ReferralRewardRecord.revoked
                    ),
                    0,
                )
            ).where(ReferralRewardRecord.inviter_user_id == user.id)
        )
        if referral
        else 0
    )
    adjustment = session.get(GrowthDebtRecord, user.id) if allowed else None
    events = []
    if daily:
        for row in session.scalars(
            select(DailyCheckinRecord)
            .where(DailyCheckinRecord.user_id == user.id)
            .order_by(DailyCheckinRecord.created_at.desc())
            .limit(5)
        ):
            events.append(
                {
                    "kind": "checkin",
                    "title": "每日签到",
                    "credits": row.net_credits,
                    "created_at": aware(row.created_at).isoformat(),
                    "adjusted": False,
                }
            )
    if referral:
        for reward in session.scalars(
            select(ReferralRewardRecord)
            .where(ReferralRewardRecord.inviter_user_id == user.id)
            .order_by(ReferralRewardRecord.created_at.desc())
            .limit(5)
        ):
            events.append(
                {
                    "kind": "referral",
                    "title": "邀请奖励",
                    "credits": reward.amount - reward.revoked,
                    "created_at": aware(reward.created_at).isoformat(),
                    "adjusted": reward.revoked > 0,
                }
            )
    events.sort(key=lambda e: str(e["created_at"]), reverse=True)
    return {
        "checkin_enabled": bool(daily),
        "referral_enabled": bool(referral),
        "checked_in": claimed,
        "daily_credits": DAILY_CREDITS,
        "reward_percent": REWARD_BPS // 100,
        "validity_days": BONUS_VALIDITY_DAYS,
        "day": today,
        "next_day_at": datetime.combine(
            now.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).isoformat(),
        "invite_url": "https://huipin.haiyushuke.com/?invite=" + invite_code
        if invite_code
        else None,
        "invited_count": invited or 0,
        "earned_credits": earned or 0,
        "pending_adjustment_credits": adjustment.pending_credits if adjustment else 0,
        "recent": events[:5],
    }
