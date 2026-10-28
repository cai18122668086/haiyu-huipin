from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain import JobType
from app.execution import _output_asset_role
from app.routes_v1 import (
    _job_item_configuration,
    _job_item_key,
    _new_job_from_payload,
    runtime_status_response,
)
from app.schemas_v1 import (
    AmazonVisualSuiteJobCreate,
    CreativeImageJobCreate,
    DetailPackJobCreate,
    ProductImageJobCreate,
    ReferenceRemixJobCreate,
)
from app.skill_runtime import OPERATION_SKILLS

PRODUCT_PRESETS = (
    "scene_main",
    "white_background",
    "model_image",
    "change_model",
    "try_on",
    "change_scene",
    "change_background",
    "change_color",
)


@pytest.mark.parametrize("preset", PRODUCT_PRESETS)
def test_product_image_presets_share_one_job_type(preset: str) -> None:
    configuration: dict[str, object] = {"task_type": preset, "image_count": 2}
    if preset == "change_color":
        configuration["target_color"] = "???"
    payload = ProductImageJobCreate(
        type="product_image",
        input={"product_asset_ids": ["asset-product"]},
        configuration=configuration,
    )

    job, roles = _new_job_from_payload("ws-contract", payload)

    assert job.type == JobType.PRODUCT_IMAGE.value
    assert job.expected_item_count == 2
    assert [_job_item_key(job.type, index) for index in range(2)] == [
        "image-001",
        "image-002",
    ]
    assert roles == [("asset-product", "product_reference", 0)]


def test_product_image_items_preserve_independent_output_identity() -> None:
    assert [
        _job_item_configuration(JobType.PRODUCT_IMAGE.value, ordinal)
        for ordinal in range(3)
    ] == [
        {"item_key": "image-001", "ordinal": 0},
        {"item_key": "image-002", "ordinal": 1},
        {"item_key": "image-003", "ordinal": 2},
    ]


def test_change_color_requires_target_color() -> None:
    with pytest.raises(ValidationError, match="target_color is required"):
        ProductImageJobCreate(
            type="product_image",
            input={"product_asset_ids": ["asset-product"]},
            configuration={"task_type": "change_color"},
        )


@pytest.mark.parametrize(
    "input_payload",
    (
        {"product_asset_ids": [], "reference_asset_ids": ["style"]},
        {"product_asset_ids": ["product"], "reference_asset_ids": []},
    ),
)
def test_reference_remix_requires_product_and_style_assets(
    input_payload: dict[str, list[str]],
) -> None:
    with pytest.raises(ValidationError):
        ReferenceRemixJobCreate(
            type="reference_remix",
            input=input_payload,
            configuration={},
        )


def test_creative_image_defaults_to_one_and_caps_at_twelve() -> None:
    payload = CreativeImageJobCreate(
        type="creative_image",
        input={"product_asset_ids": ["product"]},
        configuration={},
    )
    job, _ = _new_job_from_payload("ws-contract", payload)
    assert payload.configuration.image_count == 1
    assert job.expected_item_count == 1

    maximum = CreativeImageJobCreate(
        type="creative_image",
        input={"product_asset_ids": ["product"]},
        configuration={"image_count": 12},
    )
    maximum_job, _ = _new_job_from_payload("ws-contract", maximum)
    assert maximum_job.expected_item_count == 12

    with pytest.raises(ValidationError):
        CreativeImageJobCreate(
            type="creative_image",
            input={"product_asset_ids": ["product"]},
            configuration={"image_count": 13},
        )


def test_inspiration_creative_auto_ratio_is_reserved_for_inspiration_mode() -> None:
    payload = CreativeImageJobCreate(
        type="creative_image",
        input={"product_asset_ids": ["product"]},
        configuration={
            "aspect_ratio": "follow-reference",
            "inspiration_planning": True,
        },
    )
    assert payload.configuration.aspect_ratio == "follow-reference"

    with pytest.raises(ValidationError, match="reserved for the Inspiration tab"):
        CreativeImageJobCreate(
            type="creative_image",
            input={"product_asset_ids": ["product"]},
            configuration={"aspect_ratio": "follow-reference"},
        )


