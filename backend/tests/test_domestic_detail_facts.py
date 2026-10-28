from __future__ import annotations

import pytest

from app.domestic_detail.contracts import normalize_native_plan_for_harness
from app.domestic_detail.facts import text_tokens


@pytest.mark.parametrize("value,expected", [
    ("圆角矩形截面", ["圆角矩形截面"]),
    (["圆角矩形截面", "宽高比1:1.6"], ["圆角矩形截面", "宽高比1:1.6"]),
    ((" 真实材质 ", ""), ["真实材质"]),
    (None, []), ("  ", []),
])
def test_tokens_preserve_complete_semantics(value: object, expected: list[str]) -> None:
    assert text_tokens(value, field="identity") == expected


@pytest.mark.parametrize("value", [7, True, {"fact": "shape"}, ["valid", 7]])
def test_invalid_tokens_are_rejected_instead_of_becoming_facts(value: object) -> None:
    with pytest.raises(ValueError, match="identity"):
        text_tokens(value, field="identity")


def test_scalar_identity_and_photography_do_not_become_characters() -> None:
    plan = {
        "modules": [],
        "series_visual_dna": {
            "identity_geometry": "圆角矩形截面，宽高比1:1.6",
            "photography_tokens": "保留拉丝金属表面与真实主光方向",
        },
    }
    normalized = normalize_native_plan_for_harness(plan)
    dna = normalized["series_visual_dna"]
    assert dna["product_tokens"] == [
        "圆角矩形截面，宽高比1:1.6", "保留拉丝金属表面与真实主光方向",
    ]
    assert dna["invariants"][0] == "圆角矩形截面，宽高比1:1.6"
    assert "product_tokens" not in plan["series_visual_dna"]
    assert normalize_native_plan_for_harness(normalized) == normalized
