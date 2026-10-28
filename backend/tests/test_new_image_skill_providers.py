# ruff: noqa: E501
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.creative_image_provider import CreativeImageProvider
from app.new_detail_skill_provider import SKILL_ADAPTER_CONFIGURATION_KEY
from app.new_image_skill_provider import (
    SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION,
    NewImageSkillProvider,
)
from app.product_image_provider import PRODUCT_IMAGE_PRESETS, ProductImageProvider
from app.provider import (
    ProviderArtifact,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from app.reference_remix_provider import (
    ReferenceRemixProvider,
    ReferenceVisualReverseRemixProvider,
)
from app.skill_protocol import load_skill_protocol
from app.xinghe_single_image_provider import (
    DEFAULT_ALLOWED_PROMPT_FIELDS,
    XingheSingleImageProvider,
)


@dataclass
class ManifestSpy:
    calls: list[str] = field(default_factory=list)
    error: Exception | None = None

    def __call__(self, skill_id: str) -> str:
        self.calls.append(skill_id)
        if self.error is not None:
            raise self.error
        return "a" * 64


class StubDelegate:
    name = "stub-image-provider"
    model = "stub-image-model"
    pricing_version = "test-only"

    def __init__(self, result: ProviderResult) -> None:
        self.result = result
        self.requests: list[ProviderRequest] = []

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        _assert_strict_single_image_envelope(request)
        self.requests.append(request)
        return self.result


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
    identifier = re.compile(r"[a-z][a-z0-9_-]{0,63}")
    assert envelope["schema_version"] == SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION
    assert re.fullmatch(r"[0-9a-fA-F]{64}", envelope["skill_content_hash"])
    assert identifier.fullmatch(envelope["item_key"])
    assert isinstance(envelope["item_ordinal"], int)
    assert 1 <= len(envelope["source_assets"]) <= 8
    assert all(
        set(source) == {"asset_id", "role"}
        and identifier.fullmatch(source["asset_id"])
        and identifier.fullmatch(source["role"])
        for source in envelope["source_assets"]
    )
    target = envelope["target"]
    assert set(target) == {
        "aspect_ratio",
        "width",
        "height",
        "quality",
        "output_format",
        "fit",
    }
    if target["aspect_ratio"] == "follow-reference":
        assert target["width"] is None
        assert target["height"] is None
        assert target["fit"] == "contain"
    else:
        assert isinstance(target["width"], int) and target["width"] > 0
        assert isinstance(target["height"], int) and target["height"] > 0
        divisor = math.gcd(target["width"], target["height"])
        assert target["aspect_ratio"] == (
            f"{target['width'] // divisor}:{target['height'] // divisor}"
        )
    assert target["fit"] in {"cover", "contain", "pad"}
    assert set(envelope["prompt_input"]) <= DEFAULT_ALLOWED_PROMPT_FIELDS
    assert all(value not in ("", []) for value in envelope["prompt_input"].values())


def _artifact_result(*, raw_usage: dict[str, Any] | None = None) -> ProviderResult:
    payload = b"independent-image-payload"
    return ProviderResult(
        outcome=ProviderOutcome.SUCCEEDED,
        provider_request_id="provider-request-1",
        usage_reported=True,
        raw_usage=raw_usage or {"output_images": 1},
        artifact=ProviderArtifact(
            payload=payload,
            filename="image-001.png",
            content_type="image/png",
            width=1024,
            height=1024,
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
        provider_name="stub-image-provider",
        model_name="stub-image-model",
        pricing_version="test-only",
    )


def _request(
    operation: str,
    *,
    input_config: dict[str, Any],
    settings: dict[str, Any],
) -> ProviderRequest:
    return ProviderRequest(
        workspace_id="ws-1",
        job_id="job-1",
        job_item_id="item-1",
        attempt_id="attempt-1",
        operation=operation,
        idempotency_key="idem-1",
        configuration={
            "job": {"input": input_config, "settings": settings},
            "item": {"item_key": "image-001", "ordinal": 0},
        },
    )


def test_reference_remix_compiles_canonical_prompt_with_skill_protocol() -> None:
    """Reference remix keeps the task prompt and verified Skill protocol together."""

    provider = XingheSingleImageProvider(
        session_factory=None,  # type: ignore[arg-type]
        gateway=type(
            "Gateway",
            (),
            {
                "provider_name": "test",
                "image_model": "gpt-image-2",
                "pricing_version": "test",
            },
        )(),
        storage=None,  # type: ignore[arg-type]
        operation="reference_remix",
        skill_id="reference-visual-reverse-remix",
        manifest_validator=lambda _: "a" * 64,
    )
    protocol = load_skill_protocol(
        "reference-visual-reverse-remix", source_content_hash="a" * 64
    )
    canonical = (
        "[Product Identity Lock] product image is the only identity authority.\n"
        "[Visual Strategy Lock] transfer composition and lighting only.\n"
        "[Original Remix Prompt] Create one original ecommerce image.\n"
        "[Forbidden Transfer] no source brand, logo, watermark, or exact copy."
    )
    envelope = type(
        "Envelope",
        (),
        {
            "skill_id": "reference-visual-reverse-remix",
            "operation": "reference_remix",
            "item_key": "image-001",
            "item_ordinal": 1,
            "source_assets": (
                type(
                    "Source",
                    (),
                    {"asset_id": "asset-product", "role": "product_reference"},
                )(),
                type(
                    "Source", (), {"asset_id": "asset-style", "role": "style_reference"}
                )(),
            ),
            "target": type(
                "Target",
                (),
                {
                    "aspect_ratio": "4:5",
                    "fit": "contain",
                    "height": 1280,
                    "output_format": "png",
                    "quality": "auto",
                    "width": 1024,
                },
            )(),
            "prompt_input": {"original_remix_prompt": canonical},
        },
    )()

    compiled = provider._compile_prompt(envelope, protocol)
    payload = json.loads(compiled)
    assert payload["instructions"]["original_remix_prompt"] == canonical
    assert payload["skill_protocol"]["skill_id"] == "reference-visual-reverse-remix"
    assert payload["source_images"] == [
        {"asset_id": "asset-product", "ordinal": 1, "role": "product_reference"},
        {"asset_id": "asset-style", "ordinal": 2, "role": "style_reference"},
    ]


@pytest.mark.parametrize("preset", sorted(PRODUCT_IMAGE_PRESETS))
def test_product_image_supports_all_eight_presets_and_delegates(preset: str) -> None:
    expected = _artifact_result()
    delegate = StubDelegate(expected)
    validator = ManifestSpy()
    provider = ProductImageProvider(delegate, manifest_validator=validator)
    settings: dict[str, Any] = {"task_type": preset, "image_count": 1}
    if preset == "change_color":
        settings["target_color"] = "海军蓝"
    request = _request(
        "product_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings=settings,
    )

    result = asyncio.run(provider.execute(request))

    assert result is expected
    assert len(delegate.requests) == 1
    assert validator.calls == ["xinghe-wanneng-shengtu-3-0"]


def test_manifest_validator_runs_before_every_delegate_call() -> None:
    delegate = StubDelegate(_artifact_result())
    validator = ManifestSpy()
    provider = CreativeImageProvider(delegate, manifest_validator=validator)
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"image_count": 4},
    )

    asyncio.run(provider.execute(request))
    asyncio.run(provider.execute(request))

    assert validator.calls == [
        "xinghe-ecommerce-creative-image",
        "xinghe-ecommerce-creative-image",
    ]
    assert len(delegate.requests) == 2


