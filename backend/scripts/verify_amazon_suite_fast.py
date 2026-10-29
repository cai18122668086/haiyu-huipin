"""Verify Amazon fast through local website endpoints and the real worker.

Default invocation only checks local prerequisites. ``--run`` performs paid
upstream calls. All images, complete prompts, receipts and the isolated SQLite
stay under an ignored output directory. This never accesses production data.
Run with PYTHONPATH pointing to this checkout's backend.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = (
    REPO.parent / "outputs/real-cases-20261029/lifestyle-originals/06-tumbler.png"
)
DEFAULT_ENV = REPO.parent / "polaris-commerce-next/backend/.env"
DEFAULT_OUTPUT = REPO / "backend/.data/amazon-suite-fast-live"
WORKSPACE = "ws_amazon_suite_fast_local"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


class TraceGateway:
    """Persist exact prompts and real raw results without exposing credentials."""

    def __init__(self, wrapped: Any, root: Path) -> None:
        self.wrapped = wrapped
        self.root = root
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.event_file = "events.private.json"
        self.calls = {"planning": 0, "image": 0, "review": 0}
        self.image_hashes: dict[str, str] = {}
        self.signed_downloads: dict[str, bytes] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    def emit(self, kind: str, **values: Any) -> None:
        event = {
            "event": kind,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            **values,
        }
        self.events.append(event)
        save(self.root / self.event_file, self.events)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    async def plan(self, request: Any) -> Any:
        from app.xinghe_agent import _planner_prompt, _planner_response_format

        self.calls["planning"] += 1
        self.emit("planner_started", profile=request.planning_profile)
        # This is the complete effective planner input, excluding binary sources.
        frozen = {
            key: value for key, value in vars(request).items() if key != "sources"
        }
        frozen["sources"] = [
            {
                "asset_id": source.asset_id,
                "sha256": source.sha256,
                "source_role": source.source_role,
            }
            for source in request.sources
        ]
        frozen["assembled_planning_prompt"] = _planner_prompt(request)
        frozen["response_format"] = _planner_response_format(request)
        save(
            self.root / "calls" / f"plan-{self.calls['planning']}-request.private.json",
            frozen,
        )
        started = time.monotonic()
        result = await self.wrapped.plan(request)
        save(
            self.root / "calls" / f"plan-{self.calls['planning']}-result.private.json",
            {
                "payload": result.payload,
                "provider_request_id": result.provider_request_id,
                "error_code": result.error_code,
                "error_message": result.error_message,
                "raw_usage": result.raw_usage,
                "normalized_usage": result.normalized_usage,
                "actual_cost_micros": result.actual_cost_micros,
                "api_latency_seconds": result.api_latency_seconds,
            },
        )
        self.emit(
            "planner_returned",
            error=result.error_code,
            seconds=round(time.monotonic() - started, 3),
        )
        return result

    async def edit(self, request: Any) -> Any:
        self.calls["image"] += 1
        item_id = request.job_item_id
        self.emit(
            "image_started", item_id=item_id, quality=request.quality, size=request.size
        )
        frozen = {
            key: value for key, value in vars(request).items() if key != "sources"
        }
        frozen["sources"] = [
            {
                "asset_id": source.asset_id,
                "sha256": source.sha256,
                "source_role": source.source_role,
            }
            for source in request.sources
        ]
        save(self.root / "calls" / f"{item_id}-request.private.json", frozen)
        started = time.monotonic()
        result = await self.wrapped.edit(request)
        receipt = {
            "provider_request_id": result.provider_request_id,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "outcome": str(result.outcome),
            "http_status": result.http_status,
            "raw_usage": result.raw_usage,
            "normalized_usage": result.normalized_usage,
            "actual_cost_micros": result.actual_cost_micros,
            "api_latency_seconds": result.api_latency_seconds,
        }
        if result.artifact:
            artifact = result.artifact
            raw = artifact.payload
            self.image_hashes[item_id] = digest(raw)
            directory = self.root / "raw"
            directory.mkdir(exist_ok=True)
            (directory / f"{item_id}.image").write_bytes(raw)
            receipt.update(
                width=artifact.width,
                height=artifact.height,
                sha256=digest(raw),
                locally_resampled=artifact.locally_resampled,
                provider_width=artifact.provider_width,
                provider_height=artifact.provider_height,
            )
        save(self.root / "calls" / f"{item_id}-result.private.json", receipt)
        self.emit(
            "image_returned",
            item_id=item_id,
            outcome=str(result.outcome),
            error=result.error_code,
            seconds=round(time.monotonic() - started, 3),
            size=[result.artifact.width, result.artifact.height]
            if result.artifact
            else None,
        )
        return result

    async def review_amazon(self, request: Any) -> Any:
        self.calls["review"] += 1
        self.emit("unexpected_ai_review")
        raise RuntimeError(
            "Fast verification refuses a blocking AI review or redraw cycle"
        )


def credit_snapshot(app: Any, job_id: str | None = None) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models import CreditReservationRecord, WorkspaceCreditAccountRecord

    with app.state.session_factory() as session:
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        result = {
            "balance": account.balance_credits if account else None,
            "reserved": account.reserved_credits if account else None,
        }
        if job_id:
            reservation = session.scalar(
                select(CreditReservationRecord).where(
                    CreditReservationRecord.job_id == job_id
                )
            )
            result["reservation"] = (
                None
                if reservation is None
                else {
                    "reserved": reservation.reserved_credits,
                    "settled": reservation.settled_credits,
                    "status": reservation.status,
                }
            )
        return result


def collect(
    client: Any, app: Any, root: Path, job_id: str, trace: TraceGateway
) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models import AssetRecord, JobAssetRecord, JobItemRecord

    base = f"/api/v1/workspaces/{WORKSPACE}"
    response = client.get(f"{base}/jobs/{job_id}")
    response.raise_for_status()
    job = response.json()
    save(root / "final-job.private.json", job)
    save(root / "jobs" / f"{job_id}-final.private.json", job)
    downloads = []
    with app.state.session_factory() as session:
        rows = session.execute(
            select(JobAssetRecord, JobItemRecord, AssetRecord)
            .join(JobItemRecord, JobItemRecord.id == JobAssetRecord.job_item_id)
            .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
            .where(
                JobAssetRecord.job_id == job_id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.is_deliverable.is_(True),
            )
            .order_by(JobItemRecord.ordinal)
        ).all()
        for _, item, asset in rows:
            access = client.post(
                f"{base}/assets/{asset.id}/access-url",
                json={"disposition": "attachment", "variant": "original"},
            )
            access.raise_for_status()
            if asset.id in trace.signed_downloads:
                content = trace.signed_downloads[asset.id]
            else:
                download = client.get(access.json()["url"])
                download.raise_for_status()
                content = download.content
                trace.signed_downloads[asset.id] = content
            sha = digest(content)
            assert sha == asset.sha256, "Signed download changed saved original bytes"
            raw_sha = trace.image_hashes.get(item.id)
            if not raw_sha:
                root_item_id = item.configuration.get(
                    "amazon_fast_root_item_id", item.id
                )
                root_item = session.get(JobItemRecord, root_item_id)
                receipt = (
                    root_item.configuration.get("amazon_fast_receipt", {})
                    if root_item is not None
                    else {}
                )
                raw_sha = receipt.get("sha256")
            if raw_sha:
                assert sha == raw_sha, "Delivered asset changed the provider original"
            (root / "downloads").mkdir(exist_ok=True)
            extension = ".jpg" if asset.content_type == "image/jpeg" else ".png"
            (root / "downloads" / f"{item.item_key}{extension}").write_bytes(content)
            downloads.append(
                {
                    "item_key": item.item_key,
                    "ordinal": item.ordinal,
                    "sha256": sha,
                    "width": asset.width,
                    "height": asset.height,
                    "http_status": 200,
                    "provider_original_sha_matches": sha == raw_sha
                    if raw_sha
                    else None,
                }
            )
    archive = client.get(f"{base}/jobs/{job_id}/deliverables.zip")
    zip_hashes: list[str] = []
    if archive.status_code == 200:
        (root / "deliverables.zip").write_bytes(archive.content)
        with ZipFile(BytesIO(archive.content)) as bundle:
            assert bundle.testzip() is None
            zip_hashes = [
                digest(bundle.read(member))
                for member in bundle.infolist()
                if not member.is_dir()
            ]
        assert sorted(zip_hashes) == sorted(row["sha256"] for row in downloads)
    save(root / "download-integrity.json", downloads)
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "stage": job.get("stage"),
        "error_code": job.get("error_code"),
        "expected_images": 21,
        "download_count": len(downloads),
        "signed_download_count": len(downloads),
        "zip_http_status": archive.status_code,
        "zip_image_count": len(zip_hashes),
        "credit_final": credit_snapshot(app, job_id),
        "cost": job.get("cost"),
        "semantic_review": "NOT_REVIEWED",
        "production_modified": False,
        "full_suite_delivered": job.get("status") == "completed"
        and len(downloads) == len(zip_hashes) == 21,
    }


async def run_observed(client: Any, app: Any, job_id: str, trace: TraceGateway) -> None:
    """Record the first genuinely downloadable output during worker execution."""
    from sqlalchemy import select

    from app.models import JobAssetRecord

    worker = asyncio.create_task(app.state.task_runner.run(job_id))
    try:
        while not worker.done():
            if not trace.signed_downloads:
                with app.state.session_factory() as session:
                    asset_id = session.scalar(
                        select(JobAssetRecord.asset_id)
                        .where(
                            JobAssetRecord.job_id == job_id,
                            JobAssetRecord.direction == "output",
                            JobAssetRecord.is_deliverable.is_(True),
                        )
                        .limit(1)
                    )
                if asset_id:
                    base = f"/api/v1/workspaces/{WORKSPACE}"
                    access = client.post(
                        f"{base}/assets/{asset_id}/access-url",
                        json={"disposition": "attachment", "variant": "original"},
                    )
                    access.raise_for_status()
                    response = client.get(access.json()["url"])
                    response.raise_for_status()
                    trace.signed_downloads[asset_id] = response.content
                    trace.emit("first_signed_download", http_status=200)
            await asyncio.sleep(2)
        await worker
    finally:
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def validate_recovery_cache(app: Any, job_id: str) -> int:
    """Refuse retries unless every failed item already has an acceptable original."""
    from sqlalchemy import select

    from app.amazon_suite_fast_provider import AmazonFastProvider
    from app.amazon_visual_suite_provider import amazon_item_spec
    from app.models import JobItemRecord

    originals = []
    with app.state.session_factory() as session:
        for item in session.scalars(
            select(JobItemRecord).where(
                JobItemRecord.job_id == job_id, JobItemRecord.status == "failed"
            )
        ).all():
            root = session.get(
                JobItemRecord,
                item.configuration.get("amazon_fast_root_item_id", item.id),
            )
            receipt = root.configuration.get("amazon_fast_receipt", {})
            if receipt.get("state") not in {"returned", "rejected"} or not receipt.get(
                "object_key"
            ):
                raise RuntimeError(
                    "A failed item has no confirmed original; "
                    "refusing model-free recovery"
                )
            originals.append((item.item_key, receipt["object_key"], receipt["sha256"]))
    if not originals:
        raise RuntimeError("No failed items with originals need recovery")
    for key, object_key, sha in originals:
        data = await app.state.storage.read_bytes(object_key)
        if digest(data) != sha:
            raise RuntimeError("Stored original SHA mismatch")
        AmazonFastProvider.artifact(data, amazon_item_spec(key))
    return len(originals)


def main() -> None:
    from fastapi.testclient import TestClient

    from app import xinghe_agent
    from app.config import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--run", action="store_true", help="Make real paid planner/image calls"
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Download an existing local run without invoking models",
    )
    parser.add_argument(
        "--recover-existing",
        action="store_true",
        help=(
            "Explicit failed-item retry using only confirmed cached originals; "
            "all model calls forbidden"
        ),
    )
    args = parser.parse_args()
    root = args.output.resolve()
    # Keep private receipts inside this checkout's explicitly ignored subtree.
    if not root.is_relative_to((REPO / "backend/.data").resolve()):
        raise ValueError("Output must be inside this checkout backend/.data")
    if not args.source.is_file() or not args.env.is_file():
        raise FileNotFoundError("Local source image or provider env is missing")
    config = Settings(_env_file=args.env)
    if not args.run and not args.collect_only and not args.recover_existing:
        print(
            json.dumps(
                {
                    "prepared": True,
                    "source_sha256": digest(args.source.read_bytes()),
                    "output": str(root),
                    "run_started": False,
                    "credentials_configured": any(
                        bool(getattr(config, name).get_secret_value())
                        for name in [
                            "analysis_api_key",
                            "image_api_key",
                            "tengli_api_key",
                        ]
                    ),
                }
            )
        )
        return
    if sum([args.run, args.collect_only, args.recover_existing]) != 1:
        raise ValueError("Choose only --run, --collect-only or --recover-existing")
    if args.run and (root / "live.db").exists():
        raise FileExistsError(
            "Existing run database: use --collect-only; never resubmit unknown results"
        )
    root.mkdir(parents=True, exist_ok=True)
    from app.main import create_app

    settings = config.model_copy(
        update={
            "environment": "development",
            "auth_mode": "development",
            "development_user_id": "usr_amazon_suite_fast_local",
            "development_workspace_id": WORKSPACE,
            "development_workspace_name": "Amazon fast isolated local verification",
            "access_url_secret": "amazon-fast-isolated-download-verification",
            "database_url": "sqlite:///" + (root / "live.db").as_posix(),
            "auto_create_schema": True,
            "storage_backend": "local",
            "storage_dir": root / "storage",
            "aurora_work_root": root / "work",
            "task_runner": "tengli",
            "auto_process_jobs": False,
            "task_dispatcher": "inline",
            "amazon_visual_suite_provider_enabled": True,
            "product_image_provider_enabled": False,
            "reference_remix_provider_enabled": False,
            "domestic_main_image_provider_enabled": False,
            "creative_image_provider_enabled": False,
            "crossborder_detail_provider_enabled": False,
            "model_pose_transfer_provider_enabled": False,
            "workspace_credit_enforcement_enabled": True,
            "provider_quota_enabled": False,
            "credit_payments_enabled": False,
            "credit_payment_reconciliation_enabled": False,
            "ecommerce_login_enabled": False,
            "ecommerce_startup_recovery_enabled": False,
            "image_capacity_redis_url": "",
            "worker_async_runtime_enabled": False,
            "provider_async_transport_enabled": False,
            "xinghe_concurrency": 20,
            "tengli_smoke_budget_usd": 20,
            "max_upload_bytes": 30_000_000,
            "max_image_pixels": 40_000_000,
        }
    )
    wrapped = xinghe_agent.build_tengli_gateway(settings)
    if isinstance(wrapped, xinghe_agent.FakeXingheGateway):
        raise RuntimeError("A real network gateway is required")
    trace = TraceGateway(wrapped, root)
    original_builder = xinghe_agent.build_tengli_gateway
    xinghe_agent.build_tengli_gateway = lambda _: trace
    try:
        app = create_app(settings)
    finally:
        xinghe_agent.build_tengli_gateway = original_builder
    with TestClient(app) as client:
        base = f"/api/v1/workspaces/{WORKSPACE}"
        manifest_path = root / "manifest.private.json"
        if args.recover_existing:
            trace.event_file = "recovery-events.private.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            source_job_id = manifest["job_id"]
            cache_count = asyncio.run(validate_recovery_cache(app, source_job_id))

            async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
                raise RuntimeError(
                    "Model-free recovery attempted a prohibited new model call"
                )

            wrapped.plan = wrapped.edit = forbidden
            response = client.post(
                f"{base}/jobs/{source_job_id}/retry",
                headers={
                    "Idempotency-Key": "amazon-fast-cached-recovery-" + source_job_id
                },
                json={"scope": "failed"},
            )
            response.raise_for_status()
            job_id = response.json()["id"]
            manifest.update(job_id=job_id, recovered_from_job_id=source_job_id)
            save(manifest_path, manifest)
            reserved = credit_snapshot(app, job_id)
            trace.emit(
                "cached_recovery_submitted",
                job_id=job_id,
                validated_originals=cache_count,
            )
            asyncio.run(run_observed(client, app, job_id, trace))
            summary = collect(client, app, root, job_id, trace)
            summary.update(
                new_calls=trace.calls,
                validated_originals=cache_count,
                recovered_from_job_id=source_job_id,
                credit_reserved=reserved,
                recovery_seconds=round(time.monotonic() - trace.started, 3),
            )
            save(root / "recovery-summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            assert trace.calls == {"planning": 0, "image": 0, "review": 0}
            if not summary["full_suite_delivered"]:
                raise SystemExit(2)
            return
        if args.collect_only:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary = collect(client, app, root, manifest["job_id"], trace)
            save(root / "collection-summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False))
            return
        upload_key, job_key = str(uuid4()), str(uuid4())
        response = client.post(
            base + "/assets",
            headers={"Idempotency-Key": upload_key},
            files={"file": (args.source.name, args.source.read_bytes(), "image/png")},
        )
        response.raise_for_status()
        source = response.json()
        before = credit_snapshot(app)
        payload = {
            "type": "amazon_visual_suite",
            "intent": "submit",
            "display_name": "Amazon 21 fast local real verification",
            "input": {"white_background_product_asset_ids": [source["id"]]},
            "configuration": {
                "amazon_speed": "fast",
                "auto_start_after_plan": True,
                "marketplace": "amazon-us",
                "product_name": "Light blue handled tumbler with clear lid",
                "listing_title": "Light Blue Tumbler with Handle and Clear Lid",
                "product_facts": [
                    "Light blue body and side handle",
                    "Clear lid with blue closure",
                    "Metallic rim",
                ],
                "forbidden_claims": [
                    "Do not invent capacity, insulation duration, leakproofness, "
                    "material grades, certifications, ratings or package contents"
                ],
                "instruction": (
                    "Preserve the single pictured product as the identity master. "
                    "The room and props are reference context, not included "
                    "accessories. Main image must independently depict the real "
                    "product against pure white. Avoid unsupported performance "
                    "comparisons or measurement charts. Design all 21 distinct "
                    "deliverables with independent desktop and mobile compositions."
                ),
                "language": "en-US",
            },
        }
        response = client.post(
            base + "/jobs", headers={"Idempotency-Key": job_key}, json=payload
        )
        if response.status_code != 202:
            save(root / "submission-failure.private.json", response.json())
        response.raise_for_status()
        job_id = response.json()["id"]
        save(
            manifest_path,
            {
                "job_id": job_id,
                "source_sha256": source["sha256"],
                "job_idempotency_key": job_key,
                "source_asset_id": source["id"],
                "provider": trace.provider_name,
                "planner_model": trace.planner_model,
                "image_model": trace.image_model,
            },
        )
        replay = client.post(
            base + "/jobs", headers={"Idempotency-Key": job_key}, json=payload
        )
        replay.raise_for_status()
        assert replay.json()["id"] == job_id
        reserved = credit_snapshot(app, job_id)
        trace.emit("job_submitted", job_id=job_id)
        asyncio.run(run_observed(client, app, job_id, trace))
        summary = collect(client, app, root, job_id, trace)
        original_calls = dict(trace.calls)
        if summary["status"] == "completed":
            asyncio.run(app.state.task_runner.run(job_id))
            assert trace.calls == original_calls, (
                "Duplicate dispatch invoked upstream models"
            )
        events = trace.events
        image_returns = [
            event["elapsed_seconds"]
            for event in events
            if event["event"] == "image_returned"
        ]
        summary.update(
            calls=trace.calls,
            credit_before=before,
            credit_reserved=reserved,
            duplicate_dispatch_added_calls={
                key: trace.calls[key] - original_calls[key] for key in trace.calls
            },
            total_seconds=round(time.monotonic() - trace.started, 3),
            first_provider_return_seconds=min(image_returns) if image_returns else None,
            last_provider_return_seconds=max(image_returns) if image_returns else None,
            first_download_seconds=next(
                (
                    event["elapsed_seconds"]
                    for event in events
                    if event["event"] == "first_signed_download"
                ),
                None,
            ),
            prompt_comparison=(
                "No scheduling A/B performed; complete inputs/prompts frozen "
                "in ignored calls directory"
            ),
            actual_provider_currency_cost=None,
        )
        save(root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if not summary["full_suite_delivered"]:
            raise SystemExit(2)
        assert trace.calls == {"planning": 1, "image": 21, "review": 0}, trace.calls
        assert summary["credit_final"]["reservation"]["status"] == "settled"


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
