"""Bounded, private thumbnail cache; originals remain immutable in storage."""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from io import BytesIO

from PIL import Image, ImageOps


class ThumbnailBusy(Exception):
    pass


def render_thumbnail(payload: bytes) -> bytes:
    with Image.open(BytesIO(payload)) as image:
        # JPEG draft decoding and resizing before EXIF transpose avoid copying
        # a full-size raster. Only one decode runs at a time per API process.
        image.draft("RGB", (480, 480))
        image.thumbnail((480, 480), Image.Resampling.LANCZOS, reducing_gap=3.0)
        thumbnail = ImageOps.exif_transpose(image)
        thumbnail = thumbnail.convert("RGBA" if "A" in thumbnail.getbands() else "RGB")
        output = BytesIO()
        thumbnail.save(output, format="WEBP", quality=72, method=2)
        return output.getvalue()


class ThumbnailCache:
    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_pending: int = 64):
        self.max_bytes = max_bytes
        self.max_pending = max_pending
        self._bytes = 0
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._pending: dict[str, asyncio.Task[bytes]] = {}
        self._decode = asyncio.Semaphore(1)

    async def get(self, key: str, read: Callable[[], Awaitable[bytes]]) -> bytes:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        task = self._pending.get(key)
        if task is None:
            if len(self._pending) >= self.max_pending:
                raise ThumbnailBusy
            task = asyncio.create_task(self._produce(key, read))
            self._pending[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        try:
            # One tab cancelling a download must not cancel shared work for
            # another tab. A bounded queue also limits cold-cache memory use.
            return await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except TimeoutError as exc:
            raise ThumbnailBusy from exc

    def _finished(self, key: str, task: asyncio.Task[bytes]) -> None:
        self._pending.pop(key, None)
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every caller disconnected.

    async def _produce(self, key: str, read: Callable[[], Awaitable[bytes]]) -> bytes:
        async with self._decode:
            payload = await asyncio.wait_for(read(), timeout=10)
            result = await asyncio.to_thread(render_thumbnail, payload)
        if len(result) <= self.max_bytes:
            while self._cache and self._bytes + len(result) > self.max_bytes:
                _, previous = self._cache.popitem(last=False)
                self._bytes -= len(previous)
            self._cache[key] = result
            self._bytes += len(result)
        return result
