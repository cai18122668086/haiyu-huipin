"""Complete only the original preauthorized local delivery after usage settlement."""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..aurora_detail_runtime import detail_workspace
from ..domain import JobStatus
from ..models import AuditEventRecord, JobItemRecord, JobPlanRecord, JobRecord
from ..services import transition_job
from ..storage import ImageStorage
from .campaign_durability import json_once
from .delivery import (
    DELIVERY_VERSION,
    _finish,
    _reject,
    _verified_context,
    public_delivery_manifest,
)
from .delivery_authorization import (
    AUTO_MODE,
    job_receipt_authorization,
    native_authorization_text,
)
from .generation import PIPELINE_VERSION
from .plans import canonical_hash
from .review_packet import prepare_final_review


async def complete_authorized_delivery(
    session_factory: sessionmaker[Session], storage: ImageStorage | None, job_id: str
) -> JobStatus | None:
    authorization: dict[str, Any] | None = None
    try:
        with session_factory() as db:
            job = db.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            current = JobStatus(job.status)
            if (
                job.type != "detail_pack"
                or job.configuration.get("domestic_detail_pipeline") != PIPELINE_VERSION
                or current != JobStatus.AWAITING_FINAL_REVIEW
                or job.cancel_requested_at is not None
            ):
                return current
            plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job.id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if plan is None:
                return current
            receipt = plan.payload.get("aurora_v4_3", {}).get("approval_receipt") or {}
            if "automatic_delivery_authorization" not in receipt:
                return current
            authorization = job_receipt_authorization(db, job, plan)
            if authorization is None or storage is None:
                _reject(
                    "Automatic delivery requires its original authority "
                    "and durable storage"
                )
            items = db.scalars(
                select(JobItemRecord).where(JobItemRecord.job_id == job.id)
            ).all()
            if len(items) != job.expected_item_count or any(
                item.status not in {"succeeded", "reused"} for item in items
            ):
                _reject(
                    "Automatic delivery requires every original item "
                    "to be settled successfully"
                )
            context, _, root = await _verified_context(db, job, plan, storage)
            review = await prepare_final_review(db, job, plan, storage)
            if review["context_sha256"] != canonical_hash(context):
                _reject("Accepted source changed during automatic packet creation")
            binding = {
                "version": DELIVERY_VERSION,
                "job_id": job.id,
                "workspace_id": job.workspace_id,
                "actor_id": authorization["actor_user_id"],
                "plan_id": plan.id,
                "plan_revision": plan.revision,
                "approval_text": native_authorization_text(plan.payload),
                "final_review_sha256": review["sha256"],
                "delivery_mode": AUTO_MODE,
                "automatic_delivery_authorization_sha256": authorization["sha256"],
                **context,
            }
            completed = _finish(root, binding, allow_create=True)
            manifest = public_delivery_manifest(completed, context, review)
            manifest["automatic_delivery_authorization"] = authorization
            value = deepcopy(plan.payload)
            value["final_manifest"] = manifest
            value["aurora_v4_3"]["delivery"] = manifest
            plan.payload = value
            plan.payload_hash = canonical_hash(value)
            job.error_code = None
            job.error_message = None
            job.error_details = None
            transition_job(job, JobStatus.COMPLETED, stage="completed", progress=100)
            db.add(
                AuditEventRecord(
                    workspace_id=job.workspace_id,
                    action="domestic_detail.automatic_delivery_completed",
                    resource_type="job",
                    resource_id=job.id,
                    details={
                        "authorization_sha256": authorization["sha256"],
                        "final_review_sha256": review["sha256"],
                        "delivery_extra_model_requests": 0,
                        "delivery_extra_image_requests": 0,
                    },
                )
            )
            db.commit()
            return JobStatus.COMPLETED
    except Exception as exc:
        # No gateway, usage event, quota or image retry exists in this path.
        failure = {
            "job_id": job_id,
            "authorization_sha256": authorization.get("sha256")
            if authorization
            else None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "delivery_extra_model_requests": 0,
            "delivery_extra_image_requests": 0,
        }
        with suppress(OSError, ValueError):
            json_once(
                detail_workspace(job_id)
                / "automatic-delivery-errors"
                / (canonical_hash(failure) + ".json"),
                failure,
            )
        with session_factory() as db:
            job = db.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if (
                job.status == "awaiting_final_review"
                and job.cancel_requested_at is None
            ):
                job.stage = "automatic_delivery_reconciliation_required"
                job.error_code = (
                    "NATIVE_DETAIL_AUTOMATIC_DELIVERY_RECONCILIATION_REQUIRED"
                )
                job.error_message = (
                    "自动打包尚未完成原证据核验；成图和原费用保留，"
                    "可恢复已授权的原打包。"
                )
                job.error_details = {
                    "authorization_sha256": failure["authorization_sha256"],
                    "delivery_extra_model_requests": 0,
                    "delivery_extra_image_requests": 0,
                }
                db.add(
                    AuditEventRecord(
                        workspace_id=job.workspace_id,
                        action="domestic_detail.automatic_delivery_interrupted",
                        resource_type="job",
                        resource_id=job.id,
                        details=job.error_details,
                    )
                )
                db.commit()
            return JobStatus(job.status)
