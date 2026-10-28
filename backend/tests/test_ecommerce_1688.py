from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.ecommerce_routes as ecommerce_routes
from app.ecommerce_1688 import (
    Alibaba1688Adapter,
    _image_dimensions,
    _image_is_allowed,
    _rendered_1688_content,
)
from app.ecommerce_platform import PlatformPolicyError
from app.ecommerce_session import EcommerceSessionManager


@pytest.fixture(autouse=True)
def admin_access(client) -> None:
    client.app.state.settings.announcement_admin_emails = ["admin@example.com"]


FIXTURES = Path(__file__).parent / "fixtures" / "1688"
URL = "https://detail.1688.com/offer/123456789012.html"


@pytest.mark.parametrize(
    ("fixture", "status"),
    [
        ("product.html", "product"),
        ("login.html", "login_required"),
        ("risk.html", "risk_controlled"),
        ("not-found.html", "product_not_found"),
        ("empty.html", "empty"),
    ],
)
def test_1688_page_entry_states(fixture: str, status: str) -> None:
    result = Alibaba1688Adapter().inspect_page(
        (FIXTURES / fixture).read_text(encoding="utf-8"), URL
    )
    assert result.status == status
    if status == "product":
        assert result.title == "不锈钢保温杯厂家批发"
        assert result.product_id == "123456789012"


def test_1688_punish_redirect_with_generic_body_is_risk_controlled() -> None:
    html = """
    <!doctype html>
    <html><head><title>验证码拦截</title></head><body>
      <div>亲，请按照说明进行验证哦</div>
      <div aria-label="slider challenge"></div>
    </body></html>
    """
    resolved_url = (
        "https://detail.1688.com/offer/123456789012.html/"
        "_____tmd_____/punish?x5step=1"
    )

    result = Alibaba1688Adapter().inspect_page(html, resolved_url)

    assert result.status == "risk_controlled"
    assert "验证码" in result.matched_markers
    assert "1688安全验证页面" in result.matched_markers


def test_1688_dynamic_product_json_is_not_treated_as_empty_page() -> None:
    html = """
    <!doctype html>
    <html><head><title>1688</title></head><body>
      <div id="app"></div>
      <script type="application/json">
        {"offerId":"123456789012","subject":"动态加载的保温杯","price":"12.80",
         "mainImages":["https://cbu01.alicdn.com/img/ibank/O1CN01dynamic.jpg"]}
      </script>
    </body></html>
    """

    result = Alibaba1688Adapter().inspect_page(html, URL)

    assert result.status == "product"
    assert result.product_id == "123456789012"
    assert result.title == "动态加载的保温杯"


def test_1688_basic_product_fields_are_extracted_from_public_html() -> None:
    product = Alibaba1688Adapter().parse_product(
        (FIXTURES / "product.html").read_text(encoding="utf-8"),
        "https://qr.1688.com/s/abc123",
        URL,
    )

    assert product.status == "product"
    assert product.product_id == "123456789012"
    assert product.title == "不锈钢保温杯厂家批发"
    assert product.price == "12.80"
    assert product.min_order_quantity == "10件"
    assert product.shop_name == "宁波示例保温杯工厂"
    assert product.sales == "1.2万+件"
    assert product.props_text == "材质: 304不锈钢; 容量: 500ml"
    assert {item["name"] for item in product.properties} == {"材质", "容量"}
    assert product.services == ("48小时内发货", "免运费")
    assert product.warnings == ()


