#!/usr/bin/env python3
"""Build a hard-gated audit of every visible compiled copy item."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "pre-generation-copy-review-v0.1.3.schema.json"


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-design-contract", required=True)
    parser.add_argument("--compiled-output", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    campaign = load(args.campaign_design_contract)
    compiled = load(args.compiled_output)
    records = [
        record
        for page in campaign["pages"]
        for record in page["copy_plan"]["copy_integrity_gate"]["records"]
    ]
    blocked = [
        record
        for record in records
        if not record["semantic_complete"]
        or not record["fact_traceable"]
        or record["new_claim"]
        or not all(record["checks"].values())
    ]
    passed = (
        campaign["status"] == "READY"
        and compiled["status"] == "READY_FOR_CONTROLLED_GENERATION"
        and not blocked
        and bool(records)
    )
    result = {
        "schema_version": "pre-generation-copy-review-0.1.3",
        "request_id": campaign["request_id"],
        "status": "PASS" if passed else "BLOCKED",
        "generation_allowed": passed,
        "records": records,
        "blocked_records": blocked,
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(load(SCHEMA)).validate(result)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "records": len(records), "blocked": len(blocked)}, ensure_ascii=False))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
