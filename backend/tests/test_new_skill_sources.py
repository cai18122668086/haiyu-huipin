# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SOURCES = REPO_ROOT / "backend" / "skill_sources"
MANIFESTS = REPO_ROOT / "backend" / "app" / "skill_manifests"
ZIP_DIR = Path(
    os.environ.get(
        "NEW_SKILL_ZIP_DIR",
        "D:/Users/Administrator/Desktop/\u7535\u5546\u7d20\u6750\u8c03\u67e5/\u4e3b\u8981\u529f\u80fd",
    )
)
BASELINES = json.loads(r"""{
    "xinghe-wanneng-shengtu-3-0": {
        "zip_name": "星河万能生图3.0.zip",
        "zip_sha256": "98724520465ec3564756b0f658531532178517463d05f49c38237972eae29fb0",
        "source_root": "xinghe-wanneng-shengtu-3-0",
        "rename_only": false,
        "controlled_differences": {},
        "required_preserved_paths": []
    },
    "xinghe-reference-image-remix-3": {
        "zip_name": "星河参考图二创3.0.zip",
        "zip_sha256": "3bc9f9b1c28123d961547bc8bc5c0f56cd001855770c7c0dcf2ee756452e0d2f",
        "source_root": "xinghe-reference-image-remix-3",
        "rename_only": false,
        "controlled_differences": {},
        "required_preserved_paths": [
            "scripts/xinghe-image-fallback.mjs"
        ]
    },
    "xinghe-ecommerce-creative-image": {
        "zip_name": "星河创意图3.0.zip",
        "zip_sha256": "790535f0738217d48c723a14c79a36c12fa436786b84daee2c41fb9f305cb277",
        "source_root": "xinghe-ecommerce-creative-image",
        "rename_only": false,
        "controlled_differences": {},
        "required_preserved_paths": []
    },
    "xinghe-ecommerce-detail-domestic-3": {
        "zip_name": "星河电商详情3.0.zip",
        "zip_sha256": "578ec9286e6d52c4d87da2b6832a0e846b70ef43c17d46bf13c58fc538e96002",
        "source_root": "星河电商详情",
        "rename_only": true,
        "controlled_differences": {
            "SKILL.md": {
                "kind": "frontmatter_name",
                "source_sha256": "c1994ade053e0a525ac393c7d0120cb6a304a81242a1c29fef3068b40398442d",
                "installed_sha256": "7a5fcb5d6dee85d64643214a907fdde20dce8c48abd939e063d4dad167ffe8e2"
            },
            "agents/openai.yaml": {
                "kind": "default_prompt_skill_token",
                "source_sha256": "b9b41e9bca2f58166fa0cba0e1ab09ffe3f0be4036164ad1d74ee274c654421f",
                "installed_sha256": "e4283b04a08131aabd3a008c71e34204e126f814ce5fe6f18b536046db5028f1"
            }
        },
        "required_preserved_paths": []
    },
    "xinghe-crossborder-detail-3": {
        "zip_name": "星河跨境电商详情3.0.zip",
        "zip_sha256": "a90994f402fc1ae989dbdac027f8e2dd4fcc4ee529ae75b4ca57f7cdaeb03879",
        "source_root": "xinghe-crossborder-detail-3",
        "rename_only": false,
        "controlled_differences": {},
        "required_preserved_paths": [
            ".env.example",
            "scripts/generate_batch.py",
            "scripts/generate_image.py"
        ]
    },
    "xinghe-amazon-visual-suite": {
        "zip_name": "星河亚马逊主副图A+.zip",
        "zip_sha256": "5e8c534861c3b22f161a202acab1ff110427a281e6a8528bb10afa976fa463c9",
        "source_root": "xinghe-amazon-visual-suite",
        "rename_only": false,
        "controlled_differences": {},
        "required_preserved_paths": [
            "scripts/make_contact_sheet.py",
            "scripts/visual_size_helper.py"
        ]
    }
}""")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_files(zip_path: Path) -> tuple[str, dict[str, bytes]]:
    with ZipFile(zip_path) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        paths = [PurePosixPath(entry.filename.replace("\\", "/")) for entry in entries]
        roots = {path.parts[0] for path in paths if path.parts}
        assert len(roots) == 1
        root = next(iter(roots))
        files = {}
        for entry, member_path in zip(entries, paths, strict=True):
            assert not member_path.is_absolute()
            assert ".." not in member_path.parts
            assert len(member_path.parts) >= 2
            relative = PurePosixPath(*member_path.parts[1:]).as_posix()
            files[relative] = archive.read(entry)
    return root, files


