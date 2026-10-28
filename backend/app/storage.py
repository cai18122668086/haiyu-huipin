import asyncio
import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .errors import UploadValidationError

ALLOWED_IMAGE_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}


@dataclass(frozen=True)
class StoredImage:
    filename: str
    content_type: str
    byte_size: int
    width: int
    height: int
    object_key: str
    sha256: str
    storage_provider: str
    storage_bucket: str
    object_version: str = ""
    etag: str | None = None


@dataclass(frozen=True)
class ValidatedImage:
    payload: bytes
    filename: str
    content_type: str
    width: int
    height: int
    object_key: str
    sha256: str


class ImageStorage(Protocol):
    provider_name: str
    bucket_name: str
    validator: "ImageValidator"

    async def save_image(
        self,
        upload: UploadFile,
        workspace_id: str,
    ) -> StoredImage: ...

    async def save_validated(self, image: ValidatedImage) -> StoredImage: ...

    async def delete_object(
        self,
        object_key: str,
        object_version: str = "",
    ) -> None: ...

    async def read_bytes(self, object_key: str) -> bytes: ...

    async def read_stored_image(
        self, object_key: str, object_version: str = ""
    ) -> tuple[bytes, StoredImage]: ...

    async def create_access_url(
        self,
        object_key: str,
        filename: str,
        disposition: str,
        expires_seconds: int,
    ) -> str | None: ...


def build_image_storage(settings: Settings) -> ImageStorage:
    """Build the shared API/worker storage dependency from one configuration."""

    validator = ImageValidator(
        settings.max_upload_bytes,
        settings.max_image_pixels,
    )
    if settings.storage_backend == "s3":
        return S3ImageStorage(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            validator=validator,
            auto_create_bucket=settings.s3_auto_create_bucket,
        )
    return LocalImageStorage(settings.storage_dir, validator)


class ImageValidator:
    def __init__(self, max_upload_bytes: int, max_image_pixels: int) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.max_image_pixels = max_image_pixels

    async def validate(
        self,
        upload: UploadFile,
        workspace_id: str,
    ) -> ValidatedImage:
        payload = await upload.read(self.max_upload_bytes + 1)
        if not payload:
            raise UploadValidationError("EMPTY_FILE", "上传文件不能为空。")
        if len(payload) > self.max_upload_bytes:
            raise UploadValidationError(
                "FILE_TOO_LARGE",
                f"图片不能超过 {self.max_upload_bytes // (1024 * 1024)}MB。",
            )

        try:
            with Image.open(BytesIO(payload)) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise UploadValidationError(
                "INVALID_IMAGE",
                "文件不是可识别的 JPG/JPEG、PNG 或 WebP 图片。",
            ) from exc

        if image_format not in ALLOWED_IMAGE_FORMATS:
            raise UploadValidationError(
                "UNSUPPORTED_IMAGE_TYPE",
                "仅支持 JPG/JPEG、PNG 和 WebP 图片。",
            )
        if width * height > self.max_image_pixels:
            raise UploadValidationError(
                "IMAGE_DIMENSIONS_TOO_LARGE",
                "图片像素总量超过当前限制。",
            )

        if not re.fullmatch(r"[A-Za-z0-9_-]+", workspace_id):
            raise UploadValidationError(
                "INVALID_WORKSPACE_ID",
                "工作区标识格式不正确。",
            )

        suffix, content_type = ALLOWED_IMAGE_FORMATS[image_format]
        filename = Path(upload.filename or f"upload{suffix}").name[:255]
        return ValidatedImage(
            payload=payload,
            filename=filename,
            content_type=content_type,
            width=width,
            height=height,
            object_key=f"{workspace_id}/{uuid4().hex}{suffix}",
            sha256=hashlib.sha256(payload).hexdigest(),
        )


