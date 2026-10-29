"""Haiyu's official Alipay Page Pay and WeChat API v3 Native protocols."""

import base64
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings
from .errors import ApiError

ALIPAY_URL = "https://openapi.alipay.com/gateway.do"
WECHAT_URL = "https://api.mch.weixin.qq.com"


def unavailable() -> ApiError:
    return ApiError(503, "PAYMENTS_UNAVAILABLE", "该支付方式暂未开放。")


def _public(pem: str) -> rsa.RSAPublicKey:
    raw = pem.encode()
    key = (
        x509.load_pem_x509_certificate(raw).public_key()
        if b"BEGIN CERTIFICATE" in raw
        else serialization.load_pem_public_key(raw)
    )
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("RSA key required")
    return key


def _sign(message: bytes, pem: str) -> str:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("RSA key required")
    return base64.b64encode(
        key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()


def _verify(message: bytes, signature: str, pem: str) -> None:
    try:
        _public(pem).verify(
            base64.b64decode(signature, validate=True),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        raise ApiError(400, "INVALID_PAYMENT_SIGNATURE", "支付签名无效。") from None


def _private(settings: Settings, channel: str) -> str:
    path = (
        settings.credit_alipay_private_key_path
        if channel == "alipay"
        else settings.credit_wechat_private_key_path
    )
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        raise unavailable() from None


def channels(settings: Settings) -> list[Literal["alipay", "wechat"]]:
    if not settings.credit_payments_enabled:
        return []
    result: list[Literal["alipay", "wechat"]] = []
    if (
        settings.credit_alipay_app_id
        and settings.credit_alipay_seller_id
        and settings.credit_alipay_public_key
        and settings.credit_alipay_private_key_path
        and (
            settings.credit_alipay_mode == "key"
            or (
                settings.credit_alipay_app_cert_sn
                and settings.credit_alipay_root_cert_sn
            )
        )
    ):
        result.append("alipay")
    if (
        settings.credit_wechat_app_id
        and settings.credit_wechat_mch_id
        and settings.credit_wechat_certificate_serial
        and settings.credit_wechat_public_keys
        and settings.credit_wechat_private_key_path
        and (
            len(settings.credit_wechat_api_v3_key.get_secret_value().encode()) == 32
            or (
                settings.credit_wechat_confirmation_mode == "query"
                and settings.credit_payment_reconciliation_enabled
            )
        )
    ):
        result.append("wechat")
    return result


def identity(settings: Settings, channel: str) -> dict[str, str]:
    if channel not in channels(settings):
        raise unavailable()
    if channel == "alipay":
        return {
            "app_id": settings.credit_alipay_app_id,
            "merchant_id": settings.credit_alipay_seller_id,
        }
    return {
        "app_id": settings.credit_wechat_app_id,
        "merchant_id": settings.credit_wechat_mch_id,
    }


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _alipay_parameters(settings: Settings, method: str, biz: dict) -> dict[str, str]:
    data = {
        "app_id": settings.credit_alipay_app_id,
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "version": "1.0",
        "biz_content": _json(biz),
    }
    if settings.credit_alipay_mode == "cert":
        data.update(
            app_cert_sn=settings.credit_alipay_app_cert_sn,
            alipay_root_cert_sn=settings.credit_alipay_root_cert_sn,
        )
    return data


def _alipay_sign(settings: Settings, data: dict[str, str]) -> None:
    data["sign"] = _sign(
        "&".join(
            f"{k}={v}" for k, v in sorted(data.items()) if k != "sign" and v != ""
        ).encode(),
        _private(settings, "alipay"),
    )


def verify_alipay(settings: Settings, fields: dict[str, str]) -> dict:
    if fields.get("sign_type") != "RSA2":
        raise ApiError(400, "INVALID_PAYMENT_SIGNATURE", "支付签名类型无效。")
    message = "&".join(
        f"{k}={v}"
        for k, v in sorted(fields.items())
        if k not in {"sign", "sign_type"} and v != ""
    )
    _verify(message.encode(), fields.get("sign", ""), settings.credit_alipay_public_key)
    return fields


def verify_wechat(settings: Settings, headers: Any, body: bytes) -> None:
    serial = headers.get("wechatpay-serial", "")
    timestamp = headers.get("wechatpay-timestamp", "")
    nonce = headers.get("wechatpay-nonce", "")
    key = settings.credit_wechat_public_keys.get(serial)
    if (
        not key
        or not timestamp.isdigit()
        or abs(time.time() - int(timestamp)) > 300
        or not nonce
        or len(nonce) > 128
    ):
        raise ApiError(400, "INVALID_PAYMENT_SIGNATURE", "支付签名或时间无效。")
    _verify(
        timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n",
        headers.get("wechatpay-signature", ""),
        key,
    )


def decrypt_wechat_event(
    settings: Settings, headers: Any, body: bytes, events: set[str]
) -> dict:
    verify_wechat(settings, headers, body)
    try:
        envelope = json.loads(body)
        if envelope["event_type"] not in events:
            raise ValueError("Unexpected event")
        resource = envelope["resource"]
        if resource["algorithm"] != "AEAD_AES_256_GCM":
            raise ValueError("Unexpected cipher")
        plain = AESGCM(
            settings.credit_wechat_api_v3_key.get_secret_value().encode()
        ).decrypt(
            resource["nonce"].encode(),
            base64.b64decode(resource["ciphertext"], validate=True),
            resource.get("associated_data", "").encode(),
        )
        return json.loads(plain)
    except Exception:
        raise ApiError(
            400, "INVALID_PAYMENT_CALLBACK", "微信支付通知无法解密。"
        ) from None


def decrypt_wechat(settings: Settings, headers: Any, body: bytes) -> dict:
    return decrypt_wechat_event(settings, headers, body, {"TRANSACTION.SUCCESS"})


def verify_wechat_query_notice(
    settings: Settings, headers: Any, body: bytes, events: set[str]
) -> None:
    """Authentic notification hint only; no encrypted payload can settle money."""
    verify_wechat(settings, headers, body)
    try:
        event = json.loads(body)
        if (
            not isinstance(event, dict)
            or event.get("event_type") not in events
            or not isinstance(event.get("resource"), dict)
            or event["resource"].get("algorithm") != "AEAD_AES_256_GCM"
        ):
            raise ValueError("Invalid notification envelope")
    except (ValueError, TypeError):
        raise ApiError(400, "INVALID_PAYMENT_CALLBACK", "微信通知格式无效。") from None


class PaymentProviderError(ApiError):
    def __init__(self, code: str, definitive: bool = False):
        safe_code = (
            code if re.fullmatch(r"[A-Za-z0-9_.-]{1,70}", code) else "PROVIDER_ERROR"
        )
        super().__init__(
            502, "PAYMENT_PROVIDER_ERROR", "支付渠道暂未完成处理，请核对状态。"
        )
        self.provider_code = safe_code
        self.definitive = definitive


def _wechat_request(
    settings: Settings, method: str, path: str, data: dict | None = None
) -> dict:
    body = _json(data) if data is not None else ""
    nonce, timestamp = secrets.token_hex(16), str(int(time.time()))
    signature = _sign(
        f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n".encode(),
        _private(settings, "wechat"),
    )
    auth = (
        "WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{settings.credit_wechat_mch_id}",nonce_str="{nonce}",'
        f'timestamp="{timestamp}",serial_no="{settings.credit_wechat_certificate_serial}",'
        f'signature="{signature}"'
    )
    with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
        response = client.request(
            method,
            WECHAT_URL + path,
            content=body.encode(),
            headers={
                "Authorization": auth,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
    if len(response.content) > 65536:
        raise ValueError("Oversized payment response")
    verify_wechat(settings, response.headers, response.content)
    data = response.json() if response.content else {}
    if response.is_error:
        code = data.get("code", "PROVIDER_ERROR")
        raise PaymentProviderError(
            code,
            code
            in {
                "PARAM_ERROR",
                "ORDER_NOT_EXIST",
                "NO_AUTH",
                "NOT_ENOUGH",
                "INVALID_REQUEST",
                "RESOURCE_NOT_EXISTS",
                "SIGN_ERROR",
            },
        )
    return data


def create_checkout(
    settings: Settings, channel: str, order_id: str, name: str, amount: int
) -> str:
    try:
        if channel == "alipay":
            data = _alipay_parameters(
                settings,
                "alipay.trade.page.pay",
                {
                    "out_trade_no": order_id,
                    "total_amount": f"{amount // 100}.{amount % 100:02d}",
                    "subject": name,
                    "product_code": "FAST_INSTANT_TRADE_PAY",
                    "timeout_express": "30m",
                },
            )
            data.update(
                notify_url=settings.credit_payment_public_base_url
                + "/api/v1/payments/alipay/notify",
                return_url=settings.credit_payment_public_base_url
                + "/account?purchase="
                + order_id,
            )
            _alipay_sign(settings, data)
            return ALIPAY_URL + "?" + urlencode(data)
        data = _wechat_request(
            settings,
            "POST",
            "/v3/pay/transactions/native",
            {
                "appid": settings.credit_wechat_app_id,
                "mchid": settings.credit_wechat_mch_id,
                "description": name,
                "out_trade_no": order_id,
                "notify_url": settings.credit_payment_public_base_url
                + "/api/v1/payments/wechat/notify",
                "amount": {"total": amount, "currency": "CNY"},
            },
        )
        url = data["code_url"]
        if not isinstance(url, str) or not url.startswith("weixin://wxpay/"):
            raise ValueError("Invalid WeChat code URL")
        return url
    except ApiError:
        raise
    except Exception:
        raise ApiError(
            502, "PAYMENT_CREATE_FAILED", "支付下单暂未完成，请重试。"
        ) from None


def query_payment(settings: Settings, channel: str, order_id: str) -> dict:
    try:
        if channel == "wechat":
            return _wechat_request(
                settings,
                "GET",
                "/v3/pay/transactions/out-trade-no/"
                + order_id
                + "?"
                + urlencode({"mchid": settings.credit_wechat_mch_id}),
            )
        result = _alipay_call(
            settings, "alipay.trade.query", {"out_trade_no": order_id}
        )
        return {**result, "app_id": settings.credit_alipay_app_id}
    except ApiError:
        raise
    except Exception:
        raise ApiError(
            502, "PAYMENT_QUERY_UNAVAILABLE", "支付渠道暂时无法查询，请稍后重试。"
        ) from None


def _alipay_call(settings: Settings, method: str, biz: dict) -> dict:
    data = _alipay_parameters(settings, method, biz)
    _alipay_sign(settings, data)
    with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
        response = client.post(ALIPAY_URL, data=data)
    response.raise_for_status()
    if len(response.content) > 65536:
        raise ValueError("Oversized payment response")
    encoding = "utf-8"
    try:
        raw = response.content.decode(encoding)
    except UnicodeDecodeError:
        encoding = "gb18030"
        raw = response.content.decode(encoding)
    parsed = json.loads(raw)
    key = method.replace(".", "_") + "_response"
    if key not in parsed:
        key = "error_response"
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*', raw)
    if not match:
        raise ValueError("Missing payment response")
    start = match.end()
    result, length = json.JSONDecoder().raw_decode(raw[start:])
    _verify(
        raw[start : start + length].encode(encoding),
        parsed["sign"],
        settings.credit_alipay_public_key,
    )
    if result.get("code") != "10000":
        code = result.get("sub_code", "PROVIDER_ERROR")
        raise PaymentProviderError(
            code,
            code
            in {
                "isv.invalid-parameter",
                "ACQ.TRADE_NOT_EXIST",
                "ACQ.TRADE_STATUS_ERROR",
                "ACQ.REFUND_AMT_NOT_EQUAL_TOTAL",
                "ACQ.REFUND_AMT_NOT_GREATER_THAN_ZERO",
                "ACQ.SELLER_BALANCE_NOT_ENOUGH",
                "ACQ.REFUND_AMT_LESS_THAN_COUPON_AMOUNT",
                "ACQ.REASON_TRADE_REFUND_FEE_ERR",
                "ACQ.REFUND_AMOUNT_EXCEED",
            },
        )
    return result


def _refund_amount(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]{1,2})?", value
    ):
        raise ValueError("Invalid refund amount")
    whole, _, fraction = value.partition(".")
    return int(whole) * 100 + int(fraction.ljust(2, "0") or "0")


def _refund_receipt(
    channel: str,
    data: dict,
    order_id: str,
    refund_id: str,
    amount_fen: int,
    total_fen: int,
) -> dict:
    if channel == "alipay":
        if (
            data.get("out_trade_no") != order_id
            or data.get("out_request_no") != refund_id
            or _refund_amount(data.get("refund_amount", "")) != amount_fen
        ):
            raise ValueError("Refund query receipt differs from the persisted intent")
        status = (
            "succeeded"
            if data.get("refund_status") == "REFUND_SUCCESS"
            else "processing"
        )
        return {"status": status, "provider_refund_id": refund_id}
    amount = data.get("amount", {})
    if (
        data.get("out_trade_no") != order_id
        or data.get("out_refund_no") != refund_id
        or type(amount.get("refund")) is not int
        or amount["refund"] != amount_fen
        or type(amount.get("total")) is not int
        or amount["total"] != total_fen
        or amount.get("currency", "CNY") != "CNY"
    ):
        raise ValueError("Refund receipt differs from the persisted intent")
    status = {"SUCCESS": "succeeded", "CLOSED": "failed"}.get(
        str(data.get("status", "")), "processing"
    )
    return {
        "status": status,
        "provider_refund_id": data.get("refund_id"),
        "error_code": "WECHAT_REFUND_ABNORMAL"
        if data.get("status") == "ABNORMAL"
        else None,
    }


def query_refund(
    settings: Settings,
    channel: str,
    order_id: str,
    refund_id: str,
    amount_fen: int,
    total_fen: int,
) -> dict:
    if channel == "wechat":
        try:
            data = _wechat_request(
                settings, "GET", "/v3/refund/domestic/refunds/" + refund_id
            )
        except PaymentProviderError as error:
            if error.provider_code == "RESOURCE_NOT_EXISTS":
                return {"status": "not_found"}
            raise
    else:
        try:
            data = _alipay_call(
                settings,
                "alipay.trade.fastpay.refund.query",
                {"out_trade_no": order_id, "out_request_no": refund_id},
            )
        except PaymentProviderError as error:
            if error.provider_code in {
                "ACQ.TRADE_NOT_EXIST",
                "ACQ.REFUND_NOT_EXIST",
                "ACQ.REFUND_DETAIL_NOT_EXIST",
            }:
                return {"status": "not_found", "provider_refund_id": None}
            raise
    return _refund_receipt(channel, data, order_id, refund_id, amount_fen, total_fen)


def create_refund(
    settings: Settings,
    channel: str,
    order_id: str,
    refund_id: str,
    amount_fen: int,
    total_fen: int,
    reason: str,
) -> dict:
    if channel == "wechat":
        data = _wechat_request(
            settings,
            "POST",
            "/v3/refund/domestic/refunds",
            {
                "out_trade_no": order_id,
                "out_refund_no": refund_id,
                "reason": reason,
                "notify_url": settings.credit_payment_public_base_url
                + "/api/v1/payments/wechat/refund-notify",
                "amount": {"refund": amount_fen, "total": total_fen, "currency": "CNY"},
            },
        )
        return _refund_receipt(
            channel, data, order_id, refund_id, amount_fen, total_fen
        )
    result = _alipay_call(
        settings,
        "alipay.trade.refund",
        {
            "out_trade_no": order_id,
            "out_request_no": refund_id,
            "refund_amount": f"{amount_fen // 100}.{amount_fen % 100:02d}",
            "refund_reason": reason,
        },
    )
    if (
        result.get("out_trade_no") != order_id
        or _refund_amount(result.get("refund_fee", "")) != amount_fen
    ):
        raise ValueError("Refund acknowledgment differs from persisted intent")
    # Query later: a signed acknowledgment is not the final refund outcome.
    return {"status": "processing", "provider_refund_id": refund_id}
