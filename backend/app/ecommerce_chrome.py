"""Run a user-visible installed Chrome and attach to it through localhost CDP.

This module intentionally contains no fingerprint-masking or challenge-solving
logic. It exists so the app's human-assisted 1688 flow uses the same kind of
branded Chrome process as a user-operated browser instead of a Playwright-owned
persistent browser.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Literal
from urllib.request import urlopen


def _chrome_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("POLARIS_CHROME_PATH", "").strip()
    if configured:
        candidates.append(Path(configured))
    for name in ("chrome.exe", "chrome"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))
    for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(name)
        if root:
            candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    # The browser-enabled production image ships Playwright's pinned Chrome
    # for Testing under /ms-playwright rather than exposing a `chrome` PATH
    # entry. Prefer that bundled binary when no system Chrome is installed.
    candidates.extend(
        Path("/ms-playwright").glob("chromium-*/chrome-linux64/chrome")
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved_path = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved_path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(resolved_path)
    return unique


def find_chrome_executable() -> Path:
    for candidate in _chrome_candidates():
        if candidate.is_file():
            return candidate
    raise RuntimeError("BRANDED_CHROME_UNAVAILABLE")


def _find_headless_shell_executable() -> Path | None:
    """Return Playwright's headless shell when running without a display.

    Chrome for Testing exits during startup in the production container while
    initializing Crashpad; Playwright's companion headless shell is shipped
    with the same browser bundle and supports the CDP attach flow we need.
    """

    for candidate in Path("/ms-playwright").glob(
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_cdp(port: int, process: subprocess.Popen[Any], timeout: float) -> str:
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("BRANDED_CHROME_EXITED")
        try:
            with urlopen(f"{endpoint}/json/version", timeout=0.5) as response:
                payload = json.load(response)
            if isinstance(payload, dict) and payload.get("Browser"):
                return endpoint
        except Exception as exc:  # startup races are expected here
            last_error = exc
        time.sleep(0.1)
    detail = f": {type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"BRANDED_CHROME_CDP_TIMEOUT{detail}")


ChromeNetworkMode = Literal["direct", "system"]


def _network_args(network_mode: ChromeNetworkMode) -> list[str]:
    """Return proxy flags for the isolated browser process.

    Domestic ecommerce sessions must not inherit the workstation's system
    proxy.  Keeping this at the browser-process boundary is safer than a
    global ``PROCESS-NAME,chrome.exe,DIRECT`` rule, which would also bypass
    proxying for the user's unrelated Chrome windows.
    """

    if network_mode == "direct":
        return ["--no-proxy-server"]
    if network_mode == "system":
        return []
    raise ValueError(f"unsupported Chrome network mode: {network_mode}")


def start_owned_chrome(
    profile_dir: str | Path,
    timeout: float = 15.0,
    *,
    network_mode: ChromeNetworkMode = "system",
    headless: bool = False,
) -> tuple[subprocess.Popen[Any], str]:
    """Start a local, visible Chrome process using only the supplied profile.

    ``network_mode=direct`` is used by domestic ecommerce flows so this
    dedicated profile bypasses system proxy settings without changing the
    routing of any other browser or application.
    """

    profile = Path(profile_dir).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    executable = find_chrome_executable()
    if headless:
        executable = _find_headless_shell_executable() or executable
    port = _free_local_port()
    command = [
        str(executable),
        f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        *(
            [
                "--headless=new",
                "--disable-gpu",
                "--window-size=1440,1100",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
            if headless
            else []
        ),
        *_network_args(network_mode),
    ]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        endpoint = _wait_for_cdp(port, process, timeout)
    except Exception:
        _stop_owned_chrome(process)
        raise
    return process, endpoint


def _stop_owned_chrome(process: subprocess.Popen[Any]) -> None:
    """Stop only the Chrome process owned by this fetch attempt.

    The normal cleanup path waits for the browser's own close command to let
    Chromium flush its profile (including cookies) before falling back to a
    hard process stop.
    """

    if process.poll() is not None:
        return
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    with suppress(Exception):
        process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        with suppress(Exception):
            process.kill()


def _close_owned_browser(browser: Any, process: subprocess.Popen[Any]) -> None:
    """Close the CDP session, then give Chromium time to exit cleanly."""

    with suppress(Exception):
        browser.close()
    _stop_owned_chrome(process)


@contextmanager
def connect_to_owned_chrome(
    playwright: Any,
    profile_dir: str | Path,
    *,
    network_mode: ChromeNetworkMode = "system",
    headless: bool = False,
) -> Iterator[tuple[Any, Any]]:
    """Yield ``(browser, context)`` for a user-visible Chrome process.

    Playwright is used only as a CDP client. Closing the context disconnects the
    client first; the process that this function started is then stopped. The
    CDP endpoint is bound to loopback only and is never returned to the API.
    """

    process, endpoint = start_owned_chrome(
        profile_dir, network_mode=network_mode, headless=headless
    )
    browser = None
    try:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        contexts = browser.contexts
        context = contexts[0] if contexts else browser.new_context(locale="zh-CN")
        yield browser, context
    finally:
        if browser is not None:
            _close_owned_browser(browser, process)
        else:
            _stop_owned_chrome(process)
