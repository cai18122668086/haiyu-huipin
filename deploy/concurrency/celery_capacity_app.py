"""Isolated, synthetic tasks exercising the production Celery -> asyncio bridge."""

import asyncio
from types import SimpleNamespace

from aiohttp import web
from app.async_provider_transport import AsyncProviderTransport
from app.celery_app import celery_app
from app.config import Settings
from app.domain import JobStatus
from app.provider_capacity import ImageCapacity, image_capacity

from app import tasks

settings = Settings()
active = peak = completed = 0
barrier = None
endpoint = None


class SyntheticGateway:
    def __init__(self):
        self._image_capacity = ImageCapacity(
            settings.image_capacity_redis_url,
            "capacity-test:images",
            3000,
            7200,
        )

    @image_capacity
    async def call(self):
        global endpoint, barrier
        if endpoint is None:
            barrier = asyncio.Event()

            async def receive(request):
                global active, peak, completed
                await request.read()
                active += 1
                peak = max(peak, active)
                if active == 3000:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), 180)
                await asyncio.sleep(1)
                active -= 1
                completed += 1
                return web.json_response({"done": True})

            app = web.Application()
            app.router.add_post("/synthetic", receive)
            runner = web.AppRunner(app)
            # Bind before yielding so only the first coroutine creates it.
            endpoint = asyncio.get_running_loop().create_future()
            startup = endpoint
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 19099, backlog=4096)
            await site.start()
            startup.set_result("http://127.0.0.1:19099/synthetic")
        url = await endpoint
        status, payload = await AsyncProviderTransport().post_json(
            url=url,
            headers={},
            payload={"synthetic": True},
            timeout_seconds=240,
        )
        assert status == 200 and payload["done"]
        return SimpleNamespace(outcome="succeeded")


gateway = SyntheticGateway()


class SyntheticRunner:
    async def run(self, job_id):
        await gateway.call()
        return JobStatus.COMPLETED

    async def cancel(self, job_id):
        return JobStatus.CANCELLED


tasks._process_runner = lambda: SyntheticRunner()


@celery_app.task(name="polaris.capacity_stats")
def stats():
    return {"peak": peak, "completed": completed, "active": active}
