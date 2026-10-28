from copy import deepcopy

import pytest

from app.replica_layout import (
    bind_module_text,
    exact_review_context,
    validate_product_evidence,
    validate_reference_layouts,
)


def test_product_transcription_keeps_original_pixels_and_explicit_uncertainty():
    source = [{"sha256": "a" * 64, "width": 1320, "height": 1760}]
    observed = {
        "products": [
            {
                "product_index": 0,
                "source_sha256": "a" * 64,
                "readable_marks": [
                    {
                        "text": "Chicken · liver · egg yolk",
                        "bbox": [100, 1200, 1100, 1600],
                    }
                ],
                "uncertainty_notes": (
                    "Only the large printed ingredient names are legible."
                ),
            }
        ]
    }
    result = validate_product_evidence(observed, source)
    assert result[0]["readable_marks"][0]["bbox"][3] == 1600
    assert result[0]["readable_marks"][0]["text"] == "Chicken · liver · egg yolk"
    observed["products"][0]["readable_marks"] = []
    assert validate_product_evidence(observed, source)[0]["readable_marks"] == []


def test_dense_package_label_observations_are_preserved_without_count_gate():
    marks = [
        {
            "text": f"Printed label row {index}",
            "bbox": [0, index * 10, 100, index * 10 + 8],
        }
        for index in range(75)
    ]
    result = validate_product_evidence(
        {
            "products": [
                {
                    "product_index": 0,
                    "source_sha256": "a" * 64,
                    "readable_marks": marks,
                    "uncertainty_notes": (
                        "Small unreadable text is omitted rather than inferred."
                    ),
                }
            ]
        },
        [{"sha256": "a" * 64, "width": 100, "height": 1000}],
    )
    assert result[0]["readable_marks"] == marks


@pytest.mark.parametrize(
    "bad",
    [
        "hash",
        "missing",
        "duplicate",
        "pixel_bounds",
        "empty_mark",
        "missing_uncertainty",
    ],
)
def test_product_evidence_cannot_substitute_another_source_or_invent_readable_regions(
    bad,
):
    observed = {
        "products": [
            {
                "product_index": 0,
                "source_sha256": "a" * 64,
                "readable_marks": [
                    {"text": "Real printed words", "bbox": [0, 0, 100, 200]}
                ],
                "uncertainty_notes": "Unobserved effects and suppliers remain unknown.",
            }
        ]
    }
    row = observed["products"][0]
    if bad == "hash":
        row["source_sha256"] = "b" * 64
    elif bad == "missing":
        observed["products"] = []
    elif bad == "duplicate":
        observed["products"] *= 2
    elif bad == "pixel_bounds":
        row["readable_marks"][0]["bbox"][3] = 201
    elif bad == "empty_mark":
        row["readable_marks"][0]["text"] = " "
    else:
        row.pop("uncertainty_notes")
    with pytest.raises(ValueError):
        validate_product_evidence(
            observed, [{"sha256": "a" * 64, "width": 100, "height": 200}]
        )


def source_layout():
    regions = [
        ("T1", "headline", "dominant", "line1", "每一次排毛", [40, 110, 950, 230]),
        ("T2", "connector", "small", "line2", "都是一次", [30, 240, 200, 350]),
        ("T3", "headline", "dominant", "line2", "营养补给", [220, 240, 950, 350]),
        ("T4", "badge", "small", "pill", "专注宠物健康", [650, 10, 950, 60]),
    ]
    return {
        "units": [
            {
                "reference_unit_id": "R001",
                "reference_sha256": "a" * 64,
                "reference_product_present": True,
                "has_poster_text": True,
                "layout_anchors": [
                    {"label": "small package", "bbox": [480, 540, 950, 910]}
                ],
                "typography_notes": (
                    "Two dominant lines with a small connector on line two."
                ),
                "text_regions": [
                    dict(
                        zip(
                            (
                                "id",
                                "role",
                                "font_tier",
                                "line_group",
                                "observed_text",
                                "bbox",
                            ),
                            row,
                            strict=True,
                        )
                    )
                    for row in regions
                ],
            }
        ]
    }


def bindings():
    return {
        "M001": {
            "module_id": "M001",
            "reference_sha256": "a" * 64,
            "product_visibility": "visible_product",
            "text_bindings": [
                {
                    "region_id": "T1",
                    "action": "render",
                    "copy_unit_index": 0,
                    "fragment": "三拼冻干",
                },
                {
                    "region_id": "T2",
                    "action": "render",
                    "copy_unit_index": 0,
                    "fragment": "一口",
                },
                {
                    "region_id": "T3",
                    "action": "render",
                    "copy_unit_index": 0,
                    "fragment": "多重营养",
                },
                {
                    "region_id": "T4",
                    "action": "render",
                    "copy_unit_index": 1,
                    "fragment": "全阶段适用",
                },
            ],
        }
    }


