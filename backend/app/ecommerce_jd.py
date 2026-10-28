"""京东商品页适配器。

该模块只读取公开页面已经返回的 HTML、公开 JSON 和浏览器渲染后的 DOM，
不会提交登录信息、破解验证码或绕过京东风控。京东规则集中在本文件中，
以避免把京东的页面结构判断混入淘宝/天猫或 1688 适配器。
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from html import escape, unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from playwright.sync_api import Page

from .ecommerce_chrome import connect_to_owned_chrome
from .ecommerce_platform import PlatformPolicyError, SiteFamily, classify_site

ImageRole = Literal["main", "detail", "sku"]
PageEntryStatus = Literal[
    "product", "login_required", "risk_controlled", "product_not_found", "empty"
]

# JD Global Purchase product pages use npcitem.jd.hk, but their
# crawling and session policy still belong to the JD adapter. Keep this host
# explicit instead of accepting the entire jd.hk domain.
JD_HOSTS = ("jd.com", "3.cn", "npcitem.jd.hk")
JD_IMAGE_HOST_MARKERS = ("360buyimg.com", "jd.com")
_LOGIN_MARKERS = ("请登录", "登录后查看", "扫码登录", "京东登录", "登录京东")
_RISK_MARKERS = (
    "验证码",
    "滑块验证",
    "访问受限",
    "请求异常",
    "安全验证",
    "风控",
    "机器人验证",
    "too many requests",
    "forbidden",
    "暂时无法展示该商品的信息",
    "暂时无法展示该商品",
    # 京东桌面端频控页可能只有通用标题，没有正文。
    # Keep these markers before product-title/ID fallback in ``inspect_page``
    # so a shell such as ``PC频控页`` cannot become a fake product.
    "频控",
    "频繁访问",
)
_RISK_HOSTS = ("pc-frequent-pro.pf.jd.com",)
_NOT_FOUND_MARKERS = (
    "商品不存在",
    "页面不存在",
    "商品已下架",
    "抱歉,您访问的商品不存在",
)
_GENERIC_TITLES = {"京东", "京东商城", "", "登录", "京东登录", "商品详情"}
_UI_IMAGE_TOKENS = (
    "logo",
    "icon",
    "sprite",
    "favicon",
    "qrcode",
    "qr-code",
    "loading",
    "placeholder",
    "default",
    "arrow",
    "close",
    "search",
    "service",
    "advert",
    "ad.jpg",
    "ad.png",
    "coupon",
    "star",
    "trust",
    "activity",
    "business_icon",
    "tip_img",
    "shoplogo",
    "transparent",
    "mod_tag",
    "detail_decoration",
    "specbusiness",
    "shortcut",
    "buttons_icon",
    "mod_",
    "time-order",
    "header_",
    "transfer_row",
    "summaryenter",
    "icon_promote",
    "icon_point",
    "tax-info",
    "recommend",
    "guess",
    "similar",
    "relate",
)
_RECOMMEND_TOKENS = (
    "recommend",
    "guess",
    "similar",
    "relate",
    "continuous-product",
    "shop-best-seller",
    "recently",
    "recent-view",
    "also-buy",
    "may-like",
    "广告",
    "推荐",
    "猜你喜欢",
    "看了又看",
    "为你推荐",
)
# These markers identify the product gallery itself.  Keep this list narrower
# than the generic page markers: ``product-intro`` and ``content`` also occur
# on containers that hold toolbar icons, badges, and other non-product assets.
_MAIN_TOKENS = (
    "gallery",
    "preview",
    "carousel",
    "main-image",
    "mainimage",
    "j-img",
    "jqzoom",
    "商品图片",
    "主图",
    "轮播",
    "product-image",
    "loopimgul",
    "loopimgdiv",
    "loop-img",
    "firstimg",
    "firstimgli",
    "pic_list",
    "mod_slider",
    # Current desktop JD product page markers.
    "spec-img",
    "main-img",
    "pc-__image_main__",
)
_EXPLICIT_MAIN_TOKENS = ("spec-img", "main-img", "pc-__image_main__")
_EXPLICIT_DETAIL_TOKENS = ("detail-main", "ssd-module", "ssd-widget-pic")
_DETAIL_TOKENS = ("detail", "description", "desc", "图文详情", "商品详情")
_REVIEW_TOKENS = (
    "review",
    "comment",
    "comments",
    "comment-list",
    "评价",
    "评论",
    "晒单",
    "买家秀",
    "追评",
    "问答",
    "avatar",
    "user-avatar",
    "user-photo",
    "head-photo",
    "portrait",
    "ugc",
)
# A detail/gallery container may also contain controls.  These markers are
# intentionally checked as context, not only in the image URL, because JD
# often serves icon files from the same 360buyimg host as product images.
_UI_CONTEXT_TOKENS = (
    "toolbar",
    "share",
    "collect",
    "report",
    "button",
    "btn",
    "action",
    "zoom",
    "expand",
    "collapse",
    "backtop",
    "sprite",
    "图标",
    "按钮",
    "分享",
    "收藏",
    "放大镜",
    "查看大图",
    "关闭",
    "箭头",
    "返回",
    "二维码",
    "bagimage",
    "jdmcc",
    "elevator",
)
_SKU_TOKENS = (
    "sku",
    "spec",
    "color",
    "size",
    "choose",
    "stock",
    "规格",
    "颜色",
    "尺码",
)
_EXPLICIT_SKU_IMAGE_TOKENS = (
    "specification-item-sku-image",
    "sku-image",
    "sku_img",
    "specimage",
)
_LAZY_ATTRS = (
    "data-lazy-img",
    "data-lazy-src",
    "data-src",
    "data-original",
    "data-url",
    "data-image",
    "data-image-url",
    "data-preview",
    "data-preview-url",
    "data-zoom-image",
)
_IMAGE_KEY_ROLES: dict[str, ImageRole] = {
    "imagelist": "main",
    "imageurls": "main",
    "mainimage": "main",
    "mainimages": "main",
    "mainimageurls": "main",
    "productimages": "main",
    "galleryimages": "main",
    "bigimages": "main",
    "largeimages": "main",
    "detailimages": "detail",
    "descimages": "detail",
    "descriptionimages": "detail",
    "detailimageurls": "detail",
    "skuimages": "sku",
    "skuimage": "sku",
    "skuimagelist": "sku",
    "specimages": "sku",
    "colorimages": "sku",
    "colorimage": "sku",
    "propimgs": "sku",
    "propertyimages": "sku",
}


@dataclass(frozen=True)
class JDPageEntry:
    status: PageEntryStatus
    resolved_url: str
    title: str | None = None
    matched_markers: tuple[str, ...] = ()
    product_id: str | None = None

    @property
    def is_product(self) -> bool:
        return self.status == "product"


@dataclass(frozen=True)
class JDImage:
    url: str
    role: ImageRole = "main"
    ordinal: int = 0
    source: str = "dom"
    context: str = ""


@dataclass(frozen=True)
class JDSkuGroup:
    name: str
    values: tuple[str, ...] = ()
    image_urls: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JDSku:
    sku_id: str
    properties: tuple[dict[str, str], ...] = ()
    price: str | None = None
    stock: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class JDProduct:
    status: PageEntryStatus
    source_url: str
    resolved_url: str
    product_id: str | None = None
    title: str | None = None
    price: str | None = None
    shop_name: str | None = None
    sales: str | None = None
    properties: tuple[dict[str, str], ...] = ()
    services: tuple[str, ...] = ()
    images: tuple[JDImage, ...] = ()
    sku_prop_groups: tuple[JDSkuGroup, ...] = ()
    skus: tuple[JDSku, ...] = ()
    props_text: str = ""
    image_diagnostics: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def platform(self) -> str:
        return "jd"

    @property
    def is_product(self) -> bool:
        return self.status == "product"


@dataclass(frozen=True)
class _DomImage:
    url: str
    context: str
    source: str = "dom"


class _JDPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.scripts: list[tuple[str, str]] = []
        self.images: list[_DomImage] = []
        self._title_depth = 0
        self._script_depth = 0
        self._style_depth = 0
        self._script_type = ""
        self._script_parts: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.casefold(): value or "" for key, value in attrs}
        marker = " ".join(
            value
            for key in (
                "id",
                "class",
                "data-testid",
                "data-role",
                "aria-label",
                "title",
                "alt",
                "role",
            )
            if (value := attr.get(key))
        )
        context = " ".join((*self._stack[-2:], marker))[-800:]
        self._stack.append(context)
        lowered = tag.casefold()
        if lowered == "meta":
            key = attr.get("property") or attr.get("name")
            content = attr.get("content")
            if key and content:
                self.meta[key.casefold()] = content
        if lowered == "title":
            self._title_depth += 1
        elif lowered == "script":
            self._script_depth += 1
            self._script_type = attr.get("type", "").casefold()
            self._script_parts = []
        elif lowered == "style":
            self._style_depth += 1
        elif lowered in {"img", "source"}:
            values: list[str] = []
            for key in ("src", "srcset", *_LAZY_ATTRS):
                value = attr.get(key)
                if value:
                    values.extend(_split_srcset(value))
            for value in values:
                source = "rendered" if "jd-rendered-image" in context else "dom"
                self.images.append(_DomImage(value, context, source))
        style = attr.get("style", "")
        for value in re.findall(r"url\(\s*['\"]?([^'\")]+)", style, re.I):
            self.images.append(_DomImage(value, context, "css"))
        if lowered in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self._stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title" and self._title_depth:
            self._title_depth -= 1
        elif lowered == "script" and self._script_depth:
            self._script_depth -= 1
            self.scripts.append((self._script_type, "".join(self._script_parts)))
            self._script_parts = []
        elif lowered == "style" and self._style_depth:
            self._style_depth -= 1
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self._script_parts.append(data)
        elif self._title_depth:
            self.title_parts.append(data)
        elif not self._style_depth:
            self.visible_parts.append(data)


class _JDTextParser(HTMLParser):
    """Collect text together with the marker of its containing element.

    This intentionally stays independent from a CSS selector engine: the crawler
    only needs a small amount of stable seller text, and a tiny parser keeps the
    adapter dependency-free while still handling nested spans and links.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[tuple[str, str]] = []
        self._stack: list[tuple[str, list[str]]] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
            return
        attr = {key.casefold(): value or "" for key, value in attrs}
        marker = " ".join(
            value
            for key in (
                "id",
                "class",
                "data-testid",
                "data-role",
                "aria-label",
                "title",
            )
            if (value := attr.get(key))
        ).casefold()
        self._stack.append((marker, []))
        if lowered in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.handle_endtag(lowered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "noscript", "template"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if not self._stack:
            return
        marker, parts = self._stack.pop()
        value = _text(" ".join(parts))
        if marker and value:
            self.nodes.append((marker, value))

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        for _, parts in self._stack:
            parts.append(data)


def _split_srcset(value: str) -> list[str]:
    if value.lstrip().startswith(("data:", "blob:")):
        return []
    return [part.strip().split()[0] for part in value.split(",") if part.strip()]


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value).casefold())


