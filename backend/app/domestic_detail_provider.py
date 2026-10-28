"""Independent adapter for the newly installed domestic detail skill."""

from __future__ import annotations

from collections.abc import Mapping
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

DOMESTIC_DETAIL_SKILL_ID = "aurora-domestic-ecommerce-detail-page"
DOMESTIC_MARKET_REGION = "domestic"
DOMESTIC_DEFAULT_PLATFORM = "taobao_tmall"
DOMESTIC_DEFAULT_SIZE = "1440x1920"
DOMESTIC_PAIR_CANVAS_SIZE = "1440x3840"
DOMESTIC_PAIR_CANVAS_ASPECT_RATIO = "3:8"
DOMESTIC_SCREEN_ASPECT_RATIO = "3:4"
DOMESTIC_ALLOWED_PAGE_COUNTS = frozenset({8, 10, 12})

_DOMESTIC_SUPPORTED_SIZES = frozenset(
    {
        "750x1000",
        DOMESTIC_DEFAULT_SIZE,
    }
)

_DOMESTIC_PLATFORM_ALIASES = {
    "taobao": "taobao_tmall",
    "tmall": "taobao_tmall",
    "taobao_tmall": "taobao_tmall",
    "\u6dd8\u5b9d": "taobao_tmall",
    "\u5929\u732b": "taobao_tmall",
    "\u6dd8\u5b9d/\u5929\u732b": "taobao_tmall",
    "jd": "jd",
    "jingdong": "jd",
    "\u4eac\u4e1c": "jd",
    "pdd": "pinduoduo",
    "pinduoduo": "pinduoduo",
    "\u62fc\u591a\u591a": "pinduoduo",
    "douyin": "douyin",
    "douyin_ecommerce": "douyin",
    "\u6296\u97f3": "douyin",
    "\u6296\u97f3\u7535\u5546": "douyin",
    "xiaohongshu": "xiaohongshu",
    "\u5c0f\u7ea2\u4e66": "xiaohongshu",
    "other": "other_domestic",
    "other_domestic": "other_domestic",
    "\u5176\u4ed6\u56fd\u5185\u5e73\u53f0": "other_domestic",
}


def resolve_domestic_detail_size(configuration: Mapping[str, Any]) -> str:
    """Resolve the internal 3:4 delivery slice profile.

    Aurora renders each adjacent pair on one 3:8 canvas and deterministically
    decomposes it into two slices.  The 3:4 value is therefore a delivery
    slice, not the generation request.
    """

    requested_size = configuration.get("size")
    if requested_size is None:
        return DOMESTIC_DEFAULT_SIZE
    size = str(requested_size).strip().lower()
    if size not in _DOMESTIC_SUPPORTED_SIZES:
        raise AdapterValidationError(
            "DETAIL_SIZE_UNSUPPORTED",
            "Domestic detail delivery slice must use an approved 3:4 profile.",
        )
    width_text, height_text = size.split("x", maxsplit=1)
    if int(width_text) * 4 != int(height_text) * 3:
        raise AdapterValidationError(
            "DETAIL_SIZE_ASPECT_RATIO_MISMATCH",
            "Domestic detail delivery slice must use the strict 3:4 aspect ratio.",
        )
    return size


def domestic_detail_prompt_configuration(
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    """Build canonical domestic planner defaults without mutating user input."""

    requested_platform = normalized_text(
        configuration.get("platform_code", DOMESTIC_DEFAULT_PLATFORM)
    )
    platform_code = _DOMESTIC_PLATFORM_ALIASES.get(requested_platform)
    if platform_code is None:
        raise AdapterValidationError(
            "DETAIL_PLATFORM_UNSUPPORTED",
            f"Unsupported domestic platform: {requested_platform}.",
        )
    resolved_size = resolve_domestic_detail_size(configuration)
    page_count = requested_page_count(configuration)
    if page_count not in DOMESTIC_ALLOWED_PAGE_COUNTS:
        raise AdapterValidationError(
            "DETAIL_PAGE_COUNT_UNSUPPORTED",
            "Aurora domestic detail pages must contain exactly 8, 10, or 12 screens.",
        )
    width_text, height_text = resolved_size.split("x", maxsplit=1)
    return {
        "profile_id": f"domestic_{platform_code}_v1",
        "platform_code": platform_code,
        "language": requested_language(configuration, "zh-CN"),
        "requested_page_count": page_count,
        "default_page_count": 8,
        "screen_aspect_ratio": DOMESTIC_SCREEN_ASPECT_RATIO,
        "pair_aspect_ratio": DOMESTIC_PAIR_CANVAS_ASPECT_RATIO,
        "pair_canvas_size": f"{int(width_text)}x{int(height_text) * 2}",
        "platform_profile": {
            "screen_width": int(width_text),
            "screen_height": int(height_text),
            "pair_canvas_width": int(width_text),
            "pair_canvas_height": int(height_text) * 2,
            "safe_margin": "profile_defined_by_platform",
            "format": "PNG",
            "color_space": "sRGB",
            "verification_status": "BACKEND_CONFIRMATION_REQUIRED",
            "source": "INTERNAL_REVIEW_PROFILE",
        },
        "target_size": {
            "mode": "exact",
            "width": int(width_text),
            "height": int(height_text),
            "aspect_ratio": "3:4",
        },
        "resolved_size": resolved_size,
        "module_rule": "one_buyer_demand_per_screen",
        "pair_rule": "adjacent_two_screen_pair_plan_merge",
        "pair_count": page_count // 2,
        "pair_generation": "one_3_8_canvas_then_deterministic_two_3_4_slices",
        "independent_page_generation": False,
        "reference_edit_concurrency": 1,
        "prompt_only_concurrency": 4,
        "batch_failure_policy": "fail_closed_serial_drain_once",
        "pixel_audit": {
            "entrypoint": "audit-pair",
            "required": True,
            "actual_pixels": True,
            "max_automatic_repairs": 1,
            "second_failure": "COMPLETE_WITH_RESIDUALS",
        },
        "approval_gate": {
            "required": True,
            "scope": "campaign_approval_group_to_screen_packet",
            "auto_confirm_after_validation": configuration.get(
                "auto_start_after_plan"
            ) is True,
        },
    }


class DomesticDetailProvider(NewDetailSkillProvider):
    """Manifest-gated provider selected only for ``market_region=domestic``."""

    def __init__(
        self,
        delegate: Provider,
        *,
        manifest_validator: ManifestValidator | None = None,
    ) -> None:
        super().__init__(
            delegate,
            DetailSkillRoute(
                skill_id=DOMESTIC_DETAIL_SKILL_ID,
                market_region=DOMESTIC_MARKET_REGION,
                configure_prompt=domestic_detail_prompt_configuration,
            ),
            manifest_validator=manifest_validator,
        )
