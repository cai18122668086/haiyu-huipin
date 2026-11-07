"""Private invitation reporting and reminders after a delivered free trial."""

from contextlib import suppress
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import growth_rewards as rewards
from .credit_memberships import aware
from .credit_products import PRODUCTS, STARTER_PACK_ID
from .errors import ApiError
from .models import (
    CreditPurchaseRecord,
    InvitationRecord,
    JobAssetRecord,
    JobRecord,
    NewcomerOfferRecord,
    ReferralRewardRecord,
    ReplicaDocumentRecord,
    ReplicaWelcomeGrantRecord,
    UserRecord,
    utc_now,
)
from .onboarding import personal_workspace_id
from .replica_welcome import NEWCOMER_PACK, newcomer_eligible

REMINDER = "newcomer-reminder-v1"
VIEWING_SECONDS = 30
ACTION_SECONDS = 5
WINDOW_SECONDS = 24 * 60 * 60
PAID = ["paid", "partially_refunded", "refunded"]


def personal_user(
    session: Session, user_id: str, workspace_id: str, *, lock: bool = False
) -> UserRecord:
    user = session.get(
        UserRecord, user_id, with_for_update=lock, populate_existing=lock
    )
    if (
        not rewards.eligible(user)
        or not user
        or workspace_id != personal_workspace_id(user_id)
    ):
        raise ApiError(
            403, "PERSONAL_REWARDS_REQUIRED", "请使用已验证手机号的个人账户。"
        )
    return user


def invitation_center(
    session: Session, user: UserRecord, offset: int, limit: int, status: str
) -> dict:
    if not rewards.active(session, rewards.REFERRAL):
        raise ApiError(403, "INVITATIONS_UNAVAILABLE", "邀请活动暂未开放。")
    overview = rewards.state(session, user, personal_workspace_id(user.id))
    purchases = (
        select(
            CreditPurchaseRecord.created_by_user_id.label("buyer"),
            func.max(CreditPurchaseRecord.paid_at).label("paid_at"),
            func.sum(
                case(
                    (
                        CreditPurchaseRecord.status.in_(["paid", "partially_refunded"]),
                        1,
                    ),
                    else_=0,
                )
            ).label("active_count"),
            func.count().label("paid_count"),
        )
        .join(
            InvitationRecord,
            InvitationRecord.invitee_user_id == CreditPurchaseRecord.created_by_user_id,
        )
        .where(
            InvitationRecord.inviter_user_id == user.id,
            CreditPurchaseRecord.status.in_(PAID),
        )
        .group_by(CreditPurchaseRecord.created_by_user_id)
        .subquery()
    )
    totals = (
        select(
            ReferralRewardRecord.invitee_user_id.label("invitee"),
            func.sum(ReferralRewardRecord.amount - ReferralRewardRecord.revoked).label(
                "credits"
            ),
        )
        .where(ReferralRewardRecord.inviter_user_id == user.id)
        .group_by(ReferralRewardRecord.invitee_user_id)
        .subquery()
    )
    paid_status = case(
        (purchases.c.active_count > 0, "paid"),
        (purchases.c.paid_count > 0, "refunded"),
        else_="unpaid",
    )
    statement = (
        select(
            UserRecord,
            InvitationRecord,
            paid_status.label("payment_status"),
            purchases.c.paid_at,
            func.coalesce(totals.c.credits, 0),
        )
        .join(InvitationRecord, InvitationRecord.invitee_user_id == UserRecord.id)
        .outerjoin(purchases, purchases.c.buyer == UserRecord.id)
        .outerjoin(totals, totals.c.invitee == UserRecord.id)
        .where(InvitationRecord.inviter_user_id == user.id)
    )
    counts: dict[str, int] = dict(
        session.execute(
            select(paid_status, func.count())
            .select_from(InvitationRecord)
            .outerjoin(purchases, purchases.c.buyer == InvitationRecord.invitee_user_id)
            .where(InvitationRecord.inviter_user_id == user.id)
            .group_by(paid_status)
        ).all()
    )
    if status != "all":
        statement = statement.where(paid_status == status)
    records = []
    for friend, _invitation, payment_status, paid_at, credits in session.execute(
        statement.order_by(
            InvitationRecord.created_at.desc(), InvitationRecord.invitee_user_id.desc()
        )
        .offset(offset)
        .limit(limit)
    ):
        assert isinstance(friend, UserRecord)
        assert paid_at is None or isinstance(paid_at, datetime)
        phone = (friend.phone_number or "").removeprefix("+86")
        records.append(
            {
                "id": sha256(friend.id.encode()).hexdigest()[:16],
                "friend": phone[:3] + "****" + phone[-4:]
                if len(phone) == 11
                else "受邀好友",
                "registered_at": aware(friend.created_at).isoformat(),
                "payment_status": payment_status,
                "last_paid_at": aware(paid_at).isoformat() if paid_at else None,
                "earned_credits": credits,
            }
        )
    url = overview["invite_url"]
    return {
        "invite_code": url.split("=")[-1] if url else "",
        "invite_url": "https://huipin.haiyushuke.com/auth/register?invite="
        + url.split("=")[-1]
        if url
        else "",
        "invited_count": overview["invited_count"],
        "paid_count": counts.get("paid", 0),
        "unpaid_count": counts.get("unpaid", 0),
        "refunded_count": counts.get("refunded", 0),
        "earned_credits": overview["earned_credits"],
        "reward_percent": overview["reward_percent"],
        "validity_days": overview["validity_days"],
        "total": sum(counts.values()) if status == "all" else counts.get(status, 0),
        "offset": offset,
        "limit": limit,
        "items": records,
    }


