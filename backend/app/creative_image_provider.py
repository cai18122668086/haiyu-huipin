"""Provider adapter for ecommerce creative-image generation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.new_image_skill_provider import (
    NewImageSkillContractError,
    NewImageSkillProvider,
    compact_prompt_input,
    controlled_target,
    optional_string,
    optional_string_list,
    require_asset_ids,
    require_image_count,
    role_labelled_assets,
    validate_optional_asset_ids,
)

CREATIVE_DIRECTIONS = frozenset(
    {"selling_point", "pain_point", "scenario", "promotion", "premium", "social"}
)


class CreativeImageProvider(NewImageSkillProvider):
    """Authorize and delegate one independent ecommerce creative image."""

    operation = "creative_image"
    skill_id = "xinghe-ecommerce-creative-image"

    def build_adapter_envelope(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
        *,
        content_hash: str,
    ) -> dict[str, Any]:
        """Bind competitor references to the Inspiration reference planner.

        The standalone workspace sends this flag explicitly, but the adapter
        also enforces the same contract for direct API callers. A competitor
        image must never silently fall through to the generic creative prompt;
        it is a visual-strategy reference and therefore requires the Skill's
        reference-planning stage.
        """
        envelope = super().build_adapter_envelope(
            input_config,
            settings,
            item_config,
            content_hash=content_hash,
        )
        competitor_asset_ids = validate_optional_asset_ids(
            input_config, "competitor_asset_ids"
        )
        if competitor_asset_ids:
            prompt_input = dict(envelope["prompt_input"])
            prompt_input["planning_mode"] = "reference"
            envelope["prompt_input"] = prompt_input
        return envelope

    def validate_configuration(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> None:
        del item_config
        product_asset_ids = require_asset_ids(input_config, "product_asset_ids")
        competitor_asset_ids = validate_optional_asset_ids(
            input_config, "competitor_asset_ids"
        )
        role_labelled_assets(
            ("product_reference", product_asset_ids),
            ("competitor_reference", competitor_asset_ids),
        )
        require_image_count(settings)
        self.target(settings)
        creative_direction = optional_string(
            settings,
            "creative_direction",
            default="selling_point",
        )
        if creative_direction not in CREATIVE_DIRECTIONS:
            raise NewImageSkillContractError(
                "CREATIVE_DIRECTION_NOT_SUPPORTED",
                "The requested creative direction is not supported.",
            )
        optional_string(settings, "platform_code", default="tmall")
        optional_string(settings, "language", default="zh-CN")
        optional_string_list(settings, "selling_points")
        optional_string(settings, "instruction")
        optional_string(settings, "user_instruction")
        optional_string(settings, "negative_constraints")
        inspiration_planning = settings.get("inspiration_planning")
        if inspiration_planning is not None and not isinstance(
            inspiration_planning, bool
        ):
            raise NewImageSkillContractError(
                "INSPIRATION_PLANNING_INVALID",
                "inspiration_planning must be a boolean.",
            )
        inspiration_reference_planning = settings.get(
            "inspiration_reference_planning"
        )
        if inspiration_reference_planning is not None and not isinstance(
            inspiration_reference_planning, bool
        ):
            raise NewImageSkillContractError(
                "INSPIRATION_REFERENCE_PLANNING_INVALID",
                "inspiration_reference_planning must be a boolean.",
            )

    def target(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        # The Inspiration tab's automatic canvas follows the first reference
        # image and falls back to the first product image. The standalone
        # creative-image workspace remains fixed-canvas only.
        return controlled_target(
            settings,
            allow_follow_reference=(
                settings.get("inspiration_planning") is True
                or settings.get("inspiration_reference_planning") is True
            ),
        )

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
                "competitor_reference",
                validate_optional_asset_ids(input_config, "competitor_asset_ids"),
            ),
        )

    def prompt_input(
        self,
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        del item_config
        instruction = optional_string(settings, "instruction")
        return compact_prompt_input(
            {
                "creative_direction": optional_string(
                    settings,
                    "creative_direction",
                    default="selling_point",
                ),
                "platform_code": optional_string(
                    settings, "platform_code", default="tmall"
                ),
                "language": optional_string(settings, "language", default="zh-CN"),
                "product_information": optional_string_list(settings, "selling_points"),
                # The user's brief is an instruction in both standalone and
                # Inspiration modes. It must never be placed in
                # ``negative_constraints`` because that field is reserved for
                # explicit prohibitions.
                "user_instruction": optional_string(settings, "user_instruction")
                or instruction,
                "negative_constraints": optional_string(
                    settings, "negative_constraints"
                ),
                "post_generation_review": optional_string(
                    settings,
                    "post_generation_review",
                    default=(
                        "After generation, manually check product fidelity, "
                        "SKU/packaging text, "
                        "3-second and mobile scan clarity, copy legibility, "
                        "competitor leakage, "
                        "and compliance. If a material defect is visible, "
                        "use one explicit "
                        "conservative retry; never enter an automatic redraw loop."
                    ),
                ),
                "planning_mode": (
                    "reference"
                    if settings.get("inspiration_reference_planning") is True
                    else (
                        "creative"
                        if settings.get("inspiration_planning") is True
                        else ""
                    )
                ),
            }
        )
