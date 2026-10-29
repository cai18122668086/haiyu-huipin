"""Fail-closed skill runtime boundary for the zero-cost provider path."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from .config import (
    default_skill_root,
)
from .domain import JobType
from .models import AuditEventRecord
from .provider import (
    FakeProvider,
    Provider,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)

SHA256_HEX_LENGTH = 64
MANIFEST_SCHEMA_VERSION = "skill-runtime-manifest-v1"
POLICY_SCHEMA_VERSION = "skill-runtime-policy-v1"
CONTENT_HASH_ALGORITHM = "sha256-ordered-path-nul-file-sha256-lf-v1"
IGNORED_RUNTIME_ARTIFACTS = ("**/__pycache__/**", "**/*.pyc")
KNOWN_SKILLS = {
    "aurora-ecommerce-reference-remix-fast",
    "all-category-style-benchmark-still-fast",
    "aurora-remix-fast",
    "aurora-ecommerce-reference-remix",
    "all-category-style-benchmark-still",
    "masked-image-repair",
    "xinghe-ecommerce-detail",
    "xinghe-wanneng-shengtu-3-0",
    "xinghe-reference-image-remix-3",
    "reference-visual-reverse-remix",
    "ecommerce-model-pose-transfer",
    "xinghe-ecommerce-creative-image",
    "xinghe-ecommerce-detail-domestic-3",
    "xinghe-ecommerce-detail-domestic-4",
    "xinghe-ecommerce-detail-domestic-4-fast",
    "aurora-domestic-ecommerce-detail-page",
    "xinghe-crossborder-detail-3",
    "xinghe-amazon-visual-suite",
    "xinghe-amazon-visual-suite-fast",
    "aurora-domestic-ecommerce-main-image",
    "aurora-domestic-ecommerce-main-image-fast",
    "aurora-domestic-ecommerce-main-image-native-fast",
}
OPERATION_SKILLS = {
    JobType.LOCAL_EDIT.value: "masked-image-repair",
    JobType.PRODUCT_IMAGE.value: "xinghe-wanneng-shengtu-3-0",
    JobType.REFERENCE_REMIX.value: "xinghe-reference-image-remix-3",
    JobType.MODEL_POSE_TRANSFER.value: "ecommerce-model-pose-transfer",
    JobType.CREATIVE_IMAGE.value: "xinghe-ecommerce-creative-image",
    JobType.DETAIL_PACK.value: "xinghe-ecommerce-detail",
    JobType.AMAZON_VISUAL_SUITE.value: "xinghe-amazon-visual-suite",
    JobType.DOMESTIC_MAIN_IMAGE.value: "aurora-domestic-ecommerce-main-image",
}
ALLOWED_PROVIDER_TYPES = {"fake", "mock"}
SENSITIVE_DETAIL_KEYS = {
    "api_key",
    "authorization",
    "configuration",
    "input",
    "object_key",
    "prompt",
    "secret",
    "storage_key",
}
SENSITIVE_DETAIL_KEY_FRAGMENTS = {
    "api_key",
    "object_key",
    "prompt",
    "raw_input",
    "secret",
    "storage_key",
}


class SkillCapability(StrEnum):
    MASKED_IMAGE_REPAIR = "masked_image_repair"
    PRODUCT_IMAGE = "product_image"
    REFERENCE_REMIX = "reference_remix"
    MODEL_POSE_TRANSFER = "model_pose_transfer"
    CREATIVE_IMAGE = "creative_image"
    ECOMMERCE_DETAIL_PACK = "ecommerce_detail_pack"
    AMAZON_VISUAL_SUITE = "amazon_visual_suite"
    DOMESTIC_MAIN_IMAGE = "domestic_main_image"


class SafeExpansionMode(StrEnum):
    DISABLED = "disabled"
    PREAUTHORIZED_ONLY = "preauthorized_only"


class IterationMode(StrEnum):
    DISABLED = "disabled"


class IterationTrigger(StrEnum):
    EXPLICIT_USER_REQUEST_ONLY = "explicit_user_request_only"


@dataclass(frozen=True)
class SkillFileLock:
    path: str
    sha256: str


@dataclass(frozen=True)
class SkillContract:
    schema_id: str
    required_fields: tuple[str, ...]


@dataclass(frozen=True)
class SkillProfile:
    profile_id: str
    market_scope: str
    enabled: bool
    default_language: str
    default_page_count: int
    min_page_count: int
    max_page_count: int


@dataclass(frozen=True)
class IterationPolicy:
    version: str
    mode: IterationMode
    max_iteration_rounds: int
    trigger: IterationTrigger


@dataclass(frozen=True)
class SafeExpansionPolicy:
    halo_pixels: int
    max_distance_relative: float
    max_distance_absolute_pixels: int
    max_added_pixels_relative: float
    max_added_pixels_absolute: int
    max_relative_expansion: float
    layout_adaptation_allowed: bool
    forbidden_layout_adaptations: tuple[str, ...]
    protected_boundaries: tuple[str, ...]
    outside_effective_selection_changed_pixels: int
    unsafe_outcomes: tuple[str, ...]
    actual_budget_must_not_exceed_request: bool


@dataclass(frozen=True)
class SkillPolicy:
    schema_version: str
    auto_review_regeneration_allowed: bool
    successful_page_auto_redraw_allowed: bool
    safe_expansion_mode: SafeExpansionMode
    policy_version: str
    active_profile_id: str | None
    profiles: tuple[SkillProfile, ...]
    iteration: IterationPolicy | None
    safe_expansion: SafeExpansionPolicy | None


@dataclass(frozen=True)
class SkillManifest:
    schema_version: str
    content_hash_algorithm: str
    ignored_runtime_artifacts: tuple[str, ...]
    skill_id: str
    content_hash: str
    capability: SkillCapability
    input_contract: SkillContract
    output_contract: SkillContract
    policy: SkillPolicy
    provider_type: str
    cost_limit_micros: int
    real_provider_request_limit: int
    files: tuple[SkillFileLock, ...]


@dataclass(frozen=True)
class SafeExpansionAuthorization:
    authorization_id: str
    workspace_id: str
    policy_version: str
    user_selection_sha256: str
    safe_region_sha256: str
    safe_region_width_pixels: int
    safe_region_height_pixels: int
    user_selection_area_pixels: int
    requested_max_added_pixels: int
    requested_max_expansion_distance_pixels: int
    requested_max_relative_expansion: float
    required_pixels_outside_safe_region_count: int
    protected_structure_intersection_count: int


@dataclass(frozen=True)
class SafeExpansionObservation:
    added_authority_pixels: int
    max_expansion_distance_pixels: int
    relative_expansion: float
    content_alpha_outside_effective_selection: int
    outside_effective_selection_changed_pixels: int
    protected_structure_intersection_count: int


@dataclass(frozen=True)
class RuntimePolicyRequest:
    auto_review_regeneration_requested: bool = False
    successful_page_auto_redraw_requested: bool = False
    iteration_requested: bool = False
    requested_iteration_rounds: int = 0
    iteration_trigger: IterationTrigger = IterationTrigger.EXPLICIT_USER_REQUEST_ONLY
    safe_expansion_requested: bool = False
    layout_adaptation_requested: bool = False
    safe_expansion_authorization: SafeExpansionAuthorization | None = None


@dataclass(frozen=True)
class SkillExecutionRequest:
    skill_id: str
    skill_content_hash: str
    workspace_id: str
    job_id: str
    job_item_id: str
    attempt_id: str
    operation: str
    idempotency_key: str
    provider_type: str
    cost_limit_micros: int
    policy: RuntimePolicyRequest
    profile_id: str | None = None
    language: str | None = None
    page_count: int | None = None


@dataclass(frozen=True)
class SkillRuntimeAuditEvent:
    action: str
    workspace_id: str
    job_id: str
    attempt_id: str
    details: dict[str, str | int | float | bool | None]


class SkillRuntimeAuditSink(Protocol):
    def record(self, event: SkillRuntimeAuditEvent) -> None: ...


class SkillRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillManifestRegistry:
    """Loads immutable manifests and verifies their canonical content locks."""

    def __init__(self, manifests: dict[str, SkillManifest]) -> None:
        self._manifests = dict(manifests)

    @classmethod
    def bundled(cls, directory: Path | None = None) -> SkillManifestRegistry:
        root = directory or Path(__file__).with_name("skill_manifests")
        if not root.is_dir():
            raise SkillRuntimeError(
                "SKILL_MANIFEST_MISSING",
                "The bundled skill manifest directory is missing.",
            )
        manifests: dict[str, SkillManifest] = {}
        for path in sorted(root.glob("*.json")):
            manifest = _load_manifest(path)
            if manifest.skill_id in manifests:
                raise SkillRuntimeError(
                    "SKILL_MANIFEST_INVALID",
                    f"Duplicate manifest for {manifest.skill_id}.",
                )
            manifests[manifest.skill_id] = manifest
        return cls(manifests)

    def get(self, skill_id: str) -> SkillManifest:
        if skill_id not in KNOWN_SKILLS:
            raise SkillRuntimeError("UNKNOWN_SKILL", f"Unknown skill: {skill_id}.")
        manifest = self._manifests.get(skill_id)
        if manifest is None:
            raise SkillRuntimeError(
                "SKILL_MANIFEST_MISSING",
                f"Manifest missing for {skill_id}.",
            )
        _validate_manifest_lock(manifest)
        return manifest

    def verify_source(self, skill_id: str, source_root: Path) -> str:
        """Verify a source tree against the complete, pinned file inventory."""

        manifest = self.get(skill_id)
        if not source_root.is_dir():
            raise SkillRuntimeError(
                "SKILL_SOURCE_MISSING",
                f"Skill source missing for {skill_id}.",
            )
        actual_paths = {
            path.relative_to(source_root).as_posix()
            for path in source_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        }
        expected_paths = {item.path for item in manifest.files}
        if actual_paths != expected_paths:
            raise SkillRuntimeError(
                "SKILL_CONTENT_HASH_MISMATCH",
                f"Skill file inventory drifted for {skill_id}.",
            )
        actual_locks = tuple(
            SkillFileLock(
                path=relative,
                sha256=_sha256_file(source_root / Path(relative)),
            )
            for relative in sorted(actual_paths)
        )
        actual_hash = _aggregate_content_hash(actual_locks)
        if actual_hash != manifest.content_hash:
            raise SkillRuntimeError(
                "SKILL_CONTENT_HASH_MISMATCH",
                f"Skill content drifted for {skill_id}.",
            )
        return actual_hash

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._manifests))


class PostgresSkillRuntimeAuditSink:
    """Persists redacted runtime decisions in the existing audit_events table."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def record(self, event: SkillRuntimeAuditEvent) -> None:
        if any(_is_sensitive_detail_key(key) for key in event.details):
            raise SkillRuntimeError(
                "SKILL_AUDIT_UNSAFE",
                "Sensitive fields are not allowed in runtime audit details.",
            )
        with self.session_factory() as session:
            session.add(
                AuditEventRecord(
                    workspace_id=event.workspace_id,
                    action=event.action,
                    resource_type="job",
                    resource_id=event.job_id,
                    request_id=event.attempt_id,
                    details=dict(event.details),
                )
            )
            session.commit()


