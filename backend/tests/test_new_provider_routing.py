from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.amazon_visual_suite_provider import (
    AMAZON_VISUAL_SUITE_SKILL_ID,
    AmazonVisualSuiteProvider,
)
from app.aurora_main_image_provider import AuroraMainImageProvider
from app.config import default_skill_root, default_xinghe_skill_root
from app.creative_image_provider import CreativeImageProvider
from app.crossborder_detail_provider import (
    CROSSBORDER_DETAIL_SKILL_ID,
    CrossborderDetailProvider,
)
from app.domain import JobStatus, JobType
from app.domestic_detail.campaign_provider import DomesticCampaignReviewProvider
from app.domestic_detail_provider import (
    DOMESTIC_DETAIL_SKILL_ID,
    DomesticDetailProvider,
)
from app.execution import build_fake_coordinator
from app.model_pose_transfer_provider import ModelPoseTransferProvider
from app.product_image_provider import ProductImageProvider
from app.provider import (
    FakeProvider,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from app.provider_registry import (
    DetailMarketRoutingPlanner,
    RegisteredOperationRoutingProvider,
    UnavailableProvider,
)
from app.reference_remix_provider import (
    ReferenceRemixProvider,
    ReferenceRemixRouterProvider,
)
from app.skill_runtime import SkillRuntimeProvider
from app.task_runner import build_tengli_task_runner
from app.xinghe_agent import (
    _AURORA_PLANNER_PROTOCOL_FILES,
    FakeXingheGateway,
    XingheImageProvider,
    XinghePlanningCoordinator,
    load_planner_system_prompt,
)
from app.xinghe_single_image_provider import XingheSingleImageProvider
from tests.test_api import WORKSPACE_ID, idem, upload_asset


@dataclass
class RecordingProvider:
    name: str
    model: str = "routing-test"
    pricing_version: str = "routing-test-v1"
    requests: list[ProviderRequest] = field(default_factory=list)

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        return ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id=f"{self.name}-request",
            usage_reported=True,
            raw_usage={"provider_type": self.name, "network_requests": 0},
        )


class TrackingUnavailableProvider(UnavailableProvider):
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        return await super().execute(request)


@dataclass
class RecordingPlanner:
    status: JobStatus = JobStatus.AWAITING_CONFIRMATION
    calls: list[str] = field(default_factory=list)

    async def plan(self, job_id: str) -> JobStatus | None:
        self.calls.append(job_id)
        return self.status


def _request(operation: str, configuration: dict[str, Any]) -> ProviderRequest:
    return ProviderRequest(
        workspace_id=WORKSPACE_ID,
        job_id="job-routing",
        job_item_id="item-routing",
        attempt_id="attempt-routing",
        operation=operation,
        idempotency_key="routing-idempotency",
        configuration=configuration,
    )


