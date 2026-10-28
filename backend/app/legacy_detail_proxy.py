"""Keep historical domestic-detail HTTP operations on their frozen API."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

import httpx
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .models import JobRecord

_ALLOWED_ORIGINS = {"http://api:8000", "http://legacy-api:8000"}
_COLLECTION_PATH = re.compile(r"^/api/v1/workspaces/([^/]+)/jobs$")
_JOB_PATH = re.compile(r"^/api/v1/workspaces/([^/]+)/jobs/([^/]+)(?:/.*)?$")
_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
_HOP = b"x-polaris-legacy-detail-hop"


def _headers(
    raw: list[tuple[bytes, bytes]], *, outgoing_request: bool
) -> list[tuple[bytes, bytes]]:
    omitted = set(_HOP_HEADERS)
    if outgoing_request:
        omitted.add(b"content-length")
    for name, value in raw:
        if name.lower() == b"connection":
            omitted.update(part.strip().lower() for part in value.split(b","))
    return [(name, value) for name, value in raw if name.lower() not in omitted]


def _historical_job(
    factory: sessionmaker[Session], workspace: str, job_id: str
) -> bool:
    with factory() as session:
        job = session.get(JobRecord, job_id)
        if job is None or job.workspace_id != workspace or job.type != "detail_pack":
            return False
        configuration = job.configuration
        settings = configuration.get("settings")
        return (
            configuration.get("domestic_detail_pipeline") is None
            and isinstance(settings, Mapping)
            and settings.get("market_region") == "domestic"
        )


def upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(60, connect=5),
    )


class LegacyDetailProxy:
    def __init__(
        self,
        app: ASGIApp,
        *,
        origin: str,
        session_factory: sessionmaker[Session],
        native_creation_enabled: bool = True,
    ) -> None:
        if origin not in _ALLOWED_ORIGINS:
            raise ValueError(
                "Legacy detail origin must be a fixed internal API service"
            )
        self.app = app
        self.origin = origin
        self.session_factory = session_factory
        self.native_creation_enabled = native_creation_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        match = (
            _JOB_PATH.fullmatch(scope.get("path", ""))
            if scope["type"] == "http"
            else None
        )
        historical = match is not None and await run_in_threadpool(
            _historical_job,
            self.session_factory,
            match[1],
            match[2],
        )
        body = None
        creation_rollback = False
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and not self.native_creation_enabled
            and _COLLECTION_PATH.fullmatch(scope.get("path", ""))
        ):
            body = await Request(scope, receive).body()
            try:
                value = json.loads(body)
            except (ValueError, UnicodeError):
                value = None
            configuration = (
                value.get("configuration") if isinstance(value, dict) else None
            )
            creation_rollback = (
                isinstance(value, dict)
                and value.get("type") == "detail_pack"
                and isinstance(configuration, dict)
                and configuration.get("market_region") == "domestic"
            )
            if (
                creation_rollback
                and isinstance(configuration, dict)
                and configuration.get("detail_skill") == "refined"
            ):
                await JSONResponse(
                    {
                        "error": {
                            "code": "REFINED_DETAIL_SERVICE_UNAVAILABLE",
                            "message": "精美详情服务暂不可用，请稍后再试。",
                        }
                    },
                    status_code=503,
                )(scope, receive, send)
                return
        if not historical and not creation_rollback:
            if body is not None:
                original_receive = receive
                replayed = False

                async def receive_body() -> Message:
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {
                            "type": "http.request",
                            "body": body,
                            "more_body": False,
                        }
                    return await original_receive()

                receive = receive_body
            await self.app(scope, receive, send)
            return
        if any(name.lower() == _HOP for name, _ in scope.get("headers", [])):
            await JSONResponse(
                {
                    "error": {
                        "code": "DOMESTIC_DETAIL_LEGACY_API_LOOP",
                        "message": "Historical runtime routing is unavailable.",
                    }
                },
                status_code=502,
            )(scope, receive, send)
            return
        if body is None:
            body = await Request(scope, receive).body()
        raw_path = scope.get("raw_path", scope["path"].encode("utf-8"))
        query = scope.get("query_string", b"")
        url = (
            self.origin
            + raw_path.decode("ascii")
            + ("?" + query.decode("ascii") if query else "")
        )
        headers = _headers(scope.get("headers", []), outgoing_request=True)
        headers.append((_HOP, b"1"))
        started = False
        try:
            async with (
                upstream_client() as client,
                client.stream(
                    scope["method"],
                    url,
                    headers=httpx.Headers(headers),
                    content=body,
                ) as response,
            ):
                await send(
                    {
                        "type": "http.response.start",
                        "status": response.status_code,
                        "headers": _headers(
                            response.headers.raw, outgoing_request=False
                        ),
                    }
                )
                started = True
                async for chunk in response.aiter_raw():
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
        except httpx.RequestError:
            if started:
                raise
            await JSONResponse(
                {
                    "error": {
                        "code": "DOMESTIC_DETAIL_LEGACY_API_UNAVAILABLE",
                        "message": (
                            "Historical runtime unavailable; preserve this request's "
                            "idempotency key while reconciling its original result."
                        ),
                        "details": {
                            "original_result": "unknown",
                            "same_idempotency_key_required": True,
                        },
                    }
                },
                status_code=502,
            )(scope, receive, send)