def _text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\\n", " ").split()).strip()


_SHOP_MARKERS = (
    "shop-name",
    "shopname",
    "seller",
    "seller-name",
    "vendor",
    "vendor-name",
    "store-name",
    "店铺名称",
    "店铺名",
    "店铺",
)


def _find_shop_name(html: str) -> str | None:
    """Read a seller label from DOM without treating the whole page as a shop name."""
    parser = _JDTextParser()
    parser.feed(html)
    ranked: list[tuple[int, int, str]] = []
    for marker, value in parser.nodes:
        if len(value) > 80:
            continue
        matched = [
            index for index, token in enumerate(_SHOP_MARKERS) if token in marker
        ]
        if not matched:
            continue
        cleaned = re.sub(r"^(?:店铺(?:名称|名)?|商家)\s*[:：]\s*", "", value).strip()
        if not cleaned or cleaned in {"店铺", "店铺名称", "商家"}:
            continue
        ranked.append((min(matched), len(value), cleaned))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _extract_text_values(value: object) -> list[str]:
    if isinstance(value, str):
        text = _text(value)
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_extract_text_values(item))
        return result
    if isinstance(value, dict):
        for key in ("name", "label", "value", "val", "text", "optionName"):
            candidate = value.get(key)
            if isinstance(candidate, str) and _text(candidate):
                return [_text(candidate)]
    return []


