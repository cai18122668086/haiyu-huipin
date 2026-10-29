import asyncio
import hashlib
import json
import socket
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    default_masked_skill_root,
    default_xinghe_skill_root,
)
from app.domain import AttemptStatus, JobItemStatus, JobStatus
from app.execution import UsageEventWriter
from app.models import (
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    UsageEventRecord,
)
from app.provider import (
    FakeProvider,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from app.skill_runtime import (
    CONTENT_HASH_ALGORITHM,
    MANIFEST_SCHEMA_VERSION,
    POLICY_SCHEMA_VERSION,
    IterationMode,
    IterationTrigger,
    PostgresSkillRuntimeAuditSink,
    RuntimePolicyRequest,
    SafeExpansionAuthorization,
    SafeExpansionMode,
    SafeExpansionObservation,
    SkillExecutionRequest,
    SkillManifestRegistry,
    SkillRuntime,
    SkillRuntimeAuditEvent,
    SkillRuntimeError,
    SkillRuntimeProvider,
)

WORKSPACE_ID = "ws_test"
XINGHE_HASH = "c438e479eb465742115549779acd3de7b6c83e45745b9b4184fef7da6f86e932"
MASKED_HASH = "2578e9266e3d2ac3e7216ef4827939cc13bf66c5b7d58ae2ff9e249e4e7b7c3b"


@dataclass
class RecordingAuditSink:
    events: list[SkillRuntimeAuditEvent] = field(default_factory=list)

    def record(self, event: SkillRuntimeAuditEvent) -> None:
        self.events.append(event)


class FailingAuditSink:
    def record(self, event: SkillRuntimeAuditEvent) -> None:
        del event
        raise RuntimeError("simulated audit sink failure")


class FailingUsageWriter(UsageEventWriter):
    def add(self, session: Session, event: UsageEventRecord) -> None:
        del session, event
        raise RuntimeError("simulated usage sink failure")


class RecordingRealProvider:
    name = "openai"
    model = "gpt-image-2"
    pricing_version = "real-provider-test"

    def __init__(self) -> None:
        self.call_count = 0

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        del request
        self.call_count += 1
        return ProviderResult(outcome=ProviderOutcome.SUCCEEDED)


class IncompleteUsageProvider:
    name = "mock"
    model = "none"
    pricing_version = "incomplete-usage-test"

    def __init__(self, result: ProviderResult) -> None:
        self.result = result
        self.call_count = 0

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        del request
        self.call_count += 1
        return self.result


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(files: list[dict[str, str]]) -> str:
    canonical = "".join(
        f"{item['path']}\0{item['sha256']}\n"
        for item in sorted(files, key=lambda item: item["path"])
    )
    return _sha256(canonical.encode())


def _write_minimal_manifest(
    manifest_dir: Path,
    source_dir: Path,
    *,
    include_policy: bool = True,
) -> None:
    payload = b"frozen skill content\n"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_bytes(payload)
    files = [{"path": "SKILL.md", "sha256": _sha256(payload)}]
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "content_hash_algorithm": CONTENT_HASH_ALGORITHM,
        "ignored_runtime_artifacts": [
            "**/__pycache__/**",
            "**/*.pyc",
        ],
        "skill_id": "masked-image-repair",
        "content_hash": _canonical_hash(files),
        "capability": "masked_image_repair",
        "input_contract": {
            "schema_id": "test.input.v1",
            "required_fields": ["source"],
        },
        "output_contract": {
            "schema_id": "test.output.v1",
            "required_fields": ["result"],
        },
        "provider_type": "mock",
        "cost_limit_micros": 0,
        "real_provider_request_limit": 0,
        "files": files,
    }
    if include_policy:
        manifest["policy"] = {
            "schema_version": POLICY_SCHEMA_VERSION,
            "auto_review_regeneration_allowed": False,
            "successful_page_auto_redraw_allowed": False,
            "safe_expansion_mode": "preauthorized_only",
            "policy_version": "masked-authority-v1",
            "active_profile_id": None,
            "profiles": [],
            "iteration": None,
            "safe_expansion": {
                "halo_pixels": 1,
                "max_distance_relative": 0.02,
                "max_distance_absolute_pixels": 24,
                "max_added_pixels_relative": 0.10,
                "max_added_pixels_absolute": 25000,
                "max_relative_expansion": 0.10,
                "layout_adaptation_allowed": False,
                "forbidden_layout_adaptations": [
                    "font_compression",
                    "object_distortion",
                    "arbitrary_repositioning",
                    "curvature_change",
                ],
                "protected_boundaries": [
                    "container_edge",
                    "rounded_corner",
                    "occlusion",
                    "seam",
                    "identity_feature",
                    "adjacent_text",
                    "logo",
                    "label_outline",
                    "protected_artwork",
                ],
                "outside_effective_selection_changed_pixels": 0,
                "unsafe_outcomes": [
                    "needs_larger_selection",
                    "unsafe_expansion",
                ],
                "actual_budget_must_not_exceed_request": True,
            },
        }
    manifest_dir.mkdir()
    (manifest_dir / "masked-image-repair.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _runtime_request(
    skill_id: str,
    content_hash: str,
    *,
    provider_type: str = "mock",
    cost_limit_micros: int = 0,
    policy: RuntimePolicyRequest | None = None,
    workspace_id: str = WORKSPACE_ID,
) -> SkillExecutionRequest:
    return SkillExecutionRequest(
        skill_id=skill_id,
        skill_content_hash=content_hash,
        workspace_id=workspace_id,
        job_id="job_runtime",
        job_item_id="item_runtime",
        attempt_id="attempt_runtime",
        operation="test",
        idempotency_key="runtime-idempotency-key",
        provider_type=provider_type,
        cost_limit_micros=cost_limit_micros,
        policy=policy or RuntimePolicyRequest(),
    )


