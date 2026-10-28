from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import app.ecommerce_crawl_tasks as crawl_tasks
import app.ecommerce_worker as ecommerce_worker
from app.main import create_app
from app.models import (
    AssetRecord,
    CrawlProfileLeaseRecord,
    CrawlTaskRecord,
    EcommerceWorkerRecord,
    ProductAssetRecord,
    ProductSnapshotRecord,
    utc_now,
)
from app.storage import ValidatedImage


def _wait_for_status(
    client: TestClient, task_id: str, expected: set[str]
) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/ecommerce/crawl-tasks/{task_id}")
        assert response.status_code == 200
        latest = response.json()["data"]
        if latest["status"] in expected:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"task did not reach {expected}: {latest}")


def test_crawl_task_create_query_events_and_result(
    client: TestClient, monkeypatch
) -> None:
    async def fake_fetch(request, payload):
        return {
            "success": True,
            "data": {
                "platform": "taobao",
                "title": "测试商品",
                "imageUrls": ["https://img.example.test/1.jpg"],
            },
        }

    monkeypatch.setattr(crawl_tasks, "fetch_item", fake_fetch)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        headers={"Idempotency-Key": "crawl-test-1"},
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "includeDescImages": False,
        },
    )
    assert response.status_code == 202
    task = response.json()["data"]
    assert task["status"] == "queued"

    completed = _wait_for_status(client, task["id"], {"succeeded"})
    assert completed["progress"] == 100
    result = client.get(f"/api/ecommerce/crawl-tasks/{task['id']}/result")
    assert result.status_code == 200
    assert result.json()["data"]["result"]["title"] == "测试商品"

    events = client.get(f"/api/ecommerce/crawl-tasks/{task['id']}/events")
    assert events.status_code == 200
    statuses = [event["to_status"] for event in events.json()["data"]]
    assert statuses[:2] == ["created", "queued"]
    assert "running" in statuses
    assert statuses[-1] == "succeeded"


