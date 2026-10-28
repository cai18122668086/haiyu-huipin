"""Public, low-rate ecommerce product fetch endpoint.

The endpoint deliberately delegates parsing to the standalone Taobao/Tmall
adapter and never attempts login, CAPTCHA solving, or access-control bypasses.
"""

import asyncio
import base64
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .admin_auth import AdminDependency, require_admin
from .auth import Principal, get_principal
from .database import get_session
from .domain import (
    EcommerceAccountStatus,
    EcommerceLoginSessionStatus,
    EcommerceLoginState,
)
from .ecommerce_1688 import (
    Alibaba1688Product,
    crawl_one_1688,
    crawl_one_1688_browser,
)
from .ecommerce_account_routes import get_ecommerce_account_for_principal
from .ecommerce_fetcher import crawl_one, crawl_one_browser
from .ecommerce_jd import JDProduct, crawl_one_jd, crawl_one_jd_browser
from .ecommerce_pinduoduo import (
    PinduoduoProduct,
    crawl_one_pinduoduo,
    crawl_one_pinduoduo_browser,
)
from .ecommerce_platform import (
    PlatformPolicyError,
    SiteFamily,
    resolve_site_policy,
)
from .ecommerce_session import EcommerceSessionManager
from .errors import ApiError
from .models import (
    AuditEventRecord,
    CrawlProfileLeaseRecord,
    CrawlTaskRecord,
    EcommerceLoginSessionRecord,
    EcommerceWorkerRecord,
    utc_now,
)

router = APIRouter(prefix="/api/ecommerce", tags=["ecommerce"])
logger = logging.getLogger(__name__)
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]

_ACCOUNT_PLATFORM_TO_FAMILY = {
    "taobao": SiteFamily.TAOBAO,
    "1688": SiteFamily.ALIBABA_1688,
    "jd": SiteFamily.JD,
    "pinduoduo": SiteFamily.PINDUODUO,
}


def _account_manager(
    request: Request | WebSocket, account: Any
) -> EcommerceSessionManager:
    managers = request.app.state.ecommerce_account_sessions
    manager = managers.get(account.id)
    if manager is None:
        manager = EcommerceSessionManager(
            Path(account.profile_path),
            platform="alibaba1688" if account.platform == "1688" else account.platform,
            session_profile=account.profile_id,
            state_style="legacy" if account.platform == "taobao" else "canonical",
        )
        managers[account.id] = manager
    return manager


def _sync_account_login_state(
    session: Session, account: Any, session_status: dict[str, Any]
) -> None:
    state = session_status.get("state")
    if state == "ready":
        if account.login_state != EcommerceLoginState.READY.value:
            account.last_login_at = utc_now()
        account.login_state = EcommerceLoginState.READY.value
    elif state in {
        "signed_out",
        "needs_relogin",
        "login_required",
        "risk_controlled",
        "not_initialized",
        "unavailable",
        "error",
    }:
        account.login_state = EcommerceLoginState.NEEDS_LOGIN.value


def _resolve_account_session_target(
    request: Request | WebSocket,
    session: Session,
    principal: Principal,
    *,
    account_id: str | None,
    requested_platform: str,
    write: bool,
) -> tuple[Any | None, EcommerceSessionManager, SiteFamily | None]:
    if not account_id:
        manager = _session_manager_for_hint(request, requested_platform)
        return None, manager, None
    account = get_ecommerce_account_for_principal(
        session, principal, account_id, write=write
    )
    if write and account.status != EcommerceAccountStatus.ACTIVE.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_NOT_AVAILABLE",
                "message": "当前电商账号不可用，请先恢复账号。",
                "retryable": False,
            },
        )
    allowed_platforms = {"auto", account.platform}
    if account.platform == "taobao":
        allowed_platforms.add("tmall")
    if requested_platform not in allowed_platforms:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_PLATFORM_MISMATCH",
                "message": "授权会话平台与电商账号平台不一致。",
                "retryable": False,
            },
        )
    family = _ACCOUNT_PLATFORM_TO_FAMILY[account.platform]
    return account, _account_manager(request, account), family


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _login_session_response(
    record: EcommerceLoginSessionRecord | None,
) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "status": record.status,
        "transport": record.transport,
        "startedAt": _iso(record.started_at),
        "expiresAt": _iso(record.expires_at),
        "lastSeenAt": _iso(record.last_seen_at),
        "completedAt": _iso(record.completed_at),
        "errorCode": record.error_code,
        "state": getattr(record, "state", "queued"),
        "workerId": getattr(record, "worker_id", None),
        "claimedAt": _iso(getattr(record, "claimed_at", None)),
        "lastFrameAt": _iso(getattr(record, "last_frame_at", None)),
        "previewUrl": (
            f"/api/ecommerce/session/preview?session_id={record.id}"
            if record.transport == "remote_worker"
            else None
        ),
    }


def _sync_login_session(
    session: Session,
    record: EcommerceLoginSessionRecord | None,
    manager: EcommerceSessionManager,
) -> EcommerceLoginSessionRecord | None:
    if record is None:
        return None
    now = utc_now()
    if record.transport == "remote_worker":
        record.last_seen_at = now
        if (
            record.status == EcommerceLoginSessionStatus.ACTIVE.value
            and _as_utc(record.expires_at) <= now
        ):
            record.status = EcommerceLoginSessionStatus.EXPIRED.value
            record.state = "expired"
            record.error_code = "LOGIN_SESSION_EXPIRED"
            record.completed_at = now
        return record
    status = manager.status()
    record.last_seen_at = now
    if record.status == EcommerceLoginSessionStatus.ACTIVE.value:
        if _as_utc(record.expires_at) <= now:
            manager.cancel_login()
            record.status = EcommerceLoginSessionStatus.EXPIRED.value
            record.error_code = "LOGIN_SESSION_EXPIRED"
            record.completed_at = now
        elif status.get("state") == "ready":
            record.status = EcommerceLoginSessionStatus.COMPLETED.value
            record.completed_at = now
        elif status.get("state") == "error":
            record.status = EcommerceLoginSessionStatus.FAILED.value
            record.error_code = "LOGIN_HELPER_FAILED"
            record.completed_at = now
    return record


def _latest_login_session(
    session: Session, account_id: str, *, include_terminal: bool = False
) -> EcommerceLoginSessionRecord | None:
    records = session.scalars(
        select(EcommerceLoginSessionRecord)
        .where(EcommerceLoginSessionRecord.account_id == account_id)
        .order_by(
            EcommerceLoginSessionRecord.started_at.desc(),
            EcommerceLoginSessionRecord.id.desc(),
        )
        .limit(10)
    ).all()
    now = utc_now()
    latest: EcommerceLoginSessionRecord | None = None
    for record in records:
        if (
            record.status == EcommerceLoginSessionStatus.ACTIVE.value
            and _as_utc(record.expires_at) <= now
        ):
            record.status = EcommerceLoginSessionStatus.EXPIRED.value
            record.error_code = "LOGIN_SESSION_EXPIRED"
            record.completed_at = now
        if latest is None and (
            include_terminal
            or record.status == EcommerceLoginSessionStatus.ACTIVE.value
        ):
            latest = record
    return latest


def _require_login_enabled(request: Request) -> None:
    if not request.app.state.settings.ecommerce_login_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ECOMMERCE_LOGIN_DISABLED",
                "message": "当前环境未启用受控电商登录。请通过批准的运维流程开启。",
                "retryable": False,
            },
        )


def _session_manager_for_family(request: Request, site_family: SiteFamily):
    """Return the manager bound to the resolved site policy.

    Crawl tasks may attach a workspace-owned account to the internal request.
    In that case lazily create a manager for that account's isolated profile;
    legacy interactive endpoints continue to use the platform default manager.
    """

    account = getattr(getattr(request, "state", None), "ecommerce_account", None)
    if account is not None:
        managers = request.app.state.ecommerce_account_sessions
        manager = managers.get(account.id)
        if manager is None:
            platform = {
                SiteFamily.ALIBABA_1688: "alibaba1688",
                SiteFamily.JD: "jd",
                SiteFamily.PINDUODUO: "pinduoduo",
            }.get(site_family, "taobao")
            manager = EcommerceSessionManager(
                Path(account.profile_path),
                platform=platform,
                session_profile=account.profile_id,
                state_style="legacy"
                if site_family in {SiteFamily.TAOBAO, SiteFamily.TMALL}
                else "canonical",
            )
            managers[account.id] = manager
        return manager

    if site_family is SiteFamily.ALIBABA_1688:
        return request.app.state.ecommerce_session_1688
    if site_family is SiteFamily.JD:
        return request.app.state.ecommerce_session_jd
    if site_family is SiteFamily.PINDUODUO:
        return request.app.state.ecommerce_session_pinduoduo
    return request.app.state.ecommerce_session


