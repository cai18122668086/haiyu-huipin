"""Submission-authorized six-image execution; manual historical jobs stay manual."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.orm import Session

from .domain import JobStatus, PlanValidationStatus
from .models import AuditEventRecord, JobPlanRecord, JobRecord, utc_now
from .services import canonical_hash, transition_job

SUBMISSION_KEY = "automatic_main_image_submission"
SUBMISSION_VERSION = "automatic-main-image-submission-v1"
APPROVAL_TEXT = "按任务提交授权，由后台校验六图方案并生成；不代表用户已人工审阅。"


def eligible(job: JobRecord) -> bool:
    settings = job.configuration.get("settings") or {}
    submission = job.configuration.get(SUBMISSION_KEY) or {}
    return (
        job.type == "domestic_main_image"
        and isinstance(settings, Mapping)
        and isinstance(submission, Mapping)
        and settings.get("auto_start_after_plan") is True
        and submission.get("version") == SUBMISSION_VERSION
        and bool(submission.get("actor_user_id"))
    )


def bind_submission(job: JobRecord, actor_id: str) -> None:
    settings = job.configuration.get("settings") or {}
    if (
        job.type == "domestic_main_image"
        and settings.get("auto_start_after_plan") is True
    ):
        job.configuration = {
            **job.configuration,
            SUBMISSION_KEY: {"version": SUBMISSION_VERSION, "actor_user_id": actor_id},
        }


def confirm_validated_plan(
    session: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    *,
    work_root: Path | None = None,
) -> None:
    from .aurora_main_image_planner import authorize_aurora_plan

    if (
        not eligible(job)
        or job.status != JobStatus.AWAITING_CONFIRMATION.value
        or plan.validation_status != PlanValidationStatus.VALID.value
        or plan.confirmed_at is not None
        or job.cancel_requested_at is not None
    ):
        return
    authorize_aurora_plan(plan, job, APPROVAL_TEXT, work_root=work_root)
    receipt = {
        "confirmation_source": "task_submission_default",
        "actor_user_id": job.configuration[SUBMISSION_KEY]["actor_user_id"],
        "plan_revision": plan.revision,
        "approval_text": APPROVAL_TEXT,
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
            action="domestic_main_image.plan_auto_confirmed",
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details=receipt,
        )
    )


def finalize_validated_set(
    session: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    *,
    work_root: Path,
) -> bool:
    """Use the original native finalizer only after a passing six-image audit."""
    from .aurora_execution_modes import is_parallel_aurora_mode
    from .aurora_main_image_runtime import AuroraV20HarnessRunner, write_contract

    if (
        not eligible(job)
        or job.status != JobStatus.AWAITING_FINAL_REVIEW.value
        or job.cancel_requested_at is not None
    ):
        return False
    if plan.payload.get("automatic_final_confirmation"):
        return True
    mode = (plan.payload.get("execution_policy") or {}).get("execution_mode")
    key = (
        "_parallel_v20_set_session"
        if is_parallel_aurora_mode(mode)
        else "_sequential_v20_set_session"
    )
    state = plan.payload.get(key)
    if not isinstance(state, Mapping) or state.get("set_audit_passed") is not True:
        return False
    relative = "parallel-v20" if is_parallel_aurora_mode(mode) else "sequential-v20"
    workspace = (work_root / job.id / relative / "set-audit").resolve()
    if not workspace.is_relative_to(
        work_root.resolve()
    ) or not workspace.is_relative_to((work_root / job.id).resolve()):
        raise ValueError("Finalization workspace escaped the job root")
    workspace.mkdir(parents=True, exist_ok=True)
    session_path = workspace / "session.json"
    output = workspace / "final-manifest.json"
    write_contract(session_path, dict(state))
    AuroraV20HarnessRunner().run(
        "finalize",
        workspace,
        {
            "session": session_path,
            "output": output,
            "approval-text": APPROVAL_TEXT,
        },
    )
    finalized = json.loads(session_path.read_text(encoding="utf-8"))
    receipt = {
        "confirmation_source": "task_submission_default",
        "actor_user_id": job.configuration[SUBMISSION_KEY]["actor_user_id"],
        "confirmed_at": utc_now().isoformat(),
    }
    plan.payload = {
        **plan.payload,
        "_harness_session": finalized,
        "final_manifest": str(output),
        "harness_state": finalized.get("state"),
        "automatic_final_confirmation": receipt,
    }
    plan.payload_hash = canonical_hash(plan.payload)
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            actor_user_id=receipt["actor_user_id"],
            action="domestic_main_image.set_auto_finalized",
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details=receipt,
        )
    )
    return True
