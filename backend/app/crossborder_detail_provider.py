"""Independent adapter for the newly installed cross-border detail skill."""

from __future__ import annotations

from collections.abc import Mapping
from math import gcd
from typing import Any

from app.new_detail_skill_provider import (
    AdapterValidationError,
    DetailSkillRoute,
    ManifestValidator,
    NewDetailSkillProvider,
    normalized_text,
    requested_language,
    requested_page_count,
)
from app.provider import Provider

CROSSBORDER_DETAIL_SKILL_ID = "xinghe-crossborder-detail-3"
INTERNATIONAL_MARKET_REGION = "international"
CROSSBORDER_DEFAULT_PLATFORM = "generic_crossborder_pdp"
CROSSBORDER_DEFAULT_SIZE = "2048x1152"
RUSSIAN_VERTICAL_DEFAULT_SIZE = "900x1200"

_CROSSBORDER_PLATFORM_ALIASES = {
    "amazon": "amazon",
    "shopify": "shopify",
    "temu": "temu",
    "aliexpress": "aliexpress",
    "ali_express": "aliexpress",
    "tiktok_shop": "tiktok_shop",
    "tiktokshop": "tiktok_shop",
    "ozon": "ozon",
    "wildberries": "wildberries",
    "wb": "wildberries",
    "generic": "generic_crossborder_pdp",
    "generic_crossborder_pdp": "generic_crossborder_pdp",
    "\u901a\u7528\u8de8\u5883_pdp": "generic_crossborder_pdp",
    "\u901a\u7528\u8de8\u5883pdp": "generic_crossborder_pdp",
}
_RUSSIAN_VERTICAL_PLATFORMS = {"ozon", "wildberries"}


def _explicit_size(configuration: Mapping[str, Any]) -> tuple[int, int] | None:
    value = configuration.get("size")
    if value is None:
        return None
    if not isinstance(value, str) or "x" not in value.lower():
        raise AdapterValidationError(
            "DETAIL_SIZE_INVALID",
            "Cross-border detail size must use WIDTHxHEIGHT pixels.",
        )
    raw_width, raw_height = value.lower().split("x", maxsplit=1)
    try:
        width, height = int(raw_width.strip()), int(raw_height.strip())
    except ValueError as exc:
        raise AdapterValidationError(
            "DETAIL_SIZE_INVALID",
            "Cross-border detail size must use positive integer dimensions.",
        ) from exc
    if width <= 0 or height <= 0:
        raise AdapterValidationError(
            "DETAIL_SIZE_INVALID",
            "Cross-border detail size must use positive integer dimensions.",
        )
    return width, height


def crossborder_detail_prompt_configuration(
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    """Build platform-aware cross-border planner defaults."""

    requested_platform = normalized_text(
        configuration.get("platform_code", CROSSBORDER_DEFAULT_PLATFORM)
    )
    platform_code = _CROSSBORDER_PLATFORM_ALIASES.get(requested_platform)
    if platform_code is None:
        raise AdapterValidationError(
            "DETAIL_PLATFORM_UNSUPPORTED",
            f"Unsupported cross-border platform: {requested_platform}.",
        )
    is_russian_vertical = platform_code in _RUSSIAN_VERTICAL_PLATFORMS
    default_language = "ru" if is_russian_vertical else "en"
    requested_size = _explicit_size(configuration)
    target_size: dict[str, Any]
    if requested_size is not None:
        width, height = requested_size
        target_size = {
            "mode": "exact",
            "width": width,
            "height": height,
            "aspect_ratio": (
                f"{width // gcd(width, height)}:{height // gcd(width, height)}"
            ),
        }
        resolved_size = f"{width}x{height}"
        aspect_ratio = target_size["aspect_ratio"]
    elif is_russian_vertical:
        target_size = {
            "mode": "exact",
            "width": 900,
            "height": 1200,
            "aspect_ratio": "3:4",
        }
        resolved_size = RUSSIAN_VERTICAL_DEFAULT_SIZE
        aspect_ratio = "3:4"
    else:
        target_size = {
            "mode": "quality_tier",
            "quality": "2K",
            "aspect_ratio": "16:9",
        }
        resolved_size = CROSSBORDER_DEFAULT_SIZE
        aspect_ratio = "16:9"
    return {
        "profile_id": f"crossborder_{platform_code}_v1",
        "platform_code": platform_code,
        "language": requested_language(configuration, default_language),
        "requested_page_count": requested_page_count(configuration),
        "default_page_count": 8,
        "aspect_ratio": aspect_ratio,
        "target_size": target_size,
        "resolved_size": resolved_size,
        # The bundled Xinghe detail generator emits the configured runtime
        # format (PNG); Amazon's JPEG contract is isolated in its own adapter.
        "output_format": "png",
        "quality_tier": (
            "2K" if not is_russian_vertical and requested_size is None else None
        ),
        "delivery": {
            "separate_module_images": True,
            "same_size_across_set": True,
            "final_count": requested_page_count(configuration),
            # The Skill's QA contract is disclosure/selection by default. It
            # must be acknowledged by a human before the archive is released;
            # it never authorizes an automatic redraw loop.
            "qa_status_required": False,
            "manual_review_required": True,
            "qa_mode": "disclosure_and_selection",
        },
        "module_rule": "one_buyer_demand_per_page",
        "independent_page_generation": True,
        "continue_after_single_page_failure": True,
        "successful_page_auto_redraw_allowed": False,
    }


class CrossborderDetailProvider(NewDetailSkillProvider):
    """Manifest-gated provider selected for the new international gray path."""

    def __init__(
        self,
        delegate: Provider,
        *,
        manifest_validator: ManifestValidator | None = None,
    ) -> None:
        super().__init__(
            delegate,
            DetailSkillRoute(
                skill_id=CROSSBORDER_DETAIL_SKILL_ID,
                market_region=INTERNATIONAL_MARKET_REGION,
                configure_prompt=crossborder_detail_prompt_configuration,
            ),
            manifest_validator=manifest_validator,
        )
