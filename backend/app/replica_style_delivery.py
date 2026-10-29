"""Style-only direct delivery, retaining the native pre-generation compiler.

The original workflow, plan and AI verdicts are immutable history. A separate
generation workspace uses GENERATED markers, never fabricated audit passes.
"""

from __future__ import annotations

import asyncio
import shutil
from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .models import JobRecord, ReplicaDocumentRecord, utc_now
from .native_portrait_delivery import (
    STYLE_NATIVE_POLICY,
    style_native_budget_matches,
)
from .provider import ProviderOutcome, ProviderRequest, ProviderResult
from .replica_canvas import delivery_receipt
from .replica_native import (
    NativeReplicaRun,
    ReplicaNativeError,
    digest,
    read_json,
    write_json,
)
from .replica_native_provider import (
    NativeReplicaProvider,
    ReplicaCanvasMismatch,
    ReplicaImageOutcomeUnknown,
)
from .replica_progress import native_progress
from .replica_retry import RetryArtwork, _document

POLICY = "style-approved-plan-direct-delivery-v1"
STATE_FILE = "style-direct-session.json"
RUNTIME_FIELDS = {
    "status",
    "revision_index",
    "generation_token",
    "generated_at",
    "artwork",
    "render_provenance",
    "accepted_at",
    "passport_frozen",
}


class StyleDeliveryInvalid(ReplicaNativeError):
    code = "REPLICA_DELIVERY_INVALID"
    message = "生成文件无法完成交付完整性检查，原始产物与进度已保留。"


def workflow_name(snapshot: dict[str, Any], name: str) -> str:
    root = Path(snapshot["root"]).resolve()
    try:
        return (
            Path(snapshot["run"]).resolve().relative_to(root) / "artifacts" / name
        ).as_posix()
    except ValueError as exc:
        raise ReplicaNativeError("风格复刻来源目录不属于当前任务。") from exc


def plan_identity(state: dict[str, Any]) -> dict[str, Any]:
    """Exclude only native execution fields and adopted pixel dimensions."""
    value = deepcopy(state)
    for key in (
        "state",
        "active_token",
        "artwork",
        "audits",
        "receipts",
        "global_audit",
        "revision_index",
        "detail_ratio",
    ):
        value.pop(key, None)
    for pool in ("units", "groups"):
        for row in value.get(pool, {}).values():
            for key in RUNTIME_FIELDS:
                row.pop(key, None)
    for contract in (
        value.get("layout", {}),
        value.get("route_specific_composition_plan", {}).get("contract", {}),
    ):
        for job in contract.get("jobs", []):
            job.pop("canvas", None)
    return value


def require_plan(state: dict[str, Any]) -> None:
    audit = state.get("plan_audit", {})
    layout = state.get("layout", {})
    if (
        audit.get("pass") is not True
        or audit.get("route") != state.get("route")
        or audit.get("unit_ids") != layout.get("unit_ids")
        or not layout.get("jobs")
        or [u for job in layout["jobs"] for u in job["unit_ids"]] != layout["unit_ids"]
    ):
        raise ReplicaNativeError("必须保留生图前通过检查的完整风格方案与顺序。")


def validate_source(
    previous: dict[str, Any], approved: dict[str, Any], prefix: str
) -> None:
    if previous.get("method") != "style" or any(
        previous.get(k) != approved.get(k)
        for k in (
            "schema",
            "method",
            "skill_id",
            "source_hash",
            "root",
            "run",
            "model_protocol",
        )
    ):
        raise ReplicaNativeError("风格复刻来源版本或归属不一致。")
    name = workflow_name(approved, "workflow.json")
    before, plan = _document(previous, name), _document(approved, name)
    require_plan(plan)
    if plan_identity(before) != plan_identity(plan):
        raise ReplicaNativeError("风格复刻来源的方案、事实或参考绑定发生变化。")
    for name in ("context.json", workflow_name(approved, "route-plan.json")):
        if _document(previous, name) != _document(approved, name):
            raise ReplicaNativeError("风格复刻来源的确认方案、素材或事实不一致。")
    for name, record in approved["files"].items():
        if "object_key" in record and previous["files"].get(name) != record:
            raise ReplicaNativeError("原始素材的持久哈希绑定发生变化。")
    if any(
        "object_key" in row and not row["object_key"].startswith(prefix)
        for row in previous["files"].values()
    ):
        raise ReplicaNativeError("风格复刻产物未绑定当前工作区和任务。")
    if "invocations.json" in previous["files"] and any(
        row.get("state") == "invoked"
        for row in _document(previous, "invocations.json").values()
    ):
        raise ReplicaImageOutcomeUnknown("存在结果尚未核实的生图请求，禁止自动重发。")


