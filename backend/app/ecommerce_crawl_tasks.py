from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener
from urllib.request import Request as UrlRequest
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .admin_auth import is_admin
from .auth import Principal, get_principal
from .database import get_session
from .domain import (
    CRAWL_TASK_TERMINAL_STATUSES,
    CrawlTaskStatus,
    EcommerceLoginState,
    MembershipStatus,
    WorkspaceStatus,
    can_transition_crawl_task,
)
from .ecommerce_platform import (
    PlatformPolicyError,
    SiteFamily,
    classify_site,
    resolve_site_policy,
)
from .ecommerce_routes import FetchItemRequest, fetch_item
from .errors import ApiError, UploadValidationError
from .models import (
    AssetRecord,
    CrawlAttemptRecord,
    CrawlEventRecord,
    CrawlProfileLeaseRecord,
    CrawlTaskRecord,
    EcommerceAccountRecord,
    EcommerceLoginSessionRecord,
    EcommerceWorkerRecord,
    ProductAssetRecord,
    ProductSnapshotRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
    utc_now,
)
from .storage import ALLOWED_IMAGE_FORMATS, ImageStorage, StoredImage, ValidatedImage

router = APIRouter(prefix="/api/ecommerce", tags=["ecommerce-crawl-tasks"])
logger = logging.getLogger(__name__)


class _ProfileLeaseLost(RuntimeError):
    """Raised when a worker no longer owns the profile fencing generation."""


SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]

_SAFE_MESSAGES = {
    "INVALID_URL": "商品链接无效。",
    "UNSUPPORTED_PLATFORM": "当前商品平台不受支持。",
    "PLATFORM_NOT_ENABLED": "该平台尚未在当前版本启用。",
    "LOGIN_REQUIRED": "需要先完成平台登录。",
    "CAPTCHA_REQUIRED": "需要人工完成平台验证。",
    "RISK_CONTROL": "平台触发风控验证，请完成人工验证后重试。",
    "PRODUCT_NOT_FOUND": "未找到商品，商品可能不存在或已下架。",
    "PAGE_LOAD_TIMEOUT": "商品页面加载超时，请稍后重试。",
    "PARSE_FAILED": "商品页面解析失败，请稍后重试。",
    "UPSTREAM_UNAVAILABLE": "平台暂时不可用，请稍后重试。",
    "TASK_CANCELLED": "任务已取消。",
    "INTERNAL_ERROR": "抓取任务执行失败，请稍后重试。",
    "ASSET_PERSIST_FAILED": "商品结果保存失败，请稍后重试。",
    "PROFILE_BUSY": "当前平台浏览器会话正被其他任务占用，请稍后重试。",
    "ACCOUNT_NOT_AVAILABLE": "当前电商账号不可用，请先恢复账号或完成登录。",
}
_WAITING_SESSION_CODES = {
    "LOGIN_REQUIRED",
    "CAPTCHA_REQUIRED",
    "RISK_CONTROL",
    "RISK_CONTROL_PAGE",
}


class CrawlTaskCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=8, max_length=2048)
    platform: str = Field(default="auto", max_length=24)
    includeDescImages: bool = True
    max_attempts: int = Field(default=1, ge=1, le=3)
    account_id: str | None = Field(default=None, min_length=1, max_length=40)


class CrawlTaskActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _ensure_workspace(
    session: Session, principal: Principal, workspace_id: str, *, write: bool = False
) -> None:
    workspace = session.get(WorkspaceRecord, workspace_id)
    membership = session.scalar(
        select(WorkspaceMemberRecord).where(
            WorkspaceMemberRecord.workspace_id == workspace_id,
            WorkspaceMemberRecord.user_id == principal.user_id,
            WorkspaceMemberRecord.status == MembershipStatus.ACTIVE.value,
        )
    )
    if (
        workspace is None
        or workspace.status != WorkspaceStatus.ACTIVE.value
        or membership is None
    ):
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "WORKSPACE_NOT_FOUND", "未找到指定工作区。"
        )
    if write and membership.role == "viewer":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "WORKSPACE_ACCESS_DENIED",
            "当前成员角色没有任务写入权限。",
        )


def _task_response(
    task: CrawlTaskRecord,
    *,
    include_result: bool = False,
    include_internal_account: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": task.id,
        "workspace_id": task.workspace_id,
        "created_by": task.created_by,
        "platform": task.platform,
        "source_url": task.source_url,
        "canonical_url": task.canonical_url,
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "error_code": task.error_code,
        "error_message_safe": task.error_message_safe,
        "attempt_count": task.attempt_count,
        "max_attempts": task.max_attempts,
        "account_id": task.account_id if include_internal_account else None,
        "profile_id": task.profile_id if include_internal_account else None,
        "result_snapshot_id": task.result_snapshot_id,
        "queued_at": _iso(task.queued_at),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "cancelled_at": _iso(task.cancelled_at),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
    }
    if include_result:
        data["result"] = task.result_payload
    return data


def _append_event(
    session: Session,
    task: CrawlTaskRecord,
    event_type: str,
    message: str,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        CrawlEventRecord(
            id=_new_id("cev"),
            task_id=task.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            message=message,
            details=details,
        )
    )


def _set_status(
    session: Session,
    task: CrawlTaskRecord,
    target: CrawlTaskStatus,
    *,
    stage: str | None = None,
    progress: int | None = None,
    message: str,
    event_type: str = "status_changed",
) -> None:
    current = CrawlTaskStatus(task.status)
    if current != target and not can_transition_crawl_task(current, target):
        raise RuntimeError(
            f"invalid crawl task transition: {current.value} -> {target.value}"
        )
    if current != target:
        task.status = target.value
        task.state_version += 1
    if stage is not None:
        task.stage = stage
    if progress is not None:
        task.progress = max(0, min(100, progress))
    now = utc_now()
    if target == CrawlTaskStatus.QUEUED:
        task.queued_at = now
        task.finished_at = None
        task.cancelled_at = None
    if target == CrawlTaskStatus.RUNNING and task.started_at is None:
        task.started_at = now
    if target in CRAWL_TASK_TERMINAL_STATUSES:
        task.finished_at = now
    if target == CrawlTaskStatus.CANCELLED:
        task.cancelled_at = now
    _append_event(
        session,
        task,
        event_type,
        message,
        from_status=current.value if current != target else None,
        to_status=target.value if current != target else None,
    )


_IMAGE_ROLE_FIELDS = (
    ("main", "imageUrls"),
    ("detail", "descImageUrls"),
    ("sku", "skuImageUrls"),
)


def _safe_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _image_filename(url: str, suffix: str) -> str:
    name = PurePosixPath(urlparse(url).path).name
    if not name or len(name) > 180:
        name = f"remote-{uuid4().hex[:12]}{suffix}"
    if "." not in name:
        name += suffix
    return name[:255]


