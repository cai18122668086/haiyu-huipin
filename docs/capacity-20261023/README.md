# Isolated capacity verification, 2026-10-23

The probe uses actual FastAPI ASGI requests, PostgreSQL 17, Redis 7, the durable coordinator and a deterministic no-network image gateway with a one-second delay. It uses a Redis consumer harness, **not production Celery processes or real supplier traffic**. Results do not establish production API limits, image quality, network latency, CPU/memory capacity or a safe production worker count.

| Consumers | Per-process image limit | Jobs | Completed | Image calls | Generation seconds | Jobs/second |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 6 | 36 | 36 | 36 | 22.616 | 1.592 |
| 4 | 6 | 36 | 36 | 36 | 12.514 | 2.877 |
| 6 | 6 | 36 | 36 | 36 | 8.641 | 4.166 |
| 12 | 6 | 36 | 36 | 36 | 7.097 | 5.073 |
| 12 | 12 | 72 | 72 | 72 | 10.792 | 6.672 |

The first four cases used 32 HTTP clients and 320 reads each, all HTTP 200. The last used 64 clients and 640 reads, all HTTP 200; read p95 712.59 ms and submission p95 2184.55 ms. Each case replayed four accepted HTTP submissions and four queue deliveries without creating additional jobs, image calls or usage events. No paid requests were made. See the JSON for full samples and scope.

## Configuration

Image concurrency accepts integers 1..32, with the existing default 6. Compose worker concurrency is configurable through POLARIS_WORKER_CONCURRENCY, also default 6. The private replica release preparer accepts --worker-concurrency 1..16, defaults to 4 instead of 2, and prepares a 2 GiB limit instead of 1 GiB; the script does not deploy. Replica analysis concurrency is configurable 1..16, default 4. Worker count, per-worker image limit, supplier admission limits and memory must be sized together; these are separate controls.

## Reproduce

Use backend/scripts/measure_capacity.py with a dedicated localhost PostgreSQL database/user polaris_load and dedicated Redis on port 56382. The script creates random schemas and queues and removes only its own schema/queue. It rejects other destinations. Pass --slots 2 4 6 12 --jobs 36 --reads 320 --clients 32, or --slots 12 --page-concurrency 12 --jobs 72 --reads 640 --clients 64. Supply the isolated database/Redis URLs and output path explicitly.

Validation includes real PostgreSQL coordinator/explicit retry/usage durability, configurable 12-way execution, fail-closed bounds, and the existing deploy preflight suite. Production was not modified or deployed.
