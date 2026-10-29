from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .amazon_visual_suite_contract import (
    AMAZON_DEFAULT_MARKETPLACE,
    AmazonMarketplace,
)
from .domain import (
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    JobItemStatus,
    JobStatus,
    JobType,
    MembershipRole,
    RetryScope,
    UsageCompleteness,
    UserStatus,
)


class MoneyAmount(BaseModel):
    micros: str
    currency: str


class CostSummary(BaseModel):
    estimated_min: MoneyAmount | None
    estimated_max: MoneyAmount | None
    actual: MoneyAmount | None
    estimated_credits: int | None
    actual_credits: int | None
    usage_completeness: UsageCompleteness


class CurrentUserResponse(BaseModel):
    id: str
    email: str | None
    display_name: str | None
    status: UserStatus
    email_verified: bool


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    role: MembershipRole


CapabilityAvailability = Literal["available", "unavailable"]
CapabilityExecution = Literal["local", "mock", "provider", "none"]
CapabilityReasonCode = Literal[
    "LOCAL_EDIT_PROVIDER_NOT_IMPLEMENTED",
    "TASK_RUNNER_MOCK",
    "REAL_PROVIDER_NOT_REGISTERED",
    "EXTERNAL_AUTHENTICATION_NOT_IMPLEMENTED",
]


class CapabilityStatus(BaseModel):
    availability: CapabilityAvailability = Field(
        description=(
            "Whether the workflow and API are available for internal testing. "
            "This does not mean that real generation is available; clients must "
            "also inspect execution."
        )
    )
    execution: CapabilityExecution = Field(
        description=(
            "How this capability executes: locally, through the zero-cost mock "
            "runner, through a real provider, or not at all."
        )
    )
    reason_code: CapabilityReasonCode | None = Field(
        description=(
            "Stable machine-readable explanation when execution is mocked or the "
            "capability is unavailable."
        )
    )


class RuntimeCapabilities(BaseModel):
    features: dict[str, CapabilityStatus] = Field(
        default_factory=dict,
        description="Configuration-driven workflow capability map.",
    )
    asset_management: CapabilityStatus = Field(
        description="Asset upload, listing, and private access URL workflow."
    )
    local_edit: CapabilityStatus = Field(
        description=(
            "Local-edit workflow. It uses the mock adapter under the mock runner "
            "and the dedicated strict masked-repair provider under Tengli."
        )
    )
    detail_pack: CapabilityStatus = Field(
        description=(
            "Detail-pack workflow. Its execution follows the selected task runner."
        )
    )
    external_authentication: CapabilityStatus = Field(
        description=(
            "External authentication capability. A configured mode alone does not "
            "mean that the implementation is available."
        )
    )


class RuntimeStatusResponse(BaseModel):
    environment: Literal["development", "test", "production"]
    authentication_mode: Literal["development", "external"]
    task_runner: Literal["mock", "tengli"]
    capabilities: RuntimeCapabilities


class DevelopmentProviderSettingsUpdate(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096, repr=False)
    base_url: str = Field(min_length=8, max_length=512)
    image_model: str = Field(min_length=1, max_length=120)
    vision_model: str = Field(min_length=1, max_length=120)


class DevelopmentProviderSettingsResponse(BaseModel):
    available: bool
    configured: bool
    task_runner: Literal["mock", "tengli"]
    product_image_provider_enabled: bool
    reference_remix_provider_enabled: bool
    model_pose_transfer_provider_enabled: bool
    creative_image_provider_enabled: bool
    amazon_visual_suite_provider_enabled: bool
    crossborder_detail_provider_enabled: bool
    domestic_main_image_provider_enabled: bool
    base_url: str
    image_model: str
    vision_model: str
    message: str


class AssetResponse(BaseModel):
    id: str
    workspace_id: str
    media_kind: AssetMediaKind
    origin_kind: AssetOriginKind
    lifecycle_status: AssetLifecycleStatus
    original_filename: str
    content_type: str
    byte_size: int
    width: int | None
    height: int | None
    sha256: str
    created_at: datetime
    expires_at: datetime | None


class AssetListResponse(BaseModel):
    items: list[AssetResponse]
    next_cursor: str | None
    has_more: bool
    total: int | None = None