def _create_detail_job(
    client: TestClient,
    asset_id: str,
    market_region: str,
    *,
    platform_code: str | None = None,
) -> str:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "detail_pack",
            "intent": "submit",
            "input": {
                "product_groups": [
                    {"group_key": market_region, "asset_ids": [asset_id]}
                ]
            },
            "configuration": {
                "product_name": f"{market_region} routing",
                "platform_code": platform_code
                or ("tmall" if market_region == "domestic" else "amazon"),
                "market_region": market_region,
                "language": "zh-CN" if market_region == "domestic" else "en",
                "page_count": 8 if market_region == "domestic" else 1,
            },
        },
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def test_mock_new_image_routes_keep_the_structural_fake_provider(
    client: TestClient,
) -> None:
    router = cast(
        RegisteredOperationRoutingProvider,
        client.app.state.task_runner.coordinator.provider,
    )
    default = router.default_provider

    assert isinstance(default, SkillRuntimeProvider)
    assert isinstance(default.provider, FakeProvider)
    assert default.provider.external_network_request_count == 0
    for operation in (
        JobType.PRODUCT_IMAGE.value,
        JobType.MODEL_POSE_TRANSFER.value,
        JobType.CREATIVE_IMAGE.value,
    ):
        assert router.routes[operation] is default
    replica = router.routes[JobType.REFERENCE_REMIX.value]
    assert isinstance(replica, ReferenceRemixRouterProvider)
    assert replica.legacy is default and replica.exact is default
    assert isinstance(replica.native, UnavailableProvider)
    for skill in (
        "aurora-ecommerce-reference-remix",
        "all-category-style-benchmark-still",
    ):
        result = asyncio.run(
            replica.execute(
                _request(
                    JobType.REFERENCE_REMIX.value,
                    {
                        "job": {
                            "input": {
                                "product_asset_ids": ["product"],
                                "reference_asset_ids": ["reference"],
                            },
                            "settings": {"reference_skill": skill},
                        },
                        "item": {"item_key": "image-001"},
                    },
                )
            )
        )
        assert result.outcome == ProviderOutcome.FAILED
        assert result.error_code == "PROVIDER_NOT_CONFIGURED"
        assert result.artifact is None and result.raw_usage["network_requests"] == 0
    assert default.provider.external_network_request_count == 0
    amazon = router.routes[JobType.AMAZON_VISUAL_SUITE.value]
    assert isinstance(amazon, AmazonVisualSuiteProvider)
    assert amazon.delegate is default


@pytest.mark.parametrize(
    ("operation", "adapter_type", "configuration"),
    [
        (
            JobType.PRODUCT_IMAGE.value,
            ProductImageProvider,
            {
                "job": {
                    "input": {"product_asset_ids": ["asset-product"]},
                    "settings": {"task_type": "scene_main", "image_count": 1},
                },
                "item": {"item_key": "image-001"},
            },
        ),
        (
            JobType.REFERENCE_REMIX.value,
            ReferenceRemixProvider,
            {
                "job": {
                    "input": {
                        "product_asset_ids": ["asset-product"],
                        "reference_asset_ids": ["asset-style"],
                    },
                    "settings": {"image_count": 1},
                },
                "item": {"item_key": "image-001"},
            },
        ),
        (
            JobType.MODEL_POSE_TRANSFER.value,
            ModelPoseTransferProvider,
            {
                "job": {
                    "input": {
                        "model_source_asset_id": "asset-model",
                        "pose_reference_asset_id": "asset-pose",
                        "pose_slug": None,
                        "pose_reference_sha256": None,
                    },
                    "settings": {
                        "aspect_ratio": "follow-source",
                        "image_count": 1,
                    },
                },
                "item": {"item_key": "image-001"},
            },
        ),
        (
            JobType.CREATIVE_IMAGE.value,
            CreativeImageProvider,
            {
                "job": {
                    "input": {"product_asset_ids": ["asset-product"]},
                    "settings": {"image_count": 1},
                },
                "item": {"item_key": "image-001"},
            },
        ),
    ],
)
def test_real_unregistered_new_image_routes_fail_closed_without_fake_fallback(
    operation: str,
    adapter_type: type[
        ProductImageProvider
        | ReferenceRemixProvider
        | ModelPoseTransferProvider
        | CreativeImageProvider
    ],
    configuration: dict[str, Any],
) -> None:
    fallback = FakeProvider()
    unavailable = TrackingUnavailableProvider()
    route = adapter_type(
        unavailable,
        manifest_validator=lambda _skill_id: "a" * 64,
    )
    detail = RecordingProvider("stable-detail")
    local = RecordingProvider("stable-local")
    domestic = RecordingProvider("domestic-detail")
    router = RegisteredOperationRoutingProvider(
        fallback,
        detail,
        local,
        {operation: route},
        domestic_detail_provider=domestic,
    )

    result = asyncio.run(router.execute(_request(operation, configuration)))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "PROVIDER_NOT_CONFIGURED"
    assert result.raw_usage["called"] is False
    assert result.raw_usage["network_requests"] == 0
    assert fallback.call_count == 0
    assert len(unavailable.requests) == 1
    assert detail.requests == []
    assert local.requests == []
    assert domestic.requests == []


