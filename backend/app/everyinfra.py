"""EveryInfra 商品取图协议；付费提交先持久化，重试只恢复已有任务。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .errors import ApiError
from .product_sources import SOURCES, image_url

BASE_URL = "https://api.everyinfra.com"
POLL_TIMEOUT = 600
POLL_INTERVAL = 2
Checkpoint = Callable[[dict[str, Any]], None]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        # Bearer 凭据只发送到固定 API 主机，不能随上游跳转转发。
        return None


def _request(token: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    request = Request(
        BASE_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=180 if body and body.get("mode") == "sync" else 45
        ) as response:
            status, payload = response.status, response.read(32 * 1024 * 1024 + 1)
    except HTTPError as error:
        status, payload = error.code, error.read(1024 * 1024)
    except (URLError, TimeoutError, OSError):
        raise ApiError(
            502,
            "PRODUCT_PROVIDER_REQUEST_UNKNOWN",
            "商品采集请求暂未确认，请稍后核对或重试。",
        ) from None
    try:
        value = json.loads(payload) if len(payload) <= 32 * 1024 * 1024 else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise ApiError(
            502, "PRODUCT_DATA_INVALID", "商品采集服务未返回有效数据，请稍后重试。"
        )
    return status, value


def _provider_error(status: int) -> ApiError:
    messages = {
        401: "商品采集服务凭据无效，请联系管理员。",
        402: "商品采集服务余额不足，请联系管理员。",
        403: "该商品采集接口未开通，请联系管理员。",
        422: "商品链接暂不受支持，请检查来源平台和完整链接。",
        429: "商品采集服务繁忙，请稍后重试。",
    }
    return ApiError(
        502,
        f"PRODUCT_PROVIDER_{status}",
        messages.get(status, "商品暂时获取失败，请稍后重试或上传参考图。"),
    )


def _images(value: object) -> list[str]:
    if isinstance(value, str):
        return [url] if (url := image_url(value)) else []
    if isinstance(value, list):
        return [url for part in value for url in _images(part)]
    if isinstance(value, Mapping):
        for key in ("image_url", "image", "src", "url"):
            if key in value and (urls := _images(value[key])):
                return urls
    return []


def _variant_images(value: object, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    if isinstance(value, list):
        return [url for item in value for url in _variant_images(item, depth + 1)]
    if isinstance(value, Mapping):
        images = []
        for key, child in value.items():
            if key in {"image_url", "image", "images", "imageUrl"}:
                images.extend(_images(child))
            elif isinstance(child, (dict, list)):
                images.extend(_variant_images(child, depth + 1))
        return images
    return []


def normalize_product(
    body: object, platform: str, item_id: str, canonical: str
) -> dict:
    """只提取商品图和规格图，不将卖家头像、评论或推荐商品混入主图。"""
    source = SOURCES[platform]
    expected = item_id.split(".")[-1] if platform == "shopee" else item_id
    if isinstance(body, list):
        body = next(
            (
                row
                for row in body
                if isinstance(row, dict)
                and any(
                    str(row.get(k, "")).upper() == expected.upper()
                    for k in source.identity_fields
                )
            ),
            None,
        )
    if not isinstance(body, Mapping):
        raise ApiError(
            502,
            "PRODUCT_DATA_INVALID",
            "未获取到有效商品信息，请更换链接或上传参考图。",
        )
    actual = next(
        (str(body[k]) for k in source.identity_fields if body.get(k) is not None), ""
    )
    shared_spu = (
        platform == "dewu" and item_id.startswith("share:") and actual.isdigit()
    )
    if not actual or (not shared_spu and actual.upper() != expected.upper()):
        raise ApiError(
            502, "PRODUCT_ID_MISMATCH", "采集服务返回的商品与链接不一致，请稍后重试。"
        )
    main = (
        _images(body.get("images"))
        + _images(body.get("image_urls"))
        + _images(body.get("main_image_url"))
        + _images(body.get("image_url"))
    )
    detail = _images(body.get("description_images"))
    props = body.get("props_images")
    sku = _images(list(props.values()) if isinstance(props, Mapping) else props)
    for name in ("skus", "variants", "options"):
        sku.extend(_variant_images(body.get(name)))
    html = body.get("description_html")
    if isinstance(html, str):
        detail.extend(
            _images(re.findall(r"""(?:src|data-src)=["']([^"']+)["']""", html))
        )
    main, detail, sku = (list(dict.fromkeys(group)) for group in (main, detail, sku))
    if not main and not detail and not sku:
        raise ApiError(
            502,
            "PRODUCT_IMAGES_MISSING",
            "暂未获取到商品图片，请更换链接或上传参考图。",
        )
    return {
        "platform": platform,
        "numIid": actual,
        "sourceUrl": canonical,
        "targetPlatform": source.target,
        "title": body.get("title") or body.get("name"),
        "price": body.get("price") or body.get("price_min"),
        "shopName": body.get("shop_name")
        or body.get("store_name")
        or body.get("supplier_name"),
        "imageUrls": main,
        "descImageUrls": detail,
        "skuImageUrls": sku,
        "supplier": "everyinfra",
        "endpoint": f"{source.provider_platform}/{source.action}",
    }


class ProductClient:
    def __init__(self, token: str, state: dict, checkpoint: Checkpoint):
        self.token, self.state, self.checkpoint = token, state, checkpoint

    def _save(self) -> None:
        self.checkpoint(deepcopy(self.state))

    def action(
        self, platform: str, action: str, params: dict, *, sync: bool = False
    ) -> object:
        key = f"{platform}/{action}"
        entry = self.state.get(key, {})
        if entry.get("status") == "succeeded":
            return entry["results"]
        if entry.get("status") == "submitting":
            raise ApiError(
                503,
                "PRODUCT_PROVIDER_REQUEST_UNKNOWN",
                "上次商品采集请求正在核对，请稍后再试。",
            )
        if entry.get("job_id") and entry.get("status") != "failed":
            wire = entry
        else:
            entry = {"status": "submitting"}
            self.state[key] = entry
            self._save()
            status, wire = _request(
                self.token,
                "/api/v1/social",
                {
                    "platform": platform,
                    "action": action,
                    "params": params,
                    "mode": "sync" if sync else "async",
                },
            )
            if status >= 400:
                entry.update(status="failed", http_status=status)
                self._save()
                raise _provider_error(status)
            billing = wire.get("billing", {})
            entry = {
                "status": wire.get("status", "received"),
                "request_id": wire.get("id"),
                "billing": {
                    k: billing[k]
                    for k in ("charged_usd", "credits", "units", "refunded_credits")
                    if k in billing
                },
            }
            if wire.get("job_id"):
                entry["job_id"] = wire["job_id"]
            self.state[key] = entry
            self._save()
        deadline = time.monotonic() + POLL_TIMEOUT
        while "results" not in wire:
            job_id = entry.get("job_id")
            if not isinstance(job_id, str) or not re.fullmatch(
                r"job_[A-Za-z0-9_-]+", job_id
            ):
                entry["status"] = "failed"
                self._save()
                raise ApiError(
                    502,
                    "PRODUCT_DATA_INVALID",
                    "商品采集服务未返回有效任务，请稍后重试。",
                )
            if time.monotonic() >= deadline:
                raise ApiError(
                    504,
                    "PRODUCT_PROVIDER_PENDING",
                    "商品采集仍在处理中，稍后重试会继续查询原任务。",
                )
            # 每次轮询也续约；进程中断后只恢复 job_id，不重新提交付费请求。
            self._save()
            status, wire = _request(self.token, "/api/v1/jobs/" + job_id)
            if status >= 400:
                raise _provider_error(status)
            entry["status"] = wire.get("status", "running")
            if entry["status"] in {"failed", "cancelled", "refunded", "expired"}:
                self._save()
                raise _provider_error(503)
            if "results" not in wire:
                time.sleep(POLL_INTERVAL)
        if wire["results"] is None or wire["results"] == []:
            entry["status"] = "failed"
            self._save()
            raise ApiError(
                502,
                "PRODUCT_DATA_INVALID",
                "暂未获取到商品信息，请稍后重试或上传参考图。",
            )
        entry.update(status="succeeded", results=wire["results"])
        self._save()
        return wire["results"]


def fetch_product(
    token: str,
    platform: str,
    item_id: str,
    canonical: str,
    *,
    state: dict | None = None,
    on_checkpoint: Checkpoint | None = None,
) -> dict:
    if not token:
        raise ApiError(
            503,
            "PRODUCT_PROVIDER_NOT_CONFIGURED",
            "商品采集服务正在配置，请先上传参考图。",
        )
    state = state if state is not None else {}
    client = ProductClient(token, state, on_checkpoint or (lambda value: None))
    source = SOURCES[platform]
    params: dict[str, Any] = {"url": canonical}
    if platform == "jd":
        params = {"item_id": item_id}
    elif platform == "xianyu":
        params = {"urls": [canonical]}
    elif platform == "dewu":
        params = {"url": canonical if item_id.startswith("share:") else item_id}
    body = client.action(source.provider_platform, source.action, params)
    product = normalize_product(body, platform, item_id, canonical)
    if platform == "jd":
        # 详情长图和 SKU 是独立接口；补充调用失败不丢弃已取得的商品图片。
        for action in ("description_images", "skus"):
            try:
                rows = client.action(
                    "jd", action, {"item_id": item_id}, sync=action == "skus"
                )
            except ApiError:
                continue
            if isinstance(rows, list):
                field = (
                    "descImageUrls"
                    if action == "description_images"
                    else "skuImageUrls"
                )
                product[field] = list(dict.fromkeys(product[field] + _images(rows)))
    # 只输出已知实扣金额；不把缺少回执的调用误标为免费。
    fees = [entry.get("billing", {}).get("charged_usd") for entry in state.values()]
    product["costUsd"] = (
        float(sum(Decimal(str(fee)) for fee in fees if fee is not None))
        if fees and all(fee is not None for fee in fees)
        else None
    )
    return product