class AccessUrlRequest(BaseModel):
    disposition: Literal["inline", "attachment"] = "inline"
    variant: Literal["original", "thumbnail"] = "original"


class AccessUrlResponse(BaseModel):
    url: str
    expires_at: datetime
    disposition: Literal["inline", "attachment"]
    original_url: str | None = None


class LocalEditInput(BaseModel):
    source_asset_id: str
    selection_mask_asset_id: str

    @model_validator(mode="after")
    def distinct_assets(self) -> "LocalEditInput":
        if self.source_asset_id == self.selection_mask_asset_id:
            raise ValueError("source and selection mask must be different assets")
        return self


class LocalEditConfiguration(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    quality: Literal["preview", "balanced", "final", "auto"] = "balanced"
    # The masked-image-repair Skill chooses the cheapest viable execution path.
    # ``auto`` keeps the existing UI behaviour while allowing API clients to
    # request an explicit deterministic, hybrid, cropped, or full-canvas path.
    execution_path: Literal["auto", "deterministic", "hybrid", "cropped", "full"] = (
        "auto"
    )
    output_format: Literal["png"] = "png"


class ProductGroupInput(BaseModel):
    group_key: str = Field(min_length=1, max_length=64)
    asset_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_assets(self) -> "ProductGroupInput":
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("asset_ids must not contain duplicates")
        return self


class ProductImageInput(BaseModel):
    product_asset_ids: list[str] = Field(min_length=1, max_length=8)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_assets(self) -> "ProductImageInput":
        combined = self.product_asset_ids + self.reference_asset_ids
        if len(combined) != len(set(combined)):
            raise ValueError("input assets must not contain duplicates")
        return self


ProductImageTaskType = Literal[
    "scene_main",
    "white_background",
    "model_image",
    "change_model",
    "try_on",
    "change_scene",
    "change_background",
    "change_color",
]


class ProductImageConfiguration(BaseModel):
    task_type: ProductImageTaskType
    platform_code: str = Field(default="tmall", min_length=1, max_length=32)
    aspect_ratio: Literal["1:1", "3:4", "4:3", "4:5", "9:16", "16:9"] = "1:1"
    image_count: int = Field(default=1, ge=1, le=12)
    instruction: str = Field(default="", max_length=4000)
    target_color: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_target_color(self) -> "ProductImageConfiguration":
        if self.task_type == "change_color" and not (self.target_color or "").strip():
            raise ValueError("target_color is required for change_color")
        return self


class ReferenceRemixInput(BaseModel):
    product_asset_ids: list[str] = Field(min_length=1, max_length=8)
    reference_asset_ids: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_assets(self) -> "ReferenceRemixInput":
        combined = self.product_asset_ids + self.reference_asset_ids
        if len(combined) != len(set(combined)):
            raise ValueError("input assets must not contain duplicates")
        return self


class ReferenceRemixConfiguration(BaseModel):
    reference_skill: (
        Literal[
            "aurora-remix-fast",
            "reference-visual-reverse-remix",
            "aurora-ecommerce-reference-remix",
            "all-category-style-benchmark-still",
              "aurora-ecommerce-reference-remix-fast",
              "all-category-style-benchmark-still-fast",
        ]
        | None
    ) = None
    replication_method: Literal["exact", "style"] | None = None
    replica_plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    platform_code: str = Field(default="tmall", min_length=1, max_length=32)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    selling_points: list[str] = Field(default_factory=list, max_length=12)
    aspect_ratio: Literal[
        "follow-reference", "1:1", "3:4", "4:3", "4:5", "9:16", "16:9"
    ] = "follow-reference"
    image_count: int = Field(default=1, ge=1, le=60)
    execution_mode: Literal["planner", "direct_prompt"] = "planner"
    inspiration_product_first: bool = False
    quality: Literal["low", "medium", "high"] = "high"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    instruction: str = Field(default="", max_length=4000)
    product_identity_lock: str = Field(default="", max_length=2000)
    reference_visual_analysis: str = Field(default="", max_length=2000)
    commercial_intent: str = Field(default="", max_length=2000)
    reusable_visual_strategy: str = Field(default="", max_length=2000)
    original_remix_prompt: str = Field(default="", max_length=4000)
    forbidden_transfer: str = Field(default="", max_length=2000)
    post_generation_review: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def preserve_legacy_count_limit(self) -> "ReferenceRemixConfiguration":
        if (
            self.reference_skill
            not in {
                "aurora-ecommerce-reference-remix",
                "all-category-style-benchmark-still",
                "aurora-ecommerce-reference-remix-fast",
                "all-category-style-benchmark-still-fast",
            }
            and self.image_count > 12
        ):
            raise ValueError("legacy reference remix supports at most 12 images")
        return self


class ModelPoseAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pose_slug: str = Field(pattern=r"^(?:male|female)-[0-9]{3}$")
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelPoseTransferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_source_asset_id: str
    pose_reference_asset_id: str
    pose_slug: str | None = Field(default=None, pattern=r"^(?:male|female)-[0-9]{3}$")
    pose_reference_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_pose_inputs(self) -> "ModelPoseTransferInput":
        if self.model_source_asset_id == self.pose_reference_asset_id:
            raise ValueError("model source and pose reference must be different assets")
        if (self.pose_slug is None) != (self.pose_reference_sha256 is None):
            raise ValueError(
                "pose_slug and pose_reference_sha256 must be provided together"
            )
        return self


class ModelPoseTransferConfiguration(BaseModel):
    aspect_ratio: Literal["follow-source"] = "follow-source"
    image_count: Literal[1] = 1
    quality: Literal["low", "medium", "high"] = "high"
    output_format: Literal["png"] = "png"
    instruction: str = Field(default="", max_length=4000)


class CreativeImageInput(BaseModel):
    product_asset_ids: list[str] = Field(min_length=1, max_length=8)
    competitor_asset_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_assets(self) -> "CreativeImageInput":
        combined = self.product_asset_ids + self.competitor_asset_ids
        if len(combined) != len(set(combined)):
            raise ValueError("input assets must not contain duplicates")
        return self


class CreativeImageConfiguration(BaseModel):
    creative_direction: Literal[
        "selling_point",
        "pain_point",
        "scenario",
        "promotion",
        "premium",
        "social",
    ] = "selling_point"
    platform_code: str = Field(default="tmall", min_length=1, max_length=32)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    selling_points: list[str] = Field(default_factory=list, max_length=12)
    aspect_ratio: Literal[
        "follow-reference", "1:1", "3:4", "4:3", "4:5", "9:16", "16:9"
    ] = "1:1"
    image_count: int = Field(default=1, ge=1, le=12)
    inspiration_planning: bool = False
    inspiration_reference_planning: bool = False
    instruction: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def dynamic_aspect_requires_inspiration(self) -> "CreativeImageConfiguration":
        if self.aspect_ratio == "follow-reference" and not self.inspiration_planning:
            raise ValueError(
                "follow-reference aspect_ratio is reserved for the Inspiration tab"
            )
        return self


class DetailPackInput(BaseModel):
    product_groups: list[ProductGroupInput] = Field(min_length=1, max_length=1)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)


