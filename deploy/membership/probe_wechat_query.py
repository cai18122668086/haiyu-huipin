"""Run in the candidate image: create/query/close one unexposed, unpaid test QR."""

import json
from uuid import uuid4

from app.config import Settings

from app import haiyu_payments as payments

settings = Settings(
    _env_file="/run/huipin-payments/payments.env",
    environment="test",
    credit_wechat_confirmation_mode="query",
    credit_payment_reconciliation_enabled=True,
)
order_id = "hpq_" + uuid4().hex[:28]
assert len(order_id) <= 32
assert "wechat" in payments.channels(settings)
assert not settings.credit_wechat_api_v3_key.get_secret_value()
report = {"api_v3_key_used": False, "actual_payment_executed": False}
created = False
try:
    code = payments.create_checkout(
        settings, "wechat", order_id, "海宇绘品微信接入验证（不付款）", 1
    )
    created = True
    assert code.startswith("weixin://wxpay/")
    data = payments.query_payment(settings, "wechat", order_id)
    assert data["out_trade_no"] == order_id
    assert data["appid"] == settings.credit_wechat_app_id
    assert data["mchid"] == settings.credit_wechat_mch_id
    assert data["amount"]["total"] == 1 and data["amount"]["currency"] == "CNY"
    assert data["trade_state"] == "NOTPAY"
    report.update(
        {
            "native_qr_created": True,
            "signed_query_verified": True,
            "merchant_app_amount_verified": True,
            "initial_state": "NOTPAY",
        }
    )
finally:
    if created:
        payments._wechat_request(
            settings,
            "POST",
            "/v3/pay/transactions/out-trade-no/" + order_id + "/close",
            {"mchid": settings.credit_wechat_mch_id},
        )
        data = payments.query_payment(settings, "wechat", order_id)
        assert data["out_trade_no"] == order_id and data["trade_state"] == "CLOSED"
        report["probe_order_closed"] = True
print(json.dumps(report))
