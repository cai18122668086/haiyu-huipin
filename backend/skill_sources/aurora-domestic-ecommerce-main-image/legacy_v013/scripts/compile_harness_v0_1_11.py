#!/usr/bin/env python3
"""Compile v0.1.7 gates with v0.1.11 full-corpus design intelligence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from compile_harness_v0_1_7 import compile_raw_v017
from campaign_style_freeze_v0_1_11 import freeze_campaign_style
from cross_category_design_intelligence_v0_1_11 import (
    apply_design_intelligence,
    build_design_intelligence_plan,
)
from durable_case_analysis_overlay_v0_1_10 import (
    apply_overlay,
    build_overlay_plan,
    load_visual_analysis,
    raw_for_stable_baseline,
)
from identity_framework_v0_1_2 import load, write


def compile_raw_v011(
    raw: dict[str, Any],
    identity_contract: dict[str, Any],
    source_manifest: str | Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    stable_result = compile_raw_v017(
        raw_for_stable_baseline(raw),
        identity_contract,
    )
    analysis = load_visual_analysis(source_manifest)
    plan = build_overlay_plan(raw, identity_contract, analysis)
    durable_result = apply_overlay(stable_result[0], plan)
    design_plan = build_design_intelligence_plan(
        durable_result,
        raw,
        identity_contract,
    )
    plan["cross_category_design_intelligence"] = design_plan
    final_result = freeze_campaign_style(
        apply_design_intelligence(durable_result, design_plan),
        raw,
    )
    return (
        final_result,
        *stable_result[1:],
        plan,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    raw = load(args.input)
    identity = load(args.identity_contract)
    result = compile_raw_v011(
        raw,
        identity,
        args.source_manifest,
    )
    write(args.output, result[0])
    print(
        json.dumps(
            {
                "status": result[0]["status"],
                "overlay_status": result[-1]["status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
