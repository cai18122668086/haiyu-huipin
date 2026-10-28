from __future__ import annotations

import asyncio
import os
import socket
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .celery_app import celery_app, settings
from .database import build_database
from .ecommerce_crawl_tasks import _run_task
from .ecommerce_session import EcommerceSessionManager
from .models import CrawlTaskRecord
from .storage import ImageStorage, build_image_storage

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_storage: ImageStorage | None = None
_runtime: Any | None = None
_owner_pid: int | None = None

# Celery imports task modules independently from the API process. Validate the
# same fail-closed production policy before this worker can accept messages.
settings.validate_runtime_safety()


def _build_runtime() -> Any:
    """Build the minimal API-compatible runtime needed by a crawl worker.

    The worker deliberately does not import ``app.main``. That keeps the API
    process and the worker process independently restartable without creating a
    second FastAPI application or sharing browser state accidentally.
    """

    global _engine, _session_factory, _storage, _runtime, _owner_pid
    process_id = os.getpid()
    if _runtime is not None and _owner_pid == process_id:
        return _runtime
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
    state = SimpleNamespace(
        settings=settings,
        session_factory=_session_factory,
        storage=_storage,
        ecommerce_crawl_handles=set(),
        ecommerce_account_sessions={},
    )
    state.ecommerce_session = EcommerceSessionManager(
        settings.ecommerce_session_dir,
        platform="taobao",
        session_profile="domestic-taobao-tmall",
        state_style="legacy",
    )
    state.ecommerce_session_1688 = EcommerceSessionManager(
        settings.ecommerce_1688_session_dir,
        platform="alibaba1688",
        session_profile="domestic-1688",
        state_style="canonical",
    )
    state.ecommerce_session_jd = EcommerceSessionManager(
        settings.ecommerce_jd_session_dir,
        platform="jd",
        session_profile="domestic-jd",
        state_style="canonical",
    )
    state.ecommerce_session_pinduoduo = EcommerceSessionManager(
        settings.ecommerce_pinduoduo_session_dir,
        platform="pinduoduo",
        session_profile="domestic-pinduoduo",
        state_style="canonical",
    )
    state.ecommerce_sessions = {
        "taobao": state.ecommerce_session,
        "1688": state.ecommerce_session_1688,
        "jd": state.ecommerce_session_jd,
        "pinduoduo": state.ecommerce_session_pinduoduo,
    }
    _runtime = SimpleNamespace(state=state)
    _owner_pid = process_id
    return _runtime


def enqueue_crawl_task(task_id: str, *, countdown: int = 0) -> Any:
    """Publish a durable crawl message for the dedicated ``crawl`` queue."""

    return celery_app.send_task(
        "polaris.process_crawl_task",
        args=[task_id],
        countdown=max(0, countdown),
        queue="crawl",
    )


@celery_app.task(
    bind=True,
    name="polaris.process_crawl_task",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def process_crawl_task(self: Any, task_id: str) -> dict[str, str]:
    del self
    runtime = _build_runtime()
    worker_id = (
        f"crawl-worker-{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:10]}"
    )
    asyncio.run(_run_task(runtime, task_id, worker_id=worker_id))
    session_factory = _session_factory
    if session_factory is None:
        raise RuntimeError("crawl worker database is not initialized")
    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        return {
            "task_id": task_id,
            "status": task.status if task is not None else "not_found",
        }

