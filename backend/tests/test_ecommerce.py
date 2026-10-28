from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import app.ecommerce_routes as ecommerce_routes
from app.ecommerce_fetcher import Image, Product, parse_product_html
from app.ecommerce_login import (
    _drain_input_actions,
    looks_like_authenticated_page,
    looks_like_risk_control_page,
)
from app.ecommerce_session import EcommerceSessionManager


def test_login_input_actions_are_applied_and_consumed(tmp_path: Path) -> None:
    class Mouse:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def click(self, *args: object, **kwargs: object) -> None:
            self.calls.append(("click", (args, kwargs)))

        def wheel(self, *args: object) -> None:
            self.calls.append(("wheel", args))

    class Keyboard:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def press(self, key: str) -> None:
            self.calls.append(("press", key))

        def insert_text(self, text: str) -> None:
            self.calls.append(("text", text))

    class Page:
        def __init__(self) -> None:
            self.mouse = Mouse()
            self.keyboard = Keyboard()

    actions = tmp_path / "actions"
    actions.mkdir()
    (actions / "1.json").write_text('{"type":"click","x":12,"y":34}')
    (actions / "2.json").write_text('{"type":"text","text":"abc"}')
    page = Page()

    _drain_input_actions(page, actions)

    assert len(page.mouse.calls) == 1
    assert page.keyboard.calls == [("text", "abc")]
    assert list(actions.glob("*.json")) == []


