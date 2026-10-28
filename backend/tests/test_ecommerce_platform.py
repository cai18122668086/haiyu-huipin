from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.ecommerce_routes as ecommerce_routes
from app.ecommerce_platform import (
    NetworkMode,
    PlatformPolicyError,
    SiteFamily,
    classify_url,
    resolve_site_policy,
    validate_fetch_context,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://detail.1688.com/offer/123456789.html", SiteFamily.ALIBABA_1688),
        ("https://qr.1688.com/s/abc123", SiteFamily.ALIBABA_1688),
        ("https://detail.1688.com/offer/123456789.html?spm=a", SiteFamily.ALIBABA_1688),
        ("https://item.taobao.com/item.htm?id=123456789012", SiteFamily.TAOBAO),
        ("https://detail.tmall.com/item.htm?id=123456789012", SiteFamily.TMALL),
        ("https://item.jd.com/123456.html", SiteFamily.JD),
        (
            "https://npcitem.jd.hk/65656494448.html?extension_id=example&jd_pop=abc",
            SiteFamily.JD,
        ),
        ("https://www.amazon.co.jp/dp/B000000000", SiteFamily.AMAZON),
        ("https://not-1688.com/offer/123456789.html", SiteFamily.UNKNOWN),
    ],
)
def test_classify_url_uses_parsed_hostname(url: str, expected: SiteFamily) -> None:
    assert classify_url(url) is expected


def test_1688_policy_is_direct_and_uses_an_independent_profile() -> None:
    policy = resolve_site_policy("https://qr.1688.com/s/abc123")

    assert policy.site_family is SiteFamily.ALIBABA_1688
    assert policy.adapter_name == "1688"
    assert policy.network_mode is NetworkMode.DIRECT
    assert policy.session_profile == "domestic-1688"
    assert policy.proxy_required is False


def test_global_buy_jd_policy_uses_the_existing_jd_adapter_and_session() -> None:
    policy = resolve_site_policy("https://npcitem.jd.hk/65656494448.html")

    assert policy.site_family is SiteFamily.JD
    assert policy.adapter_name == "jd"
    assert policy.network_mode is NetworkMode.DIRECT
    assert policy.session_profile == "domestic-jd"


def test_domestic_policy_fails_closed_when_proxy_is_configured() -> None:
    with pytest.raises(PlatformPolicyError, match="仅允许国内直连") as caught:
        resolve_site_policy(
            "https://detail.1688.com/offer/123456789.html",
            proxy_configured=True,
        )

    assert caught.value.code == "NETWORK_POLICY_VIOLATION"


def test_amazon_policy_fails_closed_without_proxy() -> None:
    with pytest.raises(PlatformPolicyError, match="必须使用独立代理") as caught:
        resolve_site_policy("https://www.amazon.com/dp/B000000000")

    assert caught.value.code == "PROXY_REQUIRED"


def test_amazon_policy_requires_proxy_and_never_changes_to_direct() -> None:
    policy = resolve_site_policy(
        "https://www.amazon.com/dp/B000000000",
        proxy_configured=True,
    )

    assert policy.site_family is SiteFamily.AMAZON
    assert policy.network_mode is NetworkMode.PROXY
    assert policy.session_profile == "amazon-proxy"


def test_frontend_platform_hint_cannot_override_backend_classification() -> None:
    with pytest.raises(PlatformPolicyError) as caught:
        resolve_site_policy(
            "https://detail.1688.com/offer/123456789.html",
            requested_platform="taobao",
        )

    assert caught.value.code == "NETWORK_POLICY_VIOLATION"


def test_fetch_context_rejects_adapter_network_or_profile_mismatch() -> None:
    policy = resolve_site_policy("https://detail.1688.com/offer/123456789.html")

    validate_fetch_context(
        policy,
        adapter_name="1688",
        network_mode="direct",
        session_profile="domestic-1688",
    )
    with pytest.raises(PlatformPolicyError, match="网络模式不匹配"):
        validate_fetch_context(
            policy,
            adapter_name="1688",
            network_mode="proxy",
            session_profile="domestic-1688",
        )


def test_1688_route_is_recognized_but_not_accidentally_sent_to_taobao(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_1688_fetch(target_url: str) -> object:
        calls.append(target_url)
        raise RuntimeError("1688 adapter sentinel")

    monkeypatch.setattr(ecommerce_routes, "crawl_one_1688", fake_1688_fetch)
    monkeypatch.setattr(
        ecommerce_routes,
        "crawl_one",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("1688 URL must not reach the Taobao adapter")
        ),
    )
    response = client.post(
        "/api/ecommerce/fetch-item",
        json={
            "url": "https://detail.1688.com/offer/123456789.html",
            "platform": "auto",
            "includeDescImages": True,
        },
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "FETCH_FAILED"
    assert calls == ["https://detail.1688.com/offer/123456789.html"]


def test_unknown_route_is_rejected_before_any_adapter_is_called(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/ecommerce/fetch-item",
        json={"url": "https://not-1688.com/offer/123456789.html"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SITE_NOT_RECOGNIZED"


def test_jd_risk_redirect_is_rejected_as_a_crawl_target() -> None:
    with pytest.raises(PlatformPolicyError, match="安全验证页") as caught:
        resolve_site_policy(
            "https://cfe.m.jd.com/privatedomain/risk_handler/03101900/"
            "?returnurl=https%3A%2F%2Fitem.jd.com%2F10031245841079.html"
        )
    assert caught.value.code == "JD_VERIFICATION_URL"