def test_creative_instruction_is_sent_as_user_instruction_not_negative_constraint() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"instruction": "突出轻量便携", "image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    prompt_input = _adapter_request(delegate)["prompt_input"]
    assert prompt_input["user_instruction"] == "突出轻量便携"
    assert "negative_constraints" not in prompt_input


def test_product_change_color_rejects_missing_target_color() -> None:
    delegate = StubDelegate(_artifact_result())
    validator = ManifestSpy()
    provider = ProductImageProvider(delegate, manifest_validator=validator)
    request = _request(
        "product_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"task_type": "change_color", "image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "TARGET_COLOR_REQUIRED"
    assert delegate.requests == []
    assert validator.calls == []


@pytest.mark.parametrize(
    ("input_config", "missing_key"),
    [
        ({"reference_asset_ids": ["asset-style"]}, "product_asset_ids"),
        ({"product_asset_ids": ["asset-product"]}, "reference_asset_ids"),
    ],
)
def test_reference_remix_requires_product_and_style_clues(
    input_config: dict[str, Any],
    missing_key: str,
) -> None:
    delegate = StubDelegate(_artifact_result())
    validator = ManifestSpy()
    provider = ReferenceRemixProvider(delegate, manifest_validator=validator)
    request = _request(
        "reference_remix",
        input_config=input_config,
        settings={"image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "REQUIRED_IMAGE_INPUT_MISSING"
    assert missing_key in (result.error_message or "")
    assert delegate.requests == []
    assert validator.calls == []


@pytest.mark.parametrize("image_count", [0, 13, True, "12"])
def test_creative_image_rejects_out_of_range_image_count(
    image_count: object,
) -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"image_count": image_count},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "IMAGE_COUNT_OUT_OF_RANGE"
    assert delegate.requests == []


def test_delegate_failure_is_passed_through_unchanged() -> None:
    expected = ProviderResult(
        outcome=ProviderOutcome.FAILED,
        provider_request_id="provider-request-failed",
        error_code="UPSTREAM_FAILED",
        error_message="stub failure",
        usage_reported=True,
        raw_usage={"request_count": 1},
        retryable=True,
        provider_name="stub-image-provider",
    )
    delegate = StubDelegate(expected)
    provider = ReferenceRemixProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "reference_remix",
        input_config={
            "product_asset_ids": ["asset-product"],
            "reference_asset_ids": ["asset-style"],
        },
        settings={"image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result is expected
    assert len(delegate.requests) == 1


def test_operation_mismatch_fails_before_manifest_or_delegate() -> None:
    delegate = StubDelegate(_artifact_result())
    validator = ManifestSpy()
    provider = ProductImageProvider(delegate, manifest_validator=validator)
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"task_type": "scene_main", "image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "OPERATION_NOT_SUPPORTED"
    assert delegate.requests == []
    assert validator.calls == []


def test_missing_manifest_validator_fails_closed() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate)
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"image_count": 4},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "SKILL_MANIFEST_VALIDATOR_REQUIRED"
    assert delegate.requests == []


def test_manifest_validation_failure_does_not_call_delegate() -> None:
    delegate = StubDelegate(_artifact_result())
    validator = ManifestSpy(error=RuntimeError("private validator details"))
    provider = CreativeImageProvider(delegate, manifest_validator=validator)
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"image_count": 4},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "SKILL_MANIFEST_VALIDATION_FAILED"
    assert "private validator details" not in (result.error_message or "")
    assert delegate.requests == []


