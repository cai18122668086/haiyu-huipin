"""Regenerate the checked-in integrity manifest for the bundled Aurora Skill."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKILL_ID = "aurora-domestic-ecommerce-main-image"
SOURCE = BACKEND / "skill_sources" / SKILL_ID
TARGET = BACKEND / "app" / "skill_manifests" / f"{SKILL_ID}.json"


def main() -> None:
    files = []
    source_files = [item for item in SOURCE.rglob("*") if item.is_file()]
    for path in sorted(
        source_files,
        key=lambda item: item.relative_to(SOURCE).as_posix(),
    ):
        relative = path.relative_to(SOURCE).as_posix()
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    aggregate = hashlib.sha256(
        "".join(f"{item['path']}\0{item['sha256']}\n" for item in files).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "skill-runtime-manifest-v1",
        "content_hash_algorithm": "sha256-ordered-path-nul-file-sha256-lf-v1",
        "ignored_runtime_artifacts": ["**/__pycache__/**", "**/*.pyc"],
        "skill_id": SKILL_ID,
        "content_hash": aggregate,
        "capability": "domestic_main_image",
        "input_contract": {
            "schema_id": "polaris.skill.aurora-domestic-ecommerce-main-image.input.v1",
            "required_fields": [
                "product_asset_ids",
                "product_name",
                "category",
                "selling_points",
            ],
        },
        "output_contract": {
            "schema_id": "polaris.skill.aurora-domestic-ecommerce-main-image.output.v1",
            "required_fields": ["result_asset_refs", "usage", "final_manifest"],
        },
        "policy": {
            "schema_version": "skill-runtime-policy-v1",
            "auto_review_regeneration_allowed": False,
            "successful_page_auto_redraw_allowed": False,
            "safe_expansion_mode": "disabled",
            "policy_version": "aurora-main-image-harness-0.1.17",
            "active_profile_id": None,
            "profiles": [],
            "iteration": None,
            "safe_expansion": None,
        },
        "provider_type": "mock",
        "cost_limit_micros": 0,
        "real_provider_request_limit": 0,
        "files": files,
    }
    TARGET.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