def test_detail_provider_routes_domestic_only_and_preserves_international_chain() -> (
    None
):
    fallback = RecordingProvider("default")
    international = RecordingProvider("stable-international")
    local = RecordingProvider("stable-local")
    domestic = RecordingProvider("domestic")
    router = RegisteredOperationRoutingProvider(
        fallback,
        international,
        local,
        {},
        domestic_detail_provider=domestic,
    )

    domestic_result = asyncio.run(
        router.execute(
            _request(
                JobType.DETAIL_PACK.value,
                {"job": {"settings": {"market_region": "domestic"}}},
            )
        )
    )
    international_result = asyncio.run(
        router.execute(
            _request(
                JobType.DETAIL_PACK.value,
                {"job": {"settings": {"market_region": "international"}}},
            )
        )
    )

    assert domestic_result.provider_request_id == "domestic-request"
    assert international_result.provider_request_id == "stable-international-request"
    assert len(domestic.requests) == 1
    assert len(international.requests) == 1
    assert fallback.requests == []
    assert local.requests == []
    assert router.detail_provider is international
    assert not isinstance(router.detail_provider, CrossborderDetailProvider)


def test_detail_planner_routes_by_durable_market_region(client: TestClient) -> None:
    source = upload_asset(client, filename="planner-routing.png")
    domestic_job_id = _create_detail_job(client, source["id"], "domestic")
    international_job_id = _create_detail_job(client, source["id"], "international")
    domestic = RecordingPlanner()
    international = RecordingPlanner()
    router = DetailMarketRoutingPlanner(
        client.app.state.session_factory,
        international,
        domestic,
    )

    assert asyncio.run(router.plan(domestic_job_id)) == JobStatus.AWAITING_CONFIRMATION
    assert (
        asyncio.run(router.plan(international_job_id))
        == JobStatus.AWAITING_CONFIRMATION
    )

    assert domestic.calls == [domestic_job_id]
    assert international.calls == [international_job_id]


def test_built_routing_keeps_international_legacy_and_domestic_skill_identity(
    client: TestClient,
) -> None:
    coordinator = client.app.state.task_runner.coordinator
    planner_router = cast(DetailMarketRoutingPlanner, coordinator.detail_planner)
    provider_router = cast(RegisteredOperationRoutingProvider, coordinator.provider)
    international_planner = cast(
        XinghePlanningCoordinator, planner_router.international
    )
    domestic_planner = cast(XinghePlanningCoordinator, planner_router.domestic)
    domestic_adapter = provider_router.domestic_detail_provider

    assert international_planner.skill_id == "xinghe-ecommerce-detail"
    assert international_planner.skill_root == default_xinghe_skill_root()
    assert domestic_planner.skill_id == DOMESTIC_DETAIL_SKILL_ID
    assert domestic_planner.skill_root == default_skill_root(DOMESTIC_DETAIL_SKILL_ID)

    assert isinstance(provider_router.detail_provider, XingheImageProvider)
    assert provider_router.detail_provider.skill_id == "xinghe-ecommerce-detail"
    assert provider_router.detail_provider.skill_root == default_xinghe_skill_root()
    assert JobType.DETAIL_PACK.value not in provider_router.routes
    assert not isinstance(provider_router.detail_provider, CrossborderDetailProvider)

    assert isinstance(domestic_adapter, DomesticDetailProvider)
    assert domestic_adapter.route.skill_id == DOMESTIC_DETAIL_SKILL_ID
    domestic_delegate = cast(XingheImageProvider, domestic_adapter.delegate)
    assert domestic_delegate.skill_id == DOMESTIC_DETAIL_SKILL_ID
    assert domestic_delegate.skill_root == default_skill_root(DOMESTIC_DETAIL_SKILL_ID)
    assert Path(domestic_delegate.skill_root).is_dir()


def _build_real_coordinator(
    client: TestClient,
    **provider_flags: bool,
):
    return build_fake_coordinator(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=FakeXingheGateway(),
        xinghe_concurrency=2,
        **provider_flags,
    )


