"""商品来源协议、付费请求恢复和一键复刻 API 的回归检查。"""

from datetime import timedelta

import pytest
from pydantic import SecretStr

from app.errors import ApiError
from app.everyinfra import fetch_product, normalize_product
from app.models import ReplicaDocumentRecord, utc_now
from app.product_sources import SOURCES, image_url, parse_product_link

CASES = [
    ("taobao", "https://item.taobao.com/item.htm?id=123456789&skuId=999", "123456789"),
    ("tmall", "https://detail.tmall.com/item.htm?id=123456789", "123456789"),
    ("1688", "https://detail.1688.com/offer/123456789.html", "123456789"),
    ("jd", "https://item.jd.com/123456789.html?tracking=1", "123456789"),
    ("xianyu", "https://www.goofish.com/item?id=123456789", "123456789"),
    ("dewu", "https://fast.dewu.com/product-detail.html?spuId=123456789", "123456789"),
    ("amazon", "https://www.amazon.co.jp/dp/B0C2JXSHSK?ref=test", "B0C2JXSHSK"),
    ("aliexpress", "https://www.aliexpress.com/item/123456789.html", "123456789"),
    ("shopee", "https://shopee.sg/product/12345/67890", "12345.67890"),
    ("temu", "https://www.temu.com/goods.html?goods_id=123456789", "123456789"),
    ("etsy", "https://www.etsy.com/listing/123456789/product-name", "123456789"),
    ("walmart", "https://www.walmart.com/ip/product-name/123456789", "123456789"),
]


def product_for(platform, item_id):
    expected = item_id.split(".")[-1]
    value = {
        SOURCES[platform].identity_fields[0]: expected,
        "title": "商品",
        "images": ["https://img.alicdn.com/main.jpg"],
        "description_images": ["https://img.alicdn.com/detail.jpg"],
        "skus": [{"image_url": "https://img.alicdn.com/sku.jpg"}],
    }
    if platform == "amazon":
        value["asin"] = item_id
    return value


@pytest.mark.parametrize("platform,url,item_id", CASES)
def test_all_product_sources_parse_and_use_their_real_contract(
    monkeypatch, platform, url, item_id
):
    code, identity, canonical = parse_product_link(url)
    assert (code, identity) == (platform, item_id)
    requests = []

    def request(token, path, body=None):
        requests.append(body)
        assert path == "/api/v1/social"
        assert token == "everyinfra-test-token"
        if body["action"] in {"description_images", "skus"}:
            return 200, {"results": [{"image_url": "https://img.alicdn.com/extra.jpg"}]}
        result = product_for(platform, item_id)
        return 200, {"results": [result] if platform == "xianyu" else result}

    monkeypatch.setattr("app.everyinfra._request", request)
    result = fetch_product("everyinfra-test-token", platform, item_id, canonical)
    assert result["supplier"] == "everyinfra"
    assert result["imageUrls"] == ["https://img.alicdn.com/main.jpg"]
    assert result["descImageUrls"][0] == "https://img.alicdn.com/detail.jpg"
    assert requests[0]["platform"] == SOURCES[platform].provider_platform
    assert requests[0]["action"] == SOURCES[platform].action
    if platform == "xianyu":
        assert requests[0]["params"] == {"urls": [canonical]}
    if platform == "jd":
        assert requests[0]["params"] == {"item_id": item_id}
        assert requests[-1]["action"] == "skus" and requests[-1]["mode"] == "sync"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/item?id=123456789",
        "https://www.amazon.com.evil.test/dp/B0C2JXSHSK",
        "https://user:secret@www.amazon.com/dp/B0C2JXSHSK",
        "https://www.etsy.com:9000/listing/123456789",
        "https://www.amazon.com/",
        "https://www.ebay.com/itm/123456789",
        "https://www.thomasnet.com/",
    ],
)
def test_non_product_or_untrusted_links_are_rejected(url):
    with pytest.raises(ApiError):
        parse_product_link(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://m.media-amazon.com/images/I/product.jpg",
        "https://i.etsystatic.com/123/image.jpg",
        "https://down-sg.img.susercontent.com/file/product",
        "https://img.kwcdn.com/product.webp",
        "https://i5.walmartimages.com/product.jpg",
        "https://cdn.poizon.com/product.jpg",
    ],
)
def test_new_platform_cdn_images_can_be_imported(url):
    assert image_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x.jpg",
        "https://media-amazon.com.evil.test/x.jpg",
        "https://m.media-amazon.com:9000/x.jpg",
        "https://www.amazon.com/dp/B0C2JXSHSK",
    ],
)
def test_image_proxy_cannot_fetch_arbitrary_urls(url):
    assert image_url(url) is None


