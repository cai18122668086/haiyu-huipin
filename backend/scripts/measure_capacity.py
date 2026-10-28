"""Isolated HTTP/Redis/PostgreSQL capacity probe with a no-network image gateway.

Requires a dedicated localhost database named polaris_load and localhost Redis.
Creates a fresh schema and queue for every case. It never reads production
credentials, rewrites a workspace quota outside its own schema, or calls a paid
provider. The Redis consumer harness exercises the real durable coordinator,
not production Celery topology or real supplier throughput.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import redis
import redis.asyncio as async_redis
from PIL import Image
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from app.models import JobRecord, UsageEventRecord, WorkspaceLimitRecord
from app.outbox import publish_pending_events
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 2)


async def measure(args, slots):
    schema = "capacity_" + uuid4().hex
    queue = schema + ":jobs"
    base = make_url(args.database_url)
    engine = create_engine(base)
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA "' + schema + '"'))
    scoped = base.update_query_dict({"options": "-csearch_path=" + schema})
    root = (args.output.parent / schema).resolve()
    root.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="development",
        development_user_id="usr_capacity",
        development_workspace_id="ws_capacity",
        development_workspace_name="Isolated capacity probe",
        access_url_secret="isolated-capacity-only",
        database_url=scoped.render_as_string(hide_password=False),
        auto_create_schema=True,
        auto_process_jobs=False,
        task_dispatcher="inline",
        storage_backend="local",
        storage_dir=root / "storage",
        aurora_work_root=root / "aurora",
        ecommerce_session_dir=root / "profiles",
        ecommerce_startup_recovery_enabled=False,
        workspace_credit_enforcement_enabled=False,
        xinghe_concurrency=args.page_concurrency,
    )
    app = create_app(settings)

    class CapacityGateway(FakeXingheGateway):
        async def edit(self, request):
            result = await super().edit(request)
            return replace(result, provider_request_id="capacity_" + request.attempt_id)

    gateway = CapacityGateway(delay_seconds=args.provider_delay)
    broker = redis.Redis.from_url(args.redis_url)
    consumer = async_redis.Redis.from_url(args.redis_url)
    samples = []
    statuses = {}
    try:
        async with app.router.lifespan_context(app):
            runner = MockTaskRunner(
                app.state.session_factory,
                0,
                storage=app.state.storage,
                xinghe_gateway=gateway,
                xinghe_concurrency=args.page_concurrency,
                product_image_provider_enabled=True,
            )
            app.state.task_runner = runner
            with app.state.session_factory() as db:
                limits = db.get(WorkspaceLimitRecord, "ws_capacity")
                limits.max_active_jobs = args.jobs + 20
                db.commit()
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://isolated.test"
            ) as client:
                raw = BytesIO()
                Image.new("RGB", (512, 512), "steelblue").save(raw, format="PNG")
                upload = await client.post(
                    "/api/v1/workspaces/ws_capacity/assets",
                    headers={"Idempotency-Key": str(uuid4())},
                    data={"origin_kind": "upload", "media_kind": "image"},
                    files={"file": ("capacity.png", raw.getvalue(), "image/png")},
                )
                upload.raise_for_status()
                asset_id = upload.json()["id"]
                semaphore = asyncio.Semaphore(args.clients)

                async def read_one(index):
                    async with semaphore:
                        start = time.perf_counter()
                        path = (
                            "/api/v1/workspaces/ws_capacity/assets"
                            if index % 2
                            else "/api/v1/workspaces/ws_capacity/jobs"
                        )
                        response = await client.get(path)
                        samples.append((time.perf_counter() - start) * 1000)
                        statuses[response.status_code] = (
                            statuses.get(response.status_code, 0) + 1
                        )

                read_start = time.perf_counter()
                await asyncio.gather(*(read_one(n) for n in range(args.reads)))
                read_duration = time.perf_counter() - read_start
                payload = {
                    "type": "product_image",
                    "intent": "submit",
                    "input": {"product_asset_ids": [asset_id]},
                    "configuration": {
                        "task_type": "white_background",
                        "platform_code": "tmall",
                        "language": "zh-CN",
                        "aspect_ratio": "1:1",
                        "image_count": 1,
                        "instruction": "Preserve the product identity.",
                    },
                }
                submissions = []

                async def submit_one(index):
                    async with semaphore:
                        key = str(uuid4())
                        start = time.perf_counter()
                        response = await client.post(
                            "/api/v1/workspaces/ws_capacity/jobs",
                            headers={"Idempotency-Key": key},
                            json=payload,
                        )
                        if response.status_code != 202:
                            raise RuntimeError(
                                "Isolated submission failed: " + response.text[:300]
                            )
                        submissions.append(
                            (
                                response.json()["id"],
                                key,
                                (time.perf_counter() - start) * 1000,
                            )
                        )

                await asyncio.gather(*(submit_one(i) for i in range(args.jobs)))
                # Replaying accepted HTTP submissions must not create another job.
                for job_id, key, _ in submissions[:4]:
                    replay = await client.post(
                        "/api/v1/workspaces/ws_capacity/jobs",
                        headers={"Idempotency-Key": key},
                        json=payload,
                    )
                    assert replay.json()["id"] == job_id

                class QueuePublisher:
                    async def run(self, job_id):
                        broker.rpush(
                            queue,
                            json.dumps({"job_id": job_id, "at": time.perf_counter()}),
                        )

                publisher = QueuePublisher()
                published = await asyncio.to_thread(
                    publish_pending_events,
                    settings,
                    app.state.session_factory,
                    publisher,
                    limit=args.jobs + 10,
                )
                assert published == args.jobs, published
                for job_id, _, _ in submissions[:4]:
                    broker.rpush(
                        queue, json.dumps({"job_id": job_id, "at": time.perf_counter()})
                    )
                for _ in range(slots):
                    broker.rpush(queue, "STOP")
                waits, executions = [], []

                async def worker():
                    while True:
                        item = await consumer.blpop(queue, timeout=5)
                        if item is None:
                            raise RuntimeError("Isolated consumer stalled")
                        if item[1] == b"STOP":
                            return
                        message = json.loads(item[1])
                        waits.append((time.perf_counter() - message["at"]) * 1000)
                        started = time.perf_counter()
                        await runner.run(message["job_id"])
                        executions.append((time.perf_counter() - started) * 1000)

                started = time.perf_counter()
                await asyncio.gather(*(worker() for _ in range(slots)))
                generation_seconds = time.perf_counter() - started
                with app.state.session_factory() as db:
                    jobs = db.scalars(select(JobRecord)).all()
                    usages = db.scalars(select(UsageEventRecord)).all()
                    assert len(jobs) == args.jobs
                    assert all(job.status == "completed" for job in jobs), [
                        job.status for job in jobs
                    ]
                    assert len(usages) == args.jobs, len(usages)
                assert len(gateway.image_calls) == args.jobs, len(gateway.image_calls)
                assert gateway.external_network_request_count == 0
                report = {
                    "worker_slots": slots,
                    "page_concurrency": args.page_concurrency,
                    "http_clients": args.clients,
                    "reads": args.reads,
                    "read_statuses": statuses,
                    "read_requests_per_second": round(args.reads / read_duration, 2),
                    "read_ms": {
                        "p50": percentile(samples, 0.50),
                        "p95": percentile(samples, 0.95),
                        "p99": percentile(samples, 0.99),
                    },
                    "submission_p95_ms": percentile([x[2] for x in submissions], 0.95),
                    "jobs": args.jobs,
                    "completed": len(jobs),
                    "usage_events": len(usages),
                    "generation_seconds": round(generation_seconds, 3),
                    "jobs_per_second": round(args.jobs / generation_seconds, 3),
                    "queue_wait_p95_ms": percentile(waits, 0.95),
                    "execution_p95_ms": percentile(executions, 0.95),
                    "max_active_image_requests": gateway.max_active_images,
                    "image_calls": len(gateway.image_calls),
                    "duplicate_deliveries": 4,
                    "paid_provider_requests": 0,
                }
                print(json.dumps(report), flush=True)
                return report
    finally:
        await consumer.aclose()
        broker.delete(queue)
        broker.close()
        app.state.engine.dispose()
        with engine.begin() as conn:
            conn.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        engine.dispose()


async def main(args):
    results = []
    for slots in args.slots:
        results.append(await measure(args, slots))
    report = {
        "scope": (
            "isolated ASGI HTTP + real PostgreSQL + real Redis + durable coordinator "
            "+ deterministic no-network image gateway"
        ),
        "limits": (
            "Not production Celery, remote network latency, real image quality, "
            "or supplier capacity."
        ),
        "provider_delay_seconds": args.provider_delay,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slots", type=int, nargs="+", default=[2, 4, 6, 12])
    parser.add_argument("--jobs", type=int, default=36)
    parser.add_argument("--reads", type=int, default=320)
    parser.add_argument("--clients", type=int, default=32)
    parser.add_argument("--page-concurrency", type=int, default=6)
    parser.add_argument("--provider-delay", type=float, default=1)
    args = parser.parse_args()
    database = make_url(args.database_url)
    broker = urlparse(args.redis_url)
    if (
        database.host not in {"127.0.0.1", "localhost"}
        or database.database != "polaris_load"
        or database.username != "polaris_load"
        or broker.hostname not in {"127.0.0.1", "localhost"}
        or broker.port != 56382
        or not all(1 <= n <= 32 for n in args.slots)
        or not 1 <= args.clients <= 128
        or not 1 <= args.jobs <= 500
    ):
        raise SystemExit(
            "Only explicitly isolated localhost capacity services are allowed."
        )
    asyncio.run(main(args))