def _session_manager_for_hint(request: Request | WebSocket, platform: str):
    if platform in {"alibaba1688", "1688"}:
        return request.app.state.ecommerce_session_1688
    if platform == "jd":
        return request.app.state.ecommerce_session_jd
    if platform == "pinduoduo":
        return request.app.state.ecommerce_session_pinduoduo
    if platform in {"taobao", "tmall", "auto"}:
        return request.app.state.ecommerce_session
    raise HTTPException(status_code=400, detail="不支持的授权会话平台。")


class FetchItemRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    platform: Literal[
        "auto", "taobao", "tmall", "alibaba1688", "jd", "pinduoduo", "amazon"
    ] = "auto"
    includeDescImages: bool = True


class SessionStartRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    account_id: str | None = Field(default=None, min_length=1, max_length=40)
    platform: Literal[
        "auto", "taobao", "tmall", "alibaba1688", "1688", "jd", "pinduoduo"
    ] = "auto"


class SessionCancelRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=40)


class LoginJobCreateRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=40)
    worker_id: str | None = Field(default=None, max_length=40)
    platform: str = Field(default="auto", min_length=1, max_length=24)
    expires_in: int = Field(default=600, ge=60, le=1800)


class SessionInputRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=40)
    session_id: str | None = Field(default=None, min_length=1, max_length=40)
    type: Literal["click", "drag", "wheel", "key", "text"]
    x: float | None = Field(default=None, ge=0, le=10000)
    y: float | None = Field(default=None, ge=0, le=10000)
    fromX: float | None = Field(default=None, ge=0, le=10000)
    fromY: float | None = Field(default=None, ge=0, le=10000)
    toX: float | None = Field(default=None, ge=0, le=10000)
    toY: float | None = Field(default=None, ge=0, le=10000)
    deltaX: float | None = Field(default=None, ge=-10000, le=10000)
    deltaY: float | None = Field(default=None, ge=-10000, le=10000)
    key: str | None = Field(default=None, min_length=1, max_length=80)
    text: str | None = Field(default=None, max_length=200)
    button: Literal["left", "middle", "right"] = "left"


def _normalise(result: object, source_url: str, include_desc: bool) -> dict:
    raw = getattr(result, "__dict__", {})

    def as_mapping(value: object) -> dict:
        if isinstance(value, dict):
            return value
        return getattr(value, "__dict__", {})

    images = raw.get("images", [])
    main: list[dict[str, object]] = []
    detail: list[dict[str, object]] = []
    sku: list[dict[str, object]] = []
    for item in images:
        value = as_mapping(item)
        entry = {
            "url": value.get("url"),
            "ordinal": value.get("ordinal", 0),
            "source": value.get("source", ""),
        }
        role = value.get("role", "main")
        (detail if role == "detail" else sku if role == "sku" else main).append(entry)

    raw_groups = raw.get("sku_prop_groups", [])
    sku_prop_groups = [as_mapping(group) for group in raw_groups]
    raw_skus = raw.get("skus", [])
    skus = [as_mapping(item) for item in raw_skus]
    platform = raw.get("platform") or getattr(result, "platform", None)
    if not platform:
        platform = "tmall" if raw.get("platform") == "tmall" else "taobao"
    platform_data: dict[str, object] = {}
    if platform == "alibaba1688":
        platform_data = {
            "minimumOrderQuantity": raw.get("min_order_quantity"),
        }
    return {
        "platform": platform,
        "numIid": raw.get("product_id"),
        "tmall": platform == "tmall",
        "title": raw.get("title"),
        "price": raw.get("price"),
        "shopName": raw.get("shop_name"),
        "sales": raw.get("sales"),
        "imageUrls": [x["url"] for x in main],
        "descImageUrls": [x["url"] for x in detail] if include_desc else [],
        "skuImageUrls": [x["url"] for x in sku],
        "skuPropGroups": sku_prop_groups,
        "skus": skus,
        "propsText": raw.get("props_text", ""),
        "services": raw.get("services", []),
        "platformData": platform_data,
        "imageDiagnostics": raw.get("image_diagnostics", {}),
        "accessState": raw.get("access_state"),
        "sourceUrl": source_url,
        "warnings": list(raw.get("warnings", [])),
    }


def _log_image_diagnostics(stage: str, result: object) -> None:
    """Log counts only; never log product URLs or session-bearing values."""

    diagnostics = getattr(result, "image_diagnostics", {})
    images = getattr(result, "images", [])
    role_counts = {"main": 0, "detail": 0, "sku": 0}
    for image in images:
        role = getattr(image, "role", "main")
        if role in role_counts:
            role_counts[role] += 1
    logger.warning(
        "ecommerce_image_diagnostics stage=%s main=%d detail=%d sku=%d "
        "dom_elements=%d dom_url_values=%d json_values=%d "
        "seen=%d accepted=%d rejected_invalid=%d rejected_filter=%d "
        "rejected_small_media=%d dedup_exact=%d dedup_identity=%d "
        "rejected_main_overflow=%d "
        "reclassified_main_overflow_to_detail=%d",
        stage,
        role_counts["main"],
        role_counts["detail"],
        role_counts["sku"],
        diagnostics.get("dom_elements", 0),
        diagnostics.get("dom_url_values", 0),
        diagnostics.get("json_values", 0),
        diagnostics.get("image_values_seen", diagnostics.get("seen", 0)),
        diagnostics.get("accepted_candidates", diagnostics.get("accepted", 0)),
        diagnostics.get("rejected_invalid_url", diagnostics.get("rejected_invalid", 0)),
        diagnostics.get("rejected_image_filter", diagnostics.get("rejected_filter", 0)),
        diagnostics.get("rejected_dom_small_media", 0),
        diagnostics.get("dedup_exact", 0),
        diagnostics.get("dedup_identity", 0),
        diagnostics.get("rejected_main_overflow", 0),
        diagnostics.get("reclassified_main_overflow_to_detail", 0),
    )


@router.get("/capabilities")
def ecommerce_capabilities(request: Request) -> dict:
    """Expose non-sensitive adapter wiring for local UI/service diagnostics.

    This endpoint intentionally returns capability metadata only. It does not
    reveal profile paths, cookies, tokens, or any browser-session contents.
    """

    return {
        "success": True,
        "data": {
            "apiVersion": "ecommerce-1688-v1",
            "platforms": {
                "taobao": {
                    "enabled": True,
                    "adapter": "taobao",
                    "networkMode": "direct",
                    "sessionProfile": "domestic-taobao-tmall",
                },
                "tmall": {
                    "enabled": True,
                    "adapter": "taobao",
                    "networkMode": "direct",
                    "sessionProfile": "domestic-taobao-tmall",
                },
                "1688": {
                    "enabled": True,
                    "adapter": "1688",
                    "networkMode": "direct",
                    "sessionProfile": "domestic-1688",
                },
                "jd": {
                    "enabled": True,
                    "adapter": "jd",
                    "networkMode": "direct",
                    "sessionProfile": "domestic-jd",
                },
                "pinduoduo": {
                    "enabled": True,
                    "adapter": "pinduoduo",
                    "networkMode": "direct",
                    "sessionProfile": "domestic-pinduoduo",
                },
                "amazon": {
                    "enabled": False,
                    "status": "planned",
                    "adapter": "amazon",
                    "networkMode": "proxy",
                },
            },
            "browserFallback": bool(
                request.app.state.settings.ecommerce_browser_fallback
            ),
        },
    }


