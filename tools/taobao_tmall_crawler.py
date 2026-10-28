#!/usr/bin/env python3
"""Fetch publicly visible Taobao/Tmall product images from one URL.

This is intentionally a standalone, low-rate verification tool.  It does not
log in, solve CAPTCHAs, bypass access controls, or crawl search/store pages.
The parser works with the HTML returned by the page and is deliberately
independent of the website application's database and API.

Examples:
    python tools/taobao_tmall_crawler.py \
      "https://item.taobao.com/item.htm?id=123456789012" --out artifacts/item
    python tools/taobao_tmall_crawler.py input.txt --batch --no-download
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import mimetypes
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen


SUPPORTED_HOSTS = {
    "taobao.com",
    "www.taobao.com",
    "item.taobao.com",
    "m.taobao.com",
    "tmall.com",
    "www.tmall.com",
    "detail.tmall.com",
    "m.tmall.com",
    "chaoshi.tmall.com",
}
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
IMAGE_ATTRS = (
    "src",
    "data-src",
    "data-original",
    "data-ks-lazyload",
    "data-lazy-src",
    "data-url",
)
URL_RE = re.compile(r"https?://[^\"'\s<>]+", re.I)
ID_RE = re.compile(r"(?<!\d)(\d{10,14})(?!\d)")


@dataclass
class ImageCandidate:
    url: str
    role: str
    ordinal: int
    source: str = "html"
    downloaded_path: str | None = None
    sha256: str | None = None
    content_type: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class ProductResult:
    source_url: str
    resolved_url: str
    platform: str
    product_id: str | None
    title: str | None
    price: str | None
    images: list[ImageCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_input(value: str) -> str:
    value = value.strip().strip("\u3000")
    # Share text often contains punctuation around the URL.
    match = URL_RE.search(value)
    return match.group(0).rstrip("，。；;）)]") if match else value


def platform_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("tmall.com"):
        return "tmall"
    if host.endswith("taobao.com") or host.endswith("tb.cn"):
        return "taobao"
    return "unknown"


def is_supported_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host in SUPPORTED_HOSTS or host.endswith(".taobao.com") or host.endswith(".tmall.com") or host.endswith(".tb.cn")
    )


def product_id_from_url(url: str, text: str = "") -> str | None:
    query = parse_qs(urlparse(url).query)
    for key in ("id", "item_id", "itemId"):
        if query.get(key) and query[key][0].isdigit():
            return query[key][0]
    match = ID_RE.search(url) or ID_RE.search(text)
    return match.group(1) if match else None


def clean_image_url(raw: str, base_url: str) -> str | None:
    value = html_lib.unescape(raw).strip().replace("\\/", "/")
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return None
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    # Taobao CDN size suffixes are safe to remove for the original asset.
    value = re.sub(r"(?:_\d+x\d+|_\d+x\d+q\d+|_q\d+|_\.webp)(?=(?:\?|$))", "", value, flags=re.I)
    return value


def image_role(context: str, url: str) -> str:
    text = f"{context} {url}".lower()
    if any(token in text for token in ("sku", "propimg", "property", "颜色", "尺码", "规格")):
        return "sku"
    if any(token in text for token in ("desc", "detail", "description", "详情", "图文")):
        return "detail"
    return "main"


def _iter_image_values(page_html: str, base_url: str) -> Iterable[tuple[str, str]]:
    # Attribute extraction catches both static and lazy-loaded image markup.
    tag_re = re.compile(r"<img\b[^>]*>|<source\b[^>]*>", re.I | re.S)
    for tag_match in tag_re.finditer(page_html):
        tag = tag_match.group(0)
        # Prefer the nearest section wrapper. A broad window makes a
        # neighbouring SKU/detail section incorrectly label every image.
        preceding = page_html[max(0, tag_match.start() - 500): tag_match.start()]
        wrappers = re.findall(r"<(?:div|section|li|ul)\b[^>]*>", preceding, re.I | re.S)
        context = (wrappers[-1] if wrappers else preceding[-100:]) + tag
        attrs = dict(re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I))
        for attr in IMAGE_ATTRS:
            if attrs.get(attr):
                cleaned = clean_image_url(attrs[attr], base_url)
                if cleaned:
                    yield cleaned, context
        for attr in ("srcset", "data-srcset"):
            for raw in (attrs.get(attr) or "").split(","):
                cleaned = clean_image_url(raw.strip().split(" ")[0], base_url)
                if cleaned:
                    yield cleaned, context

    # Product pages also embed image arrays in JSON state.  Keep this generic;
    # it avoids depending on frequently changing hashed CSS class names.
    for raw in URL_RE.findall(html_lib.unescape(page_html)):
        lowered = raw.lower()
        # Do not mistake Alibaba's JS/CSS assets (for example windvane.js) for
        # product images merely because they are hosted on an Alibaba CDN.
        if any(lowered.split("?")[0].endswith(ext) for ext in (".js", ".css", ".woff", ".woff2", ".svg")):
            continue
        looks_like_image = any(ext in lowered for ext in IMAGE_EXTENSIONS) or any(
            token in lowered for token in ("/imgextra/", "/bao/uploaded/", "/tbfs/", "/img.alicdn.com/img/")
        )
        if looks_like_image:
            cleaned = clean_image_url(raw, base_url)
            if cleaned:
                yield cleaned, "embedded-state"


def _first_text(page_html: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, page_html, re.I | re.S)
        if match:
            value = html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value
    return None


def parse_product_html(page_html: str, source_url: str, resolved_url: str) -> ProductResult:
    platform = platform_for_url(resolved_url) if platform_for_url(resolved_url) != "unknown" else platform_for_url(source_url)
    title = _first_text(page_html, (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)',
        r"<title[^>]*>(.*?)</title>",
    ))
    price = _first_text(page_html, (
        r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)',
        r'"(?:price|salePrice|currentPrice)"\s*:\s*["\']?([0-9]+(?:\.[0-9]{1,2})?)',
    ))
    product_id = product_id_from_url(resolved_url, page_html)

    seen: set[str] = set()
    images: list[ImageCandidate] = []
    role_counts: dict[str, int] = {"main": 0, "sku": 0, "detail": 0}
    for url, context in _iter_image_values(page_html, resolved_url):
        if url in seen:
            continue
        seen.add(url)
        role = image_role(context, url)
        candidate = ImageCandidate(url=url, role=role, ordinal=role_counts[role], source="html")
        role_counts[role] += 1
        images.append(candidate)

    warnings: list[str] = []
    if not title:
        warnings.append("未从页面 HTML 读取到商品标题，页面可能需要浏览器渲染或登录。")
    if not images:
        warnings.append("未发现图片 URL，页面可能需要浏览器渲染、登录或触发了平台风控。")
    return ProductResult(source_url, resolved_url, platform, product_id, title, price, images, warnings)


def fetch_html(url: str, timeout: int = 25) -> tuple[str, str]:
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - host is validated by caller
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace"), response.geturl()


def download_images(result: ProductResult, output_dir: Path, limit: int, delay: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": result.resolved_url}
    for index, image in enumerate(result.images[:limit], start=1):
        try:
            request = Request(image.url, headers=headers)
            with urlopen(request, timeout=25) as response:  # nosec B310 - image URLs came from page HTML
                content = response.read()
                content_type = response.headers.get_content_type()
            if not content:
                continue
            extension = Path(urlparse(image.url).path).suffix.lower()
            if extension not in IMAGE_EXTENSIONS:
                extension = mimetypes.guess_extension(content_type) or ".bin"
            filename = f"{image.role}-{index:03d}{extension}"
            target = output_dir / filename
            target.write_bytes(content)
            image.downloaded_path = str(target)
            image.sha256 = hashlib.sha256(content).hexdigest()
            image.content_type = content_type
            time.sleep(delay)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            result.warnings.append(f"图片下载失败 {image.url}: {exc}")


def fetch_html_in_browser(url: str, timeout_ms: int = 30000) -> tuple[str, str]:
    """Render one page with Playwright when a JS shell is required.

    Playwright is optional so the parser remains usable in CI and offline
    tests. This function only reads the rendered page and never attempts login,
    CAPTCHA solving, or access-control bypasses.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("浏览器模式需要先安装 playwright，并执行 playwright install chromium。") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="zh-CN", user_agent="Mozilla/5.0")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            return page.content(), page.url
        finally:
            context.close()
            browser.close()


