"""Platform classification and hard network policy for ecommerce fetching.

The classifier is intentionally independent from any platform parser. It is the
security boundary used before a request is sent to an adapter: the backend
re-parses the URL, selects the adapter/network policy, and rejects mismatches
instead of trusting a client-provided platform hint.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class SiteFamily(StrEnum):
    TAOBAO = "taobao"
    TMALL = "tmall"
    ALIBABA_1688 = "alibaba1688"
    JD = "jd"
    PINDUODUO = "pinduoduo"
    AMAZON = "amazon"
    UNKNOWN = "unknown"


class NetworkMode(StrEnum):
    DIRECT = "direct"
    PROXY = "proxy"


class PlatformPolicyError(ValueError):
    """A request cannot be executed under the resolved platform policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SitePolicy:
    site_family: SiteFamily
    site_region: str
    adapter_name: str
    network_mode: NetworkMode
    session_profile: str
    host: str
    canonical_url: str

    @property
    def proxy_required(self) -> bool:
        return self.network_mode is NetworkMode.PROXY

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "siteFamily": self.site_family.value,
            "siteRegion": self.site_region,
            "adapterName": self.adapter_name,
            "networkMode": self.network_mode.value,
            "sessionProfile": self.session_profile,
            "host": self.host,
            "canonicalUrl": self.canonical_url,
            "proxyRequired": self.proxy_required,
        }


# Short links are identified by their platform-owned host and retain the
# platform's network policy. Resolution to a final product URL is a later
# adapter concern and must not happen through a different network route.
_TAOBAO_HOSTS = ("taobao.com", "tb.cn")
_TMALL_HOSTS = ("tmall.com",)
_1688_HOSTS = ("1688.com",)
# ``npcitem.jd.hk`` is JD Global Purchase's product-page host. It uses the same
# product-page/session flow as domestic JD, so keep it in the JD family rather
# than treating the Hong Kong hostname as an unknown marketplace. Keep the host
# entry specific instead of accepting all of ``jd.hk``.
_JD_HOSTS = ("jd.com", "3.cn", "npcitem.jd.hk")
# PDD product pages and share redirects currently use both official roots.
# Keep image-only CDN hosts out of site classification; they are not product pages.
_PINDUODUO_HOSTS = ("pinduoduo.com", "yangkeduo.com")
_AMAZON_HOSTS = (
    "amazon.com",
    "amazon.ca",
    "amazon.com.mx",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.nl",
    "amazon.se",
    "amazon.pl",
    "amazon.com.tr",
    "amazon.co.jp",
    "amazon.in",
    "amazon.com.au",
    "amazon.sg",
    "amazon.ae",
    "amazon.sa",
    "amazon.com.br",
)

_POLICY_BY_SITE: dict[SiteFamily, tuple[str, NetworkMode, str]] = {
    SiteFamily.TAOBAO: ("taobao", NetworkMode.DIRECT, "domestic-taobao-tmall"),
    SiteFamily.TMALL: ("tmall", NetworkMode.DIRECT, "domestic-taobao-tmall"),
    SiteFamily.ALIBABA_1688: ("1688", NetworkMode.DIRECT, "domestic-1688"),
    SiteFamily.JD: ("jd", NetworkMode.DIRECT, "domestic-jd"),
    SiteFamily.PINDUODUO: ("pinduoduo", NetworkMode.DIRECT, "domestic-pinduoduo"),
    SiteFamily.AMAZON: ("amazon", NetworkMode.PROXY, "amazon-proxy"),
}


