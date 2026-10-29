import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from zipfile import ZIP_STORED, ZipFile

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import SecretStr
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from . import detail_fast, detail_fast_runtime, refined_detail
from .amazon_visual_suite_provider import AMAZON_EXPECTED_ITEM_COUNT
from .archive_response import ArchiveResponse, close_archive, open_archive
from .aurora_execution_modes import is_parallel_aurora_mode, is_v20_aurora_mode
from .auth import (
    Principal,
    WorkspaceDependency,
    WorkspaceWriteDependency,
    get_principal,
)
from .database import get_session, read_snapshot
from .domain import (
    TERMINAL_JOB_STATUSES,
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    JobAssetDirection,
    JobItemStatus,
    JobStatus,
    JobType,
    MembershipRole,
    MembershipStatus,
    PlanValidationStatus,
    RetryScope,
)
from .domestic_detail.generation import (
    PIPELINE_VERSION,
    DetailGenerationError,
    generation_binding,
    require_current_pipeline,
    require_recoverable_pair,
)
from .domestic_detail.plans import (
    DetailPlanError,
    approval_binding,
    validate_approval_binding,
    validate_plan_views,
)
from .errors import ApiError, UploadValidationError
from .inspiration_routes import _sync_configuration_status
from .masked_repair import (
    MaskedRepairError,
    validate_local_edit_asset_records,
)
from .model_pose_catalog import (
    MODEL_POSE_CATALOG_ID,
    ModelPoseCatalogError,
    ModelPoseCatalogItem,
    resolve_model_pose_catalog_item,
)
from .models import (
    AssetRecord,
    AuditEventRecord,
    InspirationCaseRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
    utc_now,
)
from .provider_quota import reserve_provider_job_quota
from .schemas_v1 import (
    AccessUrlRequest,
    AccessUrlResponse,
    AmazonVisualSuiteJobCreate,
    AssetListResponse,
    AssetResponse,
    CancelRequest,
    CapabilityStatus,
    CreativeImageJobCreate,
    CurrentUserResponse,
    DetailAutomaticBrandRequest,
    DetailIntakeResolutionRequest,
    DetailPackJobCreate,
    DevelopmentProviderSettingsResponse,
    DevelopmentProviderSettingsUpdate,
    DomesticCampaignRecoveryQuote,
    DomesticCampaignRecoveryRequest,
    DomesticCampaignReviewQuote,
    DomesticCampaignReviewReport,
    DomesticCampaignReviewRequest,
    DomesticCampaignReviewSelection,
    DomesticDetailAutomaticDeliveryAuthorization,
    DomesticDetailAutomaticDeliveryRecoveryRequest,
    DomesticDetailFinalReviewResponse,
    DomesticDetailLocalRevisionCreateRequest,
    DomesticDetailLocalRevisionQuote,
    DomesticDetailLocalRevisionQuoteRequest,
    DomesticMainImageJobCreate,
    DraftJobUpdate,
    ErrorResponse,
    FinalizeJobRequest,
    JobCreate,
    JobDetailResponse,
    JobListResponse,
    LocalEditJobCreate,
    ModelPoseAssetRequest,
    ModelPoseTransferJobCreate,
    PlanConfirmRequest,
    PlanResponse,
    PlanUpdateRequest,
    ProductImageJobCreate,
    ReferenceRemixJobCreate,
    RetryRequest,
    RuntimeCapabilities,
    RuntimeStatusResponse,
    WorkspaceResponse,
)
from .services import (
    _is_audit_only_generated_set,
    add_outbox_event,
    asset_is_available,
    asset_response,
    canonical_hash,
    claim_idempotency,
    complete_idempotency,
    decode_cursor,
    encode_cursor,
    enforce_active_job_limit,
    estimate_job,
    get_ready_assets,
    get_workspace_asset,
    get_workspace_job,
    job_detail_response,
    job_history_is_available,
    job_list_response,
    lock_workspace_limits,
    new_id,
    release_credits,
    require_idempotency_key,
    retry_item_ids,
    settle_credits,
    transition_job,
)
from .services import (
    reserve_image_credits as reserve_credits,
)
from .storage import (
    ImageStorage,
    StoredImage,
    ValidatedImage,
    build_content_disposition,
)
from .thumbnails import ThumbnailBusy

router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]
logger = logging.getLogger(__name__)


class _UploadCommitOutcomeUnknown(RuntimeError):
    def __init__(self, asset_id: str, object_key: str) -> None:
        super().__init__(
            f"Unable to determine upload commit outcome for asset {asset_id}"
        )
        self.asset_id = asset_id
        self.object_key = object_key


def _schedule_outbox(request: Request, background_tasks: BackgroundTasks) -> None:
    if not request.app.state.settings.auto_process_jobs:
        return
    from .outbox import publish_pending_events

    background_tasks.add_task(
        publish_pending_events,
        request.app.state.settings,
        request.app.state.session_factory,
        request.app.state.task_runner,
    )


async def _cleanup_upload_object(
    storage: ImageStorage,
    *,
    object_key: str,
    object_version: str = "",
    reason: str,
) -> None:
    try:
        await storage.delete_object(object_key, object_version)
    except Exception:
        logger.exception(
            "upload_object_cleanup_failed",
            extra={
                "object_key": object_key,
                "object_version": object_version,
                "cleanup_reason": reason,
            },
        )


async def _finish_cleanup_before_cancellation(
    storage: ImageStorage,
    *,
    object_key: str,
    reason: str,
) -> None:
    cleanup_task = asyncio.create_task(
        _cleanup_upload_object(
            storage,
            object_key=object_key,
            reason=reason,
        )
    )
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            continue
    cleanup_task.result()


def _commit_staged_asset(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: str,
    actor_user_id: str,
    idempotency_key: str,
    request_hash: str,
    origin_kind: AssetOriginKind,
    media_kind: AssetMediaKind,
    stored: StoredImage,
) -> tuple[AssetResponse, bool]:
    """Run the complete quota critical section in a worker-thread transaction."""

    with session_factory() as session:
        idem, replay = claim_idempotency(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            method="POST",
            route_template="/api/v1/workspaces/{workspace_id}/assets",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay:
            return (
                asset_response(
                    get_workspace_asset(
                        session,
                        workspace_id,
                        cast(str, idem.resource_id),
                    )
                ),
                False,
            )

        limits = lock_workspace_limits(session, workspace_id)
        used_bytes = session.scalar(
            select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
                AssetRecord.deleted_at.is_(None),
            )
        )
        if int(used_bytes or 0) + stored.byte_size > limits.storage_quota_bytes:
            raise ApiError(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "STORAGE_QUOTA_EXCEEDED",
                "当前工作区存储空间不足。",
            )

        asset_id = new_id("asset")
        asset = AssetRecord(
            id=asset_id,
            workspace_id=workspace_id,
            media_kind=media_kind.value,
            origin_kind=origin_kind.value,
            lifecycle_status=AssetLifecycleStatus.READY.value,
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
            asset_metadata={},
        )
        session.add(asset)
        complete_idempotency(
            idem,
            resource_type="asset",
            resource_id=asset.id,
            response_status=status.HTTP_201_CREATED,
        )
        session.flush()
        response = asset_response(asset)
        try:
            session.commit()
        except Exception as commit_error:
            session.rollback()
            try:
                with session_factory() as verification_session:
                    committed = verification_session.get(AssetRecord, asset_id)
                    if (
                        committed is not None
                        and committed.object_key == stored.object_key
                    ):
                        return asset_response(committed), True
            except Exception:
                raise _UploadCommitOutcomeUnknown(
                    asset_id,
                    stored.object_key,
                ) from commit_error
            raise
        return response, True


def _commit_catalog_pose_asset(
    session_factory: sessionmaker[Session],
    *,
    workspace_id: str,
    actor_user_id: str,
    idempotency_key: str,
    request_hash: str,
    pose: ModelPoseCatalogItem,
    stored: StoredImage,
) -> tuple[AssetResponse, bool]:
    """Commit or reuse a private catalog original inside one workspace."""

    with session_factory() as session:
        idem, replay = claim_idempotency(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            method="POST",
            route_template=(
                "/api/v1/workspaces/{workspace_id}/model-pose-transfer/pose-assets"
            ),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay:
            return (
                asset_response(
                    get_workspace_asset(
                        session, workspace_id, cast(str, idem.resource_id)
                    )
                ),
                False,
            )

        existing = session.scalar(
            select(AssetRecord)
            .where(
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.media_kind == AssetMediaKind.IMAGE.value,
                AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
                AssetRecord.sha256 == pose.reference_sha256,
                AssetRecord.deleted_at.is_(None),
            )
            .order_by(AssetRecord.created_at)
            .limit(1)
        )
        if existing is not None:
            complete_idempotency(
                idem,
                resource_type="asset",
                resource_id=existing.id,
                response_status=status.HTTP_201_CREATED,
            )
            session.commit()
            return asset_response(existing), False

        limits = lock_workspace_limits(session, workspace_id)
        used_bytes = session.scalar(
            select(func.coalesce(func.sum(AssetRecord.byte_size), 0)).where(
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
                AssetRecord.deleted_at.is_(None),
            )
        )
        if int(used_bytes or 0) + stored.byte_size > limits.storage_quota_bytes:
            raise ApiError(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "STORAGE_QUOTA_EXCEEDED",
                "The workspace does not have enough storage for this pose asset.",
            )

        asset_id = new_id("asset")
        asset = AssetRecord(
            id=asset_id,
            workspace_id=workspace_id,
            media_kind=AssetMediaKind.IMAGE.value,
            origin_kind=AssetOriginKind.IMPORTED.value,
            lifecycle_status=AssetLifecycleStatus.READY.value,
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
            asset_metadata={
                "catalog_id": MODEL_POSE_CATALOG_ID,
                "pose_slug": pose.slug,
                "reference_sha256": pose.reference_sha256,
            },
        )
        session.add(asset)
        complete_idempotency(
            idem,
            resource_type="asset",
            resource_id=asset.id,
            response_status=status.HTTP_201_CREATED,
        )
        session.flush()
        response = asset_response(asset)
        try:
            session.commit()
        except Exception as commit_error:
            session.rollback()
            try:
                with session_factory() as verification_session:
                    committed = verification_session.get(AssetRecord, asset_id)
                    if (
                        committed is not None
                        and committed.object_key == stored.object_key
                    ):
                        return asset_response(committed), True
            except Exception:
                raise _UploadCommitOutcomeUnknown(
                    asset_id,
                    stored.object_key,
                ) from commit_error
            raise
        return response, True


@router.get("/me", response_model=CurrentUserResponse, tags=["identity"])
def current_user(principal: PrincipalDependency) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        status=principal.status,
        email_verified=principal.email_verified,
    )


def runtime_status_response(
    *,
    environment: Literal["development", "test", "production"],
    authentication_mode: Literal["development", "external"],
    task_runner: Literal["mock", "tengli"],
    product_image_provider_enabled: bool = False,
    reference_remix_provider_enabled: bool = False,
    model_pose_transfer_provider_enabled: bool = False,
    creative_image_provider_enabled: bool = False,
    amazon_visual_suite_provider_enabled: bool = False,
    crossborder_detail_provider_enabled: bool = False,
    domestic_main_image_provider_enabled: bool = False,
) -> RuntimeStatusResponse:
    local_edit = (
        CapabilityStatus(
            availability="available",
            execution="mock",
            reason_code="TASK_RUNNER_MOCK",
        )
        if task_runner == "mock"
        else CapabilityStatus(
            availability="available",
            execution="provider",
            reason_code=None,
        )
    )
    detail_pack = (
        CapabilityStatus(
            availability="available",
            execution="mock",
            reason_code="TASK_RUNNER_MOCK",
        )
        if task_runner == "mock"
        else CapabilityStatus(
            availability="available",
            execution="provider",
            reason_code=None,
        )
    )
    mock_feature_status = CapabilityStatus(
        availability="available",
        execution="mock",
        reason_code="TASK_RUNNER_MOCK",
    )
    unregistered_provider_status = CapabilityStatus(
        availability="unavailable",
        execution="none",
        reason_code="REAL_PROVIDER_NOT_REGISTERED",
    )
    registered_provider_status = CapabilityStatus(
        availability="available",
        execution="provider",
        reason_code=None,
    )

    def feature_status(enabled: bool) -> CapabilityStatus:
        if task_runner == "mock":
            return mock_feature_status
        return registered_provider_status if enabled else unregistered_provider_status

    features = {
        "local_edit": local_edit,
        "product_image": feature_status(product_image_provider_enabled),
        "reference_remix": feature_status(reference_remix_provider_enabled),
        "model_pose_transfer": feature_status(model_pose_transfer_provider_enabled),
        "creative_image": feature_status(creative_image_provider_enabled),
        "detail_pack": detail_pack,
        "amazon_visual_suite": feature_status(amazon_visual_suite_provider_enabled),
        "domestic_main_image": feature_status(domestic_main_image_provider_enabled),
    }
    if task_runner == "tengli":
        features["crossborder_detail"] = feature_status(
            crossborder_detail_provider_enabled
        )

    return RuntimeStatusResponse(
        environment=environment,
        authentication_mode=authentication_mode,
        task_runner=task_runner,
        capabilities=RuntimeCapabilities(
            features=features,
            asset_management=CapabilityStatus(
                availability="available",
                execution="local",
                reason_code=None,
            ),
            local_edit=local_edit,
            detail_pack=detail_pack,
            external_authentication=CapabilityStatus(
                availability="unavailable",
                execution="none",
                reason_code="EXTERNAL_AUTHENTICATION_NOT_IMPLEMENTED",
            ),
        ),
    )


@router.get(
    "/runtime",
    response_model=RuntimeStatusResponse,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
    tags=["system"],
    description=(
        "Reports internal-test workflow availability and execution mode. "
        "availability=available only means the workflow/API can be exercised; "
        "clients must read execution before describing real generation support."
    ),
)
def get_runtime_status(
    request: Request,
    _principal: PrincipalDependency,
) -> RuntimeStatusResponse:
    settings = request.app.state.settings
    return runtime_status_response(
        environment=settings.environment,
        authentication_mode=settings.auth_mode,
        task_runner=settings.task_runner,
        product_image_provider_enabled=settings.product_image_provider_enabled,
        reference_remix_provider_enabled=settings.reference_remix_provider_enabled,
        model_pose_transfer_provider_enabled=(
            settings.model_pose_transfer_provider_enabled
        ),
        creative_image_provider_enabled=settings.creative_image_provider_enabled,
        amazon_visual_suite_provider_enabled=(
            settings.amazon_visual_suite_provider_enabled
        ),
        crossborder_detail_provider_enabled=(
            settings.crossborder_detail_provider_enabled
        ),
        domestic_main_image_provider_enabled=(
            settings.domestic_main_image_provider_enabled
        ),
    )


def _development_provider_response(
    settings: Any, message: str
) -> DevelopmentProviderSettingsResponse:
    configured = bool(settings.tengli_api_key.get_secret_value().strip())
    return DevelopmentProviderSettingsResponse(
        available=settings.development_provider_settings_enabled
        and settings.environment == "development"
        and settings.auth_mode == "development",
        configured=configured,
        task_runner=settings.task_runner,
        product_image_provider_enabled=settings.product_image_provider_enabled,
        reference_remix_provider_enabled=settings.reference_remix_provider_enabled,
        model_pose_transfer_provider_enabled=(
            settings.model_pose_transfer_provider_enabled
        ),
        creative_image_provider_enabled=settings.creative_image_provider_enabled,
        amazon_visual_suite_provider_enabled=(
            settings.amazon_visual_suite_provider_enabled
        ),
        crossborder_detail_provider_enabled=(
            settings.crossborder_detail_provider_enabled
        ),
        domestic_main_image_provider_enabled=(
            settings.domestic_main_image_provider_enabled
        ),
        base_url=settings.tengli_base_url,
        image_model=settings.tengli_image_model,
        vision_model=settings.tengli_vision_model,
        message=message,
    )


def _write_development_provider_env(env_path: Path, updates: dict[str, str]) -> None:
    current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    remaining = dict(updates)
    output: list[str] = []
    for line in current.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            output.append(f"{key}={json.dumps(remaining.pop(key), ensure_ascii=False)}")
        else:
            output.append(line)
    output.extend(
        f"{key}={json.dumps(value, ensure_ascii=False)}"
        for key, value in remaining.items()
    )
    temporary = env_path.with_name(f".env.provider-{os.getpid()}.tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.replace(temporary, env_path)


@router.get(
    "/development/provider-settings",
    response_model=DevelopmentProviderSettingsResponse,
    tags=["runtime"],
)
def get_development_provider_settings(
    request: Request,
    _principal: PrincipalDependency,
) -> DevelopmentProviderSettingsResponse:
    settings = request.app.state.settings
    if (
        not settings.development_provider_settings_enabled
        or settings.environment != "development"
        or settings.auth_mode != "development"
    ):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "DEVELOPMENT_PROVIDER_SETTINGS_UNAVAILABLE",
            "开发者生图配置入口不可用。",
        )
    return _development_provider_response(settings, "开发者生图配置可用。")


