#!/usr/bin/env python3
"""Compile v0.1.7 with opt-in durable-goods experimental visual grammar."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import jsonschema

from compile_harness_v0_1_6 import compile_raw_v016
from durable_goods_experimental_pattern_v0_1_7 import (
    build_experimental_pattern_contract,
    experimental_pattern_prompt_block,
)
from identity_framework_v0_1_2 import load, sha256, write


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA = ROOT / "schemas" / "compiled-output-v0.1.7.schema.json"
VERSION = "domestic-ecommerce-prompt-compiler-0.1.7"


def compile_raw_v017(
    raw: dict[str, Any], identity_contract: dict[str, Any]
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    # The v0.1.7 opt-in is an overlay-only control. Keep it out of the
    # frozen v0.1.1-v0.1.6 planner interface so the new field cannot alter
    # legacy planning or evidence-rule matching.
    base_raw = copy.deepcopy(raw)
    base_preference = base_raw.get("campaign_preference")
    if isinstance(base_preference, dict):
        base_preference.pop("experimental_patterns", None)

    (
        output,
        design,
        overlay,
        evidence_plan,
        style_layout,
        style_tokens,
    ) = compile_raw_v016(base_raw, identity_contract)
    experimental = build_experimental_pattern_contract(raw)
    output["schema_version"] = "domestic-ecommerce-compiled-output-0.1.7"
    output["compiler_version"] = VERSION
    output["experimental_pattern_contract"] = {
        "schema_version": experimental["schema_version"],
        "request_id": experimental["request_id"],
        "status": experimental["status"],
        "pattern_bundle_id": experimental["pattern_bundle_id"],
        "source_type": experimental["source_type"],
        "source_context": experimental["source_context"],
        "evidence_ceiling": experimental["evidence_ceiling"],
        "surface_usage_mode": experimental["surface_usage_mode"],
        "active_pattern_ids": experimental["active_pattern_ids"],
        "generation_performed": False,
    }
    for page in output["pages"]:
        page["experimental_pattern"] = {
            "status": experimental["status"],
            "pattern_bundle_id": experimental["pattern_bundle_id"],
            "surface_usage_mode": experimental["surface_usage_mode"],
            "active_pattern_ids": experimental["active_pattern_ids"],
        }
        block = experimental_pattern_prompt_block(
            experimental, page["index"]
        )
        if block:
            page["generation_prompt"] += "\n\n" + block
        page["audit_contract"]["experimental_pattern"] = (
            page["experimental_pattern"]
        )
        page["gates"][
            "experimental_pattern_source_ceiling_respected"
        ] = experimental["gates"]["note_content_not_promoted_to_evidence"]
        page["gates"][
            "experimental_pattern_controls_no_product_identity"
        ] = experimental["gates"]["product_identity_not_controlled"]
        page["gates"][
            "experimental_pattern_controls_no_visible_copy"
        ] = experimental["gates"]["visible_copy_not_controlled"]
        page["field_sources"][
            "experimental_pattern.*"
        ] = "EXPERIMENTAL_PATTERN"
    output["set_gates"].update(
        {
            f"experimental_{key}": value
            for key, value in experimental["gates"].items()
        }
    )
    output["status"] = (
        "READY_FOR_CONTROLLED_GENERATION"
        if output["status"] == "READY_FOR_CONTROLLED_GENERATION"
        and experimental["status"] != "BLOCKED"
        and all(output["set_gates"].values())
        and all(all(page["gates"].values()) for page in output["pages"])
        else "BLOCKED"
    )
    jsonschema.Draft202012Validator(load(OUTPUT_SCHEMA)).validate(output)
    return (
        output,
        design,
        overlay,
        evidence_plan,
        style_layout,
        style_tokens,
        experimental,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--campaign-design-contract", required=True)
    parser.add_argument("--design-prototype-overlay", required=True)
    parser.add_argument("--evidence-representation-plan", required=True)
    parser.add_argument("--style-layout-contract", required=True)
    parser.add_argument("--campaign-style-token-contract", required=True)
    parser.add_argument("--experimental-pattern-contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = load(args.input)
    identity = load(args.identity_contract)
    (
        output,
        design,
        overlay,
        evidence_plan,
        style_layout,
        style_tokens,
        experimental,
    ) = compile_raw_v017(raw, identity)
    output["product_identity_contract"]["sha256"] = sha256(
        args.identity_contract
    )
    artifacts = {
        args.campaign_design_contract: design,
        args.design_prototype_overlay: overlay,
        args.evidence_representation_plan: evidence_plan,
        args.style_layout_contract: style_layout,
        args.campaign_style_token_contract: style_tokens,
        args.experimental_pattern_contract: experimental,
    }
    for path, value in artifacts.items():
        write(path, value)
    output["campaign_design_contract"]["sha256"] = sha256(
        args.campaign_design_contract
    )
    output["design_prototype_overlay"]["sha256"] = sha256(
        args.design_prototype_overlay
    )
    output["evidence_representation_plan"]["sha256"] = sha256(
        args.evidence_representation_plan
    )
    output["style_layout_separation_contract"]["sha256"] = sha256(
        args.style_layout_contract
    )
    output["campaign_style_token_contract"]["sha256"] = sha256(
        args.campaign_style_token_contract
    )
    output["experimental_pattern_contract"]["sha256"] = sha256(
        args.experimental_pattern_contract
    )
    write(args.output, output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "pages": len(output["pages"]),
                "experimental_pattern_status": experimental["status"],
                "surface_usage_mode": experimental["surface_usage_mode"],
                "generation_performed": False,
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )
    return 0 if output["status"] == "READY_FOR_CONTROLLED_GENERATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
