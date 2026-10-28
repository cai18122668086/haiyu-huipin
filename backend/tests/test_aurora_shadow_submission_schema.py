from __future__ import annotations

from app.schemas_v1 import DomesticMainImageConfiguration


def test_domestic_main_image_accepts_opt_in_shadow_mode_and_defaults_off() -> None:
    base = {
        "product_name": "儿童电动越野车",
        "category": "儿童玩具车",
        "selling_points": ["12V 电瓶", "双座", "遥控操作"],
    }

    assert DomesticMainImageConfiguration(**base).aurora_shadow_mode == "off"
    assert (
        DomesticMainImageConfiguration(
            **base,
            aurora_shadow_mode="contract_first_rebuild_v1",
        ).aurora_shadow_mode
        == "contract_first_rebuild_v1"
    )
    assert (
        DomesticMainImageConfiguration(
            **base,
            aurora_shadow_mode="execution_semantics_v1",
        ).aurora_shadow_mode
        == "execution_semantics_v1"
    )
