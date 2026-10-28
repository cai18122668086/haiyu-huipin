"""Local copy and geometry overlays preserve approved facts and sibling contracts."""

from copy import deepcopy

import pytest

from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.revision_contracts import effective_contracts
from app.domestic_detail.revision_protocol import (
    RevisionScopeError,
    validate_scope_response,
)
from tests.test_domestic_detail_revision_contracts import bound_case
from tests.test_domestic_detail_revision_scope import make_request, observation
from tests.test_native_detail_typography_override import native as native


def case(kind="COPY_CONTENT", effect="REPHRASE_CONFIRMED"):
    context, _, _ = bound_case()
    original = context["approved_plan"]["modules"][1]
    original["visible_copy"] = [
        {"text": "原始标题", "role": "主结论", "anchor": "商品主体"},
        {"text": "原始辅助句", "role": "辅助句", "anchor": "局部细节"},
    ]
    original["copy_intent"] = {
        "text_to_visual_bindings": [
            {"text": row["text"], "bound_to_visual": row["anchor"]}
            for row in original["visible_copy"]
        ]
    }
    original["module_spatial_structure_contract"] = {
        "structure_family": "SPLIT_PANEL",
        "zones": [
            {
                "role": "主证据",
                "content": "现有商品主体及其事实",
                "geometry": "左侧非对称主区",
                "visual_weight_percent": 64,
            },
            {
                "role": "辅助证据",
                "content": "原有商品的已批准局部",
                "geometry": "右侧窄辅助区域",
                "visual_weight_percent": 36,
            },
        ],
    }
    context["adjustment"]["change_kind"] = kind
    context["adjustment_sha256"] = canonical_hash(context["adjustment"])
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
    change = value["changes"][0]
    change.update(contract_effect="OVERRIDE_LOCAL_FIELDS", claim_effect=effect)
    if kind == "COPY_CONTENT":
        old = deepcopy(original["visible_copy"])
        new = deepcopy(old)
        if effect == "REMOVE":
            new.pop()
        else:
            new[0]["text"] = "已确认事实的新说法"
            fact = next(
                r["fact"]
                for r in authority["original_fact_ledger"]
                if r["approved_disposition"] in {"LEAD", "SUPPORT"}
            )
            change["fact_refs"] = [{"id": fact["id"], "text": fact["text"]}]
        path = "/visible_copy"
    else:
        path = "/module_spatial_structure_contract/zones"
        old = deepcopy(original["module_spatial_structure_contract"]["zones"])
        new = deepcopy(old)
        new[0].update(geometry="上部横向主导区域", visual_weight_percent=60)
        new[1].update(geometry="下部辅助判断区域", visual_weight_percent=40)
    value["contract_overrides"] = [
        {
            "module_id": "M02",
            "change_index": 0,
            "path": path,
            "original_value": old,
            "replacement_value": new,
        }
    ]
    return context, request, value


@pytest.mark.parametrize(
    "kind,effect",
    [
        ("COPY_CONTENT", "REPHRASE_CONFIRMED"),
        ("COPY_CONTENT", "REMOVE"),
        ("VISUAL_DETAIL", "PRESERVE"),
        ("OTHER_LOCAL", "PRESERVE"),
    ],
)
def test_source_bound_local_copy_and_geometry_reach_native_effective_contracts(
    native, kind, effect
):
    context, request, value = case(kind, effect)
    before = deepcopy(context)
    assert validate_scope_response(value, request)["accepted_local_scope"]
    from runtime.local_contracts import _apply

    original = context["effective_module_contracts"][1]["contract"]
    updated = _apply(original, value["contract_overrides"], value["changes"], "M02")
    assert original == before["effective_module_contracts"][1]["contract"]
    projection = deepcopy(context["effective_module_contracts"])
    projection[1] = {
        "module_id": "M02",
        "contract": updated,
        "contract_sha256": canonical_hash(updated),
    }
    assert (
        effective_contracts(context["approved_plan"], context["module_ids"], projection)
        == projection
    )
    assert projection[0] == before["effective_module_contracts"][0]
    if kind == "COPY_CONTENT":
        assert updated["copy_intent"]["text_to_visual_bindings"] == [
            {"text": row["text"], "bound_to_visual": row["anchor"]}
            for row in updated["visible_copy"]
        ]


@pytest.mark.parametrize(
    "tamper",
    [
        "fact_id",
        "fact_text",
        "no_fact",
        "anchor",
        "role",
        "blank",
        "extra_unit",
        "new_claim",
        "new_path",
        "sibling",
        "empty_copy",
        "removal_rephrases",
    ],
)
def test_copy_overlay_does_not_promote_facts_or_change_anchors(tamper):
    context, request, value = case(
        effect="REMOVE" if tamper == "removal_rephrases" else "REPHRASE_CONFIRMED"
    )
    patch = value["contract_overrides"][0]
    if tamper in {"fact_id", "fact_text"}:
        value["changes"][0]["fact_refs"][0]["id" if tamper == "fact_id" else "text"] = (
            "unauthorized"
        )
    elif tamper == "no_fact":
        value["changes"][0]["fact_refs"] = []
    elif tamper in {"anchor", "role", "blank", "removal_rephrases"}:
        patch["replacement_value"][0][
            {"anchor": "anchor", "role": "role"}.get(tamper, "text")
        ] = "changed" if tamper != "blank" else ""
    elif tamper == "extra_unit":
        patch["replacement_value"].append(deepcopy(patch["replacement_value"][0]))
    elif tamper == "new_claim":
        value["changes"][0]["claim_effect"] = "NEW_OR_UNKNOWN"
    elif tamper == "new_path":
        patch["path"] = "/lead_claim_id"
    elif tamper == "sibling":
        patch["module_id"] = "M01"
    else:
        patch["replacement_value"] = []
    with pytest.raises(RevisionScopeError):
        validate_scope_response(value, request)


@pytest.mark.parametrize("tamper", ["count", "role", "content", "weight", "kind"])
def test_geometry_overlay_keeps_original_zones_and_content(tamper):
    context, request, value = case("VISUAL_DETAIL", "PRESERVE")
    patch = value["contract_overrides"][0]
    if tamper == "count":
        patch["replacement_value"].pop()
    elif tamper in {"role", "content"}:
        patch["replacement_value"][0][tamper] = "another proof or new claim"
    elif tamper == "weight":
        patch["replacement_value"][0]["visual_weight_percent"] = 90
    else:
        value["changes"][0]["claim_effect"] = "REPHRASE_CONFIRMED"
    with pytest.raises(RevisionScopeError):
        validate_scope_response(value, request)
