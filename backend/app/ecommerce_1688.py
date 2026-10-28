"""Alibaba 1688 page-entry, public-field, and human-assisted browser adapter.

The parser is intentionally limited to fields already present in rendered HTML
or embedded public page data. Browser access uses a user-visible installed
Chrome process only for the authorized profile; it never solves CAPTCHA,
bypasses risk controls, or persists credential material.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

if TYPE_CHECKING:
    from playwright.sync_api import Page

from .ecommerce_chrome import connect_to_owned_chrome
from .ecommerce_platform import PlatformPolicyError, SiteFamily, classify_site

ImageRole = Literal["main", "detail", "sku"]

PageEntryStatus = Literal[
    "product",
    "login_required",
    "risk_controlled",
    "product_not_found",
    "empty",
]

_LOGIN_MARKERS = (
    "请登录",
    "登录后查看",
    "扫码登录",
    "密码登录",
    "1688登录",
)
_RISK_MARKERS = (
    "验证码",
    "滑块验证",
    "访问受限",
    "请求异常",
    "安全验证",
)
_NOT_FOUND_MARKERS = (
    "商品不存在",
    "页面不存在",
    "已下架",
)
_GENERIC_TITLES = {"1688", "阿里巴巴", "", "登录", "1688登录"}


@dataclass(frozen=True)
class PageEntryResult:
    status: PageEntryStatus
    resolved_url: str
    title: str | None = None
    matched_markers: tuple[str, ...] = field(default_factory=tuple)
    product_id: str | None = None

    @property
    def is_product(self) -> bool:
        return self.status == "product"


@dataclass(frozen=True)
class Alibaba1688Image:
    """A public product image with a platform-specific role."""

    url: str
    role: ImageRole = "main"
    ordinal: int = 0
    source: str = "dom"
    context: str = ""
    # Keep dimensions from the original CDN URL even though
    # ``_clean_image_url`` removes delivery-size suffixes for deduplication.
    # They help distinguish portrait/strip detail assets after normalization.
    dimensions: tuple[int, int] | None = None


@dataclass(frozen=True)
class Alibaba1688SkuGroup:
    """One 1688 specification group and its visible values."""

    name: str
    values: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Alibaba1688Sku:
    """A normalized SKU row extracted from public page data."""

    sku_id: str
    properties: tuple[dict[str, str], ...] = field(default_factory=tuple)
    price: str | None = None
    stock: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class Alibaba1688Product:
    """Basic public product fields for the Phase C adapter contract."""

    status: PageEntryStatus
    source_url: str
    resolved_url: str
    product_id: str | None = None
    title: str | None = None
    price: str | None = None
    min_order_quantity: str | None = None
    shop_name: str | None = None
    sales: str | None = None
    props_text: str = ""
    properties: tuple[dict[str, str], ...] = field(default_factory=tuple)
    services: tuple[str, ...] = field(default_factory=tuple)
    images: tuple[Alibaba1688Image, ...] = field(default_factory=tuple)
    sku_prop_groups: tuple[Alibaba1688SkuGroup, ...] = field(default_factory=tuple)
    skus: tuple[Alibaba1688Sku, ...] = field(default_factory=tuple)
    image_diagnostics: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def platform(self) -> str:
        return "alibaba1688"

    @property
    def is_product(self) -> bool:
        return self.status == "product"


class _RenderedPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.scripts: list[tuple[str, str]] = []
        self._in_title = False
        self._in_script = False
        self._in_style = False
        self._script_type = ""
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        attr_map = {key.casefold(): value or "" for key, value in attrs}
        if lowered == "title":
            self._in_title = True
        elif lowered == "meta":
            key = attr_map.get("property") or attr_map.get("name")
            content = attr_map.get("content")
            if key and content:
                self.meta[key.casefold()] = content
        elif lowered == "script":
            self._in_script = True
            self._script_type = attr_map.get("type", "").casefold()
            self._script_parts = []
        elif lowered == "style":
            self._in_style = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = False
        elif lowered == "script":
            if self._in_script:
                self.scripts.append((self._script_type, "".join(self._script_parts)))
            self._in_script = False
            self._script_type = ""
            self._script_parts = []
        elif lowered == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)
        elif self._in_title:
            self.title_parts.append(data)
        elif not self._in_style:
            self.visible_parts.append(data)


_TITLE_RE = re.compile(r"<title[^>]*>(?P<value>.*?)</title>", re.I | re.S)
_META_TITLE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:title|title)["\'][^>]+content=["\'](?P<value>.*?)["\'][^>]*>',
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_OFFER_ID_RE = re.compile(r"/offer/(\d+)(?:\.html)?", re.I)


def _clean_text(value: str | None) -> str:
    return " ".join(unescape(value or "").split())


def _parse_page(html: str) -> _RenderedPageParser:
    parser = _RenderedPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed marketplace HTML should still be inspected conservatively
        # using the parts collected before the parser encountered the error.
        pass
    return parser


def _page_text(html: str) -> str:
    parser = _parse_page(html)
    if parser.visible_parts:
        return _clean_text(" ".join(parser.visible_parts))
    without_non_text = re.sub(
        r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>",
        " ",
        html,
        flags=re.I | re.S,
    )
    return _clean_text(_TAG_RE.sub(" ", without_non_text))


def _extract_title(html: str, parser: _RenderedPageParser | None = None) -> str | None:
    page = parser or _parse_page(html)
    value = _clean_text(" ".join(page.title_parts))
    if not value:
        value = _clean_text(page.meta.get("og:title") or page.meta.get("title"))
    if not value:
        match = _TITLE_RE.search(html) or _META_TITLE_RE.search(html)
        value = _clean_text(match.group("value") if match else None)
    return value or None


def _json_documents(parser: _RenderedPageParser) -> list[Any]:
    documents: list[Any] = []
    for script_type, raw in parser.scripts:
        if "json" not in script_type and not raw.lstrip().startswith(("{", "[")):
            continue
        try:
            documents.append(json.loads(raw.strip()))
        except (TypeError, ValueError):
            continue
    return documents


def _walk_json(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for nested in value.values():
            found.extend(_walk_json(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_walk_json(nested))
    return found


def _structured_value(documents: list[Any], keys: tuple[str, ...]) -> Any:
    wanted = {key.casefold() for key in keys}
    for item in documents:
        for mapping in _walk_json(item):
            for key, value in mapping.items():
                if str(key).casefold() in wanted and value not in (None, "", [], {}):
                    return value
    return None


def _regex_value(html: str, keys: tuple[str, ...]) -> str | None:
    key_pattern = "|".join(re.escape(key) for key in keys)
    pattern = re.compile(
        rf"[\"'](?:{key_pattern})[\"']\s*:\s*[\"']([^\"']+)[\"']",
        re.I,
    )
    match = pattern.search(html)
    return _clean_text(match.group(1)) if match else None


def _string_value(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        cleaned = _clean_text(str(value))
        return cleaned or None
    return None


def _first_numeric_id(html: str, documents: list[Any], resolved_url: str) -> str | None:
    url_match = _OFFER_ID_RE.search(resolved_url)
    if url_match:
        return url_match.group(1)
    structured = _structured_value(
        documents,
        ("offerId", "offer_id", "productId", "product_id", "itemId", "item_id"),
    )
    if structured is not None:
        match = re.search(r"\d{8,20}", str(structured))
        if match:
            return match.group(0)
    for match in re.finditer(
        r"[\"'](?:offerId|productId|itemId)[\"']\s*:\s*[\"']?(\d{8,20})", html, re.I
    ):
        return match.group(1)
    return None


def _price_value(
    html: str, parser: _RenderedPageParser, documents: list[Any]
) -> str | None:
    minimum = _string_value(_structured_value(documents, ("minPrice", "minimumPrice")))
    maximum = _string_value(_structured_value(documents, ("maxPrice", "maximumPrice")))
    if minimum and maximum and minimum != maximum:
        return f"{minimum}-{maximum}"
    structured = _string_value(
        _structured_value(
            documents, ("price", "salePrice", "currentPrice", "priceText")
        )
    )
    if structured:
        return structured
    meta_price = parser.meta.get("product:price:amount")
    if meta_price:
        return _clean_text(meta_price)
    match = re.search(
        r"[¥￥]\s*([0-9]+(?:\.[0-9]{1,2})?(?:\s*[-~至]\s*[0-9]+(?:\.[0-9]{1,2})?)?)",
        _page_text(html),
    )
    if match:
        return match.group(1).replace(" ", "")
    match = re.search(r"([0-9]+(?:\.[0-9]{1,2})?)\s*元", _page_text(html))
    return match.group(1) if match else None


def _label_value(text: str, labels: tuple[str, ...]) -> str | None:
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{pattern})\s*[:：]\s*([^，,|；;。\s]+)", text, re.I)
    return _clean_text(match.group(1)) if match else None


def _properties(value: Any) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    items: Iterable[tuple[Any, Any]]
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = (
            (item.get("name") or item.get("key"), item.get("value") or item.get("text"))
            for item in value
            if isinstance(item, dict)
        )
    else:
        return ()
    for name, raw_value in items:
        clean_name = _string_value(name)
        clean_value = _string_value(raw_value)
        if clean_name and clean_value:
            result.append({"name": clean_name, "value": clean_value})
    return tuple(result)


_MAIN_IMAGE_KEYS = {
    "image",
    "images",
    "imageurl",
    "imageurls",
    "mainimages",
    "mainimageurls",
    "productimages",
    "offerimages",
    "galleryimages",
    "itemimages",
    "imagelist",
    "imglist",
    "mainimagelist",
    "offerimglist",
    "offerimagelist",
    "carouselimages",
}
_DETAIL_IMAGE_KEYS = {
    "detailimages",
    "detailimageurls",
    "detailimagelist",
    "descimages",
    "descimageurls",
    "descriptionimages",
    "descriptionimageurls",
    "itemdescimages",
    "detailimgs",
    "descimgs",
    "detailimglist",
    "detailurl",
    "detailurls",
    "descurl",
    "descurls",
    "descriptionurl",
    "descriptionurls",
    "offerdetailimages",
}
_SKU_IMAGE_KEYS = {
    "skuimages",
    "skuimage",
    "skuimageurl",
    "propimages",
    "propimage",
    "propertyimages",
    "propertyimage",
    "specimages",
    "specimage",
    "colorimages",
    "colorimage",
    "picurl",
    "picurls",
}
_SKU_CONTAINER_KEYS = {
    "skus",
    "skulist",
    "skuinfo",
    "skumap",
    "skuitems",
    "skuinfos",
    "skuvalues",
}
_SKU_ID_KEYS = {"skuid", "sku_id", "skucode", "sku_code", "specid", "spec_id"}


def _clean_image_url(raw: Any, base_url: str) -> str | None:
    if not isinstance(raw, str):
        return None
    value = unescape(raw).replace("\\/", "/").strip().strip("\"'")
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return None
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = (parsed.hostname or "").casefold()
    if hostname.startswith(("login.", "passport.", "auth.", "member.")) or hostname in {
        "qr.1688.com",
        "login.1688.com",
        "passport.alibaba.com",
        "login.alibaba.com",
    }:
        return None
    sensitive_keys = {
        "cookie",
        "token",
        "_tb_token_",
        "access_token",
        "authorization",
        "session",
        "signature",
        "sign",
        "auth",
        "ticket",
        "password",
        "passwd",
    }
    if any(
        key.casefold() in sensitive_keys
        or any(token in key.casefold() for token in ("cookie", "token", "session"))
        for key in parse_qs(parsed.query)
    ):
        return None
    path = unquote(parsed.path)
    path = re.sub(
        r"(?P<ext>\.[a-z0-9]{2,6})[_-](?:\d{2,5}x\d{2,5}(?:q\d+)?|q\d+|sum)(?:\.[a-z0-9]{2,6})?$",
        r"\g<ext>",
        path,
        flags=re.I,
    )
    path = re.sub(
        r"[_-](?:\d{2,5}x\d{2,5}(?:q\d+)?|q\d+|sum|\.webp)(?=\.[a-z0-9]{2,6}$)",
        "",
        path,
        flags=re.I,
    )
    # Gallery and SKU swatches use different CDN delivery variants for the
    # same asset (for example ``.jpg_b.jpg``, ``.jpg_sum.jpg`` and
    # ``.jpg_.webp``). Normalize those suffixes before role reconciliation.
    path = re.sub(
        r"(?P<ext>\.[a-z0-9]{2,6})[_-](?:\d{2,5}x\d{2,5}(?:q\d+)?|q\d+|sum|b)(?:\.[a-z0-9]{2,6})?$",
        r"\g<ext>",
        path,
        flags=re.I,
    )
    path = re.sub(
        r"(?P<ext>\.[a-z0-9]{2,6})_?\.(?:webp|avif)$",
        r"\g<ext>",
        path,
        flags=re.I,
    )
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _image_dimensions(value: str) -> tuple[int, int] | None:
    # 1688 emits both ``600x600`` and ``tps-15-8`` style dimensions.
    # The latter is commonly a tiny SVG loading placeholder, not a product
    # asset, so it must participate in the same small-image filter.
    match = re.search(
        r"(?:^|[-_])(?P<w>\d{1,5})[-x](?P<h>\d{1,5})(?:q\d+)?(?:[-_.]|$)",
        value,
        re.I,
    )
    return (int(match.group("w")), int(match.group("h"))) if match else None


def _image_identity(value: str) -> str:
    parsed = urlparse(value)
    path = re.sub(
        r"(?P<ext>\.[a-z0-9]{2,6})[_-](?:\d+x\d+(?:q\d+)?|q\d+|sum|b)(?:\.[a-z0-9]{2,6})?$",
        r"\g<ext>",
        unquote(parsed.path).casefold(),
    )
    path = re.sub(
        r"(?P<ext>\.[a-z0-9]{2,6})_?\.(?:webp|avif)$",
        r"\g<ext>",
        path,
        flags=re.I,
    )
    return f"{(parsed.hostname or '').casefold()}{path}"


_SKU_CONTEXT_TOKENS = (
    "sku",
    "spec",
    "prop",
    "item-image-icon",
    "expand-view-item",
    "expand-view-list",
    "sku-image",
    "skuimg",
    "\u989c\u8272",
    "\u89c4\u683c",
    "\u5c3a\u7801",
)
_DETAIL_CONTEXT_TOKENS = (
    "detail",
    "desc",
    "description",
    "desc-content",
    "desc-lazyload",
    "html-description",
    "\u8be6\u60c5",
    "\u56fe\u6587",
)
_MAIN_CONTEXT_TOKENS = (
    # These are the gallery wrappers used by different generations of the
    # 1688 offer page. They are role evidence, not a quantity rule: a seller
    # may expose any number of carousel assets.
    "gallery",
    "od-gallery-list",
    "module-od-picture-gallery",
    "offer-img-list",
    "offer-image-list",
    "main-image",
    "main-image-list",
    "main-gallery",
    # JSON keys are camel-cased in the page state, so include their compact
    # forms for duplicate-role reconciliation as well.
    "mainimages",
    "mainimageurls",
    "productimages",
    "offerimages",
    "galleryimages",
    "itemimages",
    "imagelist",
    "imglist",
    "mainimagelist",
    "offerimglist",
    "offerimagelist",
    "carouselimages",
    "carousel",
    "\u8f6e\u64ad",
    "\u4e3b\u56fe",
)
_RECOMMENDATION_CONTEXT_TOKENS = (
    "offer-img",
    # The current 1688 recommendation module wraps its cards in these
    # containers.  ``offer-image-wrap`` is deliberately *not* included: it is
    # also used by the legitimate description renderer.
    "sdmap-dynamic-offer-list",
    "offer-list-wapper",
    "recommend",
    "recommendation",
    "productevaluation",
    "evaluate-images",
    "dynamic-backup-img",
    "backup-img",
    "\u5e97\u94fa\u63a8\u8350",
    "\u76f8\u5173\u63a8\u8350",
    "\u731c\u4f60\u559c\u6b22",
    "\u770b\u4e86\u53c8\u770b",
)


def _context_has_token(context: str, token: str) -> bool:
    """Match a DOM role token without matching it inside another word.

    DOM contexts are made from class/id strings. A raw substring check makes
    a page-level class such as ``detail-page`` look like a detail image
    container and can relabel the product gallery. Hyphenated BEM names are
    intentionally supported, so ``od-gallery-list`` also matches ``gallery``.
    Chinese markers have no word boundary and remain simple substring checks.
    """

    lowered = context.casefold()
    lowered_token = token.casefold()
    if any("\u4e00" <= char <= "\u9fff" for char in lowered_token):
        return lowered_token in lowered
    return re.search(
        rf"(?<![a-z0-9]){re.escape(lowered_token)}(?![a-z0-9])", lowered
    ) is not None


def _image_context_role(context: str) -> ImageRole:
    if any(_context_has_token(context, token) for token in _SKU_CONTEXT_TOKENS):
        return "sku"
    # Prefer an explicit gallery marker over a broad page-level ``detail``
    # class. 1688 often nests the product gallery inside a root element whose
    # class contains ``detail``; treating that ancestor as authoritative drops
    # otherwise valid main images into the detail bucket.
    if any(_context_has_token(context, token) for token in _MAIN_CONTEXT_TOKENS):
        return "main"
    if any(_context_has_token(context, token) for token in _DETAIL_CONTEXT_TOKENS):
        return "detail"
    return "main"


def _image_is_allowed(value: str, *, context: str = "") -> bool:
    lowered = value.casefold()
    context_lowered = context.casefold()
    sku_context = any(
        _context_has_token(context, token) for token in _SKU_CONTEXT_TOKENS
    )
    if any(
        token.casefold() in context_lowered
        for token in _RECOMMENDATION_CONTEXT_TOKENS
    ):
        return False

    ui_url_tokens = (
        "undefined",
        ".svg",
        "tps-",
        "gg_dtc",
    )
    if any(token in lowered for token in ui_url_tokens):
        return False

    ui_tokens = (
        "favicon",
        "sprite",
        "avatar",
        "qrcode",
        "qr-code",
        "loading",
        "placeholder",
        "default-icon",
        "defaulticon",
        "collect-icon",
        "shop-icon",
        "seller-icon",
        "logo",
        "icon",
        "captcha",
        "verify",
        "security",
        "1x1",
        "10x10",
    )
    for token in ui_tokens:
        # 1688 uses ``item-image-icon`` for the 32x32 SKU swatches. They are
        # intentionally allowed only when the surrounding DOM proves that the
        # element belongs to the SKU/specification picker.
        if token == "icon" and sku_context:
            continue
        if token in lowered or token in context_lowered:
            return False

    dimensions = _image_dimensions(lowered)
    if dimensions:
        smaller, larger = sorted(dimensions)
        if (
            "img.alicdn.com" in lowered
            and smaller <= 96
            and not sku_context
        ):
            return False
        if not sku_context and (larger <= 180 or smaller <= 48):
            return False
    return True


def _image_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_image_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        preferred = (
            "url",
            "image",
            "imageUrl",
            "src",
            "path",
            "pic",
            "picUrl",
            "original",
        )
        found = False
        for key in preferred:
            if key in value:
                found = True
                result.extend(_image_strings(value[key]))
        if not found:
            for item in value.values():
                result.extend(_image_strings(item))
        return result
    return []


def _image_role_for_key(key: str, inherited: ImageRole = "main") -> ImageRole:
    lowered = key.casefold().replace("-", "").replace("_", "")
    if lowered in {item.replace("-", "").replace("_", "") for item in _SKU_IMAGE_KEYS}:
        return "sku"
    if lowered in {
        item.replace("-", "").replace("_", "") for item in _DETAIL_IMAGE_KEYS
    }:
        return "detail"
    if lowered in {item.replace("-", "").replace("_", "") for item in _MAIN_IMAGE_KEYS}:
        return inherited if inherited == "sku" else "main"
    return inherited


def _json_image_values(documents: list[Any]) -> list[tuple[str, ImageRole, str]]:
    values: list[tuple[str, ImageRole, str]] = []

    def visit(value: Any, inherited: ImageRole = "main", context: str = "json") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key)
                lowered = key_text.casefold().replace("-", "").replace("_", "")
                role = _image_role_for_key(key_text, inherited)
                if lowered in {
                    "image",
                    "images",
                    "imageurl",
                    "imageurls",
                    "src",
                    "url",
                    "pic",
                    "picurl",
                    "mainimages",
                    "mainimageurls",
                    "productimages",
                    "offerimages",
                    "galleryimages",
                    "itemimages",
                    "imagelist",
                    "imglist",
                    "mainimagelist",
                    "offerimglist",
                    "offerimagelist",
                    "carouselimages",
                    "detailimages",
                    "detailimageurls",
                    "detailimagelist",
                    "descimages",
                    "descimageurls",
                    "descriptionimages",
                    "descriptionimageurls",
                    "itemdescimages",
                    "detailimglist",
                    "detailurl",
                    "detailurls",
                    "descurl",
                    "descurls",
                    "descriptionurl",
                    "descriptionurls",
                    "offerdetailimages",
                    "skuimages",
                    "skuimage",
                    "skuimageurl",
                    "propimages",
                    "propimage",
                    "propertyimages",
                    "propertyimage",
                    "specimages",
                    "specimage",
                    "colorimages",
                    "colorimage",
                    "picurls",
                }:
                    for raw in _image_strings(nested):
                        values.append((raw, role, f"{context}:{key_text}"))
                child_context = f"{context}:{key_text}"
                visit(
                    nested,
                    role if lowered in _SKU_CONTAINER_KEYS else inherited,
                    child_context,
                )
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, inherited, f"{context}[{index}]")

    for document in documents:
        visit(document)
    return values


def _image_candidates_1688(
    page_html: str,
    base_url: str,
    documents: list[Any],
    sku_image_values: list[tuple[str, str]] | None = None,
    diagnostics: dict[str, int] | None = None,
) -> tuple[Alibaba1688Image, ...]:
    candidates: list[Alibaba1688Image] = []
    stats = diagnostics if diagnostics is not None else {}

    def bump(name: str) -> None:
        stats[name] = stats.get(name, 0) + 1

    seen: dict[str, int] = {}
    identities: dict[str, int] = {}

    def add(raw: Any, role: ImageRole, source: str, context: str = "") -> None:
        bump("image_values_seen")
        raw_dimensions = _image_dimensions(str(raw)) if raw is not None else None
        cleaned = _clean_image_url(raw, base_url)
        if not cleaned:
            bump("rejected_invalid_url")
            return
        if not _image_is_allowed(cleaned, context=context):
            bump("rejected_image_filter")
            return
        identity = _image_identity(cleaned)
        existing_index = seen.get(cleaned)
        identity_index = identities.get(identity)
        index = existing_index if existing_index is not None else identity_index
        if index is not None:
            bump("dedup_exact" if existing_index is not None else "dedup_identity")
            existing = candidates[index]
            def evidence_score(image: Alibaba1688Image) -> int:
                image_context = image.context.casefold()
                explicit_sku = image.source == "sku" or any(
                    token.casefold() in image_context
                    for token in _SKU_CONTEXT_TOKENS
                )
                explicit_main = any(
                    _context_has_token(image.context, token)
                    for token in _MAIN_CONTEXT_TOKENS
                )
                explicit_detail = any(
                    _context_has_token(image.context, token)
                    for token in _DETAIL_CONTEXT_TOKENS
                )
                if image.role == "sku" and explicit_sku:
                    return 300
                if image.role == "main" and explicit_main:
                    return 250
                if image.role == "detail" and explicit_detail:
                    return 200
                if image.role == "sku":
                    return 150
                if image.role == "detail":
                    return 100
                return 50

            incoming = Alibaba1688Image(
                cleaned,
                role,
                existing.ordinal,
                source,
                context,
                raw_dimensions,
            )
            # A precise gallery context must survive a later duplicate found
            # in the description.  Conversely, an explicit SKU value is
            # allowed to claim the same CDN asset that was also emitted by the
            # broad gallery list (1688 commonly reuses those URLs).
            if evidence_score(incoming) > evidence_score(existing):
                candidates[index] = Alibaba1688Image(
                    cleaned,
                    incoming.role,
                    existing.ordinal,
                    incoming.source,
                    incoming.context,
                    incoming.dimensions or existing.dimensions,
                )
            return
        index = len(candidates)
        candidates.append(
            Alibaba1688Image(
                cleaned,
                role,
                index,
                source,
                context,
                raw_dimensions,
            )
        )
        seen[cleaned] = index
        identities[identity] = index
        bump("accepted_candidates")

    # DOM values retain rendered order and role context. A short text window is
    # not enough for 1688 descriptions: the actual image may be nested several
    # layers below a container such as ``#detail`` or
    # ``#desc-lazyload-container``. Build a lightweight ancestor context map so
    # those containers classify their images as detail assets without relying on
    # an arbitrary six-image gallery cutoff.
    def tag_attributes(tag: str) -> dict[str, str]:
        return {
            key.casefold(): unescape(value)
            for key, value in re.findall(
                r"([\w:-]+)\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I
            )
        }

    def quoted_attribute(tag: str, name: str) -> str:
        match = re.search(
            rf"\b{re.escape(name)}\s*=\s*(?P<quote>[\'\"])(?P<value>.*?)(?P=quote)",
            tag,
            re.I | re.S,
        )
        return unescape(match.group("value")) if match else ""

    void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}
    ancestor_contexts: dict[int, str] = {}
    ancestor_stack: list[tuple[str, str]] = []
    for dom_tag in re.finditer(
        r"<!--.*?-->|</?[\w:-]+\b[^>]*>", page_html, re.I | re.S
    ):
        token = dom_tag.group(0)
        if token.startswith("<!--"):
            continue
        closing = token.startswith("</")
        name_match = re.match(r"</?([\w:-]+)", token)
        if not name_match:
            continue
        name = name_match.group(1).casefold()
        if closing:
            for index in range(len(ancestor_stack) - 1, -1, -1):
                if ancestor_stack[index][0] == name:
                    del ancestor_stack[index:]
                    break
            continue
        attrs = tag_attributes(token)
        own_context = " ".join(
            attrs.get(attr, "") for attr in ("class", "id", "alt", "aria-label", "role")
        )
        if name in {"img", "source"}:
            ancestor_contexts[dom_tag.start()] = " ".join(
                context
                for _, context in (*ancestor_stack, (name, own_context))
                if context
            )
        if name not in void_tags and not token.rstrip().endswith("/>"):
            ancestor_stack.append((name, own_context))

    # DOM values retain rendered order and nearby role context.
    for match in re.finditer(r"<(?:img|source)\b[^>]*>", page_html, re.I | re.S):
        bump("dom_elements")
        tag = match.group(0)
        attrs = tag_attributes(tag)
        context_raw = ancestor_contexts.get(match.start(), "")
        if not context_raw:
            context_raw = page_html[
                max(0, match.start() - 700) : match.start()
            ].rsplit("</div>", 1)[-1]
        # Do not let a neighboring image URL contaminate this candidate's
        # role/filter context. In particular, 1688 often places a tiny
        # ``placeholder`` SVG immediately before a real gallery image;
        # retaining raw ``src`` text would incorrectly reject the real image.
        context_raw = re.sub(
            r"\b(?:src|srcset|data-[\w:-]+|style)\s*=\s*(['\"]).*?\1",
            " ",
            context_raw,
            flags=re.I | re.S,
        )
        context_raw = re.sub(r"https?://\S+", " ", context_raw, flags=re.I)
        context = (
            context_raw
            + " "
            + " ".join(
                attrs.get(name, "") for name in ("class", "id", "alt", "aria-label")
            )
        )
        role = _image_context_role(context)
        for attr in (
            "src",
            "data-src",
            "data-lazyload",
            "data-original",
            "data-original-src",
            "data-preview-src",
            "data-preview",
            "data-preview-url",
            "data-img-src",
            "data-imgurl",
            "data-img-url",
            "data-image",
            "data-image-url",
            "data-zoom-image",
            "data-zoom-src",
            "data-thumb",
            "data-thumb-src",
            "data-ks-lazyload",
            "data-lazy-src",
            "data-url",
        ):
            if attrs.get(attr):
                bump("dom_url_values")
                add(attrs[attr], role, "dom", context)
        for attr in ("srcset", "data-srcset"):
            for item in (attrs.get(attr) or "").split(","):
                raw = item.strip().split(" ", 1)[0]
                if raw:
                    bump("dom_url_values")
                    add(raw, role, "dom", context)
        # Some 1688 gallery/detail implementations use CSS backgrounds or
        # non-standard data attributes instead of an ``img`` element. Keep
        # these URLs in the same role bucket while retaining the surrounding
        # class/alt context for the normal UI-asset filter.
        for attr in ("style", "data-background", "data-bg", "data-image-url"):
            attr_value = quoted_attribute(tag, attr) or attrs.get(attr, "")
            for raw in re.findall(
                r"(?:url\(\s*[\"']?|^)(https?://[^\"'\s)]+)",
                attr_value,
                re.I,
            ):
                bump("dom_url_values")
                add(raw, role, "dom", context)

    # Detail panels may render their assets on a ``div`` background rather
    # than an ``img`` tag. Scan non-image tags for those explicit image-bearing
    # attributes as well.
    for match in re.finditer(r"<(?P<tag>[\w:-]+)\b[^>]*>", page_html, re.I | re.S):
        if match.group("tag").casefold() in {"img", "source"}:
            continue
        tag = match.group(0)
        attrs = {
            key.casefold(): unescape(value)
            for key, value in re.findall(
                r"([\w:-]+)\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I
            )
        }
        image_attrs = [
            quoted_attribute(tag, name) or attrs.get(name, "")
            for name in ("style", "data-background", "data-bg", "data-image-url")
            if quoted_attribute(tag, name) or attrs.get(name)
        ]
        if not image_attrs:
            continue
        context = " ".join(
            attrs.get(name, "") for name in ("class", "id", "alt", "aria-label")
        )
        role = _image_context_role(context)
        for attr_value in image_attrs:
            for raw in re.findall(
                r"(?:url\(\s*[\"']?|^)(https?://[^\"'\s)]+)",
                attr_value,
                re.I,
            ):
                bump("dom_url_values")
                add(raw, role, "dom", context)

    # Do not scan arbitrary <style> text. 1688 includes global UI sprites and
    # recommendation icons there; only explicit element image attributes are
    # product-image evidence.
    json_values = _json_image_values(documents)
    stats["json_values"] = len(json_values)
    for raw, role, context in json_values:
        add(raw, role, "json", context)
    for raw, context in sku_image_values or []:
        add(raw, "sku", "sku", context)

    # A rendered 1688 description can contain plain ``img`` nodes without a
    # detail-specific class or data key. Those nodes arrive here as ambiguous
    # ``main`` candidates used to be discarded after an assumed six-image
    # gallery limit. That silently loses legitimate 1688 main images. Keep all
    # candidates instead.  Do not infer a role from image shape or position:
    # portrait/strip gallery assets are valid main images on some offers, and a
    # fixed gallery count is not a reliable 1688 contract.
    # Do not impose a six-image cap here. The caller may choose how many assets
    # to process, but the parser must not discard or reshape source roles based
    # on a guessed quantity or aspect ratio.
    kept = candidates
    ordinals = {"main": 0, "detail": 0, "sku": 0}
    result: list[Alibaba1688Image] = []
    for image in kept:
        role = image.role
        result.append(
            Alibaba1688Image(
                image.url,
                role,
                ordinals[role],
                image.source,
                image.context,
                image.dimensions,
            )
        )
        ordinals[role] += 1
    return tuple(result)


def _mapping_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    wanted = {key.casefold().replace("-", "").replace("_", "") for key in keys}
    for key, value in mapping.items():
        normalized = str(key).casefold().replace("-", "").replace("_", "")
        if normalized in wanted and value not in (None, "", [], {}):
            return value
    return None


def _scalar_text(value: Any) -> str | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        cleaned = _clean_text(str(value))
        return cleaned or None
    if isinstance(value, dict):
        nested = _mapping_value(
            value, ("value", "text", "name", "label", "amount", "quantity")
        )
        return _scalar_text(nested)
    return None


def _property_pairs(value: Any) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    if isinstance(value, dict):
        for name, raw in value.items():
            clean_name = _scalar_text(name)
            clean_value = _scalar_text(raw)
            if clean_name and clean_value:
                result.append({"name": clean_name, "value": clean_value})
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _scalar_text(
                    _mapping_value(
                        item, ("name", "propName", "propertyName", "key", "label")
                    )
                )
                raw_value = _mapping_value(
                    item, ("value", "propValue", "propertyValue", "text", "label")
                )
                clean_value = _scalar_text(raw_value)
                if name and clean_value:
                    result.append({"name": name, "value": clean_value})
    return tuple(result)


def _extract_sku_groups(documents: list[Any]) -> tuple[Alibaba1688SkuGroup, ...]:
    groups: list[Alibaba1688SkuGroup] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def visit(value: Any, key_context: str = "") -> None:
        if isinstance(value, dict):
            name = _scalar_text(
                _mapping_value(
                    value, ("name", "propName", "propertyName", "specName", "key")
                )
            )
            raw_values = _mapping_value(
                value,
                ("values", "valueList", "propValues", "propertyValues", "options"),
            )
            if name and isinstance(raw_values, list):
                parsed: list[str] = []
                for raw in raw_values:
                    text = _scalar_text(raw)
                    if text and text not in parsed:
                        parsed.append(text)
                if parsed and (name, tuple(parsed)) not in seen:
                    seen.add((name, tuple(parsed)))
                    groups.append(Alibaba1688SkuGroup(name, tuple(parsed)))
            for key, nested in value.items():
                visit(nested, str(key))
        elif isinstance(value, list):
            for nested in value:
                visit(nested, key_context)

    for document in documents:
        visit(document)
    return tuple(groups)


def _extract_skus(
    documents: list[Any], base_url: str
) -> tuple[tuple[Alibaba1688Sku, ...], list[tuple[str, str]]]:
    entries: list[tuple[dict[str, Any], str | None, str]] = []
    seen_objects: set[int] = set()

    def visit(
        value: Any,
        in_sku_context: bool = False,
        forced_id: str | None = None,
        context: str = "json",
    ) -> None:
        if isinstance(value, dict):
            explicit_id = _mapping_value(value, ("skuId", "skuCode", "specId"))
            id_value = explicit_id or _mapping_value(value, ("id",))
            has_sku_signal = bool(
                _mapping_value(
                    value,
                    (
                        "properties",
                        "props",
                        "attributes",
                        "price",
                        "salePrice",
                        "stock",
                        "quantity",
                        "image",
                        "imageUrl",
                    ),
                )
            )
            # A generic ``id`` appears throughout navigation and analytics data.
            # Accept it only inside a known SKU container; explicit SKU ids are
            # safe outside that container when the row carries SKU fields.
            sku_row = (in_sku_context and id_value is not None) or (
                explicit_id is not None and has_sku_signal
            )
            if sku_row and id_value is not None and id(value) not in seen_objects:
                seen_objects.add(id(value))
                entries.append((value, _scalar_text(id_value) or forced_id, context))
            for key, nested in value.items():
                lowered = str(key).casefold().replace("-", "").replace("_", "")
                if lowered == "skumap" and isinstance(nested, dict):
                    for map_id, map_entry in nested.items():
                        if isinstance(map_entry, dict):
                            visit(
                                map_entry,
                                True,
                                _scalar_text(map_id),
                                f"{context}:{key}",
                            )
                else:
                    visit(
                        nested,
                        in_sku_context
                        or lowered
                        in {
                            item.replace("-", "").replace("_", "")
                            for item in _SKU_CONTAINER_KEYS
                        },
                        forced_id,
                        f"{context}:{key}",
                    )
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, in_sku_context, forced_id, f"{context}[{index}]")

    for document in documents:
        visit(document)

    skus: list[Alibaba1688Sku] = []
    image_values: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for mapping, sku_id, context in entries:
        if not sku_id or sku_id in seen_ids or len(sku_id) < 2:
            continue
        properties = _property_pairs(
            _mapping_value(
                mapping, ("properties", "props", "attributes", "specification")
            )
        )
        price = _scalar_text(
            _mapping_value(mapping, ("price", "salePrice", "currentPrice", "priceText"))
        )
        stock = _scalar_text(
            _mapping_value(
                mapping, ("stock", "quantity", "inventory", "availableStock")
            )
        )
        raw_image = _mapping_value(
            mapping,
            ("image", "imageUrl", "skuImage", "propImage", "pic", "picUrl", "url"),
        )
        image_url = next(
            (
                _clean_image_url(raw, base_url)
                for raw in _image_strings(raw_image)
                if _clean_image_url(raw, base_url)
            ),
            None,
        )
        if image_url:
            image_values.append((image_url, context))
        if not (properties or price or stock or image_url):
            continue
        seen_ids.add(sku_id)
        skus.append(Alibaba1688Sku(sku_id, properties, price, stock, image_url))
    return tuple(skus), image_values


class Alibaba1688Adapter:
    """Validate an 1688 page and extract its basic public product fields."""

    adapter_name = "1688"
    network_mode = "direct"
    session_profile = "domestic-1688"

    def inspect_page(self, html: str, resolved_url: str) -> PageEntryResult:
        if classify_site(resolved_url) is not SiteFamily.ALIBABA_1688:
            raise PlatformPolicyError(
                "NETWORK_POLICY_VIOLATION",
                "1688 页面入口收到非 1688 URL，已拒绝执行。",
            )
        parser = _parse_page(html)
        title = _extract_title(html, parser)
        documents = _json_documents(parser)
        structured_title = _string_value(
            _structured_value(documents, ("name", "title", "subject"))
        )
        text = _clean_text(" ".join(parser.visible_parts))
        if not text:
            text = _page_text(html)
        lowered = text.casefold()

        # Alibaba's actual challenge page may keep the visible copy generic
        # (for example, “请按照说明进行验证哦”) while exposing the decisive
        # signal only in the document title or the redirected ``punish`` URL.
        # Inspect all non-secret page-entry signals before attempting product
        # extraction so a challenge can never fall through as a product shell.
        entry_signal = " ".join((resolved_url, title or "", text)).casefold()
        risk_hits = tuple(
            marker for marker in _RISK_MARKERS if marker.casefold() in entry_signal
        )
        if "/_____tmd_____/punish" in resolved_url.casefold():
            risk_hits = (*risk_hits, "1688安全验证页面")
        if risk_hits:
            return PageEntryResult("risk_controlled", resolved_url, title, risk_hits)
        login_hits = tuple(
            marker for marker in _LOGIN_MARKERS if marker.casefold() in lowered
        )
        if login_hits:
            return PageEntryResult("login_required", resolved_url, title, login_hits)
        not_found_hits = tuple(
            marker for marker in _NOT_FOUND_MARKERS if marker.casefold() in lowered
        )
        if not_found_hits:
            return PageEntryResult(
                "product_not_found", resolved_url, title, not_found_hits
            )
        clean_title = next(
            (
                value
                for value in (title, structured_title)
                if value and value.casefold() not in _GENERIC_TITLES
            ),
            None,
        )
        structured_product_data = any(
            _structured_value(documents, keys) is not None
            for keys in (
                ("price", "salePrice", "currentPrice", "priceText"),
                ("mainImages", "mainImageUrls", "productImages", "offerImages"),
                ("shopName", "sellerName", "supplierName", "companyName"),
            )
        )
        if not text and clean_title is None and not structured_product_data:
            return PageEntryResult("empty", resolved_url, title)
        product_signal = (
            clean_title is not None
            or "/offer/" in resolved_url.casefold()
            and (
                any(
                    token in lowered
                    for token in ("商品", "价格", "起批", "供应商", "立即购买")
                )
                or structured_product_data
            )
        )
        if product_signal:
            product_id_match = _OFFER_ID_RE.search(resolved_url)
            return PageEntryResult(
                "product",
                resolved_url,
                clean_title,
                product_id=product_id_match.group(1) if product_id_match else None,
            )
        return PageEntryResult("empty", resolved_url, title)

    def parse_product(
        self,
        html: str,
        source_url: str,
        resolved_url: str | None = None,
    ) -> Alibaba1688Product:
        """Extract Phase C fields after applying the page-entry guard."""

        final_url = resolved_url or source_url
        entry = self.inspect_page(html, final_url)
        if not entry.is_product:
            warning_by_status = {
                "login_required": "1688 页面要求登录，未读取商品字段。",
                "risk_controlled": "1688 页面触发风控，未读取商品字段。",
                "product_not_found": "1688 商品不存在或已下架。",
                "empty": "1688 页面为空或尚未完成渲染。",
            }
            return Alibaba1688Product(
                status=entry.status,
                source_url=source_url,
                resolved_url=final_url,
                title=entry.title,
                warnings=(warning_by_status[entry.status],),
            )

        parser = _parse_page(html)
        documents = _json_documents(parser)
        text = _page_text(html)
        title = entry.title or _string_value(
            _structured_value(documents, ("name", "title", "subject"))
        )
        product_id = entry.product_id or _first_numeric_id(html, documents, final_url)
        price = _price_value(html, parser, documents)
        min_order = _string_value(
            _structured_value(
                documents,
                ("minOrderQuantity", "minOrder", "minimumOrderQuantity", "moq"),
            )
        ) or _label_value(text, ("起批量", "最小起订量", "MOQ"))
        shop_name = (
            _string_value(
                _structured_value(
                    documents,
                    ("shopName", "sellerName", "supplierName", "companyName"),
                )
            )
            or _clean_text(parser.meta.get("og:site_name"))
            or _label_value(
                text, ("供应商", "供应商名称", "店铺", "店铺名称", "公司名称")
            )
        )
        sales = _string_value(
            _structured_value(documents, ("sales", "soldCount", "tradeCount", "volume"))
        ) or _label_value(text, ("成交", "销量", "已售"))
        props_text = (
            _string_value(
                _structured_value(
                    documents, ("propsText", "propertiesText", "attributeText")
                )
            )
            or _regex_value(html, ("propsText", "propertiesText"))
            or ""
        )
        properties = _properties(
            _structured_value(documents, ("attributes", "properties", "props"))
        )
        sku_prop_groups = _extract_sku_groups(documents)
        skus, sku_image_values = _extract_skus(documents, final_url)
        image_diagnostics: dict[str, int] = {}
        images = _image_candidates_1688(
            html,
            final_url,
            documents,
            sku_image_values=sku_image_values,
            diagnostics=image_diagnostics,
        )
        service_markers = (
            "48 小时内发货",
            "48小时内发货",
            "免运费",
            "退货宝",
            "7 天无理由退货",
            "7天无理由退货",
        )
        services = tuple(marker for marker in service_markers if marker in text)
        warnings: list[str] = []
        for label, value in (
            ("商品标题", title),
            ("商品价格", price),
            ("供应商名称", shop_name),
        ):
            if not value:
                warnings.append(f"未从 1688 页面读取到{label}。")
        if not images:
            warnings.append("未从 1688 页面读取到商品图片，页面可能需要浏览器渲染。")
        return Alibaba1688Product(
            status="product",
            source_url=source_url,
            resolved_url=final_url,
            product_id=product_id,
            title=title,
            price=price,
            min_order_quantity=min_order,
            shop_name=shop_name,
            sales=sales,
            props_text=props_text,
            properties=properties,
            services=services,
            images=images,
            sku_prop_groups=sku_prop_groups,
            skus=skus,
            image_diagnostics=image_diagnostics,
            warnings=tuple(warnings),
        )


def crawl_one_1688(url: str, timeout_seconds: float = 30.0) -> Alibaba1688Product:
    """Fetch one 1688 page through an explicit direct-only opener.

    The opener disables environment/system proxies. It never submits credentials,
    follows login instructions, or attempts to bypass a risk-control page.
    """

    from urllib.request import ProxyHandler, Request, build_opener

    source_url = url.strip()
    if classify_site(source_url) is not SiteFamily.ALIBABA_1688:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "1688 抓取只接受 1688 URL，已拒绝执行。"
        )
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds) as response:  # nosec B310 - host validated above
        body = response.read().decode(
            response.headers.get_content_charset() or "utf-8", errors="replace"
        )
        resolved_url = response.geturl()
    return Alibaba1688Adapter().parse_product(body, source_url, resolved_url)


def _scroll_1688_page_for_lazy_images(page: Page, timeout_ms: int) -> None:
    """Walk the page so viewport-triggered gallery/detail images are loaded."""

    settle_budget_ms = min(12_000, max(2_000, timeout_ms // 3))
    interval_ms = 250
    max_steps = max(8, min(48, settle_budget_ms // interval_ms))
    previous_signature: tuple[int, int, int] | None = None
    stable_bottoms = 0
    for _ in range(max_steps):
        metrics = page.evaluate(
            """
            () => {
              const root = document.documentElement;
              const body = document.body;
              const height = Math.max(
                root?.scrollHeight || 0, body?.scrollHeight || 0,
                root?.offsetHeight || 0, body?.offsetHeight || 0,
              );
              const viewport = window.innerHeight || 800;
              const maxY = Math.max(0, height - viewport);
              const step = Math.max(600, viewport * 0.8);
              const y = Math.min(maxY, window.scrollY + step);
              window.scrollTo(0, y);

              // 1688 detail panels are sometimes independently scrollable. A
              // window-only walk leaves their lazy images untouched, so visit
              // each genuinely scrollable element once per pass as well.
              let scrollableCount = 0;
              for (const element of Array.from(document.querySelectorAll('*'))) {
                const style = window.getComputedStyle(element);
                const canScroll =
                  element.scrollHeight > element.clientHeight + 24 &&
                  ['auto', 'scroll', 'overlay'].includes(style.overflowY);
                if (!canScroll) continue;
                scrollableCount += 1;
                element.scrollTop = element.scrollHeight;
              }
              return {
                y,
                height,
                viewport,
                imageCount: document.images.length,
                scrollableCount,
              };
            }
            """
        )
        page.wait_for_timeout(interval_ms)
        if not isinstance(metrics, dict):
            break
        y = int(metrics.get("y", 0))
        height = int(metrics.get("height", 0))
        viewport = int(metrics.get("viewport", 800))
        image_count = int(metrics.get("imageCount", 0))
        if y >= max(0, height - viewport) - 4:
            signature = (y, height, image_count)
            stable_bottoms = (
                stable_bottoms + 1 if signature == previous_signature else 0
            )
            previous_signature = signature
            if stable_bottoms >= 2:
                break
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(min(500, interval_ms))


def _activate_1688_detail_section(page: Page) -> bool:
    """Activate a visible product-detail tab without touching risk controls."""

    result = page.evaluate(
        """
        () => {
          const nodes = Array.from(document.querySelectorAll(
            'button, [role="tab"], a'
          ));
          const target = nodes.find((node) => {
            const text = (node.textContent || '').replace(/\\s+/g, '').trim();
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 &&
              /(?:图文详情|商品详情|详情描述|产品详情|详情)/.test(text);
          });
          if (!target) return false;
          target.click();
          return true;
        }
        """
    )
    return result is True


def _shadow_dom_1688_content(page: Page) -> str:
    """Serialize only public image attributes from open Shadow DOM trees.

    ``page.content()`` intentionally omits open Shadow DOM contents. 1688's
    current long-description component places its real assets under
    ``v-detail-1.html-description``. The browser evaluates a read-only,
    narrowly scoped extractor and returns synthetic image fragments in memory;
    it does not read or persist cookies, storage, headers, or page scripts.
    """

    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return ""
    script = r"""
    () => {
      const imageAttrs = [
        "src", "data-src", "data-lazyload", "data-original",
        "data-original-src", "data-preview-src", "data-preview",
        "data-preview-url", "data-img-src", "data-imgurl", "data-img-url",
        "data-image", "data-image-url", "data-zoom-image", "data-zoom-src",
        "data-thumb", "data-thumb-src", "data-ks-lazyload", "data-lazy-src",
        "data-url",
        "srcset", "data-srcset"
      ];
      const backgroundAttrs = [
        "style", "data-background", "data-bg", "data-image-url"
      ];
      const escapeAttr = (value) => String(value)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      const describe = (node) => {
        const parts = [];
        let current = node;
        let hops = 0;
        while (current && hops++ < 24) {
          if (current.nodeType !== 1) break;
          for (const name of ["class", "id", "alt", "aria-label", "role"]) {
            const value = current.getAttribute(name);
            if (value) parts.push(value);
          }
          current = current.parentElement;
        }
        return parts.join(" ");
      };
      const srcsetValues = (value) => String(value || "")
        .split(",")
        .map((item) => item.trim().split(/\s+/, 1)[0])
        .filter(Boolean);
      const backgroundValues = (value) => {
        const text = String(value || "");
        const matches = [];
        const re = /url\(\s*["']?([^"'\s)]+)["']?\s*\)/gi;
        let match;
        while ((match = re.exec(text))) matches.push(match[1]);
        if (!matches.length && /^https?:\/\//i.test(text.trim())) {
          matches.push(text.trim());
        }
        return matches;
      };
      const output = [];
      const seen = new Set();
      const walk = (root, inheritedContext) => {
        for (const element of Array.from(root.querySelectorAll("*"))) {
          const tagName = element.tagName.toLowerCase();
          const context = [inheritedContext, describe(element)]
            .filter(Boolean).join(" ");
          const values = [];
          if (tagName === "img" || tagName === "source") {
            for (const name of imageAttrs) {
              const value = element.getAttribute(name);
              if (!value) continue;
              if (name === "srcset" || name === "data-srcset") {
                values.push(...srcsetValues(value));
              } else {
                values.push(value);
              }
            }
          }
          for (const name of backgroundAttrs) {
            const value = element.getAttribute(name);
            if (value) values.push(...backgroundValues(value));
          }
          for (const value of values) {
            const normalized = String(value).trim();
            if (!normalized || seen.has(normalized)) continue;
            seen.add(normalized);
            output.push(
              `<div class="shadow-rendered ${escapeAttr(context)}">` +
              `<img src="${escapeAttr(normalized)}" alt="shadow-rendered">` +
              `</div>`
            );
          }
          if (element.shadowRoot) walk(element.shadowRoot, context);
        }
      };
      walk(document, "");
      return output.join("\n");
    }
    """
    with suppress(Exception):
        result = evaluate(script)
        if isinstance(result, str):
            return result
    return ""


def _rendered_1688_content(page: Page) -> str:
    """Return main-document, Shadow DOM, and safe same-site iframe fragments.

    Some offers render the long description in an iframe or an open Shadow DOM.
    ``page.content()`` serializes neither, so those assets otherwise never reach
    the adapter. All supplemental markup is kept in memory and wrapped as a
    detail region; no cookies, storage, headers, or URLs with session parameters
    are persisted.
    """

    parts = [page.content()]
    shadow_content = _shadow_dom_1688_content(page)
    if shadow_content.strip():
        parts.append(f'<div class="1688-shadow-content">{shadow_content}</div>')
    main_frame = getattr(page, "main_frame", None)
    for frame in getattr(page, "frames", ()):  # pragma: no branch - browser API
        if frame is main_frame:
            continue
        frame_url = str(getattr(frame, "url", "") or "")
        hostname = (urlparse(frame_url).hostname or "").casefold()
        if frame_url and not (
            hostname.endswith("1688.com")
            or hostname.endswith("alibaba.com")
            or hostname.endswith("alicdn.com")
        ):
            continue
        with suppress(Exception):
            content = frame.content()
            if not isinstance(content, str) or not content.strip():
                continue
            # Avoid letting an iframe's title replace the product title while
            # retaining its images and embedded public JSON.
            content = re.sub(
                r"<title\b[^>]*>.*?</title>",
                "",
                content,
                flags=re.I | re.S,
            )
            parts.append(f'<div class="1688-detail-iframe detail 详情">{content}</div>')
    return "\n".join(parts)


def _crawl_rendered_1688_page(
    page: Page,
    source_url: str,
    timeout_ms: int,
    playwright_error: type[Exception],
    playwright_timeout_error: type[Exception],
) -> Alibaba1688Product:
    """Navigate and parse one already-created browser page."""

    page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(1_000)
    with suppress(playwright_timeout_error):
        page.wait_for_load_state("load", timeout=min(timeout_ms, 5_000))
    # Do not interact with an interstitial. The entry guard is also checked
    # before any detail-tab click so a risk page cannot be mistaken for a
    # product shell.
    initial_content = page.content()
    initial_entry = Alibaba1688Adapter().inspect_page(initial_content, page.url)
    if initial_entry.status not in {"risk_controlled", "login_required"}:
        with suppress(playwright_error):
            _activate_1688_detail_section(page)
            page.wait_for_timeout(1_500)
            _scroll_1688_page_for_lazy_images(page, timeout_ms)
    content = _rendered_1688_content(page)
    return Alibaba1688Adapter().parse_product(content, source_url, page.url)


def crawl_one_1688_browser(
    url: str,
    timeout_ms: int = 30_000,
    profile_dir: str | None = None,
) -> Alibaba1688Product:
    """Render a 1688 page in its independent direct browser profile."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise RuntimeError("BROWSER_RENDERER_UNAVAILABLE") from exc
    source_url = url.strip()
    if classify_site(source_url) is not SiteFamily.ALIBABA_1688:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "1688 浏览器抓取只接受 1688 URL，已拒绝执行。"
        )
    with sync_playwright() as playwright:
        # When an authorized profile is available, use a real installed Chrome
        # process and attach over localhost CDP.  ``launch_persistent_context``
        # starts a Playwright-owned browser, which is materially different from
        # the Chrome window in which a user can pass the official slider.  CDP
        # here is only a transport: the challenge remains fully manual and no
        # stealth flags, credential extraction, or challenge replay are used.
        if profile_dir:
            with connect_to_owned_chrome(
                playwright, profile_dir, network_mode="direct"
            ) as (_browser, context):
                page = context.new_page()
                return _crawl_rendered_1688_page(
                    page,
                    source_url,
                    timeout_ms,
                    PlaywrightError,
                    PlaywrightTimeoutError,
                )

        browser = playwright.chromium.launch(
            headless=True, args=["--no-proxy-server"]
        )
        context = browser.new_context(locale="zh-CN", user_agent="Mozilla/5.0")
        page = context.new_page()
        try:
            return _crawl_rendered_1688_page(
                page, source_url, timeout_ms, PlaywrightError, PlaywrightTimeoutError
            )
        finally:
            context.close()
            browser.close()




