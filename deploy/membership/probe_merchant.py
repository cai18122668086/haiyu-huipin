"""Read-only verification of merchant signing; never creates a payment order."""

import json
import re

import httpx

from app import haiyu_payments as payments
from app.config import Settings

settings = Settings(_env_file="/run/huipin-payments/payments.env", environment="test")
data = payments._alipay_parameters(settings, "alipay.trade.query", {
    "out_trade_no": "huipin_configuration_probe_no_order"
})
payments._alipay_sign(settings, data)
with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
    response = client.post(payments.ALIPAY_URL, data=data)
response.raise_for_status()
response_encoding = "utf-8"
try:
    raw = response.content.decode("utf-8")
except UnicodeDecodeError:
    response_encoding = "gb18030"
    raw = response.content.decode("gb18030")
parsed = json.loads(raw)
if "sign" not in parsed:
    detail = parsed.get("alipay_trade_query_response", {})
    print(json.dumps({"authenticated": False, "code": detail.get("code"), "sub_code": detail.get("sub_code"), "content_type": response.headers.get("content-type")}))
    raise SystemExit("Unsigned gateway error; activation remains disabled")
match = re.search(r'"alipay_trade_query_response"\s*:\s*', raw)
if not match:
    raise SystemExit("Alipay returned an unexpected response format")
start = match.end()
result, length = json.JSONDecoder().raw_decode(raw[start:])
payments._verify(raw[start:start + length].encode(response_encoding), parsed["sign"],
                 settings.credit_alipay_public_key)
report = {"operation": "query_nonexistent_order", "real_order_created": False,
          "response_signature_valid": True, "code": result.get("code"),
          "sub_code": result.get("sub_code"), "configured_channels": payments.channels(settings)}
print(json.dumps(report))
if result.get("sub_code") != "ACQ.TRADE_NOT_EXIST":
    raise SystemExit("Merchant configuration needs review before activation")
