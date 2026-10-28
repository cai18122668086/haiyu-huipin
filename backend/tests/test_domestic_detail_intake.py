from copy import deepcopy

import pytest

from app.domestic_detail.facts import (
    DetailIntakeError,
    user_fact_ledger,
    validate_intake,
)

CONFIRMATION = "确认这个合成测试商品没有品牌，不添加独立标志"
SETTINGS = {
    "selling_points": ["  用户给定的原始卖点  "],
    "verified_facts": ["容量 500 ml"],
}
SOURCES = [
    {"asset_id": "product-1", "role": "product_reference", "sha256": "a" * 64},
    {"asset_id": "style-1", "role": "style_reference", "sha256": "b" * 64},
]


def intake():
    features = [
        "透明圆柱瓶身与扁圆瓶盖",
        "可见瓶身高度约为宽度两倍",
        "一个瓶盖与一段直立瓶身",
        "瓶身透明玻璃而瓶盖呈银灰色",
    ]
    methods = ["左上方短标题与右侧竖直产品共同形成不对称构图"]
    return {
        "version": "domestic-detail-intake-v1",
        "conflict_scan": {
            "status": "CLEAR",
            "scanned_topics": ["identity", "specification", "performance"],
            "conflict_ids": [],
            "scanned_source_refs": ["product-1", "F01", "F02"],
        },
        "source_conflicts": [],
        "observed_facts": [
            {
                "id": "O01",
                "text": "瓶身呈透明圆柱状",
                "source_class": "OBSERVED_PRODUCT_EVIDENCE",
                "evidence_ref": "product-1",
                "observed_region": "瓶身",
                "claim_kind": "PRODUCT_IDENTITY",
                "display_intent": "MUST_SHOW",
                "user_prominence": "SUPPORTING_POINT",
                "named_elements": ["透明瓶身"],
            }
        ],
        "identity": {
            "canonical_name": "合成测试瓶",
            "category": "容器",
            "reference_image_ids": ["product-1"],
            "invariant_features": features,
            "allowed_states": ["直立展示"],
            "uncertain_features": [],
            "forbidden_mutations": ["不得增加第二个瓶盖"],
            "brand_logo_source": {
                "mode": "BRANDLESS",
                "image_id": "NONE",
                "authorization_basis": "NOT_APPLICABLE",
                "readability": "NOT_APPLICABLE",
                "usage": "FORBIDDEN_ALL_SCREENS",
                "brandless_evidence": CONFIRMATION,
            },
        },
        "identity_observations": [
            {
                "kind": kind,
                "source_asset_id": "product-1",
                "region": "正面",
                "text": text,
            }
            for kind, text in zip(
                ("silhouette", "proportions", "parts", "color_material"),
                features,
                strict=True,
            )
        ],
        "reference_materials": [
            {
                "id": "style-1",
                "path": "asset:style-1",
                "category": "style_reference",
                "analysis_status": "ANALYZED",
                "observed_expression_methods": methods,
                "analyzed_units": [
                    {
                        "id": "style-1-U1",
                        "source_region": "画面左上与右侧",
                        "page_duty": "形成标题与主体的主次",
                        "visual_carrier": "竖直主体与留白",
                        "composition_logic": "右侧主体与左侧短标题相对",
                        "typography_logic": "短标题沿左侧上沿排列",
                        "observed_expression_method": methods[0],
                    }
                ],
            }
        ],
    }


def validate(value, **kwargs):
    return validate_intake(
        value,
        settings=SETTINGS,
        sources=SOURCES,
        brandless_confirmation=CONFIRMATION,
        **kwargs,
    )


def test_user_facts_keep_verbatim_text_and_original_source_positions():
    rows = user_fact_ledger(SETTINGS)
    assert [row["text"] for row in rows] == SETTINGS["selling_points"] + SETTINGS[
        "verified_facts"
    ]
    assert [row["evidence_ref"] for row in rows] == [
        "settings/selling_points/0",
        "settings/verified_facts/0",
    ]
    assert all(row["source_class"] == "USER_FACT" for row in rows)


def test_valid_observations_are_detached_and_have_no_invented_truth():
    original = intake()
    result = validate(original)
    assert result["identity"] == original["identity"]
    result["identity"]["category"] = "changed"
    assert original["identity"]["category"] == "容器"