class DetailPackConfiguration(BaseModel):
    detail_skill: Literal["quick", "refined"] = Field(default_factory=lambda: "quick")
    detail_speed: Literal["standard", "fast"] = Field(
        default_factory=lambda: "standard"
    )

    @model_serializer(mode="wrap")
    def serialize_detail_speed(self, handler: Any) -> dict[str, Any]:
        # A new default must not change historical request hashes or settings.
        value: dict[str, Any] = handler(self)
        if self.detail_speed == "standard":
            value.pop("detail_speed", None)
        return value

    website_contract_version: Literal["WEBSITE_LEAN_V1", "WEBSITE_DIRECT_V2"] | None = (
        None
    )
    brandless_confirmation: str | None = Field(
        default=None, min_length=2, max_length=240
    )
    product_name: str = Field(min_length=1, max_length=160)
    selling_points: list[str] = Field(default_factory=list, max_length=12)
    platform_code: str = Field(min_length=1, max_length=32)
    market_region: Literal["domestic", "international"] = "international"
    category: str | None = Field(default=None, max_length=80)
    target_buyer: str | None = Field(default=None, max_length=500)
    verified_facts: list[str] = Field(default_factory=list, max_length=24)
    conflict_scan: dict[str, Any] = Field(default_factory=dict)
    source_conflicts: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    reference_materials: list[str] = Field(default_factory=list, max_length=24)
    platform_profile: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    page_count: int = Field(ge=1, le=12)
    auto_start_after_plan: bool = True
    # ``None`` preserves the legacy international planner default while the
    # cross-border Skill adapter resolves its own 16:9/2K canvas. Domestic
    # requests are normalized below to the strict 3:4 canvas.
    size: str | None = None

    @model_validator(mode="after")
    def default_domestic_delivery_size(self) -> "DetailPackConfiguration":
        if self.detail_speed == "fast" and (
            self.detail_skill != "refined" or self.market_region != "domestic"
        ):
            raise ValueError("Fast detail requires domestic refined detail.")
        if self.detail_skill == "refined" and self.market_region != "domestic":
            raise ValueError(
                "Refined detail 4.0 is available for domestic markets only."
            )
        if self.detail_skill == "refined" and self.website_contract_version is not None:
            raise ValueError("Refined detail cannot use an Aurora website contract.")
        if (
            self.market_region == "domestic"
            and self.brandless_confirmation is not None
            and len(self.brandless_confirmation.strip()) < 2
        ):
            raise ValueError(
                "Brandless mode requires explicit user confirmation words."
            )
        if self.market_region == "domestic" and "size" not in self.model_fields_set:
            self.size = "1440x1920"
        if (
            self.market_region == "domestic"
            and self.detail_skill == "quick"
            and self.page_count not in {8, 10, 12}
        ):
            raise ValueError(
                "Domestic Aurora detail pages must contain 8, 10, or 12 screens."
            )
        if (
            self.market_region == "domestic"
            and self.detail_skill == "quick"
            and self.website_contract_version is None
        ):
            # Historical, unversioned clients retain their manual checkpoint.
            self.auto_start_after_plan = False
        allowed_sizes = {
            "750x1000",
            "1200x1600",
            "1440x1920",
            "900x1200",
            "1024x1536",
            "2048x1152",
            "1600x1600",
            "2200x2200",
            "2000x2000",
            "1000x1000",
            "1024x1024",
            "2048x2048",
            "1200x1200",
            "1464x600",
            "1600x1200",
        }
        if self.size is not None and self.size not in allowed_sizes:
            raise ValueError("Unsupported detail image size.")
        if (
            self.market_region == "international"
            and "language" not in self.model_fields_set
        ):
            self.language = "en"
        return self


