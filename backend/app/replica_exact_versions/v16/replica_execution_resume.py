"""Resume a verified image-execution checkpoint after the retired runtime budget.

The original plan and pixel verdicts keep their provenance. Nothing is replanned,
re-rendered or marked PASS by this compatibility boundary.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import JobItemRecord, JobRecord
from .replica_exact_contracts import canonical_hash
from .replica_native import ReplicaNativeError, contained, write_json
from .replica_retry import _document

BUDGET_ERROR = "REPLICA_ANALYSIS_BUDGET_REACHED"
V3_BINDING = {
    "version": "exact-compact-v3",
    "content_sha256": (
        "54a2aa4ac9a74fe8ce55806c042b38af5173db6bda7b78784ffe42e7b42e38f5"
    ),
}


def validate_checkpoint(
    previous: dict[str, Any], current: dict[str, Any], object_prefix: str
) -> None:
    if (
        any(
            previous.get(key) != current.get(key)
            for key in (
                "schema",
                "method",
                "skill_id",
                "source_hash",
                "root",
                "run",
                "model_protocol",
            )
        )
        or previous.get("model_protocol") != V3_BINDING
    ):
        raise ReplicaNativeError("续跑检查点与原方案版本不一致。")
    before, approved = (
        _document(previous, "session.json"),
        _document(current, "session.json"),
    )
    if approved.get("state") != "READY" or any(
        before.get(key) != approved.get(key)
        for key in ("runtime_version", "plan_sha256", "plan", "input")
    ):
        raise ReplicaNativeError("续跑检查点的方案或输入已变化。")
    contexts = [
        {
            k: v
            for k, v in _document(s, "context.json").items()
            if k != "analysis_progress"
        }
        for s in (previous, current)
    ]
    if contexts[0] != contexts[1]:
        raise ReplicaNativeError("续跑检查点的事实或素材绑定已变化。")
    for name, record in current["files"].items():
        if "object_key" in record and previous["files"].get(name) != record:
            raise ReplicaNativeError("续跑检查点的原始素材哈希已变化。")
    index = before.get("module_index")
    modules = before.get("modules", [])
    if (
        before.get("state") != "AWAITING_AUDIT"
        or type(index) is not int
        or not 0 <= index < len(modules)
        or len(modules) != len(approved["plan"]["modules"])
    ):
        raise ReplicaNativeError("预算中断检查点不在可恢复的审图阶段。")
    for i, (record, module) in enumerate(
        zip(modules, approved["plan"]["modules"], strict=True)
    ):
        if record.get("id") != module["id"]:
            raise ReplicaNativeError("续跑模块顺序不一致。")
        if i > index:
            if record.get("status") != "PENDING" or record.get("attempts"):
                raise ReplicaNativeError("续跑检查点包含越序处理。")
            continue
        attempts = record.get("attempts", [])
        if not attempts or not isinstance(attempts[-1].get("artwork"), str):
            raise ReplicaNativeError("续跑检查点缺少实际生图产物。")
        attempt = attempts[-1]
        relative = (
            contained(Path(previous["root"]), attempt["artwork"])
            .relative_to(Path(previous["root"]).resolve())
            .as_posix()
        )
        image = previous["files"].get(relative, {})
        if not image.get("sha256") or not str(image.get("object_key", "")).startswith(
            object_prefix
        ):
            raise ReplicaNativeError("续跑产物未绑定当前工作区。")
        if i < index:
            judgment = attempt.get("judgment") or {}
            if (
                record.get("status") != "COMPLETE"
                or judgment.get("derived_decision") != "PASS"
                or judgment.get("major_mismatches") != []
                or judgment.get("artwork_sha256") != image["sha256"]
            ):
                raise ReplicaNativeError("已完成模块缺少原始有效审图记录。")
        elif attempt.get("status") != "AWAITING_AUDIT" or attempt.get("judgment"):
            raise ReplicaNativeError("中断模块不能带有被忽略的审图判定。")


def budget_resume_source(
    values: dict[str, Any], request: Any, session_factory: Any
) -> tuple[dict[str, Any], str] | None:
    from sqlalchemy import select

    if values.get("replication_method") != "exact" or not values.get("retry_events"):
        return None
    with session_factory() as session:
        job = session.get(JobRecord, request.job_id)
        parent_id = job.retry_of_job_id if job else None
        if not parent_id or not any(
            event.get("from") == parent_id and event.get("to") == request.job_id
            for event in values.get("retry_events", [])
        ):
            return None
        parent = session.get(JobRecord, parent_id)
        if (
            not parent
            or parent.workspace_id != request.workspace_id
            or job.workspace_id != request.workspace_id
            or parent.status != "failed"
            or parent.configuration.get("replica_session_id")
            != job.configuration.get("replica_session_id")
            or parent.configuration.get("settings", {}).get("replica_plan_hash")
            != values.get("plan_hash")
        ):
            return None
        error = session.scalar(
            select(JobItemRecord.error_code)
            .where(JobItemRecord.job_id == parent_id, JobItemRecord.status == "failed")
            .order_by(JobItemRecord.ordinal)
            .limit(1)
        )
        if error != BUDGET_ERROR:
            return None
    previous = values.get("native_executions", {}).get(parent_id, {}).get("snapshot")
    if not previous:
        raise ReplicaNativeError("原任务缺少持久执行检查点，不能重复生图代替续跑。")
    validate_checkpoint(
        previous,
        values["native_snapshot"],
        f"{request.workspace_id}/replica/{job.configuration['replica_session_id']}/",
    )
    return previous, parent_id


async def restore_execution(
    run: Any, snapshot: dict[str, Any], source_job_id: str
) -> None:
    from .replica_exact_compact import run_class

    legacy_type = run_class("exact", V3_BINDING)
    legacy: Any = legacy_type(
        "exact",
        run.document_id,
        run.workspace_id,
        run.gateway,
        run.storage,
        root=run.root,
    )
    if legacy.binding != V3_BINDING or snapshot.get("model_protocol") != V3_BINDING:
        raise ReplicaNativeError("原执行协议不匹配，已停止续跑。")
    await legacy.restore(snapshot)  # Verifies every persisted JSON and image byte.
    run.context, run.checkpoints = (
        deepcopy(legacy.context),
        deepcopy(legacy.checkpoints),
    )
    run.run, run.snapshot_files = legacy.run, deepcopy(legacy.snapshot_files)
    run.usage, run.cost = deepcopy(legacy.usage), legacy.cost
    state = legacy.state()
    provenance = {
        "source_job_id": source_job_id,
        "source_protocol": V3_BINDING,
        "source_snapshot_sha256": canonical_hash(snapshot),
        "resume_module_index": state["module_index"],
        "preserved_modules": {
            m["id"]: canonical_hash(m)
            for m in state["modules"][: state["module_index"]]
        },
        "target_protocol": run.binding,
    }
    write_json(run.root / "execution-resume.json", provenance)
    run.context["model_protocol"] = run.binding
    run.checkpoints["execution_resume"] = provenance
    run._restored = True
    await run.checkpoint()