def inspect_artwork(
    run: NativeReplicaRun,
    path: str,
    job: dict[str, Any],
    detail: bool,
    expected_sha: str | None = None,
    baseline_ratio: float | None = None,
) -> dict[str, Any]:
    file = run.path(path)
    try:
        data = file.read_bytes()
        sha = digest(data)
        if expected_sha is not None and expected_sha != sha:
            raise StyleDeliveryInvalid("风格复刻图片原始字节哈希不一致。")
        with Image.open(file) as image:
            image.load()
            size = list(image.size)
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise StyleDeliveryInvalid("不支持的交付图片格式。")
    except (OSError, UnidentifiedImageError) as exc:
        raise StyleDeliveryInvalid("风格复刻图片不可读取。") from exc
    count = len(job["unit_ids"])
    # Same native style rules as workflow.record; no exact-mode minimum edge.
    if detail:
        ratio = 8 / 3 if count == 2 else 4 / 3
        if size[1] % count or abs(size[1] / size[0] - ratio) > 0.01:
            raise ReplicaCanvasMismatch(job["canvas"], (size[0], size[1]))
        if (
            baseline_ratio is not None
            and abs(size[1] / count / size[0] - baseline_ratio) > 0.01
        ):
            raise ReplicaCanvasMismatch(job["canvas"], (size[0], size[1]))
    record: dict[str, Any] = {"artwork": str(file), "sha256": sha, "size": size}
    if not detail and size != job["canvas"]:
        if not style_native_budget_matches(job["canvas"], (size[0], size[1])):
            raise ReplicaCanvasMismatch(job["canvas"], (size[0], size[1]))
        # Keep the approved plan intact; record actual dimensions separately.
        # The provider bytes are delivered unchanged, including after recovery.
        record["delivery_canvas"] = {
            **delivery_receipt(job["canvas"], (size[0], size[1]), sha),
            "policy": STYLE_NATIVE_POLICY,
        }
    return record


def direct_state(run: NativeReplicaRun) -> dict[str, Any]:
    return read_json(run.root / STATE_FILE)