@router.post(
    "/development/provider-settings",
    response_model=DevelopmentProviderSettingsResponse,
    tags=["runtime"],
)
def update_development_provider_settings(
    payload: DevelopmentProviderSettingsUpdate,
    request: Request,
    _principal: PrincipalDependency,
) -> DevelopmentProviderSettingsResponse:
    settings = request.app.state.settings
    if (
        not settings.development_provider_settings_enabled
        or settings.environment != "development"
        or settings.auth_mode != "development"
    ):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "DEVELOPMENT_PROVIDER_SETTINGS_UNAVAILABLE",
            "开发者生图配置入口不可用。",
        )
    # The API and a Celery worker are separate processes. Mutating only
    # ``request.app.state`` would make the API advertise provider readiness
    # while the already-started worker continued using its old runner and
    # flags. Require an inline runner for this in-process developer switch;
    # Celery deployments must update the shared compose environment and
    # restart API + worker together.
    if settings.task_dispatcher != "inline":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DEVELOPMENT_PROVIDER_SETTINGS_REQUIRES_WORKER_RESTART",
            "当前使用异步 worker，不能只在 API 进程内启用生图。"
            "请更新 API 与 worker 共用的环境配置后一起重启。",
        )
    values = (
        payload.api_key,
        payload.base_url,
        payload.image_model,
        payload.vision_model,
    )
    if any("\r" in value or "\n" in value for value in values):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "DEVELOPMENT_PROVIDER_SETTINGS_INVALID",
            "生图服务配置不能包含换行符。",
        )
    candidate = settings.model_copy(deep=True)
    candidate.task_runner = "tengli"
    # The development provider settings endpoint is the single local entry
    # point for enabling the Tengli-backed image workflows. Keep all Skill
    # provider switches in the same candidate so an inline development runner
    # cannot report a partially enabled surface. Celery deployments are
    # rejected above until the shared environment is changed and both
    # processes are restarted.
    candidate.product_image_provider_enabled = True
    candidate.reference_remix_provider_enabled = True
    candidate.model_pose_transfer_provider_enabled = True
    candidate.creative_image_provider_enabled = True
    candidate.amazon_visual_suite_provider_enabled = True
    candidate.crossborder_detail_provider_enabled = True
    candidate.domestic_main_image_provider_enabled = True
    candidate.tengli_api_key = SecretStr(payload.api_key.strip())
    candidate.tengli_base_url = payload.base_url.strip()
    candidate.tengli_image_model = payload.image_model.strip()
    candidate.tengli_vision_model = payload.vision_model.strip()
    candidate.analysis_api_key = candidate.tengli_api_key
    candidate.analysis_base_url = candidate.tengli_base_url
    candidate.analysis_model = candidate.tengli_vision_model
    candidate.image_api_key = candidate.tengli_api_key
    candidate.image_base_url = candidate.tengli_base_url
    candidate.image_model = candidate.tengli_image_model
    candidate.xinghe_concurrency = request.app.state.settings.xinghe_concurrency
    from .task_runner import build_task_runner, validate_task_runner_settings

    try:
        validate_task_runner_settings(candidate)
        runner = build_task_runner(
            request.app.state.session_factory,
            candidate,
            request.app.state.storage,
        )
        _write_development_provider_env(
            candidate.development_provider_env_file,
            {
                "POLARIS_TASK_RUNNER": "tengli",
                "POLARIS_PRODUCT_IMAGE_PROVIDER_ENABLED": "true",
                "POLARIS_REFERENCE_REMIX_PROVIDER_ENABLED": "true",
                "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED": "true",
                "POLARIS_CREATIVE_IMAGE_PROVIDER_ENABLED": "true",
                "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED": "true",
                "POLARIS_CROSSBORDER_DETAIL_PROVIDER_ENABLED": "true",
                "POLARIS_DOMESTIC_MAIN_IMAGE_PROVIDER_ENABLED": "true",
                "POLARIS_TENGLI_API_KEY": payload.api_key.strip(),
                "POLARIS_TENGLI_BASE_URL": candidate.tengli_base_url,
                "POLARIS_TENGLI_IMAGE_MODEL": candidate.tengli_image_model,
                "POLARIS_TENGLI_VISION_MODEL": candidate.tengli_vision_model,
                "POLARIS_ANALYSIS_API_KEY": payload.api_key.strip(),
                "POLARIS_ANALYSIS_BASE_URL": candidate.analysis_base_url,
                "POLARIS_ANALYSIS_MODEL": candidate.analysis_model,
                "POLARIS_IMAGE_API_KEY": payload.api_key.strip(),
                "POLARIS_IMAGE_BASE_URL": candidate.image_base_url,
                "POLARIS_IMAGE_MODEL": candidate.image_model,
                "POLARIS_XINGHE_CONCURRENCY": "6",
            },
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "DEVELOPMENT_PROVIDER_SETTINGS_INVALID",
            "生图服务配置未能启用，请检查地址、模型和密钥格式。",
            {"reason": type(exc).__name__},
        ) from exc
    request.app.state.settings = candidate
    request.app.state.task_runner = runner
    return _development_provider_response(
        candidate,
        "全部 Skill Provider 已在当前 inline runner 启用；请重新提交真实素材任务。",
    )


@router.get("/workspaces", response_model=list[WorkspaceResponse], tags=["identity"])
def list_workspaces(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> list[WorkspaceResponse]:
    rows = session.execute(
        select(WorkspaceRecord, WorkspaceMemberRecord)
        .join(
            WorkspaceMemberRecord,
            WorkspaceMemberRecord.workspace_id == WorkspaceRecord.id,
        )
        .where(
            WorkspaceMemberRecord.user_id == principal.user_id,
            WorkspaceMemberRecord.status == MembershipStatus.ACTIVE.value,
        )
        .order_by(WorkspaceRecord.created_at)
    ).all()
    return [
        WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            role=MembershipRole(membership.role),
        )
        for workspace, membership in rows
    ]


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    tags=["identity"],
)
def get_workspace(
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> WorkspaceResponse:
    record = cast(
        WorkspaceRecord,
        session.get(WorkspaceRecord, workspace.workspace_id),
    )
    return WorkspaceResponse(
        id=record.id,
        name=record.name,
        role=workspace.role,
    )


@router.post(
    "/workspaces/{workspace_id}/assets",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def upload_asset(
    request: Request,
    workspace: WorkspaceWriteDependency,
    file: Annotated[UploadFile, File()],
    origin_kind: Annotated[AssetOriginKind, Form()] = AssetOriginKind.UPLOAD,
    media_kind: Annotated[AssetMediaKind, Form()] = AssetMediaKind.IMAGE,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AssetResponse:
    idempotency_key = require_idempotency_key(idempotency_header)
    try:
        validated = await request.app.state.image_validator.validate(
            file, workspace.workspace_id
        )
    except UploadValidationError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc

    request_hash = canonical_hash(
        {
            "sha256": validated.sha256,
            "origin_kind": origin_kind.value,
            "media_kind": media_kind.value,
        }
    )
    storage: ImageStorage = request.app.state.storage
    try:
        stored = await storage.save_validated(validated)
    except asyncio.CancelledError:
        await _finish_cleanup_before_cancellation(
            storage,
            object_key=validated.object_key,
            reason="storage_save_cancelled",
        )
        raise
    except Exception:
        await _cleanup_upload_object(
            storage,
            object_key=validated.object_key,
            reason="storage_save_failed",
        )
        raise

    try:
        response, retained = await asyncio.to_thread(
            _commit_staged_asset,
            request.app.state.session_factory,
            workspace_id=workspace.workspace_id,
            actor_user_id=workspace.principal.user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            origin_kind=origin_kind,
            media_kind=media_kind,
            stored=stored,
        )
    except _UploadCommitOutcomeUnknown as exc:
        logger.critical(
            "upload_commit_outcome_unknown",
            extra={
                "asset_id": exc.asset_id,
                "object_key": exc.object_key,
                "workspace_id": workspace.workspace_id,
            },
            exc_info=True,
        )
        raise
    except Exception:
        await _cleanup_upload_object(
            storage,
            object_key=stored.object_key,
            object_version=stored.object_version,
            reason="database_write_failed",
        )
        raise

    if not retained:
        await _cleanup_upload_object(
            storage,
            object_key=stored.object_key,
            object_version=stored.object_version,
            reason="idempotency_replay",
        )
    return response


@router.post(
    "/workspaces/{workspace_id}/model-pose-transfer/pose-assets",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def materialize_model_pose_asset(
    payload: ModelPoseAssetRequest,
    request: Request,
    workspace: WorkspaceWriteDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AssetResponse:
    """Materialize only a private catalog original resolved by slug and hash."""

    key = require_idempotency_key(idempotency_header)
    try:
        pose = await asyncio.to_thread(
            resolve_model_pose_catalog_item,
            payload.pose_slug,
            payload.reference_sha256,
        )
        original = await asyncio.to_thread(pose.read_original)
    except ModelPoseCatalogError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc)
        ) from exc

    validated = ValidatedImage(
        payload=original,
        filename=pose.filename,
        content_type=pose.mime_type,
        width=pose.width,
        height=pose.height,
        object_key=f"{workspace.workspace_id}/{new_id('pose')}.png",
        sha256=pose.reference_sha256,
    )
    storage: ImageStorage = request.app.state.storage
    try:
        stored = await storage.save_validated(validated)
    except asyncio.CancelledError:
        await _finish_cleanup_before_cancellation(
            storage,
            object_key=validated.object_key,
            reason="catalog_pose_save_cancelled",
        )
        raise
    except Exception:
        await _cleanup_upload_object(
            storage,
            object_key=validated.object_key,
            reason="catalog_pose_save_failed",
        )
        raise

    try:
        response, retained = await asyncio.to_thread(
            _commit_catalog_pose_asset,
            request.app.state.session_factory,
            workspace_id=workspace.workspace_id,
            actor_user_id=workspace.principal.user_id,
            idempotency_key=key,
            request_hash=canonical_hash(payload.model_dump(mode="json")),
            pose=pose,
            stored=stored,
        )
    except _UploadCommitOutcomeUnknown as exc:
        logger.critical(
            "catalog_pose_commit_outcome_unknown",
            extra={
                "asset_id": exc.asset_id,
                "object_key": exc.object_key,
                "workspace_id": workspace.workspace_id,
            },
            exc_info=True,
        )
        raise
    except Exception:
        await _cleanup_upload_object(
            storage,
            object_key=stored.object_key,
            object_version=stored.object_version,
            reason="catalog_pose_database_write_failed",
        )
        raise
    if not retained:
        await _cleanup_upload_object(
            storage,
            object_key=stored.object_key,
            object_version=stored.object_version,
            reason="catalog_pose_reused",
        )
    return response


@router.get(
    "/workspaces/{workspace_id}/assets",
    response_model=AssetListResponse,
    tags=["assets"],
)
def list_assets(
    workspace: WorkspaceDependency,
    session: SessionDependency,
    origin_kind: AssetOriginKind | None = None,
    media_kind: AssetMediaKind | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    cursor: str | None = None,
    include_total: bool = False,
) -> AssetListResponse:
    scope = canonical_hash(
        {
            "workspace_id": workspace.workspace_id,
            "origin_kind": origin_kind,
            "media_kind": media_kind,
        }
    )
    filters: list[Any] = [
        AssetRecord.workspace_id == workspace.workspace_id,
        asset_is_available(),
    ]
    if origin_kind is not None:
        filters.append(AssetRecord.origin_kind == origin_kind.value)
    if media_kind is not None:
        filters.append(AssetRecord.media_kind == media_kind.value)
    if cursor:
        created_raw, asset_id = decode_cursor(cursor, scope)
        created_at = datetime.fromisoformat(created_raw)
        filters.append(
            or_(
                AssetRecord.created_at < created_at,
                and_(
                    AssetRecord.created_at == created_at,
                    AssetRecord.id < asset_id,
                ),
            )
        )
    rows = session.scalars(
        select(AssetRecord)
        .where(*filters)
        .order_by(AssetRecord.created_at.desc(), AssetRecord.id.desc())
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id, scope)
        if has_more and items
        else None
    )
    total = None
    if include_total:
        total_filters = filters[:-1] if cursor else filters
        total = session.scalar(
            select(func.count()).select_from(AssetRecord).where(*total_filters)
        )
    return AssetListResponse(
        items=[asset_response(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
        total=total,
    )


@router.get(
    "/workspaces/{workspace_id}/assets/{asset_id}",
    response_model=AssetResponse,
    tags=["assets"],
)
def get_asset(
    asset_id: str,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> AssetResponse:
    return asset_response(
        get_workspace_asset(session, workspace.workspace_id, asset_id)
    )


@router.post(
    "/workspaces/{workspace_id}/assets/{asset_id}/access-url",
    response_model=AccessUrlResponse,
    tags=["assets"],
)
async def create_asset_access_url(
    asset_id: str,
    payload: AccessUrlRequest,
    request: Request,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> AccessUrlResponse:
    asset = await run_in_threadpool(
        _asset_for_storage, session, workspace.workspace_id, asset_id
    )
    if asset.lifecycle_status != AssetLifecycleStatus.READY.value:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "ASSET_NOT_READY",
            "素材尚未就绪。",
        )
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    if payload.variant == "thumbnail":
        # A stable five-minute URL bucket allows private browser reuse across
        # tabs; the signature still expires in at most ten minutes.
        expires_at = datetime.fromtimestamp(
            (int(datetime.now(UTC).timestamp()) // 300 + 2) * 300, UTC
        )
    if asset.expires_at is not None:
        asset_expires_at = asset.expires_at
        if asset_expires_at.tzinfo is None:
            asset_expires_at = asset_expires_at.replace(tzinfo=UTC)
        expires_at = min(expires_at, asset_expires_at)
    expires_seconds = max(
        1,
        int((expires_at - datetime.now(UTC)).total_seconds()),
    )
    url = None
    if (
        payload.variant == "original"
        and not asset.asset_metadata.get("native_detail_pair_master")
        and request.app.state.settings.asset_access_url_mode == "auto"
    ):
        url = await request.app.state.storage.create_access_url(
            asset.object_key,
            asset.original_filename,
            payload.disposition,
            expires_seconds,
        )
    if url is None:
        url = _signed_asset_url(
            request,
            workspace.workspace_id,
            asset.id,
            expires_at,
            payload.disposition,
            payload.variant,
        )
    return AccessUrlResponse(
        url=url,
        expires_at=expires_at,
        disposition=payload.disposition,
        original_url=(
            _signed_asset_url(
                request,
                workspace.workspace_id,
                asset.id,
                expires_at,
                payload.disposition,
                "original",
            )
            if payload.variant == "thumbnail"
            else None
        ),
    )


def _signed_asset_url(
    request: Request,
    workspace_id: str,
    asset_id: str,
    expires_at: datetime,
    disposition: str,
    variant: str,
) -> str:
    expires = int(expires_at.timestamp())
    signature = _content_signature(
        request.app.state.settings.access_url_secret,
        workspace_id,
        asset_id,
        expires,
        disposition,
        variant,
    )
    url = str(
        request.url_for(
            "get_signed_asset_content",
            workspace_id=workspace_id,
            asset_id=asset_id,
        ).include_query_params(
            expires=expires,
            disposition=disposition,
            signature=signature,
            variant=variant,
        )
    )
    if request.app.state.settings.asset_access_url_mode == "same_origin":
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        return parsed.path + "?" + parsed.query
    return url


def _asset_for_storage(
    session: Session, workspace_id: str, asset_id: str
) -> AssetRecord:
    """Return loaded scalar metadata without holding a connection during I/O."""
    asset = get_workspace_asset(session, workspace_id, asset_id)
    session.expunge(asset)
    session.rollback()
    return asset


async def _asset_content_bytes(request: Request, asset: AssetRecord) -> bytes:
    if asset.asset_metadata.get("native_detail_pair_master"):
        from .domestic_detail.delivery import DetailDeliveryError
        from .domestic_detail.review_packet import read_pair_master

        try:
            return await read_pair_master(request.app.state.storage, asset)
        except DetailDeliveryError as exc:
            raise ApiError(exc.status_code, exc.code, str(exc)) from exc
    return await request.app.state.storage.read_bytes(asset.object_key)


@router.get(
    "/workspaces/{workspace_id}/assets/{asset_id}/content",
    name="get_signed_asset_content",
    include_in_schema=False,
)
async def get_signed_asset_content(
    workspace_id: str,
    asset_id: str,
    request: Request,
    session: SessionDependency,
    expires: int,
    disposition: str,
    signature: str,
    variant: str = "original",
) -> Response:
    if disposition not in {"inline", "attachment"} or variant not in {
        "original",
        "thumbnail",
    }:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "ACCESS_URL_INVALID", "访问地址无效。"
        )
    expected = _content_signature(
        request.app.state.settings.access_url_secret,
        workspace_id,
        asset_id,
        expires,
        disposition,
        variant,
    )
    if expires < int(datetime.now(UTC).timestamp()) or not hmac.compare_digest(
        signature, expected
    ):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ACCESS_URL_EXPIRED",
            "访问地址无效或已过期。",
        )
    asset = await run_in_threadpool(_asset_for_storage, session, workspace_id, asset_id)
    if variant == "thumbnail":
        etag = f'"thumbnail-v1-{asset.sha256}"'
        max_age = max(0, min(60, expires - int(datetime.now(UTC).timestamp())))
        headers = {
            "Cache-Control": f"private, max-age={max_age}",
            "ETag": etag,
            "Content-Disposition": 'inline; filename="preview.webp"',
        }
        if request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers=headers)
        try:
            body = await request.app.state.thumbnails.get(
                f"{asset.object_key}:{asset.sha256}",
                lambda: _asset_content_bytes(request, asset),
            )
        except ThumbnailBusy:
            return Response(
                status_code=503,
                headers={"Retry-After": "3", "Cache-Control": "no-store"},
            )
        return Response(body, media_type="image/webp", headers=headers)
    content_disposition = build_content_disposition(
        disposition, asset.original_filename
    )
    etag = f'"original-v1-{asset.sha256}"'
    max_age = max(0, min(60, expires - int(datetime.now(UTC).timestamp())))
    headers = {
        "Cache-Control": f"private, max-age={max_age}",
        "ETag": etag,
        "Content-Disposition": content_disposition,
    }
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    body = await _asset_content_bytes(request, asset)
    return Response(
        body,
        media_type=asset.content_type,
        headers=headers,
    )


_INSPIRATION_WORKFLOW_JOB_TYPES = {
    "reference_remix": JobType.REFERENCE_REMIX.value,
    "product_prompt": JobType.CREATIVE_IMAGE.value,
    "local_edit": JobType.LOCAL_EDIT.value,
}


def _validate_inspiration_case_job(
    session: Session,
    payload: JobCreate,
) -> InspirationCaseRecord | None:
    case_id = payload.inspiration_case_id
    if not case_id:
        return None
    case = session.scalar(
        select(InspirationCaseRecord).where(InspirationCaseRecord.id == case_id)
    )
    if case is None or case.status != "published":
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "INSPIRATION_CASE_NOT_FOUND",
            "The inspiration case is not available.",
        )
    # Asset uploads can predate the workflow-status fix, leaving the persisted
    # status stale. Recompute executable workflows from the current case assets
    # before accepting a job; legacy cases keep their unsupported-workflow path.
    if case.workflow_type in _INSPIRATION_WORKFLOW_JOB_TYPES:
        _sync_configuration_status(case, session)
    if case.configuration_status != "ready":
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INSPIRATION_CASE_NOT_READY",
            "This inspiration case is still being configured.",
            {"configuration_status": case.configuration_status},
        )
    expected_type = _INSPIRATION_WORKFLOW_JOB_TYPES.get(case.workflow_type)
    if expected_type is None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INSPIRATION_CASE_WORKFLOW_UNSUPPORTED",
            "This inspiration case has no executable workflow.",
        )
    if payload.type != expected_type:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INSPIRATION_WORKFLOW_MISMATCH",
            "The submitted job type does not match the inspiration case workflow.",
            {"expected_job_type": expected_type, "workflow_type": case.workflow_type},
        )
    if case.workflow_type == "product_prompt":
        if not isinstance(payload, CreativeImageJobCreate):
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INSPIRATION_PRODUCT_PROMPT_INVALID",
                "A product-prompt inspiration case requires a creative image job.",
            )
        if payload.input.competitor_asset_ids:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INSPIRATION_REFERENCE_NOT_ALLOWED",
                "This product-prompt case does not accept a reference image.",
            )
        if not payload.configuration.instruction.strip():
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INSPIRATION_PROMPT_REQUIRED",
                "The inspiration prompt is required.",
            )
    elif case.workflow_type == "reference_remix":
        if (
            not isinstance(payload, ReferenceRemixJobCreate)
            or not payload.input.reference_asset_ids
        ):
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INSPIRATION_REFERENCE_REQUIRED",
                "This inspiration case requires a reference image.",
            )
    elif case.workflow_type == "local_edit" and not isinstance(
        payload, LocalEditJobCreate
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INSPIRATION_LOCAL_EDIT_INVALID",
            "This inspiration case requires a local-edit job with a source "
            "image and mask.",
        )
    return case