def test_login_helper_passes_headless_mode_to_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production QR transport must start Chrome without a display."""

    import app.ecommerce_login as ecommerce_login

    captured: dict[str, object] = {}

    class FakePlaywrightContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self.page = FakePage()

        @property
        def pages(self) -> list[object]:
            return [self.page]

        def new_page(self) -> object:
            return self.page

    class FakePage:
        closed = False

        def goto(self, *_args: object, **_kwargs: object) -> None:
            self.closed = True

        def is_closed(self) -> bool:
            return self.closed

    class FakeChromeConnection:
        def __enter__(self) -> tuple[object, FakeContext]:
            return object(), FakeContext()

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_connect(*_args: object, **kwargs: object) -> FakeChromeConnection:
        captured.update(kwargs)
        return FakeChromeConnection()

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = Exception
    sync_api.TimeoutError = TimeoutError
    sync_api.sync_playwright = lambda: FakePlaywrightContext()
    playwright_package = types.ModuleType("playwright")
    playwright_package.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(ecommerce_login, "connect_to_owned_chrome", fake_connect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ecommerce_login",
            "--profile-dir",
            str(tmp_path / "profile"),
            "--status-path",
            str(tmp_path / "status.json"),
            "--headless",
            "--timeout-seconds",
            "1",
        ],
    )

    assert ecommerce_login.main() == 0
    assert captured["headless"] is True


def test_existing_authenticated_page_is_detected_without_credentials() -> None:
    assert looks_like_authenticated_page(
        "https://www.taobao.com/",
        "淘宝网",
        "我的淘宝 购物车 收藏夹 退出登录",
    )
    assert looks_like_authenticated_page(
        "https://i.taobao.com/my_taobao.htm",
        "我的淘宝",
        "账户中心",
    )


def test_1688_authenticated_landing_pages_are_detected() -> None:
    assert looks_like_authenticated_page(
        "https://work.1688.com/",
        "阿里巴巴1688",
        "工作台 采购车 消息",
        platform="alibaba1688",
    )
    assert looks_like_authenticated_page(
        "https://member.1688.com/member/operations/member_operations_jump_engine.htm",
        "会员中心",
        "我的阿里 会员中心 退出登录",
        platform="1688",
    )
    assert looks_like_authenticated_page(
        "https://login.1688.com/member/jump.htm",
        "1688",
        "我的阿里 采购车 退出登录",
        platform="alibaba1688",
    )


def test_1688_public_or_login_pages_are_not_treated_as_authenticated() -> None:
    assert not looks_like_authenticated_page(
        "https://www.1688.com/",
        "阿里巴巴",
        "批发采购 商品详情",
        platform="alibaba1688",
    )
    assert not looks_like_authenticated_page(
        "https://login.1688.com/",
        "1688登录",
        "扫码登录 密码登录",
        platform="alibaba1688",
    )
    assert not looks_like_authenticated_page(
        "https://work.1688.com/",
        "安全验证",
        "退出登录 请完成验证码",
        platform="alibaba1688",
    )


def test_existing_pinduoduo_home_session_is_detected() -> None:
    assert looks_like_authenticated_page(
        "https://mobile.yangkeduo.com/",
        "拼多多",
        "个人中心 商品详情",
        platform="pinduoduo",
    )
    assert not looks_like_authenticated_page(
        "https://mobile.yangkeduo.com/",
        "拼多多",
        "请登录 扫码登录",
        platform="pinduoduo",
    )


def test_public_product_page_is_not_treated_as_authenticated() -> None:
    assert not looks_like_authenticated_page(
        "https://item.taobao.com/item.htm?id=123456789012",
        "商品详情",
        "商品标题 ¥2090 立即购买",
    )

    assert not looks_like_authenticated_page(
        "https://www.taobao.com/",
        "淘宝网",
        "我的淘宝 购物车 收藏夹",
    )


def test_risk_control_page_is_detected_from_url_or_title() -> None:
    assert looks_like_risk_control_page(
        "https://login.1688.com/verify", "安全验证", "请完成验证"
    )
    assert looks_like_risk_control_page(
        "https://login.1688.com/", "1688", "访问受限，请稍后重试"
    )


def test_login_or_risk_page_is_never_treated_as_authenticated() -> None:
    assert not looks_like_authenticated_page(
        "https://login.taobao.com/",
        "登录",
        "我的淘宝 购物车 退出登录",
    )
    assert not looks_like_authenticated_page(
        "https://www.taobao.com/",
        "淘宝网",
        "我的淘宝 购物车 验证码",
    )


def test_ready_profile_is_reused_without_starting_login(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    status_path = profile / "session-status.json"
    status_path.write_text(
        json.dumps({"state": "ready", "message": "已有会话"}),
        encoding="utf-8",
    )

    result = EcommerceSessionManager(profile).start_login()

    assert result["state"] == "ready"
    assert result["profileReady"] is True


def test_login_risk_html_discards_all_images() -> None:
    html = """
    <html><head><meta property="og:title" content="登录"></head>
    <body><img src="https://img.alicdn.com/product-800x800.jpg">
    <p>请登录后查看</p></body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert product.title is None
    assert product.images == []
    assert product.access_state == "login_required"
    assert any("登录或风控" in warning for warning in product.warnings)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            '<html><script>var login_jump="https://login.taobao.com"</script></html>',
            "login_required",
        ),
        ('<html><script>var x5secdata="challenge"</script></html>', "risk_controlled"),
        ('<html><body>验证码</body></html>', "captcha_required"),
    ],
)
def test_taobao_interstitial_markers_have_explicit_access_state(
    html: str, expected: str
) -> None:
    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )
    assert product.access_state == expected
    assert product.images == []


def test_fetch_item_surfaces_login_required_instead_of_parse_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = Product(
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
        "taobao",
        "123456789012",
        None,
        None,
        warnings=["平台返回了登录或风控页面，未读取到商品数据。"],
        access_state="login_required",
    )
    monkeypatch.setattr(ecommerce_routes, "crawl_one", lambda *args: blocked)

    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": blocked.source_url, "platform": "taobao"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "LOGIN_REQUIRED"


