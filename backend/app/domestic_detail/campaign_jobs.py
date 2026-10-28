"""Source-bound optional campaign-review operations using public job infrastructure."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..aurora_detail_runtime import detail_workspace
from ..config import Settings
from ..domain import JobType
from ..models import JobAttemptRecord, JobItemRecord, JobPlanRecord, JobRecord, utc_now
from ..provider_quota import reserve_provider_job_quota
from ..services import enforce_active_job_limit, new_id
from ..services import reserve_image_credits as reserve_credits
from ..storage import ImageStorage
from .delivery import _json, _reject, _verified_context
from .generation import runtime_source_hash
from .planner_dispatch import validate_dispatch_result
from .plans import canonical_hash
from .review import ReviewStore, approved_review_authority
from .review_packet import validate_final_review

CAMPAIGN_JOB_VERSION = "domestic-campaign-review-v1"
CAMPAIGN_PROFILE = "aurora_detail_campaign_audit"
CAMPAIGN_RECOVERY_VERSION = "campaign-saved-result-v1"


async def source_scope(
    db: Session,
    source: JobRecord,
    storage: ImageStorage,
    fingerprint: str,
) -> tuple[JobPlanRecord, dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    if source.type != JobType.DETAIL_PACK.value or source.status not in {
        "awaiting_final_review",
        "completed",
    }:
        _reject(
            "The source detail campaign is not ready for optional review",
            "NATIVE_DETAIL_CAMPAIGN_SOURCE_NOT_READY",
        )
    plan = db.scalar(
        select(JobPlanRecord)
        .where(
            JobPlanRecord.job_id == source.id,
            JobPlanRecord.workspace_id == source.workspace_id,
        )
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if plan is None:
        _reject("The source campaign has no approved plan")
    context, state, root = await _verified_context(db, source, plan, storage)
    packet = await validate_final_review(
        db,
        source,
        plan,
        storage,
        context,
        state,
        root,
        fingerprint,
    )
    return plan, context, state, root, packet


def campaign_quote(
    source_id: str,
    fingerprint: str,
    context: Mapping[str, Any],
    settings: Settings,
    route: Mapping[str, Any],
) -> dict[str, Any]:
    value = {
        "version": CAMPAIGN_JOB_VERSION,
        "source_job_id": source_id,
        "final_review_sha256": fingerprint,
        "source_context_sha256": canonical_hash(context),
        "provider": route["provider"],
        "model": route["model"],
        "dispatch_route_sha256": route["sha256"],
        "reserve_micros": settings.tengli_planner_reserve_micros,
        "currency": "USD",
        "model_requests": 1,
        "image_requests": 0,
    }
    return {**value, "sha256": canonical_hash(value)}


def require_campaign_review_reconciled(db: Session, source: JobRecord) -> None:
    prior = db.scalars(
        select(JobRecord).where(
            JobRecord.workspace_id == source.workspace_id,
            JobRecord.type == JobType.DOMESTIC_DETAIL_REVIEW.value,
            JobRecord.configuration["source_job_id"].as_string() == source.id,
        )
    ).all()
    for review in prior:
        if review.status in {"queued", "processing", "cancelling"}:
            _reject(
                "An optional campaign review is already active: " + review.id,
                "NATIVE_DETAIL_CAMPAIGN_ALREADY_ACTIVE",
            )
        if review.configuration.get("version") == CAMPAIGN_RECOVERY_VERSION:
            # A local zero-model recovery has no uncertain supplier dispatch.
            # Its report is verified separately when resolving its parent.
            continue
        uncertain_attempt = db.scalar(
            select(JobAttemptRecord.id)
            .where(
                JobAttemptRecord.workspace_id == source.workspace_id,
                JobAttemptRecord.job_id == review.id,
                JobAttemptRecord.status.in_({"running", "outcome_unknown"}),
            )
            .limit(1)
        )
        uncertain_item = db.scalar(
            select(JobItemRecord.id)
            .where(
                JobItemRecord.workspace_id == source.workspace_id,
                JobItemRecord.job_id == review.id,
                JobItemRecord.error_code == "PROVIDER_OUTCOME_UNKNOWN",
            )
            .limit(1)
        )
        if uncertain_attempt is not None or uncertain_item is not None:
            _reject(
                "Reconcile the durable original review attempt "
                "before another paid call",
                "NATIVE_DETAIL_CAMPAIGN_OUTCOME_UNKNOWN",
            )
        if review.configuration.get("version") == CAMPAIGN_JOB_VERSION:
            from .campaign_durability import JOURNAL, sha

            root = detail_workspace(review.id)
            original_report_valid = False
            if (root / "campaign-report.json").exists():
                from .campaign_reports import load_campaign_report

                try:
                    load_campaign_report(review)
                    original_report_valid = True
                except (ValueError, OSError, KeyError):
                    pass
            if (root / JOURNAL).exists() and not original_report_valid:
                resolved = False
                from .campaign_reports import load_campaign_report

                for candidate in prior:
                    if (
                        candidate.configuration.get("original_review_job_id")
                        != review.id
                    ):
                        continue
                    if candidate.status not in {"completed", "failed"}:
                        continue
                    try:
                        report, _ = load_campaign_report(candidate)
                        resolved = report["report"]["original_review"][
                            "journal_file_sha256"
                        ] == sha((root / JOURNAL).read_bytes())
                    except (ValueError, OSError, KeyError):
                        continue
                    if resolved:
                        break
                if resolved:
                    continue
                if not resolved:
                    _reject(
                        "Recover the saved original result before another paid review",
                        "NATIVE_DETAIL_CAMPAIGN_SAVED_RESULT_AVAILABLE",
                    )
        directory = detail_workspace(review.id) / "reviews"
        for path in directory.glob("*.request.json"):
            try:
                binding = _json(path)["binding"]
                store = ReviewStore(directory, binding)
                if (
                    store.request_path != path
                    or binding.get("render_token") != review.id
                    or binding.get("native_request_sha256")
                    != review.configuration.get("intent_sha256")
                ):
                    raise ValueError("Original campaign request binding changed")
                result = store.completed()
                if result is None:
                    raise ValueError("Original campaign response is missing")
                validate_dispatch_result(result, binding["dispatch_route"])
                dispatch = result["provider_envelope"]["native_planner_dispatch"]
                if dispatch["outcome"] in {"outcome_unknown", "response_invalid"}:
                    raise ValueError("Original optional-review outcome is uncertain")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                _reject(
                    "Reconcile the original optional review before another paid call: "
                    + str(exc),
                    "NATIVE_DETAIL_CAMPAIGN_OUTCOME_UNKNOWN",
                )


async def create_campaign_review_job(
    db: Session,
    source: JobRecord,
    storage: ImageStorage,
    settings: Settings,
    *,
    actor_id: str,
    fingerprint: str,
    approval_text: str,
    authorized_reserve_micros: int,
    quote_sha256: str,
    dispatch_route: Mapping[str, Any],
) -> JobRecord:
    if not approval_text.strip():
        _reject("Explicit optional campaign-review authorization is required")
    reserve = settings.tengli_planner_reserve_micros
    if (
        type(authorized_reserve_micros) is not int
        or authorized_reserve_micros != reserve
    ):
        _reject(
            "The model review reserve changed; review the current quote",
            "NATIVE_DETAIL_CAMPAIGN_RESERVE_CHANGED",
        )
    plan, context, state, _, _ = await source_scope(db, source, storage, fingerprint)
    quote = campaign_quote(source.id, fingerprint, context, settings, dispatch_route)
    if quote["sha256"] != quote_sha256:
        _reject(
            "The source, quoted model route or reserve changed",
            "NATIVE_DETAIL_CAMPAIGN_QUOTE_CHANGED",
        )
    require_campaign_review_reconciled(db, source)
    enforce_active_job_limit(db, source.workspace_id)
    intent = {
        "version": CAMPAIGN_JOB_VERSION,
        "source_job_id": source.id,
        "source_plan_id": plan.id,
        "source_plan_revision": plan.revision,
        "source_context_sha256": canonical_hash(context),
        "source_native_truth_sha256": context["native_session_truth_sha256"],
        "approved_authority_sha256": canonical_hash(approved_review_authority(state)),
        "generation_binding_sha256": context["generation_binding_sha256"],
        "final_review_sha256": fingerprint,
        "approval_text": approval_text,
        "authorized_reserve_micros": reserve,
        "quote_sha256": quote_sha256,
        "dispatch_route_sha256": dispatch_route["sha256"],
        "planner_model": dispatch_route["model"],
        "authorized_model_requests": 1,
        "authorized_image_requests": 0,
        "source_native_version": state["version"],
        "runtime_source_sha256": runtime_source_hash(),
        "actor_id": actor_id,
    }
    configuration = {**intent, "intent_sha256": canonical_hash(intent)}
    job = JobRecord(
        id=new_id("job"),
        workspace_id=source.workspace_id,
        type=JobType.DOMESTIC_DETAIL_REVIEW.value,
        status="queued",
        stage="queued",
        display_name=("详情整页审核 · " + (source.display_name or source.id))[:160],
        configuration=configuration,
        configuration_schema=CAMPAIGN_JOB_VERSION,
        prompt_version=CAMPAIGN_JOB_VERSION,
        strategy_version=CAMPAIGN_JOB_VERSION,
        release_version=source.release_version,
        root_job_id=source.root_job_id or source.id,
        expected_item_count=1,
        estimated_cost_min_micros=0,
        estimated_cost_max_micros=reserve,
        estimated_credits=0,
        currency="USD",
        queued_at=utc_now(),
    )
    db.add(job)
    db.flush()
    db.add(
        JobItemRecord(
            id=new_id("item"),
            workspace_id=source.workspace_id,
            job_id=job.id,
            item_key="campaign_review",
            ordinal=0,
            status="pending",
            stage="queued",
            configuration={"intent_sha256": configuration["intent_sha256"]},
        )
    )
    if settings.provider_quota_enabled:
        reserve_provider_job_quota(
            db,
            job=job,
            actor_user_id=actor_id,
            estimated_cost_micros=reserve,
            provider_request_count=1,
            daily_limit_micros=round(settings.tengli_daily_budget_usd * 1_000_000),
            monthly_limit_micros=round(settings.tengli_monthly_budget_usd * 1_000_000),
            daily_request_limit=settings.tengli_daily_request_limit,
            planner_reserve_micros=reserve,
            image_budget_micros=0,
            group_count=1,
        )
    reserve_credits(db, source.workspace_id, job, 0)
    return job
