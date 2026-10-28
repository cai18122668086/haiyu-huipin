"""Website submission authorizes cross-border planning and original-byte delivery."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import JobStatus, PlanValidationStatus
from .models import AuditEventRecord, JobAssetRecord, JobPlanRecord, JobRecord, utc_now
from .services import canonical_hash, transition_job

KEY = "automatic_crossborder_submission"


def eligible(job: JobRecord) -> bool:
    settings = job.configuration.get("settings") or {}
    submission = job.configuration.get(KEY) or {}
    return (
        job.type == "detail_pack"
        and settings.get("market_region") == "international"
        and settings.get("auto_start_after_plan") is True
        and submission.get("version") == "automatic-crossborder-submission-v1"
        and bool(submission.get("actor_user_id"))
        and job.retry_scope != "selected"
    )


def bind_submission(
    job: JobRecord,
    actor_id: str,
    *,
    requested: bool | None = None,
) -> None:
    if requested is not True and not (requested is None and KEY in job.configuration):
        return
    settings = job.configuration.get("settings") or {}
    if (
        job.type == "detail_pack"
        and settings.get("market_region") == "international"
        and settings.get("auto_start_after_plan") is True
    ):
        job.configuration = {
            **job.configuration,
            KEY: {
                "version": "automatic-crossborder-submission-v1",
                "actor_user_id": actor_id,
            },
        }


def confirm_validated_plan(
    session: Session, job: JobRecord, plan: JobPlanRecord
) -> None:
    from .xinghe_agent import synchronize_confirmed_plan

    if (
        not eligible(job)
        or job.status != JobStatus.AWAITING_CONFIRMATION.value
        or plan.validation_status != PlanValidationStatus.VALID.value
        or plan.confirmed_at is not None
        or job.cancel_requested_at is not None
    ):
        return
    synchronize_confirmed_plan(session, job, plan)
    receipt = {
        "confirmation_source": "task_submission_default",
        "actor_user_id": job.configuration[KEY]["actor_user_id"],
        "plan_revision": plan.revision,
        "confirmed_at": utc_now().isoformat(),
    }
    plan.payload = {**plan.payload, "automatic_plan_confirmation": receipt}
    plan.payload_hash = canonical_hash(plan.payload)
    plan.confirmed_at = utc_now()
    transition_job(job, JobStatus.PROCESSING, stage="generating", progress=20)
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            actor_user_id=receipt["actor_user_id"],
            action="crossborder_detail.plan_auto_confirmed",
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details=receipt,
        )
    )


def complete_delivery(session: Session, job: JobRecord, plan: JobPlanRecord) -> bool:
    if not eligible(job) or job.status != JobStatus.AWAITING_FINAL_REVIEW.value:
        return False
    links = session.scalars(
        select(JobAssetRecord)
        .where(
            JobAssetRecord.job_id == job.id,
            JobAssetRecord.direction == "output",
            JobAssetRecord.is_deliverable.is_(True),
        )
        .order_by(JobAssetRecord.ordinal)
    ).all()
    if (
        job.cancel_requested_at is not None
        or len(links) != job.expected_item_count
        or job.succeeded_item_count != job.expected_item_count
        or plan.confirmed_at is None
    ):
        return False
    manifest = {
        "skill_id": "xinghe-crossborder-detail-3",
        "page_asset_refs": [link.asset_id for link in links],
        "delivery": "SUBMISSION_AUTHORIZED_AUTOMATIC_DELIVERY",
        "manual_review_performed": False,
    }
    plan.payload = {**plan.payload, "crossborder_final_manifest": manifest}
    plan.payload_hash = canonical_hash(plan.payload)
    transition_job(job, JobStatus.COMPLETED, stage="completed", progress=100)
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            actor_user_id=job.configuration[KEY]["actor_user_id"],
            action="crossborder_detail.automatically_delivered",
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details=manifest,
        )
    )
    return True