@pytest.mark.parametrize(
    "delegate_result",
    [
        ProviderResult(outcome=ProviderOutcome.SUCCEEDED),
        _artifact_result(raw_usage={"placeholder": True}),
        _artifact_result(raw_usage={"artifact_kind": "contact_sheet"}),
    ],
)
def test_success_requires_a_real_independent_image_artifact(
    delegate_result: ProviderResult,
) -> None:
    delegate = StubDelegate(delegate_result)
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"image_count": 4},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "GENERATED_IMAGE_ARTIFACT_REQUIRED"
    assert len(delegate.requests) == 1


def _adapter_request(delegate: StubDelegate) -> dict[str, Any]:
    assert len(delegate.requests) == 1
    return delegate.requests[0].configuration[SKILL_ADAPTER_CONFIGURATION_KEY]


def test_product_adapter_builds_controlled_envelope_and_role_inventory() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = ProductImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "product_image",
        input_config={
            "product_asset_ids": ["asset-product-1", "asset-product-2"],
            "reference_asset_ids": ["asset-style"],
            "untrusted_input": "drop-me",
        },
        settings={
            "task_type": "change_color",
            "target_color": "navy",
            "platform_code": "tmall",
            "aspect_ratio": "3:4",
            "image_count": 1,
            "instruction": "Keep the logo unchanged.",
            "quality": "high",
            "output_format": "png",
            "api_key": "drop-me",
        },
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    adapter = _adapter_request(delegate)
    assert adapter == {
        "schema_version": SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION,
        "skill_id": "xinghe-wanneng-shengtu-3-0",
        "skill_content_hash": "a" * 64,
        "operation": "product_image",
        "item_key": "image-001",
        "item_ordinal": 0,
        "source_assets": [
            {"asset_id": "asset-product-1", "role": "product_reference"},
            {"asset_id": "asset-product-2", "role": "product_reference"},
            {"asset_id": "asset-style", "role": "style_reference"},
        ],
        "target": {
            "aspect_ratio": "3:4",
            "width": 768,
            "height": 1024,
            "quality": "high",
            "output_format": "png",
            "fit": "cover",
        },
        "prompt_input": {
            "task_type": "change_color",
            "platform_code": "tmall",
            "reference_guidance": "Keep the logo unchanged.",
            "target_color": "navy",
        },
    }


