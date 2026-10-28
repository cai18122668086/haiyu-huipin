from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.errors import ApiError
from app.inspiration_routes import _configuration_errors, _configuration_status
from app.inspiration_workflows import (
    CANONICAL_ASSETS_BY_TITLE,
    CANONICAL_METADATA_BY_TITLE,
    CANONICAL_PROMPT_BY_TITLE,
    FINAL_INSPIRATION_WORKFLOW_BY_TITLE,
)
from app.routes_v1 import _validate_inspiration_case_job
from app.schemas_v1 import CreativeImageJobCreate, ReferenceRemixJobCreate


def _session_for(case, assets=()):
    session = Mock()
    session.scalar.return_value = case
    session.scalars.return_value.all.return_value = list(assets)
    return session


def _case(
    workflow_type: str,
    *,
    reference_source: str = "none",
    status: str = "published",
    configuration_status: str = "ready",
):
    return SimpleNamespace(
        id="ins_test",
        title="test",
        workflow_type=workflow_type,
        reference_source=reference_source,
        status=status,
        configuration_status=configuration_status,
        prompt="case prompt",
        button_label="一键做同款",
    )


def _creative(**kwargs):
    return CreativeImageJobCreate(
        inspiration_case_id="ins_test",
        type="creative_image",
        input={"product_asset_ids": ["product"], **kwargs.pop("input", {})},
        configuration={
            "inspiration_planning": True,
            "instruction": "case prompt",
            **kwargs.pop("configuration", {}),
        },
    )


def test_final_mapping_contains_exactly_the_approved_six_eight_two_split() -> None:
    assert len(FINAL_INSPIRATION_WORKFLOW_BY_TITLE) == 16
    assert (
        list(FINAL_INSPIRATION_WORKFLOW_BY_TITLE.values()).count("product_prompt") == 6
    )
    assert (
        list(FINAL_INSPIRATION_WORKFLOW_BY_TITLE.values()).count("reference_remix") == 8
    )
    assert list(FINAL_INSPIRATION_WORKFLOW_BY_TITLE.values()).count("local_edit") == 2
    assert "legacy_unclassified" not in FINAL_INSPIRATION_WORKFLOW_BY_TITLE.values()


def test_historical_cases_have_canonical_legacy_metadata() -> None:
    expected_fields = {
        "role",
        "type",
        "category",
        "platform",
        "ratio",
        "description",
        "intent",
    }
    assert set(CANONICAL_METADATA_BY_TITLE) == set(
        FINAL_INSPIRATION_WORKFLOW_BY_TITLE
    ) - {"生成AI模特"}
    assert all(
        set(metadata) == expected_fields
        for metadata in CANONICAL_METADATA_BY_TITLE.values()
    )
    assert CANONICAL_METADATA_BY_TITLE["暖金梳妆台 · 花香时刻"]["type"] == "卖点图"
    assert CANONICAL_METADATA_BY_TITLE["场景图（玩偶）"]["category"] == "儿童母婴"
    assert CANONICAL_METADATA_BY_TITLE["我发现你们好爱抄"]["platform"] == "国内"


def test_canonical_reference_inputs_are_product_then_visual_reference() -> None:
    for title, assets in CANONICAL_ASSETS_BY_TITLE.items():
        if FINAL_INSPIRATION_WORKFLOW_BY_TITLE[title] != "reference_remix":
            continue
        roles = [role for _filename, role, _ordinal in assets]
        assert roles[-1] == "result"
        input_roles = roles[:-1]
        assert input_roles[0] == "user_upload"
        assert input_roles[1:] == ["reference"] * (len(input_roles) - 1)


def test_every_case_has_a_specific_canonical_prompt() -> None:
    assert set(CANONICAL_PROMPT_BY_TITLE) == set(FINAL_INSPIRATION_WORKFLOW_BY_TITLE)
    assert all(len(prompt) > 80 for prompt in CANONICAL_PROMPT_BY_TITLE.values())
    assert all(
        "保持案例的商业表达" not in prompt
        for prompt in CANONICAL_PROMPT_BY_TITLE.values()
    )


