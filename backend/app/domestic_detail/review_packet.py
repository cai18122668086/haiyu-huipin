"""Bind human final review to original accepted screens and accessible masters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import ZIP_STORED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..archive_response import close_archive, open_archive
from ..aurora_detail_runtime import AuroraDetailHarnessRunner
from ..database import read_snapshot_async
from ..models import AssetRecord, JobAssetRecord, JobPlanRecord, JobRecord
from ..storage import ImageStorage, StoredImage
from .delivery import (
    DetailDeliveryError,
    _inspect,
    _json,
    _once,
    _reject,
    _verified_context,
)
from .plans import canonical_hash

REVIEW_VERSION = "domestic-detail-final-review-v1"
MASTER_ROLE = "detail_pair_canvas"


def _metadata(stored: StoredImage) -> dict[str, Any]:
    return {
        name: getattr(stored, name)
        for name in (
            "content_type",
            "byte_size",
            "width",
            "height",
            "sha256",
            "storage_provider",
            "storage_bucket",
            "object_key",
            "object_version",
            "etag",
        )
    }


async def _read(
    storage: ImageStorage, key: str, version: str = ""
) -> tuple[bytes, StoredImage]:
    try:
        return await storage.read_stored_image(key, version)
    except Exception as exc:
        raise DetailDeliveryError(
            "NATIVE_DETAIL_DELIVERY_STORAGE_UNAVAILABLE",
            "Original delivery object or its pinned version is unavailable",
            503,
        ) from exc


async def read_pair_master(storage: ImageStorage, asset: AssetRecord) -> bytes:
    """Read the registered exact version, including when making a thumbnail."""
    payload, stored = await _read(storage, asset.object_key, asset.object_version)
    if _metadata(stored) != {name: getattr(asset, name) for name in _metadata(stored)}:
        _reject("The registered pair master or its storage metadata changed")
    return payload


def _master_record(
    db: Session,
    job: JobRecord,
    pair: Mapping[str, Any],
    stored: StoredImage,
    context: Mapping[str, Any],
    *,
    register: bool,
) -> AssetRecord:
    identity = {
        "workspace_id": job.workspace_id,
        **{
            name: getattr(stored, name)
            for name in (
                "storage_provider",
                "storage_bucket",
                "object_key",
                "object_version",
            )
        },
    }
    asset_id = "ast_" + canonical_hash(identity)[:32]
    proof = {
        "native_detail_pair_master": True,
        "pair_id": pair["pair_id"],
        "pixel_lineage_sha256": pair["pixel_lineage_sha256"],
        "generation_binding_sha256": pair.get(
            "generation_binding_sha256", context["generation_binding_sha256"]
        ),
    }
    asset = db.get(AssetRecord, asset_id)
    if asset is None and register:
        source_assets = [
            db.get(AssetRecord, row["asset_id"]) for row in context["screens"]
        ]
        expiries = [
            a.expires_at
            for a in source_assets
            if a is not None and a.expires_at is not None
        ]
        asset = AssetRecord(
            id=asset_id,
            workspace_id=job.workspace_id,
            media_kind="image",
            origin_kind="generated",
            lifecycle_status="ready",
            original_filename=pair["pair_id"] + Path(stored.filename).suffix,
            asset_metadata=proof,
            expires_at=min(expiries) if expiries else None,
            **_metadata(stored),
        )
        db.add(asset)
        db.flush()
    if (
        asset is None
        or asset.workspace_id != job.workspace_id
        or asset.lifecycle_status != "ready"
        or asset.deleted_at is not None
        or asset.asset_metadata != proof
        or _metadata(stored)
        != {name: getattr(asset, name) for name in _metadata(stored)}
    ):
        _reject("Accepted pair-master registration is missing or changed")
    relation = db.scalar(
        select(JobAssetRecord).where(
            JobAssetRecord.workspace_id == job.workspace_id,
            JobAssetRecord.job_id == job.id,
            JobAssetRecord.role == MASTER_ROLE,
            JobAssetRecord.ordinal == pair["ordinal"],
        )
    )
    expected = {
        **proof,
        "module_ids": pair["module_ids"],
        "sha256": stored.sha256,
    }
    if relation is None and register:
        relation = JobAssetRecord(
            workspace_id=job.workspace_id,
            job_id=job.id,
            job_item_id=None,
            asset_id=asset.id,
            direction="intermediate",
            role=MASTER_ROLE,
            ordinal=pair["ordinal"],
            is_deliverable=False,
            validation=expected,
        )
        db.add(relation)
        db.flush()
    if (
        relation is None
        or relation.asset_id != asset.id
        or relation.direction != "intermediate"
        or relation.is_deliverable
        or relation.job_item_id is not None
        or relation.validation != expected
    ):
        _reject("The accepted pair-master job relation changed")
    return asset


def _document(packet: Mapping[str, Any]) -> str:
    automatic = packet.get("delivery_mode") == "AUTO_FINALIZE_AUTHORIZED"
    lines = [
        "# 国内详情自动交付记录" if automatic else "# 国内详情最终审阅",
        "",
        f"任务：{packet['job_id']}；方案版本：{packet['plan_revision']}",
        f"详情规范：{packet['skill_version']}",
        "",
        "## 审核范围",
        "",
        "已记录逐屏与组内像素审核。整页节奏、组间接缝和长图审核未执行。",
        "单屏与双屏母版可直接人工审阅并交付；长图预览为可选步骤。",
        "",
        "## 接受的单屏（按顺序）",
        "",
        "| 屏 | 原生状态 | 图片指纹 |",
        "|---|---|---|",
    ]
    for row in packet["screens"]:
        lines.append(f"| {row['module_id']} | {row['status']} | {row['sha256']} |")
    lines.extend(["", "## 接受的双屏母版", ""])
    for row in packet["pair_canvases"]:
        lines.append(
            f"{row['pair_id']}：{' / '.join(row['module_ids'])}；{row['sha256']}"
        )
    lines.extend(["", "## 仍需接受的残差", ""])
    if packet["residual_issues"]:
        for row in packet["residual_issues"]:
            lines.append(f"{row['module_id']}：")
            lines.append(
                json.dumps(row["issues"], ensure_ascii=False, indent=2, allow_nan=False)
            )
    else:
        lines.append("接受的逐屏记录未列出材料残差。这不表示已完成整页质量审核。")
    supplement = packet.get("supplementary_campaign_review")
    if supplement:
        report = supplement["report"]
        lines.extend(
            [
                "",
                "## 独立整页审核补充记录",
                "",
                "上述整页未审核描述仅指原详情会话；以下为明确选择的独立审核记录，不改写原生会话的审核范围。",
                f"审核任务：{supplement['review_job_id']}；报告指纹：{supplement['report_sha256']}",
                f"原始回复状态：{supplement['original_message_state']}；"
                f"原生审核接受：{report.get('native_gate_accepted') is True}",
                "原始观察、校验拒绝原因及残差：",
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
                "可用原文、请求证据和实际所见图片随交付包保存；该报告不改变原任务的费用状态。",
            ]
        )
    if automatic:
        lines.extend(
            [
                "",
                "## 方案阶段的自动交付授权",
                "",
                packet["automatic_delivery_authorization"]["authorization_text"],
                "以上记录依据方案阶段的明确授权，不表示用户已经逐屏审阅最终成图。逐屏/组内观察与残差保留，整页审核范围不因此扩大。",
                "",
            ]
        )
    else:
        lines.extend(["", "最终确认表示接受以上图片、审核范围及所列残差。", ""])
    return "\n".join(lines)


async def _packet(
    db: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    storage: ImageStorage,
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    register: bool,
    selection: Mapping[str, Any] | None = None,
    frozen: Mapping[str, Any] | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    supplement = frozen
    if selection is not None:
        from .campaign_selection import select_campaign_review

        supplement, _ = select_campaign_review(db, job, plan, context, state, selection)
    masters = []
    for ordinal, row in enumerate(context["pair_canvases"]):
        pair = {**row, "ordinal": ordinal}
        _, stored = await _read(storage, pair["object_key"])
        if (
            (stored.sha256, stored.width, stored.height)
            != (pair["sha256"], pair["width"], pair["height"])
            or stored.storage_provider != storage.provider_name
            or stored.storage_bucket != storage.bucket_name
        ):
            _reject("Original accepted pair-master bytes or storage changed")
        asset = (
            await read_snapshot_async(
                db, _master_record, db, job, pair, stored, context, register=register
            )
            if read_only
            else _master_record(db, job, pair, stored, context, register=register)
        )
        masters.append({**pair, **_metadata(stored), "asset_id": asset.id})
    screens = []
    for row, module in zip(context["screens"], state["modules"], strict=True):
        screen_asset = (
            await read_snapshot_async(db, db.get, AssetRecord, row["asset_id"])
            if read_only
            else db.get(AssetRecord, row["asset_id"])
        )
        assert screen_asset is not None
        _, stored = await _read(
            storage, screen_asset.object_key, screen_asset.object_version
        )
        if _metadata(stored) != {
            name: getattr(screen_asset, name) for name in _metadata(stored)
        }:
            _reject("Original accepted screen version or metadata changed")
        screens.append({**row, "status": module["status"]})
    residuals = [
        {"module_id": module["id"], "issues": deepcopy(module["residual_issues"])}
        for module in state["modules"]
        if module["residual_issues"]
    ]
    value = {
        "version": REVIEW_VERSION,
        "job_id": job.id,
        "plan_id": plan.id,
        "plan_revision": plan.revision,
        "skill_version": (AuroraDetailHarnessRunner().skill_root / "VERSION")
        .read_text()
        .strip(),
        "context_sha256": canonical_hash(context),
        "generation_binding_sha256": context["generation_binding_sha256"],
        "quality_state": "WITH_RESIDUALS" if residuals else "PAIR_REVIEW_PASSED",
        "campaign_audit_status": "NOT_REQUESTED",
        "screens": screens,
        "pair_canvases": masters,
        "residual_issues": residuals,
    }
    if supplement is not None:
        value["supplementary_campaign_review"] = dict(supplement)
        report = supplement["report"]
        if report.get("native_gate_accepted") is not True or report.get(
            "residual_checks"
        ):
            value["quality_state"] = "WITH_RESIDUALS"
    from .delivery_authorization import (
        AUTO_MODE,
        job_receipt_authorization,
    )

    authorization = (
        await read_snapshot_async(db, job_receipt_authorization, db, job, plan)
        if read_only
        else job_receipt_authorization(db, job, plan)
    )
    if authorization is not None:
        value["delivery_mode"] = AUTO_MODE
        value["automatic_delivery_authorization"] = authorization
    value["document_markdown"] = _document(value)
    return {**value, "sha256": canonical_hash(value)}


async def prepare_final_review(
    db: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    storage: ImageStorage,
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Register existing masters and persist an immutable human-review packet."""
    context, state, root = await _verified_context(db, job, plan, storage)
    from .delivery_authorization import receipt_authorization

    if selection is not None and receipt_authorization(plan.payload) is not None:
        _reject(
            "Original automatic delivery keeps its preauthorized scope; "
            "later reviews remain independent"
        )
    if job.status == "completed":
        if selection is not None:
            _reject("A completed delivery keeps its originally accepted review")
        fingerprint = plan.payload["final_manifest"]["final_review"]["sha256"]
        return await validate_final_review(
            db, job, plan, storage, context, state, root, fingerprint
        )
    packet = await _packet(
        db, job, plan, storage, context, state, register=True, selection=selection
    )
    if selection is not None:
        from .campaign_selection import preserve_selection, select_campaign_review

        checked, files = select_campaign_review(
            db, job, plan, context, state, selection
        )
        if canonical_hash(checked) != canonical_hash(
            packet["supplementary_campaign_review"]
        ):
            _reject("Selected evidence changed during snapshot creation")
        preserve_selection(root / "final-review" / packet["sha256"], files)
    path = root / "final-review" / packet["sha256"] / "packet.json"
    if path.exists():
        if canonical_hash(_json(path)) != canonical_hash(packet):
            _reject("Original human-review packet changed")
    else:
        _once(path, packet)
    return packet