@router.post(
    "/workspaces/{workspace_id}/jobs",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def create_job(
    payload: JobCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    if (
        isinstance(payload, ReferenceRemixJobCreate)
        and payload.configuration.reference_skill
        in {
            "aurora-ecommerce-reference-remix",
            "all-category-style-benchmark-still",
            "aurora-ecommerce-reference-remix-fast",
            "all-category-style-benchmark-still-fast",
        }
        and not getattr(request.state, "replica_session_id", None)
    ):
        raise ApiError(
            409, "REPLICA_CONFIRM_REQUIRED", "请在一键复刻中分析并确认方案后生成。"
        )
    key = require_idempotency_key(idempotency_header)
    inspiration_case = _validate_inspiration_case_job(session, payload)
    payload_data = payload.model_dump(mode="json")
    request_hash = canonical_hash(payload_data)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs",
        idempotency_key=key,
        request_hash=request_hash,
    )
    if replay:
        return job_detail_response(
            session,
            get_workspace_job(
                session, workspace.workspace_id, cast(str, idem.resource_id)
            ),
        )
    if payload.intent == "submit":
        enforce_active_job_limit(session, workspace.workspace_id)

    job, asset_roles = _new_job_from_payload(
        workspace.workspace_id,
        payload,
        detail_medium_price_micros=(
            request.app.state.settings.xinghe_medium_price_micros
        ),
        detail_high_price_micros=(request.app.state.settings.xinghe_high_price_micros),
    )
    replica_session_id = getattr(request.state, "replica_session_id", None)
    if job.type == JobType.REFERENCE_REMIX.value and isinstance(
        replica_session_id, str
    ):
        job.configuration = {
            **job.configuration,
            "replica_session_id": replica_session_id,
        }
    from .amazon_auto_start import bind_submission as bind_amazon_submission
    from .domestic_detail.auto_start import bind_submission

    bind_submission(job, workspace.principal.user_id)
    bind_amazon_submission(job, workspace.principal.user_id)
    from .main_image_auto_start import bind_submission as bind_main_submission

    bind_main_submission(job, workspace.principal.user_id)
    from .crossborder_auto_start import bind_submission as bind_crossborder_submission

    bind_crossborder_submission(
        job,
        workspace.principal.user_id,
        requested=(
            isinstance(payload, DetailPackJobCreate)
            and "auto_start_after_plan" in payload.configuration.model_fields_set
        ),
    )
    if inspiration_case is not None:
        job.configuration["inspiration_case_id"] = inspiration_case.id
        job.configuration["inspiration_workflow"] = inspiration_case.workflow_type
        job.configuration["inspiration_prompt"] = inspiration_case.prompt
        job.configuration["inspiration_reference_source"] = (
            inspiration_case.reference_source
        )
    asset_ids = [item[0] for item in asset_roles]
    assets = get_ready_assets(session, workspace.workspace_id, asset_ids)
    if isinstance(payload, LocalEditJobCreate):
        try:
            validate_local_edit_asset_records(
                assets[payload.input.source_asset_id],
                assets[payload.input.selection_mask_asset_id],
            )
        except MaskedRepairError as exc:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                exc.code,
                str(exc),
                {"needs_reselection": exc.needs_reselection},
            ) from exc
    elif isinstance(payload, ModelPoseTransferJobCreate):
        _validate_model_pose_transfer_assets(payload, assets)
    session.add(job)
    session.flush()
    _add_items_and_inputs(session, job, asset_roles)
    if payload.intent == "submit":
        _reserve_real_provider_job_if_enabled(
            session,
            request,
            job,
            workspace.principal.user_id,
        )
        reserve_credits(
            session, workspace.workspace_id, job, cast(int, job.estimated_credits)
        )
        add_outbox_event(
            session,
            workspace_id=workspace.workspace_id,
            job_id=job.id,
            event_type="job.dispatch_requested",
            suffix="initial",
        )
    complete_idempotency(
        idem,
        resource_type="job",
        resource_id=job.id,
        response_status=status.HTTP_202_ACCEPTED,
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


@router.get(
    "/workspaces/{workspace_id}/jobs",
    response_model=JobListResponse,
    tags=["jobs"],
)
def list_jobs(
    workspace: WorkspaceDependency,
    session: SessionDependency,
    job_type: Annotated[JobType | None, Query(alias="type")] = None,
    statuses: Annotated[list[JobStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
    include_total: bool = False,
) -> JobListResponse:
    status_values = sorted(item.value for item in statuses or [])
    scope = canonical_hash(
        {
            "workspace_id": workspace.workspace_id,
            "type": job_type,
            "statuses": status_values,
        }
    )
    base_filters: list[Any] = [
        JobRecord.workspace_id == workspace.workspace_id,
        job_history_is_available(),
    ]
    if job_type is not None:
        base_filters.append(JobRecord.type == job_type.value)
    if status_values:
        base_filters.append(JobRecord.status.in_(status_values))
    filters = list(base_filters)
    if cursor:
        created_raw, job_id = decode_cursor(cursor, scope)
        created_at = datetime.fromisoformat(created_raw)
        filters.append(
            or_(
                JobRecord.created_at < created_at,
                and_(JobRecord.created_at == created_at, JobRecord.id < job_id),
            )
        )
    rows = session.scalars(
        select(JobRecord)
        .where(*filters)
        .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    output_summaries: dict[str, tuple[int, str | None]] = {}
    if items:
        output_rows = session.execute(
            select(
                JobAssetRecord.job_id,
                JobAssetRecord.asset_id,
                AssetRecord.lifecycle_status,
            )
            .join(
                AssetRecord,
                and_(
                    AssetRecord.workspace_id == JobAssetRecord.workspace_id,
                    AssetRecord.id == JobAssetRecord.asset_id,
                ),
            )
            .where(
                JobAssetRecord.workspace_id == workspace.workspace_id,
                JobAssetRecord.job_id.in_([item.id for item in items]),
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                JobAssetRecord.is_deliverable.is_(True),
                asset_is_available(),
            )
            .order_by(
                JobAssetRecord.job_id,
                JobAssetRecord.ordinal,
                JobAssetRecord.id,
            )
        ).all()
        for output_job_id, asset_id, lifecycle_status in output_rows:
            count, primary_asset_id = output_summaries.get(output_job_id, (0, None))
            if (
                primary_asset_id is None
                and lifecycle_status == AssetLifecycleStatus.READY.value
            ):
                primary_asset_id = asset_id
            output_summaries[output_job_id] = (count + 1, primary_asset_id)
    total = (
        session.scalar(select(func.count()).select_from(JobRecord).where(*base_filters))
        if include_total
        else None
    )
    item_rows = (
        session.execute(
            select(JobItemRecord).where(
                JobItemRecord.job_id.in_([item.id for item in items])
            )
        )
        .scalars()
        .all()
        if items
        else []
    )
    item_groups: dict[str, list[JobItemRecord]] = {}
    for job_item in item_rows:
        item_groups.setdefault(job_item.job_id, []).append(job_item)
    return JobListResponse(
        items=[
            job_list_response(
                item,
                output_asset_count=output_summaries.get(item.id, (0, None))[0],
                primary_output_asset_id=output_summaries.get(item.id, (0, None))[1],
                generated_set_complete=_is_audit_only_generated_set(
                    item,
                    item_groups.get(item.id, []),
                    output_summaries.get(item.id, (0, None))[0],
                ),
            )
            for item in items
        ],
        next_cursor=(
            encode_cursor(items[-1].created_at, items[-1].id, scope)
            if has_more and items
            else None
        ),
        has_more=has_more,
        total=total,
    )


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}",
    response_model=JobDetailResponse,
    tags=["jobs"],
)
def get_job(
    job_id: str,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> JobDetailResponse:
    return job_detail_response(
        session, get_workspace_job(session, workspace.workspace_id, job_id)
    )


def _is_preserved_amazon_review_infrastructure_failure(
    item: JobItemRecord,
) -> bool:
    details = item.error_details
    return (
        item.status == JobItemStatus.FAILED.value
        and isinstance(details, dict)
        and details.get("failure_kind") == "review_infrastructure_failure"
        and details.get("image_quality_failed") is False
        and details.get("generated_asset_preserved") is True
    )


def _allows_preserved_amazon_partial_archive(
    job: JobRecord,
    items: Sequence[JobItemRecord],
) -> bool:
    if (
        job.type != JobType.AMAZON_VISUAL_SUITE.value
        or JobStatus(job.status) != JobStatus.PARTIAL
        or job.expected_item_count != AMAZON_EXPECTED_ITEM_COUNT
        or len(items) != job.expected_item_count
        or job.succeeded_item_count + job.failed_item_count != job.expected_item_count
    ):
        return False

    preserved_failures = [
        item
        for item in items
        if _is_preserved_amazon_review_infrastructure_failure(item)
    ]
    return (
        bool(preserved_failures)
        and len(preserved_failures) == job.failed_item_count
        and all(
            item.status
            in {
                JobItemStatus.SUCCEEDED.value,
                JobItemStatus.REUSED.value,
            }
            or _is_preserved_amazon_review_infrastructure_failure(item)
            for item in items
        )
    )


def _job_archive_snapshot(
    session: Session, workspace_id: str, job_id: str
) -> tuple[JobRecord, JobPlanRecord | None, list[Any], bool]:
    job = get_workspace_job(session, workspace_id, job_id)
    if (
        job.type == JobType.DETAIL_PACK.value
        and job.configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION
    ):
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == job.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        if plan is None:
            raise ApiError(
                409, "NATIVE_DETAIL_DELIVERY_CONTEXT_MISSING", "原始方案缺失。"
            )
        return job, plan, [], False
    if job.type not in {
        JobType.DETAIL_PACK.value,
        JobType.AMAZON_VISUAL_SUITE.value,
        JobType.DOMESTIC_MAIN_IMAGE.value,
    }:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DELIVERABLE_ARCHIVE_UNSUPPORTED",
            "该任务类型暂不支持整套下载。",
        )
    partial_items: Sequence[JobItemRecord] = ()
    if (
        job.type == JobType.AMAZON_VISUAL_SUITE.value
        and JobStatus(job.status) == JobStatus.PARTIAL
    ):
        partial_items = session.scalars(
            select(JobItemRecord)
            .where(
                JobItemRecord.workspace_id == workspace_id,
                JobItemRecord.job_id == job.id,
            )
            .order_by(JobItemRecord.ordinal)
        ).all()
    preserved_partial_archive = _allows_preserved_amazon_partial_archive(
        job, partial_items
    )
    if JobStatus(job.status) != JobStatus.COMPLETED and not preserved_partial_archive:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DELIVERABLE_ARCHIVE_NOT_READY",
            "套图尚未完整生成，请在全部图片完成后下载。",
        )

    deliverables = session.execute(
        select(JobAssetRecord, JobItemRecord, AssetRecord)
        .join(JobItemRecord, JobItemRecord.id == JobAssetRecord.job_item_id)
        .join(AssetRecord, AssetRecord.id == JobAssetRecord.asset_id)
        .where(
            JobAssetRecord.workspace_id == workspace_id,
            JobAssetRecord.job_id == job.id,
            JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
            JobAssetRecord.is_deliverable.is_(True),
            asset_is_available(),
        )
        .order_by(JobAssetRecord.ordinal)
    ).all()
    if len(deliverables) != job.expected_item_count:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DELIVERABLE_ARCHIVE_INCOMPLETE",
            "套图文件数量与任务合同不一致，暂不能打包下载。",
            {
                "expected_item_count": job.expected_item_count,
                "deliverable_count": len(deliverables),
            },
        )
    if preserved_partial_archive and {item.id for _, item, _ in deliverables} != {
        item.id for item in partial_items
    }:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DELIVERABLE_ARCHIVE_INCOMPLETE",
            "套图文件与 Amazon 21 张正式交付集不一致，暂不能打包下载。",
            {
                "expected_item_count": job.expected_item_count,
                "deliverable_count": len(deliverables),
            },
        )

    return job, None, list(deliverables), preserved_partial_archive


def _append_archive_member(
    bundle: ZipFile, name: str, payload: bytes, byte_size: int, sha256: str
) -> None:
    if len(payload) != byte_size or hashlib.sha256(payload).hexdigest() != sha256:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DELIVERABLE_INTEGRITY_FAILED",
            "套图文件完整性校验失败，请联系管理员处理。",
        )
    bundle.writestr(name, payload)


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/deliverables.zip",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}}
            },
            "description": "Complete canonical deliverables archive.",
        }
    },
    tags=["jobs"],
)
async def download_job_deliverables(
    job_id: str,
    request: Request,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> Response:
    job, plan, deliverables, preserved_partial_archive = await run_in_threadpool(
        read_snapshot,
        session,
        _job_archive_snapshot,
        session,
        workspace.workspace_id,
        job_id,
    )
    if plan is not None:
        from .domestic_detail.delivery import DetailDeliveryError
        from .domestic_detail.review_packet import delivery_archive

        try:
            archive = await delivery_archive(
                session, job, plan, request.app.state.storage
            )
        except (DetailDeliveryError, ValueError, OSError, KeyError) as exc:
            raise ApiError(
                getattr(exc, "status_code", 409),
                getattr(exc, "code", "NATIVE_DETAIL_DELIVERY_INVALID"),
                str(exc),
            ) from exc
        return ArchiveResponse(archive, f"detail-pack-{job.id}.zip")
    archive = await run_in_threadpool(
        open_archive,
        request.app.state.settings.aurora_work_root / "download-cache",
    )
    member_names: set[str] = set()
    bundle = None
    try:
        bundle = await run_in_threadpool(
            ZipFile, archive, mode="w", compression=ZIP_STORED
        )
        for _, item, asset in deliverables:
            extension = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }.get(asset.content_type.lower())
            member_name = f"{item.item_key}{extension or ''}"
            if (
                extension is None
                or member_name in member_names
                or asset.lifecycle_status != AssetLifecycleStatus.READY.value
                or (
                    item.status
                    not in {
                        JobItemStatus.SUCCEEDED.value,
                        JobItemStatus.REUSED.value,
                    }
                    and not (
                        preserved_partial_archive
                        and _is_preserved_amazon_review_infrastructure_failure(item)
                    )
                )
            ):
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "DELIVERABLE_ARCHIVE_INVALID",
                    "套图中存在不可交付或命名冲突的文件，暂不能打包下载。",
                )
            payload = await request.app.state.storage.read_bytes(asset.object_key)
            member_names.add(member_name)
            await run_in_threadpool(
                _append_archive_member,
                bundle,
                member_name,
                payload,
                asset.byte_size,
                asset.sha256,
            )

        await run_in_threadpool(bundle.close)
        archive_name = f"{job.type.replace('_', '-')}-{job.id}.zip"
        return ArchiveResponse(archive, archive_name)
    except BaseException:
        await run_in_threadpool(close_archive, bundle, archive)
        raise


