from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.ecommerce_routes as ecommerce_routes
from app.ecommerce_pinduoduo import PinduoduoAdapter, parse_product_html
from app.ecommerce_platform import SiteFamily, resolve_site_policy


@pytest.fixture(autouse=True)
def admin_access(client) -> None:
    client.app.state.settings.announcement_admin_emails = ["admin@example.com"]


FIXTURES = Path(__file__).parent / "fixtures" / "pinduoduo"
URL = "https://mobile.yangkeduo.com/goods.html?goods_id=401084455839"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_current_window_raw_data_assignment_extracts_product_title() -> None:
    html = """
    <html>
      <head><title>PDD</title></head>
      <body>
        <div class="goods-container-v2">Product details</div>
        <script>window.rawData={"initDataObj":{"goods":
          {"goodsID":988165446649,"goodsName":"Current product title",
           "topGallery":[{"url":"https://img.pddpic.com/product/main.jpg"}]},
          "price":"12.80"}};</script>
      </body>
    </html>
    """
    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods1.html?goods_id=988165446649"
    )

    assert product.status == "product"
    assert product.product_id == "988165446649"
    assert product.title == "Current product title"
    assert product.images


def test_product_fixture_extracts_fields_and_image_roles() -> None:
    product = parse_product_html(fixture("product.html"), URL)

    assert product.status == "product"
    assert product.product_id == "401084455839"
    assert product.title == "拼多多示例商品"
    assert product.price == "9.90"
    assert product.shop_name == "示例拼多多店铺"
    assert product.sales == "已拼355件"
    assert [image.role for image in product.images].count("main") == 3
    assert [image.role for image in product.images].count("detail") == 2
    assert [image.role for image in product.images].count("sku") == 1
    assert all(
        "avatar" not in image.url and "logo" not in image.url
        for image in product.images
    )


def test_structured_media_uses_detail_gallery_and_sku_property_images() -> None:
    product = parse_product_html(
        fixture("structured-media.html"),
        "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949",
    )

    assert product.status == "product"
    assert [image.url for image in product.images if image.role == "main"] == [
        "https://img.pddpic.com/product/main-1.jpg",
        "https://img-1.pddpic.com/product/main-2.jpg",
    ]
    assert [image.url for image in product.images if image.role == "detail"] == [
        "https://img.pddpic.com/product/detail-1.jpg",
    ]
    assert [image.url for image in product.images if image.role == "sku"] == [
        "https://img.pddpic.com/product/sku-red.jpg",
        "https://img-1.pddpic.com/product/sku-blue.jpg",
    ]
    assert [(group.name, group.values) for group in product.sku_prop_groups] == [
        ("颜色", ("红色", "蓝色")),
    ]
    assert product.skus[0].image_url == "https://img.pddpic.com/product/sku-red.jpg"


def test_rendered_square_detail_gallery_asset_is_reclassified_as_main() -> None:
    html = """
    <html><head><title>商品</title></head><body>
      <div class="goods-container-v2 main container">
        <img src="https://img-1.pddpic.com/mms-goods-image/detail-square.jpg"
             data-pdd-natural-width="800" data-pdd-natural-height="800">
      </div>
      <script type="application/json">
      {
        "goods_id":"972804085949",
        "goodsName":"商品",
        "detailGallery":[{"url":"https://img.pddpic.com/mms-goods-image/detail-square.jpg"}]
      }
      </script>
    </body></html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert [(image.role, image.url) for image in product.images] == [
        ("main", "https://img.pddpic.com/mms-goods-image/detail-square.jpg"),
    ]


def test_main_gallery_preserves_more_than_five_valid_primary_assets() -> None:
    urls = [
        f"https://img.pddpic.com/mms-goods-image/main-{index}.jpg"
        for index in range(1, 8)
    ]
    gallery_json = ",".join(f'{{"url":"{url}"}}' for url in urls)
    html = f"""
    <html><head><title>商品</title></head><body>
      <script type="application/json">
      {{
        "goods_id":"972804085949",
        "goodsName":"商品",
        "topGallery":[{gallery_json}]
      }}
      </script>
    </body></html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert [image.url for image in product.images if image.role == "main"] == urls
    assert "rejected_main_overflow" not in product.image_diagnostics