def test_normalization_excludes_seller_and_review_images_and_keeps_all_product_images():
    raw = product_for("taobao", "123456789")
    raw.update(
        seller={"image_url": "https://img.alicdn.com/avatar.jpg"},
        reviews=[{"images": ["https://img.alicdn.com/review.jpg"]}],
        images=[f"https://img.alicdn.com/{i}.jpg" for i in range(150)],
    )
    result = normalize_product(
        raw, "taobao", "123456789", "https://item.taobao.com/item.htm?id=123456789"
    )
    assert len(result["imageUrls"]) == 150
    assert result["skuImageUrls"] == ["https://img.alicdn.com/sku.jpg"]
    assert "avatar" not in str(result) and "review.jpg" not in str(result)


def test_wrong_identity_and_unrelated_xianyu_results_are_rejected():
    with pytest.raises(ApiError, match="商品与链接不一致"):
        normalize_product(
            product_for("amazon", "B012345678"),
            "amazon",
            "B0C2JXSHSK",
            "https://www.amazon.com/dp/B0C2JXSHSK",
        )
    correct = product_for("xianyu", "123456789")
    result = normalize_product(
        [product_for("xianyu", "999"), correct],
        "xianyu",
        "123456789",
        "https://www.goofish.com/item?id=123456789",
    )
    assert result["numIid"] == "123456789"


def test_async_finalizing_is_polled_without_a_second_paid_post(monkeypatch):
    calls, snapshots = [], []
    responses = iter(
        [
            (202, {"job_id": "job_test", "status": "running"}),
            (200, {"status": "finalizing"}),
            (
                200,
                {"status": "succeeded", "results": product_for("taobao", "123456789")},
            ),
        ]
    )

    def request(token, path, body=None):
        calls.append((path, body))
        return next(responses)

    monkeypatch.setattr("app.everyinfra._request", request)
    monkeypatch.setattr("app.everyinfra.time.sleep", lambda seconds: None)
    fetch_product(
        "test",
        "taobao",
        "123456789",
        "https://item.taobao.com/item.htm?id=123456789",
        on_checkpoint=snapshots.append,
    )
    assert sum(body is not None for _, body in calls) == 1
    assert any(
        s["taobao/product_detail"].get("job_id") == "job_test" for s in snapshots
    )


def test_interrupted_poll_resumes_existing_paid_job(monkeypatch):
    state = {"taobao/product_detail": {"status": "running", "job_id": "job_existing"}}
    calls = []

    def request(token, path, body=None):
        calls.append((path, body))
        return 200, {
            "status": "succeeded",
            "results": product_for("taobao", "123456789"),
        }

    monkeypatch.setattr("app.everyinfra._request", request)
    fetch_product(
        "test",
        "taobao",
        "123456789",
        "https://item.taobao.com/item.htm?id=123456789",
        state=state,
    )
    assert calls == [("/api/v1/jobs/job_existing", None)]


def test_unknown_paid_submission_is_not_repeated(monkeypatch):
    state, calls = {}, []

    def request(*args, **kwargs):
        calls.append(1)
        raise ApiError(502, "PRODUCT_PROVIDER_REQUEST_UNKNOWN", "unknown")

    monkeypatch.setattr("app.everyinfra._request", request)
    for _ in range(2):
        with pytest.raises(ApiError) as error:
            fetch_product(
                "test",
                "taobao",
                "123456789",
                "https://item.taobao.com/item.htm?id=123456789",
                state=state,
            )
        assert error.value.code == "PRODUCT_PROVIDER_REQUEST_UNKNOWN"
    assert len(calls) == 1


def route_provider(monkeypatch, replies):
    pending = iter(replies)
    calls = []

    def request(token, path, body=None):
        calls.append((token, path, body))
        return next(pending)

    monkeypatch.setattr("app.everyinfra._request", request)
    return calls


