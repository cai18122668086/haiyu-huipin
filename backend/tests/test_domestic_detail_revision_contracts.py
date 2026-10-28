"""Bound effective contracts prevent semantic replies from widening local authority."""

from copy import deepcopy

import pytest

from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.revision_contracts import effective_contracts
from app.domestic_detail.revision_protocol import (
    RevisionScopeError,
    require_context,
    validate_scope_response,
)
from tests.test_domestic_detail_revision_scope import (
    context_fixture,
    make_request,
    observation,
)


def bound_case():
    context, _ = context_fixture()
    context["approved_plan"]["modules"][1]["text_system"] = {
        "anchor_zone": "center",
        "placement_logic": "original placement",
        "max_lines": 2,
        "neighbor_difference": "immutable sibling distinction",
    }
    authority = context["approved_authority"]
    authority["compiled_plan_sha256"] = canonical_hash(context["approved_plan"])
    authority["sha256"] = canonical_hash(
        {k: v for k, v in authority.items() if k != "sha256"}
    )
    context["effective_module_contracts"] = effective_contracts(
        context["approved_plan"], context["module_ids"]
    )
    context["sha256"] = canonical_hash(
        {k: v for k, v in context.items() if k != "sha256"}
    )
    request = make_request(context)
    value = observation(request)
    value["changes"][0]["contract_effect"] = "OVERRIDE_LOCAL_FIELDS"
    value["contract_overrides"] = [
        {
            "module_id": "M02",
            "change_index": 0,
            "path": "/text_system/anchor_zone",
            "original_value": "center",
            "replacement_value": "edge_left",
        }
    ]
    return context, request, value


def test_real_model_patch_preserves_original_context_and_exact_raw_observation():
    context, request, value = bound_case()
    before = deepcopy(context)
    result = validate_scope_response(value, request)
    assert result["accepted_local_scope"]
    assert result["original_observation"] == value
    assert context == before


@pytest.mark.parametrize(
    "tamper",
    [
        "old_value",
        "sibling",
        "unknown_path",
        "copy",
        "index",
        "bool_index",
        "duplicate",
        "empty_effect",
        "orphan_patch",
        "missing_effect",
        "unknown_effect",
        "missing_patches",
        "blank",
        "same_value",
        "invalid_enum",
        "claim",
        "global",
        "kind",
        "bool_integer",
        "float_old",
    ],
)
def test_patch_cannot_widen_or_replace_original_local_authority(tamper):
    context, request, value = bound_case()
    patch = value["contract_overrides"][0]
    if tamper == "old_value":
        patch["original_value"] = "top_left"
    elif tamper == "sibling":
        patch["module_id"] = "M01"
    elif tamper in {"unknown_path", "copy"}:
        patch["path"] = (
            "/text_system/neighbor_difference"
            if tamper == "unknown_path"
            else "/copy/headline"
        )
    elif tamper == "index":
        patch["change_index"] = 1
    elif tamper == "bool_index":
        patch["change_index"] = False
    elif tamper == "float_old":
        patch.update(
            path="/text_system/max_lines", original_value=2.0, replacement_value=3
        )
    elif tamper == "duplicate":
        value["contract_overrides"].append(deepcopy(patch))
    elif tamper == "empty_effect":
        value["contract_overrides"] = []
    elif tamper == "orphan_patch":
        value["changes"][0]["contract_effect"] = "RESTORE_OR_PRESERVE_APPROVED"
    elif tamper == "missing_effect":
        value["changes"][0].pop("contract_effect")
    elif tamper == "unknown_effect":
        value["changes"][0]["contract_effect"] = "UNKNOWN"
    elif tamper == "missing_patches":
        value.pop("contract_overrides")
    elif tamper == "blank":
        patch.update(
            path="/text_system/placement_logic",
            original_value="original placement",
            replacement_value="  ",
        )
    elif tamper == "same_value":
        patch["replacement_value"] = "center"
    elif tamper == "invalid_enum":
        patch["replacement_value"] = "made_up_zone"
    elif tamper == "claim":
        value["changes"][0]["claim_effect"] = "REMOVE"
    elif tamper == "global":
        value["changes"][0]["scope"] = "GLOBAL"
    elif tamper == "kind":
        context["adjustment"]["change_kind"] = "COPY_CONTENT"
        context["adjustment_sha256"] = canonical_hash(context["adjustment"])
        context["sha256"] = canonical_hash(
            {k: v for k, v in context.items() if k != "sha256"}
        )
        request = make_request(context)
        value.update(
            context_sha256=context["sha256"],
            adjustment_sha256=context["adjustment_sha256"],
        )
    else:
        patch.update(
            path="/text_system/max_lines", original_value=2, replacement_value=True
        )
    with pytest.raises((RevisionScopeError, ValueError)):
        validate_scope_response(value, request)


@pytest.mark.parametrize(
    "tamper",
    ["order", "digest", "extra", "facts", "copy", "neighbor", "missing", "empty"],
)
def test_effective_projection_rejects_unsupported_source_drift(tamper):
    context, _, _ = bound_case()
    rows = context["effective_module_contracts"]
    if tamper == "order":
        rows.reverse()
    elif tamper == "digest":
        rows[1]["contract_sha256"] = "0" * 64
    elif tamper == "extra":
        rows[1]["optimistic"] = True
    elif tamper in {"facts", "copy"}:
        rows[1]["contract"][tamper] = "invented"
    elif tamper == "neighbor":
        rows[1]["contract"]["text_system"]["neighbor_difference"] = "changed"
    elif tamper == "missing":
        context.pop("effective_module_contracts")
    else:
        context["effective_module_contracts"] = []
    if tamper in {"facts", "copy", "neighbor"}:
        rows[1]["contract_sha256"] = canonical_hash(rows[1]["contract"])
    context["sha256"] = canonical_hash(
        {k: v for k, v in context.items() if k != "sha256"}
    )
    with pytest.raises(RevisionScopeError):
        require_context(context)


def test_existing_typography_projection_becomes_the_next_exact_old_value():
    context, _, _ = bound_case()
    row = context["effective_module_contracts"][1]
    row["contract"]["text_system"]["anchor_zone"] = "edge_left"
    row["contract_sha256"] = canonical_hash(row["contract"])
    context["sha256"] = canonical_hash(
        {k: v for k, v in context.items() if k != "sha256"}
    )
    request = make_request(context)
    value = observation(request)
    value["changes"][0]["contract_effect"] = "OVERRIDE_LOCAL_FIELDS"
    value["contract_overrides"] = [
        {
            "module_id": "M02",
            "change_index": 0,
            "path": "/text_system/anchor_zone",
            "original_value": "edge_left",
            "replacement_value": "lower_left",
        }
    ]
    assert validate_scope_response(value, request)["accepted_local_scope"]
    value["contract_overrides"][0]["original_value"] = "center"
    with pytest.raises(RevisionScopeError):
        validate_scope_response(value, request)
