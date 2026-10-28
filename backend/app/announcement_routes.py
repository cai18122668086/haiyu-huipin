from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from starlette.concurrency import run_in_threadpool

from .admin_auth import is_admin
from .announcement_schemas import (
    AnnouncementAdminAccessResponse,
    AnnouncementCreate,
    AnnouncementKind,
    AnnouncementListResponse,
    AnnouncementResponse,
    AnnouncementStatus,
    AnnouncementUpdate,
)
from .auth import Principal, get_principal
from .config import Settings
from .database import get_session, read_snapshot
from .domain import AssetLifecycleStatus
from .errors import ApiError
from .models import AnnouncementRecord, AssetRecord
from .services import new_id

router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


def _now() -> datetime:
    return datetime.now(UTC)


def _announcement_response(
    record: AnnouncementRecord, request: Request | None = None
) -> AnnouncementResponse:
    image_url = None
    if (
        record.image_asset_id is not None
        and record.status == "published"
        and request is not None
    ):
        image_url = str(
            request.url_for("get_announcement_image", announcement_id=record.id)
        )
    return AnnouncementResponse(
        id=record.id,
        title=record.title,
        summary=record.summary,
        content=record.content,
        image_asset_id=record.image_asset_id,
        image_alt=record.image_alt,
        image_url=image_url,
        kind=cast(AnnouncementKind, record.kind),
        status=cast(AnnouncementStatus, record.status),
        link_url=record.link_url,
        starts_at=record.starts_at,
        ends_at=record.ends_at,
        is_pinned=record.is_pinned,
        published_at=record.published_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _active_filters(now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        AnnouncementRecord.status == "published",
        (
            AnnouncementRecord.starts_at.is_(None)
            | (AnnouncementRecord.starts_at <= now)
        ),
        (AnnouncementRecord.ends_at.is_(None) | (AnnouncementRecord.ends_at > now)),
    )


def _is_announcement_admin(
    principal: Principal,
    session: Session,
    settings: Settings,
) -> bool:
    # Keep this compatibility wrapper for the announcement and inspiration
    # modules while sharing the global admin authorization policy.
    return is_admin(principal, session, settings)


def require_announcement_admin(
    request: Request,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Principal:
    if not _is_announcement_admin(principal, session, request.app.state.settings):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "ANNOUNCEMENT_ADMIN_REQUIRED",
            "An active workspace owner or admin role is required to manage "
            "announcements.",
        )
    return principal


AnnouncementAdminDependency = Annotated[Principal, Depends(require_announcement_admin)]


def _get_admin_announcement(
    announcement_id: str,
    session: Session,
) -> AnnouncementRecord:
    record = session.get(AnnouncementRecord, announcement_id)
    if record is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "ANNOUNCEMENT_NOT_FOUND",
            "The announcement was not found.",
        )
    return record


def _validate_record(record: AnnouncementRecord) -> None:
    if not record.title.strip() or not record.content.strip():
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ANNOUNCEMENT_CONTENT_REQUIRED",
            "Announcement title and content are required.",
        )
    if (
        record.starts_at is not None
        and record.ends_at is not None
        and record.ends_at <= record.starts_at
    ):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ANNOUNCEMENT_INVALID_WINDOW",
            "The end time must be later than the start time.",
        )


def _preserve_announcement_image(session: Session, image_asset_id: str | None) -> None:
    if image_asset_id is None:
        return
    asset = session.get(AssetRecord, image_asset_id)
    if asset is None or asset.deleted_at is not None:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ANNOUNCEMENT_IMAGE_NOT_FOUND",
            "The announcement image was not found.",
        )
    asset.retention_class = "system_persistent"
    asset.expires_at = None


@router.get(
    "/announcements/current",
    response_model=AnnouncementResponse | None,
    tags=["announcements"],
)
def get_current_announcement(
    request: Request, session: SessionDependency
) -> AnnouncementResponse | None:
    record = session.scalar(
        select(AnnouncementRecord)
        .where(*_active_filters(_now()))
        .order_by(
            AnnouncementRecord.is_pinned.desc(),
            AnnouncementRecord.published_at.desc(),
            AnnouncementRecord.created_at.desc(),
        )
        .limit(1)
    )
    return _announcement_response(record, request) if record is not None else None


@router.get(
    "/announcements",
    response_model=AnnouncementListResponse,
    tags=["announcements"],
)
def list_public_announcements(
    request: Request,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AnnouncementListResponse:
    records = session.scalars(
        select(AnnouncementRecord)
        .where(*_active_filters(_now()))
        .order_by(
            AnnouncementRecord.is_pinned.desc(),
            AnnouncementRecord.published_at.desc(),
            AnnouncementRecord.created_at.desc(),
        )
        .limit(limit)
    ).all()
    return AnnouncementListResponse(
        items=[_announcement_response(item, request) for item in records]
    )


def _announcement_image_asset(session: Session, announcement_id: str) -> AssetRecord:
    record = session.scalar(
        select(AnnouncementRecord).where(
            AnnouncementRecord.id == announcement_id,
            *_active_filters(_now()),
        )
    )
    if record is None or record.image_asset_id is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "ANNOUNCEMENT_IMAGE_NOT_FOUND",
            "The announcement image was not found.",
        )
    asset = session.scalar(
        select(AssetRecord).where(
            AssetRecord.id == record.image_asset_id,
            AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
            AssetRecord.deleted_at.is_(None),
        )
    )
    if asset is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "ANNOUNCEMENT_IMAGE_NOT_FOUND",
            "The announcement image was not found.",
        )
    return asset


