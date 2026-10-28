"""Spool validated downloads to writable task storage and close them on disconnect."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast
from zipfile import ZipFile

from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from .storage import build_content_disposition

ARCHIVE_MEMORY_LIMIT = 8 * 1024 * 1024


def open_archive(directory: Path) -> BinaryIO:
    directory.mkdir(parents=True, exist_ok=True)
    return cast(
        BinaryIO,
        SpooledTemporaryFile(
            max_size=ARCHIVE_MEMORY_LIMIT, mode="w+b", dir=str(directory)
        ),
    )


def close_archive(bundle: ZipFile | None, archive: BinaryIO) -> None:
    try:
        if bundle is not None:
            bundle.close()
    finally:
        archive.close()


def _chunks(archive: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := archive.read(64 * 1024):
            yield chunk
    finally:
        archive.close()


class ArchiveResponse(StreamingResponse):
    def __init__(self, archive: BinaryIO, filename: str) -> None:
        size = archive.seek(0, 2)
        archive.seek(0)
        self.archive = archive
        super().__init__(
            _chunks(archive),
            media_type="application/zip",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": build_content_disposition(
                    "attachment", filename
                ),
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(self.archive.close)