class AmazonVisualSuiteInput(BaseModel):
    white_background_product_asset_ids: list[str] = Field(min_length=1, max_length=8)
    detail_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    package_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    scene_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    brand_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    competitor_reference_asset_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
    )

    @model_validator(mode="after")
    def unique_assets(self) -> "AmazonVisualSuiteInput":
        combined = (
            self.white_background_product_asset_ids
            + self.detail_asset_ids
            + self.package_asset_ids
            + self.scene_asset_ids
            + self.brand_asset_ids
            + self.reference_asset_ids
            + self.competitor_reference_asset_ids
        )
        if len(combined) != len(set(combined)):
            raise ValueError("input assets must not contain duplicates")
        if len(combined) > 8:
            raise ValueError("Amazon visual suite accepts at most 8 source images")
        return self


class AmazonVisualSuiteConfiguration(BaseModel):
    amazon_speed: Literal["standard", "fast"] | None = None
    auto_start_after_plan: bool = True
    marketplace: AmazonMarketplace = AMAZON_DEFAULT_MARKETPLACE
    product_name: str = Field(default="", max_length=160)
    category: str = Field(default="", max_length=120)
    target_buyer: str = Field(default="", max_length=500)
    listing_title: str = Field(default="", max_length=240)
    bullet_points: list[str] = Field(default_factory=list, max_length=12)
    listing_description: str = Field(default="", max_length=4000)
    a_plus_copy: str = Field(default="", max_length=4000)
    product_facts: list[str] = Field(default_factory=list, max_length=24)
    parameters: list[str] = Field(default_factory=list, max_length=24)
    certifications_or_evidence: list[str] = Field(default_factory=list, max_length=24)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=24)
    brand_colors: list[str] = Field(default_factory=list, max_length=8)
    style_preferences: list[str] = Field(default_factory=list, max_length=12)
    instruction: str = Field(default="", max_length=4000)
    language: str = Field(default="en-US", min_length=2, max_length=16)

    @model_serializer(mode="wrap")
    def serialize_speed(self, handler: Any) -> dict[str, Any]:
        value = handler(self)
        # Preserve historical request hashes and durable settings for the default.
        if value.get("amazon_speed") in {None, "standard"}:
            value.pop("amazon_speed", None)
        return value