def crawl_one(source_url: str, output_dir: Path | None, limit: int, delay: float, use_browser: bool = False) -> ProductResult:
    source_url = normalize_input(source_url)
    if not is_supported_url(source_url):
        raise ValueError("只接受淘宝/天猫商品链接；不接受搜索页、店铺页或其他站点链接。")
    try:
        page_html, resolved_url = fetch_html_in_browser(source_url) if use_browser else fetch_html(source_url)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"商品页面请求失败：{exc}") from exc
    result = parse_product_html(page_html, source_url, resolved_url)
    if output_dir is not None:
        download_images(result, output_dir, limit, delay)
    return result


def json_ready(result: ProductResult) -> dict:
    return asdict(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="淘宝/天猫单链接商品图片采集器")
    parser.add_argument("url", help="商品链接、分享文本，或 --batch 模式下的输入文件")
    parser.add_argument("--out", type=Path, help="图片输出目录；不提供则只输出 JSON")
    parser.add_argument("--json", type=Path, help="将结果 JSON 写入文件")
    parser.add_argument("--max-images", type=int, default=60, help="最多处理多少张图片，默认 60")
    parser.add_argument("--delay", type=float, default=0.15, help="图片请求之间的间隔秒数")
    parser.add_argument("--batch", action="store_true", help="按行读取多个链接")
    parser.add_argument("--no-download", action="store_true", help="只解析 URL，不下载图片")
    parser.add_argument("--browser", action="store_true", help="使用 Playwright 渲染动态商品页")
    args = parser.parse_args(argv)
    urls = [line.strip() for line in args.url.read_text(encoding="utf-8").splitlines() if line.strip()] if args.batch else [args.url]
    results: list[dict] = []
    for value in urls:
        try:
            output_dir = None if args.no_download else (args.out / str(len(results) + 1) if args.batch and args.out else args.out)
            results.append(json_ready(crawl_one(value, output_dir, max(1, args.max_images), max(0, args.delay), args.browser)))
        except (ValueError, RuntimeError) as exc:
            results.append({"source_url": value, "error": str(exc)})
    payload: object = results if args.batch else results[0]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all("error" not in item for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
