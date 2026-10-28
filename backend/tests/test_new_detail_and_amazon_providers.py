import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.amazon_visual_suite_provider import (
    AMAZON_AUDIT_CONFIGURATION_KEY,
    AMAZON_EXPECTED_ITEM_COUNT,
    AMAZON_ITEM_KEYS,
    AMAZON_ITEM_SPECS,
    AMAZON_PLAN_CONFIGURATION_KEY,
    AMAZON_PLANNING_POLICY_VERSION,
    AMAZON_SKILL_ROW_ID_BY_ITEM_KEY,
    AmazonVisualSuiteProvider,
    amazon_item_spec,
    validate_amazon_item_keys,
)
from app.crossborder_detail_provider import (
    CROSSBORDER_DETAIL_SKILL_ID,
    CrossborderDetailProvider,
)
from app.domestic_detail_provider import (
    DOMESTIC_DEFAULT_SIZE,
    DOMESTIC_DETAIL_SKILL_ID,
    DomesticDetailProvider,
)
from app.new_detail_skill_provider import (
    SKILL_ADAPTER_CONFIGURATION_KEY,
    AdapterValidationError,
    BundledSkillManifestValidator,
)
from app.new_image_skill_provider import SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION
from app.provider import (
    ProviderArtifact,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from app.skill_runtime import SkillManifestRegistry, SkillRuntimeError
from app.xinghe_single_image_provider import DEFAULT_ALLOWED_PROMPT_FIELDS


def _assert_strict_single_image_envelope(request: ProviderRequest) -> None:
    envelope = request.configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert set(envelope) == {
        "schema_version",
        "skill_id",
        "skill_content_hash",
        "operation",
        "item_key",
        "item_ordinal",
        "source_assets",
        "target",
        "prompt_input",
    }
    identifier = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
    assert identifier.fullmatch(envelope["item_key"])
    assert 1 <= len(envelope["source_assets"]) <= 8
    target = envelope["target"]
    assert set(target) == {
        "width",
        "height",
        "aspect_ratio",
        "quality",
        "output_format",
        "fit",
    }
    assert isinstance(target["width"], int) and target["width"] > 0
    assert isinstance(target["height"], int) and target["height"] > 0
    divisor = math.gcd(target["width"], target["height"])
    assert target["aspect_ratio"] == (
        f"{target['width'] // divisor}:{target['height'] // divisor}"
    )
    assert target["fit"] in {"cover", "contain", "pad"}
    assert set(envelope["prompt_input"]) <= DEFAULT_ALLOWED_PROMPT_FIELDS
    assert all(value not in ("", []) for value in envelope["prompt_input"].values())


@dataclass
class RecordingManifestValidator:
    content_hash: str = "a" * 64
    error: SkillRuntimeError | None = None
    calls: list[str] = field(default_factory=list)

    def validate(self, skill_id: str) -> str:
        self.calls.append(skill_id)
        if self.error is not None:
            raise self.error
        return self.content_hash


class RecordingDelegate:
    name = "mock"
    model = "adapter-test"
    pricing_version = "adapter-test-v1"

    def __init__(self, result: ProviderResult | None = None) -> None:
        self.result = result or ProviderResult(outcome=ProviderOutcome.SUCCEEDED)
        self.requests: list[ProviderRequest] = []

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        return self.result


def _request(
    operation: str,
    configuration: dict[str, object],
) -> ProviderRequest:
    return ProviderRequest(
        workspace_id="ws_test",
        job_id="job_test",
        job_item_id="item_test",
        attempt_id="attempt_test",
        operation=operation,
        idempotency_key="idem_test",
        configuration=configuration,
    )


def _amazon_request(
    item_key: str,
    width: int,
    height: int,
    *,
    operation: str = "amazon_visual_suite",
    marketplace: object = "amazon-us",
    **item_overrides: object,
) -> ProviderRequest:
    item: dict[str, object] = {
        "item_key": item_key,
        "target_width": width,
        "target_height": height,
        **item_overrides,
    }
    try:
        spec = amazon_item_spec(item_key)
    except AdapterValidationError:
        spec = None
    if spec is not None:
        item[AMAZON_PLAN_CONFIGURATION_KEY] = {
            "schema_version": AMAZON_PLANNING_POLICY_VERSION,
            "analysis": {
                "product_image_analysis": {
                    "visible_facts": ["One supplied test SKU."],
                    "possible_use_contexts": ["category-appropriate use"],
                    "unknowns": [],
                },
                "information_confidence": {
                    "confirmed": ["Visible supplied SKU."],
                    "reasonable_inference": [],
                    "needs_confirmation": [],
                },
                "buyer_demand_map": ["recognition", "trust", "purchase"],
                "demand_to_selling_point_match": [
                    "recognition -> Show the exact supplied SKU."
                ],
                "product_master_description": {
                    "category_and_identity": "One exact supplied test SKU.",
                    "silhouette_and_proportions": "Preserve the supplied shape.",
                    "color_material_and_texture": "Preserve visible appearance.",
                    "structure_parts_and_accessories": (
                        "Do not add or remove visible parts."
                    ),
                    "logos_labels_patterns_and_packaging": (
                        "Preserve visible identity markings."
                    ),
                    "scale_and_physics": ("Keep scale and physical logic credible."),
                    "unknowns": [],
                },
            },
            "campaign_style_lock": {
                "platform": "Amazon US",
                "marketplace_language": "en-US",
                "design_direction": "Product-led evidence",
                "palette": "Product-led restrained palette",
                "lighting": "Consistent commercial lighting",
                "typography_mood": "Clear shopper-facing hierarchy",
                "label_style": "Sparse evidence labels",
                "density": "Readable at listing and phone size",
            },
            "product_consistency_lock": {
                "product_identity": "Preserve one exact SKU",
                "structure": "Preserve all visible structural parts",
                "color_area_ratio": "Preserve visible color-area ratio",
                "material_look": "Preserve visible material appearance",
                "logo_and_label_placement": (
                    "Preserve authentic logo and label placement"
                ),
                "physical_scale": "Keep physical scale credible",
            },
            "layout_diversity_lock": {
                "structure_strategy": (
                    "Use at least five distinct structures across selling images"
                ),
                "large_product_top_title_limit": 3,
            },
            "screen": {
                "id": AMAZON_SKILL_ROW_ID_BY_ITEM_KEY[spec.item_key],
                "item_key": spec.item_key,
                "asset_type": spec.asset_type,
                "final_size": f"{spec.width}x{spec.height}",
                "buyer_question": spec.buyer_question,
                "selling_point": "Planner-selected selling point.",
                "information_confidence": "confirmed",
                "visual_proof": spec.visual_proof,
                "headline": "Planner-selected headline.",
                "subcopy": "Planner-selected support copy.",
                "layout_direction": "Planner-selected layout direction.",
                "source_material": ("white_background_product and product_detail"),
                "risk_note": "Use only confirmed source facts.",
            },
            "resume_signature": "b" * 64,
        }
    return _request(
        operation,
        {
            "job": {
                "input": {
                    "white_background_product_asset_ids": ["asset-white"],
                    "detail_asset_ids": ["asset-detail"],
                    "package_asset_ids": ["asset-package"],
                    "scene_asset_ids": ["asset-scene"],
                    "brand_asset_ids": ["asset-brand"],
                    "reference_asset_ids": ["asset-style"],
                    "competitor_reference_asset_ids": ["asset-competitor"],
                },
                "settings": {
                    "marketplace": marketplace,
                    "product_name": "Test product",
                    "listing_title": "Test listing",
                    "bullet_points": ["Point one"],
                    "brand_colors": ["navy"],
                    "language": "en-US",
                    "private_note": "must-not-reach-envelope",
                },
            },
            "item": item,
        },
    )


class StrictRecordingDelegate(RecordingDelegate):
    async def execute(self, request: ProviderRequest) -> ProviderResult:
        _assert_strict_single_image_envelope(request)
        return await super().execute(request)


def _artifact(width: int, height: int) -> ProviderArtifact:
    payload = b"real-provider-test-payload"
    return ProviderArtifact(
        payload=payload,
        filename="result.png",
        content_type="image/png",
        width=width,
        height=height,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _jpeg_artifact(width: int, height: int) -> ProviderArtifact:
    payload = b"amazon-ready-jpeg-test-payload"
    return ProviderArtifact(
        payload=payload,
        filename="result.jpg",
        content_type="image/jpeg",
        width=width,
        height=height,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_bundled_manifest_validator_verifies_three_new_sources() -> None:
    registry = SkillManifestRegistry.bundled()
    source_base = Path(__file__).resolve().parents[1] / "skill_sources"
    skill_ids = (
        DOMESTIC_DETAIL_SKILL_ID,
        CROSSBORDER_DETAIL_SKILL_ID,
        "xinghe-amazon-visual-suite",
    )
    validator = BundledSkillManifestValidator(
        registry,
        source_roots={skill_id: source_base / skill_id for skill_id in skill_ids},
    )

    for skill_id in skill_ids:
        assert validator.validate(skill_id) == registry.get(skill_id).content_hash


def test_domestic_adapter_attaches_auto_plan_contract_and_defaults() -> None:
    delegate = RecordingDelegate()
    validator = RecordingManifestValidator()
    provider = DomesticDetailProvider(delegate, manifest_validator=validator)
    request = _request(
        "detail_pack",
        {
            "job": {
                "market_region": "domestic",
                "platform_code": "jd",
                "page_count": 8,
            },
            "item": {"item_key": "page-001"},
        },
    )

    result = asyncio.run(provider.execute(request))

    assert result is delegate.result
    assert validator.calls == [DOMESTIC_DETAIL_SKILL_ID]
    routed = delegate.requests[0]
    assert routed.operation == "detail_pack"
    assert routed.configuration["job"] == request.configuration["job"]
    adapter = routed.configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert adapter["skill_id"] == DOMESTIC_DETAIL_SKILL_ID
    assert adapter["skill_content_hash"] == "a" * 64
    assert adapter["market_region"] == "domestic"
    assert adapter["planning"] == {
        "required": True,
        "persist_validated_plan": True,
        "auto_confirm_after_validation": False,
        "structure_repair_limit": 1,
        "changes_existing_confirmation_gate": False,
        "approval_scope": "campaign_approval_group_to_screen_packet",
    }
    assert adapter["prompt"]["platform_code"] == "jd"
    assert adapter["prompt"]["language"] == "zh-CN"
    assert adapter["prompt"]["screen_aspect_ratio"] == "3:4"
    assert adapter["prompt"]["pair_aspect_ratio"] == "3:8"
    assert adapter["prompt"]["resolved_size"] == DOMESTIC_DEFAULT_SIZE
    assert adapter["prompt"]["target_size"] == {
        "mode": "exact",
        "width": 1440,
        "height": 1920,
        "aspect_ratio": "3:4",
    }
    assert adapter["prompt"]["requested_page_count"] == 8


def test_domestic_adapter_normalizes_pdd_to_pinduoduo() -> None:
    delegate = RecordingDelegate()
    provider = DomesticDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(
        provider.execute(
            _request(
                "detail_pack",
                {
                    "job": {
                        "settings": {
                            "market_region": "domestic",
                            "platform_code": "pdd",
                        }
                    },
                    "item": {"item_key": "page-001"},
                },
            )
        )
    )

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert len(delegate.requests) == 1
    adapter = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert adapter["prompt"]["platform_code"] == "pinduoduo"


@pytest.mark.parametrize("platform_code", ["ozon", "wildberries"])
def test_crossborder_russian_platform_prompt_defaults(platform_code: str) -> None:
    delegate = RecordingDelegate()
    provider = CrossborderDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    request = _request(
        "detail_pack",
        {
            "job": {
                "market_region": "international",
                "platform_code": platform_code,
            }
        },
    )

    result = asyncio.run(provider.execute(request))

    assert result is delegate.result
    prompt = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY][
        "prompt"
    ]
    assert prompt["language"] == "ru"
    assert prompt["aspect_ratio"] == "3:4"
    assert prompt["target_size"] == {
        "mode": "exact",
        "width": 900,
        "height": 1200,
        "aspect_ratio": "3:4",
    }
    assert prompt["requested_page_count"] == 8
    assert prompt["continue_after_single_page_failure"] is True


def test_crossborder_generic_prompt_uses_2k_english_defaults() -> None:
    delegate = RecordingDelegate()
    provider = CrossborderDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(
        provider.execute(
            _request(
                "detail_pack",
                {
                    "job": {
                        "market_region": "international",
                        "platform_code": "amazon",
                    }
                },
            )
        )
    )

    assert result is delegate.result
    prompt = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY][
        "prompt"
    ]
    assert prompt["platform_code"] == "amazon"
    assert prompt["language"] == "en"
    assert prompt["aspect_ratio"] == "16:9"
    assert prompt["target_size"] == {
        "mode": "quality_tier",
        "quality": "2K",
        "aspect_ratio": "16:9",
    }
    assert prompt["resolved_size"] == "2048x1152"
    assert prompt["output_format"] == "png"
    delivery = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY][
        "delivery"
    ]
    assert delivery["expected_dimensions"] == {"width": 2048, "height": 1152}
    assert delivery["content_type"] == "image/png"
    assert delivery["qa_status_required"] is False
    assert delivery["manual_review_required"] is True
    assert delivery["qa_mode"] == "disclosure_and_selection"


