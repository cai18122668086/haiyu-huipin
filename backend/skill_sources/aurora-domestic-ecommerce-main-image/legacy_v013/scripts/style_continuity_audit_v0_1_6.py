#!/usr/bin/env python3
"""Compile visible-image style judgments into a deterministic hard gate."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "style-continuity-audit-v0.1.6.schema.json"
CHECK_TO_ERROR = {
    "typography_family_match": "TYPOGRAPHY_STYLE_DRIFT",
    "headline_treatment_match": "TYPOGRAPHY_STYLE_DRIFT",
    "color_role_match": "PALETTE_ROLE_DRIFT",
    "graphic_device_family_match": "GRAPHIC_LANGUAGE_DRIFT",
    "photography_finish_match": "PHOTOGRAPHY_FINISH_DRIFT",
    "information_surface_match": "GRAPHIC_LANGUAGE_DRIFT",
    "layout_not_cloned": "LAYOUT_OVERCLONED",
    "product_identity_match": "PRODUCT_IDENTITY_DRIFT",
    "factual_copy_match": "TEXT_FACT_DRIFT",
}
STYLE_ONLY_ERRORS = {
    "TYPOGRAPHY_STYLE_DRIFT",
    "PALETTE_ROLE_DRIFT",
    "GRAPHIC_LANGUAGE_DRIFT",
    "PHOTOGRAPHY_FINISH_DRIFT",
    "LAYOUT_OVERCLONED",
}


def build_style_continuity_audit(
    contract: dict[str, Any], judgments: list[dict[str, Any]]
) -> dict[str, Any]:
    pages = []
    counts: Counter[str] = Counter()
    for judgment in sorted(judgments, key=lambda x: x["page_index"]):
        checks = {
            name: bool(judgment.get(name, False)) for name in CHECK_TO_ERROR
        }
        errors = sorted(
            {CHECK_TO_ERROR[name] for name, passed in checks.items() if not passed}
        )
        counts.update(errors)
        if not errors:
            repair_scope = "NONE"
        elif set(errors).issubset(STYLE_ONLY_ERRORS):
            repair_scope = "STYLE_LAYER_ONLY"
        else:
            repair_scope = "PRODUCT_OR_FACT_REPAIR_REQUIRED"
        pages.append(
            {
                "page_index": judgment["page_index"],
                "file": judgment["file"],
                "status": "PASS" if not errors else "BLOCKED",
                "checks": checks,
                "errors": errors,
                "repair_scope": repair_scope,
            }
        )
    set_gates = {
        "all_pages_style_continuous": all(
            p["checks"]["typography_family_match"]
            and p["checks"]["headline_treatment_match"]
            and p["checks"]["color_role_match"]
            and p["checks"]["graphic_device_family_match"]
            and p["checks"]["photography_finish_match"]
            and p["checks"]["information_surface_match"]
            for p in pages
        ),
        "all_pages_layouts_not_cloned": all(
            p["checks"]["layout_not_cloned"] for p in pages
        ),
        "all_pages_product_identity_match": all(
            p["checks"]["product_identity_match"] for p in pages
        ),
        "all_pages_factual_copy_match": all(
            p["checks"]["factual_copy_match"] for p in pages
        ),
        "failed_versions_cannot_enter_final_set": all(
            p["status"] == "PASS" for p in pages
        ),
        "profile01_semantics_read_false": True,
    }
    result = {
        "schema_version": "style-continuity-audit-0.1.6",
        "request_id": contract["request_id"],
        "status": "PASS" if pages and all(set_gates.values()) else "BLOCKED",
        "baseline_contract_mode": contract["mode"],
        "pages": pages,
        "set_gates": set_gates,
        "error_counts": dict(sorted(counts.items())),
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    ).validate(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--style-token-contract", required=True)
    parser.add_argument("--judgments", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    contract = json.loads(
        Path(args.style_token_contract).read_text(encoding="utf-8")
    )
    judgments = json.loads(Path(args.judgments).read_text(encoding="utf-8"))
    result = build_style_continuity_audit(contract, judgments["pages"])
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "pages": len(result["pages"])}))
    return 0 if result["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