class DomesticMainImageInput(BaseModel):
    product_asset_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_assets(self) -> "DomesticMainImageInput":
        if len(self.product_asset_ids) != len(set(self.product_asset_ids)):
            raise ValueError("product assets must not contain duplicates")
        return self


def _main_image_speed_schema(schema: dict[str, Any]) -> None:
    # Keep the opt-in optional in generated request types. Runtime still
    # defaults to standard, whose field is omitted by the serializer below.
    schema.pop("default", None)


class DomesticMainImageConfiguration(BaseModel):
    main_image_speed: Literal["standard", "fast", "native_fast"] = Field(
        default="standard",
        description="Optional fast workflow opt-in; omitted requests use standard.",
        json_schema_extra=_main_image_speed_schema,
    )

    @model_serializer(mode="wrap")
    def preserve_standard_request_identity(self, handler: Any) -> dict[str, Any]:
        value = handler(self)
        if self.main_image_speed == "standard":
            value.pop("main_image_speed", None)
        return value

    auto_start_after_plan: bool = True
    product_name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=120)
    selling_points: list[str] = Field(min_length=1, max_length=24)
    user_provided_display_facts: list[str] = Field(default_factory=list, max_length=24)
    product_attributes: list[str] = Field(default_factory=list, max_length=24)
    scenes: list[str] = Field(default_factory=list, max_length=12)
    offers: list[str] = Field(default_factory=list, max_length=12)
    service_or_trust_facts: list[str] = Field(default_factory=list, max_length=12)
    style_preferences: list[str] = Field(default_factory=list, max_length=12)
    instruction: str = Field(default="", max_length=4000)
    language: Literal["zh-CN"] = "zh-CN"
    # Development-only opt-in. The production default remains the native
    # Aurora generation path; the shadow value is carried through the normal
    # job envelope so a local user submission can be reviewed end to end.
    aurora_shadow_mode: Literal[
        "off", "contract_first_rebuild_v1", "execution_semantics_v1"
    ] = "off"


class JobCreateBase(BaseModel):
    intent: Literal["draft", "submit"] = "submit"
    display_name: str | None = Field(default=None, max_length=160)
    # Optional provenance for the inspiration gallery. When present, the
    # backend resolves the authoritative workflow from the published case and
    # rejects mismatched or unconfigured job payloads.
    inspiration_case_id: str | None = Field(default=None, max_length=40)


class LocalEditJobCreate(JobCreateBase):
    type: Literal[JobType.LOCAL_EDIT]
    input: LocalEditInput
    configuration: LocalEditConfiguration


class ProductImageJobCreate(JobCreateBase):
    type: Literal[JobType.PRODUCT_IMAGE]
    input: ProductImageInput
    configuration: ProductImageConfiguration


class ReferenceRemixJobCreate(JobCreateBase):
    type: Literal[JobType.REFERENCE_REMIX]
    input: ReferenceRemixInput
    configuration: ReferenceRemixConfiguration

    @model_validator(mode="after")
    def fast_remix_input(self) -> "ReferenceRemixJobCreate":
        if self.configuration.reference_skill == "aurora-remix-fast" and (
            len(self.input.product_asset_ids) != 1
            or len(self.input.reference_asset_ids) != 1
            or self.configuration.aspect_ratio != "follow-reference"
            or self.configuration.language != "zh-CN"
            or self.configuration.execution_mode != "planner"
            or not any(value.strip() for value in self.configuration.selling_points)
        ):
            raise ValueError(
                "单图模仿需要一张商品图、一张对标图及真实商品事实，画幅比例跟随对标图。"
            )
        return self