def test_detail_adapter_rejects_concrete_output_contract_drift() -> None:
    payload = b"detail-output"
    digest = hashlib.sha256(payload).hexdigest()
    delegate = RecordingDelegate(
        ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=ProviderArtifact(
                payload=payload,
                filename="result.png",
                content_type="image/png",
                width=900,
                height=900,
                sha256=digest,
            ),
        )
    )
    provider = CrossborderDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    result = asyncio.run(
        provider.execute(
            _request(
                "detail_pack",
                {"job": {"market_region": "international", "platform_code": "amazon"}},
            )
        )
    )
    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "DETAIL_OUTPUT_DIMENSIONS_INVALID"


def test_domestic_rejects_explicit_non_three_by_four_size() -> None:
    delegate = RecordingDelegate()
    provider = DomesticDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(
        provider.execute(
            _request(
                "detail_pack",
                {
                    "job": {
                        "market_region": "domestic",
                        "platform_code": "tmall",
                        "size": "1600x1600",
                    }
                },
            )
        )
    )

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "DETAIL_SIZE_UNSUPPORTED"
    assert delegate.requests == []


@pytest.mark.parametrize(
    ("provider_factory", "configuration", "expected_code"),
    [
        (
            DomesticDetailProvider,
            {"job": {"market_region": "international"}},
            "DETAIL_MARKET_REGION_MISMATCH",
        ),
        (
            CrossborderDetailProvider,
            {"job": {"market_region": "domestic"}},
            "DETAIL_MARKET_REGION_MISMATCH",
        ),
    ],
)
def test_detail_adapters_reject_wrong_market_route(
    provider_factory: type[DomesticDetailProvider] | type[CrossborderDetailProvider],
    configuration: dict[str, object],
    expected_code: str,
) -> None:
    delegate = RecordingDelegate()
    provider = provider_factory(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(provider.execute(_request("detail_pack", configuration)))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == expected_code
    assert result.artifact is None
    assert delegate.requests == []


def test_detail_adapter_rejects_operation_mismatch() -> None:
    delegate = RecordingDelegate()
    validator = RecordingManifestValidator()
    provider = DomesticDetailProvider(delegate, manifest_validator=validator)

    result = asyncio.run(
        provider.execute(_request("local_edit", {"job": {"market_region": "domestic"}}))
    )

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "OPERATION_NOT_SUPPORTED"
    assert validator.calls == [DOMESTIC_DETAIL_SKILL_ID]
    assert delegate.requests == []


def test_manifest_failure_is_fail_closed_and_rechecked_per_call() -> None:
    delegate = RecordingDelegate()
    validator = RecordingManifestValidator(
        error=SkillRuntimeError(
            "SKILL_CONTENT_HASH_MISMATCH",
            "simulated source drift",
        )
    )
    provider = CrossborderDetailProvider(
        delegate,
        manifest_validator=validator,
    )
    request = _request(
        "detail_pack",
        {"job": {"market_region": "international"}},
    )

    first = asyncio.run(provider.execute(request))
    second = asyncio.run(provider.execute(request))

    assert first.outcome == ProviderOutcome.FAILED
    assert first.error_code == "SKILL_CONTENT_HASH_MISMATCH"
    assert second.error_code == "SKILL_CONTENT_HASH_MISMATCH"
    assert validator.calls == [
        CROSSBORDER_DETAIL_SKILL_ID,
        CROSSBORDER_DETAIL_SKILL_ID,
    ]
    assert delegate.requests == []


@pytest.mark.parametrize(
    "outcome",
    [ProviderOutcome.FAILED, ProviderOutcome.OUTCOME_UNKNOWN],
)
def test_detail_delegate_failure_and_unknown_are_passthrough(
    outcome: ProviderOutcome,
) -> None:
    delegated_result = ProviderResult(
        outcome=outcome,
        error_code="DELEGATE_RESULT",
        error_message="delegate controls the outcome",
        retryable=True,
    )
    delegate = RecordingDelegate(delegated_result)
    provider = DomesticDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(
        provider.execute(
            _request("detail_pack", {"job": {"market_region": "domestic"}})
        )
    )

    assert result is delegated_result
    assert result.artifact is None


def test_amazon_inventory_and_dimension_mapping_are_canonical() -> None:
    assert AMAZON_EXPECTED_ITEM_COUNT == 21
    assert len(AMAZON_ITEM_KEYS) == 21
    assert (
        "MAIN-01",
        *(f"SECONDARY-{index:02d}" for index in range(1, 7)),
        *(f"A-DESKTOP-{index:02d}" for index in range(1, 8)),
        *(f"A-MOBILE-{index:02d}" for index in range(1, 8)),
    ) == AMAZON_ITEM_KEYS
    assert validate_amazon_item_keys(AMAZON_ITEM_KEYS) == AMAZON_ITEM_KEYS
    assert amazon_item_spec("MAIN-01").width == 2000
    assert amazon_item_spec("MAIN-01").height == 2000
    secondary = amazon_item_spec("SECONDARY-06")
    desktop = amazon_item_spec("A-DESKTOP-07")
    mobile = amazon_item_spec("A-MOBILE-07")
    assert (secondary.width, secondary.height) == (1200, 1600)
    assert (desktop.width, desktop.height) == (1464, 600)
    assert (mobile.width, mobile.height) == (1600, 1200)
    assert len(AMAZON_ITEM_SPECS) == AMAZON_EXPECTED_ITEM_COUNT
    assert not hasattr(amazon_item_spec("SECONDARY-01"), "selling_point")
    assert not hasattr(amazon_item_spec("SECONDARY-01"), "layout_direction")
    assert not hasattr(amazon_item_spec("SECONDARY-01"), "source_material")


def test_amazon_adapter_attaches_item_contract_and_preserves_result() -> None:
    delegated_result = ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    delegate = StrictRecordingDelegate(delegated_result)
    validator = RecordingManifestValidator()
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=validator,
    )

    result = asyncio.run(provider.execute(_amazon_request("A-DESKTOP-03", 1464, 600)))

    assert result is delegated_result
    assert validator.calls == ["xinghe-amazon-visual-suite"]
    adapter = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert adapter["schema_version"] == SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION
    assert adapter["skill_id"] == "xinghe-amazon-visual-suite"
    assert adapter["skill_content_hash"] == "a" * 64
    assert adapter["operation"] == "amazon_visual_suite"
    assert adapter["item_key"] == "A-DESKTOP-03"
    assert adapter["item_ordinal"] == AMAZON_ITEM_KEYS.index("A-DESKTOP-03")
    assert adapter["source_assets"] == [
        {"asset_id": "asset-white", "role": "white_background_product"},
        {"asset_id": "asset-detail", "role": "product_detail"},
    ]
    assert adapter["target"] == {
        "aspect_ratio": "61:25",
        "width": 1464,
        "height": 600,
        "quality": "high",
        "output_format": "jpeg",
        "fit": "cover",
    }
    prompt = adapter["prompt_input"]
    assert prompt["marketplace"] == "amazon-us"
    assert prompt["product_name"] == "Test product"
    assert prompt["product_information"] == ["Test listing", "Point one"]
    assert prompt["color_palette"] == ["navy"]
    assert prompt["language"] == "en-US"
    assert prompt["composition"] == "a_desktop"
    assert prompt["image_count"] == 1
    assert prompt["creative_direction"] == [
        "Asset type and final role: Desktop A+ module",
        "Final size: 1464x600",
        "Layout direction: Planner-selected layout direction.",
        (
            "Visual proof method: Mechanism, structure, technology, craft, "
            "or detail evidence"
        ),
    ]
    assert prompt["copy_requirements"] == [
        "Headline: Planner-selected headline.",
        "Subcopy: Planner-selected support copy.",
    ]
    guidance = prompt["reference_guidance"]
    for prefix in (
        "Product Master Description: ",
        "Product Image Analysis: ",
        "Information Confidence: ",
        "Buyer Demand Map: ",
        "Demand-to-Selling-Point Match: ",
        "Campaign Style Lock: ",
        "Product Consistency Lock: ",
        "Layout Diversity Lock: ",
    ):
        assert any(value.startswith(prefix) for value in guidance)
    assert "Buyer question: What makes it different?" in guidance
    assert len(prompt["negative_constraints"]) == 2
    assert "private_note" not in prompt
    audit = delegate.requests[0].configuration[AMAZON_AUDIT_CONFIGURATION_KEY]
    assert audit["expected_item_count"] == 21
    assert tuple(audit["canonical_item_keys"]) == AMAZON_ITEM_KEYS
    assert audit["item"]["task"]["id"] == "aplus-desktop-03"
    assert audit["item"]["plan_resume_signature"] == "b" * 64


