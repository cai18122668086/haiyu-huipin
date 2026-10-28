"""Human-assisted login helper for one authorized ecommerce browser profile."""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

from .ecommerce_chrome import connect_to_owned_chrome

_PLATFORM_LABELS = {
    "taobao": "淘宝",
    "tmall": "天猫",
    "alibaba1688": "1688",
    "1688": "1688",
    "jd": "京东",
    "pinduoduo": "拼多多",
}
_LOGIN_MARKERS = (
    "请登录",
    "登录后查看",
    "扫码登录",
    "密码登录",
    "1688登录",
    "淘宝登录",
    "京东登录",
    "登录京东",
    "拼多多登录",
    "登录拼多多",
)
_RISK_MARKERS = (
    "验证码",
    "滑块验证",
    "访问受限",
    "请求异常",
    "安全验证",
)
AUTHENTICATED_PAGE_MARKERS = (
    "退出登录",
    "log out",
    "sign out",
)
_1688_AUTHENTICATED_PAGE_MARKERS = (
    *AUTHENTICATED_PAGE_MARKERS,
    "我的阿里",
    "会员中心",
    "采购车",
)
_1688_AUTHENTICATED_HOSTS = (
    "member.1688.com",
    "work.1688.com",
)
_JD_SESSION_CHECK_URL = "https://order.jd.com/center/list.action"
_JD_ORDER_PAGE_MARKERS = ("订单中心", "近3个月订单")
_JD_ACCOUNT_SHELL_MARKERS = (
    "我的订单",
    "我的钱包",
    "账户设置",
    "足迹收藏",
    "客户服务",
    "我的活动",
    "特色业务",
    "生活服务",
)
_JD_LOGIN_FLOW_MARKERS = ("扫码登录", "密码登录", "登录京东", "京东登录")
_PDD_AUTHENTICATED_HOSTS = (
    "mobile.yangkeduo.com",
    "yangkeduo.com",
    "pinduoduo.com",
)
_PDD_AUTHENTICATED_PAGE_MARKERS = (
    *AUTHENTICATED_PAGE_MARKERS,
    "个人中心",
    "我的订单",
    "我的拼多多",
    "账户中心",
)
_LOGIN_URLS = {
    "taobao": "https://login.taobao.com/",
    "tmall": "https://login.taobao.com/",
    "alibaba1688": "https://login.1688.com/",
    "1688": "https://login.1688.com/",
    "jd": "https://passport.jd.com/new/login.aspx",
    "pinduoduo": "https://mobile.yangkeduo.com/",
}


def _default_login_url(platform: str) -> str:
    return _LOGIN_URLS.get(platform, _LOGIN_URLS["taobao"])


def _platform_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(platform, platform)


def looks_like_risk_control_page(current_url: str, title: str, body: str) -> bool:
    """Detect visible CAPTCHA or access-control messaging without bypassing it."""

    haystack = " ".join((current_url, title, body)).lower()
    return any(marker.lower() in haystack for marker in _RISK_MARKERS)