def test_same_asset_can_be_retained_as_main_and_sku() -> None:
    html = """
    <html><head><title>商品</title></head><body>
      <div class="goods-gallery">
        <img src="https://img.pddpic.com/mms-goods-image/main.jpg"
             data-pdd-natural-width="800" data-pdd-natural-height="800">
      </div>
      <script type="application/json">
      {
        "goods_id":"972804085949",
        "goodsName":"商品",
        "topGallery":[{"url":"https://img.pddpic.com/mms-goods-image/main.jpg"}],
        "skuProperty":[{
          "specName":"颜色",
          "specValues":[{
            "specValue":"红色",
            "specValueImgUrl":"https://img.pddpic.com/mms-goods-image/main.jpg"
          }]
        }]
      }
      </script>
    </body></html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert [(image.role, image.url) for image in product.images] == [
        ("main", "https://img.pddpic.com/mms-goods-image/main.jpg"),
        ("sku", "https://img.pddpic.com/mms-goods-image/main.jpg"),
    ]


def test_small_rendered_ui_asset_is_not_promoted_from_structured_gallery() -> None:
    html = """
    <html><head><title>商品</title></head><body>
      <div class="goods-gallery">
        <img src="https://img.pddpic.com/mms-goods-image/main.jpg"
             data-pdd-natural-width="800" data-pdd-natural-height="800">
        <img src="https://img.pddpic.com/mms-goods-image/ui-uuid.jpg"
             data-pdd-natural-width="96" data-pdd-natural-height="51">
      </div>
      <script type="application/json">
      {
        "goods_id":"972804085949",
        "goodsName":"商品",
        "topGallery":[
          {"url":"https://img.pddpic.com/mms-goods-image/main.jpg"},
          {"url":"https://img.pddpic.com/mms-goods-image/ui-uuid.jpg"}
        ]
      }
      </script>
    </body></html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert [image.url for image in product.images if image.role == "main"] == [
        "https://img.pddpic.com/mms-goods-image/main.jpg"
    ]
    assert product.image_diagnostics["rejected_dom_small_media"] >= 1


def test_generic_main_shell_without_dimensions_does_not_promote_lazy_detail() -> None:
    html = """
    <html><head><title>商品</title></head><body>
      <div class="goods-container-v2 main container">
        <img src="https://img.pddpic.com/mms-goods-image/main.jpg"
             data-pdd-natural-width="800" data-pdd-natural-height="800">
        <img src="https://img.pddpic.com/mms-goods-image/lazy-detail-square.jpg">
      </div>
      <script type="application/json">
      {"goods_id":"972804085949","goodsName":"商品"}
      </script>
    </body></html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert [(image.role, image.url) for image in product.images] == [
        ("main", "https://img.pddpic.com/mms-goods-image/main.jpg"),
    ]


def test_dom_cdn_host_variants_are_deduplicated() -> None:
    html = """
    <html><head><title>商品</title></head><body>
      <div class="goods-gallery">
        <img src="https://img.pddpic.com/mms-goods-image/shared.jpg">
        <img src="https://img-1.pddpic.com/mms-goods-image/shared.jpg">
      </div>
    </body></html>
    """

    product = parse_product_html(html, URL)

    assert [image.url for image in product.images] == [
        "https://img.pddpic.com/mms-goods-image/shared.jpg"
    ]


def test_dom_nearest_container_wins_and_known_ui_resource_families_are_ignored(
) -> None:
    html = """
    <html><head><title>嵌套媒体商品</title></head><body>
      <section class="goods-detail">
        <div class="goods-gallery">
          <img src="https://img.pddpic.com/mms-goods-image/main-2.jpg">
          <div class="sku-spec">
            <img src="https://img.pddpic.com/mms-goods-image/sku-3.jpg">
          </div>
          <img src="https://img.pddpic.com/funimg/detail-icon.png">
          <img src="https://img.pddpic.com/commimg/common-icon.png">
          <img src="https://promotion.pddpic.com/promotion/badge.png">
        </div>
      </section>
    </body></html>
    """

    product = parse_product_html(html, URL)

    assert product.status == "product"
    assert [(image.role, image.url) for image in product.images] == [
        ("main", "https://img.pddpic.com/mms-goods-image/main-2.jpg"),
        ("sku", "https://img.pddpic.com/mms-goods-image/sku-3.jpg"),
    ]


def test_mms_goods_image_dimension_fallback_separates_main_detail_and_sku() -> None:
    html = """
    <html><head><title>尺寸回退商品</title></head><body>
      <div class="goods-container-v2 main container">
        <img src="https://img.pddpic.com/mms-goods-image/main-1.jpeg"
             data-pdd-dom-index="0"
             data-pdd-natural-width="800" data-pdd-natural-height="800">
        <img src="https://img.pddpic.com/mms-goods-image/detail-1.jpeg"
             data-pdd-dom-index="1"
             data-pdd-natural-width="750" data-pdd-natural-height="1048">
        <img src="https://img.pddpic.com/mms-goods-image/detail-2.jpeg"
             data-pdd-dom-index="2"
             data-pdd-natural-width="750" data-pdd-natural-height="447">
        <div class="sku-spec">
          <img src="https://img.pddpic.com/mms-goods-image/sku-1.jpeg"
               data-pdd-dom-index="3"
               data-pdd-natural-width="800" data-pdd-natural-height="800">
        </div>
      </div>
    </body></html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert [(image.role, image.url) for image in product.images] == [
        ("main", "https://img.pddpic.com/mms-goods-image/main-1.jpeg"),
        ("detail", "https://img.pddpic.com/mms-goods-image/detail-1.jpeg"),
        ("detail", "https://img.pddpic.com/mms-goods-image/detail-2.jpeg"),
        ("sku", "https://img.pddpic.com/mms-goods-image/sku-1.jpeg"),
    ]


