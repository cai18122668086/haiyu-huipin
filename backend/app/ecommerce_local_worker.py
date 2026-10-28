"""Local-only runtime primitives for the outbound ecommerce worker.

This module is intentionally transport- and browser-client-agnostic.  It owns
local profile paths and locks, while :class:`WorkerApiClient` sends only the
structured worker protocol messages.  No profile contents, cookies, local
storage, or CDP endpoint are ever serialized by this module.
"""

from __future__ import annotations

import argparse
import base64
import errno
import json
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SENSITIVE_KEYS = {
    "cookie",
    "cookies",
    "localstorage",
    "profile",
    "profilepath",
    "cdp",
    "debuggingport",
}
_DEFAULT_CAPABILITIES = (
    "browser.taobao",
    "browser.tmall",
    "browser.1688",
    "browser.jd",
    "browser.pinduoduo",
)


def _normalise_windows_resolved_path(path: Path) -> Path:
    """Keep resolved paths comparable when Windows returns an extended prefix.

    ``Path.resolve()`` can return ``//?/C:/...`` while another thread creates
    a missing parent directory.  The prefix is equivalent to the regular
    ``C:/...`` spelling, but ``Path.is_relative_to`` treats the two spellings
    as unrelated.  Strip only that presentation prefix after resolution; the
    symlink-aware resolution and containment check remain intact.
    """

    value = str(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("//?/UNC/"):
        value = "//" + value[8:]
    elif value.startswith("\\\\?\\") or value.startswith("//?/"):
        value = value[4:]
    return Path(value)


class LocalWorkerError(RuntimeError):
    """A safe, non-secret local worker error."""


class LocalWorkerTaskError(LocalWorkerError):
    """A safe task outcome that can be reported to the control plane."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(code)


@dataclass(frozen=True)
class LocalWorkerConfig:
    """Non-secret runtime configuration loaded from the local environment."""

    api_url: str
    session_token: str
    recovery_token: str
    enrollment_token: str
    session_token_file: Path
    recovery_token_file: Path
    worker_id: str
    profile_root: Path
    worker_id_file: Path | None = None
    capabilities: tuple[str, ...] = _DEFAULT_CAPABILITIES
    request_timeout_seconds: float = 20.0
    heartbeat_interval_seconds: float = 20.0
    poll_interval_seconds: float = 2.0
    concurrency: int = 1
    lease_renew_interval_seconds: float = 30.0
    browser_timeout_ms: int = 30_000
    # Keep this below the server's minimum 60-second token TTL so a deployment
    # using the shortest allowed lifetime still refreshes before expiry.
    token_refresh_interval_seconds: float = 30.0
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8765
    bridge_origin: str = ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LocalWorkerConfig:
        values = os.environ if environ is None else environ
        api_url = values.get("POLARIS_WORKER_API_URL", "").strip()
        session_token = values.get("POLARIS_WORKER_SESSION_TOKEN", "").strip()
        recovery_token = values.get("POLARIS_WORKER_RECOVERY_TOKEN", "").strip()
        enrollment_token = values.get("POLARIS_WORKER_ENROLLMENT_TOKEN", "").strip()
        worker_id = values.get("POLARIS_WORKER_ID", "").strip()
        device_id = values.get("POLARIS_WORKER_DEVICE_ID", "").strip()
        profile_root = values.get("POLARIS_WORKER_PROFILE_ROOT", "").strip()
        if not api_url or not profile_root:
            raise LocalWorkerError("WORKER_CONFIG_REQUIRED")
        token_file_value = values.get("POLARIS_WORKER_SESSION_TOKEN_FILE", "").strip()
        token_file = (
            Path(token_file_value).expanduser()
            if token_file_value
            else Path(profile_root).expanduser() / ".worker-session-token"
        )
        recovery_token_file_value = values.get(
            "POLARIS_WORKER_RECOVERY_TOKEN_FILE", ""
        ).strip()
        recovery_token_file = (
            Path(recovery_token_file_value).expanduser()
            if recovery_token_file_value
            else token_file.with_name(".worker-recovery-token")
        )
        worker_id_file_value = values.get("POLARIS_WORKER_ID_FILE", "").strip()
        worker_id_file = (
            Path(worker_id_file_value).expanduser()
            if worker_id_file_value
            else token_file.with_name(".worker-id")
        )
        # Once a refresh has been persisted, the file is authoritative over
        # the bootstrap value in the env script. This lets a reboot continue
        # with the rotated token instead of replaying an expired token.
        if token_file.is_file():
            try:
                persisted_token = token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                if not enrollment_token:
                    raise LocalWorkerError("WORKER_TOKEN_FILE_UNREADABLE") from exc
                persisted_token = ""
            if persisted_token:
                session_token = persisted_token
        if recovery_token_file.is_file():
            try:
                persisted_recovery_token = recovery_token_file.read_text(
                    encoding="utf-8"
                ).strip()
            except OSError as exc:
                if not enrollment_token:
                    raise LocalWorkerError(
                        "WORKER_RECOVERY_TOKEN_FILE_UNREADABLE"
                    ) from exc
                persisted_recovery_token = ""
            if persisted_recovery_token:
                recovery_token = persisted_recovery_token
        if not worker_id and worker_id_file.is_file():
            try:
                worker_id = worker_id_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                if not enrollment_token:
                    raise LocalWorkerError("WORKER_ID_FILE_UNREADABLE") from exc
            else:
                worker_id = worker_id.strip()
        if not worker_id and enrollment_token:
            worker_id = device_id
        if not worker_id or (enrollment_token and not device_id):
            raise LocalWorkerError("WORKER_CONFIG_REQUIRED")
        if not session_token and not enrollment_token and not recovery_token:
            raise LocalWorkerError("WORKER_CONFIG_REQUIRED")
        raw_capabilities = values.get("POLARIS_WORKER_CAPABILITIES", "")
        capabilities = (
            tuple(
                item.strip().casefold()
                for item in raw_capabilities.split(",")
                if item.strip()
            )
            or _DEFAULT_CAPABILITIES
        )
        if any(
            len(item) > 64 or any(char.isspace() for char in item)
            for item in capabilities
        ):
            raise LocalWorkerError("WORKER_CAPABILITIES_INVALID")
        try:
            request_timeout_seconds = float(
                values.get("POLARIS_WORKER_REQUEST_TIMEOUT", "20")
            )
            heartbeat_interval_seconds = float(
                values.get("POLARIS_WORKER_HEARTBEAT_INTERVAL", "20")
            )
            poll_interval_seconds = float(
                values.get("POLARIS_WORKER_POLL_INTERVAL", "2")
            )
            concurrency = int(values.get("POLARIS_WORKER_CONCURRENCY", "1"))
            lease_renew_interval_seconds = float(
                values.get("POLARIS_WORKER_LEASE_INTERVAL", "30")
            )
            browser_timeout_ms = int(
                values.get("POLARIS_WORKER_BROWSER_TIMEOUT_MS", "30000")
            )
            token_refresh_interval_seconds = float(
                values.get("POLARIS_WORKER_TOKEN_REFRESH_INTERVAL", "30")
            )
            bridge_port = int(values.get("POLARIS_WORKER_BRIDGE_PORT", "8765"))
        except ValueError as exc:
            raise LocalWorkerError("WORKER_CONFIG_INVALID") from exc
        if any(
            value <= 0
            for value in (
                request_timeout_seconds,
                heartbeat_interval_seconds,
                poll_interval_seconds,
                lease_renew_interval_seconds,
                browser_timeout_ms,
                token_refresh_interval_seconds,
            )
        ):
            raise LocalWorkerError("WORKER_CONFIG_INVALID")
        if not 1 <= concurrency <= 4:
            raise LocalWorkerError("WORKER_CONCURRENCY_INVALID")
        if not 1024 <= bridge_port <= 65535:
            raise LocalWorkerError("WORKER_BRIDGE_PORT_INVALID")
        parsed_api_url = urlparse(api_url)
        bridge_origin = values.get("POLARIS_WORKER_BRIDGE_ORIGIN", "").strip()
        if not bridge_origin:
            bridge_origin = f"{parsed_api_url.scheme}://{parsed_api_url.netloc}"
        return cls(
            api_url=api_url,
            session_token=session_token,
            recovery_token=recovery_token,
            enrollment_token=enrollment_token,
            session_token_file=token_file.resolve(),
            recovery_token_file=recovery_token_file.resolve(),
            worker_id=worker_id,
            profile_root=Path(profile_root).expanduser(),
            worker_id_file=worker_id_file.resolve(),
            capabilities=capabilities,
            request_timeout_seconds=request_timeout_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            poll_interval_seconds=poll_interval_seconds,
            concurrency=concurrency,
            lease_renew_interval_seconds=lease_renew_interval_seconds,
            browser_timeout_ms=browser_timeout_ms,
            token_refresh_interval_seconds=token_refresh_interval_seconds,
            bridge_port=bridge_port,
            bridge_origin=bridge_origin,
        )

    def persist_session_token(self, token: str) -> None:
        """Persist a rotated token atomically, best-effort with restrictive mode."""

        token = token.strip()
        if not token:
            raise LocalWorkerError("WORKER_TOKEN_REQUIRED")
        path = self.session_token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(token + "\n", encoding="utf-8")
            with suppress(OSError):
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            with suppress(OSError):
                os.chmod(path, 0o600)
        except OSError as exc:
            with suppress(OSError):
                temporary.unlink()
            raise LocalWorkerError("WORKER_TOKEN_FILE_UNWRITABLE") from exc

    def persist_worker_id(self, worker_id: str) -> None:
        """Persist the server-assigned worker id for the next local start."""

        normalized = _validate_id(worker_id, "worker_id")
        path = self.worker_id_file
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(normalized + "\n", encoding="utf-8")
            with suppress(OSError):
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            with suppress(OSError):
                os.chmod(path, 0o600)
        except OSError as exc:
            with suppress(OSError):
                temporary.unlink()
            raise LocalWorkerError("WORKER_ID_FILE_UNWRITABLE") from exc

    def persist_recovery_token(self, token: str) -> None:
        """Persist a rotated recovery token atomically."""

        token = token.strip()
        if not token:
            raise LocalWorkerError("WORKER_RECOVERY_TOKEN_REQUIRED")
        path = self.recovery_token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(token + "\n", encoding="utf-8")
            with suppress(OSError):
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            with suppress(OSError):
                os.chmod(path, 0o600)
        except OSError as exc:
            with suppress(OSError):
                temporary.unlink()
            raise LocalWorkerError("WORKER_RECOVERY_TOKEN_FILE_UNWRITABLE") from exc


def _validate_id(value: str, label: str) -> str:
    normalized = value.strip()
    if not _SAFE_ID.fullmatch(normalized):
        raise LocalWorkerError(f"INVALID_{label.upper()}")
    return normalized


def _assert_no_sensitive_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in _SENSITIVE_KEYS:
                raise LocalWorkerError("SENSITIVE_FIELD_BLOCKED")
            _assert_no_sensitive_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_sensitive_fields(child)


class LocalProfileStore:
    """Resolve account profiles below one user-selected local directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = _normalise_windows_resolved_path(
            Path(root).expanduser().resolve()
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks = self.root / ".locks"
        self._locks.mkdir(parents=True, exist_ok=True)

    def profile_path(self, account_id: str) -> Path:
        safe_account = _validate_id(account_id, "account_id")
        path = _normalise_windows_resolved_path(
            (self.root / "accounts" / safe_account).resolve()
        )
        if not path.is_relative_to(self.root):
            raise LocalWorkerError("PROFILE_PATH_OUTSIDE_ROOT")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @contextmanager
    def lock(
        self, account_id: str, *, stale_after_seconds: int = 3600
    ) -> Iterator[Path]:
        """Hold an exclusive cross-process lock for one account profile."""

        safe_account = _validate_id(account_id, "account_id")
        lock_path = self._locks / f"{safe_account}.lock"
        descriptor: int | None = None
        for attempt in range(2):
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                if attempt == 0 and self._reclaim_dead_lock(
                    lock_path, stale_after_seconds
                ):
                    continue
                raise LocalWorkerError("PROFILE_BUSY") from exc
        if descriptor is None:
            raise LocalWorkerError("PROFILE_BUSY")
        try:
            try:
                os.write(descriptor, str(os.getpid()).encode("ascii"))
            finally:
                os.close(descriptor)
            yield self.profile_path(safe_account)
        finally:
            with suppress(FileNotFoundError):
                lock_path.unlink()

    @staticmethod
    def _reclaim_dead_lock(lock_path: Path, stale_after_seconds: int) -> bool:
        try:
            if time.time() - lock_path.stat().st_mtime <= stale_after_seconds:
                return False
            owner_pid = int(lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            with suppress(FileNotFoundError):
                lock_path.unlink()
            return True
        except PermissionError:
            # Windows may deny the probe for a live process. Never reclaim it.
            return False
        except OSError as exc:
            # Windows reports an invalid PID as ERROR_INVALID_PARAMETER rather
            # than raising ProcessLookupError; both mean the owner is gone.
            if exc.errno == errno.ESRCH or getattr(exc, "winerror", None) in {
                87,
                128,
            }:
                with suppress(FileNotFoundError):
                    lock_path.unlink()
                return True
            return False
        return False


class WorkerApiClient:
    """Small outbound HTTPS client for the worker protocol."""

    def __init__(
        self, base_url: str, session_token: str, *, timeout: float = 20.0
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
        }:
            raise LocalWorkerError("WORKER_API_REQUIRES_HTTPS")
        if not parsed.netloc:
            raise LocalWorkerError("INVALID_WORKER_API_URL")
        if not session_token.strip():
            session_token = "enrollment-bootstrap"
        self.base_url = base_url.rstrip("/") + "/"
        self.session_token = session_token
        self.timeout = timeout
        self._session_token_lock = threading.RLock()

    def set_session_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            raise LocalWorkerError("WORKER_TOKEN_REQUIRED")
        with self._session_token_lock:
            self.session_token = token

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None
        if payload is not None:
            _assert_no_sensitive_fields(payload)
            body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        with self._session_token_lock:
            session_token = self.session_token
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=body,
            headers={
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                parsed = json.loads(response.read())
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                code = error_payload.get("error", {}).get("code")
            except Exception:
                code = None
            raise LocalWorkerError(str(code or "WORKER_API_UNAVAILABLE")) from exc
        except (URLError, TimeoutError) as exc:
            raise LocalWorkerError("WORKER_API_UNAVAILABLE") from exc
        except (TypeError, ValueError) as exc:
            raise LocalWorkerError("WORKER_API_INVALID_RESPONSE") from exc
        if not isinstance(parsed, dict):
            raise LocalWorkerError("WORKER_API_INVALID_RESPONSE")
        return parsed

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def heartbeat(self, worker_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._post(
            f"/api/ecommerce/workers/{_validate_id(worker_id, 'worker_id')}/heartbeat",
            payload,
        )

    def register_worker(
        self,
        enrollment_token: str,
        device_id: str,
        *,
        protocol_version: str,
        agent_version: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        token = enrollment_token.strip()
        if not token:
            raise LocalWorkerError("WORKER_ENROLLMENT_REQUIRED")
        device = _validate_id(device_id, "device_id")
        return self._post(
            "/api/ecommerce/workers/register",
            {
                "enrollment_token": token,
                "device_id": device,
                "protocol_version": protocol_version,
                "agent_version": agent_version,
                "capabilities": capabilities,
            },
        )

    def refresh_token(self, worker_id: str) -> dict[str, Any]:
        """Rotate the current session token and update this client in-place."""

        worker = _validate_id(worker_id, "worker_id")
        response = self._post(f"/api/ecommerce/workers/{worker}/token/refresh", {})
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise LocalWorkerError("WORKER_API_INVALID_RESPONSE")
        replacement = data.get("session_token")
        if not isinstance(replacement, str) or not replacement.strip():
            raise LocalWorkerError("WORKER_API_INVALID_RESPONSE")
        self.set_session_token(replacement)
        return response

    def recover_worker(
        self,
        recovery_token: str,
        worker_id: str,
        device_id: str,
        *,
        protocol_version: str,
        agent_version: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        token = recovery_token.strip()
        if not token:
            raise LocalWorkerError("WORKER_RECOVERY_REQUIRED")
        worker = _validate_id(worker_id, "worker_id")
        device = _validate_id(device_id, "device_id")
        return self._post(
            "/api/ecommerce/workers/recover",
            {
                "recovery_token": token,
                "worker_id": worker,
                "device_id": device,
                "protocol_version": protocol_version,
                "agent_version": agent_version,
                "capabilities": capabilities,
            },
        )

    def update_account_session(
        self, worker_id: str, account_id: str, login_state: str
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        account = _validate_id(account_id, "account_id")
        if login_state not in {"ready", "needs_login"}:
            raise LocalWorkerError("LOGIN_STATE_INVALID")
        return self._post(
            f"/api/ecommerce/workers/{worker}/accounts/{account}/session",
            {"login_state": login_state},
        )

    def claim_login(
        self, worker_id: str, platforms: list[str] | None = None
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        payload: dict[str, Any] = {}
        if platforms is not None:
            payload["platforms"] = platforms
        return self._post(f"/api/ecommerce/workers/{worker}/login-jobs/claim", payload)

    def update_login(
        self, worker_id: str, session_id: str, state: str, message: str = ""
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        session = _validate_id(session_id, "login_session_id")
        return self._post(
            f"/api/ecommerce/workers/{worker}/login-jobs/{session}/state",
            {"state": state, "message": message},
        )

    def login_control(self, worker_id: str, session_id: str) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        session = _validate_id(session_id, "login_session_id")
        return self._get(
            f"/api/ecommerce/workers/{worker}/login-jobs/{session}/control"
        )

    def upload_login_frame(
        self,
        worker_id: str,
        session_id: str,
        data: bytes,
        *,
        content_type: str = "image/jpeg",
        width: int = 0,
        height: int = 0,
        sequence: int = 0,
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        session = _validate_id(session_id, "login_session_id")
        if len(data) > 512 * 1024:
            raise LocalWorkerError("LOGIN_FRAME_TOO_LARGE")
        return self._post(
            f"/api/ecommerce/workers/{worker}/login-jobs/{session}/frames",
            {
                "data": base64.b64encode(data).decode("ascii"),
                "content_type": content_type,
                "width": width,
                "height": height,
                "sequence": sequence,
            },
        )

    def claim(
        self, worker_id: str, platforms: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if platforms is not None:
            payload["platforms"] = platforms
        worker = _validate_id(worker_id, "worker_id")
        return self._post(f"/api/ecommerce/workers/{worker}/tasks/claim", payload)

    def renew_lease(
        self, worker_id: str, task_id: str, fencing_token: int
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        task = _validate_id(task_id, "task_id")
        return self._post(
            f"/api/ecommerce/workers/{worker}/tasks/{task}/lease",
            {"fencing_token": fencing_token},
        )

    def complete(
        self, worker_id: str, task_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        task = _validate_id(task_id, "task_id")
        return self._post(
            f"/api/ecommerce/workers/{worker}/tasks/{task}/complete", payload
        )

    def upload_result(
        self, worker_id: str, task_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        task = _validate_id(task_id, "task_id")
        return self._post(
            f"/api/ecommerce/workers/{worker}/tasks/{task}/result", payload
        )

    def control(self, worker_id: str, task_id: str) -> dict[str, Any]:
        worker = _validate_id(worker_id, "worker_id")
        task = _validate_id(task_id, "task_id")
        return self._get(f"/api/ecommerce/workers/{worker}/tasks/{task}/control")


class LocalWorkerRuntime:
    """Execute one claimed task with a local profile and report safe outcomes."""

    def __init__(
        self, api: WorkerApiClient, worker_id: str, profiles: LocalProfileStore
    ) -> None:
        self.api = api
        self.worker_id = _validate_id(worker_id, "worker_id")
        self.profiles = profiles

    def execute_claimed(
        self,
        task: Mapping[str, Any],
        crawl: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        _assert_no_sensitive_fields(task)
        task_id = _validate_id(str(task.get("id", "")), "task_id")
        account_id = task.get("account_id") or task.get("profile_id")
        if not isinstance(account_id, str):
            raise LocalWorkerError("TASK_PROFILE_REQUIRED")
        lease = task.get("lease")
        if not isinstance(lease, Mapping) or not isinstance(
            lease.get("fencing_token"), int
        ):
            raise LocalWorkerError("TASK_LEASE_REQUIRED")
        fencing_token = int(lease["fencing_token"])
        try:
            with self.profiles.lock(account_id) as profile_path:
                result = crawl(profile_path, task)
                if not isinstance(result, Mapping):
                    raise LocalWorkerError("WORKER_RESULT_INVALID")
                return self.api.upload_result(
                    self.worker_id,
                    task_id,
                    {"fencing_token": fencing_token, "result": dict(result)},
                )
        except LocalWorkerTaskError as exc:
            with suppress(Exception):
                self.api.complete(
                    self.worker_id,
                    task_id,
                    {
                        "fencing_token": fencing_token,
                        "outcome": "failed",
                        "error_code": exc.code,
                        "error_message_safe": exc.message,
                    },
                )
            raise
        except LocalWorkerError:
            raise
        except Exception as exc:
            with suppress(Exception):
                self.api.complete(
                    self.worker_id,
                    task_id,
                    {
                        "fencing_token": fencing_token,
                        "outcome": "failed",
                        "error_code": "WORKER_BROWSER_CRASH",
                        "error_message_safe": "本机浏览器执行失败。",
                    },
                )
            raise LocalWorkerError("WORKER_BROWSER_CRASH") from exc


def build_local_product_crawler(
    *, timeout_ms: int = 30_000
) -> Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]:
    """Build a profile-backed browser crawler for claimed domestic tasks.

    The returned callback accepts only the local profile path and server task
    metadata. It never reads or serializes profile files itself; the platform
    adapters use the path to launch their isolated browser session.
    """

    if timeout_ms <= 0:
        raise LocalWorkerError("BROWSER_TIMEOUT_INVALID")

    def crawl(profile_path: Path, task: Mapping[str, Any]) -> Mapping[str, Any]:
        from .ecommerce_1688 import crawl_one_1688_browser
        from .ecommerce_fetcher import crawl_one_browser
        from .ecommerce_jd import crawl_one_jd_browser
        from .ecommerce_pinduoduo import crawl_one_pinduoduo_browser
        from .ecommerce_routes import _normalise

        url = task.get("canonical_url") or task.get("source_url")
        if not isinstance(url, str) or not url.strip():
            raise LocalWorkerTaskError("TASK_URL_REQUIRED", "任务缺少商品链接。")
        task_platform = task.get("platform")
        if not isinstance(task_platform, str):
            raise LocalWorkerTaskError("TASK_PLATFORM_REQUIRED", "任务缺少平台标识。")
        request_payload = task.get("request_payload")
        include_desc = True
        if isinstance(request_payload, Mapping):
            include_desc = bool(request_payload.get("includeDescImages", True))
        platform = task_platform.casefold()
        result: Any
        try:
            if platform in {"taobao", "tmall"}:
                result = crawl_one_browser(url, timeout_ms, str(profile_path))
            elif platform == "1688":
                result = crawl_one_1688_browser(url, timeout_ms, str(profile_path))
            elif platform == "jd":
                result = crawl_one_jd_browser(url, timeout_ms, str(profile_path))
            elif platform == "pinduoduo":
                result = crawl_one_pinduoduo_browser(url, timeout_ms, str(profile_path))
            else:
                raise LocalWorkerTaskError(
                    "PLATFORM_NOT_SUPPORTED", "本机代理暂不支持该平台。"
                )
        except LocalWorkerTaskError:
            raise
        except TimeoutError as exc:
            raise LocalWorkerTaskError(
                "WORKER_TIMEOUT", "本机浏览器执行超时，请稍后重试。"
            ) from exc
        except Exception as exc:
            raise LocalWorkerTaskError(
                "WORKER_BROWSER_CRASH", "本机浏览器执行失败，请稍后重试。"
            ) from exc

        access_state = getattr(result, "access_state", None)
        status_value = getattr(result, "status", None)
        if access_state in {"login_required", "risk_controlled", "captcha_required"}:
            messages = {
                "login_required": "本机账号需要重新登录。",
                "risk_controlled": "商品页面触发安全验证，请人工处理后重试。",
                "captcha_required": "商品页面需要人工完成验证码验证。",
            }
            raise LocalWorkerTaskError(access_state.upper(), messages[access_state])
        if status_value is not None and status_value != "product":
            status_code = {
                "login_required": "LOGIN_REQUIRED",
                "risk_controlled": "RISK_CONTROL_PAGE",
                "captcha_required": "CAPTCHA_REQUIRED",
                "product_not_found": "PRODUCT_NOT_FOUND",
            }.get(str(status_value), "WORKER_FAILED")
            raise LocalWorkerTaskError(status_code, "本机商品页面未返回可用商品结果。")
        # The completed JD browser result must contain a validated product
        # image. A page title or an arbitrary ID alone can belong to an
        # unrecognized error shell and must not be uploaded as a success.
        if platform == "jd" and not getattr(result, "images", ()):
            raise LocalWorkerTaskError(
                "IMAGE_EXTRACTION_INCOMPLETE",
                "京东页面未返回可用商品图片，请确认商品页可以正常展示后重试。",
            )
        normalized = _normalise(result, url.strip(), include_desc)
        normalized["platform"] = platform
        if not normalized.get("title") and not normalized.get("numIid"):
            raise LocalWorkerTaskError(
                "RESULT_INCOMPLETE", "本机未读取到完整商品字段。"
            )
        return normalized

    return crawl


class LocalWorkerLoop:
    """Poll the control plane while keeping task leases alive."""

    def __init__(
        self,
        api: WorkerApiClient,
        runtime: LocalWorkerRuntime,
        *,
        protocol_version: str = "worker.v1",
        agent_version: str = "local-runtime.v1",
        capabilities: list[str] | None = None,
        heartbeat_interval_seconds: float = 20.0,
        poll_interval_seconds: float = 2.0,
        lease_renew_interval_seconds: float = 30.0,
        token_refresh_interval_seconds: float = 30.0,
        concurrency: int = 1,
        on_token_refreshed: Callable[[str], None] | None = None,
        on_recovery_token_refreshed: Callable[[str], None] | None = None,
        on_worker_id_persisted: Callable[[str], None] | None = None,
        enrollment_token: str = "",
        recovery_token: str = "",
        device_id: str = "",
    ) -> None:
        if heartbeat_interval_seconds <= 0 or poll_interval_seconds <= 0:
            raise LocalWorkerError("WORKER_INTERVAL_INVALID")
        if lease_renew_interval_seconds <= 0:
            raise LocalWorkerError("WORKER_LEASE_INTERVAL_INVALID")
        if token_refresh_interval_seconds <= 0:
            raise LocalWorkerError("WORKER_TOKEN_REFRESH_INTERVAL_INVALID")
        if not 1 <= concurrency <= 4:
            raise LocalWorkerError("WORKER_CONCURRENCY_INVALID")
        self.api = api
        self.runtime = runtime
        self.protocol_version = protocol_version
        self.agent_version = agent_version
        self.capabilities = list(capabilities or [])
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_renew_interval_seconds = lease_renew_interval_seconds
        self.token_refresh_interval_seconds = token_refresh_interval_seconds
        self.concurrency = concurrency
        self.on_token_refreshed = on_token_refreshed
        self.on_recovery_token_refreshed = on_recovery_token_refreshed
        self.on_worker_id_persisted = on_worker_id_persisted
        self.enrollment_token = enrollment_token.strip()
        self.recovery_token = recovery_token.strip()
        self.device_id = (device_id or runtime.worker_id).strip()
        # A bootstrap token is attempted once at startup. A token delivered
        # later through the loopback bridge explicitly re-arms the attempt,
        # even when this process already has a formal server-assigned worker id.
        self._enrollment_requested = bool(
            self.enrollment_token and runtime.worker_id == self.device_id
        )
        self._enrollment_lock = threading.RLock()
        self._claim_lock = threading.Lock()

    def accept_enrollment(self, enrollment_token: str, device_id: str) -> None:
        """Accept a browser-delivered one-time token without logging it."""

        token = enrollment_token.strip()
        normalized_device_id = device_id.strip()
        if not token or len(token) > 256:
            raise LocalWorkerError("WORKER_ENROLLMENT_REQUIRED")
        if normalized_device_id != self.device_id:
            raise LocalWorkerError("WORKER_DEVICE_MISMATCH")
        with self._enrollment_lock:
            self.enrollment_token = token
            self._enrollment_requested = True

    def _recover_registration(self) -> bool:
        # Heartbeat recovery and an explicit browser enrollment can now run
        # alongside browser work. Serialize token rotation and persistence.
        with self._enrollment_lock:
            return self._recover_registration_locked()

    def _recover_registration_locked(self) -> bool:
        recover_worker = getattr(self.api, "recover_worker", None)
        if (
            self.recovery_token
            and not self._enrollment_requested
            and callable(recover_worker)
        ):
            try:
                response = recover_worker(
                    self.recovery_token,
                    self.runtime.worker_id,
                    self.device_id,
                    protocol_version=self.protocol_version,
                    agent_version=self.agent_version,
                    capabilities=self.capabilities,
                )
                data = response.get("data") if isinstance(response, Mapping) else None
                token = data.get("session_token") if isinstance(data, Mapping) else None
                replacement = (
                    data.get("recovery_token") if isinstance(data, Mapping) else None
                )
                if (
                    not isinstance(token, str)
                    or not token.strip()
                    or not isinstance(replacement, str)
                    or not replacement.strip()
                ):
                    return False
                setter = getattr(self.api, "set_session_token", None)
                if callable(setter):
                    setter(token)
                else:
                    self.api.session_token = token.strip()
                self.recovery_token = replacement.strip()
                if self.on_token_refreshed is not None:
                    self.on_token_refreshed(token)
                if self.on_recovery_token_refreshed is not None:
                    self.on_recovery_token_refreshed(replacement)
                return True
            except LocalWorkerError:
                pass
        if not self.enrollment_token or not self._enrollment_requested:
            return False
        try:
            response = self.api.register_worker(
                self.enrollment_token,
                self.device_id,
                protocol_version=self.protocol_version,
                agent_version=self.agent_version,
                capabilities=self.capabilities,
            )
            data = response.get("data") if isinstance(response, Mapping) else None
            token = data.get("session_token") if isinstance(data, Mapping) else None
            worker = data.get("worker") if isinstance(data, Mapping) else None
            worker_id = worker.get("id") if isinstance(worker, Mapping) else None
            if (
                not isinstance(token, str)
                or not token.strip()
                or not isinstance(worker_id, str)
            ):
                return False
            setter = getattr(self.api, "set_session_token", None)
            if callable(setter):
                setter(token)
            else:
                self.api.session_token = token.strip()
            self.runtime.worker_id = _validate_id(worker_id, "worker_id")
            recovery_token = (
                data.get("recovery_token") if isinstance(data, Mapping) else None
            )
            if isinstance(recovery_token, str) and recovery_token.strip():
                self.recovery_token = recovery_token.strip()
                if self.on_recovery_token_refreshed is not None:
                    self.on_recovery_token_refreshed(recovery_token)
            self.enrollment_token = ""
            self._enrollment_requested = False
            if self.on_token_refreshed is not None:
                self.on_token_refreshed(token)
            if self.on_worker_id_persisted is not None:
                self.on_worker_id_persisted(worker_id)
            return True
        except LocalWorkerError:
            self._enrollment_requested = False
            return False

    def _heartbeat(self, ready: bool) -> dict[str, Any]:
        return self.api.heartbeat(
            self.runtime.worker_id,
            {
                "protocol_version": self.protocol_version,
                "agent_version": self.agent_version,
                "capabilities": self.capabilities,
                "ready": ready,
            },
        )

    def run_once(
        self, crawl: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
    ) -> bool:
        """Claim and execute at most one task; return whether work was done."""

        # Serialize only claims, not browser execution. This also supports
        # control planes predating concurrent slots without double-claiming.
        login_job = None
        task = None
        with self._claim_lock:
            claim_login = getattr(self.api, "claim_login", None)
            if callable(claim_login):
                login_job = claim_login(self.runtime.worker_id, self._platforms()).get(
                    "data"
                )
            if login_job is None:
                task = self.api.claim(self.runtime.worker_id, self._platforms()).get(
                    "data"
                )
        if login_job is not None:
            if not isinstance(login_job, Mapping):
                raise LocalWorkerError("WORKER_LOGIN_INVALID")
            account_id = _validate_id(
                str(login_job.get("account_id", "")), "account_id"
            )
            try:
                with self.runtime.profiles.lock(account_id):
                    run_remote_login(
                        self.api,
                        self.runtime.worker_id,
                        self.runtime.profiles,
                        login_job,
                    )
            except LocalWorkerError as exc:
                if str(exc) == "PROFILE_BUSY":
                    with suppress(Exception):
                        self.api.update_login(
                            self.runtime.worker_id,
                            str(login_job["id"]),
                            "failed",
                            "该账号正在执行任务，请等待完成后再登录。",
                        )
                raise
            return True
        if task is None:
            return False
        if not isinstance(task, Mapping):
            raise LocalWorkerError("WORKER_TASK_INVALID")
        task_id = _validate_id(str(task.get("id", "")), "task_id")
        lease = task.get("lease")
        if not isinstance(lease, Mapping) or not isinstance(
            lease.get("fencing_token"), int
        ):
            raise LocalWorkerError("TASK_LEASE_REQUIRED")
        fencing_token = int(lease["fencing_token"])
        stop_renew = threading.Event()

        def renew() -> None:
            while not stop_renew.wait(self.lease_renew_interval_seconds):
                try:
                    self.api.renew_lease(self.runtime.worker_id, task_id, fencing_token)
                except LocalWorkerError:
                    return

        renewer = threading.Thread(
            target=renew, name="polaris-lease-renew", daemon=True
        )
        renewer.start()
        try:
            self.runtime.execute_claimed(task, crawl)
        finally:
            stop_renew.set()
            renewer.join(timeout=max(1.0, self.lease_renew_interval_seconds))
        return True

    def run(
        self,
        crawl: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]],
        stop_event: threading.Event | None = None,
    ) -> None:
        """Run until ``stop_event`` is set, tolerating transient API outages."""

        stop = stop_event or threading.Event()
        refresh_stop = threading.Event()

        def heartbeat_loop() -> None:
            """Keep the worker online while login or crawling blocks the loop.

            Login/browser work can legitimately take several minutes. Keeping
            heartbeat in the claim loop meant the control plane marked an
            otherwise healthy Worker offline after 90 seconds, so new jobs
            were rejected with ``ACCOUNT_NOT_AVAILABLE``.
            """

            while not stop.is_set():
                try:
                    self._heartbeat(True)
                except LocalWorkerError as exc:
                    if exc.args and str(exc.args[0]) in {
                        "WORKER_AUTH_INVALID",
                        "WORKER_TOKEN_EXPIRED",
                        "WORKER_TOKEN_ROTATED",
                    }:
                        if self.enrollment_token and (
                            not self.recovery_token
                            or not callable(getattr(self.api, "recover_worker", None))
                        ):
                            self._enrollment_requested = True
                        self._recover_registration()
                except Exception:
                    # A transient network/API outage must not terminate the
                    # Worker; the next heartbeat will retry.
                    pass
                if stop.wait(self.heartbeat_interval_seconds):
                    return

        def refresh_token_loop() -> None:
            # Keep the token alive independently of crawl/login work.  A
            # transient outage is harmless; the next interval retries.  If the
            # server rejects an expired token, normal heartbeat handling will
            # continue and the process can be re-enrolled explicitly.
            while not refresh_stop.wait(self.token_refresh_interval_seconds):
                refresh = getattr(self.api, "refresh_token", None)
                if not callable(refresh):
                    return
                try:
                    response = refresh(self.runtime.worker_id)
                    data = (
                        response.get("data") if isinstance(response, Mapping) else None
                    )
                    token = (
                        data.get("session_token") if isinstance(data, Mapping) else None
                    )
                    if isinstance(token, str) and self.on_token_refreshed is not None:
                        self.on_token_refreshed(token)
                except Exception:
                    continue

        refresher = threading.Thread(
            target=refresh_token_loop, name="polaris-token-refresh", daemon=True
        )
        refresher.start()
        heartbeat = threading.Thread(
            target=heartbeat_loop, name="polaris-worker-heartbeat", daemon=True
        )
        heartbeat.start()
        executor = ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="polaris-worker"
        )
        futures: set[Future[bool]] = set()

        def run_slot() -> bool:
            try:
                return self.run_once(crawl)
            except LocalWorkerError:
                return False
            except Exception:
                return False

        try:
            while not stop.is_set():
                # A new device has only its device id at bootstrap time. An
                # existing worker should try its saved session first and use
                # the one-time token only after the server rejects that
                # session, so a stale token cannot block normal heartbeats.
                if (
                    self.enrollment_token
                    and self._enrollment_requested
                    and not self._recover_registration()
                ):
                    stop.wait(self.poll_interval_seconds)
                    continue
                while len(futures) < self.concurrency and not stop.is_set():
                    futures.add(executor.submit(run_slot))
                if not futures:
                    stop.wait(self.poll_interval_seconds)
                    continue
                done, pending = wait(
                    futures,
                    timeout=self.poll_interval_seconds,
                    return_when=FIRST_COMPLETED,
                )
                futures = set(pending)
                if not done:
                    continue
                did_work = any(future.result() for future in done)
                if not did_work:
                    stop.wait(self.poll_interval_seconds)
        finally:
            stop.set()
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            refresh_stop.set()
            refresher.join(timeout=max(1.0, self.token_refresh_interval_seconds))
            heartbeat.join(timeout=max(1.0, self.heartbeat_interval_seconds))
            with suppress(LocalWorkerError):
                self._heartbeat(False)

    def _platforms(self) -> list[str] | None:
        platforms = [
            value.rsplit(".", 1)[-1]
            for value in self.capabilities
            if value.startswith("browser.")
        ]
        return list(dict.fromkeys(platforms)) or None


class LocalWorkerBridge:
    """Loopback-only bridge used by the signed-in control-plane page."""

    def __init__(
        self,
        loop: LocalWorkerLoop,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        allowed_origin: str = "",
    ) -> None:
        self.loop = loop
        self.host = host
        self.port = port
        self.allowed_origin = allowed_origin.rstrip("/")
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def _origin_allowed(self) -> bool:
                origin = self.headers.get("Origin", "").rstrip("/")
                return not origin or origin == bridge.allowed_origin

            def _headers(self) -> None:
                origin = self.headers.get("Origin", "").rstrip("/")
                if origin and origin == bridge.allowed_origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Private-Network", "true")

            def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self._headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
                if self.path != "/v1/enrollment" or not self._origin_allowed():
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "ORIGIN_FORBIDDEN"},
                    )
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self._headers()
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                if self.path != "/v1/enrollment" or not self._origin_allowed():
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "ORIGIN_FORBIDDEN"},
                    )
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0 or content_length > 4096:
                        raise LocalWorkerError("WORKER_ENROLLMENT_PAYLOAD_INVALID")
                    payload = json.loads(self.rfile.read(content_length))
                    if not isinstance(payload, Mapping):
                        raise LocalWorkerError("WORKER_ENROLLMENT_PAYLOAD_INVALID")
                    bridge.loop.accept_enrollment(
                        str(payload.get("enrollment_token", "")),
                        str(payload.get("device_id", "")),
                    )
                except (ValueError, json.JSONDecodeError, LocalWorkerError) as exc:
                    code = str(exc.args[0]) if exc.args else "WORKER_ENROLLMENT_FAILED"
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": code})
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "status": "registering",
                        "device_id": bridge.loop.device_id,
                    },
                )

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        return Handler

    def start(self) -> None:
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), self._handler())
        except OSError as exc:
            raise LocalWorkerError("WORKER_BRIDGE_START_FAILED") from exc
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="polaris-worker-bridge",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        server = self.server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        self.server = None
        self.thread = None


def run_local_worker(config: LocalWorkerConfig) -> None:
    """Start the local loop from already validated, non-secret config."""

    api = WorkerApiClient(
        config.api_url,
        config.session_token,
        timeout=config.request_timeout_seconds,
    )
    profiles = LocalProfileStore(config.profile_root)
    runtime = LocalWorkerRuntime(api, config.worker_id, profiles)
    loop = LocalWorkerLoop(
        api,
        runtime,
        capabilities=list(config.capabilities),
        concurrency=config.concurrency,
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
        lease_renew_interval_seconds=config.lease_renew_interval_seconds,
        token_refresh_interval_seconds=config.token_refresh_interval_seconds,
        on_token_refreshed=config.persist_session_token,
        on_recovery_token_refreshed=config.persist_recovery_token,
        on_worker_id_persisted=config.persist_worker_id,
        enrollment_token=config.enrollment_token,
        recovery_token=config.recovery_token,
        device_id=os.environ.get("POLARIS_WORKER_DEVICE_ID", config.worker_id),
    )
    bridge = LocalWorkerBridge(
        loop,
        host=config.bridge_host,
        port=config.bridge_port,
        allowed_origin=config.bridge_origin,
    )
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            with suppress(ValueError):
                signal.signal(signal_value, request_stop)
    bridge.start()
    try:
        loop.run(
            build_local_product_crawler(timeout_ms=config.browser_timeout_ms),
            stop_event,
        )
    finally:
        bridge.stop()


def run_local_login(
    profile_root: str | Path,
    account_id: str,
    platform: str,
    *,
    target_url: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Open a visible local login browser for one account profile."""

    from .ecommerce_session import EcommerceSessionManager

    if timeout_seconds <= 0:
        raise LocalWorkerError("LOGIN_TIMEOUT_INVALID")
    normalized_platform = platform.strip().casefold()
    platform_map = {"tmall": "taobao", "alibaba1688": "1688"}
    normalized_platform = platform_map.get(normalized_platform, normalized_platform)
    if normalized_platform not in {"taobao", "1688", "jd", "pinduoduo"}:
        raise LocalWorkerError("LOGIN_PLATFORM_INVALID")
    profile_path = LocalProfileStore(profile_root).profile_path(account_id)
    manager = EcommerceSessionManager(
        profile_path,
        platform="alibaba1688"
        if normalized_platform == "1688"
        else normalized_platform,
        session_profile=f"domestic-{normalized_platform}",
        state_style="legacy" if normalized_platform == "taobao" else "canonical",
    )
    manager.start_login(
        target_url,
        headless=False,
        timeout_seconds=timeout_seconds,
        force=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = manager.status()
        if status.get("state") not in {"login_pending", "risk_controlled"}:
            return status
        # Keep remote browser input responsive while retaining a bounded
        # control-plane request rate. The browser helper itself drains the
        # local action queue on its 200ms loop.
        time.sleep(0.1)
    with suppress(Exception):
        manager.cancel_login()
    raise LocalWorkerError("LOGIN_TIMEOUT")


def run_remote_login(
    api: WorkerApiClient,
    worker_id: str,
    profiles: LocalProfileStore,
    job: Mapping[str, Any],
    *,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Execute a queued login job locally and publish only transient frames."""

    account_id = _validate_id(str(job.get("account_id", "")), "account_id")
    session_id = _validate_id(str(job.get("id", "")), "login_session_id")
    platform = str(job.get("platform") or "taobao").casefold()
    url = str(job.get("login_url") or "").strip() or None
    profile_path = profiles.profile_path(account_id)
    from .ecommerce_session import EcommerceSessionManager

    manager = EcommerceSessionManager(
        profile_path,
        platform="alibaba1688" if platform == "1688" else platform,
        session_profile=f"domestic-{platform}",
        state_style="legacy" if platform == "taobao" else "canonical",
    )
    try:
        manager.start_login(
            url,
            headless=True,
            timeout_seconds=timeout_seconds,
            force=True,
        )
    except Exception as exc:
        # Keep the control plane from leaving a claimed login job waiting
        # forever when the local browser/profile cannot be started. The
        # exception type is useful in the local supervisor log and contains
        # no profile, cookie, or credential material.
        print(
            f"local login browser startup failed: {type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        with suppress(Exception):
            api.update_login(
                worker_id,
                session_id,
                "failed",
                "本机浏览器启动失败，请检查本机浏览器和 Profile 权限。",
            )
        raise LocalWorkerError("WORKER_LOGIN_BROWSER_START_FAILED") from exc

    # A login flow can wait several minutes for a QR scan or a manual risk
    # check. Keep the Worker lease alive while that foreground job occupies
    # the polling loop, otherwise the control plane marks it offline midway
    # through a valid login session.
    def keep_worker_online() -> None:
        while True:
            state = str(manager.status().get("state") or "")
            if state not in {"login_pending", "risk_controlled"}:
                return
            with suppress(Exception):
                api.heartbeat(
                    worker_id,
                    {
                        "protocol_version": "worker.v1",
                        "agent_version": "local-runtime.v1",
                        "ready": True,
                    },
                )
            time.sleep(10)

    threading.Thread(
        target=keep_worker_online,
        name="polaris-login-heartbeat",
        daemon=True,
    ).start()
    api.update_login(worker_id, session_id, "waiting_qr", "目标本机登录页已打开。")
    deadline = time.monotonic() + timeout_seconds
    sequence = 0
    last_mtime_ns = 0
    while time.monotonic() < deadline:
        try:
            control = api.login_control(worker_id, session_id).get("data") or {}
            actions = control.get("actions") or []
            if isinstance(actions, list):
                for action in actions:
                    if isinstance(action, Mapping):
                        manager.enqueue_input(dict(action))
            if control.get("cancel_requested") or control.get("expired"):
                with suppress(Exception):
                    manager.cancel_login()
                api.update_login(
                    worker_id,
                    session_id,
                    "cancelled" if control.get("cancel_requested") else "expired",
                )
                return {
                    "state": "cancelled"
                    if control.get("cancel_requested")
                    else "expired"
                }
        except LocalWorkerError:
            pass
        current = manager.status()
        state = str(current.get("state") or "waiting_user")
        mapped = {
            "login_pending": "waiting_user",
            "risk_controlled": "risk_controlled",
            "ready": "ready",
            "error": "failed",
            # Canonical (JD/1688/PDD) profiles normalize the helper's
            # terminal legacy states to these names. Keep them from falling
            # through to ``waiting_user`` forever after the browser closes or
            # fails during startup.
            "not_initialized": "needs_login",
            "signed_out": "needs_login",
            "login_required": "needs_login",
            "unavailable": "failed",
            "needs_relogin": "needs_login",
        }.get(state, "waiting_user")
        frame_path = (
            manager.stream_path
            if manager.stream_path.is_file()
            else manager.preview_path
        )
        if frame_path.is_file():
            try:
                stat = frame_path.stat()
                if stat.st_mtime_ns != last_mtime_ns:
                    frame = frame_path.read_bytes()
                    if len(frame) <= 512 * 1024:
                        sequence += 1
                        api.upload_login_frame(
                            worker_id,
                            session_id,
                            frame,
                            content_type="image/jpeg"
                            if frame_path == manager.stream_path
                            else "image/png",
                            sequence=sequence,
                        )
                        last_mtime_ns = stat.st_mtime_ns
            except OSError:
                pass
            except LocalWorkerError as exc:
                print(
                    f"local login frame upload failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        if mapped in {"ready", "needs_login", "risk_controlled", "failed"}:
            api.update_login(
                worker_id, session_id, mapped, str(current.get("message") or "")
            )
            return current
        api.update_login(worker_id, session_id, mapped)
        time.sleep(0.5)
    with suppress(Exception):
        manager.cancel_login()
    api.update_login(worker_id, session_id, "expired", "本机登录任务已超时。")
    raise LocalWorkerError("LOGIN_TIMEOUT")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; secrets are accepted only through environment vars."""

    parser = argparse.ArgumentParser(
        description="Run the outbound-only Polaris local ecommerce worker."
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate environment configuration without connecting to the API",
    )
    parser.add_argument(
        "--login-account-id",
        help="open a visible local login browser for one account ID",
    )
    parser.add_argument(
        "--login-platform",
        choices=("taobao", "tmall", "1688", "jd", "pinduoduo"),
        default="taobao",
    )
    parser.add_argument("--login-url", help="optional official platform URL to open")
    parser.add_argument("--login-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        if args.login_account_id:
            profile_root = os.environ.get("POLARIS_WORKER_PROFILE_ROOT", "").strip()
            if not profile_root:
                raise LocalWorkerError("WORKER_PROFILE_ROOT_REQUIRED")
            login_status = run_local_login(
                profile_root,
                args.login_account_id,
                args.login_platform,
                target_url=args.login_url,
                timeout_seconds=args.login_timeout,
            )
            # When the worker credentials are present, publish only the
            # coarse login state.  Cookies, profile paths and browser data
            # never leave the local machine.  Keep login usable in standalone
            # mode when the worker has not yet been registered.
            login_state = (
                login_status.get("state") if isinstance(login_status, dict) else None
            )
            if login_state in {"ready", "needs_login"}:
                try:
                    config = LocalWorkerConfig.from_env()
                    WorkerApiClient(
                        config.api_url,
                        config.session_token,
                        timeout=config.request_timeout_seconds,
                    ).update_account_session(
                        config.worker_id,
                        args.login_account_id,
                        login_state,
                    )
                except LocalWorkerError:
                    # The browser login itself succeeded; status sync can be
                    # retried by the worker after registration.
                    pass
            return 0
        config = LocalWorkerConfig.from_env()
        if args.check_config:
            return 0
        run_local_worker(config)
    except LocalWorkerError as exc:
        print(f"local worker stopped: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by deployment tooling
    raise SystemExit(main())