async def _fetch_pinduoduo(
    request: Request, payload: FetchItemRequest, url: str
) -> dict:
    """Run the PDD adapter under its own direct-only session policy."""

    settings = request.app.state.settings
    session_manager = _session_manager_for_family(request, SiteFamily.PINDUODUO)
    profile_status = session_manager.status()
    profile_ready = bool(profile_status.get("profileReady"))
    session_ready = profile_ready and profile_status.get("state") == "ready"
    result: PinduoduoProduct | None = None
    fetch_error: Exception | None = None
    browser_used = False

    async def render_with_authorized_session() -> PinduoduoProduct:
        return await asyncio.wait_for(
            asyncio.to_thread(
                crawl_one_pinduoduo_browser,
                url,
                settings.ecommerce_browser_timeout_ms,
                str(session_manager.profile_dir),
            ),
            timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
        )

    # The profile directory is the durable authorization boundary. The status
    # file can be not_initialized after the visible login helper exits (for
    # example when the user closes the window), even though the Chrome profile
    # still contains the signed-in session. Static HTTP cannot see that
    # account context and commonly returns an empty shell, so try the owned
    # profile first whenever it exists.
    if profile_ready:
        try:
            result = await render_with_authorized_session()
            browser_used = True
        except Exception as exc:
            fetch_error = exc

    if result is None:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(crawl_one_pinduoduo, url), timeout=35
            )
            fetch_error = None
        except Exception as exc:
            fetch_error = exc

    if fetch_error is not None:
        if isinstance(fetch_error, TimeoutError):
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "FETCH_TIMEOUT",
                    "message": "拼多多商品页面响应超时，请稍后重试。",
                    "retryable": True,
                },
            ) from fetch_error
        raise HTTPException(
            status_code=502,
            detail={
                "code": "FETCH_FAILED",
                "message": f"拼多多商品解析失败：{fetch_error}",
                "retryable": True,
            },
        ) from fetch_error

    assert result is not None
    if result.status != "product":
        status_details = {
            "login_required": (401, "LOGIN_REQUIRED", "拼多多页面要求正常登录授权。"),
            "risk_controlled": (
                403,
                "RISK_CONTROL_PAGE",
                "拼多多页面触发风控，请稍后重试。",
            ),
            "product_not_found": (
                404,
                "PRODUCT_NOT_FOUND",
                "拼多多商品不存在、已下架或已售罄。",
            ),
            "empty": (422, "EMPTY_PRODUCT", "未读取到拼多多商品内容。"),
        }
        status_code, code, message = status_details[result.status]
        if result.status == "risk_controlled" and session_ready:
            message = (
                "本次拼多多商品页触发风控，当前会话仍保持连接；"
                "请打开拼多多官方页面完成人工验证后再拉取。"
            )
        if result.status == "login_required":
            session_manager.mark_needs_relogin()
        elif result.status == "risk_controlled" and not session_ready:
            session_manager.mark_state(
                "risk_controlled",
                "拼多多商品页触发安全验证或风控，请稍后通过官方页面完成验证。",
            )
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "retryable": result.status != "product_not_found",
            },
        )

    data = _normalise(result, url, payload.includeDescImages)
    _log_image_diagnostics("pinduoduo-browser" if browser_used else "pinduoduo", result)
    if browser_used:
        # A product response obtained through the owned profile validates the
        # session even when the site's post-login DOM is not recognized by the
        # login helper's account-page heuristic.
        session_manager.mark_ready()
    if (
        (settings.ecommerce_browser_fallback or profile_ready)
        and (not data["imageUrls"] or not data["title"] or payload.includeDescImages)
        and not browser_used
    ):
        try:
            rendered = await asyncio.wait_for(
                asyncio.to_thread(
                    crawl_one_pinduoduo_browser,
                    url,
                    settings.ecommerce_browser_timeout_ms,
                    str(session_manager.profile_dir) if profile_ready else None,
                ),
                timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
            )
            if rendered.status == "product":
                data = _normalise(rendered, url, payload.includeDescImages)
                _log_image_diagnostics("pinduoduo-browser", rendered)
                data["warnings"] = list(data.get("warnings", [])) + [
                    "已使用服务端浏览器渲染拼多多页面。"
                ]
                if profile_ready:
                    session_manager.mark_ready()
        except TimeoutError:
            data["warnings"] = list(data.get("warnings", [])) + [
                "拼多多服务端浏览器渲染超时。"
            ]
        except Exception:
            data["warnings"] = list(data.get("warnings", [])) + [
                "拼多多服务端浏览器渲染不可用，已保留静态解析结果。"
            ]
    if not data["imageUrls"] and not data["descImageUrls"]:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "IMAGE_EXTRACTION_INCOMPLETE",
                "message": "已读取到拼多多商品字段，但未读取到商品图片。",
                "retryable": True,
            },
        )
    return {"success": True, "data": data}