def test_crawl_task_idempotency_reuses_task_and_rejects_payload_change(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    payload = {
        "workspace_id": "ws_test",
        "url": "https://item.taobao.com/item.htm?id=123456789012",
        "platform": "taobao",
    }
    first = client.post(
        "/api/ecommerce/crawl-tasks",
        headers={"Idempotency-Key": "crawl-test-2"},
        json=payload,
    )
    second = client.post(
        "/api/ecommerce/crawl-tasks",
        headers={"Idempotency-Key": "crawl-test-2"},
        json=payload,
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["idempotent"] is True
    assert second.json()["data"]["id"] == first.json()["data"]["id"]

    changed = dict(payload)
    changed["includeDescImages"] = False
    conflict = client.post(
        "/api/ecommerce/crawl-tasks",
        headers={"Idempotency-Key": "crawl-test-2"},
        json=changed,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_crawl_task_history_is_paginated_and_filterable(client, monkeypatch) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    urls = [
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=987654321098",
    ]
    for url in urls:
        response = client.post(
            "/api/ecommerce/crawl-tasks",
            json={
                "workspace_id": "ws_test",
                "url": url,
                "platform": "taobao",
            },
        )
        assert response.status_code == 202

    listed = client.get(
        "/api/ecommerce/crawl-tasks",
        params={"workspace_id": "ws_test", "limit": 1, "offset": 0},
    )
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert data["pagination"] == {
        "total": 2,
        "limit": 1,
        "offset": 0,
        "has_more": True,
    }
    assert len(data["items"]) == 1

    filtered = client.get(
        "/api/ecommerce/crawl-tasks",
        params={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "status": "queued",
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["pagination"]["total"] == 2

    invalid = client.get(
        "/api/ecommerce/crawl-tasks",
        params={"workspace_id": "ws_test", "status": "not-a-status"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_CRAWL_TASK_STATUS"


def test_crawl_task_cancel_and_manual_retry(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    payload = {
        "workspace_id": "ws_test",
        "url": "https://item.taobao.com/item.htm?id=123456789012",
        "platform": "taobao",
    }
    created = client.post("/api/ecommerce/crawl-tasks", json=payload)
    task_id = created.json()["data"]["id"]
    cancelled = client.post(f"/api/ecommerce/crawl-tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"

    retried = client.post(f"/api/ecommerce/crawl-tasks/{task_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["data"]["status"] == "queued"


def test_amazon_is_planned_not_enabled(client: TestClient) -> None:
    capabilities = client.get("/api/ecommerce/capabilities").json()["data"]
    assert capabilities["platforms"]["amazon"]["enabled"] is False
    assert capabilities["platforms"]["amazon"]["status"] == "planned"

    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://www.amazon.com/dp/B000000000",
            "platform": "auto",
        },
    )
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "PLATFORM_NOT_ENABLED"


def test_crawl_task_persists_product_snapshot_and_internal_asset(
    client: TestClient, monkeypatch
) -> None:
    image_buffer = BytesIO()
    Image.new("RGB", (4, 3), (20, 80, 140)).save(image_buffer, format="JPEG")
    image_payload = image_buffer.getvalue()

    async def fake_fetch(request, payload):
        return {
            "success": True,
            "data": {
                "platform": "taobao",
                "numIid": "123456789012",
                "title": "带资产的测试商品",
                "price": "39.90",
                "shopName": "测试店铺",
                "imageUrls": ["https://img.example.test/main.jpg"],
                "descImageUrls": [],
                "skuImageUrls": [],
            },
        }

    def fake_download(url, *, max_bytes, timeout_seconds):
        return ValidatedImage(
            payload=image_payload,
            filename="main.jpg",
            content_type="image/jpeg",
            width=4,
            height=3,
            object_key="__WORKSPACE__/ecommerce/test.jpg",
            sha256=__import__("hashlib").sha256(image_payload).hexdigest(),
        )

    monkeypatch.setattr(crawl_tasks, "fetch_item", fake_fetch)
    monkeypatch.setattr(crawl_tasks, "_download_remote_image", fake_download)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
        },
    )
    assert response.status_code == 202
    task = _wait_for_status(client, response.json()["data"]["id"], {"succeeded"})
    assert task["result_snapshot_id"]

    result = client.get(f"/api/ecommerce/crawl-tasks/{task['id']}/result")
    result_data = result.json()["data"]["result"]
    assert result_data["snapshotId"] == task["result_snapshot_id"]
    assert len(result_data["assetIds"]["main"]) == 1

    with client.app.state.session_factory() as session:
        snapshot = session.get(ProductSnapshotRecord, task["result_snapshot_id"])
        assert snapshot is not None
        assert snapshot.title == "带资产的测试商品"
        asset_links = (
            session.query(ProductAssetRecord).filter_by(snapshot_id=snapshot.id).all()
        )
        assert len(asset_links) == 1
        asset = session.get(AssetRecord, asset_links[0].asset_id)
        assert asset is not None
        assert asset.lifecycle_status == "ready"
        assert asset.sha256 == __import__("hashlib").sha256(image_payload).hexdigest()


def test_retryable_crawl_failure_honours_max_attempts(
    client: TestClient, monkeypatch
) -> None:
    calls = 0

    async def flaky_fetch(request, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "FETCH_FAILED",
                    "message": "upstream unavailable",
                    "retryable": True,
                },
            )
        return {"success": True, "data": {"platform": "taobao", "title": "重试成功"}}

    monkeypatch.setattr(crawl_tasks, "fetch_item", flaky_fetch)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "max_attempts": 2,
        },
    )
    assert response.status_code == 202
    task = _wait_for_status(client, response.json()["data"]["id"], {"succeeded"})
    assert task["attempt_count"] == 2
    events = client.get(f"/api/ecommerce/crawl-tasks/{task['id']}/events")
    statuses = [event["to_status"] for event in events.json()["data"]]
    assert "retry_scheduled" in statuses
    assert statuses[-1] == "succeeded"


def test_profile_lease_is_exclusive_and_released(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    payload = {
        "workspace_id": "ws_test",
        "url": "https://item.taobao.com/item.htm?id=123456789012",
        "platform": "taobao",
    }
    first = client.post("/api/ecommerce/crawl-tasks", json=payload).json()["data"]
    second = client.post("/api/ecommerce/crawl-tasks", json=payload).json()["data"]
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        first_record = session.get(CrawlTaskRecord, first["id"])
        second_record = session.get(CrawlTaskRecord, second["id"])
        assert first_record is not None
        assert second_record is not None
        first_profile_id = first_record.profile_id
        second_profile_id = second_record.profile_id

    assert first_profile_id is not None
    assert second_profile_id == first_profile_id
    assert crawl_tasks._try_acquire_profile_lease(
        session_factory,
        profile_id=first_profile_id,
        platform="taobao",
        task_id=first["id"],
        owner_id="worker-one",
    ) is True
    assert crawl_tasks._try_acquire_profile_lease(
        session_factory,
        profile_id=second_profile_id,
        platform="taobao",
        task_id=second["id"],
        owner_id="worker-two",
    ) is False

    with session_factory() as session:
        lease = session.query(CrawlProfileLeaseRecord).one()
        assert lease.task_id == first["id"]
        assert lease.owner_id == "worker-one"

    # An expired lease is reclaimable even when the previous worker did not
    # get a chance to run its release path after a process crash.
    assert crawl_tasks._try_acquire_profile_lease(
        session_factory,
        profile_id=first_profile_id,
        platform="taobao",
        task_id=first["id"],
        owner_id="worker-one",
        ttl_seconds=0,
    ) is True
    assert crawl_tasks._try_acquire_profile_lease(
        session_factory,
        profile_id=second_profile_id,
        platform="taobao",
        task_id=second["id"],
        owner_id="worker-two",
    ) is True
    crawl_tasks._release_profile_lease(
        session_factory,
        profile_id=second_profile_id,
        task_id=second["id"],
        owner_id="worker-two",
    )
    assert crawl_tasks._try_acquire_profile_lease(
        session_factory,
        profile_id=second_profile_id,
        platform="taobao",
        task_id=second["id"],
        owner_id="worker-two",
    ) is True
    crawl_tasks._release_profile_lease(
        session_factory,
        profile_id=second_profile_id,
        task_id=second["id"],
        owner_id="worker-two",
    )



def test_stale_lease_cannot_persist_product_snapshot(client, monkeypatch) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
        },
    )
    task_id = response.json()["data"]["id"]

    with pytest.raises(crawl_tasks._ProfileLeaseLost):
        asyncio.run(
            crawl_tasks._persist_product_snapshot(
                client.app,
                task_id,
                {"platform": "taobao", "title": "不得写入"},
                lease_guard=lambda: False,
            )
        )

    with client.app.state.session_factory() as session:
        assert (
            session.query(ProductSnapshotRecord).filter_by(task_id=task_id).count() == 0
        )


def test_lease_loss_during_fetch_does_not_commit_failure(client, monkeypatch) -> None:
    monkeypatch.setattr(
        crawl_tasks, "_profile_lease_is_owned", lambda *args, **kwargs: False
    )

    async def fail_fetch(request, payload):
        raise HTTPException(
            status_code=502,
            detail={"code": "FETCH_FAILED", "retryable": True},
        )

    monkeypatch.setattr(crawl_tasks, "fetch_item", fail_fetch)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
        },
    )
    task_id = response.json()["data"]["id"]

    asyncio.run(crawl_tasks._run_task(client.app, task_id))

    with client.app.state.session_factory() as session:
        task = session.get(crawl_tasks.CrawlTaskRecord, task_id)
        assert task is not None
        assert task.status == "running"
        assert task.error_code is None
        assert (
            session.query(CrawlProfileLeaseRecord).filter_by(task_id=task_id).count()
            == 0
        )



