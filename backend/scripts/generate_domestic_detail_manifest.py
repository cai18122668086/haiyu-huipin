"""Refresh only the pinned domestic detail source lock after reviewed edits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> None:
    backend = Path(__file__).resolve().parents[1]
    skill_id = "aurora-domestic-ecommerce-detail-page"
    source = backend / "skill_sources" / skill_id
    target = backend / "app/skill_manifests" / f"{skill_id}.json"
    manifest = json.loads(target.read_text(encoding="utf-8"))
    assert manifest["skill_id"] == skill_id
    files = [
        {
            "path": path.relative_to(source).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(
            source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()
        )
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    manifest["files"] = files
    manifest["content_hash"] = hashlib.sha256(
        "".join(f"{item['path']}\0{item['sha256']}\n" for item in files).encode()
    ).hexdigest()
    version = (source / "VERSION").read_text(encoding="utf-8").strip()
    manifest["policy"]["policy_version"] = f"{skill_id}-v{version}"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "skill_id": skill_id,
                "version": version,
                "files": len(files),
                "content_hash": manifest["content_hash"],
            }
        )
    )


if __name__ == "__main__":
    main()
