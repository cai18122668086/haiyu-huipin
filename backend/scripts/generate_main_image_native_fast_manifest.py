"""Seed the independent source once, or refresh its reviewed runtime manifest.

An existing checkout needs no private archive: run without arguments after
editing the independent Skill. --source-zip only imports a pristine source tree
into a previously absent destination; it never overwrites reviewed source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

BACKEND = Path(__file__).resolve().parents[1]
SKILL_ID = "aurora-domestic-ecommerce-main-image-native-fast"
ARCHIVE_SHA256 = "0c5d3daa3889d0ff5964114996a49c428dce9b200b3591d9b690991170869d3c"
ARCHIVE_ROOT = "aurora-domestic-ecommerce-main-image"
POLICY_VERSION = "aurora-main-image-native-fast-v1"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seed_source(source: Path, root: Path) -> None:
    if root.exists():
        raise ValueError("Independent Skill already exists; refusing to overwrite it")
    if sha256(source.read_bytes()) != ARCHIVE_SHA256:
        raise ValueError("The original Skill archive SHA-256 does not match")
    baseline_path = BACKEND / "source_manifests" / f"{ARCHIVE_ROOT}-v20-source.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = {
        entry.partition("  ")[2]: entry.partition("  ")[0]
        for entry in baseline["files"]
    }
    files: dict[str, bytes] = {}
    with ZipFile(source) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.parts[0] != ARCHIVE_ROOT
                or "\\" in entry.filename
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError("Unsafe path or symlink in source archive")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if relative in files or relative not in expected:
                raise ValueError("Unexpected or duplicate source file")
            data = archive.read(entry)
            if sha256(data) != expected[relative]:
                raise ValueError(f"Source bytes differ from provenance: {relative}")
            files[relative] = data
    if set(files) != set(expected) or len(files) != 24:
        raise ValueError("The source archive must contain the original 24 files")
    root.mkdir(parents=True)
    for relative, data in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    provenance = {
        "skill_id": SKILL_ID,
        "source_skill_id": ARCHIVE_ROOT,
        "source_version": "2.0.0",
        "source_archive": baseline["source_archive"],
        "archive_sha256": ARCHIVE_SHA256,
        "source_file_count": 24,
        "all_24_files_match_bundled_v20": True,
        "source_content_hash": baseline["source_content_hash"],
        "files": [
            {"path": name, "sha256": expected[name]} for name in sorted(expected)
        ],
        "preserved_without_changes": [
            "scripts/runtime/compiler.py",
            "scripts/runtime/style.py",
            "scripts/runtime/identity.py",
            "references/**",
        ],
        "runtime_policy_version": POLICY_VERSION,
    }
    (BACKEND / "source_manifests" / f"{SKILL_ID}-source.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def update_manifest(root: Path) -> None:
    if not (root / "SKILL.md").is_file():
        raise ValueError(
            "Missing independent Skill; seed it explicitly with --source-zip"
        )
    files = sorted(
        (
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256(path.read_bytes()),
            }
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        ),
        key=lambda row: row["path"],
    )
    source = BACKEND / "app/skill_manifests/aurora-domestic-ecommerce-main-image.json"
    manifest = json.loads(source.read_text(encoding="utf-8"))
    manifest.update(
        skill_id=SKILL_ID,
        capability="domestic_main_image",
        files=files,
        content_hash=sha256(
            "".join(f"{row['path']}\0{row['sha256']}\n" for row in files).encode()
        ),
    )
    manifest["policy"]["policy_version"] = POLICY_VERSION
    manifest["output_contract"]["required_fields"] = ["result_asset_refs", "usage"]
    for name in ("input_contract", "output_contract"):
        manifest[name]["schema_id"] = f"polaris.skill.{SKILL_ID}.{name}.v1"
    (BACKEND / "app/skill_manifests" / f"{SKILL_ID}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", type=Path)
    args = parser.parse_args()
    root = BACKEND / "skill_sources" / SKILL_ID
    if args.source_zip is not None:
        seed_source(args.source_zip, root)
    update_manifest(root)


if __name__ == "__main__":
    main()