def offer_state(session: Session, user: UserRecord, workspace_id: str) -> dict:
    product = PRODUCTS[NEWCOMER_PACK]
    regular = PRODUCTS[STARTER_PACK_ID]
    now = aware(utc_now())
    enabled = rewards.active(session, REMINDER) and newcomer_eligible(
        session, user.id, workspace_id
    )
    row = session.get(NewcomerOfferRecord, user.id) if enabled else None
    anchor = (
        aware(row.first_viewed_at) + timedelta(seconds=VIEWING_SECONDS) if row else None
    )
    ready = anchor
    if row and ready:
        document = session.get(ReplicaDocumentRecord, row.session_id)
        action_at = document.payload.get("newcomer_action_at") if document else None
        if isinstance(action_at, str):
            with suppress(ValueError):
                ready = min(
                    ready,
                    aware(datetime.fromisoformat(action_at))
                    + timedelta(seconds=ACTION_SECONDS),
                )
    window = (
        max(0, int((now - anchor).total_seconds()) // WINDOW_SECONDS) if anchor else 0
    )
    # Accelerating a first reminder must not restart the daily offer's deadline.
    end = anchor + timedelta(seconds=(window + 1) * WINDOW_SECONDS) if anchor else None
    shown_today = bool(
        row
        and row.last_shown_window >= 0
        and aware(row.updated_at).astimezone(ZoneInfo("Asia/Shanghai")).date()
        == now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    )
    return {
        "eligible": bool(enabled),
        "trial_viewed": row is not None,
        "reminded_today": shown_today,
        "should_show": bool(
            row
            and ready
            and now >= ready
            and row.last_shown_window < window
            and not shown_today
        ),
        "server_now": now.isoformat(),
        "ready_at": ready.isoformat() if ready else None,
        "window_ends_at": end.isoformat() if end else None,
        "amount_fen": product.amount_fen,
        "regular_amount_fen": regular.amount_fen,
        "savings_fen": regular.amount_fen - product.amount_fen,
        "credits": product.credits,
        "fast_images": product.credits // 8,
        "validity_days": product.validity_days,
    }


def mark_trial_viewed(
    session: Session,
    user: UserRecord,
    workspace_id: str,
    session_id: str | None,
    *,
    job_id: str | None = None,
    event: str = "viewed",
) -> dict:
    if not rewards.active(session, REMINDER) or not newcomer_eligible(
        session, user.id, workspace_id
    ):
        return offer_state(session, user, workspace_id)
    grant = session.get(ReplicaWelcomeGrantRecord, user.id)
    session_id = session_id or (grant.session_id if grant else None)
    document = session.get(ReplicaDocumentRecord, session_id) if session_id else None
    if (
        not grant
        or grant.session_id != session_id
        or grant.workspace_id != workspace_id
        or not document
        or document.created_by != user.id
        or document.workspace_id != workspace_id
        or not document.payload.get("use_welcome_trial")
        or document.payload.get("submission_complete") is False
    ):
        raise ApiError(409, "TRIAL_NOT_DELIVERED", "免费套图完成后，再继续体验。")
    ids = document.payload.get("job_ids", [])
    if not isinstance(ids, list) or not 0 < len(ids) <= 100:
        raise ApiError(409, "TRIAL_NOT_DELIVERED", "免费套图还在生成中。")
    if job_id and job_id not in ids:
        raise ApiError(409, "TRIAL_NOT_DELIVERED", "请查看本人的免费套图结果。")
    jobs = list(
        session.scalars(
            select(JobRecord).where(JobRecord.id.in_([job_id] if job_id else ids))
        )
    )
    delivered = False
    for job in jobs:
        if (
            job.workspace_id != workspace_id
            or job.status not in ("completed", "partial")
            or job.configuration.get("replica_session_id") != session_id
        ):
            continue
        if session.scalar(
            select(JobAssetRecord.id)
            .where(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.workspace_id == workspace_id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.is_deliverable.is_(True),
            )
            .limit(1)
        ):
            delivered = True
            break
    if not delivered:
        raise ApiError(409, "TRIAL_NOT_DELIVERED", "免费套图还在生成中。")
    existing = session.get(NewcomerOfferRecord, user.id)
    if not existing:
        session.add(
            NewcomerOfferRecord(
                user_id=user.id,
                session_id=session_id,
                first_viewed_at=utc_now(),
                last_shown_window=-1,
            )
        )
    if event in ("downloaded", "continue") and not document.payload.get(
        "newcomer_action_at"
    ):
        # Server-owned metadata on the verified trial avoids changing the schema.
        document.payload = {
            **document.payload,
            "newcomer_action_at": aware(utc_now()).isoformat(),
        }
    session.flush()
    return offer_state(session, user, workspace_id)


def claim_impression(
    session: Session, user: UserRecord, workspace_id: str, *, manual: bool = False
) -> dict:
    result = offer_state(session, user, workspace_id)
    if manual:
        result["should_show"] = result["eligible"] and result["trial_viewed"]
    if result["should_show"]:
        row = session.get(NewcomerOfferRecord, user.id)
        assert row is not None
        row.last_shown_window = max(
            0,
            int(
                (aware(utc_now()) - aware(row.first_viewed_at)).total_seconds()
                - VIEWING_SECONDS
            )
            // WINDOW_SECONDS,
        )
        row.updated_at = utc_now()
        session.flush()
    return result
