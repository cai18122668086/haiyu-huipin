"""Publish verified files from terminal replica checkpoints without generating."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import (
    AssetRecord,
    JobAssetRecord,
    JobItemRecord,
    JobRecord,
    ReplicaDocumentRecord,
    utc_now,
)
from .replica_canvas import native_budget_matches
from .replica_failure import failure_fields
from .replica_native import ReplicaNativeError, contained, digest
from .replica_retry import _document
from .storage import ImageStorage


async def recover_outputs(
    session: Session,
    storage: ImageStorage,
    document: ReplicaDocumentRecord,
    job: JobRecord,
) -> int:
    if job.status not in {"failed", "partial", "cancelled"}:
        raise ApiError(
            409, "REPLICA_RECOVERY_NOT_READY", "请等待当前任务结束后找回图片。"
        )
    if (
        job.workspace_id != document.workspace_id
        or job.configuration.get("replica_session_id") != document.id
    ):
        raise ApiError(404, "REPLICA_JOB_NOT_FOUND", "任务不属于当前复刻。")
    execution = document.payload.get("native_executions", {}).get(job.id, {})
    snapshot = execution.get("snapshot")
    if not snapshot:
        return 0
    # Restore only the terminal job's immutable checkpoint, never the shared
    # runtime directory which another retry may currently be writing.
    files = snapshot.get("files", {})
    if "completion-session.json" in files:
        state = _document(snapshot, "completion-session.json")
        plan = _document(snapshot, "completion-plan.json")
        from .replica_exact_contracts import canonical_hash

        if (
            plan.get("document_id") != document.id
            or plan.get("workspace_id") != document.workspace_id
            or state.get("plan_sha256") != canonical_hash(plan)
        ):
            raise ReplicaNativeError("找回图片的方案归属或哈希不一致。")
        modules = state["modules"]
        canvases = [unit["canvas"] for unit in plan["units"]]
    elif "direct-session.json" in files:
        state = _document(snapshot, "direct-session.json")
        modules = state["modules"]
        canvases = [
            [m["output_canvas"]["width"], m["output_canvas"]["height"]]
            for m in state["plan"]["modules"]
        ]
    else:
        return 0
    from .replica_retained_canvas import retained_module

    modules = [
        retained_module(
            snapshot, module, canvas, f"{job.workspace_id}/replica/{document.id}/"
        ) or module
        for module, canvas in zip(modules, canvases, strict=True)
    ]
    items = list(
        session.scalars(
            select(JobItemRecord)
            .where(
                JobItemRecord.job_id == job.id,
                JobItemRecord.workspace_id == document.workspace_id,
            )
            .order_by(JobItemRecord.ordinal)
        )
    )
    if len(items) != len(modules) or len(canvases) != len(modules):
        raise ReplicaNativeError("找回图片的屏数不一致。")
    verified = []
    for item, module, canvas in zip(items, modules, canvases, strict=True):
        if module.get("status") != "GENERATED":
            continue
        relative = (
            contained(Path(snapshot["root"]), module["artwork"])
            .relative_to(Path(snapshot["root"]).resolve())
            .as_posix()
        )
        record = files.get(relative, {})
        key = record.get("object_key", "")
        if not key.startswith(
            f"{job.workspace_id}/replica/{document.id}/"
        ) or record.get("sha256") != module.get("sha256"):
            raise ReplicaNativeError("找回图片缺少当前工作区的持久哈希绑定。")
        raw = await storage.read_bytes(key)
        if digest(raw) != module["sha256"]:
            raise ReplicaNativeError("已生成图片的哈希发生变化，已停止恢复。")
        with Image.open(BytesIO(raw)) as image:
            image.load()
            size, mime = image.size, Image.MIME[image.format or "PNG"]
        if module.get("recovered_canvas_verdict") and list(size) != module["size"]:
            raise ReplicaNativeError("保留画幅的真实尺寸与原调用回执不一致。")
        if not native_budget_matches(canvas, size):
            # Small test/provider canvases can be exact matches as well.
            from .replica_native_provider import _canvas_matches

            if not _canvas_matches(canvas, size):
                raise ReplicaNativeError("找回图片的画幅与原方案不一致。")
        verified.append((item, module, key, raw, size, mime))
    # Only the legacy whole-group failure lived on an already generated item.
    # Usage is cumulative across retries; it is not the current failure record.
    misplaced_legacy_failure = any(
        item.status == "failed" and item.error_code == "REPLICA_SKILL_BLOCKED"
        for item, *_ in verified
    )
    recovered_job_error = any(
        item.status == "failed" and item.error_code == job.error_code
        for item, *_ in verified
    )
    count = 0
    for item, module, key, raw, size, mime in verified:
        link = session.scalar(
            select(JobAssetRecord).where(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.job_item_id == item.id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.is_deliverable.is_(True),
            )
        )
        if link is None:
            asset = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.workspace_id == job.workspace_id,
                    AssetRecord.storage_provider == storage.provider_name,
                    AssetRecord.storage_bucket == storage.bucket_name,
                    AssetRecord.object_key == key,
                    AssetRecord.object_version == "",
                )
            )
            if asset is None:
                asset = AssetRecord(
                    id="asset_" + uuid4().hex,
                    workspace_id=job.workspace_id,
                    media_kind="image",
                    origin_kind="generated",
                    lifecycle_status="ready",
                    original_filename=f"replica-{item.ordinal + 1:03}.png",
                    content_type=mime,
                    byte_size=len(raw),
                    width=size[0],
                    height=size[1],
                    sha256=module["sha256"],
                    storage_provider=storage.provider_name,
                    storage_bucket=storage.bucket_name,
                    object_key=key,
                    asset_metadata={
                        "recovered_from_job": job.id,
                        "review_status": "NOT_REVIEWED",
                        **({"delivery_canvas": module["delivery_canvas"]}
                           if "delivery_canvas" in module else {}),
                    },
                )
                session.add(asset)
                session.flush()
            if asset.deleted_at or asset.purged_at:
                raise ReplicaNativeError("图片已删除，不能从检查点重新发布。")
            session.add(
                JobAssetRecord(
                    workspace_id=job.workspace_id,
                    job_id=job.id,
                    job_item_id=item.id,
                    asset_id=asset.id,
                    direction="output",
                    role="reference_remix_result",
                    ordinal=item.ordinal,
                    is_deliverable=True,
                    validation={
                        "sha256": module["sha256"],
                        "actual_width": size[0],
                        "actual_height": size[1],
                        "recovered_from_checkpoint": True,
                        "review_status": "NOT_REVIEWED",
                    },
                )
            )
            count += 1
        item.status, item.stage, item.result_source = (
            "succeeded",
            "completed",
            "recovered",
        )
        item.error_code = item.error_message = item.error_details = None
        item.finished_at = item.finished_at or utc_now()
    # Legacy whole-group execution attached its sixth-screen failure to item 0.
    # Move that retained receipt to the actual ungenerated module.
    failed = next(
        (c for c in reversed(snapshot.get("usage", [])) if c.get("error_code")), None
    )
    missing = next(
        (
            i
            for i, m in zip(items, modules, strict=True)
            if m.get("status") != "GENERATED"
        ),
        None,
    )
    if misplaced_legacy_failure and failed and missing and missing.status != "failed":
        error = ReplicaNativeError("上游图片任务未返回可用图片。")
        fields = failure_fields(error, [failed], missing.ordinal)
        details = fields.pop("raw_usage_metrics")
        missing.status, missing.stage = "failed", "failed"
        missing.error_code, missing.error_message = (
            fields["error_code"],
            fields["error_message"],
        )
        ledger = (
            _document(snapshot, "invocations.json")
            if "invocations.json" in files
            else {}
        )
        unknown = any(row.get("state") == "invoked" for row in ledger.values())
        if unknown:
            fields["error_code"] = "PROVIDER_OUTCOME_UNKNOWN"
            fields["error_message"] = (
                f"第 {missing.ordinal + 1} 屏：上游任务结果尚未核实，禁止自动重发。"
                f" 原任务编号：{fields['provider_request_id'] or '尚未返回'}。"
                " 已生成图片已保留。"
            )
            missing.error_code, missing.error_message = (
                fields["error_code"],
                fields["error_message"],
            )
        missing.error_details = {
            **details,
            "provider_request_id": fields["provider_request_id"],
            "provider_outcome": "outcome_unknown" if unknown else "failed",
        }
        job.error_code, job.error_message = missing.error_code, missing.error_message
        job.error_details = missing.error_details
    job.succeeded_item_count = sum(i.status in {"succeeded", "reused"} for i in items)
    job.failed_item_count = sum(i.status == "failed" for i in items)
    if job.succeeded_item_count:
        job.status = (
            "completed" if job.succeeded_item_count == len(items) else "partial"
        )
        job.stage = job.status
        if job.status == "completed" or (
            recovered_job_error and job.failed_item_count == 0
        ):
            job.error_code = job.error_message = job.error_details = None
    job.state_version += 1
    session.flush()
    return count
