"""Bounded analysis followed by delivery, including honest best-effort plans.

The original Skill checkpoints and failed judgments are never rewritten. This
application policy authorizes generation from available evidence after analysis
has exhausted its corrections; it does not manufacture a passing audit.
"""

from __future__ import annotations

import json
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .models import JobRecord
from .provider import ProviderArtifact, ProviderOutcome, ProviderResult
from .replica_exact_contracts import canonical_hash
from .replica_native import (
    NativeReplicaRun,
    ReplicaNativeError,
    digest,
    native_accounting,
    read_json,
    write_json,
)
from .replica_native_provider import ReplicaImageOutcomeUnknown, _canvas_matches

POLICY = "replica-bounded-analysis-generate-v1"
PLAN = "completion-plan.json"
STATE = "completion-session.json"


class CompleteExistingAnalysis(ReplicaNativeError):
    def __init__(self, code: str) -> None:
        super().__init__("按已保存的分析结果自动生成")
        self.code = code


def available(snapshot: dict[str, Any]) -> bool:
    return PLAN in snapshot.get("files", {})


def _originals(run: NativeReplicaRun, payload: dict, records: dict) -> list[str]:
    paths = []
    for role, ids in (
        ("product", payload["product_asset_ids"]),
        ("reference", [r["asset_id"] for r in payload["references"]]),
    ):
        for i, aid in enumerate(ids):
            path = run.path(str(run.root / "inputs" / f"{role}-{i:02}.img"))
            if digest(path.read_bytes()) != records[aid][2]:
                raise ReplicaNativeError("原始素材哈希发生变化，不能继续生成。")
            paths.append(str(path))
    return paths