def _download_remote_image(
    url: str, *, max_bytes: int, timeout_seconds: float = 8.0
) -> ValidatedImage:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UploadValidationError("INVALID_IMAGE_URL", "商品图片地址无效。")
    opener = build_opener(ProxyHandler({}))
    request = UrlRequest(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128 Safari/537.36"
            ),
            "Accept": (
                "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
            ),
            "Referer": f"{parsed.scheme}://{parsed.hostname}/",
        },
    )
    with opener.open(request, timeout=timeout_seconds) as response:  # nosec B310 - URL scheme validated above
        payload = response.read(max_bytes + 1)
    if not payload:
        raise UploadValidationError("EMPTY_FILE", "商品图片为空。")
    if len(payload) > max_bytes:
        raise UploadValidationError("FILE_TOO_LARGE", "商品图片超过当前大小限制。")
    try:
        with Image.open(BytesIO(payload)) as image:
            image_format = str(image.format or "")
            width, height = image.size
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadValidationError("INVALID_IMAGE", "商品图片格式不可识别。") from exc
    format_contract = ALLOWED_IMAGE_FORMATS.get(image_format)
    if format_contract is None:
        raise UploadValidationError("UNSUPPORTED_IMAGE_TYPE", "商品图片格式不受支持。")
    suffix, content_type = format_contract
    digest = hashlib.sha256(payload).hexdigest()
    return ValidatedImage(
        payload=payload,
        filename=_image_filename(url, suffix),
        content_type=content_type,
        width=width,
        height=height,
        object_key=f"__WORKSPACE__/ecommerce/{digest}{suffix}",
        sha256=digest,
    )


def _snapshot_workspace(session_factory: Any, task_id: str) -> str:
    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        if task is None:
            raise RuntimeError("crawl task disappeared before snapshot persistence")
        workspace_id = task.workspace_id

    return str(workspace_id)


def _commit_crawled_asset(
    session_factory: Any, workspace_id: str, stored: StoredImage, url: str, task_id: str
) -> str:
    with session_factory() as session:
        asset = session.scalar(
            select(AssetRecord).where(
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.storage_provider == stored.storage_provider,
                AssetRecord.storage_bucket == stored.storage_bucket,
                AssetRecord.object_key == stored.object_key,
                AssetRecord.object_version == stored.object_version,
            )
        )
        if asset is None:
            asset = AssetRecord(
                id=_new_id("ast"),
                workspace_id=workspace_id,
                media_kind="image",
                origin_kind="imported",
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
                asset_metadata={"source_url": url, "crawl_task_id": task_id},
            )
            session.add(asset)
            session.flush()
            session.commit()
        asset_id = asset.id
    return str(asset_id)


def _snapshot_lease_owned(
    session: Session,
    lease_guard: Callable[[], bool] | None,
    lease_guard_in_session: Callable[[Session], bool] | None,
) -> bool:
    if lease_guard_in_session is not None:
        return lease_guard_in_session(session)
    return lease_guard is None or lease_guard()


def _commit_product_snapshot(
    session_factory: Any,
    task_id: str,
    enriched: dict[str, Any],
    asset_refs: list[dict[str, Any]],
    lease_guard: Callable[[], bool] | None,
    lease_guard_in_session: Callable[[Session], bool] | None,
) -> tuple[str, dict[str, Any]]:
    snapshot_id = str(enriched["snapshotId"])
    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        if task is None:
            raise RuntimeError("crawl task disappeared during snapshot persistence")
        if not _snapshot_lease_owned(session, lease_guard, lease_guard_in_session):
            raise _ProfileLeaseLost("profile lease lost before snapshot persistence")
        snapshot = ProductSnapshotRecord(
            id=snapshot_id,
            workspace_id=task.workspace_id,
            task_id=task.id,
            platform=str(enriched.get("platform") or task.platform),
            product_id=_safe_text(enriched.get("numIid"), 128),
            canonical_url=task.canonical_url,
            title=_safe_text(enriched.get("title"), 500),
            price=_safe_text(enriched.get("price"), 128),
            shop_name=_safe_text(enriched.get("shopName"), 300),
            sales=_safe_text(enriched.get("sales"), 128),
            payload=enriched,
            warnings=enriched["warnings"],
        )
        session.add(snapshot)
        task.result_snapshot_id = snapshot_id
        session.flush()
        for ref in asset_refs:
            session.add(
                ProductAssetRecord(
                    id=_new_id("pas"),
                    snapshot_id=snapshot_id,
                    asset_id=str(ref["asset_id"]),
                    role=str(ref["role"]),
                    ordinal=int(ref["ordinal"]),
                    source_url=str(ref["source_url"]),
                    source=str(ref.get("source") or "crawler"),
                )
            )
        if not _snapshot_lease_owned(session, lease_guard, lease_guard_in_session):
            session.rollback()
            raise _ProfileLeaseLost("profile lease lost before snapshot commit")
        session.commit()
    return snapshot_id, enriched


async def _persist_product_snapshot(
    app: Any,
    task_id: str,
    result_data: dict[str, Any],
    *,
    lease_guard: Callable[[], bool] | None = None,
    lease_guard_in_session: Callable[[Session], bool] | None = None,
) -> tuple[str, dict[str, Any]]:
    session_factory = app.state.session_factory
    storage: ImageStorage = app.state.storage
    settings = app.state.settings
    workspace_id = await asyncio.to_thread(
        _snapshot_workspace, session_factory, task_id
    )

    enriched = deepcopy(result_data)
    raw_warnings = enriched.get("warnings", [])
    warnings = (
        [str(item) for item in raw_warnings] if isinstance(raw_warnings, list) else []
    )
    asset_refs: list[dict[str, Any]] = []
    asset_groups: dict[str, list[str]] = {role: [] for role, _ in _IMAGE_ROLE_FIELDS}

    for role, field in _IMAGE_ROLE_FIELDS:
        values = enriched.get(field)
        if not isinstance(values, list):
            continue
        for ordinal, value in enumerate(values):
            if lease_guard is not None and not await asyncio.to_thread(lease_guard):
                raise _ProfileLeaseLost("profile lease lost during asset persistence")
            url = str(value).strip() if value is not None else ""
            if not url:
                continue
            try:
                image = await asyncio.to_thread(
                    _download_remote_image,
                    url,
                    max_bytes=int(settings.max_upload_bytes),
                    timeout_seconds=8.0,
                )
                image = ValidatedImage(
                    payload=image.payload,
                    filename=image.filename,
                    content_type=image.content_type,
                    width=image.width,
                    height=image.height,
                    object_key=image.object_key.replace(
                        "__WORKSPACE__", workspace_id, 1
                    ),
                    sha256=image.sha256,
                )
                stored = await storage.save_validated(image)
            except Exception as exc:
                warnings.append(f"{role} 图片未能保存到内部存储。")
                logger.info(
                    (
                        "ecommerce_asset_persist_skipped "
                        "task=%s role=%s ordinal=%d error=%s"
                    ),
                    task_id,
                    role,
                    ordinal,
                    type(exc).__name__,
                )
                continue

            asset_id = await asyncio.to_thread(
                _commit_crawled_asset,
                session_factory,
                workspace_id,
                stored,
                url,
                task_id,
            )
            asset_refs.append(
                {
                    "asset_id": asset_id,
                    "role": role,
                    "ordinal": ordinal,
                    "source_url": url,
                    "source": "crawler",
                }
            )
            asset_groups[role].append(asset_id)

    enriched["snapshotId"] = _new_id("snap")
    enriched["assetIds"] = asset_groups
    enriched["assets"] = asset_refs
    enriched["warnings"] = list(dict.fromkeys(warnings))

    return await asyncio.to_thread(
        _commit_product_snapshot,
        session_factory,
        task_id,
        enriched,
        asset_refs,
        lease_guard,
        lease_guard_in_session,
    )


