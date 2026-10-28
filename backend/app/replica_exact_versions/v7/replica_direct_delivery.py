"""Approved planning followed by generation and file delivery, without AI review.

Legacy sessions and their real verdicts remain immutable source history. This
execution policy records GENERATED / NOT_REVIEWED, never a synthetic PASS.
"""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .models import JobRecord
from .replica_canvas import delivery_receipt, native_budget_matches
from .replica_exact_contracts import canonical_hash
from .replica_native import ReplicaNativeError, contained, digest, read_json, write_json
from .replica_retry import _document

POLICY = "approved-plan-direct-delivery-v1"
V4_BINDING = {
    "version": "exact-compact-v4",
    "content_sha256": (
        "015928ce4643858ca182ed38e8a24c444ea866047548bc0e75a8744436999e83"
    ),
}
V5_BINDING = {
    "version": "exact-compact-v5",
    "content_sha256": (
        "82dddb7fffe4f733d3a5879c91ed8ba9f21dd40a4abaa3d05168c31f9ca8e438"
    ),
}
V6_BINDING = {
    "version": "exact-compact-v6",
    "content_sha256": (
        "67e52f5ff635e39f077a9819ceff2a0ee8d4fded1f44b3e0ffab2c9e3bca5c11"
    ),
}


def validate_source(
    previous: dict[str, Any], approved: dict[str, Any], prefix: str
) -> None:
    from .replica_exact_compact import protocol_binding
    from .replica_execution_resume import V3_BINDING

    allowed = (V3_BINDING, V4_BINDING, V5_BINDING, V6_BINDING, protocol_binding())
    if (
        previous.get("model_protocol") not in allowed
        or approved.get("model_protocol") not in allowed
        or previous.get("method") != "exact"
        or any(
            previous.get(k) != approved.get(k)
            for k in ("schema", "method", "skill_id", "source_hash", "root", "run")
        )
    ):
        raise ReplicaNativeError("直接交付来源与原方案版本或归属不一致。")
    before, plan = (
        _document(previous, "session.json"),
        _document(approved, "session.json"),
    )
    if plan.get("state") != "READY" or any(
        before.get(k) != plan.get(k)
        for k in ("runtime_version", "plan_sha256", "plan", "input", "plan_audit")
    ):
        raise ReplicaNativeError("直接交付必须保留原先通过检查的完整方案。")
    contexts = [
        {
            k: v
            for k, v in _document(s, "context.json").items()
            if k
            not in {"analysis_progress", "model_protocol", "post_generation_review"}
        }
        for s in (previous, approved)
    ]
    if contexts[0] != contexts[1]:
        raise ReplicaNativeError("直接交付来源的事实、素材或布局绑定发生变化。")
    for name, record in approved["files"].items():
        if "object_key" in record and previous["files"].get(name) != record:
            raise ReplicaNativeError("直接交付来源的原始素材哈希已变化。")
    if any(
        "object_key" in r and not r["object_key"].startswith(prefix)
        for r in previous["files"].values()
    ):
        raise ReplicaNativeError("直接交付产物未绑定当前工作区。")
    if "invocations.json" in previous["files"] and any(
        r.get("state") == "invoked"
        for r in _document(previous, "invocations.json").values()
    ):
        raise ReplicaNativeError("仍有尚未核实的生图请求，不能自动再次生图。")
    planned = plan["plan"]["modules"]
    modules = before.get("modules", [])
    if len(modules) != len(planned) or any(
        r.get("id") != m["id"] for r, m in zip(modules, planned, strict=True)
    ):
        raise ReplicaNativeError("直接交付来源的模块顺序发生变化。")


def retry_source(
    values: dict[str, Any], request: Any, session_factory: Any
) -> tuple[dict[str, Any], str] | None:
    if values.get("replication_method") != "exact" or values.get(
        "native_snapshot", {}
    ).get("model_protocol", {}).get("version") not in {
        "exact-compact-v3",
        "exact-compact-v4",
        "exact-compact-v5",
        "exact-compact-v6",
        "exact-compact-v7",
    }:
        return None
    with session_factory() as session:
        job = session.get(JobRecord, request.job_id)
        parent_id = job.retry_of_job_id if job else None
        if not parent_id or not any(
            e.get("from") == parent_id and e.get("to") == request.job_id
            for e in values.get("retry_events", [])
        ):
            return None
        parent = session.get(JobRecord, parent_id)
        if (
            not parent
            or parent.status != "failed"
            or job.workspace_id != request.workspace_id
            or parent.workspace_id != request.workspace_id
            or parent.configuration.get("replica_session_id")
            != job.configuration.get("replica_session_id")
            or parent.configuration.get("settings", {}).get("replica_plan_hash")
            != values.get("plan_hash")
        ):
            return None
        document_id = job.configuration["replica_session_id"]
    source = values.get("native_executions", {}).get(parent_id, {}).get("snapshot")
    if not source:
        raise ReplicaNativeError("原任务缺少持久生图检查点，不能重复生成代替恢复。")
    validate_source(
        source,
        values["native_snapshot"],
        f"{request.workspace_id}/replica/{document_id}/",
    )
    return source, parent_id


