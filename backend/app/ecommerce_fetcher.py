"""Taobao/Tmall product adapter used by the ecommerce replica endpoint.

This module only reads publicly returned HTML. It does not log in, solve
CAPTCHAs, or attempt to bypass platform access controls. The parser is layered
so a licensed data provider or a normal server-side browser can be added later
without changing the API contract.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from playwright.sync_api import Page

HOSTS = ("taobao.com", "tmall.com", "tb.cn")
ID_RE = re.compile(r"(?<!\d)(\d{10,14})(?!\d)")
IMAGE_UI_TOKENS = (
    "favicon",
    "sprite",
    "avatar",
    "qrcode",
    "qr-code",
    "loading",
    "placeholder",
    "default-icon",
    "collect-icon",
    "s.gif",
    "atmosphere_center_image",
    "center_image",
    "qnaigc",
)
SKU_IMAGE_TOKENS = (
    "sku",
    "skuimage",
    "propimg",
    "property",
    "specification",
    "colorprop",
    "sizeprop",
    "尺码",
    "颜色",
    "规格",
)
SKU_CONTEXT_TOKENS = ("sku", "propimg", "property", "颜色分类", "尺码", "规格")
DETAIL_URL_TOKENS = ("/bao/uploaded/", "/imgextra/desc", "/imgextra/detail")
DETAIL_STATE_KEYS = (
    "descImageUrls",
    "detailImageUrls",
    "descImages",
    "detailImages",
    "descImgs",
    "detailImgs",
    "descriptionImages",
    "itemDescImages",
    "descImageList",
    "detailImageList",
)
BLOCKED_PAGE_MARKERS = (
    "\u8bf7\u767b\u5f55",
    "\u767b\u5f55\u540e\u67e5\u770b",
    "\u6ed1\u5757\u9a8c\u8bc1",
    "\u9a8c\u8bc1\u7801",
    "\u8bbf\u95ee\u53d7\u9650",
    "\u8bf7\u6c42\u5f02\u5e38",
    "\u7cfb\u7edf\u7e41\u5fd9",
)
LOGIN_PAGE_MARKERS = ("login_jump",)
CAPTCHA_PAGE_MARKERS = ("\u6ed1\u5757\u9a8c\u8bc1", "\u9a8c\u8bc1\u7801", "nc_1_n1")
RISK_PAGE_MARKERS = (
    "x5secdata",
    "\u8bbf\u95ee\u53d7\u9650",
    "\u8bf7\u6c42\u5f02\u5e38",
)
_CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, Product]] = {}
_CACHE_LOCK = RLock()


@dataclass
class Image:
    url: str
    role: str = "main"
    ordinal: int = 0
    source: str = "dom"
    width: int | None = None
    height: int | None = None
    context: str = ""


@dataclass
class Product:
    source_url: str
    resolved_url: str
    platform: str
    product_id: str | None
    title: str | None
    price: str | None
    images: list[Image] = field(default_factory=list)
    shop_name: str | None = None
    sales: str | None = None
    sku_prop_groups: list[dict[str, object]] = field(default_factory=list)
    props_text: str = ""
    services: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    image_diagnostics: dict[str, int] = field(default_factory=dict)
    # Explicit page-level access state used to distinguish an upstream login,
    # CAPTCHA, or risk-control shell from a genuinely empty product page.
    access_state: str | None = None


def is_supported_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and any(
        host == value or host.endswith(f".{value}") for value in HOSTS
    )


def _clean_url(raw: str, base_url: str) -> str | None:
    value = html_lib.unescape(raw).replace("\\/", "/").strip()
    value = re.split(
        r"[);]\\s*(?:background|data:image)", value, maxsplit=1, flags=re.I
    )[0].rstrip(");, ")
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return None
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = (parsed.hostname or "").lower()
    # Rendered Taobao pages contain cross-site login/redirect URLs with
    # session-bearing query parameters. They are navigation targets, never
    # product assets, and must not escape through the API response.
    if hostname.startswith(("pass.", "login.", "passport.")):
        return None
    sensitive_query_keys = {
        "cookie1",
        "cookie2",
        "cookie17",
        "_tb_token_",
        "wk_cookie2",
        "sgcookie",
        "pacc",
        "unb",
        "opi",
        "tmsc",
    }
    if any(key.casefold() in sensitive_query_keys for key in parse_qs(parsed.query)):
        return None
    # Remove common CDN resize suffixes, while keeping the original source host.
    # Taobao commonly puts the resize segment before the extension, e.g.
    # ``image.jpg_760x760q30.jpg``. Keeping that suffix makes the UI show a
    # compressed thumbnail instead of the source image.
    # Taobao emits both ``image.jpg_760x760q30.jpg`` and
    # ``image_760x760q30.jpg``. Normalize both forms to the original asset so
    # a thumbnail and its full-size counterpart cannot appear as two details.
    value = re.sub(
        r"(?P<ext>\.[a-z]{2,5})_(?:\d+x\d+(?:q\d+)?|q\d+)(?:\.[a-z]{2,5})?(?=\?|$)",
        r"\g<ext>",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"_(?:\d+x\d+(?:q\d+)?|q\d+|\.webp)(?=\.[a-z]{2,5}(?:\?|$)|\?|$)",
        "",
        value,
        flags=re.I,
    )
    if re.search(r"(?:^|[_-])(?:1|10)x(?:1|10)(?:[_-]|\.|$)", value, re.I):
        return None
    return value


def _url_dimensions(value: str) -> tuple[int, int] | None:
    patterns = (
        r"(?:[-_]|tps[-_])(?P<w>\d{2,5})[-x](?P<h>\d{2,5})(?=(?:[._?]|$))",
        r"(?P<w>\d{2,5})x(?P<h>\d{2,5})(?=(?:[._?]|$))",
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return int(match.group("w")), int(match.group("h"))
    return None


def _looks_like_product_image(
    value: str,
    *,
    width: int | None = None,
    height: int | None = None,
    context: str = "",
    source: str = "",
) -> bool:
    lowered = value.lower()
    context_lowered = context.lower()
    if any(token in lowered or token in context_lowered for token in IMAGE_UI_TOKENS):
        return False
    dimensions = (width, height) if width and height else _url_dimensions(lowered)
    if dimensions:
        width, height = dimensions
        smaller, larger = sorted((width, height))
        # Platform chrome, badges and shop icons are normally <= 180px on one
        # side. Keep larger product/detail assets, including portrait images.
        if larger <= 180 or smaller <= 48:
            return False
        if "-tps-" in lowered and (smaller < 300 or larger < 500):
            return False
    if (
        "-gg_dtc" in lowered
        or "-2-tps-" in lowered
        and lowered.endswith((".png", ".gif", ".svg"))
    ):
        return False
    # Broad state arrays contain promotional banners and other page chrome.
    # Taobao's long-detail assets can also use ``-tps-`` names, so only reject
    # the known generic image bucket used by page chrome. Explicit detail
    # state values from the uploaded CDN remain eligible when their dimensions
    # look like real content images.
    if source == "state:detail" and "-tps-" in lowered:
        path = urlparse(lowered).path
        if "/imgextra/i2/" in path or "/tbsp/" in path:
            return False
    return not (
        any(token in lowered for token in SKU_IMAGE_TOKENS)
        or any(token in context_lowered for token in SKU_CONTEXT_TOKENS)
    )


def _first_attr(page_html: str, name: str, value: str) -> str | None:
    patterns = (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page_html, re.I | re.S)
        if match:
            return html_lib.unescape(match.group(1)).strip()
    return None


def _json_ld_products(page_html: str) -> list[dict[str, object]]:
    products: list[dict[str, object]] = []
    for raw in re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        page_html,
        re.I | re.S,
    ):
        try:
            parsed = json.loads(html_lib.unescape(raw).strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        values = parsed if isinstance(parsed, list) else [parsed]
        for value in values:
            if isinstance(value, dict) and (
                value.get("@type") == "Product"
                or isinstance(value.get("image"), (list, str))
            ):
                products.append(value)
    return products


def _state_values(page_html: str, keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    def add_urls(block: str) -> None:
        for raw in re.findall(r"[\"'](https?:(?:\\/|/){2}[^\"']+)[\"']", block, re.I):
            if raw not in seen:
                seen.add(raw)
                values.append(raw)

    for key in keys:
        # Most pages expose a simple URL array. A few rendered versions use
        # arrays of objects (``[{url: ...}]``), so balance the value instead
        # of stopping at the first nested closing bracket.
        for match in re.finditer(
            rf'["\']{re.escape(key)}["\']\s*:\s*', page_html, re.I
        ):
            start = match.end()
            while start < len(page_html) and page_html[start].isspace():
                start += 1
            if start >= len(page_html):
                continue
            opener = page_html[start]
            closer = "]" if opener == "[" else "}" if opener == "{" else None
            if closer:
                depth = 0
                quote: str | None = None
                escaped = False
                end = start
                for index in range(start, len(page_html)):
                    char = page_html[index]
                    if quote:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == quote:
                            quote = None
                        continue
                    if char in ('"', "'"):
                        quote = char
                    elif char == opener:
                        depth += 1
                    elif char == closer:
                        depth -= 1
                        if depth == 0:
                            end = index + 1
                            break
                add_urls(
                    page_html[start:end] if end else page_html[start : start + 12000]
                )
            else:
                add_urls(page_html[start : start + 1000])
    return values


def _image_candidates(
    page_html: str,
    base_url: str,
    *,
    diagnostics: dict[str, int] | None = None,
) -> list[Image]:
    candidates: list[Image] = []
    seen: dict[str, int] = {}
    seen_identity: dict[str, int] = {}
    rejected: set[str] = set()
    dimensions_by_url: dict[str, tuple[int | None, int | None]] = {}

    stats = diagnostics if diagnostics is not None else {}

    def bump(name: str, amount: int = 1) -> None:
        stats[name] = stats.get(name, 0) + amount

    main_state_values = _state_values(
        page_html, ("imageUrls", "mainImageUrls", "galleryImages", "itemImages")
    )
    detail_state_values = _state_values(page_html, DETAIL_STATE_KEYS)
    sku_state_values = _state_values(
        page_html, ("skuImages", "propImages", "propertyImages")
    )
    stats["state_main_values"] = len(main_state_values)
    stats["state_detail_values"] = len(detail_state_values)
    stats["state_sku_values"] = len(sku_state_values)

    main_urls = {
        cleaned for raw in main_state_values if (cleaned := _clean_url(raw, base_url))
    }
    detail_urls = {
        cleaned for raw in detail_state_values if (cleaned := _clean_url(raw, base_url))
    }
    gallery_dom_urls: set[str] = set()
    detail_dom_urls: set[str] = set()
    # SKU URLs can be repeated in the broad detail arrays. Read them first so
    # a later SKU declaration always wins over an earlier detail declaration.
    sku_urls = {
        cleaned for raw in sku_state_values if (cleaned := _clean_url(raw, base_url))
    }
    stats["state_main_clean_urls"] = len(main_urls)
    stats["state_detail_clean_urls"] = len(detail_urls)
    stats["state_sku_clean_urls"] = len(sku_urls)
    # Do not discard a whole trailing ``bao/uploaded`` block based on CDN
    # host alone. On some Taobao pages that block is the actual long-detail
    # sequence; broad removal silently drops most legitimate detail images.

    def image_identity(cleaned: str) -> str:
        parsed = urlparse(cleaned)
        path = unquote(parsed.path).lower()
        path = re.sub(r"(?:_\d+x\d+(?:q\d+)?|_q\d+)(?:\.[a-z]{2,5})?$", "", path)
        # Keep the directory and host in the identity. Different CDN folders
        # can legally contain the same basename; basename-only de-duplication
        # silently removed valid detail images from those pages.
        return f"{parsed.hostname or ''}{path}"

    def add(
        raw: str,
        role: str,
        source: str = "dom",
        *,
        width: int | None = None,
        height: int | None = None,
        context: str = "",
    ) -> None:
        bump("image_values_seen")
        cleaned = _clean_url(raw, base_url)
        if not cleaned:
            bump("rejected_invalid_url")
            return
        if cleaned in rejected:
            bump("skipped_pre_rejected")
            return
        if width is None or height is None:
            known_width, known_height = dimensions_by_url.get(cleaned, (None, None))
            width = width or known_width
            height = height or known_height
        elif cleaned not in dimensions_by_url:
            dimensions_by_url[cleaned] = (width, height)
        cleaned_lower = cleaned.lower()
        if (
            role == "main"
            and "/bao/uploaded/" in cleaned_lower
            and not any(
                token in context.lower()
                for token in ("gallery", "thumb", "主图", "轮播")
            )
        ):
            bump("rejected_uploaded_main_without_gallery_context")
            # A bare uploaded-CDN image is usually a swatch/recommendation
            # asset. It must not consume one of the six gallery slots merely
            # because a page state object listed it under a broad image key.
            return
        if (
            source.startswith("state")
            and "/bao/uploaded/" in cleaned.lower()
            and role != "detail"
            and cleaned not in gallery_dom_urls
        ):
            bump("rejected_uploaded_state_without_gallery_context")
            return
        if cleaned in sku_urls:
            role = "sku"
        elif cleaned in main_urls:
            role = "main"
        elif cleaned in detail_urls:
            role = "detail"
        elif source == "dom" and any(
            token in cleaned.lower() for token in DETAIL_URL_TOKENS[1:]
        ):
            # The rendered detail body commonly uses gw.alicdn.com/bao/uploaded
            # URLs without a useful class/id around the <img> tag.
            role = "detail"
        existing_index = seen.get(cleaned)
        if existing_index is not None:
            bump("dedup_exact")
            existing = candidates[existing_index]
            if role == "sku":
                existing.role = "sku"
            elif role == "main" and existing.role == "detail":
                existing.role = "main"
            return
        identity = image_identity(cleaned)
        identity_index = seen_identity.get(identity)
        if identity_index is not None:
            bump("dedup_identity")
            existing = candidates[identity_index]
            if role == "sku":
                existing.role = "sku"
            elif role == "main" and existing.role == "detail":
                existing.role = "main"
            return
        if not _looks_like_product_image(
            cleaned,
            width=width,
            height=height,
            context=context,
            source=source,
        ):
            bump("rejected_image_filter")
            rejected.add(cleaned)
            return
        seen[cleaned] = len(candidates)
        seen_identity[identity] = len(candidates)
        candidates.append(
            Image(cleaned, role, len(candidates), source, width, height, context)
        )
        bump("accepted_candidates")

    # Prefer image elements from the rendered page. They carry enough nearby
    # context to distinguish the six gallery images from description assets;
    # broad state arrays often also contain logos, size charts, and sprites.
    for match in re.finditer(r"<(?:img|source)\b[^>]*>", page_html, re.I | re.S):
        bump("dom_elements")
        tag = match.group(0)
        attrs = dict(re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I))
        # Do not include the image URL itself in the role context: filenames
        # frequently contain words such as ``sku`` or ``detail`` even when
        # they are ordinary gallery assets.
        context = (
            page_html[max(0, match.start() - 700) : match.start()]
            + " "
            + " ".join(
                attrs.get(name, "") for name in ("class", "id", "alt", "aria-label")
            )
        ).lower()
        # Limit role hints to the nearest open container. A preceding gallery
        # sibling should not classify a later ``<div class="detail">`` image
        # as another main image merely because both fit the 700-character
        # context window.
        role_context = (
            page_html[max(0, match.start() - 700) : match.start()].rsplit("</div>", 1)[
                -1
            ]
            + " "
            + " ".join(
                attrs.get(name, "") for name in ("class", "id", "alt", "aria-label")
            )
        ).lower()
        is_sku_context = any(token in role_context for token in SKU_CONTEXT_TOKENS)
        is_detail_context = any(
            token in role_context
            for token in ("desc", "detail", "description", "详情", "图文")
        )
        is_gallery_context = any(
            token in role_context for token in ("gallery", "thumb", "主图", "轮播")
        )
        role = (
            "sku"
            if is_sku_context
            else "main"
            if is_gallery_context
            else "detail"
            if is_detail_context
            else "main"
        )
        width = next(
            (
                int(attrs[name])
                for name in ("width", "data-width", "data-natural-width")
                if attrs.get(name, "").isdigit()
            ),
            None,
        )
        height = next(
            (
                int(attrs[name])
                for name in ("height", "data-height", "data-natural-height")
                if attrs.get(name, "").isdigit()
            ),
            None,
        )
        for attr in (
            "src",
            "data-src",
            "data-original",
            "data-ks-lazyload",
            "data-lazy-src",
            "data-url",
        ):
            if attrs.get(attr):
                bump("dom_url_values")
                cleaned = _clean_url(attrs[attr], base_url)
                if cleaned:
                    dimensions_by_url.setdefault(cleaned, (width, height))
                    if not _looks_like_product_image(
                        cleaned,
                        width=width,
                        height=height,
                        context=context,
                        source="dom",
                    ):
                        rejected.add(cleaned)
                if (
                    main_urls
                    and role == "main"
                    and cleaned not in main_urls
                    and not is_gallery_context
                ):
                    continue
                if cleaned:
                    if is_gallery_context:
                        gallery_dom_urls.add(cleaned)
                    if is_detail_context:
                        detail_dom_urls.add(cleaned)
                add(attrs[attr], role, width=width, height=height, context=context)
        for attr in ("srcset", "data-srcset"):
            for item in (attrs.get(attr) or "").split(","):
                if item.strip():
                    bump("dom_url_values")
                    raw = item.strip().split(" ")[0]
                    cleaned = _clean_url(raw, base_url)
                    if cleaned:
                        dimensions_by_url.setdefault(cleaned, (width, height))
                        if not _looks_like_product_image(
                            cleaned,
                            width=width,
                            height=height,
                            context=context,
                            source="dom",
                        ):
                            rejected.add(cleaned)
                    if (
                        main_urls
                        and role == "main"
                        and cleaned not in main_urls
                        and not is_gallery_context
                    ):
                        continue
                    if cleaned:
                        if is_gallery_context:
                            gallery_dom_urls.add(cleaned)
                        if is_detail_context:
                            detail_dom_urls.add(cleaned)
                    add(raw, role, width=width, height=height, context=context)

    # State arrays are a fallback for lazy-loaded images that are not emitted
    # as elements in the initial HTML. They are appended after DOM candidates
    # so they cannot displace the actual gallery order.
    for raw in main_state_values:
        add(raw, "main", "state:main")
    for raw in detail_state_values:
        add(raw, "detail", "state:detail")
    for raw in sku_state_values:
        add(raw, "sku", "state:sku")

    # JSON-LD is a safe, structured fallback for pages that omit image tags
    # and broad state arrays. It is intentionally narrower than scanning every
    # URL embedded in scripts, which also captures page chrome and trackers.
    for product in _json_ld_products(page_html):
        raw_images = product.get("image")
        values = raw_images if isinstance(raw_images, list) else [raw_images]
        for raw in values:
            if isinstance(raw, str):
                add(raw, "main", "jsonld")

    # A Taobao/Tmall gallery is at most six images in the user-facing flow.
    # Keep explicit gallery/state-main candidates ahead of ambiguous DOM
    # candidates. Some rendered pages omit any ``detail`` class from the
    # long-description images, so blindly dropping every seventh ``main``
    # candidate loses real detail assets. Only promote an overflow candidate
    # when the page also contains independent detail evidence and the asset
    # has a content-image shape/path; otherwise retain the conservative drop.
    explicit_main_urls = main_urls | gallery_dom_urls
    explicit_detail_urls = detail_urls | detail_dom_urls
    has_gallery_evidence = any(
        image.role == "main" and image.url in explicit_main_urls for image in candidates
    )
    has_detail_evidence = any(image.role == "detail" for image in candidates)

    def is_detail_shaped(image: Image) -> bool:
        if image.source != "dom":
            return False
        parsed_path = urlparse(image.url).path.lower()
        if "/bao/uploaded/" in parsed_path or "/imgextra/desc" in parsed_path:
            return True
        if image.width is not None and image.height is not None:
            width, height = image.width, image.height
        else:
            dimensions = _url_dimensions(image.url)
            if dimensions is None:
                return False
            width, height = dimensions
        smaller, larger = sorted((width, height))
        return larger >= 900 and larger / max(smaller, 1) >= 1.35

    main_candidates = [image for image in candidates if image.role == "main"]
    prioritized_main = [
        image for image in main_candidates if image.url in explicit_main_urls
    ]
    ambiguous_main = [
        image for image in main_candidates if image.url not in explicit_main_urls
    ]
    main_order = iter(prioritized_main + ambiguous_main)
    main_seen = 0
    kept: list[Image] = []
    for image in candidates:
        if image.role != "main":
            kept.append(image)
            continue
        image = next(main_order)
        if main_seen < 6:
            main_seen += 1
            kept.append(image)
            continue
        if (
            has_gallery_evidence
            and has_detail_evidence
            and image.url not in explicit_main_urls
            and image.url not in explicit_detail_urls
            and is_detail_shaped(image)
        ):
            image.role = "detail"
            bump("reclassified_main_overflow_to_detail")
            kept.append(image)
            continue
        bump("rejected_main_overflow")
    candidates = kept
    role_ordinals: dict[str, int] = {"main": 0, "detail": 0, "sku": 0}
    for image in candidates:
        image.ordinal = role_ordinals[image.role]
        role_ordinals[image.role] += 1
    stats["final_main"] = role_ordinals["main"]
    stats["final_detail"] = role_ordinals["detail"]
    stats["final_sku"] = role_ordinals["sku"]
    stats["final_detail_dom"] = sum(
        1 for image in candidates if image.role == "detail" and image.source == "dom"
    )
    stats["final_detail_state"] = sum(
        1
        for image in candidates
        if image.role == "detail" and image.source == "state:detail"
    )
    return candidates


def parse_product_html(page_html: str, source_url: str, resolved_url: str) -> Product:
    source_host = (
        urlparse(resolved_url).hostname or urlparse(source_url).hostname or ""
    ).lower()
    platform = "tmall" if "tmall.com" in source_host else "taobao"
    query = parse_qs(urlparse(resolved_url).query)
    product_id = next(
        (query[key][0] for key in ("id", "item_id", "itemId") if query.get(key)), None
    )
    if not product_id:
        match = ID_RE.search(page_html)
        product_id = match.group(1) if match else None

    ld_products = _json_ld_products(page_html)
    ld = ld_products[0] if ld_products else {}
    title = _first_attr(page_html, "og:title", "og:title") or _first_attr(
        page_html, "title", "title"
    )
    title = title or (str(ld.get("name")).strip() if ld.get("name") else None)
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
        title = (
            re.sub(
                r"\s+",
                " ",
                html_lib.unescape(re.sub(r"<[^>]+>", " ", title_match.group(1))),
            ).strip()
            if title_match
            else None
        )
    offers = ld.get("offers") if isinstance(ld, dict) else None
    ld_price = offers.get("price") if isinstance(offers, dict) else None
    price_match = re.search(
        r'"(?:price|salePrice|currentPrice)"\s*:\s*["\']?([0-9]+(?:\.[0-9]{1,2})?)',
        page_html,
        re.I,
    )
    price = (
        str(ld_price)
        if ld_price is not None
        else price_match.group(1)
        if price_match
        else None
    )

    shop_name = _first_attr(page_html, "og:site_name", "og:site_name")
    shop_match = re.search(
        r'"(?:shopName|sellerName|nick)"\s*:\s*["\']([^"\']+)', page_html, re.I
    )
    shop_name = shop_name or (shop_match.group(1).strip() if shop_match else None)
    sales_match = re.search(
        r'"(?:sales|soldCount|volume)"\s*:\s*["\']?([^,"\'}]+)', page_html, re.I
    )
    sales = sales_match.group(1).strip() if sales_match else None

    props_text = ""
    props_match = re.search(r'"propsText"\s*:\s*["\']([^"\']*)', page_html, re.I)
    if props_match:
        props_text = unquote(props_match.group(1)).replace("\\n", "\n")
    sku_prop_groups: list[dict[str, object]] = []
    for name, values in re.findall(
        r'"(?:name|propName)"\s*:\s*["\']([^"\']+)["\'][^{}]{0,300}?"values?"\s*:\s*\[([^\]]*)\]',
        page_html,
        re.I | re.S,
    ):
        parsed_values = [
            html_lib.unescape(item)
            for item in re.findall(r'["\']([^"\']+)["\']', values)
        ]
        if parsed_values:
            sku_prop_groups.append({"name": name, "values": parsed_values})

    services = []
    for service in ("48 小时内发货", "免运费", "退货宝", "7 天无理由退货"):
        if service in page_html:
            services.append(service)
    image_diagnostics: dict[str, int] = {}
    images = _image_candidates(page_html, resolved_url, diagnostics=image_diagnostics)
    warnings: list[str] = []
    # Taobao may return a perfectly valid HTML document for a login or risk
    # control page. Its logo/QR assets must never be presented as product
    # images. Treat the response as blocked and let the route surface a clear
    # retry/provider fallback message instead.
    page_text = re.sub(
        r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|<[^>]+>",
        " ",
        page_html,
        flags=re.I | re.S,
    )
    page_text = re.sub(r"\s+", " ", html_lib.unescape(page_text)).strip().lower()
    normalized_title = (title or "").strip().lower()
    normalized_html = page_html.lower()
    blocked = normalized_title in {
        "\u767b\u5f55",
        "\u8bf7\u767b\u5f55",
        "\u9a8c\u8bc1",
    } or any(marker in page_text for marker in BLOCKED_PAGE_MARKERS) or any(
        marker in normalized_html
        for marker in (*LOGIN_PAGE_MARKERS, *CAPTCHA_PAGE_MARKERS, *RISK_PAGE_MARKERS)
    )
    access_state: str | None = None
    if blocked:
        # Prefer the most actionable diagnosis when a platform combines
        # multiple prompts on one interstitial (for example, "请登录后完成
        # 验证码").  No page content is returned to callers or logs.
        if any(
            marker in page_text or marker in normalized_html
            for marker in CAPTCHA_PAGE_MARKERS
        ):
            access_state = "captcha_required"
        elif normalized_title in {"登录", "请登录"} or any(
            marker in page_text or marker in normalized_html
            for marker in ("请登录", "登录后查看", *LOGIN_PAGE_MARKERS)
        ):
            access_state = "login_required"
        else:
            access_state = "risk_controlled"
    if blocked:
        images = []
        title = None
        price = None
        shop_name = None
        sales = None
        sku_prop_groups = []
        props_text = ""
        services = []
        warnings.append("平台返回了登录或风控页面，未读取到商品数据。")
    if not title:
        warnings.append("未从页面读取到商品标题，页面可能需要浏览器渲染。")
    if not images:
        warnings.append("未发现图片 URL，页面可能需要浏览器渲染或触发了平台风控。")
    return Product(
        source_url,
        resolved_url,
        platform,
        product_id,
        title,
        price,
        images,
        shop_name,
        sales,
        sku_prop_groups,
        props_text,
        services,
        warnings,
        image_diagnostics,
        access_state,
    )


def crawl_one(url: str, *_args: object, **_kwargs: object) -> Product:
    source_url = url.strip()
    if not is_supported_url(source_url):
        raise ValueError("只接受淘宝/天猫商品链接。")
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
    with urlopen(request, timeout=30) as response:  # nosec B310 - host validated above
        body = response.read().decode(
            response.headers.get_content_charset() or "utf-8", errors="replace"
        )
        resolved_url = response.geturl()
    parsed = parse_product_html(body, source_url, resolved_url)
    cache_key = parsed.product_id or resolved_url
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        _CACHE[cache_key] = (now, parsed)
    return parsed


def _scroll_page_for_lazy_images(page: Page, timeout_ms: int) -> None:
    """Walk the rendered page so viewport-triggered detail images are loaded.

    Taobao detail sections commonly lazy-load images only when their elements
    approach the viewport. A single ``page.content()`` call after navigation
    therefore captures only the first visible portion of a long description.
    This bounded pass only scrolls the page; it does not submit forms or
    interact with login/risk controls.
    """

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
                root?.scrollHeight || 0,
                body?.scrollHeight || 0,
                root?.offsetHeight || 0,
                body?.offsetHeight || 0,
              );
              const viewport = window.innerHeight || 800;
              const maxY = Math.max(0, height - viewport);
              const step = Math.max(600, viewport * 0.8);
              const y = Math.min(maxY, window.scrollY + step);
              window.scrollTo(0, y);
              return { y, height, viewport, imageCount: document.images.length };
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


def crawl_one_browser(
    url: str,
    timeout_ms: int = 30_000,
    profile_dir: str | None = None,
) -> Product:
    """Render a public product page with Playwright when explicitly enabled.

    Playwright is optional and this path never submits credentials or attempts
    to solve an interstitial. A missing browser runtime is reported clearly so
    callers can keep the static parser as the default.
    """
    try:
        from playwright.sync_api import (
            Error as PlaywrightError,
        )
        from playwright.sync_api import (
            TimeoutError as PlaywrightTimeoutError,
        )
        from playwright.sync_api import (
            sync_playwright,
        )
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise RuntimeError("BROWSER_RENDERER_UNAVAILABLE") from exc
    if not is_supported_url(url):
        raise ValueError("只接受淘宝/天猫商品链接。")
    with sync_playwright() as playwright:
        if profile_dir:
            context = playwright.chromium.launch_persistent_context(
                profile_dir,
                headless=True,
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/128 Safari/537.36"
                ),
            )
            browser = None
        else:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(locale="zh-CN", user_agent="Mozilla/5.0")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1_500)
            with suppress(PlaywrightTimeoutError):
                page.wait_for_load_state("load", timeout=min(timeout_ms, 5_000))
            # Trigger viewport-based lazy loading in long detail sections.
            # If a page disallows script evaluation, retain the normal parser
            # path rather than failing the entire product fetch.
            with suppress(PlaywrightError):
                _scroll_page_for_lazy_images(page, timeout_ms)
            # Preserve only non-sensitive rendering facts in the HTML fixture.
            # Natural dimensions catch transparent strips and tiny swatches
            # whose URLs carry no size suffix (for example 1500x2 or 1500x16).
            with suppress(PlaywrightError):
                page.evaluate(
                    """
                    () => {
                      for (const image of document.images) {
                        if (image.naturalWidth > 0) {
                          image.setAttribute(
                            'data-natural-width',
                            String(image.naturalWidth),
                          );
                        }
                        if (image.naturalHeight > 0) {
                          image.setAttribute(
                            'data-natural-height',
                            String(image.naturalHeight),
                          );
                        }
                      }
                    }
                    """
                )
            content: str | None = None
            content_error: PlaywrightError | None = None
            for _ in range(3):
                try:
                    content = page.content()
                    break
                except PlaywrightError as exc:
                    content_error = exc
                    page.wait_for_timeout(500)
            if content is None:
                raise RuntimeError(
                    "浏览器页面仍在导航，无法读取商品内容。"
                ) from content_error
            return parse_product_html(content, url, page.url)
        finally:
            context.close()
            if browser is not None:
                browser.close()
