import asyncio
import logging
import time

from .config import Settings
from .database import build_database
from .outbox import publish_pending_events
from .retention import run_retention_cycle
from .storage import build_image_storage
from .task_runner import build_task_runner, validate_task_runner_settings

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    settings.validate_runtime_safety()
    validate_task_runner_settings(settings)
    engine, session_factory = build_database(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
    )
    storage = build_image_storage(settings)
    runner = build_task_runner(session_factory, settings, storage)
    next_retention_at = 0.0
    try:
        while True:
            published = publish_pending_events(
                settings,
                session_factory,
                runner,
                limit=settings.outbox_batch_size,
            )
            now = time.monotonic()
            if settings.retention_enabled and now >= next_retention_at:
                try:
                    asyncio.run(
                        run_retention_cycle(
                            session_factory,
                            storage,
                            batch_size=settings.retention_batch_size,
                            aurora_work_root=settings.aurora_work_root,
                        )
                    )
                except Exception:
                    logger.exception("retention_cycle_failed")
                next_retention_at = now + settings.retention_interval_seconds
            time.sleep(0.5 if published else 2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