async def cleanup_orphaned_crawl_assets(
    app: Any, *, grace_seconds: int = 3600, limit: int = 100
) -> int:
    """Delete crawler-uploaded objects that are not referenced by a snapshot.

    Asset rows are retained as tombstones after the object is deleted so a
    failed cleanup can be retried without losing the audit trail. The grace
    period protects the short window between uploading an object and committing
    its ProductSnapshot link.
    """

    cutoff = utc_now() - timedelta(seconds=max(60, grace_seconds))
    session_factory = app.state.session_factory
    storage: ImageStorage = app.state.storage
    candidates: list[AssetRecord] = []
    with session_factory() as session:
        for asset in session.scalars(
            select(AssetRecord)
            .where(
                AssetRecord.origin_kind == "imported",
                AssetRecord.lifecycle_status == "ready",
                AssetRecord.deleted_at.is_(None),
                AssetRecord.created_at < cutoff,
                or_(
                    AssetRecord.purge_next_retry_at.is_(None),
                    AssetRecord.purge_next_retry_at <= utc_now(),
                ),
            )
            .order_by(AssetRecord.created_at)
            .limit(max(1, limit))
        ).all():
            metadata = asset.asset_metadata or {}
            if isinstance(metadata, dict) and metadata.get("crawl_task_id"):
                candidates.append(asset)

    cleaned = 0
    for candidate in candidates:
        with session_factory() as session:
            asset = session.get(AssetRecord, candidate.id)
            if asset is None or asset.deleted_at is not None:
                continue
            if (
                session.scalar(
                    select(ProductAssetRecord.id)
                    .where(ProductAssetRecord.asset_id == asset.id)
                    .limit(1)
                )
                is not None
            ):
                continue
            object_key = asset.object_key
            object_version = asset.object_version
        try:
            await storage.delete_object(object_key, object_version)
        except Exception as exc:
            with session_factory() as session:
                asset = session.get(AssetRecord, candidate.id)
                if asset is not None and asset.deleted_at is None:
                    asset.purge_attempts += 1
                    delay_seconds = min(3600, 60 * (2 ** min(asset.purge_attempts, 6)))
                    asset.purge_next_retry_at = utc_now() + timedelta(
                        seconds=delay_seconds
                    )
                    asset.purge_last_error = type(exc).__name__[:120]
                    session.commit()
            logger.warning(
                "ecommerce orphan asset cleanup failed asset=%s error=%s",
                candidate.id,
                type(exc).__name__,
            )
            continue
        with session_factory() as session:
            asset = session.get(AssetRecord, candidate.id)
            if asset is None or asset.deleted_at is not None:
                continue
            if (
                session.scalar(
                    select(ProductAssetRecord.id)
                    .where(ProductAssetRecord.asset_id == asset.id)
                    .limit(1)
                )
                is not None
            ):
                continue
            now = utc_now()
            asset.lifecycle_status = "deleted"
            asset.deleted_at = now
            asset.purged_at = now
            asset.purge_next_retry_at = None
            asset.purge_last_error = None
            session.commit()
            cleaned += 1
    return cleaned


def _error_from_exception(exc: BaseException) -> tuple[str, str, bool]:
    detail: Any = exc.detail if isinstance(exc, HTTPException) else None
    code = "INTERNAL_ERROR"
    retryable = True
    if isinstance(detail, dict):
        raw_code = detail.get("code")
        if isinstance(raw_code, str):
            code = {
                "FETCH_TIMEOUT": "PAGE_LOAD_TIMEOUT",
                "FETCH_FAILED": "UPSTREAM_UNAVAILABLE",
                "RISK_CONTROL_PAGE": "RISK_CONTROL",
                "EMPTY_PRODUCT": "PARSE_FAILED",
                "IMAGE_EXTRACTION_INCOMPLETE": "PARSE_FAILED",
            }.get(raw_code, raw_code)
        retryable = bool(detail.get("retryable", retryable))
    elif isinstance(detail, str) and detail:
        code = "INTERNAL_ERROR"
    return code, _SAFE_MESSAGES.get(code, _SAFE_MESSAGES["INTERNAL_ERROR"]), retryable


_PROFILE_LEASE_TTL_SECONDS = 15 * 60
_PROFILE_BUSY_RETRY_SECONDS = 1.0


