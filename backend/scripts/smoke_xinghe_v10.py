"""Offline-auditable Xinghe v10 detail-pack real-smoke harness.

``calibrate-blocked`` uses the exact live workflow with a deterministic clock
and a transport that cannot access the network.  ``live`` remains fail-closed
until an explicit approval file is atomically consumed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

RESULT_SCHEMA = "polaris-xinghe-v10-smoke-result-v1"
APPROVAL_SCHEMA = "polaris-xinghe-v10-live-approval-v1"
TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})
PLAN_CONTENT_FIELDS = (
    "role",
    "title",
    "label",
    "composition",
    "device",
    "difference",
)
BLOCKED_ESTIMATED_MAX_MICROS = 1_500_000


class HarnessError(RuntimeError):
    """A content-free, machine-reportable harness failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HarnessTimeout(HarnessError):
    """A bounded workflow deadline was reached."""

    def __init__(self, layer: str, elapsed_seconds: float) -> None:
        super().__init__(f"{layer.upper()}_EXCEEDED")
        self.layer = layer
        self.elapsed_seconds = elapsed_seconds


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> TransportResponse: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    payload: Mapping[str, Any]


@dataclass
class SafetyCounters:
    network_attempts: int = 0
    provider_calls: int = 0
    secret_reads: int = 0
    real_jobs: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "network_attempts": self.network_attempts,
            "provider_calls": self.provider_calls,
            "secret_reads": self.secret_reads,
            "real_jobs": self.real_jobs,
        }


@dataclass(frozen=True)
class TimeoutPolicy:
    transport_seconds: float
    poll_seconds: float
    total_deadline_seconds: float
    cleanup_margin_seconds: float
    poll_interval_seconds: float

    def validate(self) -> None:
        values = (
            self.transport_seconds,
            self.poll_seconds,
            self.total_deadline_seconds,
            self.cleanup_margin_seconds,
            self.poll_interval_seconds,
        )
        if any(value <= 0 for value in values):
            raise HarnessError("TIMEOUT_VALUE_INVALID")
        if self.transport_seconds > self.total_deadline_seconds:
            raise HarnessError("TRANSPORT_EXCEEDS_TOTAL_DEADLINE")
        if self.poll_seconds > self.total_deadline_seconds:
            raise HarnessError("POLL_EXCEEDS_TOTAL_DEADLINE")
        if self.poll_interval_seconds > self.poll_seconds:
            raise HarnessError("POLL_INTERVAL_EXCEEDS_POLL_TIMEOUT")

    @property
    def outer_tool_minimum_seconds(self) -> float:
        return self.total_deadline_seconds + self.cleanup_margin_seconds

    def as_dict(self) -> dict[str, float]:
        return {
            "transport_seconds": self.transport_seconds,
            "poll_seconds": self.poll_seconds,
            "harness_total_deadline_seconds": self.total_deadline_seconds,
            "cleanup_margin_seconds": self.cleanup_margin_seconds,
            "outer_tool_minimum_seconds": self.outer_tool_minimum_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
        }


@dataclass(frozen=True)
class HarnessConfig:
    mode: str
    base_url: str
    workspace_id: str
    asset_ids: tuple[str, ...]
    product_name: str
    selling_points: tuple[str, ...]
    platform_code: str
    language: str
    page_count: int
    timeouts: TimeoutPolicy
    approved_budget_micros: int | None = None
    approval_file: Path | None = None
    approval_id: str | None = None
    auth_token_file: Path | None = None

    def validate(self) -> None:
        if self.mode not in {"calibrate-blocked", "live"}:
            raise HarnessError("MODE_INVALID")
        self.timeouts.validate()
        if not self.workspace_id.strip():
            raise HarnessError("WORKSPACE_ID_REQUIRED")
        if not self.asset_ids:
            raise HarnessError("ASSET_ID_REQUIRED")
        if not 1 <= self.page_count <= 12:
            raise HarnessError("PAGE_COUNT_INVALID")
        if not self.product_name.strip():
            raise HarnessError("PRODUCT_NAME_REQUIRED")
        if self.approved_budget_micros is None:
            raise HarnessError("APPROVED_BUDGET_REQUIRED")
        if (
            not isinstance(self.approved_budget_micros, int)
            or isinstance(self.approved_budget_micros, bool)
            or self.approved_budget_micros < 0
        ):
            raise HarnessError("APPROVED_BUDGET_INVALID")