def activate(run: NativeReplicaRun) -> dict[str, Any]:
    source = run.state()
    require_plan(source)
    original_sha = digest((run.run / "artifacts/workflow.json").read_bytes())
    if (run.root / STATE_FILE).exists():
        state = direct_state(run)
        if (
            state.get("delivery_policy") != POLICY
            or state.get("review_status") != "NOT_REVIEWED"
            or state.get("source_workflow_sha256") != original_sha
            or state.get("plan") != read_json(run.run / "artifacts/route-plan.json")
        ):
            raise StyleDeliveryInvalid("直接交付检查点与原确认方案不一致。")
        expected = [(j["id"], j["unit_ids"]) for j in source["layout"]["jobs"]]
        if (
            [(j["id"], j["unit_ids"]) for j in state["jobs"]] != expected
            or state["unit_ids"] != source["layout"]["unit_ids"]
            or state["route"] != source["route"]
        ):
            raise StyleDeliveryInvalid("直接交付检查点的画幅单元或顺序发生变化。")
    else:
        ledger = (
            read_json(run.root / "invocations.json")
            if (run.root / "invocations.json").exists()
            else {}
        )
        if any(row.get("state") == "invoked" for row in ledger.values()):
            raise ReplicaImageOutcomeUnknown("生图结果尚未核实，禁止自动重发。")
        jobs = deepcopy(source["layout"]["jobs"])
        detail = source["route"] == "DETAIL_PAGE"
        targets = []
        baseline_ratio = None
        for job in jobs:
            key = job["id"]
            record: dict[str, Any] = {
                "id": key,
                "status": "PENDING",
                "review_status": "NOT_REVIEWED",
            }
            candidate = next(
                (
                    row
                    for row in reversed(source.get("artwork", []))
                    if row["target_id"] == key
                ),
                None,
            )
            returned = next(
                (
                    row
                    for row in reversed(list(ledger.values()))
                    if row.get("target") == key
                    and row.get("path")
                    and row.get("state") in {"returned", "canvas_rejected"}
                ),
                None,
            )
            if returned:
                candidate = returned
            if candidate:
                path = run.path(candidate["path"])
                persisted = run.snapshot_files.get(
                    path.relative_to(run.root).as_posix(), {}
                )
                if persisted.get("sha256") != candidate.get("sha256"):
                    raise StyleDeliveryInvalid("已有真实画布缺少一致的持久哈希绑定。")
                record.update(
                    inspect_artwork(
                        run, str(path), job, detail, persisted["sha256"], baseline_ratio
                    )
                )
                record["status"] = "GENERATED"
                if detail and baseline_ratio is None:
                    baseline_ratio = (
                        record["size"][1] / len(job["unit_ids"]) / record["size"][0]
                    )
            else:
                pool = source["groups"] if detail else source["units"]
                if pool[key].get("status") == "awaiting_artwork":
                    native = read_json(
                        run.run / f"artifacts/generation-request-{key}.json"
                    )
                    if native.get("token") != pool[key].get("generation_token"):
                        raise StyleDeliveryInvalid("已有生图请求的令牌与目标不一致。")
                    record["legacy_request"] = str(
                        run.run / f"artifacts/generation-request-{key}.json"
                    )
            targets.append(record)
        state = {
            "delivery_policy": POLICY,
            "review_status": "NOT_REVIEWED",
            "state": "READY",
            "source_workflow_sha256": original_sha,
            "plan": read_json(run.run / "artifacts/route-plan.json"),
            "route": source["route"],
            "jobs": jobs,
            "unit_ids": source["layout"]["unit_ids"],
            "targets": targets,
            "module_index": 0,
        }
        write_json(run.root / STATE_FILE, state)
    run.direct_execution = True  # type: ignore[attr-defined]
    return state


def progress_state(state: dict[str, Any]) -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    for job, record in zip(state["jobs"], state["targets"], strict=True):
        if job["id"] != record["id"] or record.get("review_status") != "NOT_REVIEWED":
            raise StyleDeliveryInvalid("风格复刻交付顺序不一致。")
        modules.extend(
            {
                "id": uid,
                "status": record["status"],
                "attempts": [{"token": record["request"]["token"]}]
                if record.get("request")
                else [],
            }
            for uid in job["unit_ids"]
        )
    if [m["id"] for m in modules] != state["unit_ids"]:
        raise StyleDeliveryInvalid("风格复刻交付数量或顺序不完整。")
    return {
        "state": state["state"],
        "review_status": "NOT_REVIEWED",
        "modules": modules,
        "module_index": state["module_index"],
    }