@router.post("/fetch-item")
async def fetch_item(request: Request, payload: FetchItemRequest) -> dict:
    url = payload.url.strip()
    settings = request.app.state.settings
    try:
        # Amazon remains a future capability in the domestic ecommerce MVP.
        # Fail explicitly instead of exposing a proxy/configuration error.
        if payload.platform == "amazon" or "amazon" in url.casefold():
            raise HTTPException(
                status_code=501,
                detail={
                    "code": "PLATFORM_NOT_ENABLED",
                    "message": "亚马逊当前仅规划，尚未启用。",
                    "retryable": False,
                },
            )
        policy = resolve_site_policy(
            url,
            requested_platform=payload.platform,
            proxy_configured=settings.ecommerce_proxy_enabled,
        )
    except PlatformPolicyError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        ) from exc

    if policy.site_family is SiteFamily.PINDUODUO:
        return await _fetch_pinduoduo(request, payload, url)

    if policy.site_family is SiteFamily.ALIBABA_1688:
        # 1688 is wired to its own direct-only adapter. It never falls through
        # to the Taobao parser and never reads the Amazon proxy configuration.
        session_manager = _session_manager_for_family(request, SiteFamily.ALIBABA_1688)
        profile_status = session_manager.status()
        profile_ready = bool(profile_status.get("profileReady"))
        session_ready = profile_ready and profile_status.get("state") == "ready"
        alibaba_result: Alibaba1688Product | None = None
        static_error: Exception | None = None

        async def render_with_authorized_session():
            return await asyncio.wait_for(
                asyncio.to_thread(
                    crawl_one_1688_browser,
                    url,
                    settings.ecommerce_browser_timeout_ms,
                    str(session_manager.profile_dir),
                ),
                timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
            )

        browser_used = False
        if session_ready:
            # Once the user has authorized a browser profile, do not make an
            # extra unauthenticated urllib request first. That request has no
            # account context and can itself trigger a page-level challenge;
            # the browser the user authorized is the safer and more faithful
            # first hop. It also avoids two visits to the same offer before a
            # human has had a chance to complete the official verification.
            try:
                alibaba_result = await render_with_authorized_session()
                browser_used = True
            except Exception as browser_error:
                static_error = browser_error
        else:
            try:
                alibaba_result = await asyncio.wait_for(
                    asyncio.to_thread(crawl_one_1688, url), timeout=35
                )
            except Exception as exc:
                static_error = exc

        if static_error is not None:
            if isinstance(static_error, TimeoutError):
                raise HTTPException(
                    status_code=504,
                    detail={
                        "code": "FETCH_TIMEOUT",
                        "message": "1688 商品页面响应超时，请稍后重试。",
                        "retryable": True,
                    },
                ) from static_error
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"1688 商品解析失败：{static_error}",
                    "retryable": True,
                },
            ) from static_error

        assert alibaba_result is not None
        if alibaba_result.status != "product":
            status_details = {
                "login_required": (
                    401,
                    "LOGIN_REQUIRED",
                    "1688 页面要求正常登录授权。",
                ),
                "risk_controlled": (
                    403,
                    "RISK_CONTROL_PAGE",
                    "1688 页面触发风控，请稍后重试。",
                ),
                "product_not_found": (
                    404,
                    "PRODUCT_NOT_FOUND",
                    "1688 商品不存在或已下架。",
                ),
                "empty": (422, "EMPTY_PRODUCT", "未读取到 1688 商品内容。"),
            }
            status_code, code, message = status_details[alibaba_result.status]
            if alibaba_result.status == "risk_controlled" and session_ready:
                message = (
                    "本次 1688 商品页触发风控，当前会话仍保持连接，"
                    "无需重新登录。请打开官方验证页完成一次人工验证后再拉取。"
                )
            # A risk-control page is a transient page-level response. It does
            # not prove that the human-authorized 1688 session expired, so keep
            # the existing session state (especially ``ready``) intact. Only an
            # explicit login page is evidence that the session needs relogin.
            if alibaba_result.status == "login_required":
                session_manager.mark_needs_relogin()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": code,
                    "message": message,
                    "retryable": alibaba_result.status != "product_not_found",
                    "policy": policy.as_dict(),
                },
            )
        data = _normalise(alibaba_result, url, payload.includeDescImages)
        _log_image_diagnostics(
            "1688-browser" if browser_used else "1688-static", alibaba_result
        )
        needs_browser = (
            not data["imageUrls"]
            or not data["title"]
            or payload.includeDescImages
            and not data["descImageUrls"]
        )
        # 1688 product shells commonly expose title/price in the initial HTML
        # while adding the gallery only after client-side rendering. Image
        # extraction therefore needs a browser retry whenever the static alibaba_result
        # is incomplete, even when the optional authenticated profile is not
        # ready. This retry never logs in or submits credentials: it uses an
        # authorized profile only when ``session_ready`` is true, otherwise it
        # renders with an isolated ephemeral browser context.
        if needs_browser and not browser_used:
            try:
                rendered = await (
                    render_with_authorized_session()
                    if session_ready
                    else asyncio.wait_for(
                        asyncio.to_thread(
                            crawl_one_1688_browser,
                            url,
                            settings.ecommerce_browser_timeout_ms,
                            None,
                        ),
                        timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
                    )
                )
                if rendered.status == "product":
                    data = _normalise(rendered, url, payload.includeDescImages)
                    _log_image_diagnostics("1688-browser", rendered)
                    data["warnings"] = list(data.get("warnings", [])) + [
                        "已使用独立 1688 浏览器会话渲染页面。"
                    ]
                    if session_ready:
                        session_manager.mark_ready()
            except TimeoutError:
                data["warnings"] = list(data.get("warnings", [])) + [
                    "1688 浏览器渲染超时，已保留静态解析结果。"
                ]
            except Exception as exc:
                data["warnings"] = list(data.get("warnings", [])) + [
                    "1688 浏览器渲染不可用，已保留静态解析结果。"
                ]
                if str(exc) != "BROWSER_RENDERER_UNAVAILABLE":
                    data["warnings"].append("1688 渲染器错误已记录为可重试失败。")
        if not data["imageUrls"] and not data["descImageUrls"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "IMAGE_EXTRACTION_INCOMPLETE",
                    "message": "已读取到 1688 商品字段，但未读取到商品图片。",
                    "retryable": True,
                },
            )
        return {"success": True, "data": data}

    if policy.site_family is SiteFamily.JD:
        # JD has its own parser and persistent profile. Keep this branch
        # separate from both the Taobao fetcher and the 1688 adapter: the
        # three sites expose different page shells and image semantics.
        session_manager = _session_manager_for_family(request, SiteFamily.JD)
        profile_status = session_manager.status()
        profile_ready = bool(profile_status.get("profileReady"))
        session_ready = profile_ready and profile_status.get("state") == "ready"
        jd_result: JDProduct | None = None
        fetch_error: Exception | None = None

        async def render_with_authorized_session():
            return await asyncio.wait_for(
                asyncio.to_thread(
                    crawl_one_jd_browser,
                    url,
                    settings.ecommerce_browser_timeout_ms,
                    str(session_manager.profile_dir),
                ),
                timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
            )

        if session_ready:
            try:
                jd_result = await render_with_authorized_session()
            except Exception as exc:
                fetch_error = exc
        else:
            try:
                jd_result = await asyncio.wait_for(
                    asyncio.to_thread(crawl_one_jd, url), timeout=35
                )
            except Exception as exc:
                fetch_error = exc

        if fetch_error is not None:
            if isinstance(fetch_error, TimeoutError):
                raise HTTPException(
                    status_code=504,
                    detail={
                        "code": "FETCH_TIMEOUT",
                        "message": "京东商品页面响应超时，请稍后重试。",
                        "retryable": True,
                    },
                ) from fetch_error
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "FETCH_FAILED",
                    "message": f"京东商品解析失败：{fetch_error}",
                    "retryable": True,
                },
            ) from fetch_error

        assert jd_result is not None
        if jd_result.status != "product":
            status_details = {
                "login_required": (401, "LOGIN_REQUIRED", "京东页面要求正常登录授权。"),
                "risk_controlled": (
                    403,
                    "RISK_CONTROL_PAGE",
                    "京东页面触发风控，请稍后重试。",
                ),
                "product_not_found": (
                    404,
                    "PRODUCT_NOT_FOUND",
                    "京东商品不存在或已下架。",
                ),
                "empty": (422, "EMPTY_PRODUCT", "未读取到京东商品内容。"),
            }
            status_code, code, message = status_details[jd_result.status]
            if jd_result.status == "risk_controlled" and session_ready:
                message = (
                    "本次京东商品页触发风控，当前会话仍保持连接；"
                    "请打开京东官方页面完成人工验证后再拉取。"
                )
            if (
                jd_result.status in {"login_required", "risk_controlled"}
                and not session_ready
            ):
                session_manager.mark_needs_relogin()
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": code,
                    "message": message,
                    "retryable": jd_result.status != "product_not_found",
                },
            )

        data = _normalise(jd_result, url, payload.includeDescImages)
        _log_image_diagnostics("jd", jd_result)
        if (
            (settings.ecommerce_browser_fallback or profile_ready)
            and (
                not data["imageUrls"] or not data["title"] or payload.includeDescImages
            )
            and not session_ready
        ):
            try:
                rendered = await asyncio.wait_for(
                    asyncio.to_thread(
                        crawl_one_jd_browser,
                        url,
                        settings.ecommerce_browser_timeout_ms,
                        str(session_manager.profile_dir) if profile_ready else None,
                    ),
                    timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
                )
                if rendered.status == "product":
                    data = _normalise(rendered, url, payload.includeDescImages)
                    _log_image_diagnostics("jd-browser", rendered)
                    data["warnings"] = list(data.get("warnings", [])) + [
                        "已使用服务端浏览器渲染京东页面。"
                    ]
            except TimeoutError:
                data["warnings"] = list(data.get("warnings", [])) + [
                    "京东服务端浏览器渲染超时。"
                ]
            except Exception:
                data["warnings"] = list(data.get("warnings", [])) + [
                    "京东服务端浏览器渲染不可用，已保留静态解析结果。"
                ]
        if not data["imageUrls"] and not data["descImageUrls"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "IMAGE_EXTRACTION_INCOMPLETE",
                    "message": "已读取到京东商品字段，但未读取到商品图片。",
                    "retryable": True,
                },
            )
        return {"success": True, "data": data}

    if policy.site_family not in {SiteFamily.TAOBAO, SiteFamily.TMALL}:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "PLATFORM_NOT_IMPLEMENTED",
                "message": (
                    f"{policy.site_family.value} 已完成站点识别与策略校验，"
                    "商品适配器尚未接入。"
                ),
                "retryable": False,
                "policy": policy.as_dict(),
            },
        )
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(crawl_one, url, None, 80, 0.0, False), timeout=35
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "code": "FETCH_TIMEOUT",
                "message": "商品页面响应超时，请稍后重试。",
                "retryable": True,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "FETCH_FAILED",
                "message": f"商品解析失败：{exc}",
                "retryable": True,
            },
        ) from exc
    data = _normalise(result, url, payload.includeDescImages)
    _log_image_diagnostics("static", result)
    session_manager = _session_manager_for_family(request, SiteFamily.TAOBAO)
    session_status = session_manager.status()
    profile_ready = bool(session_status.get("profileReady"))
    # A static response can contain the title and six main images while still
    # omitting the tail of a lazy-loaded description. When an authorized
    # profile is available, render the page whenever detail images were
    # requested so the browser can walk the full description before parsing.
    needs_browser = (
        not data["imageUrls"] or not data["title"] or payload.includeDescImages
    )
    if (settings.ecommerce_browser_fallback or profile_ready) and needs_browser:
        try:
            rendered = await asyncio.wait_for(
                asyncio.to_thread(
                    crawl_one_browser,
                    url,
                    settings.ecommerce_browser_timeout_ms,
                    str(session_manager.profile_dir) if profile_ready else None,
                ),
                timeout=(settings.ecommerce_browser_timeout_ms / 1000) + 5,
            )
            data = _normalise(rendered, url, payload.includeDescImages)
            _log_image_diagnostics("browser", rendered)
            data["warnings"] = list(data.get("warnings", [])) + [
                "已使用服务端浏览器渲染页面。"
            ]
            if profile_ready and (
                data["title"] or data["imageUrls"] or data["descImageUrls"]
            ):
                session_manager.mark_ready()
        except TimeoutError:
            data["warnings"] = list(data.get("warnings", [])) + [
                "服务端浏览器渲染超时。"
            ]
        except Exception as exc:
            data["warnings"] = list(data.get("warnings", [])) + [
                "服务端浏览器渲染不可用，已保留静态解析结果。"
            ]
            if str(exc) != "BROWSER_RENDERER_UNAVAILABLE":
                data["warnings"].append("渲染器错误已记录为可重试失败。")
    if not data["imageUrls"] and not data["descImageUrls"]:
        # An empty static response or a renderer timeout does not prove that
        # the human-authorized session expired. Only an explicit platform
        # login/risk response should force a new authorization flow.
        access_state = data.get("accessState")
        if access_state == "login_required":
            session_manager.mark_needs_relogin()
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "LOGIN_REQUIRED",
                    "message": "淘宝/天猫商品页要求登录授权，请先完成官方登录。",
                    "retryable": True,
                },
            )
        if access_state == "captcha_required":
            session_manager.mark_state(
                "risk_controlled",
                "淘宝/天猫商品页要求人工完成验证码验证。",
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "CAPTCHA_REQUIRED",
                    "message": "淘宝/天猫商品页要求人工完成验证码验证。",
                    "retryable": True,
                },
            )
        if access_state == "risk_controlled" or any(
            "登录或风控" in warning or "风控" in warning
            for warning in data.get("warnings", [])
        ):
            session_manager.mark_state(
                "risk_controlled",
                "淘宝/天猫商品页触发安全验证或风控。",
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "RISK_CONTROL",
                    "message": "淘宝/天猫商品页触发风控，请完成人工验证后重试。",
                    "retryable": True,
                },
            )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EMPTY_PRODUCT",
                "message": "未读取到商品内容，可能触发了平台风控。",
                "retryable": True,
            },
        )
    # ``accessState`` is an internal classification signal; callers receive
    # the stable product contract while blocked pages are represented by the
    # explicit HTTP error above.
    data.pop("accessState", None)
    return {"success": True, "data": data}


