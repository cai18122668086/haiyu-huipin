#!/usr/bin/env python3
"""Compile v0.1.5 prompts with separated style, layout and evidence routing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import jsonschema

from compile_harness_v0_1_3 import compile_raw_v013
from design_prototype_overlay_v0_1_5 import (
    SOURCE_PRECEDENCE,
    build_design_prototype_overlay,
    overlay_prompt_block,
)
from evidence_representation_router_v0_1_5 import (
    build_evidence_representation_plan,
    evidence_prompt_block,
)
from style_layout_separation_v0_1_5 import (
    build_style_layout_contract,
    style_layout_prompt_block,
)
from identity_framework_v0_1_2 import load, sha256, write


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA = ROOT / "schemas" / "compiled-output-v0.1.5.schema.json"
VERSION = "domestic-ecommerce-prompt-compiler-0.1.5"


def compile_raw_v015(
    raw: dict[str, Any], identity_contract: dict[str, Any]
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    output, design = compile_raw_v013(raw, identity_contract)
    design["source_precedence"] = SOURCE_PRECEDENCE
    overlay = build_design_prototype_overlay(
        raw, identity_contract, design, output["pages"]
    )
    evidence_plan = build_evidence_representation_plan(raw, design, overlay)
    style_layout = build_style_layout_contract(raw, overlay, evidence_plan)
    overlay_by_index = {p["page_index"]: p for p in overlay["pages"]}
    evidence_by_index = {p["page_index"]: p for p in evidence_plan["pages"]}
    style_by_index = {p["page_index"]: p for p in style_layout["pages"]}
    output["schema_version"] = "domestic-ecommerce-compiled-output-0.1.5"
    output["compiler_version"] = VERSION
    output["generation_performed"] = False
    output["design_prototype_overlay"] = {
        "schema_version": overlay["schema_version"],
        "request_id": overlay["request_id"],
        "status": overlay["status"],
        "source_type": "ENGINEERED_DEFAULT",
        "generation_performed": False,
    }
    output["evidence_representation_plan"] = {
        "schema_version": evidence_plan["schema_version"],
        "request_id": evidence_plan["request_id"],
        "status": evidence_plan["status"],
        "source_type": "ENGINEERED_DEFAULT",
        "generation_performed": False,
    }
    output["style_layout_separation_contract"] = {
        "schema_version": style_layout["schema_version"],
        "request_id": style_layout["request_id"],
        "status": style_layout["status"],
        "source_type": "ENGINEERED_DEFAULT",
        "generation_performed": False,
    }
    for page in output["pages"]:
        page_overlay = overlay_by_index[page["index"]]
        page_evidence = evidence_by_index[page["index"]]
        page_style_layout = style_by_index[page["index"]]
        page["design_prototype_overlay"] = page_overlay
        page["evidence_representation"] = page_evidence
        page["style_layout_separation"] = page_style_layout
        page["generation_prompt"] += (
            "\n\n"
            + overlay_prompt_block(page_overlay)
            + "\n\n"
            + evidence_prompt_block(page_evidence)
            + "\n\n"
            + style_layout_prompt_block(style_layout, page_style_layout)
        )
        page["audit_contract"]["design_prototype_overlay"] = {
            "contract": page_overlay,
            "scope": "NON_FACTUAL_VISUAL_FIELDS_ONLY",
            "product_identity_priority": "HARD_LOCK_BEFORE_OVERLAY",
            "campaign_style_lock": overlay["campaign_style_lock"],
            "source_type": "ENGINEERED_DEFAULT",
        }
        page["audit_contract"]["evidence_representation"] = page_evidence
        page["audit_contract"]["style_layout_separation"] = page_style_layout
        page["audit_contract"]["locks"]["design_strength"][
            "prototype_overlay"
        ] = {
            "layout_archetype": page_overlay["layout_archetype"],
            "composition_axis": page_overlay["composition_axis"],
            "product_occupancy": page_overlay["product_occupancy"],
            "title_structure": page_overlay["title_structure"],
            "difference_from_previous_page": page_overlay[
                "difference_from_previous_page"
            ],
            "source_type": "ENGINEERED_DEFAULT",
        }
        for field, source in page_overlay["field_sources"].items():
            page["field_sources"][f"design_prototype_overlay.{field}"] = source
        page["gates"]["design_prototype_overlay_ready"] = (
            overlay["status"] == "READY"
        )
        page["gates"]["overlay_does_not_override_identity_or_facts"] = (
            page_overlay["non_factual_visual_fields_only"]
            and page_overlay["product_identity_priority"]
            == "HARD_LOCK_BEFORE_OVERLAY"
        )
        page["gates"]["evidence_representation_ready"] = (
            evidence_plan["status"] == "READY"
        )
        page["gates"]["style_layout_separation_ready"] = (
            style_layout["status"] == "READY"
        )
        page["gates"]["style_reference_does_not_control_layout"] = (
            "LAYOUT_ARCHETYPE" in style_layout["style_lock"]["must_not_control"]
            and "TYPOGRAPHY_FAMILY" in style_layout["style_lock"]["controls"]
        )
    output["set_gates"].update(
        {f"overlay_{k}": v for k, v in overlay["set_gates"].items()}
    )
    output["set_gates"].update(
        {f"evidence_{k}": v for k, v in evidence_plan["set_gates"].items()}
    )
    output["set_gates"].update(
        {f"style_layout_{k}": v for k, v in style_layout["set_gates"].items()}
    )
    output["status"] = (
        "READY_FOR_CONTROLLED_GENERATION"
        if output["status"] == "READY_FOR_CONTROLLED_GENERATION"
        and overlay["status"] == "READY"
        and evidence_plan["status"] == "READY"
        and style_layout["status"] == "READY"
        and all(output["set_gates"].values())
        and all(all(p["gates"].values()) for p in output["pages"])
        else "BLOCKED"
    )
    jsonschema.Draft202012Validator(load(OUTPUT_SCHEMA)).validate(output)
    return output, design, overlay, evidence_plan, style_layout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--campaign-design-contract", required=True)
    parser.add_argument("--design-prototype-overlay", required=True)
    parser.add_argument("--evidence-representation-plan", required=True)
    parser.add_argument("--style-layout-contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = load(args.input)
    identity = load(args.identity_contract)
    output, design, overlay, evidence_plan, style_layout = compile_raw_v015(
        raw, identity
    )
    output["product_identity_contract"]["sha256"] = sha256(args.identity_contract)
    write(args.campaign_design_contract, design)
    write(args.design_prototype_overlay, overlay)
    write(args.evidence_representation_plan, evidence_plan)
    write(args.style_layout_contract, style_layout)
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
    write(args.output, output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "pages": len(output["pages"]),
                "layout_archetypes": [
                    x["layout_archetype"] for x in overlay["pages"]
                ],
                "set_gates": overlay["set_gates"],
                "evidence_modes": [
                    x["representation_mode"] for x in evidence_plan["pages"]
                ],
                "style_layout_gates": style_layout["set_gates"],
                "generation_performed": False,
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )
    return 0 if output["status"] == "READY_FOR_CONTROLLED_GENERATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
