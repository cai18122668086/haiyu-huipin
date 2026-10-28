"""Official-channel contract tests; all keys and transactions are synthetic."""

import base64
import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app import haiyu_payments as payments
from app.main import create_app
from app.models import (
    CreditLedgerEntryRecord,
    CreditPurchaseRecord,
    WorkspaceCreditAccountRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)

WORKSPACE = "ws_test"
OTHER = "ws_not_in_pilot"
BASE = f"/api/v1/workspaces/{WORKSPACE}/credits"
NOTIFY = "/api/v1/payments/alipay/notify"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC = (
    KEY.public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode()
)
V3KEY = "0123456789abcdef0123456789abcdef"


def sign(raw):
    return base64.b64encode(KEY.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()


@pytest.fixture
def pilot_client(settings, tmp_path, monkeypatch):
    settings.workspace_credit_enforcement_enabled = False
    settings.workspace_credit_pilot_ids = [WORKSPACE]
    settings.provider_quota_enabled = False
    settings.credit_payments_enabled = True
    settings.credit_alipay_app_id = "test-app"
    settings.credit_alipay_seller_id = "test-seller"
    settings.credit_alipay_public_key = PUBLIC
    private_path = tmp_path / "test.pem"
    private_path.write_text(PRIVATE)
    settings.credit_alipay_private_key_path = str(private_path)
    settings.credit_wechat_app_id = "test-wx-app"
    settings.credit_wechat_mch_id = "test-mch"
    settings.credit_wechat_certificate_serial = "test-serial"
    settings.credit_wechat_private_key_path = str(private_path)
    settings.credit_wechat_public_keys = {"test-public-id": PUBLIC}
    settings.credit_wechat_api_v3_key = SecretStr(V3KEY)
    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as session:
            session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits = 0
            session.add(
                WorkspaceRecord(
                    id=OTHER, name="Non-pilot", created_by_user_id="usr_test"
                )
            )
            session.flush()
            session.add_all(
                [
                    WorkspaceMemberRecord(
                        workspace_id=OTHER,
                        user_id="usr_test",
                        role="owner",
                        status="active",
                    ),
                    WorkspaceCreditAccountRecord(
                        workspace_id=OTHER, balance_credits=0, reserved_credits=0
                    ),
                ]
            )
            session.commit()
        yield client


def purchase(client, key=None, channel="alipay", product="credits_1500"):
    response = client.post(
        BASE + "/purchases",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={"package_id": product, "channel": channel},
    )
    assert response.status_code == 201, response.text
    return response.json()


def paid_fields(order, *, trade="test-trade-001", **changes):
    fields = {
        "app_id": "test-app",
        "seller_id": "test-seller",
        "out_trade_no": order["id"],
        "trade_no": trade,
        "total_amount": "99.00",
        "trade_status": "TRADE_SUCCESS",
    }
    if "money" in changes:
        changes["total_amount"] = changes.pop("money")
    fields.update(changes)
    fields["sign"] = sign(
        "&".join(f"{k}={v}" for k, v in sorted(fields.items()) if v != "").encode()
    )
    fields["sign_type"] = "RSA2"
    return fields


def balance(client):
    return client.get(BASE).json()["balance_credits"]


def wx_notice(order, **changes):
    transaction = {
        "appid": "test-wx-app",
        "mchid": "test-mch",
        "out_trade_no": order["id"],
        "transaction_id": "wx-trade-001",
        "trade_state": "SUCCESS",
        "amount": {"total": order["amount_fen"], "currency": "CNY"},
    }
    transaction.update(changes)
    nonce, aad = "123456789012", "transaction"
    encrypted = AESGCM(V3KEY.encode()).encrypt(
        nonce.encode(), json.dumps(transaction).encode(), aad.encode()
    )
    body = json.dumps(
        {
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": nonce,
                "associated_data": aad,
                "ciphertext": base64.b64encode(encrypted).decode(),
            },
        }
    ).encode()
    stamp = str(int(time.time()))
    headers = {
        "Wechatpay-Serial": "test-public-id",
        "Wechatpay-Timestamp": stamp,
        "Wechatpay-Nonce": "test-nonce",
        "Content-Type": "application/json",
    }
    headers["Wechatpay-Signature"] = sign(
        stamp.encode() + b"\n" + b"test-nonce\n" + body + b"\n"
    )
    return body, headers


def test_signed_checkout_idempotency_and_browser_return_cannot_credit(pilot_client):
    key = str(uuid4())
    order = purchase(pilot_client, key)
    assert purchase(pilot_client, key) == order
    parts = urlsplit(order["checkout_url"])
    assert parts.hostname == "openapi.alipay.com"
    fields = dict(parse_qsl(parts.query))
    signature = fields.pop("sign")
    payments._verify(
        "&".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(),
        signature,
        PUBLIC,
    )
    assert json.loads(fields["biz_content"])["total_amount"] == "99.00"
    assert fields["notify_url"].startswith("https://huipin.haiyushuke.com/")
    assert "PRIVATE KEY" not in order["checkout_url"]
    assert balance(pilot_client) == 0
    assert (
        pilot_client.get(
            BASE + "/purchases/" + order["id"], params={"trade_status": "TRADE_SUCCESS"}
        ).json()["status"]
        == "pending"
    )
    assert balance(pilot_client) == 0
    assert pilot_client.get(NOTIFY).status_code == 405
    assert pilot_client.get("/api/v1/payments/epay/notify").status_code == 404