class ModelPoseTransferJobCreate(JobCreateBase):
    type: Literal[JobType.MODEL_POSE_TRANSFER]
    input: ModelPoseTransferInput
    configuration: ModelPoseTransferConfiguration


class CreativeImageJobCreate(JobCreateBase):
    type: Literal[JobType.CREATIVE_IMAGE]
    input: CreativeImageInput
    configuration: CreativeImageConfiguration


class DetailPackJobCreate(JobCreateBase):
    type: Literal[JobType.DETAIL_PACK]
    input: DetailPackInput
    configuration: DetailPackConfiguration


class AmazonVisualSuiteJobCreate(JobCreateBase):
    type: Literal[JobType.AMAZON_VISUAL_SUITE]
    input: AmazonVisualSuiteInput
    configuration: AmazonVisualSuiteConfiguration


class DomesticMainImageJobCreate(JobCreateBase):
    type: Literal[JobType.DOMESTIC_MAIN_IMAGE]
    input: DomesticMainImageInput
    configuration: DomesticMainImageConfiguration


JobCreate = Annotated[
    LocalEditJobCreate
    | ProductImageJobCreate
    | ReferenceRemixJobCreate
    | ModelPoseTransferJobCreate
    | CreativeImageJobCreate
    | DetailPackJobCreate
    | AmazonVisualSuiteJobCreate
    | DomesticMainImageJobCreate,
    Field(discriminator="type"),
]


class DraftJobUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    configuration: dict[str, Any] | None = None


class JobItemResponse(BaseModel):
    id: str
    item_key: str
    ordinal: int
    status: JobItemStatus
    stage: str | None
    quality: str | None
    result_source: str | None
    error_code: str | None
    error_message: str | None
    error_details: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None


class JobAssetResponse(BaseModel):
    asset_id: str
    job_item_id: str | None
    direction: str
    role: str
    ordinal: int
    is_deliverable: bool
    validation: dict[str, Any]


class PlanResponse(BaseModel):
    id: str
    revision: int
    schema_version: str
    payload: dict[str, Any]
    validation_status: str
    validation_errors: list[dict[str, Any]]
    confirmed_at: datetime | None


class JobListItemResponse(BaseModel):
    id: str
    workspace_id: str
    # Retired executors remain readable without becoming valid creation types.
    type: JobType | Literal["product_replica"]
    status: JobStatus
    stage: str | None
    display_name: str | None
    task_preset: ProductImageTaskType | None = None
    progress: int
    expected_item_count: int
    succeeded_item_count: int
    failed_item_count: int
    output_asset_count: int = 0
    primary_output_asset_id: str | None = None
    allowed_actions: list[str]
    cost: CostSummary
    created_at: datetime
    updated_at: datetime
    history_expires_at: datetime | None


class JobDetailResponse(JobListItemResponse):
    configuration: dict[str, Any]
    configuration_schema: str
    prompt_version: str
    strategy_version: str
    release_version: str
    root_job_id: str | None
    retry_of_job_id: str | None
    retry_scope: str | None
    state_version: int
    error_code: str | None
    error_message: str | None
    error_details: dict[str, Any] | None
    api_request_count: int
    cancel_requested_at: datetime | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    items: list[JobItemResponse]
    assets: list[JobAssetResponse]
    plan: PlanResponse | None


class JobListResponse(BaseModel):
    items: list[JobListItemResponse]
    next_cursor: str | None
    has_more: bool
    total: int | None = None


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RetryRequest(BaseModel):
    scope: RetryScope = RetryScope.FAILED
    item_ids: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def selected_requires_items(self) -> "RetryRequest":
        if self.scope == RetryScope.SELECTED and not self.item_ids:
            raise ValueError("selected retry scope requires item_ids")
        if self.scope != RetryScope.SELECTED and self.item_ids:
            raise ValueError("item_ids are only valid for selected retry scope")
        return self


class PlanUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]