@pytest.mark.parametrize(
    "job_model,input_payload",
    [
        (ProductImageJobCreate, {"product_asset_ids": ["product"]}),
        (
            ReferenceRemixJobCreate,
            {
                "product_asset_ids": ["product"],
                "reference_asset_ids": ["style"],
            },
        ),
        (CreativeImageJobCreate, {"product_asset_ids": ["product"]}),
    ],
)
def test_single_image_families_preserve_twelve_requested_items(
    job_model: object,
    input_payload: dict[str, list[str]],
) -> None:
    configuration: dict[str, object] = {"image_count": 12}
    if job_model is ProductImageJobCreate:
        configuration["task_type"] = "scene_main"
    payload = job_model(  # type: ignore[operator]
        type=(
            "product_image"
            if job_model is ProductImageJobCreate
            else "reference_remix"
            if job_model is ReferenceRemixJobCreate
            else "creative_image"
        ),
        input=input_payload,
        configuration=configuration,
    )
    job, _ = _new_job_from_payload("ws-contract", payload)
    assert job.expected_item_count == 12


def test_detail_market_region_is_stored_without_changing_job_type() -> None:
    payload = DetailPackJobCreate(
        type="detail_pack",
        input={
            "product_groups": [{"group_key": "primary", "asset_ids": ["product"]}],
            "reference_asset_ids": ["style"],
        },
        configuration={
            "product_name": "????",
            "platform_code": "tmall",
            "market_region": "domestic",
            "page_count": 8,
        },
    )
    job, roles = _new_job_from_payload("ws-contract", payload)
    assert job.type == JobType.DETAIL_PACK.value
    assert job.configuration["settings"]["market_region"] == "domestic"
    assert job.configuration["settings"]["auto_start_after_plan"] is False
    assert job.expected_item_count == 8
    assert roles == [
        ("product", "product_reference", 0),
        ("style", "style_reference", 0),
    ]


def test_amazon_contract_has_fixed_21_items_and_input_roles() -> None:
    payload = AmazonVisualSuiteJobCreate(
        type="amazon_visual_suite",
        input={
            "white_background_product_asset_ids": ["white"],
            "detail_asset_ids": ["detail"],
            "package_asset_ids": ["package"],
            "scene_asset_ids": ["scene"],
            "brand_asset_ids": ["brand"],
        },
        configuration={"product_name": "Product"},
    )
    job, roles = _new_job_from_payload("ws-contract", payload)
    keys = [_job_item_key(job.type, index) for index in range(21)]
    assert job.expected_item_count == 21
    assert keys == [
        "MAIN-01",
        *[f"SECONDARY-{index:02d}" for index in range(1, 7)],
        *[f"A-DESKTOP-{index:02d}" for index in range(1, 8)],
        *[f"A-MOBILE-{index:02d}" for index in range(1, 8)],
    ]
    assert roles == [
        ("white", "white_background_product", 0),
        ("detail", "product_detail", 0),
        ("package", "package_reference", 0),
        ("scene", "scene_reference", 0),
        ("brand", "brand_reference", 0),
    ]


def test_amazon_contract_accepts_image_only_analysis_without_product_name() -> None:
    payload = AmazonVisualSuiteJobCreate(
        type="amazon_visual_suite",
        input={"white_background_product_asset_ids": ["white"]},
        configuration={},
    )

    job, _ = _new_job_from_payload("ws-contract", payload)

    assert job.configuration.get("product_name") is None
    assert job.expected_item_count == 21


def test_amazon_contract_rejects_non_amazon_marketplace() -> None:
    with pytest.raises(ValidationError, match="amazon-us"):
        AmazonVisualSuiteJobCreate(
            type="amazon_visual_suite",
            input={"white_background_product_asset_ids": ["white"]},
            configuration={
                "marketplace": "tmall",
                "product_name": "Product",
            },
        )


