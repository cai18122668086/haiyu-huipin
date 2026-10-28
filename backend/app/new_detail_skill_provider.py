"""Shared adapters for the new domestic and cross-border detail skills.

The adapters in this module are deliberately registration-only building blocks:
they validate the frozen skill source on every provider call, attach a canonical
planner/prompt contract, and delegate the actual provider work.  They do not
change the existing ``detail_pack`` confirmation state machine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from app.provider import Provider, ProviderOutcome, ProviderRequest, ProviderResult
from app.skill_runtime import (
    SkillManifestRegistry,
    SkillRuntimeError,
)

DETAIL_PACK_OPERATION = "detail_pack"
SKILL_ADAPTER_CONFIGURATION_KEY = "new_skill_adapter"
PLANNER_CONTRACT_VERSION = "new-detail-planner-v1"


class ManifestValidator(Protocol):
    """Minimal source-integrity boundary used by the adapters."""

    def validate(self, skill_id: str) -> str: ...


class BundledSkillManifestValidator:
    """Validate bundled manifests and source trees without invoking a provider."""

    def __init__(
        self,
        registry: SkillManifestRegistry | None = None,
        *,
        source_roots: Mapping[str, Path] | None = None,
    ) -> None:
        self.registry = registry or SkillManifestRegistry.bundled()
        self.source_roots = dict(source_roots or {})

    def validate(self, skill_id: str) -> str:
        source_root = self.source_roots.get(skill_id)
        if source_root is None:
            source_root = (
                Path(__file__).resolve().parents[1] / "skill_sources" / skill_id
            )
        return self.registry.verify_source(skill_id, source_root)


class AdapterValidationError(ValueError):
    """Fail-closed request validation error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DetailSkillRoute:
    skill_id: str
    market_region: str
    configure_prompt: Callable[[Mapping[str, Any]], dict[str, Any]]


class NewDetailSkillProvider:
    """Manifest-gated adapter for one newly installed detail skill.

    ``operation`` stays ``detail_pack`` so the durable job model can be reused.
    A future router only needs to select this adapter by ``market_region``.
    """

    def __init__(
        self,
        delegate: Provider,
        route: DetailSkillRoute,
        *,
        manifest_validator: ManifestValidator | None = None,
    ) -> None:
        self.delegate = delegate
        self.route = route
        self.manifest_validator = (
            manifest_validator or BundledSkillManifestValidator()
        )
        self.name = delegate.name
        self.model = delegate.model
        self.pricing_version = delegate.pricing_version

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            # Integrity is checked on every call and before the delegate can run.
            content_hash = self.manifest_validator.validate(self.route.skill_id)
            routed_request = self.prepare_request(request, content_hash=content_hash)
        except SkillRuntimeError as exc:
            return rejected_result(self, exc.code, str(exc))
        except AdapterValidationError as exc:
            return rejected_result(self, exc.code, str(exc))
        result = await self.delegate.execute(routed_request)
        if result.outcome != ProviderOutcome.SUCCEEDED:
            return result
        return self._validate_output_contract(result, routed_request)

    def prepare_request(
        self,
        request: ProviderRequest,
        *,
        content_hash: str,
    ) -> ProviderRequest:
        if request.operation != DETAIL_PACK_OPERATION:
            raise AdapterValidationError(
                "OPERATION_NOT_SUPPORTED",
                f"{self.route.skill_id} only accepts {DETAIL_PACK_OPERATION}.",
            )
        job_configuration = job_configuration_from(request.configuration)
        market_region = normalized_text(job_configuration.get("market_region"))
        if market_region != self.route.market_region:
            raise AdapterValidationError(
                "DETAIL_MARKET_REGION_MISMATCH",
                (
                    f"{self.route.skill_id} requires market_region="
                    f"{self.route.market_region}."
                ),
            )
        prompt_configuration = self.route.configure_prompt(job_configuration)
        delivery = _detail_delivery_contract(prompt_configuration)
        adapter_configuration = {
            "schema_version": PLANNER_CONTRACT_VERSION,
            "skill_id": self.route.skill_id,
            "skill_content_hash": content_hash,
            "market_region": self.route.market_region,
            "planning": {
                "required": True,
                "persist_validated_plan": True,
                # Aurora v4.3 requires one explicit campaign approval packet
                # before the first pair can be generated.
                "auto_confirm_after_validation": bool(
                    prompt_configuration.get("approval_gate", {}).get(
                        "auto_confirm_after_validation", True
                    )
                ),
                "structure_repair_limit": 1,
                "changes_existing_confirmation_gate": False,
                "approval_scope": prompt_configuration.get("approval_gate", {}).get(
                    "scope"
                ),
            },
            "prompt": prompt_configuration,
            "delivery": delivery,
        }
        return replace(
            request,
            configuration={
                **request.configuration,
                SKILL_ADAPTER_CONFIGURATION_KEY: adapter_configuration,
            },
        )

    def _validate_output_contract(
        self,
        result: ProviderResult,
        request: ProviderRequest,
    ) -> ProviderResult:
        """Validate dimensions/format when the delegate returned an artifact.

        Some provider test doubles and asynchronous delegates return a successful
        result before persistence attaches the artifact.  Preserve that boundary,
        while rejecting a concrete artifact that violates the Skill's delivery
        contract before it can be exposed as a deliverable.
        """

        artifact = result.persisted_artifact or result.artifact
        if artifact is None:
            return result
        adapter = request.configuration.get(SKILL_ADAPTER_CONFIGURATION_KEY)
        delivery = adapter.get("delivery") if isinstance(adapter, Mapping) else None
        if not isinstance(delivery, Mapping):
            return result
        expected = delivery.get("expected_dimensions")
        if isinstance(expected, Mapping):
            width, height = expected.get("width"), expected.get("height")
            if isinstance(width, int) and isinstance(height, int) and (
                artifact.width,
                artifact.height,
            ) != (width, height):
                # Pair canvases are normalized/sliced by the Aurora runtime;
                # the gateway result is allowed to carry the pair dimensions
                # while screen artifacts retain the delivery dimensions.
                pair_expected = delivery.get("pair_canvas_dimensions")
                if isinstance(pair_expected, Mapping) and (
                    artifact.width,
                    artifact.height,
                ) == (pair_expected.get("width"), pair_expected.get("height")):
                    return result
                return replace(
                    result,
                    outcome=ProviderOutcome.FAILED,
                    error_code="DETAIL_OUTPUT_DIMENSIONS_INVALID",
                    error_message=(
                        "Generated detail output does not match the requested "
                        f"canvas {width}x{height}."
                    ),
                    retryable=False,
                    artifact=None,
                    persisted_artifact=None,
                )
        expected_type = delivery.get("content_type")
        if (
            isinstance(expected_type, str)
            and artifact.content_type.lower() != expected_type
        ):
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                error_code="DETAIL_OUTPUT_FORMAT_INVALID",
                error_message=(
                    f"Generated detail output must use {expected_type}."
                ),
                retryable=False,
                artifact=None,
                persisted_artifact=None,
            )
        return result