def test_celery_schedule_publishes_to_dedicated_queue(monkeypatch) -> None:
    published: list[tuple[str, int]] = []

    def fake_enqueue(task_id: str, *, countdown: int = 0) -> None:
        published.append((task_id, countdown))

    monkeypatch.setattr(ecommerce_worker, "enqueue_crawl_task", fake_enqueue)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(task_dispatcher="celery")
        )
    )

    crawl_tasks._schedule(app, "crw_worker_queue", delay_seconds=1)

    assert published == [("crw_worker_queue", 1)]


def test_celery_schedule_defers_to_online_local_worker(client, monkeypatch) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        ecommerce_worker,
        "enqueue_crawl_task",
        lambda task_id, *, countdown=0: published.append(task_id),
    )
    with client.app.state.session_factory() as session:
        session.add(
            CrawlTaskRecord(
                id="crw_local_preferred",
                workspace_id="ws_test",
                created_by="usr_test",
                platform="taobao",
                source_url="https://item.taobao.com/item.htm?id=123456789012",
                canonical_url="https://item.taobao.com/item.htm?id=123456789012",
                request_hash="b" * 64,
                status="queued",
                stage="queued",
                max_attempts=1,
                profile_id="domestic-taobao",
                request_payload={"platform": "taobao"},
                queued_at=utc_now(),
            )
        )
        session.commit()
    with client.app.state.session_factory() as session:
        session.add(
            EcommerceWorkerRecord(
                id="wrk_local_preferred",
                user_id="usr_test",
                workspace_id="ws_test",
                device_id="device-local-preferred",
                protocol_version="worker.v1",
                agent_version="local-runtime.v1",
                capabilities=["browser.taobao"],
                status="online",
                last_seen_at=utc_now(),
            )
        )
        session.commit()
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(
                task_dispatcher="celery",
                ecommerce_worker_heartbeat_timeout_seconds=90,
            ),
            session_factory=client.app.state.session_factory,
        )
    )

    crawl_tasks._schedule(app, "crw_local_preferred")

    assert published == []