@router.get("/session/status")
def ecommerce_session_status(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
    platform: str = Query(default="taobao"),
    account_id: str | None = Query(default=None, min_length=1, max_length=40),
) -> dict:
    account, manager, _ = _resolve_account_session_target(
        request,
        session,
        principal,
        account_id=account_id,
        requested_platform=platform,
        write=False,
    )
    session_status = (
        manager.status()
        if account is None or not account.worker_id
        else {"state": account.login_state, "message": ""}
    )
    login_session = None
    if account is not None:
        login_session = _latest_login_session(
            session, account.id, include_terminal=True
        )
        if login_session is not None and login_session.transport == "remote_worker":
            _sync_login_session(session, login_session, manager)
            session_status = {
                "state": login_session.state,
                "message": login_session.error_code or "",
                "previewAvailable": bool(
                    login_session.id in request.app.state.ecommerce_remote_login_frames
                ),
            }
            if login_session.state == "ready":
                # A completed login handshake is not proof that the next
                # product request still has access. Keep a later crawl-time
                # login/risk result authoritative until the user verifies the
                # product page and retries.
                blocked_after_login = session.scalar(
                    select(CrawlTaskRecord.id)
                    .where(
                        CrawlTaskRecord.account_id == account.id,
                        CrawlTaskRecord.error_code.in_(
                            {
                                "LOGIN_REQUIRED",
                                "RISK_CONTROL",
                                "RISK_CONTROL_PAGE",
                                "CAPTCHA_REQUIRED",
                            }
                        ),
                        CrawlTaskRecord.created_at
                        > (login_session.completed_at or login_session.started_at),
                    )
                    .order_by(CrawlTaskRecord.created_at.desc())
                    .limit(1)
                )
                if blocked_after_login is None:
                    account.login_state = EcommerceLoginState.READY.value
                else:
                    account.login_state = EcommerceLoginState.NEEDS_LOGIN.value
                    session_status["state"] = EcommerceLoginState.NEEDS_LOGIN.value
                    session_status["message"] = (
                        "账号登录会话已建立，但商品页仍要求登录或安全验证；"
                        "请在本机浏览器完成验证后重试。"
                    )
            elif login_session.state in {
                "needs_login",
                "risk_controlled",
                "captcha_required",
            }:
                account.login_state = EcommerceLoginState.NEEDS_LOGIN.value
        else:
            _sync_account_login_state(session, account, session_status)
            _sync_login_session(session, login_session, manager)
        session.commit()
    session_status["loginSession"] = _login_session_response(login_session)
    return {"success": True, "data": session_status}


@router.post("/login-jobs", status_code=201)
def create_login_job(
    request: Request,
    payload: LoginJobCreateRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    """Create the worker-bound login job contract used by remote consoles."""

    account = get_ecommerce_account_for_principal(
        session, principal, payload.account_id, write=True
    )
    if payload.worker_id:
        worker = session.get(EcommerceWorkerRecord, payload.worker_id)
        if (
            worker is None
            or worker.workspace_id != account.workspace_id
            or worker.user_id != principal.user_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORKER_NOT_AVAILABLE",
                    "message": "目标执行设备不可用。",
                },
            )
        account.worker_id = worker.id
        session.commit()
    if not account.worker_id:
        raise ApiError(
            409,
            "WORKER_REQUIRED",
            "远程登录任务必须绑定目标本机执行设备。",
        )
    start_payload = SessionStartRequest(
        account_id=account.id,
        platform=account.platform,  # type: ignore[arg-type]
    )
    result = ecommerce_session_start(request, session, principal, _admin, start_payload)
    data = result.get("data", {})
    login_session = data.get("loginSession")
    if (
        login_session
        and payload.expires_in
        != request.app.state.settings.ecommerce_login_session_timeout_seconds
    ):
        record = session.get(EcommerceLoginSessionRecord, login_session.get("id"))
        if (
            record is not None
            and record.status == EcommerceLoginSessionStatus.ACTIVE.value
        ):
            record.expires_at = utc_now() + timedelta(seconds=payload.expires_in)
            session.commit()
            login_session = _login_session_response(record)
            data["loginSession"] = login_session
    return {"success": True, "data": {"login_job": login_session, **data}}


@router.get("/login-jobs/{login_job_id}")
def get_login_job(
    login_job_id: str,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    record = session.get(EcommerceLoginSessionRecord, login_job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LOGIN_SESSION_NOT_FOUND", "message": "未找到登录任务。"},
        )
    account = get_ecommerce_account_for_principal(session, principal, record.account_id)
    if record.transport != "remote_worker":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOGIN_JOB_NOT_REMOTE",
                "message": "该登录任务不是本机执行任务。",
            },
        )
    _sync_login_session(session, record, _account_manager(request, account))
    session.commit()
    frame = request.app.state.ecommerce_remote_login_frames.get(record.id)
    preview_url = (
        f"/api/ecommerce/session/preview?session_id={record.id}"
        if frame and frame.get("expires_at", utc_now()) > utc_now()
        else None
    )
    return {
        "success": True,
        "data": {**(_login_session_response(record) or {}), "preview_url": preview_url},
    }