def test_reference_remix_adapter_never_confuses_product_and_style_roles() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = ReferenceRemixProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "reference_remix",
        input_config={
            "product_asset_ids": ["asset-product"],
            "reference_asset_ids": ["asset-reference"],
        },
        settings={
            "image_count": 1,
            "platform_code": "amazon-us",
            "language": "en-US",
            "selling_points": ["Durable", "Compact"],
            "instruction": "Follow layout only.",
            "unknown_prompt_field": "drop-me",
        },
    )

    asyncio.run(provider.execute(request))

    adapter = _adapter_request(delegate)
    assert adapter["source_assets"] == [
        {"asset_id": "asset-product", "role": "product_reference"},
        {"asset_id": "asset-reference", "role": "style_reference"},
    ]
    assert set(adapter["prompt_input"]) == {
        "platform_code",
        "language",
        "product_information",
        "reference_guidance",
    }
    assert "unknown_prompt_field" not in adapter["prompt_input"]


def test_creative_adapter_uses_competitor_reference_role_and_whitelist() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "creative_image",
        input_config={
            "product_asset_ids": ["asset-product"],
            "competitor_asset_ids": ["asset-competitor"],
        },
        settings={
            "image_count": 1,
            "creative_direction": "premium",
            "platform_code": "tmall",
            "language": "zh-CN",
            "selling_points": ["Premium finish"],
            "instruction": "No unverified claims.",
            "raw_provider_options": {"retry": 99},
        },
    )

    asyncio.run(provider.execute(request))

    adapter = _adapter_request(delegate)
    assert adapter["source_assets"] == [
        {"asset_id": "asset-product", "role": "product_reference"},
        {"asset_id": "asset-competitor", "role": "competitor_reference"},
    ]
    assert adapter["prompt_input"] == {
        "creative_direction": "premium",
        "platform_code": "tmall",
        "language": "zh-CN",
        "product_information": ["Premium finish"],
        "user_instruction": "No unverified claims.",
        "post_generation_review": "After generation, manually check product fidelity, SKU/packaging text, 3-second and mobile scan clarity, copy legibility, competitor leakage, and compliance. If a material defect is visible, use one explicit conservative retry; never enter an automatic redraw loop.",
        "planning_mode": "reference",
    }


def test_creative_competitor_reference_forces_reference_planning_even_without_flag() -> None:
    """Direct API callers cannot bypass the competitor-reference planner."""
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    result = asyncio.run(
        provider.execute(
            _request(
                "creative_image",
                input_config={
                    "product_asset_ids": ["asset-product"],
                    "competitor_asset_ids": ["asset-competitor"],
                },
                settings={"image_count": 1},
            )
        )
    )

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert _adapter_request(delegate)["prompt_input"]["planning_mode"] == "reference"


def test_inspiration_creative_brief_is_first_class_user_instruction() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={
            "image_count": 1,
            "inspiration_planning": True,
            "instruction": "标签必须写“好凉快！”，禁止出现数显面板。",
        },
    )

    asyncio.run(provider.execute(request))

    adapter = _adapter_request(delegate)
    assert (
        adapter["prompt_input"]["user_instruction"]
        == "标签必须写“好凉快！”，禁止出现数显面板。"
    )
    assert "negative_constraints" not in adapter["prompt_input"]


def test_inspiration_creative_allows_dynamic_reference_canvas() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "creative_image",
        input_config={
            "product_asset_ids": ["asset-product"],
            "competitor_asset_ids": ["asset-reference"],
        },
        settings={
            "image_count": 1,
            "aspect_ratio": "follow-reference",
            "inspiration_planning": True,
            "inspiration_reference_planning": True,
            "instruction": "Preserve the reference canvas.",
        },
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert _adapter_request(delegate)["target"] == {
        "aspect_ratio": "follow-reference",
        "width": None,
        "height": None,
        "quality": "high",
        "output_format": "png",
        "fit": "contain",
    }


