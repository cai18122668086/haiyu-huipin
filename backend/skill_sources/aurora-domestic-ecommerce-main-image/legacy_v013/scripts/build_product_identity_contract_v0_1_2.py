#!/usr/bin/env python3
"""Build a product-specific identity contract from reviewed visual observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from identity_framework_v0_1_2 import build_identity_contract, load, write


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-input", required=True)
    parser.add_argument("--visual-observations", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    contract = build_identity_contract(load(args.raw_input), load(args.visual_observations))
    write(args.out, contract)
    print(json.dumps({"status": contract["status"], "output": str(Path(args.out))}))
    return 0 if contract["status"] == "READY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
