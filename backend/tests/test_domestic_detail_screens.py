from __future__ import annotations

from copy import deepcopy

import pytest

from app.domestic_detail.contracts import normalize_native_plan_for_harness


@pytest.mark.parametrize(
    "family,briefs",
    [
        ("FRAMED_LIFESTYLE_PLUS_CARDS", ["真实场景", "动作证据", "结果局部"]),
        ("CENTRAL_OBJECT_ORBIT", ["商品部件", "环绕关系", "结果锚点"]),
        ("STACKED_BANDS", ["上部使用动作", "下部结果"]),
    ],
)
def test_authored_spatial_blueprint_is_not_replaced_by_screen_index(
    family: str,
    briefs: list[str],
) -> None:
    structure = {"structure_family": family, "zone_briefs": briefs}
    plan = {
        "modules": [
            {
                "id": "M01",
                "story_function": "HOOK",
                "structure": structure,
                "module_spatial_structure_contract": deepcopy(structure),
            }
        ]
    }
    normalized = normalize_native_plan_for_harness(plan)
    assert normalized["modules"][0]["structure"] == structure
    assert normalized["modules"][0]["module_spatial_structure_contract"] == structure
    assert plan["modules"][0]["structure"] == structure


@pytest.mark.parametrize("count", [8, 10, 12])
def test_closure_role_follows_authored_story_instead_of_fixed_eighth_screen(
    count: int,
) -> None:
    modules = [
        {"id": f"M{index:02d}", "story_function": "MECHANISM_PROOF"}
        for index in range(1, count + 1)
    ]
    modules[0]["story_function"] = "HOOK"
    modules[-1]["story_function"] = "PURCHASE_CLOSURE"
    normalized = normalize_native_plan_for_harness({"modules": modules})
    roles = [module["information_role"] for module in normalized["modules"]]
    assert roles[0] == "IDENTITY"
    assert roles[-1] == "CLOSURE"
    assert roles.count("CLOSURE") == 1
    if count > 8:
        assert roles[7] == "MECHANISM_OR_STRUCTURE"


def test_explicit_information_role_is_never_overwritten() -> None:
    plan = {
        "modules": [
            {
                "id": "M02",
                "story_function": "USE_OR_RESULT",
                "information_role": "RESULT_OR_EXPERIENCE",
            }
        ]
    }
    normalized = normalize_native_plan_for_harness(plan)
    assert normalized["modules"][0]["information_role"] == "RESULT_OR_EXPERIENCE"