def _parse_sku_groups(value: object) -> list[JDSkuGroup]:
    """Normalise JD's several public representations of selectable SKU specs."""
    groups: list[JDSkuGroup] = []

    def add(name: object, raw_values: object, raw_images: object = None) -> None:
        group_name = _text(name)
        values = tuple(dict.fromkeys(_extract_text_values(raw_values)))
        if group_name and values:
            images = {}
            if isinstance(raw_images, dict):
                for option in values:
                    url = _clean_image_url(raw_images.get(option), "https://item.jd.com/")
                    if url and _image_allowed(
                        url, "specification-item-sku-image", "sku"
                    ):
                        images[option] = url
            groups.append(JDSkuGroup(group_name, values, images))

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = (
                    item.get("name")
                    or item.get("label")
                    or item.get("key")
                    or item.get("propName")
                    or item.get("propertyName")
                )
                raw_values = (
                    item.get("values")
                    or item.get("options")
                    or item.get("items")
                    or item.get("valueList")
                    or item.get("value")
                    or item.get("val")
                )
                if name is not None and raw_values is not None:
                    add(name, raw_values, item.get("image_urls"))
                elif len(item) == 1:
                    name, raw_values = next(iter(item.items()))
                    add(name, raw_values)
    elif isinstance(value, dict):
        name = (
            value.get("name")
            or value.get("label")
            or value.get("key")
            or value.get("propName")
            or value.get("propertyName")
        )
        raw_values = (
            value.get("values")
            or value.get("options")
            or value.get("items")
            or value.get("valueList")
            or value.get("value")
            or value.get("val")
        )
        if name is not None and raw_values is not None:
            add(name, raw_values, value.get("image_urls"))
        else:
            for key, child in value.items():
                if _key(key) in {"name", "label", "key", "propname", "propertyname"}:
                    continue
                if isinstance(child, (list, tuple, str)):
                    add(key, child)
    return groups


def _dedupe_sku_groups(values: list[JDSkuGroup]) -> list[JDSkuGroup]:
    seen: dict[tuple[str, tuple[str, ...]], int] = {}
    result: list[JDSkuGroup] = []
    for group in values:
        identity = (group.name, group.values)
        if identity not in seen:
            seen[identity] = len(result)
            result.append(group)
        else:
            index = seen[identity]
            previous = result[index]
            result[index] = JDSkuGroup(
                group.name, group.values, {**previous.image_urls, **group.image_urls}
            )
    return result


def _parse_sku_properties(value: object) -> tuple[dict[str, str], ...]:
    mappings: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _text(item.get("name") or item.get("key") or item.get("label"))
                val = _text(item.get("value") or item.get("val") or item.get("text"))
                if name and val:
                    mappings.append({"name": name, "value": val})
    elif isinstance(value, dict):
        for name, val in value.items():
            if isinstance(val, (str, int, float)) and _text(val):
                mappings.append({"name": _text(name), "value": _text(val)})
    return tuple(_dedupe_mappings(mappings))


def _sku_image_url(item: dict[str, object], base_url: str) -> str | None:
    for key in ("image", "imageUrl", "skuImage", "specImage", "imageURL"):
        raw = item.get(key)
        if isinstance(raw, dict):
            raw = raw.get("url") or raw.get("src")
        if isinstance(raw, str) and _looks_like_image(raw):
            return _clean_image_url(raw, base_url)
    return None


def _extract_product_id(url: str, *values: object) -> str | None:
    parsed = urlparse(url)
    match = re.search(r"/(\d{6,20})(?:\.html)?(?:/|$)", parsed.path)
    if match:
        return match.group(1)
    for key in ("sku", "skuId", "id", "productId", "itemId"):
        candidate = re.search(
            rf"(?:^|[?&]){re.escape(key)}=(\d{{6,20}})", parsed.query, re.I
        )
        if candidate:
            return candidate.group(1)
    for value in values:
        match = re.search(r"(?<!\d)(\d{6,20})(?!\d)", _text(value))
        if match:
            return match.group(1)
    return None