@router.patch(
    "/workspaces/{workspace_id}/jobs/{job_id}",
    response_model=JobDetailResponse,
    tags=["jobs"],
)
def update_draft_job(
    job_id: str,
    payload: DraftJobUpdate,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> JobDetailResponse:
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    if JobStatus(job.status) != JobStatus.DRAFT:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_EDITABLE",
            "只有草稿任务可以修改。",
        )
    if payload.display_name is not None:
        job.display_name = payload.display_name
    if payload.configuration is not None:
        updated_configuration = dict(payload.configuration)
        updated_configuration.pop("customer_credit_pricing", None)
        if job.type == JobType.DETAIL_PACK.value:
            updated_configuration.pop("domestic_detail_pipeline", None)
            pipeline = job.configuration.get("domestic_detail_pipeline")
            if pipeline in {
                PIPELINE_VERSION,
                refined_detail.PIPELINE_VERSION,
                detail_fast.PIPELINE_VERSION,
            }:
                updated_configuration["domestic_detail_pipeline"] = pipeline
                # Skill selection is immutable once the job has been created.
                updated_settings = updated_configuration.get("settings")
                if not isinstance(updated_settings, dict):
                    raise ApiError(
                        422, "DETAIL_SETTINGS_REQUIRED", "详情任务设置不能为空。"
                    )
                original_settings = job.configuration.get("settings") or {}
                if any(
                    updated_settings.get(key) != original_settings.get(key)
                    for key in (
                        "detail_skill",
                        "detail_speed",
                        "market_region",
                        "website_contract_version",
                    )
                ):
                    raise ApiError(
                        409, "DETAIL_SKILL_IMMUTABLE", "请新建任务选择其他详情方案。"
                    )
        job.configuration = updated_configuration
    job.state_version += 1
    session.commit()
    return job_detail_response(session, job)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/submit",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def submit_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/submit",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job_id}),
    )
    if replay:
        return job_detail_response(session, job)
    if JobStatus(job.status) != JobStatus.DRAFT:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_SUBMITTABLE",
            "当前任务状态不能提交。",
            {"status": job.status},
        )
    from .amazon_auto_start import bind_submission as bind_amazon_submission
    from .domestic_detail.auto_start import bind_submission

    bind_submission(job, workspace.principal.user_id)
    bind_amazon_submission(job, workspace.principal.user_id)
    from .main_image_auto_start import bind_submission as bind_main_submission

    bind_main_submission(job, workspace.principal.user_id)
    from .crossborder_auto_start import bind_submission as bind_crossborder_submission

    bind_crossborder_submission(job, workspace.principal.user_id)
    enforce_active_job_limit(session, workspace.workspace_id)
    _reserve_real_provider_job_if_enabled(
        session,
        request,
        job,
        workspace.principal.user_id,
    )
    transition_job(job, JobStatus.QUEUED, progress=0)
    reserve_credits(
        session, workspace.workspace_id, job, cast(int, job.estimated_credits)
    )
    add_outbox_event(
        session,
        workspace_id=workspace.workspace_id,
        job_id=job.id,
        event_type="job.dispatch_requested",
        suffix="submit",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/cancel",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def cancel_job(
    job_id: str,
    payload: CancelRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/cancel",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job_id, **payload.model_dump()}),
    )
    if replay or JobStatus(job.status) == JobStatus.CANCELLED:
        return job_detail_response(session, job)
    current = JobStatus(job.status)
    if current in {JobStatus.PARTIAL, JobStatus.COMPLETED, JobStatus.FAILED}:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_CANCELLABLE",
            "当前任务状态不能取消。",
            {"status": current.value},
        )
    job.cancel_requested_at = utc_now()
    job.cancel_reason = payload.reason
    if current in {
        JobStatus.DRAFT,
        JobStatus.QUEUED,
        JobStatus.AWAITING_CONFIRMATION,
    }:
        cancellable_items = session.scalars(
            select(JobItemRecord)
            .where(
                JobItemRecord.job_id == job.id,
                JobItemRecord.status.in_(
                    [
                        JobItemStatus.PENDING.value,
                        JobItemStatus.QUEUED.value,
                        JobItemStatus.RUNNING.value,
                    ]
                ),
            )
            .with_for_update()
        ).all()
        for item in cancellable_items:
            item.status = JobItemStatus.CANCELLED.value
            item.finished_at = utc_now()
            item.state_version += 1
        transition_job(job, JobStatus.CANCELLED, progress=job.progress)
        settle_credits(session, job, 0)
    else:
        transition_job(job, JobStatus.CANCELLING, progress=job.progress)
        add_outbox_event(
            session,
            workspace_id=workspace.workspace_id,
            job_id=job.id,
            event_type="job.cancel_requested",
            suffix=str(job.state_version),
        )
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/retry",
    response_model=JobDetailResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["jobs"],
)
async def retry_job(
    job_id: str,
    payload: RetryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    key = require_idempotency_key(idempotency_header)
    source = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    if source.type == JobType.DOMESTIC_DETAIL_REVIEW.value:
        raise ApiError(
            409,
            "NATIVE_DETAIL_CAMPAIGN_RECONCILIATION_REQUIRED",
            "请先核对原审核请求；新的整页审核须从原详情结果明确授权后发起。",
        )
    manual_refined_revision = (
        source.type == JobType.DETAIL_PACK.value
        and detail_fast_runtime.is_independent(source.configuration)
        and source.status == JobStatus.COMPLETED.value
        and payload.scope == RetryScope.SELECTED
    )
    if not manual_refined_revision and JobStatus(source.status) not in {
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_RETRYABLE",
            "只有部分成功、失败或已取消任务可以重试。",
        )
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/retry",
        idempotency_key=key,
        request_hash=canonical_hash(
            {"job_id": job_id, **payload.model_dump(mode="json")}
        ),
    )
    if replay:
        return job_detail_response(
            session,
            get_workspace_job(
                session, workspace.workspace_id, cast(str, idem.resource_id)
            ),
        )
    replica_document = None
    document_id = source.configuration.get("replica_session_id")
    if source.type == JobType.REFERENCE_REMIX.value and document_id:
        from .models import ReplicaDocumentRecord
        from .replica_native import ReplicaNativeError
        from .replica_preserved_outputs import recover_outputs

        replica_document = session.scalar(
            select(ReplicaDocumentRecord)
            .where(
                ReplicaDocumentRecord.id == document_id,
                ReplicaDocumentRecord.workspace_id == workspace.workspace_id,
            )
            .with_for_update()
        )
        if replica_document is None or source.id not in replica_document.payload.get(
            "job_ids", []
        ):
            raise ApiError(
                409,
                "REPLICA_RETRY_SUPERSEDED",
                "请从当前复刻任务重试，旧任务已被接续。",
            )
        unknown_codes = {
            "PROVIDER_OUTCOME_UNKNOWN",
            "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED",
        }
        unknown = session.scalar(
            select(JobItemRecord.id)
            .where(
                JobItemRecord.job_id == source.id,
                JobItemRecord.error_code.in_(unknown_codes),
            )
            .limit(1)
        )
        if source.error_code in unknown_codes or unknown:
            raise ApiError(
                409,
                "REPLICA_OUTCOME_RECONCILIATION_REQUIRED",
                "原生图结果尚未核实，请先找回原任务，避免重复生成。",
            )
        try:
            await recover_outputs(
                session, request.app.state.storage, replica_document, source
            )
        except ReplicaNativeError as error:
            raise ApiError(409, "REPLICA_RECOVERY_INVALID", str(error)) from error
        payload = payload.model_copy(
            update={"scope": RetryScope.FAILED, "item_ids": []}
        )
    enforce_active_job_limit(session, workspace.workspace_id)
    selected = retry_item_ids(session, source, payload.scope, payload.item_ids)
    if not selected:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_RETRYABLE",
            "当前重试范围内没有可重试条目。",
        )
    _validate_domestic_detail_retry(session, source, selected)
    retried = _copy_job_for_retry(session, source, payload.scope, selected)
    session.add(retried)
    session.flush()
    _copy_job_relations_for_retry(session, source, retried, selected)
    if replica_document is not None:
        values = dict(replica_document.payload)
        replica_document.status = "generating"
        replica_document.payload = {
            **{
                k: v
                for k, v in values.items()
                if k not in {"error", "error_code", "execution_progress"}
            },
            "job_ids": [
                retried.id if jid == source.id else jid for jid in values["job_ids"]
            ],
            "retry_events": [
                *values.get("retry_events", []),
                {"from": source.id, "to": retried.id, "request_id": key},
            ],
        }
    session.flush()
    if manual_refined_revision:
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == retried.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        if plan is None:
            raise ApiError(409, "PLAN_NOT_READY", "原任务缺少可修订的完整方案。")
        # User-requested corrections preserve every unselected artifact and
        # require a fresh full-plan approval before any selected page is paid.
        retried.status = JobStatus.AWAITING_CONFIRMATION.value
        retried.stage = "awaiting_confirmation"
        retried.queued_at = None
        plan.confirmed_at = None
        plan.payload = {**dict(plan.payload), "state": "awaiting_confirmation"}
        plan.payload_hash = canonical_hash(plan.payload)
        session.add(
            AuditEventRecord(
                workspace_id=workspace.workspace_id,
                action="refined.manual_revision_created",
                resource_type="job",
                resource_id=retried.id,
                request_id=plan.id,
                details={
                    "source_job_id": source.id,
                    "selected_item_ids": selected,
                    "reused_pages": retried.succeeded_item_count,
                    "external_requests": 0,
                },
            )
        )
        complete_idempotency(
            idem, resource_type="job", resource_id=retried.id, response_status=201
        )
        session.commit()
        return job_detail_response(session, retried)
    _ensure_parallel_aurora_retry_snapshots(
        session,
        retried,
        work_root=request.app.state.settings.aurora_work_root,
    )
    release_credits(session, source)
    _reserve_real_provider_job_if_enabled(
        session,
        request,
        retried,
        workspace.principal.user_id,
        runnable_item_count=len(selected),
    )
    reserve_credits(
        session,
        workspace.workspace_id,
        retried,
        cast(int, retried.estimated_credits),
    )
    add_outbox_event(
        session,
        workspace_id=workspace.workspace_id,
        job_id=retried.id,
        event_type="job.dispatch_requested",
        suffix="retry",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=retried.id, response_status=201
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, retried)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/clone",
    response_model=JobDetailResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["jobs"],
)
def clone_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    source = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    if source.type == JobType.DOMESTIC_DETAIL_REVIEW.value:
        raise ApiError(
            409,
            "NATIVE_DETAIL_CAMPAIGN_RECONCILIATION_REQUIRED",
            "请先核对原审核请求；新的整页审核须从原详情结果明确授权后发起。",
        )
    if JobStatus(source.status) not in TERMINAL_JOB_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_CLONEABLE",
            "只有终态任务可以复制。",
        )
    key = require_idempotency_key(idempotency_header)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/clone",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job_id}),
    )
    if replay:
        return job_detail_response(
            session,
            get_workspace_job(
                session, workspace.workspace_id, cast(str, idem.resource_id)
            ),
        )
    enforce_active_job_limit(session, workspace.workspace_id)
    source_items = session.scalars(
        select(JobItemRecord).where(JobItemRecord.job_id == source.id)
    ).all()
    selected = [item.id for item in source_items]
    _validate_domestic_detail_retry(session, source, selected, clone=True)
    cloned = _copy_job_for_retry(session, source, RetryScope.FULL, selected)
    cloned.root_job_id = None
    cloned.retry_of_job_id = None
    cloned.retry_scope = None
    session.add(cloned)
    session.flush()
    _copy_job_relations_for_retry(session, source, cloned, selected)
    session.flush()
    _ensure_parallel_aurora_retry_snapshots(
        session,
        cloned,
        work_root=request.app.state.settings.aurora_work_root,
    )
    release_credits(session, source)
    _reserve_real_provider_job_if_enabled(
        session,
        request,
        cloned,
        workspace.principal.user_id,
        runnable_item_count=len(selected),
    )
    reserve_credits(
        session,
        workspace.workspace_id,
        cloned,
        cast(int, cloned.estimated_credits),
    )
    add_outbox_event(
        session,
        workspace_id=workspace.workspace_id,
        job_id=cloned.id,
        event_type="job.dispatch_requested",
        suffix="clone",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=cloned.id, response_status=201
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, cloned)