@dataclass
class WorkflowTrace:
    actions: list[str] = field(default_factory=list)
    preconfirm_gate_passed: bool = False
    estimated_max_micros: int | None = None


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class DeterministicClock:
    """A monotonic clock that advances without wall-clock sleeping."""

    def __init__(self) -> None:
        self._value = 0.0

    def monotonic(self) -> float:
        return self._value

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("deterministic clock cannot move backwards")
        self._value += seconds


class CalibrateBlockedTransport:
    """A local state machine with no network or Provider implementation."""

    def __init__(self, counters: SafetyCounters) -> None:
        self._counters = counters
        self._job_created = False
        self._confirmed = False
        self._page_count = 0

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> TransportResponse:
        del headers, timeout_seconds
        jobs_path = "/api/v1/workspaces/ws/jobs"
        job_path = f"{jobs_path}/job_calibration_blocked"
        confirm_path = f"{job_path}/plan/confirm"
        normalized = _normalize_workspace_path(path)
        if method == "POST" and normalized == jobs_path:
            if json_body is None or json_body.get("type") != "detail_pack":
                raise HarnessError("CALIBRATION_PAYLOAD_INVALID")
            configuration = json_body.get("configuration")
            if not isinstance(configuration, Mapping):
                raise HarnessError("CALIBRATION_PAYLOAD_INVALID")
            page_count = configuration.get("page_count")
            if page_count != 6:
                raise HarnessError("CALIBRATION_PAGE_COUNT_INVALID")
            self._page_count = page_count
            self._job_created = True
            return TransportResponse(202, self._job_payload("planning"))
        if method == "GET" and normalized == job_path and self._job_created:
            status = "processing" if self._confirmed else "awaiting_confirmation"
            return TransportResponse(200, self._job_payload(status))
        if method == "POST" and normalized == confirm_path and self._job_created:
            if json_body != {"expected_revision": 1}:
                raise HarnessError("CALIBRATION_CONFIRMATION_INVALID")
            self._confirmed = True
            return TransportResponse(202, self._job_payload("processing"))
        raise HarnessError("CALIBRATION_ROUTE_BLOCKED")

    def close(self) -> None:
        return None

    def _job_payload(self, status: str) -> Mapping[str, Any]:
        plan: Mapping[str, Any] | None = None
        if status != "planning":
            plan = {
                "revision": 1,
                "validation_status": "valid",
                "confirmed_at": (
                    "2000-01-01T00:00:00Z" if self._confirmed else None
                ),
                "payload": {"pages": self._safe_screens()},
            }
        return {
            "id": "job_calibration_blocked",
            "type": "detail_pack",
            "status": status,
            "expected_item_count": self._page_count,
            "succeeded_item_count": 0,
            "failed_item_count": 0,
            "api_request_count": self._counters.provider_calls,
            "cost": {
                "estimated_max": {
                    "micros": str(BLOCKED_ESTIMATED_MAX_MICROS),
                    "currency": "USD",
                }
            },
            "plan": plan,
        }

    def _safe_screens(self) -> list[dict[str, str]]:
        devices = (
            "Scale grid",
            "Macro inset",
            "Feature callout",
            "Angle comparison",
            "Material map",
            "Closing tableau",
        )
        return [
            {
                "role": f"screen-{index}",
                "title": f"Product view {index}",
                "label": f"View {index}",
                "composition": f"Composition {index}",
                "device": device,
                "difference": f"Layout variation {index}",
            }
            for index, device in enumerate(devices, start=1)
        ]


