#!/usr/bin/env python3
"""Compile v0.1.6 prompts with concrete Campaign Style tokens."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import jsonschema

from campaign_style_tokens_v0_1_6 import (
    build_planned_style_token_contract,
    style_token_prompt_block,
)
from compile_harness_v0_1_5 import compile_raw_v015
from identity_framework_v0_1_2 import load, sha256, write


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA = ROOT / "schemas" / "compiled-output-v0.1.6.schema.json"
VERSION = "domestic-ecommerce-prompt-compiler-0.1.6"


def compile_raw_v016(
    raw: dict[str, Any], identity_contract: dict[str, Any]
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    output, design, overlay, evidence_plan, style_layout = compile_raw_v015(
        raw, identity_contract
    )
    style_tokens = build_planned_style_token_contract(raw, design, overlay)
    variation_by_index = {
        p["page_index"]: p for p in style_tokens["page_role_variations"]
    }
    output["schema_version"] = "domestic-ecommerce-compiled-output-0.1.6"
    output["compiler_version"] = VERSION
    output["campaign_style_token_contract"] = {
        "schema_version": style_tokens["schema_version"],
        "request_id": style_tokens["request_id"],
        "status": style_tokens["status"],
        "mode": style_tokens["mode"],
        "source_type": style_tokens["source_type"],
        "generation_performed": False,
    }
    for page in output["pages"]:
        index = page["index"]
        page["campaign_style_tokens"] = {
            "mode": style_tokens["mode"],
            "tokens": style_tokens["tokens"],
            "page_role_variation": variation_by_index[index],
            "style_reference_policy": style_tokens["style_reference_policy"],
        }
        page["generation_prompt"] += (
            "\n\n" + style_token_prompt_block(style_tokens, index)
        )
        page["audit_contract"]["campaign_style_tokens"] = (
            page["campaign_style_tokens"]
        )
        page["gates"]["campaign_style_tokens_ready"] = (
            style_tokens["status"] == "READY"
        )
        page["gates"]["style_reference_scope_is_style_only"] = (
            style_tokens["style_reference_policy"]["scope"]
            == "STYLE_REFERENCE_ONLY"
            and style_tokens["style_reference_policy"][
                "product_identity_authority"
            ]
            == "ORIGINAL_CURRENT_PRODUCT_REFERENCES_ONLY"
        )
        page["gates"]["layout_remains_independent_from_style_tokens"] = (
            variation_by_index[index]["layout_must_remain_independent"]
        )
        page["field_sources"][
            "campaign_style_tokens.*"
        ] = "ENGINEERED_DEFAULT"
    output["set_gates"].update(
        {
            f"style_tokens_{key}": value
            for key, value in style_tokens["set_gates"].items()
        }
    )
    output["status"] = (
        "READY_FOR_CONTROLLED_GENERATION"
        if output["status"] == "READY_FOR_CONTROLLED_GENERATION"
        and style_tokens["status"] == "READY"
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
    ) = compile_raw_v016(raw, identity)
    output["product_identity_contract"]["sha256"] = sha256(args.identity_contract)
    write(args.campaign_design_contract, design)
    write(args.design_prototype_overlay, overlay)
    write(args.evidence_representation_plan, evidence_plan)
    write(args.style_layout_contract, style_layout)
    write(args.campaign_style_token_contract, style_tokens)
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
    write(args.output, output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "pages": len(output["pages"]),
                "style_token_mode": style_tokens["mode"],
                "style_token_gates": style_tokens["set_gates"],
                "generation_performed": False,
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )
    return 0 if output["status"] == "READY_FOR_CONTROLLED_GENERATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