def test_image_filter_removes_small_platform_assets_but_keeps_product_images() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <img src="https://img.alicdn.com/tps-30-30.png">
      <img src="https://img.alicdn.com/product-800x800.jpg">
      <img src="https://img.alicdn.com/detail-1200x900.webp">
      <a href="https://pass.tmall.com/add?cookie1=redacted&target=https%3A%2F%2Fwww.tmall.com">跳转</a>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    urls = [image.url for image in product.images]
    assert "https://img.alicdn.com/tps-30-30.png" not in urls
    assert "https://img.alicdn.com/product-800x800.jpg" in urls
    assert "https://img.alicdn.com/detail-1200x900.webp" in urls
    assert not any(url.startswith("https://pass.") for url in urls)


def test_gallery_is_capped_at_six_and_resize_suffix_is_removed() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <div class="gallery">
        <img src="https://img.alicdn.com/gallery-1-800x800.jpg">
        <img src="https://img.alicdn.com/gallery-2-800x800.jpg">
        <img src="https://img.alicdn.com/gallery-3-800x800.jpg">
        <img src="https://img.alicdn.com/gallery-4-800x800.jpg">
        <img src="https://img.alicdn.com/gallery-5-800x800.jpg">
        <img src="https://img.alicdn.com/gallery-6-800x800.jpg">
        <img src="https://img.alicdn.com/auxiliary-800x800.jpg">
      </div>
      <div class="detail"><img src="https://img.alicdn.com/detail-1200x900.webp_760x760q30.jpg"></div>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    main = [image for image in product.images if image.role == "main"]
    detail = [image for image in product.images if image.role == "detail"]
    assert len(main) == 6
    assert not any("auxiliary-800x800.jpg" in image.url for image in detail)
    assert any("detail-1200x900.webp" in image.url for image in detail)
    assert "_760x760q30" not in detail[-1].url


