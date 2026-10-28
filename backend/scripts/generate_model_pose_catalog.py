"""Build the deterministic model-pose catalog and its web image derivatives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

CATALOG_ID = "ecommerce-model-pose-transfer"
SCHEMA_VERSION = "model-pose-catalog-v1"
DEFAULT_SOURCE_DIR = Path(
    os.environ.get(
        "MODEL_POSE_SOURCE_DIR",
        "D:/Users/Administrator/Desktop/电商素材调查/模特换姿势商用",
    )
)
EXPECTED_SOURCE_NUMBERS: dict[str, tuple[int, ...]] = {
    "male": tuple(range(1, 22)),
    "female": (*range(1, 34), 35, 36, 38, 39, 40, 41),
}
SOURCE_DIRECTORIES = {"male": "男", "female": "女"}
GROUP_LABELS = {"male": "男姿势", "female": "女姿势"}
THUMBNAIL_SPECS = ((320, 82), (640, 84))
PREVIEW_MAX_EDGE = 960
PREVIEW_QUALITY = 86
WEBP_METHOD = 6


@dataclass(frozen=True)
class PoseSource:
    gender_group: str
    source_number: int
    path: Path

    @property
    def slug(self) -> str:
        return f"{self.gender_group}-{self.source_number:03d}"


@dataclass(frozen=True)
class FileMetadata:
    width: int
    height: int
    byte_size: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metadata(path: Path) -> FileMetadata:
    with Image.open(path) as image:
        width, height = image.size
    return FileMetadata(
        width=width,
        height=height,
        byte_size=path.stat().st_size,
        sha256=sha256_file(path),
    )


def discover_source_images(source_dir: Path) -> list[PoseSource]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Pose source directory does not exist: {source_dir}")

    sources: list[PoseSource] = []
    for gender_group, directory_name in SOURCE_DIRECTORIES.items():
        group_dir = source_dir / directory_name
        if not group_dir.is_dir():
            raise FileNotFoundError(f"Pose source group does not exist: {group_dir}")

        numbered_pngs: dict[int, Path] = {}
        for path in group_dir.iterdir():
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            try:
                source_number = int(path.stem)
            except ValueError as error:
                raise ValueError(
                    f"Pose PNG must use a numeric filename: {path}"
                ) from error
            if source_number in numbered_pngs:
                raise ValueError(
                    f"Duplicate pose number {source_number} in source group {group_dir}"
                )
            numbered_pngs[source_number] = path

        expected = set(EXPECTED_SOURCE_NUMBERS[gender_group])
        actual = set(numbered_pngs)
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            raise ValueError(
                f"Unexpected {gender_group} pose set; missing={missing}, "
                f"unexpected={unexpected}"
            )

        sources.extend(
            PoseSource(gender_group, number, numbered_pngs[number])
            for number in EXPECTED_SOURCE_NUMBERS[gender_group]
        )

    return sources


def resized_dimensions(
    width: int,
    height: int,
    *,
    width_limit: int | None = None,
    max_edge: int | None = None,
) -> tuple[int, int]:
    if (width_limit is None) == (max_edge is None):
        raise ValueError("Exactly one resize limit must be provided")
    limit_scale = (
        width_limit / width
        if width_limit is not None
        else max_edge / max(width, height)  # type: ignore[operator]
    )
    scale = min(1.0, limit_scale)
    return max(1, round(width * scale)), max(1, round(height * scale))


def save_webp(
    source_image: Image.Image,
    target: Path,
    *,
    size: tuple[int, int],
    quality: int,
) -> FileMetadata:
    image = source_image
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    if image.mode != "RGB":
        image = image.convert("RGB")

    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        target,
        format="WEBP",
        quality=quality,
        method=WEBP_METHOD,
        exact=True,
        exif=b"",
        icc_profile=None,
    )
    return image_metadata(target)


def metadata_dict(metadata: FileMetadata) -> dict[str, int | str]:
    return {
        "width": metadata.width,
        "height": metadata.height,
        "byteSize": metadata.byte_size,
        "sha256": metadata.sha256,
    }


def clean_generated_directories(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def build_catalog(
    *,
    source_dir: Path,
    catalog_dir: Path,
    public_dir: Path,
) -> dict[str, Any]:
    sources = discover_source_images(source_dir)
    originals_dir = catalog_dir / "originals"
    thumbs_dir = public_dir / "thumbs"
    previews_dir = public_dir / "previews"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    clean_generated_directories((originals_dir, thumbs_dir, previews_dir))

    items: list[dict[str, Any]] = []
    for source in sources:
        original_filename = f"{source.slug}.png"
        original_path = originals_dir / original_filename
        shutil.copyfile(source.path, original_path)
        original_metadata = image_metadata(original_path)

        with Image.open(original_path) as opened_image:
            source_image = ImageOps.exif_transpose(opened_image).convert("RGB")

            thumbnail: dict[str, Any] = {}
            for width_limit, quality in THUMBNAIL_SPECS:
                target_name = f"{source.slug}-{width_limit}.webp"
                target_path = thumbs_dir / target_name
                target_size = resized_dimensions(
                    source_image.width,
                    source_image.height,
                    width_limit=width_limit,
                )
                generated_metadata = save_webp(
                    source_image,
                    target_path,
                    size=target_size,
                    quality=quality,
                )
                suffix = str(width_limit)
                thumbnail.update(
                    {
                        f"src{suffix}": (
                            f"/images/model-pose-transfer/thumbs/{target_name}"
                        ),
                        f"width{suffix}": generated_metadata.width,
                        f"height{suffix}": generated_metadata.height,
                        f"byteSize{suffix}": generated_metadata.byte_size,
                        f"sha256{suffix}": generated_metadata.sha256,
                    }
                )

            preview_name = f"{source.slug}-{PREVIEW_MAX_EDGE}.webp"
            preview_path = previews_dir / preview_name
            preview_size = resized_dimensions(
                source_image.width,
                source_image.height,
                max_edge=PREVIEW_MAX_EDGE,
            )
            preview_metadata = save_webp(
                source_image,
                preview_path,
                size=preview_size,
                quality=PREVIEW_QUALITY,
            )

        items.append(
            {
                "slug": source.slug,
                "genderGroup": source.gender_group,
                "sourceNumber": source.source_number,
                "sortOrder": source.source_number,
                "enabled": True,
                "label": f"{GROUP_LABELS[source.gender_group]} {source.source_number}",
                "referenceSha256": original_metadata.sha256,
                "originalKey": f"originals/{original_filename}",
                "original": {
                    "filename": original_filename,
                    "mimeType": "image/png",
                    **metadata_dict(original_metadata),
                },
                "thumbnail": thumbnail,
                "preview": {
                    "src": (
                        "/images/model-pose-transfer/previews/"
                        f"{preview_name}"
                    ),
                    **metadata_dict(preview_metadata),
                },
            }
        )

    catalog: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "catalogId": CATALOG_ID,
        "itemCount": len(items),
        "derivativePolicy": {
            "crop": False,
            "upscale": False,
            "format": "image/webp",
            "method": WEBP_METHOD,
            "thumbnails": [
                {"width": width, "quality": quality}
                for width, quality in THUMBNAIL_SPECS
            ],
            "preview": {
                "maxEdge": PREVIEW_MAX_EDGE,
                "quality": PREVIEW_QUALITY,
            },
        },
        "items": items,
    }
    catalog_path = catalog_dir / "catalog.json"
    temporary_catalog_path = catalog_dir / "catalog.json.tmp"
    temporary_catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    try:
        temporary_catalog_path.replace(catalog_path)
    except PermissionError:
        # Some Windows filesystems reject replacing an existing destination.
        catalog_path.unlink(missing_ok=True)
        temporary_catalog_path.replace(catalog_path)
    return catalog


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=repo_root / "backend" / "catalog_assets" / "model-pose-transfer",
    )
    parser.add_argument(
        "--public-dir",
        type=Path,
        default=repo_root / "public" / "images" / "model-pose-transfer",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = build_catalog(
        source_dir=args.source.resolve(),
        catalog_dir=args.catalog_dir.resolve(),
        public_dir=args.public_dir.resolve(),
    )
    print(
        json.dumps(
            {
                "catalogId": catalog["catalogId"],
                "itemCount": catalog["itemCount"],
                "catalogDir": str(args.catalog_dir.resolve()),
                "publicDir": str(args.public_dir.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
