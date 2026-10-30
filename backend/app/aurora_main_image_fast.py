"""Routing and recovery for the retained original-style main-image fast workflow."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .aurora_main_image_native_fast import validate_plan
from .aurora_main_image_planner import AuroraPlanningError
from .domain import JobStatus
from .errors import ApiError
from .models import JobAssetRecord, JobAttemptRecord, JobPlanRecord, JobRecord


def selected(configuration: Any) -> bool:
    return (
        isinstance(configuration, dict)
        and (configuration.get("settings") or {}).get("main_image_speed")
        == "native_fast"
    )


def require_recoverable_plan(
    session: Any, job: JobRecord, *, allow_replanning: bool = False
) -> bool:
    """Validate recovery and return whether a fresh plan is required."""
    if job.type != "domestic_main_image" or not selected(job.configuration):
        return False
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    # 仅规划阶段失败且从未启动生图的任务可重新规划；原方案重试仍须完整授权。
    if (
        allow_replanning
        and plan is None
        and job.status == JobStatus.FAILED.value
        and job.stage == "planning_failed"
        and job.expected_item_count == 6
        and job.succeeded_item_count == 0
        and not job.configuration.get("_main_image_fast_invocations")
        and session.scalar(
            select(JobAttemptRecord.id)
            .where(JobAttemptRecord.job_id == job.id)
            .limit(1)
        )
        is None
        and session.scalar(
            select(JobAssetRecord.id)
            .where(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.direction == "output",
            )
            .limit(1)
        )
        is None
    ):
        return True
    if plan is None or plan.confirmed_at is None:
        raise ApiError(
            409,
            "FAST_MAIN_IMAGE_PLAN_UNAVAILABLE",
            "原任务缺少已确认的完整六图方案，请核对原规划结果后新建任务。",
        )
    try:
        validate_plan(plan, job)
    except AuroraPlanningError as error:
        raise ApiError(409, "FAST_MAIN_IMAGE_PLAN_CHANGED", str(error)) from error
    receipts = job.configuration.get("_main_image_fast_invocations", {})
    if any(
        receipt.get("state") in {"invoked", "unknown"} for receipt in receipts.values()
    ):
        raise ApiError(
            409,
            "PROVIDER_OUTCOME_UNKNOWN",
            "原生图结果尚未核实，请先找回原任务，避免重复生成。",
        )
    return False


class MainImagePlannerRouter:
    def __init__(
        self,
        session_factory: Any,
        standard: Any,
        native_fast: Any,
    ) -> None:
        self.session_factory, self.standard = session_factory, standard
        self.work_root = standard.work_root
        self.native_fast = native_fast

    async def plan(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            native = job is not None and selected(job.configuration)
        return await (self.native_fast if native else self.standard).plan(job_id)


class MainImageProviderRouter:
    def __init__(self, session_factory: Any, standard: Any, fast: Any) -> None:
        self.session_factory, self.standard, self.fast = session_factory, standard, fast
        self.name, self.model, self.pricing_version = (
            standard.name,
            standard.model,
            standard.pricing_version,
        )

    async def execute(self, request: Any) -> Any:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            fast = job is not None and selected(job.configuration)
        return await (self.fast if fast else self.standard).execute(request)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.standard, name)
