#!/usr/bin/env python3
"""Build a hard-gated post-generation typography and graphic design audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import jsonschema

from identity_framework_v0_1_2 import load, sha256, write


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "typography-design-audit-v0.1.3.schema.json"
CHECKS = [
    "typography_legibility",
    "typography_hierarchy_quality",
    "keyword_emphasis_visible",
    "accent_color_application",
    "numeric_hierarchy_quality",
    "headline_treatment_matches_contract",
    "graphic_device_execution",
    "copy_density_match",
    "text_product_separation",
    "page_role_visual_expression",
    "commercial_visual_impact",
]
SET_CHECKS = ["cross_page_typography_diversity", "repeated_template_detection"]
ERRORS = {
    "TYPOGRAPHY_TOO_GENERIC",
    "ALL_BLACK_HEADLINE_SET",
    "HEADLINE_TREATMENT_REPEATED",
    "KEYWORD_NOT_EMPHASIZED",
    "WEAK_NUMERIC_HIERARCHY",
    "COPY_DENSITY_TOO_HIGH",
    "COPY_DENSITY_TOO_LOW",
    "TEXT_CONTRAST_WEAK",
    "GRAPHIC_DEVICE_MISSING",
    "PAGE_ROLE_VISUALLY_UNCLEAR",
    "PROMOTION_IMPACT_TOO_WEAK",
    "TEMPLATE_REPETITION",
    "TEXT_OCCLUDES_PRODUCT",
    "DESIGN_STRENGTH_BELOW_THRESHOLD",
    "TEXT_HALLUCINATION",
    "PRODUCT_IDENTITY_DRIFT",
}
HARD_ERRORS = {
    "ALL_BLACK_HEADLINE_SET",
    "HEADLINE_TREATMENT_REPEATED",
    "WEAK_NUMERIC_HIERARCHY",
    "TEXT_OCCLUDES_PRODUCT",
    "TEXT_HALLUCINATION",
    "PAGE_ROLE_VISUALLY_UNCLEAR",
    "PRODUCT_IDENTITY_DRIFT",
}


def _check(value: dict[str, Any], name: str) -> dict[str, Any]:
    raw = value.get(name, {})
    score = max(0.0, min(100.0, float(raw.get("score", 0))))
    status = "PASS" if raw.get("status") == "PASS" and score >= 70 else "FAIL"
    error = raw.get("error_code")
    if error not in ERRORS:
        error = None
    return {
        "status": status,
        "score": score,
        "observed_evidence": str(raw.get("observed_evidence", "")),
        "expected_contract": str(raw.get("expected_contract", "")),
        "error_code": error,
    }


def build_audit(
    request_id: str,
    campaign_contract_path: str,
    judgments: list[dict[str, Any]],
    set_judgment: dict[str, Any],
    identity_status: str,
    factual_status: str,
) -> dict[str, Any]:
    pages = []
    errors: set[str] = set()
    page_scores = []
    for item in sorted(judgments, key=lambda x: x["page_index"]):
        checks = {name: _check(item.get("checks", {}), name) for name in CHECKS}
        page_errors = sorted(
            {
                x["error_code"]
                for x in checks.values()
                if x["status"] == "FAIL" and x["error_code"]
            }
            | {x for x in item.get("errors", []) if x in ERRORS}
        )
        errors.update(page_errors)
        page_score = round(sum(x["score"] for x in checks.values()) / len(checks), 2)
        page_scores.append(page_score)
        pages.append(
            {
                "page_index": item["page_index"],
                "generated_image": str(Path(item["generated_image"]).resolve()),
                "treatment_id": item["treatment_id"],
                "checks": checks,
                "page_score": page_score,
                "errors": page_errors,
                "status": "PASS"
                if all(x["status"] == "PASS" for x in checks.values()) and not page_errors
                else "BLOCKED",
            }
        )
    set_checks = {
        name: _check(set_judgment.get("checks", {}), name) for name in SET_CHECKS
    }
    set_errors = {
        x["error_code"]
        for x in set_checks.values()
        if x["status"] == "FAIL" and x["error_code"]
    } | {x for x in set_judgment.get("errors", []) if x in ERRORS}
    errors.update(x for x in set_errors if x)
    category_scores = {
        "typography_hierarchy": round(
            sum(p["checks"]["typography_hierarchy_quality"]["score"] for p in pages)
            / len(pages),
            2,
        ),
        "keyword_emphasis": round(
            sum(p["checks"]["keyword_emphasis_visible"]["score"] for p in pages)
            / len(pages),
            2,
        ),
        "page_role_fit": round(
            sum(p["checks"]["page_role_visual_expression"]["score"] for p in pages)
            / len(pages),
            2,
        ),
        "set_variation": round(
            sum(x["score"] for x in set_checks.values()) / len(set_checks), 2
        ),
        "graphic_devices": round(
            sum(p["checks"]["graphic_device_execution"]["score"] for p in pages)
            / len(pages),
            2,
        ),
        "color_system": round(
            sum(p["checks"]["accent_color_application"]["score"] for p in pages)
            / len(pages),
            2,
        ),
        "legibility": round(
            sum(p["checks"]["typography_legibility"]["score"] for p in pages)
            / len(pages),
            2,
        ),
        "product_text_separation": round(
            sum(p["checks"]["text_product_separation"]["score"] for p in pages)
            / len(pages),
            2,
        ),
    }
    weights = {
        "typography_hierarchy": 0.20,
        "keyword_emphasis": 0.15,
        "page_role_fit": 0.15,
        "set_variation": 0.15,
        "graphic_devices": 0.10,
        "color_system": 0.10,
        "legibility": 0.10,
        "product_text_separation": 0.05,
    }
    score = round(sum(category_scores[k] * weights[k] for k in weights), 2)
    if score < 80:
        errors.add("DESIGN_STRENGTH_BELOW_THRESHOLD")
    identity_pass = identity_status == "PASS"
    factual_pass = factual_status == "PASS"
    page_pass = all(x["status"] == "PASS" for x in pages)
    set_pass = all(x["status"] == "PASS" for x in set_checks.values())
    hard_pass = not (errors & HARD_ERRORS)
    ready = identity_pass and factual_pass and page_pass and set_pass and score >= 80 and hard_pass
    result = {
        "schema_version": "typography-design-audit-0.1.3",
        "request_id": request_id,
        "campaign_design_contract_sha256": sha256(campaign_contract_path),
        "pages": pages,
        "set_checks": set_checks,
        "score_breakdown": {
            k: {"score": category_scores[k], "weight": int(weights[k] * 100)}
            for k in weights
        },
        "design_score": score,
        "errors": sorted(errors),
        "identity_audit": "PASS" if identity_pass else "FAIL",
        "factual_audit": "PASS" if factual_pass else "FAIL",
        "typography_design_audit": "PASS" if page_pass and score >= 80 and hard_pass else "FAIL",
        "set_diversity_audit": "PASS" if set_pass else "FAIL",
        "status": "READY_FOR_NEXT_CONTROLLED_PRODUCT_TEST" if ready else "BLOCKED",
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(load(SCHEMA)).validate(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--campaign-design-contract", required=True)
    parser.add_argument("--judgment", action="append", required=True)
    parser.add_argument("--set-judgment", required=True)
    parser.add_argument("--identity-status", choices=["PASS", "FAIL"], required=True)
    parser.add_argument("--factual-status", choices=["PASS", "FAIL"], required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = build_audit(
        args.request_id,
        args.campaign_design_contract,
        [load(x) for x in args.judgment],
        load(args.set_judgment),
        args.identity_status,
        args.factual_status,
    )
    write(args.out, result)
    print(json.dumps({"status": result["status"], "design_score": result["design_score"]}))
    return 0 if result["status"] == "READY_FOR_NEXT_CONTROLLED_PRODUCT_TEST" else 3


if __name__ == "__main__":
    raise SystemExit(main())
