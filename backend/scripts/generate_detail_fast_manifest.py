"""Regenerate the independent domestic detail fast lock, never original locks."""

import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ORIGINAL = "xinghe-ecommerce-detail-domestic-4"
SKILL_ID = ORIGINAL + "-fast"


def main() -> None:
    root = BACKEND / "skill_sources" / SKILL_ID
    files = sorted(
        (
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        ),
        key=lambda entry: entry["path"],
    )
    source = BACKEND / "app" / "skill_manifests" / f"{ORIGINAL}.json"
    manifest = json.loads(source.read_text(encoding="utf-8"))
    manifest.update(
        skill_id=SKILL_ID,
        files=files,
        content_hash=hashlib.sha256(
            "".join(f"{entry['path']}\0{entry['sha256']}\n" for entry in files).encode()
        ).hexdigest(),
    )
    manifest["policy"]["policy_version"] = "domestic-detail-fast-suite-v1"
    for key in ("input_contract", "output_contract"):
        manifest[key]["schema_id"] = manifest[key]["schema_id"].replace(
            ORIGINAL, SKILL_ID
        )
    target = BACKEND / "app" / "skill_manifests" / f"{SKILL_ID}.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
