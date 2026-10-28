"""Regressions for image bursts starving auth and the ASGI event loop."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import sessionmaker

from app import auth, main
from tests.test_auth_external import FakeExternalAuthBackend, verified_identity

# The burst keeps every storage call blocked, but stages arrivals so the test
# does not fail merely because 48 tasks race for a two-connection DB pool.
BURST_START_TIMEOUT_SECONDS = 3


def test_database_capacity_error_is_retryable_and_safe(settings):
    app = main.create_app(settings)

    @app.get("/pool-test")
    def busy():
        raise DatabasePoolTimeout("internal driver detail must stay private")

    with TestClient(app) as client:
        response = client.get("/pool-test")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert response.json()["error"]["code"] == "DATABASE_BUSY"
    assert "internal driver" not in response.text


@pytest.mark.parametrize("request_count", [8, 48])
@pytest.mark.parametrize("phase", ["content", "presign"])
def test_slow_image_burst_releases_database_before_storage(
    settings, monkeypatch, request_count, phase
):
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.25,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "build_database", lambda _url: (engine, factory))
    app = main.create_app(settings)
    output = BytesIO()
    Image.new("RGB", (16, 16), "red").save(output, format="PNG")
    image = output.getvalue()
    with TestClient(app) as setup:
        asset = setup.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": "image"},
            files={"file": ("test.png", image, "image/png")},
        )
        assert asset.status_code == 201
        access_path = (
            f"/api/v1/workspaces/ws_test/assets/{asset.json()['id']}/access-url"
        )
        access = setup.post(
            access_path,
            json={"disposition": "inline"},
        )
        assert access.status_code == 200
        url = access.json()["url"]

        async def scenario():
            release = asyncio.Event()
            reading_started: asyncio.Queue[None] = asyncio.Queue()
            reading = 0

            async def slow_read(_key):
                nonlocal reading
                reading += 1
                reading_started.put_nowait(None)
                await release.wait()
                return image

            monkeypatch.setattr(app.state.storage, "read_bytes", slow_read)

            async def slow_presign(*_args):
                await slow_read("unused")
                return "https://storage.example.test/test.png"

            monkeypatch.setattr(app.state.storage, "create_access_url", slow_presign)
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport) as client:
                tasks = []
                try:
                    for _ in range(request_count):
                        tasks.append(
                            asyncio.create_task(
                                client.get(url)
                                if phase == "content"
                                else client.post(
                                    f"http://testserver{access_path}",
                                    json={"disposition": "inline"},
                                )
                            )
                        )
                        await asyncio.wait_for(
                            reading_started.get(), BURST_START_TIMEOUT_SECONDS
                        )
                except TimeoutError:
                    pytest.fail(
                        "A burst request did not reach storage before the "
                        "watchdog expired"
                    )
                try:
                    # Every download is still waiting; none may retain a DB slot.
                    assert engine.pool.checkedout() == 0
                    ready, identity = await asyncio.wait_for(
                        asyncio.gather(
                            client.get("http://testserver/health/ready"),
                            client.get("http://testserver/api/v1/me"),
                        ),
                        1,
                    )
                    assert ready.status_code == identity.status_code == 200
                finally:
                    release.set()
                    responses = await asyncio.gather(*tasks)
                assert all(response.status_code == 200 for response in responses)
                if phase == "content":
                    assert all(response.content == image for response in responses)
                else:
                    assert all(
                        response.json()["url"]
                        == "https://storage.example.test/test.png"
                        for response in responses
                    )

        asyncio.run(scenario())


def test_slow_identity_database_work_does_not_block_health(settings, monkeypatch):
    settings.auth_mode = "external"
    backend = FakeExternalAuthBackend(identity=verified_identity())
    entered, release = threading.Event(), threading.Event()
    original = auth._ensure_external_user

    def delayed(session, identity):
        entered.set()
        assert release.wait(3), "watchdog: identity DB work was not released"
        return original(session, identity)

    monkeypatch.setattr(auth, "_ensure_external_user", delayed)
    with TestClient(main.create_app(settings, backend)) as setup:

        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=setup.app, raise_app_exceptions=False
                ),
                base_url="http://testserver",
            ) as client:
                identity_task = asyncio.create_task(client.get("/api/v1/me"))
                try:
                    assert await asyncio.to_thread(entered.wait, 1)
                    response = await asyncio.wait_for(client.get("/health/live"), 1)
                    assert response.status_code == 200
                    assert not release.is_set()
                finally:
                    release.set()
                    identity_response = await identity_task
                assert identity_response.status_code == 200

        # A watchdog also releases the blocking stub if the old implementation
        # stalls the event loop, making a regression fail instead of hanging CI.
        with ThreadPoolExecutor(max_workers=1) as executor:
            watchdog = executor.submit(lambda: release.wait(2) or release.set())
            asyncio.run(scenario())
            watchdog.result()