def job_configuration_from(configuration: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read durable coordinator configuration while supporting direct unit calls."""

    nested = configuration.get("job")
    if nested is None:
        return configuration
    if not isinstance(nested, Mapping):
        raise AdapterValidationError(
            "JOB_CONFIGURATION_INVALID",
            "Provider request job configuration must be an object.",
        )
    settings = nested.get("settings")
    if settings is None:
        return nested
    if not isinstance(settings, Mapping):
        raise AdapterValidationError(
            "JOB_CONFIGURATION_INVALID",
            "Provider request job settings must be an object.",
        )
    return settings


def normalized_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def requested_page_count(configuration: Mapping[str, Any], default: int = 8) -> int:
    value = configuration.get("page_count", default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdapterValidationError(
            "DETAIL_PAGE_COUNT_INVALID",
            "Detail page_count must be a positive integer.",
        )
    return value


def requested_language(configuration: Mapping[str, Any], default: str) -> str:
    value = configuration.get("language")
    if value is None:
        return default
    language = str(value).strip()
    if not language:
        raise AdapterValidationError(
            "DETAIL_LANGUAGE_INVALID",
            "Detail language cannot be empty.",
        )
    return language


def rejected_result(
    provider: Provider,
    error_code: str,
    error_message: str,
) -> ProviderResult:
    """Return a durable failure without fabricating an output artifact."""

    return ProviderResult(
        outcome=ProviderOutcome.FAILED,
        error_code=error_code,
        error_message=error_message,
        usage_reported=False,
        estimated_cost_micros=None,
        actual_cost_micros=None,
        retryable=False,
        provider_name=provider.name,
        model_name=provider.model,
        pricing_version=provider.pricing_version,
    )


def _detail_delivery_contract(prompt: Mapping[str, Any]) -> dict[str, Any]:
    target = prompt.get("target_size")
    expected: dict[str, int] | None = None
    if isinstance(target, Mapping):
        width, height = target.get("width"), target.get("height")
        if isinstance(width, int) and isinstance(height, int):
            expected = {"width": width, "height": height}
    if expected is None:
        resolved = prompt.get("resolved_size")
        if isinstance(resolved, str) and "x" in resolved:
            width_text, height_text = resolved.lower().split("x", maxsplit=1)
            try:
                expected = {"width": int(width_text), "height": int(height_text)}
            except ValueError:
                expected = None
    pair_size = prompt.get("pair_canvas_size")
    pair_expected: dict[str, int] | None = None
    if isinstance(pair_size, str) and "x" in pair_size:
        pair_width, pair_height = pair_size.lower().split("x", maxsplit=1)
        try:
            pair_expected = {"width": int(pair_width), "height": int(pair_height)}
        except ValueError:
            pair_expected = None
    return {
        "separate_module_images": not bool(pair_expected),
        "same_size_across_set": True,
        "expected_dimensions": expected,
        "pair_canvas_dimensions": pair_expected,
        "pair_canvas_aspect_ratio": prompt.get("pair_aspect_ratio"),
        "decomposition_window_aspect_ratio": prompt.get("screen_aspect_ratio"),
        "content_type": {
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "webp": "image/webp",
            "png": "image/png",
        }.get(str(prompt.get("output_format") or "png").lower(), "image/png"),
        # The bundled domestic and cross-border detail Skills emit PNG module
        # files by default.  Keep the contract fallback aligned with the
        # content type fallback so a concrete artifact is not rejected merely
        # because the planner omitted an optional format field.
        "output_format": str(prompt.get("output_format") or "png"),
        "qa_status_required": bool(
            isinstance(prompt.get("delivery"), Mapping)
            and prompt["delivery"].get("qa_status_required", False)
        ),
        "manual_review_required": bool(
            isinstance(prompt.get("delivery"), Mapping)
            and prompt["delivery"].get("manual_review_required", False)
        ),
        "qa_mode": (
            str(prompt["delivery"].get("qa_mode"))
            if isinstance(prompt.get("delivery"), Mapping)
            and prompt["delivery"].get("qa_mode") is not None
            else None
        ),
    }