@router.put(
    "/workspaces/{workspace_id}/jobs/{job_id}/plan",
    response_model=PlanResponse,
    tags=["jobs"],
)
def update_plan(
    job_id: str,
    payload: PlanUpdateRequest,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> PlanResponse:
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    if JobStatus(job.status) != JobStatus.AWAITING_CONFIRMATION:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_EDITABLE",
            "当前任务不允许修改计划。",
        )
    current = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if current is None or current.revision != payload.expected_revision:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PLAN_REVISION_CONFLICT",
            "计划已更新，请刷新后重试。",
        )
    editable_payload = dict(payload.payload)
    settings = (
        job.configuration.get("settings")
        if isinstance(job.configuration, Mapping)
        else None
    )
    if (
        job.type == JobType.DETAIL_PACK.value
        and isinstance(settings, Mapping)
        and settings.get("market_region") == "domestic"
        and not detail_fast_runtime.is_independent(job.configuration)
    ):
        if job.stage == "intake_confirmation_required":
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "INTAKE_CONFIRMATION_REQUIRED",
                "请先确认商品事实，再审阅完整规划。",
            )
        from .domestic_detail.plans import resolve_native_plan

        try:
            old_native = resolve_native_plan(current.payload)
            new_native = resolve_native_plan(editable_payload)
            if (
                old_native is None
                or new_native is None
                or any(
                    editable_payload.get(field) != current.payload.get(field)
                    for field in (
                        "product_intake",
                        "native_approval",
                        "input_signature",
                        "source_input_sha256",
                        "source_asset_manifest",
                    )
                )
            ):
                raise DetailPlanError(
                    "Source intake and original source manifests are immutable"
                )
        except DetailPlanError as exc:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "INTAKE_AUTHORITY_IMMUTABLE",
                "规划编辑不能改写原始商品观察或素材来源。",
                {"reason": str(exc)},
            ) from exc
        # Rebind the campaign approval document whenever the plan is edited;
        # an old approval fingerprint may never authorize changed pages.
        from .aurora_detail_runtime import (
            AuroraDetailHarnessError,
            AuroraDetailHarnessRunner,
            detail_workspace,
            normalize_native_plan_for_harness,
        )
        from .domestic_detail.native_approval import (
            compile_native_approval,
            load_native_approval,
        )
        from .domestic_detail.plans import validate_plan_views
        from .xinghe_agent import compile_aurora_campaign_approval_document

        groups = editable_payload.get("groups")
        pages = editable_payload.get("pages")
        if isinstance(groups, list) and isinstance(pages, list):
            try:
                new_native = validate_plan_views(editable_payload)
                original_contracts = load_native_approval(
                    detail_workspace(job.id), current.payload.get("native_approval")
                )
                editable_payload["native_approval"] = compile_native_approval(
                    detail_workspace(job.id),
                    runner=AuroraDetailHarnessRunner(),
                    native_input=original_contracts["input"],
                    identity=original_contracts["identity"],
                    normalized_plan=normalize_native_plan_for_harness(new_native),
                    resolution=(current.payload.get("product_intake") or {}).get(
                        "resolution"
                    ),
                )
                detail_binding = approval_binding(editable_payload, settings)
            except (ValueError, TypeError, KeyError, AuroraDetailHarnessError, OSError):
                detail_binding = None
            aurora = editable_payload.get("aurora_v4_3")
            if isinstance(aurora, dict):
                aurora = dict(aurora)
                aurora.pop("approval_receipt", None)
                aurora["contract_binding"] = detail_binding
                if detail_binding is None:
                    aurora["approval_document_markdown"] = ""
                    aurora["approval_document_sha256"] = None
                if detail_binding is not None:
                    approval_document = compile_aurora_campaign_approval_document(
                        group_plans=[
                            group for group in groups if isinstance(group, Mapping)
                        ],
                        pages=[page for page in pages if isinstance(page, Mapping)],
                        page_count=int(settings.get("page_count", 0)),
                        binding=detail_binding,
                        native_document=editable_payload["native_approval"][
                            "document_markdown"
                        ],
                    )
                    aurora["approval_document_markdown"] = approval_document
                    aurora["approval_document_sha256"] = hashlib.sha256(
                        approval_document.encode("utf-8")
                    ).hexdigest()
                editable_payload["aurora_v4_3"] = aurora
    validation_errors = _validate_plan_payload(job, editable_payload)
    plan = JobPlanRecord(
        id=new_id("plan"),
        workspace_id=workspace.workspace_id,
        job_id=job.id,
        revision=current.revision + 1,
        schema_version="v1",
        payload=editable_payload,
        payload_hash=canonical_hash(editable_payload),
        validation_status=(
            PlanValidationStatus.INVALID.value
            if validation_errors
            else PlanValidationStatus.VALID.value
        ),
        validation_errors=validation_errors,
        validated_at=utc_now(),
    )
    session.add(plan)
    job.state_version += 1
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PLAN_REVISION_CONFLICT",
            "计划已更新，请刷新后重试。",
        ) from exc
    return PlanResponse(
        id=plan.id,
        revision=plan.revision,
        schema_version=plan.schema_version,
        payload=plan.payload,
        validation_status=plan.validation_status,
        validation_errors=plan.validation_errors,
        confirmed_at=plan.confirmed_at,
    )


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/intake/brand/continue",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def continue_detail_brand(
    job_id: str,
    payload: DetailAutomaticBrandRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    from .domestic_detail import automatic_brand
    from .domestic_detail.generation import PIPELINE_VERSION

    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/intake/brand/continue",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job.id, **payload.model_dump()}),
    )
    if replay:
        return job_detail_response(session, job)
    settings = job.configuration.get("settings") or {}
    if (
        job.type != JobType.DETAIL_PACK.value
        or job.configuration.get("domestic_detail_pipeline") != PIPELINE_VERSION
        or not automatic_brand.enabled(settings)
        or job.status != JobStatus.AWAITING_CONFIRMATION.value
        or job.stage != "intake_confirmation_required"
        or job.cancel_requested_at is not None
    ):
        raise ApiError(
            409, "INTAKE_NOT_AWAITING_BRAND", "当前任务没有待后台处理的品牌信息。"
        )
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
        .with_for_update()
    )
    if plan is None or plan.revision != payload.expected_revision:
        raise ApiError(409, "PLAN_REVISION_CONFLICT", "商品信息已更新，请刷新后重试。")
    intake = plan.payload.get("product_intake")
    if (
        not isinstance(intake, dict)
        or canonical_hash(intake) != plan.payload.get("intake_question_sha256")
        or canonical_hash(settings) != plan.payload.get("intake_settings_sha256")
    ):
        raise ApiError(
            409, "INTAKE_BINDING_CHANGED", "商品信息来源已变化，请刷新后重试。"
        )
    facts = {str(row.get("id")): row for row in intake.get("observed_facts", [])}
    if not intake.get("brand_confirmation_required") and not any(
        automatic_brand.brand_conflict(row, facts)
        for row in intake.get("source_conflicts", [])
    ):
        raise ApiError(
            409, "INTAKE_NOT_AWAITING_BRAND", "当前需要处理的是商品规格信息。"
        )
    manifest = plan.payload.get("intake_source_manifest")
    if not isinstance(manifest, list) or not manifest:
        raise ApiError(409, "INTAKE_BINDING_CHANGED", "商品图片来源已变化。")
    for source in manifest:
        asset = session.get(AssetRecord, source["asset_id"])
        role_exists = session.scalar(
            select(JobAssetRecord.id)
            .where(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.asset_id == source["asset_id"],
                JobAssetRecord.role == source["role"],
                JobAssetRecord.direction == JobAssetDirection.INPUT.value,
            )
            .limit(1)
        )
        if (
            asset is None
            or asset.workspace_id != job.workspace_id
            or asset.deleted_at is not None
            or asset.sha256 != source["sha256"]
            or role_exists is None
        ):
            raise ApiError(409, "INTAKE_BINDING_CHANGED", "商品图片或来源用途已变化。")
    receipt = {
        "version": automatic_brand.VERSION,
        "decision_source": "website_default",
        "source_intake_sha256": canonical_hash(intake),
        "product_brandless_claim": False,
        "requested_at": utc_now().isoformat(),
    }
    job.configuration = {**job.configuration, "automatic_brand_continuation": receipt}
    plan.payload = {
        **plan.payload,
        "state": "automatic_brand_continuation",
        "automatic_brand_continuation": receipt,
        "planner_lease_expires_at": None,
    }
    plan.payload_hash = canonical_hash(plan.payload)
    plan.revision += 1
    transition_job(job, JobStatus.PLANNING, stage="analyzing_product", progress=10)
    session.add(
        AuditEventRecord(
            workspace_id=job.workspace_id,
            action="detail_brand.automatic_continuation",
            resource_type="job",
            resource_id=job.id,
            request_id=job.id,
            details=receipt,
        )
    )
    add_outbox_event(
        session,
        workspace_id=job.workspace_id,
        job_id=job.id,
        event_type="job.dispatch_requested",
        suffix=f"automatic-brand-{plan.revision}",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/intake/resolve",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def resolve_detail_intake(
    job_id: str,
    payload: DetailIntakeResolutionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    from .domestic_detail.facts import DetailIntakeError
    from .domestic_detail.intake import validate_resolution

    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/intake/resolve",
        idempotency_key=key,
        request_hash=canonical_hash(
            {"job_id": job.id, **payload.model_dump(mode="json")}
        ),
    )
    if replay:
        return job_detail_response(session, job)
    if (
        job.type != JobType.DETAIL_PACK.value
        or job.status != JobStatus.AWAITING_CONFIRMATION.value
        or job.stage != "intake_confirmation_required"
    ):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INTAKE_NOT_AWAITING_CONFIRMATION",
            "当前没有待确认的商品事实。",
        )
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
        .with_for_update()
    )
    if plan is None or plan.revision != payload.expected_revision:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PLAN_REVISION_CONFLICT",
            "事实确认内容已更新，请刷新后重试。",
        )
    intake = plan.payload.get("product_intake")
    if not isinstance(intake, dict) or canonical_hash(intake) != plan.payload.get(
        "intake_question_sha256"
    ):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INTAKE_BINDING_CHANGED",
            "事实来源已变化，请重新核对。",
        )
    if canonical_hash(job.configuration.get("settings") or {}) != plan.payload.get(
        "intake_settings_sha256"
    ):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INTAKE_BINDING_CHANGED",
            "商品事实已变化，不能沿用旧确认。",
        )
    for source in plan.payload.get("intake_source_manifest", []):
        asset = session.get(AssetRecord, source["asset_id"])
        role_exists = session.scalar(
            select(JobAssetRecord.id)
            .where(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.asset_id == source["asset_id"],
                JobAssetRecord.role == source["role"],
                JobAssetRecord.direction == JobAssetDirection.INPUT.value,
            )
            .limit(1)
        )
        if (
            asset is None
            or asset.workspace_id != job.workspace_id
            or asset.deleted_at is not None
            or asset.sha256 != source["sha256"]
            or role_exists is None
        ):
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "INTAKE_BINDING_CHANGED",
                "商品图片或来源用途已变化，不能沿用旧确认。",
            )
    if job.configuration.get("domestic_detail_intake_resolution"):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "INTAKE_RESOLUTION_ALREADY_APPLIED",
            "本次事实确认已保存，不能重复改写原确认。",
        )
    resolution: dict[str, Any] = {
        "confirmation_text": payload.confirmation_text,
        "resolutions": [row.model_dump(mode="json") for row in payload.resolutions],
    }
    try:
        validate_resolution(intake, resolution)
    except DetailIntakeError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            exc.code,
            "请一次确认全部冲突值，并明确排除其余值。",
            {"reason": str(exc)},
        ) from exc
    if (
        intake.get("brand_confirmation_required")
        and not payload.brandless_confirmation
        and not payload.additional_product_asset_ids
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INTAKE_BRAND_CONFIRMATION_REQUIRED",
            "请明确确认无品牌，或补充清晰的本商品品牌图片。",
        )
    if (
        payload.brandless_confirmation is not None
        and len(payload.brandless_confirmation.strip()) < 2
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INTAKE_BRAND_CONFIRMATION_REQUIRED",
            "请明确填写无品牌确认。",
        )
    style_ids = set(job.configuration["input"].get("reference_asset_ids", []))
    if style_ids.intersection(payload.additional_product_asset_ids):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INTAKE_SOURCE_AUTHORITY_INVALID",
            "风格参考不能改作商品品牌证据，请补充本商品图片。",
        )
    configuration = deepcopy(job.configuration)
    product_ids = configuration["input"]["product_groups"][0]["asset_ids"]
    for asset_id in payload.additional_product_asset_ids:
        asset = session.scalar(
            select(AssetRecord).where(
                AssetRecord.id == asset_id,
                AssetRecord.workspace_id == job.workspace_id,
                AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
                AssetRecord.deleted_at.is_(None),
            )
        )
        if asset is None:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INPUT_ASSET_NOT_FOUND",
                "补充图片不存在或不属于当前工作区。",
            )
        if (
            asset.media_kind != AssetMediaKind.IMAGE.value
            or not asset.content_type.startswith("image/")
        ):
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "INTAKE_PRODUCT_IMAGE_REQUIRED",
                "补充素材必须是商品图片。",
            )
        if asset_id not in product_ids:
            product_ids.append(asset_id)
            session.add(
                JobAssetRecord(
                    workspace_id=job.workspace_id,
                    job_id=job.id,
                    asset_id=asset_id,
                    direction=JobAssetDirection.INPUT.value,
                    role="product_reference",
                    ordinal=len(product_ids) - 1,
                    is_deliverable=False,
                    validation={},
                )
            )
    if len(product_ids) > 8:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INTAKE_SOURCE_LIMIT",
            "商品素材总数不能超过 8 张。",
        )
    resolution.update(
        {
            "brandless_confirmation": payload.brandless_confirmation,
            "source_intake": deepcopy(intake),
            "source_intake_sha256": canonical_hash(intake),
            "actor_user_id": workspace.principal.user_id,
            "confirmed_at": utc_now().isoformat(),
        }
    )
    configuration["domestic_detail_intake_resolution"] = resolution
    job.configuration = configuration
    plan.payload = {
        **dict(plan.payload),
        "state": "intake_resolved",
        "intake_resolution": resolution,
        "planner_lease_expires_at": None,
    }
    plan.payload_hash = canonical_hash(plan.payload)
    plan.revision += 1
    transition_job(
        job, JobStatus.PLANNING, stage="analyzing_confirmed_sources", progress=10
    )
    add_outbox_event(
        session,
        workspace_id=job.workspace_id,
        job_id=job.id,
        event_type="job.dispatch_requested",
        suffix=f"intake-{plan.revision}",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/plan/automatic-delivery-authorization",
    response_model=DomesticDetailAutomaticDeliveryAuthorization,
    tags=["jobs"],
)
def get_automatic_detail_delivery_authorization(
    job_id: str,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> DomesticDetailAutomaticDeliveryAuthorization:
    from .aurora_detail_runtime import detail_workspace
    from .domestic_detail.delivery_authorization import authorization_quote
    from .domestic_detail.native_approval import verify_approval_sources

    job = get_workspace_job(session, workspace.workspace_id, job_id)
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if (
        job.type != JobType.DETAIL_PACK.value
        or job.configuration.get("domestic_detail_pipeline") != PIPELINE_VERSION
        or job.status != "awaiting_confirmation"
        or job.stage == "intake_confirmation_required"
        or plan is None
        or plan.validation_status != "valid"
    ):
        raise ApiError(
            409,
            "NATIVE_DETAIL_AUTO_DELIVERY_NOT_READY",
            "请先完成商品事实确认并审阅当前完整方案。",
        )
    try:
        validate_approval_binding(plan.payload, job.configuration["settings"])
        verify_approval_sources(
            detail_workspace(job.id),
            plan.payload.get("native_approval"),
            plan.payload["source_asset_manifest"][0]["assets"],
        )
        quote = authorization_quote(
            job_id=job.id,
            workspace_id=job.workspace_id,
            actor_id=workspace.principal.user_id,
            plan_id=plan.id,
            revision=plan.revision,
            payload=plan.payload,
        )
    except (ValueError, OSError, KeyError) as exc:
        raise ApiError(
            409, "NATIVE_DETAIL_AUTO_DELIVERY_AUTHORIZATION_INVALID", str(exc)
        ) from exc
    return DomesticDetailAutomaticDeliveryAuthorization.model_validate(quote)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/plan/confirm",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def confirm_plan(
    job_id: str,
    payload: PlanConfirmRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if plan is None or plan.revision != payload.expected_revision:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PLAN_REVISION_CONFLICT",
            "计划版本不一致。",
        )
    if plan.validation_status != PlanValidationStatus.VALID.value:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PLAN_INVALID",
            "计划未通过校验，不能开始生成。",
        )
    current_status = JobStatus(job.status)
    if current_status == JobStatus.PROCESSING and plan.confirmed_at is not None:
        return job_detail_response(session, job)
    if current_status != JobStatus.AWAITING_CONFIRMATION:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_AWAITING_CONFIRMATION",
            "任务当前状态不允许确认计划。",
        )
    _ensure_detail_skill_provider_enabled(request, job)
    automatic_delivery_authorization = None
    auto_fields_supplied = any(
        value is not None
        for value in (
            payload.domestic_detail_delivery_mode,
            payload.automatic_delivery_authorization_text,
            payload.automatic_delivery_authorization_sha256,
        )
    )
    if auto_fields_supplied and not (
        job.type == JobType.DETAIL_PACK.value
        and job.configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION
    ):
        raise ApiError(
            409,
            "NATIVE_DETAIL_AUTO_DELIVERY_SCOPE_INVALID",
            "自动交付授权只适用于当前国内详情方案。",
        )
    if job.type == JobType.DETAIL_PACK.value:
        settings = job.configuration.get("settings")
        if (
            isinstance(settings, dict)
            and settings.get("market_region") == "domestic"
            and not detail_fast_runtime.is_independent(job.configuration)
        ):
            aurora = plan.payload.get("aurora_v4_3")
            if (
                not isinstance(payload.approval_text, str)
                or len(payload.approval_text.strip()) < 2
            ):
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "AURORA_APPROVAL_TEXT_REQUIRED",
                    "请明确确认已审阅完整的 group→screen 审批包。",
                )
            if (
                not isinstance(aurora, dict)
                or aurora.get("approval_required") is not True
            ):
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "AURORA_APPROVAL_PACKET_MISSING",
                    "Aurora 详情图必须先生成完整的 group→screen 审批包。",
                )
            try:
                from .aurora_detail_runtime import detail_workspace
                from .domestic_detail.native_approval import verify_approval_sources

                validate_approval_binding(plan.payload, settings)
                verify_approval_sources(
                    detail_workspace(job.id),
                    plan.payload.get("native_approval"),
                    plan.payload["source_asset_manifest"][0]["assets"],
                )
            except DetailPlanError as exc:
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "AURORA_APPROVAL_BINDING_INVALID",
                    "商品输入、完整方案或 Skill 已变化，请重新审阅当前审批包。",
                    {"reason": str(exc)},
                ) from exc
            if (
                payload.approval_document_sha256
                != aurora.get("approval_document_sha256")
                or payload.approved_module_count != aurora.get("approved_module_count")
                or payload.approved_pair_canvas_aspect_ratio
                != aurora.get("approved_pair_canvas_aspect_ratio")
            ):
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "AURORA_APPROVAL_PACKET_MISMATCH",
                    "审批包指纹、屏数或 3:8 pair 画布比例不匹配。",
                )
            native_approval = plan.payload["native_approval"]
            if (
                payload.approval_protocol != native_approval["version"]
                or payload.native_approval_document_sha256
                != native_approval["document_sha256"]
            ):
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    "AURORA_NATIVE_APPROVAL_REQUIRED",
                    "请使用当前完整原生审批文档和版本指纹确认方案。",
                )
    if detail_fast_runtime.is_independent(job.configuration):
        approval = plan.payload.get("refined_approval") or {}
        if (
            not detail_fast_runtime.contract(job.configuration).approval_matches(
                plan.payload
            )
            or payload.approval_document_sha256 != approval.get("document_sha256")
            or not payload.approval_text
            or len(payload.approval_text.strip()) < 2
        ):
            raise ApiError(
                409,
                "REFINED_DETAIL_APPROVAL_REQUIRED",
                "请审阅并确认当前精美详情方案。",
            )
    if (
        job.type == JobType.DETAIL_PACK.value
        and job.configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION
    ):
        from .domestic_detail.delivery_authorization import confirm_authorization

        try:
            automatic_delivery_authorization = confirm_authorization(
                mode=payload.domestic_detail_delivery_mode,
                text=payload.automatic_delivery_authorization_text,
                fingerprint=payload.automatic_delivery_authorization_sha256,
                job_id=job.id,
                workspace_id=job.workspace_id,
                actor_id=workspace.principal.user_id,
                plan_id=plan.id,
                revision=plan.revision,
                payload=plan.payload,
            )
        except ValueError as exc:
            raise ApiError(
                409, "NATIVE_DETAIL_AUTO_DELIVERY_AUTHORIZATION_INVALID", str(exc)
            ) from exc
    if job.type == JobType.DOMESTIC_MAIN_IMAGE.value:
        settings = request.app.state.settings
        if (
            settings.task_runner != "tengli"
            or not settings.domestic_main_image_provider_enabled
        ):
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "DOMESTIC_MAIN_IMAGE_PROVIDER_UNAVAILABLE",
                "真实六图生成服务尚未启用；当前 Mock 只能编译规划，不能授权生图。",
            )
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/plan/confirm",
        idempotency_key=key,
        request_hash=canonical_hash(
            {
                **(
                    {
                        "approval_protocol": payload.approval_protocol,
                        "native_approval_document_sha256": (
                            payload.native_approval_document_sha256
                        ),
                    }
                    if job.type == JobType.DETAIL_PACK.value
                    and isinstance(job.configuration.get("settings"), dict)
                    and job.configuration["settings"].get("market_region") == "domestic"
                    else {}
                ),
                **(
                    {
                        "automatic_delivery_authorization": (
                            automatic_delivery_authorization
                        )
                    }
                    if automatic_delivery_authorization is not None
                    else {}
                ),
                "job_id": job.id,
                "expected_revision": payload.expected_revision,
                "approval_text": payload.approval_text,
                "approval_document_sha256": payload.approval_document_sha256,
                "approved_module_count": payload.approved_module_count,
                "approved_pair_canvas_aspect_ratio": (
                    payload.approved_pair_canvas_aspect_ratio
                ),
            }
        ),
    )
    if replay:
        return job_detail_response(session, job)
    if job.type == JobType.DETAIL_PACK.value:
        from .xinghe_agent import (
            XingheAgentError,
            synchronize_confirmed_plan,
        )

        try:
            synchronize_confirmed_plan(session, job, plan)
        except XingheAgentError as exc:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "PLAN_INVALID",
                "The plan is invalid and cannot be confirmed.",
                {"reason": exc.code},
            ) from exc
    elif job.type == JobType.AMAZON_VISUAL_SUITE.value:
        from .amazon_visual_suite_provider import (
            AdapterValidationError,
            synchronize_confirmed_amazon_plan,
        )
        from .xinghe_agent import XingheAgentError

        try:
            synchronize_confirmed_amazon_plan(session, job, plan)
        except (AdapterValidationError, XingheAgentError) as exc:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "PLAN_INVALID",
                "The Amazon plan is invalid and cannot be confirmed.",
                {"reason": exc.code},
            ) from exc
    elif job.type == JobType.DOMESTIC_MAIN_IMAGE.value:
        from .aurora_main_image_planner import (
            AuroraPlanningError,
            authorize_aurora_plan,
        )
        from .aurora_main_image_runtime import AuroraHarnessError

        try:
            authorize_aurora_plan(
                plan,
                job,
                payload.approval_text or "用户确认六图规划并授权逐张生成",
                work_root=request.app.state.settings.aurora_work_root,
            )
        except (AuroraPlanningError, AuroraHarnessError) as exc:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "AURORA_AUTHORIZATION_FAILED",
                "六图规划未能通过 Aurora Harness 授权，不能开始生成。",
                {"reason": getattr(exc, "code", type(exc).__name__)},
            ) from exc
    if (
        job.type == JobType.DETAIL_PACK.value
        and isinstance(job.configuration.get("settings"), dict)
        and job.configuration["settings"].get("market_region") == "domestic"
        and not detail_fast_runtime.is_independent(job.configuration)
    ):
        detail_payload = deepcopy(plan.payload)
        detail_aurora = detail_payload["aurora_v4_3"]
        detail_aurora["approval_receipt"] = {
            **(
                {"automatic_delivery_authorization": automatic_delivery_authorization}
                if automatic_delivery_authorization is not None
                else {}
            ),
            "approval_protocol": payload.approval_protocol,
            "native_approval_document_sha256": payload.native_approval_document_sha256,
            "pipeline": PIPELINE_VERSION,
            "approval_text": payload.approval_text,
            "approval_document_sha256": payload.approval_document_sha256,
            "contract_binding_sha256": detail_aurora["contract_binding"]["sha256"],
            "approved_at": utc_now().isoformat(),
            "actor_user_id": workspace.principal.user_id,
            "plan_revision": plan.revision,
        }
        plan.payload = detail_payload
    if (
        detail_fast_runtime.is_independent(job.configuration)
        and job.retry_of_job_id is not None
        and job.retry_scope == RetryScope.SELECTED.value
    ):
        runnable_count = session.scalar(
            select(func.count())
            .select_from(JobItemRecord)
            .where(
                JobItemRecord.job_id == job.id,
                JobItemRecord.status == JobItemStatus.PENDING.value,
            )
        )
        _reserve_real_provider_job_if_enabled(
            session,
            request,
            job,
            workspace.principal.user_id,
            runnable_item_count=int(runnable_count or 0),
        )
        reserve_credits(
            session, workspace.workspace_id, job, cast(int, job.estimated_credits)
        )
    plan.confirmed_at = utc_now()
    transition_job(job, JobStatus.PROCESSING, stage="generating", progress=20)
    add_outbox_event(
        session,
        workspace_id=workspace.workspace_id,
        job_id=job.id,
        event_type="job.dispatch_requested",
        suffix=f"plan-{plan.revision}",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


def _campaign_report_for_job(
    session: Session, workspace_id: str, job_id: str
) -> tuple[dict[str, Any], bytes]:
    from .domestic_detail.campaign_reports import load_campaign_report
    from .domestic_detail.delivery import DetailDeliveryError

    job = get_workspace_job(session, workspace_id, job_id)
    try:
        return load_campaign_report(job)
    except (DetailDeliveryError, ValueError, OSError, KeyError) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_CAMPAIGN_REPORT_INVALID"),
            str(exc),
        ) from exc


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/report",
    response_model=DomesticCampaignReviewReport,
    tags=["jobs"],
)
def get_domestic_campaign_report(
    job_id: str,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> DomesticCampaignReviewReport:
    report, _ = _campaign_report_for_job(session, workspace.workspace_id, job_id)
    return DomesticCampaignReviewReport.model_validate(report)


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/preview.png",
    response_class=Response,
    tags=["jobs"],
    responses={
        200: {
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}
        }
    },
)
def get_domestic_campaign_preview(
    job_id: str,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> Response:
    _, preview = _campaign_report_for_job(session, workspace.workspace_id, job_id)
    return Response(
        preview,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'inline; filename="campaign-preview.png"',
        },
    )


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/recovery-quote",
    response_model=DomesticCampaignRecoveryQuote,
    tags=["jobs"],
)
def quote_domestic_campaign_recovery(
    job_id: str,
    request: Request,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> DomesticCampaignRecoveryQuote:
    from .domestic_detail.campaign_recovery import recovery_quote, recovery_source
    from .domestic_detail.delivery import DetailDeliveryError

    parent = get_workspace_job(session, workspace.workspace_id, job_id)
    try:
        evidence = asyncio.run(
            recovery_source(session, parent, request.app.state.storage)
        )
        return DomesticCampaignRecoveryQuote.model_validate(
            recovery_quote(parent, evidence)
        )
    except (
        DetailDeliveryError,
        DetailGenerationError,
        DetailPlanError,
        ValueError,
        OSError,
        KeyError,
    ) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_SAVED_RESULT_UNAVAILABLE"),
            str(exc),
        ) from exc


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/recover",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def recover_domestic_campaign_result(
    job_id: str,
    payload: DomesticCampaignRecoveryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    from .domestic_detail.campaign_recovery import create_recovery_job
    from .domestic_detail.delivery import DetailDeliveryError

    key = require_idempotency_key(idempotency_header)
    parent = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/recover",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job_id, **payload.model_dump()}),
    )
    if replay:
        return job_detail_response(
            session,
            get_workspace_job(
                session, workspace.workspace_id, cast(str, idem.resource_id)
            ),
        )
    try:
        recovery = asyncio.run(
            create_recovery_job(
                session,
                parent,
                request.app.state.storage,
                actor_id=workspace.principal.user_id,
                journal_sha256=payload.original_result_journal_sha256,
                quote_sha256=payload.quote_sha256,
            )
        )
    except (
        DetailDeliveryError,
        DetailGenerationError,
        DetailPlanError,
        ValueError,
        OSError,
        KeyError,
    ) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_SAVED_RESULT_UNAVAILABLE"),
            str(exc),
        ) from exc
    if recovery.status == "queued":
        add_outbox_event(
            session,
            workspace_id=workspace.workspace_id,
            job_id=recovery.id,
            event_type="job.dispatch_requested",
            suffix="saved-original-result",
        )
    complete_idempotency(
        idem, resource_type="job", resource_id=recovery.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, recovery)


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-reviews",
    response_model=JobListResponse,
    tags=["jobs"],
)
def list_domestic_campaign_reviews(
    job_id: str,
    workspace: WorkspaceDependency,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> JobListResponse:
    source = get_workspace_job(session, workspace.workspace_id, job_id)
    if (
        source.type != JobType.DETAIL_PACK.value
        or source.configuration.get("domestic_detail_pipeline") != PIPELINE_VERSION
    ):
        raise ApiError(
            409,
            "NATIVE_DETAIL_CAMPAIGN_SOURCE_NOT_READY",
            "当前任务不是可用的国内详情来源。",
        )
    scope = canonical_hash(
        {
            "workspace_id": workspace.workspace_id,
            "source_job_id": source.id,
            "kind": "domestic-campaign-history-v1",
        }
    )
    filters = [
        JobRecord.workspace_id == workspace.workspace_id,
        JobRecord.type == JobType.DOMESTIC_DETAIL_REVIEW.value,
        JobRecord.configuration["source_job_id"].as_string() == source.id,
        job_history_is_available(),
    ]
    if cursor:
        created_raw, review_id = decode_cursor(cursor, scope)
        created = datetime.fromisoformat(created_raw)
        filters.append(
            or_(
                JobRecord.created_at < created,
                and_(JobRecord.created_at == created, JobRecord.id < review_id),
            )
        )
    rows = session.scalars(
        select(JobRecord)
        .where(*filters)
        .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
        .limit(limit + 1)
    ).all()
    items = rows[:limit]
    return JobListResponse(
        items=[job_list_response(item) for item in items],
        next_cursor=encode_cursor(items[-1].created_at, items[-1].id, scope)
        if len(rows) > limit and items
        else None,
        has_more=len(rows) > limit,
    )


def _campaign_review_provider(request: Request) -> Any:
    provider = request.app.state.task_runner.coordinator.provider.routes.get(
        JobType.DOMESTIC_DETAIL_REVIEW.value
    )
    if provider is None or not callable(getattr(provider, "describe_route", None)):
        raise ApiError(
            503, "NATIVE_DETAIL_CAMPAIGN_UNAVAILABLE", "整页审核服务当前不可用。"
        )
    return provider


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-review/quote",
    response_model=DomesticCampaignReviewQuote,
    tags=["jobs"],
)
def quote_domestic_campaign_review(
    job_id: str,
    request: Request,
    workspace: WorkspaceDependency,
    session: SessionDependency,
    final_review_sha256: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
) -> DomesticCampaignReviewQuote:
    from .domestic_detail.campaign_jobs import campaign_quote, source_scope
    from .domestic_detail.delivery import DetailDeliveryError

    source = get_workspace_job(session, workspace.workspace_id, job_id)
    try:
        _, context, _, _, _ = asyncio.run(
            source_scope(
                session,
                source,
                request.app.state.storage,
                final_review_sha256,
            )
        )
        route = _campaign_review_provider(request).describe_route()
        quote = campaign_quote(
            source.id, final_review_sha256, context, request.app.state.settings, route
        )
    except (
        DetailDeliveryError,
        DetailGenerationError,
        DetailPlanError,
        ValueError,
        OSError,
        KeyError,
    ) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_CAMPAIGN_INVALID"),
            str(exc),
        ) from exc
    return DomesticCampaignReviewQuote.model_validate(quote)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/campaign-review",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def create_domestic_campaign_review(
    job_id: str,
    payload: DomesticCampaignReviewRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    from .domestic_detail.campaign_jobs import create_campaign_review_job
    from .domestic_detail.delivery import DetailDeliveryError

    key = require_idempotency_key(idempotency_header)
    source = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/campaign-review",
        idempotency_key=key,
        request_hash=canonical_hash(
            {"source_job_id": source.id, **payload.model_dump()}
        ),
    )
    if replay:
        return job_detail_response(
            session,
            get_workspace_job(
                session,
                workspace.workspace_id,
                cast(str, idem.resource_id),
            ),
        )
    try:
        review = asyncio.run(
            create_campaign_review_job(
                session,
                source,
                request.app.state.storage,
                request.app.state.settings,
                actor_id=workspace.principal.user_id,
                fingerprint=payload.final_review_sha256,
                approval_text=payload.approval_text,
                authorized_reserve_micros=payload.authorized_reserve_micros,
                quote_sha256=payload.quote_sha256,
                dispatch_route=_campaign_review_provider(request).describe_route(),
            )
        )
    except (
        DetailDeliveryError,
        DetailGenerationError,
        DetailPlanError,
        ValueError,
        OSError,
        KeyError,
    ) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_CAMPAIGN_INVALID"),
            str(exc),
        ) from exc
    add_outbox_event(
        session,
        workspace_id=workspace.workspace_id,
        job_id=review.id,
        event_type="job.dispatch_requested",
        suffix="initial",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=review.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, review)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/local-revision/quote",
    response_model=DomesticDetailLocalRevisionQuote,
    tags=["jobs"],
)
def quote_domestic_detail_local_revision(
    job_id: str,
    payload: DomesticDetailLocalRevisionQuoteRequest,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> DomesticDetailLocalRevisionQuote:
    from .domestic_detail.revision_authorization import build_revision_quote

    source = get_workspace_job(session, workspace.workspace_id, job_id)
    _ensure_detail_skill_provider_enabled(request, source)
    provider = request.app.state.task_runner.coordinator.provider
    delegate = getattr(
        getattr(provider, "domestic_detail_provider", None), "delegate", None
    )
    gateway = getattr(delegate, "gateway", None)
    if gateway is None:
        raise ApiError(
            503,
            "NATIVE_DETAIL_LOCAL_REVISION_UNAVAILABLE",
            "详情局部修订路由当前不可用。",
        )
    try:
        quote = asyncio.run(
            build_revision_quote(
                session,
                source,
                request.app.state.storage,
                request.app.state.settings,
                gateway,
                fingerprint=payload.final_review_sha256,
                adjustment=payload.adjustment.model_dump(),
                actor_id=workspace.principal.user_id,
            )
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_LOCAL_REVISION_QUOTE_INVALID"),
            str(exc),
        ) from exc
    return DomesticDetailLocalRevisionQuote.model_validate(quote)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/local-revision",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def create_domestic_detail_local_revision(
    job_id: str,
    payload: DomesticDetailLocalRevisionCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    from .domestic_detail.revision_jobs import create_revision_job

    key = require_idempotency_key(idempotency_header)
    source = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    _ensure_detail_skill_provider_enabled(request, source)
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/local-revision",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job_id, **payload.model_dump()}),
    )
    if replay:
        return job_detail_response(
            session,
            get_workspace_job(
                session, workspace.workspace_id, cast(str, idem.resource_id)
            ),
        )
    provider = request.app.state.task_runner.coordinator.provider
    delegate = getattr(
        getattr(provider, "domestic_detail_provider", None), "delegate", None
    )
    gateway = getattr(delegate, "gateway", None)
    if gateway is None:
        raise ApiError(
            503,
            "NATIVE_DETAIL_LOCAL_REVISION_UNAVAILABLE",
            "详情局部修订路由当前不可用。",
        )
    try:
        child = asyncio.run(
            create_revision_job(
                session,
                source,
                request.app.state.storage,
                request.app.state.settings,
                gateway,
                actor_id=workspace.principal.user_id,
                fingerprint=payload.final_review_sha256,
                adjustment=payload.adjustment.model_dump(),
                quote_sha256=payload.quote_sha256,
                approval_text=payload.approval_text,
            )
        )
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_LOCAL_REVISION_INVALID"),
            str(exc),
        ) from exc
    add_outbox_event(
        session,
        workspace_id=workspace.workspace_id,
        job_id=child.id,
        event_type="job.dispatch_requested",
        suffix="local-revision",
    )
    complete_idempotency(
        idem, resource_type="job", resource_id=child.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, child)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/automatic-delivery/recover",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