def test_default_provider_cache_retry_and_private_receipts(client, monkeypatch):
    calls = route_provider(
        monkeypatch, [(503, {}), (200, {"results": product_for("taobao", "123456789")})]
    )
    client.app.state.settings.everyinfra_api_key = SecretStr("everyinfra-private-test")
    client.app.state.settings.justone_api_token = SecretStr("must-not-use")
    base = "/api/v1/workspaces/ws_test/replica"
    created = client.post(
        base + "/products",
        json={"url": "https://item.taobao.com/item.htm?id=123456789"},
    ).json()
    for _ in range(3):
        failed = client.get(base + "/documents/" + created["id"]).json()
        assert failed["status"] == "failed"
        assert failed["payload"]["error_code"] == "PRODUCT_PROVIDER_503"
    assert len(calls) == 1
    retry = client.post(
        base + "/products",
        json={"url": "https://item.taobao.com/item.htm?id=123456789&skuId=999"},
    ).json()
    assert retry["id"] == created["id"]
    ready = client.get(base + "/documents/" + created["id"]).json()
    assert ready["status"] == "ready"
    assert ready["payload"]["product"]["supplier"] == "everyinfra"
    assert "_product_provider_state" not in ready["payload"]
    assert "everyinfra-private-test" not in str(ready)
    client.post(
        base + "/products",
        json={"url": "https://item.taobao.com/item.htm?id=123456789"},
    )
    assert len(calls) == 2 and calls[0][0] == "everyinfra-private-test"


def test_amazon_regional_products_have_separate_cache_entries(client, monkeypatch):
    calls = route_provider(
        monkeypatch, [(200, {"results": product_for("amazon", "B0C2JXSHSK")})] * 2
    )
    client.app.state.settings.everyinfra_api_key = SecretStr("test")
    route = "/api/v1/workspaces/ws_test/replica/products"
    us = client.post(route, json={"url": "https://www.amazon.com/dp/B0C2JXSHSK"}).json()
    jp = client.post(
        route, json={"url": "https://www.amazon.co.jp/dp/B0C2JXSHSK"}
    ).json()
    assert us["id"] != jp["id"] and len(calls) == 2


def test_stale_fetch_recovers_job_without_paid_resubmission(client, monkeypatch):
    client.app.state.settings.everyinfra_api_key = SecretStr("test")
    calls = route_provider(
        monkeypatch, [(202, {"job_id": "job_existing", "status": "running"})]
    )
    # 模拟已有任务轮询暂时中断，而非伪造一个成功结果。
    import app.everyinfra as provider

    original = provider._request

    def interrupted(token, path, body=None):
        if body is None:
            raise ApiError(502, "PRODUCT_PROVIDER_PENDING", "pending")
        return original(token, path, body)

    monkeypatch.setattr(provider, "_request", interrupted)
    base = "/api/v1/workspaces/ws_test/replica"
    created = client.post(
        base + "/products",
        json={"url": "https://item.taobao.com/item.htm?id=123456789"},
    ).json()
    with client.app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, created["id"])
        row.status = "fetching"
        row.updated_at = utc_now() - timedelta(minutes=10)
        old_attempt = row.payload["_product_fetch_attempt"]
        session.commit()
    resumed = []

    def completed(token, path, body=None):
        resumed.append((path, body))
        return 200, {
            "status": "succeeded",
            "results": product_for("taobao", "123456789"),
        }

    monkeypatch.setattr(provider, "_request", completed)
    client.get(base + "/documents/" + created["id"])
    ready = client.get(base + "/documents/" + created["id"])
    assert ready.json()["status"] == "ready"
    assert resumed == [("/api/v1/jobs/job_existing", None)] and len(calls) == 1
    with client.app.state.session_factory() as session:
        assert (
            session.get(ReplicaDocumentRecord, created["id"]).payload[
                "_product_fetch_attempt"
            ]
            != old_attempt
        )


def test_supported_source_list_is_workspace_authenticated(client):
    response = client.get("/api/v1/workspaces/ws_test/replica/product-platforms")
    assert response.status_code == 200
    assert {row["code"] for row in response.json()} == set(SOURCES)
    assert "everyinfra_api_key" not in str(response.json())
