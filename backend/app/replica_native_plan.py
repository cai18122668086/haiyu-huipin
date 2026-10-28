"""Retain the real final native plan; never replace absent plans with a prompt."""

import importlib.util
from copy import copy, deepcopy
from typing import Any

from .replica_analysis_recovery import document_file
from .replica_layout import bind_module_text
from .replica_native import (
    ReplicaAnalysisUnavailable,
    ReplicaNativeError,
    read_json,
    validate_module_constraints,
)


class ReplicaPlanIncomplete(ReplicaAnalysisUnavailable):
    code = "REPLICA_PLAN_INCOMPLETE"
    message = (
        "原生详细方案尚未完整生成，不能改用空方案生图；已有分析、素材和图片已保留。"
    )


def full_plan(state: dict[str, Any]) -> bool:
    plan = state.get("plan") or {}
    modules, refs = plan.get("modules", []), plan.get("reference_units", [])
    required = {
        "id",
        "reference_unit_id",
        "visual_contract",
        "page_duty",
        "buyer_conclusion",
        "generation_brief",
        "copy_units",
        "output_canvas",
    }
    return bool(
        state.get("input")
        and modules
        and len(modules) == len(refs)
        and all(required <= module.keys() for module in modules)
        and [m["reference_unit_id"] for m in modules] == [r["id"] for r in refs]
    )


def snapshot_has_full_plan(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("method") == "style":
        from .replica_style_delivery import workflow_name

        try:
            workflow = workflow_name(snapshot, "workflow.json")
        except (KeyError, TypeError, ValueError, ReplicaNativeError):
            return False
        return style_completion_ready(
            document_file(snapshot, workflow),
            document_file(snapshot, "style-analysis-checkpoints.json").get(
                "cursor", {}
            ),
        )
    return full_plan(document_file(snapshot, "session.json"))


def snapshot_completion_ready(snapshot: dict[str, Any]) -> bool:
    if not snapshot_has_full_plan(snapshot):
        return False
    if snapshot.get("method") == "style":
        return True
    state = document_file(snapshot, "session.json")
    cursor = document_file(snapshot, "compact-checkpoints.json").get("plan_cursor", {})
    return state.get("state") == "READY" or cursor.get("rounds", 0) >= 3


def style_completion_ready(state: dict[str, Any], cursor: dict[str, Any]) -> bool:
    """Compiled detailed units and exhausted real rounds, never crop stubs."""
    ids = state.get("layout", {}).get("unit_ids", [])
    units = state.get("units", {})
    jobs = state.get("layout", {}).get("jobs", [])
    return bool(
        cursor.get("phase") in {"ready", "rejected"}
        and (cursor.get("phase") == "ready" or cursor.get("revision", 0) >= 3)
        and ids
        and list(units) == ids
        and [uid for job in jobs for uid in job.get("unit_ids", [])] == ids
        and all(
            isinstance(units[uid], dict)
            and units[uid].get("primary_duty")
            and units[uid].get("source_content_step_ids")
            and units[uid].get("mechanism_ids")
            and units[uid].get("semantic_translation")
            for uid in ids
        )
        and state.get("product_visual_facts")
        and state.get("reference_style_mechanisms")
        and state.get("semantic_translation_map")
    )


def completion_ready(run: Any, state: dict[str, Any] | None = None) -> bool:
    if run.method == "style":
        return style_completion_ready(
            run.state(), getattr(run, "journal", {}).get("cursor", {})
        )
    native = state if state is not None else run.state()
    cursor = getattr(run, "checkpoints", {}).get("plan_cursor", {})
    return full_plan(native) and (
        native.get("state") == "READY" or cursor.get("rounds", 0) >= 3
    )


def retain_native_plan(run: Any) -> tuple[dict[str, Any], list[str], int]:
    path = run.root / "session.json"
    state = read_json(path) if path.exists() else {}
    if not full_plan(state):
        raise ReplicaPlanIncomplete()
    cursor = getattr(run, "checkpoints", {}).get("plan_cursor", {})
    rounds = cursor.get("rounds", run.planning_revisions)
    if state.get("state") != "READY" and rounds < run.planning_revisions:
        raise ReplicaPlanIncomplete()
    plan = state["plan"]
    if plan.get("_compact_scopes"):
        scoped = validate_module_constraints(
            plan["_compact_scopes"],
            {
                m["id"]: r["crop_sha256"]
                for m, r in zip(plan["modules"], plan["reference_units"], strict=True)
            },
        )
        scoped = bind_module_text(
            scoped, plan["modules"], run.context["reference_layouts"]
        )
        run.context["module_constraints"] = scoped
        adapt = getattr(run, "model_module_evidence", lambda value: value)
        run.context["_completion_module_evidence"] = {
            mid: adapt(value) for mid, value in scoped.items()
        }
    run.context["_completion_render_binding_version"] = getattr(
        run, "render_binding_version", None
    )
    run.context["_completion_generation_policy"] = run.generation_policy()
    spec = importlib.util.spec_from_file_location(
        "retained_native_replica_prompt", run.skill_root / "scripts/harness.py"
    )
    if spec is None or spec.loader is None:
        raise ReplicaPlanIncomplete()
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    prompts = []
    for index, module in enumerate(plan["modules"]):
        renderer = copy(run)
        original = {**deepcopy(state), "module_index": index}
        renderer.state = lambda value=original: value
        raw = compiler.compile_prompt(state, module, False).replace(
            "before this exact page is bound and audited.",
            "before this exact page is bound.",
        )
        prompts.append(renderer.image_prompt(raw))
    # No state transition, synthetic PASS, or change to the rejected session.
    return state, prompts, rounds