def _provider_request(operation: str = "local_edit") -> ProviderRequest:
    return ProviderRequest(
        workspace_id=WORKSPACE_ID,
        job_id="job_runtime",
        job_item_id="item_runtime",
        attempt_id="attempt_runtime",
        operation=operation,
        idempotency_key="runtime-idempotency-key",
        configuration={},
    )


def _expansion_authorization(
    *,
    workspace_id: str = WORKSPACE_ID,
    policy_version: str = "masked-authority-v1",
    safe_region_width_pixels: int = 1200,
    safe_region_height_pixels: int = 800,
    user_selection_area_pixels: int = 100_000,
    requested_max_added_pixels: int = 10_000,
    requested_max_expansion_distance_pixels: int = 16,
    requested_max_relative_expansion: float = 0.10,
    required_pixels_outside_safe_region_count: int = 0,
    protected_structure_intersection_count: int = 0,
) -> SafeExpansionAuthorization:
    return SafeExpansionAuthorization(
        authorization_id="auth_runtime_1",
        workspace_id=workspace_id,
        policy_version=policy_version,
        user_selection_sha256="a" * 64,
        safe_region_sha256="b" * 64,
        safe_region_width_pixels=safe_region_width_pixels,
        safe_region_height_pixels=safe_region_height_pixels,
        user_selection_area_pixels=user_selection_area_pixels,
        requested_max_added_pixels=requested_max_added_pixels,
        requested_max_expansion_distance_pixels=(
            requested_max_expansion_distance_pixels
        ),
        requested_max_relative_expansion=requested_max_relative_expansion,
        required_pixels_outside_safe_region_count=(
            required_pixels_outside_safe_region_count
        ),
        protected_structure_intersection_count=(
            protected_structure_intersection_count
        ),
    )


def _image_payload(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _create_local_edit_job(client: TestClient) -> str:
    asset_ids: list[str] = []
    for index, media_kind in enumerate(("image", "mask")):
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": media_kind},
            files={
                "file": (
                    f"runtime-{index}.png",
                    _image_payload((40 + index, 80, 120)),
                    "image/png",
                )
            },
        )
        assert response.status_code == 201
        asset_ids.append(str(response.json()["id"]))
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "type": "local_edit",
            "intent": "submit",
            "input": {
                "source_asset_id": asset_ids[0],
                "selection_mask_asset_id": asset_ids[1],
            },
            "configuration": {
                "instruction": "runtime foundation test",
                "quality": "balanced",
                "output_format": "png",
            },
        },
    )
    assert response.status_code == 202
    return str(response.json()["id"])