class LocalImageStorage:
    def __init__(
        self,
        root: Path,
        validator: ImageValidator,
    ) -> None:
        self.root = root.resolve()
        self.validator = validator
        self.provider_name = "local"
        self.bucket_name = "local"

    async def save_image(
        self,
        upload: UploadFile,
        workspace_id: str,
    ) -> StoredImage:
        image = await self.validator.validate(upload, workspace_id)
        return await self.save_validated(image)

    async def save_validated(self, image: ValidatedImage) -> StoredImage:
        validate_prepared_image(
            image,
            max_bytes=self.validator.max_upload_bytes,
            max_pixels=self.validator.max_image_pixels,
        )
        destination = (self.root / image.object_key).resolve()
        self._require_safe_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, image.payload)
        return _stored_image(
            image,
            provider=self.provider_name,
            bucket=self.bucket_name,
        )

    async def delete_object(
        self,
        object_key: str,
        object_version: str = "",
    ) -> None:
        del object_version
        destination = (self.root / object_key).resolve()
        self._require_safe_path(destination)
        await asyncio.to_thread(destination.unlink, missing_ok=True)

    async def read_bytes(self, object_key: str) -> bytes:
        source = (self.root / object_key).resolve()
        if self.root not in source.parents:
            raise FileNotFoundError(object_key)
        return await asyncio.to_thread(source.read_bytes)

    async def read_stored_image(
        self, object_key: str, object_version: str = ""
    ) -> tuple[bytes, StoredImage]:
        if object_version:
            raise ValueError("Local image storage has no object versions")
        payload = await self.read_bytes(object_key)
        return payload, _describe_stored_image(
            payload, object_key, provider=self.provider_name, bucket=self.bucket_name
        )

    def _require_safe_path(self, path: Path) -> None:
        if self.root not in path.parents:
            raise UploadValidationError(
                "INVALID_STORAGE_PATH",
                "无法创建安全的文件存储路径。",
            )

    async def create_access_url(
        self,
        object_key: str,
        filename: str,
        disposition: str,
        expires_seconds: int,
    ) -> str | None:
        del object_key, filename, disposition, expires_seconds
        return None


class S3ImageStorage:
    def __init__(
        self,
        *,
        endpoint_url: str | None,
        region: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        validator: ImageValidator,
        auto_create_bucket: bool = False,
    ) -> None:
        self.bucket = bucket
        self.bucket_name = bucket
        self.provider_name = "s3"
        self.validator = validator
        self.auto_create_bucket = auto_create_bucket
        self.client: BaseClient = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # Object storage is on the request path for private assets. Bound
            # network waits so a degraded MinIO/S3 endpoint cannot occupy the
            # API thread pool indefinitely.
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=30,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )
        self._bucket_ready = False

    async def save_image(
        self,
        upload: UploadFile,
        workspace_id: str,
    ) -> StoredImage:
        image = await self.validator.validate(upload, workspace_id)
        return await self.save_validated(image)

    async def save_validated(self, image: ValidatedImage) -> StoredImage:
        validate_prepared_image(
            image,
            max_bytes=self.validator.max_upload_bytes,
            max_pixels=self.validator.max_image_pixels,
        )
        result = await asyncio.to_thread(self._put_image, image)
        raw_etag = result.get("ETag")
        return _stored_image(
            image,
            provider=self.provider_name,
            bucket=self.bucket,
            etag=str(raw_etag).strip('"') if raw_etag else None,
            version=str(result.get("VersionId") or ""),
        )

    def _put_image(self, image: ValidatedImage) -> dict[str, object]:
        self._ensure_bucket()
        result = self.client.put_object(
            Bucket=self.bucket,
            Key=image.object_key,
            Body=image.payload,
            ContentType=image.content_type,
            Metadata={
                "workspace-id": image.object_key.split("/", 1)[0],
                "sha256": image.sha256,
            },
        )
        return dict(result)

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            if not self.auto_create_bucket:
                raise RuntimeError(
                    f"对象存储桶 {self.bucket!r} 不存在，且禁止在请求路径自动创建。"
                ) from exc
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    async def delete_object(
        self,
        object_key: str,
        object_version: str = "",
    ) -> None:
        await asyncio.to_thread(self._delete_object, object_key, object_version)

    def _delete_object(self, object_key: str, object_version: str) -> None:
        request: dict[str, str] = {
            "Bucket": self.bucket,
            "Key": object_key,
        }
        if object_version:
            request["VersionId"] = object_version
        self.client.delete_object(**request)

    async def read_bytes(self, object_key: str) -> bytes:
        response = await asyncio.to_thread(
            self.client.get_object,
            Bucket=self.bucket,
            Key=object_key,
        )
        return await asyncio.to_thread(response["Body"].read)

    async def read_stored_image(
        self, object_key: str, object_version: str = ""
    ) -> tuple[bytes, StoredImage]:
        parameters = {"Bucket": self.bucket, "Key": object_key}
        if object_version:
            parameters["VersionId"] = object_version
        response = await asyncio.to_thread(self.client.get_object, **parameters)
        payload = await asyncio.to_thread(response["Body"].read)
        actual_version = str(response.get("VersionId") or "")
        if object_version and actual_version != object_version:
            raise ValueError("Storage returned a different object version")
        metadata = _describe_stored_image(
            payload,
            object_key,
            provider=self.provider_name,
            bucket=self.bucket_name,
            version=actual_version,
            etag=str(response.get("ETag") or "").strip('"') or None,
        )
        if (response.get("ContentType"), response.get("ContentLength")) != (
            metadata.content_type,
            metadata.byte_size,
        ):
            raise ValueError("Stored image headers differ from its exact raster")
        return payload, metadata

    async def create_access_url(
        self,
        object_key: str,
        filename: str,
        disposition: str,
        expires_seconds: int,
    ) -> str | None:
        content_disposition = build_content_disposition(disposition, filename)
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "ResponseContentDisposition": content_disposition,
            },
            ExpiresIn=expires_seconds,
        )


