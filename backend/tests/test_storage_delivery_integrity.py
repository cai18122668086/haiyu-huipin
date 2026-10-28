from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app.errors import UploadValidationError
from app.storage import ImageValidator, LocalImageStorage, ValidatedImage


def _jpeg_payload(size: tuple[int, int] = (1200, 1600)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "#315a88").save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _prepared(payload: bytes, **overrides: object) -> ValidatedImage:
    values: dict[str, object] = {
        "payload": payload,
        "filename": "deliverable.jpg",
        "content_type": "image/jpeg",
        "width": 1200,
        "height": 1600,
        "object_key": "ws_test/generated/deliverable.jpg",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    values.update(overrides)
    return ValidatedImage(**values)  # type: ignore[arg-type]


def test_local_storage_revalidates_prepared_output_bytes(tmp_path) -> None:
    payload = _jpeg_payload()
    storage = LocalImageStorage(tmp_path, ImageValidator(20_000_000, 64_000_000))

    stored = asyncio.run(storage.save_validated(_prepared(payload)))

    assert (stored.width, stored.height) == (1200, 1600)
    assert (tmp_path / stored.object_key).read_bytes() == payload


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"width": 1199}, "IMAGE_DIMENSIONS_MISMATCH"),
        ({"content_type": "image/png"}, "IMAGE_CONTENT_TYPE_MISMATCH"),
        (
            {"object_key": "ws_test/generated/deliverable.png"},
            "IMAGE_FILENAME_MISMATCH",
        ),
        ({"sha256": "0" * 64}, "IMAGE_HASH_MISMATCH"),
    ],
)
def test_local_storage_rejects_mismatched_prepared_output(
    tmp_path,
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    payload = _jpeg_payload()
    storage = LocalImageStorage(tmp_path, ImageValidator(20_000_000, 64_000_000))

    with pytest.raises(UploadValidationError) as caught:
        asyncio.run(storage.save_validated(_prepared(payload, **overrides)))

    assert caught.value.code == expected_code
    assert not list(tmp_path.rglob("*.*"))


def test_local_storage_rejects_declared_jpeg_with_png_bytes(tmp_path) -> None:
    buffer = BytesIO()
    Image.new("RGB", (1200, 1600), "white").save(buffer, format="PNG")
    payload = buffer.getvalue()
    storage = LocalImageStorage(tmp_path, ImageValidator(20_000_000, 64_000_000))

    with pytest.raises(UploadValidationError) as caught:
        asyncio.run(storage.save_validated(_prepared(payload)))

    assert caught.value.code == "IMAGE_CONTENT_TYPE_MISMATCH"
    assert not list(tmp_path.rglob("*.*"))


def test_storage_preserves_user_filename_when_bytes_have_another_extension(
    tmp_path,
) -> None:
    payload = _jpeg_payload()
    storage = LocalImageStorage(tmp_path, ImageValidator(20_000_000, 64_000_000))

    stored = asyncio.run(
        storage.save_validated(_prepared(payload, filename="original-upload.png"))
    )

    assert stored.filename == "original-upload.png"
    assert stored.content_type == "image/jpeg"
