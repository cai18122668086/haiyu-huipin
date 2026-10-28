from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.amazon_visual_suite_provider import (
    AMAZON_EXPECTED_ITEM_COUNT,
    AMAZON_ITEM_KEYS,
    AMAZON_ITEM_ROLES,
    AMAZON_ITEM_SPECS,
    AMAZON_PLAN_CONFIGURATION_KEY,
    AMAZON_SKILL_ROW_IDS,
    AmazonVisualSuiteProvider,
    validate_amazon_planner_payload,
)
from app.domain import JobStatus, PlanValidationStatus
from app.models import JobAssetRecord, JobItemRecord, JobPlanRecord
from app.provider import ProviderOutcome, ProviderRequest, ProviderResult
from app.task_runner import MockTaskRunner
from app.xinghe_agent import (
    AmazonVisualReviewCallResult,
    AmazonVisualReviewRequest,
    FakeXingheGateway,
    ImageCallResult,
    ImageEditRequest,
    PlannerCallResult,
    PlannerRequest,
    SourceImage,
    XingheAgentError,
    _fake_amazon_plan,
    _fake_plan,
    validate_planner_payload,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset


@dataclass
class _RecordingDelegate:
    name: str = "recording"
    model: str = "recording-image"
    pricing_version: str = "recording-v1"
    requests: list[ProviderRequest] = field(default_factory=list)

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        return ProviderResult(outcome=ProviderOutcome.SUCCEEDED)


class _ManifestValidator:
    def validate(self, skill_id: str) -> str:
        assert skill_id == "xinghe-amazon-visual-suite"
        return "a" * 64


class _CountingGateway(FakeXingheGateway):
    provider_name = "offline-contract-provider"
    planner_model = "offline-contract-planner"
    image_model = "offline-contract-image"
    pricing_version = "offline-contract-v1"

    async def plan(self, request: PlannerRequest) -> PlannerCallResult:
        result = await super().plan(request)
        return replace(
            result,
            provider_request_id=f"offline_plan_{len(self.planner_calls)}",
            request_id_presence="present",
        )

    async def edit(self, request: ImageEditRequest) -> ImageCallResult:
        result = await super().edit(request)
        return replace(
            result,
            provider_request_id=(
                f"offline_image_{request.job_id}_{request.job_item_id}_"
                f"{request.attempt_id}"
            ),
        )


class _DuplicateOutputGateway(_CountingGateway):
    async def edit(self, request: ImageEditRequest) -> ImageCallResult:
        return await super().edit(replace(request, page_number=1))


class _OneFailureGateway(_CountingGateway):
    def __init__(self) -> None:
        super().__init__(image_failures={1: 1})


class _ReviewProtocolErrorGateway(_CountingGateway):
    async def review_amazon(
        self, request: AmazonVisualReviewRequest
    ) -> AmazonVisualReviewCallResult:
        result = await super().review_amazon(request)
        return replace(
            result,
            visual_success=None,
            error_code="AMAZON_VISUAL_REVIEW_INVALID",
            error_message="Amazon visual review named an unknown candidate item.",
            retryable=False,
        )


class _ReviewQualityFailureGateway(_CountingGateway):
    failed_item_key = AMAZON_ITEM_KEYS[1]

    async def review_amazon(
        self, request: AmazonVisualReviewRequest
    ) -> AmazonVisualReviewCallResult:
        result = await super().review_amazon(request)
        return replace(
            result,
            visual_success=False,
            summary="The listed image has a real visual quality defect.",
            reason_codes=("AMAZON_PRODUCT_IDENTITY_MISMATCH",),
            retry_guidance="Redraw only the listed item with the confirmed product.",
            failed_item_keys=(self.failed_item_key,),
        )


def _install_counting_runner(
    client: TestClient,
    *,
    gateway_type: type[_CountingGateway] = _CountingGateway,
) -> _CountingGateway:
    gateway = gateway_type()
    client.app.state.storage.validator.max_image_pixels = 5_000_000
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        xinghe_concurrency=2,
        amazon_visual_suite_provider_enabled=True,
    )
    return gateway


def _planner_request(
    *,
    page_count: int = AMAZON_EXPECTED_ITEM_COUNT,
    planning_profile: str = "amazon_visual_suite",
) -> PlannerRequest:
    return PlannerRequest(
        workspace_id=WORKSPACE_ID,
        job_id="job_contract",
        group_key="amazon-product",
        sources=(),
        page_count=page_count,
        language="en-US",
        product_name="Visible product",
        product_information=("Visible facts only",),
        platform_code="amazon-us",
        size="amazon-canonical-multi-size-v1",
        idempotency_key="job_contract:planner",
        planning_profile=planning_profile,
    )