def test_amazon_main_uses_all_product_truth_sources_without_optional_references() -> (
    None
):
    delegate = StrictRecordingDelegate(
        ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    request = _amazon_request("MAIN-01", 2000, 2000)
    screen = request.configuration["item"][AMAZON_PLAN_CONFIGURATION_KEY]["screen"]
    screen["headline"] = ""
    screen["subcopy"] = ""
    request.configuration["job"]["input"]["white_background_product_asset_ids"] = [
        "asset-white",
        "asset-white-side",
    ]
    request.configuration["job"]["input"]["detail_asset_ids"] = [
        "asset-detail",
        "asset-detail-close",
    ]

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    envelope = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert envelope["source_assets"] == [
        {"asset_id": "asset-white", "role": "white_background_product"},
        {"asset_id": "asset-white-side", "role": "white_background_product"},
        {"asset_id": "asset-detail", "role": "product_detail"},
        {"asset_id": "asset-detail-close", "role": "product_detail"},
    ]
    prompt = envelope["prompt_input"]
    assert "copy_requirements" not in prompt
    constraints = prompt["negative_constraints"]
    assert any("authentic trademark, logo, label" in value for value in constraints)
    assert any("surrounding background to pure white" in value for value in constraints)
    assert any("empty or pure-color canvas" in value for value in constraints)
    assert any("Do not add lifestyle context" in value for value in constraints)


def test_amazon_non_main_uses_only_planner_requested_optional_source_roles() -> None:
    delegate = StrictRecordingDelegate(
        ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    request = _amazon_request("A-DESKTOP-06", 1464, 600)
    screen = request.configuration["item"][AMAZON_PLAN_CONFIGURATION_KEY]["screen"]
    screen["source_material"] = (
        "white_background_product, product_detail, package_reference, "
        "style_reference, and competitor_reference"
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    envelope = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert envelope["source_assets"] == [
        {"asset_id": "asset-white", "role": "white_background_product"},
        {"asset_id": "asset-detail", "role": "product_detail"},
        {"asset_id": "asset-package", "role": "package_reference"},
        {"asset_id": "asset-style", "role": "style_reference"},
        {"asset_id": "asset-competitor", "role": "competitor_reference"},
    ]


def test_amazon_page_prompt_receives_complete_commercial_input() -> None:
    delegate = StrictRecordingDelegate(
        ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    request = _amazon_request("SECONDARY-01", 1200, 1600)
    settings = request.configuration["job"]["settings"]
    settings.update(
        {
            "category": "commuter backpack",
            "target_buyer": "urban commuters",
            "listing_description": "Confirmed listing description.",
            "a_plus_copy": "Existing A+ copy.",
            "product_facts": ["Visible dual zipper"],
            "parameters": ["42 x 28 x 18 cm"],
            "certifications_or_evidence": ["Provided test report"],
            "forbidden_claims": ["waterproof rating"],
            "style_preferences": ["editorial", "technical"],
            "instruction": "Prioritize commute organization.",
        }
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    prompt = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY][
        "prompt_input"
    ]
    assert prompt["product_category"] == "commuter backpack"
    assert prompt["target_buyer"] == "urban commuters"
    assert prompt["listing_description"] == "Confirmed listing description."
    assert prompt["a_plus_copy"] == "Existing A+ copy."
    assert prompt["product_facts"] == ["Visible dual zipper"]
    assert prompt["parameters"] == ["42 x 28 x 18 cm"]
    assert prompt["certifications_or_evidence"] == ["Provided test report"]
    assert prompt["forbidden_claims"] == ["waterproof rating"]
    assert prompt["style_preferences"] == ["editorial", "technical"]
    assert prompt["user_instruction"] == "Prioritize commute organization."
    assert "Forbidden claim: waterproof rating" in prompt["negative_constraints"]


def test_amazon_long_skill_guidance_is_chunked_without_content_loss() -> None:
    delegate = StrictRecordingDelegate(
        ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    request = _amazon_request("SECONDARY-01", 1200, 1600)
    plan_context = request.configuration["item"][AMAZON_PLAN_CONFIGURATION_KEY]
    product_master = plan_context["analysis"]["product_master_description"]
    product_master["category_and_identity"] = "one-sku-" + ("x" * 2_100)

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    prompt = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY][
        "prompt_input"
    ]
    guidance = prompt["reference_guidance"]
    assert all(len(value) <= 2_000 for value in guidance)
    parts = [
        value
        for value in guidance
        if value.startswith("Product Master Description (part ")
    ]
    assert len(parts) == 2
    payload_parts: list[str] = []
    for ordinal, value in enumerate(parts, start=1):
        prefix = f"Product Master Description (part {ordinal}/{len(parts)}): "
        assert value.startswith(prefix)
        payload_parts.append(value[len(prefix) :])
    assert "".join(payload_parts) == json.dumps(
        product_master,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert any(value.startswith("Campaign Style Lock: ") for value in guidance)


def test_amazon_provider_does_not_require_removed_legacy_plan_fields() -> None:
    delegate = StrictRecordingDelegate(
        ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    spec = amazon_item_spec("A-DESKTOP-03")
    request = _amazon_request(spec.item_key, spec.width, spec.height)

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    prompt = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY][
        "prompt_input"
    ]
    assert not any(
        value.startswith("Suite Cohesion Lock: ")
        for value in prompt["reference_guidance"]
    )
    assert not any(
        "Responsive concept" in value for value in prompt["creative_direction"]
    )


def test_amazon_all_21_items_have_independent_authoritative_task_contracts() -> None:
    delegate = StrictRecordingDelegate(
        ProviderResult(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    for spec in AMAZON_ITEM_SPECS:
        asyncio.run(
            provider.execute(_amazon_request(spec.item_key, spec.width, spec.height))
        )

    required_task_fields = {
        "id",
        "asset_type",
        "final_size",
        "buyer_question",
        "selling_point",
        "information_confidence",
        "visual_proof",
        "headline",
        "subcopy",
        "layout_direction",
        "source_material",
        "risk_note",
    }
    required_guidance_prefixes = (
        "Product Master Description",
        "Product Image Analysis",
        "Information Confidence",
        "Buyer Demand Map",
        "Demand-to-Selling-Point Match",
        "Campaign Style Lock",
        "Product Consistency Lock",
        "Layout Diversity Lock",
    )
    removed_legacy_terms = (
        "Design Strength Lock",
        "Suite Cohesion Lock",
        "Responsive concept",
        "layout_family",
        "camera_angle",
        "product_position",
        "background_system",
        "prop_strategy",
        "copy_density",
    )
    task_rows: list[tuple[tuple[str, str], ...]] = []
    skill_row_ids: set[str] = set()
    for ordinal, (spec, request) in enumerate(
        zip(AMAZON_ITEM_SPECS, delegate.requests, strict=True)
    ):
        adapter = request.configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
        audit = request.configuration[AMAZON_AUDIT_CONFIGURATION_KEY]
        task = audit["item"]["task"]
        prompt = adapter["prompt_input"]

        assert adapter["item_key"] == AMAZON_ITEM_KEYS[ordinal] == spec.item_key
        assert adapter["item_ordinal"] == ordinal
        assert adapter["target"]["width"] == spec.width
        assert adapter["target"]["height"] == spec.height
        assert adapter["target"]["output_format"] == "jpeg"
        assert adapter["target"]["fit"] == ("pad" if spec.group == "main" else "cover")
        assert set(task) == required_task_fields
        assert task["id"] == AMAZON_SKILL_ROW_ID_BY_ITEM_KEY[spec.item_key]
        assert task["final_size"] == f"{spec.width}x{spec.height}"
        assert all(isinstance(value, str) and value for value in task.values())
        assert prompt["product_information"] == ["Test listing", "Point one"]
        assert prompt["creative_direction"] == [
            f"Asset type and final role: {task['asset_type']}",
            f"Final size: {task['final_size']}",
            f"Layout direction: {task['layout_direction']}",
            f"Visual proof method: {task['visual_proof']}",
        ]
        if spec.item_key == "MAIN-01":
            assert "copy_requirements" not in prompt
        else:
            assert prompt["copy_requirements"] == [
                f"Headline: {task['headline']}",
                f"Subcopy: {task['subcopy']}",
            ]
        assert prompt["negative_constraints"][1] == task["risk_note"]
        for prefix in required_guidance_prefixes:
            assert any(
                value.startswith(f"{prefix}: ") or value.startswith(f"{prefix} (part ")
                for value in prompt["reference_guidance"]
            )
        assert (
            f"Buyer question: {task['buyer_question']}" in prompt["reference_guidance"]
        )
        assert f"Selling point: {task['selling_point']}" in prompt["reference_guidance"]
        assert (
            f"Information confidence: {task['information_confidence']}"
            in prompt["reference_guidance"]
        )
        assert (
            f"Required source material: {task['source_material']}"
            in prompt["reference_guidance"]
        )
        serialized_prompt = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
        assert not any(term in serialized_prompt for term in removed_legacy_terms)
        task_rows.append(tuple(sorted(task.items())))
        skill_row_ids.add(task["id"])

    assert len(delegate.requests) == AMAZON_EXPECTED_ITEM_COUNT
    assert len(set(task_rows)) == AMAZON_EXPECTED_ITEM_COUNT
    assert skill_row_ids == set(AMAZON_SKILL_ROW_ID_BY_ITEM_KEY.values())


@pytest.mark.parametrize(
    ("provider_request", "expected_code"),
    [
        (
            _amazon_request("CONTACT-SHEET", 2000, 2000),
            "AMAZON_ITEM_KEY_INVALID",
        ),
        (
            _amazon_request(
                "MAIN-01",
                2000,
                2000,
                contact_sheet=True,
            ),
            "AMAZON_CONTACT_SHEET_NOT_DELIVERABLE",
        ),
        (
            _amazon_request("SECONDARY-01", 2000, 2000),
            "AMAZON_TARGET_SIZE_MISMATCH",
        ),
        (
            _request(
                "amazon_visual_suite",
                {"item": {"item_key": "MAIN-01"}},
            ),
            "AMAZON_TARGET_SIZE_MISSING",
        ),
    ],
)
def test_amazon_invalid_key_contact_sheet_and_size_are_rejected(
    provider_request: ProviderRequest,
    expected_code: str,
) -> None:
    delegate = RecordingDelegate()
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(provider.execute(provider_request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == expected_code
    assert result.artifact is None
    assert delegate.requests == []


def test_amazon_non_amazon_marketplace_is_rejected_before_delegate() -> None:
    delegate = RecordingDelegate()
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(
        provider.execute(_amazon_request("MAIN-01", 2000, 2000, marketplace="tmall"))
    )

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "AMAZON_MARKETPLACE_UNSUPPORTED"
    assert result.artifact is None
    assert delegate.requests == []


def test_amazon_operation_mismatch_is_rejected() -> None:
    delegate = RecordingDelegate()
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(
        provider.execute(
            _amazon_request("MAIN-01", 2000, 2000, operation="detail_pack")
        )
    )

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "OPERATION_NOT_SUPPORTED"
    assert delegate.requests == []


def test_amazon_output_dimension_mismatch_fails_without_placeholder() -> None:
    delegate = RecordingDelegate(
        ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=_artifact(1200, 1200),
        )
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(provider.execute(_amazon_request("MAIN-01", 2000, 2000)))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "AMAZON_OUTPUT_DIMENSIONS_INVALID"
    assert result.artifact is None


def test_amazon_exact_size_png_fails_amazon_ready_format_contract() -> None:
    delegate = RecordingDelegate(
        ProviderResult(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=_artifact(2000, 2000),
        )
    )
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(provider.execute(_amazon_request("MAIN-01", 2000, 2000)))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "AMAZON_OUTPUT_FORMAT_INVALID"
    assert result.retryable is False
    assert result.artifact is None


def test_amazon_exact_size_jpeg_is_preserved_as_final_deliverable() -> None:
    artifact = _jpeg_artifact(1600, 1200)
    delegated_result = ProviderResult(
        outcome=ProviderOutcome.SUCCEEDED,
        artifact=artifact,
    )
    delegate = RecordingDelegate(delegated_result)
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(provider.execute(_amazon_request("A-MOBILE-07", 1600, 1200)))

    assert result is delegated_result
    assert result.artifact is artifact
    assert result.artifact.content_type == "image/jpeg"


def test_amazon_success_without_artifact_fails_closed() -> None:
    delegate = RecordingDelegate(ProviderResult(outcome=ProviderOutcome.SUCCEEDED))
    provider = AmazonVisualSuiteProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )

    result = asyncio.run(provider.execute(_amazon_request("MAIN-01", 2000, 2000)))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "AMAZON_OUTPUT_ARTIFACT_MISSING"
    assert result.retryable is False
    assert result.artifact is None
    assert result.persisted_artifact is None
    assert len(delegate.requests) == 1


def test_amazon_inventory_rejects_contact_sheet_or_partial_list() -> None:
    with pytest.raises(AdapterValidationError) as exc_info:
        validate_amazon_item_keys((*AMAZON_ITEM_KEYS[:-1], "CONTACT-SHEET"))

    assert exc_info.value.code == "AMAZON_ITEM_INVENTORY_INVALID"


def test_domestic_detail_accepts_execution_coordinator_envelope() -> None:
    delegate = RecordingDelegate()
    provider = DomesticDetailProvider(
        delegate,
        manifest_validator=RecordingManifestValidator(),
    )
    request = _request(
        "detail_pack",
        {
            "job": {
                "input": {"product_groups": [{"group_key": "main"}]},
                "settings": {
                    "market_region": "domestic",
                    "platform_code": "jd",
                    "page_count": 8,
                    "language": "zh-CN",
                },
            },
            "item": {"page": 1},
        },
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert len(delegate.requests) == 1
    adapter = delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY]
    assert adapter["market_region"] == "domestic"
    assert adapter["prompt"]["platform_code"] == "jd"
    assert adapter["prompt"]["requested_page_count"] == 8