async def validate_final_review(
    db: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    storage: ImageStorage,
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    root: Path,
    fingerprint: str | None,
    *,
    read_only: bool = False,
) -> dict[str, Any]:
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(c not in "0123456789abcdef" for c in fingerprint)
    ):
        _reject(
            "Review the current final packet before confirming delivery",
            "NATIVE_DETAIL_FINAL_REVIEW_REQUIRED",
        )
    if job.status == "completed":
        from .campaign_selection import verify_accepted_review_anchor

        verify_accepted_review_anchor(root, job, plan, context, state, fingerprint)
    packet = _json(root / "final-review" / fingerprint / "packet.json")
    if (
        packet.get("sha256") != fingerprint
        or canonical_hash({k: v for k, v in packet.items() if k != "sha256"})
        != fingerprint
    ):
        _reject("The original final-review packet changed")
    supplement = packet.get("supplementary_campaign_review")
    if supplement is not None:
        from .campaign_selection import frozen_selection

        frozen_selection(
            root / "final-review" / fingerprint, supplement, job.id, context
        )
    expected = await _packet(
        db,
        job,
        plan,
        storage,
        context,
        state,
        register=False,
        selection=supplement if job.status != "completed" else None,
        frozen=supplement if job.status == "completed" else None,
        read_only=read_only,
    )
    if (
        canonical_hash(packet) != canonical_hash(expected)
        or expected["sha256"] != fingerprint
    ):
        _reject(
            "The reviewed result or material residuals changed",
            "NATIVE_DETAIL_FINAL_REVIEW_STALE",
        )
    return packet


