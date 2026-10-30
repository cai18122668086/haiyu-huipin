import asyncio
import hmac
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine, text
from sqlalchemy.exc import TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import Session, sessionmaker

from .admin_account_routes import router as admin_account_router
from .admin_billing_routes import router as admin_billing_router
from .admin_dashboard_routes import router as admin_dashboard_router
from .admin_log_routes import router as admin_log_router
from .admin_management_routes import router as admin_management_router
from .admin_usage_routes import router as admin_usage_router
from .announcement_routes import router as announcement_router
from .auth import ensure_development_identity
from .brand_resume_routes import router as brand_resume_router
from .config import Settings
from .creation_summary import router as creation_summary_router
from .credit_payment_routes import router as credit_payment_router
from .credit_refund_routes import router as credit_refund_router
from .credit_routes import router as credit_router
from .database import Base, build_database
from .ecommerce_account_routes import router as ecommerce_account_router
from .ecommerce_crawl_tasks import (
    recover_interrupted_crawl_tasks,
    resume_queued_crawl_tasks,
)
from .ecommerce_crawl_tasks import (
    router as ecommerce_crawl_tasks_router,
)
from .ecommerce_health import (
    check_object_storage,
    check_task_broker,
    collect_crawl_metrics,
)
from .ecommerce_routes import router as ecommerce_router
from .ecommerce_session import EcommerceSessionManager
from .ecommerce_worker_routes import router as ecommerce_worker_router
from .errors import ApiError
from .inspiration_routes import router as inspiration_router
from .legacy_detail_proxy import LegacyDetailProxy
from .models import WorkspaceLimitRecord
from .replica import router as replica_router
from .routes_v1 import router as v1_router
from .schemas_v1 import HealthResponse
from .services import (
    set_credit_enforcement_enabled,
    set_credit_pilot_workspaces,
    set_quota_enabled,
)
from .storage import (
    ImageStorage,
    LocalImageStorage,
    build_image_storage,
)
from .supertokens_auth import ExternalAuthBackend, SuperTokensAuthAdapter
from .support_routes import router as support_router
from .task_runner import build_task_runner, validate_task_runner_settings
from .thumbnails import ThumbnailCache

logger = logging.getLogger(__name__)

