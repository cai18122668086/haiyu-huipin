"""Private model-pose catalog resolution and original-image integrity checks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

MODEL_POSE_CATALOG_ID = "ecommerce-model-pose-transfer"
MODEL_POSE_CATALOG_SCHEMA = "model-pose-catalog-v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SLUG_PATTERN = re.compile(r"(?:male|female)-[0-9]{3}")


class ModelPoseCatalogError(ValueError):
    """A stable, client-safe catalog validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelPoseCatalogItem:
    slug: str
    enabled: bool
    reference_sha256: str
    original_key: str
    filename: str
    mime_type: str
    width: int
    height: int
    byte_size: int
    path: Path

    def read_original(self) -> bytes:
        try:
            payload = self.path.read_bytes()
        except OSError as exc:
            raise ModelPoseCatalogError(
                "MODEL_POSE_ORIGINAL_UNAVAILABLE",
                f"The catalog pose original is unavailable ({type(exc).__name__}).",
            ) from None
        digest = hashlib.sha256(payload).hexdigest()
        if digest != self.reference_sha256 or len(payload) != self.byte_size:
            raise ModelPoseCatalogError(
                "MODEL_POSE_ORIGINAL_INTEGRITY_FAILED",
                "The catalog pose original failed its integrity check.",
            )
        try:
            with Image.open(BytesIO(payload)) as image:
                image.load()
                image_format = str(image.format)
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ModelPoseCatalogError(
                "MODEL_POSE_ORIGINAL_INTEGRITY_FAILED",
                f"The catalog pose original is invalid ({type(exc).__name__}).",
            ) from None
        if (
            image_format != "PNG"
            or self.mime_type != "image/png"
            or width != self.width
            or height != self.height
        ):
            raise ModelPoseCatalogError(
                "MODEL_POSE_ORIGINAL_INTEGRITY_FAILED",
                "The catalog pose original metadata does not match the catalog.",
            )
        return payload


def bundled_model_pose_catalog_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "catalog_assets"
        / "model-pose-transfer"
        / "catalog.json"
    )


def resolve_model_pose_catalog_item(
    pose_slug: str,
    reference_sha256: str,
    *,
    catalog_path: Path | None = None,
) -> ModelPoseCatalogItem:
    """Resolve one enabled slug/hash pair without accepting any client path."""

    slug = pose_slug.strip() if isinstance(pose_slug, str) else ""
    digest = (
        reference_sha256.strip().lower() if isinstance(reference_sha256, str) else ""
    )
    if not _SLUG_PATTERN.fullmatch(slug):
        raise ModelPoseCatalogError(
            "MODEL_POSE_SLUG_INVALID", "The selected catalog pose slug is invalid."
        )
    if not _SHA256_PATTERN.fullmatch(digest):
        raise ModelPoseCatalogError(
            "MODEL_POSE_HASH_INVALID", "The selected catalog pose hash is invalid."
        )

    path = catalog_path or bundled_model_pose_catalog_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_UNAVAILABLE",
            f"The model-pose catalog is unavailable ({type(exc).__name__}).",
        ) from None
    if not isinstance(payload, dict):
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_INVALID", "The model-pose catalog is invalid."
        )
    items = payload.get("items")
    if (
        payload.get("schemaVersion") != MODEL_POSE_CATALOG_SCHEMA
        or payload.get("catalogId") != MODEL_POSE_CATALOG_ID
        or not isinstance(items, list)
        or payload.get("itemCount") != len(items)
    ):
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_INVALID", "The model-pose catalog is invalid."
        )

    selected: dict[str, Any] | None = None
    for value in items:
        if isinstance(value, dict) and value.get("slug") == slug:
            selected = value
            break
    if selected is None:
        raise ModelPoseCatalogError(
            "MODEL_POSE_NOT_FOUND", "The selected catalog pose does not exist."
        )
    if selected.get("enabled") is not True:
        raise ModelPoseCatalogError(
            "MODEL_POSE_DISABLED", "The selected catalog pose is disabled."
        )
    if selected.get("referenceSha256") != digest:
        raise ModelPoseCatalogError(
            "MODEL_POSE_HASH_MISMATCH",
            "The selected catalog pose hash does not match the private original.",
        )

    original = selected.get("original")
    original_key = selected.get("originalKey")
    if not isinstance(original, dict) or not isinstance(original_key, str):
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_INVALID", "The selected catalog pose is invalid."
        )
    catalog_root = path.parent.resolve()
    candidate = (catalog_root / original_key).resolve()
    if (
        not candidate.is_relative_to(catalog_root)
        or candidate.suffix.lower() != ".png"
        or Path(original_key).is_absolute()
        or ".." in Path(original_key).parts
    ):
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_INVALID", "The selected catalog pose path is invalid."
        )

    try:
        filename = str(original["filename"])
        mime_type = str(original["mimeType"])
        width = int(original["width"])
        height = int(original["height"])
        byte_size = int(original["byteSize"])
        original_sha256 = str(original["sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_INVALID", "The selected catalog pose is invalid."
        ) from exc
    if (
        filename != candidate.name
        or mime_type != "image/png"
        or width <= 0
        or height <= 0
        or byte_size <= 0
        or original_sha256 != digest
    ):
        raise ModelPoseCatalogError(
            "MODEL_POSE_CATALOG_INVALID", "The selected catalog pose is invalid."
        )
    return ModelPoseCatalogItem(
        slug=slug,
        enabled=True,
        reference_sha256=digest,
        original_key=original_key,
        filename=filename,
        mime_type=mime_type,
        width=width,
        height=height,
        byte_size=byte_size,
        path=candidate,
    )
