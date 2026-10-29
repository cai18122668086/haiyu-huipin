"""Regenerate the independent Amazon fast manifest, leaving the original frozen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ORIGINAL = "xinghe-amazon-visual-suite"
SKILL_ID = ORIGINAL + "-fast"


def main() -> None:
    root = BACKEND / "skill_sources" / SKILL_ID
    files = sorted(
        (
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        ),
        key=lambda row: row["path"],
    )
    directory = BACKEND / "app" / "skill_manifests"
    manifest = json.loads((directory / f"{ORIGINAL}.json").read_text(encoding="utf-8"))
    manifest.update(
        skill_id=SKILL_ID,
        files=files,
        content_hash=hashlib.sha256(
            "".join(f"{row['path']}\0{row['sha256']}\n" for row in files).encode()
        ).hexdigest(),
    )
    manifest["policy"]["policy_version"] = "amazon-visual-suite-fast-v1"
    for name in ("input_contract", "output_contract"):
        manifest[name]["schema_id"] = manifest[name]["schema_id"].replace(
            ORIGINAL, SKILL_ID
        )
    (directory / f"{SKILL_ID}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
