from taobao_tmall_crawler import parse_product_html, product_id_from_url


FIXTURE = """
<html><head>
<meta property="og:title" content="测试商品">
<meta property="product:price:amount" content="19.90">
</head><body>
<div id="picGalleryEle"><img data-src="//img.alicdn.com/main.jpg_200x200.jpg"></div>
<div class="sku"><img src="https://img.alicdn.com/sku-red.jpg"></div>
<div class="desc-root"><img data-ks-lazyload="https://img.alicdn.com/detail-01.png"></div>
</body></html>
"""


def test_product_id_from_supported_links() -> None:
    assert product_id_from_url("https://item.taobao.com/item.htm?id=123456789012") == "123456789012"
    assert product_id_from_url("https://detail.tmall.com/item.htm?id=123456789012") == "123456789012"


def test_parse_static_html_extracts_roles_and_metadata() -> None:
    result = parse_product_html(
        FIXTURE,
        "https://item.taobao.com/item.htm?id=123456789012",
        "https://item.taobao.com/item.htm?id=123456789012",
    )
    assert result.platform == "taobao"
    assert result.product_id == "123456789012"
    assert result.title == "测试商品"
    assert result.price == "19.90"
    assert [item.role for item in result.images] == ["main", "sku", "detail"]
    assert result.images[0].url == "https://img.alicdn.com/main.jpg_200x200.jpg"