def _describe_stored_image(
    payload: bytes,
    object_key: str,
    *,
    provider: str,
    bucket: str,
    version: str = "",
    etag: str | None = None,
) -> StoredImage:
    with Image.open(BytesIO(payload)) as image:
        width, height = image.size
        image_format = image.format
        image.verify()
    contract = ALLOWED_IMAGE_FORMATS.get(image_format or "")
    if contract is None:
        raise ValueError("Stored object is not a supported raster")
    return StoredImage(
        filename=Path(object_key).name,
        content_type=contract[1],
        byte_size=len(payload),
        width=width,
        height=height,
        object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(),
        storage_provider=provider,
        storage_bucket=bucket,
        object_version=version,
        etag=etag,
    )


def _stored_image(
    image: ValidatedImage,
    *,
    provider: str,
    bucket: str,
    version: str = "",
    etag: str | None = None,
) -> StoredImage:
    return StoredImage(
        filename=image.filename,
        content_type=image.content_type,
        byte_size=len(image.payload),
        width=image.width,
        height=image.height,
        object_key=image.object_key,
        sha256=image.sha256,
        storage_provider=provider,
        storage_bucket=bucket,
        object_version=version,
        etag=etag,
    )


def validate_prepared_image(
    image: ValidatedImage,
    *,
    max_bytes: int,
    max_pixels: int,
) -> None:
    """Fail closed unless prepared output metadata matches the actual pixels.

    ``ValidatedImage`` is also used for generated artifacts.  Treating its
    fields as trusted would allow a provider metadata bug to cross the final
    persistence boundary, so both local and S3 storage re-open the exact bytes
    immediately before writing them.
    """

    if not image.payload:
        raise UploadValidationError("EMPTY_FILE", "Prepared image is empty.")
    if len(image.payload) > max_bytes:
        raise UploadValidationError(
            "FILE_TOO_LARGE",
            "Prepared image exceeds the configured byte limit.",
        )
    if hashlib.sha256(image.payload).hexdigest() != image.sha256:
        raise UploadValidationError(
            "IMAGE_HASH_MISMATCH",
            "Prepared image bytes do not match the declared SHA-256.",
        )
    try:
        with Image.open(BytesIO(image.payload)) as opened:
            image_format = str(opened.format or "")
            opened.load()
            width, height = ImageOps.exif_transpose(opened).size
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise UploadValidationError(
            "INVALID_IMAGE",
            "Prepared output is not a valid supported image.",
        ) from exc
    format_contract = ALLOWED_IMAGE_FORMATS.get(image_format)
    if format_contract is None:
        raise UploadValidationError(
            "UNSUPPORTED_IMAGE_TYPE",
            "Prepared output uses an unsupported image format.",
        )
    canonical_suffix, canonical_content_type = format_contract
    if (width, height) != (image.width, image.height):
        raise UploadValidationError(
            "IMAGE_DIMENSIONS_MISMATCH",
            "Prepared image pixels do not match the declared dimensions.",
        )
    if width * height > max_pixels:
        raise UploadValidationError(
            "IMAGE_DIMENSIONS_TOO_LARGE",
            "Prepared image exceeds the configured pixel limit.",
        )
    if image.content_type.lower() != canonical_content_type:
        raise UploadValidationError(
            "IMAGE_CONTENT_TYPE_MISMATCH",
            "Prepared image bytes do not match the declared content type.",
        )
    allowed_suffixes = (
        {".jpg", ".jpeg"} if canonical_suffix == ".jpg" else {canonical_suffix}
    )
    object_suffix = Path(image.object_key).suffix.lower()
    if object_suffix not in allowed_suffixes:
        raise UploadValidationError(
            "IMAGE_FILENAME_MISMATCH",
            "Prepared image storage key has the wrong extension.",
        )


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    return cleaned[:120] or "download"


def build_content_disposition(disposition: str, filename: str) -> str:
    """Return an ASCII-safe Content-Disposition with a UTF-8 filename."""

    if disposition not in {"inline", "attachment"}:
        raise ValueError("Unsupported content disposition.")
    basename = Path(filename).name or "download"
    fallback = _safe_filename(basename)
    encoded = quote(basename, safe="!#$&+-.^_`|~")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