def generation_workspace(
    run: NativeReplicaRun, state: dict[str, Any], index: int
) -> NativeReplicaRun:
    gate = copy(run)
    gate.run = run.root / "style-generation"
    artifacts = gate.run / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    source = deepcopy(run.state())
    source["state"] = "GENERATION_ALLOWED"
    source.pop("active_token", None)
    detail = state["route"] == "DETAIL_PAGE"
    pool = source["groups"] if detail else source["units"]
    for row in pool.values():
        for key in RUNTIME_FIELDS:
            row.pop(key, None)
        row.update(status="pending", revision_index=0)
    for prior in state["targets"][:index]:
        if prior["status"] != "GENERATED":
            raise StyleDeliveryInvalid("必须按原方案顺序生成。")
        pool[prior["id"]]["status"] = "generated"
    # Adopt only real dimensions, as the original style workflow does for G01.
    if detail and index and state["targets"][0]["status"] == "GENERATED":
        w, h = state["targets"][0]["size"]
        screen_h = h // len(state["jobs"][0]["unit_ids"])
        source["detail_ratio"] = {
            "width": w,
            "screen_height": screen_h,
            "ratio": screen_h / w,
        }
        for job in source["layout"]["jobs"]:
            job["canvas"] = [w, screen_h * len(job["unit_ids"])]
        source["route_specific_composition_plan"]["contract"] = source["layout"]
    manifest = run.run / "artifacts/reference-crops/manifest.json"
    from .replica_style_evidence import job_crop_manifest

    bound_manifest = job_crop_manifest(
        run, source, read_json(manifest), state["jobs"][index]
    )
    source["reference_crops"] = deepcopy(bound_manifest)
    write_json(artifacts / "workflow.json", source)
    write_json(artifacts / "reference-crops/manifest.json", bound_manifest)
    return gate


async def deliver(
    provider: Any, run: NativeReplicaRun, request: ProviderRequest
) -> list[str]:
    state = activate(run)
    progress_state(state)
    await provider._save(run, request.job_id)
    detail = state["route"] == "DETAIL_PAGE"
    baseline_ratio = None
    for index, (job, record) in enumerate(
        zip(state["jobs"], state["targets"], strict=True)
    ):
        state["module_index"] = sum(len(j["unit_ids"]) for j in state["jobs"][:index])
        if state["module_index"] > getattr(
            run, "delivery_ordinal", len(state["unit_ids"])
        ):
            break
        if record["status"] != "GENERATED":
            existing = record.get("request")
            if existing is None and record.get("legacy_request"):
                existing = read_json(run.path(record["legacy_request"]))
            ledger_path = run.root / "invocations.json"
            ledger = read_json(ledger_path) if ledger_path.exists() else {}
            prior = ledger.get(existing["token"], {}) if existing else {}
            # _load verifies normal retry lineage before this boundary. A
            # different job may retry a known provider rejection, retaining
            # its original token/result. Same-job and unknown calls stay fenced.
            if (
                prior.get("state") == "rejected"
                and isinstance(prior.get("job_id"), str)
                and prior["job_id"] != request.job_id
                and prior.get("target") == job["id"]
            ):
                record.setdefault("previous_requests", []).append(existing)
                for key in ("request", "request_path", "gate_path", "legacy_request"):
                    record.pop(key, None)
                record["status"] = "PENDING"
            if not record.get("request"):
                if record.get("legacy_request"):
                    gate = copy(run)
                    path = run.path(record["legacy_request"])
                else:
                    gate = generation_workspace(run, state, index)
                    gate.cli(
                        "generate-pair-request"
                        if detail
                        else "generate-single-request",
                        workspace=gate.run,
                        **{"group-id" if detail else "unit-id": job["id"]},
                    )
                    path = gate.run / f"artifacts/generation-request-{job['id']}.json"
                record.update(
                    request=read_json(path),
                    request_path=str(path),
                    gate_path=str(gate.run),
                    status="AWAITING_ARTWORK",
                )
                state["state"] = "AWAITING_ARTWORK"
                write_json(run.root / STATE_FILE, state)
                await provider._save(run, request.job_id)
            native = record["request"]
            gate = copy(run)
            gate.run = run.path(record["gate_path"])
            path = run.path(record["request_path"])
            if (
                read_json(path) != native
                or native.get("generation_scope", {}).get("unit_ids") != job["unit_ids"]
            ):
                raise StyleDeliveryInvalid("生图请求与确认的单元绑定不一致。")
            handoff = await asyncio.to_thread(provider._style_handoff, gate, path)
            prompt = run.path(handoff["prompt_path"]).read_text(encoding="utf-8-sig")
            paths = [run.path(p) for p in handoff["referenced_image_paths"]]
            image = await provider._render(
                run,
                request,
                native,
                prompt,
                paths,
                native["render_contract"]["native_generation_size"],
                job["id"],
            )
            record.update(
                inspect_artwork(
                    run, str(image), job, detail, baseline_ratio=baseline_ratio
                )
            )
            record["status"] = "GENERATED"
            write_json(run.root / STATE_FILE, state)
            await provider._save(run, request.job_id)
        inspect_artwork(
            run, record["artwork"], job, detail, record["sha256"], baseline_ratio
        )
        if detail and baseline_ratio is None:
            baseline_ratio = (
                record["size"][1] / len(job["unit_ids"]) / record["size"][0]
            )
    # Native export responsibilities: equal-height pair crops; unchanged singles.
    out = run.root / "style-exports"
    out.mkdir(exist_ok=True)
    files = []
    for job, record in zip(state["jobs"], state["targets"], strict=True):
        if record["status"] != "GENERATED":
            break
        master = run.path(record["artwork"])
        for index, uid in enumerate(job["unit_ids"]):
            target = out / (uid + ".png")
            if detail:
                with Image.open(master) as image:
                    width, height = image.size
                    screen_h = height // len(job["unit_ids"])
                    image.crop(
                        (0, index * screen_h, width, (index + 1) * screen_h)
                    ).save(target)
            else:
                shutil.copy2(master, target)
            with Image.open(target) as image:
                image.load()
                size = list(image.size)
            files.append(
                {
                    "id": uid,
                    "path": str(target),
                    "sha256": digest(target.read_bytes()),
                    "size": size,
                    "review_status": "NOT_REVIEWED",
                    "source_sha256": record["sha256"],
                    **({"delivery_canvas": record["delivery_canvas"]}
                       if "delivery_canvas" in record else {}),
                }
            )
    if len(files) < len(state["unit_ids"]):
        state["files"] = files
        write_json(run.root / STATE_FILE, state)
        return [f["path"] for f in files]
    if [f["id"] for f in files] != state["unit_ids"]:
        raise StyleDeliveryInvalid("导出图片数量或顺序不完整。")
    state.update(state="DELIVERED", module_index=len(files), files=files)
    write_json(run.root / STATE_FILE, state)
    write_json(
        run.root / "style-direct-delivery.json",
        {"delivery_policy": POLICY, "review_status": "NOT_REVIEWED", "files": files},
    )
    return [f["path"] for f in files]


