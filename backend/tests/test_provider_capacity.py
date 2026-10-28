import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from aiohttp import web

from app.async_provider_transport import (
    AsyncProviderTransport,
    close_async_provider_session,
)
from app.config import Settings
from app.provider_capacity import image_capacity
from app.worker_async_runtime import WorkerAsyncRuntime
from app.xinghe_agent import XingheAgentError


def test_upstream_capacity_settings() -> None:
    settings = Settings(_env_file=None)
    assert settings.xinghe_concurrency == 3000
    assert settings.replica_analysis_concurrency == 0
    for value in (0, 3001, True):
        with pytest.raises(ValueError):
            Settings(_env_file=None, xinghe_concurrency=value)


def test_thread_deliveries_share_one_persistent_loop() -> None:
    runtime = WorkerAsyncRuntime()

    async def identify() -> int:
        await asyncio.sleep(0.005)
        return id(asyncio.get_running_loop())

    try:
        with ThreadPoolExecutor(max_workers=32) as executor:
            loops = list(executor.map(lambda _: runtime.run(identify), range(150)))
        assert len(set(loops)) == 1
        assert runtime.run(identify) == loops[0]
    finally:
        runtime.close()


def test_unknown_outcomes_and_cancellation_keep_capacity() -> None:
    class Limiter:
        def __init__(self) -> None:
            self.active: set[str] = set()

        async def acquire(self) -> str:
            token = str(len(self.active))
            self.active.add(token)
            return token

        async def release(self, token: str) -> None:
            self.active.remove(token)

    class Gateway:
        def __init__(self) -> None:
            self._image_capacity = Limiter()

        @image_capacity
        async def call(self, outcome: str) -> SimpleNamespace:
            if outcome == "cancel":
                raise asyncio.CancelledError
            return SimpleNamespace(outcome=outcome)

    async def run() -> None:
        gateway = Gateway()
        await gateway.call("succeeded")
        assert not gateway._image_capacity.active
        await gateway.call("outcome_unknown")
        with pytest.raises(asyncio.CancelledError):
            await gateway.call("cancel")
        assert len(gateway._image_capacity.active) == 2

    asyncio.run(run())


def test_async_transport_parallel_chat_and_one_dispatch() -> None:
    async def run() -> None:
        active = peak = received = 0
        barrier = asyncio.Event()

        async def handler(request: web.Request) -> web.Response:
            nonlocal active, peak, received
            received += 1
            if request.path == "/redirect":
                raise web.HTTPTemporaryRedirect("/chat")
            if request.path == "/error":
                return web.Response(status=503, text="temporarily unavailable")
            active += 1
            peak = max(peak, active)
            if active == 100:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 10)
            active -= 1
            return web.json_response({"ok": True}, headers={"x-request-id": "test-1"})

        app = web.Application()
        app.router.add_post("/{path}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        transport = AsyncProviderTransport()

        async def send(path: str) -> tuple[int, dict]:
            return await transport.post_json(
                url=f"http://127.0.0.1:{port}/{path}",
                headers={},
                payload={},
                timeout_seconds=15,
            )

        try:
            results = await asyncio.gather(*(send("chat") for _ in range(100)))
            assert peak == 100
            assert all(payload["request_id"] == "test-1" for _, payload in results)
            with pytest.raises(XingheAgentError, match="redirect"):
                await send("redirect")
            assert received == 101  # Redirect was never followed.
            status, payload = await send("error")
            assert status == 503
            assert payload["error"]["code"] == "non_json_error_response"
            assert received == 102  # No automatic retry of a failed POST.
        finally:
            await close_async_provider_session()
            await runner.cleanup()

    asyncio.run(run())