@pytest.mark.parametrize(
    ("operation", "flag_name", "adapter_type", "skill_id"),
    [
        (
            JobType.PRODUCT_IMAGE.value,
            "product_image_provider_enabled",
            ProductImageProvider,
            ProductImageProvider.skill_id,
        ),
        (
            JobType.REFERENCE_REMIX.value,
            "reference_remix_provider_enabled",
            ReferenceRemixProvider,
            ReferenceRemixProvider.skill_id,
        ),
        (
            JobType.MODEL_POSE_TRANSFER.value,
            "model_pose_transfer_provider_enabled",
            ModelPoseTransferProvider,
            ModelPoseTransferProvider.skill_id,
        ),
        (
            JobType.CREATIVE_IMAGE.value,
            "creative_image_provider_enabled",
            CreativeImageProvider,
            CreativeImageProvider.skill_id,
        ),
        (
            JobType.AMAZON_VISUAL_SUITE.value,
            "amazon_visual_suite_provider_enabled",
            AmazonVisualSuiteProvider,
            AMAZON_VISUAL_SUITE_SKILL_ID,
        ),
    ],
)
def test_real_single_image_route_uses_its_own_skill_delegate_when_enabled(
    client: TestClient,
    operation: str,
    flag_name: str,
    adapter_type: type[object],
    skill_id: str,
) -> None:
    coordinator = _build_real_coordinator(
        client,
        **{flag_name: True},
    )
    router = cast(RegisteredOperationRoutingProvider, coordinator.provider)
    adapter = router.routes[operation]

    assert isinstance(adapter, adapter_type)
    delegate = adapter.delegate
    assert isinstance(delegate, XingheSingleImageProvider)
    assert delegate.operation == operation
    assert delegate.skill_id == skill_id
    assert delegate.storage is client.app.state.storage
    validator = getattr(delegate.manifest_validator, "__self__", None)
    assert validator is not None
    assert validator.source_roots == {skill_id: default_skill_root(skill_id)}

    for other_operation, other_adapter in router.routes.items():
        if other_operation == JobType.DOMESTIC_MAIN_IMAGE.value:
            assert isinstance(other_adapter, UnavailableProvider)
            continue
        if other_operation == JobType.DOMESTIC_DETAIL_REVIEW.value:
            assert isinstance(other_adapter, DomesticCampaignReviewProvider)
            continue
        if other_operation != operation:
            assert isinstance(other_adapter.delegate, UnavailableProvider)


def test_real_single_image_routes_receive_independent_delegates(
    client: TestClient,
) -> None:
    coordinator = _build_real_coordinator(
        client,
        product_image_provider_enabled=True,
        reference_remix_provider_enabled=True,
        model_pose_transfer_provider_enabled=True,
        creative_image_provider_enabled=True,
        amazon_visual_suite_provider_enabled=True,
    )
    router = cast(RegisteredOperationRoutingProvider, coordinator.provider)
    delegates = [
        adapter.delegate
        for operation, adapter in router.routes.items()
        if operation
        not in {
            JobType.DOMESTIC_MAIN_IMAGE.value,
            JobType.DOMESTIC_DETAIL_REVIEW.value,
        }
    ]
    assert isinstance(
        router.routes[JobType.DOMESTIC_MAIN_IMAGE.value], UnavailableProvider
    )

    assert all(
        isinstance(delegate, XingheSingleImageProvider) for delegate in delegates
    )
    assert len({id(delegate) for delegate in delegates}) == 5
    assert {delegate.skill_id for delegate in delegates} == {
        ProductImageProvider.skill_id,
        ReferenceRemixProvider.skill_id,
        ModelPoseTransferProvider.skill_id,
        CreativeImageProvider.skill_id,
        AMAZON_VISUAL_SUITE_SKILL_ID,
    }


