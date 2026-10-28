#!/usr/bin/env python3
"""Compile page-level visual judgments into a hard-gated set audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from identity_framework_v0_1_2 import build_audit, load, write


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--identity-contract", required=True)
    parser.add_argument("--judgment", action="append", required=True)
    parser.add_argument("--same-product-across-set", choices=["PASS", "FAIL"], required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    artifacts = [load(x) for x in args.judgment]
    artifacts.sort(key=lambda x: x["page_index"])
    pages = [
        {"page_index": x["page_index"], "generated_image": x["generated_image"]}
        for x in artifacts
    ]
    judgments = [x["judgment"] for x in artifacts]
    audit = build_audit(
        args.request_id,
        args.identity_contract,
        pages,
        judgments,
        args.same_product_across_set,
    )
    write(args.out, audit)
    print(json.dumps({"status": audit["status"], "output": str(Path(args.out))}))
    return 0 if audit["status"] == "READY_FOR_CONTROLLED_USE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