def _lease_time_is_active(value: datetime, now: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value > now


def _acquire_profile_lease(
    session_factory: Any,
    *,
    profile_id: str,
    platform: str,
    task_id: str,
    owner_id: str,
    ttl_seconds: int = _PROFILE_LEASE_TTL_SECONDS,
    allow_reentry: bool = True,
) -> int | None:
    """Claim a profile and return its fencing token, or ``None`` if busy."""

    now = utc_now()
    expires_at = now.replace(microsecond=0) + timedelta(seconds=ttl_seconds)
    with session_factory() as session:
        lease = session.scalar(
            select(CrawlProfileLeaseRecord)
            .where(CrawlProfileLeaseRecord.profile_id == profile_id)
            .with_for_update()
        )
        if (
            lease is not None
            and _lease_time_is_active(lease.expires_at, now)
            and (
                not allow_reentry
                or lease.task_id != task_id
                or lease.owner_id != owner_id
            )
        ):
            return None
        fencing_token = 1 if lease is None else lease.fencing_token + 1
        if lease is None:
            session.add(
                CrawlProfileLeaseRecord(
                    id=_new_id("ple"),
                    profile_id=profile_id,
                    platform=platform,
                    task_id=task_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires_at,
                )
            )
        else:
            lease.platform = platform
            lease.task_id = task_id
            lease.owner_id = owner_id
            lease.fencing_token = fencing_token
            lease.acquired_at = now
            lease.heartbeat_at = now
            lease.expires_at = expires_at
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return None
    return fencing_token


def _try_acquire_profile_lease(
    session_factory: Any,
    *,
    profile_id: str,
    platform: str,
    task_id: str,
    owner_id: str,
    ttl_seconds: int = _PROFILE_LEASE_TTL_SECONDS,
) -> bool:
    """Backward-compatible boolean wrapper for lease acquisition tests/callers."""

    return (
        _acquire_profile_lease(
            session_factory,
            profile_id=profile_id,
            platform=platform,
            task_id=task_id,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
        )
        is not None
    )


def _heartbeat_profile_lease(
    session_factory: Any,
    *,
    profile_id: str,
    task_id: str,
    owner_id: str,
    fencing_token: int,
    ttl_seconds: int = _PROFILE_LEASE_TTL_SECONDS,
) -> bool:
    """Renew only the lease generation currently owned by this worker."""

    now = utc_now()
    expires_at = now.replace(microsecond=0) + timedelta(seconds=ttl_seconds)
    with session_factory() as session:
        updated = (
            session.query(CrawlProfileLeaseRecord)
            .filter(
                CrawlProfileLeaseRecord.profile_id == profile_id,
                CrawlProfileLeaseRecord.task_id == task_id,
                CrawlProfileLeaseRecord.owner_id == owner_id,
                CrawlProfileLeaseRecord.fencing_token == fencing_token,
                CrawlProfileLeaseRecord.expires_at > now,
            )
            .update(
                {
                    CrawlProfileLeaseRecord.heartbeat_at: now,
                    CrawlProfileLeaseRecord.expires_at: expires_at,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            session.rollback()
            return False
        session.commit()
    return True


def _profile_lease_is_owned(
    session_factory: Any,
    *,
    profile_id: str,
    task_id: str,
    owner_id: str,
    fencing_token: int,
    session: Session | None = None,
) -> bool:
    if session is None:
        with session_factory() as owned:
            return _profile_lease_is_owned(
                session_factory,
                profile_id=profile_id,
                task_id=task_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                session=owned,
            )
    now = utc_now()
    lease = session.scalar(
        select(CrawlProfileLeaseRecord)
        .where(
            CrawlProfileLeaseRecord.profile_id == profile_id,
            CrawlProfileLeaseRecord.task_id == task_id,
            CrawlProfileLeaseRecord.owner_id == owner_id,
            CrawlProfileLeaseRecord.fencing_token == fencing_token,
        )
        .execution_options(populate_existing=True)
    )
    return lease is not None and _lease_time_is_active(lease.expires_at, now)


def _release_profile_lease(
    session_factory: Any,
    *,
    profile_id: str,
    task_id: str,
    owner_id: str,
    fencing_token: int | None = None,
) -> None:
    with session_factory() as session:
        filters = [
            CrawlProfileLeaseRecord.profile_id == profile_id,
            CrawlProfileLeaseRecord.task_id == task_id,
            CrawlProfileLeaseRecord.owner_id == owner_id,
        ]
        if fencing_token is not None:
            filters.append(CrawlProfileLeaseRecord.fencing_token == fencing_token)
        lease = session.scalar(select(CrawlProfileLeaseRecord).where(*filters))
        if lease is not None:
            session.delete(lease)
            session.commit()


def _schedule(app: Any, task_id: str, *, delay_seconds: float = 0.0) -> None:
    """Dispatch one crawl task inline or to the independent worker queue."""

    bound, online = _bound_worker_required_and_online(app, task_id)
    if bound and not online:
        with app.state.session_factory() as session:
            task = session.get(CrawlTaskRecord, task_id)
            if task is not None and task.status == CrawlTaskStatus.QUEUED.value:
                task.error_code = "LOCAL_WORKER_OFFLINE"
                task.error_message_safe = "绑定的本机执行设备离线，任务未回退到服务器。"
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.FAILED,
                    stage="failed",
                    progress=100,
                    message=task.error_message_safe,
                    event_type="local_worker_offline",
                )
                session.commit()
        return

    if app.state.settings.task_dispatcher == "celery":
        # A live local browser worker is the execution authority for domestic
        # ecommerce tasks.  Celery remains available as a compatibility
        # fallback when no local worker is connected, but must not race a
        # connected browser worker and execute the task in the server runtime.
        if _has_online_local_worker(app, task_id):
            return
        from .ecommerce_worker import enqueue_crawl_task

        enqueue_crawl_task(task_id, countdown=max(0, round(delay_seconds)))
        return

    handles: set[asyncio.Task[Any]] = app.state.ecommerce_crawl_handles
    if any(
        not handle.done() and handle.get_name() == f"crawl-task:{task_id}"
        for handle in handles
    ):
        return

    loop = asyncio.get_running_loop()
    if delay_seconds > 0:
        # A short inline retry must be deferred until the current handle's
        # done-callback removes it. Longer profile-busy waits can use a timer.
        if delay_seconds <= 1:
            loop.call_soon(_schedule, app, task_id)
        else:
            loop.call_later(delay_seconds, _schedule, app, task_id)
        return
    handle = asyncio.create_task(_run_task(app, task_id), name=f"crawl-task:{task_id}")
    handles.add(handle)
    handle.add_done_callback(handles.discard)


def _has_online_local_worker(app: Any, task_id: str) -> bool:
    """Return whether a capable local worker is currently connected.

    This check is intentionally best-effort: a missing session factory or a
    malformed task must not prevent the legacy Celery fallback from running.
    The worker claim endpoint still performs the authoritative lease and
    capability checks, so this only prevents the server worker from winning a
    race against a connected local browser.
    """

    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        return False
    timeout_seconds = int(
        getattr(app.state.settings, "ecommerce_worker_heartbeat_timeout_seconds", 90)
    )
    try:
        with session_factory() as session:
            task = session.get(CrawlTaskRecord, task_id)
            if task is None or task.status != CrawlTaskStatus.QUEUED.value:
                return False
            account = (
                session.get(EcommerceAccountRecord, task.account_id)
                if task.account_id
                else None
            )
            bound_worker_id = account.worker_id if account is not None else None
            workers = session.scalars(
                select(EcommerceWorkerRecord).where(
                    EcommerceWorkerRecord.workspace_id == task.workspace_id,
                    EcommerceWorkerRecord.status == "online",
                    *(
                        [EcommerceWorkerRecord.id == bound_worker_id]
                        if bound_worker_id
                        else []
                    ),
                )
            ).all()
            now = utc_now()
            required_capability = f"browser.{task.platform}"
            for worker in workers:
                if worker.last_seen_at is None:
                    continue
                last_seen = worker.last_seen_at
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=UTC)
                if now - last_seen > timedelta(seconds=timeout_seconds):
                    continue
                if required_capability in set(worker.capabilities or []):
                    return True
    except Exception:
        logger.debug("local worker availability probe failed", exc_info=True)
    return False


def _bound_worker_required_and_online(app: Any, task_id: str) -> tuple[bool, bool]:
    """Return (bound, online) for an account-bound crawl task."""

    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        return False, False
    timeout_seconds = int(
        getattr(app.state.settings, "ecommerce_worker_heartbeat_timeout_seconds", 90)
    )
    try:
        with session_factory() as session:
            task = session.get(CrawlTaskRecord, task_id)
            account = (
                session.get(EcommerceAccountRecord, task.account_id)
                if task and task.account_id
                else None
            )
            worker_id = account.worker_id if account is not None else None
            if not worker_id:
                return False, False
            worker = session.get(EcommerceWorkerRecord, worker_id)
            if (
                worker is None
                or worker.status != "online"
                or worker.last_seen_at is None
            ):
                return True, False
            last_seen = (
                worker.last_seen_at.replace(tzinfo=UTC)
                if worker.last_seen_at.tzinfo is None
                else worker.last_seen_at
            )
            return True, utc_now() - last_seen <= timedelta(seconds=timeout_seconds)
    except Exception:
        logger.debug("bound worker availability probe failed", exc_info=True)
        return True, False


async def _lease_heartbeat_loop(
    session_factory: Any,
    *,
    profile_id: str,
    task_id: str,
    owner_id: str,
    fencing_token: int,
) -> None:
    """Keep a worker lease alive until its generation is no longer valid."""

    while True:
        await asyncio.sleep(5)
        if not _heartbeat_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
        ):
            return


async def _stop_lease_heartbeat(handle: asyncio.Task[Any]) -> None:
    if handle.done():
        return
    handle.cancel()
    with suppress(asyncio.CancelledError):
        await handle


async def _run_task(app: Any, task_id: str, *, worker_id: str | None = None) -> None:
    session_factory = app.state.session_factory
    worker_id = worker_id or f"api-worker-{uuid4().hex[:12]}"
    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        if task is None or task.status != CrawlTaskStatus.QUEUED.value:
            return
        # A queued Celery message can outlive the worker-availability check in
        # ``_schedule``.  Re-check here so a server crawl worker cannot take
        # over after a local browser has connected in the meantime.
        if worker_id.startswith("crawl-worker-"):
            bound, online = _bound_worker_required_and_online(app, task_id)
            if bound:
                if not online:
                    task.error_code = "LOCAL_WORKER_OFFLINE"
                    task.error_message_safe = (
                        "绑定的本机执行设备离线，任务未回退到服务器。"
                    )
                    _set_status(
                        session,
                        task,
                        CrawlTaskStatus.FAILED,
                        stage="failed",
                        progress=100,
                        message=task.error_message_safe,
                        event_type="local_worker_offline",
                    )
                    session.commit()
                return
            if _has_online_local_worker(app, task_id):
                return
        if task.cancel_requested_at is not None:
            _set_status(
                session,
                task,
                CrawlTaskStatus.CANCELLED,
                progress=100,
                stage="cancelled",
                message="任务在执行前被取消。",
            )
            session.commit()
            return
        account = (
            session.get(EcommerceAccountRecord, task.account_id)
            if task.account_id
            else None
        )
        if task.account_id and (account is None or account.status != "active"):
            _set_status(
                session,
                task,
                CrawlTaskStatus.FAILED,
                stage="failed",
                progress=100,
                message="当前电商账号不可用，请先恢复账号或完成登录。",
            )
            task.error_code = "ACCOUNT_NOT_AVAILABLE"
            task.error_message_safe = _SAFE_MESSAGES["ACCOUNT_NOT_AVAILABLE"]
            session.commit()
            return
        profile_id = task.profile_id or f"domestic-{task.platform}"
        payload = dict(task.request_payload)
        url = task.canonical_url

    fencing_token = _acquire_profile_lease(
        session_factory,
        profile_id=profile_id,
        platform=task.platform,
        task_id=task_id,
        owner_id=worker_id,
    )
    if fencing_token is None:
        with session_factory() as session:
            task = session.get(CrawlTaskRecord, task_id)
            if task is not None and task.status == CrawlTaskStatus.QUEUED.value:
                if task.stage != "profile_waiting":
                    _append_event(
                        session,
                        task,
                        "profile_waiting",
                        "平台浏览器会话正在执行其他任务。",
                        details={"profile_id": profile_id},
                    )
                task.stage = "profile_waiting"
                task.progress = max(task.progress, 1)
                session.commit()
        _schedule(app, task_id, delay_seconds=_PROFILE_BUSY_RETRY_SECONDS)
        return

    release_before_fetch = False
    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        if task is None or task.status != CrawlTaskStatus.QUEUED.value:
            release_before_fetch = True
        elif task.cancel_requested_at is not None:
            _set_status(
                session,
                task,
                CrawlTaskStatus.CANCELLED,
                progress=100,
                stage="cancelled",
                message="任务在执行前被取消。",
            )
            session.commit()
            release_before_fetch = True
        else:
            _set_status(
                session,
                task,
                CrawlTaskStatus.RUNNING,
                stage="fetching",
                progress=5,
                message="任务已开始执行。",
            )
            task.attempt_count += 1
            attempt = CrawlAttemptRecord(
                id=_new_id("cat"),
                task_id=task.id,
                attempt_no=task.attempt_count,
                status="running",
                worker_id=worker_id,
                started_at=utc_now(),
            )
            session.add(attempt)
            session.commit()

    if release_before_fetch:
        _release_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        )
        return

    heartbeat_handle = asyncio.create_task(
        _lease_heartbeat_loop(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        ),
        name=f"crawl-lease-heartbeat:{task_id}",
    )
    try:
        if account is not None:
            account.last_used_at = utc_now()
            with session_factory() as session:
                stored_account = session.get(EcommerceAccountRecord, account.id)
                if stored_account is not None:
                    stored_account.last_used_at = utc_now()
                    session.commit()
        request = SimpleNamespace(
            app=app, state=SimpleNamespace(ecommerce_account=account)
        )
        result = await fetch_item(
            request,  # type: ignore[arg-type]
            FetchItemRequest(
                url=url,
                platform=cast(
                    Literal[
                        "auto",
                        "taobao",
                        "tmall",
                        "alibaba1688",
                        "jd",
                        "pinduoduo",
                        "amazon",
                    ],
                    str(payload.get("platform", task.platform)),
                ),
                includeDescImages=bool(payload.get("includeDescImages", True)),
            ),
        )
    except asyncio.CancelledError:
        await _stop_lease_heartbeat(heartbeat_handle)
        _release_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        )
        raise
    except BaseException as exc:
        await _stop_lease_heartbeat(heartbeat_handle)
        if not _profile_lease_is_owned(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        ):
            logger.warning(
                "ecommerce crawl lease lost before failure commit task=%s", task_id
            )
            _release_profile_lease(
                session_factory,
                profile_id=profile_id,
                task_id=task_id,
                owner_id=worker_id,
                fencing_token=fencing_token,
            )
            return
        code, message, retryable = _error_from_exception(exc)
        schedule_retry = False
        with session_factory() as session:
            task = session.get(CrawlTaskRecord, task_id)
            if task is None:
                _release_profile_lease(
                    session_factory,
                    profile_id=profile_id,
                    task_id=task_id,
                    owner_id=worker_id,
                )
                return
            attempt = session.scalar(
                select(CrawlAttemptRecord).where(
                    CrawlAttemptRecord.task_id == task_id,
                    CrawlAttemptRecord.attempt_no == task.attempt_count,
                )
            )
            if attempt:
                attempt.status = (
                    "waiting_session" if code in _WAITING_SESSION_CODES else "failed"
                )
                attempt.error_code = code
                attempt.error_message_safe = message
                attempt.finished_at = utc_now()
            if (
                task.cancel_requested_at is not None
                or task.status == CrawlTaskStatus.CANCELLING.value
            ):
                task.error_code = "TASK_CANCELLED"
                task.error_message_safe = _SAFE_MESSAGES["TASK_CANCELLED"]
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.CANCELLED,
                    stage="cancelled",
                    progress=100,
                    message=_SAFE_MESSAGES["TASK_CANCELLED"],
                )
            elif code in _WAITING_SESSION_CODES:
                if task.account_id:
                    account_record = session.get(
                        EcommerceAccountRecord, task.account_id
                    )
                    if account_record is not None:
                        account_record.login_state = (
                            EcommerceLoginState.NEEDS_LOGIN.value
                        )
                task.error_code = code
                task.error_message_safe = message
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.WAITING_SESSION,
                    stage="waiting_session",
                    progress=10,
                    message=message,
                )
            else:
                task.error_code = code
                task.error_message_safe = message
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.FAILED,
                    stage="failed",
                    progress=100,
                    message=message,
                )
                if retryable and task.attempt_count < task.max_attempts:
                    _set_status(
                        session,
                        task,
                        CrawlTaskStatus.RETRY_SCHEDULED,
                        stage="retry_scheduled",
                        progress=0,
                        message="任务失败，已安排自动重试。",
                        event_type="retry_scheduled",
                    )
                    task.error_code = None
                    task.error_message_safe = None
                    _set_status(
                        session,
                        task,
                        CrawlTaskStatus.QUEUED,
                        stage="queued",
                        progress=0,
                        message="任务已重新进入队列。",
                    )
                    schedule_retry = True
            session.commit()
        _release_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        )
        if schedule_retry:
            # The current task handle is still completing; defer scheduling until
            # its done callback has removed the handle from the in-process set.
            if app.state.settings.task_dispatcher == "celery":
                _schedule(app, task_id, delay_seconds=1)
            else:
                # Preserve the inline runner's post-completion ordering: the
                # current handle must leave the in-process set before rescheduling.
                asyncio.get_running_loop().call_soon(_schedule, app, task_id)
        logger.info(
            "ecommerce crawl task failed id=%s code=%s retryable=%s retry=%s",
            task_id,
            code,
            retryable,
            schedule_retry,
        )
        return

    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        result_data: dict[str, Any] = result["data"]
    else:
        result_data = {"raw": result}
    if not _profile_lease_is_owned(
        session_factory,
        profile_id=profile_id,
        task_id=task_id,
        owner_id=worker_id,
        fencing_token=fencing_token,
    ):
        logger.warning(
            "ecommerce crawl lease lost before result persist task=%s", task_id
        )
        await _stop_lease_heartbeat(heartbeat_handle)
        return
    try:
        snapshot_id, enriched_result = await _persist_product_snapshot(
            app,
            task_id,
            result_data,
            lease_guard=lambda: _profile_lease_is_owned(
                session_factory,
                profile_id=profile_id,
                task_id=task_id,
                owner_id=worker_id,
                fencing_token=fencing_token,
            ),
            lease_guard_in_session=lambda db: _profile_lease_is_owned(
                session_factory,
                profile_id=profile_id,
                task_id=task_id,
                owner_id=worker_id,
                fencing_token=fencing_token,
                session=db,
            ),
        )
    except asyncio.CancelledError:
        await _stop_lease_heartbeat(heartbeat_handle)
        _release_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        )
        raise
    except _ProfileLeaseLost:
        await _stop_lease_heartbeat(heartbeat_handle)
        logger.warning(
            "ecommerce crawl lease lost during result persist task=%s", task_id
        )
        _release_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        )
        return
    except BaseException as exc:
        await _stop_lease_heartbeat(heartbeat_handle)
        if not isinstance(exc, _ProfileLeaseLost) and not _profile_lease_is_owned(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        ):
            logger.warning(
                "ecommerce crawl lease lost before persistence failure commit task=%s",
                task_id,
            )
            _release_profile_lease(
                session_factory,
                profile_id=profile_id,
                task_id=task_id,
                owner_id=worker_id,
                fencing_token=fencing_token,
            )
            return
        code = "ASSET_PERSIST_FAILED"
        message = "商品结果保存失败，请稍后重试。"
        logger.exception(
            "ecommerce_snapshot_persist_failed task=%s error=%s",
            task_id,
            type(exc).__name__,
        )
        with session_factory() as session:
            task = session.get(CrawlTaskRecord, task_id)
            if task is None:
                _release_profile_lease(
                    session_factory,
                    profile_id=profile_id,
                    task_id=task_id,
                    owner_id=worker_id,
                )
                return
            attempt = session.scalar(
                select(CrawlAttemptRecord).where(
                    CrawlAttemptRecord.task_id == task_id,
                    CrawlAttemptRecord.attempt_no == task.attempt_count,
                )
            )
            if attempt:
                attempt.status = "failed"
                attempt.error_code = code
                attempt.error_message_safe = message
                attempt.finished_at = utc_now()
            task.error_code = code
            task.error_message_safe = message
            _set_status(
                session,
                task,
                CrawlTaskStatus.FAILED,
                stage="failed",
                progress=100,
                message=message,
            )
            session.commit()
        _release_profile_lease(
            session_factory,
            profile_id=profile_id,
            task_id=task_id,
            owner_id=worker_id,
            fencing_token=fencing_token,
        )
        return

    if not _profile_lease_is_owned(
        session_factory,
        profile_id=profile_id,
        task_id=task_id,
        owner_id=worker_id,
        fencing_token=fencing_token,
    ):
        logger.warning("ecommerce crawl lease lost before task commit task=%s", task_id)
        await _stop_lease_heartbeat(heartbeat_handle)
        return

    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        if task is None:
            _release_profile_lease(
                session_factory,
                profile_id=profile_id,
                task_id=task_id,
                owner_id=worker_id,
            )
            return
        attempt = session.scalar(
            select(CrawlAttemptRecord).where(
                CrawlAttemptRecord.task_id == task_id,
                CrawlAttemptRecord.attempt_no == task.attempt_count,
            )
        )
        if (
            task.cancel_requested_at is not None
            or task.status == CrawlTaskStatus.CANCELLING.value
        ):
            if attempt:
                attempt.status = "cancelled"
                attempt.error_code = "TASK_CANCELLED"
                attempt.error_message_safe = _SAFE_MESSAGES["TASK_CANCELLED"]
                attempt.finished_at = utc_now()
            task.error_code = "TASK_CANCELLED"
            task.error_message_safe = _SAFE_MESSAGES["TASK_CANCELLED"]
            _set_status(
                session,
                task,
                CrawlTaskStatus.CANCELLED,
                stage="cancelled",
                progress=100,
                message="任务已取消。",
            )
        else:
            if attempt:
                attempt.status = "succeeded"
                attempt.finished_at = utc_now()
            task.result_payload = enriched_result
            task.result_snapshot_id = snapshot_id
            task.error_code = None
            task.error_message_safe = None
            _set_status(
                session,
                task,
                CrawlTaskStatus.SUCCEEDED,
                stage="completed",
                progress=100,
                message="商品抓取完成。",
            )
        session.commit()
    await _stop_lease_heartbeat(heartbeat_handle)
    _release_profile_lease(
        session_factory,
        profile_id=profile_id,
        task_id=task_id,
        owner_id=worker_id,
        fencing_token=fencing_token,
    )