async def restore_source(
    run: Any, snapshot: dict[str, Any], source_job_id: str
) -> None:
    from .replica_exact_compact import run_class

    legacy_type = run_class("exact", snapshot["model_protocol"])
    legacy: Any = legacy_type(
        "exact",
        run.document_id,
        run.workspace_id,
        run.gateway,
        run.storage,
        root=run.root,
    )
    await legacy.restore(snapshot)  # Verifies every persisted JSON and image byte.
    run.context, run.checkpoints = (
        deepcopy(legacy.context),
        deepcopy(legacy.checkpoints),
    )
    run.run, run.snapshot_files = legacy.run, deepcopy(legacy.snapshot_files)
    run.usage, run.cost = deepcopy(legacy.usage), legacy.cost
    run.context["model_protocol"] = run.binding
    run._restored = True
    write_json(
        run.root / "direct-source.json",
        {
            "source_job_id": source_job_id,
            "source_protocol": snapshot["model_protocol"],
            "source_snapshot_sha256": canonical_hash(snapshot),
            "source_session_sha256": snapshot["files"]["session.json"]["sha256"],
            "target_protocol": run.binding,
        },
    )
    await run.checkpoint()


def inspect_artwork(
    run: Any, path: str, canvas: dict[str, int], sha256: str | None = None
) -> dict[str, Any]:
    from .replica_native_provider import ReplicaCanvasMismatch, _canvas_matches

    file = run.path(path)
    data = file.read_bytes()
    sha = digest(data)
    if sha256 is not None and sha != sha256:
        raise ReplicaNativeError("直接交付图片哈希发生变化。")
    with Image.open(file) as image:
        image.load()
        size = image.size
    expected = [canvas["width"], canvas["height"]]
    record: dict[str, Any] = {"artwork": str(file), "sha256": sha, "size": list(size)}
    if not _canvas_matches(expected, size, min(expected)):
        if not native_budget_matches(expected, size):
            raise ReplicaCanvasMismatch(expected, size)
        record["delivery_canvas"] = delivery_receipt(expected, size, sha)
    return record


def validate_direct_state(run: Any) -> None:
    state = read_json(run.root / "direct-session.json")
    original = read_json(run.run / "session.json")
    if (
        state.get("delivery_policy") != POLICY
        or state.get("review_status") != "NOT_REVIEWED"
        or any(
            state.get(k) != original.get(k) for k in ("plan_sha256", "plan", "input")
        )
    ):
        raise ReplicaNativeError("直接交付记录与原确认方案不一致。")
    modules, planned = state["modules"], state["plan"]["modules"]
    if len(modules) != len(planned):
        raise ReplicaNativeError("直接交付模块数量不完整。")
    for record, module in zip(modules, planned, strict=True):
        if (
            record.get("id") != module["id"]
            or record.get("review_status") != "NOT_REVIEWED"
        ):
            raise ReplicaNativeError("直接交付模块顺序或审查声明不一致。")
        if record.get("status") == "GENERATED":
            inspect_artwork(
                run, record["artwork"], module["output_canvas"], record["sha256"]
            )
        elif record.get("status") not in {"PENDING", "AWAITING_ARTWORK"}:
            raise ReplicaNativeError("直接交付包含不支持的模块状态。")


