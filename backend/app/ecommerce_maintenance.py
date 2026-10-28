from __future__ import annotations

import argparse
import asyncio
import logging
import time

from .ecommerce_crawl_tasks import (
    cleanup_orphaned_crawl_assets,
    recover_interrupted_crawl_tasks,
    resume_queued_crawl_tasks,
)
from .ecommerce_worker import _build_runtime

logger = logging.getLogger(__name__)


def run_once() -> dict[str, int]:
    """Run maintenance steps with independent failure isolation.

    A transient storage failure must not prevent stale lease recovery, and a
    database/queue failure must be visible in the result and logs instead of
    making a long-running maintenance process silently exit.
    """

    started = time.monotonic()
    runtime = _build_runtime()
    result = {
        "recovered": 0,
        "resumed": 0,
        "orphan_assets_cleaned": 0,
        "recovery_errors": 0,
        "resume_errors": 0,
        "cleanup_errors": 0,
        "duration_ms": 0,
    }

    try:
        recovered = recover_interrupted_crawl_tasks(runtime.state.session_factory)
        result["recovered"] = len(recovered)
    except Exception:
        result["recovery_errors"] += 1
        logger.exception("ecommerce maintenance recovery step failed")

    try:
        with runtime.state.session_factory() as session:
            # Count before publishing so the pass reports work handed to the
            # queue without exposing task ids or request payloads.
            from sqlalchemy import func, select

            from .domain import CrawlTaskStatus
            from .models import CrawlTaskRecord

            result["resumed"] = int(
                session.scalar(
                    select(func.count(CrawlTaskRecord.id)).where(
                        CrawlTaskRecord.status == CrawlTaskStatus.QUEUED.value
                    )
                )
                or 0
            )
        resume_queued_crawl_tasks(runtime, runtime.state.session_factory)
    except Exception:
        result["resume_errors"] += 1
        logger.exception("ecommerce maintenance queue resume step failed")

    try:
        result["orphan_assets_cleaned"] = asyncio.run(
            cleanup_orphaned_crawl_assets(runtime)
        )
    except Exception:
        result["cleanup_errors"] += 1
        logger.exception("ecommerce maintenance orphan asset cleanup failed")

    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    logger.info("ecommerce maintenance pass completed: %s", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Polaris ecommerce maintenance worker")
    parser.add_argument(
        "--once", action="store_true", help="run one maintenance pass and exit"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="seconds between maintenance passes (default: 60)",
    )
    args = parser.parse_args()
    interval = max(10, args.interval)
    while True:
        print(run_once(), flush=True)
        if args.once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