def test_sold_out_shell_is_not_mistaken_for_product_content() -> None:
    # A sold-out page can still render a recommendation waterfall with valid
    # pddpic images. Those images must not be returned as the requested item.
    html = """
    <html>
      <head><title>拼多多</title></head>
      <body>
        <div class=\"goods-container-v2 not-sale\">商品已售罄</div>
        <div class=\"goods-recommend-list-container\">
          <img src=\"https://img-4.pddpic.com/recommend/item.jpg\">
        </div>
      </body>
    </html>
    """
    product = parse_product_html(html, URL)

    assert product.status == "product_not_found"
    assert product.product_id == "401084455839"
    assert product.images == ()
    assert product.warnings == ("拼多多商品不存在、已下架或已售罄。",)


def test_product_signal_wins_over_sold_out_marker_from_secondary_ui() -> None:
    html = """
    <html>
      <head><title>拼多多</title></head>
      <body>
        <div class="goods-container-v2">
          <h1>仍在售商品</h1>
          <span>¥3</span>
        </div>
        <div class="goods-recommend-list-container">部分推荐商品已售罄</div>
        <script>window.rawData={"goods":{"goodsID":401084455839,
          "goodsName":"仍在售商品","price":3,
          "topGallery":[{"url":"https://img.pddpic.com/product/main.jpg"}]}};</script>
      </body>
    </html>
    """
    product = parse_product_html(html, URL)

    assert product.status == "product"
    assert product.title == "仍在售商品"
    assert product.price == "3"


def test_sku_data_and_status_pages_are_structured() -> None:
    product = parse_product_html(fixture("product.html"), URL)
    assert [(group.name, group.values) for group in product.sku_prop_groups] == [
        ("颜色", ("红色", "蓝色")),
        ("尺码", ("M", "L")),
    ]
    assert product.skus[0].sku_id == "sku-1"
    assert product.skus[0].image_url == "https://img.pddpic.com/product/sku-red.jpg"

    adapter = PinduoduoAdapter()
    assert adapter.parse_product(fixture("login.html"), URL).status == "login_required"
    assert adapter.parse_product(fixture("risk.html"), URL).status == "risk_controlled"
    assert (
        adapter.parse_product(fixture("not-found.html"), URL).status
        == "product_not_found"
    )


