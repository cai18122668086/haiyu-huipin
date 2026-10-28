"""Small OpenAI-compatible image edits client for relay APIs."""

from __future__ import annotations

import base64
import mimetypes
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from PIL import Image

from xinghe_usage import normalize_token_usage


class ImageAPIError(RuntimeError):
    pass


def inspect_image_file(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            return {
                "format": image.format,
                "width": image.width,
                "height": image.height,
            }
    except Exception as exc:
        raise ImageAPIError(f"输出文件不是可识别的图像：{exc}") from exc


class XingheImageClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: int = 600):
        if not api_key.strip():
            raise ValueError("API 密钥不能为空")
        self.base_url = base_url.rstrip("/") + "/"
        self.model = model.strip() or "gpt-image-2"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key.strip()}"})

    @property
    def edits_url(self) -> str:
        return urljoin(self.base_url, "v1/images/edits")

    def edit(self, **kwargs: Any) -> Path:
        path, _ = self.edit_with_metadata(**kwargs)
        return path

    def edit_with_metadata(
        self,
        *,
        image_bytes: bytes | None = None,
        image_name: str | None = None,
        reference_images: Sequence[tuple[str, bytes]] | None = None,
        prompt: str,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "high",
        output_format: str = "jpeg",
        output_compression: int = 92,
        image_field: str = "image",
        retries: int = 1,
    ) -> tuple[Path, dict[str, Any]]:
        references = self._normalize_references(
            image_bytes=image_bytes,
            image_name=image_name,
            reference_images=reference_images,
        )
        data = {
            "model": self.model,
            "prompt": prompt,
            "n": "1",
            "size": size,
            "quality": quality,
            "output_format": output_format,
            "output_compression": str(output_compression),
            "response_format": "b64_json",
        }
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                files = [
                    (
                        image_field,
                        (
                            reference_name,
                            reference_bytes,
                            mimetypes.guess_type(reference_name)[0] or "image/png",
                        ),
                    )
                    for reference_name, reference_bytes in references
                ]
                response = self.session.post(
                    self.edits_url,
                    data=data,
                    files=files,
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    summary = response.text[:1000]
                    raise ImageAPIError(f"HTTP {response.status_code}: {summary}")
                payload = response.json()
                result = self._save_response(payload, output_path)
                if not result.exists() or result.stat().st_size == 0:
                    raise ImageAPIError("接口返回成功，但输出文件不存在或为空")
                return result, normalize_token_usage(payload.get("usage"))
            except (requests.RequestException, ValueError, ImageAPIError) as exc:
                last_error = exc
                transient = isinstance(exc, requests.RequestException) or "HTTP 429" in str(exc) or "HTTP 5" in str(exc)
                if attempt >= retries or not transient:
                    break
                time.sleep(2**attempt)

        raise ImageAPIError(str(last_error) if last_error else "未知图像接口错误")

    @staticmethod
    def _normalize_references(
        *,
        image_bytes: bytes | None,
        image_name: str | None,
        reference_images: Sequence[tuple[str, bytes]] | None,
    ) -> list[tuple[str, bytes]]:
        if reference_images is not None:
            references = [(str(name), data) for name, data in reference_images]
        elif image_bytes is not None and image_name:
            references = [(image_name, image_bytes)]
        else:
            references = []
        if not references:
            raise ValueError("至少需要一张参考图")
        for index, (name, data) in enumerate(references, start=1):
            if not name or not isinstance(data, bytes) or not data:
                raise ValueError(f"第 {index} 张参考图无效")
        return references

    def _save_response(self, payload: dict[str, Any], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        item: dict[str, Any] = payload
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            item = data[0]

        encoded = item.get("b64_json") or item.get("base64") or item.get("image_base64")
        if encoded:
            try:
                output_path.write_bytes(base64.b64decode(encoded))
            except Exception as exc:
                raise ImageAPIError(f"无法解码接口返回的 base64 图像：{exc}") from exc
            return output_path

        url = item.get("url") or item.get("image_url")
        if url:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            output_path.write_bytes(response.content)
            return output_path

        task_id = payload.get("task_id") or payload.get("id")
        if task_id:
            raise ImageAPIError(
                f"中转站返回了异步任务 ID（{task_id}），但未同时返回图像或轮询地址；请按该站文档补充轮询接口。"
            )
        raise ImageAPIError(f"无法识别接口响应格式，顶层字段：{', '.join(payload.keys())}")