class SkillRuntime:
    """Validates policy and integrity before a provider can be invoked."""

    def __init__(
        self,
        registry: SkillManifestRegistry,
        audit_sink: SkillRuntimeAuditSink,
        *,
        global_cost_limit_micros: int = 0,
        real_provider_request_limit: int = 0,
        source_roots: dict[str, Path] | None = None,
    ) -> None:
        if global_cost_limit_micros != 0:
            raise ValueError("The foundation runtime cost limit must remain zero.")
        if real_provider_request_limit != 0:
            raise ValueError("Real provider requests must remain disabled.")
        self.registry = registry
        self.audit_sink = audit_sink
        self.global_cost_limit_micros = global_cost_limit_micros
        self.real_provider_request_limit = real_provider_request_limit
        self.source_roots = dict(source_roots) if source_roots is not None else None

    def authorize(self, request: SkillExecutionRequest) -> SkillManifest:
        manifest = self.registry.get(request.skill_id)
        if self.source_roots is not None:
            source_root = self.source_roots.get(request.skill_id)
            if source_root is None:
                raise SkillRuntimeError(
                    "SKILL_SOURCE_MISSING",
                    f"Skill source missing for {request.skill_id}.",
                )
            self.registry.verify_source(request.skill_id, source_root)
        self._validate_request(manifest, request)
        details = self._audit_details(manifest, request)
        try:
            self.audit_sink.record(
                SkillRuntimeAuditEvent(
                    action="skill_runtime.execution_authorized",
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    attempt_id=request.attempt_id,
                    details=details,
                )
            )
        except SkillRuntimeError:
            raise
        except Exception as exc:
            raise SkillRuntimeError(
                "SKILL_AUDIT_WRITE_FAILED",
                "Runtime authorization audit could not be recorded.",
            ) from exc
        return manifest

    def validate_safe_expansion_observation(
        self,
        request: SkillExecutionRequest,
        observation: SafeExpansionObservation,
    ) -> None:
        """Validate future expansion output without executing pixel repair."""

        manifest = self.registry.get(request.skill_id)
        self._validate_safe_expansion(manifest, request)
        authorization = request.policy.safe_expansion_authorization
        if authorization is None:
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
                "Safe expansion requires an authorization.",
            )
        if (
            observation.added_authority_pixels < 0
            or observation.max_expansion_distance_pixels < 0
            or observation.relative_expansion < 0
            or observation.added_authority_pixels
            > authorization.requested_max_added_pixels
            or observation.max_expansion_distance_pixels
            > authorization.requested_max_expansion_distance_pixels
            or observation.relative_expansion
            > authorization.requested_max_relative_expansion
        ):
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_BUDGET_EXCEEDED",
                "Observed expansion exceeds the authorized request budget.",
            )
        if (
            observation.content_alpha_outside_effective_selection != 0
            or observation.outside_effective_selection_changed_pixels != 0
            or observation.protected_structure_intersection_count != 0
        ):
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_UNSAFE",
                "Observed expansion violates the authority boundary.",
            )
        try:
            self.audit_sink.record(
                SkillRuntimeAuditEvent(
                    action="skill_runtime.safe_expansion_verified",
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    attempt_id=request.attempt_id,
                    details={
                        "skill_id": manifest.skill_id,
                        "policy_version": manifest.policy.policy_version,
                        "authorization_id": authorization.authorization_id,
                        "added_authority_pixels": (
                            observation.added_authority_pixels
                        ),
                        "max_expansion_distance_pixels": (
                            observation.max_expansion_distance_pixels
                        ),
                        "relative_expansion": observation.relative_expansion,
                        "content_alpha_outside_effective_selection": 0,
                        "outside_effective_selection_changed_pixels": 0,
                        "protected_structure_intersection_count": 0,
                    },
                )
            )
        except SkillRuntimeError:
            raise
        except Exception as exc:
            raise SkillRuntimeError(
                "SKILL_AUDIT_WRITE_FAILED",
                "Expansion verification audit could not be recorded.",
            ) from exc

    def record_completion(
        self,
        manifest: SkillManifest,
        request: SkillExecutionRequest,
        result: ProviderResult,
    ) -> None:
        self._validate_completion_contract(result, request)
        details = self._audit_details(manifest, request)
        details.update(
            {
                "outcome": result.outcome.value,
                "usage_reported": result.usage_reported,
                "estimated_cost_micros": result.estimated_cost_micros,
                "actual_cost_micros": result.actual_cost_micros,
            }
        )
        try:
            self.audit_sink.record(
                SkillRuntimeAuditEvent(
                    action="skill_runtime.execution_completed",
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    attempt_id=request.attempt_id,
                    details=details,
                )
            )
        except SkillRuntimeError:
            raise
        except Exception as exc:
            raise SkillRuntimeError(
                "SKILL_AUDIT_WRITE_FAILED",
                "Runtime completion audit could not be recorded.",
            ) from exc

    def record_rejection(
        self,
        request: SkillExecutionRequest,
        error_code: str,
    ) -> None:
        try:
            self.audit_sink.record(
                SkillRuntimeAuditEvent(
                    action="skill_runtime.execution_rejected",
                    workspace_id=request.workspace_id,
                    job_id=request.job_id,
                    attempt_id=request.attempt_id,
                    details={
                        "skill_id": request.skill_id,
                        "skill_content_hash": request.skill_content_hash,
                        "provider_type": request.provider_type,
                        "cost_limit_micros": request.cost_limit_micros,
                        "error_code": error_code,
                        "idempotency_key_sha256": _sha256_text(
                            request.idempotency_key
                        ),
                    },
                )
            )
        except Exception:
            return

    def _validate_request(
        self,
        manifest: SkillManifest,
        request: SkillExecutionRequest,
    ) -> None:
        if request.skill_content_hash != manifest.content_hash:
            raise SkillRuntimeError(
                "SKILL_CONTENT_HASH_MISMATCH",
                "Requested skill content does not match the locked manifest.",
            )
        provider_type = _normalize_provider_type(request.provider_type)
        if provider_type not in ALLOWED_PROVIDER_TYPES:
            raise SkillRuntimeError(
                "SKILL_PROVIDER_NOT_ALLOWED",
                "Only Fake/Mock providers are allowed in this runtime.",
            )
        if _normalize_provider_type(manifest.provider_type) != provider_type:
            raise SkillRuntimeError(
                "SKILL_PROVIDER_NOT_ALLOWED",
                "Provider type does not match the skill manifest.",
            )
        if (
            request.cost_limit_micros != 0
            or request.cost_limit_micros != manifest.cost_limit_micros
            or manifest.cost_limit_micros != self.global_cost_limit_micros
        ):
            raise SkillRuntimeError(
                "SKILL_COST_LIMIT_INVALID",
                "The skill runtime cost limit must be exactly zero.",
            )
        if (
            manifest.real_provider_request_limit != 0
            or self.real_provider_request_limit != 0
        ):
            raise SkillRuntimeError(
                "SKILL_PROVIDER_NOT_ALLOWED",
                "Real provider requests must remain disabled.",
            )
        if (
            request.policy.auto_review_regeneration_requested
            and not manifest.policy.auto_review_regeneration_allowed
        ):
            raise SkillRuntimeError(
                "SKILL_AUTO_REDRAW_NOT_AUTHORIZED",
                "Automatic review and regeneration is disabled.",
            )
        if (
            request.policy.successful_page_auto_redraw_requested
            and not manifest.policy.successful_page_auto_redraw_allowed
        ):
            raise SkillRuntimeError(
                "SKILL_AUTO_REDRAW_NOT_AUTHORIZED",
                "Successful pages cannot be redrawn automatically.",
            )
        self._validate_iteration_and_profile(manifest, request)
        self._validate_safe_expansion(manifest, request)

    @staticmethod
    def _validate_iteration_and_profile(
        manifest: SkillManifest,
        request: SkillExecutionRequest,
    ) -> None:
        policy = request.policy
        if (
            policy.iteration_requested
            or policy.requested_iteration_rounds != 0
            or policy.iteration_trigger
            != IterationTrigger.EXPLICIT_USER_REQUEST_ONLY
        ):
            raise SkillRuntimeError(
                "SKILL_ITERATION_NOT_ALLOWED",
                "Automatic reflection and iteration are disabled.",
            )
        iteration = manifest.policy.iteration
        if iteration is not None and (
            iteration.mode != IterationMode.DISABLED
            or iteration.max_iteration_rounds != 0
            or iteration.trigger != IterationTrigger.EXPLICIT_USER_REQUEST_ONLY
        ):
            raise SkillRuntimeError(
                "SKILL_POLICY_INVALID",
                "The manifest iteration policy is not fail-closed.",
            )
        if manifest.skill_id != "xinghe-ecommerce-detail":
            if (
                request.profile_id is not None
                or request.language is not None
                or request.page_count is not None
            ):
                raise SkillRuntimeError(
                    "SKILL_PROFILE_NOT_ALLOWED",
                    "This skill does not accept a market profile.",
                )
            return
        profile = _resolve_profile(manifest, request.profile_id)
        if not profile.enabled:
            raise SkillRuntimeError(
                "SKILL_PROFILE_NOT_ALLOWED",
                "The requested market profile is disabled.",
            )
        language = request.language or profile.default_language
        if not language.strip():
            raise SkillRuntimeError(
                "SKILL_PROFILE_INVALID",
                "The runtime language must be explicit.",
            )
        page_count = (
            request.page_count
            if request.page_count is not None
            else profile.default_page_count
        )
        if (
            isinstance(page_count, bool)
            or not isinstance(page_count, int)
            or not profile.min_page_count <= page_count <= profile.max_page_count
        ):
            raise SkillRuntimeError(
                "SKILL_PAGE_COUNT_INVALID",
                "The requested page count must remain within 1-12.",
            )

    @staticmethod
    def _validate_safe_expansion(
        manifest: SkillManifest,
        request: SkillExecutionRequest,
    ) -> None:
        policy = request.policy
        if policy.layout_adaptation_requested:
            raise SkillRuntimeError(
                "SKILL_LAYOUT_ADAPTATION_NOT_ALLOWED",
                "Layout adaptation is disabled by the authority policy.",
            )
        if not policy.safe_expansion_requested:
            if policy.safe_expansion_authorization is not None:
                raise SkillRuntimeError(
                    "SKILL_SAFE_EXPANSION_NOT_REQUESTED",
                    "Expansion authorization was supplied without a request.",
                )
            return
        if manifest.policy.safe_expansion_mode != SafeExpansionMode.PREAUTHORIZED_ONLY:
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
                "This skill does not allow safe expansion.",
            )
        expansion_policy = manifest.policy.safe_expansion
        if expansion_policy is None:
            raise SkillRuntimeError(
                "SKILL_POLICY_MISSING",
                "Safe expansion policy is missing.",
            )
        if expansion_policy.layout_adaptation_allowed:
            raise SkillRuntimeError(
                "SKILL_POLICY_INVALID",
                "Layout adaptation must remain disabled.",
            )
        authorization = policy.safe_expansion_authorization
        if authorization is None:
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
                "Safe expansion requires an explicit bounded authorization.",
            )
        if authorization.workspace_id != request.workspace_id:
            raise SkillRuntimeError(
                "SKILL_EXPANSION_WORKSPACE_MISMATCH",
                "Expansion authorization belongs to another workspace.",
            )
        if authorization.policy_version != manifest.policy.policy_version:
            raise SkillRuntimeError(
                "SKILL_POLICY_VERSION_MISMATCH",
                "Expansion policy version does not match the manifest.",
            )
        if not authorization.authorization_id.strip():
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
                "Expansion authorization must be auditable.",
            )
        for digest in (
            authorization.user_selection_sha256,
            authorization.safe_region_sha256,
        ):
            _require_sha256(digest, "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED")
        if (
            authorization.safe_region_width_pixels <= 0
            or authorization.safe_region_height_pixels <= 0
            or authorization.user_selection_area_pixels <= 0
            or authorization.requested_max_added_pixels <= 0
            or authorization.requested_max_expansion_distance_pixels <= 0
            or not 0 < authorization.requested_max_relative_expansion <= 1
            or authorization.required_pixels_outside_safe_region_count != 0
            or authorization.protected_structure_intersection_count != 0
        ):
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
                "Expansion authorization is incomplete or crosses a "
                "protected boundary.",
            )
        safe_region_short_side = min(
            authorization.safe_region_width_pixels,
            authorization.safe_region_height_pixels,
        )
        max_distance = max(
            1,
            min(
                math.floor(
                    safe_region_short_side
                    * expansion_policy.max_distance_relative
                ),
                expansion_policy.max_distance_absolute_pixels,
            ),
        )
        max_added_pixels = max(
            1,
            min(
                math.floor(
                    authorization.user_selection_area_pixels
                    * expansion_policy.max_added_pixels_relative
                ),
                expansion_policy.max_added_pixels_absolute,
            ),
        )
        if (
            authorization.requested_max_expansion_distance_pixels > max_distance
            or authorization.requested_max_added_pixels > max_added_pixels
            or authorization.requested_max_relative_expansion
            > expansion_policy.max_relative_expansion
        ):
            raise SkillRuntimeError(
                "SKILL_SAFE_EXPANSION_BUDGET_EXCEEDED",
                "Requested expansion exceeds the scale-aware policy budget.",
            )

    @staticmethod
    def _validate_completion_contract(
        result: ProviderResult,
        request: SkillExecutionRequest,
    ) -> None:
        if not result.usage_reported:
            raise SkillRuntimeError(
                "SKILL_USAGE_CONTRACT_MISSING",
                "Foundation completion requires explicit zero usage.",
            )
        provider_type = _normalize_provider_type(
            str(result.raw_usage.get("provider_type", ""))
        )
        if provider_type != _normalize_provider_type(request.provider_type):
            raise SkillRuntimeError(
                "SKILL_USAGE_CONTRACT_MISSING",
                "Usage provider type does not match the authorized provider.",
            )
        for key in (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "input_images",
            "output_images",
            "network_requests",
            "cost_micros",
        ):
            value = result.raw_usage.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value != 0:
                raise SkillRuntimeError(
                    "SKILL_USAGE_CONTRACT_MISSING",
                    "Foundation completion requires explicit zero usage fields.",
                )
        for value in (result.estimated_cost_micros, result.actual_cost_micros):
            if value is None:
                raise SkillRuntimeError(
                    "SKILL_COST_CONTRACT_MISSING",
                    "Foundation completion requires explicit zero cost.",
                )
            if value < 0 or value > request.cost_limit_micros:
                raise SkillRuntimeError(
                    "SKILL_COST_LIMIT_EXCEEDED",
                    "Provider result exceeded the zero-cost runtime limit.",
                )

    @staticmethod
    def _audit_details(
        manifest: SkillManifest,
        request: SkillExecutionRequest,
    ) -> dict[str, str | int | float | bool | None]:
        authorization = request.policy.safe_expansion_authorization
        profile = (
            _resolve_profile(manifest, request.profile_id)
            if manifest.policy.profiles
            else None
        )
        return {
            "skill_id": manifest.skill_id,
            "skill_content_hash": manifest.content_hash,
            "capability": manifest.capability.value,
            "policy_version": manifest.policy.policy_version,
            "policy_schema_version": manifest.policy.schema_version,
            "provider_type": request.provider_type,
            "cost_limit_micros": request.cost_limit_micros,
            "real_provider_request_limit": manifest.real_provider_request_limit,
            "auto_review_regeneration_requested": (
                request.policy.auto_review_regeneration_requested
            ),
            "successful_page_auto_redraw_requested": (
                request.policy.successful_page_auto_redraw_requested
            ),
            "iteration_requested": request.policy.iteration_requested,
            "requested_iteration_rounds": (
                request.policy.requested_iteration_rounds
            ),
            "iteration_trigger": request.policy.iteration_trigger.value,
            "profile_id": profile.profile_id if profile else None,
            "market_scope": profile.market_scope if profile else None,
            "language": (
                request.language or profile.default_language if profile else None
            ),
            "page_count": (
                request.page_count
                if request.page_count is not None
                else (profile.default_page_count if profile else None)
            ),
            "safe_expansion_requested": request.policy.safe_expansion_requested,
            "layout_adaptation_requested": (
                request.policy.layout_adaptation_requested
            ),
            "expansion_authorization_id": (
                authorization.authorization_id if authorization else None
            ),
            "expansion_max_added_pixels": (
                authorization.requested_max_added_pixels if authorization else None
            ),
            "expansion_max_distance_pixels": (
                authorization.requested_max_expansion_distance_pixels
                if authorization
                else None
            ),
            "expansion_max_relative": (
                authorization.requested_max_relative_expansion
                if authorization
                else None
            ),
            "safe_region_width_pixels": (
                authorization.safe_region_width_pixels if authorization else None
            ),
            "safe_region_height_pixels": (
                authorization.safe_region_height_pixels if authorization else None
            ),
            "user_selection_area_pixels": (
                authorization.user_selection_area_pixels if authorization else None
            ),
            "protected_structure_intersection_count": (
                authorization.protected_structure_intersection_count
                if authorization
                else None
            ),
            "required_pixels_outside_safe_region_count": (
                authorization.required_pixels_outside_safe_region_count
                if authorization
                else None
            ),
            "user_selection_sha256": (
                authorization.user_selection_sha256 if authorization else None
            ),
            "safe_region_sha256": (
                authorization.safe_region_sha256 if authorization else None
            ),
            "job_item_id": request.job_item_id,
            "operation": request.operation,
            "idempotency_key_sha256": _sha256_text(request.idempotency_key),
        }


