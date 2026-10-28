import asyncio
import os
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .celery_app import celery_app, settings
from .database import build_database
from .storage import ImageStorage, build_image_storage
from .task_runner import (
    MockTaskRunner,
    build_task_runner,
    validate_task_runner_settings,
)
from .worker_async_runtime import WorkerAsyncRuntime

_async_runtime = WorkerAsyncRuntime()


def _run_operation(job_id: str, *, cancel: bool = False) -> Any:
    async def invoke() -> Any:
        runner = _process_runner()
        return await (runner.cancel(job_id) if cancel else runner.run(job_id))

    if settings.worker_async_runtime_enabled:
        return _async_runtime.run(invoke)
    return asyncio.run(invoke())


validate_task_runner_settings(settings)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_runner: MockTaskRunner | None = None
_storage: ImageStorage | None = None
_owner_pid: int | None = None


def _process_runner() -> MockTaskRunner:
    """Build one Engine/pool per Celery worker process, never per task."""

    global _engine, _session_factory, _runner, _storage, _owner_pid
    process_id = os.getpid()
    if _runner is not None and _owner_pid == process_id:
        return _runner
    if _engine is not None:
        _engine.dispose()
    _engine, _session_factory = build_database(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
    )
    _storage = build_image_storage(settings)
    _runner = build_task_runner(_session_factory, settings, _storage)
    _owner_pid = process_id
    return _runner


@celery_app.task(
    bind=True,
    name="polaris.process_job",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def process_job(self: Any, job_id: str) -> dict[str, str]:
    del self
    terminal_status = _run_operation(job_id)
    return {
        "job_id": job_id,
        "status": terminal_status.value if terminal_status else "not_found",
    }


@celery_app.task(
    bind=True,
    name="polaris.cancel_job",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def cancel_job_task(self: Any, job_id: str) -> dict[str, str]:
    del self
    terminal_status = _run_operation(job_id, cancel=True)
    return {
        "job_id": job_id,
        "status": terminal_status.value if terminal_status else "not_found",
    }
