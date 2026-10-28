from celery import Celery

from .config import Settings

settings = Settings()
celery_app = Celery(
    "polaris",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks", "app.ecommerce_worker"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    result_backend_thread_safe=True,
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # Thread workers cannot terminate an individual native thread. Existing
    # database fencing and provider receipts guard broker redeliveries.
    worker_cancel_long_running_tasks_on_connection_loss=(
        not settings.worker_async_runtime_enabled
    ),
    broker_transport_options={"visibility_timeout": 14_400},
    result_backend_transport_options={"visibility_timeout": 14_400},
    visibility_timeout=14_400,
    worker_soft_shutdown_timeout=30,
    task_routes={
        "polaris.process_job": {"queue": "default"},
        "polaris.cancel_job": {"queue": "default"},
        "polaris.process_crawl_task": {"queue": "crawl"},
    },
)