def recover_automatic_detail_delivery(
    job_id: str,
    payload: DomesticDetailAutomaticDeliveryRecoveryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:

    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    try:
        if (
            job.type != JobType.DETAIL_PACK.value
            or job.configuration.get("domestic_detail_pipeline") != PIPELINE_VERSION
            or plan is None
        ):
            raise ValueError(
                "This is not an original automatic domestic detail delivery"
            )
        from .domestic_detail.delivery_authorization import job_receipt_authorization

        authorization = job_receipt_authorization(session, job, plan)
        if (
            authorization is None
            or authorization["sha256"] != payload.authorization_sha256
        ):
            raise ValueError("The original automatic-delivery authorization changed")
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError(
            409, "NATIVE_DETAIL_AUTO_DELIVERY_AUTHORIZATION_INVALID", str(exc)
        ) from exc
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/automatic-delivery/recover",
        idempotency_key=key,
        request_hash=canonical_hash({"job_id": job.id, **payload.model_dump()}),
    )
    if replay:
        return job_detail_response(session, job)
    if job.status not in {"awaiting_final_review", "completed"}:
        raise ApiError(
            409,
            "NATIVE_DETAIL_AUTO_DELIVERY_NOT_READY",
            "等待当前生成任务结束后，才能恢复原自动打包。",
        )
    if job.status == "awaiting_final_review":
        job.stage = "automatic_delivery_reconciliation_requested"
        add_outbox_event(
            session,
            workspace_id=job.workspace_id,
            job_id=job.id,
            event_type="job.dispatch_requested",
            suffix=f"automatic-delivery-recovery-{idem.id}",
        )
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=202
    )
    session.commit()
    _schedule_outbox(request, background_tasks)
    return job_detail_response(session, job)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/detail-final-review",
    response_model=DomesticDetailFinalReviewResponse,
    response_model_exclude_none=True,
    tags=["jobs"],
)
def prepare_domestic_detail_final_review(
    job_id: str,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    payload: DomesticCampaignReviewSelection | None = None,
) -> DomesticDetailFinalReviewResponse:
    from .domestic_detail.delivery import DetailDeliveryError
    from .domestic_detail.review_packet import prepare_final_review

    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    if (
        job.type != JobType.DETAIL_PACK.value
        or job.configuration.get("domestic_detail_pipeline") != PIPELINE_VERSION
        or job.status not in {"awaiting_final_review", "completed"}
    ):
        raise ApiError(
            409,
            "NATIVE_DETAIL_FINAL_REVIEW_NOT_READY",
            "当前任务尚不能进行国内详情最终审阅。",
        )
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if plan is None:
        raise ApiError(409, "NATIVE_DETAIL_DELIVERY_CONTEXT_MISSING", "原始方案缺失。")
    try:
        packet = asyncio.run(
            prepare_final_review(
                session,
                job,
                plan,
                request.app.state.storage,
                payload.model_dump() if payload is not None else None,
            )
        )
    except (DetailDeliveryError, ValueError, OSError, KeyError) as exc:
        raise ApiError(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "NATIVE_DETAIL_DELIVERY_INVALID"),
            str(exc),
        ) from exc
    session.commit()
    return DomesticDetailFinalReviewResponse.model_validate(packet)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/finalize",
    response_model=JobDetailResponse,
    tags=["jobs"],
)
def finalize_domestic_main_image(
    job_id: str,
    payload: FinalizeJobRequest,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobDetailResponse:
    """Record the user's complete-set approval through the native v2 Harness."""

    key = require_idempotency_key(idempotency_header)
    job = get_workspace_job(session, workspace.workspace_id, job_id, for_update=True)
    detail_settings = (
        job.configuration.get("settings")
        if isinstance(job.configuration, Mapping)
        else None
    )
    if (
        job.type == JobType.DETAIL_PACK.value
        and isinstance(detail_settings, Mapping)
        and detail_settings.get("market_region") == "domestic"
        and not detail_fast_runtime.is_independent(job.configuration)
    ):
        from .domestic_detail.delivery import (
            DetailDeliveryError,
            finalize_native_delivery,
        )

        idem, replay = claim_idempotency(
            session,
            workspace_id=workspace.workspace_id,
            actor_user_id=workspace.principal.user_id,
            method="POST",
            route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/finalize",
            idempotency_key=key,
            request_hash=canonical_hash(
                {
                    "job_id": job.id,
                    "approval_text": payload.approval_text,
                    "final_review_sha256": payload.final_review_sha256,
                }
            ),
        )
        if not replay and JobStatus(job.status) != JobStatus.AWAITING_FINAL_REVIEW:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "JOB_NOT_AWAITING_FINAL_REVIEW",
                "国内详情图尚未进入最终审核阶段。",
            )
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == job.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        if plan is None:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "NATIVE_DETAIL_DELIVERY_CONTEXT_MISSING",
                "原始详情方案缺失，无法确认交付。",
            )
        try:
            final_manifest = asyncio.run(
                finalize_native_delivery(
                    session,
                    job,
                    plan,
                    request.app.state.storage,
                    actor_id=workspace.principal.user_id,
                    approval_text=payload.approval_text,
                    final_review_sha256=payload.final_review_sha256,
                    allow_create=not replay,
                )
            )
        except DetailDeliveryError as exc:
            raise ApiError(exc.status_code, exc.code, str(exc)) from exc
        if replay:
            return job_detail_response(session, job)
        plan_payload = deepcopy(plan.payload)
        aurora = dict(plan_payload.get("aurora_v4_3") or {})
        aurora["delivery"] = final_manifest
        plan_payload["aurora_v4_3"] = aurora
        plan_payload["final_manifest"] = final_manifest
        plan.payload = plan_payload
        plan.payload_hash = canonical_hash(plan_payload)
        complete_idempotency(
            idem, resource_type="job", resource_id=job.id, response_status=200
        )
        transition_job(job, JobStatus.COMPLETED, stage="completed", progress=100)
        session.commit()
        return job_detail_response(session, job)

    if (
        job.type == JobType.DETAIL_PACK.value
        and isinstance(detail_settings, Mapping)
        and detail_settings.get("market_region") == "international"
    ):
        if JobStatus(job.status) != JobStatus.AWAITING_FINAL_REVIEW:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "JOB_NOT_AWAITING_FINAL_REVIEW",
                "跨境详情图尚未进入人工 QA 审核阶段。",
            )
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == job.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        links = session.scalars(
            select(JobAssetRecord)
            .where(
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.direction == JobAssetDirection.OUTPUT.value,
                JobAssetRecord.is_deliverable.is_(True),
            )
            .order_by(JobAssetRecord.ordinal)
        ).all()
        if plan is None or len(links) != job.expected_item_count:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "CROSSBORDER_DETAIL_QA_INCOMPLETE",
                "跨境详情图产物尚未完整，不能确认人工 QA。",
            )
        if (
            not isinstance(payload.approval_text, str)
            or len(payload.approval_text.strip()) < 2
        ):
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "CROSSBORDER_DETAIL_QA_APPROVAL_REQUIRED",
                "请明确确认已完成跨境详情图 QA 审阅。",
            )
        idem, replay = claim_idempotency(
            session,
            workspace_id=workspace.workspace_id,
            actor_user_id=workspace.principal.user_id,
            method="POST",
            route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/finalize",
            idempotency_key=key,
            request_hash=canonical_hash(
                {"job_id": job.id, "approval_text": payload.approval_text}
            ),
        )
        if replay:
            return job_detail_response(session, job)
        plan_payload = dict(plan.payload)
        qa_review = {
            "status": "approved",
            "mode": "disclosure_and_selection",
            "manual_review_required": False,
            "approval_text": payload.approval_text.strip(),
            "reviewed_asset_ids": [link.asset_id for link in links],
        }
        final_manifest = {
            "skill_id": "xinghe-crossborder-detail-3",
            "platform_code": detail_settings.get("platform_code"),
            "page_asset_refs": [link.asset_id for link in links],
            "delivery": "USER_MANUAL_QA_APPROVAL",
            "qa_review": qa_review,
        }
        plan_payload["crossborder_quality_review"] = qa_review
        plan_payload["crossborder_final_manifest"] = final_manifest
        plan.payload = plan_payload
        plan.payload_hash = canonical_hash(plan_payload)
        complete_idempotency(
            idem, resource_type="job", resource_id=job.id, response_status=200
        )
        transition_job(job, JobStatus.COMPLETED, stage="completed", progress=100)
        session.commit()
        return job_detail_response(session, job)
    if job.type != JobType.DOMESTIC_MAIN_IMAGE.value:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "AURORA_FINALIZE_UNSUPPORTED",
            "只有国内主图六图任务支持 Aurora 最终批准。",
        )
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    policy = plan.payload.get("execution_policy") if plan else None
    mode = policy.get("execution_mode") if isinstance(policy, Mapping) else None
    if plan is None or not is_v20_aurora_mode(mode):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "AURORA_FINALIZE_REQUIRES_V20",
            "只有 Aurora v2.0 执行链支持最终批准。",
        )

    from .aurora_main_image_runtime import (
        AuroraHarnessError,
        AuroraV20HarnessRunner,
        write_contract,
    )

    set_session = plan.payload.get(
        "_parallel_v20_set_session"
        if is_parallel_aurora_mode(mode)
        else "_sequential_v20_set_session"
    )
    if not isinstance(set_session, Mapping):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "AURORA_SET_AUDIT_MISSING",
            "完整六图审计尚未完成，不能最终批准。",
        )
    idem, replay = claim_idempotency(
        session,
        workspace_id=workspace.workspace_id,
        actor_user_id=workspace.principal.user_id,
        method="POST",
        route_template="/api/v1/workspaces/{workspace_id}/jobs/{job_id}/finalize",
        idempotency_key=key,
        request_hash=canonical_hash(
            {"job_id": job.id, "approval_text": payload.approval_text}
        ),
    )
    if replay:
        # Approval is durably recorded before cost settlement; a retry can
        # complete settlement if the first response was interrupted.
        coordinator = getattr(request.app.state.task_runner, "coordinator", None)
        if JobStatus(job.status) == JobStatus.AWAITING_FINAL_REVIEW and coordinator:
            session.commit()
            coordinator.finalize_job(job.id)
            session.expire_all()
        return job_detail_response(session, job)
    if JobStatus(job.status) != JobStatus.AWAITING_FINAL_REVIEW:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_AWAITING_FINAL_REVIEW",
            "任务当前状态不允许最终批准。",
        )
    coordinator = getattr(request.app.state.task_runner, "coordinator", None)
    if coordinator is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AURORA_FINALIZE_COORDINATOR_UNAVAILABLE",
            "任务执行协调器当前不可用，暂不能完成最终批准。",
        )
    workspace_root = (request.app.state.settings.aurora_work_root / job.id).resolve()
    set_dir = "parallel-v20" if is_parallel_aurora_mode(mode) else "sequential-v20"
    finalize_workspace = workspace_root / set_dir / "set-audit"
    finalize_workspace.mkdir(parents=True, exist_ok=True)
    session_path = finalize_workspace / "session.json"
    output_path = finalize_workspace / "final-manifest.json"
    write_contract(session_path, dict(set_session))
    try:
        AuroraV20HarnessRunner().run(
            "finalize",
            finalize_workspace,
            {
                "session": session_path,
                "output": output_path,
                "approval-text": payload.approval_text,
            },
        )
    except (AuroraHarnessError, OSError, ValueError) as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "AURORA_FINALIZE_FAILED",
            "Aurora 最终批准未能写入 Harness。",
            {"reason": getattr(exc, "code", type(exc).__name__)},
        ) from exc

    plan_payload = dict(plan.payload)
    finalized_session = json.loads(session_path.read_text(encoding="utf-8"))
    plan_payload["_harness_session"] = finalized_session
    plan_payload["final_manifest"] = str(output_path)
    plan_payload["harness_state"] = finalized_session.get("state")
    plan.payload = plan_payload
    plan.payload_hash = canonical_hash(plan_payload)
    complete_idempotency(
        idem, resource_type="job", resource_id=job.id, response_status=200
    )
    session.commit()
    coordinator.finalize_job(job.id)
    session.expire_all()
    finalized_job = get_workspace_job(session, workspace.workspace_id, job.id)
    return job_detail_response(session, finalized_job)


