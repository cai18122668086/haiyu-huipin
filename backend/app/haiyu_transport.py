"""Haiyu's one-reference, asynchronous image API at the transport boundary.

Original assets and workflow contracts remain in the existing job/storage layer.
Only the provider wire format is adapted here; no request is replayed after an
unknown outcome. See https://www.haiyushuke.com/docs/guides/image.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import math
import socket
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps

from .haiyu_recovery import (
    MAX_DOWNLOAD_ATTEMPTS,
    MAX_POLL_ERRORS,
    MAX_RATE_LIMIT_RETRIES,
    MAX_URL_REFRESHES,
    no_acceptance_rate_limit,
    refreshable_url,
    retry_after,
    transient_download,
)

MAX_IMAGE_BYTES = 32 * 1024 * 1024


def _error(message: str, task_id: str | None = None) -> Exception:
    from .xinghe_agent import XingheAgentError

    # This code is classified as outcome unknown by the existing gateway.
    return XingheAgentError(
        "PROVIDER_RESPONSE_INVALID", message, provider_request_id=task_id
    )


def _public_url(url: str, allowed_hosts: tuple[str, ...] = ()) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise _error("Image result URL must be public HTTPS.")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    resolved = [ipaddress.ip_address(item[4][0]) for item in addresses]
    # Clash can return benchmark-range synthetic addresses for public CDN
    # domains. Accept that only for an exact operator-provisioned hostname
    # through the system HTTPS proxy; never allow ordinary private addresses.
    fake_ranges = (
        ipaddress.ip_network("198.18.0.0/15"),
        ipaddress.ip_network("2001:2::/48"),
    )
    proxy_dns = (
        parsed.hostname in allowed_hosts
        and bool(getproxies().get("https"))
        and all(
            any(ip.version == net.version and ip in net for net in fake_ranges)
            for ip in resolved
        )
    )
    if not resolved or (not proxy_dns and any(not ip.is_global for ip in resolved)):
        raise _error("Image result URL resolved to a non-public address.")


class _PublicRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Request | None:
        _public_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_result(
    url: str, timeout: float, allowed_hosts: tuple[str, ...] = ()
) -> bytes:
    _public_url(url, allowed_hosts)
    # Never attach the provider's Authorization header to an artifact/CDN URL.
    with build_opener(_PublicRedirect(allowed_hosts)).open(
        Request(url), timeout=timeout
    ) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise _error("Image result exceeded the download limit.")
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > 40_000_000:
            raise _error("Image result exceeded the pixel limit.")
        image.verify()
    return data


def pack_references(files: Sequence[tuple[str, str, str, bytes]]) -> tuple[list, str]:
    """Retain every source in a numbered sheet; never silently drop references."""
    images = [item for item in files if item[0] == "image"]
    masks = [item for item in files if item[0] == "mask"]
    if len(images) == 1 and not masks and len(images[0][3]) <= 10 * 1024 * 1024:
        return images, ""
    panels: list[Image.Image] = []
    labels: list[str] = []
    for _, name, _, data in images:
        with Image.open(BytesIO(data)) as source_image:
            panels.append(ImageOps.exif_transpose(source_image).convert("RGB"))
        labels.append(name)
    if masks and panels:
        # API mask alpha=0 denotes the authorized area. Make it visible in an
        # extra reference panel; the existing server compositor still enforces
        # the exact delivery mask and rejects failed visual reviews.
        with Image.open(BytesIO(masks[0][3])) as source_mask:
            mask = source_mask.convert("RGBA")
            alpha = ImageOps.invert(mask.getchannel("A"))
            if alpha.getbbox() is None:
                alpha = ImageOps.invert(mask.convert("L"))
            alpha = alpha.resize(panels[0].size)
        overlay = Image.new("RGB", panels[0].size, "#a977e7")
        marked = Image.blend(panels[0], overlay, 0.55)
        panels.append(Image.composite(marked, panels[0], alpha))
        labels.append("EDIT MAP: purple marks the only editable region")
    if not panels:
        raise _error("Image edit requires at least one reference image.")
    columns = min(3, math.ceil(math.sqrt(len(panels))))
    rows = math.ceil(len(panels) / columns)
    cell = min(1024, 3072 // max(columns, rows))
    sheet = Image.new("RGB", (columns * cell, rows * (cell + 30)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(panels):
        thumb = ImageOps.contain(image, (cell - 16, cell - 16))
        x, y = (index % columns) * cell, (index // columns) * (cell + 30)
        draw.text((x + 8, y + 8), f"REFERENCE {index + 1}", fill="black")
        sheet.paste(
            thumb, (x + (cell - thumb.width) // 2, y + 30 + (cell - thumb.height) // 2)
        )
    output = BytesIO()
    sheet.save(output, format="JPEG", quality=94)
    note = (
        "\nREFERENCE SHEET (input evidence only, never reproduce this grid):\n"
        "This one attachment contains numbered panels. Every 'image N', "
        "'reference image N' and IMAGE_BINDINGS entry in the prompt refers "
        "to panel REFERENCE N inside this sheet, not to a separate attachment. "
        "Follow those roles: composition_reference controls layout and "
        "presence/absence; product_original and product_detail supply product "
        "identity only. Do not insert packaging into a context-only scene.\n"
    )
    note += "\n".join(f"Reference {i + 1}: {label}" for i, label in enumerate(labels))
    note += (
        "\nProduce one final image, using all relevant evidence. "
        "Preserve the product identity."
    )
    if masks:
        note += (
            " Modify only the purple area shown in the EDIT MAP; "
            "do not render the purple overlay."
        )
    return [("image", "references.jpg", "image/jpeg", output.getvalue())], note


class HaiyuTransport:
    def __init__(
        self,
        delegate: Any,
        *,
        poll_interval: float = 3,
        task_timeout: float = 900,
        result_allowed_hosts: tuple[str, ...] = (),
        send_size: bool = False,
        journal_dir: Path | None = None,
    ) -> None:
        self.delegate = delegate
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout
        self.result_allowed_hosts = result_allowed_hosts
        self.send_size = send_size
        self.journal_dir = journal_dir

    def _request_journal(
        self,
        url: str,
        key: str,
        fields: Sequence[tuple[str, str]],
        files: Sequence[tuple[str, str, str, bytes]],
        *,
        reconcile_only: bool = False,
    ) -> tuple[Path | None, dict[str, Any], bool]:
        if self.journal_dir is None:
            if reconcile_only:
                raise _error(
                    "Original image receipt is unavailable; no new request was sent."
                )
            return None, {}, True
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        scope = hashlib.sha256(json.dumps([url, key]).encode()).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    list(fields),
                    [
                        (role, name, mime, hashlib.sha256(data).hexdigest())
                        for role, name, mime, data in files
                    ],
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        path = self.journal_dir / f"{scope}.json"
        if reconcile_only and not path.is_file():
            raise _error(
                "Original image receipt is unavailable; no new request was sent."
            )
        record: dict[str, Any] = {
            "fingerprint": fingerprint,
            "state": "dispatching",
        }
        try:
            # Reserve the dispatch fence before the network call. A crashed
            # request with no receipt must never become a second paid submit.
            if reconcile_only:
                raise FileExistsError
            with path.open("x", encoding="utf8") as stream:
                json.dump(record, stream)
            return path, record, True
        except FileExistsError:
            try:
                record = json.loads(path.read_text(encoding="utf8"))
            except (ValueError, OSError) as exc:
                raise _error("Image request journal requires reconciliation.") from exc
            if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
                raise _error(
                    "Image request parameters changed; reconciliation required."
                ) from None
            return path, record, False

    def accepted_task(self, url: str, key: str, model: str) -> str | None:
        """Read-only admission; exact request fingerprint is checked on use."""
        if self.journal_dir is None:
            return None
        wire_key = hashlib.sha256(f"{key}:{model}".encode()).hexdigest()
        scope = hashlib.sha256(json.dumps([url, wire_key]).encode()).hexdigest()
        try:
            record = json.loads(
                (self.journal_dir / f"{scope}.json").read_text(encoding="utf8")
            )
        except (OSError, ValueError):
            return None
        task = record.get("task_id") if isinstance(record, dict) else None
        return (
            task
            if isinstance(task, str) and task and record.get("state") == "accepted"
            else None
        )

    @staticmethod
    def _save_journal(path: Path | None, record: dict[str, Any]) -> None:
        if path is None:
            return
        for attempt in range(4):
            temporary = path.with_suffix(f".{uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(record), encoding="utf8")
                temporary.replace(path)
                return
            except PermissionError:
                # Retry only the local atomic replacement. The provider POST
                # has already succeeded and must never be repeated here.
                if attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
            finally:
                # A scanner may also hold this disposable temporary file.
                # Do not turn a persisted receipt into an unknown outcome.
                with suppress(PermissionError):
                    temporary.unlink(missing_ok=True)

    async def post_json(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        return await self.delegate.post_json(**kwargs)

    async def get_task(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, dict[str, Any]]:
        from .async_provider_transport import AsyncProviderTransport

        if isinstance(self.delegate, AsyncProviderTransport):
            return await self.delegate.get_json(url, headers, timeout)

        def read() -> tuple[int, dict[str, Any]]:
            from .domestic_detail.transport import native_urlopen

            try:
                with native_urlopen(
                    Request(url, headers=dict(headers)), timeout
                ) as response:
                    return int(response.status), json.loads(response.read(1024 * 1024))
            except HTTPError as exc:
                return exc.code, {}

        return await asyncio.to_thread(read)

    async def image_data(
        self, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        from .xinghe_agent import XingheAgentError

        items = payload.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise _error("Image service returned no deliverable image.")
        first = items[0]
        if first.get("b64_json"):
            return payload
        if not isinstance(first.get("url"), str):
            raise _error("Image service omitted its result URL.")
        try:
            download_deadline = time.monotonic() + timeout
            for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
                try:
                    data = await asyncio.to_thread(
                        download_result,
                        first["url"],
                        max(0.01, download_deadline - time.monotonic()),
                        self.result_allowed_hosts,
                    )
                    break
                except OSError as error:
                    if (
                        not transient_download(error)
                        or attempt == MAX_DOWNLOAD_ATTEMPTS - 1
                        or time.monotonic() >= download_deadline
                    ):
                        raise
                    await asyncio.sleep(min(2**attempt, max(0.0, timeout / 4)))
        except (OSError, ValueError, XingheAgentError) as exc:
            from .provider_diagnostics import safe_text

            raise XingheAgentError(
                "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED",
                "生成服务已返回图片，但下载或校验未完成；原任务已保留，请勿重复生成。",
                provider_request_id=payload.get("haiyu_task_id")
                or payload.get("request_id"),
                provider_error={
                    "code": "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED",
                    "message": safe_text(str(exc)),
                    "param": urlparse(first["url"]).hostname or "image_result",
                },
                retryable=transient_download(exc) or refreshable_url(exc),
            ) from exc
        return {
            **payload,
            "data": [{"b64_json": base64.b64encode(data).decode("ascii")}],
        }

    async def post_multipart(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        fields: Sequence[tuple[str, str]],
        files: Sequence[tuple[str, str, str, bytes]],
        timeout_seconds: float,
        reconcile_only: bool = False,
        _deadline: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        # Keep explicitly configured backup providers on their own protocol.
        if urlparse(url).hostname != "api.haiyushuke.com":
            if reconcile_only:
                raise _error(
                    "This image provider cannot resume an original task safely."
                )
            return await self.delegate.post_multipart(
                url=url,
                headers=headers,
                fields=fields,
                files=files,
                timeout_seconds=timeout_seconds,
            )
        values = dict(fields)
        wire_files, note = await asyncio.to_thread(pack_references, files)
        # Haiyu currently verifies the default canvas only; explicitly passing
        # size returns 503 "No verified channel supports these parameters".
        # Express the target in the prompt and let existing workflow validators
        # reject a native artifact that does not meet its delivery contract.
        if values.get("size"):
            note += f"\nTarget output canvas: {values['size']} pixels."
        from .haiyu_prompt import fit_prompt

        try:
            wire_prompt = fit_prompt(values.get("prompt", "") + note)
        except ValueError as prompt_error:
            if reconcile_only:
                raise _error(
                    "Original image request exceeds the current wire limit; "
                    "reconciliation is still required."
                ) from prompt_error
            return 400, {
                "error": {
                    "code": "image_prompt_too_long",
                    "type": "invalid_request_error",
                    "message": str(prompt_error),
                }
            }
        wire_fields = [(key, value) for key, value in fields if key in {"model", "n"}]
        if self.send_size and values.get("size"):
            wire_fields.append(("size", values["size"]))
        wire_fields += [
            ("prompt", wire_prompt),
            ("async", "true"),
            ("response_format", "url"),
        ]
        # Haiyu rejects the same key with different parameters. Give each
        # fallback model a stable key while preserving retries of that model.
        key = headers.get("Idempotency-Key", "")
        wire_headers = {
            **headers,
            "Idempotency-Key": hashlib.sha256(
                f"{key}:{values.get('model', '')}".encode()
            ).hexdigest(),
        }
        deadline = (
            _deadline
            if _deadline is not None
            else time.monotonic()
            + min(self.task_timeout, 120 if reconcile_only else self.task_timeout)
        )
        receipt_warning = None
        try:
            journal, receipt, fresh = self._request_journal(
                url,
                wire_headers["Idempotency-Key"],
                wire_fields,
                wire_files,
                reconcile_only=reconcile_only,
            )
        except OSError as exc:
            raise _error(
                "Image request journal is unavailable; reconcile before retry."
            ) from exc
        if reconcile_only and (
            fresh or receipt.get("state") != "accepted" or not receipt.get("task_id")
        ):
            raise _error(
                "No accepted original image task was found; no new request was sent."
            )
        rate_retries = int(receipt.get("rate_limit_retries", 0))
        replay_rate_limit = (
            not reconcile_only
            and receipt.get("state") == "rejected"
            and no_acceptance_rate_limit(
                receipt.get("http_status", 0), {"error": receipt.get("error")}
            )
            and rate_retries < MAX_RATE_LIMIT_RETRIES
        )
        if fresh or replay_rate_limit:
            if replay_rate_limit:
                delay = retry_after(receipt.get("retry_after"), 2**rate_retries)
                if delay > 60 or time.monotonic() + delay >= deadline:
                    return 429, {"error": receipt["error"]}
                await asyncio.sleep(delay)
                # Durable bound before another same-key request. A crash while
                # sending reverts to unknown, never to a replayable rejection.
                receipt.update(state="dispatching", rate_limit_retries=rate_retries + 1)
                self._save_journal(journal, receipt)
            status, payload = await self.delegate.post_multipart(
                url=url,
                headers=wire_headers,
                fields=wire_fields,
                files=wire_files,
                timeout_seconds=min(timeout_seconds, self.task_timeout),
            )
            receipt.update(
                task_id=payload.get("id"), request_id=payload.get("request_id")
            )
            if status >= 400:
                from .provider_diagnostics import error_summary

                receipt.update(
                    state="rejected", http_status=status, error=error_summary(payload)
                )
                receipt["retry_after"] = payload.get("_transport_retry_after")
            else:
                receipt["state"] = "accepted"
            try:
                self._save_journal(journal, receipt)
            except OSError as exc:
                if status >= 400 or not isinstance(payload.get("id"), str):
                    raise _error(
                        "Image task receipt could not be persisted; "
                        f"reconciliation required (errno={exc.errno}, "
                        f"winerror={getattr(exc, 'winerror', None)}).",
                        payload.get("id"),
                    ) from exc
                # The pre-dispatch fence already exists. We hold the original
                # accepted task ID, so continuing its GET polling is safe even
                # if saving that receipt failed. Never issue another POST.
                receipt_warning = {
                    "errno": exc.errno,
                    "winerror": getattr(exc, "winerror", None),
                    "task_id": payload["id"],
                }
            if (
                no_acceptance_rate_limit(status, payload)
                and journal is not None
                and rate_retries < MAX_RATE_LIMIT_RETRIES
            ):
                # Same fingerprint/key, only an explicit pre-acceptance limit.
                # The journal enforces the cumulative retry bound on re-entry.
                return await self.post_multipart(
                    url=url,
                    headers=headers,
                    fields=fields,
                    files=files,
                    timeout_seconds=max(0.01, deadline - time.monotonic()),
                    _deadline=deadline,
                )
        elif receipt.get("task_id"):
            # Continue the already accepted task; do not call images/edits.
            status, payload = (
                202,
                {
                    "id": receipt["task_id"],
                    "request_id": receipt.get("request_id"),
                    "status": "queued",
                },
            )
        elif receipt.get("state") == "rejected":
            status, payload = receipt["http_status"], {"error": receipt["error"]}
        else:
            raise _error(
                "Image request outcome requires reconciliation; do not resubmit."
            )
        if status >= 400:
            if any(
                payload.get(k)
                for k in (
                    "id",
                    "task_id",
                    "generation_id",
                    "data",
                    "images",
                    "output",
                    "url",
                    "b64_json",
                )
            ):
                raise _error(
                    "Image request may have been accepted; reconciliation required.",
                    payload.get("id") or payload.get("task_id"),
                )
            error = payload.get("error")
            code = str(error.get("code", "")) if isinstance(error, Mapping) else ""
            message = (
                str(error.get("message", "")) if isinstance(error, Mapping) else ""
            )
            from .provider_diagnostics import error_summary, image_content_refusal

            explicit_stop = any(
                word in message.casefold()
                for word in (
                    "do not create",
                    "do not resubmit",
                    "do not retry",
                    "don't retry",
                    "不要重新",
                    "不得重试",
                    "结算待处理",
                )
            )
            known_route_rejection = code.casefold() in {
                "channel_unavailable",
                "no_available_channel",
                "channel_not_found",
                "model_not_found",
                "model_not_available",
                "unsupported_model",
                "model_not_supported",
                "unsupported_parameters",
            }
            uncertain_rejection = (
                status in {408, 425}
                or (status >= 500 and not known_route_rejection)
                or (status == 429 and not no_acceptance_rate_limit(status, payload))
            ) and not image_content_refusal(payload)
            if explicit_stop or uncertain_rejection:
                from .xinghe_agent import XingheAgentError

                raise XingheAgentError(
                    "PROVIDER_RESPONSE_INVALID",
                    f"生成接口返回 HTTP {status}，未确认尚未接单或明确禁止替代；"
                    "已停止自动重发和模型切换。",
                    provider_request_id=payload.get("request_id"),
                    provider_error=error_summary(payload),
                )
            if any(
                word in code.lower()
                for word in ("unknown", "pending", "settlement", "reconcil")
            ):
                raise _error(
                    "Image request requires outcome reconciliation; "
                    "do not create a replacement."
                )
            # A 409 is a key/parameter conflict; switching models cannot repair
            # it. Convert it to a terminal parameter rejection for the gateway.
            return (400 if status == 409 else status), payload
        if payload.get("data"):
            return status, await self.image_data(
                {**payload, "haiyu_task_id": payload.get("id")},
                min(timeout_seconds, 60),
            )
        task_id = payload.get("id")
        initial_request_id = payload.get("request_id")
        if not isinstance(task_id, str) or not task_id:
            raise _error("Image service returned neither an image nor a task ID.")
        task_url = url.rsplit("/", 1)[0] + "/tasks/" + quote(task_id, safe="")
        poll_errors = 0
        url_refreshes = 0

        def pending(message: str) -> Exception:
            from .xinghe_agent import XingheAgentError

            return XingheAgentError(
                "IMAGE_TASK_QUERY_UNCONFIRMED",
                message,
                provider_request_id=task_id,
                retryable=True,
            )

        while time.monotonic() < deadline:
            state = payload.get("status")
            if state == "succeeded":
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise _error("Completed image task omitted its result.", task_id)
                try:
                    result = await self.image_data(
                        {
                            **result,
                            "request_id": initial_request_id or task_id,
                            "haiyu_task_id": task_id,
                        },
                        min(
                            timeout_seconds, 60, max(0.01, deadline - time.monotonic())
                        ),
                    )
                except Exception as exc:
                    if (
                        getattr(exc, "code", None)
                        == "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"
                        and getattr(exc, "retryable", False)
                        and url_refreshes < MAX_URL_REFRESHES
                        and time.monotonic() < deadline
                    ):
                        # Refresh result metadata via the original task, never POST.
                        url_refreshes += 1
                        payload = {"status": "delivering"}
                        continue
                    raise
                return 200, {
                    **result,
                    "request_id": initial_request_id or task_id,
                    "haiyu_task_id": task_id,
                    "image_recovery": {
                        "reconcile_only": reconcile_only,
                        "url_refreshes": url_refreshes,
                        "rate_limit_retries": int(receipt.get("rate_limit_retries", 0)),
                        "original_task_id": task_id,
                    },
                    **(
                        {"receipt_persistence_warning": receipt_warning}
                        if receipt_warning
                        else {}
                    ),
                }
            if state in {"failed", "expired", "cancelled"}:
                error = payload.get("error") or {}
                code = str(error.get("code", "")) if isinstance(error, dict) else ""
                if state != "failed" or any(
                    word in code.lower()
                    for word in ("unknown", "pending", "settlement", "reconcil")
                ):
                    raise _error(
                        f"Image task {task_id} requires outcome reconciliation.",
                        task_id,
                    )
                invalid_input = code in {
                    "invalid_image",
                    "invalid_request_error",
                    "invalid_parameter",
                    "content_policy_violation",
                    "safety_violation",
                }
                if invalid_input:
                    return 400, {
                        "error": error,
                        "request_id": initial_request_id or task_id,
                    }
                from .provider_diagnostics import error_summary
                from .xinghe_agent import XingheAgentError

                # The accepted async task has already run. Its terminal error
                # is not a routing rejection and must not buy another image.
                raise XingheAgentError(
                    "IMAGE_ASYNC_TASK_FAILED",
                    "上游图片任务未完成，原任务编号已保留，请核查后再决定是否重新生成。",
                    provider_request_id=task_id,
                    provider_error=error_summary({"error": error}),
                )
            await asyncio.sleep(
                min(self.poll_interval, max(0, deadline - time.monotonic()))
            )
            try:
                status, polled = await self.get_task(
                    task_url, headers, min(30, max(0.01, deadline - time.monotonic()))
                )
            except (OSError, ValueError, TypeError) as exc:
                poll_errors += 1
                if poll_errors > MAX_POLL_ERRORS:
                    raise pending(
                        "原任务查询连续异常，已达到本轮查询恢复上限；未重新生成。"
                    ) from exc
                continue  # Retry only GET for the same original task.
            if status == 200 and isinstance(polled, dict):
                if polled.get("id", task_id) != task_id:
                    raise _error(
                        "Image task query returned a different task ID.", task_id
                    )
                if polled.get("status") not in {
                    "queued",
                    "running",
                    "delivering",
                    "succeeded",
                    "failed",
                    "expired",
                    "cancelled",
                }:
                    poll_errors += 1
                    if poll_errors > MAX_POLL_ERRORS:
                        raise pending("原任务状态响应连续不完整，未重新生成。")
                    continue
                poll_errors = 0
                payload = polled
            elif status in {401, 403, 404, 410}:
                raise _error(
                    f"Image task {task_id} status could not be confirmed.", task_id
                )
            else:
                poll_errors += 1
                if poll_errors > MAX_POLL_ERRORS:
                    raise pending(
                        f"原任务查询连续返回 HTTP {status}，"
                        "已停止本轮恢复；未重新生成。"
                    )
        raise pending(
            f"Image task {task_id} is still pending; do not resubmit automatically.",
        )