_METRICS_MAX_ROUTE_KEYS = 200
_METRICS_BUCKETS_SECONDS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, math.inf)


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _metrics_text(application: FastAPI) -> str:
    metrics = application.state.request_metrics
    lines = [
        "# HELP polaris_http_requests_total Total HTTP requests by route "
        "and status class.",
        "# TYPE polaris_http_requests_total counter",
    ]
    for key, value in sorted(metrics["requests"].items()):
        method, route, status_class = key
        lines.append(
            f'polaris_http_requests_total{{method="{_prometheus_label(method)}",'
            f'route="{_prometheus_label(route)}",'
            f'status_class="{_prometheus_label(status_class)}"}} {value}'
        )
    lines.extend(
        [
            "# HELP polaris_http_request_duration_seconds HTTP request duration.",
            "# TYPE polaris_http_request_duration_seconds histogram",
        ]
    )
    for key, values in sorted(metrics["latency"].items()):
        method, route = key
        labels = (
            f'method="{_prometheus_label(method)}",route="{_prometheus_label(route)}"'
        )
        cumulative = 0
        for bucket, count in zip(
            _METRICS_BUCKETS_SECONDS, values["buckets"], strict=True
        ):
            cumulative += count
            boundary = "+Inf" if math.isinf(bucket) else f"{bucket:g}"
            lines.append(
                "polaris_http_request_duration_seconds_bucket{"
                f'{labels},le="{boundary}"}} {cumulative}'
            )
        lines.append(
            f"polaris_http_request_duration_seconds_sum{{{labels}}} {values['sum']:.6f}"
        )
        lines.append(
            f"polaris_http_request_duration_seconds_count{{{labels}}} {values['count']}"
        )
    lines.extend(
        [
            "# HELP polaris_http_requests_in_flight Current in-flight requests.",
            "# TYPE polaris_http_requests_in_flight gauge",
            f"polaris_http_requests_in_flight {metrics['in_flight']}",
        ]
    )
    pool = application.state.engine.pool
    pool_size = getattr(pool, "size", lambda: 0)()
    checked_out = getattr(pool, "checkedout", lambda: 0)()
    overflow = max(0, int(getattr(pool, "overflow", lambda: 0)()))
    available = max(0, int(pool_size) + overflow - int(checked_out))
    lines.extend(
        [
            "# HELP polaris_database_pool_size Configured SQLAlchemy pool size.",
            "# TYPE polaris_database_pool_size gauge",
            f"polaris_database_pool_size {pool_size}",
            "# HELP polaris_database_pool_checked_out Current checked-out connections.",
            "# TYPE polaris_database_pool_checked_out gauge",
            f"polaris_database_pool_checked_out {checked_out}",
            "# HELP polaris_database_pool_overflow Current overflow connections.",
            "# TYPE polaris_database_pool_overflow gauge",
            f"polaris_database_pool_overflow {overflow}",
            "# HELP polaris_database_pool_available Approximate idle pool capacity.",
            "# TYPE polaris_database_pool_available gauge",
            f"polaris_database_pool_available {available}",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_database_for_settings(
    app_settings: Settings,
) -> tuple[Engine, sessionmaker[Session]]:
    """Build a configured engine while keeping simple test doubles compatible."""

    try:
        return build_database(
            app_settings.database_url,
            pool_size=app_settings.database_pool_size,
            max_overflow=app_settings.database_max_overflow,
            pool_timeout=app_settings.database_pool_timeout_seconds,
            pool_recycle=app_settings.database_pool_recycle_seconds,
        )
    except TypeError as exc:
        # A few integration tests replace build_database with a one-argument
        # factory. Only fall back for that explicit signature mismatch; real
        # engine construction errors must still propagate.
        if "unexpected keyword argument" not in str(exc):
            raise
        return build_database(app_settings.database_url)


def _serializable_validation_errors(
    exc: RequestValidationError,
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for item in exc.errors():
        error = dict(item)
        context = error.get("ctx")
        if isinstance(context, dict):
            error["ctx"] = {str(key): str(value) for key, value in context.items()}
        errors.append(error)
    return errors


def create_app(
    settings: Settings | None = None,
    external_auth_backend: ExternalAuthBackend | None = None,
) -> FastAPI:
    app_settings = settings or Settings()
    app_settings.validate_runtime_safety()
    validate_task_runner_settings(app_settings)
    set_quota_enabled(app_settings.provider_quota_enabled)
    set_credit_enforcement_enabled(app_settings.workspace_credit_enforcement_enabled)
    set_credit_pilot_workspaces(app_settings.workspace_credit_pilot_ids)
    auth_backend = external_auth_backend
    if app_settings.auth_mode == "external":
        auth_backend = auth_backend or SuperTokensAuthAdapter(app_settings)
        auth_backend.initialize()
    engine, session_factory = _build_database_for_settings(app_settings)
    storage: ImageStorage = build_image_storage(app_settings)
    validator = storage.validator
    task_runner = build_task_runner(session_factory, app_settings, storage)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if isinstance(storage, LocalImageStorage):
            storage.root.mkdir(parents=True, exist_ok=True)
        if app_settings.auto_create_schema:
            Base.metadata.create_all(engine)
        ensure_development_identity(application)
        if app_settings.ecommerce_startup_recovery_enabled:
            recover_interrupted_crawl_tasks(session_factory)
            # Celery maintenance owns queued-message recovery. The auxiliary
            # detail API disables this entire crawler startup path.
            if app_settings.task_dispatcher == "inline":
                resume_queued_crawl_tasks(application, session_factory)
        from .credit_memberships import maintenance_loop

        membership_task = asyncio.create_task(
            maintenance_loop(session_factory, app_settings)
        )
        reconciliation_task = None
        if app_settings.credit_payment_reconciliation_enabled:
            from .credit_payment_reconciliation import maintenance_loop as query_loop

            reconciliation_task = asyncio.create_task(
                query_loop(session_factory, app_settings)
            )
        try:
            yield
        finally:
            if reconciliation_task is not None:
                reconciliation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconciliation_task
            membership_task.cancel()
            with suppress(asyncio.CancelledError):
                await membership_task
            from .async_provider_transport import close_async_provider_session

            await close_async_provider_session()
            engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        description=("极光商绘 API v1。当前执行器只模拟状态，不会调用图片生成服务。"),
        lifespan=lifespan,
    )
    from .phone_accounts import router as phone_auth_router

    application.include_router(phone_auth_router)
    application.state.settings = app_settings
    application.state.engine = engine
    application.state.request_metrics = {
        "requests": {},
        "latency": {},
        "in_flight": 0,
    }
    application.state.session_factory = session_factory
    application.state.storage = storage
    application.state.thumbnails = ThumbnailCache()
    application.state.image_validator = validator
    application.state.task_runner = task_runner
    application.state.ecommerce_crawl_handles = set()
    # Lazily created account-specific managers are keyed by account id and
    # use the durable profile path owned by that account.
    application.state.ecommerce_account_sessions = {}
    application.state.ecommerce_stream_accounts = set()
    # Ephemeral remote-worker login frames. Entries are byte payloads with an
    # expiry timestamp and are never written to object storage or logs.
    application.state.ecommerce_remote_login_frames = {}
    # Short-lived user input actions for remote Worker login sessions. Actions
    # are consumed by the owning Worker and never persisted with credentials.
    application.state.ecommerce_remote_login_inputs = {}
    # Keep the historical Taobao manager alias for existing callers, while
    # registering an independent 1688 manager and profile. Never share a
    # persistent browser profile across platform policies.
    application.state.ecommerce_session = EcommerceSessionManager(
        app_settings.ecommerce_session_dir,
        platform="taobao",
        session_profile="domestic-taobao-tmall",
        state_style="legacy",
    )
    application.state.ecommerce_session_1688 = EcommerceSessionManager(
        app_settings.ecommerce_1688_session_dir,
        platform="alibaba1688",
        session_profile="domestic-1688",
        state_style="canonical",
    )
    application.state.ecommerce_session_jd = EcommerceSessionManager(
        app_settings.ecommerce_jd_session_dir,
        platform="jd",
        session_profile="domestic-jd",
        state_style="canonical",
    )
    application.state.ecommerce_session_pinduoduo = EcommerceSessionManager(
        app_settings.ecommerce_pinduoduo_session_dir,
        platform="pinduoduo",
        session_profile="domestic-pinduoduo",
        state_style="canonical",
    )
    application.state.ecommerce_sessions = {
        "taobao": application.state.ecommerce_session,
        "1688": application.state.ecommerce_session_1688,
        "jd": application.state.ecommerce_session_jd,
        "pinduoduo": application.state.ecommerce_session_pinduoduo,
    }
    application.state.external_auth_backend = auth_backend
    application.state.workspace_limits = lambda session, workspace_id: session.get(
        WorkspaceLimitRecord, workspace_id
    )

    if app_settings.legacy_detail_api_url:
        application.add_middleware(
            LegacyDetailProxy,
            origin=app_settings.legacy_detail_api_url,
            session_factory=session_factory,
            native_creation_enabled=app_settings.domestic_detail_native_creation_enabled,
        )

    auth_cors_headers: list[str] = []
    if auth_backend is not None:
        auth_backend.install_middleware(application)
        auth_cors_headers = auth_backend.cors_headers()
    allowed_headers = [
        "Content-Type",
        "Idempotency-Key",
        "X-Request-Id",
    ]
    if app_settings.auth_mode == "development":
        allowed_headers.append("X-User-Id")
    for header in auth_cors_headers:
        if header not in allowed_headers:
            allowed_headers.append(header)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
        allow_headers=allowed_headers,
        expose_headers=auth_cors_headers,
    )

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        started = monotonic()
        application.state.request_metrics["in_flight"] += 1
        response: Response | None = None
        try:
            response = await call_next(request)
        finally:
            elapsed = monotonic() - started
            application.state.request_metrics["in_flight"] -= 1
            if request.url.path != "/health/metrics":
                route = request.scope.get("route")
                route_name = str(getattr(route, "path", request.url.path))
                requests = application.state.request_metrics["requests"]
                latency = application.state.request_metrics["latency"]
                if len(requests) < _METRICS_MAX_ROUTE_KEYS or any(
                    key[0:2] == (request.method, route_name) for key in requests
                ):
                    status_code = response.status_code if response is not None else 500
                    request_key = (
                        request.method,
                        route_name,
                        f"{status_code // 100}xx",
                    )
                    requests[request_key] = requests.get(request_key, 0) + 1
                    latency_key = (request.method, route_name)
                    values = latency.setdefault(
                        latency_key,
                        {
                            "buckets": [0] * len(_METRICS_BUCKETS_SECONDS),
                            "sum": 0.0,
                            "count": 0,
                        },
                    )
                    values["sum"] += elapsed
                    values["count"] += 1
                    for index, boundary in enumerate(_METRICS_BUCKETS_SECONDS):
                        if elapsed <= boundary:
                            values["buckets"][index] += 1
                            break
        response.headers["X-Request-Id"] = request_id
        if elapsed >= 2:
            logger.warning(
                "slow_request method=%s path=%s status=%s seconds=%.3f",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
            )
        return response

    @application.exception_handler(DatabasePoolTimeout)
    async def handle_pool_timeout(
        request: Request, exc: DatabasePoolTimeout
    ) -> JSONResponse:
        logger.warning("database_pool_busy path=%s", request.url.path)
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "3"},
            content={
                "error": {
                    "code": "DATABASE_BUSY",
                    "message": "服务暂时繁忙，请稍后重试。",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数校验失败。",
                    "details": _serializable_validation_errors(exc),
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.get(
        "/health/live",
        response_model=HealthResponse,
        tags=["system"],
    )
    def liveness() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=app_settings.app_name,
            version=app_settings.app_version,
            checks={"process": "ok"},
        )

    @application.get(
        "/health/ready",
        response_model=HealthResponse,
        tags=["system"],
    )
    def readiness(response: Response) -> HealthResponse:
        checks = {"database": "ok"}
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            checks["database"] = "failed"
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        if app_settings.task_dispatcher == "celery":
            checks["task_broker"] = check_task_broker(app_settings.celery_broker_url)
            if checks["task_broker"] != "ok":
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        if app_settings.storage_backend == "s3":
            checks["object_storage"] = check_object_storage(storage)
            if checks["object_storage"] != "ok":
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        if app_settings.auth_mode == "external":
            authentication_ready = bool(auth_backend and auth_backend.is_ready())
            checks["authentication"] = "ok" if authentication_ready else "failed"
            if not authentication_ready:
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status=(
                "ok" if all(value == "ok" for value in checks.values()) else "degraded"
            ),
            service=app_settings.app_name,
            version=app_settings.app_version,
            checks=checks,
        )

    @application.get(
        "/health/metrics",
        include_in_schema=False,
        response_class=Response,
        tags=["system"],
    )
    def metrics(request: Request) -> Response:
        configured_token = app_settings.metrics_token.get_secret_value()
        supplied_token = request.headers.get("X-Metrics-Token", "")
        if not configured_token or not hmac.compare_digest(
            supplied_token, configured_token
        ):
            # Keep the endpoint indistinguishable from a disabled route when
            # an operator has not provisioned a scrape token.
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        return Response(
            content=_metrics_text(application),
            media_type="text/plain; version=0.0.4",
        )

    @application.get("/health/ecommerce", tags=["system"])
    def ecommerce_health(response: Response) -> dict[str, object]:
        try:
            metrics = collect_crawl_metrics(session_factory)
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "degraded", "metrics": {}}
        return {"status": "ok", "metrics": metrics}

    application.include_router(v1_router)
    application.include_router(admin_billing_router)
    application.include_router(credit_refund_router)
    application.include_router(credit_router)
    application.include_router(creation_summary_router)
    application.include_router(support_router)
    application.include_router(credit_payment_router)
    application.include_router(brand_resume_router)
    application.include_router(announcement_router)
    application.include_router(admin_dashboard_router)
    application.include_router(admin_account_router)
    application.include_router(admin_usage_router)
    application.include_router(admin_log_router)
    application.include_router(admin_management_router)
    application.include_router(inspiration_router)
    application.include_router(ecommerce_router)
    application.include_router(replica_router)
    application.include_router(ecommerce_account_router)
    application.include_router(ecommerce_crawl_tasks_router)
    application.include_router(ecommerce_worker_router)
    return application


app = create_app()
