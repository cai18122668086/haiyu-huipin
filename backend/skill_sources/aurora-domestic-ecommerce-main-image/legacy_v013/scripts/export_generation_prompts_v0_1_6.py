#!/usr/bin/env python3
"""Export v0.1.6 prompt-only review files without invoking generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiled-output", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    compiled = json.loads(Path(args.compiled_output).read_text(encoding="utf-8"))
    if compiled.get("status") != "READY_FOR_CONTROLLED_GENERATION":
        raise SystemExit("compiled output is not ready")
    if compiled.get("generation_performed") is not False:
        raise SystemExit("prompt-only export requires generation_performed=false")
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for page in compiled["pages"]:
        index = int(page["index"])
        path = out / f"page-{index:02d}.txt"
        path.write_text(page["generation_prompt"].strip() + "\n", encoding="utf-8")
        manifest.append(
            {
                "index": index,
                "page_role": page["page_role"],
                "layout_archetype": page["design_prototype_overlay"][
                    "layout_archetype"
                ],
                "style_token_mode": page["campaign_style_tokens"]["mode"],
                "style_reference_scope": page["campaign_style_tokens"][
                    "style_reference_policy"
                ]["scope"],
                "path": str(path.resolve()),
                "previous_generated_images_used": False,
                "generation_performed": False,
            }
        )
    manifest_path = out / "prompt-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "generation-prompt-manifest-0.1.6",
                "request_id": compiled["request_id"],
                "status": "PROMPT_ONLY_READY_FOR_USER_REVIEW",
                "generation_performed": False,
                "profile01_semantics_read": False,
                "prompts": manifest,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "prompts": len(manifest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