def test_bundled_manifests_load_with_frozen_policies() -> None:
    registry = SkillManifestRegistry.bundled()
    assert registry.skill_ids == (
        "all-category-style-benchmark-still",
        "all-category-style-benchmark-still-fast",
        "aurora-domestic-ecommerce-detail-page",
        "aurora-domestic-ecommerce-main-image",
        "aurora-ecommerce-reference-remix",
        "aurora-ecommerce-reference-remix-fast",
        "aurora-remix-fast",
        "ecommerce-model-pose-transfer",
        "masked-image-repair",
        "reference-visual-reverse-remix",
        "xinghe-amazon-visual-suite",
        "xinghe-crossborder-detail-3",
        "xinghe-ecommerce-creative-image",
        "xinghe-ecommerce-detail",
        "xinghe-ecommerce-detail-domestic-3",
        "xinghe-ecommerce-detail-domestic-4",
        "xinghe-ecommerce-detail-domestic-4-fast",
        "xinghe-reference-image-remix-3",
        "xinghe-wanneng-shengtu-3-0",
    )
    fast = registry.get("aurora-remix-fast")
    assert fast.content_hash == (
        "67c50598adb2a0bc9f1137d29419266f1476541f9d82b79f411e771979eb86a1"
    )
    assert len(fast.files) == 10
    assert fast.policy.auto_review_regeneration_allowed is False
    assert fast.policy.successful_page_auto_redraw_allowed is False
    masked = registry.get("masked-image-repair")
    xinghe = registry.get("xinghe-ecommerce-detail")
    assert masked.content_hash == MASKED_HASH
    assert len(masked.files) == 9
    assert masked.policy.safe_expansion_mode == SafeExpansionMode.PREAUTHORIZED_ONLY
    assert masked.schema_version == MANIFEST_SCHEMA_VERSION
    assert masked.policy.schema_version == POLICY_SCHEMA_VERSION
    expansion = masked.policy.safe_expansion
    assert expansion is not None
    assert expansion.halo_pixels == 1
    assert expansion.max_distance_relative == 0.02
    assert expansion.max_distance_absolute_pixels == 24
    assert expansion.max_added_pixels_relative == 0.10
    assert expansion.max_added_pixels_absolute == 25_000
    assert expansion.max_relative_expansion == 0.10
    assert expansion.layout_adaptation_allowed is False
    assert expansion.outside_effective_selection_changed_pixels == 0
    assert xinghe.content_hash == XINGHE_HASH
    assert len(xinghe.files) == 21
    assert xinghe.policy.auto_review_regeneration_allowed is False
    assert xinghe.policy.successful_page_auto_redraw_allowed is False
    assert xinghe.policy.safe_expansion_mode == SafeExpansionMode.DISABLED
    assert xinghe.policy.active_profile_id == "international_generic_v1"
    international = next(
        profile
        for profile in xinghe.policy.profiles
        if profile.profile_id == "international_generic_v1"
    )
    domestic = next(
        profile
        for profile in xinghe.policy.profiles
        if profile.profile_id == "domestic_ecommerce_v1"
    )
    assert international.market_scope == "international"
    assert international.enabled is True
    assert international.default_language == "en-US"
    assert international.default_page_count == 8
    assert (international.min_page_count, international.max_page_count) == (1, 12)
    assert domestic.enabled is False
    iteration = xinghe.policy.iteration
    assert iteration is not None
    assert iteration.version == "iteration-disabled-v1"
    assert iteration.mode == IterationMode.DISABLED
    assert iteration.max_iteration_rounds == 0
    assert iteration.trigger == IterationTrigger.EXPLICIT_USER_REQUEST_ONLY
    for manifest in (masked, xinghe):
        assert manifest.content_hash_algorithm == CONTENT_HASH_ALGORITHM
        assert manifest.provider_type == "mock"
        assert manifest.cost_limit_micros == 0
        assert manifest.real_provider_request_limit == 0


def test_bundled_skill_sources_are_project_owned_and_verified() -> None:
    sources_root = Path(__file__).resolve().parents[1] / "skill_sources"
    roots = {
        "masked-image-repair": (default_masked_skill_root(), MASKED_HASH),
        "xinghe-ecommerce-detail": (
            default_xinghe_skill_root(),
            XINGHE_HASH,
        ),
    }
    registry = SkillManifestRegistry.bundled()
    for skill_id, (root, expected_hash) in roots.items():
        assert root == sources_root / skill_id
        assert registry.verify_source(skill_id, root) == expected_hash