def test_crossborder_planner_loads_its_pinned_protocol() -> None:
    prompt = load_planner_system_prompt(
        default_skill_root(CROSSBORDER_DETAIL_SKILL_ID),
        CROSSBORDER_DETAIL_SKILL_ID,
    )

    assert "--- BEGIN SKILL.md ---" in prompt
    assert "--- BEGIN references/platform-profiles.md ---" in prompt
    assert "--- BEGIN references/page-planning-strategy.md ---" in prompt
    assert "--- BEGIN references/prompt-contract.md ---" in prompt
    assert "references/category-router.md" not in prompt


def test_domestic_planner_loads_every_pinned_reference_in_stable_order() -> None:
    skill_root = default_skill_root(DOMESTIC_DETAIL_SKILL_ID)
    prompt = load_planner_system_prompt(skill_root, DOMESTIC_DETAIL_SKILL_ID)
    expected_references = [
        path
        for path in _AURORA_PLANNER_PROTOCOL_FILES
        if path.startswith("references/")
    ]

    markers = [f"--- BEGIN {relative} ---" for relative in expected_references]
    assert "--- BEGIN SKILL.md ---" in prompt
    assert all(marker in prompt for marker in markers)
    assert [prompt.index(marker) for marker in markers] == sorted(
        prompt.index(marker) for marker in markers
    )
    # The planner prompt is intentionally bounded. The executor independently
    # verifies the complete Skill source/manifest before issuing a gateway call.
    assert "--- BEGIN references/category-router.md ---" not in prompt


def test_crossborder_detail_flag_switches_only_the_international_real_route(
    client: TestClient,
) -> None:
    enabled = _build_real_coordinator(
        client,
        crossborder_detail_provider_enabled=True,
    )
    enabled_planners = cast(DetailMarketRoutingPlanner, enabled.detail_planner)
    enabled_router = cast(RegisteredOperationRoutingProvider, enabled.provider)
    crossborder_planner = cast(
        XinghePlanningCoordinator,
        enabled_planners.international,
    )
    crossborder_adapter = cast(
        CrossborderDetailProvider,
        enabled_router.detail_provider,
    )

    assert crossborder_planner.skill_id == CROSSBORDER_DETAIL_SKILL_ID
    assert crossborder_planner.skill_root == default_skill_root(
        CROSSBORDER_DETAIL_SKILL_ID
    )
    assert isinstance(crossborder_adapter, CrossborderDetailProvider)
    crossborder_delegate = cast(XingheImageProvider, crossborder_adapter.delegate)
    assert crossborder_delegate.skill_id == CROSSBORDER_DETAIL_SKILL_ID
    assert crossborder_delegate.skill_root == default_skill_root(
        CROSSBORDER_DETAIL_SKILL_ID
    )
    assert enabled_planners.domestic.skill_id == DOMESTIC_DETAIL_SKILL_ID
    assert (
        enabled_router.domestic_detail_provider.route.skill_id
        == DOMESTIC_DETAIL_SKILL_ID
    )

    disabled = _build_real_coordinator(client)
    disabled_planners = cast(DetailMarketRoutingPlanner, disabled.detail_planner)
    disabled_router = cast(RegisteredOperationRoutingProvider, disabled.provider)
    legacy_planner = cast(XinghePlanningCoordinator, disabled_planners.international)
    legacy_provider = cast(XingheImageProvider, disabled_router.detail_provider)

    assert legacy_planner.skill_id == "xinghe-ecommerce-detail"
    assert legacy_planner.skill_root == default_xinghe_skill_root()
    assert isinstance(legacy_provider, XingheImageProvider)
    assert legacy_provider.skill_id == "xinghe-ecommerce-detail"
    assert legacy_provider.skill_root == default_xinghe_skill_root()
    assert not isinstance(disabled_router.detail_provider, CrossborderDetailProvider)
    assert disabled_planners.domestic.skill_id == DOMESTIC_DETAIL_SKILL_ID
    assert (
        disabled_router.domestic_detail_provider.route.skill_id
        == DOMESTIC_DETAIL_SKILL_ID
    )