def test_1688_images_and_skus_are_normalized_from_public_page_data() -> None:
    product = Alibaba1688Adapter().parse_product(
        (FIXTURES / "product-images-sku.html").read_text(encoding="utf-8"),
        URL,
    )

    assert product.status == "product"
    assert len([image for image in product.images if image.role == "main"]) == 8
    assert len([image for image in product.images if image.role == "detail"]) == 2
    assert len([image for image in product.images if image.role == "sku"]) == 2
    assert len({image.url for image in product.images}) == len(product.images)
    assert all("login.1688.com" not in image.url for image in product.images)
    assert [group.name for group in product.sku_prop_groups] == ["颜色", "版本"]
    assert product.sku_prop_groups[0].values == ("黑色", "白色")
    assert [sku.sku_id for sku in product.skus] == ["1001", "1002"]
    assert product.skus[0].price == "39.90"
    assert product.skus[0].stock == "100"
    assert product.skus[1].price == "49.90"
    assert product.skus[1].stock == "20"
    assert (
        product.skus[0].image_url
        == "https://cbu01.alicdn.com/img/ibank/O1CN04black.jpg"
    )


@pytest.mark.parametrize(
    ("url", "dimensions", "allowed"),
    [
        ("https://img.alicdn.com/imgextra/tps-15-8.svg", (15, 8), False),
        (
            "https://img.alicdn.com/imgextra/i2/6000000006837/"
            "O1CN01DYdEcy20NP2PQrCPF_!!6000000006837-2-gg_dtc.png",
            None,
            False,
        ),
        ("https://cbu01.alicdn.com/img/ibank/main-800x800.jpg", (800, 800), True),
        ("https://cbu01.alicdn.com/img/ibank/detail-1200x1800.jpg", (1200, 1800), True),
    ],
)
def test_1688_image_dimensions_cover_tps_placeholders_and_product_assets(
    url: str, dimensions: tuple[int, int], allowed: bool
) -> None:
    assert _image_dimensions(url) == dimensions
    assert _image_is_allowed(url) is allowed


def test_1688_image_parser_keeps_detail_aliases_and_drops_tiny_tps_placeholders(
) -> None:
    html = """
    <!doctype html>
    <html><head><title>示例商品</title></head><body>
      <div class="gallery">
        <img src="https://img.alicdn.com/imgextra/i2/O1CNplaceholder-0-tps-15-8.svg">
        <img src="https://cbu01.alicdn.com/img/ibank/main-1-800x800.jpg">
      </div>
      <div class="detail 详情" style="background-image:url('https://cbu01.alicdn.com/img/ibank/detail-1-1200x1800.jpg')"></div>
      <script type="application/json">
        {"offerId":"123456789012", "subject":"示例商品",
         "offerImgList":["https://cbu01.alicdn.com/img/ibank/main-2-800x800.jpg"],
         "detailUrl":["https://cbu01.alicdn.com/img/ibank/detail-2-1200x1800.jpg"]}
      </script>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)
    main = [image.url for image in product.images if image.role == "main"]
    detail = [image.url for image in product.images if image.role == "detail"]

    assert "15-8.svg" not in " ".join(main + detail)
    assert product.image_diagnostics["rejected_image_filter"] >= 1
    assert main == [
        "https://cbu01.alicdn.com/img/ibank/main-1.jpg",
        "https://cbu01.alicdn.com/img/ibank/main-2.jpg",
    ]
    assert detail == [
        "https://cbu01.alicdn.com/img/ibank/detail-1.jpg",
        "https://cbu01.alicdn.com/img/ibank/detail-2.jpg",
    ]


def test_1688_classless_portrait_is_not_reclassified_by_shape() -> None:
    main_urls = "\n".join(
        f'<img src="https://cbu01.alicdn.com/img/ibank/gallery-{index}-800x800.jpg">'
        for index in range(1, 7)
    )
    html = f"""
    <!doctype html>
    <html><head><title>示例商品</title></head><body>
      <div class="gallery 主图">{main_urls}</div>
      <div class="detail 详情">
        <img src="https://cbu01.alicdn.com/img/ibank/known-detail-1200x900.jpg">
      </div>
      <img src="https://cbu01.alicdn.com/img/ibank/classless-detail-800x1400.jpg">
      <img src="https://cbu01.alicdn.com/img/ibank/classless-square-800x800.jpg">
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)
    main = [image.url for image in product.images if image.role == "main"]
    detail = [image.url for image in product.images if image.role == "detail"]

    # The parser must not silently discard or relabel an additional source image
    # merely because an older implementation assumed that every gallery has
    # six images. Without a detail-container signal, shape alone is not enough
    # to call a portrait asset a detail image.
    assert main == [
        *(
            f"https://cbu01.alicdn.com/img/ibank/gallery-{index}.jpg"
            for index in range(1, 7)
        ),
        "https://cbu01.alicdn.com/img/ibank/classless-detail.jpg",
        "https://cbu01.alicdn.com/img/ibank/classless-square.jpg",
    ]
    assert detail == [
        "https://cbu01.alicdn.com/img/ibank/known-detail.jpg",
    ]
    assert product.image_diagnostics.get("reclassified_main_overflow_to_detail", 0) == 0
    assert product.image_diagnostics.get("rejected_main_overflow", 0) == 0


