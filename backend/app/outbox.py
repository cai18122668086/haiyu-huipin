import asyncio
import socket
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .domain import JobStatus, OutboxStatus
from .models import JobRecord, OutboxEventRecord, utc_now
from .task_runner import MockTaskRunner


def publish_pending_events(
    settings: Settings,
    session_factory: sessionmaker[Session],
    task_runner: MockTaskRunner,
    *,
    limit: int = 20,
) -> int:
    worker_id = f"outbox-{socket.gethostname()}-{uuid4().hex}"
    now = utc_now()
    stale_lock_before = now - timedelta(minutes=5)
    live_jobs = (
        _live_celery_job_ids() if settings.task_dispatcher == "celery" else set()
    )
    with session_factory() as session:
        # A slow provider call is not a lost broker delivery. In particular,
        # set audits have no running image-item lease to fence duplicate calls.
        if live_jobs is not None:
            _requeue_lost_deliveries(session, now, live_job_ids=live_jobs)
        events = session.scalars(
            select(OutboxEventRecord)
            .where(
                or_(
                    and_(
                        OutboxEventRecord.status.in_(
                            [
                                OutboxStatus.PENDING.value,
                                OutboxStatus.FAILED.value,
                            ]
                        ),
                        OutboxEventRecord.available_at <= now,
                    ),
                    and_(
                        OutboxEventRecord.status == OutboxStatus.PUBLISHING.value,
                        OutboxEventRecord.locked_at.is_not(None),
                        OutboxEventRecord.locked_at < stale_lock_before,
                    ),
                ),
            )
            .order_by(OutboxEventRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        event_ids = [event.id for event in events]
        for event in events:
            event.status = OutboxStatus.PUBLISHING.value
            event.locked_at = utc_now()
            event.locked_by = worker_id
            event.attempt_count += 1
        session.commit()

    published = 0
    for event_id in event_ids:
        try:
            with session_factory() as session:
                current_event = session.get(OutboxEventRecord, event_id)
                if current_event is None:
                    continue
                event_type = current_event.event_type
                dedupe_key = current_event.dedupe_key
                payload = dict(current_event.payload)
            _dispatch(
                settings,
                task_runner,
                event_type=event_type,
                dedupe_key=dedupe_key,
                payload=payload,
            )
            with session_factory() as session:
                changed_id = session.scalar(
                    update(OutboxEventRecord)
                    .where(
                        OutboxEventRecord.id == event_id,
                        OutboxEventRecord.status == OutboxStatus.PUBLISHING.value,
                        OutboxEventRecord.locked_by == worker_id,
                    )
                    .values(
                        status=OutboxStatus.PUBLISHED.value,
                        published_at=utc_now(),
                        last_error=None,
                        locked_at=None,
                        locked_by=None,
                    )
                    .returning(OutboxEventRecord.id)
                )
                session.commit()
                published += int(changed_id is not None)
        except Exception as exc:
            with session_factory() as session:
                failed_event = session.scalar(
                    select(OutboxEventRecord).where(
                        OutboxEventRecord.id == event_id,
                        OutboxEventRecord.status == OutboxStatus.PUBLISHING.value,
                        OutboxEventRecord.locked_by == worker_id,
                    )
                )
                if failed_event is not None:
                    failed_event.status = OutboxStatus.FAILED.value
                    failed_event.available_at = utc_now() + timedelta(
                        seconds=min(60, 2 ** min(failed_event.attempt_count, 5))
                    )
                    failed_event.last_error = _safe_error(exc)
                    failed_event.locked_at = None
                    failed_event.locked_by = None
                session.commit()
    return published


def _live_celery_job_ids() -> set[str] | None:
    """Protect active/reserved deliveries; an unknown snapshot is not proof of loss."""
    from .celery_app import celery_app

    try:
        inspector = celery_app.control.inspect(timeout=1)
        active, reserved = inspector.active(), inspector.reserved()
        if not active or reserved is None:
            return None
        job_ids: set[str] = set()
        for snapshot in (active, reserved):
            for tasks in snapshot.values():
                for task in tasks:
                    if task.get("name") != "polaris.process_job":
                        continue
                    args = task.get("args")
                    if (
                        not isinstance(args, (list, tuple))
                        or not args
                        or not isinstance(args[0], str)
                    ):
                        return None
                    job_ids.add(args[0])
        return job_ids
    except Exception:
        # New pending events can still be published. Postgres recovery of old
        # acknowledgements waits for a usable worker snapshot instead of
        # submitting another paid call while the broker's state is unknown.
        return None


def _requeue_lost_deliveries(
    session: Session, now: Any, *, live_job_ids: set[str] | None = None,
) -> None:
    """Restore broker deliveries from PostgreSQL after Redis/task loss.

    A published event is a delivery acknowledgement, not completion state.
    Non-terminal jobs therefore make old acknowledgements eligible again.
    Database claims and fencing make the resulting at-least-once delivery safe.
    """

    active_job_ids = select(JobRecord.id).where(
        JobRecord.status.in_(
            [
                JobStatus.QUEUED.value,
                JobStatus.PLANNING.value,
                JobStatus.PROCESSING.value,
                JobStatus.CANCELLING.value,
            ]
        )
    )
    if live_job_ids:
        active_job_ids = active_job_ids.where(JobRecord.id.not_in(live_job_ids))
    session.execute(
        update(OutboxEventRecord)
        .where(
            OutboxEventRecord.aggregate_type == "job",
            OutboxEventRecord.aggregate_id.in_(active_job_ids),
            OutboxEventRecord.status == OutboxStatus.PUBLISHED.value,
            OutboxEventRecord.published_at.is_not(None),
            OutboxEventRecord.published_at <= now - timedelta(seconds=30),
        )
        .values(
            status=OutboxStatus.PENDING.value,
            available_at=now,
            published_at=None,
            last_error="delivery acknowledgement expired before job completion",
        )
    )


def _dispatch(
    settings: Settings,
    task_runner: MockTaskRunner,
    *,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> None:
    job_id = str(payload["job_id"])
    if settings.task_dispatcher == "inline":
        if event_type == "job.cancel_requested":
            asyncio.run(task_runner.cancel(job_id))
        else:
            asyncio.run(task_runner.run(job_id))
        return

    from .tasks import cancel_job_task, process_job

    task: Any = cancel_job_task if event_type == "job.cancel_requested" else process_job
    # Celery task ids are observability identifiers only. Database attempts and
    # lease fencing are the sole duplicate-execution guard.
    options: dict[str, Any] = {}
    with task_runner.session_factory() as session:
        job = session.get(JobRecord, job_id)
        from .amazon_auto_start import eligible as automatic_amazon

        if job is not None and (
            job.type == "domestic_detail_review"
            or job.type == "detail_pack"
            and job.configuration.get("domestic_detail_pipeline")
            in {"aurora-domestic-detail-v4.3.1", "xinghe-domestic-detail-v4.0"}
            or automatic_amazon(job)
        ):
            # Keep historical tasks and other products on their frozen workers.
            # A dedicated current-runtime consumer owns only this new queue.
            options["queue"] = "domestic_detail_v431"
        elif (job is not None and job.type == "reference_remix"
              and job.configuration.get("replica_session_id")):
            options["queue"] = "one_click_replica"
    task.apply_async(
        args=[job_id], headers={"outbox_dedupe_key": dedupe_key}, **options
    )


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:500]}"
