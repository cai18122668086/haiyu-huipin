#!/usr/bin/env python3
"""Validate Celery active queue assignments without printing raw broker output.

Run this on the deployment host after the health smoke test, piping the output
of ``celery inspect active_queues --json`` into the command. Production uses
Celery's default ``celery@...`` node name for the general worker and an
explicit ``crawl@...`` node name for the browser worker.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from typing import Any

EXPECTED_QUEUE_PREFIXES: tuple[tuple[str, frozenset[str]], ...] = (
    ("celery@", frozenset({"default"})),
    ("crawl@", frozenset({"crawl"})),
)


def _expected_queues(worker_name: str) -> frozenset[str] | None:
    for prefix, queues in EXPECTED_QUEUE_PREFIXES:
        if worker_name.startswith(prefix):
            return queues
    return None


def _queue_names(value: Any) -> set[str] | None:
    if not isinstance(value, list):
        return None
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            return None
        names.add(item["name"])
    return names


def validate_active_queues(payload: str | Mapping[str, Any]) -> list[str]:
    """Return safe, actionable errors for a Celery active_queues payload."""

    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return ["active_queues output was not valid JSON"]
    else:
        decoded = payload
    if not isinstance(decoded, Mapping):
        return ["active_queues JSON must be an object"]
    if not decoded:
        return ["no Celery workers reported active queues"]

    errors: list[str] = []
    seen_prefixes: set[str] = set()
    for worker_name, queues in decoded.items():
        if not isinstance(worker_name, str):
            errors.append("active_queues contained a non-string worker name")
            continue
        expected = _expected_queues(worker_name)
        if expected is None:
            errors.append(f"{worker_name} has an unrecognized worker name prefix")
            continue
        prefix = next(prefix for prefix, _ in EXPECTED_QUEUE_PREFIXES if worker_name.startswith(prefix))
        seen_prefixes.add(prefix)
        actual = _queue_names(queues)
        if actual is None:
            errors.append(f"{worker_name} returned an invalid queue list")
            continue
        if actual != expected:
            expected_text = ", ".join(sorted(expected)) or "none"
            actual_text = ", ".join(sorted(actual)) or "none"
            errors.append(
                f"{worker_name} must listen only to queue(s) {expected_text}; found {actual_text}"
            )
    for prefix, _ in EXPECTED_QUEUE_PREFIXES:
        if prefix not in seen_prefixes:
            errors.append(f"no active worker matched the required {prefix} node prefix")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate secret-free Celery active queue assignments"
    )
    parser.add_argument(
        "--input",
        type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin,
        help="JSON input file; defaults to stdin",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_active_queues(args.input.read())
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        print("BLOCKED: active Celery queue check failed")
        return 1
    print("PASS: active Celery queue assignments match production topology")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