def recover_interrupted_crawl_tasks(session_factory: Any) -> list[str]:
    """Recover only tasks whose worker lease is absent or expired.

    API restarts must not mark a live task as failed while a separate crawl
    worker still owns its profile. Expired tasks are requeued when they have
    attempts left; cancelled tasks remain terminal and are not resurrected.
    """

    recovered: list[str] = []
    now = utc_now()
    with session_factory() as session:
        tasks = session.scalars(
            select(CrawlTaskRecord).where(
                CrawlTaskRecord.status.in_(
                    [CrawlTaskStatus.RUNNING.value, CrawlTaskStatus.CANCELLING.value]
                )
            )
        ).all()
        for task in tasks:
            lease = session.scalar(
                select(CrawlProfileLeaseRecord).where(
                    CrawlProfileLeaseRecord.task_id == task.id
                )
            )
            if lease is not None and _lease_time_is_active(lease.expires_at, now):
                continue
            if lease is not None:
                session.delete(lease)

            if task.cancel_requested_at is not None or task.status == (
                CrawlTaskStatus.CANCELLING.value
            ):
                task.error_code = "TASK_CANCELLED"
                task.error_message_safe = _SAFE_MESSAGES["TASK_CANCELLED"]
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.CANCELLED,
                    stage="cancelled",
                    progress=100,
                    message=task.error_message_safe,
                )
                recovered.append(task.id)
                continue

            task.error_code = "TASK_INTERRUPTED"
            task.error_message_safe = "抓取 Worker 中断，任务已重新排队。"
            _set_status(
                session,
                task,
                CrawlTaskStatus.FAILED,
                stage="failed",
                progress=100,
                message=task.error_message_safe,
            )
            if task.attempt_count < task.max_attempts:
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.RETRY_SCHEDULED,
                    stage="retry_scheduled",
                    progress=0,
                    message="Worker 中断，任务已安排恢复。",
                    event_type="recovery_scheduled",
                )
                task.error_code = None
                task.error_message_safe = None
                _set_status(
                    session,
                    task,
                    CrawlTaskStatus.QUEUED,
                    stage="queued",
                    progress=0,
                    message="任务已恢复并重新进入队列。",
                )
            recovered.append(task.id)
        session.commit()
    return recovered