@pytest.mark.parametrize(
    "drift",
    ["content", "added", "deleted", "renamed"],
)
def test_source_hash_inventory_drift_fails_closed(
    tmp_path: Path,
    drift: str,
) -> None:
    manifest_dir = tmp_path / "manifests"
    source_dir = tmp_path / "source"
    _write_minimal_manifest(manifest_dir, source_dir)
    registry = SkillManifestRegistry.bundled(manifest_dir)
    assert registry.verify_source("masked-image-repair", source_dir)

    skill_file = source_dir / "SKILL.md"
    if drift == "content":
        skill_file.write_text("drifted\n", encoding="utf-8")
    elif drift == "added":
        (source_dir / "added.md").write_text("added\n", encoding="utf-8")
    elif drift == "deleted":
        skill_file.unlink()
    else:
        skill_file.rename(source_dir / "RENAMED.md")
    with pytest.raises(
        SkillRuntimeError,
        match="drifted",
    ) as exc_info:
        registry.verify_source("masked-image-repair", source_dir)
    assert exc_info.value.code == "SKILL_CONTENT_HASH_MISMATCH"


def test_unknown_skill_manifest_missing_and_policy_missing_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(SkillRuntimeError) as unknown:
        SkillManifestRegistry({}).get("unknown-skill")
    assert unknown.value.code == "UNKNOWN_SKILL"

    with pytest.raises(SkillRuntimeError) as missing:
        SkillManifestRegistry({}).get("masked-image-repair")
    assert missing.value.code == "SKILL_MANIFEST_MISSING"

    manifest_dir = tmp_path / "manifests"
    _write_minimal_manifest(
        manifest_dir,
        tmp_path / "source",
        include_policy=False,
    )
    with pytest.raises(SkillRuntimeError) as policy:
        SkillManifestRegistry.bundled(manifest_dir)
    assert policy.value.code == "SKILL_POLICY_MISSING"


def test_runtime_recomputes_source_tree_before_each_provider_call(
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / "manifests"
    source_dir = tmp_path / "source"
    _write_minimal_manifest(manifest_dir, source_dir)
    registry = SkillManifestRegistry.bundled(manifest_dir)
    fake = FakeProvider()
    adapter = SkillRuntimeProvider(
        fake,
        SkillRuntime(
            registry,
            RecordingAuditSink(),
            source_roots={"masked-image-repair": source_dir},
        ),
    )
    assert (
        asyncio.run(adapter.execute(_provider_request())).outcome
        == ProviderOutcome.SUCCEEDED
    )
    assert fake.call_count == 1

    (source_dir / "SKILL.md").write_text("drifted\n", encoding="utf-8")
    blocked = asyncio.run(adapter.execute(_provider_request()))
    assert blocked.outcome == ProviderOutcome.FAILED
    assert blocked.error_code == "SKILL_CONTENT_HASH_MISMATCH"
    assert fake.call_count == 1


@pytest.mark.parametrize(
    ("registry", "operation", "expected_code"),
    [
        (SkillManifestRegistry({}), "local_edit", "SKILL_MANIFEST_MISSING"),
        (
            SkillManifestRegistry.bundled(),
            "unknown-operation",
            "UNKNOWN_SKILL",
        ),
    ],
)
def test_unknown_or_missing_manifest_blocks_provider_before_call(
    registry: SkillManifestRegistry,
    operation: str,
    expected_code: str,
) -> None:
    fake = FakeProvider()
    adapter = SkillRuntimeProvider(
        fake,
        SkillRuntime(registry, RecordingAuditSink()),
    )
    result = asyncio.run(adapter.execute(_provider_request(operation)))
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == expected_code
    assert fake.call_count == 0


@pytest.mark.parametrize(
    "policy",
    [
        RuntimePolicyRequest(auto_review_regeneration_requested=True),
        RuntimePolicyRequest(successful_page_auto_redraw_requested=True),
    ],
)
def test_xinghe_automatic_redraw_is_rejected(
    policy: RuntimePolicyRequest,
) -> None:
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), RecordingAuditSink())
    request = _runtime_request(
        "xinghe-ecommerce-detail",
        XINGHE_HASH,
        policy=policy,
    )
    with pytest.raises(SkillRuntimeError) as exc_info:
        runtime.authorize(request)
    assert exc_info.value.code == "SKILL_AUTO_REDRAW_NOT_AUTHORIZED"