def test_duplicate_callback_is_atomic_and_survives_disable(pilot_client):
    order = purchase(pilot_client)
    pilot_client.app.state.settings.credit_payments_enabled = False
    for _ in range(3):
        r = pilot_client.post(NOTIFY, data=paid_fields(order))
        assert r.status_code == 200, r.text
        assert r.text == "success"
    assert balance(pilot_client) == 1500
    with pilot_client.app.state.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(CreditLedgerEntryRecord))
            == 1
        )


@pytest.mark.parametrize(
    "changes",
    [{"money": "0.01"}, {"seller_id": "other"}, {"app_id": "other"}, {"trade_no": ""}],
)
def test_signed_wrong_receipt_rejected(pilot_client, changes):
    order = purchase(pilot_client)
    r = pilot_client.post(NOTIFY, data=paid_fields(order, **changes))
    assert r.status_code == 400
    assert balance(pilot_client) == 0


def test_tamper_duplicate_fields_and_channel_conflict_rejected(pilot_client):
    key = str(uuid4())
    order = purchase(pilot_client, key)
    fields = paid_fields(order)
    fields["total_amount"] = "1.00"
    assert pilot_client.post(NOTIFY, data=fields).status_code == 400
    assert (
        pilot_client.post(
            NOTIFY,
            content=urlencode(paid_fields(order)) + "&total_amount=99.00",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 400
    )
    assert (
        pilot_client.post(
            BASE + "/purchases",
            headers={"Idempotency-Key": key},
            json={"package_id": "credits_1500", "channel": "wechat"},
        ).status_code
        == 409
    )
    assert balance(pilot_client) == 0


def test_trade_cannot_be_reused_across_orders(pilot_client):
    first, second = purchase(pilot_client), purchase(pilot_client)
    assert pilot_client.post(NOTIFY, data=paid_fields(first)).status_code == 200
    assert pilot_client.post(NOTIFY, data=paid_fields(second)).status_code == 409
    assert balance(pilot_client) == 1500


def test_wallet_error_rolls_back_payment(pilot_client, monkeypatch):
    from app import credit_payment_routes as routes

    order = purchase(pilot_client)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic ledger failure")

    monkeypatch.setattr(routes, "grant", fail)
    with pytest.raises(RuntimeError):
        pilot_client.post(NOTIFY, data=paid_fields(order))
    with pilot_client.app.state.session_factory() as session:
        assert session.get(CreditPurchaseRecord, order["id"]).status == "pending"
    assert balance(pilot_client) == 0


def test_wechat_encrypted_callback_and_replay(pilot_client, monkeypatch):
    monkeypatch.setattr(
        payments,
        "_wechat_request",
        lambda *args, **kwargs: {"code_url": "weixin://wxpay/test"},
    )
    order = purchase(pilot_client, channel="wechat", product="pack_small")
    assert order["checkout_url"] is None
    assert order["code_url"].startswith("weixin://")
    body, headers = wx_notice(order)
    for _ in range(2):
        r = pilot_client.post(
            "/api/v1/payments/wechat/notify", content=body, headers=headers
        )
        assert r.status_code == 204, r.text
    assert balance(pilot_client) == 300


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": {"total": 1, "currency": "CNY"}},
        {"mchid": "wrong"},
        {"appid": "wrong"},
        {"amount": {"total": 2990, "currency": "USD"}},
    ],
)
def test_wechat_wrong_identity_or_amount_rejected(pilot_client, monkeypatch, changes):
    monkeypatch.setattr(
        payments, "_wechat_request", lambda *a, **k: {"code_url": "weixin://wxpay/test"}
    )
    order = purchase(pilot_client, channel="wechat", product="pack_small")
    body, headers = wx_notice(order, **changes)
    assert (
        pilot_client.post(
            "/api/v1/payments/wechat/notify", content=body, headers=headers
        ).status_code
        == 400
    )
    assert balance(pilot_client) == 0


def test_wechat_stale_or_tampered_notice_rejected(pilot_client, monkeypatch):
    monkeypatch.setattr(
        payments, "_wechat_request", lambda *a, **k: {"code_url": "weixin://wxpay/test"}
    )
    order = purchase(pilot_client, channel="wechat")
    body, headers = wx_notice(order)
    assert (
        pilot_client.post(
            "/api/v1/payments/wechat/notify", content=body + b" ", headers=headers
        ).status_code
        == 400
    )
    headers["Wechatpay-Timestamp"] = "1"
    assert (
        pilot_client.post(
            "/api/v1/payments/wechat/notify", content=body, headers=headers
        ).status_code
        == 400
    )
    assert balance(pilot_client) == 0


def test_unconfigured_payments_show_catalog_but_block_purchase(pilot_client):
    pilot_client.app.state.settings.credit_payments_enabled = False
    catalog = pilot_client.get(BASE + "/packages?include_memberships=true").json()
    assert not catalog["enabled"] and catalog["channels"] == []
    assert len(catalog["packages"]) == 9
    assert (
        pilot_client.post(
            BASE + "/purchases",
            headers={"Idempotency-Key": str(uuid4())},
            json={"package_id": "pack_small"},
        ).status_code
        == 503
    )
