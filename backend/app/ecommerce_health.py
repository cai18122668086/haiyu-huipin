from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from sqlalchemy import func, select

from .domain import CrawlTaskStatus
from .models import CrawlProfileLeaseRecord, CrawlTaskRecord
from .storage import LocalImageStorage, S3ImageStorage


def check_task_broker(url: str) -> str:
    """Return a readiness status without leaking broker credentials."""

    client = Redis.from_url(
        url,
        socket_connect_timeout=1,
        socket_timeout=1,
        health_check_interval=30,
    )
    try:
        client.ping()
    except Exception:
        return "failed"
    finally:
        with suppress(Exception):
            client.close()
    return "ok"


def check_object_storage(storage: Any) -> str:
    """Check the configured bucket or local root without writing user data."""

    try:
        if isinstance(storage, LocalImageStorage):
            return "ok" if storage.root.is_dir() else "failed"
        if isinstance(storage, S3ImageStorage):
            # Development Compose may create the predeclared bucket; production
            # must provision it ahead of time and keeps auto-create disabled.
            storage._ensure_bucket()
            # `_ensure_bucket` intentionally caches successful provisioning, but
            # readiness must still detect a later S3/MinIO outage.  A lightweight
            # HEAD is read-only and does not depend on object contents.
            storage.client.head_bucket(Bucket=storage.bucket)
            return "ok"
    except Exception:
        return "failed"
    return "failed"


def collect_crawl_metrics(session_factory: Any) -> dict[str, Any]:
    """Collect aggregate crawl metrics safe for an unauthenticated health view."""

    now = datetime.now(UTC)
    with session_factory() as session:
        rows = session.execute(
            select(CrawlTaskRecord.status, func.count(CrawlTaskRecord.id)).group_by(
                CrawlTaskRecord.status
            )
        ).all()
        task_counts = {str(status): int(count) for status, count in rows}
        active_leases = int(
            session.scalar(
                select(func.count(CrawlProfileLeaseRecord.id)).where(
                    CrawlProfileLeaseRecord.expires_at > now
                )
            )
            or 0
        )
        expired_leases = int(
            session.scalar(
                select(func.count(CrawlProfileLeaseRecord.id)).where(
                    CrawlProfileLeaseRecord.expires_at <= now
                )
            )
            or 0
        )
        oldest_queued = session.scalar(
            select(func.min(CrawlTaskRecord.queued_at)).where(
                CrawlTaskRecord.status == CrawlTaskStatus.QUEUED.value
            )
        )

    oldest_queued_at = (
        oldest_queued.astimezone(UTC).isoformat()
        if oldest_queued is not None and oldest_queued.tzinfo is not None
        else oldest_queued.replace(tzinfo=UTC).isoformat()
        if oldest_queued is not None
        else None
    )
    queue_age = (
        max(0.0, (now - oldest_queued.replace(tzinfo=UTC)).total_seconds())
        if oldest_queued is not None and oldest_queued.tzinfo is None
        else max(0.0, (now - oldest_queued).total_seconds())
        if oldest_queued is not None
        else 0.0
    )
    return {
        "task_counts": task_counts,
        "active_leases": active_leases,
        "expired_leases": expired_leases,
        "oldest_queued_at": oldest_queued_at,
        "oldest_queued_age_seconds": round(queue_age, 3),
    }