def test_1688_gallery_sku_thumbnails_and_detail_images_are_separated() -> None:
    gallery = "".join(
        f'<img class="preview-img" src="https://cbu01.alicdn.com/img/ibank/'
        f'{"hero" if index <= 5 else "sku"}-{index}-800x800.jpg">'
        for index in range(1, 70)
    )
    sku_thumbnails = "".join(
        f'<div class="expand-view-item"><img class="item-image-icon" '
        f'src="https://cbu01.alicdn.com/img/ibank/sku-{index}-32x32.jpg_sum.jpg"></div>'
        for index in range(6, 70)
    )
    recommendations = "".join(
        f'<img class="offer-img" src="https://cbu01.alicdn.com/img/ibank/'
        f'recommend-{index}-800x800.jpg">'
        for index in range(20)
    )
    details = "".join(
        f'<img src="https://cbu01.alicdn.com/img/ibank/detail-{index}-800x1200.jpg">'
        for index in range(1, 16)
    )
    html = f"""
    <!doctype html>
    <html><head><title>精确图片分类商品</title></head><body>
      <h1>精确图片分类商品</h1>
      <ul class="od-gallery-list">{gallery}</ul>
      <div class="expand-view-list">{sku_thumbnails}</div>
      <div class="recommendation">{recommendations}</div>
      <div id="detail"><div>{details}</div></div>
      <img class="dynamic-backup-img" src="https://cbu01.alicdn.com/img/ibank/backup-1440x900.jpg">
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)

    assert len([image for image in product.images if image.role == "main"]) == 5
    assert len([image for image in product.images if image.role == "detail"]) == 15
    assert len([image for image in product.images if image.role == "sku"]) == 64
    assert not any("recommend-" in image.url for image in product.images)
    assert not any("backup-" in image.url for image in product.images)


def test_1688_detail_container_offer_image_wrap_is_not_recommendation() -> None:
    html = """
    <!doctype html>
    <html><body>
      <h1>offer-image-wrap 详情商品</h1>
      <div class="gallery 主图">
        <img src="https://cbu01.alicdn.com/img/ibank/main-1-800x800.jpg">
        <img src="https://cbu01.alicdn.com/img/ibank/main-2-800x800.jpg">
        <img src="https://cbu01.alicdn.com/img/ibank/main-3-800x800.jpg">
        <img src="https://cbu01.alicdn.com/img/ibank/main-4-800x800.jpg">
        <img src="https://cbu01.alicdn.com/img/ibank/main-5-800x800.jpg">
      </div>
      <div class="offer-image-wrap desc-dynamic-module detail">
        <img src="https://cbu01.alicdn.com/img/ibank/detail-1-1200x1800.jpg">
      </div>
      <div class="shopProductRecommend">
        <img class="offer-img" src="https://cbu01.alicdn.com/img/ibank/recommend-1-800x800.jpg">
      </div>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)

    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://cbu01.alicdn.com/img/ibank/detail-1.jpg"
    ]
    assert not any("recommend-" in image.url for image in product.images)


