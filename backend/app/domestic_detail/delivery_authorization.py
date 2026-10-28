"""Plan-stage human authorization for native automatic delivery only."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .plans import DetailPlanError, canonical_hash

AUTO_MODE = "AUTO_FINALIZE_AUTHORIZED"
MANUAL_MODE = "USER_FINAL_REVIEW"
AUTH_VERSION = "domestic-detail-auto-delivery-authorization-v1"
AUTO_AUTHORIZATION_TEXT = (
    "我已审阅当前完整方案，明确授权按此方案生成后自动交付，"
    "接受保留的材料残差，并知悉生成后不再等待人工最终确认。"
)
AUTO_DISCLOSURE = (
    "自动打包不新增模型或图片调用；正常生成与一次自动修复仍按已确认方案计费。"
    "自动交付不表示画面无残差，也不表示已完成整页审核。"
)
SUBMISSION_SOURCE = "task_submission_default"
SUBMISSION_TEXT = (
    "按创建任务时的自动生成设置，后台确认校验通过的方案并生成整套图片，"
    "完成检查后自动交付。"
)


def authorization_quote(
    *,
    job_id: str,
    workspace_id: str,
    actor_id: str,
    plan_id: str,
    revision: int,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from .generation import runtime_source_hash

    native = payload["native_approval"]
    if native.get("source_job_id") != job_id:
        raise DetailPlanError(
            "Automatic delivery requires the exact original approval owner"
        )
    value = {
        "version": AUTH_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "actor_user_id": actor_id,
        "plan_id": plan_id,
        "plan_revision": revision,
        "native_approval_document_sha256": native["document_sha256"],
        "approval_document_sha256": payload["aurora_v4_3"]["approval_document_sha256"],
        "contract_binding_sha256": payload["aurora_v4_3"]["contract_binding"]["sha256"],
        "source_input_sha256": payload["source_input_sha256"],
        "runtime_source_sha256": runtime_source_hash(),
        "delivery_mode": AUTO_MODE,
        "authorization_text": AUTO_AUTHORIZATION_TEXT,
        "completion_disclosure": AUTO_DISCLOSURE,
        "delivery_extra_model_requests": 0,
        "delivery_extra_image_requests": 0,
    }
    return {**value, "sha256": canonical_hash(value)}


def confirm_authorization(
    *,
    mode: str | None,
    text: str | None,
    fingerprint: str | None,
    job_id: str,
    workspace_id: str,
    actor_id: str,
    plan_id: str,
    revision: int,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    if mode in {None, MANUAL_MODE}:
        if text is not None or fingerprint is not None:
            raise DetailPlanError(
                "Automatic consent cannot be attached to ordinary manual delivery"
            )
        return None
    if mode != AUTO_MODE or text != AUTO_AUTHORIZATION_TEXT:
        raise DetailPlanError(
            "Explicit current automatic-delivery authorization is required"
        )
    quote = authorization_quote(
        job_id=job_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        plan_id=plan_id,
        revision=revision,
        payload=payload,
    )
    if fingerprint != quote["sha256"]:
        raise DetailPlanError(
            "The automatic-delivery source, user or approval fingerprint changed"
        )
    return quote


def submission_authorization_quote(**kwargs: Any) -> dict[str, Any]:
    """Bind submission-time automatic generation without claiming human review."""
    quote = authorization_quote(**kwargs)
    quote.pop("sha256")
    quote["authorization_source"] = SUBMISSION_SOURCE
    quote["authorization_text"] = SUBMISSION_TEXT
    return {**quote, "sha256": canonical_hash(quote)}


def receipt_authorization(
    payload: Mapping[str, Any],
    *,
    job_id: str | None = None,
    workspace_id: str | None = None,
    plan_id: str | None = None,
    revision: int | None = None,
) -> dict[str, Any] | None:
    receipt = payload.get("aurora_v4_3", {}).get("approval_receipt") or {}
    authorization = receipt.get("automatic_delivery_authorization")
    if authorization is None:
        return None
    if not isinstance(authorization, dict):
        raise DetailPlanError("Original automatic-delivery authorization is invalid")
    try:
        quote_factory = (
            submission_authorization_quote
            if receipt.get("confirmation_source") == SUBMISSION_SOURCE
            else authorization_quote
        )
        expected = quote_factory(
            job_id=payload["native_approval"]["source_job_id"],
            workspace_id=authorization["workspace_id"],
            actor_id=receipt["actor_user_id"],
            plan_id=authorization["plan_id"],
            revision=receipt["plan_revision"],
            payload=payload,
        )
        if receipt.get("confirmation_source") == SUBMISSION_SOURCE:
            # Submission consent survives a runtime repair; the exact original
            # approval, actor, inputs and compiler contract remain hash-bound.
            original_runtime = authorization.get("runtime_source_sha256")
            if not isinstance(original_runtime, str) or not re.fullmatch(
                r"[0-9a-f]{64}", original_runtime
            ):
                raise DetailPlanError("Original submission runtime is missing")
            expected.pop("sha256")
            expected["runtime_source_sha256"] = original_runtime
            expected["sha256"] = canonical_hash(expected)
        if (
            canonical_hash(authorization) != canonical_hash(expected)
            or job_id is not None
            and authorization["job_id"] != job_id
            or workspace_id is not None
            and authorization["workspace_id"] != workspace_id
            or plan_id is not None
            and authorization["plan_id"] != plan_id
            or revision is not None
            and authorization["plan_revision"] != revision
        ):
            raise DetailPlanError(
                "The original automatic-delivery authority changed; "
                "a copied child needs new explicit approval"
            )
    except (KeyError, TypeError) as exc:
        raise DetailPlanError(
            "The complete original automatic-delivery authority is missing"
        ) from exc
    return authorization


def job_receipt_authorization(db: Any, job: Any, plan: Any) -> dict[str, Any] | None:
    """A genuine failed-only retry can reuse its unchanged submission authority.

    Copied plans, full clones, manual consent, edits and cross-workspace ancestry
    remain subject to the original strict owner check.
    """
    from sqlalchemy import select

    from ..models import IdempotencyRequestRecord, JobPlanRecord, JobRecord

    payload = plan.payload
    owner = (payload.get("native_approval") or {}).get("source_job_id")
    if owner == job.id or receipt_authorization(payload) is None:
        return receipt_authorization(
            payload,
            job_id=job.id,
            workspace_id=job.workspace_id,
            plan_id=plan.id,
            revision=plan.revision,
        )
    receipt = payload.get("aurora_v4_3", {}).get("approval_receipt") or {}
    if receipt.get("confirmation_source") != SUBMISSION_SOURCE:
        raise DetailPlanError("Manual automatic-delivery consent cannot be copied")
    authority = receipt_authorization(payload)
    assert authority is not None
    origin = db.get(JobPlanRecord, authority["plan_id"])
    if (
        origin is None
        or origin.job_id != owner
        or origin.workspace_id != job.workspace_id
        or origin.revision != authority["plan_revision"]
        or origin.confirmed_at is None
        or plan.confirmed_at is None
        or origin.validation_status != "valid"
        or plan.validation_status != "valid"
    ):
        raise DetailPlanError("Original confirmed retry authority is missing")
    # Usage and diagnostics accumulate independently; every authored/approved
    # field must still equal the original plan, including cost and asset views.
    mutable = {"usage", "planner_diagnostics", "metrics", "state"}

    def scope(p: Mapping[str, Any]) -> str:
        view = {k: v for k, v in p.items() if k not in mutable}
        if isinstance(view.get("cost"), Mapping):
            view["cost"] = {
                k: v
                for k, v in view["cost"].items()
                if k
                not in {
                    "pending_medium_count",
                    "pending_high_count",
                    "estimated_pending_micros",
                }
            }
        return canonical_hash(view)

    if scope(origin.payload) != scope(payload):
        raise DetailPlanError("A retry cannot change its original approved plan")
    current = job
    seen: set[str] = set()
    while current.id != owner:
        if (
            current.id in seen
            or len(seen) >= 32
            or current.workspace_id != job.workspace_id
            or current.retry_scope != "failed"
            or not current.retry_of_job_id
            or current.root_job_id != (job.root_job_id or owner)
        ):
            raise DetailPlanError("Failed retry ancestry is invalid")
        seen.add(current.id)
        request = db.scalar(
            select(IdempotencyRequestRecord)
            .where(
                IdempotencyRequestRecord.workspace_id == job.workspace_id,
                IdempotencyRequestRecord.method == "POST",
                IdempotencyRequestRecord.status == "completed",
                IdempotencyRequestRecord.route_template
                == "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/retry",
                IdempotencyRequestRecord.resource_id == current.id,
                IdempotencyRequestRecord.resource_type == "job",
                IdempotencyRequestRecord.response_status == 201,
            )
            .limit(1)
        )
        parent = db.get(JobRecord, current.retry_of_job_id)
        if (
            request is None
            or parent is None
            or parent.workspace_id != job.workspace_id
            or current.type != parent.type
            or current.type != "detail_pack"
            or current.configuration != parent.configuration
            or (getattr(parent, "root_job_id", None) or parent.id)
            != (job.root_job_id or owner)
        ):
            raise DetailPlanError("Retry requires its original workspace request")
        current = parent
    return receipt_authorization(
        payload,
        job_id=owner,
        workspace_id=job.workspace_id,
        plan_id=origin.id,
        revision=origin.revision,
    )


def native_authorization_text(payload: Mapping[str, Any]) -> str:
    text = str(payload["aurora_v4_3"]["approval_receipt"]["approval_text"])
    authorization = receipt_authorization(payload)
    return text + "\n" + authorization["authorization_text"] if authorization else text


def native_delivery_mode(payload: Mapping[str, Any]) -> str:
    return AUTO_MODE if receipt_authorization(payload) is not None else MANUAL_MODE
