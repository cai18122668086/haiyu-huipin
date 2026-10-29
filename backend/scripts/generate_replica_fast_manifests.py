"""Regenerate only the independent fast-suite manifests, never original locks."""

import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def main() -> None:
    for original in (
        "aurora-ecommerce-reference-remix",
        "all-category-style-benchmark-still",
    ):
        skill_id = original + "-fast"
        root = BACKEND / "skill_sources" / skill_id
        files = sorted(
            (
                {
                    "path": p.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
                for p in root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            ),
            key=lambda f: f["path"],
        )
        source = BACKEND / "app" / "skill_manifests" / f"{original}.json"
        manifest = json.loads(source.read_text(encoding="utf-8"))
        manifest.update(
            skill_id=skill_id,
            files=files,
            content_hash=hashlib.sha256(
                "".join(f"{f['path']}\0{f['sha256']}\n" for f in files).encode()
            ).hexdigest(),
        )
        manifest["policy"]["policy_version"] = "replica-fast-suite-v1"
        for key in ("input_contract", "output_contract"):
            manifest[key]["schema_id"] = manifest[key]["schema_id"].replace(
                original, skill_id
            )
        target = BACKEND / "app" / "skill_manifests" / f"{skill_id}.json"
        target.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