class SkillRuntimeProvider:
    """Provider adapter that makes the runtime gate explicit and testable."""

    def __init__(
        self,
        provider: Provider,
        runtime: SkillRuntime,
    ) -> None:
        self.provider = provider
        self.runtime = runtime
        self.name = provider.name
        self.model = provider.model
        self.pricing_version = provider.pricing_version

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        skill_id = OPERATION_SKILLS.get(request.operation, request.operation)
        language, page_count = _xinghe_request_options(
            skill_id,
            request.configuration,
        )
        runtime_request = SkillExecutionRequest(
            skill_id=skill_id,
            skill_content_hash="",
            workspace_id=request.workspace_id,
            job_id=request.job_id,
            job_item_id=request.job_item_id,
            attempt_id=request.attempt_id,
            operation=request.operation,
            idempotency_key=request.idempotency_key,
            provider_type=self.provider.name,
            cost_limit_micros=0,
            policy=RuntimePolicyRequest(),
            language=language,
            page_count=page_count,
        )
        try:
            manifest = self.runtime.registry.get(skill_id)
            runtime_request = replace(
                runtime_request,
                skill_content_hash=manifest.content_hash,
                cost_limit_micros=manifest.cost_limit_micros,
            )
        except SkillRuntimeError as exc:
            self.runtime.record_rejection(runtime_request, exc.code)
            return _rejected_result(exc)

        try:
            manifest = self.runtime.authorize(runtime_request)
        except SkillRuntimeError as exc:
            self.runtime.record_rejection(runtime_request, exc.code)
            return _rejected_result(exc)

        result = await self.provider.execute(request)
        try:
            self.runtime.record_completion(manifest, runtime_request, result)
        except SkillRuntimeError as exc:
            self.runtime.record_rejection(runtime_request, exc.code)
            return _rejected_result(exc)
        return result