@pytest.mark.parametrize(
    ("provider_class", "operation", "input_config", "extra_settings"),
    [
        (
            ProductImageProvider,
            "product_image",
            {"product_asset_ids": ["asset-product"]},
            {"task_type": "scene_main"},
        ),
        (
            ReferenceRemixProvider,
            "reference_remix",
            {
                "product_asset_ids": ["asset-product"],
                "reference_asset_ids": ["asset-reference"],
            },
            {},
        ),
        (
            CreativeImageProvider,
            "creative_image",
            {"product_asset_ids": ["asset-product"]},
            {},
        ),
    ],
)
def test_single_image_adapters_map_four_by_five_to_exact_delivery_dimensions(
    provider_class: type[NewImageSkillProvider],
    operation: str,
    input_config: dict[str, list[str]],
    extra_settings: dict[str, str],
) -> None:
    delegate = StubDelegate(_artifact_result())
    provider = provider_class(delegate, manifest_validator=ManifestSpy())
    request = _request(
        operation,
        input_config=input_config,
        settings={"aspect_ratio": "4:5", "image_count": 12, **extra_settings},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert _adapter_request(delegate)["target"] == {
        "aspect_ratio": "4:5",
        "width": 1024,
        "height": 1280,
        "quality": "high",
        "output_format": "png",
        "fit": "cover",
    }


def test_reference_remix_defaults_to_follow_reference_wire_contract() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = ReferenceRemixProvider(delegate, manifest_validator=ManifestSpy())

    result = asyncio.run(
        provider.execute(
            _request(
                "reference_remix",
                input_config={
                    "product_asset_ids": ["asset-product"],
                    "reference_asset_ids": ["asset-reference"],
                },
                settings={"image_count": 1},
            )
        )
    )

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert _adapter_request(delegate)["target"] == {
        "aspect_ratio": "follow-reference",
        "width": None,
        "height": None,
        "quality": "high",
        "output_format": "png",
        "fit": "contain",
    }


@pytest.mark.parametrize(
    ("settings", "error_code"),
    [
        ({"image_count": 1, "execution_mode": "unknown"}, "EXECUTION_MODE_INVALID"),
        (
            {"image_count": 1, "inspiration_product_first": "yes"},
            "INSPIRATION_PRODUCT_FIRST_INVALID",
        ),
    ],
)
def test_reference_contract_rejections_are_stable_results(
    settings: dict[str, Any], error_code: str
) -> None:
    delegate = StubDelegate(_artifact_result())
    provider = ReferenceRemixProvider(delegate, manifest_validator=ManifestSpy())

    result = asyncio.run(
        provider.execute(
            _request(
                "reference_remix",
                input_config={
                    "product_asset_ids": ["asset-product"],
                    "reference_asset_ids": ["asset-reference"],
                },
                settings=settings,
            )
        )
    )

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == error_code
    assert delegate.requests == []


def test_creative_contract_accepts_explicit_false_inspiration_flag() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = CreativeImageProvider(delegate, manifest_validator=ManifestSpy())

    result = asyncio.run(
        provider.execute(
            _request(
                "creative_image",
                input_config={"product_asset_ids": ["asset-product"]},
                settings={
                    "image_count": 1,
                    "inspiration_planning": False,
                    "inspiration_reference_planning": False,
                },
            )
        )
    )

    assert result.outcome == ProviderOutcome.SUCCEEDED


def test_reference_visual_reverse_remix_preserves_portrait_composition_when_targeting_four_by_five(  # noqa: E501
) -> None:
    delegate = StubDelegate(_artifact_result())
    provider = ReferenceVisualReverseRemixProvider(
        delegate,
        manifest_validator=ManifestSpy(),
    )

    result = asyncio.run(
        provider.execute(
            _request(
                "reference_remix",
                input_config={
                    "product_asset_ids": ["asset-product"],
                    "reference_asset_ids": ["asset-reference"],
                },
                settings={"aspect_ratio": "4:5", "image_count": 1},
            )
        )
    )

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert _adapter_request(delegate)["target"]["fit"] == "contain"


def test_cross_role_duplicate_asset_is_rejected_before_delegate() -> None:
    delegate = StubDelegate(_artifact_result())
    provider = ReferenceRemixProvider(delegate, manifest_validator=ManifestSpy())
    request = _request(
        "reference_remix",
        input_config={
            "product_asset_ids": ["asset-shared"],
            "reference_asset_ids": ["asset-shared"],
        },
        settings={"image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "IMAGE_INPUT_INVALID"
    assert delegate.requests == []


def test_manifest_validator_must_return_content_hash() -> None:
    delegate = StubDelegate(_artifact_result())

    def invalid_validator(skill_id: str) -> None:
        del skill_id

    provider = CreativeImageProvider(
        delegate,
        manifest_validator=invalid_validator,  # type: ignore[arg-type]
    )
    request = _request(
        "creative_image",
        input_config={"product_asset_ids": ["asset-product"]},
        settings={"image_count": 1},
    )

    result = asyncio.run(provider.execute(request))

    assert result.outcome == ProviderOutcome.FAILED
    assert result.error_code == "SKILL_MANIFEST_VALIDATION_FAILED"
    assert delegate.requests == []