def _installed_files(skill_id: str) -> dict[str, bytes]:
    root = SKILL_SOURCES / skill_id
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _expected_domestic_file(path: str, source: bytes) -> bytes:
    if path == "SKILL.md":
        old = b"name: xinghe-ecommerce-detail"
        new = b"name: xinghe-ecommerce-detail-domestic-3"
        assert source.count(old) == 1
        return source.replace(old, new, 1)
    if path == "agents/openai.yaml":
        prefix = b'default_prompt: "Use $'
        suffix = b" to turn"
        start = source.find(prefix)
        assert start >= 0
        token_start = start + len(prefix)
        token_end = source.find(suffix, token_start)
        assert token_end >= 0
        return (
            source[:token_start]
            + b"xinghe-ecommerce-detail-domestic-3"
            + source[token_end:]
        )
    return source


@pytest.mark.parametrize("skill_id", sorted(BASELINES))
def test_source_zip_hash_tree_and_bytes(skill_id: str) -> None:
    baseline = BASELINES[skill_id]
    zip_path = ZIP_DIR / baseline["zip_name"]
    if not zip_path.is_file():
        pytest.skip(f"Source ZIP unavailable: {zip_path}")
    assert _sha256(zip_path.read_bytes()) == baseline["zip_sha256"]
    source_root, source_files = _zip_files(zip_path)
    assert source_root == baseline["source_root"]
    installed = _installed_files(skill_id)
    assert set(installed) == set(source_files)
    for path, source_bytes in source_files.items():
        expected = (
            _expected_domestic_file(path, source_bytes)
            if baseline["rename_only"]
            else source_bytes
        )
        assert installed[path] == expected, path


@pytest.mark.parametrize("skill_id", sorted(BASELINES))
def test_manifest_locks_complete_installed_tree(skill_id: str) -> None:
    manifest = json.loads((MANIFESTS / f"{skill_id}.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "skill-runtime-manifest-v1"
    assert (
        manifest["content_hash_algorithm"]
        == "sha256-ordered-path-nul-file-sha256-lf-v1"
    )
    assert manifest["ignored_runtime_artifacts"] == ["**/__pycache__/**", "**/*.pyc"]
    assert manifest["skill_id"] == skill_id
    installed = _installed_files(skill_id)
    expected_files = [
        {"path": path, "sha256": _sha256(data)}
        for path, data in sorted(installed.items())
    ]
    assert manifest["files"] == expected_files
    canonical = "".join(
        f"{item['path']}\0{item['sha256'].lower()}\n" for item in expected_files
    )
    assert manifest["content_hash"] == _sha256(canonical.encode("utf-8"))


def test_domestic_manifest_records_only_controlled_rename() -> None:
    skill_id = "xinghe-ecommerce-detail-domestic-3"
    baseline = BASELINES[skill_id]
    manifest = json.loads((MANIFESTS / f"{skill_id}.json").read_text(encoding="utf-8"))
    assert manifest["rename_only"] is True
    assert manifest["source_zip_sha256"] == baseline["zip_sha256"]
    assert manifest["source_root"] == baseline["source_root"]
    differences = {item["path"]: item for item in manifest["controlled_differences"]}
    assert set(differences) == {"SKILL.md", "agents/openai.yaml"}
    assert differences["SKILL.md"]["kind"] == "frontmatter_name"
    assert differences["agents/openai.yaml"]["kind"] == "default_prompt_skill_token"
    for path, metadata in baseline["controlled_differences"].items():
        assert differences[path]["source_sha256"] == metadata["source_sha256"]
        assert differences[path]["installed_sha256"] == metadata["installed_sha256"]


@pytest.mark.parametrize("skill_id", sorted(BASELINES))
def test_scripts_and_dotenv_entries_are_preserved(skill_id: str) -> None:
    baseline = BASELINES[skill_id]
    installed = _installed_files(skill_id)
    for path in baseline["required_preserved_paths"]:
        assert path in installed
