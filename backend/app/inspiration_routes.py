from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .announcement_routes import _is_announcement_admin
from .auth import Principal, get_principal
from .database import get_session
from .domain import AssetLifecycleStatus
from .errors import ApiError
from .inspiration_schemas import (
    InspirationAdminAccessResponse,
    InspirationAssetResponse,
    InspirationCaseCreate,
    InspirationCaseListResponse,
    InspirationCaseResponse,
    InspirationCaseUpdate,
)
from .inspiration_workflows import CASES_REQUIRING_ASSET_REVIEW
from .models import AssetRecord, InspirationCaseAssetRecord, InspirationCaseRecord
from .services import new_id
from .thumbnails import ThumbnailBusy

router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


def _admin(
    request: Request, principal: PrincipalDependency, session: SessionDependency
) -> Principal:
    if not _is_announcement_admin(principal, session, request.app.state.settings):
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "INSPIRATION_ADMIN_REQUIRED",
            "An active workspace owner or admin role is required.",
        )
    return principal


AdminDependency = Annotated[Principal, Depends(_admin)]


def _record(case_id: str, session: Session) -> InspirationCaseRecord:
    item = session.get(InspirationCaseRecord, case_id)
    if item is None:
        raise ApiError(
            404, "INSPIRATION_CASE_NOT_FOUND", "The inspiration case was not found."
        )
    return item


def _asset_response(
    rel: InspirationCaseAssetRecord, request: Request
) -> InspirationAssetResponse:
    url = request.url_for(
        "get_inspiration_case_asset", case_id=rel.case_id, asset_id=rel.asset_id
    )
    return InspirationAssetResponse(
        asset_id=rel.asset_id,
        role=cast(Literal["result", "user_upload", "reference"], rel.role),
        ordinal=rel.ordinal,
        label=rel.label,
        url=(
            url.path
            if request.app.state.settings.asset_access_url_mode == "same_origin"
            else str(url)
        ),
    )


def _configuration_errors(
    item: InspirationCaseRecord,
    rels: Sequence[InspirationCaseAssetRecord],
) -> list[str]:
    errors: list[str] = []
    if item.title in CASES_REQUIRING_ASSET_REVIEW:
        roles = {rel.role for rel in rels}
        if not {"result", "user_upload"}.issubset(roles):
            errors.append("verified_case_assets_required")
    if not item.prompt.strip():
        errors.append("prompt_required")
    if not item.button_label.strip():
        errors.append("button_label_required")
    if item.workflow_type == "legacy_unclassified":
        errors.append("workflow_type_required")
    elif item.workflow_type == "reference_remix":
        if item.reference_source == "none":
            errors.append("reference_source_required")
        elif item.reference_source == "admin_reference" and not any(
            rel.role == "reference" for rel in rels
        ):
            errors.append("admin_reference_asset_required")
        elif item.reference_source == "result_image" and not any(
            rel.role == "result" for rel in rels
        ):
            errors.append("result_reference_asset_required")
    elif (
        item.workflow_type in {"product_prompt", "local_edit"}
        and item.reference_source != "none"
    ):
        errors.append("reference_source_must_be_none")
    return errors


def _configuration_status(
    item: InspirationCaseRecord,
    errors: list[str],
) -> str:
    if item.status == "archived":
        return "archived"
    if errors:
        return "incomplete"
    return "ready" if item.status == "published" else "draft"


def _sync_configuration_status(
    item: InspirationCaseRecord,
    session: Session,
) -> list[str]:
    rels = list(
        session.scalars(
            select(InspirationCaseAssetRecord).where(
                InspirationCaseAssetRecord.case_id == item.id
            )
        ).all()
    )
    errors = _configuration_errors(item, rels)
    item.configuration_status = _configuration_status(item, errors)
    return errors


