import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from app import haiyu_payments as payments
from app.errors import ApiError
from tests.test_credit_payments import PUBLIC, sign
from tests.test_credit_payments import pilot_client as pilot_client  # noqa: F401

HTTPClient = httpx.Client


def alipay_response(method, payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return httpx.Response(
        200,
        content='{"'
        + method.replace(".", "_")
        + '_response":'
        + raw
        + ',"sign":'
        + json.dumps(sign(raw.encode()))
        + "}",
    )


def test_alipay_refund_uses_persisted_request_and_delayed_query(
    pilot_client, monkeypatch
):
    seen = []

    def handle(request):
        fields = dict(parse_qsl(request.content.decode()))
        signature = fields.pop("sign")
        payments._verify(
            "&".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(),
            signature,
            PUBLIC,
        )
        method = fields["method"]
        seen.append(method)
        biz = json.loads(fields["biz_content"])
        assert biz["out_trade_no"] == "pay_test"
        assert biz["out_request_no"] == "ref_test"
        if method == "alipay.trade.refund":
            assert biz["refund_amount"] == "12.34"
            return alipay_response(
                method,
                {"code": "10000", "out_trade_no": "pay_test", "refund_fee": "12.34"},
            )
        return alipay_response(
            method,
            {
                "code": "10000",
                "out_trade_no": "pay_test",
                "out_request_no": "ref_test",
                "refund_amount": "12.34",
                "refund_status": "REFUND_SUCCESS",
            },
        )

    transport(monkeypatch, handle)
    settings = pilot_client.app.state.settings
    assert (
        payments.create_refund(
            settings, "alipay", "pay_test", "ref_test", 1234, 9900, "Synthetic"
        )["status"]
        == "processing"
    )
    assert seen == ["alipay.trade.refund"]
    assert (
        payments.query_refund(settings, "alipay", "pay_test", "ref_test", 1234, 9900)[
            "status"
        ]
        == "succeeded"
    )


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_refund_mismatched_amount_never_settles(pilot_client, monkeypatch, channel):
    if channel == "alipay":
        transport(
            monkeypatch,
            lambda req: alipay_response(
                "alipay.trade.fastpay.refund.query",
                {
                    "code": "10000",
                    "out_trade_no": "pay_test",
                    "out_request_no": "ref_test",
                    "refund_amount": "99.00",
                    "refund_status": "REFUND_SUCCESS",
                },
            ),
        )
    else:
        transport(
            monkeypatch,
            lambda req: signed_response(
                json.dumps(
                    {
                        "out_trade_no": "pay_test",
                        "out_refund_no": "ref_test",
                        "status": "SUCCESS",
                        "amount": {"total": 9900, "refund": 9900, "currency": "CNY"},
                    }
                ).encode()
            ),
        )
    with pytest.raises(ValueError):
        payments.query_refund(
            pilot_client.app.state.settings, channel, "pay_test", "ref_test", 1234, 9900
        )


def test_wechat_refund_request_and_abnormal_never_restore_rights(
    pilot_client, monkeypatch
):
    def handle(request):
        assert request.url.path == "/v3/refund/domestic/refunds"
        data = json.loads(request.content)
        assert data["out_refund_no"] == "ref_test"
        assert data["amount"] == {"refund": 1234, "total": 9900, "currency": "CNY"}
        assert data["notify_url"].endswith("/wechat/refund-notify")
        return signed_response(
            json.dumps(
                {**data, "status": "ABNORMAL", "refund_id": "wx_refund"}
            ).encode()
        )

    transport(monkeypatch, handle)
    result = payments.create_refund(
        pilot_client.app.state.settings,
        "wechat",
        "pay_test",
        "ref_test",
        1234,
        9900,
        "Synthetic",
    )
    assert (
        result["status"] == "processing"
        and result["error_code"] == "WECHAT_REFUND_ABNORMAL"
    )


def transport(monkeypatch, handler):
    monkeypatch.setattr(
        payments.httpx,
        "Client",
        lambda **kwargs: HTTPClient(transport=httpx.MockTransport(handler), **kwargs),
    )


def signed_response(body):
    stamp, nonce = str(int(time.time())), "provider-response-nonce"
    signature = sign(stamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n")
    return httpx.Response(
        200,
        content=body,
        headers={
            "Wechatpay-Serial": "test-public-id",
            "Wechatpay-Timestamp": stamp,
            "Wechatpay-Nonce": nonce,
            "Wechatpay-Signature": signature,
        },
    )


def test_wechat_signs_exact_body_and_path_and_verifies_response(
    pilot_client, monkeypatch
):
    settings = pilot_client.app.state.settings
    order_id = "pay_synthetic_001"

    def handle(request):
        assert str(request.url) == payments.WECHAT_URL + "/v3/pay/transactions/native"
        values = dict(re.findall(r'(\w+)="([^"]+)"', request.headers["Authorization"]))
        assert values["mchid"] == "test-mch"
        assert values["serial_no"] == "test-serial"
        message = (
            (
                f"POST\n/v3/pay/transactions/native\n{values['timestamp']}\n"
                f"{values['nonce_str']}\n"
            ).encode()
            + request.content
            + b"\n"
        )
        payments._verify(message, values["signature"], PUBLIC)
        payload = json.loads(request.content)
        assert payload["amount"] == {"total": 2990, "currency": "CNY"}
        assert payload["out_trade_no"] == order_id
        assert (
            payload["notify_url"]
            == "https://huipin.haiyushuke.com/api/v1/payments/wechat/notify"
        )
        return signed_response(b'{"code_url":"weixin://wxpay/test"}')

    transport(monkeypatch, handle)
    assert (
        payments.create_checkout(settings, "wechat", order_id, "Synthetic", 2990)
        == "weixin://wxpay/test"
    )


def test_wechat_query_path_is_signed_including_query(pilot_client, monkeypatch):
    def handle(request):
        assert request.method == "GET"
        path = request.url.raw_path.decode()
        assert path.endswith("?mchid=test-mch")
        values = dict(re.findall(r'(\w+)="([^"]+)"', request.headers["Authorization"]))
        message = (
            f"GET\n{path}\n{values['timestamp']}\n{values['nonce_str']}\n\n".encode()
        )
        payments._verify(message, values["signature"], PUBLIC)
        return signed_response(
            b'{"trade_state":"NOTPAY","out_trade_no":"pay_synthetic"}'
        )

    transport(monkeypatch, handle)
    assert (
        payments.query_payment(
            pilot_client.app.state.settings, "wechat", "pay_synthetic"
        )["trade_state"]
        == "NOTPAY"
    )


def test_wechat_rejects_unsigned_success_response(pilot_client, monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"code_url": "weixin://wxpay/forged"}),
    )
    with pytest.raises(ApiError, match="支付签名"):
        payments.create_checkout(
            pilot_client.app.state.settings,
            "wechat",
            "pay_synthetic",
            "Synthetic",
            2990,
        )