@router.get(
    "/announcements/{announcement_id}/image",
    name="get_announcement_image",
    include_in_schema=False,
)
async def get_announcement_image(
    announcement_id: str,
    request: Request,
    session: SessionDependency,
) -> Response:
    asset = await run_in_threadpool(
        read_snapshot, session, _announcement_image_asset, session, announcement_id
    )
    body = await request.app.state.storage.read_bytes(asset.object_key)
    return Response(
        body,
        media_type=asset.content_type,
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get(
    "/admin/announcements/access",
    response_model=AnnouncementAdminAccessResponse,
    tags=["announcements-admin"],
)
def get_announcement_admin_access(
    request: Request,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AnnouncementAdminAccessResponse:
    return AnnouncementAdminAccessResponse(
        can_manage=_is_announcement_admin(
            principal,
            session,
            request.app.state.settings,
        )
    )


@router.get(
    "/admin/announcements",
    response_model=AnnouncementListResponse,
    tags=["announcements-admin"],
)
def list_admin_announcements(
    session: SessionDependency,
    _admin: AnnouncementAdminDependency,
    status_filter: Annotated[
        Literal["all", "draft", "published", "archived"], Query(alias="status")
    ] = "all",
) -> AnnouncementListResponse:
    filters: list[ColumnElement[bool]] = []
    if status_filter != "all":
        filters.append(AnnouncementRecord.status == status_filter)
    statement = select(AnnouncementRecord)
    if filters:
        statement = statement.where(*filters)
    records = session.scalars(
        statement.order_by(
            AnnouncementRecord.is_pinned.desc(),
            AnnouncementRecord.published_at.desc().nullslast(),
            AnnouncementRecord.created_at.desc(),
        )
    ).all()
    return AnnouncementListResponse(
        items=[_announcement_response(item) for item in records]
    )


@router.post(
    "/admin/announcements",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["announcements-admin"],
)
def create_announcement(
    payload: AnnouncementCreate,
    session: SessionDependency,
    admin: AnnouncementAdminDependency,
) -> AnnouncementResponse:
    record = AnnouncementRecord(
        id=new_id("ann"),
        title=payload.title.strip(),
        summary=payload.summary.strip() if payload.summary else None,
        content=payload.content.strip(),
        image_asset_id=payload.image_asset_id,
        image_alt=payload.image_alt.strip() if payload.image_alt else None,
        kind=payload.kind,
        status="draft",
        link_url=str(payload.link_url) if payload.link_url else None,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        is_pinned=payload.is_pinned,
        created_by_user_id=admin.user_id,
        updated_by_user_id=admin.user_id,
    )
    _validate_record(record)
    _preserve_announcement_image(session, record.image_asset_id)
    session.add(record)
    session.commit()
    session.refresh(record)
    return _announcement_response(record, None)


@router.patch(
    "/admin/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    tags=["announcements-admin"],
)
def update_announcement(
    announcement_id: str,
    payload: AnnouncementUpdate,
    session: SessionDependency,
    admin: AnnouncementAdminDependency,
) -> AnnouncementResponse:
    record = _get_admin_announcement(announcement_id, session)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if key == "link_url":
            value = str(value) if value else None
        if isinstance(value, str):
            value = value.strip()
        setattr(record, key, value)
    _validate_record(record)
    _preserve_announcement_image(session, record.image_asset_id)
    record.updated_by_user_id = admin.user_id
    session.commit()
    session.refresh(record)
    return _announcement_response(record, None)


@router.post(
    "/admin/announcements/{announcement_id}/publish",
    response_model=AnnouncementResponse,
    tags=["announcements-admin"],
)
def publish_announcement(
    announcement_id: str,
    session: SessionDependency,
    admin: AnnouncementAdminDependency,
) -> AnnouncementResponse:
    record = _get_admin_announcement(announcement_id, session)
    _validate_record(record)
    record.status = "published"
    record.published_at = record.published_at or _now()
    record.updated_by_user_id = admin.user_id
    session.commit()
    session.refresh(record)
    return _announcement_response(record, None)


@router.post(
    "/admin/announcements/{announcement_id}/archive",
    response_model=AnnouncementResponse,
    tags=["announcements-admin"],
)
def archive_announcement(
    announcement_id: str,
    session: SessionDependency,
    admin: AnnouncementAdminDependency,
) -> AnnouncementResponse:
    record = _get_admin_announcement(announcement_id, session)
    record.status = "archived"
    record.is_pinned = False
    record.updated_by_user_id = admin.user_id
    session.commit()
    session.refresh(record)
    return _announcement_response(record, None)