def _host_is(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _normalise_input_url(url: str) -> tuple[str, str]:
    value = url.strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise PlatformPolicyError(
            "UNSUPPORTED_URL", "商品链接必须是有效的 HTTP 或 HTTPS URL。"
        )
    # A JD risk/login redirect is an access-control surface, never a product
    # target. Accepting it as a crawl URL makes the worker repeatedly fetch the
    # challenge page and can leave a valid account looking broken. The caller
    # must submit the original item/detail URL after completing any challenge.
    if host == "cfe.m.jd.com" and "risk_handler" in parsed.path.casefold():
        raise PlatformPolicyError(
            "JD_VERIFICATION_URL",
            "这是京东登录或安全验证页地址，请提交验证跳转中的商品详情页链接（item.jd.com/...），并先在该账号的本机浏览器完成验证。",
        )
    return value, host


def classify_site(url: str) -> SiteFamily:
    """Classify a URL using its parsed hostname, never substring matching."""

    _, host = _normalise_input_url(url)
    if _host_is(host, _TMALL_HOSTS):
        return SiteFamily.TMALL
    if _host_is(host, _TAOBAO_HOSTS):
        return SiteFamily.TAOBAO
    if _host_is(host, _1688_HOSTS):
        return SiteFamily.ALIBABA_1688
    if _host_is(host, _JD_HOSTS):
        return SiteFamily.JD
    if _host_is(host, _PINDUODUO_HOSTS):
        return SiteFamily.PINDUODUO
    if _host_is(host, _AMAZON_HOSTS):
        return SiteFamily.AMAZON
    return SiteFamily.UNKNOWN


def _requested_site_family(requested_platform: str) -> SiteFamily | None:
    if requested_platform == "auto":
        return None
    try:
        return SiteFamily(requested_platform)
    except ValueError as exc:
        raise PlatformPolicyError(
            "SITE_NOT_RECOGNIZED", "不支持的前端平台标识。"
        ) from exc


def resolve_site_policy(
    url: str,
    *,
    requested_platform: str = "auto",
    proxy_configured: bool = False,
) -> SitePolicy:
    """Resolve and validate the immutable policy for one fetch attempt.

    ``proxy_configured`` is supplied by server-side settings only. Domestic
    platforms fail closed when it is enabled; Amazon fails closed when it is
    not enabled. No automatic direct/proxy fallback is performed.
    """

    source_url, host = _normalise_input_url(url)
    site_family = classify_site(source_url)
    if site_family is SiteFamily.UNKNOWN:
        raise PlatformPolicyError("SITE_NOT_RECOGNIZED", "无法识别商品链接所属平台。")

    requested_family = _requested_site_family(requested_platform)
    if requested_family is not None and requested_family is not site_family:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION",
            "前端平台标识与后端识别结果不一致，已拒绝执行。",
        )

    adapter_name, network_mode, session_profile = _POLICY_BY_SITE[site_family]
    if network_mode is NetworkMode.DIRECT and proxy_configured:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION",
            f"{site_family.value} 仅允许国内直连，检测到代理配置后拒绝执行。",
        )
    if network_mode is NetworkMode.PROXY and not proxy_configured:
        raise PlatformPolicyError(
            "PROXY_REQUIRED", "亚马逊抓取必须使用独立代理通道，当前代理不可用。"
        )

    return SitePolicy(
        site_family=site_family,
        site_region="CN" if site_family is not SiteFamily.AMAZON else "GLOBAL",
        adapter_name=adapter_name,
        network_mode=network_mode,
        session_profile=session_profile,
        host=host,
        canonical_url=source_url,
    )


def validate_fetch_context(
    policy: SitePolicy,
    *,
    adapter_name: str,
    network_mode: str,
    session_profile: str,
) -> None:
    """Reject an adapter context that does not match the resolved policy."""

    if adapter_name != policy.adapter_name:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "站点与适配器不匹配，已拒绝执行。"
        )
    if network_mode != policy.network_mode.value:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "站点与网络模式不匹配，已拒绝执行。"
        )
    if session_profile != policy.session_profile:
        raise PlatformPolicyError(
            "NETWORK_POLICY_VIOLATION", "会话 Profile 与站点策略不匹配，已拒绝执行。"
        )


# Explicit aliases make the intended public boundary discoverable to the next
# adapter implementation without coupling it to the old Taobao fetcher name.
classify_url = classify_site
resolve_fetch_policy = resolve_site_policy