def test_unclassified_long_description_overflow_is_reclassified_after_gallery() -> None:
    gallery = "".join(
        f'<img src="https://img.alicdn.com/gallery-{index}-800x800.jpg">'
        for index in range(1, 7)
    )
    unclassified_details = "".join(
        f'<img src="https://img.alicdn.com/imgextra/content-{index}-800x1400.jpg">'
        for index in range(1, 4)
    )
    html = f"""
    <html><head><title>示例商品</title></head><body>
      <div class="gallery">{gallery}</div>
      {unclassified_details}
      <div class="description-marker"><img src="https://img.alicdn.com/imgextra/desc-marker-1200x1800.jpg"></div>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images if image.role == "main"] == [
        f"https://img.alicdn.com/gallery-{index}-800x800.jpg"
        for index in range(1, 7)
    ]
    assert [image.url for image in product.images if image.role == "detail"] == [
        f"https://img.alicdn.com/imgextra/content-{index}-800x1400.jpg"
        for index in range(1, 4)
    ] + ["https://img.alicdn.com/imgextra/desc-marker-1200x1800.jpg"]
    assert product.image_diagnostics["reclassified_main_overflow_to_detail"] == 3


def test_detail_state_excludes_ui_tps_and_repeated_sku_assets() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <div class="gallery">
        <img src="https://img.alicdn.com/gallery-1-800x800.jpg">
        <img src="https://img.alicdn.com/gallery-2-800x800.jpg">
      </div>
      <div class="detail"><img src="https://img.alicdn.com/detail-long-1200x1800.jpg"></div>
      <script>
        window.__DATA__ = {
          "descImageUrls": [
            "https://img.alicdn.com/detail-long-1200x1800.jpg",
            "https://img.alicdn.com/imgextra/i2/O1CN01mYpkoM1iP5QDr8xjC_!!6000000004404-0-tps-4964-3510.jpg",
            "https://img.alicdn.com/sku-choice.jpg",
            "https://g.alicdn.com/s.gif"
          ],
          "skuImages": ["https://img.alicdn.com/sku-choice.jpg"]
        };
      </script>
      <img width="16" height="16" src="https://img.alicdn.com/page-icon.png">
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    detail_urls = [image.url for image in product.images if image.role == "detail"]
    assert detail_urls == ["https://img.alicdn.com/detail-long-1200x1800.jpg"]
    assert not any("sku-choice" in image.url for image in product.images)
    assert not any(
        "page-icon" in image.url or image.url.endswith("s.gif")
        for image in product.images
    )


def test_detail_state_keeps_uploaded_tps_content_and_reads_object_lists() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <script>
        window.__DATA__ = {
          "detailImageList": [
            {"url": "https://gw.alicdn.com/bao/uploaded/real-1-760x1200-tps-760-1200.jpg"},
            {"url": "https://img.alicdn.com/imgextra/i2/O1CN01ui-0-tps-4964-3510.jpg"},
            {"url": "https://gw.alicdn.com/bao/uploaded/real-2-760x1200-tps-760-1200.jpg"}
          ]
        };
      </script>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://gw.alicdn.com/bao/uploaded/real-1-760x1200-tps-760-1200.jpg",
        "https://gw.alicdn.com/bao/uploaded/real-2-760x1200-tps-760-1200.jpg",
    ]


def test_same_basename_in_different_cdn_paths_is_not_deduplicated() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <div class="detail-content">
        <img src="https://img.alicdn.com/first/detail.jpg_760x1200q30.jpg">
        <img src="https://img.alicdn.com/second/detail.jpg_760x1200q30.jpg">
      </div>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://img.alicdn.com/first/detail.jpg",
        "https://img.alicdn.com/second/detail.jpg",
    ]


def test_uploaded_asset_without_detail_context_does_not_become_detail() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <img src="https://gw.alicdn.com/bao/uploaded/page-asset-800x800.jpg">
      <div class="detail-content"><img src="https://img.alicdn.com/imgextra/real-detail-800x1200.jpg"></div>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://img.alicdn.com/imgextra/real-detail-800x1200.jpg"
    ]


def test_rendered_natural_dimensions_remove_thin_and_tiny_assets() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <img data-natural-width="1500" data-natural-height="2"
        src="https://img.alicdn.com/imgextra/strip.jpg">
      <img data-natural-width="1500" data-natural-height="16"
        src="https://img.alicdn.com/imgextra/badge.jpg">
      <img data-natural-width="790" data-natural-height="1247"
        src="https://img.alicdn.com/imgextra/detail.jpg">
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images] == [
        "https://img.alicdn.com/imgextra/detail.jpg"
    ]


def test_rejected_dom_asset_is_not_reintroduced_by_state_fallback() -> None:
    url = "https://img.alicdn.com/imgextra/strip.jpg"
    html = f"""
    <html><head><title>示例商品</title></head><body>
      <img data-natural-width="1500" data-natural-height="2" src="{url}">
      <script>window.__DATA__ = {{"descImageUrls": ["{url}"]}};</script>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert product.images == []


def test_state_uploaded_asset_is_detail_not_main_without_gallery_context() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <img src="https://img.alicdn.com/main-1-800x800.jpg">
      <img src="https://gw.alicdn.com/bao/uploaded/detail-1-800x1200.jpg">
      <script>
        window.__DATA__ = {
          "mainImageUrls": [
            "https://img.alicdn.com/main-1-800x800.jpg",
            "https://img.alicdn.com/main-2-800x800.jpg",
            "https://img.alicdn.com/main-3-800x800.jpg",
            "https://img.alicdn.com/main-4-800x800.jpg",
            "https://img.alicdn.com/main-5-800x800.jpg",
            "https://img.alicdn.com/main-6-800x800.jpg"
          ],
          "descImageUrls": ["https://gw.alicdn.com/bao/uploaded/detail-1-800x1200.jpg"]
        };
      </script>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    main_urls = [image.url for image in product.images if image.role == "main"]
    detail_urls = [image.url for image in product.images if image.role == "detail"]
    assert main_urls == [
        f"https://img.alicdn.com/main-{index}-800x800.jpg"
        for index in range(1, 7)
    ]
    assert detail_urls == ["https://gw.alicdn.com/bao/uploaded/detail-1-800x1200.jpg"]


