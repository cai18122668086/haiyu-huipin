"""Server confirmation of valid new website plans, preserving manual history."""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from .. import refined_detail
from ..aurora_detail_runtime import detail_workspace
from ..domain import JobStatus, PlanValidationStatus
from ..models import AuditEventRecord, JobPlanRecord, JobRecord, utc_now
from ..services import canonical_hash, transition_job
from .delivery_authorization import (
    SUBMISSION_SOURCE,
    SUBMISSION_TEXT,
    submission_authorization_quote,
)
from .generation import PIPELINE_VERSION
from .native_approval import verify_approval_sources
from .plans import validate_approval_binding


def eligible(job: JobRecord) -> bool:
    settings = job.configuration.get("settings") or {}
    submission = job.configuration.get("automatic_detail_submission") or {}
    return (
        job.type == "detail_pack"
        and settings.get("market_region") == "domestic"
        and settings.get("auto_start_after_plan") is True
        and submission.get("version") == "automatic-detail-submission-v1"
        and isinstance(submission.get("actor_user_id"), str)
        and (
            refined_detail.is_refined(job.configuration)
            or job.configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION
            and settings.get("website_contract_version")
            in {"WEBSITE_LEAN_V1", "WEBSITE_DIRECT_V2"}
        )
        # Selected-page revisions retain their own credit reservation and review.
        and job.retry_scope != "selected"
    )


def bind_submission(job: JobRecord, actor_id: str) -> None:
    settings = job.configuration.get("settings") or {}
    if (
        job.type == "detail_pack"
        and settings.get("market_region") == "domestic"
        and settings.get("auto_start_after_plan") is True
    ):
        job.configuration = {
            **job.configuration,
            "automatic_detail_submission": {
                "version": "automatic-detail-submission-v1",
                "actor_user_id": actor_id,
            },
        }


def confirm_validated_plan(
    session: Session, job: JobRecord, plan: JobPlanRecord
) -> None:
    from ..xinghe_agent import synchronize_confirmed_plan

    if (
        not eligible(job)
        or job.status != JobStatus.AWAITING_CONFIRMATION.value
        or job.stage == "intake_confirmation_required"
        or plan.validation_status != PlanValidationStatus.VALID.value
        or plan.confirmed_at is not None
        or job.cancel_requested_at is not None
    ):
        return
    authorization = None
    actor_id = job.configuration["automatic_detail_submission"]["actor_user_id"]
    if refined_detail.is_refined(job.configuration):
        refined_detail.validate_payload(plan.payload, job.expected_item_count)
        if not refined_detail.approval_matches(plan.payload):
            raise ValueError("The saved detail plan document changed")
        document_sha = plan.payload["refined_approval"]["document_sha256"]
    else:
        validate_approval_binding(plan.payload, job.configuration["settings"])
        verify_approval_sources(
            detail_workspace(job.id),
            plan.payload.get("native_approval"),
            plan.payload["source_asset_manifest"][0]["assets"],
        )
        document_sha = plan.payload["aurora_v4_3"]["approval_document_sha256"]
        authorization = submission_authorization_quote(
            job_id=job.id,
            workspace_id=job.workspace_id,
            actor_id=actor_id,
            plan_id=plan.id,
            revision=plan.revision,
            payload=plan.payload,
        )
    synchronize_confirmed_plan(session, job, plan)
    value = deepcopy(plan.payload)
    receipt = {
        "confirmation_source": SUBMISSION_SOURCE,
        "approval_text": SUBMISSION_TEXT,
        "approval_document_sha256": document_sha,
        "actor_user_id": actor_id,
        "plan_revision": plan.revision,
        "approved_at": utc_now().isoformat(),
    }
    value["automatic_plan_confirmation"] = receipt
    if authorization is not None:
        aurora = value["aurora_v4_3"]
        aurora["approval_receipt"] = {
            **receipt,
            "pipeline": PIPELINE_VERSION,
            "approval_protocol": value["native_approval"]["version"],
            "native_approval_document_sha256": (
                value["native_approval"]["document_sha256"]
            ),
            "contract_binding_sha256": aurora["contract_binding"]["sha256"],
            "automatic_delivery_authorization": authorization,
        }
    plan.payload = value
    plan.payload_hash = canonical_hash(value)
    plan.confirmed_at = utc_now()
    transition_job(job, JobStatus.PROCESSING, stage="generating", progress=20)
    session.add(AuditEventRecord(
        workspace_id=job.workspace_id,
        action="detail_plan.auto_confirmed",
        resource_type="job", resource_id=job.id, request_id=job.id,
        details={"plan_id": plan.id, **receipt},
    ))