def test_crossborder_unsupported_platform_fails_before_provider_calls(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="crossborder-unsupported-platform.png")
    job_id = _create_detail_job(
        client,
        source["id"],
        "international",
        platform_code="unsupported-marketplace",
    )
    gateway = FakeXingheGateway()
    coordinator = build_fake_coordinator(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        crossborder_detail_provider_enabled=True,
    )

    status = asyncio.run(coordinator.run(job_id))
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()

    assert status == JobStatus.FAILED
    assert current["status"] == JobStatus.FAILED.value
    assert current["error_code"] == "DETAIL_PLATFORM_UNSUPPORTED"
    assert gateway.planner_calls == []
    assert gateway.image_calls == []
    assert current.get("plan") is None


def test_mock_coordinator_ignores_real_provider_flags(
    client: TestClient,
) -> None:
    coordinator = build_fake_coordinator(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        product_image_provider_enabled=True,
        reference_remix_provider_enabled=True,
        model_pose_transfer_provider_enabled=True,
        creative_image_provider_enabled=True,
        amazon_visual_suite_provider_enabled=True,
        crossborder_detail_provider_enabled=True,
    )
    router = cast(RegisteredOperationRoutingProvider, coordinator.provider)
    planner_router = cast(DetailMarketRoutingPlanner, coordinator.detail_planner)

    for operation in (
        JobType.PRODUCT_IMAGE.value,
        JobType.MODEL_POSE_TRANSFER.value,
        JobType.CREATIVE_IMAGE.value,
    ):
        assert router.routes[operation] is router.default_provider
    replica = router.routes[JobType.REFERENCE_REMIX.value]
    assert isinstance(replica, ReferenceRemixRouterProvider)
    assert replica.legacy is router.default_provider
    assert replica.exact is router.default_provider
    assert isinstance(replica.native, UnavailableProvider)
    amazon = router.routes[JobType.AMAZON_VISUAL_SUITE.value]
    assert amazon.delegate is router.default_provider
    domestic_main_image = router.routes[JobType.DOMESTIC_MAIN_IMAGE.value]
    assert isinstance(domestic_main_image, AuroraMainImageProvider)
    assert (
        domestic_main_image.gateway is coordinator.detail_planner.international.gateway
    )
    assert planner_router.international.skill_id == "xinghe-ecommerce-detail"
    assert isinstance(router.detail_provider, XingheImageProvider)
    assert not isinstance(router.detail_provider, CrossborderDetailProvider)


def test_tengli_task_runner_propagates_all_provider_switches(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.xinghe_agent as xinghe_agent

    monkeypatch.setattr(
        xinghe_agent,
        "build_tengli_gateway",
        lambda _settings: FakeXingheGateway(),
    )
    enabled_settings = client.app.state.settings.model_copy(
        update={
            "product_image_provider_enabled": True,
            "reference_remix_provider_enabled": True,
            "model_pose_transfer_provider_enabled": True,
            "creative_image_provider_enabled": True,
            "amazon_visual_suite_provider_enabled": True,
            "crossborder_detail_provider_enabled": True,
        }
    )

    runner = build_tengli_task_runner(
        client.app.state.session_factory,
        enabled_settings,
        client.app.state.storage,
        concurrency=2,
    )
    router = cast(
        RegisteredOperationRoutingProvider,
        runner.coordinator.provider,
    )
    planner_router = cast(
        DetailMarketRoutingPlanner,
        runner.coordinator.detail_planner,
    )

    assert all(
        isinstance(adapter.delegate, XingheSingleImageProvider)
        for operation, adapter in router.routes.items()
        if operation
        not in {
            JobType.DOMESTIC_MAIN_IMAGE.value,
            JobType.DOMESTIC_DETAIL_REVIEW.value,
        }
    )
    assert isinstance(
        router.routes[JobType.DOMESTIC_MAIN_IMAGE.value], UnavailableProvider
    )
    assert isinstance(router.detail_provider, CrossborderDetailProvider)
    assert planner_router.international.skill_id == CROSSBORDER_DETAIL_SKILL_ID