class LiveHttpTransport:
    """HTTP adapter instantiated only after the live approval gate passes."""

    def __init__(
        self,
        base_url: str,
        counters: SafetyCounters,
        authorization: str | None,
    ) -> None:
        import httpx2

        self._client = httpx2.Client(base_url=base_url, trust_env=False)
        self._counters = counters
        self._authorization = authorization
        self._timeout_exception = httpx2.TimeoutException
        self._http_error = httpx2.HTTPError

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> TransportResponse:
        request_headers = dict(headers)
        if self._authorization is not None:
            request_headers["Authorization"] = self._authorization
        self._counters.network_attempts += 1
        try:
            response = self._client.request(
                method,
                path,
                headers=request_headers,
                json=json_body,
                timeout=timeout_seconds,
            )
        except Exception as exc:
            if isinstance(exc, self._timeout_exception):
                raise HarnessTimeout("transport", timeout_seconds) from exc
            if isinstance(exc, self._http_error):
                raise HarnessError("TRANSPORT_REQUEST_FAILED") from exc
            raise
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise HarnessError("HTTP_RESPONSE_NOT_OBJECT")
        if (
            method == "POST"
            and path.endswith("/jobs")
            and response.status_code == 202
        ):
            self._counters.real_jobs += 1
        return TransportResponse(response.status_code, payload)

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class RuntimeAdapters:
    clock: Clock
    transport: Transport
    counters: SafetyCounters
    deadline_is_expected: bool


def source_audit() -> dict[str, str]:
    """Hash and compile the candidate boundary without importing product code."""

    backend_root = Path(__file__).resolve().parents[1]
    sources = {
        "runner": backend_root / "app" / "task_runner.py",
        "adapter": backend_root / "app" / "xinghe_agent.py",
        "harness": Path(__file__).resolve(),
    }
    digests: dict[str, str] = {}
    for name, path in sources.items():
        payload = path.read_bytes()
        tree = ast.parse(payload, filename=name)
        compile(tree, name, "exec")
        digests[name] = hashlib.sha256(payload).hexdigest()
    manifest = "".join(
        f"{name}\0{digests[name]}\n" for name in sorted(digests)
    ).encode()
    digests["candidate"] = hashlib.sha256(manifest).hexdigest()
    return {
        "candidate": digests["candidate"],
        "runner": digests["runner"],
        "adapter": digests["adapter"],
        "harness": digests["harness"],
    }


def build_detail_job_payload(config: HarnessConfig) -> dict[str, Any]:
    return {
        "type": "detail_pack",
        "intent": "submit",
        "display_name": "Xinghe v10 real smoke",
        "input": {
            "product_groups": [
                {
                    "group_key": "v10-smoke",
                    "asset_ids": list(config.asset_ids),
                }
            ]
        },
        "configuration": {
            "product_name": config.product_name,
            "selling_points": list(config.selling_points),
            "platform_code": config.platform_code,
            "language": config.language,
            "page_count": config.page_count,
        },
    }


