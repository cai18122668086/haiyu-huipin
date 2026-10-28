"""Storage waits must not consume DB capacity or block unrelated requests."""

import asyncio
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import ClientDisconnect

from app import ecommerce_crawl_tasks, main
from app.archive_response import ARCHIVE_MEMORY_LIMIT, ArchiveResponse, open_archive
from app.domain import JobStatus, JobType
from app.models import (
    AnnouncementRecord,
    CrawlProfileLeaseRecord,
    ProductSnapshotRecord,
    UserRecord,
)
from app.storage import ValidatedImage
from tests.test_api import (
    _attach_retry_fixture_deliverables,
    _seed_asset_classification_job,
    _seed_partial_retry_source,
    image_payload,
    upload_asset,
)
from tests.test_ecommerce_workers import _queued_task, _register


@pytest.fixture
def bounded_client(settings, monkeypatch):
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.25,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "build_database", lambda _url: (engine, factory))
    with TestClient(main.create_app(settings)) as client:
        yield client


def _public_image(client):
    asset = upload_asset(client)
    with client.app.state.session_factory() as session:
        session.add(
            AnnouncementRecord(
                id="ann_pool",
                title="Pool test",
                content="Synthetic announcement",
                image_asset_id=asset["id"],
                status="published",
                published_at=datetime.now(UTC),
                created_by_user_id="usr_test",
                updated_by_user_id="usr_test",
            )
        )
        session.commit()
    return "/api/v1/announcements/ann_pool/image"


def _archive_url(client):
    asset = upload_asset(client)
    job_id, _ = _seed_partial_retry_source(
        client,
        job_type=JobType.AMAZON_VISUAL_SUITE,
        failed_error_details={
            "failure_kind": "review_infrastructure_failure",
            "image_quality_failed": False,
            "generated_asset_preserved": True,
            "retryable": False,
        },
    )
    _attach_retry_fixture_deliverables(client, job_id=job_id, asset_id=asset["id"])
    return f"/api/v1/workspaces/ws_test/jobs/{job_id}/deliverables.zip"


@pytest.mark.parametrize("kind", ["announcement", "archive"])
@pytest.mark.parametrize("count", [8, 48])
def test_public_storage_bursts_keep_authentication_responsive(
    bounded_client, monkeypatch, kind, count
):
    app = bounded_client.app
    url = (
        _public_image(bounded_client)
        if kind == "announcement"
        else _archive_url(bounded_client)
    )

    async def scenario():
        entered = asyncio.Queue()
        release = asyncio.Event()
        read = app.state.storage.read_bytes

        async def slow_read(key):
            entered.put_nowait(None)
            await release.wait()
            return await read(key)

        monkeypatch.setattr(app.state.storage, "read_bytes", slow_read)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            tasks = []
            try:
                for _ in range(count):
                    tasks.append(asyncio.create_task(client.get(url)))
                    await asyncio.wait_for(entered.get(), 3)
                    assert app.state.engine.pool.checkedout() == 0
                ready, identity = await asyncio.wait_for(
                    asyncio.gather(
                        client.get("/health/ready"), client.get("/api/v1/me")
                    ),
                    1,
                )
                assert ready.status_code == identity.status_code == 200
                assert not any(task.done() for task in tasks)
            finally:
                release.set()
                responses = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(
                isinstance(r, httpx.Response) and r.status_code == 200
                for r in responses
            )
            if kind == "archive":
                for response in responses:
                    with ZipFile(BytesIO(response.content)) as archive:
                        assert len(archive.namelist()) == 21

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,table", [("announcement", "announcements"), ("archive", "jobs")]
)
def test_slow_public_database_lookup_does_not_block_event_loop(
    bounded_client, kind, table
):
    app = bounded_client.app
    url = (
        _public_image(bounded_client)
        if kind == "announcement"
        else _archive_url(bounded_client)
    )
    entered, release = threading.Event(), threading.Event()

    def slow_query(_conn, _cursor, statement, *_args):
        if f"FROM {table}" in statement:
            entered.set()
            assert release.wait(3)

    event.listen(app.state.engine, "before_cursor_execute", slow_query)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            task = asyncio.create_task(client.get(url))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                ready = await asyncio.wait_for(client.get("/health/live"), 1)
                assert ready.status_code == 200
                assert not release.is_set()
            finally:
                release.set()
                response = await task
            assert response.status_code == 200

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            watchdog = executor.submit(lambda: release.wait(2) or release.set())
            asyncio.run(scenario())
            watchdog.result()
    finally:
        release.set()
        event.remove(app.state.engine, "before_cursor_execute", slow_query)