def looks_like_authenticated_page(
    current_url: str,
    title: str,
    body: str,
    platform: str = "taobao",
) -> bool:
    """Detect a visible authenticated account page without reading credentials."""

    lowered_url = current_url.lower()
    lowered_title = title.strip().lower()
    lowered_body = " ".join(body.split()).lower()
    parsed_url = urlparse(lowered_url)
    hostname = parsed_url.hostname or ""
    if not hostname:
        return False
    # Risk-control content always wins over an account-page signal. Some
    # 1688 challenge pages keep account navigation text in the DOM while the
    # challenge is displayed, so checking this first avoids false ``ready``.
    if looks_like_risk_control_page(current_url, title, body):
        return False
    if platform in {"alibaba1688", "1688"}:
        body_has_login_marker = any(
            marker.lower() in lowered_body for marker in _LOGIN_MARKERS
        )
        if body_has_login_marker or lowered_title in {
            "登录",
            "1688登录",
            "login",
            "sign in",
        }:
            return False
        is_1688_account_host = any(
            hostname == host or hostname.endswith(f".{host}")
            for host in _1688_AUTHENTICATED_HOSTS
        )
        if is_1688_account_host:
            # work.1688.com is the normal post-scan landing page and can
            # briefly render with little/no text, so its trusted host is
            # sufficient once login/risk signals are absent. The member
            # domain is more conservative and requires an account marker.
            if hostname == "work.1688.com" or hostname.endswith(".work.1688.com"):
                return True
            return any(
                marker.lower() in lowered_body
                for marker in _1688_AUTHENTICATED_PAGE_MARKERS
            )

        # A successful scan can stop at the official jump URL before landing
        # on member/work. It still contains ``login.1688.com`` in its host,
        # therefore it must be handled explicitly instead of by the generic
        # login-URL rejection below.
        is_member_jump = hostname == "login.1688.com" and (
            parsed_url.path.rstrip("/").lower() == "/member/jump.htm"
            or "member_operations_jump_engine.htm" in parsed_url.path.lower()
        )
        if is_member_jump:
            return any(
                marker.lower() in lowered_body
                for marker in _1688_AUTHENTICATED_PAGE_MARKERS
            )
        return False

    if platform == "pinduoduo":
        if any(
            marker in lowered_url for marker in ("login", "passport", "signin", "auth")
        ):
            return False
        if lowered_title in {"登录", "拼多多登录", "login", "sign in"}:
            return False
        if any(marker.lower() in lowered_body for marker in _LOGIN_MARKERS):
            return False
        is_pdd_host = any(
            hostname == host or hostname.endswith(f".{host}")
            for host in _PDD_AUTHENTICATED_HOSTS
        )
        return is_pdd_host and any(
            marker.lower() in lowered_body
            for marker in _PDD_AUTHENTICATED_PAGE_MARKERS
        )

    if platform == "jd":
        if any(
            marker in lowered_url for marker in ("login", "passport", "signin", "auth")
        ):
            return False
        if lowered_title in {"登录", "京东登录", "login", "sign in"}:
            return False

        # 京东商品页的顶部导航可能在已经有效的会话中仍然显示
        # ``你好，请登录``，因此不能把这个文案作为全站的唯一否定信号。
        # 订单中心是更可靠的、同一会话下的官方验证页：匿名访问也能看到
        # 页面骨架，但不会渲染带订单号的订单卡片。只使用布尔页面信号，
        # 不保存或返回订单内容。
        is_jd_order_page = hostname == "order.jd.com" and all(
            marker.lower() in lowered_body for marker in _JD_ORDER_PAGE_MARKERS
        )
        has_order_card = bool(re.search(r"订单号\s*\d{8,}", lowered_body))
        # JD's public header often contains “你好，请登录” even after an
        # authenticated redirect. Treat a greeting with an actual account
        # name as the positive signal, while ignoring that public prompt.
        has_account_greeting = bool(
            re.search(r"你好[，,](?!请登录)([^\s，,]{1,40})", lowered_body)
        )
        has_account_shell = sum(
            marker.lower() in lowered_body for marker in _JD_ACCOUNT_SHELL_MARKERS
        ) >= 2
        has_login_flow_marker = any(
            marker.lower() in lowered_body for marker in _JD_LOGIN_FLOW_MARKERS
        )
        if is_jd_order_page and (
            has_order_card
            or has_account_greeting
            or (has_account_shell and not has_login_flow_marker)
        ):
            return True

        if any(marker.lower() in lowered_body for marker in AUTHENTICATED_PAGE_MARKERS):
            return True
        if any(marker.lower() in lowered_body for marker in _LOGIN_MARKERS):
            return False
        return hostname in {"www.jd.com", "jd.com", "home.jd.com"} and any(
            marker.lower() in lowered_body
            for marker in ("退出登录", "账户中心")
        )

    if any(
        marker in lowered_url
        for marker in ("login", "passport", "signin", "sign-in", "auth")
    ):
        return False
    if lowered_title in {"登录", "淘宝登录", "login", "sign in"}:
        return False
    if any(marker.lower() in lowered_body for marker in _LOGIN_MARKERS):
        return False
    if hostname == "i.taobao.com" and "my_taobao" in parsed_url.path:
        return True
    if hostname.endswith("buyertrade.taobao.com"):
        return True
    return any(marker.lower() in lowered_body for marker in AUTHENTICATED_PAGE_MARKERS)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(path: Path, state: str, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"state": state, "message": message, "updatedAt": _now()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_closed_status(path: Path, message: str) -> None:
    """Record a manual-window close without downgrading a validated session."""

    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        current = {}
    if isinstance(current, dict) and current.get("state") == "ready":
        return
    _write(path, "signed_out", message)


def _write_preview(page: Page, screenshot_path: Path | None) -> None:
    """Persist a short-lived browser preview without exposing browser state."""

    if screenshot_path is None:
        return
    try:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = screenshot_path.with_suffix(".tmp.png")
        page.screenshot(  # type: ignore[attr-defined]
            path=str(temporary_path),
            type="png",
            animations="disabled",
            timeout=5_000,
        )
        temporary_path.replace(screenshot_path)
    except Exception:
        # Preview delivery is best effort; login state detection must continue.
        return


def _start_screencast(page: Page, screencast_path: Path | None) -> object | None:
    """Start a CDP JPEG screencast and atomically publish the newest frame.

    The file is only a loopback IPC handoff between the isolated helper and
    the authenticated API process; it never contains browser state beyond
    the pixels already visible in the login preview.
    """

    if screencast_path is None:
        return None
    try:
        context = page.context  # type: ignore[attr-defined]
        cdp = context.new_cdp_session(page)
        screencast_path.parent.mkdir(parents=True, exist_ok=True)

        def on_frame(params: dict[str, object]) -> None:
            try:
                data = base64.b64decode(str(params.get("data", "")), validate=True)
                if not data:
                    return
                temporary = screencast_path.with_suffix(".tmp.jpg")
                temporary.write_bytes(data)
                temporary.replace(screencast_path)
                sequence = params.get("sessionId")
                if sequence is not None:
                    cdp.send("Page.screencastFrameAck", {"sessionId": sequence})
            except Exception:
                return

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 82, "maxWidth": 1600, "maxHeight": 1200},
        )
        return cdp
    except Exception:
        return None