def consume_live_approval(
    path: Path | None,
    approval_id: str | None,
    candidate_sha256: str,
) -> None:
    if path is None or approval_id is None:
        raise HarnessError("LIVE_APPROVAL_REQUIRED")
    try:
        UUID(approval_id)
    except ValueError as exc:
        raise HarnessError("LIVE_APPROVAL_ID_INVALID") from exc
    consumed_path = path.with_name(f"{path.name}.consumed")
    if consumed_path.exists():
        raise HarnessError("LIVE_APPROVAL_ALREADY_CONSUMED")
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError("LIVE_APPROVAL_UNREADABLE") from exc
    if not isinstance(value, Mapping):
        raise HarnessError("LIVE_APPROVAL_INVALID")
    required = {
        "schema": APPROVAL_SCHEMA,
        "mode": "live",
        "approval_id": approval_id,
        "candidate_sha256": candidate_sha256,
        "acknowledge_real_job": True,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise HarnessError("LIVE_APPROVAL_INVALID")
    expires_at = value.get("expires_at")
    if not isinstance(expires_at, str):
        raise HarnessError("LIVE_APPROVAL_INVALID")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarnessError("LIVE_APPROVAL_INVALID") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise HarnessError("LIVE_APPROVAL_EXPIRED")
    try:
        os.replace(path, consumed_path)
    except OSError as exc:
        raise HarnessError("LIVE_APPROVAL_CONSUME_FAILED") from exc


def read_authorization(
    path: Path | None,
    counters: SafetyCounters,
) -> str | None:
    if path is None:
        return None
    counters.secret_reads += 1
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise HarnessError("AUTH_TOKEN_UNREADABLE") from exc
    if not token:
        raise HarnessError("AUTH_TOKEN_EMPTY")
    return f"Bearer {token}"


def create_runtime(
    config: HarnessConfig,
    counters: SafetyCounters,
    hashes: Mapping[str, str],
    *,
    blocked_factory: Callable[[SafetyCounters], Transport] = (
        CalibrateBlockedTransport
    ),
    live_factory: Callable[
        [str, SafetyCounters, str | None], Transport
    ] = LiveHttpTransport,
) -> RuntimeAdapters:
    """The sole calibrate/live branch: inject clock and transport adapters."""

    if config.mode == "calibrate-blocked":
        return RuntimeAdapters(
            clock=DeterministicClock(),
            transport=blocked_factory(counters),
            counters=counters,
            deadline_is_expected=True,
        )
    consume_live_approval(
        config.approval_file,
        config.approval_id,
        hashes["candidate"],
    )
    authorization = read_authorization(config.auth_token_file, counters)
    return RuntimeAdapters(
        clock=SystemClock(),
        transport=live_factory(config.base_url, counters, authorization),
        counters=counters,
        deadline_is_expected=False,
    )


def run_workflow(
    config: HarnessConfig,
    runtime: RuntimeAdapters,
    trace: WorkflowTrace,
) -> tuple[Mapping[str, Any], WorkflowTrace, float]:
    """Run the shared create/dispatch/confirm/poll/validate control path."""

    clock = runtime.clock
    start = clock.monotonic()
    total_deadline = start + config.timeouts.total_deadline_seconds
    jobs_path = f"/api/v1/workspaces/{config.workspace_id}/jobs"

    created = _request(
        runtime,
        config,
        total_deadline,
        "POST",
        jobs_path,
        json_body=build_detail_job_payload(config),
        expected_status=202,
    )
    job_id = _required_string(created, "id")
    trace.actions.extend(["job_create", "initial_dispatch"])

    planned = _poll_for(
        runtime,
        config,
        total_deadline,
        f"{jobs_path}/{job_id}",
        {"awaiting_confirmation"} | TERMINAL_STATUSES,
        trace,
        "plan_poll",
    )
    if planned.get("status") != "awaiting_confirmation":
        _validate_terminal(planned, config.page_count)
        raise HarnessError("JOB_TERMINATED_BEFORE_CONFIRMATION")
    _validate_frozen_plan(planned, config.page_count)
    trace.actions.append("frozen_plan_gate")
    trace.estimated_max_micros = _validate_approved_budget(
        planned,
        config.approved_budget_micros,
    )
    trace.actions.append("approved_budget_gate")
    revision = _plan_revision(planned)
    trace.preconfirm_gate_passed = True

    _request(
        runtime,
        config,
        total_deadline,
        "POST",
        f"{jobs_path}/{job_id}/plan/confirm",
        json_body={"expected_revision": revision},
        expected_status=202,
    )
    trace.actions.extend(["plan_confirm", "confirmed_dispatch"])

    completed = _poll_for(
        runtime,
        config,
        total_deadline,
        f"{jobs_path}/{job_id}",
        TERMINAL_STATUSES,
        trace,
        "result_poll",
    )
    runtime.counters.provider_calls = _validate_terminal(
        completed,
        config.page_count,
    )
    trace.actions.append("result_validation")
    return completed, trace, clock.monotonic() - start


def run_harness(
    config: HarnessConfig,
    *,
    blocked_factory: Callable[[SafetyCounters], Transport] = (
        CalibrateBlockedTransport
    ),
    live_factory: Callable[
        [str, SafetyCounters, str | None], Transport
    ] = LiveHttpTransport,
) -> dict[str, Any]:
    """Run a mode and return a sanitized, machine-readable result."""

    config.validate()
    hashes = source_audit()
    counters = SafetyCounters()
    runtime = create_runtime(
        config,
        counters,
        hashes,
        blocked_factory=blocked_factory,
        live_factory=live_factory,
    )
    trace = WorkflowTrace()
    try:
        terminal, trace, elapsed = run_workflow(config, runtime, trace)
    except HarnessTimeout as exc:
        elapsed = exc.elapsed_seconds
        trace.actions.append(f"{exc.layer}_timeout")
        if not runtime.deadline_is_expected:
            raise
        if elapsed < 10:
            raise HarnessError("CALIBRATION_DURATION_TOO_SHORT") from exc
        return _result(
            config,
            counters,
            hashes,
            trace,
            status="pass",
            outcome="deadline_calibrated",
            elapsed_seconds=elapsed,
            timeout_layer=exc.layer,
        )
    finally:
        runtime.transport.close()
    if runtime.deadline_is_expected:
        raise HarnessError("CALIBRATION_DID_NOT_REACH_TIMEOUT")
    return _result(
        config,
        counters,
        hashes,
        trace,
        status="pass",
        outcome=str(terminal["status"]),
        elapsed_seconds=elapsed,
        timeout_layer=None,
    )


def _result(
    config: HarnessConfig,
    counters: SafetyCounters,
    hashes: Mapping[str, str],
    trace: WorkflowTrace,
    *,
    status: str,
    outcome: str,
    elapsed_seconds: float,
    timeout_layer: str | None,
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "mode": config.mode,
        "status": status,
        "outcome": outcome,
        "timeout_layer": timeout_layer,
        "calibration": {
            "formal_path_covered": trace.actions,
            "logical_elapsed_seconds": round(elapsed_seconds, 6),
            "minimum_required_seconds": 10.0,
        },
        "timeouts": config.timeouts.as_dict(),
        "preconfirm_gate": {
            "passed": trace.preconfirm_gate_passed,
            "approved_budget_micros": config.approved_budget_micros,
            "estimated_max_micros": trace.estimated_max_micros,
        },
        "safety": counters.as_dict(),
        "source_sha256": dict(hashes),
        "candidate_hash_algorithm": (
            "sha256(sorted-name-nul-file-sha256-lf-v1)"
        ),
    }


def _request(
    runtime: RuntimeAdapters,
    config: HarnessConfig,
    total_deadline: float,
    method: str,
    path: str,
    *,
    json_body: Mapping[str, Any] | None,
    expected_status: int,
) -> Mapping[str, Any]:
    remaining = total_deadline - runtime.clock.monotonic()
    if remaining <= 0:
        raise HarnessTimeout(
            "harness_total_deadline",
            config.timeouts.total_deadline_seconds,
        )
    timeout = min(config.timeouts.transport_seconds, remaining)
    response = runtime.transport.request(
        method,
        path,
        headers={"Idempotency-Key": str(uuid4())},
        json_body=json_body,
        timeout_seconds=timeout,
    )
    if response.status_code != expected_status:
        raise HarnessError(f"HTTP_STATUS_{response.status_code}")
    return response.payload


def _poll_for(
    runtime: RuntimeAdapters,
    config: HarnessConfig,
    total_deadline: float,
    path: str,
    desired_statuses: frozenset[str] | set[str],
    trace: WorkflowTrace,
    action: str,
) -> Mapping[str, Any]:
    trace.actions.append(action)
    phase_started = runtime.clock.monotonic()
    phase_deadline = min(
        phase_started + config.timeouts.poll_seconds,
        total_deadline,
    )
    while True:
        now = runtime.clock.monotonic()
        if now >= total_deadline:
            raise HarnessTimeout(
                "harness_total_deadline",
                now - (total_deadline - config.timeouts.total_deadline_seconds),
            )
        if now >= phase_deadline:
            raise HarnessTimeout("poll", now - phase_started)
        payload = _request(
            runtime,
            config,
            total_deadline,
            "GET",
            path,
            json_body=None,
            expected_status=200,
        )
        status = payload.get("status")
        if not isinstance(status, str):
            raise HarnessError("JOB_STATUS_INVALID")
        if status in desired_statuses:
            return payload
        remaining = min(phase_deadline, total_deadline) - runtime.clock.monotonic()
        runtime.clock.sleep(min(config.timeouts.poll_interval_seconds, remaining))


def _plan_revision(payload: Mapping[str, Any]) -> int:
    plan = payload.get("plan")
    if not isinstance(plan, Mapping):
        raise HarnessError("VALID_PLAN_REQUIRED")
    if plan.get("validation_status") != "valid":
        raise HarnessError("VALID_PLAN_REQUIRED")
    revision = plan.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise HarnessError("PLAN_REVISION_INVALID")
    return revision


def _validate_frozen_plan(payload: Mapping[str, Any], page_count: int) -> None:
    screens = _plan_screens(payload)
    if len(screens) != page_count:
        raise HarnessError("PLAN_SCREEN_COUNT")
    normalized_devices: set[str] = set()
    for screen in screens:
        if not isinstance(screen, Mapping):
            raise HarnessError("PLAN_SCREEN_INVALID")
        for field_name in PLAN_CONTENT_FIELDS:
            value = screen.get(field_name)
            if not isinstance(value, str):
                raise HarnessError("PLAN_SCREEN_INVALID")
            normalized = " ".join(value.split()).casefold()
            if field_name == "device":
                if not normalized:
                    raise HarnessError("PLAN_DEVICE_REQUIRED")
                normalized_devices.add(normalized)
            _reject_unsupported_claim(normalized)
    if len(normalized_devices) < 2:
        raise HarnessError("PLAN_DEVICE_DIVERSITY")


def _plan_screens(payload: Mapping[str, Any]) -> list[Any]:
    plan = payload.get("plan")
    if not isinstance(plan, Mapping):
        raise HarnessError("PLAN_SCREENS_MISSING")
    plan_payload = plan.get("payload")
    if not isinstance(plan_payload, Mapping):
        raise HarnessError("PLAN_SCREENS_MISSING")
    screens = plan_payload.get("screens")
    if screens is None:
        screens = plan_payload.get("pages")
    if not isinstance(screens, list):
        raise HarnessError("PLAN_SCREENS_MISSING")
    return screens


def _reject_unsupported_claim(normalized: str) -> None:
    normalized_words = re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE)
    claim_patterns = (
        ("UNSUPPORTED_LEATHER", r"\bleather\b"),
        ("UNSUPPORTED_COMFORT", r"\bcomfort(?:able)?\b"),
        ("UNSUPPORTED_DURABILITY", r"\bdurab(?:ility|le)\b"),
        (
            "UNSUPPORTED_WEAR_RESISTANCE",
            r"\bwear\s+resistan(?:ce|t)\b",
        ),
    )
    for error_code, pattern in claim_patterns:
        if re.search(pattern, normalized_words):
            raise HarnessError(error_code)
    if (
        re.search(r"\bfalilian\b", normalized_words)
        or re.search(r"\bwater\s*mark\b", normalized_words)
        or "水印" in normalized
    ):
        raise HarnessError("WATERMARK_AS_PRODUCT_BRANDING")