def _clean_image_url(raw: object, base_url: str) -> str | None:
    value = unescape(str(raw)).replace("\\/", "/").strip().strip("'\"")
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return None
    if re.match(r"^/?jfs/", value, re.I):
        value = "https://img10.360buyimg.com/n1/" + value.lstrip("/")
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = (parsed.hostname or "").casefold()
    # A fully qualified item.m.jd.com product path is a page proxy, not a
    # CDN image endpoint. Bare jfs paths are normalized to the CDN above.
    if host == "item.m.jd.com":
        return None
    if re.search(r"\.(?:otf|ttf|woff2?|eot|css|js)$", parsed.path, re.I):
        return None
    if not any(
        host == marker or host.endswith(f".{marker}")
        for marker in JD_IMAGE_HOST_MARKERS
    ):
        return None
    # 京东会把尺寸前缀写成 ``/s800x800_jfs/``，也会写成
    # ``/pc_s800x800_jfs/``、``/img_s800x800_jfs/`` 等变体。
    path = re.sub(r"/(?:[\w-]+_)?s\d+x\d+_jfs/", "/jfs/", parsed.path, flags=re.I)
    path = re.sub(r"/(?:s\d+x\d+|s\d+)(?:_q\d+)?(?=\/|$)", "", path, flags=re.I)
    path = re.sub(r"[!_]s\d+x\d+(?:_q\d+)?(?=\.|$)", "", path, flags=re.I)
    path = re.sub(r"[!_]q\d+(?=\.|$)", "", path, flags=re.I)
    # JD often serves a JPEG/PNG through AVIF negotiation as ``photo.jpg.avif``.
    # The browser can render that response, but the server asset validator
    # intentionally accepts JPEG/PNG/WEBP only.  Ask the CDN for the native
    # public format so persistence follows the local preview contract.
    path = re.sub(r"\.(jpe?g|png|webp)\.avif$", r".\1", path, flags=re.I)
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _context_has(context: str, tokens: tuple[str, ...]) -> bool:
    """Match marker words without letting ``preview`` match ``review``."""

    lowered = context.casefold()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(token.casefold())}(?![a-z0-9])", lowered)
        for token in tokens
    )


def _is_jd_detail_asset_url(url: str) -> bool:
    """Recognize JD's detail-image CDN namespaces independently of DOM markers.

    On the live JD page the detail images can be rendered inside a broad
    ``preview``/product wrapper, so context alone incorrectly labels them as
    main images.  These namespaces are part of the public image URL shape and
    are more specific than that broad ancestor context.
    """

    path = urlparse(url.strip()).path.casefold()
    return bool(re.search(r"/(?:popwaredetail|imgzone)(?:/|$)", path))


def _role_for_image_url(
    url: str, role: ImageRole | None, context: str = ""
) -> ImageRole | None:
    """Apply URL-specific role evidence without overriding SKU or blocked DOM."""

    if _context_has(context, _RECOMMEND_TOKENS):
        return None
    if _context_has(context, _REVIEW_TOKENS):
        return None
    if _context_has(context, _UI_CONTEXT_TOKENS) and not _context_has(
        context, _EXPLICIT_MAIN_TOKENS
    ):
        return None
    if role == "sku":
        return role
    if _is_jd_detail_asset_url(url):
        return "detail"
    return role


def _image_allowed(
    url: str, context: str, role: ImageRole, raw_url: str = ""
) -> bool:
    # Check the un-normalised URL as well.  _clean_image_url intentionally
    # removes JD size prefixes, but those prefixes are useful for rejecting
    # ordinary UI icons while retaining the original SKU thumbnail behavior.
    haystack = f"{raw_url} {url} {context}".casefold()
    if any(token.casefold() in haystack for token in _UI_IMAGE_TOKENS):
        return False
    # This is metadata added by _rendered_image_fragment, not semantic DOM
    # context.  Keep the marker name out of _SKU_TOKENS so an ordinary loaded
    # image cannot become a SKU merely because it has natural dimensions.
    rendered_size = re.search(r"rendered-dimensions-(\d+)x(\d+)", context)
    if rendered_size:
        width, height = map(int, rendered_size.groups())
        if (
            width
            and height
            and min(width, height) < 64
            and (
                role != "sku"
                or not _context_has(context, _EXPLICIT_SKU_IMAGE_TOKENS)
            )
        ):
            return False
    if (
        role != "sku"
        and _context_has(context, _UI_CONTEXT_TOKENS)
        and not _context_has(context, _EXPLICIT_MAIN_TOKENS)
    ):
        return False
    if role != "sku":
        for width, height in re.findall(
            r"(?<!\d)(\d{1,4})x(\d{1,4})(?!\d)",
            f"{raw_url} {context}",
        ):
            if int(width) <= 160 and int(height) <= 160:
                return False
    return True


def _role_from_context(context: str) -> ImageRole | None:
    lowered = context.casefold()
    if _context_has(context, _RECOMMEND_TOKENS):
        return None
    if _context_has(context, _REVIEW_TOKENS):
        return None
    if _context_has(context, _EXPLICIT_MAIN_TOKENS):
        return "main"
    if _context_has(context, _UI_CONTEXT_TOKENS):
        return None
    # Current JD wraps the entire left column in preview-wrap. Its SSD
    # detail modules are more specific evidence than that gallery ancestor.
    if _context_has(context, _EXPLICIT_DETAIL_TOKENS):
        return "detail"
    # JD's main gallery is commonly named ``spec-list``; ``spec`` here means
    # picture specification, not a selectable SKU specification.
    if "spec-list" in lowered and "choose" not in lowered and "sku" not in lowered:
        return "main"
    # Main markers must win over a broad ancestor such as ``detail-page``.
    # This is what prevents main gallery images 2..9 from being duplicated in
    # the detail group when the page wraps the whole product in that class.
    if _context_has(context, _MAIN_TOKENS):
        return "main"
    if _context_has(context, _SKU_TOKENS):
        return "sku"
    if _context_has(context, _DETAIL_TOKENS):
        return "detail"
    # Do not treat every <img> on a product page as a main image.  Header,
    # footer, promotion, review, and control images otherwise become false
    # positives because they share the JD image host.
    return None


def _iter_json_values(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    values: list[object] = []
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, (dict, list)):
            values.append(value)
    return values


