"""Create a separately owned local revision; callers own commit and outbox."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..aurora_detail_runtime import detail_workspace
from ..config import Settings
from ..domain import JobType
from ..models import (
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from ..provider_quota import reserve_provider_job_quota
from ..services import (
    enforce_active_job_limit,
    lock_workspace_limits,
    new_id,
)
from ..services import (
    reserve_image_credits as reserve_credits,
)
from ..storage import ImageStorage
from .delivery import _json, _reject
from .planner_dispatch import validate_dispatch_result
from .plans import canonical_hash
from .review import ReviewStore
from .revision_authorization import (
    authorize_revision_quote,
    build_revision_quote,
)

REVISION_VERSION = "domestic-detail-local-revision-v1"
REVISION_SCHEMA = "detail-local-revision-v1"


def _require_reconciled_files(root: Path) -> None:
    if root.is_symlink() or root.is_junction():
        raise ValueError("Revision evidence cannot traverse a link")
    for directory in ("planner-calls", "reviews"):
        for path in (root / directory).glob("*.request.json"):
            binding = _json(path)["binding"]
            store = ReviewStore(path.parent, binding)
            if store.request_path != path:
                raise ValueError("An original model request binding changed")
            result = store.completed()
            if result is None:
                raise ValueError("An original model response is still unknown")
            validate_dispatch_result(result, binding["dispatch_route"])
            if result["provider_envelope"]["native_planner_dispatch"]["outcome"] in {
                "outcome_unknown",
                "response_invalid",
            }:
                raise ValueError("Reconcile the original model response first")
    for path in (root / "generation-calls").glob("*.json"):
        if path.name.endswith(".completion.json"):
            continue
        request = _json(path)
        if request.get("request_sha256") != canonical_hash(
            {k: v for k, v in request.items() if k not in {"request_sha256", "outcome"}}
        ):
            raise ValueError("An original image invocation changed")
        completion = _json(path.with_suffix(".completion.json"))
        if completion.get("request_sha256") != request[
            "request_sha256"
        ] or completion.get("outcome") not in {"succeeded", "failed"}:
            raise ValueError("Reconcile the original image result first")
        if completion["outcome"] == "succeeded":
            import hashlib

            artifact = path.with_suffix(".artifact")
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != completion.get(
                "artifact_sha256"
            ):
                raise ValueError("The saved original image response changed")


def require_revision_reconciled(db: Session, source: JobRecord) -> None:
    """Caller holds the workspace lock; completed revisions are not auto-cloned."""
    root_id = source.root_job_id or source.id
    prior = db.scalars(
        select(JobRecord).where(
            JobRecord.workspace_id == source.workspace_id,
            JobRecord.type == JobType.DETAIL_PACK.value,
            or_(JobRecord.id == root_id, JobRecord.root_job_id == root_id),
        )
    ).all()
    for child in prior:
        marker = child.configuration.get("local_revision")
        if marker is None:
            continue
        if (
            not isinstance(marker, Mapping)
            or marker.get("job_id") != child.id
            or marker.get("root_job_id") != root_id
            or marker.get("sha256")
            != canonical_hash({k: v for k, v in marker.items() if k != "sha256"})
        ):
            _reject(
                "An original revision authority changed",
                "NATIVE_DETAIL_REVISION_AUTHORITY_CHANGED",
            )
        if child.status in {
            "queued",
            "planning",
            "processing",
            "cancelling",
            "awaiting_confirmation",
        }:
            _reject(
                "A local revision is already active: " + child.id,
                "NATIVE_DETAIL_REVISION_ALREADY_ACTIVE",
            )
        uncertain = db.scalar(
            select(JobAttemptRecord.id)
            .where(
                JobAttemptRecord.workspace_id == source.workspace_id,
                JobAttemptRecord.job_id == child.id,
                JobAttemptRecord.status.in_({"running", "outcome_unknown"}),
            )
            .limit(1)
        )
        unknown_item = db.scalar(
            select(JobItemRecord.id)
            .where(
                JobItemRecord.workspace_id == source.workspace_id,
                JobItemRecord.job_id == child.id,
                JobItemRecord.error_code == "PROVIDER_OUTCOME_UNKNOWN",
            )
            .limit(1)
        )
        if uncertain is not None or unknown_item is not None:
            _reject(
                "Reconcile the original revision attempt before another paid cycle",
                "NATIVE_DETAIL_REVISION_OUTCOME_UNKNOWN",
            )
        try:
            _require_reconciled_files(detail_workspace(child.id))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            _reject(str(exc), "NATIVE_DETAIL_REVISION_OUTCOME_UNKNOWN")


async def create_revision_job(
    db: Session,
    source: JobRecord,
    storage: ImageStorage,
    settings: Settings,
    gateway: Any,
    *,
    actor_id: str,
    fingerprint: str,
    adjustment: Mapping[str, Any],
    quote_sha256: str,
    approval_text: str,
) -> JobRecord:
    """Recompute authority before any insert; no supplier calls or source writes."""
    lock_workspace_limits(db, source.workspace_id)
    quote = await build_revision_quote(
        db,
        source,
        storage,
        settings,
        gateway,
        fingerprint=fingerprint,
        adjustment=adjustment,
        actor_id=actor_id,
    )
    authority = authorize_revision_quote(
        quote,
        submitted_sha256=quote_sha256,
        approval_text=approval_text,
        source_job_id=source.id,
        workspace_id=source.workspace_id,
        actor_id=actor_id,
    )
    require_revision_reconciled(db, source)
    enforce_active_job_limit(db, source.workspace_id)
    plan = db.get(JobPlanRecord, quote["source_plan_id"])
    if (
        plan is None
        or plan.job_id != source.id
        or canonical_hash(plan.payload) != quote["source_plan_payload_sha256"]
    ):
        _reject("The original approved plan changed")
    items = db.scalars(
        select(JobItemRecord)
        .where(
            JobItemRecord.workspace_id == source.workspace_id,
            JobItemRecord.job_id == source.id,
        )
        .order_by(JobItemRecord.ordinal)
    ).all()
    if (
        len(items) != quote["approved_screen_count"]
        or [i.ordinal for i in items] != list(range(len(items)))
        or any(i.status not in {"succeeded", "reused"} for i in items)
    ):
        _reject("The original accepted screen items changed")
    target_ordinals = {row["ordinal"] for row in quote["source_screens"]}
    if any(
        items[row["ordinal"]].configuration.get("pair_id") != quote["pair_id"]
        for row in quote["source_screens"]
    ):
        _reject("The selected pair differs from original public items")
    child_id = new_id("job")
    intent_value = {
        "version": REVISION_VERSION,
        "job_id": child_id,
        "root_job_id": source.root_job_id or source.id,
        "source_job_id": source.id,
        "workspace_id": source.workspace_id,
        "actor_id": actor_id,
        "source_session_sha256": quote["source_native_session_sha256"],
        "source_generation_binding_sha256": quote["source_generation_binding_sha256"],
        "adjustment": deepcopy(quote["adjustment"]),
        "approval_text": approval_text,
        "authority": authority,
        "authority_sha256": authority["sha256"],
        "delivery_mode": "USER_FINAL_REVIEW",
        "maximum_new_image_requests": 2,
        "maximum_new_model_requests": 5,
    }
    intent = {**intent_value, "sha256": canonical_hash(intent_value)}
    configuration = deepcopy(source.configuration)
    configuration["local_revision"] = intent
    child = JobRecord(
        id=child_id,
        workspace_id=source.workspace_id,
        type=JobType.DETAIL_PACK.value,
        status="queued",
        stage="queued",
        display_name=("详情局部修订 · " + (source.display_name or source.id))[:160],
        configuration=configuration,
        configuration_schema=REVISION_SCHEMA,
        prompt_version=source.prompt_version,
        strategy_version=source.strategy_version,
        release_version=source.release_version,
        root_job_id=intent["root_job_id"],
        expected_item_count=len(items),
        succeeded_item_count=len(items) - 2,
        estimated_cost_min_micros=0,
        estimated_cost_max_micros=quote["maximum_cost_micros"],
        estimated_credits=0,
        currency="USD",
        queued_at=utc_now(),
    )
    db.add(child)
    db.flush()
    copied = {}
    for item in items:
        pending = item.ordinal in target_ordinals
        value = deepcopy(item.configuration)
        for key in ("recovery_only", "aurora_pair_source_job_id"):
            value.pop(key, None)
        value["local_revision_intent_sha256"] = intent["sha256"]
        copied[item.id] = new_id("item")
        db.add(
            JobItemRecord(
                id=copied[item.id],
                workspace_id=source.workspace_id,
                job_id=child.id,
                item_key=item.item_key,
                ordinal=item.ordinal,
                status="pending" if pending else "reused",
                stage="queued" if pending else "reused",
                quality=item.quality,
                configuration=value,
                source_job_item_id=item.id,
                result_source=None if pending else "reused",
            )
        )
    db.flush()
    for link in db.scalars(
        select(JobAssetRecord).where(
            JobAssetRecord.workspace_id == source.workspace_id,
            JobAssetRecord.job_id == source.id,
        )
    ).all():
        # Reuse original inputs and accepted per-item results; never copy a final ZIP.
        if link.direction != "input" and (
            link.direction != "output"
            or link.job_item_id not in copied
            or next(i for i in items if i.id == link.job_item_id).ordinal
            in target_ordinals
        ):
            continue
        db.add(
            JobAssetRecord(
                workspace_id=source.workspace_id,
                job_id=child.id,
                job_item_id=copied.get(link.job_item_id) if link.job_item_id else None,
                asset_id=link.asset_id,
                direction=link.direction,
                role=link.role,
                ordinal=link.ordinal,
                is_deliverable=link.is_deliverable,
                validation=deepcopy(link.validation),
            )
        )
    payload = deepcopy(plan.payload)
    payload.pop("final_manifest", None)
    payload.pop("automatic_delivery", None)
    payload["aurora_v4_3"].pop("delivery", None)
    payload["state"] = "confirmed"
    payload["local_revision"] = intent
    receipt = payload["aurora_v4_3"]["approval_receipt"]
    payload["local_revision_source"] = {
        "source_job_id": source.id,
        "plan_id": plan.id,
        "plan_revision": plan.revision,
        "approval_receipt": deepcopy(receipt),
        "payload_sha256": quote["source_plan_payload_sha256"],
    }
    receipt.pop("automatic_delivery_authorization", None)
    receipt.update(
        approval_text=approval_text,
        actor_user_id=actor_id,
        plan_revision=1,
        local_revision_authority_sha256=authority["sha256"],
    )
    payload.pop("planner_request_count", None)
    payload.pop("local_scope_billing_receipt", None)
    payload["planner_diagnostics"] = []
    payload["cost"] = {
        **payload.get("cost", {}),
        "high_unit_micros": quote["image_unit_micros"],
        "medium_unit_micros": quote["image_unit_micros"],
        "estimated_pending_micros": 2 * quote["image_unit_micros"],
        "local_revision_maximum_micros": quote["maximum_cost_micros"],
    }
    payload["usage"] = {
        "planner": [],
        "image": {"reported_requests": 0, "unreported_requests": 0},
        "reused_from_source_job_id": source.id,
    }
    db.add(
        JobPlanRecord(
            id=new_id("plan"),
            workspace_id=source.workspace_id,
            job_id=child.id,
            revision=1,
            schema_version=plan.schema_version,
            payload=payload,
            payload_hash=canonical_hash(payload),
            validation_status=plan.validation_status,
            validation_errors=[],
            validator_version=plan.validator_version,
            confirmed_at=utc_now(),
        )
    )
    if settings.provider_quota_enabled:
        reserve_provider_job_quota(
            db,
            job=child,
            actor_user_id=actor_id,
            estimated_cost_micros=quote["maximum_cost_micros"],
            provider_request_count=7,
            daily_limit_micros=round(settings.tengli_daily_budget_usd * 1_000_000),
            monthly_limit_micros=round(settings.tengli_monthly_budget_usd * 1_000_000),
            daily_request_limit=settings.tengli_daily_request_limit,
            planner_reserve_micros=quote["review_reserve_micros"],
            image_budget_micros=2 * quote["image_unit_micros"],
            group_count=1,
        )
    reserve_credits(db, source.workspace_id, child, 0)
    return child