def _valid_amazon_payload() -> dict[str, Any]:
    return _fake_amazon_plan(_planner_request())


def _provider_request_without_plan_context() -> ProviderRequest:
    spec = AMAZON_ITEM_SPECS[0]
    return ProviderRequest(
        workspace_id=WORKSPACE_ID,
        job_id="job_contract",
        job_item_id="item_contract",
        attempt_id="attempt_contract",
        operation="amazon_visual_suite",
        idempotency_key="attempt_contract",
        configuration={
            "job": {
                "input": {
                    "white_background_product_asset_ids": ["asset-white"],
                },
                "settings": {
                    "marketplace": "amazon-us",
                    "language": "en-US",
                },
            },
            "item": {
                "item_key": spec.item_key,
                "group": spec.group,
                "target_width": spec.width,
                "target_height": spec.height,
            },
        },
    )


def _white_product_source(*, asset_id: str = "asset-white") -> SourceImage:
    return SourceImage(
        asset_id=asset_id,
        group_key="amazon-product",
        filename="white.jpg",
        content_type="image/jpeg",
        sha256="a" * 64,
        payload=b"real-product-source",
        source_role="white_background_product",
    )


def test_amazon_validator_accepts_original_skill_contract_without_extra_locks() -> None:
    payload = _valid_amazon_payload()
    original = deepcopy(payload)

    structured = validate_amazon_planner_payload(
        payload,
        sources=(_white_product_source(),),
    )

    assert payload == original
    assert set(structured["plan"]) == {
        "campaign_style_lock",
        "product_consistency_lock",
        "layout_diversity_lock",
        "screens",
    }
    assert [screen["id"] for screen in structured["plan"]["screens"]] == list(
        AMAZON_SKILL_ROW_IDS
    )
    assert [screen["item_key"] for screen in structured["plan"]["screens"]] == list(
        AMAZON_ITEM_KEYS
    )
    assert [screen["role"] for screen in structured["plan"]["screens"]] == list(
        AMAZON_ITEM_ROLES
    )


def test_amazon_validator_requires_a_real_primary_product_source_role() -> None:
    payload = _valid_amazon_payload()
    scene_source = replace(
        _white_product_source(),
        source_role="scene_reference",
    )

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload, sources=(scene_source,))

    assert raised.value.code == "AMAZON_WHITE_BACKGROUND_SOURCE_REQUIRED"
    assert raised.value.json_pointer == "/analysis/product_image_analysis"


def test_amazon_validator_does_not_reject_authentic_logo_or_label_evidence() -> None:
    payload = _valid_amazon_payload()
    payload["analysis"]["product_image_analysis"]["visible_facts"].append(
        "Authentic trademark and readable product label are visible."
    )

    structured = validate_amazon_planner_payload(
        payload,
        sources=(_white_product_source(),),
    )

    assert structured["plan"]["screens"][0]["item_key"] == "MAIN-01"


def test_amazon_validator_requires_complete_product_master() -> None:
    payload = _valid_amazon_payload()
    payload["analysis"]["product_master_description"].pop("silhouette_and_proportions")

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload)

    assert raised.value.code == "PLANNER_SCHEMA_INVALID"
    assert raised.value.json_pointer == (
        "/analysis/product_master_description/silhouette_and_proportions"
    )


def test_amazon_validator_requires_exactly_21_canonical_skill_ids() -> None:
    payload = _valid_amazon_payload()
    payload["plan"]["screens"][4]["id"] = "secondary-99"

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload)

    assert raised.value.code == "AMAZON_ITEM_INVENTORY_INVALID"
    assert raised.value.json_pointer == "/plan/screens"


def test_amazon_validator_rejects_wrong_skill_size() -> None:
    payload = _valid_amazon_payload()
    payload["plan"]["screens"][7]["final_size"] = "1600x1200"

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload)

    assert raised.value.code == "AMAZON_TARGET_SIZE_MISMATCH"
    assert raised.value.json_pointer == "/plan/screens/7/final_size"


def test_amazon_validator_rejects_missing_required_row_field() -> None:
    payload = _valid_amazon_payload()
    payload["plan"]["screens"][0].pop("headline")

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload)

    assert raised.value.code == "AMAZON_PLAN_SCREEN_INVALID"
    assert raised.value.json_pointer == "/plan/screens/0/headline"


def test_amazon_validator_allows_blank_main_image_headline() -> None:
    payload = _valid_amazon_payload()
    payload["plan"]["screens"][0]["headline"] = ""

    structured = validate_amazon_planner_payload(payload)

    assert structured["plan"]["screens"][0]["headline"] == ""


