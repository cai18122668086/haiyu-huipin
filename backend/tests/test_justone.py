import json
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.errors import ApiError
from app.justone import (
    TAOBAO_FALLBACK_ENDPOINT,
    MarketRedirects,
    fetch_product,
    normalize_product,
    parse_product_link,
)

ITEM_ID = "1063600240762"
CANONICAL = f"https://item.taobao.com/item.htm?id={ITEM_ID}"
AD_LINK = (
    "https://item.taobao.com/item.htm?"
    "ali_refid=a3_420860_1007%3A10353112734%3AH%3A10353112734_0_28295483766"
    "&ali_trackid=319_f0f958ef812f8763109184428df675c0"
    f"&id={ITEM_ID}&item_type=ad&mi_id=0000EOABCGMO"
    "&mm_sceneid=0_0_10353112734_0_0&skuId=6107973556141"
    "&spm=tbpc.pc_sem_alimama%2Fa.201876.d5"
)


def stub_provider(monkeypatch, replies):
    calls = []
    pending = iter(replies)

    def open_request(request, timeout):
        calls.append(request)
        return BytesIO(json.dumps(next(pending)).encode())

    monkeypatch.setattr(
        "app.justone.build_opener",
        lambda *handlers: SimpleNamespace(open=open_request),
    )
    return calls


def test_ad_link_uses_item_id_instead_of_tracking_id_or_sku():
    assert parse_product_link(AD_LINK) == ("taobao", ITEM_ID, CANONICAL)


@pytest.mark.parametrize(
    "body,final_url,expected",
    [
        (
            b"",
            "https://detail.tmall.com/item.htm?id=1090430565044",
            (
                "tmall",
                "1090430565044",
                "https://detail.tmall.com/item.htm?id=1090430565044",
            ),
        ),
        (
            b"<script>var invalidUrl='https://m.tb.cn/scanError.htm';"
            b"var url = 'https://item.taobao.com/item.htm?id=1090430565044&amp;skuId=999';"
            b"window.location.replace(url);</script>",
            None,
            (
                "taobao",
                "1090430565044",
                "https://item.taobao.com/item.htm?id=1090430565044",
            ),
        ),
        (
            rb"""<script>var url="https:\/\/item.taobao.com\/item.htm"""
            rb"""?id=1090430565044\u0026skuId=999";</script>""",
            None,
            (
                "taobao",
                "1090430565044",
                "https://item.taobao.com/item.htm?id=1090430565044",
            ),
        ),
    ],
)
def test_mobile_share_resolves_http_or_script_target(
    monkeypatch, body, final_url, expected
):
    short = "https://e.tb.cn/h.example?tk=test"

    def open_request(request, timeout):
        response = BytesIO(body)
        response.geturl = lambda: final_url or request.full_url
        return response

    monkeypatch.setattr(
        "app.justone.build_opener",
        lambda *handlers: SimpleNamespace(open=open_request),
    )
    assert (
        parse_product_link("【淘宝】大促价保 " + short + " CZ009 「商品」") == expected
    )


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/item.htm?id=123456789",
        "https://item.taobao.com.evil.test/item.htm?id=123456789",
        "https://user:password@item.taobao.com/item.htm?id=123456789",
        "https://item.taobao.com:8000/item.htm?id=123456789",
        "https://item.taobao.com:bad/item.htm?id=123456789",
        "file:///etc/passwd",
    ],
)
def test_share_redirects_reject_unsafe_destinations(monkeypatch, target):
    short = "https://e.tb.cn/h.example"
    response = BytesIO(f"<script>var url = '{target}';</script>".encode())
    response.geturl = lambda: short
    monkeypatch.setattr(
        "app.justone.build_opener",
        lambda *handlers: SimpleNamespace(open=lambda *args, **kwargs: response),
    )
    with pytest.raises(ApiError) as caught:
        parse_product_link(short)
    assert caught.value.code == "INVALID_REDIRECT"
    with pytest.raises(ApiError) as caught:
        MarketRedirects().redirect_request(None, None, 302, "", {}, target)
    assert caught.value.code == "INVALID_REDIRECT"