@pytest.mark.parametrize("ref", ["style-1", "generated-1", "unknown-image"])
def test_style_generated_and_unknown_sources_never_become_product_facts(ref):
    value = intake()
    value["observed_facts"][0]["evidence_ref"] = ref
    with pytest.raises(DetailIntakeError, match="product source"):
        validate(value)


@pytest.mark.parametrize(
    "change",
    [
        "missing_scan",
        "incomplete_sources",
        "missing_reference",
        "policy_geometry",
        "malformed_geometry",
        "mismatched_identity",
    ],
)
def test_missing_or_optimistic_intake_is_rejected(change):
    value = intake()
    if change == "missing_scan":
        del value["conflict_scan"]
    elif change == "incomplete_sources":
        value["conflict_scan"]["scanned_source_refs"] = ["product-1"]
    elif change == "missing_reference":
        value["reference_materials"] = []
    elif change == "policy_geometry":
        value["identity_observations"][0]["text"] = "所有结构严格以用户商品参考图为准"
    elif change == "malformed_geometry":
        value["identity_observations"].append("not an observation")
    else:
        value["identity"]["invariant_features"][0] = "different geometry"
    with pytest.raises(DetailIntakeError):
        validate(value)


def test_brandless_does_not_follow_from_missing_brand_assets():
    with pytest.raises(DetailIntakeError, match="explicit words"):
        validate_intake(intake(), settings=SETTINGS, sources=SOURCES)
    value = intake()
    value["identity"]["brand_logo_source"]["brandless_evidence"] = "自动认定无品牌"
    with pytest.raises(DetailIntakeError, match="original confirmation"):
        validate(value)


def test_readable_short_brand_requires_its_real_product_source():
    value = intake()
    value["identity"]["brand_logo_source"] = {
        "mode": "BRANDED",
        "image_id": "product-1",
        "authorization_basis": "USER_SUPPLIED_CURRENT_PRODUCT",
        "readability": "READABLE",
        "usage": "FIRST_SCREEN_INDEPENDENT_LOGO",
        "observed_logo_text": "某牌",
        "observed_region": "瓶盖",
    }
    validate(value)
    value["identity"]["brand_logo_source"]["image_id"] = "style-1"
    with pytest.raises(DetailIntakeError, match="current product"):
        validate(value)


def conflict_intake():
    value = intake()
    value["source_conflicts"] = [
        {
            "id": "C01",
            "topic": "容量",
            "alternative_fact_ids": ["F01", "F02"],
            "why_material": "两项给定容量不能同时描述同一销售规格",
            "status": "UNRESOLVED",
        }
    ]
    value["conflict_scan"].update(status="CONFLICTS_FOUND", conflict_ids=["C01"])
    del value["identity"]
    del value["identity_observations"]
    return value


def test_material_conflict_returns_all_sources_before_identity_or_plan():
    result = validate(conflict_intake())
    assert result["source_conflicts"][0]["alternative_fact_ids"] == ["F01", "F02"]
    assert "identity" not in result and "plan" not in result
    value = conflict_intake()
    value["identity"] = deepcopy(intake()["identity"])
    with pytest.raises(DetailIntakeError, match="must wait"):
        validate(value)


def test_model_cannot_silently_resolve_conflicting_values():
    value = conflict_intake()
    value["conflict_scan"]["status"] = "CLEAR"
    value["source_conflicts"][0]["status"] = "RESOLVED"
    with pytest.raises(DetailIntakeError, match="silently resolve"):
        validate(value)


@pytest.mark.parametrize(
    "change", ["scan_ref_type", "reference_policy", "reference_method", "user_identity"]
)
def test_intake_does_not_repair_bad_evidence_optimistically(change):
    value = intake()
    if change == "scan_ref_type":
        value["conflict_scan"]["scanned_source_refs"] = [{"not": "a source"}]
    elif change == "reference_policy":
        value["reference_materials"][0]["analyzed_units"][0]["composition_logic"] = (
            "构图严格以用户参考图为准"
        )
    elif change == "reference_method":
        value["reference_materials"][0]["analyzed_units"][0][
            "observed_expression_method"
        ] = "底部沿横向排列的三个分解结构形成连续阅读路径"
    else:
        with pytest.raises(DetailIntakeError, match="silently renamed"):
            validate_intake(
                value,
                settings={**SETTINGS, "product_name": "用户给定名称"},
                sources=SOURCES,
                brandless_confirmation=CONFIRMATION,
            )
        return
    with pytest.raises(DetailIntakeError):
        validate(value)