def test_recovery_leaves_active_worker_lease_untouched(client, monkeypatch) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "max_attempts": 2,
        },
    )
    task_id = response.json()["data"]["id"]
    with client.app.state.session_factory() as session:
        task = session.get(crawl_tasks.CrawlTaskRecord, task_id)
        assert task is not None
        task.status = "running"
        task.attempt_count = 1
        session.commit()

    assert crawl_tasks._try_acquire_profile_lease(
        client.app.state.session_factory,
        profile_id="domestic-taobao-tmall",
        platform="taobao",
        task_id=task_id,
        owner_id="live-crawl-worker",
    ) is True

    assert crawl_tasks.recover_interrupted_crawl_tasks(
        client.app.state.session_factory
    ) == []
    with client.app.state.session_factory() as session:
        task = session.get(crawl_tasks.CrawlTaskRecord, task_id)
        assert task is not None
        assert task.status == "running"


def test_recovery_requeues_expired_worker_task(client, monkeypatch) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "max_attempts": 2,
        },
    )
    task_id = response.json()["data"]["id"]
    with client.app.state.session_factory() as session:
        task = session.get(crawl_tasks.CrawlTaskRecord, task_id)
        assert task is not None
        task.status = "running"
        task.attempt_count = 1
        session.commit()

    assert crawl_tasks._try_acquire_profile_lease(
        client.app.state.session_factory,
        profile_id="domestic-taobao-tmall",
        platform="taobao",
        task_id=task_id,
        owner_id="crashed-crawl-worker",
        ttl_seconds=0,
    ) is True

    assert crawl_tasks.recover_interrupted_crawl_tasks(
        client.app.state.session_factory
    ) == [task_id]
    with client.app.state.session_factory() as session:
        task = session.get(crawl_tasks.CrawlTaskRecord, task_id)
        assert task is not None
        assert task.status == "queued"
        assert task.error_code is None
        events = (
            session.query(crawl_tasks.CrawlEventRecord)
            .filter_by(task_id=task_id)
            .all()
        )
        assert any(event.event_type == "recovery_scheduled" for event in events)


def test_orphaned_crawl_asset_cleanup_removes_unreferenced_object(
    client, monkeypatch
) -> None:
    deleted: list[tuple[str, str]] = []

    class TrackingStorage:
        async def delete_object(
            self, object_key: str, object_version: str = ""
        ) -> None:
            deleted.append((object_key, object_version))

    monkeypatch.setattr(client.app.state, "storage", TrackingStorage())
    with client.app.state.session_factory() as session:
        asset = AssetRecord(
            id="ast_orphan_cleanup",
            workspace_id="ws_test",
            media_kind="image",
            origin_kind="imported",
            lifecycle_status="ready",
            original_filename="orphan.jpg",
            content_type="image/jpeg",
            byte_size=4,
            width=2,
            height=2,
            sha256="a" * 64,
            storage_provider="local",
            storage_bucket="local",
            object_key="ws_test/ecommerce/orphan.jpg",
            object_version="",
            asset_metadata={"crawl_task_id": "crw_missing"},
            created_at=utc_now() - timedelta(hours=2),
        )
        session.add(asset)
        session.commit()

    cleaned = asyncio.run(
        crawl_tasks.cleanup_orphaned_crawl_assets(
            client.app, grace_seconds=3600, limit=10
        )
    )

    assert cleaned == 1
    assert deleted == [("ws_test/ecommerce/orphan.jpg", "")]
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, "ast_orphan_cleanup")
        assert asset is not None
        assert asset.lifecycle_status == "deleted"
        assert asset.deleted_at is not None


