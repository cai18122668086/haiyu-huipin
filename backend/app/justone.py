"""Product data adapter. No browser sessions or marketplace credentials."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .errors import ApiError

ENDPOINTS = {
    "taobao": ("/api/taobao/get-item-detail/v6", 25),
    "tmall": ("/api/taobao/get-item-detail/v6", 25),
    "1688": ("/api/1688/get-item-detail/v1", 10),
    "jd": ("/api/jd/get-item-detail/v3", 40),
}
TAOBAO_FALLBACK_ENDPOINT = "/api/taobao/get-item-detail/v3"
MARKET_HOSTS = {
    "taobao": ("taobao.com", "tb.cn"),
    "tmall": ("tmall.com",),
    "1688": ("1688.com",),
    "jd": ("jd.com", "3.cn", "npcitem.jd.hk"),
}
IMAGE_HOSTS = ("alicdn.com", "taobaocdn.com", "tbcdn.cn", "360buyimg.com")


def domain_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def image_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = html.unescape(value.strip()).replace("\\/", "/")
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        return None
    if parsed.path.lower().endswith((".html", ".htm", ".js", ".json", ".css")):
        return None
    if not domain_matches(parsed.hostname.lower(), IMAGE_HOSTS):
        return None
    return value


class MarketRedirects(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        if not _is_market_url(newurl):
            raise ApiError(
                400,
                "INVALID_REDIRECT",
                "分享链接跳转到了不支持的地址，请使用完整商品链接。",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_market_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme in {"https", "http"}
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 80, 443}
            and domain_matches(
                parsed.hostname or "",
                tuple(d for group in MARKET_HOSTS.values() for d in group),
            )
        )
    except ValueError:
        return False


def _share_target(raw: str) -> str | None:
    with build_opener(ProxyHandler({}), MarketRedirects()).open(
        Request(raw, headers={"User-Agent": "Mozilla/5.0"}), timeout=15
    ) as response:
        final = response.geturl()
        if not _is_market_url(final):
            raise ApiError(400, "INVALID_REDIRECT", "分享链接跳转到了不支持的地址。")
        if final != raw:
            return str(final)
        body = response.read(512 * 1024 + 1)
    if len(body) > 512 * 1024:
        return None
    # 手淘 e.tb.cn 返回 HTTP 200，商品地址在脚本的 var url 中。
    # 仅读取指定跳转字段，不执行脚本，也不从广告或推荐链接猜测商品 ID。
    match = re.search(
        r"""\b(?:var|let|const)\s+(?:url|targetUrl|redirectUrl)\s*=\s*"""
        r"""(?P<quote>["'])(?P<url>[^"'\r\n]+)(?P=quote)""",
        body.decode("utf-8", errors="replace"),
    )
    if not match:
        return None
    target = re.sub(
        r"""\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|([/"'\\]))""",
        lambda part: (
            chr(int(part[1] or part[2], 16)) if part[1] or part[2] else part[3]
        ),
        html.unescape(match["url"]),
    )
    if not _is_market_url(target):
        raise ApiError(400, "INVALID_REDIRECT", "分享链接跳转到了不支持的地址。")
    return target


def parse_product_link(raw: str, platform_hint: str = "auto") -> tuple[str, str, str]:
    return _parse_product_link(raw, platform_hint, remaining_shares=3)


def _parse_product_link(
    raw: str, platform_hint: str, *, remaining_shares: int
) -> tuple[str, str, str]:
    raw = raw.strip()
    if raw.isdigit():
        if platform_hint not in ENDPOINTS:
            raise ApiError(400, "PLATFORM_REQUIRED", "输入商品 ID 时，请选择来源平台。")
        platform, item_id = platform_hint, raw
    else:
        match = re.search(r"https?://[^\s<>\"，]+", raw)
        if not match:
            raise ApiError(
                400, "INVALID_PRODUCT_LINK", "请粘贴淘宝、天猫、1688 或京东的商品链接。"
            )
        raw = match.group(0)
        try:
            parsed = urlparse(raw)
            port = parsed.port
        except ValueError:
            raise ApiError(400, "INVALID_PRODUCT_LINK", "商品链接格式无效。") from None
        host = (parsed.hostname or "").lower()
        platform = next(
            (
                name
                for name, domains in MARKET_HOSTS.items()
                if domain_matches(host, domains)
            ),
            "",
        )
        if (
            not platform
            or parsed.username
            or parsed.password
            or port not in {None, 80, 443}
        ):
            raise ApiError(
                400, "UNSUPPORTED_SOURCE", "暂不支持该商品链接，可切换为上传参考图。"
            )
        query = parse_qs(parsed.query)
        item_id = next(
            (
                query[k][0]
                for k in ("id", "itemId", "item_id", "sku", "wareId")
                if query.get(k)
            ),
            "",
        )
        if not item_id:
            match = re.search(r"/(?:offer/|product/)?(\d{6,})\.html", parsed.path)
            item_id = match.group(1) if match else ""
        if not item_id and (
            domain_matches(host, ("tb.cn", "3.cn")) or "short" in parsed.path
        ):
            if remaining_shares <= 0:
                raise ApiError(
                    400, "PRODUCT_ID_MISSING", "分享链接跳转过多，请使用完整商品链接。"
                )
            try:
                target = _share_target(raw)
                if target and target != raw:
                    return _parse_product_link(
                        target, platform_hint, remaining_shares=remaining_shares - 1
                    )
            except ApiError:
                raise
            except Exception:
                pass
        if not item_id.isdigit():
            raise ApiError(
                400,
                "PRODUCT_ID_MISSING",
                "未识别出商品 ID，请使用完整商品链接，或选择来源平台后输入商品 ID。",
            )
    canonical = {
        "taobao": f"https://item.taobao.com/item.htm?id={item_id}",
        "tmall": f"https://detail.tmall.com/item.htm?id={item_id}",
        "1688": f"https://detail.1688.com/offer/{item_id}.html",
        "jd": f"https://item.jd.com/{item_id}.html",
    }[platform]
    return platform, item_id, canonical


def _urls(value: object) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.extend(
            re.findall(r"(?:https?:)?//[^\s\"'<>]+", html.unescape(value))
        )
    elif isinstance(value, list):
        for part in value:
            candidates.extend(_urls(part))
    elif isinstance(value, Mapping):
        for preferred in ("fullPathImageURI", "original", "big"):
            if value.get(preferred):
                return _urls(value[preferred])
        for part in value.values():
            candidates.extend(_urls(part))
    return list(
        dict.fromkeys(url for candidate in candidates if (url := image_url(candidate)))
    )


def normalize_product(
    body: object, platform: str, item_id: str, canonical: str
) -> dict:
    if not isinstance(body, Mapping):
        # V6 can return code=0 with a failure message in data instead of a product.
        # Attribute that result to the collector; it does not prove the item is
        # unavailable on the marketplace itself. Never echo arbitrary upstream text.
        if isinstance(body, str) and any(
            marker in body for marker in ("商品不存在", "商品已下架")
        ):
            raise ApiError(
                502,
                "PRODUCT_UNAVAILABLE",
                "采集服务提示商品不存在或已下架；若原商品仍可打开，"
                "可能是采集服务暂时无法获取，请稍后重试或上传参考图。",
            )
        raise ApiError(
            502,
            "PRODUCT_DATA_INVALID",
            "采集服务未返回有效商品信息，请稍后重试或上传参考图。",
        )
    root = dict(body)
    item = next(
        (
            root[key]
            for key in ("item", "item_info", "itemInfo", "result", "product")
            if isinstance(root.get(key), Mapping)
        ),
        root,
    )
    groups: dict[str, list[str]] = {"main": [], "detail": [], "sku": []}
    main_keys = {
        "images",
        "pics",
        "itemimages",
        "item_imgs",
        "itemimgs",
        "mainimages",
        "main_imgs",
        "mainimagesurl",
        "img_list",
        "pic_url",
        "picurl",
        "image",
        "imageurls",
        "offerimages",
        "offerimglist",
    }
    detail_keys = {
        "desc_img",
        "desc_imgs",
        "descimages",
        "desc_imgs_list",
        "descimageurls",
        "descriptionimages",
        "detailimages",
        "detail_imgs",
        "description",
        "desc",
        "desc_html",
        "descriptionhtml",
        "description_html",
    }
    sku_keys = {"skuimages", "skuimageurls", "sku_imgs", "props_img", "sku_url"}

    def walk(value: object, context: str = "main", depth: int = 0) -> None:
        if depth > 9:
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                lower = key.lower()
                if lower in detail_keys:
                    groups["detail"].extend(_urls(child))
                elif lower in sku_keys:
                    groups["sku"].extend(_urls(child))
                elif lower in main_keys:
                    groups[context].extend(_urls(child))
                elif isinstance(child, (dict, list)) and lower not in {
                    "shop",
                    "seller",
                    "recommend",
                    "recommendations",
                    "reviews",
                }:
                    walk(
                        child,
                        "sku"
                        if "sku" in lower or lower in {"props", "propvalues", "specs"}
                        else context,
                        depth + 1,
                    )
        elif isinstance(value, list):
            for child in value:
                walk(child, context, depth + 1)

    walk(root)
    if platform == "1688":
        # V1's root images combines the five gallery shots with every SKU.
        # mainPic identifies the actual gallery independently of that union.
        main_pic = root.get("mainPic")
        gallery = []
        if isinstance(main_pic, Mapping):
            gallery = _urls(main_pic.get("offerImgList")) or _urls(
                main_pic.get("offerImages")
            )
        if not gallery and root.get("images"):
            gallery = _urls(root["images"])
            without_sku = [url for url in gallery if url not in groups["sku"]]
            if without_sku:
                gallery = without_sku
        if gallery:
            groups["main"] = gallery
    if item is not root:
        for key in detail_keys:
            if key in root:
                groups["detail"].extend(_urls(root[key]))
    groups = {name: list(dict.fromkeys(urls))[:120] for name, urls in groups.items()}
    if not groups["main"] and not groups["detail"]:
        raise ApiError(
            502,
            "PRODUCT_IMAGES_MISSING",
            "商品数据已返回，但未找到可用图片；请上传参考图或更换商品。",
        )

    def first(*keys: str) -> str | None:
        for source in (item, root):
            for key in keys:
                if source.get(key) is not None and not isinstance(
                    source[key], (dict, list)
                ):
                    return str(source[key])[:500]
        return None

    return {
        "platform": platform,
        "numIid": item_id,
        "sourceUrl": canonical,
        "title": first(
            "title", "itemTitle", "item_title", "offerTitle", "name", "productName"
        ),
        "price": first("price", "salePrice", "priceText", "itemPrice"),
        "shopName": first("shopName", "shop_name", "companyName", "sellerNick", "nick"),
        "imageUrls": groups["main"],
        "descImageUrls": groups["detail"],
        "skuImageUrls": groups["sku"],
        "supplier": "justoneapi",
        "endpoint": ENDPOINTS[platform][0],
        "costCents": ENDPOINTS[platform][1],
    }


def fetch_product(
    token: str,
    platform: str,
    item_id: str,
    canonical: str,
    *,
    allow_v3_fallback: bool = False,
) -> dict:
    if not token:
        raise ApiError(
            503,
            "PRODUCT_PROVIDER_NOT_CONFIGURED",
            "商品采集服务正在配置，请先上传参考图。",
        )
    endpoint = ENDPOINTS[platform][0]
    try:
        return _fetch_endpoint(token, platform, item_id, canonical, endpoint)
    except ApiError as error:
        # V3 单次费用较高，普通商品拉取禁止隐式升级到该接口。
        if (
            not allow_v3_fallback
            or platform not in {"taobao", "tmall"}
            or error.code not in {"PRODUCT_UNAVAILABLE", "PRODUCT_PROVIDER_301"}
        ):
            raise
    # V6 对可访问商品也可能返回采集失败（301）或下架文本，V3 已实测可恢复。
    # 只回退一次；网络、鉴权、余额和限流错误不触发额外的数据请求。
    product = _fetch_endpoint(
        token, platform, item_id, canonical, TAOBAO_FALLBACK_ENDPOINT
    )
    product["attemptedEndpoints"] = [endpoint, TAOBAO_FALLBACK_ENDPOINT]
    # V3's account tariff and charges for unsuccessful calls are unverified.
    # Do not reuse the V6 price or imply that the additional call was free.
    product["costCents"] = None
    return product


def _fetch_endpoint(
    token: str, platform: str, item_id: str, canonical: str, endpoint: str
) -> dict:
    try:
        request = Request(
            "https://api.justoneapi.com"
            + endpoint
            + "?"
            + urlencode({"token": token, "itemId": item_id}),
            headers={"User-Agent": "Polaris/1.0", "Accept": "application/json"},
        )
        with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
            wire = json.loads(response.read(16 * 1024 * 1024))
    except (HTTPError, URLError, TimeoutError, ValueError):
        raise ApiError(
            502,
            "PRODUCT_PROVIDER_UNAVAILABLE",
            "商品服务暂时无法连接，请稍后重试或上传参考图。",
        ) from None
    if not isinstance(wire, Mapping):
        raise ApiError(
            502,
            "PRODUCT_DATA_INVALID",
            "采集服务未返回有效商品信息，请稍后重试或上传参考图。",
        )
    code = wire.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise ApiError(
            502,
            "PRODUCT_DATA_INVALID",
            "采集服务未返回有效商品信息，请稍后重试或上传参考图。",
        )
    if code != 0:
        messages = {
            100: "商品服务凭证失效，请联系管理员。",
            301: "该商品暂时获取失败，请更换链接或上传参考图。",
            302: "商品服务繁忙，请稍后重试。",
            303: "今日商品查询额度已用完，请联系管理员。",
            400: "商品 ID 无效，请检查链接。",
            600: "商品接口未开通，请联系管理员。",
            601: "商品服务余额不足，请联系管理员充值。",
            602: "商品服务令牌额度不足，请联系管理员。",
        }
        raise ApiError(
            502,
            f"PRODUCT_PROVIDER_{code}",
            messages.get(code, "商品服务返回异常，请稍后重试。"),
        )
    body = wire.get("data")
    if (
        endpoint == TAOBAO_FALLBACK_ENDPOINT
        and isinstance(body, Mapping)
        and str(body.get("num_iid", "")) != item_id
    ):
        raise ApiError(
            502,
            "PRODUCT_ID_MISMATCH",
            "采集服务返回的商品与链接不一致，请稍后重试或上传参考图。",
        )
    if isinstance(body, dict) and isinstance(body.get("description"), dict):
        detail_url = body["description"].get("detailUrl", "")
        parsed = urlparse(detail_url)
        if (
            parsed.scheme == "https"
            and domain_matches(parsed.hostname or "", ("tmall.com", "alicdn.com"))
            and not parsed.username
            and not parsed.password
        ):
            try:
                with build_opener(ProxyHandler({}), MarketRedirects()).open(
                    Request(detail_url, headers={"User-Agent": "Mozilla/5.0"}),
                    timeout=25,
                ) as response:
                    body["description_html"] = response.read(4 * 1024 * 1024).decode(
                        "utf-8", errors="replace"
                    )
            except Exception:
                pass
    product = normalize_product(body, platform, item_id, canonical)
    product["endpoint"] = endpoint
    return product