MODULE = {
    "id": "M001",
    "reference_unit_id": "R001",
    "copy_units": ["三拼冻干 一口多重营养", "全阶段适用"],
}


def test_two_dominant_lines_keep_small_connector_and_replace_source_capsule():
    layouts = validate_reference_layouts(source_layout(), {"R001": "a" * 64})
    scope = bindings()
    scope["M001"]["text_bindings"][1].update(
        font_tier="dominant", bbox=[0, 0, 1000, 900]
    )
    result = bind_module_text(scope, [MODULE], layouts)["M001"]
    text = result["text_bindings"]
    assert [row["font_tier"] for row in text] == [
        "dominant",
        "small",
        "dominant",
        "small",
    ]
    assert text[1]["line_group"] == text[2]["line_group"] == "line2"
    assert text[1]["bbox"] == [30, 240, 200, 350]
    assert text[3]["render_text"] == "全阶段适用"
    assert all("observed_text" not in row for row in text)


@pytest.mark.parametrize("group", [2, "line2"])
def test_observed_line_group_accepts_numeric_or_named_identifiers(group):
    value = source_layout()
    for region in value["units"][0]["text_regions"][1:3]:
        region["line_group"] = group
    layouts = validate_reference_layouts(value, {"R001": "a" * 64})
    result = bind_module_text(bindings(), [MODULE], layouts)["M001"]
    assert result["text_bindings"][1]["line_group"] == group
    assert result["text_bindings"][2]["line_group"] == group


@pytest.mark.parametrize("group", [True, -1, " ", None])
def test_observed_line_group_rejects_ambiguous_or_empty_identifiers(group):
    value = source_layout()
    value["units"][0]["text_regions"][1]["line_group"] = group
    with pytest.raises(ValueError, match="R001.*T2"):
        validate_reference_layouts(value, {"R001": "a" * 64})


@pytest.mark.parametrize(
    "bad",
    [
        "unapproved_pill",
        "missing_slot",
        "duplicate_slot",
        "omit_headline",
        "wrong_copy_index",
    ],
)
def test_untrusted_copy_mapping_cannot_authorize_source_slogan_or_drop_headline(bad):
    scope = bindings()
    rows = scope["M001"]["text_bindings"]
    if bad == "unapproved_pill":
        rows[-1]["fragment"] = "专注宠物健康"
    elif bad == "missing_slot":
        rows.pop()
    elif bad == "duplicate_slot":
        rows[-1]["region_id"] = "T1"
    elif bad == "omit_headline":
        rows[0].update(
            action="omit_unsupported_copy",
            reason="The source claim has no supporting product facts.",
            retain_carrier=True,
        )
    else:
        rows[-1]["copy_unit_index"] = True
    with pytest.raises(ValueError):
        bind_module_text(scope, [MODULE], {"R001": source_layout()["units"][0]})


@pytest.mark.parametrize(
    "bad", ["source_hash", "duplicate_region", "out_of_bounds", "absent_text"]
)
def test_source_layout_must_bind_real_crop_and_valid_regions(bad):
    value = source_layout()
    row = value["units"][0]
    if bad == "source_hash":
        row["reference_sha256"] = "b" * 64
    elif bad == "duplicate_region":
        row["text_regions"].append(deepcopy(row["text_regions"][0]))
    elif bad == "out_of_bounds":
        row["text_regions"][0]["bbox"][2] = 1001
    else:
        row["has_poster_text"] = False
    with pytest.raises(ValueError):
        validate_reference_layouts(value, {"R001": "a" * 64})


def test_fresh_review_excludes_wrong_plan_description_and_other_module_constraints():
    module = {
        **MODULE,
        "visual_contract": {"typography_structure": "three huge rows"},
        "generation_brief": "make every line huge",
    }
    scope = {
        "module_id": "M001",
        "required_identity_checks": ["Visible real package"],
        "layout_anchors": [{"label": "three huge rows"}],
        "composition_notes": "three huge rows",
    }
    context = {
        "user_facts": "Verified product facts",
        "reference_layouts": source_layout(),
        "module_constraints": {
            "M001": scope,
            "M003": {"instruction": "show loose food"},
        },
    }
    result = exact_review_context(module, context)
    assert result["user_context"] == {"user_facts": "Verified product facts"}
    assert "visual_contract" not in result["module"]
    assert "generation_brief" not in result["module"]
    assert result["module_constraints"] == {
        "module_id": "M001",
        "required_identity_checks": ["Visible real package"],
    }