def test_duplicate_conflict_ids_cannot_collapse_the_source_question():
    value = conflict_intake()
    value["source_conflicts"].append(deepcopy(value["source_conflicts"][0]))
    with pytest.raises(DetailIntakeError, match="identifiers must be unique"):
        validate(value)


def test_one_fact_cannot_be_retained_and_rejected_in_different_conflicts():
    from app.domestic_detail.intake import validate_resolution

    value = conflict_intake()
    second = {**deepcopy(value["source_conflicts"][0]), "id": "C02"}
    value["source_conflicts"].append(second)
    resolution = {
        "confirmation_text": "确认同一批规格取舍",
        "resolutions": [
            {
                "conflict_id": "C01",
                "retained_fact_ids": ["F01"],
                "rejected_fact_ids": ["F02"],
            },
            {
                "conflict_id": "C02",
                "retained_fact_ids": ["F02"],
                "rejected_fact_ids": ["F01"],
            },
        ],
    }
    with pytest.raises(DetailIntakeError, match="both retained and rejected"):
        validate_resolution(value, resolution)
    resolution["resolutions"][1].update(
        retained_fact_ids=["F01"], rejected_fact_ids=["F02"]
    )
    assert validate_resolution(value, resolution) == resolution


@pytest.mark.parametrize(
    "source_class,evidence_ref",
    [
        ("OBSERVED_PRODUCT_EVIDENCE", "product-1"),
        ("USER_FACT", "product-1"),
        ("OBSERVED_PRODUCT_EVIDENCE", "style-1"),
    ],
)
def test_model_observation_contract_matches_runtime_source_boundary(
    source_class,
    evidence_ref,
):
    from jsonschema import Draft202012Validator

    from app.domestic_detail.intake import intake_response_schema

    value = intake()
    value["brand_confirmation_required"] = False
    value["observed_facts"][0].update(
        source_class=source_class,
        evidence_ref=evidence_ref,
    )
    schema = intake_response_schema(["product-1"])
    valid = source_class == "OBSERVED_PRODUCT_EVIDENCE" and evidence_ref == "product-1"
    assert Draft202012Validator(schema).is_valid(value) is valid
    if valid:
        validate_intake(
            value,
            settings=SETTINGS,
            sources=SOURCES,
            brandless_confirmation=CONFIRMATION,
        )
    else:
        with pytest.raises(DetailIntakeError) as error:
            validate_intake(
                value,
                settings=SETTINGS,
                sources=SOURCES,
                brandless_confirmation=CONFIRMATION,
            )
        assert error.value.code == "INTAKE_FACT_SOURCE_INVALID"


@pytest.mark.parametrize(
    "tamper", [None, "absent", "missing_fact", "duplicate", "unknown_source"]
)
def test_model_scan_schema_requires_the_exact_complete_source_inventory(tamper):
    from jsonschema import Draft202012Validator

    from app.domestic_detail.intake import intake_response_schema

    value = intake()
    value["brand_confirmation_required"] = False
    expected = list(value["conflict_scan"]["scanned_source_refs"])
    facts = [ref for ref in expected if ref != "product-1"]
    schema = intake_response_schema(["product-1"], facts)
    assert "scanned_source_refs" in schema["properties"]["conflict_scan"]["required"]
    if tamper == "absent":
        value["conflict_scan"].pop("scanned_source_refs")
    elif tamper == "missing_fact":
        value["conflict_scan"]["scanned_source_refs"].pop()
    elif tamper == "duplicate":
        value["conflict_scan"]["scanned_source_refs"][0] = value["conflict_scan"][
            "scanned_source_refs"
        ][1]
    elif tamper == "unknown_source":
        value["conflict_scan"]["scanned_source_refs"][0] = "another-product"
    assert Draft202012Validator(schema).is_valid(value) is (tamper is None)