def _stop_screencast(cdp: object | None) -> None:
    if cdp is None:
        return
    with suppress(Exception):
        cdp.send("Page.stopScreencast")  # type: ignore[attr-defined]


def _drain_input_actions(page: Page, actions_dir: Path | None) -> None:
    """Apply user-originated input events from the protected action queue."""

    if actions_dir is None or not actions_dir.is_dir():
        return
    for action_path in sorted(actions_dir.glob("*.json")):
        try:
            action = json.loads(action_path.read_text(encoding="utf-8"))
            action_path.unlink(missing_ok=True)
            if not isinstance(action, dict):
                continue
            kind = action.get("type")
            if kind == "click":
                page.mouse.click(
                    float(action["x"]),
                    float(action["y"]),
                    button=action.get("button", "left"),
                )
            elif kind == "drag":
                page.mouse.move(float(action["fromX"]), float(action["fromY"]))
                page.mouse.down()
                page.mouse.move(float(action["toX"]), float(action["toY"]))
                page.mouse.up()
            elif kind == "wheel":
                page.mouse.wheel(
                    float(action.get("deltaX", 0)), float(action.get("deltaY", 0))
                )
            elif kind == "key":
                page.keyboard.press(str(action["key"]))
            elif kind == "text":
                page.keyboard.insert_text(str(action["text"]))
        except Exception:
            with suppress(Exception):
                action_path.unlink(missing_ok=True)
def _read_page_snapshot(page: Page) -> tuple[str, str, str]:
    """Read only URL/title/visible text needed for session state detection."""

    current_url = page.url  # type: ignore[attr-defined]
    title = page.title() or ""  # type: ignore[attr-defined]
    body = " ".join(
        page.locator("body").inner_text(timeout=1_500).split()  # type: ignore[attr-defined]
    )
    return current_url, title, body


def _close_extra_pages(context: BrowserContext, keep_page: Page) -> None:
    """Keep the dedicated human-facing profile to one visible tab.

    This only operates on the isolated Chrome profile owned by Polaris. It
    never touches the user's ordinary Chrome windows or tabs.
    """

    for candidate in list(context.pages):  # type: ignore[attr-defined]
        if candidate is keep_page:
            continue
        with suppress(Exception):
            if not candidate.is_closed():
                candidate.close()


