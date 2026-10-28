#!/usr/bin/env python3
"""Fail-closed, secret-free post-deployment health smoke test.

The command intentionally checks only unauthenticated aggregate health endpoints.
It does not create crawl jobs, touch browser profiles, or print response bodies.
Use the controlled browser/platform procedure in the production runbook for
stateful verification after these checks pass.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINTS = ("/health/live", "/health/ready", "/health/ecommerce")
READY_CHECKS = ("database", "task_broker", "object_storage", "authentication")


@dataclass(frozen=True)
class EndpointResult:
    path: str
    status_code: int | None
    error: str | None = None


def _normalise_base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("base URL must start with http:// or https://")
    return base


def _read_json(url: str, timeout: float) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "polaris-post-deploy-smoke/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"request failed: {exc.__class__.__name__}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("response JSON was not an object")
    return status_code, payload


def _validate(path: str, status_code: int, payload: dict[str, Any]) -> str | None:
    if status_code != 200:
        return f"HTTP {status_code}"
    if payload.get("status") != "ok":
        return "status is not ok"
    if path == "/health/live":
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            return "checks object is missing"
        if checks.get("process") != "ok":
            return "process check is not ok"
        return None
    if path == "/health/ready":
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            return "checks object is missing"
        missing = [name for name in READY_CHECKS if checks.get(name) != "ok"]
        return f"checks not ok: {', '.join(missing)}" if missing else None
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return "metrics object is missing"
    for name in (
        "task_counts",
        "active_leases",
        "expired_leases",
        "oldest_queued_age_seconds",
    ):
        if name not in metrics:
            return f"metric is missing: {name}"
    return None


def run_smoke(base_url: str, timeout: float = 10.0) -> list[EndpointResult]:
    base = _normalise_base_url(base_url)
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    results: list[EndpointResult] = []
    for path in ENDPOINTS:
        try:
            status_code, payload = _read_json(f"{base}{path}", timeout)
            error = _validate(path, status_code, payload)
            results.append(EndpointResult(path, status_code, error))
        except (RuntimeError, TypeError) as exc:
            results.append(EndpointResult(path, None, str(exc)))
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run secret-free post-deployment Polaris health checks"
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="public or loopback API base URL, for example https://api.example.com",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = run_smoke(args.base_url, args.timeout)
    except ValueError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    failed = False
    for result in results:
        if result.error:
            failed = True
            suffix = result.error
            print(f"FAIL {result.path}: {suffix}")
        else:
            print(f"PASS {result.path}: HTTP {result.status_code}")
    if failed:
        print("BLOCKED: post-deployment health smoke failed")
        return 1
    print("PASS: post-deployment health smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
