"""Native async HTTP for long provider calls; no implicit POST retries."""

import asyncio
import json
from typing import Any
from weakref import WeakKeyDictionary

import aiohttp

from .xinghe_agent import UrllibOpenAITransport, XingheAgentError, _safe_request_id

_sessions: WeakKeyDictionary[asyncio.AbstractEventLoop, aiohttp.ClientSession] = (
    WeakKeyDictionary()
)


async def close_async_provider_session() -> None:
    session = _sessions.pop(asyncio.get_running_loop(), None)
    if session is not None:
        await session.close()


def _session() -> aiohttp.ClientSession:
    loop = asyncio.get_running_loop()
    session = _sessions.get(loop)
    if session is None or session.closed:
        # Admission belongs to the shared limiter. Avoid aiohttp's default
        # 100-connection cap and reuse TLS connections for async image polls.
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=0, keepalive_timeout=30),
            trust_env=True,
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        _sessions[loop] = session
    return session


class AsyncProviderTransport(UrllibOpenAITransport):
    # Inherit wire encoders and error parsing. Override only their blocking send.
    async def post_json(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        headers = {**kwargs["headers"], "Content-Type": "application/json"}
        return await self._request(
            "POST",
            kwargs["url"],
            headers,
            json.dumps(kwargs["payload"], ensure_ascii=False).encode("utf8"),
            kwargs["timeout_seconds"],
        )

    async def post_multipart(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        # Keep the exact existing repeated image fields and native MIME format.
        import secrets

        from .xinghe_agent import _encode_multipart

        boundary = "polaris-" + secrets.token_hex(16)
        body = _encode_multipart(boundary, kwargs["fields"], kwargs["files"])
        headers = {
            **kwargs["headers"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        return await self._request(
            "POST",
            kwargs["url"],
            headers,
            body,
            kwargs["timeout_seconds"],
        )

    async def get_json(
        self,
        url: str,
        headers: Any,
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        return await self._request("GET", url, headers, None, timeout)

    @staticmethod
    async def _request(
        method: str,
        url: str,
        headers: Any,
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        # Credentials are request-scoped and cookies are disabled. Redirects
        # cannot replay a native provider POST against another endpoint.
        try:
            async with _session().request(
                method,
                url,
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                status = response.status
                raw = await response.read()
                response_headers = response.headers
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise XingheAgentError(
                "PROVIDER_TRANSPORT_FAILED",
                f"Provider transport failed ({type(exc).__name__}).",
                retryable=True,
            ) from exc
        from .provider_diagnostics import safe_text

        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            if status < 300:
                raise XingheAgentError(
                    "PROVIDER_RESPONSE_INVALID",
                    "Provider returned invalid JSON.",
                ) from exc
            payload = {
                "error": {
                    "code": "non_json_error_response",
                    "message": safe_text(raw.decode("utf8", "replace")),
                }
            }
        if not isinstance(payload, dict):
            raise XingheAgentError(
                "PROVIDER_RESPONSE_INVALID",
                "Provider returned non-object JSON.",
            )
        if 300 <= status < 400:
            raise XingheAgentError(
                "PROVIDER_RESPONSE_INVALID",
                "Provider redirect refused.",
            )
        request_id = response_headers.get("x-request-id") or response_headers.get(
            "request-id"
        )
        if request_id and not _safe_request_id(payload):
            payload["_transport_request_id"] = safe_text(request_id, 255)
            payload.setdefault("request_id", payload["_transport_request_id"])
        if "Retry-After" in response_headers:
            payload["_transport_retry_after"] = response_headers["Retry-After"][:128]
        return status, payload
