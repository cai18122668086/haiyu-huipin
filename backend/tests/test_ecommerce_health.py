from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr


def test_object_storage_readiness_rechecks_cached_s3_bucket(monkeypatch) -> None:
    from app.ecommerce_health import check_object_storage
    from app.storage import S3ImageStorage

    class _Client:
        def __init__(self) -> None:
            self.calls = 0

        def head_bucket(self, *, Bucket: str) -> None:
            assert Bucket == "bucket"
            self.calls += 1

    storage = object.__new__(S3ImageStorage)
    storage._bucket_ready = True
    storage.bucket = "bucket"
    storage.client = _Client()

    assert check_object_storage(storage) == "ok"
    assert storage.client.calls == 1

    def _failed(*, Bucket: str) -> None:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(storage.client, "head_bucket", _failed)
    assert check_object_storage(storage) == "failed"


def test_ecommerce_health_exposes_aggregate_queue_and_lease_metrics(
    client: TestClient,
) -> None:
    response = client.get("/health/ecommerce")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["metrics"]["task_counts"] == {}
    assert payload["metrics"]["active_leases"] == 0
    assert payload["metrics"]["expired_leases"] == 0
    assert payload["metrics"]["oldest_queued_at"] is None
    assert payload["metrics"]["oldest_queued_age_seconds"] == 0.0


def test_ecommerce_health_counts_queued_tasks_without_exposing_payload(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.ecommerce_crawl_tasks._schedule", lambda app, task_id: None
    )
    created = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
        },
    )
    assert created.status_code == 202

    response = client.get("/health/ecommerce")
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["task_counts"]["queued"] == 1
    assert "source_url" not in response.text
    assert "profile_path" not in response.text


def test_health_ready_reports_broker_only_for_celery(settings, monkeypatch) -> None:
    settings.task_dispatcher = "celery"
    settings.celery_broker_url = "redis://example.invalid:6379/0"
    monkeypatch.setattr(
        "app.main.check_task_broker", lambda url: "failed" if url else "ok"
    )

    from app.main import create_app

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["task_broker"] == "failed"


def test_metrics_endpoint_requires_operator_token_and_reports_pool(
    client, settings
) -> None:
    assert client.get("/health/metrics").status_code == 404

    settings.metrics_token = SecretStr("metrics-test-token")
    from app.main import create_app

    with TestClient(create_app(settings)) as token_client:
        token_client.get("/health/live")
        response = token_client.get(
            "/health/metrics", headers={"X-Metrics-Token": "metrics-test-token"}
        )

    assert response.status_code == 200
    assert "polaris_http_requests_total" in response.text
    assert "polaris_database_pool_size" in response.text