def test_canonical_asset_roles_never_use_result_as_reference() -> None:
    for title, assets in CANONICAL_ASSETS_BY_TITLE.items():
        assert title in FINAL_INSPIRATION_WORKFLOW_BY_TITLE
        assert not any(
            role == "reference" and "result" in filename for filename, role, _ in assets
        )
        if FINAL_INSPIRATION_WORKFLOW_BY_TITLE[title] == "reference_remix":
            assert any(role == "reference" for _filename, role, _ in assets)
        else:
            assert not any(role == "reference" for _filename, role, _ in assets)


def test_generated_ai_model_remains_incomplete_without_verified_case_assets() -> None:
    case = _case("product_prompt")
    case.title = "生成AI模特"
    errors = _configuration_errors(case, [])
    assert errors == ["verified_case_assets_required"]
    assert _configuration_status(case, errors) == "incomplete"


def test_generated_ai_model_is_ready_with_result_and_user_upload_assets() -> None:
    case = _case("product_prompt")
    case.title = "生成AI模特"
    assets = [
        SimpleNamespace(role="result"),
        SimpleNamespace(role="user_upload"),
    ]
    errors = _configuration_errors(case, assets)
    assert errors == []
    assert _configuration_status(case, errors) == "ready"


def test_generated_ai_model_still_requires_both_display_asset_roles() -> None:
    case = _case("product_prompt")
    case.title = "生成AI模特"
    for assets in (
        [SimpleNamespace(role="result")],
        [SimpleNamespace(role="user_upload")],
        [SimpleNamespace(role="reference"), SimpleNamespace(role="result")],
    ):
        errors = _configuration_errors(case, assets)
        assert errors == ["verified_case_assets_required"]
        assert _configuration_status(case, errors) == "incomplete"


def test_job_validation_refreshes_stale_case_status_from_assets() -> None:
    case = _case("product_prompt", configuration_status="incomplete")
    case.title = "生成AI模特"
    session = _session_for(
        case,
        assets=[
            SimpleNamespace(role="result"),
            SimpleNamespace(role="user_upload"),
        ],
    )

    result = _validate_inspiration_case_job(session, _creative())

    assert result is case
    assert case.configuration_status == "ready"


def test_product_prompt_accepts_product_only_without_reference() -> None:
    case = _case("product_prompt")
    result = _validate_inspiration_case_job(_session_for(case), _creative())
    assert result is case


def test_product_prompt_rejects_reference_assets() -> None:
    case = _case("product_prompt")
    with pytest.raises(ApiError) as raised:
        _validate_inspiration_case_job(
            _session_for(case),
            _creative(input={"competitor_asset_ids": ["reference"]}),
        )
    assert raised.value.code == "INSPIRATION_REFERENCE_NOT_ALLOWED"


def test_reference_remix_requires_reference_asset() -> None:
    with pytest.raises(ValueError):
        ReferenceRemixJobCreate(
            inspiration_case_id="ins_test",
            type="reference_remix",
            input={"product_asset_ids": ["product"], "reference_asset_ids": []},
            configuration={},
        )


def test_reference_remix_does_not_treat_result_as_admin_reference() -> None:
    case = _case("reference_remix", reference_source="admin_reference")
    assert _configuration_errors(case, [SimpleNamespace(role="result")]) == [
        "admin_reference_asset_required"
    ]


def test_product_prompt_and_local_edit_require_no_reference_source() -> None:
    for workflow_type in ("product_prompt", "local_edit"):
        case = _case(workflow_type, reference_source="result_image")
        assert _configuration_errors(case, []) == ["reference_source_must_be_none"]


def test_product_prompt_cannot_be_called_as_reference_remix() -> None:
    case = _case("product_prompt")
    payload = ReferenceRemixJobCreate(
        inspiration_case_id="ins_test",
        type="reference_remix",
        input={"product_asset_ids": ["product"], "reference_asset_ids": ["reference"]},
        configuration={},
    )
    with pytest.raises(ApiError) as raised:
        _validate_inspiration_case_job(_session_for(case), payload)
    assert raised.value.code == "INSPIRATION_WORKFLOW_MISMATCH"


def test_unclassified_case_is_rejected_before_job_creation() -> None:
    case = _case("legacy_unclassified")
    with pytest.raises(ApiError) as raised:
        _validate_inspiration_case_job(_session_for(case), _creative())
    assert raised.value.code == "INSPIRATION_CASE_WORKFLOW_UNSUPPORTED"