def _validate_model_pose_transfer_assets(
    payload: ModelPoseTransferJobCreate,
    assets: dict[str, AssetRecord],
) -> None:
    model_source = assets[payload.input.model_source_asset_id]
    pose_reference = assets[payload.input.pose_reference_asset_id]
    for asset in (model_source, pose_reference):
        if (
            asset.media_kind != AssetMediaKind.IMAGE.value
            or not asset.content_type.lower().startswith("image/")
        ):
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "MODEL_POSE_IMAGE_REQUIRED",
                "Model pose transfer requires two READY image assets.",
            )

    if payload.input.pose_slug is None:
        return
    try:
        catalog_pose = resolve_model_pose_catalog_item(
            payload.input.pose_slug,
            cast(str, payload.input.pose_reference_sha256),
        )
        catalog_pose.read_original()
    except ModelPoseCatalogError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc)
        ) from exc
    if (
        pose_reference.sha256 != catalog_pose.reference_sha256
        or pose_reference.content_type != catalog_pose.mime_type
        or pose_reference.width != catalog_pose.width
        or pose_reference.height != catalog_pose.height
        or pose_reference.byte_size != catalog_pose.byte_size
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "MODEL_POSE_REFERENCE_ASSET_MISMATCH",
            "The pose reference asset is not the selected catalog original.",
        )


def _new_job_from_payload(
    workspace_id: str,
    payload: (
        LocalEditJobCreate
        | ProductImageJobCreate
        | ReferenceRemixJobCreate
        | ModelPoseTransferJobCreate
        | CreativeImageJobCreate
        | DetailPackJobCreate
        | AmazonVisualSuiteJobCreate
        | DomesticMainImageJobCreate
    ),
    *,
    detail_medium_price_micros: int = 45_000,
    detail_high_price_micros: int = 90_000,
) -> tuple[JobRecord, list[tuple[str, str, int]]]:
    if isinstance(payload, LocalEditJobCreate):
        item_count = 1
        asset_roles = [
            (payload.input.source_asset_id, "source", 0),
            (payload.input.selection_mask_asset_id, "selection_mask", 0),
        ]
    elif isinstance(payload, ModelPoseTransferJobCreate):
        item_count = 1
        asset_roles = [
            (payload.input.model_source_asset_id, "model_source", 0),
            (payload.input.pose_reference_asset_id, "pose_reference", 0),
        ]
    elif isinstance(payload, (ProductImageJobCreate, ReferenceRemixJobCreate)):
        item_count = payload.configuration.image_count
        asset_roles = [
            *[
                (asset_id, "product_reference", ordinal)
                for ordinal, asset_id in enumerate(payload.input.product_asset_ids)
            ],
            *[
                (asset_id, "style_reference", ordinal)
                for ordinal, asset_id in enumerate(payload.input.reference_asset_ids)
            ],
        ]
    elif isinstance(payload, CreativeImageJobCreate):
        item_count = payload.configuration.image_count
        asset_roles = [
            *[
                (asset_id, "product_reference", ordinal)
                for ordinal, asset_id in enumerate(payload.input.product_asset_ids)
            ],
            *[
                (asset_id, "competitor_reference", ordinal)
                for ordinal, asset_id in enumerate(payload.input.competitor_asset_ids)
            ],
        ]
    elif isinstance(payload, DetailPackJobCreate):
        item_count = payload.configuration.page_count * len(
            payload.input.product_groups
        )
        asset_roles = [
            *[
                (asset_id, "product_reference", ordinal)
                for group in payload.input.product_groups
                for ordinal, asset_id in enumerate(group.asset_ids)
            ],
            *[
                (asset_id, "style_reference", ordinal)
                for ordinal, asset_id in enumerate(payload.input.reference_asset_ids)
            ],
        ]
    elif isinstance(payload, DomesticMainImageJobCreate):
        item_count = 6
        asset_roles = [
            (asset_id, "product_identity_authority", ordinal)
            for ordinal, asset_id in enumerate(payload.input.product_asset_ids)
        ]
    else:
        item_count = 21
        asset_roles = []
        for role, asset_ids in (
            (
                "white_background_product",
                payload.input.white_background_product_asset_ids,
            ),
            ("product_detail", payload.input.detail_asset_ids),
            ("package_reference", payload.input.package_asset_ids),
            ("scene_reference", payload.input.scene_asset_ids),
            ("brand_reference", payload.input.brand_asset_ids),
            ("style_reference", payload.input.reference_asset_ids),
            (
                "competitor_reference",
                payload.input.competitor_reference_asset_ids,
            ),
        ):
            asset_roles.extend(
                (asset_id, role, ordinal) for ordinal, asset_id in enumerate(asset_ids)
            )
    if isinstance(payload, DetailPackJobCreate):
        minimum = item_count * detail_high_price_micros
        maximum = item_count * detail_high_price_micros
    else:
        minimum = item_count * min(detail_medium_price_micros, detail_high_price_micros)
        maximum = item_count * max(detail_medium_price_micros, detail_high_price_micros)
    _, _, credits = estimate_job(
        JobType(payload.type),
        item_count,
        {"settings": payload.configuration.model_dump(mode="json")},
        workspace_id=workspace_id,
    )
    is_submit = payload.intent == "submit"
    job = JobRecord(
        id=new_id("job"),
        workspace_id=workspace_id,
        type=payload.type,
        status=(JobStatus.QUEUED.value if is_submit else JobStatus.DRAFT.value),
        stage=("queued" if is_submit else "draft"),
        display_name=payload.display_name,
        progress=0,
        configuration={
            "input": payload.input.model_dump(mode="json"),
            "settings": payload.configuration.model_dump(mode="json"),
            **(
                {
                    "domestic_detail_pipeline": (
                        detail_fast.PIPELINE_VERSION
                        if payload.configuration.detail_speed == "fast"
                        else refined_detail.PIPELINE_VERSION
                        if payload.configuration.detail_skill == "refined"
                        else PIPELINE_VERSION
                    )
                }
                if isinstance(payload, DetailPackJobCreate)
                and payload.configuration.market_region == "domestic"
                else {}
            ),
        },
        expected_item_count=item_count,
        estimated_cost_min_micros=minimum,
        estimated_cost_max_micros=maximum,
        estimated_credits=credits,
        currency="USD",
        queued_at=utc_now() if is_submit else None,
        history_expires_at=None if is_submit else utc_now() + timedelta(days=30),
    )
    return job, asset_roles


_REAL_PROVIDER_ENABLE_FLAGS = {
    JobType.PRODUCT_IMAGE.value: "product_image_provider_enabled",
    JobType.REFERENCE_REMIX.value: "reference_remix_provider_enabled",
    JobType.MODEL_POSE_TRANSFER.value: "model_pose_transfer_provider_enabled",
    JobType.CREATIVE_IMAGE.value: "creative_image_provider_enabled",
    JobType.AMAZON_VISUAL_SUITE.value: "amazon_visual_suite_provider_enabled",
    JobType.DOMESTIC_MAIN_IMAGE.value: "domestic_main_image_provider_enabled",
}


_AMAZON_INITIAL_IMAGE_REQUESTS = AMAZON_EXPECTED_ITEM_COUNT
_AMAZON_MAX_REDRAW_REQUESTS = AMAZON_EXPECTED_ITEM_COUNT
_AMAZON_PLANNER_REQUESTS = 1
_AMAZON_MAX_SUITE_REVIEW_REQUESTS = 8
_AMAZON_PROVIDER_REQUEST_ENVELOPE = (
    _AMAZON_INITIAL_IMAGE_REQUESTS
    + _AMAZON_MAX_REDRAW_REQUESTS
    + _AMAZON_PLANNER_REQUESTS
    + _AMAZON_MAX_SUITE_REVIEW_REQUESTS
)
_AURORA_INITIAL_IMAGE_REQUESTS = 6
_AURORA_MAX_REPAIR_IMAGE_REQUESTS = 6
_AURORA_IDENTITY_PLANNER_REQUESTS = 1
_AURORA_MAX_PAGE_AUDIT_REQUESTS = 12
_AURORA_STYLE_AND_SET_AUDIT_REQUESTS = 2
_AURORA_PLANNER_REQUEST_ENVELOPE = (
    _AURORA_IDENTITY_PLANNER_REQUESTS
    + _AURORA_MAX_PAGE_AUDIT_REQUESTS
    + _AURORA_STYLE_AND_SET_AUDIT_REQUESTS
)
_AURORA_PROVIDER_REQUEST_ENVELOPE = (
    _AURORA_INITIAL_IMAGE_REQUESTS
    + _AURORA_MAX_REPAIR_IMAGE_REQUESTS
    + _AURORA_PLANNER_REQUEST_ENVELOPE
)


def _ensure_detail_skill_provider_enabled(request: Request, job: JobRecord) -> None:
    """Prevent international detail jobs from falling back to the legacy Skill."""

    settings = request.app.state.settings
    if settings.task_runner != "tengli" or job.type != JobType.DETAIL_PACK.value:
        return
    configuration = job.configuration if isinstance(job.configuration, dict) else {}
    detail_settings = configuration.get("settings")
    market_region = (
        str(detail_settings.get("market_region", "international"))
        .strip()
        .lower()
        .replace("-", "_")
        if isinstance(detail_settings, Mapping)
        else "international"
    )
    if market_region == "domestic" or bool(
        getattr(settings, "crossborder_detail_provider_enabled", False)
    ):
        return
    raise ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "REAL_PROVIDER_NOT_REGISTERED",
        "The cross-border detail Skill Provider is not enabled.",
        {
            "job_type": job.type,
            "capability": "crossborder_detail",
            "provider_called": False,
        },
    )


def _reserve_real_provider_job_if_enabled(
    session: Session,
    request: Request,
    job: JobRecord,
    actor_user_id: str,
    *,
    runnable_item_count: int | None = None,
) -> None:
    settings = request.app.state.settings
    if settings.task_runner != "tengli":
        return
    enable_flag = _REAL_PROVIDER_ENABLE_FLAGS.get(job.type)
    if enable_flag is not None and not bool(getattr(settings, enable_flag, False)):
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "REAL_PROVIDER_NOT_REGISTERED",
            "The real Provider for this job type is not enabled.",
            {"job_type": job.type, "provider_called": False},
        )
    configuration = job.configuration if isinstance(job.configuration, dict) else {}
    _ensure_detail_skill_provider_enabled(request, job)
    input_configuration = configuration.get("input")
    group_count = 0
    if job.type == JobType.DETAIL_PACK.value and isinstance(input_configuration, dict):
        groups = input_configuration.get("product_groups")
        if isinstance(groups, list):
            group_count = len(groups)
    image_cost = int(job.estimated_cost_max_micros or 0)
    if job.type == JobType.AMAZON_VISUAL_SUITE.value:
        job_id = getattr(job, "id", None)
        reusable_plan = (
            session.scalar(
                select(JobPlanRecord.id)
                .where(
                    JobPlanRecord.job_id == job_id,
                    JobPlanRecord.validation_status == PlanValidationStatus.VALID.value,
                    JobPlanRecord.confirmed_at.is_not(None),
                )
                .limit(1)
            )
            if isinstance(job_id, str) and hasattr(session, "scalar")
            else None
        )
        planner_required = reusable_plan is None
        # Reserve the complete fail-closed Amazon automation envelope: 21 initial
        # images, up to 21 automatic redraws, one planner call, and up to two
        # whole-suite reviews. Reused plans release the unused planner allowance
        # during settlement rather than weakening the production reservation.
        estimated_cost = image_cost * 2 + settings.tengli_planner_reserve_micros * (
            _AMAZON_PLANNER_REQUESTS + _AMAZON_MAX_SUITE_REVIEW_REQUESTS
        )
        provider_requests = _AMAZON_PROVIDER_REQUEST_ENVELOPE
        group_count = int(planner_required)
    elif job.type == JobType.DOMESTIC_MAIN_IMAGE.value:
        # Reserve the complete Skill envelope before the identity-planner call:
        # six initial generations, one repair per page, identity inspection,
        # one audit per attempt, first-pair style calibration and final set audit.
        transient_retry_count = (
            settings.aurora_max_transient_retries
            if is_parallel_aurora_mode(settings.aurora_execution_mode)
            else 0
        )
        estimated_cost = (
            image_cost * (2 + transient_retry_count)
            + settings.tengli_planner_reserve_micros * _AURORA_PLANNER_REQUEST_ENVELOPE
        )
        provider_requests = (
            _AURORA_PROVIDER_REQUEST_ENVELOPE
            + _AURORA_INITIAL_IMAGE_REQUESTS * transient_retry_count
        )
        group_count = _AURORA_PLANNER_REQUEST_ENVELOPE
    elif (
        job.type == JobType.REFERENCE_REMIX.value
        and (job.configuration.get("settings") or {}).get("reference_skill")
        == "aurora-remix-fast"
        and settings.task_runner == "tengli"
    ):
        fast_count = (
            runnable_item_count
            if runnable_item_count is not None
            else job.expected_item_count
        )
        estimated_cost = (
            image_cost * 2 + settings.tengli_planner_reserve_micros * 4 * fast_count
        )
        provider_requests = 6 * fast_count
        group_count = 2 * fast_count
    elif job.type == JobType.REFERENCE_REMIX.value and settings.task_runner == "tengli":
        # Reference remix now performs one bounded vision-analysis call before
        # the image edit so the Skill's composition skeleton reaches the image
        # model. Reserve both calls before dispatching the job.
        estimated_cost = image_cost * 2 + settings.tengli_planner_reserve_micros * 2
        provider_requests = (
            runnable_item_count
            if runnable_item_count is not None
            else job.expected_item_count
        ) * 2 + 2
        group_count = 1
    elif enable_flag is not None:
        estimated_cost = image_cost * 2
        provider_requests = (
            runnable_item_count
            if runnable_item_count is not None
            else job.expected_item_count
        ) * 2
        if job.type == JobType.CREATIVE_IMAGE.value:
            estimated_cost += settings.tengli_planner_reserve_micros * 2
            provider_requests += 2
    elif job.type == JobType.DETAIL_PACK.value:
        detail_settings = configuration.get("settings") or {}
        if detail_fast_runtime.is_independent(configuration):
            planner_groups = group_count if runnable_item_count is None else 0
            estimated_cost = (
                image_cost * 2 + planner_groups * settings.tengli_planner_reserve_micros
            )
            # Reserve the existing gateway's transport fallback bound, without
            # adding quality-review or redraw requests.
            provider_requests = (
                job.expected_item_count
                if runnable_item_count is None
                else runnable_item_count
            ) * 2 + planner_groups
        elif (
            isinstance(detail_settings, Mapping)
            and detail_settings.get("market_region") == "domestic"
        ):
            # Initial campaign + one root repair + one completion per module +
            # one Harness preflight repair; each pair has at most two images
            # and two actual-pixel reviews, each with at most one targeted
            # same-image identity-envelope review. Reserve the full bound.
            page_count = int(detail_settings.get("page_count") or 8)
            authoring_requests = group_count * (page_count + 5)
            review_requests = job.expected_item_count * 2
            planner_requests = authoring_requests + review_requests
            estimated_cost = (
                image_cost * 2
                + planner_requests * settings.tengli_planner_reserve_micros
            )
            provider_requests = planner_requests + job.expected_item_count
        else:
            estimated_cost = (
                image_cost * 2 + group_count * settings.tengli_planner_reserve_micros
            )
            provider_requests = job.expected_item_count * 2 + group_count
    else:
        estimated_cost = image_cost * 2 + settings.tengli_planner_reserve_micros * 4
        # Two edit calls plus up to four reviews, including same-candidate
        # recovery. Review recovery never regenerates the saved image.
        provider_requests = 6
    reserve_provider_job_quota(
        session,
        job=job,
        actor_user_id=actor_user_id,
        estimated_cost_micros=estimated_cost,
        provider_request_count=provider_requests,
        daily_limit_micros=int(settings.tengli_daily_budget_usd * 1_000_000),
        monthly_limit_micros=int(settings.tengli_monthly_budget_usd * 1_000_000),
        daily_request_limit=settings.tengli_daily_request_limit,
        planner_reserve_micros=settings.tengli_planner_reserve_micros,
        image_budget_micros=image_cost,
        group_count=group_count,
    )


_AMAZON_ITEM_KEYS = (
    "MAIN-01",
    *(f"SECONDARY-{index:02d}" for index in range(1, 7)),
    *(f"A-DESKTOP-{index:02d}" for index in range(1, 8)),
    *(f"A-MOBILE-{index:02d}" for index in range(1, 8)),
)


def _job_item_key(job_type: str, ordinal: int) -> str:
    if job_type == JobType.LOCAL_EDIT.value:
        return "result"
    if job_type == JobType.AMAZON_VISUAL_SUITE.value:
        return _AMAZON_ITEM_KEYS[ordinal]
    if job_type in {
        JobType.PRODUCT_IMAGE.value,
        JobType.REFERENCE_REMIX.value,
        JobType.MODEL_POSE_TRANSFER.value,
        JobType.CREATIVE_IMAGE.value,
    }:
        return f"image-{ordinal + 1:03d}"
    return f"page-{ordinal + 1:03d}"


def _job_item_configuration(
    job_type: str,
    ordinal: int,
    *,
    inspiration_workflow: bool = False,
) -> dict[str, object]:
    # Every independent single-image provider executes one durable JobItem per
    # requested output. Preserve that identity in the item envelope so
    # image_count=N never makes every adapter request look like image-001. The
    # inspiration_workflow flag remains part of the signature for compatibility
    # with callers that use it to select the page-level contract.
    if job_type in {
        JobType.PRODUCT_IMAGE.value,
        JobType.REFERENCE_REMIX.value,
        JobType.MODEL_POSE_TRANSFER.value,
        JobType.CREATIVE_IMAGE.value,
    }:
        return {
            "item_key": _job_item_key(job_type, ordinal),
            "ordinal": ordinal,
        }
    if job_type != JobType.AMAZON_VISUAL_SUITE.value:
        return {}

    from .amazon_visual_suite_provider import AMAZON_ITEM_SPECS

    try:
        spec = AMAZON_ITEM_SPECS[ordinal]
    except IndexError as exc:
        raise ValueError("Amazon visual suite item ordinal is out of range.") from exc
    return {
        "item_key": spec.item_key,
        "group": spec.group,
        "group_ordinal": spec.group_ordinal,
        "target_width": spec.width,
        "target_height": spec.height,
        "target_size": {"width": spec.width, "height": spec.height},
        "contact_sheet": False,
    }


def _add_items_and_inputs(
    session: Session,
    job: JobRecord,
    asset_roles: list[tuple[str, str, int]],
) -> None:
    settings = job.configuration.get("settings", {})
    inspiration_workflow = (
        job.type == JobType.REFERENCE_REMIX.value
        and settings.get("reference_skill") == "reference-visual-reverse-remix"
    ) or (
        job.type == JobType.CREATIVE_IMAGE.value
        and settings.get("inspiration_planning") is True
    )
    for ordinal in range(job.expected_item_count):
        session.add(
            JobItemRecord(
                id=new_id("item"),
                workspace_id=job.workspace_id,
                job_id=job.id,
                item_key=_job_item_key(job.type, ordinal),
                ordinal=ordinal,
                status=JobItemStatus.PENDING.value,
                configuration=_job_item_configuration(
                    job.type,
                    ordinal,
                    inspiration_workflow=inspiration_workflow,
                ),
            )
        )
    for asset_id, role, ordinal in asset_roles:
        session.add(
            JobAssetRecord(
                workspace_id=job.workspace_id,
                job_id=job.id,
                asset_id=asset_id,
                direction=JobAssetDirection.INPUT.value,
                role=role,
                ordinal=ordinal,
                is_deliverable=False,
                validation={},
            )
        )