def test_xinghe_profile_defaults_page_bounds_and_iteration_freeze() -> None:
    sink = RecordingAuditSink()
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), sink)
    default_request = _runtime_request(
        "xinghe-ecommerce-detail",
        XINGHE_HASH,
    )
    runtime.authorize(default_request)
    assert sink.events[-1].details["profile_id"] == "international_generic_v1"
    assert sink.events[-1].details["market_scope"] == "international"
    assert sink.events[-1].details["language"] == "en-US"
    assert sink.events[-1].details["page_count"] == 8

    runtime.authorize(replace(default_request, page_count=1))
    runtime.authorize(replace(default_request, page_count=12))
    for invalid_page_count in (0, 13):
        with pytest.raises(SkillRuntimeError) as invalid_page:
            runtime.authorize(
                replace(default_request, page_count=invalid_page_count)
            )
        assert invalid_page.value.code == "SKILL_PAGE_COUNT_INVALID"

    with pytest.raises(SkillRuntimeError) as domestic:
        runtime.authorize(
            replace(default_request, profile_id="domestic_ecommerce_v1")
        )
    assert domestic.value.code == "SKILL_PROFILE_NOT_ALLOWED"

    for policy in (
        RuntimePolicyRequest(iteration_requested=True),
        RuntimePolicyRequest(requested_iteration_rounds=1),
    ):
        with pytest.raises(SkillRuntimeError) as iteration:
            runtime.authorize(replace(default_request, policy=policy))
        assert iteration.value.code == "SKILL_ITERATION_NOT_ALLOWED"


def test_xinghe_adapter_uses_existing_typed_page_count_and_language() -> None:
    sink = RecordingAuditSink()
    adapter = SkillRuntimeProvider(
        FakeProvider(),
        SkillRuntime(SkillManifestRegistry.bundled(), sink),
    )
    request = replace(
        _provider_request("detail_pack"),
        configuration={
            "job": {
                "settings": {
                    "language": "fr-FR",
                    "page_count": 12,
                }
            },
            "item": {},
        },
    )
    result = asyncio.run(adapter.execute(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert sink.events[0].details["profile_id"] == "international_generic_v1"
    assert sink.events[0].details["language"] == "fr-FR"
    assert sink.events[0].details["page_count"] == 12


def test_masked_expansion_requires_bounded_same_workspace_authorization() -> None:
    sink = RecordingAuditSink()
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), sink)
    unauthorized = _runtime_request(
        "masked-image-repair",
        MASKED_HASH,
        policy=RuntimePolicyRequest(safe_expansion_requested=True),
    )
    with pytest.raises(SkillRuntimeError) as missing:
        runtime.authorize(unauthorized)
    assert missing.value.code == "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED"

    authorization = _expansion_authorization()
    authorized = replace(
        unauthorized,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=authorization,
        ),
    )
    manifest = runtime.authorize(authorized)
    assert manifest.skill_id == "masked-image-repair"
    assert sink.events[-1].details["expansion_authorization_id"] == "auth_runtime_1"

    cross_workspace = replace(
        authorized,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=replace(
                authorization,
                workspace_id="ws_other",
            ),
        ),
    )
    with pytest.raises(SkillRuntimeError) as mismatch:
        runtime.authorize(cross_workspace)
    assert mismatch.value.code == "SKILL_EXPANSION_WORKSPACE_MISMATCH"