def test_amazon_validator_rejects_blank_non_main_headline() -> None:
    payload = _valid_amazon_payload()
    payload["plan"]["screens"][1]["headline"] = "  "

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload)

    assert raised.value.code == "AMAZON_PLAN_SCREEN_INVALID"
    assert raised.value.json_pointer == "/plan/screens/1/headline"


def test_amazon_validator_rejects_needs_confirmation_claim_in_copy() -> None:
    payload = _valid_amazon_payload()
    payload["analysis"]["information_confidence"]["needs_confirmation"] = [
        "Certified waterproof"
    ]
    payload["plan"]["screens"][1]["headline"] = "Certified waterproof"

    with pytest.raises(XingheAgentError) as raised:
        validate_amazon_planner_payload(payload)

    assert raised.value.code == "AMAZON_UNSUPPORTED_CLAIM_INVALID"
    assert raised.value.json_pointer == "/plan/screens/1/headline"


def test_amazon_validator_allows_safe_copy_when_other_information_is_unknown() -> None:
    payload = _valid_amazon_payload()
    payload["analysis"]["information_confidence"]["needs_confirmation"] = [
        "Exact material composition"
    ]
    payload["plan"]["screens"][1]["information_confidence"] = "needs confirmation"
    payload["plan"]["screens"][1]["headline"] = "See the visible structure"

    structured = validate_amazon_planner_payload(payload)

    assert structured["plan"]["screens"][1]["headline"] == ("See the visible structure")


def test_crossborder_validator_rejects_excessive_composition_reuse() -> None:
    request = _planner_request(
        page_count=8,
        planning_profile="crossborder_detail",
    )
    payload = _fake_plan(request)
    for screen in payload["plan"]["screens"]:
        screen["layout_family"] = "same-composition-system"

    with pytest.raises(XingheAgentError) as raised:
        validate_planner_payload(
            payload,
            request.page_count,
            require_cross_page_locks=True,
        )

    assert raised.value.code == "PLANNER_CROSS_PAGE_DIVERSITY_INVALID"
    assert raised.value.json_pointer == "/plan/screens"


def test_amazon_provider_rejects_missing_persisted_plan_context() -> None:
    delegate = _RecordingDelegate()
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=_ManifestValidator(),
    )

    result = asyncio.run(provider.execute(_provider_request_without_plan_context()))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "AMAZON_PLAN_CONTEXT_MISSING"
    assert result.artifact is None
    assert delegate.requests == []


def _create_amazon_job(
    client: TestClient, asset_id: str, *, auto_start: bool | None = False
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "amazon_visual_suite",
            "intent": "submit",
            "display_name": "Amazon fail-closed planning gate",
            "input": {
                "white_background_product_asset_ids": [asset_id],
            },
            "configuration": {
                **(
                    {"auto_start_after_plan": auto_start}
                    if auto_start is not None else {}
                ),
                "marketplace": "amazon-us",
                "product_name": "Visible product",
                "listing_title": "Visible product listing",
                "bullet_points": ["Visible facts only"],
                "language": "en-US",
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _reachable_gateways(root: object) -> list[Any]:
    gateways: dict[int, Any] = {}
    seen: set[int] = set()
    pending = [root]
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        gateway = getattr(value, "gateway", None)
        if gateway is not None and hasattr(gateway, "image_calls"):
            gateways[id(gateway)] = gateway
        for attribute in ("international", "domestic", "amazon"):
            child = getattr(value, attribute, None)
            if child is not None:
                pending.append(child)
    return list(gateways.values())


def test_amazon_job_persists_product_master_and_plan_before_confirmation(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="amazon-white-background.png")
    job = _create_amazon_job(client, source["id"])

    published = dispatch_pending(client)
    assert published == 1
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()

    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert planned["stage"] == "awaiting_confirmation"
    assert planned["plan"]["validation_status"] == PlanValidationStatus.VALID.value
    assert planned["plan"]["confirmed_at"] is None
    payload = planned["plan"]["payload"]
    assert payload["state"] == "awaiting_confirmation"
    assert len(payload["pages"]) == AMAZON_EXPECTED_ITEM_COUNT
    assert tuple(page["item_key"] for page in payload["pages"]) == AMAZON_ITEM_KEYS
    product_master = payload["groups"][0]["analysis"]["product_master_description"]
    assert set(product_master) >= {
        "category_and_identity",
        "silhouette_and_proportions",
        "color_material_and_texture",
        "structure_parts_and_accessories",
        "logos_labels_patterns_and_packaging",
        "scale_and_physics",
        "unknowns",
    }
    assert payload["usage"]["image"] == {
        "reported_requests": 0,
        "unreported_requests": 0,
    }

    with client.app.state.session_factory() as session:
        durable_plan = session.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"])
        )
        items = session.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job["id"])
            .order_by(JobItemRecord.ordinal)
        ).all()

    assert durable_plan is not None
    assert durable_plan.confirmed_at is None
    assert durable_plan.validation_status == PlanValidationStatus.VALID.value
    assert len(items) == AMAZON_EXPECTED_ITEM_COUNT
    assert tuple(item.item_key for item in items) == AMAZON_ITEM_KEYS
    assert all(item.stage == "awaiting_confirmation" for item in items)
    for item in items:
        context = item.configuration[AMAZON_PLAN_CONFIGURATION_KEY]
        assert set(context) >= {
            "analysis",
            "campaign_style_lock",
            "product_consistency_lock",
            "layout_diversity_lock",
            "screen",
            "resume_signature",
        }
        assert context["analysis"]["product_master_description"] == product_master
        assert context["screen"]["item_key"] == item.item_key
        assert context["resume_signature"] == payload["resume_signature"]

    gateways = _reachable_gateways(
        client.app.state.task_runner.coordinator.detail_planner
    )
    assert gateways
    assert all(gateway.image_calls == [] for gateway in gateways)
    assert sum(len(gateway.planner_calls) for gateway in gateways) == 1

    assert dispatch_pending(client) == 0
    assert sum(len(gateway.planner_calls) for gateway in gateways) == 1