def activate(run: Any) -> None:
    path = run.root / "direct-session.json"
    if run.context.get("post_generation_review") != "disabled":
        original = read_json(run.run / "session.json")
        if not original.get("plan_audit") or original.get("state") in {
            "STARTED",
            "PLAN_REJECTED",
            "PLAN_AUDIT_REQUIRED",
        }:
            raise ReplicaNativeError("必须先完成生图前的方案检查。")
        ledger = (
            read_json(run.root / "invocations.json")
            if (run.root / "invocations.json").exists()
            else {}
        )
        if any(r.get("state") == "invoked" for r in ledger.values()):
            raise ReplicaNativeError("仍有未核实的生图请求，已停止自动重发。")
        modules = []
        for record, module in zip(
            original["modules"], original["plan"]["modules"], strict=True
        ):
            row = {
                "id": module["id"],
                "status": "PENDING",
                "review_status": "NOT_REVIEWED",
                "attempts": [],
            }
            # Retain the latest real canvas, independent of historical AI verdict.
            candidate = next(
                (a for a in reversed(record.get("attempts", [])) if a.get("artwork")),
                None,
            )
            attempts = record.get("attempts", [])
            returned = ledger.get(attempts[-1].get("token"), {}) if attempts else {}
            if returned.get("state") == "returned":
                if returned.get("target") != module["id"]:
                    raise ReplicaNativeError("已有生图请求与当前模块不一致。")
                candidate = {"artwork": returned["path"]}
            if candidate:
                file = contained(run.root, candidate["artwork"])
                relative = file.relative_to(run.root).as_posix()
                persisted = run.snapshot_files.get(relative, {})
                if not persisted.get("sha256"):
                    raise ReplicaNativeError("已有图片缺少持久哈希绑定。")
                row.update(
                    inspect_artwork(
                        run, str(file), module["output_canvas"], persisted["sha256"]
                    )
                )
                row["status"] = "GENERATED"
            modules.append(row)
        write_json(
            path,
            {
                "delivery_policy": POLICY,
                "review_status": "NOT_REVIEWED",
                "state": "READY",
                "module_index": 0,
                "plan_sha256": original["plan_sha256"],
                "plan": original["plan"],
                "input": original["input"],
                "modules": modules,
            },
        )
    run.direct_execution = True
    run.context["post_generation_review"] = "disabled"
    validate_direct_state(run)


def generation_request(run: Any, state: dict[str, Any], index: int) -> dict[str, Any]:
    module = state["plan"]["modules"][index]
    if (
        module["reference_unit_id"]
        != state["plan"]["sequence_contract"]["source_unit_order"][index]
    ):
        raise ReplicaNativeError("当前生图模块与确认的参考顺序不一致。")
    # Load only the manifest-verified, bundled native prompt compiler.
    spec = importlib.util.spec_from_file_location(
        "replica_direct_prompt", run.skill_root / "scripts/harness.py"
    )
    if spec is None or spec.loader is None:
        raise ReplicaNativeError("原生生图提示编译器不可用。")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    prompt = compiler.compile_prompt(state, module, False).replace(
        "before this exact page is bound and audited.",
        "before this exact page is bound.",
    )
    return {
        "token": str(uuid4()),
        "mode": "INITIAL",
        "module_id": module["id"],
        "prompt": prompt,
        "canvas": module["output_canvas"],
        "reference_images": [
            module["reference_path"],
            *state["input"]["product_images"],
        ],
    }


async def execute(provider: Any, run: Any, request: Any) -> list[str]:
    activate(run)
    await run.checkpoint()
    for index in range(len(run.state()["modules"])):
        state = run.state()
        state["module_index"] = index
        record, module = state["modules"][index], state["plan"]["modules"][index]
        if record["status"] == "GENERATED":
            continue
        native = record.get("request")
        if native is None:
            native = generation_request(run, state, index)
            record.update(
                status="AWAITING_ARTWORK",
                request=native,
                attempts=[{"token": native["token"]}],
            )
        state["state"] = "AWAITING_ARTWORK"
        write_json(run.root / "direct-session.json", state)
        await provider._save(run, request.job_id)
        image = await provider._render(
            run,
            request,
            native,
            native["prompt"],
            [Path(p) for p in native["reference_images"]],
            [native["canvas"]["width"], native["canvas"]["height"]],
            module["id"],
        )
        record.update(inspect_artwork(run, str(image), module["output_canvas"]))
        record["status"] = "GENERATED"
        write_json(run.root / "direct-session.json", state)
        await provider._save(run, request.job_id)
    validate_direct_state(run)
    state = run.state()
    state.update(state="DELIVERED", module_index=len(state["modules"]))
    write_json(run.root / "direct-session.json", state)
    write_json(
        run.root / "direct-delivery.json",
        {
            "delivery_policy": POLICY,
            "review_status": "NOT_REVIEWED",
            "plan_sha256": state["plan_sha256"],
            "modules": [
                {k: r[k] for k in ("id", "artwork", "sha256", "size", "review_status")}
                for r in state["modules"]
            ],
        },
    )
    return [r["artwork"] for r in state["modules"]]
