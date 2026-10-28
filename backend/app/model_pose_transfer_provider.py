"""Provider adapter for pose-only transfer between two model images."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.new_image_skill_provider import (
    NewImageSkillContractError,
    NewImageSkillProvider,
    compact_prompt_input,
    controlled_target,
    optional_string,
    role_labelled_assets,
)


class ModelPoseTransferProvider(NewImageSkillProvider):
    """Bind the source model first and the pose-only reference second."""

    operation = "model_pose_transfer"
    skill_id = "ecommerce-model-pose-transfer"

    def validate_configuration(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> None:
        del item_config
        model_source_asset_id = _require_single_asset_id(
            input_config, "model_source_asset_id"
        )
        pose_reference_asset_id = _require_single_asset_id(
            input_config, "pose_reference_asset_id"
        )
        role_labelled_assets(
            ("model_source", (model_source_asset_id,)),
            ("pose_reference", (pose_reference_asset_id,)),
        )
        _catalog_reference(input_config)
        if settings.get("image_count") != 1:
            raise NewImageSkillContractError(
                "IMAGE_COUNT_OUT_OF_RANGE",
                "model pose transfer requires exactly one output image.",
            )
        controlled_target(
            settings,
            default_aspect_ratio="follow-source",
            allow_follow_source=True,
        )
        optional_string(settings, "instruction")

    def build_adapter_envelope(
        self,
        input_config: Mapping[str, Any],
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
        *,
        content_hash: str,
    ) -> dict[str, Any]:
        envelope = super().build_adapter_envelope(
            input_config,
            settings,
            item_config,
            content_hash=content_hash,
        )
        pose_slug, pose_reference_sha256 = _catalog_reference(input_config)
        envelope["prompt_input"] = compact_prompt_input(
            {
                **envelope["prompt_input"],
                "pose_slug": pose_slug or "",
                "pose_reference_sha256": pose_reference_sha256 or "",
            }
        )
        return envelope

    def source_assets(
        self,
        input_config: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        return role_labelled_assets(
            (
                "model_source",
                (_require_single_asset_id(input_config, "model_source_asset_id"),),
            ),
            (
                "pose_reference",
                (_require_single_asset_id(input_config, "pose_reference_asset_id"),),
            ),
        )

    def target(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        return controlled_target(
            settings,
            default_aspect_ratio="follow-source",
            allow_follow_source=True,
        )

    def prompt_input(
        self,
        settings: Mapping[str, Any],
        item_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        del item_config
        return compact_prompt_input(
            {"user_instruction": optional_string(settings, "instruction")}
        )


def _require_single_asset_id(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NewImageSkillContractError(
            "REQUIRED_IMAGE_INPUT_MISSING", f"{key} is required."
        )
    return value.strip()


_POSE_SLUG_PATTERN = re.compile(r"(?:male|female)-[0-9]{3}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _catalog_reference(
    source: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    if "reference_sha256" in source:
        raise NewImageSkillContractError(
            "MODEL_POSE_INPUT_FIELD_NOT_ALLOWED",
            "Use pose_reference_sha256 for the model pose Job input.",
        )
    pose_slug = source.get("pose_slug")
    digest = source.get("pose_reference_sha256")
    if pose_slug is None and digest is None:
        return None, None
    if not isinstance(pose_slug, str) or not isinstance(digest, str):
        raise NewImageSkillContractError(
            "MODEL_POSE_CATALOG_REFERENCE_INVALID",
            "pose_slug and pose_reference_sha256 must be provided together.",
        )
    normalized_slug = pose_slug.strip()
    normalized_digest = digest.strip().lower()
    slug_valid = _POSE_SLUG_PATTERN.fullmatch(normalized_slug)
    digest_valid = _SHA256_PATTERN.fullmatch(normalized_digest)
    if not slug_valid or not digest_valid:
        raise NewImageSkillContractError(
            "MODEL_POSE_CATALOG_REFERENCE_INVALID",
            "The catalog pose slug or original asset hash is invalid.",
        )
    return normalized_slug, normalized_digest
