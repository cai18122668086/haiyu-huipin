"""Authorized browser-session lifecycle for ecommerce fetching.

Each platform owns an independent persistent browser profile. The session is
created by a human in a visible browser window; this module stores only the
profile directory and non-secret status metadata. It never logs or returns
cookie values and never automates CAPTCHA or login credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


_PLATFORM_LABELS = {
    "taobao": "淘宝",
    "tmall": "天猫",
    "alibaba1688": "1688",
    "1688": "1688",
    "jd": "京东",
    "pinduoduo": "拼多多",
}


class EcommerceSessionManager:
    """Own one platform/session profile and its human-assisted login process."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        platform: str = "taobao",
        session_profile: str | None = None,
        state_style: str = "legacy",
    ) -> None:
        if state_style not in {"legacy", "canonical"}:
            raise ValueError("state_style must be 'legacy' or 'canonical'")
        self.profile_dir = Path(profile_dir)
        self.platform = platform
        self.session_profile = session_profile or (
            "domestic-taobao-tmall"
            if platform in {"taobao", "tmall"}
            else f"domestic-{platform}"
        )
        self.state_style = state_style
        self.status_path = self.profile_dir / "session-status.json"
        self.preview_path = self.profile_dir / "login-preview.png"
        self.stream_path = self.profile_dir / "login-stream.jpg"
        self.actions_dir = self.profile_dir / "login-actions"
        self._lock = RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._login_headless = False

    @property
    def platform_label(self) -> str:
        return _PLATFORM_LABELS.get(self.platform, self.platform)

    @property
    def _canonical(self) -> bool:
        return self.state_style == "canonical"

    def _default_status(self) -> dict[str, Any]:
        if self._canonical:
            return {
                "state": "not_initialized",
                "message": f"尚未初始化{self.platform_label}会话。",
            }
        return {
            "state": "signed_out",
            "message": f"尚未登录{self.platform_label}账号。",
        }

    def _message(self, kind: str, *, headless: bool | None = None) -> str:
        label = self.platform_label
        use_headless = self._login_headless if headless is None else headless
        if kind == "pending":
            return (
                f"请在登录预览中使用官方二维码完成{label}登录。"
                if use_headless
                else f"请在弹出的{label}窗口中完成登录，完成后关闭窗口。"
            )
        if kind == "pending_short":
            return (
                f"请在登录预览中完成{label}登录。"
                if use_headless
                else f"请在弹出的{label}窗口中完成登录。"
            )
        if kind == "closed":
            return (
                "登录预览已关闭，请重新发起登录。"
                if use_headless
                else "登录窗口已关闭，请重新发起登录。"
            )
        if kind == "relogin":
            return f"{label}会话已失效，请在当前网络环境下重新登录。"
        if kind == "ready":
            return f"{label}账号会话已建立，可进行商品抓取。"
        return f"{label}会话状态已更新。"

    def _read_status(self) -> dict[str, Any]:
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return self._default_status()
        return value if isinstance(value, dict) else self._default_status()

    def _clean_status(self, value: dict[str, Any]) -> dict[str, Any]:
        if self._canonical:
            # The helper is shared with the legacy Taobao flow and can write
            # legacy terminal states when a human closes the window. Normalize
            # those states at this profile boundary instead of exposing a
            # second state vocabulary for 1688.
            state = value.get("state", "not_initialized")
            value["state"] = {
                "signed_out": "not_initialized",
                "error": "unavailable",
            }.get(state, state) if isinstance(state, str) else state
        value.pop("cookie", None)
        value.pop("cookies", None)
        value["profileReady"] = self.profile_dir.exists()
        value["previewAvailable"] = self.preview_path.is_file()
        value["streamAvailable"] = self.stream_path.is_file()
        value["sessionProfile"] = self.session_profile
        value["platform"] = self.platform
        return value

    def status(self) -> dict[str, Any]:
        with self._lock:
            current = self._read_status()
            process = self._process
            transient_states = (
                {"login_pending"} if not self._canonical else {"login_pending"}
            )
            if process is not None and process.poll() is None:
                # The helper can recognize an already-authenticated persistent
                # profile and write ``ready`` before it exits. Do not mask that
                # state with a transient process status.
                if current.get("state") not in {
                    "ready",
                    "needs_relogin",
                    "error",
                    "risk_controlled",
                }:
                    current["state"] = "login_pending"
                    current["message"] = self._message("pending")
            elif process is not None and process.poll() is not None:
                self._process = None
                if current.get("state") == "login_pending":
                    current["state"] = (
                        "signed_out" if not self._canonical else "not_initialized"
                    )
                    current["message"] = self._message("closed")
            elif process is None and current.get("state") in transient_states:
                current["state"] = (
                    "signed_out" if not self._canonical else "not_initialized"
                )
                current["message"] = self._message("closed")
            return self._clean_status(current)

    def start_login(
        self,
        target_url: str | None = None,
        *,
        headless: bool = False,
        timeout_seconds: int = 600,
        force: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.status()
            existing = self._read_status()
            # An explicit account login request (the admin page's “登录/重新登录”
            # action) must validate the actual browser profile again.  A stale
            # status file otherwise short-circuits the helper before it can
            # show the platform login page, which is especially confusing for
            # Taobao and JD accounts whose persisted state may outlive their
            # web session.
            if (
                not force
                and existing.get("state") == "ready"
                and self.profile_dir.exists()
            ):
                return self._clean_status(existing)
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._login_headless = headless
            self.preview_path.unlink(missing_ok=True)
            self.stream_path.unlink(missing_ok=True)
            self.actions_dir.mkdir(parents=True, exist_ok=True)
            for action_path in self.actions_dir.glob("*.json"):
                action_path.unlink(missing_ok=True)
            self.status_path.write_text(
                json.dumps(
                    {
                        "state": "login_pending",
                        "message": self._message("pending"),
                        "startedAt": _utc_now(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            project_python = (
                Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
            )
            python_executable = (
                str(project_python) if project_python.exists() else sys.executable
            )
            child_env = os.environ.copy()
            venv_site = project_python.parent.parent / "Lib" / "site-packages"
            if venv_site.exists():
                existing_python_path = child_env.get("PYTHONPATH", "")
                child_env["PYTHONPATH"] = os.pathsep.join(
                    part for part in (str(venv_site), existing_python_path) if part
                )
            if venv_site.exists():
                bootstrap = (
                    "import runpy,sys; "
                    f"sys.path.insert(0,{str(venv_site)!r}); "
                    "runpy.run_module('app.ecommerce_login', run_name='__main__')"
                )
                command = [python_executable, "-c", bootstrap]
            else:
                command = [python_executable, "-m", "app.ecommerce_login"]
            command.extend(
                [
                    "--profile-dir",
                    str(self.profile_dir),
                    "--status-path",
                    str(self.status_path),
                    "--platform",
                    self.platform,
                    "--timeout-seconds",
                    str(max(1, timeout_seconds)),
                ]
            )
            if headless:
                command.append("--headless")
                command.extend(["--screenshot-path", str(self.preview_path)])
                command.extend(["--actions-dir", str(self.actions_dir)])
                command.extend(["--screencast-path", str(self.stream_path)])
            if target_url:
                command.extend(["--target-url", target_url])
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            log_path = self.profile_dir / "login-process.log"
            log_handle = log_path.open("ab")
            self._process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=child_env,
            )
            log_handle.close()
            if not headless:
                focus_command = (
                    "$ErrorActionPreference='SilentlyContinue'; "
                    "$shell=New-Object -ComObject WScript.Shell; "
                    "for($i=0;$i -lt 60;$i++){ "
                    "$w=Get-Process chrome | Where-Object {$_.MainWindowHandle -ne 0} "
                    "| Sort-Object StartTime -Descending | Select-Object -First 1; "
                    "if($w){[void]$shell.AppActivate($w.Id); break}; "
                    "Start-Sleep -Milliseconds 500 }"
                )
                subprocess.Popen(
                    [
                        "powershell",
                        "-NoProfile",
                        "-WindowStyle",
                        "Hidden",
                        "-Command",
                        focus_command,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            return self.status()

    def enqueue_input(self, action: dict[str, Any]) -> None:
        """Queue one user-originated browser input for the login helper."""

        with self._lock:
            if self._process is None or self._process.poll() is not None:
                raise RuntimeError("LOGIN_SESSION_NOT_ACTIVE")
            self.actions_dir.mkdir(parents=True, exist_ok=True)
            action_path = self.actions_dir / f"{uuid4().hex}.json"
            temporary_path = action_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(action, ensure_ascii=False), encoding="utf-8"
            )
            temporary_path.replace(action_path)

    def cancel_login(self) -> dict[str, Any]:
        """Stop only this account's helper and clear its short-lived preview."""

        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                with suppress(Exception):
                    process.terminate()
                with suppress(Exception):
                    process.wait(timeout=5)
            self._process = None
            self.preview_path.unlink(missing_ok=True)
            self.stream_path.unlink(missing_ok=True)
            for action_path in self.actions_dir.glob("*.json"):
                action_path.unlink(missing_ok=True)
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(
                json.dumps(
                    {
                        "state": "signed_out"
                        if not self._canonical
                        else "not_initialized",
                        "message": "登录流程已取消，请需要时重新发起登录。",
                        "updatedAt": _utc_now(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return self.status()

    def start_manual_page(self, target_url: str) -> dict[str, Any]:
        """Open an official platform page for human-only verification.

        This deliberately reuses the authorized persistent profile but does not
        change the session state, submit credentials, inspect cookies, or solve
        any challenge. The helper exits after the user completes the visible
        challenge and the page returns to the requested product.
        """

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                current = self.status()
                current["manualPageOpened"] = False
                return current
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            project_python = (
                Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
            )
            python_executable = (
                str(project_python) if project_python.exists() else sys.executable
            )
            child_env = os.environ.copy()
            venv_site = project_python.parent.parent / "Lib" / "site-packages"
            if venv_site.exists():
                existing_python_path = child_env.get("PYTHONPATH", "")
                child_env["PYTHONPATH"] = os.pathsep.join(
                    part for part in (str(venv_site), existing_python_path) if part
                )
            if venv_site.exists():
                bootstrap = (
                    "import runpy,sys; "
                    f"sys.path.insert(0,{str(venv_site)!r}); "
                    "runpy.run_module('app.ecommerce_login', run_name='__main__')"
                )
                command = [python_executable, "-c", bootstrap]
            else:
                command = [python_executable, "-m", "app.ecommerce_login"]
            command.extend(
                [
                    "--mode",
                    "manual_page",
                    "--profile-dir",
                    str(self.profile_dir),
                    "--status-path",
                    str(self.status_path),
                    "--platform",
                    self.platform,
                    "--target-url",
                    target_url,
                ]
            )
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            log_path = self.profile_dir / "manual-page.log"
            log_handle = log_path.open("ab")
            self._process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=child_env,
            )
            log_handle.close()
            current = self.status()
            current["manualPageOpened"] = True
            current["message"] = (
                f"已打开{self.platform_label}官方页面，请在可见窗口中手动完成安全验证。"
            )
            return current

    def mark_needs_relogin(self, message: str | None = None) -> None:
        with self._lock:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(
                json.dumps(
                    {
                        "state": "needs_relogin"
                        if not self._canonical
                        else "login_required",
                        "message": message or self._message("relogin"),
                        "updatedAt": _utc_now(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def mark_ready(self, message: str | None = None) -> None:
        """Record a successful validation without persisting credential data."""

        with self._lock:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(
                json.dumps(
                    {
                        "state": "ready",
                        "message": message or self._message("ready"),
                        "updatedAt": _utc_now(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def mark_state(self, state: str, message: str) -> None:
        """Record a non-secret canonical state reported by a page inspection."""

        allowed = {
            "not_initialized",
            "ready",
            "login_required",
            "risk_controlled",
            "unavailable",
        }
        if not self._canonical or state not in allowed:
            raise ValueError("invalid session state")
        with self._lock:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(
                json.dumps(
                    {"state": state, "message": message, "updatedAt": _utc_now()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