def test_amazon_cancel_while_awaiting_confirmation_keeps_image_calls_zero(
    client: TestClient,
) -> None:
    gateway = _install_counting_runner(client)
    source = upload_asset(client, filename="amazon-cancel-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1

    cancelled = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/cancel",
        headers=idem(),
        json={"reason": "user cancelled before Amazon generation"},
    )

    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == JobStatus.CANCELLED.value
    assert len(gateway.planner_calls) == 1
    assert gateway.image_calls == []


def test_confirmed_amazon_plan_drives_all_prompts_and_retry_reuses_plan(
    client: TestClient,
) -> None:
    gateway = _install_counting_runner(client)
    source = upload_asset(client, filename="amazon-confirm-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1
    assert len(gateway.planner_calls) == 1

    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 202, confirmed.text
    published = dispatch_pending(client)
    assert published == 1, {
        "job": client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json(),
        "planner_calls": len(gateway.planner_calls),
        "image_calls": len(gateway.image_calls),
    }
    completed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()

    assert completed["status"] == JobStatus.COMPLETED.value, [
        (item["item_key"], item["status"], item.get("error_code"))
        for item in completed["items"]
    ]
    # The production path now includes one bounded suite-quality review call.
    assert completed["api_request_count"] == 23
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == AMAZON_EXPECTED_ITEM_COUNT
    assert len(gateway.amazon_review_calls) == 1
    assert {call.group_key for call in gateway.image_calls} == set(AMAZON_ITEM_KEYS)
    for call in gateway.image_calls:
        assert "Product Master Description:" in call.prompt
        assert "Product Consistency Lock:" in call.prompt
        assert "Campaign Style Lock:" in call.prompt
        assert "Layout Diversity Lock:" in call.prompt

    business_quality = completed["plan"]["payload"]["business_quality"]
    assert business_quality["status"] == "passed"
    assert business_quality["review_round"] == 1
    assert business_quality["visual_review"]["visual_success"] is True
    assert len(business_quality["generation_routes"]) == AMAZON_EXPECTED_ITEM_COUNT

    calls_before_redelivery = (
        len(gateway.planner_calls),
        len(gateway.image_calls),
        len(gateway.amazon_review_calls),
    )
    asyncio.run(client.app.state.task_runner.run(job["id"]))
    redelivered = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert (
        len(gateway.planner_calls),
        len(gateway.image_calls),
        len(gateway.amazon_review_calls),
    ) == calls_before_redelivery
    assert redelivered["plan"]["payload"]["business_quality"] == business_quality


def test_amazon_transient_failure_retries_in_place_without_second_planner_call(
    client: TestClient,
) -> None:
    gateway = _install_counting_runner(
        client,
        gateway_type=_OneFailureGateway,
    )
    source = upload_asset(client, filename="amazon-retry-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert dispatch_pending(client) == 1
    completed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()

    assert completed["status"] == JobStatus.COMPLETED.value
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == AMAZON_EXPECTED_ITEM_COUNT + 1
    assert completed["succeeded_item_count"] == AMAZON_EXPECTED_ITEM_COUNT
    assert completed["failed_item_count"] == 0


def test_amazon_review_protocol_failure_requires_manual_review(
    client: TestClient,
) -> None:
    gateway = _install_counting_runner(
        client,
        gateway_type=_ReviewProtocolErrorGateway,
    )
    source = upload_asset(client, filename="amazon-review-protocol-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert dispatch_pending(client) == 1

    completed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()

    assert completed["status"] == JobStatus.AWAITING_FINAL_REVIEW.value
    assert completed["stage"] == "awaiting_suite_review"
    assert completed["succeeded_item_count"] == AMAZON_EXPECTED_ITEM_COUNT
    assert completed["failed_item_count"] == 0
    assert len(gateway.image_calls) == AMAZON_EXPECTED_ITEM_COUNT
    assert len(gateway.amazon_review_calls) == 1
    assert completed["plan"]["payload"]["business_quality"]["status"] == (
        "review_infrastructure_failed"
    )
    assert all(item["status"] == "succeeded" for item in completed["items"])

    with client.app.state.session_factory() as session:
        generated_links = session.scalars(
            select(JobAssetRecord).where(
                JobAssetRecord.job_id == job["id"],
                JobAssetRecord.job_item_id.is_not(None),
            )
        ).all()

    assert len(generated_links) == AMAZON_EXPECTED_ITEM_COUNT
    assert all(link.is_deliverable for link in generated_links)
    assert all(
        not link.validation.get("superseded_by_suite_review")
        for link in generated_links
    )


def test_amazon_quality_failure_redraws_failed_item_and_blocks_after_two_rounds(
    client: TestClient,
) -> None:
    gateway = _install_counting_runner(
        client,
        gateway_type=_ReviewQualityFailureGateway,
    )
    source = upload_asset(client, filename="amazon-review-quality-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert dispatch_pending(client) == 1

    completed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()

    assert completed["status"] == JobStatus.PARTIAL.value
    assert completed["succeeded_item_count"] == AMAZON_EXPECTED_ITEM_COUNT - 1
    assert completed["failed_item_count"] == 1
    assert len(gateway.amazon_review_calls) == 2
    assert len(gateway.image_calls) == AMAZON_EXPECTED_ITEM_COUNT + 1
    assert all(
        sum(call.group_key == item_key for call in gateway.image_calls)
        == (2 if item_key == "SECONDARY-01" else 1)
        for item_key in AMAZON_ITEM_KEYS
    )
    assert next(
        item for item in completed["items"] if item["item_key"] == "SECONDARY-01"
    )["error_code"] == "AMAZON_QA_QUALITY_FAILED"


def test_amazon_quality_review_accepts_duplicate_outputs_without_redraw(
    client: TestClient,
) -> None:
    gateway = _install_counting_runner(
        client,
        gateway_type=_DuplicateOutputGateway,
    )
    source = upload_asset(client, filename="amazon-duplicate-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 202, confirmed.text
    published = dispatch_pending(client)
    assert published == 1, {
        "job": client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json(),
        "planner_calls": len(gateway.planner_calls),
        "image_calls": len(gateway.image_calls),
    }

    completed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()

    assert completed["status"] == JobStatus.COMPLETED.value
    assert completed["stage"] == "completed"
    assert completed["succeeded_item_count"] == AMAZON_EXPECTED_ITEM_COUNT
    assert completed["failed_item_count"] == 0
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == AMAZON_EXPECTED_ITEM_COUNT
    assert len(gateway.amazon_review_calls) == 1


def test_duplicate_amazon_dispatch_does_not_close_an_unfinished_suite(
    client: TestClient,
) -> None:
    _install_counting_runner(client)
    source = upload_asset(client, filename="amazon-inflight-white.png")
    job = _create_amazon_job(client, source["id"])
    assert dispatch_pending(client) == 1
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 202, confirmed.text

    coordinator = client.app.state.task_runner.coordinator
    coordinator.claim_next_item = lambda job_id: None
    status = asyncio.run(coordinator.run(job["id"]))

    observed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert status == JobStatus.PROCESSING
    assert observed["status"] == JobStatus.PROCESSING.value
    assert observed["succeeded_item_count"] == 0
    assert any(item["status"] == "pending" for item in observed["items"])


def test_valid_amazon_payload_is_not_mutated_by_validation() -> None:
    payload = _valid_amazon_payload()
    original = deepcopy(payload)

    validated = validate_amazon_planner_payload(payload)

    assert payload == original
    assert (
        tuple(screen["item_key"] for screen in validated["plan"]["screens"])
        == AMAZON_ITEM_KEYS
    )