class IntakeConflictResolution(BaseModel):
    conflict_id: str = Field(min_length=1, max_length=120)
    retained_fact_ids: list[str] = Field(min_length=1, max_length=36)
    rejected_fact_ids: list[str] = Field(default_factory=list, max_length=36)


class DetailIntakeResolutionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    confirmation_text: str = Field(min_length=2, max_length=500)
    resolutions: list[IntakeConflictResolution] = Field(
        default_factory=list, max_length=24
    )
    brandless_confirmation: str | None = Field(
        default=None, min_length=2, max_length=240
    )
    additional_product_asset_ids: list[str] = Field(default_factory=list, max_length=2)


class DetailAutomaticBrandRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class PlanConfirmRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    approval_text: str | None = Field(default=None, min_length=2, max_length=240)
    # Aurora domestic v4.3 binds authorization to the compiler-produced
    # group→screen approval packet and pair canvas contract.
    approval_document_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    approval_protocol: Literal["domestic-native-approval-v1"] | None = None
    native_approval_document_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    approved_module_count: int | None = Field(default=None, ge=8, le=12)
    approved_pair_canvas_aspect_ratio: Literal["3:8"] | None = None
    domestic_detail_delivery_mode: (
        Literal["USER_FINAL_REVIEW", "AUTO_FINALIZE_AUTHORIZED"] | None
    ) = None
    automatic_delivery_authorization_text: str | None = Field(
        default=None, min_length=2, max_length=240
    )
    automatic_delivery_authorization_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class DomesticDetailLocalRevisionDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_id: str = Field(pattern=r"^M(?:0[1-9]|1[0-2])$")
    verbatim_instruction: str = Field(min_length=2, max_length=2000)
    change_scope: Literal["local"]
    change_kind: Literal[
        "TYPOGRAPHY_LAYOUT", "COPY_CONTENT", "VISUAL_DETAIL", "OTHER_LOCAL"
    ]
    change_only: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        min_length=1, max_length=12
    )
    preserve: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        min_length=1, max_length=12
    )
    must_not_regress: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        min_length=1, max_length=12
    )
    changes_identity: Literal[False]
    changes_facts: Literal[False]
    changes_target_buyer: Literal[False]
    changes_campaign_style: Literal[False]
    changes_module_sequence: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def explicit_local_scope(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for name in (
                "changes_identity",
                "changes_facts",
                "changes_target_buyer",
                "changes_campaign_style",
                "changes_module_sequence",
            ):
                if type(value.get(name)) is not bool or value[name] is not False:
                    raise ValueError(
                        "Global change flags must be false; otherwise replan"
                    )
        return value


class DomesticDetailLocalRevisionQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    final_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adjustment: DomesticDetailLocalRevisionDelta


class DomesticDetailLocalRevisionCreateRequest(DomesticDetailLocalRevisionQuoteRequest):
    quote_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_text: str = Field(min_length=1, max_length=2000)


class DomesticDetailLocalRevisionScreen(BaseModel):
    module_id: str
    asset_id: str
    ordinal: int
    sha256: str
    width: int
    height: int


class DomesticDetailLocalRevisionCanvas(BaseModel):
    asset_id: str
    sha256: str
    width: int
    height: int
    pixel_lineage_sha256: str


class DomesticDetailLocalRevisionQuote(BaseModel):
    version: Literal["domestic-detail-local-revision-quote-v1"]
    source_job_id: str
    workspace_id: str
    actor_user_id: str
    source_plan_id: str
    source_plan_revision: int
    source_plan_payload_sha256: str
    final_review_sha256: str
    source_context_sha256: str
    source_native_session_sha256: str
    source_generation_binding_sha256: str
    native_approval_document_sha256: str
    original_approval_receipt_sha256: str
    source_approved_scope_sha256: str
    skill_content_hash: str
    runtime_source_sha256: str
    revision_policy_sha256: str
    approved_screen_count: Literal[8, 10, 12]
    selected_module_id: str
    pair_id: str
    module_ids: list[str]
    source_screens: list[DomesticDetailLocalRevisionScreen]
    source_pair_canvas: DomesticDetailLocalRevisionCanvas
    source_quality_state: Literal["PAIR_REVIEW_PASSED", "WITH_RESIDUALS"]
    source_residual_issues: list[dict[str, Any]]
    adjustment: DomesticDetailLocalRevisionDelta
    image_dispatch_sha256: str
    review_dispatch_sha256: str
    image_provider: str
    image_model: str
    review_model: str
    dispatch_kind: Literal["http", "synthetic"]
    pricing_version: str
    image_unit_micros: int = Field(ge=0)
    review_reserve_micros: int = Field(ge=0)
    maximum_image_requests: Literal[2]
    maximum_scope_model_requests: Literal[1]
    maximum_review_model_requests: Literal[4]
    maximum_model_requests: Literal[5]
    maximum_provider_requests: Literal[7]
    maximum_cost_micros: int = Field(ge=0)
    currency: Literal["USD"]
    delivery_mode: Literal["USER_FINAL_REVIEW"]
    authorization_text: str
    disclosure: str
    sha256: str


class DomesticDetailAutomaticDeliveryAuthorization(BaseModel):
    version: Literal["domestic-detail-auto-delivery-authorization-v1"]
    job_id: str
    workspace_id: str
    actor_user_id: str
    plan_id: str
    plan_revision: int
    native_approval_document_sha256: str
    approval_document_sha256: str
    contract_binding_sha256: str
    source_input_sha256: str
    runtime_source_sha256: str
    delivery_mode: Literal["AUTO_FINALIZE_AUTHORIZED"]
    authorization_text: str
    completion_disclosure: str
    delivery_extra_model_requests: Literal[0]
    delivery_extra_image_requests: Literal[0]
    sha256: str


class DomesticDetailAutomaticDeliveryRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DomesticCampaignRecoveryQuote(BaseModel):
    version: Literal["campaign-saved-result-v1"]
    original_review_job_id: str
    source_job_id: str
    original_result_journal_sha256: str
    original_request_context_sha256: str
    model_requests: Literal[0]
    image_requests: Literal[0]
    reserve_micros: Literal[0]
    currency: Literal["USD"]
    sha256: str


class DomesticCampaignRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_result_journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DomesticCampaignReviewReport(BaseModel):
    report: dict[str, Any]
    request_context: dict[str, Any]
    original_message: str | None
    original_message_state: Literal["PRESERVED_UTF8", "UNAVAILABLE"]
    model: str
    source_job_id: str
    scope: Literal["OPTIONAL_CAMPAIGN_REVIEW_ONLY"]


class DomesticCampaignReviewQuote(BaseModel):
    version: Literal["domestic-campaign-review-v1"]
    source_job_id: str
    final_review_sha256: str
    source_context_sha256: str
    provider: str
    model: str
    dispatch_route_sha256: str
    reserve_micros: int
    currency: Literal["USD"]
    model_requests: Literal[1]
    image_requests: Literal[0]
    sha256: str


class DomesticCampaignReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    final_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_text: str = Field(min_length=2, max_length=240)
    authorized_reserve_micros: int = Field(gt=0, le=2_000_000, strict=True)
    quote_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DomesticCampaignReviewSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_job_id: str = Field(min_length=1, max_length=40)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DomesticDetailFinalReviewResponse(BaseModel):
    version: Literal["domestic-detail-final-review-v1"]
    job_id: str
    plan_id: str
    plan_revision: int
    skill_version: str
    context_sha256: str
    generation_binding_sha256: str
    sha256: str
    document_markdown: str
    quality_state: Literal["PAIR_REVIEW_PASSED", "WITH_RESIDUALS"]
    campaign_audit_status: Literal["NOT_REQUESTED"]
    supplementary_campaign_review: dict[str, Any] | None = None
    delivery_mode: Literal["AUTO_FINALIZE_AUTHORIZED"] | None = None
    automatic_delivery_authorization: dict[str, Any] | None = None
    screens: list[dict[str, Any]]
    pair_canvases: list[dict[str, Any]]
    residual_issues: list[dict[str, Any]]


class FinalizeJobRequest(BaseModel):
    approval_text: str = Field(min_length=2, max_length=240)
    final_review_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    checks: dict[str, str]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class OpenAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
