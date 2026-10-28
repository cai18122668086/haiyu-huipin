import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .domain import TERMINAL_JOB_STATUSES, AssetLifecycleStatus
from .models import (
    AnnouncementRecord,
    AssetRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
)
from .storage import ImageStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionResult:
    expired_jobs: int = 0
    claimed_assets: int = 0
    purged_assets: int = 0
    failed_assets: int = 0
    purged_bytes: int = 0
    purged_workspaces: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


def _expire_job_history(session: Session, *, now: datetime, batch_size: int) -> int:
    job_ids = session.scalars(
        select(JobRecord.id)
        .where(
            JobRecord.history_deleted_at.is_(None),
            JobRecord.history_expires_at.is_not(None),
            JobRecord.history_expires_at <= now,
        )
        .order_by(JobRecord.history_expires_at, JobRecord.id)
        .limit(batch_size)
    ).all()
    if not job_ids:
        return 0
    session.execute(delete(JobPlanRecord).where(JobPlanRecord.job_id.in_(job_ids)))
    session.execute(delete(JobAssetRecord).where(JobAssetRecord.job_id.in_(job_ids)))
    session.execute(
        update(JobItemRecord)
        .where(JobItemRecord.job_id.in_(job_ids))
        .values(configuration={}, error_message=None, error_details=None)
    )
    session.execute(
        update(JobAttemptRecord)
        .where(JobAttemptRecord.job_id.in_(job_ids))
        .values(error_message=None, error_details=None)
    )
    session.execute(
        update(JobRecord)
        .where(JobRecord.id.in_(job_ids), JobRecord.history_deleted_at.is_(None))
        .values(
            history_deleted_at=now,
            display_name=None,
            configuration={},
            error_message=None,
            error_details=None,
            cancel_reason=None,
        )
    )
    return len(job_ids)


def _claim_due_assets(
    session: Session, *, now: datetime, batch_size: int
) -> list[AssetRecord]:
    active_job_reference = exists(
        select(JobAssetRecord.id)
        .join(JobRecord, JobRecord.id == JobAssetRecord.job_id)
        .where(
            JobAssetRecord.asset_id == AssetRecord.id,
            JobRecord.status.not_in([status.value for status in TERMINAL_JOB_STATUSES]),
        )
    )
    announcement_reference = exists(
        select(AnnouncementRecord.id).where(
            AnnouncementRecord.image_asset_id == AssetRecord.id
        )
    )
    candidates = session.scalars(
        select(AssetRecord)
        .where(
            AssetRecord.retention_class == "user_30d",
            AssetRecord.purged_at.is_(None),
            ~active_job_reference,
            ~announcement_reference,
            or_(
                (
                    AssetRecord.deleted_at.is_(None)
                    & AssetRecord.expires_at.is_not(None)
                    & (AssetRecord.expires_at <= now)
                ),
                (
                    AssetRecord.deleted_at.is_not(None)
                    & or_(
                        AssetRecord.purge_next_retry_at.is_(None),
                        AssetRecord.purge_next_retry_at <= now,
                    )
                ),
            ),
        )
        .order_by(AssetRecord.expires_at, AssetRecord.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    for asset in candidates:
        if asset.deleted_at is None:
            asset.deleted_at = now
            asset.lifecycle_status = AssetLifecycleStatus.DELETED.value
    return list(candidates)


async def run_retention_cycle(
    session_factory: sessionmaker[Session],
    storage: ImageStorage,
    *,
    batch_size: int = 100,
    aurora_work_root: Path | None = None,
) -> RetentionResult:
    now = _now()
    with session_factory() as session:
        expired_jobs = _expire_job_history(session, now=now, batch_size=batch_size)
        assets = _claim_due_assets(session, now=now, batch_size=batch_size)
        claimed = [
            (asset.id, asset.object_key, asset.object_version, asset.byte_size)
            for asset in assets
        ]
        session.commit()

    purged_assets = 0
    failed_assets = 0
    purged_bytes = 0
    for asset_id, object_key, object_version, byte_size in claimed:
        try:
            await storage.delete_object(object_key, object_version)
        except Exception as exc:
            failed_assets += 1
            with session_factory() as session:
                asset = session.get(AssetRecord, asset_id)
                if asset is not None and asset.purged_at is None:
                    asset.purge_attempts += 1
                    delay_seconds = min(3600, 60 * (2 ** min(asset.purge_attempts, 6)))
                    asset.purge_next_retry_at = _now() + timedelta(
                        seconds=delay_seconds
                    )
                    asset.purge_last_error = type(exc).__name__[:120]
                    session.commit()
            logger.exception(
                "retention_asset_purge_failed",
                extra={"asset_id": asset_id},
            )
            continue

        with session_factory() as session:
            asset = session.get(AssetRecord, asset_id)
            if asset is not None and asset.purged_at is None:
                asset.purged_at = _now()
                asset.purge_next_retry_at = None
                asset.purge_last_error = None
                session.commit()
        purged_assets += 1
        purged_bytes += byte_size

    purged_workspaces = 0
    if aurora_work_root is not None:
        with session_factory() as session:
            expired_aurora_ids = session.scalars(
                select(JobRecord.id)
                .where(
                    JobRecord.type == "domestic_main_image",
                    JobRecord.history_deleted_at.is_not(None),
                )
                .limit(batch_size)
            ).all()
        root = aurora_work_root.resolve()
        for job_id in expired_aurora_ids:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
                continue
            workspace = (root / job_id).resolve()
            if root not in workspace.parents or not workspace.exists():
                continue
            try:
                shutil.rmtree(workspace)
            except OSError:
                logger.exception(
                    "retention_aurora_workspace_purge_failed",
                    extra={"job_id": job_id},
                )
            else:
                purged_workspaces += 1

    result = RetentionResult(
        expired_jobs=expired_jobs,
        claimed_assets=len(claimed),
        purged_assets=purged_assets,
        failed_assets=failed_assets,
        purged_bytes=purged_bytes,
        purged_workspaces=purged_workspaces,
    )
    if any(result.__dict__.values()):
        logger.info("retention_cycle_completed", extra=result.__dict__)
    return result