def _response(
    item: InspirationCaseRecord, session: Session, request: Request
) -> InspirationCaseResponse:
    rels = list(
        session.scalars(
            select(InspirationCaseAssetRecord).where(
                InspirationCaseAssetRecord.case_id == item.id
            )
        ).all()
    )
    # Keep the public gallery order aligned with the prompt's semantic input
    # order: product first, visual references second, display-only results last.
    # SQL's lexical role order put ``reference`` before ``user_upload``, which
    # made the UI's image numbering contradict prompts such as "图一是商品，
    # 图二是参考图" even though the stored roles were correct.
    role_order = {"user_upload": 0, "reference": 1, "result": 2}
    rels.sort(key=lambda rel: (role_order.get(rel.role, 3), rel.ordinal))
    assets = [_asset_response(rel, request) for rel in rels]
    cover = next((a for a in assets if a.role == "result" and a.ordinal == 0), None)
    return InspirationCaseResponse.model_validate(
        {
            "id": item.id,
            "slug": item.slug,
            "title": item.title,
            "description": item.description,
            "role": item.role,
            "type": item.type,
            "category": item.category,
            "platform": item.platform,
            "ratio": item.ratio,
            "intent": item.intent,
            "prompt": item.prompt,
            "workflow_type": item.workflow_type,
            "reference_source": item.reference_source,
            "configuration_status": _configuration_status(
                item, _configuration_errors(item, rels)
            ),
            "configuration_errors": _configuration_errors(item, rels),
            "button_label": item.button_label,
            "status": item.status,
            "sort_order": item.sort_order,
            "is_pinned": item.is_pinned,
            "cover_image": cover,
            "gallery": assets,
            "published_at": item.published_at,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
    )


def _save_assets(
    item: InspirationCaseRecord,
    payload: InspirationCaseCreate | InspirationCaseUpdate,
    session: Session,
) -> None:
    session.execute(
        delete(InspirationCaseAssetRecord).where(
            InspirationCaseAssetRecord.case_id == item.id
        )
    )
    seen: set[tuple[str, int]] = set()
    for asset in payload.assets:
        key = (asset.role, asset.ordinal)
        if key in seen:
            raise ApiError(
                422,
                "INSPIRATION_DUPLICATE_ASSET_ORDER",
                "Duplicate asset role and ordinal.",
            )
        seen.add(key)
        stored = session.get(AssetRecord, asset.asset_id)
        if (
            stored is None
            or stored.deleted_at is not None
            or stored.lifecycle_status != AssetLifecycleStatus.READY.value
        ):
            raise ApiError(
                422,
                "INSPIRATION_ASSET_NOT_FOUND",
                "One or more image assets are unavailable.",
            )
        stored.retention_class = "system_persistent"
        stored.expires_at = None
        session.add(
            InspirationCaseAssetRecord(
                id=new_id("ica"),
                case_id=item.id,
                asset_id=asset.asset_id,
                role=asset.role,
                ordinal=asset.ordinal,
                label=asset.label,
            )
        )


@router.get(
    "/inspiration/cases",
    response_model=InspirationCaseListResponse,
    tags=["inspiration"],
)
def list_cases(
    request: Request,
    session: SessionDependency,
    type: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    query: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InspirationCaseListResponse:
    filters = [InspirationCaseRecord.status == "published"]
    if type:
        filters.append(InspirationCaseRecord.type == type)
    if category:
        filters.append(InspirationCaseRecord.category == category)
    if platform:
        filters.append(InspirationCaseRecord.platform == platform)
    if query:
        filters.append(InspirationCaseRecord.title.ilike(f"%{query}%"))
    items = session.scalars(
        select(InspirationCaseRecord)
        .where(*filters)
        .order_by(
            InspirationCaseRecord.is_pinned.desc(),
            InspirationCaseRecord.sort_order.asc(),
            InspirationCaseRecord.published_at.desc().nullslast(),
        )
        .limit(limit)
    ).all()
    return InspirationCaseListResponse(
        items=[_response(i, session, request) for i in items]
    )


@router.get(
    "/inspiration/cases/{case_id}",
    response_model=InspirationCaseResponse,
    tags=["inspiration"],
)
def get_case(
    case_id: str, request: Request, session: SessionDependency
) -> InspirationCaseResponse:
    item = _record(case_id, session)
    if item.status != "published":
        raise ApiError(
            404, "INSPIRATION_CASE_NOT_FOUND", "The inspiration case was not found."
        )
    return _response(item, session, request)


def _case_asset_for_storage(
    session: Session, case_id: str, asset_id: str
) -> AssetRecord:
    """Load verified metadata without holding a DB connection during image I/O."""
    item = session.scalar(
        select(InspirationCaseRecord).where(
            InspirationCaseRecord.id == case_id,
            InspirationCaseRecord.status == "published",
        )
    )
    rel = session.scalar(
        select(InspirationCaseAssetRecord).where(
            InspirationCaseAssetRecord.case_id == case_id,
            InspirationCaseAssetRecord.asset_id == asset_id,
        )
    )
    asset = session.scalar(
        select(AssetRecord).where(
            AssetRecord.id == asset_id,
            AssetRecord.lifecycle_status == AssetLifecycleStatus.READY.value,
            AssetRecord.deleted_at.is_(None),
        )
    )
    if item is None or rel is None or asset is None:
        raise ApiError(
            404, "INSPIRATION_ASSET_NOT_FOUND", "The inspiration image was not found."
        )
    session.expunge(asset)
    session.rollback()
    return asset


@router.get(
    "/inspiration/cases/{case_id}/assets/{asset_id}",
    name="get_inspiration_case_asset",
    include_in_schema=False,
)
async def get_case_asset(
    case_id: str,
    asset_id: str,
    request: Request,
    session: SessionDependency,
    variant: Literal["original", "thumbnail"] = "original",
) -> Response:
    asset = await run_in_threadpool(_case_asset_for_storage, session, case_id, asset_id)
    # Check publication and asset access before either cache or conditional hits.
    headers = {
        "Cache-Control": "public, max-age=300",
        "ETag": f'"inspiration-{variant}-v1-{asset.sha256}"',
    }
    if request.headers.get("If-None-Match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    if variant == "thumbnail":
        try:
            body = await request.app.state.thumbnails.get(
                f"{asset.object_key}:{asset.sha256}",
                lambda: request.app.state.storage.read_bytes(asset.object_key),
            )
        except ThumbnailBusy:
            return Response(
                status_code=503,
                headers={"Retry-After": "3", "Cache-Control": "no-store"},
            )
        return Response(body, media_type="image/webp", headers=headers)
    return Response(
        await request.app.state.storage.read_bytes(asset.object_key),
        media_type=asset.content_type,
        headers=headers,
    )


@router.get(
    "/admin/inspiration/cases/access",
    response_model=InspirationAdminAccessResponse,
    tags=["inspiration-admin"],
)
def admin_access(
    request: Request, principal: PrincipalDependency, session: SessionDependency
) -> InspirationAdminAccessResponse:
    return InspirationAdminAccessResponse(
        can_manage=_is_announcement_admin(
            principal, session, request.app.state.settings
        )
    )


@router.get(
    "/admin/inspiration/cases",
    response_model=InspirationCaseListResponse,
    tags=["inspiration-admin"],
)
def admin_list(
    request: Request,
    session: SessionDependency,
    _admin: AdminDependency,
    status_filter: Literal["all", "draft", "published", "archived"] = Query(
        "all", alias="status"
    ),
) -> InspirationCaseListResponse:
    stmt = select(InspirationCaseRecord)
    if status_filter != "all":
        stmt = stmt.where(InspirationCaseRecord.status == status_filter)
    items = session.scalars(
        stmt.order_by(
            InspirationCaseRecord.is_pinned.desc(),
            InspirationCaseRecord.sort_order.asc(),
            InspirationCaseRecord.updated_at.desc(),
        )
    ).all()
    return InspirationCaseListResponse(
        items=[_response(i, session, request) for i in items]
    )


@router.post(
    "/admin/inspiration/cases",
    response_model=InspirationCaseResponse,
    status_code=201,
    tags=["inspiration-admin"],
)
def create_case(
    payload: InspirationCaseCreate,
    request: Request,
    session: SessionDependency,
    admin: AdminDependency,
) -> InspirationCaseResponse:
    item = InspirationCaseRecord(
        id=new_id("ins"),
        **payload.model_dump(exclude={"assets"}),
        created_by_user_id=admin.user_id,
        updated_by_user_id=admin.user_id,
    )
    session.add(item)
    session.flush()
    _save_assets(item, payload, session)
    _sync_configuration_status(item, session)
    session.commit()
    session.refresh(item)
    return _response(item, session, request)


@router.patch(
    "/admin/inspiration/cases/{case_id}",
    response_model=InspirationCaseResponse,
    tags=["inspiration-admin"],
)
def update_case(
    case_id: str,
    payload: InspirationCaseUpdate,
    request: Request,
    session: SessionDependency,
    admin: AdminDependency,
) -> InspirationCaseResponse:
    item = _record(case_id, session)
    for key, value in payload.model_dump(exclude={"assets"}).items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    item.updated_by_user_id = admin.user_id
    _save_assets(item, payload, session)
    _sync_configuration_status(item, session)
    session.commit()
    session.refresh(item)
    return _response(item, session, request)


@router.post(
    "/admin/inspiration/cases/{case_id}/publish",
    response_model=InspirationCaseResponse,
    tags=["inspiration-admin"],
)
def publish_case(
    case_id: str, request: Request, session: SessionDependency, admin: AdminDependency
) -> InspirationCaseResponse:
    item = _record(case_id, session)
    errors = _sync_configuration_status(item, session)
    if errors:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INSPIRATION_CASE_CONFIGURATION_INCOMPLETE",
            "Inspiration case configuration is incomplete.",
            {"errors": errors},
        )
    item.status = "published"
    item.published_at = datetime.now(UTC)
    item.configuration_status = "ready"
    item.archived_at = None
    item.updated_by_user_id = admin.user_id
    session.commit()
    session.refresh(item)
    return _response(item, session, request)


@router.post(
    "/admin/inspiration/cases/{case_id}/archive",
    response_model=InspirationCaseResponse,
    tags=["inspiration-admin"],
)
def archive_case(
    case_id: str, request: Request, session: SessionDependency, admin: AdminDependency
) -> InspirationCaseResponse:
    item = _record(case_id, session)
    item.status = "archived"
    item.archived_at = datetime.now(UTC)
    item.configuration_status = "archived"
    item.is_pinned = False
    item.updated_by_user_id = admin.user_id
    session.commit()
    session.refresh(item)
    return _response(item, session, request)