def _json_role(key: str, inherited: ImageRole | None) -> ImageRole | None:
    normal = _key(key)
    if inherited == "sku" and normal == "imageurls":
        return "sku"
    if normal in _IMAGE_KEY_ROLES:
        return _IMAGE_KEY_ROLES[normal]
    if any(
        token in normal
        for token in (
            "recommend",
            "guess",
            "similar",
            "relate",
            "广告",
            "推荐",
            "review",
            "comment",
            "avatar",
            "portrait",
            "icon",
            "sprite",
            "logo",
        )
    ):
        return None
    if inherited:
        return inherited
    if any(token in normal for token in ("sku", "spec", "color", "prop")):
        return "sku"
    if any(token in normal for token in ("detail", "desc", "description")):
        return "detail"
    # A product's image array is useful evidence; arbitrary picUrl/mimgUrl
    # fields also occur in service badges and must not become product images.
    return "main" if normal in {"image", "images", "imageurl", "imagepath"} else None


def _looks_like_image(value: str) -> bool:
    return (
        bool(re.search(r"\.(?:jpe?g|png|webp|gif|avif)(?:[?#]|$)", value, re.I))
        or "360buyimg.com" in value
    )


class JDAdapter:
    """Parse one 京东 page without depending on a fixed image count."""

    def inspect_page(self, html: str, resolved_url: str) -> JDPageEntry:
        parser = _JDPageParser()
        parser.feed(html)
        title = _normalise_title(
            " ".join(parser.title_parts) or parser.meta.get("og:title")
        )
        body = " ".join(parser.visible_parts)
        parsed_host = (urlparse(resolved_url).hostname or "").casefold()
        haystack = " ".join((resolved_url, title or "", body)).casefold()
        risk = tuple(
            marker for marker in _RISK_MARKERS if marker.casefold() in haystack
        )
        if any(
            parsed_host == host or parsed_host.endswith(f".{host}")
            for host in _RISK_HOSTS
        ):
            risk = (*risk, parsed_host)
        if risk:
            return JDPageEntry(
                "risk_controlled",
                resolved_url,
                title,
                risk,
                _extract_product_id(resolved_url),
            )
        login = tuple(
            marker for marker in _LOGIN_MARKERS if marker.casefold() in haystack
        )
        if login or any(part in parsed_host for part in ("passport", "login")):
            return JDPageEntry(
                "login_required",
                resolved_url,
                title,
                login or ("login",),
                _extract_product_id(resolved_url),
            )
        not_found = tuple(
            marker for marker in _NOT_FOUND_MARKERS if marker.casefold() in haystack
        )
        if not_found:
            return JDPageEntry(
                "product_not_found",
                resolved_url,
                title,
                not_found,
                _extract_product_id(resolved_url),
            )
        product_id = _extract_product_id(resolved_url, *parser.scripts)
        if title or product_id or parser.images:
            return JDPageEntry("product", resolved_url, title, (), product_id)
        return JDPageEntry("empty", resolved_url, None, (), None)

    def parse_product(
        self, html: str, source_url: str, resolved_url: str | None = None
    ) -> JDProduct:
        target_url = resolved_url or source_url
        parser = _JDPageParser()
        parser.feed(html)
        entry = self.inspect_page(html, target_url)
        if entry.status != "product":
            warning = {
                "login_required": "京东页面要求登录或风控验证。",
                "risk_controlled": "京东页面触发了风控或验证码。",
                "product_not_found": "京东商品不存在或已下架。",
                "empty": "未读取到京东商品页面内容。",
            }.get(entry.status)
            return JDProduct(
                entry.status,
                source_url,
                target_url,
                entry.product_id,
                warnings=(warning,) if warning else (),
            )

        json_values = [
            value for _, script in parser.scripts for value in _iter_json_values(script)
        ]
        candidates: list[tuple[str, ImageRole, str, str]] = []
        diagnostics: dict[str, int] = {
            "dom_elements": len(parser.images),
            "dom_url_values": 0,
            "json_values": 0,
            "seen": 0,
            "accepted": 0,
            "rejected_image_filter": 0,
            "dedup_identity": 0,
            "recommendation_rejected": 0,
        }
        for item in parser.images:
            role = _role_for_image_url(
                item.url, _role_from_context(item.context), item.context
            )
            if role is None:
                diagnostics["recommendation_rejected"] += 1
                continue
            diagnostics["dom_url_values"] += 1
            candidates.append((item.url, role, item.source, item.context))

        fields: dict[str, object] = {}
        properties: list[dict[str, str]] = []
        sku_groups: list[JDSkuGroup] = []
        skus: list[JDSku] = []

        def walk(
            value: object,
            inherited_role: ImageRole | None = None,
            path: tuple[str, ...] = (),
            blocked: bool = False,
        ) -> None:
            if isinstance(value, str):
                context = ".".join(path)
                role = _role_for_image_url(value, inherited_role, context)
                if not blocked and role and _looks_like_image(value):
                    candidates.append((value, role, "json", context))
                    diagnostics["json_values"] += 1
                return
            if isinstance(value, list):
                for item in value:
                    walk(item, inherited_role, path, blocked)
                return
            if not isinstance(value, dict):
                return
            for raw_key, child in value.items():
                key = str(raw_key)
                lower_key = _key(key)
                child_blocked = blocked or any(
                    token in lower_key
                    for token in (
                        "recommend",
                        "guess",
                        "similar",
                        "relate",
                        "广告",
                        "推荐",
                        "review",
                        "comment",
                        "avatar",
                        "portrait",
                        "icon",
                        "sprite",
                        "logo",
                    )
                )
                role = None if child_blocked else _json_role(key, inherited_role)
                if lower_key in {
                    "name",
                    "title",
                    "productname",
                    "goodsname",
                    "skuname",
                } and isinstance(child, str):
                    fields.setdefault("title", child)
                if lower_key in {
                    "price",
                    "saleprice",
                    "jdpprice",
                    "lowestprice",
                    "minprice",
                }:
                    fields.setdefault("price", _text(child))
                if lower_key in {"shopname", "vendorname", "shop"} and isinstance(
                    child, str
                ):
                    fields.setdefault("shop_name", child)
                if lower_key in {"sales", "salecount", "commentsales"}:
                    fields.setdefault("sales", _text(child))
                if lower_key in {"productid", "skuid", "itemid"}:
                    fields.setdefault("product_id", _text(child))
                if lower_key in {
                    "properties",
                    "params",
                    "productparams",
                    "specifications",
                } and isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            name = _text(
                                item.get("name") or item.get("key") or item.get("label")
                            )
                            val = _text(
                                item.get("value") or item.get("val") or item.get("text")
                            )
                            if name and val:
                                properties.append({"name": name, "value": val})
                if lower_key in {
                    "skupropgroups",
                    "specgroups",
                    "dimensions",
                    "specs",
                }:
                    sku_groups.extend(_parse_sku_groups(child))
                if lower_key in {"sku", "skus", "skuinfo", "skuinfos"} and isinstance(
                    child, (dict, list)
                ):
                    records = child if isinstance(child, list) else [child]
                    for item in records:
                        if not isinstance(item, dict):
                            continue
                        sku_id = _text(
                            item.get("skuId")
                            or item.get("skuID")
                            or item.get("id")
                            or item.get("sku_id")
                        )
                        if sku_id:
                            sku_properties = (
                                item.get("properties")
                                or item.get("props")
                                or item.get("attributes")
                                or item.get("specs")
                            )
                            skus.append(
                                JDSku(
                                    sku_id,
                                    properties=_parse_sku_properties(sku_properties),
                                    price=_text(
                                        item.get("price") or item.get("salePrice")
                                    )
                                    or None,
                                    stock=_text(
                                        item.get("stock") or item.get("stockState")
                                    )
                                    or None,
                                    image_url=_sku_image_url(item, target_url),
                                )
                            )
                if isinstance(child, (dict, list)):
                    walk(child, role, (*path, key), child_blocked)
                elif (
                    not child_blocked
                    and isinstance(child, str)
                    and _looks_like_image(child)
                ):
                    context = ".".join((*path, key))
                    candidate_role = _role_for_image_url(child, role, context)
                    if candidate_role:
                        candidates.append((child, candidate_role, "json", context))
                        diagnostics["json_values"] += 1

        for value in json_values:
            walk(value)
        product_id = (
            entry.product_id
            or _text(fields.get("product_id"))
            or _extract_product_id(target_url)
        )
        title = entry.title or _normalise_title(_text(fields.get("title")))
        price = _text(fields.get("price")) or _find_price(
            " ".join(parser.visible_parts)
        )
        shop_name = _text(fields.get("shop_name")) or _find_shop_name(html)
        sales = _text(fields.get("sales")) or None
        images = _finalise_images(candidates, target_url, diagnostics)
        warnings: list[str] = []
        if not images:
            warnings.append("已读取到京东商品字段，但未读取到商品图片。")
        return JDProduct(
            "product",
            source_url,
            target_url,
            product_id,
            title,
            price,
            shop_name,
            sales,
            tuple(_dedupe_mappings(properties)),
            (),
            tuple(images),
            tuple(_dedupe_sku_groups(sku_groups)),
            tuple(_dedupe_skus(skus)),
            " ".join(parser.visible_parts[:80]),
            diagnostics,
            tuple(warnings),
        )


