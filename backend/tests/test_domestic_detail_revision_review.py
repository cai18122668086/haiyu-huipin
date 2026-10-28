"""Reject unknown local pixel observations before identity follow-up."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.review import (
    DetailReviewError,
    approved_review_authority,
    normalize_pair_observation,
    validate_pair_before_identity,
)
from app.domestic_detail.review_provenance import pair_review_contract
from app.domestic_detail.revision_review import (
    baseline_inventory,
    local_model_context,
    observation_schema,
    with_local_schema,
)
from app.xinghe_agent import _planner_prompt, _planner_response_format
from tests.domestic_detail_atomic_fixtures import review_context
from tests.test_domestic_detail_review_authority import compiled_review_session
from tests.test_native_detail_revision_pixel_review import observation
from tests.test_native_detail_revision_pixel_review import revision as revision

pytest_plugins = ("tests.test_native_detail_failure_transition",)


@pytest.fixture
def local_case(revision):
    root, _, raw, contract, _, _ = revision
    native = review_context(root)
    native["local_revision_contract"] = deepcopy(contract)
    raw = deepcopy(raw)
    # These solid-color synthetic fixture rasters contain no visible text.
    raw.update(
        ocr_text=deepcopy(raw["artifact_binding"]["ocr_text"]),
        unauthorized_ocr_text=[],
        forbidden_text_hits=[],
    )
    raw["local_revision_observation"] = observation(contract, native["pixel_lineage"])
    return native, raw


def normalize(native, raw, pending=False):
    canvas = native["pixel_lineage"]["canvas"]
    function = validate_pair_before_identity if pending else normalize_pair_observation
    return function(
        raw,
        native,
        image_sha256=canvas["sha256"],
        width=canvas["width_px"],
        height=canvas["height_px"],
    )


def test_positive_observation_is_kept_exactly_and_context_has_no_private_paths(
    local_case,
):
    native, raw = local_case
    original_native, original_raw = deepcopy(native), deepcopy(raw)
    result = normalize(native, raw)
    assert (
        result["local_revision_observation"]
        == original_raw["local_revision_observation"]
    )
    assert native == original_native and raw == original_raw
    context = local_model_context(native)
    assert (
        context["expected_observation"]["contract_sha256"]
        == native["local_revision_contract"]["sha256"]
    )
    assert "authority_files" not in json.dumps(context) and '"path"' not in json.dumps(
        context
    )
    assert Draft202012Validator(observation_schema(context)).is_valid(
        raw["local_revision_observation"]
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "null",
        "intent",
        "contract",
        "source_session",
        "source_canvas",
        "candidate_canvas",
        "target_before",
        "target_candidate",
        "sibling_before",
        "sibling_candidate",
        "sibling_id",
        "module_order",
        "selected",
        "missing_requirement",
        "reordered_requirement",
        "rewritten_requirement",
        "unknown_pass",
        "null_pass",
        "integer_pass",
        "unknown_pixels",
        "blank_pixels",
        "extra",
        "contradictory_pass",
    ],
)
def test_incomplete_or_misbound_local_report_cannot_reach_identity_followup(
    local_case, tamper
):
    native, raw = local_case
    raw.pop("identity_silhouette")
    row = raw["local_revision_observation"]
    if tamper == "missing":
        raw.pop("local_revision_observation")
    elif tamper == "null":
        raw["local_revision_observation"] = None
    elif tamper in {
        "intent",
        "contract",
        "source_session",
        "source_canvas",
        "candidate_canvas",
    }:
        row[tamper + "_sha256"] = "0" * 64
    elif tamper in {"target_before", "target_candidate"}:
        row[tamper + "_slice_sha256"] = "0" * 64
    elif tamper in {"sibling_before", "sibling_candidate"}:
        row["sibling"][tamper.split("_")[1] + "_slice_sha256"] = "0" * 64
    elif tamper == "sibling_id":
        row["sibling"]["module_id"] = native["local_revision_contract"][
            "selected_module_id"
        ]
    elif tamper == "module_order":
        row["module_ids"].reverse()
    elif tamper == "selected":
        row["selected_module_id"] = row["sibling"]["module_id"]
    elif tamper == "missing_requirement":
        row["change_only"].pop()
    elif tamper == "reordered_requirement":
        row["change_only"].reverse()
    elif tamper == "rewritten_requirement":
        row["change_only"][0]["original_requirement"] = (
            "Model invented a looser requirement"
        )
    elif tamper == "unknown_pass":
        row["change_only"][0]["pass"] = "unknown"
    elif tamper == "null_pass":
        row["preserve"][0]["pass"] = None
    elif tamper == "integer_pass":
        row["preserve"][0]["pass"] = 1
    elif tamper == "unknown_pixels":
        row["sibling"]["visible_evidence"] = "unknown: cannot observe original pixels"
    elif tamper == "blank_pixels":
        row["preserve"][0]["before_observation"] = " " * 20
    elif tamper == "extra":
        row["optimistic_default"] = True
    else:
        row["sibling"]["pass"] = False
    with pytest.raises(DetailReviewError):
        normalize(native, raw, pending=True)


@pytest.mark.parametrize(
    "group", ["change_only", "preserve", "must_not_regress", "sibling"]
)
def test_explicit_local_negative_preserved_without_inventing_model_failure_text(
    local_case, group
):
    native, raw = local_case
    row = raw["local_revision_observation"][group]
    row = row if group == "sibling" else row[0]
    row["pass"] = False
    row["visible_evidence"] = (
        "Explicit synthetic observed difference in the requested local feature"
    )
    raw.update(status="MATERIAL_FAIL", material_failures=[])
    result = normalize(native, raw)
    assert result["local_revision_observation"] == raw["local_revision_observation"]
    assert result["material_failures"] == []


def test_unrequested_local_report_has_no_authority(local_case):
    native, raw = local_case
    native.pop("local_revision_contract")
    with pytest.raises(DetailReviewError):
        normalize(native, raw, pending=True)


def test_actual_pair_response_schema_requires_bound_local_observation(local_case):
    from dataclasses import replace

    from tests.test_domestic_detail_revision_scope import context_fixture

    native, raw = local_case
    native["review_authority"] = approved_review_authority(compiled_review_session())
    canvas = native["pixel_lineage"]["canvas"]
    inventory = baseline_inventory(native)
    context = pair_review_contract(
        native, canvas["sha256"], canvas["width_px"], canvas["height_px"], inventory
    )
    _, request = context_fixture()
    request = replace(
        request,
        planning_profile="aurora_detail_pair_audit",
        product_information=(json.dumps(context),),
    )
    schema = _planner_response_format(request)["json_schema"]["schema"]
    assert "local_revision_observation" in schema["required"]
    assert Draft202012Validator(
        schema["properties"]["local_revision_observation"]
    ).is_valid(raw["local_revision_observation"])
    ordinary = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"type": "string"}},
    }
    assert with_local_schema(ordinary, {}) is ordinary
    for rows in [[], inventory[:-1], list(reversed(inventory))]:
        with pytest.raises(DetailReviewError):
            pair_review_contract(
                native, canvas["sha256"], canvas["width_px"], canvas["height_px"], rows
            )


@pytest.mark.parametrize("tamper", ["hash", "pair", "selected", "order", "dimensions"])
def test_local_context_does_not_accept_another_authority_or_canvas(local_case, tamper):
    native, _ = local_case
    contract = native["local_revision_contract"]
    if tamper == "hash":
        contract["sha256"] = "0" * 64
    elif tamper == "pair":
        native["pair_id"] = "P99"
    elif tamper == "selected":
        contract["selected_module_id"] = "M99"
    elif tamper == "order":
        native["module_ids"] = list(reversed(native["module_ids"]))
    else:
        native["pixel_lineage"]["canvas"]["height_px"] += 2
    if tamper == "selected":
        contract["sha256"] = canonical_hash(
            {k: v for k, v in contract.items() if k != "sha256"}
        )
    with pytest.raises(DetailReviewError):
        local_model_context(native)


@pytest.mark.parametrize("text", ["ordinary product fact", "[]", "null", "{}"])
def test_ordinary_pair_schema_is_unchanged_without_local_contract(text):
    from dataclasses import replace

    from tests.test_domestic_detail_revision_scope import context_fixture

    _, request = context_fixture()
    request = replace(
        request,
        planning_profile="aurora_detail_pair_audit",
        product_information=(text,),
    )
    actual = _planner_response_format(request)
    baseline = _planner_response_format(replace(request, product_information=()))
    assert actual == baseline
    assert "all 44 canonical checks" in _planner_prompt(request)
    assert (
        "local_revision_observation" not in actual["json_schema"]["schema"]["required"]
    )


@pytest.mark.parametrize(
    "history",
    [
        "allowed",
        "duplicate",
        "unknown",
        "failed",
        "missing_error",
        "missing_raw",
        "four_calls",
    ],
)
def test_original_request_history_bounds_new_review_fees(local_case, history):
    from pathlib import Path

    from app.domestic_detail.review import ReviewStore
    from app.domestic_detail.revision_review import require_new_review_history

    native, _ = local_case
    root = Path(
        native["local_revision_contract"]["authority_files"]["source_session"]["path"]
    ).parent
    native["token"] = "new-fixture-token"
    count = 4 if history == "four_calls" else 1
    for index in range(count):
        token = (
            native["token"]
            if history == "duplicate"
            else "old-fixture-token-" + str(index)
        )
        store = ReviewStore(
            root / "reviews",
            {"phase": "aurora_detail_pair_audit", "render_token": token},
        )
        store.begin()
        if history != "unknown":
            result = {"payload": {}, "audit_raw_content": "{}", "error_code": None}
            if history == "failed":
                result["error_code"] = "ERROR_FIXTURE"
            elif history == "missing_error":
                result.pop("error_code")
            elif history == "missing_raw":
                result.pop("audit_raw_content")
            store.finish(result)
    if history == "allowed":
        require_new_review_history(root, native, "aurora_detail_pair_audit")
    else:
        with pytest.raises(DetailReviewError) as rejected:
            require_new_review_history(root, native, "aurora_detail_pair_audit")
        if history == "unknown":
            assert rejected.value.code == "AURORA_NATIVE_REVIEW_OUTCOME_UNKNOWN"