def resume_queued_crawl_tasks(app: Any, session_factory: Any) -> None:
    with session_factory() as session:
        task_ids = session.scalars(
            select(CrawlTaskRecord.id).where(
                CrawlTaskRecord.status == CrawlTaskStatus.QUEUED.value
            )
        ).all()
    for task_id in task_ids:
        _schedule(app, task_id)


def _account_platform_for_policy(adapter_name: str) -> str:
    return "taobao" if adapter_name == "tmall" else adapter_name


def _account_unavailable_error() -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        "ACCOUNT_NOT_AVAILABLE",
        "当前电商账号不可用，请先恢复账号或完成登录。",
    )


def _select_automatic_account(
    session: Session,
    *,
    workspace_id: str,
    platform: str,
    lock_accounts: bool = False,
) -> tuple[EcommerceAccountRecord | None, bool]:
    """Pick the least-recently used ready account for a platform.

    The selection is deliberately small and deterministic for the first
    account-pool release: disabled/paused/unlogged accounts are excluded and
    an active profile lease is treated as busy.  When called while creating a
    task, the account rows are locked until the task and ``last_used_at``
    update are committed, so concurrent submissions rotate across accounts
    before the worker acquires the profile lease.

    The second tuple member distinguishes "no accounts configured" from
    "accounts configured but none are currently runnable".  The former keeps
    the legacy default platform session available; the latter fails closed so
    an automatic task cannot silently run under an unintended session.
    """

    account_query = (
        select(EcommerceAccountRecord)
        .where(
            EcommerceAccountRecord.workspace_id == workspace_id,
            EcommerceAccountRecord.platform == platform,
        )
        .order_by(EcommerceAccountRecord.id)
    )
    if lock_accounts:
        # Serialize automatic assignments for this workspace/platform. The
        # last_used_at update and task insert happen in the same transaction,
        # so simultaneous submissions cannot select the same account before a
        # worker has had a chance to acquire its profile lease.
        account_query = account_query.with_for_update().execution_options(
            populate_existing=True
        )
    accounts = list(session.scalars(account_query).all())
    # PostgreSQL can sort before waiting on FOR UPDATE. Sort the refreshed
    # values after obtaining the locks, and lock rows in stable ID order.
    accounts.sort(
        key=lambda account: (
            account.last_used_at.replace(tzinfo=UTC)
            if account.last_used_at is not None
            else datetime.min.replace(tzinfo=UTC),
            account.created_at.replace(tzinfo=UTC),
            account.id,
        )
    )
    if not accounts:
        return None, False

    now = utc_now()
    worker_ids = {account.worker_id for account in accounts if account.worker_id}
    workers = (
        {
            worker.id: worker
            for worker in session.scalars(
                select(EcommerceWorkerRecord).where(
                    EcommerceWorkerRecord.id.in_(worker_ids)
                )
            ).all()
        }
        if worker_ids
        else {}
    )
    active_profile_ids = {
        lease.profile_id
        for lease in session.scalars(
            select(CrawlProfileLeaseRecord).where(
                CrawlProfileLeaseRecord.profile_id.in_(
                    [account.profile_id for account in accounts]
                ),
                CrawlProfileLeaseRecord.expires_at > now,
            )
        ).all()
    }
    login_account_ids = set(
        session.scalars(
            select(EcommerceLoginSessionRecord.account_id).where(
                EcommerceLoginSessionRecord.account_id.in_(
                    [account.id for account in accounts]
                ),
                EcommerceLoginSessionRecord.status == "active",
                EcommerceLoginSessionRecord.expires_at > now,
            )
        ).all()
    )
    for account in accounts:
        if account.worker_id:
            worker = workers.get(account.worker_id)
            if (
                worker is None
                or worker.status != "online"
                or worker.last_seen_at is None
            ):
                continue
            last_seen = (
                worker.last_seen_at.replace(tzinfo=UTC)
                if worker.last_seen_at.tzinfo is None
                else worker.last_seen_at
            )
            if now - last_seen > timedelta(
                seconds=90
            ) or f"browser.{platform}" not in set(worker.capabilities or []):
                continue
        if (
            account.status == "active"
            and account.login_state == EcommerceLoginState.READY.value
            and account.profile_id not in active_profile_ids
            and account.id not in login_account_ids
        ):
            return account, True
    return None, True


