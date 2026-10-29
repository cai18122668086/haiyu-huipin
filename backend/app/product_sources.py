"""一键复刻的商品来源与受信任图片域名。"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

from .errors import ApiError
from .justone import domain_matches
from .justone import parse_product_link as parse_domestic_link

ProductPlatform = Literal[
    "taobao",
    "tmall",
    "1688",
    "jd",
    "xianyu",
    "dewu",
    "amazon",
    "aliexpress",
    "shopee",
    "temu",
    "etsy",
    "walmart",
]


@dataclass(frozen=True)
class ProductSource:
    label: str
    provider_platform: str
    action: str
    domains: tuple[str, ...]
    identity_fields: tuple[str, ...]
    target: str = "amazon-us"


SOURCES = {
    "taobao": ProductSource(
        "淘宝",
        "taobao",
        "product_detail",
        ("taobao.com", "tb.cn"),
        ("id", "item_id"),
        "taobao",
    ),
    "tmall": ProductSource(
        "天猫", "taobao", "product_detail", ("tmall.com",), ("id", "item_id"), "tmall"
    ),
    "1688": ProductSource(
        "1688",
        "alibaba1688",
        "product_detail",
        ("1688.com",),
        ("id", "offer_id"),
        "1688",
    ),
    "jd": ProductSource(
        "京东",
        "jd",
        "product_detail",
        ("jd.com", "3.cn", "npcitem.jd.hk"),
        ("item_id", "id"),
        "jd",
    ),
    "xianyu": ProductSource(
        "闲鱼",
        "xianyu",
        "item_detail",
        ("goofish.com", "2.taobao.com"),
        ("item_id", "id"),
        "taobao",
    ),
    "dewu": ProductSource(
        "得物",
        "dewu",
        "product_summary",
        ("dewu.com", "dw4.co"),
        ("spu_id", "id"),
        "taobao",
    ),
    "amazon": ProductSource(
        "Amazon",
        "amazon",
        "profile",
        (
            "amazon.com",
            "amazon.co.uk",
            "amazon.co.jp",
            "amazon.de",
            "amazon.fr",
            "amazon.it",
            "amazon.es",
            "amazon.ca",
            "amazon.com.au",
            "amazon.in",
            "amazon.com.br",
            "amazon.com.mx",
            "amazon.nl",
            "amazon.se",
            "amazon.pl",
            "amazon.sg",
            "amazon.ae",
            "amazon.sa",
            "amazon.com.tr",
            "amazon.co.za",
            "amazon.ie",
            "amazon.com.be",
        ),
        ("asin", "id"),
    ),
    "aliexpress": ProductSource(
        "速卖通",
        "aliexpress",
        "product_detail",
        ("aliexpress.com", "aliexpress.us", "aliexpress.ru"),
        ("id", "item_id"),
    ),
    "shopee": ProductSource(
        "Shopee",
        "shopee",
        "profile",
        (
            "shopee.sg",
            "shopee.com.my",
            "shopee.co.id",
            "shopee.co.th",
            "shopee.ph",
            "shopee.vn",
            "shopee.com.br",
            "shopee.tw",
            "shopee.com.mx",
            "shopee.com.co",
            "shopee.cl",
        ),
        ("item_id", "id"),
        "shopee",
    ),
    "temu": ProductSource(
        "Temu", "temu", "profile", ("temu.com",), ("product_id", "id"), "temu"
    ),
    "etsy": ProductSource(
        "Etsy", "etsy", "profile", ("etsy.com",), ("listing_id", "id")
    ),
    "walmart": ProductSource(
        "Walmart", "walmart", "profile", ("walmart.com",), ("item_id", "id")
    ),
}

# 只开放供应商商品图片使用的 CDN；商品页、店铺头像和任意外链不能成为图片代理入口。
IMAGE_DOMAINS = (
    "alicdn.com",
    "taobaocdn.com",
    "tbcdn.cn",
    "360buyimg.com",
    "media-amazon.com",
    "ssl-images-amazon.com",
    "images-amazon.com",
    "aliexpress-media.com",
    "etsystatic.com",
    "susercontent.com",
    "shopeeusercontent.com",
    "kwcdn.com",
    "temuimg.com",
    "walmartimages.com",
    "poizon.com",
    "cdn.dewu.com",
    "cdnimg.dewu.com",
)


def image_url(value: object) -> str | None:
    """允许历史素材与新来源的商品 CDN 图片，拒绝网页和带凭据地址。"""
    if not isinstance(value, str):
        return None
    value = html.unescape(value.strip()).replace("\\/", "/")
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
            or not domain_matches(parsed.hostname or "", IMAGE_DOMAINS)
            or parsed.path.lower().endswith((".html", ".htm", ".js", ".css", ".json"))
        ):
            return None
    except ValueError:
        return None
    return value


def parse_product_link(raw: str, platform_hint: str = "auto") -> tuple[str, str, str]:
    """标准化商品身份，缓存键保留地区站点；分享短链沿用受限跳转解析。"""
    raw = raw.strip()
    if platform_hint not in {"auto", *SOURCES}:
        raise ApiError(400, "UNSUPPORTED_SOURCE", "暂不支持该商品来源。")
    match = re.search(r'https?://[^\s<>"，]+', raw)
    if match:
        value = html.unescape(match[0])
        try:
            parsed = urlparse(value)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in {None, 80, 443}
            ):
                raise ValueError
        except ValueError:
            raise ApiError(400, "INVALID_PRODUCT_LINK", "商品链接格式无效。") from None
        host = (parsed.hostname or "").lower()
        # 2.taobao.com 的闲鱼历史链接必须先于 taobao.com 匹配。
        platform = (
            "xianyu"
            if domain_matches(host, ("2.taobao.com",))
            else next(
                (
                    name
                    for name, spec in SOURCES.items()
                    if domain_matches(host, spec.domains)
                ),
                "",
            )
        )
        if not platform:
            raise ApiError(
                400,
                "UNSUPPORTED_SOURCE",
                "暂不支持该商品来源，请使用受支持的商品链接。",
            )
        if platform in {"taobao", "tmall", "1688", "jd"}:
            return parse_domestic_link(value, platform_hint)
        query = parse_qs(parsed.query)
        item_id = ""
        path = parsed.path
        patterns = {
            "amazon": (
                r"/(?:dp|gp/product|gp/aw/d|product-reviews)/"
                r"([A-Za-z0-9]{10})(?:/|$)"
            ),
            "aliexpress": r"/item/(\d+)\.html",
            "etsy": r"/listing/(\d+)(?:/|$)",
            "walmart": r"/ip/(?:[^/]+/)?(\d+)(?:/|$)",
            "shopee": r"(?:-i\.(\d+\.\d+)(?:/|$)|/product/(\d+)/(\d+)(?:/|$))",
            "temu": r"-g-(\d+)\.html",
        }
        if platform in patterns and (found := re.search(patterns[platform], path)):
            if platform == "shopee":
                item_id = found[1] or f"{found[2]}.{found[3]}"
            else:
                item_id = found[1]
        if not item_id:
            keys = {
                "xianyu": ("id", "itemId"),
                "dewu": ("spuId", "spu_id"),
                "temu": ("goods_id",),
            }.get(platform, ())
            item_id = next((query[k][0] for k in keys if query.get(k)), "")
        if not item_id:
            if platform == "dewu" and domain_matches(host, ("dw4.co",)):
                # 供应商支持直接展开得物分享链接；用短链身份缓存，返回后核对 SPU。
                return (
                    platform,
                    "share:" + hashlib.sha256(value.encode()).hexdigest(),
                    value,
                )
            raise ApiError(
                400, "PRODUCT_ID_MISSING", "未识别出商品 ID，请复制完整商品链接。"
            )
        if platform == "amazon":
            item_id = item_id.upper()
        elif platform != "shopee" and not item_id.isdigit():
            raise ApiError(400, "PRODUCT_ID_MISSING", "未识别出有效商品 ID。")
        if platform == "shopee":
            shop_id, product_id = item_id.split(".")
            # profile 的协议要求 i.shop.item，而不是 product_detail 的另一种路径。
            canonical = f"https://{host}/item-i.{shop_id}.{product_id}"
        elif platform == "amazon":
            canonical = f"https://{host}/dp/{item_id}"
        elif platform == "aliexpress":
            canonical = f"https://{host}/item/{item_id}.html"
        elif platform == "etsy":
            canonical = f"https://www.etsy.com/listing/{item_id}"
        elif platform == "walmart":
            canonical = f"https://www.walmart.com/ip/{item_id}"
        elif platform == "temu":
            canonical = f"https://www.temu.com/goods.html?goods_id={item_id}"
        elif platform == "xianyu":
            canonical = f"https://www.goofish.com/item?id={item_id}"
        else:
            canonical = f"https://www.dewu.com/product-detail.html?spuId={item_id}"
        return platform, item_id, canonical
    if platform_hint == "auto":
        raise ApiError(400, "PLATFORM_REQUIRED", "输入商品 ID 时，请选择来源平台。")
    if platform_hint in {"taobao", "tmall", "1688", "jd"}:
        return parse_domestic_link(raw, platform_hint)
    if platform_hint == "shopee":
        raise ApiError(
            400, "PRODUCT_ID_MISSING", "Shopee 需要包含店铺 ID 的完整商品链接。"
        )
    if platform_hint == "amazon":
        if not re.fullmatch(r"[A-Za-z0-9]{10}", raw):
            raise ApiError(400, "PRODUCT_ID_MISSING", "请输入有效的 Amazon ASIN。")
        return "amazon", raw.upper(), f"https://www.amazon.com/dp/{raw.upper()}"
    if not raw.isdigit():
        raise ApiError(400, "INVALID_PRODUCT_LINK", "请粘贴完整商品链接或有效商品 ID。")
    base = {
        "xianyu": f"https://www.goofish.com/item?id={raw}",
        "dewu": f"https://www.dewu.com/product-detail.html?spuId={raw}",
        "aliexpress": f"https://www.aliexpress.com/item/{raw}.html",
        "temu": f"https://www.temu.com/goods.html?goods_id={raw}",
        "etsy": f"https://www.etsy.com/listing/{raw}",
        "walmart": f"https://www.walmart.com/ip/{raw}",
    }
    return platform_hint, raw, base[platform_hint]