def test_masked_expansion_version_hash_layout_and_protected_boundaries_reject() -> None:
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), RecordingAuditSink())
    base = _runtime_request(
        "masked-image-repair",
        MASKED_HASH,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=_expansion_authorization(),
        ),
    )
    cases = (
        (
            replace(
                base,
                policy=replace(
                    base.policy,
                    safe_expansion_authorization=replace(
                        _expansion_authorization(),
                        policy_version="wrong-policy",
                    ),
                ),
            ),
            "SKILL_POLICY_VERSION_MISMATCH",
        ),
        (
            replace(
                base,
                policy=replace(
                    base.policy,
                    safe_expansion_authorization=replace(
                        _expansion_authorization(),
                        user_selection_sha256="not-a-hash",
                    ),
                ),
            ),
            "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
        ),
        (
            replace(
                base,
                policy=replace(
                    base.policy,
                    safe_expansion_authorization=replace(
                        _expansion_authorization(),
                        required_pixels_outside_safe_region_count=1,
                    ),
                ),
            ),
            "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
        ),
        (
            replace(
                base,
                policy=replace(
                    base.policy,
                    safe_expansion_authorization=replace(
                        _expansion_authorization(),
                        protected_structure_intersection_count=1,
                    ),
                ),
            ),
            "SKILL_SAFE_EXPANSION_NOT_AUTHORIZED",
        ),
        (
            replace(
                base,
                policy=replace(
                    base.policy,
                    layout_adaptation_requested=True,
                ),
            ),
            "SKILL_LAYOUT_ADAPTATION_NOT_ALLOWED",
        ),
    )
    for request, expected_code in cases:
        with pytest.raises(SkillRuntimeError) as exc_info:
            runtime.authorize(request)
        assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    "authorization",
    [
        _expansion_authorization(
            requested_max_expansion_distance_pixels=17,
        ),
        _expansion_authorization(
            requested_max_added_pixels=10_001,
        ),
        _expansion_authorization(
            requested_max_relative_expansion=0.1001,
        ),
        _expansion_authorization(
            safe_region_width_pixels=2_000,
            safe_region_height_pixels=2_000,
            user_selection_area_pixels=300_000,
            requested_max_expansion_distance_pixels=25,
            requested_max_added_pixels=25_000,
        ),
        _expansion_authorization(
            safe_region_width_pixels=2_000,
            safe_region_height_pixels=2_000,
            user_selection_area_pixels=300_000,
            requested_max_expansion_distance_pixels=24,
            requested_max_added_pixels=25_001,
        ),
    ],
)
def test_masked_expansion_over_scale_or_absolute_budget_rejects(
    authorization: SafeExpansionAuthorization,
) -> None:
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), RecordingAuditSink())
    request = _runtime_request(
        "masked-image-repair",
        MASKED_HASH,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=authorization,
        ),
    )
    with pytest.raises(SkillRuntimeError) as exc_info:
        runtime.authorize(request)
    assert exc_info.value.code == "SKILL_SAFE_EXPANSION_BUDGET_EXCEEDED"


@pytest.mark.parametrize(
    "authorization",
    [
        _expansion_authorization(),
        _expansion_authorization(
            safe_region_width_pixels=2_000,
            safe_region_height_pixels=2_000,
            user_selection_area_pixels=300_000,
            requested_max_expansion_distance_pixels=24,
            requested_max_added_pixels=25_000,
        ),
        _expansion_authorization(
            safe_region_width_pixels=20,
            safe_region_height_pixels=20,
            user_selection_area_pixels=5,
            requested_max_expansion_distance_pixels=1,
            requested_max_added_pixels=1,
        ),
    ],
)
def test_masked_expansion_legal_scale_and_cap_boundaries_pass(
    authorization: SafeExpansionAuthorization,
) -> None:
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), RecordingAuditSink())
    request = _runtime_request(
        "masked-image-repair",
        MASKED_HASH,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=authorization,
        ),
    )
    assert runtime.authorize(request).skill_id == "masked-image-repair"


def test_masked_observed_expansion_must_stay_inside_request_and_authority() -> None:
    sink = RecordingAuditSink()
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), sink)
    request = _runtime_request(
        "masked-image-repair",
        MASKED_HASH,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=_expansion_authorization(),
        ),
    )
    valid = SafeExpansionObservation(
        added_authority_pixels=10_000,
        max_expansion_distance_pixels=16,
        relative_expansion=0.10,
        content_alpha_outside_effective_selection=0,
        outside_effective_selection_changed_pixels=0,
        protected_structure_intersection_count=0,
    )
    runtime.validate_safe_expansion_observation(request, valid)
    assert sink.events[-1].action == "skill_runtime.safe_expansion_verified"

    for invalid, expected_code in (
        (
            replace(valid, added_authority_pixels=10_001),
            "SKILL_SAFE_EXPANSION_BUDGET_EXCEEDED",
        ),
        (
            replace(valid, max_expansion_distance_pixels=17),
            "SKILL_SAFE_EXPANSION_BUDGET_EXCEEDED",
        ),
        (
            replace(valid, relative_expansion=0.1001),
            "SKILL_SAFE_EXPANSION_BUDGET_EXCEEDED",
        ),
        (
            replace(valid, content_alpha_outside_effective_selection=1),
            "SKILL_SAFE_EXPANSION_UNSAFE",
        ),
        (
            replace(valid, outside_effective_selection_changed_pixels=1),
            "SKILL_SAFE_EXPANSION_UNSAFE",
        ),
    ):
        with pytest.raises(SkillRuntimeError) as exc_info:
            runtime.validate_safe_expansion_observation(request, invalid)
        assert exc_info.value.code == expected_code