@router.post("/login-jobs/{login_job_id}/cancel")
def cancel_login_job(
    login_job_id: str,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict[str, Any]:
    record = session.get(EcommerceLoginSessionRecord, login_job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LOGIN_SESSION_NOT_FOUND", "message": "未找到登录任务。"},
        )
    account = get_ecommerce_account_for_principal(
        session, principal, record.account_id, write=True
    )
    if (
        record.transport != "remote_worker"
        or record.status != EcommerceLoginSessionStatus.ACTIVE.value
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOGIN_SESSION_NOT_ACTIVE",
                "message": "当前登录任务不可取消。",
            },
        )
    now = utc_now()
    record.status = EcommerceLoginSessionStatus.CANCELLED.value
    record.state = "cancelled"
    record.completed_at = now
    record.last_seen_at = now
    request.app.state.ecommerce_remote_login_frames.pop(record.id, None)
    session.add(
        AuditEventRecord(
            workspace_id=account.workspace_id,
            actor_user_id=principal.user_id,
            action="ecommerce.remote_login_cancelled",
            resource_type="ecommerce_account",
            resource_id=account.id,
            details={"worker_id": record.worker_id},
        )
    )
    session.commit()
    return {"success": True, "data": _login_session_response(record)}


@router.post("/session/open-page")
def ecommerce_session_open_page(
    request: Request,
    payload: SessionStartRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict:
    """Open a visible official page for a human-completed challenge."""

    if request.app.state.settings.environment == "production":
        raise HTTPException(
            status_code=403, detail="生产环境请通过受控运维流程打开官方验证页面。"
        )
    if not payload.url:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PLATFORM_URL_REQUIRED",
                "message": "请提供商品链接后再打开官方验证页面。",
                "retryable": False,
            },
        )
    account, manager, account_family = _resolve_account_session_target(
        request,
        session,
        principal,
        account_id=payload.account_id,
        requested_platform=payload.platform,
        write=True,
    )
    if (
        account is not None
        and request.app.state.settings.ecommerce_login_require_worker
        and not account.worker_id
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOCAL_WORKER_REQUIRED",
                "message": (
                    "该环境要求绑定固定的本机执行设备后才能登录。"
                    "请先为账号选择在线本机 Worker。"
                ),
                "retryable": False,
            },
        )
    requested = (
        ("alibaba1688" if account.platform == "1688" else account.platform)
        if account is not None
        else (
            "alibaba1688"
            if payload.platform in {"1688", "alibaba1688"}
            else payload.platform
        )
    )
    if requested == "auto":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PLATFORM_URL_REQUIRED",
                "message": "请明确指定平台或绑定电商账号。",
                "retryable": False,
            },
        )
    try:
        policy = resolve_site_policy(
            payload.url.strip(),
            requested_platform=requested,
            proxy_configured=request.app.state.settings.ecommerce_proxy_enabled,
        )
    except PlatformPolicyError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        ) from exc
    expected = account_family or _ACCOUNT_PLATFORM_TO_FAMILY.get(
        "1688" if requested == "alibaba1688" else requested
    )
    if expected is None or policy.site_family is not expected:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "NETWORK_POLICY_VIOLATION",
                "message": "官方验证页面链接与目标平台不一致。",
                "retryable": False,
            },
        )
    session_status = manager.status()
    if session_status.get("state") != "ready":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SESSION_NOT_READY",
                "message": "当前会话尚未连接，请先完成一次官方登录。",
                "retryable": False,
            },
        )
    result = manager.start_manual_page(payload.url.strip())
    if account is not None:
        _sync_account_login_state(session, account, result)
        session.commit()
    if not result.get("manualPageOpened"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MANUAL_PAGE_ALREADY_OPEN",
                "message": "已有官方窗口正在使用，请先完成或关闭当前验证窗口。",
                "retryable": False,
            },
        )
    return {"success": True, "data": result}