async def delivery_archive(
    db: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    storage: ImageStorage,
) -> BinaryIO:
    if job.status != "completed":
        _reject("Final human review is required before downloading this delivery")
    context, state, root = await _verified_context(
        db, job, plan, storage, read_only=True
    )
    public = plan.payload["final_manifest"]
    packet = await validate_final_review(
        db,
        job,
        plan,
        storage,
        context,
        state,
        root,
        public["final_review"]["sha256"],
        read_only=True,
    )
    original_path = Path(state["final"]["manifest"])
    inspection = await run_in_threadpool(_inspect, root, original_path)
    if inspection["manifest_file_sha256"] != public["native_manifest_sha256"] or any(
        canonical_hash(public.get(k)) != canonical_hash(v)
        for k, v in inspection["manifest"].items()
    ):
        _reject("Native final approval differs from the recorded delivery")
    output = await run_in_threadpool(open_archive, root / "download-cache")
    archive = None
    try:
        archive = await run_in_threadpool(ZipFile, output, "w", compression=ZIP_STORED)
        for row in packet["screens"]:
            asset = await read_snapshot_async(db, db.get, AssetRecord, row["asset_id"])
            assert asset is not None
            payload, _ = await _read(storage, asset.object_key, asset.object_version)
            if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                _reject("A screen changed while building the delivery archive")
            await run_in_threadpool(
                archive.writestr,
                "screens/" + row["module_id"] + Path(asset.original_filename).suffix,
                payload,
            )
        for row in packet["pair_canvases"]:
            asset = await read_snapshot_async(db, db.get, AssetRecord, row["asset_id"])
            assert asset is not None
            await run_in_threadpool(
                archive.writestr,
                "pair-masters/" + asset.original_filename,
                await read_pair_master(storage, asset),
            )
        await run_in_threadpool(
            archive.writestr,
            "final-review.md",
            packet["document_markdown"].encode("utf-8"),
        )
        await run_in_threadpool(
            archive.writestr,
            "final-review.json",
            json.dumps(packet, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        if packet.get("supplementary_campaign_review") is not None:
            from .campaign_selection import frozen_selection

            files = frozen_selection(
                root / "final-review" / packet["sha256"],
                packet["supplementary_campaign_review"],
                job.id,
                context,
            )
            for name, data in files.items():
                await run_in_threadpool(
                    archive.writestr, "supplementary-review/" + name, data
                )
        await run_in_threadpool(
            archive.writestr,
            "native-final-manifest.json",
            await run_in_threadpool(original_path.read_bytes),
        )
        await run_in_threadpool(archive.close)
        return output
    except BaseException:
        await run_in_threadpool(close_archive, archive, output)
        raise