def test_masked_observation_audit_failure_fails_closed() -> None:
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), FailingAuditSink())
    request = _runtime_request(
        "masked-image-repair",
        MASKED_HASH,
        policy=RuntimePolicyRequest(
            safe_expansion_requested=True,
            safe_expansion_authorization=_expansion_authorization(),
        ),
    )
    observation = SafeExpansionObservation(
        added_authority_pixels=1,
        max_expansion_distance_pixels=1,
        relative_expansion=0.01,
        content_alpha_outside_effective_selection=0,
        outside_effective_selection_changed_pixels=0,
        protected_structure_intersection_count=0,
    )
    with pytest.raises(SkillRuntimeError) as exc_info:
        runtime.validate_safe_expansion_observation(request, observation)
    assert exc_info.value.code == "SKILL_AUDIT_WRITE_FAILED"


@pytest.mark.parametrize(
    ("runtime_request", "expected_code"),
    [
        (
            _runtime_request(
                "masked-image-repair",
                "0" * 64,
            ),
            "SKILL_CONTENT_HASH_MISMATCH",
        ),
        (
            _runtime_request(
                "masked-image-repair",
                MASKED_HASH,
                provider_type="openai",
            ),
            "SKILL_PROVIDER_NOT_ALLOWED",
        ),
        (
            _runtime_request(
                "masked-image-repair",
                MASKED_HASH,
                cost_limit_micros=1,
            ),
            "SKILL_COST_LIMIT_INVALID",
        ),
    ],
)
def test_hash_provider_and_cost_guards_fail_closed(
    runtime_request: SkillExecutionRequest,
    expected_code: str,
) -> None:
    runtime = SkillRuntime(SkillManifestRegistry.bundled(), RecordingAuditSink())
    with pytest.raises(SkillRuntimeError) as exc_info:
        runtime.authorize(runtime_request)
    assert exc_info.value.code == expected_code


def test_fake_provider_succeeds_without_network_and_real_provider_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = 0
    original_connect = socket.socket.connect

    def reject_external_network(
        sock: socket.socket,
        address: tuple[str, int],
    ) -> None:
        nonlocal network_calls
        if address[0] in {"127.0.0.1", "::1"}:
            original_connect(sock, address)
            return
        network_calls += 1
        raise AssertionError("external network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", reject_external_network)
    registry = SkillManifestRegistry.bundled()
    fake = FakeProvider()
    sink = RecordingAuditSink()
    adapter = SkillRuntimeProvider(fake, SkillRuntime(registry, sink))
    result = asyncio.run(adapter.execute(_provider_request()))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert fake.call_count == 1
    assert fake.external_network_request_count == 0
    assert result.usage_reported is True
    assert result.estimated_cost_micros == 0
    assert result.actual_cost_micros == 0
    assert result.raw_usage == {
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
    assert [event.action for event in sink.events] == [
        "skill_runtime.execution_authorized",
        "skill_runtime.execution_completed",
    ]

    real = RecordingRealProvider()
    blocked = SkillRuntimeProvider(
        real,
        SkillRuntime(registry, RecordingAuditSink()),
    )
    result = asyncio.run(blocked.execute(_provider_request()))
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "SKILL_PROVIDER_NOT_ALLOWED"
    assert real.call_count == 0
    assert network_calls == 0


def test_authorization_audit_failure_blocks_provider_call() -> None:
    fake = FakeProvider()
    adapter = SkillRuntimeProvider(
        fake,
        SkillRuntime(SkillManifestRegistry.bundled(), FailingAuditSink()),
    )
    result = asyncio.run(adapter.execute(_provider_request()))
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "SKILL_AUDIT_WRITE_FAILED"
    assert fake.call_count == 0
    assert fake.external_network_request_count == 0


@pytest.mark.parametrize(
    ("provider_result", "expected_code"),
    [
        (
            ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                usage_reported=False,
                estimated_cost_micros=0,
                actual_cost_micros=0,
            ),
            "SKILL_USAGE_CONTRACT_MISSING",
        ),
        (
            ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                usage_reported=True,
                raw_usage={
                    "provider_type": "mock",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "input_images": 0,
                    "output_images": 0,
                    "network_requests": 0,
                    "cost_micros": 0,
                },
                estimated_cost_micros=None,
                actual_cost_micros=0,
            ),
            "SKILL_COST_CONTRACT_MISSING",
        ),
    ],
)
def test_incomplete_fake_usage_or_cost_contract_fails_closed(
    provider_result: ProviderResult,
    expected_code: str,
) -> None:
    provider = IncompleteUsageProvider(provider_result)
    adapter = SkillRuntimeProvider(
        provider,
        SkillRuntime(SkillManifestRegistry.bundled(), RecordingAuditSink()),
    )
    result = asyncio.run(adapter.execute(_provider_request()))
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == expected_code
    assert provider.call_count == 1