def test_alipay_query_uses_signed_raw_response_and_post_credentials(
    pilot_client, monkeypatch
):
    result = (
        '{"code":"10000", "out_trade_no":"pay_synthetic", '
        '"trade_status":"TRADE_SUCCESS", "total_amount":"99.00"}'
    )

    def handle(request):
        assert str(request.url) == payments.ALIPAY_URL
        assert request.method == "POST"
        fields = dict(parse_qsl(request.content.decode()))
        signature = fields.pop("sign")
        payments._verify(
            "&".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(),
            signature,
            PUBLIC,
        )
        assert fields["method"] == "alipay.trade.query"
        assert json.loads(fields["biz_content"])["out_trade_no"] == "pay_synthetic"
        body = (
            '{"alipay_trade_query_response":'
            + result
            + ',"sign":'
            + json.dumps(sign(result.encode()))
            + "}"
        )
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    transport(monkeypatch, handle)
    assert (
        payments.query_payment(
            pilot_client.app.state.settings, "alipay", "pay_synthetic"
        )["total_amount"]
        == "99.00"
    )


def test_alipay_forged_query_does_not_pass_verification(pilot_client, monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "alipay_trade_query_response": {
                    "code": "10000",
                    "trade_status": "TRADE_SUCCESS",
                },
                "sign": sign(b"different response"),
            },
        ),
    )
    with pytest.raises(ApiError, match="支付签名"):
        payments.query_payment(
            pilot_client.app.state.settings, "alipay", "pay_synthetic"
        )


def test_alipay_certificate_mode_and_order_id_length(pilot_client):
    settings = pilot_client.app.state.settings
    settings.credit_alipay_mode = "cert"
    settings.credit_alipay_app_cert_sn = "synthetic-app-cert-sn"
    settings.credit_alipay_root_cert_sn = "synthetic-root-cert-sn"
    response = pilot_client.post(
        "/api/v1/workspaces/ws_test/credits/purchases",
        headers={"Idempotency-Key": "cert-test"},
        json={"package_id": "pack_small"},
    )
    assert response.status_code == 201
    order = response.json()
    assert len(order["id"]) <= 32
    fields = dict(parse_qsl(urlsplit(order["checkout_url"]).query))
    assert fields["app_cert_sn"] == "synthetic-app-cert-sn"
    assert fields["alipay_root_cert_sn"] == "synthetic-root-cert-sn"


def test_alipay_verifies_original_gbk_response_bytes(pilot_client, monkeypatch):
    result = (
        '{"code":"10000","out_trade_no":"pay_test",'
        '"trade_status":"TRADE_SUCCESS","total_amount":"99.00",'
        '"note":"真实渠道编码"}'
    )
    raw_result = result.encode("gb18030")
    body = (
        b'{"alipay_trade_query_response":'
        + raw_result
        + b',"sign":'
        + json.dumps(sign(raw_result)).encode()
        + b"}"
    )
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=body, headers={"Content-Type": "text/html; charset=gbk"}
        ),
    )
    assert (
        payments.query_payment(pilot_client.app.state.settings, "alipay", "pay_test")[
            "total_amount"
        ]
        == "99.00"
    )