@router.get(
    "/session/preview",
    response_class=FileResponse,
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "image/png": {"schema": {"type": "string", "format": "binary"}}
                }
            }
        }
    },
)
def ecommerce_session_preview(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
    account_id: str | None = Query(default=None, min_length=1, max_length=40),
    session_id: str | None = Query(default=None, min_length=1, max_length=40),
):
    """Return the short-lived, protected QR login preview for one account."""

    _require_login_enabled(request)
    if session_id and not account_id:
        remote_record = session.get(EcommerceLoginSessionRecord, session_id)
        if remote_record is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "LOGIN_SESSION_NOT_FOUND",
                    "message": "未找到登录任务。",
                },
            )
        account_id = remote_record.account_id
    account, manager, _ = _resolve_account_session_target(
        request,
        session,
        principal,
        account_id=account_id,
        requested_platform="auto",
        write=False,
    )
    if account is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LOGIN_ACCOUNT_REQUIRED",
                "message": "登录预览必须绑定到指定电商账号。",
                "retryable": False,
            },
        )
    record = _latest_login_session(session, account.id)
    if session_id and (record is None or record.id != session_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "LOGIN_SESSION_NOT_FOUND", "message": "未找到登录任务。"},
        )
    if record is not None:
        _sync_login_session(session, record, manager)
        session.commit()
    if record is None or record.status != EcommerceLoginSessionStatus.ACTIVE.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOGIN_SESSION_NOT_ACTIVE",
                "message": "当前账号没有进行中的登录流程，请先发起登录。",
                "retryable": False,
            },
        )
    if record.transport == "remote_worker":
        frame = request.app.state.ecommerce_remote_login_frames.get(record.id)
        if (
            frame is None
            or frame.get("expires_at") is None
            or frame["expires_at"] <= utc_now()
        ):
            request.app.state.ecommerce_remote_login_frames.pop(record.id, None)
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "LOGIN_PREVIEW_NOT_READY",
                    "message": "目标本机登录二维码尚未准备好，请稍后刷新。",
                    "retryable": True,
                },
            )
        return Response(
            content=frame["data"],
            media_type=frame.get("content_type", "image/jpeg"),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    if record.transport != "headless_qr":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOGIN_PREVIEW_UNAVAILABLE",
                "message": "当前登录流程不提供二维码预览。",
                "retryable": False,
            },
        )
    status = manager.status()
    preview_path = manager.preview_path
    if not status.get("previewAvailable") or not preview_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LOGIN_PREVIEW_NOT_READY",
                "message": "登录二维码预览尚未准备好，请稍后刷新。",
                "retryable": True,
            },
        )
    return FileResponse(
        preview_path,
        media_type="image/png",
        filename="login-preview.png",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/session/input")
def ecommerce_session_input(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
    payload: SessionInputRequest,
) -> dict[str, Any]:
    """Relay one user input event to the isolated login browser."""

    _require_login_enabled(request)
    account, manager, _ = _resolve_account_session_target(
        request,
        session,
        principal,
        account_id=payload.account_id,
        requested_platform="auto",
        write=False,
    )
    if account is None:
        raise HTTPException(status_code=404, detail="登录会话必须绑定到指定电商账号。")
    active_record = (
        session.get(EcommerceLoginSessionRecord, payload.session_id)
        if payload.session_id
        else _latest_login_session(session, account.id, include_terminal=False)
    )
    if payload.session_id and active_record is None:
        raise HTTPException(status_code=404, detail="未找到登录会话。")
    if active_record is not None and active_record.account_id != account.id:
        raise HTTPException(status_code=404, detail="登录会话与电商账号不匹配。")
    if payload.type == "click" and (payload.x is None or payload.y is None):
        raise HTTPException(status_code=422, detail="点击事件缺少坐标。")
    if payload.type == "drag" and any(
        value is None
        for value in (payload.fromX, payload.fromY, payload.toX, payload.toY)
    ):
        raise HTTPException(status_code=422, detail="拖动事件缺少坐标。")
    if payload.type == "key" and payload.key is None:
        raise HTTPException(status_code=422, detail="键盘事件缺少按键。")
    if payload.type == "text" and payload.text is None:
        raise HTTPException(status_code=422, detail="文本事件缺少内容。")
    action = payload.model_dump(exclude_none=True)
    action.pop("account_id", None)
    action.pop("session_id", None)
    if active_record is not None and active_record.transport == "remote_worker":
        if active_record.worker_id is None:
            raise HTTPException(status_code=409, detail="登录 Worker 未绑定。")
        if (
            active_record.status != EcommerceLoginSessionStatus.ACTIVE.value
            or active_record.state
            not in {
                "claimed",
                "waiting_qr",
                "waiting_user",
                "risk_controlled",
            }
        ):
            raise HTTPException(
                status_code=409, detail="当前没有可操作的登录浏览器会话。"
            )
        actions = request.app.state.ecommerce_remote_login_inputs.setdefault(
            active_record.id, []
        )
        if len(actions) >= 256:
            raise HTTPException(
                status_code=429, detail="登录输入队列繁忙，请稍后重试。"
            )
        actions.append(action)
        return {"success": True, "queued": True}
    status = manager.status()
    login_session = _latest_login_session(session, account.id)
    if (
        login_session is None
        or login_session.status != EcommerceLoginSessionStatus.ACTIVE.value
        or status.get("state") not in {"login_pending", "risk_controlled"}
    ):
        raise HTTPException(status_code=409, detail="当前没有可操作的登录浏览器会话。")
    try:
        manager.enqueue_input(action)
    except RuntimeError as exc:
        # The browser may exit between the status check and the enqueue. Treat
        # that race as a stale session instead of surfacing a generic 500.
        raise HTTPException(
            status_code=409, detail="当前登录浏览器会话已结束。"
        ) from exc
    return {"success": True}


@router.websocket("/session/stream")
async def ecommerce_session_stream(
    websocket: WebSocket,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: Annotated[Principal, Depends(require_admin)],
    account_id: str = Query(min_length=1, max_length=40),
    session_id: str | None = Query(default=None, min_length=1, max_length=40),
) -> None:
    """Stream the isolated login browser and relay authenticated input.

    Frames are published by the helper through a loopback file handoff. The
    endpoint never exposes the profile directory, cookies, or CDP endpoint.
    """

    _require_login_enabled(websocket)  # type: ignore[arg-type]
    account, manager, _ = _resolve_account_session_target(  # type: ignore[arg-type]
        websocket,
        session,
        principal,
        account_id=account_id,
        requested_platform="auto",
        write=False,
    )
    if account is None:
        await websocket.close(code=4404, reason="登录会话必须绑定到指定电商账号。")
        return
    active_record = (
        session.get(EcommerceLoginSessionRecord, session_id)
        if session_id
        else _latest_login_session(session, account.id, include_terminal=False)
    )
    if session_id and active_record is None:
        await websocket.close(code=4404, reason="未找到登录会话。")
        return
    if active_record is not None and active_record.account_id != account.id:
        await websocket.close(code=4404, reason="登录会话与电商账号不匹配。")
        return
    if active_record is not None and active_record.transport == "remote_worker":
        await websocket.close(code=4409, reason="远程登录不开放浏览器控制。")
        return
    record = active_record or _latest_login_session(session, account.id)
    if record is not None:
        _sync_login_session(session, record, manager)
        session.commit()
    if record is None or record.status != EcommerceLoginSessionStatus.ACTIVE.value:
        await websocket.close(code=4409, reason="当前没有进行中的登录流程。")
        return
    if record.transport != "headless_qr":
        await websocket.close(code=4409, reason="当前登录流程不提供实时预览。")
        return

    stream_accounts = websocket.app.state.ecommerce_stream_accounts
    if account.id in stream_accounts:
        await websocket.close(code=4409, reason="该账号已有实时登录控制连接。")
        return
    stream_accounts.add(account.id)

    await websocket.accept()
    send_lock = asyncio.Lock()
    sequence = 0
    last_mtime_ns = 0
    connected_at = asyncio.get_running_loop().time()
    last_input_at = 0.0
    try:

        async def send_frame() -> None:
            nonlocal sequence, last_mtime_ns
            frame_path = (
                manager.stream_path
                if manager.stream_path.is_file()
                else manager.preview_path
            )
            if not frame_path.is_file():
                return
            try:
                stat = frame_path.stat()
                if stat.st_mtime_ns == last_mtime_ns:
                    return
                payload = base64.b64encode(frame_path.read_bytes()).decode("ascii")
            except OSError:
                return
            last_mtime_ns = stat.st_mtime_ns
            sequence += 1
            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "frame",
                        "seq": sequence,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "format": "jpeg"
                        if frame_path == manager.stream_path
                        else "png",
                        "width": 0,
                        "height": 0,
                        "data": payload,
                    }
                )

        async def frame_pump() -> None:
            while True:
                status = manager.status()
                if status.get("state") not in {"login_pending", "risk_controlled"}:
                    with suppress(Exception):
                        await websocket.close(code=4409, reason="登录会话已结束。")
                    return
                await send_frame()
                await asyncio.sleep(0.1)

        pump = asyncio.create_task(frame_pump())
        try:
            while True:
                message = await websocket.receive_text()
                if asyncio.get_running_loop().time() - connected_at > 30 * 60:
                    await websocket.close(
                        code=4408, reason="实时登录连接已达到时长上限。"
                    )
                    return
                if len(message) > 64 * 1024:
                    await websocket.close(code=4400, reason="输入消息过大。")
                    return
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "accepted": False,
                            "reason": "输入消息格式无效。",
                        }
                    )
                    continue
                if not isinstance(payload, dict):
                    continue
                action = (
                    payload.get("action") if payload.get("type") == "input" else payload
                )
                if not isinstance(action, dict) or action.get("type") not in {
                    "click",
                    "drag",
                    "wheel",
                    "key",
                    "text",
                }:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "seq": payload.get("seq"),
                            "accepted": False,
                            "reason": "不支持的输入事件。",
                        }
                    )
                    continue
                action = dict(action)
                action.pop("session_id", None)
                status = manager.status()
                if status.get("state") not in {"login_pending", "risk_controlled"}:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "seq": payload.get("seq"),
                            "accepted": False,
                            "reason": "登录会话已结束。",
                        }
                    )
                    continue
                now = asyncio.get_running_loop().time()
                if now - last_input_at < 0.02:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "seq": payload.get("seq"),
                            "accepted": False,
                            "reason": "输入事件过于频繁。",
                        }
                    )
                    continue
                last_input_at = now
                try:
                    manager.enqueue_input(action)
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "seq": payload.get("seq"),
                            "accepted": True,
                        }
                    )
                except RuntimeError:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "seq": payload.get("seq"),
                            "accepted": False,
                            "reason": "登录会话已结束。",
                        }
                    )
        finally:
            pump.cancel()
            with suppress(asyncio.CancelledError):
                await pump
    except WebSocketDisconnect:
        return
    finally:
        stream_accounts.discard(account.id)


@router.post("/session/cancel")
def ecommerce_session_cancel(
    request: Request,
    payload: SessionCancelRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
) -> dict:
    """Cancel the active human-assisted login flow for one account."""

    _require_login_enabled(request)
    account, manager, _ = _resolve_account_session_target(
        request,
        session,
        principal,
        account_id=payload.account_id,
        requested_platform="auto",
        write=False,
    )
    if account is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LOGIN_ACCOUNT_REQUIRED",
                "message": "取消登录必须绑定到指定电商账号。",
                "retryable": False,
            },
        )
    record = _latest_login_session(session, account.id)
    if record is not None:
        _sync_login_session(session, record, manager)
    if record is None or record.status != EcommerceLoginSessionStatus.ACTIVE.value:
        session.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOGIN_SESSION_NOT_ACTIVE",
                "message": "当前账号没有进行中的登录流程。",
                "retryable": False,
            },
        )
    result: dict[str, Any]
    if record.transport == "remote_worker":
        result = {
            "state": "cancelled",
            "message": "已取消目标本机登录任务。",
        }
        request.app.state.ecommerce_remote_login_frames.pop(record.id, None)
    else:
        result = cast(dict[str, Any], manager.cancel_login())
    now = utc_now()
    record.status = EcommerceLoginSessionStatus.CANCELLED.value
    record.completed_at = now
    record.last_seen_at = now
    record.error_code = None
    record.state = "cancelled"
    _sync_account_login_state(session, account, result)
    session.add(
        AuditEventRecord(
            workspace_id=account.workspace_id,
            actor_user_id=principal.user_id,
            action="ecommerce.login_cancelled",
            resource_type="ecommerce_account",
            resource_id=account.id,
            request_id=request.headers.get("X-Request-Id"),
            details={"transport": record.transport},
        )
    )
    session.commit()
    result["loginSession"] = _login_session_response(record)
    return {"success": True, "data": result}