def build_fake_skill_runtime_provider(
    session_factory: sessionmaker[Session],
    delay_seconds: float,
) -> SkillRuntimeProvider:
    """Build the only provider path authorized by the foundation batch."""

    registry = SkillManifestRegistry.bundled()
    runtime = SkillRuntime(
        registry,
        PostgresSkillRuntimeAuditSink(session_factory),
        global_cost_limit_micros=0,
        real_provider_request_limit=0,
        source_roots={
            skill_id: default_skill_root(skill_id) for skill_id in KNOWN_SKILLS
        },
    )
    return SkillRuntimeProvider(
        FakeProvider(delay_seconds=delay_seconds),
        runtime,
    )


def _load_manifest(path: Path) -> SkillManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillRuntimeError(
            "SKILL_MANIFEST_INVALID",
            f"Could not load skill manifest {path.name}.",
        ) from exc
    if not isinstance(payload, dict):
        raise SkillRuntimeError(
            "SKILL_MANIFEST_INVALID",
            f"Skill manifest {path.name} must be an object.",
        )
    if "policy" not in payload:
        raise SkillRuntimeError(
            "SKILL_POLICY_MISSING",
            f"Skill manifest {path.name} has no policy.",
        )
    try:
        policy_payload = _mapping(payload["policy"])
        input_payload = _mapping(payload["input_contract"])
        output_payload = _mapping(payload["output_contract"])
        profiles_payload = policy_payload.get("profiles", [])
        if not isinstance(profiles_payload, list):
            raise TypeError("profiles must be a list")
        iteration_payload = policy_payload.get("iteration")
        safe_expansion_payload = policy_payload.get("safe_expansion")
        files_payload = payload["files"]
        if not isinstance(files_payload, list):
            raise TypeError("files must be a list")
        manifest = SkillManifest(
            schema_version=str(payload["schema_version"]),
            content_hash_algorithm=str(payload["content_hash_algorithm"]),
            ignored_runtime_artifacts=_string_tuple(
                payload["ignored_runtime_artifacts"]
            ),
            skill_id=str(payload["skill_id"]),
            content_hash=str(payload["content_hash"]),
            capability=SkillCapability(str(payload["capability"])),
            input_contract=SkillContract(
                schema_id=str(input_payload["schema_id"]),
                required_fields=_string_tuple(input_payload["required_fields"]),
            ),
            output_contract=SkillContract(
                schema_id=str(output_payload["schema_id"]),
                required_fields=_string_tuple(output_payload["required_fields"]),
            ),
            policy=SkillPolicy(
                schema_version=str(policy_payload["schema_version"]),
                auto_review_regeneration_allowed=_strict_bool(
                    policy_payload["auto_review_regeneration_allowed"]
                ),
                successful_page_auto_redraw_allowed=_strict_bool(
                    policy_payload["successful_page_auto_redraw_allowed"]
                ),
                safe_expansion_mode=SafeExpansionMode(
                    str(policy_payload["safe_expansion_mode"])
                ),
                policy_version=str(policy_payload["policy_version"]),
                active_profile_id=_optional_string(
                    policy_payload.get("active_profile_id")
                ),
                profiles=tuple(
                    _parse_profile(_mapping(item)) for item in profiles_payload
                ),
                iteration=(
                    _parse_iteration(_mapping(iteration_payload))
                    if iteration_payload is not None
                    else None
                ),
                safe_expansion=(
                    _parse_safe_expansion(_mapping(safe_expansion_payload))
                    if safe_expansion_payload is not None
                    else None
                ),
            ),
            provider_type=str(payload["provider_type"]),
            cost_limit_micros=_strict_int(payload["cost_limit_micros"]),
            real_provider_request_limit=_strict_int(
                payload["real_provider_request_limit"]
            ),
            files=tuple(
                SkillFileLock(
                    path=str(_mapping(item)["path"]),
                    sha256=str(_mapping(item)["sha256"]),
                )
                for item in files_payload
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SkillRuntimeError(
            "SKILL_MANIFEST_INVALID",
            f"Skill manifest {path.name} has an invalid schema.",
        ) from exc
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: SkillManifest) -> None:
    if manifest.skill_id not in KNOWN_SKILLS:
        raise SkillRuntimeError(
            "UNKNOWN_SKILL",
            f"Unknown skill manifest: {manifest.skill_id}.",
        )
    if (
        manifest.schema_version != MANIFEST_SCHEMA_VERSION
        or manifest.content_hash_algorithm != CONTENT_HASH_ALGORITHM
        or manifest.ignored_runtime_artifacts != IGNORED_RUNTIME_ARTIFACTS
        or not manifest.files
    ):
        raise SkillRuntimeError(
            "SKILL_MANIFEST_INVALID",
            f"Manifest {manifest.skill_id} is incomplete.",
        )
    if manifest.policy.schema_version != POLICY_SCHEMA_VERSION:
        raise SkillRuntimeError(
            "SKILL_POLICY_INVALID",
            f"Manifest {manifest.skill_id} has an unsupported policy schema.",
        )
    if not manifest.input_contract.required_fields:
        raise SkillRuntimeError(
            "SKILL_MANIFEST_INVALID",
            f"Manifest {manifest.skill_id} has no input contract.",
        )
    if not manifest.output_contract.required_fields:
        raise SkillRuntimeError(
            "SKILL_MANIFEST_INVALID",
            f"Manifest {manifest.skill_id} has no output contract.",
        )
    if manifest.cost_limit_micros < 0 or manifest.real_provider_request_limit < 0:
        raise SkillRuntimeError(
            "SKILL_COST_LIMIT_INVALID",
            f"Manifest {manifest.skill_id} has a negative limit.",
        )
    _validate_frozen_skill_policy(manifest)
    seen_paths: set[str] = set()
    for item in manifest.files:
        path = Path(item.path)
        if path.is_absolute() or ".." in path.parts or item.path in seen_paths:
            raise SkillRuntimeError(
                "SKILL_MANIFEST_INVALID",
                f"Manifest {manifest.skill_id} has an unsafe file path.",
            )
        seen_paths.add(item.path)
        _require_sha256(item.sha256, "SKILL_MANIFEST_INVALID")
    _validate_manifest_lock(manifest)


def _validate_frozen_skill_policy(manifest: SkillManifest) -> None:
    policy = manifest.policy
    if (
        policy.auto_review_regeneration_allowed
        or policy.successful_page_auto_redraw_allowed
    ):
        raise SkillRuntimeError(
            "SKILL_POLICY_INVALID",
            f"Manifest {manifest.skill_id} enables automatic redraw.",
        )
    if manifest.skill_id == "xinghe-ecommerce-detail":
        iteration = policy.iteration
        if (
            policy.active_profile_id != "international_generic_v1"
            or iteration is None
            or iteration.version != "iteration-disabled-v1"
            or iteration.mode != IterationMode.DISABLED
            or iteration.max_iteration_rounds != 0
            or iteration.trigger
            != IterationTrigger.EXPLICIT_USER_REQUEST_ONLY
            or policy.safe_expansion_mode != SafeExpansionMode.DISABLED
            or policy.safe_expansion is not None
        ):
            raise SkillRuntimeError(
                "SKILL_POLICY_INVALID",
                "Xinghe Foundation policy does not match the frozen contract.",
            )
        international = _resolve_profile(manifest, "international_generic_v1")
        if (
            international.market_scope != "international"
            or not international.enabled
            or international.default_language != "en-US"
            or international.default_page_count != 8
            or international.min_page_count != 1
            or international.max_page_count != 12
        ):
            raise SkillRuntimeError(
                "SKILL_PROFILE_INVALID",
                "The international profile does not match the frozen contract.",
            )
        for profile in policy.profiles:
            if profile.market_scope == "domestic" and profile.enabled:
                raise SkillRuntimeError(
                    "SKILL_PROFILE_NOT_ALLOWED",
                    "Domestic ecommerce profiles must remain disabled.",
                )
        return
    if manifest.skill_id != "masked-image-repair":
        if (
            policy.active_profile_id is not None
            or policy.profiles
            or policy.iteration is not None
            or policy.safe_expansion_mode != SafeExpansionMode.DISABLED
            or policy.safe_expansion is not None
        ):
            raise SkillRuntimeError(
                "SKILL_POLICY_INVALID",
                f"Manifest {manifest.skill_id} does not match the frozen policy.",
            )
        return
    if (
        policy.active_profile_id is not None
        or policy.profiles
        or policy.iteration is not None
        or policy.safe_expansion_mode != SafeExpansionMode.PREAUTHORIZED_ONLY
    ):
        raise SkillRuntimeError(
            "SKILL_POLICY_INVALID",
            "Masked repair policy does not match the frozen contract.",
        )
    expansion = policy.safe_expansion
    required_adaptation_bans = {
        "font_compression",
        "object_distortion",
        "arbitrary_repositioning",
        "curvature_change",
    }
    required_protected_boundaries = {
        "container_edge",
        "rounded_corner",
        "occlusion",
        "seam",
        "identity_feature",
        "adjacent_text",
        "logo",
        "label_outline",
        "protected_artwork",
    }
    if (
        expansion is None
        or expansion.halo_pixels != 1
        or expansion.max_distance_relative != 0.02
        or expansion.max_distance_absolute_pixels != 24
        or expansion.max_added_pixels_relative != 0.10
        or expansion.max_added_pixels_absolute != 25_000
        or expansion.max_relative_expansion != 0.10
        or expansion.layout_adaptation_allowed
        or expansion.outside_effective_selection_changed_pixels != 0
        or not expansion.actual_budget_must_not_exceed_request
        or not required_adaptation_bans.issubset(
            expansion.forbidden_layout_adaptations
        )
        or not required_protected_boundaries.issubset(
            expansion.protected_boundaries
        )
        or "needs_larger_selection" not in expansion.unsafe_outcomes
        or "unsafe_expansion" not in expansion.unsafe_outcomes
    ):
        raise SkillRuntimeError(
            "SKILL_POLICY_INVALID",
            "Masked expansion policy does not match the frozen contract.",
        )


def _validate_manifest_lock(manifest: SkillManifest) -> None:
    _require_sha256(manifest.content_hash, "SKILL_MANIFEST_INVALID")
    if _aggregate_content_hash(manifest.files) != manifest.content_hash:
        raise SkillRuntimeError(
            "SKILL_CONTENT_HASH_MISMATCH",
            f"Manifest lock drifted for {manifest.skill_id}.",
        )


def _aggregate_content_hash(files: tuple[SkillFileLock, ...]) -> str:
    canonical = "".join(
        f"{item.path}\0{item.sha256.lower()}\n"
        for item in sorted(files, key=lambda item: item.path)
    )
    return _sha256_text(canonical)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_sha256(value: str, code: str) -> None:
    if len(value) != SHA256_HEX_LENGTH:
        raise SkillRuntimeError(code, "Expected a SHA-256 hexadecimal digest.")
    try:
        int(value, 16)
    except ValueError as exc:
        raise SkillRuntimeError(
            code,
            "Expected a SHA-256 hexadecimal digest.",
        ) from exc


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("expected an object")
    return value


def _parse_profile(payload: dict[str, Any]) -> SkillProfile:
    return SkillProfile(
        profile_id=str(payload["profile_id"]),
        market_scope=str(payload["market_scope"]),
        enabled=_strict_bool(payload["enabled"]),
        default_language=str(payload["default_language"]),
        default_page_count=_strict_int(payload["default_page_count"]),
        min_page_count=_strict_int(payload["min_page_count"]),
        max_page_count=_strict_int(payload["max_page_count"]),
    )


def _parse_iteration(payload: dict[str, Any]) -> IterationPolicy:
    return IterationPolicy(
        version=str(payload["version"]),
        mode=IterationMode(str(payload["mode"])),
        max_iteration_rounds=_strict_int(payload["max_iteration_rounds"]),
        trigger=IterationTrigger(str(payload["trigger"])),
    )


def _parse_safe_expansion(payload: dict[str, Any]) -> SafeExpansionPolicy:
    return SafeExpansionPolicy(
        halo_pixels=_strict_int(payload["halo_pixels"]),
        max_distance_relative=_strict_float(payload["max_distance_relative"]),
        max_distance_absolute_pixels=_strict_int(
            payload["max_distance_absolute_pixels"]
        ),
        max_added_pixels_relative=_strict_float(
            payload["max_added_pixels_relative"]
        ),
        max_added_pixels_absolute=_strict_int(
            payload["max_added_pixels_absolute"]
        ),
        max_relative_expansion=_strict_float(payload["max_relative_expansion"]),
        layout_adaptation_allowed=_strict_bool(
            payload["layout_adaptation_allowed"]
        ),
        forbidden_layout_adaptations=_string_tuple(
            payload["forbidden_layout_adaptations"]
        ),
        protected_boundaries=_string_tuple(payload["protected_boundaries"]),
        outside_effective_selection_changed_pixels=_strict_int(
            payload["outside_effective_selection_changed_pixels"]
        ),
        unsafe_outcomes=_string_tuple(payload["unsafe_outcomes"]),
        actual_budget_must_not_exceed_request=_strict_bool(
            payload["actual_budget_must_not_exceed_request"]
        ),
    )


def _resolve_profile(
    manifest: SkillManifest,
    profile_id: str | None,
) -> SkillProfile:
    requested = profile_id or manifest.policy.active_profile_id
    for profile in manifest.policy.profiles:
        if profile.profile_id == requested:
            return profile
    raise SkillRuntimeError(
        "SKILL_PROFILE_NOT_ALLOWED",
        "The requested market profile is not available.",
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("expected a list of strings")
    return tuple(value)


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected an integer")
    return value


def _strict_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("expected a number")
    return float(value)


def _strict_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected a boolean")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("expected a string or null")
    return value


def _normalize_provider_type(value: str) -> str:
    normalized = value.strip().lower()
    return "mock" if normalized == "fake" else normalized


def _is_sensitive_detail_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in SENSITIVE_DETAIL_KEYS or any(
        fragment in normalized for fragment in SENSITIVE_DETAIL_KEY_FRAGMENTS
    )


def _xinghe_request_options(
    skill_id: str,
    configuration: dict[str, Any],
) -> tuple[str | None, int | None]:
    if skill_id != "xinghe-ecommerce-detail":
        return None, None
    job_configuration = configuration.get("job")
    if not isinstance(job_configuration, dict):
        return None, None
    settings = job_configuration.get("settings")
    if not isinstance(settings, dict):
        return None, None
    language_value = settings.get("language")
    language = language_value if isinstance(language_value, str) else None
    page_count_value = settings.get("page_count")
    page_count = (
        page_count_value
        if isinstance(page_count_value, int)
        and not isinstance(page_count_value, bool)
        else None
    )
    return language, page_count


def _rejected_result(error: SkillRuntimeError) -> ProviderResult:
    return ProviderResult(
        outcome=ProviderOutcome.FAILED,
        error_code=error.code,
        error_message=str(error),
        usage_reported=True,
        raw_usage={
            "provider_type": "mock",
            "fake": True,
            "called": False,
            "runtime_rejected": True,
            "reason": error.code,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "input_images": 0,
            "output_images": 0,
            "network_requests": 0,
            "cost_micros": 0,
        },
        estimated_cost_micros=0,
        actual_cost_micros=0,
    )
