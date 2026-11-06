"""A verified signup receives one fast-replica task, never spendable credits."""

from datetime import timedelta
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .credit_memberships import aware
from .errors import ApiError
from .models import (
    AuditEventRecord,
    CreditPurchaseRecord,
    JobRecord,
    PromotionCampaignRecord,
    ReplicaDocumentRecord,
    ReplicaWelcomeGrantRecord,
    UserRecord,
    utc_now,
)
from .onboarding import personal_workspace_id

CAMPAIGN = "replica-welcome-v1"
NEWCOMER_PACK = "newcomer_160"
MAX_REFERENCES = 10
FAST_SKILLS = {
    "aurora-ecommerce-reference-remix-fast",
    "all-category-style-benchmark-still-fast",
    "aurora-remix-fast",
}


def newcomer_eligible(session: Session, user_id: str, workspace_id: str) -> bool:
    if not campaign_enabled(session) or workspace_id != personal_workspace_id(user_id):
        return False
    user = session.get(UserRecord, user_id)
    if not user or not ensure_grant(session, user):
        return False
    return (
        session.scalar(
            select(CreditPurchaseRecord.id)
            .where(
                CreditPurchaseRecord.created_by_user_id == user_id,
                CreditPurchaseRecord.status.in_(
                    ["paid", "partially_refunded", "refunded"]
                ),
            )
            .limit(1)
        )
        is None
    )


def campaign_enabled(session: Session) -> bool:
    campaign = session.get(PromotionCampaignRecord, CAMPAIGN)
    return bool(
        campaign and campaign.enabled and aware(campaign.starts_at) <= aware(utc_now())
    )


def ensure_grant(
    session: Session, user: UserRecord
) -> ReplicaWelcomeGrantRecord | None:
    existing = session.get(ReplicaWelcomeGrantRecord, user.id)
    if existing:
        return existing
    campaign = session.get(PromotionCampaignRecord, CAMPAIGN)
    if (
        not campaign
        or not campaign.enabled
        or not user.phone_number
        or user.status != "active"
        or aware(user.created_at) < aware(campaign.starts_at)
        or aware(campaign.starts_at) > aware(utc_now())
    ):
        return None
    # phone_number is persisted only by the verified identity adapter.
    phone_key = sha256(user.phone_number.encode()).hexdigest()
    if session.scalar(
        select(ReplicaWelcomeGrantRecord.user_id).where(
            ReplicaWelcomeGrantRecord.phone_key == phone_key
        )
    ):
        return None
    try:
        with session.begin_nested():
            grant = ReplicaWelcomeGrantRecord(
                user_id=user.id,
                phone_key=phone_key,
                workspace_id=personal_workspace_id(user.id),
                expires_at=utc_now() + timedelta(days=7),
            )
            session.add(grant)
            session.flush()
            session.add(
                AuditEventRecord(
                    workspace_id=grant.workspace_id,
                    actor_user_id=user.id,
                    action="promotion.replica_welcome_granted",
                    resource_type="replica_welcome_grant",
                    resource_id=user.id,
                )
            )
        return grant
    except IntegrityError:
        return session.get(ReplicaWelcomeGrantRecord, user.id)


def bind_session(session: Session, row: ReplicaDocumentRecord) -> None:
    grant = session.get(ReplicaWelcomeGrantRecord, row.created_by, with_for_update=True)
    if (
        not campaign_enabled(session)
        or not grant
        or grant.workspace_id != row.workspace_id
    ):
        raise ApiError(403, "WELCOME_TRIAL_UNAVAILABLE", "当前账号暂无免费复刻权益。")
    if grant.session_id and grant.session_id != row.id:
        raise ApiError(
            409,
            "WELCOME_TRIAL_ALREADY_BOUND",
            "免费体验已用于另一项复刻，请继续原任务。",
            {"session_id": grant.session_id},
        )
    values = row.payload
    if (
        values.get("replica_speed") != "fast"
        or len(values.get("references", [])) > MAX_REFERENCES
    ):
        raise ApiError(
            422, "WELCOME_TRIAL_SCOPE", "请选择快速复刻，或减少本次参考图片后再试。"
        )
    if aware(grant.expires_at) <= aware(utc_now()):
        raise ApiError(409, "WELCOME_TRIAL_EXPIRED", "本次免费复刻权益已到期。")
    grant.session_id = row.id


def free_pricing(session: Session, job: JobRecord, pricing: dict) -> bool:
    document_id = job.configuration.get("replica_session_id")
    if not document_id or job.type != "reference_remix":
        return False
    row = session.get(ReplicaDocumentRecord, document_id)
    if not row or not row.payload.get("use_welcome_trial"):
        return False
    grant = session.get(ReplicaWelcomeGrantRecord, row.created_by, with_for_update=True)
    settings = job.configuration.get("settings", {})
    if (
        not grant
        or grant.session_id != row.id
        or grant.workspace_id != job.workspace_id
        or row.workspace_id != job.workspace_id
        or row.payload.get("replica_speed") != "fast"
        or len(row.payload.get("references", [])) > MAX_REFERENCES
        or settings.get("reference_skill") not in FAST_SKILLS
    ):
        raise ApiError(
            409, "WELCOME_TRIAL_INVALID", "免费复刻任务关联无效，请从原任务继续。"
        )
    if grant.source_job_id is None:
        if aware(grant.expires_at) <= aware(utc_now()):
            raise ApiError(409, "WELCOME_TRIAL_EXPIRED", "本次免费复刻权益已到期。")
        grant.source_job_id = job.id
    elif job.id != grant.source_job_id:
        parent = (
            session.get(JobRecord, job.retry_of_job_id) if job.retry_of_job_id else None
        )
        if (
            job.root_job_id != grant.source_job_id
            or not parent
            or parent.workspace_id != job.workspace_id
            or parent.status not in {"partial", "failed", "cancelled"}
            or (
                parent.id != grant.source_job_id
                and parent.root_job_id != grant.source_job_id
            )
            or parent.configuration.get("replica_session_id") != row.id
        ):
            raise ApiError(
                409,
                "WELCOME_TRIAL_ALREADY_USED",
                "免费权益已使用，请从原任务恢复未完成图片。",
            )
        attempts = (
            session.scalar(
                select(func.count())
                .select_from(JobRecord)
                .where(JobRecord.root_job_id == grant.source_job_id)
            )
            or 0
        )
        if attempts > 3:
            raise ApiError(
                409,
                "WELCOME_TRIAL_RECOVERY_LIMIT",
                "这项体验任务需要人工协助，请联系客服继续处理。",
            )
    pricing.update(credits_per_image=0, welcome_trial=CAMPAIGN)
    job.estimated_credits = 0
    return True