def _validate_domestic_detail_retry(
    session: Session,
    source: JobRecord,
    selected: list[str],
    *,
    clone: bool = False,
) -> None:
    if (
        source.type == JobType.DETAIL_PACK.value
        and source.configuration.get("local_revision") is not None
    ):
        raise ApiError(
            409,
            "NATIVE_DETAIL_REVISION_RECONCILIATION_REQUIRED",
            "请先核对原修订请求；新的局部修订须审阅当前结果并单独授权。",
        )
    settings = source.configuration.get("settings") or {}
    if (
        source.type != JobType.DETAIL_PACK.value
        or settings.get("market_region") != "domestic"
        or detail_fast_runtime.is_independent(source.configuration)
    ):
        return
    from .aurora_detail_runtime import detail_workspace

    try:
        require_current_pipeline(source.configuration)
        plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == source.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        items = session.scalars(
            select(JobItemRecord).where(
                JobItemRecord.job_id == source.id, JobItemRecord.id.in_(selected)
            )
        ).all()
        for item in items:
            uncertain = (
                item.error_code == "PROVIDER_OUTCOME_UNKNOWN"
                or session.scalar(
                    select(JobAttemptRecord.id)
                    .where(
                        JobAttemptRecord.job_item_id == item.id,
                        JobAttemptRecord.status == "outcome_unknown",
                    )
                    .limit(1)
                )
                is not None
            )
            if not uncertain:
                continue
            if clone or plan is None:
                raise DetailGenerationError(
                    "NATIVE_DETAIL_RECONCILIATION_REQUIRED",
                    "Uncertain calls cannot be cloned into new paid requests",
                )
            binding = generation_binding(
                plan.payload,
                settings,
                item.configuration["source_assets"],
                group_key=str(item.configuration["group_key"]),
                job_input=source.configuration.get("input") or {},
            )
            workspace = detail_workspace(source.id)
            checkpoint = json.loads(
                (workspace / "session.json").read_text(encoding="utf-8")
            )
            origin = detail_workspace(
                str(checkpoint.get("checkpoint_source_job_id") or source.id)
            )
            page = int(item.configuration["page"])
            page -= 1 if int(item.configuration.get("pair_position") or 1) == 2 else 0
            require_recoverable_pair(
                list(dict.fromkeys((workspace, origin))),
                binding,
                page=page,
                pair_id=str(item.configuration["pair_id"]),
            )
    except (
        DetailGenerationError,
        DetailPlanError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "NATIVE_DETAIL_RECONCILIATION_REQUIRED",
            "原请求结果尚未核对，不能再次付费生成；请先核对原结果。",
            {"reason": str(exc)},
        ) from exc


def _copy_job_for_retry(
    session: Session,
    source: JobRecord,
    scope: RetryScope,
    selected: list[str],
) -> JobRecord:
    minimum, maximum, credits = estimate_job(
        JobType(source.type),
        len(selected),
        source.configuration,
        workspace_id=source.workspace_id,
    )
    if source.type == JobType.LOCAL_EDIT.value:
        minimum = source.estimated_cost_min_micros or minimum
        maximum = source.estimated_cost_max_micros or maximum
    elif source.type == JobType.DETAIL_PACK.value:
        source_plan = session.scalar(
            select(JobPlanRecord)
            .where(JobPlanRecord.job_id == source.id)
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        cost = (
            source_plan.payload.get("cost")
            if source_plan is not None and isinstance(source_plan.payload, dict)
            else None
        )
        if isinstance(cost, dict):
            medium_price = cost.get("medium_unit_micros")
            high_price = cost.get("high_unit_micros")
            if (
                isinstance(medium_price, int)
                and not isinstance(medium_price, bool)
                and medium_price >= 0
                and isinstance(high_price, int)
                and not isinstance(high_price, bool)
                and high_price >= 0
            ):
                minimum = len(selected) * high_price
                maximum = len(selected) * high_price
    return JobRecord(
        id=new_id("job"),
        workspace_id=source.workspace_id,
        type=source.type,
        status=JobStatus.QUEUED.value,
        stage="queued",
        display_name=source.display_name,
        configuration=source.configuration,
        configuration_schema=source.configuration_schema,
        prompt_version=source.prompt_version,
        strategy_version=source.strategy_version,
        release_version=source.release_version,
        root_job_id=source.root_job_id or source.id,
        retry_of_job_id=source.id,
        retry_scope=scope.value,
        expected_item_count=source.expected_item_count,
        estimated_cost_min_micros=minimum,
        estimated_cost_max_micros=maximum,
        estimated_credits=credits,
        currency=source.currency or "USD",
        queued_at=utc_now(),
    )


def _copy_job_relations_for_retry(
    session: Session,
    source: JobRecord,
    target: JobRecord,
    selected: list[str],
) -> None:
    source_items = session.scalars(
        select(JobItemRecord)
        .where(JobItemRecord.job_id == source.id)
        .order_by(JobItemRecord.ordinal)
    ).all()
    regenerated_pairs = {
        item.configuration.get("pair_id")
        for item in source_items
        if item.id in selected
        and isinstance(item.configuration, Mapping)
        and str(item.configuration.get("pair_position")) == "1"
    }
    item_map: dict[str, str] = {}
    preserved_set_audit_ids = {
        item_id
        for item_id in session.scalars(
            select(JobAssetRecord.job_item_id).where(
                JobAssetRecord.job_id == source.id,
                JobAssetRecord.is_deliverable.is_(True),
                JobAssetRecord.job_item_id.is_not(None),
            )
        ).all()
    }
    for item in source_items:
        new_item_id = new_id("item")
        item_map[item.id] = new_item_id
        retry_this = item.id in selected
        reused = not retry_this and item.status in {
            JobItemStatus.SUCCEEDED.value,
            JobItemStatus.REUSED.value,
        }
        reused = reused or (
            item.error_code == "AURORA_SET_AUDIT_FAILED"
            and item.id in preserved_set_audit_ids
        )
        copied_configuration = dict(item.configuration or {})
        if (
            source.type == JobType.DETAIL_PACK.value
            and (source.configuration.get("settings") or {}).get("market_region")
            == "domestic"
            and retry_this
            and (
                item.error_code == "PROVIDER_OUTCOME_UNKNOWN"
                or session.scalar(
                    select(JobAttemptRecord.id)
                    .where(
                        JobAttemptRecord.job_item_id == item.id,
                        JobAttemptRecord.status == "outcome_unknown",
                    )
                    .limit(1)
                )
                is not None
            )
        ):
            copied_configuration["recovery_only"] = True
        # A parallel v2 retry may reuse page 0 while retrying page 1.  The
        # native Campaign Style gate still needs the accepted page-0 pixels;
        # retain the source job id so the provider can read that durable
        # session/artifact without regenerating the reused page.
        if source.type == JobType.DOMESTIC_MAIN_IMAGE.value:
            copied_configuration["aurora_parallel_source_job_id"] = source.id
        elif (
            source.type == JobType.DETAIL_PACK.value
            and isinstance(source.configuration, Mapping)
            and isinstance(source.configuration.get("settings"), Mapping)
            and source.configuration["settings"].get("market_region") == "domestic"
            and copied_configuration.get("pair_id")
        ):
            # A retry may reuse the first slice of an already successful pair.
            # Let the second slice rehydrate that exact source-job canvas before
            # generating any new pair canvas in the retry job.
            if copied_configuration.get("pair_id") in regenerated_pairs:
                # Both slices belong to the new job's freshly generated canvas.
                copied_configuration.pop("aurora_pair_source_job_id", None)
            else:
                copied_configuration["aurora_pair_source_job_id"] = source.id
        session.add(
            JobItemRecord(
                id=new_item_id,
                workspace_id=target.workspace_id,
                job_id=target.id,
                item_key=item.item_key,
                ordinal=item.ordinal,
                status=(
                    JobItemStatus.PENDING.value
                    if retry_this
                    else (
                        JobItemStatus.REUSED.value
                        if reused
                        else JobItemStatus.SKIPPED.value
                    )
                ),
                stage="retry" if retry_this else "reused",
                quality=item.quality,
                configuration=copied_configuration,
                source_job_item_id=item.id,
                result_source="reused" if reused else None,
            )
        )
        if reused:
            target.succeeded_item_count += 1
    relations = session.scalars(
        select(JobAssetRecord).where(JobAssetRecord.job_id == source.id)
    ).all()
    for relation in relations:
        if relation.direction == JobAssetDirection.INPUT.value or (
            relation.job_item_id in item_map
            and relation.is_deliverable
            and relation.job_item_id not in selected
        ):
            session.add(
                JobAssetRecord(
                    workspace_id=target.workspace_id,
                    job_id=target.id,
                    job_item_id=(
                        item_map.get(relation.job_item_id)
                        if relation.job_item_id
                        else None
                    ),
                    asset_id=relation.asset_id,
                    direction=relation.direction,
                    role=relation.role,
                    ordinal=relation.ordinal,
                    is_deliverable=relation.is_deliverable,
                    validation=relation.validation,
                )
            )
    if source.type in {
        JobType.DETAIL_PACK.value,
        JobType.AMAZON_VISUAL_SUITE.value,
        JobType.DOMESTIC_MAIN_IMAGE.value,
    }:
        source_plan = session.scalar(
            select(JobPlanRecord)
            .where(
                JobPlanRecord.job_id == source.id,
                JobPlanRecord.validation_status == PlanValidationStatus.VALID.value,
            )
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
        )
        if source_plan is not None and source_plan.confirmed_at is not None:
            copied_payload = dict(source_plan.payload)
            copied_payload["state"] = "confirmed"
            # A transient provider failure can happen after the native v2
            # harness has issued ``generate-next`` but before an image is
            # bound.  The source session is then intentionally left in
            # ``AWAITING_GENERATED_ASSET``.  A user-requested retry has a new
            # provider idempotency scope, so clear only that unbound pending
            # call and return to the already authorized page cursor.  This
            # preserves accepted pages and the immutable plan while ensuring
            # the retry enters the Skill through a fresh ``generate-next``.
            provider_failed_source_items = [
                item
                for item in source_items
                if item.id in selected
                and item.id not in preserved_set_audit_ids
                and isinstance(item.error_details, dict)
                and item.error_details.get("provider_outcome") == "failed"
            ]
            copied_session = copied_payload.get("_harness_session")
            execution_policy = copied_payload.get("execution_policy")
            if (
                source.type == JobType.DOMESTIC_MAIN_IMAGE.value
                and is_v20_aurora_mode(
                    execution_policy.get("execution_mode")
                    if isinstance(execution_policy, Mapping)
                    else None
                )
                and provider_failed_source_items
                and isinstance(copied_session, Mapping)
                and copied_session.get("state") == "AWAITING_GENERATED_ASSET"
            ):
                from .aurora_main_image_runtime import (
                    reset_v20_pending_generation_for_retry,
                )

                retry_session = reset_v20_pending_generation_for_retry(copied_session)
                copied_payload["_harness_session"] = retry_session
                copied_payload["harness_state"] = "AUTHORIZED"
            cost_value = copied_payload.get("cost")
            cost = dict(cost_value) if isinstance(cost_value, dict) else {}
            pending_items = [item for item in source_items if item.id in selected]
            pending_high = sum(item.quality == "high" for item in pending_items)
            pending_medium = len(pending_items) - pending_high
            medium_price = int(cost.get("medium_unit_micros", 45_000))
            high_price = int(cost.get("high_unit_micros", 90_000))
            cost.update(
                {
                    "pending_medium_count": pending_medium,
                    "pending_high_count": pending_high,
                    "estimated_pending_micros": (
                        pending_medium * medium_price + pending_high * high_price
                    ),
                }
            )
            copied_payload["cost"] = cost
            copied_payload["usage"] = {
                "planner": [],
                "planner_reused_from_job_id": source.id,
                "image": {
                    "reported_requests": 0,
                    "unreported_requests": 0,
                },
            }
            copied_payload["metrics"] = {
                "planning_time": 0,
                "planning_reused": True,
                "image_wall_time": None,
                "average_api_latency": None,
                "effective_seconds_per_page": None,
                "throughput_pages_per_second": 0.0,
                "job_wall_time": None,
                "success_count": 0,
                "failure_count": 0,
                "rate_limit_429_count": 0,
                "actual_concurrency": 0,
                "usage_missing_count": 0,
            }
            if source.type == JobType.AMAZON_VISUAL_SUITE.value:
                copied_payload["business_quality"] = {
                    "status": "pending_generation",
                    "policy_version": source_plan.validator_version,
                    "technical_delivery_status": "pending",
                    "manual_review_required": True,
                    "automatic_redraw_enabled": False,
                    "deterministic_findings": [],
                    "reused_from_job_id": source.id,
                }
            session.add(
                JobPlanRecord(
                    id=new_id("plan"),
                    workspace_id=target.workspace_id,
                    job_id=target.id,
                    revision=1,
                    schema_version=source_plan.schema_version,
                    payload=copied_payload,
                    payload_hash=canonical_hash(copied_payload),
                    validation_status=PlanValidationStatus.VALID.value,
                    validation_errors=[],
                    validator_version=source_plan.validator_version,
                    validated_at=utc_now(),
                    confirmed_at=utc_now(),
                )
            )


def _ensure_parallel_aurora_retry_snapshots(
    session: Session,
    job: JobRecord,
    *,
    work_root: Path,
) -> None:
    """Rebuild missing v2 page snapshots on a retry/clone before dispatch.

    Older parallel jobs may have persisted only pages 0–1 before the style gate
    blocked the remaining rows.  A retry copies the confirmed plan, but those
    skipped rows have no immutable generation request to execute.  Materialize
    the six native v2 requests now, in the same way plan authorization does,
    while leaving successful deliverables reusable and untouched.
    """

    if job.type != JobType.DOMESTIC_MAIN_IMAGE.value:
        return
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if plan is None or not isinstance(plan.payload, dict):
        return
    policy = plan.payload.get("execution_policy")
    if not (
        isinstance(policy, dict)
        and is_parallel_aurora_mode(policy.get("execution_mode"))
    ):
        return
    items = session.scalars(
        select(JobItemRecord)
        .where(JobItemRecord.job_id == job.id)
        .order_by(JobItemRecord.ordinal)
        .with_for_update()
    ).all()
    if len(items) != 6:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "AURORA_RETRY_SNAPSHOT_INCOMPLETE",
            "Aurora 并发重试缺少完整六页条目，无法安全恢复。",
        )
    calibrated = policy.get("execution_mode") == "parallel_v20_calibrated"
    if all(
        isinstance(item.configuration, dict)
        and isinstance(item.configuration.get("aurora_snapshot"), dict)
        for item in items
    ):
        return
    authorized_session = plan.payload.get("_harness_session")
    if not isinstance(authorized_session, Mapping):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "AURORA_RETRY_SESSION_MISSING",
            "Aurora 并发重试缺少已授权的原生 Skill 会话。",
        )
    from .aurora_main_image_planner import _persist_parallel_v20_snapshots
    from .aurora_main_image_runtime import AuroraV20HarnessRunner

    workspace = (work_root / job.id).resolve()
    missing_indexes = tuple(
        item.ordinal
        for item in items
        if not (
            isinstance(item.configuration, dict)
            and isinstance(item.configuration.get("aurora_snapshot"), dict)
        )
    )
    if calibrated:
        if plan.payload.get("_parallel_v20_style_calibrated") is True:
            allowed_indexes = missing_indexes
        elif plan.payload.get("_parallel_v20_search_calibrated") is True:
            allowed_indexes = tuple(index for index in missing_indexes if index == 1)
        else:
            allowed_indexes = tuple(index for index in missing_indexes if index == 0)
    else:
        allowed_indexes = tuple(range(6))
    _persist_parallel_v20_snapshots(
        plan,
        job,
        workspace,
        authorized_session,
        AuroraV20HarnessRunner(),
        page_indexes=allowed_indexes,
        execution_mode=str(policy.get("execution_mode") or "parallel_v20"),
    )


def _validate_plan_payload(
    job: JobRecord, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return [{"field": "pages", "message": "pages 必须是数组。"}]
    if len(pages) != job.expected_item_count:
        return [
            {
                "field": "pages",
                "message": "计划页数必须与任务要求一致。",
                "expected": job.expected_item_count,
                "actual": len(pages),
            }
        ]
    if job.type == JobType.DETAIL_PACK.value:
        from .xinghe_agent import validate_editable_plan_payload

        errors = validate_editable_plan_payload(payload, job.expected_item_count)
        if detail_fast_runtime.is_independent(job.configuration) and not errors:
            from .xinghe_agent import XingheAgentError

            try:
                detail_fast_runtime.contract(job.configuration).validate_payload(
                    payload, job.expected_item_count
                )
                if not detail_fast_runtime.contract(job.configuration).approval_matches(
                    payload
                ):
                    errors.append(
                        {
                            "field": "refined_approval",
                            "message": "精美方案已变化，请重新规划并确认。",
                        }
                    )
            except XingheAgentError as exc:
                errors.append(
                    {"field": exc.json_pointer or "plan", "message": str(exc)}
                )
        settings = (
            job.configuration.get("settings")
            if isinstance(job.configuration, Mapping)
            else None
        )
        if (
            isinstance(settings, Mapping)
            and settings.get("market_region") == "domestic"
            and not detail_fast_runtime.is_independent(job.configuration)
        ):
            aurora = payload.get("aurora_v4_3")
            if not isinstance(aurora, Mapping):
                errors.append(
                    {
                        "field": "aurora_v4_3",
                        "message": "Aurora approval packet is required.",
                    }
                )
            elif (
                aurora.get("approved_module_count") != settings.get("page_count")
                or aurora.get("approved_pair_canvas_aspect_ratio") != "3:8"
                or not isinstance(aurora.get("approval_document_sha256"), str)
            ):
                errors.append(
                    {
                        "field": "aurora_v4_3",
                        "message": "Aurora pair approval contract is invalid.",
                    }
                )
            groups = payload.get("groups")
            plan_protocol = (
                groups[0].get("plan_protocol_fields")
                if isinstance(groups, list)
                and groups
                and isinstance(groups[0], Mapping)
                else None
            )
            aurora_plan = (
                plan_protocol.get("aurora_plan")
                if isinstance(plan_protocol, Mapping)
                else None
            )
            if not isinstance(aurora_plan, Mapping):
                errors.append(
                    {
                        "field": "groups.0.plan_protocol_fields.aurora_plan",
                        "message": "Full Aurora compact plan is required.",
                    }
                )
            try:
                validate_plan_views(payload)
                validate_approval_binding(payload, settings)
            except DetailPlanError as exc:
                errors.append(
                    {"field": "aurora_v4_3.contract_binding", "message": str(exc)}
                )
        return errors
    if job.type == JobType.AMAZON_VISUAL_SUITE.value:
        from .amazon_visual_suite_provider import (
            validate_editable_amazon_plan_payload,
        )

        return validate_editable_amazon_plan_payload(payload)
    return []


def _content_signature(
    secret: str,
    workspace_id: str,
    asset_id: str,
    expires: int,
    disposition: str,
    variant: str = "original",
) -> str:
    message = f"{workspace_id}:{asset_id}:{expires}:{disposition}"
    if variant != "original":
        message += f":{variant}-v1"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
