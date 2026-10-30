"""Customer credits are charged for successful output images, not API calls."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .domain import JobItemStatus, JobType
from .errors import ApiError
from .models import CreditReservationRecord, JobAssetRecord, JobItemRecord, JobRecord

PRICING_VERSION = "image-credits-5-8-15-v2"
SUPPORTED_PRICING_VERSIONS = {PRICING_VERSION, "image-credits-5-15-v1"}
SINGLE_IMAGE_CREDITS = 5
FAST_IMAGE_CREDITS = 8
PLANNED_IMAGE_CREDITS = 15
PRICING_KEY = "customer_credit_pricing"


def image_unit_credits(
    job_type: str, configuration: Mapping[str, Any] | None = None
) -> int:
    """Classify every executable job, including product-image presets."""
    kind = JobType(job_type)
    if kind == JobType.DOMESTIC_DETAIL_REVIEW:
        return 0  # Review/export/reconciliation produces no new customer image.
    config = configuration or {}
    settings = config.get("settings", config)
    # This provenance is written only after the published case is validated.
    # A user-supplied field inside settings must never activate this tariff.
    if config.get("inspiration_case_id") and config.get("inspiration_workflow"):
        return SINGLE_IMAGE_CREDITS
    if (
        (
            kind == JobType.DOMESTIC_MAIN_IMAGE
            and settings.get("main_image_speed") == "native_fast"
        )
        or (kind == JobType.DETAIL_PACK and settings.get("detail_speed") == "fast")
        or (
            kind == JobType.AMAZON_VISUAL_SUITE
            and settings.get("amazon_speed") == "fast"
        )
        or (
            kind == JobType.REFERENCE_REMIX
            and settings.get("reference_skill")
            in {
                "aurora-ecommerce-reference-remix-fast",
                "all-category-style-benchmark-still-fast",
                "aurora-remix-fast",
            }
        )
    ):
        return FAST_IMAGE_CREDITS
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
    job.estimated_credits = unit * len(ids)
    return job.estimated_credits, {
        "version": PRICING_VERSION,
        "credits_per_image": unit,
        "item_ids": ids,
    }


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
    if (
        not isinstance(pricing, dict)
        or pricing.get("version") not in SUPPORTED_PRICING_VERSIONS
    ):
        return None
    # Later reviews, retention or output removal must not rewrite a settled bill.
    if reservation.status in {"settled", "released"}:
        return reservation.settled_credits
    unit = pricing["credits_per_image"]
    ids = pricing["item_ids"]
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
    # Version-specific rules accompany the legacy default rules so older
    # clients remain compatible while new clients can quote the chosen mode.
    for kind in (
        JobType.DOMESTIC_MAIN_IMAGE,
        JobType.DETAIL_PACK,
        JobType.AMAZON_VISUAL_SUITE,
        JobType.REFERENCE_REMIX,
    ):
        items.append(
            {
                "job_type": kind.value,
                "task_type": None,
                "credits_per_image": FAST_IMAGE_CREDITS,
                "variant": "fast",
            }
        )
    items.append(
        {
            "job_type": "inspiration",
            "task_type": None,
            "credits_per_image": SINGLE_IMAGE_CREDITS,
            "variant": "inspiration",
        }
    )
    return items
