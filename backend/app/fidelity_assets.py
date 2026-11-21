"""Private, quota-bound image evidence scoped to a durable authorized attempt."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select

from .models import (
    AssetRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    WorkspaceLimitRecord,
)
from .provider import ProviderArtifact
from .services import new_id
from .storage import ValidatedImage


def prior_recolor_evidence(
    sessions: Any, request: Any, source: Any
) -> dict[str, Any] | None:
    """Reuse only this item's verified source-only recipe after reader failure."""
    from .services import canonical_hash
    from .source_recolor import VERSION

    with sessions() as session:
        job = session.get(JobRecord, request.job_id)
        item = session.get(JobItemRecord, request.job_item_id)
        seen = set()
        while job and item and job.retry_of_job_id and item.source_job_item_id:
            parent = session.get(JobRecord, job.retry_of_job_id)
            old = session.get(JobItemRecord, item.source_job_item_id)
            if (
                not parent
                or not old
                or parent.id in seen
                or item.job_id != job.id
                or old.job_id != parent.id
                or old.item_key != item.item_key
                or any(
                    row.workspace_id != request.workspace_id
                    for row in (old, parent, item, job)
                )
                or parent.type != job.type
                or any(
                    job.configuration.get(k) != parent.configuration.get(k)
                    for k in ("input", "settings")
                )
            ):
                return None
            seen.add(parent.id)
            if old.error_code == "IMAGE_FIDELITY_REJECTED":
                audit = (old.error_details or {}).get("image_fidelity") or {}
                if audit.get("failed_checks") != ["physical_text_readback"]:
                    return None
                link = session.scalar(
                    select(JobAssetRecord)
                    .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
                    .where(
                        JobAssetRecord.workspace_id == request.workspace_id,
                        JobAssetRecord.job_id == parent.id,
                        JobAssetRecord.job_item_id == old.id,
                        JobAssetRecord.role == "fidelity_candidate",
                        JobAssetRecord.asset_id == audit.get("retained_asset_id"),
                        AssetRecord.workspace_id == request.workspace_id,
                        AssetRecord.sha256 == audit.get("candidate_sha256"),
                        AssetRecord.lifecycle_status == "ready",
                        AssetRecord.deleted_at.is_(None),
                    )
                )
                if not link:
                    return None
                row = old.configuration.get("_image_fidelity_frozen", {}).get(
                    (link.validation or {}).get("fidelity_receipt")
                )
                if not row:
                    return None
                recipe = row.get("source_recolor_recipe") or {}
                if (
                    recipe.get("version") != VERSION
                    or recipe.get("source_asset_id") != source.asset_id
                    or recipe.get("source_sha256") != source.sha256
                    or row.get("receipt_sha256")
                    != canonical_hash(
                        {k: v for k, v in row.items() if k != "receipt_sha256"}
                    )
                    or not row.get("preparation", {}).get("literal_print_ledger")
                ):
                    return None
                return row
            if old.error_code != "IMAGE_FIDELITY_PREPARATION_FAILED":
                return None
            job, item = parent, old
    return None


async def prior_garment_candidate(
    sessions: Any,
    storage: Any,
    request: Any,
    review_version: str,
) -> ProviderArtifact | None:
    """Reinspect a rejected original only when retry inputs and instructions match."""
    with sessions() as session:
        job = session.get(JobRecord, request.job_id)
        item = session.get(JobItemRecord, request.job_item_id)
        if (
            not job
            or not item
            or not job.retry_of_job_id
            or not item.source_job_item_id
        ):
            return None
        parent = session.get(JobRecord, job.retry_of_job_id)
        source_item = session.get(JobItemRecord, item.source_job_item_id)
        if (
            not parent
            or not source_item
            or (
                (
                    job.workspace_id,
                    item.workspace_id,
                    parent.workspace_id,
                    source_item.workspace_id,
                )
                != (request.workspace_id,) * 4
                or item.job_id != job.id
                or source_item.job_id != parent.id
                or source_item.item_key != item.item_key
                or job.type != parent.type
                or source_item.error_code != "IMAGE_FIDELITY_REJECTED"
                or any(
                    job.configuration.get(k) != parent.configuration.get(k)
                    for k in ("input", "settings")
                )
            )
        ):
            return None
        receipt = (source_item.error_details or {}).get("image_fidelity") or {}
        if receipt.get("detail_review_version") == review_version:
            return None
        asset = session.scalar(
            select(AssetRecord)
            .join(
                JobAssetRecord,
                JobAssetRecord.asset_id == AssetRecord.id,
            )
            .where(
                AssetRecord.workspace_id == request.workspace_id,
                JobAssetRecord.workspace_id == request.workspace_id,
                JobAssetRecord.job_id == parent.id,
                JobAssetRecord.job_item_id == source_item.id,
                JobAssetRecord.role == "fidelity_candidate",
                AssetRecord.id == receipt.get("retained_asset_id"),
                AssetRecord.sha256 == receipt.get("candidate_sha256"),
                AssetRecord.lifecycle_status == "ready",
                AssetRecord.deleted_at.is_(None),
            )
        )
        if asset is None or not asset.width or not asset.height:
            return None
        key, name, content = (
            asset.object_key,
            asset.original_filename,
            asset.content_type,
        )
        width, height, digest = asset.width, asset.height, asset.sha256
    payload = await storage.read_bytes(key)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Retained candidate bytes changed")
    return ProviderArtifact(payload, name, content, width, height, digest)


async def retain(
    sessions: Any,
    storage: Any,
    request: Any,
    artifact: Any,
    *,
    role: str = "fidelity_candidate",
    ordinal: int | None = None,
    authority_key: str | None = None,
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
                authority_key or request.idempotency_key,
            ):
                raise ValueError("Candidate scope invalid")
            receipt = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
            part = ordinal if ordinal is not None else 0
            prior = session.scalar(
                select(JobAssetRecord).where(
                    JobAssetRecord.workspace_id == request.workspace_id,
                    JobAssetRecord.job_id == request.job_id,
                    JobAssetRecord.job_item_id == request.job_item_id,
                    JobAssetRecord.role == role,
                    JobAssetRecord.validation["fidelity_receipt"].as_string()
                    == receipt,
                    JobAssetRecord.validation["part"].as_integer() == part,
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
            # The workspace row lock also serializes allocation across signed
            # native child invocations under one durable parent attempt.
            last = session.scalar(
                select(func.max(JobAssetRecord.ordinal)).where(
                    JobAssetRecord.workspace_id == request.workspace_id,
                    JobAssetRecord.job_id == request.job_id,
                    JobAssetRecord.job_item_id == request.job_item_id,
                    JobAssetRecord.role == role,
                )
            )
            ordinal = int(last) + 1 if last is not None else 0
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
                f"{request.job_item_id}:{request.attempt_id}:{receipt}:{role}".encode()
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
                    validation={"fidelity_receipt": receipt, "part": part},
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