def test_new_operation_skill_mapping_preserves_existing_skills() -> None:
    assert OPERATION_SKILLS == {
        "local_edit": "masked-image-repair",
        "product_image": "xinghe-wanneng-shengtu-3-0",
        "reference_remix": "xinghe-reference-image-remix-3",
        "model_pose_transfer": "ecommerce-model-pose-transfer",
        "creative_image": "xinghe-ecommerce-creative-image",
        "detail_pack": "xinghe-ecommerce-detail",
        "amazon_visual_suite": "xinghe-amazon-visual-suite",
        "domestic_main_image": "aurora-domestic-ecommerce-main-image",
    }


def test_runtime_features_expose_all_job_families() -> None:
    runtime = runtime_status_response(
        environment="test",
        authentication_mode="development",
        task_runner="mock",
    )
    assert set(runtime.capabilities.features) == {
        "local_edit",
        "product_image",
        "reference_remix",
        "model_pose_transfer",
        "creative_image",
        "detail_pack",
        "amazon_visual_suite",
        "domestic_main_image",
    }
    assert all(
        capability.execution == "mock"
        for capability in runtime.capabilities.features.values()
    )


def test_amazon_item_configuration_matches_canonical_sizes() -> None:
    main = _job_item_configuration(JobType.AMAZON_VISUAL_SUITE.value, 0)
    secondary = _job_item_configuration(JobType.AMAZON_VISUAL_SUITE.value, 1)
    desktop = _job_item_configuration(JobType.AMAZON_VISUAL_SUITE.value, 7)
    mobile = _job_item_configuration(JobType.AMAZON_VISUAL_SUITE.value, 14)

    assert main == {
        "item_key": "MAIN-01",
        "group": "main",
        "group_ordinal": 1,
        "target_width": 2000,
        "target_height": 2000,
        "target_size": {"width": 2000, "height": 2000},
        "contact_sheet": False,
    }
    assert secondary["item_key"] == "SECONDARY-01"
    assert secondary["target_size"] == {"width": 1200, "height": 1600}
    assert desktop["item_key"] == "A-DESKTOP-01"
    assert desktop["target_size"] == {"width": 1464, "height": 600}
    assert mobile["item_key"] == "A-MOBILE-01"
    assert mobile["target_size"] == {"width": 1600, "height": 1200}


@pytest.mark.parametrize(
    "job_type",
    (
        JobType.PRODUCT_IMAGE.value,
        JobType.REFERENCE_REMIX.value,
        JobType.CREATIVE_IMAGE.value,
    ),
)
def test_single_image_item_configuration_preserves_output_identity(
    job_type: str,
) -> None:
    assert _job_item_configuration(job_type, 0, inspiration_workflow=True) == {
        "item_key": "image-001",
        "ordinal": 0,
    }
    assert _job_item_configuration(job_type, 1, inspiration_workflow=True) == {
        "item_key": "image-002",
        "ordinal": 1,
    }


def test_inspiration_flag_does_not_change_single_image_identity_contract() -> None:
    assert _job_item_configuration(
        JobType.CREATIVE_IMAGE.value, 0, inspiration_workflow=False
    ) == _job_item_configuration(
        JobType.CREATIVE_IMAGE.value, 0, inspiration_workflow=True
    )


@pytest.mark.parametrize(
    ("job_type", "expected_role"),
    (
        (JobType.LOCAL_EDIT.value, "local_edit_result"),
        (JobType.PRODUCT_IMAGE.value, "product_image_result"),
        (JobType.REFERENCE_REMIX.value, "reference_remix_result"),
        (JobType.CREATIVE_IMAGE.value, "creative_image_result"),
        (JobType.DETAIL_PACK.value, "detail_page"),
        (JobType.AMAZON_VISUAL_SUITE.value, "amazon_visual"),
    ),
)
def test_new_job_types_have_stable_output_roles(
    job_type: str, expected_role: str
) -> None:
    assert _output_asset_role(job_type) == expected_role