def test_1688_nested_offer_recommendation_container_is_not_detail() -> None:
    html = """
    <!doctype html>
    <html><body>
      <h1>嵌套推荐过滤商品</h1>
      <div class="module-od-picture-gallery od-gallery-list-wapper">
        <img src="https://cbu01.alicdn.com/img/ibank/main-1-800x800.jpg">
      </div>
      <div id="detail">
        <img src="https://cbu01.alicdn.com/img/ibank/detail-real-1200x1800.jpg">
        <div class="sdmap-dynamic-offer-list">
          <div class="desc-dynamic-module offer-list-wapper">
            <div class="offer-image-wrap">
              <img src="https://cbu01.alicdn.com/img/ibank/recommend-nested-1200x1800.jpg">
            </div>
          </div>
        </div>
      </div>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)

    assert [image.url for image in product.images if image.role == "main"] == [
        "https://cbu01.alicdn.com/img/ibank/main-1.jpg"
    ]
    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://cbu01.alicdn.com/img/ibank/detail-real.jpg"
    ]
    assert not any("recommend-nested" in image.url for image in product.images)
    assert product.image_diagnostics["rejected_image_filter"] >= 1


def test_1688_explicit_gallery_role_survives_duplicate_detail_reference() -> None:
    html = """
    <!doctype html>
    <html><body>
      <h1>主图重复引用商品</h1>
      <div class="module-od-picture-gallery od-gallery-list-wapper">
        <img src="https://cbu01.alicdn.com/img/ibank/reused-800x800.jpg">
      </div>
      <div id="detail">
        <img src="https://cbu01.alicdn.com/img/ibank/reused-1200x1800.jpg">
      </div>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)

    assert [image.url for image in product.images if image.role == "main"] == [
        "https://cbu01.alicdn.com/img/ibank/reused.jpg"
    ]
    assert not [image for image in product.images if image.role == "detail"]


def test_1688_json_main_role_survives_duplicate_detail_reference() -> None:
    html = """
    <!doctype html>
    <html><head><title>JSON 主图重复商品</title></head><body>
      <div id="detail">
        <img src="https://cbu01.alicdn.com/img/ibank/reused-json-1200x1800.jpg">
      </div>
      <script type="application/json">
        {
          "offerId": "123456789012",
          "mainImages": ["https://cbu01.alicdn.com/img/ibank/reused-json-800x800.jpg"],
          "detailImages": ["https://cbu01.alicdn.com/img/ibank/reused-json-1200x1800.jpg"]
        }
      </script>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)

    assert [image.url for image in product.images if image.role == "main"] == [
        "https://cbu01.alicdn.com/img/ibank/reused-json.jpg",
    ]
    assert not [image for image in product.images if image.role == "detail"]


def test_1688_page_level_detail_class_does_not_relabel_gallery_images() -> None:
    html = """
    <!doctype html>
    <html><head><title>页面级详情类商品</title></head>
    <body class="detail-page">
      <div class="offer-detail-shell">
        <ul class="od-gallery-list">
          <li><img data-preview-src="https://cbu01.alicdn.com/img/ibank/main-preview-1-800x800.jpg"></li>
          <li><img data-img-src="https://cbu01.alicdn.com/img/ibank/main-preview-2-800x800.jpg"></li>
          <li><img src="https://cbu01.alicdn.com/img/ibank/main-preview-3-800x800.jpg"></li>
        </ul>
        <div id="detail"><div class="desc-content">
          <img src="https://cbu01.alicdn.com/img/ibank/detail-1-1200x1800.jpg">
        </div></div>
      </div>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)

    assert [image.url for image in product.images if image.role == "main"] == [
        "https://cbu01.alicdn.com/img/ibank/main-preview-1.jpg",
        "https://cbu01.alicdn.com/img/ibank/main-preview-2.jpg",
        "https://cbu01.alicdn.com/img/ibank/main-preview-3.jpg",
    ]
    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://cbu01.alicdn.com/img/ibank/detail-1.jpg",
    ]


