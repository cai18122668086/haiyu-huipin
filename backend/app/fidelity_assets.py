"""Private, quota-bound image evidence scoped to a durable authorized attempt."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select

from .models import AssetRecord, JobAssetRecord, JobAttemptRecord, WorkspaceLimitRecord
from .services import new_id
from .storage import ValidatedImage


async def retain(
    sessions: Any,
    storage: Any,
    request: Any,
    artifact: Any,
    *,
    role: str = "fidelity_candidate",
    ordinal: int | None = None,
) -> str:
    if role not in {
        "fidelity_candidate",
        "fidelity_source_cutout",
        "fidelity_background",
    }:
        raise ValueError("Invalid fidelity evidence role")
    stored = None
    try:
        with sessions() as session:
            limits = session.get(
                WorkspaceLimitRecord,
                request.workspace_id,
                with_for_update=True,
            )
            attempt = session.get(JobAttemptRecord, request.attempt_id)
            if attempt is None or (
                attempt.workspace_id,
                attempt.job_id,
                attempt.job_item_id,
                attempt.provider_idempotency_key,
            ) != (
                request.workspace_id,
                request.job_id,
                request.job_item_id,
                request.idempotency_key,
            ):
                raise ValueError("Candidate scope invalid")
            ordinal = (
                attempt.attempt_no * 8 + ordinal
                if role == "fidelity_source_cutout" and ordinal is not None
                else attempt.attempt_no
                if ordinal is None
                else ordinal
            )
            prior = session.scalar(
                select(JobAssetRecord).where(
                    JobAssetRecord.workspace_id == request.workspace_id,
                    JobAssetRecord.job_id == request.job_id,
                    JobAssetRecord.job_item_id == request.job_item_id,
                    JobAssetRecord.role == role,
                    JobAssetRecord.ordinal == ordinal,
                )
            )
            if prior is not None:
                asset = session.get(AssetRecord, prior.asset_id)
                if (
                    asset is None
                    or asset.workspace_id != request.workspace_id
                    or asset.sha256 != artifact.sha256
                ):
                    raise ValueError("Retained candidate binding changed")
                return prior.asset_id
            used = session.scalar(
                select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                    AssetRecord.workspace_id == request.workspace_id,
                    AssetRecord.lifecycle_status == "ready",
                    AssetRecord.deleted_at.is_(None),
                )
            )
            if (
                limits is None
                or int(used or 0) + len(artifact.payload) > limits.storage_quota_bytes
            ):
                raise ValueError(
                    "Private image evidence exceeds workspace storage quota"
                )
            suffix = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
            }.get(artifact.content_type)
            if suffix is None:
                raise ValueError("Invalid fidelity evidence image type")
            scope = hashlib.sha256(
                f"{request.job_item_id}:{request.attempt_id}:{role}".encode()
            ).hexdigest()[:32]
            key = f"{request.workspace_id}/fidelity/{scope}/{artifact.sha256}{suffix}"
            stored = await storage.save_validated(
                ValidatedImage(
                    artifact.payload,
                    artifact.filename,
                    artifact.content_type,
                    artifact.width,
                    artifact.height,
                    key,
                    artifact.sha256,
                )
            )
            asset_id = new_id("asset")
            session.add(
                AssetRecord(
                    id=asset_id,
                    workspace_id=request.workspace_id,
                    media_kind="image",
                    origin_kind="generated",
                    lifecycle_status="ready",
                    original_filename=stored.filename,
                    content_type=stored.content_type,
                    byte_size=stored.byte_size,
                    width=stored.width,
                    height=stored.height,
                    sha256=stored.sha256,
                    storage_provider=stored.storage_provider,
                    storage_bucket=stored.storage_bucket,
                    object_key=stored.object_key,
                    object_version=stored.object_version,
                    etag=stored.etag,
                    asset_metadata={"fidelity_evidence": True, "role": role},
                )
            )
            session.flush()
            session.add(
                JobAssetRecord(
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    job_item_id=request.job_item_id,
                    asset_id=asset_id,
                    direction="intermediate",
                    role=role,
                    ordinal=ordinal,
                    is_deliverable=False,
                )
            )
            session.commit()
            return asset_id
    except BaseException:
        if stored is not None:
            # A database commit can succeed even when its acknowledgement is
            # lost. Never delete an object that may already have a durable row.
            from contextlib import suppress

            with suppress(Exception):
                with sessions() as check:
                    referenced = check.scalar(
                        select(AssetRecord.id).where(
                            AssetRecord.workspace_id == request.workspace_id,
                            AssetRecord.object_key == stored.object_key,
                        )
                    )
                if referenced is None:
                    await storage.delete_object(
                        stored.object_key, stored.object_version
                    )
        raise
