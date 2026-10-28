# ruff: noqa: E501
"""Image provider driven by the original replica Skill state machines."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from .models import JobItemRecord, JobRecord, ReplicaDocumentRecord, utc_now
from .provider import ProviderArtifact, ProviderOutcome, ProviderRequest, ProviderResult
from .replica_canvas import delivery_receipt, native_budget_matches
from .replica_layout import exact_review_context
from .replica_native import (
    VISUAL_EVIDENCE_POLICY_VERSION,
    NativeReplicaRun,
    ReplicaAnalysisUnavailable,
    ReplicaInputTooLarge,
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    digest,
    native_accounting,
    read_json,
    write_json,
)
from .replica_progress import native_progress
from .replica_retry import RetryArtwork, prior_pixel_reviews, retry_artwork_candidates


class ReplicaImageError(ReplicaNativeError):
    provider_request_id: str | None = None
    http_status: int | None = None
    provider_metrics: dict[str, Any] = {}


class ReplicaImageOutcomeUnknown(ReplicaImageError):
    code = "PROVIDER_OUTCOME_UNKNOWN"


class ReplicaCanvasMismatch(ReplicaNativeError):
    code = "REPLICA_CANVAS_MISMATCH"

    def __init__(self, expected: list[int], actual: tuple[int, int], attempts: int = 1) -> None:
        self.expected_size, self.actual_size, self.attempts = expected, list(actual), attempts
        self.message = (
            f"当前屏要求 {expected[0]}×{expected[1]}，供应商返回 {actual[0]}×{actual[1]}，"
            "分辨率或比例未通过校验；原始产物已保留。"
        )
        if attempts > 1:
            self.message += f" 已自动纠正画幅 {attempts - 1} 次，达到次数上限。"
        super().__init__(self.message)


def _canvas_matches(
    expected: list[int], actual: tuple[int, int], minimum_edge: int = 0
) -> bool:
    return (
        min(actual) >= minimum_edge
        and abs((actual[0] / actual[1]) / (expected[0] / expected[1]) - 1) <= 0.01
    )


class NativeReplicaProvider:
    name = "replica-native"
    model = "gpt-image-2"
    pricing_version = "replica-native-20260923"

    def __init__(self, session_factory: Any, gateway: Any, storage: Any) -> None:
        self.session_factory, self.gateway, self.storage = (
            session_factory,
            gateway,
            storage,
        )

    def _load(self, request: ProviderRequest) -> tuple[dict[str, Any], str, int]:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            item = session.get(JobItemRecord, request.job_item_id)
            document_id = job.configuration.get("replica_session_id") if job else None
            row = (
                session.get(ReplicaDocumentRecord, document_id) if document_id else None
            )
            if (
                row is None
                or item is None
                or job is None
                or row.workspace_id != request.workspace_id
                or job.id not in row.payload.get("job_ids", [])
                or job.configuration.get("settings", {}).get("replica_plan_hash")
                != row.payload.get("plan_hash")
                or not row.payload.get("confirmed_instructions_hash")
            ):
                raise ReplicaNativeError("未找到当前工作区已确认的原生复刻方案。")
            return dict(row.payload), row.id, item.ordinal

    def _retry_candidates(
        self, values: dict[str, Any], request: ProviderRequest, snapshot: dict[str, Any]
    ) -> dict[str, RetryArtwork]:
        events = {
            event.get("to"): event.get("from")
            for event in values.get("retry_events", [])
        }
        if not values.get("native_executions") or request.job_id not in events:
            return {}
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            if (
                job is None
                or job.workspace_id != request.workspace_id
                or not job.retry_of_job_id
                or job.retry_of_job_id != events[request.job_id]
            ):
                return {}
            document_id = job.configuration.get("replica_session_id")
            if not isinstance(document_id, str):
                return {}
            parent = session.get(JobRecord, job.retry_of_job_id)
            if (
                parent is None
                or parent.workspace_id != request.workspace_id
                or parent.status not in {"failed", "partial"}
                or parent.configuration.get("replica_session_id")
                != job.configuration.get("replica_session_id")
                or parent.configuration.get("settings", {}).get("replica_plan_hash")
                != values.get("plan_hash")
            ):
                return {}
            source_job_id = parent.id
        previous = values["native_executions"].get(source_job_id, {})
        if previous.get("state") not in {"SET_COMPLETE", "FINALIZED"}:
            return {}
        history = {}
        ancestor = events.get(source_job_id)
        seen = {request.job_id, source_job_id}
        while ancestor and ancestor not in seen:
            seen.add(ancestor)
            execution = values["native_executions"].get(ancestor, {})
            if execution.get("state") in {"SET_COMPLETE", "FINALIZED"}:
                history[ancestor] = execution["snapshot"]
            ancestor = events.get(ancestor)
        return retry_artwork_candidates(
            previous["snapshot"],
            snapshot,
            source_job_id,
            f"{request.workspace_id}/replica/{document_id}/",
            history=history,
        )

    async def _save(
        self, run: NativeReplicaRun, job_id: str, *, delivery: list[str] | None = None
    ) -> None:
        snapshot = await run.snapshot()
        with self.session_factory() as session:
            # Match execution's job -> document lock order.
            job = session.get(JobRecord, job_id, with_for_update=True)
            row = session.get(
                ReplicaDocumentRecord, run.document_id, with_for_update=True
            )
            values = dict(row.payload)
            executions = dict(values.get("native_executions", {}))
            executions[job_id] = {
                "snapshot": snapshot,
                "delivery": delivery if delivery is not None else executions.get(job_id, {}).get("delivery", []),
                "state": run.state()["state"],
            }
            progress = native_progress(run.state()) if run.method == "exact" else None
            if progress:
                previous = values.get("execution_progress", {})
                if previous.get("job_id") != job_id:
                    previous = {}
                progress["job_id"] = job_id
                progress["percentage"] = max(
                    int(previous.get("percentage", 0)), progress["percentage"]
                )
                values["execution_progress"] = progress
                if job is not None and job.status not in {
                    "completed",
                    "failed",
                    "partial",
                    "cancelled",
                }:
                    job.progress = max(job.progress, progress["percentage"])
                    job.state_version += 1
            row.payload = {**values, "native_executions": executions}
            row.updated_at = utc_now()
            session.commit()

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        run = None
        initial_calls = 0
        try:
            values, document_id, ordinal = self._load(request)
            execution = values.get("native_executions", {}).get(request.job_id, {})
            snapshot = execution.get("snapshot") or values["native_snapshot"]
            from . import replica_direct_delivery
            from .replica_execution_resume import (
                budget_resume_source,
                restore_execution,
            )

            direct_source = (
                None
                if execution
                else replica_direct_delivery.retry_source(
                    values, request, self.session_factory
                )
            )

            resumed = (
                None
                if execution or direct_source
                else budget_resume_source(values, request, self.session_factory)
            )
            if resumed:
                snapshot = resumed[0]
            if direct_source:
                snapshot = direct_source[0]
            run_type = NativeReplicaRun
            if snapshot.get("model_protocol"):
                from .replica_exact_compact import CompactExactReplicaRun, run_class

                run_type = (
                    CompactExactReplicaRun
                    if resumed
                    or (
                        direct_source
                        and snapshot["model_protocol"].get("version")
                        not in {"exact-compact-v5", "exact-compact-v6", "exact-compact-v7", "exact-compact-v8", "exact-compact-v9", "exact-compact-v10", "exact-compact-v11", "exact-compact-v12", "exact-compact-v13", "exact-compact-v14"}
                    )
                    else run_class(
                        values["replication_method"], snapshot["model_protocol"]
                    )
                )
            if values["replication_method"] == "exact":
                from .replica_platform_delivery import delivery_run_type

                run_type = delivery_run_type(run_type)
            run = run_type(
                values["replication_method"], document_id, request.workspace_id,
                self.gateway, self.storage, root=Path(snapshot["root"]),
            )
            if direct_source and snapshot.get("model_protocol") and snapshot.get("model_protocol", {}).get(
                "version"
            ) not in {"exact-compact-v5", "exact-compact-v6", "exact-compact-v7", "exact-compact-v8", "exact-compact-v9", "exact-compact-v10", "exact-compact-v11", "exact-compact-v12", "exact-compact-v13", "exact-compact-v14"}:
                await replica_direct_delivery.restore_source(
                    run, snapshot, direct_source[1]
                )
            elif resumed:
                await restore_execution(run, snapshot, resumed[1])
            else:
                await run.restore(snapshot)
            # An explicit retry authorizes the current application delivery
            # policy while keeping its frozen original plan and model verdicts.
            run.canvas_correction_enabled = any(  # type: ignore[attr-defined]
                event.get("to") == request.job_id
                for event in values.get("retry_events", [])
            )
            initial_calls = len(run.usage)
            run.delivery_ordinal = ordinal  # type: ignore[attr-defined]
            delivery = execution.get("delivery", [])
            if len(delivery) <= ordinal:
                if run.method == "exact":
                    delivery = await replica_direct_delivery.execute(self, run, request)
                else:
                    delivery = await self._style(run, request)
                if not ordinal < len(delivery) <= len(values["plans"]):
                    raise ReplicaNativeError("验收图片数量与确认方案不一致。")
                await self._save(run, request.job_id, delivery=delivery)
            path = run.path(delivery[ordinal])
            data = path.read_bytes()
            with Image.open(path) as image:
                width, height = image.size
                content_type = Image.MIME.get(image.format or "", "image/png")
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[
                    image.format or "PNG"
                ]
            accounting = native_accounting(run.usage[initial_calls:])
            return ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id=f"replica:{request.job_id}:{ordinal}",
                usage_reported=accounting["reported"],
                raw_usage={
                    **accounting,
                    "skill_id": run.skill_id,
                    "skill_content_hash": run.source_hash,
                    "replication_method": run.method,
                    "native_state": run.state()["state"],
                    "native_visual_audited": not getattr(
                        run, "direct_execution", False
                    ),
                    "post_generation_review": "disabled"
                    if getattr(run, "direct_execution", False)
                    else "enabled",
                    "native_calls": run.usage[initial_calls:],
                },
                actual_cost_micros=accounting["actual_cost_micros"],
                estimated_cost_micros=accounting["estimated_cost_micros"],
                artifact=ProviderArtifact(
                    payload=data,
                    filename=f"replica-{ordinal + 1:03}{suffix}",
                    content_type=content_type,
                    width=width,
                    height=height,
                    sha256=digest(data),
                ),
                provider_name=self.gateway.provider_name,
                model_name=self.gateway.image_model,
                pricing_version=self.gateway.pricing_version,
            )
        except Exception as exc:
            # Generated files and issued tokens survive audit/transport failures.
            if run is not None:
                with suppress(Exception):
                    await self._save(run, request.job_id)
            accounting = native_accounting(run.usage[initial_calls:] if run else [])
            from .provider_diagnostics import retained_metrics

            diagnostics = next((retained_metrics(call) for call in reversed(
                run.usage[initial_calls:] if run else []
            ) if retained_metrics(call)), {})
            progress: dict[str, Any] = {}
            if run is not None and run.method == "exact":
                with suppress(Exception):
                    progress = native_progress(run.state())
            safe_message = (
                f"{progress['total']} 屏中 {progress['rejected']} 屏未通过审图，整组暂未交付。请查看逐屏原因后重试。"
                if progress.get("rejected")
                else "本次复刻暂未完成，进度已保留。可按原方案重新复刻整组。"
            )
            from .replica_failure import failure_fields
            from .replica_image_recovery import retryable as can_resume_original

            fields = failure_fields(exc, run.usage[initial_calls:] if run else [], ordinal if run else 0)
            failure_metrics = fields.pop("raw_usage_metrics")
            return ProviderResult(
                outcome=ProviderOutcome.OUTCOME_UNKNOWN
                if isinstance(exc, ReplicaImageOutcomeUnknown)
                else ProviderOutcome.FAILED,
                provider_request_id=fields["provider_request_id"],
                http_status=fields["http_status"],
                error_code=fields["error_code"],
                error_message=fields["error_message"] if fields["provider_request_id"] else (
                    "生图结果尚未核实，已停止自动重发。请稍后查看任务，避免重复提交。"
                    if isinstance(exc, ReplicaImageOutcomeUnknown)
                    else exc.message
                    if isinstance(
                        exc,
                        (
                            ReplicaInputTooLarge,
                            ReplicaAnalysisUnavailable,
                            ReplicaPlanAuditRejected,
                            ReplicaCanvasMismatch,
                        ),
                    )
                    else safe_message
                ),
                retryable=can_resume_original(run, exc) if run else False,
                usage_reported=run is not None and accounting["reported"],
                raw_usage={
                    **accounting,
                    "native_calls": run.usage[initial_calls:] if run else [],
                    "metrics": {**diagnostics, **failure_metrics},
                },
                actual_cost_micros=accounting["actual_cost_micros"],
                estimated_cost_micros=accounting["estimated_cost_micros"],
            )

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
        from .xinghe_agent import ImageEditRequest, SourceImage

        ledger_path = run.root / "invocations.json"
        ledger = read_json(ledger_path) if ledger_path.exists() else {}
        token = native["token"]
        minimum_edge = min(canvas) if run.method == "exact" else 0
        native_delivery = run.method == "exact" and getattr(run, "direct_execution", False)
        canvas_correction = native_delivery and (
            getattr(run, "binding", {}).get("version") in {"exact-compact-v9", "exact-compact-v10", "exact-compact-v11", "exact-compact-v12", "exact-compact-v13", "exact-compact-v14"}
            or getattr(run, "canvas_correction_enabled", False)
        )
        format_limit = 2 if canvas_correction or not getattr(run, "direct_execution", False) else 1
        format_start = 0
        reconcile_only = False
        resolution_input: Path | None = None
        resolution_recovery: dict[str, Any] | None = None
        prior = ledger.get(token)
        if prior:
            if (
                prior.get("state") in {"returned", "canvas_rejected"}
                and prior.get("path")
                and run.path(prior["path"]).is_file()
                and (prior.get("state") == "returned" or native_delivery)
            ):
                if digest(run.path(prior["path"]).read_bytes()) != prior.get("sha256"):
                    raise ReplicaNativeError(
                        "已记录的生图产物哈希发生变化，已停止恢复。"
                    )
                path = run.path(prior["path"])
                with Image.open(path) as image:
                    image.load()
                    actual = image.size
                    if not _canvas_matches(canvas, image.size, minimum_edge):
                        if not native_delivery or not native_budget_matches(canvas, actual):
                            attempts = prior.get("canvas_attempts", [])
                            if not (
                                canvas_correction
                                and prior.get("state") == "canvas_rejected"
                                and 0 < len(attempts) < format_limit
                                and prior.get("job_id") == request.job_id
                                and prior.get("target") == key
                                and prior.get("native_prompt_sha256") == digest(prompt.encode("utf-8"))
                                and prior.get("actual_size") == list(actual)
                                and attempts[-1].get("sha256") == prior.get("sha256")
                                and attempts[-1].get("path") == prior.get("path")
                                and attempts[-1].get("accepted_canvas") is False
                            ):
                                raise ReplicaCanvasMismatch(canvas, actual, len(attempts) or 1)
                            format_start = len(attempts)
                        if prior.get("state") == "canvas_rejected" and (
                            prior.get("target") != key
                            or prior.get("native_prompt_sha256") != digest(prompt.encode("utf-8"))
                            or prior.get("actual_size") != list(actual)
                        ):
                            raise ReplicaNativeError("缓存图片与原生模块或画幅绑定不一致。")
                        receipt = delivery_receipt(canvas, actual, prior["sha256"])
                        if not format_start and prior.get("delivery_canvas") not in (None, receipt):
                            raise ReplicaNativeError("原生画幅交付记录发生变化。")
                        if not format_start and prior.get("delivery_canvas") != receipt:
                            # Retain canvas_rejected and its original strict verdict.
                            prior["delivery_canvas"] = receipt
                            write_json(ledger_path, ledger)
                            await self._save(run, request.job_id)
                if not format_start:
                    return path
            if not format_start:
                if prior.get("state") == "canvas_rejected":
                    raise ReplicaCanvasMismatch(canvas, tuple(prior["actual_size"]), len(prior.get("canvas_attempts", [])) or 1)
                from .replica_image_recovery import MAX_RESUMPTIONS, VERSION

                if (
                    prior.get("state") == "invoked"
                    and (getattr(run, "binding", None) or {}).get("version") == VERSION
                    and prior.get("job_id") == request.job_id
                    and prior.get("target") == key
                    and prior.get("native_prompt_sha256") == digest(prompt.encode("utf-8"))
                    and type(prior.get("reconciliation_attempts", 0)) is int
                    and 0 <= prior.get("reconciliation_attempts", 0) < MAX_RESUMPTIONS
                ):
                    reconcile_only = True
                    format_start = prior.get("inflight_format_attempt", 0)
                    if type(format_start) is not int or not 0 <= format_start < format_limit:
                        raise ReplicaNativeError("原任务恢复的画幅次数记录无效。")
                unknown_error = ReplicaImageOutcomeUnknown(
                    "此令牌的生图结果尚未核实，系统不会自动重复调用。请先核查原任务。"
                )
                unknown_error.provider_request_id = prior.get("provider_request_id")
                unknown_error.http_status = prior.get("http_status")
                unknown_error.provider_metrics = prior.get("provider_metrics", {})
                if not reconcile_only:
                    raise unknown_error
        if retry_candidate and run.method == "exact" and native.get("mode") != "REPAIR":
            if (
                retry_candidate.module_id != key
                or retry_candidate.plan_sha256 != run.state().get("plan_sha256")
            ):
                raise ReplicaNativeError("重试画布与当前原生方案不一致。")
            data = await self.storage.read_bytes(retry_candidate.object_key)
            if digest(data) != retry_candidate.sha256:
                raise ReplicaNativeError("重试画布原始字节哈希发生变化，已停止恢复。")
            with Image.open(BytesIO(data)) as image:
                actual = image.size
                if not _canvas_matches(canvas, actual):
                    raise ReplicaCanvasMismatch(canvas, image.size)
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[
                    image.format or "PNG"
                ]
            path = run.root / "artwork" / ("reused-" + retry_candidate.sha256 + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if _canvas_matches(canvas, actual, minimum_edge):
                ledger[token] = {
                    "state": "returned",
                    "path": str(path),
                    "sha256": retry_candidate.sha256,
                    "job_id": request.job_id,
                    "target": key,
                    "kind": "native_retry_candidate",
                    "source_job_id": retry_candidate.source_job_id,
                    "source_plan_sha256": retry_candidate.plan_sha256,
                    "network_requests": 0,
                }
                write_json(ledger_path, ledger)
                run.usage.append(
                    {
                        "kind": "image_reuse",
                        "reported": True,
                        "network_requests": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "actual_cost_micros": 0,
                        "estimated_cost_micros": 0,
                        "source_job_id": retry_candidate.source_job_id,
                    }
                )
                await self._save(run, request.job_id)
                return path
            # A verified old canvas below the requested resolution remains an
            # editing input. Only a fresh, full-resolution native result can
            # become artwork; never enlarge the stored pixels locally.
            resolution_input = path
            resolution_recovery = {
                "source_job_id": retry_candidate.source_job_id,
                "sha256": retry_candidate.sha256,
                "actual_size": list(actual),
                "required_short_edge": minimum_edge,
            }
        # Crops isolate detail; the original views retain fit, scale and drape.
        # Generation and the later pixel audit must share that identity evidence.
        # Keep the native blueprint/repair ordering and add only this run's
        # original product inputs, with the same byte budget and path guards.
        originals = sorted((run.root / "inputs").glob("product-*"))
        draft = resolution_input
        if run.method == "exact" and native.get("mode") == "REPAIR":
            if not paths:
                raise ReplicaNativeError("修复缺少当前商品初稿。")
            draft = paths[0]
        elif resolution_input is not None:
            paths = [resolution_input, *paths]
        if run.method == "style" and native.get("revision_index", 0):
            if not paths:
                raise ReplicaNativeError("修复缺少当前商品初稿。")
            # The native style handoff explicitly binds the LAST image as the
            # failed artwork. Supplement product evidence before that image.
            draft = paths[-1]
            paths = [p for p in [*paths[:-1], *originals] if p != draft] + [draft]
        else:
            paths = [*paths, *originals]
        render_sources = run.sources(list(dict.fromkeys(paths)))
        bindings = []
        evidence = []
        for index, source in enumerate(render_sources, start=1):
            path = Path(source.asset_id)
            if draft is not None and path == draft.resolve():
                role = "current_artwork"
            elif source.source_role == "product_reference":
                role = (
                    "product_original"
                    if path.parent == run.root / "inputs"
                    else "product_detail"
                )
            else:
                role = "composition_reference"
            binding = {"image": index, "role": role}
            bindings.append(binding)
            evidence.append({**binding, "sha256": source.sha256})
        # An empty geometry guide is a generation input, never a resized result.
        # Keep the native blueprint/current-artwork order and bind it separately.
        if min(canvas) >= 1024 and all(
            abs((canvas[0] / canvas[1]) / ratio - 1) > 0.01
            for ratio in (1, 2 / 3, 3 / 2)
        ):
            if canvas[0] * canvas[1] > 20_000_000:
                raise ReplicaNativeError("单屏画幅过大，需要重新核对物理拆屏。")
            output = BytesIO()
            Image.new("RGB", (canvas[0], canvas[1]), "white").save(output, "PNG")
            raw = output.getvalue()
            guide = SourceImage(
                asset_id="format-canvas",
                group_key=key,
                filename="format-canvas.png",
                content_type="image/png",
                sha256=digest(raw),
                payload=raw,
                source_role="format_canvas",
            )
            render_sources = (*render_sources, guide)
            binding = {"image": len(render_sources), "role": "format_canvas"}
            bindings.append(binding)
            evidence.append({**binding, "sha256": guide.sha256})
        dispatch_prompt = prompt
        if run.method == "exact" and draft is not None:
            blueprint = next(
                (
                    row["image"]
                    for row in bindings
                    if row["role"] == "composition_reference"
                ),
                None,
            )
            if blueprint is None:
                raise ReplicaNativeError("修复缺少原始版式参考。")
            marker = "Reference image 1 is the binding composition blueprint;"
            if prompt.count(marker) != 1:
                raise ReplicaNativeError("修复提示缺少唯一的原生版式绑定。")
            # The native request prepends the draft. Translate its one logical
            # blueprint ordinal to the actual multipart ordinal; retain the
            # original request unchanged in the run snapshot.
            dispatch_prompt = prompt.replace(
                marker,
                f"Reference image {blueprint} is the binding composition blueprint;",
                1,
            )
            if resolution_input is not None:
                dispatch_prompt = (
                    "NATIVE RESOLUTION RECOVERY: current_artwork is the real prior "
                    "canvas, but its resolution is too small. Preserve its correct "
                    "composition, product identity and approved copy. Recreate the "
                    f"complete visual natively at {canvas[0]}x{canvas[1]}, with a "
                    f"short edge of at least {minimum_edge} pixels. Do not return "
                    "preview dimensions or a locally enlarged copy.\n" + dispatch_prompt
                )
        dispatch_prompt = run.image_prompt(dispatch_prompt)
        bound_prompt = (
            dispatch_prompt
            + "\n\nIMAGE_BINDINGS="
            + json.dumps(bindings)
            + "\nThese server-bound image numbers identify the native input roles above. "
            "Only product_original and product_detail define the real product identity, "
            "geometry, fabric and surface pattern. composition_reference supplies layout, "
            "camera, lighting and styling, never product facts or another person's identity. "
            "Read composition left/right in viewer image coordinates, never anatomical "
            "hand labels. Actual visible positions in composition_reference outrank "
            "inferred handedness words. current_artwork is the repair canvas; preserve "
            "its passed regions and correct only the audited mismatches using the "
            "original product evidence."
            + "\n\n"
            + run.generation_policy()
            + "\nThe separately bound format_canvas, when present, defines only the exact "
            f"{canvas[0]}x{canvas[1]} output boundary. Its white pixels are not a background "
            "reference. Paint the full requested canvas using the composition reference's "
            "background and visual anchors; never default to a generic 2:3 poster."
        )
        module_constraints = (
            getattr(run, "context", {}).get("module_constraints", {}).get(key)
        )
        if module_constraints:
            adapt_evidence = getattr(run, "model_module_evidence", None)
            bound_prompt += "\nAPPROVED_MODULE_EVIDENCE=" + json.dumps(
                adapt_evidence(module_constraints)
                if callable(adapt_evidence)
                else module_constraints,
                ensure_ascii=False,
            )
            if module_constraints.get("text_bindings") is not None:
                bound_prompt += (
                    "\nTEXT SLOT EXECUTION: Render ONLY each binding's render_text in its "
                    "source bbox and font_tier. line_group records the reference's true line "
                    "structure; small connecting words remain small, not another giant row. "
                    "Every pill uses its bound approved text, never the source slogan. "
                    "Do not add an extra ribbon, row or caption for surplus approved copy. "
                    + (
                        "Follow final layout_anchors for adapted objects and geometry; "
                        "reference pixels preserve composition, not old product attributes."
                        if getattr(run, "render_binding_version", None) == 3
                        else "Retain all subject bounds and negative space in source_layout_anchors."
                    )
                )
            if module_constraints.get("product_visibility") == "context_only":
                bound_prompt += (
                    "\nCONTEXT-ONLY SCENE: Product photos identify what is being marketed; "
                    "the physical product, packaging and loose contents are absent from this "
                    "page. Global product geometry locks apply only when visible. Do not "
                    "insert food, product fragments or packaging into scene props. Reproduce "
                    "the scene's own natural objects at the reference's actual scale."
                )
        resumed_receipt = prior or {}
        if (format_start or reconcile_only) and (
            resumed_receipt.get("source_bindings") != evidence
            or resumed_receipt.get("dispatch_prompt_sha256") != digest(bound_prompt.encode("utf-8"))
        ):
            raise ReplicaNativeError("画幅纠正的原始素材或完整方案绑定发生变化。")
        ledger[token] = {
            **(resumed_receipt if format_start or reconcile_only else {}),
            "state": "invoked",
            "job_id": request.job_id,
            "target": key,
            "source_bindings": evidence,
            "native_prompt_sha256": digest(prompt.encode("utf-8")),
            "dispatch_prompt_sha256": digest(bound_prompt.encode("utf-8")),
            "visual_evidence_policy_version": VISUAL_EVIDENCE_POLICY_VERSION,
            "inflight_format_attempt": format_start,
        }
        if reconcile_only:
            ledger[token]["reconciliation_attempts"] = resumed_receipt.get("reconciliation_attempts", 0) + 1
        if resolution_recovery is not None:
            ledger[token]["resolution_recovery"] = resolution_recovery
        write_json(ledger_path, ledger)
        # The invocation fence is durable BEFORE the network call.
        await self._save(run, request.job_id)
        for format_attempt in range(format_start, format_limit):
            attempt_prompt = bound_prompt
            if format_attempt:
                attempt_prompt += (
                    "\nFORMAT CORRECTION: The previous known response failed the canvas "
                    "resolution or aspect ratio. Re-render this same approved module "
                    f"natively at EXACT {canvas[0]}x{canvas[1]}, with a short edge "
                    f"of at least {min(canvas)} pixels. "
                    "Use the bound format_canvas boundary; preserve the blueprint and product."
                )
                if canvas_correction:
                    previous = ledger[token]["canvas_attempts"][-1]
                    attempt_prompt += (
                        f" Previous actual canvas: {previous['size'][0]}x{previous['size'][1]}."
                        f" REQUIRED WIDTH/HEIGHT RATIO = {canvas[0]}/{canvas[1]}"
                        f" ({canvas[0] / canvas[1]:.8f})."
                        " Do not substitute the nearest standard ratio or a 3:4 preset."
                        " Preserve every approved plan detail and all source bindings;"
                        " this is a native file-format correction, not a new visual audit."
                    )
                    ledger[token]["canvas_correction"] = {
                        "policy": "bounded-native-canvas-correction-v1",
                        "automatic_correction_number": format_attempt,
                        "maximum_automatic_corrections": format_limit - 1,
                        "previous_sha256": previous["sha256"],
                        "previous_actual_size": previous["size"],
                        "requested_size": canvas,
                    }
                ledger[token]["state"] = "invoked"
                ledger[token]["inflight_format_attempt"] = format_attempt
                write_json(ledger_path, ledger)
                await self._save(run, request.job_id)
            before = getattr(self.gateway, "external_network_request_count", None)
            result = await self.gateway.edit(
                ImageEditRequest(
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    job_item_id=request.job_item_id,
                    attempt_id=request.attempt_id,
                    page_number=1,
                    group_key=key,
                    sources=render_sources,
                    prompt=attempt_prompt,
                    quality="high",
                    idempotency_key=f"replica-native:{request.job_id}:{token}"
                    + (f":format-{format_attempt + 1}" if format_attempt else ""),
                    size=f"{canvas[0]}x{canvas[1]}",
                    preserve_provider_artifact=True,
                    single_dispatch=False,
                    reconcile_only=reconcile_only,
                )
            )
            after = getattr(self.gateway, "external_network_request_count", None)
            was_reconciliation = reconcile_only
            # A known returned file may authorize a remaining format correction;
            # only the first resumed invocation is constrained to GET-only.
            reconcile_only = False
            requests = (
                0 if was_reconciliation else
                max(1, after - before)
                if type(before) is int and type(after) is int
                else 1
            )
            run.record_usage(result, "image", requests)
            if was_reconciliation:
                run.usage[-1]["actual_cost_micros"] = 0
            from .provider_diagnostics import retained_metrics

            run.usage[-1]["metrics"] = retained_metrics(getattr(result, "raw_usage", None))
            run.usage[-1]["http_status"] = getattr(result, "http_status", None)
            run.usage[-1]["provider_request_id"] = getattr(result, "provider_request_id", None)
            run.usage[-1].update(
                http_status=result.http_status,
                error_code=result.error_code,
                metrics=(result.raw_usage or {}).get("metrics", {}),
            )
            if result.outcome == ProviderOutcome.OUTCOME_UNKNOWN:
                error: ReplicaImageError = ReplicaImageOutcomeUnknown(result.error_message or "生图结果尚未核实。")
                error.provider_request_id = getattr(result, "provider_request_id", None) or ledger[token].get("provider_request_id")
                error.http_status = result.http_status
                error.provider_metrics = retained_metrics(result.raw_usage)
                error.provider_metrics.setdefault("image_recovery", {})["automatic_resumptions"] = ledger[token].get("reconciliation_attempts", 0)
                ledger[token].update(
                    provider_request_id=error.provider_request_id,
                    http_status=error.http_status,
                    provider_metrics=error.provider_metrics,
                    error_code=result.error_code,
                )
                write_json(ledger_path, ledger)
                await self._save(run, request.job_id)
                raise error
            if result.outcome != ProviderOutcome.SUCCEEDED or result.artifact is None:
                ledger[token] = {
                    **ledger[token],
                    "state": "rejected",
                    "http_status": result.http_status,
                    "error_code": result.error_code,
                    "provider_request_id": getattr(result, "provider_request_id", None),
                    "provider_metrics": retained_metrics(result.raw_usage),
                }
                write_json(ledger_path, ledger)
                await self._save(run, request.job_id)
                error = ReplicaImageError(result.error_message or "生图未返回可核实产物。")
                error.code = result.error_code or "REPLICA_IMAGE_FAILED"
                error.provider_request_id = getattr(result, "provider_request_id", None)
                error.http_status = result.http_status
                error.provider_metrics = retained_metrics(result.raw_usage)
                raise error
            if result.artifact.locally_resampled:
                raise ReplicaNativeError(
                    "供应商返回了本地缩放产物，原生画幅验收未通过。"
                )
            suffix = f"-format-{format_attempt + 1}" if format_attempt else ""
            path = run.root / "artwork" / f"{request.job_id}-{key}-{token}{suffix}.png"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(result.artifact.payload)
            with Image.open(path) as image:
                actual = image.size
            matches = _canvas_matches(canvas, actual, minimum_edge)
            native_admitted = native_delivery and native_budget_matches(canvas, actual)
            candidates = ledger[token].get("canvas_attempts", [])
            candidates.append(
                {
                    "path": str(path),
                    "sha256": digest(path.read_bytes()),
                    "size": list(actual),
                    "accepted_canvas": matches,
                    "dispatch_prompt_sha256": digest(attempt_prompt.encode("utf-8")),
                }
            )
            ledger[token] = {
                **ledger[token],
                "state": "returned" if matches else "canvas_rejected",
                "path": str(path),
                "sha256": digest(path.read_bytes()),
                "actual_size": list(actual),
                "canvas_attempts": candidates,
            }
            if not matches and native_admitted:
                ledger[token]["delivery_canvas"] = delivery_receipt(
                    canvas, actual, ledger[token]["sha256"]
                )
            write_json(ledger_path, ledger)
            await self._save(run, request.job_id)
            if matches or native_admitted:
                return path
        raise ReplicaCanvasMismatch(canvas, actual, len(ledger[token]["canvas_attempts"]))

    async def _exact(
        self,
        run: NativeReplicaRun,
        request: ProviderRequest,
        retry_candidates: dict[str, RetryArtwork] | None = None,
    ) -> list[str]:
        while run.state()["state"] not in {"SET_COMPLETE", "FINALIZED"}:
            state = run.state()
            if state["state"] in {"READY", "REPAIR_PREPARED"}:
                path = run.root / "request.json"
                run.cli("generate-next", session=run.run / "session.json", out=path)
                await self._save(run, request.job_id)
            native = read_json(run.root / "request.json")
            if run.state()["state"] == "AWAITING_ARTWORK":
                canvas = native["canvas"]
                image = await self._render(
                    run,
                    request,
                    native,
                    native["prompt"],
                    [Path(p) for p in native["reference_images"]],
                    [canvas["width"], canvas["height"]],
                    native["module_id"],
                    retry_candidate=(retry_candidates or {}).get(native["module_id"]),
                )
                run.cli(
                    "record-artwork",
                    session=run.run / "session.json",
                    token=native["token"],
                    image=image,
                )
                await self._save(run, request.job_id)
            if run.state()["state"] == "AWAITING_AUDIT":
                state = run.state()
                module = state["plan"]["modules"][state["module_index"]]
                artwork = state["modules"][state["module_index"]]["attempts"][-1][
                    "artwork"
                ]
                expected = module["output_canvas"]
                canvas = [expected["width"], expected["height"]]
                with Image.open(artwork) as audited_image:
                    if not _canvas_matches(canvas, audited_image.size, min(canvas)):
                        raise ReplicaCanvasMismatch(canvas, audited_image.size)
                await run.contract(
                    "exact-pixel-audit-" + native["token"],
                    "Fresh side-by-side PIXEL audit. Return judgment schema, unique observations/evidence per dimension. "
                    "First observe the attached reference itself, then the artwork. Determine "
                    "actual font-size tiers, dominant line groups, subject bounds and text "
                    "carriers from pixels; a small connector is not a giant headline. Prior "
                    "metadata may be wrong and never overrules reference pixels. "
                    "For product forms actually visible in this module, compare cross-section, ratios, segments and layers; "
                    "check label spelling and complete numerals only when packaging or those marks belong in this module. "
                    "Independently verify module_constraints against the attached reference pixels: never demand an absent "
                    "package in a contents, ingredient or scene module, and never waive visible product mismatches. "
                    "product_geometry_match must truthfully reflect actual pixels; list all material deltas. No all-pass template. "
                    "Reassess prior_pixel_reviews observations against current pixels: these are fallible questions, "
                    "not inherited verdicts. Explain unresolved and resolved concerns in the relevant comparison evidence.",
                    {
                        **exact_review_context(module, run.context),
                        "artwork": artwork,
                        "artwork_sha256": digest(Path(artwork).read_bytes()),
                        "geometry": state["input"]["product_geometry"],
                        "prior_pixel_reviews": prior_pixel_reviews(
                            (retry_candidates or {}).get(module["id"]),
                            state["modules"][state["module_index"]],
                            request.job_id,
                        ),
                    },
                    "audit-current",
                    "judgment",
                    schemas=("judgment.schema.json",),
                    paths=[
                        *sorted((run.root / "inputs").glob("product-*")),
                        Path(module["reference_path"]),
                        Path(artwork),
                    ],
                )
                await self._save(run, request.job_id)
            state = run.state()
            if state["state"] == "REPAIR_READY":
                module = state["plan"]["modules"][state["module_index"]]
                artwork = state["modules"][state["module_index"]]["attempts"][-1][
                    "artwork"
                ]
                await run.contract(
                    "exact-repair-" + native["token"],
                    "Return {material_failures:[every observed major_mismatches in exact order],"
                    "single_edit_instruction:one integrated targeted repair}. Inspect attached pixels: "
                    "last image is the failed canvas, preceding image is the composition blueprint, "
                    "earlier images are product originals. Specify spatial deltas in viewer/image "
                    "coordinates; do not conflate subject-anatomical left/right with image left/right. "
                    "Use the hand or edge nearest the target image position rather than an ambiguous "
                    "handedness label. Preserve correct regions and product identity.",
                    {
                        **exact_review_context(module, run.context),
                        "failed_judgment": state["modules"][state["module_index"]][
                            "attempts"
                        ][-1]["judgment"],
                        "artwork": artwork,
                        "geometry": state["input"]["product_geometry"],
                        "prior_pixel_reviews": prior_pixel_reviews(
                            (retry_candidates or {}).get(module["id"]),
                            state["modules"][state["module_index"]],
                            request.job_id,
                        ),
                    },
                    "prepare-repair",
                    "repair-plan",
                    paths=[
                        *sorted((run.root / "inputs").glob("product-*")),
                        Path(module["reference_path"]),
                        Path(artwork),
                    ],
                )
            elif state["state"] not in {"READY", "REPAIR_PREPARED", "SET_COMPLETE"}:
                raise ReplicaNativeError("一比一原生状态无法继续：" + state["state"])
        if run.state()["state"] != "FINALIZED":
            run.cli(
                "finalize",
                session=run.run / "session.json",
                output=run.root / "delivery.json",
            )
        return [x["artwork"] for x in read_json(run.root / "delivery.json")["outputs"]]

    @staticmethod
    def _style_render_inputs(
        run: NativeReplicaRun, native: dict[str, Any], handoff: dict[str, Any], key: str
    ) -> tuple[str, list[Path]]:
        prompt = Path(handoff["prompt_path"]).read_text(encoding="utf-8-sig")
        paths = [Path(p) for p in handoff["referenced_image_paths"]]
        if native.get("revision_index", 0):
            state = run.state()
            audit = next(
                (
                    row
                    for row in reversed(state["audits"])
                    if row["target_id"] == key and row["pass"] is False
                ),
                None,
            )
            artwork = next(
                (row for row in reversed(state["artwork"]) if row["target_id"] == key),
                None,
            )
            if audit is None or artwork is None:
                raise ReplicaNativeError("修复缺少当前目标的真实失败审计或初稿。")
            image = run.path(artwork["path"])
            if digest(image.read_bytes()) != audit["artwork_sha256"]:
                raise ReplicaNativeError("修复初稿与失败审计哈希不一致。")
            paths.append(image)
            prompt += (
                "\n【本次唯一一次修复】最后一张参考图是当前商品初稿，不是风格参考。保留已正确的画面、产品身份和实体标签，只修复下列实际差异；全部文字仍由模型原生生成，禁止后期叠字。\n"
                + json.dumps(audit["evidence"], ensure_ascii=False)
            )
        return prompt, paths

    async def _style(
        self, run: NativeReplicaRun, request: ProviderRequest
    ) -> list[str]:
        state = run.state()
        if state["state"] == "DELIVERED":
            return [
                x["path"]
                for x in read_json(run.run / "artifacts/delivery-manifest.json")[
                    "files"
                ]
            ]
        detail = state["route"] == "DETAIL_PAGE"
        pool_name, target_arg = (
            ("groups", "group-id") if detail else ("units", "unit-id")
        )
        suffix = "pair" if detail else "single"
        for key in state[pool_name]:
            while run.state()[pool_name][key]["status"] != "accepted":
                state = run.state()
                status = state[pool_name][key]["status"]
                if status == "failed_continue":
                    raise ReplicaNativeError(
                        "风格复刻在一次修复后仍未通过80%及商品一致性验收。"
                    )
                if status == "revision_allowed":
                    run.cli(
                        "prepare-revision" if detail else "prepare-single-revision",
                        workspace=run.run,
                        **{target_arg: key},
                    )
                    status = "pending"
                if status == "pending":
                    run.cli(
                        f"generate-{suffix}-request",
                        workspace=run.run,
                        **{target_arg: key},
                    )
                    await self._save(run, request.job_id)
                native_path = run.run / f"artifacts/generation-request-{key}.json"
                native = read_json(native_path)
                if run.state()[pool_name][key]["status"] == "awaiting_artwork":
                    # Run the source adapter's exact generation gate and prompt compiler;
                    # use Polaris' existing Artflow/Tengli transport for credentials/failover.
                    handoff = await asyncio.to_thread(
                        self._style_handoff, run, native_path
                    )
                    prompt, image_paths = self._style_render_inputs(
                        run, native, handoff, key
                    )
                    image = await self._render(
                        run,
                        request,
                        native,
                        prompt,
                        image_paths,
                        native["render_contract"]["native_generation_size"],
                        key,
                    )
                    receipt = run.root / "artwork" / (image.stem + ".receipt.json")
                    self._provenance(run, native_path, image, receipt)
                    run.cli(
                        f"record-{suffix}-artwork",
                        workspace=run.run,
                        artwork=image,
                        **{target_arg: key, "render-receipt": receipt},
                    )
                    await self._save(run, request.job_id)
                state = run.state()
                item = state[pool_name][key]
                if item["status"] == "audit_required":
                    artwork = item["artwork"]["path"]
                    await run.contract(
                        "style-pixel-audit-" + native["token"],
                        "Inspect actual artwork against all product originals and mapped reference panels. Return native audit.schema.json. "
                        "review_kind=visual_model, artwork_sha256 matches supplied bytes, truthful per-mechanism scores and dimensions, "
                        "density_checks/transition_checks, product_identity and fact_safety. All eight scores must be >=0.8 to pass; "
                        "identity/shape/label/numeral or contamination errors are hard failures. Do not forge passing evidence.",
                        {"workflow": state, "request": native, "current_target": key},
                        f"audit-{suffix}",
                        "audit",
                        schemas=("audit.schema.json",),
                        **{target_arg: key},
                        paths=[
                            *sorted((run.root / "inputs").glob("product-*")),
                            *[
                                Path(p["source_crop_path"])
                                for p in state["overview_detection"]["panels"]
                            ],
                            Path(artwork),
                        ],
                    )
                    await self._save(run, request.job_id)
            run.cli(f"accept-{suffix}", workspace=run.run, **{target_arg: key})
        if run.state()["state"] not in {"FINALIZED", "DELIVERED"}:
            await run.contract(
                "style-global-audit",
                "Fresh full-set audit: check complete order, identity, visual consistency, no missing/extra screens or contamination. Return {review_kind:visual_model,pass,ordered_ids,evidence:[concrete set findings]}. Fail honestly.",
                run.state(),
                "audit-global",
                "audit",
                paths=[
                    Path(x["artwork"]["path"]) for x in run.state()[pool_name].values()
                ],
            )
            run.cli("finalize", workspace=run.run)
        if run.state()["state"] != "DELIVERED":
            run.cli(
                "export-single-screens" if detail else "export-single",
                workspace=run.run,
            )
        return [
            x["path"]
            for x in read_json(run.run / "artifacts/delivery-manifest.json")["files"]
        ]

    @staticmethod
    def _style_handoff(run: NativeReplicaRun, native_path: Path) -> dict[str, Any]:
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                str(run.skill_root / "scripts/imagegen_adapter.py"),
                "--workspace",
                str(run.run),
                "--request",
                str(native_path),
                "--output",
                str(run.root / "native-output.png"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            shell=False,
        )
        if result.returncode:
            raise ReplicaNativeError((result.stderr or result.stdout)[-3000:])
        import json

        return json.loads(result.stdout)

    @staticmethod
    def _provenance(
        run: NativeReplicaRun, native_path: Path, image: Path, receipt: Path
    ) -> None:
        import subprocess
        import sys

        if receipt.exists():
            return
        result = subprocess.run(
            [
                sys.executable,
                str(run.skill_root / "scripts/render_provenance.py"),
                "--request",
                str(native_path),
                "--native-output",
                str(image),
                "--artwork",
                str(image),
                "--output",
                str(receipt),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            shell=False,
        )
        if result.returncode:
            raise ReplicaNativeError((result.stderr or result.stdout)[-3000:])