def test_share_link_cycles_are_bounded(monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append(request.full_url)
        other = "https://e.tb.cn/h.two" if len(calls) % 2 else "https://e.tb.cn/h.one"
        response = BytesIO(f"<script>var url = '{other}';</script>".encode())
        response.geturl = lambda: request.full_url
        return response

    monkeypatch.setattr(
        "app.justone.build_opener",
        lambda *handlers: SimpleNamespace(open=open_request),
    )
    with pytest.raises(ApiError) as caught:
        parse_product_link("https://e.tb.cn/h.one")
    assert caught.value.code == "PRODUCT_ID_MISSING"
    assert len(calls) == 3


def test_share_page_does_not_guess_id_from_ads(monkeypatch):
    short = "https://e.tb.cn/h.example"
    response = BytesIO(
        b'<a href="https://item.taobao.com/item.htm?id=123456789">Ad</a>'
    )
    response.geturl = lambda: short
    monkeypatch.setattr(
        "app.justone.build_opener",
        lambda *handlers: SimpleNamespace(open=lambda *args, **kwargs: response),
    )
    with pytest.raises(ApiError) as caught:
        parse_product_link(short)
    assert caught.value.code == "PRODUCT_ID_MISSING"


@pytest.mark.parametrize("text", ["商品不存在或已下架", "商品不存在", "商品已下架"])
def test_code_zero_with_unavailable_text_is_a_collection_failure(monkeypatch, text):
    calls = stub_provider(monkeypatch, [{"code": 0, "data": text}] * 2)
    with pytest.raises(ApiError) as caught:
        fetch_product("test-token", "taobao", ITEM_ID, CANONICAL)
    assert caught.value.status_code == 502
    assert caught.value.code == "PRODUCT_UNAVAILABLE"
    assert "采集服务" in caught.value.message
    assert "若原商品仍可打开" in caught.value.message
    assert len(calls) == 1
    assert parse_qs(urlparse(calls[0].full_url).query)["itemId"] == [ITEM_ID]


@pytest.mark.parametrize("platform", ["taobao", "tmall"])
@pytest.mark.parametrize(
    "first_reply",
    [
        {"code": 0, "data": "商品不存在或已下架"},
        {"code": 301, "data": None},
    ],
)
def test_v6_collection_failure_uses_v3_once(monkeypatch, platform, first_reply):
    calls = stub_provider(
        monkeypatch,
        [
            first_reply,
            {
                "code": 0,
                "data": {
                    "num_iid": ITEM_ID,
                    "title": "紫色上衣",
                    "item_imgs": [{"url": "//img.alicdn.com/main.jpg"}],
                    "desc_img": ["//img.alicdn.com/detail.jpg"],
                    "props_img": {"color:purple": "//img.alicdn.com/sku.jpg"},
                },
            },
        ],
    )
    product = fetch_product(
        "test-token", platform, ITEM_ID, CANONICAL, allow_v3_fallback=True
    )
    assert product["title"] == "紫色上衣"
    assert product["endpoint"] == TAOBAO_FALLBACK_ENDPOINT
    assert product["attemptedEndpoints"] == [
        "/api/taobao/get-item-detail/v6",
        TAOBAO_FALLBACK_ENDPOINT,
    ]
    assert product["costCents"] is None
    assert product["imageUrls"] == ["https://img.alicdn.com/main.jpg"]
    assert product["descImageUrls"] == ["https://img.alicdn.com/detail.jpg"]
    assert product["skuImageUrls"] == ["https://img.alicdn.com/sku.jpg"]
    assert len(calls) == 2


@pytest.mark.parametrize("returned_id", [None, "987654321"])
def test_fallback_rejects_missing_or_different_product_id(monkeypatch, returned_id):
    calls = stub_provider(
        monkeypatch,
        [
            {"code": 0, "data": "商品不存在或已下架"},
            {
                "code": 0,
                "data": {
                    "num_iid": returned_id,
                    "images": ["//img.alicdn.com/wrong.jpg"],
                },
            },
        ],
    )
    with pytest.raises(ApiError) as caught:
        fetch_product(
            "test-token", "taobao", ITEM_ID, CANONICAL, allow_v3_fallback=True
        )
    assert caught.value.code == "PRODUCT_ID_MISMATCH"
    assert len(calls) == 2


@pytest.mark.parametrize("platform", ["1688", "jd"])
def test_other_platforms_never_use_taobao_fallback(monkeypatch, platform):
    calls = stub_provider(monkeypatch, [{"code": 0, "data": "商品不存在或已下架"}])
    with pytest.raises(ApiError) as caught:
        fetch_product("test-token", platform, ITEM_ID, CANONICAL)
    assert caught.value.code == "PRODUCT_UNAVAILABLE"
    assert len(calls) == 1


@pytest.mark.parametrize("data", [None, [], "", 42, "private-token-and-raw-error"])
def test_invalid_product_data_has_safe_actionable_error(monkeypatch, data):
    calls = stub_provider(monkeypatch, [{"code": 0, "data": data}])
    with pytest.raises(ApiError) as caught:
        fetch_product("test-token", "taobao", ITEM_ID, CANONICAL)
    assert caught.value.code == "PRODUCT_DATA_INVALID"
    assert "上传参考图" in caught.value.message
    assert "private-token" not in caught.value.message
    assert "格式无法识别" not in caught.value.message
    assert len(calls) == 1


@pytest.mark.parametrize(
    "wire", [None, [], "unexpected response", {}, {"code": "0"}, {"code": False}]
)
def test_invalid_envelope_does_not_escape_as_attribute_error(monkeypatch, wire):
    stub_provider(monkeypatch, [wire])
    with pytest.raises(ApiError, match="未返回有效商品信息") as caught:
        fetch_product("test-token", "taobao", ITEM_ID, CANONICAL)
    assert caught.value.code == "PRODUCT_DATA_INVALID"


@pytest.mark.parametrize("code", [100, 302, 303, 400, 600, 601, 602])
def test_provider_account_and_limit_errors_never_fall_back(monkeypatch, code):
    calls = stub_provider(monkeypatch, [{"code": code, "data": "商品不存在或已下架"}])
    with pytest.raises(ApiError) as caught:
        fetch_product("test-token", "taobao", ITEM_ID, CANONICAL)
    assert caught.value.code == f"PRODUCT_PROVIDER_{code}"
    assert len(calls) == 1


def test_valid_product_still_normalizes_image_groups():
    product = normalize_product(
        {
            "item": {
                "title": "保温杯",
                "images": ["//img.alicdn.com/main.jpg"],
                "desc": '<img src="//img.alicdn.com/detail.jpg">',
                "sku": {"images": ["//img.alicdn.com/sku.jpg"]},
            }
        },
        "taobao",
        ITEM_ID,
        CANONICAL,
    )
    assert product["title"] == "保温杯"
    assert product["imageUrls"] == ["https://img.alicdn.com/main.jpg"]
    assert product["descImageUrls"] == ["https://img.alicdn.com/detail.jpg"]
    assert product["skuImageUrls"] == ["https://img.alicdn.com/sku.jpg"]
