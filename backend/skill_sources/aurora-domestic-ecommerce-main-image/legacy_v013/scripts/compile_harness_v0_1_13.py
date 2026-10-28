#!/usr/bin/env python3
"""Compile v0.1.11, prepend one v0.1.13 commercial search cover, and keep its five pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from compile_harness_v0_1_11 import compile_raw_v011
from identity_framework_v0_1_2 import load, write
from promotion_offer_router_v0_1_13 import (
    build_search_main_image_plan,
    prepend_search_main_image,
)


def compile_raw_v013(raw: dict[str, Any], identity_contract: dict[str, Any], source_manifest: str | Path):
    previous = compile_raw_v011(raw, identity_contract, source_manifest)
    search_plan = build_search_main_image_plan(raw)
    final = prepend_search_main_image(previous[0], search_plan)
    overlay = dict(previous[-1])
    overlay["search_main_image_routing"] = search_plan
    return (final, *previous[1:-1], overlay)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = load(args.input)
    identity = load(args.identity_contract)
    result = compile_raw_v013(raw, identity, args.source_manifest)
    write(args.output, result[0])
    print(json.dumps({"status": result[0]["status"], "search_main_status": result[-1]["search_main_image_routing"]["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
