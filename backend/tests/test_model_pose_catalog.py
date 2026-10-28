from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = REPO_ROOT / "backend" / "catalog_assets" / "model-pose-transfer"
CATALOG_PATH = CATALOG_ROOT / "catalog.json"
PUBLIC_ROOT = REPO_ROOT / "public"
EXPECTED_MALE = [f"male-{number:03d}" for number in range(1, 22)]
EXPECTED_FEMALE_NUMBERS = [*range(1, 34), 35, 36, 38, 39, 40, 41]
EXPECTED_FEMALE = [f"female-{number:03d}" for number in EXPECTED_FEMALE_NUMBERS]


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_file_metadata(
    path: Path,
    metadata: dict[str, Any],
    *,
    expected_format: str,
) -> None:
    assert path.is_file(), path
    assert path.stat().st_size == metadata["byteSize"]
    assert sha256_file(path) == metadata["sha256"]
    with Image.open(path) as image:
        assert image.format == expected_format
        assert image.size == (metadata["width"], metadata["height"])


def test_catalog_has_the_explicit_stable_60_pose_set() -> None:
    catalog = load_catalog()
    assert catalog["schemaVersion"] == "model-pose-catalog-v1"
    assert catalog["catalogId"] == "ecommerce-model-pose-transfer"
    assert catalog["itemCount"] == 60

    items = catalog["items"]
    assert len(items) == 60
    male = [item for item in items if item["genderGroup"] == "male"]
    female = [item for item in items if item["genderGroup"] == "female"]
    assert [item["slug"] for item in male] == EXPECTED_MALE
    assert [item["slug"] for item in female] == EXPECTED_FEMALE
    assert len(male) == 21
    assert len(female) == 39
    assert "female-034" not in {item["slug"] for item in items}
    assert "female-037" not in {item["slug"] for item in items}
    assert len({item["slug"] for item in items}) == 60


def test_catalog_originals_and_derivatives_match_their_manifest() -> None:
    catalog = load_catalog()
    assert catalog["derivativePolicy"] == {
        "crop": False,
        "upscale": False,
        "format": "image/webp",
        "method": 6,
        "thumbnails": [
            {"width": 320, "quality": 82},
            {"width": 640, "quality": 84},
        ],
        "preview": {"maxEdge": 960, "quality": 86},
    }

    for item in catalog["items"]:
        original_path = CATALOG_ROOT / item["originalKey"]
        original = item["original"]
        assert item["originalKey"] == f"originals/{item['slug']}.png"
        assert original["filename"] == f"{item['slug']}.png"
        assert original["mimeType"] == "image/png"
        assert item["referenceSha256"] == original["sha256"]
        assert_file_metadata(original_path, original, expected_format="PNG")

        thumbnail = item["thumbnail"]
        for width_limit in (320, 640):
            suffix = str(width_limit)
            src = thumbnail[f"src{suffix}"]
            expected_src = (
                f"/images/model-pose-transfer/thumbs/{item['slug']}-{width_limit}.webp"
            )
            assert src == expected_src
            metadata = {
                "width": thumbnail[f"width{suffix}"],
                "height": thumbnail[f"height{suffix}"],
                "byteSize": thumbnail[f"byteSize{suffix}"],
                "sha256": thumbnail[f"sha256{suffix}"],
            }
            assert metadata["width"] == min(width_limit, original["width"])
            expected_height = round(
                original["height"] * metadata["width"] / original["width"]
            )
            assert metadata["height"] == expected_height
            assert_file_metadata(
                PUBLIC_ROOT / src.removeprefix("/"),
                metadata,
                expected_format="WEBP",
            )

        preview = item["preview"]
        expected_preview_src = (
            f"/images/model-pose-transfer/previews/{item['slug']}-960.webp"
        )
        assert preview["src"] == expected_preview_src
        assert max(preview["width"], preview["height"]) == min(
            960, max(original["width"], original["height"])
        )
        assert preview["width"] <= original["width"]
        assert preview["height"] <= original["height"]
        assert_file_metadata(
            PUBLIC_ROOT / preview["src"].removeprefix("/"),
            preview,
            expected_format="WEBP",
        )


def test_catalog_asset_directories_have_no_stale_or_public_original_files() -> None:
    catalog = load_catalog()
    expected_slugs = {item["slug"] for item in catalog["items"]}
    original_names = {
        path.name for path in (CATALOG_ROOT / "originals").iterdir() if path.is_file()
    }
    thumbnail_names = {
        path.name
        for path in (
            PUBLIC_ROOT / "images" / "model-pose-transfer" / "thumbs"
        ).iterdir()
        if path.is_file()
    }
    preview_names = {
        path.name
        for path in (
            PUBLIC_ROOT / "images" / "model-pose-transfer" / "previews"
        ).iterdir()
        if path.is_file()
    }

    assert original_names == {f"{slug}.png" for slug in expected_slugs}
    assert thumbnail_names == {
        f"{slug}-{width}.webp" for slug in expected_slugs for width in (320, 640)
    }
    assert preview_names == {f"{slug}-960.webp" for slug in expected_slugs}
    assert not list((PUBLIC_ROOT / "images" / "model-pose-transfer").rglob("*.png"))
