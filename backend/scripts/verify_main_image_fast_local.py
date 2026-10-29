"""Opt-in paid acceptance through local website APIs; credentials never exported.

Run from the repository with PYTHONPATH pointing at this repository's backend.
Without --run this command performs no API requests or filesystem mutations.
Private inputs, frozen prompts, receipts, database and images stay in .data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = Path("C:/Users/a1726/Desktop/杂/身体乳测试")
VARIANT_PROFILES = {
    "native_fast": "aurora_native_fast_identity",
}
DEFAULT_OUTPUTS = {
    "fast": REPO / "backend/.data/main-image-fast-local",
    "native_fast": REPO / "backend/.data/main-image-native-fast-local",
}
PRODUCT_FILES = (
    "O1CN01G5SSkK4MBQL1SdaC_!!4611686018427381386-0-rate.jpg_960x960.jpg",
    "O1CN01krdMZyjilNL2BxNI_!!4611686018427387074-0-tbbala.jpg_960x960.jpg",
    "O1CN015sz28h7RPyI68gFc~livephoto~_!!4019824527-0-rate_livephoto.jpg_960x960.jpg",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


class Observer:
    """Observe the real configured gateway and fence accidental extra paid calls."""

    def __init__(self, gateway: Any, root: Path, variant: str = "native_fast") -> None:
        self.gateway = gateway
        self.root = root
        self.variant = variant
        self.profile = VARIANT_PROFILES[variant]
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.plan_count = 0
        self.image_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.gateway, name)

    def event(self, kind: str, **values: Any) -> None:
        with self.lock:
            row = {
                "event": kind,
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                **values,
            }
            self.events.append(row)
            write_json(self.root / "events.json", self.events)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    async def plan(self, request: Any) -> Any:
        self.plan_count += 1
        if self.plan_count > 1 or request.planning_profile != self.profile:
            raise RuntimeError("Acceptance budget stops a second planning/audit call")
        self.event("plan_started", profile=request.planning_profile)
        write_json(
            self.root / "planning-request-private.json",
            {
                "profile": request.planning_profile,
                "system_prompt": request.system_prompt,
                "product_information": request.product_information,
                "source_sha256": [source.sha256 for source in request.sources],
            },
        )
        result = await self.gateway.plan(request)
        write_json(self.root / "planning-result-private.json", result.payload)
        self.event(
            "plan_returned",
            api_seconds=result.api_latency_seconds,
            error=result.error_code,
            usage=result.normalized_usage,
            actual_cost_micros=result.actual_cost_micros,
        )
        if result.error_code:
            write_json(
                self.root / "planning-failure-private.json",
                {
                    "error_code": result.error_code,
                    "error_message": getattr(result, "error_message", None),
                },
            )
        return result

    async def edit(self, request: Any) -> Any:
        self.image_count += 1
        if self.image_count > 6:
            raise RuntimeError("Acceptance budget stops more than six image calls")
        screen = request.page_number
        self.event(
            "image_started", screen=screen, size=request.size, quality=request.quality
        )
        write_json(
            self.root / f"image-request-{screen:02d}-private.json",
            {
                "screen": screen,
                "prompt": request.prompt,
                "prompt_sha256": digest(request.prompt.encode()),
                "size": request.size,
                "quality": request.quality,
                "source_sha256": [source.sha256 for source in request.sources],
            },
        )
        result = await self.gateway.edit(request)
        artifact = result.artifact
        self.event(
            "image_returned",
            screen=screen,
            api_seconds=result.api_latency_seconds,
            outcome=str(result.outcome),
            error=result.error_code,
            sha256=digest(artifact.payload) if artifact else None,
            width=artifact.width if artifact else None,
            height=artifact.height if artifact else None,
            usage=result.normalized_usage,
            actual_cost_micros=result.actual_cost_micros,
        )
        return result


def checked(response: Any, expected: int) -> Any:
    if response.status_code != expected:
        raise RuntimeError(
            f"Local website API {response.request.url.path} HTTP {response.status_code}"
        )
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Allow one real paid suite")
    parser.add_argument(
        "--variant",
        choices=tuple(VARIANT_PROFILES),
        default="native_fast",
        help="native_fast = retained original-style workflow",
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO.parent / "polaris-commerce-next/backend/.env",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    root = (args.output_dir or DEFAULT_OUTPUTS[args.variant]).resolve()
    original_fast_root = DEFAULT_OUTPUTS["fast"].resolve()
    if args.variant == "native_fast" and (
        root == original_fast_root or root.is_relative_to(original_fast_root)
    ):
        parser.error(
            "native_fast requires its own directory outside the option-A output"
        )
    private_root = (REPO / "backend/.data").resolve()
    if not root.is_relative_to(private_root) or root == private_root:
        parser.error("Output must be a dedicated subdirectory of this backend/.data")
    if not args.run:
        print("Dry run: no requests, no charges, no files written.")
        print("Use --run only for the authorized one-plan/six-image local acceptance.")
        print(
            f"Variant: {args.variant}; "
            f"allowed profile: {VARIANT_PROFILES[args.variant]}"
        )
        print(f"Private evidence directory: {root}")
        return 0
    if (root / "run-started.json").exists() or (root / "live.db").exists():
        parser.error(
            "Prior run exists; inspect retained evidence before any new paid run"
        )
    if not args.env_file.is_file():
        parser.error("The existing provider configuration file is unavailable")
    for filename in (*PRODUCT_FILES, "卖点.txt"):
        if not (args.source_dir / filename).is_file():
            parser.error("A required authorized source input is unavailable")

    # Imports happen only after explicit --run. This script never reads or copies
    # another workspace's database, material records or application source.
    from fastapi.testclient import TestClient
    from PIL import Image
    from sqlalchemy import select

    from app import xinghe_agent
    from app.config import Settings
    from app.credit_memberships import grant
    from app.main import create_app
    from app.models import JobPlanRecord, WorkspaceCreditAccountRecord
    from app.outbox import publish_pending_events

    root.mkdir(parents=True, exist_ok=True)
    write_json(
        root / "run-started.json",
        {
            "run_id": uuid4().hex,
            "mode": "paid-fast",
            "variant": args.variant,
            "planning_profile": VARIANT_PROFILES[args.variant],
        },
    )
    config = Settings(_env_file=args.env_file)
    settings = config.model_copy(
        update={
            "environment": "development",
            "auth_mode": "development",
            "database_url": f"sqlite:///{root / 'live.db'}",
            "auto_create_schema": True,
            "storage_backend": "local",
            "storage_dir": root / "storage",
            "aurora_work_root": root / "work",
            "task_runner": "tengli",
            "domestic_main_image_provider_enabled": True,
            "reference_remix_provider_enabled": False,
            "product_image_provider_enabled": False,
            "creative_image_provider_enabled": False,
            "crossborder_detail_provider_enabled": False,
            "amazon_visual_suite_provider_enabled": False,
            "model_pose_transfer_provider_enabled": False,
            "auto_process_jobs": False,
            "task_dispatcher": "inline",
            "workspace_credit_enforcement_enabled": True,
            "workspace_credit_pilot_ids": [],
            "provider_quota_enabled": False,
            "credit_payments_enabled": False,
            "credit_payment_reconciliation_enabled": False,
            "ecommerce_login_enabled": False,
            "ecommerce_startup_recovery_enabled": False,
            "image_capacity_redis_url": "",
            "worker_async_runtime_enabled": False,
            "provider_async_transport_enabled": False,
            "xinghe_concurrency": 6,
            "ecommerce_session_dir": root / "sessions/taobao",
            "ecommerce_1688_session_dir": root / "sessions/1688",
            "ecommerce_jd_session_dir": root / "sessions/jd",
            "ecommerce_pinduoduo_session_dir": root / "sessions/pinduoduo",
        }
    )
    observer = Observer(xinghe_agent.build_tengli_gateway(settings), root, args.variant)
    original_factory = xinghe_agent.build_tengli_gateway
    xinghe_agent.build_tengli_gateway = lambda _: observer
    try:
        app = create_app(settings)
    finally:
        xinghe_agent.build_tengli_gateway = original_factory
    base = f"/api/v1/workspaces/{settings.development_workspace_id}"
    downloaded: dict[str, dict[str, Any]] = {}

    with TestClient(app) as client:
        with app.state.session_factory() as session:
            account = session.get(
                WorkspaceCreditAccountRecord, settings.development_workspace_id
            )
            grant(
                session,
                account,
                "main-image-fast-local-acceptance",
                1000,
                None,
                "Local acceptance test credits",
                kind="test",
            )
            session.commit()
        before = checked(client.get(base + "/credits"), 200)
        asset_ids = []
        inputs = []
        for filename in PRODUCT_FILES:
            data = (args.source_dir / filename).read_bytes()
            result = checked(
                client.post(
                    base + "/assets",
                    headers={"Idempotency-Key": str(uuid4())},
                    files={"file": (filename, data, "image/jpeg")},
                ),
                201,
            )
            asset_ids.append(result["id"])
            inputs.append({"filename": filename, "sha256": digest(data)})
        facts = (args.source_dir / "卖点.txt").read_text(encoding="utf-8-sig").strip()
        write_json(root / "inputs-private.json", {"images": inputs, "facts": facts})
        observer.event("uploads_completed", count=len(asset_ids))
        created = checked(
            client.post(
                base + "/jobs",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "type": "domestic_main_image",
                    "intent": "submit",
                    "display_name": f"国内主图{args.variant}本地真实链路验收",
                    "input": {"product_asset_ids": asset_ids},
                    "configuration": {
                        "main_image_speed": args.variant,
                        "auto_start_after_plan": True,
                        "product_name": "OLAY 身体乳",
                        "category": "身体护理",
                        "selling_points": [facts],
                        "instruction": (
                            "第一张商品图为主商品身份，另外两张仅补充同一包装的可见事实；"
                            "包装版本不同的标签和细节不得混用。只使用用户提供的事实，"
                            "不虚构数据、认证、价格或使用前后曲线。"
                        ),
                    },
                },
            ),
            202,
        )
        job_id = created["id"]
        write_json(root / "created-job.json", created)
        observer.event("job_created", job_id=job_id)

        def freeze_plan() -> None:
            """Persist the immutable native or option-A prompts before comparison."""
            if (root / "frozen-prompts-private.json").exists():
                return
            with app.state.session_factory() as session:
                row = session.scalar(
                    select(JobPlanRecord).where(JobPlanRecord.job_id == job_id)
                )
                if row is None or row.confirmed_at is None:
                    return
                from app.aurora_main_image_fast_provider import image_prompt

                payload = row.payload
                prompts = [
                    {
                        "page_index": page["page_index"],
                        "page_role": page["page_role"],
                        "prompt": image_prompt(payload, page),
                        "prompt_sha256": digest(image_prompt(payload, page).encode()),
                        "source_asset_ids": page.get(
                            "source_asset_ids", payload["product_asset_ids"]
                        ),
                    }
                    for page in payload["pages"]
                ]
                write_json(root / "plan-frozen-private.json", payload)
                write_json(
                    root / "frozen-prompts-private.json",
                    {
                        "variant": args.variant,
                        "version": payload.get("version"),
                        "skill_id": payload.get("skill_id"),
                        "plan_hash": row.payload_hash,
                        "prompts": prompts,
                    },
                )

        def download_available(detail: dict[str, Any]) -> None:
            ordinals = {item["id"]: index for index, item in enumerate(detail["items"])}
            for asset in detail.get("assets", []):
                asset_id = asset["asset_id"]
                if (
                    asset.get("direction") != "output"
                    or not asset.get("is_deliverable")
                    or asset_id in downloaded
                ):
                    continue
                access = checked(
                    client.post(base + f"/assets/{asset_id}/access-url", json={}), 200
                )
                response = client.get(access["url"])
                if response.status_code != 200:
                    raise RuntimeError("Signed original image download failed")
                data = response.content
                with Image.open(BytesIO(data)) as image:
                    image.load()
                    size = list(image.size)
                    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[
                        image.format
                    ]
                ordinal = ordinals[asset["job_item_id"]]
                name = f"delivered-{ordinal + 1:02d}{suffix}"
                (root / name).write_bytes(data)
                sha = digest(data)
                native_hashes = {
                    event["sha256"]
                    for event in observer.events
                    if event["event"] == "image_returned" and event.get("sha256")
                }
                if sha not in native_hashes:
                    raise RuntimeError("Downloaded bytes differ from provider original")
                downloaded[asset_id] = {
                    "ordinal": ordinal,
                    "filename": name,
                    "sha256": sha,
                    "size": size,
                    "same_bytes_as_provider": True,
                    "http_status": 200,
                }
                observer.event("image_downloadable", ordinal=ordinal, sha256=sha)

        with ThreadPoolExecutor(max_workers=1) as pool:
            dispatch = pool.submit(
                publish_pending_events,
                settings,
                app.state.session_factory,
                app.state.task_runner,
            )
            while not dispatch.done():
                detail = checked(client.get(base + f"/jobs/{job_id}"), 200)
                freeze_plan()
                download_available(detail)
                time.sleep(0.5)
            published = dispatch.result()
        detail = checked(client.get(base + f"/jobs/{job_id}"), 200)
        freeze_plan()
        download_available(detail)
        observer.event("suite_finished", status=detail["status"], published=published)
        write_json(root / "job-public.json", detail)
        with app.state.session_factory() as session:
            plan = session.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == job_id)
            )
            write_json(root / "plan-private.json", plan.payload if plan else None)
        after = checked(client.get(base + "/credits"), 200)
        ledger = checked(client.get(base + "/credits/ledger"), 200)
        write_json(
            root / "credits.json", {"before": before, "after": after, "ledger": ledger}
        )
        zip_response = client.get(base + f"/jobs/{job_id}/deliverables.zip")
        zip_summary: dict[str, Any] = {"http_status": zip_response.status_code}
        if zip_response.status_code == 200:
            (root / "deliverables.zip").write_bytes(zip_response.content)
            with ZipFile(BytesIO(zip_response.content)) as archive:
                hashes = [digest(archive.read(name)) for name in archive.namelist()]
                zip_summary.update(
                    {
                        "members": len(hashes),
                        "valid": archive.testzip() is None,
                        "all_original_bytes": sorted(hashes)
                        == sorted(value["sha256"] for value in downloaded.values()),
                    }
                )
        returned = [
            event for event in observer.events if event["event"] == "image_returned"
        ]
        ready = [
            event for event in observer.events if event["event"] == "image_downloadable"
        ]
        plan_events = [
            event for event in observer.events if event["event"] == "plan_returned"
        ]
        image_starts = [
            event for event in observer.events if event["event"] == "image_started"
        ]
        costs = [event.get("actual_cost_micros") for event in returned + plan_events]
        frozen_match = None
        frozen_file = root / "frozen-prompts-private.json"
        if frozen_file.exists():
            frozen = json.loads(frozen_file.read_text(encoding="utf-8"))
            frozen_match = all(
                (
                    root / f"image-request-{page['page_index'] + 1:02d}-private.json"
                ).exists()
                and json.loads(
                    (
                        root
                        / f"image-request-{page['page_index'] + 1:02d}-private.json"
                    ).read_text(encoding="utf-8")
                )["prompt"]
                == page["prompt"]
                for page in frozen["prompts"]
            )
        summary = {
            "variant": args.variant,
            "planning_profile": VARIANT_PROFILES[args.variant],
            "frozen_prompts_match_actual_requests": frozen_match,
            "status": detail["status"],
            "job_id": job_id,
            "planning_calls": observer.plan_count,
            "image_calls": observer.image_count,
            "planning_seconds": plan_events[0]["api_seconds"] if plan_events else None,
            "first_image_returned_seconds": min(
                (event["elapsed_seconds"] for event in returned), default=None
            ),
            "first_image_downloadable_seconds": min(
                (event["elapsed_seconds"] for event in ready), default=None
            ),
            "complete_delivery_seconds": (
                max(event["elapsed_seconds"] for event in ready)
                if len(downloaded) == 6
                else None
            ),
            "suite_finished_seconds": observer.events[-1]["elapsed_seconds"],
            "image_submission_spread_seconds": (
                image_starts[-1]["elapsed_seconds"] - image_starts[0]["elapsed_seconds"]
                if image_starts
                else None
            ),
            "downloadable_images": len(downloaded),
            "downloads": list(downloaded.values()),
            "zip": zip_summary,
            "job_api_request_count": detail.get("api_request_count"),
            "job_cost": detail.get("cost"),
            "actual_upstream_cost_micros": sum(costs)
            if costs and all(isinstance(value, int) for value in costs)
            else None,
            "credit_balance_delta": before["balance_credits"]
            - after["balance_credits"],
            "reserved_credits_after": after["reserved_credits"],
            "ai_review_status": "NOT_REVIEWED",
            "limits": (
                "Single real sample; no frozen-plan scheduling comparison "
                "or quality equivalence claim."
            ),
        }
        write_json(root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return (
            0
            if (
                detail["status"] == "completed"
                and len(downloaded) == 6
                and observer.plan_count == 1
                and observer.image_count == 6
                and frozen_match is True
                and zip_summary.get("members") == 6
                and zip_summary.get("all_original_bytes")
            )
            else 1
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