@router.post("/crawl-tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_crawl_task(
    request: Request,
    payload: CrawlTaskCreateRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    _ensure_workspace(session, principal, payload.workspace_id, write=True)
    raw_key = idempotency_key or None
    if raw_key and len(raw_key) > 128:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "INVALID_IDEMPOTENCY_KEY", "幂等键过长。"
        )
    if payload.platform.casefold() == "amazon":
        raise ApiError(
            status.HTTP_501_NOT_IMPLEMENTED,
            "PLATFORM_NOT_ENABLED",
            "亚马逊当前仅规划，尚未启用。",
        )
    try:
        site_family = classify_site(payload.url)
    except PlatformPolicyError:
        site_family = SiteFamily.UNKNOWN
    if site_family is SiteFamily.AMAZON:
        raise ApiError(
            status.HTTP_501_NOT_IMPLEMENTED,
            "PLATFORM_NOT_ENABLED",
            "亚马逊当前仅规划，尚未启用。",
        )
    try:
        policy = resolve_site_policy(
            payload.url,
            requested_platform=payload.platform,
            proxy_configured=request.app.state.settings.ecommerce_proxy_enabled,
        )
    except PlatformPolicyError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc
    if policy.site_family is SiteFamily.AMAZON:
        raise ApiError(
            status.HTTP_501_NOT_IMPLEMENTED,
            "PLATFORM_NOT_ENABLED",
            "亚马逊当前仅规划，尚未启用。",
        )
    account: EcommerceAccountRecord | None = None
    if payload.account_id is not None and not is_admin(
        principal, session, request.app.state.settings
    ):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ACCOUNT_SELECTION_ADMIN_REQUIRED",
            "普通用户只能使用系统自动分配的可用账号池。",
        )
    if payload.account_id is not None:
        account = session.get(EcommerceAccountRecord, payload.account_id)
        if account is None or account.workspace_id != payload.workspace_id:
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "ECOMMERCE_ACCOUNT_NOT_FOUND",
                "未找到当前工作区中的电商账号。",
            )
        expected_platform = _account_platform_for_policy(policy.adapter_name)
        if account.platform != expected_platform:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "ACCOUNT_PLATFORM_MISMATCH",
                "电商账号平台与商品链接平台不一致。",
            )
        if account.status != "active":
            raise _account_unavailable_error()
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "url": policy.canonical_url,
                "platform": payload.platform,
                "includeDescImages": payload.includeDescImages,
                "account_id": payload.account_id,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if raw_key:
        existing = session.scalar(
            select(CrawlTaskRecord).where(
                CrawlTaskRecord.workspace_id == payload.workspace_id,
                CrawlTaskRecord.idempotency_key == raw_key,
            )
        )
        if existing:
            if existing.request_hash != request_hash:
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "IDEMPOTENCY_KEY_REUSED",
                    "幂等键已用于另一组请求参数。",
                )
            return {
                "success": True,
                "data": _task_response(existing),
                "idempotent": True,
            }

    # With no explicit account, use a ready workspace account when one is
    # configured.  Keep the historical platform session only for workspaces
    # that have not configured any account yet; never fall back to it when an
    # account pool exists but all candidates require attention or are busy.
    if account is None and payload.account_id is None:
        account, has_platform_accounts = _select_automatic_account(
            session,
            workspace_id=payload.workspace_id,
            platform=_account_platform_for_policy(policy.adapter_name),
            lock_accounts=True,
        )
        if has_platform_accounts and account is None:
            raise _account_unavailable_error()
        if account is not None:
            # Mark selection time so concurrent queued requests rotate across
            # ready accounts even before the worker begins the fetch.
            account.last_used_at = utc_now()
    task = CrawlTaskRecord(
        id=_new_id("crw"),
        workspace_id=payload.workspace_id,
        created_by=principal.user_id,
        platform=policy.adapter_name,
        source_url=policy.canonical_url,
        canonical_url=policy.canonical_url,
        idempotency_key=raw_key,
        request_hash=request_hash,
        status=CrawlTaskStatus.CREATED.value,
        max_attempts=payload.max_attempts,
        account_id=account.id if account is not None else None,
        profile_id=(
            account.profile_id if account is not None else policy.session_profile
        ),
        request_payload=payload.model_dump(
            exclude={"workspace_id", "url", "max_attempts"}
        ),
    )
    session.add(task)
    session.flush()
    _append_event(
        session,
        task,
        "created",
        "任务已创建。",
        to_status=CrawlTaskStatus.CREATED.value,
    )
    _set_status(
        session,
        task,
        CrawlTaskStatus.QUEUED,
        stage="queued",
        progress=0,
        message="任务已进入队列。",
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if raw_key:
            existing = session.scalar(
                select(CrawlTaskRecord).where(
                    CrawlTaskRecord.workspace_id == payload.workspace_id,
                    CrawlTaskRecord.idempotency_key == raw_key,
                )
            )
            if existing and existing.request_hash == request_hash:
                return {
                    "success": True,
                    "data": _task_response(existing),
                    "idempotent": True,
                }
        raise ApiError(
            status.HTTP_409_CONFLICT, "TASK_CREATE_CONFLICT", "任务创建冲突，请重试。"
        ) from None
    _schedule(request.app, task.id)
    return {"success": True, "data": _task_response(task), "idempotent": False}


@router.get("/crawl-tasks")
def list_crawl_tasks(
    session: SessionDependency,
    principal: PrincipalDependency,
    workspace_id: str = Query(min_length=1, max_length=40),
    platform: str | None = Query(default=None, min_length=1, max_length=24),
    task_status: str | None = Query(default=None, alias="status", max_length=32),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return the authenticated user's durable crawl-task history."""

    _ensure_workspace(session, principal, workspace_id)
    normalized_platform = platform.strip().casefold() if platform else None
    normalized_status = task_status.strip().casefold() if task_status else None
    if normalized_status and normalized_status not in {
        item.value for item in CrawlTaskStatus
    }:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_CRAWL_TASK_STATUS",
            "任务状态筛选条件无效。",
        )
    filters = [CrawlTaskRecord.workspace_id == workspace_id]
    if normalized_platform:
        filters.append(CrawlTaskRecord.platform == normalized_platform)
    if normalized_status:
        filters.append(CrawlTaskRecord.status == normalized_status)
    total = (
        session.scalar(
            select(func.count()).select_from(CrawlTaskRecord).where(*filters)
        )
        or 0
    )
    tasks = session.scalars(
        select(CrawlTaskRecord)
        .where(*filters)
        .order_by(CrawlTaskRecord.created_at.desc(), CrawlTaskRecord.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "success": True,
        "data": {
            "items": [_task_response(task) for task in tasks],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(tasks) < total,
            },
        },
    }


def _get_task_for_principal(
    session: Session, principal: Principal, task_id: str, *, write: bool = False
) -> CrawlTaskRecord:
    task = session.get(CrawlTaskRecord, task_id)
    if task is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "CRAWL_TASK_NOT_FOUND", "未找到指定抓取任务。"
        )
    _ensure_workspace(session, principal, task.workspace_id, write=write)
    return task


