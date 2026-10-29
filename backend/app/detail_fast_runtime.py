"""Version selection for independent domestic detail."""

from collections.abc import Mapping
from typing import Any

from . import detail_fast, refined_detail


def is_fast(configuration: Mapping[str, Any]) -> bool:
    return configuration.get("domestic_detail_pipeline") == detail_fast.PIPELINE_VERSION


def is_independent(configuration: Mapping[str, Any]) -> bool:
    return is_fast(configuration) or refined_detail.is_refined(configuration)


def contract(configuration: Mapping[str, Any]) -> Any:
    return detail_fast if is_fast(configuration) else refined_detail


def skill_contract(skill_id: str | None) -> Any:
    return detail_fast if skill_id == detail_fast.SKILL_ID else refined_detail


def independent_skill(skill_id: str | None) -> bool:
    return skill_id in {detail_fast.SKILL_ID, refined_detail.SKILL_ID}
