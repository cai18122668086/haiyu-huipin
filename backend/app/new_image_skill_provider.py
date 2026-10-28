"""Fail-closed adapter primitives for the new single-image Skill providers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from app.new_detail_skill_provider import SKILL_ADAPTER_CONFIGURATION_KEY
from app.provider import Provider, ProviderOutcome, ProviderRequest, ProviderResult
from app.skill_runtime import SkillRuntimeError

SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION = "single-image-skill-adapter-v1"
SUPPORTED_ASPECT_RATIOS = frozenset(
    {"1:1", "3:4", "4:3", "4:5", "9:16", "16:9"}
)
SUPPORTED_IMAGE_QUALITIES = frozenset({"low", "medium", "high"})
SUPPORTED_OUTPUT_FORMATS = frozenset({"png", "jpeg", "webp"})
SUPPORTED_TARGET_FITS = frozenset({"cover", "contain", "pad"})
TARGET_DIMENSIONS = {
    "1:1": (1024, 1024),
    "3:4": (768, 1024),
    "4:3": (1024, 768),
    "4:5": (1024, 1280),
    "9:16": (576, 1024),
    "16:9": (1024, 576),
}
MAX_SOURCE_ASSETS = 8
_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_CONTENT_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


class ManifestValidator(Protocol):
    """Validate the installed source/manifest pair for one Skill execution."""

    def __call__(self, skill_id: str) -> str: ...


class NewImageSkillContractError(ValueError):
    """A redaction-safe request contract violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NewImageSkillProvider:
    """Validate and normalize one single-image item before delegation.

    The delegate receives only a deterministic, manifest-bound adapter envelope.
    Raw job configuration remains available for durable audit compatibility, but a
    real provider must consume ``new_skill_adapter`` rather than compiling prompts
    from arbitrary configuration keys.
    """

    operation: str
    skill_id: str

    def __init__(
        self,
        delegate: Provider,
        *,
        manifest_validator: ManifestValidator | None = None,
    ) -> None:
        self.delegate = delegate
        self.manifest_validator = manifest_validator
        self.name = delegate.name
        self.model = delegate.model
        self.pricing_version = delegate.pricing_version

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != self.operation:
            return self._rejection(
                "OPERATION_NOT_SUPPORTED",
                f"Expected operation {self.operation}.",
            )

        try:
            input_config, settings, item_config = configuration_sections(
                request.configuration
            )
            self.validate_configuration(input_config, settings, item_config)
        except NewImageSkillContractError as exc:
            return self._rejection(exc.code, str(exc))

        if self.manifest_validator is None:
            return self._rejection(
                "SKILL_MANIFEST_VALIDATOR_REQUIRED",
                "A Skill manifest validator is required before provider execution.",
            )
        try:
            content_hash = self.manifest_validator(self.skill_id)
            if (
                not isinstance(content_hash, str)
                or not _CONTENT_HASH_PATTERN.fullmatch(content_hash.strip())
            ):
                raise NewImageSkillContractError(
                    "SKILL_MANIFEST_VALIDATION_FAILED",
                    "The Skill manifest validator did not return a valid content hash.",
                )
            routed_request = replace(
                request,
                configuration={
                    **request.configuration,
                    SKILL_ADAPTER_CONFIGURATION_KEY: self.build_adapter_envelope(
                        input_config,
                        settings,
                        item_config,
                        content_hash=content_hash.strip(),
                    ),
                },
            )
        except SkillRuntimeError as exc:
            return self._rejection(exc.code, str(exc))
        except NewImageSkillContractError as exc:
            return self._rejection(exc.code, str(exc))
        except Exception as exc:
            return self._rejection(
                "SKILL_MANIFEST_VALIDATION_FAILED",
                "Skill manifest validation failed "
                f"({type(exc).__name__}).",
            )

        result = await self.delegate.execute(routed_request)
        if result.outcome != ProviderOutcome.SUCCEEDED:
            return result
        if not _has_independent_image_artifact(result):
            return self._rejection(
                "GENERATED_IMAGE_ARTIFACT_REQUIRED",
                "A successful generation must contain one real image artifact.",
                result=result,
            )
        return result

    def validate_configuration(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> None:
        """Validate the operation-specific request contract."""

        raise NotImplementedError

    def source_assets(
        self,
        input_config: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        """Return the operation-specific, role-labelled source asset inventory."""

        raise NotImplementedError

    def prompt_input(
        self,
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return only operation-specific prompt fields from an explicit whitelist."""

        raise NotImplementedError

    def target(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Return the operation-specific deterministic delivery target."""

        return controlled_target(settings)

    def build_adapter_envelope(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
        *,
        content_hash: str,
    ) -> dict[str, Any]:
        item_ordinal = normalized_item_ordinal(item_config)
        item_key = normalized_item_key(item_config, item_ordinal=item_ordinal)
        return {
            "schema_version": SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION,
            "skill_id": self.skill_id,
            "skill_content_hash": content_hash,
            "operation": self.operation,
            "item_key": item_key,
            "item_ordinal": item_ordinal,
            "source_assets": self.source_assets(input_config),
            "target": self.target(settings),
            "prompt_input": self.prompt_input(settings, item_config),
        }

    def _rejection(
        self,
        code: str,
        message: str,
        *,
        result: ProviderResult | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            outcome=ProviderOutcome.FAILED,
            provider_request_id=(result.provider_request_id if result else None),
            error_code=code,
            error_message=message,
            usage_reported=(result.usage_reported if result else True),
            raw_usage=(result.raw_usage if result else _zero_rejected_usage(code)),
            estimated_cost_micros=(result.estimated_cost_micros if result else 0),
            actual_cost_micros=(result.actual_cost_micros if result else 0),
            retryable=False,
            http_status=(result.http_status if result else None),
            provider_name=(result.provider_name if result else self.name),
            model_name=(result.model_name if result else self.model),
            pricing_version=(
                result.pricing_version if result else self.pricing_version
            ),
        )


def configuration_sections(
    configuration: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Extract the durable ExecutionCoordinator configuration envelope."""

    job = require_mapping(configuration, "job", code="JOB_CONFIGURATION_REQUIRED")
    input_config = require_mapping(job, "input", code="JOB_INPUT_REQUIRED")
    settings = require_mapping(job, "settings", code="JOB_SETTINGS_REQUIRED")
    item_value = configuration.get("item", {})
    if not isinstance(item_value, Mapping):
        raise NewImageSkillContractError(
            "ITEM_CONFIGURATION_INVALID",
            "item configuration must be an object.",
        )
    return input_config, settings, item_value


def require_mapping(
    source: Mapping[str, Any],
    key: str,
    *,
    code: str,
) -> Mapping[str, Any]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise NewImageSkillContractError(code, f"{key} must be an object.")
    return value


def require_asset_ids(source: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list) or not value:
        raise NewImageSkillContractError(
            "REQUIRED_IMAGE_INPUT_MISSING",
            f"{key} must contain at least one image asset id.",
        )
    return _validated_asset_ids(value, key=key)


def validate_optional_asset_ids(source: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = source.get(key, [])
    if not isinstance(value, list):
        raise NewImageSkillContractError(
            "IMAGE_INPUT_INVALID",
            f"{key} must be an array of image asset ids.",
        )
    return _validated_asset_ids(value, key=key)


def _validated_asset_ids(value: Sequence[object], *, key: str) -> tuple[str, ...]:
    if any(not isinstance(asset_id, str) or not asset_id.strip() for asset_id in value):
        raise NewImageSkillContractError(
            "IMAGE_INPUT_INVALID",
            f"{key} must contain only non-empty image asset ids.",
        )
    normalized = tuple(str(asset_id).strip() for asset_id in value)
    if len(normalized) != len(set(normalized)):
        raise NewImageSkillContractError(
            "IMAGE_INPUT_INVALID",
            f"{key} must not contain duplicate image asset ids.",
        )
    return normalized


def role_labelled_assets(
    *role_groups: tuple[str, Sequence[str]],
) -> list[dict[str, str]]:
    """Build a stable source inventory while preventing cross-role aliasing."""

    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    for role, asset_ids in role_groups:
        for asset_id in asset_ids:
            if asset_id in seen:
                raise NewImageSkillContractError(
                    "IMAGE_INPUT_INVALID",
                    "An image asset id cannot be assigned to multiple source roles.",
                )
            if not _IDENTIFIER_PATTERN.fullmatch(asset_id):
                raise NewImageSkillContractError(
                    "IMAGE_INPUT_INVALID",
                    "Image asset ids must satisfy the single-image delegate contract.",
                )
            if not _IDENTIFIER_PATTERN.fullmatch(role):
                raise NewImageSkillContractError(
                    "IMAGE_INPUT_INVALID",
                    "Image source roles must satisfy the single-image "
                    "delegate contract.",
                )
            seen.add(asset_id)
            assets.append({"asset_id": asset_id, "role": role})
    if not 1 <= len(assets) <= MAX_SOURCE_ASSETS:
        raise NewImageSkillContractError(
            "IMAGE_INPUT_INVALID",
            f"A single image item requires between 1 and {MAX_SOURCE_ASSETS} sources.",
        )
    return assets


def require_image_count(settings: Mapping[str, Any]) -> int:
    value = settings.get("image_count")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 12:
        raise NewImageSkillContractError(
            "IMAGE_COUNT_OUT_OF_RANGE",
            "image_count must be between 1 and 12.",
        )
    return value


def require_non_empty_string(
    source: Mapping[str, Any],
    key: str,
    *,
    code: str,
) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NewImageSkillContractError(code, f"{key} is required.")
    return value.strip()


def optional_string(
    source: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    code: str = "PROMPT_INPUT_INVALID",
) -> str:
    value = source.get(key, default)
    if not isinstance(value, str):
        raise NewImageSkillContractError(code, f"{key} must be a string.")
    return value.strip()


def optional_string_list(
    source: Mapping[str, Any],
    key: str,
    *,
    code: str = "PROMPT_INPUT_INVALID",
) -> list[str]:
    value = source.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(entry, str) for entry in value
    ):
        raise NewImageSkillContractError(code, f"{key} must be an array of strings.")
    return [entry.strip() for entry in value if entry.strip()]


def compact_prompt_input(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop absent optional values while preserving the explicit field whitelist."""

    return {
        key: value
        for key, value in values.items()
        if value != "" and value != []
    }


def normalized_item_ordinal(item_config: Mapping[str, Any]) -> int:
    value = item_config.get("ordinal", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NewImageSkillContractError(
            "ITEM_ORDINAL_INVALID",
            "item ordinal must be a non-negative integer.",
        )
    return value


def normalized_item_key(
    item_config: Mapping[str, Any],
    *,
    item_ordinal: int,
) -> str:
    value = item_config.get("item_key")
    if value is None:
        return f"image-{item_ordinal + 1:03d}"
    if (
        not isinstance(value, str)
        or not _IDENTIFIER_PATTERN.fullmatch(value.strip())
    ):
        raise NewImageSkillContractError(
            "ITEM_KEY_INVALID",
            "item_key must satisfy the single-image delegate identifier contract.",
        )
    return value.strip()


def controlled_target(
    settings: Mapping[str, Any],
    *,
    default_aspect_ratio: str = "1:1",
    allow_follow_reference: bool = False,
    allow_follow_source: bool = False,
) -> dict[str, Any]:
    aspect_ratio = settings.get("aspect_ratio", default_aspect_ratio)
    if aspect_ratio in {"follow-reference", "follow-source"}:
        allowed = (
            aspect_ratio == "follow-reference" and allow_follow_reference
        ) or (aspect_ratio == "follow-source" and allow_follow_source)
        if not allowed:
            raise NewImageSkillContractError(
                "ASPECT_RATIO_NOT_SUPPORTED",
                "The requested aspect_ratio is not supported.",
            )
        quality, output_format = _controlled_image_encoding(settings)
        return {
            "aspect_ratio": aspect_ratio,
            "width": None,
            "height": None,
            "quality": quality,
            "output_format": output_format,
            "fit": "contain",
        }
    if not isinstance(aspect_ratio, str) or aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise NewImageSkillContractError(
            "ASPECT_RATIO_NOT_SUPPORTED",
            "The requested aspect_ratio is not supported.",
        )
    quality, output_format = _controlled_image_encoding(settings)
    fit = settings.get("fit", "cover")
    if not isinstance(fit, str) or fit not in SUPPORTED_TARGET_FITS:
        raise NewImageSkillContractError(
            "TARGET_FIT_NOT_SUPPORTED",
            "The requested target fit is not supported.",
        )
    width, height = TARGET_DIMENSIONS[aspect_ratio]
    return {
        "aspect_ratio": aspect_ratio,
        "width": width,
        "height": height,
        "quality": quality,
        "output_format": output_format,
        "fit": fit,
    }


def _controlled_image_encoding(settings: Mapping[str, Any]) -> tuple[str, str]:
    quality = settings.get("quality", "high")
    if not isinstance(quality, str) or quality not in SUPPORTED_IMAGE_QUALITIES:
        raise NewImageSkillContractError(
            "IMAGE_QUALITY_NOT_SUPPORTED",
            "The requested image quality is not supported.",
        )
    output_format = settings.get("output_format", "png")
    if not isinstance(output_format, str):
        raise NewImageSkillContractError(
            "OUTPUT_FORMAT_NOT_SUPPORTED",
            "The requested output format is not supported.",
        )
    output_format = output_format.lower()
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise NewImageSkillContractError(
            "OUTPUT_FORMAT_NOT_SUPPORTED",
            "The requested output format is not supported.",
        )
    return quality, output_format


def _has_independent_image_artifact(result: ProviderResult) -> bool:
    artifact = result.persisted_artifact or result.artifact
    if artifact is None:
        return False
    if not artifact.content_type.lower().startswith("image/"):
        return False
    if (
        result.persisted_artifact is not None
        and result.persisted_artifact.byte_size <= 0
    ):
        return False
    raw_usage = result.raw_usage
    if raw_usage.get("placeholder") is True or raw_usage.get("contact_sheet") is True:
        return False
    artifact_kind = raw_usage.get("artifact_kind")
    return artifact_kind not in {"placeholder", "contact_sheet", "prompt", "plan"}


def _zero_rejected_usage(code: str) -> dict[str, Any]:
    return {
        "provider_type": "adapter",
        "called": False,
        "runtime_rejected": True,
        "reason": code,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "input_images": 0,
        "output_images": 0,
        "request_count": 0,
    }