def _probe_pinduoduo_session(current_page: Page) -> bool:
    """Validate an existing PDD profile on its official home page."""

    try:
        current_page.goto(
            _default_login_url("pinduoduo"),
            wait_until="domcontentloaded",
            timeout=15_000,
        )  # type: ignore[attr-defined]
        for _ in range(3):
            current_url, title, body = _read_page_snapshot(current_page)
            if looks_like_authenticated_page(
                current_url, title, body, platform="pinduoduo"
            ):
                return True
            time.sleep(0.75)
    except Exception:
        return False
    return False


def _probe_jd_session(
    context: BrowserContext,
    current_page: Page,
    probe_page: Page | None = None,
) -> tuple[Page | None, bool]:
    """Validate JD login in the current tab, without opening a second tab.

    The order center is a more reliable authentication signal than a product
    page, but creating a background tab made the human-facing login flow look
    like two unrelated pages. Reusing ``current_page`` keeps the dedicated
    profile to one tab. ``probe_page`` remains accepted for compatibility with
    callers from the previous implementation, but is deliberately ignored.
    """

    del context, probe_page
    try:
        with suppress(Exception):
            current_page.goto(  # type: ignore[attr-defined]
                _JD_SESSION_CHECK_URL,
                wait_until="domcontentloaded",
                timeout=15_000,
            )
        for attempt in range(3):
            probe_url, probe_title, probe_body = _read_page_snapshot(current_page)
            authenticated = looks_like_authenticated_page(
                probe_url, probe_title, probe_body, platform="jd"
            )
            if authenticated or attempt == 2:
                return current_page, authenticated
            # The order list is client-rendered. Give its account header a
            # short same-tab settling window instead of opening a background
            # tab or treating an intermediate skeleton as a failed session.
            time.sleep(0.75)
        return current_page, False
    except Exception:
        return None, False