def test_1688_shadow_dom_images_are_added_to_rendered_content() -> None:
    class Page:
        main_frame = object()
        frames = [main_frame]
        evaluate_calls: list[str] = []

        def content(self) -> str:
            return """
            <html><head><title>Shadow 商品</title></head><body>
              <h1>Shadow 商品</h1>
              <div class="od-gallery-list">
                <img src="https://cbu01.alicdn.com/img/ibank/main-800x800.jpg">
              </div>
            </body></html>
            """

        def evaluate(self, script: str) -> str:
            self.evaluate_calls.append(script)
            return (
                '<div class="shadow-rendered detail html-description">'
                '<img src="https://cbu01.alicdn.com/img/ibank/shadow-detail-1200x1800.jpg">'
                '</div>'
            )

    page = Page()
    rendered = _rendered_1688_content(page)
    product = Alibaba1688Adapter().parse_product(rendered, URL)

    assert page.evaluate_calls
    assert "1688-shadow-content" in rendered
    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://cbu01.alicdn.com/img/ibank/shadow-detail.jpg"
    ]


def test_1688_nested_desc_container_classifies_dimensionless_images_as_detail() -> None:
    html = """
    <!doctype html>
    <html><head><title>嵌套详情商品</title></head><body>
      <div class="gallery"><img src="https://cbu01.alicdn.com/img/ibank/main-800x800.jpg"></div>
      <div id="detail"><div class="desc-content"><div>
        <img src="https://cbu01.alicdn.com/img/ibank/detail-without-size.jpg">
      </div></div></div>
    </body></html>
    """

    product = Alibaba1688Adapter().parse_product(html, URL)
    assert [image.url for image in product.images if image.role == "main"] == [
        "https://cbu01.alicdn.com/img/ibank/main.jpg"
    ]
    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://cbu01.alicdn.com/img/ibank/detail-without-size.jpg"
    ]


def test_1688_same_site_detail_iframe_is_included_in_rendered_content() -> None:
    class Frame:
        url = "https://detail.1688.com/desc/123456789012"

        def content(self) -> str:
            return """
            <html><head><title>详情 iframe</title></head><body>
              <img src="https://cbu01.alicdn.com/img/ibank/iframe-detail-1200x1800.jpg">
            </body></html>
            """

    class Page:
        main_frame = object()
        frames = [main_frame, Frame()]

        def content(self) -> str:
            return """
            <html><head><title>示例商品</title></head><body>
              <div class="gallery 主图">
                <img src="https://cbu01.alicdn.com/img/ibank/main-800x800.jpg">
              </div>
            </body></html>
            """

    rendered = _rendered_1688_content(Page())
    product = Alibaba1688Adapter().parse_product(rendered, URL)

    assert "1688-detail-iframe" in rendered
    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://cbu01.alicdn.com/img/ibank/iframe-detail.jpg"
    ]


def test_1688_blocked_page_never_returns_product_fields_or_assets() -> None:
    for fixture, status in (
        ("login.html", "login_required"),
        ("risk.html", "risk_controlled"),
    ):
        product = Alibaba1688Adapter().parse_product(
            (FIXTURES / fixture).read_text(encoding="utf-8"),
            URL,
        )
        assert product.status == status
        assert product.product_id is None
        assert product.price is None
        assert product.shop_name is None
        assert product.images == ()
        assert product.sku_prop_groups == ()
        assert product.skus == ()
        assert product.warnings


def test_1688_page_entry_rejects_non_1688_url() -> None:
    with pytest.raises(PlatformPolicyError, match="非 1688 URL"):
        Alibaba1688Adapter().inspect_page(
            (FIXTURES / "product.html").read_text(encoding="utf-8"),
            "https://item.taobao.com/item.htm?id=123456789012",
        )