@router.get("/crawl-tasks/{task_id}")
def get_crawl_task(
    task_id: str, session: SessionDependency, principal: PrincipalDependency
) -> dict[str, Any]:
    task = _get_task_for_principal(session, principal, task_id)
    return {"success": True, "data": _task_response(task)}


@router.get("/crawl-tasks/{task_id}/events")
def get_crawl_task_events(
    task_id: str, session: SessionDependency, principal: PrincipalDependency
) -> dict[str, Any]:
    task = _get_task_for_principal(session, principal, task_id)
    events = session.scalars(
        select(CrawlEventRecord)
        .where(CrawlEventRecord.task_id == task.id)
        .order_by(CrawlEventRecord.created_at, CrawlEventRecord.id)
    ).all()
    return {
        "success": True,
        "data": [
            {
                "id": event.id,
                "task_id": event.task_id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "message": event.message,
                "details": event.details,
                "created_at": _iso(event.created_at),
            }
            for event in events
        ],
    }


@router.post("/crawl-tasks/{task_id}/cancel")
def cancel_crawl_task(
    task_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
    payload: CrawlTaskActionRequest | None = None,
) -> dict[str, Any]:
    task = _get_task_for_principal(session, principal, task_id, write=True)
    current = CrawlTaskStatus(task.status)
    if current in CRAWL_TASK_TERMINAL_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "TASK_ALREADY_TERMINAL",
            "任务已经结束，不能取消。",
        )
    task.cancel_requested_at = utc_now()
    if current in {
        CrawlTaskStatus.CREATED,
        CrawlTaskStatus.QUEUED,
        CrawlTaskStatus.WAITING_SESSION,
    }:
        _set_status(
            session,
            task,
            CrawlTaskStatus.CANCELLED,
            stage="cancelled",
            progress=100,
            message=(payload.reason if payload and payload.reason else "任务已取消。"),
            event_type="cancelled",
        )
    elif current == CrawlTaskStatus.RUNNING:
        _set_status(
            session,
            task,
            CrawlTaskStatus.CANCELLING,
            stage="cancelling",
            message=(
                payload.reason if payload and payload.reason else "已请求取消任务。"
            ),
            event_type="cancel_requested",
        )
    session.commit()
    return {"success": True, "data": _task_response(task)}


@router.post("/crawl-tasks/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_crawl_task(
    task_id: str,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> dict[str, Any]:
    task = _get_task_for_principal(session, principal, task_id, write=True)
    current = CrawlTaskStatus(task.status)
    if current not in {
        CrawlTaskStatus.FAILED,
        CrawlTaskStatus.WAITING_SESSION,
        CrawlTaskStatus.CANCELLED,
    }:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "TASK_NOT_RETRYABLE",
            "当前任务状态不支持人工重试。",
        )
    task.cancel_requested_at = None
    task.error_code = None
    task.error_message_safe = None
    task.result_payload = None
    if current in {CrawlTaskStatus.FAILED, CrawlTaskStatus.CANCELLED}:
        _set_status(
            session,
            task,
            CrawlTaskStatus.RETRY_SCHEDULED,
            stage="retry_scheduled",
            progress=0,
            message="任务已安排人工重试。",
            event_type="retry_scheduled",
        )
    _set_status(
        session,
        task,
        CrawlTaskStatus.QUEUED,
        stage="queued",
        progress=0,
        message="任务已重新进入队列。",
    )
    session.commit()
    _schedule(request.app, task.id)
    return {"success": True, "data": _task_response(task)}


@router.get("/crawl-tasks/{task_id}/result")
def get_crawl_task_result(
    task_id: str, session: SessionDependency, principal: PrincipalDependency
) -> dict[str, Any]:
    task = _get_task_for_principal(session, principal, task_id)
    if task.status != CrawlTaskStatus.SUCCEEDED.value:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "RESULT_NOT_READY",
            "任务尚未成功完成，结果暂不可用。",
            {"status": task.status},
        )
    return {
        "success": True,
        "data": {"task": _task_response(task), "result": task.result_payload},
    }
