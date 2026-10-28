"""Retry only TLS setup; never replay a paid HTTP request or a redirect."""

from __future__ import annotations

import http.client
import logging
import ssl
import time
from collections.abc import Buffer, Mapping
from typing import Any
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

logger = logging.getLogger(__name__)
MAX_CONNECT_ATTEMPTS = 5
CONNECT_BUDGET_SECONDS = 30.0
CONNECT_ATTEMPT_SECONDS = 10.0


class PreconnectedHTTPSConnection(http.client.HTTPSConnection):
    def request(
        self,
        method: str,
        url: str,
        body: Any = None,
        headers: Mapping[str, Buffer | str | int] | None = None,
        *,
        encode_chunked: bool = False,
    ) -> None:
        # Establish TLS while HTTP state is still IDLE. A rejected proxy
        # CONNECT may call close(); it cannot reset a started provider POST.
        if self.sock is None:
            self.connect()
        super().request(
            method,
            url,
            body,
            {} if headers is None else headers,
            encode_chunked=encode_chunked,
        )

    def connect(self) -> None:
        # http.client calls connect before sending the first provider HTTP
        # header byte. Proxy CONNECT and TLS setup carry no provider body.
        read_timeout = self.timeout
        numeric_timeout = (
            float(read_timeout) if isinstance(read_timeout, (int, float)) else 30.0
        )
        deadline = time.monotonic() + min(CONNECT_BUDGET_SECONDS, numeric_timeout)
        try:
            for attempt in range(MAX_CONNECT_ATTEMPTS):
                self.timeout = min(
                    CONNECT_ATTEMPT_SECONDS,
                    max(0.001, deadline - time.monotonic()),
                )
                try:
                    super().connect()
                except ssl.SSLCertVerificationError:
                    self.close()
                    raise
                except OSError as exc:
                    self.close()
                    if (
                        attempt + 1 == MAX_CONNECT_ATTEMPTS
                        or time.monotonic() >= deadline
                    ):
                        raise
                    logger.info(
                        "native pre-request TLS retry attempt=%s cause=%s",
                        attempt + 1,
                        type(exc).__name__,
                    )
                    continue
                if self.sock is not None:
                    self.sock.settimeout(
                        numeric_timeout
                        if isinstance(read_timeout, (int, float))
                        else None
                    )
                return
        finally:
            self.timeout = read_timeout


class PreconnectedHTTPSHandler(HTTPSHandler):
    _context: ssl.SSLContext

    def https_open(self, req: Request) -> Any:
        return self.do_open(PreconnectedHTTPSConnection, req, context=self._context)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def native_urlopen(request: Request, timeout: float) -> Any:
    # ProxyHandler retains the configured CONNECT proxy. HTTPSHandler creates
    # the standard verifying SSL context; no certificate policy is weakened.
    return build_opener(PreconnectedHTTPSHandler(), NoRedirect()).open(
        request, timeout=timeout
    )
