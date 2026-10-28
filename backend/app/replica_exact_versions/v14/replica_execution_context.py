"""Durable exact-plan execution bindings, reconstructed only from the native plan."""

from copy import deepcopy
from typing import Any

from .replica_exact_contracts import canonical_hash
from .replica_native import (
    ReplicaAnalysisUnavailable,
    ReplicaNativeError,
    read_json,
    write_json,
)

POLICY = "native-plan-execution-context-v1"
FIELDS = (
    "module_constraints",
    "_completion_module_evidence",
    "_completion_render_binding_version",
    "_completion_generation_policy",
)


class ExecutionContextInvalid(ReplicaAnalysisUnavailable):
    code = "REPLICA_EXECUTION_CONTEXT_INVALID"

    def __init__(self, reason: str) -> None:
        self.message = "完整原生方案的执行绑定未通过校验：" + reason
        ReplicaNativeError.__init__(self, self.message)


def required(run: Any) -> bool:
    return (getattr(run, "binding", None) or {}).get("version") in {
        "exact-compact-v13", "exact-compact-v14"
    }


def binding(run: Any) -> dict[str, Any]:
    from .replica_native_plan import full_plan

    native = read_json(run.root / "session.json")
    if not full_plan(native) or any(key not in run.context for key in FIELDS):
        raise ExecutionContextInvalid("缺少完整方案或持久化构图、文字槽约束。")
    ids = [m["id"] for m in native["plan"]["modules"]]
    if any(set(run.context[key]) != set(ids) for key in FIELDS[:2]):
        raise ExecutionContextInvalid("执行约束没有覆盖全部原生模块。")
    return {
        "policy": POLICY,
        "native_plan_sha256": canonical_hash(native["plan"]),
        "context_sha256": canonical_hash({key: run.context[key] for key in FIELDS}),
        "module_ids": ids,
        "model_protocol": run.binding,
    }


def persist(run: Any, plan: dict[str, Any]) -> None:
    plan["execution_context_binding"] = binding(run)
    write_json(run.root / "context.json", run.context)


def restore(run: Any, plan: dict[str, Any]) -> None:
    from .replica_exact_compact import run_class
    from .replica_native_plan import full_plan, retain_native_plan

    native = read_json(run.root / "session.json")
    if (
        not full_plan(native)
        or not plan.get("native_plan_retained")
        or [u.get("proposal") for u in plan["units"]] != native["plan"]["modules"]
    ):
        raise ExecutionContextInvalid("生成单元与最后完整原生方案不一致。")
    compiler: Any = run_class("exact", run.binding)(
        "exact",
        run.document_id,
        run.workspace_id,
        run.gateway,
        run.storage,
        root=run.root,
    )
    compiler.context = deepcopy(run.context)
    compiler.checkpoints = read_json(run.root / "compact-checkpoints.json")
    _, prompts, rounds = retain_native_plan(compiler)
    if [u.get("native_prompt") for u in plan["units"]] != prompts or plan[
        "completed_planning_rounds"
    ] != rounds:
        raise ExecutionContextInvalid("原生提示词或规划轮数发生变化。")
    expected = binding(compiler)
    if plan.get("execution_context_binding") != expected:
        raise ExecutionContextInvalid("执行上下文哈希与原生方案绑定不一致。")
    # Recompute the derived scope after every cold restore. No model calls,
    # plan rewrites, resampling, or fabricated visual verdicts are involved.
    run.context = compiler.context
    write_json(run.root / "context.json", run.context)
    write_json(run.root / "execution-context-binding.json", expected)