def test_orphaned_crawl_asset_cleanup_records_retry_after_storage_failure(
    client, monkeypatch
) -> None:
    class FailingStorage:
        async def delete_object(
            self, object_key: str, object_version: str = ""
        ) -> None:
            raise OSError(f"storage unavailable: {object_key}:{object_version}")

    monkeypatch.setattr(client.app.state, "storage", FailingStorage())
    with client.app.state.session_factory() as session:
        session.add(
            AssetRecord(
                id="ast_orphan_retry",
                workspace_id="ws_test",
                media_kind="image",
                origin_kind="imported",
                lifecycle_status="ready",
                original_filename="orphan-retry.jpg",
                content_type="image/jpeg",
                byte_size=4,
                width=2,
                height=2,
                sha256="b" * 64,
                storage_provider="local",
                storage_bucket="local",
                object_key="ws_test/ecommerce/orphan-retry.jpg",
                object_version="",
                asset_metadata={"crawl_task_id": "crw_retry"},
                created_at=utc_now() - timedelta(hours=2),
            )
        )
        session.commit()

    cleaned = asyncio.run(
        crawl_tasks.cleanup_orphaned_crawl_assets(
            client.app, grace_seconds=3600, limit=10
        )
    )

    assert cleaned == 0
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, "ast_orphan_retry")
        assert asset is not None
        assert asset.lifecycle_status == "ready"
        assert asset.purge_attempts == 1
        assert asset.purge_next_retry_at is not None
        assert asset.purge_next_retry_at > utc_now().replace(tzinfo=None)
        assert asset.purge_last_error == "OSError"


def test_orphaned_crawl_asset_cleanup_skips_snapshot_references(
    client, monkeypatch
) -> None:
    deleted: list[str] = []

    class TrackingStorage:
        async def delete_object(
            self, object_key: str, object_version: str = ""
        ) -> None:
            deleted.append(object_key)

    monkeypatch.setattr(client.app.state, "storage", TrackingStorage())
    with client.app.state.session_factory() as session:
        snapshot = ProductSnapshotRecord(
            id="snap_orphan_reference",
            workspace_id="ws_test",
            task_id="crw_snapshot_reference",
            platform="taobao",
            canonical_url="https://item.taobao.com/item.htm?id=987654321012",
            payload={},
            warnings=[],
        )
        session.add(snapshot)
        session.flush()
        asset = AssetRecord(
            id="ast_snapshot_reference",
            workspace_id="ws_test",
            media_kind="image",
            origin_kind="imported",
            lifecycle_status="ready",
            original_filename="referenced.jpg",
            content_type="image/jpeg",
            byte_size=4,
            width=2,
            height=2,
            sha256="c" * 64,
            storage_provider="local",
            storage_bucket="local",
            object_key="ws_test/ecommerce/referenced.jpg",
            object_version="",
            asset_metadata={"crawl_task_id": "crw_snapshot_reference"},
            created_at=utc_now() - timedelta(hours=2),
        )
        session.add(asset)
        session.flush()
        session.add(
            ProductAssetRecord(
                id="pas_snapshot_reference",
                snapshot_id=snapshot.id,
                asset_id=asset.id,
                role="main",
                ordinal=0,
                source_url="https://img.example.test/referenced.jpg",
                source="crawler",
            )
        )
        session.commit()

    cleaned = asyncio.run(
        crawl_tasks.cleanup_orphaned_crawl_assets(
            client.app, grace_seconds=3600, limit=10
        )
    )

    assert cleaned == 0
    assert deleted == []
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, "ast_snapshot_reference")
        assert asset is not None
        assert asset.lifecycle_status == "ready"
        assert asset.deleted_at is None



def test_celery_api_startup_leaves_queue_recovery_to_maintenance(
    settings, monkeypatch
) -> None:
    settings.task_dispatcher = "celery"
    recovered_calls: list[object] = []
    resumed_calls: list[object] = []

    monkeypatch.setattr(
        "app.main.recover_interrupted_crawl_tasks",
        lambda session_factory: recovered_calls.append(session_factory) or [],
    )
    monkeypatch.setattr(
        "app.main.resume_queued_crawl_tasks",
        lambda application, session_factory: resumed_calls.append(application),
    )

    with TestClient(create_app(settings)):
        pass

    assert len(recovered_calls) == 1
    assert resumed_calls == []

