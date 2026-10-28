"""Submission-authorized Amazon planning, separate from historical approvals."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from .domain import JobStatus, PlanValidationStatus
from .models import AuditEventRecord, JobPlanRecord, JobRecord, utc_now
from .services import canonical_hash, transition_job

SUBMISSION_KEY = "automatic_amazon_submission"
SUBMISSION_VERSION = "automatic-amazon-submission-v1"


def uses_current_runtime(configuration: Mapping[str, Any]) -> bool:
    submission = configuration.get(SUBMISSION_KEY) or {}
    settings = configuration.get("settings") or {}
    return (
        isinstance(submission, Mapping)
        and isinstance(settings, Mapping)
        and submission.get("version") == SUBMISSION_VERSION
        and isinstance(submission.get("actor_user_id"), str)
        and bool(submission.get("actor_user_id"))
        and settings.get("auto_start_after_plan") is True
    )


def eligible(job: JobRecord) -> bool:
    return job.type == "amazon_visual_suite" and uses_current_runtime(job.configuration)


def bind_submission(job: JobRecord, actor_id: str) -> None:
    settings = job.configuration.get("settings") or {}
    if (
        job.type == "amazon_visual_suite"
        and settings.get("auto_start_after_plan") is True
    ):
        job.configuration = {
            **job.configuration,
            SUBMISSION_KEY: {"version": SUBMISSION_VERSION, "actor_user_id": actor_id},
        }


def confirm_validated_plan(
    session: Session, job: JobRecord, plan: JobPlanRecord
) -> None:
    from .amazon_visual_suite_provider import synchronize_confirmed_amazon_plan

    if (
        not eligible(job)
        or job.status != JobStatus.AWAITING_CONFIRMATION.value
        or plan.validation_status != PlanValidationStatus.VALID.value
        or plan.confirmed_at is not None
        or job.cancel_requested_at is not None
    ):
        return
    # This validates the complete Product Master, canonical 21-row inventory,
    # page dimensions, facts and prompts before applying them to durable items.
    synchronize_confirmed_amazon_plan(session, job, plan)
    value = deepcopy(plan.payload)
    receipt = {
        "confirmation_source": "task_submission_default",
        "actor_user_id": job.configuration[SUBMISSION_KEY]["actor_user_id"],
        "plan_id": plan.id,
        "plan_revision": plan.revision,
        "resume_signature": value["resume_signature"],
        "confirmed_at": utc_now().isoformat(),
    }
    value["automatic_plan_confirmation"] = receipt
    value["controls"] = {
        **value.get("controls", {}),
        "manual_confirmation_required": False,
    }
    plan.payload = value
    plan.payload_hash = canonical_hash(value)
    plan.confirmed_at = utc_now()
    transition_job(job, JobStatus.PROCESSING, stage="generating", progress=20)
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            actor_user_id=receipt["actor_user_id"],
            action="amazon_visual_suite.plan_auto_confirmed",
            resource_type="job",
            resource_id=job.id,
            request_id=plan.id,
            details=receipt,
        )
    )