def _validate_approved_budget(
    payload: Mapping[str, Any],
    approved_budget_micros: int | None,
) -> int:
    if approved_budget_micros is None:
        raise HarnessError("APPROVED_BUDGET_REQUIRED")
    cost = payload.get("cost")
    if not isinstance(cost, Mapping):
        raise HarnessError("ESTIMATED_COST_MISSING")
    estimated_max = cost.get("estimated_max")
    if not isinstance(estimated_max, Mapping) or "micros" not in estimated_max:
        raise HarnessError("ESTIMATED_COST_MISSING")
    raw_micros = estimated_max["micros"]
    if isinstance(raw_micros, bool):
        raise HarnessError("ESTIMATED_COST_INVALID")
    if isinstance(raw_micros, int):
        estimated_micros = raw_micros
    elif isinstance(raw_micros, str) and re.fullmatch(r"[0-9]+", raw_micros):
        estimated_micros = int(raw_micros)
    else:
        raise HarnessError("ESTIMATED_COST_INVALID")
    if estimated_micros < 0:
        raise HarnessError("ESTIMATED_COST_INVALID")
    if estimated_micros > approved_budget_micros:
        raise HarnessError("APPROVED_BUDGET_EXCEEDED")
    return estimated_micros


def _validate_terminal(payload: Mapping[str, Any], page_count: int) -> int:
    if payload.get("status") != "completed":
        raise HarnessError("JOB_NOT_COMPLETED")
    if payload.get("type") != "detail_pack":
        raise HarnessError("JOB_TYPE_INVALID")
    if payload.get("expected_item_count") != page_count:
        raise HarnessError("RESULT_ITEM_COUNT_INVALID")
    if payload.get("succeeded_item_count") != page_count:
        raise HarnessError("RESULT_SUCCESS_COUNT_INVALID")
    if payload.get("failed_item_count") != 0:
        raise HarnessError("RESULT_FAILURE_COUNT_INVALID")
    plan = payload.get("plan")
    if not isinstance(plan, Mapping) or not plan.get("confirmed_at"):
        raise HarnessError("RESULT_PLAN_NOT_CONFIRMED")
    provider_calls = payload.get("api_request_count")
    if (
        not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
        or provider_calls < page_count + 1
    ):
        raise HarnessError("RESULT_PROVIDER_CALL_COUNT_INVALID")
    return provider_calls


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessError(f"{key.upper()}_INVALID")
    return value


