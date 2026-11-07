"""Customer credits are charged for successful output images, not API calls."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .domain import JobItemStatus, JobType
from .errors import ApiError
from .models import CreditReservationRecord, JobAssetRecord, JobItemRecord, JobRecord

PRICING_VERSION = "image-credits-5-15-v1"
SINGLE_IMAGE_CREDITS = 5
PLANNED_IMAGE_CREDITS = 15
PRICING_KEY = "customer_credit_pricing"


def image_unit_credits(
    job_type: str, configuration: Mapping[str, Any] | None = None
) -> int:
    """Classify every executable job, including product-image presets."""
    kind = JobType(job_type)
    if kind == JobType.DOMESTIC_DETAIL_REVIEW:
        return 0  # Review/export/reconciliation produces no new customer image.
    if kind in {JobType.LOCAL_EDIT, JobType.MODEL_POSE_TRANSFER}:
        return SINGLE_IMAGE_CREDITS
    if kind == JobType.PRODUCT_IMAGE:
        settings = (configuration or {}).get("settings", configuration or {})
        return (
            PLANNED_IMAGE_CREDITS
            if settings.get("task_type") == "scene_main"
            else SINGLE_IMAGE_CREDITS
        )
    # Creative compositions, remix, domestic/cross-border detail, domestic
    # main-image campaigns and Amazon suites all include planning.
    return PLANNED_IMAGE_CREDITS


def bind_image_reservation(
    session: Session, job: JobRecord
) -> tuple[int, dict[str, Any]]:
    """Freeze a server-owned tariff and the exact runnable item IDs once.

    Preserved REUSED items are excluded. This also covers retry/clone children
    and the local-revision constructor, whose old estimate was always zero.
    """
    session.flush()
    ids = list(
        session.scalars(
            select(JobItemRecord.id)
            .where(
                JobItemRecord.workspace_id == job.workspace_id,
                JobItemRecord.job_id == job.id,
                JobItemRecord.status == JobItemStatus.PENDING.value,
            )
            .order_by(JobItemRecord.ordinal)
        )
    )
    unit = image_unit_credits(job.type, job.configuration)
    if not ids and job.expected_item_count and unit:
        raise ApiError(
            409, "CREDIT_ITEMS_NOT_READY", "创作图片数量尚未确定，请重新提交。"
        )
    job.estimated_credits = unit * billable_units(job, len(ids))
    return job.estimated_credits, {
        "version": PRICING_VERSION,
        "credits_per_image": unit,
        "item_ids": ids,
        "billable_units": billable_units(job, len(ids)),
    }


def billable_units(job: JobRecord, runnable: int) -> int:
    """Charge what the customer submitted, not how many screens it split into.

    One-click replica holds the reference images the customer chose. Long
    references are cut into extra screens downstream, so counting runnable
    items would bill the customer for the split. ``billable_image_count`` is
    written by the replica session that owns the charge and caps the bill at
    the submitted count; every other job type keeps item-based billing.
    """
    cap = _settings(job.configuration).get("billable_image_count")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0:
        return min(runnable, cap)
    return runnable


def _settings(configuration: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Job configuration nests the request under ``settings``."""
    if not isinstance(configuration, Mapping):
        return {}
    nested = configuration.get("settings")
    return nested if isinstance(nested, Mapping) else configuration


def successful_image_credits(session: Session, job: JobRecord) -> int | None:
    """None means a historical reservation: preserve its original accounting.

    A selected image recovered from its original call may become REUSED; it is
    charged once on this reservation, whereas preserved sibling IDs are absent.
    """
    reservation = session.scalar(
        select(CreditReservationRecord).where(
            CreditReservationRecord.workspace_id == job.workspace_id,
            CreditReservationRecord.job_id == job.id,
        )
    )
    if reservation is None:
        return None
    pricing = reservation.pricing_snapshot
    if not isinstance(pricing, dict) or pricing.get("version") != PRICING_VERSION:
        return None
    # Later reviews, retention or output removal must not rewrite a settled bill.
    if reservation.status in {"settled", "released"}:
        return reservation.settled_credits
    unit = pricing["credits_per_image"]
    ids = pricing["item_ids"]
    billable = pricing.get("billable_units")
    succeeded = (
        session.scalar(
            select(func.count(func.distinct(JobItemRecord.id)))
            .select_from(JobItemRecord)
            .join(JobAssetRecord, JobAssetRecord.job_item_id == JobItemRecord.id)
            .where(
                JobAssetRecord.workspace_id == job.workspace_id,
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.is_deliverable.is_(True),
                JobItemRecord.workspace_id == job.workspace_id,
                JobItemRecord.job_id == job.id,
                JobItemRecord.id.in_(ids),
                JobItemRecord.status.in_(
                    [JobItemStatus.SUCCEEDED.value, JobItemStatus.REUSED.value]
                ),
            )
        )
        or 0
    )
    # A capped reservation (replica screens beyond the submitted count) settles
    # on the same ceiling it reserved, so a partial run can never overcharge.
    if isinstance(billable, int) and not isinstance(billable, bool):
        succeeded = min(int(succeeded), billable)
    return int(succeeded) * int(unit)


def pricing_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for kind in JobType:
        if kind == JobType.PRODUCT_IMAGE:
            for preset in (
                "scene_main",
                "white_background",
                "model_image",
                "change_model",
                "try_on",
                "change_scene",
                "change_background",
                "change_color",
            ):
                items.append(
                    {
                        "job_type": kind.value,
                        "task_type": preset,
                        "credits_per_image": image_unit_credits(
                            kind.value, {"task_type": preset}
                        ),
                    }
                )
        else:
            items.append(
                {
                    "job_type": kind.value,
                    "task_type": None,
                    "credits_per_image": image_unit_credits(kind.value),
                }
            )
    return items
