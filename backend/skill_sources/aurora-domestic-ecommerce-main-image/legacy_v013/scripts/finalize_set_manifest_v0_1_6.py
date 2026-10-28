#!/usr/bin/env python3
"""Write a final five-page manifest only from visually accepted assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_final_manifest(
    request_id: str,
    audit: dict[str, Any],
    selected: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
) -> dict[str, Any]:
    audit_by_index = {p["page_index"]: p for p in audit["pages"]}
    final_pages = []
    if audit["status"] != "PASS":
        status = "BLOCKED"
    else:
        status = "FINAL_SET_SELECTED"
        for page in sorted(selected, key=lambda x: x["page_index"]):
            evidence = audit_by_index.get(page["page_index"])
            path = Path(page["file"]).resolve()
            if (
                evidence is None
                or evidence["status"] != "PASS"
                or not path.is_file()
            ):
                status = "BLOCKED"
                continue
            final_pages.append(
                {
                    "page_index": page["page_index"],
                    "page_role": page["page_role"],
                    "file": str(path),
                    "sha256": _sha256(path),
                }
            )
        if len(final_pages) != 5:
            status = "BLOCKED"
    return {
        "schema_version": "controlled-generation-set-manifest-0.1.6",
        "request_id": request_id,
        "status": status,
        "final_pages": final_pages,
        "excluded_generated_assets": excluded,
        "style_continuity_audit_status": audit["status"],
        "generation_receipt_is_not_quality_acceptance": True,
        "profile01_semantics_read": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--style-continuity-audit", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    audit = json.loads(
        Path(args.style_continuity_audit).read_text(encoding="utf-8")
    )
    selection = json.loads(Path(args.selection).read_text(encoding="utf-8"))
    result = build_final_manifest(
        args.request_id,
        audit,
        selection["selected"],
        selection.get("excluded", []),
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "pages": len(result["final_pages"])}))
    return 0 if result["status"] == "FINAL_SET_SELECTED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
