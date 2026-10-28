"""Provider boundary for production-safe execution.

Phase 1.2A intentionally wires only the deterministic fake provider.  Real
providers can implement the same protocol without leaking provider concerns
into job state, leases, credits, or API routes.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4


class ProviderOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class ProviderRequest:
    workspace_id: str
    job_id: str
    job_item_id: str
    attempt_id: str
    operation: str
    idempotency_key: str
    configuration: dict[str, Any]


@dataclass(frozen=True)
class ProviderArtifact:
    payload: bytes
    filename: str
    content_type: str
    width: int
    height: int
    sha256: str
    locally_resampled: bool = False
    provider_width: int | None = None
    provider_height: int | None = None

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError("Provider artifact payload cannot be empty.")
        if hashlib.sha256(self.payload).hexdigest() != self.sha256:
            raise ValueError("Provider artifact SHA-256 does not match its payload.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Provider artifact dimensions must be positive.")
        if (self.provider_width is None) != (self.provider_height is None):
            raise ValueError("Provider artifact source dimensions must be paired.")
        if (
            self.provider_width is not None
            and self.provider_height is not None
            and (self.provider_width <= 0 or self.provider_height <= 0)
        ):
            raise ValueError(
                "Provider artifact source dimensions must be positive."
            )


@dataclass(frozen=True)
class PersistedProviderArtifact:
    filename: str
    content_type: str
    byte_size: int
    width: int
    height: int
    sha256: str
    storage_provider: str
    storage_bucket: str
    object_key: str
    object_version: str
    etag: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    outcome: ProviderOutcome
    provider_request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage_reported: bool = False
    raw_usage: dict[str, Any] = field(default_factory=dict)
    estimated_cost_micros: int | None = 0
    actual_cost_micros: int | None = 0
    retryable: bool = False
    http_status: int | None = None
    artifact: ProviderArtifact | None = None
    persisted_artifact: PersistedProviderArtifact | None = None
    provider_name: str | None = None
    model_name: str | None = None
    pricing_version: str | None = None


class Provider(Protocol):
    name: str
    model: str
    pricing_version: str

    async def execute(self, request: ProviderRequest) -> ProviderResult: ...


class FakeProvider:
    """Zero-cost provider used by all Phase 1.2A runtime paths and tests."""

    name = "mock"
    model = "none"
    pricing_version = "phase1.2a-fake"

    def __init__(
        self,
        *,
        delay_seconds: float = 0,
        outcome: ProviderOutcome = ProviderOutcome.SUCCEEDED,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.outcome = outcome
        self.call_count = 0
        self.external_network_request_count = 0

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.call_count += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        request_id = f"mock_{uuid4().hex}"
        if self.outcome == ProviderOutcome.FAILED:
            return ProviderResult(
                outcome=self.outcome,
                provider_request_id=request_id,
                error_code="MOCK_PROVIDER_FAILED",
                error_message="The fake provider returned a deterministic failure.",
                usage_reported=True,
                raw_usage=_zero_usage(),
            )
        if self.outcome == ProviderOutcome.OUTCOME_UNKNOWN:
            return ProviderResult(
                outcome=self.outcome,
                provider_request_id=None,
                error_code="PROVIDER_OUTCOME_UNKNOWN",
                error_message="The fake provider outcome is intentionally unknown.",
                usage_reported=True,
                raw_usage={**_zero_usage(), "outcome": "unknown"},
            )
        return ProviderResult(
            outcome=self.outcome,
            provider_request_id=request_id,
            usage_reported=True,
            raw_usage=_zero_usage(),
        )


def _zero_usage() -> dict[str, Any]:
    return {
        "provider_type": "mock",
        "fake": True,
        "called": True,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "input_images": 0,
        "output_images": 0,
        "network_requests": 0,
        "cost_micros": 0,
    }