def _dedupe_skus(values: list[JDSku]) -> list[JDSku]:
    """Keep one row per SKU while retaining the richest public fields."""
    by_id: dict[str, JDSku] = {}
    for sku in values:
        current = by_id.get(sku.sku_id)
        if current is None:
            by_id[sku.sku_id] = sku
            continue
        by_id[sku.sku_id] = JDSku(
            sku.sku_id,
            properties=current.properties or sku.properties,
            price=current.price or sku.price,
            stock=current.stock or sku.stock,
            image_url=current.image_url or sku.image_url,
        )
    return list(by_id.values())


def _normalise_title(value: object) -> str | None:
    title = _text(value)
    if not title or title in _GENERIC_TITLES:
        return None
    return (
        re.sub(r"\s*[-|｜].*(?:京东|JD\.com).*$", "", title, flags=re.I).strip() or None
    )


def _find_price(body: str) -> str | None:
    match = re.search(r"(?:￥|¥|价格|售价)\s*([0-9]+(?:\.[0-9]{1,2})?)", body)
    return match.group(1) if match else None


def _dedupe_mappings(values: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for value in values:
        key = (value.get("name", ""), value.get("value", ""))
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _finalise_images(
    candidates: list[tuple[str, ImageRole, str, str]],
    base_url: str,
    diagnostics: dict[str, int],
) -> tuple[JDImage, ...]:
    priority = {"detail": 1, "main": 2, "sku": 3}
    rejected_rendered_urls = {
        _clean_image_url(raw, base_url)
        for raw, role, source, context in candidates
        if source == "rendered" and not _image_allowed(raw, context, role, raw)
    }
    selected: dict[str, tuple[ImageRole, str, str, int]] = {}
    for raw_url, role, source, context in candidates:
        diagnostics["seen"] += 1
        url = _clean_image_url(raw_url, base_url)
        if not url:
            diagnostics["rejected_image_filter"] += 1
            continue
        if url in rejected_rendered_urls or not _image_allowed(
            url, context, role, raw_url
        ):
            diagnostics["rejected_image_filter"] += 1
            continue
        previous = selected.get(url)
        if previous:
            candidate_rank = (source == "rendered" and role == "main", priority[role])
            previous_rank = (
                previous[1] == "rendered" and previous[0] == "main",
                priority[previous[0]],
            )
            if candidate_rank > previous_rank:
                selected[url] = (role, source, context, previous[3])
            diagnostics["dedup_identity"] += 1
            continue
        selected[url] = (role, source, context, len(selected))
        diagnostics["accepted"] += 1
    grouped: dict[ImageRole, list[tuple[str, str, str, int]]] = {
        "main": [],
        "detail": [],
        "sku": [],
    }
    for url, value in selected.items():
        grouped[value[0]].append((url, value[1], value[2], value[3]))
    result: list[JDImage] = []
    for role in ("main", "detail", "sku"):
        for ordinal, (url, source, context, _) in enumerate(grouped[role]):
            result.append(JDImage(url, role, ordinal, source, context))
    return tuple(result)


def _is_supported_jd_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold()
    return parsed.scheme in {"http", "https"} and any(
        host == value or host.endswith(f".{value}") for value in JD_HOSTS
    )


def parse_product_html(
    html: str, source_url: str, resolved_url: str | None = None
) -> JDProduct:
    if not _is_supported_jd_url(source_url) and not _is_supported_jd_url(
        resolved_url or ""
    ):
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "京东适配器只接受京东商品链接。"
        )
    return JDAdapter().parse_product(html, source_url, resolved_url)


