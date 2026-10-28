#!/usr/bin/env python3
"""Compile v0.1.3 identity-preserving prompts with concrete design execution."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import jsonschema

from campaign_design_v0_1_3 import (
    TYPOGRAPHY_GRAPHIC_DESIGN_LOCK,
    build_campaign_design_contract,
    design_prompt_block,
)
from compile_harness_v0_1_2 import compile_raw_v012
from identity_framework_v0_1_2 import load, sha256, write


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA = ROOT / "schemas" / "compiled-output-v0.1.3.schema.json"
VERSION = "domestic-ecommerce-prompt-compiler-0.1.3"


def compile_raw_v013(
    raw: dict[str, Any], identity_contract: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    base = compile_raw_v012(raw, identity_contract)
    design = build_campaign_design_contract(raw, identity_contract, base["pages"])
    output = copy.deepcopy(base)
    output["schema_version"] = "domestic-ecommerce-compiled-output-0.1.3"
    output["compiler_version"] = VERSION
    output["campaign_design_contract"] = {
        "schema_version": design["schema_version"],
        "request_id": design["request_id"],
        "status": design["status"],
        "source_type_for_new_typography_defaults": "ENGINEERED_DEFAULT",
    }
    by_index = {x["index"]: x for x in design["pages"]}
    for page in output["pages"]:
        typography = by_index[page["index"]]
        page["typography_plan"] = typography
        page["generation_prompt"] = (
            page["generation_prompt"]
            + "\n\n"
            + design_prompt_block(typography, design)
            + "\n\n"
            + TYPOGRAPHY_GRAPHIC_DESIGN_LOCK
        )
        page["visual_plan"]["fields"]["typography.treatment_id"] = typography[
            "treatment_id"
        ]
        page["visual_plan"]["fields"]["typography.levels"] = typography[
            "typography_levels"
        ]
        page["visual_plan"]["fields"]["typography.colors"] = typography["colors"]
        page["visual_plan"]["fields"]["typography.keyword_emphasis"] = typography[
            "keyword_emphasis"
        ]
        page["visual_plan"]["fields"]["typography.graphic_devices"] = typography[
            "graphic_devices"
        ]
        page["field_sources"]["typography.treatment_id"] = "ENGINEERED_DEFAULT"
        page["field_sources"]["typography.levels"] = "ENGINEERED_DEFAULT"
        page["field_sources"]["typography.colors"] = "ENGINEERED_DEFAULT"
        page["field_sources"]["typography.keyword_emphasis"] = "ENGINEERED_DEFAULT"
        page["field_sources"]["typography.graphic_devices"] = "ENGINEERED_DEFAULT"
        page["audit_contract"]["copy_planner"] = typography["copy_plan"]
        page["audit_contract"]["typography_graphic_contract"] = typography
        page["audit_contract"]["typography_graphic_design_lock"] = {
            "source_type": "ENGINEERED_DEFAULT",
            "position": "GENERATION_PROMPT_SUFFIX",
            "module_name": "TYPOGRAPHY & GRAPHIC DESIGN LOCK",
            "future_replacement_policy": "replace parameters with dedicated image-analysis evidence when available; never relabel this baseline as EVIDENCE_DERIVED",
        }
        page["audit_contract"]["locks"]["campaign_style"][
            "typography_graphic_execution"
        ] = {
            "campaign_contract": raw["request_id"] + ":campaign-design",
            "treatment_id": typography["treatment_id"],
            "colors": typography["colors"],
            "font_system": design["font_system"],
            "source_type": "ENGINEERED_DEFAULT",
        }
        page["audit_contract"]["locks"]["design_strength"][
            "typography_variation"
        ] = {
            "design_fingerprint": typography["design_fingerprint"],
            "headline_structure": typography["headline_structure"],
            "emphasis_method": typography["keyword_emphasis"]["emphasis_method"],
            "graphic_devices": typography["graphic_devices"],
            "text_density": typography["text_density"],
            "source_type": "ENGINEERED_DEFAULT",
        }
        page["gates"]["campaign_design_contract_ready"] = design["status"] == "READY"
        page["gates"]["copy_is_fact_bound_or_safe_compression"] = typography[
            "copy_plan"
        ]["copy_source"] in {"USER_FACT", "SAFE_COMPRESSION_OF_USER_FACT"}
        page["gates"]["copy_integrity_gate"] = (
            typography["copy_plan"]["copy_integrity_gate"]["status"] == "PASS"
        )
        page["gates"]["typography_does_not_occlude_product"] = (
            typography["layout_policy"]["product_occlusion"] == "FORBIDDEN"
        )
    output["set_gates"].update(design["set_gates"])
    output["status"] = (
        "READY_FOR_CONTROLLED_GENERATION"
        if base["status"] == "READY_FOR_CONTROLLED_GENERATION"
        and design["status"] == "READY"
        and all(
            value
            for key, value in output["set_gates"].items()
            if key != "applicable"
        )
        and all(all(p["gates"].values()) for p in output["pages"])
        else "BLOCKED"
    )
    jsonschema.Draft202012Validator(load(OUTPUT_SCHEMA)).validate(output)
    return output, design


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--campaign-design-contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = load(args.input)
    identity = load(args.identity_contract)
    output, design = compile_raw_v013(raw, identity)
    output["product_identity_contract"]["sha256"] = sha256(args.identity_contract)
    write(args.campaign_design_contract, design)
    output["campaign_design_contract"]["sha256"] = sha256(
        args.campaign_design_contract
    )
    write(args.output, output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "pages": len(output["pages"]),
                "treatments": [x["treatment_id"] for x in design["pages"]],
                "set_gates": design["set_gates"],
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )
    return 0 if output["status"] == "READY_FOR_CONTROLLED_GENERATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