def test_mock_task_runner_records_redacted_runtime_audit_and_usage(
    client: TestClient,
) -> None:
    job_id = _create_local_edit_job(client)
    assert asyncio.run(client.app.state.task_runner.run(job_id)) == JobStatus.COMPLETED

    with client.app.state.session_factory() as session:
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job_id)
        )
        audit_events = session.scalars(
            select(AuditEventRecord)
            .where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action.like("skill_runtime.%"),
            )
            .order_by(AuditEventRecord.id)
        ).all()
    assert usage is not None
    assert usage.usage_reported is True
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cached_tokens == 0
    assert usage.input_image_count == 0
    assert usage.output_image_count == 0
    assert usage.actual_cost_micros == 0
    assert usage.estimated_cost_micros == 0
    assert usage.raw_usage["network_requests"] == 0
    assert usage.raw_usage["cost_micros"] == 0
    assert [event.action for event in audit_events] == [
        "skill_runtime.execution_authorized",
        "skill_runtime.execution_completed",
    ]
    for event in audit_events:
        assert event.workspace_id == WORKSPACE_ID
        serialized = json.dumps(event.details, sort_keys=True)
        assert "runtime foundation test" not in serialized
        assert "secret" not in serialized.lower()
        assert "object_key" not in serialized


def test_usage_write_failure_marks_attempt_item_and_job_failed(
    client: TestClient,
) -> None:
    job_id = _create_local_edit_job(client)
    coordinator = client.app.state.task_runner.coordinator
    coordinator.usage_writer = FailingUsageWriter()

    assert asyncio.run(coordinator.run(job_id)) == JobStatus.FAILED
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        attempt = session.scalar(
            select(JobAttemptRecord).where(JobAttemptRecord.job_id == job_id)
        )
        usage_count = session.scalar(
            select(func.count())
            .select_from(UsageEventRecord)
            .where(UsageEventRecord.job_id == job_id)
        )
        failure_audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job_id,
                AuditEventRecord.action == "skill_runtime.usage_record_failed",
            )
        )
    assert job is not None and job.status == JobStatus.FAILED.value
    assert item is not None and item.status == JobItemStatus.FAILED.value
    assert item.error_code == "USAGE_RECORD_FAILED"
    assert attempt is not None and attempt.status == AttemptStatus.FAILED.value
    assert attempt.error_code == "USAGE_RECORD_FAILED"
    assert usage_count == 0
    assert failure_audit is not None
    assert failure_audit.details["usage_error_type"] == "RuntimeError"


@pytest.mark.parametrize(
    "sensitive_key",
    ["secret", "prompt", "object_key", "source_storage_key", "provider_api_key"],
)
def test_postgres_audit_sink_rejects_sensitive_detail_keys(
    client: TestClient,
    sensitive_key: str,
) -> None:
    sink = PostgresSkillRuntimeAuditSink(client.app.state.session_factory)
    with pytest.raises(SkillRuntimeError) as exc_info:
        sink.record(
            SkillRuntimeAuditEvent(
                action="skill_runtime.test",
                workspace_id=WORKSPACE_ID,
                job_id="job_test",
                attempt_id="attempt_test",
                details={sensitive_key: "must-not-be-written"},
            )
        )
    assert exc_info.value.code == "SKILL_AUDIT_UNSAFE"