def crawl_one_jd(url: str, timeout_ms: int = 30_000) -> JDProduct:
    if classify_site(url) is not SiteFamily.JD:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "京东抓取只接受京东 URL，已拒绝执行。"
        )
    request = Request(
        url.strip(),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.jd.com/",
        },
    )
    with urlopen(request, timeout=max(5, timeout_ms / 1000)) as response:  # noqa: S310 - URL policy checked above
        content = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        html = content.decode(charset, errors="replace")
        resolved_url = response.geturl()
    return parse_product_html(html, url.strip(), resolved_url)


def _scroll_page_for_lazy_images(page: Page, timeout_ms: int) -> None:
    budget = min(12_000, max(2_000, timeout_ms // 3))
    for _ in range(max(8, min(40, budget // 300))):
        metrics = page.evaluate(
            """
            () => {
              const d = document.documentElement;
              const b = document.body;
              const h = Math.max(d?.scrollHeight || 0, b?.scrollHeight || 0);
              const v = window.innerHeight || 800;
              const y = Math.min(
                Math.max(0, h - v), window.scrollY + Math.max(600, v * 0.8)
              );
              window.scrollTo(0, y);
              return {y, h, v};
            }
            """
        )
        page.wait_for_timeout(250)
        if (
            isinstance(metrics, dict)
            and metrics.get("y", 0)
            >= max(0, metrics.get("h", 0) - metrics.get("v", 800)) - 4
        ):
            break
    with suppress(Exception):
        page.evaluate("() => window.scrollTo(0, 0)")


def _activate_detail_tab(page: Page) -> None:
    selectors = (
        "text=图文详情",
        "text=商品详情",
        "[id*='detail']",
        "[class*='detail-tab']",
    )
    for selector in selectors:
        with suppress(Exception):
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                locator.click(timeout=1_000)
                page.wait_for_timeout(500)
                return


def _computed_background_image_fragment(page: Page) -> str:
    """Expose computed CSS backgrounds to the HTML parser.

    京东详情图常把图片放在 CSS ``background-image`` 中，而不是 ``<img>``
    或内联 ``style``。参考公开的 jd-product-crawler 做法，在浏览器渲染完成后
    读取计算样式，再合成为一个很小的 HTML 片段交给现有的 URL/角色过滤器。
    这不会发起额外请求，也不会改变页面或绕过风控。
    """
    try:
        values = page.evaluate(
            r"""
            () => Array.from(document.querySelectorAll('*')).flatMap((element) => {
              const background = getComputedStyle(element).backgroundImage || '';
              const urls = Array.from(
                background.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)
              ).map((match) => match[1]).filter(Boolean);
              if (!urls.length) return [];
              const markers = [];
              let node = element;
              for (let depth = 0; node && depth < 12;
                   depth += 1, node = node.parentElement) {
                markers.push(
                  node.id,
                  typeof node.className === 'string' ? node.className : '',
                  node.getAttribute('data-role'),
                  node.getAttribute('aria-label'),
                  node.getAttribute('title'),
                );
              }
              const marker = markers.filter(Boolean).join(' ');
              return urls.map((url) => ({url, marker}));
            }).slice(0, 500)
            """
        )
    except Exception:
        return ""
    if not isinstance(values, list):
        return ""
    fragments: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        marker_value = item.get("marker")
        marker = marker_value if isinstance(marker_value, str) else ""
        fragments.append(
            f'<div class="jd-computed-background-image {escape(marker, quote=True)}">'
            f'<img src="{escape(url.strip(), quote=True)}"></div>'
        )
    return "".join(fragments)


def _rendered_image_fragment(page: Page) -> str:
    """Expose loaded ``currentSrc`` values, including browser-only CDN URLs.

    JD's mobile page keeps a stale ``item.m.jd.com/product/jfs/...`` URL in
    JSON while the browser actually loads the image from its 360buyimg CDN.
    Serialising ``currentSrc`` lets the API persist the same image the user
    sees locally.  Natural dimensions and ancestor markers also let the
    normal filter drop service icons and other tiny chrome images.
    """

    try:
        values = page.evaluate(
            """
            () => Array.from(document.images).map((element) => {
              const markers = [];
              let node = element;
              for (
                let depth = 0;
                node && depth < 12;
                depth += 1, node = node.parentElement
              ) {
                const className =
                  typeof node.className === 'string' ? node.className : '';
                markers.push(
                  node.id,
                  className,
                  node.getAttribute('data-role'),
                  node.getAttribute('aria-label'),
                  node.getAttribute('title'),
                );
              }
              return {
                url: element.currentSrc || element.src || '',
                marker: markers.filter(Boolean).join(' '),
                width: element.naturalWidth || 0,
                height: element.naturalHeight || 0,
              };
            }).filter((item) => item.url && !item.url.startsWith('data:')).slice(0, 800)
            """
        )
    except Exception:
        return ""
    if not isinstance(values, list):
        return ""
    fragments: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        marker_value = item.get("marker")
        marker = marker_value if isinstance(marker_value, str) else ""
        width_value = item.get("width")
        height_value = item.get("height")
        width = width_value if isinstance(width_value, int) else 0
        height = height_value if isinstance(height_value, int) else 0
        if width <= 0 or height <= 0:
            continue
        marker = f"{marker} rendered-dimensions-{width}x{height}".strip()
        fragments.append(
            f'<div class="jd-rendered-image {escape(marker, quote=True)}">'
            f'<img src="{escape(url.strip(), quote=True)}"></div>'
        )
    return "".join(fragments)


def _rendered_sku_fragment(page: Page) -> str:
    """Keep DOM option labels and image associations even when images repeat.

    Reading the selected product's controls does not invent SKU IDs or visit
    recommendations. Multiple options may legitimately share a seller image.
    """
    try:
        groups = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('.specification-group'))
              .map(group => {
                const name = group.querySelector('.specification-group-label')
                  ?.textContent?.trim() || '';
                const values = [];
                const image_urls = {};
                const options = group.querySelectorAll('.specification-item-sku');
                for (const option of options) {
                  const image = option.querySelector('.specification-item-sku-image');
                  const label = option.querySelector('.specification-item-sku-text')
                    ?.textContent?.trim() || image?.alt?.trim() || '';
                  if (!label) continue;
                  values.push(label);
                  const url = image?.currentSrc || image?.src;
                  if (url) image_urls[label] = url;
                }
                return {name, values, image_urls};
              }).filter(group => group.name && group.values.length)
            """
        )
    except Exception:
        return ""
    if not isinstance(groups, list):
        return ""
    payload = json.dumps({"skuPropGroups": groups}, ensure_ascii=False).replace(
        "<", "\\u003c"
    )
    return f'<script type="application/json">{payload}</script>'


def _rendered_jd_content(page: Page) -> str:
    parts = [page.content()]
    rendered = _rendered_image_fragment(page)
    if rendered:
        parts.append(rendered)
    computed = _computed_background_image_fragment(page)
    if computed:
        parts.append(computed)
    sku = _rendered_sku_fragment(page)
    if sku:
        parts.append(sku)
    with suppress(Exception):
        shadow = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('*'))
              .flatMap(e => e.shadowRoot ? [e.shadowRoot.innerHTML] : [])
              .join('\\n')
            """
        )
        if shadow:
            parts.append(f"<div class='jd-shadow-content'>{shadow}</div>")
    main_frame = getattr(page, "main_frame", None)
    for frame in getattr(page, "frames", ()):
        if frame is main_frame:
            continue
        frame_url = str(getattr(frame, "url", "") or "")
        host = (urlparse(frame_url).hostname or "").casefold()
        if not (host.endswith("jd.com") or host.endswith("360buyimg.com")):
            continue
        with suppress(Exception):
            content = frame.content()
            if content.strip():
                content = re.sub(
                    r"<title\\b[^>]*>.*?</title>", "", content, flags=re.I | re.S
                )
                parts.append(f"<div class='jd-detail-iframe detail'>{content}</div>")
            computed = _computed_background_image_fragment(frame)
            if computed:
                parts.append(f"<div class='jd-detail-iframe detail'>{computed}</div>")
    return "\n".join(parts)


def _crawl_rendered_jd_page(
    page: Page, source_url: str, timeout_ms: int, timeout_error: type[Exception]
) -> JDProduct:
    page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(1_000)
    with suppress(timeout_error):
        page.wait_for_load_state("load", timeout=min(timeout_ms, 5_000))
    initial = page.content()
    entry = JDAdapter().inspect_page(initial, page.url)
    if entry.status not in {"login_required", "risk_controlled", "product_not_found"}:
        with suppress(Exception):
            _activate_detail_tab(page)
            _scroll_page_for_lazy_images(page, timeout_ms)
    return JDAdapter().parse_product(_rendered_jd_content(page), source_url, page.url)


def crawl_one_jd_browser(
    url: str, timeout_ms: int = 30_000, profile_dir: str | None = None
) -> JDProduct:
    if classify_site(url) is not SiteFamily.JD:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "京东浏览器抓取只接受京东 URL，已拒绝执行。"
        )
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("BROWSER_RENDERER_UNAVAILABLE") from exc
    with sync_playwright() as playwright:
        if profile_dir:
            with connect_to_owned_chrome(
                playwright, profile_dir, network_mode="direct"
            ) as (
                _browser,
                context,
            ):
                page = context.new_page()
                return _crawl_rendered_jd_page(
                    page, url.strip(), timeout_ms, PlaywrightTimeoutError
                )
        browser = playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
        context = browser.new_context(locale="zh-CN", user_agent="Mozilla/5.0")
        page = context.new_page()
        try:
            return _crawl_rendered_jd_page(
                page, url.strip(), timeout_ms, PlaywrightTimeoutError
            )
        finally:
            with suppress(PlaywrightError):
                context.close()
            browser.close()


# Public aliases make the adapter boundary easy to discover in tests and callers.
Product = JDProduct
Image = JDImage
crawl_one = crawl_one_jd
crawl_one_browser = crawl_one_jd_browser