def test_sku_page_url_is_not_returned_as_sku_image() -> None:
    html = """
    <html>
      <head><title>拼多多商品</title></head>
      <body>
        <h1>商品标题</h1>
        <script type="application/json">
        {
          "goods_id": "972804085949",
          "goods_name": "商品标题",
          "sku_list": [
            {
              "sku_id": "sku-1",
              "image_url": "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
            }
          ]
        }
        </script>
      </body>
    </html>
    """

    product = parse_product_html(
        html, "https://mobile.yangkeduo.com/goods2.html?goods_id=972804085949"
    )

    assert product.status == "product"
    assert len(product.skus) == 1
    assert product.skus[0].image_url is None



def test_policy_and_route_use_independent_pinduoduo_adapter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = resolve_site_policy(URL)
    assert policy.site_family is SiteFamily.PINDUODUO
    assert policy.adapter_name == "pinduoduo"
    assert policy.session_profile == "domestic-pinduoduo"

    calls: list[str] = []

    def fake_fetch(target_url: str) -> object:
        calls.append(target_url)
        return parse_product_html(fixture("product.html"), target_url)

    monkeypatch.setattr(ecommerce_routes, "crawl_one_pinduoduo", fake_fetch)
    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": URL, "platform": "auto", "includeDescImages": True},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["platform"] == "pinduoduo"
    assert data["numIid"] == "401084455839"
    assert data["imageUrls"]
    assert calls == [URL]


def test_capabilities_and_session_status_include_pinduoduo(client: TestClient) -> None:
    capabilities = client.get("/api/ecommerce/capabilities").json()["data"]
    assert capabilities["platforms"]["pinduoduo"] == {
        "enabled": True,
        "adapter": "pinduoduo",
        "networkMode": "direct",
        "sessionProfile": "domestic-pinduoduo",
    }
    status = client.get("/api/ecommerce/session/status?platform=pinduoduo")
    assert status.status_code == 200
    assert status.json()["data"]["platform"] == "pinduoduo"
    assert status.json()["data"]["sessionProfile"] == "domestic-pinduoduo"


def test_pinduoduo_route_rejects_wrong_platform_hint(client: TestClient) -> None:
    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": URL, "platform": "jd"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "NETWORK_POLICY_VIOLATION"



def test_pinduoduo_route_uses_existing_profile_after_helper_close(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable profile must be tried even when its status file is stale."""

    adapter = PinduoduoAdapter()
    browser_product = adapter.parse_product(fixture("product.html"), URL)
    manager = client.app.state.ecommerce_session_pinduoduo
    manager.profile_dir.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(
        '{"state":"not_initialized","message":"closed"}',
        encoding="utf-8",
    )
    browser_calls: list[tuple[str, int, str | None]] = []

    def static_fetch(target_url: str) -> object:
        raise AssertionError("an existing PDD profile must be tried before static HTTP")

    def authorized_browser(
        target_url: str, timeout_ms: int, profile_dir: str | None
    ) -> object:
        browser_calls.append((target_url, timeout_ms, profile_dir))
        return browser_product

    monkeypatch.setattr(ecommerce_routes, "crawl_one_pinduoduo", static_fetch)
    monkeypatch.setattr(
        ecommerce_routes, "crawl_one_pinduoduo_browser", authorized_browser
    )
    try:
        response = client.post(
            "/api/ecommerce/fetch-item",
            json={"url": URL, "platform": "pinduoduo", "includeDescImages": True},
        )
    finally:
        manager.status_path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert response.json()["data"]["numIid"] == "401084455839"
    assert browser_calls == [
        (
            URL,
            client.app.state.settings.ecommerce_browser_timeout_ms,
            str(manager.profile_dir),
        )
    ]


def test_pinduoduo_browser_risk_state_is_not_reported_as_login_required(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = client.app.state.ecommerce_session_pinduoduo
    manager.profile_dir.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(
        '{"state":"not_initialized","message":"closed"}',
        encoding="utf-8",
    )
    blocked = PinduoduoAdapter().parse_product(fixture("risk.html"), URL)

    monkeypatch.setattr(
        ecommerce_routes,
        "crawl_one_pinduoduo_browser",
        lambda target_url, timeout_ms, profile_dir: blocked,
    )
    try:
        response = client.post(
            "/api/ecommerce/fetch-item",
            json={"url": URL, "platform": "pinduoduo"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "RISK_CONTROL_PAGE"
        assert manager.status()["state"] == "risk_controlled"
    finally:
        manager.status_path.unlink(missing_ok=True)

