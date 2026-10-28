"""Provider adapter for the eight product-image presets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.new_image_skill_provider import (
    NewImageSkillContractError,
    NewImageSkillProvider,
    compact_prompt_input,
    controlled_target,
    optional_string,
    require_asset_ids,
    require_image_count,
    require_non_empty_string,
    role_labelled_assets,
    validate_optional_asset_ids,
)

# These compact contracts mirror the source Skill's progressive-loading files.
# They are deliberately kept as explicit contract text (rather than hidden
# provider defaults) so the delegate can compile the same identity, compliance
# and output-review instructions for every generated item.
_PRODUCT_CONSISTENCY_LOCK = (
    "Use all uploaded product reference images as strict visual source of truth. "
    "Keep the same silhouette, structure, dimensions, color, material, texture, "
    "finish, packaging, logo, printed text, pattern, accessories, edge shape and "
    "scale relationship. Do not invent logos, labels, certifications, claims, "
    "accessories, buttons, decorations, stitches, straps, caps, handles, seams, "
    "textures or package copy; when a detail is unclear, preserve the visible "
    "design instead of hallucinating a new one. Product fidelity outranks styling."
)
_QUALITY_LOCK = (
    "Premium commercial product photography, 8K high-definition look, crisp focus, "
    "fine texture detail, realistic shadows, accurate perspective, clean edges, "
    "no blur, watermark or compression artifacts, and no distorted product geometry."
)
_NEGATIVE_CONSTRAINTS = (
    "No watermark, fake logo, changed product structure or ratio, changed package "
    "text, fake certification, sales data, medical efficacy wording, absolute "
    "guarantee, dense small text, hard-sell button copy, blocking props or low-"
    "resolution artifacts."
)
_REFERENCE_LOADING = (
    "Progressive reference loading: first establish the primary product reference "
    "and shared identity anchors; then use detail/packaging references for visible "
    "details; use model, scene or style references only for their requested role. "
    "If references conflict, keep the most complete front-facing product and use "
    "other images only for details or environment. Never let a reference replace "
    "product identity."
)
_QA_CHECKLIST = (
    "Post-generation QA: same SKU silhouette, structure, color, material, logo, "
    "label/text impression and accessories; natural scale and perspective; no "
    "missing/extra parts; props do not block the product; no fake badge, claim, "
    "review, price, UI or watermark; apparel keeps cut, collar, sleeves, buttons, "
    "pockets, hem and fit; packaging keeps logo, color blocks and layout."
)
_RETRY_STRATEGY = (
    "If QA finds product drift, make at most one conservative retry: reduce "
    "creativity, use a white/gray studio background, keep the original camera "
    "angle, remove props/model, and strengthen exact visible details. If text is "
    "unstable, avoid adding text and preserve its visual impression. Retry must "
    "reuse the original product references and a new attempt idempotency key."
)

_PLATFORM_DEFAULTS = {
    "tmall": (
        "Taobao/Tmall default: 1:1 premium commercial image; product 60–82% of "
        "canvas, crop-safe margins, no promotional text unless requested."
    ),
    "jd": (
        "JD default: clean conversion-oriented commercial image; product remains "
        "dominant and readable with safe crop margins."
    ),
    "douyin": (
        "TikTok/Douyin Shop default: high-attention mobile scene with clear "
        "silhouette; keep product large and avoid fake platform UI."
    ),
    "xiaohongshu": (
        "Xiaohongshu default: natural-light lifestyle/editorial atmosphere; props "
        "support mood without hiding SKU details."
    ),
    "amazon-us": (
        "Amazon default: clean product-first image; white/very clean background "
        "for the main image, no badges, ratings, reviews, price or claims."
    ),
    "generic": (
        "Generic channel default: polished commercial product image with a clear "
        "visual hierarchy and product-led composition."
    ),
}

_TASK_RECIPES = {
    "scene_main": (
        "Create a 1:1 ecommerce main scene image. Change only scene, lighting, "
        "surface and supporting props; center the product at 60–82% scale with "
        "crop-safe margin and no extra promotional text."
    ),
    "white_background": (
        "Create a pure #FFFFFF product image with subtle natural contact shadow. "
        "Change background only; keep complete outline, 8–12% safety margin, "
        "logo, package text, structure and color unchanged."
    ),
    "model_image": (
        "Create a realistic ecommerce model display image. Generate model and scene "
        "only; keep the product visible and preserve apparel/shoe/bag cut, length, "
        "hardware, print, color, fabric and fit."
    ),
    "change_model": (
        "Replace only model identity, pose, age impression or styling mood requested. "
        "Keep product, garment fit, drape, silhouette, buttons, color, fabric and "
        "visible details unchanged."
    ),
    "try_on": (
        "Place the reference product naturally on the model. Preserve cut, structure, "
        "color, pattern, material, length, accessories and scale; do not redesign "
        "the SKU."
    ),
    "change_scene": (
        "Swap only environment, lighting, surface and supporting props to match the "
        "category and buyer use case. Product shape, color, logo, labels and scale "
        "must stay unchanged."
    ),
    "change_background": (
        "Replace background only with the requested or clean premium studio "
        "background. "
        "Keep product centered and complete; preserve edges, text, logo, color and "
        "scale."
    ),
    "change_color": (
        "Change only the specified color area to the target color. Preserve material "
        "texture, highlights, logo, printed text, hardware, stitching, structure, "
        "packaging and all non-target colors."
    ),
}

PRODUCT_IMAGE_PRESETS = frozenset(
    {
        "scene_main",
        "white_background",
        "model_image",
        "change_model",
        "try_on",
        "change_scene",
        "change_background",
        "change_color",
    }
)


class ProductImageProvider(NewImageSkillProvider):
    """Authorize and delegate one product-image JobItem."""

    operation = "product_image"
    skill_id = "xinghe-wanneng-shengtu-3-0"

    def validate_configuration(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> None:
        del item_config
        product_asset_ids = require_asset_ids(input_config, "product_asset_ids")
        reference_asset_ids = validate_optional_asset_ids(
            input_config, "reference_asset_ids"
        )
        role_labelled_assets(
            ("product_reference", product_asset_ids),
            ("style_reference", reference_asset_ids),
        )
        require_image_count(settings)
        controlled_target(settings)
        task_type = require_non_empty_string(
            settings,
            "task_type",
            code="PRODUCT_IMAGE_PRESET_REQUIRED",
        )
        if task_type not in PRODUCT_IMAGE_PRESETS:
            raise NewImageSkillContractError(
                "PRODUCT_IMAGE_PRESET_NOT_SUPPORTED",
                "The requested product-image preset is not supported.",
            )
        optional_string(settings, "platform_code", default="tmall")
        optional_string(settings, "instruction")
        if task_type == "change_color":
            require_non_empty_string(
                settings,
                "target_color",
                code="TARGET_COLOR_REQUIRED",
            )
        elif "target_color" in settings and settings["target_color"] is not None:
            optional_string(settings, "target_color")

    def source_assets(
        self,
        input_config: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        return role_labelled_assets(
            (
                "product_reference",
                require_asset_ids(input_config, "product_asset_ids"),
            ),
            (
                "style_reference",
                validate_optional_asset_ids(input_config, "reference_asset_ids"),
            ),
        )

    def prompt_input(
        self,
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        del item_config
        task_type = require_non_empty_string(
            settings,
            "task_type",
            code="PRODUCT_IMAGE_PRESET_REQUIRED",
        )
        prompt: dict[str, Any] = {
            "task_type": task_type,
            "platform_code": optional_string(
                settings, "platform_code", default="tmall"
            ),
            "reference_guidance": optional_string(settings, "instruction"),
        }
        target_color = settings.get("target_color")
        if target_color is not None:
            prompt["target_color"] = optional_string(settings, "target_color")
        return compact_prompt_input(prompt)
