"""Explicit routing for newly installed Skill-backed workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from .domain import JobStatus, JobType
from .models import JobRecord
from .provider import Provider, ProviderOutcome, ProviderRequest, ProviderResult
from .xinghe_agent import OperationRoutingProvider


class DetailPlanner(Protocol):
    async def plan(self, job_id: str) -> JobStatus | None: ...


def _job_settings(configuration: Mapping[str, Any]) -> Mapping[str, Any]:
    job = configuration.get("job")
    if not isinstance(job, Mapping):
        return {}
    settings = job.get("settings")
    return settings if isinstance(settings, Mapping) else {}


def _market_region(configuration: Mapping[str, Any]) -> str:
    value = _job_settings(configuration).get("market_region", "international")
    return str(value).strip().lower().replace("-", "_")


class RegisteredOperationRoutingProvider(OperationRoutingProvider):
    """Extend the legacy router while preserving its public/tested shape."""

    def __init__(
        self,
        default_provider: Provider,
        detail_provider: Provider,
        local_provider: Provider,
        routes: Mapping[str, Provider],
        *,
        domestic_detail_provider: Provider,
        refined_detail_provider: Provider | None = None,
        fast_detail_provider: Provider | None = None,
    ) -> None:
        super().__init__(default_provider, detail_provider, local_provider)
        self.routes = dict(routes)
        self.domestic_detail_provider = domestic_detail_provider
        self.refined_detail_provider = refined_detail_provider
        self.fast_detail_provider = fast_detail_provider

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        if (
            request.operation == "detail_pack"
            and _market_region(request.configuration) == "domestic"
        ):
            if _job_settings(request.configuration).get("detail_skill") == "refined":
                if _job_settings(request.configuration).get("detail_speed") == "fast":
                    return await (
                        self.fast_detail_provider or UnavailableProvider()
                    ).execute(request)
                return await (
                    self.refined_detail_provider or UnavailableProvider()
                ).execute(request)
            return await self.domestic_detail_provider.execute(request)
        provider = self.routes.get(request.operation)
        if provider is not None:
            return await provider.execute(request)
        return await super().execute(request)


class OperationProviderRegistry:
    """Route explicit new operations and delegate legacy behavior unchanged."""

    def __init__(
        self,
        fallback: Provider,
        routes: Mapping[str, Provider],
    ) -> None:
        self.fallback = fallback
        self.routes = dict(routes)
        self.name = fallback.name
        self.model = fallback.model
        self.pricing_version = fallback.pricing_version

    def __getattr__(self, name: str) -> object:
        return getattr(self.fallback, name)

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        provider = self.routes.get(request.operation, self.fallback)
        return await provider.execute(request)


class DetailMarketRoutingProvider:
    """Keep the stable international detail provider and add domestic routing."""

    def __init__(self, international: Provider, domestic: Provider) -> None:
        self.international = international
        self.domestic = domestic
        self.name = international.name
        self.model = international.model
        self.pricing_version = international.pricing_version

    def __getattr__(self, name: str) -> object:
        return getattr(self.international, name)

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        if _market_region(request.configuration) == "domestic":
            return await self.domestic.execute(request)
        return await self.international.execute(request)


class DetailMarketRoutingPlanner:
    """Select a planner by durable job settings without changing old defaults."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        international: DetailPlanner,
        domestic: DetailPlanner,
        amazon: DetailPlanner | None = None,
        refined: DetailPlanner | None = None,
        fast: DetailPlanner | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.international = international
        self.domestic = domestic
        self.amazon = amazon
        self.refined = refined
        self.fast = fast

    def __getattr__(self, name: str) -> object:
        return getattr(self.international, name)

    async def plan(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return None
            is_amazon = job.type == JobType.AMAZON_VISUAL_SUITE.value
            settings = job.configuration.get("settings")
            market_region = (
                str(settings.get("market_region", "international")).strip().lower()
                if isinstance(settings, Mapping)
                else "international"
            )
            refined = (
                isinstance(settings, Mapping)
                and settings.get("detail_skill") == "refined"
            )
            fast = (
                refined
                and isinstance(settings, Mapping)
                and settings.get("detail_speed") == "fast"
            )
        if is_amazon:
            if self.amazon is None:
                return JobStatus.FAILED
            return await self.amazon.plan(job_id)
        planner = self.domestic if market_region == "domestic" else self.international
        if market_region == "domestic" and refined:
            if fast:
                return (
                    await self.fast.plan(job_id)
                    if self.fast is not None
                    else JobStatus.FAILED
                )
            if self.refined is None:
                return JobStatus.FAILED
            planner = self.refined
        return await planner.plan(job_id)


class UnavailableProvider:
    """Fail closed when a real generator has not been registered."""

    name = "unavailable"
    model = "none"
    pricing_version = "not-configured-v1"

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        return ProviderResult(
            outcome=ProviderOutcome.FAILED,
            error_code="PROVIDER_NOT_CONFIGURED",
            error_message=(f"No real provider is configured for {request.operation}."),
            usage_reported=True,
            raw_usage={
                "provider_type": "unavailable",
                "called": False,
                "network_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "input_images": 0,
                "output_images": 0,
            },
            estimated_cost_micros=0,
            actual_cost_micros=0,
            retryable=False,
            provider_name=self.name,
            model_name=self.model,
            pricing_version=self.pricing_version,
        )
