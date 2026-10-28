#!/usr/bin/env python3
"""Materialize explicit human-reviewed visual observations into audit inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


IDENTITY_CHECKS = [
    "product_identity_match",
    "color_partition_match",
    "silhouette_match",
    "component_match",
    "relative_geometry_match",
    "material_match",
    "view_evidence_match",
    "text_fact_match",
    "compliance_match",
    "page_role_match",
    "campaign_style_match",
]

DESIGN_CHECKS = [
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    out = Path(args.output_dir)
    identity_dir = out / "identity-page-judgments"
    design_dir = out / "design-page-judgments"
    identity_dir.mkdir(parents=True, exist_ok=True)
    design_dir.mkdir(parents=True, exist_ok=True)

    for page in review["pages"]:
        if set(page["identity_checks"]) != set(IDENTITY_CHECKS):
            raise SystemExit(f"identity checks incomplete on page {page['page_index']}")
        if set(page["design_scores"]) != set(DESIGN_CHECKS):
            raise SystemExit(f"design scores incomplete on page {page['page_index']}")
        identity = {
            "page_index": page["page_index"],
            "generated_image": page["generated_image"],
            "review_basis": page["review_basis"],
            "review_mode": "DIRECT_PIXEL_VISUAL_COMPARISON",
            "observed_evidence": page["identity_observation"],
            "judgment": {
                "checks": page["identity_checks"],
                "errors": page.get("identity_errors", []),
            },
        }
        design_checks = {
            name: {
                "status": "PASS" if page["design_scores"][name] >= 70 else "FAIL",
                "score": page["design_scores"][name],
                "observed_evidence": page["design_observation"],
                "expected_contract": page["expected_design_contract"],
                "error_code": None,
            }
            for name in DESIGN_CHECKS
        }
        design = {
            "page_index": page["page_index"],
            "generated_image": page["generated_image"],
            "treatment_id": page["treatment_id"],
            "checks": design_checks,
            "errors": page.get("design_errors", []),
        }
        (identity_dir / f"page-{page['page_index']:02d}.json").write_text(
            json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (design_dir / f"page-{page['page_index']:02d}.json").write_text(
            json.dumps(design, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    set_judgment = {
        "checks": {
            "cross_page_typography_diversity": {
                "status": "PASS" if review["set_review"]["diversity_score"] >= 70 else "FAIL",
                "score": review["set_review"]["diversity_score"],
                "observed_evidence": review["set_review"]["observed_evidence"],
                "expected_contract": "at least three actual headline structures, two emphasis methods and three graphic-device types",
                "error_code": None,
            },
            "repeated_template_detection": {
                "status": "PASS" if review["set_review"]["repetition_score"] >= 70 else "FAIL",
                "score": review["set_review"]["repetition_score"],
                "observed_evidence": review["set_review"]["observed_evidence"],
                "expected_contract": "no adjacent template reuse and fewer than four pages sharing one structure",
                "error_code": None,
            },
        },
        "errors": [],
    }
    (out / "set-design-judgment.json").write_text(
        json.dumps(set_judgment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "pages": len(review["pages"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