class ReplicaDeliveryProvider(NativeReplicaProvider):
    """Keep exact's released implementation and binding completely unchanged."""

    def _load(self, request: ProviderRequest) -> tuple[dict[str, Any], str, int]:
        values, document_id, ordinal = super()._load(request)
        if values.get("replication_method") != "style":
            return values, document_id, ordinal
        approved = values["native_snapshot"]
        source = (
            values.get("native_executions", {}).get(request.job_id, {}).get("snapshot")
        )
        if not source:
            with self.session_factory() as session:
                job = session.get(JobRecord, request.job_id)
                parent_id = job.retry_of_job_id if job else None
                if parent_id:
                    parent = session.get(JobRecord, parent_id)
                    if (
                        not parent
                        or parent.status not in {"failed", "partial"}
                        or parent.workspace_id != request.workspace_id
                        or parent.configuration.get("replica_session_id") != document_id
                        or parent.configuration.get("settings", {}).get(
                            "replica_plan_hash"
                        )
                        != values.get("plan_hash")
                        or not any(
                            e.get("from") == parent_id and e.get("to") == request.job_id
                            for e in values.get("retry_events", [])
                        )
                    ):
                        raise ReplicaNativeError("风格复刻接续来源与当前任务不一致。")
                    source = (
                        values.get("native_executions", {})
                        .get(parent_id, {})
                        .get("snapshot")
                    )
                    if not source:
                        raise ReplicaNativeError(
                            "原任务缺少持久检查点，不能重复生成代替恢复。"
                        )
        source = source or approved
        validate_source(
            source, approved, f"{request.workspace_id}/replica/{document_id}/"
        )
        return {**values, "native_snapshot": source}, document_id, ordinal

    async def _style(
        self, run: NativeReplicaRun, request: ProviderRequest
    ) -> list[str]:
        return await deliver(self, run, request)

    async def _render(
        self,
        run: NativeReplicaRun,
        request: ProviderRequest,
        native: dict[str, Any],
        prompt: str,
        paths: list[Path],
        canvas: list[int],
        key: str,
        retry_candidate: RetryArtwork | None = None,
    ) -> Path:
        try:
            return await super()._render(
                run, request, native, prompt, paths, canvas, key, retry_candidate
            )
        except UnidentifiedImageError as exc:
            if run.method != "style":
                raise
            raise StyleDeliveryInvalid("生图返回的文件不可解码，已停止交付。") from exc

    async def _save(
        self, run: NativeReplicaRun, job_id: str, *, delivery: list[str] | None = None
    ) -> None:
        from .replica_completion import CompletionRun

        if isinstance(run, CompletionRun):
            await super()._save(run, job_id, delivery=delivery)
            if run.method == "style":
                # The shared native saver only publishes exact progress.
                with self.session_factory() as session:
                    job = session.get(JobRecord, job_id, with_for_update=True)
                    row = session.get(
                        ReplicaDocumentRecord, run.document_id, with_for_update=True
                    )
                    progress = native_progress(run.state())
                    progress["job_id"] = job_id
                    row.payload = {**row.payload, "execution_progress": progress}
                    if job is not None and job.status not in {
                        "completed",
                        "failed",
                        "partial",
                        "cancelled",
                    }:
                        job.progress = max(job.progress, progress["percentage"])
                    session.commit()
            return
        if run.method != "style" or not (run.root / STATE_FILE).exists():
            await super()._save(run, job_id, delivery=delivery)
            return
        snapshot = await run.snapshot()
        state = direct_state(run)
        progress = native_progress(progress_state(state))
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            row = session.get(
                ReplicaDocumentRecord, run.document_id, with_for_update=True
            )
            values = dict(row.payload)
            previous = values.get("execution_progress", {})
            progress["job_id"] = job_id
            if previous.get("job_id") == job_id:
                progress["percentage"] = max(
                    int(previous.get("percentage", 0)), progress["percentage"]
                )
            executions = dict(values.get("native_executions", {}))
            executions[job_id] = {
                "snapshot": snapshot,
                "state": state["state"],
                "delivery": delivery if delivery is not None else executions.get(
                    job_id, {}
                ).get("delivery", []),
                "delivery_policy": POLICY,
            }
            row.payload = {
                **values,
                "native_executions": executions,
                "execution_progress": progress,
            }
            row.updated_at = utc_now()
            if job is not None and job.status not in {
                "completed",
                "failed",
                "partial",
                "cancelled",
            }:
                job.progress = max(job.progress, progress["percentage"])
                job.state_version += 1
            session.commit()

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        from . import replica_completion

        # The same workspace/job/plan authorization precedes both execution
        # policies. A best-effort plan never enters the old passing-audit gate.
        try:
            values, document_id, ordinal = super()._load(request)
        except ReplicaNativeError:
            return await super().execute(request)
        if replica_completion.available(values.get("native_snapshot", {})):
            try:
                return await replica_completion.execute(
                    self, request, values, document_id, ordinal
                )
            except ReplicaNativeError as error:
                return ProviderResult(
                    outcome=ProviderOutcome.FAILED,
                    error_code=error.code,
                    error_message="生成任务的持久检查点无法恢复。",
                    retryable=False,
                )
        result = await super().execute(request)
        if result.error_code == StyleDeliveryInvalid.code:
            return replace(result, error_message=StyleDeliveryInvalid.message)
        if result.raw_usage.get("replication_method") == "style":
            return replace(
                result,
                raw_usage={
                    **result.raw_usage,
                    "native_visual_audited": False,
                    "post_generation_review": "disabled",
                    "delivery_policy": POLICY,
                    "review_status": "NOT_REVIEWED",
                },
            )
        return result