def test_1688_fetch_route_uses_1688_adapter_and_unified_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = Alibaba1688Adapter().parse_product(
        (FIXTURES / "product-images-sku.html").read_text(encoding="utf-8"),
        URL,
    )
    calls: list[str] = []

    def fake_crawl(target_url: str) -> object:
        calls.append(target_url)
        return parsed

    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688", fake_crawl)
    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": URL, "platform": "auto", "includeDescImages": True},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert calls == [URL]
    assert data["platform"] == "alibaba1688"
    assert data["numIid"] == "123456789012"
    assert len(data["imageUrls"]) == 8
    assert len(data["descImageUrls"]) == 2
    assert len(data["skuImageUrls"]) == 2
    assert data["skuPropGroups"][0]["name"] == "颜色"
    assert data["skus"][1]["sku_id"] == "1002"
    assert data["platformData"]["minimumOrderQuantity"] == "2件"


def test_1688_fetch_route_retries_browser_when_static_product_has_no_images(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_product = Alibaba1688Adapter().parse_product(
        """
        <html><head><title>动态商品</title></head><body>
          <div>价格：¥12.80</div><div>供应商：测试工厂</div>
          <script type="application/json">
            {"offerId":"123456789012","subject":"动态商品","price":"12.80"}
          </script>
        </body></html>
        """,
        URL,
    )
    browser_product = Alibaba1688Adapter().parse_product(
        (FIXTURES / "product-images-sku.html").read_text(encoding="utf-8"),
        URL,
    )
    calls: list[tuple[str, int, str | None]] = []

    monkeypatch.setattr(
        ecommerce_routes, "crawl_one_1688", lambda target_url: static_product
    )

    def fake_browser(
        target_url: str, timeout_ms: int, profile_dir: str | None
    ) -> object:
        calls.append((target_url, timeout_ms, profile_dir))
        return browser_product

    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688_browser", fake_browser)

    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": URL, "platform": "alibaba1688", "includeDescImages": True},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["imageUrls"]) == 8
    assert len(data["descImageUrls"]) == 2
    assert calls == [
        (URL, client.app.state.settings.ecommerce_browser_timeout_ms, None)
    ]


def test_1688_fetch_route_retries_with_authorized_browser_after_static_login_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_blocked = Alibaba1688Adapter().parse_product(
        (FIXTURES / "login.html").read_text(encoding="utf-8"), URL
    )
    browser_product = Alibaba1688Adapter().parse_product(
        (FIXTURES / "product-images-sku.html").read_text(encoding="utf-8"),
        URL,
    )
    manager = client.app.state.ecommerce_session_1688
    manager.profile_dir.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(
        '{"state":"ready","message":"已有1688会话"}', encoding="utf-8"
    )
    calls: list[tuple[str, int, str | None]] = []

    monkeypatch.setattr(
        ecommerce_routes, "crawl_one_1688", lambda target_url: static_blocked
    )

    def fake_browser(
        target_url: str, timeout_ms: int, profile_dir: str | None
    ) -> object:
        calls.append((target_url, timeout_ms, profile_dir))
        return browser_product

    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688_browser", fake_browser)
    try:
        response = client.post(
            "/api/ecommerce/fetch-item",
            json={"url": URL, "platform": "alibaba1688"},
        )
    finally:
        manager.status_path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert response.json()["data"]["numIid"] == "123456789012"
    assert len(response.json()["data"]["imageUrls"]) == 8
    assert calls and calls[0][0] == URL
    assert calls[0][2] == str(manager.profile_dir)


def test_1688_ready_session_uses_authorized_browser_before_static_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser_product = Alibaba1688Adapter().parse_product(
        (FIXTURES / "product-images-sku.html").read_text(encoding="utf-8"),
        URL,
    )
    manager = client.app.state.ecommerce_session_1688
    manager.profile_dir.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(
        '{"state":"ready","message":"已有1688会话"}', encoding="utf-8"
    )
    browser_calls: list[tuple[str, int, str | None]] = []

    def unexpected_static_request(target_url: str) -> object:
        raise AssertionError("ready 1688 sessions must not preflight with urllib")

    def authorized_browser(
        target_url: str, timeout_ms: int, profile_dir: str | None
    ) -> object:
        browser_calls.append((target_url, timeout_ms, profile_dir))
        return browser_product

    monkeypatch.setattr(
        ecommerce_routes, "crawl_one_1688", unexpected_static_request
    )
    monkeypatch.setattr(
        ecommerce_routes, "crawl_one_1688_browser", authorized_browser
    )

    try:
        response = client.post(
            "/api/ecommerce/fetch-item",
            json={"url": URL, "platform": "alibaba1688"},
        )
    finally:
        manager.status_path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert len(response.json()["data"]["imageUrls"]) == 8
    assert browser_calls == [
        (
            URL,
            client.app.state.settings.ecommerce_browser_timeout_ms,
            str(manager.profile_dir),
        )
    ]


def test_1688_risk_control_does_not_invalidate_ready_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = Alibaba1688Adapter().parse_product(
        (FIXTURES / "risk.html").read_text(encoding="utf-8"), URL
    )
    manager = client.app.state.ecommerce_session_1688
    manager.profile_dir.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(
        '{"state":"ready","message":"已有1688会话"}', encoding="utf-8"
    )
    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688", lambda target_url: blocked)
    monkeypatch.setattr(
        ecommerce_routes,
        "crawl_one_1688_browser",
        lambda target_url, timeout_ms, profile_dir: blocked,
    )

    try:
        response = client.post(
            "/api/ecommerce/fetch-item",
            json={"url": URL, "platform": "alibaba1688"},
        )
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["code"] == "RISK_CONTROL_PAGE"
        assert "无需重新登录" in detail["message"]
        assert manager.status()["state"] == "ready"
    finally:
        manager.status_path.unlink(missing_ok=True)


def test_1688_risk_control_retries_authorized_browser_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = Alibaba1688Adapter().parse_product(
        (FIXTURES / "risk.html").read_text(encoding="utf-8"), URL
    )
    manager = client.app.state.ecommerce_session_1688
    manager.profile_dir.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(
        '{"state":"ready","message":"已有1688会话"}', encoding="utf-8"
    )
    browser_calls: list[tuple[str, int, str | None]] = []

    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688", lambda target_url: blocked)

    def authorized_browser(
        target_url: str, timeout_ms: int, profile_dir: str | None
    ) -> object:
        browser_calls.append((target_url, timeout_ms, profile_dir))
        return blocked

    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688_browser", authorized_browser)

    try:
        response = client.post(
            "/api/ecommerce/fetch-item",
            json={"url": URL, "platform": "alibaba1688"},
        )
    finally:
        manager.status_path.unlink(missing_ok=True)

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "RISK_CONTROL_PAGE"
    assert "人工验证" in detail["message"]
    assert len(browser_calls) == 1
    assert browser_calls[0][0] == URL
    assert browser_calls[0][2] == str(manager.profile_dir)


def test_1688_fetch_route_returns_explicit_login_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = Alibaba1688Adapter().parse_product(
        (FIXTURES / "login.html").read_text(encoding="utf-8"), URL
    )
    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688", lambda target_url: blocked)

    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": URL, "platform": "alibaba1688"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "LOGIN_REQUIRED"
    # The app fixture uses a per-test temporary profile. Remove the status
    # written by this scenario so later assertions in this test module remain
    # explicit rather than depending on fixture teardown.
    client.app.state.ecommerce_session_1688.status_path.unlink(missing_ok=True)


def test_app_fixture_isolates_ecommerce_profiles_from_workstation(
    client: TestClient,
) -> None:
    storage_parent = client.app.state.settings.storage_dir.parent.resolve()
    taobao_profile = client.app.state.ecommerce_session.profile_dir.resolve()
    alibaba_profile = client.app.state.ecommerce_session_1688.profile_dir.resolve()

    assert taobao_profile.is_relative_to(storage_parent)
    assert alibaba_profile.is_relative_to(storage_parent)
    assert taobao_profile != alibaba_profile


def test_1688_session_starts_uninitialized_and_uses_own_profile(tmp_path: Path) -> None:
    manager = EcommerceSessionManager(
        tmp_path / "domestic-1688",
        platform="alibaba1688",
        session_profile="domestic-1688",
        state_style="canonical",
    )
    status = manager.status()
    assert status["state"] == "not_initialized"
    assert status["platform"] == "alibaba1688"
    assert status["sessionProfile"] == "domestic-1688"
    assert status["profileReady"] is False


def test_capabilities_route_exposes_direct_1688_without_session_secrets(
    client: TestClient,
) -> None:
    response = client.get("/api/ecommerce/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["apiVersion"] == "ecommerce-1688-v1"
    assert data["platforms"]["1688"] == {
        "enabled": True,
        "adapter": "1688",
        "networkMode": "direct",
        "sessionProfile": "domestic-1688",
    }
    assert data["platforms"]["amazon"]["networkMode"] == "proxy"
    assert "profileDir" not in data
    assert "cookie" not in body
    assert "token" not in body

def test_session_status_route_keeps_taobao_default_and_exposes_1688(
    client: TestClient,
) -> None:
    taobao = client.get("/api/ecommerce/session/status")
    assert taobao.status_code == 200
    assert taobao.json()["data"]["platform"] == "taobao"

    alibaba = client.get("/api/ecommerce/session/status?platform=1688")
    assert alibaba.status_code == 200
    assert alibaba.json()["data"]["platform"] == "alibaba1688"
    assert alibaba.json()["data"]["state"] == "not_initialized"


def test_1688_session_open_page_requires_ready_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = client.app.state.ecommerce_session_1688
    manager.mark_ready()
    calls: list[str] = []

    def fake_open(target_url: str) -> dict[str, object]:
        calls.append(target_url)
        return {
            "state": "ready",
            "platform": "alibaba1688",
            "manualPageOpened": True,
        }

    monkeypatch.setattr(manager, "start_manual_page", fake_open)
    response = client.post(
        "/api/ecommerce/session/open-page",
        json={"platform": "1688", "url": URL},
    )

    assert response.status_code == 200
    assert response.json()["data"]["manualPageOpened"] is True
    assert calls == [URL]
    manager.status_path.unlink(missing_ok=True)


def test_1688_session_open_page_rejects_unready_session(client: TestClient) -> None:
    response = client.post(
        "/api/ecommerce/session/open-page",
        json={"platform": "1688", "url": URL},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SESSION_NOT_READY"


def test_1688_session_open_page_rejects_existing_manual_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = client.app.state.ecommerce_session_1688
    manager.mark_ready()
    monkeypatch.setattr(
        manager,
        "start_manual_page",
        lambda target_url: {
            "state": "ready",
            "platform": "alibaba1688",
            "manualPageOpened": False,
        },
    )

    try:
        response = client.post(
            "/api/ecommerce/session/open-page",
            json={"platform": "1688", "url": URL},
        )
    finally:
        manager.status_path.unlink(missing_ok=True)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MANUAL_PAGE_ALREADY_OPEN"


def test_1688_session_start_selects_independent_manager(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = client.app
    calls: list[tuple[str, str | None]] = []

    def fake_start(
        target_url: str | None = None, **_: object
    ) -> dict[str, object]:
        calls.append(("1688", target_url))
        return {"state": "login_pending", "platform": "alibaba1688"}

    monkeypatch.setattr(
        application.state.ecommerce_session_1688, "start_login", fake_start
    )
    response = client.post(
        "/api/ecommerce/session/start",
        json={"platform": "1688", "url": URL},
    )
    assert response.status_code == 200
    assert response.json()["data"]["platform"] == "alibaba1688"
    assert calls == [("1688", None)]
    assert (
        application.state.ecommerce_session.profile_dir
        != application.state.ecommerce_session_1688.profile_dir
    )


def test_session_start_rejects_platform_hint_mismatch(client: TestClient) -> None:
    response = client.post(
        "/api/ecommerce/session/start",
        json={"platform": "taobao", "url": URL},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "NETWORK_POLICY_VIOLATION"