@router.post("/session/start")
def ecommerce_session_start(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    _admin: AdminDependency,
    payload: SessionStartRequest | None = None,
) -> dict:
    _require_login_enabled(request)
    requested_platform = payload.platform if payload else "auto"
    target_url = payload.url.strip() if payload and payload.url else None
    account_id = payload.account_id if payload else None
    if request.app.state.settings.environment == "production" and not account_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ACCOUNT_REQUIRED",
                "message": "生产环境登录必须绑定到指定电商账号。",
                "retryable": False,
            },
        )
    account, manager, _ = _resolve_account_session_target(
        request,
        session,
        principal,
        account_id=account_id,
        requested_platform=requested_platform,
        write=True,
    )
    login_session: EcommerceLoginSessionRecord | None = None
    # A bound account is always logged in by its designated workstation
    # Worker. The API creates a durable queued job and never opens a server
    # browser or accepts browser-session material.
    if account is not None and account.worker_id:
        from .ecommerce_worker_routes import _worker_state

        # Lock the stable account row: locking only an existing login session
        # does not prevent two tabs from simultaneously creating the first one.
        session.refresh(account, with_for_update=True)
        active_lease = session.scalar(
            select(CrawlProfileLeaseRecord.id)
            .where(
                CrawlProfileLeaseRecord.profile_id == account.profile_id,
                CrawlProfileLeaseRecord.expires_at > utc_now(),
            )
            .limit(1)
        )
        if active_lease is not None:
            raise ApiError(
                409, "ACCOUNT_BUSY", "该账号当前被任务占用，请等待任务完成后再登录。"
            )
        existing_remote = session.scalar(
            select(EcommerceLoginSessionRecord)
            .where(
                EcommerceLoginSessionRecord.account_id == account.id,
                EcommerceLoginSessionRecord.status
                == EcommerceLoginSessionStatus.ACTIVE.value,
            )
            .order_by(EcommerceLoginSessionRecord.started_at.desc())
            .with_for_update()
            .limit(1)
        )
        if (
            existing_remote is not None
            and _as_utc(existing_remote.expires_at) > utc_now()
        ):
            raise ApiError(
                409,
                "LOGIN_SESSION_BUSY",
                "该电商账号已有登录流程进行中，请等待完成或取消。",
            )

        worker = session.get(EcommerceWorkerRecord, account.worker_id)
        timeout = request.app.state.settings.ecommerce_worker_heartbeat_timeout_seconds
        if (
            worker is None
            or worker.workspace_id != account.workspace_id
            or worker.user_id != principal.user_id
            or _worker_state(worker, timeout) != "online"
        ):
            raise ApiError(
                409,
                "LOCAL_WORKER_OFFLINE",
                "绑定的本机执行设备离线或令牌已失效。",
            )
        capability = f"login.{account.platform}"
        if capability not in set(worker.capabilities or []):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LOCAL_WORKER_CAPABILITY_MISSING",
                    "message": "绑定的本机执行设备不支持该平台登录。",
                    "retryable": False,
                },
            )
        now = utc_now()
        login_session = EcommerceLoginSessionRecord(
            id=f"login_{uuid4().hex}",
            account_id=account.id,
            workspace_id=account.workspace_id,
            started_by=principal.user_id,
            status=EcommerceLoginSessionStatus.ACTIVE.value,
            transport="remote_worker",
            started_at=now,
            expires_at=now
            + timedelta(
                seconds=request.app.state.settings.ecommerce_login_session_timeout_seconds
            ),
            last_seen_at=now,
            worker_id=worker.id,
            state="queued",
        )
        session.add(login_session)
        session.add(
            AuditEventRecord(
                workspace_id=account.workspace_id,
                actor_user_id=principal.user_id,
                action="ecommerce.remote_login_started",
                resource_type="ecommerce_account",
                resource_id=account.id,
                request_id=request.headers.get("X-Request-Id"),
                details={"worker_id": worker.id, "transport": "remote_worker"},
            )
        )
        session.commit()
        return {
            "success": True,
            "data": {
                "state": "queued",
                "message": "登录任务已发送到目标本机执行设备，等待二维码。",
                "previewAvailable": False,
                "loginSession": _login_session_response(login_session),
            },
        }
    transport = request.app.state.settings.ecommerce_login_transport
    if (
        request.app.state.settings.environment == "production"
        and transport != "headless_qr"
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LOGIN_TRANSPORT_UNAVAILABLE",
                "message": "生产环境尚未配置受控二维码登录通道。",
                "retryable": False,
            },
        )
    if account is not None:
        active_session = _latest_login_session(session, account.id)
        if active_session is not None:
            _sync_login_session(session, active_session, manager)
            if active_session.status == EcommerceLoginSessionStatus.ACTIVE.value:
                session.commit()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "LOGIN_SESSION_BUSY",
                        "message": "该电商账号已有登录流程进行中，请等待完成或取消。",
                        "retryable": True,
                    },
                )
    # Start authorization at the official login origin for platform-bound
    # accounts. The product URL is only a crawl target.
    if requested_platform in {"1688", "alibaba1688", "jd", "pinduoduo"}:
        target_url = None
    if account is None and target_url:
        try:
            policy = resolve_site_policy(
                target_url,
                requested_platform=(
                    "alibaba1688"
                    if requested_platform == "1688"
                    else requested_platform
                ),
                proxy_configured=request.app.state.settings.ecommerce_proxy_enabled,
            )
        except PlatformPolicyError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": exc.message, "retryable": False},
            ) from exc
        manager = _session_manager_for_family(request, policy.site_family)
    manager_status = manager.status()
    # Anonymous/session convenience calls may reuse a validated profile, but
    # an account-scoped admin click explicitly asks to open the login flow
    # again (the UI labels it “重新登录” when appropriate).
    if manager_status.get("state") == "ready" and account is None:
        manager_status["loginSession"] = None
        return {"success": True, "data": manager_status}

    if account is not None:
        now = utc_now()
        login_session = EcommerceLoginSessionRecord(
            id=f"login_{uuid4().hex}",
            account_id=account.id,
            workspace_id=account.workspace_id,
            started_by=principal.user_id,
            status=EcommerceLoginSessionStatus.ACTIVE.value,
            transport=transport,
            started_at=now,
            expires_at=now
            + timedelta(
                seconds=request.app.state.settings.ecommerce_login_session_timeout_seconds
            ),
            last_seen_at=now,
        )
        session.add(login_session)
        session.add(
            AuditEventRecord(
                workspace_id=account.workspace_id,
                actor_user_id=principal.user_id,
                action="ecommerce.login_started",
                resource_type="ecommerce_account",
                resource_id=account.id,
                request_id=request.headers.get("X-Request-Id"),
                details={"transport": transport},
            )
        )
        session.commit()
    try:
        result = manager.start_login(
            target_url,
            headless=transport == "headless_qr",
            timeout_seconds=(
                request.app.state.settings.ecommerce_login_session_timeout_seconds
            ),
            # An account-scoped login is an explicit admin request, including
            # the “重新登录” action. Revalidate the browser profile instead of
            # returning a possibly stale persisted ``ready`` status.
            force=account is not None,
        )
    except Exception as exc:
        if login_session is not None:
            login_session.status = EcommerceLoginSessionStatus.FAILED.value
            login_session.error_code = "LOGIN_HELPER_START_FAILED"
            login_session.completed_at = utc_now()
            session.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LOGIN_HELPER_UNAVAILABLE",
                "message": "受控登录浏览器暂时不可用，请稍后重试。",
                "retryable": True,
            },
        ) from exc
    if account is not None:
        _sync_account_login_state(session, account, result)
        _sync_login_session(session, login_session, manager)
        session.commit()
    result["loginSession"] = _login_session_response(login_session)
    return {"success": True, "data": result}
