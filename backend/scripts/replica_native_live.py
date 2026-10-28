"""Explicitly authorized live replica acceptance; never print credentials."""

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.config import Settings
from app.replica_native import (
    NativeReplicaRun,
    digest,
    native_accounting,
    read_json,
    write_json,
)
from app.replica_native_provider import NativeReplicaProvider
from app.storage import ImageValidator, LocalImageStorage
from app.xinghe_agent import build_tengli_gateway


async def main(method, run_tag=None, plan_only=False, fresh_campaign=False):
    base = Path("outputs/native-live").resolve()
    prefix = method + ("-" + run_tag if run_tag else "")
    gateway = build_tengli_gateway(
        Settings(_env_file="backend/.env", replica_image_failover_enabled=True)
    )
    original_edit = gateway.edit

    async def serial_edit(request):
        import fcntl

        with (base / "validation-image-call.lock").open("a") as lock:
            await asyncio.to_thread(fcntl.flock, lock.fileno(), fcntl.LOCK_EX)
            try:
                return await original_edit(request)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    if __import__("os").name != "nt":
        gateway.edit = serial_edit
    storage = LocalImageStorage(
        base / "storage", ImageValidator(20_000_000, 50_000_000)
    )
    sources = {key: base / (key + ".img") for key in ("product", "reference")}
    storage.root.mkdir(parents=True, exist_ok=True)
    for key, path in sources.items():
        (storage.root / key).write_bytes(path.read_bytes())
    pointer = base / (prefix + "-run.json")
    identity = read_json(pointer) if pointer.exists() else {"id": "rep_" + uuid4().hex}
    write_json(pointer, identity)
    run = NativeReplicaRun(
        method,
        identity["id"],
        "ws_native_acceptance",
        gateway,
        storage,
        root=base / method / identity["id"],
    )
    ask = run.ask

    async def logged(stage, *args, **kwargs):
        print(stage + ": observing", flush=True)
        value = await ask(stage, *args, **kwargs)
        print(stage + ": received", flush=True)
        return value

    run.ask = logged
    snapshot_path = base / (prefix + "-approved-native.json")
    if snapshot_path.exists():
        await run.restore(read_json(snapshot_path))
    elif (
        method == "exact"
        and (run.root / "session.json").exists()
        and run.state()["state"] == "READY"
    ):
        run.context = read_json(run.root / "context.json")
        write_json(snapshot_path, await run.snapshot())
        print("recovered same audited plan", flush=True)
    elif (
        method == "exact"
        and (run.root / "session.json").exists()
        and run.state()["state"] == "PLAN_REJECTED"
    ):
        run.context = read_json(run.root / "context.json")
        audits = sorted(
            (run.root / "contracts").glob("exact-plan-audit-*.json"),
            key=lambda path: path.stat().st_mtime,
        )
        if audits:
            run.context["prior_visual_audit"] = read_json(audits[-1])
        original = run.state()["input"]
        observation = read_json(run.root / "identity-segmentation.json")
        plans = await run._prepare_exact(
            [Path(p) for p in original["product_images"]],
            [Path(p) for p in original["reference_images"]],
            observation["units"],
            observation["product_geometry"],
        )
        write_json(base / (prefix + "-plans.json"), plans)
        write_json(snapshot_path, await run.snapshot())
    elif method == "style" and list(run.root.glob("run-*/artifacts/workflow.json")):
        workflows = sorted(
            run.root.glob("run-*/artifacts/workflow.json"),
            key=lambda path: path.stat().st_mtime,
        )
        candidates = [
            path
            for path in workflows
            if read_json(path)["state"]
            in {"PRODUCT_FACTS_READY", "PLAN_AUDIT_REQUIRED"}
        ]
        if not candidates:
            raise RuntimeError(
                "No recoverable pre-generation style plan; inspect native state"
            )
        run.run = candidates[-1].parents[1]
        run.context = read_json(run.root / "context.json")
        audits = sorted(
            (run.root / "contracts").glob("style-plan-audit-r*.json"),
            key=lambda path: path.stat().st_mtime,
        )
        plans = await run.finish_style_planning(
            read_json(audits[-1]) if audits else None
        )
        write_json(base / (prefix + "-plans.json"), plans)
        write_json(snapshot_path, await run.snapshot())
        print("recovered same style run with a fresh audited plan", flush=True)
    else:
        payload = {
            "product_asset_ids": ["product"],
            "references": [
                {
                    "asset_id": "reference",
                    "title": "香水商业构图",
                    "aspect_ratio": "follow-reference",
                }
            ],
            "facts": (
                "保留实拍香水瓶的粉色液体、方形玻璃瓶、透明方形瓶盖、银色蝴蝶结及原标签文字。"
                "文案使用简体中文，不虚构功效、成分、价格或认证。"
            ),
            "language": "zh-CN",
            "platform_code": "tmall",
        }
        records = {
            key: (path.name, "image/png", digest(path.read_bytes()), key, 0, 0)
            for key, path in sources.items()
        }
        plans = await run.prepare(payload, records)
        write_json(base / (prefix + "-plans.json"), plans)
        write_json(snapshot_path, await run.snapshot())
        print("plan native audit passed; screens=" + str(len(plans)), flush=True)
    if plan_only:
        return
    provider = NativeReplicaProvider(None, gateway, storage)

    async def save(current, *args, **kwargs):
        write_json(base / (prefix + "-execution-native.json"), await current.snapshot())

    provider._save = save
    execution_path = base / (prefix + "-execution-native.json")
    if execution_path.exists() and not fresh_campaign:
        await run.restore(read_json(execution_path))
    if fresh_campaign and execution_path.exists():
        # Match an explicit website full retry: restart the confirmed plan;
        # retain the previous campaign's immutable execution evidence separately.
        execution_path.rename(
            execution_path.with_name(prefix + "-previous-" + uuid4().hex[:8] + ".json")
        )
    request = SimpleNamespace(
        workspace_id="ws_native_acceptance",
        job_id="job_" + method,
        job_item_id="item_live",
        attempt_id="attempt_live",
    )
    delivery = await (
        provider._exact(run, request)
        if method == "exact"
        else provider._style(run, request)
    )
    write_json(
        base / (prefix + "-acceptance.json"),
        {
            "method": method,
            "skill_id": run.skill_id,
            "source_hash": run.source_hash,
            "delivery": delivery,
            "native_state": run.state()["state"],
            "state": run.state(),
            "usage": run.usage,
            "cost_micros": native_accounting(run.usage)["actual_cost_micros"],
            "known_cost_subtotal_micros": run.cost,
        },
    )
    print("ACCEPTED " + method + "; count=" + str(len(delivery)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("method", choices=["exact", "style"])
    parser.add_argument("--run-tag", choices=["verified", "final"])
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--fresh-campaign", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.method, args.run_tag, args.plan_only, args.fresh_campaign))
