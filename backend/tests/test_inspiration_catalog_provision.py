from app.inspiration_legacy_templates import LEGACY_PROMPT_TEMPLATES
from app.inspiration_schemas import InspirationCaseCreate
from scripts.provision_inspiration_catalog import catalog_specifications


def test_provisioning_preserves_all_eighteen_historical_cards_and_order():
    cards = catalog_specifications()
    assert len(cards) == len({card["title"] for card in cards}) == 18
    assert [card["sort_order"] for card in cards] == list(range(18))
    assert [card["slug"] for card in cards[3:6]] == ["detail", "white", "poster"]
    for card in cards:
        InspirationCaseCreate.model_validate(
            {key: value for key, value in card.items() if key != "asset_specs"}
        )


def test_legacy_templates_keep_the_original_prompts_without_pose_library_results():
    cards = {card["title"]: card for card in catalog_specifications()}
    for template in LEGACY_PROMPT_TEMPLATES:
        card = cards[template["title"]]
        assert card["prompt"] == template["prompt"]
        assert card["asset_specs"] == ()
        assert card["workflow_type"] == "product_prompt"
        assert card["reference_source"] == "none"
        assert card["button_label"] == "使用模板"
