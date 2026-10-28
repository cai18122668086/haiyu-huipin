#!/usr/bin/env python3
"""Compile v0.1.2 prompts with a high-priority, product-specific identity lock."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import jsonschema

from compile_harness_v0_1_1 import compile_raw as compile_v011
from identity_framework_v0_1_2 import identity_prompt_block, load, sha256, write


ROOT = Path(__file__).resolve().parents[1]
RAW_SCHEMA = ROOT / "schemas" / "raw-input-v0.1.2.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "compiled-output-v0.1.2.schema.json"
VERSION = "domestic-ecommerce-prompt-compiler-0.1.2"


ROLE_VIEW_NEEDS = {
    "STANDARD_IDENTITY_MAIN": ["FRONT", "THREE_QUARTER"],
    "OFFER_IDENTITY_MAIN": ["FRONT", "THREE_QUARTER"],
    "CAROUSEL_SLOT_1_IDENTITY": ["FRONT", "THREE_QUARTER"],
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT": ["FRONT", "THREE_QUARTER", "DETAIL"],
    "CAROUSEL_SLOT_3_SCENE_BENEFIT": ["FRONT", "THREE_QUARTER"],
    "CAROUSEL_SLOT_4_DETAIL_PROOF": ["DETAIL", "SIDE_OR_RECLINE", "REAR"],
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": ["FRONT", "THREE_QUARTER"],
    "DETAIL_LONG_FEATURE_SECTION": ["FRONT", "THREE_QUARTER", "DETAIL"],
    "FEATURE": ["DETAIL", "THREE_QUARTER"],
    "BENEFIT": ["FRONT", "THREE_QUARTER"],
    "SPECIFICATION": ["FRONT", "DETAIL"],
    "TRUST": ["FRONT", "THREE_QUARTER"],
}


def view_binding(contract: dict[str, Any], page_role: str) -> dict[str, Any]:
    available = contract["view_specific_evidence"]
    needed = ROLE_VIEW_NEEDS.get(page_role, ["FRONT", "THREE_QUARTER"])
    bound = {}
    for tag in needed:
        ids = available.get(tag, [])
        if ids:
            bound[tag] = ids
    return {
        "required_view_types": needed,
        "bound_evidence_ids": bound,
        "status": "READY" if bound else "BLOCKED",
    }


def compile_raw_v012(raw: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    jsonschema.Draft202012Validator(load(RAW_SCHEMA)).validate(raw)
    if contract["product"]["name"] != raw["product"]["name"]:
        raise ValueError("IDENTITY_CONTRACT_PRODUCT_MISMATCH")
    base_raw = copy.deepcopy(raw)
    base_raw["schema_version"] = "domestic-ecommerce-raw-input-0.1.1"
    output = compile_v011(base_raw)
    output["schema_version"] = "domestic-ecommerce-compiled-output-0.1.2"
    output["compiler_version"] = VERSION
    output["product_identity_contract"] = {
        "contract_id": contract["contract_id"],
        "sha256": None,
        "status": contract["status"],
        "evidence_conflicts": contract["evidence_conflicts"],
    }
    output["previous_generated_images_used"] = False
    identity_ready = contract["status"] == "READY"
    for page in output["pages"]:
        binding = view_binding(contract, page["page_role"])
        page["view_evidence_binding"] = binding
        high_priority = identity_prompt_block(contract, binding)
        page["generation_prompt"] = high_priority + "\n\n" + page["generation_prompt"]
        page["audit_contract"]["locks"]["product_consistency"] = {
            "source_type": "PRODUCT_IDENTITY_CONTRACT",
            "contract_id": contract["contract_id"],
            "contract_status": contract["status"],
            "must_preserve": contract["must_preserve"],
            "must_not_change": contract["must_not_change"],
            "view_specific_evidence": binding,
            "previous_generated_images_used": False,
            "editing_priority": "PRESERVE_PRODUCT_THEN_REPLACE_BACKGROUND_AND_ADD_LAYOUT",
        }
        page["gates"]["product_identity_contract_ready"] = identity_ready
        page["gates"]["view_evidence_bound"] = binding["status"] == "READY"
        page["gates"]["identity_lock_is_prompt_prefix"] = page[
            "generation_prompt"
        ].startswith("【最高优先级 PRODUCT IDENTITY LOCK】")
        page["gates"]["previous_generated_images_excluded"] = True
    all_page_gates = all(all(x["gates"].values()) for x in output["pages"])
    all_set_gates = all(
        value for key, value in output["set_gates"].items() if key != "applicable"
    )
    output["status"] = (
        "READY_FOR_CONTROLLED_GENERATION"
        if identity_ready and all_page_gates and all_set_gates
        else "BLOCKED"
    )
    jsonschema.Draft202012Validator(load(OUTPUT_SCHEMA)).validate(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = load(args.input)
    contract = load(args.identity_contract)
    output = compile_raw_v012(raw, contract)
    output["product_identity_contract"]["sha256"] = sha256(args.identity_contract)
    write(args.output, output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "pages": len(output["pages"]),
                "applied_evidence_rule_count": output["applied_evidence_rule_count"],
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )
    return 0 if output["status"] == "READY_FOR_CONTROLLED_GENERATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