def test_admin_asset_signing_releases_database(bounded_client, monkeypatch):
    app = bounded_client.app
    app.state.settings.announcement_admin_emails = ["admin@example.com"]
    app.state.settings.asset_access_url_mode = "auto"
    with app.state.session_factory() as session:
        session.get(UserRecord, "usr_test").email_normalized = "admin@example.com"
        session.commit()
    asset = upload_asset(bounded_client)
    job_id = _seed_asset_classification_job(
        bounded_client,
        status=JobStatus.COMPLETED,
        input_asset_id=asset["id"],
        output_asset_ids=[asset["id"]],
    )

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_sign(*_args):
            entered.set()
            await release.wait()
            return "https://storage.example.test/synthetic"

        monkeypatch.setattr(app.state.storage, "create_access_url", slow_sign)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            task = asyncio.create_task(
                client.get(f"/api/v1/admin/generation-jobs/{job_id}")
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                assert app.state.engine.pool.checkedout() == 0
                ready = await asyncio.wait_for(client.get("/health/ready"), 1)
                assert ready.status_code == 200
            finally:
                release.set()
                response = await task
            assert response.status_code == 200
            assert all(a["access_url"] for a in response.json()["data"]["assets"])

    asyncio.run(scenario())


@pytest.mark.parametrize("lose_lease", [False, True])
def test_worker_result_waits_without_connections_and_preserves_fencing(
    bounded_client, settings, monkeypatch, lose_lease
):
    app = bounded_client.app
    worker = _register(bounded_client)
    task_id = "crw_pool_result"
    _queued_task(bounded_client, task_id)
    headers = {"Authorization": f"Bearer {worker['session_token']}"}
    claimed = bounded_client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/claim",
        headers=headers,
        json={},
    )
    assert claimed.status_code == 200
    token = claimed.json()["data"]["lease"]["fencing_token"]
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    payload = image_payload()
    image = ValidatedImage(
        payload=payload,
        filename="crawler.png",
        content_type="image/png",
        width=96,
        height=96,
        object_key="__WORKSPACE__/crawler-pool.png",
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    monkeypatch.setattr(
        ecommerce_crawl_tasks, "_download_remote_image", lambda *_a, **_k: image
    )
    url = f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/result"
    body = {
        "fencing_token": token,
        "result": {
            "platform": "taobao",
            "title": "Synthetic product",
            "imageUrls": ["https://example.test/product.png"],
        },
    }

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        save = app.state.storage.save_validated

        async def slow_save(prepared):
            entered.set()
            await release.wait()
            return await save(prepared)

        monkeypatch.setattr(app.state.storage, "save_validated", slow_save)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            task = asyncio.create_task(client.post(url, headers=headers, json=body))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                assert engine.pool.checkedout() == 0
                ready = await asyncio.wait_for(client.get("/health/ready"), 1)
                assert ready.status_code == 200
                if lose_lease:
                    with app.state.session_factory() as session:
                        lease = session.scalar(
                            select(CrawlProfileLeaseRecord).where(
                                CrawlProfileLeaseRecord.task_id == task_id
                            )
                        )
                        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                        session.commit()
            finally:
                release.set()
                response = await task
            if lose_lease:
                assert response.status_code == 409
                assert response.json()["error"]["code"] == "TASK_LEASE_LOST"
                with app.state.session_factory() as session:
                    assert (
                        session.scalar(select(func.count(ProductSnapshotRecord.id)))
                        == 0
                    )
            else:
                assert response.status_code == 200, response.text
                replay = await client.post(url, headers=headers, json=body)
                assert replay.status_code == 200
                assert replay.json()["data"]["idempotent"] is True

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_archive_spools_to_disk_and_closes_after_download(tmp_path):
    archive = open_archive(tmp_path)
    with ZipFile(archive, "w", compression=ZIP_STORED) as bundle:
        bundle.writestr("large.bin", b"x" * (ARCHIVE_MEMORY_LIMIT + 1024))
    assert archive._rolled
    app = FastAPI()

    @app.get("/archive")
    def download():
        return ArchiveResponse(archive, "test.zip")

    with TestClient(app) as client:
        response = client.get("/archive")
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    with ZipFile(BytesIO(response.content)) as result:
        assert len(result.read("large.bin")) == ARCHIVE_MEMORY_LIMIT + 1024
    assert archive.closed


def test_archive_closes_when_client_disconnects(tmp_path):
    archive = open_archive(tmp_path)
    archive.write(b"synthetic archive")
    response = ArchiveResponse(archive, "test.zip")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("synthetic disconnect")

    with pytest.raises(ClientDisconnect):
        asyncio.run(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        )
    assert archive.closed


def test_archive_integrity_failure_closes_staged_file(bounded_client, monkeypatch):
    from app import routes_v1

    url = _archive_url(bounded_client)
    buffers = []

    def capture(directory):
        buffer = open_archive(directory)
        buffers.append(buffer)
        return buffer

    async def corrupt(_key):
        return b"corrupted original"

    monkeypatch.setattr(routes_v1, "open_archive", capture)
    monkeypatch.setattr(bounded_client.app.state.storage, "read_bytes", corrupt)
    response = bounded_client.get(url)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DELIVERABLE_INTEGRITY_FAILED"
    assert buffers and all(buffer.closed for buffer in buffers)


@pytest.mark.parametrize("corrupt", [False, True])
def test_native_archive_reads_detached_records_and_preserves_screen_hash(
    bounded_client, monkeypatch, tmp_path, corrupt
):
    """Exercise archive I/O with a preverified contract, independent of generation."""
    import json
    from types import SimpleNamespace

    from app.domestic_detail import review_packet
    from app.domestic_detail.delivery import DetailDeliveryError

    app = bounded_client.app
    source = upload_asset(bounded_client)
    job_id = _seed_asset_classification_job(
        bounded_client,
        status=JobStatus.COMPLETED,
        input_asset_id=source["id"],
        output_asset_ids=[source["id"]],
    )
    job = SimpleNamespace(id=job_id, status="completed", workspace_id="ws_test")
    plan = SimpleNamespace(id="plan_packet", revision=1, payload={})
    context = {
        "generation_binding_sha256": "a" * 64,
        "screens": [
            {"module_id": "M01", "asset_id": source["id"], "sha256": source["sha256"]}
        ],
        "pair_canvases": [],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version":"synthetic"}', encoding="utf-8")
    state = {
        "modules": [{"id": "M01", "status": "accepted", "residual_issues": []}],
        "final": {"manifest": str(manifest)},
    }
    original_read = app.state.storage.read_bytes
    read_count = 0

    async def observe_read(key):
        nonlocal read_count
        read_count += 1
        assert app.state.engine.pool.checkedout() == 0
        return await original_read(key)

    monkeypatch.setattr(app.state.storage, "read_bytes", observe_read)
    with app.state.session_factory() as db:

        async def prepare_packet():
            return await review_packet._packet(
                db,
                job,
                plan,
                app.state.storage,
                context,
                state,
                register=False,
                read_only=True,
            )

        packet = asyncio.run(prepare_packet())
        plan.payload = {
            "final_manifest": {
                "final_review": {"sha256": packet["sha256"]},
                "native_manifest_sha256": "b" * 64,
                "version": "synthetic",
            }
        }
        assert app.state.engine.pool.checkedout() == 0
        assert read_count == 1

        async def verified_context(*_args, read_only=False):
            assert read_only
            return context, state, tmp_path

        async def verified_packet(*_args, read_only=False):
            assert read_only
            return packet

        monkeypatch.setattr(review_packet, "_verified_context", verified_context)
        monkeypatch.setattr(review_packet, "validate_final_review", verified_packet)
        monkeypatch.setattr(
            review_packet,
            "_inspect",
            lambda *_a: {
                "manifest_file_sha256": "b" * 64,
                "manifest": {"version": "synthetic"},
            },
        )
        buffers = []

        def capture(directory):
            buffer = open_archive(directory)
            buffers.append(buffer)
            return buffer

        monkeypatch.setattr(review_packet, "open_archive", capture)
        if corrupt:

            async def damaged(_storage, _key, _version=""):
                assert app.state.engine.pool.checkedout() == 0
                return b"changed pixels", None

            monkeypatch.setattr(review_packet, "_read", damaged)
            with pytest.raises(DetailDeliveryError, match="screen changed"):
                asyncio.run(
                    review_packet.delivery_archive(db, job, plan, app.state.storage)
                )
            assert buffers and all(buffer.closed for buffer in buffers)
        else:
            output = asyncio.run(
                review_packet.delivery_archive(db, job, plan, app.state.storage)
            )
            try:
                output.seek(0)
                with ZipFile(output) as archive:
                    payload = archive.read("screens/M01.png")
                    assert hashlib.sha256(payload).hexdigest() == source["sha256"]
                    assert (
                        json.loads(archive.read("final-review.json"))["sha256"]
                        == packet["sha256"]
                    )
            finally:
                output.close()
            assert read_count > 1
        assert app.state.engine.pool.checkedout() == 0


def test_archive_uses_writable_work_mount_with_readonly_parent(
    bounded_client, monkeypatch
):
    from errno import EROFS
    from pathlib import Path

    url = _archive_url(bounded_client)
    writable = bounded_client.app.state.settings.aurora_work_root.resolve()
    writable.mkdir(parents=True, exist_ok=True)
    mkdir = Path.mkdir

    def mounted_mkdir(directory, *args, **kwargs):
        if not directory.resolve().is_relative_to(writable):
            raise OSError(EROFS, "Synthetic read-only container filesystem")
        return mkdir(directory, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mounted_mkdir)
    response = bounded_client.get(url)
    assert response.status_code == 200, response.text
    assert (writable / "download-cache").is_dir()
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert archive.namelist()