def _units(run: NativeReplicaRun, payload: dict) -> list[dict[str, Any]]:
    """Prefer the last complete plan, then physical segmentation, then sources."""
    if run.method == "exact":
        state = (
            read_json(run.root / "session.json")
            if (run.root / "session.json").exists()
            else {}
        )
        plan = state.get("plan") or {}
        refs = {u["id"]: u for u in plan.get("reference_units", [])}
        if plan.get("modules"):
            physical = (
                getattr(run, "checkpoints", {})
                .get("plan_cursor", {})
                .get("physical_units", [])
            )
            source_indexes = {u["id"]: u["source_reference_index"] for u in physical}
            if (
                not source_indexes
                and (run.root / "identity-segmentation.json").exists()
            ):
                observed = read_json(run.root / "identity-segmentation.json").get(
                    "units", []
                )
                source_indexes = {
                    uid: u["reference_index"]
                    for uid, u in zip(refs, observed, strict=True)
                }
            for ref in refs.values():
                if (
                    digest(run.path(ref["crop_path"]).read_bytes())
                    != ref["crop_sha256"]
                ):
                    raise ReplicaNativeError("参考图裁片哈希发生变化。")
            return [
                {
                    "id": m["id"],
                    "reference_index": source_indexes[m["reference_unit_id"]],
                    "reference_path": refs[m["reference_unit_id"]]["crop_path"],
                    "canvas": [
                        m["output_canvas"]["width"],
                        m["output_canvas"]["height"],
                    ],
                    "proposal": deepcopy(m),
                }
                for m in plan["modules"]
            ]
    cursor = getattr(run, "journal", {}).get("cursor", {})
    observation = (
        read_json(run.root / "identity-segmentation.json")
        if (run.root / "identity-segmentation.json").exists()
        else {}
    )
    physical = cursor.get("units") or observation.get("units") or []
    workflow = run.state() if run.method == "style" else {}
    planned = list(workflow.get("units", {}).values())
    result = []
    targets = planned if planned and physical else physical
    for i, target in enumerate(targets):
        unit = physical[min(len(physical) - 1, i * len(physical) // len(targets))]
        index = unit["reference_index"]
        source = run.path(run.context["reference_paths"][index])
        crop = run.crop(
            source,
            unit["bbox"],
            run.root / "completion-references" / f"screen-{i:03}.png",
        )
        with Image.open(crop) as image:
            width, height = image.size
        result.append(
            {
                "id": f"M{i + 1:03}",
                "reference_index": index,
                "reference_path": str(crop),
                "canvas": [
                    round(width * max(1024 / width, 1024 / height)),
                    round(height * max(1024 / width, 1024 / height)),
                ],
                "proposal": deepcopy(target) if planned else {},
            }
        )
    if result:
        return result
    # Missing model segmentation is not a request for the user to resubmit.
    # A conservative server split covers every source pixel in source order.
    for index, path in enumerate(run.context["reference_paths"]):
        with Image.open(run.path(path)) as image:
            width, height = image.size
        step = round(width * 4 / 3) if height > width * 2.5 else height
        for top in range(0, height, step):
            bottom = min(height, top + step)
            crop = run.crop(
                Path(path),
                [0, top, width, bottom],
                run.root / "completion-references" / f"screen-{len(result):03}.png",
            )
            result.append(
                {
                    "id": f"M{len(result) + 1:03}",
                    "reference_index": index,
                    "reference_path": str(crop),
                    "canvas": [1024, max(1, round(1024 * (bottom - top) / width))],
                    "proposal": {},
                }
            )
    return result


async def prepare(
    run: NativeReplicaRun, payload: dict, records: dict, error: Exception
) -> list[dict]:
    originals = _originals(run, payload, records)
    native_prompts: list[str] = []
    native_rounds = 0
    if run.method == "style":
        from .replica_native_plan import ReplicaPlanIncomplete, completion_ready

        if not completion_ready(run):
            raise ReplicaPlanIncomplete()
        native_rounds = getattr(run, "journal", {}).get("cursor", {}).get("revision", 0)
    if run.method == "exact":
        from .replica_native_plan import retain_native_plan

        _, native_prompts, native_rounds = retain_native_plan(run)
    if (run.root / "invocations.json").exists() and read_json(
        run.root / "invocations.json"
    ):
        raise ReplicaNativeError("已有生图请求，不能用分析兜底替换原任务。")
    units = _units(run, payload)
    if not units or len(units) > 60:
        raise ReplicaNativeError("没有可执行的参考图单元。")
    for index, unit in enumerate(units):
        if native_prompts:
            unit["native_prompt"] = native_prompts[index]
        path = run.path(unit["reference_path"])
        unit["reference_sha256"] = digest(path.read_bytes())
        # Style detail pages retain the established 3:4 per-screen delivery.
        if (
            run.method == "style"
            and getattr(run, "journal", {})
            .get("cursor", {})
            .get("observation", {})
            .get("route")
            == "DETAIL_PAGE"
        ):
            unit["canvas"] = [1536, 2048]
    plan = {
        "policy": POLICY,
        "document_id": run.document_id,
        "workspace_id": run.workspace_id,
        "method": run.method,
        "planning_status": "BEST_EFFORT",
        "completed_planning_rounds": native_rounds,
        "native_plan_retained": bool(native_prompts),
        "reason_code": getattr(error, "code", "REPLICA_ANALYSIS_FAILED"),
        "last_judgment": deepcopy(
            getattr(error, "judgment", None)
            or getattr(run, "journal", {}).get("cursor", {}).get("prior_audit")
            or getattr(run, "checkpoints", {}).get("plan_cursor", {}).get("audit")
        ),
        "source_inputs": [
            {"path": p, "sha256": digest(Path(p).read_bytes())} for p in originals
        ],
        "product_paths": originals[: len(payload["product_asset_ids"])],
        "facts": payload["facts"],
        "language": payload["language"],
        "platform": payload["platform_code"],
        "units": units,
        "style_observations": deepcopy(
            getattr(run, "journal", {})
            .get("cursor", {})
            .get("analysis", {})
            .get("mechanisms", {})
        ),
    }
    from .replica_execution_context import persist, required

    if run.method == "exact" and required(run):
        persist(run, plan)
    write_json(run.root / PLAN, plan)
    write_json(
        run.root / STATE,
        {
            "policy": POLICY,
            "plan_sha256": canonical_hash(plan),
            "state": "READY",
            "module_index": 0,
            "review_status": "NOT_REVIEWED",
            "modules": [
                {
                    "id": u["id"],
                    "status": "PENDING",
                    "review_status": "NOT_REVIEWED",
                    "attempts": [],
                }
                for u in units
            ],
        },
    )
    return [
        {
            "reference": payload["references"][u["reference_index"]],
            "unit_id": u["id"],
            "canvas": {"width": u["canvas"][0], "height": u["canvas"][1]},
            "analysis": {"commercial_intent": "按现有方案生成"},
            "prompt": "按现有方案自动生成",
        }
        for u in units
    ]


class CompletionRun(NativeReplicaRun):
    direct_execution = True
    source_metadata: dict[str, Any]
    binding: dict[str, Any] | None

    async def restore(self, snapshot: dict[str, Any]) -> None:
        prefix = f"{self.workspace_id}/replica/{self.document_id}/"
        if snapshot.get("method") != self.method or any(
            "object_key" in r and not r["object_key"].startswith(prefix)
            for r in snapshot.get("files", {}).values()
        ):
            raise ReplicaNativeError("自动生成检查点不属于当前工作区和任务。")
        await super().restore(snapshot)
        self.legacy_native_prompts: dict[str, str] = {}
        completion = read_json(self.root / PLAN)
        from .replica_execution_context import required, restore

        if self.method == "exact" and required(self):
            restore(self, completion)
        elif (
            self.method == "exact"
            and any(not u.get("native_prompt") for u in completion["units"])
            and all(u.get("proposal") for u in completion["units"])
        ):
            from .replica_exact_compact import run_class
            from .replica_native_plan import full_plan, retain_native_plan

            state_path = self.root / "session.json"
            original = read_json(state_path) if state_path.exists() else {}
            if (
                full_plan(original)
                and [u["proposal"] for u in completion["units"]]
                == original["plan"]["modules"]
            ):
                compiler = run_class("exact", self.binding)(
                    "exact",
                    self.document_id,
                    self.workspace_id,
                    self.gateway,
                    self.storage,
                    root=self.root,
                )
                compiler.context = deepcopy(self.context)
                compiler.run = self.run
                if hasattr(compiler, "checkpoints"):
                    compiler.checkpoints = read_json(
                        self.root / "compact-checkpoints.json"
                    )
                _, prompts, _ = retain_native_plan(compiler)
                self.context = compiler.context
                self.legacy_native_prompts = {
                    unit["id"]: text
                    for unit, text in zip(completion["units"], prompts, strict=True)
                }
        self.render_binding_version = self.context.get(
            "_completion_render_binding_version"
        )

    def model_module_evidence(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.context.get("_completion_module_evidence", {}).get(
            value["module_id"], value
        )

    def generation_policy(self) -> str:
        return self.context.get(
            "_completion_generation_policy", super().generation_policy()
        )

    def state(self) -> dict[str, Any]:
        return read_json(self.root / STATE)

    async def snapshot(self) -> dict[str, Any]:
        value = await super().snapshot()
        for key in ("model_protocol", "analysis_policy"):
            if key in self.source_metadata:
                value[key] = self.source_metadata[key]
        return value


def validate(run: CompletionRun) -> dict[str, Any]:
    plan, state = read_json(run.root / PLAN), run.state()
    if (
        plan.get("policy") != POLICY
        or state.get("policy") != POLICY
        or plan.get("document_id") != run.document_id
        or plan.get("workspace_id") != run.workspace_id
        or plan.get("method") != run.method
        or state.get("plan_sha256") != canonical_hash(plan)
        or [u["id"] for u in plan["units"]] != [m["id"] for m in state["modules"]]
    ):
        raise ReplicaNativeError("自动生成方案的归属、顺序或哈希发生变化。")
    for source in [
        *plan["source_inputs"],
        *[
            {"path": u["reference_path"], "sha256": u["reference_sha256"]}
            for u in plan["units"]
        ],
    ]:
        if digest(run.path(source["path"]).read_bytes()) != source["sha256"]:
            raise ReplicaNativeError("自动生成素材的原始字节发生变化。")
    return plan


def prompt(plan: dict, unit: dict, index: int) -> str:
    if not unit.get("proposal"):
        from .replica_native_plan import ReplicaPlanIncomplete

        raise ReplicaPlanIncomplete()
    if plan["method"] == "exact":
        from .replica_native_plan import ReplicaPlanIncomplete

        if not unit.get("native_prompt"):
            # Old simplified results stay downloadable, but missing screens
            # require a real native plan rather than another generic image.
            raise ReplicaPlanIncomplete()
        return unit["native_prompt"]
    return (
        "Generate the final ecommerce image using the available plan. This is screen "
        f"{index + 1} of {len(plan['units'])}; render only this screen. "
        "Preserve actual product geometry, packaging and original physical marks. "
        "Use explicit user facts and readable product facts; omit unsupported claims. "
        "Render new page copy in the target language. No local text overlays. "
        + (
            "Reference image 1 supplies composition, never product identity. "
            if plan["method"] == "exact"
            else "Only product images are attached. Use the style observations below. "
            "Do not reproduce source brands, products, slogans or literal scenes. "
        )
        + "Keep a coherent campaign. Use this best-effort plan to generate the image.\n"
        + json.dumps(
            {
                "language": plan["language"],
                "platform": plan["platform"],
                "user_facts": plan["facts"],
                "current_screen": unit["proposal"],
                "style_observations": plan["style_observations"],
            },
            ensure_ascii=False,
        )
    )


async def execute(
    provider: Any, request: Any, values: dict, document_id: str, ordinal: int
) -> ProviderResult:
    execution = values.get("native_executions", {}).get(request.job_id, {})
    snapshot = execution.get("snapshot") or values["native_snapshot"]
    if not execution:
        with provider.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            parent = (
                session.get(JobRecord, job.retry_of_job_id)
                if job and job.retry_of_job_id
                else None
            )
            if parent is not None:
                if (
                    parent.workspace_id != request.workspace_id
                    or parent.status not in {"failed", "partial"}
                    or parent.configuration.get("replica_session_id") != document_id
                    or parent.configuration.get("settings", {}).get("replica_plan_hash")
                    != values["plan_hash"]
                    or not any(
                        e.get("from") == parent.id and e.get("to") == request.job_id
                        for e in values.get("retry_events", [])
                    )
                ):
                    raise ReplicaNativeError("自动生成接续来源与当前任务不一致。")
                snapshot = (
                    values.get("native_executions", {})
                    .get(parent.id, {})
                    .get("snapshot")
                )
                if (
                    not snapshot
                    or snapshot["files"].get(PLAN)
                    != values["native_snapshot"]["files"][PLAN]
                ):
                    raise ReplicaNativeError("自动生成接续缺少一致的持久方案。")
    run = CompletionRun(
        values["replication_method"],
        document_id,
        request.workspace_id,
        provider.gateway,
        provider.storage,
        root=Path(snapshot["root"]),
    )
    run.source_metadata = snapshot
    run.binding = snapshot.get("model_protocol")
    initial = len(snapshot.get("usage", []))
    restored = False
    try:
        await run.restore(snapshot)
        restored = True
        plan = validate(run)
        state = run.state()
        # Each durable JobItem owns one screen. Earlier screens are already
        # delivered by the executor; a later failure cannot hide their assets.
        for i in [ordinal]:
            unit, module = plan["units"][i], state["modules"][i]
            state["module_index"] = i
            if module["status"] != "GENERATED":
                ledger = (
                    read_json(run.root / "invocations.json")
                    if (run.root / "invocations.json").exists()
                    else {}
                )
                previous = ledger.get(module.get("request", {}).get("token"), {})
                if (
                    previous.get("state") == "rejected"
                    and previous.get("job_id") != request.job_id
                ):
                    module.setdefault("previous_requests", []).append(
                        module.pop("request")
                    )
                native = module.setdefault(
                    "request",
                    {"token": uuid4().hex, "revision_index": 0, "mode": "INITIAL"},
                )
                module.update(
                    status="AWAITING_ARTWORK", attempts=[{"token": native["token"]}]
                )
                state["state"] = "AWAITING_ARTWORK"
                write_json(run.root / STATE, state)
                await provider._save(run, request.job_id)
                paths = (
                    [run.path(unit["reference_path"])] if run.method == "exact" else []
                ) + [run.path(p) for p in plan["product_paths"]]
                image = await provider._render(
                    run,
                    request,
                    native,
                    prompt(
                        plan,
                        {
                            **unit,
                            "native_prompt": unit.get("native_prompt")
                            or run.legacy_native_prompts.get(unit["id"]),
                        },
                        i,
                    ),
                    paths,
                    unit["canvas"],
                    unit["id"],
                )
                module.update(
                    status="GENERATED",
                    artwork=str(image),
                    sha256=digest(image.read_bytes()),
                )
                write_json(run.root / STATE, state)
                await provider._save(run, request.job_id)
            data = run.path(module["artwork"]).read_bytes()
            with Image.open(run.path(module["artwork"])) as image:
                image.load()
                if digest(data) != module["sha256"] or not _canvas_matches(
                    unit["canvas"], image.size
                ):
                    raise ReplicaNativeError("生成图片的字节或画幅不一致。")
        complete = all(m["status"] == "GENERATED" for m in state["modules"])
        state.update(
            state="DELIVERED" if complete else "READY", module_index=ordinal + 1
        )
        write_json(run.root / STATE, state)
        await provider._save(run, request.job_id)
        data = run.path(state["modules"][ordinal]["artwork"]).read_bytes()
        with Image.open(run.path(state["modules"][ordinal]["artwork"])) as image:
            width, height = image.size
            content_type = Image.MIME[image.format or "PNG"]
            suffix = {"image/jpeg": "jpg", "image/webp": "webp"}.get(
                content_type, "png"
            )
        accounting = native_accounting(run.usage[initial:])
        return ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id=f"replica:{request.job_id}:{ordinal}",
            usage_reported=accounting["reported"],
            actual_cost_micros=accounting["actual_cost_micros"],
            estimated_cost_micros=accounting["estimated_cost_micros"],
            raw_usage={
                **accounting,
                "replication_method": run.method,
                "delivery_policy": POLICY,
                "planning_status": "BEST_EFFORT",
                "post_generation_review": "disabled",
                "native_visual_audited": False,
                "native_state": state["state"],
                "native_calls": run.usage[initial:],
            },
            artifact=ProviderArtifact(
                payload=data,
                filename=f"replica-{ordinal + 1:03}.{suffix}",
                content_type=content_type,
                width=width,
                height=height,
                sha256=digest(data),
            ),
            provider_name=provider.gateway.provider_name,
            model_name=provider.gateway.image_model,
            pricing_version=provider.gateway.pricing_version,
        )
    except Exception as error:
        # Same invocation fence as the normal renderer; unknown calls are never
        # reissued by automatic best-effort completion.
        ledger = (
            read_json(run.root / "invocations.json")
            if restored and (run.root / "invocations.json").exists()
            else {}
        )
        pending_tokens = [
            token for token, row in ledger.items() if row.get("state") == "invoked"
        ]
        unknown = isinstance(error, ReplicaImageOutcomeUnknown) or bool(pending_tokens)
        if unknown and not any(u.get("kind") == "image" for u in run.usage[initial:]):
            known_tokens = {u.get("invocation_token") for u in run.usage}
            for token in pending_tokens:
                if token not in known_tokens:
                    run.usage.append(
                        {
                            "kind": "image",
                            "network_requests": 1,
                            "reported": False,
                            "actual_cost_micros": None,
                            "estimated_cost_micros": None,
                            "invocation_token": token,
                        }
                    )
        if restored and (run.root / STATE).exists():
            with suppress(Exception):
                await provider._save(run, request.job_id)
        accounting = native_accounting(run.usage[initial:])
        from .replica_failure import failure_fields
        from .replica_image_recovery import retryable as can_resume_original

        fields = failure_fields(error, run.usage[initial:], ordinal)
        metrics = fields.pop("raw_usage_metrics")
        return ProviderResult(
            outcome=ProviderOutcome.OUTCOME_UNKNOWN
            if unknown
            else ProviderOutcome.FAILED,
            **fields,
            retryable=can_resume_original(run, error) if restored else False,
            usage_reported=accounting["reported"],
            actual_cost_micros=accounting["actual_cost_micros"],
            estimated_cost_micros=accounting["estimated_cost_micros"],
            raw_usage={
                **accounting,
                "native_calls": run.usage[initial:],
                "metrics": metrics,
            },
        )