def _normalize_workspace_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) > 5 and parts[3] == "workspaces":
        parts[4] = "ws"
    return "/".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Polaris Xinghe v10 detail-pack real-smoke harness"
    )
    parser.add_argument("mode", choices=("calibrate-blocked", "live"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--workspace-id", default="ws_development")
    parser.add_argument("--asset-id", action="append", default=[])
    parser.add_argument("--product-name", default="v10 smoke product")
    parser.add_argument("--selling-point", action="append", default=[])
    parser.add_argument("--platform-code", default="amazon")
    parser.add_argument("--language", default="en")
    parser.add_argument("--page-count", type=int, default=6)
    parser.add_argument("--approved-budget-micros", type=int)
    parser.add_argument("--transport-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--poll-timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--harness-total-deadline-seconds",
        type=float,
        default=10.0,
    )
    parser.add_argument("--cleanup-margin-seconds", type=float, default=5.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.5)
    parser.add_argument("--live-approval-file", type=Path)
    parser.add_argument("--live-approval-id")
    parser.add_argument("--auth-token-file", type=Path)
    return parser


def config_from_args(arguments: argparse.Namespace) -> HarnessConfig:
    return HarnessConfig(
        mode=str(arguments.mode),
        base_url=str(arguments.base_url),
        workspace_id=str(arguments.workspace_id),
        asset_ids=tuple(arguments.asset_id),
        product_name=str(arguments.product_name),
        selling_points=tuple(arguments.selling_point),
        platform_code=str(arguments.platform_code),
        language=str(arguments.language),
        page_count=int(arguments.page_count),
        approved_budget_micros=arguments.approved_budget_micros,
        timeouts=TimeoutPolicy(
            transport_seconds=float(arguments.transport_timeout_seconds),
            poll_seconds=float(arguments.poll_timeout_seconds),
            total_deadline_seconds=float(
                arguments.harness_total_deadline_seconds
            ),
            cleanup_margin_seconds=float(arguments.cleanup_margin_seconds),
            poll_interval_seconds=float(arguments.poll_interval_seconds),
        ),
        approval_file=arguments.live_approval_file,
        approval_id=arguments.live_approval_id,
        auth_token_file=arguments.auth_token_file,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    config = config_from_args(parser.parse_args(argv))
    try:
        result = run_harness(config)
    except HarnessError as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "mode": config.mode,
            "status": "fail",
            "error_code": exc.code,
            "preconfirm_gate": {
                "passed": False,
                "approved_budget_micros": config.approved_budget_micros,
                "estimated_max_micros": None,
            },
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