def _run_manual_page(args: argparse.Namespace) -> int:
    """Open a visible official page and wait for a human to finish a challenge."""

    label = _platform_label(args.platform)
    profile_dir = Path(args.profile_dir)
    status_path = Path(args.status_path)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return 1

    challenge_seen = False
    try:
        with (
            sync_playwright() as playwright,
            connect_to_owned_chrome(
                playwright,
                profile_dir,
                network_mode="direct",
                headless=args.headless,
            ) as (_browser, context),
        ):
            # Use a normal installed Chrome process for the human-facing
            # challenge. Playwright only attaches over loopback CDP; it does
            # not drag the slider, inject stealth code, or replay a challenge.
            page = context.pages[0] if context.pages else context.new_page()
            with suppress(PlaywrightTimeoutError):
                page.goto(
                    args.target_url, wait_until="domcontentloaded", timeout=45_000
                )
            for _ in range(max(1, args.timeout_seconds)):
                if not context.pages or page.is_closed():
                    break
                try:
                    current_url = page.url
                    title = page.title() or ""
                    body = " ".join(
                        page.locator("body").inner_text(timeout=5_000).split()
                    )
                except (PlaywrightTimeoutError, PlaywrightError):
                    if page.is_closed():
                        break
                    time.sleep(0.5)
                    continue
                blocked_page = (
                    looks_like_risk_control_page(current_url, title, body)
                    or "/_____tmd_____/punish" in current_url.lower()
                )
                challenge_seen = challenge_seen or blocked_page
                if challenge_seen and not blocked_page:
                    parsed = urlparse(current_url)
                    if parsed.hostname and parsed.hostname.endswith("1688.com"):
                        _write(
                            status_path,
                            "ready",
                            f"{label}官方安全验证已完成，可以重新拉取商品。",
                        )
                        return 0
                time.sleep(0.5)
    except Exception:
        # The original session state is intentionally left untouched.
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("login", "manual_page"), default="login")
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--target-url")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the authorized login profile without a local display.",
    )
    parser.add_argument("--screenshot-path", type=Path)
    parser.add_argument("--screencast-path", type=Path)
    parser.add_argument("--actions-dir", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--platform",
        default="taobao",
        choices=("taobao", "tmall", "alibaba1688", "1688", "jd", "pinduoduo"),
    )
    args = parser.parse_args()
    if args.mode == "manual_page":
        return _run_manual_page(args)
    label = _platform_label(args.platform)
    target_url = args.target_url or _default_login_url(args.platform)
    profile_dir = Path(args.profile_dir)
    status_path = Path(args.status_path)
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        surface = "登录预览" if args.headless else "登录窗口"
        _write(status_path, "error", f"{label}{surface}启动失败：Playwright 未安装。")
        return 1
    _write(
        status_path,
        "login_pending",
        (
            f"请在登录预览中使用官方二维码完成{label}登录。"
            if args.headless
            else f"请在弹出的{label}窗口中完成登录。"
        ),
    )
    last_valid_page = False
    blocked_seen = False
    authenticated_streak = 0
    jd_probe_page: Page | None = None
    jd_login_page_seen = False
    jd_probe_attempted_since_login = False
    try:
        with (
            sync_playwright() as playwright,
            connect_to_owned_chrome(
                playwright,
                profile_dir,
                network_mode="direct",
                headless=args.headless,
            ) as (_browser, context),
        ):
            # Use a normal installed Chrome process for the human-facing
            # login and any official challenge. Playwright only attaches over
            # loopback CDP; it does not solve or bypass the challenge.
            page = context.pages[0] if context.pages else context.new_page()
            _close_extra_pages(context, page)
            screencast = _start_screencast(page, args.screencast_path)

            # A persistent profile can already contain the user's logged-in
            # account page. Inspect it before navigating to the login origin;
            # otherwise a helper restart would overwrite the very page that
            # proves the session is valid. For JD, if the last page is merely a
            # new tab or a stale login page, validate the same profile through
            # the official order center in this same tab before asking for a
            # new login.
            if args.platform == "jd":
                try:
                    candidate_url, candidate_title, candidate_body = (
                        _read_page_snapshot(page)
                    )
                except (PlaywrightTimeoutError, PlaywrightError):
                    candidate_url = candidate_title = candidate_body = ""
                if looks_like_authenticated_page(
                    candidate_url,
                    candidate_title,
                    candidate_body,
                    platform="jd",
                ):
                    _write(
                        status_path,
                        "ready",
                        f"检测到已有{label}登录会话，无需再次登录。",
                    )
                    return 0
                jd_probe_page, jd_probe_authenticated = _probe_jd_session(
                    context, page
                )
                if jd_probe_authenticated:
                    _write(
                        status_path,
                        "ready",
                        f"检测到已有{label}登录会话，无需再次登录。",
                    )
                    return 0
            elif args.platform == "pinduoduo" and _probe_pinduoduo_session(page):
                _write(
                    status_path,
                    "ready",
                    f"检测到已有{label}登录会话，无需再次登录。",
                )
                return 0

            with suppress(PlaywrightTimeoutError):
                page.goto(
                    target_url,
                    wait_until="commit" if args.headless else "domcontentloaded",
                    timeout=15_000 if args.headless else 45_000,
                )
            if args.platform == "jd":
                # The page is now the only login surface. Wait for a real
                # post-login navigation before checking the order center again;
                # this prevents the initial probe from opening (or repeatedly
                # reloading) a second visible page while the user is scanning.
                jd_login_page_seen = True
                jd_probe_attempted_since_login = False
            # Honor the caller-provided timeout instead of the historical
            # fixed 600-iteration (~120 second) limit.  Browser/network
            # probes can take longer than the nominal 200 ms sleep, so use a
            # monotonic deadline rather than converting seconds to a loop
            # count.
            login_deadline = time.monotonic() + max(1, args.timeout_seconds)
            iteration = 0
            while time.monotonic() < login_deadline:
                iteration += 1
                _drain_input_actions(page, args.actions_dir)
                try:
                    if not context.pages or page.is_closed():
                        break
                    current_url = page.url.lower()
                    title = (page.title() or "").lower()
                    body = " ".join(
                        page.locator("body").inner_text(timeout=1_500).split()
                    ).lower()
                except (PlaywrightTimeoutError, PlaywrightError):
                    if page.is_closed():
                        break
                    body = ""
                # CDP screencast frames are the primary transport. Keep the
                # PNG snapshot as a low-rate fallback for older clients.
                if args.screenshot_path is not None and (iteration % 10 == 0):
                    _write_preview(page, args.screenshot_path)
                login_page = (
                    any(
                        marker in current_url
                        for marker in ("login", "passport", "signin", "auth")
                    )
                    or title in {"登录", "淘宝登录", "1688登录"}
                    or any(marker.lower() in body for marker in _LOGIN_MARKERS)
                )
                # The JD order center retains the public “你好，请登录”
                # navigation text while rendering an authenticated account.
                # Do not let that shared header keep the helper in the login
                # state after the browser has left the passport page.
                if (
                    args.platform == "jd"
                    and urlparse(page.url).hostname == "order.jd.com"
                ):
                    login_page = (
                        any(
                            marker in current_url
                            for marker in ("login", "passport", "signin", "auth")
                        )
                        or title in {"登录", "京东登录", "login", "sign in"}
                    )
                blocked_page = looks_like_risk_control_page(page.url, title, body)
                blocked_seen = blocked_seen or blocked_page
                authenticated_page = looks_like_authenticated_page(
                    page.url, title, body, args.platform
                )
                if args.platform == "jd":
                    if login_page:
                        jd_login_page_seen = True
                        jd_probe_attempted_since_login = False
                    elif (
                        jd_login_page_seen
                        and not jd_probe_attempted_since_login
                        and not blocked_page
                    ):
                        # Confirm only after the login page has actually
                        # navigated away. The probe reuses ``page`` so the
                        # helper never exposes a second tab.
                        jd_probe_page, jd_probe_authenticated = _probe_jd_session(
                            context, page, jd_probe_page
                        )
                        authenticated_page = (
                            authenticated_page or jd_probe_authenticated
                        )
                        jd_probe_attempted_since_login = True
                if authenticated_page:
                    # QR login can briefly pass through the official jump URL
                    # while the account page is still loading. Require two
                    # consecutive authenticated observations so we persist the
                    # session only after the redirect has settled.
                    authenticated_streak += 1
                    if authenticated_streak >= 2:
                        _write(
                            status_path,
                            "ready",
                            f"检测到已有{label}登录会话，无需再次登录。",
                        )
                        return 0
                else:
                    authenticated_streak = 0
                last_valid_page = (
                    not login_page
                    and not blocked_page
                    and bool(urlparse(page.url).hostname)
                )
                _write(
                    status_path,
                    "login_pending",
                    (
                        f"登录页面已打开；完成{label}登录后等待系统确认。"
                        if last_valid_page
                        else f"请在登录预览中完成{label}登录。"
                    )
                    if args.headless
                    else (
                        f"登录页面已打开；完成{label}登录后请关闭此窗口。"
                        if last_valid_page
                        else f"请在弹出的{label}窗口中完成登录。"
                    ),
                )
                time.sleep(0.2)
            else:
                _write(
                    status_path, "signed_out", f"{label}登录等待超时，请重新发起登录。"
                )
                return 0
            if blocked_seen:
                _write(
                    status_path,
                    "risk_controlled",
                    f"{label}登录触发安全验证或风控，请稍后通过官方页面完成验证。",
                )
            else:
                _write_closed_status(
                    status_path,
                    "登录预览已关闭，请重新发起登录。"
                    if args.headless
                    else "登录窗口已关闭，请重新发起登录。",
                )
            _stop_screencast(screencast)
            with suppress(PlaywrightError):
                context.close()
    except PlaywrightError as exc:  # pragma: no cover - local browser runtime
        if "TargetClosed" in type(exc).__name__:
            if blocked_seen:
                _write(
                    status_path,
                    "risk_controlled",
                    f"{label}登录触发安全验证或风控，请稍后通过官方页面完成验证。",
                )
            else:
                _write_closed_status(
                    status_path,
                    "登录预览已关闭，请重新发起登录。"
                    if args.headless
                    else "登录窗口已关闭，请重新发起登录。",
                )
            return 0
        surface = "登录预览" if args.headless else "登录窗口"
        _write(status_path, "error", f"{label}{surface}启动失败：{type(exc).__name__}")
        return 1
    except Exception as exc:  # pragma: no cover - depends on local browser runtime
        surface = "登录预览" if args.headless else "登录窗口"
        _write(status_path, "error", f"{label}{surface}启动失败：{type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