def test_explicit_detail_dom_can_retain_uploaded_asset() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <div class="detail-content">
        <img src="https://gw.alicdn.com/bao/uploaded/detail-1-800x1200.jpg">
      </div>
      <script>
        window.__DATA__ = {
          "descImageUrls": ["https://gw.alicdn.com/bao/uploaded/detail-1-800x1200.jpg"]
        };
      </script>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://gw.alicdn.com/bao/uploaded/detail-1-800x1200.jpg"
    ]


def test_resize_variants_collapse_to_one_source_url() -> None:
    html = """
    <html><head><title>示例商品</title></head><body>
      <div class="detail">
        <img src="https://img.alicdn.com/imgextra/detail.jpg_760x760q30.jpg">
        <img src="https://img.alicdn.com/imgextra/detail.jpg_1200x1200q30.jpg">
      </div>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://img.alicdn.com/imgextra/detail.jpg"
    ]


def test_uploaded_detail_sequence_is_not_dropped_as_a_trailing_block() -> None:
    urls = [
        f"https://gw.alicdn.com/bao/uploaded/detail-{index}-1200x1600.jpg"
        for index in range(1, 13)
    ]
    html = f"""
    <html><head><title>示例商品</title></head><body>
      <div class="detail-content">{''.join(f'<img src="{url}">' for url in urls)}</div>
      <script>window.__DATA__ = {{"descImageUrls": {json.dumps(
          urls, ensure_ascii=False
      )}}};</script>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert len([image for image in product.images if image.role == "detail"]) == 12


def test_complete_detail_state_sequence_keeps_all_27_images() -> None:
    urls = [
        f"https://gw.alicdn.com/bao/uploaded/detail-{index}-1200x1600.jpg"
        for index in range(1, 28)
    ]
    html = f"""
    <html><head><title>示例商品</title></head><body>
      <div class="detail-content">{''.join(
          f'<img src="{url}">' for url in urls[:16]
      )}</div>
      <script>window.__DATA__ = {{"descImageUrls": {json.dumps(
          urls, ensure_ascii=False
      )}}};</script>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    details = [image for image in product.images if image.role == "detail"]
    assert len(details) == 27
    assert [image.url for image in details] == urls
    assert product.image_diagnostics["state_detail_values"] == 27
    assert product.image_diagnostics["final_detail"] == 27


def test_gallery_role_wins_when_same_asset_is_seen_in_detail_first() -> None:
    shared = "https://img.alicdn.com/imgextra/shared-1200x1200.jpg"
    html = f"""
    <html><head><title>示例商品</title></head><body>
      <div class="detail-content"><img src="{shared}"></div>
      <div class="gallery"><img src="{shared}"></div>
    </body></html>
    """

    product = parse_product_html(
        html,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )

    assert [image.role for image in product.images] == ["main"]


def test_profile_is_revalidated_after_previous_empty_result(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    application = cast(Any, client.app)
    session_manager = application.state.ecommerce_session
    # Rebind the manager as well as settings: the manager owns the persistent
    # profile path used by the browser fallback.
    session_manager.profile_dir = profile
    session_manager.status_path = profile / "session-status.json"
    application.state.settings.ecommerce_browser_timeout_ms = 5_000

    empty = Product(
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
        "taobao",
        "123456789012",
        None,
        None,
    )
    rendered = Product(
        empty.source_url,
        empty.resolved_url,
        empty.platform,
        empty.product_id,
        "示例商品",
        "2090",
        [Image("https://img.alicdn.com/product-800x800.jpg")],
        "示例店铺",
    )
    monkeypatch.setattr(ecommerce_routes, "crawl_one", lambda *args: empty)
    monkeypatch.setattr(ecommerce_routes, "crawl_one_browser", lambda *args: rendered)

    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": empty.source_url, "platform": "auto", "includeDescImages": True},
    )

    assert response.status_code == 200
    assert response.json()["data"]["title"] == "示例商品"
    assert application.state.ecommerce_session.status()["state"] == "ready"
