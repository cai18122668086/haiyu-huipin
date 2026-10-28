"""拼多多公开商品页适配器。

只读取公开商品页已经返回的 HTML、公开结构化页面状态和浏览器渲染后的 DOM。
不会调用私有接口、破解签名、提交登录信息或绕过验证码/风控。
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener

if TYPE_CHECKING:
    from playwright.sync_api import Page

from .ecommerce_chrome import connect_to_owned_chrome
from .ecommerce_platform import PlatformPolicyError, SiteFamily, classify_site

logger = logging.getLogger(__name__)

ImageRole = Literal["main", "detail", "sku"]
PageEntryStatus = Literal[
    "product", "login_required", "risk_controlled", "product_not_found", "empty"
]

PINDUODUO_HOSTS = (
    "pinduoduo.com",
    "yangkeduo.com",
    "pddpic.com",
    "pinduoduoimg.com",
)
_PAGE_HOSTS = ("pinduoduo.com", "yangkeduo.com")
_IMAGE_HOSTS = ("pddpic.com", "pinduoduoimg.com", "yangkeduo.com", "pinduoduo.com")
_LOGIN_MARKERS = ("请登录", "登录后查看", "扫码登录", "登录拼多多", "请先登录")
_RISK_MARKERS = (
    "验证码",
    "滑块验证",
    "访问受限",
    "请求异常",
    "安全验证",
    "风控",
    "机器人验证",
    "forbidden",
)
_NOT_FOUND_MARKERS = (
    "商品不存在",
    "页面不存在",
    "商品已下架",
    "抱歉，商品已下架",
    "商品已售罄",
    "goodsidexception",
)
_UI_TOKENS = (
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
    "coupon",
    "tag",
    "avatar",
    "portrait",
    "head",
    "comment",
    "review",
    "晒单",
    "买家秀",
    "recommend",
    "猜你喜欢",
    "广告",
    "分享",
    "收藏",
    "按钮",
    # These asset families are used by the mobile page for payment prompts,
    # recommendation badges, avatars and other chrome.  They are not product
    # media even when they happen to be rendered inside a detail section.
    "promotion",
    "funimg",
    "ddpay",
    "super-star",
    "oms_img_promotion",
    "chat",
    "base/share_logo",
)
_MAIN_TOKENS = (
    # The current mobile page uses a generic ``main container`` wrapper
    # instead of a stable gallery class. Keep ``main`` as a standalone
    # semantic token; the dimension fallback below prevents long detail
    # images inside that wrapper from becoming main images.
    "main",
    "main container",
    "gallery",
    "goods-gallery",
    "swiper",
    "banner",
    "主图",
    "轮播",
    "goods_image",
    "goodsimage",
    "thumb_url",
    "hd_url",
    "image_url",
    "imageurl",
    "goods_model",
)
_DETAIL_TOKENS = (
    "detail",
    "description",
    "desc",
    "图文详情",
    "商品详情",
    "rich_text",
    "long_banner",
)
_SKU_TOKENS = (
    "sku",
    "spec",
    "property",
    "color",
    "size",
    "规格",
    "颜色",
    "尺码",
    "属性",
)

_LOGIN_PAGE_PATHS = {"/login", "/login.html", "/passport/login"}


@dataclass(frozen=True)
class PinduoduoPageEntry:
    status: PageEntryStatus
    resolved_url: str
    title: str | None = None
    matched_markers: tuple[str, ...] = ()
    product_id: str | None = None


@dataclass(frozen=True)
class PinduoduoImage:
    url: str
    role: ImageRole = "main"
    ordinal: int = 0
    source: str = "dom"
    context: str = ""


@dataclass(frozen=True)
class _DomImageNode:
    """An image plus the nearest DOM context needed for role inference.

    Keeping the nearest contexts separate is important on the mobile page:
    product detail markup often contains nested promotion/payment widgets. A
    single flattened ancestor string makes every descendant look like a
    detail image and was the source of the icon-heavy result reported by the
    user.
    """

    attrs: dict[str, str]
    own_context: str
    ancestor_contexts: tuple[str, ...] = ()
    dom_index: int | None = None
    natural_width: int | None = None
    natural_height: int | None = None
    is_background: bool = False


@dataclass(frozen=True)
class PinduoduoSkuGroup:
    name: str
    values: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PinduoduoSku:
    sku_id: str
    properties: tuple[dict[str, str], ...] = field(default_factory=tuple)
    price: str | None = None
    stock: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class PinduoduoProduct:
    status: PageEntryStatus
    source_url: str
    resolved_url: str
    product_id: str | None = None
    title: str | None = None
    price: str | None = None
    shop_name: str | None = None
    sales: str | None = None
    props_text: str = ""
    properties: tuple[dict[str, str], ...] = field(default_factory=tuple)
    services: tuple[str, ...] = field(default_factory=tuple)
    images: tuple[PinduoduoImage, ...] = field(default_factory=tuple)
    sku_prop_groups: tuple[PinduoduoSkuGroup, ...] = field(default_factory=tuple)
    skus: tuple[PinduoduoSku, ...] = field(default_factory=tuple)
    image_diagnostics: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def platform(self) -> str:
        return "pinduoduo"


class _PageParser(HTMLParser):
    _VOID_TAGS = {
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
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.json_scripts: list[str] = []
        self.image_nodes: list[_DomImageNode] = []
        self._in_title = False
        self._in_script = False
        self._script_type = ""
        self._script_parts: list[str] = []
        self._contexts: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        values = {key.casefold(): value or "" for key, value in attrs}
        context_parts = [
            values.get(key, "")
            for key in (
                "id",
                "class",
                "alt",
                "aria-label",
                "data-testid",
                "data-role",
                "data-component",
                "data-name",
                "data-pdd-context",
            )
        ]
        # Keep semantic data-* attributes, but never copy URL-bearing
        # attributes such as data-src into a diagnostic context.
        context_parts.extend(
            value
            for key, value in values.items()
            if key.startswith("data-")
            and key
            not in {
                "data-src",
                "data-lazy-src",
                "data-original",
                "data-image",
                "data-url",
                "data-thumb",
                "data-hd-url",
                "data-pdd-context",
            }
        )
        own_context = " ".join(part for part in context_parts if part).strip()
        ancestor_contexts = tuple(
            context for _tag, context in reversed(self._contexts) if context
        )
        if name == "title":
            self._in_title = True
        if name == "meta":
            key = values.get("property") or values.get("name")
            if key and values.get("content"):
                self.meta[key.casefold()] = values["content"]
        if name == "script":
            self._in_script = True
            self._script_type = values.get("type", "").casefold()
            self._script_parts = []
        if name == "img":
            self.image_nodes.append(
                _DomImageNode(
                    values,
                    own_context,
                    ancestor_contexts,
                    _positive_int(values.get("data-pdd-dom-index")),
                    _positive_int(values.get("data-pdd-natural-width")),
                    _positive_int(values.get("data-pdd-natural-height")),
                    values.get("data-pdd-background") == "1",
                )
            )
        if name not in self._VOID_TAGS:
            self._contexts.append((name, own_context))

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "title":
            self._in_title = False
        if name == "script":
            value = "".join(self._script_parts).strip()
            if value and (
                self._script_type in {"application/json", "application/ld+json"}
                or value[:1] in "[{"
                or re.match(
                    r"^\s*window(?:\.[A-Za-z_$][\w$]*)+\s*=", value
                )
            ):
                self.json_scripts.append(value)
            self._in_script = False
            self._script_parts = []
        if name not in self._VOID_TAGS:
            for index in range(len(self._contexts) - 1, -1, -1):
                if self._contexts[index][0] == name:
                    del self._contexts[index:]
                    break

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if self._in_title:
            self.title_parts.append(data)
        if self._in_script:
            self._script_parts.append(data)
        elif text:
            self.visible_parts.append(text)


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", unescape(str(value))).strip()
    return text or None


def _parse_page(html: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    return parser


def _json_documents(parser: _PageParser) -> list[Any]:
    result: list[Any] = []
    for raw in parser.json_scripts:
        candidates = [raw]
        # Current mobile PDD pages expose SSR data as window.rawData={...}.
        # Accept a plain window assignment and decode only its JSON payload.
        if re.match(r"^\s*window(?:\.[A-Za-z_$][\w$]*)+\s*=", raw):
            candidates.append(raw.split("=", 1)[1].strip().rstrip(";"))
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            result.append(value)
            break
    return result


def _walk(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk(child))
    return found


def _key_normalise(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


_IMAGE_LEAF_KEYS = {
    _key_normalise(key)
    for key in (
        "url",
        "src",
        "image_url",
        "imageUrl",
        "hd_url",
        "hdUrl",
        "thumb_url",
        "thumbUrl",
        "thumb",
        "image",
        "img",
        "img_url",
        "imgUrl",
        "specValueImgUrl",
        "spec_value_img_url",
        "skuImageUrl",
        "sku_image_url",
        "skuThumbUrl",
        "sku_thumb_url",
        "data_url",
        "dataUrl",
    )
}
_MAIN_CONTAINER_KEYS = {
    _key_normalise(key)
    for key in (
        "topGallery",
        "goodsGallery",
        "goods_gallery",
        "goodsImages",
        "goodsImage",
        "goodsImageList",
        # Some mobile SSR snapshots expose the carousel through this field
        # instead of a complete topGallery array.
        "viewImageData",
        "view_image_data",
    )
}
_DETAIL_CONTAINER_KEYS = {
    _key_normalise(key)
    for key in (
        "goodsDetail",
        "goods_detail",
        "detailGallery",
        "detail_gallery",
        "detailImages",
        "detail_images",
        "descImages",
        "desc_images",
        "descriptionImages",
        "richTextImages",
        "rich_text_images",
    )
}
_SKU_CONTAINER_KEYS = {
    _key_normalise(key)
    for key in (
        "skuImages",
        "sku_images",
        "skuProperty",
        "sku_property",
        "skuImage",
        "sku_image",
        "skuList",
        "sku_list",
        "skus",
    )
}


def _first_value(documents: list[Any], keys: tuple[str, ...]) -> Any:
    wanted = {_key_normalise(key) for key in keys}
    for document in documents:
        for mapping in _walk(document):
            for key, value in mapping.items():
                if _key_normalise(str(key)) in wanted and value not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    return value
    return None


def _string_value(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        return _clean_text(value)
    return None


def _product_id(source_url: str, html: str, documents: list[Any]) -> str | None:
    parsed = urlparse(source_url)
    query = parse_qs(parsed.query)
    for key in ("goods_id", "goodsId", "goodsid", "id"):
        values = query.get(key)
        if values and re.fullmatch(r"\d{5,}", values[0]):
            return values[0]
    for pattern in (
        r"(?:goods[_-]?id|goodsId)\\?['\"]?\s*[:=]\\?\s*['\"]?(\d{5,})",
        r"/goods(?:\.html)?/([0-9]{5,})",
    ):
        match = re.search(pattern, html, flags=re.I)
        if match:
            return match.group(1)
    value = _first_value(
        documents, ("goods_id", "goodsId", "goodsid", "product_id", "productId")
    )
    if isinstance(value, (str, int)) and re.fullmatch(r"\d{5,}", str(value)):
        return str(value)
    numbers = re.findall(r"(?<!\d)\d{8,}(?!\d)", parsed.path)
    return numbers[0] if numbers else None


def _requires_login(documents: list[Any]) -> bool:
    """Return the explicit login flag emitted by the mobile PDD SSR state."""

    for mapping in _walk(documents):
        for key, value in mapping.items():
            if _key_normalise(str(key)) in {
                "needlogin",
                "loginrequired",
                "requireslogin",
            } and value is True:
                return True
    return False


def _price(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in (
            "price_info",
            "priceInfo",
            "displayPrice",
            "formattedPrice",
            "price",
        ):
            if key in value:
                result = _price(value[key])
                if result:
                    return result
        return None
    if isinstance(value, (int, float)):
        # PDD page state often carries cents as an integer. Prefer explicit
        # display/price_info fields before this fallback. Zero is a placeholder
        # found in some nested pricing records, not a usable display price.
        amount = float(value) / 100 if value >= 1000 else float(value)
        if amount <= 0:
            return None
        return f"{amount:.2f}".rstrip("0").rstrip(".")
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d{1,2})?", text.replace(",", ""))
    if not match or float(match.group(0)) <= 0:
        return None
    return match.group(0)


def _url(raw: Any, base_url: str) -> str | None:
    if not isinstance(raw, str):
        return None
    value = unescape(raw.strip()).strip("\"'")
    if value.startswith("//"):
        value = "https:" + value
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


_RESOURCE_UI_MARKERS = (
    "/funimg/",
    "/promotion/",
    "/commimg/",
    "/ddpay/",
    "/avatar/",
    "/avatars/",
    "/headimg/",
    "/recommend/",
    "/logo",
    "/icon",
    "/sprite",
    "promotion.",
    "funimg.",
    "commimg.",
)


def _contains_context_token(context: str, token: str) -> bool:
    """Match a semantic token without treating every substring as a marker."""

    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", context.casefold())
    wanted = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", token.casefold()).strip()
    if not wanted:
        return False
    if wanted in {
        "detail",
        "desc",
        "sku",
        "spec",
        "gallery",
        "swiper",
        "main",
    }:
        return bool(re.search(rf"(?:^|\s){re.escape(wanted)}(?:$|\s)", normalized))
    return wanted in normalized


def _image_identity(value: str) -> str:
    """Return a stable identity for CDN host/URL variants of one asset.

    PDD serves the same object from hosts such as ``img.pddpic.com`` and
    ``img-1.pddpic.com``.  Those host changes are delivery details, not a
    second product image, so deduplication must happen after normalising them.
    Keep the public URL untouched; this key is only for internal identity.
    """

    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    host = re.sub(r"^img-\d+\.", "img.", host)
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return f"{parsed.scheme.casefold()}://{host}{path}".casefold()


def _has_strong_main_context(contexts: tuple[str, ...]) -> bool:
    """Whether a DOM node is inside a dedicated gallery, not just the page shell."""

    strong_tokens = ("gallery", "swiper", "轮播", "商品大图", "goodsimage")
    return any(
        any(_contains_context_token(context, token) for token in strong_tokens)
        for context in contexts
    )


def _image_allowed(value: str, context: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if not any(
        host == domain or host.endswith(f".{domain}") for domain in _IMAGE_HOSTS
    ):
        return False
    path = parsed.path.casefold()
    if path.endswith((".html", ".htm")) or not path or path == "/":
        return False
    if any(marker in path or marker in host for marker in _RESOURCE_UI_MARKERS):
        return False
    # URL families are more reliable than a growing blacklist of CSS names.
    # Keep the legacy markers only for explicit UI contexts/asset names.
    for token in _UI_TOKENS:
        lowered = token.casefold()
        if lowered in path or _contains_context_token(context, lowered):
            return False
    return True


def _role(
    context: str, inherited: ImageRole | None = None
) -> ImageRole | None:
    lowered = context.casefold()
    if any(_contains_context_token(lowered, token) for token in _SKU_TOKENS):
        return "sku"
    if any(_contains_context_token(lowered, token) for token in _DETAIL_TOKENS):
        return "detail"
    if any(_contains_context_token(lowered, token) for token in _MAIN_TOKENS):
        return "main"
    return inherited


def _dimension_role(node: _DomImageNode, image_url: str) -> ImageRole | None:
    """Infer product-media role from rendered dimensions when classes are opaque.

    The current mobile PDD page uses generated class names. Its loaded product
    assets still expose a useful shape split: square 800-ish images are the
    carousel, while 750-wide non-square assets are long detail panels. Apply
    this fallback only to the product-image family so UI thumbnails cannot be
    promoted merely because they happen to have dimensions.
    """

    path = urlparse(image_url).path.casefold()
    if "/mms-goods-image/" not in path:
        return None
    width = node.natural_width
    height = node.natural_height
    if not width or not height or width < 500 or height < 100:
        return None
    ratio = height / width
    if 0.88 <= ratio <= 1.12:
        return "main"
    # Any sufficiently large non-square product asset is more likely to be a
    # detail panel than a carousel slide. This includes landscape detail
    # banners such as 750x447 as well as tall 750x1048/1217 panels.
    return "detail"


def _json_image_values(
    documents: list[Any], base_url: str
) -> list[tuple[str, ImageRole, str, str]]:
    """Read images only from known product-media containers.

    The mobile page contains a large amount of JSON for recommendations,
    coupons, avatars and payment UI.  Treating every ``url``/``src`` value as
    a main image is what produced the 60+ icon galleries reported by the
    user.  We therefore recurse through JSON only to locate known gallery,
    detail and SKU containers; URLs are extracted from those containers, not
    from arbitrary dictionaries or lists.
    """

    values: list[tuple[str, ImageRole, str, str]] = []

    def append(raw: Any, role: ImageRole, source: str, context: str) -> None:
        candidate = _url(raw, base_url)
        if candidate and _image_allowed(candidate, context):
            values.append((candidate, role, source, context))

    def collect_container(value: Any, role: ImageRole, path: str) -> None:
        if isinstance(value, str):
            append(value, role, f"json:{path}", path)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                collect_container(child, role, f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _key_normalise(key_text) in _IMAGE_LEAF_KEYS:
                if isinstance(child, list):
                    for index, item in enumerate(child):
                        append(item, role, f"json:{child_path}[{index}]", child_path)
                else:
                    append(child, role, f"json:{child_path}", child_path)
            elif isinstance(child, (dict, list)):
                collect_container(child, role, child_path)

    def visit(value: Any, path: str = "json") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}"
                normalised = _key_normalise(key_text)
                if normalised in _MAIN_CONTAINER_KEYS:
                    collect_container(child, "main", child_path)
                elif normalised in _DETAIL_CONTAINER_KEYS:
                    collect_container(child, "detail", child_path)
                elif normalised in _SKU_CONTAINER_KEYS:
                    collect_container(child, "sku", child_path)
                # Continue looking for a nested goods object/gallery, but do
                # not interpret its ordinary URL fields as media.
                if isinstance(child, (dict, list)):
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, (dict, list)):
                    visit(child, f"{path}[{index}]")

    for document in documents:
        visit(document)
    return values


def _dom_images(
    parser: _PageParser, base_url: str
) -> list[tuple[str, ImageRole, str, str]]:
    values: list[tuple[str, ImageRole, str, str]] = []
    attrs = (
        "src",
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-image",
        "data-url",
        "data-thumb",
        "data-hd-url",
    )
    for node in parser.image_nodes:
        explicit_role = node.attrs.get("data-pdd-role", "").casefold()
        role: ImageRole | None = (
            cast(ImageRole, explicit_role)
            if explicit_role in {"main", "detail", "sku"}
            else None
        )
        # Use the closest semantic container first. Do not let a distant
        # ``detail`` ancestor override a nearer ``sku``/``promotion`` node.
        context_candidates = (node.own_context, *node.ancestor_contexts)
        semantic_role = role
        if semantic_role is None:
            for context_part in context_candidates:
                semantic_role = _role(context_part)
                if semantic_role is not None:
                    break
        context = " | ".join(part for part in context_candidates if part)
        for key in attrs:
            candidate = _url(node.attrs.get(key), base_url)
            if not candidate or not _image_allowed(candidate, context):
                continue
            # Generated mobile-page classes often expose only ``main
            # container`` for the whole product area. Reclassify non-square
            # product assets as detail panels, while preserving an explicit
            # SKU role and explicit detail role.
            dimension_role = _dimension_role(node, candidate)
            # The shell also contains lazy detail placeholders. They inherit
            # ``main container`` but have no loaded dimensions and are not
            # gallery slides. Do not promote those placeholders to main media;
            # a dedicated gallery context or an explicit role is required when
            # dimensions are unavailable.
            if (
                semantic_role == "main"
                and explicit_role != "main"
                and dimension_role is None
                and not _has_strong_main_context(context_candidates)
            ):
                continue
            if semantic_role == "sku":
                role = "sku"
            elif semantic_role == "detail":
                role = "detail"
            elif dimension_role is not None:
                role = dimension_role
            else:
                role = semantic_role
            # An unclassified DOM image is usually an icon, recommendation
            # card, avatar or navigation asset. Never default it to the main
            # gallery without both a product-media URL and dimensions.
            if role is None:
                continue
            values.append((candidate, role, f"dom:{key}", context))
    return values


def _images(
    parser: _PageParser,
    documents: list[Any],
    base_url: str,
    diagnostics: dict[str, int],
) -> tuple[PinduoduoImage, ...]:
    # Structured page state is emitted before the DOM so its array order is
    # authoritative.  DOM media remains a compatibility fallback for pages
    # whose gallery is rendered without SSR JSON, but only in an explicitly
    # classified product-media container.
    dom_candidates = _dom_images(parser, base_url)
    candidates = _json_image_values(documents, base_url) + dom_candidates

    # The mobile SSR payload is not consistently partitioned. On the current
    # goods2 page the first carousel image is in ``topGallery`` but the
    # remaining carousel images can appear at the head of ``detailGallery``.
    # A rendered DOM node with a real square natural size is stronger evidence
    # for ``main`` than that container name. Likewise, a loaded 750-wide
    # non-square node is strong evidence for ``detail``. Keep this map separate
    # from output candidates so it corrects structured roles without allowing
    # arbitrary DOM URLs into the result.
    dom_dimension_roles: dict[str, ImageRole] = {}
    dom_small_identities: set[str] = set()
    for node in parser.image_nodes:
        for attr_name in (
            "src",
            "data-src",
            "data-lazy-src",
            "data-original",
            "data-image",
            "data-url",
            "data-thumb",
            "data-hd-url",
        ):
            raw = _url(node.attrs.get(attr_name), base_url)
            if not raw or not _image_allowed(raw, node.own_context):
                continue
            identity = _image_identity(raw)
            if node.natural_width and node.natural_height:
                if node.natural_width < 500 or node.natural_height < 100:
                    dom_small_identities.add(identity)
                else:
                    dimension_role = _dimension_role(node, raw)
                    if dimension_role and (
                        dimension_role == "main"
                        or identity not in dom_dimension_roles
                    ):
                        dom_dimension_roles[identity] = dimension_role

    # A product image may appear once as a large carousel asset and again as a
    # small thumbnail. Large evidence wins; do not reject the structured
    # candidate merely because a thumbnail was also rendered.
    dom_small_identities.difference_update(dom_dimension_roles)

    # Deduplicate within a role, not across roles. A variant thumbnail can
    # legitimately be the same URL as a carousel image, and dropping it from
    # the SKU collection is how one of the user's SKU images went missing.
    # Detail-vs-main suppression remains explicit below.
    selected: dict[tuple[ImageRole, str], tuple[ImageRole, str, str, str, int]] = {}
    for raw, role, source, context in candidates:
        diagnostics["image_values_seen"] = diagnostics.get("image_values_seen", 0) + 1
        key = _image_identity(raw)
        dom_role = dom_dimension_roles.get(key)
        if role != "sku" and key in dom_small_identities:
            diagnostics["rejected_dom_small_media"] = (
                diagnostics.get("rejected_dom_small_media", 0) + 1
            )
            continue
        if role != "sku" and dom_role in {"main", "detail"}:
            role = dom_role
        selected_key = (role, key)
        existing = selected.get(selected_key)
        if existing:
            diagnostics["dedup_identity"] = diagnostics.get("dedup_identity", 0) + 1
            # Structured product containers are authoritative over DOM
            # fallbacks, except for the dimension-based role correction above.
            if existing[1].startswith("dom:") and source.startswith("json:"):
                selected[selected_key] = (role, source, raw, context, existing[4])
            continue
        selected[selected_key] = (role, source, raw, context, len(selected))
        diagnostics["accepted_candidates"] = (
            diagnostics.get("accepted_candidates", 0) + 1
        )
    grouped: dict[ImageRole, list[tuple[str, str, str, int]]] = {
        "main": [],
        "detail": [],
        "sku": [],
    }
    for role, source, raw, context, ordinal in selected.values():
        grouped[role].append((raw, source, context, ordinal))
    main_urls = {_image_identity(raw) for raw, *_ in grouped["main"]}
    # A detail asset duplicated in the generic product shell should not be
    # emitted twice. Do not apply this rule to SKU media: SKU thumbnails are a
    # separate semantic collection even when a seller reuses a main-image URL.
    for role in ("detail",):
        before = len(grouped[role])
        grouped[role] = [
            item for item in grouped[role] if _image_identity(item[0]) not in main_urls
        ]
        diagnostics[f"rejected_{role}_duplicate_main"] = before - len(grouped[role])
    result: list[PinduoduoImage] = []
    output_roles: tuple[ImageRole, ImageRole, ImageRole] = (
        "main",
        "detail",
        "sku",
    )
    for role in output_roles:
        for ordinal, (raw, source, context, _) in enumerate(grouped[role]):
            result.append(PinduoduoImage(raw, role, ordinal, source, context))
    return tuple(result)


def _properties(value: Any) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            text = _string_value(child)
            if text:
                result.append({"name": str(key), "value": text})
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, dict):
                name = _string_value(
                    child.get("name") or child.get("key") or child.get("prop_name")
                )
                val = _string_value(
                    child.get("value") or child.get("val") or child.get("prop_value")
                )
                if name and val:
                    result.append({"name": name, "value": val})
    return tuple(result)


def _sku_data(
    documents: list[Any], base_url: str
) -> tuple[tuple[PinduoduoSkuGroup, ...], tuple[PinduoduoSku, ...]]:
    groups: list[PinduoduoSkuGroup] = []
    raw_group_value = _first_value(
        documents,
        (
            "spec_list",
            "specList",
            "sku_prop_groups",
            "skuPropGroups",
            "skuProperty",
            "sku_property",
            "specs",
        ),
    )
    raw_groups = (
        raw_group_value
        if isinstance(raw_group_value, list)
        else [raw_group_value]
        if raw_group_value is not None
        else []
    )
    for item in raw_groups:
        if not isinstance(item, dict):
            continue
        name = _string_value(
            item.get("name")
            or item.get("spec_name")
            or item.get("specName")
            or item.get("key")
        )
        raw_values = (
            item.get("values")
            or item.get("spec_values")
            or item.get("specValues")
            or item.get("options")
            or []
        )
        values = (
            tuple(
                filter(
                    None,
                    (
                        _string_value(
                            (
                                value.get("name")
                                or value.get("spec_value")
                                or value.get("specValue")
                                or value.get("value")
                            )
                            if isinstance(value, dict)
                            else value
                        )
                        for value in raw_values
                    ),
                )
            )
            if isinstance(raw_values, list)
            else ()
        )
        if name and values:
            groups.append(PinduoduoSkuGroup(name, values))

    skus: list[PinduoduoSku] = []
    raw_skus = _first_value(
        documents, ("sku_list", "skuList", "skus", "sku_data", "skuData")
    )
    if isinstance(raw_skus, list):
        for item in raw_skus:
            if not isinstance(item, dict):
                continue
            sku_id = _string_value(
                item.get("sku_id") or item.get("skuId") or item.get("id")
            )
            if not sku_id:
                continue
            image = _url(
                item.get("image_url")
                or item.get("imageUrl")
                or item.get("img_url")
                or item.get("imgUrl")
                or item.get("image")
                or item.get("img")
                or item.get("thumb_url")
                or item.get("thumbUrl")
                or item.get("thumb")
                or item.get("sku_image")
                or item.get("skuImage")
                or item.get("sku_image_url")
                or item.get("skuImageUrl")
                or item.get("spec_value_img_url")
                or item.get("specValueImgUrl"),
                base_url,
            )
            if image and not _image_allowed(image, "sku"):
                image = None
            skus.append(
                PinduoduoSku(
                    sku_id,
                    _properties(
                        item.get("properties") or item.get("specs") or item.get("spec")
                    ),
                    _price(item.get("price") or item.get("price_info")),
                    _string_value(item.get("stock") or item.get("stock_tip")),
                    image,
                )
            )
    return tuple(groups), tuple(skus)


class PinduoduoAdapter:
    adapter_name = "pinduoduo"
    network_mode = "direct"
    session_profile = "domestic-pinduoduo"

    def inspect_page(self, html: str, resolved_url: str) -> PinduoduoPageEntry:
        if classify_site(resolved_url) is not SiteFamily.PINDUODUO:
            raise PlatformPolicyError(
                "NETWORK_POLICY_VIOLATION", "拼多多适配器只接受拼多多 URL。"
            )
        parser = _parse_page(html)
        page_title = _clean_text(" ".join(parser.title_parts)) or _clean_text(
            parser.meta.get("og:title")
        )
        text = _clean_text(" ".join(parser.visible_parts)) or ""
        lowered = f"{resolved_url} {page_title or ''} {text}".casefold()
        documents = _json_documents(parser)
        product_id = _product_id(resolved_url, html, documents)
        parsed_url = urlparse(resolved_url)
        explicit_login_page = (
            parsed_url.path.rstrip("/").casefold() in _LOGIN_PAGE_PATHS
            or (page_title or "").casefold() in {"登录", "拼多多登录", "login"}
            or _requires_login(documents)
        )
        if explicit_login_page:
            return PinduoduoPageEntry(
                "login_required",
                resolved_url,
                page_title,
                ("needLogin",) if _requires_login(documents) else ("登录页",),
                product_id,
            )
        matched_risk = tuple(
            marker for marker in _RISK_MARKERS if marker.casefold() in lowered
        )
        if matched_risk:
            return PinduoduoPageEntry(
                "risk_controlled", resolved_url, page_title, matched_risk, product_id
            )
        structured_product_value = _first_value(
            documents,
            (
                "goods_name",
                "goodsName",
                "price_info",
                "priceInfo",
                "goods_model",
                "goods_gallery",
            ),
        )
        usable_page_title = (
            page_title
            if page_title and page_title.casefold() not in {"拼多多", "pinduoduo"}
            else None
        )
        # A rendered recommendation list can contain many valid PDD images but
        # is not evidence that the requested goods page loaded. Only accept
        # image nodes that survive the product-image filter, or a specific
        # title/id pair, instead of treating every <img> as product content.
        title_has_not_found_marker = bool(
            usable_page_title
            and any(
                marker.casefold() in usable_page_title.casefold()
                for marker in _NOT_FOUND_MARKERS
            )
        )
        has_product_signal = bool(structured_product_value) or bool(
            _dom_images(parser, resolved_url)
        ) or bool(usable_page_title and product_id and not title_has_not_found_marker)
        # Recommendation cards and hidden/secondary UI can mention that some
        # other item is sold out. Only treat a not-found marker as authoritative
        # when the requested page has no product signal of its own.
        matched_not_found = tuple(
            marker for marker in _NOT_FOUND_MARKERS if marker.casefold() in lowered
        )
        if matched_not_found and not has_product_signal:
            return PinduoduoPageEntry(
                "product_not_found",
                resolved_url,
                page_title,
                matched_not_found,
                product_id,
            )
        matched_login = tuple(
            marker for marker in _LOGIN_MARKERS if marker.casefold() in lowered
        )
        if matched_login and not has_product_signal:
            return PinduoduoPageEntry(
                "login_required",
                resolved_url,
                usable_page_title,
                matched_login,
                product_id,
            )
        if not has_product_signal:
            return PinduoduoPageEntry(
                "empty", resolved_url, usable_page_title, (), product_id
            )
        return PinduoduoPageEntry(
            "product", resolved_url, usable_page_title, (), product_id
        )

    def parse_product(
        self, html: str, source_url: str, resolved_url: str | None = None
    ) -> PinduoduoProduct:
        final_url = resolved_url or source_url
        entry = self.inspect_page(html, final_url)
        if entry.status != "product":
            messages = {
                "login_required": "拼多多商品页要求登录。",
                "risk_controlled": "拼多多商品页触发风控。",
                "product_not_found": "拼多多商品不存在、已下架或已售罄。",
                "empty": "未读取到拼多多商品内容。",
            }
            return PinduoduoProduct(
                entry.status,
                source_url,
                final_url,
                product_id=entry.product_id,
                title=entry.title,
                warnings=(messages[entry.status],),
            )
        parser = _parse_page(html)
        documents = _json_documents(parser)
        text = _clean_text(" ".join(parser.visible_parts)) or ""
        title = _string_value(
            _first_value(
                documents, ("goods_name", "goodsName", "title", "name", "subject")
            )
        ) or entry.title
        product_id = entry.product_id or _product_id(source_url, html, documents)
        raw_price = _first_value(
            documents,
            (
                "price_info",
                "priceInfo",
                "display_price",
                "displayPrice",
                "normal_price",
                "normalPrice",
                "price",
            ),
        )
        price_match = re.search(r"(?:¥|￥)\s*(\d+(?:\.\d{1,2})?)", text)
        price = _price(raw_price) or _price(
            price_match.group(1) if price_match else None
        )
        shop_name = _string_value(
            _first_value(
                documents,
                (
                    "mall_name",
                    "mallName",
                    "shop_name",
                    "shopName",
                    "seller_name",
                    "sellerName",
                ),
            )
        ) or _clean_text(parser.meta.get("og:site_name"))
        sales = _string_value(
            _first_value(
                documents, ("sales_tip", "salesTip", "sold_count", "soldCount", "sales")
            )
        )
        properties = _properties(
            _first_value(
                documents, ("property", "properties", "props", "goods_property")
            )
        )
        groups, skus = _sku_data(documents, final_url)
        diagnostics: dict[str, int] = {}
        images = _images(parser, documents, final_url, diagnostics)
        service_markers = (
            "退货包运费",
            "7天无理由退货",
            "7 天无理由退货",
            "免运费",
            "极速退款",
            "48小时发货",
        )
        services = tuple(marker for marker in service_markers if marker in text)
        warnings: list[str] = []
        for label, value in (
            ("商品标题", title),
            ("商品价格", price),
            ("商品 ID", product_id),
        ):
            if not value:
                warnings.append(f"未从拼多多页面读取到{label}。")
        if not images:
            warnings.append("未从拼多多页面读取到商品图片，页面可能需要浏览器渲染。")
        return PinduoduoProduct(
            "product",
            source_url,
            final_url,
            product_id,
            title,
            price,
            shop_name,
            sales,
            "；".join(f"{x['name']}：{x['value']}" for x in properties),
            properties,
            services,
            images,
            groups,
            skus,
            diagnostics,
            tuple(warnings),
        )


def _is_supported_pinduoduo_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme in {"http", "https"} and any(
        host == item or host.endswith(f".{item}") for item in _PAGE_HOSTS
    )


def parse_product_html(
    html: str, source_url: str, resolved_url: str | None = None
) -> PinduoduoProduct:
    if not _is_supported_pinduoduo_url(source_url) and not _is_supported_pinduoduo_url(
        resolved_url or ""
    ):
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "拼多多适配器只接受拼多多商品链接。"
        )
    return PinduoduoAdapter().parse_product(html, source_url, resolved_url)


def crawl_one_pinduoduo(url: str, timeout_ms: int = 30_000) -> PinduoduoProduct:
    if classify_site(url) is not SiteFamily.PINDUODUO:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "拼多多抓取只接受拼多多 URL。"
        )
    opener = build_opener(ProxyHandler({}))
    request = Request(
        url.strip(),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mobile.yangkeduo.com/",
        },
    )
    with opener.open(request, timeout=max(5, timeout_ms / 1000)) as response:  # noqa: S310 - URL policy checked above
        content = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        html = content.decode(charset, errors="replace")
        resolved_url = response.geturl()
    return parse_product_html(html, url.strip(), resolved_url)


def _scroll_page(page: Page, timeout_ms: int) -> None:
    for _ in range(max(8, min(32, timeout_ms // 500))):
        with suppress(Exception):
            metrics = page.evaluate(
                """
                () => {
                    const d = document.documentElement;
                    const b = document.body;
                    const h = Math.max(d?.scrollHeight || 0, b?.scrollHeight || 0);
                    const v = window.innerHeight || 800;
                    const y = Math.min(
                        Math.max(0, h - v),
                        window.scrollY + Math.max(600, v * 0.8),
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


def _rendered_content(page: Page) -> str:
    """Serialize rendered media with the DOM context that produced it.

    ``page.content()`` contains classes/attributes but not runtime dimensions
    or a stable nearest-container hint. Annotating the owned page before
    serialization lets the parser distinguish a real gallery image from a
    payment/promotion image nested under the same detail section.
    """

    with suppress(Exception):
        page.evaluate(
            r"""
            () => {
                const contextFor = (element) => {
                    const parts = [];
                    let current = element;
                    for (let depth = 0; current && depth < 8; depth += 1) {
                        const values = [
                            current.id,
                            current.className,
                            current.getAttribute('alt'),
                            current.getAttribute('aria-label'),
                            current.getAttribute('data-testid'),
                            current.getAttribute('data-role'),
                            current.getAttribute('data-component'),
                            current.getAttribute('data-name'),
                        ].filter((value) => typeof value === 'string' && value.trim());
                        if (values.length) parts.push(values.join(' '));
                        current = current.parentElement;
                    }
                    return parts;
                };

                Array.from(document.querySelectorAll('img')).forEach((image, index) => {
                    const context = contextFor(image);
                    image.setAttribute('data-pdd-dom-index', String(index));
                    image.setAttribute('data-pdd-context', context.join(' | '));
                    if (image.naturalWidth > 0) {
                        image.setAttribute(
                            'data-pdd-natural-width', String(image.naturalWidth),
                        );
                    }
                    if (image.naturalHeight > 0) {
                        image.setAttribute(
                            'data-pdd-natural-height', String(image.naturalHeight),
                        );
                    }
                });

                // Some long-detail blocks use CSS background-image instead of
                // <img>. Add a parser-only image carrying the same nearest
                // context; do not label every background as ``detail``.
                Array.from(document.querySelectorAll('*')).forEach((element) => {
                    const background = getComputedStyle(element).backgroundImage || '';
                    const match = background.match(/url\(["']?([^"')]+)["']?\)/);
                    if (
                        !match
                        || !match[1]
                        || element.querySelector('[data-pdd-background="1"]')
                    ) {
                        return;
                    }
                    const image = document.createElement('img');
                    image.setAttribute('data-pdd-background', '1');
                    image.setAttribute(
                        'data-pdd-context', contextFor(element).join(' | '),
                    );
                    image.src = match[1];
                    element.appendChild(image);
                });
            }
            """
        )
    return page.content()


def _navigation_diagnostics(
    requested_url: str, resolved_url: str, page: Page
) -> None:
    """Log safe navigation facts without query strings or page contents."""

    requested = urlparse(requested_url)
    resolved = urlparse(resolved_url)
    query = parse_qs(requested.query)
    goods_id = next(
        (
            value[0]
            for key in ("goods_id", "goodsId", "goodsid", "id")
            if (value := query.get(key)) and re.fullmatch(r"\d{5,}", value[0])
        ),
        None,
    )
    with suppress(Exception):
        logger.warning(
            "pinduoduo_browser_navigation requested_host=%s requested_path=%s "
            "goods_id=%s resolved_host=%s resolved_path=%s images=%d scripts=%d",
            requested.hostname or "",
            requested.path or "/",
            goods_id or "",
            resolved.hostname or "",
            resolved.path or "/",
            page.locator("img").count(),
            page.locator("script").count(),
        )


def crawl_one_pinduoduo_browser(
    url: str, timeout_ms: int = 30_000, profile_dir: str | None = None
) -> PinduoduoProduct:
    if classify_site(url) is not SiteFamily.PINDUODUO:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "拼多多浏览器抓取只接受拼多多 URL。"
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
            ) as (_browser, context):
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    url.strip(), wait_until="domcontentloaded", timeout=timeout_ms
                )
                page.wait_for_timeout(1_000)
                _navigation_diagnostics(url, page.url, page)
                entry = PinduoduoAdapter().inspect_page(page.content(), page.url)
                if entry.status == "product":
                    _scroll_page(page, timeout_ms)
                return PinduoduoAdapter().parse_product(
                    _rendered_content(page), url.strip(), page.url
                )
        browser = playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
        context = browser.new_context(locale="zh-CN", user_agent="Mozilla/5.0")
        page = context.new_page()
        try:
            page.goto(url.strip(), wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1_000)
            _navigation_diagnostics(url, page.url, page)
            entry = PinduoduoAdapter().inspect_page(page.content(), page.url)
            if entry.status == "product":
                _scroll_page(page, timeout_ms)
            return PinduoduoAdapter().parse_product(
                _rendered_content(page), url.strip(), page.url
            )
        except PlaywrightTimeoutError:
            raise
        finally:
            with suppress(PlaywrightError):
                context.close()
            browser.close()


Product = PinduoduoProduct
Image = PinduoduoImage
crawl_one = crawl_one_pinduoduo
crawl_one_browser = crawl_one_pinduoduo_browser
