"""Explicit protocol revision and durable continuation of the latest plan round.

Old model verdicts keep their original protocol/request/response provenance.
They are never inserted into the new protocol's model-response cache.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .replica_exact_adaptation import blank_parallel_branch
from .replica_exact_contracts import canonical_hash
from .replica_layout import bind_module_text
from .replica_native import (
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    read_json,
    validate_module_constraints,
    write_json,
)

V2_BINDING = {
    "version": "exact-compact-v2",
    "content_sha256": (
        "67253dc1030edfa306ffa27998c0c8b831f338b731166c8375163cc8a924b7ea"
    ),
}


def review_scope(plan: dict[str, Any], uid: str, products: Any) -> dict[str, Any]:
    module = next(m for m in plan["modules"] if m["reference_unit_id"] == uid)
    return {
        **plan,
        "reference_units": [u for u in plan["reference_units"] if u["id"] == uid],
        "modules": [module],
        "_compact_scopes": {
            "modules": [
                s
                for s in plan["_compact_scopes"]["modules"]
                if s["module_id"] == module["id"]
            ]
        },
        "product_evidence": products,
    }


def review_subject(run: Any, scoped: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "module": scoped["modules"][0],
            "scope": scoped["_compact_scopes"]["modules"][0],
            "reference": scoped["reference_units"][0],
            "products": [
                s.sha256
                for s in run.sources([Path(p) for p in run.context["product_paths"]])
            ],
            "ledger": run.context["resolved_facts"],
            "product_evidence": run.context["product_evidence"],
        }
    )


async def import_revision(run: Any, snapshot: dict[str, Any]) -> None:
    from .replica_exact_compact import run_class

    legacy_type = run_class("exact", V2_BINDING)
    legacy: Any = legacy_type(
        "exact",
        run.document_id,
        run.workspace_id,
        run.gateway,
        run.storage,
        root=run.root,
    )
    if snapshot.get("model_protocol") != V2_BINDING or legacy.binding != V2_BINDING:
        raise ReplicaNativeError("修正规则与原协议不兼容，请保留原任务并重新诊断。")
    await legacy.restore(snapshot)  # Restores and verifies the durable checkpoint.
    state = legacy.state()
    if state["state"] != "PLAN_REJECTED":
        raise ReplicaNativeError("只能修正尚未生成图片且未通过审查的方案。")
    plan = state["plan"]
    ledger = legacy.context["resolved_facts"]
    if (
        canonical_hash({k: v for k, v in ledger.items() if k != "version"})
        != ledger["version"]
    ):
        raise ReplicaNativeError("原事实账本哈希不一致。")
    run.context = deepcopy(legacy.context)
    run.run, run.snapshot_files = legacy.run, deepcopy(legacy.snapshot_files)
    run.usage, run.cost = deepcopy(legacy.usage), legacy.cost
    imported, audits = {}, []
    module = __import__(legacy_type.__module__, fromlist=["response_schema"])
    prompts = module.PROMPTS
    system = "\n".join(
        (prompts / name).read_text(encoding="utf-8")
        for name in ("common.md", "visual_policy.md", "review_plan.md")
    )
    for unit in plan["reference_units"]:
        uid = unit["id"]
        scoped = review_scope(plan, uid, legacy.context["product_evidence"])
        selected = legacy._inputs("review_plan", scoped, None)
        schema = module.response_schema("review_plan")
        target = schema["properties"]["unit_audits"]
        target.update(minItems=1, maxItems=1)
        target["items"]["properties"]["unit_id"]["enum"] = [uid]
        matches = []
        for request_hash, record in legacy.checkpoints["results"].items():
            if record["phase"] != "review_plan":
                continue
            identity = canonical_hash(
                {
                    "protocol": V2_BINDING,
                    "phase": "review_plan",
                    "context": legacy._data("review_plan", scoped),
                    "observation_pass": None,
                    "schema": schema,
                    "system": system,
                    "planner": record["planner"],
                    "images": [s.sha256 for s in legacy.sources(selected)],
                    "image_order": legacy._image_order(selected, scoped),
                }
            )
            if identity == request_hash:
                if canonical_hash(record["raw"]) != record["response_sha256"]:
                    raise ReplicaNativeError("原模型审查记录哈希不一致。")
                from jsonschema import Draft202012Validator

                Draft202012Validator(schema).validate(record["raw"])
                actual = legacy._bind("review_plan", record["raw"], scoped)
                matches.append((request_hash, record, actual))
        if len(matches) != 1:
            raise ReplicaNativeError("原方案缺少唯一且可验证的模型审查，已停止迁移。")
        request_hash, record, actual = matches[0]
        row = actual["unit_audits"][0]
        audits.append(row)
        if row["decision"] == "PASS":
            proposal = module.plan_proposal(plan, uid)
            imported[uid] = {
                "parent_protocol": V2_BINDING,
                "request_sha256": request_hash,
                "response_sha256": record["response_sha256"],
                "raw": deepcopy(record["raw"]),
                "planner": deepcopy(record["planner"]),
                "subject_sha256": review_subject(run, scoped),
                "compatible": not blank_parallel_branch(
                    proposal, run.context["reference_layouts"][uid]
                ),
            }
    if not any(r["decision"] == "FAIL" for r in audits):
        raise ReplicaNativeError("原任务没有需要修正的失败项。")
    write_json(run.root / "legacy-v2-checkpoints.json", legacy.checkpoints)
    write_json(run.root / "legacy-v2-context.json", legacy.context)
    metrics = read_json(run.root / "compact-protocol-metrics.json")
    write_json(run.root / "legacy-v2-protocol-metrics.json", metrics)
    write_json(
        run.root / "compact-protocol-metrics.json",
        {
            "version": run.binding["version"],
            "calls": [
                {**row, "protocol": V2_BINDING["version"]} for row in metrics["calls"]
            ],
        },
    )
    run.context["model_protocol"] = run.binding
    run.context["revision_lineage"] = {
        "parent_protocol": V2_BINDING,
        "parent_snapshot_sha256": canonical_hash(snapshot),
        "parent_plan_sha256": canonical_hash(plan),
        "ledger_version": ledger["version"],
    }
    run.checkpoints = {
        "results": {},
        "planned": deepcopy(legacy.checkpoints["planned"]),
        "reviews": deepcopy(legacy.checkpoints["reviews"]),
        "total": len(plan["modules"]),
        "inherited_reviews": imported,
        "plan_cursor": {
            "rounds": 0,
            "phase": "revise",
            "plan": deepcopy(plan),
            "audit": {"method": "AI_FRESH_REFERENCE_AUDIT", "unit_audits": audits},
        },
    }
    run._restored = True
    await run.checkpoint()


def inherited_review(run: Any, scoped: dict[str, Any]) -> dict[str, Any] | None:
    uid = scoped["reference_units"][0]["id"]
    record = run.checkpoints.get("inherited_reviews", {}).get(uid)
    if record is None:
        return None
    parent = read_json(run.root / "legacy-v2-checkpoints.json")["results"].get(
        record["request_sha256"]
    )
    if (
        canonical_hash(record["raw"]) != record["response_sha256"]
        or record["parent_protocol"] != V2_BINDING
        or parent is None
        or parent["response_sha256"] != record["response_sha256"]
        or parent["raw"] != record["raw"]
        or parent["planner"] != record["planner"]
    ):
        raise ReplicaNativeError("保留的原始审查记录已变化。")
    if (
        not record["compatible"]
        or record["subject_sha256"] != review_subject(run, scoped)
        or record["planner"] != run._planner_identity()
    ):
        return None
    value = run._bind("review_plan", record["raw"], scoped)
    run.checkpoints["inherited_reviews_used"] = sorted(
        {*run.checkpoints.get("inherited_reviews_used", []), uid}
    )
    return value


async def resume_planning(run: Any) -> list[dict[str, Any]]:
    """Continue the latest round without replaying observation or earlier FAILs."""
    cursor = run.checkpoints["plan_cursor"]
    if cursor["rounds"] >= run.planning_revisions and cursor["phase"] != "accepted":
        raise ReplicaPlanAuditRejected(cursor["audit"])
    state = run.state()
    if state["state"] not in {
        "STARTED",
        "PLAN_REJECTED",
        "PLAN_AUDIT_REQUIRED",
        "READY",
    }:
        raise ReplicaNativeError("已有图片执行，不能重新开始规划。")
    session = run.root / "session.json"
    session.rename(run.root / "contracts" / f"previous-planning-{uuid4().hex}.json")
    run.cli("start", input=run.root / "input.json", session=session)
    units = sorted(
        read_json(run.root / "identity-segmentation.json")["units"],
        key=lambda u: (u["reference_index"], u["column_index"], u["screen_index"]),
    )
    plan = cursor["plan"]
    evidence = cursor.get("physical_units") or [
        {
            **u,
            "recommended_canvas": m["output_canvas"],
            "source_reference_index": physical["reference_index"],
        }
        for u, m, physical in zip(
            plan["reference_units"], plan["modules"], units, strict=True
        )
    ]
    while True:
        index = cursor["rounds"]
        plan = await run.contract(
            f"exact-plan-r{index}",
            "",
            {**run.context, "physical_units": evidence},
            "compile-plan",
            "plan",
        )
        try:
            await run.contract(
                f"exact-plan-audit-r{index}",
                "",
                {**plan, "product_evidence": run.context["product_evidence"]},
                "audit-plan",
                "judgment",
            )
            break
        except ReplicaPlanAuditRejected:
            if cursor["rounds"] >= run.planning_revisions:
                raise
    scoped = validate_module_constraints(
        plan["_compact_scopes"],
        {
            m["id"]: u["crop_sha256"]
            for m, u in zip(plan["modules"], evidence, strict=True)
        },
    )
    run.context["module_constraints"] = bind_module_text(
        scoped, plan["modules"], run.context["reference_layouts"]
    )
    await run.checkpoint()
    return [
        {
            "reference": run.context["references"][e["source_reference_index"]],
            "analysis": {"commercial_intent": m["page_duty"]},
            "prompt": m["generation_brief"],
            "unit_id": m["id"],
            "canvas": m["output_canvas"],
        }
        for e, m in zip(evidence, plan["modules"], strict=True)
    ]
